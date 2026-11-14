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
