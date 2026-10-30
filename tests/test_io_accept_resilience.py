"""tests/test_io_accept_resilience.py

FEAT-UVICORN-ACCEPT-RESILIENCE - guards for the accept-path resilience patch.

Coverage: acceptance items G1-G9 of the work order, plus the evidence G10 asks
for (population completeness + a synthetic sample that turns the guards red).

* **Population completeness** - this module proves it really scanned the
  target: the public API signature is compared verbatim against the work
  order, the file the implementation is loaded from is asserted, and the G1-G9
  coverage list is asserted to be complete. A guard that scans nothing must not
  be able to pass.
* **Synthetic sample turns red** - every behavioural guard is written as a
  reusable ``_assert_g*`` check, and the same checks are applied to IN-MEMORY
  mutants (the M1-M5 semantics). Every mutant must raise ``AssertionError``,
  which proves the guards have teeth. The repo files stay read-only.

RED LINES (AGENTS.md): zero network, zero real socket, zero listening port,
zero subprocess, zero LLM call. ``accept`` and ``sleep`` are fakes; the only
real event loop is a private ``asyncio.new_event_loop()`` created inside a
test and closed again. Nothing here may bind or probe a production port.
"""

import ast
import asyncio
import inspect
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.io.accept_resilience import (  # noqa: E402
    BENIGN_ACCEPT_WINERRORS,
    INSTALL_MARKER_ATTR,
    install_accept_resilience,
    is_benign_accept_error,
    make_resilient_accept,
)

_LOG = logging.getLogger("soul_os.server.accept_resilience")

#: Baseline captured AT IMPORT TIME - before any test can run (AGENTS.md rule 12:
#: a baseline must be taken before the thing it is compared against happens).
try:  # pragma: no cover - Windows-only seam
    import asyncio.windows_events as _WINDOWS_EVENTS
except ImportError:  # pragma: no cover - non-Windows
    _WINDOWS_EVENTS = None

_BASELINE_IOCP_ACCEPT = (
    getattr(_WINDOWS_EVENTS.IocpProactor, "accept", None) if _WINDOWS_EVENTS else None
)

#: The backoff sequence the work order pins down (G3): 0.05, 0.1, 0.2 ... capped
#: at 1.0.
_EXPECTED_BACKOFF_SEQUENCE = [0.05, 0.1, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0]


# ─────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────


def _oserror(winerror=None, msg="synthetic accept failure"):
    """Build an ``OSError`` that carries (or does not carry) a ``winerror``."""
    if winerror is None:
        return OSError(0, msg)
    try:
        return OSError(0, msg, None, winerror)
    except TypeError:  # pragma: no cover - non-Windows builds
        exc = OSError(winerror, msg)
        exc.winerror = winerror
        return exc


class _FakeListener:
    """Fake listening socket - records whether anybody closed it."""

    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


def _fake_accept(outcomes, limit=50):
    """Return ``(accept_impl, calls)``.

    ``outcomes`` is the sequence of results/exceptions handed out per call
    (the last entry repeats). ``limit`` bounds the number of calls so a mutant
    that retries forever cannot hang the guard.
    """

    calls = []

    async def accept_impl(listener):
        if len(calls) >= limit:
            raise RuntimeError(f"fake accept call limit reached ({limit})")
        calls.append(listener)
        outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return accept_impl, calls


def _fake_sleep():
    """Return ``(sleep, delays)`` - a fake backoff sleep that never waits."""
    delays = []

    async def sleep(delay):
        delays.append(delay)

    return sleep, delays


async def _mimic_start_serving(wrapper, listener):
    """Mimic asyncio/proactor_events.py:840-876 (the upstream accept caller).

    Upstream catches ``OSError`` around the accept and CLOSES the listening
    socket (``sock.close()``) - that is the root cause this work order fixes.
    Returns ``(outcome, value)`` where outcome is:

    * ``"ok"``     - the accept succeeded (no upstream error branch taken)
    * ``"closed"`` - an ``OSError`` escaped and the caller closed the socket
    * ``"raised"`` - a non-``OSError`` escaped
    """
    try:
        result = await wrapper(listener)
    except OSError as exc:
        listener.close()
        return ("closed", exc)
    except BaseException as exc:  # noqa: BLE001 - mirrors upstream's behaviour
        return ("raised", exc)
    return ("ok", result)


