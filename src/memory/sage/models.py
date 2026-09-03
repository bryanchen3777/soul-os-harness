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
    # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B + 防呆規則):
    # 標記這條事實原始是哪兩個 entity 之間的對話, 格式 "<user_id>:<agent_id>"
    # 例: "bryan:agent_ruka" = Bry user 跟 agent_ruka 的對話事實
    # prefetch 時, middleware 拿 self agent_id 組成 allowed_pairs, 過濾掉
    # source_pair 非空且不在 allowed_pairs 內的事實 (避免 ram/miku/yua 撈到
    # Bry-mai/Bry-ruka 的私域喇稱記憶)
    # Bry 拍板防呆: 空 source_pair (既有 5040 facts 沒標記) 一律視為可見, 不過濾
    source_pair: Optional[str] = None
    # M5.4-5.2 (Bry 派工 2026-08-09 18:38): inner_life_event_id 整合 M5.4-5.1 Inner Life Foundation
    # Optional: None for existing 5040 facts (pre-M5.4-5.1) 跟無 inner_life_writer 的 case
    # 設值: SAGELiteProvider 配 optional inner_life_writer 時, 每個 Fact 帶對應 InnerLifeEvent.event_id
    # 不影響 M5.3 retrieval: 純 metadata, 不參與 scoring / dedup / threshold
    # 不影響 SAGE: 不改 extraction logic, 只是 attach 一個 canonical reference
    inner_life_event_id: Optional[str] = None
    # MR-1/MR-2 (Temporal Memory & Mem0 Primitives): 時序維度
    # valid_from: 事實開始有效的時間 (unix float)。None = 未知 (理論殘留, 遷移後不應存在)。
    #   - 遷移回填 = timestamp (寫入時間, event_time 100% NULL 不可用)。
    #   - primitives.add_fact 寫入前若未設置, 預設 = time.time()。
    # invalidated_at: 事實失效的時間。None = 當前仍有效 (永不過期)。
    #   - 軟刪 (invalidate_fact) 設置此欄位; 硬刪 (remove_fact) 不經此欄位。
    #   - 半開區間 [valid_from, invalidated_at): valid_from <= t < invalidated_at 時可見。
    valid_from: Optional[float] = None
    invalidated_at: Optional[float] = None

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
            "source_pair":  self.source_pair,
            "inner_life_event_id": self.inner_life_event_id,
            "valid_from":   self.valid_from,
            "invalidated_at": self.invalidated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fact":
        d.setdefault("event_time",   None)
        d.setdefault("is_anchor",    False)
        d.setdefault("confidence",   1.0)
        d.setdefault("merged_from",  None)
        d.setdefault("merge_reason", None)
        d.setdefault("source_pair",  None)
        # M5.4-5.2: backward compat for pre-integration Facts (M5.4-5.1 前的 facts 都沒有這個欄位)
        d.setdefault("inner_life_event_id", None)
        # MR-1/MR-2: backward compat for pre-v7 Facts (v6 及以下的 facts 沒有時序欄位)
        d.setdefault("valid_from", None)
        d.setdefault("invalidated_at", None)
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