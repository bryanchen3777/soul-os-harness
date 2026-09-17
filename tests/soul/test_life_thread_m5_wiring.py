# tests/soul/test_life_thread_m5_wiring.py
# LIFE-THREAD-M5 — 生活線頭引擎排程器介接（orchestrator）接線矩陣。
#
# 受測模組：`src/soul/life_thread_orchestrator.py`（新增介接層）
#           ＋ `src/soul/scheduler.py` 的最小化掛載
#           ＋ `scripts/run_server.py` 的 LLM 接縫注入
#
# 範式沿用 `tests/harness/test_c21_periodic_narrative.py`：
#   資料根隔離 fixture / 時鐘建構 / `monkeypatch.setattr(scheduler_mod, "now_local", ...)`
#   / 原始碼護欄（AST／原始碼掃描）。
#
# 🔴 LLM **一律用 stub**（`llm_caller=` 或 `set_llm_proxy(stub)`）—— 本檔 0 真實 LLM、0 網路。
# 🔴 本檔**不讀寫生產 `data/**`**：所有落地檔都在 `SOUL_OS_DATA_DIR` 隔離的 tmp 內。
from __future__ import annotations

import ast
import asyncio
import inspect
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
from src.soul import life_thread_consolidation_wiring as lt_wiring  # noqa: E402
from src.soul import life_thread_dissolution as lt_diss  # noqa: E402
from src.soul import life_thread_orchestrator as m5  # noqa: E402
from src.soul import life_thread_origins as lt_origins  # noqa: E402
from src.soul import life_thread_wake_gate as lt_gate  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

AGENT = "agent_m5"
OTHER = "agent_m5b"
_SOUL = "我是ルカ。我喜歡在夜裡散步，討厭吵鬧的地方。"
_NARRATIVE = "她在清晨想起昨夜的雨。"
_TITLE = "晨間的線頭"

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
SCHEDULER_PATH = _REPO_ROOT / "src" / "soul" / "scheduler.py"
RUN_SERVER_PATH = _REPO_ROOT / "scripts" / "run_server.py"

#: orchestrator 允許的 `src.*` import 白名單（§D 靜態鐵律）。
#: LIFE-THREAD-M2-WIRING-1 新增第 5 個：沉澱接線模組（**預設關**旗標）。
#: LIFE-THREAD-BOOTSTRAP-1 新增第 6 個：冷啟動引導模組（**預設關**旗標，契約 §4.4）。
ALLOWED_SRC_IMPORTS = frozenset({
    "src.soul.life_threads",
    "src.soul.life_thread_wake_gate",
    "src.soul.life_thread_dissolution",
    "src.soul.life_thread_origins",
    "src.soul.life_thread_consolidation_wiring",
    "src.soul.life_thread_bootstrap",
})


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


def _spy_gate(monkeypatch) -> List[Dict[str, Any]]:
    """攔 M3 `evaluate_wake_gate`：計數 + 記錄入參（仍呼叫真函式）。"""
    calls: List[Dict[str, Any]] = []
    real = lt_gate.evaluate_wake_gate

    def spy(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(lt_gate, "evaluate_wake_gate", spy)
    return calls


def _patch_soul(monkeypatch, value: str = _SOUL) -> None:
    monkeypatch.setattr(lt_origins, "load_soul_context", lambda agent_id, **kw: value)


def _seed_due_thread(agent_id: str = AGENT, *, origin_type: str = "whim_driven",
                     now: datetime = MORNING) -> str:
    """建一條**檢驗點已到期**的 active 線頭（M3 判定 1 的唯一觸發條件）。"""
    due = (now - timedelta(hours=2)).astimezone(timezone.utc).isoformat()
    tid = lt.create_thread(
        agent_id, _TITLE, _NARRATIVE, origin_type, check_after_ts=due
    )
    assert tid, "fixture 建線頭失敗"
    return tid


def _lt_bytes(agent_id: str = AGENT) -> bytes:
    p = lt.life_threads_path(agent_id)
    return p.read_bytes() if p.exists() else b""


def _seed_thread(agent_id: str = AGENT, *, title: str, check_after: datetime) -> str:
    """建一條 active 線頭，`check_after_ts` 由呼叫端指定（測 due 邊界／未到期用）。"""
    tid = lt.create_thread(
        agent_id, title, _NARRATIVE, "whim_driven",
        check_after_ts=check_after.astimezone(timezone.utc).isoformat(),
    )
    assert tid, "fixture 建線頭失敗"
    return tid


def _widen_capacity(monkeypatch, value: int = 5) -> None:
    """放寬活躍池上限。

    ⚠️ `life_threads.capacity()` 缺鍵 fail-closed 回 **2** ⇒ 兩條 active 就飽和，
    M3（`enforce_strict_capacity=True`）會直接 `SLEEP/ACTIVE_POOL_SATURATED`，
    管線根本走不到 M4 prompt —— 凡「需要 ≥2 條 active 又必須 WAKE」的測試都要先放寬。
    """
    monkeypatch.setattr(lt, "capacity", lambda aid, config_path=None: value)


def _due_block(user_text: str) -> str:
    """取 prompt 的 `[到期線頭]` 區塊（M4 `build_origin_prompt` 把它放在 user 第 1 段）。"""
    assert user_text.startswith("[到期線頭]"), user_text[:80]
    return user_text.split("\n\n[", 1)[0]


def _lt_lines(agent_id: str = AGENT) -> List[Dict[str, Any]]:
    raw = _lt_bytes(agent_id)
    if not raw:
        return []
    return [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]


def _write_perception(records: List[Any], *, raw_lines: List[str] | None = None) -> None:
    p = lt_origins._perception_trace_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if raw_lines is None:
        raw_lines = [json.dumps(r, ensure_ascii=False) for r in records]
    p.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# 1. 介面釘死
# ══════════════════════════════════════════════════════════════


def test_01_module_slot_values_exact():
    """契約 §4.1：只有兩個評估點，`daytime`／`evening` **絕不接受**。"""
    assert m5.MODULE_SLOT_VALUES == ("morning", "night")
    assert isinstance(m5.MODULE_SLOT_VALUES, tuple)


def test_02_policy_constants_pinned():
    """Owner 裁定：嚴格容量 True、軟性歸檔 False、天數門檻 14／7。"""
    assert m5.POLICY_ENFORCE_STRICT_CAPACITY is True
    assert m5.POLICY_ALLOW_SOFT_ARCHIVE is False
    assert m5.POLICY_MAX_ACTIVE_DURATION_DAYS == 14
    assert m5.POLICY_STALE_CHECK_THRESHOLD_DAYS == 7
    assert m5._LOG_MAX_CHARS == 200


def test_03_run_slot_pipeline_signature_pinned():
    """介面逐字釘死（含 keyword-only 的 `llm_caller`）。"""
    sig = inspect.signature(m5.run_slot_pipeline)
    assert list(sig.parameters) == ["agent_ids", "now", "slot", "llm_caller"]
    assert sig.parameters["llm_caller"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["llm_caller"].default is None
    assert inspect.iscoroutinefunction(m5.run_slot_pipeline)


def test_04_reset_state_is_callable_and_clears_keys():
    """`reset_state()` 清空冪等鍵（docstring 明示僅測試呼叫）。"""
    m5._LAST_PROCESSED["x:morning:2026-09-06"] = 1
    m5.reset_state()
    assert m5._LAST_PROCESSED == {}
    assert "僅供測試呼叫" in m5.reset_state.__doc__ or "測試" in m5.reset_state.__doc__


def test_05_docstring_declares_boundaries():
    """模組 docstring 必寫三條邊界：0 新定時器／M2 僅評估／張力未供給。"""
    doc = m5.__doc__ or ""
    assert "0 新定時器" in doc
    assert "0 進入 Agency 觸發鏈" in doc
    assert "M2 僅評估不寫入" in doc
    assert "張力" in doc and "後續票" in doc


# ══════════════════════════════════════════════════════════════
# 2. slot 閘門（0 M3 呼叫、0 LLM）
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize("bad_slot", ["daytime", "evening", "MORNING", "Morning", ""])
def test_10_slot_gate_rejects_non_whitelisted(iso_env, monkeypatch, bad_slot):
    """非 `morning`/`night` ⇒ 回 `{}`，且 **0 次 M3 呼叫、0 次 LLM**。"""
    calls = _spy_gate(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT], MORNING, bad_slot, llm_caller=spy)
    assert out == {}
    assert calls == []
    assert spy.calls == []


def test_11_slot_gate_rejects_none(iso_env, monkeypatch):
    """`slot=None` ⇒ 回 `{}`，0 次 M3、0 次 LLM。"""
    calls = _spy_gate(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    assert _run([AGENT], MORNING, None, llm_caller=spy) == {}
    assert calls == []
    assert spy.calls == []


def test_12_slot_gate_does_not_read_perception(iso_env, monkeypatch):
    """slot 閘門在**讀感知檔之前**就短路（0 多餘 I/O）。"""
    reads: List[int] = []
    real = m5._perception_records_path
    monkeypatch.setattr(
        m5, "_perception_records_path", lambda: (reads.append(1), real())[1]
    )
    assert _run([AGENT], MORNING, "daytime") == {}
    assert reads == []


@pytest.mark.parametrize("bad_agents", [None, "agent_m5", b"agent_m5", 123, {}])
def test_13_agent_ids_invalid_returns_empty(iso_env, monkeypatch, bad_agents):
    """`agent_ids` 非序列／為 None ⇒ 回 `{}`，**永不 raise**。"""
    calls = _spy_gate(monkeypatch)
    assert _run(bad_agents, MORNING, "morning") == {}
    assert calls == []


def test_14_empty_agent_list_returns_empty(iso_env, monkeypatch):
    """空序列 ⇒ 回 `{}`（不是 exception）。"""
    calls = _spy_gate(monkeypatch)
    assert _run([], MORNING, "morning") == {}
    assert calls == []


# ══════════════════════════════════════════════════════════════
# 3. at-most-once 冪等
# ══════════════════════════════════════════════════════════════


def test_20_duplicate_slot_skipped_and_llm_not_called_again(iso_env, monkeypatch):
    """同 `(agent, slot, date)` 連呼兩次：第二次 `duplicate_slot`、**LLM 不再被呼叫**。"""
    _seed_due_thread()
    _patch_soul(monkeypatch)
    tid = lt.list_active(AGENT)[0]["thread_id"]
    spy = _SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "雨停了。",
         "next_check_hours": 8},
    ]))

    first = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert first[AGENT]["woke"] is True
    assert len(spy.calls) == 1

    second = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert second == {AGENT: {"skipped": "duplicate_slot"}}
    assert len(spy.calls) == 1, "第二次不得再花 LLM"


