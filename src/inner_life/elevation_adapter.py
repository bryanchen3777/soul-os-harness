"""
src/inner_life/elevation_adapter.py — Soul OS ↔ soul-elevation adapter seam

本模块是 Soul OS 侧**唯一**接触 `soul_elevation` 的地方。它把上游只读数据
（`InnerLifeEvent` + v1 `Memory` + SAGE `Fact`）映射成 soul-elevation 的
`ElevationInput`，喂给 `InternalizingEngine`，产出升华节点（belief / value /
trait / essence），并把结果写入 soul-elevation 自有 store（`data/elevation/`）。

定位（照 docs/MEMORY-ELEVATION-DESIGN.md §2）：
  - **只读消费者 / 旁路观察者**：adapter 只读 InnerLifeEvent + Memory，
    **不调用** ``InnerLifeWriter.create_event()``（不是第 6 个 producer）。
  - **不改任何 frozen contract**：不改 InnerLifeEvent schema（M5.4-5.1）、
    TriggerEnvelope（M5.2-F）、Agency 4 stages / 4 handlers、SAGE 写入逻辑、
    InnerLifeWriter、NarrativeTrace。
  - **只写自有 store**：写 ``data/elevation/``（elevation_trace.jsonl +
    elevation_nodes.jsonl + elevation_edges.jsonl），不碰 memory.db / SAGE
    graph / trace.jsonl。

触发方式（照工单关键决策 #4）：Soul OS 在 InnerLifeEvent 写入**之后**调用
``run_elevation(inner_life_event, memory_facts)``（或经 ``ElevationObserver``），
adapter 接在写路径之后，是 fire-and-forget，失败隔离不阻断写路径。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional, Sequence

from soul_elevation import (
    DEFAULT_ELEVATE_MIN_EVIDENCE,
    ElevationInput,
    ElevationNode,
    ElevationTraceWriter,
    EvidenceEdge,
    InternalizingEngine,
    SOUL_NODE_TYPES,
    StubElevationLLM,
)

if TYPE_CHECKING:  # 仅类型标注，运行时不执行（避免拉入 networkx 等重依赖链）
    from src.memory.sage.models import Fact
    from src.memory.v1.schema import Memory

    from .event import InnerLifeEvent

logger = logging.getLogger("soul_os.inner_life.elevation_adapter")

# 默认 store 目录名（挂在 data_root() 下）。
ELEVATION_DIR_NAME = "elevation"

# 三个自有 store 文件名（独立于上游 store，不碰 trace.jsonl / memory.db / SAGE graph）。
TRACE_FILENAME = "elevation_trace.jsonl"
NODES_FILENAME = "elevation_nodes.jsonl"
EDGES_FILENAME = "elevation_edges.jsonl"

# 事件 / memory / fact 归一化为 ElevationInput 时的统一 event_type（走 prior.py 的
# CATEGORY_TRIGGER_TYPES 分支，依 provenance 里的 category 决定先验维度）。
_EVENT_TYPE_MEMORY_FACT = "memory_fact"


def _utcnow_iso() -> str:
    """当前 UTC 时刻的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _ts_to_iso(ts: Any) -> str:
    """把 unix 时间戳（float/int）转 ISO 8601 UTC 字符串。

    v1 Memory.created_at / SAGE Fact.timestamp 都是 unix float；ElevationInput
    的 timestamp 约定是 ISO 8601 UTC，故在此归一化。
    """
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _event_content(inner_life_event: Any) -> str:
    """InnerLifeEvent 的「事件内容」。

    InnerLifeEvent 是 identity + lineage + provenance，**没有正文文本**（正文留在
    Memory / Fact 里）。因此事件自身的「内容」取其最忠实的可表达形式：
    trigger_type + provenance.extras（确定性、无损于可表达的元数据）。
    """
    p = inner_life_event.provenance
    if p.extras:
        parts = [f"{k}={v}" for k, v in sorted(p.extras.items())]
        return f"{p.trigger_type}: " + "; ".join(parts)
    return p.trigger_type


