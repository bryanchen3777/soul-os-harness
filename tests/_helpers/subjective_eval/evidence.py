"""
tests/_helpers/subjective_eval/evidence.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Evaluation evidence packet.

Evidence = serialized snapshot of LLM call context that the judge sees.
Each judge receives the SAME evidence (no cross-contamination).

NOT exposed in evidence (per M6.0-4 audit §4.2):
  - raw production memory.db full content
  - unrelated agents' private state
  - credentials, API keys
  - TTS/audio
  - private message metadata unrelated to evaluation
  - arbitrary production files
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EvaluationEvidence:
    """
    Immutable evaluation evidence packet.

    Captures the minimum required context for subjective evaluation:
    - scenario_id: which scenario produced this
    - user_input: the user message that triggered LLM
    - composed_context: the 8-block (group) / 7-block (private) system prompt
    - llm_response: the LLM's actual output text
    - state_snapshot: relevant state at evaluation time
      (mood, memory_facts_count, relationship_confidence, world_events_active, etc.)
    - model: exact model identifier (e.g. "claude-haiku-4-5-20251001")
    - prompt_version: git hash or version string of system prompt
    - temperature: generation temperature
    - rubric_version: rubric version used to evaluate
    - extra: optional extra metadata (timestamp, agent_id, user_id, etc.)
    """
    scenario_id: str
    user_input: str
    composed_context: str
    llm_response: str
    state_snapshot: Dict[str, Any]
    model: str
    prompt_version: str
    temperature: float
    rubric_version: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Ensure state_snapshot is a dict (frozen=True with mutable defaults)
        if not isinstance(self.state_snapshot, dict):
            raise TypeError(
                f"state_snapshot must be dict, got {type(self.state_snapshot).__name__}"
            )
        if not isinstance(self.extra, dict):
            raise TypeError(
                f"extra must be dict, got {type(self.extra).__name__}"
            )


def evidence_to_dict(evidence: EvaluationEvidence) -> Dict[str, Any]:
    """Serialize evidence to JSON-safe dict."""
    return asdict(evidence)


def evidence_from_dict(d: Dict[str, Any]) -> EvaluationEvidence:
    """Deserialize evidence from dict. Strict — missing fields raise."""
    required = [
        "scenario_id", "user_input", "composed_context", "llm_response",
        "state_snapshot", "model", "prompt_version", "temperature",
        "rubric_version",
    ]
    for key in required:
        if key not in d:
            raise KeyError(
                f"Evidence missing required field: {key!r}. "
                f"Got: {sorted(d.keys())}"
            )
    return EvaluationEvidence(
        scenario_id=d["scenario_id"],
        user_input=d["user_input"],
        composed_context=d["composed_context"],
        llm_response=d["llm_response"],
        state_snapshot=d["state_snapshot"],
        model=d["model"],
        prompt_version=d["prompt_version"],
        temperature=float(d["temperature"]),
        rubric_version=d["rubric_version"],
        extra=d.get("extra", {}),
    )


def build_evidence_from_llmproxy_call(
    scenario_id: str,
    user_input: str,
    composed_context: str,
    llm_response: str,
    model: str,
    temperature: float,
    state_snapshot: Optional[Dict[str, Any]] = None,
    prompt_version: str = "unknown",
    rubric_version: str = "v1-2026-08-11",
    extra: Optional[Dict[str, Any]] = None,
) -> EvaluationEvidence:
    """
    Helper to build evidence from an LLMProxy call observation.
    Used by tests and (eventually) by M6.0-5 production recording backend.
    """
    return EvaluationEvidence(
        scenario_id=scenario_id,
        user_input=user_input,
        composed_context=composed_context,
        llm_response=llm_response,
        state_snapshot=state_snapshot or {},
        model=model,
        prompt_version=prompt_version,
        temperature=temperature,
        rubric_version=rubric_version,
        extra=extra or {},
    )
