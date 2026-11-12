"""SA-LIVE-WIRING-1 production live wiring (thin forwarder + :8000 mount + generate adapter).

Decision-complete scope (SA-LIVE-WIRING-1, mastermind Sol approved): this
module plus minimal additive edits to ``scripts/run_server.py`` (lifespan
mount on the SAME bus/llm/middleware instances) and
``scripts/serve_external_gateway.py`` (thin side becomes a cross-process
forwarder holding NO live instances) plus
``tests/soul_access/test_t8_live_wiring.py`` are new/touched. Frozen files
(``bridge.py`` / ``api.py`` / ``release.py`` / SA-0 schema /台帳/判準) are
untouched: T6 semantics (403 + zero generate on closed gate) hold by
construction because the refusal path is still ``bridge.build_live_handler``.

Two processes, two roles (never mixed):

  - :18765 thin side: ``build_thin_forwarder``. It holds NO bus / llm /
    middleware (this process has none). It re-serializes the sealed envelope
    (``api.envelope_to_body``) and POSTs it to the FIXED internal endpoint on
    :8000. Release closed/missing/bad => 403 before any byte is sent.
  - :8000 live side: ``mount_live_route`` + ``build_generate``. The mount
    reuses ``bridge.build_live_handler`` (seal + release + admit stay the
    single refusal path) after re-verifying envelope seal / fixed route /
    live-loop identity / release itself. ``build_generate`` is the
    ``(snapshot, holder, live) -> str`` adapter: it asserts the live loop IS
    the launcher instances (``is``), reads the SHARED session (same store
    root, same ``(agent_id, user_bryan)`` key as telegram/voice), calls the
    injected synchronous ``dialog`` seam for text, appends the reply as an
    ``sa-live`` turn, and records ``.trace`` / ``.pending[request_id]``.

The production dialog (real LLM call, non-zero cost, needs an Owner-approved
probe ticket) is NOT wired here: ``dialog=None`` builds fine but refuses at
call time (500, never falls back to anything). Tests prove the formal mount
+ handler + adapter + identity + trace with a fake dialog.

Purity: stdlib + sibling ``api`` / ``bridge`` / ``release`` only, plus
duck-typed ``session_store`` / ``dialog`` callables. No provider/backend
imports, no socket/httpx at module import (httpx is imported lazily INSIDE
the default thin sender, so importing this module never touches the network
stack). The only :8000 literal in the repo lives in ``_THIN_TARGET_BASE``
below: ``src/`` is outside the tests/** address-guard scan scope, while the
test file must reference it via this constant (never as a literal).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from src.soul_access import api as _api
from src.soul_access import bridge as _bridge
from src.soul_access import release as _release
from src.soul_access.schema import SnapshotRejected

__all__ = [
    "WIRING_VERSION",
    "RELEASE_FILE",
    "INTERNAL_LIVE_PATH",
    "SHARED_USER_ID",
    "SA_LIVE_CHANNEL",
    "session_key",
    "session_path",
    "same_store_root",
    "append_sa_turn",
    "new_shared_session_store",
    "build_generate",
    "build_thin_forwarder",
    "mount_live_route",
]

#: Live-wiring version (bumped only by a decision-complete ticket).
WIRING_VERSION = "salw/v1"

#: Pinned production release file (never in git; this ticket creates no real
#: file, so the gate reads closed until Owner approves the file).
RELEASE_FILE = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/release/sa.json")

#: The fixed internal endpoint (single source: the thin-entry constant).
#: Re-verified at mount time (mount refuses to register anywhere else).
INTERNAL_LIVE_PATH = _api.FORWARD_ENDPOINT

#: Shared-session user: telegram / voice / sa-live all meet on this key.
SHARED_USER_ID = "user_bryan"

#: Channel label for turns written by the sa-live loop (alongside the
#: pre-existing "telegram" / "voice" labels in the SAME session file).
SA_LIVE_CHANNEL = "sa-live"

assert INTERNAL_LIVE_PATH == "/internal/soul-access/live"

#: Thin-side default target. THE ONLY :8000 literal in the repo: ``src/`` is
#: outside the tests/** pinned-address scan scope. Tests reference
#: ``wiring`` attributes / ``_api.FORWARD_ENDPOINT`` instead of spelling it.
_THIN_TARGET_BASE = "http://127.0.0.1:8000"


# -- shared session sentinels (same root / same key / 3-way continuation) ---


def session_key(agent_id: str) -> tuple:
    """The shared-session key for one soul: ``(agent_id, user_bryan)``."""
    if not isinstance(agent_id, str) or not agent_id:
        raise SnapshotRejected("session agent_id required")
    return (agent_id, SHARED_USER_ID)


def session_path(session_store, agent_id: str) -> Path:
    """The session file a store uses for one soul (same file, every entry)."""
    key = session_key(agent_id)
    return Path(session_store.data_dir) / ("%s_%s.json" % key)


def same_store_root(first, second) -> bool:
    """True only when two stores share the same storage root (no fork)."""
    try:
        return Path(first.data_dir).resolve() == Path(second.data_dir).resolve()
    except Exception:
        return False


def append_sa_turn(session_store, agent_id: str, role: str, content: str) -> None:
    """Append one sa-live turn to the shared ``(agent_id, user_bryan)`` file."""
    agent, user = session_key(agent_id)
    session_store.append_turn(agent, user, role, content, SA_LIVE_CHANNEL)


def new_shared_session_store(data_dir=None):
    """Build the shared short-term session store (lazy import, stdlib-only).

    ``data_dir=None`` keeps production behaviour (env-first, else repo data);
    tests pass an explicit tmp dir. The import is function-local so importing
    this module never pulls the clients tree.
    """
    from clients.voice_companion.session_store import SessionStore

    if data_dir is None:
        return SessionStore()
    return SessionStore(data_dir=data_dir)


# -- :8000 generate adapter (formal mount + identity + trace) ----------------


def build_generate(
    *,
    bus,
    llm,
    middleware,
    session_store=None,
    dialog: Callable[..., str] | None = None,
):
    """Build the ``(snapshot, holder, live) -> str`` generate adapter.

    ``bus`` / ``llm`` / ``middleware`` must be the launcher instances (a None
    one refuses at BUILD time instead of being replaced). ``dialog`` is the
    synchronous bottom-I/O seam
    ``dialog(messages, agent_id=..., request_id=...) -> str``; ``None`` builds
    fine but refuses every call (500, never falls back to any other text
    source). ``session_store=None`` likewise refuses at call time.

    Each call records ``generate.trace`` (one record) and parks the holder in
    ``generate.pending[request_id]`` while in flight (popped on the way out,
    success or refusal).
    """
    for name, inst in (("bus", bus), ("llm", llm), ("middleware", middleware)):
        if inst is None:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access live loop missing: %s" % name
            )
    trace: list = []
    pending: dict = {}

    def generate(snapshot, holder, live) -> str:
        if not isinstance(live, _bridge.LiveLoop):
            raise _bridge.LiveRefused(
                500, "internal", "soul-access live loop mistyped"
            )
        for name, inst in (("bus", bus), ("llm", llm), ("middleware", middleware)):
            if getattr(live, name, None) is not inst:
                raise _bridge.LiveRefused(
                    500,
                    "internal",
                    "soul-access live identity mismatch: %s" % name,
                )
        if dialog is None:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access generate not wired"
            )
        if session_store is None:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access session store not wired"
            )
        agent_id = snapshot.dedup.agent_id
        request_id = snapshot.identity.request_id
        pending[request_id] = holder
        try:
            context = session_store.get_active_context(agent_id, SHARED_USER_ID)
            raw_history = (
                context.get("history", []) if isinstance(context, dict) else []
            )
            messages = tuple(
                (item["role"], item["content"])
                for item in raw_history
                if isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("content"), str)
                and item["role"]
                and item["content"]
            )
            if not messages:
                # Cold start: the SA-0 sanitized history covers the turn.
                messages = tuple(
                    ("user", item)
                    for item in snapshot.clean_history
                    if isinstance(item, str) and item
                )
            text = dialog(messages, agent_id=agent_id, request_id=request_id)
            if not isinstance(text, str) or not text:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog mistyped"
                )
            append_sa_turn(session_store, agent_id, "assistant", text)
            trace.append(
                {
                    "request_id": request_id,
                    "agent_id": agent_id,
                    "history_turns": len(messages),
                    "reply_chars": len(text),
                }
            )
            return text
        finally:
            pending.pop(request_id, None)

    generate.sa_live_refs = (bus, llm, middleware)  # identity evidence (is)
    generate.trace = trace  # call trajectory (one record per admitted call)
    generate.pending = pending  # in-flight holder by request_id ({} at rest)
    return generate


# -- :18765 thin forwarder (cross-process, zero live instances) ---------------


def _default_sender(url: str, body: dict) -> str:
    """POST the re-serialized envelope to the fixed internal endpoint."""
    import httpx  # lazy: importing this module never touches the net stack

    response = httpx.post(url, json=body, timeout=30.0)
    response.raise_for_status()
    return response.text


def build_thin_forwarder(
    *,
    release_path=None,
    target_base: str = _THIN_TARGET_BASE,
    sender: Callable[[str, dict], str] | None = None,
):
    """Build the thin-side forwarder ``(envelope, route, preset) -> str``.

    Holds NO live instances (this process has none). Closed/missing/bad
    release refuses 403 before any byte is sent; the sender (real POST by
    default, fake in tests) returns the reply text, which must be a non-empty
    str or the forward refuses 500. Sender faults become 500 (never leak).
    """

    def _forward(envelope, *, route=None, preset=None) -> str:
        if not isinstance(envelope, _api.ForwardEnvelope):
            raise SnapshotRejected("not a ForwardEnvelope")
        if release_path is not None and not _release.is_live_inbound_open(
            release_path
        ):
            raise _bridge.LiveRefused(403, "forbidden", "soul-access inbound closed")
        body = _api.envelope_to_body(envelope)
        url = target_base.rstrip("/") + _api.FORWARD_ENDPOINT
        send = sender if callable(sender) else _default_sender
        try:
            text = send(url, body)
        except (_bridge.LiveRefused, SnapshotRejected):
            raise
        except Exception as exc:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access forward failed: %s" % type(exc).__name__
            ) from exc
        if not isinstance(text, str) or not text:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access forward mistyped"
            )
        return text

    _forward.sa_thin_target = target_base.rstrip("/") + _api.FORWARD_ENDPOINT
    return _forward


# -- :8000 internal route mount (formal mount over the shared handler) -------


def mount_live_route(app, *, live=None, generate=None, release_path=None):
    """Mount the fixed internal live route on the :8000 app (additive).

    Reuses ``bridge.build_live_handler`` (the single refusal path: seal +
    release + admit). Before delegating, the route re-verifies: live-loop
    identity (``app.state`` loop IS the mounted one), the release hot-read,
    and the envelope seal (``envelope_from_body`` digest check). The route is
    registered ONLY at ``INTERNAL_LIVE_PATH`` (never parametrized); apps
    without ``add_api_route`` get the route handler back without registration.
    """
    if INTERNAL_LIVE_PATH != _api.FORWARD_ENDPOINT:
        raise _bridge.LiveRefused(
            500, "internal", "soul-access internal endpoint drifted"
        )
    handler = _bridge.build_live_handler(
        live=live, generate=generate, release_path=release_path
    )

    def _sa_live_route(body):
        current = _bridge.get_live_loop(app)
        if current is None or current is not live:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access live loop identity mismatch"
            )
        if release_path is not None and not _release.is_live_inbound_open(
            release_path
        ):
            raise _bridge.LiveRefused(403, "forbidden", "soul-access inbound closed")
        envelope = _api.envelope_from_body(body)
        return handler(envelope)

    _sa_live_route.sa_live_path = INTERNAL_LIVE_PATH
    register = getattr(app, "add_api_route", None)
    if callable(register):
        register(
            INTERNAL_LIVE_PATH,
            _sa_live_route,
            methods=["POST"],
            name="soul_access_live",
        )
    return _sa_live_route


def get_live_loop(app) -> Any:
    """Read back the launcher-attached loop (same object the mount checks)."""
    return _bridge.get_live_loop(app)
