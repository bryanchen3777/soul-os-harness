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
import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        top_k_per_category: int = 3,
    ) -> Dict[str, Any]:
        """Bry §12: 跑 retrieve + gating + trace + 回傳 inject-ready 記憶列表。

        Args:
            query_tags: 跟 v1.retrieve 一樣的 tag 過濾
            agent_id: 例如 "agent_rem"
            top_k_per_category: Bry §12 spec 沒限定細節, 用 3 個 per category 當
                起始值 (之後 Bry §13 再精細)。

        Returns:
            dict with keys:
              - response_id (str, uuid4)
              - eligible_memories: List[Memory] 給 prompt 注入
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
        # 注意: 不改 query_tags, 只改 memory.tags 的 overlap 計算

        # Step 4: 用 query_tags 過濾
        # Perplexity Bry §18 (b): overlap 詞數 ≥ MIN_OVERLAP_FOR_CANDIDATE (默認 2)
        # Bry §17 揭穿 "只命中 1 詞" 是弱證據, "雷姆" 這種高頻詞會導致 80% 噪音命中
        query_set = set(query_tags)
        by_tags: List[Memory] = []
        for m in same_agent:
            mem_tags_filtered = set(m.tags) - agent_name_stopwords
            overlap_count = len(query_set & mem_tags_filtered)
            if overlap_count >= MIN_OVERLAP_FOR_CANDIDATE:
                by_tags.append(m)

        # Step 5: 對每筆做 confidence threshold gating + 標 status
        candidates_with_status: List[Dict[str, Any]] = []
        eligible: List[Memory] = []
        for m in by_tags:
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
                })
                continue
            th = threshold_for(m.category)
            # Bry §12 fail-safe #2: top1 confidence < threshold → 拒
            if m.confidence < th:
                status = "rejected_low_confidence"
            else:
                status = "selected"
                eligible.append(m)
            candidates_with_status.append({
                "memory_id": m.memory_id,
                "category": m.category,
                "tags": m.tags,
                "confidence": m.confidence,
                "confidence_threshold": th,
                "status": status,
            })

        # Step 6: Bry §12 fail-safe #3 - eligible 空 → 全空 trigger
        if not eligible:
            eligible = []
            fail_safe_triggered = "all_rejected_low_confidence"
        else:
            fail_safe_triggered = None

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

# 極簡中英停用詞 (跟 middleware._derive_query_tags 同份, 集中放這裡)
_SHARED_STOPWORDS = {
    "的", "了", "是", "我", "你", "他", "她", "它", "在", "有", "沒", "和", "或",
    "就", "也", "都", "還", "會", "要", "不", "嗎", "吧", "啊", "呢", "的話",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "his", "her",
    "this", "that", "these", "those", "and", "or", "but", "if", "so", "as",
}


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
MIN_OVERLAP_FOR_CANDIDATE = 2


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
