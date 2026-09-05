"""
tests/harness/test_goal_driven_harness.py — TG-3 自主目标驱动行为 Harness 整合验收

阶段 C-1「自主目标与意向引擎」最终生产闭环钢印: 在模拟多心跳时间序列与
外部干扰环境下, 实证跨心跳长程推进 / 突发中断与唤醒 / 双轴配额轮替防饥饿 /
终态记忆沉淀, 刚性验证 Volition Gate 与 No-Scoring 哲学在端到端运行时的不变量。

四大剧本:
  1. 跨心跳长程推进: 多步骤目标 × 连续 5 心跳无外部干扰 →
     选中心跳 advance_count 严格 ==1 / 同心跳 0 二次连续决策 (No ReAct cascade) /
     首个推进心跳后 ACTIVE→IN_PROGRESS。
  2. 突发中断与唤醒恢复: IN_PROGRESS 时注入 Bryan 1:1 私聊 →
     心跳让位 (决策让位对话响应) + SUSPENDED 无损; 对话结束 2 次 IDLE 心跳 →
     唤醒条件满足 → 自动恢复 ACTIVE 并接续, 进度无丢失。
  3. 双轴配额轮替与防饥饿: Bryan 轴 + 自我轴目标 × 连续 10 心跳 →
     单心跳候选 ≤1 / 同轴连续选中 ≤2 / 两轴均获推进 / 任一轴无 >3 周期饥饿 +
     No-Scoring 铁证 (源码 AST 审计 + 运行期结构 sidecar)。
  4. 达成与终态沉淀: 最后一步 → COMPLETED → goals 表状态 + 时间戳写入 +
     不再入活跃轮替; 沉淀隔离 (facts 0 直写) + 合规沉淀链 (InnerLifeWriter →
     trace.jsonl → 既有升华管线)。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/harness/test_goal_driven_harness.py -v

Frozen contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT。
本文件只新增 harness 测试; 运行时 monkeypatch 仅剥离 Decision 重检索子块
(Relevant context 按需注入, 非 contract 字段), 主路径 (Framing/Motive/Boundary/
时间注入/LLM stub/parse) 全真实。
"""
from __future__ import annotations

import ast
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.goals.motive_provider import (
    GOAL_MAX_ROTATION_STREAK,
    GOAL_ROTATION_WINDOW,
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    Goal,
)
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root
from src.soul.decision import decide_motive

AGENT = "agent_tg3"

# 项目根 (向上: tests/harness/<file> → 项目根)
ROOT = Path(__file__).resolve().parents[2]

# ───────────────────────────────────────────────────────────
# 可控时钟（消除真实 time.time 对状态机判定的环境干扰）
# ───────────────────────────────────────────────────────────


class _FakeClock:
    """可控 time.time 源: 替换 graph_store / motive_provider 模块的 time 引用。

    transition_goal / suspend_on_takeover 内部走 time.time(), 真实时钟会让
    SUSPENDED 跨日判定、state_updated_at 断言依赖运行日——用模拟时钟彻底确定化。
    """

    def __init__(self) -> None:
        self.value: float = 0.0

    def time(self) -> float:
        return self.value


# ───────────────────────────────────────────────────────────
# No-Scoring 铁证: 源码 AST 审计（返回违规清单, 空 = 通过）
# ───────────────────────────────────────────────────────────

# 排序/极值 key 允许引用的字段白名单: 纯时间戳 / 纯结构（0 数值权重打分）
_SORT_KEY_ALLOWED_FIELDS = frozenset({
    "last_advanced_at",   # 轴内轮候: 时间戳
    "last_ts",            # 轴轮替: 时间戳
    "created_at",         # 排序退化
    "ts",                 # rotation 结构时间戳
    "axis",               # 轴名字符串
    "goal_id",            # 结构身份
})


