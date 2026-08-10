"""
src/conversation_qualification/ — Conversation → InnerLifeEvent Qualification Boundary

M5.6-2 (Bry 派工 2026-08-10): First deterministic ConversationQualification boundary.

派工精神:
  - USER_MESSAGE ≠ InnerLifeEvent
  - ConversationQualification 是「決定是否值得升級」的 boundary
  - InnerLifeWriter 是唯一 InnerLifeEvent creator
  - Qualifier 絕對不得自行 fabricate event identity

Architecture flow (per M5.6-1 audit Section 10.1):
  USER_MESSAGE
      ↓
  conversation/session lifecycle (Heartbeat tracks session_id)
      ↓
  SESSION_END (with last_session_id / last_user_id / last_agent_id payload)
      ↓
  ConversationQualification.on_session_end()
      ↓
  if duration >= 5min AND turn_depth >= 4:
      qualified = True
      InnerLifeWriter.create_event(
          provenance=Provenance(trigger_type="conversation:user_message", ...),
          session_id=...,
          correlation_id=...,
      )
      ↓
      canonical InnerLifeEvent → trace.jsonl (via InnerLifeWriter)

v1 policy (Bry 派工 2026-08-10 拍板):
  - Duration threshold: 5 min (from SESSION_END.payload.elapsed_mins)
  - Turn-depth threshold: 4 entries (from data/conversations/{user}_{agent}_private.json)
  - NO topic continuity (v1)
  - NO LLM qualification (v1)
  - NO conversation content inspection (v1)
  - Default: if both thresholds met → qualified = True; otherwise → False

Out of scope (per ticket):
  - LLM conversation judge
  - semantic topic analysis
  - embeddings / vector DB
  - conversation summarization
  - emotional / novelty / relationship scoring
  - cross-session aggregation
  - unrelated refactor

Frozen contracts preserved:
  - InnerLifeWriter remains sole InnerLifeEvent creator
  - M5.4-5.1 event model unchanged
  - Provenance frozen model unchanged (uses existing trigger_type string field, additive new value)
  - SESSION_END event schema unchanged (only payload fields added in Heartbeat, additive optional)
  - Event Bus contract unchanged
  - All existing 4 producers (Diary / Dream / Event / ProactiveDM) unchanged
"""
from __future__ import annotations

from .qualifier import (
    ConversationQualification,
    ConversationQualificationResult,
    QUALIFICATION_DURATION_THRESHOLD_MINS,
    QUALIFICATION_TURN_DEPTH_THRESHOLD,
    TRIGGER_TYPE_CONVERSATION_USER_MESSAGE,
)

__all__ = [
    "ConversationQualification",
    "ConversationQualificationResult",
    "QUALIFICATION_DURATION_THRESHOLD_MINS",
    "QUALIFICATION_TURN_DEPTH_THRESHOLD",
    "TRIGGER_TYPE_CONVERSATION_USER_MESSAGE",
]
