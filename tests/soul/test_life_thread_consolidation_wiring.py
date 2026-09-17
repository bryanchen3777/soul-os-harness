# tests/soul/test_life_thread_consolidation_wiring.py
# LIFE-THREAD-M2-WIRING-1 — 沉澱接線層（flag / 同步 hook / adapter / fact_id 回填）測試矩陣。
#
# 受測模組：`src/soul/life_thread_consolidation_wiring.py`
#
# 本檔紅線自證：
#   * **0 真實 LLM、0 網路**：所有 LLM 一律注入 fake proxy／fake backend；HTTP 層用
#     `httpx.MockTransport`（記憶體內回應，**不對任何 URL 發請求**）。
#   * **0 生產 `data/**` 寫入**：`tests/conftest.py` 的 autouse 資料根隔離把
#     `SOUL_OS_DATA_DIR` 指向 pytest tmp；唯一真實 SAGE 寫入落在 tmp 的 `graph.sqlite`。
#   * 不啟停任何服務、不綁任何埠（本檔**不含**生產服務位址字面量）、0 行程命令。
#   * 背景任務一律在測試結束前被 `await`（autouse fixture 斷言 0 殘留）。
#   * §E 額外釘死**逾時單一事實來源**：接線模組**不得**對執行層呼叫施加短於執行層內層
#     逾時（`life_thread_dissolution_exec.CONSOLIDATION_TIMEOUT_SECONDS` ＝ 120 s）的
#     包裹（外層短於內層 ⇒ 錢花了、結果被丟棄；見模組 docstring
#     「逾時單一事實來源（LIFE-THREAD-M2-WIRING-1-FUP）」）。
#
# 慣例對齊 `tests/soul/test_life_thread_dissolution_exec.py`（`tests/soul/` 無
# `__init__.py`，本檔自行 `sys.path.insert(0, REPO_ROOT)`）。
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.llm.proxy as proxy_mod  # noqa: E402
import src.soul.life_thread_consolidation_wiring as w  # noqa: E402
import src.soul.life_thread_dissolution_exec as ex  # noqa: E402
import src.soul.life_thread_orchestrator as m5  # noqa: E402
import src.soul.life_threads as lt  # noqa: E402

AGENT = "agent_ltwiring"
THREAD_ID = "th-ltwiring-1"
NARRATIVE = "我把簡報做完了，這讓我覺得自己能扛事。"
LLM_JSON = json.dumps({"dissolution": NARRATIVE, "meaning_kind": "competence"})
FACT_ID = "fact-ltwiring-abc123"

#: 假 LLM 回傳（**永不**是網路回應）。
PROMPT = "你剛完成一條生活線頭：「完成一份簡報」，歷時 7 天。"


# ══════════════════════════════════════════════════════════════
# fixtures / helpers
# ══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolate_process_state():
    """清空**行程級**狀態（否則跨測試互相污染）＋ 斷言 0 背景任務殘留。

    - 執行層的冪等 sentinel／預設預算（`ex._CONSOLIDATED` / `ex._DEFAULT_BUDGET`）。
    - 本模組的 per-agent writer 快取（`w._WRITERS`）—— 否則會沿用**上一個測試**的
      tmp `graph.sqlite`。
    - `m5._LAST_PROCESSED`（orchestrator 的 at-most-once 章）。
    """
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()
    m5.reset_state()
    yield
    assert w.pending_task_count() == 0, (
        "測試結束仍有未清理的背景任務（會出現 'Task was destroyed but it is pending'）"
    )
    w._BACKGROUND_TASKS.clear()
    ex._clear_consolidated_registry()
    ex._reset_default_budget()
    w._reset_writers()
    m5.reset_state()


def _terminal_thread(thread_id: str = THREAD_ID, status: str = "completed") -> Dict[str, Any]:
    """M1 fold 後形狀的**終態**線頭（M2 步驟 2 的唯一觸發條件）。"""
    return {
        "thread_id": thread_id,
        "status": status,
        "title": "完成一份簡報",
        "narrative_content": "她把簡報做完了。",
        "origin_type": "goal_driven",
        "created_at": "2026-09-10T12:00:00+00:00",
        "updated_at": "2026-09-17T12:00:00+00:00",
        "check_after_ts": None,
        "share_target": "none",
        "dissolved_at": None,
        "sage_fact_id": None,
    }


