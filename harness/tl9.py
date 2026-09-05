"""
harness/tl9.py — TL-9 Relation Evolution Long-Horizon Harness (C-3 闭环钢印)

工单 TL-9 (决策已定, 照做):
  - 目标: 在受控多体模拟下完整实证「公开发言 → 他者感知 → 相互 Reply →
    关系带整数跃迁 → B5 种子触发 → Motive.target 指向他者灵魂」端到端生命链路,
    四大剧本硬断言全绿。
  - 双 agent fixture (agent_ruka / agent_akane, 映射 configs/default.yaml 既有
    persona 配置), 隔离 data_root (data/time_lapse/TL-9/<scenario>/<run_id>),
    0 production mutation (run 系列前后 production data/ 逐档 byte-hash 0 diff)。
  - SimulationClock: harness-local 推进 (24h 粒度 tick), 禁止加速 production
    scheduler、0 新定时器。
  - 信号注入用真实载体: reply → world/perception_trace.jsonl (event_type="reply",
    extra.event_kind="social"), co-presence → soul/interactions.jsonl
    (agents 数组含双方), 对照 relation_settlement.collect_window_signals 实际
    读取来源构造 (隔离隔离副本, 0 碰生产文件)。
  - 结算驱动: 每 24h 窗口调 settle_relations (真实实现, 含 evaluate_band /
    apply_relation_evaluation / 幂等 ref / 24h 节流), 禁止另写一套 band 逻辑。
  - 种子/动机链: B5 走 GoalSeedProvider.scan_seeds 真实轮替 + 确定性 stub LLM
    (方案 B 语义化通道, 0 网络), 产物过 make_motive (fail-closed valid target),
    motive 落 MotiveTraceStore 读回验证; Decision 层 stub (确定性四元),
    禁止另写 classifier。
  - 记录 checkpoint 轨迹 (band/belief 快照/计数, JSON 落隔离目录)。

四大剧本 (scenario):
  1. relation_up (剧本 1 关系正向跃迁): A/B 公开互动 + 交互 Reply 累计 →
     stranger→known (reply≥1 OR co≥2) → familiar (reply≥3 AND co≥5) →
     close; 硬断言: 单次 24h 结算至多升 1 级 / 24h 窗口节流生效
     (窗口内重复信号不重复结算)。
  2. other_target (剧本 2 他者目标自发生成): band known + 印象标签 →
     B5 种子出现且 Motive.target == "agent_akane" (make_motive 合法);
     stranger 时 0 种子 (B5 不发射); 非法 target fail-closed。
  3. natural_cooling (剧本 3 现象学自然冷却): 快进 30 天无新信号 →
     结算后降 1 级 (familiar→known→stranger 方向), 断言不跌穿 stranger,
     band_updated_at 更新; 恰好 30 天整不降 (>30 天才降, 契约已落地语义)。
     SG-2.1 修复追认: 底带 stranger 无信号不慢爬回升 (降带后再无信号 30 天
     保持 stranger, 振荡消除), 窗口出现新 reply → 正常升回 known (门槛照旧)。
  4. firewall_no_scoring (剧本 4 三大防线 + No-Scoring 刚性复核):
     AST 审计 SG-2 相关模块 0 直通 publish / 0 数值打分 (float 权重常数);
     Direct Query (sqlite3 只读) 断言 SAGE facts 表 0 关系域写入、
     自体情景记忆 0 他者事件 (防线 3 Identity Firewall);
     0 直写 facts / 0 新定时器 / 候选 ≤1。

Frozen Contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段结构 /
DECISION-PROMPT 一律不动; 本 harness 只读生产源码、写隔离目录。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.goals.motive_provider import (
    GOAL_QUOTA_WINDOW_SECONDS,
    reset_goal_providers,
)
from src.goals.seed_provider import GoalSeedProvider, reset_seed_providers
from src.memory.sage.graph_store import GraphStore
from src.paths import data_root, reset_data_root
from src.social.relation_settlement import settle_relations
from src.soul.decision import decide_motive
from src.soul.motive import (
    Motive,
    MotiveTraceStore,
    make_motive,
    new_motive_id,
    set_agent_ids,
)

from .clock import SimulationClock
from .runner import snapshot_data_root_hashes, verify_zero_mutation

logger = logging.getLogger("soul_os.harness.tl9")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL9_EXPERIMENT_ID = "TL-9"
TL9_SEED = 42

# 双 agent fixture (映射 configs/default.yaml 既有 persona 配置)
TL9_AGENT_A = "agent_ruka"      # 感知主体 (主体视角)
TL9_AGENT_B = "agent_akane"     # 他者 (B5 种子 target)

SCENARIO_RELATION_UP = "relation_up"
SCENARIO_OTHER_TARGET = "other_target"
SCENARIO_NATURAL_COOLING = "natural_cooling"
SCENARIO_FIREWALL = "firewall_no_scoring"

SCENARIOS = (
    SCENARIO_RELATION_UP,
    SCENARIO_OTHER_TARGET,
    SCENARIO_NATURAL_COOLING,
    SCENARIO_FIREWALL,
)

SCENARIO_LABELS = {
    SCENARIO_RELATION_UP: "剧本 1 关系正向跃迁",
    SCENARIO_OTHER_TARGET: "剧本 2 他者目标自发生成",
    SCENARIO_NATURAL_COOLING: "剧本 3 现象学自然冷却",
    SCENARIO_FIREWALL: "剧本 4 三大防线 + No-Scoring 刚性复核",
}

# 契约整数门槛 (SG-1 §3.3, 仅断言用; 判定一律走真实 evaluate_band)
KNOWN_REPLY_MIN = 1
KNOWN_CO_MIN = 2
FAMILIAR_REPLY_MIN = 3
FAMILIAR_CO_MIN = 5
CLOSE_REPLY_MIN = 10
CLOSE_CO_MIN = 15

BAND_STRANGER = "stranger"
BAND_KNOWN = "known"
BAND_FAMILIAR = "familiar"
BAND_CLOSE = "close"

# 四元 decision
DECISION_ACTIONS = ("transmit", "observe", "reflect", "do_nothing")

# 剧本 1 信号量 (整数, 对照契约门槛)
S1_INIT_REPLY_PAIRS = 3      # 首窗 reply 对 (min 折抵后 = 3)
S1_INIT_CO_SESSIONS = 5      # 首窗 co-presence
S1_DUPE_REPLY_PAIRS = 1      # 节流窗内重复信号 (被 24h 节流吞掉; 计入 D2 慢爬窗)
S1_CLOSE_REPLY_EXTRA = 6     # close 门槛: 3 + 1(重复) + 6 = 10
S1_CLOSE_CO_EXTRA = 10       # close 门槛: 5 + 10 = 15


# ───────────────────────────────────────────────────────────
# 数据结构
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL9BandRecord:
    """单次结算的 band 轨迹 checkpoint (canonical evidence)。"""
    experiment_id: str
    scenario: str
    run_id: str
    step: str
    sim_ts: str
    band_before: str
    band_after: str
    reply_exchanges: int
    co_presence_sessions: int
    settle_skipped: Optional[str]
    settle_updated: int
    settle_demoted: int
    band_updated_at: Optional[str]
    note: str


@dataclass(frozen=True)
class TL9ScenarioDerived:
    """单场景派生指标。"""
    scenario: str
    passed: bool
    checks: Dict[str, bool]
    key_numbers: Dict[str, Any]
    summary: str


@dataclass(frozen=True)
class TL9SeriesMetrics:
    """run 系列 (D2) 派生指标。"""
    scenario: str
    n_runs: int
    determinism_ok: bool
    all_passed: bool
    per_run_passed: List[bool]
    summary: str


# ───────────────────────────────────────────────────────────
# 确定性 stub LLM (0 网络调用)
# ───────────────────────────────────────────────────────────

class _StubSeedLLM:
    """方案 B 语义化 stub: 固定返回 {title, description} (确定性)。"""

    def __init__(self, title: str, description: str) -> None:
        self._title = title
        self._description = description
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        self.calls.append(
            {"agent_id": agent_id, "max_tokens": max_tokens,
             "temperature": temperature}
        )
        return json.dumps(
            {"title": self._title, "description": self._description},
            ensure_ascii=False,
        )


class _StubDecisionLLM:
    """Decision stub: 固定返回四元之一 (确定性, 走真实 parse_decision_output)。"""

    def __init__(self, decision: str = "transmit") -> None:
        if decision not in DECISION_ACTIONS:
            raise ValueError(f"stub decision 必须是四元之一: {DECISION_ACTIONS}")
        self._decision = decision
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        prompt = messages[-1]["content"] if messages else ""
        self.calls.append(
            {"prompt": prompt, "agent_id": agent_id, "max_tokens": max_tokens,
             "temperature": temperature}
        )
        return (
            f'{{"decision": "{self._decision}", '
            f'"reason": "TL-9 stub: 确定性四元选择（留白/行动皆合法主态）。"}}'
        )


# ───────────────────────────────────────────────────────────
# 隔离环境装配 helpers
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _ts_to_dt(ts: str) -> datetime:
    """ISO 8601 (clock.sim_ts 产物) → aware datetime (settle_relations 输入)。"""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _make_relationships_file(
    root: Path, agent_id: str, others: Dict[str, Dict[str, Any]]
) -> None:
    """写 4.2 schema relationships.json (schema_version 4.2, 隔离副本)。"""
    path = root / "soul" / agent_id / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": agent_id,
        "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": others,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rel_entry(band: str, tags: List[str], **kwargs: Any) -> Dict[str, Any]:
    """4.2 entry 构造 (objective 全整数; 0 浮点权重)。"""
    entry = {
        "impression": "一起在客厅聊过天的灵魂",
        "feeling": "neutral",
        "confidence": 0.0,  # 只读遗留字段 (D4), harness 不写新 confidence
        "interaction_count": 0,
        "last_interaction_at": None,
        "last_updated": "2026-09-01T00:00:00+00:00",
        "created_at": "2026-09-01T00:00:00+00:00",
        "objective": {
            "reply_exchanges": 0,
            "co_presence_sessions": 0,
            "dream_exchanges": 0,
            "last_signal_at": None,
        },
        "impression_tags": tags,
        "relational_band": band,
        "band_updated_at": None,
        "last_relation_update_ref": None,
    }
    entry.update(kwargs)
    return entry


def _write_perception_reply(
    root: Path, actor_id: str, ts: str, n: int
) -> None:
    """真实载体 reply 信号: world/perception_trace.jsonl (append)。"""
    path = root / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    if path.is_file():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    for i in range(n):
        lines.append(json.dumps({
            "event_id": f"tl9-reply-{ts}-{actor_id}-{i}",
            "timestamp": ts,
            "event_type": "reply",
            "extra": {"event_kind": "social", "actor_id": actor_id},
        }, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_co_presence(
    root: Path, agents: List[str], ts: str, n: int
) -> None:
    """真实载体 co-presence 信号: soul/interactions.jsonl (append)。"""
    path = root / "soul" / "interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    if path.is_file():
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
    for i in range(n):
        lines.append(json.dumps({
            "ts": ts,
            "type": "cross_chat",
            "agents": agents,
            "content": f"TL-9 共在会话 #{i}",
        }, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_relationships_entry(root: Path, agent_id: str, other_id: str) -> Dict[str, Any]:
    """读隔离 root 下 relationships.json 的 entry (断言用)。"""
    path = root / "soul" / agent_id / "relationships.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["others"].get(other_id) or {}


def _reset_process_state() -> None:
    """run 之间重置进程级单例 (隔离 data_root 切换后缓存必须清空)。"""
    reset_goal_providers()
    reset_seed_providers()
    import src.soul.relationships as rel_mod
    rel_mod._manager_singleton = None  # type: ignore[attr-defined]
    set_agent_ids([])


def _motive_target_from_ref(seed_source_ref: str) -> str:
    """B5 静态 ref 契约 (SG-1 §9.3.3): "relation:<other>" → 他者 target。

    非 relation 前缀 (B1-B4 / S 轴) → 回退 "bryan" (对齐生产
    GoalMotiveProvider.assemble_candidate 现状: 非他者源恒 target=bryan)。
    """
    if seed_source_ref.startswith("relation:"):
        return seed_source_ref.split(":", 1)[1]
    from src.soul.motive import TARGET_BRYAN
    return TARGET_BRYAN


def _prepare_isolated_root(run_dir: Path) -> Path:
    """装配隔离 data_root (SOUL_OS_DATA_DIR + 单例重置)。返回 isolated root。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
    reset_data_root()
    _reset_process_state()
    isolated_root = data_root()
    # per-agent graph.sqlite (Schema v8 迁移预检; B5/Decision 检索共用)
    for agent_id in (TL9_AGENT_A, TL9_AGENT_B):
        db = isolated_root / "memory" / agent_id / "graph.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        GraphStore(db_path=db).close()
    return isolated_root


