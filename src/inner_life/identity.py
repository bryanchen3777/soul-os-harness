"""
src/inner_life/identity.py — Inner Life Identity Validation & Generation

M5.4-5.1 (Bry 派工 2026-08-09 18:25) — Inner Life Unified Architecture Foundation
M5.15-5 (Bry 派工 2026-08-12 19:14) — WorldEvent ↔ InnerLifeEvent Identity Bridge
  Adds source_world_event_novelty_id validator (format-only, accepts any non-empty str OR None).
  0 change to existing validators (event_id / parent_event_id / correlation_id / session_id / ts).

派工精神:
  - canonical identity semantics, not just fields
  - 不為假設中的未來灑過濾網
  - identity rule 要 deterministic + testable

Identity rules (per派工):
  - event_id: 32 char lowercase hex (uuid4 without dashes)
  - session_id: optional, non-empty if set
  - correlation_id: optional, non-empty if set
  - parent_event_id: optional, must reference known event if set
  - source_world_event_novelty_id (M5.15-5): optional, non-empty if set
    — free string (NO 32-hex requirement, NO existence check)
    — represents WorldEvent.novelty_id (the upstream causal source)
    — does NOT replace parent_event_id (M5.4-5.1 frozen)
  - ts: ISO 8601 UTC, immutable

This module is the SINGLE source of truth for:
  - event_id format validation
  - UUID generation
  - ts validation
  - Provenance validation helpers
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Format constants (派工: "Define event_id semantic, not just field")
# ─────────────────────────────────────────────────────────────────────

EVENT_ID_LENGTH = 32  # uuid4 hex without dashes
EVENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
TS_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)$"
)
# Match ISO 8601 UTC: 2026-08-10T12:34:56.789+00:00 or 2026-08-10T12:34:56Z


# ─────────────────────────────────────────────────────────────────────
# Generation
# ─────────────────────────────────────────────────────────────────────

def generate_event_id() -> str:
    """
    Generate a new canonical Inner Life event_id.

    Format: 32-char lowercase hex (uuid4 without dashes).
    Globally unique (uuid4 collision probability 2^-122).
    Immutable, never re-issued.

    Returns:
        str: 32-char hex (e.g., "a1b2c3d4e5f6...")
    """
    return uuid.uuid4().hex


def now_utc_iso() -> str:
    """
    Generate a canonical ISO 8601 UTC timestamp at current moment.

    Format: YYYY-MM-DDTHH:MM:SS.ffffff+00:00
    Validated by TS_PATTERN.

    Returns:
        str: ISO 8601 UTC string
    """
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────

class IdentityValidationError(ValueError):
    """Raised when identity fields fail canonical format validation."""
    pass


def validate_event_id(event_id: object) -> str:
    """
    Validate that an event_id matches the canonical 32-hex format.

    Args:
        event_id: candidate value (any type)

    Returns:
        str: validated event_id (lowercase)

    Raises:
        IdentityValidationError: if not a 32-char lowercase hex string
    """
    if not isinstance(event_id, str):
        raise IdentityValidationError(
            f"event_id 必須是 str, got: {type(event_id).__name__}"
        )
    if not EVENT_ID_PATTERN.match(event_id):
        raise IdentityValidationError(
            f"event_id 必須是 32 char lowercase hex (uuid4 no dashes), got: {event_id!r}"
        )
    return event_id


def validate_session_id(session_id: object, *, allow_none: bool = True) -> Optional[str]:
    """
    Validate that a session_id is either None or a non-empty string.

    Args:
        session_id: candidate value
        allow_none: if True, None is accepted (cross-session event)

    Returns:
        Optional[str]: validated session_id, or None if allowed + not provided

    Raises:
        IdentityValidationError: if not None and not a non-empty string
    """
    if session_id is None:
        if not allow_none:
            raise IdentityValidationError("session_id 不可為 None")
        return None
    if not isinstance(session_id, str):
        raise IdentityValidationError(
            f"session_id 必須是 str 或 None, got: {type(session_id).__name__}"
        )
    if not session_id.strip():
        raise IdentityValidationError("session_id 不可為空字串")
    return session_id


def validate_correlation_id(correlation_id: object, *, allow_none: bool = True) -> Optional[str]:
    """
    Validate that a correlation_id is either None or a non-empty string.

    Correlation semantic (派工明列):
      "不要把 correlation_id 當成萬用欄位, 必須先定義 semantic meaning"
      - correlation_id = "two events that should be considered the SAME narrative context"
      - NOT a causation link (that's parent_event_id)
      - NOT a foreign key
      - Grouping marker for narrative arcs

    Example: Bry's user message + agent's reply share correlation_id
             Morning diary + same day's events share correlation_id = "<date>"

    Args:
        correlation_id: candidate value
        allow_none: if True, None is accepted (event outside any narrative)

    Returns:
        Optional[str]: validated correlation_id, or None if allowed + not provided

    Raises:
        IdentityValidationError: if not None and not a non-empty string
    """
    if correlation_id is None:
        if not allow_none:
            raise IdentityValidationError("correlation_id 不可為 None")
        return None
    if not isinstance(correlation_id, str):
        raise IdentityValidationError(
            f"correlation_id 必須是 str 或 None, got: {type(correlation_id).__name__}"
        )
    if not correlation_id.strip():
        raise IdentityValidationError("correlation_id 不可為空字串")
    return correlation_id


def validate_parent_event_id(parent_event_id: object, *, allow_none: bool = True) -> Optional[str]:
    """
    Validate that a parent_event_id is either None or a valid event_id format.

    Note: This validates FORMAT, not existence. Existence (parent must be known)
    is checked by InnerLifeWriter at creation time (since parent may not exist
    yet when caller has a reference to a future event).

    Args:
        parent_event_id: candidate value
        allow_none: if True, None is accepted (root event, no parent)

    Returns:
        Optional[str]: validated parent_event_id, or None if allowed + not provided

    Raises:
        IdentityValidationError: if not None and not a valid event_id format
    """
    if parent_event_id is None:
        if not allow_none:
            raise IdentityValidationError("parent_event_id 不可為 None")
        return None
    return validate_event_id(parent_event_id)


def validate_source_world_event_novelty_id(
    source_world_event_novelty_id: object,
    *,
    allow_none: bool = True,
) -> Optional[str]:
    """
    M5.15-5 (Bry 派工 2026-08-12 19:14): Validate that a
    source_world_event_novelty_id is either None or a non-empty string.

    Distinct from validate_event_id (32-hex, for InnerLifeEvent.event_id)
    and validate_parent_event_id (32-hex, must be known InnerLifeEvent).

    This validator is INTENTIONALLY LENIENT:
      - Accepts any non-empty string (NOT 32-hex format)
      - Does NOT check existence (WorldEvent.novelty_id is external, not
        in InnerLifeWriter._known_event_ids)
      - WorldEvent.novelty_id remains its existing domain (free string
        set by WorldEventSource)

    Rationale (per M5.15-5 decision):
      - WorldEvent.novelty_id is a free-form string set by WorldEventSource
        (e.g., "weather_rain_20260807", "calendar_meeting_20260807_1500")
      - It does NOT need to match the 32-char hex InnerLifeEvent.event_id format
      - It is an EXTERNAL identity, not a sibling in the InnerLifeEvent tree
      - parent_event_id remains InnerLifeEvent-only (M5.4-5.1 frozen)
      - This validator is for cross-system causality, not internal lineage

    Args:
        source_world_event_novelty_id: candidate value
        allow_none: if True, None is accepted (no WorldEvent parent)

    Returns:
        Optional[str]: validated source_world_event_novelty_id, or None if allowed

    Raises:
        IdentityValidationError: if not None and not a non-empty string
    """
    if source_world_event_novelty_id is None:
        if not allow_none:
            raise IdentityValidationError(
                "source_world_event_novelty_id 不可為 None"
            )
        return None
    if not isinstance(source_world_event_novelty_id, str):
        raise IdentityValidationError(
            f"source_world_event_novelty_id 必須是 str 或 None, got: "
            f"{type(source_world_event_novelty_id).__name__}"
        )
    if not source_world_event_novelty_id.strip():
        raise IdentityValidationError(
            "source_world_event_novelty_id 不可為空字串"
        )
    return source_world_event_novelty_id


def validate_ts(ts: object) -> str:
    """
    Validate that a timestamp is a canonical ISO 8601 UTC string.

    Format: YYYY-MM-DDTHH:MM:SS[.ffffff][+00:00|Z]

    Args:
        ts: candidate value

    Returns:
        str: validated ts

    Raises:
        IdentityValidationError: if not a valid ISO 8601 UTC string
    """
    if not isinstance(ts, str):
        raise IdentityValidationError(
            f"ts 必須是 str, got: {type(ts).__name__}"
        )
    if not TS_PATTERN.match(ts):
        raise IdentityValidationError(
            f"ts 必須是 ISO 8601 UTC (YYYY-MM-DDTHH:MM:SS+00:00 or Z), got: {ts!r}"
        )
    # 進一步驗證可以用 fromisoformat 解析
    try:
        # Python 3.11+ 支援 Z 後綴
        normalized = ts.replace("Z", "+00:00")
        datetime.fromisoformat(normalized)
    except ValueError as e:
        raise IdentityValidationError(
            f"ts 不是合法 ISO 8601 timestamp: {ts!r} ({e})"
        )
    return ts


def derive_lineage(
    parent_depth: Optional[int],
    parent_path: Optional[str],
    own_event_id: str,
) -> tuple[int, str]:
    """
    Derive lineage_depth and lineage_path from parent's lineage + own event_id.

    Semantic (派工: "How are parent/child narrative events represented"):
      - lineage_depth: 0 for root events (no parent), parent's depth + 1 otherwise
      - lineage_path: empty for root, "parent_path/own_id" otherwise (slash-separated)
      - lineage_path is DENORMALIZED for efficient query (no need to traverse chain)

    Args:
        parent_depth: parent's lineage_depth, or None if no parent (root event)
        parent_path: parent's lineage_path, or None if no parent (root event)
        own_event_id: this event's own event_id (must be 32-char hex)

    Returns:
        tuple[int, str]: (lineage_depth, lineage_path)

    Raises:
        IdentityValidationError: on malformed inputs
    """
    validate_event_id(own_event_id)
    if parent_depth is None and parent_path is None:
        # Root event
        return 0, own_event_id
    if parent_depth is None or parent_path is None:
        raise IdentityValidationError(
            "parent_depth 跟 parent_path 必須同時為 None (root) 或同時有值 (child)"
        )
    if parent_depth < 0:
        raise IdentityValidationError(
            f"parent_depth 不可為負數, got: {parent_depth}"
        )
    return parent_depth + 1, f"{parent_path}/{own_event_id}"
