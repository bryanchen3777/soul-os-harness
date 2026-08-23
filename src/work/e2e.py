"""
src/work/e2e.py
End-to-end vertical slice（2A–2D 完整閉環）。

把 MVP-1~6 的所有模組串成一個可執行的 vertical slice：
Human → Chief → Work → Specialist → Handoff → Evidence → Chief → Approval
→ privileged action → Evidence → Durable Store。

核心原則（2D §1）：
> Soul OS owns the durable work truth. DSH owns ephemeral execution.

本模組只走既有模組（WorkflowOrchestrator / WorkKernel / AuthorityManager /
AuthorityStore / state_machine / schema），不 import 任何 DSH type、不建 subagent、
不呼叫 LLM。

Human authority 由注入的 `HumanAuthorityPort` 驗證（deny-by-default，2A invariant #2：
No agent may manufacture, infer, or substitute a Human Approval）。本模組不實作真實
認證；`approval` / `context` 的預設值是 demonstration fixture（資料，非 proof），
真正的 authentication 由注入的 port 執行。

Canonical 來源（權威，不得修改）：
- docs/DSH-WORK-CONTRACT.md §4（state machine）、§6（Handoff Protocol）
- docs/DSH-HUMAN-AUTHORITY.md §2（Approval）、§6（provenance chain）
- docs/DSH-PERSISTENCE.md §1（single-writer）、§5（recovery flow）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .authority import (
    ActionScope,
    AgentAction,
    Approval,
    AuthorityManager,
    CapabilityGrant,
    HumanAuthorityContext,
    HumanAuthorityPort,
)
from .kernel import WorkKernel
from .persistence import AuthorityStore
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
from .workflow import WorkflowOrchestrator


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 schema.py 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


def _future(**delta_kwargs) -> datetime:
    """回傳未來時間（預設 +1 小時）。"""
    return datetime.now(timezone.utc) + timedelta(**delta_kwargs)


@dataclass
class VerticalSliceResult:
    """`run_vertical_slice()` 的完整閉環結果（供驗證 / resume 測試）。

    承載閉環的 durable evidence：final WorkObject、Approval、CapabilityGrant、
    AgentAction、authorization 前後狀態、以及寫入 durable store 的 evidence event。
    """

    work_id: str
    work: WorkObject            # final fold（state=done，含 artifact + evidence）
    approval: Approval          # Human Approval（provenance chain 起點）
    grant: CapabilityGrant      # CapabilityGrant（grant → approval 一對一）
    action: AgentAction         # privileged action（action → grant 一對一）
    authorized: bool            # consume 前 is_authorized(action)
    post_consume_authorized: bool  # consume 後 is_authorized(action)（single_action 失效）
    evidence_event: WorkEvent   # evidence → durable store（evidence → action 一對一）


def _record_transition(
    kernel: WorkKernel,
    work_id: str,
    from_state: WorkState,
    to_state: WorkState,
    *,
    role: str,
    capability: str,
) -> WorkEvent:
    """驗證並記錄一筆 state_transition（2A §4）。

    `validate_transition` 先驗證合法性（非法 → InvalidTransitionError），
    再經 `kernel.append` 寫 durable state（single-writer 不破壞）。
    """
    validate_transition(from_state, to_state)
    event = WorkEvent(
        work_id=work_id,
        event_type=WorkEventType.STATE_TRANSITION,
        payload={"from": from_state.value, "to": to_state.value},
        provenance=Provenance(role=role, capability=capability),
    )
    kernel.append(event)
    return event


def run_vertical_slice(
    objective: str,
    owner: Role | str = Role.CHIEF,
    *,
    data_dir: Path | str | None = None,
    human_authority: HumanAuthorityPort | None = None,
    approval: Approval | None = None,
    context: HumanAuthorityContext | None = None,
) -> VerticalSliceResult:
    """串起 2A–2D 完整閉環（end-to-end vertical slice）。

    流程（2A §4 state machine + §6 Handoff + 2C approval + 2D durable store）：
    1. create_work → proposed
    2. Human approval #1：validate_transition(proposed → approved) + 記 state_transition
    3. assign(developer) → decision_made
    4. 自主 transition：approved → assigned → in_progress
    5. Specialist handoff：consume_handoff(artifact) → artifact_produced
    6. 自主 transition：in_progress → awaiting_review → awaiting_approval
    7. synthesize → fold current WorkObject
    8. Human approval #2：validate_transition(awaiting_approval → done) + 記 state_transition
    9. Privileged action：grant → is_authorized → consume
    10. Evidence → durable store（evidence_produced + AuthorityStore）

    Human authority 由注入的 `human_authority` port 驗證（deny-by-default）。
    `approval` / `context` 未提供時用 demonstration fixture（資料，非 proof）。
    """
    # ── 1. 組件（只走既有模組） ──
    kernel = WorkKernel(data_dir=data_dir)
    orchestrator = WorkflowOrchestrator(kernel=kernel)
    authority_store = AuthorityStore(data_dir=data_dir)
    authority = AuthorityManager(
        human_authority=human_authority,
        store=authority_store,
    )

    # ── 2. create_work → proposed ──
    work_id = orchestrator.create_work(objective, owner)

    # ── 3. Human approval #1：proposed → approved（2A §4） ──
    _record_transition(
        kernel, work_id, WorkState.PROPOSED, WorkState.APPROVED,
        role=Role.HUMAN.value, capability="approval",
    )

    # ── 4. assign(developer) → decision_made（Chief 自主指派，不 gate） ──
    orchestrator.assign(work_id, Role.DEVELOPER)

    # ── 5. 自主 transition：approved → assigned → in_progress ──
    _record_transition(
        kernel, work_id, WorkState.APPROVED, WorkState.ASSIGNED,
        role=Role.CHIEF.value, capability="work.assign",
    )
    _record_transition(
        kernel, work_id, WorkState.ASSIGNED, WorkState.IN_PROGRESS,
        role=Role.DEVELOPER.value, capability="isolated.write",
    )

    # ── 6. Specialist handoff：artifact（2A §6） ──
    orchestrator.consume_handoff(HandoffResult(
        work_id=work_id,
        role=Role.DEVELOPER.value,
        result_type=ResultType.ARTIFACT,
        artifact_refs=["sha256:artifact-1"],
        status=HandoffStatus.DONE,
    ))

    # ── 7. 自主 transition：in_progress → awaiting_review → awaiting_approval ──
    _record_transition(
        kernel, work_id, WorkState.IN_PROGRESS, WorkState.AWAITING_REVIEW,
        role=Role.DEVELOPER.value, capability="artifact.create",
    )
    _record_transition(
        kernel, work_id, WorkState.AWAITING_REVIEW, WorkState.AWAITING_APPROVAL,
        role=Role.AUDITOR.value, capability="review",
    )

    # ── 8. synthesize → fold current WorkObject（Chief 判斷下一步） ──
    orchestrator.synthesize(work_id)

    # ── 9. Human approval #2：awaiting_approval → done（2A §4） ──
    _record_transition(
        kernel, work_id, WorkState.AWAITING_APPROVAL, WorkState.DONE,
        role=Role.HUMAN.value, capability="approval",
    )

    # ── 10. Privileged action：grant → is_authorized → consume（2C §6） ──
    if approval is None:
        approval = Approval(
            work_id=work_id,
            capability="git.commit",
            requested_action={"repository": "soul-os-harness", "branch": "main"},
            action_scope=ActionScope.SINGLE_ACTION,
            grantee_role=Role.DEVELOPER.value,
            granted_by="human",
            expires_at=_future(hours=1),
        )
    else:
        # 確保 approval 錨定到本 vertical slice 的 work（2C §2：work_scope = work_id）
        approval = approval.model_copy(update={"work_id": work_id})

    if context is None:
        context = HumanAuthorityContext(
            identity="human",
            authority_token="trusted-token",
            issued_at=_utcnow(),
            expires_at=_future(hours=1),
        )

    grant = authority.grant(approval, context)
    action = AgentAction(
        grant_id=grant.grant_id,
        work_id=work_id,
        role=grant.grantee_role,
        capability=grant.capability,
        action=dict(approval.requested_action),
    )
    authorized = authority.is_authorized(action)
    authority.consume(grant.grant_id)
    post_consume_authorized = authority.is_authorized(action)

    # ── 11. Evidence → durable store（evidence → action，2C §6 provenance chain） ──
    evidence_event = orchestrator.consume_handoff(HandoffResult(
        work_id=work_id,
        role=Role.TESTER.value,
        result_type=ResultType.EVIDENCE,
        evidence_refs=[f"action:{action.action_id}"],
        status=HandoffStatus.DONE,
    ))

    # ── 12. 最終 fold（含 done + evidence） ──
    final_work = orchestrator.synthesize(work_id)

    return VerticalSliceResult(
        work_id=work_id,
        work=final_work,
        approval=approval,
        grant=grant,
        action=action,
        authorized=authorized,
        post_consume_authorized=post_consume_authorized,
        evidence_event=evidence_event,
    )
