"""
harness/tl11.py — TL-11 Commitment Closure + Periodic Narrative End-to-End Harness (C-2.1 验收钢印)

工单 TL-11（决策已定, 照做）:
  - 目标: 为 C-2.1（B6 承诺闭环种子源 + 周期叙事升华）打上 Time-lapse/端到端
    Harness 验收钢印, 让 C-2.1 正式 CLOSED。
  - 模式: 全程走真实实现（seed_provider / narrative_sublimator / motive_provider /
    decision / scheduler._publish_agency_trigger / InnerLifeWriter 既有沉淀链）,
    LLM 用确定性 stub（0 网络）, 0 src/ 生产改动, 隔离 data_root。

真实实现入口（0 另写模拟）:
  - src/goals/seed_provider.py  GoalSeedProvider.scan_seeds(now=) —— B1/B6 真实探针 +
    24h 节流 + 作息相位 + 双轴约束 + 方案 B 语义化 + _create_goal（llm_call 注入点）
  - src/goals/motive_provider.py GoalMotiveProvider.assemble_candidate(now=) /
    on_decision(motive, result, now=) / apply_interrupt_signals(now=) / sediment_completion
    —— 真实候选装配 / 状态同步 / 逾期判定 / COMPLETED 沉淀链
  - src/soul/motive.py  MotiveEngine.decide(motive, agent_id) —— 真实四元 Decision
    （set_llm_proxy 注入确定性 stub）; make_motive 目标归一化
  - src/soul/scheduler.py  SoulScheduler._publish_agency_trigger —— 既有 AGENCY_TRIGGER
    发布链（A2「唯一出口」实证）; _slot_for_time —— night slot 判据（A3 非每日）
  - src/goals/narrative_sublimator.py  PeriodicNarrativeSublimator.sublimate_weekly /
    sublimate_memorial —— 周记/纪念日真实聚合 + 幂等判重 + 身份防火墙 + 一次
    InnerLifeWriter.create_event（llm_call 注入点）
  - src/inner_life/trace_reader.py / GraphStore —— 只读断言面（trace / goals / facts）

四大剧本（scenario, Owner 拍板）:
  1. b6_closure（剧本 1 B6 终态闭环实证）: 双 epoch 各自隔离 data_root ——
     epoch_completed: B1 种子 → ACTIVE → 推进 ×2 → COMPLETED + sediment（真实链）→
     B6 判窗（state_updated_at ∈ 窗）→ 关怀 goal（ref=commitment_closure:{goal_id}）→
     候选池 → 四元 Decision transmit → 仅显式 _publish_agency_trigger 才出 1 条
     AGENCY_TRIGGER（0 直发）; epoch_abandoned: B1 第二承诺 → 推进 → timeout 判据
     （apply_interrupt_signals）→ ABANDONED → B6 判窗 → 「已逾期釋懷」反馈。
     → A1（承诺状态转移闭环）+ A2（反馈走 volition path 不直发）
  2. weekly（剧本 2 周记频率实证）: ISO 周判据下同夜重入 / 同周再触发 → 0 二次沉淀;
     跨周 → 新沉淀（每 ISO 周恰 1）; morning slot 0 触发（非每日产物）→ A3
  3. memorial（剧本 3 纪念日反芻实证）: calendar_event 白名单「今日事件」触发 →
     往年今日自己 diary 聚合 → 一次沉淀（actor_id==self / source_system==system）;
     空聚合 fail-closed 0 半成品; 沉淀后 SAGE facts 表计数 0 → A5（+ A6 侧面）
  4. identity_firewall（剧本 4 身份防火墙实证）: 周记聚合窗注入他者 diary（路径隔离）
     与他者 trace（actor_id 过滤）+ 自己 goal:/periodic: 引用（0 递归）→ 聚合
     prompt 0 内化; 沉淀事件 actor_id==self → A6（+ A5 强化）

全局断言（run_series 层）:
  - A4 0 新定时器: scheduler / narrative_sublimator / seed_provider 静态 AST 断言
    （无 threading.Timer / cron / 新增 sleep 循环; asyncio.sleep 仅主循环既有 2 处）
  - A7 D2 确定性: 每剧本连跑 3 次, 判定字段（checks/passed）全一致; B6 触发/选取、
    叙事触发/幂等判定逐项一致; production data_root 前后快照 mutation_diff == {}

Frozen Contract 边界（0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / diary 排程一律不动;
本 harness 只读生产源码、写隔离目录（data/time_lapse/TL-11/...）。
"""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("soul_os.harness.tl11")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL11_EXPERIMENT_ID = "TL-11"
TL11_SEED = 42

# 主体 Agent（对齐 TL-10: 映射 configs/default.yaml 既有 persona 配置）
TL11_AGENT = "agent_ruka"
# 他者 Agent（身份防火墙注入用, 真实存在的第二 persona）
TL11_OTHER = "agent_akane"

SCENARIO_B6_CLOSURE = "b6_closure"                # 剧本 1
SCENARIO_WEEKLY = "weekly"                        # 剧本 2
SCENARIO_MEMORIAL = "memorial"                    # 剧本 3
SCENARIO_IDENTITY_FIREWALL = "identity_firewall"  # 剧本 4

SCENARIOS = (
    SCENARIO_B6_CLOSURE,
    SCENARIO_WEEKLY,
    SCENARIO_MEMORIAL,
    SCENARIO_IDENTITY_FIREWALL,
)

SCENARIO_LABELS = {
    SCENARIO_B6_CLOSURE: "剧本 1 B6 终态闭环实证 (A1 状态转移闭环 + A2 volition path 不直发)",
    SCENARIO_WEEKLY: "剧本 2 周记频率实证 (A3 ISO 周判据 + 幂等 + 非每日)",
    SCENARIO_MEMORIAL: "剧本 3 纪念日反芻实证 (A5 沉淀路径 0 直写 facts + A6 只聚自己的)",
    SCENARIO_IDENTITY_FIREWALL: "剧本 4 身份防火墙实证 (A6 他者 0 内化 + A5 强化)",
}

# 契约 §3.3 B6 反馈 criteria 模板（断言用镜像, 真实模板在 seed_provider._CRITERIA_TEMPLATES）
B6_CRITERIA_EXPECTED = {"kind": "interaction", "count": 1, "timeout_days": 7}

# B1 承诺 criteria（剧本 1 epoch_completed 断言）
B1_CRITERIA_EXPECTED = {"kind": "interaction", "count": 2, "timeout_days": 7}

# 剧本 2/4 周记 fixture 用日期（真实 ISO 周由实现同一算法推导, 期望键同构校验）
WEEKLY_MONDAY = date(2026, 9, 7)        # 2026-W37 周一
WEEKLY_WEDNESDAY = date(2026, 9, 9)     # 2026-W37 周三（首触）
WEEKLY_THURSDAY = date(2026, 9, 10)     # 2026-W37 周四（同周重触）
WEEKLY_NEXT_MONDAY = date(2026, 9, 14)  # 2026-W38 周一（跨周）
FW_MONDAY = date(2026, 9, 21)           # 2026-W39 周一（剧本 4）
FW_WEDNESDAY = date(2026, 9, 23)        # 2026-W39 周三（剧本 4 触发）

