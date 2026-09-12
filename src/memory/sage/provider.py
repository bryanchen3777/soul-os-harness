"""
SAGELiteProvider — vendored from hermes-sage-memory v0.1.3
Phase 2.0: 去掉 Hermes MemoryProvider ABC 相依，純化為 soul-os-harness 的記憶服務

保留的核心方法（給 MemoryMiddleware 用）：
  - initialize(profile_id, data_dir)  改用顯式 data_dir，不靠 hermes_home
  - prefetch(query, session_id)       sync 查詢，回傳可注入 prompt 的字串
  - sync_turn(user, assistant, sid)   sync 寫入
  - post_reply_commit(sid, user, ai)  async 寫入（內部用 run_in_executor）
  - system_prompt_block()             健康指標字串
  - shutdown()                        收尾

移除的 Hermes-only 方法：
  - get_tool_schemas / handle_tool_call   （Hermes tool API）
  - on_memory_write / on_pre_compress     （Hermes hook）
  - on_session_switch / on_session_end / on_turn_start
  - save_config / get_config_schema       （Hermes config UI）
  - get_write_health
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any, Optional

from .graph_store import GraphStore
from .writer import MemoryWriter
from .reader import MemoryReader
from .evolution import MemoryEvolution, REINFORCEMENT_DELTA
from .models import Fact, ContextResult
from .token_utils import TokenBudget, SummaryCompressor, PrefetchCache

logger = logging.getLogger("soul_os.sage")

# Phase 7 — Ram (Re:Zero · COS v1.0) no-diary 差異化
# 任務書「Ram 沒有 feelings/diary.md」：soul-os-harness 的 SAGE 對應語意為
# 「Ram 的對話不寫入 graph.sqlite facts」，情感狀態由 emotional-state.json 表達。
# 注意：其他 agent 的 sync_turn/post_reply_commit 行為不受影響（回歸測試必跑）。
NO_DIARY_AGENTS: set[str] = {"agent_ram"}

# EH-3/3.1 (Write-Side Assimilation): 句法定義標記 — 使用者本輪文本需含「解釋／定義性」
# 句法才觸發 assimilated 打標。純句法詞, 0 hardcoded tech keywords（無「插電／機器／
# 科技／電腦／家電」等實體詞 — 打標依據是「定義句的形式」而非「定義了什麼」）。
# EH-3.1: 移除裸「就是」與簡體「是一个」— 裸「就是」會誤捕「就是想／就是要」等
# 主觀生命狀態句法（由 _SUBJECTIVE_MODAL_PHRASES 另行排除）；保留「就是個」等
# 兼完整定義式 marker。
_ASSIMILATION_SYNTAX_MARKERS: tuple[str, ...] = (
    "就是個", "是個", "是一個", "是一种", "是一種",
    "用來", "用来", "所謂", "意思是", "指的是", "指的",
)

# EH-3.1: 主觀生命狀態句法 — 「就是」+ 情態／情緒助詞 = 主觀表述, 非定義句。
# 「就是想／就要／就會／就愛／就喜歡／就在／就覺得」前一語段的實體 = 主觀主體,
# 該實體相關 fact 一律不打標（Ex: 「今天就是想休息」→ 今天）。
_SUBJECTIVE_MODAL_PHRASES: tuple[str, ...] = (
    "就是想", "就是要", "就是會", "就是愛",
    "就是喜歡", "就是在", "就是覺得",
)

# EH-3.1: 人稱代名詞主語精確排除集（exact match, 非 substring）— 主觀敘述主體。
_DEICTIC_SUBJECT_BLOCK: frozenset[str] = frozenset(
    {"我", "你", "妳", "我們", "咱們", "主人"}
)

# EH-3.1: 被定義實體的語段切隔符（上個標點／空白）與尾綴剝離字。
_ENTITY_SEGMENT_SEPARATORS: str = "，。、；;：:！？!?（）()「」『』【】《》<> \t\n"
_ENTITY_TRAILING_PARTICLES: str = "呢嗎啊呀喔哦吧啦的囉是就"

# ── EH-3.1 helpers（純文字掃描, module-level, 可單元測試）──────────


def _segment_entity(text: str, marker_pos: int) -> str:
    """回傳 marker 前一語段（上個標點／空白之後）的候選實體。

    Ex: 「氣炸鍋就是個…箱子。」的「就是個」marker → "氣炸鍋";
        「今天就是想休息。」的「就是想」marker → "今天"。
    剝離尾綴語助詞（的／呢／啊／是／就…）; 空實體回傳 ""。
    """
    segment = text[:marker_pos].rstrip()
    cut = -1
    for sep in _ENTITY_SEGMENT_SEPARATORS:
        idx = segment.rfind(sep)
        if idx > cut:
            cut = idx
    entity = segment[cut + 1:].strip()
    while entity and entity[-1] in _ENTITY_TRAILING_PARTICLES:
        entity = entity[:-1]
    return entity.strip()


def _scan_assimilation_entities(user_text: str) -> tuple[set[str], set[str]]:
    """掃描 user 文本, 回傳 (定義實體集合, 主觀實體集合)。

    定義實體: 任一 _ASSIMILATION_SYNTAX_MARKERS 前一語段的實體（被定義者）。
    主觀實體: 任一 _SUBJECTIVE_MODAL_PHRASES 前一語段的實體（主觀生命狀態主體）。
    """
    definitional: set[str] = set()
    modal: set[str] = set()
    for marker in _ASSIMILATION_SYNTAX_MARKERS:
        start = 0
        while True:
            idx = user_text.find(marker, start)
            if idx < 0:
                break
            entity = _segment_entity(user_text, idx)
            if entity:
                definitional.add(entity)
            start = idx + len(marker)
    for phrase in _SUBJECTIVE_MODAL_PHRASES:
        start = 0
        while True:
            idx = user_text.find(phrase, start)
            if idx < 0:
                break
            entity = _segment_entity(user_text, idx)
            if entity:
                modal.add(entity)
            start = idx + len(phrase)
    return definitional, modal


def _fact_intersects_entity(fact: Fact, entities: set[str]) -> bool:
    """fact.subject / fact.object 是否與定義實體集合相交（entity 為 needle）。"""
    if not entities:
        return False
    for entity in entities:
        if entity in fact.subject or entity in fact.object:
            return True
    return False


# ═════════════════════════════════════════════════════════════════════
# EH-4.2 (L4 雙軌概念同化圖譜) — 五元組寫回 + 安全熔接
#
# 契約：docs/EH-4-EPISTEMIC-MIND-CONTRACT.md §4.1 / §4.2（含 SF-1~SF-7）/ §4.3
#   - 槽位 1 earth_term：**現況不動**（EH-3.1 既有 user 列即為槽位 1，本模組不重寫）
#   - 槽位 2~5：本模組以決定性模板追加 4 條衍生列（0 LLM；契約 L4-D2）
#   - 觸發唯一閘門：EH-3.1 定義句閘門「成功打標之實體」（0 新句法判定）
#   - 純日常提及／購買陳述（「買了／有一台」）→ 0 寫入
# ═════════════════════════════════════════════════════════════════════

#: 保留命名空間（契約 §4.1）：一般抽取器不得產生此前綴，投影端只認 prefix。
EH4_PREDICATE_MENTAL_MODEL = "eh4_mental_model"
EH4_PREDICATE_SAFETY_RULE = "eh4_safety_rule"
EH4_PREDICATE_DUTY_ACTION = "eh4_duty_action"
EH4_PREDICATE_IDIOLECT = "eh4_idiolect"

#: 一輪一實體最多寫 4 條衍生列（契約 §4.2 I2；earth_term 由 EH-3.1 既有列承擔）。
_EH4_DERIVED_PREDICATES: tuple[str, ...] = (
    EH4_PREDICATE_MENTAL_MODEL,
    EH4_PREDICATE_SAFETY_RULE,
    EH4_PREDICATE_DUTY_ACTION,
    EH4_PREDICATE_IDIOLECT,
)

#: 安全子句偵測詞（通用感官／安全語彙；0 世界觀名詞、0 現代器具名）。
_EH4_SAFETY_TOKENS: tuple[str, ...] = (
    "燙", "熱", "危險", "注意", "小心", "焦", "煙", "觸電", "漏電", "割", "濕",
)
#: 叮嚀／命令式尾綴（SF-3：熔接子句必為**敘述性直覺**，不得寫成命令句）→ 剝除。
_EH4_HORTATORY_TAILS: tuple[str, ...] = (
    "要注意", "要小心", "務必小心", "務必注意", "小心", "注意", "務必", "記得",
)
#: 子句切分標點（決定性組裝用；與抽取器同構）。
_EH4_CLAUSE_SPLIT = "，。！？、；：,.!?;:\n\t "
#: 非家務軸時的侍奉分工敘述（SF-6：含「誰碰／怎麼碰」界線、不列動作清單、非命令句）。
_EH4_DUTY_FALLBACK = "我負責在旁看顧，先問過主人才動手"


def _eh4_clauses(user_text: str) -> list[str]:
    """依標點切分子句（保序、去空白；0 語義判斷）。"""
    raw = str(user_text or "")
    for sep in "。！？!?\n":
        raw = raw.replace(sep, "，")
    return [c.strip() for c in raw.split("，") if c.strip()]


def _eh4_master_safety_clause(user_text: str) -> str:
    """主人解釋句中的**安全子句**（敘述性直覺；剝除叮嚀尾綴）。

    取「最後一個含安全詞的子句」→ 去掉尾端叮嚀（「要注意」…）→ 回傳描述句。
    Ex: 「…箱子，外殼會燙要注意。」→ ``外殼會燙``（SF-3：非命令句）。
    """
    for clause in reversed(_eh4_clauses(user_text)):
        if not any(tok in clause for tok in _EH4_SAFETY_TOKENS):
            continue
        text = clause.strip()
        changed = True
        while changed and text:
            changed = False
            for tail in _EH4_HORTATORY_TAILS:
                if text.endswith(tail) and len(text) > len(tail):
                    text = text[: -len(tail)].strip()
                    changed = True
                    break
        return text
    return ""


def _eh4_master_noun(user_text: str) -> str:
    """定義子句尾端的**名物詞**（主人用詞；0 品名硬編碼）。

    Ex: 「氣炸鍋就是個插電吹熱風把食物烤熟的箱子」→ ``箱子``
        「…是插電加熱的小箱子」→ ``小箱子``（取最後一個「的」之後、上限 6 字）。
    """
    raw = str(user_text or "")
    for marker in _ASSIMILATION_SYNTAX_MARKERS:
        idx = raw.find(marker)
        if idx < 0:
            continue
        tail = raw[idx + len(marker):]
        for sep in _EH4_CLAUSE_SPLIT:
            cut = tail.find(sep)
            if cut >= 0:
                tail = tail[:cut]
        tail = tail.strip()
        if not tail:
            continue
        if "的" in tail:
            tail = tail.rsplit("的", 1)[1].strip()
        while tail and tail[-1] in _ENTITY_TRAILING_PARTICLES:
            tail = tail[:-1]
        tail = tail.strip()[:6]
        if len(tail) >= 2:
            return tail
    return ""


def _eh4_dedup_substrings(terms: tuple[str, ...]) -> list[str]:
    """去除互為子串的重複詞條（「柴火」與「火」→ 只留「柴火」；保序）。"""
    out: list[str] = []
    for term in terms:
        if not term:
            continue
        if any(term in kept for kept in out):
            continue
        out = [kept for kept in out if kept not in term]
        out.append(term)
    return out


def build_concept_quintuple_slots(
    agent_id: str,
    entity: str,
    user_text: str,
) -> Optional[dict[str, str]]:
    """以 L1 pack ＋ 通用特徵詞決定性組裝槽位 2~5（0 LLM；契約 §4.2）。

    回傳 ``{predicate: object}``（恰 4 條衍生列）；材料不足（無 pack／無特徵詞／
    無可解析軸）→ ``None``（fail-silent，不寫任何列）。

    - ``eh4_mental_model``（SF-2／SF-3）：``似{anchor}，卻無{absent}；{安全直覺}；{侍奉界線}``
      —— 安全直覺**強制熔接**進描述句（raw safety 欄位不投影，SF-5／L4-D3）。
    - ``eh4_safety_rule``：該軸 ``hazard_rules`` ＋ 主人描述（raw，永不投影進 prompt）。
    - ``eh4_duty_action``：``labor_domesticity`` 命中時取其 ``duty_hooks[0]``，
      否則用通用侍奉界線敘述（SF-6）。
    - ``eh4_idiolect``：``那個{特徵詞}的{主人名物詞}``（契約 §4.1 槽位 5）。
    """
    try:
        from src.inner_life import epistemic_appraisal as ea

        pack = ea.load_epistemic_pack(agent_id)
        if pack is None or not entity:
            return None
        features = tuple(ea._match_features(str(user_text or "")))
        if not features:
            return None
        axis_hits = ea._map_axes(features)
        if not axis_hits:
            return None

        anchor_class = ea._resolve_anchor_class(features)
        bridge = ea._select_bridge(pack, features, anchor_class)
        if bridge is not None:
            anchor = bridge.preferred_anchor
            axis_key = bridge.axis if bridge.axis in pack.axes else axis_hits[0]
            absent = tuple(
                t for t in bridge.expect_absent if t and t not in str(user_text or "")
            )
        else:
            axis_key = axis_hits[0]
            axis_pack = pack.axes.get(axis_key)
            if axis_pack is None or not axis_pack.analogy_anchors:
                return None
            anchor = axis_pack.analogy_anchors[0]
            absent = tuple(
                t for t in axis_pack.native_basis if t and t not in str(user_text or "")
            )

        # ── 安全軸（SF-1）：主人給出安全子句或命中 hot_shell 時，取
        #    safety_hazard 軸的 hazard_rules；否則取該輪主軸條款 ──
        own_safety = _eh4_master_safety_clause(user_text)
        safety_axis_key = (
            "safety_hazard"
            if ("hot_shell" in features or own_safety) and "safety_hazard" in pack.axes
            else axis_key
        )
        hazard_rules = tuple(pack.axes[safety_axis_key].hazard_rules) if safety_axis_key in pack.axes else ()
        safety_narrative = own_safety or (hazard_rules[0] if hazard_rules else "")
        safety_rule_obj = "；".join(hazard_rules)
        if own_safety:
            safety_rule_obj = (
                f"{safety_rule_obj}；主人描述：{own_safety}" if safety_rule_obj else own_safety
            )

        # ── 侍奉界線（SF-6）：labor_domesticity 命中 → pack duty_hooks；否則通用敘述 ──
        duty_clause = _EH4_DUTY_FALLBACK
        if "labor_domesticity" in axis_hits:
            hooks = tuple(pack.axes["labor_domesticity"].duty_hooks)
            if hooks:
                duty_clause = f"我負責{hooks[0]}"

        # ── mental_model（SF-2）：{anchor 類比}，{absent 缺席}；{安全直覺}；{侍奉界線} ──
        absent_core = _eh4_dedup_substrings(absent)
        analogy = f"似{anchor}"
        if absent_core:
            analogy += f"，卻無{'、'.join(absent_core)}"
        mental_model = "；".join(p for p in (analogy, safety_narrative, duty_clause) if p)

        # ── idiolect（槽位 5）：那個{特徵詞}的{主人名物詞} ──
        hit_features = set(features)
        displays = [
            ea._FEATURE_DISPLAY[k]
            for k, _t, _a in ea._FEATURE_LEXICON
            if k in hit_features and k in ea._FEATURE_DISPLAY
        ]
        master_noun = _eh4_master_noun(user_text) or anchor
        idiolect = (
            f"那個{'、'.join(displays[:2])}的{master_noun}"
            if displays
            else f"那個{master_noun}"
        )

        return {
            EH4_PREDICATE_MENTAL_MODEL: mental_model,
            EH4_PREDICATE_SAFETY_RULE: safety_rule_obj,
            EH4_PREDICATE_DUTY_ACTION: duty_clause,
            EH4_PREDICATE_IDIOLECT: idiolect,
        }
    except Exception as exc:  # noqa: BLE001 — fail-silent（契約 §4.2 I5）
        logger.warning(
            f"[SAGE] EH-4.2 槽位組裝失敗: {type(exc).__name__}: {exc}"
        )
        return None


def assimilate_concept_graph(
    store: "GraphStore",
    agent_id: str,
    entity: str,
    user_text: str,
    *,
    session_id: str = "",
    source_pair: Optional[str] = None,
) -> int:
    """EH-4.2 (L4)：為**已成功打標**的實體追加 4 條衍生列（契約 §4.2）。

    冪等（I1）：同 ``(subject, predicate)`` 已有 aware 列 → skip（不覆寫、不重複）。
    維度：``origin='assimilated'`` / ``horizon_state='aware'`` / ``learned_at=time.time()``
    （經既有 ``set_fact_dimensions`` 標記，0 DDL／0 schema 變更）。

    回傳實際新增列數（0 = 全數跳過或材料不足）。任何異常 → ``logger.warning``
    ＋ 靜默放棄（I5 fail-silent，不得影響已完成的回覆）。
    """
    try:
        slots = build_concept_quintuple_slots(agent_id, entity, user_text)
        if not slots:
            return 0
        # 冪等預檢（I1）：一次撈齊既有 aware 列，避免逐列查詢。
        existing = {
            (f.subject, f.predicate) for f in store.get_idiolect_facts()
        }
        written = 0
        for predicate in _EH4_DERIVED_PREDICATES:
            obj = slots.get(predicate) or ""
            if not obj or (entity, predicate) in existing:
                continue
            fact = Fact(
                subject=entity,
                predicate=predicate,
                object=obj,
                source="inference",
                session_id=session_id,
                source_pair=source_pair,
            )
            fact_id = store.add_fact(fact)
            store.set_fact_dimensions(
                fact_id=fact_id,
                origin="assimilated",
                horizon_state="aware",
                learned_at=time.time(),
            )
            existing.add((entity, predicate))
            written += 1
        if written:
            # 沿用 EH-3.1 既有強制提交語義：讀側 retrieve_idiolect 開新連線，
            # 未 commit 的 INSERT 讀不到 → 閉環斷裂。
            store.flush()
        logger.info(
            f"[SAGE] EH-4.2 五元組同化 ok | profile={agent_id} | "
            f"entity={entity} | written={written}/4"
        )
        return written
    except Exception as exc:  # noqa: BLE001 — fail-silent（契約 §4.2 I5）
        logger.warning(
            f"[SAGE] EH-4.2 五元組同化失敗 (profile={agent_id}, entity={entity}): "
            f"{type(exc).__name__}: {exc}"
        )
        return 0


class SAGELiteProvider:
    """soul-os-harness 相容的 SAGE-lite 記憶服務

    與 hermes-sage-memory v0.1.3 adapter.py 的差異：
    - 不繼承 Hermes MemoryProvider ABC
    - 移除所有 Hermes-only hooks
    - 路徑解析：data_dir 顯式傳入（不再依賴 ~/.hermes）
    - profile_id 直接是建構子參數
    """

    PROVIDER_NAME = "sage_lite"

    def __init__(
        self,
        profile_id: str = "default",
        data_dir: Optional[str] = None,
        top_k: int = 5,
        max_hops: int = 2,
        max_tokens: int = 800,
        recall_mode: str = "balanced",
    ):
        self.profile_id = profile_id
        self.data_dir = Path(data_dir) if data_dir else None
        self.top_k = top_k
        self.max_hops = max_hops
        self.max_tokens = max_tokens
        self.recall_mode = recall_mode
        self._session_id: str = ""
        self._store: Optional[GraphStore] = None
        self._writer: Optional[MemoryWriter] = None
        self._reader: Optional[MemoryReader] = None
        self._evolution: Optional[MemoryEvolution] = None
        self._turn_count: int = 0
        self._compressor = SummaryCompressor()
        self._cache = PrefetchCache(ttl_seconds=30.0, max_size=50)
        self._write_failures: list[dict] = []

    # ── 生命週期 ──────────────────────────────────────────────

    def initialize(self, session_id: str = "default") -> None:
        """Lazy-init components。session_id 可在之後切換。"""
        self._session_id = session_id
        self._init_components()

    def _db_path(self) -> Path:
        """每個 profile 獨立 graph.sqlite 檔。"""
        if self.data_dir is None:
            raise ValueError(
                "SAGELiteProvider.data_dir is not set; "
                "pass it to constructor or call initialize() with a data_dir"
            )
        return self.data_dir / "graph.sqlite"

    def _init_components(self) -> None:
        if self._store:
            self._store.close()
        self._store = GraphStore(db_path=self._db_path())
        # M5.10-2: 先建 reader (跟 writer 共用同一 GraphStore), 再傳給 writer
        # 順序不可調換 — writer._memory_reader 需要 reader instance
        self._reader = MemoryReader(
            self._store,
            on_retrieved=self._on_memory_retrieved,
        )
        # Bry 拍板 2026-07-18 Stage 1.6: 傳 profile_id 給 writer, 讓 v1 mirror 知道歸屬哪個 agent
        # M5.10-2: 傳 _reader 讓 _extract_facts_llm 在 call LLM Judge 前先取 v1 context
        self._writer = MemoryWriter(
            self._store,
            default_session_id=self._session_id,
            agent_id=self.profile_id,
            memory_reader=self._reader,
        )
        self._evolution = MemoryEvolution(self._store)

    def shutdown(self) -> None:
        if self._store:
            self._store.flush()
            self._store.close()

    # ── Prefetch（sync — MemoryMiddleware 會包 asyncio.to_thread）──

    def prefetch(
        self,
        query: str,
        *,
        session_id: str,
        boost_tags: Optional[list[str]] = None,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): source_pair 過濾白名單
        # 格式: set of "<user_id>:<agent_id>", 例 {"bryan:agent_ruka"}
        # reader 撈事實時, 過濾掉 source_pair 非空且不在這個 set 內的事實
        # (避免 ram/miku/yua 撈到 Bry-mai/Bry-ruka 私域喇稱)
        # None = 不過濾 (向後相容)
        # Bry 拍板防呆: 空 source_pair (既有資料) 一律視為可見, 不被過濾
        source_pair_filter: Optional[set[str]] = None,
    ) -> str:
        """查詢相關記憶，回傳 token-bounded 字串。

        Empty graph 或無匹配時回傳空字串。
        相同 query 在 TTL 內會走快取。
        """
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        result = self._reader.retrieve_context(
            query,
            top_k=self.top_k,
            max_hops=self.max_hops,
            max_tokens=self.max_tokens,
            mode=self.recall_mode,
            boost_tags=boost_tags,
            source_pair_filter=source_pair_filter,
        )
        if result.is_empty:
            return ""

        budget = TokenBudget(self.max_tokens)
        summary = self._compressor.compress(result, budget)
        self._cache.set(query, summary)
        return summary

    def queue_prefetch(self, query: str, *, session_id: str) -> None:
        """背景 thread 版 prefetch（給非同步管線用，soul-os Phase 2 不一定會用到）。"""
        t = threading.Thread(
            target=self.prefetch,
            kwargs={"query": query, "session_id": session_id},
            daemon=True,
        )
        t.start()

    # ── 寫入（sync 與 async 兩種） ─────────────────────────────

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): 寫入帶 source_pair 標記
        source_pair: Optional[str] = None,
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 跟 post_reply_commit 對齊, sync / async 兩條路徑都吃 canonical event_id
        inner_life_event_id: Optional[str] = None,
    ) -> None:
        """
        Sync 寫入。soul-os MemoryMiddleware 會包 asyncio.to_thread。

        Phase 7 — no-diary 白名單：agent_ram 的對話不寫入 graph.sqlite，
        情感狀態由 AgentConsciousness._on_session_end → emotional-state.json 表達。
        其他 agent 行為不受影響（回歸測試驗證）。
        """
        if not self._writer:
            return
        # Phase 7: NO_DIARY_AGENTS 白名單攔截
        if self.profile_id in NO_DIARY_AGENTS:
            logger.debug(
                f"[SAGE] sync_turn skipped (no-diary agent): profile={self.profile_id}"
            )
            return
        self._writer.write_turn(
            user_content, assistant_content,
            session_id=session_id, source_pair=source_pair,
            inner_life_event_id=inner_life_event_id,
        )
        self._turn_count += 1
        self._cache.invalidate()
        if self._turn_count % 20 == 0:
            self._evolution.run_scheduled_decay()
            self._evolution.auto_resolve_conflicts()

    async def post_reply_commit(
        self,
        session_id: str,
        last_user_msg: str,
        agent_reply: str,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): 寫入帶 source_pair 標記
        # middleware._on_agent_speak 從 event.payload 拿 target_user_id + agent_id 組成
        # 例: "bryan:agent_ruka" = Bry 跟 ruka 的對話事實
        source_pair: Optional[str] = None,
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 從 AGENT_SPEAK SoulEvent top-level field (M5.4-5.5 frozen) 透傳
        # Memory 是 consumer, 絕不 create_event() 建立新的 InnerLifeEvent
        # - proactive_dm 路徑 (M5.4-6.2): canonical event_id 從 executor 傳來
        # - USER_MESSAGE / heartbeat / 等路徑: None → MemoryWriter fallback synthetic UUID
        inner_life_event_id: Optional[str] = None,
    ) -> None:
        """
        Async 寫入（內部已用 run_in_executor，不會阻塞 event loop）。

        這是 MemoryMiddleware 在 AGENT_SPEAK 階段呼叫的方法。

        Phase 7 — no-diary 白名單：與 sync_turn 對齊，agent_ram 跳過圖譜寫入。

        Bry 拍板 2026-07-18 Stage 2.1: NO_DIARY agents 仍跑 v1 mirror (skip_graph=True),
        理由: v1 mirror 是結構化備忘, 跟 diary (graph.sqlite) 是不同概念, Ram 不寫 diary
        仍可以有 v1 facts。
        """
        # Phase 7 + Bry 拍板 Stage 2.1: NO_DIARY_AGENTS 跳 graph 寫入, 但仍 mirror
        if self.profile_id in NO_DIARY_AGENTS:
            logger.debug(
                f"[SAGE] post_reply_commit no-diary: profile={self.profile_id} "
                f"(跳 graph 寫入, 仍 v1 mirror)"
            )
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                partial(
                    self._writer.write_turn,
                    skip_graph=True,
                    source_pair=source_pair,
                    inner_life_event_id=inner_life_event_id,
                ),
                last_user_msg, agent_reply, session_id,
            )
            self._cache.invalidate()
            return
        loop = asyncio.get_event_loop()
        # MEM-WIRING-1 (P0 BUGFIX): run_in_executor 只收位置參數, 直接位置傳遞會把
        # source_pair（第 5 位置參數）錯綁成 write_turn 的第 4 位置參數 skip_graph；
        # 非空字串 truthy → skip_graph=True → 誤跳 graph.sqlite 萃取落庫。
        # 修法: partial 以 keyword 繫結 source_pair / inner_life_event_id,
        #       前三個位置參數 (user_content, assistant_content, session_id) 保留原序。
        # EH-3: 捕獲 write_turn 返回的新增 fact_id 清單（不可丟棄）供 post-commit hook。
        fact_ids = await loop.run_in_executor(
            None,
            partial(
                self._writer.write_turn,
                source_pair=source_pair,
                inner_life_event_id=inner_life_event_id,
            ),
            last_user_msg,
            agent_reply,
            session_id,
        )
        # EH-3 (Write-Side Assimilation): post-commit hook — 對「user 解釋句」萃取的
        # fact 打 assimilated/aware + learned_at。同步 GraphStore 呼叫維持在
        # run_in_executor 的 worker 執行緒內（不在主 asyncio thread 觸發 SQLite 寫入）。
        # Fail-silent: hook 內部任何例外僅 warning, 不中斷主流程。
        if fact_ids:
            # EH-3.1 回傳本輪**成功打標之實體**（定義句閘門唯一判準）。
            tagged_entities = await loop.run_in_executor(
                None,
                partial(
                    self._tag_explanatory_assimilations,
                    fact_ids,
                    last_user_msg,
                ),
            )
            # EH-4.2 (L4)：唯一觸發閘門 = EH-3.1 定義句閘門打標成功之實體
            # （契約 §4.2；0 新句法判定、0 重跑分類）。純日常提及／購買陳述
            # （「買了／有一台」）不經此閘門 → 嚴格 0 寫入。
            if tagged_entities:
                await loop.run_in_executor(
                    None,
                    partial(
                        self._assimilate_concept_graph,
                        list(tagged_entities),
                        last_user_msg,
                        session_id=session_id,
                        source_pair=source_pair,
                    ),
                )
        self._cache.invalidate()

        if self._turn_count % 20 == 0:
            await loop.run_in_executor(
                None, self._evolution.run_scheduled_decay
            )
            await loop.run_in_executor(
                None, self._evolution.auto_resolve_conflicts
            )
        self._turn_count += 1

    # ── EH-3.1: Write-Side Assimilation（寫側內化閉環, Fact-Level）────

    def _tag_explanatory_assimilations(
        self,
        fact_ids: list[str],
        user_text: str,
    ) -> list[str]:
        """EH-3.1 post-commit hook：Fact-Level 精準打標（EH-3 turn-level 粗標升級）。

        同步方法, 由 post_reply_commit 以 run_in_executor 包覆在 worker 執行緒內
        執行（GraphStore.set_fact_dimensions 為同步 DB/圖更新, 維持 thread-affinity,
        不在主 asyncio thread 呼叫, 避免阻塞 event loop）。

        EH-3 缺陷: turn-level 粗標 — user 文本只要含任一定義性 marker, 整輪所有
        user fact 全被打 assimilated; 「今天就是想休息」這類主觀生命狀態
        （就是想/就是要/…）被誤標成內化常識, 污染 Idiolect 檢索。

        EH-3.1 改為逐 Fact 五閘門（A–E 全過才打標）:
          A. fact.source == 'user'（User-Only Source, 不對 assistant/inference 打標）
          B. user 原始文本含定義性 marker（_ASSIMILATION_SYNTAX_MARKERS）
          C. fact.subject 精確排除人稱代名詞（_DEICTIC_SUBJECT_BLOCK,
             exact match 非 substring）— 主觀敘述主體不打標
          D. 「就是」+ 情態助詞主觀句法排除: 文本中任 _SUBJECTIVE_MODAL_PHRASES
             前一語段的實體 = 主觀生命狀態主體, fact 不得觸及
             （Ex: 「今天就是想休息。」→ 今天）
          E. fact.subject/object 必須與被定義實體相交（定義 marker 前一語段的
             實體, Ex: 「氣炸鍋就是個…箱子。」→ 氣炸鍋）— 只標被定義實體

        打標: origin=assimilated / horizon_state=aware / learned_at=time.time() (float)。
        Fail-silent: 任何未預期例外僅記錄 logger.warning, 嚴禁中斷主流程或
        回滾既有 Graph 寫入; 未過閘門的 fact 維持預設 lived_experience, 永不刪改。

        EH-4.2: 回傳本輪**成功打標之實體**（``fact.subject``、保序去重）——
        此即 L4 五元組同化的**唯一觸發閘門**（契約 §4.2；0 新句法判定）。
        未過閘門 / 例外 → 回空 list。
        """
        # Gate 1: 現代原生白名單（契約 §6.1 D3 分流）
        try:
            from .horizon import is_modern_native
        except Exception as exc:  # noqa: BLE001 — fail-silent: import 失敗視為不 bypass
            logger.warning(
                f"[SAGE] EH-3 horizon import failed: {type(exc).__name__}: {exc}"
            )
            is_modern_native = None
        if is_modern_native is not None and is_modern_native(self.profile_id):
            logger.debug(
                f"[SAGE] EH-3.1 skip (modern native): profile={self.profile_id}"
            )
            return []
        # Gate 2: 本輪無新增 fact（skip_graph / no-diary / 0 萃取）→ no-op
        if not fact_ids:
            return []
        # Gate B (text-level): 無定義性 marker → 本輪不打標
        definitional_entities, modal_entities = _scan_assimilation_entities(user_text)
        if not definitional_entities:
            logger.debug(
                f"[SAGE] EH-3.1 no definitional syntax, skip tagging: "
                f"profile={self.profile_id}"
            )
            return []
        if self._store is None:
            return []
        tagged_entities: list[str] = []
        try:
            tagged = 0
            for fact_id in fact_ids:
                fact = self._store.get_fact(fact_id)
                if fact is None or fact.source != "user":
                    # Gate A: User-Only Source — assistant/inference fact 不打標
                    continue
                # Gate C: 人稱代名詞主語精確排除（exact match, 非 substring）
                if fact.subject in _DEICTIC_SUBJECT_BLOCK:
                    continue
                # Gate D: 主觀生命狀態實體觸及排除
                if (
                    fact.subject in modal_entities
                    or fact.object in modal_entities
                ):
                    continue
                # Gate E: 必須觸及被定義實體
                if not _fact_intersects_entity(fact, definitional_entities):
                    continue
                self._store.set_fact_dimensions(
                    fact_id=fact_id,
                    origin="assimilated",
                    horizon_state="aware",
                    learned_at=time.time(),  # float Unix timestamp (契約 §3.2)
                )
                tagged += 1
                if fact.subject and fact.subject not in tagged_entities:
                    tagged_entities.append(fact.subject)
            # 強制 commit: set_fact_dimensions 只在 batch_size 達標時才自動 commit,
            # 讀側 Idiolect 檢索（retrieve_idiolect）開新 sqlite 連接, 未 commit 的
            # UPDATE 讀不到 → 閉環斷裂。flush 維持在 worker 執行緒內（@_locked 安全）。
            self._store.flush()
            logger.info(
                f"[SAGE] EH-3.1 assimilated tagging ok | profile={self.profile_id} | "
                f"tagged={tagged}/{len(fact_ids)}"
            )
        except Exception as exc:  # noqa: BLE001 — fail-silent: 絕不中斷主流程
            logger.warning(
                f"[SAGE] EH-3.1 assimilated tagging failed "
                f"(profile={self.profile_id}): {type(exc).__name__}: {exc}"
            )
            return []
        return tagged_entities

    # ── EH-4.2 (L4): 五元組同化寫回（契約 §4.2）────────────────

    def _assimilate_concept_graph(
        self,
        entities: list[str],
        user_text: str,
        *,
        session_id: str = "",
        source_pair: Optional[str] = None,
    ) -> int:
        """逐實體追加 4 條衍生列（槽位 2~5）。

        呼叫端已限定為 EH-3.1 定義句閘門打標成功之實體；本方法維持在
        ``run_in_executor`` 的 worker 執行緒內（I3 SQLite thread-affinity）。
        Fail-silent（I5）：任何例外僅 warning，回 0。
        """
        if self._store is None or not entities:
            return 0
        total = 0
        for entity in entities:
            total += assimilate_concept_graph(
                self._store,
                self.profile_id,
                entity,
                user_text,
                session_id=session_id,
                source_pair=source_pair,
            )
        return total

    # ── 健康指標 ──────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        s = self._store.stats()
        return (
            f"[SAGE-lite Memory] "
            f"{s['active_facts']} active facts | "
            f"{s['node_count']} entities | "
            f"avg confidence {s['avg_weight']:.2f} | "
            f"profile: {self.profile_id}"
        )

    def stats(self) -> dict:
        """公開統計資訊，供 MemoryMiddleware / Dashboard 使用。"""
        if not self._store:
            return {"profile": self.profile_id, "active_facts": 0}
        s = self._store.stats()
        s["profile"] = self.profile_id
        return s

    # ── 內部 hook ─────────────────────────────────────────────

    def _on_memory_retrieved(self, result: ContextResult) -> None:
        """
        Post-retrieval hook (M7-forgetting 擴充, Bry 拍板 2026-08-19):
        高分 facts 強化、低分 facts 輕微 decay。

        呼應「越常想起越記得牢, 不再提起就淡忘」:
          - score >= 0.5 → reinforce (+REINFORCEMENT_DELTA)
          - score <  0.2 → decay (-0.02)
        anchor 不動 (固定保留)。
        """
        for fact in result.facts:
            if fact.is_anchor:
                continue
            score = result.retrieval_scores.get(fact.fact_id, 0.0)
            if score >= 0.5:
                self._evolution.apply_correction(
                    fact.fact_id, "reinforce",
                    delta=REINFORCEMENT_DELTA,
                    reason="high_retrieval_score",
                )
            elif score < 0.2:
                self._evolution.apply_correction(
                    fact.fact_id, "decay",
                    delta=0.02,
                    reason="low_retrieval_score",
                )

    def get_write_health(self) -> dict:
        return {
            "total_write_failures": len(self._write_failures),
            "recent_failures": self._write_failures[-5:],
            "store_stats": self._store.stats() if self._store else {},
        }