class _RecordingProxy:
    """假 LLMProxy（**0 真實 LLM**）：記錄每次 `generate_text` 的 kwargs。"""

    def __init__(self, text: Optional[str] = LLM_JSON, exc: Optional[BaseException] = None):
        self.calls: List[Dict[str, Any]] = []
        self.text = text
        self.exc = exc

    async def generate_text(self, **kwargs) -> Optional[str]:
        self.calls.append(dict(kwargs))
        if self.exc is not None:
            raise self.exc
        return self.text


class _Boom:
    """被呼叫就 raise（用來證明某條路径**完全沒被觸達**）。"""

    def __init__(self, label: str):
        self.label = label
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"seam 不該被呼叫：{self.label}")


def _drive(
    agent_id: str = AGENT,
    thread_id: str = THREAD_ID,
    status: str = "completed",
    *,
    probe=None,
) -> Dict[str, Any]:
    """在 running loop 內呼叫**同步** hook，並把背景任務 `await` 完（0 殘留）。

    回傳 `{"tasks": [...], "results": [...], "probe_after_hook": ...}`。
    `results` 是 `gather(return_exceptions=True)` 的輸出 ⇒ 任務內例外以物件形式取得
    （不會讓測試炸掉，也不會留 "exception was never retrieved"）。
    """
    out: Dict[str, Any] = {"tasks": [], "results": [], "probe_after_hook": None}

    async def _driver():
        hook = w.build_dissolve_hook()
        hook(agent_id, thread_id, status)
        if probe is not None:
            out["probe_after_hook"] = probe()
        tasks = list(w._BACKGROUND_TASKS)
        out["tasks"] = tasks
        if tasks:
            out["results"] = await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)  # 讓 done-callback 跑完（清 set）

    asyncio.run(_driver())
    return out


def _result_of(out: Dict[str, Any]) -> Any:
    """取出唯一任務的結果（例外物件原樣回傳）。"""
    assert len(out["results"]) == 1, out["results"]
    return out["results"][0]


def _agent_db_path(agent_id: str) -> Path:
    """與 `life_thread_origins._goal_db_path` 相同的路徑（本檔只用於斷言落點）。"""
    from src.soul import life_thread_origins as lt_origins

    return lt_origins._goal_db_path(agent_id)


# ══════════════════════════════════════════════════════════════
# §A 旗標（預設關；呼叫時即時讀取）
# ══════════════════════════════════════════════════════════════


def test_a1_env_var_name_and_truthy_set_are_the_contract():
    assert w.CONSOLIDATION_ENABLED_ENV == "LIFE_THREAD_CONSOLIDATION_ENABLED"
    assert w.TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})
    assert w.CONSOLIDATION_REASONING_EFFORT == "none"


def test_a2_flag_absent_is_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(w.CONSOLIDATION_ENABLED_ENV, raising=False)
    assert w.consolidation_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", " on "])
def test_a3_truthy_values_enable(raw: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, raw)
    assert w.consolidation_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "maybe", "", "  ", "2", "enabled"])
def test_a4_non_truthy_values_stay_off(raw: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, raw)
    assert w.consolidation_enabled() is False


def test_a5_flag_is_read_at_call_time_not_cached(monkeypatch: pytest.MonkeyPatch):
    """旗標**每次呼叫**即時讀取 ⇒ 同一個 hook 物件可隨 env 改變行為。"""
    monkeypatch.delenv(w.CONSOLIDATION_ENABLED_ENV, raising=False)
    hook = w.build_dissolve_hook()
    assert w.consolidation_enabled() is False
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    assert w.consolidation_enabled() is True
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "0")
    assert w.consolidation_enabled() is False
    assert callable(hook)