def _audit_provider_no_scoring() -> List[str]:
    """AST 审计 src/goals/motive_provider.py 的 No-Scoring 铁证。

    R1: 模块源码无 score / weight 字样（字符串级, TG-2 先例）。
    R2: 所有 sort/min/max 调用（含 key lambda）只引用时间/结构白名单字段,
        绝无数值权重参与排序路径。
    返回违规描述列表; 空列表 = No-Scoring 成立。
    """
    issues: List[str] = []
    src_path = ROOT / "src" / "goals" / "motive_provider.py"
    src = src_path.read_text(encoding="utf-8")

    # R1: 字符串级
    for token in ("score", "weight"):
        if token in src:
            issues.append(f"源码含 {token!r} 字样 (R1 违反)")

    # R2: AST 层 sort/min/max key 审计
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        issues.append(f"源码解析失败: {e}")
        return issues
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func_name not in ("sort", "min", "max"):
            continue
        # 收集 key 参数（位置或 keyword）
        lambdas: List[ast.Lambda] = []
        if node.args and isinstance(node.args[0], ast.Lambda):
            lambdas.append(node.args[0])
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Lambda):
                lambdas.append(kw.value)
        for lam in lambdas:
            refs = {
                sub.attr
                for sub in ast.walk(lam)
                if isinstance(sub, ast.Attribute)
            }
            for sub in ast.walk(lam):
                if not isinstance(sub, ast.Subscript):
                    continue
                sl = sub.slice
                if isinstance(sl, ast.Index):      # py3.8 包装
                    sl = sl.value
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    refs.add(sl.value)
            non_allowed = refs - _SORT_KEY_ALLOWED_FIELDS
            if non_allowed:
                issues.append(
                    f"{func_name} key 引用非白名单字段 {sorted(non_allowed)} (R2 违反)"
                )
    return issues


# ───────────────────────────────────────────────────────────
# Fixture 隔离: 每用例独立 tmp_path SQLite (Schema v8) + 可控时钟
# ───────────────────────────────────────────────────────────


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """P0.5 隔离: SOUL_OS_DATA_DIR → tmp_path + v8 迁移预检 + 可控时钟 + 升华数据隔离。

    - 每测试用例独立 tmp_path → 独立 SQLite（含 goals/facts 表, Schema v8）
    - graph_store / motive_provider 的 time 引用替换为 _FakeClock（状态机判定确定化）
    - Decision 重检索子块剥离（_build_memory_summary/_build_emergent_summary/
      _build_temporal_anchor → None）: 只禁按需注入的检索, 不碰 Framing/Motive/
      Boundary 冻结四块与 parse 主路径
    - data_root/elevation/elevation_nodes.jsonl 空文件模拟既有升华数据隔离
    """
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()

    clock = _FakeClock()
    import src.memory.sage.graph_store as gs_mod
    import src.goals.motive_provider as mp_mod
    monkeypatch.setattr(gs_mod, "time", clock)
    monkeypatch.setattr(mp_mod, "time", clock)

    # Schema v8 迁移预检: 全新库走迁移 → goals + facts 表, version=8
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path=db).close()
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        assert row[0] == "8", f"Schema v8 迁移失败: version={row[0]}"
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert {"goals", "facts"} <= tables, f"缺表: {tables}"
    finally:
        conn.close()

    # Decision 重检索子块剥离（主路径依旧真实）
    import src.soul.decision as dec_mod
    monkeypatch.setattr(dec_mod, "_build_memory_summary", lambda *a, **k: None)
    monkeypatch.setattr(dec_mod, "_build_emergent_summary", lambda *a, **k: None)
    monkeypatch.setattr(dec_mod, "_build_temporal_anchor", lambda *a, **k: None)

    # 升华数据隔离（生产环境既有 elevation_nodes.jsonl 独立于 goal 引擎）
    elev = tmp_path / "elevation"
    elev.mkdir(parents=True, exist_ok=True)
    (elev / "elevation_nodes.jsonl").write_text("", encoding="utf-8")

    yield {"tmp": tmp_path, "clock": clock}

    reset_goal_providers()
    reset_data_root()


# ───────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────


def _local(hour: int = 10, day: int = 0) -> datetime:
    """固定基准时间（系统本地时区; quiet hours 判定以本地作息语义对齐）。"""
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _make_goal(
    goal_id: str,
    axis: str = AXIS_BRYAN,
    title: str = "测试目标",
    state: str = GOAL_STATE_ACTIVE,
    now_ts: Optional[float] = None,
    **kw: Any,
) -> Goal:
    ts = now_ts if now_ts is not None else 0.0
    return Goal(
        goal_id=goal_id,
        agent_id=AGENT,
        axis=axis,
        title=title,
        description="",
        seed_source_ref="rel:tg3",
        state=state,
        state_updated_at=ts,
        created_at=ts,
        **kw,
    )


def _new_provider(tmp_path: Path, clock: _FakeClock) -> GoalMotiveProvider:
    """新建隔离 provider（注入 GraphStore; 与 MOTIVE_TTL 无涉 → 传模拟 now）。"""
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    store = GraphStore(db_path=db)
    return GoalMotiveProvider(agent_id=AGENT, store=store)


def _facts_count(tmp_path: Path, where: str = "") -> int:
    """facts 表计数（沉淀隔离断言用）。"""
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        sql = "SELECT COUNT(*) FROM facts" + (f" WHERE {where}" if where else "")
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _trace_records(tmp_path: Path) -> List[Dict[str, Any]]:
    """读沉淀链 trace.jsonl（InnerLifeWriter 事件日志）。"""
    path = tmp_path / "inner_life" / "trace.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ───────────────────────────────────────────────────────────
