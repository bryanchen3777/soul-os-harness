"""
tests/goals/test_goal_engine.py — TG-2 Goal Engine 验收测试

覆盖（工单验收 4 项把关清单):
  1. Schema v8 迁移幂等性（v7 升级平滑 + goals 表建立 + facts/nodes 不受影响）
  2. motive_provider.py 严格 Plan B（零侵入 MotiveEngine 核心代码）
  3. 轮替配额生效（单心跳 ≤1 候选、N=3、同轴 ≤2、0 浮点数打分）
  4. 状态机 / 中断 / 完成判定 / 心跳接线

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_goal_engine.py -v
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.goals.motive_provider import (
    GOAL_MAX_ROTATION_STREAK,
    GOAL_QUOTA_WINDOW_SECONDS,
    GOAL_ROTATION_WINDOW,
    GOAL_SUSPEND_NOT_TRANSMIT_STREAK,
    GOAL_WAKE_FORCE_SECONDS,
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.memory.sage.graph_store import GraphStore, _SCHEMA_VERSION
from src.paths import reset_data_root
from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ABANDONED,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    Goal,
    InvalidGoalTransitionError,
    validate_goal_transition,
)

AGENT = "agent_t"

# ───────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────

def _utc(hour: int = 12, day: int = 0) -> datetime:
    """固定基准时间（UTC, 避免跨日/时段抖动）。"""
    return datetime(2026, 9, 6, hour, tzinfo=timezone.utc) + timedelta(days=day)


def _local(hour: int = 12, day: int = 0) -> datetime:
    """固定基准时间（系统本地时区 — quiet hours / 跨日以本地作息语义判定）。"""
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _make_goal(
    goal_id: str,
    axis: str = AXIS_BRYAN,
    title: str = "测试目标",
    state: str = GOAL_STATE_ACTIVE,
    now_ts: float | None = None,
    **kw,
) -> Goal:
    ts = now_ts if now_ts is not None else time.time()
    return Goal(
        goal_id=goal_id,
        agent_id=AGENT,
        axis=axis,
        title=title,
        description="",
        seed_source_ref="rel:test",
        state=state,
        state_updated_at=ts,
        created_at=ts,
        **kw,
    )


def _provider(tmp_path: Path, agent_id: str = AGENT) -> tuple[GoalMotiveProvider, GraphStore]:
    db = tmp_path / "memory" / agent_id / "graph.sqlite"
    store = GraphStore(db_path=db)
    return GoalMotiveProvider(agent_id=agent_id, store=store), store


def _decision(action: str):
    """构造 DecisionResult（SM-4 四元）。"""
    from src.soul.decision import DecisionResult
    return DecisionResult(
        decision=action,
        transmit=(action == "transmit"),
        reason="test",
        motive_id="m1",
    )


def _write_last_seen(tmp_path: Path, ago_hours: float) -> None:
    """写 Bry last_seen 信号文件（统一信号源 data/state/bryan_last_seen.json）。"""
    path = tmp_path / "state" / "bryan_last_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(timezone.utc) - timedelta(hours=ago_hours)).isoformat()
    path.write_text(json.dumps({"last_recv_ts": ts}), encoding="utf-8")


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """P0.5 隔离: SOUL_OS_DATA_DIR → tmp_path + 缓存重置。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    yield tmp_path
    reset_goal_providers()
    reset_data_root()


# ───────────────────────────────────────────────────────────
# 1. Schema v8 迁移幂等性
# ───────────────────────────────────────────────────────────

