"""
src/work/kernel.py
WorkKernel — durable work state 的唯一 writer（single-writer enforcement）。

核心原則（2D §1）：
> Soul OS owns the durable work truth. DSH owns ephemeral execution.

只有 Work kernel 能寫 durable state；Chief / Specialist / DSH 都透過 kernel，
不直接寫 store。非 kernel 的 actor 呼叫寫入 → 拋 NotDurableWriterError。

Canonical 來源（權威，不得修改）：
- docs/DSH-PERSISTENCE.md §1（single-writer rule）
- docs/DSH-WORK-CONTRACT.md §5（capability authorization）、§6（Handoff Protocol）
"""
from __future__ import annotations

import logging
from pathlib import Path

from .bridge import DURABLE_WRITER
from .roles import Role
from .schema import (
    HandoffResult,
    HandoffStatus,
    Provenance,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkObject,
    WorkState,
)
from .state_machine import validate_transition
from .store import (
    NotDurableWriterError,
    WorkStore,
    derive_idempotency_key_from_handoff,
)

logger = logging.getLogger(__name__)


# HandoffResult.result_type → WorkEventType（2A §6：result_type 只能是
# artifact / evidence / decision，不得有 approval）。
_HANDOFF_EVENT_TYPE: dict[ResultType, WorkEventType] = {
    ResultType.ARTIFACT: WorkEventType.ARTIFACT_PRODUCED,
    ResultType.EVIDENCE: WorkEventType.EVIDENCE_PRODUCED,
    ResultType.DECISION: WorkEventType.DECISION_MADE,
}

# HandoffResult.result_type → provenance capability（capability-neutral，非 DSH tool 名）。
_HANDOFF_CAPABILITY: dict[ResultType, str] = {
    ResultType.ARTIFACT: "artifact.create",
    ResultType.EVIDENCE: "evidence.create",
    ResultType.DECISION: "decision",
}


def result_type_for_capability(capability: str) -> ResultType:
    """request capability → 預期 result_type（canonical，adapter mirror 同一語義）。

    - capability 含 "evidence" → EVIDENCE
    - capability 含 "decision" → DECISION
    - 其餘 → ARTIFACT

    與 mock adapter `dsh_adapter/soul-dsh-adapter.mjs` 的 `mockResultType` 一致，
    但此為 Domain Core canonical：execution.py anchor 以它驗證 handoff.result_type，
    確保 event 類型 + provenance capability 不因 adapter 竄改而錯記。
    """
    if "evidence" in capability:
        return ResultType.EVIDENCE
    if "decision" in capability:
        return ResultType.DECISION
    return ResultType.ARTIFACT


