# src/paths.py
# Soul OS — Canonical runtime data-root resolver
"""
P0.5 (Bry 派工 2026-08-09 19:48): Canonical data-root for runtime persistence.

Provides a single `data_root()` accessor that resolves the runtime data directory
based on the `SOUL_OS_DATA_DIR` environment variable.

Production default (env var unset):
    data_root() == Path("data").resolve()

Test subprocess (env var set to temp dir):
    data_root() == Path(SOUL_OS_DATA_DIR).resolve()

Every module that writes runtime state should use `data_root() / <sub-path>`
instead of the hardcoded `Path("data/...")` literal.

Frozen contract: this helper does NOT change production behavior when
SOUL_OS_DATA_DIR is unset. All existing production paths continue to work.

FEAT-ISOLATED-DATA-ROOT-FACTORY（2026-10-24）：本檔追加**受控隔離環境上下文工廠**。

背景（實測現況）
----------------
`data_root()` 是**行程期快取單例**，而多個模組在 **import 期**就把資料根**算死**
成模組層級常數（例：`proxy.CONV_DIR`、`memory.store.DB_PATH`、
`diary.DEFAULT_DIARY_ROOT` …）。⇒ 既有 `tests/conftest.py` 的 autouse fixture
只覆寫「根」（env ＋ `reset_data_root()`），**覆寫不到那些已經算死的常數**
⇒ 測試端不得不到處手動 monkeypatch 全域常數（散落、易漏、漏一個就靜默讀寫生產）。

本檔的解法（**單一入口、內部統一置換、退出必還原**）
--------------------------------------------------
1. `DERIVED_DATA_ROOT_CONSTANTS`：**盤點清單**（AST 靜態掃描 ＋ 執行期動態掃描
   雙重交叉驗證；見 `tests/infra/test_isolated_data_root_guard.py`）。
2. `reset_data_root()`：除了清快取，**同時**把清單裡的每個常數（連同「以它為
   預設參數值」的函式）重新指向**新解析出的根** ⇒ 既有 conftest fixture 呼叫
   `reset_data_root()` 就自動獲得完整覆蓋（**全庫只有一套隔離機制**）。
3. `isolated_data_root(root)`：**受控隔離環境上下文**（僅限測試／沙盒）。

🔴 **生產語意零變更**：
  * `data_root()` 的快取語意不變（仍可快取；`reset_data_root()` 是明確的重置路徑）。
  * `check_store_dir()` 一行未改；`format_working_state_lines_production()`／
    `drive_loop_async_production()` 的「路徑不可注入」語意不變。
  * `reset_data_root()` 在**生產行程從未被呼叫**（只有 `harness/` 離線腳本、
    `scripts/sim_*.py` 與 `tests/**` 會呼叫）⇒ 本檔的新增行為**不觸及生產**。
"""
from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

#: 生產預設資料根（``SOUL_OS_DATA_DIR`` 未設時）—— **單一事實來源**。
#: ⚠️ 原先 `data_root()` 內嵌的 ``Path("data")`` 字面值抽成此常數，
#:    語意**逐位元不變**（仍是「相對於 cwd 的 data/」）。
_DEFAULT_DATA_ROOT = Path("data")

#: 行程期快取（``data_root()`` 的解析結果）。
_DATA_ROOT: Path | None = None

#: 「預設參數捕捉」的掃描結果快取（鍵：``len(sys.modules)``）。
_DEFAULT_ARG_HOLDERS: Optional[List[Tuple[Any, int]]] = None
_DEFAULT_ARG_HOLDERS_KEY: Optional[int] = None


@dataclass(frozen=True)
class DerivedConstant:
    """盤點清單的一項：**在 import 期擷取資料根**的模組層級常數。

    Attributes:
        module: 模組的 dotted 名稱（``src.xxx.yyy``）。
        attr: 模組層級屬性名。
        rel: 相對於**資料根**的子路徑（posix 格式）—— 置換時用它重建新值。
    """

    module: str
    attr: str
    rel: str

    @property
    def key(self) -> Tuple[str, str]:
        """(模組, 屬性) 二元組（可雜湊）。"""
        return (self.module, self.attr)