# MockHeartbeatRunner — 受控时间推进步进器（复刻 _decision_check 接线顺序）
# ───────────────────────────────────────────────────────────

# 外部干扰类型（对齐 §8.1 信号 1/2）
INTERFERENCE_USER_MESSAGE = "USER_MESSAGE"
INTERFERENCE_MULTIMODAL = "MULTIMODAL_HIGH_PRIORITY"


class MockHeartbeatRunner:
    """受控时间推进步进器: 复刻 scheduler._decision_check 接线顺序
    (interpret → interrupt → wakeup → assemble → resolve → decide → on_decision),
    逐心跳观察 GoalMotiveProvider / DecisionEngine / 状态转移。

    语义要点:
      - 时钟: 自持 _now（本地 aware）, 步进时同步 _FakeClock（transition_goal/
        suspend_on_takeover 的内部 time.time 用同一模拟时间）
      - 干扰心跳（Bryan 私聊 / 多模态高优）: 决策让位对话响应 — suspend_on_takeover
        立即挂起 + 跳过例行扫描/装配/决策, 记录 yielded_dialog=True
      - decide: 真实 decide_motive（Framing/Motive/Boundary 冻结四块 + LLM stub +
        fail-closed parse）, 每心跳至多 1 次（Volition Gate: 1 Heartbeat 1 Step）
      - 每心跳记录: 候选 / 决策次数 / 状态快照 / advance delta
    """

    def __init__(
        self,
        provider: GoalMotiveProvider,
        clock: _FakeClock,
        start: Optional[datetime] = None,
        decision_action: str = "transmit",
    ) -> None:
        self.provider = provider
        self.agent_id = AGENT
        self.clock = clock
        self._now = start if start is not None else _local(10)
        self.decision_action = decision_action
        self._interference: Optional[str] = None
        self.records: List[Dict[str, Any]] = []
        self.prompts: List[str] = []      # DecisionEngine 收到的 prompt（观察用）

    # ── 外部干扰注入（ExternalInterferenceSimulator 调用面）──

    def inject_interference(self, kind: str) -> None:
        assert kind in (INTERFERENCE_USER_MESSAGE, INTERFERENCE_MULTIMODAL)
        self._interference = kind

    def clear_interference(self) -> None:
        self._interference = None

    def _write_bryan_last_seen(self) -> None:
        """模拟 channel inbound: touch_bryan_last_seen（统一信号源）。"""
        from src.io.channels.bryan_state import _bryan_last_seen_file
        path = _bryan_last_seen_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "last_recv_ts": self._now.isoformat(),
            "last_recv_agent": AGENT,
            "last_recv_preview": "TG-3 harness",
        }, ensure_ascii=False), encoding="utf-8")

    # ── 心跳执行 ───────────────────────────────────────────

    def step(self, interval: timedelta) -> Dict[str, Any]:
        """推进 interval 并执行一次心跳, 返回观察记录。"""
        self._now += interval
        return self.run_heartbeat()

    def run_heartbeat(self) -> Dict[str, Any]:
        self.clock.value = self._now.timestamp()
        beat = len(self.records) + 1
        rec: Dict[str, Any] = {
            "beat": beat,
            "now": self._now.isoformat(),
            "interference": self._interference,
            "yielded_dialog": False,
            "suspend_changed": [],
            "wakeup_changed": [],
            "candidate": None,
            "candidate_axis": None,
            "decision_count": 0,
            "decision": None,
            "state_before": {},
            "state_after": {},
            "advance_before": {},
            "advance_after": {},
            "advance_delta": {},
        }
        # 心跳开始基线（delta 以同心跳 before→after 计算, 不依赖跨心跳记忆）
        self._snapshot_before(rec)

        # 0) 干扰心跳: 决策让位对话响应
        if self._interference is not None:
            if self._interference == INTERFERENCE_USER_MESSAGE:
                self._write_bryan_last_seen()
                reason = "user_message_burst"
            else:
                reason = "multimodal_high_priority"
            rec["yielded_dialog"] = True
            rec["suspend_changed"] = self.provider.suspend_on_takeover(
                reason=reason,
                snapshot=json.dumps({"reason": reason, "ts": self._now.timestamp()}),
            )
            self._snapshot_after(rec)
            self.records.append(rec)
            return rec

        # 1) 中断信号扫描（§8.2; 信号 5/6 + ABANDON 周期）
        rec["suspend_changed"] = self.provider.apply_interrupt_signals(now=self._now)
        # 2) 唤醒扫描（§8.3; SUSPENDED → ACTIVE, 含 quiet hours 过滤）
        rec["wakeup_changed"] = self.provider.scheduled_wakeup_scan(now=self._now)
        # 3) 装配候选（≤1/心跳; 24h 配额 + N=3 轮替 + streak=2, 0 权重）
        candidate = self.provider.assemble_candidate(now=self._now)
        if candidate is not None:
            rec["candidate"] = candidate.provenance_ref
            cid = candidate.provenance_ref[len("goal:"):]
            cgoal = next(
                (g for g in self.provider._store_for().get_goals(self.agent_id)
                 if g.goal_id == cid),
                None,
            )
            rec["candidate_axis"] = cgoal.axis if cgoal is not None else None
        # 4) resolve（同池单条选取; TTL 以模拟时钟判定）
        motive = self.provider._trace_store.resolve_pending(
            AGENT, now=self._now.astimezone(timezone.utc)
        )
        # 5) DecisionEngine（真实 decide_motive; Volition Gate: 至多 1 次/心跳）
        if motive is not None:
            import asyncio

            result = asyncio.run(decide_motive(
                motive,
                self.agent_id,
                llm_call=self._stub_llm,
                current_time=self._now.strftime("%Y-%m-%d %H:%M"),
            ))
            rec["decision_count"] = 1
            rec["decision"] = result.decision
            # 6) 状态同步（只认 goal: 前缀; 普通 motive → no-op）
            #    对齐 InnerLifeEvent ts UTC 契约: 传 UTC aware now（沉淀链 ts 校验）
            self.provider.on_decision(
                motive, result, now=self._now.astimezone(timezone.utc)
            )
            # 对齐真实 scheduler: transmit → mark_transmitted / 其余 → mark_rejected
            if result.transmit:
                self.provider._trace_store.mark_transmitted(motive.motive_id)
            else:
                self.provider._trace_store.mark_rejected(motive.motive_id)

        self._snapshot_after(rec)
        self.records.append(rec)
        return rec

    async def _stub_llm(self, messages, agent_id, max_tokens, temperature):
        """Decision LLM stub: 记录 prompt + 返回预定四元动作（fail-closed 主路径真实）。"""
        self.prompts.append(messages[0]["content"])
        return json.dumps({
            "decision": self.decision_action,
            "reason": "harness-stub",
        })

    def _snapshot_before(self, rec: Dict[str, Any]) -> None:
        for g in self.provider._store_for().get_goals(self.agent_id):
            rec["state_before"][g.goal_id] = g.state
            rec["advance_before"][g.goal_id] = g.advance_count

    def _snapshot_after(self, rec: Dict[str, Any]) -> None:
        for g in self.provider._store_for().get_goals(self.agent_id):
            rec["state_after"][g.goal_id] = g.state
            rec["advance_after"][g.goal_id] = g.advance_count
            # 同心跳 before→after delta（选中心跳 advance 严格 ==1 的判定基）
            rec["advance_delta"][g.goal_id] = (
                g.advance_count - rec["advance_before"].get(g.goal_id, g.advance_count)
            )

    # ── 断言辅助 ───────────────────────────────────────────

    def goal(self, goal_id: str) -> Dict[str, Any]:
        """最新 goal 快照（从 store 读, 不信任缓存）。"""
        g = next(
            (g for g in self.provider._store_for().get_goals(self.agent_id)
             if g.goal_id == goal_id),
            None,
        )
        assert g is not None, f"goal {goal_id} 不存在"
        return {
            "state": g.state,
            "advance_count": g.advance_count,
            "last_advanced_at": g.last_advanced_at,
            "state_updated_at": g.state_updated_at,
            "suspend_snapshot": g.suspend_snapshot,
        }


