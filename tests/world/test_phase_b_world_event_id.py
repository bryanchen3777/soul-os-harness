"""
tests/world/test_phase_b_world_event_id.py — [Step 60/60] Phase B 引用閉環

Owner-locked contract under test (Phase B: **引用閉環，不是行為閉環**):

    World Log record  ←(join)←  perception trace ``extra["world_event_id"]``

``src/world/middleware.py`` 的 **world evaluated** ``WorldPerceptionTrace(...)``
建構處（唯一 runtime 寫入點）在**既有 ``extra`` dict 內**追加
``"world_event_id": build_world_event_id(world_event.source, world_event.novelty_id)``
—— **僅當** ``world_event.source in WORLD_LOG_BACKED_SOURCES``。

單一 identity authority：``build_world_event_id`` 與 ``WORLD_LOG_BACKED_SOURCES``
都直接從 ``src/world/world_log.py`` import（本檔以 ``is`` 同一物件斷言證明）。

紅線（本檔逐條釘死）:
  1. ``null`` **不寫**：不寫引用的列一律「**缺欄**」（``"world_event_id" not in extra``），
     不是 ``world_event_id: null``。``null`` 保留給未來「有 world source 依據、
     但引用無法成立」的顯式契約語意。
  2. 不寫引用的列：validation 列、received 列（**即使 source=weather/news/calendar**）、
     social validation / social received / social evaluated、所有歷史 trace、
     不在 ``WORLD_LOG_BACKED_SOURCES`` 的 source（synthetic / 未知 source /
     未來新 source）。
  3. ``world_event_id`` 是 observation / join evidence，**不是**行為信號：
     它不進任何 gate，也不改 timestamp / summary / score / threshold /
     priority / collision window / wake gate / prompt。
  4. Phase A 的 writer eligibility（``is_world_log_eligible`` /
     ``EXCLUDED_WORLD_LOG_SOURCES``）**語意凍結**：不得被改寫成
     ``source in WORLD_LOG_BACKED_SOURCES``（那會讓 social / 未來 source
     突然變成不可落盤）。

測試段落:
  A. 正向：calendar / weather / news 的 evaluated 列有引用，逐字 ==
     ``world:{source}:{novelty_id}`` == ``build_world_event_id(...)``
  B. 負向：received / validation / social evaluated / synthetic / 未知 source
     —— 每一條都斷言「**缺欄**」
  C. 邊界與權威：identity authority、唯一 builder 呼叫點、golden top-level key set
  D. Phase A eligibility 未被改寫（行為 + 靜態）
  E. frozen-boundary 守門（靜態 + 行為）

測試紀律（全部為本票硬性要求）:
  - repo 內 ``.venv`` 直譯器（``.venv\\Scripts\\python.exe -m pytest``）
  - 0 network（autouse fixture 封 ``socket.create_connection`` / ``getaddrinfo``）
  - 0 LLM / 0 真 Event Bus server / 0 queue worker（一律 fake bus）
  - 0 伺服器啟動 / 0 生產埠 / 0 行程操作 / 0 生產 ``data/**`` 寫入
    （``tests/conftest.py`` 全域 autouse 資料根隔離；本檔另以
    ``test_data_root_is_isolated_from_production`` 直接斷言）
"""
from __future__ import annotations

import asyncio
import ast
import inspect
import json
import re
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.paths import data_root, reset_data_root
from src.social.schema import SPACE_LOUNGE, VISIBILITY_PUBLIC, SocialWorldEvent
from src.world import middleware as mwmod
from src.world import world_log as wlmod
from src.world.middleware import WorldPerceptionMiddleware
from src.world.perception import (
    SELECTION_SELECTED_TOP_N,
    WorldEvent,
    compute_scores,
    should_accept,
)
from src.world.state import WorldPerceptionState
from src.world.trace import WorldPerceptionTraceWriter


# ────────────────────────────────────────────────────────────────────
# 固定常數 / 黃金集（golden，逐字）
# ────────────────────────────────────────────────────────────────────

#: 本票明文的三個引用權威 source（**逐字**；不得由常數推導，才驗得出漂移）。
BACKED_SOURCES: Tuple[str, ...] = ("calendar", "weather", "news")

#: 明文「不寫引用」的 source（synthetic = Phase A 明文排除；future_source =
#: 未來的未知 source；audio_input = 合法但不在 Phase B 引用權威內）。
NON_BACKED_SOURCES: Tuple[str, ...] = ("social", "synthetic", "future_source", "audio_input")