def _fact_content(fact: Any) -> str:
    """SAGE Fact 的「内容」：subject predicate object 三元组拼成一句。"""
    return f"{fact.subject} {fact.predicate} {fact.object}"


def _fact_agent(fact: Any) -> Optional[str]:
    """从 SAGE Fact.source_pair（"<user_id>:<agent_id>"）抽取 agent_id。"""
    sp = getattr(fact, "source_pair", None)
    if isinstance(sp, str) and ":" in sp:
        return sp.split(":", 1)[1]
    return None


# ─────────────────────────────────────────────────────────────────────
# 映射（InnerLifeEvent / Memory / Fact → ElevationInput）
# ─────────────────────────────────────────────────────────────────────


def inner_life_event_to_input(
    inner_life_event: Any, *, agent_id: Optional[str] = None
) -> ElevationInput:
    """把 ``InnerLifeEvent`` 映射成 ``ElevationInput``（source_type="inner_life_event"）。

    字段映射（照工单关键决策 #3）：
      - event_type  = provenance.trigger_type
      - content     = trigger_type + provenance.extras（事件自身可表达的内容）
      - source_id   = event_id
      - source_type = "inner_life_event"
      - timestamp   = event.ts（已是 ISO 8601 UTC）
      - provenance  = dict（trigger_type / source_system / actor_id / agent_id +
        extras / identity 元数据）
    """
    p = inner_life_event.provenance
    provenance: dict[str, Any] = {
        "trigger_type": p.trigger_type,
        "source_system": p.source_system,
        "actor_id": p.actor_id,
    }
    # extras 是 canonical 元数据（world_type / world_source / world_novelty_id /
    # qualification_reason 等），原样并入 provenance，供 prior / LLM 后验读取。
    provenance.update(dict(getattr(p, "extras", {}) or {}))

    # 归属 agent（灵魂本体）。显式传入 > extras.agent_id > actor_id（若形似 agent）。
    resolved_agent = agent_id or provenance.get("agent_id") or p.actor_id
    if resolved_agent:
        provenance["agent_id"] = resolved_agent

    # 身份/lineage 元数据（join 回 trace 的审计锚点）。
    provenance["inner_life_event_id"] = inner_life_event.event_id
    provenance["correlation_id"] = inner_life_event.correlation_id
    provenance["session_id"] = inner_life_event.session_id
    provenance["parent_event_id"] = inner_life_event.parent_event_id
    provenance["source_world_event_novelty_id"] = (
        inner_life_event.source_world_event_novelty_id
    )

    return ElevationInput(
        event_type=p.trigger_type,
        content=_event_content(inner_life_event),
        source_id=inner_life_event.event_id,
        source_type="inner_life_event",
        timestamp=inner_life_event.ts,
        provenance=provenance,
    )


def v1_memory_to_input(memory: Any, *, agent_id: Optional[str] = None) -> ElevationInput:
    """把 v1 ``Memory`` 映射成 ``ElevationInput``（source_type="v1_memory"）。

    字段映射：
      - event_type  = "memory_fact"（走 prior.py 类别分支，依 category 定先验）
      - content     = memory.content（原文）
      - source_id   = memory.memory_id
      - source_type = "v1_memory"
      - timestamp   = memory.created_at（unix float → ISO 8601 UTC）
      - provenance  = dict（agent_id / category / confidence / tags / inner_life_event_id）
    """
    provenance: dict[str, Any] = {
        "agent_id": agent_id or memory.agent_id,
        "category": memory.category,
        "confidence": memory.confidence,
        "tags": list(memory.tags or []),
        "inner_life_event_id": memory.inner_life_event_id,
    }
    return ElevationInput(
        event_type=_EVENT_TYPE_MEMORY_FACT,
        content=memory.content,
        source_id=memory.memory_id,
        source_type="v1_memory",
        timestamp=_ts_to_iso(memory.created_at),
        provenance=provenance,
    )


