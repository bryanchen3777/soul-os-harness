# tests/test_world_fact_text_3_weather_rejected.py
# WORLD-FACT-TEXT-3 P1（2026-10-11，Owner 已批）— 契約可測斷言。
#
# 規格：`src/world/middleware.py` `_fact_summary_extra` 放寬——
#   `source == "weather"` 時即使 `accepted is not True`（被拒）也寫
#   `extra["summary"]`；news／social／其他 source 被拒仍不寫（一字不動）。
#   空白／非字串 ⇒ 不寫鍵、寫入值截斷 <= 200 字元、評分／門檻零變動。
#
# 隔離：`tests/conftest.py` 的 autouse fixture 已把 `SOUL_OS_DATA_DIR` 指向
# per-test tmp；本檔另以 `monkeypatch.setenv` ＋ `reset_data_root()` 顯式隔離。
# **0 生產 `data/**` 寫入**、**0 真實 LLM 呼叫**、**0 網路**。
from __future__ import annotations

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
from src.world import (  # noqa: E402
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)
from src.world import middleware as mwmod  # noqa: E402

#: 讀取端口徑（`src/soul/life_thread_origins.py:103` MAX_FACT_CHARS = 200）。
CONSUMER_MAX_FACT_CHARS = 200

#: 固定時間戳（determinism；validation 要求 ISO 8601 UTC）。
FIXED_TS = "2026-10-11T09:30:00+00:00"

#: 合成 weather 事實文字／合成 news 事實文字。
WEATHER_FACT = "外面開始下雨了，氣溫掉到 18 度。"
NEWS_FACT = "合成測試用新聞快訊：本週天氣轉涼。"
WEATHER_FACT_LONG = "雨" * 250

AGENT = "agent_ruka"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """全程禁止對外連線：任何 outbound 連線嘗試即 fail（證明本票 0 網路）。"""
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


def _run(coro):
    """在 sync test 內跑 async coroutine。"""
    import asyncio
    return asyncio.run(coro)


def _make_enriched_event(agent_id: str = AGENT, draft: str = "") -> SoulEvent:
    """建一個 AGENT_INTENT_ENRICHED event（觸發 evaluate + 寫 trace）。"""
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


def _scenario(
    tmp_path: Path,
    *,
    source: str,
    type_: str,
    summary: str,
    novelty_id: str,
    accept_threshold: float = 0.0,
    tag: str = "x",
) -> List[Dict[str, Any]]:
    """跑一次「外部世界事件 → evaluated」路徑，回傳全部 trace records。"""
    trace_path = tmp_path / f"trace_wft3_{tag}.jsonl"
    mw = _make_middleware(trace_path, accept_threshold=accept_threshold)
    ev = WorldEvent(
        source=source,
        type=type_,
        novelty_id=novelty_id,
        ts=FIXED_TS,
        summary=summary,
        data={},
        priority=0,
    )
    _run(mw.process_world_event_direct(ev))
    _run(mw.handle_event(_make_enriched_event()))
    return _read_jsonl(trace_path)


# ══════════════════════════════════════════════════════════════
# 1. weather 被拒也寫 summary（P1 放寬本體）
# ══════════════════════════════════════════════════════════════

