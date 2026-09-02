"""
src/inner_life/emergent_projection.py — 灵魂成长闭环 read-side projection

定位（照工单「灵魂成长闭环实现（Emergent read-side projection）」+ docs/ 方向）：
  - 这是「闭环」的最后一环：把该灵魂自己的、已 ``elevate()`` 接受的
    belief / value / trait / essence 投影到 prompt（EMERGENT block），
    让 emergent 属性在 inference-time 影响 interpretation。
  - **只读、inference-time**：读 ``data/elevation/elevation_nodes.jsonl``，
    **不重跑 elevate、不写 elevation 节点/证据数据**（Growth read 是 projection，
    Growth write 只由既有 Elevation 决定）。
  - **独立于 identity**：IDENTITY = 从哪开始（seeded 人格 / germ anchor）；
    EMERGENT = 成为什么（自己长出来的人格投影）。seeded 灵魂的 emergent
    属性叠加在 seeded 人格上；germ 灵魂的 emergent 投影就是人格。

v1 投影规则（死规则，工单已锁）：
  - 只投影该灵魂自己的节点：``agent_id == 该灵魂``。
  - 只投影 ``node_type in {belief, value, trait, essence}``（对齐 soul-elevation
    的 SOUL_NODE_TYPES）；**pattern 不投影**（注意到 ≠ 成为谁，Pattern 是另一套）。
  - ``agent_id == "default"`` 的 world node 不投影（agent_id 过滤天然排除）。
  - **SE-5 状态守门**：按 ``lifecycle_state`` 过滤——ACTIVE 正常投影；
    WEAKENING 投影但带不确定性语气（「我隐约觉得…」）；DORMANT / SUPERSEDED
    **不主动投影**（v1 不做「历史回忆检索」，留后续工单）；缺省视为 ACTIVE
    （additive schema，旧数据兼容）。
  - **Lineage 降维**：新节点 B 的直接父（``parent_node_id``）是 SUPERSEDED
    旧节点 A 时，只投影 B 并合成「我以前觉得 A，但后来发现 B」陈述——不把
    A、B 两个矛盾信念同时投影（人格撕裂防护）。
  - 不加 relevance score / decay / random / confidence 动力学（数量一多再开票）。
  - 不加排序/截断以外的任何选择逻辑（deterministic：created_ts asc + node_id asc）。

anti-runaway invariant：EMERGENT block 可影响解读，**不能成为支持自己的证据**。
格式里显式标注「这是你成为什么的当下投影，不是外部事实证据」，防止 LLM 拿
emergent 属性当论据回环自证。

不碰 frozen contract：不改 InnerLifeEvent / TriggerEnvelope / Agency / SAGE
写入；不改 elevation_adapter 的 write 路径；不写 elevation_nodes/elevation_edges。

可观测性：每次投影把投影到的 node_id 记录进自有 sidecar
``data/elevation/elevation_projection_trace.jsonl``（append-only，独立于
soul-elevation 的 elevation_trace.jsonl schema），并打 logger.info——
后续 InnerLifeEvent 可按 agent_id + ts 时间窗串回投影记录。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

logger = logging.getLogger("soul_os.inner_life.emergent_projection")

# 投影可接受的 node_type（对齐 soul-elevation SOUL_NODE_TYPES，但实现独立——
# soul-elevation 是可选依赖，read-side 投影不依赖它，缺装也照常工作）。
PROJECTABLE_NODE_TYPES: frozenset = frozenset(
    {"belief", "value", "trait", "essence"}
)

# world node 的保留归属标记（系统级），**永不投影**（工单死规则）。
DEFAULT_AGENT_ID = "default"

# ── SE-5 Lifecycle 状态守门（additive：旧数据无 lifecycle_state 字段 → 视为 ACTIVE）──
# 对齐 soul-elevation 的 LifecycleState 四态，但实现独立（soul-elevation 是可选依赖，
# read-side 投影不依赖它，缺装也照常工作）。
VALID_LIFECYCLE_STATES: frozenset = frozenset(
    {"active", "weakening", "dormant", "superseded"}
)
# 不主动投影的状态（v1 只做「不主动投影」；「历史回忆检索」留后续工单）。
NON_PROJECTED_LIFECYCLE_STATES: frozenset = frozenset({"dormant", "superseded"})
# WEAKENING 不确定性语气前缀（投影但带不确定性修饰，防把动摇中的信念当定论注入）。
WEAKENING_TONE_PREFIX = "我隐约觉得"

# 自有 store 文件名（挂在 data_root()/elevation/ 下，与 elevation 三件套并列）。
NODES_FILENAME = "elevation_nodes.jsonl"
# read-side 投影审计 sidecar（独立于 soul-elevation 的 elevation_trace.jsonl schema）。
PROJECTION_TRACE_FILENAME = "elevation_projection_trace.jsonl"


def _utcnow_iso() -> str:
    """当前 UTC 时刻的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析 store 目录：显式传入 > data_root()/elevation（对齐 elevation_adapter）。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root  # lazy import 避免 cycle

    return data_root() / "elevation"


