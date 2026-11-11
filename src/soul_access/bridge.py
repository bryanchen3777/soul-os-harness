"""SA-1 :8000-side live-loop bridge (re-validate, then use live instances).

The bridge runs on the :8000 side. For each inbound envelope it:

  1. re-validates auth-adjacent evidence (:8000 side never trusts the thin
     entry blind): SA-0 ``validate_snapshot`` + digest-seal check +
     no-tools phase walk (``HEAD_LOAD -> GEN1 -> ADMIT``; ``TOOL_LOOP`` is
     never entered on this path);
  2. consults the release gate (``release.is_live_inbound_open`` hot-read per
     inbound; closed refuses new inbound);
  3. drives the request through the INJECTED live instances
     (``LiveLoop``: the very ``bus`` / ``llm`` / ``middleware`` objects the
     launcher built, cf. ``scripts/run_server.py`` lifespan wiring). This
     module NEVER constructs a bus, an LLM proxy, or middleware: any missing
     instance refuses fail-closed instead of self-building one.

Generation accounting mirrors the EI-1 contract (one provider attempt, zero
tool rounds): exactly one ``generate`` call per admitted request, so
``BridgeResult.loads == 1`` and ``BridgeResult.generation_attempts == 1``.

Purity of imports: stdlib + SA-0 ``schema`` + sibling ``api`` / ``release``
only. No EI-1 ``src.interfaces`` imports, no provider/backend imports, and
(offline-harness only) no ``soul_context_adapter`` import by ticket ban.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from src.soul_access.api import ForwardEnvelope
from src.soul_access.api import build_holder
from src.soul_access.release import is_live_inbound_open
from src.soul_access.schema import (
    Phase,
    Provenance,
    RequestLocalHolder,
    SoulSnapshot,
    SnapshotRejected,
    advance,
    snapshot_digest,
    validate_snapshot,
)

__all__ = [
    "LiveLoop",
    "BridgeResult",
    "LiveRefused",
    "attach_live_loop",
    "get_live_loop",
    "admit_no_tools",
    "build_live_handler",
]

#: app.state attribute holding the attached LiveLoop (additive; the launcher
#: sets it once from the already-built bus/llm/middleware instances).
_LIVE_ATTR = "_soul_access_live"


class LiveRefused(Exception):
    """Fail-closed refusal: release closed, loop unwired, or seal broken."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LiveLoop:
    """Injected :8000 live instances (never constructed in this module)."""

    bus: Any
    llm: Any
    middleware: Any


@dataclass(frozen=True)
class BridgeResult:
    """Admitted reply + accounting + travelled evidence."""

    text: str
    loads: int
    generation_attempts: int
    provenance: Provenance


def attach_live_loop(app, *, bus, llm, middleware) -> LiveLoop:
    """Attach the launcher's existing instances to ``app.state`` (additive).

    Stores (never builds): a None instance raises LiveRefused instead of
    being replaced by a fresh construction.
    """
    for name, inst in (("bus", bus), ("llm", llm), ("middleware", middleware)):
        if inst is None:
            raise LiveRefused(500, "internal", "soul-access live loop missing: %s" % name)
    loop = LiveLoop(bus=bus, llm=llm, middleware=middleware)
    app.state._soul_access_live = loop
    return loop


def get_live_loop(app):
    """Return the attached loop, or None when the launcher attached none."""
    return getattr(getattr(app, "state", None), "_soul_access_live", None)


def _walk_no_tools() -> tuple:
    phase = Phase.HEAD_LOAD
    walked = [phase.value]
    for nxt in (Phase.GEN1, Phase.ADMIT):
        phase = advance(phase, nxt, has_tools=False)
        walked.append(phase.value)
    return tuple(walked)


def admit_no_tools(
    *,
    snapshot: SoulSnapshot,
    holder: RequestLocalHolder,
    provenance: Provenance,
    live: LiveLoop | None,
    generate: Callable[..., str] | None,
) -> BridgeResult:
    """Re-validate and admit one no-tools request through the live loop.

    Raises LiveRefused (gate faults) or SnapshotRejected (seal faults).
    Exactly one ``generate`` call is made; the counters pin that.
    """
    if live is None:
        raise LiveRefused(500, "internal", "soul-access live loop not wired")
    for name in ("bus", "llm", "middleware"):
        if getattr(live, name, None) is None:
            raise LiveRefused(500, "internal", "soul-access live loop missing: %s" % name)
    if not isinstance(live, LiveLoop):
        raise LiveRefused(500, "internal", "soul-access live loop mistyped")
    if not isinstance(holder, RequestLocalHolder):
        raise SnapshotRejected("holder mistyped")
    if not holder.capability:
        raise SnapshotRejected("holder capability required")
    validate_snapshot(snapshot)
    if not isinstance(provenance, Provenance):
        raise SnapshotRejected("provenance mistyped")
    digest = snapshot_digest(snapshot)
    if provenance.snapshot_digest != digest:
        raise SnapshotRejected("provenance digest seal broken")
    walked = _walk_no_tools()
    if generate is None:
        raise LiveRefused(500, "internal", "soul-access generate not wired")
    text = generate(snapshot, holder, live)
    if not isinstance(text, str):
        raise LiveRefused(500, "internal", "soul-access generate mistyped")
    return BridgeResult(
        text=text,
        loads=1,
        generation_attempts=1,
        provenance=Provenance(
            created_utc=provenance.created_utc,
            snapshot_digest=digest,
            transitions=walked,
        ),
    )


def build_live_handler(*, live=None, generate=None, release_path=None):
    """Build the thin-entry forward handler (release-gated, fail-closed).

    ``release_path`` None means the release gate is not consulted by this
    handler (the caller gates elsewhere); a set path is hot-read per call
    and a closed gate raises LiveRefused(403). Missing loop / generate
    raises LiveRefused(500) at CALL time (build never raises), so wiring
    faults surface as refusals, not import/startup crashes.
    """

    def _handle(envelope: ForwardEnvelope, *, route=None, preset=None) -> str:
        if not isinstance(envelope, ForwardEnvelope):
            raise SnapshotRejected("not a ForwardEnvelope")
        if release_path is not None and not is_live_inbound_open(release_path):
            raise LiveRefused(403, "forbidden", "soul-access inbound closed")
        holder = build_holder(
            request_id=envelope.snapshot.identity.request_id,
            snapshot_digest_hex=envelope.provenance.snapshot_digest,
        )
        result = admit_no_tools(
            snapshot=envelope.snapshot,
            holder=holder,
            provenance=envelope.provenance,
            live=live,
            generate=generate,
        )
        return result.text

    return _handle
