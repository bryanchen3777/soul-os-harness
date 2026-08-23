"""
src/work_adapter/execution.py
DSH-P0-1 — Work execution path（Python WorkKernel → BridgeMessage → Adapter →
HandoffResult → consume_handoff → WorkEvent）。
DSH P1-C1 — 真 DSH execution path（C1.5/C1.6）：identity binding 驗證 + claim 重建。

把 Domain Core 的既有模組串成 execution path；本模組**不改 Domain Core**，
只做 transport/invoke wiring：

1. `orchestrator.synthesize(work_id)` → current WorkObject（fold durable log）
2. `build_execution_request(work, role, capability)` → BridgeMessage(request)
3. `WorkExecutionBridge.execute(message)` → HandoffResult（mock/scripted）
4. `orchestrator.consume_handoff(handoff)` → WorkEvent（durable log，
   dedup by idempotency key，effectively-once）

無 durable write authority：本模組的 durable write 一律經 Domain Core 的
orchestrator/kernel；bridge / adapter 只 transport，不寫 durable store。

── 真 DSH path（`execute_work_dsh`，P1-C1）──
bridge.execute_dsh 回報 session log 絕對路徑 → Domain Core
`read_execution_evidence(log_path)` 自行開檔讀 header + final message →
`verify_role_binding(role, evidence)` → 重建 HandoffResult claim → 三層
cross-check → consume_handoff：

1. **identity**（誰跑的）：header.cwd → role（A1，Domain Core 開檔）。
2. **capability**（能不能產）：result_type → capability（P1-C0，kernel
   enforcement）。
3. **content**（產了什麼）：claimed ref → 存在性 + hash（P1-B D10）。

HandoffResult.role 的 canonical 值 = `role_for(evidence.cwd)`（binding 決定，
process 事實）；final_message 解析出的 role 是 **claim**——claim.role 必須 ==
binding role，否則 fail-closed（B2：identity 權威 = header.cwd→role）。
重建 claim 失敗 / 非結構化 → fail-closed（P1-C D5）。

Anchor 驗證（DSH failure isolation）：HandoffResult.work_id / role 必須與
request 一致（adapter 不可竄改），否則拋 BridgeExecutionError——mis-routed
handoff 不得寫進另一 work 的 durable log（不污染 durable truth）。
"""
from __future__ import annotations

import json
import re

