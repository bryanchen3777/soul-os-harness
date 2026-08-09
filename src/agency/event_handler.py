"""
src/agency/event_handler.py — Soul OS M5.2-H EventHandler

Bry 派工 2026-08-08 M5.2-H Phase 1:
  - Migrate `event` scheduler trigger 從 AGENCY_BYPASS → Agency 4 stages
  - WRITER_ONLY (per C3): EventHandler 不呼叫 agent._fire_intent / AGENT_SPEAK
    只呼叫 writer.write_event(agent_id)
  - 跟 AgencyTriggerHandler 平行: AgencyTriggerHandler 處理 proactive_dm
    EventHandler 處理 event (過濾 trigger_type)

Architecture:
  Scheduler._fire_event
    ↓ publish AGENCY_TRIGGER (trigger_type="event")
  EventHandler.handle_event
    ↓ [validate envelope, filter trigger_type=="event"]
  run_agency(perception=None, trigger=envelope)
    ↓ [Stage 1 Eligibility → Stage 2 Decision]
  if should_act:
    writer_executor(agent_id) → writer.write_event(agent_id)  [1 call]
  else:
    log reason, 0 writer call

Lifecycle:
  1. constructor: inject Agency (or state) + writer_executor
  2. register on bus: handler.handle_event (event_filter={AGENCY_TRIGGER})
  3. on AGENCY_TRIGGER: validate → run agency → if YES, invoke writer_executor

Differences from AgencyTriggerHandler (M5.2-G):
  - trigger_type filter: "event" (vs "proactive_dm")
  - executor signature: writer_executor(agent_id) (vs llm_executor(agent_id, trigger))
  - 語意: WRITER_ONLY (生成內容 + 寫 diary file) vs AGENT_SPEAK (角色說話)
  - 共用 Agency 4 stages 跟 trigger-only path (perception=None, trigger=envelope)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from src.eventbus.schema import EventType, SoulEvent

from .agency import Agency, AgencyRunResult, run_agency
from .state import AgencyState
from .trigger import TriggerEnvelope

logger = logging.getLogger("soul_os.agency.event_handler")


# Writer executor signature: (agent_id) -> awaitable None
# 跟 AgencyTriggerHandler 的 LLMExecutor 簽名 (agent_id, trigger) 不同:
# EventHandler 只需要 agent_id 因為 writer 從 agent_id 內部 context 拿 trigger 細節
# (trigger.extra / trigger.reason / trigger.timestamp 都在 envelope 內, 但 writer 介面只吃 agent_id)
WriterExecutor = Callable[[str], Awaitable[None]]


async def _default_noop_writer(agent_id: str) -> None:
    """
    M5.2-H default writer executor: no-op.

    若 caller 沒注入 writer executor, handler 走完 4 stages 然後 log 結果。
    Production 必須注入實際 writer executor (run_server.py 注入
    `lambda agent_id: writer.write_event(agent_id)`)。
    """
    logger.info(
        f"[EventHandler] noop writer | agent_id={agent_id}"
    )


class EventHandler:
    """
    M5.2-H bus subscriber: 收到 AGENCY_TRIGGER (trigger_type="event") → writer.write_event。

    Lifecycle:
      1. constructor: inject Agency (or state) + writer_executor
      2. register on bus: handler.handle_event
      3. on AGENCY_TRIGGER event: validate envelope → run agency → if YES, invoke writer_executor
    """
    def __init__(
        self,
        agency: Optional[Agency] = None,
        state: Optional[AgencyState] = None,
        writer_executor: Optional[WriterExecutor] = None,
    ):
        if agency is not None:
            self.agency = agency
        else:
            self.agency = Agency(state or AgencyState())
        self.writer_executor = writer_executor or _default_noop_writer

    async def handle_event(self, event: SoulEvent) -> None:
        """
        Bus handler: 處理 AGENCY_TRIGGER event。

        M5.2-H Phase 1: trigger_type 限定 "event"。
        其他 trigger_type 暫時 log + skip (等後續 migration):
          - proactive_dm → 走 AgencyTriggerHandler
          - dream / morning / night / heartbeat → M5.2-H 之後 phases
        """
        if event.event_type != EventType.AGENCY_TRIGGER:
            return

        # 1. Validate TriggerEnvelope
        envelope = self._parse_envelope(event)
        if envelope is None:
            return

        # 2. M5.2-H Phase 1 限定 event
        if envelope.trigger_type != "event":
            logger.debug(
                f"[EventHandler] trigger_type={envelope.trigger_type} "
                f"not event, skip (M5.2-H Phase 1 = event only)"
            )
            return

        # 3. Run Agency 4 stages (trigger-only path)
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
                f"[EventHandler] run_agency 失敗: "
                f"agent_id={envelope.agent_id} trigger_type={envelope.trigger_type} err={e}"
            )
            return

        # 4. 根據 decision 決定要不要 invoke writer
        if result.decision.should_act:
            logger.info(
                f"[EventHandler] decision=YES | "
                f"agent_id={envelope.agent_id} reason={result.decision.reason}"
            )
            # 執行 writer (Production 注入實際 executor; test 注入 mock)
            # WRITER_ONLY (C3): 只寫 diary, 不調 LLM, 不發 AGENT_SPEAK
            try:
                await self.writer_executor(envelope.agent_id)
            except Exception as e:
                # 「拒絕問, 強制讀」: 失敗不中斷 bus
                logger.warning(
                    f"[EventHandler] writer executor 失敗: "
                    f"agent_id={envelope.agent_id} err={type(e).__name__}: {e}"
                )
        else:
            logger.info(
                f"[EventHandler] decision=NO | "
                f"agent_id={envelope.agent_id} reason={result.decision.reason}"
            )

    def _parse_envelope(self, event: SoulEvent) -> Optional[TriggerEnvelope]:
        """
        從 AGENCY_TRIGGER payload 構造 TriggerEnvelope。
        失敗 (缺欄位 / 型別錯) → log warning + return None。

        跟 AgencyTriggerHandler._parse_envelope 邏輯相同 (M5.2-F frozen contract):
        payload 必須含 trigger_type / agent_id / reason, 其餘欄位 optional。
        """
        payload = event.payload
        if not isinstance(payload, dict):
            logger.warning(
                f"[EventHandler] payload 不是 dict: {type(payload).__name__}"
            )
            return None

        trigger_type = payload.get("trigger_type")
        agent_id = payload.get("agent_id")
        reason = payload.get("reason")

        if not isinstance(trigger_type, str):
            logger.warning(f"[EventHandler] trigger_type 缺/非字串: {trigger_type!r}")
            return None
        if not isinstance(agent_id, str):
            logger.warning(f"[EventHandler] agent_id 缺/非字串: {agent_id!r}")
            return None
        if not isinstance(reason, str):
            logger.warning(f"[EventHandler] reason 缺/非字串: {reason!r}")
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