def test_21_slot_change_allows_rerun(iso_env, monkeypatch):
    """換 slot（morning→night）⇒ 可再跑（冪等鍵含 slot）。"""
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    tid = lt.list_active(AGENT)[0]["thread_id"]
    spy = _SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "夜深了。",
         "next_check_hours": 8},
    ]))
    a = _run([AGENT], MORNING, "morning", llm_caller=spy)
    b = _run([AGENT], NIGHT, "night", llm_caller=spy)
    assert a[AGENT]["woke"] is True
    assert b[AGENT]["woke"] is True
    assert len(spy.calls) == 2


def test_22_date_change_allows_rerun(iso_env, monkeypatch):
    """換日期（隔天同一 slot）⇒ 可再跑（冪等鍵含日期）。"""
    tomorrow = MORNING + timedelta(days=1)
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    tid = lt.list_active(AGENT)[0]["thread_id"]
    spy = _SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "又一天。",
         "next_check_hours": 8},
    ]))
    a = _run([AGENT], MORNING, "morning", llm_caller=spy)
    b = _run([AGENT], tomorrow, "morning", llm_caller=spy)
    assert a[AGENT]["woke"] is True
    assert b[AGENT]["woke"] is True
    assert len(spy.calls) == 2


def test_23_reset_state_clears_idempotency():
    """`reset_state()` 後，同鍵可再跑（冪等鍵真的被清空）。"""
    m5._LAST_PROCESSED[f"{AGENT}:morning:2026-09-06"] = 1
    assert f"{AGENT}:morning:2026-09-06" in m5._LAST_PROCESSED
    m5.reset_state()
    assert f"{AGENT}:morning:2026-09-06" not in m5._LAST_PROCESSED


def test_24_duplicate_is_per_agent(iso_env, monkeypatch):
    """冪等鍵是 **per-agent**：A 已跑不影響 B。"""
    _seed_due_thread(AGENT, now=MORNING)
    _seed_due_thread(OTHER, now=MORNING)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))  # 空 actions ⇒ 不落盤，但確實呼叫了 LLM
    first = _run([AGENT, OTHER], MORNING, "morning", llm_caller=spy)
    assert set(first) == {AGENT, OTHER}
    assert len(spy.calls) == 2
    second = _run([AGENT, OTHER], MORNING, "morning", llm_caller=spy)
    assert second == {AGENT: {"skipped": "duplicate_slot"},
                      OTHER: {"skipped": "duplicate_slot"}}
    assert len(spy.calls) == 2


def test_25_failure_still_stamps_slot_and_never_retries(iso_env, monkeypatch):
    """🔴 F1：at-most-once 的關鍵在**先蓋章再執行**（`_run_agent` 的「先蓋章」）。

    第 1 輪讓 agent 的 `fold` raise ⇒ 該 agent 記 `error`（但**章已蓋**）；
    第 2 輪**同一** `(agent, slot, date)` ⇒ 必須回 `{"skipped": "duplicate_slot"}`，
    且 `fold` 對該 agent 的呼叫次數**不得增加**（總計仍為 1）。

    這是**刻意的 fail-quiet、不是 bug**：`run_slot_pipeline` docstring 明文
    「該輪若失敗（LLM 失敗／例外）**不重試**（fail-quiet）—— 寧可漏一次，不可重複花費；
    下一輪（下一個 slot 或隔日）自然補上」。

    牙齒：把 `_LAST_PROCESSED[key] = 1` 移到 `fold` **之後** ⇒ 本測試紅
    （第 1 輪還沒蓋章就炸 ⇒ 第 2 輪會重跑 fold 並再次回 `error`，而不是 `duplicate_slot`）。
    """
    folds: List[str] = []
    real_fold = lt.fold

    def flaky(agent_id):
        folds.append(agent_id)
        raise RuntimeError("boom (simulated fold failure)")

    monkeypatch.setattr(lt, "fold", flaky)
    first = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert "error" in first[AGENT] and "boom" in first[AGENT]["error"], first
    assert folds == [AGENT], "第 1 輪 fold 恰 1 次"

    second = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert second == {AGENT: {"skipped": "duplicate_slot"}}, (
        "🔴 先蓋章 ⇒ 失敗那一輪**不得重試**（刻意的 fail-quiet）"
    )
    assert folds == [AGENT], f"🔴 fold 呼叫次數不得增加（總計仍為 1）：{folds}"


