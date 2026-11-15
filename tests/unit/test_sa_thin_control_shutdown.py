"""SA-THIN-SHUTDOWN-FIX: graceful shutdown releases the serving loop.

Proves the fixed behavior of ``scripts/serve_sa_thin.py``: after a verified
``shutdown`` POST the controller not only drains and closes the listener but
also flips ``should_exit`` on the injected ``uvicorn.Server`` instance, so
the serving loop returns, the process ends normally (exit 0), and the
loopback bind is released.

Hygiene (per ticket, enforced below, not just documented):

- no test binds a fixed port: the only real socket in this file binds
  ``127.0.0.1:0`` (OS-assigned dynamic port); ``18765`` / ``8000`` /
  ``8765`` / ``8766`` / ``8767`` never appear in a ``bind`` call;
- no server is spawned, no production path is touched (control files live
  under ``tmp_path``; ``uvicorn.Server`` / ``preflight`` /
  ``build_thin_app`` are monkeypatched where ``main()`` is exercised);
- no kills anywhere: process-termination primitives must not appear in
  executable code of either this file or ``scripts/serve_sa_thin.py``
  (denial prose lives in full-line comments only; the exact token list is
  asserted by the negative tests below);
- no Frozen contract is touched (Agency 4 stages / TriggerEnvelope /
  InnerLifeEvent / 4 handlers / SAGE writes): this file only drives
  ``ThinControl`` shutdown logic.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest

import scripts.serve_sa_thin as thin_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
THIN_ENTRY = REPO_ROOT / "scripts" / "serve_sa_thin.py"

#: Ports this file must never bind (production + thin fixed bind).
FORBIDDEN_PORTS = (18765, 8000, 8765, 8766, 8767)


class _FakeListener:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeClock:
    def __init__(self, start=3000.0):
        self.t = start

    def __call__(self):
        return self.t


def _make_advancing_clock(start=3000.0, step=1.0):
    """Frozen-clock-safe drain clock: the sleeper advances the clock, so a
    drain that outlives its budget terminates (BLOCKED) instead of spinning
    forever on a clock that never moves while the sleeper is a no-op."""
    clock = _FakeClock(start)

    def _sleeper(s):
        clock.t += max(step, s)

    return clock, _sleeper


class _FakeServer:
    """Minimal serving-loop stand-in: ``run()`` exits when told to."""

    def __init__(self, config=None):
        self.config = config
        self.should_exit = False
        self.run_called = 0

    def run(self):
        self.run_called += 1
        while not self.should_exit:
            time.sleep(0.01)


class _FaultyServer:
    """``should_exit`` cannot be set: shutdown must still reply 200/SHUT."""

    def __setattr__(self, name, value):
        if name == "should_exit":
            raise RuntimeError("faulty-server")
        super().__setattr__(name, value)


def _control(tmp_path, **kwargs):
    kwargs.setdefault("control_path", str(tmp_path / "control.json"))
    kwargs.setdefault("sleeper", lambda s: None)
    return thin_mod.ThinControl(**kwargs)


def _code_lines(path: Path) -> str:
    """File text minus full-line comments (denial prose is comments-only)."""
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def _unquoted(line: str) -> str:
    """Strip single/double-quoted literals (guard assertions name the very
    tokens they forbid; only unquoted *usage* counts as a violation)."""
    return re.sub(r'"[^"]*"|\'[^\']*\'', "", line)


# -- shutdown signals the injected server ------------------------------------


def test_shutdown_signals_injected_server(tmp_path):
    """Verified shutdown: 200/draining reply, SHUT, listener closed AND
    the injected server told to exit (the SA-THIN-SHUTDOWN-FIX behavior)."""
    listener = _FakeListener()
    server = _FakeServer()
    control = _control(
        tmp_path,
        run_id="run-shut-1",
        token="shut-token-1",
        clock=_FakeClock(),
        listener=listener,
        server=server,
    )
    control.write_control_file()
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "shut-token-1", "command": "shutdown"}
    )
    assert status == 200 and payload.get("state") == thin_mod.STATE_DRAINING
    assert control.state == thin_mod.STATE_SHUT
    assert listener.closed == 1 and control.result_code == 0
    assert server.should_exit is True
    assert any(ev["event"] == "server-signalled" for ev in control.events)


def test_shutdown_without_server_stays_pure_logic(tmp_path):
    """No server attached: shutdown keeps the old pure-logic behavior
    (backward compatible with the T15/T17 fake-listener tests)."""
    listener = _FakeListener()
    control = _control(
        tmp_path,
        run_id="run-shut-0",
        token="shut-token-0",
        clock=_FakeClock(),
        listener=listener,
    )
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "shut-token-0", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT
    assert listener.closed == 1 and control.result_code == 0


def test_blocked_drain_still_signals_server(tmp_path):
    """Stuck drain: state BLOCKED (result 3) but the serving loop is still
    released -- blocked reports via state, never by hanging the process."""
    listener = _FakeListener()
    server = _FakeServer()
    clock, sleeper = _make_advancing_clock()
    control = _control(
        tmp_path,
        run_id="run-shut-blocked",
        token="shut-token-blocked",
        clock=clock,
        sleeper=sleeper,
        listener=listener,
        server=server,
    )
    control.inflight = 1  # stuck work: the advancing clock outruns the budget
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "shut-token-blocked", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_BLOCKED
    assert control.result_code == 3
    assert server.should_exit is True


def test_repeat_shutdown_keeps_server_signalled(tmp_path):
    """Idempotent: a second shutdown re-replies 200 and the exit signal
    stays latched."""
    server = _FakeServer()
    control = _control(
        tmp_path,
        run_id="run-shut-repeat",
        token="shut-token-repeat",
        clock=_FakeClock(),
        listener=_FakeListener(),
        server=server,
    )
    control.handle_post(
        "127.0.0.1", {"token": "shut-token-repeat", "command": "shutdown"}
    )
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "shut-token-repeat", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT
    assert server.should_exit is True


def test_faulty_server_never_breaks_shutdown_reply(tmp_path):
    """A server object that refuses ``should_exit`` must not break the
    200/SHUT shutdown reply (signal faults are swallowed, never raised)."""
    control = _control(
        tmp_path,
        run_id="run-shut-faulty",
        token="shut-token-faulty",
        clock=_FakeClock(),
        listener=_FakeListener(),
        server=_FaultyServer(),
    )
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "shut-token-faulty", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT


def test_attach_server_returns_self_and_detach_restores_pure_logic(tmp_path):
    control = _control(tmp_path, run_id="run-attach", token="tok-attach")
    server = _FakeServer()
    assert control.attach_server(server) is control
    assert control._server is server
    assert control.attach_server(None) is control
    assert control._server is None


# -- dynamic-port release (never a fixed / production port) -------------------


def test_shutdown_releases_dynamic_port(tmp_path):
    """A real loopback socket on an OS-assigned port (``bind(:0)``) wrapped
    as the listener is actually closed by shutdown: re-binding the same
    port succeeds, i.e. the bind is released."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 0))  # dynamic only: never a fixed port
    port = probe.getsockname()[1]
    assert port not in FORBIDDEN_PORTS

    class _SocketListener:
        def __init__(self, sock):
            self._sock = sock
            self.closed = 0

        def close(self):
            self.closed += 1
            self._sock.close()

    listener = _SocketListener(probe)
    control = _control(
        tmp_path,
        run_id="run-shut-port",
        token="shut-token-port",
        clock=_FakeClock(),
        listener=listener,
    )
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "shut-token-port", "command": "shutdown"}
    )
    assert status == 200 and listener.closed == 1

    check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        check.bind(("127.0.0.1", port))  # raises if the port were LISTENING
    finally:
        check.close()


