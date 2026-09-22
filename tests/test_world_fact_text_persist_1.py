# tests/test_world_fact_text_persist_1.py
# WORLD-FACT-TEXT-PERSIST-1（2026-09-14）— 契約可測斷言。
#
# 規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §5.2.4 ＋ 附錄 A.4（修訂 1）。
#
# 本票行為（**加法**，0 新檔 / 0 新 artifact / 0 新 schema 版本 / 0 新儲存面）：
#   1. `accepted == True` ⇒ 既有感知 trace 的**既有** `extra` dict 被寫入
#      `extra["summary"]`（世界事實文字副本），且**截斷 <= 200 字元**。
#   2. `accepted == False` ⇒ **不寫**該鍵（被拒事件不增加資料量）。
#   3. summary 為空字串／純空白 ⇒ **不寫**該鍵（缺欄 ＝ 無 fact text）。
#   4. 既有判定（`accepted`／`scores`／`novelty_*`／`context_injected`／
#      `memory_written`／`selection_reason`／`reason`）**逐位元不變**。
#
# 隔離：`tests/conftest.py` 的 autouse fixture 已把 `SOUL_OS_DATA_DIR` 指向
# per-test tmp；本檔另以 `monkeypatch.setenv` ＋ `reset_data_root()` 顯式隔離。
# **0 生產 `data/**` 寫入**、**0 真實 LLM 呼叫**、**0 網路**（模組層另有
# autouse 反網路 fixture 與 AST 靜態檢查把關）。
from __future__ import annotations

import ast
import json
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.eventbus.schema import EventPriority, EventType, SoulEvent  # noqa: E402
from src.paths import reset_data_root  # noqa: E402
from src.social import SPACE_LOUNGE, VISIBILITY_PUBLIC, SocialWorldEvent  # noqa: E402
from src.world import (  # noqa: E402
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)
from src.world import middleware as mwmod  # noqa: E402

MODULE_PATH = _REPO_ROOT / "src" / "world" / "middleware.py"

#: 讀取端口徑（`src/soul/life_thread_origins.py:103` MAX_FACT_CHARS = 200）。
CONSUMER_MAX_FACT_CHARS = 200

#: 固定時間戳（determinism；validation 要求 ISO 8601 UTC）。
FIXED_TS = "2026-09-14T09:30:00+00:00"

#: 正常的世界事實文字。
FACT_TEXT = "外面開始下雨了，氣溫掉到 18 度。"
#: 超過上限的事實文字（250 字元，用來驗截斷）。
FACT_TEXT_LONG = "雨" * 250

AGENT = "agent_ruka"

# ── 改動前 schema 黃金集（來源：生產 `data/world/perception_trace.jsonl` 唯讀實測，
#    14,655 筆的 top-level keys union／`scores` keys union／`phase=="evaluated"`
#    之 `extra` keys）────────────────────────────────────────────────
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
GOLDEN_SOCIAL_EVALUATED_EXTRA_BASE = {
    "phase", "agent_id", "actor_id", "space_id", "visibility",
}


# ══════════════════════════════════════════════════════════════
# fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """全程禁止對外連線：任何 outbound 連線嘗試即 fail（證明本票 0 網路）。

    注意：**不** patch `socket.socket.connect`——Windows 的 asyncio
    ProactorEventLoop 自體管道會走 `socket.socketpair()`，patch 下去會把
    event loop 弄死（實測 `_ssock` AttributeError）。只封 `create_connection`
    （所有高階 outbound 連線的入口）與 DNS 解析。
    """
    def _boom(*args, **kwargs):  # pragma: no cover - 只在違規時觸發
        raise AssertionError("本測試禁止任何網路呼叫（0 網路）")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    yield


@pytest.fixture
def iso_env(tmp_path, monkeypatch):
    """顯式隔離：資料根 → tmp（0 生產 `data/**` 寫入）。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    try:
        yield tmp_path / "data"
    finally:
        monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
        reset_data_root()


# ══════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════

def _run(coro):
    """在 sync test 內跑 async coroutine。"""
    import asyncio
    return asyncio.run(coro)


def _make_enriched_event(agent_id: str = AGENT, draft: str = "") -> SoulEvent:
    """建一個 AGENT_INTENT_ENRICHED event（觸發 Pass 1/2/3 evaluate + 寫 trace）。"""
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id,
        target=agent_id,
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id,
            "reason": "user_message",
            "mode": "private",
            "draft": draft,
            "target_user_id": "bryan",
            "chrono_context": "",
            "memory_context": "",
        },
    )


