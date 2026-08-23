"""
tests/test_work_p1c1_routing.py
Soul OS — DSH P1-C1：Real DSH single_shot Routing（12 情境矩陣 + C1.9 smoke）。

對照 logs/DSH-P1-C1-WORK-ORDER.md：
- C1.1-C1.3（src/work/execution_evidence.py）：ExecutionEvidence /
  RoleCwdRegistry / read_execution_evidence / verify_role_binding
- C1.4（src/work_adapter/bridge.py）：execute_dsh（spawn dsh headless +
  --patch overlay + 事後讀回 log 路徑）
- C1.5-C1.6（src/work_adapter/execution.py）：execute_work_dsh（identity →
  claim → content 三層 cross-check，fail-closed）
- C1.7（src/work/artifact_store.py）：write_artifact / verify_artifact_ref /
  single-writer / staging
- C1.8：12 情境矩陣——**T1-T8/T11/T12 是 fake-log 單元測試**（構造 fake
  session log 測 Domain Core reader/verify），T9/T10 測 registry invariant。
- C1.9：真 DSH smoke（環境不可用時 skip，記錄風險註記）。

執行：pytest tests/test_work_p1c1_routing.py -v
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.paths import reset_data_root
from src.work.artifact_store import ArtifactStore
from src.work.bridge import DURABLE_WRITER, BridgeMessage, BridgeMessageType
from src.work.execution_evidence import (
    SESSION_LOG_FORMAT_VERSION,
    ExecutionEvidenceError,
    RoleCwdConflictError,
    RoleCwdRegistry,
    read_execution_evidence,
)
from src.work.kernel import WorkKernel
from src.work.roles import CapabilityNotAuthorizedError, Role
from src.work.schema import (
    HandoffStatus,
    ResultType,
    WorkEventType,
    WorkState,
)
from src.work.store import NotDurableWriterError
from src.work.workflow import WorkflowOrchestrator
from src.work_adapter import BridgeExecutionError, WorkExecutionBridge
from src.work_adapter.execution import execute_work_dsh, rebuild_handoff_claim


# ─────────────────────────────────────────────
# C1.9 環境偵測（真 DSH 不可用 → skip + 風險註記）
# ─────────────────────────────────────────────

def _credentials_available() -> bool:
    """LLM credential 是否可用（env 或 $DSH_HOME/.credentials.yaml）。"""
    if __import__("os").environ.get("DEEPSEEK_API_KEY"):
        return True
    home = Path(__import__("os").environ.get("DSH_HOME", str(Path.home() / ".dsh")))
    cred = home / ".credentials.yaml"
    if cred.is_file():
        try:
            return "DEEPSEEK_API_KEY" in cred.read_text(encoding="utf-8")
        except OSError:
            return False
    return False


_DSH_AVAILABLE = shutil.which("dsh") is not None
_DSH_CREDENTIALS = _credentials_available()

needs_real_dsh = pytest.mark.skipif(
    not (_DSH_AVAILABLE and _DSH_CREDENTIALS),
    reason=(
        "real DSH environment unavailable (dsh CLI or LLM credential missing); "
        "C1.9 skipped — risk note: config-driven/zstd/tool-scope feasibility "
        "not validated against real DSH"
    ),
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_data_root():
    """每個測試前後重設 data_root cache（SOUL_OS_DATA_DIR 變更隔離）。"""
    reset_data_root()
    yield
    reset_data_root()


def _orchestrator(tmp_path) -> WorkflowOrchestrator:
    return WorkflowOrchestrator(kernel=WorkKernel(data_dir=tmp_path))


def _create_work(orch: WorkflowOrchestrator, objective: str = "build feature X") -> str:
    work_id = orch.create_work(objective, Role.CHIEF)
    orch.assign(work_id, Role.DEVELOPER)
    return work_id


def _make_session_log(
    log_path: Path,
    *,
    cwd: str,
    messages: list[str],
    session_id: str = "session-test-0000",
    created_at: int = 1787500000000,
    version: int = SESSION_LOG_FORMAT_VERSION,
    extra_events: list[dict] | None = None,
) -> Path:
    """構造 fake DSH session log（mirror dsh-session-persistence-jsonl 格式）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = {
        "type": "session",
        "version": version,
        "id": session_id,
        "createdAt": created_at,
        "cwd": cwd,
        "delegationDepth": 0,
    }
    lines = [json.dumps(header)]
    seq = 1
    for text in messages:
        lines.append(json.dumps({
            "type": "assistant/message",
            "seq": seq,
            "time": created_at + seq,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            },
        }))
        seq += 1
    for event in extra_events or []:
        lines.append(json.dumps(event))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def _claim_json(
    work_id: str,
    role: str,
    result_type: str = "artifact",
    refs: list[str] | None = None,
    status: str = "done",
    decision: dict | None = None,
) -> str:
    return json.dumps({
        "work_id": work_id,
        "role": role,
        "result_type": result_type,
        "artifact_refs": refs or [],
        "evidence_refs": [],
        "decision": decision or {},
        "status": status,
        "resume_hint": {},
    })


