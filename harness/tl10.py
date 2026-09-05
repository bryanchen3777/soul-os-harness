"""
harness/tl10.py — TL-10 Relational Expression End-to-End Harness (C-3.1 验收钢印)

工单 TL-10（决策已定, 照做）:
  - 目标: 为 C-3.1（关系带双组装注入 + motive_target 透传 + P1 公开频道分流）
    打上 Time-lapse/端到端 Harness 验收钢印, 让 C-3.1 正式 CLOSED。
  - 模式: 全程走真实实现、隔离 data_root、确定性 stub（仅 LLM / bus 收集）、
    0 src/ 生产改动。四大剧本硬断言由 Owner 拍板, 不可减弱。

真实实现入口（0 另写模拟）:
  - scripts/run_server.py 模块级纯函数 resolve_proactive_delivery —— P1 分流判定。
    正常 import 不启动 server（启动在 `if __name__ == "__main__"` 保护内）; 模块级
    side-effect 仅有 faulthandler 日志（隔离 data_root）+ logging 配置, 以
    importlib 载入并缓存, 载入后 cancel 60s dump 定时器（0 阻塞 side-effect）。
  - src/llm/proxy.py `_format_relational_perception_block(agent_id, target)`
    —— 注入块渲染（真实函数, 三重 fail-safe → ""）。
  - src/llm/proxy.py `_build_messages_group` / `_build_messages_private`
    —— A2A/A2U 双组装注入点（可选参数 motive_target, None 零行为变化）。
  - src/soul/scheduler.py `_decision_check`（transmit 分支记录 _last_transmit_target）
    + `_publish_agency_trigger`（extra["motive_target"] 写入 + 单次消费）。
  - src/agent/consciousness.py `_fire_intent`（chrono_payload → intent_payload 透传,
    经 AgentYua 真实子类实例）。
  - src/soul/motive.py target 归一化语义（"bryan" → relationships key "user_bryan",
    契约 §2.4）+ set_agent_ids / get_agent_ids（SG-2 注册表）+ MotiveTraceStore /
    make_motive（fail-closed）+ set_llm_proxy（确定性 stub 注入点）。
  - src/soul/relationships.py 真实存取（get_relationships_manager →
    get_store(agent_id).get(other_id); 4.2 schema fixture 直接写隔离目录）。

四大剧本（scenario, Owner 拍板）:
  1. a2a_public_routing（剧本 1 A2A 客厅公开分流实证）: scheduler transmit 记录
     → extra["motive_target"] → resolve_proactive_delivery 全链; 硬断言
     resolve_proactive_delivery("agent_b") 严格导出 {"mode": "group",
     "target_channel": None, "target_user_id": None}（lounge/soul_wall 公开语义),
     0 穿透 Bryan 1:1 私聊（mode 不得为 private、target_user_id 为空）。
  2. band_injection（剧本 2 关系带差异化注入实证）: stranger / familiar 两种关系带
     下触发 A2A 组装（真实 _format_relational_perception_block + 真实 relationships
     entry）; 硬断言 注入块存在且标签逐字相符（stranger→陌生人 / familiar→熟悉）、
     impression_tags 正确渲染、整块 token 估算严格 ≤80、stranger 也注入。
  3. a2u_private_preserve（剧本 3 A2U 私聊保全实证）: target == "user_bryan" 与
     原始 "bryan" 各测一次; 硬断言 归一化生效、resolve_proactive_delivery 返回
     private/telegram/Bry chat_id 100% 维持原状、A2U 私聊组装注入正常且 None
     向后兼容（无 motive_target 时行为逐字节不变）。
  4. fail_safe（剧本 4 三重 Fail-Safe 容错复核）: ①无关系记录 ②非合法 target
     （None/空/莫名字串）③读取异常（mock 抛异常）; 硬断言 信息块安全平滑省略
     （回 ""), 0 崩溃 0 抛出未捕获异常; resolve_proactive_delivery 未知 target
     fail-safe 默认私聊不报错。

Frozen Contract 边界（0 change): Agency 4 stages / TriggerEnvelope /
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

logger = logging.getLogger("soul_os.harness.tl10")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL10_EXPERIMENT_ID = "TL-10"
TL10_SEED = 42

# 双 agent fixture (对齐 TL-9: 映射 configs/default.yaml 既有 persona 配置)
TL10_AGENT_A = "agent_ruka"      # 主体 Agent (发起方)
TL10_AGENT_B = "agent_akane"     # 目标他者 (P1 group 分流对象)

SCENARIO_A2A_ROUTING = "a2a_public_routing"      # 剧本 1
SCENARIO_BAND_INJECTION = "band_injection"       # 剧本 2
SCENARIO_A2U_PRESERVE = "a2u_private_preserve"   # 剧本 3
SCENARIO_FAIL_SAFE = "fail_safe"                 # 剧本 4

SCENARIOS = (
    SCENARIO_A2A_ROUTING,
    SCENARIO_BAND_INJECTION,
    SCENARIO_A2U_PRESERVE,
    SCENARIO_FAIL_SAFE,
)

SCENARIO_LABELS = {
    SCENARIO_A2A_ROUTING: "剧本 1 A2A 客厅公开分流实证 (P1 闭环)",
    SCENARIO_BAND_INJECTION: "剧本 2 关系带差异化注入实证 (Prompt 感知核验)",
    SCENARIO_A2U_PRESERVE: "剧本 3 A2U 私聊保全实证 (Bryan 通道零退化)",
    SCENARIO_FAIL_SAFE: "剧本 4 三重 Fail-Safe 容错复核",
}

# C-3.1 契约 §4.1 band label 映射 (断言用镜像; helper 内部持有同契约映射)
BAND_LABELS = {
    "stranger": "陌生人",
    "known": "認識",
    "familiar": "熟悉",
    "close": "親近",
}

# C-3.1 契约 §4.2 token 预算 (硬上限)
MAX_REL_BLOCK_TOKENS = 80

# 剧本 2 最坏情况: 远超过 5 个长 tag → 真实 helper 只取前 5 个、单项最长 12 字符
WORST_CASE_TAGS = [
    "滿天星星閃耀著", "一起聽過的音樂", "客廳窗邊的午後", "喜歡的甜點口味",
    "深夜長談的回憶", "一起看過的電影",  # 第 6 个 (超量 → 真实实现截断)
]
WORST_CASE_TAGS_EXTRA = ["第六個標籤不該出現", "第七個也一樣"]  # 超量 → 截断

# P1 私聊路由 (C-3.1 判定表 fail-safe / Bryan 通道, run_server 真实常量)
BRYAN_CHAT_ID = "1696287850"
PRIVATE_DELIVERY = {
    "mode": "private",
    "target_channel": "telegram",
    "target_user_id": BRYAN_CHAT_ID,
}


# ───────────────────────────────────────────────────────────
# 数据结构
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL10ScenarioDerived:
    """单场景派生指标 (canonical evidence 之一)。"""
    scenario: str
    passed: bool
    checks: Dict[str, bool]
    key_numbers: Dict[str, Any]
    summary: str


@dataclass(frozen=True)
class TL10SeriesMetrics:
    """run 系列派生指标。"""
    scenario: str
    n_runs: int
    determinism_ok: bool
    all_passed: bool
    per_run_passed: List[bool]
    summary: str


# ───────────────────────────────────────────────────────────
# 确定性 stub LLM (0 网络调用)
# ───────────────────────────────────────────────────────────

class _StubLLMProxy:
    """Decision stub (process-global proxy 注入点, set_llm_proxy 对齐)。

    真实 _default_llm_call 以 `proxy.generate_text(messages=, agent_id=,
    max_tokens=, temperature=)` 形状调用; 只有 decision prompt 返回
    transmit JSON（真实 parse_decision_output 解析）, 其余调用返回 None
    （fail-closed 0 产出, 例如 Goal 引擎语义化 / Motive interpretation——
    隔离目录下本就无 InnerLifeEvent, 不必产出）。
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
            {"agent_id": agent_id, "max_tokens": max_tokens,
             "temperature": temperature, "prompt_tail": content[-120:]}
        )
        if "decision" in content:
            return (
                '{"decision": "transmit", '
                '"reason": "TL-10 stub: 确定性四元选择 (transmit)。"}'
            )
        return None  # 非 decision 调用 → fail-closed 0 产出