def _make_fake_proactor(behaviour=None, with_anchor=True):
    """Return a BRAND NEW fake proactor class (a fresh class per call).

    ``with_anchor=True`` gives the fake an ``accept`` whose source contains the
    version-guard shape anchor, i.e. it looks like the upstream implementation.
    ``with_anchor=False`` gives it an unknown shape (the version guard must
    refuse it).
    """

    if with_anchor:

        class FakeProactor:
            def accept(self, listener):
                # AcceptEx - the shape anchor the version guard looks for.
                if behaviour is None:
                    return ("conn", "addr")
                return behaviour(listener)

    else:

        class FakeProactor:
            def accept(self, listener):
                if behaviour is None:
                    return ("conn", "addr")
                return behaviour(listener)

    return FakeProactor


# ─────────────────────────────────────────────────────────────────────
# reusable guard checks (G1-G7) - shared by the real implementation and the
# in-memory mutants, so the mutants prove the guards have teeth
# ─────────────────────────────────────────────────────────────────────


def _assert_g1(is_benign=is_benign_accept_error):
    """G1 benign classification: only OSError with winerror in {64, 10054}.

    The expected codes are spelled out here on purpose - a guard must never
    derive its expectation from the implementation it is guarding (that would
    let a shrunk whitelist silently redefine "correct").
    """
    for code in (64, 10054):
        assert is_benign(_oserror(code)) is True, f"winerror={code} must be benign"
    for code in (10048, 10013, 100, 9999):
        assert is_benign(_oserror(code)) is False, f"winerror={code} must NOT be benign"
    assert is_benign(OSError(0, "no winerror")) is False, (
        "an OSError without winerror must not be benign"
    )
    assert is_benign(ValueError("not an OSError")) is False, (
        "a non-OSError must not be benign"
    )


def _assert_g2(core_factory):
    """G2 a benign failure must not kill the accept loop."""
    for label, outcomes, min_calls in (
        ("one benign failure then success", [_oserror(64), ("conn", "addr")], 2),
        ("two benign failures then success", [_oserror(64), _oserror(10054), ("conn", "addr")], 3),
    ):
        listener = _FakeListener()
        accept_impl, calls = _fake_accept(outcomes)
        sleep, delays = _fake_sleep()
        wrapper = core_factory(accept_impl=accept_impl, sleep=sleep)
        outcome, result = asyncio.run(_mimic_start_serving(wrapper, listener))
        assert outcome == "ok", (
            f"[{label}] the benign failure must not escape the accept loop (outcome={outcome})"
        )
        assert result == ("conn", "addr"), f"[{label}] unexpected result {result!r}"
        assert len(calls) >= min_calls, (
            f"[{label}] the accept must be re-armed (calls={len(calls)} < {min_calls})"
        )
        assert listener.close_calls == 0, (
            f"[{label}] the listening socket must not be closed"
        )


def _assert_g3(core_factory, failures=8):
    """G3 the backoff grows and is capped."""
    listener = _FakeListener()
    outcomes = [_oserror(10054) for _ in range(failures)] + [("conn", "addr")]
    accept_impl, calls = _fake_accept(outcomes)
    sleep, delays = _fake_sleep()
    wrapper = core_factory(accept_impl=accept_impl, sleep=sleep)
    outcome, result = asyncio.run(_mimic_start_serving(wrapper, listener))
    assert outcome == "ok", f"benign failures must not kill the accept loop (outcome={outcome})"
    assert result == ("conn", "addr")
    assert len(calls) == failures + 1, (
        f"expected {failures + 1} accept calls, got {len(calls)}"
    )
    assert len(delays) == failures, f"expected {failures} backoff sleeps, got {len(delays)}"
    expected = _EXPECTED_BACKOFF_SEQUENCE[:failures]
    assert delays == pytest.approx(expected), (
        f"backoff sequence mismatch: {delays} != {expected}"
    )
    assert max(delays) <= 1.0, "the backoff must be capped at 1.0s"