def test_weather_rejected_writes_summary(tmp_path, iso_env):
    """weather accepted == False ⇒ `extra["summary"]` 照寫（P1 放寬）。"""
    recs = _scenario(
        tmp_path, source="weather", type_="rain_started",
        summary=WEATHER_FACT, novelty_id="novelty_wft3_weather_rej_0001",
        accept_threshold=0.99, tag="weather_rej",
    )
    evaluated = _records_of_phase(recs, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is False
    assert rec["extra"]["summary"] == WEATHER_FACT
    assert len(rec["extra"]["summary"]) <= CONSUMER_MAX_FACT_CHARS


def test_weather_rejected_summary_truncated_to_200_chars(tmp_path, iso_env):
    """weather 被拒且事實文字過長 ⇒ 截斷到 <= 200 字元（同 accepted 路徑規則）。"""
    recs = _scenario(
        tmp_path, source="weather", type_="rain_started",
        summary=WEATHER_FACT_LONG, novelty_id="novelty_wft3_weather_rej_0002",
        accept_threshold=0.99, tag="weather_rej_trunc",
    )
    rec = _records_of_phase(recs, "evaluated")[0]
    assert rec["accepted"] is False
    text = rec["extra"]["summary"]
    assert len(text) == CONSUMER_MAX_FACT_CHARS
    assert text == WEATHER_FACT_LONG[:CONSUMER_MAX_FACT_CHARS]


def test_weather_rejected_blank_summary_never_writes_key(tmp_path, iso_env):
    """weather summary 純空白 ⇒ validation 擋下且**任何** trace 都無該鍵（放寬不含空白）。"""
    recs = _scenario(
        tmp_path, source="weather", type_="rain_started",
        summary="   ", novelty_id="novelty_wft3_weather_rej_blank",
        tag="weather_rej_blank",
    )
    assert recs, "validation reject 仍必須寫 trace（既有行為）"
    for rec in recs:
        assert "summary" not in (rec.get("extra") or {})
        assert rec["accepted"] is False


# ══════════════════════════════════════════════════════════════
# 2. news 行為不變（被拒不寫／接受寫）
# ══════════════════════════════════════════════════════════════

def test_news_rejected_still_does_not_write_summary(tmp_path, iso_env):
    """news accepted == False ⇒ **不寫** `extra["summary"]`（P1 未動 news 路徑）。"""
    recs = _scenario(
        tmp_path, source="news", type_="news_event",
        summary=NEWS_FACT, novelty_id="novelty_wft3_news_rej_0001",
        accept_threshold=0.99, tag="news_rej",
    )
    evaluated = _records_of_phase(recs, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is False
    assert "summary" not in rec["extra"]


def test_news_accepted_still_writes_summary(tmp_path, iso_env):
    """news accepted == True ⇒ `extra["summary"]` 照寫（既有行為不變）。"""
    recs = _scenario(
        tmp_path, source="news", type_="news_event",
        summary=NEWS_FACT, novelty_id="novelty_wft3_news_acc_0001",
        tag="news_acc",
    )
    evaluated = _records_of_phase(recs, "evaluated")
    assert len(evaluated) == 1
    rec = evaluated[0]
    assert rec["accepted"] is True
    assert rec["extra"]["summary"] == NEWS_FACT


# ══════════════════════════════════════════════════════════════
# 3. 單元矩陣（含 source 參數）＋ 評分／門檻零變動
# ══════════════════════════════════════════════════════════════

def test_fact_summary_extra_source_matrix():
    """`_fact_summary_extra` 逐格斷言：只有 weather 被拒才寫，其餘 source 被拒不寫。"""
    f = mwmod._fact_summary_extra
    # weather 放寬
    assert f(False, WEATHER_FACT, "weather") == {"summary": WEATHER_FACT}
    assert f(None, WEATHER_FACT, "weather") == {"summary": WEATHER_FACT}
    assert f(0, WEATHER_FACT, "weather") == {"summary": WEATHER_FACT}
    assert f(False, "  " + WEATHER_FACT_LONG + "  ", "weather") == {
        "summary": WEATHER_FACT_LONG[:CONSUMER_MAX_FACT_CHARS]
    }
    assert f(False, "   ", "weather") == {}
    assert f(False, "", "weather") == {}
    assert f(False, None, "weather") == {}
    assert f(False, 123, "weather") == {}
    # 非 weather：被拒一律不寫（行為一字不動）
    assert f(False, NEWS_FACT, "news") == {}
    assert f(False, NEWS_FACT, "social") == {}
    assert f(False, NEWS_FACT, "calendar") == {}
    assert f(False, NEWS_FACT, "synthetic") == {}
    assert f(False, NEWS_FACT) == {}
    assert f(False, NEWS_FACT, None) == {}
    # accepted：全 source 照舊寫
    assert f(True, WEATHER_FACT, "weather") == {"summary": WEATHER_FACT}
    assert f(True, NEWS_FACT, "news") == {"summary": NEWS_FACT}
    assert f(True, "打招呼", "social") == {"summary": "打招呼"}
    assert f(True, "   ", "weather") == {}


def test_weather_rejected_verdict_matches_pure_core(tmp_path, iso_env):
    """weather 被拒的判定仍 == 既有 pure core 輸出（評分／門檻零變動；只多 summary 鍵）。"""
    from src.world import PerceptionScores, compute_scores, should_accept

    recs = _scenario(
        tmp_path, source="weather", type_="rain_started",
        summary=WEATHER_FACT, novelty_id="novelty_wft3_weather_core_0001",
        accept_threshold=0.99, tag="weather_core",
    )
    rec = _records_of_phase(recs, "evaluated")[0]
    ev = WorldEvent(
        source="weather", type="rain_started",
        novelty_id="novelty_wft3_weather_core_0001",
        ts=FIXED_TS, summary=WEATHER_FACT, data={}, priority=0,
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
    expected_accepted, expected_reason = should_accept(
        expected_scores, threshold=0.99
    )

    assert rec["scores"] == {
        k: getattr(expected_scores, k)
        for k in (
            "emotional_significance", "novelty", "personal_significance",
            "priority_boost", "relevance", "temporal_significance",
        )
    }
    assert rec["accepted"] is expected_accepted is False
    assert rec["reason"] == expected_reason
    # P1 唯一差異：多 summary 鍵
    assert rec["extra"]["summary"] == WEATHER_FACT
