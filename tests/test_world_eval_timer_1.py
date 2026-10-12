# tests/test_world_eval_timer_1.py
# WORLD-EVAL-TIMER-1（2026-10-12，Owner 已選 B）— 獨立定時評分任務契約斷言。
#
# 背景：評分只跑在 AGENT_INTENT_ENRICHED 內，10-07 後零對話流入 → 5 天 0 評分。
# 本票新增與對話無關的定時評分（`WorldPerceptionMiddleware.evaluate_received_pending`，
# 由 scheduler 30s wake 並列分支 `_fire_world_timer_eval` 驅動）：
#   無對話時 world 事件照常評分落盤；有對話時行為不變（不雙寫）。
#
# 本檔斷言（合成事件，全部 tmp 隔離）：
#   1. 無 ENRICHED 時定時任務產 evaluated 行（accepted＋rejected 各一）。
#   2. 已有 evaluated（對話路徑或首輪定時）時定時不重寫（去重）。
#   3. Frozen 不動：SCORE_WEIGHTS／accept_threshold=0.35／compute_scores＋
#      should_accept 簽名／heartbeat（scheduler sleep×2／while×1／_fire_heartbeat
#      仍在）；定時路徑 0 bus.publish（不經 AGENT_INTENT，不觸 LLM/SpeakerToken）。
#   4. scheduler 分支 fail-closed：hook 未注入空轉、hook 異常不拋。
#
# 隔離：per-test tmp（`SOUL_OS_DATA_DIR`＋`reset_data_root()`）；0 生產 `data/**`
# 寫入、0 真實 LLM、0 網路（autouse 反網路 fixture）。
from __future__ import annotations

import asyncio
import inspect
import json
import socket
import sys
from datetime import datetime, timezone
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
from src.soul.scheduler import SoulScheduler  # noqa: E402
from src.world import (  # noqa: E402
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)
from src.world.perception import (  # noqa: E402
    DEFAULT_ACCEPT_THRESHOLD,
    SCORE_WEIGHTS,
    SELECTION_BELOW_BUDGET,
    SELECTION_REJECTED_AT_THRESHOLD,
    SELECTION_SELECTED_TOP_N,
    compute_scores,
    should_accept,
)

FIXED_TS = "2026-10-12T12:00:00+00:00"
AGENT = "agent_ruka"

GOLDEN_TOP_LEVEL_KEYS = {
    "accepted", "context_injected", "event_id", "event_type", "extra",
    "memory_written", "novelty_count_in_window", "novelty_id", "reason",
    "scores", "selection_reason", "source", "timestamp",
}
GOLDEN_SCORES_KEYS = {
    "emotional_significance", "novelty", "personal_significance",
    "priority_boost", "relevance", "temporal_significance",
}


# ══════════════════════════════════════════════════════════════
# fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """全程禁止對外連線（證明本票 0 網路；不封 socketpair，見既有檔同款註解）。"""
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
    import asyncio as _aio
    return _aio.run(coro)


def _make_bus():
    bus = MagicMock()
    calls: List[Any] = []

    async def _capture(ev):
        calls.append(ev)
        return None

    bus.publish = _capture
    bus.captured = calls  # type: ignore[attr-defined]
    return bus


def _make_middleware(trace_path: Path):
    """門檻用預設（0.35，Frozen；本票不斷言改門檻，只斷言不動）。"""
    bus = _make_bus()
    mw = WorldPerceptionMiddleware(
        bus=bus,
        state=WorldPerceptionState(),
        trace_writer=WorldPerceptionTraceWriter(trace_path),
    )
    return mw, bus


def _make_enriched_event(agent_id: str = AGENT, draft: str = "") -> SoulEvent:
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


def _feed_received(mw: WorldPerceptionMiddleware, ev: WorldEvent) -> None:
    _run(mw.process_world_event_direct(ev))


# ══════════════════════════════════════════════════════════════
# 1. 無 ENRICHED 時定時任務產 evaluated 行
# ══════════════════════════════════════════════════════════════