class _FakeBridge:
    """fake DSH bridge：execute_dsh 直接回報預設 log 路徑（fake-log 單元測試）。"""

    def __init__(self, log_path: Path):
        self.log_path = str(log_path)
        self.last_role_cwd: str | None = None
        self.last_message: BridgeMessage | None = None

    def execute_dsh(self, message: BridgeMessage, role_cwd: str) -> str:
        self.last_message = message
        self.last_role_cwd = role_cwd
        return self.log_path


def _registry_with(
    tmp_path, *bindings: tuple[str, str]
) -> RoleCwdRegistry:
    registry = RoleCwdRegistry()
    for role, cwd in bindings:
        registry.register(role, cwd)
    return registry


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
# 12 情境矩陣（T1-T8/T11/T12 fake-log；T9/T10 registry invariant）
# ─────────────────────────────────────────────

class TestMatrixFakeLog:
    def test_t1_researcher_cwd_artifact_passes(self, tmp_path):
        """T1：Researcher → Researcher cwd → artifact → PASS。

        P1-C2（D2 遷移）：claim **不聲稱 ref**（artifact_refs=[]）——final_message
        就是 artifact content，canonical ref 由 Domain Core 寫入 store 後回填。
        """
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        claim_text = _claim_json(work_id, "researcher")
        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)

        _, claim, event, evidence = execute_work_dsh(
            orch, work_id, Role.RESEARCHER.value, "artifact.create",
            bridge, registry, store,
        )
        canonical = "sha256:" + hashlib.sha256(
            claim_text.encode("utf-8")
        ).hexdigest()
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert event.provenance.output_refs == [canonical]
        assert evidence.cwd == res_cwd
        assert claim.role == "researcher"
        assert claim.artifact_refs == [canonical]  # Domain Core 回填
        assert store.verify_artifact_ref(canonical)  # 真的落盤（content 定址）
        assert orch.synthesize(work_id).artifacts == [{"refs": [canonical]}]

    def test_t2_developer_artifact_denied_capability(self, tmp_path):
        """T2：Developer → Developer cwd → artifact → DENY（P1-C0 capability）。

        P1-C2 遷移：claim 不聲稱 ref（否則 D2 防偽先於 capability gate 觸發），
        capability gate 在 consume_handoff（kernel enforcement）拒絕。
        """
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        claim_text = _claim_json(work_id, "developer")  # 不聲稱 ref（D2）

        log_path = _make_session_log(
            tmp_path / "logs" / "dev" / "session.jsonl",
            cwd=dev_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("developer", dev_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(CapabilityNotAuthorizedError, match="artifact.create"):
            execute_work_dsh(
                orch, work_id, Role.DEVELOPER.value, "artifact.create",
                bridge, registry, store,
            )
        # DENY 不寫 durable（無半寫入）
        assert len(_log_rows(tmp_path)) == rows_before

    def test_t3_researcher_task_in_developer_cwd_denied(self, tmp_path):
        """T3：Researcher task 在 Developer cwd → DENY（identity binding）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        log_path = _make_session_log(
            tmp_path / "logs" / "dev" / "session.jsonl",
            cwd=dev_cwd,  # session 真的在 developer cwd 跑
            messages=[_claim_json(work_id, "researcher")],
        )
        registry = _registry_with(
            tmp_path, ("researcher", res_cwd), ("developer", dev_cwd)
        )
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="identity binding failed"):
            execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before

    def test_t4_llm_claims_researcher_but_cwd_developer_denied(self, tmp_path):
        """T4：LLM 自稱 Researcher，但 cwd=Developer → DENY（claim.role ≠ binding role）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        log_path = _make_session_log(
            tmp_path / "logs" / "dev" / "session.jsonl",
            cwd=dev_cwd,  # process 事實：developer cwd
            messages=[_claim_json(work_id, "researcher")],  # LLM 自稱 researcher
        )
        registry = _registry_with(tmp_path, ("developer", dev_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="claim.role"):
            execute_work_dsh(
                orch, work_id, Role.DEVELOPER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before

    def test_t5_valid_identity_agent_claimed_wrong_ref_denied(self, tmp_path):
        """T5：valid identity + agent 聲稱錯誤 ref → DENY（P1-C2 D2 防偽）。

        P1-C2 遷移：ref 由 Domain Core 從 final_message 計算（agent 不聲稱）。
        agent 聲稱了 ref 且 ≠ canonical → fail-closed（防偽語義保留，P1-B D4）。
        """
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        bogus_ref = "sha256:" + "0" * 64  # ≠ sha256(final_message) 的亂 claim
        claim_text = _claim_json(work_id, "researcher", refs=[bogus_ref])
        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="do not match the canonical ref"):
            execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before

    def test_t6_valid_identity_capability_artifact_passes(self, tmp_path):
        """T6：valid identity + valid capability + artifact → PASS（C2：ref 回填）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        claim_text = _claim_json(work_id, "researcher")  # 不聲稱 ref（D2）
        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        _, claim, event, _ = execute_work_dsh(
            orch, work_id, Role.RESEARCHER.value, "artifact.create",
            bridge, registry, store,
        )
        canonical = "sha256:" + hashlib.sha256(
            claim_text.encode("utf-8")
        ).hexdigest()
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert len(_log_rows(tmp_path)) == rows_before + 1
        assert claim.artifact_refs == [canonical]  # Domain Core 回填

    def test_t7_blocked_needs_input_no_artifact_gate(self, tmp_path):
        """T7：blocked / needs_input → 不觸發 artifact capability gate（M1）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        # Developer + artifact + BLOCKED：capability gate 不該觸發（M1 無產出）
        claim = _claim_json(work_id, "developer", refs=[], status="blocked")
        log_path = _make_session_log(
            tmp_path / "logs" / "dev" / "session.jsonl",
            cwd=dev_cwd,
            messages=[claim],
        )
        registry = _registry_with(tmp_path, ("developer", dev_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        _, claim_out, event, _ = execute_work_dsh(
            orch, work_id, Role.DEVELOPER.value, "artifact.create",
            bridge, registry, store,
        )
        assert claim_out.status == HandoffStatus.BLOCKED
        assert event.event_type == WorkEventType.STATE_TRANSITION
        assert len(_log_rows(tmp_path)) == rows_before + 1
        assert orch.synthesize(work_id).state == WorkState.BLOCKED

    def test_t8_decision_no_artifact_gate(self, tmp_path):
        """T8：decision → 不受 artifact capability gate 影響（2A §3.1）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[_claim_json(
                work_id, "researcher", result_type="decision",
                decision={"choice": "p1c1-decision"},
            )],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)

        _, claim, event, _ = execute_work_dsh(
            orch, work_id, Role.RESEARCHER.value, "decision",
            bridge, registry, store,
        )
        assert claim.result_type == ResultType.DECISION
        assert event.event_type == WorkEventType.DECISION_MADE
        work = orch.synthesize(work_id)
        assert {"choice": "p1c1-decision"} in work.decisions

    def test_t9_cwd_inside_data_root_invariant_failure(self, monkeypatch, tmp_path):
        """T9：cwd 落入 data_root → invariant failure（A3.2）。"""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
        reset_data_root()
        registry = RoleCwdRegistry()
        with pytest.raises(RoleCwdConflictError, match="data_root"):
            registry.register("researcher", str(tmp_path / "data" / "work" / "x"))

    def test_t10_role_cwd_overlap_invariant_failure(self, tmp_path):
        """T10：role cwd overlap → invariant failure（A3.1）。"""
        shared_cwd = str((tmp_path / "workspaces" / "shared").resolve())
        registry = RoleCwdRegistry()
        registry.register("researcher", shared_cwd)
        with pytest.raises(RoleCwdConflictError, match="already bound"):
            registry.register("developer", shared_cwd)

    def test_t11_adapter_wrong_log_path_fail_closed(self, tmp_path):
        """T11：adapter 提供錯誤 log path → fail-closed（無 durable write）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        # bridge 回報不存在的 log path（不存在 / 缺 header 都 fail-closed）
        bridge = _FakeBridge(tmp_path / "logs" / "missing" / "session.jsonl")
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="session evidence"):
            execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before

    def test_t12_header_cwd_mismatch_binding_fail_closed(self, tmp_path):
        """T12：session log header 與 expected binding 不符 → fail-closed。

        與 T3 的區別：T3 是「另一個已註冊 role 的 cwd」；T12 是「未註冊的
        陌生 cwd」——role_for 回 None，binding 驗證失敗。
        """
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())
        rogue_cwd = str((tmp_path / "rogue" / "elsewhere").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        log_path = _make_session_log(
            tmp_path / "logs" / "rogue" / "session.jsonl",
            cwd=rogue_cwd,  # 未註冊的 cwd
            messages=[_claim_json(work_id, "researcher")],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="identity binding failed"):
            execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before


# ─────────────────────────────────────────────
# Domain Core reader / registry / store 單元（C1.1/C1.3/C1.7 直接驗證）
# ─────────────────────────────────────────────

class TestExecutionEvidenceReader:
    def test_reader_parses_header_and_final_message(self, tmp_path):
        """reader 解析 header + 最後非空 assistant text。"""
        log_path = _make_session_log(
            tmp_path / "s" / "session.jsonl",
            cwd=str(tmp_path.resolve()),
            messages=["first", "", "final answer"],
        )
        evidence = read_execution_evidence(log_path)
        assert evidence.cwd == str(tmp_path.resolve())
        assert evidence.session_id == "session-test-0000"
        assert evidence.created_at == "1787500000000"
        assert evidence.final_message == "final answer"

    def test_reader_skips_empty_text_uses_last_nonempty(self, tmp_path):
        """非空才更新 final_message（與 headless summarize 語義一致）。"""
        log_path = _make_session_log(
            tmp_path / "s" / "session.jsonl",
            cwd=str(tmp_path.resolve()),
            messages=["a", "", "b", ""],
        )
        evidence = read_execution_evidence(log_path)
        assert evidence.final_message == "b"

    def test_reader_unknown_version_fail_closed(self, tmp_path):
        """未知 header.version → fail-closed（格式 drift 防線）。"""
        log_path = _make_session_log(
            tmp_path / "s" / "session.jsonl",
            cwd=str(tmp_path.resolve()),
            messages=["x"],
            version=SESSION_LOG_FORMAT_VERSION + 1,
        )
        with pytest.raises(ExecutionEvidenceError, match="version"):
            read_execution_evidence(log_path)

    def test_reader_missing_cwd_fail_closed(self, tmp_path):
        """缺 header cwd → fail-closed。"""
        log_path = tmp_path / "s" / "session.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = {
            "type": "session",
            "version": SESSION_LOG_FORMAT_VERSION,
            "id": "session-x",
            "createdAt": 1,
        }  # 缺 cwd
        log_path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        with pytest.raises(ExecutionEvidenceError, match="cwd"):
            read_execution_evidence(log_path)

    def test_reader_missing_file_fail_closed(self, tmp_path):
        """檔案不存在 → fail-closed。"""
        with pytest.raises(ExecutionEvidenceError):
            read_execution_evidence(tmp_path / "nope" / "session.jsonl")

    def test_reader_no_assistant_message_returns_empty(self, tmp_path):
        """沒有 assistant/message → final_message=""（fail-closed 由呼叫端決定）。"""
        log_path = _make_session_log(
            tmp_path / "s" / "session.jsonl",
            cwd=str(tmp_path.resolve()),
            messages=[],
            extra_events=[{"type": "turn/end", "seq": 1, "time": 1, "data": {"turn": 1, "reason": {"kind": "completed"}}}],
        )
        evidence = read_execution_evidence(log_path)
        assert evidence.final_message == ""


class TestRoleCwdRegistry:
    def test_register_lookup_roundtrip(self, tmp_path):
        res = str((tmp_path / "res").resolve())
        dev = str((tmp_path / "dev").resolve())
        registry = RoleCwdRegistry()
        registry.register("researcher", res)
        registry.register("developer", dev)
        assert registry.cwd_for("researcher") == res
        assert registry.role_for(res) == "researcher"
        assert registry.role_for(dev) == "developer"
        assert registry.cwd_for("unknown") is None
        assert registry.role_for(str(tmp_path / "unknown")) is None

    def test_register_idempotent_same_role_same_cwd(self, tmp_path):
        res = str((tmp_path / "res").resolve())
        registry = RoleCwdRegistry()
        registry.register("researcher", res)
        registry.register("researcher", res)  # no-op
        assert registry.role_for(res) == "researcher"

    def test_register_same_role_different_cwd_rejected(self, tmp_path):
        registry = RoleCwdRegistry()
        registry.register("researcher", str((tmp_path / "a").resolve()))
        with pytest.raises(RoleCwdConflictError, match="rebind"):
            registry.register("researcher", str((tmp_path / "b").resolve()))

    def test_verify_role_binding(self, tmp_path):
        res = str((tmp_path / "res").resolve())
        registry = RoleCwdRegistry()
        registry.register("researcher", res)
        good = read_execution_evidence(
            _make_session_log(
                tmp_path / "s" / "a.jsonl", cwd=res, messages=["ok"]
            )
        )
        bad = read_execution_evidence(
            _make_session_log(
                tmp_path / "s" / "b.jsonl", cwd=str((tmp_path / "dev").resolve()), messages=["ok"]
            )
        )
        assert registry.verify_role_binding("researcher", good) is True
        assert registry.verify_role_binding("researcher", bad) is False


class TestArtifactStore:
    def test_write_and_verify_roundtrip(self, tmp_path):
        store = ArtifactStore(data_dir=tmp_path)
        content = b"hello artifact"
        ref = store.write_artifact(content, DURABLE_WRITER)
        assert ref.startswith("sha256:")
        assert len(ref) == len("sha256:") + 64
        assert store.verify_artifact_ref(ref) is True
        # 同 content → 同 ref（dedup 冪等，不產生第二份）
        assert store.write_artifact(content, DURABLE_WRITER) == ref

    def test_write_atomic_no_partial(self, tmp_path):
        """write temp + atomic rename：canonical 路徑要嘛完整、要嘛不存在。"""
        store = ArtifactStore(data_dir=tmp_path)
        ref = store.write_artifact(b"atomic content", DURABLE_WRITER)
        digest = ref[len("sha256:"):]
        target = store.artifacts_dir / digest
        assert target.is_file()
        # 沒有殘留 temp
        assert list(store.artifacts_dir.glob("*.tmp")) == []

    def test_verify_invalid_refs(self, tmp_path):
        store = ArtifactStore(data_dir=tmp_path)
        assert store.verify_artifact_ref("sha256:zz") is False
        assert store.verify_artifact_ref("mock:sha256:abc") is False
        assert store.verify_artifact_ref("sha256:" + "a" * 64) is False  # 未寫入
        assert store.verify_artifact_ref("") is False

    def test_verify_hash_mismatch(self, tmp_path):
        store = ArtifactStore(data_dir=tmp_path)
        ref = store.write_artifact(b"content A", DURABLE_WRITER)
        digest = ref[len("sha256:"):]
        # 篡改 canonical 檔案內容 → hash 不符 → verify False
        (store.artifacts_dir / digest).write_bytes(b"tampered")
        assert store.verify_artifact_ref(ref) is False

    def test_single_writer_enforced(self, tmp_path):
        """只有 kernel（durable writer）能寫 artifact store（P1-B D3）。"""
        store = ArtifactStore(data_dir=tmp_path)
        with pytest.raises(NotDurableWriterError):
            store.write_artifact(b"x", "dsh_adapter")
        with pytest.raises(NotDurableWriterError):
            store.ingest_staging(tmp_path / "x", "dsh_session")

    def test_staging_ingest_and_cleanup(self, tmp_path):
        """staging → ingest（hash 驗證 + 原子寫入）→ 清理（P1-B §3.1 選項 B）。"""
        store = ArtifactStore(data_dir=tmp_path)
        staged = store.staging_dir() / "payload.bin"
        staged.write_bytes(b"staged content")
        ref = store.ingest_staging(staged, DURABLE_WRITER)
        assert store.verify_artifact_ref(ref) is True
        assert not staged.exists()  # ingest 後清理

    def test_ingest_rejects_outside_staging(self, tmp_path):
        store = ArtifactStore(data_dir=tmp_path)
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"x")
        with pytest.raises(Exception, match="outside staging"):
            store.ingest_staging(outside, DURABLE_WRITER)


class TestClaimRebuild:
    def test_strict_json(self):
        claim = rebuild_handoff_claim(
            '{"work_id": "w1", "role": "researcher", "result_type": "artifact", '
            '"artifact_refs": ["sha256:aa"], "evidence_refs": [], "decision": {}, '
            '"status": "done", "resume_hint": {}}'
        )
        assert claim.work_id == "w1"
        assert claim.role == "researcher"

    def test_fenced_json(self):
        claim = rebuild_handoff_claim(
            '```json\n{"work_id": "w1", "role": "researcher", "result_type": "decision", '
            '"decision": {"x": 1}, "status": "done"}\n```'
        )
        assert claim.result_type == ResultType.DECISION

    def test_lenient_bare_keys_and_values(self):
        """LLM 常見的 bare key/value（真 DSH 觀察到的行為）→ 容錯重建。"""
        claim = rebuild_handoff_claim(
            "{work_id: w-smoke, role: researcher, result_type: artifact, "
            "artifact_refs: [sha256:4dc39ea352d818652f4c7be99b406273a558e7dfe3d067b86f61d2bccdc9e434], "
            "evidence_refs: [], decision: {}, status: done, resume_hint: {}}"
        )
        assert claim.work_id == "w-smoke"
        assert claim.role == "researcher"
        assert claim.artifact_refs == [
            "sha256:4dc39ea352d818652f4c7be99b406273a558e7dfe3d067b86f61d2bccdc9e434"
        ]

    def test_unstructured_rejected(self):
        with pytest.raises(BridgeExecutionError, match="not a structured claim"):
            rebuild_handoff_claim("I did the work, here is my report.")

    def test_contract_mismatch_rejected(self):
        """結構合法但 contract 不符（缺 work_id）→ fail-closed。"""
        with pytest.raises(BridgeExecutionError, match="not a valid HandoffResult"):
            rebuild_handoff_claim('{"role": "researcher", "status": "done"}')


# ─────────────────────────────────────────────
# C1.9 — Real DSH smoke（環境不可用 → skip + 風險註記）
# ─────────────────────────────────────────────

@needs_real_dsh
class TestRealDshSmoke:
    def test_real_dsh_execution_three_layer_pass(self, tmp_path):
        """真 DSH headless execution 經三層驗證進 Domain Core（P1-C1 核心驗收，C2 語義）。

        真 DSH 環境可用時 PASS；不可用時 skip（見 needs_real_dsh 的風險註記）。

        P1-C2（D1/D2）語義：artifact content = final_message；agent **不聲稱
        ref**（task prompt 指示 artifact_refs=[]），canonical ref 由 Domain
        Core 從 final_message 計算、寫入 store、回填 claim——三層仍是真驗證：
        identity（真 cwd→role）/ capability（kernel gate）/ content（Domain
        Core 寫入 + 回填，claim→verify 語義自洽，無自指矛盾）。

        設計（2026-08-23 實測）：
        - 要求 agent 用工具算 sha256 的 task 會讓 headless agent 進入工具迴圈
          （曾 240s 不結束，已觀察到 agent 在 workspace 內反覆寫 probe 檔）。
          因此自動化 smoke 用**不呼叫工具**的 deterministic task：agent 直接
          產出文字內容 + 空 refs claim——identity（真 cwd→role）/ capability
          （kernel gate）/ content（Domain Core 回填）三層仍是真驗證。
        - **session_root 與 role_cwd 必須用短路徑**：dsh 的 Win32 durable
          publish（MoveFileExW via koffi）在合併路徑超過 ~260 chars 時會產生
          phantom session 目錄（可列舉但 stat 失敗、log 讀不到——實測復現）。
          production 預設 `$DSH_HOME/sessions-headless` 很短（安全）；測試
          用 TEMP 下的短目錄（不能放深層 tmp_path）。
        """
        orch = _orchestrator(tmp_path)
        work_id = _create_work(
            orch,
            objective=(
                "do not use any tools. Produce a short text report describing "
                "the Soul OS work status. Your final message must be the claim "
                "JSON with artifact_refs left empty."
            ),
        )

        import tempfile
        import uuid
        tag = uuid.uuid4().hex[:8]
        role_cwd = Path(tempfile.gettempdir()) / f"soul-p1c1-role-{tag}"
        role_cwd.mkdir(parents=True, exist_ok=True)
        res_cwd = str(role_cwd.resolve())
        session_root = Path(tempfile.gettempdir()) / f"soul-p1c1-sessions-{tag}"

        try:
            store = ArtifactStore(data_dir=tmp_path)
            registry = RoleCwdRegistry()
            registry.register("researcher", res_cwd)

            bridge = WorkExecutionBridge(
                session_root=session_root, dsh_timeout=180.0
            )

            message, claim, event, evidence = execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )

            # C2：canonical ref = sha256(final_message)，Domain Core 回填
            canonical = "sha256:" + hashlib.sha256(
                evidence.final_message.encode("utf-8")
            ).hexdigest()
            # 三層全過：identity（header.cwd→role）✓ capability（kernel）✓
            # content（Domain Core 寫入 + 回填）✓
            assert evidence.cwd == res_cwd
            assert claim.role == "researcher"
            assert claim.artifact_refs == [canonical]
            assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
            assert event.provenance.output_refs == [canonical]
            assert orch.synthesize(work_id).artifacts[0]["refs"] == [canonical]
            assert store.verify_artifact_ref(canonical)
            # task 確實經 bridge 送出（single_shot）
            assert message.message_type == BridgeMessageType.REQUEST
        finally:
            # 清理 TEMP 下的短暫 session root / role cwd（best effort）
            for p in (session_root, role_cwd):
                try:
                    for child in p.rglob("*"):
                        child.unlink(missing_ok=True)
                    p.rmdir()
                except OSError:
                    pass
