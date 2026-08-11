"""
tests/_helpers/subjective_eval/precedence.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Deterministic > Subjective precedence.

Per M6.0-4 audit §7.3 + Bry 派工 spec §"DETERMINISTIC + SUBJECTIVE COMBINATION":

  Det PASS + Subj PASS = PASS
  Det PASS + Subj FAIL = PARTIAL
  Det FAIL + Subj PASS = FAIL   (deterministic overrides)
  Det FAIL + Subj FAIL = FAIL

CRITICAL: A subjective evaluator MUST NEVER override a deterministic
contract failure.
"""
from __future__ import annotations

from typing import Any, Dict

from .consensus import (
    EvaluationResult,
    PASS as SUBJ_PASS,
    PARTIAL as SUBJ_PARTIAL,
    FAIL as SUBJ_FAIL,
)


# Final result labels
PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"

# Bry 派工 spec explicit rule
DET_OVERRIDES_SUBJECTIVE = True


def combine_deterministic_subjective(
    deterministic_pass: bool,
    subjective_result: EvaluationResult,
) -> Dict[str, Any]:
    """
    Apply deterministic > subjective precedence.

    Returns dict with:
      - deterministic_pass: bool
      - subjective_status: PASS / PARTIAL / FAIL
      - final_status: PASS / PARTIAL / FAIL (after precedence)
      - precedence_rule_applied: str (describes the rule)
    """
    subj = subjective_result.overall_subjective_status

    if not deterministic_pass:
        # Deterministic FAIL always wins, regardless of subjective
        return {
            "deterministic_pass": False,
            "subjective_status": subj,
            "final_status": FAIL,
            "precedence_rule_applied": (
                "deterministic FAIL overrides subjective PASS (DET_OVERRIDES_SUBJECTIVE=True)"
            ),
        }

    # Deterministic PASS
    if subj == SUBJ_PASS:
        return {
            "deterministic_pass": True,
            "subjective_status": subj,
            "final_status": PASS,
            "precedence_rule_applied": "det PASS + subj PASS = PASS",
        }

    # det PASS + subj PARTIAL or FAIL = PARTIAL
    return {
        "deterministic_pass": True,
        "subjective_status": subj,
        "final_status": PARTIAL,
        "precedence_rule_applied": (
            f"det PASS + subj {subj} = PARTIAL (subjective quality issue, "
            f"fix prompt/persona)"
        ),
    }