def test_26_stamp_statement_precedes_fold_call_in_source_order():
    """🔴 F1（結構面）：`_LAST_PROCESSED[key] = 1` 必須**早於** `lt.fold(...)`。

    以 **AST 行號**判定（不用文字包含比對）：這是「先蓋章再執行」唯一可靜態釘死的形態，
    補上 `test_25` 的行為面牙齒。並斷言「不重試」是**已宣告**的刻意設計（docstring 為證）。
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agent"
    )
    stamp_lines: List[int] = []
    fold_lines: List[int] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "_LAST_PROCESSED"
                ):
                    stamp_lines.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "fold"
        ):
            fold_lines.append(node.lineno)
    assert stamp_lines, "找不到 `_LAST_PROCESSED[key] = 1` 蓋章語句"
    assert fold_lines, "找不到 `lt.fold(...)` 呼叫"
    assert min(stamp_lines) < min(fold_lines), (
        f"🔴 蓋章（line {stamp_lines}）必須早於 fold（line {fold_lines}）："
        "否則失敗輪不會被蓋章 ⇒ 同一 slot 窗會重試並重複花費"
    )
    doc = m5.run_slot_pipeline.__doc__ or ""
    assert "先蓋章" in doc, "docstring 必須宣告 at-most-once 的「先蓋章」語意"
    assert "不重試" in doc, "docstring 必須宣告失敗不重試是刻意的（fail-quiet）"


# ══════════════════════════════════════════════════════════════
# 4. 完整走通（morning / night 各一）
# ══════════════════════════════════════════════════════════════


def test_30_full_round_morning_writes_life_thread_event(iso_env, monkeypatch):
    """M1 真檔 → M3 判 WAKE → stub LLM 回合法 JSON → `apply_actions` 落盤。"""
    tid = _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    before = _lt_lines(AGENT)
    assert len(before) == 1 and before[0]["event_type"] == "created"

    spy = _SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "雨停了，她把窗推開。",
         "next_check_hours": 8},
    ]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)

    summary = out[AGENT]
    assert summary["woke"] is True
    assert summary["origin_type"] == "whim_driven"
    assert summary["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE

    round_result = summary["origin_round"]
    assert round_result["called"] is True
    assert round_result["prompt_available"] is True
    assert round_result["llm_calls"] == 1
    assert round_result["advanced"] == [tid]
    assert round_result["created"] == []
    assert round_result["dissolved"] == []  # 旗標 OFF（預設）⇒ hook 立刻 return ⇒ 0 SAGE

    after = _lt_lines(AGENT)
    assert len(after) == 2
    assert after[1]["event_type"] == "updated"
    assert after[1]["thread_id"] == tid
    assert after[1]["narrative_content"] == "雨停了，她把窗推開。"
    assert after[1]["event_seq"] == 2
    assert len(spy.calls) == 1


def test_31_full_round_night_writes_life_thread_event(iso_env, monkeypatch):
    """night 評估點同樣能走完整條管線（契約 §4.1 兩個評估點對稱）。"""
    tid = _seed_due_thread(now=NIGHT)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "夜裡的雨聲很輕。",
         "next_check_hours": 6},
    ]))
    out = _run([AGENT], NIGHT, "night", llm_caller=spy)
    assert out[AGENT]["woke"] is True
    assert out[AGENT]["origin_round"]["advanced"] == [tid]
    after = _lt_lines(AGENT)
    assert [e["event_type"] for e in after] == ["created", "updated"]
    assert after[1]["narrative_content"] == "夜裡的雨聲很輕。"
    assert len(spy.calls) == 1


def test_32_full_round_prompt_carries_soul_context_and_due_thread(iso_env, monkeypatch):
    """顯式傳入的 `soul_context` 真的進到 prompt；due 線頭由 M4 自己的 predicate 撈出。"""
    tid = _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert len(spy.calls) == 1
    user_msg = spy.calls[0]["messages"][1]["content"]
    assert tid in user_msg
    assert _TITLE in user_msg


# ══════════════════════════════════════════════════════════════
# 4b. F2：due 集合交還 M4 自己的 predicate（不得 over-include）
# ══════════════════════════════════════════════════════════════


def test_33_prompt_excludes_not_yet_due_threads(iso_env, monkeypatch):
    """🔴 F2：未到期（`check_after_ts` 在 30 天後）的 active 線頭**不得**出現在 `[到期線頭]`。

    前身寫法（`due_threads=active_threads`）走的是 `_render_due_threads` 的
    `due_threads is not None` 分支，該分支**完全不做** due 過濾 ⇒ 未到期線頭被
    **over-include** 進 prompt（與 renderer docstring 及契約 §4.2「`status == active`
    且 `check_after_ts <= now`」互斥）。F2 裁定改傳 `due_threads=None`，
    交給 M4 自己那條已被既有測試覆蓋的過濾路徑。
    """
    due_tid = _seed_due_thread(now=MORNING)
    future_tid = _seed_thread(
        title="三十天後才要檢視的線頭", check_after=MORNING + timedelta(days=30)
    )
    _widen_capacity(monkeypatch)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)

    assert out[AGENT]["woke"] is True, out[AGENT]
    assert len(spy.calls) == 1
    block = _due_block(spy.calls[0]["messages"][1]["content"])
    assert due_tid in block, "已到期線頭必須進 `[到期線頭]`"
    assert future_tid not in block, "🔴 未到期線頭不得被 over-include 進 `[到期線頭]`"


def test_34_wake_path_calls_list_active_exactly_once(iso_env, monkeypatch):
    """🔴 F2：`due_threads=None` ⇒ M4 fallback **恰**呼叫 1 次 `lt.list_active()`。

    代價已由協調者拍板（WAKE 時多 1 次整檔讀、≤2 次/日/agent）；本測試把它釘死，
    避免日後有人「順手」在 orchestrator 再撈一次。
    """
    _seed_due_thread(now=MORNING)
    _seed_thread(
        title="三十天後才要檢視的線頭", check_after=MORNING + timedelta(days=30)
    )
    _widen_capacity(monkeypatch)
    _patch_soul(monkeypatch)
    calls: List[str] = []
    real = lt.list_active

    def spy(agent_id):
        calls.append(agent_id)
        return real(agent_id)

    monkeypatch.setattr(lt, "list_active", spy)
    out = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert out[AGENT]["woke"] is True, out[AGENT]
    assert calls == [AGENT], f"WAKE 路徑下 list_active 恰 1 次：{calls}"


def test_35_due_boundary_is_inclusive_and_future_is_excluded(iso_env, monkeypatch):
    """🔴 F2 邊界：`check_after_ts == now`（`<=` 含等於）進 due；`now + 1h` 不進。

    交還 M4 predicate 後仍必須與 §4.2 判定 1 **同口徑**，本測試是該口徑的可測形態。
    """
    boundary_tid = _seed_thread(title="恰好在這一刻到期", check_after=MORNING)
    future_tid = _seed_thread(
        title="一小時後才到期", check_after=MORNING + timedelta(hours=1)
    )
    _widen_capacity(monkeypatch)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)

    assert out[AGENT]["woke"] is True, out[AGENT]
    assert len(spy.calls) == 1
    block = _due_block(spy.calls[0]["messages"][1]["content"])
    assert boundary_tid in block, "`check_after_ts <= now` 含等於 ⇒ 必須進 due"
    assert future_tid not in block, "未到期（`now + 1h`）不得進 due"


# ══════════════════════════════════════════════════════════════
# 5. 飽和封鎖
# ══════════════════════════════════════════════════════════════


def _saturate(monkeypatch, agent_id: str = AGENT) -> None:
    """建 1 條 active 線頭後把容量壓成 1 ⇒ M3 嚴格容量防線必然飽和。"""
    lt.create_thread(agent_id, _TITLE, _NARRATIVE, "whim_driven")
    monkeypatch.setattr(lt, "capacity", lambda aid, config_path=None: 1)


def test_40_saturated_pool_sleeps_with_active_pool_saturated(iso_env, monkeypatch):
    """容量飽和（`enforce_strict_capacity=True`）⇒ `SLEEP/ACTIVE_POOL_SATURATED`、0 LLM。"""
    _saturate(monkeypatch)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([
        {"op": "create", "title": "不該被寫", "narrative_content": "不該被寫。",
         "origin_type": "whim_driven", "next_check_hours": 8},
    ]))
    before = _lt_bytes(AGENT)
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)

    assert out[AGENT]["woke"] is False
    assert out[AGENT]["reason"] == lt_gate.REASON_ACTIVE_POOL_SATURATED
    assert spy.calls == [], "飽和時 0 LLM 花費"
    assert _lt_bytes(AGENT) == before, "飽和時不得寫入 M1"


def test_41_saturated_gate_receives_strict_policy_true(iso_env, monkeypatch):
    """容量政策旋鈕以 `POLICY_ENFORCE_STRICT_CAPACITY=True` 傳入 M3。"""
    _saturate(monkeypatch)
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert len(calls) == 1
    kw = calls[0]["kwargs"]
    assert kw["enforce_strict_capacity"] is True
    assert kw["recent_perceptions"] == [] or isinstance(kw["recent_perceptions"], list)


def test_42_saturation_not_triggered_below_capacity(iso_env, monkeypatch):
    """1 條 active、容量 2 ⇒ 不飽和（防線不得把正常情況判死）。"""
    lt.create_thread(AGENT, _TITLE, _NARRATIVE, "whim_driven")
    monkeypatch.setattr(lt, "capacity", lambda aid, config_path=None: 2)
    _patch_soul(monkeypatch)
    calls = _spy_gate(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert calls[0]["args"][4] == 2  # capacity_limit 位置參數
    # 無到期線頭、無碰撞 ⇒ 留白（morning 屬 REFLECTION_SLOTS）
    assert calls[0]["kwargs"]["unresolved_tensions"] is None


# ══════════════════════════════════════════════════════════════
# 6. M2 唯讀（只評估、0 M1 寫入）
# ══════════════════════════════════════════════════════════════


def _terminal_undissolved(agent_id: str = AGENT) -> str:
    """終態（`completed`）但 `dissolved_at is None` 的線頭 ⇒ M2 應判 `should_mutate=True`。"""
    tid = lt.create_thread(agent_id, _TITLE, _NARRATIVE, "whim_driven")
    assert lt.append_transition(agent_id, tid, "completed") is True
    state = lt.fold(agent_id)[tid]
    assert state["status"] == "completed"
    assert state["dissolved_at"] is None
    return tid


def test_50_m2_flags_terminal_thread_but_writes_nothing(iso_env, monkeypatch):
    """構造「終態但未沉澱」⇒ `dissolved_candidates >= 1`，但 jsonl **逐位元未變**。"""
    _terminal_undissolved()
    _patch_soul(monkeypatch)
    before = _lt_bytes(AGENT)
    out = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert out[AGENT]["dissolved_candidates"] >= 1
    assert _lt_bytes(AGENT) == before, "🔴 M5 不得寫入 dissolved_at（修正 2）"


def test_51_append_dissolved_and_transition_never_called(iso_env, monkeypatch):
    """M2 執行層 out of scope ⇒ `append_dissolved` / `append_transition` **0 呼叫**。"""
    _terminal_undissolved()
    _patch_soul(monkeypatch)
    calls: List[str] = []
    real_dis = lt.append_dissolved
    real_tr = lt.append_transition

    def spy_dis(*a, **k):
        calls.append("append_dissolved")
        return real_dis(*a, **k)

    def spy_tr(*a, **k):
        calls.append("append_transition")
        return real_tr(*a, **k)

    monkeypatch.setattr(lt, "append_dissolved", spy_dis)
    monkeypatch.setattr(lt, "append_transition", spy_tr)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert calls == [], f"M5 不得寫 M1：{calls}"


def test_52_m2_allow_soft_archive_false(iso_env, monkeypatch):
    """`allow_soft_archive=False`（Owner 裁定 B）以具名常數傳入 M2。"""
    _terminal_undissolved()
    captured: List[Dict[str, Any]] = []
    real = lt_diss.evaluate_batch_dissolution

    def spy(*a, **k):
        captured.append({"args": a, "kwargs": k})
        return real(*a, **k)

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert len(captured) == 1
    assert captured[0]["kwargs"]["allow_soft_archive"] is False
    assert captured[0]["kwargs"]["max_active_duration_days"] == 14
    assert captured[0]["kwargs"]["stale_check_threshold_days"] == 7


def test_53_m2_evaluation_does_not_mutate_thread_dicts(iso_env, monkeypatch):
    """M2 純決策 ⇒ 傳進去的線頭 dict 內容不得被改（無 `dissolved_at` 寫入）。"""
    tid = _terminal_undissolved()
    captured: List[Any] = []
    real = lt_diss.evaluate_batch_dissolution

    def spy(threads, *a, **k):
        snapshot = [dict(t) for t in threads]
        out = real(threads, *a, **k)
        captured.append((snapshot, [dict(t) for t in threads]))
        return out

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert len(captured) == 1
    snap_before, snap_after = captured[0]
    assert snap_before == snap_after
    assert all(t["dissolved_at"] is None for t in snap_after)
    assert any(t["thread_id"] == tid for t in snap_after)


# ══════════════════════════════════════════════════════════════
# 7. M2 的輸入是「全部線頭」而非 list_active()
# ══════════════════════════════════════════════════════════════


def test_60_m2_receives_all_threads_including_terminal(iso_env, monkeypatch):
    """🔴 直接擋住「修正 5」的回歸：M2 收到的清單必須**含終態線頭**。

    若只傳 `lt.list_active()`（僅 `status=="active"`），M2 在此管線中**永遠是 no-op**。
    """
    terminal = _terminal_undissolved()
    active = _seed_due_thread(now=MORNING)
    captured: List[List[Dict[str, Any]]] = []
    real = lt_diss.evaluate_batch_dissolution

    def spy(threads, *a, **k):
        captured.append([dict(t) for t in threads])
        return real(threads, *a, **k)

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([
        {"thread_id": active, "op": "advance", "narrative_content": "推進。",
         "next_check_hours": 8},
    ])))

    assert len(captured) == 1
    ids = {t["thread_id"] for t in captured[0]}
    assert ids == {terminal, active}, "M2 必須收到全部線頭（含終態）"
    statuses = {t["thread_id"]: t["status"] for t in captured[0]}
    assert statuses[terminal] == "completed"
    assert statuses[active] == "active"


def test_61_m3_still_receives_only_active_threads(iso_env, monkeypatch):
    """對照：M3 只吃 active（終態不得進 M3 的 active_threads）。"""
    terminal = _terminal_undissolved()
    active = _seed_due_thread(now=MORNING)
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([
        {"thread_id": active, "op": "advance", "narrative_content": "推進。",
         "next_check_hours": 8},
    ])))
    passed = calls[0]["args"][3]
    ids = {t["thread_id"] for t in passed}
    assert ids == {active}
    assert terminal not in ids


def test_62_fold_called_once_per_agent(iso_env, monkeypatch):
    """每 agent **只 fold 一次**（1 次整檔讀）派生兩份視圖。

    場景刻意選「兩隻 agent 都沒有線頭」（無 WAKE）⇒ 管線內唯一會碰 M1 讀取的就是
    orchestrator 自己，計數因此是決定性的。

    🔴 F6 拆分（b）：原版在同一個測試裡另寫 `assert list_active_calls == []`，但其註解
    理由是「**M2 的輸入**不得用 `list_active()`」—— 斷言範圍比理由寬（把 M4 的 due
    fallback 也一起封殺）。F2 已拍板 M4 改走 `due_threads=None` ⇒ WAKE 時 **必然**
    呼叫 1 次 `lt.list_active()`（見 `test_34`），故該封殺移除。
    「M2 的輸入不得用 list_active()」這條**真正的不變量**改由 `test_60`（全庫）
    與 `test_63`（本檔案、M2 專屬）承擔，強度未減。
    """
    folds: List[str] = []
    real_fold = lt.fold

    def spy_fold(agent_id):
        folds.append(agent_id)
        return real_fold(agent_id)

    monkeypatch.setattr(lt, "fold", spy_fold)
    _patch_soul(monkeypatch)
    _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    assert folds == [AGENT, OTHER], f"每 agent 恰 1 次 fold：{folds}"


def test_63_m2_input_carries_terminal_threads_not_list_active(iso_env, monkeypatch):
    """🔴 F6 拆分（a）：M2 專屬不變量 —— `evaluate_batch_dissolution` 的輸入**含終態線頭**。

    原 `test_62` 想表達的就是這件事，但用 `list_active_calls == []` 間接表達。
    這裡改成**直接**斷言 M2 收到的清單裡有 `status == "completed"` 的線頭：
    `lt.list_active()` **永不含終態** ⇒ M2 一旦改用 `list_active()` 當輸入，本斷言必紅
    （與 `test_60` 形成兩道獨立的閘）。
    """
    terminal = _terminal_undissolved()
    captured: List[List[Dict[str, Any]]] = []
    real = lt_diss.evaluate_batch_dissolution

    def spy(threads, *a, **k):
        captured.append([dict(t) for t in threads])
        return real(threads, *a, **k)

    monkeypatch.setattr(lt_diss, "evaluate_batch_dissolution", spy)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    assert len(captured) == 1
    statuses = {t["thread_id"]: t["status"] for t in captured[0]}
    assert terminal in statuses and statuses[terminal] == "completed", (
        "🔴 M2 的輸入必須是**全部**線頭（含終態）；只給 active 會讓 M2 永遠 no-op"
    )
    assert lt.list_active(AGENT) == [], (
        "對照：`list_active()` 永不含終態 ⇒ M2 的輸入不可能來自它"
    )


# ══════════════════════════════════════════════════════════════
# 8. 空 soul_context ⇒ 提前跳過（0 LLM）
# ══════════════════════════════════════════════════════════════


@pytest.mark.parametrize("empty", ["", None])
def test_70_empty_soul_context_blocks_wake(iso_env, monkeypatch, empty):
    """人格上下文為空／None ⇒ `wake_blocked=empty_soul_context`、**0 次 LLM**。"""
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch, empty)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is True
    assert out[AGENT]["wake_blocked"] == "empty_soul_context"
    assert out[AGENT]["reason"] == lt_gate.REASON_CHECKPOINT_DUE_WAKE
    assert spy.calls == [], "空 soul_context ⇒ 0 LLM 花費"


def test_71_empty_soul_context_leaves_m1_untouched(iso_env, monkeypatch):
    """空 soul_context 時不得落盤任何事件。"""
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch, "")
    before = _lt_bytes(AGENT)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert _lt_bytes(AGENT) == before


def test_72_empty_soul_context_does_not_consume_other_agents(iso_env, monkeypatch):
    """空 soul_context **只跳過該 agent**，其他 agent 照常走完。"""
    _seed_due_thread(AGENT, now=MORNING)
    _seed_due_thread(OTHER, now=MORNING)
    tid_other = [t["thread_id"] for t in lt.list_active(OTHER)][0]
    monkeypatch.setattr(
        lt_origins, "load_soul_context",
        lambda agent_id, **kw: "" if agent_id == AGENT else _SOUL,
    )
    spy = _SpyLLM(_json_response([
        {"thread_id": tid_other, "op": "advance", "narrative_content": "B 推進。",
         "next_check_hours": 8},
    ]))
    out = _run([AGENT, OTHER], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["wake_blocked"] == "empty_soul_context"
    assert out[OTHER]["woke"] is True
    assert out[OTHER]["origin_round"]["advanced"] == [tid_other]
    assert len(spy.calls) == 1


# ══════════════════════════════════════════════════════════════
# 9. dissolve_hook 已接線（**預設關** ⇒ 0 SAGE）
# ══════════════════════════════════════════════════════════════


def test_80_run_origin_round_receives_wired_default_off_dissolve_hook(iso_env, monkeypatch):
    """🔴 LIFE-THREAD-M2-WIRING-1 **取代**舊斷言「`dissolve_hook is None`」。

    舊斷言的理由（「M2 執行層 out of scope」）已失效：執行層已驗收並接線。新不變量
    **不弱化**、且更精確：

    1. `dissolve_hook` **不是 `None`**（＝已接線），且**必須**是接線模組的 hook
       （`hook.__module__` 逐字指名）⇒ 若把它改回 `None`、或注入別人的 hook，本測試紅。
    2. 旗標**預設關** ⇒ 呼叫它是**可證明的 0 成本**：0 背景任務、0 M1 寫入（0 SAGE）。
       ⇒ 若旗標預設被改成 ON，`pending_task_count()` 必為 1 ⇒ 本測試紅。
    """
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    captured: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        captured.append({"args": a, "kwargs": k})
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    assert len(captured) == 1
    hook = captured[0]["kwargs"]["dissolve_hook"]
    assert hook is not None, "M2 執行層已接線 ⇒ dissolve_hook 不得再是 None"
    assert hook.__module__ == "src.soul.life_thread_consolidation_wiring", hook.__module__

    # 旗標預設關（缺席）⇒ 呼叫 hook 是可證明的 0 成本
    assert lt_wiring.consolidation_enabled() is False
    before = _lt_lines(AGENT)
    hook(AGENT, "th-m5-wiring-default-off", "completed")
    assert lt_wiring.pending_task_count() == 0, "旗標 OFF ⇒ 不得建立背景任務"
    assert _lt_lines(AGENT) == before, "旗標 OFF ⇒ 0 M1 寫入（0 SAGE）"


def test_81_run_origin_round_receives_due_threads_none_and_build_kwargs(iso_env, monkeypatch):
    """🔴 F2：`due_threads` 必須**顯式為 `None`**（due 過濾交還 M4 自己的 predicate）；
    `build_kwargs` 仍顯式帶 `soul_context`／`world_records`（避免 M4 二次 I/O）。
    """
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    captured: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        captured.append({"args": a, "kwargs": k})
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    kw = captured[0]["kwargs"]
    assert "due_threads" in kw, "必須**顯式**傳 due_threads（不得靠預設值靜默漂移）"
    assert kw["due_threads"] is None, (
        "F2 裁定：傳 None ⇒ M4 `_render_due_threads` 走自己的 "
        "`lt.list_active()` ＋ `check_after_ts <= now`（與 §4.2 判定 1 同口徑）"
    )
    assert kw["build_kwargs"]["soul_context"] == _SOUL
    assert "world_records" in kw["build_kwargs"]


def test_82_origin_round_is_the_only_wake_action(iso_env, monkeypatch):
    """WAKE 的唯一動作 ＝ `run_origin_round` 恰 1 次（0 其他副作用通道）。"""
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    n = {"calls": 0}
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        n["calls"] += 1
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert n["calls"] == 1


# ══════════════════════════════════════════════════════════════
# 10. fail-closed 隔離（單一 agent 失敗不中斷整批）
# ══════════════════════════════════════════════════════════════


def test_90_first_agent_fold_error_isolated(iso_env, monkeypatch):
    """第 1 個 agent 的 `fold` raise ⇒ 只有它 `error`，第 2 個仍被執行。"""
    real_fold = lt.fold

    def flaky(agent_id):
        if agent_id == AGENT:
            raise RuntimeError("boom (simulated fold failure)")
        return real_fold(agent_id)

    monkeypatch.setattr(lt, "fold", flaky)
    _seed_due_thread(OTHER, now=MORNING)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT, OTHER], MORNING, "morning", llm_caller=spy)

    assert "error" in out[AGENT]
    assert "boom" in out[AGENT]["error"]
    assert "error" not in out[OTHER]
    assert out[OTHER]["woke"] is True, "第 2 個 agent 必須照常走完（有到期線頭 ⇒ WAKE）"
    assert len(spy.calls) == 1, "失敗的 agent 不得消耗 LLM；成功的 agent 恰 1 次"
    assert set(out) == {AGENT, OTHER}


def test_91_pipeline_never_raises_on_agent_error(iso_env, monkeypatch):
    """單一 agent 的任何例外都不得讓整批 raise。"""
    def boom(agent_id):
        raise ValueError("boom")

    monkeypatch.setattr(lt, "fold", boom)
    out = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert out[AGENT]["error"] == "boom"


def test_92_error_in_second_agent_keeps_first_result(iso_env, monkeypatch):
    """第 2 個 agent 失敗 ⇒ 第 1 個的結果仍完整保留。"""
    real_fold = lt.fold

    def flaky(agent_id):
        if agent_id == OTHER:
            raise RuntimeError("late boom")
        return real_fold(agent_id)

    monkeypatch.setattr(lt, "fold", flaky)
    tid = _seed_due_thread(AGENT, now=MORNING)
    _patch_soul(monkeypatch)
    out = _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([
        {"thread_id": tid, "op": "advance", "narrative_content": "推進。",
         "next_check_hours": 8},
    ])))
    assert out[AGENT]["origin_round"]["advanced"] == [tid]
    assert "error" in out[OTHER]


def test_93_run_origin_round_exception_is_isolated(iso_env, monkeypatch):
    """M4 入口 raise ⇒ 記在該 agent 的 `error`，不 raise、不影響他人。"""
    _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("m4 boom")

    monkeypatch.setattr(lt_origins, "run_origin_round", boom)
    out = _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert "error" in out[AGENT] and "m4 boom" in out[AGENT]["error"]


# ══════════════════════════════════════════════════════════════
# 11. 不得吞 CancelledError
# ══════════════════════════════════════════════════════════════


def test_95_cancelled_error_propagates(iso_env, monkeypatch):
    """`asyncio.CancelledError` 必須往外冒（不得被 `except Exception` 吃掉）。"""
    def cancel(agent_id):
        raise asyncio.CancelledError()

    monkeypatch.setattr(lt, "fold", cancel)
    with pytest.raises(asyncio.CancelledError):
        _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))


def test_96_cancelled_error_source_is_explicit_reraise():
    """原始碼層：orchestrator 必須顯式 `except asyncio.CancelledError: raise`。

    🔴 F-04：舊版用 `src[idx:idx + 120]` 的**固定字元窗**找 `raise` —— 窗內多幾行
    註解就會滑出窗外而**靜默空轉**。改以 AST 判定：存在一個 `except` 處理器，
    其型別為 `asyncio.CancelledError`，且**處理器體內有 `raise`**。
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    handlers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.type is not None
    ]
    cancelling = [
        h for h in handlers
        if _dotted_name(h.type) in ("asyncio.CancelledError", "CancelledError")
    ]
    assert cancelling, "必須顯式攔 `asyncio.CancelledError`（不得只靠 except Exception）"
    assert any(
        isinstance(stmt, ast.Raise) for h in cancelling for stmt in h.body
    ), "攔到 CancelledError 後必須顯式 re-raise"
    # `asyncio.CancelledError` 必須真的可解析（不得是字串／註解）
    assert "asyncio" in _leaf_imports(tree), _leaf_imports(tree)


