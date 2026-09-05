"""
tests/social/test_sg2_guardrails.py — SG-2 护栏验收（TL-8 同款, 静态 + 行为双证）

覆盖:
  1. 0 直通 publish（AST: relation_settlement.py 无 publish/handler/actuator 调用）
  2. 0 新定时器（AST: scheduler 无新增 Timer/sleep; settle 无 asyncio 调度）
  3. 候选 ≤1（settle 不产出/装配任何 Motive 候选; 不动 Decision 池）
  4. 0 直写 facts（settle 后 graph.sqlite facts 0 行 / 文件不存在）
  5. No Scoring（settle/bands 源码 0 score 字样; bands AST 0 float 常量）
  6. D2: Motive.target 值域 — bryan/agent_id 合法, 非值域 fail-closed 拒绝,
     Motive 其余 5 字段与结构冻结断言

Frozen contract 边界 (0 change): Agency / TriggerEnvelope / InnerLifeEvent /
4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT 全部不触碰。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_sg2_guardrails.py -v
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

from src.goals.motive_provider import GOAL_QUOTA_WINDOW_SECONDS, reset_goal_providers
from src.goals.seed_provider import reset_seed_providers
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root
from src.social.relation_settlement import settle_relations
from src.soul.motive import (
    InvalidMotiveTargetError,
    Motive,
    get_agent_ids,
    make_motive,
    register_agent_id,
    set_agent_ids,
    validate_motive_target,
)

AGENT = "agent_sg2g"
ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# 静态审计（TL-8 同款 AST 刚性断言）
# ───────────────────────────────────────────────────────────

_FORBIDDEN_CALL_TOKENS = (
    "publish", "AGENCY_TRIGGER", "handler", "tool_registry", "actuator",
    "call_tool", "mark_transmitted", "send_message", "_fire",
)
_ASYNCIO_TOKENS: tuple = ("create_task", "ensure_future", "call_later",
                          "call_soon", "Timer")


def _audit_no_publish(path: Path) -> List[str]:
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
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


def _audit_no_timers(path: Path) -> List[str]:
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if isinstance(name, str) and name in _ASYNCIO_TOKENS:
                issues.append(f"禁止后台任务调用 {name}")
        if isinstance(node, ast.Import) and any(
            a.name == "asyncio" or a.name.startswith("asyncio.") for a in node.names
        ):
            issues.append("禁止导入异步调度模块")
        if isinstance(node, ast.ImportFrom) and (
            node.module == "asyncio" or (node.module or "").startswith("asyncio.")
        ):
            issues.append(f"禁止导入异步调度模块 {node.module}")
    return issues


def _audit_no_scoring(path: Path) -> List[str]:
    src = path.read_text(encoding="utf-8")
    issues: List[str] = []
    if "score" in src or "affinity" in src:
        issues.append("源码含 score/affinity 字样")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"源码解析失败: {e}"]
    floats = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, float)
    ]
    if floats:
        issues.append(f"源码含 float 常量: {floats}")
    return issues


def _audit_scheduler_no_new_loop() -> List[str]:
    """scheduler.py: 主循环仍只有既有 _run_loop 的 30s wake（2 处既有 sleep, 0 新增）;
    _goal_scan_all 新增 settle_relations 并列分支, 不新增任何循环/定时器。"""
    issues: List[str] = []
    src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    sleep_count = src.count("asyncio.sleep(30)")
    if sleep_count > 2:
        issues.append(
            f"asyncio.sleep(30) 出现 {sleep_count} 次（既有基线 = 2）"
        )
    run_loop_start = src.find("async def _run_loop")
    run_loop_end = src.find("async def _goal_scan_all")
    if run_loop_start == -1 or run_loop_end == -1 or run_loop_end <= run_loop_start:
        issues.append("scheduler 主循环/扫描函数定位失败")
    else:
        body = src[run_loop_start:run_loop_end]
        if body.count("while ") != 1:
            issues.append(f"_run_loop 内 while 数量 != 1")
    if "settle_relations(agent_id)" not in src:
        issues.append("_goal_scan_all 未挂载 settle_relations")
    return issues


class TestStaticGuardrails:
    def test_settlement_no_direct_publish(self):
        assert _audit_no_publish(ROOT / "src" / "social" / "relation_settlement.py") == []

    def test_settlement_no_timers(self):
        assert _audit_no_timers(ROOT / "src" / "social" / "relation_settlement.py") == []
        assert _audit_scheduler_no_new_loop() == []

    def test_settlement_and_bands_no_scoring(self):
        assert _audit_no_scoring(ROOT / "src" / "social" / "relation_settlement.py") == []
        assert _audit_no_scoring(ROOT / "src" / "social" / "relational_bands.py") == []

    def test_scheduler_register_injects_motive_target(self):
        src = (ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
        assert "register_agent_id(agent_id)" in src


# ───────────────────────────────────────────────────────────
# D2: Motive.target 值域（fail-closed）
# ───────────────────────────────────────────────────────────

class TestMotiveTargetDomain:
    def setup_method(self):
        set_agent_ids(["agent_rem", "agent_ruka"])

    def teardown_method(self):
        set_agent_ids([])

    def test_bryan_always_valid(self):
        assert validate_motive_target("bryan") is True

    def test_registered_agent_id_valid(self):
        assert validate_motive_target("agent_rem") is True
        assert validate_motive_target("agent_ruka") is True
        assert "agent_rem" in get_agent_ids()

    def test_unregistered_target_fail_closed(self):
        assert validate_motive_target("agent_unknown") is False
        assert validate_motive_target("") is False
        assert validate_motive_target(123) is False
        assert validate_motive_target(None) is False

    def test_make_motive_rejects_unregistered(self):
        with pytest.raises(InvalidMotiveTargetError):
            make_motive(
                motive_id="m1", content="想找 Rem 讨论音乐",
                target="agent_unknown", provenance_ref="x", created_at="2026-09-06T00:00:00+00:00",
            )

    def test_make_motive_accepts_agent_target(self):
        m = make_motive(
            motive_id="m1", content="想找 Rem 讨论音乐",
            target="agent_rem", provenance_ref="x", created_at="2026-09-06T00:00:00+00:00",
        )
        assert m.target == "agent_rem"
        assert m.content == "想找 Rem 讨论音乐"

    def test_motive_five_fields_frozen(self):
        """Motive 5 字段与结构冻结断言（D2 仅 target 值域解冻, 字段集 0 变更）。"""
        m = make_motive(
            motive_id="mid123", content="c", target="bryan",
            provenance_ref="p", created_at="t",
        )
        d = m.to_dict()
        assert list(d.keys()) == ["motive_id", "content", "target", "provenance_ref", "created_at"]
        assert Motive.from_dict(d).target == "bryan"
        src = (ROOT / "src" / "soul" / "motive.py").read_text(encoding="utf-8")
        assert "Motive dataclass" in src or "frozen=True" in src
        # target 类型仍为 str（值域语义扩展, 结构不变）
        assert isinstance(m.target, str)

    def test_register_agent_id_roundtrip(self):
        set_agent_ids([])
        register_agent_id("agent_yua")
        assert validate_motive_target("agent_yua") is True
        set_agent_ids([])


# ───────────────────────────────────────────────────────────
# 行为护栏: 沉淀层 0 候选 / 0 facts / 0 投递
# ───────────────────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    reset_seed_providers()
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path=db).close()
    yield tmp_path
    reset_seed_providers()
    reset_goal_providers()
    reset_data_root()


def _write_relationships(tmp_path: Path, others: Dict[str, Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": AGENT,
        "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": others,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _write_perception_trace(tmp_path: Path, records: List[Dict[str, Any]]) -> None:
    path = tmp_path / "world" / "perception_trace.jsonl"
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


def _entry(band: str = "known", **kw) -> Dict[str, Any]:
    e = {
        "impression": "x", "feeling": "neutral", "confidence": 0.0,
        "interaction_count": 3, "last_interaction_at": "2026-09-05T10:00:00+00:00",
        "last_updated": "2026-09-05T10:00:00+00:00", "created_at": "2026-08-01T00:00:00+00:00",
        "objective": {"reply_exchanges": 1, "co_presence_sessions": 4,
                      "dream_exchanges": 0, "last_signal_at": "2026-09-05T10:00:00+00:00"},
        "impression_tags": [], "relational_band": band,
        "band_updated_at": "2026-09-05T10:00:00+00:00",
        "last_relation_update_ref": None,
    }
    e.update(kw)
    return e


def _facts_count(tmp_path: Path) -> int:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────
# SG-2.2: 4.1 老数据 × 无信号 stranger 组合缺口（settle 全链）
# ───────────────────────────────────────────────────────────

def _write_legacy_41_relationships(tmp_path: Path) -> None:
    """写一份 schema 4.1 老格式 relationships.json（entry 无 relational_band /
    objective / last_relation_update_ref 键, 对应生产半成品前身）。"""
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": AGENT,
        "schema_version": "4.1",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": {
            "agent_rem": {
                "impression": "静かな人",
                "feeling": "neutral",
                "confidence": 0.5,
                "interaction_count": 2,
                "last_interaction_at": "2026-09-05T10:00:00+00:00",
                "last_updated": "2026-09-05T10:00:00+00:00",
                "created_at": "2026-08-01T00:00:00+00:00",
            }
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestSG22SettleLegacy41Compat:
    """SG-2.2 settle 全链回归：老 4.1 entry × 无信号 stranger 对子（窗口 0 信号
    文件）→ settle_relations 完整跑完（0 KeyError）、entry 补全 band、sidecar
    last_relation_update_at 推进（24h 节流恢复生效）、幂等 ref 落盘。"""

    def test_settle_41_no_signal_no_keyerror_sidecar_advances(self, iso_env):
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_legacy_41_relationships(tmp)
        # 窗口 0 信号: 不写 perception_trace / interactions → 无信号 stranger 对子
        result = settle_relations(AGENT, now=now, base_dir=tmp)
        # settle 完整跑完（修复前: apply 末尾日志 KeyError, _goal_scan_all
        # fail-closed 中断, 本行不达）
        assert result["skipped"] is None
        assert result["updated"] == 0
        assert result["demoted"] == 0
        # 老 entry 补全 band + 幂等 ref 落盘（半成品自愈）
        data = json.loads(
            (tmp / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8")
        )
        entry = data["others"]["agent_rem"]
        assert entry["relational_band"] == "stranger"
        assert entry["last_relation_update_ref"] == f"rel:agent_rem:{now.isoformat()}"
        assert entry["last_updated"] == now.isoformat()
        # sidecar last_relation_update_at 推进 → 24h 节流失效修复
        from src.goals.motive_provider import GoalMotiveProvider
        state = GoalMotiveProvider.for_agent(AGENT)._load_state()
        assert abs(state.last_relation_update_at - now.timestamp()) < 2.0
        # 节流生效（sidecar 推进的直接反面证据: 次轮跳过）
        r2 = settle_relations(
            AGENT, now=now + timedelta(hours=1), base_dir=tmp,
        )
        assert r2["skipped"] == "throttle"

    def test_settle_41_signal_upgrade_path_unchanged(self, iso_env):
        """有信号时升级路径不受影响（settle 层回归）: 老 4.1 entry + 窗口 reply
        信号 → stranger→known 照旧（SG-2.1 语义保留）。"""
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_legacy_41_relationships(tmp)
        _write_perception_trace(tmp, [{
            "event_id": "ev1", "timestamp": "2026-09-06T09:00:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social",
                                             "actor_id": "agent_rem"},
        }, {
            "event_id": "ev2", "timestamp": "2026-09-06T09:05:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social",
                                             "actor_id": AGENT},
        }])
        result = settle_relations(AGENT, now=now, base_dir=tmp)
        assert result["skipped"] is None
        assert result["updated"] >= 1
        data = json.loads(
            (tmp / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8")
        )
        entry = data["others"]["agent_rem"]
        assert entry["relational_band"] == "known"  # reply≥1 → stranger→known 照旧
        assert entry["objective"]["reply_exchanges"] == 1  # min(他 1, 我 1)


class TestSettlementBehavior:
    def test_settle_updates_band_and_counts(self, iso_env):
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {
            "agent_rem": _entry("known"),
        })
        _write_perception_trace(tmp, [{
            "event_id": "ev1", "timestamp": "2026-09-06T09:00:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social",
                                             "actor_id": "agent_rem"},
        }])
        _write_interactions(tmp, [{
            "ts": "2026-09-06T08:00:00+00:00", "type": "cross_chat",
            "agents": [AGENT, "agent_rem"], "content": "聊了音乐",
        }])
        result = settle_relations(AGENT, now=now, base_dir=tmp)
        assert result["skipped"] is None
        assert result["updated"] >= 1
        path = tmp / "soul" / AGENT / "relationships.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data["others"]["agent_rem"]
        # reply 成对折抵: min(对方 1, 我方 0) = 0; co=1
        assert entry["objective"]["co_presence_sessions"] == 5  # 4 + 1
        assert entry["objective"]["reply_exchanges"] == 1  # 窗口无我方 reply → 无折抵
        assert entry["objective"]["last_signal_at"] == "2026-09-06T10:00:00+00:00"
        assert entry["last_relation_update_ref"] == "rel:agent_rem:2026-09-06T10:00:00+00:00"
        assert entry["interaction_count"] == 3  # legacy 字段 0 变更

    def test_settle_24h_throttle(self, iso_env):
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("known")})
        settle_relations(AGENT, now=now, base_dir=tmp)
        # +1h → 节流窗内跳过
        r2 = settle_relations(AGENT, now=now + timedelta(hours=1), base_dir=tmp)
        assert r2["skipped"] == "throttle"
        # force 越过节流（测试用）
        r3 = settle_relations(AGENT, now=now + timedelta(hours=1), base_dir=tmp, force=True)
        assert r3["skipped"] is None

    def test_settle_zero_candidates(self, iso_env):
        """G1: 沉淀层不产出/装配任何 Motive 候选（不碰 Decision 池）。"""
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("familiar")})
        _write_perception_trace(tmp, [{
            "event_id": "ev1", "timestamp": "2026-09-06T09:00:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social",
                                             "actor_id": "agent_rem"},
        }])
        _write_interactions(tmp, [{
            "ts": "2026-09-06T08:00:00+00:00", "type": "cross_chat",
            "agents": [AGENT, "agent_rem"], "content": "x",
        }])
        settle_relations(AGENT, now=now, base_dir=tmp)
        # motive_trace.jsonl 不存在（沉淀层 0 写 trace / 0 候选入池）
        assert not (tmp / "soul" / "motive_trace.jsonl").exists()
        # goals 表 0 行（不建池）
        db = tmp / "memory" / AGENT / "graph.sqlite"
        conn = sqlite3.connect(db)
        try:
            n = int(conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0])
        finally:
            conn.close()
        assert n == 0

    def test_settle_zero_direct_facts(self, iso_env):
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("familiar")})
        settle_relations(AGENT, now=now, base_dir=tmp)
        assert _facts_count(tmp) == 0

    def test_settle_window_reply_pairing(self, iso_env):
        """成对折抵: 我方也有 reply 时 reply_exchanges = min(双方 reply 数)。"""
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("stranger", objective={
            "reply_exchanges": 0, "co_presence_sessions": 0,
            "dream_exchanges": 0, "last_signal_at": None,
        })})
        _write_perception_trace(tmp, [
            {"event_id": "ev1", "timestamp": "2026-09-06T09:00:00+00:00",
             "event_type": "reply", "extra": {"event_kind": "social", "actor_id": "agent_rem"}},
            {"event_id": "ev2", "timestamp": "2026-09-06T09:05:00+00:00",
             "event_type": "reply", "extra": {"event_kind": "social", "actor_id": "agent_rem"}},
            {"event_id": "ev3", "timestamp": "2026-09-06T09:10:00+00:00",
             "event_type": "reply", "extra": {"event_kind": "social", "actor_id": AGENT}},
        ])
        settle_relations(AGENT, now=now, base_dir=tmp)
        data = json.loads((tmp / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8"))
        entry = data["others"]["agent_rem"]
        assert entry["objective"]["reply_exchanges"] == 1  # min(2, 1)
        assert entry["relational_band"] == "known"  # reply≥1 → stranger→known

    def test_out_of_window_signals_ignored(self, iso_env):
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("stranger", objective={
            "reply_exchanges": 0, "co_presence_sessions": 0,
            "dream_exchanges": 0, "last_signal_at": None,
        })})
        # 30 天前的 reply（窗口外）→ 不计
        _write_perception_trace(tmp, [{
            "event_id": "ev1", "timestamp": "2026-08-01T09:00:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social", "actor_id": "agent_rem"},
        }])
        settle_relations(AGENT, now=now, base_dir=tmp)
        data = json.loads((tmp / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8"))
        entry = data["others"]["agent_rem"]
        assert entry["objective"]["reply_exchanges"] == 0
        assert entry["relational_band"] == "stranger"

    def test_two_way_reply_pairing_only_when_agent_replies(self, iso_env):
        """他 reply 但窗口内我方 0 reply → 不成对（保守近似）; 但提他方 reply 不算 0
        信号 —— 契约 §4.2 评估输入不含方向, v1 以成对近似计数。"""
        tmp = iso_env
        now = datetime(2026, 9, 6, 10, tzinfo=timezone.utc)
        _write_relationships(tmp, {"agent_rem": _entry("stranger", objective={
            "reply_exchanges": 0, "co_presence_sessions": 0,
            "dream_exchanges": 0, "last_signal_at": None,
        })})
        _write_perception_trace(tmp, [{
            "event_id": "ev1", "timestamp": "2026-09-06T09:00:00+00:00",
            "event_type": "reply", "extra": {"event_kind": "social", "actor_id": "agent_rem"},
        }])
        settle_relations(AGENT, now=now, base_dir=tmp)
        data = json.loads((tmp / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8"))
        entry = data["others"]["agent_rem"]
        assert entry["objective"]["reply_exchanges"] == 0
        assert entry["relational_band"] == "stranger"  # 无信号 → 不升带