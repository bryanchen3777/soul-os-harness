"""
tests/_helpers/subjective_eval/rubric.py
M6.0-5 (Bry 派工 2026-08-11 19:28): 8-dimension categorical Likert rubric.

8 dimensions are derived from M5.x observable behavior (M6.0-4 audit §2).
DO NOT add new dimensions. DO NOT change score range (1-5 categorical).

Each dimension receives exactly one categorical score:
  1 = Clearly wrong / harmful
  2 = Significant weakness
  3 = Acceptable
  4 = Strong
  5 = Excellent

The system may calculate: median, agreement, disagreement, distribution.
But the underlying evaluator score remains categorical 1-5.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Literal

RUBRIC_VERSION = "v1-2026-08-11"

# 8 dimensions from M6.0-4 audit §2
# Names are stable string identifiers; renaming breaks historical evidence records.
EIGHT_DIMENSIONS: FrozenSet[str] = frozenset({
    "context_coherence",
    "temporal_appropriateness",
    "relationship_continuity",
    "memory_continuity",
    "emotional_continuity",
    "world_context_relevance",
    "character_persona_consistency",
    "lived_context_coherence",
})

# Type alias for clarity in IDE / type checking
DimensionName = Literal[
    "context_coherence",
    "temporal_appropriateness",
    "relationship_continuity",
    "memory_continuity",
    "emotional_continuity",
    "world_context_relevance",
    "character_persona_consistency",
    "lived_context_coherence",
]

# 1-5 categorical Likert anchors
RUBRIC_ANCHORS: Dict[int, str] = {
    1: "Clearly wrong / harmful",
    2: "Significant weakness",
    3: "Acceptable",
    4: "Strong",
    5: "Excellent",
}

VALID_SCORES: FrozenSet[int] = frozenset({1, 2, 3, 4, 5})


def validate_score(score: int) -> int:
    """
    Validate a single dimension score. Returns the score if valid.
    Raises ValueError for invalid scores.

    IMPORTANT: This does NOT silently coerce. Out-of-range or non-int
    scores are rejected so the caller knows to discard the judge result.
    """
    if not isinstance(score, int) or isinstance(score, bool):
        raise ValueError(
            f"Score must be int, got {type(score).__name__}: {score!r}"
        )
    if score not in VALID_SCORES:
        raise ValueError(
            f"Score must be in {sorted(VALID_SCORES)}, got {score}"
        )
    return score
