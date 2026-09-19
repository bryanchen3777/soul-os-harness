# tests/soul/test_life_thread_bootstrap.py
# LIFE-THREAD-BOOTSTRAP-1 — 冷啟動引導（bootstrap wake；契約 §4.4 第三條喚醒路徑）。
#
# 受測模組：`src/soul/life_thread_bootstrap.py`（新增）
#           ＋ `src/soul/life_thread_orchestrator.py` 的 bootstrap 分支（最小改動）
#
# 範式沿用 `tests/soul/test_life_thread_m5_wiring.py`：
#   資料根隔離 fixture（`SOUL_OS_DATA_DIR` ＋ `reset_data_root()`）／`_LAST_PROCESSED`
#   重置／stub LLM（`llm_caller=`）／死代理（process-global proxy 必 raise）／
#   原始碼護欄（AST ＋ 字串索引）。
#
# 🔴 LLM **一律用 stub** ＋ process-global proxy 一律換成**死代理** ⇒ 本檔
#    **0 真實 LLM、0 網路請求**。
# 🔴 本檔**不讀寫生產 `data/**`**：所有落地檔都在 `SOUL_OS_DATA_DIR` 隔離的 tmp 內。
from __future__ import annotations

import ast
import asyncio
import builtins
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_bootstrap as lt_boot  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_wake_gate as lt_gate  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

AGENT = "agent_boot"
OTHER = "agent_boot_b"
_SOUL = "我是ルカ。我喜歡在夜裡散步，討厭吵鬧的地方。"

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_bootstrap.py"
ORCH_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"

#: 契約 §4.4「有界性」：標記內容至少要有這兩個鍵。
_MARKER_REQUIRED_KEYS = ("bootstrapped_at", "count")


# ══════════════════════════════════════════════════════════════
# fixtures / helpers
# ══════════════════════════════════════════════════════════════


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """顯式隔離資料根（0 生產 data 接觸）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    yield tmp_path
    reset_data_root()


@pytest.fixture(autouse=True)
def _clean_idempotency():
    """每個測試前後清空行程內冪等鍵（`_LAST_PROCESSED` 是 process-global）。"""
    m5.reset_state()
    yield
    m5.reset_state()


@pytest.fixture(autouse=True)
def _reset_flag(monkeypatch):
    """旗標一律由測試顯式設定；空字串阻擋 dotenv 重新注入 production 的 ON。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    yield


@pytest.fixture(autouse=True)
def _dead_proxy(monkeypatch):
    """**死代理**：process-global LLM proxy 換成必定 raise 的物件。

    本檔一律以 `llm_caller=` 傳 stub；此 fixture 是第二道保險 —— 任何意外落到
    process-global proxy 的路徑都會當場爆掉（而非發出真實請求）。
    """

    class _DeadProxy:
        async def generate_text(self, *args, **kwargs):
            raise AssertionError("死代理：不得呼叫真實 LLM 通道（0 真實 LLM／0 網路）")

    monkeypatch.setattr(lt_origins, "_global_llm_proxy", _DeadProxy(), raising=False)
    yield


def _loc(hour: int = 8, day_delta: int = 0) -> datetime:
    """本地時區固定時刻（2026-09-06 起算；8:00 → morning、22:00 → night）。"""
    return datetime(2026, 9, 6, hour, tzinfo=LOCAL_TZ) + timedelta(days=day_delta)


MORNING = _loc(8)
NIGHT = _loc(22)


def _run(agent_ids, now, slot, *, llm_caller=None):
    return asyncio.run(m5.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller))


class _SpyLLM:
    """假 LLM 呼叫端（**0 真實 LLM／0 網路**）；記錄每次呼叫。"""

    def __init__(self, response: Any = None):
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def __call__(self, messages, agent_id):
        self.calls.append({"messages": messages, "agent_id": agent_id})
        return self.response


def _json_response(actions: List[Dict[str, Any]]) -> str:
    return json.dumps({"actions": actions}, ensure_ascii=False)


def _patch_soul(monkeypatch, value: str = _SOUL) -> None:
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: value)


def _marker_path(agent_id: str = AGENT) -> Path:
    """受測標記路徑（經 M1 `life_threads_path` ＋ 本模組的檔名常數）。"""
    return lt_boot.bootstrap_marker_path(lt.life_threads_path(agent_id))


def _set_flag(monkeypatch, value: str = "1") -> None:
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, value)


def _read_marker_file(agent_id: str = AGENT) -> Dict[str, Any]:
    return json.loads(_marker_path(agent_id).read_text(encoding="utf-8"))


def _seed_thread(agent_id: str = AGENT, *, check_after: datetime) -> str:
    """建一條 active 線頭（`check_after_ts` 未到期 ⇒ M3 不會因判定 1 喚醒）。"""
    tid = lt.create_thread(
        agent_id,
        "既有的線頭",
        "她在清晨想起昨夜的雨。",
        "whim_driven",
        check_after_ts=check_after.astimezone(timezone.utc).isoformat(),
    )
    assert tid, "fixture 建線頭失敗"
    return tid


def _spy_round(monkeypatch) -> List[Dict[str, Any]]:
    """攔 `run_origin_round`：記錄入參（仍呼叫真函式）。"""
    calls: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        calls.append({"args": a, "kwargs": k})
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    return calls


def _spy_claim(monkeypatch) -> List[str]:
    """攔 `claim_bootstrap`：只計數（**不呼叫真函式** ⇒ 不寫檔）。"""
    calls: List[str] = []

    def spy(marker_path, *, now_iso):
        calls.append(str(marker_path))
        return False

    monkeypatch.setattr(lt_boot, "claim_bootstrap", spy)
    return calls


