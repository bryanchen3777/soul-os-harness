"""LIFE-THREAD-BOOTSTRAP-1：冷啟動引導（bootstrap wake）—— 契約 §4.4 的**第三條喚醒路徑**。

**性質宣告（三條，逐字可驗）**：

1. **純標準庫**（`json` / `os` / `tempfile` / `pathlib` / `typing`），**0 個 `src.*` import**
   —— 本模組**不得**、也**未** import M3（`life_thread_wake_gate`）；
   `ACTIVE_POOL_SATURATED_REASON` 是**本模組自帶的字串常數**，只是與 M3 的字面值相同，
   兩者**無 import 相依**（M3 為凍結面，見契約 §4.4「不得觸碰」）。
2. **匯入時 0 I/O**：模組層只有常數賦值（＋ `frozenset(...)` 這個純建構子），
   **不**讀檔案、**不**讀環境變數、**不**建目錄、**不**寫任何東西。
3. **永不 raise（fail-closed / fail-quiet）**：所有公開函式在**任何**輸入下都不得拋例外。
   方向一律是「**不 bootstrap**」—— 讀不到標記 ⇒ 視為「已設」（不花錢）；
   寫不進去 ⇒ 回 `False`（無法保證 at-most-once ⇒ 不執行）。

**與契約 §4.4 的對應**（`docs/LIFE-THREAD-ENGINE-CONTRACT.md`）：

| 本模組 | 契約條文 |
|---|---|
| `BOOTSTRAP_ENABLED_ENV` ＝ `LIFE_THREAD_BOOTSTRAP_ENABLED` | §4.4「旗標（**預設關**）」 |
| `bootstrap_enabled()` **呼叫時**讀 `os.environ`；真值集合 `{"1","true","yes","on"}` | §4.4 旗標表（沿用 `consolidation_enabled()` 先例） |
| `BOOTSTRAP_MARKER_FILENAME` ＝ `life_thread_bootstrap.json` | §4.4「有界性」標記落點 |
| `read_marker()` / `claim_bootstrap()` | §4.4「有界性」的**先蓋章再執行**與讀／寫失敗 ⇒ 不 bootstrap |
| `bootstrap_should_fire()` | §4.4「觸發條件」①~④ ＋ 落點第 2 點的「非容量相關」 |
| `ACTIVE_POOL_SATURATED_REASON` | §4.4 落點第 2 點（`REASON_ACTIVE_POOL_SATURATED`，M3 內） |

🔴 **本模組不決定 `origin_type` 的語意**：`BOOTSTRAP_ORIGIN_TYPE`（`"necessity_driven"`）
只是把契約 §4.4「`origin_type` 的選定（**已拍板**）」的裁定值**以常數形式提供**給
orchestrator 使用 —— 本模組**只提供常數**、**不決定**其語意。
§5.2.2 的模板、注入內容與語意**全部屬 M4**（由 M4 的 `build_origin_prompt` 組出），
本模組**不**組 prompt、**不**呼叫 LLM。
契約 §2.3 的 4 值值域**不因此新增第五個**。

🔴 **本模組不做任何 I/O 決策**：路徑由呼叫端（orchestrator）以 M1
`life_threads_path(agent_id)`（已經 `src/paths.py` 的 `data_root()` 組出）傳入，
`bootstrap_marker_path()` 只把 `<threads_path>.parent` 換上標記檔名。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

# ══════════════════════════════════════════════════════════════
# 常數（具名、可測；全部為純字面值 ⇒ 匯入時 0 I/O）
# ══════════════════════════════════════════════════════════════

#: 契約 §4.4「旗標」：旗標名。**不得**寫進 `.env`／`configs/**`（落地即休眠）。
BOOTSTRAP_ENABLED_ENV = "LIFE_THREAD_BOOTSTRAP_ENABLED"

#: 契約 §4.4「`origin_type` 的選定」：已拍板值（§2.3 四值之一，**不發明第五個**）。
#: 本模組只提供常數，**不決定**其語意（語意屬 M4 §5.2.2）。
BOOTSTRAP_ORIGIN_TYPE = "necessity_driven"

#: 契約 §4.4「有界性」：標記檔名（落點＝ `<threads_path>.parent / 本檔名`）。
BOOTSTRAP_MARKER_FILENAME = "life_thread_bootstrap.json"

#: 真值集合（不分大小寫；去首尾空白後比較）。同 `TRUTHY_VALUES` 既有慣例。
BOOTSTRAP_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

#: 契約 §4.4 落點第 2 點：容量飽和理由（＝ M3 `REASON_ACTIVE_POOL_SATURATED` 的
#: **字面值**）。🔴 本模組**不 import M3**（凍結面），故以字串常數自行比對。
ACTIVE_POOL_SATURATED_REASON = "ACTIVE_POOL_SATURATED"

#: 標記存在但**讀不到／讀不懂**時的哨兵值（**truthy** ⇒ 視為「已設」⇒ 不 bootstrap）。
_UNREADABLE_MARKER: Dict[str, Any] = {"__unreadable__": True}

__all__ = [
    "BOOTSTRAP_ENABLED_ENV",
    "BOOTSTRAP_ORIGIN_TYPE",
    "BOOTSTRAP_MARKER_FILENAME",
    "BOOTSTRAP_TRUTHY_VALUES",
    "ACTIVE_POOL_SATURATED_REASON",
    "bootstrap_enabled",
    "bootstrap_marker_path",
    "read_marker",
    "bootstrap_should_fire",
    "claim_bootstrap",
]


# ══════════════════════════════════════════════════════════════
# 旗標（**呼叫時**讀取，非匯入時快取）
# ══════════════════════════════════════════════════════════════


def bootstrap_enabled() -> bool:
    """旗標是否開啟（**每次呼叫都重新讀 `os.environ`**，讓測試能 monkeypatch）。

    真值集合 `BOOTSTRAP_TRUTHY_VALUES`（不分大小寫、去首尾空白）；
    **缺席／空字串／非真值／讀取失敗 ⇒ `False`**（fail-safe 方向 ＝ 不花錢）。
    **永不 raise。**
    """
    try:
        raw = os.environ.get(BOOTSTRAP_ENABLED_ENV)
    except Exception:  # pragma: no cover - defensive（理論上不可能）
        return False
    if not isinstance(raw, str):
        return False
    try:
        return raw.strip().lower() in BOOTSTRAP_TRUTHY_VALUES
    except Exception:  # pragma: no cover - defensive
        return False


# ══════════════════════════════════════════════════════════════
# 標記檔（at-most-once 的唯一持久防重）
# ══════════════════════════════════════════════════════════════


def bootstrap_marker_path(threads_path) -> Path:
    """標記檔路徑 ＝ `Path(threads_path).parent / BOOTSTRAP_MARKER_FILENAME`。

    形同 §4.4「標記落點」：`data/soul/<agent_id>/life_thread_bootstrap.json`
    （呼叫端傳入 M1 `life_threads_path(agent_id)`）。
    **永不 raise**：路徑無法組出時回 `Path(os.devnull)` 哨兵 —— 該路徑讀取必定
    落到 `read_marker()` 的「讀不懂」分支（回 truthy 哨兵 ⇒ **不** bootstrap），
    寫入亦必定失敗（回 `False`）⇒ 仍是 fail-closed 方向。
    """
    try:
        return Path(threads_path).parent / BOOTSTRAP_MARKER_FILENAME
    except Exception:  # pragma: no cover - defensive
        try:
            return Path(os.devnull)
        except Exception:  # pragma: no cover - defensive
            return Path(BOOTSTRAP_MARKER_FILENAME)


def read_marker(marker_path) -> Optional[Dict[str, Any]]:
    """讀 bootstrap 標記（**永不 raise**）。

    - 檔案**不存在**（`FileNotFoundError`）⇒ `None`（＝**未設** ⇒ 允許 bootstrap）。
    - 可讀且為**合法 JSON `dict`** ⇒ 該 dict。
    - **其他任何情形**（權限／壞 JSON／空檔／合法 JSON 但非 dict／路徑怪異）
      ⇒ 回 truthy 哨兵 `{"__unreadable__": True}`（＝**已設** ⇒ **不** bootstrap）。

    🔴 方向是 fail-quiet：**讀不到一律視為已設**（寧可漏一次，不可重複花費）。

    🔴 **先經 `Path()` 正規化再開檔**（防 fd 語意陷阱）：`open(bool)`／`open(int)`
    會被 CPython 當成 **file descriptor**（`True` ＝ fd 1）——那會**阻塞**讀取、
    甚至關掉呼叫端的 fd。`Path()` 對 `int`／`bool`／`None` 一律 `TypeError`
    ⇒ 落到下面的哨兵分支（型別怪異 ＝ 已設），**永不 raise、永不阻塞**。
    """
    try:
        target = str(Path(marker_path))
    except Exception:
        # 型別怪異（`None` / `int` / `bool` / 怪物件）⇒ 無法確認「未設」⇒ 視為已設。
        return dict(_UNREADABLE_MARKER)

    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None
    except Exception:
        # 權限／是目錄／路徑怪異 ⇒ 無法確認「未設」⇒ 視為已設。
        return dict(_UNREADABLE_MARKER)

    try:
        data = json.loads(raw)
    except Exception:
        return dict(_UNREADABLE_MARKER)
    if not isinstance(data, dict):
        return dict(_UNREADABLE_MARKER)
    return data


def claim_bootstrap(marker_path, *, now_iso: str) -> bool:
    """**先蓋章再執行**（契約 §4.4「有界性」）：原子寫入標記檔，成功回 `True`。

    - 內容至少 `{"bootstrapped_at": now_iso, "count": 1}`（§4.4 標記內容規格）。
    - 父目錄自動建立（`parents=True, exist_ok=True`）。
    - 寫入**暫存檔**後 `os.replace` **原子替換**（避免半寫檔案被讀成「已設」或「壞檔」）。
    - **任何失敗 ⇒ 回 `False`**（無法保證 at-most-once ⇒ 呼叫端不執行）。
      **永不 raise**；失敗時盡力清掉暫存檔（best-effort，失敗亦吞掉）。
    """
    target: Optional[Path] = None
    tmp_path: Optional[Path] = None
    try:
        target = Path(marker_path)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "bootstrapped_at": now_iso if isinstance(now_iso, str) else str(now_iso),
            "count": 1,
        }
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(parent),
            prefix=BOOTSTRAP_MARKER_FILENAME + ".",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
            newline="\n",
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(target))
        tmp_path = None
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            try:
                os.unlink(str(tmp_path))
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
# 觸發判定（契約 §4.4「觸發條件」①~④；**純函式、無 I/O 之外僅讀標記**）
# ══════════════════════════════════════════════════════════════


def bootstrap_should_fire(
    *,
    enabled: Any,
    m3_should_wake: Any,
    m3_reason: Any,
    active_count: Any,
    capacity: Any,
    marker_path: Any,
) -> bool:
    """契約 §4.4：**六項全部成立**才回 `True`；任一不成立（含型別怪異）⇒ `False`。

    ① `enabled is True`（嚴格身分比較；`1`／`"true"` 等**不算**）
    ② `m3_should_wake is False`（M3 判 SLEEP）
    ③ `str(m3_reason) != ACTIVE_POOL_SATURATED_REASON`（**非容量相關**）
    ④ `active_count` 為 `int`（非 `bool`）且 **== 0**（零 active 線頭）
    ⑤ `capacity` 為 `int`（非 `bool`）且 **> 0**
    ⑥ `read_marker(marker_path)` 為 **falsy**（標記**未設**）

    **永不 raise**：任何意外（含 `read_marker` 內部已被吞掉的例外）⇒ `False`。
    """
    try:
        if enabled is not True:
            return False
        if m3_should_wake is not False:
            return False
        if str(m3_reason) == ACTIVE_POOL_SATURATED_REASON:
            return False
        if not isinstance(active_count, int) or isinstance(active_count, bool):
            return False
        if active_count != 0:
            return False
        if not isinstance(capacity, int) or isinstance(capacity, bool):
            return False
        if capacity <= 0:
            return False
        if read_marker(marker_path):
            return False
        return True
    except Exception:  # pragma: no cover - defensive
        return False