class TestTimerEvaluatesWithoutDialogue:
    def test_accepted_calendar_event_gets_evaluated_row(self, tmp_path, iso_env):
        """accepted 事件：定時產 evaluated 行，格式沿 Pass 3，0 對話、0 publish。"""
        trace_path = tmp_path / "trace_timer_acc.jsonl"
        mw, bus = _make_middleware(trace_path)
        ev = WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id="novelty_timer_cal_0001",
            ts=FIXED_TS,
            summary="晚上 22:00 與 Bryan 的週記對話",
            data={},
            priority=0,
        )
        _feed_received(mw, ev)
        assert len(_records_of_phase(_read_jsonl(trace_path), "received")) == 1
        assert _records_of_phase(_read_jsonl(trace_path), "evaluated") == []
        published_before = len(bus.captured)

        n = mw.evaluate_received_pending()

        assert n == 1
        # 定時路徑 0 bus.publish（不經 AGENT_INTENT，不觸 LLM/SpeakerToken）。
        assert len(bus.captured) == published_before
        recs = _read_jsonl(trace_path)
        evaluated = _records_of_phase(recs, "evaluated")
        assert len(evaluated) == 1
        rec = evaluated[0]

        # 與純函數直算 parity（中性上下文；簽名不動）。
        expected_scores = compute_scores(
            event=ev,
            novelty_count=1,
            current_user_context_keywords=[],
            temporal_salience="low",
            anticipatory_flavor="none",
            vulnerability_window=False,
            silence_hours=0.0,
            event_priority=0,
        )
        expected_accepted, _ = should_accept(
            expected_scores, threshold=DEFAULT_ACCEPT_THRESHOLD
        )
        assert expected_accepted is True  # 本用例選 accepted 事件
        assert rec["accepted"] is True
        assert rec["novelty_id"] == "novelty_timer_cal_0001"
        assert rec["novelty_count_in_window"] == 1
        for k in GOLDEN_SCORES_KEYS:
            assert rec["scores"][k] == pytest.approx(expected_scores.__dict__[k])
        assert rec["context_injected"] is False  # 定時不向任何 prompt 注入
        assert rec["memory_written"] is False
        assert rec["selection_reason"].startswith(SELECTION_SELECTED_TOP_N)
        assert set(rec.keys()) == GOLDEN_TOP_LEVEL_KEYS
        # extra：沿 Pass 3＋timer 標記；無 agent（定時無 agent）；fact text 照舊。
        extra = rec["extra"]
        assert extra["phase"] == "evaluated"
        assert extra["evaluation_trigger"] == "timer"
        assert "agent_id" not in extra
        assert extra["temporal_salience"] == "low"
        assert extra["anticipatory_flavor"] == "none"
        assert extra["vulnerability_window"] is False
        assert extra["user_keyword_count"] == 0
        assert extra["world_event_priority"] == 0
        assert extra["summary"] == "晚上 22:00 與 Bryan 的週記對話"
        assert extra["world_event_id"] == "world:calendar:novelty_timer_cal_0001"

    def test_rejected_celebrity_news_gets_evaluated_row_without_summary(
        self, tmp_path, iso_env
    ):
        """rejected 事件：定時照常落 evaluated 行（threshold 拒），不寫 summary。"""
        trace_path = tmp_path / "trace_timer_rej.jsonl"
        mw, bus = _make_middleware(trace_path)
        ev = WorldEvent(
            source="news",
            type="celebrity_news",
            novelty_id="novelty_timer_celeb_0001",
            ts=FIXED_TS,
            summary="某明星 gener penggemar 八卦",
            data={},
            priority=0,
        )
        _feed_received(mw, ev)
        published_before = len(bus.captured)

        n = mw.evaluate_received_pending()

        assert n == 1
        assert len(bus.captured) == published_before
        evaluated = _records_of_phase(_read_jsonl(trace_path), "evaluated")
        assert len(evaluated) == 1
        rec = evaluated[0]
        assert rec["accepted"] is False
        assert rec["selection_reason"] == SELECTION_REJECTED_AT_THRESHOLD
        assert rec["context_injected"] is False
        assert "summary" not in rec["extra"]  # news 被拒不寫（WORLD-FACT-TEXT 原則）
        assert rec["extra"]["evaluation_trigger"] == "timer"


