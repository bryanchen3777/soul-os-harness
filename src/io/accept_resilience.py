"""FEAT-UVICORN-ACCEPT-RESILIENCE - Windows accept path resilience.

Why this module exists (root cause already read in CPython 3.11.15 source by the
main brain on 2026-10-29; do not re-derive it)::

    asyncio/proactor_events.py:861
        except OSError as exc:
            if sock.fileno() != -1:
                ...
                sock.close()            # <- closes the LISTENING socket
            ...
        else:
            self._accept_futures[sock.fileno()] = f
            f.add_done_callback(loop)   # <- only the else branch re-arms accept

One benign network noise (``WinError 64`` / ``WinError 10054``) therefore:
  (a) removes the listening socket from the OS LISTEN table -> the watchdog sees
      ``port_listen=False`` -> brutal restart -> the running schedule slot is cut;
  (b) stops the accept loop from re-arming -> even an open socket stops serving.

This module intercepts benign failures at the ``asyncio.windows_events.
IocpProactor.accept`` seam (the accept implementation shared by the Windows
Proactor **and** Selector event loops). The exception is caught **inside** the
seam and never propagates, so upstream's ``sock.close()`` branch is never
reached. Instead we log a WARNING, back off, and **re-arm accept** on the same
listener socket - the listener socket stays open for the whole time.

Design rules (mapped to the work order acceptance items G1-G9):

* Pure, injectable core: ``make_resilient_accept`` depends only on the injected
  ``accept_impl`` and ``sleep`` - no real socket, no real event loop.
* The criterion is never widened: only ``OSError`` with
  ``winerror in {64, 10054}`` is benign. Every other ``OSError`` (e.g.
  ``10048`` address-in-use, ``10013`` permission denied) is re-raised unchanged.
* Fail closed: before installing, the source of ``IocpProactor.accept`` must
  contain ``AcceptEx``. If the upstream shape changed we refuse to install and
  log an ERROR - "rather lose the feature than behave wrongly".
* Idempotent: a second install is a no-op (returns False, logs INFO).
* Non-Windows: a no-op (returns False, patches nothing, logs INFO).
* stdlib only - no third-party dependency.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from typing import Any, Awaitable, Callable, Optional

__all__ = [
    "BENIGN_ACCEPT_WINERRORS",
    "INSTALL_MARKER_ATTR",
    "is_benign_accept_error",
    "make_resilient_accept",
    "install_accept_resilience",
]

#: Whitelist of benign Windows error codes on the accept path.
#:
#: - ``64``    = ERROR_NETNAME_DELETED (the network name cannot be used)
#: - ``10054`` = ERROR_CONNECTION_RESET (connection reset by peer)
#:
#: This is a WHITELIST: any ``OSError`` not listed here (including ``10048``
#: address-already-in-use and ``10013`` permission-denied) is re-raised
#: unchanged - never swallowed, never widened.
BENIGN_ACCEPT_WINERRORS = frozenset({64, 10054})

#: Marker attribute set on the patched class - the idempotency guard reads it.
INSTALL_MARKER_ATTR = "_soul_os_accept_resilience_installed"

#: Per-proactor-instance cache attribute for the resilient wrapper.
_RESILIENT_CACHE_ATTR = "_soul_os_resilient_accept"

#: Shape anchor for the version guard: the upstream ``IocpProactor.accept``
#: source must contain this token, otherwise the patch is refused.
_ACCEPT_SHAPE_ANCHOR = "AcceptEx"

#: Backoff parameters (work order section 2 (c)): start at 0.05s, double each
#: time, capped at 1.0s - no busy-spin, no unbounded growth.
_INITIAL_BACKOFF = 0.05
_MAX_BACKOFF = 1.0

_LOGGER = logging.getLogger("soul_os.server.accept_resilience")


def is_benign_accept_error(exc: BaseException) -> bool:
    """True only when *exc* is an ``OSError`` whose ``winerror`` is benign.

    An ``OSError`` without a ``winerror`` attribute (or with a non-benign one)
    is NOT benign.
    """
    if not isinstance(exc, OSError):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror is None:
        return False
    try:
        return int(winerror) in BENIGN_ACCEPT_WINERRORS
    except (TypeError, ValueError):
        return False


def make_resilient_accept(
    *,
    accept_impl,
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,
) -> Callable[[Any], Awaitable[tuple]]:
    """Build an accept wrapper that survives benign accept failures.

    The returned callable takes the listener and returns an awaitable resolving
    to the upstream ``(conn, addr)`` tuple:

    * call ``accept_impl(listener)`` and await it;
    * a benign failure -> ``sleep(backoff)`` -> retry (backoff doubles, capped
      at ``max_backoff``); the listener socket is never closed and the failure
      never escapes to the caller;
    * a non-benign failure -> re-raised unchanged (never swallowed).

    ``on_benign(exc, backoff, consecutive)`` is an optional observer called once
    per benign failure - used for the post-mortem WARNING trail.
    """

    async def resilient_accept(listener):
        backoff = initial_backoff
        consecutive = 0
        while True:
            try:
                return await accept_impl(listener)
            except BaseException as exc:  # noqa: BLE001 - benign or not is decided by is_benign
                if not is_benign(exc):
                    # Not benign: re-raise unchanged so upstream keeps its own
                    # error handling (including closing the socket).
                    raise
                consecutive += 1
                if on_benign is not None:
                    try:
                        on_benign(exc, backoff, consecutive)
                    except Exception as observer_exc:  # pragma: no cover - observer must never break accept
                        _LOGGER.error(
                            "[AcceptResilience] on_benign observer failed: %r",
                            observer_exc,
                        )
                await sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    return resilient_accept


def install_accept_resilience(
    *,
    proactor_cls=None,
    platform: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> bool:
    """Install the accept-path resilience patch on ``IocpProactor.accept``.

    Returns True when the patch was installed by THIS call, False when nothing
    was installed (non-Windows platform, already installed, or the version guard
    refused). All keyword arguments exist so the behaviour can be exercised
    without touching the real asyncio class.
    """
    plat = sys.platform if platform is None else platform
    log = _LOGGER if logger is None else logger

    # (e) Non-Windows: a no-op - no class is patched.
    if plat != "win32":
        log.info(
            "[AcceptResilience] platform=%r is not win32 - accept resilience not installed (no-op)",
            plat,
        )
        return False

    if proactor_cls is None:
        try:
            from asyncio import windows_events as _windows_events
        except Exception as exc:  # pragma: no cover - asyncio.windows_events is stdlib on Windows
            log.error(
                "[AcceptResilience] asyncio.windows_events unavailable (%r) - refusing to install",
                exc,
            )
            return False
        proactor_cls = getattr(_windows_events, "IocpProactor", None)
        if proactor_cls is None:
            log.error(
                "[AcceptResilience] asyncio.windows_events.IocpProactor is missing - refusing to install"
            )
            return False

    # (d) Idempotent: a second install must not stack another wrapper.
    if getattr(proactor_cls, INSTALL_MARKER_ATTR, False):
        log.info(
            "[AcceptResilience] already installed on %s.accept - no-op",
            getattr(proactor_cls, "__name__", proactor_cls),
        )
        return False

    original_accept = getattr(proactor_cls, "accept", None)
    if original_accept is None:
        log.error(
            "[AcceptResilience] %s has no accept() - refusing to install",
            getattr(proactor_cls, "__name__", proactor_cls),
        )
        return False

    # (f) Version guard, fail closed: the upstream accept implementation must
    # still look like the one we know how to wrap.
    source = _read_source(original_accept)
    if source is None or _ACCEPT_SHAPE_ANCHOR not in source:
        log.error(
            "[AcceptResilience] version guard failed: %s.accept source does not contain %r "
            "- refusing to install (fail-closed)",
            getattr(proactor_cls, "__name__", proactor_cls),
            _ACCEPT_SHAPE_ANCHOR,
        )
        return False

    proactor_cls.accept = _build_installed_accept(original_accept, log)
    setattr(proactor_cls, INSTALL_MARKER_ATTR, True)
    log.info(
        "[AcceptResilience] installed on %s.accept (benign winerrors=%s, backoff=%s..%s)",
        getattr(proactor_cls, "__name__", proactor_cls),
        sorted(BENIGN_ACCEPT_WINERRORS),
        _INITIAL_BACKOFF,
        _MAX_BACKOFF,
    )
    return True


def _read_source(func: Any) -> Optional[str]:
    """Source of *func*, or None when it cannot be read."""
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return None


def _make_benign_observer(logger: logging.Logger) -> Callable[[BaseException, float, int], None]:
    """Observer that emits the post-mortem WARNING trail (work order 2 (g))."""

    def on_benign(exc: BaseException, backoff: float, consecutive: int) -> None:
        logger.warning(
            "[AcceptResilience] benign accept failure winerror=%s backoff=%.3fs consecutive=%d "
            "- listener socket kept open, re-arming accept",
            getattr(exc, "winerror", None),
            backoff,
            consecutive,
        )

    return on_benign


def _resilient_accept_for(proactor: Any, original_accept: Callable[..., Any], logger: logging.Logger):
    """Per-instance resilient wrapper (cached, so it is built only once)."""

    cached = getattr(proactor, _RESILIENT_CACHE_ATTR, None)
    if cached is not None:
        return cached

    def accept_impl(listener):
        return original_accept(proactor, listener)

    resilient = make_resilient_accept(
        accept_impl=accept_impl,
        is_benign=is_benign_accept_error,
        sleep=asyncio.sleep,
        initial_backoff=_INITIAL_BACKOFF,
        max_backoff=_MAX_BACKOFF,
        on_benign=_make_benign_observer(logger),
    )
    try:
        setattr(proactor, _RESILIENT_CACHE_ATTR, resilient)
    except Exception:  # pragma: no cover - instances that forbid attributes are simply not cached
        pass
    return resilient


def _build_installed_accept(original_accept: Callable[..., Any], logger: logging.Logger) -> Callable[..., Any]:
    """Wrap the upstream accept with the resilient retry logic.

    The wrapper keeps the upstream method shape ``(self, listener)`` so that
    ``self._proactor.accept(sock)`` needs no change on the caller side, and it
    still returns a **Future** - upstream's ``_start_serving`` calls
    ``add_done_callback`` and ``result()`` on it, so the accept loop semantics
    are preserved.
    """

    def installed_accept(self, listener):
        loop = self._loop
        resilient = _resilient_accept_for(self, original_accept, logger)
        fut = loop.create_future()
        task = loop.create_task(resilient(listener))

        def _on_task_done(t):
            if fut.cancelled():
                return
            if t.cancelled():
                fut.cancel()
                return
            exc = t.exception()
            if exc is not None:
                fut.set_exception(exc)
            else:
                fut.set_result(t.result())

        def _on_fut_done(f):
            # The outer future was cancelled (e.g. loop shutdown) -> stop the
            # inner retry task as well.
            if f.cancelled():
                task.cancel()

        task.add_done_callback(_on_task_done)
        fut.add_done_callback(_on_fut_done)
        return fut

    installed_accept.__name__ = "accept"
    installed_accept.__qualname__ = getattr(original_accept, "__qualname__", "accept")
    installed_accept.__module__ = getattr(original_accept, "__module__", __name__)
    installed_accept.__doc__ = (
        "FEAT-UVICORN-ACCEPT-RESILIENCE: accept wrapper - benign WinError is backed off "
        "and the accept is re-armed instead of closing the listening socket."
    )
    setattr(installed_accept, "__soul_os_accept_resilience__", True)
    return installed_accept
