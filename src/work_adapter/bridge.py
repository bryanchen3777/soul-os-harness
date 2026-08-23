"""
src/work_adapter/bridge.py
DSH-P0-1 — Python ↔ TypeScript IPC bridge（Work Execution Adapter 的 Python 端）。
DSH P1-C1 — 升級：真 DSH headless execution（C1.4）。

獨立模組，**不放進 src/work/ Domain Core**（避免污染零 DSH boundary）：
- Domain Core（src/work/）零 DSH import 永久鎖死；bridge 只 import Domain Core
  的 language-neutral contract（BridgeMessage / HandoffResult），Domain Core
  永不 import bridge。
- transport（script 面，P0-1 保留）：spawn Node.js subprocess，request
  （BridgeMessage）寫 stdin（一行 JSON），response（HandoffResult）讀 stdout
  （一行 JSON）。一 request 一 response，EOF 即結束。這是 mock/scripted
  adapter 通道（regression 測試沿用）。
- transport（DSH 面，P1-C1 新增）：`execute_dsh` spawn
  `dsh --profile headless "<task>"`，`cwd=` 依 role 設定（binding 錨點）。
  - headless runner 硬編碼 `sessionId=randomUUID()` + `meta.cwd=process.cwd()`
    （dsh-headless/lib/index.js），**sessionId 不可控，但 cwd 可控**。
  - --patch overlay 注入（不用全域 `$DSH_HOME/cordis.patch.yml`，避免污染
    其他 profile）：
    1. `session-persistence-jsonl`：`compression: none`（預設 zstd，檔名
       `.jsonl.zstd`，Domain Core reader 讀不到明文）+ 專屬 `root`
       （`$DSH_HOME/sessions-headless/`——shared root 有其他 profile 的
       zstd artifacts，compression 切換會撞 `encodingMismatch`）。
    2. `sandbox-policy`：`mode: workspace-write` + `workspaceRoot` = role cwd。
       **tool scope 的實際機制是 `sandboxPolicy` service**
       （`ctx.sandboxPolicy.resolve()` 的 workspaceRoot = session.header.cwd =
       spawn cwd = role cwd），不是 tool plugin 的 config 欄位
       （dsh-tool-bash 的 Config 只有 enableRunInBackground；dsh-tool-fs /
       dsh-fs-sandbox 用 writableRoots(policy) = [workspaceRoot, /tmp, tmpdir]）。
       role cwd 與 DSH_HOME / data_root disjoint（A3）→ agent 無法寫
       DSH_HOME 與 data_root（篡改自己的 session log header）。
    3. `permission`：disabled。settings.yaml 的 `permission.defaultPreset:
       danger-full-access` 會在每個新 session 上 pin `sandbox/mode:
       danger-full-access` event（dsh-permission-presets.pinInitialPermission），
       override 掉 workspace-write——disable 後 session 無 override，
       resolve() 落回 deployment default（= overlay 的 workspace-write）。
    4. `approval`（dsh-user-approval，P1-C2 D6）：`config.policy: never`。
       DSH 枚舉值 `ApprovalPolicy = 'ask' | 'never'`（實讀 dsh-user-approval
       types），`never` = 確定性 rejected（approval.request() 在 dispatch
       waterfall 前直接回 "rejected"）——headless/CI 的 fail-fast deny 姿態，
       避免 base approval policy "ask" 在無 answerer 的 headless session 上
       escalation hang（C1.9 觀察到）。`permission` disabled 後不會 pin
       `approval/policy` event，effective policy 落回 plugin config =
       never。
  - **log 路徑定位（事後讀回，sessionId 不可控）**：
    1. spawn 前記錄 `<session_root>/<projectKey(cwd)>/` 下既有的 session
       目錄清單（projectKey = dsh-session-persistence-jsonl 的 on-disk
       project 目錄編碼）。
    2. run 後 diff 找出唯一新增的 session 目錄（`<encode(id)>/`），取其
       `session.jsonl` 絕對路徑。
    3. 新增目錄不唯一（同 cwd 並行執行）→ fail-closed（時序歧義不可接受）。
    4. bridge 只回報絕對路徑，**不轉述 header 值**（decomposition T1：
       Domain Core 自行開檔讀 header）。
  - **Windows 長路徑注意（2026-08-23 實測）**：dsh 的 Win32 durable publish
    （MoveFileExW via koffi）在 `session_root + project_dir + session_dir`
    合併路徑超過 ~260 chars 時會產生 phantom session 目錄（可列舉但 stat
    失敗、`session.jsonl` 讀不到）。`_session_dirs` 的 is_dir() 對 phantom
    回 False → 本方法 fail-closed（正確行為：證據不可讀 = 執行失敗）。生產
    預設 session_root（`$DSH_HOME/sessions-headless`，很短）與短 role cwd
    不會觸發；呼叫端若自訂深層 session_root 需自行控制路徑長度。
- failure isolation：non-zero exit / timeout / malformed / 缺 runtime /
  缺 dsh / log 定位失敗 → 拋 BridgeExecutionError；bridge 不寫任何 durable
  state（adapter 只 transport，無 durable write authority），DSH 失敗不
  污染 durable truth。

單一真相（2D §1）：bridge 只 transport/invoke，durable write 一律回 Domain
Core（WorkflowOrchestrator.consume_handoff）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from src.work.bridge import BridgeMessage
from src.work.kernel import result_type_for_capability
from src.work.schema import HandoffResult

# repo root = src/work_adapter/bridge.py 往上三層（work_adapter → src → root）
_DEFAULT_ADAPTER_SCRIPT = (
    Path(__file__).resolve().parents[2] / "dsh_adapter" / "soul-dsh-adapter.mjs"
)

# DSH on-disk session log 的專屬 root（與 shared root 分離，避免
# compression 切換撞其他 profile 的 zstd artifacts）。
_DEFAULT_SESSION_ROOT_NAME = "sessions-headless"


class BridgeExecutionError(RuntimeError):
    """bridge execution 失敗（crash / timeout / malformed / 缺 runtime /
    缺 dsh / log 定位失敗）。

    DSH 失敗隔離：抛錯時**不寫任何 durable state**。呼叫端（execution path）
    收到此錯只能選擇重試或記錄，不能把未驗證的結果寫進 durable log。
    """


class WorkExecutionBridge:
    """Python ↔ TypeScript bridge：BridgeMessage in → HandoffResult out。

    script 面（`execute`）：spawn node subprocess + JSON serialization。
    DSH 面（`execute_dsh`）：spawn `dsh --profile headless` + --patch overlay
    + 事後讀回 session log 路徑。

    無 durable write authority：本 class 不寫任何 durable store。

    Args:
        node_bin: Node.js 可執行檔（預設 "node"，從 PATH 解析；script 面用）。
        adapter_script: TS adapter script 路徑（預設
            <repo>/dsh_adapter/soul-dsh-adapter.mjs；script 面用）。
        timeout: script 面 subprocess 逾時秒數。逾時 → kill + BridgeExecutionError。
        dsh_bin: dsh 指令（預設 "dsh"）；.cmd/.bat shim 會解析回
            node <dsh 套件>/lib/bin.js（npm shim 的實際行為）。
        dsh_home: DSH_HOME（預設 env DSH_HOME，無則 ~/.dsh）；會傳給子程序。
        session_root: headless session log 的專屬 root（預設
            <dsh_home>/sessions-headless）。測試可指向 temp dir。
        dsh_timeout: DSH 面 subprocess 逾時秒數（真實 LLM run 較久）。
    """

    def __init__(
        self,
        *,
        node_bin: str = "node",
        adapter_script: Path | str | None = None,
        timeout: float = 10.0,
        dsh_bin: str = "dsh",
        dsh_home: Path | str | None = None,
        session_root: Path | str | None = None,
        dsh_timeout: float = 300.0,
    ):
        self._node_bin = node_bin
        self._adapter_script = (
            Path(adapter_script)
            if adapter_script is not None
            else _DEFAULT_ADAPTER_SCRIPT
        )
        self._timeout = timeout
        self._dsh_bin = dsh_bin
        self._dsh_home = Path(
            dsh_home
            if dsh_home is not None
            else os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))
        )
        self._session_root = (
            Path(session_root)
            if session_root is not None
            else self._dsh_home / _DEFAULT_SESSION_ROOT_NAME
        )
        self._dsh_timeout = dsh_timeout

    # ── script 面（P0-1，mock/scripted adapter） ──

    def execute(self, message: BridgeMessage) -> HandoffResult:
        """送出一個 BridgeMessage(request) → 讀回 HandoffResult。

        失敗（缺 node / 缺 adapter / crash / timeout / malformed / 不合
        HandoffResult contract）→ 一律拋 BridgeExecutionError，不寫任何
        durable state。
        """
        if not self._adapter_script.exists():
            raise BridgeExecutionError(
                f"adapter script not found: {self._adapter_script}"
            )

        request_json = message.model_dump_json()
        try:
            proc = subprocess.Popen(
                [self._node_bin, str(self._adapter_script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # binary I/O：decode 一律在主執行緒做。text=True 時 decode 發生在
                # communicate 的 reader thread，UnicodeDecodeError 會被 thread 吞掉
                # （只留 unraisable warning），呼叫端捕不到；binary + 主執行緒
                # strict decode 才能讓 UnicodeDecodeError 落在下面同層級的 except。
            )
        except (OSError, ValueError) as exc:
            # OSError：node 不在 PATH / 無法執行
            raise BridgeExecutionError(
                f"failed to spawn adapter ({self._node_bin!r}): {exc}"
            ) from exc

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                input=request_json.encode("utf-8"), timeout=self._timeout
            )
            stdout = stdout_bytes.decode("utf-8")
            stderr = stderr_bytes.decode("utf-8", errors="replace")  # 診斷用，不 fail
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise BridgeExecutionError(
                f"adapter timed out after {self._timeout}s "
                f"(script={self._adapter_script.name})"
            )
        except UnicodeDecodeError as exc:
            # M3（P1-Preflight）：非 UTF-8 stdout 的 decode 失敗 → fail closed，
            # 統一收斂成 BridgeExecutionError（不冒 UnicodeDecodeError），
            # 不寫任何 durable state。
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            raise BridgeExecutionError(
                f"adapter output is not valid UTF-8 "
                f"(script={self._adapter_script.name}): {exc}"
            ) from exc

        if proc.returncode != 0:
            raise BridgeExecutionError(
                f"adapter crashed (exit={proc.returncode}, "
                f"script={self._adapter_script.name}): {stderr.strip()[:500]}"
            )

        stdout = stdout.strip()
        if not stdout:
            raise BridgeExecutionError(
                f"adapter returned empty stdout (script={self._adapter_script.name})"
            )

        try:
            data: Any = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BridgeExecutionError(
                f"adapter returned malformed JSON: {exc}"
            ) from exc

        try:
            return HandoffResult.model_validate(data)
        except Exception as exc:  # pydantic ValidationError → contract 不符
            raise BridgeExecutionError(
                f"adapter returned invalid HandoffResult: {exc}"
            ) from exc

    # ── DSH 面（P1-C1：真 DSH headless execution） ──

    def execute_dsh(self, message: BridgeMessage, role_cwd: str) -> str:
        """spawn `dsh --profile headless "<task>"`（cwd=role_cwd），回報 session log 絕對路徑。

        Args:
            message: BridgeMessage(request)（payload 帶 work_id / objective /
                role / capability——task text 由本方法組裝）。
            role_cwd: 該 role 的專屬 cwd（identity binding 錨點；DSH 把
                process.cwd() 寫進 session header）。

        Returns:
            session log 的**絕對路徑**（`<session_root>/<projectKey(cwd)>/
            <encode(id)>/session.jsonl`）。不轉述任何 header 值。

        Raises:
            BridgeExecutionError: 缺 dsh / spawn 失敗 / crash / timeout /
                新增 session 目錄不唯一或不存在 / log 檔案不存在。
        """
        role_cwd = str(Path(role_cwd).resolve())
        Path(role_cwd).mkdir(parents=True, exist_ok=True)

        project_dir = self._session_root / _project_key(role_cwd)
        before = _session_dirs(project_dir)

        overlay_path = self._write_overlay(role_cwd)
        try:
            self._spawn_dsh(message, role_cwd, overlay_path)
        finally:
            overlay_path.unlink(missing_ok=True)

        after = _session_dirs(project_dir)
        new_dirs = sorted(after - before)
        if len(new_dirs) != 1:
            raise BridgeExecutionError(
                f"expected exactly one new DSH session dir under {project_dir}, "
                f"found {len(new_dirs)}: {new_dirs} (concurrent executions in "
                f"the same role cwd are ambiguous — fail closed)"
            )
        log_path = project_dir / new_dirs[0] / "session.jsonl"
        if not log_path.is_file():
            raise BridgeExecutionError(
                f"DSH session log not found at {log_path} (session dir "
                f"{new_dirs[0]!r} exists but has no session.jsonl)"
            )
        return str(log_path)

    # ── DSH 面 internal ──

    def _write_overlay(self, role_cwd: str) -> Path:
        """寫出本次 execution 的 --patch overlay（temp file，用完即刪）。

        config 是**整段取代**（dsh-app-boot applyEntryPatches 的 patch
        語義），所以 `session-persistence-jsonl` 的 `root` 必須重述。
        """
        session_root_posix = self._session_root.as_posix()
        role_cwd_posix = Path(role_cwd).as_posix()
        overlay = (
            "# DSH P1-C2 headless execution overlay (identity-safe session "
            "log + confined tool scope + fail-fast approval deny)\n"
            f"- id: session-persistence-jsonl\n"
            f"  config:\n"
            f"    root: \"{session_root_posix}\"\n"
            f"    compression: none\n"
            f"- id: sandbox-policy\n"
            f"  config:\n"
            f"    mode: workspace-write\n"
            f"    workspaceRoot: \"{role_cwd_posix}\"\n"
            f"- id: approval\n"
            f"  config:\n"
            f"    policy: never\n"
            f"- id: permission\n"
            f"  disabled: true\n"
        )
        fd, path = tempfile.mkstemp(prefix="dsh-p1c1-overlay-", suffix=".yml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(overlay)
        except Exception:
            os.unlink(path)
            raise
        return Path(path)

    def _spawn_dsh(self, message: BridgeMessage, role_cwd: str, overlay_path: Path) -> None:
        """spawn dsh headless 並等待完成；任何失敗 → BridgeExecutionError。"""
        task = _build_task_text(message)
        invocation = self._resolve_dsh_invocation()
        env = dict(os.environ)
        env["DSH_HOME"] = str(self._dsh_home)
        try:
            proc = subprocess.Popen(
                [*invocation, "--profile", "headless", "--patch", str(overlay_path), task],
                cwd=role_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except (OSError, ValueError) as exc:
            raise BridgeExecutionError(
                f"failed to spawn dsh headless ({invocation!r}): {exc}"
            ) from exc

        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=self._dsh_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise BridgeExecutionError(
                f"dsh headless timed out after {self._dsh_timeout}s "
                f"(role_cwd={role_cwd!r})"
            )
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            raise BridgeExecutionError(
                f"dsh headless crashed (exit={proc.returncode}, "
                f"role_cwd={role_cwd!r}): {stderr.strip()[:500]}"
            )
        # exit 0 代表 run 完成（headless runner 只在 reason.kind == completed
        # 時 exit 0）；stdout 是 final assistant text，非 contract——最終產出
        # 以 session log 為準（B1），此處不回傳。

    def _resolve_dsh_invocation(self) -> list[str]:
        """解析 dsh 指令為可 spawn 的 argv。

        Windows npm shim 是 .cmd/.bat（CreateProcess 無法直接跑），解析回
        npm shim 的實際行為：`node <npm root>/node_modules/@deepseek-ai/dsh/
        lib/bin.js`。
        """
        resolved = shutil.which(self._dsh_bin)
        if resolved is None:
            raise BridgeExecutionError(
                f"dsh not found on PATH: {self._dsh_bin!r} "
                f"(install the dsh CLI or pass dsh_bin=)"
            )
        if resolved.lower().endswith((".cmd", ".bat")):
            shim_dir = Path(resolved).resolve().parent
            pkg_bin = shim_dir / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
            node = shutil.which(self._node_bin)
            if node is None or not pkg_bin.is_file():
                raise BridgeExecutionError(
                    f"dsh is an npm shim ({resolved}) but cannot resolve "
                    f"{pkg_bin} or node {self._node_bin!r}"
                )
            return [node, str(pkg_bin)]
        return [resolved]


# ─────────────────────────────────────────────
# DSH on-disk 路徑編碼（adapter 側格式知識；Domain Core 不碰）
# ─────────────────────────────────────────────

_SAFE_CHAR_RE = re.compile(r"[A-Za-z0-9._-]")


def _project_key(cwd: str) -> str:
    """mirror dsh-session-persistence-jsonl 的 `projectKey(cwd)` 編碼。

    separator（/ \\ :）run → `-`；unsafe code unit → `~XXXX`；包 `--...--`，
    去前導 `-`，截 251 chars（empty → "root"）。讓 adapter 能在
    `<session_root>/` 下定位該 role cwd 的 project 目錄。
    """
    if not cwd:
        raise ValueError("cannot encode an empty project path")
    readable: list[str] = []
    separator_run = False
    for ch in cwd:
        if ch in "/\\:":
            if not separator_run:
                readable.append("-")
            separator_run = True
        elif ch != "~" and _SAFE_CHAR_RE.fullmatch(ch):
            readable.append(ch)
            separator_run = False
        else:
            readable.append(f"~{ord(ch):04X}")
            separator_run = False
    joined = "".join(readable)
    joined = re.sub(r"^-+", "", joined) or "root"
    return f"--{joined[:251]}--"


def _session_dirs(project_dir: Path) -> set[str]:
    """project 目錄下既有的 session 目錄（`<encode(id)>/`）名稱集合。"""
    if not project_dir.is_dir():
        return set()
    return {entry.name for entry in project_dir.iterdir() if entry.is_dir()}


def _build_task_text(message: BridgeMessage) -> str:
    """把 BridgeMessage(request) 組裝成 headless task text（single_shot）。

    要求 LLM 的 final message 是結構化 claim JSON（HandoffResult 形狀），
    Domain Core 從 session log 讀回後重建 claim（C1.6）。claim 模板的
    result_type 由 request capability 推導（Domain Core canonical：
    result_type_for_capability），與 M2 anchor 一致。

    P1-C2（D1/D2）：artifact 分支的 artifact_refs 模板改為 []——ref 由
    Domain Core 從 final_message 計算回填（agent 不聲稱，自指矛盾）；
    evidence 分支保留 evidence_refs 指向**被驗證對象**的語義。
    """
    payload = message.payload
    work_id = payload.get("work_id", "")
    objective = payload.get("objective", "")
    role = payload.get("role", "")
    capability = payload.get("capability", "")
    result_type = result_type_for_capability(capability).value
    if result_type == "artifact":
        claim_form = (
            f'"artifact_refs": [], "evidence_refs": [], '
            f'"decision": {{}}'
        )
        guidance = (
            "Your final message text IS the artifact content; the system "
            "computes its content-addressed ref from that text and records "
            "it for you — do NOT compute or list any sha256 refs, leave "
            "artifact_refs empty."
        )
    elif result_type == "evidence":
        claim_form = (
            f'"artifact_refs": [], "evidence_refs": ["sha256:<hex>"], '
            f'"decision": {{}}'
        )
        guidance = (
            "evidence_refs lists the content-addressed ref of the artifact "
            "you verified (the object under test), not a ref of your own "
            "message — leave artifact_refs empty."
        )
    else:  # decision
        claim_form = (
            f'"artifact_refs": [], "evidence_refs": [], '
            f'"decision": {{"choice": "..."}}'
        )
        guidance = "Leave artifact_refs and evidence_refs empty."
    return (
        f"You are the {role} agent for Soul OS work {work_id}. "
        f"Objective: {objective}. Capability: {capability}. "
        f"Execute the work now. When you are done, your FINAL message must be "
        f"EXACTLY a single JSON object with no markdown fences and no "
        f"surrounding text, of the form: "
        f'{{"work_id": "<work_id>", "role": "<your role>", '
        f'"result_type": "{result_type}", {claim_form}, '
        f'"status": "done", "resume_hint": {{}}}}. '
        f"{guidance} "
        f'If you cannot complete the work, use "status": "blocked" with a '
        f"resume_hint explaining why."
    )
