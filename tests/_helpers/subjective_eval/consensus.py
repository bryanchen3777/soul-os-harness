"""
tests/_helpers/subjective_eval/consensus.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Consensus aggregation.

For each of the 8 dimensions:
  - 3 judges each score 1-5 (categorical)
  - median(A, B, C) is the final categorical score
  - max_diff = max(scores) - min(scores) measures disagreement
  - If any dim has max_diff >= AGREEMENT_THRESHOLD → calibration_required
  - If any dim has score=1 → calibration_required (harmful content)

Overall subjective status (independent of deterministic precedence):
  - PASS:     all 8 median >= OVERALL_PASS_THRESHOLD (default 3)
  - PARTIAL:  1-2 dimensions median < 3
  - FAIL:     3+ dimensions median < 3, OR any score=1 (harmful)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .rubric import EIGHT_DIMENSIONS
from .judge import JudgeResult


PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"

# Per M6.0-4 audit §6.4:
OVERALL_PASS_THRESHOLD = 3   # median >= 3 → acceptable
AGREEMENT_THRESHOLD = 2      # max_diff >= 2 → disagreement (1=Likert step, 2+ = disagree)

# 8-dim: PASS=0/1-2 below threshold, PARTIAL=1-2, FAIL=3+ below
FAIL_DIM_COUNT = 3
PARTIAL_MAX_DIM_COUNT = 2


@dataclass(frozen=True)
class EvaluationResult:
    """
    Aggregated subjective evaluation result.

    scenario_id: identifier of the scenario
    judge_results: list of 3 JudgeResult (one per judge)
    per_dimension_scores: dict of dimension_name -> list of 3 scores [A, B, C]
    median_scores: dict of dimension_name -> int (median of 3 scores)
    agreement_metadata:
      - per_dimension_max_diff: dict of dim -> int
      - num_disagreements: int (count of dimensions with max_diff >= 2)
    overall_subjective_status: PASS / PARTIAL / FAIL
    calibration_required: bool
    rubric_version: str
    evaluator_version: str
    extra: optional metadata (timestamp, scenario description, etc.)
    """
    scenario_id: str
    judge_results: List[JudgeResult]
    per_dimension_scores: Dict[str, List[int]]
    median_scores: Dict[str, int]
    agreement_metadata: Dict[str, Any]
    overall_subjective_status: str
    calibration_required: bool
    rubric_version: str
    evaluator_version: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for logging / calibration queue."""
        return {
            "scenario_id": self.scenario_id,
            "judge_results": [
                {
                    "judge_id": jr.judge_id,
                    "model": jr.model,
                    "per_dimension_scores": dict(jr.per_dimension_scores),
                    "rationale": jr.rationale,
                }
                for jr in self.judge_results
            ],
            "per_dimension_scores": dict(self.per_dimension_scores),
            "median_scores": dict(self.median_scores),
            "agreement_metadata": dict(self.agreement_metadata),
            "overall_subjective_status": self.overall_subjective_status,
            "calibration_required": self.calibration_required,
            "rubric_version": self.rubric_version,
            "evaluator_version": self.evaluator_version,
            "extra": dict(self.extra),
        }


def calculate_agreement(
    judge_results: List[JudgeResult],
    dimensions: List[str],
) -> Dict[str, Any]:
    """
    Calculate per-dimension max difference and overall disagreement count.

    Returns dict with:
      - per_dimension_max_diff: {dim: int}
      - num_disagreements: int (count of dims with max_diff >= AGREEMENT_THRESHOLD)
      - num_harmful: int (count of dims where any score == 1)
    """
    per_dim_max_diff: Dict[str, int] = {}
    num_disagreements = 0
    num_harmful = 0

    for dim in dimensions:
        scores: List[int] = []
        for jr in judge_results:
            if dim in jr.per_dimension_scores:
                scores.append(jr.per_dimension_scores[dim])
        if not scores:
            per_dim_max_diff[dim] = -1  # missing
            continue
        max_diff = max(scores) - min(scores)
        per_dim_max_diff[dim] = max_diff
        if max_diff >= AGREEMENT_THRESHOLD:
            num_disagreements += 1
        if any(s == 1 for s in scores):
            num_harmful += 1

    return {
        "per_dimension_max_diff": per_dim_max_diff,
        "num_disagreements": num_disagreements,
        "num_harmful": num_harmful,
    }