# ───────────────────────────────────────────────────────────
# 真实 Event Bus 收集 (真实 SoulEventBus + 记录 handler, 0 模拟 bus)
# ───────────────────────────────────────────────────────────

class _BusRecorder:
    """真实 SoulEventBus 的订阅记录器 (async handler 收件箱)。"""

    def __init__(self) -> None:
        self.events: List[Any] = []

    async def handle(self, event: Any) -> None:
        self.events.append(event)


def _run_bus_scenario(agent_a: str, target: str) -> Dict[str, Any]:
    """剧此 1/3 的 scheduler + consciousness 真实全链 (在独立 event loop 内)。

    走真实函数链:
      MotiveTraceStore.append_motive(真实 pending) →
      SoulScheduler._decision_check（真实: MotiveEngine.resolve_pending →
        decide_motive → decide_motive stub LLM → transmit 分支记录
        _last_transmit_target）→
      SoulScheduler._publish_agency_trigger（真实: extra["motive_target"] 写入
        + 单次消费）→ 真实 SoulEventBus.publish →
      再调一次 _publish_agency_trigger（验证单次消费）→
      AgentYua._fire_intent（真实: chrono_payload["motive_target"] →
        intent_payload 透传）→ 真实 SoulEventBus.publish。

    Returns:
        {"trigger_events": [...], "intent_events": [...], "calls": [...],
         "last_target_after_consume": ...}
    """
    # ── lazy import 真实实现 (隔离 data_root 就绪后, 0 src 改动) ──
    from src.agent.consciousness import AgentYua
    from src.eventbus import SoulEventBus
    from src.eventbus.schema import EventType
    from src.soul.motive import (
        MotiveTraceStore,
        make_motive,
        new_motive_id,
        set_llm_proxy,
    )
    from src.soul.scheduler import SoulScheduler

    stub = _StubLLMProxy()

    async def _run() -> Dict[str, Any]:
        # bus 1: AGENCY_TRIGGER 收集
        bus1 = SoulEventBus()
        rec1 = _BusRecorder()
        bus1.subscribe(
            subscriber_id="tl10_agency_observer",
            handler=rec1.handle,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        await bus1.start()
        scheduler = SoulScheduler(bus=bus1)
        set_llm_proxy(stub)

        # 真实 pending motive (target = 目标他者); created_at 取当前 → 未过期
        trace_store = MotiveTraceStore()
        motive = make_motive(
            motive_id=new_motive_id(),
            content="TL-10 stub 意向: 想找目标聊聊最近一起经历的事",
            target=target,
            provenance_ref="tl10:fixture",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        trace_store.append_motive(motive, agent_a)

        # ── 全链 1: _decision_check → transmit 记录 ──
        decided = await scheduler._decision_check(agent_a)  # noqa: SLF001
        last_target_after_decision = scheduler._last_transmit_target

        # ── 全链 2: _publish_agency_trigger → extra 写入 + 单次消费 ──
        await scheduler._publish_agency_trigger(agent_a, trigger_type="event")
        after_first_publish = scheduler._last_transmit_target  # 应已清空 (单次消费)
        # 第二次发布: 无残留 target 泄漏 (0 残留)
        await scheduler._publish_agency_trigger(agent_a, trigger_type="event")
        await bus1.stop()
        trigger_events = [e for e in rec1.events]

        # ── 全链 3: _fire_intent 透传 (真实 AgentYua + chrono_payload) ──
        bus2 = SoulEventBus()
        rec2 = _BusRecorder()
        bus2.subscribe(
            subscriber_id="tl10_intent_observer",
            handler=rec2.handle,
            event_filter={EventType.AGENT_INTENT},
        )
        await bus2.start()
        yua = AgentYua(agent_id=agent_a, bus=bus2)
        await yua._fire_intent(  # noqa: SLF001
            reason="event",
            elapsed_mins=10.0,
            chrono_payload={"motive_target": target},
            mode="group",
        )
        await bus2.stop()
        intent_events = [e for e in rec2.events]

        return {
            "decided": decided,
            "last_target_after_decision": last_target_after_decision,
            "after_first_publish": after_first_publish,
            "trigger_events": trigger_events,
            "intent_events": intent_events,
            "calls": list(stub.calls),
        }

    return asyncio.run(_run())


# ───────────────────────────────────────────────────────────
# 隔离环境装配 helpers
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _reset_process_state() -> None:
    """run 之间重置进程级单例 (隔离 data_root 切换后缓存必须清空)。"""
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
    """装配隔离 data_root (SOUL_OS_DATA_DIR + 单例重置)。返回 isolated root。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
    from src.paths import data_root, reset_data_root
    reset_data_root()
    _reset_process_state()
    isolated_root = data_root()
    # per-agent graph.sqlite (Schema v8 迁移预检; Goal 引擎 lazy 打开复用)
    for agent_id in (TL10_AGENT_A, TL10_AGENT_B):
        db = isolated_root / "memory" / agent_id / "graph.sqlite"
        db.parent.mkdir(parents=True, exist_ok=True)
        from src.memory.sage.graph_store import GraphStore
        GraphStore(db_path=db).close()
    return isolated_root


def _make_relationships_file(
    root: Path, agent_id: str, others: Dict[str, Dict[str, Any]]
) -> None:
    """写 4.2 schema relationships.json (隔离副本, 与 TL-9 同款 fixture 方式)。"""
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
    band: str, tags: List[str], impression: str = "一起在客厅聊过天的灵魂"
) -> Dict[str, Any]:
    """4.2 entry 构造 (objective 全整数; 0 浮点权重, No-Scoring)。"""
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
        "impression_tags": tags,
        "relational_band": band,
        "band_updated_at": None,
        "last_relation_update_ref": None,
    }


def _ensure_relationships_manager(root: Path) -> None:
    """真实 manager 读侧就绪: 写文件后重建进程级单例 (读新 fixture)。

    重建单例后触发真实读侧 get_store(agent_id) 惰性加载 (0 写, 只读)。
    """
    from src.soul.relationships import get_relationships_manager
    import src.soul.relationships as rel_mod
    rel_mod._manager_singleton = None  # type: ignore[attr-defined]
    manager = get_relationships_manager()
    store = manager.get_store(TL10_AGENT_A)
    assert store is not None, "real manager must resolve agent store"


def _format_rel_block(agent_id: str, target: str) -> str:
    """真实 helper 调用 (其内部 lazy import relationships manager)。"""
    from src.llm.proxy import _format_relational_perception_block
    return _format_relational_perception_block(agent_id, target)


class _StubMemory:
    """组装的确定性内存 stub (真实 _build_messages_* 唯一用到的两个接口)。

    0 业务模拟: 只提供真实组装函数要求的空历史接口。
    """

    def get_group_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return []

    def get_recent_with_meta(
        self, session_key: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        return []


def _assemble_group(
    agent_id: str, motive_target: Optional[str] = None
) -> List[Dict[str, str]]:
    """真实 A2A 组裝 _build_messages_group (motive_target 可选参数)。"""
    from src.llm.proxy import _build_messages_group
    return _build_messages_group(
        agent_id=agent_id,
        soul="TL-10 stub soul: 你是客厅里的灵魂, 自然的和人相处。",
        current_input="",
        memory_context="",
        memory=_StubMemory(),
        mood=0.0,
        user_id="bryan",
        current_time="",
        motive_target=motive_target,
    )


def _assemble_private(
    agent_id: str, motive_target: Optional[str] = None
) -> List[Dict[str, str]]:
    """真实 A2U 组裝 _build_messages_private (motive_target 可选参数)。"""
    from src.llm.proxy import _build_messages_private
    return _build_messages_private(
        agent_id=agent_id,
        soul="TL-10 stub soul: 你是客厅里的灵魂, 自然的和人相处。",
        current_input="",
        memory_context="",
        memory=_StubMemory(),
        mood=0.0,
        user_id="bryan",
        current_time="",
        reason="user_message",
        motive_target=motive_target,
    )


def _system_text(messages: List[Dict[str, str]]) -> str:
    """提取组装结果的 system 文本 (块存在性断言用)。"""
    return "\n".join(
        m.get("content", "") for m in messages if m.get("role") == "system"
    )


def _estimate_tokens(text: str) -> int:
    """契约 §4.2 估算: 字符数 / 2 向上取整 (近似 tokenizer 断言用)。"""
    return (len(text) + 1) // 2


# ───────────────────────────────────────────────────────────
# run_server 真实模块载入 (resolve_proactive_delivery)
# ───────────────────────────────────────────────────────────

_RUN_SERVER_CACHE: Dict[str, Any] = {}


def _load_run_server_module() -> Any:
    """importlib 载入 scripts/run_server.py (真实模块, 缓存单例)。

    启动在 `if __name__ == "__main__"` 保护内 → import 不启动 server。
    模块级 side-effect 仅: data_root()/faulthandler.log 打开 (隔离 root,
    因调用方保证 SOUL_OS_DATA_DIR 已指向隔离目录) + logging/dotenv 配置;
    载入后立即 cancel 60s dump 定时器 → 0 持久 side-effect。
    """
    name = "soul_os_run_server_tl10"
    cached = _RUN_SERVER_CACHE.get(name)
    if cached is not None:
        return cached
    import faulthandler
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / "run_server.py"
    )
    assert spec is not None and spec.loader is not None, "run_server spec 解析失败"
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:  # noqa: BLE001 — 清理性调用, 失败不影响
        pass
    _RUN_SERVER_CACHE[name] = mod
    return mod


def resolve_proactive_delivery(motive_target: Any) -> Dict[str, Any]:
    """真实 P1 分流判定 (scripts/run_server.resolve_proactive_delivery)。"""
    return _load_run_server_module().resolve_proactive_delivery(motive_target)


# ───────────────────────────────────────────────────────────
# TL10Runner — 四剧本验证编排器
# ───────────────────────────────────────────────────────────

class TL10Runner:
    """TL-10 关系表达端到端验证编排器 (C-3.1 验收钢印)。"""

    def __init__(
        self,
        repo_root: Path,
        seed: int = TL10_SEED,
        experiment_id: str = TL10_EXPERIMENT_ID,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id

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
        harness_root = self._repo_root / "data" / "time_lapse" / self._experiment_id
        run_dir = harness_root / scenario / run_id
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        isolated_root = _prepare_isolated_root(run_dir)

        if scenario == SCENARIO_A2A_ROUTING:
            derived = self._run_a2a_routing(isolated_root)
        elif scenario == SCENARIO_BAND_INJECTION:
            derived = self._run_band_injection(isolated_root)
        elif scenario == SCENARIO_A2U_PRESERVE:
            derived = self._run_a2u_preserve(isolated_root)
        else:
            derived = self._run_fail_safe(isolated_root)

        with open(run_dir / "derived.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(derived), ensure_ascii=False, indent=2) + "\n")
        return {
            "scenario": scenario,
            "run_id": run_id,
            "run_dir": str(run_dir),
            "derived": derived,
        }

    # ── 剧本 1: A2A 客厅公开分流实证 (P1 闭环) ─────────────

    def _run_a2a_routing(self, root: Path) -> TL10ScenarioDerived:
        """scheduler transmit 记录 → extra["motive_target"] → P1 分流全链。"""
        a, b = TL10_AGENT_A, TL10_AGENT_B
        checks: Dict[str, bool] = {}

        # SG-2 注册表: agent_b 在 target 值域内
        from src.soul.motive import get_agent_ids, set_agent_ids
        set_agent_ids([b])
        checks["registry_has_b"] = b in get_agent_ids()

        # fixture: A 对 B 的 4.2 entry (stranger 底档; P1 分流与 band 无关)
        _make_relationships_file(root, a, {b: _rel_entry("stranger", [])})
        _make_relationships_file(root, b, {})
        _ensure_relationships_manager(root)

        # ── 全链: scheduler transmit 记录 → extra → _fire_intent 透传 ──
        chain = _run_bus_scenario(agent_a=a, target=b)
        checks["chain_decision_ok"] = chain["decided"] is True
        checks["chain_last_target_recorded"] = (
            chain["last_target_after_decision"] == b
        )
        trigger0 = chain["trigger_events"][0] if chain["trigger_events"] else None
        extra0 = (trigger0.payload or {}).get("extra", {}) if trigger0 else {}
        checks["chain_extra_has_target"] = (
            bool(trigger0) and extra0.get("motive_target") == b
        )
        checks["chain_single_consume"] = (
            chain["after_first_publish"] is None
            and len(chain["trigger_events"]) >= 2
            and all(
                (e.payload or {}).get("extra", {}).get("motive_target") is None
                for e in chain["trigger_events"][1:]
            )
        )
        intent0 = chain["intent_events"][0] if chain["intent_events"] else None
        checks["chain_intent_passthrough"] = bool(
            intent0 and intent0.payload.get("motive_target") == b
        )
        checks["chain_intent_meta"] = bool(
            intent0
            and intent0.payload.get("agent_id") == a
            and intent0.payload.get("mode") == "group"
        )

        # ── P1 分流: agent-target → group 公开语义, 0 穿透 Bryan 私聊 ──
        delivery = resolve_proactive_delivery(b)
        checks["delivery_group"] = delivery == {
            "mode": "group", "target_channel": None, "target_user_id": None,
        }
        checks["delivery_not_private"] = (
            delivery.get("mode") != "private"
            and delivery.get("target_user_id") is None
        )

        passed = all(checks.values())
        derived = TL10ScenarioDerived(
            scenario=SCENARIO_A2A_ROUTING,
            passed=passed,
            checks=checks,
            key_numbers={
                "delivery": delivery,
                "extra_motive_target": extra0.get("motive_target"),
                "intent_motive_target": (
                    intent0.payload.get("motive_target") if intent0 else None
                ),
                "trigger_event_count": len(chain["trigger_events"]),
                "decision": "transmit" if chain["decided"] else None,
            },
            summary=(
                f"scheduler transmit 记录 → extra 透传 = "
                f"{'PASS' if checks['chain_extra_has_target'] else 'FAIL'}; "
                f"resolve_proactive_delivery({b!r}) → mode="
                f"{delivery.get('mode')} "
                f"(公开语义={'PASS' if checks['delivery_group'] else 'FAIL'}, "
                f"0 穿透 Bryan 私聊={'PASS' if checks['delivery_not_private'] else 'FAIL'})"
            ),
        )
        return derived

    # ── 剧本 2: 关系带差异化注入实证 (Prompt 感知核验) ────

    def _run_band_injection(self, root: Path) -> TL10ScenarioDerived:
        """stranger / familiar 两带 → 真实 helper + 真实组裝注入; token 预算。"""
        a, b = TL10_AGENT_A, TL10_AGENT_B
        checks: Dict[str, bool] = {}

        # ── 阶段 1: stranger 关系带 (陌生客氣) ──
        _make_relationships_file(root, a, {
            b: _rel_entry("stranger", []),
        })
        _make_relationships_file(root, b, {})
        _ensure_relationships_manager(root)
        block_stranger = _format_rel_block(a, b)
        checks["stranger_injected"] = block_stranger != ""
        checks["stranger_has_header"] = "[關係感知]" in block_stranger
        checks["stranger_label_verbatim"] = (
            f"- 對 {b} 的關係帶：{BAND_LABELS['stranger']}" in block_stranger
        )
        checks["stranger_no_tags_line"] = "印象：" not in block_stranger
        checks["stranger_token_budget"] = _estimate_tokens(block_stranger) <= 80

        # 组裝层 (A2A 路径): motive_target 传入 → 块注入存在
        msgs_inject = _assemble_group(a, motive_target=b)
        checks["group_inject_present"] = "[關係感知]" in _system_text(msgs_inject)
        checks["group_inject_label"] = (
            f"- 對 {b} 的關係帶：{BAND_LABELS['stranger']}" in _system_text(msgs_inject)
        )

        # ── 阶段 2: familiar 关系带 (熟稔親近) + impression_tags ──
        tags = ["開朗", "喜歡音樂"]
        _make_relationships_file(root, a, {
            b: _rel_entry("familiar", tags),
        })
        _make_relationships_file(root, b, {})
        _ensure_relationships_manager(root)
        block_familiar = _format_rel_block(a, b)
        checks["familiar_injected"] = block_familiar != ""
        checks["familiar_label_verbatim"] = (
            f"- 對 {b} 的關係帶：{BAND_LABELS['familiar']}" in block_familiar
        )
        checks["tags_line_verbatim"] = f"- 印象：{'、'.join(tags)}" in block_familiar
        checks["familiar_token_budget"] = _estimate_tokens(block_familiar) <= 80

        # ── 阶段 3: 契约 §4.2 最坏情况 (5 个长 tag → 截断) ──
        _make_relationships_file(root, a, {
            b: _rel_entry("close", WORST_CASE_TAGS + WORST_CASE_TAGS_EXTRA),
        })
        _make_relationships_file(root, b, {})
        _ensure_relationships_manager(root)
        block_worst = _format_rel_block(a, b)
        worst_tags_line = [
            ln for ln in block_worst.splitlines() if "印象：" in ln
        ]
        rendered = worst_tags_line[0] if worst_tags_line else ""
        checks["worst_case_budget"] = _estimate_tokens(block_worst) <= 80
        checks["worst_case_chars_budget"] = len(block_worst) <= 160
        checks["worst_case_tag_cap5"] = (
            "、" in rendered and rendered.count("、") == 4
        )
        checks["worst_case_tag_trim"] = (
            "第六個標籤不該出現" not in rendered
        )

        # ── 阶段 4: 组裝层 None 向後兼容 (无 motive_target → 0 注入) ──
        msgs_none = _assemble_group(a)  # 不传键 (签名默认 None)
        msgs_none_explicit = _assemble_group(a, motive_target=None)
        checks["group_none_no_inject"] = "[關係感知]" not in _system_text(msgs_none)
        checks["group_none_byte_identical"] = msgs_none == msgs_none_explicit

        passed = all(checks.values())
        derived = TL10ScenarioDerived(
            scenario=SCENARIO_BAND_INJECTION,
            passed=passed,
            checks=checks,
            key_numbers={
                "stranger_block": block_stranger,
                "familiar_block": block_familiar,
                "worst_case_block_len": len(block_worst),
                "worst_case_tokens_est": _estimate_tokens(block_worst),
                "worst_case_tags_rendered": rendered,
            },
            summary=(
                f"stranger 注入={'PASS' if checks['stranger_injected'] else 'FAIL'} "
                f"(標籤「{BAND_LABELS['stranger']}」逐字="
                f"{checks['stranger_label_verbatim']}); "
                f"familiar 標籤「{BAND_LABELS['familiar']}」逐字="
                f"{checks['familiar_label_verbatim']}; "
                f"tags 行={checks['tags_line_verbatim']}; "
                f"最坏 case token 估算={_estimate_tokens(block_worst)} ≤ 80 = "
                f"{checks['worst_case_budget']}"
            ),
        )
        return derived

    # ── 剧本 3: A2U 私聊保全实证 (Bryan 通道零退化) ────────

    def _run_a2u_preserve(self, root: Path) -> TL10ScenarioDerived:
        """target == "user_bryan" 与 "bryan" 各测: 归一化 + private 原状。"""
        a = TL10_AGENT_A
        checks: Dict[str, bool] = {}

        # fixture: A 对 Bry (user_bryan) 的 4.2 entry (familiar + tags)
        _make_relationships_file(root, a, {
            "user_bryan": _rel_entry("familiar", ["默契"]),
        })
        _ensure_relationships_manager(root)

        # ── P1: bryan 双形归一化, 100% private 原状 ──
        d_bryan = resolve_proactive_delivery("bryan")
        d_user = resolve_proactive_delivery("user_bryan")
        checks["norm_bryan_private"] = d_bryan == PRIVATE_DELIVERY
        checks["norm_user_bryan_private"] = d_user == PRIVATE_DELIVERY
        checks["norm_consistent"] = d_bryan == d_user
        checks["private_100_preserved"] = (
            d_bryan.get("mode") == "private"
            and d_bryan.get("target_channel") == "telegram"
            and d_bryan.get("target_user_id") == BRYAN_CHAT_ID
        )

        # ── 归一化注入 (契约 §2.4: "bryan" → relationships key "user_bryan") ──
        block_via_bryan = _format_rel_block(a, "bryan")
        block_via_user = _format_rel_block(a, "user_bryan")
        checks["helper_normalize_hits_user_bryan"] = (
            block_via_bryan != ""
            and f"- 對 bryan 的關係帶：{BAND_LABELS['familiar']}" in block_via_bryan
        )
        checks["helper_direct_key"] = (
            block_via_user != ""
            and f"- 對 user_bryan 的關係帶：{BAND_LABELS['familiar']}" in block_via_user
        )

        # ── A2U 私有组裝: motive_target 注入正常 ──
        msgs_bryan = _assemble_private(a, motive_target="bryan")
        sys_text = _system_text(msgs_bryan)
        checks["private_inject_present"] = "[關係感知]" in sys_text
        checks["private_inject_label"] = (
            f"- 對 bryan 的關係帶：{BAND_LABELS['familiar']}" in sys_text
        )
        checks["private_inject_tags"] = "- 印象：默契" in sys_text

        # ── None 向後兼容 (无 motive_target 行为逐字节不变) ──
        msgs_none = _assemble_private(a)
        msgs_none_explicit = _assemble_private(a, motive_target=None)
        checks["private_none_no_inject"] = "[關係感知]" not in _system_text(msgs_none)
        checks["private_none_byte_identical"] = msgs_none == msgs_none_explicit

        # ── scheduler 全链 (bryan target) → extra → P1 仍 private ──
        # bryan 不在 agent 注册表 (Bryan 通道): make_motive("bryan") 恒合法
        from src.soul.motive import set_agent_ids
        set_agent_ids([])
        chain = _run_bus_scenario(agent_a=a, target="bryan")
        checks["chain_bryan_decided"] = chain["decided"] is True
        trigger0 = chain["trigger_events"][0] if chain["trigger_events"] else None
        extra0 = (trigger0.payload or {}).get("extra", {}) if trigger0 else {}
        checks["chain_bryan_extra"] = extra0.get("motive_target") == "bryan"
        intent0 = chain["intent_events"][0] if chain["intent_events"] else None
        checks["chain_bryan_intent"] = bool(
            intent0 and intent0.payload.get("motive_target") == "bryan"
        )
        d_chain = resolve_proactive_delivery(extra0.get("motive_target"))
        checks["chain_bryan_delivery_private"] = d_chain == PRIVATE_DELIVERY

        passed = all(checks.values())
        derived = TL10ScenarioDerived(
            scenario=SCENARIO_A2U_PRESERVE,
            passed=passed,
            checks=checks,
            key_numbers={
                "delivery_bryan": d_bryan,
                "delivery_user_bryan": d_user,
                "helper_block_via_bryan": block_via_bryan,
                "chain_delivery": d_chain,
            },
            summary=(
                f"归一化 ('bryan'→user_bryan entry) = "
                f"{'PASS' if checks['helper_normalize_hits_user_bryan'] else 'FAIL'}; "
                f"resolve('bryan')/{'user_bryan'} → "
                f"mode={d_bryan.get('mode')} channel="
                f"{d_bryan.get('target_channel')} uid="
                f"{d_bryan.get('target_user_id')} (100% 原状="
                f"{checks['private_100_preserved']}); "
                f"None 逐字节兼容={checks['private_none_byte_identical']}"
            ),
        )
        return derived

    # ── 剧本 4: 三重 Fail-Safe 容错复核 ───────────────────

    def _run_fail_safe(self, root: Path) -> TL10ScenarioDerived:
        """①无记录 ②非法 target ③读取异常; resolve 未知 target fail-safe。"""
        a, b = TL10_AGENT_A, TL10_AGENT_B
        checks: Dict[str, bool] = {}

        # ── ① 无关系记录 (entry 缺失) ──
        _make_relationships_file(root, a, {})  # 有文件但 0 others
        _make_relationships_file(root, b, {})
        _ensure_relationships_manager(root)
        checks["no_entry_blank"] = _format_rel_block(a, b) == ""

        # ── ② 非合法 target (None / 空 / 莫名字串) ──
        checks["target_none_blank"] = _format_rel_block(a, None) == ""
        checks["target_empty_blank"] = _format_rel_block(a, "") == ""
        checks["target_weird_blank"] = _format_rel_block(a, "agent_unknown") == ""

        # ── ③ 读取异常 (mock 抛异常 → 真实 helper try/except → "") ──
        from unittest import mock
        boom = RuntimeError("TL-10 simulated relationships read failure")
        with mock.patch(
            "src.soul.relationships.get_relationships_manager",
            side_effect=boom,
        ):
            checks["read_exception_blank"] = _format_rel_block(a, b) == ""
        checks["read_exception_no_raise"] = True  # 上面调用未抛出即成立

        # ── P1: 未知 target fail-safe (默认私聊, 不报错) ──
        from src.soul.motive import set_agent_ids
        set_agent_ids([])  # 注册表清空: agent-target 也不再命中 group
        failsafe_cases = {
            "delivery_failsafe_weird": "agent_unknown",
            "delivery_failsafe_none": None,
            "delivery_failsafe_empty": "",
            "delivery_failsafe_unregistered": b,
        }
        for check_key, t in failsafe_cases.items():
            checks[check_key] = resolve_proactive_delivery(t) == PRIVATE_DELIVERY

        # ── 全剧本 0 崩溃: 上述真实调用无一抛出未捕获异常 ──
        checks["zero_uncaught"] = True

        passed = all(checks.values())
        derived = TL10ScenarioDerived(
            scenario=SCENARIO_FAIL_SAFE,
            passed=passed,
            checks=checks,
            key_numbers={
                "no_entry_result": _format_rel_block(a, b),
                "weird_target_result": _format_rel_block(a, "agent_unknown"),
                "failsafe_delivery": PRIVATE_DELIVERY,
            },
            summary=(
                f"①无记录 → {''!r} = {checks['no_entry_blank']}; "
                f"②非法 target → \"\" = "
                f"{checks['target_none_blank'] and checks['target_empty_blank'] and checks['target_weird_blank']}; "
                f"③读取异常 → \"\" = {checks['read_exception_blank']}; "
                f"resolve 未知 fail-safe private = "
                f"{checks['delivery_failsafe_none']}"
            ),
        )
        return derived

    # ── run 系列 (D2 determinism + 0 mutation) ─────────────

    def run_series(
        self,
        scenarios: Optional[tuple[str, ...]] = None,
        n_runs: int = 3,
    ) -> Dict[str, Any]:
        """四剧本各连跑 n_runs 次 (D2 宏确定性) + production 0 mutation 验证。"""
        scenarios = scenarios or SCENARIOS
        production_root = self._repo_root / "data"
        from .runner import snapshot_data_root_hashes, verify_zero_mutation
        before = snapshot_data_root_hashes(production_root)

        series: List[TL10SeriesMetrics] = []
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
            series.append(TL10SeriesMetrics(
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
    "scenario", "passed", "summary",
)


def _scenario_determinism(runs: List[Dict[str, Any]]) -> bool:
    """同一剧本 3 个 run 的派生判定字段完全一致 (MoE 特性下宏确定性)。"""
    if not runs:
        return False
    ref = asdict(runs[0]["derived"])
    for run in runs[1:]:
        cur = asdict(run["derived"])
        if len(cur) != len(ref):
            return False
        # 只比判定字段 (key_numbers 内 uuid 等运行产物不参与)
        for key in _DETERMINISM_FIELDS:
            if cur.get(key) != ref.get(key):
                return False
        if cur.get("checks") != ref.get("checks"):
            return False
    return True


__all__ = [
    "TL10_EXPERIMENT_ID",
    "TL10_AGENT_A",
    "TL10_AGENT_B",
    "SCENARIOS",
    "SCENARIO_LABELS",
    "BAND_LABELS",
    "PRIVATE_DELIVERY",
    "TL10ScenarioDerived",
    "TL10Runner",
    "resolve_proactive_delivery",
]