"""
tests/test_work_contract.py
Soul OS — DSH Multi-Agent MVP-1：Work Contract domain schema + state machine + durable store。

驗收（對照 logs/DSH-MVP-1-WORK-ORDER.md）：
- schema 序列化 round-trip
- WorkObject 序列化後不含任何 DSH type/id 字串
- state machine 拒絕未列於 2A §4 的 state 與 transition
- store 是 append-only（無 update/delete API）
- store append + fold、corrupt row 跳過、resume_state 最小重建

執行：pytest tests/test_work_contract.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from pydantic import ValidationError

from src.work import (
    HandoffResult,
    HandoffStatus,
    InvalidTransitionError,
    Provenance,
    ResumeState,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkObject,
    WorkState,
    WorkStore,
    can_transition,
    requires_human_approval,
    validate_transition,
)
from src.work.bridge import derive_idempotency_key


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _prov(role: str = "chief", capability: str = "work.assign",
          output_refs: list[str] | None = None) -> Provenance:
    return Provenance(role=role, capability=capability, output_refs=output_refs or [])


def _transition(work_id: str, from_state: str | None, to_state: str,
                provenance: Provenance | None = None, **seed) -> WorkEvent:
    payload: dict = {"from": from_state, "to": to_state}
    payload.update(seed)
    return WorkEvent(
        work_id=work_id,
        event_type=WorkEventType.STATE_TRANSITION,
        payload=payload,
        provenance=provenance or _prov(),
    )


def _append(store, event):
    """append 一筆 event（actor=kernel，single-writer enforcement）。"""
    store.append(event, "kernel")


def _make_work() -> WorkObject:
    return WorkObject(
        objective="build feature X",
        state=WorkState.IN_PROGRESS,
        owner="chief",
        assigned_agents=["developer", "tester"],
        artifacts=[{"ref": "sha256:abc", "path": "src/x.py"}],
        evidence=[{"ref": "sha256:def", "conclusion": "passed"}],
        decisions=[{"choice": "use sqlite"}],
        approvals=[{"by": "human", "action": "approve"}],
        dependencies=["work-0"],
        provenance=_prov(),
        resume_state=ResumeState(current_phase=WorkState.IN_PROGRESS),
    )


# ─────────────────────────────────────────────
# 1. Schema
# ─────────────────────────────────────────────

def test_work_state_enum_is_authoritative():
    """WorkState 只有 2A §4 的 10 個值，不得有 reviewing / failed / waiting。"""
    values = {s.value for s in WorkState}
    assert values == {
        "proposed", "approved", "assigned", "in_progress",
        "awaiting_review", "awaiting_approval", "done",
        "rejected", "cancelled", "blocked",
    }
    for illegal in ("reviewing", "failed", "waiting"):
        assert illegal not in values


def test_work_state_rejects_illegal_state():
    """未列於 2A §4 的 state 會被 pydantic 拒絕。"""
    with pytest.raises(ValidationError):
        WorkObject(
            objective="x", state="reviewing", owner="chief",
            provenance=_prov(), resume_state=ResumeState(current_phase=WorkState.PROPOSED),
        )


def test_work_object_roundtrip():
    """WorkObject 序列化 → 反序列化 round-trip。"""
    work = _make_work()
    data = json.loads(work.model_dump_json())
    work2 = WorkObject(**data)

    assert work2.work_id == work.work_id
    assert work2.schema_version == "1.0"
    assert work2.objective == work.objective
    assert work2.state == WorkState.IN_PROGRESS
    assert work2.owner == "chief"
    assert work2.assigned_agents == ["developer", "tester"]
    assert work2.artifacts == work.artifacts
    assert work2.evidence == work.evidence
    assert work2.decisions == work.decisions
    assert work2.approvals == work.approvals
    assert work2.dependencies == ["work-0"]
    assert work2.provenance.role == "chief"
    assert work2.resume_state.current_phase == WorkState.IN_PROGRESS


def test_serialized_objects_contain_no_dsh_strings():
    """WorkObject / WorkEvent / HandoffResult 序列化後不含任何 DSH type/id 字串。"""
    work = _make_work()
    event = _transition("work-1", "proposed", "approved")
    handoff = HandoffResult(
        work_id="work-1", role="developer", result_type=ResultType.ARTIFACT,
        artifact_refs=["sha256:abc"], status=HandoffStatus.DONE,
    )
    for obj in (work, event, handoff):
        assert "dsh" not in obj.model_dump_json().lower()


def test_handoff_result_rejects_approval():
    """HandoffResult.result_type 不得有 approval（2A §6）。"""
    assert {r.value for r in ResultType} == {"artifact", "evidence", "decision"}
    with pytest.raises(ValidationError):
        HandoffResult(
            work_id="work-1", role="developer", result_type="approval",
            status=HandoffStatus.DONE,
        )


# ─────────────────────────────────────────────
# 2. State machine
# ─────────────────────────────────────────────

def test_valid_transitions():
    """2A §4 happy path 全部合法。"""
    happy_path = [
        (WorkState.PROPOSED, WorkState.APPROVED),
        (WorkState.APPROVED, WorkState.ASSIGNED),
        (WorkState.ASSIGNED, WorkState.IN_PROGRESS),
        (WorkState.IN_PROGRESS, WorkState.AWAITING_REVIEW),
        (WorkState.AWAITING_REVIEW, WorkState.AWAITING_APPROVAL),
        (WorkState.AWAITING_APPROVAL, WorkState.DONE),
    ]
    for f, t in happy_path:
        assert can_transition(f, t), f"{f} -> {t} 應合法"
        validate_transition(f, t)  # 不拋錯


def test_human_approval_transitions_exactly_two():
    """只有 proposed→approved 與 awaiting_approval→done 需要 Human approval。"""
    assert requires_human_approval(WorkState.PROPOSED, WorkState.APPROVED)
    assert requires_human_approval(WorkState.AWAITING_APPROVAL, WorkState.DONE)

    # 其餘 autonomous
    assert not requires_human_approval(WorkState.APPROVED, WorkState.ASSIGNED)
    assert not requires_human_approval(WorkState.IN_PROGRESS, WorkState.AWAITING_REVIEW)
    assert not requires_human_approval(WorkState.IN_PROGRESS, WorkState.BLOCKED)

    # 全表掃描：需要 Human approval 的 transition 恰好 2 個
    human = [
        (f, t)
        for f in WorkState
        for t in WorkState
        if requires_human_approval(f, t)
    ]
    assert human == [
        (WorkState.PROPOSED, WorkState.APPROVED),
        (WorkState.AWAITING_APPROVAL, WorkState.DONE),
    ]


def test_invalid_transition_raises():
    """未列於 2A §4 的 transition 拋 InvalidTransitionError。"""
    illegal = [
        (WorkState.PROPOSED, WorkState.DONE),
        (WorkState.PROPOSED, WorkState.IN_PROGRESS),
        (WorkState.APPROVED, WorkState.PROPOSED),  # 不可倒退
        (WorkState.IN_PROGRESS, WorkState.APPROVED),
        (WorkState.DONE, WorkState.IN_PROGRESS),   # 終態不可再動
        (WorkState.REJECTED, WorkState.APPROVED),
        (WorkState.CANCELLED, WorkState.ASSIGNED),
    ]
    for f, t in illegal:
        assert not can_transition(f, t), f"{f} -> {t} 應非法"
        with pytest.raises(InvalidTransitionError):
            validate_transition(f, t)


def test_blocked_is_non_terminal_and_resumable():
    """blocked 是 non-terminal：active → blocked 合法，blocked → active 合法，blocked → 終態非法。"""
    # 任何 active state 可進入 blocked
    for active in (WorkState.PROPOSED, WorkState.APPROVED, WorkState.ASSIGNED,
                   WorkState.IN_PROGRESS, WorkState.AWAITING_REVIEW, WorkState.AWAITING_APPROVAL):
        assert can_transition(active, WorkState.BLOCKED), f"{active} -> blocked 應合法"

    # blocked 可 resume 回 active state
    assert can_transition(WorkState.BLOCKED, WorkState.IN_PROGRESS)
    assert can_transition(WorkState.BLOCKED, WorkState.AWAITING_REVIEW)

    # blocked 不可進終態 / 不可再 blocked
    assert not can_transition(WorkState.BLOCKED, WorkState.DONE)
    assert not can_transition(WorkState.BLOCKED, WorkState.REJECTED)
    assert not can_transition(WorkState.BLOCKED, WorkState.CANCELLED)
    assert not can_transition(WorkState.BLOCKED, WorkState.BLOCKED)


# ─────────────────────────────────────────────
# 3. Store
# ─────────────────────────────────────────────

def test_store_append_and_fold(tmp_path):
    """append 多筆 event → fold 回 current WorkObject。"""
    store = WorkStore(data_dir=tmp_path)
    work_id = "work-1"

    _append(store, _transition(
        work_id, None, "proposed",
        objective="build X", owner="chief",
        assigned_agents=["developer"], dependencies=["work-0"],
    ))
    _append(store, _transition(work_id, "proposed", "approved", provenance=_prov(role="human", capability="approval")))
    _append(store, _transition(work_id, "approved", "assigned"))
    _append(store, _transition(work_id, "assigned", "in_progress"))
    _append(store, WorkEvent(
        work_id=work_id,
        event_type=WorkEventType.ARTIFACT_PRODUCED,
        payload={"artifact": {"ref": "sha256:abc", "path": "src/x.py"}},
        provenance=_prov(role="developer", capability="artifact.create", output_refs=["sha256:abc"]),
    ))

    work = store.fold(work_id)
    assert work.work_id == work_id
    assert work.state == WorkState.IN_PROGRESS
    assert work.objective == "build X"
    assert work.owner == "chief"
    assert work.assigned_agents == ["developer"]
    assert work.dependencies == ["work-0"]
    assert work.artifacts == [{"ref": "sha256:abc", "path": "src/x.py"}]
    assert work.provenance.role == "chief"


def test_store_fold_unknown_work_raises(tmp_path):
    """fold 找不到 work_id 時拋 WorkNotFoundError。"""
    store = WorkStore(data_dir=tmp_path)
    with pytest.raises(KeyError):
        store.fold("no-such-work")


def test_store_corrupt_row_skipped(tmp_path):
    """corrupt row（壞 JSON / 非法 event_type）跳過，不影響 fold。"""
    store = WorkStore(data_dir=tmp_path)
    work_id = "work-c"

    _append(store, _transition(work_id, None, "proposed", objective="obj", owner="chief"))
    # 手動塞 corrupt rows
    with open(store.store_file, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
        f.write(json.dumps({
            "work_id": work_id, "event_type": "not_a_type",
            "payload": {}, "provenance": {"role": "x", "capability": "y"},
        }) + "\n")
    _append(store, _transition(work_id, "proposed", "approved"))

    work = store.fold(work_id)
    assert work.state == WorkState.APPROVED
    assert work.objective == "obj"


def test_store_is_append_only(tmp_path):
    """store 只有 append / fold，無 update / delete / remove API。"""
    store = WorkStore(data_dir=tmp_path)
    for forbidden in ("update", "delete", "remove", "modify", "overwrite", "replace"):
        assert not hasattr(store, forbidden), f"store 不應有 {forbidden} API"


def test_resume_state_minimal_rebuild(tmp_path):
    """fold 從 events 最小重建 resume_state（blocked 時 current_phase = 進入 blocked 前的 phase）。"""
    store = WorkStore(data_dir=tmp_path)
    work_id = "work-r"

    _append(store, _transition(work_id, None, "proposed", objective="obj", owner="chief"))
    _append(store, _transition(work_id, "proposed", "approved"))
    _append(store, _transition(work_id, "approved", "assigned"))
    _append(store, _transition(work_id, "assigned", "in_progress"))
    _append(store, WorkEvent(
        work_id=work_id,
        event_type=WorkEventType.ARTIFACT_PRODUCED,
        payload={"artifact": {"ref": "sha256:abc"}},
        provenance=_prov(role="developer", capability="artifact.create", output_refs=["sha256:abc"]),
    ))
    _append(store, _transition(work_id, "in_progress", "blocked"))

    work = store.fold(work_id)
    assert work.state == WorkState.BLOCKED
    # 最小重建：current_phase 回到進入 blocked 前的 phase
    assert work.resume_state.current_phase == WorkState.IN_PROGRESS
    assert work.resume_state.last_artifact_refs == ["sha256:abc"]
    assert work.resume_state.pending_handoffs == []
    # R1：idempotency_keys 從 handoff events 推導（不再是 dead field）
    expected_key = derive_idempotency_key(
        work_id=work_id, role="developer", result_type="artifact",
        refs=["sha256:abc"],
    )
    assert work.resume_state.idempotency_keys == [expected_key]

    # 解除阻塞後 resume 回 current_phase 是合法 transition
    validate_transition(WorkState.BLOCKED, work.resume_state.current_phase)
