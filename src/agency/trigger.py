"""
src/agency/trigger.py — Soul OS M5.2-G TriggerEnvelope

TriggerEnvelope 是 Scheduler → Agency 的 bridge input。
跟 Perception result 完全不同的語意:
  - Perception = "Soul 注意到了" (有 accepted / priority)
  - Trigger    = "Scheduler 提議現在 act" (有 trigger_type / reason)

Bry 拍板 2026-08-08 M5.2-F:
  不要用 fake perception 偽裝 scheduler trigger。
  TriggerEnvelope 是獨立的 dataclass。

M5.2-G Phase 1 限定 trigger_type="proactive_dm"。
(M5.2-H Phase 1/2/3 已將 event / dream / morning / night 全部 migrate, heartbeat 仍 legacy 走 _publish_agent_intent)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

_logger = logging.getLogger("src.agency.trigger")


@dataclass
class TriggerEnvelope:
    """
    M5.2-F frozen contract: Scheduler → Agency bridge input.

    跟 Perception result 不同:
      - TriggerEnvelope.trigger_type = "proactive_dm" | "event" | ... (timing/intent)
      - Perception.accepted = True/False (perception decision)

    語意:
      Scheduler 提出一個 trigger, 表示「現在是提議 act 的時機」。
      Agency 收到後跑 4 個 stage 決定要不要真的 act。
    """
    trigger_type: str          # "proactive_dm" (M5.2-G) | "event" | "heartbeat" | "dream" | "morning" | "night"
    agent_id: str              # 誰應該 act
    reason: str                # 為什麼現在 (例: "scheduler.proactive_dm")
    elapsed_mins: float = 0.0  # 距上次同類 trigger 的分鐘數
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    extra: Dict[str, Any] = field(default_factory=dict)  # trigger-specific context

    @classmethod
    def from_payload(
        cls,
        payload: Any,
        logger_name: Optional[str] = None,
    ) -> Optional["TriggerEnvelope"]:
        """
        M5.2-Q-4 (Bry 拍板 2026-08-08): 從 AGENCY_TRIGGER payload 構造 TriggerEnvelope.

        Q-3 feasibility 確認 4 handler 各自的 _parse_envelope() 100% 等價
        (除了 logger prefix),收斂成 single source of truth.

        Failure modes (跟原 4 個 _parse_envelope 行為 100% 一致):
          - payload 不是 dict → log warning + return None
          - trigger_type 缺/非字串 → log warning + return None
          - agent_id 缺/非字串 → log warning + return None
          - reason 缺/非字串 → log warning + return None
          - elapsed_mins 缺/非數字 → silent coerce 0.0
          - timestamp 缺/非字串/格式錯 → silent coerce None
          - extra 缺/非 dict → silent coerce {}

        Args:
            payload: AGENCY_TRIGGER event.payload
            logger_name: log message prefix (例如 "[AgencyTriggerHandler]"),
                         用於保留各 handler 的 debuggability

        Returns:
            TriggerEnvelope if all required fields valid, else None
        """
        prefix = logger_name + " " if logger_name else ""

        if not isinstance(payload, dict):
            _logger.warning(f"{prefix}payload 不是 dict: {type(payload).__name__}")
            return None

        trigger_type = payload.get("trigger_type")
        agent_id = payload.get("agent_id")
        reason = payload.get("reason")

        if not isinstance(trigger_type, str):
            _logger.warning(f"{prefix}trigger_type 缺/非字串: {trigger_type!r}")
            return None
        if not isinstance(agent_id, str):
            _logger.warning(f"{prefix}agent_id 缺/非字串: {agent_id!r}")
            return None
        if not isinstance(reason, str):
            _logger.warning(f"{prefix}reason 缺/非字串: {reason!r}")
            return None

        elapsed_mins = payload.get("elapsed_mins", 0.0)
        if not isinstance(elapsed_mins, (int, float)):
            elapsed_mins = 0.0

        # timestamp optional, default = None
        timestamp_str = payload.get("timestamp")
        timestamp = None
        if isinstance(timestamp_str, str):
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                timestamp = None

        extra = payload.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}

        return cls(
            trigger_type=trigger_type,
            agent_id=agent_id,
            reason=reason,
            elapsed_mins=float(elapsed_mins),
            timestamp=timestamp if timestamp else None,
            extra=extra,
        )
