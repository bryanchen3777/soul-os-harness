"""
src/world/middleware.py — Soul OS M3 Phase 1

WorldPerceptionMiddleware (Bry 拍板 2026-08-07 19:40):

Event Bus 整合位置:
    AGENT_INTENT
        ↓ MemoryMiddleware (既有, 不動)
    AGENT_INTENT_ENRICHED (含 memory_context)
        ↓ WorldPerceptionMiddleware (新, M3)
    AGENT_INTENT_PERCEIVED (含 world_context + world_perception_meta)
        ↓ SpeakerTokenManager (改 event_filter, 見 token_manager.py)
    SPEAKER_TOKEN_GRANTED
        ↓ LLMProxy (改 _handle_event_impl, 讀 world_context, 傳給 _build_messages_*)

跟 MemoryMiddleware 鏡像 pattern:
- 訂閱 event → process → re-publish 為新 event type
- 失敗 log warning 不 raise (「拒絕問, 強制讀」)

設計原則:
- 不打 LLM judge, scoring 全 deterministic
- 不動 SAGE, 不動 memory storage
- personal_significance 從現有 intent payload 抽, 不從 world event payload 拿
  (Bry 拍板: 「世界不能自己告訴 Soul 我對你很重要, Soul 必須自己判斷」)
- WorldPerceptionTrace 必寫, 不論 accept/reject
- Invalid event → reject → trace → no context → no memory
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import (
    EventPriority,
    EventType,
    SoulEvent,
)

from .perception import (
    DEFAULT_ACCEPT_THRESHOLD,
    PerceptionDecision,
    PerceptionScores,
    WorldContext,
    WorldEvent,
    WorldPerceptionTrace,
    SELECTION_BELOW_BUDGET,
    SELECTION_REJECTED_AT_THRESHOLD,
    SELECTION_REJECTED_AT_VALIDATION,
    SELECTION_SELECTED_TOP_N,
    compute_scores,
    format_world_context_block,
    should_accept,
)
from .state import WorldPerceptionState
from .trace import WorldPerceptionTraceWriter
from .validation import WorldEventValidationError, validate_world_event

# SI-2.1 (Social Diffusion Contract, 2026-09-03): 防線 1 Ambient Perception Path —
# SOCIAL_WORLD_EVENT 平行訂閱 (additive, 既有 WORLD_EVENT / AGENT_INTENT_ENRICHED
# 訂閱與處理路徑 0 變更)。SocialWorldEvent 繼承 WorldEvent, 可進同一個
# WorldPerceptionState (ephemeral 容器, 0 改動) 與 compute_scores 評分管道。
#
# 注意: 不在模組頂層 import src.social — src/social/schema.py 依賴
# src.world.perception (WorldEvent), 而 src/world/__init__.py 會 import 本模組,
# 頂層 import 會造成 circular import。改為方法內局部 import (Python import
# 有快取, 開銷可忽略)。
if TYPE_CHECKING:  # 僅類型標註, 運行時不執行
    from src.social.aggregator import SocialPerceptionAggregator
    from src.social.schema import SocialWorldEvent

logger = logging.getLogger("soul_os.world.middleware")


# Bry 拍板 2026-08-07 19:40: Perception Budget 做成 config, Phase 1 initial = 3
# 拒絕「一個時間點只有一個世界事件」假設
DEFAULT_PERCEPTION_BUDGET = 3

# SI-2.1 (2026-09-03): 社交感知獨立 budget (低刺激度背景氛圍, 比世界感知更收斂)。
# 平行於 DEFAULT_PERCEPTION_BUDGET, 不佔用世界事件的名額。
DEFAULT_SOCIAL_PERCEPTION_BUDGET = 2


def _extract_user_context_keywords(agent_intent_payload: Dict[str, Any]) -> List[str]:
    """
    從現有 AGENT_INTENT_ENRICHED payload 抽 user context keywords。

    來源 (Phase 1 範圍):
    - draft (使用者訊息 / proactive trigger 文字)
    - text (向後相容)

    不從 WorldEvent payload 抽 (Bry 拍板: 「世界不能自己告訴 Soul 我對你很重要」)。
    不加 conversation parser (Bry 拍板)。
    """
    keywords: List[str] = []
    draft = agent_intent_payload.get("draft", "")
    text = agent_intent_payload.get("text", "")
    raw = f"{draft} {text}".strip()
    if not raw:
        return []
    # 簡單 tokenize: 拆空白 + 標點, 取長度 >= 2 的詞, lower
    # Phase 1 簡化, Phase 2 可用 jieba / 日文 token 強化
    # 注意: 保留中文字符 (Chinese chars 本身不拆, e.g. "外面是不是還在下雨" 是一個 token)
    # Bry 拍板: 「不加 conversation parser」, 不上 jieba
    # 但對於簡單中文, 拆成短 char (e.g. "外面", "下雨") 比較好比較 → 額外加 char n-gram
    tokens = re.split(r"[\s,。、!?！？「」『』()（）\[\]【】:：;；\.]+", raw)
    for t in tokens:
        t = t.strip().lower()
        if not t:
            continue
        # 完整 token (>= 2 字)
        if len(t) >= 2:
            keywords.append(t)
        # 中文字符 2-gram 強化 (e.g. "外面開始下雨了" → "外面", "面開", "開始", "始下", "下雨", "雨了")
        # 目的: 讓 user context 跟 summary 的子字串有 overlap
        cjk_only = re.sub(r"[^\u4e00-\u9fff]", "", t)
        if len(cjk_only) >= 2:
            for i in range(len(cjk_only) - 1):
                gram = cjk_only[i:i+2]
                if gram not in keywords:
                    keywords.append(gram)
    return keywords


def _infer_temporal_salience(agent_intent_payload: Dict[str, Any]) -> str:
    """
    從現有 chrono_context (string) 推 temporal_salience。

    來源: payload["chrono_context"] 是已渲染的字串 (v2.2 format)。
    Phase 1 簡化: 用關鍵字偵測 (vulnerability_window=True, salience=high, etc.)
    """
    chrono_text = agent_intent_payload.get("chrono_context", "")
    if not isinstance(chrono_text, str):
        return "low"
    chrono_lower = chrono_text.lower()
    if "salience=high" in chrono_lower or "vulnerability_window=true" in chrono_lower:
        return "high"
    if "salience=medium" in chrono_lower:
        return "medium"
    return "low"


def _infer_anticipatory_flavor(agent_intent_payload: Dict[str, Any]) -> str:
    """從 chrono_context 字串推 anticipatory_flavor。"""
    chrono_text = agent_intent_payload.get("chrono_context", "")
    if not isinstance(chrono_text, str):
        return "none"
    chrono_lower = chrono_text.lower()
    for flavor in ("longing", "worried", "anxious"):
        if flavor in chrono_lower:
            return flavor
    return "none"


def _infer_vulnerability_window(agent_intent_payload: Dict[str, Any]) -> bool:
    """從 chrono_context 字串推 vulnerability_window。"""
    chrono_text = agent_intent_payload.get("chrono_context", "")
    if not isinstance(chrono_text, str):
        return False
    return "vulnerability_window=true" in str(chrono_text).lower()


def _infer_silence_hours(agent_intent_payload: Dict[str, Any]) -> float:
    """從 chrono_context 字串抽 silence_hours (regex 抓 silence=12.3h)。"""
    chrono_text = agent_intent_payload.get("chrono_context", "")
    if not isinstance(chrono_text, str):
        return 0.0
    m = re.search(r"silence=([\d.]+)h", chrono_text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 0.0
    return 0.0


def _ts_to_epoch(ts: str) -> float:
    """
    SI-3 Phase 2 (2026-09-03): ISO 8601 UTC timestamp → epoch 秒。

    給 SocialPerceptionAggregator 的 now 用 (TTL 從事件時間起算, 測試可控)。
    解析失敗 → 回傳當前時間 (fail-safe, 不阻斷感知管線)。
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).timestamp()


