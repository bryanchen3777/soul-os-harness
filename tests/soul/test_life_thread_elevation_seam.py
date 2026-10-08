# tests/soul/test_life_thread_elevation_seam.py
# LIFE-THREAD-W3 — 生活線頭→昇華接縫（門檻二接縫）驗收矩陣。
#
# 受測模組：`src/soul/life_thread_orchestrator.py`（唯一的改動面）
# 凍結面（**本檔只讀、0 改動**）：`src/inner_life/**`、`life_thread_wake_gate.py`、
# `life_thread_dissolution_exec.py`
#
# 範式沿用 `tests/soul/test_life_thread_whim_origin.py`：資料根隔離 fixture
# （`SOUL_OS_DATA_DIR` ＋ `reset_data_root`）／spy 包裝真函式計數／AST 凍結面守門。
#
# 🔴 全部測試使用**合成資料與假時鐘**（固定 ISO 時刻），不依賴生產資料／真實時間。
# 🔴 LLM **一律用 stub**（`llm_caller=`）—— 本檔 0 真實 LLM、0 網路、0 伺服器、
#    0 生產埠請求。昇華 consume 內若需 LLM，一律走既有確定性 stub（`llm=None`）。
# 🔴 本檔**不讀寫生產 `data/**`**：所有落地檔都在隔離的 tmp 內（見末項快照測試）。
# 🔴 INV-7（留白是成功）：本檔**沒有**任何「線頭數 > 0」形式的 assert。
from __future__ import annotations

import ast
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_orchestrator as orch  # noqa: E402
from src.soul import life_threads as lt  # noqa: E402
from src.timezone_utils import LOCAL_TZ  # noqa: E402

AGENT = "agent_elev"
SYNTH_FACT_ID = "fact-synth-w3-001"

ORCHESTRATOR_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_orchestrator.py"
INNER_LIFE_EVENT_PATH = _REPO_ROOT / "src" / "inner_life" / "event.py"


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
    """每個測試前後清空行程內狀態，並把旗標**釘成 OFF**。

    - `orch.reset_state()`：既有 at-most-once 冪等鍵（既有慣例）。
    - `orch._ELEVATION_EMITTED.clear()`：§4.6 已發射集合（同 `_WHIM_DAILY` 前例，
      直接清記憶體 dict，不擴大 `reset_state()` 契約面）。
    - `monkeypatch.setenv(ELEVATION_ENABLED_ENV, "")`：**釘成空字串**（不是 delenv）——
      與 whim 測試同一地雷：`load_dotenv(..., override=False)` 會在「變數不存在」時
      把生產 `.env` 的值補回 pytest 行程。`""` 明確 OFF 且免疫補回。
    """
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "")
    orch.reset_state()
    orch._ELEVATION_EMITTED.clear()
    yield
    orch.reset_state()
    orch._ELEVATION_EMITTED.clear()


def _loc(hour: int = 8, day_delta: int = 0) -> datetime:
    """本地時區固定時刻（8:00 → morning、22:00 → night；假時鐘）。"""
    return datetime(2026, 10, 2, hour, tzinfo=LOCAL_TZ) + timedelta(days=day_delta)


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


def _quiet_llm() -> _SpyLLM:
    """回空 actions 的 stub（可觀測「被呼叫了幾次」，但 0 寫入）。"""
    import json

    return _SpyLLM(json.dumps({"actions": []}, ensure_ascii=False))


def _seed_active(agent_id: str, *, now: datetime) -> str:
    """一條檢驗點在未來的 active 線頭（M3 必判 SLEEP，0 喚醒）。"""
    future = (now + timedelta(hours=6)).astimezone(LOCAL_TZ).isoformat()
    tid = lt.create_thread(
        agent_id, "未竟之事", "她還在為那盆薄荷想辦法。",
        "necessity_driven", check_after_ts=future,
        created_at="2026-10-01T08:00:00+00:00",
    )
    assert tid, "fixture 建 active 線頭失敗"
    return tid


def _seed_completed_with_fact(agent_id: str, *, fact_id: str = SYNTH_FACT_ID) -> str:
    """一條 completed＋有 `sage_fact_id` 的線頭（合成溶解事實，0 LLM／0 SAGE）。"""
    tid = lt.create_thread(
        agent_id, "救回薄荷", "她把陽台那盆枯掉的薄荷救回來了。",
        "necessity_driven",
        check_after_ts="2026-10-01T08:00:00+00:00",
        created_at="2026-10-01T08:00:00+00:00",
    )
    assert tid, "fixture 建線頭失敗"
    assert lt.append_transition(agent_id, tid, "completed") is True
    assert lt.append_dissolved(
        agent_id, tid,
        dissolved_at="2026-10-01T12:00:00+00:00",
        sage_fact_id=fact_id,
    ) is True
    state = lt.get_state(agent_id, tid)
    assert state is not None and state.get("status") == "completed"
    assert state.get("sage_fact_id") == fact_id
    return tid


