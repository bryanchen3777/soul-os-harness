"""
src/work/workflow.py
WorkflowOrchestrator — Chief 的 decision / assign / consume 閉環（domain orchestration）。

這是「domain orchestration」（Chief 的決策邏輯），不是「execution orchestration」
（DSH subagent，屬後續 phase）。Chief 透過本 orchestrator 建立 Work、指派 role、
消費 Specialist 的 handoff result、fold 出 current WorkObject 判斷下一步。

核心原則（2D §1）：
> Soul OS owns the durable work truth. DSH owns ephemeral execution.

orchestrator 只透過 `WorkKernel` 操作 durable state（single-writer 不破壞），
不直接 import / 使用 `WorkStore`。全程不 import 任何 DSH type、不呼叫 LLM、
不建 subagent。

Canonical 來源（權威，不得修改）：
- docs/DSH-WORK-CONTRACT.md §4（state machine）、§6（Handoff Protocol）、§7（Adapter mapping）
- docs/DSH-PERSISTENCE.md §1（single-writer rule）
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .kernel import WorkKernel
from .roles import Role
from .schema import (
    ExecutionShape,
    HandoffResult,
    Provenance,
    WorkEvent,
    WorkEventType,
    WorkObject,
    WorkState,
)


def derive_execution_shape(work: WorkObject) -> ExecutionShape:
    """從 Work Object 語義推導 execution shape（Soul 決定，adapter 只 translate）。

    規則（P1 decomposition §3.3）：
    - work.dependencies 非空 → multi_stage
    - 需「無 human/chief 介入的多輪自動續輪」→ continuous
    - 其餘（含 blocked 後單輪 specialist resume）→ single_shot

    resume discriminator（關鍵）：blocked 後由單一 specialist 再 handoff 一輪
    完成（blocked → resume 回 in_progress → consume_handoff）是 single_shot，
    不是 continuous。continuous 只保留給 goal 驅動多輪自動續輪。
    """
    if work.dependencies:
        return ExecutionShape.MULTI_STAGE
    # continuous 的判別：目前 Work Object 無「goal 驅動」欄位。
    # 第一期：dependencies 永為 []（create_work 硬編 []），且無 goal 語義載體，
    # 故本函數在第一期實質只回 single_shot。continuous 的觸發條件待 P1-D
    # 定義（goal resume semantics），此處先以「resume_state 帶 continuous 標記」為
    # 預留判別點，但不實作（避免假驗證）。
    return ExecutionShape.SINGLE_SHOT


class WorkflowOrchestrator:
    """Chief 的 decision / assign / consume 閉環。

    只透過 `WorkKernel` 操作 durable state（single-writer 不破壞）：
    - create_work：建立 Work（proposed，記 state_transition）
    - assign：委派給 kernel.assign（記 decision_made）
    - consume_handoff：委派給 kernel.record_handoff（記 artifact/evidence/decision）
    - synthesize：fold 出 current WorkObject，供 Chief 判斷下一步

    不 import 任何 DSH type、不呼叫 LLM、不建 subagent。
    """

    def __init__(
        self,
        kernel: WorkKernel | None = None,
        data_dir: Path | str | None = None,
    ):
        """可注入既有 kernel（測試 / 組合用），或依 data_dir 建一個。

        預設 data_dir=None → kernel 用 data_root() / "work"（durable truth 位置）。
        """
        self._kernel = kernel if kernel is not None else WorkKernel(data_dir=data_dir)

    # ── Chief 的 decision / assign / consume 閉環 ──

    def create_work(self, objective: str, owner: str) -> str:
        """建立一個 Work（proposed），記一筆 state_transition（creation event）。

        回傳 work_id（後續 assign / consume / synthesize 的 handle）。
        owner 是 role（chief | developer | ... | soul_identity），不是 DSH id（2A §8.2）。
        """
        owner = owner.value if isinstance(owner, Role) else owner
        work_id = str(uuid4())
        event = WorkEvent(
            work_id=work_id,
            event_type=WorkEventType.STATE_TRANSITION,
            payload={
                "from": None,
                "to": WorkState.PROPOSED.value,
                "objective": objective,
                "owner": owner,
                "assigned_agents": [],
                "dependencies": [],
            },
            provenance=Provenance(role=owner, capability="orchestration"),
        )
        self._kernel.append(event)
        return work_id

    def assign(self, work_id: str, role: Role | str) -> WorkEvent:
        """Chief 指派 role 給 work（work.assign capability）→ 記 decision_made。

        委派給 kernel.assign（Chief 的自主指派決策，2A §3.1：decision 只記錄、
        供 audit，不 gate）。指派本身是 autonomous，不需 Human approval。
        """
        return self._kernel.assign(work_id, role)

    def consume_handoff(self, handoff: HandoffResult) -> WorkEvent:
        """消費 Specialist 的 handoff result（2A §6）→ 記 artifact/evidence/decision。

        委派給 kernel.record_handoff。result_type 只能是 artifact / evidence /
        decision（不得有 approval——Human Approval 走獨立的 Human authority path）。
        """
        return self._kernel.record_handoff(handoff)

    def synthesize(self, work_id: str) -> WorkObject:
        """fold 出 current WorkObject，供 Chief 判斷下一步（唯讀）。"""
        return self._kernel.fold(work_id)