def test_a6_flag_off_hook_returns_immediately_touching_nothing(
    monkeypatch: pytest.MonkeyPatch,
):
    """🔴 OFF（預設）⇒ hook 立刻返回：**0 LLM／0 SAGE／0 讀檔／0 任務**。

    證明方式：把**所有** seam 換成「被呼叫就 raise」的物件；hook 仍不得拋出任何例外，
    且不得建立背景任務（0 成本是可證明的，不是推測的）。
    """
    monkeypatch.delenv(w.CONSOLIDATION_ENABLED_ENV, raising=False)
    booms = {}
    for name in (
        "_read_thread_state",
        "_evaluate",
        "_resolve_llm_proxy",
        "_make_llm_call",
        "_write_fact",
        "_append_dissolved",
    ):
        booms[name] = _Boom(name)
        monkeypatch.setattr(w, name, booms[name])

    out = _drive(probe=lambda: w.pending_task_count())

    assert out["tasks"] == [], "旗標 OFF ⇒ 不得建立背景任務"
    assert out["probe_after_hook"] == 0
    for name, boom in booms.items():
        assert boom.calls == 0, f"旗標 OFF ⇒ seam {name} 不得被觸達"
    assert w.pending_task_count() == 0


@pytest.mark.parametrize("raw", ["0", "false", "off", ""])
def test_a7_falsy_flagged_hook_is_also_zero_cost(raw: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, raw)
    monkeypatch.setattr(w, "_read_thread_state", _Boom("_read_thread_state"))
    out = _drive()
    assert out["tasks"] == []
    assert w.pending_task_count() == 0


# ══════════════════════════════════════════════════════════════
# §B ON ＋ fake seam：恰 1 次呼叫、fact 落地、M1 回填
# ══════════════════════════════════════════════════════════════


def test_b1_flag_on_runs_once_with_expected_params_and_backfills(
    monkeypatch: pytest.MonkeyPatch,
):
    """ON ＋ fake seam ⇒ 恰 1 次 LLM、`max_tokens=300`、`reasoning_effort="none"`、
    零額外重試、fact 落地恰 1 次、M1 以**正確的 `sage_fact_id`** 回填。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))
    # `_evaluate` **不換**：走真實 M2 決策層（純函式、0 網路）⇒ 證明真整合。
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)

    written: List[Dict[str, Any]] = []

    def fake_write_fact(fact, agent_id):
        written.append(dict(fact))
        return FACT_ID

    backfills: List[Dict[str, Any]] = []
    monkeypatch.setattr(w, "_write_fact", fake_write_fact)
    monkeypatch.setattr(
        w,
        "_append_dissolved",
        lambda a, t, fid: backfills.append({"agent": a, "thread": t, "fact_id": fid}) or True,
    )

    out = _drive(probe=lambda: len(proxy.calls))

    # 🔴 不得 inline await：hook 已返回，但 LLM 尚未被呼叫（改由背景任務執行）
    assert out["probe_after_hook"] == 0, "hook 內不得 inline await LLM（必須丟背景任務）"

    result = _result_of(out)
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert result.llm_calls == 1

    assert len(proxy.calls) == 1, proxy.calls
    call = proxy.calls[0]
    assert call["max_tokens"] == 300
    assert call["reasoning_effort"] == "none", "adapter 必須帶推理抑制（校準 −95.8%）"
    assert call["max_retries"] == 0, "adapter 必須要求零額外重試"
    assert call["temperature"] == w.CONSOLIDATION_TEMPERATURE
    assert call["agent_id"] == AGENT
    assert len(call["messages"]) == 1
    assert call["messages"][0]["role"] == "user"
    assert call["messages"][0]["content"].strip(), "prompt 不得為空（M2 reflection_prompt）"

    assert len(written) == 1, "fact 必須恰落地 1 次"
    fact = written[0]
    assert fact["subject"] == AGENT
    assert fact["predicate"] == ex.FACT_PREDICATE
    assert fact["object"] == NARRATIVE
    assert fact["confidence"] == 1.0

    assert backfills == [{"agent": AGENT, "thread": THREAD_ID, "fact_id": FACT_ID}], backfills


def test_b2_real_m2_prompt_is_what_reaches_the_llm(monkeypatch: pytest.MonkeyPatch):
    """送進 LLM 的 prompt **逐字**是 M2 `evaluation.reflection_prompt`（不重組）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    thread = _terminal_thread()
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: dict(thread))
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)
    monkeypatch.setattr(w, "_write_fact", lambda fact, agent_id: FACT_ID)
    monkeypatch.setattr(w, "_append_dissolved", lambda a, t, fid: True)

    from src.soul import life_thread_dissolution as lt_diss

    evaluation = lt_diss.evaluate_thread_dissolution(
        thread, datetime.now(timezone.utc), agent_id=AGENT
    )
    assert evaluation.should_mutate is True and evaluation.reflection_prompt

    out = _drive()
    assert _result_of(out).status == ex.STATUS_CONSOLIDATED
    assert proxy.calls[0]["messages"][0]["content"] == evaluation.reflection_prompt