# ───────────────────────────────────────────────────────────
# TL9Runner — 关系演化四剧本验证编排器
# ───────────────────────────────────────────────────────────

class TL9Runner:
    """TL-9 关系演化端到端验证编排器 (C-3 闭环钢印)。"""

    def __init__(
        self,
        repo_root: Path,
        seed: int = TL9_SEED,
        experiment_id: str = TL9_EXPERIMENT_ID,
        seed_llm: Optional[Callable[..., Any]] = None,
        decision_llm: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id
        self._seed_llm = seed_llm or _StubSeedLLM(
            title="想找 Akane 聊聊最近一起经历的事",
            description="TL-9 stub 内心独白：我们最近越来越熟了，想和她聊聊。",
        )
        self._decision_llm = decision_llm or _StubDecisionLLM(decision="transmit")

    # ── 场景运行 ───────────────────────────────────────────

    def run_scenario(
        self,
        scenario: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行单个剧本 (隔离 data_root)。返回 records + derived。"""
        if scenario not in SCENARIOS:
            raise ValueError(f"未知剧本: {scenario!r}")
        run_id = run_id or _new_run_id()
        harness_root = (
            self._repo_root / "data" / "time_lapse" / self._experiment_id
        )
        run_dir = harness_root / scenario / run_id
        # 固定 run_id 重放时清空重建 (防陈旧 sidecar 残留 — e.g. 上次执行的
        # goal_provider.json 节流戳会拦截本次 settle; 仅清理 harness 写区)
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        isolated_root = _prepare_isolated_root(run_dir)
        clock = SimulationClock(start_day=0)

        if scenario == SCENARIO_RELATION_UP:
            records, derived = self._run_relation_up(run_id, isolated_root, clock)
        elif scenario == SCENARIO_OTHER_TARGET:
            records, derived = self._run_other_target(run_id, isolated_root, clock)
        elif scenario == SCENARIO_NATURAL_COOLING:
            records, derived = self._run_natural_cooling(run_id, isolated_root, clock)
        else:
            records, derived = self._run_firewall(run_id, isolated_root, clock)

        rec_dir = run_dir / "records"
        rec_dir.mkdir(parents=True, exist_ok=True)
        with open(rec_dir / "bands.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        with open(run_dir / "derived.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(derived), ensure_ascii=False, indent=2) + "\n")

        return {
            "scenario": scenario,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "derived": derived,
        }

    # ── 剧本 1: 关系正向跃迁 ───────────────────────────────

    def _run_relation_up(
        self, run_id: str, root: Path, clock: SimulationClock
    ) -> tuple[List[TL9BandRecord], TL9ScenarioDerived]:
        """stranger → known → familiar → close (整数门槛, 每窗至多升 1 级)。"""
        a, b = TL9_AGENT_A, TL9_AGENT_B
        # fixture: agent_a 认识 agent_akane (stranger 底档 entry; 沉淀层只评估
        # 既有对子 — 对子创建属采集层 on_agent_speak, 不在本次结算范围)
        _make_relationships_file(root, a, {
            b: _rel_entry(BAND_STRANGER, []),
        })
        _make_relationships_file(root, b, {})

        records: List[TL9BandRecord] = []
        checks: Dict[str, bool] = {}

        def settle(
            step: str, day: int, hour: int, note: str
        ) -> tuple[Dict[str, Any], str, str]:
            now = _ts_to_dt(clock.sim_ts(day, hour))
            res = settle_relations(a, now=now, base_dir=root)
            entry = _read_relationships_entry(root, a, b)
            band_before = records[-1].band_after if records else BAND_STRANGER
            records.append(TL9BandRecord(
                experiment_id=self._experiment_id,
                scenario=SCENARIO_RELATION_UP,
                run_id=run_id,
                step=step,
                sim_ts=clock.sim_ts(day, hour),
                band_before=band_before,
                band_after=entry.get("relational_band", BAND_STRANGER),
                reply_exchanges=int(entry.get("objective", {}).get("reply_exchanges", 0)),
                co_presence_sessions=int(
                    entry.get("objective", {}).get("co_presence_sessions", 0)
                ),
                settle_skipped=res.get("skipped"),
                settle_updated=int(res.get("updated", 0)),
                settle_demoted=int(res.get("demoted", 0)),
                band_updated_at=entry.get("band_updated_at"),
                note=note,
            ))
            return res, entry.get("relational_band", BAND_STRANGER), entry.get(
                "band_updated_at"
            )

        # D0: 公开互动 + 双向 reply 累计 (首窗直接给满 known 与 familiar 门槛计数;
        # 信号 ts 必须在 D1 12:00 结算窗口 (D0 12:00~D1 12:00) 内)
        _write_perception_reply(root, b, clock.sim_ts(0, 13), S1_INIT_REPLY_PAIRS)
        _write_perception_reply(root, a, clock.sim_ts(0, 13), S1_INIT_REPLY_PAIRS)
        _write_co_presence(root, [a, b], clock.sim_ts(0, 14), S1_INIT_CO_SESSIONS)

        # D1 12:00 结算 #1: reply_delta=3, co_delta=5 → 计数已达 familiar 门槛,
        # 但单次结算至多升 1 级 → 只到 known (硬断言: 不跳级)
        _, band1, _ = settle("S1-1", 1, 12, "首窗结算: 计数满 familiar 门槛仍只升 1 级")
        checks["step1_known_not_jump"] = band1 == BAND_KNOWN
        entry1 = _read_relationships_entry(root, a, b)
        obj1 = entry1["objective"]
        checks["step1_reply_count"] = obj1["reply_exchanges"] == S1_INIT_REPLY_PAIRS
        checks["step1_co_count"] = obj1["co_presence_sessions"] == S1_INIT_CO_SESSIONS
        checks["step1_band_updated_at"] = bool(entry1.get("band_updated_at"))

        # 窗口内重复信号 → 24h 节流: 不重复结算、计数不变 (硬断言)。
        # 重复信号 ts=D1 12:30 落在 settle#2 (D1 13:00) 窗口内 — 若节流失效
        # 该信号将被计入 (reply→4), step2_counts_unchanged 即捕获此回归。
        _dupe_ts = (_ts_to_dt(clock.sim_ts(1, 12)) + timedelta(minutes=30)).isoformat()
        _write_perception_reply(root, b, _dupe_ts, S1_DUPE_REPLY_PAIRS)
        _write_perception_reply(root, a, _dupe_ts, S1_DUPE_REPLY_PAIRS)
        res2, band2, _ = settle("S1-2", 1, 13, "窗口内重复信号: 24h 节流吞掉")
        checks["step2_throttle"] = res2.get("skipped") == "throttle"
        obj2 = _read_relationships_entry(root, a, b)["objective"]
        checks["step2_counts_unchanged"] = (
            obj2["reply_exchanges"] == S1_INIT_REPLY_PAIRS
            and obj2["co_presence_sessions"] == S1_INIT_CO_SESSIONS
        )
        checks["step2_band_stable_known"] = band2 == BAND_KNOWN

        # D2 12:00 结算 #3: 无新信号 → 慢爬评估 (累计计数满足 familiar 门槛)
        _, band3, _ = settle("S1-3", 2, 12, "跨窗慢爬评估: known→familiar")
        checks["step3_familiar"] = band3 == BAND_FAMILIAR

        # D3 12:00 结算 #4: 计数 (3,5) 未达 close 门槛 (10,15) → 保持 familiar
        _, band4, _ = settle("S1-4", 3, 12, "计数不足 close 门槛 → familiar 保持")
        checks["step4_familiar_held"] = band4 == BAND_FAMILIAR

        # D4: 注入 close 门槛信号 (D4 10:00, 在 settle#5 D4 12:00 窗口内)
        #     → familiar→close (每窗仍只升 1 级)
        _write_perception_reply(root, b, clock.sim_ts(4, 10), S1_CLOSE_REPLY_EXTRA)
        _write_perception_reply(root, a, clock.sim_ts(4, 10), S1_CLOSE_REPLY_EXTRA)
        _write_co_presence(root, [a, b], clock.sim_ts(4, 11), S1_CLOSE_CO_EXTRA)
        _, band5, _ = settle("S1-5", 4, 12, "close 双门槛命中 → familiar→close")
        checks["step5_close"] = band5 == BAND_CLOSE

        trajectory = [r.band_after for r in records]
        checks["trajectory"] = trajectory == [
            BAND_KNOWN, BAND_KNOWN, BAND_FAMILIAR, BAND_FAMILIAR, BAND_CLOSE,
        ]
        checks["no_demote"] = all(r.settle_demoted == 0 for r in records)
        # 硬断言 1: 单次结算至多升 1 级 (step1 计数满 familiar 门槛仍只到 known
        # — 由 trajectory 与 step1_known_not_jump 双重锁定)
        # 硬断言 2: 24h 窗口节流生效 (step2_throttle + step2_counts_unchanged)

        passed = all(checks.values())
        key_numbers = {
            "final_reply_exchanges": records[-1].reply_exchanges,
            "final_co_presence_sessions": records[-1].co_presence_sessions,
            "trajectory": trajectory,
            "throttle_skipped": res2.get("skipped"),
        }
        derived = TL9ScenarioDerived(
            scenario=SCENARIO_RELATION_UP,
            passed=passed,
            checks=checks,
            key_numbers=key_numbers,
            summary=(
                f"正向跃迁轨迹: {' → '.join(trajectory)}; "
                f"单次结算至多升 1 级="
                f"{'PASS' if checks['step1_known_not_jump'] else 'FAIL'}; "
                f"24h 节流={'PASS' if checks['step2_throttle'] else 'FAIL'}"
            ),
        )
        return records, derived

    # ── 剧本 2: 他者目标自发生成 ───────────────────────────

    def _run_other_target(
        self, run_id: str, root: Path, clock: SimulationClock
    ) -> tuple[List[TL9BandRecord], TL9ScenarioDerived]:
        """band≥known+tags → B5 种子 → Motive.target=agent_akane; stranger 0 种子。"""
        a, b = TL9_AGENT_A, TL9_AGENT_B
        records: List[TL9BandRecord] = []
        checks: Dict[str, bool] = {}

        # Phase 0: stranger fixture → B5 不发射 (0 种子)
        _make_relationships_file(root, a, {
            b: _rel_entry(BAND_STRANGER, [], objective={
                "reply_exchanges": 0, "co_presence_sessions": 0,
                "dream_exchanges": 0, "last_signal_at": None,
            }),
        })
        _make_relationships_file(root, b, {})
        set_agent_ids([b])
        prov_stranger = self._seed_provider_for(root, a)
        created_stranger = asyncio.run(prov_stranger.scan_seeds(
            now=_ts_to_dt(clock.sim_ts(1, 12))
        ))
        checks["stranger_zero_seed"] = len(created_stranger) == 0

        # Phase 1: 结算实体 → band known + impression_tags (契约 §9.3.3 触发条件)
        now_iso = clock.sim_ts(1, 12)
        _make_relationships_file(root, a, {
            b: _rel_entry(BAND_KNOWN, ["warm"], objective={
                "reply_exchanges": 3, "co_presence_sessions": 5,
                "dream_exchanges": 0, "last_signal_at": now_iso,
            }),
        })
        _make_relationships_file(root, b, {})
        # RelationshipsStore._cache 在 manager 构造时加载一次 (覆盖写文件后
        # 缓存仍旧) — 重建进程级 manager 单例, 让 Phase 2 探针读到新 fixture
        import src.soul.relationships as rel_mod
        rel_mod._manager_singleton = None  # type: ignore[attr-defined]

        # Phase 2: B5 真实轮替 (B1-B4 无源: 无 user_bryan / calendar / trace /
        # interactions 信号文件 → 轮序自然命中 relation 源)
        prov = self._seed_provider_for(root, a)
        created = asyncio.run(prov.scan_seeds(
            now=_ts_to_dt(clock.sim_ts(2, 12))
        ))
        checks["b5_seed_emitted"] = len(created) == 1
        goal = created[0] if created else None
        checks["b5_ref"] = bool(goal and goal.seed_source_ref == f"relation:{b}")

        # Phase 3: B5 goal → Motive (过 make_motive, fail-closed valid target)
        # 静态 ref 契约 (SG-1 §9.3.3): B5 ref = "relation:<other>" → target 解析
        motive: Optional[Motive] = None
        if goal is not None:
            target = _motive_target_from_ref(goal.seed_source_ref)
            motive = make_motive(
                motive_id=new_motive_id(),
                content=goal.title,
                target=target,
                provenance_ref=f"seed:{goal.goal_id}",
                created_at=clock.sim_ts(2, 12),
            )
        checks["motive_target_other"] = bool(
            motive and motive.target == b and motive.content
        )
        # fail-closed: 未注册 target → 拒绝 (make_motive 抛异常, 0 静默放行)
        try:
            make_motive(
                motive_id="bad", content="x", target="agent_unknown",
                provenance_ref="x", created_at="2026-09-06T00:00:00+00:00",
            )
            checks["fail_closed_target"] = False
        except ValueError:
            checks["fail_closed_target"] = True

        # Phase 4: motive 落 MotiveTraceStore 读回验证 (append-only, 隔离副本)
        trace_path = root / "soul" / "motive_trace.jsonl"
        trace_store = MotiveTraceStore(trace_path=trace_path)
        if motive is not None:
            trace_store.append_motive(motive, a)
        resolved = trace_store.resolve_pending(
            a, now=_ts_to_dt(clock.sim_ts(2, 13))
        )
        checks["trace_readback"] = bool(
            resolved and resolved.target == b
            and resolved.motive_id == (motive.motive_id if motive else None)
        )

        # Phase 5: Decision 层 stub (确定性四元, 真实 parse_decision_output:
        # 禁止另写 classifier — target 透传进 prompt 且四元都合法)
        decision_ok = False
        prompt_has_target = False
        if motive is not None:
            result = asyncio.run(decide_motive(
                motive, a,
                llm_call=self._decision_llm,
                current_time=clock.sim_ts(2, 12),
            ))
            decision_ok = result.decision in DECISION_ACTIONS and result.transmit
            calls = getattr(self._decision_llm, "calls", [])
            prompt_has_target = bool(
                calls and b in str(calls[-1].get("prompt", ""))
            )
        checks["decision_quadrant"] = decision_ok
        checks["prompt_target_passthrough"] = prompt_has_target

        # 候选 ≤1 (B 轴本窗至多 1 种子 / 1 pending motive)
        checks["candidate_le_1"] = (
            len(created) <= 1
            and len(trace_store._read_all()) <= 1  # noqa: SLF001 (观察用)
        )

        records.append(TL9BandRecord(
            experiment_id=self._experiment_id,
            scenario=SCENARIO_OTHER_TARGET,
            run_id=run_id,
            step="S2-1",
            sim_ts=clock.sim_ts(2, 12),
            band_before=BAND_STRANGER,
            band_after=BAND_KNOWN,
            reply_exchanges=3,
            co_presence_sessions=5,
            settle_skipped=None,
            settle_updated=0,
            settle_demoted=0,
            band_updated_at=now_iso,
            note=(
                "stranger→0 种子; known+tags→B5 种子→Motive.target="
                f"{motive.target if motive else '(无)'}"
            ),
        ))

        passed = all(checks.values())
        derived = TL9ScenarioDerived(
            scenario=SCENARIO_OTHER_TARGET,
            passed=passed,
            checks=checks,
            key_numbers={
                "stranger_seeds": len(created_stranger),
                "b5_seeds": len(created),
                "motive_target": motive.target if motive else None,
                "decision": "transmit" if decision_ok else None,
                "pending_trace_records": len(trace_store._read_all()),  # noqa: SLF001
            },
            summary=(
                f"B5 种子={len(created)} 笔; Motive.target={motive.target if motive else '无'} "
                f"(合法={checks['motive_target_other']}); "
                f"stranger 0 种子={'PASS' if checks['stranger_zero_seed'] else 'FAIL'}"
            ),
        )
        return records, derived

    def _seed_provider_for(self, root: Path, agent_id: str) -> GoalSeedProvider:
        db = root / "memory" / agent_id / "graph.sqlite"
        store = GraphStore(db_path=db)
        return GoalSeedProvider(agent_id=agent_id, store=store, llm_call=self._seed_llm)

    # ── 剧本 3: 现象学自然冷却 ─────────────────────────────

    def _run_natural_cooling(
        self, run_id: str, root: Path, clock: SimulationClock
    ) -> tuple[List[TL9BandRecord], TL9ScenarioDerived]:
        """快进 30 天无新信号 → 降 1 带; 不跌穿 stranger; SG-2.1 无信号不回升,
        新窗口信号正常恢复 known; band_updated_at 更新。"""
        a, b = TL9_AGENT_A, TL9_AGENT_B
        signal_ts = clock.sim_ts(0, 12)
        _make_relationships_file(root, a, {
            b: _rel_entry(BAND_FAMILIAR, ["warm"], objective={
                "reply_exchanges": 5, "co_presence_sessions": 6,
                "dream_exchanges": 0, "last_signal_at": signal_ts,
            }),
        })
        _make_relationships_file(root, b, {})

        records: List[TL9BandRecord] = []
        checks: Dict[str, bool] = {}

        def settle(step: str, day: int, note: str) -> tuple[Dict[str, Any], str, Optional[str]]:
            now = _ts_to_dt(clock.sim_ts(day, 12))
            res = settle_relations(a, now=now, base_dir=root)
            entry = _read_relationships_entry(root, a, b)
            band_before = records[-1].band_after if records else BAND_FAMILIAR
            records.append(TL9BandRecord(
                experiment_id=self._experiment_id,
                scenario=SCENARIO_NATURAL_COOLING,
                run_id=run_id,
                step=step,
                sim_ts=clock.sim_ts(day, 12),
                band_before=band_before,
                band_after=entry.get("relational_band", BAND_FAMILIAR),
                reply_exchanges=int(entry.get("objective", {}).get("reply_exchanges", 0)),
                co_presence_sessions=int(
                    entry.get("objective", {}).get("co_presence_sessions", 0)
                ),
                settle_skipped=res.get("skipped"),
                settle_updated=int(res.get("updated", 0)),
                settle_demoted=int(res.get("demoted", 0)),
                band_updated_at=entry.get("band_updated_at"),
                note=note,
            ))
            return res, entry.get("relational_band", BAND_FAMILIAR), entry.get(
                "band_updated_at"
            )

        # D30: 恰好 30 天整 → 不降 (契约: 连续 >30 天才降, 已落地语义)
        res30, band30, _ = settle("S3-30", 30, "恰好 30 天整 → 不降")
        checks["day30_no_demote"] = (
            band30 == BAND_FAMILIAR and res30.get("demoted", 0) == 0
        )

        # D31: 超过 30 天 → 降 1 级 familiar→known, band_updated_at 更新
        res31, band31, updated31 = settle("S3-31", 31, "31 天无信号 → 降 1 级")
        checks["day31_demote_one_step"] = (
            band31 == BAND_KNOWN and res31.get("demoted", 0) == 1
        )
        checks["band_updated_at_refreshed"] = (
            updated31 == clock.sim_ts(31, 12)
        )

        # D62: 再降 1 级 known→stranger
        res62, band62, _ = settle("S3-62", 62, "62 天无信号 → known→stranger")
        checks["day62_demote_floor"] = (
            band62 == BAND_STRANGER and res62.get("demoted", 0) == 1
        )

        # D93: 底带不跌穿 (stranger 不再执行降带, demoted==0)。
        # SG-2.1 (TL-9 呈报主大脑拍板): 无信号底带不慢爬回升 — stranger 保持。
        # 修复前: 无信号不降带时慢爬评估会把底带 stranger (累计计数非零
        # reply=5/co=6) 补升回 known — 降带后计数不清零的确定性振荡; 已修。
        res93, band93, _ = settle("S3-93", 93, "底带不再降; 无信号不慢爬回升")
        checks["floor_not_breached"] = res93.get("demoted", 0) == 0
        checks["no_slow_climb_rebound"] = band93 == BAND_STRANGER

        # D123: 降带到 stranger 后继续无信号 30 天 → 仍保持 stranger (不再回升)
        res123, band123, _ = settle("S3-123", 123, "继续无信号 30 天 → 保持 stranger")
        checks["stranger_held_no_rebound"] = (
            band123 == BAND_STRANGER and res123.get("demoted", 0) == 0
        )

        # 无新信号: 计数 0 增量 (全窗口无信号, 不写 last_signal_at)
        checks["counts_frozen"] = (
            records[-1].reply_exchanges == 5
            and records[-1].co_presence_sessions == 6
        )

        # D124: 窗口出现新 reply 信号 (双向成对, 真实载体; ts 落在 D124 12:00
        # 结算的 24h 窗口内) → 正常升级路径升回 known (stranger→known 门槛
        # reply≥1 照旧; 计数累计不清零: 5+1=6)
        _write_perception_reply(root, b, clock.sim_ts(124, 10), 1)
        _write_perception_reply(root, a, clock.sim_ts(124, 10), 1)
        res124, band124, _ = settle("S3-124", 124, "新窗口 reply 信号 → 正常恢复 known")
        checks["new_signal_recovers_known"] = (
            band124 == BAND_KNOWN and res124.get("updated", 0) >= 1
        )

        trajectory = [r.band_after for r in records]
        passed = all(checks.values())
        derived = TL9ScenarioDerived(
            scenario=SCENARIO_NATURAL_COOLING,
            passed=passed,
            checks=checks,
            key_numbers={
                "trajectory": trajectory,
                "demote_days": 31,
                "final_band": trajectory[-1],
            },
            summary=(
                f"冷却轨迹: familiar → {' → '.join(trajectory)}; "
                f"降 1 级/窗={'PASS' if checks['day31_demote_one_step'] else 'FAIL'}; "
                f"底带不再降={'PASS' if checks['floor_not_breached'] else 'FAIL'} "
                f"(无信号不回升={checks['no_slow_climb_rebound']}, "
                f"新信号恢复={checks['new_signal_recovers_known']})"
            ),
        )
        return records, derived

    # ── 剧本 4: 三大防线 + No-Scoring 刚性复核 ─────────────

    def _run_firewall(
        self, run_id: str, root: Path, clock: SimulationClock
    ) -> tuple[List[TL9BandRecord], TL9ScenarioDerived]:
        """AST 审计 (0 直通 publish / 0 float 权重) + Direct Query (facts 0 写 /
        自体记忆 0 他者事件) + 0 新定时器 + 候选 ≤1。"""
        a, b = TL9_AGENT_A, TL9_AGENT_B
        records: List[TL9BandRecord] = []
        checks: Dict[str, bool] = {}

        # 走一遍真实链路 (settle + B5 seed + motive), 再断言沉淀面 0 污染
        _make_relationships_file(root, a, {
            b: _rel_entry(BAND_KNOWN, ["warm"], objective={
                "reply_exchanges": 0, "co_presence_sessions": 0,
                "dream_exchanges": 0, "last_signal_at": None,
            }),
        })
        _make_relationships_file(root, b, {})
        set_agent_ids([b])
        # settle 阶段只用 reply 载体 (co-presence 信号已由剧本 1 覆盖):
        # B4 interaction 探针无窗口过滤 (seed_provider._probe_interaction),
        # 若写 interactions.jsonl 将抢先于 B5 命中 — 故剧本 4 不写, 保证
        # B5 (relation 源) 在真实轮序中命中。
        _write_perception_reply(root, b, clock.sim_ts(0, 13), 1)
        _write_perception_reply(root, a, clock.sim_ts(0, 13), 1)
        settle_res = settle_relations(a, now=_ts_to_dt(clock.sim_ts(1, 12)),
                                      base_dir=root)
        checks["chain_settle_ok"] = settle_res.get("skipped") is None

        prov = self._seed_provider_for(root, a)
        created = asyncio.run(prov.scan_seeds(now=_ts_to_dt(clock.sim_ts(2, 12))))
        checks["chain_seed_ok"] = len(created) == 1
        if created:
            target = _motive_target_from_ref(created[0].seed_source_ref)
            if target == "bryan":
                # 非他者源 (防御): 不装配他者动机, checks 由 chain_seed_ok 约束
                motive = None
            else:
                motive = make_motive(
                    motive_id=new_motive_id(), content=created[0].title,
                    target=target, provenance_ref=f"seed:{created[0].goal_id}",
                    created_at=clock.sim_ts(2, 12),
                )
                MotiveTraceStore(trace_path=root / "soul" / "motive_trace.jsonl").append_motive(
                    motive, a
                )

        # ── 防线 A: AST 审计 SG-2 模块 (0 直通 publish / 0 定时器 / 0 float) ──
        # 审计对象 = 真实项目根 src/social (repo_root 在 pytest 下是 tmp_path)
        _repo_src = Path(__file__).resolve().parents[1] / "src"
        audit_targets = (
            _repo_src / "social" / "relation_settlement.py",
            _repo_src / "social" / "relational_bands.py",
        )
        checks["audit_no_publish"] = all(
            _audit_no_publish(p) == [] for p in audit_targets
        )
        checks["audit_no_timers"] = all(
            _audit_no_timers(p) == [] for p in audit_targets
        )
        checks["audit_no_scoring"] = all(
            _audit_no_scoring(p) == [] for p in audit_targets
        )

        # ── 防线 B: Direct Query (sqlite3 只读) — SAGE facts 0 关系域写入 ──
        db_a = root / "memory" / a / "graph.sqlite"
        facts = _sqlite_scalar(db_a, "SELECT COUNT(*) FROM facts")
        goals = _sqlite_scalar(db_a, "SELECT COUNT(*) FROM goals")
        checks["facts_zero"] = facts == 0
        checks["goals_le_1"] = goals <= 1
        records.append(TL9BandRecord(
            experiment_id=self._experiment_id,
            scenario=SCENARIO_FIREWALL,
            run_id=run_id,
            step="S4-1",
            sim_ts=clock.sim_ts(2, 12),
            band_before=BAND_STRANGER,
            band_after=BAND_KNOWN,
            reply_exchanges=1,
            co_presence_sessions=0,
            settle_skipped=None,
            settle_updated=1,
            settle_demoted=0,
            band_updated_at=clock.sim_ts(1, 12),
            note="soul 沉淀后 SAGE facts 0 行 (防线 3)",
        ))

        # ── 防线 C: 自体情景记忆 0 他者事件 (Identity Firewall) ──
        inner_life_trace = root / "inner_life" / "trace.jsonl"
        checks["self_memory_no_other_events"] = not inner_life_trace.exists()

        # 候选 ≤1: motive_trace pending 记录 ≤1
        trace_records = MotiveTraceStore(
            trace_path=root / "soul" / "motive_trace.jsonl"
        )._read_all()  # noqa: SLF001
        checks["candidate_le_1"] = len(trace_records) <= 1

        passed = all(checks.values())
        derived = TL9ScenarioDerived(
            scenario=SCENARIO_FIREWALL,
            passed=passed,
            checks=checks,
            key_numbers={
                "facts_count": facts,
                "goals_count": goals,
                "pending_motive_records": len(trace_records),
                "settle_updated": int(settle_res.get("updated", 0)),
            },
            summary=(
                f"AST 0 直通 publish / 0 定时器 / 0 float; "
                f"facts={facts} 行 (0 关系域写入); "
                f"自体情景记忆 0 他者事件={'PASS' if checks['self_memory_no_other_events'] else 'FAIL'}"
            ),
        )
        return records, derived

    # ── run 系列 (D2 determinism + 0 mutation) ─────────────

    def run_series(
        self,
        scenarios: Optional[tuple[str, ...]] = None,
        n_runs: int = 3,
    ) -> Dict[str, Any]:
        """四大剧本各连跑 n_runs 次 (D2 宏确定性) + production 0 mutation 验证。"""
        scenarios = scenarios or SCENARIOS
        production_root = self._repo_root / "data"
        before = snapshot_data_root_hashes(production_root)

        series: List[Dict[str, Any]] = []
        all_passed = True
        for scenario in scenarios:
            runs = []
            passed_flags = []
            for i in range(n_runs):
                out = self.run_scenario(scenario, run_id=f"run_{i + 1}")
                runs.append(out)
                passed_flags.append(out["derived"].passed)
            determinism_ok = _scenario_determinism(runs)
            s_passed = all(passed_flags) and determinism_ok
            all_passed = all_passed and s_passed
            series.append(TL9SeriesMetrics(
                scenario=scenario,
                n_runs=n_runs,
                determinism_ok=determinism_ok,
                all_passed=s_passed,
                per_run_passed=passed_flags,
                summary=(
                    f"{SCENARIO_LABELS[scenario]}: "
                    f"{'ALL PASS' if s_passed else 'FAIL'} "
                    f"(3 runs 判定轨迹一致={determinism_ok})"
                ),
            ))

        mut_res = verify_zero_mutation(production_root, before)
        zero_mut_ok = mut_res["pass"]
        all_passed = all_passed and zero_mut_ok

        return {
            "experiment_id": self._experiment_id,
            "scenarios": [asdict(s) for s in series],
            "all_passed": all_passed,
            "zero_mutation_ok": zero_mut_ok,
            "mutation_diff": mut_res["diff"],
            "mutation_added": mut_res["added"],
        }


# ───────────────────────────────────────────────────────────
# D2 宏确定性: 跨 run 判定字段比对 (uuid 不参与)
# ───────────────────────────────────────────────────────────

_DETERMINISM_FIELDS = (
    "step", "band_before", "band_after", "reply_exchanges",
    "co_presence_sessions", "settle_skipped", "settle_updated",
    "settle_demoted", "band_updated_at", "note",
)


def _scenario_determinism(runs: List[Dict[str, Any]]) -> bool:
    """同一剧本 3 个 run 的 band 轨迹判定字段完全一致 (MoE 特性下宏确定性)。"""
    if not runs:
        return False
    ref = [asdict(r) for r in runs[0]["records"]]
    for run in runs[1:]:
        cur = [asdict(r) for r in run["records"]]
        if len(cur) != len(ref):
            return False
        for t1, t2 in zip(ref, cur):
            for key in _DETERMINISM_FIELDS:
                if t1[key] != t2[key]:
                    return False
        if run["derived"].passed != runs[0]["derived"].passed:
            return False
    return True


# ───────────────────────────────────────────────────────────
# 静态审计 (对齐 tl8/sg2 护栏断言风格; 返回违规清单, 空 = 通过)
# ───────────────────────────────────────────────────────────

_FORBIDDEN_CALL_TOKENS = (
    "publish", "AGENCY_TRIGGER", "handler", "tool_registry", "actuator",
    "call_tool", "mark_transmitted", "send_message", "_fire",
)
_ASYNCIO_TOKENS: tuple = ("create_task", "ensure_future", "call_later",
                          "call_soon", "Timer")


def _audit_no_publish(path: Path) -> List[str]:
    import ast
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
    import ast
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
    import ast
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


# ───────────────────────────────────────────────────────────
# Direct Query helper (sqlite3 只读)
# ───────────────────────────────────────────────────────────

def _sqlite_scalar(db_path: Path, sql: str) -> int:
    """sqlite3 只读打开 (隔离 DB) 执行标量查询 (只读连接, 0 写)。"""
    import sqlite3
    if not db_path.is_file():
        return 0
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


__all__ = [
    "TL9_EXPERIMENT_ID",
    "TL9_AGENT_A",
    "TL9_AGENT_B",
    "SCENARIOS",
    "SCENARIO_LABELS",
    "TL9BandRecord",
    "TL9ScenarioDerived",
    "TL9Runner",
]