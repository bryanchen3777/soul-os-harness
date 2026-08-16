"""
src/agency/diary_handler.py — Soul OS M5.2-H Phase 3 DiaryHandler

Bry 派工 2026-08-08 M5.2-H Phase 3:
  - Migrate `morning` + `night` scheduler trigger 從 AGENCY_BYPASS → Agency 4 stages
  - 一個 Handler 同時負責 morning + night (兩者都是 diary_callback_factory pattern)
  - Handler 只負責把 trigger 對應到既有 diary execution, 不重新找 agent / memories / persona
  - WRITER_ONLY (精神跟 Phase 1/2 一致): 不調 LLM, 不發 AGENT_SPEAK
    只呼叫既有的 diary callback (cb(agent_id, slot))
  - 跟 AgencyTriggerHandler / EventHandler / DreamHandler 平行:
    AgencyTriggerHandler 處理 proactive_dm
    EventHandler 處理 event
    DreamHandler 處理 dream
    DiaryHandler 處理 morning + night (共用)

Architecture:
  Scheduler._fire_all(slot)
    ↓ [registered agent iteration + dedup 全部保留]
    ↓ [過渡期: callback 仍被呼叫 (noop in production) 為向後相容]
    ↓
  _publish_agency_trigger(agent_id, trigger_type=slot)  # slot = "morning" | "night"
    ↓
  EventType.AGENCY_TRIGGER
    ↓
  DiaryHandler.handle_event
    ↓ [validate envelope, filter trigger_type in {"morning", "night"}]
  run_agency(perception=None, trigger=envelope)
    ↓
  Stage 1 (Eligibility) → Stage 2 (Decision)
    ↓
  if should_act: diary_writer_executor(agent_id, slot) → cb(agent_id, slot)  [1 call]
  else: log reason, 0 diary call, 0 LLM call

Differences from previous handlers:
  - trigger_type filter: {"morning", "night"} 兩個都接 (vs 各自單一 trigger_type)
  - writer_executor signature: (agent_id, slot) 2 params
    (EventHandler: (agent_id); DreamHandler: (dreamer, target, all_agents))
  - 行為: WRITER_ONLY 但不直接寫 file, 而是 delegate 回 scheduler 註冊的 diary callback
    (callback 內部做 generate_diary_entry → LLM → writer.write_diary 全套)
  - 共用 Agency 4 stages 跟 trigger-only path
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Optional

from src.eventbus.schema import EventType, SoulEvent

from .agency import Agency, AgencyRunResult, run_agency
from .state import AgencyState
from .trigger import TriggerEnvelope

logger = logging.getLogger("soul_os.agency.diary_handler")


# Diary writer executor signature: (agent_id, slot) -> awaitable None
# slot ∈ {"morning", "night"}
#
# M5.2-J Phase J-1 doc correction (Bry 拍板 2026-08-08):
#   舊 docstring 描述「從 scheduler._callbacks[agent_id][slot] 拿 callback」
#   已不是 production 架構。M5.2-I Phase 7 (I-7) 後 production 不再用 scheduler.register
#   註冊 morning/night callback (即 scheduler._callbacks[morning][night] 在 production
#   為 empty)。目前真實路徑:
#     - run_server.py 維護獨立 dict `diary_callbacks_real: Dict[str, Any]`
#       (key=agent_id, value=await diary_callback_factory(agent_id) 產出的 real callback)
#     - run_server.py 把這個 dict 透過 closure 注進 DiaryHandler 的 diary_writer_executor
#     - DiaryHandler.handle_event 在 decision=YES 時 invoke diary_writer_executor(agent_id, slot)
#     - executor 內部 `cb_real = diary_callbacks_real.get(agent_id); await cb_real(agent_id, slot)`
#   Scheduler 端 _fire_all 仍舊透過 _publish_agency_trigger 發 AGENCY_TRIGGER
#   (trigger_type=slot), 不再查 _callbacks (I-8 iteration source refactor)。
#   這層 doc correction 只改 docstring, 不改 type alias / runtime behavior。
DiaryWriterExecutor = Callable[[str, str], Awaitable[None]]


# DiaryHandler 接受的 trigger_type 集合 (M5.2-H Phase 3 限定)
SUPPORTED_DIARY_SLOTS = ("morning", "night")


async def _default_noop_diary_writer(agent_id: str, slot: str) -> None:
    """
    M5.2-H Phase 3 default diary writer executor: no-op.

    若 caller 沒注入 diary writer executor, handler 走完 4 stages 然後 log 結果。
    Production 必須注入實際 executor; run_server.py 維護 `diary_callbacks_real` dict
    (key=agent_id, value=diary_callback_factory 產出的 real callback) 並透過 closure
    注進 DiaryHandler 的 diary_writer_executor。executor 內部
    `cb_real = diary_callbacks_real.get(agent_id); await cb_real(agent_id, slot)`。
    舊 docstring 描述的 `scheduler._callbacks[aid][s](aid, s)` lookup pattern
    在 M5.2-I Phase 7 (I-7) 後已非 production 路徑 (見 DiaryWriterExecutor 註解)。
    """
    logger.info(
        f"[DiaryHandler] noop writer | agent_id={agent_id} slot={slot}"
    )


class DiaryHandler:
    """
    M5.2-H Phase 3 bus subscriber: 收到 AGENCY_TRIGGER (trigger_type ∈ {morning, night})
    → 既有 diary callback execution.

    Lifecycle:
      1. constructor: inject Agency (or state) + diary_writer_executor
      2. register on bus: handler.handle_event (event_filter={AGENCY_TRIGGER})
      3. on AGENCY_TRIGGER event: validate envelope → run agency → if YES, invoke diary_writer_executor
    """
    def __init__(
        self,
        agency: Optional[Agency] = None,
        state: Optional[AgencyState] = None,
        diary_writer_executor: Optional[DiaryWriterExecutor] = None,
    ):
        if agency is not None:
            self.agency = agency
        else:
            self.agency = Agency(state or AgencyState())
        self.diary_writer_executor = diary_writer_executor or _default_noop_diary_writer
        # M6.1-9 cooldown 衝突修法 (Bry 拍板 2026-08-16):
        # 根因: 單一共享 AgencyState 導致 morning/night slot 同時觸發 10 個 agent 時,
        # 第一個 agent 執行後設置 last_action_at, 後續 9 個 agent 被 60s action cooldown 擋住
        # (log 證據: 08:00:14 yua decision=YES → 08:00:16 ruka decision=NO
        #  "action cooldown active (2.7s < 60s)", 其餘 8 個角色同樣被擋)。
        # 修法: 每個 agent_id 一個獨立 AgencyState, cooldown 按 agent 隔離。
        # 向後相容: 若 caller 傳入 state (非 None), 作為第一個 agent 的種子 state
        # (H3-I4/I5 測試用 decision_cooldown 驗證 decision=NO 的行為不變)。
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

        M5.2-H Phase 3: trigger_type 限定 {"morning", "night"}。
        其他 trigger_type 暫時 log + skip:
          - proactive_dm → 走 AgencyTriggerHandler
          - event → 走 EventHandler
          - dream → 走 DreamHandler
          - heartbeat → suspended, 不 migrate
        """
        if event.event_type != EventType.AGENCY_TRIGGER:
            return

        # 1. Validate TriggerEnvelope
        envelope = self._parse_envelope(event)
        if envelope is None:
            return

        # 2. M5.2-H Phase 3 限定 morning / night
        if envelope.trigger_type not in SUPPORTED_DIARY_SLOTS:
            logger.debug(
                f"[DiaryHandler] trigger_type={envelope.trigger_type} "
                f"not in {SUPPORTED_DIARY_SLOTS}, skip (M5.2-H Phase 3 = morning/night only)"
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
                f"[DiaryHandler] run_agency 失敗: "
                f"agent_id={envelope.agent_id} slot={envelope.trigger_type} err={e}"
            )
            return

        # 4. 根據 decision 決定要不要 invoke writer
        if result.decision.should_act:
            logger.info(
                f"[DiaryHandler] decision=YES | "
                f"agent_id={envelope.agent_id} slot={envelope.trigger_type} "
                f"reason={result.decision.reason}"
            )
            # WRITER_ONLY: delegate 回既有 diary callback (內部跑 generate_diary_entry → LLM → writer)
            # 1 trigger → 1 writer call (防止 legacy callback + handler 雙重執行)
            try:
                await self.diary_writer_executor(
                    envelope.agent_id,
                    envelope.trigger_type,
                )
            except Exception as e:
                # 「拒絕問, 強制讀」: 失敗不中斷 bus
                logger.warning(
                    f"[DiaryHandler] diary writer executor 失敗: "
                    f"agent_id={envelope.agent_id} slot={envelope.trigger_type} "
                    f"err={type(e).__name__}: {e}"
                )
        else:
            # decision=NO → 0 diary call, 0 LLM call
            logger.info(
                f"[DiaryHandler] decision=NO | "
                f"agent_id={envelope.agent_id} slot={envelope.trigger_type} "
                f"reason={result.decision.reason}"
            )

    def _parse_envelope(self, event: SoulEvent) -> Optional[TriggerEnvelope]:
        """
        從 AGENCY_TRIGGER payload 構造 TriggerEnvelope。

        M5.2-Q-4 (Bry 拍板 2026-08-08): 收斂到 TriggerEnvelope.from_payload
        (Q-3 feasibility 確認 4 handler 100% 等價,Logger prefix 保留供 debuggability).
        """
        return TriggerEnvelope.from_payload(event.payload, logger_name="[DiaryHandler]")
