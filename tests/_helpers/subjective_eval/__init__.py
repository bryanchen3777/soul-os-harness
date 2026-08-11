"""
tests/_helpers/subjective_eval/__init__.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Subjective LLM Quality Evaluation Infrastructure.

This module is the FIRST subjective LLM quality evaluation layer for Soul OS.
It is INTENTIONALLY test-only — it lives under tests/_helpers/ and is NOT part
of Soul OS production runtime.

Scope (frozen for this ticket per Bry 派工 spec):
  - LLMProxy chat ONLY
  - 3 independent LLM judges
  - 1-5 categorical Likert rubric
  - Median + agreement aggregation
  - High disagreement → calibration_required
  - Mock judges for tests (no real LLM)
  - Asynchronous/non-blocking Bry calibration

Out of scope (this ticket):
  - diary LLM subjective eval
  - dream LLM subjective eval
  - proactive DM subjective eval
  - TTS / voice
  - UI / human-review dashboard
  - database infrastructure
"""
from __future__ import annotations

from .rubric import (
    EIGHT_DIMENSIONS,
    RUBRIC_ANCHORS,
    RUBRIC_VERSION,
    DimensionName,
    validate_score,
)
from .evidence import (
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    evidence_to_dict,
    evidence_from_dict,
)
from .judge import (
    Judge,
    JudgeResult,
    JudgeProvenance,
    FixedScoreJudge,
    ScriptedJudge,
    HighAgreementJudge,
    HighDisagreementJudge,
    SequentialJudgeRunner,
)
from .real_judge import (
    RealLLMJudge,
    JUDGE_PROMPT_VERSION,
    _build_judge_prompt,
    _parse_judge_response,
)
from .consensus import (
    EvaluationResult,
    aggregate,
    calculate_agreement,
    OVERALL_PASS_THRESHOLD,
    AGREEMENT_THRESHOLD,
    PASS,
    PARTIAL,
    FAIL,
)
from .calibration import (
    CalibrationItem,
    CalibrationQueue,
    CalibrationStatus,
)
from .precedence import (
    combine_deterministic_subjective,
    DET_OVERRIDES_SUBJECTIVE,
)

__all__ = [
    # rubric
    "EIGHT_DIMENSIONS",
    "RUBRIC_ANCHORS",
    "RUBRIC_VERSION",
    "DimensionName",
    "validate_score",
    # evidence
    "EvaluationEvidence",
    "build_evidence_from_llmproxy_call",
    "evidence_to_dict",
    "evidence_from_dict",
    # judge
    "Judge",
    "JudgeResult",
    "JudgeProvenance",
    "FixedScoreJudge",
    "ScriptedJudge",
    "HighAgreementJudge",
    "HighDisagreementJudge",
    "SequentialJudgeRunner",
    # real judge (M6.0-5.2)
    "RealLLMJudge",
    "JUDGE_PROMPT_VERSION",
    "_build_judge_prompt",
    "_parse_judge_response",
    # consensus
    "EvaluationResult",
    "aggregate",
    "calculate_agreement",
    "OVERALL_PASS_THRESHOLD",
    "AGREEMENT_THRESHOLD",
    "PASS",
    "PARTIAL",
    "FAIL",
    # calibration
    "CalibrationItem",
    "CalibrationQueue",
    "CalibrationStatus",
    # precedence
    "combine_deterministic_subjective",
    "DET_OVERRIDES_SUBJECTIVE",
]

EVALUATOR_VERSION = "m6.0.5-2026-08-11"
