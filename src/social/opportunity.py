"""
src/social/opportunity.py — SI-3 Social Opportunity (社交機會) 結構與緩存池

工单: TICKET-SI-3-PHASE-1 (Dual-Brain Edition)
设计: docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md (v1.0, 2026-09-03, 已鎖定)

核心哲學 (SI-3 §1.1 Ambient ≠ Inert):
  社交事件不是直接引發回覆的「觸發器」, 而是提供給靈魂的「潛在機會」。
  世界發生事件 → 靈魂評估顯著性 (Salience) → 產生帶生命週期 (TTL) 的
  SocialOpportunity → 最終由靈魂自主意志 (SM-4 Volition) 決定是否行動。

Frozen Invariants (SI-3 §2):
  - No Cascading Volition: 本模組只產生「機會」, 絕不直接觸發 transmit,
    亦不繞過 SM-4 決策管道 (Perception → Salience → Motive → Volition)。
  - Fail-Closed: 已過期之機會絕不餵入 Motive 候選 — get_active_opportunities
    自動修剪, 過期即刪, 避免「昨天別人聊餅乾, 今天突然回覆」的非人僵硬行為。
  - No Vector DB: 純記憶體 dict 緩存, 0 外部依賴。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SocialOpportunity:
    """一個值得靈魂考慮的短期社交話題或互動機會 (SI-3 §3.1)。

    Attributes:
        opportunity_id: 唯一識別碼 (格式 "opp_<uuid4_hex[:12]>").
        source_event_id: 關聯之 SocialWorldEvent.novelty_id.
        actor_id: 行為主體 (發言者).
        space_id: 發生空間 ("lounge" | "soul_wall").
        topic: 話題關鍵字 (<= 20 chars).
        summary: 簡述 (<= 100 chars).
        created_at: 建立時間戳 (epoch seconds).
        ttl_seconds: 生命週期秒數, 預設 300 秒 (5 分鐘).
        salience_level: 顯著性 ("subtle" | "noticeable" | "prominent").
        world_occurrence_id: 共享事件關聯鍵 (SI-3 §4, 讀側聚合用).
        metadata: 擴充欄位.
    """

    opportunity_id: str
    source_event_id: str
    actor_id: str
    space_id: str
    topic: str
    summary: str
    created_at: float
    ttl_seconds: float = 300.0
    salience_level: str = "noticeable"
    world_occurrence_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: float) -> bool:
        """是否已過期 (邊界: now >= created_at + ttl_seconds 即過期)。"""
        return now >= (self.created_at + self.ttl_seconds)


class SocialOpportunityBuffer:
    """管理單個靈魂當前有效的社交機會 (SI-3 §3.2)。

    - 容量上限: 最多容納 max_capacity 筆 (超出時淘汰 created_at 最舊者)。
    - 過期自動修剪: 每次 get_active_opportunities(now) 自動刪除過期條目。
    - Fail-Closed: 已過期之機會絕不返回。
    """

    def __init__(self, max_capacity: int = 5) -> None:
        if (
            not isinstance(max_capacity, int)
            or isinstance(max_capacity, bool)
            or max_capacity < 1
        ):
            raise ValueError(
                f"max_capacity 必須為 >=1 的 int, got: {max_capacity!r}"
            )
        self.max_capacity = max_capacity
        self._opportunities: Dict[str, SocialOpportunity] = {}

    def add_opportunity(self, opp: SocialOpportunity) -> None:
        """加入或更新機會: 同 opportunity_id 覆蓋; 超容量淘汰最舊者。"""
        self._opportunities[opp.opportunity_id] = opp
        if len(self._opportunities) > self.max_capacity:
            oldest_id = min(
                self._opportunities,
                key=lambda oid: self._opportunities[oid].created_at,
            )
            del self._opportunities[oldest_id]

    def get_active_opportunities(self, now: float) -> List[SocialOpportunity]:
        """過濾並修剪已過期項目, 回傳有效清單 (按 created_at 升序)。"""
        expired_ids = [
            oid
            for oid, opp in self._opportunities.items()
            if opp.is_expired(now)
        ]
        for oid in expired_ids:
            del self._opportunities[oid]
        return sorted(
            self._opportunities.values(),
            key=lambda opp: opp.created_at,
        )

    def clear(self) -> None:
        """重置緩存。"""
        self._opportunities.clear()

    def __len__(self) -> int:
        return len(self._opportunities)


__all__ = ["SocialOpportunity", "SocialOpportunityBuffer"]
