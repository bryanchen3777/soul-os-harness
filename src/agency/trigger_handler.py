"""
src/agency/trigger_handler.py — Soul OS M5.2-G AgencyTriggerHandler

Bry 派工 2026-08-08 M5.2-G:
Handler 訂閱 EventType.AGENCY_TRIGGER,
validate TriggerEnvelope,
呼叫 Agency.run(perception=None, trigger=envelope, now)。

Handler 不負責:
  - priority / novelty / perception score / accepted 重算
  - cooldown policy
  - LLM call (M5.2-G Phase 1 留給 caller 處理)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from src.eventbus.schema import EventType, SoulEvent

from .agency import Agency, AgencyRunResult, run_agency
from .state import AgencyState
from .trigger import TriggerEnvelope

logger = logging.getLogger("soul_os.agency.trigger_handler")


# LLM executor signature: (agent_id, trigger_envelope) -> awaitable None
LLMExecutor = Callable[[str, TriggerEnvelope], Awaitable[None]]


async def _default_noop_executor(agent_id: str, trigger: TriggerEnvelope) -> None:
    """
    M5.2-G default LLM executor: no-op.
    
    若 caller 沒注入 LLM executor, handler 走完 4 stages 然後 log 結果。
    Production 必須注入實際 LLM executor (run_server.py)。
    """
    logger.info(
        f"[AgencyTriggerHandler] noop executor | "
        f"agent_id={agent_id} trigger_type={trigger.trigger_type}"
    )


class AgencyTriggerHandler:
    """
    M5.2-G bus subscriber: 收到 AGENCY_TRIGGER → 跑 Agency 4 stages。
    
    Lifecycle:
      1. constructor: inject Agency (or state) + llm_executor
      2. register on bus: handler.handle_event
      3. on AGENCY_TRIGGER event: validate envelope → run agency → if YES, invoke llm_executor
    """
    def __init__(
        self,
        agency: Optional[Agency] = None,
        state: Optional[AgencyState] = None,
        llm_executor: Optional[LLMExecutor] = None,
    ):
        if agency is not None:
            self.agency = agency
        else:
            self.agency = Agency(state or AgencyState())
        self.llm_executor = llm_executor or _default_noop_executor
        # M6.1-9 cooldown 衝突修法 (Bry 拍板 2026-08-16, 跟 DiaryHandler/DreamHandler 同根因同修法):
        # 根因: 單一共享 AgencyState 導致多個 agent 同時觸發時, 第一個 agent 執行後設置
        # last_action_at, 後續 agent 被 60s action cooldown 擋住。
        # 修法: 每個 agent_id 一個獨立 AgencyState, cooldown 按 agent 隔離。
        # 向後相容: 若 caller 傳入 state (非 None), 作為第一個 agent 的種子 state。
        self._states: Dict[str, AgencyState] = {}
        self._seed_state = state

    def _get_state(self, agent_id: str) -> AgencyState:
        """Per-agent AgencyState: cooldown 按 agent 隔離 (M6.1-9 修法)。"""
        if agent_id not in self._states:
            if self._seed_state is not None:
                self._states[agent_id] = self._seed_state
                self._seed_state = None
            else:
                self._states[agent_id] = AgencyState()
        return self._states[agent_id]
    
    async def handle_event(self, event: SoulEvent) -> None:
        """
        Bus handler: 處理 AGENCY_TRIGGER event。
        
        M5.2-G Phase 1: trigger_type 限定 proactive_dm。
        其他 trigger_type 暫時 log + skip (等後續 migration)。
        """
        if event.event_type != EventType.AGENCY_TRIGGER:
            return
        
        # 1. Validate TriggerEnvelope
        envelope = self._parse_envelope(event)
        if envelope is None:
            return
        
        # 2. M5.2-G Phase 1 限定 proactive_dm
        if envelope.trigger_type != "proactive_dm":
            logger.debug(
                f"[AgencyTriggerHandler] trigger_type={envelope.trigger_type} "
                f"not yet implemented, skip (M5.2-G Phase 1 = proactive_dm only)"
            )
            return
        
        # 3. Run Agency 4 stages (trigger-only path)
        now = datetime.now(timezone.utc)
        try:
            result = run_agency(
                state=self._get_state(envelope.agent_id),
                perception=None,
                now=now,
                trigger=envelope,
            )
        except Exception as e:
            logger.warning(
                f"[AgencyTriggerHandler] run_agency 失敗: "
                f"agent_id={envelope.agent_id} trigger_type={envelope.trigger_type} err={e}"
            )
            return
        
        # 4. 根據 decision 決定要不要 invoke LLM
        if result.decision.should_act:
            logger.info(
                f"[AgencyTriggerHandler] decision=YES | "
                f"agent_id={envelope.agent_id} trigger_type={envelope.trigger_type} "
                f"reason={result.decision.reason}"
            )
            # 執行 LLM (Production 注入實際 executor; test 注入 mock)
            try:
                await self.llm_executor(envelope.agent_id, envelope)
            except Exception as e:
                # 「拒絕問, 強制讀」: 失敗不中斷 bus
                logger.warning(
                    f"[AgencyTriggerHandler] LLM executor 失敗: "
                    f"agent_id={envelope.agent_id} err={type(e).__name__}: {e}"
                )
        else:
            logger.info(
                f"[AgencyTriggerHandler] decision=NO | "
                f"agent_id={envelope.agent_id} trigger_type={envelope.trigger_type} "
                f"reason={result.decision.reason}"
            )
    
    def _parse_envelope(self, event: SoulEvent) -> Optional[TriggerEnvelope]:
        """
        從 AGENCY_TRIGGER payload 構造 TriggerEnvelope。

        M5.2-Q-4 (Bry 拍板 2026-08-08): 收斂到 TriggerEnvelope.from_payload
        (Q-3 feasibility 確認 4 handler 100% 等價,Logger prefix 保留供 debuggability).
        """
        return TriggerEnvelope.from_payload(event.payload, logger_name="[AgencyTriggerHandler]")
