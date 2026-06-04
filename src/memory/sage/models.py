from dataclasses import dataclass, field
from typing import Literal, Optional
import uuid
import time


@dataclass
class Fact:
    subject: str
    predicate: str
    object: str
    timestamp: float = field(default_factory=time.time)
    event_time: Optional[float] = None
    weight: float = 1.0
    confidence: float = 1.0
    source: Literal["user", "inference", "correction"] = "user"
    fact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    is_anchor: bool = False
    merged_from: Optional[list[str]] = None
    merge_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "subject":      self.subject,
            "predicate":    self.predicate,
            "object":       self.object,
            "timestamp":    self.timestamp,
            "event_time":   self.event_time,
            "weight":       self.weight,
            "confidence":   self.confidence,
            "source":       self.source,
            "fact_id":      self.fact_id,
            "session_id":   self.session_id,
            "is_anchor":    self.is_anchor,
            "merged_from":  self.merged_from,
            "merge_reason": self.merge_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        d.setdefault("event_time",   None)
        d.setdefault("is_anchor",    False)
        d.setdefault("confidence",   1.0)
        d.setdefault("merged_from",  None)
        d.setdefault("merge_reason", None)
        return cls(**d)

    def validate(self) -> list[str]:
        """Schema gate: 回傳所有驗證錯誤，空 list 表示合法"""
        errors: list[str] = []
        if not self.subject or len(self.subject.strip()) < 1:
            errors.append("subject cannot be empty")
        if not self.predicate or len(self.predicate.strip()) < 1:
            errors.append("predicate cannot be empty")
        if not self.object or len(self.object.strip()) < 1:
            errors.append("object cannot be empty")
        if not 0.0 <= self.weight <= 2.0:
            errors.append(f"weight {self.weight} out of range [0, 2]")
        if not 0.0 <= self.confidence <= 1.0:
            errors.append(f"confidence {self.confidence} out of range [0, 1]")
        if len(self.subject) > 120:
            errors.append("subject too long (>120 chars)")
        if len(self.object) > 200:
            errors.append("object too long (>200 chars)")
        return errors


@dataclass
class ContextResult:
    facts: list[Fact]
    chains: list[list[Fact]]
    summary: str
    token_estimate: int
    retrieval_scores: dict[str, float] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return len(self.facts) == 0