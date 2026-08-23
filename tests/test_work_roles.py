"""
tests/test_work_roles.py
Soul OS — DSH Multi-Agent MVP-3：role model + single-writer enforcement。

驗收（對照 logs/DSH-MVP-3-WORK-ORDER.md + logs/DSH-MVP-3-R1-WORK-ORDER.md）：
- role → capability mapping 對齊 2A §5.1
- capability 名稱是 capability-neutral（非 DSH tool 名）
- single-writer 強制：非 kernel actor 寫入拋 NotDurableWriterError
- kernel 寫入正常（append / assign / record_handoff）
- roles.py / kernel.py 不 import 任何 DSH type
- R1：writer enforcement 是 STORE-LEVEL（WorkStore.append 檢查），
  三條 bypass（B/C/D）全部封死

執行：pytest tests/test_work_roles.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work import kernel as kernel_mod
from src.work import roles as roles_mod
from src.work.kernel import NotDurableWriterError, WorkKernel
from src.work.roles import (
    ROLE_CAPABILITIES,
    Role,
    capabilities_for,
    has_capability,
)
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    Provenance,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkState,
)
from src.work.store import WorkStore


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _prov(role: str = "chief", capability: str = "work.assign") -> Provenance:
    return Provenance(role=role, capability=capability)


def _transition(work_id: str, from_state: str | None, to_state: str, **seed) -> WorkEvent:
    payload: dict = {"from": from_state, "to": to_state}
    payload.update(seed)
    return WorkEvent(
        work_id=work_id,
        event_type=WorkEventType.STATE_TRANSITION,
        payload=payload,
        provenance=_prov(),
    )


# ─────────────────────────────────────────────
# 1. Role model（2A §5.1）
# ─────────────────────────────────────────────

def test_role_enum_values():
    """Role enum 只有 2A §5.1 的 6 個 role。"""
    assert {r.value for r in Role} == {
        "researcher", "developer", "tester", "auditor", "chief", "human",
    }


def test_role_capabilities_align_with_2a_s5():
    """ROLE_CAPABILITIES 對齊 2A §5.1 矩陣（唯一 authoritative source）。"""
    expected = {
        Role.RESEARCHER: frozenset({"workspace.read", "research", "artifact.create"}),
        Role.DEVELOPER: frozenset({"workspace.read", "isolated.write", "test.execute", "git.branch"}),
        Role.TESTER: frozenset({"workspace.read", "test.execute", "evidence.create"}),
        Role.AUDITOR: frozenset({"workspace.read", "review", "evidence.create"}),
        Role.CHIEF: frozenset({"orchestration", "decision", "work.assign"}),
        Role.HUMAN: frozenset({"approval", "privileged actions"}),
    }
    assert ROLE_CAPABILITIES == expected


def test_capabilities_are_capability_neutral():
    """capability 名稱是 capability-neutral（非 DSH tool 名）。"""
    all_caps = {c for caps in ROLE_CAPABILITIES.values() for c in caps}
    assert all_caps
    for cap in all_caps:
        assert "dsh" not in cap.lower(), f"capability {cap!r} 不得是 DSH tool 名"


def test_capabilities_for_and_has_capability():
    """capabilities_for / has_capability 依 2A §5.1 回傳。"""
    assert capabilities_for(Role.CHIEF) == frozenset({"orchestration", "decision", "work.assign"})
    assert capabilities_for("developer") == frozenset(
        {"workspace.read", "isolated.write", "test.execute", "git.branch"}
    )

    assert has_capability(Role.DEVELOPER, "isolated.write")
    assert has_capability(Role.TESTER, "evidence.create")
    assert has_capability(Role.HUMAN, "approval")
    assert not has_capability(Role.DEVELOPER, "approval")
    assert not has_capability(Role.TESTER, "work.assign")


def test_roles_do_not_import_dsh():
    """roles.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(roles_mod)), (
        "roles.py 不得 import DSH / Cordis type"
    )


# ─────────────────────────────────────────────
# 2. single-writer enforcement（kernel）
# ─────────────────────────────────────────────

