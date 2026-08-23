"""
tests/test_work_adapter.py
Soul OS — DSH-P0-1：Minimal Work Execution Adapter（Phase 0 implementation）。

驗收（對照 logs/DSH-P0-1-WORK-ORDER.md 的 10 條 acceptance gate）：
1. Bridge contract PASS（BridgeMessage JSON round-trip，兩側一致）
2. Work execution E2E PASS（create → execution → handoff → WorkEvent）
3. Handoff → durable WorkEvent PASS
4. Adapter 無 durable write authority
5. restart / resume PASS
6. duplicate handoff dedup PASS
7. DSH failure isolation PASS（crash / timeout / malformed / mis-routed
   不污染 durable truth）
8. No-DSH Survival PASS（拔掉 TS 後 Domain Core 仍可 fold / authorize / resume）
9. regression 全綠（tests/test_work_*.py，另跑）
10. Phase 0 scope containment PASS（src/work/ 僅 P1-Preflight 授權的 kernel.py 可動，
    其餘模組零改動）

執行：pytest tests/test_work_adapter.py
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work.authority import (
    ActionScope,
    AgentAction,
    Approval,
    AuthorityManager,
    HumanAuthorityContext,
)
from src.work.bridge import BridgeMessage, BridgeMessageType
from src.work.kernel import WorkKernel
from src.work.persistence import AuthorityStore
from src.work.roles import Role
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    ResultType,
    WorkEventType,
)
from src.work.store import NotDurableWriterError, WorkStore
from src.work.workflow import WorkflowOrchestrator
from src.work_adapter import BridgeExecutionError, WorkExecutionBridge
from src.work_adapter.bridge import _DEFAULT_ADAPTER_SCRIPT
from src.work_adapter.execution import build_execution_request, execute_work

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE_AVAILABLE = shutil.which("node") is not None

# 只匹配「實際 import 陳述」，不匹配 docstring 中的文檔引用。
_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)

needs_node = pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

class _TrustedPort:
    """Fake HumanAuthorityPort：只認可 authority_token == "trusted-token"。"""

    def authenticate(self, context) -> bool:
        if not isinstance(context, HumanAuthorityContext):
            return False
        return context.authority_token == "trusted-token"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _future(**delta_kwargs) -> datetime:
    return _utcnow() + timedelta(**delta_kwargs)


def _bridge(tmp_path, **kwargs) -> WorkExecutionBridge:
    """預設 bridge：repo 內建 soul-dsh-adapter.mjs + node。"""
    return WorkExecutionBridge(**kwargs)


def _orchestrator(tmp_path) -> WorkflowOrchestrator:
    return WorkflowOrchestrator(kernel=WorkKernel(data_dir=tmp_path))


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


def _artifact_rows(data_dir) -> list[dict]:
    return [
        r for r in _log_rows(data_dir)
        if r.get("event_type") == WorkEventType.ARTIFACT_PRODUCED.value
    ]


def _write_script(tmp_path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ─────────────────────────────────────────────
# Gate 1 — Bridge contract PASS
# ─────────────────────────────────────────────

@needs_node
class TestBridgeContract:
    def test_bridge_message_json_round_trip(self):
        """BridgeMessage JSON round-trip（Python 側）：envelope 欄位完整保留。"""
        msg = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="developer",
            source="soul_kernel",
            causation="evt-1",
            reference="dsh-session-abc",
            payload={"work_id": "w1", "capability": "artifact.create"},
        )
        data = json.loads(msg.model_dump_json())
        restored = BridgeMessage.model_validate(data)

        assert restored.message_type == BridgeMessageType.REQUEST
        assert restored.actor == "developer"
        assert restored.source == "soul_kernel"
        assert restored.causation == "evt-1"
        assert restored.reference == "dsh-session-abc"
        assert restored.schema_version == "1.0"
        assert restored.event_id == msg.event_id
        assert restored.timestamp == msg.timestamp
        assert restored.payload == msg.payload

    def test_bridge_round_trip_artifact(self, tmp_path):
        """TS adapter round-trip：request → HandoffResult（artifact）。"""
        bridge = _bridge(tmp_path)
        msg = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="developer",
            source="soul_kernel",
            payload={
                "work_id": "w-artifact",
                "objective": "build X",
                "role": "developer",
                "capability": "artifact.create",
                "resume_state": {},
            },
        )
        handoff = bridge.execute(msg)

        assert handoff.work_id == "w-artifact"           # 回聲 request 的 work_id
        assert handoff.role == "developer"                # 回聲 request 的 role
        assert handoff.result_type == ResultType.ARTIFACT
        assert len(handoff.artifact_refs) == 1
        assert handoff.artifact_refs[0].startswith("mock:sha256:")
        assert handoff.status == HandoffStatus.DONE
        assert handoff.resume_hint.get("execution") == "mock"

    def test_bridge_round_trip_evidence(self, tmp_path):
        """TS adapter round-trip：capability 含 evidence → evidence result。"""
        bridge = _bridge(tmp_path)
        msg = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="tester",
            source="soul_kernel",
            payload={
                "work_id": "w-evidence",
                "objective": "verify X",
                "role": "tester",
                "capability": "evidence.create",
                "resume_state": {},
            },
        )
        handoff = bridge.execute(msg)

        assert handoff.result_type == ResultType.EVIDENCE
        assert len(handoff.evidence_refs) == 1
        assert handoff.artifact_refs == []
        assert handoff.evidence_refs[0].startswith("mock:sha256:")

    def test_bridge_round_trip_decision(self, tmp_path):
        """TS adapter round-trip：capability 含 decision → decision result。"""
        bridge = _bridge(tmp_path)
        msg = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="chief",
            source="soul_kernel",
            payload={
                "work_id": "w-decision",
                "objective": "decide X",
                "role": "chief",
                "capability": "decision",
                "resume_state": {},
            },
        )
        handoff = bridge.execute(msg)

        assert handoff.result_type == ResultType.DECISION
        assert handoff.decision.get("mock") is True
        assert handoff.artifact_refs == []
        assert handoff.evidence_refs == []

    def test_same_request_same_ref(self, tmp_path):
        """mock execution 是 content-addressed：同 request → 同 ref（dedup 前置條件）。"""
        bridge = _bridge(tmp_path)
        msg = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="developer",
            source="soul_kernel",
            payload={
                "work_id": "w-dedup",
                "objective": "build X",
                "role": "developer",
                "capability": "artifact.create",
                "resume_state": {},
            },
        )
        h1 = bridge.execute(msg)
        h2 = bridge.execute(msg)
        assert h1.artifact_refs == h2.artifact_refs


# ─────────────────────────────────────────────
# Gate 2 — Work execution E2E PASS
# ─────────────────────────────────────────────

@needs_node
class TestExecutionE2E:
    def test_execution_path_e2e(self, tmp_path):
        """完整 path：create → assign → execute_work → HandoffResult → WorkEvent。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        message, handoff, event = execute_work(
            orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
        )

        # request message 是 BridgeMessage(request)，actor/source 正確
        assert message.message_type == BridgeMessageType.REQUEST
        assert message.actor == Role.RESEARCHER.value
        assert message.source == "soul_kernel"
        assert message.payload["work_id"] == work_id
        assert message.payload["capability"] == "artifact.create"

        # handoff 錨定到同一 work
        assert handoff.work_id == work_id
        assert handoff.role == Role.RESEARCHER.value

        # WorkEvent：artifact_produced，錨定到同一 work
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert event.work_id == work_id
        assert event.provenance.role == Role.RESEARCHER.value
        assert event.provenance.output_refs == handoff.artifact_refs

    def test_execution_path_evidence(self, tmp_path):
        """evidence 路徑：capability evidence.create → evidence_produced。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        _, handoff, event = execute_work(
            orch, work_id, Role.TESTER.value, "evidence.create", bridge
        )

        assert handoff.result_type == ResultType.EVIDENCE
        assert event.event_type == WorkEventType.EVIDENCE_PRODUCED
        assert event.provenance.output_refs == handoff.evidence_refs

    def test_execution_path_decision(self, tmp_path):
        """decision 路徑：capability decision → decision_made。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        _, handoff, event = execute_work(
            orch, work_id, Role.CHIEF.value, "decision", bridge
        )

        assert handoff.result_type == ResultType.DECISION
        assert event.event_type == WorkEventType.DECISION_MADE