#: **盤點清單**（12 筆）：所有「在 import 期擷取資料根」的模組層級常數。
#:
#: 🔺 本清單是**單一事實來源**：置換機制只認這張表；守衛檔
#: ``tests/infra/test_isolated_data_root_guard.py`` 會用**執行期動態掃描**
#: 反向比對 ⇒ 新增一個 import 期資料根常數卻沒登錄 ⇒ 守衛轉紅。
#:
#: ⚠️ 本清單刻意**集中宣告於本檔**：各模組**一行都不必改**（`src/llm/proxy.py`
#:    是主對話路徑，觸及面必須最小）。
DERIVED_DATA_ROOT_CONSTANTS: Tuple[DerivedConstant, ...] = (
    DerivedConstant("src.io.channels.router", "_STATE_DIR", "state"),
    DerivedConstant(
        "src.io.channels.router", "_LAST_TG_USER_FILE", "state/last_tg_user.json"
    ),
    DerivedConstant(
        "src.io.channels.router", "_BRYAN_LAST_SEEN_FILE", "state/bryan_last_seen.json"
    ),
    DerivedConstant("src.io.channels.router", "_OUTBOX_FILE", "state/outbox.json"),
    DerivedConstant("src.llm.proxy", "CONV_DIR", "conversations"),
    DerivedConstant("src.llm.proxy", "INNER_LIFE_DATA_DIR", "soul"),
    DerivedConstant("src.llm.proxy", "_GROUP_FILE", "conversations/group_chat.json"),
    DerivedConstant("src.llm.tts_toggle", "_STATE_DIR", "state"),
    DerivedConstant("src.llm.tts_toggle", "_STATE_FILE", "state/tts_toggle.json"),
    DerivedConstant("src.memory.store", "DB_PATH", "memory.db"),
    DerivedConstant("src.soul.diary", "DEFAULT_DIARY_ROOT", "soul"),
)

#: 「**生產資料根解析器**」清單：(模組, 屬性)。
#:
#: 🔺 這些屬性在隔離期間**必須維持指向真實生產根**：否則生產守門會
#:    「拿沙盒跟自己比」⇒ **語意反轉**（沙盒也會被拒絕）。
#:    例：`src.inner_life.tension_closure_loop._production_data_root`。
#: ⚠️ 隔離期間**只改這一項的解析結果**，不動 `check_store_dir()` 一行。
PRODUCTION_DATA_ROOT_RESOLVERS: Tuple[Tuple[str, str], ...] = (
    ("src.inner_life.tension_closure_loop", "_production_data_root"),
)


# ─────────────────────────────────────────────────────────────────────────
# 根解析
# ─────────────────────────────────────────────────────────────────────────
def _resolve_root() -> Path:
    """解析當下的資料根（**不**建立目錄；`data_root()` 才會 mkdir）。"""
    env = os.environ.get("SOUL_OS_DATA_DIR")
    if env:
        return Path(env).resolve()
    return _DEFAULT_DATA_ROOT.resolve()


def data_root() -> Path:
    """Resolve the runtime data root.

    Reads SOUL_OS_DATA_DIR from environment. If unset, defaults to "data"
    (relative to cwd). Creates the directory on first call if it doesn't exist.

    Cached after first call (subprocess-lifetime singleton).

    Returns:
        Absolute Path to the data root.
    """
    global _DATA_ROOT
    if _DATA_ROOT is None:
        _DATA_ROOT = _resolve_root()
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT


def production_data_root() -> Path:
    """**生產**資料根（與 ``SOUL_OS_DATA_DIR`` **無關**）。

    語意 ＝ ``SOUL_OS_DATA_DIR`` 未設時 ``data_root()`` 的解析結果
    （即相對於 cwd 的 ``data/``）—— 與生產服務的預設完全一致。

    用途：隔離期間，生產安全守門必須繼續對**真實生產根** fail-closed，
    而不能拿沙盒跟自己比（否則語意反轉）。🔴 **不改變任何生產行為**。
    """
    return _DEFAULT_DATA_ROOT.resolve()


# ─────────────────────────────────────────────────────────────────────────
# 統一置換（re-point）：把 import 期算死的資料根常數重新指向新根
# ─────────────────────────────────────────────────────────────────────────
def _is_path_like(value: Any) -> bool:
    return isinstance(value, (str, Path))


def _current_constant_values() -> Dict[Tuple[str, str], Any]:
    """盤點清單各項的**當前值**（模組尚未匯入者略過）。"""
    out: Dict[Tuple[str, str], Any] = {}
    for entry in DERIVED_DATA_ROOT_CONSTANTS:
        module = sys.modules.get(entry.module)
        if module is None:
            continue
        value = getattr(module, entry.attr, None)
        if value is None:
            continue
        out[entry.key] = value
    return out


def _iter_src_callables() -> Iterator[Any]:
    """所有已匯入的 ``src.*`` 模組裡的函式（含類別 method）。"""
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith("src."):
            continue
        try:
            members = list(vars(module).items())
        except Exception:  # noqa: BLE001 — 掃描失敗不得影響主流程
            continue
        for _attr, value in members:
            if inspect.isfunction(value):
                yield value
            elif inspect.isclass(value):
                try:
                    class_members = list(vars(value).items())
                except Exception:  # noqa: BLE001
                    continue
                for _m_attr, method in class_members:
                    if inspect.isfunction(method):
                        yield method