def test_kernel_does_not_import_dsh():
    """kernel.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(kernel_mod)), (
        "kernel.py 不得 import DSH / Cordis type"
    )


def test_kernel_can_write(tmp_path):
    """kernel 是唯一 writer：append 正常寫入，fold 讀回。"""
    kernel = WorkKernel(data_dir=tmp_path)
    work_id = "work-1"

    kernel.append(_transition(work_id, None, "proposed", objective="build X", owner="chief"))
    kernel.append(_transition(work_id, "proposed", "approved"))

    work = kernel.fold(work_id)
    assert work.work_id == work_id
    assert work.state == WorkState.APPROVED
    assert work.objective == "build X"
    assert work.owner == "chief"


def test_non_kernel_actor_cannot_write(tmp_path):
    """非 kernel 的 actor 呼叫寫入 → 拋 NotDurableWriterError。"""
    kernel = WorkKernel(data_dir=tmp_path)
    event = _transition("work-1", None, "proposed", objective="obj", owner="chief")

    non_writers = [
        "chief", "developer", "tester", "auditor", "human", "researcher",
        "dsh_adapter", "dsh_session", "dsh_runtime",
    ]
    for actor in non_writers:
        with pytest.raises(NotDurableWriterError):
            kernel.append(event, actor=actor)

    # 非 kernel actor 寫入被拒後，store 沒有被污染
    with pytest.raises(KeyError):
        kernel.fold("work-1")


def test_kernel_assign_records_decision(tmp_path):
    """Chief 透過 kernel assign role → 記錄 decision_made event。"""
    kernel = WorkKernel(data_dir=tmp_path)
    work_id = "work-1"

    kernel.append(_transition(work_id, None, "proposed", objective="obj", owner="chief"))
    kernel.assign(work_id, "developer")

    work = kernel.fold(work_id)
    assert work.decisions == [{"assign_role": "developer"}]


def test_kernel_assign_rejects_unknown_role(tmp_path):
    """assign 傳入未列於 2A §5.1 的 role → 拋 ValueError。"""
    kernel = WorkKernel(data_dir=tmp_path)
    with pytest.raises(ValueError):
        kernel.assign("work-1", "not_a_role")


def test_kernel_record_handoff_artifact(tmp_path):
    """Specialist 透過 kernel record_handoff(artifact) → artifact_produced。"""
    kernel = WorkKernel(data_dir=tmp_path)
    work_id = "work-1"

    kernel.append(_transition(work_id, None, "proposed", objective="obj", owner="chief"))
    handoff = HandoffResult(
        work_id=work_id, role="researcher", result_type=ResultType.ARTIFACT,
        artifact_refs=["sha256:abc"], status=HandoffStatus.DONE,
    )
    kernel.record_handoff(handoff)

    work = kernel.fold(work_id)
    assert work.artifacts == [{"refs": ["sha256:abc"]}]
    assert work.resume_state.last_artifact_refs == ["sha256:abc"]


def test_kernel_record_handoff_evidence(tmp_path):
    """Specialist 透過 kernel record_handoff(evidence) → evidence_produced。"""
    kernel = WorkKernel(data_dir=tmp_path)
    work_id = "work-1"

    kernel.append(_transition(work_id, None, "proposed", objective="obj", owner="chief"))
    handoff = HandoffResult(
        work_id=work_id, role="tester", result_type=ResultType.EVIDENCE,
        evidence_refs=["sha256:def"], status=HandoffStatus.DONE,
    )
    kernel.record_handoff(handoff)

    work = kernel.fold(work_id)
    assert work.evidence == [{"refs": ["sha256:def"]}]


def test_kernel_record_handoff_decision(tmp_path):
    """Specialist 透過 kernel record_handoff(decision) → decision_made。"""
    kernel = WorkKernel(data_dir=tmp_path)
    work_id = "work-1"

    kernel.append(_transition(work_id, None, "proposed", objective="obj", owner="chief"))
    handoff = HandoffResult(
        work_id=work_id, role="developer", result_type=ResultType.DECISION,
        decision={"choice": "use sqlite"}, status=HandoffStatus.DONE,
    )
    kernel.record_handoff(handoff)

    work = kernel.fold(work_id)
    assert work.decisions == [{"choice": "use sqlite"}]


# ─────────────────────────────────────────────
# 3. single-writer enforcement（STORE-LEVEL，bypass 封閉）
# ─────────────────────────────────────────────

def test_store_append_requires_explicit_actor(tmp_path):
    """WorkStore.append 的 actor 無 default：缺參數即失敗（TypeError）。"""
    store = WorkStore(data_dir=tmp_path)
    event = _transition("work-1", None, "proposed", objective="obj", owner="chief")
    with pytest.raises(TypeError):
        store.append(event)  # 缺 actor → TypeError（無 default）


def test_bypass_b_store_append_rejects_non_writer(tmp_path):
    """Bypass B：WorkStore().append(event, "developer") → NotDurableWriterError。"""
    store = WorkStore(data_dir=tmp_path)
    event = _transition("work-1", None, "proposed", objective="obj", owner="chief")
    with pytest.raises(NotDurableWriterError):
        store.append(event, "developer")


def test_bypass_c_kernel_store_append_rejects_non_writer(tmp_path):
    """Bypass C：kernel._store.append(event, "developer") → NotDurableWriterError。"""
    kernel = WorkKernel(data_dir=tmp_path)
    event = _transition("work-1", None, "proposed", objective="obj", owner="chief")
    with pytest.raises(NotDurableWriterError):
        kernel._store.append(event, "developer")


def test_bypass_d_direct_import_store_append_rejects_non_writer(tmp_path):
    """Bypass D：from src.work.store import WorkStore 也不能 bypass。"""
    from src.work.store import WorkStore as DirectWorkStore
    store = DirectWorkStore(data_dir=tmp_path)
    event = _transition("work-1", None, "proposed", objective="obj", owner="chief")
    with pytest.raises(NotDurableWriterError):
        store.append(event, "developer")


def test_store_append_accepts_kernel_writer(tmp_path):
    """WorkStore.append(event, "kernel") 正常寫入（store-level 授權通過）。"""
    store = WorkStore(data_dir=tmp_path)
    work_id = "work-1"
    store.append(_transition(work_id, None, "proposed", objective="obj", owner="chief"), "kernel")
    work = store.fold(work_id)
    assert work.state == WorkState.PROPOSED
    assert work.objective == "obj"
