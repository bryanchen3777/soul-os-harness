"""
tests/test_work_p1c0_enforcement.py
Soul OS — DSH P1-C0：Domain Core Capability Enforcement（kernel.record_handoff）。

對照 logs/DSH-P1-C0-WORK-ORDER.md（2A §5.1 frozen role→capability matrix 的
Domain Core enforcement）：
- record_handoff 在記錄產出前驗證 handoff.role 具備 result_type 對應 capability；
  Developer + artifact.create → CapabilityNotAuthorizedError，且不寫 durable
  （半寫入防護）
- Researcher + artifact.create → 通過（正控制；Owner 拍板 artifact.create 歸 Researcher）
- Tester / Auditor + evidence.create → 通過；Developer + evidence.create → deny
- decision 不 gate（2A §3.1：任何 agent 可記錄 decision，只記錄供 audit）
- blocked / needs_input 不 gate（M1 語義不變：blocked 無產出）

全部用直接 `orch.consume_handoff(HandoffResult(...))`，無需 bridge（Domain Core
enforcement 不依賴 DSH transport）。

執行：pytest tests/test_work_p1c0_enforcement.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work.kernel import WorkKernel
from src.work.roles import CapabilityNotAuthorizedError, Role
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    ResultType,
    WorkEventType,
    WorkState,
)
from src.work.workflow import WorkflowOrchestrator


# ─────────────────────────────────────────────
# Helpers（沿用 tests/test_work_p1_preflight.py 既有模式）
# ─────────────────────────────────────────────

def _orchestrator_with_kernel(tmp_path):
    """回傳 (orchestrator, kernel)；kernel 供測試直接 append 合法 transition 鏈。"""
    kernel = WorkKernel(data_dir=tmp_path)
    return WorkflowOrchestrator(kernel=kernel), kernel


def _create_assigned_work(orch: WorkflowOrchestrator, objective: str = "build feature X") -> str:
    """create（proposed）→ assign(developer) → decision_made。回傳 work_id。"""
    work_id = orch.create_work(objective, Role.CHIEF)
    orch.assign(work_id, Role.DEVELOPER)
    return work_id


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


# ─────────────────────────────────────────────
# P1-C0 — Domain Core capability enforcement（2A §5.1）
# ─────────────────────────────────────────────

class TestCapabilityEnforcement:
    def test_developer_artifact_create_denied_no_durable_write(self, tmp_path):
        """Developer + artifact.create → CapabilityNotAuthorizedError，不寫 durable（半寫入防護）。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(CapabilityNotAuthorizedError, match="artifact.create"):
            orch.consume_handoff(HandoffResult(
                work_id=work_id,
                role=Role.DEVELOPER.value,
                result_type=ResultType.ARTIFACT,
                artifact_refs=["mock:denied"],
                status=HandoffStatus.DONE,
            ))

        # deny 不寫 durable：rows 完全不變（無半寫入）
        assert len(_log_rows(tmp_path)) == rows_before
        assert orch.synthesize(work_id).artifacts == []

    def test_researcher_artifact_create_passes(self, tmp_path):
        """Researcher + artifact.create → 通過（正控制；artifact.create 歸 Researcher）。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)
        rows_before = len(_log_rows(tmp_path))

        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.RESEARCHER.value,
            result_type=ResultType.ARTIFACT,
            artifact_refs=["mock:allowed"],
            status=HandoffStatus.DONE,
        ))

        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert len(_log_rows(tmp_path)) == rows_before + 1
        work = orch.synthesize(work_id)
        assert work.artifacts == [{"refs": ["mock:allowed"]}]

    def test_tester_evidence_passes_developer_evidence_denied(self, tmp_path):
        """Tester + evidence.create → 通過；Developer + evidence.create → deny。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)

        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.TESTER.value,
            result_type=ResultType.EVIDENCE,
            evidence_refs=["mock:evidence-1"],
            status=HandoffStatus.DONE,
        ))
        assert event.event_type == WorkEventType.EVIDENCE_PRODUCED

        rows_before = len(_log_rows(tmp_path))
        with pytest.raises(CapabilityNotAuthorizedError, match="evidence.create"):
            orch.consume_handoff(HandoffResult(
                work_id=work_id,
                role=Role.DEVELOPER.value,
                result_type=ResultType.EVIDENCE,
                evidence_refs=["mock:evidence-2"],
                status=HandoffStatus.DONE,
            ))
        assert len(_log_rows(tmp_path)) == rows_before  # deny 不寫 durable

    def test_decision_not_gated_any_role(self, tmp_path):
        """decision 不 gate（2A §3.1）：Chief 與 Developer 都可記錄 decision。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)

        chief_event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.CHIEF.value,
            result_type=ResultType.DECISION,
            decision={"choice": "chief-decides"},
            status=HandoffStatus.DONE,
        ))
        assert chief_event.event_type == WorkEventType.DECISION_MADE

        dev_event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.DEVELOPER.value,
            result_type=ResultType.DECISION,
            decision={"choice": "dev-note"},
            status=HandoffStatus.DONE,
        ))
        assert dev_event.event_type == WorkEventType.DECISION_MADE

        work = orch.synthesize(work_id)
        assert work.decisions == [
            {"assign_role": "developer"},  # _create_assigned_work 的 assign decision
            {"choice": "chief-decides"},
            {"choice": "dev-note"},
        ]

    def test_auditor_evidence_create_passes(self, tmp_path):
        """Auditor + evidence.create → 通過（Auditor 有 evidence.create，2A §5.1）。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)

        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.AUDITOR.value,
            result_type=ResultType.EVIDENCE,
            evidence_refs=["mock:audit-evidence"],
            status=HandoffStatus.DONE,
        ))
        assert event.event_type == WorkEventType.EVIDENCE_PRODUCED
        work = orch.synthesize(work_id)
        assert work.evidence == [{"refs": ["mock:audit-evidence"]}]

    def test_blocked_handoff_not_gated(self, tmp_path):
        """M1 語義不變：blocked 無產出，Developer + artifact + BLOCKED 不觸發 enforcement。"""
        orch, _ = _orchestrator_with_kernel(tmp_path)
        work_id = _create_assigned_work(orch)
        rows_before = len(_log_rows(tmp_path))

        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.DEVELOPER.value,
            result_type=ResultType.ARTIFACT,
            artifact_refs=["mock:blocked"],
            status=HandoffStatus.BLOCKED,
            resume_hint={"reason": "missing dependency"},
        ))
        assert event.event_type == WorkEventType.STATE_TRANSITION
        assert len(_log_rows(tmp_path)) == rows_before + 1
        assert orch.synthesize(work_id).state == WorkState.BLOCKED

    def test_error_is_permission_error_subclass(self):
        """CapabilityNotAuthorizedError 繼承 PermissionError（與 NotDurableWriterError 同類授權錯誤）。"""
        assert issubclass(CapabilityNotAuthorizedError, PermissionError)
