"""
tests/goals/test_tl8_volition_guardrails.py — TL-8 Volition 相容护栏验收（LS-2）

目标: 验证 Goal Seed 生成器（LS-2 生产落地）守住 Volition Gate 六项不变量:

  1. 提醒类候选 0 直通 publish（候选只能进 Decision; 生成器自身 0 发送路径）
  2. 0 新定时器（生成器不新增 asyncio 循环/定时器, 静态 AST 断言）
  3. 候选 ≤1/心跳（配额与轮替仍然生效）
  4. 承诺候选不挤占自我轴（双轴轮替可观测）
  5. 0 直写 SAGE facts（facts 表 source='goal_direct' 计数 == 0 且总量 0）
  6. SM-4.2 分布锁（四元 Decision 值域封闭 + 生成器 0 新增 transmit 通道）

Frozen contract 边界 (0 change): Agency / TriggerEnvelope / InnerLifeEvent /
4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT 全部不触碰。
本文件只给 seed_provider + scheduler 挂载侧做验收（additive 模块）。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_tl8_volition_guardrails.py -v
"""
from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ACTIVE,
    Goal,
)
from src.goals.motive_provider import (
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.goals.seed_provider import (
    SEED_ROTATION,
    GoalSeedProvider,
    reset_seed_providers,
)
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root

AGENT = "agent_tl8"
ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# 静态断言 1: 种子生成器 0 发送路径（AST 审计: 无 publish/handler/tool 调用）
# ───────────────────────────────────────────────────────────

_FORBIDDEN_CALL_TOKENS = (
    "publish", "AGENCY_TRIGGER", "handler", "tool_registry", "actuator",
    "call_tool", "mark_transmitted", "send_message", "_fire",
)


def _audit_seed_provider_no_publish() -> List[str]:
    """AST 审计 src/goals/seed_provider.py: 0 直通 publish 路径。

    R1: 禁止调用形态（publish / AGENCY_TRIGGER / handler / tool_registry /
        actuator / call_tool / mark_transmitted / send_message / _fire）。
    R2: 禁止 import 上述发送路径模块名。
    （docstring/注释里描述性字样不计 — 只看实际代码形态）
    返回违规描述列表; 空 = 通过。
    """
    issues: List[str] = []
    src = (ROOT / "src" / "goals" / "seed_provider.py").read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        issues.append(f"源码解析失败: {e}")
        return issues
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if isinstance(name, str) and any(t in name for t in _FORBIDDEN_CALL_TOKENS):
                issues.append(f"禁止调用 {name}")
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if any(t in alias.name for t in _FORBIDDEN_CALL_TOKENS):
                    issues.append(f"禁止导入 {alias.name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(t in alias.name for t in _FORBIDDEN_CALL_TOKENS):
                    issues.append(f"禁止导入 {alias.name}")
    return issues


# ───────────────────────────────────────────────────────────
# 静态断言 2: 0 新定时器（AST 审计: 无 asyncio loop / Timer / sleep / cron）
# ───────────────────────────────────────────────────────────

_ASYNCIO_TOKENS: tuple = ("create_task", "ensure_future", "call_later",
                          "call_soon", "Timer")


def _audit_seed_provider_no_timers() -> List[str]:
    """AST 审计: 生成器 0 新增定时器/异步循环。

    - R1: 无后台任务/定时器调用（create_task / ensure_future / call_later /
          call_soon / threading.Timer / Timer）
    - R2: 无 asyncio 模块 import（async def 方法由既有 async 锚点 await,
          不构成新循环; 0 新 tick）
    返回违规描述列表; 空 = 通过。
    """
    issues: List[str] = []
    src = (ROOT / "src" / "goals" / "seed_provider.py").read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        issues.append(f"源码解析失败: {e}")
        return issues
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if isinstance(name, str) and name in _ASYNCIO_TOKENS:
                issues.append(f"禁止后台任务调用 {name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "asyncio" or alias.name.startswith("asyncio."):
                    issues.append(f"禁止导入异步调度模块 {alias.name}")
        if isinstance(node, ast.ImportFrom):
            if node.module == "asyncio" or (node.module or "").startswith("asyncio."):
                issues.append(f"禁止导入异步调度模块 {node.module}")
    return issues


# ───────────────────────────────────────────────────────────
# No-Scoring 静态审计（继承 TG-3 精神, 施加于生成器）
# ───────────────────────────────────────────────────────────

_SORT_KEY_ALLOWED_FIELDS = frozenset({
    "timestamp", "last_advanced_at", "last_ts", "created_at", "ts",
    "axis", "goal_id", "event_id", "fact_id", "node_id",
})


def _audit_seed_provider_no_scoring() -> List[str]:
    """生成器 No-Scoring 铁证: 源码 0 score 字样; 排序路径 key 只引用
    时间/结构白名单字段（weight/confidence 作为过滤阈值与只读展示不参与排序选择:
    契约 §2.4 本身以 weight 描述 S2 依据, 字符串级禁 weight 会误伤合法阈值过滤）。"""
    issues: List[str] = []
    src = (ROOT / "src" / "goals" / "seed_provider.py").read_text(encoding="utf-8")
    if "score" in src:
        issues.append("seed_provider.py 源码含 'score' 字样")
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
                if isinstance(sl, ast.Index):
                    sl = sl.value
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    refs.add(sl.value)
            non_allowed = refs - _SORT_KEY_ALLOWED_FIELDS
            if non_allowed:
                issues.append(
                    f"{func_name} key 引用非白名单字段 {sorted(non_allowed)}"
                )
    return issues


# ───────────────────────────────────────────────────────────
# 静态断言: scheduler 挂载仍为既有 30s wake 并列分支（0 新循环）
# ───────────────────────────────────────────────────────────


def _audit_scheduler_no_new_loop() -> List[str]:
    """审计 scheduler.py: 主循环仍只有既有 _run_loop 的 30s wake（正常分支 +
    except 恢复分支两处既有 sleep, 0 新增）; _goal_scan_all 只新增 seed 生成器
    调用（并列分支 await）, 不新增任何循环/定时器。"""
    issues: List[str] = []
    src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    sleep_count = src.count("asyncio.sleep(30)")
    if sleep_count > 2:
        issues.append(
            f"asyncio.sleep(30) 出现 {sleep_count} 次（既有基线 = 2: 主循环 + except 恢复）"
        )
    # 主循环 while 数量仍为 1（没有新增第二循环）
    run_loop_start = src.find("async def _run_loop")
    run_loop_end = src.find("async def _goal_scan_all")
    if run_loop_start == -1 or run_loop_end == -1 or run_loop_end <= run_loop_start:
        issues.append("scheduler 主循环/扫描函数定位失败")
    else:
        body = src[run_loop_start:run_loop_end]
        while_count = body.count("while ")
        if while_count != 1:
            issues.append(f"_run_loop 内 while 数量 {while_count}（期望恰 1 个主循环）")
    # 生成器必须以 await 调用形态挂载在 _goal_scan_all 内（并列分支, 非新循环）
    if "await GoalSeedProvider.for_agent(agent_id).scan_seeds()" not in src:
        issues.append("_goal_scan_all 未挂载 GoalSeedProvider.scan_seeds")
    return issues


# ───────────────────────────────────────────────────────────
# Fixture: 隔离 data_root + 生成器/供应商复位
# ───────────────────────────────────────────────────────────


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    reset_seed_providers()
    # relationships manager 是进程级 singleton（data_dir 固定）→ 跨测试污染;
    # 每用例重建, 让它跟随当前 data_root 读隔离目录
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path=db).close()
    yield tmp_path
    reset_seed_providers()
    reset_goal_providers()
    reset_data_root()


# ───────────────────────────────────────────────────────────
# 数据触点写入 helpers（全部只读消费方测试夹具）
# ───────────────────────────────────────────────────────────

def _seed_provider(tmp_path: Path, llm: Any) -> GoalSeedProvider:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    return GoalSeedProvider(agent_id=AGENT, store=store, llm_call=llm)


def _llm(title: str, description: str = "内心独白（stub）"):
    async def stub(messages, agent_id, max_tokens, temperature):
        return json.dumps({"title": title, "description": description})
    return stub


def _llm_bad():
    async def stub(messages, agent_id, max_tokens, temperature):
        return None
    return stub


def _local(hour: int = 10, day: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _goals(tmp_path: Path) -> List[Goal]:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()
        return [Goal(**dict(r)) for r in rows]
    finally:
        conn.close()


def _facts_count(tmp_path: Path, where: str = "") -> int:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        sql = "SELECT COUNT(*) FROM facts" + (f" WHERE {where}" if where else "")
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def _write_relationships(tmp_path: Path, entry: Dict[str, Any]) -> None:
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": AGENT,
        "schema_version": "4.1",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": {"user_bryan": entry},
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_perception_trace(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_trace(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "inner_life" / "trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_interactions(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / "interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_elevation(tmp_path: Path, nodes: List[Dict[str, Any]]) -> None:
    path = tmp_path / "elevation" / "elevation_nodes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )


def _write_tool_trace(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / "tool_registry_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_motive_trace(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / "motive_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _write_last_seen(
    tmp_path: Path, ago_hours: float, base: Optional[datetime] = None
) -> None:
    """写 Bry last_seen（默认相对真实 now; 提供 base 时相对模拟基准时刻）。

    时序一致性: 模拟 now 是固定基准日, 若用真实 now 会对不上
    （真实日期 ≠ 基准日期会差出数十小时 → 抑制判定漂移）。
    """
    base = base if base is not None else datetime.now(timezone.utc)
    path = tmp_path / "state" / "bryan_last_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = (base - timedelta(hours=ago_hours)).isoformat()
    path.write_text(json.dumps({"last_recv_ts": ts}), encoding="utf-8")


def _seed_row(key: str, axis: str, ref: str, material: str = "素材") -> Dict[str, Any]:
    return {"key": key, "axis": axis, "ref": ref, "material": material}


# ───────────────────────────────────────────────────────────
# 剧本 0: 静态护栏（AST 级刚证, 0 production mutation）
# ───────────────────────────────────────────────────────────

class TestStaticGuardrails:
    """0 直通 publish / 0 新定时器 / No-Scoring 的源码级静态断言。"""

    def test_no_direct_publish_path(self):
        assert _audit_seed_provider_no_publish() == []

    def test_no_new_timers_static(self):
        assert _audit_seed_provider_no_timers() == []
        assert _audit_scheduler_no_new_loop() == []

    def test_no_scoring_static(self):
        assert _audit_seed_provider_no_scoring() == []


# ───────────────────────────────────────────────────────────
# 剧本 1: 24h 节流 + 轮序确定性 + 幂等去重
# ───────────────────────────────────────────────────────────

class TestThrottleAndRotation:
    """生成器 24h 节流（复用 GOAL_QUOTA_WINDOW_SECONDS, 0 新配额体系）;
    8 源固定轮序确定性; seed_source_ref 精确幂等去重。"""

    def test_throttle_24h_one_goal_per_window(self, iso_env):
        tmp = iso_env
        # 只有 B1 承诺源有数据
        _write_relationships(tmp, {
            "impression": "最近在忙转型",
            "feeling": "warming",
            "confidence": 0.6,
            "interaction_count": 5,
            "last_interaction_at": "2026-09-05T20:00:00+00:00",
        })
        prov = _seed_provider(tmp, _llm("找机会关心 Bry 的转型复盘", "他上次提过"))
        created = asyncio.run(prov.scan_seeds(now=_local(10)))
        assert len(created) == 1
        assert created[0].seed_source_ref == "relationship:user_bryan"
        assert created[0].axis == AXIS_BRYAN
        assert created[0].state == GOAL_STATE_ACTIVE
        crit = json.loads(created[0].completion_criteria)
        assert crit == {"kind": "interaction", "count": 2, "timeout_days": 7}
        # 同窗第二次调用（+1h）→ 节流 0 创建
        again = asyncio.run(prov.scan_seeds(now=_local(11)))
        assert again == []
        assert len(_goals(tmp)) == 1
        # 跨 24h 后仍同引用 → 幂等去重 0 新 goal（0 文本相似度, 精确 ref）
        after = asyncio.run(prov.scan_seeds(now=_local(10, day=1)))
        assert after == []
        assert len(_goals(tmp)) == 1

    def test_cursor_deterministic_rotation(self, iso_env):
        tmp = iso_env
        # 只给 S1 elevation 数据 → 轮序第 5 位（B1-B4 先行空转探测）
        _write_elevation(tmp, [{
            "node_id": "el_node_1", "node_type": "belief", "content": "我想更懂自己",
            "agent_id": AGENT, "created_ts": "2026-09-01T00:00:00+00:00",
            "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("我想更懂自己"))
        created = asyncio.run(prov.scan_seeds(now=_local(10)))
        assert len(created) == 1
        assert created[0].seed_source_ref == "elevation:el_node_1"
        assert created[0].axis == AXIS_SELF
        # 游标后移 → 下一轮从 calendar（index 1）起, B 轴无数据 + S 轴 elevation
        # 已被追踪（幂等）→ 轮到 fact 前仍无创建; 验证轮序状态持久化
        state_file = tmp / "memory" / AGENT / "goal_provider.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["seed_source_cursor"] == 5  # index 0 + 5 探测 = 5
        assert state["last_seed_axis"] == AXIS_SELF
        assert state["seed_axis_streak"] == 1
        assert state["last_seed_scan_at"] > 0

    def test_all_eight_sources_probe(self, iso_env):
        """8 源逐个数据触点注入 → 每源独立命中一次（确定性轮序）。"""
        tmp = iso_env
        # 8 源夹具（每个 source 唯一 ref）
        _write_relationships(tmp, {"impression": "承诺", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_perception_trace(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_1", "timestamp": "2026-09-05T10:00:00+00:00",
        }])
        _write_trace(tmp, [{
            "event_id": "tr_1", "ts": "2026-09-05T09:00:00+00:00",
            "provenance": {"trigger_type": "diary:morning", "trace_ref": None,
                           "actor_id": AGENT},
        }])
        _write_interactions(tmp, [{
            "ts": "2026-09-05T08:00:00+00:00", "type": "cross_chat",
            "agents": [AGENT, "agent_other"], "content": "聊了最近的书",
        }])
        _write_elevation(tmp, [{
            "node_id": "el_1", "node_type": "trait", "content": "对未知好奇",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        db = tmp / "memory" / AGENT / "graph.sqlite"
        store = GraphStore(db_path=db)
        from src.memory.sage.models import Fact
        # 注: confidence 列存在既有 bug（add_fact 不写, 恒 1.0）→ 用 weight 过滤
        store.add_fact(Fact(subject="bryan", predicate="talked_about",
                            object="面试", weight=0.4, timestamp=1000.0))
        store.close()
        _write_tool_trace(tmp, [{
            "ts": "2026-09-05T07:00:00+00:00", "event_type": "tool_registered",
            "tool_id": "mcp:calendar", "name": "calendar",
            "capability_group": "observe_environment",
            "permission_class": "auto_approved",
        }])
        _write_motive_trace(tmp, [
            {"motive_id": "m1", "agent_id": AGENT, "content": "反复想秋天的计划",
             "provenance_ref": "trace:tr_x",
             "created_at": "2026-09-05T06:00:00+00:00", "updated_at": "2026-09-05T06:00:00+00:00"},
        ])

        # 逐轮扫描（每轮 +25h 跨节流窗）: 期待按 8 源固定轮序命中:
        # 第 1 轮: commitment(bryan)  第 2 轮: calendar(bryan)
        # 第 3 轮: trace(bryan)       第 4 轮: interaction(bryan)
        # 第 5 轮: elevation(self)    第 6 轮: fact(self)
        # 第 7 轮: tool(self)         第 8 轮: motive_trace(self)
        prov = _seed_provider(tmp, _llm("stub"))
        expect_refs = {
            "relationship:user_bryan",
            "calendar:cal_1",
            "trace:tr_1",
            "interaction:2026-09-05T08:00:00+00:00",
            "elevation:el_1",
            "tool:mcp:calendar",
            "motive_trace:2026-09-05T06:00:00+00:00",
        }
        seen: List[str] = []
        # 轮序起点确定性: 第 1 轮必为轮序首位 commitment(bryan)
        first = asyncio.run(prov.scan_seeds(now=_local(10)))
        assert len(first) == 1
        assert first[0].seed_source_ref == "relationship:user_bryan"
        seen.append(first[0].seed_source_ref)
        # 逐轮扫描（每轮 +25h 跨节流窗）: 双轴约束（同轴 ≤2 强制换轴）会
        # 交叉命中, 但 8 源全部可及 — 循环直到 8 个不同 ref 全收集
        for day in range(1, 16):
            if len(seen) == 8:
                break
            created = asyncio.run(prov.scan_seeds(now=_local(10, day=day)))
            if created:
                ref = created[0].seed_source_ref
                if ref not in seen:
                    if ref.startswith("fact:"):
                        seen.append("fact:<uuid>")
                    else:
                        seen.append(ref)
            assert len(_goals(tmp)) <= 8, "每源幂等去重后 0 重复 goal"
        assert set(seen) == expect_refs | {"fact:<uuid>"}, f"8 源未全覆盖: {seen}"
        # 全源幂等后: 后续扫描 0 创建
        assert asyncio.run(prov.scan_seeds(now=_local(10, day=20))) == []
        assert len(_goals(tmp)) == 8


# ───────────────────────────────────────────────────────────
# 剧本 2: 方案 B 语义化 fail-closed
# ───────────────────────────────────────────────────────────

class TestSemantizationFailClosed:
    """LLM 失败 / 坏输出 → 该种子跳过, 不产生脏 goal（fail-closed）。"""

    def test_llm_none_drops_seed(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "x", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 1,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        prov = _seed_provider(tmp, _llm_bad())
        created = asyncio.run(prov.scan_seeds(now=_local(10)))
        assert created == []
        assert _goals(tmp) == []
        # 但扫描已记账（节流戳更新）→ 本轮不会无限重试; 跨窗后仍可重试
        again = asyncio.run(prov.scan_seeds(now=_local(11)))
        assert again == []

    def test_llm_bad_json_drops_seed(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "x", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 1,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})

        async def bad(messages, agent_id, max_tokens, temperature):
            return "这根本不是 JSON"
        prov = _seed_provider(tmp, bad)
        assert asyncio.run(prov.scan_seeds(now=_local(10))) == []
        assert _goals(tmp) == []

    def test_llm_empty_title_drops_seed(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "x", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 1,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        prov = _seed_provider(tmp, _llm("  "))
        assert asyncio.run(prov.scan_seeds(now=_local(10))) == []
        assert _goals(tmp) == []

    def test_llm_title_too_long_drops_seed(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "x", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 1,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        prov = _seed_provider(tmp, _llm("长" * 121))
        assert asyncio.run(prov.scan_seeds(now=_local(10))) == []
        assert _goals(tmp) == []


# ───────────────────────────────────────────────────────────
# 剧本 3: 作息相位抑制（quiet 23-08 / last_seen>4h → B 轴抑制, S 轴不受限）
# ───────────────────────────────────────────────────────────

class TestPhaseSuppression:
    """LS-1 §4.2: quiet 时段与 Bryan 离开 >4h 不生成 B 轴种子; 自我轴照常。"""

    def test_quiet_hours_suppress_bryan_axis_only(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 3,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_elevation(tmp, [{
            "node_id": "el_n", "node_type": "value", "content": "夜晚的思考",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        # quiet 时段（凌晨 02:00）: B1 跳过 → 轮序走到 S1 → self goal
        created = asyncio.run(prov.scan_seeds(now=_local(2)))
        assert len(created) == 1
        assert created[0].axis == AXIS_SELF
        assert created[0].seed_source_ref == "elevation:el_n"

    def test_bryan_away_over_4h_suppress_bryan_axis(self, iso_env):
        tmp = iso_env
        _write_last_seen(tmp, ago_hours=10, base=_local(12).astimezone(timezone.utc))
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_elevation(tmp, [{
            "node_id": "el_b", "node_type": "belief", "content": "自我成长",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        assert created[0].axis == AXIS_SELF

    def test_bryan_recent_allows_bryan_axis(self, iso_env):
        tmp = iso_env
        _write_last_seen(tmp, ago_hours=1, base=_local(12).astimezone(timezone.utc))
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        prov = _seed_provider(tmp, _llm("stub"))
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        assert created[0].axis == AXIS_BRYAN


# ───────────────────────────────────────────────────────────
# 剧本 4: 双轴约束（承诺类不挤占自我轴）+ 候选 ≤1/心跳
# ───────────────────────────────────────────────────────────

class TestDualAxisGuardrail:
    """Bryan 轴承诺候选密集填充时, 生成约束（同轴 ≤2 强制换轴）仍保证
    自我轴出现; 候选装配 ≤1/心跳不受生成器影响。"""

    def test_bryan_seeds_cannot_crowd_out_self_axis(self, iso_env):
        tmp = iso_env
        # B1-B4 全命中 + S1 命中 → 轮序前 4 位全 bryan, 但同轴 ≤2 强制换轴
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_perception_trace(tmp, [{
            "event_type": "calendar_event", "accepted": True,
            "novelty_id": "cal_x", "timestamp": "2026-09-05T10:00:00+00:00",
        }])
        _write_trace(tmp, [{
            "event_id": "tr_x", "ts": "2026-09-05T09:00:00+00:00",
            "provenance": {"trigger_type": "diary:morning", "trace_ref": None,
                           "actor_id": AGENT},
        }])
        _write_interactions(tmp, [{
            "ts": "2026-09-05T08:00:00+00:00", "type": "shared_event",
            "agents": [AGENT, "agent_other"], "content": "一起的活动",
        }])
        _write_elevation(tmp, [{
            "node_id": "el_y", "node_type": "belief", "content": "自我的命题",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        axes: List[str] = []
        refs: List[str] = []
        for day in range(4):
            created = asyncio.run(prov.scan_seeds(now=_local(10, day=day)))
            if created:
                axes.append(created[0].axis)
                refs.append(created[0].seed_source_ref)
        # 第 1 轮: commitment(bryan); 第 2 轮: calendar(bryan) → streak=2;
        # 第 3 轮: 强制换轴跳过 trace/interaction → elevation(self);
        # 第 4 轮: cursor 回转到 calendar 无新命中…elevation 已追踪 → interaction(self 前) 无
        # 实际期望: [bryan, bryan, self]
        assert axes[:3] == [AXIS_BRYAN, AXIS_BRYAN, AXIS_SELF], axes
        # 同轴连续生成 ≤2
        streak = mx = 1
        for a, b in zip(axes, axes[1:]):
            streak = streak + 1 if a == b else 1
            mx = max(mx, streak)
        assert mx <= 2, f"同轴连续生成超限: {axes}"

    def test_candidate_at_most_one_per_heartbeat(self, iso_env):
        """生成器建池后, 装配/决策链仍 ≤1 候选/心跳（Volition Gate G1）。"""
        tmp = iso_env
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        prov = _seed_provider(tmp, _llm("关心 Bryan 的近况"))
        created = asyncio.run(prov.scan_seeds(now=_local(10)))
        assert len(created) == 1

        # 装配链: 每心跳至多 1 候选（24h 配额 + 轮替不变）
        db = tmp / "memory" / AGENT / "graph.sqlite"
        motive_prov = GoalMotiveProvider(agent_id=AGENT, store=GraphStore(db_path=db))
        c1 = motive_prov.assemble_candidate(now=_local(10, day=1))
        assert c1 is not None
        assert c1.provenance_ref == f"goal:{created[0].goal_id}"
        # 同窗（25h 内已产候选）→ 配额挡 0 候选
        c2 = motive_prov.assemble_candidate(now=_local(10, day=1) + timedelta(hours=1))
        assert c2 is None


# ───────────────────────────────────────────────────────────
# 剧本 5: 0 直写 SAGE facts + 四元 Decision 值域封闭（SM-4.2 分布锁形式）
# ───────────────────────────────────────────────────────────

class TestFactsIsolationAndDecisionDomain:
    """生成器 0 直写 facts（source='goal_direct' 计数 == 0 且总量 0）;
    候选经既有四元 Decision（值域封闭, 0 新增 transmit 通道）。"""

    def test_generator_zero_direct_facts(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_elevation(tmp, [{
            "node_id": "el_z", "node_type": "belief", "content": "z",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        for day in range(3):
            asyncio.run(prov.scan_seeds(now=_local(10, day=day)))
        assert len(_goals(tmp)) >= 1
        assert _facts_count(tmp, "source='goal_direct'") == 0
        assert _facts_count(tmp) == 0  # 更严: 全程 0 facts 写入

    def test_candidate_decision_domain_closed_under_four_actions(self, iso_env):
        """生成器产出的 goal 候选 → assemble_candidate → decide_motive:
        决策值域始终 ∈ 四元（SM-4.2）; stub 分布采样下 0 越界、0 直接发送。"""
        tmp = iso_env
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_elevation(tmp, [{
            "node_id": "el_d", "node_type": "belief", "content": "自我的命题",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        # 双轴 goal（bryan + self）→ 强制换轴总有候选 → 每心跳候选 ≤1 且连续产出
        g1 = asyncio.run(prov.scan_seeds(now=_local(10)))
        g2 = asyncio.run(prov.scan_seeds(now=_local(10, day=1)))
        assert len(g1) == 1 and len(g2) == 1
        assert {g.axis for g in (g1[0], g2[0])} == {AXIS_BRYAN, AXIS_SELF}

        from src.soul.decision import DECISION_ACTIONS, decide_motive
        db = tmp / "memory" / AGENT / "graph.sqlite"
        motive_prov = GoalMotiveProvider(agent_id=AGENT, store=GraphStore(db_path=db))
        decision_calls = {"count": 0}

        # stub 分布（覆盖四元采样, 观察形式: 值域封闭 + 全部动作可达;
        # SM-4.2 百分比基线由真实 LLM 分布决定, 单元级锁「值域 + 通道」）
        seq = ["do_nothing", "do_nothing", "observe", "do_nothing",
               "reflect", "do_nothing", "transmit", "do_nothing"]

        async def decision_llm(messages, agent_id, max_tokens, temperature):
            decision_calls["count"] += 1
            action = seq[decision_calls["count"] - 1]
            return json.dumps({"decision": action, "reason": "tl8-stub"})

        results: List[str] = []
        for i in range(8):
            motive = motive_prov.assemble_candidate(
                now=_local(12, day=1) + timedelta(hours=25 * i)
            )
            if motive is None:
                continue
            result = asyncio.run(decide_motive(
                motive, AGENT, llm_call=decision_llm,
                current_time=_local(12, day=1).strftime("%Y-%m-%d %H:%M"),
            ))
            results.append(result.decision)
            assert result.decision in DECISION_ACTIONS  # 值域封闭
        # 双轴轮替下 8 心跳全产候选（强制换轴总有另一轴可装配）
        assert len(results) == 8, f"候选应全心跳产出: {results}"
        # stub 分布全部在合法四元内, observe/reflect/transmit/do_nothing 全部可达
        assert results.count("do_nothing") >= 3
        assert sorted(set(results)) == sorted(DECISION_ACTIONS)
        # 0 直接发送通道: 决策后无任何 publish 存在（生成器 AST 已证）;
        # 这里再证: 全程未调用任何发送路径（stub 只被 Decision 调用）
        assert decision_calls["count"] == 8


# ───────────────────────────────────────────────────────────
# 剧本 6: 24h 节流 + 轮替组合（工作单验收: 配额与轮替仍然生效）
# ───────────────────────────────────────────────────────────

class TestQuotaAndRoundRobinStillEffective:
    """生成器存在时, 候选装配 24h/1 配额与双轴轮替 0 退化。"""

    def test_assemble_quota_unchanged_with_generator(self, iso_env):
        tmp = iso_env
        # 生成 2 个 goal（两个轴）
        _write_relationships(tmp, {"impression": "p", "feeling": "neutral",
                                   "confidence": 0.5, "interaction_count": 2,
                                   "last_interaction_at": "2026-09-05T10:00:00+00:00"})
        _write_elevation(tmp, [{
            "node_id": "el_q", "node_type": "belief", "content": "q",
            "agent_id": AGENT, "lifecycle_state": "active",
        }])
        prov = _seed_provider(tmp, _llm("stub"))
        g1 = asyncio.run(prov.scan_seeds(now=_local(10)))
        g2 = asyncio.run(prov.scan_seeds(now=_local(10, day=1)))
        assert len(g1) == 1 and len(g2) == 1

        db = tmp / "memory" / AGENT / "graph.sqlite"
        motive_prov = GoalMotiveProvider(agent_id=AGENT, store=GraphStore(db_path=db))
        # 心跳 1: 装配 1 候选; 心跳 2 (+25h): 装配另一轴候选（轮替）
        c1 = motive_prov.assemble_candidate(now=_local(12))
        c2 = motive_prov.assemble_candidate(now=_local(12, day=1))
        assert c1 is not None and c2 is not None
        g1_id, g2_id = g1[0].goal_id, g2[0].goal_id
        ids = [c1.provenance_ref, c2.provenance_ref]
        assert f"goal:{g1_id}" in ids and f"goal:{g2_id}" in ids
        axes = [g.axis for g in _goals(tmp)]
        assert AXIS_BRYAN in axes and AXIS_SELF in axes