"""SA-4 dual-memory admission + B firewall (decision-complete).

Decision-complete scope (SA-4): this module + ``harness/tool_memory.py`` +
``tests/soul_access/test_t4_memory.py`` are new; the proxy gains two additive
policy-gate call sites (failure branch ~:4912, history-write block ~:4948)
plus one request-local junction block; the middleware gains one pre-commit
gate in ``_on_agent_speak``. No writer / SAGE / generation line is modified.

Dual-memory rule (Owner B方案, 2026-11-11):

  - Tools live in the harness. Tool details (paths / param templates / raw
    results / failure experience) are stored ONLY in ``harness/tool_memory.py``
    under the ``harness/`` namespace. The soul side MUST NOT read them.
  - The soul side (long-term sinks: history / MemoryStore / SAGE / InnerLife /
    projection) receives ONLY:
      1. the human's verbatim text (user_text), and
      2. clean final texts, including completion evidence built EXCLUSIVELY
         from the whitelisted template ``build_completion_note`` — never from
         tool output, and never via an extra LLM summarisation call.
  - A legitimate tool round does NOT trigger a ban by itself. A ban triggers
    only when a candidate soul text overflows tool content (tool names /
    params / raw results / sentinels, paraphrased laundering included): the
    WHOLE round stores zero (user text included — writing user first is
    banned, so the gate always runs BEFORE any write).
  - The admission credential is minted service-side (``mint_admission``).
    Payload booleans are NEVER consulted — ``decide`` has no such parameter,
    so a forged ``{"clean": true}`` cannot open the gate.
  - Partial write then contact loss isolates the round (``RoundLedger``):
    no backfill, no retry for that key.

Purity: stdlib only (threading is used for the request-local round context,
mirroring the SA-3 proxy junction; no network / subprocess / clock / IO).
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from dataclasses import field

__all__ = [
    "SA4_ADMISSION_VERSION",
    "SOUL_NAMESPACE",
    "HARNESS_NAMESPACE",
    "COMPLETION_KINDS",
    "COMPLETION_TEMPLATE",
    "AdmissionRejected",
    "AdmissionCredential",
    "AdmissionDecision",
    "RoundResult",
    "RoundLedger",
    "build_completion_note",
    "mint_admission",
    "is_trusted",
    "decide",
    "soul_may_read",
    "set_round_context",
    "get_round_context",
    "reset_round_context",
    "round_soul_write_allowed",
    "commit_soul_round",
]

#: Admission version (bumped only by a decision-complete ticket).
SA4_ADMISSION_VERSION = "sa4/v1"

#: Namespace separation: soul sinks live under ``soul/``, harness tool
#: memory under ``harness/``. Neither side reads the other's namespace.
SOUL_NAMESPACE = "soul"
HARNESS_NAMESPACE = "harness"

#: Whitelisted completion-evidence kinds (the ONLY soul-side completion text).
COMPLETION_KINDS = ("查詢", "整理", "檢查", "修改")

#: Whitelisted completion template (instantiated, never LLM-summarised).
COMPLETION_TEMPLATE = "我幫你完成了這次%s。"


class AdmissionRejected(Exception):
    """Fail-closed refusal: bad admission use (400) or untrusted claim."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class AdmissionCredential:
    """Opaque service-minted admission credential (trust by registry, not bool).

    Anyone can CONSTRUCT one, but only ``mint_admission`` REGISTERS its nonce,
    so a forged instance (or a payload boolean) is never trusted.
    """

    nonce: str


#: Monotonic mint registry (mint-only by design; the single deliberate
#: module-global mutable state in this module — everything else is pure).
_MINTED_NONCES: set = set()


def mint_admission() -> AdmissionCredential:
    """Mint one trusted admission credential (service side only)."""
    credential = AdmissionCredential(nonce=secrets.token_hex(16))
    _MINTED_NONCES.add(credential.nonce)
    return credential