def aggregate(
    judge_results: List[JudgeResult],
    scenario_id: str,
    dimensions: Optional[List[str]] = None,
    rubric_version: str = "v1-2026-08-11",
    evaluator_version: str = "m6.0.5-2026-08-11",
    extra: Optional[Dict[str, Any]] = None,
) -> EvaluationResult:
    """
    Aggregate 3 JudgeResult into a single EvaluationResult.

    Steps:
      1. Per-dimension: gather [A, B, C] scores, compute median
      2. Calculate agreement (max_diff per dim, num disagreements)
      3. Determine calibration_required
      4. Determine overall_subjective_status (PASS/PARTIAL/FAIL)

    Errored judges (JudgeResult.error != None) are treated as having
    no scores for all dimensions. This triggers calibration_required=True
    (failure to evaluate is itself a calibration event).
    """
    if dimensions is None:
        dimensions = sorted(EIGHT_DIMENSIONS)

    # Per-dimension score collection
    per_dimension_scores: Dict[str, List[int]] = {}
    median_scores: Dict[str, int] = {}

    # Track which judges errored
    errored_judges = [jr for jr in judge_results if jr.error is not None]
    successful_judges = [jr for jr in judge_results if jr.error is None]

    for dim in dimensions:
        scores: List[int] = []
        for jr in successful_judges:
            if dim in jr.per_dimension_scores:
                scores.append(jr.per_dimension_scores[dim])
        if not scores:
            # All judges missing this dim (or all errored) — treat as unacceptable
            per_dimension_scores[dim] = []
            median_scores[dim] = OVERALL_PASS_THRESHOLD  # neutral default
            continue
        per_dimension_scores[dim] = list(scores)
        median_scores[dim] = int(statistics.median(scores))

    agreement = calculate_agreement(successful_judges, dimensions)

    # Calibration trigger:
    # 1. High disagreement (any dim max_diff >= AGREEMENT_THRESHOLD)
    # 2. Harmful content (any score = 1)
    # 3. Any judge errored (M6.0-5.2: failure to evaluate = calibration event)
    calibration_required = (
        agreement["num_disagreements"] > 0
        or agreement["num_harmful"] > 0
        or len(errored_judges) > 0
    )

    # Add error info to agreement metadata
    agreement["errored_judges"] = [
        {"judge_id": jr.judge_id, "error": jr.error} for jr in errored_judges
    ]

    # Overall subjective status:
    # If 2+ judges errored → FAIL (insufficient evidence)
    # If 1 judge errored but 2 judges agree → PARTIAL (incomplete but recoverable)
    if len(errored_judges) >= 2:
        overall = FAIL
    else:
        below_threshold = sum(
            1 for dim in dimensions
            if median_scores.get(dim, OVERALL_PASS_THRESHOLD) < OVERALL_PASS_THRESHOLD
        )
        harmful_dims = agreement["num_harmful"]

        if below_threshold >= FAIL_DIM_COUNT or harmful_dims > 0:
            overall = FAIL
        elif below_threshold >= 1:
            overall = PARTIAL
        else:
            overall = PASS

    return EvaluationResult(
        scenario_id=scenario_id,
        judge_results=list(judge_results),
        per_dimension_scores=per_dimension_scores,
        median_scores=median_scores,
        agreement_metadata=agreement,
        overall_subjective_status=overall,
        calibration_required=calibration_required,
        rubric_version=rubric_version,
        evaluator_version=evaluator_version,
        extra=extra or {},
    )
