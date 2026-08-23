"""
tests/test_work_e2e.py
Soul OS — DSH Multi-Agent MVP-7：End-to-End Vertical Slice（2A–2D 完整閉環）。

驗收（對照 logs/DSH-MVP-7-WORK-ORDER.md）：
- 完整閉環跑通：create → approve → assign → handoff → synthesize → approve
  → privileged action → evidence → durable store
- restart → resume 後 authorization 語意一致（consumed grant 跨 restart 仍 deny）
- single-writer 不破壞（e2e 只走 kernel，不直接 import WorkStore）
- 零 DSH coupling（e2e.py 不 import 任何 DSH / Cordis type）

執行：pytest tests/test_work_e2e.py
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work import e2e as e2e_mod
from src.work.authority import (
    ActionScope,
    Approval,
    AuthorityManager,
    HumanAuthorityContext,
    NotHumanGrantorError,
)
from src.work.e2e import VerticalSliceResult, run_vertical_slice
from src.work.kernel import WorkKernel
from src.work.persistence import AuthorityEventType, AuthorityStore
from src.work.roles import Role
from src.work.schema import HandoffStatus, ResultType, WorkEventType, WorkState
from src.work.state_machine import requires_human_approval


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)

# 只匹配「實際 import 陳述」，不匹配 docstring 中的文檔引用。
_WORKSTORE_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*WorkStore", re.MULTILINE
)


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def _future(**delta_kwargs) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta_kwargs)


class _TrustedPort:
    """Fake HumanAuthorityPort：只認可 authority_token == "trusted-token" 的 context。"""

    def __init__(self, valid_token: str = "trusted-token"):
        self._valid_token = valid_token

    def authenticate(self, context) -> bool:
        if not isinstance(context, HumanAuthorityContext):
            return False
        return context.authority_token == self._valid_token


class _RejectingPort:
    """Fake HumanAuthorityPort：一律拒絕。"""

    def authenticate(self, context) -> bool:
        return False


def _run(tmp_path, **kwargs) -> VerticalSliceResult:
    """跑一次 happy-path vertical slice（注入 trusted port）。"""
    return run_vertical_slice(
        "build feature X",
        data_dir=tmp_path,
        human_authority=_TrustedPort(),
        **kwargs,
    )


def _state_path(data_dir) -> list[str]:
    """讀 raw WorkEvent log，回傳 state_transition 的 `to` 序列。"""
    path = Path(data_dir) / "work_events.jsonl"
    states: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("event_type") == WorkEventType.STATE_TRANSITION.value:
                states.append(data["payload"]["to"])
    return states


# ─────────────────────────────────────────────
# 1. 完整閉環跑通
# ─────────────────────────────────────────────

def test_vertical_slice_full_loop(tmp_path):
    """完整閉環跑通：final WorkObject state=done，含 artifact/evidence/decision。"""
    result = _run(tmp_path)

    assert isinstance(result, VerticalSliceResult)
    assert result.work.work_id == result.work_id
    assert result.work.state == WorkState.DONE
    assert result.work.objective == "build feature X"
    assert result.work.owner == "chief"
    # assign 的 decision_made
    assert result.work.decisions == [{"assign_role": "developer"}]
    # Specialist 的 artifact
    assert result.work.artifacts == [{"refs": ["sha256:artifact-1"]}]
    # Tester 的 evidence（指向 action）
    assert result.work.evidence == [{"refs": [f"action:{result.action.action_id}"]}]
    # resume_state 最小重建：last_artifact_refs 指向 artifact
    assert result.work.resume_state.last_artifact_refs == ["sha256:artifact-1"]


def test_vertical_slice_state_machine_path(tmp_path):
    """state machine 走完整 2A §4 happy path（7 個 state_transition）。"""
    _run(tmp_path)
    assert _state_path(tmp_path) == [
        "proposed",
        "approved",
        "assigned",
        "in_progress",
        "awaiting_review",
        "awaiting_approval",
        "done",
    ]


def test_vertical_slice_human_approval_transitions(tmp_path):
    """兩個 Human approval transition 確實需要 Human approval（2A §4）。"""
    assert requires_human_approval(WorkState.PROPOSED, WorkState.APPROVED)
    assert requires_human_approval(WorkState.AWAITING_APPROVAL, WorkState.DONE)
    # 其餘 autonomous
    assert not requires_human_approval(WorkState.APPROVED, WorkState.ASSIGNED)
    assert not requires_human_approval(WorkState.IN_PROGRESS, WorkState.AWAITING_REVIEW)


def test_vertical_slice_privileged_action_flow(tmp_path):
    """privileged action：grant → is_authorized(True) → consume → is_authorized(False)。"""
    result = _run(tmp_path)

    # provenance chain 一對一（2C §6）
    assert result.grant.approval_id == result.approval.approval_id
    assert result.action.grant_id == result.grant.grant_id
    assert result.approval.work_id == result.work_id
    assert result.grant.work_id == result.work_id
    assert result.action.work_id == result.work_id

    # single_action：consume 前 authorized，consume 後 deny（I8 exactly once）
    assert result.authorized is True
    assert result.post_consume_authorized is False


def test_vertical_slice_evidence_references_action(tmp_path):
    """evidence → action（2C §6 provenance chain 最後一段）。"""
    result = _run(tmp_path)
    assert result.evidence_event.event_type == WorkEventType.EVIDENCE_PRODUCED
    assert result.evidence_event.provenance.role == "tester"
    assert result.evidence_event.provenance.output_refs == [f"action:{result.action.action_id}"]


def test_vertical_slice_durable_store_has_authority_events(tmp_path):
    """AuthorityStore 記錄 approval_granted / grant_issued / grant_consumed。"""
    _run(tmp_path)
    store = AuthorityStore(data_dir=tmp_path)
    types = [e.event_type for e in store.read_events()]
    assert AuthorityEventType.APPROVAL_GRANTED in types
    assert AuthorityEventType.GRANT_ISSUED in types
    assert AuthorityEventType.GRANT_CONSUMED in types


# ─────────────────────────────────────────────
# 2. restart → resume 後 authorization 語意一致
# ─────────────────────────────────────────────

def test_restart_resume_authorization_consistent(tmp_path):
    """restart → resume 後，consumed single_action grant 仍 deny（語意一致）。"""
    result = _run(tmp_path)

    # restart：新 AuthorityManager，同 durable store，resume
    mgr2 = AuthorityManager(
        human_authority=_TrustedPort(),
        store=AuthorityStore(data_dir=tmp_path),
    )
    mgr2.resume()
    # consumed grant 跨 restart 仍 deny（single_action exactly once）
    assert mgr2.is_authorized(result.action) is False


def test_restart_resume_work_durable(tmp_path):
    """restart 後新 WorkKernel fold 出 done + artifact + evidence（durable truth）。"""
    result = _run(tmp_path)

    # restart：新 WorkKernel，同 durable store
    kernel2 = WorkKernel(data_dir=tmp_path)
    work2 = kernel2.fold(result.work_id)

    assert work2.state == WorkState.DONE
    assert work2.objective == "build feature X"
    assert work2.artifacts == [{"refs": ["sha256:artifact-1"]}]
    assert work2.evidence == [{"refs": [f"action:{result.action.action_id}"]}]


# ─────────────────────────────────────────────
# 3. single-writer 不破壞
# ─────────────────────────────────────────────

def test_e2e_does_not_import_workstore():
    """e2e 只走 kernel，不直接 import / 使用 WorkStore。"""
    src = _source_of(e2e_mod)
    assert not _WORKSTORE_IMPORT_RE.search(src), (
        "e2e.py 不得 import WorkStore（只走 kernel）"
    )


def test_e2e_writes_only_via_kernel_and_authority(tmp_path):
    """e2e 的 durable write 只經 kernel（WorkEvent）與 AuthorityManager（AuthorityEvent）。"""
    result = _run(tmp_path)

    # WorkEvent log 由 kernel 寫入（single-writer）
    work_store_file = Path(tmp_path) / "work_events.jsonl"
    assert work_store_file.exists()

    # AuthorityEvent log 由 AuthorityManager 寫入（DURABLE_WRITER）
    authority_store_file = Path(tmp_path) / "authority_events.jsonl"
    assert authority_store_file.exists()

    # 兩者都是 append-only JSONL，且 work 可 fold 回 done
    assert result.work.state == WorkState.DONE


# ─────────────────────────────────────────────
# 4. 零 DSH coupling
# ─────────────────────────────────────────────

def test_e2e_does_not_import_dsh():
    """e2e.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(e2e_mod)), (
        "e2e.py 不得 import DSH / Cordis type"
    )