#: 改動前生產 schema 黃金集（來源同 ``tests/test_world_fact_text_persist_1.py``:
#: 生產 ``data/world/perception_trace.jsonl`` 唯讀實測的 keys union）。
GOLDEN_TOP_LEVEL_KEYS = {
    "accepted", "context_injected", "event_id", "event_type", "extra",
    "memory_written", "novelty_count_in_window", "novelty_id", "reason",
    "scores", "selection_reason", "source", "timestamp",
}
GOLDEN_SCORES_KEYS = {
    "emotional_significance", "novelty", "personal_significance",
    "priority_boost", "relevance", "temporal_significance",
}
GOLDEN_EVALUATED_EXTRA_BASE = {
    "phase", "agent_id", "temporal_salience", "anticipatory_flavor",
    "vulnerability_window", "user_keyword_count", "world_event_priority",
}

#: frozen-boundary 靜態守門：這兩個檔**完全不得**出現引用相關字樣。
FROZEN_BOUNDARY_FILES: Tuple[str, ...] = (
    "src/soul/life_thread_wake_gate.py",
    "src/soul/life_thread_origins.py",
)
FORBIDDEN_BOUNDARY_TOKENS: Tuple[str, ...] = (
    "world_event_id",
    "WORLD_LOG_BACKED_SOURCES",
)

MIDDLEWARE_PATH = _REPO_ROOT / "src" / "world" / "middleware.py"
WORLD_LOG_PATH = _REPO_ROOT / "src" / "world" / "world_log.py"

#: 固定時間戳（determinism；validation 要求 ISO 8601 UTC）。
FIXED_TS = "2026-09-22T09:30:00+00:00"
FACT_TEXT = "外面開始下雨了，氣溫掉到 18 度。"
AGENT = "agent_ruka"

_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")

#: event type 只影響 scoring，與本票契約無關；仍給語意合理值避免誤讀。
_TYPE_BY_SOURCE = {
    "weather": "rain_started",
    "calendar": "calendar_event",
    "news": "celebrity_news",
    "audio_input": "ambient_audio",
}


