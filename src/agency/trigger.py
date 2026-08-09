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
其他 5 種 trigger (event / heartbeat / dream / morning / night) 在後續 migration 才加入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


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
