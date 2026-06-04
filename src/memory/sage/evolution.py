from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Literal, Optional

from .graph_store import GraphStore
from .models import Fact

DecayAction = Literal["decay", "prune", "merge", "conflict_flag"]

DECAY_FLOOR = 0.08   # 記憶最低保留值，防止集體失憶
ANCHOR_DECAY_FLOOR = 0.5  # anchor 有更高的 floor

@dataclass
class EvolutionEvent:
    action: str
    fact_id: str
    target_id: Optional[str]
    old_weight: float
    new_weight: float
    timestamp: float = field(default_factory=time.time)
    reason: str = ""

    def to_log_line(self) -> str:
        return (
            f"[{self.action.upper()}] fact={self.fact_id[:8]} "
            f"w:{self.old_weight:.2f}→{self.new_weight:.2f} "
            f"reason={self.reason}"
        )


_DECAY_RATES: dict[str, float] = {
    "user":        0.03,
    "inference":   0.08,
    "correction":  0.02,
}

from .writer import _PREDICATE_SYNONYMS

_CONFLICT_PREDICATES: list[frozenset[str]] = [
    frozenset({"likes",    "hates"}),
    frozenset({"likes",    "dislikes"}),
    frozenset({"loves",    "hates"}),
    frozenset({"is",       "was"}),
    frozenset({"lives_in", "lived_in"}),
    frozenset({"喜歡",      "討厭"}),
]