# ─────────────────────────────────────────────
# Gate 3 — Handoff → durable WorkEvent PASS
# ─────────────────────────────────────────────

@needs_node
class TestDurableHandoff:
    def test_handoff_writes_durable_workevent(self, tmp_path):
        """handoff → durable WorkEvent：restart 後 fold 出 artifact。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        _, handoff, event = execute_work(
            orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
        )

        # raw durable log：恰一筆 artifact_produced
        assert len(_artifact_rows(tmp_path)) == 1

        # restart：新 kernel 從 durable log fold
        kernel2 = WorkKernel(data_dir=tmp_path)
        work2 = kernel2.fold(work_id)
        assert work2.state.value == "proposed"  # 未記 transition，state 仍為 proposed
        assert work2.artifacts == [{"refs": handoff.artifact_refs}]
        # resume_state 最小重建：last_artifact_refs 指向 mock artifact
        assert work2.resume_state.last_artifact_refs == handoff.artifact_refs

    def test_handoff_event_persisted(self, tmp_path):
        """WorkEvent 原樣寫入 durable log（persisted 內容 == 回傳 event）。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        _, _, event = execute_work(
            orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
        )

        rows = _artifact_rows(tmp_path)
        # 寫入 log 的 row（含 timestamp / provenance）與回傳的 event 完全一致
        assert rows[0] == event.model_dump(mode="json")


