"""
src/work/execution_evidence.py
Execution evidence + role→cwd identity binding（DSH P1-C1，C1.1–C1.3）。

Domain Core 維護 role → cwd canonical mapping（純路徑資料，無 DSH id），
並**自行開檔**讀 DSH 的 on-disk session log 驗證 cwd（decomposition A1 / T1）。

格式契約（重要）：
- 本模組**零 DSH import**——不 import 任何 DSH type / id。
- 但它讀的檔案格式是 **DSH 的 on-disk session log 格式**，這是「已知的格式契約」
  （decomposition §1.1：header = `{type, version, id, createdAt, cwd, ...}`，
  event row = JSONL；`assistant/message` 的 `data.message.content` text blocks
  是 LLM 最終產出）。格式知識寫在 docstring 與常數，不是 language-neutral。
- `SESSION_LOG_FORMAT_VERSION` 驗證是格式 drift 防線：未知 version → fail-closed。

Trust model（decomposition §0.1）：Domain Core 開檔讀 header，仍擋不住「惡意
adapter 偽造整個 log 檔案」——那是信任根極限（adapter 是 Soul OS 自己的可信
transport 程式碼）。本模組防的是**惡意 LLM 偽造 role**：LLM 在文字裡自稱
Researcher 改不了 header.cwd；只有真的在該 role cwd 跑的 session 才驗得過。

A3 invariants（decomposition §1.4，可測試）：
1. role cwd 彼此 disjoint（`RoleCwdRegistry.register` 拒絕兩 role 同 cwd）。
2. role cwd ∉ data_root 樹（workspace 與 durable truth 分離，P1-B D7）。
3. harness home 與 role cwd disjoint 且 ∉ tool scope——本模組零 harness-home
   概念（grep 零該字串於 src/work/），DSH 側由 adapter 的 --patch overlay 落地。

No-DSH Survival：fold/authorize/resume/persist 不 import 本模組、不要求
session log 存在；本模組只在 execution 流程被 adapter 側呼叫。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from src.paths import data_root

# ─────────────────────────────────────────────
# DSH on-disk session log 格式契約（已知格式契約，非 import）
# ─────────────────────────────────────────────

# DSH session log 的格式版本（dsh-session 的 SESSION_FORMAT_VERSION）。
# header line 的 `version` 必須 == 此值；未知版本 → fail-closed（格式 drift 防線）。
SESSION_LOG_FORMAT_VERSION = 0

# header line 的 type tag（dsh-session-persistence-jsonl 的 toHeaderLine）。
SESSION_HEADER_TYPE = "session"

# 最終產出事件 type（headless summarize 語義：最後一個非空 text block）。
ASSISTANT_MESSAGE_TYPE = "assistant/message"


class ExecutionEvidence(BaseModel):
    """Domain Core 從 session log 自行解析出的 execution 證據（全 str）。

    由 `read_execution_evidence` 產生；`final_message` = 最後一個非空
    assistant text（與 headless runner `summarize()` 語義一致）。
    """

    cwd: str
    session_id: str
    created_at: str
    final_message: str


class ExecutionEvidenceError(RuntimeError):
    """session log 讀取/解析失敗（缺 header / 缺 cwd / 未知 version / malformed）。

    fail-closed：呼叫端（execution path）收到此錯只能重試或記錄，
    不能把未驗證的結果寫進 durable log。
    """


class RoleCwdConflictError(ValueError):
    """role→cwd invariant 違反（兩 role 同 cwd / cwd 落在 data_root 樹內）。"""


class RoleCwdRegistry:
    """Domain Core 維護的 role → cwd canonical mapping（adapter 只讀）。

    純路徑資料（2A §8.2：不存 DSH id；MA-1 §9.2：sessionId 只作 reference）。
    A3 invariants：
    - 兩 role 同 cwd → `RoleCwdConflictError`（一目錄 → 一 role）。
    - cwd 落在 `data_root()` 樹內 → `RoleCwdConflictError`（workspace 與
      durable truth 分離，P1-B D7）。
    """

    def __init__(self) -> None:
        self._role_to_cwd: dict[str, str] = {}
        self._cwd_to_role: dict[str, str] = {}

    @staticmethod
    def _normalize(cwd: str) -> str:
        return str(Path(cwd).resolve())

    def register(self, role: str, cwd: str) -> None:
        """註冊 role → cwd 綁定；違反 A3 invariant → `RoleCwdConflictError`。

        相同 role 重新註冊到**不同** cwd → 拒絕（避免靜默 rebind 造成
        binding 塌陷）。相同 role + 相同 cwd 重複註冊是 no-op（冪等）。
        """
        role = str(role)
        cwd_norm = self._normalize(cwd)

        existing_cwd = self._role_to_cwd.get(role)
        if existing_cwd is not None and existing_cwd != cwd_norm:
            raise RoleCwdConflictError(
                f"role={role!r} is already bound to cwd={existing_cwd!r}; "
                f"cannot rebind to cwd={cwd_norm!r}"
            )
        existing_role = self._cwd_to_role.get(cwd_norm)
        if existing_role is not None and existing_role != role:
            raise RoleCwdConflictError(
                f"cwd={cwd_norm!r} is already bound to role={existing_role!r}; "
                f"two roles sharing one cwd would collapse identity binding "
                f"(A3.1)"
            )
        root = data_root().resolve()
        if self._is_within(cwd_norm, root):
            raise RoleCwdConflictError(
                f"role cwd={cwd_norm!r} must not be inside data_root={root!r} "
                f"(workspace must stay disjoint from durable truth, A3.2 / P1-B D7)"
            )

        self._role_to_cwd[role] = cwd_norm
        self._cwd_to_role[cwd_norm] = role

    @staticmethod
    def _is_within(path: str, root: str) -> bool:
        return Path(path).is_relative_to(Path(root))

    def cwd_for(self, role: str) -> str | None:
        """回傳 role 綁定的 cwd（未註冊 → None）。"""
        return self._role_to_cwd.get(str(role))

    def role_for(self, cwd: str) -> str | None:
        """回傳 cwd 綁定的 role（未註冊 → None）。"""
        return self._cwd_to_role.get(self._normalize(cwd))

    def verify_role_binding(self, role: str, evidence: ExecutionEvidence) -> bool:
        """identity binding 驗證：`role_for(evidence.cwd) == role`。

        這是 A1 的核心：LLM 自稱 role 改不了 header.cwd；只有真的在該
        role cwd 跑的 session 才驗得過。失敗 → 呼叫端 fail-closed。
        """
        return self.role_for(evidence.cwd) == str(role)


# ─────────────────────────────────────────────
# Session-log reader（Domain Core 自行開檔）
# ─────────────────────────────────────────────

def read_execution_evidence(log_path: Path | str) -> ExecutionEvidence:
    """開檔讀 DSH session log，解析出 `ExecutionEvidence`（fail-closed）。

    1. 讀第一行 header（`{type, version, id, createdAt, cwd, ...}`）。
    2. 驗 header.version（未知 → `ExecutionEvidenceError`，格式 drift 防線）。
    3. 遍歷 event rows，找最後一個非空 `assistant/message` 的 text
       （`data.message.content` 的 text blocks join；與 headless
       `summarize()` 語義一致）。
    4. 缺 header / 缺 cwd / JSON 解析失敗 → `ExecutionEvidenceError`。

    Args:
        log_path: session log 的絕對路徑（adapter 只回報絕對路徑，
            不轉述 header 值——T1 決策）。

    Raises:
        ExecutionEvidenceError: 任何解析/格式問題（fail-closed）。
    """
    path = Path(log_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExecutionEvidenceError(
            f"cannot read session log {path}: {exc}"
        ) from exc

    if not lines:
        raise ExecutionEvidenceError(f"empty or header-less session log: {path}")

    header = _parse_header(lines[0], path)
    # 注意：header 驗證後才開始逐行掃 events；header 那行不算 event。
    final_message = _extract_final_assistant_message(lines[1:], path)

    return ExecutionEvidence(
        cwd=header["cwd"],
        session_id=header["id"],
        created_at=header["createdAt"],
        final_message=final_message,
    )


def _parse_header(first_line: str, path: Path) -> dict[str, Any]:
    """解析並驗證 header line（fail-closed）。"""
    try:
        header = json.loads(first_line)
    except json.JSONDecodeError as exc:
        raise ExecutionEvidenceError(
            f"corrupt session log: header line is not valid JSON ({path}): {exc}"
        ) from exc

    if not isinstance(header, dict):
        raise ExecutionEvidenceError(
            f"corrupt session log: header line is not an object ({path})"
        )

    # 格式 drift 防線：未知 version → fail-closed（先於其他欄位檢查）。
    version = header.get("version")
    if version != SESSION_LOG_FORMAT_VERSION:
        raise ExecutionEvidenceError(
            f"unsupported session log format version={version!r} "
            f"(expected {SESSION_LOG_FORMAT_VERSION}); log format drift "
            f"defense failed closed ({path})"
        )

    if header.get("type") != SESSION_HEADER_TYPE:
        raise ExecutionEvidenceError(
            f"corrupt session log: header type={header.get('type')!r} is not "
            f"{SESSION_HEADER_TYPE!r} ({path})"
        )

    session_id = header.get("id")
    cwd = header.get("cwd")
    created_at = header.get("createdAt")
    if not isinstance(session_id, str) or not session_id:
        raise ExecutionEvidenceError(
            f"corrupt session log: header missing string 'id' ({path})"
        )
    if not isinstance(cwd, str) or not cwd:
        raise ExecutionEvidenceError(
            f"corrupt session log: header missing string 'cwd' ({path})"
        )
    if created_at is None:
        raise ExecutionEvidenceError(
            f"corrupt session log: header missing 'createdAt' ({path})"
        )
    return {
        "type": header["type"],
        "version": version,
        "id": session_id,
        "createdAt": str(created_at),
        "cwd": cwd,
    }


def _extract_final_assistant_message(event_lines: list[str], path: Path) -> str:
    """遍歷 event rows，回傳最後一個非空 assistant text（可能為空字串）。

    與 headless `summarize()` 一致：`assistant/message` 事件的
    `data.message.content` 中所有 `type == "text"` block 的 text join，
    非空才更新；`reasoning` 等非 text block 忽略。
    沒有非空 assistant message → 回傳 ""（fail-closed 由呼叫端決定）。
    """
    final: str = ""
    for line in event_lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # malformed 非 header row：跳過（與 DSH SessionLogScanner 的
            # corrupt-row 容忍一致）；但若它是資料行而非空白，記錄為格式漂移。
            continue
        if not isinstance(row, dict) or row.get("type") != ASSISTANT_MESSAGE_TYPE:
            continue
        text = _join_text_blocks(row)
        if text:
            final = text
    return final


def _join_text_blocks(row: dict[str, Any]) -> str:
    """取 `assistant/message` 的 text blocks 並 join；格式不符 → ""。"""
    data = row.get("data")
    if not isinstance(data, dict):
        return ""
    message = data.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    joined = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )
    return joined
