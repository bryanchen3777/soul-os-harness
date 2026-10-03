# tests/soul/test_life_thread_whim_origin.py
# LIFE-THREAD-W1 — 契約 §4.5：`whim_driven` 接線（第四條喚醒路徑）驗收矩陣。
#
# 受測模組：`src/soul/life_thread_orchestrator.py`（唯一的改動面）
# 凍結面（**本檔只讀、0 改動**）：`src/soul/life_thread_wake_gate.py`（M3）
#
# 範式沿用 `tests/soul/test_life_thread_m5_wiring.py`：資料根隔離 fixture
# （`SOUL_OS_DATA_DIR` ＋ `reset_data_root`）／`_SpyLLM` mock LLM 計數／
# `monkeypatch.setattr` spy 實際函式／AST 凍結面守門。
#
# 🔴 LLM **一律用 stub**（`llm_caller=`）—— 本檔 0 真實 LLM、0 網路、0 伺服器、
#    0 生產埠請求。
# 🔴 本檔**不讀寫生產 `data/**`**：所有落地檔都在隔離的 tmp 內。
# 🔴 INV-7（留白是成功）：本檔**沒有**任何「線頭數 > 0」形式的 assert。
from __future__ import annotations

import ast
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_orchestrator as orch  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

AGENT = "agent_whim"
SOUL = "我是ルカ。我喜歡在夜裡散步，討厭吵鬧的地方。"

ORCHESTRATOR_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
WAKE_GATE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_wake_gate.py"

#: 契約 §2.3 的四值域（**不得**新增第五值）—— 逐字釘死。
ORIGIN_TYPE_VALUES = frozenset(
    {"goal_driven", "necessity_driven", "whim_driven", "world_collision"}
)


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
def _clean_module_state(monkeypatch):
    """每個測試前後清空兩個 process-global 狀態，並確保旗標**被釘成 OFF**。

    - `m5.reset_state()`：既有 at-most-once 冪等鍵（既有慣例）。
    - `orch._WHIM_DAILY.clear()`：§4.5 每日計數器（**不經 `reset_state()`** ——
      `reset_state()` 的 docstring 把它限定為 at-most-once 鍵的測試用清理，
      本檔不擴大它的契約面；直接清記憶體 dict 即可，且不動生產語意）。
    - `monkeypatch.setenv(WHIM_ENABLED_ENV, "")`：**釘成空字串**（不是 delenv）——
      這是 `tests/conftest.py` 已記載的同一類地雷：`load_dotenv(..., override=False)`
      會在「變數不存在」時把生產 `.env` 的值**補回** pytest 行程（該檔第 86-101 行
      已對 `LIFE_THREAD_BOOTSTRAP_ENABLED`／`CONSOLIDATION`／`CATCHUP`／`INTIMACY_DECAY`
      等旗標釘 `""`，共 4 次踩點教訓）。`""` 對本旗標是**明確 OFF**（空字串非真值），
      且變數「存在」⇒ dotenv 無權補回 ⇒ 本檔對開發機 `.env` 具免疫性。
      （本檔不修改 `tests/conftest.py` —— 超出本票的三檔範圍；已回報建議補釘。）
    """
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "")
    orch.reset_state()
    orch._WHIM_DAILY.clear()
    yield
    orch.reset_state()
    orch._WHIM_DAILY.clear()


def _loc(hour: int = 8, day_delta: int = 0) -> datetime:
    """本地時區固定時刻（8:00 → morning、22:00 → night；契約 §4.1 的兩個評估點）。"""
    return datetime(2026, 9, 6, hour, tzinfo=LOCAL_TZ) + timedelta(days=day_delta)


MORNING = _loc(8)
NIGHT = _loc(22)


def _run(agent_ids, now, slot, *, llm_caller=None):
    return asyncio.run(orch.run_slot_pipeline(agent_ids, now, slot, llm_caller=llm_caller))


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


def _quiet_llm() -> _SpyLLM:
    """回空 actions 的 stub（可觀測「被呼叫了幾次」，但 0 寫入）。"""
    return _SpyLLM(_json_response([]))


def _patch_soul(monkeypatch, value: str = SOUL) -> None:
    """M4 的 0-LLM 前置檢查（`load_soul_context`）—— 沿用 M5 測試的既有慣例。"""
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: value)