class TestSchemaV8Migration:
    """验收 1: v7 升级平滑无损耗 + goals 表建立 + facts/nodes 不受影响。"""

    def _build_v7_db(self, path: Path) -> float:
        """按 graph_store 历史迁移逐级建造一个完整 v7 库（含 schema_meta version=7）。"""
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_meta VALUES ('version', '7')")
        conn.execute("""
            CREATE TABLE facts (
                fact_id TEXT PRIMARY KEY, subject TEXT NOT NULL,
                predicate TEXT NOT NULL, object TEXT NOT NULL,
                timestamp REAL NOT NULL, weight REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'user',
                session_id TEXT NOT NULL DEFAULT ''
            )
        """)
        for ddl in (
            "ALTER TABLE facts ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE facts ADD COLUMN event_time REAL",
            "ALTER TABLE facts ADD COLUMN is_anchor INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE facts ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "ALTER TABLE facts ADD COLUMN merged_from TEXT",
            "ALTER TABLE facts ADD COLUMN merge_reason TEXT",
            "ALTER TABLE facts ADD COLUMN source_pair TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE facts ADD COLUMN inner_life_event_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE facts ADD COLUMN valid_from REAL",
            "ALTER TABLE facts ADD COLUMN invalidated_at REAL",
        ):
            conn.execute(ddl)
        ts = time.time()
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp,"
            " weight, valid_from) VALUES (?,?,?,?,?,?,?)",
            ("f1", AGENT, "likes", "coffee", ts, 1.0, ts),
        )
        conn.commit()
        conn.close()
        return ts

    def test_v7_upgrade_smooth_no_loss(self, iso_env):
        """v7 既有库 → GraphStore 打开 → v8: goals 表建立, 既有 facts/nodes 无损。"""
        db = iso_env / "memory" / AGENT / "graph.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        ts = self._build_v7_db(db)

        gs = GraphStore(db_path=db)

        # 版本升级 7 → 8
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        assert row[0] == str(_SCHEMA_VERSION) == "8"

        # goals 表 + 索引建立
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "goals" in tables
        indexes = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        assert "idx_goals_agent_state" in indexes

        # 既有 facts 无损（含时序列）
        facts = gs.get_all_facts(min_weight=0.0)
        assert len(facts) == 1
        assert facts[0].fact_id == "f1"
        assert facts[0].subject == AGENT
        assert facts[0].valid_from == ts
        assert facts[0].invalidated_at is None
        # networkx 内存图加载正常（subject + object 两个节点）
        assert gs.node_count == 2
        assert gs.edge_count == 1

        # goals 表可写入（v8 新能力）
        goal = _make_goal("ab" * 16, now_ts=ts)
        gs.upsert_goal(goal)
        assert len(gs.get_goals(AGENT)) == 1
        gs.close()

    def test_fresh_db_creates_v8(self, iso_env):
        """空库 → 直接建到 v8（幂等 CREATE IF NOT EXISTS）。"""
        db = iso_env / "memory" / AGENT / "graph.sqlite"
        gs = GraphStore(db_path=db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        assert row[0] == "8"
        assert "goals" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "facts" in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        gs.close()

    def test_reopen_idempotent(self, iso_env):
        """同一库重复打开（迁移幂等）不报错、数据不丢。"""
        db = iso_env / "memory" / AGENT / "graph.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        self._build_v7_db(db)
        for _ in range(3):
            gs = GraphStore(db_path=db)
            assert _SCHEMA_VERSION == 8
            assert len(gs.get_all_facts(min_weight=0.0)) == 1
            gs.close()


# ───────────────────────────────────────────────────────────
# 2. ledger 三方法（upsert / get / transition）
# ───────────────────────────────────────────────────────────

class TestGoalLedger:
    def test_upsert_idempotent_and_get_filter(self, iso_env):
        provider, store = _provider(iso_env)
        g1 = _make_goal("g1" + "0" * 29)
        g2 = _make_goal("g2" + "0" * 29, axis=AXIS_SELF)
        store.upsert_goal(g1)
        store.upsert_goal(g2)
        # upsert 幂等（同 id 覆盖; INSERT OR REPLACE 全字段覆盖, 故按 id 断言）
        g1b = _make_goal(g1.goal_id, title="覆盖标题", advance_count=3)
        store.upsert_goal(g1b)
        all_goals = store.get_goals(AGENT)
        assert len(all_goals) == 2
        by_id = {g.goal_id: g for g in all_goals}
        assert by_id[g1.goal_id].advance_count == 3
        assert by_id[g1.goal_id].title == "覆盖标题"
        # state 过滤（由 created_at 排序, 覆盖后顺序不保证 → 逐条断言）
        states = {g.goal_id: g.state for g in store.get_goals(AGENT, state=GOAL_STATE_ACTIVE)}
        assert states == {g1.goal_id: GOAL_STATE_ACTIVE, g2.goal_id: GOAL_STATE_ACTIVE}
        assert store.get_goals(AGENT, state=GOAL_STATE_COMPLETED) == []
        # per-agent 隔离（N3）
        assert store.get_goals("other_agent") == []

    def test_transition_goal_suspend_three_fields(self, iso_env):
        """SUSPENDED 无损: 只写 state + suspend_snapshot + state_updated_at。"""
        provider, store = _provider(iso_env)
        g = _make_goal("g3" + "0" * 29, advance_count=2,
                       completion_criteria=json.dumps({"kind": "interaction", "count": 2}))
        store.upsert_goal(g)
        store.transition_goal(g.goal_id, GOAL_STATE_SUSPENDED,
                              meta={"suspend_snapshot": json.dumps({"reason": "quiet"})})
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_SUSPENDED
        assert json.loads(got.suspend_snapshot)["reason"] == "quiet"
        assert got.advance_count == 2          # 0 计数重置
        assert got.seed_source_ref == "rel:test"  # 0 丢种子源引用
        assert got.completion_criteria is not None  # 0 丢 criteria
        assert got.state_updated_at >= g.state_updated_at

    def test_transition_goal_preserves_snapshot_on_resume(self, iso_env):
        """恢复 SUSPENDED→ACTIVE 时 suspend_snapshot 保留（无损语义, 仅启发）。"""
        provider, store = _provider(iso_env)
        g = _make_goal("g4" + "0" * 29)
        store.upsert_goal(g)
        store.transition_goal(g.goal_id, GOAL_STATE_SUSPENDED,
                              meta={"suspend_snapshot": "snap-1"})
        store.transition_goal(g.goal_id, GOAL_STATE_ACTIVE)
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_ACTIVE
        assert got.suspend_snapshot == "snap-1"

    def test_transition_unknown_goal_returns_false(self, iso_env):
        provider, store = _provider(iso_env)
        assert store.transition_goal("nope" * 8, GOAL_STATE_SUSPENDED) is False


# ───────────────────────────────────────────────────────────
# 3. 状态机（转移表 / 非法转移 / 终态无出边）
# ───────────────────────────────────────────────────────────

class TestGoalStateMachine:
    def test_valid_transitions_allowed(self):
        valid = [
            (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS),
            (GOAL_STATE_ACTIVE, GOAL_STATE_SUSPENDED),
            (GOAL_STATE_IN_PROGRESS, GOAL_STATE_SUSPENDED),
            (GOAL_STATE_IN_PROGRESS, GOAL_STATE_COMPLETED),
            (GOAL_STATE_IN_PROGRESS, GOAL_STATE_ABANDONED),
            (GOAL_STATE_SUSPENDED, GOAL_STATE_ACTIVE),
        ]
        for frm, to in valid:
            validate_goal_transition(frm, to)  # 不抛

    def test_invalid_transitions_rejected(self):
        invalid = [
            (GOAL_STATE_ACTIVE, GOAL_STATE_COMPLETED),   # 不跳态
            (GOAL_STATE_ACTIVE, GOAL_STATE_ABANDONED),   # 不跳态
            (GOAL_STATE_SUSPENDED, GOAL_STATE_COMPLETED),  # SUSPENDED 只能回 ACTIVE
            (GOAL_STATE_SUSPENDED, GOAL_STATE_ABANDONED),
            (GOAL_STATE_SUSPENDED, GOAL_STATE_IN_PROGRESS),
            (GOAL_STATE_COMPLETED, GOAL_STATE_ACTIVE),   # 终态无出边
            (GOAL_STATE_ABANDONED, GOAL_STATE_ACTIVE),   # 终态无出边
            (GOAL_STATE_COMPLETED, GOAL_STATE_IN_PROGRESS),
            (GOAL_STATE_ABANDONED, GOAL_STATE_SUSPENDED),
        ]
        for frm, to in invalid:
            with pytest.raises(InvalidGoalTransitionError):
                validate_goal_transition(frm, to)

    def test_store_rejects_invalid_transition(self, iso_env):
        provider, store = _provider(iso_env)
        g = _make_goal("g5" + "0" * 29, state=GOAL_STATE_COMPLETED)
        store.upsert_goal(g)
        with pytest.raises(InvalidGoalTransitionError):
            store.transition_goal(g.goal_id, GOAL_STATE_ACTIVE)


# ───────────────────────────────────────────────────────────
# 4. 轮替配额（单心跳 ≤1 / 24h 窗 / N=3 / 同轴 ≤2 / 防饿死）
# ───────────────────────────────────────────────────────────

class TestRotationQuota:
    """验收 3: 结构轮替配额生效, 0 数值打分。"""

    def test_single_candidate_per_heartbeat_and_24h_window(self, iso_env):
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gq" + "0" * 29))
        t0 = _utc(10)
        m1 = provider.assemble_candidate(now=t0)
        assert m1 is not None
        assert m1.provenance_ref == f"goal:{'gq' + '0' * 29}"
        # 同一心跳第二次 → 24h 配额窗内 → None
        assert provider.assemble_candidate(now=t0) is None
        # 窗内（+23h）依然挡
        assert provider.assemble_candidate(now=t0 + timedelta(hours=23)) is None
        # 窗过（+25h）→ 可再产
        m2 = provider.assemble_candidate(now=t0 + timedelta(hours=25))
        assert m2 is not None
        assert m2.provenance_ref.startswith("goal:")

    def test_quota_resets_across_windows(self, iso_env):
        provider, store = _provider(iso_env)
        g = _make_goal("gr" + "0" * 29)
        store.upsert_goal(g)
        t0 = _utc(10)
        produced = 0
        for day in range(0, 6):
            if provider.assemble_candidate(now=t0 + timedelta(days=day)) is not None:
                produced += 1
        # 24h 配额下每天至多 1 候选; 单轴场景含 2 次轮替放弃（streak 强制换轴失败）:
        # 序列 [产, 产, 弃, 产, 弃, 产] → 4 产（防饿死兜底保证不永久饥饿）
        assert produced == 4

    def test_two_axis_rotation_sequence(self, iso_env):
        """双轴轮替: bryan → self → bryan → self（N=3 窗口内轮替）。"""
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gb" + "0" * 29, axis=AXIS_BRYAN))
        store.upsert_goal(_make_goal("gs" + "0" * 29, axis=AXIS_SELF))
        t0 = _utc(10)
        axes = []
        for day in range(4):
            m = provider.assemble_candidate(now=t0 + timedelta(days=day))
            assert m is not None
            gid = m.provenance_ref[len("goal:"):]
            axis = "self" if gid.startswith("gs") else "bryan"
            axes.append(axis)
        assert axes == [AXIS_BRYAN, AXIS_SELF, AXIS_BRYAN, AXIS_SELF]

    def test_same_axis_streak_forces_other_axis(self, iso_env):
        """同轴连续 ≥2 → 强制另一轴; 另一轴无候选 → 放弃本心跳（保底）。"""
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gb" + "0" * 29, axis=AXIS_BRYAN))
        t0 = _utc(10)
        results = []
        for day in range(5):
            m = provider.assemble_candidate(now=t0 + timedelta(days=day))
            results.append(m is not None)
        # c1 bryan 产 / c2 bryan（轮替偏好 self 无候选 → fallback bryan）→ streak=2
        # c3 强制 self 无候选 → 放弃（skip=1）
        # c4 防饿死兜底（连续放弃 ≥2）→ 允许原轴再产
        # c5 强制 self 无候选 → 放弃
        assert results == [True, True, False, True, False]

    def test_exclude_last_candidate_goal_anti_monopoly(self, iso_env):
        """排除上一次已产候选的 goal（防单目标霸占）: 同轴两 goal 交替。"""
        provider, store = _provider(iso_env)
        g1 = _make_goal("gx1" + "0" * 28, axis=AXIS_BRYAN, title="目标一",
                        now_ts=time.time() - 100)
        g2 = _make_goal("gx2" + "0" * 28, axis=AXIS_BRYAN, title="目标二",
                        now_ts=time.time())
        # g1 last_advanced_at 更旧 → 优先; 但第二次装配排除上次候选 goal
        store.upsert_goal(g2)
        store.upsert_goal(g1)
        t0 = _utc(10)
        m1 = provider.assemble_candidate(now=t0)
        assert m1.provenance_ref.endswith(g1.goal_id)  # 最久未推进者优先
        m2 = provider.assemble_candidate(now=t0 + timedelta(days=1))
        assert m2 is not None
        assert m2.provenance_ref.endswith(g2.goal_id)  # 排除上一候选 → 换目标

    def test_no_scoring_no_float_fields(self, iso_env):
        """No Scoring 哲学（N1）: provider 源码 0 数值权重/打分字段。"""
        src_path = Path(__file__).resolve().parents[2] / "src" / "goals" / "motive_provider.py"
        src = src_path.read_text(encoding="utf-8")
        assert "score" not in src
        assert "weight" not in src
        # 轮替记忆只有结构记录（axis / goal_id / ts）
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gn" + "0" * 29))
        provider.assemble_candidate(now=_utc(10))
        state_file = iso_env / "memory" / AGENT / "goal_provider.json"
        assert state_file.is_file()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert len(data["rotation"]) == 1
        assert set(data["rotation"][0].keys()) == {"axis", "goal_id", "ts"}
        # LS-2 (2026-09-06): GoalProviderState 扩展种子生成器 5 个 additive 结构字段
        # （旧文件 from_dict 缺省兼容）→ 断言改为既有 4 字段 + 新字段类型校验
        assert {
            "last_candidate_at", "rotation", "consecutive_do_nothing",
            "consecutive_skips",
        } <= set(data.keys())
        assert isinstance(data["last_seed_scan_at"], (int, float))
        assert isinstance(data["seed_source_cursor"], int)
        assert isinstance(data["seed_axis_streak"], int)
        assert data["last_seed_axis"] is None          # 生成器未跑 → 缺省 None
        assert isinstance(data["seed_empty_rounds"], int)


# ───────────────────────────────────────────────────────────
# 5. 中断信号（do_nothing 计数 / last-seen 超时 / timeout ABANDON / 会话接管）
# ───────────────────────────────────────────────────────────

class TestInterruptSignals:
    def test_do_nothing_streak_suspends(self, iso_env):
        """信号 6: 连续 not_transmit ≥3 → SUSPENDED（不推进、不标记失败）。"""
        provider, store = _provider(iso_env)
        gid = "gd" + "0" * 29
        store.upsert_goal(_make_goal(gid))
        t0 = _utc(10)
        m = provider.assemble_candidate(now=t0)
        assert m is not None
        for i in range(GOAL_SUSPEND_NOT_TRANSMIT_STREAK - 1):
            r = provider.on_decision(m, _decision("do_nothing"), now=t0)
            assert r is None                      # 不推进不标记失败
            assert store.get_goals(AGENT)[0].state == GOAL_STATE_ACTIVE
            assert store.get_goals(AGENT)[0].advance_count == 0
        r = provider.on_decision(m, _decision("do_nothing"), now=t0)
        assert r == GOAL_STATE_SUSPENDED
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_SUSPENDED
        assert json.loads(got.suspend_snapshot)["reason"] == "decision_not_transmit_streak"

    def test_bryan_last_seen_timeout_suspends(self, iso_env):
        """信号 5: Bry last-seen > 4h → ACTIVE/IN_PROGRESS batch SUSPENDED。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=6)  # 6h 前
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gl1" + "0" * 28))
        store.upsert_goal(_make_goal("gl2" + "0" * 28, state=GOAL_STATE_IN_PROGRESS,
                                     advance_count=1))
        store.upsert_goal(_make_goal("gl3" + "0" * 28, state=GOAL_STATE_COMPLETED))
        changed = provider.apply_interrupt_signals(now=real_now)
        assert sorted(changed) == ["gl1" + "0" * 28, "gl2" + "0" * 28]
        states = {g.goal_id: g.state for g in store.get_goals(AGENT)}
        assert states["gl1" + "0" * 28] == GOAL_STATE_SUSPENDED
        assert states["gl2" + "0" * 28] == GOAL_STATE_SUSPENDED
        assert states["gl3" + "0" * 28] == GOAL_STATE_COMPLETED  # 终态不动

    def test_bryan_active_no_suspend(self, iso_env):
        """Bry 活跃（< 4h）→ 不挂起。"""
        real_now = datetime.now(timezone.utc)
        _write_last_seen(iso_env, ago_hours=1)
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gj" + "0" * 29))
        assert provider.apply_interrupt_signals(now=real_now) == []
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_ACTIVE

    def test_coldstart_no_suspend(self, iso_env):
        """last_seen 文件不存在（冷启动）→ 不挂起（跟 scheduler 一致）。"""
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gk" + "0" * 29))
        assert provider.apply_interrupt_signals(now=_utc(10)) == []

    def test_timeout_abandons_in_progress(self, iso_env):
        """timeout_days 超时（IN_PROGRESS + 无推进）→ ABANDONED（保留 record）。"""
        provider, store = _provider(iso_env)
        old_ts = time.time() - 2 * 86400
        store.upsert_goal(_make_goal(
            "ga" + "0" * 29, state=GOAL_STATE_IN_PROGRESS, advance_count=1,
            now_ts=old_ts, last_advanced_at=old_ts,
            completion_criteria=json.dumps({
                "kind": "interaction", "count": 2, "timeout_days": 1,
            }),
        ))
        changed = provider.apply_interrupt_signals(now=_utc(10))
        assert changed == ["ga" + "0" * 29]
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_ABANDONED
        assert got.advance_count == 1  # record 保留

    def test_suspend_on_takeover(self, iso_env):
        """信号 1/2（会话接管）: 立即挂起 ACTIVE/IN_PROGRESS（API 提供, 0 新 tick）。"""
        provider, store = _provider(iso_env)
        store.upsert_goal(_make_goal("gt1" + "0" * 28))
        store.upsert_goal(_make_goal("gt2" + "0" * 28, state=GOAL_STATE_IN_PROGRESS))
        store.upsert_goal(_make_goal("gt3" + "0" * 28, state=GOAL_STATE_COMPLETED))
        changed = provider.suspend_on_takeover(reason="user_message_burst")
        assert sorted(changed) == ["gt1" + "0" * 28, "gt2" + "0" * 28]
        states = {g.goal_id: g.state for g in store.get_goals(AGENT)}
        assert states["gt3" + "0" * 28] == GOAL_STATE_COMPLETED  # 终态不动


# ───────────────────────────────────────────────────────────
# 6. 唤醒扫描（quiet 过滤 / 跨日 / 外部信号解除 / 强制最长暂停）
# ───────────────────────────────────────────────────────────

class TestWakeupScan:
    def _suspend_goal(self, store, goal_id: str, suspended_at: float):
        """直接写入 SUSPENDED 状态 goal（等价于 transition 后现场, 无损三字段语义）。"""
        store.upsert_goal(_make_goal(
            goal_id, state=GOAL_STATE_SUSPENDED, now_ts=suspended_at,
            suspend_snapshot=json.dumps({"reason": "t", "ts": suspended_at}),
        ))

    def test_quiet_hours_no_wakeup(self, iso_env):
        """夜间（23:00 本地）不唤醒（唤醒侧过滤, §8.1 注）。"""
        provider, store = _provider(iso_env)
        self._suspend_goal(store, "gw1" + "0" * 28, _local(10).timestamp())
        assert provider.scheduled_wakeup_scan(now=_local(hour=23)) == []
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_SUSPENDED

    def test_new_day_wakes_up(self, iso_env):
        """新一天开始（跨日）→ SUSPENDED → ACTIVE。"""
        provider, store = _provider(iso_env)
        self._suspend_goal(store, "gw2" + "0" * 28, _local(10).timestamp())
        changed = provider.scheduled_wakeup_scan(now=_local(hour=9, day=1))
        assert changed == ["gw2" + "0" * 28]
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_ACTIVE

    def test_same_day_no_wakeup_without_signal(self, iso_env):
        """同一天、无外部信号、未到 7 天 → 保持 SUSPENDED。"""
        provider, store = _provider(iso_env)
        t0 = _local(10)
        self._suspend_goal(store, "gw3" + "0" * 28, t0.timestamp())
        assert provider.scheduled_wakeup_scan(now=t0 + timedelta(hours=2)) == []
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_SUSPENDED

    def test_external_signal_released_wakes_up(self, iso_env):
        """外部信号解除: Bry 重新互动（last_seen < 4h）→ 唤醒。"""
        t0 = _local(10)
        # 与 scan 时间同基准: 最近互动发生在 scan 前 1.5h（< 4h 阈值）
        path = iso_env / "state" / "bryan_last_seen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "last_recv_ts": (t0 + timedelta(minutes=30)).isoformat(),
        }), encoding="utf-8")
        provider, store = _provider(iso_env)
        self._suspend_goal(store, "gw4" + "0" * 28, t0.timestamp())
        changed = provider.scheduled_wakeup_scan(now=t0 + timedelta(hours=2))
        assert changed == ["gw4" + "0" * 28]

    def test_force_wakeup_after_7_days(self, iso_env):
        """强制最长暂停 > 7 天 → 唤醒一次（防永久冻结）。"""
        provider, store = _provider(iso_env)
        t0 = _local(10)
        self._suspend_goal(store, "gw5" + "0" * 28,
                           (t0 - timedelta(days=8)).timestamp())
        changed = provider.scheduled_wakeup_scan(now=t0)
        assert changed == ["gw5" + "0" * 28]
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_ACTIVE


# ───────────────────────────────────────────────────────────
# 7. 完成判定（completion_criteria 结构化条件 + 沉淀通道）
# ───────────────────────────────────────────────────────────

class TestCompletionCriteria:
    def test_complete_after_count_advances(self, iso_env):
        """count=2: 推进 2 次 → COMPLETED + 沉淀（InnerLifeEvent → trace 链）。"""
        provider, store = _provider(iso_env)
        gid = "gc" + "0" * 29
        store.upsert_goal(_make_goal(
            gid, completion_criteria=json.dumps({
                "kind": "interaction", "count": 2, "timeout_days": 30,
            }),
        ))
        t0 = _utc(10)
        m1 = provider.assemble_candidate(now=t0)
        r1 = provider.on_decision(m1, _decision("transmit"), now=t0)
        assert r1 == GOAL_STATE_IN_PROGRESS
        got = store.get_goals(AGENT)[0]
        assert got.advance_count == 1          # G2: 推进 ≤+1
        assert got.last_advanced_at is not None

        m2 = provider.assemble_candidate(now=t0 + timedelta(days=1))
        r2 = provider.on_decision(m2, _decision("reflect"), now=t0 + timedelta(days=1))
        assert r2 == GOAL_STATE_COMPLETED
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_COMPLETED
        assert got.advance_count == 2

        # 沉淀通道: trace.jsonl 有 goal 完成事件（extras.goal_id）
        trace = iso_env / "inner_life" / "trace.jsonl"
        assert trace.is_file()
        lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lines, "沉淀事件应写入 trace"
        last = lines[-1]
        assert last["provenance"]["trace_ref"] == f"goal:{gid}"
        assert last["provenance"]["extras"]["goal_id"] == gid
        assert last["provenance"]["trigger_type"] == "system"  # 0 新增 trigger_type
        assert last["provenance"]["source_system"] == "system"

    def test_no_criteria_never_auto_completes(self, iso_env):
        """criteria 缺失 → 永不自动完成（推进后保持 IN_PROGRESS）。"""
        provider, store = _provider(iso_env)
        gid = "gnc" + "0" * 28
        store.upsert_goal(_make_goal(gid))
        t0 = _utc(10)
        advances = 0
        for day in range(4):
            # 单轴 streak 节奏: [产, 产, 弃(轮替), 产(防饿死)]
            m = provider.assemble_candidate(now=t0 + timedelta(days=day))
            if m is not None:
                provider.on_decision(m, _decision("observe"),
                                     now=t0 + timedelta(days=day))
                advances += 1
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_IN_PROGRESS
        assert advances == 3
        assert got.advance_count == 3

    def test_invalid_criteria_kind_no_complete(self, iso_env):
        """criteria.kind 非法 → 判定失败（fail-closed, 不自动完成）。"""
        provider, store = _provider(iso_env)
        gid = "gik" + "0" * 28
        store.upsert_goal(_make_goal(
            gid, advance_count=5, state=GOAL_STATE_IN_PROGRESS,
            completion_criteria=json.dumps({"kind": "bogus", "count": 1}),
        ))
        # 推进一次不触发完成（kind 非法）
        t0 = _utc(10)
        m = provider.assemble_candidate(now=t0)
        provider.on_decision(m, _decision("transmit"), now=t0)
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_IN_PROGRESS

    def test_terminal_goal_ignored_by_on_decision(self, iso_env):
        """终态 goal 的候选被观察 → no-op（终态无出边）。"""
        provider, store = _provider(iso_env)
        gid = "gtt" + "0" * 28
        store.upsert_goal(_make_goal(gid, state=GOAL_STATE_COMPLETED))
        from src.soul.motive import Motive
        m = Motive(
            motive_id="mm" + "0" * 30,
            content="x",
            target="bryan",
            provenance_ref=f"goal:{gid}",
            created_at=_utc(10).isoformat(),
        )
        assert provider.on_decision(m, _decision("transmit"), now=_utc(10)) is None
        assert store.get_goals(AGENT)[0].state == GOAL_STATE_COMPLETED


# ───────────────────────────────────────────────────────────
# TG-3.1: 2 个 production 缺陷回归（UTC 沉淀对齐 + SUSPENDED 守卫）
# ───────────────────────────────────────────────────────────

class TestTG31ProductionDefectFixes:
    """TG-3 验收发现的 2 个生产缺陷修复断言。

    缺陷 1: sediment_completion 事件 ts 用本地时间 → 非 UTC 时区下
            InnerLifeEvent validate_ts（TS_PATTERN: +00:00|Z）拒绝 →
            fail-closed 静默丢弃, Trace 无产出。修复: 事件 ts 统一 UTC。
    缺陷 2: on_decision 只过滤终态, 中断窗口残留的 pending 候选会对
            SUSPENDED 目标误推进（advance_count +1 / 误判完成）。
            修复: SUSPENDED 拦截守卫（状态不变、计数不推）。
    """

    def test_sediment_ts_utc_cross_timezone(self, iso_env):
        """缺陷 1: 非 UTC 时区（UTC-4）下 COMPLETED 沉淀 → trace.jsonl 正常写入,
        事件 ts 为 UTC ISO-8601（+00:00/Z）且时刻等于推进时刻的 UTC 换算。"""
        provider, store = _provider(iso_env)
        gid = "gtz" + "0" * 28
        store.upsert_goal(_make_goal(
            gid, completion_criteria=json.dumps({
                "kind": "interaction", "count": 1, "timeout_days": 30,
            }),
        ))
        # 跨时区基准: UTC-4 aware now（模拟非 UTC 生产环境/显式传参路径）
        t0 = datetime(2026, 9, 6, 10, 30, tzinfo=timezone(timedelta(hours=-4)))
        m = provider.assemble_candidate(now=t0)
        assert m is not None
        r = provider.on_decision(m, _decision("transmit"), now=t0)
        assert r == GOAL_STATE_COMPLETED   # 完成 + 沉淀链路全走通

        trace = iso_env / "inner_life" / "trace.jsonl"
        assert trace.is_file(), "COMPLETED 沉淀事件应写入 trace（修复前非 UTC 被静默丢弃）"
        lines = [json.loads(l) for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert lines, "trace 不应为空"
        last = lines[-1]
        assert last["provenance"]["trace_ref"] == f"goal:{gid}"
        ts = last["ts"]
        # 契约断言: 必须是 UTC ISO-8601（+00:00 或 Z 后缀）
        assert ts.endswith("+00:00") or ts.endswith("Z"), f"ts 非 UTC 格式: {ts!r}"
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset().total_seconds() == 0
        # 时刻等价: 事件 ts == 推进时刻 t0 的 UTC 换算
        assert parsed == t0.astimezone(timezone.utc)

    def test_suspended_goal_stale_candidate_guard(self, iso_env):
        """缺陷 2: 目标被挂起后, 中断窗口残留候选被决策选中 → 守卫拦截（transmit/
        do_nothing 均 no-op, 状态不变、advance_count 不推）; 唤醒后守卫不误伤。"""
        provider, store = _provider(iso_env)
        gid = "gsu" + "0" * 28
        store.upsert_goal(_make_goal(
            gid, completion_criteria=json.dumps({
                "kind": "interaction", "count": 2, "timeout_days": 30,
            }),
        ))
        t0 = _utc(10)
        m = provider.assemble_candidate(now=t0)   # ACTIVE 时产出的候选（陈旧）
        assert m is not None
        # 装配后目标被中断挂起（候选仍残留 pending 池/决策已发出）
        store.transition_goal(
            gid, GOAL_STATE_SUSPENDED,
            meta={"suspend_snapshot": json.dumps({"reason": "interrupt", "ts": t0.timestamp()})},
        )
        # 陈旧候选 transmit → 拦截: 无推进、无状态变更
        assert provider.on_decision(
            m, _decision("transmit"), now=t0 + timedelta(hours=1)
        ) is None
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_SUSPENDED
        assert got.advance_count == 0           # 计数不推（修复前会误 +1）
        # 陈旧候选 do_nothing → 同样拦截（不进 do_nothing 计数, 不会二次计数挂起）
        assert provider.on_decision(
            m, _decision("do_nothing"), now=t0 + timedelta(hours=2)
        ) is None
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_SUSPENDED
        assert got.advance_count == 0
        # 守卫只拦 SUSPENDED: 唤醒 → ACTIVE 后接续推进正常（不误伤）
        store.transition_goal(gid, GOAL_STATE_ACTIVE)
        r = provider.on_decision(m, _decision("transmit"), now=t0 + timedelta(hours=3))
        assert r == GOAL_STATE_IN_PROGRESS
        got = store.get_goals(AGENT)[0]
        assert got.state == GOAL_STATE_IN_PROGRESS
        assert got.advance_count == 1


# ───────────────────────────────────────────────────────────
# 8. 心跳接线（装配 ≤1 汇入 pending → resolve_pending; _decision_check 内扩）
# ───────────────────────────────────────────────────────────

class TestHeartbeatWiring:
    def test_goal_candidate_enters_pending_pool(self, iso_env):
        """真链: assemble → MotiveTraceStore pending 池 → resolve_pending 取到候选。"""
        from src.soul.motive import MotiveEngine
        provider, store = _provider(iso_env)
        gid = "gw" + "0" * 29
        store.upsert_goal(_make_goal(gid, title="关心 Bry 上次提到的工作面试"))

        engine = MotiveEngine()
        # 先经历后目标: interpret（无事件 → 无普通 motive）
        produced = []
        import asyncio

        async def run():
            produced.extend(await engine.interpret_new_events(AGENT))
            m = provider.assemble_candidate(now=_utc(10))
            assert m is not None
            return engine.resolve_pending(AGENT)

        motive = asyncio.run(run())
        assert produced == []
        assert motive is not None
        assert motive.provenance_ref == f"goal:{gid}"   # goal 候选被单条选取
        assert motive.content == "关心 Bry 上次提到的工作面试"

    def test_decision_check_wires_goal_provider(self, iso_env, monkeypatch):
        """_decision_check 内扩: GoalMotiveProvider 装配备调用（fail-closed 不影响管线）。"""
        calls: list[str] = []

        class FakeEngine:
            async def interpret_new_events(self, agent_id):
                calls.append("interpret")
                return []
            def resolve_pending(self, agent_id):
                calls.append("resolve")
                return None
            def mark_transmitted(self, motive_id):
                pass
            def mark_rejected(self, motive_id):
                pass

        class FakeProvider:
            @classmethod
            def for_agent(cls, agent_id):
                return cls()
            def apply_interrupt_signals(self):
                calls.append("interrupt")
            def scheduled_wakeup_scan(self):
                calls.append("wakeup")
            def assemble_candidate(self):
                calls.append("assemble")
            def on_decision(self, motive, result):
                calls.append("on_decision")

        import src.soul.scheduler as sched_mod
        monkeypatch.setattr("src.soul.motive.MotiveEngine", FakeEngine)
        monkeypatch.setattr("src.goals.motive_provider.GoalMotiveProvider", FakeProvider)

        scheduler = sched_mod.SoulScheduler()
        import asyncio
        ok = asyncio.run(scheduler._decision_check(AGENT))
        assert ok is False                       # 无 pending → fail-closed skip
        assert calls == ["interpret", "interrupt", "wakeup", "assemble", "resolve"]
        assert "on_decision" not in calls        # resolve None → 无 decision

    def test_decision_check_syncs_goal_on_transmit(self, iso_env, monkeypatch):
        """_decision_check 全链: goal 候选 transmit → on_decision 状态同步被调用。"""
        from src.soul.decision import DecisionResult

        class FakeProvider:
            seen: list = []
            @classmethod
            def for_agent(cls, agent_id):
                return cls()
            def apply_interrupt_signals(self):
                pass
            def scheduled_wakeup_scan(self):
                pass
            def assemble_candidate(self):
                pass
            def on_decision(self, motive, result):
                self.__class__.seen.append(
                    (motive.provenance_ref, result.decision)
                )

        import src.soul.scheduler as sched_mod
        from src.soul.motive import Motive

        class FakeEngine:
            async def interpret_new_events(self, agent_id):
                return []
            def resolve_pending(self, agent_id):
                return Motive(
                    motive_id="mq" + "0" * 30,
                    content="goal 候选",
                    target="bryan",
                    provenance_ref=f"goal:{'gg' + '0' * 29}",
                    created_at=_utc(10).isoformat(),
                )
            async def decide(self, motive, agent_id):
                return DecisionResult(
                    decision="transmit", transmit=True, reason="t", motive_id=motive.motive_id,
                )
            def mark_transmitted(self, motive_id):
                pass
            def mark_rejected(self, motive_id):
                pass

        monkeypatch.setattr("src.soul.motive.MotiveEngine", FakeEngine)
        monkeypatch.setattr("src.goals.motive_provider.GoalMotiveProvider", FakeProvider)

        scheduler = sched_mod.SoulScheduler()
        import asyncio
        ok = asyncio.run(scheduler._decision_check(AGENT))
        assert ok is True
        assert FakeProvider.seen == [(f"goal:{'gg' + '0' * 29}", "transmit")]


# ───────────────────────────────────────────────────────────
# 9. Plan B 独立性（0 侵入 MotiveEngine 核心代码）
# ───────────────────────────────────────────────────────────

class TestPlanBIndependence:
    def test_motive_engine_source_untouched(self):
        """motive.py 未新增任何 Goal 相关代码（Plan B: 0 侵入核心 MotiveEngine）。"""
        motive_src = (
            Path(__file__).resolve().parents[2] / "src" / "soul" / "motive.py"
        ).read_text(encoding="utf-8")
        assert "goal" not in motive_src.lower().replace("目标", "")
        # provider 只依赖 Motive dataclass 与 MotiveTraceStore.append_motive（既有 API）
        provider_src = (
            Path(__file__).resolve().parents[2] / "src" / "goals" / "motive_provider.py"
        ).read_text(encoding="utf-8")
        assert "MotiveEngine(" not in provider_src
        assert "import src.soul.motive" not in provider_src or "MotiveTraceStore" in provider_src