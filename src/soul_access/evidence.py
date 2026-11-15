"""SA-EVIDENCE-CHANNEL-1 (Sol approved): per-request evidence sink (offline).

Single-fire probe evidence was stuck at EVIDENCE_GAP because ``dialog``
returns ``str`` only (raw + usage never leave the transport). This module
provides the *original-evidence capability*: the caller-owned sandbox
directory where the backend deposits, per request,

  1. the actual provider payload + its sha256 (pre-send, replayable), and
  2. the same response's verbatim transport text + native usage (post-return),

correlated by one ``request_id``.

Scope (decision-complete, no deviations):

  - stdlib only; no provider/backend imports, no socket/httpx at import.
  - The sink writes ONLY to the caller-provided sandbox directory (tests use
    ``tmp_path``; ticket evidence uses ``verification/sa-evidence-channel-1/``).
    Resolving under repo ``data/**`` refuses at construction (structural,
    never a flag comparison at write time).
  - Atomic landing: each record is written to a unique tmp file then
    ``os.replace``-renamed. No credentials are ever stored: only the key
    *variable name* (``key_ref``) is recorded, never the secret.
  - No "last result" global/module cache of any kind: every record is keyed
    by its explicit ``request_id``. Concurrent requests stay isolated by
    distinct ``request_id`` files (separate sinks or one shared sink alike).

Raised errors: ``EvidenceGap`` (missing raw/usage -- unqualified, no resend),
``EvidenceStoreError`` (landing failure -- unqualified, no resend).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from pathlib import Path


EVIDENCE_VERSION = "sa-evidence-channel-1"

#: Key *variable name* recorded on every artifact. The secret value itself
#: is never read here and never stored.
KEY_REF = "OLLAMA_API_KEY"

REQUEST_KIND = "sa-evidence-request"
RESPONSE_KIND = "sa-evidence-response"


class EvidenceError(Exception):
    """Base for all evidence-channel failures (unqualified, never resend)."""


class EvidenceGap(EvidenceError):
    """Response lacks the native raw text or native usage (missing field)."""


class EvidenceStoreError(EvidenceError):
    """A record could not be landed atomically in the sandbox directory."""


_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_DATA = (_REPO_ROOT / "data").resolve()

_counter = itertools.count(1)


def canonical_bytes(payload) -> bytes:
    """Canonical bytes of the provider payload (hash input).

    The backend hands the *same* ``json_body`` dict to the transport; the
    hash is computed over these canonical bytes so the test-side fake
    transport can recompute it from the body it actually received and prove
    ``recorded payload == sent body`` without trusting templates.
    """
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_response(raw_text, usage) -> tuple:
    """Validate native transport evidence; raise ``EvidenceGap`` if unfit.

    Returns ``(raw_text, usage)`` unchanged on success. ``raw_text`` must be
    the verbatim transport body (non-empty ``str``); ``usage`` must be the
    native non-empty mapping. Anything else is unqualified: no resend.
    """
    if not isinstance(raw_text, str) or not raw_text:
        raise EvidenceGap("evidence response missing verbatim raw text")
    if not isinstance(usage, dict) or not usage:
        raise EvidenceGap("evidence response missing native usage")
    return raw_text, usage


class EvidenceSink:
    """Caller-owned sandbox directory for one evidence capture session.

    ``directory`` must be a caller-provided sandbox path (``tmp_path`` in
    tests). It is resolved once here; anything under repo ``data/**``
    refuses immediately with ``EvidenceStoreError``.
    """

    def __init__(self, directory, *, key_ref: str = KEY_REF):
        if not isinstance(key_ref, str) or not key_ref:
            raise ValueError("evidence key_ref must be a non-empty name")
        try:
            resolved = Path(directory).expanduser().resolve()
        except Exception as exc:
            raise EvidenceStoreError(
                "evidence directory unresolvable: %s" % type(exc).__name__
            ) from exc
        if resolved == _PROD_DATA or _PROD_DATA in resolved.parents:
            raise EvidenceStoreError("evidence directory under data/** refused")
        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise EvidenceStoreError(
                "evidence directory not creatable: %s" % type(exc).__name__
            ) from exc
        self._directory = resolved
        self._key_ref = key_ref

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def key_ref(self) -> str:
        return self._key_ref

    def request_path(self, request_id: str) -> Path:
        self._check_request_id(request_id)
        return self._directory / ("%s.request.json" % request_id)

    def response_path(self, request_id: str) -> Path:
        self._check_request_id(request_id)
        return self._directory / ("%s.response.json" % request_id)

    @staticmethod
    def _check_request_id(request_id) -> None:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("evidence request_id must be a non-empty str")
        if "/" in request_id or "\\" in request_id or ".." in request_id:
            raise ValueError("evidence request_id must be a plain file stem")

    def _atomic_write_json(self, path: Path, obj: dict) -> Path:
        text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)
        tmp = path.with_name(
            ".tmp-%s-%d-%d" % (path.name, os.getpid(), next(_counter))
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception as exc:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise EvidenceStoreError(
                "evidence landing failed: %s" % type(exc).__name__
            ) from exc
        return path

    def record_request(self, *, request_id: str, payload: dict, payload_sha256: str) -> Path:
        """Land the pre-send provider payload + hash. Returns the file path."""
        self._check_request_id(request_id)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("evidence payload must be a non-empty dict")
        if (
            not isinstance(payload_sha256, str)
            or len(payload_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in payload_sha256)
        ):
            raise ValueError("evidence payload_sha256 must be a sha256 hex digest")
        return self._atomic_write_json(
            self.request_path(request_id),
            {
                "kind": REQUEST_KIND,
                "version": EVIDENCE_VERSION,
                "request_id": request_id,
                "payload": payload,
                "payload_sha256": payload_sha256,
                "key_ref": self._key_ref,
            },
        )

    def record_response(self, *, request_id: str, raw_text: str, usage: dict) -> Path:
        """Land the post-return verbatim raw text + native usage."""
        self._check_request_id(request_id)
        check_response(raw_text, usage)
        return self._atomic_write_json(
            self.response_path(request_id),
            {
                "kind": RESPONSE_KIND,
                "version": EVIDENCE_VERSION,
                "request_id": request_id,
                "raw_text": raw_text,
                "raw_sha256": sha256_hex(raw_text.encode("utf-8")),
                "usage": usage,
                "key_ref": self._key_ref,
            },
        )

    def verify_pair(self, request_id: str) -> dict:
        """Re-verify one landed pair (hash recompute, no trust in stored digests).

        Returns a report dict; every ``*_ok`` flag must be ``True`` for the
        pair to count as qualified. Raises ``EvidenceStoreError`` when either
        file is missing or unparsable.
        """
        self._check_request_id(request_id)
        try:
            request = json.loads(self.request_path(request_id).read_text(encoding="utf-8"))
            response = json.loads(
                self.response_path(request_id).read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise EvidenceStoreError(
                "evidence pair unreadable: %s" % type(exc).__name__
            ) from exc
        recomputed_payload = sha256_hex(canonical_bytes(request.get("payload")))
        recomputed_raw = sha256_hex(str(response.get("raw_text", "")).encode("utf-8"))
        report = {
            "request_id": request_id,
            "request_kind_ok": request.get("kind") == REQUEST_KIND,
            "response_kind_ok": response.get("kind") == RESPONSE_KIND,
            "ids_match": request.get("request_id") == request_id
            and response.get("request_id") == request_id,
            "payload_sha256_ok": request.get("payload_sha256") == recomputed_payload,
            "raw_sha256_ok": response.get("raw_sha256") == recomputed_raw,
            "usage_ok": isinstance(response.get("usage"), dict)
            and bool(response.get("usage")),
        }
        report["pair_ok"] = all(
            report[key]
            for key in (
                "request_kind_ok",
                "response_kind_ok",
                "ids_match",
                "payload_sha256_ok",
                "raw_sha256_ok",
                "usage_ok",
            )
        )
        return report


# -- SA-TRANSPORT-OBS-1 (Sol approved): transport failure observation ---------
#
# Exit-7 (transport exhaustion) previously left zero persisted evidence. This
# section adds a *whitelisted* transport diagnosis: per-attempt records with a
# fixed five-field shape (type / chain / errno / elapsed_ms / phase), the
# transport call count, and complete/submit/result boundary marks. It is
# observation-only: stdlib only, no provider/backend/socket/httpx imports at
# module import (classification keys off ``type(exc).__name__`` strings so
# this module never touches the network stack), no globals, no caches -- every
# record is built per call and keyed by its explicit request ids.
#
# Privacy: only type-name labels, ints, floats and id stems are ever
# serialized. Exception messages, ``repr``, tracebacks, ``Authorization``,
# credential values and request/response bodies can never enter the schema
# (the builders accept nothing else). Before landing, the canonical text is
# scanned for ``Bearer`` / ``api_key`` (same needles as the original-evidence
# channel); a hit refuses the write with ``EvidenceStoreError`` (observation
# unqualified -- never masking the original transport error).

TRANSPORT_DIAGNOSIS_KIND = "sa-transport-diagnosis"
TRANSPORT_DIAGNOSIS_VERSION = "sa-transport-obs-1"

#: Whitelisted transport type labels. These are *type inferences* from the
#: raised exception class, not DNS/TCP/TLS measurements.
TRANSPORT_TYPES = (
    "connect",
    "connect_timeout",
    "read_timeout",
    "write_timeout",
    "pool_timeout",
    "unknown",
)

#: Whitelisted attempt/boundary phase labels.
TRANSPORT_PHASES = ("post", "complete", "submit", "result")

#: Exception-class-name -> transport label. Anything else is ``unknown``.
_TRANSPORT_TYPE_BY_NAME = {
    "ConnectError": "connect",
    "ConnectTimeout": "connect_timeout",
    "ReadTimeout": "read_timeout",
    "WriteTimeout": "write_timeout",
    "PoolTimeout": "pool_timeout",
}

_TRANSPORT_SCAN_NEEDLES = ("Bearer", "api_key")

#: Attribute carrying the per-call transport snapshot along an exception
#: (``raise ... from exc``) chain. Plain data dict, never an exception.
TRANSPORT_OBS_ATTR = "_sa_transport_obs"

_TYPE_NAME_RE = None  # compiled lazily (stdlib ``re`` only, no import cost here)


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


def classify_transport_type(exc) -> str:
    """Map an exception instance to a whitelisted transport label.

    Keyed off ``type(exc).__name__`` only (no httpx import, no I/O). Unknown
    classes -- including plain ``TimeoutException`` or non-network errors --
    map to ``"unknown"``.
    """
    return _TRANSPORT_TYPE_BY_NAME.get(type(exc).__name__, "unknown")


def transport_chain(exc) -> list:
    """Direct cause/context type-name chain (one layer each, at most 3 names).

    ``[own, cause, context]``; the context slot is skipped when it is absent
    or identical to the cause. Messages, args and tracebacks are never read.
    """
    names = [type(exc).__name__]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        names.append(type(cause).__name__)
    context = getattr(exc, "__context__", None)
    if context is not None and context is not cause:
        names.append(type(context).__name__)
    return names


def transport_errno(exc):
    """First integer ``errno`` along self -> cause -> context; else ``None``.

    Only a direct ``.errno`` attribute that is an ``int`` (``bool`` excluded)
    counts. Nothing is guessed, parsed or coerced from messages.
    """
    seen = set()
    queue = [exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)]
    for current in queue:
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        value = getattr(current, "errno", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def build_transport_attempt(*, seq, exc_type, chain, errno, elapsed_ms, phase="post") -> dict:
    """Build one whitelisted per-attempt record (fixed five fields + seq).

    Raises ``ValueError`` on anything outside the whitelist (missing field,
    wrong label, non-int errno, negative/non-finite elapsed, bad phase).
    """
    import math

    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValueError("transport attempt seq must be a non-negative int")
    if exc_type not in TRANSPORT_TYPES:
        raise ValueError("transport attempt type outside whitelist: %r" % (exc_type,))
    if (
        not isinstance(chain, (list, tuple))
        or not chain
        or len(chain) > 3
        or not all(_type_name_ok(name) for name in chain)
    ):
        raise ValueError("transport attempt chain must be 1-3 type names")
    if errno is not None and (
        isinstance(errno, bool) or not isinstance(errno, int)
    ):
        raise ValueError("transport attempt errno must be an int or None")
    if (
        isinstance(elapsed_ms, bool)
        or not isinstance(elapsed_ms, (int, float))
        or not math.isfinite(elapsed_ms)
        or elapsed_ms < 0
    ):
        raise ValueError("transport attempt elapsed_ms must be a finite non-negative number")
    if phase not in TRANSPORT_PHASES:
        raise ValueError("transport attempt phase outside whitelist: %r" % (phase,))
    return {
        "seq": seq,
        "type": exc_type,
        "chain": list(chain),
        "errno": errno,
        "elapsed_ms": elapsed_ms,
        "phase": phase,
    }


def build_transport_diagnosis(
    *,
    dialog_request_id=None,
    evidence_request_id=None,
    transport_calls,
    backend_observations=None,
    attempts,
    boundaries=None,
    observation_ok=True,
) -> dict:
    """Build the whitelisted exit-7 transport diagnosis dict.

    ``backend_observations`` must stay ``None`` (no backend-observation
    channel exists; never 0, never a billing count). ``attempts`` is a list of
    ``build_transport_attempt`` dicts (exact keys enforced). ``boundaries`` is
    a subset of ``{complete, submit, result} -> bool``.
    """
    for label, value in (
        ("dialog_request_id", dialog_request_id),
        ("evidence_request_id", evidence_request_id),
    ):
        if value is not None:
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError("transport %s must be a short non-empty str" % label)
            if "/" in value or "\\" in value or ".." in value:
                raise ValueError("transport %s must be a plain id stem" % label)
    if (
        isinstance(transport_calls, bool)
        or not isinstance(transport_calls, int)
        or transport_calls < 0
    ):
        raise ValueError("transport transport_calls must be a non-negative int")
    if backend_observations is not None:
        raise ValueError("transport backend_observations has no channel: must be None")
    if not isinstance(attempts, (list, tuple)):
        raise ValueError("transport attempts must be a list")
    clean_attempts = []
    for item in attempts:
        if not isinstance(item, dict) or set(item.keys()) != {
            "seq",
            "type",
            "chain",
            "errno",
            "elapsed_ms",
            "phase",
        }:
            raise ValueError("transport attempt has non-whitelisted keys")
        clean_attempts.append(
            build_transport_attempt(
                seq=item["seq"],
                exc_type=item["type"],
                chain=item["chain"],
                errno=item["errno"],
                elapsed_ms=item["elapsed_ms"],
                phase=item["phase"],
            )
        )
    clean_boundaries = {}
    if boundaries:
        if not isinstance(boundaries, dict):
            raise ValueError("transport boundaries must be a dict")
        for key, value in boundaries.items():
            if key not in ("complete", "submit", "result") or not isinstance(value, bool):
                raise ValueError("transport boundary outside whitelist: %r" % (key,))
            clean_boundaries[key] = value
    if not isinstance(observation_ok, bool):
        raise ValueError("transport observation_ok must be a bool")
    return {
        "kind": TRANSPORT_DIAGNOSIS_KIND,
        "version": TRANSPORT_DIAGNOSIS_VERSION,
        "dialog_request_id": dialog_request_id,
        "evidence_request_id": evidence_request_id,
        "transport_calls": transport_calls,
        "backend_observations": None,
        "attempts": clean_attempts,
        "boundaries": clean_boundaries,
        "observation_ok": observation_ok,
    }


def serialize_transport_diagnosis(diagnosis) -> str:
    """Canonical JSON text of a whitelisted diagnosis; refuse on pollution.

    Re-validates through ``build_transport_diagnosis`` (so only whitelisted
    keys/values can land), then scans for ``Bearer`` / ``api_key``. A hit
    raises ``EvidenceStoreError`` (observation unqualified, never masking the
    original error at the call site).
    """
    clean = build_transport_diagnosis(
        dialog_request_id=diagnosis.get("dialog_request_id"),
        evidence_request_id=diagnosis.get("evidence_request_id"),
        transport_calls=diagnosis.get("transport_calls"),
        backend_observations=diagnosis.get("backend_observations"),
        attempts=diagnosis.get("attempts"),
        boundaries=diagnosis.get("boundaries"),
        observation_ok=diagnosis.get("observation_ok", True),
    )
    if clean.get("kind") != TRANSPORT_DIAGNOSIS_KIND or clean.get("version") != TRANSPORT_DIAGNOSIS_VERSION:
        raise EvidenceStoreError("transport diagnosis kind/version drift")
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True, indent=2)
    for needle in _TRANSPORT_SCAN_NEEDLES:
        if needle in text:
            raise EvidenceStoreError(
                "transport observation unqualified: credential-shaped text"
            )
    return text


def record_transport_diagnosis(*, directory, dialog_request_id, diagnosis):
    """Atomically land one diagnosis as ``<dialog_request_id>.json``.

    ``directory`` is caller-provided (the live-fire ticket uses its own
    ``transport/`` dir, never the ``evidence/`` pair dir). Paths under repo
    ``data/**`` refuse structurally. Landing is tmp-file + ``os.replace``.
    Returns the landed path; raises ``EvidenceStoreError`` on refusal/failure.
    """
    if not isinstance(diagnosis, dict):
        raise EvidenceStoreError("transport diagnosis must be a dict")
    EvidenceSink._check_request_id(dialog_request_id)
    try:
        resolved = Path(directory).expanduser().resolve()
    except Exception as exc:
        raise EvidenceStoreError(
            "transport directory unresolvable: %s" % type(exc).__name__
        ) from exc
    if resolved == _PROD_DATA or _PROD_DATA in resolved.parents:
        raise EvidenceStoreError("transport directory under data/** refused")
    text = serialize_transport_diagnosis(diagnosis)
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise EvidenceStoreError(
            "transport directory not creatable: %s" % type(exc).__name__
        ) from exc
    path = resolved / ("%s.json" % dialog_request_id)
    tmp = path.with_name(".tmp-%s-%d-%d" % (path.name, os.getpid(), next(_counter)))
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise EvidenceStoreError(
            "transport landing failed: %s" % type(exc).__name__
        ) from exc
    return path


def attach_transport_obs(exc, diagnosis) -> None:
    """Attach a diagnosis snapshot to an exception (observation only)."""
    if exc is None or not isinstance(diagnosis, dict):
        return
    try:
        setattr(exc, TRANSPORT_OBS_ATTR, diagnosis)
    except Exception:
        pass


def find_transport_obs(exc):
    """First diagnosis snapshot along ``exc`` -> cause -> context (else None)."""
    seen = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        obs = getattr(current, TRANSPORT_OBS_ATTR, None)
        if isinstance(obs, dict) and obs.get("kind") == TRANSPORT_DIAGNOSIS_KIND:
            return obs
        stack.append(getattr(current, "__cause__", None))
        stack.append(getattr(current, "__context__", None))
        if len(seen) > 32:
            break
    return None


def propagate_transport_obs(new_exc, caught_exc, *, boundary=None, request_id=None) -> None:
    """Carry a transport snapshot onto a wrapper exception (never raises).

    Shallow-copies the snapshot found on ``caught_exc`` (or its cause chain),
    marks ``boundaries[boundary]`` and fills ``dialog_request_id`` from the
    wrapper's ``request_id`` when still unset, then attaches it to ``new_exc``.
    No snapshot found, or anything unfit, means no-op: legacy/unconfigured
    paths keep their exact old behavior.
    """
    try:
        obs = find_transport_obs(caught_exc)
        if obs is None:
            return
        carried = dict(obs)
        carried_boundaries = dict(obs.get("boundaries") or {})
        if boundary is not None:
            if boundary not in ("complete", "submit", "result"):
                return
            carried_boundaries[boundary] = True
        carried["boundaries"] = carried_boundaries
        if (
            carried.get("dialog_request_id") is None
            and isinstance(request_id, str)
            and request_id
            and "/" not in request_id
            and "\\" not in request_id
            and ".." not in request_id
        ):
            carried["dialog_request_id"] = request_id
        attach_transport_obs(new_exc, carried)
    except Exception:
        pass