def _seed_abandoned_with_fact(agent_id: str, *, fact_id: str = SYNTH_FACT_ID) -> str:
    """一條 abandoned＋有 `sage_fact_id` 的線頭（必須被接縫排除）。"""
    tid = lt.create_thread(
        agent_id, "放棄的念頭", "那件事她決定不再追了。",
        "necessity_driven",
        check_after_ts="2026-10-01T08:00:00+00:00",
        created_at="2026-10-01T08:00:00+00:00",
    )
    assert tid, "fixture 建線頭失敗"
    assert lt.append_transition(agent_id, tid, "abandoned") is True
    assert lt.append_dissolved(
        agent_id, tid,
        dissolved_at="2026-10-01T12:00:00+00:00",
        sage_fact_id=fact_id,
    ) is True
    return tid


def _spy_writer_create(monkeypatch) -> List[Dict[str, Any]]:
    """攔 `InnerLifeWriter.create_event`：記錄 kwargs 與回傳事件（仍呼叫真函式）。"""
    from src.inner_life.writer import InnerLifeWriter

    calls: List[Dict[str, Any]] = []
    real = InnerLifeWriter.create_event

    def spy(self, **kwargs):
        event = real(self, **kwargs)
        calls.append({"kwargs": kwargs, "event": event})
        return event

    monkeypatch.setattr(InnerLifeWriter, "create_event", spy)
    return calls


def _spy_gate_submit(monkeypatch) -> List[Dict[str, Any]]:
    """攔 `SubmissionGate.submit`：記錄 event_id 與 agent_id（仍呼叫真函式）。"""
    from src.inner_life.submission_gate import SubmissionGate

    calls: List[Dict[str, Any]] = []
    real = SubmissionGate.submit

    def spy(self, event_id, memory_facts=(), **kwargs):
        result = real(self, event_id, memory_facts, **kwargs)
        calls.append(
            {"event_id": event_id, "agent_id": kwargs.get("agent_id"),
             "result": result}
        )
        return result

    monkeypatch.setattr(SubmissionGate, "submit", spy)
    return calls


# ══════════════════════════════════════════════════════════════
# 接縫行為（工單測試清單 10 項）
# ══════════════════════════════════════════════════════════════


def test_flag_off_is_noop(iso_env, monkeypatch):
    """旗標缺席／OFF ⇒ 建立事件 0 次、submit 0 次、**0 LLM 呼叫**。

    OFF token 逐一釘死（含 `off`／`no`／`disabled` 等 fail-open 陷阱值），
    真值解析**必須**是 `lt_wiring.TRUTHY_VALUES` 白名單（缺席即 OFF）。
    """
    from src.soul import life_thread_consolidation_wiring as lt_wiring

    assert orch.ELEVATION_ENABLED_ENV == "LIFE_THREAD_ELEVATION_ENABLED"
    assert lt_wiring.TRUTHY_VALUES == frozenset({"1", "true", "yes", "on"})

    llm = _quiet_llm()
    writer_calls = _spy_writer_create(monkeypatch)
    submit_calls = _spy_gate_submit(monkeypatch)

    # OFF 站點禁用 delenv（TEST-INFRA-ENV-ISOLATION-1）：管線內 load_config()
    # 執行 load_dotenv(env_path, override=False)，delenv 後會把生產 `.env` 值
    # 補回行程；故用空字串表達 OFF（空字串本就在下方 OFF token 清單內）。
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "")
    assert orch.elevation_enabled() is False, "空字串 ⇒ OFF"
    agent_absent = "agent_elev_off_absent"
    _seed_completed_with_fact(agent_absent)
    out = _run([agent_absent], MORNING, "morning", llm_caller=llm)[agent_absent]
    assert writer_calls == [] and submit_calls == [] and llm.calls == []
    assert "elevation_event_created" not in out

    # OFF token 逐一 ⇒ OFF（每 token 用獨立 agent，避開 at-most-once／已發射集合）。
    for i, off in enumerate(("", "0", "false", "FALSE", " False ", "  ",
                              "off", "OFF", "no", "NO", "disabled", "maybe",
                              "0.0", "2")):
        monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, off)
        assert orch.elevation_enabled() is False, f"{off!r} ⇒ 必須 OFF"
        agent = f"agent_elev_off_{i}"
        _seed_completed_with_fact(agent)
        out = _run([agent], MORNING, "morning", llm_caller=llm)[agent]
        assert "elevation_event_created" not in out, f"{off!r} 不得建事件：{out}"

    assert writer_calls == [], f"旗標 OFF ⇒ 建立事件 0 次：{writer_calls}"
    assert submit_calls == [], f"旗標 OFF ⇒ submit 0 次：{submit_calls}"
    assert llm.calls == [], f"旗標 OFF ⇒ 0 LLM 呼叫：{llm.calls}"