def _assert_g4(core_factory):
    """G4 a non-benign failure must be re-raised unchanged (never swallowed)."""
    listener = _FakeListener()
    boom = _oserror(10048)
    accept_impl, calls = _fake_accept([boom], limit=5)
    sleep, delays = _fake_sleep()
    wrapper = core_factory(accept_impl=accept_impl, sleep=sleep)
    outcome, exc = asyncio.run(_mimic_start_serving(wrapper, listener))
    assert outcome == "closed", (
        f"a non-benign failure must keep the upstream error path (outcome={outcome})"
    )
    assert exc is boom, "the non-benign exception must be re-raised unchanged"
    assert delays == [], f"a non-benign failure must not be retried (delays={delays})"
    assert len(calls) == 1, f"a non-benign failure must not be retried (calls={len(calls)})"


def _assert_g5(installer, proactor_cls, caplog):
    """G5 the install is idempotent - the wrapper stacks only once."""
    before = proactor_cls.accept
    with caplog.at_level(logging.INFO, logger=_LOG.name):
        first = installer(proactor_cls=proactor_cls, platform="win32", logger=_LOG)
    assert first is True, "the first install must really patch"
    after_first = proactor_cls.accept
    assert after_first is not before, "the first install must replace accept()"

    # Only the SECOND install's records are inspected: the idempotency path
    # must be the reason it is refused (INFO, no ERROR). A second install that
    # is refused by the version guard instead (ERROR) means the idempotency
    # guard is gone - that must not pass as "still idempotent".
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=_LOG.name):
        second = installer(proactor_cls=proactor_cls, platform="win32", logger=_LOG)
    assert second is False, "the second install must be a no-op (returns False)"
    assert proactor_cls.accept is after_first, "the wrapper must stack only once"
    assert any(r.levelno == logging.INFO for r in caplog.records), (
        "the no-op install must be logged as INFO"
    )
    assert not any(r.levelno >= logging.ERROR for r in caplog.records), (
        "the second install must be refused by the idempotency guard (INFO), "
        "not by the version guard (ERROR)"
    )


def _assert_g6(installer, proactor_cls, caplog):
    """G6 the version guard fails closed - an unknown shape is not patched."""
    before = proactor_cls.accept
    with caplog.at_level(logging.ERROR, logger=_LOG.name):
        result = installer(proactor_cls=proactor_cls, platform="win32", logger=_LOG)
    assert result is False, "the version guard must refuse the install"
    assert proactor_cls.accept is before, "the class behaviour must stay unchanged"
    assert not getattr(proactor_cls, INSTALL_MARKER_ATTR, False), (
        "no install marker may be set when the install was refused"
    )
    assert any(r.levelno == logging.ERROR for r in caplog.records), (
        "the refusal must be logged as ERROR"
    )


def _assert_g7(installer, proactor_cls, caplog):
    """G7 non-Windows is a no-op - nothing is patched."""
    before = proactor_cls.accept
    with caplog.at_level(logging.INFO, logger=_LOG.name):
        result = installer(proactor_cls=proactor_cls, platform="linux", logger=_LOG)
    assert result is False, "a non-Windows platform must not install"
    assert proactor_cls.accept is before, "no class may be patched"
    assert not getattr(proactor_cls, INSTALL_MARKER_ATTR, False), (
        "no install marker may be set on a non-Windows platform"
    )
    assert any(r.levelno == logging.INFO for r in caplog.records), (
        "the no-op must be logged as INFO"
    )


def _assert_g8():
    """G8 the wiring is unchanged and the install precedes uvicorn.run."""
    source = (REPO_ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    uvicorn_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
    ]
    assert len(uvicorn_calls) == 1, (
        f"there must be exactly one uvicorn.run call, found {len(uvicorn_calls)}"
    )
    call = uvicorn_calls[0]

    # arguments / order / position must be byte-identical
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Name), (
        "uvicorn.run must take app as its only positional argument"
    )
    assert call.args[0].id == "app", "uvicorn.run must take app as its only positional argument"
    assert [kw.arg for kw in call.keywords] == ["host", "port", "reload", "log_level"], (
        "the uvicorn.run keyword names/order must not change"
    )
    values = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
    assert values["host"] == "0.0.0.0", "uvicorn.run host must not change"
    assert values["port"] == 8000, "uvicorn.run port must not change"
    assert values["reload"] is False, "uvicorn.run reload must not change"
    assert values["log_level"] == "info", "uvicorn.run log_level must not change"

    # the install call must live inside the __main__ block, before uvicorn.run
    main_blocks = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "__main__"
        ):
            main_blocks.append(node)
    assert len(main_blocks) == 1, "there must be exactly one __main__ block"
    main = main_blocks[0]

    def _is_install_call(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "install_accept_resilience"
        )

    install_calls = [node for node in ast.walk(main) if _is_install_call(node)]
    assert len(install_calls) == 1, (
        "install_accept_resilience() must be called exactly once inside __main__"
    )
    assert install_calls[0].lineno < call.lineno, (
        "install_accept_resilience() must come before uvicorn.run(...)"
    )

    imports = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.ImportFrom) and node.module == "src.io.accept_resilience"
    ]
    assert len(imports) == 1, "the accept_resilience import must live inside __main__"

    # module level must stay clean (no patch side effect on import)
    assert not [node for node in tree.body if _is_install_call(node)], (
        "there must be no install call at module level"
    )
    assert not [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "src.io.accept_resilience"
    ], "the accept_resilience import must not live at module level"


