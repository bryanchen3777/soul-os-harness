import asyncio
import logging
import os
import re
import time
import datetime
from pathlib import Path  # Bry §21: 修 Path import bug, Bry §12 _mirror_to_v1_store 用 Path 但 module 沒 import
from typing import Any, Dict, List, Optional, Tuple

from .models import Fact
from .graph_store import GraphStore
from .token_utils import TokenBudget, SummaryCompressor

logger = logging.getLogger("soul_os.memory.writer")

# Predicate 同義詞正規化表（W-2）
_PREDICATE_SYNONYMS: dict[str, str] = {
    "enjoys":       "likes",
    "loves":        "likes",
    "adores":       "likes",
    "prefers":      "likes",
    "dislikes":     "hates",
    "despises":     "hates",
    "loathes":      "hates",
    "is_at":        "lives_in",
    "located_in":   "lives_in",
    "resides_in":   "lives_in",
    "employed_at":  "works_at",
    "employed_by":  "works_at",
    "works_for":    "works_at",
    "喜愛":         "喜歡",
    "熱愛":         "喜歡",
    "厭惡":         "討厭",
    "居住在":       "住在",
    "任職於":       "工作於",
}

# 反義 predicate 對（用於 contradiction detection）
_ANTONYM_PREDICATES: list[frozenset[str]] = [
    frozenset({"likes",    "hates"}),
    frozenset({"likes",    "dislikes"}),
    frozenset({"loves",    "hates"}),
    frozenset({"lives_in", "not_in"}),
    frozenset({"is",       "is_not"}),
    frozenset({"喜歡",      "討厭"}),
]

_RELATION_PATTERNS: list[tuple[str, str, float]] = [
    (r"(.+?)\s+is\s+(?:a\s+|an\s+|the\s+)?(.+)",           "is",          1.0),
    (r"(.+?)\s+are\s+(.+)",                                  "is",          1.0),
    (r"(.+?)\s+was\s+(?:a\s+|an\s+)?(.+)",                  "was",         0.8),
    (r"(.+?)\s+(?:really\s+)?likes?\s+(.+)",                 "likes",       1.0),
    (r"(.+?)\s+loves?\s+(.+)",                               "likes",       1.0),
    (r"(.+?)\s+(?:really\s+)?hates?\s+(.+)",                 "hates",       1.0),
    (r"(.+?)\s+(?:enjoys?|enjoyed)\s+(.+)",                  "likes",       0.9),
    (r"(.+?)\s+(?:prefers?)\s+(.+)",                         "likes",       0.9),
    (r"(.+?)\s+(?:dislikes?|doesn'?t like)\s+(.+)",          "hates",       1.0),
    (r"(.+?)\s+(?:works?|worked)\s+(?:at|for|in)\s+(.+)",   "works_at",    1.0),
    (r"(.+?)\s+(?:lives?|lived)\s+(?:in|at|near)\s+(.+)",   "lives_in",    1.0),
    (r"(.+?)\s+(?:is\s+from|comes?\s+from)\s+(.+)",         "from",        0.9),
    (r"(.+?)\s+(?:knows?|knew)\s+(.+)",                      "knows",       0.9),
    (r"(.+?)\s+(?:has|have|had)\s+(?:a\s+|an\s+)?(.+)",     "has",         0.9),
    (r"(.+?)\s+(?:wants?|wanted|need[s]?)\s+(.+)",           "wants",       0.8),
    (r"(.+?)\s+(?:plans?\s+to|going\s+to)\s+(.+)",           "plans_to",    0.8),
    (r"(.+?)喜歡(.+)",   "喜歡",  1.0),
    (r"(.+?)討厭(.+)",   "討厭",  1.0),
    (r"(.+?)住在(.+)",   "住在",  1.0),
    (r"(.+?)在(.+?)工作", "工作於", 1.0),
    (r"(.+?)是(.+)",     "是",    1.0),
    (r"(.+?)有(.+)",     "有",    0.9),

    # ── Phase 2.3 擴充：口語句型 ──
    # 分類 A：移動 / 行為動詞（weight 0.9，雜訊比正式句型高）
    (r"(.{1,10})去了?(.{1,20})",   "去",    0.9),
    (r"(.{1,10})來了?(.{1,20})",   "來",    0.9),
    (r"(.{1,10})到了?(.{1,20})",   "到",    0.9),
    (r"(.{1,10})買了?(.{1,20})",   "買",    0.9),
    (r"(.{1,10})吃了?(.{1,20})",   "吃",    0.9),
    (r"(.{1,10})見了?(.{1,20})",   "見",    0.9),
    (r"(.{1,10})用了?(.{1,20})",   "使用",  0.9),

    # 分類 B：狀態描述（weight 0.8-1.0）
    (r"(.{1,10})覺得(.{1,20})",    "覺得",  0.8),
    (r"(.{1,10})感覺(.{1,20})",    "感覺",  0.8),
    (r"(.{1,10})住(.{1,15})",      "住在",  1.0),  # 補「我住台北」漏網

    # 分類 C：時間 / 承諾（weight 1.0，subject 至少 1 字）
    # 注：原 spec 寫 3 字下限會擋掉 "你說過" 這種 1 字 subject 的正常句型
    #     實際上 1 字下限對 "上次見到→去" 這種 false positive 沒實質幫助
    #     （keyword 不在 position 1 就對不上），所以降為 1
    (r"(.{1,15})說過(.{1,20})",    "說過",  1.0),
    (r"(.{1,15})答應(.{1,20})",    "答應",  1.0),
    (r"(.{1,15})上次(.{1,20})",    "上次",  1.0),
    (r"(.{1,15})下次(.{1,20})",    "下次要", 1.0),
    (r"(.{1,15})記得(.{1,20})",    "記得",  1.0),
]