def test_b3_end_to_end_real_m1_m2_sage_backfill(monkeypatch: pytest.MonkeyPatch):
    """🔴 端到端（全離線、資料根＝pytest tmp）：M1 建線頭 → 進終態 → hook →
    真實 M2 ＋ 真實執行層 ＋ **真實 SAGE writer** ＋ 真實 M1 回填。

    同時證明 `MemoryWriter.add_fact` 路徑**不觸發 LLM judge**（另一次付費呼叫）。
    """
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")

    tid = lt.create_thread(
        AGENT, "完成一份簡報", "她把簡報做完了。", "goal_driven", check_after_ts=None
    )
    assert tid, "fixture 建線頭失敗"
    assert lt.append_transition(AGENT, tid, "completed") is True
    state = lt.get_state(AGENT, tid)
    assert state is not None and state["status"] == "completed"

    proxy = _RecordingProxy(text=LLM_JSON)
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)

    # SAGE writer 的 LLM judge 一旦被呼叫就炸（本路徑必須 0 呼叫）
    from src.memory.sage import writer as sage_writer

    judge_boom = _Boom("MemoryWriter LLM judge")
    monkeypatch.setattr(sage_writer, "_global_llm_proxy", judge_boom, raising=False)

    out = _drive(agent_id=AGENT, thread_id=tid)
    result = _result_of(out)
    assert isinstance(result, ex.ConsolidationResult), result
    assert result.status == ex.STATUS_CONSOLIDATED, result.reason
    assert judge_boom.calls == 0, "add_fact 不得觸發 LLM judge（否則多一次付費呼叫）"

    after = lt.get_state(AGENT, tid)
    assert after is not None
    assert after["dissolved_at"], "M1 必須回填 dissolved_at"
    assert after["sage_fact_id"], "M1 必須回填 sage_fact_id"
    assert after["sage_fact_id"] != ""
    assert after["status"] == "completed"

    # fact 真的落在該 agent 的 tmp graph.sqlite（0 生產 data/** 接觸）
    writer = w._WRITERS[AGENT]
    stored = writer.store.get_fact(after["sage_fact_id"])
    assert stored is not None
    assert stored.predicate == ex.FACT_PREDICATE
    assert stored.object == NARRATIVE
    assert len(proxy.calls) == 1

    repo_data = (_REPO_ROOT / "data").resolve()
    db = _agent_db_path(AGENT).resolve()
    assert db.is_file()
    assert repo_data not in db.parents, f"SAGE 寫入跑到生產 data/**：{db}"


def test_b4_policy_constants_match_orchestrator(monkeypatch: pytest.MonkeyPatch):
    """本模組的 M2 門檻常數**必須**與 orchestrator 的 Owner 裁定值一致（防漂移）。"""
    assert w.POLICY_MAX_ACTIVE_DURATION_DAYS == m5.POLICY_MAX_ACTIVE_DURATION_DAYS
    assert w.POLICY_STALE_CHECK_THRESHOLD_DAYS == m5.POLICY_STALE_CHECK_THRESHOLD_DAYS


# ══════════════════════════════════════════════════════════════
# §C fail-safe 族：hook 永不拋、記 warning、0 殘留
# ══════════════════════════════════════════════════════════════


def test_c1_m1_read_failure_is_fail_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")

    def boom(a, t):
        raise OSError("m1 read exploded")

    monkeypatch.setattr(w, "_read_thread_state", boom)
    monkeypatch.setattr(w, "_write_fact", _Boom("_write_fact"))

    with caplog.at_level(logging.WARNING, logger=w.logger.name):
        out = _drive()  # 不得拋

    assert isinstance(_result_of(out), BaseException), "任務內例外應被 done-callback 讀取"
    assert any("consolidation task failed" in r.message for r in caplog.records), caplog.text
    assert any("OSError" in r.message for r in caplog.records), caplog.text
    assert w.pending_task_count() == 0


