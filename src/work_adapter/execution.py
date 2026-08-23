"""
src/work_adapter/execution.py
DSH-P0-1 — Work execution path（Python WorkKernel → BridgeMessage → Adapter →
HandoffResult → consume_handoff → WorkEvent）。

把 Domain Core 的既有模組串成 execution path；本模組**不改 Domain Core**，
只做 transport/invoke wiring（工單做法 #3）：

1. `orchestrator.synthesize(work_id)` → current WorkObject（fold durable log）
2. `build_execution_request(work, role, capability)` → BridgeMessage(request)
3. `WorkExecutionBridge.execute(message)` → HandoffResult（DSH execution，mock）
4. `orchestrator.consume_handoff(handoff)` → WorkEvent（durable log，
   dedup by idempotency key，effectively-once）

無 durable write authority：本模組的 durable write 一律經 Domain Core 的
orchestrator/kernel；bridge / adapter 只 transport，不寫 durable store。

Anchor 驗證（DSH failure isolation）：HandoffResult.work_id / role 必須與
request 一致（adapter 不可竄改），否則拋 BridgeExecutionError——mis-routed
handoff 不得寫進另一 work 的 durable log（不污染 durable truth）。
"""
from __future__ import annotations

from src.work.bridge import BridgeMessage, BridgeMessageType
from src.work.kernel import result_type_for_capability
from src.work.schema import HandoffResult, WorkEvent, WorkObject
from src.work.workflow import WorkflowOrchestrator, derive_execution_shape

from .bridge import BridgeExecutionError, WorkExecutionBridge


def build_execution_request(
    work: WorkObject,
    role: str,
    capability: str,
    *,
    causation: str | None = None,
    reference: str | None = None,
) -> BridgeMessage:
    """把 current WorkObject 轉成 BridgeMessage(request)（migration plan §3.2）。

    payload 帶 minimal execution request：work_id / objective / role /
    capability / execution_shape / resume_state（最小重建狀態）。execution_shape
    由 Domain Core 推導（Soul 決定），adapter 只 translate，不自決（P1-A §3.4）。
    causation 是 Soul causal truth（event_id），reference 是外部 reference
    （如 DSH sessionId），兩者都不是 Soul identity（migration plan §3.2）。
    """
    return BridgeMessage(
        message_type=BridgeMessageType.REQUEST,
        actor=role,  # 執行者 role（specialist），不是 DSH id
        source="soul_kernel",
        causation=causation,
        reference=reference,
        payload={
            "work_id": work.work_id,
            "objective": work.objective,
            "role": role,
            "capability": capability,
            "execution_shape": derive_execution_shape(work).value,  # ← P1-A 新增
            "resume_state": work.resume_state.model_dump(mode="json"),
        },
    )


def execute_work(
    orchestrator: WorkflowOrchestrator,
    work_id: str,
    role: str,
    capability: str,
    bridge: WorkExecutionBridge,
    *,
    causation: str | None = None,
    reference: str | None = None,
) -> tuple[BridgeMessage, HandoffResult, WorkEvent]:
    """完整 execution path：synthesize → request → bridge → handoff → WorkEvent。

    Returns:
        (message, handoff, event)：request message、adapter 回傳的 HandoffResult、
        consume_handoff 產生的 WorkEvent（durable log；duplicate handoff →
        Domain Core dedup 回傳既有 event，不重複 append）。

    Raises:
        BridgeExecutionError: DSH 失敗（crash / timeout / malformed /
            mis-routed handoff）。此時**不寫任何 durable state**。
    """
    work = orchestrator.synthesize(work_id)
    message = build_execution_request(
        work, role, capability, causation=causation, reference=reference
    )
    handoff = bridge.execute(message)

    # Anchor 驗證：adapter 不可竄改 work_id / role（mis-routed handoff
    # 不得寫進另一 work 的 durable log）
    if handoff.work_id != work_id:
        raise BridgeExecutionError(
            f"mis-routed handoff: work_id={handoff.work_id!r} "
            f"does not match request work_id={work_id!r}"
        )
    if handoff.role != role:
        raise BridgeExecutionError(
            f"mis-routed handoff: role={handoff.role!r} does not match request role={role!r}"
        )

    # Anchor 驗證（M2，P1-Preflight）：result_type 必須與 request capability 對齊
    # （canonical 映射在 Domain Core result_type_for_capability）。adapter 回傳與
    # capability 不符的 result_type → event 類型 + provenance capability 會錯記
    # （artifact.create 卻寫 decision_made），fail closed，不寫 durable state。
    expected = result_type_for_capability(capability)
    if handoff.result_type != expected:
        raise BridgeExecutionError(
            f"result_type mismatch: handoff={handoff.result_type.value!r} does not "
            f"match request capability={capability!r} (expected {expected.value!r})"
        )

    event = orchestrator.consume_handoff(handoff)
    return message, handoff, event