def test_serving_loop_thread_exits_after_shutdown(tmp_path):
    """End-to-end shape of the fix: a thread running the ``while not
    should_exit`` serving loop (what ``uvicorn.Server.serve`` does) exits
    within seconds of a verified shutdown -- the process-end equivalent."""
    server = _FakeServer()
    control = _control(
        tmp_path,
        run_id="run-shut-thread",
        token="shut-token-thread",
        clock=_FakeClock(),
        listener=_FakeListener(),
        server=server,
    )
    worker = threading.Thread(target=server.run, name="fake-serve-loop")
    worker.daemon = True
    worker.start()
    assert worker.is_alive()
    try:
        status, _ = control.handle_post(
            "127.0.0.1", {"token": "shut-token-thread", "command": "shutdown"}
        )
        assert status == 200
        worker.join(timeout=5.0)
        assert not worker.is_alive()
        assert server.run_called == 1
    finally:
        server.should_exit = True  # belt-and-braces: never leak the thread
        worker.join(timeout=5.0)


# -- main() wiring: injects the very Server that shutdown signals -----------


def test_main_injects_server_and_returns_zero(tmp_path, monkeypatch):
    """``main()`` builds a loopback ``Config`` + ``Server``, injects it via
    ``attach_server()``, serves, and returns 0. Driving shutdown on the
    injected control flips that same server's ``should_exit`` -- the exact
    path that was missing before the fix (``uvicorn.run`` owned an
    unreachable Server). No port is bound: ``preflight``,
    ``build_thin_app`` and ``uvicorn.Server`` are all stubbed."""
    import uvicorn

    created = {}

    class _SpyServer:
        """Wiring-only stand-in: ``run()`` returns at once (no serving loop).

        The loop-exit half is proven by
        ``test_serving_loop_thread_exits_after_shutdown``; here ``run()``
        must NOT block or ``main()`` could never return to the assertion.
        """

        def __init__(self, config):
            self.config = config
            self.should_exit = False
            self.run_called = 0
            created["server"] = self

        def run(self):
            self.run_called += 1

    monkeypatch.setattr(thin_mod, "preflight", lambda *a, **k: 0)
    monkeypatch.setattr(thin_mod, "build_thin_app", lambda *a, **k: object())
    monkeypatch.setattr(uvicorn, "Server", _SpyServer)

    orig_attach = thin_mod.ThinControl.attach_server

    def _spy_attach(self, server):
        created["control"] = self
        created["server_arg"] = server
        return orig_attach(self, server)

    monkeypatch.setattr(thin_mod.ThinControl, "attach_server", _spy_attach)

    control_file = tmp_path / "control.json"
    rc = thin_mod.main(["--control-file", str(control_file)])
    assert rc == 0
    assert created["server"].run_called == 1
    # Same object both ways: main injected the serving server.
    assert created["server_arg"] is created["server"]
    assert created["control"]._server is created["server"]
    # The serving config stays loopback-only on the frozen thin port.
    assert created["server"].config.host == "127.0.0.1"
    assert created["server"].config.port == thin_mod.THIN_PORT
    # Control file stayed inside the sandbox (no repo writes).
    assert control_file.is_file()

    control = created["control"]
    status, _ = control.handle_post(
        "127.0.0.1", {"token": control.read_token(), "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT
    assert created["server"].should_exit is True


# -- hygiene: no kill capability, no fixed-port binds --------------------------


def test_no_kill_capability_in_thin_entry():
    """``scripts/serve_sa_thin.py`` must contain no process-kill capability
    in executable code (denial prose lives in full-line comments only)."""
    code = _code_lines(THIN_ENTRY).lower()
    assert "stop-process" not in code
    assert "taskkill" not in code
    assert "pkill" not in code
    assert "os.kill" not in code
    bare = [
        line
        for line in code.splitlines()
        if re.search(r"(?<![\w\-.])kill\s*\(", line)
    ]
    assert bare == []


def test_no_kill_capability_in_this_test_file():
    """The test itself must also carry no kill capability (usage only:
    the assertion literals below are unquoted away before matching)."""
    code = "\n".join(_unquoted(line) for line in _code_lines(Path(__file__)).splitlines()).lower()
    assert "stop-process" not in code
    assert "taskkill" not in code
    assert "pkill" not in code
    assert "os.kill" not in code

    # The only bind calls in this file target the dynamic port (``:0``);
    # every forbidden literal below appears solely in the guard tuple.
    binds = [
        line
        for line in code.splitlines()
        if ".bind(" in line
    ]
    assert binds, "expected the dynamic-port bind to be present"
    for line in binds:
        for port in FORBIDDEN_PORTS:
            assert str(port) not in line, line