def test_c2_m2_evaluate_failure_is_fail_silent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))

    def boom(thread, now, agent_id):
        raise ValueError("m2 exploded")

    monkeypatch.setattr(w, "_evaluate", boom)

    with caplog.at_level(logging.WARNING, logger=w.logger.name):
        out = _drive()

    assert isinstance(_result_of(out), BaseException)
    assert any("ValueError" in r.message for r in caplog.records), caplog.text
    assert w.pending_task_count() == 0


def test_c3_no_running_loop_is_fail_safe(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """ON 但在**沒有 running loop** 的同步環境呼叫 ⇒ 記 warning ＋ return（不拋）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", _Boom("_read_thread_state"))

    hook = w.build_dissolve_hook()
    with caplog.at_level(logging.WARNING, logger=w.logger.name):
        hook(AGENT, THREAD_ID, "completed")  # 不得拋

    assert any("無 running loop" in r.message for r in caplog.records), caplog.text
    assert w.pending_task_count() == 0


def test_c4_writer_empty_fact_id_skips_backfill(monkeypatch: pytest.MonkeyPatch):
    """writer 回 `""` ⇒ 執行層判 `write_failed`，且**不得**回填 M1。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)
    monkeypatch.setattr(w, "_write_fact", lambda fact, agent_id: "")
    backfill_boom = _Boom("_append_dissolved")
    monkeypatch.setattr(w, "_append_dissolved", backfill_boom)

    out = _drive()
    result = _result_of(out)
    assert isinstance(result, ex.ConsolidationResult)
    assert result.status == ex.STATUS_WRITE_FAILED, result.reason
    assert result.llm_calls == 1
    assert backfill_boom.calls == 0, "寫入失敗不得回填 sage_fact_id"


def test_c5_llm_exception_degrades_without_backfill(monkeypatch: pytest.MonkeyPatch):
    """LLM 拋例外 ⇒ `llm_failed`、0 寫入、0 回填、hook 不拋。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))
    proxy = _RecordingProxy(exc=RuntimeError("llm exploded"))
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)
    write_boom = _Boom("_write_fact")
    backfill_boom = _Boom("_append_dissolved")
    monkeypatch.setattr(w, "_write_fact", write_boom)
    monkeypatch.setattr(w, "_append_dissolved", backfill_boom)

    out = _drive()
    result = _result_of(out)
    assert isinstance(result, ex.ConsolidationResult)
    assert result.status == ex.STATUS_LLM_FAILED, result.reason
    assert result.llm_calls == 1
    assert len(proxy.calls) == 1
    assert write_boom.calls == 0 and backfill_boom.calls == 0
    assert w.pending_task_count() == 0


def test_c6_unknown_thread_is_zero_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: None)
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)

    out = _drive()
    assert out["results"] == [None], out["results"]  # 無例外、無結果
    assert proxy.calls == []
    assert w.pending_task_count() == 0


@pytest.mark.parametrize("status", ["active", "dormant"])
def test_c7_non_terminal_thread_is_zero_call(
    status: str, monkeypatch: pytest.MonkeyPatch
):
    """M2 終態閘門是**唯一**放行條件：非終態線頭 0 LLM 呼叫（即使 hook 被呼叫）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t, status))
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)

    out = _drive()
    result = _result_of(out)
    assert isinstance(result, ex.ConsolidationResult)
    assert result.status == ex.STATUS_SKIPPED_NOT_TERMINAL, result.reason
    assert result.llm_calls == 0
    assert proxy.calls == []


def test_c8_backfill_failure_does_not_lose_the_fact(monkeypatch: pytest.MonkeyPatch):
    """回填 M1 失敗 ⇒ 只記 warning，fact 仍算成功落地（不得反轉執行層結果）。"""
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))
    proxy = _RecordingProxy()
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: proxy)
    monkeypatch.setattr(w, "_write_fact", lambda fact, agent_id: FACT_ID)

    def boom(a, t, fid):
        raise RuntimeError("m1 write exploded")

    monkeypatch.setattr(w, "_append_dissolved", boom)

    out = _drive()
    assert _result_of(out).status == ex.STATUS_CONSOLIDATED
    assert w.pending_task_count() == 0