# ────────────────────────────────────────────────────────────────────
# fixtures / doubles
# ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """全程禁止對外連線（證明本票 0 網路）。

    注意：**不** patch ``socket.socket.connect``（Windows asyncio Proactor
    event loop 的自體管道會走 ``socket.socketpair()``，patch 下去會把 event
    loop 弄死）。只封高階 outbound 入口與 DNS。
    """
    def _boom(*args, **kwargs):  # pragma: no cover - 只在違規時觸發
        raise AssertionError("本測試禁止任何網路呼叫（0 網路）")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    yield


@pytest.fixture
def iso_env(tmp_path, monkeypatch):
    """顯式隔離：資料根 → pytest tmp（0 生產 ``data/**`` 寫入）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    try:
        yield tmp_path / "data"
    finally:
        monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
        reset_data_root()


class RecordingBus:
    """Fake bus：0 server / 0 queue worker / 0 production Event Bus。"""

    def __init__(self) -> None:
        self.published: List[SoulEvent] = []

    async def publish(self, event: SoulEvent) -> None:
        self.published.append(event)

    async def start(self) -> None:  # pragma: no cover - compatibility only
        return None

    async def stop(self) -> None:  # pragma: no cover - compatibility only
        return None


# ────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _make_middleware(trace_path: Path, *, accept_threshold: float = 0.0):
    return WorldPerceptionMiddleware(
        bus=RecordingBus(),
        state=WorldPerceptionState(),
        trace_writer=WorldPerceptionTraceWriter(trace_path),
        accept_threshold=accept_threshold,
    )


def _read_jsonl(path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """回 (records, raw_lines)，raw_lines 用來斷言「缺欄」而非 ``null``。"""
    if not path.exists():  # pragma: no cover - defensive
        return [], []
    records: List[Dict[str, Any]] = []
    raws: List[str] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                raws.append(line)
                records.append(json.loads(line))
    return records, raws


def _of_phase(records: List[Dict[str, Any]], phase: str) -> List[Dict[str, Any]]:
    return [r for r in records if (r.get("extra") or {}).get("phase") == phase]


def _novelty_id(source: str, n: int = 1) -> str:
    return f"novelty_{source}_{n:04d}"


def _world_event(source: str, novelty_id: str, *, summary: str = FACT_TEXT) -> WorldEvent:
    return WorldEvent(
        source=source,
        type=_TYPE_BY_SOURCE.get(source, "rain_started"),
        novelty_id=novelty_id,
        ts=FIXED_TS,
        summary=summary,
        data={},
        priority=0,
    )


def _make_enriched_event(agent_id: str = AGENT) -> SoulEvent:
    """觸發 Pass 1/2/3（evaluated trace）的 AGENT_INTENT_ENRICHED event。"""
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id,
        target=agent_id,
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id,
            "reason": "user_message",
            "mode": "private",
            "draft": "",
            "target_user_id": "bryan",
            "chrono_context": "",
            "memory_context": "",
        },
    )


def _run_evaluated(
    tmp_path: Path,
    source: str,
    *,
    tag: str,
    via_state: bool = False,
    accept_threshold: float = 0.0,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """跑一次「world event → evaluated」路徑，回 (records, raw_lines)。

    ``via_state=True``：繞過 bus validation 直接進 state（用來模擬「未來的
    未知 source」走到 evaluated 分支的形狀；``VALID_SOURCES`` 白名單會先擋掉
    它，所以不能走 bus）。
    """
    trace_path = tmp_path / f"trace_{tag}.jsonl"
    mw = _make_middleware(trace_path, accept_threshold=accept_threshold)
    ev = _world_event(source, _novelty_id(source))
    if via_state:
        mw.state.add(ev)
    else:
        _run(mw.process_world_event_direct(ev))
    _run(mw.handle_event(_make_enriched_event()))
    return _read_jsonl(trace_path)


# ────────────────────────────────────────────────────────────────────
# A. 正向：有引用的 evaluated 列
# ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", BACKED_SOURCES)
def test_a1_backed_source_evaluated_row_carries_world_event_id(tmp_path, source):
    """A.1: calendar / weather / news 的 evaluated 列必須有 ``extra["world_event_id"]``。

    逐字 == ``world:{source}:{novelty_id}``，且 == ``build_world_event_id(...)``
    （單一 identity authority 的輸出）。
    """
    records, raws = _run_evaluated(tmp_path, source, tag=f"a1_{source}")
    evaluated = _of_phase(records, "evaluated")
    assert len(evaluated) == 1, f"{source}: 應恰 1 筆 evaluated trace"
    rec = evaluated[0]

    extra = rec["extra"]
    assert "world_event_id" in extra, f"{source}: evaluated 列必須帶引用鍵"
    nid = _novelty_id(source)
    expected = f"world:{source}:{nid}"
    assert rec["novelty_id"] == nid
    assert rec["source"] == source
    assert extra["world_event_id"] == expected
    assert extra["world_event_id"] == wlmod.build_world_event_id(source, nid)
    # 引用是「有值」的字串，不是 null / 空字串
    assert isinstance(extra["world_event_id"], str)
    assert extra["world_event_id"] != ""
    assert extra["world_event_id"].count(":") == 2

    # raw JSONL 逐字：值是字串，**不是** null
    raw = next(r for r in raws if '"phase": "evaluated"' in r)
    assert f'"world_event_id": "{expected}"' in raw
    assert '"world_event_id": null' not in raw


@pytest.mark.parametrize("source", BACKED_SOURCES)
def test_a2_backed_source_evaluated_extra_has_no_null_reference(tmp_path, source):
    """A.2: 有引用的 evaluated 列，``extra`` 內**不得**出現任何 ``None`` 值的引用。

    ``null`` 保留給未來「有 world source 依據、但引用無法成立」的顯式契約語意；
    本票不製造這種假語意。
    """
    records, _ = _run_evaluated(tmp_path, source, tag=f"a2_{source}")
    extra = _of_phase(records, "evaluated")[0]["extra"]
    assert extra["world_event_id"] is not None
    assert not any(
        v is None for k, v in extra.items() if "world_event_id" in k
    ), "不得以 null 佔位"
    # 本票不新增任何「值為 None 的引用鍵」
    assert [k for k, v in extra.items() if v is None] == []


def test_a3_backed_sources_set_is_exactly_the_ticket_triple():
    """A.3: 引用權威集合逐字 == {"calendar","weather","news"}（防參數化漂移）。"""
    assert wlmod.WORLD_LOG_BACKED_SOURCES == frozenset({"calendar", "weather", "news"})
    assert frozenset(BACKED_SOURCES) == wlmod.WORLD_LOG_BACKED_SOURCES


def test_a4_evaluated_extra_is_additive_only(tmp_path):
    """A.4: 引用是**純加法**：既有 evaluated extra 7 鍵逐字不變，只多 1 鍵。"""
    records, _ = _run_evaluated(tmp_path, "weather", tag="a4")
    extra = _of_phase(records, "evaluated")[0]["extra"]
    assert GOLDEN_EVALUATED_EXTRA_BASE <= set(extra)
    assert set(extra) == GOLDEN_EVALUATED_EXTRA_BASE | {"summary", "world_event_id"}
    assert extra["phase"] == "evaluated"


# ────────────────────────────────────────────────────────────────────
# B. 負向：一律「缺欄」（不是 is None）
# ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", BACKED_SOURCES)
def test_b1_received_row_has_no_reference_key(tmp_path, source):
    """B.1 🔴: received 列**即使 source=weather/news/calendar** 也必須缺欄。"""
    trace_path = tmp_path / f"trace_b1_{source}.jsonl"
    mw = _make_middleware(trace_path)
    _run(mw.process_world_event_direct(_world_event(source, _novelty_id(source))))
    records, raws = _read_jsonl(trace_path)

    received = _of_phase(records, "received")
    assert len(received) == 1
    rec = received[0]
    assert rec["source"] == source, "前提：received 列的 source 就是該 backed source"
    assert rec["extra"]["phase"] == "received"
    assert "world_event_id" not in rec["extra"], "received 列不得寫引用（缺欄，不是 null）"
    assert "world_event_id" not in raws[0]


@pytest.mark.parametrize("source", BACKED_SOURCES)
def test_b2_validation_row_has_no_reference_key(tmp_path, source):
    """B.2 🔴: validation 列（source=weather/news/calendar）必須缺欄。"""
    trace_path = tmp_path / f"trace_b2_{source}.jsonl"
    mw = _make_middleware(trace_path)
    # 故意讓 ts 不是 ISO 8601 ⇒ validation reject；payload 仍帶 source
    bad = SoulEvent(
        event_type=EventType.WORLD_EVENT,
        source="synthetic",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "source": source,
            "type": "rain_started",
            "novelty_id": _novelty_id(source),
            "ts": "not-a-timestamp",
            "summary": FACT_TEXT,
            "data": {},
        },
    )
    _run(mw.handle_event(bad))
    records, raws = _read_jsonl(trace_path)

    validation = _of_phase(records, "validation")
    assert len(validation) == 1, f"{source}: validation reject 仍必須寫 trace（既有行為）"
    rec = validation[0]
    assert rec["source"] == source
    assert rec["extra"]["phase"] == "validation"
    assert "world_event_id" not in rec["extra"]
    assert "world_event_id" not in raws[0]


def test_b3_social_evaluated_row_has_no_reference_key(tmp_path):
    """B.3 🔴: social evaluated 列必須缺欄（social 不在引用權威內）。"""
    trace_path = tmp_path / "trace_b3_social.jsonl"
    mw = _make_middleware(trace_path)
    social = SocialWorldEvent(
        source="social",
        type="social",
        novelty_id="novelty_social_0001",
        ts=FIXED_TS,
        summary="agent_miku 在客廳打了招呼",
        data={},
        priority=0,
        actor_id="agent_miku",
        space_id=SPACE_LOUNGE,
        visibility=VISIBILITY_PUBLIC,
        event_type="greeting",
        content="大家好",
    )
    mw._render_social_context(
        [social],
        user_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        agent_id=AGENT,
    )
    records, raws = _read_jsonl(trace_path)

    social_rows = _of_phase(records, "social_evaluated")
    assert len(social_rows) == 1
    rec = social_rows[0]
    assert rec["source"] == "social"
    assert rec["extra"]["phase"] == "social_evaluated"
    assert "world_event_id" not in rec["extra"]
    assert "world_event_id" not in raws[0]


@pytest.mark.parametrize("source", ("synthetic", "future_source", "audio_input"))
@pytest.mark.parametrize("via_state", (False, True))
def test_b4_non_backed_source_evaluated_row_has_no_reference_key(
    tmp_path, source, via_state
):
    """B.4 🔴: 不在 ``WORLD_LOG_BACKED_SOURCES`` 的 source（synthetic / 未知 /
    合法但未覆蓋）的 evaluated 列必須缺欄。

    ``via_state=False`` 走 bus validation（``synthetic`` / ``audio_input`` 在
    ``VALID_SOURCES`` 內）；``via_state=True`` 直接進 state，模擬「未來的未知
    source」（``future_source`` 不在白名單，bus 會先擋掉）。
    """
    if source == "future_source" and not via_state:
        pytest.skip("future_source 不在 VALID_SOURCES，只有 state 直注路徑可達 evaluated")
    records, raws = _run_evaluated(
        tmp_path, source, tag=f"b4_{source}_{int(via_state)}", via_state=via_state
    )
    evaluated = _of_phase(records, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["source"] == source
    assert source not in wlmod.WORLD_LOG_BACKED_SOURCES
    assert "world_event_id" not in rec["extra"]
    assert rec["extra"]["phase"] == "evaluated"
    assert all("world_event_id" not in r for r in raws), "任何一列都不得寫該鍵"
    # 沒有引用鍵時，extra 就是黃金集 + summary（純加法、無佔位）
    assert set(rec["extra"]) == GOLDEN_EVALUATED_EXTRA_BASE | {"summary"}


@pytest.mark.parametrize("source", NON_BACKED_SOURCES)
def test_b5_never_writes_null_placeholder(tmp_path, source):
    """B.5 🔴: 不寫引用的列是**缺欄**，raw JSON 內不得出現 ``"world_event_id"``。"""
    if source == "social":
        trace_path = tmp_path / "trace_b5_social.jsonl"
        mw = _make_middleware(trace_path)
        social = SocialWorldEvent(
            source="social",
            type="social",
            novelty_id="novelty_social_0001",
            ts=FIXED_TS,
            summary="打招呼",
            data={},
            priority=0,
            actor_id="agent_miku",
            space_id=SPACE_LOUNGE,
            visibility=VISIBILITY_PUBLIC,
            event_type="greeting",
            content="大家好",
        )
        mw._render_social_context(
            [social],
            user_keywords=[],
            temporal_salience="low",
            anticipatory_flavor="none",
            vulnerability_window=False,
            agent_id=AGENT,
        )
        records, raws = _read_jsonl(trace_path)
    else:
        records, raws = _run_evaluated(
            tmp_path,
            source,
            tag=f"b5_{source}",
            via_state=(source == "future_source"),
        )
    assert records, f"{source}: 前提是這條路徑會寫 trace"
    for raw in raws:
        assert "world_event_id" not in raw, f"{source}: 不得出現該鍵（含 null）"


# ────────────────────────────────────────────────────────────────────
# C. 邊界與權威
# ────────────────────────────────────────────────────────────────────

def test_c1_identity_authority_is_a_single_object():
    """C.1: ``middleware`` 的 builder / 常數必須與 ``world_log`` 是**同一物件**。

    這是「單一 identity authority」的硬證：不是同名副本、不是重新實作。
    """
    assert mwmod.build_world_event_id is wlmod.build_world_event_id
    assert mwmod.WORLD_LOG_BACKED_SOURCES is wlmod.WORLD_LOG_BACKED_SOURCES
    # 逐字格式（canonical，非 bus UUID）
    assert mwmod.build_world_event_id("calendar", "nid_1") == "world:calendar:nid_1"


def test_c2_builder_is_imported_not_reimplemented_and_has_one_call_site():
    """C.2: 靜態證明 —— middleware 從 ``.world_log`` import（唯一權威），
    不在本模組自建清單，且 builder 恰有**一個**呼叫點（唯一 runtime 寫入點）。"""
    tree = ast.parse(MIDDLEWARE_PATH.read_text(encoding="utf-8"))

    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("world_log"):
            imported |= {alias.name for alias in node.names}
    assert {"WORLD_LOG_BACKED_SOURCES", "build_world_event_id"} <= imported, (
        "必須以 import 取得身份權威（不得自行拼字串 / 自建第二份清單）"
    )

    # 不得在模組層重新定義同名列（shadowing 會破壞身份權威）
    for node in tree.body:
        targets: List[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in ("WORLD_LOG_BACKED_SOURCES", "build_world_event_id"):
            assert name not in targets, f"middleware 不得重新定義 {name}"

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_world_event_id"
    ]
    assert len(calls) == 1, f"builder 只允許一個呼叫點，實際 {len(calls)}"


@pytest.mark.parametrize("source", BACKED_SOURCES)
def test_c3_golden_top_level_key_set_unchanged(tmp_path, source):
    """C.3: trace 的 top-level key 集合與既有黃金集**逐字相同**。

    證明本票**沒有**新增任何 top-level schema 欄位（``world_event_id`` 只存在
    於 ``extra`` 內，不是新的 top-level 欄位）。
    """
    records, _ = _run_evaluated(tmp_path, source, tag=f"c3_{source}")
    for rec in records:
        assert set(rec) == GOLDEN_TOP_LEVEL_KEYS, (
            f"{source}: top-level key set 被改動: {set(rec) ^ GOLDEN_TOP_LEVEL_KEYS}"
        )
        assert "world_event_id" not in rec, "world_event_id 不得是 top-level 欄位"
        assert set(rec["scores"]) == GOLDEN_SCORES_KEYS


def test_c4_reference_is_perception_side_only_not_a_second_artifact(tmp_path, iso_env):
    """C.4: 引用只落在既有 ``perception_trace.jsonl``；0 新 artifact、0 生產寫入。"""
    before = {p.name for p in iso_env.rglob("*")} if iso_env.exists() else set()
    _run_evaluated(tmp_path, "calendar", tag="c4")
    after = {p.name for p in iso_env.rglob("*")} if iso_env.exists() else set()
    assert before == after, "不得新增任何 artifact 到資料根"
    assert not (iso_env / "world").exists()
    # 資料根（全域 autouse 隔離）不得是 repo 生產 data/
    assert data_root() != (_REPO_ROOT / "data")
    assert str(_REPO_ROOT / "data") != str(data_root())


# ────────────────────────────────────────────────────────────────────
# D. Phase A eligibility 未被改寫
# ────────────────────────────────────────────────────────────────────

def test_d1_phase_a_eligibility_not_bound_to_phase_b_constant():
    """D.1 🔴: ``is_world_log_eligible`` 仍**只**看 ``EXCLUDED_WORLD_LOG_SOURCES``。

    一個不在 ``WORLD_LOG_BACKED_SOURCES`` 的 source（``future_source`` /
    ``social``）必須仍是 eligible —— 否則 Phase A 行為被偷偷改寫（social 與
    未來 source 會突然不可落盤）。
    """
    assert wlmod.is_world_log_eligible(_world_event("future_source", "nid_future_1")) is True
    assert wlmod.is_world_log_eligible(_world_event("social", "nid_social_1")) is True
    assert wlmod.is_world_log_eligible(_world_event("audio_input", "nid_audio_1")) is True
    for src in BACKED_SOURCES:
        assert wlmod.is_world_log_eligible(_world_event(src, "nid_backed_1")) is True
    # 唯一被排除的仍是字面 "synthetic"
    assert wlmod.is_world_log_eligible(_world_event("synthetic", "nid_synth_1")) is False

    # 兩個集合語意不同：不得互相取代、不得相等
    assert wlmod.EXCLUDED_WORLD_LOG_SOURCES == frozenset({"synthetic"})
    assert wlmod.WORLD_LOG_BACKED_SOURCES != wlmod.EXCLUDED_WORLD_LOG_SOURCES
    assert wlmod.WORLD_LOG_BACKED_SOURCES & wlmod.EXCLUDED_WORLD_LOG_SOURCES == frozenset()


def test_d2_eligibility_source_does_not_reference_phase_b_constant():
    """D.2 🔴: 靜態守門 —— ``is_world_log_eligible`` 的實作不得提及
    ``WORLD_LOG_BACKED_SOURCES``（禁止「一致性重構」）。"""
    src = inspect.getsource(wlmod.is_world_log_eligible)
    assert "WORLD_LOG_BACKED_SOURCES" not in src
    assert "EXCLUDED_WORLD_LOG_SOURCES" in src


# ────────────────────────────────────────────────────────────────────
# E. frozen-boundary 守門
# ────────────────────────────────────────────────────────────────────

def test_e1_frozen_boundary_files_have_no_reference_token():
    """E.1 🔴: wake gate 與 origins 內**完全沒有**引用相關字樣（grep 式守門）。"""
    for rel in FROZEN_BOUNDARY_FILES:
        path = _REPO_ROOT / rel
        assert path.is_file(), f"守門前提：{rel} 必須存在"
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_BOUNDARY_TOKENS:
            assert token not in text, f"{rel} 不得出現 {token}"
    # 非空洞控制：同一掃描機制在「確實含該 token」的檔案上會命中
    probe = MIDDLEWARE_PATH.read_text(encoding="utf-8")
    assert all(t in probe for t in FORBIDDEN_BOUNDARY_TOKENS), (
        "守門不得是空洞的：middleware 內確實含這些 token，掃描機制必須能命中"
    )


def test_e2_evaluated_behavior_is_unchanged_besides_the_reference(tmp_path):
    """E.2 🔴: 行為守門 —— 引用只加在 ``extra``，其餘欄位與基準逐位元一致。

    ``timestamp`` 格式（perception-time ISO UTC）／``scores``／``accepted``／
    ``reason``／``selection_reason``／``novelty_count_in_window``／
    ``memory_written`` 全部與既有 pure core 的輸出一致；
    ``extra["summary"]`` 語意（World Fact Text）未變。
    """
    records, _ = _run_evaluated(tmp_path, "weather", tag="e2")
    rec = _of_phase(records, "evaluated")[0]

    ev = _world_event("weather", _novelty_id("weather"))
    expected = compute_scores(
        event=ev,
        novelty_count=rec["novelty_count_in_window"],
        current_user_context_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        silence_hours=0.0,
        event_priority=0,
    )
    expected_accepted, expected_reason = should_accept(expected, threshold=0.0)

    assert set(rec) == GOLDEN_TOP_LEVEL_KEYS
    assert rec["scores"] == {k: getattr(expected, k) for k in GOLDEN_SCORES_KEYS}
    assert rec["accepted"] is expected_accepted is True
    assert rec["reason"] == expected_reason
    assert rec["context_injected"] is True          # accepted 且唯一事件 ⇒ top-N
    assert rec["memory_written"] is False           # Perception ≠ Memory
    assert rec["novelty_count_in_window"] == 1
    assert rec["selection_reason"].startswith(SELECTION_SELECTED_TOP_N)

    # timestamp：perception-time、ISO-8601 UTC（不是 happened_at、不得被本票動到）
    ts = rec["timestamp"]
    assert _ISO_UTC_RE.match(ts), f"timestamp 格式被改動: {ts!r}"
    parsed = datetime.fromisoformat(ts)
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0

    # extra["summary"] 語意未變（World Fact Text，<= 200 字元）
    assert rec["extra"]["summary"] == FACT_TEXT
    assert len(rec["extra"]["summary"]) <= 200

    # 引用是 observation / join evidence：不進 scores、不改判定
    assert set(rec["scores"]) == GOLDEN_SCORES_KEYS
    assert "world_event_id" not in rec["scores"]
    assert rec["extra"]["world_event_id"] == f"world:weather:{_novelty_id('weather')}"


def test_e3_reference_is_not_a_gate_input(tmp_path):
    """E.3 🔴: 引用不得成為行為信號 —— 判定完全由既有 pure core 決定。

    以同一個事件（``weather``，有引用）驗證 ``accepted`` / ``reason`` 逐字等於
    ``should_accept(compute_scores(...))``；並直接斷言引用字串不出現在任何判定
    欄位（``reason`` / ``selection_reason``）內。
    """
    records, _ = _run_evaluated(tmp_path, "weather", tag="e3")
    rec = _of_phase(records, "evaluated")[0]

    expected = compute_scores(
        event=_world_event("weather", _novelty_id("weather")),
        novelty_count=rec["novelty_count_in_window"],
        current_user_context_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        silence_hours=0.0,
        event_priority=0,
    )
    accepted, reason = should_accept(expected, threshold=0.0)
    assert rec["accepted"] is accepted
    assert rec["reason"] == reason

    reference = rec["extra"]["world_event_id"]
    assert "world:" not in rec["reason"]
    assert "world:" not in rec["selection_reason"]
    assert reference not in rec["reason"]
    assert reference not in rec["selection_reason"]
    assert reference not in json.dumps(rec["scores"])


# ────────────────────────────────────────────────────────────────────
# F. Owner Authorization A 守門：被授權更新的兩條 golden extra 斷言
#
# Owner 裁定（Authorization A）：`tests/test_world_fact_text_persist_1.py` 的兩條
# exact-extra assertion 可做 **additive acceptance-baseline expansion**（把
# ``world_event_id`` 併入期待集合），但語意**必須仍是精確 equality**。
# 本段守門把「授權範圍」變成可被 mutation 證明的牙齒：
#   1. 兩條 assert 必須是 ``== GOLDEN_EVALUATED_EXTRA_BASE | {<str>...}``
#      的精確集合相等（單行或多行排版皆可）。
#   2. 這兩條上**不得**出現任何寬鬆運算子 / 方法（``<=`` / ``>=`` /
#      ``.issubset`` / ``.issuperset`` / ``setdefault`` / ``pop`` / ``in`` …）。
#   3. ``GOLDEN_EVALUATED_EXTRA_BASE`` 的**定義體**必須與凍結期望集合逐鍵相同
#      （防偷改本體）。
# ────────────────────────────────────────────────────────────────────

PERSIST_TEST_PATH = _REPO_ROOT / "tests" / "test_world_fact_text_persist_1.py"

#: Owner 凍結的 golden extra 集合本體（改動前生產 schema 的 7 鍵）。
FROZEN_GOLDEN_EVALUATED_EXTRA_BASE = frozenset({
    "phase", "agent_id", "temporal_salience", "anticipatory_flavor",
    "vulnerability_window", "user_keyword_count", "world_event_priority",
})

#: 兩條被授權的 assertion 各自必須等於這個**精確**集合（順序無關）。
AUTHORIZED_EXTRA_ASSERTION_SETS: Tuple[frozenset, ...] = (
    frozenset({"world_event_id"}),                      # ~:287
    frozenset({"summary", "world_event_id"}),           # ~:455
)

#: 寬鬆字樣：一旦出現在這兩條 assertion 的原始碼片段上，即視為放寬 frozen
#: acceptance（Owner 明文禁止 subset / contains / issuperset / setdefault / pop …）。
LENIENT_TOKENS: Tuple[str, ...] = (
    "<=", ">=", "!=", "issubset", "issuperset", "isdisjoint",
    "setdefault", "pop(", "union(", "update(", "difference(",
    "symmetric_difference(", "discard(", "add(",
    'in rec["extra"]', "any(", "all(",
)


def _persist_test_source() -> str:
    assert PERSIST_TEST_PATH.is_file(), f"守門前提：{PERSIST_TEST_PATH} 必須存在"
    return PERSIST_TEST_PATH.read_text(encoding="utf-8")


def _golden_extra_assert_nodes(text: str) -> List[Tuple[ast.Assert, str]]:
    """原始碼內「比對 ``GOLDEN_EVALUATED_EXTRA_BASE`` 的 extra 集合」的 assert 節點。"""
    tree = ast.parse(text)
    found: List[Tuple[ast.Assert, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        seg = ast.get_source_segment(text, node.test) or ""
        if '["extra"]' in seg and "GOLDEN_EVALUATED_EXTRA_BASE" in seg:
            found.append((node, seg))
    found.sort(key=lambda pair: pair[0].lineno)
    return found


def _asserted_set_literal(node: ast.AST) -> Any:
    """``== GOLDEN_EVALUATED_EXTRA_BASE | {<str>...}`` 形 ⇒ frozenset；其他形 ⇒ None。

    只接受**精確相等**（``ast.Eq``）＋右側 BitOr 字面集合；任何寬鬆比較、
    方法呼叫（``.issubset(...)``）、非字面集合、``in`` 形式一律回 ``None``
    ⇒ 守門 RED。
    """
    if not isinstance(node, ast.Compare):
        return None
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return None
    if len(node.comparators) != 1:
        return None
    left, right = node.left, node.comparators[0]
    if not (
        isinstance(left, ast.Call)
        and isinstance(left.func, ast.Name)
        and left.func.id == "set"
        and len(left.args) == 1
        and isinstance(left.args[0], ast.Subscript)
    ):
        return None
    if not (isinstance(right, ast.BinOp) and isinstance(right.op, ast.BitOr)):
        return None
    base, extra = right.left, right.right
    if not (isinstance(base, ast.Name) and base.id == "GOLDEN_EVALUATED_EXTRA_BASE"):
        return None
    if not isinstance(extra, ast.Set):
        return None
    names: List[str] = []
    for elt in extra.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        names.append(elt.value)
    return frozenset(names)


def test_f1_authorized_golden_extra_assertions_are_exact_equality():
    """F.1 🔴: 被授權的兩條 assertion 必須是**精確集合相等**，且**不得**放寬。

    mutation 牙齒：把任一條的 ``==`` 改成 ``>=`` / ``.issubset(...)``（M7）⇒ 本測試 RED。
    """
    text = _persist_test_source()
    nodes = _golden_extra_assert_nodes(text)
    assert len(nodes) == 2, (
        f"預期恰 2 條 GOLDEN_EVALUATED_EXTRA_BASE 的 extra 斷言，實際 {len(nodes)}"
    )

    literal_sets: List[frozenset] = []
    for node, seg in nodes:
        for token in LENIENT_TOKENS:
            assert token not in seg, (
                f"assertion 出現寬鬆字樣 {token!r}（Authorization A 禁止放寬）: {seg!r}"
            )
        literal = _asserted_set_literal(node.test)
        assert literal is not None, (
            "必須是 `set(...) == GOLDEN_EVALUATED_EXTRA_BASE | {...}` 的精確集合相等"
            f"（不得 subset / contains / issuperset / 方法呼叫）: {seg!r}"
        )
        literal_sets.append(literal)

    assert tuple(literal_sets) == AUTHORIZED_EXTRA_ASSERTION_SETS, (
        "被授權的期待集合必須逐字等於 Owner 裁定值："
        f"{literal_sets} != {list(AUTHORIZED_EXTRA_ASSERTION_SETS)}"
    )
    # :287 那條（rejected 路徑）不得含 summary；:455 那條必須含 summary
    assert "summary" not in literal_sets[0]
    assert "summary" in literal_sets[1]
    # 兩條都必須含 world_event_id（Authorization A 的 additive 授權內容）
    assert all("world_event_id" in s for s in literal_sets)


def test_f2_golden_evaluated_extra_base_definition_keys_are_frozen():
    """F.2 🔴: ``GOLDEN_EVALUATED_EXTRA_BASE`` **本體**逐鍵凍結（7 鍵，不得偷改）。

    mutation 牙齒：對本體增刪一鍵（M7b）⇒ 本測試 RED。
    """
    text = _persist_test_source()
    tree = ast.parse(text)

    definition: Any = None
    extra_assignments: List[int] = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target] if isinstance(node.target, ast.Name) else []
        for target in targets:
            if target.id == "GOLDEN_EVALUATED_EXTRA_BASE":
                extra_assignments.append(node.lineno)
                definition = node.value

    assert extra_assignments, "GOLDEN_EVALUATED_EXTRA_BASE 必須在模組層被定義"
    assert len(extra_assignments) == 1, (
        f"GOLDEN_EVALUATED_EXTRA_BASE 只能有一個模組層定義，實際 {extra_assignments}"
    )
    assert isinstance(definition, ast.Set), (
        "定義體必須是**字面集合**（不得由運算式 / 函式推導，否則凍結性不可驗）"
    )

    keys: set = set()
    for elt in definition.elts:
        assert isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        keys.add(elt.value)
    assert keys == set(FROZEN_GOLDEN_EVALUATED_EXTRA_BASE), (
        f"GOLDEN_EVALUATED_EXTRA_BASE 本體被改動：{sorted(keys)}"
    )
    assert len(definition.elts) == len(FROZEN_GOLDEN_EVALUATED_EXTRA_BASE), "不得有重複鍵"
    # 本體不得含 world_event_id（那是 additive 的期待項，不是 baseline 本體）
    assert "world_event_id" not in keys
    assert "summary" not in keys