def _spy_gate(monkeypatch) -> List[Dict[str, Any]]:
    """攔 M3 `evaluate_wake_gate`：記錄入參（仍呼叫真函式）—— 供 BS-5 用。"""
    calls: List[Dict[str, Any]] = []
    real = lt_gate.evaluate_wake_gate

    def spy(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(lt_gate, "evaluate_wake_gate", spy)
    return calls


# ══════════════════════════════════════════════════════════════
# AST／原始碼小工具
# ══════════════════════════════════════════════════════════════


def _leaf_imports(tree: ast.AST) -> List[str]:
    """把 AST 攤平成「被 import 的葉節點模組路徑」清單。"""
    leaves: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            leaves += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.names:
                leaves += [f"{mod}.{a.name}" for a in node.names]
            elif mod:
                leaves.append(mod)
    return leaves


def _calls_named(node: ast.AST) -> List[str]:
    """所有被呼叫的函式／屬性名（含 `f()` 與 `x.f()` 兩種形態）。"""
    names: List[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


# ══════════════════════════════════════════════════════════════
# 1. 介面釘死（契約 §4.4 的常數與簽名）
# ══════════════════════════════════════════════════════════════


def test_01_constants_pinned():
    """契約 §4.4 的常數**逐字**釘死（旗標名／origin_type／標記檔名／真值集合）。"""
    assert lt_boot.BOOTSTRAP_ENABLED_ENV == "LIFE_THREAD_BOOTSTRAP_ENABLED"
    assert lt_boot.BOOTSTRAP_ORIGIN_TYPE == "necessity_driven"
    assert lt_boot.BOOTSTRAP_MARKER_FILENAME == "life_thread_bootstrap.json"
    assert lt_boot.ACTIVE_POOL_SATURATED_REASON == "ACTIVE_POOL_SATURATED"
    assert lt_boot.BOOTSTRAP_TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})
    assert isinstance(lt_boot.BOOTSTRAP_TRUTHY_VALUES, frozenset)


def test_02_origin_type_is_one_of_four_not_a_fifth():
    """🔴 `necessity_driven` **必須**是 §2.3 四值之一 —— **不得**發明第五個。"""
    assert lt_boot.BOOTSTRAP_ORIGIN_TYPE in lt.ORIGIN_TYPES
    assert len(lt.ORIGIN_TYPES) == 4, lt.ORIGIN_TYPES


def test_03_active_pool_reason_matches_m3_literal():
    """容量理由常數**逐字等於** M3 的 `REASON_ACTIVE_POOL_SATURATED`（但**無 import 相依**）。"""
    assert lt_boot.ACTIVE_POOL_SATURATED_REASON == lt_gate.REASON_ACTIVE_POOL_SATURATED


def test_04_public_interface_complete():
    """公開介面逐字齊備（名稱不得漂移）。"""
    for name in (
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
    ):
        assert hasattr(lt_boot, name), name


def test_05_marker_path_derived_from_threads_path_parent(iso_env):
    """標記路徑 ＝ `Path(threads_path).parent / "life_thread_bootstrap.json"`（§4.4 落點）。"""
    threads = lt.life_threads_path(AGENT)
    marker = lt_boot.bootstrap_marker_path(threads)
    assert marker == threads.parent / lt_boot.BOOTSTRAP_MARKER_FILENAME
    assert marker.parent == threads.parent
    assert marker.name == "life_thread_bootstrap.json"
    # 逐字對上 §4.4 的 `data/soul/<agent_id>/life_thread_bootstrap.json`
    assert marker == Path(iso_env) / "soul" / AGENT / "life_thread_bootstrap.json"


def test_06_marker_path_never_raises_on_garbage():
    """路徑組不出來 ⇒ 不得 raise（fail-closed 哨兵）。"""

    class _Explosive:
        @property
        def parent(self):
            raise RuntimeError("boom")

    out = lt_boot.bootstrap_marker_path(_Explosive())
    assert isinstance(out, Path)


# ══════════════════════════════════════════════════════════════
# 2. 旗標矩陣（呼叫時語意；fail-safe 方向 ＝ OFF）
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw",
    ["1", "true", "yes", "on", "ON", "YeS", " 1 ", "TRUE", "On", "\ttrue\n"],
)
def test_10_flag_truthy(monkeypatch, raw):
    """真值（不分大小寫、去首尾空白）⇒ `True`。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, raw)
    assert lt_boot.bootstrap_enabled() is True


@pytest.mark.parametrize(
    "raw",
    ["", " ", "0", "false", "off", "no", "FALSE", "Off", "NO", "2", "y", "t",
     "enabled", "true!", " on！", "\t"],
)
def test_11_flag_falsy(monkeypatch, raw):
    """空字串／空白／非真值 ⇒ `False`（fail-safe 方向）。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, raw)
    assert lt_boot.bootstrap_enabled() is False


def test_12_flag_absent_is_off(monkeypatch):
    """缺席（＝預設）⇒ `False`（契約 §4.4「落地即休眠」）。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    assert lt_boot.bootstrap_enabled() is False


def test_13_flag_read_at_call_time_not_import_time(monkeypatch):
    """🔴 **呼叫時**讀取（非匯入時快取）：import 後才 setenv ⇒ **立即**反映。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    assert lt_boot.bootstrap_enabled() is False
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "on")
    assert lt_boot.bootstrap_enabled() is True, "模組已 import 過 ⇒ 必須仍反映最新 env"
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "off")
    assert lt_boot.bootstrap_enabled() is False, "反向亦須立即反映"
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    assert lt_boot.bootstrap_enabled() is False


# ══════════════════════════════════════════════════════════════
# 3. `read_marker`（fail-quiet：讀不到 ⇒ 視為「已設」）
# ══════════════════════════════════════════════════════════════


def test_20_read_marker_missing_returns_none(tmp_path):
    """檔案不存在 ⇒ `None`（＝未設）。"""
    assert lt_boot.read_marker(tmp_path / "nope.json") is None


def test_21_read_marker_valid_dict_returned(tmp_path):
    """合法 JSON dict ⇒ 原樣回傳。"""
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"bootstrapped_at": "t", "count": 1}), encoding="utf-8")
    assert lt_boot.read_marker(p) == {"bootstrapped_at": "t", "count": 1}


@pytest.mark.parametrize(
    "content",
    ["", "  ", "{", "not json", "null", "[]", "[1,2]", "3", '"str"', "true"],
)
def test_22_read_marker_unreadable_returns_set_sentinel(tmp_path, content):
    """壞 JSON／空檔／合法 JSON 但**非 dict** ⇒ truthy 哨兵（＝已設 ⇒ 不 bootstrap）。"""
    p = tmp_path / "m.json"
    p.write_text(content, encoding="utf-8")
    out = lt_boot.read_marker(p)
    assert out, f"讀不懂必須視為「已設」：{content!r}"
    assert out.get("__unreadable__") is True


def test_23_read_marker_directory_and_io_error(tmp_path, monkeypatch):
    """是目錄／IO 例外 ⇒ truthy 哨兵，**永不 raise**。"""
    d = tmp_path / "m.json"
    d.mkdir()
    assert lt_boot.read_marker(d).get("__unreadable__") is True

    def _boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(lt_boot, "open", _boom, raising=False)
    assert lt_boot.read_marker(tmp_path / "whatever.json").get("__unreadable__") is True


