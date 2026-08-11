"""
tests/_helpers/subjective_eval/judge.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Judge abstraction.

Each judge receives the SAME evidence. Judges MUST NOT see each other's answers.
The consensus layer reads the 3 independent results only AFTER all 3 evaluations.

Mock judges are deterministic, network-free, no real LLM calls.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .rubric import (
    EIGHT_DIMENSIONS,
    VALID_SCORES,
    validate_score,
)
from .evidence import EvaluationEvidence


@dataclass(frozen=True)
class JudgeResult:
    """
    Per-judge output.

    judge_id: stable identifier (e.g. "judge-A", "mock-judge-fixed-4")
    model: model identifier ("mock" for fake judges)
    per_dimension_scores: dict of dimension_name -> int score 1-5
    rationale: short free-form explanation (optional, can be empty for tests)
    """
    judge_id: str
    model: str
    per_dimension_scores: Dict[str, int]
    rationale: str = ""

    def __post_init__(self):
        # Validate all scores
        for dim, score in self.per_dimension_scores.items():
            if dim not in EIGHT_DIMENSIONS:
                raise ValueError(
                    f"Unknown dimension: {dim!r}. "
                    f"Valid: {sorted(EIGHT_DIMENSIONS)}"
                )
            validate_score(score)


class Judge(ABC):
    """
    Abstract judge.

    Real LLM judge would override evaluate() to call a model API.
    Mock judges (FixedScoreJudge, ScriptedJudge, HighAgreementJudge, etc.)
    are deterministic and network-free.
    """
    def __init__(self, judge_id: str, model: str = "mock"):
        self.judge_id = judge_id
        self.model = model

    @abstractmethod
    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        """
        Evaluate evidence and return JudgeResult.
        MUST be pure (no side effects, no mutation of input).
        MUST be deterministic for given (judge_id, evidence).
        """
        ...


class FixedScoreJudge(Judge):
    """
    Mock judge: returns the same score for every dimension.
    Useful for baseline / reproducibility tests.
    """
    def __init__(self, judge_id: str, score: int = 4, model: str = "mock-fixed"):
        super().__init__(judge_id, model)
        if score not in VALID_SCORES:
            raise ValueError(f"score must be in {sorted(VALID_SCORES)}, got {score}")
        self.score = score

    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        scores = {dim: self.score for dim in EIGHT_DIMENSIONS}
        return JudgeResult(
            judge_id=self.judge_id,
            model=self.model,
            per_dimension_scores=scores,
            rationale=f"FixedScoreJudge: all dimensions = {self.score}",
        )


class ScriptedJudge(Judge):
    """
    Mock judge: returns a pre-scripted sequence of (per-dim) scores.
    Use evaluate_call_count to advance through the script.
    On exhaustion, repeats the last entry.
    """
    def __init__(self, judge_id: str, script: List[Dict[str, int]], model: str = "mock-scripted"):
        super().__init__(judge_id, model)
        if not script:
            raise ValueError("script must be non-empty")
        # Validate script entries
        for entry in script:
            for dim, score in entry.items():
                if dim not in EIGHT_DIMENSIONS:
                    raise ValueError(f"Unknown dimension in script: {dim!r}")
                validate_score(score)
        self.script = script
        self._call_count = 0

    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        idx = min(self._call_count, len(self.script) - 1)
        scores = dict(self.script[idx])  # copy
        self._call_count += 1
        return JudgeResult(
            judge_id=self.judge_id,
            model=self.model,
            per_dimension_scores=scores,
            rationale=f"ScriptedJudge call #{self._call_count}, entry {idx}",
        )


class HighAgreementJudge(Judge):
    """
    Mock judge: returns 4 or 5 for all dimensions (low disagreement baseline).
    """
    def __init__(self, judge_id: str, base: int = 4, model: str = "mock-high-agree"):
        super().__init__(judge_id, model)
        if base not in VALID_SCORES:
            raise ValueError(f"base must be in {sorted(VALID_SCORES)}, got {base}")
        self.base = base

    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        scores = {dim: self.base for dim in EIGHT_DIMENSIONS}
        return JudgeResult(
            judge_id=self.judge_id,
            model=self.model,
            per_dimension_scores=scores,
            rationale=f"HighAgreementJudge: all dims = {self.base}",
        )


class HighDisagreementJudge(Judge):
    """
    Mock judge: alternates between very low and very high scores
    to test calibration queue triggering.
    Per call: 1 for half the dimensions, 5 for the other half, alternating.
    """
    def __init__(self, judge_id: str, model: str = "mock-high-disagree"):
        super().__init__(judge_id, model)
        self._call_count = 0

    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        dims = list(EIGHT_DIMENSIONS)
        # Alternate pattern: even calls split dims 1/5, odd calls split 5/1
        if self._call_count % 2 == 0:
            scores = {dim: (1 if i % 2 == 0 else 5) for i, dim in enumerate(dims)}
        else:
            scores = {dim: (5 if i % 2 == 0 else 1) for i, dim in enumerate(dims)}
        self._call_count += 1
        return JudgeResult(
            judge_id=self.judge_id,
            model=self.model,
            per_dimension_scores=scores,
            rationale=f"HighDisagreementJudge call #{self._call_count}",
        )


class SequentialJudgeRunner:
    """
    Runs N judges in sequence, each with the SAME evidence.
    Judges are isolated (no shared state, no cross-contamination).
    Returns list of JudgeResult in submission order.

    IMPORTANT: This is sequential for simplicity. Real implementation
    may parallelize. Output ordering is deterministic (by judge_id).
    """
    def __init__(self, judges: List[Judge]):
        if len(judges) != 3:
            raise ValueError(
                f"M6.0-5 requires exactly 3 judges, got {len(judges)}"
            )
        # Verify unique judge_ids
        ids = [j.judge_id for j in judges]
        if len(set(ids)) != 3:
            raise ValueError(
                f"Judge IDs must be unique, got {ids}"
            )
        self.judges = judges

    def run(self, evidence: EvaluationEvidence) -> List[JudgeResult]:
        """
        Run all 3 judges with the same evidence.
        Each judge.evaluate() is called in isolation.
        """
        results = []
        for judge in self.judges:
            # CRITICAL: each judge receives the SAME evidence object
            # but they evaluate independently and cannot see each other's results
            result = judge.evaluate(evidence)
            results.append(result)
        return results