# ───────────────────────────────────────────────────────────
# ExternalInterferenceSimulator — 外部干扰注入器
# ───────────────────────────────────────────────────────────


class ExternalInterferenceSimulator:
    """随时注入外部干扰信号（模拟通道层接线点）:

      - bryan_private_message(): 注入 Bryan 1:1 私聊（USER_MESSAGE, 会话接管）
      - multimodal_high_priority(): 注入高优先级多模态感知事件
      - conversation_ended(): 对话结束, 清除干扰 → 进入 IDLE 心跳序列
    """

    def __init__(self, runner: MockHeartbeatRunner) -> None:
        self.runner = runner

    def bryan_private_message(self) -> None:
        self.runner.inject_interference(INTERFERENCE_USER_MESSAGE)

    def multimodal_high_priority(self) -> None:
        self.runner.inject_interference(INTERFERENCE_MULTIMODAL)

    def conversation_ended(self) -> None:
        self.runner.clear_interference()


# ───────────────────────────────────────────────────────────
# 剧本 1: 跨心跳长程推进（Volition Gate: 1 Heartbeat 1 Step）
# ───────────────────────────────────────────────────────────


class TestScenario1LongRangeAdvance:
    """多步骤目标 × 连续 5 心跳无外部干扰。

    刚性断言:
      - 落地目标被选中（候选产出）的心跳 advance_count 严格 ==1（G2: ≤+1 且仅当选中）
      - 同心跳内 0 二次连续决策（No ReAct cascade）
      - 首个推进心跳后 ACTIVE → IN_PROGRESS
      - 跨 5 心跳长程推进: 双目标 advance 单调递增、无回退
    """

    def test_five_heartbeats_strict_advance_no_react_cascade(self, iso_env):
        runner, interference, gb, gs = self._setup(iso_env)
        # 连续 5 心跳, 无外部干扰; 间隔 25h > 24h 配额窗 → 每心跳可产 1 候选
        for _ in range(5):
            runner.step(timedelta(hours=25))

        assert len(runner.records) == 5
        assert all(r["interference"] is None for r in runner.records)
        produced_beats = [r for r in runner.records if r["candidate"] is not None]
        # 双轴轮替下 5 心跳全部产出候选（排除上候选 + streak 交替 → 无 skip）
        assert len(produced_beats) == 5

        for r in runner.records:
            # 同心跳 0 二次连续决策: 至多 1 次决策（且此时恰有 1 候选）
            assert r["decision_count"] <= 1
            assert r["decision"] in (None, "transmit", "observe", "reflect", "do_nothing")
            if r["candidate"] is not None:
                assert r["decision_count"] == 1
                # 被选中且推进: advance delta 严格 == 1（不跳变、不 +0 滑水）
                deltas = [d for d in r["advance_delta"].values() if d != 0]
                assert deltas == [1], f"beat {r['beat']} advance delta 非 1: {r}"

        # 首个推进心跳后 ACTIVE → IN_PROGRESS
        bg = runner.goal(gb)
        sg = runner.goal(gs)
        assert runner.records[0]["state_after"][gb] == GOAL_STATE_IN_PROGRESS
        # 跨心跳长程推进: 5 心跳推进 5 次（双目标累加）
        assert bg["advance_count"] == 3
        assert sg["advance_count"] == 2
        assert bg["state"] == GOAL_STATE_IN_PROGRESS
        assert sg["state"] == GOAL_STATE_IN_PROGRESS
        # last_advanced_at 写入（模拟时间戳, 非空）
        assert bg["last_advanced_at"] is not None
        assert sg["last_advanced_at"] is not None
        # 无二次连续决策的旁证: 每心跳决策恰好 ≤1 次全心跳合计 == 5
        assert sum(r["decision_count"] for r in runner.records) == 5
        # DecisionEngine 主路径真实: 每决策都产出了冻结四块 prompt
        assert len(runner.prompts) == 5
        for p in runner.prompts:
            assert "你想告诉 bryan" in p          # Motive 块（冻结措辞）
            assert "[當前時間感知]" in p          # SM-4.5 时间注入
            for action in ("transmit", "observe", "reflect", "do_nothing"):
                assert action in p                # Boundary 四元行动（冻结）

    def _setup(self, iso_env):
        tmp, clock = iso_env["tmp"], iso_env["clock"]
        provider = _new_provider(tmp, clock)
        gb = "gb" + "0" * 30
        gs = "gs" + "0" * 30
        # 多步骤目标: count=6 → 5 心跳内不完成, 持续 IN_PROGRESS 长程演示
        criteria = json.dumps({"kind": "interaction", "count": 6, "timeout_days": 30})
        provider._store_for().upsert_goal(_make_goal(
            gb, axis=AXIS_BRYAN, title="陪 Bry 走完转型计划复盘", completion_criteria=criteria,
        ))
        provider._store_for().upsert_goal(_make_goal(
            gs, axis=AXIS_SELF, title="沉淀本周三个观察", completion_criteria=criteria,
        ))
        runner = MockHeartbeatRunner(provider, clock, start=_local(10))
        interference = ExternalInterferenceSimulator(runner)
        return runner, interference, gb, gs