# ══════════════════════════════════════════════════════════════
# 4. `bootstrap_should_fire`（六條件全成立才 True；任一破壞 ⇒ False）
# ══════════════════════════════════════════════════════════════


def _fire(tmp_path, **over):
    """全條件成立的基準呼叫；`over` 逐項破壞（含覆寫 `marker_path`）。"""
    kw: Dict[str, Any] = {
        "enabled": True,
        "m3_should_wake": False,
        "m3_reason": lt_gate.REASON_REFLECTION_SLOT_CLEAR,
        "active_count": 0,
        "capacity": 2,
        "marker_path": tmp_path / lt_boot.BOOTSTRAP_MARKER_FILENAME,
    }
    kw.update(over)
    return lt_boot.bootstrap_should_fire(**kw)


def test_30_all_conditions_met_fires(tmp_path):
    """六條件全成立 ⇒ `True`（且此時標記檔不存在）。"""
    marker = tmp_path / lt_boot.BOOTSTRAP_MARKER_FILENAME
    assert not marker.exists()
    assert _fire(tmp_path) is True


@pytest.mark.parametrize(
    "broken, over",
    [
        ("enabled=False", {"enabled": False}),
        ("enabled=None", {"enabled": None}),
        ("enabled=1（非 True 身分）", {"enabled": 1}),
        ("enabled='true'（字串）", {"enabled": "true"}),
        ("m3_should_wake=True", {"m3_should_wake": True}),
        ("m3_should_wake=None", {"m3_should_wake": None}),
        ("m3_reason=ACTIVE_POOL_SATURATED", {"m3_reason": "ACTIVE_POOL_SATURATED"}),
        (
            "m3_reason=容量常數（import 自本模組）",
            {"m3_reason": "ACTIVE_POOL_SATURATED"},
        ),
        ("active_count=1", {"active_count": 1}),
        ("active_count=None", {"active_count": None}),
        ("active_count=-1", {"active_count": -1}),
        ("active_count='0'（字串）", {"active_count": "0"}),
        ("active_count=True（bool）", {"active_count": True}),
        ("capacity=0", {"capacity": 0}),
        ("capacity=-3", {"capacity": -3}),
        ("capacity=None", {"capacity": None}),
        ("capacity='2'（字串）", {"capacity": "2"}),
        ("capacity=True（bool）", {"capacity": True}),
    ],
)
def test_31_single_break_never_fires(tmp_path, broken, over):
    """🔴 **逐項單一破壞** ⇒ `False`（少一個條件都不行）。"""
    assert _fire(tmp_path, **over) is False, broken


def test_32_marker_present_never_fires(tmp_path):
    """條件⑥：標記**已存在** ⇒ `False`（跨重啟亦然的持久防重）。"""
    marker = tmp_path / lt_boot.BOOTSTRAP_MARKER_FILENAME
    marker.write_text(json.dumps({"bootstrapped_at": "t", "count": 1}), encoding="utf-8")
    assert _fire(tmp_path) is False


def test_33_marker_unreadable_never_fires(tmp_path):
    """條件⑥（fail-quiet）：標記**存在但讀不懂** ⇒ 視為已設 ⇒ `False`。"""
    marker = tmp_path / lt_boot.BOOTSTRAP_MARKER_FILENAME
    marker.write_text("{ 半寫的壞檔", encoding="utf-8")
    assert _fire(tmp_path) is False


def test_34_marker_is_a_directory_never_fires(tmp_path):
    """條件⑥：標記位置是**目錄**（讀不到）⇒ `False`。"""
    marker = tmp_path / lt_boot.BOOTSTRAP_MARKER_FILENAME
    marker.mkdir()
    assert _fire(tmp_path) is False


@pytest.mark.parametrize(
    "over",
    [
        {"active_count": None},
        {"active_count": "zero"},
        {"active_count": -1},
        {"active_count": [0]},
        {"capacity": None},
        {"capacity": "cap"},
        {"capacity": -1},
        {"capacity": float("inf")},
        {"enabled": object()},
        {"marker_path": None},
        {"marker_path": 12345},
        {"marker_path": object()},
        {"marker_path": ""},
        {"marker_path": 1.5},
    ],
)
def test_35_garbage_inputs_never_raise(tmp_path, over):
    """🔴 亂輸入（None／字串／負數／超大／怪物件／壞路徑）⇒ `False`，**不得 raise**。"""
    assert _fire(tmp_path, **over) is False


def test_35d_int_like_path_never_falls_into_fd_semantics(tmp_path):
    """🔴 `bool`／`int` 若**直接**餵給 `open()` 會被當成 **file descriptor**
    （`True` ＝ fd 1 ⇒ 阻塞讀取、甚至關掉呼叫端的 fd）。本模組以 `Path()` 正規化擋掉。

    以 daemon 執行緒 ＋ 逾時探測：若哪天有人拿掉那道正規化，本測試會**變紅**
    （而非讓整個測試行程卡死）。
    """
    box: Dict[str, Any] = {}

    def _probe():
        box["out"] = lt_boot.read_marker(True)

    th = threading.Thread(target=_probe, daemon=True)
    th.start()
    th.join(timeout=5.0)
    assert not th.is_alive(), "read_marker(True) 阻塞 ⇒ 疑似落到 open() 的 fd 語意"
    assert box["out"] == {"__unreadable__": True}

    for bad in (True, False, 0, 1, 7):
        assert _fire(tmp_path, marker_path=bad) is False


@pytest.mark.parametrize("reason", [None, "", "  ", object(), 0, [], "FAIL_CLOSED_DEFAULT_SLEEP"])
def test_35b_non_saturated_reason_does_not_block(tmp_path, reason):
    """條件③的口徑：**只有**字面 `ACTIVE_POOL_SATURATED` 才擋；其他理由（含怪型別）
    不擋、且**不得 raise**（其餘條件成立 ⇒ `True`）。"""
    assert _fire(tmp_path, m3_reason=reason) is True


def test_36_never_raise_when_read_marker_explodes(tmp_path, monkeypatch):
    """`read_marker` 內部爆掉 ⇒ 仍回 `False`（雙層 fail-closed）。"""

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lt_boot, "read_marker", _boom)
    assert _fire(tmp_path) is False


# ══════════════════════════════════════════════════════════════
# 5. `claim_bootstrap`（先蓋章；原子替換；失敗 ⇒ False）
# ══════════════════════════════════════════════════════════════


