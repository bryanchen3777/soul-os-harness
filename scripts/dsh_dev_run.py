"""
scripts/dsh_dev_run.py — Soul OS Multi-Agent Development Loop entrypoint（DSH-DEV-ENV-0 S1/S2/S3）。

用法：
    python scripts/dsh_dev_run.py <role> <task>

role ∈ {researcher, developer, tester}（S2：三 role config 內建於本檔，是
開發環境配置，不是 Soul OS contract）。

流程（S1）：
    create_work(Chief) → assign(role) → execute_work_dsh(...) → 印出結果
    （work_id / WorkEvent / artifact ref / fold 摘要）。

失敗（S3 fail-closed，不寫 durable WorkEvent）→ 非零 exit + 明確錯誤類別：
    exit 2  用法錯誤（缺參數 / 未知 role / 空 task）
    exit 3  infra（BridgeExecutionError：crash / timeout / malformed / 缺 dsh）
    exit 4  契約（CapabilityNotAuthorizedError：role 無 capability）→ 不重跑，
            升級給 pro/Owner
    exit 5  claim/evidence（claim 畸形 / header 不符 / 驗證失敗——含
            ExecutionEvidenceError，與 BridgeExecutionError 訊息命中 claim
            關鍵字的分類）
    exit 1  未分類錯誤

重跑語義（S3）：每次執行 = 新 work（create_work → 新 work_id），**不是**
resume（resume 是 P1-D 範疇）。durable truth 保護 = 內容定址（同 content →
同 ref，write_artifact 檢查既有檔回傳）+ fail-closed-before-write。

staging 清理（S3）：只清 *.tmp 孤兒（write temp + atomic rename crash 的
真垃圾）；staged-but-uningested（非 *.tmp）是 pending，**不清**（P1-B §3.1
選項 B：crash 後由重跑/ingest 閉合）。

環境變數（可選）：
    SOUL_OS_DATA_DIR         durable truth 位置（預設 data_root() 即 ./data）
    SOUL_OS_DSH_SESSION_ROOT headless session log root（預設 $DSH_HOME/sessions-headless）
    SOUL_OS_DSH_TIMEOUT      dsh headless 逾時秒數（預設 300）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 控制台編碼防護：Windows console 可能是 cp950/GBK，印不出某些 Unicode
# （如簡體中文）會讓 print 拋 UnicodeEncodeError → 把 stdout/stderr 強制
# UTF-8 + errors=replace，任何 codepage 下輸出都不 crash（顯示層容錯，
# durable 內容仍是正確 UTF-8）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.paths import data_root
from src.work.artifact_store import ArtifactStore
from src.work.execution_evidence import ExecutionEvidenceError, RoleCwdRegistry
from src.work.kernel import WorkKernel
from src.work.roles import CapabilityNotAuthorizedError, Role
from src.work.workflow import WorkflowOrchestrator
from src.work_adapter import BridgeExecutionError, WorkExecutionBridge
from src.work_adapter.execution import execute_work_dsh

# ─────────────────────────────────────────────
# S2 — 三 role config（開發環境配置，非 Soul OS contract）
# ─────────────────────────────────────────────

ROLE_CONFIG: dict[str, dict] = {
    "researcher": {
        "capability": "artifact.create",
        "cwd": ROOT / "workspaces" / "researcher",
        "prompt_template": (
            "You are the Researcher for Soul OS. Analyze the task below and "
            "produce a text artifact: root cause analysis + concrete proposal. "
            "Do not use any tools. Task: {task}"
        ),
        "output_expectation": "text artifact (analysis / report / research findings)",
    },
    "developer": {
        "capability": "artifact.create",
        "cwd": ROOT / "workspaces" / "developer",
        "prompt_template": (
            "You are the Developer for Soul OS. Produce a text artifact that "
            "implements or designs the task below. Do not use any tools. "
            "Task: {task}"
        ),
        "output_expectation": "text artifact (implementation / design / change proposal)",
    },
    "tester": {
        "capability": "evidence.create",
        "cwd": ROOT / "workspaces" / "tester",
        "prompt_template": (
            "You are the Tester for Soul OS. Verify the artifact ref given in "
            "the task and produce an evidence verdict. Do not use any tools. "
            "Your evidence_refs must list the verified artifact ref "
            "(sha256:<hex>) from the task. Task: {task}"
        ),
        "output_expectation": "evidence (verification verdict on a given artifact ref)",
    },
}

# BridgeExecutionError 訊息分類關鍵字（claim/evidence 類，其餘歸 infra）。
# execute_work_dsh 把 ExecutionEvidenceError 包進 BridgeExecutionError
# （execution.py C1.6 的 except ... from），entrypoint 依訊息拆出 claim 類
# 供 README Q7 runbook 處置。
_CLAIM_ERROR_MARKERS = (
    "session evidence",
    "not a structured claim",
    "not a valid HandoffResult",
    "identity binding failed",
    "claim.role",
    "mis-routed",
    "do not match the canonical ref",
    "content verification",
    "P1-C2 D3",
    "result_type mismatch",
)

# ─────────────────────────────────────────────
# S3 — operational resilience
# ─────────────────────────────────────────────


def _cleanup_orphan_tmp(store: ArtifactStore) -> int:
    """只清 *.tmp 孤兒（atomic rename crash 的殘留）；staged-but-uningested 不清。

    staging 語義（P1-B §3.1 選項 B）：`*.tmp` = 真垃圾（canonical 路徑從未
    出現）；`staged-but-uningested` = pending（crash 後由重跑/ingest 閉合）。
    當前 staging 未 wire production，無害；語義標籤正確即可。
    """
    removed = 0
    for directory in (store.artifacts_dir, store.staging_dir()):
        if not directory.is_dir():
            continue
        for p in directory.glob("*.tmp"):
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def _classify_error(exc: BridgeExecutionError) -> tuple[int, str]:
    """BridgeExecutionError → (exit code, category)。訊息命中 claim 關鍵字
    → claim 類（exit 5）；其餘 → infra 類（exit 3）。"""
    msg = str(exc)
    if any(marker in msg for marker in _CLAIM_ERROR_MARKERS):
        return 5, "claim"
    return 3, "infra"


def _fail(code: int, category: str, exc: BaseException, *, hint: str) -> int:
    print(f"error[{code}] {category}: {exc}", file=sys.stderr)
    print(f"hint: {hint}", file=sys.stderr)
    print(
        "(fail-closed: 執行結果未寫入 durable（無 artifact/evidence event；"
        "既有 create/assign 簿記除外）。重跑 = 新 work，不是 resume)",
        file=sys.stderr,
    )
    return code


# ─────────────────────────────────────────────
# S1 — run entrypoint
# ─────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            f"usage: python {Path(__file__).name} <role> <task>\n"
            f"       role ∈ {{{', '.join(sorted(ROLE_CONFIG))}}}",
            file=sys.stderr,
        )
        return 2
    role, task = args
    if role not in ROLE_CONFIG:
        print(
            f"error[2] usage: unknown role {role!r}; "
            f"expected one of {sorted(ROLE_CONFIG)}",
            file=sys.stderr,
        )
        return 2
    task = task.strip()
    if not task:
        print("error[2] usage: empty task", file=sys.stderr)
        return 2

    cfg = ROLE_CONFIG[role]
    capability = cfg["capability"]

    try:
        data_dir = data_root() / "work"
        orchestrator = WorkflowOrchestrator(kernel=WorkKernel(data_dir=data_dir))
        store = ArtifactStore(data_dir=data_dir)

        # S3：staging/orphan 清理（只清 *.tmp 孤兒）
        removed = _cleanup_orphan_tmp(store)
        if removed:
            print(f"[cleanup] removed {removed} orphan *.tmp file(s)")

        # S1：RoleCwdRegistry 註冊三 role cwd（A3：disjoint + ∉ data_root，
        # register 強制；任一違反 → RoleCwdConflictError → exit 1）
        registry = RoleCwdRegistry()
        for r, rcfg in ROLE_CONFIG.items():
            registry.register(r, str(rcfg["cwd"]))

        # S1：WorkExecutionBridge（session_root / timeout 可經 env 覆寫；
        # env 驗證在 create_work 前——usage 錯誤不寫任何 durable 簿記）
        bridge_kwargs: dict = {}
        session_root = os.environ.get("SOUL_OS_DSH_SESSION_ROOT")
        if session_root:
            bridge_kwargs["session_root"] = session_root
        timeout_raw = os.environ.get("SOUL_OS_DSH_TIMEOUT")
        if timeout_raw:
            try:
                bridge_kwargs["dsh_timeout"] = float(timeout_raw)
            except ValueError:
                print(
                    f"error[2] usage: SOUL_OS_DSH_TIMEOUT={timeout_raw!r} is not a number",
                    file=sys.stderr,
                )
                return 2
        bridge = WorkExecutionBridge(**bridge_kwargs)

        # S1：create_work(Chief) → assign(role)（每次執行 = 新 work）
        objective = cfg["prompt_template"].format(task=task)
        work_id = orchestrator.create_work(objective, Role.CHIEF)
        orchestrator.assign(work_id, role)

        print(f"[run] work_id={work_id} role={role} capability={capability}")
        preview = objective if len(objective) <= 200 else objective[:200] + "..."
        print(f"[run] objective: {preview}")
        print("[run] executing real DSH headless ...")

        message, claim, event, evidence = execute_work_dsh(
            orchestrator, work_id, role, capability, bridge, registry, store,
        )
    except CapabilityNotAuthorizedError as exc:
        return _fail(
            4, "contract", exc,
            hint="role 無此 capability（契約）；不重跑，升級給 pro/Owner",
        )
    except ExecutionEvidenceError as exc:
        return _fail(
            5, "claim", exc,
            hint="claim/evidence 畸形或 header 不符；檢查 task 是否超出 role 能力",
        )
    except BridgeExecutionError as exc:
        code, category = _classify_error(exc)
        hint = (
            "檢查 task 是否超出 role 能力（claim 畸形 / 驗證對象不存在）"
            if category == "claim"
            else "重跑或檢查 DSH 環境（dsh CLI / credential / session log 可讀）"
        )
        return _fail(code, category, exc, hint=hint)
    except Exception as exc:  # noqa: BLE001 — entrypoint 邊界：任何失敗都轉非零 exit
        return _fail(1, "unknown", exc, hint="未分類錯誤（見 traceback）")

    _print_result(orchestrator, work_id, role, capability, claim, event, evidence)
    return 0


def _print_result(
    orchestrator: WorkflowOrchestrator,
    work_id: str,
    role: str,
    capability: str,
    claim,
    event,
    evidence,
) -> None:
    """印出結果（ref / WorkEvent / fold 摘要）。"""
    print("\n=== result ===")
    print(f"work_id     : {work_id}")
    print(f"role        : {role}")
    print(f"capability  : {capability}")
    print(f"session_id  : {evidence.session_id}")
    print(f"session cwd : {evidence.cwd}")
    print(f"event_type  : {event.event_type.value}")
    print(
        f"provenance  : role={event.provenance.role} "
        f"capability={event.provenance.capability} "
        f"output_refs={event.provenance.output_refs}"
    )
    if claim.status.value != "done":
        print(f"status      : {claim.status.value} resume_hint={claim.resume_hint}")
    for ref in claim.artifact_refs:
        print(f"ARTIFACT REF: {ref}")
    for ref in claim.evidence_refs:
        print(f"EVIDENCE REF: {ref}  (被驗證對象)")
    work = orchestrator.synthesize(work_id)
    print(f"work state  : {work.state.value}")
    print(
        f"fold        : artifacts={len(work.artifacts)} "
        f"evidence={len(work.evidence)} decisions={len(work.decisions)}"
    )
    print("\n(done; rerun = new work, not resume)")


if __name__ == "__main__":
    sys.exit(main())