from src.work.artifact_store import ArtifactStore
from src.work.bridge import BridgeMessage, BridgeMessageType
from src.work.execution_evidence import (
    ExecutionEvidence,
    ExecutionEvidenceError,
    RoleCwdRegistry,
    read_execution_evidence,
)
from src.work.kernel import result_type_for_capability
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    WorkEvent,
    WorkObject,
)
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
    """完整 execution path（mock/scripted 面）：synthesize → request → bridge → handoff → WorkEvent。

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


# ─────────────────────────────────────────────
# P1-C1：真 DSH execution path（identity verification）
# ─────────────────────────────────────────────

def execute_work_dsh(
    orchestrator: WorkflowOrchestrator,
    work_id: str,
    role: str,
    capability: str,
    bridge: WorkExecutionBridge,
    registry: RoleCwdRegistry,
    artifact_store: ArtifactStore,
    *,
    causation: str | None = None,
    reference: str | None = None,
) -> tuple[BridgeMessage, HandoffResult, WorkEvent, ExecutionEvidence]:
    """真 DSH execution path：identity → claim → content → durable（P1-C1 C1.5/C1.6）。

    Flow:
    1. synthesize → build_execution_request。
    2. `bridge.execute_dsh(message, role_cwd)`（role_cwd 由 registry 決定，
       binding 錨點）→ 回報 session log 絕對路徑（adapter 不轉述 header）。
    3. Domain Core 自行開檔 `read_execution_evidence(log_path)`（fail-closed）。
    4. identity 層：`registry.verify_role_binding(role, evidence)` 失敗
       → fail-closed（借殼攻擊：header.cwd 對不上 handoff.role → DENY）。
    5. claim 層：從 evidence.final_message 重建 HandoffResult claim（非結構化
       → fail-closed，P1-C D5）；claim.work_id == work_id；
       claim.role == canonical binding role（`role_for(evidence.cwd)`）
       == request role（B2：identity 權威 = header.cwd→role）。
    6. capability 層：result_type ↔ capability（M2）+ kernel.record_handoff
       role↔capability enforcement（P1-C0）。
    7. content 層（P1-B D10）：ARTIFACT 的 claimed refs 逐一
       `artifact_store.verify_artifact_ref`（存在性 + hash）；任一不符
       → fail-closed，不寫 durable。
    8. blocked / needs_input（M1）：不觸發 artifact capability / content gate，
       記錄 state_transition(current → blocked)，無產出。

    Returns:
        (message, claim, event, evidence)。

    Raises:
        BridgeExecutionError: 任何一層驗證失敗 / DSH 失敗 / log 讀取失敗——
            此時**不寫任何 durable state**（fail-closed）。
        CapabilityNotAuthorizedError: P1-C0 capability gate（Domain Core
            kernel 的授權錯誤，依既有語義原樣浮出）。
    """
    work = orchestrator.synthesize(work_id)
    message = build_execution_request(
        work, role, capability, causation=causation, reference=reference
    )

    # C1.2：role→cwd 由 execution path 註冊；未註冊 → 無法建立 binding 錨點
    role_cwd = registry.cwd_for(role)
    if role_cwd is None:
        raise BridgeExecutionError(
            f"role={role!r} is not registered in RoleCwdRegistry; "
            f"cannot establish identity binding (cwd anchor)"
        )

    log_path = bridge.execute_dsh(message, role_cwd)
    try:
        evidence = read_execution_evidence(log_path)
    except ExecutionEvidenceError as exc:
        raise BridgeExecutionError(
            f"failed to read DSH session evidence from {log_path}: {exc}"
        ) from exc

    # C1.5：identity 層（A1）——Domain Core 自行開檔讀 header.cwd→role
    if not registry.verify_role_binding(role, evidence):
        raise BridgeExecutionError(
            f"identity binding failed: session cwd={evidence.cwd!r} maps to "
            f"role={registry.role_for(evidence.cwd)!r}, not requested role={role!r} "
            f"(malicious role claim rejected — header.cwd is the process fact)"
        )

    # C1.6：claim 重建（final_message 的 role/result_type/refs 是 LLM 的 claim）
    claim = rebuild_handoff_claim(evidence.final_message)

    canonical_role = registry.role_for(evidence.cwd)
    if claim.work_id != work_id:
        raise BridgeExecutionError(
            f"mis-routed handoff claim: work_id={claim.work_id!r} "
            f"does not match request work_id={work_id!r}"
        )
    if canonical_role is None or claim.role != canonical_role:
        raise BridgeExecutionError(
            f"claim.role={claim.role!r} does not match identity binding "
            f"role={canonical_role!r} (identity authority = header.cwd→role, B2)"
        )
    if claim.role != role:
        raise BridgeExecutionError(
            f"mis-routed handoff claim: role={claim.role!r} does not match "
            f"request role={role!r}"
        )

    # M2（P1-Preflight）：result_type ↔ capability 對齊
    expected = result_type_for_capability(capability)
    if claim.result_type != expected:
        raise BridgeExecutionError(
            f"result_type mismatch: claim={claim.result_type.value!r} does not "
            f"match request capability={capability!r} (expected {expected.value!r})"
        )

    # M1：blocked / needs_input → 無產出，不觸發 artifact capability / content gate
    if claim.status in (HandoffStatus.BLOCKED, HandoffStatus.NEEDS_INPUT):
        event = orchestrator.consume_handoff(claim)
        return message, claim, event, evidence

    # C1.7 content 層（P1-B D10）：claimed artifact refs → 存在性 + hash
    # （P1-C1 第一期只做 artifact；evidence 的 claim→verify 列為 P1-C
    # requirement，不在第一期——P1-B D8 註記）。
    if claim.result_type.value == "artifact":
        for ref in claim.artifact_refs:
            if not artifact_store.verify_artifact_ref(ref):
                raise BridgeExecutionError(
                    f"claimed artifact ref {ref!r} failed content verification "
                    f"(P1-B D10): ref missing from artifact store or hash mismatch"
                )

    # capability 層（P1-C0）：kernel.record_handoff 的 role↔capability
    # enforcement 在 durable write boundary 強制
    event = orchestrator.consume_handoff(claim)
    return message, claim, event, evidence


def rebuild_handoff_claim(final_message: str) -> HandoffResult:
    """從 session log 的 final assistant message 重建 HandoffResult **claim**（C1.6）。

    語法層容忍 LLM 輸出（code fence 剝離、`{...}` 區段抽取、bare key/value
    補引號）；契約層嚴格（pydantic `HandoffResult.model_validate`，enum 檢查）。
    完全非結構化 / contract 不符 → BridgeExecutionError（fail-closed，P1-C D5）。

    重建出的 role/result_type/refs 是 **claim**（LLM 宣稱），不是 canonical——
    canonical 由 identity binding（header.cwd→role）決定，呼叫端 cross-check。
    """
    text = final_message.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    data = _parse_claim_json(text)
    if data is None:
        raise BridgeExecutionError(
            "final assistant message is not a structured claim JSON "
            "(unstructured handoff rejected, P1-C D5)"
        )
    try:
        return HandoffResult.model_validate(data)
    except Exception as exc:  # pydantic ValidationError → contract 不符
        raise BridgeExecutionError(
            f"final assistant message is not a valid HandoffResult claim: {exc}"
        ) from exc


def _parse_claim_json(text: str) -> dict | None:
    """容錯解析 claim JSON：strict → `{...}` 區段 → bare key/value 修復。

    回傳 dict（非 dict / 全部失敗 → None，fail-closed 由呼叫端決定）。
    """
    candidates: list[str] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
        candidates.append(_repair_claim_json(text[start : end + 1]))
    else:
        candidates.append(_repair_claim_json(text))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _repair_claim_json(text: str) -> str:
    """保守修復 LLM 常見的 bare key / bare string value 引號省略。

    只補引號，不改語義；仍不是合法 JSON → 呼叫端 fail-closed。
    """
    # 1. bare keys：`{key:` → `{"key":`、`, key:` → `, "key":`
    text = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', text)
    # 2. bare string values（非數字/布林）：`: value[,}]` → `: "value"[,}]`
    text = re.sub(
        r'(:\s*)([A-Za-z_][A-Za-z0-9_.:\-]*)(\s*[,}])', r'\1"\2"\3', text
    )
    # 3. bare array elements（含 sha256:... 這種帶冒號的 token）
    text = re.sub(
        r'([\[,]\s*)([A-Za-z_][A-Za-z0-9_.:\-]*)(\s*[,\]])', r'\1"\2"\3', text
    )
    return text