def test_c9_dissolve_hook_never_raises_even_if_internals_are_broken(
    monkeypatch: pytest.MonkeyPatch,
):
    """hook 最外層 try/except 的牙齒：`consolidation_enabled` 本身炸掉也不得外溢。"""
    monkeypatch.setattr(w, "consolidation_enabled", _Boom("consolidation_enabled"))
    hook = w.build_dissolve_hook()
    hook(AGENT, THREAD_ID, "completed")  # 不得拋
    assert w.pending_task_count() == 0


# ══════════════════════════════════════════════════════════════
# §D adapter：**恰好 1 次 HTTP 嘗試**（計數 transport）
# ══════════════════════════════════════════════════════════════


class _ProxyShim:
    """最小 proxy 替身：只提供 `generate_text` 真正需要的 `backend` / `model`。

    `generate_text` 直接綁**真實** `LLMProxy.generate_text`（未改寫）⇒ 本測試走的是
    真正的生產方法，只把 HTTP client 換成記憶體 transport。
    """

    generate_text = proxy_mod.LLMProxy.generate_text

    def __init__(self, backend: Any, model: str = "test-model"):
        self.backend = backend
        self.model = model


def _counting_transport(status_code: int = 500) -> Any:
    """記憶體內 transport：計數每次 HTTP 嘗試（**0 真實網路**）。"""
    calls: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status_code, json={"error": "boom"})

    return httpx.MockTransport(handler), calls


def test_d1_adapter_makes_exactly_one_http_attempt_on_retryable_status(
    monkeypatch: pytest.MonkeyPatch,
):
    """🔴 零額外重試的**硬證據**：500（可重試狀態）下 HTTP 嘗試次數 **恰 1**。"""
    transport, calls = _counting_transport(500)
    real_client = httpx.AsyncClient

    def factory(*a, **k):
        k["transport"] = transport
        return real_client(*a, **k)

    monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", factory)

    backend = proxy_mod.OpenAIBackend(api_key="test-key", base_url="http://offline.invalid/v1")
    shim = _ProxyShim(backend)
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: shim)

    llm_call = w._make_llm_call(AGENT)
    with pytest.raises(RuntimeError):
        asyncio.run(llm_call(PROMPT, max_tokens=300))

    assert len(calls) == 1, f"必須恰 1 次 HTTP 嘗試（無重試），實得 {len(calls)}"
    body = json.loads(calls[0].content.decode("utf-8"))
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == 300
    assert "retry" not in body


def test_d2_adapter_passes_zero_retry_to_both_layers(monkeypatch: pytest.MonkeyPatch):
    """adapter 把 `max_retries=0` 傳進 `generate_text`（本層）⇒ 本層恰 1 次嘗試。"""
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", "http://offline.invalid"),
                response=httpx.Response(500),
            )

    shim = _ProxyShim(_Backend())
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: shim)
    monkeypatch.setattr(proxy_mod.asyncio, "sleep", _no_sleep)

    llm_call = w._make_llm_call(AGENT)
    with pytest.raises(RuntimeError):
        asyncio.run(llm_call(PROMPT, max_tokens=300))

    assert len(recorded) == 1, "max_retries=0 ⇒ backend 只能被呼叫 1 次"
    assert recorded[0]["max_retries"] == 0
    assert recorded[0]["reasoning_effort"] == "none"


def test_d3_default_generate_text_still_retries_once(monkeypatch: pytest.MonkeyPatch):
    """對照組：**未傳** `max_retries` 時維持既有行為（本層 2 次嘗試）⇒ 預設路徑不變。"""
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", "http://offline.invalid"),
                response=httpx.Response(500),
            )

    shim = _ProxyShim(_Backend())
    monkeypatch.setattr(proxy_mod.asyncio, "sleep", _no_sleep)

    out = asyncio.run(shim.generate_text(messages=[{"role": "user", "content": "x"}]))
    assert out is None
    assert len(recorded) == 2, "預設（None）⇒ 既有行為＝本層 2 次嘗試"
    assert "max_retries" not in recorded[0], "未傳時不得多送參數（逐位元不變）"
    assert "reasoning_effort" not in recorded[0]


async def _no_sleep(_seconds):
    """把重試退避縮成 0（測試用；不動生產碼）。"""
    return None


