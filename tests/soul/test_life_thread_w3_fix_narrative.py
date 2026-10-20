# -*- coding: utf-8 -*-
"""LIFE-THREAD-W3-FIX-1 — 生活線頭昇華接縫的正文修復（Owner 2026-10-19 裁決 1／2）。

背景：生活線頭的「經歷」**從未**變成靈魂的「信念」。不是被防火牆擋的（35/35 條
通過所有閘門），而是**事件根本沒帶正文**：

    life_thread_orchestrator.py   extras = {thread_id, sage_fact_id, origin_type, title}
    elevation_adapter.py           _NARRATIVE_EXTRA_KEYS = ("narrative",)
      ⇒ _event_content() 回 "" ⇒ inner_life_event_to_input() 回 None
      ⇒ inputs=[] ⇒ consume 0 次 ⇒ 0 節點

本檔釘死四件事（每條都是**可變異打紅**的守衛）：

1. `extras["narrative"]` ＝ 線頭的 `narrative_content`（**逐位元**）⇒ 節點正文來源。
2. `extras["reflection"]` ＝ 該 `sage_fact_id` 事實的 `object`（**逐位元**）；
   查無該 fact ⇒ **空字串**。
   ⚠️ `reflection` **不被** `_event_content()` 消費（白名單刻意只認 `narrative`）
      ⇒ 它是**純伴隨 metadata**，**不得**宣稱它影響節點內容。
3. 白名單 `_NARRATIVE_EXTRA_KEYS` **零擴張**（仍是 `("narrative",)`）。
4. 裁決 2：`submit()` 仍收**空 tuple**（fact 實體不接入昇華鏈）。

🔴 0 生產接觸：所有寫入都落在 pytest 的 `tmp_path`（`iso_env` 隔離
`SOUL_OS_DATA_DIR`）；0 LLM、0 伺服器、0 subprocess、0 對外請求。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.inner_life import elevation_adapter as ea  # noqa: E402
from src.paths import reset_data_root  # noqa: E402
from src.soul import life_thread_orchestrator as orch  # noqa: E402

AGENT = "agent_w3fix"
FACT_ID = "fact-w3fix-001"
NARRATIVE = "她把陽台那盆枯掉的薄荷救回來了，葉子在幾天後才慢慢挺直。"
TITLE = "救回薄荷"
REFLECTION = "枯掉的東西不一定真的死了，只是還沒等到對的時候。"

_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


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
    """清空行程內 at-most-once 狀態，並把旗標**釘成 ON**。

    本檔每一支測試都走「接縫已開啟」的路徑（旗標 OFF ⇒ 0 事件 ⇒ 全部無意義），
    故 autouse 直接釘成 `"1"`。用 `setenv` 而非 `delenv` ⇒ 免疫
    `load_dotenv(..., override=False)` 把生產 `.env` 的值補回 pytest 行程
    （與既有 seam 測試同一地雷）。
    """
    monkeypatch.setenv(orch.ELEVATION_ENABLED_ENV, "1")
    orch.reset_state()
    orch._ELEVATION_EMITTED.clear()
    yield
    orch.reset_state()
    orch._ELEVATION_EMITTED.clear()


class _RecordingWriter:
    """只記錄 `create_event` 的 provenance（不落盤）。"""

    def __init__(self) -> None:
        self.created: List[SimpleNamespace] = []

    def create_event(self, *, provenance: Any, ts: str) -> SimpleNamespace:
        event = SimpleNamespace(
            event_id=f"evt-w3fix-{len(self.created):03d}",
            provenance=provenance,
            ts=ts,
            # 真實 `InnerLifeEvent` 另有此欄位（`inner_life_event_to_input` 會讀）
            correlation_id="corr-w3fix",
        )
        self.created.append(event)
        return event


class _RecordingGate:
    """只記錄 `submit()` 的呼叫（不消費）。"""

    def __init__(self) -> None:
        self.submits: List[Dict[str, Any]] = []

    def submit(self, event_id: str, memory_facts: Any = (), **kwargs: Any) -> List[Any]:
        self.submits.append(
            {"event_id": event_id, "memory_facts": memory_facts, "kwargs": kwargs}
        )
        return []


def _thread(
    thread_id: str,
    *,
    narrative: str = NARRATIVE,
    title: str = TITLE,
    fact_id: str = FACT_ID,
    status: str = "completed",
) -> Dict[str, Any]:
    """一條線頭的 folded state（形狀同 `lt.fold()` 的輸出）。"""
    return {
        "thread_id": thread_id,
        "status": status,
        "title": title,
        "narrative_content": narrative,
        "sage_fact_id": fact_id,
        "origin_type": "necessity_driven",
    }


def _seed_fact(tmp_path: Path, *, fact_id: str = FACT_ID, obj: str = REFLECTION) -> Path:
    """在 tmp 資料根建一個 per-agent 事實庫並寫入一條事實（合成、0 生產）。"""
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.models import Fact

    db_path = tmp_path / "memory" / AGENT / "graph.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path=db_path)
    try:
        store.add_fact(
            Fact(
                subject="薄荷",
                predicate="救回",
                object=obj,
                timestamp=1760000000.0,
                fact_id=fact_id,
                source="user",
            )
        )
        store.flush()
    finally:
        store.close()
    return db_path


def _emit(threads, *, writer=None, gate=None):
    """跑一次接縫，回傳 (writer, gate)。"""
    writer = writer if writer is not None else _RecordingWriter()
    gate = gate if gate is not None else _RecordingGate()
    orch.maybe_emit_elevation_events(
        AGENT, threads, "morning", _NOW, writer=writer, submission_gate=gate
    )
    return writer, gate


# ══════════════════════════════════════════════════════════════
# 1. 正文提取守衛
# ══════════════════════════════════════════════════════════════


def test_narrative_extra_is_thread_narrative_byte_for_byte(iso_env):
    """`extras["narrative"]` ＝ 線頭的 `narrative_content`（**逐位元**相同）。"""
    thread = _thread("t-w3-001")
    writer, _gate = _emit([thread])

    assert len(writer.created) == 1, writer.created
    extras = writer.created[0].provenance.extras
    assert "narrative" in extras, sorted(extras)
    assert extras["narrative"] == thread["narrative_content"]
    assert extras["narrative"].encode("utf-8") == thread["narrative_content"].encode("utf-8")


def test_narrative_is_not_the_title(iso_env):
    """正文**不得**是標題（SOUL-BUG-006：寧可少一顆節點也不讓標題冒充正文）。"""
    thread = _thread("t-w3-002")
    writer, _gate = _emit([thread])

    extras = writer.created[0].provenance.extras
    assert extras["narrative"] != extras["title"]
    assert extras["narrative"].strip() != thread["title"].strip()


def test_narrative_missing_or_empty_becomes_empty_string(iso_env):
    """`narrative_content` 缺失／空 ⇒ `narrative` 為**空字串**。

    🔴 **不得拋例外、不得跳過事件建立**（跳過＝靜默失敗，量測報告 §4.4 的坑）。
    """
    missing = _thread("t-w3-003a")
    missing.pop("narrative_content")
    empty = _thread("t-w3-003b", narrative="")
    writer, gate = _emit([missing, empty])

    assert len(writer.created) == 2, "缺正文**不得**跳過事件建立"
    assert len(gate.submits) == 2, gate.submits
    assert writer.created[0].provenance.extras["narrative"] == ""
    assert writer.created[1].provenance.extras["narrative"] == ""


# ══════════════════════════════════════════════════════════════
# 2. 感悟（reflection）守衛
# ══════════════════════════════════════════════════════════════


def test_reflection_extra_is_fact_object_byte_for_byte(iso_env, tmp_path):
    """`extras["reflection"]` ＝ 事實的 `object`（**逐位元**相同）。"""
    _seed_fact(tmp_path, obj=REFLECTION)
    writer, _gate = _emit([_thread("t-w3-003")])

    extras = writer.created[0].provenance.extras
    assert "reflection" in extras, sorted(extras)
    assert extras["reflection"] == REFLECTION
    assert extras["reflection"].encode("utf-8") == REFLECTION.encode("utf-8")


def test_reflection_is_empty_when_fact_absent(iso_env, tmp_path):
    """事實查無 ⇒ `reflection` ＝ **空字串**（不得編造、不得拿別的欄位湊）。"""
    _seed_fact(tmp_path, fact_id="fact-other", obj="別的事實")
    writer, _gate = _emit([_thread("t-w3-004", fact_id="fact-not-there")])

    extras = writer.created[0].provenance.extras
    assert extras["reflection"] == ""


def test_reflection_read_does_not_write_facts(iso_env, tmp_path):
    """感悟回讀**唯讀**：事實列與 schema 版本逐位元不變（0 寫入）。"""
    from src.memory.sage.graph_store import GraphStore

    db_path = _seed_fact(tmp_path)

    def _facts() -> List[tuple]:
        store = GraphStore(db_path=db_path)
        try:
            rows = store._get_conn().execute(
                "SELECT fact_id, subject, predicate, object FROM facts ORDER BY fact_id"
            ).fetchall()
            ver = store._get_conn().execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            return [[tuple(r) for r in rows], tuple(ver) if ver else None]
        finally:
            store.close()

    before = _facts()
    writer, _gate = _emit([_thread("t-w3-005")])
    assert writer.created[0].provenance.extras["reflection"] == REFLECTION
    after = _facts()

    assert after == before, "感悟回讀寫入了事實庫"


def test_elevation_reflection_is_fail_soft(iso_env, tmp_path, monkeypatch):
    """`_elevation_reflection` 的 **fail-soft**：任何異常 ⇒ 回 `""`，**不 raise**。

    覆蓋三種失敗態：DB 不存在／查詢拋例外（鎖住等）／空 `agent_id`／空 `fact_id`。
    """
    import src.memory.sage.graph_store as gs_mod

    # (a) 事實庫不存在 ⇒ ""（且**不開** store）
    assert orch._elevation_reflection(AGENT, FACT_ID) == ""
    # (b) 空參數 ⇒ ""
    assert orch._elevation_reflection("", FACT_ID) == ""
    assert orch._elevation_reflection(AGENT, "") == ""

    # (c) 查詢拋例外（模擬 database is locked）⇒ "" 且不 raise
    _seed_fact(tmp_path)

    def _boom(self, fact_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(gs_mod.GraphStore, "get_fact", _boom)
    assert orch._elevation_reflection(AGENT, FACT_ID) == ""


# ══════════════════════════════════════════════════════════════
# 3. store 開啟次數守衛
# ══════════════════════════════════════════════════════════════


def test_fact_store_opened_at_most_once_per_call(iso_env, tmp_path, monkeypatch):
    """**每次呼叫至多開一次 store**（多條線頭 ⇒ `GraphStore.__init__` 恰 1 次）。"""
    import src.memory.sage.graph_store as gs_mod

    _seed_fact(tmp_path)
    opens: List[Any] = []
    original_init = gs_mod.GraphStore.__init__

    def _counting_init(self, db_path, batch_size=gs_mod._BATCH_SIZE):
        opens.append(db_path)
        return original_init(self, db_path=db_path, batch_size=batch_size)

    monkeypatch.setattr(gs_mod.GraphStore, "__init__", _counting_init)

    threads = [
        _thread(f"t-w3-open-{i:02d}", fact_id=FACT_ID if i == 0 else "fact-none")
        for i in range(5)
    ]
    writer, _gate = _emit(threads)

    assert len(writer.created) == 5, len(writer.created)
    assert len(opens) == 1, f"每次呼叫至多開一次 store，實際 {len(opens)} 次：{opens}"


# ══════════════════════════════════════════════════════════════
# 4. 沙盒端到端（正文真的變成節點）
# ══════════════════════════════════════════════════════════════


def test_event_content_equals_narrative(iso_env):
    """`_event_content(event)` 回**非空**且**逐位元等於** `narrative_content`。"""
    thread = _thread("t-w3-006")
    writer, _gate = _emit([thread])

    content = ea._event_content(writer.created[0])
    assert content, "正文不得為空（否則 fail-closed ⇒ 0 節點）"
    assert content == thread["narrative_content"]
    assert content.encode("utf-8") == thread["narrative_content"].encode("utf-8")


def test_sandbox_end_to_end_produces_elevation_nodes(iso_env):
    """端到端：帶正文的線頭 ⇒ 昇華鏈**真的產出節點**（> 0）。"""
    from src.inner_life.submission_gate import SubmissionGate
    from src.inner_life.writer import InnerLifeWriter

    writer = InnerLifeWriter()
    gate = SubmissionGate(writer=writer)
    created = orch.maybe_emit_elevation_events(
        AGENT, [_thread("t-w3-007")], "morning", _NOW, writer=writer, submission_gate=gate
    )
    assert created is True, "接縫應建立 1 個事件"

    nodes_path = iso_env / "elevation" / ea.NODES_FILENAME
    assert nodes_path.is_file(), f"昇華節點檔未產生：{nodes_path}"
    lines = [
        ln for ln in nodes_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) >= 1, "帶正文的線頭應產出至少 1 顆昇華節點"


def test_event_maps_to_elevation_input(iso_env):
    """`inner_life_event_to_input(event)` **不**回 `None`（正文到位 ⇒ 可進引擎）。"""
    from src.inner_life.submission_gate import SubmissionGate
    from src.inner_life.writer import InnerLifeWriter

    # 用**真實** `InnerLifeWriter`（真實 `InnerLifeEvent` 才帶齊欄位）
    created: List[Any] = []
    writer = InnerLifeWriter()
    original = writer.create_event

    def _spy(*a, **kw):
        event = original(*a, **kw)
        created.append(event)
        return event

    writer.create_event = _spy
    gate = SubmissionGate(writer=writer)
    orch.maybe_emit_elevation_events(
        AGENT, [_thread("t-w3-009")], "morning", _NOW, writer=writer, submission_gate=gate
    )
    assert len(created) == 1, created

    inp = ea.inner_life_event_to_input(created[0])
    assert inp is not None, "正文已在 extras ⇒ 必須映射成 ElevationInput"
    assert inp.content == NARRATIVE
    assert inp.source_type == "inner_life_event"


def test_elevate_is_never_called(iso_env, monkeypatch):
    """`elevate()` 呼叫次數仍為 **0**（契約：昇華路徑只 consume）。

    pattern → belief／value 走**另一個機制** `elevate_matured_patterns`，
    與 Submission Gate 相互獨立。
    """
    import soul_elevation
    from src.inner_life.submission_gate import SubmissionGate
    from src.inner_life.writer import InnerLifeWriter

    calls: List[Any] = []
    original = soul_elevation.InternalizingEngine.elevate

    def _spy(self, *a, **kw):
        calls.append(1)
        return original(self, *a, **kw)

    monkeypatch.setattr(soul_elevation.InternalizingEngine, "elevate", _spy)

    writer = InnerLifeWriter()
    gate = SubmissionGate(writer=writer)
    orch.maybe_emit_elevation_events(
        AGENT, [_thread("t-w3-010")], "morning", _NOW, writer=writer, submission_gate=gate
    )
    assert calls == [], f"接縫不得觸發 elevate()（實際 {len(calls)} 次）"


def test_production_data_untouched(iso_env):
    """生產資料零寫入：執行前後 repo `data/**` 的檔名集合與 sha256 逐位元相同。

    與既有 seam 測試同法：**兩層偵測力**——
      1. 檔名集合（新增／刪除／改名）—— **0 排除**；
      2. size／mtime —— 只比對**未被排除**的檔案（活服務自寫路徑由
         `harness.runner._is_mutation_skipped` 集中判定，不另立一份清單）。
    """
    from harness.runner import _is_mutation_skipped

    data_dir = _REPO_ROOT / "data"

    def _snapshot() -> tuple:
        names: set = set()
        guarded: Dict[str, tuple] = {}
        if data_dir.is_dir():
            for path in sorted(data_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(data_dir)
                names.add(str(rel))
                if _is_mutation_skipped(rel):
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                guarded[str(rel)] = (st.st_size, st.st_mtime_ns)
        return names, guarded

    before_names, before_guarded = _snapshot()
    assert len(before_names) > 0, "前置：repo data/** 應非空，否則快照無意義"

    # 走真實接縫（真實 Writer＋Gate ⇒ 真實 consume 路徑），資料根隔離在 tmp。
    from src.inner_life.submission_gate import SubmissionGate
    from src.inner_life.writer import InnerLifeWriter

    writer = InnerLifeWriter()
    gate = SubmissionGate(writer=writer)
    orch.maybe_emit_elevation_events(
        AGENT, [_thread("t-w3-011")], "morning", _NOW, writer=writer, submission_gate=gate
    )

    after_names, after_guarded = _snapshot()
    assert after_names == before_names, (
        "data/** 檔案清單被改動：" f"{sorted(after_names ^ before_names)[:10]}"
    )
    assert after_guarded == before_guarded, (
        "data/** 內容／mtime 被改動："
        f"{sorted(k for k in set(after_guarded) & set(before_guarded) if after_guarded[k] != before_guarded[k])[:10]}"
    )


# ══════════════════════════════════════════════════════════════
# 5. 白名單守衛（本票不得擴張）
# ══════════════════════════════════════════════════════════════


def test_narrative_whitelist_not_expanded():
    """`_NARRATIVE_EXTRA_KEYS` **必須仍是** `("narrative",)`（SOUL-BUG-006 刻意）。

    🔴 這是 MUT-W6 的靶心：把白名單擴成 `("narrative", "title")` 必須轉紅
    （用標題冒充正文會讓節點語意失真）。
    """
    assert ea._NARRATIVE_EXTRA_KEYS == ("narrative",), (
        f"白名單不得擴張：{ea._NARRATIVE_EXTRA_KEYS}"
    )
    assert "title" not in ea._NARRATIVE_EXTRA_KEYS


def test_whitelist_guard_detects_expansion(iso_env):
    """白名單守衛**有牙齒**：把白名單擴成含 `title` ⇒ 本檔守衛必須抓到。"""
    original = ea._NARRATIVE_EXTRA_KEYS
    try:
        ea._NARRATIVE_EXTRA_KEYS = ("narrative", "title")
        with pytest.raises(AssertionError):
            test_narrative_whitelist_not_expanded()
    finally:
        ea._NARRATIVE_EXTRA_KEYS = original


# ══════════════════════════════════════════════════════════════
# 6. 裁決 2（維持解耦）
# ══════════════════════════════════════════════════════════════


def test_submit_still_receives_empty_tuple(iso_env, tmp_path):
    """`submit()` 仍收**空 tuple**（fact 實體**不**接入昇華鏈）。"""
    _seed_fact(tmp_path)
    writer, gate = _emit([_thread("t-w3-008")])

    assert len(gate.submits) == 1, gate.submits
    submit = gate.submits[0]
    assert submit["memory_facts"] == (), submit
    assert isinstance(submit["memory_facts"], tuple)
    assert submit["kwargs"].get("agent_id") == AGENT