def test_40_claim_writes_marker_with_required_content(tmp_path):
    """成功 ⇒ 檔案被建立、含 `bootstrapped_at` 與 `count == 1`。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    assert lt_boot.claim_bootstrap(marker, now_iso=MORNING.isoformat()) is True
    assert marker.is_file()
    data = json.loads(marker.read_text(encoding="utf-8"))
    for key in _MARKER_REQUIRED_KEYS:
        assert key in data, key
    assert data["count"] == 1
    assert data["bootstrapped_at"] == MORNING.isoformat()


def test_41_claim_creates_parent_directories(tmp_path):
    """父目錄自動建立（`parents=True, exist_ok=True`）。"""
    marker = tmp_path / "soul" / AGENT / "life_thread_bootstrap.json"
    assert not marker.parent.exists()
    assert lt_boot.claim_bootstrap(marker, now_iso="2026-09-06T08:00:00-04:00") is True
    assert marker.is_file()


def test_42_claim_is_readable_back_and_blocks_fire(tmp_path):
    """第二次 `read_marker` 讀得回；且 `should_fire` 立刻變 `False`（一次性）。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    assert lt_boot.claim_bootstrap(marker, now_iso="t") is True
    assert lt_boot.read_marker(marker) == {"bootstrapped_at": "t", "count": 1}
    assert _fire(tmp_path) is False


def test_43_claim_leaves_no_temp_files(tmp_path):
    """原子替換後**不留暫存檔**（0 殘留）。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    assert lt_boot.claim_bootstrap(marker, now_iso="t") is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["life_thread_bootstrap.json"]


def test_44_claim_overwrites_atomically(tmp_path):
    """既有標記 ⇒ 原子替換（內容換新、仍為合法 JSON、0 暫存殘留）。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    marker.write_text(json.dumps({"bootstrapped_at": "old", "count": 1}), encoding="utf-8")
    assert lt_boot.claim_bootstrap(marker, now_iso="new") is True
    assert lt_boot.read_marker(marker) == {"bootstrapped_at": "new", "count": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["life_thread_bootstrap.json"]


def test_45_claim_write_failure_returns_false_without_raise(tmp_path):
    """父路徑被一般檔案佔住 ⇒ `False`、**不 raise**、0 殘留。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    assert lt_boot.claim_bootstrap(blocker / "sub" / "m.json", now_iso="t") is False
    assert blocker.read_text(encoding="utf-8") == "x"


def test_46_claim_onto_directory_fails_and_cleans_temp(tmp_path):
    """目標是既有目錄 ⇒ `os.replace` 失敗 ⇒ `False`，且暫存檔被清掉。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    marker.mkdir()
    assert lt_boot.claim_bootstrap(marker, now_iso="t") is False
    assert marker.is_dir()
    assert list(tmp_path.glob("*.tmp")) == [], "失敗時不得留暫存檔"


@pytest.mark.parametrize("bad", [None, 12345, object()])
def test_47_claim_garbage_target_never_raises(tmp_path, bad):
    """亂輸入 ⇒ `False`，**不得 raise**、不得留下任何檔案。"""
    assert lt_boot.claim_bootstrap(bad, now_iso="t") is False


@pytest.mark.parametrize("bad", ["", "   ", "."])
def test_47b_claim_empty_string_target_fails_closed(tmp_path, monkeypatch, bad):
    """空／空白／`.` 目標 ⇒ `False`（目錄不可被 replace），且**不留暫存檔**。"""
    monkeypatch.chdir(tmp_path)
    assert lt_boot.claim_bootstrap(bad, now_iso="t") is False
    assert list(tmp_path.glob("*.tmp")) == []


def test_48_claim_tolerates_non_string_now_iso(tmp_path):
    """`now_iso` 非字串 ⇒ 收斂成字串寫入（**不得 raise**）。"""
    marker = tmp_path / "life_thread_bootstrap.json"
    assert lt_boot.claim_bootstrap(marker, now_iso=None) is True
    assert isinstance(lt_boot.read_marker(marker)["bootstrapped_at"], str)


# ══════════════════════════════════════════════════════════════
# 6. 模組純度（0 `src.*` import／匯入時 0 I/O／不得 import M3）
# ══════════════════════════════════════════════════════════════


def test_p0_module_docstring_declares_nature_and_mapping():
    """docstring 必聲明：純標準庫／0 `src.*` import／匯入時 0 I/O／§4.4 對應／
    不決定 `origin_type` 語意（只提供常數）。"""
    doc = lt_boot.__doc__ or ""
    assert "純標準庫" in doc
    assert "0 個 `src.*` import" in doc
    assert "匯入時 0 I/O" in doc
    assert "§4.4" in doc
    assert "origin_type" in doc and "只提供常數" in doc


def test_p1_zero_src_imports_and_stdlib_only():
    """AST：**0** 個 `src.*` import；且**只** import 標準庫（無相對 import 逃逸）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    leaves = _leaf_imports(tree)
    assert [x for x in leaves if x.startswith("src.")] == [], leaves
    assert [x for x in leaves if x.startswith("soul")] == [], leaves
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.level or 0) == 0, "不得用相對 import 逃逸"
    roots = {x.split(".")[0] for x in leaves}
    assert roots <= {"__future__", "json", "os", "tempfile", "pathlib", "typing"}, roots


def test_p2_never_imports_m3_wake_gate():
    """🔴 **不得** import M3（凍結面）：容量理由以**字串常數**自行比對。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    leaves = _leaf_imports(tree)
    assert not any("life_thread_wake_gate" in x for x in leaves), leaves
    assert not any("life_thread" in x for x in leaves if x.startswith("src.")), leaves


def test_p3_no_io_at_module_import_time_ast():
    """模組層語句不得有任何 I/O 呼叫（唯一允許者 ＝ `frozenset`（純建構子））。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    calls: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        calls += _calls_named(node)
    assert set(calls) <= {"frozenset"}, f"模組層不得有 I/O：{sorted(set(calls))}"


def test_p4_module_body_executes_with_open_disarmed(monkeypatch):
    """**執行期**自證：把 `open` 換成必爆 ⇒ 模組 body 仍能執行完（匯入時 0 I/O）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    ns: Dict[str, Any] = {"__name__": "life_thread_bootstrap_purity_probe"}

    def _boom(*a, **k):
        raise AssertionError(f"匯入時不得有任何 I/O：{a[:1]}")

    monkeypatch.setattr(builtins, "open", _boom)
    exec(compile(src, str(MODULE_PATH), "exec"), ns)  # noqa: S102 - 純度探針
    assert ns["BOOTSTRAP_MARKER_FILENAME"] == "life_thread_bootstrap.json"
    assert ns["BOOTSTRAP_TRUTHY_VALUES"] == frozenset({"1", "true", "yes", "on"})


# ══════════════════════════════════════════════════════════════
# 7. orchestrator 整合（tmp 資料根 ＋ 死代理 ＋ stub LLM）
# ══════════════════════════════════════════════════════════════


def test_i1_flag_on_zero_threads_bootstraps_exactly_once(iso_env, monkeypatch):
    """BS-2 ①：旗標 ON ＋ 0 線頭 ＋ M3 判 SLEEP(非容量) ⇒ **恰 1 次** origins 輪。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    rounds = _spy_round(monkeypatch)
    spy = _SpyLLM(_json_response([
        {"op": "create", "title": "夜裡的散步", "narrative_content": "她想起今晚還沒吃飯。",
         "next_check_hours": 8},
    ]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    summary = out[AGENT]

    # ① 可觀測：bootstrap 旗標 ＋ origin_type
    assert summary["woke"] is True
    assert summary["bootstrap"] is True
    assert summary["origin_type"] == lt_boot.BOOTSTRAP_ORIGIN_TYPE == "necessity_driven"
    assert summary["reason"] != lt_gate.REASON_ACTIVE_POOL_SATURATED

    # ② stub LLM **恰 1 次**；`run_origin_round` **恰 1 次**且收到 necessity_driven
    assert len(spy.calls) == 1, "bootstrap 必須恰 1 次 LLM"
    assert len(rounds) == 1
    assert rounds[0]["args"][0] == AGENT
    assert rounds[0]["args"][1] == "necessity_driven", rounds[0]["args"][1]
    assert summary["origin_round"]["called"] is True
    assert summary["origin_round"]["prompt_available"] is True
    assert summary["origin_round"]["origin_type"] == "necessity_driven"

    # ③ 標記檔已生成（且內容合規）
    marker = _marker_path()
    assert marker.is_file(), "bootstrap 必須留下標記（at-most-once 的唯一持久防重）"
    data = _read_marker_file()
    assert data["count"] == 1
    assert data["bootstrapped_at"] == MORNING.isoformat()

    # ④ 落盤線頭的 origin_type 也是 necessity_driven（§5.2.2 模板走 M4）
    entries = [
        json.loads(x)
        for x in lt.life_threads_path(AGENT).read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert entries and entries[0]["origin_type"] == "necessity_driven"


def test_i2_marker_is_the_durable_dedup(iso_env, monkeypatch):
    """BS-2／BS-3：同 agent 再跑 ⇒ **0 次 LLM**、標記不變；且標記是**唯一**防重。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))  # 空 actions ⇒ 不落盤線頭（保持 0 active）
    marker = _marker_path()

    first = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert first[AGENT]["bootstrap"] is True
    assert len(spy.calls) == 1
    assert marker.is_file()
    before = marker.read_bytes()

    # (a) 既有 at-most-once 章仍有效：同 (agent, slot, date) ⇒ duplicate_slot
    second = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert second == {AGENT: {"skipped": "duplicate_slot"}}
    assert len(spy.calls) == 1

    # (b) 清掉行程內章（模擬跨重啟）⇒ 仍 0 次 LLM（標記擋住）
    m5.reset_state()
    third = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert len(spy.calls) == 1, "標記存在 ⇒ 跨重啟亦不得再 bootstrap"
    assert third[AGENT]["woke"] is False
    assert "bootstrap" not in third[AGENT]
    assert marker.read_bytes() == before, "標記不得被重寫"
    assert lt_boot.bootstrap_should_fire(
        enabled=True,
        m3_should_wake=False,
        m3_reason=lt_gate.REASON_REFLECTION_SLOT_CLEAR,
        active_count=0,
        capacity=2,
        marker_path=marker,
    ) is False

    # (c) 反事實（teeth）：**移除標記** ⇒ 同條件下重新 bootstrap
    #     ⇒ 證明擋住第二次的確實是「標記」，不是別的因素。
    marker.unlink()
    m5.reset_state()
    fourth = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert fourth[AGENT]["bootstrap"] is True
    assert len(spy.calls) == 2, "標記被移除 ⇒ 反事實成立（標記確為唯一防重）"