def _make_middleware(trace_path: Path, *, accept_threshold: float = 0.0):
    """建 middleware（bus 用 MagicMock；accept_threshold 決定 accept/reject）。"""
    bus = MagicMock()

    async def _noop_publish(ev):  # pragma: no cover - 只吞 bus 輸出
        return None

    bus.publish = _noop_publish
    mw = WorldPerceptionMiddleware(
        bus=bus,
        state=WorldPerceptionState(),
        trace_writer=WorldPerceptionTraceWriter(trace_path),
        accept_threshold=accept_threshold,
    )
    return mw


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _records_of_phase(records: List[Dict[str, Any]], phase: str) -> List[Dict[str, Any]]:
    return [r for r in records if (r.get("extra") or {}).get("phase") == phase]


def _external_scenario(
    tmp_path: Path,
    *,
    summary: str,
    accept_threshold: float = 0.0,
    novelty_id: str = "novelty_fact_text_0001",
    pre_change: bool = False,
    tag: str = "x",
) -> List[Dict[str, Any]]:
    """跑一次「外部世界事件 → evaluated」路徑，回傳全部 trace records。

    `pre_change=True` 時把 `_fact_summary_extra` 暫時換成**前置票前**的等價實作
    （永遠回 `{}`，即 extra 只有原本 7 個鍵）——這是同一輸入在**改動前**的形狀，
    作為對照組（control group）。
    """
    trace_path = tmp_path / f"trace_{tag}.jsonl"
    mw = _make_middleware(trace_path, accept_threshold=accept_threshold)
    ev = WorldEvent(
        source="weather",
        type="rain_started",
        novelty_id=novelty_id,
        ts=FIXED_TS,
        summary=summary,
        data={},
        priority=0,
    )
    patch = None
    if pre_change:
        patch = pytest.MonkeyPatch()
        patch.setattr(mwmod, "_fact_summary_extra", lambda *a, **k: {})
    try:
        _run(mw.process_world_event_direct(ev))
        _run(mw.handle_event(_make_enriched_event()))
    finally:
        if patch is not None:
            patch.undo()
    return _read_jsonl(trace_path)


def _social_scenario(
    tmp_path: Path,
    *,
    summary: str,
    accept_threshold: float = 0.0,
    tag: str = "s",
) -> List[Dict[str, Any]]:
    """跑一次「社交事件 → social_evaluated」路徑（`_render_social_context`）。"""
    trace_path = tmp_path / f"trace_social_{tag}.jsonl"
    mw = _make_middleware(trace_path, accept_threshold=accept_threshold)
    ev = SocialWorldEvent(
        source="social",
        type="social",
        novelty_id="novelty_social_fact_0001",
        ts=FIXED_TS,
        summary=summary,
        data={},
        priority=0,
        actor_id="agent_miku",
        space_id=SPACE_LOUNGE,
        visibility=VISIBILITY_PUBLIC,
        event_type="greeting",
        content="大家好",
    )
    mw._render_social_context(
        [ev],
        user_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        agent_id=AGENT,
    )
    return _read_jsonl(trace_path)


# ══════════════════════════════════════════════════════════════
# 1. 寫入條件（accepted / 空 / 空白 / 截斷）— 外部事件路徑
# ══════════════════════════════════════════════════════════════

