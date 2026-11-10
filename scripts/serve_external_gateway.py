#!/usr/bin/env python3
"""S7-W5 outward gateway carrier: trusted-LAN HTTP on the frozen port.

Serves the S6 assembly (``create_external_app``) with the S7 real carrier
(``PresetRoutingBackend``: per-call dispatch to ollama / deepseek over the
repo's own httpx + uvicorn -- no new third-party dependency).

S2 frozen: trusted-LAN plaintext only, no TLS. No watchdog, no schtasks, no
autostart. Importing this module has zero side effects: no env reads, no
file reads, no port attach, no serving. Every effect lives in ``main()``
behind ``__main__`` (same isolation idiom as ``scripts/run_server.py``).

Fail-closed startup (exit 2, one fixed copy, nothing else printed):
  - missing registry / preset file, or broken JSON;
  - registry text carries the template marker ``do-not-deploy``
    (refuses to serve the example registry as production).
Preset keys are NOT checked here: ``load_preset`` reads the environment on
each call, per the S6 semantic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import uvicorn

from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import PresetRoutingBackend
from src.interfaces.external_auth import RegistryProvider


#: The only startup refusal copy (fixed; carries no paths, no secrets).
_STARTUP_REFUSAL = "external gateway: startup refused."


def build_parser() -> argparse.ArgumentParser:
    """CLI shape: registry + presets required, LAN host, frozen port."""
    parser = argparse.ArgumentParser(
        description="Serve the outward OpenAI-shaped gateway on the trusted LAN."
    )
    parser.add_argument("--registry", required=True, help="Registry JSON file.")
    parser.add_argument("--presets", required=True, help="Preset JSON file.")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="LAN bind host (default: %(default)s; trusted-LAN plaintext, no TLS).",
    )
    parser.add_argument(
        "--port",
        default=EXTERNAL_PORT,
        type=int,
        help="Bind port (default: %(default)s; frozen).",
    )
    return parser


def _refuse() -> int:
    print(_STARTUP_REFUSAL, file=sys.stderr)
    return 2


def main(argv=None) -> int:
    """Parse, fail-closed checks, announce, serve. Returns process exit code."""
    args = build_parser().parse_args(argv)
    try:
        registry_text = Path(args.registry).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return _refuse()
    try:
        registry_raw = json.loads(registry_text)
    except (ValueError, TypeError):
        return _refuse()
    if "do-not-deploy" in registry_text:
        return _refuse()
    try:
        presets_text = Path(args.presets).read_text(encoding="utf-8")
        json.loads(presets_text)
    except (OSError, ValueError, TypeError):
        return _refuse()
    registry = RegistryProvider(registry_raw)
    app = create_external_app(
        registry, file_preset_loader(args.presets), PresetRoutingBackend()
    )
    print(
        "external gateway on %s:%s (trusted-LAN plaintext, no TLS)"
        % (args.host, args.port),
        flush=True,
    )
    uvicorn.run(app, host=args.host, port=args.port, reload=False, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
