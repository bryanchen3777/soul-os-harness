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
probe ticket) is wired per request via ``build_dialog_factory``: the
``primary`` alias reuses the launcher's existing backend (read-only, never
replaced); every other alias resolves through the injected
``resolve_backend`` (settings from presets) with no fallback to primary.
Each request makes exactly one ``complete(max_retries=0)`` attempt from a
worker thread; same-loop misuse refuses instead of deadlocking.

Purity: stdlib + sibling ``api`` / ``bridge`` / ``release`` only, plus
duck-typed ``session_store`` / ``dialog`` callables. No provider/backend
imports, no socket/httpx at module import (httpx is imported lazily INSIDE
the default thin sender, and ``OpenAIBackend`` lazily INSIDE the formal
preset builder, so importing this module never touches the network
stack). The only :8000 literal in the repo lives in ``_THIN_TARGET_BASE``
below: ``src/`` is outside the tests/** address-guard scan scope, while the
test file must reference it via this constant (never as a literal).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from src.soul_access import api as _api
from src.soul_access import bridge as _bridge
from src.soul_access import release as _release
from src.soul_access import schema as _schema
from src.soul_access.schema import SnapshotRejected

__all__ = [
    "WIRING_VERSION",
    "RELEASE_FILE",
    "INTERNAL_LIVE_PATH",
    "SHARED_USER_ID",
    "SA_LIVE_CHANNEL",
    "FORMAL_BACKEND_ALIASES",
    "build_preset_backends",
    "build_formal_resolve_backend",
    "session_key",
    "session_path",
    "same_store_root",
    "append_sa_turn",
    "new_shared_session_store",
    "build_generate",
    "build_dialog_factory",
    "build_gated_sender",
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


# -- request-local current-turn binding (mount side sets, generate merges) ----
#
# The bridge is frozen, so the mount cannot pass the HTTP body turn through
# its signature. Instead the mount parks ``envelope.messages`` here for the
# duration of the synchronous handler call (same thread), and ``generate``
# merges any parked turn into the dialog input. Direct handler calls (no
# mount) see no parked turn, so their behaviour is unchanged.

_TURN = threading.local()


def _parked_turn_messages():
    """Return the mount-parked current-turn messages, or None."""
    parked = getattr(_TURN, "messages", None)
    if not isinstance(parked, tuple) or not parked:
        return None
    clean = []
    for item in parked:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and item[0]
            and isinstance(item[1], str)
            and item[1]
        ):
            clean.append(item)
    return tuple(clean) if clean else None


def _merge_turn_messages(base: tuple, parked) -> tuple:
    """Merge the parked current turn into the dialog input (no loss)."""
    if not parked:
        return base
    merged = tuple(base) if isinstance(base, tuple) else ()
    for item in parked:
        if item not in merged:
            merged = merged + (item,)
    return merged


def _decode_forward_text(text):
    """Decode a transport-quoted reply back to its plain text form.

    The :8000 live mount returns plain text; when it crosses the HTTP
    boundary as JSON it arrives quoted (``'"reply"'``). A quoted payload
    that JSON-decodes to a non-empty str decodes to it; anything else is
    returned untouched (never raises, never invents text).
    """
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if len(stripped) < 2 or not stripped.startswith('"'):
        return text
    try:
        decoded = json.loads(stripped)
    except (ValueError, TypeError):
        return text
    if isinstance(decoded, str) and decoded:
        return decoded
    return text


# -- per-request dialog factory (real bottom-I/O seam, offline ticket) ------


def _default_submit(loop, coro):
    """Submit one coroutine to the launcher loop from a worker thread.

    Same-loop misuse (called ON the launcher loop thread) refuses
    immediately instead of deadlocking; nested execution is never used.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is loop:
        raise _bridge.LiveRefused(
            500, "internal", "soul-access dialog must not run on the live loop"
        )
    return asyncio.run_coroutine_threadsafe(coro, loop)


def build_dialog_factory(*, llm, loop=None, resolve_backend=None, submit=None, evidence_sink=None):
    """Build the per-request ``dialog_factory(snapshot) -> dialog`` seam.

    ``llm`` is the launcher instance (read-only; its ``backend`` serves the
    ``primary`` alias and is never replaced). ``loop`` is the launcher's
    already-running event loop (captured, never created here).
    ``resolve_backend`` maps a non-primary backend alias to its request-local
    backend instance (settings come from presets); ``None`` (or a missing /
    faulting mapping) refuses at call time -- never falls back to primary.
    ``submit`` defaults to the worker-thread submitter above; tests inject a
    controllable ``submit(loop, coro) -> future`` so no loop is ever created.

    ``evidence_sink`` (SA-EVIDENCE-CHANNEL-1, optional, default ``None``) is
    the caller-owned sandbox sink (``src/soul_access/evidence.py``). When
    configured it is passed explicitly on *every* request's
    ``backend.complete(..., evidence_sink=...)`` call -- never via any shared
    "last result" global/module cache. When ``None`` (default) the
    ``evidence_sink`` argument is not passed at all, so legacy backends with
    a fixed ``complete`` signature keep working and old behavior is
    bit-identical.

    Each ``dialog`` call makes exactly one ``complete(max_retries=0)``
    attempt: empty / mistyped / faulting replies refuse (500), never resend.
    """
    if llm is None:
        raise _bridge.LiveRefused(
            500, "internal", "soul-access live loop missing: llm"
        )
    send = submit if callable(submit) else _default_submit

    def _params_of(snapshot) -> tuple:
        temperature = 0.5
        max_tokens = 512
        try:
            pairs = dict(snapshot.params)
        except (TypeError, ValueError):
            pairs = {}
        try:
            temperature = float(pairs.get("temperature", temperature))
        except (TypeError, ValueError):
            temperature = 0.5
        try:
            max_tokens = int(pairs.get("max_tokens", max_tokens))
        except (TypeError, ValueError):
            max_tokens = 512
        if max_tokens <= 0:
            max_tokens = 512
        return (temperature, max_tokens)

    def factory(snapshot):
        _schema.validate_snapshot(snapshot)
        alias = snapshot.backend_alias
        model = snapshot.model
        temperature, max_tokens = _params_of(snapshot)
        if alias == "primary":
            backend = getattr(llm, "backend", None)
            if backend is None:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access primary backend not wired"
                )
        else:
            if resolve_backend is None:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access backend alias not mapped"
                )
            try:
                backend = resolve_backend(alias)
            except (_bridge.LiveRefused, SnapshotRejected):
                raise
            except Exception:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access backend alias not mapped"
                )
            if backend is None:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access backend alias not mapped"
                )

        def dialog(messages, *, agent_id=None, request_id=None) -> str:
            if (
                not isinstance(messages, tuple)
                or not messages
                or any(
                    not isinstance(item, tuple)
                    or len(item) != 2
                    or not isinstance(item[0], str)
                    or not item[0]
                    or not isinstance(item[1], str)
                    or not item[1]
                    for item in messages
                )
            ):
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog messages mistyped"
                )
            wire_messages = [
                {"role": role, "content": content} for role, content in messages
            ]
            # Same-loop misuse refuses BEFORE any backend call (zero attempts,
            # no un-awaited coroutine left behind). _default_submit keeps the
            # same guard as a second layer for the real submit path.
            try:
                _running = asyncio.get_running_loop()
            except RuntimeError:
                _running = None
            if _running is not None and _running is loop:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog must not run on the live loop"
                )
            try:
                # SA-EVIDENCE-CHANNEL-1: evidence_sink 只在已配置時才逐請求顯式
                # 傳遞; 未配置時完全不帶該參數 (舊後端簽名相容, 舊行為逐位元一致)。
                _complete_kwargs = {
                    "messages": wire_messages,
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "max_retries": 0,
                }
                if evidence_sink is not None:
                    _complete_kwargs["evidence_sink"] = evidence_sink
                coro = backend.complete(**_complete_kwargs)
            except (_bridge.LiveRefused, SnapshotRejected):
                raise
            except Exception as exc:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog failed: %s" % type(exc).__name__
                ) from exc
            try:
                future = send(loop, coro)
            except (_bridge.LiveRefused, SnapshotRejected):
                raise
            except Exception as exc:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog failed: %s" % type(exc).__name__
                ) from exc
            try:
                text = future.result()
            except (_bridge.LiveRefused, SnapshotRejected):
                raise
            except Exception as exc:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog failed: %s" % type(exc).__name__
                ) from exc
            if not isinstance(text, str) or not text:
                raise _bridge.LiveRefused(
                    500, "internal", "soul-access dialog mistyped"
                )
            return text

        dialog.sa_dialog_backend_alias = alias
        dialog.sa_dialog_model = model
        return dialog

    factory.sa_dialog_factory = True
    return factory


# -- formal closed-door assembly (SA-THAW-DEPLOY-1, additive) -----------------
#
# The production mount (``scripts/run_server.py``) keeps
# ``resolve_backend=None``, so the gate stays closed until Owner approves a
# dialog ticket. These builders assemble the SAME production classes
# (``OpenAIBackend`` + ``build_dialog_factory``) from formal presets in
# sandbox (tmp release / session files, non-routable ``.invalid`` base URLs,
# counting stub at the bottom transport) to prove the formal wiring resolves
# all four frozen aliases before any unfreeze. The provider import is
# function-local (same lazy idiom as the thin sender's httpx import), so
# importing this module still never touches the network stack.

#: The four frozen backend aliases (A batch = rem x these four; B batch adds
#: {mai, ruka, akane, mahiru} x the same four; union exactly 20 routes).
FORMAL_BACKEND_ALIASES = (
    "primary",
    "minimax-m3.1-flash",
    "glm-5.3-flash",
    "mimo-v2.6-flash",
)

#: The single provider the formal presets pin (uniformly, offline).
_FORMAL_PROVIDER = "openai"


def build_preset_backends(presets):
    """Build one production backend per frozen alias from formal presets.

    ``presets`` maps alias -> preset object (``provider`` / ``api_key`` /
    ``base_url`` attributes, as produced by the preset loader). The alias set
    must be EXACTLY the four frozen aliases and every preset's provider must
    be ``openai``; anything else (unknown / missing / extra alias, non-openai
    provider, empty key / base URL, mistyped mapping) refuses at BUILD time
    with 500 -- never a partial set, never a silent substitution.
    """
    if not isinstance(presets, Mapping):
        raise _bridge.LiveRefused(
            500, "internal", "soul-access formal presets mistyped"
        )
    if set(presets.keys()) != set(FORMAL_BACKEND_ALIASES):
        raise _bridge.LiveRefused(
            500, "internal", "soul-access formal alias set mismatch"
        )
    from src.llm.proxy import OpenAIBackend  # lazy: module import stays net-free

    backends = {}
    for alias in FORMAL_BACKEND_ALIASES:
        preset = presets[alias]
        if getattr(preset, "provider", None) != _FORMAL_PROVIDER:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access formal provider not openai"
            )
        api_key = getattr(preset, "api_key", None)
        base_url = getattr(preset, "base_url", None)
        if not isinstance(api_key, str) or not api_key:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access formal key missing"
            )
        if not isinstance(base_url, str) or not base_url:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access formal base_url missing"
            )
        backends[alias] = OpenAIBackend(api_key=api_key, base_url=base_url)
    return backends


def build_formal_resolve_backend(backends):
    """Resolve non-primary aliases against the formal backend set.

    The backend set must hold EXACTLY the four frozen aliases (else 500 at
    BUILD time). Resolution returns the mapped backend or ``None`` for an
    unknown alias, so ``build_dialog_factory`` refuses (500) instead of
    falling back to primary. ``primary`` itself is never resolved here: the
    factory serves it from the launcher instance (read-only, original object).
    """
    if not isinstance(backends, Mapping):
        raise _bridge.LiveRefused(
            500, "internal", "soul-access formal backends mistyped"
        )
    if set(backends.keys()) != set(FORMAL_BACKEND_ALIASES):
        raise _bridge.LiveRefused(
            500, "internal", "soul-access formal alias set mismatch"
        )
    snapshot = dict(backends)

    def _resolve(alias):
        return snapshot.get(alias)

    return _resolve


# -- :8000 generate adapter (formal mount + identity + trace) ----------------


def build_generate(
    *,
    bus,
    llm,
    middleware,
    session_store=None,
    dialog: Callable[..., str] | None = None,
    dialog_factory: Callable[..., Callable[..., str]] | None = None,
):
    """Build the ``(snapshot, holder, live) -> str`` generate adapter.

    ``bus`` / ``llm`` / ``middleware`` must be the launcher instances (a None
    one refuses at BUILD time instead of being replaced). ``dialog`` is the
    synchronous bottom-I/O seam
    ``dialog(messages, agent_id=..., request_id=...) -> str``; ``None`` builds
    fine but refuses every call (500, never falls back to any other text
    source). ``dialog_factory`` is the per-request alternative
    (``dialog_factory(snapshot) -> dialog``); passing both ``dialog`` and
    ``dialog_factory`` refuses at BUILD time (mutually exclusive). When only
    the factory is given, each admitted request resolves its own dialog
    (bound to that request's model / params); a factory fault refuses the
    call (500) with zero dialog attempts. ``session_store=None`` likewise
    refuses at call time.

    Each call records ``generate.trace`` (one record) and parks the holder in
    ``generate.pending[request_id]`` while in flight (popped on the way out,
    success or refusal). A mount-parked current turn (same thread) is merged
    into the dialog input so a warm session never drops the live user input;
    direct handler calls (no mount) see no parked turn.
    """
    for name, inst in (("bus", bus), ("llm", llm), ("middleware", middleware)):
        if inst is None:
            raise _bridge.LiveRefused(
                500, "internal", "soul-access live loop missing: %s" % name
            )
    if dialog is not None and dialog_factory is not None:
        raise _bridge.LiveRefused(
            500, "internal", "soul-access generate dialog seam ambiguous"
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
        if dialog is None and dialog_factory is None:
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
            messages = _merge_turn_messages(messages, _parked_turn_messages())
            if dialog_factory is not None:
                try:
                    call_dialog = dialog_factory(snapshot)
                except (_bridge.LiveRefused, SnapshotRejected):
                    raise
                except Exception as exc:
                    raise _bridge.LiveRefused(
                        500, "internal", "soul-access dialog failed: %s" % type(exc).__name__
                    ) from exc
                if not callable(call_dialog):
                    raise _bridge.LiveRefused(
                        500, "internal", "soul-access dialog mistyped"
                    )
            else:
                call_dialog = dialog
            text = call_dialog(messages, agent_id=agent_id, request_id=request_id)
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


# -- shared pre-dispatch gate (SA-ENTRY-GATE-1, additive) --------------------
#
# The thin forwarder (below) and any direct (diagnostic) sender share this one
# gate: the authoritative gate source is re-read from disk before EVERY
# dispatch attempt, including retries. There is no startup cache to go stale.
# Closed / missing / unreadable / bad-JSON / non-dict / wrong-status all
# refuse 403 with zero sender calls. A gate that closes mid-retry refuses the
# next attempt; already-dispatched attempts are NOT retracted (the gate only
# guards new dispatches). This ticket never opens the production gate: both
# the positive and the negative drills use a sandbox gate source.


def build_gated_sender(
    *,
    release_path=None,
    sender: Callable[[str, dict], str] | None = None,
    max_attempts: int = 1,
):
    """Build the shared gated sender ``(url, body) -> str``.

    ``release_path`` is the authoritative gate source (sandbox file in every
    drill; the production file only in production). ``None`` (no gate source
    configured) refuses fail-closed. ``sender`` is the bottom transport
    (real POST by default, counting fakes in tests); it is ONLY called while
    the gate reads open. ``max_attempts`` bounds transport retries (>= 1);
    the gate is re-read before each attempt, so a close between attempts
    refuses the next one with 403 instead of resending.
    """
    try:
        attempts = int(max_attempts)
    except (TypeError, ValueError):
        attempts = 1
    if attempts < 1:
        attempts = 1
    send = sender if callable(sender) else _default_sender

    def _send(url: str, body: dict) -> str:
        last_kind = None
        for _ in range(attempts):
            if release_path is None or not _release.is_live_inbound_open(
                release_path
            ):
                raise _bridge.LiveRefused(
                    403, "forbidden", "soul-access inbound closed"
                )
            try:
                return send(url, body)
            except (_bridge.LiveRefused, SnapshotRejected):
                raise
            except Exception as exc:
                last_kind = type(exc).__name__
                continue
        raise _bridge.LiveRefused(
            500, "internal", "soul-access forward failed: %s" % (last_kind or "unknown")
        )

    _send.sa_gated_sender_attempts = attempts
    return _send


# -- :18765 thin forwarder (cross-process, zero live instances) ---------------


def _default_sender(url: str, body: dict) -> str:
    """POST the re-serialized envelope to the fixed internal endpoint."""
    import httpx  # lazy: importing this module never touches the net stack

    response = httpx.post(url, json=body, timeout=30.0)
    response.raise_for_status()
    return _decode_forward_text(response.text)


def build_thin_forwarder(
    *,
    release_path=None,
    target_base: str = _THIN_TARGET_BASE,
    sender: Callable[[str, dict], str] | None = None,
):
    """Build the thin-side forwarder ``(envelope, route, preset) -> str``.

    Holds NO live instances (this process has none). The dispatch shares the
    SA-ENTRY-GATE-1 pre-dispatch gate (``build_gated_sender``): closed /
    missing / bad release refuses 403 before any byte is sent, re-read per
    attempt (single attempt on this path). The sender (real POST by default,
    fake in tests) returns the reply text, which must be a non-empty str or
    the forward refuses 500. Sender faults become 500 (never leak).
    """

    gated = build_gated_sender(release_path=release_path, sender=sender)

    def _forward(envelope, *, route=None, preset=None) -> str:
        if not isinstance(envelope, _api.ForwardEnvelope):
            raise SnapshotRejected("not a ForwardEnvelope")
        body = _api.envelope_to_body(envelope)
        url = target_base.rstrip("/") + _api.FORWARD_ENDPOINT
        try:
            text = gated(url, body)
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
        return _decode_forward_text(text)

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
        _TURN.messages = envelope.messages
        try:
            return handler(envelope)
        finally:
            _TURN.messages = ()

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
