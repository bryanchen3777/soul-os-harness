"""
tests/test_work_workflow.py
Soul OS — DSH Multi-Agent MVP-4：WorkflowOrchestrator（Chief 的 decision/assign/consume 閉環）。

驗收（對照 logs/DSH-MVP-4-WORK-ORDER.md）：
- create → assign → handoff → consume → synthesize 閉環跑通
- orchestrator 只走 kernel，不直接碰 store（single-writer 不破壞）
- fold 結果正確（state / objective / owner / decisions / artifacts / resume_state）
- 不 import 任何 DSH type

執行：pytest tests/test_work_workflow.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work import workflow as workflow_mod
from src.work.kernel import WorkKernel
from src.work.roles import Role
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    ResultType,
    WorkState,
)
from src.work.workflow import WorkflowOrchestrator


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)

# 只匹配「實際 import 陳述」，不匹配 docstring 中的文檔引用（如「不直接 import WorkStore」）。
_WORKSTORE_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*WorkStore", re.MULTILINE
)


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# 1. create_work（proposed，記 state_transition）
# ─────────────────────────────────────────────

def test_create_work_records_proposed_state(tmp_path):
    """create_work → synthesize 回 proposed state + objective + owner。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")

    work = orch.synthesize(work_id)
    assert work.work_id == work_id
    assert work.state == WorkState.PROPOSED
    assert work.objective == "build X"
    assert work.owner == "chief"
    assert work.assigned_agents == []
    assert work.dependencies == []
    assert work.provenance.role == "chief"
    assert work.provenance.capability == "orchestration"


def test_create_work_generates_unique_ids(tmp_path):
    """兩次 create_work 產生不同 work_id，且互不污染。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    id_a = orch.create_work("build A", "chief")
    id_b = orch.create_work("build B", "chief")

    assert id_a != id_b
    assert orch.synthesize(id_a).objective == "build A"
    assert orch.synthesize(id_b).objective == "build B"


def test_create_work_accepts_role_enum_owner(tmp_path):
    """owner 可傳 Role enum，會正規化為 role value。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", Role.CHIEF)
    assert orch.synthesize(work_id).owner == "chief"


# ─────────────────────────────────────────────
# 2. assign（委派 kernel.assign，記 decision_made）
# ─────────────────────────────────────────────

def test_assign_delegates_to_kernel(tmp_path):
    """assign → 記 decision_made（Chief 的自主指派決策）。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")

    event = orch.assign(work_id, "developer")
    assert event.event_type.value == "decision_made"

    work = orch.synthesize(work_id)
    assert work.decisions == [{"assign_role": "developer"}]


def test_assign_rejects_unknown_role(tmp_path):
    """assign 傳入未列於 2A §5.1 的 role → 拋 ValueError。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")
    with pytest.raises(ValueError):
        orch.assign(work_id, "not_a_role")


# ─────────────────────────────────────────────
# 3. consume_handoff（委派 kernel.record_handoff）
# ─────────────────────────────────────────────

def test_consume_handoff_artifact(tmp_path):
    """consume_handoff(artifact) → artifact_produced + resume_state.last_artifact_refs。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")

    handoff = HandoffResult(
        work_id=work_id, role="researcher", result_type=ResultType.ARTIFACT,
        artifact_refs=["sha256:abc"], status=HandoffStatus.DONE,
    )
    event = orch.consume_handoff(handoff)
    assert event.event_type.value == "artifact_produced"

    work = orch.synthesize(work_id)
    assert work.artifacts == [{"refs": ["sha256:abc"]}]
    assert work.resume_state.last_artifact_refs == ["sha256:abc"]


def test_consume_handoff_evidence(tmp_path):
    """consume_handoff(evidence) → evidence_produced。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")

    handoff = HandoffResult(
        work_id=work_id, role="tester", result_type=ResultType.EVIDENCE,
        evidence_refs=["sha256:def"], status=HandoffStatus.DONE,
    )
    orch.consume_handoff(handoff)

    work = orch.synthesize(work_id)
    assert work.evidence == [{"refs": ["sha256:def"]}]


def test_consume_handoff_decision(tmp_path):
    """consume_handoff(decision) → decision_made。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    work_id = orch.create_work("build X", "chief")

    handoff = HandoffResult(
        work_id=work_id, role="developer", result_type=ResultType.DECISION,
        decision={"choice": "use sqlite"}, status=HandoffStatus.DONE,
    )
    orch.consume_handoff(handoff)

    work = orch.synthesize(work_id)
    assert work.decisions == [{"choice": "use sqlite"}]


# ─────────────────────────────────────────────
# 4. 閉環（create → assign → handoff → consume → synthesize）
# ─────────────────────────────────────────────

def test_full_loop(tmp_path):
    """create → assign → consume(artifact) → synthesize 閉環跑通，fold 結果正確。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)

    work_id = orch.create_work("build feature X", "chief")
    orch.assign(work_id, "developer")
    orch.consume_handoff(HandoffResult(
        work_id=work_id, role="researcher", result_type=ResultType.ARTIFACT,
        artifact_refs=["sha256:abc"], status=HandoffStatus.DONE,
    ))

    work = orch.synthesize(work_id)
    assert work.work_id == work_id
    assert work.state == WorkState.PROPOSED  # assign/consume 不驅動 state machine
    assert work.objective == "build feature X"
    assert work.owner == "chief"
    assert work.decisions == [{"assign_role": "developer"}]
    assert work.artifacts == [{"refs": ["sha256:abc"]}]
    assert work.resume_state.last_artifact_refs == ["sha256:abc"]


def test_synthesize_unknown_work_raises(tmp_path):
    """synthesize 找不到 work_id → 拋 WorkNotFoundError（KeyError）。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    with pytest.raises(KeyError):
        orch.synthesize("no-such-work")


# ─────────────────────────────────────────────
# 5. single-writer 不破壞（orchestrator 只走 kernel）
# ─────────────────────────────────────────────

def test_orchestrator_does_not_import_dsh():
    """workflow.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(workflow_mod)), (
        "workflow.py 不得 import DSH / Cordis type"
    )


def test_orchestrator_does_not_import_store():
    """orchestrator 只走 kernel，不直接 import / 使用 WorkStore。"""
    src = _source_of(workflow_mod)
    assert not _WORKSTORE_IMPORT_RE.search(src), (
        "workflow.py 不得 import WorkStore（只走 kernel）"
    )


def test_orchestrator_holds_kernel_not_store(tmp_path):
    """orchestrator 持有 WorkKernel，不持有 store 直接引用。"""
    orch = WorkflowOrchestrator(data_dir=tmp_path)
    assert isinstance(orch._kernel, WorkKernel)
    assert not hasattr(orch, "_store")


def test_orchestrator_accepts_injected_kernel(tmp_path):
    """orchestrator 可注入既有 kernel（只依賴 kernel 介面，不自行建 store）。"""
    kernel = WorkKernel(data_dir=tmp_path)
    orch = WorkflowOrchestrator(kernel=kernel)
    assert orch._kernel is kernel

    work_id = orch.create_work("build X", "chief")
    assert kernel.fold(work_id).state == WorkState.PROPOSED
