"""
src/agency/dream_handler.py — Soul OS M5.2-H Phase 2 DreamHandler

Bry 派工 2026-08-08 M5.2-H Phase 2:
  - Migrate `dream` scheduler trigger 從 AGENCY_BYPASS → Agency 4 stages
  - WRITER_ONLY (per C3 精神): DreamHandler 不呼叫 agent._fire_intent / AGENT_SPEAK
    只呼叫 writer.write_dream(dreamer, target, all_agents)
  - writer.write_dream 內部包含 relationship side effect (impression 抽取 + on_dream touch)
    必須完整保留,handler 不能再額外做 relationship 操作
  - 跟 AgencyTriggerHandler / EventHandler 平行:
    AgencyTriggerHandler 處理 proactive_dm
    EventHandler 處理 event
    DreamHandler 處理 dream (過濾 trigger_type)

Architecture:
  Scheduler._fire_dream
    ↓ publish AGENCY_TRIGGER (trigger_type="dream", extra={target_agent_id, all_agents})
  DreamHandler.handle_event
    ↓ [validate envelope, filter trigger_type=="dream", 必填 target_agent_id]
  run_agency(perception=None, trigger=envelope)
    ↓ [Stage 1 Eligibility → Stage 2 Decision]
  if should_act:
    dream_writer_executor(dreamer, target, all_agents) → writer.write_dream  [1 call]
    內部包含 1 次 relationship side effect (透過 writer 內部 on_dream)
  else:
    log reason, 0 writer call, 0 relationship side effect

Critical Invariants:
  - H2-I3/I4: target_agent_id 從 envelope.extra 拿,handler 不重新 random
  - H2-I7: decision=NO → 0 relationship mutations (因為 0 writer calls)
  - H2-I10: relationship side effect preserved (透過 writer 內部 1 次 on_dream)
  - H2-I11: 1 trigger → max 1 writer call (避免 legacy callback + handler 雙重執行)
  - H2-I13: 缺 target_agent_id → reject safely (log warning + skip)

Differences from EventHandler (M5.2-H Phase 1):
  - trigger_type filter: "dream" (vs "event")
  - writer_executor signature: (dreamer, target_agent_id, all_agents) 3 params
    (vs EventHandler writer_executor(agent_id) 1 param)
  - writer_executor 從 envelope.extra 拿 target + all_agents
  - 共用 Agency 4 stages 跟 trigger-only path
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.eventbus.schema import EventType, SoulEvent

from .agency import Agency, AgencyRunResult, run_agency
from .state import AgencyState
from .trigger import TriggerEnvelope

logger = logging.getLogger("soul_os.agency.dream_handler")


# Dream writer executor signature: (dreamer, target_agent_id, all_agents) -> awaitable None
# 跟 writer.write_dream 簽名完全對齊,handler 不做任何加工,直接轉發
DreamWriterExecutor = Callable[[str, str, List[str]], Awaitable[None]]


async def _default_noop_dream_writer(
    dreamer: str,
    target_agent_id: str,
    all_agents: List[str],
) -> None:
    """
    M5.2-H Phase 2 default dream writer executor: no-op.

    若 caller 沒注入 dream writer executor, handler 走完 4 stages 然後 log 結果。
    Production 必須注入實際 writer executor (run_server.py 注入
    `lambda d, t, a: writer.write_dream(d, t, a)`)。
    """
    logger.info(
        f"[DreamHandler] noop writer | "
        f"dreamer={dreamer} target={target_agent_id} all_agents={len(all_agents)}"
    )


class DreamHandler:
    """
    M5.2-H Phase 2 bus subscriber: 收到 AGENCY_TRIGGER (trigger_type="dream") → writer.write_dream。

    Lifecycle:
      1. constructor: inject Agency (or state) + dream_writer_executor
      2. register on bus: handler.handle_event (event_filter={AGENCY_TRIGGER})
      3. on AGENCY_TRIGGER event: validate envelope → run agency → if YES, invoke dream_writer_executor
    """
    def __init__(
        self,
        agency: Optional[Agency] = None,
        state: Optional[AgencyState] = None,
        dream_writer_executor: Optional[DreamWriterExecutor] = None,
    ):
        if agency is not None:
            self.agency = agency
        else:
            self.agency = Agency(state or AgencyState())
        self.dream_writer_executor = dream_writer_executor or _default_noop_dream_writer

    async def handle_event(self, event: SoulEvent) -> None:
        """
        Bus handler: 處理 AGENCY_TRIGGER event。

        M5.2-H Phase 2: trigger_type 限定 "dream"。
        其他 trigger_type 暫時 log + skip:
          - proactive_dm → 走 AgencyTriggerHandler
          - event → 走 EventHandler (M5.2-H Phase 1)
          - morning / night / heartbeat → M5.2-H 之後 phases
        """
        if event.event_type != EventType.AGENCY_TRIGGER:
            return

        # 1. Validate TriggerEnvelope
        envelope = self._parse_envelope(event)
        if envelope is None:
            return

        # 2. M5.2-H Phase 2 限定 dream
        if envelope.trigger_type != "dream":
            logger.debug(
                f"[DreamHandler] trigger_type={envelope.trigger_type} "
                f"not dream, skip (M5.2-H Phase 2 = dream only)"
            )
            return

        # 3. H2-I13: target_agent_id 必填 (scheduler 一定會塞,但防禦性檢查)
        target_agent_id = envelope.extra.get("target_agent_id")
        if not isinstance(target_agent_id, str) or not target_agent_id:
            logger.warning(
                f"[DreamHandler] missing/empty target_agent_id in extra, "
                f"reject safely | agent_id={envelope.agent_id} "
                f"extra_keys={list(envelope.extra.keys())}"
            )
            return

        # all_agents optional, 缺就 fallback 到 [dreamer] 單元素 list
        # (writer.write_dream 對 all_agents 是 list 用途, 缺不會 crash, 但語意上應該有)
        all_agents_raw = envelope.extra.get("all_agents", [envelope.agent_id])
        if not isinstance(all_agents_raw, list):
            all_agents_raw = [envelope.agent_id]
        all_agents: List[str] = [str(a) for a in all_agents_raw]

        # 4. Run Agency 4 stages (trigger-only path)
        now = datetime.now(timezone.utc)
        try:
            result = run_agency(
                state=self.agency.state,
                perception=None,
                now=now,
                trigger=envelope,
            )
        except Exception as e:
            logger.warning(
                f"[DreamHandler] run_agency 失敗: "
                f"agent_id={envelope.agent_id} trigger_type={envelope.trigger_type} err={e}"
            )
            return

        # 5. 根據 decision 決定要不要 invoke writer
        if result.decision.should_act:
            logger.info(
                f"[DreamHandler] decision=YES | "
                f"dreamer={envelope.agent_id} target={target_agent_id} "
                f"reason={result.decision.reason}"
            )
            # WRITER_ONLY: 寫 diary + relationship side effect (writer 內部)
            # 1 trigger → 1 writer call → 1 relationship side effect
            try:
                await self.dream_writer_executor(
                    envelope.agent_id,
                    target_agent_id,
                    all_agents,
                )
            except Exception as e:
                # 「拒絕問, 強制讀」: 失敗不中斷 bus
                logger.warning(
                    f"[DreamHandler] dream writer executor 失敗: "
                    f"dreamer={envelope.agent_id} target={target_agent_id} "
                    f"err={type(e).__name__}: {e}"
                )
        else:
            # H2-I6 / H2-I7: decision=NO → 0 writer calls, 0 relationship side effects
            logger.info(
                f"[DreamHandler] decision=NO | "
                f"dreamer={envelope.agent_id} target={target_agent_id} "
                f"reason={result.decision.reason}"
            )

    def _parse_envelope(self, event: SoulEvent) -> Optional[TriggerEnvelope]:
        """
        從 AGENCY_TRIGGER payload 構造 TriggerEnvelope。
        失敗 (缺欄位 / 型別錯) → log warning + return None。

        跟 AgencyTriggerHandler / EventHandler._parse_envelope 邏輯相同 (M5.2-F frozen contract):
        payload 必須含 trigger_type / agent_id / reason, extra 是 dict (含 target_agent_id / all_agents)。
        """
        payload = event.payload
        if not isinstance(payload, dict):
            logger.warning(
                f"[DreamHandler] payload 不是 dict: {type(payload).__name__}"
            )
            return None

        trigger_type = payload.get("trigger_type")
        agent_id = payload.get("agent_id")
        reason = payload.get("reason")

        if not isinstance(trigger_type, str):
            logger.warning(f"[DreamHandler] trigger_type 缺/非字串: {trigger_type!r}")
            return None
        if not isinstance(agent_id, str):
            logger.warning(f"[DreamHandler] agent_id 缺/非字串: {agent_id!r}")
            return None
        if not isinstance(reason, str):
            logger.warning(f"[DreamHandler] reason 缺/非字串: {reason!r}")
            return None

        elapsed_mins = payload.get("elapsed_mins", 0.0)
        if not isinstance(elapsed_mins, (int, float)):
            elapsed_mins = 0.0

        # timestamp optional, default = now
        timestamp_str = payload.get("timestamp")
        timestamp = None
        if isinstance(timestamp_str, str):
            try:
                from datetime import datetime as _dt
                timestamp = _dt.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                timestamp = None

        extra = payload.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}

        return TriggerEnvelope(
            trigger_type=trigger_type,
            agent_id=agent_id,
            reason=reason,
            elapsed_mins=float(elapsed_mins),
            timestamp=timestamp if timestamp else None,
            extra=extra,
        )