def test_97_cancelled_error_not_swallowed_in_pipeline(iso_env, monkeypatch):
    """第 1 個 agent 取消 ⇒ 整輪中止（第 2 個不得被執行）。"""
    seen: List[str] = []
    real_fold = lt.fold

    def flaky(agent_id):
        seen.append(agent_id)
        if agent_id == AGENT:
            raise asyncio.CancelledError()
        return real_fold(agent_id)

    monkeypatch.setattr(lt, "fold", flaky)
    with pytest.raises(asyncio.CancelledError):
        _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert seen == [AGENT]


# ══════════════════════════════════════════════════════════════
# 12. 0 命中護欄（AST 掃描，本票新真相）
# ══════════════════════════════════════════════════════════════

#: 禁止的**呼叫**屬性名（AST 層；註解／文件字串不算）。
_FORBIDDEN_CALL_ATTRS = frozenset({
    "publish",              # 事件匯流排
    "append_dissolved",     # M1 沉澱寫入
    "append_transition",    # M1 狀態轉移寫入
    "generate_text",        # LLM 必須經 run_origin_round
    "load_persona",         # 人格一律經 load_soul_context
    "create_task", "call_later", "sleep",   # 0 新定時器
    "write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir", "rename",
})

#: 禁止的**內建呼叫**名。
_FORBIDDEN_CALL_NAMES = frozenset({"open"})

#: 禁止 import 的模組前綴。
_FORBIDDEN_MODULE_PREFIXES = (
    "src.agency",
    "src.memory.sage",
    "src.llm.proxy",
    "asyncio.tasks",
)

