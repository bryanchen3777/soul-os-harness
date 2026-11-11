"""SA-1 thin entry: authenticate-then-forward envelope (no provider calls).

The thin entry (outward gateway side) only authenticates the caller (done by
``src/external_gateway/app.py`` before this module is ever consulted) and
forwards a sealed envelope to the FIXED internal endpoint
(``FORWARD_ENDPOINT``). It NEVER dials a provider: this module imports only
stdlib plus SA-0 ``schema`` types and has no backend / httpx / socket /
llm / EI-1 imports by construction (pinned by ``test_t1_bridge.py``).

Envelope contents (all SA-0 frozen types, never redefined here):
  - ``snapshot``   : SoulSnapshot built thin-side from the admitted route.
  - ``provenance`` : digest seal + travelled phases (no-tools path only).
  - ``messages``   : immutable tuple of ``(role, content)`` pairs.

Wire form: the HTTP body carries the envelope under ``ENVELOPE_KEY`` next to
the plain OpenAI fields. Bodies WITHOUT the key are legacy EI-1 traffic and
must fall through to ``parse_chat_request`` untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.soul_access.schema import (
    DedupKey,
    Phase,
    Provenance,
    RequestIdentity,
    RequestLocalHolder,
    SoulSnapshot,
    SnapshotRejected,
    full_path,
    new_holder,
    snapshot_digest,
    validate_snapshot,
)

__all__ = [
    "THIN_ENTRY_VERSION",
    "FORWARD_ENDPOINT",
    "ENVELOPE_KEY",
    "ForwardEnvelope",
    "build_envelope",
    "envelope_to_body",
    "envelope_from_body",
    "try_parse_envelope",
]

#: Thin-entry wire version (bumped only by a decision-complete ticket).
THIN_ENTRY_VERSION = "sa1/v1"

#: Fixed internal endpoint the thin entry forwards to. Constant by design:
#: the thin entry may not choose, parametrize, or be redirected elsewhere.
FORWARD_ENDPOINT = "/internal/soul-access/live"

#: Body key marking a soul-access envelope. Legacy bodies never carry it.
ENVELOPE_KEY = "soul_access_v1"


@dataclass(frozen=True)
class ForwardEnvelope:
    """Sealed thin-entry envelope (immutable; SA-0 types only)."""

    snapshot: SoulSnapshot
    provenance: Provenance
    messages: tuple
    endpoint: str = FORWARD_ENDPOINT


def _require_messages(messages) -> tuple:
    if not isinstance(messages, tuple) or not messages:
        raise SnapshotRejected("envelope messages must be a non-empty tuple")
    clean = []
    for item in messages:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(part, str) and part for part in item)
        ):
            raise SnapshotRejected("envelope message must be (role, content) non-empty strs")
        clean.append(item)
    return tuple(clean)


def build_envelope(*, snapshot: SoulSnapshot, messages: tuple, created_utc: str) -> ForwardEnvelope:
    """Seal a thin-entry envelope. Pure; raises SnapshotRejected on fault."""
    validate_snapshot(snapshot)
    if not isinstance(created_utc, str) or not created_utc:
        raise SnapshotRejected("envelope created_utc required")
    clean_messages = _require_messages(messages)
    digest = snapshot_digest(snapshot)
    provenance = Provenance(
        created_utc=created_utc,
        snapshot_digest=digest,
        transitions=tuple(phase.value for phase in full_path(has_tools=False)),
    )
    return ForwardEnvelope(
        snapshot=snapshot,
        provenance=provenance,
        messages=clean_messages,
        endpoint=FORWARD_ENDPOINT,
    )


def build_holder(*, request_id: str, snapshot_digest_hex: str) -> RequestLocalHolder:
    """Mint one request-local holder (capability only, no global state)."""
    if not isinstance(request_id, str) or not request_id:
        raise SnapshotRejected("holder request_id required")
    if not isinstance(snapshot_digest_hex, str) or not snapshot_digest_hex:
        raise SnapshotRejected("holder snapshot digest required")
    return new_holder("sa1-live:" + request_id, (snapshot_digest_hex,))


# -- wire mapping (strict, fail-closed) --------------------------------------

_SNAPSHOT_FIELDS = (
    "backend_alias",
    "model",
    "budget_tokens",
    "deadline_utc",
    "personality_version",
    "emotion_state",
    "relationship_state",
    "chrono_state",
    "inner_life_state",
    "projection_state",
    "recall_version",
    "history_pool_version",
)

_IDENTITY_FIELDS = (
    "request_id",
    "conversation_id",
    "branch_id",
    "turn_id",
    "idempotency_key",
)

_DEDUP_FIELDS = (
    "principal_id",
    "agent_id",
    "scope",
    "conversation_id",
    "branch_id",
    "idempotency_key",
)


def _snapshot_to_mapping(snapshot: SoulSnapshot) -> dict:
    ident = snapshot.identity
    dedup = snapshot.dedup
    return {
        "identity": {name: getattr(ident, name) for name in _IDENTITY_FIELDS},
        "dedup": {name: getattr(dedup, name) for name in _DEDUP_FIELDS},
        "backend_alias": snapshot.backend_alias,
        "model": snapshot.model,
        "params": [list(pair) for pair in snapshot.params],
        "budget_tokens": snapshot.budget_tokens,
        "deadline_utc": snapshot.deadline_utc,
        "personality_version": snapshot.personality_version,
        "emotion_state": snapshot.emotion_state,
        "relationship_state": snapshot.relationship_state,
        "chrono_state": snapshot.chrono_state,
        "inner_life_state": snapshot.inner_life_state,
        "projection_state": snapshot.projection_state,
        "recall_version": snapshot.recall_version,
        "recall_items": list(snapshot.recall_items),
        "history_pool_version": snapshot.history_pool_version,
        "clean_history": list(snapshot.clean_history),
    }


def _snapshot_from_mapping(data) -> SoulSnapshot:
    if not isinstance(data, dict):
        raise SnapshotRejected("envelope snapshot must be a mapping")
    allowed = set(_SNAPSHOT_FIELDS) | {"identity", "dedup", "params", "recall_items", "clean_history"}
    if set(data) - allowed:
        raise SnapshotRejected("envelope snapshot carries unknown fields")
    try:
        ident_raw = data["identity"]
        dedup_raw = data["dedup"]
        params_raw = data["params"]
        recall_raw = data["recall_items"]
        history_raw = data["clean_history"]
    except KeyError as exc:
        raise SnapshotRejected("envelope snapshot missing field: %s" % (exc,)) from exc
    if not isinstance(ident_raw, dict) or set(ident_raw) != set(_IDENTITY_FIELDS):
        raise SnapshotRejected("envelope identity mistyped")
    if not isinstance(dedup_raw, dict) or set(dedup_raw) != set(_DEDUP_FIELDS):
        raise SnapshotRejected("envelope dedup mistyped")
    try:
        params = tuple(tuple(pair) for pair in params_raw)
        recall_items = tuple(recall_raw)
        clean_history = tuple(history_raw)
    except TypeError as exc:
        raise SnapshotRejected("envelope snapshot sequences mistyped") from exc
    try:
        snapshot = SoulSnapshot(
            identity=RequestIdentity(**ident_raw),
            dedup=DedupKey(**dedup_raw),
            params=params,
            recall_items=recall_items,
            clean_history=clean_history,
            **{name: data[name] for name in _SNAPSHOT_FIELDS},
        )
    except TypeError as exc:
        raise SnapshotRejected("envelope snapshot mistyped: %s" % (exc,)) from exc
    return validate_snapshot(snapshot)


def envelope_to_body(envelope: ForwardEnvelope) -> dict:
    """Render an envelope to its HTTP body form (JSON-safe plain data)."""
    if not isinstance(envelope, ForwardEnvelope):
        raise SnapshotRejected("not a ForwardEnvelope")
    validate_snapshot(envelope.snapshot)
    if envelope.endpoint != FORWARD_ENDPOINT:
        raise SnapshotRejected("envelope endpoint must be the fixed internal endpoint")
    return {
        "model": envelope.snapshot.model,
        "messages": [
            {"role": role, "content": content} for role, content in _require_messages(envelope.messages)
        ],
        "stream": False,
        ENVELOPE_KEY: {
            "version": THIN_ENTRY_VERSION,
            "snapshot": _snapshot_to_mapping(envelope.snapshot),
            "provenance": {
                "created_utc": envelope.provenance.created_utc,
                "snapshot_digest": envelope.provenance.snapshot_digest,
                "transitions": list(envelope.provenance.transitions),
            },
        },
    }


def envelope_from_body(body) -> ForwardEnvelope:
    """Rebuild an envelope from its HTTP body form. Strict; fail-closed."""
    if not isinstance(body, dict):
        raise SnapshotRejected("envelope body must be a mapping")
    section = body.get(ENVELOPE_KEY)
    if not isinstance(section, dict):
        raise SnapshotRejected("envelope section missing")
    if section.get("version") != THIN_ENTRY_VERSION:
        raise SnapshotRejected("envelope version mismatch")
    try:
        snap_raw = section["snapshot"]
        prov_raw = section["provenance"]
        messages_raw = body["messages"]
    except KeyError as exc:
        raise SnapshotRejected("envelope body missing field: %s" % (exc,)) from exc
    snapshot = _snapshot_from_mapping(snap_raw)
    if not isinstance(prov_raw, dict):
        raise SnapshotRejected("envelope provenance must be a mapping")
    try:
        provenance = Provenance(
            created_utc=prov_raw["created_utc"],
            snapshot_digest=prov_raw["snapshot_digest"],
            transitions=tuple(prov_raw["transitions"]),
        )
    except (KeyError, TypeError) as exc:
        raise SnapshotRejected("envelope provenance mistyped: %s" % (exc,)) from exc
    if not isinstance(provenance.created_utc, str) or not provenance.created_utc:
        raise SnapshotRejected("envelope provenance created_utc required")
    if not isinstance(provenance.snapshot_digest, str) or not provenance.snapshot_digest:
        raise SnapshotRejected("envelope provenance digest required")
    if not isinstance(messages_raw, list) or not messages_raw:
        raise SnapshotRejected("envelope messages must be a non-empty list")
    pairs = []
    for item in messages_raw:
        if not isinstance(item, dict):
            raise SnapshotRejected("envelope message must be a mapping")
        role, content = item.get("role"), item.get("content")
        if not isinstance(role, str) or not role or not isinstance(content, str) or not content:
            raise SnapshotRejected("envelope message role/content required")
        pairs.append((role, content))
    expected = tuple(phase.value for phase in full_path(has_tools=False))
    if tuple(provenance.transitions) != expected:
        raise SnapshotRejected("envelope provenance must carry the no-tools path")
    if provenance.snapshot_digest != snapshot_digest(snapshot):
        raise SnapshotRejected("envelope digest seal broken")
    return ForwardEnvelope(
        snapshot=snapshot,
        provenance=provenance,
        messages=tuple(pairs),
        endpoint=FORWARD_ENDPOINT,
    )


def try_parse_envelope(body):
    """Return the envelope when the body carries one, else None.

    Legacy bodies (no ENVELOPE_KEY) return None so the caller falls through
    to the legacy parser untouched. A PRESENT-but-broken envelope raises
    SnapshotRejected (fail-closed, never silent fall-through).
    """
    if not isinstance(body, dict) or ENVELOPE_KEY not in body:
        return None
    return envelope_from_body(body)


# Silence unused-import check for the re-exported SA-0 phase type: the
# no-tools path constant below is part of the thin-entry contract surface.
_NO_TOOLS_PATH = full_path(has_tools=False)
assert _NO_TOOLS_PATH[0] is Phase.HEAD_LOAD