def sage_fact_to_input(fact: Any, *, agent_id: Optional[str] = None) -> ElevationInput:
    """把 SAGE ``Fact`` 映射成 ``ElevationInput``（source_type="sage_fact"）。

    字段映射：
      - event_type  = "memory_fact"
      - content     = "subject predicate object"（三元组拼句）
      - source_id   = fact.fact_id
      - source_type = "sage_fact"
      - timestamp   = fact.timestamp（unix float → ISO 8601 UTC）
      - provenance  = dict（agent_id / confidence / weight / inner_life_event_id 等）
    """
    provenance: dict[str, Any] = {
        "agent_id": agent_id or _fact_agent(fact),
        "confidence": fact.confidence,
        "weight": fact.weight,
        "source": fact.source,
        "session_id": fact.session_id,
        "inner_life_event_id": fact.inner_life_event_id,
    }
    return ElevationInput(
        event_type=_EVENT_TYPE_MEMORY_FACT,
        content=_fact_content(fact),
        source_id=fact.fact_id,
        source_type="sage_fact",
        timestamp=_ts_to_iso(fact.timestamp),
        provenance=provenance,
    )


def _to_input(obj: Any, *, agent_id: Optional[str] = None) -> ElevationInput:
    """按对象类型分发映射（adapter 内部分发器，供 run_elevation 使用）。

    依据对象是否带 ``event_id``（InnerLifeEvent）、``memory_id``（v1 Memory）、
    ``fact_id``（SAGE Fact）区分，避免在 adapter 里强 import 上游类型。
    """
    if hasattr(obj, "event_id") and hasattr(obj, "provenance"):
        return inner_life_event_to_input(obj, agent_id=agent_id)
    if hasattr(obj, "memory_id") and hasattr(obj, "content"):
        return v1_memory_to_input(obj, agent_id=agent_id)
    if hasattr(obj, "fact_id") and hasattr(obj, "subject"):
        return sage_fact_to_input(obj, agent_id=agent_id)
    raise TypeError(
        f"elevation adapter 无法识别对象类型：{type(obj).__name__!r}；"
        f"预期 InnerLifeEvent / v1 Memory / SAGE Fact"
    )


# ─────────────────────────────────────────────────────────────────────
# 自有 store 写入（data/elevation/，append-only，失败隔离）
# ─────────────────────────────────────────────────────────────────────