MEMORIAL_DAY = date(2026, 9, 6)         # 剧本 3 纪念日（契约署名日, 周日）
MEMORIAL_PRIOR_2025 = date(2025, 9, 6)
MEMORIAL_PRIOR_2024 = date(2024, 9, 6)

# 身分防火墙标记词（断言用）
MARK_OWN_DIARY = "我自己的一周碎记"
MARK_OTHER_DIARY = "他者日记污染标记"
MARK_OWN_TRACE = "只有我自己知道的深夜回想"
MARK_OTHER_TRACE = "他者机密经历不该进我的周记"
MARK_RECURSIVE = "goal 引用不该被递归聚合"

# ───────────────────────────────────────────────────────────
# 数据结构
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL11ScenarioDerived:
    """单场景派生指标 (canonical evidence 之一)。"""
    scenario: str
    passed: bool
    checks: Dict[str, bool]
    key_numbers: Dict[str, Any]
    summary: str


@dataclass(frozen=True)
class TL11SeriesMetrics:
    """run 系列派生指标。"""
    scenario: str
    n_runs: int
    determinism_ok: bool
    all_passed: bool
    per_run_passed: List[bool]
    summary: str


# ───────────────────────────────────────────────────────────
# 确定性 stub LLM（0 网络调用; 双注入点: proxy 容器 + 可调用）
# ───────────────────────────────────────────────────────────

class _StubLLM:
    """确定性 LLM stub（process-global proxy 注入点 + seed/narrative 直接注入点）。

    真实调用形状:
      - decision:   _default_llm_call → proxy.generate_text(messages=, agent_id=,
                    max_tokens=, temperature=)（motive/decision 链）
      - seed 语义化: GoalSeedProvider._semantize → llm_call(messages, agent_id=,
                    max_tokens=, temperature=)（seed_provider 注入点）
      - 叙事语义化: PeriodicNarrativeSublimator._sublimate → 同上（sublimator 注入点）

    路由（确定性, 按 prompt 关键字）:
      - "decision"   → 四元 Decision transmit JSON（真实 parse_decision_output 解析）
      - "内心独白"   → 种子语义化 {title, description}（方案 B 同构输出形状）
      - "narrative"  → 叙事语义化 {title, narrative}（周记/纪念日形状）
      - 其余（interpretation 等）→ None（fail-closed 0 产出, 隔离目录本就无素材）
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def generate_text(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        content = messages[-1]["content"] if messages else ""
        self.calls.append(
            {
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "prompt": content,
            }
        )
        if "decision" in content:
            return (
                '{"decision": "transmit", '
                '"reason": "TL-11 stub: 确定性四元选择 (transmit)。"}'
            )
        if "内心独白" in content:  # 方案 B 种子语义化
            return (
                '{"title": "此刻心头的一个念头", '
                '"description": "TL-11 stub 种子语义化产出（确定性, 0 编造素材外事实）。"}'
            )
        if "narrative" in content:  # 周记 / 纪念日叙事语义化
            return (
                '{"title": "TL-11 stub 叙事标题", '
                '"narrative": "TL-11 stub 叙事正文（确定性, 只重构素材）。"}'
            )
        return None  # 其他调用 fail-closed 0 产出

    # seed_provider / sublimator 直接注入的可调用形状
    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        return await self.generate_text(messages, agent_id, max_tokens, temperature)


# ───────────────────────────────────────────────────────────
# 真实 Event Bus 收集（真实 SoulEventBus + 记录 handler, 0 模拟 bus）
# ───────────────────────────────────────────────────────────

class _BusRecorder:
    """真实 SoulEventBus 的订阅记录器 (async handler 收件箱)。"""

    def __init__(self) -> None:
        self.events: List[Any] = []

    async def handle(self, event: Any) -> None:
        self.events.append(event)


# ───────────────────────────────────────────────────────────
# 隔离环境装配 helpers
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _reset_process_state() -> None:
    """run / epoch 之间重置进程级单例（隔离 data_root 切换后缓存必须清空）。"""
    try:
        from src.goals.motive_provider import reset_goal_providers
        reset_goal_providers()
    except Exception:  # noqa: BLE001 — 模块未加载时无状态可重置
        pass
    try:
        from src.goals.seed_provider import reset_seed_providers
        reset_seed_providers()
    except Exception:  # noqa: BLE001
        pass
    try:
        import src.soul.relationships as rel_mod
        rel_mod._manager_singleton = None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.soul.motive import set_agent_ids
        set_agent_ids([])
    except Exception:  # noqa: BLE001
        pass


def _prepare_isolated_root(run_dir: Path) -> Path:
    """装配隔离 data_root（SOUL_OS_DATA_DIR + 单例重置）。返回 isolated root。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
    from src.paths import data_root, reset_data_root
    reset_data_root()
    _reset_process_state()
    isolated_root = data_root()
    # per-agent graph.sqlite（Schema 迁移预检; Goal 引擎 lazy 打开复用）
    for agent_id in (TL11_AGENT, TL11_OTHER):
        db = isolated_root / "memory" / agent_id / "graph.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        from src.memory.sage.graph_store import GraphStore
        GraphStore(db_path=db).close()
    return isolated_root


def _local_tz():
    """本地 aware 时区（对齐 src.timezone_utils.LOCAL_TZ 语义, 0 import 时机依赖）。"""
    return datetime.now().astimezone().tzinfo


def _local_dt(year: int, month: int, day: int, hour: int, minute: int = 0,
              second: int = 0) -> datetime:
    """本地 aware datetime 构造（harness 注入时钟, time-lapse 用）。"""
    return datetime(year, month, day, hour, minute, second, tzinfo=_local_tz())


def _past_anchor(now: datetime) -> datetime:
    """剧本 1 时间轴「过去锚」: 墙钟 -24h 归一为白天 10:00（恒 < 执行时刻）。"""
    t = now - timedelta(hours=24)
    return t.replace(hour=10, minute=0, second=0, microsecond=0)


def _future_anchor(now: datetime) -> datetime:
    """剧本 1 时间轴「未来锚」: 明日 13:00（恒 > 执行时刻, 且非 quiet 时段）。"""
    t = now + timedelta(days=1)
    return t.replace(hour=13, minute=0, second=0, microsecond=0)


def _make_relationships_file(
    root: Path, agent_id: str, others: Dict[str, Dict[str, Any]]
) -> None:
    """写 4.2 schema relationships.json（隔离副本, 与 TL-9/TL-10 同款 fixture 方式）。"""
    path = root / "soul" / agent_id / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": agent_id,
        "schema_version": "4.2",
        "created_at": "2026-09-06T00:00:00+00:00",
        "last_decay_at": "2026-09-06T00:00:00+00:00",
        "others": others,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _rel_entry(
    impression: str = "一起在客厅聊过天的灵魂",
) -> Dict[str, Any]:
    """B1 commitment 需要的 user_bryan 4.2 entry（objective 全整数, 0 浮点权重）。"""
    return {
        "impression": impression,
        "feeling": "neutral",
        "confidence": 0.0,  # 只读遗留字段 (D4), harness 不写新 confidence
        "interaction_count": 0,
        "last_interaction_at": None,
        "last_updated": "2026-09-06T00:00:00+00:00",
        "created_at": "2026-09-06T00:00:00+00:00",
        "objective": {
            "reply_exchanges": 0,
            "co_presence_sessions": 0,
            "dream_exchanges": 0,
            "last_signal_at": None,
        },
        "impression_tags": [],
        "relational_band": "known",
        "band_updated_at": None,
        "last_relation_update_ref": None,
    }


