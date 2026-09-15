#!/usr/bin/env python3
"""UI static-HTML smoke test — opt-in live probe against an ALREADY-RUNNING service.

TEST-INFRA-UI-PKILL (P2) — what was removed, and why
====================================================
This file used to run, at *module level* (i.e. on every import / test collection)::

    # Kill existing
    subprocess.run(['pkill', '-f', 'run_server'], capture_output=True)

``pkill -f run_server`` is a blind, command-line-pattern kill: it matches ANY process
whose command line mentions ``run_server`` — including the production service — and
nothing about it was scoped to this test. Because the call sat at module level it also
fired on mere *import* (e.g. a bare ``pytest`` run at the repo root), not only on
explicit execution. It only looked harmless because Windows normally has no ``pkill``
on PATH; with git-bash / MSYS on PATH it would kill production on import.

The same module-level body also spawned a *second* production server on :8000 and
inherited the production data root, so it was unsafe to import even without ``pkill``.

This file now:
  * has NO import-time side effects (safe to import / collect),
  * starts NO server and kills NO process — it owns no child process at all,
  * is SKIPPED by default, because it is only meaningful against a service that is
    already running externally and whose lifecycle this test does not own.
    Skipping is explicit opt-in via ``SOUL_OS_UI_LIVE=1``.

Original intent, preserved
--------------------------
The original script checked that the page served at ``http://localhost:8000/`` is the
REAL static UI (it contains the ``與 Yua`` marker) rather than the fallback
``DEMO_HTML`` blob, and that it exposes the ``ws://localhost:8000/ws`` endpoint.
Both checks are preserved below — upgraded from ``print`` output to real assertions.

Usage
-----
Opt in (a service must already be up; nothing is started or stopped)::

    $env:SOUL_OS_UI_LIVE = '1'
    .venv\\Scripts\\python.exe -m pytest test_ui.py -q

Manual run (same read-only probe, prints a report, exit code 0/1)::

    .venv\\Scripts\\python.exe test_ui.py
"""
from __future__ import annotations

import os
import urllib.request

import pytest

#: Probe target. Only this one URL is read; no state is mutated.
UI_URL = os.environ.get("SOUL_OS_UI_URL", "http://localhost:8000/")

#: Marker that only the real static UI has (the fallback DEMO_HTML does not).
REAL_UI_MARKER = "與 Yua"

#: WebSocket endpoint that the served page must advertise.
WS_MARKER = "ws://localhost:8000/ws"

#: Explicit opt-in. Unset ⇒ skip: this test cannot own the service lifecycle.
LIVE = os.environ.get("SOUL_OS_UI_LIVE") == "1"

pytestmark = pytest.mark.skipif(
    not LIVE,
    reason=(
        "需要「外部已在運行」的 UI 服務（預設 http://localhost:8000/）；本測試不掌管該服務的"
        "生命週期，且依 TEST-INFRA-UI-PKILL 規範不得啟動或終止任何進程，故預設跳過。"
        "設 SOUL_OS_UI_LIVE=1 可啟用（服務需先自行啟動）。"
    ),
)


def fetch_ui_html(url: str = UI_URL, timeout: float = 5.0) -> str:
    """GET the page and decode it. Read-only: never starts or kills anything."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed localhost probe
        return resp.read().decode("utf-8", errors="replace")


def test_served_page_is_real_static_ui_not_fallback_demo_html():
    """The served HTML must be the real UI, not the fallback ``DEMO_HTML`` blob."""
    content = fetch_ui_html()
    assert content, f"{UI_URL} 回傳空內容"
    assert REAL_UI_MARKER in content, (
        f"served HTML 缺少 {REAL_UI_MARKER!r} → 服務可能回落到 fallback DEMO_HTML; "
        f"前 200 字: {content[:200]!r}"
    )


def test_served_page_exposes_websocket_endpoint():
    """The served HTML must still advertise the WS endpoint the UI connects to."""
    content = fetch_ui_html()
    assert WS_MARKER in content, f"served HTML 缺少 WebSocket 端點 {WS_MARKER!r}"


def _manual_probe() -> int:
    """Manual report mode (``python test_ui.py``). Returns a process exit code."""
    print(f"Probing {UI_URL} (read-only; no server is started or stopped)")
    try:
        content = fetch_ui_html()
    except Exception as exc:  # pragma: no cover - manual path
        print(f"❌ Error: {exc!r}")
        print("   服務必須已經在運行；本腳本不會代為啟動。")
        return 1

    print("Length:", len(content))
    print(f"Has {REAL_UI_MARKER}:", REAL_UI_MARKER in content)
    print(f"Has {WS_MARKER}:", WS_MARKER in content)
    if REAL_UI_MARKER in content:
        print("✅ SUCCESS - real static HTML served!")
        return 0
    print("❌ Using fallback DEMO_HTML")
    print("First 200 chars:", content[:200])
    return 1


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(_manual_probe())