def test_i3_same_agent_second_slot_does_not_re_bootstrap(iso_env, monkeypatch):
    """BS-2／BS-3：跨 slot（morning→night）亦**不得**再 bootstrap（標記為 epoch 界線）。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    a = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert a[AGENT]["bootstrap"] is True
    b = _run([AGENT], NIGHT, "night", llm_caller=spy)
    assert len(spy.calls) == 1, "第二次（不同 slot）仍須 0 LLM"
    assert b[AGENT]["woke"] is False
    assert "bootstrap" not in b[AGENT]


def test_i4_flag_off_zero_llm_zero_marker(iso_env, monkeypatch):
    """BS-1：旗標 OFF（缺席）⇒ 本節**完全不執行**：0 LLM、標記檔**不被建立**。"""
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    _patch_soul(monkeypatch)
    claims = _spy_claim(monkeypatch)
    rounds = _spy_round(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is False
    assert "bootstrap" not in out[AGENT]
    assert len(spy.calls) == 0
    assert rounds == []
    assert claims == [], "旗標 OFF ⇒ 不得嘗試蓋章"
    assert not _marker_path().exists()
    assert list(Path(iso_env).rglob("life_thread_bootstrap.json")) == []


def test_i5_existing_active_thread_does_not_bootstrap(iso_env, monkeypatch):
    """條件①：已有 1 條 active 線頭 ⇒ **不 bootstrap**、0 LLM。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    _seed_thread(check_after=MORNING + timedelta(hours=6))  # 未到期 ⇒ M3 不因判定 1 醒
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is False
    assert out[AGENT]["reason"] != lt_gate.REASON_ACTIVE_POOL_SATURATED, (
        "本測試必須是「非容量」的 SLEEP，否則只證明了容量防線"
    )
    assert len(spy.calls) == 0
    assert not _marker_path().exists()