def test_d4_missing_proxy_is_an_error_not_a_silent_no_op(monkeypatch: pytest.MonkeyPatch):
    """proxy 未注入 ⇒ adapter raise（讓執行層記 `llm_failed`，而非靜默成功）。"""
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: None)
    llm_call = w._make_llm_call(AGENT)
    with pytest.raises(RuntimeError, match="llm_proxy_not_injected"):
        asyncio.run(llm_call(PROMPT, max_tokens=300))


def test_d5_empty_llm_text_is_an_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: _RecordingProxy(text="   "))
    llm_call = w._make_llm_call(AGENT)
    with pytest.raises(RuntimeError, match="consolidation_llm_returned_no_text"):
        asyncio.run(llm_call(PROMPT, max_tokens=300))


# ══════════════════════════════════════════════════════════════
# §E 逾時治理：外層**不得**短於執行層內層（LIFE-THREAD-M2-WIRING-1-FUP）
# ══════════════════════════════════════════════════════════════
#
# 背景：執行層內層逾時 `life_thread_dissolution_exec.CONSOLIDATION_TIMEOUT_SECONDS = 120`
# 的依據是**實測單次 26,335.6 ms**（`docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md`；
# **20 s 曾被判定為「付了錢才 abort」的錯誤值**，見 `19f8e62`）。**外層若短於內層**
# （例如 10 s），一旦 provider 變慢或 `reasoning_effort` 未被端點支援而回到 ~26 s：
# HTTP 已送出、成本已發生，任務卻被外層砍掉並**丟棄結果**（雙重浪費）。
#
# 本節採**選項 (a)**：接線模組**不自帶**逾時 ⇒ 逾時由執行層**單一治理**。
# 若日後**必須**加外層護欄（例如防任務無限懸掛），其值**必須 ≥** 執行層的
# `CONSOLIDATION_TIMEOUT_SECONDS` 且**自該常數讀取**；屆時**必須改寫本節測試**
# （改成斷言「值 ≥ 執行層逾時」），**不得**直接刪除本節。

#: 受測模組原始碼路徑（AST 掃描；**只讀**，不執行）。
_WIRING_SRC = Path(w.__file__).resolve()

#: 名稱含 timeout（不分大小寫）者視為逾時常數。
_TIMEOUT_NAME_RE = re.compile(r"timeout", re.IGNORECASE)

#: 執行層呼叫點（AST 點狀名）。
_EXEC_CALL_DOTTED = "lt_exec.consolidate_terminal_thread"

#: 會把 awaitable 包上一層逾時的 asyncio API（點狀名）。
_TIMEOUT_WRAPPERS = frozenset({"asyncio.wait_for", "asyncio.timeout"})


