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
import hashlib
import json
import socket
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import uvicorn

from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import PresetRoutingBackend
from src.interfaces.external_auth import RegistryProvider
from src.soul_access import bridge as _sa_bridge
from src.soul_access import wiring as _sa_wiring

#: Loopback-only bind. No flag can change it (there is no --host/--port).
THIN_HOST = "127.0.0.1"

#: Thin port. Must stay the frozen outward port (asserted in tests).
THIN_PORT = 18765

assert THIN_PORT == EXTERNAL_PORT

#: Pinned e351541 content hashes (``configs/sa/registry.json`` +
#: ``configs/sa/presets.json``). Re-read and compared on every start;
#: any drift refuses startup. Lowercase hex, ``hashlib.sha256().hexdigest()``.
REGISTRY_SHA256 = "89c306660a7d57050f00a8fe45d4dab0f784a63dc23b0c03cf7d6db3ef759128"
PRESETS_SHA256 = "c37de506f9ab8f6d46d613f97ade6ae5642157b7b3a19ba7ed165d092b58cdc1"

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


def is_thin_open() -> bool:
    """Report the instance door state (always False in this ticket)."""
    return _THIN_OPEN


def build_thin_closed_handler(*, release_path=None, _forwarder=None):
    """Build the closed-only soul-access handler for this instance.

    The door refuses FIRST (403, zero forward) before the shared release
    gate, the upstream POST, or any memory seam is touched. ``release_path``
    selects the forwarder the door guards (default: the pinned production
    file); the door itself never consults it while closed. ``_forwarder``
    is a test-only seam (counting stand-in); production always builds the
    real thin forwarder (which still independently checks the release file
    per request, so the entry is doubly closed).
    """
    forwarder = _forwarder
    if forwarder is None:
        target = release_path if release_path is not None else str(_sa_wiring.RELEASE_FILE)
        forwarder = _sa_wiring.build_thin_forwarder(release_path=target)

    def _handle(envelope, *, route=None, preset=None):
        if not _THIN_OPEN:
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


def build_thin_app(registry_path, presets_path, *, release_path=None, _forwarder=None):
    """Assemble the thin ASGI app (no carrier change, no serving).

    Auth / routing / preset loading are the production objects; the only
    thin-specific piece is the closed-only instance-door handler.
    """
    with open(registry_path, encoding="utf-8") as fh:
        registry = RegistryProvider(json.load(fh))
    return create_external_app(
        registry,
        file_preset_loader(presets_path),
        PresetRoutingBackend(),
        soul_live_handler=build_thin_closed_handler(
            release_path=release_path, _forwarder=_forwarder
        ),
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
    return parser


def _refuse() -> int:
    print(_STARTUP_REFUSAL, file=sys.stderr)
    return 2


def main(argv=None) -> int:
    """Parse, preflight, announce, serve. Returns process exit code."""
    args = build_parser().parse_args(argv)
    if preflight(args.registry, args.presets) != 0:
        return _refuse()
    app = build_thin_app(args.registry, args.presets)
    print(
        "sa thin entry on %s:%s (closed, loopback-only)" % (THIN_HOST, THIN_PORT),
        flush=True,
    )
    uvicorn.run(app, host=THIN_HOST, port=THIN_PORT, reload=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