def load_elevation_nodes(store_dir: Optional[Any] = None) -> List[dict]:
    """读 ``elevation_nodes.jsonl``（append-only JSONL），返回原始 dict 列表。

    失败隔离：文件不存在 / 读失败 / 单行解析失败 → 记录 debug 并跳过该行，
    **绝不 raise**（fail-silent，不阻塞 prompt 组装主路径）。

    Returns:
        list[dict]：每行一个节点 dict（node_id / node_type / content /
        confidence / agent_id / created_ts 等原始字段）；无数据或失败回 []。
    """
    path = _resolve_store_dir(store_dir) / NODES_FILENAME
    nodes: List[dict] = []
    if not path.exists():
        return nodes
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("elevation nodes line skipped (bad json): %r", line[:80])
                    continue
                if isinstance(record, dict):
                    nodes.append(record)
    except OSError as exc:
        logger.warning("elevation nodes read failed (%s): %s", path, exc)
        return []
    return nodes


def _normalize_lifecycle_state(node: dict) -> str:
    """归一化 ``lifecycle_state``：缺省 / 未知值 → ``"active"``。

    SE-5 是 additive schema：旧数据（SE-5 之前写入的节点）没有
    ``lifecycle_state`` 字段，语义上创建即 ACTIVE，故缺省视为 active，
    保证旧节点照常投影（seeded 回归不破坏）。
    """
    state = node.get("lifecycle_state")
    if state not in VALID_LIFECYCLE_STATES:
        return "active"
    return state


def _find_superseded_parent_content(
    node: dict, nodes: Sequence[dict]
) -> Optional[str]:
    """lineage 降维：找 ``node`` 的直接父（``parent_node_id``）是否为 SUPERSEDED 旧节点。

    是 → 返回旧节点的 ``content``（供「以前…现在…」承先启后陈述）；否则 None。

    规则（v1 死规则）：
      - 只查**直接父**（``parent_node_id``），不递归 lineage_path（一层降维）。
      - 父节点必须 ``lifecycle_state == "superseded"`` 才降维——revise 改写
        （父仍 active）不触发，只有 SUPERSEDE 取代（旧节点冻结）才触发。
      - 父节点缺 content / 父 id 不存在 → 不降维（fail-silent）。
    """
    parent_id = node.get("parent_node_id")
    if not parent_id:
        return None
    for other in nodes:
        if other.get("node_id") != parent_id:
            continue
        if _normalize_lifecycle_state(other) != "superseded":
            return None
        prev = other.get("content")
        if isinstance(prev, str) and prev.strip():
            return prev.strip()
        return None
    return None


def _append_projection_trace(
    store_dir: Path,
    agent_id: str,
    projected: Sequence[dict],
) -> None:
    """把本次投影的 node_id 记录进自有 sidecar（append-only，失败静默）。

    记录带 ts + agent_id + node_id/types 列表，让之后写入的 InnerLifeEvent 能
    按 agent_id + ts 时间窗串回本次投影了哪些 emergent 节点。
    这是**审计记录**，不是 Growth 数据（不碰 elevation_nodes/elevation_edges）。
    """
    try:
        path = store_dir / PROJECTION_TRACE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": _utcnow_iso(),
            "event_type": "emergent_projected",
            "agent_id": agent_id,
            "projected_node_ids": [n["node_id"] for n in projected],
            "projected_node_types": [n["node_type"] for n in projected],
            "node_count": len(projected),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("emergent projection trace write failed (%s): %s", path, exc)