def _spy_origin_round(monkeypatch) -> List[Dict[str, Any]]:
    """攔 M4 `run_origin_round`：記錄 `origin_type` 與 `build_kwargs`（仍呼叫真函式）。"""
    calls: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return await real(*args, **kwargs)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    return calls


def _seed_due_thread(agent_id: str = AGENT, *, origin_type: str = "necessity_driven",
                     now: datetime = MORNING) -> str:
    """建一條**檢驗點已到期**的 active 線頭（M3 §4.2 判定 1 的唯一觸發條件）。

    🔴 `origin_type` 由 M3 **原樣回傳**（`life_thread_wake_gate` 的步驟 2：
    `origin = thread_hint["origin_type"]`）⇒ 這是「非 whim 路徑**沒有**被覆寫」的
    判別依據：線頭用 `necessity_driven` ⇒ 喚醒也必須是 `necessity_driven`。
    """
    due = (now - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    tid = lt.create_thread(
        agent_id, "晨間的線頭", "她在清晨想起昨夜的雨。", origin_type,
        check_after_ts=due,
    )
    assert tid, "fixture 建線頭失敗"
    return tid


def _whim_calls(calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """從 spy 記錄中挑出「實際以 `whim_driven` 喚醒」的呼叫。"""
    return [c for c in calls if c["args"][1] == "whim_driven"]


# ══════════════════════════════════════════════════════════════
# §4.5 接線行為
# ══════════════════════════════════════════════════════════════


def test_whim_flag_off_is_silent(iso_env, monkeypatch):
    """W1：旗標 **OFF（空字串）** ⇒ §4.5 整段不執行（whim 觸發 0 次、LLM 呼叫 0 次）。

    這是本票最重要的 fail-closed 基線：落地後生產環境（旗標未設）必須
    與本節引入前**逐位元相同**。
    """
    assert orch.whim_enabled() is False, "前置：本測試必須在旗標 OFF 下執行"
    _patch_soul(monkeypatch)
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)
    summary = out[AGENT]

    assert calls == [], f"旗標缺席 ⇒ 0 輪 origins：{calls}"
    assert _whim_calls(calls) == []
    assert llm.calls == [], "旗標缺席 ⇒ 0 LLM 呼叫"
    assert summary["woke"] is False, summary
    assert "whim_wake" not in summary, "非 whim 路徑**不得**出現 whim_wake 鍵"
    assert "bootstrap" not in summary
    assert orch._WHIM_DAILY == {}, f"未觸發 ⇒ 不佔用額度：{orch._WHIM_DAILY}"


def test_whim_flag_on_uses_whim_origin(iso_env, monkeypatch):
    """W2：旗標 ON ＋ M3 判安靜 ＋ `soul_context` 非空 ⇒ 實際傳入 `"whim_driven"`。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch)
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)
    summary = out[AGENT]

    assert len(_whim_calls(calls)) == 1, f"恰 1 輪 whim：{calls}"
    assert calls[0]["args"][0] == AGENT
    assert summary["origin_type"] == "whim_driven", summary
    assert summary["woke"] is True, summary
    assert summary["whim_wake"] is True, summary
    # INV-7：不以產出量當驗收 —— 這裡只斷言**呼叫過**，不斷言線頭數。
    assert len(llm.calls) == 1, f"whim 路徑恰 1 次 LLM：{llm.calls}"
    assert summary["origin_round"]["llm_calls"] == 1, summary["origin_round"]
    # 計數器確實被佔用（跨 slot 累計用同一格）
    assert orch._WHIM_DAILY[AGENT] == {
        "date": MORNING.date().isoformat(),
        "count": 1,
    }, orch._WHIM_DAILY


def test_whim_not_called_when_m3_wakes(iso_env, monkeypatch):
    """W3：M3 `should_wake is True` ⇒ **不得**走 whim（沿用 `decision.origin_type`）。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch)
    _seed_due_thread(AGENT, origin_type="necessity_driven", now=MORNING)
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)
    summary = out[AGENT]

    assert len(calls) == 1, calls
    assert calls[0]["args"][1] == "necessity_driven", (
        f"M3 放行時必須沿用 decision.origin_type，不得被 whim 覆寫：{calls[0]['args']}"
    )
    assert summary["woke"] is True, summary
    assert "whim_wake" not in summary, summary
    assert "world_records" in calls[0]["kwargs"]["build_kwargs"], (
        "非 whim 路徑的 build_kwargs 維持既有形狀"
    )
    assert orch._WHIM_DAILY == {}, "M3 放行時不佔用 whim 額度"


