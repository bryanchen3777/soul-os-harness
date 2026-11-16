#!/usr/bin/env python3
"""SA-THIN-ENTRY-1 thin independent entry: loopback-only, closed-only.

Decision-complete scope (SA-THIN-ENTRY-1, mastermind Sol approved): this
file is new. The existing gateway assembly (``src/external_gateway/app.py``),
its carrier (``src/external_gateway/backends.py``), the config loader
(``configs/sa/registry.json`` + ``configs/sa/presets.json``), the release
file (``release/sa.json``), ``:8000`` and every Frozen contract are
untouched. Only this entry, its tests and the runbook are new/touched.

Two processes, two roles (never mixed):
  - ``:18765`` thin side (THIS file): loopback-only, closed-only. It reuses
    the production auth / routing / preset-loader path
    (``create_external_app`` + ``RegistryProvider`` +
    ``file_preset_loader`` + ``PresetRoutingBackend``) and then refuses
    every soul-access inbound at the instance door -- 403, before any
    upstream byte is sent and before any memory seam is touched.
  - ``:8000`` live side: unchanged (see ``scripts/run_server.py``).

Instance door (SA-THIN-ENTRY-1 seam):
  - ``_THIN_OPEN`` is process-local, ``False``, with no opener in this
    ticket. The door acts on THIS instance only: it never reads, writes,
    or flips the shared release file, and flipping that file cannot open
    this entry. A fresh process re-executes the same constant, so a
    restart stays closed by construction.
  - Closed refusal uses the frozen bridge copy
    (``LiveRefused(403, "forbidden", ...)``), so the outward app maps it
    to 403 exactly like a closed release gate -- zero forward, zero
    generate, zero memory write.

Fail-closed startup (exit 2, one fixed copy, nothing else printed):
  - registry / presets file missing, unreadable, or not JSON;
  - registry / presets bytes differ from the pinned e351541 content
    (re-read and SHA-256-compared on every start; drift refuses);
  - registry text carries the template marker ``do-not-deploy``;
  - registry fails ``RegistryProvider`` schema validation;
  - ``127.0.0.1:18765`` is already occupied (pre-flight bind probe).

The entry exposes NO ``--host`` / ``--port`` flags: the loopback bind is
structural, not configurable. Importing this module has zero side
effects: no file reads, no env reads, no port attach, no serving. Every
effect lives in ``main()`` behind ``__main__`` (same isolation idiom as
``scripts/run_server.py`` and ``scripts/serve_external_gateway.py``).

Start / stop (existing approved foreground pattern, no service install,
no background wrapper, no system service):
  foreground: .venv\\Scripts\\python.exe -B scripts\\serve_sa_thin.py
  stop:       Ctrl-C in that foreground window (same as the S8 gateway).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import http.client
import json
import logging
import os
import queue
import secrets
import socket
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn
from fastapi import Request

from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import PresetRoutingBackend
from src.external_gateway.openai_shim import ChatMessage
from src.interfaces.external_auth import RegistryProvider
from src.soul_access import bridge as _sa_bridge
from src.soul_access import release as _sa_release
from src.soul_access import wiring as _sa_wiring

#: Loopback-only bind. No flag can change it (there is no --host/--port).
THIN_HOST = "127.0.0.1"

#: Module logger (overlay applied / persona unreadable lines only; never a
#: body, a token, or a key).
_LOGGER = logging.getLogger(__name__)

#: Thin port. Must stay the frozen outward port (asserted in tests).
THIN_PORT = 18765

assert THIN_PORT == EXTERNAL_PORT

#: Pinned e351541 content hashes (``configs/sa/registry.json`` +
#: ``configs/sa/presets.json``). Re-read and compared on every start;
#: any drift refuses startup. Lowercase hex, ``hashlib.sha256().hexdigest()``.
REGISTRY_SHA256 = "6d805d70a13874240cb1a469310cc6327542d6e0bc21a663cb6ed40ac14ac203"
PRESETS_SHA256 = "35ab58bed7c72f60d7514deb8cd7e933be5b31c40c2598e3491919c28815e30a"

#: Production config defaults (same files the gateway carrier loads).
REGISTRY_DEFAULT = _REPO_ROOT / "configs" / "sa" / "registry.json"
PRESETS_DEFAULT = _REPO_ROOT / "configs" / "sa" / "presets.json"

#: The only startup refusal copy (fixed; carries no paths, no secrets).
_STARTUP_REFUSAL = "sa thin entry: startup refused."

#: The fixed closed-door refusal (same copy the frozen bridge uses).
_CLOSED_MESSAGE = "soul-access inbound closed"

#: Instance door: process-local, closed, no opener in this ticket. A fresh
#: process (restart) re-executes this constant, so restart stays closed.
_THIN_OPEN = False


#: SA-DOOR-MECH-1 note (additive): the controlled open path lives on
#: ``ThinControl`` (``close_door`` / ``install_registry`` / ``ready`` /
#: ``open_door``), instance-owned and default-closed; the module constant
#: above stays False for the legacy no-control handler path. ``--control``
#: sends one door command to the running entry and exits before any bind.


# -- SA-ENTRY-GATE-1 control plane (graceful shutdown, loopback-only) --------
#
# A loopback-only POST control endpoint lets the launcher stop THIS thin
# process without killing anything (no Stop-Process / taskkill / task_stop /
# os.kill anywhere in this file -- enforced by tests). Per start, a fresh
# random token is generated (``secrets``); the token lives ONLY in the
# control file (0600 best-effort, starting account) and in the verified
# request -- never in logs, never on stdout.
#
# Control file contents: run_id / pid / endpoint. The pid is inventory ONLY:
# no code path in this file (or its tests) uses it to terminate a process.
#
# Reply-first drain: after a verified shutdown request the endpoint replies
# 200 immediately, then refuses new work (draining), waits for in-flight work
# up to a finite timeout, closes the listener exactly once, signals the
# injected serving loop to exit (``should_exit``), releases
# resources, and returns normally, so the process ends and the loopback
# bind is released. A stuck drain still closes and releases
# but reports BLOCKED (it never hangs, never kills). Repeated shutdowns are
# idempotent. The control path never consults the release gate, so a closed
# production gate can still shut the process down.
#
# Real :18765 bind/serve needs a separate authorization and is NOT exercised
# here: the drills use a fake listener / fake clock / counting sender only.

#: Control endpoint path (POST only, loopback-only, checked per request).
CONTROL_ENDPOINT = "/internal/sa-thin/control"

#: Source addresses the control endpoint accepts (loopback only).
CONTROL_LOOPBACKS = ("127.0.0.1", "::1")

#: Default control-file location (per-user temp dir; tests inject tmp_path).
CONTROL_FILE_DEFAULT = str(
    Path(tempfile.gettempdir()) / "sa-thin-18765.control.json"
)

#: Drain states (also the reconciliation vocabulary for run_id events).
STATE_SERVING = "serving"
STATE_DRAINING = "draining"
STATE_SHUT = "shut"
STATE_BLOCKED = "blocked"


class ThinControl:
    """Loopback shutdown controller (pure logic; no sockets, no signals)."""

    def __init__(
        self,
        *,
        run_id=None,
        token=None,
        control_path=None,
        clock=None,
        drain_timeout_s=5.0,
        listener=None,
        sleeper=None,
        server=None,
        release_path=None,
        registry_path=None,
        presets_path=None,
    ):
        self.run_id = run_id if isinstance(run_id, str) and run_id else uuid.uuid4().hex
        self._token = (
            token if isinstance(token, str) and token else secrets.token_urlsafe(32)
        )
        self.control_path = (
            str(control_path) if control_path is not None else CONTROL_FILE_DEFAULT
        )
        self._clock = clock if clock is not None else time.monotonic
        try:
            self._drain_timeout = float(drain_timeout_s)
        except (TypeError, ValueError):
            self._drain_timeout = 5.0
        if self._drain_timeout <= 0:
            self._drain_timeout = 5.0
        self._listener = listener
        self._sleeper = sleeper if callable(sleeper) else (lambda s: time.sleep(s))
        self._poller = None  # test seam: called once per drain iteration.
        # SA-THIN-SHUTDOWN-FIX: the serving ``uvicorn.Server`` instance.
        # ``main()`` injects it via ``attach_server()``; ``_shutdown()``
        # flips ``should_exit`` so the serving loop returns and the process
        # ends (previously only the listener was closed and the loop kept
        # the process -- and :18765 -- alive). ``None`` keeps the pure-logic
        # (fake listener / no serve) path working unchanged.
        self._server = server
        self.state = STATE_SERVING
        self.inflight = 0
        self.events = []
        self._seq = 0
        self._listener_closed = False
        # -- SA-DOOR-MECH-1 dual-door state (instance-owned, default closed) ----
        # The instance door lives on THIS controller object: a fresh process
        # builds a fresh closed controller, so a restart never restores open.
        # Control operations (below) and inbound admission (the thin closed
        # handler) share ``_door_lock``: exactly one of them mutates or
        # consults the doors at a time.
        self._door_lock = threading.Lock()
        self._instance_open = False
        self._installed = False
        self._ready = False
        self._installed_run = None
        self._verified = None
        self.release_path = (
            str(release_path) if release_path is not None else None
        )
        self.registry_path = (
            str(registry_path) if registry_path is not None else None
        )
        self.presets_path = (
            str(presets_path) if presets_path is not None else None
        )
        self._provider = None
        self._record("started")

    # -- events (run_id reconciliation, never carries the token) -------------

    def _record(self, event, detail=""):
        self._seq += 1
        self.events.append(
            {"seq": self._seq, "event": event, "run_id": self.run_id, "detail": detail}
        )

    # -- control file (token + run_id + pid; 0600 best-effort; never logged) --

    def write_control_file(self):
        payload = {
            "run_id": self.run_id,
            "pid": os.getpid(),
            "token": self._token,
            "endpoint": CONTROL_ENDPOINT,
        }
        path = Path(self.control_path)
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except (OSError, ValueError):
            pass
        self._record("control-file-written")
        return str(path)

    def read_token(self):
        """Return the token (only the launcher reads it back from the file)."""
        return self._token

    # -- SA-DOOR-MECH-1 dual-door control (instance door + shared release) ------
    #
    # Two doors, one admission: the instance door (this object, default
    # closed) and the shared release file (open only when it hot-reads
    # ``{"status": "open"}`` right now). Inbound is admitted only while BOTH
    # are open. Every method returns the existing control shape
    # ``(status, payload)`` with ``status`` / ``run_id`` / ``state`` keys.
    # ``close_door`` never reads the configs, so drift can never block a
    # re-close. ``install_registry`` / ``open_door`` re-read and re-verify
    # the pinned dual hashes from disk on every call and drive the service's
    # actual ``RegistryProvider.update()`` with those same verified bytes
    # (snapshot semantics are the provider's own, untouched here).

    def attach(self, provider=None, *, registry_path=None, presets_path=None,
               release_path=None):
        """Bind the service's actual config/provider handles (no copies).

        ``provider`` must be the live ``RegistryProvider`` the app serves
        from; ``install_registry`` calls its ``update()`` with the freshly
        verified bytes. Paths override the constructor values when given.
        """
        if provider is not None:
            self._provider = provider
        if registry_path is not None:
            self.registry_path = str(registry_path)
        if presets_path is not None:
            self.presets_path = str(presets_path)
        if release_path is not None:
            self.release_path = str(release_path)
        self._record("attached")
        return self

    def attach_server(self, server):
        """Inject the serving ``uvicorn.Server`` (SA-THIN-SHUTDOWN-FIX).

        ``main()`` calls this exactly once before serving. ``_shutdown()``
        then sets ``server.should_exit = True`` so the serving loop returns
        and the process ends normally. Accepts ``None`` (detach, restores
        the pure-logic path). Returns ``self`` for chaining.
        """
        self._server = server
        self._record("server-attached")
        return self

    def is_instance_open(self) -> bool:
        """Report the instance door (default closed, restart-closed)."""
        with self._door_lock:
            return self._instance_open

    def is_shared_open(self) -> bool:
        """Hot-read the shared release file (fail-closed on any fault)."""
        if self.release_path is None:
            return False
        return _sa_release.is_live_inbound_open(self.release_path)

    def _door_payload(self, status, extra=None):
        payload = {
            "status": status,
            "run_id": self.run_id,
            "state": self.state,
            "instance_open": self._instance_open,
            "shared_open": self.is_shared_open(),
            "installed": self._installed,
            "ready": self._ready,
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _atomic_write_json(path, obj) -> None:
        """Replace ``path`` with ``obj`` atomically (tmp + rename, same dir)."""
        target = Path(path)
        tmp = target.parent / (".tmp-%s-%s" % (uuid.uuid4().hex, target.name))
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        os.replace(tmp, target)

    def _read_verify(self):
        """Re-read configs and pin them to the dual content hashes.

        Returns ``(True, (reg_parsed, pre_parsed))`` or
        ``(False, reason)``. Any fault (missing / unreadable / unparsable /
        hash drift / template marker) refuses -- never a partial install.
        """
        if self.registry_path is None or self.presets_path is None:
            return (False, "config-paths-unset")
        try:
            reg_raw = Path(self.registry_path).read_bytes()
        except (OSError, ValueError):
            return (False, "registry-unreadable")
        if hashlib.sha256(reg_raw).hexdigest() != REGISTRY_SHA256:
            return (False, "registry-drift")
        try:
            reg_parsed = json.loads(reg_raw.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError):
            return (False, "registry-bad-json")
        try:
            if "do-not-deploy" in reg_raw.decode("utf-8"):
                return (False, "registry-template-marker")
        except (ValueError, UnicodeDecodeError):
            return (False, "registry-bad-json")
        try:
            pre_raw = Path(self.presets_path).read_bytes()
        except (OSError, ValueError):
            return (False, "presets-unreadable")
        if hashlib.sha256(pre_raw).hexdigest() != PRESETS_SHA256:
            return (False, "presets-drift")
        try:
            pre_parsed = json.loads(pre_raw.decode("utf-8"))
        except (ValueError, TypeError, UnicodeDecodeError):
            return (False, "presets-bad-json")
        return (True, (reg_parsed, pre_parsed))

    def _shared_closed(self) -> bool:
        return self._confirm_shared_closed()

    def _confirm_shared_closed(self) -> bool:
        """Strict shared readback: True only on an explicit closed read.

        A missing file, an unreadable file, bad JSON, a non-object payload,
        or any status other than ``"closed"`` all read as UNCONFIRMED
        (False). ``is_shared_open()`` (used for admission) stays fail-closed
        in the other direction; this helper is the only readback the
        re-close paths trust, so a read/write fault can never be reported
        as a confirmed close.
        """
        if self.release_path is None:
            return False
        try:
            raw = Path(self.release_path).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return False
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return False
        return isinstance(data, dict) and data.get("status") == "closed"

    def _write_shared_closed(self) -> bool:
        """Atomically write closed and strictly read it back (shared logic).

        Returns True only when the readback confirms ``closed``; any write
        or read fault returns False. ``close_door`` and the drift re-close
        in ``open_door`` share this helper.
        """
        if self.release_path is None:
            return False
        try:
            self._atomic_write_json(self.release_path, {"status": "closed"})
        except Exception:
            return False
        return self._confirm_shared_closed()

    def close_door(self):
        """One-shot dual re-close (idempotent; never blocked by drift).

        Closes the instance door FIRST, then atomically replaces the shared
        release with closed and reads it back to confirm; revokes the ready
        qualification. Configs are never read, so a drifted config cannot
        block a re-close. On a shared-write/confirm fault the instance door
        is still closed and the reply is 500 (never a false closed claim).
        """
        with self._door_lock:
            self._instance_open = False
            self._ready = False
            self._record("instance-closed")
            if self.release_path is None:
                self._record("shared-close-skipped", "no-release-path")
                return (200, self._door_payload("ok"))
            try:
                self._atomic_write_json(self.release_path, {"status": "closed"})
            except Exception as exc:
                self._record("shared-close-failed", type(exc).__name__)
                return (500, self._door_payload("close-failed"))
            if not self._confirm_shared_closed():
                self._record("shared-close-unconfirmed")
                return (500, self._door_payload("close-failed"))
            self._record("shared-closed", "confirmed")
            return (200, self._door_payload("ok"))

    def install_registry(self):
        """Install the verified snapshot; both doors must already be closed.

        Re-reads and re-verifies the dual hashes, then calls the service's
        actual ``RegistryProvider.update()`` with those same verified bytes.
        A bad candidate raises inside ``update()`` and the old snapshot keeps
        serving (provider snapshot semantics, untouched here).
        """
        with self._door_lock:
            if self._instance_open or not self._shared_closed():
                self._record("install-refused", "doors-not-both-closed")
                return (
                    409,
                    self._door_payload("refused", {"reason": "doors-not-both-closed"}),
                )
            if self._provider is None:
                self._record("install-refused", "provider-unattached")
                return (
                    409,
                    self._door_payload("refused", {"reason": "provider-unattached"}),
                )
            ok, data = self._read_verify()
            if not ok:
                self._record("install-refused", data)
                return (409, self._door_payload("refused", {"reason": data}))
            reg_parsed, _pre_parsed = data
            try:
                self._provider.update(reg_parsed)
            except Exception as exc:
                self._record("install-refused", "snapshot-rejected=%s" % type(exc).__name__)
                return (
                    409,
                    self._door_payload("refused", {"reason": "snapshot-rejected"}),
                )
            self._installed = True
            self._installed_run = self.run_id
            self._ready = False
            self._verified = {
                "registry_sha": REGISTRY_SHA256,
                "presets_sha": PRESETS_SHA256,
            }
            self._record("installed")
            return (200, self._door_payload("ok"))

    def ready(self):
        """Confirm readiness: both doors closed AND a verified snapshot held.

        Forces the triple check (instance closed + shared closed + installed
        in this run) before arming ``open_door``. Never opens anything.
        """
        with self._door_lock:
            if self._instance_open or not self._shared_closed():
                self._record("ready-refused", "doors-not-both-closed")
                return (
                    409,
                    self._door_payload("refused", {"reason": "doors-not-both-closed"}),
                )
            if not self._installed or self._installed_run != self.run_id:
                self._record("ready-refused", "not-installed")
                return (
                    409,
                    self._door_payload("refused", {"reason": "not-installed"}),
                )
            if self._verified is None:
                self._record("ready-refused", "no-verified-snapshot")
                return (
                    409,
                    self._door_payload("refused", {"reason": "no-verified-snapshot"}),
                )
            ok, _data = self._read_verify()
            if not ok:
                self._record("ready-refused", _data)
                return (409, self._door_payload("refused", {"reason": _data}))
            self._ready = True
            self._record("ready")
            return (200, self._door_payload("ok"))

    def open_door(self):
        """Open both doors in order: shared first, instance last.

        Requires installed + ready in this same run. Re-verifies the dual
        hashes first; any drift re-closes both doors immediately and stops
        the ticket (409, never half-open). A restart (fresh controller)
        starts closed by construction and never restores open.
        """
        with self._door_lock:
            if not (
                self._installed
                and self._ready
                and self._installed_run == self.run_id
            ):
                self._record("open-refused", "not-installed-or-ready")
                return (
                    409,
                    self._door_payload(
                        "refused", {"reason": "not-installed-or-ready"}
                    ),
                )
            ok, _data = self._read_verify()
            if not ok:
                self._instance_open = False
                self._ready = False
                if self.release_path is not None:
                    if self._write_shared_closed():
                        self._record("drift-reclosed", _data)
                    else:
                        self._record("drift-reclose-unconfirmed", _data)
                else:
                    self._record("drift-reclosed", _data)
                return (
                    409,
                    self._door_payload("refused", {"reason": _data}),
                )
            if self.release_path is None:
                self._record("open-refused", "no-release-path")
                return (
                    409,
                    self._door_payload("refused", {"reason": "no-release-path"}),
                )
            try:
                self._atomic_write_json(self.release_path, {"status": "open"})
            except Exception as exc:
                self._record("open-failed", "shared-write=%s" % type(exc).__name__)
                self._instance_open = False
                return (500, self._door_payload("open-failed"))
            if not self.is_shared_open():
                self._record("open-failed", "shared-unconfirmed")
                self._instance_open = False
                return (500, self._door_payload("open-failed"))
            self._record("shared-opened", "confirmed")
            self._instance_open = True
            self._record("instance-opened")
            return (200, self._door_payload("ok"))

    # -- in-flight tracking ---------------------------------------------------

    @contextmanager
    def track(self):
        """Bracket one unit of work (drain waits for these to finish)."""
        if self.state != STATE_SERVING:
            raise _ControlRefused(503, "draining")
        self.inflight += 1
        try:
            yield
        finally:
            self.inflight -= 1

    # -- POST endpoint logic (pure; the ASGI wrapper only adapts I/O) --------

    def handle_post(self, client_host, body):
        """Decide one control POST. Returns ``(status, payload)``.

        Reply-first: on a verified shutdown the returned 200 payload is
        composed BEFORE the drain runs; the drain then executes and its
        outcome is visible in ``state`` / ``events`` (reconciliation), not
        by changing the already-composed reply.
        """
        if client_host not in CONTROL_LOOPBACKS:
            return (403, {"status": "forbidden", "run_id": self.run_id})
        presented = body.get("token") if isinstance(body, dict) else None
        if not isinstance(presented, str) or not hmac.compare_digest(
            presented, self._token
        ):
            self._record("shutdown-unauthorized")
            return (401, {"status": "unauthorized", "run_id": self.run_id})
        command = body.get("command") if isinstance(body, dict) else None
        if command == "shutdown":
            return self._shutdown()
        if command == "status":
            return (
                200,
                {
                    "status": "ok",
                    "run_id": self.run_id,
                    "state": self.state,
                    "inflight": self.inflight,
                },
            )
        # -- SA-DOOR-MECH-1 door commands: loopback + token (above) plus an
        # explicit run_id match. A wrong or missing run_id refuses 401 with
        # zero state change, exactly like a wrong token. ``panic-close`` is a
        # direct ``close_door()`` call (one-shot dual re-close, idempotent).
        if command in ("close", "install", "ready", "open", "panic-close"):
            presented_run = body.get("run_id") if isinstance(body, dict) else None
            if not isinstance(presented_run, str) or not hmac.compare_digest(
                presented_run, self.run_id
            ):
                self._record("door-unauthorized")
                return (401, {"status": "unauthorized", "run_id": self.run_id})
            if command == "close" or command == "panic-close":
                return self.close_door()
            if command == "install":
                return self.install_registry()
            if command == "ready":
                return self.ready()
            if command == "open":
                return self.open_door()
        return (400, {"status": "bad-command", "run_id": self.run_id})

    # -- shutdown (idempotent; BLOCKED on drain anomaly) ----------------------

    def _shutdown(self):
        if self.state in (STATE_SHUT, STATE_BLOCKED):
            self._record("shutdown-repeat")
            return (
                200,
                {"status": "ok", "run_id": self.run_id, "state": self.state},
            )
        reply = (200, {"status": "ok", "run_id": self.run_id, "state": STATE_DRAINING})
        self.state = STATE_DRAINING
        self._record("drain-begin", "inflight=%d" % self.inflight)
        drained = self._drain()
        self._close_listener()
        self._listener = None
        self._signal_server()
        if drained:
            self.state = STATE_SHUT
            self._record("shut")
        else:
            self.state = STATE_BLOCKED
            self._record("blocked", "inflight=%d" % self.inflight)
        return reply

    def _signal_server(self):
        """Ask the injected serving loop to exit (SA-THIN-SHUTDOWN-FIX).

        Sets ``server.should_exit = True`` on the ``uvicorn.Server`` that
        ``main()`` injected via ``attach_server()``. Runs on BOTH the shut
        and the blocked paths: even a stuck drain must still release the
        serving loop so the process ends (blocked reports via ``state`` /
        ``result_code``, never by hanging). ``None`` (pure-logic path) is
        a no-op; a faulty server object never breaks the shutdown reply.
        """
        server = self._server
        if server is None:
            return
        try:
            server.should_exit = True
        except Exception:
            return
        self._record("server-signalled")

    def _drain(self):
        deadline = self._clock() + self._drain_timeout
        while self.inflight > 0:
            if self._poller is not None:
                try:
                    self._poller()
                except Exception:
                    pass
            if self.inflight <= 0:
                break
            if self._clock() >= deadline:
                return False
            try:
                self._sleeper(0.01)
            except Exception:
                pass
        return True

    def _close_listener(self):
        if self._listener_closed:
            return
        self._listener_closed = True
        listener = self._listener
        if listener is None:
            self._record("listener-released", "none")
            return
        try:
            listener.close()
        except Exception as exc:
            self._record("listener-released", "close-fault=%s" % type(exc).__name__)
            return
        self._record("listener-released", "closed")

    @property
    def result_code(self):
        """Normal-return code: 0 shut, 3 blocked, 1 still serving/draining."""
        if self.state == STATE_SHUT:
            return 0
        if self.state == STATE_BLOCKED:
            return 3
        return 1


class _ControlRefused(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def mount_control_route(app, control):
    """Register the loopback-only POST control route (additive).

    Only ``serve_sa_thin`` calls this; the frozen gateway assembly is
    untouched (this inserts one route object on the already-built app
    instance ahead of the catch-all). The handler enforces, per request:
    loopback source -> token -> command. It never consults the release
    gate, so shutdown works while the production gate is closed.
    """
    from fastapi.responses import JSONResponse
    from fastapi.routing import APIRoute

    async def _control_endpoint(request: Request):
        try:
            scope_client = (request.scope or {}).get("client")
            host = scope_client[0] if scope_client else None
        except Exception:
            host = None
        try:
            body = await request.json()
        except Exception:
            body = None
        status, payload = control.handle_post(host, body)
        return JSONResponse(status_code=status, content=payload)

    route = APIRoute(CONTROL_ENDPOINT, endpoint=_control_endpoint, methods=["POST"])
    try:
        app.routes.insert(0, route)
    except Exception:
        try:
            app.router.routes.insert(0, route)
        except Exception:
            pass
    return route


#: Business pump (SA-DOOR-MECH-FIX-1 D2): a dedicated daemon thread driving
#: one event loop created at import time. Import time is before the SA-0
#: test fixture hard-blocks ``socket.socket`` (Windows loop construction
#: needs a socketpair-backed self-pipe), and the serving loop hands jobs
#: over with plain ``queue.Queue`` + ``asyncio.sleep`` polling -- never a
#: cross-thread asyncio wakeup (also socket-backed, also hard-blocked).
#: Production uses this identical path: no file reads, no env reads, no
#: port attach, no serving at import (same idiom as the test-side runner).
_BUSINESS_LOOP = asyncio.new_event_loop()
_BUSINESS_JOBS = queue.Queue()


def _business_pump():
    asyncio.set_event_loop(_BUSINESS_LOOP)
    while True:
        coro, out_q = _BUSINESS_JOBS.get()
        try:
            out_q.put((True, _BUSINESS_LOOP.run_until_complete(coro)))
        except BaseException as exc:  # noqa: BLE001 -- report, never die
            out_q.put((False, exc))


_BUSINESS_THREAD = threading.Thread(
    target=_business_pump, name="sa-thin-business", daemon=True
)
_BUSINESS_THREAD.start()


def _is_sa_marked_body(buffered) -> bool:
    """Report whether the buffered HTTP body carries the soul-access marker.

    Pure inspection of the already-buffered ``http.request`` messages: join
    their ``body`` bytes, decode UTF-8, JSON-parse, and check for the
    ``soul_access_v1`` key. Any fault (empty / non-UTF-8 / non-JSON /
    non-dict / marker absent) reads as NOT marked -- the caller falls back
    to the business pump, so a misread can only cost a queue slot, never a
    wrong refusal.
    """
    try:
        raw = b"".join(
            (m.get("body") or b"")
            for m in buffered
            if isinstance(m, dict) and m.get("type") == "http.request"
        )
        if not raw:
            return False
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        return False
    return isinstance(body, dict) and "soul_access_v1" in body


def _isolate_business_asgi(app, *, control=None, reject_app=None,
                           enable_reject_fast_path=True):
    """Run business paths on the pump; keep control on the serving loop.

    SA-DOOR-MECH-FIX-1 (thin-side only; frozen ``src/external_gateway/app.py``
    untouched): the gateway calls the soul handler synchronously inside its
    async dispatch, so a slow upstream pins the serving loop and starves
    the loopback control route -- panic-close could not complete during a
    stall. This wrapper keeps the control route (and lifespan / non-HTTP
    scopes) on the serving loop and runs every other HTTP scope on the
    import-time business pump: the request body is buffered on the serving
    loop, the wrapped app runs to completion on the pump thread against
    in-memory channels, and the captured response is replayed on the
    serving loop. A stalled business call then parks the pump thread,
    never the control route -- and no cross-thread asyncio wakeup is ever
    needed, so the SA-0 socket hard-block cannot trip it.

    SA-DOOR-MECH-FIX-2 (closed-door reject fast path): after the body is
    buffered on the serving loop, a closed instance door plus a
    soul-access-marked body is served DIRECTLY by ``reject_app`` on the
    serving loop -- never enqueued behind the single pump. ``reject_app``
    is a second ``create_external_app`` over the SAME provider / preset
    loader: auth (401), routing (404) and the error map are identical, and
    only its soul handler is pinned to refuse (403). Non-SA bodies are
    untouched (still via the pump). ``enable_reject_fast_path=False`` is
    the test-only bypass seam (proves the queue is the blocker: bypassed
    B starves behind A -> RED; reverted -> GREEN).
    """

    async def _run_on_this_loop(target, t_scope, t_receive, t_send):
        captured: list = []

        async def _mem_send(message):
            captured.append(message)

        await target(t_scope, t_receive, _mem_send)
        for message in captured:
            await t_send(message)

    async def _isolated(scope, receive, send):
        if not isinstance(scope, dict) or scope.get("type") != "http":
            return await app(scope, receive, send)
        if scope.get("path") == CONTROL_ENDPOINT:
            return await app(scope, receive, send)
        buffered = []
        while True:
            message = await receive()
            buffered.append(message)
            if message.get("type") != "http.request" or not message.get(
                "more_body"
            ):
                break
        # FIX-2 fast path: closed door + SA-marked body -> reject_app here,
        # never behind the pump. The door read is a short lock hold (same
        # as the in-handler admission check); the reject run reuses the
        # buffered bytes on THIS loop, so a parked pump slot cannot starve
        # the refusal. Any fault falls through to the pump (fail-queued,
        # never fail-refused).
        if enable_reject_fast_path and reject_app is not None:
            try:
                if control is None:
                    closed = not _THIN_OPEN
                else:
                    with control._door_lock:
                        closed = not control._instance_open
            except Exception:
                closed = False
            if closed and _is_sa_marked_body(buffered):
                replay_fast = iter(buffered)

                async def _fast_receive():
                    try:
                        return next(replay_fast)
                    except StopIteration:
                        await asyncio.Event().wait()

                await _run_on_this_loop(reject_app, scope, _fast_receive, send)
                return
        sent = []
        replay = iter(buffered)

        async def _mem_receive():
            try:
                return next(replay)
            except StopIteration:
                await asyncio.Event().wait()

        async def _mem_send(message):
            sent.append(message)

        out_q = queue.Queue(maxsize=1)
        _BUSINESS_JOBS.put((app(scope, _mem_receive, _mem_send), out_q))
        while True:
            try:
                ok, value = out_q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
                continue
            break
        if not ok:
            raise value
        for message in sent:
            await send(message)

    _isolated.__wrapped__ = app
    # Route-introspection passthrough: the wrapper is a plain ASGI function,
    # but registration proofs (T15) read ``app.routes`` off the built app.
    # Mirror the wrapped Starlette app's routing surface read-only; the
    # serving path itself never consults these attributes.
    _isolated.routes = app.routes
    _isolated.router = app.router
    return _isolated


def is_thin_open() -> bool:
    """Report the instance door state (always False in this ticket)."""
    return _THIN_OPEN


def build_thin_closed_handler(*, release_path=None, _forwarder=None, control=None):
    """Build the closed-only soul-access handler for this instance.

    The door refuses FIRST (403, zero forward) before the shared release
    gate, the upstream POST, or any memory seam is touched. ``release_path``
    selects the forwarder the door guards (default: the pinned production
    file); the door itself never consults it while closed. ``_forwarder``
    is a test-only seam (counting stand-in); production always builds the
    real thin forwarder (which still independently checks the release file
    per request, so the entry is doubly closed).

    SA-DOOR-MECH-1: when ``control`` (a ``ThinControl``) is given, the
    instance door is that controller's instance-owned flag, consulted under
    the shared door lock; a closed instance door refuses 403 with zero
    forward, and an open one delegates to the forwarder (which still
    hot-reads the shared gate itself). When ``control`` is ``None`` the
    legacy process constant ``_THIN_OPEN`` (always False) decides.
    """
    forwarder = _forwarder
    if forwarder is None:
        target = release_path if release_path is not None else str(_sa_wiring.RELEASE_FILE)
        forwarder = _sa_wiring.build_thin_forwarder(release_path=target)

    if control is None:
        def _handle(envelope, *, route=None, preset=None):
            if not _THIN_OPEN:
                raise _sa_bridge.LiveRefused(403, "forbidden", _CLOSED_MESSAGE)
            return forwarder(envelope, route=route, preset=preset)
    else:
        # SA-DOOR-MECH-FIX-1: the door lock guards ONLY the admission check
        # and the state flags. The forwarder runs AFTER the lock is released,
        # so a slow upstream parks the calling worker, never a re-close:
        # panic-close (instance + shared, same lock) always gets the lock.
        def _handle(envelope, *, route=None, preset=None):
            with control._door_lock:
                admitted = control._instance_open
            if not admitted:
                raise _sa_bridge.LiveRefused(403, "forbidden", _CLOSED_MESSAGE)
            return forwarder(envelope, route=route, preset=preset)

    return _handle


def _read_pinned(path, *, expected_sha256) -> tuple[bytes, object]:
    """Read a config file and pin it to the expected content hash.

    Returns ``(raw_bytes, parsed_json)``. Any fault (missing/unreadable/
    unparsable/hash drift) raises ``ValueError`` -- the caller maps every
    fault to the single fixed refusal copy.
    """
    try:
        raw = Path(path).read_bytes()
    except (OSError, ValueError):
        raise ValueError("unreadable")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("hash drift")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise ValueError("bad json")
    return (raw, parsed)


def _is_loopback_free(_socket_factory=None) -> bool:
    """Probe whether ``THIN_HOST:THIN_PORT`` is free (bind then release).

    ``_socket_factory`` defaults to ``socket.socket``; tests inject a fake
    (raising ``OSError`` = occupied, dummy = free) so no test ever binds a
    real port. Any ``OSError`` means occupied.
    """
    factory = _socket_factory if _socket_factory is not None else socket.socket
    try:
        probe = factory()
    except OSError:
        return False
    try:
        probe.bind((THIN_HOST, THIN_PORT))
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except Exception:
            pass


def preflight(registry_path, presets_path, *, _socket_factory=None) -> int:
    """Fail-closed startup checks. Returns 0 (go) or 2 (refuse).

    Pure orchestration over reads + one bind probe; never serves, never
    writes, never touches the network beyond the loopback probe.
    """
    try:
        registry_raw, registry_parsed = _read_pinned(
            registry_path, expected_sha256=REGISTRY_SHA256
        )
    except ValueError:
        return 2
    try:
        if "do-not-deploy" in registry_raw.decode("utf-8"):
            return 2
    except (ValueError, UnicodeDecodeError):
        return 2
    try:
        RegistryProvider(registry_parsed)
    except Exception:
        return 2
    try:
        _read_pinned(presets_path, expected_sha256=PRESETS_SHA256)
    except ValueError:
        return 2
    if not _is_loopback_free(_socket_factory):
        return 2
    return 0


#: Rem persona source (THIN-OVERLAY-REM-1): the project-root persona
#: file, read UTF-8 and cached per path. Fail-open: any read fault reads
#: as missing and the overlay degrades to the raw parsed input.
_REM_PERSONA_DEFAULT = _REPO_ROOT / "personas" / "agent_rem.md"

#: Persona text cache keyed by the resolved path string.
_REM_PERSONA_CACHE: dict = {}


def _load_rem_persona_text(persona_path=None):
    """Read the Rem persona file (UTF-8, cached). None on any fault.

    A missing / unreadable / empty file is logged (ERROR, err type only --
    no path, no content) and reads as None, so the overlay returns None and
    the gateway falls back to the raw parsed input through the existing
    overlay-degraded path.
    """
    path = Path(persona_path) if persona_path is not None else _REM_PERSONA_DEFAULT
    key = str(path)
    if key in _REM_PERSONA_CACHE:
        return _REM_PERSONA_CACHE[key]
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        _LOGGER.error(
            "ext soul=rem status=overlay-persona-unreadable err=%s",
            type(exc).__name__,
        )
        return None
    if not isinstance(text, str) or not text:
        _LOGGER.error("ext soul=rem status=overlay-persona-unreadable err=empty")
        return None
    _REM_PERSONA_CACHE[key] = text
    return text


def build_rem_soul_overlay(persona_path=None):
    """Build the thin Rem soul overlay (THIN-OVERLAY-REM-1, fail-open).

    The returned ``soul_overlay_fn(messages, params)`` injects the full
    ``personas/agent_rem.md`` text as the leading system message: when the
    first message is already ``system`` its content is prefixed (persona +
    blank line + original), otherwise a new system message is inserted at
    the front. Success logs ``overlay-applied``; any fault returns None so
    the gateway falls back to the raw parsed input (the existing
    overlay-degraded path, never a 500).
    """

    def _overlay(messages, params):
        try:
            text = _load_rem_persona_text(persona_path)
            if not text:
                return None
            msgs = tuple(messages)
            if msgs and msgs[0].role == "system" and isinstance(msgs[0].content, str):
                head = ChatMessage(
                    role="system",
                    content=text + "\n\n" + msgs[0].content,
                    extras=dict(msgs[0].extras),
                )
                overlaid = (head,) + msgs[1:]
            else:
                overlaid = (ChatMessage(role="system", content=text, extras={}),) + msgs
            _LOGGER.info(
                "ext soul=rem status=overlay-applied prompt_chars=%d req_msgs=%d",
                len(text),
                len(msgs),
            )
            return (overlaid, params)
        except Exception:
            return None

    return _overlay


def build_thin_app(registry_path, presets_path, *, release_path=None, _forwarder=None, control=None,
                   enable_reject_fast_path=True, soul_overlay_fn=None):
    """Assemble the thin ASGI app (no carrier change, no serving).

    Auth / routing / preset loading are the production objects; the only
    thin-specific pieces are the closed-only instance-door handler and the
    optional loopback-only control route (``control`` is a ``ThinControl``;
    ``None`` keeps the app exactly as before).

    FIX-2: builds a second app (``reject_app``) over the SAME provider /
    preset loader with ONLY the soul handler pinned to refuse. The serving
    wrapper serves closed-door SA-marked bodies through it directly, so a
    parked pump slot cannot starve a new refusal. ``_forwarder`` (test
    seam) feeds BOTH apps: the main handler and the reject handler refuse
    before touching it while closed, so the upstream count is identical.
    """
    with open(registry_path, encoding="utf-8") as fh:
        registry = RegistryProvider(json.load(fh))
    loader = file_preset_loader(presets_path)
    # THIN-OVERLAY-REM-1: the Rem soul overlay rides BOTH apps (reject +
    # business) so standard (marker-free) requests carry the Rem persona
    # upstream. Explicit injection (tests) wins; default is the project-root
    # ``personas/agent_rem.md``. Fail-open: any overlay fault degrades to
    # the raw parsed input (gateway overlay-degraded path, never a 500).
    overlay = soul_overlay_fn if callable(soul_overlay_fn) else build_rem_soul_overlay()
    # FIX-2 reject handler: the legacy constant-closed handler (control=None
    # -> ``_THIN_OPEN`` False -> always 403, zero forward). Built through
    # the SAME ``build_thin_closed_handler`` seam the door-removal mutation
    # patches, so that mutant still admits (RED) instead of being masked by
    # the fast path.
    reject_app = create_external_app(
        registry,
        loader,
        PresetRoutingBackend(),
        soul_live_handler=build_thin_closed_handler(
            release_path=release_path, _forwarder=_forwarder, control=None
        ),
        soul_overlay_fn=overlay,
    )
    app = create_external_app(
        registry,
        loader,
        PresetRoutingBackend(),
        soul_live_handler=build_thin_closed_handler(
            release_path=release_path, _forwarder=_forwarder, control=control
        ),
        soul_overlay_fn=overlay,
    )
    if control is not None:
        control.attach(
            provider=registry,
            registry_path=str(registry_path),
            presets_path=str(presets_path),
            release_path=(str(release_path) if release_path is not None else None),
        )
        mount_control_route(app, control)
    # SA-DOOR-MECH-FIX-1: business on the pump thread, control on this loop.
    # FIX-2: closed-door SA bodies refuse on this loop via reject_app.
    return _isolate_business_asgi(
        app,
        control=control,
        reject_app=reject_app,
        enable_reject_fast_path=enable_reject_fast_path,
    )


def build_parser() -> argparse.ArgumentParser:
    """CLI shape: registry + presets paths only (host/port are pinned)."""
    parser = argparse.ArgumentParser(
        description="Serve the SA thin entry on loopback 127.0.0.1:18765 (closed-only)."
    )
    parser.add_argument(
        "--registry",
        default=str(REGISTRY_DEFAULT),
        help="Registry JSON file (default: %(default)s; content must match the pinned hash).",
    )
    parser.add_argument(
        "--presets",
        default=str(PRESETS_DEFAULT),
        help="Preset JSON file (default: %(default)s; content must match the pinned hash).",
    )
    parser.add_argument(
        "--control-file",
        default=CONTROL_FILE_DEFAULT,
        help="Control-file path for the loopback shutdown token "
        "(default: %(default)s; token is never logged).",
    )
    parser.add_argument(
        "--control",
        choices=("close", "install", "ready", "open", "panic-close"),
        default=None,
        help="Send one door command to the running thin entry over "
        "loopback and exit (no bind, no serve; fixed endpoint, zero retry; "
        "success exits 0, lost contact or write failure exits 2).",
    )
    return parser


def _refuse() -> int:
    print(_STARTUP_REFUSAL, file=sys.stderr)
    return 2


#: Door commands the ``--control`` client may send (server side accepts the
#: same set; ``shutdown`` / ``status`` stay server-side only).
CONTROL_COMMANDS = ("close", "install", "ready", "open", "panic-close")


def _run_control_command(command, control_file, *, _connect=None) -> int:
    """Send one door command to the running entry; return the exit code.

    Runs BEFORE any preflight / bind / serve work in ``main()``. Reads the
    existing control file (run_id + token + endpoint), POSTs once to the
    fixed loopback endpoint (zero retry), and exits 0 only on a 200 reply.
    Lost contact, an unreadable control file, or a write failure exits 2
    and never claims a re-close. ``_connect`` is a test-only
    ``HTTPConnection`` factory seam (default: the real one, loopback-only).
    """
    try:
        filed = json.loads(Path(control_file).read_text(encoding="utf-8"))
        run_id = filed.get("run_id")
        token = filed.get("token")
        endpoint = filed.get("endpoint", CONTROL_ENDPOINT)
    except Exception:
        print("sa thin control: unreadable control file.", file=sys.stderr)
        return 2
    if (
        endpoint != CONTROL_ENDPOINT
        or not isinstance(run_id, str)
        or not run_id
        or not isinstance(token, str)
        or not token
    ):
        print("sa thin control: control file refused.", file=sys.stderr)
        return 2
    body = json.dumps({"token": token, "command": command, "run_id": run_id})
    factory = _connect if _connect is not None else http.client.HTTPConnection
    try:
        conn = factory(THIN_HOST, THIN_PORT, timeout=5)
        conn.request(
            "POST",
            CONTROL_ENDPOINT,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode("utf-8"))),
            },
        )
        response = conn.getresponse()
        status = response.status
        response.read()
    except Exception:
        print(
            "sa thin control: %s failed (no contact)." % command,
            file=sys.stderr,
        )
        return 2
    if status == 200:
        print("sa thin control: %s ok." % command)
        return 0
    print(
        "sa thin control: %s refused (status %d)." % (command, status),
        file=sys.stderr,
    )
    return 2


def main(argv=None) -> int:
    """Parse, preflight, announce, serve. Returns process exit code."""
    args = build_parser().parse_args(argv)
    if args.control is not None:
        return _run_control_command(args.control, args.control_file)
    if preflight(args.registry, args.presets) != 0:
        return _refuse()
    control = ThinControl(control_path=args.control_file)
    control.write_control_file()
    app = build_thin_app(
        args.registry,
        args.presets,
        release_path=str(_sa_wiring.RELEASE_FILE),
        control=control,
    )
    # run_id is reconciliation inventory (the token itself is never printed).
    print(
        "sa thin entry on %s:%s (closed, loopback-only) run_id=%s control=%s"
        % (THIN_HOST, THIN_PORT, control.run_id, CONTROL_ENDPOINT),
        flush=True,
    )
    # SA-THIN-SHUTDOWN-FIX: serve via an explicit Server instance injected
    # into the controller, so a verified shutdown POST flips
    # ``should_exit`` and the serving loop returns: the process ends
    # normally (exit 0) and the loopback bind is released. Previously
    # ``uvicorn.run(...)`` owned an unreachable Server: shutdown drained
    # and closed the (here ``None``) listener but the loop never exited,
    # so the process -- and :18765 -- stayed alive.
    config = uvicorn.Config(
        app, host=THIN_HOST, port=THIN_PORT, reload=False, log_level="info"
    )
    server = uvicorn.Server(config)
    control.attach_server(server)
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