class MemoryEvolution:
    """Self-Correction Loop v6: decay floor + merge lineage + adaptive threshold"""

    PRUNE_THRESHOLD  = 0.03
    CONFLICT_PENALTY = 0.3

    def __init__(self, graph_store: GraphStore):
        self.store = graph_store
        self._log: list[EvolutionEvent] = []

    # ── 主要公開 API ──────────────────────────────────────────

    def apply_correction(
        self,
        fact_id: str,
        action: DecayAction,
        target_id: Optional[str] = None,
        delta: Optional[float] = None,
        reason: str = "manual",
    ) -> bool:
        if action == "decay":
            return self._decay(fact_id, delta, reason)
        elif action == "prune":
            return self._prune(fact_id, reason)
        elif action == "merge":
            return self._merge(fact_id, target_id, reason) if target_id else False
        elif action == "conflict_flag":
            return self._conflict_flag(fact_id, reason)
        return False

    def run_scheduled_decay(
        self,
        age_days_threshold: float = 7.0,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """
        定期排程執行 decay/prune，anchor facts 不受影響。
        回傳：{"decayed": n, "pruned": n, "skipped": n, "anchors_protected": n}
        """
        now = time.time()
        stats = {"decayed": 0, "pruned": 0, "skipped": 0, "anchors_protected": 0}

        for fact in self.store.get_all_facts(min_weight=0.0):
            if fact.is_anchor:
                stats["anchors_protected"] += 1
                continue

            age_days = (now - fact.timestamp) / 86400
            if age_days <= age_days_threshold:
                stats["skipped"] += 1
                continue

            rate = _DECAY_RATES.get(fact.source, 0.05)
            extra_days = max(0, age_days - age_days_threshold)
            effective_rate = min(0.5, rate * (1 + extra_days / 30))
            new_weight = fact.weight * (1.0 - effective_rate)

            # DECAY_FLOOR 保護
            if new_weight < DECAY_FLOOR:
                new_weight = DECAY_FLOOR

            if dry_run:
                stats["decayed"] += 1
                continue

            if new_weight < self.PRUNE_THRESHOLD:
                self._prune(fact.fact_id, reason="scheduled_decay_threshold")
                stats["pruned"] += 1
            else:
                self.store.update_weight(fact.fact_id, new_weight)
                self._record(EvolutionEvent(
                    action="decay",
                    fact_id=fact.fact_id,
                    target_id=None,
                    old_weight=fact.weight,
                    new_weight=new_weight,
                    reason=f"scheduled(age={age_days:.1f}d,rate={effective_rate:.2f})",
                ))
                stats["decayed"] += 1

        return stats

    def detect_conflicts(self) -> list[tuple[Fact, Fact]]:
        conflicts: list[tuple[Fact, Fact]] = []
        all_facts = self.store.get_all_facts(min_weight=self.PRUNE_THRESHOLD)

        by_subject: dict[str, list[Fact]] = {}
        for f in all_facts:
            by_subject.setdefault(f.subject.lower(), []).append(f)

        for subj_facts in by_subject.values():
            for i, fa in enumerate(subj_facts):
                for fb in subj_facts[i + 1:]:
                    if self._are_conflicting(fa, fb):
                        conflicts.append((fa, fb))

        return conflicts

    def auto_resolve_conflicts(self) -> int:
        resolved = 0
        for fa, fb in self.detect_conflicts():
            loser = fa if fa.weight <= fb.weight else fb
            self._conflict_flag(loser.fact_id, reason="auto_resolve")
            resolved += 1
        return resolved

    @property
    def evolution_log(self) -> list[EvolutionEvent]:
        return list(self._log)

    def log_summary(self) -> str:
        if not self._log:
            return "No evolution events recorded."
        counts: dict[str, int] = {}
        for e in self._log:
            counts[e.action] = counts.get(e.action, 0) + 1
        parts = [f"{a}×{n}" for a, n in sorted(counts.items())]
        return f"Evolution log: {len(self._log)} events — " + ", ".join(parts)

    # ── 內部操作 ──────────────────────────────────────────────

    def _decay(
        self,
        fact_id: str,
        delta: Optional[float],
        reason: str,
    ) -> bool:
        fact = self.store.get_fact(fact_id)
        if not fact:
            return False
        rate = delta if delta is not None else _DECAY_RATES.get(fact.source, 0.05)
        new_weight = max(0.0, fact.weight - rate)
        # DECAY_FLOOR 保護
        if fact.is_anchor:
            new_weight = max(ANCHOR_DECAY_FLOOR, new_weight)
        else:
            new_weight = max(DECAY_FLOOR, new_weight)
        # 衰減緩衝：低於 floor 時額外補充 0.02，避免完全丢失
        if new_weight <= DECAY_FLOOR:
            new_weight = min(DECAY_FLOOR + 0.02, fact.weight)
        if new_weight < self.PRUNE_THRESHOLD:
            return self._prune(fact_id, reason=f"{reason}→below_threshold")
        ok = self.store.update_weight(fact_id, new_weight)
        if ok:
            self._record(EvolutionEvent("decay", fact_id, None,
                                        fact.weight, new_weight, reason=reason))
        return ok

    def _prune(self, fact_id: str, reason: str = "manual") -> bool:
        fact = self.store.get_fact(fact_id)
        old_w = fact.weight if fact else 0.0
        ok = self.store.remove_fact(fact_id)
        if ok:
            self._record(EvolutionEvent("prune", fact_id, None,
                                        old_w, 0.0, reason=reason))
        return ok

    def _merge(
        self,
        source_id: str,
        target_id: str,
        reason: str,
    ) -> bool:
        source = self.store.get_fact(source_id)
        target = self.store.get_fact(target_id)
        if not source or not target:
            return False

        if self._are_conflicting(source, target):
            self._conflict_flag(source_id, reason=f"merge_rejected_conflict→{reason}")
            return False

        # Post-merge edge 衝突檢查
        post_merge_facts = self.store.search_by_entity(target.subject)
        for existing in post_merge_facts:
            if existing.fact_id in (source_id, target_id):
                continue
            if self._are_conflicting(existing, source):
                loser = existing if existing.weight <= source.weight else source
                self._conflict_flag(
                    loser.fact_id,
                    reason=f"post_merge_edge_conflict:{target_id[:8]}"
                )
                self._record(EvolutionEvent(
                    action="conflict_flag",
                    fact_id=loser.fact_id,
                    target_id=target_id,
                    old_weight=loser.weight,
                    new_weight=max(0.0, loser.weight - self.CONFLICT_PENALTY),
                    reason="MEMORY_CONFLICT_RESOLVED",
                ))

        merged_weight = min(2.0, target.weight + source.weight * 0.5)
        self.store.update_weight(target_id, merged_weight)

        # E-2: Lineage tracking via update_merge_lineage
        self.store.update_merge_lineage(target_id, [source_id], reason)

        self._record(EvolutionEvent("merge", source_id, target_id,
                                    source.weight, 0.0, reason=reason))
        return self._prune(source_id, reason=f"merged_into:{target_id[:8]}")

    def _conflict_flag(self, fact_id: str, reason: str = "conflict") -> bool:
        fact = self.store.get_fact(fact_id)
        if not fact:
            return False
        new_weight = max(0.0, fact.weight - self.CONFLICT_PENALTY)
        if new_weight < self.PRUNE_THRESHOLD:
            return self._prune(fact_id, reason=f"{reason}→pruned")
        ok = self.store.update_weight(fact_id, new_weight)
        if ok:
            self._record(EvolutionEvent("conflict_flag", fact_id, None,
                                        fact.weight, new_weight, reason=reason))
        return ok

    def _are_conflicting(self, fa: Fact, fb: Fact) -> bool:
        if fa.subject.lower() != fb.subject.lower():
            return False
        fa_norm = _PREDICATE_SYNONYMS.get(fa.predicate, fa.predicate)
        fb_norm = _PREDICATE_SYNONYMS.get(fb.predicate, fb.predicate)
        pair = frozenset({fa_norm, fb_norm})
        return pair in _CONFLICT_PREDICATES

    def _record(self, event: EvolutionEvent) -> None:
        self._log.append(event)
        if len(self._log) > 1000:
            self._log = self._log[-800:]