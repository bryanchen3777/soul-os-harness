"""
tests/test_work_p1c2_integration.py
Soul OS — DSH P1-C2：Integration / Boundary Gate（content transport + 三層 deny path）。

對照 logs/DSH-P1-C2-WORK-ORDER.md：
- D1/D2：artifact content = final_message；ref 由 Domain Core 寫入 store 並回填
  claim（agent 不聲稱 ref）；agent 聲稱且 ≠ canonical → fail-closed（防偽）。
- D3：回填後空 refs → fail-closed（artifact 產出必須有至少一個非空 content ref）。
- D4：evidence_refs 指向**被驗證對象**，逐一 verify（存在性 + hash）；evidence
  自己的文字不寫成 artifact；verdict 不 machine-check（已知限制）。
- D5：execute_work（mock 面）deprecated（DeprecationWarning + docstring）。
- D6：headless overlay 追加 approval policy = never（DSH 枚舉值，fail-fast deny）。

E2E 情境（工單 §3，六項）：
1. Researcher artifact E2E（fake-log 閉環；真 DSH 版本見 TestRealDshClosedLoop）
2. Developer artifact DENY（P1-C0 capability）
3. 借殼 DENY（identity binding）
4. 回填後空 refs DENY（D3）
5. agent 聲稱錯誤 ref DENY（D2 防偽）
6. Tester evidence E2E（D4）

真 DSH 環境可用時（dsh CLI + LLM credential）額外跑真 headless 閉環；不可用時
skip（風險註記見 needs_real_dsh）。

執行：pytest tests/test_work_p1c2_integration.py -v
"""
import hashlib
import json
import shutil
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.paths import reset_data_root
from src.work.artifact_store import ArtifactStore
from src.work.bridge import DURABLE_WRITER, BridgeMessage, BridgeMessageType
from src.work.execution_evidence import (
    SESSION_LOG_FORMAT_VERSION,
    RoleCwdRegistry,
)
from src.work.kernel import WorkKernel
from src.work.roles import CapabilityNotAuthorizedError, Role
from src.work.schema import (
    HandoffResult,
    HandoffStatus,
    ResultType,
    WorkEventType,
)
from src.work.workflow import WorkflowOrchestrator
from src.work_adapter import BridgeExecutionError, WorkExecutionBridge
from src.work_adapter.bridge import _build_task_text
from src.work_adapter.execution import execute_work, execute_work_dsh


# ─────────────────────────────────────────────
# 真 DSH 環境偵測（不可用 → skip + 風險註記）
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
        "real-headless E2E skipped — risk note: content transport closed loop "
        "not validated against real DSH"
    ),
)


# ─────────────────────────────────────────────
# Helpers（同 P1-C1 fake-log 模式）
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_data_root():
    """每個測試前後重設 data_root cache（SOUL_OS_DATA_DIR 變更隔離）。"""
    reset_data_root()
    yield
    reset_data_root()


def _orchestrator(tmp_path) -> WorkflowOrchestrator:
    return WorkflowOrchestrator(kernel=WorkKernel(data_dir=tmp_path))


def _create_work(
    orch: WorkflowOrchestrator,
    objective: str = "build feature X",
    assign: Role = Role.DEVELOPER,
) -> str:
    work_id = orch.create_work(objective, Role.CHIEF)
    orch.assign(work_id, assign)
    return work_id