# ─────────────────────────────────────────────
# Gate 4 — Adapter 無 durable write authority
# ─────────────────────────────────────────────

class TestNoDurableWriteAuthority:
    def test_adapter_script_has_no_fs_writes(self):
        """TS adapter 不 import fs、不寫檔（source-level 檢查）。"""
        src = _DEFAULT_ADAPTER_SCRIPT.read_text(encoding="utf-8")
        assert "node:fs" not in src
        assert "writeFile" not in src
        assert "createWriteStream" not in src
        # 只透過 stdin/stdout 通訊
        assert "process.stdin" in src
        assert "process.stdout" in src

    @needs_node
    def test_adapter_runtime_creates_no_files(self, tmp_path):
        """跑真 adapter（cwd=tmp_path）→ 不建立任何檔案。"""
        request = json.dumps({
            "message_type": "request",
            "actor": "developer",
            "source": "soul_kernel",
            "payload": {
                "work_id": "w-nofiles",
                "objective": "build X",
                "role": "developer",
                "capability": "artifact.create",
                "resume_state": {},
            },
        })
        proc = subprocess.run(
            ["node", str(_DEFAULT_ADAPTER_SCRIPT)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=tmp_path,
            timeout=15,
        )
        assert proc.returncode == 0
        handoff = json.loads(proc.stdout.strip())
        assert handoff["work_id"] == "w-nofiles"
        # cwd 保持空：adapter 沒有寫任何檔案
        assert list(tmp_path.iterdir()) == []

    def test_adapter_actor_cannot_write_durable(self, tmp_path):
        """adapter 的 actor（dsh_adapter）直接寫 WorkStore → NotDurableWriterError。

        即使直接 import WorkStore 也不能 bypass（store-level single-writer
        enforcement，2D §1）。"""
        store = WorkStore(data_dir=tmp_path)
        event = HandoffResult(
            work_id="w1",
            role="developer",
            result_type=ResultType.ARTIFACT,
            artifact_refs=["mock:x"],
            status=HandoffStatus.DONE,
        )
        from src.work.schema import Provenance, WorkEvent

        ev = WorkEvent(
            work_id=event.work_id,
            event_type=WorkEventType.ARTIFACT_PRODUCED,
            payload={"artifact": {"refs": event.artifact_refs}},
            provenance=Provenance(role=event.role, capability="artifact.create"),
        )
        with pytest.raises(NotDurableWriterError):
            store.append(ev, actor="dsh_adapter")


# ─────────────────────────────────────────────
# Gate 5 — restart / resume PASS
# ─────────────────────────────────────────────

@needs_node
class TestRestartResume:
    def test_restart_resume_fold_and_dedup(self, tmp_path):
        """restart（新 kernel + 新 orchestrator + 新 bridge）→ fold 正確；
        同 request 重跑 → dedup 回傳既有 event（resume 不重複執行）。"""
        # 第一次執行
        orch1 = _orchestrator(tmp_path)
        bridge1 = _bridge(tmp_path)
        work_id = _create_assigned_work(orch1)
        _, _, event1 = execute_work(
            orch1, work_id, Role.RESEARCHER.value, "artifact.create", bridge1
        )

        # restart：全新組件，同一 durable data_dir
        orch2 = _orchestrator(tmp_path)
        bridge2 = _bridge(tmp_path)
        work2 = orch2.synthesize(work_id)
        assert work2.artifacts == [{"refs": event1.provenance.output_refs}]
        # resume_state 已帶 idempotency_keys（R1：resume 防重複執行）
        assert len(work2.resume_state.idempotency_keys) >= 1

        # resume 後重跑同 request → Domain Core dedup 命中，不重複 append
        _, _, event2 = execute_work(
            orch2, work_id, Role.RESEARCHER.value, "artifact.create", bridge2
        )
        # 回傳的是同一筆 event（逐位元相同），durable log 仍只有一筆
        assert event2.model_dump_json() == event1.model_dump_json()
        assert len(_artifact_rows(tmp_path)) == 1


# ─────────────────────────────────────────────
# Gate 6 — duplicate handoff dedup PASS
# ─────────────────────────────────────────────

@needs_node
class TestDedup:
    def test_duplicate_handoff_dedup(self, tmp_path):
        """同一 request 執行兩次 → 只 append 一筆 WorkEvent（effectively-once）。"""
        orch = _orchestrator(tmp_path)
        bridge = _bridge(tmp_path)
        work_id = _create_assigned_work(orch)

        _, _, event1 = execute_work(
            orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
        )
        _, _, event2 = execute_work(
            orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
        )

        # dedup 回傳同一筆 event（逐位元相同），durable log 只有一筆
        assert event2.model_dump_json() == event1.model_dump_json()
        assert len(_artifact_rows(tmp_path)) == 1
        assert len(orch.synthesize(work_id).artifacts) == 1


# ─────────────────────────────────────────────
# Gate 7 — DSH failure isolation PASS
# ─────────────────────────────────────────────

@needs_node
class TestFailureIsolation:
    def _work_with_bridge(self, tmp_path, adapter_script, **bridge_kwargs):
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        bridge = WorkExecutionBridge(adapter_script=adapter_script, **bridge_kwargs)
        return orch, work_id, bridge

    def test_crash_exit_nonzero(self, tmp_path):
        """adapter crash（exit 3）→ BridgeExecutionError；durable log 不變。"""
        script = _write_script(
            tmp_path, "crash.mjs",
            'process.stderr.write("boom\\n");\nprocess.exit(3);\n',
        )
        orch, work_id, bridge = self._work_with_bridge(tmp_path, script)

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []           # 無半寫入
        assert orch.synthesize(work_id).state.value == "proposed"  # durable truth 完好

    def test_timeout(self, tmp_path):
        """adapter 卡住（timeout）→ BridgeExecutionError；durable log 不變。"""
        script = _write_script(
            tmp_path, "hang.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.resume();\n'
            'setInterval(() => {}, 1000);\n',
        )
        orch, work_id, bridge = self._work_with_bridge(
            tmp_path, script, timeout=0.5
        )

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []

    def test_malformed_response(self, tmp_path):
        """adapter 回 malformed JSON → BridgeExecutionError；durable log 不變。"""
        script = _write_script(
            tmp_path, "malformed.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.on("end", () => {\n'
            '  process.stdout.write("this is not json\\n");\n'
            '});\n',
        )
        orch, work_id, bridge = self._work_with_bridge(tmp_path, script)

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []

    def test_empty_stdout(self, tmp_path):
        """adapter 回空 stdout → BridgeExecutionError；durable log 不變。"""
        script = _write_script(
            tmp_path, "empty.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.on("end", () => {});\n',
        )
        orch, work_id, bridge = self._work_with_bridge(tmp_path, script)

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []

    def test_invalid_handoff_contract(self, tmp_path):
        """adapter 回結構合法但缺欄位的 JSON → pydantic 拒收 → BridgeExecutionError。"""
        script = _write_script(
            tmp_path, "bad-contract.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.on("end", () => {\n'
            '  process.stdout.write(JSON.stringify({work_id: "w1"}) + "\\n");\n'
            '});\n',
        )
        orch, work_id, bridge = self._work_with_bridge(tmp_path, script)

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []

    def test_misrouted_handoff(self, tmp_path):
        """adapter 竄改 work_id → anchor 驗證拒絕；不寫進另一 work 的 durable log。"""
        script = _write_script(
            tmp_path, "misroute.mjs",
            'process.stdin.on("data", () => {});\n'
            'process.stdin.on("end", () => {\n'
            '  process.stdout.write(JSON.stringify({\n'
            '    work_id: "other-work", role: "developer", result_type: "artifact",\n'
            '    artifact_refs: ["mock:evil"], evidence_refs: [], decision: {},\n'
            '    status: "done", resume_hint: {}\n'
            '  }) + "\\n");\n'
            '});\n',
        )
        orch, work_id, bridge = self._work_with_bridge(tmp_path, script)

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []   # 沒有污染任何 work 的 log


# ─────────────────────────────────────────────
# Gate 8 — No-DSH Survival PASS
# ─────────────────────────────────────────────

class TestNoDSHSurvival:
    def test_domain_core_works_without_bridge(self, tmp_path):
        """拔掉 bridge/DSH：Domain Core 仍可 create / assign / handoff / fold /
        authorize / resume（不依賴 node）。"""
        # Work：直接建 HandoffResult，不經過 bridge
        orch = _orchestrator(tmp_path)
        work_id = orch.create_work("no-dsh survival", Role.CHIEF)
        orch.assign(work_id, Role.DEVELOPER)
        event = orch.consume_handoff(HandoffResult(
            work_id=work_id,
            role=Role.RESEARCHER.value,
            result_type=ResultType.ARTIFACT,
            artifact_refs=["local:artifact-1"],
            status=HandoffStatus.DONE,
        ))
        work = orch.synthesize(work_id)
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert work.artifacts == [{"refs": ["local:artifact-1"]}]

        # Authority：grant → authorize → consume → resume（durable 恢復）
        store = AuthorityStore(data_dir=tmp_path)
        mgr = AuthorityManager(human_authority=_TrustedPort(), store=store)
        approval = Approval(
            work_id=work_id,
            capability="git.commit",
            requested_action={"repository": "soul-os-harness"},
            action_scope=ActionScope.SINGLE_ACTION,
            grantee_role=Role.DEVELOPER.value,
            granted_by="human",
            expires_at=_future(hours=1),
        )
        context = HumanAuthorityContext(
            identity="human",
            authority_token="trusted-token",
            issued_at=_utcnow(),
            expires_at=_future(hours=1),
        )
        grant = mgr.grant(approval, context)
        action = AgentAction(
            grant_id=grant.grant_id,
            work_id=work_id,
            role=grant.grantee_role,
            capability=grant.capability,
            action=dict(approval.requested_action),
        )
        assert mgr.is_authorized(action) is True
        mgr.consume(grant.grant_id)
        assert mgr.is_authorized(action) is False

        # restart → resume：consumed grant 跨 restart 仍 deny
        mgr2 = AuthorityManager(human_authority=_TrustedPort(), store=AuthorityStore(data_dir=tmp_path))
        mgr2.resume()
        assert mgr2.is_authorized(action) is False

    def test_missing_node_raises_but_core_intact(self, tmp_path):
        """node 不在 PATH → BridgeExecutionError；WorkKernel fold 仍可用。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        bridge = WorkExecutionBridge(node_bin="definitely-missing-node-xyz")

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        # durable truth 完好，fold 仍可用（DSH 拔掉不影響 Domain Core）
        work = orch.synthesize(work_id)
        assert work.work_id == work_id
        assert _artifact_rows(tmp_path) == []

    def test_missing_adapter_script_raises(self, tmp_path):
        """adapter script 不存在 → BridgeExecutionError。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_assigned_work(orch)
        bridge = WorkExecutionBridge(adapter_script=tmp_path / "nonexistent.mjs")

        with pytest.raises(BridgeExecutionError):
            execute_work(orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge)

        assert _artifact_rows(tmp_path) == []


# ─────────────────────────────────────────────
# Gate 10 — Phase 0 scope containment PASS
# ─────────────────────────────────────────────

class TestScopeContainment:
    def test_src_work_untouched_by_git(self):
        """git 確認 src/work/ 改動僅限授權檔（scope containment）。

        Phase 0 鎖死 src/work/ 零改動；DSH P1-Preflight（M1/M2a）明確授權改
        src/work/kernel.py（record_handoff status 語義 + result_type_for_capability）；
        DSH P1-A（Execution Target Contract）授權改 src/work/schema.py（新增
        ExecutionShape enum）+ src/work/workflow.py（新增 derive_execution_shape）；
        DSH P1-C0（Domain Core Capability Enforcement）授權改 src/work/roles.py
        （新增 CapabilityNotAuthorizedError）+ src/work/kernel.py（record_handoff
        role↔capability enforcement），並依 frozen matrix 修正 src/work/e2e.py 的
        Developer + artifact.create divergence（2A §5.1：artifact.create 歸
        Researcher，不反向修改 matrix）。
        DSH P1-C1（Real DSH single_shot Routing）授權**新增**
        src/work/execution_evidence.py（C1.1-C1.3：ExecutionEvidence /
        RoleCwdRegistry / read_execution_evidence）+ src/work/artifact_store.py
        （C1.7：write_artifact / verify_artifact_ref / staging），不修改既有
        src/work/ 模組（state_machine / store / authority / persistence /
        roles / schema 等仍不得動）。
        其餘 src/work/ 模組仍不得有任何改動。
        """
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", "src/work"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert proc.returncode == 0
        changed = {
            line.split()[1]
            for line in proc.stdout.splitlines()
            if line.strip()
        }
        assert changed <= {
            "src/work/kernel.py",
            "src/work/roles.py",
            "src/work/e2e.py",
            "src/work/execution_evidence.py",
            "src/work/artifact_store.py",
        }, (
            "src/work/ 僅授權檔可被 P1-C1 改動：\n" + proc.stdout
        )

    def test_adapter_python_modules_do_not_import_dsh(self):
        """src/work_adapter/ 不 import DSH / Cordis type（bridge 獨立、無污染）。"""
        import src.work_adapter.bridge as bmod
        import src.work_adapter.execution as emod

        for mod in (bmod, emod):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert not _DSH_IMPORT_RE.search(src), (
                f"{mod.__name__} 不得 import DSH / Cordis type"
            )

    def test_work_core_still_zero_dsh_import(self):
        """回歸檢查：src/work/ 十一模組仍零 DSH import（永久鎖死不破壞）。"""
        work_dir = REPO_ROOT / "src" / "work"
        offenders = []
        for py in sorted(work_dir.glob("*.py")):
            src = py.read_text(encoding="utf-8")
            if _DSH_IMPORT_RE.search(src):
                offenders.append(py.name)
        assert offenders == [], f"src/work/ 出現 DSH import: {offenders}"
