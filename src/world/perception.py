"""
src/world/perception.py — Soul OS M3 Phase 1

World Awareness 資料結構 + deterministic scoring。

Bry 拍板 2026-08-07 19:40 派工:
- 5 個物件分清楚 (WorldEvent / WorldPerception / PerceptionDecision / WorldContext / WorldPerceptionTrace)
- personal_significance 必須由 deterministic evaluator 計算,不能從 WorldEvent payload 拿
- 不為假設中的未來灑過濾網 (Phase 1 只做 4 個 scoring 維度, 留架構彈性給 Phase 2)

Phase 1 設計 (跟 brief 對齊):
    WorldEvent
        ↓
    WorldPerception (state 中儲存, ephemeral)
        ↓
    PerceptionDecision (accepted / rejected + reason + scores)
        ↓
    WorldContext (top-N events 渲染成 prompt 注入字串)
        ↓
    WorldPerceptionTrace (sidecar log, observability artifact)

5 個物件的差別 (Bry 拍板):
- WorldEvent              : 客觀世界事實 (從 source 來)
- WorldPerception         : Soul 是否注意到 (state, in-memory)
- PerceptionDecision      : 為什麼 accept / reject (per-event decision)
- WorldContext            : 被接受的世界資訊渲染成 LLM 可讀字串
- WorldPerceptionTrace    : observability artifact, 不是 runtime state
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.world.perception")

# ───────────────────────────────────────────────────────────
# 1. WorldEvent
# ───────────────────────────────────────────────────────────

# Bry 拍板 2026-08-07 19:40 拍板: valid source 白名單
# (跟 validation.py source whitelist 對齊, 這裡是 dataclass 預設值)
VALID_SOURCES = frozenset({"weather", "news", "calendar", "social", "synthetic"})


@dataclass
class WorldEvent:
    """
    客觀世界事實 (Bry 拍板: WorldEvent 只描述客觀事實, 不含 user relevance)。

    對應 EventType.WORLD_EVENT 的 payload (見 schema.py)。
    注意: WorldEvent 是純資料結構, 不進入 bus 直接用 dataclass 傳遞。
    bus 上的 WorldEvent 是包在 SoulEvent.payload 裡的 dict。

    M3.1 Phase B (Bry 拍板 2026-08-08 02:59): 新增 priority 欄位
    - default = 0 (backward compatible, 既有 M3 caller 不需改)
    - 只用於 source 端 optional 提示 (Phase C/D routing 參考), 不進既有 payload
    - validation: 必須是 int (拒絕 str/float/list)
    - 不發明 priority range constraint (Bry 派工明確禁止)
    """
    source: str                     # "weather" | "news" | "calendar" | "social" | "synthetic"
    type: str                       # 細分類型 e.g. "rain_started", "celebrity_news", "calendar_event"
    novelty_id: str                 # 同一事實識別 (去重 key)
    ts: str                         # ISO 8601 UTC timestamp
    summary: str                    # 一句話客觀描述
    data: Dict[str, Any] = field(default_factory=dict)
    priority: int = 0               # M3.1 Phase B 新增, 預設 0

    def __post_init__(self) -> None:
        """
        M3.1 Phase B minimal validation (Bry 拍板 02:59):
        - priority 必須是 int (拒絕 str / float / list / None)
        - 不動其他既有欄位 validation (validation.py 維持現狀)
        - 不發明 priority range constraint
        """
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            # 拒絕 bool 因為 bool 是 int 的 subclass, 語意上 priority 不該是 bool
            raise TypeError(
                f"WorldEvent.priority 必須是 int, 得到 {type(self.priority).__name__}"
            )

    def to_payload(self) -> Dict[str, Any]:
        """轉成 SoulEvent.payload dict。

        M3.1 Phase B: 既有 M3 bus payload format 100% 保留, 不含 priority 欄位
        (Bry 拍板: 不改既有 bus serialization, 既有 M3 downstream 不感知 priority)。
        """
        return {
            "source": self.source,
            "type": self.type,
            "novelty_id": self.novelty_id,
            "ts": self.ts,
            "summary": self.summary,
            "data": self.data,
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "WorldEvent":
        """從 SoulEvent.payload 還原。"""
        return cls(
            source=payload["source"],
            type=payload["type"],
            novelty_id=payload["novelty_id"],
            ts=payload["ts"],
            summary=payload["summary"],
            data=payload.get("data", {}),
        )


# ───────────────────────────────────────────────────────────
# 2. PerceptionScores (deterministic scoring)
# ───────────────────────────────────────────────────────────

@dataclass
class PerceptionScores:
    """
    4 維評分 (Bry 拍板 Phase 1 範圍, Phase 2 可擴)。

    每個分數 0.0 ~ 1.0, 越高 = 越值得被 Soul 注意。
    final_score = 加權平均 (權重見 SCORE_WEIGHTS)
    """
    relevance: float = 0.0          # 跟 user context / persona / 對話主題的關聯度
    novelty: float = 0.0            # 跟 novelty_id 重複次數成反比
    personal_significance: float = 0.0   # 對 Bry/當下情境的意義 (不從 payload 拿, 從 evaluator 算)
    emotional_significance: float = 0.0  # 跟當下 emotional state 的共鳴度 (e.g. vulnerability window)
    temporal_significance: float = 0.0   # 跟當下時段的契合度 (e.g. calendar 30min 前 = 高)

    def final(self) -> float:
        """加權平均 (跟 SCORE_WEIGHTS 對齊)。"""
        weights = SCORE_WEIGHTS
        total_w = sum(weights.values())
        if total_w == 0:
            return 0.0
        return (
            weights["relevance"] * self.relevance
            + weights["novelty"] * self.novelty
            + weights["personal_significance"] * self.personal_significance
            + weights["emotional_significance"] * self.emotional_significance
            + weights["temporal_significance"] * self.temporal_significance
        ) / total_w


# Bry 拍板: Phase 1 初始權重 (跟 SAGE reader 風格一致, 全部 deterministic, 不調 LLM)
# 理由: 「Bry 對假設中的未來灑過濾網」拒絕 — 只用現有 4 維, 不加第五第六維
SCORE_WEIGHTS: Dict[str, float] = {
    "relevance": 0.30,
    "novelty": 0.20,
    "personal_significance": 0.25,
    "emotional_significance": 0.10,
    "temporal_significance": 0.15,
}


# ───────────────────────────────────────────────────────────
# 3. PerceptionDecision
# ───────────────────────────────────────────────────────────

@dataclass
class PerceptionDecision:
    """
    為什麼這個 event 被 accept / reject (per-event decision)。

    Bry 拍板: 必須能解釋 (Reason 必填), 不能是 if relevant: ... 的黑箱。
    """
    accepted: bool
    reason: str                     # 人類可讀理由 (進 trace)
    scores: PerceptionScores        # 評分快照 (進 trace, 給事後分析)
    event_id: str                   # 對應 WorldEvent.novelty_id (識別用, 不是 uuid)
    perceived_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ───────────────────────────────────────────────────────────
# 4. WorldContext (LLM 注入用)
# ───────────────────────────────────────────────────────────

@dataclass
class WorldContext:
    """
    被 Soul 接受的世界事件, 渲染成 LLM prompt 注入字串。

    規則:
    - accepted_events 最多 PERCEPTION_BUDGET 條 (Bry 拍板 Phase 1 = 3)
    - 每條 WorldEvent 渲染成一行 bullet
    - 加反框架語句 (跟 inner_life / event_block 風格一致)
    - 沒 accept 任何 event → empty, 注入 skip
    """
    accepted_events: List[WorldEvent] = field(default_factory=list)
    decisions: List[PerceptionDecision] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return len(self.accepted_events) == 0

    def to_text(self) -> str:
        """
        渲染成 system prompt 注入字串 (Bry 拍板: 跟 inner_life 同風格)。

        位置 (Bry 拍板 2026-08-07 19:40):
            Inner Life → World Context → Chrono-Social
        (inner_life 之後, current_time/chrono 之前)
        """
        if self.is_empty:
            return ""
        lines = [
            "\n[世界感知] 以下是你剛才注意到的世界事件。",
            "這些是客觀事實, 根據對話上下文自然運用即可, "
            "不要逐條複述或解釋, 也不要過度反應。",
            "\n## 你注意到的世界事件",
        ]
        for ev in self.accepted_events:
            lines.append(f"- [{ev.source}/{ev.type}] {ev.summary}")
        return "\n".join(lines) + "\n"


def format_world_context_block(world_context: WorldContext) -> str:
    """
    Public helper: 給 proxy.py 用的 world_context formatter。

    等價於 world_context.to_text(), 但允許傳 None (向後相容)。
    Bry 拍板: 「不動其他既有邏輯」, 這層 wrapper 讓 proxy.py 改動面積更小。
    """
    if world_context is None or world_context.is_empty:
        return ""
    return world_context.to_text()


# ───────────────────────────────────────────────────────────
# 5. WorldPerceptionTrace (observability artifact)
# ───────────────────────────────────────────────────────────

@dataclass
class WorldPerceptionTrace:
    """
    Sidecar trace record (Bry 拍板: observability artifact, 不是 runtime state)。

    寫進 data/world/perception_trace.jsonl (跟 data/memory/loader_trace.jsonl 對齊)。
    每個 WorldEvent 進 perception layer 都產一條 trace, 不論 accept 還是 reject。

    必填欄位 (Bry 拍板 2026-08-07 20:02 hardening):
    - event_id          (用 novelty_id, 不是 uuid, 方便跟 source 對齊)
    - timestamp         (perceived_at)
    - scores            (PerceptionScores 快照 — 4 維度)
    - accepted          (bool — 是否通過 threshold)
    - reason            (PerceptionDecision.reason — 為什麼 accept/reject)
    - context_injected  (這條 event 最後有沒有進 WorldContext → LLM prompt)
    - memory_written    (有沒有寫進 SAGE / 長期 memory; Phase 1 永遠 False — Perception ≠ Memory)
    - selection_reason  (Bry 拍板 20:02: 為什麼 accepted 卻沒進 top-N? 或為什麼進 top-N?)
                        見 SelectionReason 常數定義。
    """
    event_id: str
    timestamp: str
    source: str
    event_type: str
    scores: PerceptionScores
    accepted: bool
    reason: str
    context_injected: bool
    memory_written: bool
    novelty_id: str
    novelty_count_in_window: int = 0   # 同一 novelty_id 在 NOVELTY_WINDOW 內的次數
    selection_reason: str = ""         # Bry 拍板 20:02: 必填, 給 Bry 10 個問題中的 #8 答案
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        """序列化為 jsonl 一行。"""
        d = asdict(self)
        # asdict 對 nested dataclass 會自動遞迴, 但 scores 是 dataclass, 已經會展開
        return json.dumps(d, ensure_ascii=False, default=str)


# ───────────────────────────────────────────────────────────
# 6. Deterministic scoring (compute_scores)
# ───────────────────────────────────────────────────────────

# 跟 event type 對應的 keyword list (Phase 1 簡化版, 給 relevance 評分用)
# 設計: WorldEvent 客觀描述, evaluator 對照 persona / 對話主題算 relevance
TYPE_KEYWORDS: Dict[str, List[str]] = {
    "rain_started":       ["rain", "雨天", "下雨", "雨"],
    "weather_temp_change": ["temperature", "溫度", "天氣"],
    "celebrity_news":     ["celebrity", "明星", "entertainment", "娛樂"],
    "calendar_event":     ["calendar", "會議", "meeting", "schedule", "行程"],
    "user_going_outside": ["outside", "外出", "出門"],
}

# Bry 拍板: Phase 1 baseline relevance per type
# 設計: 不同 event type 在沒有 user context 的情況下, 對 Soul 的「基礎相關性」不同
# - calendar_event: 30%, 因為時間敏感 (高 temporal)
# - user_going_outside: 30%, 因為跟 Bry 行為直接相關
# - rain_started: 20%, 因為天氣通常只在特定情境重要
# - weather_temp_change: 5%, 溫度變化通常無感 (Bry 拍板 brief: 「minor temperature fluctuation」)
# - celebrity_news: 5%, 跟 Bry 無關的八卦
# 對齊 brief §6 Test B (celebrity) + Test D (temp) 應該被 reject
TYPE_BASELINE_RELEVANCE: Dict[str, float] = {
    "calendar_event": 0.30,
    "user_going_outside": 0.30,
    "rain_started": 0.20,
    "weather_temp_change": 0.05,
    "celebrity_news": 0.05,
}
DEFAULT_TYPE_BASELINE_RELEVANCE: float = 0.10  # 未知 type 用這個


def _count_keyword_overlap(event_summary: str, query_keywords: List[str]) -> float:
    """
    簡單 keyword overlap 算分 (Phase 1 不用 LLM judge)。

    Returns 0.0 ~ 1.0:
    - 0.0: 沒重疊
    - 1.0: 完全重疊
    """
    if not query_keywords:
        return 0.0
    summary_lower = event_summary.lower()
    hits = sum(1 for kw in query_keywords if kw.lower() in summary_lower)
    return hits / len(query_keywords)


def _count_keyword_overlap_set(summary_tokens: set, query_set: set) -> float:
    """
    Token 級 overlap (CJK 2-gram + 完整 token)。

    Returns 0.0 ~ 1.0: hits / len(query_set) (跟 query 大小比, 不是 summary)。
    理由: query 是 user context, 想知道 user context 跟 summary 有多少重疊。
    """
    if not query_set:
        return 0.0
    hits = len(summary_tokens & query_set)
    return hits / len(query_set)


def _extract_cjk_ngrams(text: str) -> set:
    """
    抽 CJK 2-gram 跟 完整 token (>= 2 chars) 當 keyword set。

    給 compute_scores 用, 跟 user context 比較。
    """
    tokens = set()
    if not text:
        return tokens
    # 先拆空白跟標點
    for t in re.split(r"[\s,。、!?！？「」『』()（）\[\]【】:：;；\.]+", text):
        t = t.strip()
        if len(t) >= 2:
            tokens.add(t)
        # CJK 2-gram
        cjk_only = re.sub(r"[^\u4e00-\u9fff]", "", t)
        if len(cjk_only) >= 2:
            for i in range(len(cjk_only) - 1):
                tokens.add(cjk_only[i:i+2])
    return tokens


def compute_scores(
    event: WorldEvent,
    novelty_count: int,
    current_user_context_keywords: Optional[List[str]] = None,
    temporal_salience: str = "low",        # "low" | "medium" | "high" (從 chrono-social 拿)
    anticipatory_flavor: str = "none",     # "none" | "longing" | "worried" | "anxious"
    vulnerability_window: bool = False,
    silence_hours: float = 0.0,
) -> PerceptionScores:
    """
    計算 4 維評分 (deterministic, 不打 LLM)。

    Bry 拍板:
    - personal_significance 不能從 event payload 拿
    - 必須由 evaluator 從「event + 現有 context + chrono-social」算
    - Phase 1 不上 LLM judge

    Args:
        event: 客觀世界事件
        novelty_count: 同一 novelty_id 在 NOVELTY_WINDOW 內已出現次數
        current_user_context_keywords: 從現有 user context (對話 / SAGE 摘要) 抽出的 keywords
                                       Phase 1 簡化: 由 caller 從 AGENT_INTENT 現有 payload 拿
        temporal_salience: 從 chrono-social 拿 (low/medium/high)
        anticipatory_flavor: 從 chrono-social 拿 (none/longing/worried/anxious)
        vulnerability_window: 從 chrono-social 拿 (bool)
        silence_hours: 從 chrono-social 拿
    """
    # ── novelty: 跟 novelty_count 成反比
    # 1 次 = 1.0, 2 次 = 0.5, 3 次 = 0.33, ...
    novelty = 1.0 / max(1, novelty_count)

    # ── relevance: baseline per type + user context match boost
    # Bry 拍板: 「Quality > Quantity」 — 不同 type 基礎 relevance 應該不同
    # 沒 user context 時: 用 type baseline
    # 有 user context 且 summary 跟 user context 重疊: 用 max(baseline, overlap)
    baseline = TYPE_BASELINE_RELEVANCE.get(event.type, DEFAULT_TYPE_BASELINE_RELEVANCE)

    if current_user_context_keywords:
        # 對 event summary 也做 CJK 2-gram 抽取, 跟 user context 同樣比對
        summary_tokens = _extract_cjk_ngrams(event.summary)
        overlap = _count_keyword_overlap_set(summary_tokens, set(current_user_context_keywords))
        # 用戶 context 跟 summary 重疊 ≥ 0.3 就算有相關, 拉高 relevance
        relevance = max(baseline, overlap)
    else:
        relevance = baseline

    # ── personal_significance: 跟 user context 重疊 + 對話最近主題
    # Phase 1 簡化: 跟 user context keyword 重疊 → 高
    #              跟 event type 內建 → 中
    #              其他 → 低
    if current_user_context_keywords:
        sig_overlap = _count_keyword_overlap(event.summary, current_user_context_keywords)
    else:
        sig_overlap = 0.0
    if sig_overlap > 0.5:
        personal_significance = 0.8
    elif sig_overlap > 0.0:
        personal_significance = 0.5
    else:
        personal_significance = 0.2

    # Bry 拍板 2026-08-07 20:02 (P2 Behavior Matrix Scenario C):
    # calendar_event / user_going_outside 本質就是 user 相關, 給 type-based boost
    # (calendar 是 user 自己的行事曆, user_going_outside 是 user 自己的行為)
    # 對齊 brief: 「user 明確有 upcoming calendar event → high personal + temporal significance」
    if event.type in ("calendar_event", "user_going_outside"):
        personal_significance = max(personal_significance, 0.7)

    # ── emotional_significance: 跟 vulnerability_window + anticipatory_flavor 對齊
    # vulnerability_window 開 → 情緒敏感 → 任何 world event 重要性提高
    if vulnerability_window:
        emotional_significance = 0.6
    elif anticipatory_flavor in ("longing", "anxious"):
        emotional_significance = 0.5
    else:
        emotional_significance = 0.2

    # ── temporal_significance: 跟 temporal_salience + 事件本身的時間敏感度
    if temporal_salience == "high":
        base_temp = 0.7
    elif temporal_salience == "medium":
        base_temp = 0.5
    else:
        base_temp = 0.3
    # 特定 type 強化 (calendar event, user_going_outside)
    if event.type in ("calendar_event", "user_going_outside"):
        base_temp = max(base_temp, 0.6)
    temporal_significance = base_temp

    return PerceptionScores(
        relevance=min(1.0, relevance),
        novelty=min(1.0, novelty),
        personal_significance=min(1.0, personal_significance),
        emotional_significance=min(1.0, emotional_significance),
        temporal_significance=min(1.0, temporal_significance),
    )


# ───────────────────────────────────────────────────────────
# 7. Acceptance gate (Phase 1 threshold)
# ───────────────────────────────────────────────────────────

# Bry 拍板 2026-08-07 20:02 hardening: Selection Reason 常數
# 給 trace 10 個問題中的 #8 答案: 「為什麼 accepted 進 top-N? 為什麼 rejected? 為什麼 below budget?」
# 設計: 不要建立新 trace framework, 只用 enum-like 字串常數
SELECTION_REJECTED_AT_VALIDATION = "rejected_at_validation"
SELECTION_REJECTED_AT_THRESHOLD = "rejected_at_threshold"
SELECTION_BELOW_BUDGET = "below_budget_after_ranking"
SELECTION_SELECTED_TOP_N = "selected_top_N_by_score"

# Bry 拍板: Phase 1 initial threshold, 不寫死
# 從 config 讀 (見 WorldPerceptionMiddleware.__init__), 這裡只是 default
DEFAULT_ACCEPT_THRESHOLD = 0.35


def should_accept(
    scores: PerceptionScores,
    threshold: float = DEFAULT_ACCEPT_THRESHOLD,
) -> Tuple[bool, str]:
    """
    根據 final score 決定 accept / reject。

    Returns (accepted, reason)。
    Bry 拍板: Reason 必填, 進 trace 給 Bry 事後看。
    """
    final = scores.final()
    if final < threshold:
        return False, (
            f"final_score={final:.2f} < threshold={threshold:.2f} "
            f"(rel={scores.relevance:.2f} nov={scores.novelty:.2f} "
            f"per={scores.personal_significance:.2f} "
            f"emo={scores.emotional_significance:.2f} "
            f"tmp={scores.temporal_significance:.2f})"
        )
    return True, (
        f"final_score={final:.2f} >= threshold={threshold:.2f} "
        f"(rel={scores.relevance:.2f} nov={scores.novelty:.2f} "
        f"per={scores.personal_significance:.2f} "
        f"emo={scores.emotional_significance:.2f} "
        f"tmp={scores.temporal_significance:.2f})"
    )