class WorldPerceptionMiddleware:
    """
    World Perception Layer 的 Bus 整合。

    職責:
    1. 訂閱 WORLD_EVENT → 進 WorldPerceptionState + 寫 trace
    2. 訂閱 AGENT_INTENT_ENRICHED → 算 top-N world_context + re-publish 為 AGENT_INTENT_PERCEIVED
    3. 維持 WorldPerceptionState (in-memory, ephemeral)
    4. 維持 WorldPerceptionTraceWriter (sidecar jsonl)

    公開 API:
    - register() / unregister()  — 跟 MemoryMiddleware 對齊
    - process_world_event_direct(event) — 給 SyntheticSource / 測試用, 不透過 bus
    - build_world_context_for_agent(agent_id, ...) — 給外部 caller / test 用
    - state_snapshot() — 給 observability 用
    """

    def __init__(
        self,
        bus: SoulEventBus,
        state: Optional[WorldPerceptionState] = None,
        trace_writer: Optional[WorldPerceptionTraceWriter] = None,
        novelty_window: timedelta = timedelta(hours=24),
        accept_threshold: float = DEFAULT_ACCEPT_THRESHOLD,
        perception_budget: int = DEFAULT_PERCEPTION_BUDGET,
        social_perception_budget: int = DEFAULT_SOCIAL_PERCEPTION_BUDGET,
    ):
        """
        Args:
            bus: Soul Event Bus
            state: 可注入 WorldPerceptionState (測試用); None = 自己 new
            trace_writer: 可注入 trace writer (測試用); None = 自己 new
            novelty_window: Bry 拍板做成 config, 預設 24h
            accept_threshold: accept gate, 預設 0.35
            perception_budget: top-N 數量, 預設 3
            social_perception_budget: SI-2.1 — 社交感知 top-N 數量, 預設 2
                (低刺激度背景氛圍, 平行於世界感知 budget)
        """
        self.bus = bus
        self.state = state or WorldPerceptionState(novelty_window=novelty_window)
        self.trace_writer = trace_writer or WorldPerceptionTraceWriter()
        self.accept_threshold = accept_threshold
        self.perception_budget = perception_budget
        self.social_perception_budget = social_perception_budget

        # observability counters
        self._events_received = 0
        self._events_validation_rejected = 0
        self._events_state_added = 0
        self._agent_intents_processed = 0
        self._contexts_injected = 0

        # SI-3 Phase 2 (2026-09-03): per-agent SocialPerceptionAggregator 緩存
        # (純記憶體, 0 檔案 IO; 每個 agent 一個聚合器, 保持身份隔離)
        self._social_aggregators: Dict[str, SocialPerceptionAggregator] = {}

    # ── Bus integration ──────────────────────────────────

    def register(self) -> None:
        """
        向 Event Bus 註冊, 開始接收:
        - WORLD_EVENT (新事件, source 發布)
        - AGENT_INTENT_ENRICHED (MemoryMiddleware 已 enrich, WorldPerception 加 world_context)
        - SOCIAL_WORLD_EVENT (SI-2.1 新增: 平行訂閱, 防線 1 Ambient Path)
        """
        self.bus.subscribe(
            subscriber_id="world_perception",
            handler=self.handle_event,
            event_filter={
                EventType.WORLD_EVENT,
                EventType.AGENT_INTENT_ENRICHED,
                EventType.SOCIAL_WORLD_EVENT,  # SI-2.1: 平行訂閱 (additive)
            },
        )
        logger.info(
            f"[WorldPerception] 已掛載 ✓ "
            f"perception_budget={self.perception_budget} "
            f"social_perception_budget={self.social_perception_budget} "
            f"accept_threshold={self.accept_threshold} "
            f"novelty_window={self.state.novelty_window}"
        )

    def unregister(self) -> None:
        self.bus.unsubscribe("world_perception")

    async def handle_event(self, event: SoulEvent) -> None:
        """Bus handler 分派。"""
        if event.event_type == EventType.WORLD_EVENT:
            await self._on_world_event(event)
        elif event.event_type == EventType.AGENT_INTENT_ENRICHED:
            await self._on_agent_intent_enriched(event)
        elif event.event_type == EventType.SOCIAL_WORLD_EVENT:
            # SI-2.1: 平行分派分支 (additive) — 防線 1 Ambient Path
            await self._on_social_world_event(event)

    async def _on_world_event(self, event: SoulEvent) -> None:
        """
        收到 WORLD_EVENT: validation → state → trace。
        invalid → reject → trace → 不進 state, 不進 context。
        """
        self._events_received += 1
        try:
            world_event = validate_world_event(event.payload)
        except WorldEventValidationError as e:
            self._events_validation_rejected += 1
            self.state.record_validation_reject()
            # trace 必寫 (不論 accept/reject)
            # Bry 拍板 2026-08-07 20:02: selection_reason 必填
            self.trace_writer.write(WorldPerceptionTrace(
                event_id=event.event_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=event.payload.get("source", "unknown"),
                event_type=event.payload.get("type", "unknown"),
                novelty_id=event.payload.get("novelty_id", "unknown"),
                scores=PerceptionScores(),  # 全部 0 (沒 scoring)
                accepted=False,
                reason=f"validation_reject: {e}",
                context_injected=False,
                memory_written=False,
                novelty_count_in_window=0,
                selection_reason=SELECTION_REJECTED_AT_VALIDATION,  # Bry 拍板 20:02
                extra={"phase": "validation"},
            ))
            logger.warning(f"[WorldPerception] validation reject: {e}")
            return

        # valid → 加到 state
        novelty_count = self.state.add(world_event)
        self._events_state_added += 1

        # trace (尚未 evaluate, 留個紀錄)
        # 注意: 真正 accept/reject 在 _on_agent_intent_enriched 算
        # 這裡只記「收到 valid event」
        # selection_reason 留空 (等 _on_agent_intent_enriched 算完再寫)
        self.trace_writer.write(WorldPerceptionTrace(
            event_id=event.event_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=world_event.source,
            event_type=world_event.type,
            novelty_id=world_event.novelty_id,
            scores=PerceptionScores(),  # 此時還沒算
            accepted=False,  # placeholder, 真正的 decision 在 AGENT_INTENT 處理時
            reason="received_valid_event_pending_evaluation",
            context_injected=False,
            memory_written=False,
            novelty_count_in_window=novelty_count,
            selection_reason="",  # 待 evaluate 後填
            extra={"phase": "received"},
        ))
        logger.info(
            f"[WorldPerception] 收到 valid event | "
            f"source={world_event.source} type={world_event.type} "
            f"novelty_id={world_event.novelty_id} "
            f"count_in_window={novelty_count}"
        )

    async def _on_social_world_event(self, event: SoulEvent) -> None:
        """
        SI-2.1 (2026-09-03): 收到 SOCIAL_WORLD_EVENT — 防線 1 Ambient Path。

        復用 `_on_world_event` 同款管道 (validate → state → trace), 但驗證器
        換成 SocialWorldEvent 驗證器 (src/social/validation.py, 薄驗證, fail-closed)。

        額外契約檢查 (SI-2.1 §3.4): visibility=private 出現在 bus 上 = 契約違例
        (防線 2 已把 private 攔截在廣播總線之外) → fail-closed 丟棄, 不進 state。

        不觸發 transmit / AGENT_INTENT / AGENCY_TRIGGER — 只進 world_context
        (Ambient Perception, 低刺激度背景氛圍)。
        """
        # 局部 import (避免 circular: src/social/schema.py 依賴 src.world.perception)
        from src.social.schema import VISIBILITY_PRIVATE
        from src.social.validation import (
            SocialWorldEventValidationError,
            validate_social_world_event,
        )

        self._events_received += 1
        try:
            social_event = validate_social_world_event(event.payload)
        except SocialWorldEventValidationError as e:
            self._events_validation_rejected += 1
            self.state.record_validation_reject()
            self.trace_writer.write(WorldPerceptionTrace(
                event_id=event.event_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=event.payload.get("actor_id", "unknown"),
                event_type=event.payload.get("event_type", "unknown"),
                novelty_id=event.payload.get("novelty_id", "unknown"),
                scores=PerceptionScores(),  # 全部 0 (沒 scoring)
                accepted=False,
                reason=f"validation_reject: {e}",
                context_injected=False,
                memory_written=False,
                novelty_count_in_window=0,
                selection_reason=SELECTION_REJECTED_AT_VALIDATION,
                extra={"phase": "validation", "event_kind": "social"},
            ))
            logger.warning(f"[WorldPerception] social validation reject: {e}")
            return

        # SI-2.1 §3.4: private 出現在 bus 上 = 契約違例 → fail-closed 丟棄
        if social_event.visibility == VISIBILITY_PRIVATE:
            self._events_validation_rejected += 1
            self.state.record_validation_reject()
            self.trace_writer.write(WorldPerceptionTrace(
                event_id=event.event_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=social_event.actor_id,
                event_type=social_event.event_type,
                novelty_id=social_event.novelty_id,
                scores=PerceptionScores(),
                accepted=False,
                reason=(
                    "private_on_bus_contract_violation: visibility=private 出現在 "
                    "bus 上 (防線 2 應已攔截) — fail-closed 丟棄"
                ),
                context_injected=False,
                memory_written=False,
                novelty_count_in_window=0,
                selection_reason=SELECTION_REJECTED_AT_VALIDATION,
                extra={
                    "phase": "validation",
                    "event_kind": "social",
                    "actor_id": social_event.actor_id,
                    "space_id": social_event.space_id,
                },
            ))
            logger.warning(
                f"[WorldPerception] social private_on_bus 契約違例, 丟棄: "
                f"actor_id={social_event.actor_id} novelty_id={social_event.novelty_id}"
            )
            return

        # valid → 加到 state (同一個 WorldPerceptionState, ephemeral, 24h novelty window)
        novelty_count = self.state.add(social_event)
        self._events_state_added += 1

        self.trace_writer.write(WorldPerceptionTrace(
            event_id=event.event_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=social_event.source,
            event_type=social_event.event_type,
            novelty_id=social_event.novelty_id,
            scores=PerceptionScores(),  # 此時還沒算
            accepted=False,  # placeholder, 真正的 decision 在 AGENT_INTENT 處理時
            reason="received_valid_social_event_pending_evaluation",
            context_injected=False,
            memory_written=False,
            novelty_count_in_window=novelty_count,
            selection_reason="",  # 待 evaluate 後填
            extra={
                "phase": "received",
                "event_kind": "social",
                "actor_id": social_event.actor_id,
                "space_id": social_event.space_id,
                "visibility": social_event.visibility,
            },
        ))
        logger.info(
            f"[WorldPerception] 收到 valid social event | "
            f"actor_id={social_event.actor_id} space={social_event.space_id} "
            f"event_type={social_event.event_type} "
            f"novelty_id={social_event.novelty_id} "
            f"count_in_window={novelty_count}"
        )

    async def _on_agent_intent_enriched(self, event: SoulEvent) -> None:
        """
        收到 AGENT_INTENT_ENRICHED (MemoryMiddleware 已 enrich):

        Bry 拍板 2026-08-07 20:02 hardening: 三段式 (Pass 1/2/3) 確保
        selection_reason 跟 context_injected 都能 trace。

        Pass 1: 算所有 active events 的 score + accept/reject decision
        Pass 2: 從 accepted 裡排 rank, 挑 top-N
        Pass 3: 寫 trace (帶 selection_reason)
        然後 re-publish 為 AGENT_INTENT_PERCEIVED
        """
        self._agent_intents_processed += 1
        agent_id = event.payload.get("agent_id", "unknown")

        # 拿現有 user context (從 AGENT_INTENT_ENRICHED payload, 不從 WorldEvent payload 拿)
        user_keywords = _extract_user_context_keywords(event.payload)
        temporal_salience = _infer_temporal_salience(event.payload)
        anticipatory_flavor = _infer_anticipatory_flavor(event.payload)
        vulnerability_window = _infer_vulnerability_window(event.payload)

        # 拿目前 active events
        active_events = self.state.get_active_events()
        if not active_events:
            # 沒 world events, 直接 publish empty world_context
            await self._publish_perceived(event, world_context=WorldContext(), agent_id=agent_id)
            return

        # SI-2.1 (additive): 分流 social events — 平行感知路徑, 不混入 [世界感知]。
        # SocialWorldEvent 繼承 WorldEvent, 會跟 world events 一起在 state 裡;
        # 這裡把兩者分開: world events 走既有邏輯 (0 變更), social events 走
        # 獨立 [社交感知] 渲染 (防線 1 Ambient, 帶「他者行為、非我經歷」反框架語)。
        from src.social.schema import SocialWorldEvent  # 局部 import (避免 circular)
        world_events = [ev for ev in active_events if not isinstance(ev, SocialWorldEvent)]
        social_events = [ev for ev in active_events if isinstance(ev, SocialWorldEvent)]

        # ── Pass 1: 算所有 scores + accept/reject decisions ──
        # Bry 拍板: novelty_count 按 perceived_at 順序算 position
        novelty_position: Dict[str, int] = {}
        scored: List[Tuple[WorldEvent, PerceptionScores, PerceptionDecision, int]] = []

        for world_event in world_events:
            nid = world_event.novelty_id
            novelty_position[nid] = novelty_position.get(nid, 0) + 1
            novelty_count = novelty_position[nid]

            scores = compute_scores(
                event=world_event,
                novelty_count=novelty_count,
                current_user_context_keywords=user_keywords,
                temporal_salience=temporal_salience,
                anticipatory_flavor=anticipatory_flavor,
                vulnerability_window=vulnerability_window,
                silence_hours=0.0,
                # M3.2-A (Bry 拍板 2026-08-08 11:36): priority 進場, 內部 read world_event.priority
                # 既有 5 維度 scoring 邏輯 0 改, additive priority_boost 進 final_score
                event_priority=world_event.priority,
            )
            accepted, reason = should_accept(scores, threshold=self.accept_threshold)
            decision = PerceptionDecision(
                accepted=accepted,
                reason=reason,
                scores=scores,
                event_id=world_event.novelty_id,
            )
            scored.append((world_event, scores, decision, novelty_count))

        # ── Pass 2: 從 accepted 排 rank, 取 top-N ──
        accepted_scored = [s for s in scored if s[2].accepted]
        accepted_scored.sort(key=lambda t: t[1].final(), reverse=True)
        top_n_ids = set(id(ev) for ev, _, _, _ in accepted_scored[:self.perception_budget])

        # ── Pass 3: 寫 trace 帶 selection_reason (Bry 拍板 20:02) ──
        for world_event, scores, decision, novelty_count in scored:
            is_top_n = id(world_event) in top_n_ids
            if not decision.accepted:
                selection_reason = SELECTION_REJECTED_AT_THRESHOLD
                context_injected = False
            elif is_top_n:
                selection_reason = f"{SELECTION_SELECTED_TOP_N} (budget={self.perception_budget})"
                context_injected = True
            else:
                # accepted 但不在 top-N
                selection_reason = SELECTION_BELOW_BUDGET
                context_injected = False

            self.trace_writer.write(WorldPerceptionTrace(
                event_id=world_event.novelty_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=world_event.source,
                event_type=world_event.type,
                novelty_id=world_event.novelty_id,
                scores=scores,
                accepted=decision.accepted,
                reason=decision.reason,
                context_injected=context_injected,
                memory_written=False,  # Phase 1 永遠 False (Bry 拍板: Perception ≠ Memory)
                novelty_count_in_window=novelty_count,
                selection_reason=selection_reason,  # Bry 拍板 20:02: 必填
                extra={
                    "phase": "evaluated",
                    "agent_id": agent_id,
                    "temporal_salience": temporal_salience,
                    "anticipatory_flavor": anticipatory_flavor,
                    "vulnerability_window": vulnerability_window,
                    "user_keyword_count": len(user_keywords),
                    # M3.2-A (Bry 拍板 2026-08-08 11:36): priority 進 trace observability
                    "world_event_priority": world_event.priority,
                },
            ))

        # ── 組 WorldContext ──
        top_n_events = [ev for ev, _, _, _ in accepted_scored[:self.perception_budget]]
        world_context = WorldContext(
            accepted_events=top_n_events,
            decisions=[d for _, _, d, _ in scored],
        )

        if not world_context.is_empty:
            self._contexts_injected += 1

        # SI-2.1 (additive): social events 獨立渲染 [社交感知] 區塊 (防線 1 Ambient)
        social_block = self._render_social_context(
            social_events,
            user_keywords=user_keywords,
            temporal_salience=temporal_salience,
            anticipatory_flavor=anticipatory_flavor,
            vulnerability_window=vulnerability_window,
            agent_id=agent_id,
        )

        logger.info(
            f"[WorldPerception] agent={agent_id} "
            f"active={len(active_events)} "
            f"accepted={sum(1 for d in world_context.decisions if d.accepted)} "
            f"top_n={len(top_n_events)} "
            f"perception_budget={self.perception_budget} "
            f"social_active={len(social_events)}"
        )

        await self._publish_perceived(
            event,
            world_context=world_context,
            agent_id=agent_id,
            social_block=social_block,
        )

    async def _publish_perceived(
        self,
        enriched_event: SoulEvent,
        world_context: WorldContext,
        agent_id: str,
        social_block: str = "",
    ) -> None:
        """
        Re-publish 為 AGENT_INTENT_PERCEIVED。
        payload 帶: 原 enriched payload + world_context (text) + world_perception_meta。

        SI-2.1 (additive): social_block 參數 (預設 "") — [社交感知] 區塊文字,
        追加在 [世界感知] 區塊之後 (防線 1 Ambient Perception)。
        """
        world_context_text = format_world_context_block(world_context)
        if social_block:
            world_context_text = (
                world_context_text + social_block
                if world_context_text
                else social_block
            )

        # 收集 top-N event ids + accept/reject counts (observability)
        meta = {
            "accepted_count": sum(1 for d in world_context.decisions if d.accepted),
            "rejected_count": sum(1 for d in world_context.decisions if not d.accepted),
            "top_event_ids": [ev.novelty_id for ev in world_context.accepted_events],
            "perception_budget": self.perception_budget,
            "state_snapshot": self.state.snapshot(),
        }

        new_payload = {
            **enriched_event.payload,
            "world_context": world_context_text,
            "world_perception_meta": meta,
            "agent_id": agent_id,
        }

        perceived = SoulEvent(
            event_type=EventType.AGENT_INTENT_PERCEIVED,
            source=enriched_event.source,
            target=enriched_event.target,
            priority=enriched_event.priority,
            payload=new_payload,
            session_id=enriched_event.session_id,
            correlation_id=enriched_event.correlation_id,
        )
        await self.bus.publish(perceived)

    # ── SI-2.1: 社交感知渲染 (防線 1 Ambient Path) ─────────

    def _get_social_aggregator(self, agent_id: str) -> SocialPerceptionAggregator:
        """
        SI-3 Phase 2 (2026-09-03): 取 (或 lazy 建立) 該 agent 的 SocialPerceptionAggregator。

        每個 agent 一個聚合器 (身份隔離: 外部 actor 事件只作背景感知, 0 檔案 IO)。
        """
        # 局部 import (避免 circular: src/social/schema.py 依賴 src.world.perception)
        from src.social.aggregator import SocialPerceptionAggregator

        if agent_id not in self._social_aggregators:
            self._social_aggregators[agent_id] = SocialPerceptionAggregator(
                current_agent_id=agent_id
            )
        return self._social_aggregators[agent_id]

    def _render_social_context(
        self,
        social_events: List[SocialWorldEvent],
        *,
        user_keywords: List[str],
        temporal_salience: str,
        anticipatory_flavor: str,
        vulnerability_window: bool,
        agent_id: str,
    ) -> str:
        """
        SI-3 Phase 2 (2026-09-03): 把 active social events 渲染成緊湊社交感知區塊。

        升級: 用 SocialPerceptionAggregator (SI-3 Phase 1) 取代 raw event feed 渲染 —
        聚合為 CompactSocialState (在場/話題/氛圍/有效機會), 固定 Token 預算 (<=150),
        帶反框架警示語 (ANTI_FRAMING_HINT)。無在場他人且無活躍話題 → 回傳 "" (留白)。

        保留既有 trace 記錄 (WorldPerceptionTrace, observability): 每個 social event
        仍走 compute_scores + should_accept 判定並寫 trace (行為 0 變更), 但渲染
        改由 aggregator 的 compact block 輸出。

        不觸發 transmit / AGENT_INTENT / AGENCY_TRIGGER — 只進 world_context。
        """
        if not social_events:
            return ""

        # SI-3 Phase 2: 依序吸收事件到 per-agent aggregator。
        # now = 事件 ts 的 epoch 秒 (TTL 從事件時間起算, 測試可控)。
        aggregator = self._get_social_aggregator(agent_id)
        now_epoch = 0.0
        for ev in social_events:
            now_epoch = _ts_to_epoch(ev.ts)
            aggregator.update_from_event(ev, now_epoch)

        state = aggregator.get_compact_state(agent_id, now_epoch)
        block = aggregator.render_compact_prompt_block(agent_id, state)

        # 保留既有 trace (observability, 行為 0 變更)
        # 簡單 scoring (novelty_count 按 perceived_at 順序算 position)
        novelty_position: Dict[str, int] = {}
        scored: List[tuple] = []
        for ev in social_events:
            nid = ev.novelty_id
            novelty_position[nid] = novelty_position.get(nid, 0) + 1
            scores = compute_scores(
                event=ev,
                novelty_count=novelty_position[nid],
                current_user_context_keywords=user_keywords,
                temporal_salience=temporal_salience,
                anticipatory_flavor=anticipatory_flavor,
                vulnerability_window=vulnerability_window,
                silence_hours=0.0,
                event_priority=ev.priority,
            )
            accepted, reason = should_accept(scores, threshold=self.accept_threshold)
            scored.append((ev, scores, accepted, reason, novelty_position[nid]))

        # Ambient 選擇: 按 final score 排名取 top-N (不過 threshold)
        ranked = sorted(scored, key=lambda t: t[1].final(), reverse=True)
        top_n = ranked[: self.social_perception_budget]
        top_n_ids = {id(t[0]) for t in top_n}

        # trace (social evaluate — 必寫, 不論 accept/reject)
        for ev, scores, accepted, reason, novelty_count in scored:
            is_top_n = id(ev) in top_n_ids
            self.trace_writer.write(WorldPerceptionTrace(
                event_id=ev.novelty_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source=ev.source,
                event_type=ev.event_type,
                novelty_id=ev.novelty_id,
                scores=scores,
                accepted=accepted,
                reason=reason,
                context_injected=is_top_n,
                memory_written=False,  # 防線 3: 他者事件永不寫 memory
                novelty_count_in_window=novelty_count,
                selection_reason=(
                    f"{SELECTION_SELECTED_TOP_N} (social_budget={self.social_perception_budget})"
                    if is_top_n
                    else SELECTION_BELOW_BUDGET
                ),
                extra={
                    "phase": "social_evaluated",
                    "agent_id": agent_id,
                    "actor_id": ev.actor_id,
                    "space_id": ev.space_id,
                    "visibility": ev.visibility,
                },
            ))

        return block

    # ── External API (給 test / observability) ───────────

    async def process_world_event_direct(self, event: WorldEvent) -> None:
        """
        直接喂 WorldEvent (不透過 bus), 給 SyntheticSource / 測試用。
        """
        # 包成 SoulEvent
        soul_event = SoulEvent(
            event_type=EventType.WORLD_EVENT,
            source="synthetic",
            target="broadcast",
            priority=EventPriority.LOW,
            payload=event.to_payload(),
        )
        await self._on_world_event(soul_event)

    async def inject(self, event: WorldEvent) -> None:
        """
        M5.4-3.1 contract repair (Bry 派工 2026-08-09 17:43):
        WorldEventInjector Protocol conform — middleware 可被
        WorldEventDispatcher 當作 injector 使用。

        Design (per 派工 4 條約束):
          1. 對齊 WorldEventInjector.inject(event) 介面
          2. 與 EventBus → Middleware 架構一致 (內部走 _on_world_event 同一個 handler)
          3. 不建立第二條 processing path (委派給 process_world_event_direct)
          4. exactly-once: 每次 inject 只跑一次 _on_world_event,
             沒有 duplicate 處理, caller 自己負責 idempotency

        對齊說明: 不直接走 bus.publish 是因為 middleware 自己就是 bus subscriber,
        直接 publish 會 race (自己訂閱自己, 可能重複處理); 委派給
        process_world_event_direct → _on_world_event 確保 single processing path。
        """
        await self.process_world_event_direct(event)

    async def inject_synthetic_events_for_smoke_test(
        self,
        events: List[WorldEvent],
    ) -> int:
        """
        P8 hardening: 注入多個 synthetic events, 給 production runtime smoke test 用。

        呼叫條件:
        - 僅由 run_server.py 在 SOULOS_WORLD_PERCEPTION_TEST_SOURCE=1 時呼叫
        - Production default 永遠不呼叫 (production 不會主動產生 synthetic event)

        Returns: 注入的 event 數量。
        """
        for ev in events:
            await self.process_world_event_direct(ev)
        logger.info(
            f"[WorldPerception] inject_synthetic_events_for_smoke_test "
            f"injected {len(events)} events"
        )
        return len(events)

    def state_snapshot(self) -> dict:
        """給 observability 用, 不是對外 query interface。"""
        base = self.state.snapshot()
        base.update({
            "events_received": self._events_received,
            "events_validation_rejected": self._events_validation_rejected,
            "events_state_added": self._events_state_added,
            "agent_intents_processed": self._agent_intents_processed,
            "contexts_injected": self._contexts_injected,
            "accept_threshold": self.accept_threshold,
            "perception_budget": self.perception_budget,
        })
        return base