def _default_arg_holders(
    values: Dict[Tuple[str, str], Any]
) -> List[Tuple[Any, int]]:
    """找出「預設參數值 ＝ 某個資料根衍生常數」的 (函式, 位置)。

    🔺 這是**第二層捕捉**：``def __init__(self, db_path: Path = DB_PATH)``
    這類**預設參數**在 import 期就把常數值綁進簽章 ⇒ 只換模組屬性**不夠**。

    ⚠️ 掃描成本會被 ``reset_data_root()`` 放大（每測試兩次）⇒ 結果**快取**，
    以 ``len(sys.modules)`` 為失效鍵（新增匯入的模組會讓它失效）。
    """
    global _DEFAULT_ARG_HOLDERS, _DEFAULT_ARG_HOLDERS_KEY
    cache_key = len(sys.modules)
    if _DEFAULT_ARG_HOLDERS is not None and _DEFAULT_ARG_HOLDERS_KEY == cache_key:
        return _DEFAULT_ARG_HOLDERS

    holders: List[Tuple[Any, int]] = []
    seen: set = set()
    reference = list(values.values())
    for func in _iter_src_callables():
        defaults = getattr(func, "__defaults__", None)
        if not defaults:
            continue
        for index, default in enumerate(defaults):
            if default is None or not _is_path_like(default):
                continue
            for original in reference:
                if default is original or (
                    type(default) is type(original) and default == original
                ):
                    key = (id(func), index)
                    if key not in seen:
                        seen.add(key)
                        holders.append((func, index))
                    break
    _DEFAULT_ARG_HOLDERS = holders
    _DEFAULT_ARG_HOLDERS_KEY = cache_key
    return holders


def _sync_default_args(
    holders: List[Tuple[Any, int]], mapping: Dict[Any, Any]
) -> None:
    """把受影響函式的預設參數值換成新的常數值。"""
    for func, index in holders:
        defaults = getattr(func, "__defaults__", None)
        if not defaults or index >= len(defaults):
            continue
        current = defaults[index]
        replacement = None
        for original, new_value in mapping.items():
            if current is original or (
                type(current) is type(original) and current == original
            ):
                replacement = new_value
                break
        if replacement is None:
            continue
        new_defaults = list(defaults)
        new_defaults[index] = replacement
        try:
            func.__defaults__ = tuple(new_defaults)
        except Exception:  # noqa: BLE001 — 不可變函式等 ⇒ 靜默跳過
            continue


def _preserve_directory_shape(old_value: Any, new_value: Any) -> None:
    """維持「舊值是目錄 ⇒ 新值也是目錄」的形狀（import 期的 mkdir 副作用）。"""
    try:
        if Path(old_value).is_dir():
            Path(new_value).mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 — 形狀維持失敗不得影響主流程
        return


def _repoint_derived_constants(new_root: Path) -> None:
    """把盤點清單裡的每個常數（＋其預設參數捕捉）重新指向 ``new_root``。"""
    current = _current_constant_values()
    if not current:
        return
    holders = _default_arg_holders(current)
    mapping: Dict[Any, Any] = {}
    for entry in DERIVED_DATA_ROOT_CONSTANTS:
        old_value = current.get(entry.key)
        if old_value is None:
            continue
        module = sys.modules.get(entry.module)
        if module is None:
            continue
        new_path = new_root / Path(entry.rel)
        new_value = str(new_path) if isinstance(old_value, str) else new_path
        try:
            setattr(module, entry.attr, new_value)
        except Exception:  # noqa: BLE001
            continue
        mapping[old_value] = new_value
        _preserve_directory_shape(old_value, new_value)
    _sync_default_args(holders, mapping)


def reset_data_root() -> None:
    """Reset the cached data root **and re-point every registered constant**.

    Intended for test setup/teardown if a single process needs to re-resolve
    after changing SOUL_OS_DATA_DIR at runtime. Not normally needed in production.

    🔺 **單一置換點**（FEAT-ISOLATED-DATA-ROOT-FACTORY）：
    除了清掉 ``data_root()`` 的行程期快取，本函式**同時**把
    :data:`DERIVED_DATA_ROOT_CONSTANTS` 盤點清單裡的每個「import 期算死的
    資料根常數」（連同以它們為**預設參數值**的函式）重新指向新解析出的根。
    ⇒ 既有 ``tests/conftest.py`` 的 autouse 隔離 fixture（呼叫本函式）
    **自動獲得完整覆蓋** ⇒ 全庫只有一套隔離機制。

    ⚠️ 生產行程**從不**呼叫本函式 ⇒ 生產行為零變更。
    """
    global _DATA_ROOT
    _DATA_ROOT = None
    _repoint_derived_constants(_resolve_root())


