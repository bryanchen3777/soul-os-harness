"""
src/inner_life/submission_gate.py — Elevation Submission Gate（SG-1）

SG-1 定稿修复：在「InnerLifeEvent → soul-elevation consume」之间加一道
**Submission Gate**，验证提交的 event_id 是 canonical InnerLifeEvent（由
InnerLifeWriter 创建、producer 合法、trace 佐证存在），伪造 id **fail-closed**
（拒绝，不 consume）。Gate 只准 ``consume()``（destination=pattern，consolidation
输出），**永不 ``elevate()``**（灵魂结构升华不在本 Gate 的授权范围内）。

背景（SG-1 审计已确认，直接采信）：
  - P1：``src/world/elevation_adapter.py`` 直通 adapter 对每个 WorldEvent
    （无 whitelist）直接 consume()，bypass InnerLifeEvent。
  - 无 Submission Gate 组件（要求的「id→canonical InnerLifeEvent→producer→
    InnerLifeWriter 验证、伪造 fail-closed」不存在）。
  - Proactive DM 已自动 consume（``run_server.py:995``），偏离定稿
    「不自動/暫不提交」。
  - elevate() 零调用（系统只产 pattern，正确）。

本模块定位（照工单关键决策 #3）：
  - **验证链（fail-closed，任一失败即拒绝）**：
      1. ``event_id`` 格式合法（``validate_event_id``，32-hex）
      2. 由 InnerLifeWriter 创建（``writer.is_event_known``）——伪造 id 在此拒绝
      3. canonical InnerLifeEvent 存在（``writer.get_event``）
      4. trace 佐证（可选）：配置了 ``trace_reader`` 时要求 trace 记录存在
      5. producer 合法：``provenance.trigger_type`` 在合法 producer 集合内
         （8 个 TRIGGER_TYPE_* 常量 + ``world:*`` 前缀，M5.9-3 WorldInnerLifeAdapter）
  - **只 consume 不 elevate**：``submit()`` 验证通过后调 ``run_elevation``
    （内部只 ``engine.consume()``，产 pattern 候选节点）；本模块**不暴露也不调用**
    ``elevate()``（测试用 AST 红线锁定）。
  - **不改任何 frozen contract**：不改 InnerLifeEvent schema（M5.4-5.1）、
    TriggerEnvelope（M5.2-F）、Agency 4 stages / 4 handlers、SAGE 写入逻辑、
    InnerLifeWriter、NarrativeTrace。
  - **失败隔离**：``submit()`` 异常时记录 warning 并返回 []（不 raise，
    不阻断调用方主路径）。

用法（run_server.py 接线，照工单关键决策 #3）：

    gate = SubmissionGate(
        writer=inner_life_writer,
        trace_reader=NarrativeTraceReader(),
    )
    # InnerLifeEvent 写入之后（producer 侧）：
    gate.submit(event.event_id)          # 验证通过 → consume()；伪造 → fail-closed []
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, Sequence

from .event import (
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
    TRIGGER_TYPE_MEMORY_FACT,
    TRIGGER_TYPE_SYSTEM,
    TRIGGER_TYPE_USER_MESSAGE,
)
from .identity import IdentityValidationError, validate_event_id

if TYPE_CHECKING:  # 仅类型标注，运行时不执行
    from .event import InnerLifeEvent
    from .trace_reader import NarrativeTraceReader
    from .writer import InnerLifeWriter

logger = logging.getLogger("soul_os.inner_life.submission_gate")

# 合法 producer 的 trigger_type 集合（8 个既有 TRIGGER_TYPE_* 常量）。
# M5.9-3 WorldInnerLifeAdapter 产生的 ``world:<type>``（如 world:news_event）
# 是第 9 类合法 producer，单独用前缀判断（见 _is_valid_producer_trigger）。
VALID_PRODUCER_TRIGGER_TYPES: frozenset = frozenset({
    TRIGGER_TYPE_USER_MESSAGE,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
    TRIGGER_TYPE_MEMORY_FACT,
    TRIGGER_TYPE_SYSTEM,
})

# M5.9-3 WorldInnerLifeAdapter 的 trigger_type 前缀（world:<type>）。
WORLD_TRIGGER_PREFIX = "world:"


def _is_valid_producer_trigger(trigger_type: str) -> bool:
    """producer 合法判断：8 个 TRIGGER_TYPE_* 常量 或 ``world:*`` 前缀。

    ``world:*`` 是 M5.9-3 WorldInnerLifeAdapter 产生的 trigger_type
    （``world:{world_event.type}``，如 world:news_event / world:calendar_event），
    是 SG-1 解冻后 world 事件走 InnerLifeEvent 正确路径的合法 producer。
    """
    if not isinstance(trigger_type, str) or not trigger_type:
        return False
    return (
        trigger_type in VALID_PRODUCER_TRIGGER_TYPES
        or trigger_type.startswith(WORLD_TRIGGER_PREFIX)
    )


# ─────────────────────────────────────────────────────────────────────
# 验证结果（observability）
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SubmissionVerdict:
    """Submission Gate 验证结果。

    - ``accepted=True``：event_id 通过全部验证，可 consume。
    - ``accepted=False``：fail-closed 拒绝，附 ``reason``（observability）。
    - ``event``：验证通过时的 canonical InnerLifeEvent（None 表示拒绝）。
    """
    accepted: bool
    reason: str
    event: Optional["InnerLifeEvent"] = None


# ─────────────────────────────────────────────────────────────────────
# Submission Gate
# ─────────────────────────────────────────────────────────────────────


class SubmissionGate:
    """Elevation Submission Gate（SG-1）。

    验证 ``event_id → canonical InnerLifeEvent → producer 合法 → 由
    InnerLifeWriter 创建``，伪造 id **fail-closed**（拒绝，不 consume）。
    只准 ``consume()``（destination=pattern），**永不 ``elevate()``**。

    Lifecycle:
      1. __init__: 注入 writer（必填，InnerLifeWriter 是 sole canonical creator）
         + 可选 trace_reader / llm / store_dir / agent_id / enabled
      2. verify(event_id): 纯验证（无副作用），返回 SubmissionVerdict
      3. submit(event_id, memory_facts): 验证通过 → consume()；失败 → []（fail-closed）
    """

    def __init__(
        self,
        writer: "InnerLifeWriter",
        *,
        trace_reader: Optional["NarrativeTraceReader"] = None,
        llm: Any = None,
        store_dir: Optional[Any] = None,
        agent_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            writer: 必填，InnerLifeWriter（sole canonical InnerLifeEvent creator，
                    per M5.4-5.1 frozen）。Gate 用它验证 event_id 是否由它创建。
            trace_reader: 可选 NarrativeTraceReader。提供时 verify 额外要求
                trace 记录存在（fail-closed 更严格）；None 则跳过 trace 佐证
                （writer per-instance 权威已足够）。
            llm: 可选 ElevationLLM 实现（透传给 run_elevation 的 consume）。
            store_dir: 可选 store 目录（透传，缺省 data_root()/elevation）。
            agent_id: 可选归属 agent（灵魂本体）覆盖。
            enabled: False 时 submit() 直接返回 []（no-op，不验证不 consume）。
        """
        if writer is None:
            raise ValueError(
                "writer 必填, InnerLifeWriter is sole canonical creator "
                "(per M5.4-5.1 frozen)"
            )
        self._writer: "InnerLifeWriter" = writer
        self._trace_reader: Optional["NarrativeTraceReader"] = trace_reader
        self._llm = llm
        self._store_dir = store_dir
        self._agent_id = agent_id
        self.enabled = enabled
        # Observability counters
        self._stats = {
            "submissions": 0,
            "accepted": 0,
            "rejected": 0,
            "consumed": 0,
            "consume_failures": 0,
        }
        logger.info(
            f"[SubmissionGate] initialized "
            f"trace_reader={'yes' if trace_reader is not None else 'no'} "
            f"enabled={enabled}"
        )

    # ─────────────────────────────────────────────────────────────
    # 验证链（fail-closed，无副作用）
    # ─────────────────────────────────────────────────────────────

    def verify(self, event_id: str) -> SubmissionVerdict:
        """验证 event_id 是否可提交升华（纯验证，不 consume）。

        验证链（任一失败 → REJECT，fail-closed）：
          1. ``event_id`` 格式合法（32-hex）
          2. 由 InnerLifeWriter 创建（``writer.is_event_known``）——伪造 id 拒绝
          3. canonical InnerLifeEvent 存在（``writer.get_event``）
          4. trace 佐证（可选）：配置了 trace_reader 时要求 trace 记录存在
          5. producer 合法：trigger_type 在合法集合内

        Returns:
            SubmissionVerdict（accepted=True 时带 canonical event）。
        """
        # 1. 格式合法
        try:
            validate_event_id(event_id)
        except IdentityValidationError as exc:
            return SubmissionVerdict(
                accepted=False,
                reason=f"invalid event_id format: {exc}",
            )

        # 2. 由 InnerLifeWriter 创建（伪造 id 在此 fail-closed）
        if not self._writer.is_event_known(event_id):
            return SubmissionVerdict(
                accepted=False,
                reason=(
                    f"event_id {event_id[:12]}... 不是由 InnerLifeWriter 创建 "
                    f"(forged / unknown id) — fail-closed"
                ),
            )

        # 3. canonical InnerLifeEvent 存在
        event = self._writer.get_event(event_id)
        if event is None:
            return SubmissionVerdict(
                accepted=False,
                reason=f"event_id {event_id[:12]}... 在 inner_life store 找不到",
            )

        # 4. trace 佐证（可选，配置了 trace_reader 才检查）
        if self._trace_reader is not None:
            records = self._trace_reader.query_by_event_id(event_id)
            if not records:
                return SubmissionVerdict(
                    accepted=False,
                    reason=(
                        f"event_id {event_id[:12]}... 在 narrative trace 找不到 "
                        f"(trace 佐证缺失) — fail-closed"
                    ),
                )

        # 5. producer 合法
        trigger_type = event.provenance.trigger_type
        if not _is_valid_producer_trigger(trigger_type):
            return SubmissionVerdict(
                accepted=False,
                reason=(
                    f"producer trigger_type {trigger_type!r} 不在合法集合 "
                    f"{sorted(VALID_PRODUCER_TRIGGER_TYPES)} + world:* — fail-closed"
                ),
            )

        return SubmissionVerdict(
            accepted=True,
            reason=f"event_id {event_id[:12]}... 验证通过 (canonical InnerLifeEvent)",
            event=event,
        )

    # ─────────────────────────────────────────────────────────────
    # 提交（验证通过 → consume；失败 → fail-closed []）
    # ─────────────────────────────────────────────────────────────

    def submit(
        self,
        event_id: str,
        memory_facts: Sequence[Any] = (),
        *,
        agent_id: Optional[str] = None,
    ) -> List[Any]:
        """验证 event_id → 通过则 consume()（destination=pattern），否则 fail-closed []。

        Args:
            event_id: 要提交升华的 canonical InnerLifeEvent.event_id。
            memory_facts: 同一叙事上下文的 v1 Memory / SAGE Fact 序列（正文来源，
                透传给 run_elevation）。
            agent_id: 可选归属 agent 覆盖（缺省用构造时注入的 agent_id）。

        Returns:
            产出的 ``ElevationNode`` 列表（consume 产 pattern 候选节点）。
            验证失败 → []（fail-closed，不 consume）。异常 → []（失败隔离，
            不 raise，不阻断调用方主路径）。
        """
        if not self.enabled:
            return []

        self._stats["submissions"] += 1

        verdict = self.verify(event_id)
        if not verdict.accepted:
            self._stats["rejected"] += 1
            logger.warning(
                f"[SubmissionGate] REJECTED (fail-closed): {verdict.reason}"
            )
            return []

        self._stats["accepted"] += 1

        # 只 consume（destination=pattern），永不 elevate。
        # run_elevation 内部只调 engine.consume()（产 pattern 候选节点）。
        try:
            from .elevation_adapter import run_elevation

            nodes = run_elevation(
                verdict.event,
                memory_facts,
                llm=self._llm,
                store_dir=self._store_dir,
                agent_id=agent_id or self._agent_id,
            )
            self._stats["consumed"] += 1
            if nodes:
                logger.info(
                    f"[SubmissionGate] consume ✓ event_id={event_id[:12]}... "
                    f"nodes={len(nodes)} (pattern only, 不 elevate)"
                )
            return nodes
        except ImportError:
            # soul-elevation 未安装 → consume 不可用，fail-closed（不产节点）
            self._stats["consume_failures"] += 1
            logger.warning(
                "[SubmissionGate] soul-elevation 未安装, consume 停用 (fail-closed)"
            )
            return []
        except Exception as exc:  # noqa: BLE001 — 失败隔离：不阻断调用方主路径
            self._stats["consume_failures"] += 1
            logger.warning(
                f"[SubmissionGate] consume failed (不影響主路徑): "
                f"{type(exc).__name__}: {exc}"
            )
            return []

    def get_stats(self) -> dict:
        """Observability counters."""
        return dict(self._stats)


__all__ = [
    "VALID_PRODUCER_TRIGGER_TYPES",
    "WORLD_TRIGGER_PREFIX",
    "SubmissionGate",
    "SubmissionVerdict",
    "_is_valid_producer_trigger",
]
