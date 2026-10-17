"""
src/memory/v1/loader.py
Bry §12 (2026-07-02): Memory Loader v1

4 件事:
1. Memory type gating (per-category confidence threshold)
2. Hard fail-safe (None retrieved → empty; low conf → empty)
3. Trace 記錄 (只記發生什麼, 不解釋)
4. 給 LLMProxy / prompt 注入用

Bryan 原則 (Bry §12 唯一裁決標準): No Memory > Wrong Memory.
任何時候信心不足,寧可不注入, 也不塞低品質記憶。

Bry §12 排除 (硬規則, 不是建議):
- ❌ 不做 span-level attribution
- ❌ 不用 LLM 做 reason/explain
- ❌ 不做 semantic interpretation
- ❌ 不做 diary 內容拆分
- ❌ 不重調 judge 邏輯
- ❌ 不做「順手」第 5 項
"""
from __future__ import annotations

import json
import math
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .retrieval import Memory, V1Store  # Bry §12 同 v1 記憶系統
from .schema import Memory as MemoryDataclass  # Bry §12 不混,但 store 用 frozen Memory

logger = logging.getLogger("soul_os.memory.v1.loader")

# Bry §12: 信心門檻 (粗分級起始值)
CONFIDENCE_THRESHOLDS = {
    "identity": 0.90,
    "fact": 0.80,
    "preference": 0.75,
    "diary": 0.60,
}


def threshold_for(category: str) -> float:
    """Bry §12: per-category 門檻。"""
    cat = category.strip().lower()
    return CONFIDENCE_THRESHOLDS.get(cat, 0.75)


def is_candidate_eligible(memory: Memory, threshold: float) -> bool:
    """Bry §12 + v1.1 schema: 一個記憶是否過門檻可被選。

    Perplexity 拍板 (Bry 轉, 2026-07-02): 沒 category 或 confidence 的舊資料,
    自動 None fail-safe — Loader 不注入。
    """
    # confidence 未設定的舊資料 → fail-safe
    if memory.confidence is None:
        return False
    # category 未設定 → fail-safe
    if memory.category is None:
        return False
    return memory.confidence >= threshold


# ────────────────────────────────────────────────────────────
# Loader 主邏輯
# ────────────────────────────────────────────────────────────