def _append_jsonl(path: Path, records: Sequence[dict]) -> bool:
    """append-only 追加 JSONL 行；写失败只告警 + 返回 False，绝不 raise。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except OSError as exc:  # 失败隔离：不阻断升华主路径
        logger.warning("elevation store write failed (%s): %s", path, exc)
        return False


def _persist_result(
    store_dir: Path,
    nodes: Sequence[ElevationNode],
    edges: Sequence[Any],
) -> None:
    """把升华节点 + 证据边持久化到 data/elevation/（自有 store）。

    节点/证据边用 ``dataclasses.asdict`` 序列化为 JSONL，与 soul-elevation 的
    elevation_trace.jsonl（审计 sidecar）并列存放，使「节点 + 证据边」可回查。
    """
    node_records = [asdict(n) for n in nodes]
    edge_records = [asdict(e) for e in edges]
    if node_records:
        _append_jsonl(store_dir / NODES_FILENAME, node_records)
    if edge_records:
        _append_jsonl(store_dir / EDGES_FILENAME, edge_records)


# ─────────────────────────────────────────────────────────────────────
# 触发函数（InnerLifeEvent 写入之后调用）
# ─────────────────────────────────────────────────────────────────────


def run_elevation(
    inner_life_event: Any,
    memory_facts: Sequence[Any] = (),
    *,
    llm: Any = None,
    store_dir: Optional[Any] = None,
    agent_id: Optional[str] = None,
) -> List[ElevationNode]:
    """把 InnerLifeEvent + Memory 喂给 InternalizingEngine，产出升华节点。

    Args:
        inner_life_event: 已写入的 InnerLifeEvent（canonical 触发事件）。
        memory_facts: 同一叙事上下文的 v1 Memory / SAGE Fact 序列（正文来源）。
        llm: 可选 ElevationLLM 实现；缺省用 StubElevationLLM（确定性桩，不硬编码 provider）。
        store_dir: 可选 store 目录（缺省 ``data_root()/elevation``）。
        agent_id: 可选归属 agent（灵魂本体）覆盖。

    Returns:
        产出的 ``ElevationNode`` 列表（信念/价值/性格/内涵）。失败隔离：异常时
        记录 warning 并返回 []（不 raise，不阻断写路径）。
    """
    try:
        resolved_dir = _resolve_store_dir(store_dir)
        # 1) 归一化输入：事件 + 全部记忆 fact。
        inputs: List[ElevationInput] = [_to_input(inner_life_event, agent_id=agent_id)]
        inputs.extend(_to_input(m, agent_id=agent_id) for m in memory_facts)

        # 2) 构造引擎：注入 LLM + soul-elevation 自有 trace writer（写到 data/elevation/）。
        #    EL-OWN-0 传递链：run_elevation(agent_id) → InternalizingEngine(agent_id)，
        #    使 engine.py 从 provenance 取不到 agent_id 时兜底 self._agent_id 也归属到
        #    具体灵魂。仅当 agent_id 为真时才传（world 事件 actor_id=None、不显式归属
        #    → 不传，保持引擎默认 "default"，system-level 语义不变）。
        _engine_kwargs: dict[str, Any] = {
            "llm": llm if llm is not None else StubElevationLLM(),
            "trace_writer": ElevationTraceWriter(str(resolved_dir / TRACE_FILENAME)),
        }
        if agent_id:
            _engine_kwargs["agent_id"] = agent_id
        engine = InternalizingEngine(**_engine_kwargs)

        # 3) 逐条 consume，累积节点。
        nodes: List[ElevationNode] = []
        for inp in inputs:
            nodes.extend(engine.consume(inp))

        # 4) 持久化节点 + 证据边到 data/elevation/（自有 store，失败隔离）。
        _persist_result(resolved_dir, nodes, engine.evidence_edges)

        return nodes
    except Exception as exc:  # noqa: BLE001 — 失败隔离：升华失败不阻断写路径
        logger.warning(
            "run_elevation failed (不影響 InnerLifeEvent 写入主路径): "
            "%s: %s",
            type(exc).__name__,
            exc,
        )
        return []


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析 store 目录：显式传入 > data_root()/elevation。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root

    return data_root() / ELEVATION_DIR_NAME


class ElevationObserver:
    """可接在写路径之后的旁路观察者（触发点接线）。

    用法（InnerLifeEvent 写入之后调用，不改 InnerLifeWriter 写入逻辑）：

        observer = ElevationObserver(...)
        event = writer.create_event(...)       # 写入
        observer.on_event_written(event, memory_facts)  # 写入之后 → 升华

    每次 ``on_event_written`` 内部调 ``run_elevation``（失败隔离，永不 raise）。
    """

    def __init__(
        self,
        *,
        llm: Any = None,
        store_dir: Optional[Any] = None,
        agent_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self._store_dir = store_dir
        self._agent_id = agent_id
        self.enabled = enabled

    def on_event_written(
        self, inner_life_event: Any, memory_facts: Sequence[Any] = ()
    ) -> List[ElevationNode]:
        """InnerLifeEvent 写入之后调用的升华入口（fire-and-forget，失败隔离）。"""
        if not self.enabled:
            return []
        try:
            return run_elevation(
                inner_life_event,
                memory_facts,
                llm=self._llm,
                store_dir=self._store_dir,
                agent_id=self._agent_id,
            )
        except Exception as exc:  # noqa: BLE001 — 双保险，绝不阻断调用方
            logger.warning("ElevationObserver.on_event_written failed: %s", exc)
            return []


# ─────────────────────────────────────────────────────────────────────
# Elevate 调用机制（证据驱动，独立于 Submission Gate）
#
# 工单「打通 diary/dream → elevation」关键决策 #2：
#   - **Submission Gate 只 consume**（产 pattern 候选），**永不 elevate()**
#     （submission_gate.py 的 AST 红线保持不变）。elevate 是独立于 Gate 的
#     机制：本函数在 consume 落盘**之后**被调用（fire-and-forget，失败隔离）。
#   - **证据驱动**：读 ``data/elevation/`` 已持久化的 pattern + 证据边，按
#     agent（灵魂本体）重建 ``InternalizingEngine`` 注册表，对「独立证据累积
#     ≥ min_evidence」的候选维度组调 ``engine.elevate()``，pattern →
#     belief/value/trait/essence（升华维度由 prior 表 / LLM 后验候选决定，
#     即 soul-elevation 的 ``candidate_node_type``）。
#   - **不提前**：独立证据 < min_evidence 的候选维度组跳过（elevate 内部
#     ValueError insufficient 捕获，不硬触发）。
#   - **不重复消化**（anti-runaway）：已被 soul node 证据边引用的
#     (source_id, event_identity) 键不再计票——同一批证据不能支持第二颗
#     灵魂结构。
#   - **agent 隔离**：只聚合同一 agent（灵魂本体）的 pattern 计票；
#     ``world`` 事件（agent_id="default"）自成一局，不影响具体灵魂计票。
# ─────────────────────────────────────────────────────────────────────


def _load_edges(store_dir: Path) -> List[dict]:
    """读 ``elevation_edges.jsonl``（append-only JSONL），返回原始 dict 列表。

    失败隔离：文件不存在 / 读失败 / 单行解析失败 → 记录并跳过该行，
    **绝不 raise**（不阻塞 elevate 主路径）。
    """
    path = store_dir / EDGES_FILENAME
    edges: List[dict] = []
    if not path.exists():
        return edges
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(
                        "elevation edges line skipped (bad json): %r", line[:80]
                    )
                    continue
                if isinstance(record, dict):
                    edges.append(record)
    except OSError as exc:
        logger.warning("elevation edges read failed (%s): %s", path, exc)
        return []
    return edges


def _rebuild_engine_for(
    store_dir: Path,
    nodes: Sequence[dict],
    edges: Sequence[dict],
    *,
    agent_id: str,
    llm: Any,
) -> InternalizingEngine:
    """为该 agent 重建 ``InternalizingEngine`` 注册表（只加载该 agent 的数据）。

    - **节点**：该 agent 的全部节点都加载（含已被消化的 pattern），保证
      lineage 完整性（``check_invariants`` 要求 ``parent_node_id`` 在注册表内）。
    - **证据边**：只加载「未被 soul node 证据边覆盖的 (source_id, event_identity)
      键」的 active pattern 边——已被 elevate 消化的证据不再计票（同一批证据
      不能支持第二颗灵魂结构，anti-runaway）。soul node 自身的边不加载
      （elevate 只聚合 pattern 证据，无需）。
    """
    engine = InternalizingEngine(
        llm=llm,
        agent_id=agent_id,
        trace_writer=ElevationTraceWriter(str(store_dir / TRACE_FILENAME)),
    )
    soul_node_ids = {
        n["node_id"] for n in nodes if n.get("node_type") in SOUL_NODE_TYPES
    }
    consumed_keys = {
        (e["source_id"], e.get("inner_life_event_id"))
        for e in edges
        if e.get("node_id") in soul_node_ids and e.get("agent_id") == agent_id
    }
    for n in nodes:
        if n.get("agent_id") != agent_id:
            continue
        engine._nodes[n["node_id"]] = ElevationNode(**n)
    for e in edges:
        if e.get("agent_id") != agent_id:
            continue
        if e.get("node_id") in soul_node_ids:
            continue
        if e.get("valid_until_ts") is not None:  # superseded 留痕不计票
            continue
        if (e.get("source_id"), e.get("inner_life_event_id")) in consumed_keys:
            continue
        engine._edges.append(EvidenceEdge(**e))
    return engine


def elevate_matured_patterns(
    *,
    store_dir: Optional[Any] = None,
    llm: Any = None,
    min_evidence: int = DEFAULT_ELEVATE_MIN_EVIDENCE,
) -> List[ElevationNode]:
    """证据驱动的 elevate（独立于 Submission Gate 的机制）。

    读 ``data/elevation/`` 已持久化的 pattern + 证据边，按 agent（灵魂本体）
    分组重建引擎注册表，对「独立证据累积 ≥ min_evidence」的候选维度组调
    ``engine.elevate()``：pattern → belief/value/trait/essence（升华维度由
    prior 表 / LLM 后验候选决定）。新灵魂节点 + 新证据边 append 回自有
    store（``elevation_nodes.jsonl`` / ``elevation_edges.jsonl``）。

    Args:
        store_dir: 可选 store 目录（缺省 ``data_root()/elevation``）。
        llm: 可选 ElevationLLM 实现（缺省用 StubElevationLLM，确定性桩；
            elevate 本身不调 LLM，仅在重建注册表时透传给引擎构造）。
        min_evidence: 独立证据阈值（对齐 soul-elevation 默认 2 独立证据）。

    Returns:
        本次新产出的灵魂节点（belief/value/trait/essence）列表。失败隔离：
        异常时记录 warning 并返回 []（不 raise，不阻断 consume 主路径）。
    """
    if not isinstance(min_evidence, int) or min_evidence < 1:
        raise ValueError(f"min_evidence must be a positive int, got {min_evidence!r}")

    resolved_dir = _resolve_store_dir(store_dir)
    try:
        from .emergent_projection import load_elevation_nodes

        nodes = load_elevation_nodes(resolved_dir)
        edges = _load_edges(resolved_dir)
        if not nodes:
            return []
        agent_ids = sorted({n.get("agent_id", "default") for n in nodes})

        elevated: List[ElevationNode] = []
        for agent_id in agent_ids:
            engine = _rebuild_engine_for(
                resolved_dir,
                nodes,
                edges,
                agent_id=agent_id,
                llm=llm if llm is not None else StubElevationLLM(),
            )
            # 候选维度分组（保持创建顺序），每组取第一个 pattern 为升华锚点
            # （elevate 内部聚合同候选维度的全部 pattern 有效证据边）。
            candidates: dict[str, list[str]] = {}
            for nid, node in engine._nodes.items():
                if node.node_type != "pattern":
                    continue
                candidates.setdefault(node.candidate_node_type, []).append(nid)

            baseline_edges = len(engine._edges)
            agent_elevated: List[ElevationNode] = []
            for cand, pids in candidates.items():
                if not pids:
                    continue
                try:
                    soul = engine.elevate(pids[0], min_evidence=min_evidence)
                    agent_elevated.append(soul)
                    elevated.append(soul)
                    logger.info(
                        f"[elevate] ✓ agent={agent_id} candidate={cand} "
                        f"patterns={len(pids)} → {soul.node_type} "
                        f"(min_evidence={min_evidence})"
                    )
                except ValueError as exc:
                    # 独立证据不足 → 不升（不提前）。这是常态（多数组未达阈值）。
                    logger.info(
                        f"[elevate] skip agent={agent_id} candidate={cand}: {exc}"
                    )
                except Exception as exc:  # noqa: BLE001 — 失败隔离
                    logger.warning(
                        f"[elevate] failed agent={agent_id} candidate={cand}: "
                        f"{type(exc).__name__}: {exc}"
                    )
            # 只 append 本次新增的边（加载的历史边不重复写，supersede 原地改不动文件）。
            _persist_result(
                resolved_dir, agent_elevated, engine.evidence_edges[baseline_edges:]
            )
        return elevated
    except Exception as exc:  # noqa: BLE001 — 失败隔离：不影响 consume 主路径
        logger.warning(
            "elevate_matured_patterns failed (不影響主路徑): %s: %s",
            type(exc).__name__,
            exc,
        )
        return []


__all__ = [
    "DEFAULT_ELEVATE_MIN_EVIDENCE",
    "ELEVATION_DIR_NAME",
    "ElevationInput",
    "ElevationNode",
    "ElevationObserver",
    "elevate_matured_patterns",
    "inner_life_event_to_input",
    "v1_memory_to_input",
    "sage_fact_to_input",
    "run_elevation",
]
