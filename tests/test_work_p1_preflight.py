"""
tests/test_work_p1_preflight.py
Soul OS — DSH P1-Preflight：MUST-FIX hardening（M1 / M2 / M3）驗收測試。

對照 logs/DSH-P1-PREFLIGHT-WORK-ORDER.md §2（MUST-FIX 實作）：
- M1：HandoffStatus → WorkState 語義（kernel.record_handoff）
    - status=blocked / needs_input → state_transition(current → blocked)，不記錄產出
    - fold 後 work.state == blocked、resume_state.current_phase == blocked_from
    - terminal state 再進 blocked → InvalidTransitionError
- M2：result_type ↔ capability anchor 對齊（execution.py）
    - capability=artifact.create 但 adapter 回 decision → BridgeExecutionError
    - capability=evidence.create 但 adapter 回 artifact → BridgeExecutionError
    - 對齊路徑（artifact）通過（正控制）
- M3：Bridge error contract 統一（bridge.py）
    - 非 UTF-8 stdout → BridgeExecutionError（不是 UnicodeDecodeError），不寫 durable

執行：pytest tests/test_work_p1_preflight.py
"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work.kernel import WorkKernel, result_type_for_capability
from src.work.roles import Role
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    Provenance,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkState,
)
from src.work.state_machine import InvalidTransitionError, validate_transition
from src.work.workflow import WorkflowOrchestrator
from src.work_adapter import BridgeExecutionError, WorkExecutionBridge
from src.work_adapter.execution import execute_work

NODE_AVAILABLE = shutil.which("node") is not None
needs_node = pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available")


# ─────────────────────────────────────────────
# Helpers（沿用 tests/test_work_adapter.py 既有模式）
# ─────────────────────────────────────────────

def _orchestrator(tmp_path) -> WorkflowOrchestrator:
    return WorkflowOrchestrator(kernel=WorkKernel(data_dir=tmp_path))


def _orchestrator_with_kernel(tmp_path):
    """回傳 (orchestrator, kernel)；kernel 供測試直接 append 合法 transition 鏈。"""
    kernel = WorkKernel(data_dir=tmp_path)
    return WorkflowOrchestrator(kernel=kernel), kernel


def _create_assigned_work(orch: WorkflowOrchestrator, objective: str = "build feature X") -> str:
    """create（proposed）→ assign(developer) → decision_made。回傳 work_id。"""
    work_id = orch.create_work(objective, Role.CHIEF)
    orch.assign(work_id, Role.DEVELOPER)
    return work_id


def _advance_chain(kernel: WorkKernel, work_id: str, states: list[WorkState]) -> None:
    """依序 append 合法 transition 鏈（states[0] → states[1] → ...）。"""
    for from_s, to_s in zip(states[:-1], states[1:]):
        kernel.append(WorkEvent(
            work_id=work_id,
            event_type=WorkEventType.STATE_TRANSITION,
            payload={"from": from_s.value, "to": to_s.value},
            provenance=Provenance(role=Role.CHIEF.value, capability="work.start"),
        ))


def _log_rows(data_dir) -> list[dict]:
    p = Path(data_dir) / "work_events.jsonl"
    if not p.exists():
        return []
    rows = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _artifact_rows(data_dir) -> list[dict]:
    return [
        r for r in _log_rows(data_dir)
        if r.get("event_type") == WorkEventType.ARTIFACT_PRODUCED.value
    ]


def _write_script(tmp_path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _mismatched_result_script(result_type: str) -> str:
    """adapter：echo request 的 work_id/role，但回傳指定的 result_type（故意與 capability 不符）。"""
    return (
        'const chunks = [];\n'
        'process.stdin.on("data", (c) => chunks.push(c));\n'
        'process.stdin.on("end", () => {\n'
        '  const msg = JSON.parse(Buffer.concat(chunks).toString("utf-8"));\n'
        '  const payload = msg.payload || {};\n'
        f'  const rt = "{result_type}";\n'
        '  process.stdout.write(JSON.stringify({\n'
        '    work_id: payload.work_id, role: payload.role, result_type: rt,\n'
        '    artifact_refs: rt === "artifact" ? ["mock:sha256:bad"] : [],\n'
        '    evidence_refs: rt === "evidence" ? ["mock:sha256:bad"] : [],\n'
        '    decision: rt === "decision" ? {mock: true} : {},\n'
        '    status: "done", resume_hint: {}\n'
        '  }) + "\\n");\n'
        '});\n'
    )


# ─────────────────────────────────────────────
# M1 — HandoffStatus → WorkState 語義（kernel.record_handoff）
# ─────────────────────────────────────────────

class TestM1HandoffStatusSemantics:
    def test_blocked_handoff_records_state_transition_no_output(self, tmp_path):
        """status=blocked → state_transition(current → blocked)，durable log 無產出 event。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)

        rows_before = len(_log_rows(tmp_path))
        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.DEVELOPER.value,
            result_type=ResultType.ARTIFACT,
            artifact_refs=["mock:blocked-ref"],
            status=HandoffStatus.BLOCKED,
            resume_hint={"reason": "missing dependency"},
        ))

        # state_transition event：payload 帶 from/to/status/resume_hint
        assert event.event_type == WorkEventType.STATE_TRANSITION
        assert event.payload["from"] == WorkState.PROPOSED.value
        assert event.payload["to"] == WorkState.BLOCKED.value
        assert event.payload["status"] == HandoffStatus.BLOCKED.value
        assert event.payload["resume_hint"] == {"reason": "missing dependency"}
        # provenance 保留 specialist role + result_type 對應 capability
        assert event.provenance.role == Role.DEVELOPER.value
        assert event.provenance.capability == "artifact.create"
        assert event.provenance.output_refs == []  # 無產出

        # durable log：blocked handoff 只 append 一筆 state_transition，無任何產出 event
        # （assign 的 decision_made 是 Chief 指派決策，非本 handoff 產出）
        rows = _log_rows(tmp_path)
        assert len(rows) == rows_before + 1
        assert rows[-1]["event_type"] == WorkEventType.STATE_TRANSITION.value
        assert rows[-1]["payload"]["to"] == WorkState.BLOCKED.value

        # fold：state == blocked；無 artifact / evidence 產出
        work = orch.synthesize(work_id)
        assert work.state == WorkState.BLOCKED
        assert work.artifacts == [] and work.evidence == []

    def test_needs_input_handoff_records_state_transition_with_resume_hint(self, tmp_path):
        """status=needs_input → 同上，payload.status=needs_input + resume_hint 保留。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)

        rows_before = len(_log_rows(tmp_path))
        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.TESTER.value,
            result_type=ResultType.EVIDENCE,
            status=HandoffStatus.NEEDS_INPUT,
            resume_hint={"missing": "test plan"},
        ))

        assert event.event_type == WorkEventType.STATE_TRANSITION
        assert event.payload["to"] == WorkState.BLOCKED.value
        assert event.payload["status"] == HandoffStatus.NEEDS_INPUT.value
        assert event.payload["resume_hint"] == {"missing": "test plan"}
        assert event.provenance.role == Role.TESTER.value
        assert event.provenance.capability == "evidence.create"

        # 只 append 一筆 state_transition，無產出 event
        rows = _log_rows(tmp_path)
        assert len(rows) == rows_before + 1
        assert rows[-1]["event_type"] == WorkEventType.STATE_TRANSITION.value

        work = orch.synthesize(work_id)
        assert work.state == WorkState.BLOCKED
        assert work.artifacts == [] and work.evidence == []

    def test_blocked_fold_resume_state_current_phase(self, tmp_path):
        """fold 後 work.state == blocked、resume_state.current_phase == in_progress（blocked_from）。"""
        orch, kernel = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)
        _advance_chain(kernel, work_id, [
            WorkState.PROPOSED,
            WorkState.APPROVED,
            WorkState.ASSIGNED,
            WorkState.IN_PROGRESS,
        ])
        assert orch.synthesize(work_id).state == WorkState.IN_PROGRESS

        orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.DEVELOPER.value,
            result_type=ResultType.ARTIFACT,
            status=HandoffStatus.BLOCKED,
        ))

        work = orch.synthesize(work_id)
        assert work.state == WorkState.BLOCKED
        # blocked_from：resume 目標 = 進入 blocked 前的 phase
        assert work.resume_state.current_phase == WorkState.IN_PROGRESS
        # resume 路徑合法（blocked → in_progress，2A §4 non-terminal）
        validate_transition(WorkState.BLOCKED, work.resume_state.current_phase)

    def test_terminal_state_then_blocked_raises(self, tmp_path):
        """work 已 done（terminal）再進 blocked → InvalidTransitionError；不寫任何新 event。"""
        orch, kernel = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)
        _advance_chain(kernel, work_id, [
            WorkState.PROPOSED,
            WorkState.APPROVED,
            WorkState.ASSIGNED,
            WorkState.IN_PROGRESS,
            WorkState.AWAITING_REVIEW,
            WorkState.AWAITING_APPROVAL,
            WorkState.DONE,
        ])
        assert orch.synthesize(work_id).state == WorkState.DONE

        rows_before = len(_log_rows(tmp_path))
        with pytest.raises(InvalidTransitionError):
            orch.consume_handoff(HandoffResult(
                work_id=work_id,
                role=Role.DEVELOPER.value,
                result_type=ResultType.ARTIFACT,
                status=HandoffStatus.BLOCKED,
            ))
        assert len(_log_rows(tmp_path)) == rows_before  # 無半寫入


# ─────────────────────────────────────────────
# M2 — result_type ↔ capability anchor（execution.py）
# ─────────────────────────────────────────────

class TestM2ResultTypeCapabilityAnchor:
    def test_result_type_for_capability_canonical_mapping(self):
        """Domain Core canonical 映射：evidence/decision/其餘 → 對應 ResultType。"""
        assert result_type_for_capability("evidence.create") == ResultType.EVIDENCE
        assert result_type_for_capability("decision") == ResultType.DECISION
        assert result_type_for_capability("artifact.create") == ResultType.ARTIFACT
        assert result_type_for_capability("git.commit") == ResultType.ARTIFACT

    @needs_node
    def test_artifact_request_decision_return_rejected(self, tmp_path):
        """capability=artifact.create 但 adapter 回 decision → BridgeExecutionError（fail closed）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        script = _write_script(tmp_path, "wrong-type.mjs", _mismatched_result_script("decision"))
        bridge = WorkExecutionBridge(adapter_script=script)

        rows_before = len(_log_rows(tmp_path))
        with pytest.raises(BridgeExecutionError, match="result_type mismatch"):
            execute_work(orch, work_id, Role.DEVELOPER.value, "artifact.create", bridge)
        assert len(_log_rows(tmp_path)) == rows_before  # 不寫 durable state
        assert _artifact_rows(tmp_path) == []

    @needs_node
    def test_evidence_request_artifact_return_rejected(self, tmp_path):
        """capability=evidence.create 但 adapter 回 artifact → BridgeExecutionError（fail closed）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        script = _write_script(tmp_path, "wrong-type.mjs", _mismatched_result_script("artifact"))
        bridge = WorkExecutionBridge(adapter_script=script)

        rows_before = len(_log_rows(tmp_path))
        with pytest.raises(BridgeExecutionError, match="result_type mismatch"):
            execute_work(orch, work_id, Role.TESTER.value, "evidence.create", bridge)
        assert len(_log_rows(tmp_path)) == rows_before

    @needs_node
    def test_aligned_artifact_path_passes(self, tmp_path):
        """對齊路徑（artifact.create → artifact）通過 M2 anchor（正控制）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        script = _write_script(tmp_path, "aligned.mjs", _mismatched_result_script("artifact"))
        bridge = WorkExecutionBridge(adapter_script=script)

        _, handoff, event = execute_work(
            orch, work_id, Role.DEVELOPER.value, "artifact.create", bridge
        )
        assert handoff.result_type == ResultType.ARTIFACT
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert len(_artifact_rows(tmp_path)) == 1


# ─────────────────────────────────────────────
# M3 — Bridge error contract 統一（bridge.py）
# ─────────────────────────────────────────────

@needs_node
class TestM3BridgeErrorContract:
    def test_non_utf8_stdout_raises_bridge_execution_error(self, tmp_path):
        """adapter 輸出非法 UTF-8 bytes → BridgeExecutionError（不是 UnicodeDecodeError）。"""
        script = _write_script(
            tmp_path, "bad-utf8.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.on("end", () => {\n'
            '  process.stdout.write(Buffer.from([0xff, 0xfe]));\n'
            '});\n',
        )
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        bridge = WorkExecutionBridge(adapter_script=script)

        rows_before = len(_log_rows(tmp_path))
        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.DEVELOPER.value, "artifact.create", bridge)
        assert len(_log_rows(tmp_path)) == rows_before  # fail closed，無半寫入
        assert _artifact_rows(tmp_path) == []
