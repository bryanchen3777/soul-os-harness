"""SA-TRANSPORT-OBS-2B failure observation (offline, sandbox only).

Decision-complete scope (SA-TRANSPORT-OBS-2B, mastermind Wolong approved):
zero product change. This module is the ONLY new product file; the companion
``tests/soul_access/test_t19_failure_observation.py`` is the ONLY new test
file; artifacts stay in ``verification/sa-transport-obs-2b/`` (self-built
sandbox) plus per-test ``tmp_path``. Read-only: ``src/llm/proxy.py``,
``src/soul_access/evidence.py`` (whitelisted v1 observation API),
``src/soul_access/wiring.py`` (three wrapper pass-throughs),
``src/soul_access/api.py`` / ``schema.py`` (snapshot validation + digest),
and the B1/B2/B3 originals. Old transport v1 contract
(``sa-transport-obs-1``) is untouched.

Problem: exit-7 failures that carry NO transport snapshot stay silent --
the original lander refuses them (no snapshot / pollution / disk error) and
nothing lands. This module wraps one injected ``dialog`` call: on success the
value returns untouched (no file); on ``Exception`` an outer
``sa-exit7-observation/v1`` record lands atomically, then the ORIGINAL
exception is re-raised identically (identity + cause kept).

Outer record rules (fixed):
  - A valid snapshot (found on the exception chain via the existing
    ``find_transport_obs``) is embedded only after passing the EXISTING
    serialization validation (``serialize_transport_diagnosis`` round-trip);
    the embedded copy is the re-validated canonical form, never the raw input.
  - Missing snapshot -> ``snapshot_status="absent"``; refused snapshot ->
    ``snapshot_status="rejected"``; both carry a fixed reason code.
  - With no valid snapshot the five columns read ``type="unknown"``,
    ``phase="unknown"``, ``chain=None``, ``errno=None``,
    ``elapsed_ms=None``; ``transport_calls=None``,
    ``backend_observations=None`` (always None: no channel exists, never 0,
    never a billing count); ``attempts=[]`` -- zeros and fabricated attempts
    are forbidden.
  - The outer exception classes ride along as ``outer_exception``: at most
    three type names (own -> cause -> context), each whitelist-filtered;
    any name outside the whitelist records ``"UnknownException"``.
  - Never stored: exception messages, ``args``, tracebacks, URLs, headers,
    raw responses. A polluted snapshot is discarded WHOLE (status rejected,
    ``snapshot=None``); scrubbed content never poses as a complete snapshot.
  - Atomic landing (tmp file + ``os.replace``) into the caller-provided
    sandbox directory only; anything under repo ``data/**`` refuses
    structurally (resolved here, never a flag comparison at write time).
  - Observation failure (refusal, disk error, any bug) prints exactly
    ``OBSERVATION_WRITE_FAILED`` -- and nothing else: no original error, no
    retry, and the original exception still propagates identically. Disk
    failure is an explicit limitation: then no record exists.

No CLI entry point. No sockets, no httpx, no provider imports, no clock, no
global mutable state. ``.env`` is never read here.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from src.soul_access import evidence as _sa_evidence

__all__ = [
    "OBSERVATION_KIND",
    "OBSERVATION_VERSION",
    "SNAPSHOT_PRESENT",
    "SNAPSHOT_ABSENT",
    "SNAPSHOT_REJECTED",
    "REASON_EMBEDDED",
    "REASON_ABSENT",
    "REASON_REJECTED",
    "WRITE_FAILED_TOKEN",
    "OUTER_UNKNOWN_NAME",
    "OBSERVATION_PHASES",
    "build_observation",
    "serialize_observation",
    "record_observation",
    "observe_dialog",
    "outer_chain",
]

#: Outer observation kind/version (bumped only by a decision-complete ticket).
OBSERVATION_KIND = "sa-exit7-observation"
OBSERVATION_VERSION = "sa-exit7-observation/v1"

#: Snapshot presence states (closed set; no other value may land).
SNAPSHOT_PRESENT = "present"
SNAPSHOT_ABSENT = "absent"
SNAPSHOT_REJECTED = "rejected"

#: Fixed reason codes, one per state (closed set).
REASON_EMBEDDED = "snapshot_embedded"
REASON_ABSENT = "snapshot_absent"
REASON_REJECTED = "snapshot_rejected"

#: The ONLY text observation failure may emit (never the original error).
WRITE_FAILED_TOKEN = "OBSERVATION_WRITE_FAILED"

#: Slot value for an outer exception class name outside the whitelist.
OUTER_UNKNOWN_NAME = "UnknownException"

#: Outer summary phases: the v1 attempt phases plus ``"unknown"``. The
#: extra value is legal ONLY on the outer row and means "no phase is
#: measurable without a valid snapshot" (absent/rejected); embedded v1
#: snapshots keep their own whitelist (validated by the existing
#: ``serialize_transport_diagnosis`` before embedding).
OBSERVATION_PHASES = ("post", "complete", "submit", "result", "unknown")

#: Credential-shaped needles (same two as the evidence channel by design:
#: a hit refuses the write; scrubbed content never lands as complete).
_SCAN_NEEDLES = ("Bearer", "api_key")

#: Fallback file stem when no usable request id is available.
_UNKNOWN_STEM = "unknown-request"

#: Max outer-exception layers (own -> cause -> context).
_MAX_OUTER_DEPTH = 3

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_DATA = (_REPO_ROOT / "data").resolve()

_TYPE_NAME_RE = None  # compiled lazily (stdlib ``re`` only)


def _type_name_ok(name) -> bool:
    import re

    global _TYPE_NAME_RE
    if _TYPE_NAME_RE is None:
        _TYPE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
    return (
        isinstance(name, str)
        and bool(name)
        and len(name) <= 128
        and _TYPE_NAME_RE.fullmatch(name) is not None
    )


def _plain_stem(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and "/" not in value
        and "\\" not in value
        and ".." not in value
    )


def outer_chain(exc) -> list:
    """Whitelist-filtered outer exception class names (at most three).

    ``[own, cause, context]``; the context slot is skipped when absent or
    identical to the cause. Each slot must pass the type-name whitelist;
    failures record ``"UnknownException"``. Messages, ``args`` and
    tracebacks are never read.
    """
    raw = [exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)]
    names = []
    seen = set()
    for current in raw:
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            name = type(current).__name__
        except Exception:
            name = None
        names.append(name if _type_name_ok(name) else OUTER_UNKNOWN_NAME)
        if len(names) >= _MAX_OUTER_DEPTH:
            break
    if not names:
        return [OUTER_UNKNOWN_NAME]
    return names


def _summary_from_snapshot(snapshot) -> dict:
    """Summary five columns + calls taken from a validated snapshot (no invention)."""
    attempts = snapshot.get("attempts") or []
    first = attempts[0]
    return {
        "type": first["type"],
        "chain": list(first["chain"]),
        "errno": first["errno"],
        "elapsed_ms": first["elapsed_ms"],
        "phase": first["phase"],
        "transport_calls": snapshot.get("transport_calls"),
        "attempts": [dict(item) for item in attempts],
    }


def build_observation(exc, *, dialog_request_id=None) -> dict:
    """Build the outer observation record for one caught exception (pure).

    Never raises for an unfit snapshot: missing -> ``absent``, refused
    (validation / pollution) -> ``rejected``. The record itself is validated
    field-by-field; anything outside the whitelist raises ``ValueError``.
    The exception's message/args/traceback are never read.
    """
    rid = dialog_request_id if _plain_stem(dialog_request_id) else None
    raw = _sa_evidence.find_transport_obs(exc)
    snapshot = None
    if raw is None:
        status, reason = SNAPSHOT_ABSENT, REASON_ABSENT
    else:
        try:
            text = _sa_evidence.serialize_transport_diagnosis(raw)
            snapshot = json.loads(text)
            status, reason = SNAPSHOT_PRESENT, REASON_EMBEDDED
        except Exception:
            snapshot = None
            status, reason = SNAPSHOT_REJECTED, REASON_REJECTED
    if status == SNAPSHOT_PRESENT:
        summary = _summary_from_snapshot(snapshot)
    else:
        summary = {
            "type": "unknown",
            "chain": None,
            "errno": None,
            "elapsed_ms": None,
            "phase": "unknown",
            "transport_calls": None,
            "attempts": [],
        }
    record = {
        "kind": OBSERVATION_KIND,
        "version": OBSERVATION_VERSION,
        "dialog_request_id": rid,
        "snapshot_status": status,
        "snapshot_reason": reason,
        "snapshot": snapshot,
        "type": summary["type"],
        "chain": summary["chain"],
        "errno": summary["errno"],
        "elapsed_ms": summary["elapsed_ms"],
        "phase": summary["phase"],
        "transport_calls": summary["transport_calls"],
        "backend_observations": None,
        "attempts": summary["attempts"],
        "outer_exception": outer_chain(exc),
    }
    _check_record(record)
    text = _canonical_text(record)
    if any(needle in text for needle in _SCAN_NEEDLES):
        if snapshot is not None:
            # Polluted snapshot: discard it WHOLE and re-honest as rejected.
            # Scrubbed content must never pose as a complete snapshot.
            record = dict(record)
            record["snapshot_status"] = SNAPSHOT_REJECTED
            record["snapshot_reason"] = REASON_REJECTED
            record["snapshot"] = None
            record["type"] = "unknown"
            record["chain"] = None
            record["errno"] = None
            record["elapsed_ms"] = None
            record["phase"] = "unknown"
            record["transport_calls"] = None
            record["attempts"] = []
            _check_record(record)
            text = _canonical_text(record)
        if any(needle in text for needle in _SCAN_NEEDLES):
            raise _sa_evidence.EvidenceStoreError(
                "observation unqualified: credential-shaped text"
            )
    return record


def _check_record(record) -> None:
    if not isinstance(record, dict):
        raise ValueError("observation record must be a dict")
    if record.get("kind") != OBSERVATION_KIND:
        raise ValueError("observation kind drift")
    if record.get("version") != OBSERVATION_VERSION:
        raise ValueError("observation version drift")
    rid = record.get("dialog_request_id")
    if rid is not None and not _plain_stem(rid):
        raise ValueError("observation dialog_request_id must be a plain stem or None")
    if record.get("snapshot_status") not in (
        SNAPSHOT_PRESENT,
        SNAPSHOT_ABSENT,
        SNAPSHOT_REJECTED,
    ):
        raise ValueError("observation snapshot_status outside closed set")
    if record.get("snapshot_reason") not in (
        REASON_EMBEDDED,
        REASON_ABSENT,
        REASON_REJECTED,
    ):
        raise ValueError("observation snapshot_reason outside closed set")
    snapshot = record.get("snapshot")
    if snapshot is not None and (
        not isinstance(snapshot, dict)
        or snapshot.get("kind") != _sa_evidence.TRANSPORT_DIAGNOSIS_KIND
    ):
        raise ValueError("observation snapshot must be a v1 diagnosis or None")
    if (record.get("snapshot_status") == SNAPSHOT_PRESENT) != (snapshot is not None):
        raise ValueError("observation snapshot presence must match its status")
    if record.get("type") not in _sa_evidence.TRANSPORT_TYPES:
        raise ValueError("observation type outside whitelist")
    if record.get("phase") not in OBSERVATION_PHASES:
        raise ValueError("observation phase outside whitelist")
    chain = record.get("chain")
    if chain is not None and (
        not isinstance(chain, list)
        or not chain
        or len(chain) > _MAX_OUTER_DEPTH
        or not all(_type_name_ok(name) for name in chain)
    ):
        raise ValueError("observation chain must be 1-3 type names or None")
    errno = record.get("errno")
    if errno is not None and (isinstance(errno, bool) or not isinstance(errno, int)):
        raise ValueError("observation errno must be an int or None")
    elapsed = record.get("elapsed_ms")
    if elapsed is not None and (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(elapsed)
        or elapsed < 0
    ):
        raise ValueError("observation elapsed_ms must be a finite non-negative number or None")
    calls = record.get("transport_calls")
    if calls is not None and (isinstance(calls, bool) or not isinstance(calls, int) or calls < 0):
        raise ValueError("observation transport_calls must be a non-negative int or None")
    if record.get("backend_observations") is not None:
        raise ValueError("observation backend_observations has no channel: must be None")
    if not isinstance(record.get("attempts"), list):
        raise ValueError("observation attempts must be a list")
    outer = record.get("outer_exception")
    if (
        not isinstance(outer, list)
        or not outer
        or len(outer) > _MAX_OUTER_DEPTH
        or not all(
            name == OUTER_UNKNOWN_NAME or _type_name_ok(name) for name in outer
        )
    ):
        raise ValueError("observation outer_exception must be 1-3 whitelisted names")
    if record.get("snapshot_status") != SNAPSHOT_PRESENT and (
        record.get("attempts")
        or record.get("transport_calls") is not None
        or record.get("chain") is not None
        or record.get("errno") is not None
        or record.get("elapsed_ms") is not None
        or record.get("type") != "unknown"
        or record.get("phase") != "unknown"
    ):
        raise ValueError("observation without a valid snapshot must stay unknown/None/empty")


def _canonical_text(record) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)


def serialize_observation(record) -> str:
    """Canonical JSON text of an observation; refuse on pollution.

    Re-validates field-by-field (only whitelisted keys/values can land),
    then scans for credential-shaped text. A hit raises
    ``EvidenceStoreError`` (observation unqualified, never masking the
    original error at the call site).
    """
    _check_record(record)
    text = _canonical_text(record)
    if any(needle in text for needle in _SCAN_NEEDLES):
        raise _sa_evidence.EvidenceStoreError(
            "observation unqualified: credential-shaped text"
        )
    return text


def record_observation(*, directory, dialog_request_id, record) -> Path:
    """Atomically land one observation as ``<dialog_request_id>.json``.

    ``directory`` is caller-provided (the ticket sandbox or per-test
    ``tmp_path``). Paths under repo ``data/**`` refuse structurally.
    Landing is tmp-file + ``os.replace``. Returns the landed path; raises
    ``EvidenceStoreError`` on refusal/failure.
    """
    if not isinstance(record, dict):
        raise _sa_evidence.EvidenceStoreError("observation record must be a dict")
    _check_record(record)
    stem = dialog_request_id if _plain_stem(dialog_request_id) else _UNKNOWN_STEM
    try:
        resolved = Path(directory).expanduser().resolve()
    except Exception as exc:
        raise _sa_evidence.EvidenceStoreError(
            "observation directory unresolvable: %s" % type(exc).__name__
        ) from exc
    if resolved == _PROD_DATA or _PROD_DATA in resolved.parents:
        raise _sa_evidence.EvidenceStoreError("observation directory under data/** refused")
    text = serialize_observation(record)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise _sa_evidence.EvidenceStoreError(
            "observation directory not creatable: %s" % type(exc).__name__
        ) from exc
    path = resolved / ("%s.json" % stem)
    tmp = path.with_name(".tmp-%s-%d-%d" % (path.name, os.getpid(), id(record)))
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise _sa_evidence.EvidenceStoreError(
            "observation landing failed: %s" % type(exc).__name__
        ) from exc
    return path


def observe_dialog(dialog, messages, *, directory, agent_id=None, request_id=None):
    """Call the injected ``dialog`` exactly once with observation on failure.

    Success returns the dialog value unchanged (no file). ``Exception`` is
    observed (one atomic outer record) and then re-raised IDENTICALLY --
    same object, same cause chain. Observation failure prints exactly
    ``OBSERVATION_WRITE_FAILED`` (no original error, no retry) and never
    masks the original exception. ``BaseException`` is never observed.
    """
    try:
        return dialog(messages, agent_id=agent_id, request_id=request_id)
    except Exception as exc:
        try:
            record = build_observation(exc, dialog_request_id=request_id)
            record_observation(
                directory=directory, dialog_request_id=request_id, record=record
            )
        except Exception:
            print(WRITE_FAILED_TOKEN)
        raise