def test_completed_thread_with_fact_creates_event(iso_env, monkeypatch):
    """`completed`＋有 `sage_fact_id` ⇒ 建立 1 個事件（經 Writer＋Gate）。"""
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    assert orch.elevation_enabled() is True, "前置：本測試必須在旗標 ON 下執行"
    _seed_completed_with_fact(AGENT)
    writer_calls = _spy_writer_create(monkeypatch)
    submit_calls = _spy_gate_submit(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)[AGENT]

    assert len(writer_calls) == 1, f"恰建 1 事件：{writer_calls}"
    provenance = writer_calls[0]["kwargs"]["provenance"]
    assert provenance.trigger_type == "diary:morning", provenance
    assert provenance.trigger_type != "system", "嚴禁以 SYSTEM 敷衍"
    assert provenance.actor_id == AGENT, provenance
    assert writer_calls[0]["kwargs"]["ts"].endswith(("+00:00", "Z")), "ts 須為 UTC"
    assert len(submit_calls) == 1, f"恰 submit 1 次：{submit_calls}"
    assert submit_calls[0]["event_id"] == writer_calls[0]["event"].event_id
    assert submit_calls[0]["agent_id"] == AGENT
    assert out.get("elevation_event_created") is True, out


def test_active_thread_creates_nothing(iso_env, monkeypatch):
    """`active` 線頭（還在進行中，不是經歷）⇒ 0 事件、0 submit。"""
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_active(AGENT, now=MORNING)
    writer_calls = _spy_writer_create(monkeypatch)
    submit_calls = _spy_gate_submit(monkeypatch)
    llm = _quiet_llm()

    out = _run([AGENT], MORNING, "morning", llm_caller=llm)[AGENT]

    assert writer_calls == [], writer_calls
    assert submit_calls == [], submit_calls
    assert llm.calls == [], "未到期 active 線頭 ⇒ M3 安靜 ⇒ 0 LLM"
    assert "elevation_event_created" not in out, out


def test_abandoned_thread_creates_nothing(iso_env, monkeypatch):
    """`abandoned` 線頭（被放棄，不是經歷）⇒ 0 事件、0 submit（即使有 fact）。"""
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_abandoned_with_fact(AGENT)
    writer_calls = _spy_writer_create(monkeypatch)
    submit_calls = _spy_gate_submit(monkeypatch)

    out = _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())[AGENT]

    assert writer_calls == [], writer_calls
    assert submit_calls == [], submit_calls
    assert "elevation_event_created" not in out, out


def test_no_new_trigger_type_constant(iso_env, monkeypatch):
    """事件 trigger_type ∈ `VALID_PRODUCER_TRIGGER_TYPES`；`event.py` 0 改動。

    - orchestrator **不得定義**任何 `TRIGGER_TYPE_*` 常數（AST）。
    - morning／night slot 分別映射到既有 diary 兩常數（行為）。
    - `src/inner_life/event.py` 無本票接線痕跡（文字）。
    """
    from src.inner_life.submission_gate import VALID_PRODUCER_TRIGGER_TYPES

    tree = ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8"))
    defined = {
        n.targets[0].id
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id.startswith("TRIGGER_TYPE_")
    }
    assert defined == set(), f"不得新增 trigger 常數：{defined}"

    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    writer_calls = _spy_writer_create(monkeypatch)
    _spy_gate_submit(monkeypatch)

    _seed_completed_with_fact("agent_elev_trig_m")
    _run(["agent_elev_trig_m"], MORNING, "morning", llm_caller=_quiet_llm())
    _seed_completed_with_fact("agent_elev_trig_n")
    _run(["agent_elev_trig_n"], NIGHT, "night", llm_caller=_quiet_llm())

    assert len(writer_calls) == 2, writer_calls
    triggers = [c["kwargs"]["provenance"].trigger_type for c in writer_calls]
    assert triggers == ["diary:morning", "diary:night"], triggers
    assert set(triggers) <= set(VALID_PRODUCER_TRIGGER_TYPES), VALID_PRODUCER_TRIGGER_TYPES

    event_source = INNER_LIFE_EVENT_PATH.read_text(encoding="utf-8")
    for trace in ("life_thread", "LIFE_THREAD", "elevation_event", "sage_fact_id"):
        assert trace not in event_source, f"event.py 出現接線痕跡：{trace}"


