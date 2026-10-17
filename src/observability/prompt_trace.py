"""src/observability/prompt_trace.py — 端到端 Prompt / 參數觀測（OBS-CHRONO-1）

**本模組存在的理由**
三個問題目前只能靠「讀程式碼 + 偶爾的對話體感」猜：
  1. 這一次呼叫到底帶了哪個 temperature、走的是哪條路（route）？
  2. `[CHRONO_SOCIAL_CONTEXT v2.2]` 真的有組裝並進到 System prompt 嗎？
  3. `[最近內在生活]`（日記／夢境）與 SAGE 檢索記憶真的有夾帶進 payload 嗎？
三者都已用生產日誌實測為**全盲**：`CHRONO_SOCIAL_CONTEXT`、`[最近內在生活]`、
`你記得以下這些事情` 在 `data/logs/server_nohup*` 中出現次數**均為 0**。

**落點為什麼在 proxy 而不是 console**
Owner 2026-10-16 裁示：Soul OS 有大量 headless 背景流量（Telegram 機器人、
夜間日記／夢境生成、無介面 agent 迴圈）。任何依賴「有人開著介面」的觀測點都會
漏掉背景流量，所以 hook 放在 `LLMProxy._handle_event_impl` 送出前那一點
（`src/llm/proxy.py` 第 3853 行附近，**所有**後處理完成、`_complete_with_retry`
呼叫之前），覆蓋角色扮演與事件生成兩條路徑。

**永遠不外洩對話內容**
本檔**只寫元資料**：presence／長度／12 字元 hash 前綴／時間中繼資料。
使用者原話、SAGE 記憶內容、日記夢境原文**一律不落盤**。
`tests/observability/test_prompt_trace.py::test_sentinel_user_text_never_hits_disk`
以哨兵字串鎖死這個不變量——那是本機制存在的核心理由。

**風格沿用 `src/memory/continuity.py`（repo 既有的 prompt 側觀測慣例）**
  - 開關式啟停（env），預設關閉
  - 命名空間 logger，失敗只 `logger.warning`，**絕不讓觀測拖垮主流程**
  - schema 常數 + 每列帶 `schema_version`
  - 凍結禁令：sidecar **不得** import LLM client（本檔沒有任何 LLM 相依）
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:  # pragma: no cover - import 形狀差異只在缺 src.paths 時發生
    from src.paths import data_root
except Exception:  # pragma: no cover
    def data_root() -> Path:  # type: ignore[misc]
        return Path("data").resolve()


SCHEMA_VERSION = "prompt_trace_v1"
ENV_FLAG = "SOULOS_PROMPT_TRACE"
TRUTHY = {"1", "true", "yes", "on"}
TRACE_DIRNAME = "observability"
TRACE_FILENAME = "prompt_trace.jsonl"
MAX_BYTES = 5 * 1024 * 1024  # 與 repo 其他 observability 檔案同級的體量上限

# ── 被觀測的 block 標頭（逐字字面值，勿臆測修改）────────────────────────
#   [CHRONO_SOCIAL_CONTEXT v2.2] / [/CHRONO_SOCIAL_CONTEXT]
#     → src/temporal/render.py:18 / :28
#   [最近內在生活]
#     → src/llm/proxy.py:802（private builder）與 :1444（group builder）
#   你記得以下這些事情:
#     → src/llm/proxy.py:761（private）與 :1403（group）＝ SAGE 檢索記憶
BLOCK_CHRONO_OPEN = "[CHRONO_SOCIAL_CONTEXT"
BLOCK_CHRONO_CLOSE = "[/CHRONO_SOCIAL_CONTEXT]"
BLOCK_INNER_LIFE = "[最近內在生活]"
BLOCK_SAGE_MEMORY = "你記得以下這些事情:"

# 測試用注入點：None = 讀環境變數；True/False = 強制覆寫。
_FORCE_OVERRIDE: Optional[bool] = None

try:
    import logging

    logger = logging.getLogger("soul_os.llm.trace")
except Exception:  # pragma: no cover
    logger = None  # type: ignore[assignment]


def set_trace_enabled(enabled: Optional[bool]) -> None:
    """測試／嵌入端注入開關；傳 None 回復成讀環境變數。"""
    global _FORCE_OVERRIDE
    _FORCE_OVERRIDE = enabled


def trace_enabled() -> bool:
    """是否啟用。**預設關閉**（env 未設或不在真值集合內）。

    真值集合刻意放寬到 1/true/yes/on：觀測開關常在重啟前手動設，寧可寬鬆接受，
    也不要出現「設了卻沒生效」的靜默失敗——那正是本機制要消滅的那類問題。
    """
    if _FORCE_OVERRIDE is not None:
        return bool(_FORCE_OVERRIDE)
    return os.environ.get(ENV_FLAG, "").strip().lower() in TRUTHY


def trace_path() -> Path:
    return Path(data_root()) / TRACE_DIRNAME / TRACE_FILENAME


def _sha12(text: str) -> str:
    """12 字元 hash 前綴：可判斷「是否同一份內容」，但不可還原。"""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:12]


def _system_text(messages: Iterable[Dict[str, Any]]) -> str:
    return "\n".join(
        str(m.get("content") or "")
        for m in messages
        if isinstance(m, dict) and m.get("role") == "system"
    )


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _chrono_fields(system_text: str) -> Dict[str, Any]:
    """從 chrono 區塊取出**非敏感**的時間中繼資料。

    time_period / silence_hours 是 payload 裡本來就有的行為偏置欄位，
    不是個人資料；而且它們正是「時段感知到底生效沒」的可驗證證據。
    """
    start = system_text.find(BLOCK_CHRONO_OPEN)
    if start < 0:
        return {}
    end = system_text.find(BLOCK_CHRONO_CLOSE, start)
    block = system_text[start:end if end > start else len(system_text)]
    out: Dict[str, Any] = {}
    for line in block.splitlines():
        if line.startswith("time_period="):
            out["time_period"] = line.split("=", 1)[1].strip()
        elif line.startswith("silence="):
            raw = line.split("=", 1)[1].strip().rstrip("h")
            try:
                out["silence_hours"] = float(raw)
            except ValueError:
                pass
    return out


def _block_presence(system_text: str) -> Dict[str, Dict[str, Any]]:
    """每個關鍵 block 的 presence / len / hash。**絕不記原文**。"""
    def slice_between(open_marker: str, close_marker: Optional[str]) -> str:
        i = system_text.find(open_marker)
        if i < 0:
            return ""
        j = system_text.find(close_marker, i) if close_marker else -1
        return system_text[i:j if j > i else len(system_text)]

    chrono = slice_between(BLOCK_CHRONO_OPEN, BLOCK_CHRONO_CLOSE)
    inner_life = slice_between(BLOCK_INNER_LIFE, None)
    sage = slice_between(BLOCK_SAGE_MEMORY, None)

    blocks: Dict[str, Dict[str, Any]] = {
        "chrono": {"present": bool(chrono), "len": len(chrono), "sha256_12": _sha12(chrono)},
        "inner_life": {"present": bool(inner_life), "len": len(inner_life), "sha256_12": _sha12(inner_life)},
        "sage_memory": {"present": bool(sage), "len": len(sage), "sha256_12": _sha12(sage)},
    }
    if chrono:
        blocks["chrono"].update(_chrono_fields(system_text))
    return blocks


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_suffix(".jsonl.1"))
    except Exception:  # pragma: no cover
        pass


def trace_prompt(
    messages: List[Dict[str, Any]],
    *,
    agent_id: str,
    route: str,
    temperature: Optional[float],
    model: Optional[str] = None,
    provider: Optional[str] = None,
    mode: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """記錄一次 LLM 呼叫的**元資料**。

    設計約束（Owner 2026-10-16）：
      - **預設關閉時熱路徑成本 = 一個 `if` 判斷 + 一次 env 讀取快取**（O(1)、零 IO）。
      - **任何例外都不得上拋**，觀測失敗只記 warning。
      - **永不寫入對話原文**。
    """
    if not trace_enabled():
        return
    try:
        system_text = _system_text(messages)
        user_text = _last_user_text(messages)
        row = {
            "schema_version": SCHEMA_VERSION,
            "ts": time.time(),
            "agent_id": agent_id,
            "route": route,
            "mode": mode,
            "reason": reason,
            "temperature": temperature,
            "model": model,
            "provider": provider,
            "n_messages": len(messages),
            "system_prompt_len": len(system_text),
            "system_prompt_sha256_12": _sha12(system_text),
            "user_prompt_len": len(user_text),
            "user_prompt_sha256_12": _sha12(user_text),
            "blocks": _block_presence(system_text),
        }
        path = trace_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - 觀測不得拖垮主流程
        if logger is not None:
            logger.warning("[prompt_trace] 寫入失敗（已忽略）: %s", exc)