# ───────────────────────────────────────────────────────────
# 剧本 2: 突发中断与唤醒恢复
# ───────────────────────────────────────────────────────────


class TestScenario2InterruptAndWakeup:
    """IN_PROGRESS 时注入 Bryan 1:1 私聊 → SUSPENDED（决策让位对话响应）;
    对话结束 → 连续 2 次 IDLE 心跳（无唤醒无推进）→ 唤醒条件满足 → 自动恢复
    ACTIVE 并接续, 进度无丢失（advance_count 挂起后保留、接续 +1）。"""

    def test_user_message_interrupt_yield_and_wakeup_resume(self, iso_env):
        tmp, clock = iso_env["tmp"], iso_env["clock"]
        provider = _new_provider(tmp, clock)
        gid = "gi" + "0" * 30
        provider._store_for().upsert_goal(_make_goal(
            gid, axis=AXIS_BRYAN, title="推动 9 月健康习惯复盘",
            completion_criteria=json.dumps({"kind": "interaction", "count": 3, "timeout_days": 30}),
        ))
        runner = MockHeartbeatRunner(provider, clock, start=_local(10))
        sim = ExternalInterferenceSimulator(runner)

        # ── 心跳 1: 无干扰 → 选中推进 → IN_PROGRESS advance=1 ──
        r1 = runner.run_heartbeat()
        assert r1["candidate"] == f"goal:{gid}"
        assert r1["decision"] == "transmit"
        assert runner.goal(gid)["state"] == GOAL_STATE_IN_PROGRESS
        assert runner.goal(gid)["advance_count"] == 1

        # ── 心跳 2 (+3h): Bryan 1:1 私聊突发 → 目标精确 SUSPENDED, 决策让位 ──
        sim.bryan_private_message()
        r2 = runner.step(timedelta(hours=3))
        assert r2["yielded_dialog"] is True           # 决策让位对话响应
        assert r2["candidate"] is None                 # 本心跳 0 goal 候选进池
        assert r2["decision_count"] == 0               # 0 二次决策（让位）
        assert r2["decision"] is None
        g = runner.goal(gid)
        assert g["state"] == GOAL_STATE_SUSPENDED      # 精确转移 SUSPENDED
        assert g["advance_count"] == 1                 # 无损: 0 计数重置
        snap = json.loads(g["suspend_snapshot"])
        assert snap["reason"] == "user_message_burst"  # 中断现场快照
        # ts 精确等于心跳 2 的模拟时间戳（可控时钟）
        assert int(snap["ts"]) == int(datetime.fromisoformat(r2["now"]).timestamp())

        # ── 对话结束 → 心跳 3 (+8h): IDLE #1 —— 同日无唤醒条件, 保持 SUSPENDED ──
        sim.conversation_ended()
        r3 = runner.step(timedelta(hours=8))
        assert r3["interference"] is None
        assert r3["wakeup_changed"] == []              # 真 IDLE: 无唤醒
        assert r3["candidate"] is None                 # SUSPENDED 不入轮替
        assert r3["decision_count"] == 0
        assert runner.goal(gid)["state"] == GOAL_STATE_SUSPENDED
        assert runner.goal(gid)["advance_count"] == 1  # 进度无丢失

        # ── 心跳 4 (+20h, 跨日): IDLE #2 —— 唤醒条件满足（新一天）→ 自动恢复并接续 ──
        r4 = runner.step(timedelta(hours=20))
        assert r4["wakeup_changed"] == [gid]           # SUSPENDED → ACTIVE
        assert r4["candidate"] == f"goal:{gid}"        # 恢复后重新入活跃轮替
        assert r4["decision"] == "transmit"
        assert r4["decision_count"] == 1
        g = runner.goal(gid)
        assert g["state"] == GOAL_STATE_IN_PROGRESS    # 接续推进（非重置）
        assert g["advance_count"] == 2                 # 1 + 1 = 2, 进度无丢失

        # 状态机合法性: SUSPENDED → ACTIVE → IN_PROGRESS 全程合法转移（无异常即证）

    def test_multimodal_high_priority_also_suspends(self, iso_env):
        """高优先级多模态感知事件同样触发无损挂起（信号 2 变体）。"""
        tmp, clock = iso_env["tmp"], iso_env["clock"]
        provider = _new_provider(tmp, clock)
        gid = "gm" + "0" * 30
        provider._store_for().upsert_goal(_make_goal(gid, axis=AXIS_SELF))
        runner = MockHeartbeatRunner(provider, clock, start=_local(10))
        sim = ExternalInterferenceSimulator(runner)
        runner.run_heartbeat()
        assert runner.goal(gid)["state"] == GOAL_STATE_IN_PROGRESS
        sim.multimodal_high_priority()
        r = runner.step(timedelta(hours=2))
        assert r["yielded_dialog"] is True
        assert runner.goal(gid)["state"] == GOAL_STATE_SUSPENDED
        assert json.loads(runner.goal(gid)["suspend_snapshot"])["reason"] == (
            "multimodal_high_priority"
        )