#: 禁止 import 的網路／LLM 套件根名。
_FORBIDDEN_NET_ROOTS = frozenset({
    "httpx", "requests", "aiohttp", "urllib", "socket", "openai", "anthropic",
})


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


def _audit_source(source: str) -> List[str]:
    """AST 稽核：回違規清單（空 ＝ 通過）。

    只認**真的** import／呼叫／關鍵字引數 —— 因此「文件字串裡聲明不新增
    `trigger_type`」不會偽紅，而真做了**一定**紅。
    """
    tree = ast.parse(source)
    issues: List[str] = []

    for leaf in _leaf_imports(tree):
        if leaf.startswith(_FORBIDDEN_MODULE_PREFIXES):
            issues.append(f"禁止 import：{leaf}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_CALL_ATTRS:
                issues.append(f"禁止呼叫：{func.attr}()")
            if isinstance(func, ast.Name) and (
                func.id in _FORBIDDEN_CALL_NAMES or func.id in _FORBIDDEN_CALL_ATTRS
            ):
                issues.append(f"禁止呼叫：{func.id}()")
        elif isinstance(node, ast.keyword) and node.arg == "trigger_type":
            issues.append("禁止傳遞 trigger_type 引數")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "trigger_type":
                    issues.append("禁止新增 trigger_type 變數")
    return sorted(set(issues))


def _non_docstring_strings(tree: ast.AST) -> List[str]:
    """收集**非 docstring** 的字串常數（docstring 是散文，不構成 I/O）。"""
    doc_positions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                doc_positions.add(id(body[0].value))
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in doc_positions:
                out.append(node.value)
    return out


def _code_only_idents(tree: ast.AST) -> List[str]:
    """收集**非 docstring**（＝真的 code）區域的識別字。

    F-03.1 的「code-only 命中為 0」判準：`_identifiers_and_dotted` 會把
    文件字串裡的散文字（`ast.Constant`）也當成 `Name`／`Attribute` 節點走訪到，
    故先標記 docstring 的位置，再只收非 docstring 子樹。
    """
    doc_positions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Constant
            ) and isinstance(body[0].value.value, str):
                doc_positions.add(id(body[0].value))
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) in doc_positions:
            continue
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.arg):
            out.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            out.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
    return out


def _calls_named(tree: ast.AST) -> List[str]:
    """所有被呼叫的函式／屬性名（含 `f()` 與 `x.f()` 兩種形態）。"""
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.append(func.attr)
            elif isinstance(func, ast.Name):
                names.append(func.id)
    return names


# ──────────────────────────────────────────────────────────────
# AST 定位輔助（LIFE-THREAD-M5-GUARD-FIX F-03／F-04）
# ──────────────────────────────────────────────────────────────


def _find_function(tree: ast.AST, name: str) -> ast.AST:
    """找 `name` 的函式節點（同步／非同步皆可，**含類別方法**）；找不到 ⇒ 直接紅。

    F-04：取代「`src.index(...)` ＋ 固定字元窗切片」—— 後者函式一長就滑出窗外，
    護欄變成**靜默空轉**（fail-open）。AST 節點自帶 `lineno`／`end_lineno`，
    範圍由 Python 剖析器決定，與函式長度無關。
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"找不到函式 {name}()（護欄的比對基準消失了）")


def _find_module_level_function(tree: ast.AST, name: str) -> ast.AST:
    """只找**模組層**（含類別內的直屬方法）的 `name`；重複 ⇒ 紅。"""
    hits = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            hits.append(node)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name == name:
                    hits.append(sub)
    assert len(hits) == 1, f"{name}() 必須恰 1 個定義：{len(hits)}"
    return hits[0]


def _function_source(src: str, tree: ast.AST, name: str) -> str:
    """以 AST 的 `lineno`／`end_lineno` 切出 `name()` 的**確切原始碼範圍**（F-04）。"""
    node = _find_function(tree, name)
    return "\n".join(src.splitlines()[node.lineno - 1:node.end_lineno])


def _dotted_name(node: ast.AST) -> str:
    """把 `Attribute`／`Name` 鏈攤平成點名（`bus.publish` ⇒ `"bus.publish"`）。"""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _identifiers_and_dotted(tree: ast.AST) -> List[str]:
    """收集樹內所有**識別字**（`Name` id、`Attribute` attr、`arg` arg、def/class 名，
    以及**關鍵字引數名** `ast.keyword.arg`）。

    ⚠️ 關鍵字引數名**必須**納入：`logger.debug(trigger_type='x')` 的
    `trigger_type` 只存在於 `ast.keyword.arg`，不算 `Name`／`Attribute`
    ⇒ 漏掉它就是一个真實的**假陰性**縫（本票實測抓到，已補並加牙齒測試）。
    """
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.arg):
            out.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            out.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(node.name)
    return out


def _module_aliases(tree: ast.AST) -> Dict[str, str]:
    """`import ... as` / `from ... import ... as` 的別名表（別名 ⇒ 原始模組路徑）。

    用來讓護欄**認人而非認字**：被注入的模組若改了別名，具名斷言不該假紅／假綠。
    """
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                aliases[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name
    return aliases


def _resolve_alias(aliases: Dict[str, str], dotted: str) -> str:
    """把點名的**根**換成原始模組路徑（`_lt_origins.set_llm_proxy` ⇒ 全名）。"""
    root, _, rest = dotted.partition(".")
    full = aliases.get(root)
    if full is None:
        return dotted
    return f"{full}.{rest}" if rest else full


def _walk_calls(tree: ast.AST):
    """產生 `(被呼叫者的點名, ast.Call)` —— `f()`／`x.f()`／`a.b.c()` 都攤平。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield _dotted_name(node.func), node


def test_a0_orchestrator_has_zero_forbidden_constructs():
    """orchestrator：0 `bus.publish`／`trigger_type`／`src.agency`／`src.memory.sage`
    ／`append_dissolved`／`append_transition`／`open(` 寫入。"""
    assert _audit_source(MODULE_PATH.read_text(encoding="utf-8")) == []


def test_a1_orchestrator_import_whitelist_only():
    """orchestrator 的 `src.*` import 白名單**恰為** 6 個模組。

    （LIFE-THREAD-M2-WIRING-1：4 → 5，新增沉澱接線模組；
      LIFE-THREAD-BOOTSTRAP-1：5 → 6，新增冷啟動引導模組。多一個或少一個都紅。）
    """
    leaves = _leaf_imports(ast.parse(MODULE_PATH.read_text(encoding="utf-8")))
    src_leaves = [x for x in leaves if x.startswith("src.")]
    allowed_ok = all(
        x in ALLOWED_SRC_IMPORTS or any(x.startswith(a + ".") for a in ALLOWED_SRC_IMPORTS)
        for x in src_leaves
    )
    assert allowed_ok, f"白名單外 import：{src_leaves}"
    assert len(src_leaves) == 6, src_leaves


def test_a2_forbidden_scanner_has_teeth(tmp_path):
    """牙齒證明：真違規 ⇒ DETECT；文件字串提及 ⇒ MISS。"""
    real_bad = (
        "from src.agency import bus\n"
        "import src.memory.sage.writer\n"
        "def f():\n"
        "    bus.publish(trigger_type='x')\n"
        "    open('/tmp/x', 'w')\n"
        "    lt.append_dissolved('a', 'b')\n"
        "    lt.append_transition('a', 'b', 'completed')\n"
        "    trigger_type = 'y'\n"
    )
    issues = _audit_source(real_bad)
    assert any("src.agency" in i for i in issues)
    assert any("src.memory.sage" in i for i in issues)
    assert any("publish" in i for i in issues)
    assert any("open" in i for i in issues)
    assert any("append_dissolved" in i for i in issues)
    assert any("append_transition" in i for i in issues)
    assert any("trigger_type" in i for i in issues)

    prose_only = (
        '"""本引擎不進入 Agency 觸發鏈：0 bus.publish、不新增 trigger_type、\n'
        "不呼叫 append_dissolved / append_transition、0 src.agency import。\"\"\"\n"
        "x = 1\n"
    )
    assert _audit_source(prose_only) == [], "文件字串不得偽紅"


def test_a3_orchestrator_has_no_float_literals_or_scoring_idents():
    """orchestrator 自身 0 浮點字面量、0 個 `score|weight|confidence|probab` 識別字。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    floats = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == [], f"0 LLM 直接呼叫 ⇒ 不得有浮點字面量：{floats}"
    lowered = src.lower()
    for banned in ("score", "weight", "confidence", "probab"):
        assert banned not in lowered, f"不得出現識別字片段：{banned}"


def test_a4_orchestrator_has_no_direct_llm_or_network_channel():
    """0 LLM 直接呼叫／0 網路通道：AST 判定呼叫與 import（文件字串聲明不算違規）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert _audit_source(src) == []
    leaves = _leaf_imports(ast.parse(src))
    for lib in sorted(_FORBIDDEN_NET_ROOTS):
        assert not any(
            leaf == lib or leaf.startswith(lib + ".") for leaf in leaves
        ), f"不得新增網路通道：{lib}"


def test_a4b_direct_llm_call_is_detected(tmp_path):
    """牙齒證明：真 `proxy.generate_text(...)` ⇒ DETECT；文件字串提及 ⇒ MISS。"""
    assert _audit_source("async def f(p):\n    return await p.generate_text(x)\n") != []
    assert _audit_source("def f():\n    return load_persona('a')\n") != []
    assert _audit_source('"""一律經 run_origin_round，不直接 generate_text。"""\nx = 1\n') == []