# ══════════════════════════════════════════════════════════════
# 2. 已有 evaluated 不重寫（不雙寫）
# ══════════════════════════════════════════════════════════════

class TestTimerSkipsAlreadyEvaluated:
    def test_skips_novelty_already_evaluated_by_dialogue(self, tmp_path, iso_env):
        """對話路徑已 evaluated ⇒ 定時跳過（有對話時行為不變）。"""
        trace_path = tmp_path / "trace_timer_dedup_dialogue.jsonl"
        mw, bus = _make_middleware(trace_path)
        ev = WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id="novelty_timer_dedup_0001",
            ts=FIXED_TS,
            summary="晚上 22:00 與 Bryan 的週記對話",
            data={},
            priority=0,
        )
        _feed_received(mw, ev)
        _run(mw.handle_event(_make_enriched_event()))
        assert len(_records_of_phase(_read_jsonl(trace_path), "evaluated")) == 1
        published_before = len(bus.captured)

        n = mw.evaluate_received_pending()

        assert n == 0
        assert len(bus.captured) == published_before
        assert len(_records_of_phase(_read_jsonl(trace_path), "evaluated")) == 1

    def test_second_timer_run_is_idempotent(self, tmp_path, iso_env):
        """首輪定時已寫 ⇒ 次輪 0 新行（冪等）。"""
        trace_path = tmp_path / "trace_timer_idem.jsonl"
        mw, _ = _make_middleware(trace_path)
        _feed_received(mw, WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id="novelty_timer_idem_0001",
            ts=FIXED_TS,
            summary="晚上 22:00 與 Bryan 的週記對話",
            data={},
            priority=0,
        ))
        assert mw.evaluate_received_pending() == 1
        assert mw.evaluate_received_pending() == 0
        assert len(_records_of_phase(_read_jsonl(trace_path), "evaluated")) == 1

    def test_mixed_pending_only_unevaluated_gets_written(self, tmp_path, iso_env):
        """混合：一筆已評（對話）＋一筆未評 ⇒ 只寫未評那筆。"""
        trace_path = tmp_path / "trace_timer_mixed.jsonl"
        mw, _ = _make_middleware(trace_path)
        _feed_received(mw, WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="novelty_timer_mixed_old", ts=FIXED_TS,
            summary="舊事件", data={}, priority=0,
        ))
        _run(mw.handle_event(_make_enriched_event()))
        _feed_received(mw, WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="novelty_timer_mixed_new", ts=FIXED_TS,
            summary="新事件", data={}, priority=0,
        ))

        n = mw.evaluate_received_pending()

        assert n == 1
        evaluated = _records_of_phase(_read_jsonl(trace_path), "evaluated")
        assert len(evaluated) == 2
        timer_rows = [
            r for r in evaluated if (r.get("extra") or {}).get("evaluation_trigger") == "timer"
        ]
        assert len(timer_rows) == 1
        assert timer_rows[0]["novelty_id"] == "novelty_timer_mixed_new"

    def test_social_events_left_to_dialogue_path(self, tmp_path, iso_env):
        """社交事件跳過（social_evaluated 需 per-agent 上下文，留給對話路徑）。"""
        trace_path = tmp_path / "trace_timer_social.jsonl"
        mw, _ = _make_middleware(trace_path)
        mw.state.add(SocialWorldEvent(
            source="social",
            type="social",
            novelty_id="novelty_timer_social_0001",
            ts=FIXED_TS,
            summary="背景社交氛圍",
            data={},
            priority=0,
            actor_id="agent_miku",
            space_id=SPACE_LOUNGE,
            visibility=VISIBILITY_PUBLIC,
            event_type="greeting",
            content="大家好",
        ))

        assert mw.evaluate_received_pending() == 0
        assert _read_jsonl(trace_path) == []


