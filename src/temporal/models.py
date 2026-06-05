"""
temporal/models.py — Phase 3.5 vendored from chrono-social-engine v2.2
資料結構：TemporalContext、EmotionalCarryover、MomentumState、AnticipatoryState、PersonaConfig
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo


# ── Time Period Definitions ──────────────────────────────────────────────────

TIME_PERIODS = [
    (4, 7,  "dawn"),
    (7, 12, "morning"),
    (12, 18,"afternoon"),
    (18, 22,"evening"),
    (22, 24,"night"),
    (0, 4,  "deep_night"),
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class EmotionalCarryover:
    intimacy_afterglow: float = 0.0
    unresolved_worry: float = 0.0
    emocional_openness_residue: float = 0.0
    attachment_heat: float = 0.0
    source_event: str = ""
    triggered_at: str = ""
    decay_rate: float = 0.12

    def save(self, agent_id: str, base_path: str = "data/agents") -> None:
        import json
        from pathlib import Path
        path = Path(base_path) / agent_id / "carryover.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, agent_id: str, base_path: str = "data/agents") -> "EmotionalCarryover":
        import json
        from pathlib import Path
        path = Path(base_path) / agent_id / "carryover.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**data)
        return cls()

    def apply_decay(self, elapsed_hours: float) -> "EmotionalCarryover":
        factor = (1 - self.decay_rate) ** elapsed_hours
        new_worry_floor = self.unresolved_worry * 0.25 if self.unresolved_worry > 0 else 0.0
        new_worry = max(new_worry_floor, self.unresolved_worry * factor)
        return EmotionalCarryover(
            intimacy_afterglow=max(0.0, self.intimacy_afterglow * factor),
            unresolved_worry=new_worry,
            emocional_openness_residue=max(0.0, self.emocional_openness_residue * factor),
            attachment_heat=max(0.0, self.attachment_heat * factor),
            source_event=self.source_event,
            triggered_at=self.triggered_at,
            decay_rate=self.decay_rate,
        )


@dataclass
class MomentumState:
    vulnerability_window: bool = False
    emotional_amplification: float = 0.0


@dataclass
class AnticipatoryState:
    preoccupation_flavor: Literal["none", "longing", "worried", "anxious"] = "none"
    expected_presence_prob: float = 0.5
    silence_hours: float = 0.0
    is_overdue: bool = False


@dataclass
class TemporalContext:
    persona_id: str
    current_hour: int
    time_period: str
    silence_hours: float
    carryover: EmotionalCarryover
    momentum: MomentumState
    anticipatory: AnticipatoryState
    deviation_interpretation: str
    emocional_inhibition: float
    stress: int


@dataclass
class PersonaConfig:
    persona_id: str
    decay_rate: float = 0.12
    vulnerability_hour_start: int = 22
    vulnerability_hour_end: int = 4
    vulnerability_inhibition_threshold: float = 0.50
    vulnerability_silence_min: float = 4.0
    worry_resolution_delta: float = 0.6
    attachment_heat_bump: float = 0.1
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Asia/Tokyo"))
