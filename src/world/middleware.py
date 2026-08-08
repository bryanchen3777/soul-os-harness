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
from typing import Any, Dict, List, Optional

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

logger = logging.getLogger("soul_os.world.middleware")


# Bry 拍板 2026-08-07 19:40: Perception Budget 做成 config, Phase 1 initial = 3
# 拒絕「一個時間點只有一個世界事件」假設
DEFAULT_PERCEPTION_BUDGET = 3


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
    ):
        """
        Args:
            bus: Soul Event Bus
            state: 可注入 WorldPerceptionState (測試用); None = 自己 new
            trace_writer: 可注入 trace writer (測試用); None = 自己 new
            novelty_window: Bry 拍板做成 config, 預設 24h
            accept_threshold: accept gate, 預設 0.35
            perception_budget: top-N 數量, 預設 3
        """
        self.bus = bus
        self.state = state or WorldPerceptionState(novelty_window=novelty_window)
        self.trace_writer = trace_writer or WorldPerceptionTraceWriter()
        self.accept_threshold = accept_threshold
        self.perception_budget = perception_budget

        # observability counters
        self._events_received = 0
        self._events_validation_rejected = 0
        self._events_state_added = 0
        self._agent_intents_processed = 0
        self._contexts_injected = 0

    # ── Bus integration ──────────────────────────────────

    def register(self) -> None:
        """
        向 Event Bus 註冊, 開始接收:
        - WORLD_EVENT (新事件, source 發布)
        - AGENT_INTENT_ENRICHED (MemoryMiddleware 已 enrich, WorldPerception 加 world_context)
        """
        self.bus.subscribe(
            subscriber_id="world_perception",
            handler=self.handle_event,
            event_filter={
                EventType.WORLD_EVENT,
                EventType.AGENT_INTENT_ENRICHED,
            },
        )
        logger.info(
            f"[WorldPerception] 已掛載 ✓ "
            f"perception_budget={self.perception_budget} "
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

        # ── Pass 1: 算所有 scores + accept/reject decisions ──
        # Bry 拍板: novelty_count 按 perceived_at 順序算 position
        novelty_position: Dict[str, int] = {}
        scored: List[Tuple[WorldEvent, PerceptionScores, PerceptionDecision, int]] = []

        for world_event in active_events:
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

        logger.info(
            f"[WorldPerception] agent={agent_id} "
            f"active={len(active_events)} "
            f"accepted={sum(1 for d in world_context.decisions if d.accepted)} "
            f"top_n={len(top_n_events)} "
            f"perception_budget={self.perception_budget}"
        )

        await self._publish_perceived(event, world_context=world_context, agent_id=agent_id)

    async def _publish_perceived(
        self,
        enriched_event: SoulEvent,
        world_context: WorldContext,
        agent_id: str,
    ) -> None:
        """
        Re-publish 為 AGENT_INTENT_PERCEIVED。
        payload 帶: 原 enriched payload + world_context (text) + world_perception_meta。
        """
        world_context_text = format_world_context_block(world_context)

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
