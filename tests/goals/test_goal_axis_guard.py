"""
tests/goals/test_goal_axis_guard.py — GOAL-AXIS-GUARD-1（P1 自軸豁免）驗收測試

原則（Owner 2026-10-11, DOC-RULING-1 落地）: Bryan 缺席時 P1（signal 5,
bryan last-seen 超時）只掛起 Bryan 軸 goal，自軸（自由生長）豁免。

覆蓋（全合成 tmp, SOUL_OS_DATA_DIR 隔離，0 生產 data 寫入）:
  1. Bryan 缺席 → B 軸 ACTIVE/IN_PROGRESS 轉 SUSPENDED（照舊）
  2. Bryan 缺席 → S 軸 ACTIVE/IN_PROGRESS 保持不動（豁免）
  3. 終態（COMPLETED）兩軸皆不動
  4. 缺席解除（喚醒側）邏輯不動：Bryan 回來 → SUSPENDED 喚醒
  5. P1 以外路徑不動：signal 6（not_transmit streak）仍掛起 S 軸；
     ABANDON 超時仍適用 S 軸

運行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_goal_axis_guard.py -q
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_ABANDONED,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    Goal,
)
from src.goals.motive_provider import (
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root

AGENT = "agent_axis_guard"


def _utc(hour: int = 12, day: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour, tzinfo=timezone.utc) + timedelta(days=day)


def _local(hour: int = 12, day: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _make_goal(
    goal_id: str,
    axis: str = AXIS_BRYAN,
    state: str = GOAL_STATE_ACTIVE,
    now_ts: float | None = None,
    **kw,
) -> Goal:
    ts = now_ts if now_ts is not None else time.time()
    return Goal(
        goal_id=goal_id,
        agent_id=AGENT,
        axis=axis,
        title="軸守衛測試目標",
        description="",
        seed_source_ref="rel:test-axis-guard",
        state=state,
        state_updated_at=ts,
        created_at=ts,
        **kw,
    )


def _provider(tmp_path: Path) -> tuple[GoalMotiveProvider, GraphStore]:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    return GoalMotiveProvider(agent_id=AGENT, store=store), store


def _write_last_seen(tmp_path: Path, ago_hours: float) -> None:
    path = tmp_path / "state" / "bryan_last_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(timezone.utc) - timedelta(hours=ago_hours)).isoformat()
    path.write_text(json.dumps({"last_recv_ts": ts}), encoding="utf-8")


import pytest


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    yield tmp_path
    reset_goal_providers()
    reset_data_root()


def _states(store) -> dict:
    return {g.goal_id: (g.axis, g.state) for g in store.get_goals(AGENT)}


class TestP1SelfAxisExempt:
    def test_bryan_absent_suspends_bryan_axis(self, iso_env):
        """Bryan 缺席（> 4h）→ B 軸 ACTIVE/IN_PROGRESS 照舊轉 SUSPENDED。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=6)
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gb1" + "0" * 28, axis=AXIS_BRYAN))
        store.upsert_goal(_make_goal(
            "gb2" + "0" * 28, axis=AXIS_BRYAN,
            state=GOAL_STATE_IN_PROGRESS, advance_count=1,
        ))
        changed = provider.apply_interrupt_signals(now=real_now)
        assert sorted(changed) == ["gb1" + "0" * 28, "gb2" + "0" * 28]
        states = _states(store)
        assert states["gb1" + "0" * 28] == (AXIS_BRYAN, GOAL_STATE_SUSPENDED)
        assert states["gb2" + "0" * 28] == (AXIS_BRYAN, GOAL_STATE_SUSPENDED)

    def test_bryan_absent_exempts_self_axis(self, iso_env):
        """Bryan 缺席（> 4h）→ S 軸 ACTIVE/IN_PROGRESS 豁免，保持不動。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=6)
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gs1" + "0" * 28, axis=AXIS_SELF))
        store.upsert_goal(_make_goal(
            "gs2" + "0" * 28, axis=AXIS_SELF,
            state=GOAL_STATE_IN_PROGRESS, advance_count=1,
        ))
        changed = provider.apply_interrupt_signals(now=real_now)
        assert changed == []
        states = _states(store)
        assert states["gs1" + "0" * 28] == (AXIS_SELF, GOAL_STATE_ACTIVE)
        assert states["gs2" + "0" * 28] == (AXIS_SELF, GOAL_STATE_IN_PROGRESS)

    def test_bryan_absent_mixed_axes(self, iso_env):
        """混合軸：B 軸掛起、S 軸豁免，changed 只含 B 軸。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=6)
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gm1" + "0" * 28, axis=AXIS_BRYAN))
        store.upsert_goal(_make_goal("gm2" + "0" * 28, axis=AXIS_SELF))
        changed = provider.apply_interrupt_signals(now=real_now)
        assert changed == ["gm1" + "0" * 28]
        states = _states(store)
        assert states["gm1" + "0" * 28] == (AXIS_BRYAN, GOAL_STATE_SUSPENDED)
        assert states["gm2" + "0" * 28] == (AXIS_SELF, GOAL_STATE_ACTIVE)

    def test_terminal_states_untouched_both_axes(self, iso_env):
        """終態兩軸皆不動（豁免邏輯不影響終態語義）。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=6)
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal(
            "gtb" + "0" * 28, axis=AXIS_BRYAN, state=GOAL_STATE_COMPLETED))
        store.upsert_goal(_make_goal(
            "gts" + "0" * 28, axis=AXIS_SELF, state=GOAL_STATE_COMPLETED))
        assert provider.apply_interrupt_signals(now=real_now) == []
        states = _states(store)
        assert states["gtb" + "0" * 28] == (AXIS_BRYAN, GOAL_STATE_COMPLETED)
        assert states["gts" + "0" * 28] == (AXIS_SELF, GOAL_STATE_COMPLETED)


class TestWakeupSideUnchanged:
    def test_bryan_return_wakes_suspended(self, iso_env):
        """缺席解除邏輯不動：Bryan 回來（last_seen < 4h）→ SUSPENDED 喚醒。"""
        t0 = _local(10)
        path = iso_env / "state" / "bryan_last_seen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "last_recv_ts": (t0 + timedelta(minutes=30)).isoformat(),
        }), encoding="utf-8")
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal(
            "gw1" + "0" * 28, axis=AXIS_BRYAN,
            state=GOAL_STATE_SUSPENDED, now_ts=t0.timestamp(),
            suspend_snapshot=json.dumps({"reason": "t", "ts": t0.timestamp()}),
        ))
        changed = provider.scheduled_wakeup_scan(now=t0 + timedelta(hours=2))
        assert changed == ["gw1" + "0" * 28]
        assert _states(store)["gw1" + "0" * 28] == (AXIS_BRYAN, GOAL_STATE_ACTIVE)


class TestNonP1PathsUnchanged:
    def test_signal6_still_suspends_self_axis(self, iso_env):
        """P1 以外不動：signal 6（連續 not_transmit）仍掛起 S 軸。"""
        from src.goals.motive_provider import GOAL_SUSPEND_NOT_TRANSMIT_STREAK
        from src.soul.decision import DecisionResult
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("g61" + "0" * 28, axis=AXIS_SELF))
        t0 = _utc(10)
        m = provider.assemble_candidate(now=t0)
        assert m is not None
        last = None
        for _ in range(GOAL_SUSPEND_NOT_TRANSMIT_STREAK):
            last = provider.on_decision(m, DecisionResult(
                decision="do_nothing", transmit=False,
                reason="test", motive_id="m1",
            ), now=t0)
        assert last == GOAL_STATE_SUSPENDED
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_SUSPENDED
        assert json.loads(got.suspend_snapshot)["reason"] == "decision_not_transmit_streak"

    def test_takeover_still_suspends_self_axis(self, iso_env):
        """P1 以外不動：suspend_on_takeover（信號 1/2）仍掛起 S 軸。
        （註：ABANDON 超時路徑既有回歸在基線即紅，屬既有問題，
        見 test_goal_engine.py::TestInterruptSignals::test_timeout_abandons_in_progress，
        本工單不碰。）"""
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gt1" + "0" * 28, axis=AXIS_SELF))
        store.upsert_goal(_make_goal(
            "gt2" + "0" * 28, axis=AXIS_SELF, state=GOAL_STATE_IN_PROGRESS))
        store.upsert_goal(_make_goal(
            "gt3" + "0" * 28, axis=AXIS_SELF, state=GOAL_STATE_COMPLETED))
        changed = provider.suspend_on_takeover(reason="user_message_burst")
        assert sorted(changed) == ["gt1" + "0" * 28, "gt2" + "0" * 28]
        states = _states(store)
        assert states["gt1" + "0" * 28] == (AXIS_SELF, GOAL_STATE_SUSPENDED)
        assert states["gt2" + "0" * 28] == (AXIS_SELF, GOAL_STATE_SUSPENDED)
        assert states["gt3" + "0" * 28] == (AXIS_SELF, GOAL_STATE_COMPLETED)