def _ensure_relationships_manager(root: Path) -> None:
    """真实 manager 读侧就绪: 写文件后重建进程级单例（读新 fixture）。"""
    from src.soul.relationships import get_relationships_manager
    import src.soul.relationships as rel_mod
    rel_mod._manager_singleton = None  # type: ignore[attr-defined]
    manager = get_relationships_manager()
    store = manager.get_store(TL11_AGENT)
    assert store is not None, "real manager must resolve agent store"


# ───────────────────────────────────────────────────────────
# fixture 写入（真实数据目录/文件格式, 0 模拟逻辑）
# ───────────────────────────────────────────────────────────

def _write_diary(root: Path, agent_id: str, day: date, slot: str, content: str) -> None:
    """写 diary 条目（真实格式: data/soul/{agent}/diary/YYYY-MM-DD.jsonl）。"""
    path = root / "soul" / agent_id / "diary" / f"{day.isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.combine(day, datetime.min.time(), tzinfo=_local_tz()).isoformat()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(
            {"ts": ts, "slot": slot, "content": content}, ensure_ascii=False
        ) + "\n")


def _append_perception_calendar_event(
    root: Path, ts_iso: str, novelty_id: str, summary: Optional[str] = None,
) -> None:
    """写 perception_trace（真实格式: data/world/perception_trace.jsonl）。"""
    path = root / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec: Dict[str, Any] = {
        "event_type": "calendar_event",
        "accepted": True,
        "novelty_id": novelty_id,
        "timestamp": ts_iso,
    }
    if summary:
        rec["summary"] = summary
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_trace_record(
    root: Path,
    event_id: str,
    actor_id: str,
    ts_iso: str,
    trigger_type: str,
    trace_ref: Optional[str],
    extras: Dict[str, Any],
    source_system: str = "system",
) -> None:
    """写 inner_life trace（真实格式: event_to_dict 的 9 字段结构, 0 模拟）。"""
    path = root / "inner_life" / "trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "event_id": event_id,
        "session_id": None,
        "correlation_id": None,
        "parent_event_id": None,
        "ts": ts_iso,
        "provenance": {
            "trigger_type": trigger_type,
            "actor_id": actor_id,
            "source_system": source_system,
            "trace_ref": trace_ref,
            "extras": extras,
        },
        "lineage_depth": 0,
        "lineage_path": "",
        "source_world_event_novelty_id": None,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _read_trace(root: Path) -> List[Dict[str, Any]]:
    """只读 inner_life trace（真实 NarrativeTraceReader 同源文件）。"""
    from src.inner_life.trace_reader import NarrativeTraceReader
    return NarrativeTraceReader()._read_all()  # noqa: SLF001 — 只读断言面


def _facts_count(root: Path) -> int:
    """SAGE facts 表计数（0 直写断言: 沉淀路径后必须仍为 0）。"""
    from src.memory.sage.graph_store import GraphStore
    from src.goals.motive_provider import _goal_db_path
    store = GraphStore(db_path=_goal_db_path(TL11_AGENT))
    try:
        return len(store.get_all_facts())
    finally:
        store.close()


# ───────────────────────────────────────────────────────────
# 静态断言（A4 0 新定时器 / A2 0 直发 / A5 0 直写 facts）
# ───────────────────────────────────────────────────────────

_FORBIDDEN_TIMER_TOKENS = (
    "threading.Timer", "Timer(", "schedule.every", "apscheduler",
    "import cron", "from cron", "time.sleep(",
)
_FORBIDDEN_DIRECT_PUBLISH_TOKENS = (
    "_publish_agency_trigger", "AGENCY_TRIGGER", "publish(", "telegram",
)


def _ast_method_no_timer(source: str, method_name: str) -> bool:
    """AST 断言: 指定方法体（含 async def）0 新定时器构造 / 0 阻塞 sleep / 0 新循环。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    targets = [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == method_name]
    if not targets:
        return False  # 方法不存在 → 断言失败（不能靠缺失混过）
    for stmt in targets[0].body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                attr = sub.func.attr
                if attr in ("sleep", "create_task", "Timer"):
                    return False
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id in ("Timer",):
                    return False
            if isinstance(sub, ast.While):
                return False
    return True


def _ast_no_call_to(source: str, func_name: str) -> bool:
    """AST 断言: 源码中 0 对 func_name 的调用（0 直写路径; 注释提及不算）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == func_name:
                return False
            if isinstance(node.func, ast.Attribute) and node.func.attr == func_name:
                return False
    return True


