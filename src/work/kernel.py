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

from pathlib import Path

from .bridge import DURABLE_WRITER
from .roles import Role
from .schema import (
    HandoffResult,
    Provenance,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkObject,
)
from .store import NotDurableWriterError, WorkStore


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
        """
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