_NOISE_OBJECTS = {
    "it", "this", "that", "things", "something", "anything",
    "him", "her", "them", "there", "here", "now", "then",
}
_FIRST_PERSON = {"i", "me", "my", "myself", "i'm", "i've", "i'd"}

_DATE_PATTERNS = [
    (r"(\d{1,2})[月/\-](\d{1,2})[日號]?", "month_day"),
    (r"(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})", "full_date"),
    (r"明天|tomorrow",    "tomorrow"),
    (r"後天",              "day_after_tomorrow"),
    (r"下週|next week",    "next_week"),
    (r"下個月|next month", "next_month"),
]


class WriteResult:
    """write 操作的確認回傳（A-2：write confirmation）"""
    def __init__(self):
        self.written: list[str] = []
        self.merged:  list[str] = []
        self.rejected: list[tuple[str, list[str]]] = []
        self.contradictions: list[tuple[Fact, Fact]] = []

    @property
    def success_count(self) -> int:
        return len(self.written) + len(self.merged)

    @property
    def has_failures(self) -> bool:
        return len(self.rejected) > 0


class MemoryWriter:
    """Writer v4：schema gate + predicate 正規化 + write confirmation"""

    ANCHOR_WEIGHT_THRESHOLD = 1.8

    def __init__(
        self,
        graph_store: GraphStore,
        default_session_id: str = "",
        agent_id: str = "",
    ):
        self.store = graph_store
        self.default_session_id = default_session_id
        # Bry 拍板 2026-07-18 Stage 1.6: 區分 subject_hint (role = user/assistant) 跟真正的 agent_id
        # 之前 _mirror_to_v1_store 把 subject_hint 當 agent_id 寫進 memory.agent_id,
        # 結果 Loader 用 m.agent_id == "agent_rem" 過濾時全部不符, 觸發 fail-safe。
        # 修法: writer.__init__ 接 agent_id, provider 傳 profile_id, _mirror_to_v1_store 用 self.agent_id
        self.agent_id = agent_id

    # ── 公開 API ──────────────────────────────────────────────

    def add_fact(self, fact: Fact) -> str:
        result = self._write_single(fact)
        if result.written:
            return result.written[0]
        if result.merged:
            return result.merged[0]
        return ""

    def add_facts_batch(self, facts: list[Fact]) -> list[str]:
        return [fid for f in facts for fid in [self.add_fact(f)] if fid]

    def write_with_confirmation(self, fact: Fact) -> WriteResult:
        result = WriteResult()
        self._write_single(fact, result)
        return result

    def extract_and_write(
        self,
        text: str,
        subject_hint: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "user",
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B):
        # 標記這個 turn 寫入的事實是誰跟誰對話產生的, 格式 "<user_id>:<agent_id>"
        # 例: "bryan:agent_ruka" = Bry 跟 ruka 的對話事實
        # reader 撈事實時, middleware 會用這個標記過濾 (避免其他角色撈到 Bry-其他角色的私域)
        # None = 不標記, reader 視為「未標記」一律保留 (Bry 拍板防呆)
        source_pair: Optional[str] = None,
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 從 AGENT_SPEAK SoulEvent top-level field (M5.4-5.5 frozen) 透傳
        # 跟 M5.4-5.2 不同:
        #   - M5.4-5.2: synthetic uuid.uuid4().hex (per-call, 不跟 canonical 串連)
        #   - M5.5-2: 用 upstream 傳進來的 canonical event_id
        #     (Memory 絕不 create_event() 建立新的 InnerLifeEvent)
        # None = backward compat: 既有 caller 不傳 → 退回 M5.4-5.2 行為
        inner_life_event_id: Optional[str] = None,
    ) -> list[str]:
        sid = session_id or self.default_session_id
        facts, raw_results = self._extract_facts(text, subject_hint, sid, source)
        # 修法 1: 把 source_pair 套到這個 turn 抽出的所有 fact
        if source_pair is not None:
            for f in facts:
                f.source_pair = source_pair
        # M5.4-5.2 + M5.5-2: Inner Life reference propagation
        # 兩種路徑:
        #   1. canonical (M5.5-2): 上游傳入 canonical event_id, 全部 facts 共用同一個 eid
        #      - 來自 AGENT_SPEAK.inner_life_event_id (M5.4-5.5 frozen top-level field)
        #      - 對應 proactive_dm / 等有 lived experience 的路徑
        #   2. synthetic (M5.4-5.2 backward compat): 上游沒傳, 用 uuid.uuid4().hex
        #      - 對應 USER_MESSAGE / heartbeat / 等沒有 InnerLifeEvent 的路徑
        #      - 維持 M5.4-5.2 行為: per-fact unique eid (per F1 test, 每個 fact 自己的 identity)
        # 永遠不 fabricate InnerLifeEvent (per ticket architectural rule)
        import uuid as _uuid_ilid
        for f, r in zip(facts, raw_results):
            if inner_life_event_id is not None:
                # M5.5-2: canonical event_id 共享給所有 facts (一個 lived experience
                # → 多個 qualified facts, 全部 reference 同一個 canonical event)
                eid = inner_life_event_id
            else:
                # M5.4-5.2 backward compat: per-fact unique synthetic UUID
                eid = _uuid_ilid.uuid4().hex
            f.inner_life_event_id = eid
            r["inner_life_event_id"] = eid
        # Bry 拍板 2026-07-18 Stage 1.5 fix: mirror 移到 _extract_facts 父層,
        # 兩條路徑 (LLM + heuristic) 都會跑, 解決 heuristic fallback 不寫 v1 的 bug
        self._mirror_extraction(
            text=text, raw_results=raw_results,
            subject_hint=subject_hint, session_id=sid, source=source,
        )
        return self.add_facts_batch(facts)

    def extract(
        self,
        text: str,
        subject_hint: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "user",
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 跟 extract_and_write 對齊, 確保 mirror / graph 對同一個 fact 來源一致
        inner_life_event_id: Optional[str] = None,
    ) -> list[Fact]:
        """只抽取事實、不寫入 graph。測試用與下游預處理層用。

        Bry 拍板 2026-07-18 Stage 1.5 fix: extract 也要 mirror (v1 是結構化備忘,
        即使沒寫 graph 也要留底), 跟 extract_and_write 走同一條 mirror 路徑。
        """
        sid = session_id or self.default_session_id
        facts, raw_results = self._extract_facts(text, subject_hint, sid, source)
        # M5.4-5.2 + M5.5-2: 跟 extract_and_write 共用同樣的 inner_life_event_id 邏輯
        # 兩種路徑 (canonical / synthetic) per extract_and_write
        import uuid as _uuid_ilid
        for f, r in zip(facts, raw_results):
            if inner_life_event_id is not None:
                # M5.5-2: canonical event_id 共享
                eid = inner_life_event_id
            else:
                # M5.4-5.2 backward compat: per-fact unique synthetic UUID
                eid = _uuid_ilid.uuid4().hex
            f.inner_life_event_id = eid
            r["inner_life_event_id"] = eid
        self._mirror_extraction(
            text=text, raw_results=raw_results,
            subject_hint=subject_hint, session_id=sid, source=source,
        )
        return facts

    def write_turn(
        self,
        user_content: str,
        assistant_content: str,
        session_id: Optional[str] = None,
        skip_graph: bool = False,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): 寫入時帶 source_pair 標記
        # 格式 "<user_id>:<agent_id>", 例 "bryan:agent_ruka"
        # middleware._on_agent_speak 從 event.payload 拿 target_user_id + agent_id 組成
        # None = 不標記, reader 視為「未標記」一律保留
        source_pair: Optional[str] = None,
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 從 AGENT_SPEAK 透傳過來 (M5.4-5.5 frozen top-level field)
        # 兩個 extract_and_write call (user + assistant) 共用同一個 canonical eid
        # (一個 turn = 一個 lived experience = 一個 canonical event_id)
        # None = 退回 M5.4-5.2 synthetic UUID 行為
        inner_life_event_id: Optional[str] = None,
    ) -> list[str]:
        """寫一輪 user + assistant。

        Args:
            skip_graph: Bry 拍板 2026-07-18 Stage 2.1 用, NO_DIARY agents (Ram) 設為 True。
                - True: 跳過 graph.sqlite 寫入, 但仍跑 LLM judge + v1 mirror
                  (理由: v1 mirror 是結構化備忘, 跟 diary 是不同概念,
                  Ram 不寫 diary 仍可以有 v1 facts)
                - False (default): 原路徑 (graph + v1 mirror)
            source_pair: 修法 1 加, 標記這 turn 寫入的事實是誰跟誰的對話。
            inner_life_event_id: M5.5-2 加, canonical InnerLifeEvent reference。
        """
        sid = session_id or self.default_session_id
        if skip_graph:
            # 走 extract 路徑 (只抽事實 + mirror, 不 add_fact 寫 graph)
            logger.debug(
                f"[MemoryWriter] write_turn skip_graph=True: session={sid}"
            )
            if user_content:
                self.extract(
                    user_content, subject_hint="user",
                    session_id=sid, source="user",
                    inner_life_event_id=inner_life_event_id,
                )
            if assistant_content:
                self.extract(
                    assistant_content, subject_hint="assistant",
                    session_id=sid, source="inference",
                    inner_life_event_id=inner_life_event_id,
                )
            return []
        # 原路徑
        user_ids = self.extract_and_write(
            user_content, subject_hint="user",
            session_id=sid, source="user", source_pair=source_pair,
            inner_life_event_id=inner_life_event_id,
        )
        assistant_ids = self.extract_and_write(
            assistant_content, subject_hint="assistant",
            session_id=sid, source="inference", source_pair=source_pair,
            inner_life_event_id=inner_life_event_id,
        )
        return user_ids + assistant_ids

    # ── 內部核心 ──────────────────────────────────────────────

    def _write_single(
        self, fact: Fact, result: Optional[WriteResult] = None
    ) -> WriteResult:
        if result is None:
            result = WriteResult()

        if not fact.session_id:
            fact.session_id = self.default_session_id

        # 1. Schema gate
        errors = fact.validate()
        if errors:
            result.rejected.append((str(fact.to_dict()), errors))
            return result

        # 2. Predicate 正規化
        fact.predicate = _PREDICATE_SYNONYMS.get(
            fact.predicate, fact.predicate
        )

        # 3. Entity 對齊
        fact.subject = self._align_entity(fact.subject)
        fact.object  = self._align_entity(fact.object)

        # 4. Contradiction detection
        contradiction = self._find_contradiction(fact)
        if contradiction:
            result.contradictions.append((fact, contradiction))
            if fact.confidence > contradiction.confidence:
                self.store.update_weight(
                    contradiction.fact_id,
                    max(0.0, contradiction.weight - 0.3)
                )
            else:
                fact.confidence *= 0.5

        # 5. 重複偵測與合併
        existing = self._find_similar(fact)
        if existing:
            new_weight = min(2.0, existing.weight + fact.weight * 0.3)
            if new_weight >= self.ANCHOR_WEIGHT_THRESHOLD and not existing.is_anchor:
                self.store.set_anchor(existing.fact_id, True)
            else:
                self.store.update_weight(existing.fact_id, new_weight)
            result.merged.append(existing.fact_id)
            return result

        # 6. 寫入
        fid = self.store.add_fact(fact)
        if fid:
            result.written.append(fid)
        else:
            result.rejected.append((str(fact.to_dict()), ["store write failed"]))
        return result

    def _find_contradiction(self, fact: Fact) -> Optional[Fact]:
        norm_pred = _PREDICATE_SYNONYMS.get(fact.predicate, fact.predicate)
        existing = self.store.search_by_entity(fact.subject)
        for e in existing:
            if e.subject.lower() != fact.subject.lower():
                continue
            if e.object.lower() != fact.object.lower():
                continue
            e_norm = _PREDICATE_SYNONYMS.get(e.predicate, e.predicate)
            ep = frozenset({e_norm})
            fp = frozenset({norm_pred})
            for antonym_pair in _ANTONYM_PREDICATES:
                if (norm_pred in antonym_pair
                        and e_norm in antonym_pair
                        and e_norm != norm_pred):
                    return e
        return None

    def _align_entity(self, name: str) -> str:
        similar = self.store.find_similar_entity(name, threshold=0.75)
        return similar if similar else name

    def _extract_event_time(self, text: str) -> Optional[float]:
        now = time.time()
        for pattern, tag in _DATE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                if tag == "tomorrow":
                    return now + 86400
                elif tag == "day_after_tomorrow":
                    return now + 86400 * 2
                elif tag == "next_week":
                    return now + 86400 * 7
                elif tag == "next_month":
                    return now + 86400 * 30
                elif tag == "month_day":
                    m = re.search(pattern, text)
                    if m:
                        try:
                            t = datetime.datetime(
                                datetime.datetime.now().year,
                                int(m.group(1)), int(m.group(2))
                            )
                            return t.timestamp()
                        except ValueError:
                            pass
                elif tag == "full_date":
                    m = re.search(pattern, text)
                    if m:
                        try:
                            t = datetime.datetime(
                                int(m.group(1)), int(m.group(2)), int(m.group(3))
                            )
                            return t.timestamp()
                        except ValueError:
                            pass
        return None

    def _normalize_entity(self, raw: str, subject_hint: Optional[str]) -> str:
        cleaned = raw.strip()
        cleaned_lower = cleaned.lower()
        if cleaned_lower in _FIRST_PERSON:
            return subject_hint or "user"
        cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        has_chinese = bool(re.search(r"[一-鿿]", cleaned))
        return cleaned if has_chinese else cleaned.capitalize()

    def _normalize_object(self, raw: str) -> str:
        cleaned = raw.strip()
        cleaned = re.sub(r"[,.;:]+$", "", cleaned)
        cleaned = re.sub(r"^(the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()
        if cleaned.lower() in _NOISE_OBJECTS:
            return ""
        return cleaned

    def _find_similar(self, fact: Fact) -> Optional[Fact]:
        existing = self.store.search_by_entity(fact.subject, min_weight=0.01)
        for e in existing:
            if (e.subject.lower() == fact.subject.lower()
                    and e.predicate == fact.predicate
                    and e.object.lower() == fact.object.lower()):
                return e
        return None

    def _extract_facts(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> tuple[list[Fact], list[dict]]:
        """抽出事實 + 同步回傳 raw_results 給 caller 跑 v1 mirror。

        Bry 拍板 2026-07-18 Stage 1.5 fix: 回傳 (facts, raw_results) tuple,
        讓 caller (extract / extract_and_write / write_turn) 統一在父層呼叫 mirror。
        之前 mirror 嵌在 _extract_facts_llm 內部, heuristic fallback 完全沒跑 mirror,
        結果 LLM judge 400 時所有 agent v1 都寫 0 筆 (Ram 之外其他 agent 也是)。
        """
        # Feature flag: USE_LLM_JUDGE=true/false 切換 LLM judge 跟 regex heuristic
        # 預設開啟 LLM judge;失敗 fallback 舊 regex
        use_llm = os.environ.get("USE_LLM_JUDGE", "true").lower() == "true"
        if use_llm:
            try:
                return self._extract_facts_llm(text, subject_hint, session_id, source)
            except Exception as e:
                logger.warning(
                    f"[MemoryWriter] LLM judge 失敗,fallback heuristic: {e}"
                )
        # fallback 舊 regex (同樣回傳 tuple 結構, 讓 caller mirror 路徑一致)
        return self._extract_facts_heuristic_fallback(
            text, subject_hint, session_id, source
        )

    def _extract_facts_llm(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> tuple[list[Fact], list[dict]]:
        """
        LLM-as-judge 版的 fact 萃取。
        同步介面但內部呼叫 asyncio.run() 跑 async LLMJudge。
        失敗拋 exception,由 _extract_facts 統一 fallback。

        Bry 拍板 2026-07-18 Stage 1.5 fix: 不再內部跑 mirror, 改回傳 (facts, raw_results),
        由 _extract_facts caller (extract / extract_and_write) 統一在父層跑 mirror。
        之前 mirror 嵌這裡導致 heuristic fallback 完全沒 mirror, 修完兩條路徑對齊。
        """
        # 取得 LLMJudge(從 _llm_judge 屬性或 lazy init)
        judge = self._get_llm_judge()
        if judge is None:
            raise RuntimeError("LLMJudge not available")
        # 跑 async extract_and_judge
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        results = loop.run_until_complete(
            judge.extract_and_judge(text, context="", agent_id=subject_hint or "")
        )
        # 轉成 Fact 物件
        facts = []
        seen: set[tuple[str, str, str]] = set()
        for r in results:
            subj = self._normalize_entity(r["subject"], subject_hint)
            obj = self._normalize_object(r["object"])
            if not subj or not obj:
                continue
            key = (subj.lower(), r["predicate"].lower(), obj.lower())
            if key in seen:
                continue
            seen.add(key)
            weight = 0.5 * (0.8 if source == "inference" else 1.0)
            facts.append(Fact(
                subject=subj, predicate=r["predicate"], object=obj,
                timestamp=time.time(), event_time=None,
                weight=weight, confidence=r["confidence"],
                source=source, session_id=session_id,
            ))

        # Stage 1.5 fix: mirror 不在這裡, 統一在 _extract_facts caller 跑
        return facts, results

    def _mirror_extraction(
        self,
        text: str,
        raw_results: list,
        subject_hint: Optional[str],
        session_id: str,
        source: str,
    ) -> None:
        """Stage 1.5 fix (Bry 拍板 2026-07-18): 統一 mirror 入口。

        從 _extract_facts_llm 抽出, 移到 _extract_facts 父層,
        讓 heuristic fallback 也能跑 mirror。
        不論 LLM 路徑或 heuristic 路徑, caller 都呼叫這裡一次。

        保留 Bry §23 spec log 格式 (warning → info + 顯式 n_facts_mirrored / text 預覽),
        跟原 _extract_facts_llm 內部的 log 一致, 不影響 Bry 觀察 mirror 真實寫入。
        """
        # 0 筆短路: log 仍要 fire, 跟原 LLM 路徑 log 對齊 (鏡像觸發了但過濾後 0 筆)
        try:
            # [TEMP-DIAG2] Bry 拍板 2026-08-05 20:13: trace MemoryWriter 全鏈路
            # LLMJudge.trace 顯示 n_triples>0 + 1 個 SUPPORTED 抽出來, 但 mirror 0 筆
            # 矛盾 → 看 raw_results 真實內容 + normalize 後值
            logger.info(
                f"[TEMP-DIAG2] MemoryWriter mirror input | "
                f"agent={subject_hint} | source={source} | "
                f"raw_results_len={len(raw_results)} | "
                f"raw_results={raw_results!r} | "
                f"text={text[:80]!r}"
            )
            mirror_count = self._mirror_to_v1_store(
                text=text,
                results=raw_results,
                subject_hint=subject_hint,
                session_id=session_id,
                source=source,
            )
            logger.info(
                f"[TEMP-DIAG2] MemoryWriter mirror output | "
                f"agent={subject_hint} | mirror_count={mirror_count}"
            )
            if mirror_count and mirror_count > 0:
                logger.info(
                    f"[MemoryWriter] v1 store mirror 成功 | "
                    f"agent={subject_hint} | "
                    f"source={source} | "
                    f"n_facts_mirrored={mirror_count}/{len(raw_results)} | "
                    f"text={text[:50]!r}"
                )
            else:
                logger.info(
                    f"[MemoryWriter] v1 store mirror 0 筆 | "
                    f"agent={subject_hint} | "
                    f"source={source} | "
                    f"judge 抽出 {len(raw_results)} 筆, 但 mirror 過濾後 0 筆 | "
                    f"text={text[:50]!r}"
                )
        except Exception as e:
            # Bry §23 spec: writer 寫進 v1 不失敗主路徑, 但 log 從 warning 升到 ERROR
            logger.error(
                f"[MemoryWriter] v1 store mirror 失敗 | "
                f"agent={subject_hint} | "
                f"source={source} | "
                f"error={e!r}"
            )

    def _mirror_to_v1_store(
        self,
        text: str,
        results: list,
        subject_hint: Optional[str],
        session_id: str,
        source: str,
    ) -> None:
        """Perplexity 拍板 (Bry 轉, 2026-07-02): 把 v6 judge 結果鏡像到 v1 store。

        Bry §12 spec:
        - 寫 (text, category, confidence, tags) per fact
        - 不做語意解讀 / 不做格式轉換
        - 既有資料沒 category/confidence → None (Loader fail-safe 自然排除)

        Data dir 路徑 (Perplexity spec: metadata-only, 不動 SAGE 主路徑):
        - GraphStore 的 db_path = $data_dir/<agent>.db (SQLite)
        - v1 Store 跟 GraphStore 共享同一個 data_dir
        - 但檔案格式不同: GraphStore 用 .db, V1Store 用 .jsonl
        - 所以從 self.store.db_path 反推 self.store.db_path.parent 當 v1 data_dir
        """
        import uuid as _uuid
        from src.memory.v1.store import V1Store as _V1Store
        from src.memory.v1.schema import Memory as _Memory

        # 取得 v1 store data_dir (Bry 拍板 Stage 1.1 路徑統一, 2026-07-18)
        # - 改用 GraphStore.db_path.parent.parent 因為新路徑約定:
        #   GraphStore 寫到 {data_dir}/{agent_id}/graph.sqlite
        #   v1 Store   寫到 {data_dir}/{agent_id}/memories.jsonl (同 agent 子目錄)
        # - 傳給 V1Store 的 data_dir 必須是父目錄 (不含 agent_id 子目錄)
        if not hasattr(self, "store") or self.store is None:
            raise RuntimeError("V1 store 鏡像失敗: writer.store 不存在")
        graph_db_path = getattr(self.store, "db_path", None)
        if graph_db_path is None:
            raise RuntimeError("V1 store 鏡像失敗: GraphStore.db_path 不存在")
        # graph_db_path = data/{data_dir}/{agent_id}/graph.sqlite
        # v1_data_dir  = data/{data_dir}/  (parent.parent)
        v1_data_dir = Path(graph_db_path).parent.parent

        # Bry 拍板 2026-07-18 Stage 1.6: V1Store agent_id 必須是真正的 agent id (e.g. "agent_rem"),
        # 不是 subject_hint ("user"/"assistant")。subject_hint 是 role 概念, 跟 v1 鏡像歸屬無關。
        # priority: self.agent_id > subject_hint if it looks like agent_id (starts with "agent_") > "unknown"
        v1_agent_id = (
            self.agent_id
            if self.agent_id
            else (subject_hint if (subject_hint and subject_hint.startswith("agent_")) else "unknown")
        )
        v1_store = _V1Store(v1_data_dir, v1_agent_id)
        # [TEMP-DIAG2] Bry 拍板 2026-08-05 20:13: 印 v1_store 寫入路徑 + self.agent_id
        # 確認 mirror 寫到哪個檔案 (Bry 派工「要看內容判斷在哪一步被歸零」)
        logger.info(
            f"[TEMP-DIAG2] v1_store path | "
            f"self.agent_id={self.agent_id!r} | "
            f"subject_hint={subject_hint!r} | "
            f"v1_agent_id={v1_agent_id!r} | "
            f"v1_data_dir={v1_data_dir} | "
            f"v1_store_file={v1_store.store_file}"
        )
        # Perplexity Bry §15 拍板: 補齊 tags 的檢索用途缺陷
        # - content 字面切詞追加進 tags (跟 middleware._derive_query_tags 同套邏輯)
        # - category 標籤保留不刪
        # - 沒牽動 judge 的 category/confidence 判斷邏輯
        # - 純機械動作, 不用 LLM, 不做語意判斷
        from src.memory.v1.loader import derive_query_tags as _derive_tags
        # Bry §23 spec: 回傳 mirror 筆數給 caller log 用
        mirror_count = 0
        for r in results:
            subj = self._normalize_entity(r["subject"], subject_hint)
            obj = self._normalize_object(r["object"])
            if not subj or not obj:
                continue
            # 組裝 content: 簡單三元組文字 (Perplexity 拍: 不做語意解讀, 純組裝)
            content = f"{subj} {r['predicate']} {obj}"
            # Bry §15: tags = 既有 tags (含 category) + content 切詞
            # 兩者共存, 不覆蓋
            existing_tags = list(r.get("tags", []))
            content_tags = _derive_tags(content)
            merged_tags = existing_tags + content_tags
            v1_store.add(_Memory(
                memory_id=str(_uuid.uuid4()),
                agent_id=v1_agent_id,
                content=content,
                tags=merged_tags,
                created_at=time.time(),
                # v1.1 schema 加的兩個 Optional 欄位, Perplexity (b)
                category=r.get("category"),
                confidence=r.get("confidence"),
                # M5.4-5.2: Inner Life canonical reference (mirror 跟 graph 同步)
                # 由 extract_and_write 設定到 r["inner_life_event_id"] (跟 Fact.inner_life_event_id 同源)
                inner_life_event_id=r.get("inner_life_event_id"),
            ))
            mirror_count += 1
        return mirror_count

    def _get_llm_judge(self):
        """Lazy init LLMJudge。需要 LLMProxy 跟 LLM backend 已經 set up。"""
        if hasattr(self, "_llm_judge_cached") and self._llm_judge_cached is not None:
            return self._llm_judge_cached
        # 從 GraphStore 路徑反向找 LLMProxy(從 process-global 變數)
        llm_proxy = globals().get("_global_llm_proxy") or _find_llm_proxy()
        if llm_proxy is None:
            return None
        from src.memory.llm_judge import LLMJudge
        self._llm_judge_cached = LLMJudge(llm_proxy)
        return self._llm_judge_cached

    def _extract_facts_heuristic_fallback(
        self, text: str, subject_hint: Optional[str],
        session_id: str, source: str
    ) -> tuple[list[Fact], list[dict]]:
        """Bry 拍板 2026-07-18 Stage 1.5 fix: 改回傳 (facts, raw_results) tuple。

        raw_results 從 facts 機械合成 (subject/predicate/object 從 Fact 拿,
        category="heuristic", confidence=base_weight, tags=[]),
        跟 LLM 路徑的 results 結構對齊, 讓 _mirror_to_v1_store 統一處理。
        """
        facts: list[Fact] = []
        seen: set[tuple[str, str, str]] = set()
        # Primary split: sentence-ending punctuation or newlines
        primary = re.split(r"(?<=[.!?。！？])\s+|[\n]+", text)
        for segment in primary:
            # Secondary split: conjunctions after sentence boundary
            sub_parts = re.split(
                r"(?<=[.!?])\s+(?:but|however|although|so|then)\s+",
                segment, flags=re.IGNORECASE,
            )
            for sentence in sub_parts:
                sentence = sentence.strip()
                if len(sentence) < 4:
                    continue
                event_time = self._extract_event_time(sentence)
                for pattern, predicate, base_weight in _RELATION_PATTERNS:
                    m = re.search(pattern, sentence, re.IGNORECASE)
                    if not m or len(m.groups()) < 2:
                        continue
                    subj_raw = m.group(1).strip()
                    obj_raw  = m.group(2).strip()
                    subj = self._normalize_entity(subj_raw, subject_hint)
                    obj  = self._normalize_object(obj_raw)
                    if not subj or not obj:
                        continue
                    if obj.lower() in _NOISE_OBJECTS:
                        continue
                    if len(subj) > 60 or len(obj) > 100:
                        continue
                    norm_pred = _PREDICATE_SYNONYMS.get(predicate, predicate)
                    key = (subj.lower(), norm_pred, obj.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    weight = base_weight * (0.8 if source == "inference" else 1.0)
                    facts.append(Fact(
                        subject=subj, predicate=norm_pred, object=obj,
                        timestamp=time.time(), event_time=event_time,
                        weight=weight, confidence=base_weight,
                        source=source, session_id=session_id,
                    ))

        # Stage 1.5 fix: 機械合成 raw_results 給 mirror 路徑用
        # 結構跟 LLM 路徑的 results 對齊:
        #   subject / predicate / object 從 Fact 拿
        #   category 固定 "heuristic" (Loader 看得懂, 跟 LLM 區分)
        #   confidence 用 Fact.confidence (= base_weight)
        #   tags=[] (heuristic 沒有 LLM 切的 tag, 但 _mirror_to_v1_store 內部會
        #   透過 _derive_tags(content) 補 content 切詞, 所以 tags 不會空)
        raw_results: list[dict] = []
        for f in facts:
            raw_results.append({
                "subject":   f.subject,
                "predicate": f.predicate,
                "object":    f.object,
                "category":  "heuristic",
                "confidence": f.confidence,
                "tags":      [],
            })
        return facts, raw_results


def _find_llm_proxy():
    """從 process-global 或 runner 變數找 LLMProxy。

    為什麼需要:MemoryWriter._get_llm_judge() 需要 LLMProxy 實例,
    但 MemoryWriter 是由 SAGELiteProvider 在 lifespan 內建構,
    沒辦法直接 import run_server 的 proxy 全域變數。
    解法:MemoryMiddleware 或 run_server 透過 set_llm_proxy() 設定,
    這裡 fallback 到 process-global 變數查詢。
    """
    return globals().get("_soul_os_llm_proxy")


def set_llm_proxy(llm_proxy):
    """設定 process-global LLMProxy reference,讓 MemoryWriter 可以拿到。"""
    globals()["_soul_os_llm_proxy"] = llm_proxy