class MemoryLoader:
    """Memory Loader v1 + Trace v1.

    Bry §12 spec:
    - retrieve memories by tags / category
    - per-category confidence threshold gating
    - hard fail-safe: 空 retrieved → []; top1 低於門檻 → []
    - 每次跑 log 一個 structured trace (response_id / candidates / status)
    - 不重調 judge, 不做 semantic interpretation
    """

    def __init__(
        self,
        store: V1Store,
        trace_log_path: Optional[Path] = None,
    ):
        self.store = store
        self.trace_log_path = trace_log_path
        if trace_log_path is not None:
            trace_log_path.parent.mkdir(parents=True, exist_ok=True)

    def load(
        self,
        query_tags: List[str],
        agent_id: str,
        top_k_per_category: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Bry §12: 跑 retrieve + gating + 分級排序 + trace + 回傳 inject-ready 記憶列表。

        Args:
            query_tags: 跟 v1.retrieve 一樣的 tag 過濾
            agent_id: 例如 "agent_rem"
            top_k_per_category: **SAGE-RANK-1（2026-10-17）** 排序後的最大回傳筆數。
                `None`（預設）＝ **不截斷，全量回傳**，維持既有生產注入量契約。
                舊預設值 `3` 從未被任何呼叫端使用（全庫僅簽章與 docstring 提及），
                若沿用該預設會無預警把注入量砍到 3 筆，故改為 `None`。
                ⚠️ 命名債：名稱寫 per_category 但實作為「總量上限」，
                保留原名是為了不破壞可能的外部呼叫端簽章相容性。
                已登記為待清技術債。

        Returns:
            dict with keys:
              - response_id (str, uuid4)
              - eligible_memories: List[Memory] 給 prompt 注入，**已按 Rank 排序**
              - trace: dict structured 包含 candidates / statuses
        """
        response_id = str(uuid.uuid4())
        timestamp = time.time()

        # Step 1: Bry §12 fail-safe #1 - 空 retrieved 直回
        if not query_tags:
            # 沒有 query_tags 也回空 (跟 v1.retrieve 行為一致)
            eligible: List[Memory] = []
            trace = self._build_trace(
                response_id=response_id,
                timestamp=timestamp,
                agent_id=agent_id,
                candidates=[],
                eligible_memories=[],
                fail_safe_triggered="empty_query_tags",
            )
            self._append_trace(trace)
            return {
                "response_id": response_id,
                "eligible_memories": eligible,
                "trace": trace,
            }

        # Step 2: 用 v1.retrieve 抓 candidate 池
        try:
            all_memories = self.store.all()  # v1: 全讀 (no index)
        except Exception as e:
            logger.warning(f"[MemoryLoader] store.all() failed: {e}")
            eligible = []
            trace = self._build_trace(
                response_id=response_id,
                timestamp=timestamp,
                agent_id=agent_id,
                candidates=[],
                eligible_memories=[],
                fail_safe_triggered="store_error",
            )
            self._append_trace(trace)
            return {
                "response_id": response_id,
                "eligible_memories": eligible,
                "trace": trace,
            }

        # Step 3: 過濾掉 agent_id 不同的 (multi-agent system 不混)
        same_agent = [m for m in all_memories if m.agent_id == agent_id]

        # Perplexity Bry §18 (c): per-agent name stopword 從 memory.tags 排除
        # "agent 自己的名字在自己記憶庫裡不具區分力" (雷姆 在 agent_rem store 裡幾乎每筆都有)
        # 在計算 overlap 時, 從 memory.tags 拿掉 agent 自己名字, 不算命中
        agent_name_stopwords = _PER_AGENT_NAME_STOPWORDS.get(agent_id, set())

        # Step 4: 用 query_tags 過濾
        # Perplexity Bry §25 (b) 拍板 (2026-07-02): MIN_OVERLAP 按 content token 數分層
        # - content tokens < 20 → MIN_OVERLAP=1
        # - content tokens >= 20 → MIN_OVERLAP=2 (Bry §18 拍板的原值)
        # - 機械可量化, 不涉及語意判斷, 跟 per-agent stopword 性質一致
        # - Bry §25 spec 強調: 這不解決根本問題, 預期命中率從 0/5 變 1/5 左右
        import re as _re_overlap
        # Bry 拍板 2026-07-18 Stage 2.2: symmetric stopword
        # 之前只 strip memory 側, 導致 query="雷姆" 找不到 memory (因為唯一 match 的 tag 被 strip 掉)
        # 修法: query 側跟 memory 側都 strip, 保持對稱, 符合 Bry §18 設計初衷
        #
        # M5.3-S2-B (Bry 派工 2026-08-09 15:27): symmetric tag normalization
        # 病因: memory.tags 是 phrase-based (e.g. "雷姆和姊姊", "姊姊的角"),
        #       query 端用 jieba tokenize 切成 ["雷姆", "姊姊"], 結構性 mismatch 導致 by_tags_empty.
        # 修法: query 跟 memory 兩端都用同一份 derive_query_tags() 展開成 component set,
        #       overlap 在 component level 計算, 維持 token boundary (jieba 不引入 substring collision).
        # 保留: MIN_OVERLAP 門檻 / is_candidate_eligible / confidence gate / fail_safe 全部不動.
        query_components = _expand_tag_components(query_tags) - agent_name_stopwords

        # ── SAGE-RANK-1（2026-10-17，Owner 裁示「案 C：Corpus-Local IDF 加權＋Boost 排序」）
        #
        # 為什麼需要它：2026-10-17 以 3,307 筆生產記憶實測漏斗，發現真正的瓶頸
        # **不是**「順序亂」，而是**召回被高 DF 通用詞稀釋**——writer 機械組裝的
        # phrase-based tags 裡充滿「記得 / 事情 / 對方 / 說話者 / 表示」這類片段。
        # 實測 agent_yua 一句 query 就命中 39 筆全部通過 overlap 門檻，最後被
        # confidence 門檻殺到剩 10 筆。手寫的 `_LOW_INFO_TOKENS` 擋不住它們
        # （那是封閉詞表），純次數加權也擋不住（它們就是「命中很多次」）。
        # Corpus-local IDF 讓 df 高的通用詞權重自動趨近下界、df=1 的稀有專名
        # （如「鬼族」「權利」）權重最大，**不必無止境維護手寫停用詞表**。
        #
        # 零額外分詞成本：`mem_components` 這一趟本來就要算（給 overlap 用），
        # df 只是在同一個迴圈裡順帶累加。N = 該 agent 自己的記憶庫大小。
        token_df: Dict[str, int] = {}
        corpus_size = len(same_agent)

        # by_tags 現在攜帶 (memory, informative, low_info_hits)，
        # 因為 ranking 需要 Gate 階段已算出的 token 集合，重算一次就是浪費。
        by_tags: List[tuple] = []
        for m in same_agent:
            mem_components = _expand_tag_components(m.tags) - agent_name_stopwords
            for tok in mem_components:
                token_df[tok] = token_df.get(tok, 0) + 1
            matched = query_components & mem_components
            # 分級重疊 · Gate 側（SOUL-BUG-002，2026-10-14 Owner 裁示）：
            # 只有「實質內容詞」能滿足門檻。零區辨力 token（今天／什麼／怎麼…）
            # 單獨命中時不放行檢索，但仍完整保留在 `matched` 供 trace 觀測。
            # 實測修復前 agent_rem 有 6 筆這樣的誤召回（什麼×4 今天×2）。
            informative = matched - _LOW_INFO_TOKENS
            # 計算 content token 數 (中文每字 1 token, 英文單字 1 token)
            m_content = m.content or ""
            m_content_tokens = (
                len(_re_overlap.findall(r"[一-鿿]", m_content))
                + len(_re_overlap.findall(r"\b\w+\b", m_content))
            )
            # 分層門檻（Bry §25(b)：機械可量化，不涉及語意判斷）
            threshold_for_mem = (
                MIN_OVERLAP_SHORT_CONTENT
                if m_content_tokens < MIN_OVERLAP_CONTENT_BOUNDARY
                else MIN_OVERLAP_LONG_CONTENT
            )
            if len(informative) >= threshold_for_mem:
                by_tags.append((m, informative, matched & _LOW_INFO_TOKENS))

        # Step 5: 對每筆做 confidence threshold gating + 標 status
        candidates_with_status: List[Dict[str, Any]] = []
        eligible: List[Memory] = []
        # rank_keys: 與 eligible 平行，第 i 個元素的 key 即第 i 筆記憶的排序鍵。
        rank_keys: List[tuple] = []
        for m, informative, low_info_hits in by_tags:
            # SAGE-RANK-1: Rank / Boost 計分。
            primary = _primary_score(informative, token_df, corpus_size)
            boost = RANK_LOW_INFO_BOOST * len(low_info_hits)
            # Perplexity 拍板 (Bry 轉, 2026-07-02): v1.1 schema 加 category/confidence,
            # 既有資料沒有這兩欄位的記憶 → 走 fail-safe (low_confidence) 不注入。
            if m.category is None or m.confidence is None:
                candidates_with_status.append({
                    "memory_id": m.memory_id,
                    "category": m.category,
                    "tags": m.tags,
                    "confidence": m.confidence,
                    "confidence_threshold": None,
                    "status": "rejected_no_v11_metadata",
                    "informative_hits": len(informative),
                    "low_info_hits": len(low_info_hits),
                    "primary_score": primary,
                    "boost_score": boost,
                })
                continue
            th = threshold_for(m.category)
            # Bry §12 fail-safe #2: top1 confidence < threshold → 拒
            if m.confidence < th:
                status = "rejected_low_confidence"
            else:
                status = "selected"
                eligible.append(m)
                rank_keys.append((
                    -primary,
                    -boost,
                    -m.confidence,
                    -(m.created_at or 0.0),
                    m.memory_id,
                ))
            candidates_with_status.append({
                "memory_id": m.memory_id,
                "category": m.category,
                "tags": m.tags,
                "confidence": m.confidence,
                "confidence_threshold": th,
                "status": status,
                "informative_hits": len(informative),
                "low_info_hits": len(low_info_hits),
                "primary_score": primary,
                "boost_score": boost,
            })

        # Step 6: Bry §12 fail-safe #3 - eligible 空 → 全空 trigger
        if not eligible:
            eligible = []
            fail_safe_triggered = "all_rejected_low_confidence"
        else:
            fail_safe_triggered = None

        # ── SAGE-RANK-1 Step 6.5: 分級重疊 · Boost 側（排序）
        #
        # 排序鍵由 Bry 2026-10-17 拍板的三段式階層決定：
        #   ① 主導：稀有實詞的 corpus-local IDF 總和（RANK_IDF_WEIGHT 加權）
        #   ② 次要：低資訊／時間詞命中數的小幅 Boost
        #   ③ 平手：confidence → created_at（新→舊）→ memory_id
        # memory_id 最後保證全序，即使前面四項完全相同也不會有殘餘不確定性。
        #
        # 這裡排序**不會**改變任何記憶是否被注入（Gate 在前面已決定），
        # 只改變它們在 prompt 裡的先後 —— 而 format_for_prompt 是順序透傳的
        # （AST 凍結，不可改），所以排序結果會 1:1 反映到 system prompt。
        # 連帶效果：continuity sidecar 的 injection_rank 語意改變（這是預期的）。
        order = sorted(range(len(eligible)), key=lambda i: rank_keys[i])
        eligible = [eligible[i] for i in order]
        if top_k_per_category is not None:
            eligible = eligible[:top_k_per_category]

        # Step 7: 寫 trace
        trace = self._build_trace(
            response_id=response_id,
            timestamp=timestamp,
            agent_id=agent_id,
            candidates=candidates_with_status,
            eligible_memories=eligible,
            fail_safe_triggered=fail_safe_triggered,
        )
        self._append_trace(trace)

        return {
            "response_id": response_id,
            "eligible_memories": eligible,
            "trace": trace,
        }

    # ── Trace helpers (Bry §12: 只記錄, 不解釋 / 不評估) ──

    def _build_trace(
        self,
        response_id: str,
        timestamp: float,
        agent_id: str,
        candidates: List[Dict[str, Any]],
        eligible_memories: List[Memory],
        fail_safe_triggered: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "response_id": response_id,
            "timestamp": timestamp,
            "agent_id": agent_id,
            "candidates": candidates,
            "eligible_count": len(eligible_memories),
            "fail_safe_triggered": fail_safe_triggered,
            # Bry §12: "做了什麼"
            "events": [
                {"step": "store.all", "ok": True},
                {"step": "tag_filter", "ok": True},
                {"step": "confidence_gating", "ok": True},
            ],
        }

    def _append_trace(self, trace: Dict[str, Any]) -> None:
        """Bry §12: append-only structured log。"""
        if self.trace_log_path is None:
            return
        try:
            with open(self.trace_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[MemoryLoader] trace write failed: {e}")


# ────────────────────────────────────────────────────────────
# Prompt 注入 helpers (Bry §12 §4 真實注入測試用)
# ────────────────────────────────────────────────────────────

def format_for_prompt(eligible_memories: List[Memory]) -> str:
    """Bry §12: 將 eligible memories 格式化為 prompt 注入字串。

    簡單列表, 不做 semantic interpretation。
    """
    if not eligible_memories:
        return ""
    lines = ["[Recall relevant memories]"]
    for m in eligible_memories:
        # Bry §12: 不加 LLM generated explanation,只列原文
        tag_str = ",".join(m.tags)
        lines.append(f"- ({m.category}, conf {m.confidence:.2f}, tags={tag_str}): {m.content}")
    lines.append("[/Recall]")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────
# 共享 query/content 字面切詞 (Perplexity Bry §15 拍板)
# ────────────────────────────────────────────────────────────

# 極簡中英**功能詞**停用詞（本模組的切詞階段唯一真相來源）
# ⚠️ 2026-10-14 更正：舊註解寫「跟 middleware._derive_query_tags 同份」是**過期**的——
#    `src/memory/middleware.py` 內並無對應常數，本清單只存在於此處。
#    維持極簡是有意的：這張表只收「語法上不帶語意」的功能詞。
#    「今天 / 什麼 / 怎麼」這類**高頻但帶語意**的實詞刻意不在這裡，
#    它們由 `_LOW_INFO_TOKENS` 以不同機制處理（見下）。
_SHARED_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "沒", "和", "或",
    "就", "也", "都", "還", "會", "要", "不", "嗎", "吧", "啊", "呢", "的話",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her",
    "this", "that", "these", "those", "and", "or", "but", "if", "so", "as",
}


# SOUL-BUG-002（2026-10-14，Owner 裁示「分級重疊 · Gate 側」）：低資訊 token 集合。
#
# 與 `_SHARED_STOPWORDS` **語意不同，不可合併**：
#   _SHARED_STOPWORDS = 語法虛詞（的/了/是…）→ 切詞階段直接丟棄，兩端皆不參與。
#   _LOW_INFO_TOKENS  = 高頻但零區辨力的**實詞**（今天／什麼／怎麼…）→
#                       切詞階段**必須保留**（它們仍承載語意），只是不得**單獨**
#                       滿足檢索門檻。否則 query="今天 天氣 怎麼 樣" 會命中
#                       「Bryan 起得很早 今天」這類與問題無關的記憶。
#
# 收錄標準（反作弊：收詞類，不收測試字面）：
#   ① 時間指示詞——在 Bry 的記憶語料裡幾乎每筆都出現，0 區辨力。
#   ② 疑問詞——單獨出現時不指向任何特定記憶。
# 刻意**不收**任何「含實體名詞的複合詞」：實測確認「天氣」並未誤命中任何記憶，
# 把「今天天氣」這類複合詞塞進來只會像是照著測試字面寫死。
_LOW_INFO_TOKENS = frozenset({
    # 時間指示
    "今天", "今日", "本日", "昨天", "昨日", "明天", "現在", "此刻",
    "剛剛", "剛才", "最近", "待會", "等一下",
    # 疑問詞（句末語氣助詞「嗎/呢/啦」刻意不收：它們是語法虛詞，
    # 已由 _SHARED_STOPWORDS 在切詞階段丟棄，放這裡會讓兩張表語意重疊）
    "什麼", "怎麼", "怎樣", "怎麼樣", "為什麼", "為啥", "為何",
    "哪個", "哪裡", "哪些", "誰", "多少", "如何",
})


# Perplexity Bry §18 拍板 (2026-07-02): per-agent high-frequency-name stopword
# 理由: agent 自己的名字 (e.g. "雷姆" 在 agent_rem store 裡) 幾乎每筆記憶都有,
# 跟 SHARED_STOPWORDS 排除虛詞同一邏輯, 只是 per-agent 而不是全局共用。
# 不是語意判斷, 是機械規則 — "agent 自己的名字在自己記憶庫裡不具區分力"。
#
# 不用自動統計, 手動列已知專名:
_PER_AGENT_NAME_STOPWORDS = {
    "agent_rem": {"雷姆", "rem", "雷姆你", "昴"},
    "agent_ram": {"拉姆", "ram", "昴"},
}


# Perplexity Bry §18 (b) 拍板: candidate 池准入條件從 overlap ≥ 1 改成 ≥ 2
# 不管 memory 長短都一致適用, "只命中一個詞" 在任何文本裡都天然是弱證據。
MIN_OVERLAP_FOR_CANDIDATE = 2  # 保留舊常數, 但 Bry §25 改用分層門檻


# Perplexity Bry §25 (b) 拍板 (2026-07-02): MIN_OVERLAP 按 content token 數分層
# - short content (< 20 tokens) → 較低門檻
# - long content (>= 20 tokens) → 較高門檻 (跟 Bry §18 拍板原值一致)
# - 機械可量化, 不涉及語意判斷
# - 邊界值 20 tokens 是 Bry §25 spec 拍板的「簡單機械判斷」, 不是調參
MIN_OVERLAP_CONTENT_BOUNDARY = 20
MIN_OVERLAP_SHORT_CONTENT = 1
MIN_OVERLAP_LONG_CONTENT = 2


# ── SAGE-RANK-1（2026-10-17，Owner 裁示）：分級重疊 · Boost 側計分權重
#
# ⚠️ 權重比例是被 Owner 親自校準過的，不要「順手」改動。
#
# 陷阱背景：單一 agent 的記憶庫 N ≈ 350~998，ln(N/(1+df)) 的上限只有 ~5~6.5。
# 若把 confidence 乘 10、low_info 加 1，一筆 conf=0.95 但只命中高頻廢詞
# 「記得」（idf≈1.2 → 1.2+9.5=10.7）的記憶，會輾壓一筆 conf=0.75 但
# 精準命中稀有詞「鬼族」（idf≈5.2 → 5.2+7.5=12.7）的記憶。
# ⇒ IDF 必須是**主導項**，confidence **只當平手破除器**，不進加權和。
#
# 三段式階層（Bry 拍板）：
#   ① 10.0 × Σ idf(t)   —— 主導。因為 _corpus_idf 保底 +1.0，單一實詞命中
#                          最低貢獻 10.0 分，等於要 10 個低資訊詞命中才追平。
#   ② 1.0 × 低資訊命中數 —— 次要 Boost，在同實詞權重下把帶時間詞的記憶浮上來。
#   ③ confidence → created_at → memory_id —— 平手破除。
RANK_IDF_WEIGHT = 10.0
RANK_LOW_INFO_BOOST = 1.0
RANK_IDF_SMOOTHING = 1.0


def _corpus_idf(token_df: int, corpus_size: int) -> float:
    """SAGE-RANK-1: 平滑 corpus-local IDF。

    idf(t) = log((N + 1) / (df + 1)) + 1.0

    - `+1` 平滑：避免 df=0 時除零、也避免 log 0。
    - `+ RANK_IDF_SMOOTHING` 保底：讓 idf **恆正且 ≥ 1.0**。
      這是權重比例能成立的前提——若某 token 滿庫都是（df=N），
      它的權重會塌到 0 甚至負值，那一筆記憶就永遠不可能靠它排前面。
      保底 1.0 代表「最無區辨力的詞也有 10.0×1.0 的底份」，
      但因為高 df 的詞會無限多筆互相競爭，仍會被稀有詞壓過去。
    """
    return math.log((corpus_size + 1) / (token_df + 1)) + RANK_IDF_SMOOTHING


def _primary_score(
    informative: "Iterable[str]",
    token_df: Dict[str, int],
    corpus_size: int,
) -> float:
    """SAGE-RANK-1: 主導項分數 = RANK_IDF_WEIGHT × Σ idf(t)。

    ⚠️ `sorted(informative)` **不是多餘的**。set 的迭代順序受 PYTHONHASHSEED
    影響，而浮點加總不具結合律 —— 3 個以上 term 時，(a+b)+c 與 a+(b+c) 可能差在
    最後幾個 ulp。若平手時靠分數高低破除，這個差異足以讓排序在不同行程間漂移，
    違反 Owner 要求的「排序 100% 確定性」。排序輸入即固定求和順序。
    """
    return RANK_IDF_WEIGHT * sum(
        _corpus_idf(token_df.get(tok, 0), corpus_size) for tok in sorted(informative)
    )


def _expand_tag_components(tags: List[str]) -> set:
    """M5.3-S2-B (Bry 派工 2026-08-09 15:27): Symmetric tag normalization.

    對 input list of tags 每個元素跑 derive_query_tags() 取 component tokens,
    union 成一個 set. 確保 query 端跟 memory 端用同一份 normalization semantics
    做 overlap 比較.

    Same input semantics → same normalization → same overlap calculation.

    Safety:
    - 單 token tag (e.g. "雷姆", "bryan") → derive_query_tags 輸出同樣單 token
    - Phrase tag (e.g. "雷姆和姊姊") → derive_query_tags 拆成 ["雷姆", "姊姊", "雷姆和姊姊"]
    - No substring matching (jieba 維持 token boundary, 不會 "京" 拆 "東京")
    - 1-char tokens filtered (在 derive_query_tags 內)
    - Stopwords filtered (在 derive_query_tags 內)
    - 數字 / 純標點 filtered (在 derive_query_tags 內)

    用法: 對 query_tags 跟 m.tags 都用這個展開, 然後在 component level 做 set intersection.
    """
    components: set = set()
    for tag in tags:
        for tok in derive_query_tags(tag):
            components.add(tok)
    return components


def derive_query_tags(text: str) -> list:
    """中英分詞切詞 (Perplexity Bry §16 拍板):
    - 中文用 jieba (中英混排時 jieba 也能切英文單字)
    - 英文/數字部分用 regex \\w+ 補強 (jieba 對純英文短語切成整段)
    - 過濾 stopwords 跟 len<=1 的 token
    - 不使用 LLM, 不做任何語意判斷 / 同義詞擴展

    使用者:
    - middleware._derive_query_tags (Bry §14 hook): query 切詞
    - writer._mirror_to_v1_store (Bry §15 patch): content 切詞追加進 memory.tags

    集中放這裡避免兩份不同步的切詞邏輯。

    Perplexity Bry §16: \\w+ 對中文不切 (整段當 1 token), 必須用 jieba 真分詞,
    否則真實 user 打字問 Rem 時查詢字面跟記憶字面不會 match。
    """
    import re as _re

    if not text:
        return []

    text_lower = text.lower().strip()
    tokens: list = []

    # ── 中文路徑: jieba 切詞 (對中英混排也 work, 英文單字也切出來) ──
    # 用 lazy import 避免每次 Loader.load 重建 prefix dict (~0.5s)
    try:
        import jieba
        # suppress jieba initial stderr noise (per-call)
        import os as _os
        import io as _io
        from contextlib import redirect_stderr
        try:
            with redirect_stderr(_io.StringIO()):
                jieba_cuts = list(jieba.cut(text_lower, cut_all=False))
        except Exception:
            jieba_cuts = []
        # jieba 切詞後, 過濾空白跟標點, 保留中文詞 + 英文單字
        for cut in jieba_cuts:
            stripped = cut.strip()
            if stripped and len(stripped) > 0:
                tokens.append(stripped)
    except ImportError:
        # fallback: 沒裝 jieba 時用原本 regex (Bry §15 版本)
        tokens.extend(_re.findall(r"\w+", text_lower))

    # ── 英文/數字補強路徑: regex \\w+ 確保純英文短語也切對 ──
    # jieba 對純英文短語 (e.g. "React useState") 切成 ['react', 'usestate'] 1 個 word
    # regex \\w+ 會切 ['react', 'usestate'] 2 個 word
    # 兩個路徑 union, 重複的 (e.g. lowercase) 自然在後面過濾
    en_tokens = _re.findall(r"\w+", text_lower)
    tokens.extend(en_tokens)

    # ── 過濾 stopwords + 純數字 + len<=1 ──
    result: list = []
    seen: set = set()
    for t in tokens:
        t_clean = t.lower()
        if t_clean in seen:
            continue
        seen.add(t_clean)
        # 純數字 (像 '1234567') 排除
        if t_clean.isdigit():
            continue
        # 單字符 (英文 1 字 / 中文標點) 排除
        if len(t_clean) <= 1:
            continue
        # stopword 排除
        if t_clean in _SHARED_STOPWORDS:
            continue
        result.append(t_clean)

    return result