# ─────────────────────────────────────────────────────────────────────
# in-memory mutants (M1-M7 semantics) - used to prove the guards have teeth
# ─────────────────────────────────────────────────────────────────────


def _mutant_m1_no_retry(
    *,
    accept_impl,
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,
):
    """M1 semantics: a benign failure is raised as-is (no re-arm)."""

    async def wrapper(listener):
        return await accept_impl(listener)

    return wrapper


def _mutant_m2_retry_once(
    *,
    accept_impl,
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,
):
    """M2 semantics: re-arm once, then give up."""

    async def wrapper(listener):
        backoff = initial_backoff
        retried = False
        while True:
            try:
                return await accept_impl(listener)
            except BaseException as exc:  # noqa: BLE001
                if not is_benign(exc) or retried:
                    raise
                retried = True
                await sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    return wrapper


def _mutant_m3_no_backoff_cap(
    *,
    accept_impl,
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,
):
    """M3 semantics: the backoff cap is removed (unbounded growth)."""

    async def wrapper(listener):
        backoff = initial_backoff
        while True:
            try:
                return await accept_impl(listener)
            except BaseException as exc:  # noqa: BLE001
                if not is_benign(exc):
                    raise
                await sleep(backoff)
                backoff = backoff * 2

    return wrapper


def _mutant_m4_all_oserror_benign(
    *,
    accept_impl,
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,
):
    """M4 semantics: the criterion is widened - every OSError counts as benign."""

    def widened(exc):
        return isinstance(exc, OSError)

    async def wrapper(listener):
        backoff = initial_backoff
        while True:
            try:
                return await accept_impl(listener)
            except BaseException as exc:  # noqa: BLE001
                if not widened(exc):
                    raise
                await sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    return wrapper


def _mutant_m5_without_10054(exc):
    """M5 semantics: 10054 is dropped from the whitelist."""
    if not isinstance(exc, OSError):
        return False
    return getattr(exc, "winerror", None) == 64


def _mutant_installer_without_version_guard(*, proactor_cls=None, platform=None, logger=None):
    """M6 semantics: the version guard is removed - an unknown shape is patched."""

    original = proactor_cls.accept

    def patched(self, listener):
        return original(self, listener)

    proactor_cls.accept = patched
    return True


def _mutant_installer_without_idempotency(*, proactor_cls=None, platform=None, logger=None):
    """M7 semantics: the idempotency guard is removed - a second install stacks."""

    original = proactor_cls.accept

    def patched(self, listener):
        return original(self, listener)

    proactor_cls.accept = patched
    return True


# ─────────────────────────────────────────────────────────────────────
# G1 - benign classification
# ─────────────────────────────────────────────────────────────────────


def test_g1_benign_error_classification():
    """G1: winerror 64 / 10054 are benign; everything else is not."""
    _assert_g1()
    assert BENIGN_ACCEPT_WINERRORS == frozenset({64, 10054}), (
        "the whitelist must stay exactly {64, 10054}"
    )


# ─────────────────────────────────────────────────────────────────────
# G2 - a benign failure must not kill the accept loop
# ─────────────────────────────────────────────────────────────────────


def test_g2_benign_failure_does_not_kill_the_accept_loop():
    """G2: the benign failure never escapes, the listener stays open, accept is re-armed."""
    _assert_g2(make_resilient_accept)