def is_trusted(credential) -> bool:
    """True only for service-minted credentials (payload bools: no such input)."""
    return (
        isinstance(credential, AdmissionCredential)
        and isinstance(credential.nonce, str)
        and bool(credential.nonce)
        and credential.nonce in _MINTED_NONCES
    )


def build_completion_note(kind: str) -> str:
    """Build soul-side completion evidence from the whitelist template only.

    No LLM call, no tool content: the kind must be whitelisted, else 400.
    """
    if kind not in COMPLETION_KINDS:
        raise AdmissionRejected(400, "invalid_request", "completion kind not whitelisted")
    return COMPLETION_TEMPLATE % (kind,)


@dataclass(frozen=True)
class AdmissionDecision:
    """One gate verdict (allowed + machine-readable code)."""

    allowed: bool
    code: str
    reason: str


def _require_text(name: str, value):
    """Return the text, or None when mistyped (caller denies fail-closed)."""
    if not isinstance(value, str):
        return None
    return value


def _require_markers(tool_markers) -> tuple:
    if not isinstance(tool_markers, (tuple, list)):
        raise AdmissionRejected(400, "invalid_request", "tool_markers must be a tuple")
    clean = []
    for marker in tool_markers:
        if not isinstance(marker, str) or not marker:
            raise AdmissionRejected(400, "invalid_request", "tool marker mistyped")
        clean.append(marker)
    return tuple(clean)


def decide(
    *,
    user_text: str = "",
    assistant_text: str = "",
    tool_markers=(),
    credential=None,
) -> AdmissionDecision:
    """Decide whether this round may touch soul long-term sinks.

    Rules (fixed):

      - Non-str candidates => deny ``mistyped`` (data-plane fail-closed: the
        round stores zero instead of raising through the caller).
      - Non-empty ``tool_markers`` WITHOUT a trusted service credential =>
        deny ``untrusted-tool-claim`` (a payload claim of tool use is never
        honoured; note there is deliberately NO payload-bool parameter).
      - Any trusted marker (or the ``harness/`` namespace token) appearing in
        EITHER candidate => deny ``tool-leak`` / ``namespace-leak`` for the
        WHOLE round (user text included; paraphrase carrying a distinctive
        token is still a substring hit, so laundering is caught).
      - Otherwise => allow (legit tool rounds store; legacy no-tool rounds
        store; the gate bans leaks, not tool use).
    """
    user_text, assistant_text = _require_text("user_text", user_text), _require_text(
        "assistant_text", assistant_text
    )
    if user_text is None or assistant_text is None:
        return AdmissionDecision(
            allowed=False,
            code="mistyped",
            reason="candidate soul text must be str",
        )
    markers = _require_markers(tool_markers)
    trusted = is_trusted(credential)
    if markers and not trusted:
        return AdmissionDecision(
            allowed=False,
            code="untrusted-tool-claim",
            reason="tool round claimed without a service-minted credential",
        )
    for marker in markers:
        if marker in user_text or marker in assistant_text:
            return AdmissionDecision(
                allowed=False,
                code="tool-leak",
                reason="candidate soul text overflows tool content",
            )
    namespace_token = HARNESS_NAMESPACE + "/"
    if namespace_token in user_text or namespace_token in assistant_text:
        return AdmissionDecision(
            allowed=False,
            code="namespace-leak",
            reason="candidate soul text references the harness namespace",
        )
    return AdmissionDecision(allowed=True, code="clean", reason="no tool overflow")


def soul_may_read(key) -> bool:
    """False for harness-namespace keys (soul must never read tool memory)."""
    return not (isinstance(key, str) and key.startswith(HARNESS_NAMESPACE + "/"))


# -- request-local round context (service-set, payload-unread) ----------------

_ROUND_STATE = threading.local()  # threading: request-local only, mirroring SA-3.


def set_round_context(*, tool_markers=(), credential=None) -> None:
    """Set this request's tool markers + credential (service side only)."""
    markers = _require_markers(tool_markers)
    if credential is not None and not isinstance(credential, AdmissionCredential):
        raise AdmissionRejected(400, "invalid_request", "credential mistyped")
    _ROUND_STATE.markers = markers
    _ROUND_STATE.credential = credential


