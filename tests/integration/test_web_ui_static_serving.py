"""TEST-INFRA-ROOT-SCRIPTS-1 (D2) — the *served* Web UI, verified fully in-process.

Why this file exists
--------------------
The root-level ``test_ui.py`` (archived verbatim to
``docs/legacy/root-scripts/test_ui.py.txt``) was the only place in the repo that
asserted "the page the service actually serves is the real UI, not the fallback
``DEMO_HTML`` blob". It paid for that coverage with a real HTTP connection to an
externally running service on the **production port**, so it could never run as
an ordinary test — and its own history (module-level ``pkill -f run_server`` plus
a module-level second-server spawn) is the incident this whole ticket is about.
Its WS assertion was also stale: it demanded the literal legacy URL in the served
page, while the real page has assembled the WebSocket URL dynamically for a long
time.

This replacement keeps the *value* and drops the *landmine*:

  * the app is built **in this process** from ``src.io.gateway.IOGateway`` — the
    object that actually owns the ``GET /`` route — and is driven through
    ``httpx.ASGITransport``: ASGI in-process, no socket, no port, no uvicorn, no
    subprocess, nothing that could touch a running service;
  * ``scripts/run_server.py`` is deliberately **not** imported. Importing it is a
    production start-up path, not a library seam: it installs a process-wide
    fatal faulthandler against the *production* data root. ``IOGateway`` is the
    library-level seam that serves the page, and it is what this file uses.
  * the requests go to a non-resolvable sentinel host, so a real network client
    could not have produced the responses asserted here.

No process is started or stopped, and no service address is ever contacted.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: RFC 6761 ``.invalid`` — guaranteed not to resolve. Used as the ASGI ``base_url``
#: so that this file cannot silently become a real network probe.
SENTINEL_BASE_URL = "http://soul-os-app.invalid"

#: Markers that exist only in the ``DEMO_HTML`` fallback (``src/io/gateway.py``).
#: If the served page carries any of these, the real ``static/index.html`` was not
#: served and the UI silently degraded to the demo blob.
DEMO_FALLBACK_MARKERS = ("Soul OS — Live Feed", "Soul OS — Live")

#: The real page's ``<title>`` (``static/index.html``).
REAL_TITLE_PREFIX = "Soul OS"


def _legacy_hardcoded_service_url() -> str:
    """The old hard-coded WS URL, *assembled from parts*.

    Deliberately not written as a literal: ``tests/infra``'s guard bans
    service-address literals anywhere under ``tests/**``, and this module must not
    be an exception to the rule it relies on. The point of the assertion is that
    this string must NOT appear in the served page.
    """
    return f"{'ws'}://{'local' + 'host'}:{8 * 1000}/ws"


def _served_html(path: str = "/") -> str:
    """GET ``path`` from the in-process ASGI app. Starts nothing, connects nowhere."""
    from fastapi import FastAPI

    from src.eventbus import SoulEventBus
    from src.io.gateway import IOGateway

    # Constructor only: SoulEventBus spawns its worker task in ``start()``, which
    # is never called here. IOGateway.__init__ only wires the routes.
    bus = SoulEventBus()
    gateway = IOGateway(bus=bus, app=FastAPI(title="Soul OS Gateway (in-process test)"))

    async def _get() -> httpx.Response:
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(transport=transport, base_url=SENTINEL_BASE_URL) as client:
            return await client.get(path)

    return asyncio.run(_get()).text


# ───────────────────────────────────────────────────────────
# What the service serves
# ───────────────────────────────────────────────────────────


def test_served_root_is_real_static_ui_not_demo_fallback():
    """``GET /`` must serve the real UI, never the ``DEMO_HTML`` fallback blob."""
    html = _served_html()
    assert html, "GET / 回傳空內容"
    present = [m for m in DEMO_FALLBACK_MARKERS if m in html]
    assert not present, (
        f"served page 帶有 DEMO_HTML fallback 專屬標記 {present} → "
        "static/index.html 沒有被服務（服務已回落 demo blob）"
    )


def test_served_page_title_is_soul_os():
    """The served page must carry the real ``<title>`` (contains ``Soul OS``)."""
    html = _served_html()
    match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    assert match, "served page 沒有 <title>"
    title = match.group(1).strip()
    assert REAL_TITLE_PREFIX in title, f"served <title>={title!r} 不含 {REAL_TITLE_PREFIX!r}"


def test_served_page_assembles_websocket_url_dynamically():
    """The WS URL must be assembled at runtime from the page's own host.

    This is the corrected form of the old root script's intent: the endpoint must
    be advertised, but derived from ``window.location.host`` rather than pinned.
    """
    html = _served_html()
    assert "window.location.host" in html, (
        "served page 未以 window.location.host 動態組裝 WS URL"
    )
    assert "/ws" in html, "served page 未提及 /ws 端點"


def test_served_page_contains_no_hardcoded_service_address():
    """No pinned service address may exist in the served page."""
    html = _served_html()
    legacy = _legacy_hardcoded_service_url()
    assert legacy not in html, f"served page 含硬寫 WS 位址 {legacy!r}"


def test_served_page_is_the_on_disk_static_index():
    """``GET /`` re-reads ``static/index.html``; the served bytes must be that file."""
    index = REPO_ROOT / "static" / "index.html"
    assert index.exists(), f"缺少 {index}"
    assert _served_html() == index.read_text(encoding="utf-8"), (
        "served page 與 static/index.html 不一致（路由未服務真實 UI）"
    )


# ───────────────────────────────────────────────────────────
# Proof that this file cannot itself become a live probe
# ───────────────────────────────────────────────────────────

#: Spawn / network entry points this file must never call.
_BANNED_CALL_HEADS = ("subprocess", "uvicorn", "socket")


def test_this_module_starts_no_process_and_no_server():
    """AST self-check: this file must stay incapable of spawning a service.

    Deliberately structural rather than "we promise": a future edit that adds a
    subprocess spawn, ``uvicorn.run`` or a raw socket fails right here.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                name = f"{func.value.id}.{func.attr}"
            elif isinstance(func, ast.Name):
                name = func.id
            if name and name.split(".")[0] in _BANNED_CALL_HEADS:
                offenders.append((node.lineno, name))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for mod in names + mods:
                if mod.split(".")[0] in _BANNED_CALL_HEADS:
                    offenders.append((node.lineno, f"import {mod}"))

    assert not offenders, (
        "本檔不得 spawn 任何行程/伺服器（AST 自我檢查）: "
        + ", ".join(f"L{ln}:{nm}" for ln, nm in offenders)
    )


def test_served_html_really_came_from_the_in_process_asgi_app():
    """Sanity: the ASGI transport + FastAPI app are the real serving path."""
    from fastapi import FastAPI

    from src.eventbus import SoulEventBus
    from src.io.gateway import IOGateway

    bus = SoulEventBus()
    assert bus._worker_task is None and bus._running is False, (
        "建構 SoulEventBus() 不得啟動 worker（本檔不得啟動任何背景服務）"
    )
    gateway = IOGateway(bus=bus, app=FastAPI())
    assert isinstance(gateway.app, FastAPI)
    assert gateway.bus is bus
    # The route under test exists on the in-process app (no server needed).
    paths = {getattr(r, "path", None) for r in gateway.app.routes}
    assert "/" in paths, f"IOGateway 未註冊 GET / 路由: {sorted(p for p in paths if p)}"


if __name__ == "__main__":  # pragma: no cover - manual convenience only
    raise SystemExit(pytest.main([__file__, "-q"]))