class WorkKernel:
    """durable work state 的唯一 writer。

    包住 WorkStore：只有 kernel 能 append（single-writer enforcement）。
    Chief / Specialist / DSH 都透過 kernel 的 assign / record_handoff 等操作，
    不直接寫 store。
    """

    def __init__(self, data_dir: Path | str | None = None):
        self._store = WorkStore(data_dir=data_dir)

    # ── 讀取（任何 actor 可讀） ──

    def fold(self, work_id: str) -> WorkObject:
        """把指定 work_id 的所有 event fold 成 current WorkObject（唯讀）。"""
        return self._store.fold(work_id)

    # ── 寫入（只有 kernel 能寫） ──

    def append(self, event: WorkEvent, actor: str = DURABLE_WRITER) -> None:
        """append 一筆 WorkEvent（single-writer enforcement）。

        actor 預設為 kernel（唯一 writer）。非 kernel 的 actor（chief / developer /
        dsh_adapter / ...）呼叫寫入 → 拋 NotDurableWriterError。

        enforcement 在 WorkStore.append（durable write boundary）強制：kernel 只是
        把 actor 明確傳給 store.append，不因 internal access 破壞 authorization。
        """
        self._store.append(event, actor)

    # ── kernel 專屬操作（Chief / Specialist 透過 kernel，不直接寫 store） ──

    def assign(self, work_id: str, role: Role | str) -> WorkEvent:
        """Chief 透過 kernel 指派 role 給 work（work.assign capability）。

        記錄一筆 decision_made event（Chief 的自主指派決策，2A §3.1：decision
        只記錄、供 audit，不 gate），provenance 標記 role=chief /
        capability=work.assign。指派本身是 autonomous，不需 Human approval。
        """
        role = Role(role)
        event = WorkEvent(
            work_id=work_id,
            event_type=WorkEventType.DECISION_MADE,
            payload={"decision": {"assign_role": role.value}},
            provenance=Provenance(role=Role.CHIEF.value, capability="work.assign"),
        )
        self.append(event)
        return event

    def record_handoff(self, handoff: HandoffResult) -> WorkEvent:
        """Specialist 透過 kernel 記錄 handoff result（2A §6）。

        result_type 只能是 artifact / evidence / decision（不得有 approval）：
        - artifact → artifact_produced（output_refs = artifact_refs）
        - evidence → evidence_produced（output_refs = evidence_refs）
        - decision → decision_made

        Resume idempotency（R1）：consume_handoff 在 Domain Core 做 dedup。
        idempotency_key = hash(work_id + role + result_type + refs/decision)；
        若 key 已存在於 durable log（duplicate / crash-after-write / retry）→
        skip（回傳既有 event），不重複 append——effectively-once 由 durable log
        保證（Soul OS owns the durable work truth, 2D §1 / §4），不依賴
        in-process 狀態、不依賴 DSH Adapter。

        M1（P1-Preflight）：status 語義（2A §6）——
        - status == DONE：記錄產出（上述 event_type 邏輯，現狀不變）。
        - status == BLOCKED / NEEDS_INPUT：**不記錄產出**，改記錄
          state_transition(current → blocked)（2A §4：blocked 是 non-terminal，
          任何 active state 可進 blocked）；非法 transition（含 terminal state
          再進 blocked）→ InvalidTransitionError 自然拋出。此分支**不做 dedup**：
          寫的是 state_transition（重複 append 不會污染產出 log；且 blocked →
          blocked 由 state machine 判為非法）。
        """
        # M1：blocked / needs_input → state_transition(current → blocked)，無產出
        if handoff.status in (HandoffStatus.BLOCKED, HandoffStatus.NEEDS_INPUT):
            current = self.fold(handoff.work_id)
            from_state = current.state
            validate_transition(from_state, WorkState.BLOCKED)
            event = WorkEvent(
                work_id=handoff.work_id,
                event_type=WorkEventType.STATE_TRANSITION,
                payload={
                    "from": from_state.value,
                    "to": WorkState.BLOCKED.value,
                    "status": handoff.status.value,
                    "resume_hint": handoff.resume_hint,
                },
                provenance=Provenance(
                    role=handoff.role,
                    capability=_HANDOFF_CAPABILITY[handoff.result_type],
                ),
            )
            self.append(event)
            return event

        event_type = _HANDOFF_EVENT_TYPE[handoff.result_type]
        capability = _HANDOFF_CAPABILITY[handoff.result_type]

        if handoff.result_type == ResultType.ARTIFACT:
            payload = {"artifact": {"refs": handoff.artifact_refs}}
            output_refs = list(handoff.artifact_refs)
        elif handoff.result_type == ResultType.EVIDENCE:
            payload = {"evidence": {"refs": handoff.evidence_refs}}
            output_refs = list(handoff.evidence_refs)
        else:  # DECISION
            payload = {"decision": handoff.decision}
            output_refs = []

        # dedup by idempotency_key：先查 durable log，命中 → skip，不重複 append。
        idem_key = derive_idempotency_key_from_handoff(handoff)
        existing = self._store.handoff_event_by_key(handoff.work_id, idem_key)
        if existing is not None:
            logger.info(
                "[WorkKernel] dedup: handoff idempotency_key=%s already recorded; "
                "skip append (effectively-once)",
                idem_key,
            )
            return existing

        event = WorkEvent(
            work_id=handoff.work_id,
            event_type=event_type,
            payload=payload,
            provenance=Provenance(
                role=handoff.role,
                capability=capability,
                output_refs=output_refs,
            ),
        )
        self.append(event)
        return event