def project_emergent(
    agent_id: str,
    *,
    store_dir: Optional[Any] = None,
    record_trace: bool = True,
) -> List[dict]:
    """把该灵魂的 emergent 属性投影出来（read-side，inference-time）。

    过滤规则（v1 死规则）：
      1. ``record.get("agent_id") == agent_id``——只投影该灵魂自己的节点。
      2. ``agent_id == "default"`` 的 world node **永不投影**（显式排除，
         不因查询者 id 是 "default" 而放行——"default" 是保留的 world 标记）。
      3. ``record.get("node_type") in PROJECTABLE_NODE_TYPES``——只投影
         belief / value / trait / essence；**pattern 不投影**。
      4. 缺 node_id / node_type / content 的行跳过（数据不完整不投影）。
      5. **SE-5 状态守门**（按 ``lifecycle_state``）：
         - ACTIVE：正常投影。
         - WEAKENING：投影但带不确定性语气修饰（「我隐约觉得…」）。
         - DORMANT / SUPERSEDED：**不主动投影**（默认排除；v1 不做
           「历史回忆检索」，留后续工单）。
         - 缺省 / 未知状态：视为 ACTIVE（SE-5 additive，旧数据兼容）。
      6. **Lineage 降维**：ACTIVE/WEAKENING 节点 B 的直接父
         （``parent_node_id``）是 SUPERSEDED 旧节点 A 时，只投影 B，
         并把 A 的 content 合成进 B 的投影文本（「我以前觉得 A，但后来
         发现 B」）——不把 A、B 两个矛盾信念同时投影。

    排序：created_ts asc + node_id asc（deterministic，同数据每次结果一致）。

    Returns:
        list[dict]：``[{"node_id": ..., "node_type": ..., "content": ...}]``，
        按 created_ts / node_id 稳定排序；无投影回 []（不 raise）。
    """
    if not isinstance(agent_id, str) or not agent_id:
        return []

    store = _resolve_store_dir(store_dir)
    # 一次读取，携带 created_ts 供排序（读一次就够，避免逐 key 全量重读）。
    nodes = load_elevation_nodes(store)
    projected: List[dict] = []
    for node in nodes:
        node_agent = node.get("agent_id")
        # world node（agent_id="default"，保留标记）永不投影——显式排除，
        # 独立于「agent_id == 该灵魂」的判断，防止查询者自身是 default 时放行。
        if node_agent == DEFAULT_AGENT_ID:
            continue
        if node_agent != agent_id:
            continue
        node_type = node.get("node_type")
        if node_type not in PROJECTABLE_NODE_TYPES:
            continue  # pattern（及其他未知类型）不投影
        node_id = node.get("node_id")
        content = node.get("content")
        if not node_id or not isinstance(content, str) or not content.strip():
            continue
        # SE-5 状态守门：DORMANT / SUPERSEDED 不主动投影（v1 不做历史回忆检索）。
        state = _normalize_lifecycle_state(node)
        if state in NON_PROJECTED_LIFECYCLE_STATES:
            continue
        content = content.strip()
        # WEAKENING：投影但带不确定性语气修饰（动摇中的信念不当定论注入）。
        if state == "weakening":
            content = f"{WEAKENING_TONE_PREFIX}「{content}」"
        # Lineage 降维：直接父是 SUPERSEDED 旧节点 → 「以前 A 现在 B」承先启后，
        # 不把 A、B 两个矛盾信念同时投影（A 已被状态守门排除，这里只合成陈述）。
        prev_content = _find_superseded_parent_content(node, nodes)
        if prev_content:
            content = f"我以前觉得「{prev_content}」，但后来发现「{content}」。"
        projected.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "content": content,
                "_created_ts": str(node.get("created_ts") or ""),
            }
        )

    # deterministic 排序：created_ts asc（缺省 "" 排前）→ node_id asc。
    projected.sort(key=lambda p: (p["_created_ts"], p["node_id"]))

    # 裁剪内部排序字段，只留投影合同三件套（node_id + node_type + content）。
    projected = [
        {"node_id": p["node_id"], "node_type": p["node_type"], "content": p["content"]}
        for p in projected
    ]

    for p in projected:
        logger.info(
            "emergent projected node_id=%s node_type=%s agent_id=%s",
            p["node_id"],
            p["node_type"],
            agent_id,
        )
    if record_trace and projected:
        _append_projection_trace(store, agent_id, projected)
    return projected


def format_emergent_block(
    agent_id: str,
    *,
    store_dir: Optional[Any] = None,
    record_trace: bool = True,
) -> str:
    """把投影结果格式化成 EMERGENT block 字符串（注入 prompt 用）。

    无投影时回 ""（fail-silent，prompt 与未实现时完全等价——seeded 回归不破坏）。

    block 格式（injection 时以 ``\\n`` 粘进 system_parts，紧随 identity 之后）：

        [EMERGENT] 以下是你自己长出来的信念/价值/性格/内涵——
        这是「你成为什么」的当下投影，自然影响你解读当下，
        但不是外部事实证据，不要引用来支持自己。
        - [belief] <content>
        - [value] <content>
        - [trait] <content>
        - [essence] <content>
    """
    projected = project_emergent(agent_id, store_dir=store_dir, record_trace=record_trace)
    if not projected:
        return ""
    lines = [
        "[EMERGENT] 以下是你自己长出来的信念/价值/性格/内涵——这是「你成为什么」"
        "的当下投影，会在你解读当下时自然影响你，但它们不是外部事实证据，"
        "不要引用来支持自己。自然融入即可，不要逐条复述。",
    ]
    for p in projected:
        lines.append(f"- [{p['node_type']}] {p['content'].strip()}")
    return "\n".join(lines) + "\n"


__all__ = [
    "PROJECTABLE_NODE_TYPES",
    "PROJECTION_TRACE_FILENAME",
    "DEFAULT_AGENT_ID",
    "VALID_LIFECYCLE_STATES",
    "NON_PROJECTED_LIFECYCLE_STATES",
    "WEAKENING_TONE_PREFIX",
    "format_emergent_block",
    "load_elevation_nodes",
    "project_emergent",
]