def test_whim_respects_daily_cap(iso_env, monkeypatch):
    """W4：同一 agent 同一日兩個 slot ⇒ whim 生效**恰 1 次**；跨日自動歸零。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch)
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    first = _run([AGENT], MORNING, "morning", llm_caller=llm)[AGENT]
    second = _run([AGENT], NIGHT, "night", llm_caller=llm)[AGENT]
    third = _run([AGENT], MORNING + timedelta(days=1), "morning", llm_caller=llm)[AGENT]

    assert first["whim_wake"] is True, first
    assert second["woke"] is False, f"當日第二次 slot 必須被日上限擋下：{second}"
    assert "whim_wake" not in second, second
    # 跨日 ⇒ 計數歸零 ⇒ 當日額度恢復（契約 §4.5「跨日自動歸零」）
    assert third["whim_wake"] is True, f"跨日應重新可用：{third}"
    assert len(_whim_calls(calls)) == 2, f"morning(day1) ＋ morning(day2) 恰 2 輪：{calls}"
    assert len(llm.calls) == 2, len(llm.calls)
    assert orch.LIFE_THREAD_WHIM_MAX_PER_DAY == 1, "上限常數必須硬編碼為 1"
    assert orch._WHIM_DAILY[AGENT]["date"] == (MORNING + timedelta(days=1)).date().isoformat()


def test_whim_empty_soul_context_blocked(iso_env, monkeypatch):
    """W5：`soul_context` 為空 ⇒ 不呼叫且 **0 LLM 花費**（且不佔用額度）。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch, value="")
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)
    summary = out[AGENT]

    assert calls == [], f"空 soul_context ⇒ 0 輪 origins：{calls}"
    assert llm.calls == [], "空 soul_context ⇒ 0 LLM 花費"
    assert summary["woke"] is False, summary
    assert "whim_wake" not in summary, summary
    # 契約 §4.5 已知限制②：空 soul_context 不佔用額度
    assert orch._WHIM_DAILY == {}, orch._WHIM_DAILY