# ─────────────────────────────────────────────────────────────────────────
# 受控隔離環境上下文（僅限測試／沙盒）
# ─────────────────────────────────────────────────────────────────────────
def _install_production_root_resolvers() -> List[Tuple[Any, str, Any]]:
    """把「生產資料根解析器」指向**真實生產根**（隔離期間）。

    回傳還原用的備份清單。
    """
    backup: List[Tuple[Any, str, Any]] = []
    for module_name, attr in PRODUCTION_DATA_ROOT_RESOLVERS:
        module = sys.modules.get(module_name)
        if module is None:
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001 — 模組不可用 ⇒ 靜默跳過
                continue
        original = getattr(module, attr, None)
        if original is None:
            continue
        backup.append((module, attr, original))
        try:
            setattr(module, attr, production_data_root)
        except Exception:  # noqa: BLE001
            continue
    return backup


def _restore_production_root_resolvers(backup: List[Tuple[Any, str, Any]]) -> None:
    for module, attr, original in backup:
        try:
            setattr(module, attr, original)
        except Exception:  # noqa: BLE001
            continue


def _capture_restore_actions() -> List[Callable[[], None]]:
    """擷取「進入前的每一個值」，供退出時**逐位元**還原。"""
    actions: List[Callable[[], None]] = []
    current = _current_constant_values()
    for entry in DERIVED_DATA_ROOT_CONSTANTS:
        old_value = current.get(entry.key)
        module = sys.modules.get(entry.module)
        if old_value is None or module is None:
            continue
        actions.append(
            lambda m=module, a=entry.attr, v=old_value: setattr(m, a, v)
        )
    for func, index in _default_arg_holders(current):
        defaults = getattr(func, "__defaults__", None)
        if not defaults:
            continue
        snapshot = tuple(defaults)
        actions.append(lambda f=func, d=snapshot: setattr(f, "__defaults__", d))
    return actions


@contextlib.contextmanager
def isolated_data_root(root: Any) -> Iterator[Any]:
    """（本票新增）受控隔離環境：把**所有**資料根衍生常數統一置換到 ``root``，退出時還原。

    🔴 **僅限測試／沙盒**：啟用條件為「存在 ``PYTEST_CURRENT_TEST`` 環境變數」
    ⇒ **生產行程無法啟用**（生產沒有這個變數）⇒ 不構成後門。
    🔴 **退出必還原**：含例外路徑（``finally``）⇒ 例外也不會洩漏。
    🔴 **可巢狀**：進入時保存當前值、退出時還原成**進入前**的值 ⇒ 巢狀語意正確。
    🔴 **不改變防注入契約**：``format_working_state_lines_production()``／
    ``drive_loop_async_production()`` 的「路徑不可注入」語意**不變**；``check_store_dir()`` **一行不改**。

    Args:
        root: 隔離用的資料根（路徑或 ``str``）。

    Yields:
        解析後的隔離根（``Path``）。

    Raises:
        RuntimeError: 非測試情境（環境變數 ``PYTEST_CURRENT_TEST`` 不存在）**拒絕啟用**。
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError(
            "isolated_data_root() 僅限測試／沙盒情境：環境變數 PYTEST_CURRENT_TEST "
            "不存在 ⇒ 拒絕啟用（生產行程不得存在第二條資料根路徑）。"
        )

    new_root = Path(root).resolve()
    had_env = "SOUL_OS_DATA_DIR" in os.environ
    env_before = os.environ.get("SOUL_OS_DATA_DIR")
    restore_actions = _capture_restore_actions()
    resolver_backup = _install_production_root_resolvers()
    try:
        os.environ["SOUL_OS_DATA_DIR"] = str(new_root)
        # 清快取 ＋ 把所有 import 期算死的常數重新指向新根。
        reset_data_root()
        yield new_root
    finally:
        _restore_production_root_resolvers(resolver_backup)
        # 還原環境變數 ⇒ 之後的 reset_data_root() 會把常數指回進入前的根。
        if had_env:
            os.environ["SOUL_OS_DATA_DIR"] = env_before or ""
        else:
            os.environ.pop("SOUL_OS_DATA_DIR", None)
        reset_data_root()
        # 逐位元還原「進入前」的值（快照優先於重新推導）。
        for action in restore_actions:
            try:
                action()
            except Exception:  # noqa: BLE001 — 還原失敗不得洩漏
                continue