def _dotted_name(node: ast.AST) -> str:
    """`lt_exec.consolidate_terminal_thread` 形式的點狀名；非此形狀 ⇒ `""`。"""
    parts: List[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    """子節點 → 父節點（AST 包裹關係斷言用）。"""
    return {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def test_e1_wiring_module_imposes_no_own_timeout_on_exec_call():
    """(a) 接線模組**不得**對執行層呼叫施加自帶逾時（含短包裹）。

    三條硬斷言（皆為**結構性**，不依賴執行時序）：

    1. 執行層呼叫點恰 1 個（呼叫形式未被改寫）。
    2. 它是**裸的** `await lt_exec.consolidate_terminal_thread(...)`：父節點是
       `ast.Await`，且該 `Await` **不得**再被任何 `Call` 包住。
       變異：`await asyncio.wait_for(lt_exec.consolidate_terminal_thread(...), timeout=10.0)`
       ⇒ 斷言 2 紅。
    3. 全模組**不得**出現 `asyncio.wait_for` ／ `asyncio.timeout` 呼叫 ⇒ 變異紅。
    """
    tree = ast.parse(_WIRING_SRC.read_text(encoding="utf-8"))
    parents = _parent_map(tree)

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _dotted_name(node.func) == _EXEC_CALL_DOTTED
    ]
    assert len(calls) == 1, f"執行層呼叫點應恰 1 個，實得 {len(calls)}"
    call = calls[0]

    await_node = parents.get(call)
    assert isinstance(await_node, ast.Await), (
        "執行層呼叫必須直接 `await`（不得被任何 wrapper 包住）："
        f"父節點＝{type(await_node).__name__}"
    )
    outer = parents.get(await_node)
    assert not isinstance(outer, ast.Call), (
        "執行層呼叫的 `await` 不得再被任何呼叫包住（例如外層逾時包裹）："
        f"外層＝{_dotted_name(outer.func) if isinstance(outer, ast.Call) else type(outer).__name__}"
    )

    wrappers = [
        _dotted_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _dotted_name(node.func) in _TIMEOUT_WRAPPERS
    ]
    assert wrappers == [], (
        "接線模組不得自帶逾時包裹（逾時單一事實來源＝執行層內層 "
        f"CONSOLIDATION_TIMEOUT_SECONDS={ex.CONSOLIDATION_TIMEOUT_SECONDS}s）；實得 {wrappers}。"
        "若日後必須加外層護欄，其值必須 ≥ 執行層常數且自該常數讀取（並改寫本節測試）"
    )


def test_e2_no_wiring_timeout_constant_shorter_than_exec_layer():
    """(a) 模組層**不得**存在任何數值型逾時常數 < 執行層內層逾時。

    變異：在接線模組寫回 `_CONSOLIDATION_TIMEOUT_SECONDS = 10.0` ⇒ 本測試紅
    （以 `vars(w)` 掃描**實際載入的模組**，不依賴原始碼字面量）。
    """
    offenders = [
        (name, value)
        for name, value in vars(w).items()
        if _TIMEOUT_NAME_RE.search(name)
        and not isinstance(value, bool)
        and isinstance(value, (int, float))
        and value < ex.CONSOLIDATION_TIMEOUT_SECONDS
    ]
    assert offenders == [], (
        "接線模組自帶逾時常數短於執行層內層逾時"
        f"（{ex.CONSOLIDATION_TIMEOUT_SECONDS}s）：{offenders}"
    )
    # 對照：單一事實來源本身（執行層，本票凍結）——必須容得下實測 26,335.6 ms。
    assert ex.CONSOLIDATION_TIMEOUT_SECONDS >= 26.34


class _NoWaitForAsyncio:
    """`asyncio` 的**侷限替身**：轉發一切，但 `wait_for`／`timeout` 一被呼叫即 raise。

    只注入**本模組的命名空間**（`monkeypatch.setattr(w, "asyncio", ...)`）⇒ 不影響
    執行層／pytest 自身；且 raise 發生在真的等待之前 ⇒ 測試 0 延遲、0 網路、0 真實請求。
    """

    def __getattr__(self, name: str) -> Any:
        if name in ("wait_for", "timeout"):

            def _boom(*_a: Any, **_k: Any) -> Any:
                raise AssertionError(
                    f"接線模組不得用 asyncio.{name} 包裹執行層呼叫"
                    "（外層逾時會短於執行層內層逾時 ⇒ 錢花了、結果被丟棄）"
                )

            return _boom
        return getattr(asyncio, name)


def test_e3_exec_path_never_calls_asyncio_wait_for(monkeypatch: pytest.MonkeyPatch):
    """(a) **執行期牙齒**：flag ON 走完整沉澱路徑時，0 次 `asyncio.wait_for`／`timeout`。

    變異（把外層逾時改回 10 s ⇒ 以 `asyncio.wait_for` 包裹執行層呼叫）⇒ tripwire 觸發
    ⇒ 例外物件成為任務結果 ⇒ 本測試紅。正常路徑（無包裹）⇒ 綠。
    """
    monkeypatch.setenv(w.CONSOLIDATION_ENABLED_ENV, "1")
    monkeypatch.setattr(w, "_read_thread_state", lambda a, t: _terminal_thread(t))
    monkeypatch.setattr(w, "_resolve_llm_proxy", lambda: _RecordingProxy())
    monkeypatch.setattr(w, "_write_fact", lambda fact, agent_id: FACT_ID)
    monkeypatch.setattr(w, "_append_dissolved", lambda a, t, fid: True)
    monkeypatch.setattr(w, "asyncio", _NoWaitForAsyncio())

    out = _drive()
    result = _result_of(out)
    assert not isinstance(result, BaseException), f"逾時包裹使沉澱路徑失敗：{result!r}"
    assert result.status == ex.STATUS_CONSOLIDATED
    assert result.llm_calls == 1