def _static_assertions(repo_root: Path) -> Dict[str, bool]:
    """A4/A2/A5 源码级静态断言（全部只读生产源码）。"""
    checks: Dict[str, bool] = {}
    sched = (repo_root / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    seed = (repo_root / "src" / "goals" / "seed_provider.py").read_text(encoding="utf-8")
    narr = (repo_root / "src" / "goals" / "narrative_sublimator.py").read_text(
        encoding="utf-8"
    )

    # ── A4: 0 新定时器（B6 挂 _goal_scan_all 既有 30s wake; 叙事挂 night slot 检查链）
    checks["a4_scheduler_no_timer_tokens"] = all(
        tok not in sched for tok in _FORBIDDEN_TIMER_TOKENS
    )
    # asyncio.sleep 仅主循环既有 2 处（30s wake）; create_task 仅主循环启动 1 处
    checks["a4_sleep_only_main_loop"] = sched.count("asyncio.sleep") == 2
    checks["a4_create_task_only_loop_start"] = sched.count("asyncio.create_task") == 1
    checks["a4_fire_periodic_no_timer"] = _ast_method_no_timer(
        sched, "_fire_periodic_narrative"
    )
    checks["a4_goal_scan_all_no_timer"] = _ast_method_no_timer(sched, "_goal_scan_all")
    checks["a4_sublimator_no_timer"] = all(
        tok not in narr for tok in _FORBIDDEN_TIMER_TOKENS
    )
    checks["a4_seed_provider_no_timer"] = all(
        tok not in seed for tok in _FORBIDDEN_TIMER_TOKENS
    )

    # ── A2: 反馈/种子源 0 直发（禁止直连 publisher / TG / AGENCY_TRIGGER）
    checks["a2_static_seed_no_direct_publish"] = all(
        tok not in seed for tok in _FORBIDDEN_DIRECT_PUBLISH_TOKENS
    )
    checks["a2_static_narrative_no_direct_publish"] = all(
        tok not in narr for tok in _FORBIDDEN_DIRECT_PUBLISH_TOKENS
    )

    # ── A5: 叙事/种子源 0 直写 SAGE facts（唯一入口 = InnerLifeWriter.create_event）
    checks["a5_static_narrative_no_direct_facts"] = _ast_no_call_to(narr, "add_fact")
    checks["a5_static_seed_no_direct_facts"] = _ast_no_call_to(seed, "add_fact")

    # A4 佐证: 契约 §5.6 挂点描述（night slot 判据先行 return）
    checks["a4_fire_periodic_night_slot_gate"] = (
        "_slot_for_time(now) != \"night\"" in sched
    )
    return checks


# ───────────────────────────────────────────────────────────
# TL11Runner — 四剧本验证编排器
# ───────────────────────────────────────────────────────────

class TL11Runner:
    """TL-11 承諾閉環 + 週期敘事端到端验证编排器（C-2.1 验收钢印）。"""

    def __init__(
        self,
        repo_root: Path,
        seed: int = TL11_SEED,
        experiment_id: str = TL11_EXPERIMENT_ID,
        static_root: Optional[Path] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id
        # A4 静态断言读生产 src（pytest 场景 repo_root=tmp 假根时传真实 ROOT）
        self._static_root = Path(static_root) if static_root else self._repo_root

    # ── 场景运行 ───────────────────────────────────────────

    def run_scenario(
        self,
        scenario: str,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行单个剧本（隔离 data_root）。返回 records + derived。"""
        if scenario not in SCENARIOS:
            raise ValueError(f"未知剧本: {scenario!r}")
        run_id = run_id or _new_run_id()
        harness_root = self._repo_root / "data" / "time_lapse" / self._experiment_id
        run_dir = harness_root / scenario / run_id
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        stub = _StubLLM()

        if scenario == SCENARIO_B6_CLOSURE:
            derived = self._run_b6_closure(run_dir, stub)
        elif scenario == SCENARIO_WEEKLY:
            derived = self._run_weekly(run_dir, stub)
        elif scenario == SCENARIO_MEMORIAL:
            derived = self._run_memorial(run_dir, stub)
        else:
            derived = self._run_identity_firewall(run_dir, stub)

        with open(run_dir / "derived.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(derived), ensure_ascii=False, indent=2) + "\n")
        return {
            "scenario": scenario,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "derived": derived,
        }

    # ── 剧本 1: B6 终态闭环实证（双 epoch, 各自隔离 root）────────────

    def _run_b6_closure(self, run_dir: Path, stub: _StubLLM) -> TL11ScenarioDerived:
        """epoch_completed（COMPLETED 判窗）+ epoch_abandoned（ABANDONED 判窗）。"""
        checks: Dict[str, bool] = {}
        key_numbers: Dict[str, Any] = {}

        epoch_a_dir = run_dir / "epoch_completed"
        epoch_a_dir.mkdir(parents=True, exist_ok=True)
        out_a = _asyncio_run(self._closure_epoch_completed(os.fspath(epoch_a_dir), stub))
        checks.update(out_a["checks"])
        key_numbers["epoch_completed"] = out_a["key_numbers"]

        epoch_b_dir = run_dir / "epoch_abandoned"
        epoch_b_dir.mkdir(parents=True, exist_ok=True)
        out_b = _asyncio_run(self._closure_epoch_abandoned(os.fspath(epoch_b_dir), stub))
        checks.update(out_b["checks"])
        key_numbers["epoch_abandoned"] = out_b["key_numbers"]

        key_numbers["stub_calls"] = {
            "seed_semantize": sum(
                1 for c in stub.calls if "内心独白" in c["prompt"]
            ),
            "decision": sum(1 for c in stub.calls if "decision" in c["prompt"]),
            "narrative": sum(1 for c in stub.calls if "narrative" in c["prompt"]),
        }

        passed = all(checks.values())
        summary = (
            f"epoch_completed: B1→推进×2→COMPLETED+sediment→B6→G2→volition→"
            f"{'PASS' if checks.get('a1_completed_terminal') else 'FAIL'}; "
            f"epoch_abandoned: B1→推进→timeout→ABANDONED→B6→G4 = "
            f"{'PASS' if checks.get('a1_abandoned_timeout') else 'FAIL'}; "
            f"0 直发（publish 前 bus 0）="
            f"{checks.get('a1_completed_zero_direct')} "
            f"唯一出口="
            f"{checks.get('a2_publish_only_exit')}"
        )
        return TL11ScenarioDerived(
            scenario=SCENARIO_B6_CLOSURE,
            passed=passed,
            checks=checks,
            key_numbers=key_numbers,
            summary=summary,
        )

    async def _closure_epoch_completed(self, run_dir: str, stub: _StubLLM) -> Dict[str, Any]:
        """COMPLETED 判窗 epoch: B1→ACTIVE→推进×2→COMPLETED+sediment→B6→G2→决策→唯一出口。"""
        a = TL11_AGENT
        checks: Dict[str, bool] = {}
        key: Dict[str, Any] = {}
        root = _prepare_isolated_root(Path(run_dir))

        from src.goals.motive_provider import GoalMotiveProvider
        from src.goals.seed_provider import GoalSeedProvider
        from src.soul.motive import MotiveEngine, set_llm_proxy
        from src.soul.scheduler import SoulScheduler
        from src.eventbus import SoulEventBus
        from src.eventbus.schema import EventType

        set_llm_proxy(stub)
        mp = GoalMotiveProvider.for_agent(a)
        seed = GoalSeedProvider(agent_id=a, llm_call=stub, provider=mp)
        engine = MotiveEngine()

        base = datetime.now().astimezone()
        past = _past_anchor(base)
        future = _future_anchor(base)

        # bus: 0 直发观察（publish 前必须 0 条 AGENCY_TRIGGER）
        bus = SoulEventBus()
        rec = _BusRecorder()
        bus.subscribe(
            subscriber_id="tl11_closure_observer",
            handler=rec.handle,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        await bus.start()
        scheduler = SoulScheduler(bus=bus)

        # ── T1: B1 承诺种子 → ACTIVE ──
        from src.soul.relationships import BRYAN_ENTITY_ID
        _make_relationships_file(root, a, {BRYAN_ENTITY_ID: _rel_entry()})
        _ensure_relationships_manager(root)
        created1 = await seed.scan_seeds(now=past)
        checks["a1_completed_seed_b1"] = len(created1) == 1
        g1 = created1[0] if created1 else None
        key["t1_goal"] = g1.to_dict() if g1 else None
        checks["a1_seed_ref_b1"] = bool(
            g1 and g1.seed_source_ref == f"relationship:{BRYAN_ENTITY_ID}"
        )
        checks["a1_seed_state_active"] = bool(g1 and g1.state == "ACTIVE")
        checks["a1_seed_criteria_b1"] = bool(
            g1 and g1.completion_criteria_dict() == B1_CRITERIA_EXPECTED
        )

        # ── T2/T3: 推进 ×2（24h 配额 + 真实 Decision transmit）→ COMPLETED + sediment ──
        if g1 is None:  # B1 种子失败（fail-closed）→ 断言已记 false, 短路
            await bus.stop()
            return {"checks": checks, "key_numbers": key}
        mv1 = mp.assemble_candidate(now=past + timedelta(hours=25))
        checks["a1_advance_1_candidate"] = mv1 is not None
        if mv1 is not None:
            result1 = await engine.decide(mv1, a)
            checks["a1_advance_1_decided"] = getattr(result1, "transmit", False) is True
            mp.on_decision(mv1, result1, now=past + timedelta(hours=25))
        g1_mid = self._goal_by_id(a, g1.goal_id)
        checks["a1_progress_in_progress"] = bool(
            g1_mid and g1_mid.state == "IN_PROGRESS" and g1_mid.advance_count == 1
        )

        mv2 = mp.assemble_candidate(now=past + timedelta(hours=50))
        checks["a1_advance_2_candidate"] = mv2 is not None
        if mv2 is not None:
            result2 = await engine.decide(mv2, a)
            checks["a1_advance_2_decided"] = getattr(result2, "transmit", False) is True
            mp.on_decision(mv2, result2, now=past + timedelta(hours=50))
        g1_final = self._goal_by_id(a, g1.goal_id)
        checks["a1_completed_terminal"] = bool(
            g1_final and g1_final.state == "COMPLETED" and g1_final.advance_count == 2
        )
        checks["a1_completed_zero_direct"] = len(rec.events) == 0  # sediment 不发布

        # sediment event（真实 InnerLifeWriter 链 → trace）
        trace = _read_trace(root)
        sed = [r for r in trace if (r.get("provenance") or {}).get("trace_ref") == f"goal:{g1.goal_id}"]
        checks["a1_sediment_event_via_writer"] = len(sed) == 1
        checks["a1_sediment_identity"] = bool(
            sed
            and sed[0]["provenance"].get("actor_id") == a
            and sed[0]["provenance"].get("source_system") == "system"
        )

        # ── T4: B6 判窗（B1 断源; 窗 = (T1 注入, future]）→ 关怀 goal ──
        _make_relationships_file(root, a, {})
        _ensure_relationships_manager(root)
        created2 = await seed.scan_seeds(now=future)
        checks["a2_b6_goal_created"] = len(created2) == 1
        g2 = created2[0] if created2 else None
        checks["a2_b6_ref_namespace"] = bool(
            g2 and g2.seed_source_ref == f"commitment_closure:{g1.goal_id}"
        )
        checks["a2_b6_axis_bryan"] = bool(g2 and g2.axis == "bryan")
        checks["a2_b6_criteria_template"] = bool(
            g2 and g2.completion_criteria_dict() == B6_CRITERIA_EXPECTED
        )
        checks["a2_b6_no_direct_publish"] = len(rec.events) == 0  # 建池 0 直发
        # B6 素材只含结构事实（title/终态/次数/时点; No-Scoring §4.3 不变式 3）
        completed_prompts = [
            c["prompt"] for c in stub.calls
            if "内心独白" in c["prompt"] and "已達成" in c["prompt"]
        ]
        checks["a2_b6_material_completed_label"] = bool(
            completed_prompts
            and "推進次數=" in completed_prompts[0]
            and "終態時點=" in completed_prompts[0]
        )
        key["g2"] = g2.to_dict() if g2 else None

        # ── T5: volition path —— 候选池 → 四元 Decision → 推进收束 ──
        if g2 is None:  # B6 判窗失败（fail-closed）→ 断言已记 false, 短路
            await bus.stop()
            return {"checks": checks, "key_numbers": key}
        # 同轴 streak=2 → 强制换轴, 反馈 (bryan) 需经防饿死兜底（真实轮替语义）:
        # 连续放弃 2 次后第 3 次允许原轴再产候选（TG-1 §5.3）
        mv2 = None
        attempts = 0
        for _attempt in range(3):
            attempts += 1
            mv2 = mp.assemble_candidate(now=future + timedelta(hours=25))
            if mv2 is not None:
                break
        checks["a2_feedback_in_candidate_pool"] = mv2 is not None
        key["assemble_attempts"] = attempts
        checks["a2_feedback_in_candidate_pool"] = mv2 is not None
        if mv2:
            checks["a2_feedback_provenance_goal"] = (
                getattr(mv2, "provenance_ref", "") == f"goal:{g2.goal_id}"
            )
            result2 = await engine.decide(mv2, a)
            checks["a2_feedback_decision_transmit"] = getattr(result2, "transmit", False) is True
            mp.on_decision(mv2, result2, now=future + timedelta(hours=25))
        g2_final = self._goal_by_id(a, g2.goal_id)
        checks["a2_feedback_closure_completed"] = bool(
            g2_final and g2_final.state == "COMPLETED" and g2_final.advance_count == 1
        )
        # §4.3 不变式 1: 一次终态一次反馈（G1 出窗后无重复）
        created3 = await seed.scan_seeds(now=future + timedelta(hours=26))
        checks["a2_no_repeat_feedback"] = len(created3) == 0
        cc_goals = [g for g in self._all_goals(a)
                    if (g.seed_source_ref or "").startswith("commitment_closure:")]
        checks["a2_once_per_terminal"] = len(cc_goals) == 1

        # ── T6: 唯一出口 —— 显式既有 publish 链 → 恰 1 条 AGENCY_TRIGGER ──
        checks["a2_zero_bypass_before_publish"] = len(rec.events) == 0
        await scheduler._publish_agency_trigger(a, "event")  # noqa: SLF001
        await bus.stop()  # flush 异步 dispatch 后统计
        checks["a2_publish_only_exit"] = len(rec.events) == 1

        key["bus_events_before_publish"] = 0
        key["bus_events_total"] = len(rec.events)
        return {"checks": checks, "key_numbers": key}

    async def _closure_epoch_abandoned(self, run_dir: str, stub: _StubLLM) -> Dict[str, Any]:
        """ABANDONED 判窗 epoch: B1→推进→timeout（apply_interrupt_signals）→B6→逾期釋懷反馈。"""
        a = TL11_AGENT
        checks: Dict[str, bool] = {}
        key: Dict[str, Any] = {}
        root = _prepare_isolated_root(Path(run_dir))

        from src.goals.motive_provider import GoalMotiveProvider
        from src.goals.seed_provider import GoalSeedProvider
        from src.soul.motive import MotiveEngine, set_llm_proxy
        from src.soul.scheduler import SoulScheduler
        from src.eventbus import SoulEventBus
        from src.eventbus.schema import EventType

        set_llm_proxy(stub)
        mp = GoalMotiveProvider.for_agent(a)
        seed = GoalSeedProvider(agent_id=a, llm_call=stub, provider=mp)
        engine = MotiveEngine()

        base = datetime.now().astimezone()
        past = _past_anchor(base)
        future = _future_anchor(base)

        bus = SoulEventBus()
        rec = _BusRecorder()
        bus.subscribe(
            subscriber_id="tl11_abandoned_observer",
            handler=rec.handle,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        await bus.start()
        scheduler = SoulScheduler(bus=bus)

        # ── T1: B1 第二承诺（独立 root）→ ACTIVE ──
        from src.soul.relationships import BRYAN_ENTITY_ID
        _make_relationships_file(root, a, {BRYAN_ENTITY_ID: _rel_entry()})
        _ensure_relationships_manager(root)
        created1 = await seed.scan_seeds(now=past)
        checks["a1_abandoned_seed_b1"] = len(created1) == 1
        g3 = created1[0] if created1 else None

        # ── T2: 推进 1 次 → IN_PROGRESS（last_advanced_at 入注入时间轴）──
        if g3 is None:  # B1 种子失败（fail-closed）→ 断言已记 false, 短路
            await bus.stop()
            return {"checks": checks, "key_numbers": key}
        mv = mp.assemble_candidate(now=past + timedelta(hours=25))
        checks["a1_abandoned_progress_candidate"] = mv is not None
        if mv:
            result = await engine.decide(mv, a)
            mp.on_decision(mv, result, now=past + timedelta(hours=25))
        g3_ip = self._goal_by_id(a, g3.goal_id)
        checks["a1_abandoned_in_progress"] = bool(
            g3_ip and g3_ip.state == "IN_PROGRESS" and g3_ip.advance_count == 1
        )

        # ── T3: timeout 判据（真实 apply_interrupt_signals; 注入 8d+1h > timeout_days 7）──
        now_timeout = past + timedelta(hours=25) + timedelta(days=8, hours=1)
        changed = mp.apply_interrupt_signals(now=now_timeout)
        g3_final = self._goal_by_id(a, g3.goal_id)
        checks["a1_abandoned_timeout"] = bool(
            g3.goal_id in changed and g3_final and g3_final.state == "ABANDONED"
        )
        checks["a1_abandoned_zero_direct"] = len(rec.events) == 0

        # ── T4: B6 判窗（B1 断源; 窗 = (T1 注入, future] 含墙钟终态）→ 逾期釋懷反馈 ──
        _make_relationships_file(root, a, {})
        _ensure_relationships_manager(root)
        created2 = await seed.scan_seeds(now=future)
        checks["a2_b6_abandoned_created"] = len(created2) == 1
        g4 = created2[0] if created2 else None
        if g4 is None:  # B6 判窗失败（fail-closed）→ 断言已记 false, 短路
            await bus.stop()
            return {"checks": checks, "key_numbers": key}
        checks["a2_b6_abandoned_ref"] = bool(
            g4 and g4.seed_source_ref == f"commitment_closure:{g3.goal_id}"
        )
        checks["a2_b6_abandoned_state_active"] = bool(g4 and g4.state == "ACTIVE")
        checks["a2_b6_abandoned_no_direct"] = len(rec.events) == 0
        # 素材终态标记（ABANDONED → 已逾期釋懷; 語調由語義化生成, 素材 0 打分）
        abandoned_prompts = [
            c["prompt"] for c in stub.calls
            if "内心独白" in c["prompt"] and "已逾期釋懷" in c["prompt"]
        ]
        checks["a2_b6_abandoned_label"] = len(abandoned_prompts) == 1
        cc_goals = [g for g in self._all_goals(a)
                    if (g.seed_source_ref or "").startswith("commitment_closure:")]
        checks["a2_once_per_terminal_b"] = len(cc_goals) == 1

        await scheduler._publish_agency_trigger(a, "event")  # noqa: SLF001
        await bus.stop()  # flush 异步 dispatch 后统计
        checks["a2_abandoned_publish_only_exit"] = len(rec.events) == 1

        key["g3_final"] = g3_final.to_dict() if g3_final else None
        key["g4"] = g4.to_dict() if g4 else None
        return {"checks": checks, "key_numbers": key}

    # ── 剧本 2: 周记频率实证（A3）─────────────────────────────

    def _run_weekly(self, run_dir: Path, stub: _StubLLM) -> TL11ScenarioDerived:
        return _asyncio_run(self._weekly_impl(os.fspath(run_dir), stub))

    async def _weekly_impl(self, run_dir: str, stub: _StubLLM) -> TL11ScenarioDerived:
        a = TL11_AGENT
        checks: Dict[str, bool] = {}
        key: Dict[str, Any] = {}
        root = _prepare_isolated_root(Path(run_dir))

        from src.goals.narrative_sublimator import PeriodicNarrativeSublimator

        # fixture: 本周（W37）周一至周三自己的 diary
        _write_diary(root, a, WEEKLY_MONDAY, "night", "本周开端的日记")
        _write_diary(root, a, date(2026, 9, 8), "night", "本周周二的日记")
        _write_diary(root, a, WEEKLY_WEDNESDAY, "morning", "本周周三的日记")

        sub = PeriodicNarrativeSublimator(agent_id=a, llm_call=stub)
        week37_key = f"periodic:{WEEKLY_MONDAY.isocalendar().year}-W{WEEKLY_MONDAY.isocalendar().week:02d}"

        # ── 首触（周三 night slot 22:00:30）→ 1 次沉淀 ──
        night_wed = _local_dt(2026, 9, 9, 22, 0, 30)
        e1 = await sub.sublimate_weekly(now=night_wed)
        checks["a3_first_sediment"] = e1 is not None

        # ── 同夜重入（scheduler 60s 触发窗偶发重入）→ 0 二次沉淀 ──
        e1b = await sub.sublimate_weekly(now=night_wed)
        checks["a3_idempotent_same_night"] = e1b is None

        # ── 同 ISO 周次日再触发 → 0 二次沉淀 ──
        e2 = await sub.sublimate_weekly(now=_local_dt(2026, 9, 10, 22, 0, 30))
        checks["a3_no_double_same_week"] = e2 is None

        trace = _read_trace(root)
        w37_events = [r for r in trace
                      if (r.get("provenance") or {}).get("trace_ref") == week37_key]
        checks["a3_weekly_once_per_iso_week"] = len(w37_events) == 1
        checks["a3_weekly_identity"] = bool(
            w37_events
            and w37_events[0]["provenance"].get("actor_id") == a
            and w37_events[0]["provenance"].get("source_system") == "system"
        )

        # ── 跨周（下周一 W38 night）→ 新 ISO 周 1 次新沉淀 ──
        _write_diary(root, a, WEEKLY_NEXT_MONDAY, "night", "下一周开端的日记")
        night_next = _local_dt(2026, 9, 14, 22, 0, 30)
        e3 = await sub.sublimate_weekly(now=night_next)
        checks["a3_cross_week_new_sediment"] = e3 is not None
        trace2 = _read_trace(root)
        periodic_events = [r for r in trace2
                           if (r.get("provenance") or {}).get("trace_ref", "").startswith("periodic:")]
        checks["a3_two_weeks_two_sediments"] = len(periodic_events) == 2

        # ── 非每日产物: 叙事只挂 night slot（真实 _slot_for_time 判据）──
        from src.soul.scheduler import SoulScheduler
        sched = SoulScheduler()
        checks["a3_morning_is_morning"] = sched._slot_for_time(  # noqa: SLF001
            _local_dt(2026, 9, 10, 8, 0, 30)
        ) == "morning"  # 08:00:30 在既有 morning 触发窗内（diary 拍, 非叙事）
        checks["a3_non_slot_none"] = sched._slot_for_time(  # noqa: SLF001
            _local_dt(2026, 9, 10, 10, 0, 0)
        ) is None  # 白天非 slot 时刻: 无任何触发（叙事 none-slot 0 动作）
        checks["a3_night_slot_night"] = sched._slot_for_time(  # noqa: SLF001
            _local_dt(2026, 9, 10, 22, 0, 30)
        ) == "night"
        checks["a3_no_extra_daily"] = len(periodic_events) == 2  # 周记非每日产物

        key["week37_trace_ref"] = week37_key
        key["weekly_event_ids"] = [w37_events[0].get("event_id")] if w37_events else []
        key["periodic_count"] = len(periodic_events)

        passed = all(checks.values())
        summary = (
            f"首触 W37={e1 is not None} 同夜重入 0={e1b is None} "
            f"同周再触 0={e2 is None} 跨周新沉淀 ={e3 is not None}; "
            f"periodic 总数 = {len(periodic_events)}（两周各 1, 7 天窗内 ≤1）"
        )
        return TL11ScenarioDerived(
            scenario=SCENARIO_WEEKLY,
            passed=passed,
            checks=checks,
            key_numbers=key,
            summary=summary,
        )

    # ── 剧本 3: 纪念日反芻实证（A5 + A6 侧面）────────────────

    def _run_memorial(self, run_dir: Path, stub: _StubLLM) -> TL11ScenarioDerived:
        return _asyncio_run(self._memorial_impl(os.fspath(run_dir), stub))

    async def _memorial_impl(self, run_dir: str, stub: _StubLLM) -> TL11ScenarioDerived:
        a, o = TL11_AGENT, TL11_OTHER
        checks: Dict[str, bool] = {}
        key: Dict[str, Any] = {}
        root = _prepare_isolated_root(Path(run_dir))

        from src.goals.narrative_sublimator import PeriodicNarrativeSublimator

        # fixture: 往年今日（同 M-D 不同年）自己的 diary + 他者（路径隔离）
        _write_diary(root, a, MEMORIAL_PRIOR_2025, "night", "去年今日我的日记")
        _write_diary(root, a, MEMORIAL_PRIOR_2024, "night", "前年今日我的日记")
        _write_diary(root, o, MEMORIAL_PRIOR_2025, "night", "他者往年今日日记污染标记")
        # 今日 accepted calendar_event（白名单既有产物的真实格式）
        today_ts = _local_dt(2026, 9, 6, 10, 0).isoformat()
        _append_perception_calendar_event(root, today_ts, "anniv-20260906", summary="值得纪念的周年日子")

        sub = PeriodicNarrativeSublimator(agent_id=a, llm_call=stub)
        now_mem = _local_dt(2026, 9, 6, 22, 0, 30)
        e1 = await sub.sublimate_memorial(now=now_mem)
        checks["a5_memorial_sediment"] = e1 is not None

        # 沉淀事件身份（防线 3 复核: actor_id==self / source_system=="system"）
        trace = _read_trace(root)
        mem_ref = f"periodic:memorial:{MEMORIAL_DAY.isoformat()}"
        mem_events = [r for r in trace if (r.get("provenance") or {}).get("trace_ref") == mem_ref]
        checks["a5_memorial_trace_ref"] = len(mem_events) == 1
        mv = mem_events[0]["provenance"] if mem_events else {}
        checks["a6_memorial_actor_self"] = mv.get("actor_id") == a
        checks["a6_memorial_source_system"] = mv.get("source_system") == "system"
        checks["a6_memorial_trigger_system"] = mv.get("trigger_type") == "system"
        extras = mv.get("extras") or {}
        checks["a5_memorial_period_meta"] = (
            extras.get("period") == "memorial"
            and bool(extras.get("event_summary"))
        )

        # 聚合只聚自己的往年 diary（prompt 0 他者内容; 路径隔离）
        mem_prompts = [c["prompt"] for c in stub.calls if "纪念日回望" in c["prompt"]]
        checks["a6_memorial_prompt_only_own"] = bool(
            mem_prompts
            and "去年今日我的日记" in mem_prompts[-1]
            and "前年今日我的日记" in mem_prompts[-1]
            and "他者往年今日日记污染标记" not in mem_prompts[-1]
        )

        # 0 直写 SAGE facts（沉淀路径 = InnerLifeWriter.create_event 既有升华链）
        checks["a5_memorial_facts_zero"] = _facts_count(root) == 0

        # ── 幂等与 fail-closed ──
        e1b = await sub.sublimate_memorial(now=now_mem)
        checks["a3_memorial_idempotent"] = e1b is None
        # 另一日历日有事件但往年空 → 空聚合 fail-closed 0 半成品
        _append_perception_calendar_event(
            root, _local_dt(2026, 9, 7, 10, 0).isoformat(), "anniv-20260907", summary="另一个特别日子"
        )
        e2 = await sub.sublimate_memorial(now=_local_dt(2026, 9, 7, 22, 0, 30))
        checks["a5_memorial_empty_aggregate_fail_closed"] = e2 is None
        # 无事件日期 → 0 动作
        e3 = await sub.sublimate_memorial(now=_local_dt(2026, 9, 8, 22, 0, 30))
        checks["a5_memorial_no_event_no_action"] = e3 is None
        trace3 = _read_trace(root)
        checks["a5_memorial_zero_half_products"] = (
            len([r for r in trace3
                 if (r.get("provenance") or {}).get("trace_ref", "").startswith("periodic:memorial:")])
            == 1
        )

        key["memorial_trace_ref"] = mem_ref
        key["prompt_tail"] = mem_prompts[-1][-200:] if mem_prompts else ""

        passed = all(checks.values())
        summary = (
            f"纪念日沉淀={e1 is not None} 身份(actor={mv.get('actor_id')}, "
            f"src={mv.get('source_system')}); 幂等={e1b is None} "
            f"空聚合 fail-closed={e2 is None} 无事件={e3 is None}; facts={_facts_count(root)}"
        )
        return TL11ScenarioDerived(
            scenario=SCENARIO_MEMORIAL,
            passed=passed,
            checks=checks,
            key_numbers=key,
            summary=summary,
        )

    # ── 剧本 4: 身份防火墙实证（A6 + A5 强化）────────────────

    def _run_identity_firewall(self, run_dir: Path, stub: _StubLLM) -> TL11ScenarioDerived:
        return _asyncio_run(self._identity_impl(os.fspath(run_dir), stub))

    async def _identity_impl(self, run_dir: str, stub: _StubLLM) -> TL11ScenarioDerived:
        a, o = TL11_AGENT, TL11_OTHER
        checks: Dict[str, bool] = {}
        key: Dict[str, Any] = {}
        root = _prepare_isolated_root(Path(run_dir))

        from src.goals.narrative_sublimator import PeriodicNarrativeSublimator

        # fixture: W39 自己的 diary
        _write_diary(root, a, FW_MONDAY, "night", f"{MARK_OWN_DIARY}之一")
        _write_diary(root, a, FW_WEDNESDAY, "morning", f"{MARK_OWN_DIARY}之二")
        # 他者 diary（路径隔离: data/soul/{other}/diary/ → 天然不进聚合窗）
        _write_diary(root, o, FW_MONDAY, "night", MARK_OTHER_DIARY)
        # trace 注入（真实 trace 文件, event_to_dict 同构格式）:
        # 自己的经历（应被聚合）/ 他者经历（0 内化）/ 自己的 goal: 引用（0 递归）
        _append_trace_record(
            root, "tl11-own-trace-0001", a,
            "2026-09-22T10:00:00+00:00", "diary", None,
            {"marker": MARK_OWN_TRACE},
        )
        _append_trace_record(
            root, "tl11-other-trace-0002", o,
            "2026-09-22T11:00:00+00:00", "diary", None,
            {"marker": MARK_OTHER_TRACE},
        )
        _append_trace_record(
            root, "tl11-recursive-0003", a,
            "2026-09-22T12:00:00+00:00", "system", "goal:already-sedimented",
            {"marker": MARK_RECURSIVE},
        )

        sub = PeriodicNarrativeSublimator(agent_id=a, llm_call=stub)
        now_fw = _local_dt(2026, 9, 23, 22, 0, 30)
        e1 = await sub.sublimate_weekly(now=now_fw)
        checks["a6_weekly_sediment"] = e1 is not None

        # 聚合 prompt 0 内化他者内容（防火墙三大规则）
        weekly_prompts = [c["prompt"] for c in stub.calls if "narrative" in c["prompt"]]
        prompt = weekly_prompts[-1] if weekly_prompts else ""
        checks["a6_self_diary_included"] = MARK_OWN_DIARY in prompt
        checks["a6_self_trace_included"] = MARK_OWN_TRACE in prompt
        checks["a6_other_diary_excluded"] = MARK_OTHER_DIARY not in prompt
        checks["a6_other_trace_excluded"] = MARK_OTHER_TRACE not in prompt
        checks["a6_no_recursive_goal_ref"] = MARK_RECURSIVE not in prompt

        # 沉淀事件身份（防線 3: actor_id==self / source_system==system）
        trace = _read_trace(root)
        w39_key = f"periodic:{FW_MONDAY.isocalendar().year}-W{FW_MONDAY.isocalendar().week:02d}"
        w39_events = [r for r in trace
                      if (r.get("provenance") or {}).get("trace_ref") == w39_key]
        checks["a6_weekly_once"] = len(w39_events) == 1
        mv = w39_events[0]["provenance"] if w39_events else {}
        checks["a6_sediment_actor_self"] = mv.get("actor_id") == a
        checks["a6_sediment_source_system"] = mv.get("source_system") == "system"
        checks["a6_sediment_trace_ref"] = (
            mv.get("trace_ref") == w39_key
            and (mv.get("extras") or {}).get("period") == "weekly"
        )

        # A5 强化: 沉淀后 SAGE facts 表 0 直写
        checks["a6_facts_zero_after_sediment"] = _facts_count(root) == 0

        key["w39_trace_ref"] = w39_key
        key["prompt_has_other"] = MARK_OTHER_TRACE in prompt

        passed = all(checks.values())
        summary = (
            f"周记沉淀={e1 is not None} 自己的(diary+trace)入={checks['a6_self_diary_included']} "
            f"他者 diary 出={not checks['a6_other_diary_excluded']} "
            f"他者 trace 出={not checks['a6_other_trace_excluded']} "
            f"递归引用出={not checks['a6_no_recursive_goal_ref']}; "
            f"身份(actor={mv.get('actor_id')}, src={mv.get('source_system')}) facts=0"
        )
        return TL11ScenarioDerived(
            scenario=SCENARIO_IDENTITY_FIREWALL,
            passed=passed,
            checks=checks,
            key_numbers=key,
            summary=summary,
        )

    # ── 只读断言 helpers ──────────────────────────────────

    def _goal_by_id(self, agent_id: str, goal_id: str) -> Optional[Any]:
        for g in self._all_goals(agent_id):
            if g.goal_id == goal_id:
                return g
        return None

    def _all_goals(self, agent_id: str) -> List[Any]:
        """读侧走共享 provider store（0 新建 GraphStore 实例 → 0 并发锁）。"""
        from src.goals.motive_provider import GoalMotiveProvider
        return GoalMotiveProvider.for_agent(agent_id)._store_for().get_goals(agent_id)  # noqa: SLF001

    # ── run 系列 (D2 determinism + 0 mutation + A4 静态) ────

    def run_series(
        self,
        scenarios: Optional[tuple[str, ...]] = None,
        n_runs: int = 3,
    ) -> Dict[str, Any]:
        """四剧本各连跑 n_runs 次（D2 宏确定性）+ A4 静态断言 + production 0 mutation。"""
        scenarios = scenarios or SCENARIOS
        production_root = self._repo_root / "data"
        from .runner import snapshot_data_root_hashes, verify_zero_mutation
        before = snapshot_data_root_hashes(production_root)

        static_checks = _static_assertions(self._static_root)
        static_ok = all(static_checks.values())

        series: List[TL11SeriesMetrics] = []
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
            series.append(TL11SeriesMetrics(
                scenario=scenario,
                n_runs=n_runs,
                determinism_ok=determinism_ok,
                all_passed=s_passed,
                per_run_passed=passed_flags,
                summary=(
                    f"{SCENARIO_LABELS[scenario]}: "
                    f"{'ALL PASS' if s_passed else 'FAIL'} "
                    f"(3 runs 判定一致={determinism_ok})"
                ),
            ))

        mut_res = verify_zero_mutation(production_root, before)
        zero_mut_ok = mut_res["pass"]
        all_passed = all_passed and zero_mut_ok and static_ok

        return {
            "experiment_id": self._experiment_id,
            "scenarios": [asdict(s) for s in series],
            "all_passed": all_passed,
            "static_assertions": static_checks,
            "static_ok": static_ok,
            "zero_mutation_ok": zero_mut_ok,
            "mutation_diff": mut_res["diff"],
            "mutation_added": mut_res["added"],
        }


# ───────────────────────────────────────────────────────────
# D2 宏确定性: 跨 run 判定字段比对（uuid 不参与）
# ───────────────────────────────────────────────────────────

_DETERMINISM_FIELDS = (
    "scenario", "passed", "summary",
)


def _scenario_determinism(runs: List[Dict[str, Any]]) -> bool:
    """同一剧本 3 个 run 的派生判定字段完全一致（MoE 特性下宏确定性）。"""
    if not runs:
        return False
    ref = asdict(runs[0]["derived"])
    for run in runs[1:]:
        cur = asdict(run["derived"])
        if len(cur) != len(ref):
            return False
        for key in _DETERMINISM_FIELDS:
            if cur.get(key) != ref.get(key):
                return False
        if cur.get("checks") != ref.get("checks"):
            return False
    return True


def _asyncio_run(coro: Any) -> Any:
    """独立 event loop 跑剧本（对齐 TL-10: 每个剧本独立 loop, 0 残留 task）。"""
    import asyncio
    return asyncio.run(coro)


__all__ = [
    "TL11_EXPERIMENT_ID",
    "TL11_AGENT",
    "TL11_OTHER",
    "SCENARIOS",
    "SCENARIO_LABELS",
    "B6_CRITERIA_EXPECTED",
    "TL11ScenarioDerived",
    "TL11Runner",
    "_static_assertions",
]