def test_a5_orchestrator_does_not_touch_life_threads_file_path():
    """寫入一律經 M1 公開 API ⇒ 不得自行組 life_threads 檔路徑。

    🔴 **恰等值演化（LIFE-THREAD-BOOTSTRAP-1，2026-09-17）**：本測試原先斷言
    「`life_threads_path` 呼叫數 **＝ 0**」；契約 §4.4 的 bootstrap 標記落點
    （`data/soul/<agent_id>/life_thread_bootstrap.json`）必須經 `data_root()` 慣例組出
    （§2.1／§4.4），而**唯一同時**滿足「經 `data_root()`」與「`agent_id` 必須是
    **安全路徑段**」的來源就是 M1 的 `life_threads_path()`（其內部即
    `_validate_agent_id` ＋ `_SAFE_AGENT_SEGMENT` ＋ `data_root()/soul/<agent>/…`）。
    故本測試由 **0 ⇒ 恰 1 次 ＋ 位置唯一** 演化。

    **這不是放寬**（三條原斷言逐字保留）：
      1. 仍禁 `LIFE_THREADS_FILENAME` 被**呼叫**；
      2. 仍禁 `LIFE_THREADS_FILENAME` 作為**識別字**被引用；
      3. 仍禁**非 docstring** 字串出現 `life_threads.jsonl`／`life_threads_path`
         （後者亦封死 `getattr(lt, "life_threads_path")` 這類別名繞道）。
    新增的兩條**比「恰 1 次」更強**：**先數全檔呼叫總數須恰 1**（多插一次裸呼叫即紅），
    且**那唯一一次必須是 `bootstrap_marker_path(...)` 的 `node.args` 直接引數**
    （先存變數再傳、或移出引數位置 ⇒ 紅）。仍禁硬編碼 `data/`（§2.1；本檔
    `test_a6`／`test_d0` 另把守路徑來源），也仍禁對 threads 檔做任何 I/O。
    """
    def _life_threads_path_issues(source: str) -> List[str]:
        """回違規清單（空 ＝ 通過）。**純 AST 層** ⇒ 可餵假來源做牙齒自證。"""
        issues: List[str] = []
        source_tree = ast.parse(source)

        # ① **先數總數**：`life_threads_path` 的「呼叫」在全檔須恰 1。
        call_nodes = [
            n
            for n in ast.walk(source_tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "life_threads_path"
        ]
        if len(call_nodes) != 1:
            issues.append(
                f"`life_threads_path` 呼叫總數須恰 1（實得 {len(call_nodes)}）"
            )
            return issues

        # ② 那唯一一次必須是 `bootstrap_marker_path(...)` 的**直接引數**
        #    （不得先存變數再傳、不得經 getattr／別名）。
        node = call_nodes[0]
        is_direct_arg = False
        for outer in ast.walk(source_tree):
            if not isinstance(outer, ast.Call):
                continue
            outer_name = getattr(outer.func, "attr", getattr(outer.func, "id", ""))
            if outer_name != "bootstrap_marker_path":
                continue
            if any(arg is node for arg in outer.args):
                is_direct_arg = True
                break
        if not is_direct_arg:
            issues.append(
                "`life_threads_path` 的唯一呼叫必須是 `bootstrap_marker_path(...)` "
                "的直接引數（不得先存變數、不得移出引數位置、不得經 getattr／別名）"
            )
        return issues

    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "LIFE_THREADS_FILENAME" not in _calls_named(tree)
    # 🔴 F-03：「0 命中」改為 **AST 識別字**判定（舊版是 `not in src` 的全文文字比對）
    assert "LIFE_THREADS_FILENAME" not in _identifiers_and_dotted(tree), (
        "M1 的檔名常數不得被 orchestrator 引用（寫入一律經 M1 公開 API）"
    )
    for text in _non_docstring_strings(tree):
        assert "life_threads.jsonl" not in text, text
        assert "life_threads_path" not in text, text

    # ── 恰等值演化：恰 1 次，且只能是 bootstrap 標記路徑的直接引數 ──
    assert _life_threads_path_issues(src) == [], _life_threads_path_issues(src)

    # ── 牙齒自證（三種假來源**必須逐一變紅**；合格樣式 ⇒ 綠）────
    teeth = {
        "合格樣式（bootstrap_marker_path 的直接引數）": (
            "boot = lt_boot.bootstrap_marker_path(lt.life_threads_path(agent_id))\n"
        ),
        "假來源(i)：移出引數位置（先存變數再傳）": (
            "p = lt.life_threads_path(agent_id)\n"
            "boot = lt_boot.bootstrap_marker_path(p)\n"
        ),
        "假來源(ii)：出現第二次呼叫": (
            "boot = lt_boot.bootstrap_marker_path(lt.life_threads_path(agent_id))\n"
            "other = lt.life_threads_path(agent_id)\n"
        ),
        "假來源(iii)：裸呼叫出現在別處": (
            "result = await lt_origins.run_origin_round(agent_id, decision.origin_type)\n"
            "p = lt.life_threads_path(agent_id)\n"
        ),
    }
    ok_label = "合格樣式（bootstrap_marker_path 的直接引數）"
    assert _life_threads_path_issues(teeth[ok_label]) == [], (
        "合格樣式必須綠："
        + repr(_life_threads_path_issues(teeth[ok_label]))
    )
    for label, fake in teeth.items():
        if label == ok_label:
            continue
        red = _life_threads_path_issues(fake)
        print(f"[A5-TEETH-RED] {label} ⇒ {red}")
        assert red, f"牙齒失效：{label} 應變紅但未紅"


def test_a6_orchestrator_reads_only_perception_trace():
    """唯一 I/O ＝ 1 次感知檔讀取（路徑經 M4 的 `_perception_trace_path`）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "_perception_trace_path" in src
    assert "read_text" in src  # 唯讀
    assert "import asyncio" in src, "CancelledError 明示需要 asyncio"


# ══════════════════════════════════════════════════════════════
# 13. 排程器掛載護欄
# ══════════════════════════════════════════════════════════════


#: 🔴 F-03：`.py` 檔的護欄一律用 **AST 節點判定**；字元層的 `in text`／`str.count`
#: 只准用在**非 `.py` 檔**（見 §F-03.5 的說明）。
_SCHEDULER_SLOT_FN = "_fire_life_thread_slot"
_RUN_LOOP_FN = "_run_loop"

#: `run_server.py` 對 M4 唯一**允許**的呼叫；集合精確等值（多一個呼叫就紅）。
_ALLOWED_RUN_SERVER_ORIGINS_CALLS = ("set_llm_proxy",)

#: `run_server.py` 不得出現的 M4／M5 入口呼叫（0 個 `ast.Call` 節點）。
_FORBIDDEN_RUN_SERVER_CALLS = (
    "run_origin_round",
    "build_origin_prompt",
    "apply_actions",
    "run_slot_pipeline",
    "life_thread_orchestrator",
)

#: 🔴 F-03.1：`_fire_life_thread_slot` 不得洩漏的**觸發鏈識別字**
#: （AST 的 `Name`／`Attribute`／`arg` 判定；`bus.publish` ⇒ attr `publish`）。
_FORBIDDEN_TRIGGER_IDENTS = ("trigger_type", "publish")

#: 🔴 N-02（LIFE-THREAD-M5-GUARD-FIX-2）：`_fire_life_thread_slot` 內**允許**的
#: import 完整模組名 **精確集合**（`==` 比對，多一個、少一個都紅）。
#:
#: 這是**白名單**而非黑名單：舊版的 `_FORBIDDEN_MODULE_PREFIXES` 只套用在
#: orchestrator 的 `_audit_source`，scheduler 這個新方法**沒有任何護欄** ——
#: 實測在 `_fire_life_thread_slot` 內插入 `from src.agency import bus`，
#: 整個 `tests/soul` 859 筆**全綠**。白名單讓「該方法內任何新 import」一律紅。
_ALLOWED_SLOT_IMPORTS = ("src.soul.life_thread_orchestrator",)

#: 🔴 N-02 雙保險（負面斷言）：`_fire_life_thread_slot` 內**不得**出現這些前綴的
#: import。與 `_ALLOWED_SLOT_IMPORTS` 的關係：白名單是**正向精確集合等值**
#: （任何新 import 都紅），這條是**負向前綴黑名單**（就算有人把白名單放寬，
#: Agency／SAGE 這些「0 進入觸發鏈／0 落盤」的核心禁令仍獨立成立）。
#: 兩者互補：白名單防漂移，黑名單防放寬，任一單獨存在都有缺口。
_FORBIDDEN_SLOT_IMPORT_PREFIXES = ("src.agency", "src.memory.sage")


def _slot_import_targets(tree: ast.AST) -> List[str]:
    """`tree` 內所有 import 的**完整模組名**（含別名解析後的路徑、排序、含重複）。

    - `import a.b as c` ⇒ `a.b`
    - `import a.b`      ⇒ `a.b`
    - `from a.b import c` ⇒ `a.b.c`
    - `from a.b import c as d` ⇒ `a.b.c`
    - 相對 import（`level > 0`）⇒ 跳過（無絕對模組名可比對）
    """
    out: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            mod = node.module or ""
            out += [f"{mod}.{a.name}" if mod else a.name for a in node.names]
    return sorted(out)


def _slot_imports() -> List[str]:
    """`_fire_life_thread_slot` **方法節點內**所有 import 的完整模組名（N-02）。"""
    return _slot_import_targets(_scheduler_slot_tree())


def _scheduler_tree() -> ast.AST:
    """`scheduler.py` 的 AST（F-03：所有排程器護欄的唯一輸入）。"""
    return ast.parse(SCHEDULER_PATH.read_text(encoding="utf-8"))


def _scheduler_slot_tree() -> ast.AST:
    """`_fire_life_thread_slot` 的**函式節點**（AST 取範圍，非固定字元窗）。"""
    return _find_function(_scheduler_tree(), _SCHEDULER_SLOT_FN)


def _run_loop_tree() -> ast.AST:
    """`_run_loop` 的**函式節點**（F-04：取代 `src.find(...)` 的字元切片）。"""
    return _find_function(_scheduler_tree(), _RUN_LOOP_FN)


def _count_sleep_calls(tree: ast.AST, seconds: int) -> int:
    """AST 計數：`*.sleep(<seconds>)` 的呼叫節點數（引數須為字面整數）。"""
    n = 0
    for dotted, call in _walk_calls(tree):
        if not dotted.endswith("sleep") or len(call.args) != 1:
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and arg.value == seconds:
            n += 1
    return n


def _count_while_nodes(tree: ast.AST) -> int:
    """AST 計數：`ast.While` 節點數。"""
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.While))


def test_b0_scheduler_sleep_and_while_counts_unchanged():
    """`asyncio.sleep(30)` 恰 2 處；`_run_loop` 內 `while` 恰 1 處（0 新定時器）。

    🔴 F-03.3：改用 **AST 節點計數**（`Call(Attribute(attr='sleep'))` 且引數為 `30`；
    `ast.While` 節點數）。舊版的 `src.count("asyncio.sleep(30)")` 是**文字比對**：
    註解／字串裡的同字串會假陽性，而 `await asyncio.sleep(30)` 一旦改寫成
    `asyncio.sleep(SLEEP_SECS)`（語意完全相同）就**假陰性** —— 兩種都讓「0 新定時器」
    這個不變量量測失真。
    """
    tree = _scheduler_tree()
    assert _count_sleep_calls(tree, 30) == 2, "0 新定時器：主循環 + except 恢復各一"
    assert _count_while_nodes(_run_loop_tree()) == 1


def test_b1_scheduler_mounts_slot_pipeline_exactly_once():
    """`_fire_life_thread_slot` 被呼叫**恰 1 處**，且**在** `_fire_periodic_narrative` **之後**。

    🔴 F-03.6：改為 AST —— 掛載點是 `_run_loop` 內的 `ast.Call` 節點，順序以
    `lineno` 判定（而非 `src.index` 的字元位移，後者會被註解與字串位移欺騙）。
    """
    loop = _run_loop_tree()
    mounts = [c for d, c in _walk_calls(loop) if d.endswith(f"self.{_SCHEDULER_SLOT_FN}")]
    assert len(mounts) == 1, f"掛載點必須恰 1 處：{[m.lineno for m in mounts]}"
    mount = mounts[0]

    _slot_fn = _scheduler_slot_tree()  # 方法本體必須存在且簽名帶 now
    args = _slot_fn.args.args
    assert [a.arg for a in args][1:] == ["now"], [a.arg for a in args]

    narrative = [
        c for d, c in _walk_calls(loop) if d.endswith("_fire_periodic_narrative")
    ]
    assert len(narrative) == 1, [c.lineno for c in narrative]

    # 健康 log：`_run_loop` 內讀寫 `last_health_log` 的**比較**語句
    health = [
        node for node in ast.walk(loop)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(x, ast.Name) and x.id == "last_health_log"
            for x in ast.walk(node)
        )
    ]
    assert len(health) == 1, [h.lineno for h in health]

    assert narrative[0].lineno < mount.lineno < health[0].lineno, (
        "掛載點必須在 period narrative 之後、健康 log 之前："
        f"narrative@{narrative[0].lineno} mount@{mount.lineno} health@{health[0].lineno}"
    )


def test_b2_scheduler_slot_method_skeleton_is_protocol_matched():
    """新方法逐字照抄 `_fire_periodic_narrative` 骨架：lazy import 在 try 內 + fail-closed。

    🔴 F-04：不再用 `src[idx:idx + 1400]` 的**固定字元窗**（實測函式 1045 字元、
    餘裕僅 355 字元 ⇒ 一長就滑出窗外、護欄靜默空轉）。改以 AST 節點範圍切出
    確切的函式原始碼；「lazy import 在 try 內」也由**節點範圍從屬關係**判定
    （`ImportFrom` 的 `lineno` 落在 `Try` 的 `lineno..end_lineno` 之間）。
    """
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    tree = _scheduler_tree()
    fn = _find_function(tree, _SCHEDULER_SLOT_FN)
    body_src = _function_source(src, tree, _SCHEDULER_SLOT_FN)

    assert "if not self._all_agents:" in body_src
    assert 'if slot not in ("morning", "night"):' in body_src
    assert "except Exception as e:" in body_src
    assert "logger.warning" in body_src

    lazy = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "") == "src.soul"
        and any(a.name == "life_thread_orchestrator" for a in node.names)
    ]
    assert len(lazy) == 1, f"lazy import 恰 1 處：{[n.lineno for n in lazy]}"

    tries = [node for node in ast.walk(fn) if isinstance(node, ast.Try)]
    assert tries, "必須有 try（fail-closed 骨架）"
    inside = [
        t for t in tries
        if t.lineno <= lazy[0].lineno <= (t.end_lineno or t.lineno)
    ]
    assert inside, "lazy import 必須在 try 內（與 _fire_periodic_narrative 同構）"

    pipeline = [
        c for d, c in _walk_calls(fn) if d.endswith("run_slot_pipeline")
    ]
    assert len(pipeline) == 1, [c.lineno for c in pipeline]
    assert "".join(ast.unparse(a) for a in pipeline[0].args)


def test_b3_scheduler_slot_criteria_unchanged():
    """`_slot_for_time` 判據不動（±60s 窗、morning/night 兩點）。

    🔴 F-03.6：改為 AST —— `def _slot_for_time` 恰 1 個節點、比較式為
    `0 <= diff < 60`、迴圈走訪的是 `[("morning", morning_time), ("night", night_time)]`。
    """
    tree = _scheduler_tree()
    fn = _find_module_level_function(tree, "_slot_for_time")
    defs = [fn]
    assert len(defs) == 1, f"_slot_for_time 必須恰 1 個：{len(defs)}"

    compares = [
        ast.unparse(node) for node in ast.walk(defs[0])
        if isinstance(node, ast.Compare)
        and any(isinstance(op, ast.Lt) for op in node.ops)
    ]
    assert "0 <= diff < 60" in compares, compares

    slots = [
        node for node in ast.walk(defs[0])
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.List, ast.Tuple))
    ]
    assert len(slots) == 1, [n.lineno for n in slots]
    literal = ast.unparse(slots[0].iter)
    assert "'morning'" in literal and "'night'" in literal, literal
    assert "self.morning_time" in literal and "self.night_time" in literal, literal


def test_b4_scheduler_does_not_leak_new_event_types():
    """排程器不得新增 `trigger_type`／`bus.publish`（0 進入 Agency 觸發鏈）。

    🔴 F-03.1：舊版是 `assert "trigger_type" not in body` 的**文字比對**（0 命中型），
    且 `body` 還是固定 1400 字元窗（F-04）。現在改為對
    `_fire_life_thread_slot` 的 **AST 子樹**掃 `Name`／`Attribute`／`arg` 識別字，
    並斷言 **code-only 命中為 0**：
    `bus.publish` ⇒ `Attribute(value=Name('bus'), attr='publish')` 會被抓到，
    而**文件字串／註解裡的同名散文不算**（舊版反而會被註解假陽性）。
    """
    fn = _scheduler_slot_tree()
    hits = sorted({
        ident for ident in _identifiers_and_dotted(fn)
        if ident in _FORBIDDEN_TRIGGER_IDENTS
    })
    assert hits == [], f"{_SCHEDULER_SLOT_FN} 不得出現觸發鏈識別字：{hits}"

    dotted = sorted({d for d, _c in _walk_calls(fn) if d})
    assert not any(d.endswith(".publish") for d in dotted), dotted

    # code-only 證明：docstring 的散文不得被算進去（舊版文字比對會誤抓）
    prose = ast.parse(
        'def f():\n'
        '    """本方法不 publish、不新增 trigger_type。"""\n'
        '    return 1\n'
    )
    doc = prose.body[0].body[0].value.value
    assert _FORBIDDEN_TRIGGER_IDENTS[0] in doc, (
        "docstring 內確實含禁用詞（用來證明判準有區分能力）"
    )
    prose_hits = [
        ident for ident in _code_only_idents(prose)
        if ident in _FORBIDDEN_TRIGGER_IDENTS
    ]
    assert prose_hits == [], f"docstring 散文不得被算成 code 命中：{prose_hits}"
    # 對照：docstring 的**散文**（非 docstring 物件）若被文字比對掃到就會假陽性
    assert any(b in doc for b in _FORBIDDEN_TRIGGER_IDENTS), doc


def test_b4b_trigger_type_as_keyword_argument_is_detected():
    """牙齒證明（🔴 變異測試抓到的真實假陰性）：`trigger_type=` **關鍵字引數**必須被 DETECT。

    本票的變異測試在 `_fire_life_thread_slot` 內插入
    `logger.debug(trigger_type='life_thread')`，舊判準（只掃 `Name`／`Attribute`）
    **完全看不到** —— 因為關鍵字引數名只存在於 `ast.keyword.arg`。
    這裡把「真關鍵字引數 ⇒ 命中」與「docstring 散文明講 ⇒ 不命中」釘死成兩筆斷言。
    """
    leaked = ast.parse(
        "def f():\n"
        "    logger.debug(trigger_type='life_thread')\n"
        "    return 1\n"
    )
    hits = sorted({
        ident for ident in _code_only_idents(leaked)
        if ident in _FORBIDDEN_TRIGGER_IDENTS
    })
    assert hits == ["trigger_type"], hits

    prose = ast.parse(
        'def f():\n'
        '    """不新增 trigger_type。"""\n'
        '    return 1\n'
    )
    assert [
        ident for ident in _code_only_idents(prose)
        if ident in _FORBIDDEN_TRIGGER_IDENTS
    ] == []

    # 真 `bus.publish` 也要命中（Attribute 路徑）
    published = ast.parse("def f():\n    bus.publish(x)\n")
    assert [
        ident for ident in _code_only_idents(published)
        if ident in _FORBIDDEN_TRIGGER_IDENTS
    ] == ["publish"]


def test_b7_scheduler_slot_imports_are_exact_set():
    """🔴 N-02：`_fire_life_thread_slot` 內 import 的模組名**精確集合等值**。

    證據（GUARD-FIX-2）：在該方法內插入 `from src.agency import bus`，
    **整個 `tests/soul` 859 筆全綠** —— 因為 `_FORBIDDEN_MODULE_PREFIXES`
    只套用在 orchestrator 的 `_audit_source`，這個 M5 新方法沒被任何護欄守到。

    判準改為**白名單精確等值**（比黑名單嚴）：該方法目前只准有一個 lazy
    import，任何新增（`src.agency`／`src.memory.sage`／甚至多餘的 `src.soul.*`）
    都會讓 `==` 失敗。原本的 `test_b2` 只在「刪掉既有 lazy import」時紅，
    這條補上「**多**一個 import」的缺口。
    """
    got = _slot_imports()
    assert got == list(_ALLOWED_SLOT_IMPORTS), (
        f"{_SCHEDULER_SLOT_FN} 的 import 必須是精確集合 {list(_ALLOWED_SLOT_IMPORTS)}，"
        f"實得 {got}"
    )
    assert len(got) == 1, f"恰 1 個 import（currently {got}）"


def test_b8_scheduler_slot_has_no_forbidden_module_imports():
    """🔴 N-02 雙保險：`_fire_life_thread_slot` 內不得出現 Agency／SAGE 的 import。

    與 `test_b7_scheduler_slot_imports_are_exact_set` 的關係：
    `test_b7` 是**正向白名單精確集合等值**（防漂移）；
    本測試是**負向前綴黑名單**（防白名單被後人放寬）。
    兩者互補，任一單獨存在都有缺口 —— 故刻意**同時**保留。

    判準一律 **AST**（`ImportFrom.module`／`Import.names`），
    不吃文字：docstring／註解裡寫 `src.agency` 不會假紅。
    """
    got = _slot_imports()
    bad = [
        leaf for leaf in got
        if leaf.startswith(_FORBIDDEN_SLOT_IMPORT_PREFIXES)
    ]
    assert bad == [], f"{_SCHEDULER_SLOT_FN} 不得 import 觸發鏈／落盤模組：{bad}"

    # 判準自身的能力證明：這些**真 import** 必須被判準抓到（AST 路徑）
    for src, expected in (
        ("def f():\n    from src.agency import bus\n", ["src.agency.bus"]),
        ("def f():\n    import src.memory.sage.writer\n", ["src.memory.sage.writer"]),
        ("def f():\n    from src.agency.bus import publish\n", ["src.agency.bus.publish"]),
        ("def f():\n    from src.agency import bus as b\n", ["src.agency.bus"]),
    ):
        targets = _slot_import_targets(ast.parse(src))
        assert targets == expected, (src, targets)
        assert [t for t in targets if t.startswith(_FORBIDDEN_SLOT_IMPORT_PREFIXES)], (
            f"黑名單必須命中：{targets}"
        )

    # 反向：不相關的 import 不得被黑名單誤判（避免假紅）
    #   注意 `from src.memory import sage` **會**命中（點名展開後 == `src.memory.sage`）
    #   —— 那是正確行為，不是假紅，故對照組刻意選真正不相關的路徑。
    for src in (
        "def f():\n    from src.soul import life_thread_orchestrator\n",
        "def f():\n    import json\n",
        "def f():\n    from src.memory import recall\n",
        "def f():\n    from src.agencies import bus\n",
    ):
        targets = _slot_import_targets(ast.parse(src))
        assert [t for t in targets if t.startswith(_FORBIDDEN_SLOT_IMPORT_PREFIXES)] == [], (
            f"不得假紅：{targets}"
        )

    # 散文不算：docstring／註解／字串常數裡的 `src.agency` 不得命中
    prose = ast.parse(
        'def f():\n'
        '    """本方法不 import src.agency，也不碰 src.memory.sage。"""\n'
        '    # from src.agency import bus  <- 只是註解\n'
        '    s = "src.agency.bus"\n'
        '    return s\n'
    )
    assert _slot_import_targets(prose) == [], (
        f"散文不得算成 import：{_slot_import_targets(prose)}"
    )


def test_b5_run_server_injects_llm_proxy_with_existing_object():
    """C 節：`scripts/run_server.py` 用**既有** LLM proxy 物件注入 M4 接縫。

    🔴 F-03.2／F-03.6：改為 AST。斷言的是**節點事實**而非字串：
    `from src.soul import life_thread_origins as <alias>` 恰 1 筆（別名不拘，靠
    `_module_aliases()` 解析），且該別名上的 `set_llm_proxy(llm)` 恰 1 次、
    引數是既有的 `llm` 物件；注入順序以 `lineno` 判定。
    """
    src = RUN_SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    aliases = _module_aliases(tree)

    origins_roots = {
        alias for alias, full in aliases.items()
        if full == "src.soul.life_thread_origins"
        or full.endswith(".life_thread_origins")
    }
    assert len(origins_roots) == 1, f"life_thread_origins 的別名恰 1 個：{aliases}"

    imports = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "") == "src.soul"
        and any(a.name == "life_thread_origins" for a in node.names)
    ]
    assert len(imports) == 1, [n.lineno for n in imports]

    injected = [
        call for dotted, call in _walk_calls(tree)
        if _resolve_alias(aliases, dotted).endswith("life_thread_origins.set_llm_proxy")
    ]
    assert len(injected) == 1, f"set_llm_proxy 注入恰 1 次：{[c.lineno for c in injected]}"
    assert [ast.unparse(a) for a in injected[0].args] == ["llm"], ast.unparse(injected[0])

    motive = [
        call for dotted, call in _walk_calls(tree)
        if _resolve_alias(aliases, dotted).endswith("src.soul.motive.set_llm_proxy")
        or dotted == "set_motive_llm_proxy"
    ]
    assert motive, "run_server 內應有既有的 motive 注入點"
    assert motive[0].lineno < injected[0].lineno, (
        "M4 注入點必須在既有注入區（motive 之後）："
        f"motive@{motive[0].lineno} origins@{injected[0].lineno}"
    )


def test_b6_run_server_has_no_extra_life_thread_wiring():
    """除注入行外，run_server 不得認得 M4/M5 的其他入口。

    🔴 F-03.2：舊版是 `for banned in (...): assert banned not in src` 的**文字比對
    整個 `run_server.py`** —— 一行註解、一個字串、甚至同名的無關變數都能讓它假紅／
    假綠。現在改為 **AST 精確集合等值**：
    `run_origin_round` 的呼叫數 ＝ **0**，而 `life_thread_origins` 相關的呼叫集合
    **恰為** `{"set_llm_proxy"}`（多一個就紅，不是「字串不在」）。
    """
    tree = ast.parse(RUN_SERVER_PATH.read_text(encoding="utf-8"))
    aliases = _module_aliases(tree)

    calls = sorted({d for d, _c in _walk_calls(tree) if d})
    resolved = sorted({_resolve_alias(aliases, d) for d in calls})

    forbidden = [
        d for d in calls
        if any(d == f or d.endswith("." + f) for f in _FORBIDDEN_RUN_SERVER_CALLS)
    ]
    assert forbidden == [], f"run_server 不得呼叫 M4/M5 入口：{forbidden}"

    origins_calls = sorted({
        r.rsplit(".", 1)[-1] for r in resolved
        if r.startswith("src.soul.life_thread_origins.")
    })
    assert origins_calls == list(_ALLOWED_RUN_SERVER_ORIGINS_CALLS), origins_calls


# ══════════════════════════════════════════════════════════════
# 14. 純函式／不 mutate 入參／順序決定性
# ══════════════════════════════════════════════════════════════


def test_c0_input_sequence_not_mutated(iso_env, monkeypatch):
    """入參 `agent_ids` 不得被 mutate（不排序、不增刪）。"""
    _patch_soul(monkeypatch)
    agents = [OTHER, AGENT]
    snapshot = list(agents)
    _run(agents, MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert agents == snapshot


def test_c1_result_key_order_follows_input_order(iso_env, monkeypatch):
    """輸出順序 ＝ 輸入順序（**不排序**，決定性由呼叫端決定）。"""
    _patch_soul(monkeypatch)
    forward = _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    m5.reset_state()
    reverse = _run([OTHER, AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert list(forward) == [AGENT, OTHER]
    assert list(reverse) == [OTHER, AGENT]


def test_c2_m1_reads_use_only_public_api():
    """M1 讀取一律經公開 API（`fold` / `capacity`），不得繞道私有函式。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "lt.fold(" in src
    assert "lt.capacity(" in src
    for private in ("lt.read_entries(", "lt._append_entry(", "lt._validate_"):
        assert private not in src, private


def test_c3_no_io_at_module_import_time():
    """模組層不得在 import 時做 I/O（唯一允許的模組層呼叫是 `logging.getLogger`）。

    只掃**模組層語句**（跳過函式／類別本體），否則函式內的正常呼叫會被誤算。
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    calls: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        calls += _calls_named(node)
    assert set(calls) <= {"getLogger"}, f"模組層不得有 I/O：{sorted(set(calls))}"


# ══════════════════════════════════════════════════════════════
# 15. 感知檔讀取（路徑一致／fail-silent／每輪一次）
# ══════════════════════════════════════════════════════════════


def test_d0_perception_path_matches_m4_exactly():
    """🔴 路徑必須與 M4 `_perception_trace_path()` **逐字一致**。"""
    assert m5._perception_records_path() == lt_origins._perception_trace_path()


def test_d1_missing_file_returns_empty(iso_env):
    """檔案不存在 ⇒ `[]`，不 raise。"""
    assert m5._read_perception_records() == []


def test_d2_bad_lines_skipped_without_raise(iso_env):
    """壞行跳過、空行跳過、好行保留（fail-silent）。"""
    _write_perception([], raw_lines=[
        json.dumps({"event_id": "ok-1", "accepted": True}, ensure_ascii=False),
        "}{ 這不是 JSON",
        "",
        "   ",
        json.dumps({"event_id": "ok-2"}, ensure_ascii=False),
    ])
    records = m5._read_perception_records()
    assert [r.get("event_id") for r in records] == ["ok-1", "ok-2"]


def test_d3_unreadable_path_returns_empty(iso_env, monkeypatch):
    """路徑取得失敗／非檔案 ⇒ `[]`，不 raise。"""
    def boom():
        raise RuntimeError("no path")

    monkeypatch.setattr(m5, "_perception_records_path", boom)
    assert m5._read_perception_records() == []

    monkeypatch.setattr(m5, "_perception_records_path", lambda: Path(iso_env))
    assert m5._read_perception_records() == []


def test_d4_perception_read_once_per_round(iso_env, monkeypatch):
    """**每輪只讀一次**（全體 agent 共用），不是 per-agent。"""
    reads: List[int] = []
    real = m5._perception_records_path
    monkeypatch.setattr(
        m5, "_perception_records_path", lambda: (reads.append(1), real())[1]
    )
    _patch_soul(monkeypatch)
    _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert len(reads) == 1, f"每輪只准讀 1 次：{len(reads)}"


def test_d5_perception_records_are_passed_to_gate_and_build(iso_env, monkeypatch):
    """同一份感知記錄餵給 M3 與 M4（0 重複讀檔）。"""
    _write_perception([
        {"event_id": "nv-1", "accepted": True, "source": "weather",
         "event_type": "rain_started", "timestamp": MORNING.astimezone(timezone.utc).isoformat(),
         "extra": {"summary": "窗外開始下雨。"}},
    ])
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    captured: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        captured.append(k)
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    assert len(calls[0]["kwargs"]["recent_perceptions"]) == 1
    assert len(captured[0]["build_kwargs"]["world_records"]) == 1


def test_d6_world_collision_wake_path(iso_env, monkeypatch):
    """世界碰撞（M3 判定 2）也能喚醒 ⇒ 走 `world_collision` origin（無線頭亦可）。"""
    _write_perception([
        {"event_id": "nv-2", "accepted": True, "source": "weather",
         "event_type": "rain_started", "timestamp": MORNING.astimezone(timezone.utc).isoformat(),
         "extra": {"summary": "窗外開始下雨。"}},
    ])
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([
        {"op": "create", "title": "雨中的線頭", "narrative_content": "雨打在窗上。",
         "origin_type": "world_collision", "next_check_hours": 8},
    ]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is True
    assert out[AGENT]["origin_type"] == "world_collision"
    assert out[AGENT]["reason"] == lt_gate.REASON_WORLD_COLLISION_WAKE
    assert len(out[AGENT]["origin_round"]["created"]) == 1
    assert len(spy.calls) == 1


# ══════════════════════════════════════════════════════════════
# 16. 張力訊號未供給（契約 §4.2 純淨）
# ══════════════════════════════════════════════════════════════


def test_e0_unresolved_tensions_is_none(iso_env, monkeypatch):
    """🔴 修正 3：本票傳 `unresolved_tensions=None`（張力供給為後續票）。"""
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    assert calls[0]["kwargs"]["unresolved_tensions"] is None


def test_e1_gate_receives_float_epoch(iso_env, monkeypatch):
    """M3 的 `current_time` 必須是 **float epoch**（`now.timestamp()`）。"""
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))
    current_time = calls[0]["args"][1]
    assert isinstance(current_time, float)
    assert current_time == MORNING.timestamp()


def test_e2_gate_receives_agent_and_slot(iso_env, monkeypatch):
    """M3 收到 agent_id / slot 原值（不做任何正規化改寫）。"""
    calls = _spy_gate(monkeypatch)
    _patch_soul(monkeypatch)
    _run([AGENT], NIGHT, "night", llm_caller=_SpyLLM(_json_response([])))
    assert calls[0]["args"][0] == AGENT
    assert calls[0]["args"][2] == "night"


def test_e3_no_llm_when_no_signal_and_no_threads(iso_env, monkeypatch):
    """無到期線頭、無碰撞 ⇒ SLEEP 留白，**0 LLM**（管線不得亂花錢）。"""
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    out = _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert out[AGENT]["woke"] is False
    assert spy.calls == []