def test_whim_does_not_pass_world_records(iso_env, monkeypatch):
    """W6：whim 路徑的 `build_kwargs` **不含** `world_records`（必須純內生）。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch)
    calls = _spy_origin_round(monkeypatch)

    _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())

    whim = _whim_calls(calls)
    assert len(whim) == 1, calls
    build_kwargs = whim[0]["kwargs"]["build_kwargs"]
    assert "world_records" not in build_kwargs, (
        f"whim_driven 必須純內生（不得觸碰 Lived Context）：{sorted(build_kwargs)}"
    )
    assert build_kwargs["soul_context"] == SOUL, build_kwargs
    assert whim[0]["kwargs"]["due_threads"] is None, "due 過濾交還 M4 自己"


# ══════════════════════════════════════════════════════════════
# 凍結面守門
# ══════════════════════════════════════════════════════════════


def _public_surface(tree: ast.Module) -> Dict[str, str]:
    """模組**頂層公開**名稱 → 簽章字串（私有 `_` 開頭者不列入）。"""
    surface: Dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            parts: List[str] = []
            positional = [*node.args.posonlyargs, *node.args.args]
            for i, arg in enumerate(positional):
                text = arg.arg
                if arg.annotation is not None:
                    text = f"{text}: {ast.unparse(arg.annotation)}"
                if i >= len(positional) - len(node.args.defaults):
                    text = f"{text} = {ast.unparse(node.args.defaults[i - (len(positional) - len(node.args.defaults))])}"
                parts.append(text)
            surface[node.name] = f"({', '.join(parts)})"
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            fields = [
                f"{n.target.id}: {ast.unparse(n.annotation)}"
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            ]
            surface[node.name] = f"class {node.name}({', '.join(fields)})"
    return surface


#: M3 凍結面基線（本票**之前**的現況，逐位釘死）。任何一格不符 ⇒ 紅燈。
M3_BASELINE_SURFACE = {
    "WakeDecision": (
        "class WakeDecision(should_wake: bool, origin_type: Optional[str], "
        "reason: str, seed_hint: Optional[dict])"
    ),
    "evaluate_wake_gate": (
        "(agent_id: str, current_time: float, timeslot: str, active_threads: list, "
        "capacity_limit: int, recent_perceptions: list = None, "
        "enforce_strict_capacity: bool = True)"
    ),
}


def test_m3_gate_untouched():
    """W7：`life_thread_wake_gate.py` 的公開介面與本票前基線**逐位一致**（0 改動）。

    §4.5 明文聲明「M3 閘門 0 改動」：公開函式簽章、公開 dataclass 欄位、
    凍結常數 `WORLD_COLLISION_WINDOW_HOURS` 皆不得被本票動到。
    """
    source = WAKE_GATE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert _public_surface(tree) == M3_BASELINE_SURFACE, (
        "M3 公開介面偏離基線："
        f"{_public_surface(tree)} != {M3_BASELINE_SURFACE}"
    )

    # 凍結常數（契約 §12／§4.5 不得觸碰清單）：只看字面值，忽略非字面量賦值。
    consts = {
        n.targets[0].id: ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    assert consts.get("WORLD_COLLISION_WINDOW_HOURS") == 4, consts.get(
        "WORLD_COLLISION_WINDOW_HOURS"
    )

    # 沒有新增任何喚醒訊號參數（§4.2 二元、無第三態）：M3 檔**不得**出現本票的
    # 接線痕跡（旗標／每日計數器／可觀測鍵）。注意 M3 檔**本來就**合法含
    # `"whim_driven"` 字面值（§2.3 值域白名單的一員），那不是接線痕跡。
    for wiring_trace in (
        "LIFE_THREAD_WHIM", "whim_enabled", "_WHIM_DAILY",
        "WHIM_MAX_PER_DAY", "whim_wake",
    ):
        assert wiring_trace not in source, f"M3 檔出現 §4.5 接線痕跡：{wiring_trace}"


# ══════════════════════════════════════════════════════════════
# fail-closed
# ══════════════════════════════════════════════════════════════


def test_fail_closed_on_exception(iso_env, monkeypatch, caplog):
    """W8：`run_origin_round` 拋例外 ⇒ 記 warning、正常返回、**不 raise**。"""
    monkeypatch.setenv(orch.WHIM_ENABLED_ENV, "1")
    _patch_soul(monkeypatch)

    async def boom(*args, **kwargs):
        raise RuntimeError("boom (simulated origin round failure)")

    monkeypatch.setattr(lt_origins, "run_origin_round", boom)

    with caplog.at_level("WARNING"):
        out = _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())

    summary = out[AGENT]
    assert summary["woke"] is True, summary
    assert summary["origin_type"] == "whim_driven", summary
    assert summary["whim_wake"] is True, summary
    # fail-closed 回填：0 LLM、0 落盤，且 key 集合與 M4 失敗回傳相同
    assert summary["origin_round"]["llm_calls"] == 0, summary["origin_round"]
    assert summary["origin_round"]["created"] == []
    assert summary["origin_round"]["prompt_available"] is False
    assert any("whim" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


def test_fail_closed_on_flag_read_exception(iso_env, monkeypatch, caplog):
    """旗標讀取／解析例外 ⇒ warning ＋ 視為 OFF（0 輪 origins、0 LLM）。"""

    class _BrokenEnviron(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("boom (simulated environ failure)")

    class _BrokenOs:
        environ = _BrokenEnviron()

    class _ShimWiring:
        """代理真接線模組，只把 `os` 換成會拋例外的版本（**不動全域 `os.environ`**）。"""

        real = orch.lt_wiring
        TRUTHY_VALUES = real.TRUTHY_VALUES
        os = _BrokenOs()

        def __getattr__(self, name):  # 其餘屬性（build_dissolve_hook 等）照常代理
            return getattr(_ShimWiring.real, name)

    monkeypatch.setattr(orch, "lt_wiring", _ShimWiring())
    _patch_soul(monkeypatch)
    calls = _spy_origin_round(monkeypatch)
    llm = _quiet_llm()

    with caplog.at_level("WARNING"):
        out = _run([AGENT], MORNING, "morning", llm_caller=llm)

    assert calls == [], calls
    assert llm.calls == [], "旗標讀取失敗 ⇒ 0 LLM"
    assert out[AGENT]["woke"] is False, out[AGENT]
    assert any(orch.WHIM_ENABLED_ENV in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


# ══════════════════════════════════════════════════════════════
# 靜態鐵律（INV-2／INV-5／§2.3 值域）
# ══════════════════════════════════════════════════════════════


def test_w1_flag_truthiness_is_fail_closed():
    """旗標解析：**白名單** `{"1","true","yes","on"}` 才 ON；其餘一律 OFF。

    🔴 修訂（2026-10-03 11:50，主大腦覆核 W1 執行者回報）：初版規格為
    「非空且非 `0`／`false` 即 ON」，實測是 **fail-open** —— `off`／`no`／
    `disabled`／`maybe`／`0.0` 皆被判為 `True`。**企圖關閉此旗標會意外開啟它**。
    已改為與 `catchup_enabled()`／`bootstrap_enabled()` 完全一致的真值白名單。
    本測試的 OFF 清單刻意納入 `off`／`no`／`disabled`，釘死這個回歸。
    """
    assert orch.WHIM_ENABLED_ENV == "LIFE_THREAD_WHIM_ENABLED"

    import os

    saved = os.environ.pop(orch.WHIM_ENABLED_ENV, None)
    try:
        assert orch.whim_enabled() is False, "缺席 ⇒ OFF"
        for off in ("", "0", "false", "FALSE", " False ", "0 ", "  ",
                    "off", "OFF", "no", "NO", "disabled", "maybe", "0.0", "2"):
            os.environ[orch.WHIM_ENABLED_ENV] = off
            assert orch.whim_enabled() is False, f"{off!r} ⇒ 必須 OFF"
        for on in ("1", "true", "TRUE", "on", "ON", "yes", "YES",
                   " 1 ", " true "):
            os.environ[orch.WHIM_ENABLED_ENV] = on
            assert orch.whim_enabled() is True, f"{on!r} ⇒ ON"
    finally:
        os.environ.pop(orch.WHIM_ENABLED_ENV, None)
        if saved is not None:
            os.environ[orch.WHIM_ENABLED_ENV] = saved


def test_w1_flag_uses_repo_truthy_whitelist_not_invented_list():
    """本旗標的真值集合**必須就是** `lt_wiring.TRUTHY_VALUES`（不得另立一套）。"""
    assert orch.lt_wiring.TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})
    assert not hasattr(orch, "_WHIM_OFF_TOKENS"), "死碼應已移除"


def test_w2_origin_value_is_existing_four_value_domain():
    """`whim_driven` 是 §2.3 **既有**四值之一（不新增第五值；不 import 不存在的常數）。"""
    assert orch.WHIM_ORIGIN_TYPE == "whim_driven"
    assert set(lt.ORIGIN_TYPES) == set(ORIGIN_TYPE_VALUES), lt.ORIGIN_TYPES
    assert len(lt.ORIGIN_TYPES) == 4, f"§2.3 值域恰 4 值：{lt.ORIGIN_TYPES}"
    assert "whim_driven" in lt.ORIGIN_TYPES
    # M4 現況：沒有 WHIM_DRIVEN 常數可 import（故 orchestrator 以字面量登錄）
    for missing in ("WHIM_DRIVEN", "GOAL_DRIVEN", "NECESSITY_DRIVEN"):
        assert not hasattr(lt_origins, missing), f"M4 竟有 {missing}？需回報"


def test_w3_no_new_timer_and_no_numeric_score():
    """INV-2（0 新定時器）／INV-5（0 數值打分）在本檔改動面成立。

    以 **AST 識別子**判定（不是文字比對）：模組 docstring 會**逐字引用**
    `asyncio.sleep`／`call_later` 作為「不得新增」的說明，文字掃描會誤判。
    """
    tree = ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    idents = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "Timer", "APScheduler", "apscheduler", "BackgroundScheduler",
        "call_later", "sleep", "Thread", "threading",
    ):
        assert forbidden not in idents, f"INV-2 紅線（AST 識別子）：{forbidden}"
    for forbidden in ("urgency", "priority", "score", "weight"):
        assert forbidden not in idents, f"INV-5 紅線（AST 識別子）：{forbidden}"


def test_w4_orchestrator_src_import_count_unchanged():
    """0 新增 import：本檔改動面**未**增加任何 import（`src.*` 仍恰 6 個葉模組）。"""
    tree = ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    leaves = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src."):
            leaves.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            leaves.update(a.name for a in node.names if a.name.startswith("src."))
    assert sorted(leaves) == [
        "src.soul.life_thread_bootstrap",
        "src.soul.life_thread_consolidation_wiring",
        "src.soul.life_thread_dissolution",
        "src.soul.life_thread_origins",
        "src.soul.life_thread_wake_gate",
        "src.soul.life_threads",
    ], sorted(leaves)
