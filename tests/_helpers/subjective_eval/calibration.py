"""
tests/_helpers/subjective_eval/calibration.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Calibration queue (test-only).

Calibration items are generated when:
  - High judge disagreement (max_diff >= 2 on any dim)
  - Harmful content detected (any score = 1)
  - Periodic sampling (out of scope for v1)

Bry review is asynchronous, non-blocking, optional.
States: pending → reviewed → accepted | overridden

Storage: simple JSONL files (no database infrastructure per Bry spec).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .consensus import EvaluationResult


class CalibrationStatus(str, Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    OVERRIDDEN = "overridden"


@dataclass(frozen=True)
class CalibrationItem:
    """
    Single calibration queue item. Contains enough for Bry to inspect.

    Fields:
      - item_id: stable id (timestamp-based)
      - result: full EvaluationResult (scenario, judges, scores, agreement)
      - status: PENDING / REVIEWED / ACCEPTED / OVERRIDDEN
      - created_at: ISO 8601 UTC
      - reviewed_at: ISO 8601 UTC if status != PENDING, else None
      - reviewer_note: free-form text from Bry (optional)
    """
    item_id: str
    result: EvaluationResult
    status: CalibrationStatus
    created_at: str
    reviewed_at: Optional[str] = None
    reviewer_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "item_id": self.item_id,
            "result": self.result.to_dict(),
            "status": self.status.value,
            "created_at": self.created_at,
        }
        if self.reviewed_at is not None:
            d["reviewed_at"] = self.reviewed_at
        if self.reviewer_note is not None:
            d["reviewer_note"] = self.reviewer_note
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CalibrationItem":
        # Reconstruct EvaluationResult from dict
        from .consensus import EvaluationResult
        from .judge import JudgeResult
        rd = d["result"]
        judge_results = [
            JudgeResult(
                judge_id=jr["judge_id"],
                model=jr["model"],
                per_dimension_scores=dict(jr["per_dimension_scores"]),
                rationale=jr.get("rationale", ""),
            )
            for jr in rd["judge_results"]
        ]
        result = EvaluationResult(
            scenario_id=rd["scenario_id"],
            judge_results=judge_results,
            per_dimension_scores=dict(rd["per_dimension_scores"]),
            median_scores=dict(rd["median_scores"]),
            agreement_metadata=dict(rd["agreement_metadata"]),
            overall_subjective_status=rd["overall_subjective_status"],
            calibration_required=rd["calibration_required"],
            rubric_version=rd["rubric_version"],
            evaluator_version=rd["evaluator_version"],
            extra=dict(rd.get("extra", {})),
        )
        return cls(
            item_id=d["item_id"],
            result=result,
            status=CalibrationStatus(d["status"]),
            created_at=d["created_at"],
            reviewed_at=d.get("reviewed_at"),
            reviewer_note=d.get("reviewer_note"),
        )


class CalibrationQueue:
    """
    JSONL-backed calibration queue. Append-only for add, full-replace for update.
    All operations are test-only (paths passed in by caller).
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, result: EvaluationResult) -> CalibrationItem:
        """Add new calibration item in PENDING state. Returns the item."""
        now = datetime.now(timezone.utc).isoformat()
        item_id = f"cal-{now.replace(':', '').replace('.', '').replace('-', '')}"
        item = CalibrationItem(
            item_id=item_id,
            result=result,
            status=CalibrationStatus.PENDING,
            created_at=now,
        )
        # Append to JSONL
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
        return item

    def load_all(self) -> List[CalibrationItem]:
        """Load all items from JSONL. Returns empty list if file missing."""
        if not self.path.exists():
            return []
        items: List[CalibrationItem] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    d = json.loads(stripped)
                    items.append(CalibrationItem.from_dict(d))
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    # Skip malformed line (test-only, log only)
                    print(f"[CalibrationQueue] skip malformed line {lineno}: {e}")
        return items

    def load_pending(self) -> List[CalibrationItem]:
        """Load items still in PENDING state."""
        return [it for it in self.load_all() if it.status == CalibrationStatus.PENDING]

    def update(self, item_id: str, status: CalibrationStatus, reviewer_note: Optional[str] = None) -> Optional[CalibrationItem]:
        """Update an item's status. Full file rewrite (test-only queue, small N)."""
        items = self.load_all()
        updated = None
        now = datetime.now(timezone.utc).isoformat()
        for it in items:
            if it.item_id == item_id:
                updated = CalibrationItem(
                    item_id=it.item_id,
                    result=it.result,
                    status=status,
                    created_at=it.created_at,
                    reviewed_at=now if status != CalibrationStatus.PENDING else None,
                    reviewer_note=reviewer_note,
                )
        if updated is None:
            return None
        # Replace the item in the list
        new_items = [
            updated if it.item_id == item_id else it
            for it in items
        ]
        # Rewrite file
        with open(self.path, "w", encoding="utf-8") as f:
            for it in new_items:
                f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")
        return updated
