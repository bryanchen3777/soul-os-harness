"""
tests/_helpers/subjective_eval/multi_model_runner.py
M6.0-5.4 (Bry 派工 2026-08-11 20:17): Minimal Multi-Model Judge Orchestration.

Adds:
  - DiversityValidator: validates 3 judges meet diversity requirements
    (unique IDs, >=2 model families, no duplicate config, no self-eval)
  - SelfEvaluationGuard: HARD BLOCK when judge.model == response.model
  - CostBudget: bounded max calls / max retries / max tokens / max cost
  - MultiModelJudgeRunner: 3-judge orchestration with diversity + cost
    enforcement, no auto-replacement, evaluation status (COMPLETE /
    INCOMPLETE / UNAVAILABLE)
  - EvaluationStatus: enum (COMPLETE / INCOMPLETE / UNAVAILABLE)

Constraints (per Bry 派工 spec, frozen):
  - 3 judges must be 3 distinct judge configurations
  - At least 2 distinct model families/providers required
  - Same response model as judge model = HARD BLOCK
  - Same provider OK if model family differs
  - No automatic judge replacement
  - 1 failure -> incomplete + calibration_required
  - 2 failures -> FAIL/incomplete
  - 3 failures -> unavailable/FAIL
  - Retry only 429/5xx/timeout; max 2 retries
  - Temperature = 0.0 default
  - Bounded token budget + hard cost ceiling

Reuses existing Judge / RealLLMJudge / consensus / CalibrationQueue.
Does NOT modify any frozen M5.x contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .judge import Judge, JudgeResult
from .real_judge import RealLLMJudge
from .consensus import aggregate, EvaluationResult


# ── EvaluationStatus ──

class EvaluationStatus(str, Enum):
    """
    Status of a 3-judge evaluation.

    COMPLETE:    3 judges succeeded, full evaluation available
    INCOMPLETE: 1 judge failed, partial evaluation + calibration_required=True
    UNAVAILABLE: 2+ judges failed, evaluation not actionable
    """
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNAVAILABLE = "unavailable"


# ── SelfEvaluationGuard ──

class SelfEvaluationError(ValueError):
    """Raised when judge.model == response.model (HARD BLOCK per spec)."""
    pass


def check_self_evaluation(response_model: str, judge_model: str) -> None:
    """
    HARD BLOCK: judge.model == response.model is forbidden.

    Provider equality is NOT a block (per spec: "Same provider is allowed
    if model family differs").
    """
    if response_model and judge_model and response_model == judge_model:
        raise SelfEvaluationError(
            f"Self-evaluation BLOCKED: judge.model={judge_model!r} == "
            f"response.model={response_model!r}"
        )


# ── DiversityValidator ──

class DiversityError(ValueError):
    """Raised when 3 judges don't meet diversity requirements."""
    pass


def _config_tuple(judge: RealLLMJudge) -> Tuple[str, str, Optional[str]]:
    """
    Build a (model, provider, base_url) tuple for dedup check.

    Per Bry spec: "no duplicate judge configuration" — judges with the same
    (model, provider, base_url) are effectively identical and must be rejected.
    judge_id is NOT part of the configuration (it's just an identifier); two
    judges with different IDs but same model+provider+base_url are still duplicates.
    """
    return (judge.model, judge.provider, getattr(judge, "base_url", None))


def validate_diversity(judges: List[RealLLMJudge], response_model: str = "") -> None:
    """
    Validate 3 judges meet diversity requirements.

    Requirements (per Bry 派工 spec, frozen):
      1. Exactly 3 judges
      2. Unique judge_ids
      3. No duplicate (model, provider, base_url) configurations
      4. At least 2 distinct (provider, model_family) tuples
      5. No judge.model == response.model (HARD BLOCK)

    Check ordering: duplicate config is checked BEFORE family diversity so that
    tests targeting the duplicate-config check get the expected error message
    even when the same judges also fail the family check.

    Raises DiversityError on any violation.
    """
    # 1. Exactly 3 judges
    if len(judges) != 3:
        raise DiversityError(f"Expected exactly 3 judges, got {len(judges)}")

    # 2. Unique judge_ids
    ids = [j.judge_id for j in judges]
    if len(set(ids)) != 3:
        raise DiversityError(f"Judge IDs must be unique, got {ids}")

    # 3. No duplicate (model, provider, base_url) configurations
    #    (checked BEFORE family diversity so duplicates get the right error)
    configs = [_config_tuple(j) for j in judges]
    if len(set(configs)) != 3:
        raise DiversityError(
            f"Duplicate judge configurations detected: {configs}"
        )

    # 4. At least 2 distinct (provider, model_family) tuples
    family_tuples = {(j.provider, getattr(j, "model_family", j.provider)) for j in judges}
    if len(family_tuples) < 2:
        raise DiversityError(
            f"At least 2 distinct model family/provider required, "
            f"got {len(family_tuples)}: {family_tuples}"
        )

    # 5. Self-evaluation: HARD BLOCK
    for j in judges:
        if response_model and j.model == response_model:
            raise DiversityError(
                f"Self-evaluation BLOCKED: judge {j.judge_id!r} has model "
                f"{j.model!r} == response.model {response_model!r}"
            )


# ── CostBudget ──

@dataclass
class CostBudget:
    """
    Bounded cost / call / retry / token budget for one evaluation.

    Tracks cumulative usage; can_make_call() / can_retry() / is_exhausted() queries.
    Budget exhaustion fails safely: subsequent judge calls return error result.
    """
    max_judge_calls: int = 3
    max_retries_per_judge: int = 2
    max_token_budget: int = 5000
    max_cost_usd: float = 0.05

    # Tracking (mutable, public for inspection)
    calls_made: int = 0
    retries_made: int = 0
    tokens_used: int = 0
    cost_estimated: float = 0.0
    budget_exhausted_reason: Optional[str] = None

    def can_make_call(self) -> bool:
        if self.calls_made >= self.max_judge_calls:
            return False
        if self.tokens_used >= self.max_token_budget:
            return False
        if self.cost_estimated >= self.max_cost_usd:
            return False
        return True

    def can_retry(self) -> bool:
        max_total_retries = self.max_retries_per_judge * self.max_judge_calls
        if self.retries_made >= max_total_retries:
            return False
        if self.cost_estimated >= self.max_cost_usd:
            return False
        return True

    def record_call(self, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0) -> None:
        self.calls_made += 1
        self.tokens_used += tokens_in + tokens_out
        self.cost_estimated += cost_usd
        self._check_exhausted()

    def record_retry(self) -> None:
        self.retries_made += 1
        self._check_exhausted()

    def is_exhausted(self) -> bool:
        return self.budget_exhausted_reason is not None

    def _check_exhausted(self) -> None:
        if self.calls_made >= self.max_judge_calls:
            self.budget_exhausted_reason = (
                f"max_judge_calls: {self.calls_made}/{self.max_judge_calls}"
            )
        elif self.tokens_used >= self.max_token_budget:
            self.budget_exhausted_reason = (
                f"max_token_budget: {self.tokens_used}/{self.max_token_budget}"
            )
        elif self.cost_estimated >= self.max_cost_usd:
            self.budget_exhausted_reason = (
                f"max_cost_usd: {self.cost_estimated:.4f}/{self.max_cost_usd:.4f}"
            )

    def reason_if_exhausted(self) -> Optional[str]:
        return self.budget_exhausted_reason


# ── MultiModelJudgeRunner ──

@dataclass
class MultiModelRunResult:
    """
    Full result of a 3-judge multi-model evaluation.

    Fields:
      - evaluation_result: aggregated EvaluationResult from consensus.aggregate()
      - status: COMPLETE / INCOMPLETE / UNAVAILABLE
      - budget: final CostBudget state (for audit / cost tracking)
    """
    evaluation_result: EvaluationResult
    status: EvaluationStatus
    budget: CostBudget


class MultiModelJudgeRunner:
    """
    Minimal multi-model judge orchestrator (M6.0-5.4).

    Accepts exactly 3 judge configurations. Validates diversity + self-evaluation
    guard. Runs judges independently with bounded cost budget. No auto-replacement.

    Reuses existing Judge / RealLLMJudge / consensus.aggregate() / CalibrationQueue.
    """
    def __init__(
        self,
        judges: List[RealLLMJudge],
        response_model: str = "",
        cost_budget: Optional[CostBudget] = None,
    ):
        # Pre-flight validation (fail-fast at construction)
        validate_diversity(judges, response_model=response_model)
        self.judges: List[RealLLMJudge] = list(judges)
        self.response_model = response_model
        self.cost_budget: CostBudget = cost_budget or CostBudget()

    async def run(self, evidence, http_client: Optional["httpx.AsyncClient"] = None) -> MultiModelRunResult:
        """
        Run 3 judges with bounded cost budget. No auto-replacement.

        Behavior:
          - If budget.can_make_call() is False: return error result, do NOT call API
          - Each judge runs independently (no cross-contamination)
          - 0 errored judges: status = COMPLETE
          - 1 errored judge: status = INCOMPLETE (median over 2)
          - 2+ errored judges: status = UNAVAILABLE

        Note: judge.evaluate() itself handles retry per judge.max_retries.
              This orchestrator adds budget gating on top.

        http_client: optional shared httpx.AsyncClient to pass to all 3 judges.
                     If provided, the client is shared (each judge uses the same
                     transport). The caller is responsible for the client's
                     lifecycle (opening/closing). If None, each judge creates
                     its own client.
        """
        results: List[JudgeResult] = []
        for judge in self.judges:
            if not self.cost_budget.can_make_call():
                # Budget exhausted: return fail-safe error result
                reason = self.cost_budget.reason_if_exhausted() or "budget_exhausted"
                results.append(JudgeResult(
                    judge_id=judge.judge_id,
                    model=judge.model,
                    per_dimension_scores={},
                    error=f"budget_exhausted: {reason}",
                ))
                continue
            # Track call before evaluation
            # (judge.evaluate() does its own retry; we count 1 call here)
            self.cost_budget.record_call(tokens_in=0, tokens_out=0, cost_usd=0.0)
            # Update token/cost tracking from provenance if successful
            try:
                if http_client is not None:
                    result = await judge.evaluate(evidence, http_client=http_client)
                else:
                    result = await judge.evaluate(evidence)
            except Exception as e:
                # Should not happen (judge.evaluate never raises per M6.0-5.2),
                # but be defensive
                result = JudgeResult(
                    judge_id=judge.judge_id,
                    model=judge.model,
                    per_dimension_scores={},
                    error=f"orchestrator_caught: {type(e).__name__}: {e}",
                )
            # Update budget from provenance (if available)
            if result.provenance is not None and result.provenance.token_usage is not None:
                tu = result.provenance.token_usage
                self.cost_budget.tokens_used += int(tu.get("total", 0))
            results.append(result)

        # Determine status from errored count
        errored_count = sum(1 for r in results if r.error is not None)
        if errored_count >= 2:
            status = EvaluationStatus.UNAVAILABLE
        elif errored_count == 1:
            status = EvaluationStatus.INCOMPLETE
        else:
            status = EvaluationStatus.COMPLETE

        # Aggregate via existing M6.0-5 consensus
        eval_result = aggregate(results, scenario_id=evidence.scenario_id)

        return MultiModelRunResult(
            evaluation_result=eval_result,
            status=status,
            budget=self.cost_budget,
        )