def get_round_context() -> tuple:
    """Return ``(tool_markers, credential)`` for this request (defaults empty)."""
    return (
        tuple(getattr(_ROUND_STATE, "markers", ())),
        getattr(_ROUND_STATE, "credential", None),
    )


def reset_round_context() -> None:
    """Clear this request's round context (request boundary)."""
    _ROUND_STATE.markers = ()
    _ROUND_STATE.credential = None


def round_soul_write_allowed(*, user_text: str = "", assistant_text: str = "") -> bool:
    """Gate helper for the proxy / middleware call sites (round-context read)."""
    markers, credential = get_round_context()
    return decide(
        user_text=user_text,
        assistant_text=assistant_text,
        tool_markers=markers,
        credential=credential,
    ).allowed


# -- round commit (decision-before-write, isolate-on-partial) -----------------

@dataclass(frozen=True)
class RoundResult:
    """One round-commit outcome (zero new writes on deny / replay / isolate)."""

    allowed: bool
    replayed: bool
    writes: int
    isolated: bool
    code: str


@dataclass
class RoundLedger:
    """Per-scope completed/isolated round keys (instance state, no globals).

    Partial write then contact loss ISOLATES the key: later commits with the
    same key write zero (no backfill, no retry).
    """

    completed: dict = field(default_factory=dict)
    isolated: set = field(default_factory=set)

    def is_completed(self, round_key: str) -> bool:
        """True when this key already committed (replay writes zero)."""
        return round_key in self.completed

    def is_isolated(self, round_key: str) -> bool:
        """True when this key was isolated after a partial write."""
        return round_key in self.isolated


#: Default shared ledger (service path; tests use fresh ``RoundLedger()``).
_DEFAULT_LEDGER = RoundLedger()


def commit_soul_round(
    *,
    round_key: str,
    sinks,
    user_text: str = "",
    assistant_text: str = "",
    tool_markers=(),
    credential=None,
    ledger: RoundLedger | None = None,
) -> RoundResult:
    """Commit one round to soul sinks (decision structurally BEFORE writes).

    ``sinks`` is an ordered sequence of ``.append(record)`` targets (spy
    lists in tests; history / MemoryStore / SAGE / InnerLife / projection in
    service). Each sink receives its own record copy. Order: replay check ->
    isolate check -> decide -> writes. A denied round touches ZERO sinks
    (``decide`` runs before the first ``append``); a mid-write failure
    isolates the key with no backfill.
    """
    if not isinstance(round_key, str) or not round_key:
        raise AdmissionRejected(400, "invalid_request", "round key required")
    if not isinstance(sinks, (tuple, list)):
        raise AdmissionRejected(400, "invalid_request", "sinks must be a sequence")
    active = ledger if ledger is not None else _DEFAULT_LEDGER
    if not isinstance(active, RoundLedger):
        raise AdmissionRejected(400, "invalid_request", "ledger mistyped")
    if active.is_isolated(round_key):
        return RoundResult(
            allowed=False, replayed=False, writes=0, isolated=True, code="isolated"
        )
    if active.is_completed(round_key):
        return RoundResult(
            allowed=False, replayed=True, writes=0, isolated=False, code="replayed"
        )
    verdict = decide(
        user_text=user_text,
        assistant_text=assistant_text,
        tool_markers=tool_markers,
        credential=credential,
    )
    if not verdict.allowed:
        return RoundResult(
            allowed=False, replayed=False, writes=0, isolated=False, code=verdict.code
        )
    record = {"round": round_key, "user": user_text, "assistant": assistant_text}
    writes = 0
    for sink in sinks:
        try:
            sink.append(dict(record))
        except Exception:
            active.isolated.add(round_key)
            return RoundResult(
                allowed=True,
                replayed=False,
                writes=writes,
                isolated=True,
                code="partial-isolated",
            )
        writes += 1
    active.completed[round_key] = writes
    return RoundResult(
        allowed=True, replayed=False, writes=writes, isolated=False, code="committed"
    )