# ───────────────────────────────────────────────────────────
# 剧本 3: 双轴配额轮替与防饥饿 + No-Scoring 铁证
# ───────────────────────────────────────────────────────────


class TestScenario3DualAxisQuotaRoundRobin:
    """Bryan 轴 + 自我轴目标 × 连续 10 心跳。

    刚性断言:
      - 单心跳候选 ≤1（G1）
      - 同轴连续选中 ≤2（GOAL_MAX_ROTATION_STREAK 锁）
      - 两轴 10 周期内均获推进, 任一轴无 >3 周期永久饥饿
      - No-Scoring 铁证: 源码 0 score/weight + 排序路径只引用时间/结构字段
        + 运行期 sidecar 纯结构记录
    """

    def test_dual_axis_round_robin_no_starvation_no_scoring(self, iso_env):
        tmp, clock = iso_env["tmp"], iso_env["clock"]
        provider = _new_provider(tmp, clock)
        gb = "gb" + "0" * 30
        gs = "gs" + "0" * 30
        # 无 completion_criteria → 永不自动完成, 全程 IN_PROGRESS 维持轮替
        provider._store_for().upsert_goal(_make_goal(gb, axis=AXIS_BRYAN, title="陪 Bry 读那本书"))
        provider._store_for().upsert_goal(_make_goal(gs, axis=AXIS_SELF, title="记录三次自我观察"))
        runner = MockHeartbeatRunner(provider, clock, start=_local(10))
        n = 10
        for _ in range(n):
            runner.step(timedelta(hours=25))

        assert len(runner.records) == n
        axes = []
        for r in runner.records:
            # G1: 单心跳候选 ≤1
            if r["candidate"] is not None:
                assert isinstance(r["candidate"], str)
                axes.append(r["candidate_axis"])
        assert len(axes) == n, f"期望 10 心跳全产候选（双轴轮替无 skip）: {axes}"

        # 同轴连续选中 ≤2
        max_streak = 1
        cur = 1
        for a, b in zip(axes, axes[1:]):
            cur = cur + 1 if a == b else 1
            max_streak = max(max_streak, cur)
        assert max_streak <= GOAL_MAX_ROTATION_STREAK == 2

        # 两轴均获推进
        bg, sg = runner.goal(gb), runner.goal(gs)
        assert bg["advance_count"] >= 1
        assert sg["advance_count"] >= 1
        assert bg["state"] == GOAL_STATE_IN_PROGRESS
        assert sg["state"] == GOAL_STATE_IN_PROGRESS

        # 任一轴无 >3 周期永久饥饿: 相邻两次被选中间隔 ≤3 心跳
        for axis in (AXIS_BRYAN, AXIS_SELF):
            idx = [i for i, a in enumerate(axes) if a == axis]
            assert idx, f"轴 {axis} 从未被选中（永久饥饿）"
            gaps = [b - a for a, b in zip(idx, idx[1:])]
            assert all(g <= 3 for g in gaps), (
                f"轴 {axis} 饥饿窗口 {gaps} 超过 3 周期"
            )

        # ── No-Scoring 铁证 ──
        # 1) 源码 AST 审计: 0 score/weight + 排序路径只引用时间/结构白名单字段
        assert _audit_provider_no_scoring() == []
        # 2) 运行期 sidecar: 纯结构记录（rotation 条目 = {axis, goal_id, ts}, 0 数值权重）
        state_file = tmp / "memory" / AGENT / "goal_provider.json"
        assert state_file.is_file()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert set(data.keys()) == {
            "last_candidate_at", "rotation", "consecutive_do_nothing", "consecutive_skips",
        }
        assert len(data["rotation"]) <= GOAL_ROTATION_WINDOW == 3
        for entry in data["rotation"]:
            assert set(entry.keys()) == {"axis", "goal_id", "ts"}
            assert isinstance(entry["ts"], (int, float))
            assert entry["axis"] in (AXIS_BRYAN, AXIS_SELF)
        assert data["consecutive_skips"] == 0          # 双轴正常轮替无饿死兜底触发
        # 3) 运行期无非整数（权重）比较路径: 候选选择序列完全由结构轮替产生——
        #    轴序列严格交替（bryan/self 相邻全不同 → 同轴连续 =1 ≤2 已证）

        # 附加: 每心跳决策 ≤1（Volition Gate 恒成立）
        assert all(r["decision_count"] <= 1 for r in runner.records)
        assert sum(r["decision_count"] for r in runner.records) == n


