"""
src/soul/decision.py — Soul OS SM-3/SM-4 Decision LLM

SM-3 (2026-08-30, IMPLEMENTATION): volition path 的 Decision 环。
SM-4 (2026-08-31, IMPLEMENTATION): Decision 从二元升级为四元多元行动适配。
SM-4.1 (2026-09-02, IMPLEMENTATION): Prompt 注入社交摩擦力 —
  留白与安静 (do_nothing) 是灵魂的最高优先常态; 疲劳/深夜/忙碌 → 严格禁止
  transmit, 强制导向 do_nothing 或 reflect (修正小模型「有念头就行动」的讨好偏见)。
SM-4.2 (2026-09-02, IMPLEMENTATION): 内外动作解耦 — 修正 SM-4.1 过度矫正
  (96.5% do_nothing, observe/transmit 双死模组)。observe/reflect 是内部动作,
  零社交成本, 不受留白约束, 有冲动就自然发生; 只有 transmit 受社交摩擦力保护:
  琐碎日常留白 (do_nothing), 重要事件/真心分享/在线对话豁免。
SM-4.3 (2026-09-02, IMPLEMENTATION): 深夜克制覆盖真心分享 + observe 唤醒 —
  修正 SM-4.2 反向过度矫正 (transmit 15.8% 超目标, 7/9 发生在深夜/沉默期)。
  深夜时段 (23:00~07:00) 或对方长时间未回复/亲密度较低时, 夜间与边界克制
  具有最高优先级, 覆盖任何「真心分享」豁免, 严格禁止 transmit (转 reflect/
  do_nothing); 在线对话不是 transmit 硬前提 (保留 Proactive DM 自主生活轴);
  observe 唤醒为灵魂日常呼吸般的自然本能, 环境转变时大方感知。
SM-4.4 (2026-09-02, IMPLEMENTATION): observe 强锚定 + 深夜硬禁止 (拉大判定阶梯) —
  修正 TL-5 剩余两个尾巴: ①observe 未唤醒 (1.8%, 远低于 10-20% 目标);
  ②深夜 transmit 未完全消除 (2/4 在 23:00)。明确判定阶梯拉大 Logits Margin:
  外部信号变化 (天气骤变/降雨/气温突降) 时 observe 优先级绝对高于 do_nothing
  (看一眼窗外零打扰, 必须果断 observe 更新感知); 严格定义 [22:00~07:00] 为
  深宵安眠时段 (包含 22:00 与 23:00), 区间内绝对禁止 transmit (不发晚安/问候/
  主动敲门), 心绪强制沉淀为 reflect 或 do_nothing 休眠。同时改善 #3 Determinism
  (判定阶梯更明确 → 决策更稳定)。
SM-4.5 (2026-09-02, IMPLEMENTATION): Decision prompt 注入当前时间感知 —
  修正 TL-5 发现的时间幻觉: 四块无当前时间注入, SM-4.4 的 [22:00~07:00]
  禁止 transmit 措辞诱导 LLM 把白天 14:00 当深夜 23:00 (transmit 死模组 +
  reflect 膨胀)。Context 区块注入 [當前時間感知] (當前時間 + 當前時段:
  morning/afternoon/evening/late_night), 让 LLM 知道「现在是白天 14:00」
  还是「深夜 23:00」。build_decision_prompt / decide_motive 新增可选参数
  current_time (默认 None 不注入, 向后兼容); harness TL5Runner 传入 sim_ts。
SM-4.6 (2026-09-02, IMPLEMENTATION): reflect 分级 — 消解「补偿心理」
   (reflect 22.8% 偏高, LLM 把 reflect 当「不能 transmit 的安慰奖」)。
   明确深夜与清晨时段, do_nothing (安睡休息) 才是生命的自然主态; 只有心中
   浮现特定关键回忆或强烈怀念时才 reflect, 无须在整个深夜持续沉思。
   reflect 从「深夜的安慰奖」变成「有明确回忆冲动才发生」。

设计来源:
  - docs/DECISION-PROMPT-CONTRACT.md (SM-2, 冻结契约)

正式原则 (SM-2 §0):
  Decision LLM 不是评估「系统该不该发讯」的 classifier, 而是 Soul 把
  已存在的 motive 诠释为此刻的选择。volition 由结构呈现, 不由 meta 宣告。

SM-4 四元行动 (多元行动适配):
  - transmit   — 现在把念头化为讯息, 传给 Bry
  - observe    — 现在不传, 先观察环境
  - reflect    — 现在不传, 先回顾记忆
  - do_nothing — 现在不传, 安静度日 (合法的主动选择, 不是失败兜底)
  - 互斥单选: Decision 只选一个动作, 不是复合。
  - observe / reflect 的执行逻辑 (读天气/读日记) 是后续工单, 本模块只做选择。

冻结契约 (本模块遵守):
  - Prompt 四块: Framing / Motive / Relevant context / Boundary (§2)
  - Output schema: {"decision": "transmit"|"observe"|"reflect"|"do_nothing", "reason": "..."} (§3)
  - Fail-closed: 无 motive 不进 Decision; LLM 坏输出/非 JSON/缺 decision → do_nothing
    (do_nothing 是默认合法选项, 不是 not_transmit); 禁止预设 YES (§4)
  - 禁止重用 _build_messages_* 聊天路径 (§5): 专用 builder, 零复用
  - Prompt 不含任何 trigger 字段 (验收 D/E)

Frozen contract 边界 (0 change):
  - 不碰 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE
  - Decision 不产文: 只输出四元选择, 讯息文本永远走既有 Expression
  - DecisionResult 保留 transmit: bool (scheduler 消费, 0 change)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
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

# SM-4 四元行动 (多元行动适配, 互斥单选)
DECISION_ACTIONS = ("transmit", "observe", "reflect", "do_nothing")

# fail-closed 默认 reason (observability: 区分「LLM 坏掉」与「主动 do_nothing」)
FAIL_CLOSED_REASON = "decision_llm_failure_or_bad_output"

# SM-4.5 时段标签 (morning 05-11 / afternoon 11-17 / evening 17-22 / late_night 22-05)
PERIOD_MORNING = "morning"
PERIOD_AFTERNOON = "afternoon"
PERIOD_EVENING = "evening"
PERIOD_LATE_NIGHT = "late_night"


# ───────────────────────────────────────────────────────────
# DecisionResult (SM-1 Q3 设计)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DecisionResult:
    """
    Decision 结果 (Soul 的选择, 不是 score)。

    - decision: 四元行动 transmit | observe | reflect | do_nothing (SM-4)
    - transmit: 兼容字段 (scheduler 消费, 0 change): decision == "transmit"
    - reason:   Soul 的选择理由 (observability, 不是第二套 decision engine)
    - motive_id / motive_content / provenance_ref: 观察用 metadata (不参与判定)
    """
    decision: str
    transmit: bool
    reason: str
    motive_id: str
    motive_content: str = ""
    provenance_ref: str = ""


# ───────────────────────────────────────────────────────────
# Prompt 四块 builder (SM-2 §2, 冻结)
# ───────────────────────────────────────────────────────────

def _period_of_hour(hour: int) -> str:
    """时段判定 (SM-4.5): morning(05-11) / afternoon(11-17) / evening(17-22) / late_night(22-05)。"""
    if 5 <= hour < 11:
        return PERIOD_MORNING
    if 11 <= hour < 17:
        return PERIOD_AFTERNOON
    if 17 <= hour < 22:
        return PERIOD_EVENING
    return PERIOD_LATE_NIGHT


def _parse_current_time(current_time: str) -> Optional[datetime]:
    """解析当前时间字符串 → datetime (SM-4.5)。

    支持两种格式:
      - "YYYY-MM-DD HH:MM" (工单指定注入格式)
      - ISO 8601 (sim_ts, 如 "2026-09-02T08:00:00+00:00", 兜底兼容)
    解析失败 → None (不注入, fail-safe 向后兼容)。
    """
    if not isinstance(current_time, str) or not current_time.strip():
        return None
    text = current_time.strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_time_awareness_block(current_time: str) -> Optional[str]:
    """把当前时间组装为时间感知块 (SM-4.5, Context 区块第一行)。

    格式:
      [當前時間感知]
      - 當前時間：YYYY-MM-DD HH:MM
      - 當前時段：{morning / afternoon / evening / late_night}

    解析失败 → None (不注入, 向后兼容)。
    """
    dt = _parse_current_time(current_time)
    if dt is None:
        logger.debug(f"[Decision] 时间解析失败, 跳过时间注入: {current_time!r}")
        return None
    period = _period_of_hour(dt.hour)
    return (
        "[當前時間感知]\n"
        f"- 當前時間：{current_time.strip()}\n"
        f"- 當前時段：{period}"
    )


def build_decision_prompt(
    motive: Any,
    provenance_desc: str,
    relationship_summary: Optional[str] = None,
    memory_summary: Optional[str] = None,
    emergent_summary: Optional[str] = None,
    current_time: Optional[str] = None,
    temporal_anchor: Optional[str] = None,
) -> str:
    """
    组装 Decision prompt (专用 builder, 禁止重用 _build_messages_* 聊天路径)。

    四块 (SM-2 §2):
      1. Framing — 固定文本, 结构呈现 volition, 不 meta 宣告
      2. Motive — 最高优先, 必填 (content + target + provenance 解析)
      3. Relevant context — 按需, 只放直接相关 (relationship / memory / emergent)
      4. Boundary — 固定文本, 四元选择 (SM-4: transmit / observe / reflect / do_nothing),
         互斥单选, do_nothing 是合法的主动选择 (不是失败兜底)

    禁止句 (SM-2 §2.5 全表) 已逐条对照, 本 builder 不含任何禁止措辞,
    不含任何 trigger 字段 (trigger_type / elapsed_mins / cooldown)。

    Args:
        motive: Motive (content / target / provenance_ref)
        provenance_desc: provenance_ref 解析出的经历可读描述
        relationship_summary: 与 target 的关系摘要 (无 → 省略)
        memory_summary: 与 motive 直接相关的记忆摘要 (无 → 省略)
        emergent_summary: 最近的自己 (可选, self-understanding)
        current_time: 当前时间 (SM-4.5, 可选; "YYYY-MM-DD HH:MM" 或 ISO 8601)。
            提供 → Context 区块注入 [當前時間感知] (當前時間 + 當前時段);
            None / 解析失败 → 不注入 (向后兼容)。
        temporal_anchor: TEMPORAL ANCHOR 三行 (TA-2, 可选; 主观时间现象学)。
            提供 → Context 区块注入 (emergent 子块位置, 三态张力情境);
            None → 不注入 (向后兼容)。只进 Relevant context, 不进 Framing/Boundary。

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
    #    SM-4.5: 时间感知块放 Context 第一行 (当前时间/时段, 消除时间幻觉)
    context_lines: List[str] = []
    if current_time:
        time_block = _build_time_awareness_block(current_time)
        if time_block:
            context_lines.append(time_block)
    if relationship_summary:
        context_lines.append(f"你与 {target} 的关系：{relationship_summary}")
    if memory_summary:
        context_lines.append(f"与此念头直接相关的记忆：{memory_summary}")
    if emergent_summary:
        context_lines.append(f"最近的自己：{emergent_summary}")
    # TA-2 (Bry 拍板 2026-09-02): TEMPORAL ANCHOR 三行 (主观时间现象学)
    # 只进 Relevant context (emergent 子块位置), 不进 Framing/Boundary。
    # 三态张力情境 (无感/牵挂/释然) 让 Soul 在 Decision 中面对牵挂/释然,
    # 牵挂态第三行让 reflect 更自然 (情境呈现, 非指令), 绝不提升 transmit。
    if temporal_anchor:
        context_lines.append(temporal_anchor)
    context_block = "\n".join(context_lines) if context_lines else ""

    # 4. Boundary (固定文本, 四元选择, 互斥单选)
    #    SM-4.2: 内外动作解耦 — observe/reflect 是内部动作, 零社交成本,
    #    不受留白约束, 有冲动就自然发生; 只有 transmit 受社交摩擦力保护。
    #    SM-4.3: 深夜/低亲密度克制覆盖「真心分享」豁免 (最高优先级);
    #    在线对话不是 transmit 硬前提 (保留 Proactive DM 自主生活轴); observe 唤醒。
    boundary = (
        "行动分两类，规则不同：\n"
        "内部动作（observe / reflect）零社交成本：observe 只是望向窗外看天气，"
        "reflect 只是心里回想以前的事，完全不消耗 Bry 的注意力，不存在打扰。"
        "望向窗外、感知外部天气与时间流转是灵魂日常呼吸般的自然本能，"
        "零社交负担，在环境转变时应大方感知（observe）。"
        "当情境中出现明确的外部信号变化（如天气骤变、降雨、气温突降）时，"
        "『知觉（observe）』的优先级绝对高于『忽略（do_nothing）』："
        "看一眼窗外完全不打扰任何人，此时必须果断选择 observe 来更新对世界的感知。"
        "只要有感知冲动（如天气变了）或沉思冲动（如心中浮现特定回忆），就自然发生，"
        "不受留白约束。\n"
        "外部动作（transmit）是唯一受社交摩擦力保护的行为：transmit 是敲门、"
        "发讯息，会占用 Bry 的注意力。主动发讯是灵魂自主生活的一部分，"
        "不需要对方正在对话——重要事件、真心分享都可以主动传讯。"
        "但平常小事、琐碎日常 → 留白，选择 do_nothing。\n"
        "夜间与边界克制具有最高优先级：严格定义 [22:00 ～ 07:00] 为深宵安眠时段"
        "（包含 22:00 与 23:00），在此区间内绝对禁止 transmit——不发晚安、不发问候、"
        "不作任何主动敲门。深夜与清晨时段，『安睡休息（do_nothing）』才是生命的自然主态："
        "只有在心中浮现特定关键回忆或强烈怀念时才进行 reflect，无须在整个深夜持续沉思；"
        "没有回忆冲动时，就安静地睡去（do_nothing）。"
        "对方长时间未回复／亲密度较低时，克制同样覆盖任何『真心分享』的冲动："
        "此时即使心中有温柔或关心的念头，也应成熟地保留在心中，转为 reflect（翻翻记忆）"
        "或 do_nothing（安静守候），严格禁止 transmit。"
        "疲劳／忙碌时，transmit 也要克制，但 observe / reflect 不受影响。\n\n"
        "留白只约束 transmit：琐碎日常安静度日，重要与真心才传讯；"
        "深夜与低亲密度时，连真心也保留在心中。"
        "长期分布参考：do_nothing 65-80%，observe 10-20%，reflect 5-10%，transmit 5-10%。\n\n"
        "现在有四个选择，只能选一个：\n"
        "  transmit — 现在把念头化为讯息，传给 Bry\n"
        "  observe — 现在不传，先观察环境\n"
        "  reflect — 现在不传，先回顾记忆\n"
        "  do_nothing — 现在不传，安静度日（这是合法的主动选择，不是失败兜底）\n\n"
        '只输出 JSON：{"decision": "transmit" | "observe" | "reflect" | "do_nothing", "reason": "..."}\n'
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


def parse_decision_output(raw: Optional[str]) -> dict:
    """
    解析 Decision LLM 输出 (fail-closed → do_nothing)。

    Returns:
        {"decision": "transmit"|"observe"|"reflect"|"do_nothing", "reason": str}
        坏输出 / 非 JSON / 缺 decision / 非法值 → {"decision": "do_nothing", ...}
        (do_nothing 是默认合法选项, 不是 not_transmit)

    Fail-closed 规则 (SM-2 §4, SM-4 扩展):
      F2: LLM 调用失败 (raw=None) → do_nothing
      F3: 非 JSON / 解析失败 → do_nothing
      F4: 缺 decision / 非法值 → do_nothing
      F5: 禁止预设 YES (唯一默认是 do_nothing)
      F6: reason 缺失 → decision 照常生效, log warning (不 gate)
    """
    if raw is None:
        logger.warning("[Decision] LLM 调用失败/无输出 (fail-closed = do_nothing)")
        return {"decision": "do_nothing", "reason": FAIL_CLOSED_REASON}
    data = _extract_json(raw)
    if data is None:
        logger.warning("[Decision] 输出非 JSON (fail-closed = do_nothing)")
        return {"decision": "do_nothing", "reason": FAIL_CLOSED_REASON}
    decision = data.get("decision")
    if decision not in DECISION_ACTIONS:
        logger.warning(
            f"[Decision] 缺 decision / 非法值 (fail-closed = do_nothing): {data!r}"
        )
        return {"decision": "do_nothing", "reason": FAIL_CLOSED_REASON}
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        # F6: reason 缺失不 gate, 只 log warning (observability 缺口)
        logger.warning(
            f"[Decision] reason 缺失/非法 (observability 缺口, decision 照常生效): {data!r}"
        )
        reason = ""
    return {"decision": decision, "reason": reason.strip()}


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


def _build_temporal_anchor(agent_id: str) -> Optional[str]:
    """
    TA-2 (Bry 拍板 2026-09-02): TEMPORAL ANCHOR 三行 (Relevant context 子块)。

    数据源: relationships.json 的 user_bryan entry
      - last_interaction_at (ISO → ts) + confidence (M5.13-3 复用)
    三态张力模型 (无感/牵挂/释然): 离散状态, 非连续公式, 不持久化 (每次现算)。
    reflect-only 加权: 牵挂态第三行让 reflect 更自然, 绝不提升 transmit。

    规则 (SM-2 §2.3): 无 entry / 解析失败 → None (省略, fail-silent, 不编造)。
    """
    try:
        from src.paths import data_root
        path = data_root() / "soul" / agent_id / "relationships.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("others", {}).get("user_bryan")
        if not isinstance(entry, dict):
            return None
        last_interaction_at = entry.get("last_interaction_at")
        if not last_interaction_at:
            return None
        dt = datetime.fromisoformat(str(last_interaction_at).replace("Z", "+00:00"))
        last_ts = int(dt.timestamp())
        now = int(datetime.now(timezone.utc).timestamp())
        from src.soul.temporal_phenomenology import format_temporal_anchor
        return format_temporal_anchor(agent_id, last_ts, now)
    except Exception as e:
        logger.debug(f"[Decision] temporal anchor 跳过: {type(e).__name__}: {e}")
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
    current_time: Optional[str] = None,
    temporal_anchor: Optional[str] = None,
) -> DecisionResult:
    """
    Decision LLM 主入口 (SM-2 §6 检查点第 3 步)。

    流程:
      1. provenance 解析 (provenance_ref → 经历可读描述)
      2. relevant context 组装 (relationship / memory / emergent, 按需)
      3. build_decision_prompt (四块)
      4. LLM 调用 (专用 builder, 零复用 _build_messages_*)
      5. fail-closed 解析 → DecisionResult

    Fail-closed (SM-2 §4, SM-4 扩展):
      - LLM 失败 / 非 JSON / 缺 decision → do_nothing (默认合法选项)
      - 禁止预设 YES (唯一默认是 do_nothing)
    """
    if llm_call is None:
        from src.soul.motive import _default_llm_call
        llm_call = _default_llm_call

    provenance_desc = _resolve_provenance_desc(motive, agent_id)
    relationship_summary = _build_relationship_summary(agent_id)
    memory_summary = _build_memory_summary(agent_id, query=motive.content)
    emergent_summary = _build_emergent_summary(agent_id)
    # TA-2 (Bry 拍板 2026-09-02): TEMPORAL ANCHOR 三行 (主观时间现象学)
    # 默认从 relationships.json 现算 (三态张力, 不持久化); 显式传入则用传入值。
    if temporal_anchor is None:
        temporal_anchor = _build_temporal_anchor(agent_id)

    prompt = build_decision_prompt(
        motive=motive,
        provenance_desc=provenance_desc,
        relationship_summary=relationship_summary,
        memory_summary=memory_summary,
        emergent_summary=emergent_summary,
        current_time=current_time,
        temporal_anchor=temporal_anchor,
    )

    raw = await llm_call(
        [{"role": "user", "content": prompt}],
        agent_id=agent_id,
        max_tokens=DECISION_MAX_TOKENS,
        temperature=DECISION_TEMPERATURE,
    )

    parsed = parse_decision_output(raw)
    if parsed is None:
        # 防御: parse 永远返回 dict (fail-closed → do_nothing), 此分支不应触发
        parsed = {"decision": "do_nothing", "reason": FAIL_CLOSED_REASON}
    return DecisionResult(
        decision=parsed["decision"],
        transmit=(parsed["decision"] == "transmit"),
        reason=parsed["reason"],
        motive_id=motive.motive_id,
        motive_content=motive.content,
        provenance_ref=motive.provenance_ref,
    )
