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
ALLOWED_SRC_IMPORTS = frozenset({
    "src.soul.life_threads",
    "src.soul.life_thread_wake_gate",
    "src.soul.life_thread_dissolution",
    "src.soul.life_thread_origins",
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
    assert round_result["dissolved"] == []  # dissolve_hook=None ⇒ 0 SAGE

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
    """顯式傳入的 `soul_context` / `due_threads` 真的進到 prompt（非二次 I/O）。"""
    tid = _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    spy = _SpyLLM(_json_response([]))
    _run([AGENT], MORNING, "morning", llm_caller=spy)
    assert len(spy.calls) == 1
    user_msg = spy.calls[0]["messages"][1]["content"]
    assert tid in user_msg
    assert _TITLE in user_msg


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


def test_62_fold_called_once_per_agent_and_never_list_active(iso_env, monkeypatch):
    """每 agent **只 fold 一次**（1 次整檔讀）派生兩份視圖；**不得**呼叫 `list_active()`。

    場景刻意選「兩隻 agent 都沒有線頭」（無 WAKE）⇒ 管線內唯一會碰 M1 讀取的就是
    orchestrator 自己，計數因此是決定性的。
    """
    folds: List[str] = []
    list_active_calls: List[str] = []
    real_fold = lt.fold
    real_list_active = lt.list_active

    def spy_fold(agent_id):
        folds.append(agent_id)
        return real_fold(agent_id)

    def spy_list_active(agent_id):
        list_active_calls.append(agent_id)
        return real_list_active(agent_id)

    monkeypatch.setattr(lt, "fold", spy_fold)
    monkeypatch.setattr(lt, "list_active", spy_list_active)
    _patch_soul(monkeypatch)
    _run([AGENT, OTHER], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    assert folds == [AGENT, OTHER], f"每 agent 恰 1 次 fold：{folds}"
    assert list_active_calls == [], "🔴 不得用 list_active()（修正 5：會讓 M2 永遠 no-op）"


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
# 9. dissolve_hook is None（⇒ 0 SAGE）
# ══════════════════════════════════════════════════════════════


def test_80_run_origin_round_receives_dissolve_hook_none(iso_env, monkeypatch):
    """攔 `run_origin_round`：`dissolve_hook` 必須是 `None`（M2 執行層 out of scope）。"""
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
    assert captured[0]["kwargs"]["dissolve_hook"] is None


def test_81_run_origin_round_receives_due_threads_and_build_kwargs(iso_env, monkeypatch):
    """`due_threads` ＝ active fold state 清單；`build_kwargs` 顯式帶 soul_context／world_records。"""
    tid = _seed_due_thread(now=MORNING)
    _patch_soul(monkeypatch)
    captured: List[Dict[str, Any]] = []
    real = lt_origins.run_origin_round

    async def spy(*a, **k):
        captured.append({"args": a, "kwargs": k})
        return await real(*a, **k)

    monkeypatch.setattr(lt_origins, "run_origin_round", spy)
    _run([AGENT], MORNING, "morning", llm_caller=_SpyLLM(_json_response([])))

    kw = captured[0]["kwargs"]
    due = kw["due_threads"]
    assert isinstance(due, list) and len(due) == 1
    # due_threads 形狀 ＝ M1 fold state（`_render_due_threads` 逐字使用的欄位）
    for field in ("thread_id", "title", "status", "check_after_ts", "narrative_content"):
        assert field in due[0], field
    assert due[0]["thread_id"] == tid
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
    """原始碼層：orchestrator 必須顯式 `except asyncio.CancelledError: raise`。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "except asyncio.CancelledError:" in src
    idx = src.index("except asyncio.CancelledError:")
    assert "raise" in src[idx:idx + 120]


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


def test_a0_orchestrator_has_zero_forbidden_constructs():
    """orchestrator：0 `bus.publish`／`trigger_type`／`src.agency`／`src.memory.sage`
    ／`append_dissolved`／`append_transition`／`open(` 寫入。"""
    assert _audit_source(MODULE_PATH.read_text(encoding="utf-8")) == []


def test_a1_orchestrator_import_whitelist_only():
    """orchestrator 的 `src.*` import 白名單**恰為** 4 個模組。"""
    leaves = _leaf_imports(ast.parse(MODULE_PATH.read_text(encoding="utf-8")))
    src_leaves = [x for x in leaves if x.startswith("src.")]
    allowed_ok = all(
        x in ALLOWED_SRC_IMPORTS or any(x.startswith(a + ".") for a in ALLOWED_SRC_IMPORTS)
        for x in src_leaves
    )
    assert allowed_ok, f"白名單外 import：{src_leaves}"
    assert len(src_leaves) == 4, src_leaves


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

    以 AST 判定：不得呼叫 `life_threads_path`／`LIFE_THREADS_FILENAME`，且**非 docstring**
    的字串常數不得出現 `life_threads.jsonl`（文件字串裡說明副作用位置不算違規）。
    """
    src = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "life_threads_path" not in _calls_named(tree)
    assert "LIFE_THREADS_FILENAME" not in _calls_named(tree)
    assert "LIFE_THREADS_FILENAME" not in src
    for text in _non_docstring_strings(tree):
        assert "life_threads.jsonl" not in text, text
        assert "life_threads_path" not in text, text


def test_a6_orchestrator_reads_only_perception_trace():
    """唯一 I/O ＝ 1 次感知檔讀取（路徑經 M4 的 `_perception_trace_path`）。"""
    src = MODULE_PATH.read_text(encoding="utf-8")
    assert "_perception_trace_path" in src
    assert "read_text" in src  # 唯讀
    assert "import asyncio" in src, "CancelledError 明示需要 asyncio"


# ══════════════════════════════════════════════════════════════
# 13. 排程器掛載護欄
# ══════════════════════════════════════════════════════════════


def _run_loop_body() -> str:
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    start = src.find("async def _run_loop")
    end = src.find("async def _goal_scan_all")
    assert start != -1 and end > start
    return src[start:end]


def test_b0_scheduler_sleep_and_while_counts_unchanged():
    """`asyncio.sleep(30)` 恰 2 處；`_run_loop` 內 `while` 恰 1 處（0 新定時器）。"""
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    assert src.count("asyncio.sleep(30)") == 2, "0 新定時器：主循環 + except 恢復各一"
    assert _run_loop_body().count("while ") == 1


def test_b1_scheduler_mounts_slot_pipeline_exactly_once():
    """`_fire_life_thread_slot` 被呼叫**恰 1 處**，且**在** `_fire_periodic_narrative` **之後**。"""
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    assert src.count("self._fire_life_thread_slot(") == 1
    assert "async def _fire_life_thread_slot(self, now" in src
    mount = src.index("self._fire_life_thread_slot(now)")
    narrative = src.index("await self._fire_periodic_narrative()")
    health = src.index("if (now.timestamp() - last_health_log)")
    assert narrative < mount < health, "掛載點必須在 period narrative 之後、健康 log 之前"


def test_b2_scheduler_slot_method_skeleton_is_protocol_matched():
    """新方法逐字照抄 `_fire_periodic_narrative` 骨架：lazy import 在 try 內 + fail-closed。"""
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    idx = src.index("async def _fire_life_thread_slot")
    body = src[idx:idx + 1400]
    assert "if not self._all_agents:" in body
    assert 'if slot not in ("morning", "night"):' in body
    assert "try:" in body
    assert "from src.soul import life_thread_orchestrator" in body
    assert "await _lt_orchestrator.run_slot_pipeline(list(self._all_agents), now, slot)" in body
    assert "except Exception as e:" in body
    assert "logger.warning" in body
    # lazy import 必須在 try 內（與 _fire_periodic_narrative 同構）
    assert body.index("try:") < body.index("from src.soul import life_thread_orchestrator")


def test_b3_scheduler_slot_criteria_unchanged():
    """`_slot_for_time` 判據不動（±60s 窗、morning/night 兩點）。"""
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    assert "if 0 <= diff < 60:" in src
    assert 'for slot, t in [("morning", self.morning_time), ("night", self.night_time)]' in src
    assert src.count("def _slot_for_time") == 1


def test_b4_scheduler_does_not_leak_new_event_types():
    """排程器不得新增 `trigger_type`／`bus.publish`（0 進入 Agency 觸發鏈）。"""
    src = SCHEDULER_PATH.read_text(encoding="utf-8")
    idx = src.index("async def _fire_life_thread_slot")
    body = src[idx:idx + 1400]
    assert "trigger_type" not in body
    assert "publish" not in body


def test_b5_run_server_injects_llm_proxy_with_existing_object():
    """C 節：`scripts/run_server.py` 用**既有** LLM proxy 物件注入 M4 接縫。"""
    src = RUN_SERVER_PATH.read_text(encoding="utf-8")
    assert "from src.soul import life_thread_origins as _lt_origins" in src
    assert "_lt_origins.set_llm_proxy(llm)" in src
    # 注入點必須在既有注入區（motive 之後），且用同一顆 llm 物件
    assert src.index("set_motive_llm_proxy(llm)") < src.index("_lt_origins.set_llm_proxy(llm)")
    assert src.count("_lt_origins.set_llm_proxy(") == 1


def test_b6_run_server_has_no_extra_life_thread_wiring():
    """除注入行外，run_server 不得認得 M4/M5 的其他入口。"""
    src = RUN_SERVER_PATH.read_text(encoding="utf-8")
    for banned in ("run_origin_round(", "build_origin_prompt(", "apply_actions(",
                   "run_slot_pipeline", "life_thread_orchestrator"):
        assert banned not in src, f"run_server 不得出現：{banned}"


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