def test_goes_through_submission_gate(iso_env, monkeypatch):
    """呼叫點必須是 `submission_gate.submit`，**不得**直接呼叫消耗鏈（AST＋mock）。

    - 原文：orchestrator 源碼含 `submission_gate.submit(`。
    - AST：全檔無任何對消耗鏈的直接呼叫。
    - mock：submit 實被呼叫 1 次（路徑真的經過 Gate）。
    """
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "submission_gate.submit(" in source, "呼叫點必須是 submission_gate.submit"
    tree = ast.parse(source)
    called = {
        (n.func.id if isinstance(n.func, ast.Name)
         else n.func.attr if isinstance(n.func, ast.Attribute) else "")
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "run_elevation" not in called, "嚴禁直接呼叫消耗鏈（須走 Gate）"

    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_completed_with_fact(AGENT)
    submit_calls = _spy_gate_submit(monkeypatch)
    _spy_writer_create(monkeypatch)

    _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())

    assert len(submit_calls) == 1, f"必須經 submission_gate.submit：{submit_calls}"


def test_never_calls_elevate(iso_env, monkeypatch):
    """`elevate()` 呼叫次數 == 0（契約明定只 consume 的 AST 紅線）。"""
    source = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "elevate(" not in source, "源碼不得出現 elevate( 呼叫"
    tree = ast.parse(source)
    called = {
        (n.func.id if isinstance(n.func, ast.Name)
         else n.func.attr if isinstance(n.func, ast.Attribute) else "")
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "elevate" not in called, f"AST 紅線：{sorted(called)}"

    # 運行時：submit 被呼叫（consume 路徑）即足；本票不觸碰任何 elevate 符號。
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_completed_with_fact(AGENT)
    submit_calls = _spy_gate_submit(monkeypatch)
    _spy_writer_create(monkeypatch)
    _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())
    assert len(submit_calls) == 1, submit_calls
    assert source.count("InnerLifeWriter") >= 1, "必須走 InnerLifeWriter"


def test_failure_is_fail_silent(iso_env, monkeypatch, caplog):
    """`submit()` 拋例外 ⇒ 記 warning、正常返回、**不 raise**、無該鍵。"""
    from src.inner_life.submission_gate import SubmissionGate

    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_completed_with_fact(AGENT)
    _spy_writer_create(monkeypatch)

    def boom(self, event_id, memory_facts=(), **kwargs):
        raise RuntimeError("boom (simulated gate failure)")

    monkeypatch.setattr(SubmissionGate, "submit", boom)

    with caplog.at_level("WARNING"):
        out = _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())[AGENT]

    assert out.get("woke") is False, out
    assert "elevation_event_created" not in out, f"失敗 ⇒ 不得出現該鍵：{out}"
    assert any("elevation" in rec.message for rec in caplog.records), [
        r.message for r in caplog.records
    ]


def test_no_new_timer_no_new_llm_channel():
    """INV-2（0 新定時器）／0 新 LLM 通道／INV-5（0 數值打分）在改動面成立。

    以 **AST 識別子**判定（不是文字比對）：模組 docstring 會逐字引用
    `asyncio.sleep` 等作為「不得新增」的說明，文字掃描會誤判。
    另釘死：本票四個新函式體內**無任何 `llm` 識別子**（0 新 LLM 通道）。
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

    new_funcs = {
        "elevation_enabled", "_elevation_trigger_for_slot",
        "maybe_emit_elevation_events", "_elevation_provenance_module",
    }
    found = {
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert new_funcs <= found, f"新函式缺失：{new_funcs - found}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in new_funcs:
            sub = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert "llm" not in sub and "llm_caller" not in sub, (
                f"{node.name} 不得觸碰 LLM：{sorted(sub)}"
            )


def test_does_not_mutate_data_dir(iso_env, monkeypatch):
    """執行前後 repo `data/**` 檔案清單與 mtime／size 逐位元一致。"""
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    _seed_completed_with_fact(AGENT)
    _spy_writer_create(monkeypatch)
    _spy_gate_submit(monkeypatch)

    def _snapshot() -> Dict[str, tuple]:
        data_dir = _REPO_ROOT / "data"
        snap: Dict[str, tuple] = {}
        if data_dir.is_dir():
            for path in sorted(data_dir.rglob("*")):
                if path.is_file():
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    snap[str(path.relative_to(data_dir))] = (st.st_size, st.st_mtime_ns)
        return snap

    before = _snapshot()
    assert len(before) > 0, "前置：repo data/** 應非空，否則快照無意義"
    out = _run([AGENT], MORNING, "morning", llm_caller=_quiet_llm())[AGENT]
    after = _snapshot()

    assert out.get("elevation_event_created") is True, out
    assert after == before, (
        "data/** 被改動："
        f"{sorted(set(after) ^ set(before))[:10]}"
    )
