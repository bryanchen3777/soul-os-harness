"""
src/soul/decision.py — Soul OS SM-3 Decision LLM

SM-3 (2026-08-30, IMPLEMENTATION): volition path 的 Decision 环。

设计来源:
  - docs/DECISION-PROMPT-CONTRACT.md (SM-2, 冻结契约)

正式原则 (SM-2 §0):
  Decision LLM 不是评估「系统该不该发讯」的 classifier, 而是 Soul 把
  已存在的 motive 诠释为此刻的选择。volition 由结构呈现, 不由 meta 宣告。

冻结契约 (本模块遵守):
  - Prompt 四块: Framing / Motive / Relevant context / Boundary (§2)
  - Output schema: {"decision": "transmit"|"not_transmit", "reason": "..."} (§3)
  - Fail-closed: 无 motive 不进 Decision; LLM 坏输出/非 JSON/缺 decision → not_transmit;
    禁止预设 YES (§4)
  - 禁止重用 _build_messages_* 聊天路径 (§5): 专用 builder, 零复用
  - Prompt 不含任何 trigger 字段 (验收 D/E)

Frozen contract 边界 (0 change):
  - 不碰 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE
  - Decision 不产文: 只输出 transmit / not_transmit, 讯息文本永远走既有 Expression
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("soul_os.soul.decision")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

# Decision LLM 参数 (选择要稳定, 低温度; 待 Owner 拍板, 可配置)
DECISION_MAX_TOKENS = 200
DECISION_TEMPERATURE = 0.3

# emergent 摘要: 最近几条 inner life 活动
EMERGENT_RECENT_COUNT = 3


# ───────────────────────────────────────────────────────────
# DecisionResult (SM-1 Q3 设计)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DecisionResult:
    """
    Decision 结果 (Soul 的选择, 不是 score)。

    - transmit: 传 / 不传
    - reason:   Soul 的选择理由 (observability, 不是第二套 decision engine)
    - motive_id / motive_content / provenance_ref: 观察用 metadata (不参与判定)
    """
    transmit: bool
    reason: str
    motive_id: str
    motive_content: str = ""
    provenance_ref: str = ""


# ───────────────────────────────────────────────────────────
# Prompt 四块 builder (SM-2 §2, 冻结)
# ───────────────────────────────────────────────────────────

def build_decision_prompt(
    motive: Any,
    provenance_desc: str,
    relationship_summary: Optional[str] = None,
    memory_summary: Optional[str] = None,
    emergent_summary: Optional[str] = None,
) -> str:
    """
    组装 Decision prompt (专用 builder, 禁止重用 _build_messages_* 聊天路径)。

    四块 (SM-2 §2):
      1. Framing — 固定文本, 结构呈现 volition, 不 meta 宣告
      2. Motive — 最高优先, 必填 (content + target + provenance 解析)
      3. Relevant context — 按需, 只放直接相关 (relationship / memory / emergent)
      4. Boundary — 固定文本, 二元选择 (现在传 / 现在不传)

    禁止句 (SM-2 §2.5 全表) 已逐条对照, 本 builder 不含任何禁止措辞,
    不含任何 trigger 字段 (trigger_type / elapsed_mins / cooldown)。

    Args:
        motive: Motive (content / target / provenance_ref)
        provenance_desc: provenance_ref 解析出的经历可读描述
        relationship_summary: 与 target 的关系摘要 (无 → 省略)
        memory_summary: 与 motive 直接相关的记忆摘要 (无 → 省略)
        emergent_summary: 最近的自己 (可选, self-understanding)

    Returns:
        纯文本 prompt (decide 时包成 messages)
    """
    target = motive.target
    content = motive.content

    # 1. Framing (固定文本, 最小化)
    framing = (
        "你心里有一个念头，已经成形。现在你面对它："
        "要不要在此时此刻，把它化为行动。"
    )

    # 2. Motive (必填, 最高优先)
    motive_block = f"你想告诉 {target}：{content}"
    if provenance_desc:
        motive_block += f"\n这个念头来自：{provenance_desc}"

    # 3. Relevant context (按需, 只放直接相关)
    context_lines: List[str] = []
    if relationship_summary:
        context_lines.append(f"你与 {target} 的关系：{relationship_summary}")
    if memory_summary:
        context_lines.append(f"与此念头直接相关的记忆：{memory_summary}")
    if emergent_summary:
        context_lines.append(f"最近的自己：{emergent_summary}")
    context_block = "\n".join(context_lines) if context_lines else ""

    # 4. Boundary (固定文本, 二元选择)
    boundary = (
        "现在只有两个选择：现在传，或现在不传。\n\n"
        '只输出 JSON：{"decision": "transmit" | "not_transmit", "reason": "..."}\n'
        "reason 用你自己的话说明这个选择，可以提到念头、关系、出处。"
    )

    parts = [framing, motive_block]
    if context_block:
        parts.append(context_block)
    parts.append(boundary)
    return "\n\n".join(parts)


# ───────────────────────────────────────────────────────────
# Output 解析 (fail-closed, SM-2 §3/§4)
# ───────────────────────────────────────────────────────────

def _extract_json(raw: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON dict (容错 markdown 代码块 / 前后杂讯)。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def parse_decision_output(raw: Optional[str]) -> Optional[dict]:
    """
    解析 Decision LLM 输出 (fail-closed)。

    Returns:
        {"transmit": bool, "reason": str} 或 None (坏输出 → not_transmit)

    Fail-closed 规则 (SM-2 §4):
      F2: LLM 调用失败 (raw=None) → None
      F3: 非 JSON / 解析失败 → None
      F4: 缺 decision / 非法值 → None
      F5: 禁止预设 YES (唯一默认是 not_transmit)
      F6: reason 缺失 → decision 照常生效, log warning (不 gate)
    """
    if raw is None:
        logger.warning("[Decision] LLM 调用失败/无输出 (fail-closed = not_transmit)")
        return None
    data = _extract_json(raw)
    if data is None:
        logger.warning("[Decision] 输出非 JSON (fail-closed = not_transmit)")
        return None
    decision = data.get("decision")
    if decision == "transmit":
        transmit = True
    elif decision == "not_transmit":
        transmit = False
    else:
        logger.warning(
            f"[Decision] 缺 decision / 非法值 (fail-closed = not_transmit): {data!r}"
        )
        return None
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        # F6: reason 缺失不 gate, 只 log warning (observability 缺口)
        logger.warning(
            f"[Decision] reason 缺失/非法 (observability 缺口, decision 照常生效): {data!r}"
        )
        reason = ""
    return {"transmit": transmit, "reason": reason.strip()}


# ───────────────────────────────────────────────────────────
# Relevant context 组装 (SM-2 §2.3, 按需, 只放直接相关)
# ───────────────────────────────────────────────────────────

def _build_relationship_summary(agent_id: str) -> Optional[str]:
    """
    relationship 子块: 读 relationships.json 的 user_bryan entry。

    规则 (SM-2 §2.3): 通常有 (对象是人)。无 entry → None (省略), 禁止编造。
    """
    from src.paths import data_root
    path = data_root() / "soul" / agent_id / "relationships.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("others", {}).get("user_bryan")
    except Exception as e:
        logger.debug(f"[Decision] relationship 读取失败: {type(e).__name__}: {e}")
        return None
    if not isinstance(entry, dict):
        return None
    parts: List[str] = []
    if entry.get("impression"):
        parts.append(f"印象：{entry['impression']}")
    if entry.get("feeling"):
        parts.append(f"感觉：{entry['feeling']}")
    if isinstance(entry.get("confidence"), (int, float)):
        parts.append(f"信任度：{entry['confidence']:.2f}")
    if isinstance(entry.get("interaction_count"), int):
        parts.append(f"互动次数：{entry['interaction_count']}")
    if entry.get("last_interaction_at"):
        parts.append(f"最后互动：{entry['last_interaction_at']}")
    if not parts:
        return None
    return "；".join(parts)


def _build_memory_summary(agent_id: str, query: str) -> Optional[str]:
    """
    memory 子块: SAGE 检索, scope 限定与 motive 直接相关。

    规则 (SM-2 §2.3): 只放直接相关; 无直接相关 → None (省略)。
    禁止全量 memory dump。失败/无结果 → None (省略, 不编造)。
    """
    try:
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.reader import MemoryReader
        from src.paths import data_root
        db_path = data_root() / "memory" / agent_id / "graph.sqlite"
        if not db_path.exists():
            return None
        store = GraphStore(db_path=db_path)
        try:
            reader = MemoryReader(store)
            result = reader.retrieve_context(
                query=query,
                top_k=3,
                max_tokens=300,
                mode="precise",
            )
            summary = getattr(result, "summary", "") or ""
            return summary if summary.strip() else None
        finally:
            store.close()
    except Exception as e:
        logger.debug(f"[Decision] memory 检索跳过: {type(e).__name__}: {e}")
        return None


def _build_emergent_summary(agent_id: str) -> Optional[str]:
    """
    emergent 子块: 最近的 inner life 活动 (self-understanding)。

    规则 (SM-2 §2.3): 可选, 呈现 Soul 自己的近期经历, 不是分享的理由/规则。
    无 → None (省略)。
    """
    try:
        from src.inner_life.trace_reader import NarrativeTraceReader
        from datetime import datetime, timedelta, timezone
        reader = NarrativeTraceReader()
        now = datetime.now(timezone.utc)
        start = (now - timedelta(hours=24)).isoformat()
        records = reader.query_by_ts_range(start=start, end=now.isoformat())
        agent_records = []
        for r in records:
            if not isinstance(r, dict):
                continue
            prov = r.get("provenance")
            if not isinstance(prov, dict):
                continue
            if prov.get("actor_id") == agent_id:
                agent_records.append(r)
        if not agent_records:
            return None
        agent_records.sort(key=lambda r: r.get("ts", ""))
        recent = agent_records[-EMERGENT_RECENT_COUNT:]
        lines = []
        for r in recent:
            prov = r.get("provenance", {})
            tt = prov.get("trigger_type", "unknown") if isinstance(prov, dict) else "unknown"
            ts = r.get("ts", "")
            lines.append(f"{tt} @ {ts}")
        return "；".join(lines)
    except Exception as e:
        logger.debug(f"[Decision] emergent 摘要跳过: {type(e).__name__}: {e}")
        return None


def _resolve_provenance_desc(motive: Any, agent_id: str) -> str:
    """
    把 motive.provenance_ref (InnerLifeEvent.event_id) 解析为经历的可读描述
    (SM-2 §2.2: trigger_type + ts + 对应 diary/dream 文本产物)。

    找不到 → 只回 event_id (不编造)。
    """
    try:
        from src.inner_life.trace_reader import NarrativeTraceReader
        reader = NarrativeTraceReader()
        records = reader.query_by_event_id(motive.provenance_ref)
        if not records:
            return f"event_id={motive.provenance_ref}"
        rec = records[0]
        prov = rec.get("provenance", {})
        trigger_type = prov.get("trigger_type", "unknown") if isinstance(prov, dict) else "unknown"
        ts = rec.get("ts", "")
        desc = f"trigger_type={trigger_type}, ts={ts}"
        # 尝试读 diary/dream 文本产物 (跟 MotiveEngine._find_diary_text 同款)
        from src.soul.motive import MotiveEngine
        text = MotiveEngine()._find_diary_text(agent_id, trigger_type, ts)
        if text:
            desc += f"\n内容：{text}"
        return desc
    except Exception as e:
        logger.debug(f"[Decision] provenance 解析失败: {type(e).__name__}: {e}")
        return f"event_id={motive.provenance_ref}"


# ───────────────────────────────────────────────────────────
# decide 主入口 (fail-closed)
# ───────────────────────────────────────────────────────────

async def decide_motive(
    motive: Any,
    agent_id: str,
    llm_call: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
) -> DecisionResult:
    """
    Decision LLM 主入口 (SM-2 §6 检查点第 3 步)。

    流程:
      1. provenance 解析 (provenance_ref → 经历可读描述)
      2. relevant context 组装 (relationship / memory / emergent, 按需)
      3. build_decision_prompt (四块)
      4. LLM 调用 (专用 builder, 零复用 _build_messages_*)
      5. fail-closed 解析 → DecisionResult

    Fail-closed (SM-2 §4):
      - LLM 失败 / 非 JSON / 缺 decision → not_transmit
      - 禁止预设 YES (唯一默认是 not_transmit)
    """
    if llm_call is None:
        from src.soul.motive import _default_llm_call
        llm_call = _default_llm_call

    provenance_desc = _resolve_provenance_desc(motive, agent_id)
    relationship_summary = _build_relationship_summary(agent_id)
    memory_summary = _build_memory_summary(agent_id, query=motive.content)
    emergent_summary = _build_emergent_summary(agent_id)

    prompt = build_decision_prompt(
        motive=motive,
        provenance_desc=provenance_desc,
        relationship_summary=relationship_summary,
        memory_summary=memory_summary,
        emergent_summary=emergent_summary,
    )

    raw = await llm_call(
        [{"role": "user", "content": prompt}],
        agent_id=agent_id,
        max_tokens=DECISION_MAX_TOKENS,
        temperature=DECISION_TEMPERATURE,
    )

    parsed = parse_decision_output(raw)
    if parsed is None:
        # F2/F3/F4: fail-closed → not_transmit
        return DecisionResult(
            transmit=False,
            reason="decision_llm_failure_or_bad_output",
            motive_id=motive.motive_id,
            motive_content=motive.content,
            provenance_ref=motive.provenance_ref,
        )
    return DecisionResult(
        transmit=parsed["transmit"],
        reason=parsed["reason"],
        motive_id=motive.motive_id,
        motive_content=motive.content,
        provenance_ref=motive.provenance_ref,
    )
