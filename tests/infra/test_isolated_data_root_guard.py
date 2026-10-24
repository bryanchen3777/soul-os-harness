# tests/infra/test_isolated_data_root_guard.py
"""FEAT-ISOLATED-DATA-ROOT-FACTORY —— 受控隔離環境上下文工廠的守衛（I1～I8，**恰 8 筆**）。

工單：``logs/FEAT-ISOLATED-DATA-ROOT-FACTORY-WORK-ORDER.md`` §3。

本檔釘死「單一入口、內部統一置換、退出必還原」的隔離環境上下文工廠
（``src/paths.py::isolated_data_root``）的九個性質：

  * **僅限測試**（I1）：非測試情境（無 ``PYTEST_CURRENT_TEST``）**必須拒絕啟用**
    ⇒ 不構成生產可用的第二條資料根路徑。
  * **完整覆蓋**（I2）：盤點清單裡的**每一個** import 期資料根常數，
    進入上下文後**都被置換**；且**執行期動態掃描**不得發現「未登錄」的同類常數
    ⇒ 新增一個 import 期資料根常數卻忘了登錄 ⇒ 轉紅。
  * **退出必還原**（I3／I4）：正常路徑與**例外路徑**都要把每一個常數
    **逐位元**還原成進入前的值。
  * **巢狀正確**（I5）：內層退出後回到**外層**的值（不是原始值）。
  * **快取失效**（I6）：上下文內 ``data_root()`` 回傳新根；退出後回傳**原根**。
  * **防注入契約未動**（I7）：``check_store_dir()`` 對生產路徑**仍拒絕**
    （且其**原始碼逐位元未改**）；兩個生產介面的簽章**不含路徑參數**。
  * **E2E 改用它**（I8）：E2E 守衛檔**不再 monkeypatch** 那 10 個常數。

邊界：
  * 0 subprocess / 0 os.spawn* / 0 os.system / 0 os.popen（R5 護欄）。
  * 0 啟動伺服器 / 0 綁 :8000 / :8765-8767 / 0 對生產發真實請求。
  * 0 真實 LLM 呼叫 / 0 外部 API / 0 網路。
  * 0 寫入 ``data/**``（本檔只寫 pytest 的 tmp 目錄）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

import src.paths as paths  # noqa: E402
from src.paths import (  # noqa: E402
    DERIVED_DATA_ROOT_CONSTANTS,
    PRODUCTION_DATA_ROOT_RESOLVERS,
    isolated_data_root,
    production_data_root,
)

#: 盤點清單的**筆數**（守門釘死）。新增／刪減一個 import 期資料根常數時，
#: 必須同時更新這裡與 ``src/paths.py::DERIVED_DATA_ROOT_CONSTANTS``。
EXPECTED_CONSTANT_COUNT = 12

#: 盤點清單涵蓋的**模組數**（6 個）。
EXPECTED_MODULE_COUNT = 6

#: 「生產資料根解析器」的筆數（1 個）。
EXPECTED_RESOLVER_COUNT = 1

#: 「**預設參數捕捉**」捕捉點（**實測 3 筆**，主大腦 2026-10-24 獨立複核）。
#:
#: 🔺 這些值在 **import 期**就綁進函式簽章 ⇒ **只換模組屬性不夠**；
#:    而這一層一旦失效，「預設建構的 MemoryStore／EmotionEngine／DiaryWriter」
#:    會**脫離隔離、指向生產**（import 期值 ＝ cwd/data）⇒ 屬生產污染風險。
#:
#: 🔺 為什麼是 **3** 筆而不是更多：
#:    * ``src.llm.proxy.MemoryStore`` 與 ``src.memory.store.MemoryStore``
#:      是**同一個函式物件**（proxy 只是 import 了它）⇒ 不重複計。
#:    * ``src.llm.proxy`` **並未** import ``DiaryWriter``（實測
#:      ``AttributeError: module 'src.llm.proxy' has no attribute 'DiaryWriter'``）
#:      ⇒ 不存在第 4 個捕捉點。
DEFAULT_ARG_CAPTURE_POINTS: Tuple[Tuple[str, str, str, int, str], ...] = (
    ("src.agent.emotion", "EmotionEngine", "__init__", 0, "memory.db"),
    ("src.memory.store", "MemoryStore", "__init__", 0, "memory.db"),
    ("src.soul.diary", "DiaryWriter", "__init__", 0, "soul"),
)

#: 「預設參數捕捉」的筆數（守門釘死；新增／刪減捕捉點必須同時更新這裡）。
EXPECTED_DEFAULT_ARG_CAPTURE_COUNT = 3

#: ``check_store_dir()`` 的**原始碼**逐位元雜湊（EOL 正規化）。
#:
#: 🔺 為什麼釘雜湊而不只釘行為：``check_store_dir()`` 的三道拒絕條件
#:    （生產資料根／repo data/／非 temp）在**本倉**的 cwd 設定下**互相重疊**
#:    ⇒ 只斷言「生產路徑被拒」**抓不到**「放寬其中一道」的變異。
#:    釘原始碼 ⇒ **任何**放寬（哪怕只改一行）都轉紅。
#:    （沿用本倉既有慣例：``test_campaign_b_e2e_closed_loop.py`` 的
#:     ``PHASE4_GUARD_SHA256``。）
CHECK_STORE_DIR_SHA256 = "b28d82628974f26592b4791372770b2d627b6033c11fce6bf2134c7fefff2f87"

#: 兩個「路徑不可注入」的生產介面，其簽章參數名（**逐字釘死**）。
PRODUCTION_SIGNATURES: Dict[str, Tuple[str, ...]] = {
    "format_working_state_lines_production": ("agent_id", "max_entries", "max_chars"),
    "drive_loop_async_production": ("agent_id", "thinker", "now"),
}

#: E2E 守衛檔（I8）。
E2E_GUARD_FILE = "tests/inner_life/test_campaign_b_e2e_closed_loop.py"

#: E2E 守衛檔的測試函式數（8 筆；本票**不增減**其測試數）。
E2E_GUARD_TEST_COUNT = 8

#: 「改前」E2E 手動 monkeypatch 的 10 個 import 期資料根常數（I8：不得再出現）。
LEGACY_MANUAL_OVERRIDES: Tuple[str, ...] = (
    "data_root",
    "INNER_LIFE_DATA_DIR",
    "CONV_DIR",
    "_GROUP_FILE",
    "DB_PATH",
    "_STATE_DIR",
    "_STATE_FILE",
    "DEFAULT_DIARY_ROOT",
    "_production_data_root",
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────
def _snapshot_constants() -> Dict[Tuple[str, str], Tuple[str, str]]:
    """盤點清單各項的**當前值**（以 ``(型別名, 字串值)`` 表示 ⇒ 可逐位元比對）。"""
    out: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for entry in DERIVED_DATA_ROOT_CONSTANTS:
        module = importlib.import_module(entry.module)
        value = getattr(module, entry.attr)
        out[entry.key] = (type(value).__name__, str(value))
    return out


def _expected_value(root: Path, entry: Any, original_type: str) -> Any:
    """隔離根下某個常數的**預期值**（型別與原值一致）。"""
    path = root / Path(entry.rel)
    return str(path) if original_type == "str" else path


def _discover_data_root_constants(root: Path) -> Dict[Tuple[str, str], str]:
    """**執行期動態掃描**：所有「值位於 ``root`` 之下」的模組層級屬性。

    這是盤點清單的**反向驗證**：若將來有模組新增了一個 import 期資料根常數
    卻沒登錄 ⇒ 這裡會掃到 ⇒ I2 轉紅。
    """
    found: Dict[Tuple[str, str], str] = {}
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith("src."):
            continue
        if name == "src.paths":
            # ``_DATA_ROOT`` 是快取本身、``_DEFAULT_DATA_ROOT`` 是相對路徑
            # ⇒ 兩者都不是「import 期擷取根的衍生常數」。
            continue
        try:
            members = list(vars(module).items())
        except Exception:  # noqa: BLE001
            continue
        for attr, value in members:
            if attr.startswith("__"):
                continue
            if isinstance(value, str):
                kind = "str"
            elif isinstance(value, Path):
                kind = "Path"
            else:
                continue
            try:
                candidate = Path(value)
                candidate.relative_to(root)
            except Exception:  # noqa: BLE001
                continue
            found[(name, attr)] = kind
    return found


def _e2e_setattr_offenders(tree: ast.AST) -> List[str]:
    """E2E 檔內「把屬性設成別的值」的呼叫點（針對 legacy 常數名）。"""
    offenders: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        is_setattr = func.attr == "setattr"
        is_patch_object = func.attr == "object" and isinstance(func.value, ast.Name) and func.value.id == "patch"
        if not (is_setattr or is_patch_object):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords if kw.value]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value in LEGACY_MANUAL_OVERRIDES:
                    offenders.append(f"{arg.value} (line {getattr(node, 'lineno', '?')})")
    return offenders


# ═══════════════════════════════════════════════════════════════════════
# I1：僅限測試 —— 非測試情境必須拒絕啟用
# ═══════════════════════════════════════════════════════════════════════
def test_i1_factory_refuses_to_start_outside_pytest(monkeypatch, tmp_path: Path) -> None:
    """無 ``PYTEST_CURRENT_TEST`` 時 ``isolated_data_root()`` **必須拋例外**。

    ⇒ 生產行程（沒有這個環境變數）**無法**啟用它 ⇒ 不是生產後門。
    """
    before_root = paths.data_root()
    before_env = os.environ.get("SOUL_OS_DATA_DIR")

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        with isolated_data_root(tmp_path / "data"):
            pass

    message = str(excinfo.value)
    assert "PYTEST_CURRENT_TEST" in message, message
    assert "僅限測試" in message, message
    # 拒絕啟用 ⇒ **不得**有任何狀態被改動（資料根與環境變數都不動）。
    assert paths.data_root() == before_root
    assert os.environ.get("SOUL_OS_DATA_DIR") == before_env


# ═══════════════════════════════════════════════════════════════════════
# I2：完整覆蓋 —— 盤點清單每一項都被置換（且沒有漏登錄的）
# ═══════════════════════════════════════════════════════════════════════
def test_i2_every_inventory_constant_is_replaced_and_inventory_is_complete(
    tmp_path: Path,
) -> None:
    """進入上下文後，盤點清單的**每一個**常數都被置換到隔離根之下。

    🔺 兩段斷言：
      ① **逐項斷言**：清單裡每一項的值都變成 ``<隔離根>/<子路徑>``（型別不變）。
      ② **完整性**：執行期動態掃描**掃不到任何未登錄**的同類常數
         ⇒ 將來新增一個 import 期資料根常數卻沒登錄 ⇒ 轉紅。
    """
    assert len(DERIVED_DATA_ROOT_CONSTANTS) == EXPECTED_CONSTANT_COUNT, (
        f"盤點清單筆數為 {len(DERIVED_DATA_ROOT_CONSTANTS)}，應為 {EXPECTED_CONSTANT_COUNT}"
    )
    modules = {entry.module for entry in DERIVED_DATA_ROOT_CONSTANTS}
    assert len(modules) == EXPECTED_MODULE_COUNT, (
        f"盤點清單涵蓋 {len(modules)} 個模組，應為 {EXPECTED_MODULE_COUNT}"
    )
    assert len(PRODUCTION_DATA_ROOT_RESOLVERS) == EXPECTED_RESOLVER_COUNT, (
        f"生產資料根解析器筆數為 {len(PRODUCTION_DATA_ROOT_RESOLVERS)}，"
        f"應為 {EXPECTED_RESOLVER_COUNT}"
    )

    sandbox = (tmp_path / "data").resolve()
    original = _snapshot_constants()

    with isolated_data_root(sandbox):
        for entry in DERIVED_DATA_ROOT_CONSTANTS:
            module = importlib.import_module(entry.module)
            value = getattr(module, entry.attr)
            expected = _expected_value(sandbox, entry, original[entry.key][0])
            assert value == expected, (
                f"{entry.module}.{entry.attr} 未被置換：實得 {value!r}，預期 {expected!r}"
            )
            assert type(value).__name__ == original[entry.key][0], (
                f"{entry.module}.{entry.attr} 的**型別**被改變："
                f"{type(value).__name__} ≠ {original[entry.key][0]}"
            )

        discovered = _discover_data_root_constants(sandbox)
        registered = {entry.key for entry in DERIVED_DATA_ROOT_CONSTANTS}
        unregistered = sorted(set(discovered) - registered)
        assert not unregistered, (
            "執行期掃描發現**未登錄**的 import 期資料根常數（漏一個就是一個能靜默"
            f"讀寫生產的缺口）：{unregistered}"
        )
        assert registered <= set(discovered), (
            "盤點清單裡有項目在執行期掃描中找不到（常數已不存在或未匯入）："
            f"{sorted(registered - set(discovered))}"
        )

        # ③ **預設參數捕捉層**：import 期就綁進簽章的值也必須被置換
        #    （只換模組屬性**不夠**）。
        #    🔺 這一層是獨立於「模組層級常數」的第二層覆蓋；沒有這段斷言，
        #       「停用預設參數捕捉」的變異會**全綠通過**（實測：守衛 8/8 + E2E 8/8），
        #       而該缺陷會讓預設建構的 store **脫離隔離指向生產**。
        assert len(DEFAULT_ARG_CAPTURE_POINTS) == EXPECTED_DEFAULT_ARG_CAPTURE_COUNT, (
            f"預設參數捕捉點筆數為 {len(DEFAULT_ARG_CAPTURE_POINTS)}，"
            f"應為 {EXPECTED_DEFAULT_ARG_CAPTURE_COUNT}"
        )
        for module_name, cls_name, meth, index, rel in DEFAULT_ARG_CAPTURE_POINTS:
            module = importlib.import_module(module_name)
            cls = getattr(module, cls_name)
            func = getattr(cls, meth)
            defaults = getattr(func, "__defaults__", None)
            assert defaults, f"{module_name}.{cls_name}.{meth} 沒有任何預設參數"
            actual = defaults[index]
            expected = sandbox / Path(rel)
            if isinstance(actual, str):
                expected = str(expected)
            assert actual == expected, (
                f"{module_name}.{cls_name}.{meth}[{index}] 的**預設參數**未被置換："
                f"{actual!r} ≠ {expected!r}"
                "（只換模組屬性不夠；預設參數捕捉層失效會讓預設建構的 store "
                "脫離隔離、指向生產）"
            )


# ═══════════════════════════════════════════════════════════════════════
# I3：退出必還原（正常路徑）
# ═══════════════════════════════════════════════════════════════════════
def test_i3_constants_are_restored_bit_for_bit_on_normal_exit(tmp_path: Path) -> None:
    """正常退出後，每個常數**逐位元等於**進入前的值（含型別）。"""
    before = _snapshot_constants()
    with isolated_data_root(tmp_path / "data"):
        assert _snapshot_constants() != before, "前置條件：上下文內必須有被置換"
    after = _snapshot_constants()
    assert after == before, (
        "退出後常數未逐位元還原："
        f"{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }"
    )


# ═══════════════════════════════════════════════════════════════════════
# I4：例外路徑必還原
# ═══════════════════════════════════════════════════════════════════════
def test_i4_constants_are_restored_bit_for_bit_after_exception(tmp_path: Path) -> None:
    """上下文內拋例外後，常數**仍全部還原**（例外不得洩漏隔離狀態）。"""
    before = _snapshot_constants()
    with pytest.raises(RuntimeError):
        with isolated_data_root(tmp_path / "data"):
            raise RuntimeError("boom")
    after = _snapshot_constants()
    assert after == before, (
        "例外路徑未還原常數："
        f"{ {k: (before[k], after[k]) for k in before if before[k] != after[k]} }"
    )


# ═══════════════════════════════════════════════════════════════════════
# I5：巢狀正確
# ═══════════════════════════════════════════════════════════════════════
def test_i5_nested_context_returns_to_outer_values(tmp_path: Path) -> None:
    """巢狀兩層：內層退出後回到**外層**的值（不是原始值）。"""
    outer = (tmp_path / "outer").resolve()
    inner = (tmp_path / "inner").resolve()

    with isolated_data_root(outer):
        outer_values = _snapshot_constants()
        for entry in DERIVED_DATA_ROOT_CONSTANTS:
            module = importlib.import_module(entry.module)
            value = getattr(module, entry.attr)
            expected = _expected_value(outer, entry, outer_values[entry.key][0])
            assert value == expected, f"外層未置換：{entry.module}.{entry.attr}"

        with isolated_data_root(inner):
            for entry in DERIVED_DATA_ROOT_CONSTANTS:
                module = importlib.import_module(entry.module)
                value = getattr(module, entry.attr)
                expected = _expected_value(inner, entry, outer_values[entry.key][0])
                assert value == expected, f"內層未置換：{entry.module}.{entry.attr}"

        assert _snapshot_constants() == outer_values, "內層退出後未回到外層的值"

    # 外層退出 ⇒ 回到進入前的值
    assert _snapshot_constants() != outer_values


# ═══════════════════════════════════════════════════════════════════════
# I6：快取失效
# ═══════════════════════════════════════════════════════════════════════
def test_i6_data_root_cache_is_invalidated_inside_and_restored_outside(
    tmp_path: Path,
) -> None:
    """上下文內 ``data_root()`` 回傳新根；退出後回傳**原根**。

    （``data_root()`` 是行程期快取單例 ⇒ 必須被正確重置。）
    """
    sandbox = (tmp_path / "data").resolve()
    before = paths.data_root()
    with isolated_data_root(sandbox):
        assert paths.data_root() == sandbox, (
            f"上下文內 data_root() 未回傳新根：{paths.data_root()}"
        )
    assert paths.data_root() == before, (
        f"退出後 data_root() 未回傳原根：{paths.data_root()} ≠ {before}"
    )


# ═══════════════════════════════════════════════════════════════════════
# I7：防注入契約未動
# ═══════════════════════════════════════════════════════════════════════
def test_i7_production_anti_injection_contract_is_untouched(tmp_path: Path) -> None:
    """``check_store_dir()`` 對生產路徑**仍拒絕**；兩個生產介面簽章不含路徑參數。

    🔺 額外把 ``check_store_dir()`` 的**原始碼逐位元**釘死：它的三道拒絕條件
    在本倉的 cwd 設定下互相重疊 ⇒ 只釘行為抓不到「放寬其中一道」的變異。
    """
    from src.inner_life import tension_closure_loop as tcl

    # ① 原始碼逐位元未改（⇒ 任何放寬都轉紅）。
    source = inspect.getsource(tcl.check_store_dir)
    actual = hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert actual == CHECK_STORE_DIR_SHA256, (
        "check_store_dir() 的原始碼被改動（防注入契約不得放寬）："
        f"sha256={actual} 期望={CHECK_STORE_DIR_SHA256}"
    )

    # ② 生產路徑仍被拒絕（且有可稽核的理由）。
    prod_store = REPO_ROOT / "data" / "inner_life"
    allowed, reason = tcl.check_store_dir(prod_store)
    assert allowed is False, f"生產路徑必須仍被守門擋下（實得允許：{reason}）"
    assert reason, "拒絕必須有理由（可稽核）"

    # ③ 非 temp 路徑仍被拒絕（第二道條件）。
    #    用「temp 根的掛載點」當基準 ⇒ 該路徑一定**不在** temp 之下
    #    （Windows: ``C:\not_a_temp_dir_probe``；POSIX: ``/not_a_temp_dir_probe``）。
    temp_root = Path(tempfile.gettempdir()).resolve()
    outside = Path(temp_root.anchor) / "not_a_temp_dir_probe"
    allowed_outside, reason_outside = tcl.check_store_dir(outside)
    assert allowed_outside is False, (
        f"非 temp 路徑必須仍被守門擋下（實得允許：{reason_outside}）"
    )

    # ④ temp 下的沙盒仍被允許（守門**沒有**被過度收緊）。
    allowed_sandbox, reason_sandbox = tcl.check_store_dir(tmp_path)
    assert allowed_sandbox is True, (
        f"temp 下的沙盒必須被允許（實得拒絕：{reason_sandbox}）"
    )

    # ⑤ 兩個「路徑不可注入」的生產介面：簽章不含路徑參數。
    for func_name, expected_params in PRODUCTION_SIGNATURES.items():
        func = getattr(tcl, func_name)
        params = tuple(inspect.signature(func).parameters)
        assert params == expected_params, (
            f"{func_name} 的簽章被改動：{params} ≠ {expected_params}"
        )

    # ⑥ 生產資料根解析器：**隔離期間**指向真實生產根（守門語意不反轉）；
    #    退出後**還原**成原本那個解析器物件。
    for module_name, attr in PRODUCTION_DATA_ROOT_RESOLVERS:
        module = importlib.import_module(module_name)
        original_resolver = getattr(module, attr)
        with isolated_data_root(tmp_path / "data"):
            resolver = getattr(module, attr)
            assert resolver() == production_data_root(), (
                f"{module_name}.{attr} 在隔離期間未指向真實生產根："
                f"{resolver()} ≠ {production_data_root()}"
            )
        assert getattr(module, attr) is original_resolver, (
            f"{module_name}.{attr} 未在退出後還原"
        )


# ═══════════════════════════════════════════════════════════════════════
# I8：E2E 改用它（不再 monkeypatch 那 10 個常數）
# ═══════════════════════════════════════════════════════════════════════
def test_i8_e2e_guard_uses_factory_and_no_longer_monkeypatches_constants(
    tmp_path: Path,
) -> None:
    """E2E 守衛檔**不再** monkeypatch 那 10 個常數，而是使用 ``isolated_data_root()``。"""
    source = (REPO_ROOT / E2E_GUARD_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = _e2e_setattr_offenders(tree)
    assert not offenders, (
        f"{E2E_GUARD_FILE} 仍在手動 monkeypatch import 期資料根常數：{offenders}"
    )

    assert "isolated_data_root" in source, (
        f"{E2E_GUARD_FILE} 必須改用 isolated_data_root()（單一入口）"
    )
    imported = any(
        isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith("paths")
        and any(alias.name == "isolated_data_root" for alias in node.names)
        for node in ast.walk(tree)
    )
    assert imported, f"{E2E_GUARD_FILE} 必須 import isolated_data_root"

    test_funcs = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    assert len(test_funcs) == E2E_GUARD_TEST_COUNT, (
        f"{E2E_GUARD_FILE} 的測試函式數為 {len(test_funcs)}，"
        f"應為 {E2E_GUARD_TEST_COUNT}（本票不增減其測試數）"
    )

    # ④ **行為面**：E2E 的 ``sandbox`` fixture 必須**真的**把資料根指向沙盒
    #    （⇒ 工廠若是空操作，本項轉紅）。
    e2e_module = importlib.import_module(
        "tests.inner_life.test_campaign_b_e2e_closed_loop"
    )
    generator = e2e_module.sandbox.__wrapped__(tmp_path)
    sandbox = next(generator)
    try:
        sandbox_root = sandbox.data_root.resolve()
        assert paths.data_root() == sandbox_root, (
            "E2E 的 sandbox fixture 未把資料根指向沙盒："
            f"{paths.data_root()} ≠ {sandbox_root}"
        )
        for entry in DERIVED_DATA_ROOT_CONSTANTS:
            module = importlib.import_module(entry.module)
            value = getattr(module, entry.attr)
            expected = sandbox_root / Path(entry.rel)
            if isinstance(value, str):
                expected = str(expected)
            assert value == expected, (
                f"E2E 的 sandbox fixture 未把 {entry.module}.{entry.attr} 指向沙盒："
                f"{value!r} ≠ {expected!r}"
            )
    finally:
        with pytest.raises(StopIteration):
            next(generator)