# ══════════════════════════════════════════════════════════════
# 3. Frozen 不動＋簽名不動
# ══════════════════════════════════════════════════════════════

class TestTimerFrozenContracts:
    def test_scores_weights_and_threshold_untouched(self):
        assert DEFAULT_ACCEPT_THRESHOLD == 0.35
        assert SCORE_WEIGHTS == {
            "relevance": 0.30,
            "novelty": 0.20,
            "personal_significance": 0.25,
            "emotional_significance": 0.10,
            "temporal_significance": 0.15,
        }

    def test_pure_function_signatures_unchanged(self):
        assert list(inspect.signature(compute_scores).parameters) == [
            "event", "novelty_count", "current_user_context_keywords",
            "temporal_salience", "anticipatory_flavor", "vulnerability_window",
            "silence_hours", "event_priority",
        ]
        assert list(inspect.signature(should_accept).parameters) == [
            "scores", "threshold",
        ]

    def test_default_middleware_threshold_is_035(self, tmp_path, iso_env):
        mw, _ = _make_middleware(tmp_path / "trace_timer_thr.jsonl")
        assert mw.accept_threshold == 0.35

    def test_scheduler_heartbeat_and_loop_shape_untouched(self):
        src = (_REPO_ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
        assert src.count("asyncio.sleep(30)") == 2  # 既有基線＝主循環＋except
        body = src[src.find("async def _run_loop"):src.find("async def _goal_scan_all")]
        assert body.count("while ") == 1
        assert "async def _fire_heartbeat" in src  # 8/6 修法維持（機制保留）
        assert "await self._fire_world_timer_eval()" in body  # 本票並列分支
        assert "async def _fire_world_timer_eval" in src


# ══════════════════════════════════════════════════════════════
# 4. scheduler 分支 fail-closed
# ══════════════════════════════════════════════════════════════

class TestSchedulerTimerBranch:
    def test_hook_none_is_noop(self):
        sched = SoulScheduler()
        assert sched._world_eval_hook is None
        assert asyncio.run(sched._fire_world_timer_eval()) is None

    def test_hook_called_once(self):
        sched = SoulScheduler()
        calls: List[str] = []

        async def _hook() -> int:
            calls.append("x")
            return 2

        sched.set_world_eval_hook(_hook)
        asyncio.run(sched._fire_world_timer_eval())
        assert calls == ["x"]

    def test_hook_exception_is_swallowed(self):
        sched = SoulScheduler()

        async def _boom() -> int:
            raise RuntimeError("timer 評分炸了")

        sched.set_world_eval_hook(_boom)
        asyncio.run(sched._fire_world_timer_eval())  # 不拋＝fail-closed

    def test_below_budget_timer_row_shape(self, tmp_path, iso_env):
        """多筆 accepted 超 budget ⇒ 超出者 below_budget（與 ENRICHED 同式），皆不注入。"""
        trace_path = tmp_path / "trace_timer_budget.jsonl"
        mw, _ = _make_middleware(trace_path)
        for i in range(4):
            _feed_received(mw, WorldEvent(
                source="calendar", type="calendar_event",
                novelty_id=f"novelty_timer_b{i}", ts=FIXED_TS,
                summary=f"行事曆事件 {i}", data={}, priority=0,
            ))
        assert mw.evaluate_received_pending() == 4
        evaluated = _records_of_phase(_read_jsonl(trace_path), "evaluated")
        assert len(evaluated) == 4
        reasons = [r["selection_reason"] for r in evaluated]
        assert sum(1 for x in reasons if x.startswith(SELECTION_SELECTED_TOP_N)) == 3
        assert sum(1 for x in reasons if x == SELECTION_BELOW_BUDGET) == 1
        assert all(r["context_injected"] is False for r in evaluated)
