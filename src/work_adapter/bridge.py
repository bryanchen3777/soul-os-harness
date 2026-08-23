"""
src/work_adapter/bridge.py
DSH-P0-1 — Python ↔ TypeScript IPC bridge（Work Execution Adapter 的 Python 端）。

獨立模組，**不放進 src/work/ Domain Core**（避免污染零 DSH boundary）：
- Domain Core（src/work/）零 DSH import 永久鎖死；bridge 只 import Domain Core
  的 language-neutral contract（BridgeMessage / HandoffResult），Domain Core
  永不 import bridge。
- transport：spawn Node.js subprocess，request（BridgeMessage）寫 stdin（一行
  JSON），response（HandoffResult）讀 stdout（一行 JSON）。一 request 一
  response，EOF 即結束。
- serialization：language-neutral JSON（pydantic `model_dump_json` /
  `model_validate`），兩側 mirror 同一 contract（src/work/bridge.py 的
  BridgeMessage、src/work/schema.py 的 HandoffResult 是 authoritative source）。
- failure isolation：non-zero exit / timeout / malformed JSON / 缺 node /
  缺 adapter script → 拋 BridgeExecutionError；bridge 不寫任何 durable state
  （adapter 只 read/write stdin/stdout，無 durable write authority），
  DSH 失敗不污染 durable truth。

單一真相（2D §1）：bridge 只 transport/invoke，durable write 一律回 Domain
Core（WorkflowOrchestrator.consume_handoff → kernel.record_handoff）。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.work.bridge import BridgeMessage
from src.work.schema import HandoffResult

# repo root = src/work_adapter/bridge.py 往上三層（work_adapter → src → root）
_DEFAULT_ADAPTER_SCRIPT = (
    Path(__file__).resolve().parents[2] / "dsh_adapter" / "soul-dsh-adapter.mjs"
)


class BridgeExecutionError(RuntimeError):
    """bridge execution 失敗（crash / timeout / malformed / 缺 runtime）。

    DSH 失敗隔離：抛錯時**不寫任何 durable state**。呼叫端（execution path）
    收到此錯只能選擇重試或記錄，不能把未驗證的結果寫進 durable log。
    """


class WorkExecutionBridge:
    """Python ↔ TypeScript bridge：BridgeMessage in → HandoffResult out。

    只 transport/invoke（spawn node subprocess + JSON serialization）。
    無 durable write authority：本 class 不寫任何檔案/durable store。

    Args:
        node_bin: Node.js 可執行檔（預設 "node"，從 PATH 解析）。
        adapter_script: TS adapter script 路徑（預設
            <repo>/dsh_adapter/soul-dsh-adapter.mjs）。
        timeout: subprocess 逾時秒數（秒）。逾時 → kill + BridgeExecutionError。
    """

    def __init__(
        self,
        *,
        node_bin: str = "node",
        adapter_script: Path | str | None = None,
        timeout: float = 10.0,
    ):
        self._node_bin = node_bin
        self._adapter_script = (
            Path(adapter_script)
            if adapter_script is not None
            else _DEFAULT_ADAPTER_SCRIPT
        )
        self._timeout = timeout

    # ── transport / invoke ──

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