# ─────────────────────────────────────────────────────────────────────
# G3 - the backoff grows and is capped
# ─────────────────────────────────────────────────────────────────────


def test_g3_backoff_grows_and_is_capped():
    """G3: sleep sequence 0.05, 0.1, 0.2 ... capped at 1.0."""
    _assert_g3(make_resilient_accept)


# ─────────────────────────────────────────────────────────────────────
# G4 - a non-benign failure must be re-raised unchanged
# ─────────────────────────────────────────────────────────────────────


def test_g4_non_benign_failure_is_raised_unchanged():
    """G4: the criterion is never widened - other OSErrors keep the upstream path."""
    _assert_g4(make_resilient_accept)


# ─────────────────────────────────────────────────────────────────────
# G5 - the install is idempotent
# ─────────────────────────────────────────────────────────────────────


def test_g5_install_is_idempotent(caplog):
    """G5: a second install is a no-op and the wrapper stacks only once."""
    _assert_g5(install_accept_resilience, _make_fake_proactor(), caplog)


def test_g5_installed_accept_returns_a_future_and_rearms(caplog):
    """G5 (installed seam): the patched accept returns a Future and re-arms after a benign failure."""
    calls = []
    loop = asyncio.new_event_loop()
    try:

        def behaviour(listener):
            calls.append(listener)
            if len(calls) == 1:
                raise _oserror(64)
            fut = loop.create_future()
            fut.set_result(("conn", "addr"))
            return fut

        cls = _make_fake_proactor(behaviour=behaviour)
        assert install_accept_resilience(proactor_cls=cls, platform="win32", logger=_LOG) is True
        proactor = cls()
        proactor._loop = loop
        listener = _FakeListener()
        with caplog.at_level(logging.WARNING, logger=_LOG.name):
            fut = proactor.accept(listener)
            assert isinstance(fut, asyncio.Future), (
                "the patched accept must return a Future (upstream calls add_done_callback on it)"
            )
            result = loop.run_until_complete(fut)
        assert result == ("conn", "addr"), f"unexpected result {result!r}"
        assert len(calls) == 2, f"the accept must be re-armed after a benign failure (calls={len(calls)})"
        assert listener.close_calls == 0, "the listening socket must not be closed"

        # work order 2 (g): every benign failure leaves a WARNING trail that
        # carries the winerror, the backoff and the consecutive failure count
        # (the post-mortest evidence the maintenance window greps for).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "a benign failure must be logged as WARNING"
        message = warnings[0].getMessage()
        assert "winerror=64" in message, f"the WARNING must carry the winerror: {message}"
        assert "backoff=0.050" in message, f"the WARNING must carry the backoff: {message}"
        assert "consecutive=1" in message, f"the WARNING must carry the consecutive count: {message}"
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────
# G6 - the version guard fails closed
# ─────────────────────────────────────────────────────────────────────


def test_g6_version_guard_refuses_an_unknown_shape(caplog):
    """G6: an accept implementation without the shape anchor is refused, unchanged."""
    _assert_g6(install_accept_resilience, _make_fake_proactor(with_anchor=False), caplog)


# ─────────────────────────────────────────────────────────────────────
# G7 - non-Windows is a no-op
# ─────────────────────────────────────────────────────────────────────


def test_g7_non_windows_is_a_noop(caplog):
    """G7: on a non-Windows platform nothing is patched and the result is False."""
    _assert_g7(install_accept_resilience, _make_fake_proactor(), caplog)


# ─────────────────────────────────────────────────────────────────────
# G8 - the wiring is unchanged
# ─────────────────────────────────────────────────────────────────────


def test_g8_run_server_wiring_is_unchanged():
    """G8: uvicorn.run is byte-identical and the install call precedes it, inside __main__."""
    _assert_g8()


# ─────────────────────────────────────────────────────────────────────
# G9 - the existing guards stay green (no side effects, stdlib only)
# ─────────────────────────────────────────────────────────────────────