def test_i6_capacity_saturated_does_not_bootstrap_nor_stamp(iso_env, monkeypatch):
    """條件③／落點第 2 點：容量飽和（`active_count >= capacity` 且
    reason=`ACTIVE_POOL_SATURATED`）⇒ **不 bootstrap、不蓋章**。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    _seed_thread(check_after=MORNING + timedelta(hours=6))
    _seed_thread(check_after=MORNING + timedelta(hours=6))
    assert len(lt.list_active(AGENT)) == 2
    assert lt.capacity(AGENT) == 2, "fixture 前提：預設容量 2（fail-closed）"

    claims = _spy_claim(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["reason"] == lt_gate.REASON_ACTIVE_POOL_SATURATED
    assert out[AGENT] == {
        "woke": False,
        "reason": "ACTIVE_POOL_SATURATED",
        "dissolved_candidates": 0,
    }, "SLEEP 返回 dict 必須逐字沿用既有鍵與值"
    assert len(spy.calls) == 0
    assert claims == [], "不得蓋章"
    assert not _marker_path().exists()


def test_i7_empty_soul_context_does_not_stamp_or_spend(iso_env, monkeypatch):
    """BS-4／0-LLM 前置：`soul_context` 空 ⇒ **不蓋章、0 LLM**，
    且返回 dict 與既有 SLEEP 路徑**逐字相同**。"""
    _patch_soul(monkeypatch, value="")
    spy = _SpyLLM(_json_response([]))

    # 對照組（旗標 OFF）：既有 SLEEP 返回
    control = _run([OTHER], MORNING, "morning", llm_caller=spy)
    assert control[OTHER]["woke"] is False

    # 受測（旗標 ON；其餘輸入相同：0 線頭、未飽和、標記未設 ⇒ 條件全成立）
    _set_flag(monkeypatch)
    claims = _spy_claim(monkeypatch)
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)

    assert out[AGENT] == control[OTHER], "soul_context 空 ⇒ 走既有返回分支（逐字相同）"
    assert len(spy.calls) == 0, "soul_context 空 ⇒ 不花錢"
    assert claims == [], "soul_context 空 ⇒ 不得蓋章"
    assert not _marker_path(AGENT).exists()
    assert not _marker_path(OTHER).exists()


def test_i8_read_failure_does_not_bootstrap(iso_env, monkeypatch):
    """BS-4：標記**讀不到**（存在但是目錄）⇒ 0 LLM、不覆寫、不 raise。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.mkdir()  # 存在但讀不懂 ⇒ 視為已設
    claims = _spy_claim(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is False
    assert len(spy.calls) == 0
    assert claims == []
    assert marker.is_dir(), "不得覆寫"


def test_i9_write_failure_does_not_wake(iso_env, monkeypatch):
    """BS-4：蓋章**失敗** ⇒ 不喚醒（無法保證 at-most-once ⇒ 不執行）、0 LLM。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    monkeypatch.setattr(lt_boot, "claim_bootstrap", lambda *a, **k: False)
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is False
    assert "bootstrap" not in out[AGENT]
    assert len(spy.calls) == 0
    assert not _marker_path().exists()


def test_i10_m3_call_arguments_unchanged(iso_env, monkeypatch):
    """BS-5：**M3 逐位元不變** —— bootstrap 分支不得改動 M3 的呼叫參數。

    （bootstrap 在 M3 判定**之後**才接手；M3 仍收到完全相同的 5 位置參數
      ＋ 2 個關鍵字參數，且**不含**任何第三訊號參數。）
    """
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    gate_calls = _spy_gate(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert len(gate_calls) == 1
    call = gate_calls[0]
    assert call["args"][0] == AGENT
    assert call["args"][1] == MORNING.timestamp()
    assert call["args"][2] == "morning"
    assert call["args"][3] == []  # active_threads
    assert call["args"][4] == lt.capacity(AGENT)
    assert set(call["kwargs"]) == {
        "recent_perceptions",
        "enforce_strict_capacity",
    }, call["kwargs"]
    assert call["kwargs"]["recent_perceptions"] == []
    assert call["kwargs"]["enforce_strict_capacity"] is True


def test_i11_flag_on_with_zero_threads_when_m3_wakes_still_uses_m3_origin(
    iso_env, monkeypatch
):
    """M3 判 WAKE 時**不得**被 bootstrap 覆蓋：仍用 `decision.origin_type`（既有行為）。"""
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    due = (MORNING - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    tid = lt.create_thread(AGENT, "到期的線頭", "她在清晨想起昨夜的雨。", "whim_driven",
                           check_after_ts=due)
    assert tid
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is True
    assert out[AGENT]["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE
    assert out[AGENT]["origin_type"] == "whim_driven"
    assert "bootstrap" not in out[AGENT], "M3 判 WAKE ⇒ 非 bootstrap 路徑"
    assert not _marker_path().exists()
    assert len(spy.calls) == 1


# ══════════════════════════════════════════════════════════════
# 8. 變異牙齒（§E：檢查器必須**真的會紅**）
# ══════════════════════════════════════════════════════════════


def _bootstrap_wiring_issues(orch_src: str, mod_src: str) -> List[str]:
    """檢查器：回違規清單（空 ＝ 通過）。**純字串層**（可餵假字串做牙齒自證）。

    比對範圍**錨定在 `if not decision.should_wake:` 之後**（bootstrap 分支所在的區段）——
    模組 docstring 也提到 `run_origin_round(...)`（散文，非呼叫），不入計。

    檢查四件事：
      1. orchestrator 內**存在** `bootstrap_should_fire(` 呼叫（且在 M3 判定之後）。
      2. orchestrator 內**存在** `claim_bootstrap(` 呼叫，且位置**在** `run_origin_round(`
         **之前**（先蓋章再執行）。
      3. orchestrator 把 M3 的 `reason` 餵進閘門（`m3_reason=decision.reason`）。
      4. 閘門模組內有 `ACTIVE_POOL_SATURATED` 的比對（容量理由 ⇒ 不 bootstrap）。
    """
    issues: List[str] = []
    i_sleep = orch_src.find("if not decision.should_wake:")
    if i_sleep == -1:
        return ["orchestrator 找不到 M3 判定的 `if not decision.should_wake:`"]
    region = orch_src[i_sleep:]

    i_fire = region.find("bootstrap_should_fire(")
    i_claim = region.find("claim_bootstrap(")
    i_round = region.find("run_origin_round(")

    if i_fire == -1:
        issues.append("bootstrap 分支缺 `bootstrap_should_fire(` 呼叫")
    if i_claim == -1:
        issues.append("bootstrap 分支缺 `claim_bootstrap(` 呼叫")
    if i_round == -1:
        issues.append("找不到 `run_origin_round(` 呼叫")
    if i_claim != -1 and i_round != -1 and not (i_claim < i_round):
        issues.append("🔴 `claim_bootstrap` 必須在 `run_origin_round` 之前（先蓋章再執行）")
    if "m3_reason=decision.reason" not in region:
        issues.append("orchestrator 必須把 `m3_reason=decision.reason` 餵進閘門")
    if "ACTIVE_POOL_SATURATED" not in mod_src:
        issues.append("閘門模組缺 `ACTIVE_POOL_SATURATED` 比對（容量理由 ⇒ 不 bootstrap）")
    if "ACTIVE_POOL_SATURATED_REASON" not in mod_src:
        issues.append("閘門模組缺 `ACTIVE_POOL_SATURATED_REASON` 常數")
    return issues


def test_e1_real_sources_pass_the_wiring_checker():
    """受測原始碼（orchestrator ＋ 模組）通過連線檢查器。"""
    assert _bootstrap_wiring_issues(
        ORCH_PATH.read_text(encoding="utf-8"), MODULE_PATH.read_text(encoding="utf-8")
    ) == []


def test_e2_wiring_checker_has_teeth_order_guard():
    """🔴 牙齒自證 (a)：把 `claim_bootstrap` **移到大回合之後**（＝移除「先蓋章」守門）
    ⇒ 檢查器必須**變紅**，且紅在「順序」這條上（證明不是空話）。"""
    real_orch = ORCH_PATH.read_text(encoding="utf-8")
    real_mod = MODULE_PATH.read_text(encoding="utf-8")
    assert _bootstrap_wiring_issues(real_orch, real_mod) == []

    # 剪下 `if lt_boot.claim_bootstrap(...)` 那一行，貼到檔尾（＝移到 run_origin_round 之後）
    i = real_orch.find("if lt_boot.claim_bootstrap(")
    assert i != -1
    j = real_orch.find("\n", i)
    line = real_orch[i:j]
    mutated = real_orch[:i] + real_orch[j + 1:] + "\n" + line + "\n"
    issues = _bootstrap_wiring_issues(mutated, real_mod)
    assert any("claim_bootstrap` 必須在 `run_origin_round` 之前" in x for x in issues), issues


def test_e3_wiring_checker_has_teeth_missing_guards():
    """🔴 牙齒自證 (b)：逐項移除守門 ⇒ 檢查器逐項變紅。

    (i) 拿掉 `m3_reason=decision.reason`（不再把 M3 的理由餵進閘門）
    (ii) 拿掉閘門模組的 `ACTIVE_POOL_SATURATED` 比對
    (iii) 拿掉整個 `bootstrap_should_fire(` 呼叫
    """
    real_orch = ORCH_PATH.read_text(encoding="utf-8")
    real_mod = MODULE_PATH.read_text(encoding="utf-8")
    assert _bootstrap_wiring_issues(real_orch, real_mod) == []

    no_reason = real_orch.replace("m3_reason=decision.reason", "m3_reason=None")
    assert no_reason != real_orch, "mutation 必須真的改到東西"
    issues = _bootstrap_wiring_issues(no_reason, real_mod)
    assert any("m3_reason=decision.reason" in x for x in issues), issues

    # (ii) 閘門模組真的拿掉容量理由比對（模擬「移除該守門」的假字串）
    no_sat = real_mod.replace("ACTIVE_POOL_SATURATED", "SOMETHING_ELSE")
    assert no_sat != real_mod
    issues = _bootstrap_wiring_issues(real_orch, no_sat)
    assert any("ACTIVE_POOL_SATURATED" in x for x in issues), issues

    # (iii) 整個閘門呼叫被移除
    no_fire = real_orch.replace("bootstrap_should_fire(", "_not_the_gate(")
    assert no_fire != real_orch
    issues = _bootstrap_wiring_issues(no_fire, real_mod)
    assert any("bootstrap_should_fire(" in x for x in issues), issues


def test_e4_sleep_return_dict_is_byte_identical_to_pre_bootstrap():
    """SLEEP 返回 dict 的**鍵與值逐字保留**（原始碼層比對既有三鍵）。"""
    src = ORCH_PATH.read_text(encoding="utf-8")
    expected = (
        '            return {\n'
        '                "woke": False,\n'
        '                "reason": decision.reason,\n'
        '                "dissolved_candidates": dissolved_candidates,\n'
        '            }'
    )
    assert src.count(expected) == 1, "SLEEP 返回 dict 必須恰 1 處、逐字不變"


def test_e5_bootstrap_branch_inside_sleep_guard():
    """bootstrap 分支必須**在** `if not decision.should_wake:` 之內（M3 判定之後）。"""
    src = ORCH_PATH.read_text(encoding="utf-8")
    i_sleep = src.find("if not decision.should_wake:")
    region = src[i_sleep:]
    assert i_sleep != -1
    i_fire = region.find("bootstrap_should_fire(")
    i_claim = region.find("claim_bootstrap(")
    i_round = region.find("run_origin_round(")
    i_return = region.find("return summary")
    assert -1 < i_fire < i_claim < i_round < i_return, (
        "順序必須是：閘門 → 蓋章 → 大回合（先蓋章再執行）"
    )


# ══════════════════════════════════════════════════════════════
# 9. LIFE-THREAD-BOOTSTRAP-FUP-1：不安全 `agent_id` 的 fail-quiet
#    （獨立審計抓到的確鑿缺陷：`life_threads_path()` 對不安全 agent_id 會
#      `raise ValueError`，而該呼叫原本落在**旗標檢查之前** ⇒ 旗標 OFF（預設）
#      時也會拋例外，於 `run_slot_pipeline` 被收斂成 `{"error": ...}`
#      —— 這是本票**新增**的失效模式，違反「旗標 OFF ⇒ 本節完全不執行」
#      與「fail-closed／永不 raise」。修法＝把推導整段關進旗標之內並 fail-quiet。）
# ══════════════════════════════════════════════════════════════

#: 不安全 agent_id（含 `..`，違反 M1 `_validate_agent_id`）。
UNSAFE_AGENT = "../unsafe_agent"

#: 既有 SLEEP 返回的**逐字三鍵**（契約 §4.4 引入 bootstrap 之前即為此形）。
_SLEEP_KEYS = {"woke", "reason", "dissolved_candidates"}


def _markers_under(root) -> List[Path]:
    """tmp 資料根底下所有 bootstrap 標記檔（0 檔 ＝ 從未蓋章）。"""
    return list(Path(root).rglob(lt_boot.BOOTSTRAP_MARKER_FILENAME))


def test_f1_unsafe_agent_id_flag_off_is_quiet(iso_env, monkeypatch):
    """🔴 缺陷 1 迴歸：旗標**未設**（預設 OFF）＋ 不安全 `agent_id`
    ⇒ **平靜返回**既有三鍵 SLEEP dict（0 `"error"` 鍵、0 raise、0 LLM、無標記檔）。

    「旗標 OFF ⇒ 本節完全不執行」是契約 §4.4 的硬性邊界：舊版（bootstrap 引入前）
    在此輸入下是平靜的 SLEEP；本票不得讓它變成 `{"error": ...}`。
    """
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    _patch_soul(monkeypatch)
    rounds = _spy_round(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([UNSAFE_AGENT], MORNING, "morning", llm_caller=spy)
    assert UNSAFE_AGENT in out, out
    summary = out[UNSAFE_AGENT]

    # ① 鍵集合**恰為**三鍵；`"error"` 鍵不得存在（這正是缺陷 1 的紅燈形狀）
    assert set(summary) == _SLEEP_KEYS, summary
    assert "error" not in summary, summary
    # ② `woke is False`（不是 falsy，是**真 False**）
    assert summary["woke"] is False, summary
    # ③ 既有三鍵的語意逐字不變（reason 為非空字串、dissolved_candidates 為 0）
    assert isinstance(summary["reason"], str) and summary["reason"], summary
    assert summary["dissolved_candidates"] == 0, summary
    # ④ 0 LLM、0 大回合
    assert len(spy.calls) == 0, spy.calls
    assert rounds == [], rounds
    # ⑤ 標記檔**未被建立**（整棵 tmp 資料根 0 命中）
    assert _markers_under(iso_env) == [], _markers_under(iso_env)


def test_f2_unsafe_agent_id_flag_on_is_quiet(iso_env, monkeypatch):
    """🔴 缺陷 1 迴歸（旗標 **ON**）：不安全 `agent_id` ⇒ 推導標記路徑時
    `ValueError` 必須被 **fail-quiet** 吞掉 ⇒ 平靜返回三鍵 SLEEP dict
    （0 `"error"`、0 LLM、0 蓋章、無標記檔、**0 raise**）。
    """
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    claims = _spy_claim(monkeypatch)
    rounds = _spy_round(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([UNSAFE_AGENT], MORNING, "morning", llm_caller=spy)
    summary = out[UNSAFE_AGENT]

    assert set(summary) == _SLEEP_KEYS, summary
    assert "error" not in summary, summary
    assert summary["woke"] is False, summary
    assert summary["dissolved_candidates"] == 0, summary
    assert len(spy.calls) == 0, spy.calls
    assert rounds == [], rounds
    assert claims == [], "不安全 agent_id ⇒ 不得嘗試蓋章"
    assert _markers_under(iso_env) == [], _markers_under(iso_env)


def test_f3_flag_off_derives_no_marker_path(iso_env, monkeypatch):
    """🔴 釘死「旗標 OFF ⇒ 本節**完全**不執行」：對 **orchestrator 實際引用的命名空間**
    （`m5.lt`，即 orchestrator 內 `lt.life_threads_path` 的解析面）掛計數 spy。

    - 旗標未設 ⇒ 推導次數 **== 0**（本節一步都不跑）。
    - 旗標 `"1"` ＋ 正常 agent ⇒ 次數 **≥ 1**（證明 spy 真的掛上了 ⇒ 不是假綠）。

    ⚠️ 為何要 shim `m5.lt` 而不是 `monkeypatch.setattr(lt, "life_threads_path", spy)`：
    M1 自己（`read_entries`）也走**同一個模組全域** `life_threads_path`，整模組替換會把
    M1 內部的呼叫一併計入 ⇒ 就算 orchestrator 0 次推導，計數也會 ≥ 1，本測試將永遠
    無法變紅。只替換 orchestrator 視角的 `m5.lt` 才精準量到「**本節**是否執行」。
    """
    _patch_soul(monkeypatch)
    real = lt.life_threads_path
    calls: List[str] = []

    def spy(agent_id):
        calls.append(agent_id)
        return real(agent_id)

    class _CountingLt:
        """orchestrator 視角的 `lt`：只把 `life_threads_path` 換成計數器，其餘轉真模組。"""

        def __init__(self, real_mod, counter):
            self._real = real_mod
            self.life_threads_path = counter

        def __getattr__(self, name):
            return getattr(self._real, name)

    monkeypatch.setattr(m5, "lt", _CountingLt(lt, spy))

    # (a) 旗標 OFF（未設）⇒ 0 次推導
    monkeypatch.setenv(lt_boot.BOOTSTRAP_ENABLED_ENV, "")
    out = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert out[AGENT]["woke"] is False, out[AGENT]
    assert calls == [], f"旗標 OFF ⇒ 不得推導標記路徑（實得 {calls}）"

    # (b) 旗標 ON ＋ 正常 agent ⇒ ≥ 1 次推導（spy 有效性自證：反事實）
    m5.reset_state()
    _set_flag(monkeypatch)
    out2 = _run([AGENT], NIGHT, "night", llm_caller=_SpyLLM(_json_response([])))
    assert len(calls) >= 1, f"旗標 ON ⇒ 必須推導標記路徑（實得 {calls}）"
    assert out2[AGENT]["bootstrap"] is True, out2[AGENT]


def test_f4_flag_on_normal_agent_still_bootstraps_no_regression(iso_env, monkeypatch):
    """正常路徑**未回歸**：旗標 ON ＋ 正常 agent（0 線頭）仍**會** bootstrap 喚醒。

    指名既有測試 `test_i1_flag_on_zero_threads_bootstraps_exactly_once`（同一斷言面，
    該測試必須仍綠）；此處再以**獨立斷言**就地釘死（避免只靠別處的綠燈轉述）。
    """
    _set_flag(monkeypatch)
    _patch_soul(monkeypatch)
    rounds = _spy_round(monkeypatch)
    spy = _SpyLLM(_json_response([]))

    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    summary = out[AGENT]
    assert summary["woke"] is True, summary
    assert summary["bootstrap"] is True, summary
    assert summary["origin_type"] == lt_boot.BOOTSTRAP_ORIGIN_TYPE, summary
    assert len(spy.calls) == 1, spy.calls
    assert len(rounds) == 1, rounds
    assert _marker_path(AGENT).is_file(), "正常路徑仍須留下 at-most-once 標記"