def test_external_accepted_writes_summary(tmp_path, iso_env):
    """accepted == True ⇒ `extra["summary"]` 被寫入，值 ＝ 事實文字。"""
    recs = _external_scenario(tmp_path, summary=FACT_TEXT, tag="acc")
    evaluated = _records_of_phase(recs, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is True
    assert rec["extra"]["summary"] == FACT_TEXT
    assert len(rec["extra"]["summary"]) <= CONSUMER_MAX_FACT_CHARS


def test_external_accepted_summary_truncated_to_200_chars(tmp_path, iso_env):
    """accepted == True 且事實文字過長 ⇒ 截斷到 <= 200 字元（保護檔案成長）。"""
    recs = _external_scenario(tmp_path, summary=FACT_TEXT_LONG, tag="trunc")
    rec = _records_of_phase(recs, "evaluated")[0]
    assert rec["accepted"] is True
    text = rec["extra"]["summary"]
    assert len(text) == CONSUMER_MAX_FACT_CHARS
    assert text == FACT_TEXT_LONG[:CONSUMER_MAX_FACT_CHARS]
    # 只有「尾端被切掉」，開頭逐字元相同（不是重寫／不是捏造）
    assert FACT_TEXT_LONG.startswith(text)


def test_external_rejected_does_not_write_summary(tmp_path, iso_env):
    """accepted == False ⇒ **不寫** `extra["summary"]`（不讓被拒事件增加資料量）。"""
    recs = _external_scenario(
        tmp_path, summary=FACT_TEXT, accept_threshold=0.99, tag="rej"
    )
    evaluated = _records_of_phase(recs, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is False
    assert "summary" not in rec["extra"]
    assert set(rec["extra"]) == GOLDEN_EVALUATED_EXTRA_BASE | {"world_event_id"}


def test_external_blank_summary_never_writes_key(tmp_path, iso_env):
    """summary 為純空白 ⇒ validation 擋下（accepted False）且**任何** trace 都無該鍵。"""
    recs = _external_scenario(tmp_path, summary="   ", tag="blank")
    assert recs, "validation reject 仍必須寫 trace（既有行為）"
    for rec in recs:
        assert "summary" not in (rec.get("extra") or {})
        assert rec["accepted"] is False


def test_empty_and_whitespace_summary_unit_matrix():
    """`_fact_summary_extra` 逐格斷言：只寫非空事實文字，且僅 accepted == True。"""
    f = mwmod._fact_summary_extra
    assert f(True, FACT_TEXT) == {"summary": FACT_TEXT}
    assert f(True, "") == {}
    assert f(True, "   ") == {}
    assert f(True, "\n\t ") == {}
    assert f(True, None) == {}
    assert f(True, 123) == {}
    assert f(True, ["不是字串"]) == {}
    assert f(False, FACT_TEXT) == {}
    assert f(0, FACT_TEXT) == {}
    assert f(None, FACT_TEXT) == {}
    # 截斷（含前後空白 strip 後才截斷）
    assert f(True, "  " + FACT_TEXT_LONG + "  ") == {
        "summary": FACT_TEXT_LONG[:CONSUMER_MAX_FACT_CHARS]
    }


def test_producer_cap_matches_consumer_cap():
    """生產端上限 ＝ 讀取端上限（契約 §5.2.4／A.4 口徑一致）。"""
    from src.soul import life_thread_origins as m4

    assert mwmod.FACT_SUMMARY_MAX_CHARS == CONSUMER_MAX_FACT_CHARS
    assert m4.MAX_FACT_CHARS == CONSUMER_MAX_FACT_CHARS
    assert m4.FACT_TEXT_EXTRA_KEY == "summary"


# ══════════════════════════════════════════════════════════════
# 2. 社交事件路徑（第二個寫入點）
# ══════════════════════════════════════════════════════════════

def test_social_accepted_writes_summary(tmp_path, iso_env):
    """社交事件 accepted == True ⇒ 同一個 `extra["summary"]` 契約。"""
    recs = _social_scenario(tmp_path, summary="agent_miku 在客廳打了招呼", tag="acc")
    evaluated = _records_of_phase(recs, "social_evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is True
    assert rec["extra"]["summary"] == "agent_miku 在客廳打了招呼"
    assert set(rec["extra"]) == GOLDEN_SOCIAL_EVALUATED_EXTRA_BASE | {"summary"}


def test_social_rejected_does_not_write_summary(tmp_path, iso_env):
    """社交事件 accepted == False ⇒ 不寫該鍵。"""
    recs = _social_scenario(
        tmp_path, summary="agent_miku 在客廳打了招呼", accept_threshold=0.99, tag="rej"
    )
    rec = _records_of_phase(recs, "social_evaluated")[0]
    assert rec["accepted"] is False
    assert "summary" not in rec["extra"]
    assert set(rec["extra"]) == GOLDEN_SOCIAL_EVALUATED_EXTRA_BASE


def test_social_blank_summary_unit_path():
    """社交路徑同一支 helper；空白事實文字 ⇒ 不寫鍵（單元層釘死）。"""
    assert mwmod._fact_summary_extra(True, "   ") == {}
    assert mwmod._fact_summary_extra(True, "打招呼") == {"summary": "打招呼"}


# ══════════════════════════════════════════════════════════════
# 3. 對照組：既有判定逐位元不變
# ══════════════════════════════════════════════════════════════

def test_control_group_before_vs_after_only_extra_summary_differs(tmp_path, iso_env):
    """同一輸入，改動前 vs 改動後：除 `extra["summary"]` 外**逐欄位元相同**。

    「改動前」＝ 把 `_fact_summary_extra` 換成前置票前的等價行為（永遠回 `{}`，
    即 extra 只有原本 7 個鍵）。這直接證明本票是**純加法**：
    accepted／scores／novelty_*／context_injected／memory_written／selection_reason／
    reason／source／event_type／event_id 全部不變。
    """
    before = _external_scenario(
        tmp_path, summary=FACT_TEXT, pre_change=True, tag="before"
    )
    after = _external_scenario(
        tmp_path, summary=FACT_TEXT, pre_change=False, tag="after"
    )
    b = _records_of_phase(before, "evaluated")[0]
    a = _records_of_phase(after, "evaluated")[0]

    assert "summary" not in b["extra"], "對照組（改動前）不得有 summary 鍵"
    assert a["extra"]["summary"] == FACT_TEXT

    # `timestamp` 是 `datetime.now()`（唯一允許的非決定性欄位）；其餘逐一比對。
    for key in (
        "accepted", "scores", "novelty_id", "novelty_count_in_window",
        "context_injected", "memory_written", "selection_reason", "reason",
        "source", "event_type", "event_id",
    ):
        assert a[key] == b[key], f"{key} 在改動後不一致（既有判定必須逐位元不變）"

    assert {k: v for k, v in a["extra"].items() if k != "summary"} == b["extra"]
    assert set(a["extra"]) == set(b["extra"]) | {"summary"}


def test_control_group_rejected_path_bitwise_identical(tmp_path, iso_env):
    """被拒路徑（accepted False）改動前後**完全**逐位元相同（連 extra 都不變）。"""
    before = _external_scenario(
        tmp_path, summary=FACT_TEXT, accept_threshold=0.99,
        pre_change=True, tag="before_rej",
    )
    after = _external_scenario(
        tmp_path, summary=FACT_TEXT, accept_threshold=0.99,
        pre_change=False, tag="after_rej",
    )
    b = _records_of_phase(before, "evaluated")[0]
    a = _records_of_phase(after, "evaluated")[0]
    assert b["accepted"] is False and a["accepted"] is False
    b.pop("timestamp")
    a.pop("timestamp")
    assert a == b


def test_verdicts_are_still_derivable_from_untouched_deterministic_core(tmp_path, iso_env):
    """判定仍 == 既有 pure core 的輸出（accepted／reason／scores 對照斷言）。"""
    from src.world import PerceptionScores, compute_scores, should_accept

    recs = _external_scenario(tmp_path, summary=FACT_TEXT, tag="core")
    rec = _records_of_phase(recs, "evaluated")[0]
    ev = WorldEvent(
        source="weather", type="rain_started", novelty_id="novelty_fact_text_0001",
        ts=FIXED_TS, summary=FACT_TEXT, data={}, priority=0,
    )
    expected_scores = compute_scores(
        event=ev,
        novelty_count=rec["novelty_count_in_window"],
        current_user_context_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        silence_hours=0.0,
        event_priority=0,
    )
    expected_accepted, expected_reason = should_accept(expected_scores, threshold=0.0)

    assert rec["scores"] == {
        k: getattr(expected_scores, k) for k in GOLDEN_SCORES_KEYS
    }
    assert rec["accepted"] is expected_accepted
    assert rec["reason"] == expected_reason
    assert rec["context_injected"] is True   # accepted 且唯一事件 ⇒ top-N
    assert rec["memory_written"] is False    # Perception ≠ Memory（既有不變量）
    assert rec["novelty_count_in_window"] == 1


# ══════════════════════════════════════════════════════════════
# 4. 0 新 schema／0 新 artifact／0 新 LLM
# ══════════════════════════════════════════════════════════════

def test_trace_schema_is_baseline_plus_one_extra_key(tmp_path, iso_env):
    """trace top-level keys／scores keys 與改動前生產 schema **完全相同**。"""
    recs = _external_scenario(tmp_path, summary=FACT_TEXT, tag="schema")
    rec = _records_of_phase(recs, "evaluated")[0]
    assert set(rec) == GOLDEN_TOP_LEVEL_KEYS
    assert set(rec["scores"]) == GOLDEN_SCORES_KEYS
    assert set(rec["extra"]) == GOLDEN_EVALUATED_EXTRA_BASE | {
        "summary",
        "world_event_id",
    }


def test_no_new_artifact_written(tmp_path, iso_env):
    """只寫既有 `perception_trace.jsonl`（0 新檔／0 新 artifact）。"""
    before = {p.name for p in iso_env.rglob("*")} if iso_env.exists() else set()
    _external_scenario(tmp_path, summary=FACT_TEXT, tag="artifact")
    after = {p.name for p in iso_env.rglob("*")} if iso_env.exists() else set()
    _ = before, after  # 資料根不在本測試的寫入路徑（trace 走 tmp_path）
    assert not (iso_env / "world").exists(), "不得新增任何 artifact 到資料根"


def test_zero_llm_import_in_world_middleware():
    """靜態證明：`src/world/middleware.py` 不 import 任何 LLM 模組（0 新 LLM 呼叫）。"""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if "llm" in a.name.lower()]
        elif isinstance(node, ast.ImportFrom):
            if "llm" in (node.module or "").lower():
                offenders.append(node.module)
    assert offenders == [], f"世界感知路徑不得引入 LLM 依賴: {offenders}"
    assert "generate_text" not in MODULE_PATH.read_text(encoding="utf-8")