def _make_session_log(
    log_path: Path,
    *,
    cwd: str,
    messages: list[str],
    session_id: str = "session-p1c2-0000",
    created_at: int = 1787500000000,
    version: int = SESSION_LOG_FORMAT_VERSION,
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
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def _claim_json(
    work_id: str,
    role: str,
    result_type: str = "artifact",
    artifact_refs: list[str] | None = None,
    evidence_refs: list[str] | None = None,
    status: str = "done",
    decision: dict | None = None,
) -> str:
    return json.dumps({
        "work_id": work_id,
        "role": role,
        "result_type": result_type,
        "artifact_refs": artifact_refs or [],
        "evidence_refs": evidence_refs or [],
        "decision": decision or {},
        "status": status,
        "resume_hint": {},
    })


class _FakeBridge:
    """fake DSH bridge：execute_dsh 直接回報 log 路徑（fake-log 單元測試）。"""

    def __init__(self, log_path: Path):
        self.log_path = str(log_path)
        self.last_role_cwd: str | None = None
        self.last_message: BridgeMessage | None = None

    def execute_dsh(self, message: BridgeMessage, role_cwd: str) -> str:
        self.last_message = message
        self.last_role_cwd = role_cwd
        return self.log_path


class _MockBridge:
    """mock bridge（script 面）：execute() 直接回傳預設 HandoffResult（D5 測試用）。"""

    def __init__(self, handoff: HandoffResult):
        self._handoff = handoff
        self.last_message: BridgeMessage | None = None

    def execute(self, message: BridgeMessage) -> HandoffResult:
        self.last_message = message
        return self._handoff


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


def _canonical_ref(final_message: str) -> str:
    """Domain Core 的 canonical ref = sha256(final_message)（D1/D2）。"""
    return "sha256:" + hashlib.sha256(final_message.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────
# E2E 情境（工單 §3 六項，fake-log 模式）
# ─────────────────────────────────────────────

class TestE2EClosedLoop:
    def test_1_researcher_artifact_e2e_closed_loop(self, tmp_path):
        """E2E-1：Researcher artifact 閉環——final_message 進 store → ref 回填
        → WorkEvent → fold 出 artifact（D1/D2 content transport）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        claim_text = _claim_json(work_id, "researcher")  # 不聲稱 ref
        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        _, claim, event, evidence = execute_work_dsh(
            orch, work_id, Role.RESEARCHER.value, "artifact.create",
            bridge, registry, store,
        )

        canonical = _canonical_ref(evidence.final_message)
        assert canonical == _canonical_ref(claim_text)
        # content transport：final_message 真的落盤（content 定址）
        assert store.verify_artifact_ref(canonical)
        assert (
            store.artifact_path(canonical).read_bytes()
            == claim_text.encode("utf-8")
        )
        # ref 回填（Domain Core 算，agent 不聲稱）
        assert claim.artifact_refs == [canonical]
        # WorkEvent + fold
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
        assert event.provenance.output_refs == [canonical]
        assert len(_log_rows(tmp_path)) == rows_before + 1
        assert orch.synthesize(work_id).artifacts == [{"refs": [canonical]}]

    def test_2_developer_artifact_denied_capability(self, tmp_path):
        """E2E-2：Developer 產 artifact → P1-C0 capability gate 拒絕（無半寫入）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        claim_text = _claim_json(work_id, "developer")
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
        # DENY 不寫 durable work log（無半寫入）
        assert len(_log_rows(tmp_path)) == rows_before

    def test_3_role_spoof_in_other_cwd_denied(self, tmp_path):
        """E2E-3：借殼——role=Researcher 但 session 真的在 Developer cwd 跑
        → identity binding 拒絕。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())
        dev_cwd = str((tmp_path / "workspaces" / "developer").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        log_path = _make_session_log(
            tmp_path / "logs" / "dev" / "session.jsonl",
            cwd=dev_cwd,  # process 事實：developer cwd
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

    def test_4_empty_refs_after_backfill_denied(self, tmp_path, monkeypatch):
        """E2E-4：artifact + done + Domain Core 回填後仍無有效 ref → D3 拒絕。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        # fault injection：write_artifact 回傳空 ref（正常 case 不可能，
        # 這是回填下限的防禦測試——空產出 = 無效 artifact）
        monkeypatch.setattr(store, "write_artifact", lambda content, actor: "")

        log_path = _make_session_log(
            tmp_path / "logs" / "res" / "session.jsonl",
            cwd=res_cwd,
            messages=[_claim_json(work_id, "researcher")],
        )
        registry = _registry_with(tmp_path, ("researcher", res_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="P1-C2 D3"):
            execute_work_dsh(
                orch, work_id, Role.RESEARCHER.value, "artifact.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before

    def test_5_agent_claimed_wrong_ref_denied(self, tmp_path):
        """E2E-5：agent 聲稱錯誤 ref（≠ Domain Core 算的 canonical）→ D2 防偽拒絕。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        res_cwd = str((tmp_path / "workspaces" / "researcher").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        bogus_ref = "sha256:" + "0" * 64  # 亂 claim，≠ sha256(final_message)
        claim_text = _claim_json(work_id, "researcher", artifact_refs=[bogus_ref])
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

    def test_6_tester_evidence_e2e_verifies_objects(self, tmp_path):
        """E2E-6：Tester 產 evidence → evidence_refs 指向**被驗證對象** → 逐一
        verify（存在性 + hash，D4）；evidence 自己的文字不寫成 artifact。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch, assign=Role.TESTER)
        tester_cwd = str((tmp_path / "workspaces" / "tester").resolve())

        store = ArtifactStore(data_dir=tmp_path)
        # 前置：被驗證的 artifact 已落盤（Tester 聲稱「我驗證了 artifact X」）
        verified_ref = store.write_artifact(b"artifact under test", DURABLE_WRITER)

        claim_text = _claim_json(
            work_id, "tester", result_type="evidence",
            evidence_refs=[verified_ref],
        )
        log_path = _make_session_log(
            tmp_path / "logs" / "tester" / "session.jsonl",
            cwd=tester_cwd,
            messages=[claim_text],
        )
        registry = _registry_with(tmp_path, ("tester", tester_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        _, claim, event, _ = execute_work_dsh(
            orch, work_id, Role.TESTER.value, "evidence.create",
            bridge, registry, store,
        )
        assert claim.result_type == ResultType.EVIDENCE
        assert claim.evidence_refs == [verified_ref]
        assert event.event_type == WorkEventType.EVIDENCE_PRODUCED
        assert event.provenance.output_refs == [verified_ref]
        assert len(_log_rows(tmp_path)) == rows_before + 1
        # D4 語義：evidence 自己的文字**不** write_artifact（store 只有被驗證對象）
        assert sorted(p.name for p in store.artifacts_dir.iterdir()) == [
            verified_ref[len("sha256:"):]
        ]

    def test_6b_evidence_bogus_ref_denied(self, tmp_path):
        """E2E-6 deny：evidence_refs 指向不存在的 ref → D4 fail-closed。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch, assign=Role.TESTER)
        tester_cwd = str((tmp_path / "workspaces" / "tester").resolve())

        store = ArtifactStore(data_dir=tmp_path)  # 空的（無被驗證對象）
        bogus = "sha256:" + "1" * 64
        log_path = _make_session_log(
            tmp_path / "logs" / "tester" / "session.jsonl",
            cwd=tester_cwd,
            messages=[_claim_json(
                work_id, "tester", result_type="evidence",
                evidence_refs=[bogus],
            )],
        )
        registry = _registry_with(tmp_path, ("tester", tester_cwd))
        bridge = _FakeBridge(log_path)
        rows_before = len(_log_rows(tmp_path))

        with pytest.raises(BridgeExecutionError, match="content verification"):
            execute_work_dsh(
                orch, work_id, Role.TESTER.value, "evidence.create",
                bridge, registry, store,
            )
        assert len(_log_rows(tmp_path)) == rows_before


# ─────────────────────────────────────────────
# D5 / D6 契約測試
# ─────────────────────────────────────────────

class TestContractGates:
    def test_d5_execute_work_mock_face_deprecated(self, tmp_path):
        """D5：execute_work（mock 面）發出 DeprecationWarning（docstring 亦標 deprecated）。"""
        orch = _orchestrator(tmp_path)
        work_id = _create_work(orch)
        handoff = HandoffResult(
            work_id=work_id,
            role=Role.RESEARCHER.value,
            result_type="artifact",
            artifact_refs=["mock:sha256:x"],
            status="done",
        )
        bridge = _MockBridge(handoff)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            message, out_handoff, event = execute_work(
                orch, work_id, Role.RESEARCHER.value, "artifact.create", bridge
            )
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)
        assert event.event_type == WorkEventType.ARTIFACT_PRODUCED

    def test_d6_overlay_contains_approval_policy_never(self, tmp_path):
        """D6：headless --patch overlay 含 approval policy = never（DSH 枚舉值，fail-fast deny）。"""
        bridge = WorkExecutionBridge(session_root=tmp_path / "sessions")
        role_cwd = str((tmp_path / "workspaces" / "researcher").resolve())
        overlay_path = bridge._write_overlay(role_cwd)
        try:
            text = overlay_path.read_text(encoding="utf-8")
        finally:
            overlay_path.unlink(missing_ok=True)
        assert "- id: approval" in text
        assert "policy: never" in text
        # 既有 overlay 內容仍在（identity-safe session log + confined tool scope）
        assert "session-persistence-jsonl" in text
        assert "sandbox-policy" in text
        assert "workspace-write" in text

    def test_d2_task_text_artifact_no_ref_claim(self):
        """D2：artifact task text 指示 agent 不聲稱 ref（移除 list refs 要求）；
        evidence 分支保留指向被驗證對象的語義。"""
        base = BridgeMessage(
            message_type=BridgeMessageType.REQUEST,
            actor="researcher",
            source="soul_kernel",
            payload={
                "work_id": "w-p1c2",
                "objective": "produce a report",
                "role": "researcher",
                "capability": "artifact.create",
                "execution_shape": "single_shot",
                "resume_state": {},
            },
        )
        artifact_text = _build_task_text(base)
        assert '"artifact_refs": []' in artifact_text
        assert "do NOT compute or list any sha256 refs" in artifact_text
        assert "sha256:<hex>" not in artifact_text  # 模板不再示範填 ref

        evidence_msg = base.model_copy(deep=True)
        evidence_msg.payload["capability"] = "evidence.create"
        evidence_text = _build_task_text(evidence_msg)
        assert '"evidence_refs": ["sha256:<hex>"]' in evidence_text  # 保留被驗證對象語義


# ─────────────────────────────────────────────
# 真 DSH 閉環（環境可用時；不可用 → skip + 風險註記）
# ─────────────────────────────────────────────

@needs_real_dsh
class TestRealDshClosedLoop:
    def test_real_dsh_artifact_closed_loop(self, tmp_path):
        """真 DSH headless：Researcher 產 artifact → Domain Core 算 ref 回填 →
        WorkEvent → fold（P1-C2 核心驗收，content transport 真閉環）。

        注意：session_root 與 role_cwd 必須用短路徑（Win32 durable publish
        長路徑 phantom 目錄問題，同 P1-C1 實測）。
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
        role_cwd = Path(tempfile.gettempdir()) / f"soul-p1c2-role-{tag}"
        role_cwd.mkdir(parents=True, exist_ok=True)
        res_cwd = str(role_cwd.resolve())
        session_root = Path(tempfile.gettempdir()) / f"soul-p1c2-sessions-{tag}"

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

            canonical = _canonical_ref(evidence.final_message)
            # 三層：identity ✓ capability ✓ content（Domain Core 寫入 + 回填）✓
            assert evidence.cwd == res_cwd
            assert claim.role == "researcher"
            assert claim.artifact_refs == [canonical]
            assert event.event_type == WorkEventType.ARTIFACT_PRODUCED
            assert event.provenance.output_refs == [canonical]
            assert orch.synthesize(work_id).artifacts[0]["refs"] == [canonical]
            assert store.verify_artifact_ref(canonical)
            assert (
                store.artifact_path(canonical).read_bytes()
                == evidence.final_message.encode("utf-8")
            )
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