# ───────────────────────────────────────────────────────────
# 剧本 4: 达成与终态沉淀
# ───────────────────────────────────────────────────────────


class TestScenario4CompletionAndSediment:
    """目标执行最后一步 → 完成判定 → COMPLETED:
      - goals 表状态 COMPLETED + 时间戳写入（模拟时钟确定化）
      - 不再入活跃轮替（终态无出边 / 不装配）
      - 沉淀隔离: facts 表 0 直写（source='goal_direct' == 0 且总量 0）
      - 合规沉淀链: InnerLifeWriter → trace.jsonl（trigger_type=system,
        trace_ref=goal:{id}, extras.goal_id）交既有升华管线
      - elevation 数据目录零侵入
    """

    def test_completion_timestamps_and_sediment_isolation(self, iso_env):
        tmp, clock = iso_env["tmp"], iso_env["clock"]
        provider = _new_provider(tmp, clock)
        gid = "gc" + "0" * 30
        provider._store_for().upsert_goal(_make_goal(
            gid, axis=AXIS_BRYAN, title="完成九月健身打卡计划",
            completion_criteria=json.dumps({"kind": "interaction", "count": 2, "timeout_days": 30}),
        ))
        runner = MockHeartbeatRunner(provider, clock, start=_local(10))

        # ── 心跳 1: 第一步 → IN_PROGRESS advance=1 ──
        r1 = runner.run_heartbeat()
        assert r1["decision"] == "transmit"
        g1 = runner.goal(gid)
        assert g1["state"] == GOAL_STATE_IN_PROGRESS
        assert g1["advance_count"] == 1
        t1 = datetime.fromisoformat(r1["now"])

        # ── 心跳 2 (+25h): 最后一步 → 完成判定 → COMPLETED + 沉淀 ──
        r2 = runner.step(timedelta(hours=25))
        assert r2["decision"] == "transmit"
        g2 = runner.goal(gid)
        assert g2["state"] == GOAL_STATE_COMPLETED      # 终态
        assert g2["advance_count"] == 2                 # 最后一步落地
        t2 = datetime.fromisoformat(r2["now"])
        assert int(g2["last_advanced_at"]) == int(t2.timestamp())   # 时间戳写入
        assert int(g2["state_updated_at"]) == int(t2.timestamp())   # transition 同源

        # ── 心跳 3 (+25h): 终态不再入活跃轮替 ──
        r3 = runner.step(timedelta(hours=25))
        assert r3["candidate"] is None                  # 活跃池为空
        assert r3["decision_count"] == 0
        assert runner.goal(gid)["state"] == GOAL_STATE_COMPLETED  # 终态无出边

        # ── 沉淀隔离: goal 0 直写 facts（含 source='goal_direct' 计数 == 0）──
        assert _facts_count(tmp, "source='goal_direct'") == 0
        assert _facts_count(tmp) == 0                   # 更严: 全程 0 fact 写入
        # elevation 数据零侵入（升华数据与 goal 引擎物理隔离）
        elev = tmp / "elevation" / "elevation_nodes.jsonl"
        assert elev.read_text(encoding="utf-8") == ""

        # ── 合规沉淀链: InnerLifeWriter 事件日志 → trace.jsonl ──
        traces = _trace_records(tmp)
        assert traces, "COMPLETED 应经 InnerLifeWriter 写入事件日志"
        last = traces[-1]
        prov = last["provenance"]
        assert prov["trigger_type"] == "system"          # 0 新增 trigger_type
        assert prov["source_system"] == "system"         # 走既有 system 通道
        assert prov["trace_ref"] == f"goal:{gid}"        # goal 引用（不进 lineage tree）
        assert prov["extras"]["goal_id"] == gid
        assert prov["extras"]["advance_count"] == "2"    # 完成态快照
        # trace 记录 = 事件日志（身份+血缘）, 交既有 interpretation 链自然消费 → SAGE
        assert last["lineage_depth"] == 0                # root event, 不挂 lineage


# ───────────────────────────────────────────────────────────
# 汇编: 4 剧本入口（-k / 全量运行统一可见）
# ───────────────────────────────────────────────────────────


class TestGoalDrivenHarnessSuite:
    """TG-3 四大剧本汇编（运行顶层测试等价; 此 class 仅作清单与 phi 注记）。"""

    def test_suite_manifest(self):
        """清单断言: 四大剧本类均存在且各自至少 1 个刚性测试。"""
        classes = {
            "剧本1 跨心跳长程推进": TestScenario1LongRangeAdvance,
            "剧本2 突发中断与唤醒恢复": TestScenario2InterruptAndWakeup,
            "剧本3 双轴配额轮替防饥饿": TestScenario3DualAxisQuotaRoundRobin,
            "剧本4 达成与终态沉淀": TestScenario4CompletionAndSediment,
        }
        for name, cls in classes.items():
            tests = [m for m in dir(cls) if m.startswith("test_")]
            assert tests, f"{name} 无刚性测试"