def test_g9_import_has_no_patch_side_effect():
    """G9: importing the module must not patch IocpProactor (module level stays clean)."""
    if _WINDOWS_EVENTS is None:  # pragma: no cover - non-Windows
        pytest.skip("Windows-only seam: asyncio.windows_events is not importable")
    assert _WINDOWS_EVENTS.IocpProactor.accept is _BASELINE_IOCP_ACCEPT, (
        "importing accept_resilience must not touch IocpProactor.accept"
    )
    assert not getattr(_WINDOWS_EVENTS.IocpProactor, INSTALL_MARKER_ATTR, False), (
        "importing accept_resilience must not set the install marker"
    )


def test_g9_module_uses_stdlib_only():
    """G9: no third-party dependency may be introduced."""
    source = (REPO_ROOT / "src" / "io" / "accept_resilience.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {"asyncio", "inspect", "logging", "sys", "typing", "__future__"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    assert imported <= allowed, f"unexpected imports: {sorted(imported - allowed)}"


# ─────────────────────────────────────────────────────────────────────
# G10 - population completeness + synthetic samples turn the guards red
# ─────────────────────────────────────────────────────────────────────


def test_guard_population_is_complete():
    """G10 (population completeness): the guards really point at the target.

    Without this, a renamed module or a hollowed-out guard could pass green.
    """
    sig = inspect.signature(make_resilient_accept)
    assert list(sig.parameters) == [
        "accept_impl",
        "is_benign",
        "sleep",
        "initial_backoff",
        "max_backoff",
        "on_benign",
    ], f"the public signature drifted: {list(sig.parameters)}"
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values()), (
        "make_resilient_accept must stay keyword-only"
    )
    assert sig.parameters["is_benign"].default is is_benign_accept_error
    assert sig.parameters["sleep"].default is asyncio.sleep
    assert sig.parameters["initial_backoff"].default == 0.05
    assert sig.parameters["max_backoff"].default == 1.0
    assert sig.parameters["on_benign"].default is None
    assert BENIGN_ACCEPT_WINERRORS == frozenset({64, 10054})

    install_sig = inspect.signature(install_accept_resilience)
    assert list(install_sig.parameters) == ["proactor_cls", "platform", "logger"], (
        f"the installer signature drifted: {list(install_sig.parameters)}"
    )

    # the guarded implementation must be the production file, not a copy
    impl_path = Path(make_resilient_accept.__code__.co_filename).resolve()
    assert impl_path == (REPO_ROOT / "src" / "io" / "accept_resilience.py").resolve(), (
        f"the guard points at {impl_path}, not the production module"
    )

    # the G1-G9 coverage must be complete (no vacuous green light)
    module = sys.modules[__name__]
    covered = set()
    for name in dir(module):
        if not name.startswith("test_g"):
            continue
        rest = name[len("test_g"):]
        if rest[:1].isdigit():
            covered.add(int(rest.split("_")[0]))
    assert covered == set(range(1, 10)), f"G1-G9 coverage incomplete: {sorted(covered)}"


def test_guard_synthetic_violations_turn_red(caplog):
    """G10 (synthetic sample): in-memory mutants must turn the guards red.

    Every mutant mirrors the work order's mutation table; the repo files stay
    read-only - only in-memory copies are used.
    """
    # M1: a benign failure is raised as-is -> G2 must go red
    with pytest.raises(AssertionError):
        _assert_g2(_mutant_m1_no_retry)

    # M2: re-armed only once -> G2 must go red
    with pytest.raises(AssertionError):
        _assert_g2(_mutant_m2_retry_once)

    # M3: the backoff cap removed -> G3 must go red
    with pytest.raises(AssertionError):
        _assert_g3(_mutant_m3_no_backoff_cap)

    # M4: the criterion widened to every OSError -> G4 must go red
    with pytest.raises(AssertionError):
        _assert_g4(_mutant_m4_all_oserror_benign)

    # M5: 10054 dropped from the whitelist -> G1 must go red
    with pytest.raises(AssertionError):
        _assert_g1(_mutant_m5_without_10054)

    # M6: the version guard removed -> G6 must go red
    with pytest.raises(AssertionError):
        _assert_g6(
            _mutant_installer_without_version_guard,
            _make_fake_proactor(with_anchor=False),
            caplog,
        )

    # M7: the idempotency guard removed -> G5 must go red
    with pytest.raises(AssertionError):
        _assert_g5(
            _mutant_installer_without_idempotency,
            _make_fake_proactor(),
            caplog,
        )
