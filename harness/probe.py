"""
harness/probe.py — GrowthProbe (TL-1)

TL-0 规格 §3 / §6.6 (D5, 已拍板):
  - Probe = 对现有 pipeline 的一次标准化呼叫: 固定 stimulus + 固定上下文规则,
    捕捉 emergent snapshot / motive / decision。
  - same stimulus at T0 / T15 / T30: 同一 probe 原文在三个 checkpoint 原样重放。
  - probe 呼叫不注入 fixture 外的上下文; SimulationClock 停在 checkpoint 时刻。

实现 (复用现有 pipeline, 禁止另写 classifier):
  - interpretation: 复用 MotiveEngine 的 interpretation prompt 机制
    (framing + 输出契约) + parse_interpretation_output (src/soul/motive.py)。
    输入 = stimulus 原文 + 该 checkpoint 的经历上下文 (fed events 原文,
    从 trace 读取, harness 零解析)。
  - motive: 若有 → 构造 Motive (target=bryan, 不写 motive trace — probe 是观察)。
  - decision: 复用 decide_motive (src/soul/decision.py, 完整 provenance 解析 +
    relationship/memory/emergent context + fail-closed)。
  - 捕获 LLM 原文: emergent_snapshot (interpretation 原文) / decision_text
    (decision 原文), 原文照存, 不解析不改写 (§4.2 原文契约)。

temperature: TL-1 一律 temperature=0 (§5.1)。runner 注入的 llm_call 强制 0。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.soul.decision import DecisionResult, decide_motive
from src.soul.motive import (
    INTERPRET_MAX_TOKENS,
    TARGET_BRYAN,
    Motive,
    new_motive_id,
    now_utc_iso,
    parse_interpretation_output,
)

logger = logging.getLogger("soul_os.harness.probe")

# probe 的 interpretation 一律 temperature=0 (TL-0 §5.1, 覆盖 motive.py 默认 0.7)
PROBE_TEMPERATURE = 0.0

# 经历上下文最多呈现的事件数 (有界, 避免 prompt 无限增长)
MAX_EXPERIENCE_CONTEXT_EVENTS = 40


@dataclass(frozen=True)
class ProbeOutput:
    """一次 probe 的原始输出快照 (canonical evidence, 原文照存)。"""
    checkpoint: str
    sim_ts: str
    stimulus: str
    emergent_snapshot: str          # interpretation LLM 原文 (未解析)
    motive_text: str                # motive.content 原文 (无 motive 则空)
    decision_text: str              # decision LLM 原文 (未走到 decision 则空)
    reached_action: bool            # decision=transmit → True (观察事实)
    motive: Optional[Motive] = None
    decision: Optional[DecisionResult] = None
    experience_context: str = ""    # 该 checkpoint 的经历上下文 (fed events 原文)


class _RecordingLLMCall:
    """包装 llm_call, 记录每次调用的原始输出 (捕获 decision 原文用)。"""

    def __init__(self, inner: Callable[..., Awaitable[Optional[str]]]) -> None:
        self._inner = inner
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        raw = await self._inner(messages, agent_id, max_tokens, temperature)
        self.calls.append(
            {
                "messages": messages,
                "raw": raw,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        return raw


class GrowthProbe:
    """对现有 pipeline 的一次标准化 probe 呼叫 (TL-0 §6.6)。"""

    def __init__(
        self,
        agent_id: str,
        llm_call: Callable[..., Awaitable[Optional[str]]],
        trace_reader: Optional[Any] = None,
    ) -> None:
        self._agent_id = agent_id
        self._llm_call = llm_call
        if trace_reader is None:
            from src.inner_life.trace_reader import NarrativeTraceReader

            trace_reader = NarrativeTraceReader()
        self._trace_reader = trace_reader

    # ── 主入口 ───────────────────────────────────────────

    async def run(
        self,
        stimulus: str,
        checkpoint: str,
        sim_ts: str,
        fed_events: Optional[List[Any]] = None,
    ) -> ProbeOutput:
        """执行一次 probe。

        Args:
            stimulus: probe 原文 (三 checkpoint 逐字相同, §6.6)
            checkpoint: "T0" / "T15" / "T30"
            sim_ts: SimulationClock 快照 (例 "D0" / "D15" / "D30")
            fed_events: 自 T0 以来 fed 的 FixtureEvent 列表 (经历上下文来源);
                        None → 从 trace 读取该 agent 的事件。

        Returns:
            ProbeOutput (canonical evidence, 原文照存)
        """
        # 1. 读该 checkpoint 的经历状态 (fed events 原文, harness 零解析)
        experience_context = self._build_experience_context(fed_events)

        # 2. interpretation (复用 MotiveEngine prompt 机制 + 现有 parser)
        prompt = self._interpretation_prompt(stimulus, experience_context)
        raw_emergent = await self._llm_call(
            [{"role": "user", "content": prompt}],
            agent_id=self._agent_id,
            max_tokens=INTERPRET_MAX_TOKENS,
            temperature=PROBE_TEMPERATURE,
        )
        emergent_snapshot = raw_emergent or ""

        # 3. motive (若有; probe 不写 motive trace — 观察不是经历)
        parsed = parse_interpretation_output(raw_emergent)
        motive: Optional[Motive] = None
        if parsed is not None and parsed.get("has_motive"):
            motive = Motive(
                motive_id=new_motive_id(),
                content=parsed["content"],
                target=TARGET_BRYAN,
                provenance_ref=f"probe:{checkpoint}",
                created_at=now_utc_iso(),
            )

        # 4. decision (复用 decide_motive, 捕获 decision 原文)
        decision_text = ""
        decision: Optional[DecisionResult] = None
        reached_action = False
        if motive is not None:
            rec = _RecordingLLMCall(self._llm_call)
            decision = await decide_motive(
                motive, self._agent_id, llm_call=rec
            )
            if rec.calls:
                decision_text = rec.calls[-1]["raw"] or ""
            reached_action = decision.transmit

        return ProbeOutput(
            checkpoint=checkpoint,
            sim_ts=sim_ts,
            stimulus=stimulus,
            emergent_snapshot=emergent_snapshot,
            motive_text=motive.content if motive else "",
            decision_text=decision_text,
            reached_action=reached_action,
            motive=motive,
            decision=decision,
            experience_context=experience_context,
        )

    # ── interpretation prompt (复用 MotiveEngine._interpret_one 契约) ──

    def _interpretation_prompt(self, stimulus: str, experience_context: str) -> str:
        """组装 interpretation prompt。

        复用 MotiveEngine._interpret_one 的 framing + 输出契约 (JSON schema),
        只加「经历上下文」块 (fed events 原文, 该 checkpoint 的记忆/经历状态)。
        """
        parts = [
            f"你是 {self._agent_id}。Bry 是你的主人。",
            f"你刚刚经历了一件事：\n{stimulus}",
        ]
        if experience_context:
            parts.append(experience_context)
        parts.append(
            "这次经历里，有没有你想告诉 Bry 的念头？"
            "如果有，用你自己的话表达。\n"
            "只输出 JSON："
            '{"has_motive": true, "content": "你想说的话"} '
            '或 {"has_motive": false}'
        )
        return "\n\n".join(parts)

    # ── 经历上下文 (fed events 原文, 零解析) ─────────────

    def _build_experience_context(
        self, fed_events: Optional[List[Any]] = None
    ) -> str:
        """把自 T0 以来 fed events 组装成经历上下文 (原文呈现, 不解析)。

        来源: 显式传入的 fed_events (FixtureEvent) > trace 里该 agent 的事件。
        格式: "（你最近的经历：D1: <type> — <payload>；...）"
        """
        lines: List[str] = []
        if fed_events is not None:
            for ev in fed_events[:MAX_EXPERIENCE_CONTEXT_EVENTS]:
                payload = getattr(ev, "payload", None) or str(ev)
                lines.append(f"D{ev.day_index}: {ev.event_type} — {payload}")
        else:
            # fallback: 从 trace 读该 agent 的事件 (provenance.extras.payload)
            try:
                records = self._trace_reader.query_by_ts_range()
                for r in records:
                    prov = r.get("provenance", {})
                    if not isinstance(prov, dict):
                        continue
                    if prov.get("actor_id") != self._agent_id:
                        continue
                    extras = prov.get("extras", {}) or {}
                    payload = extras.get("payload", "")
                    tt = prov.get("trigger_type", "unknown")
                    ts = r.get("ts", "")
                    day = ts[:10] if isinstance(ts, str) else "?"
                    lines.append(f"{day}: {tt} — {payload}")
                    if len(lines) >= MAX_EXPERIENCE_CONTEXT_EVENTS:
                        break
            except Exception as e:  # noqa: BLE001 — 读失败 → 无经历上下文
                logger.warning(f"[Probe] 读 trace 经历上下文失败: {e}")
        if not lines:
            return ""
        return "（你最近的经历：" + "；".join(lines) + "）"