def test_e2e_result_contains_no_dsh_strings(tmp_path):
    """vertical slice 產出的 durable evidence 序列化後不含 DSH type/id 字串。"""
    result = _run(tmp_path)
    for obj in (result.work, result.approval, result.grant, result.action):
        assert "dsh" not in obj.model_dump_json().lower()


# ─────────────────────────────────────────────
# 5. Human authority deny-by-default
# ─────────────────────────────────────────────

def test_vertical_slice_deny_by_default_without_port(tmp_path):
    """無注入 HumanAuthorityPort → grant 拋 NotHumanGrantorError（deny-by-default）。"""
    with pytest.raises(NotHumanGrantorError):
        run_vertical_slice("build X", data_dir=tmp_path)  # 無 port


def test_vertical_slice_rejects_forged_context(tmp_path):
    """rejecting port → grant 拋 NotHumanGrantorError（Human authority 不可 self-attest）。"""
    with pytest.raises(NotHumanGrantorError):
        run_vertical_slice(
            "build X",
            data_dir=tmp_path,
            human_authority=_RejectingPort(),
        )


# ─────────────────────────────────────────────
# 6. 可注入 approval / context（edge case 覆寫）
# ─────────────────────────────────────────────

def test_vertical_slice_accepts_injected_approval(tmp_path):
    """注入自訂 approval（git.push + single_action）→ 閉環用該 approval 的 capability。"""
    approval = Approval(
        work_id="placeholder",  # 會被 e2e 覆寫為實際 work_id
        capability="git.push",
        requested_action={"repository": "soul-os-harness", "branch": "main"},
        action_scope=ActionScope.SINGLE_ACTION,
        grantee_role="developer",
        granted_by="human",
        expires_at=_future(hours=1),
    )
    result = _run(tmp_path, approval=approval)

    assert result.approval.work_id == result.work_id
    assert result.approval.capability == "git.push"
    assert result.grant.capability == "git.push"
    assert result.action.capability == "git.push"
    assert result.authorized is True
    assert result.post_consume_authorized is False
