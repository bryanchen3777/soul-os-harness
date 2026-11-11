"""SA-3 T3 loop tests: head/tail state machine, no-soul tool loop, clean rejoin.

Decision-complete scope (SA-3): only this file + loop.py + snapshot.py are
new; the proxy gains one additive request-local junction (hooks only, no
generation change); SA-0 / SA-1 / SA-2 / EI-1 untouched.

All test names carry the t3 marker: `pytest -q tests/soul_access -k t3`
must collect > 0. Sync only (no event loop: the SA-0 conftest in this
directory hard-blocks socket creation, and on Windows ANY event-loop
creation needs a socketpair self-pipe, cf. T1 note in test_t1_bridge.py).
Transport is in-process fakes (never a port, never a real LLM call).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.soul_access import loop as sa_loop  # noqa: E402
from src.soul_access import pool as sa_pool  # noqa: E402
from src.soul_access import snapshot as sa_snapshot  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402

OWNER = "t3-loop-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"

_SA3_MODULES = (
    _REPO_ROOT / "src" / "soul_access" / "snapshot.py",
    _REPO_ROOT / "src" / "soul_access" / "loop.py",
)

_TOOLS_PATH = ("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit")


def _identity(**over):
    kw = {
        "request_id": "req-t3-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t3-0001",
        "idempotency_key": "key-t3-0001",
    }
    kw.update(over)
    return sa_schema.RequestIdentity(**kw)


def _dedup(**over):
    kw = {
        "principal_id": OWNER,
        "agent_id": AGENT,
        "scope": SCOPE,
        "conversation_id": "dm",
        "branch_id": "root",
        "idempotency_key": "key-t3-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _live(tag="t3"):
    return sa_snapshot.LiveState(
        personality_version="personality-%s-v1" % tag,
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-%s-v1" % tag,
        projection_state="projection-%s-v1" % tag,
        recall_version="recall-%s-v1" % tag,
        recall_items=("recall-%s-item-1" % tag,),
        history_pool_version="pool-%s-v1" % tag,
        clean_history=("hist-%s-item-1" % tag,),
    )


def _head(**over):
    return sa_snapshot.take_head_snapshot(
        identity=_identity(),
        dedup=_dedup(),
        backend_alias="primary",
        model="do-not-deploy-model-t3",
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        live=_live("head"),
        **over,
    )


def _holder(head):
    return sa_schema.new_holder("sa3-live:req-t3-0001", (sa_schema.snapshot_digest(head),))


def _provenance(head):
    return sa_schema.Provenance(
        created_utc="2026-11-11T17:00:00-05:00",
        snapshot_digest=sa_schema.snapshot_digest(head),
        transitions=("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit"),
    )


def _soul_spies():
    """Soul-side hooks that TOOL_LOOP must never touch (each a record list)."""
    return {"recall": [], "consciousness": [], "judge": [], "write_state": []}


def _assert_soul_silent(spies) -> None:
    for name, calls in spies.items():
        assert calls == [], "SA-3 T3: TOOL_LOOP touched soul hook %r" % (name,)


class _FakeGenerate:
    """Soul generation hook: exactly two calls (gen1 on head, gen2 on tail)."""

    def __init__(self, text1="t3-gen1-plan", text2="t3-gen2-reply"):
        self.text1 = text1
        self.text2 = text2
        self.calls = []

    def __call__(self, snapshot, holder, phase_name):
        self.calls.append((snapshot, holder, phase_name))
        if phase_name == "gen1":
            return self.text1
        if phase_name == "gen2":
            return self.text2
        raise AssertionError("unexpected generate phase %r" % (phase_name,))


def _natural_runner(results):
    """Harness-only tool runner: returns a fixed (kind, content) per handle."""

    def _run(handle_id, harness_context, gen1_text):
        assert isinstance(harness_context, tuple)
        assert isinstance(gen1_text, str) and gen1_text
        kind, content = results[handle_id]
        return (kind, content)

    return _run


def _serve(*, pool=None, head=None, generate=None, runner=None, handles=("h1", "h2"),
           soul=None, tail_live=None, fingerprint="t3-fingerprint-0001"):
    pool = pool if pool is not None else sa_pool.PoolStore()
    head = head if head is not None else _head()
    generate = generate if generate is not None else _FakeGenerate()
    soul = soul if soul is not None else _soul_spies()
    tail_live = tail_live if tail_live is not None else _live("tail")
    if runner is None:
        runner = _natural_runner(
            {hid: ("natural_note", "t3-note-for-%s" % hid) for hid in handles}
        )
    result = sa_loop.run_tool_request(
        snapshot=head,
        provenance=_provenance(head),
        holder=_holder(head),
        pool=pool,
        fingerprint=fingerprint,
        harness_context=("t3-harness-ctx",),
        handle_ids=tuple(handles),
        tool_runner=runner,
        generate=generate,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft(
            "t3-clean-draft",
            tuple(("natural_note", "t3-note-for-%s" % hid) for hid in handles),
        ),
        tail_live=tail_live,
        soul=soul,
        created_utc="2026-11-11T17:00:00-05:00",
    )
    return (pool, result, generate, soul)


# -- T3-0: head/tail snapshots: one live read each, tail carries head cols --


def test_t3_head_tail_snapshots_one_live_read_each():
    head = _head()
    tail = sa_snapshot.take_tail_snapshot(head=head, live=_live("tail"))
    # Tail carries the head columns unchanged ...
    assert tail.identity == head.identity
    assert tail.dedup == head.dedup
    assert tail.backend_alias == head.backend_alias
    assert tail.model == head.model
    assert tail.budget_tokens == head.budget_tokens
    # ... but every live-state column comes from the FRESH read.
    assert tail.inner_life_state == "inner-tail-v1"
    assert head.inner_life_state == "inner-head-v1"
    assert sa_schema.snapshot_digest(tail) != sa_schema.snapshot_digest(head)
    # A tampered head can never seed a tail.
    from dataclasses import replace

    tampered = replace(head, budget_tokens=-1)
    with pytest.raises(sa_schema.SnapshotRejected):
        sa_snapshot.take_tail_snapshot(head=tampered, live=_live("tail"))
    # A mistyped live read is refused (fail-closed, never defaulted).
    with pytest.raises(sa_schema.SnapshotRejected):
        sa_snapshot.take_head_snapshot(
            identity=_identity(),
            dedup=_dedup(),
            backend_alias="primary",
            model="m",
            params=(),
            budget_tokens=512,
            deadline_utc="d",
            live="not-a-live-state",
        )


# -- T3-1: tools path: loads == 2, generations == 2, replay zero new --------


def test_t3_tools_path_two_loads_two_generations():
    soul = _soul_spies()
    generate = _FakeGenerate()
    pool, result, _gen, _soul = _serve(soul=soul, generate=generate)
    assert result.text == "t3-gen2-reply"
    assert result.loads == 2  # HEAD live read + TAIL live read.
    assert result.generation_attempts == 2  # GEN1 + GEN2.
    assert result.tool_provider_calls == 2  # two harness handles, counted apart.
    assert result.replayed is False
    assert result.provenance.transitions == _TOOLS_PATH
    assert len(result.join_evidence) == 2
    # GEN1 saw the head snapshot, GEN2 the fresh tail snapshot.
    phases = [phase for (_snap, _hold, phase) in generate.calls]
    assert phases == ["gen1", "gen2"]
    gen1_snap = generate.calls[0][0]
    gen2_snap = generate.calls[1][0]
    assert gen1_snap.inner_life_state == "inner-head-v1"
    assert gen2_snap.inner_life_state == "inner-tail-v1"
    _assert_soul_silent(soul)


def test_t3_same_key_replay_zero_new():
    pool = sa_pool.PoolStore()
    _pool, first, _gen, _soul = _serve(pool=pool)
    assert (first.loads, first.generation_attempts) == (2, 2)
    _pool, second, generate2, _soul2 = _serve(pool=pool)
    assert second.replayed is True
    assert second.text == first.text  # byte-identical original response.
    assert (second.loads, second.generation_attempts) == (0, 0)  # zero new.
    assert second.tool_provider_calls == 0
    assert generate2.calls == []  # replay executed nothing new.
    assert len(pool) == 1  # no extra rows for the replay.


# -- T3-2: TOOL_LOOP soul separation (spies + static slice + purity) ---------


def test_t3_tool_loop_soul_spies_stay_zero():
    soul = _soul_spies()
    generate = _FakeGenerate()

    def _harness_only(handle_id, harness_context, gen1_text):
        # The runner can SEE only harness data: no soul hook is in reach.
        assert harness_context == ("t3-harness-ctx",)
        assert "soul" not in repr(harness_context)
        return ("natural_summary", "t3-summary-for-%s" % handle_id)

    _serve(soul=soul, generate=generate, runner=_harness_only)
    _assert_soul_silent(soul)
    assert [phase for (_s, _h, phase) in generate.calls] == ["gen1", "gen2"]


def test_t3_tool_loop_section_is_soul_free():
    src = (_REPO_ROOT / "src" / "soul_access" / "loop.py").read_text(encoding="utf-8")
    start_marker = "# -- TOOL_LOOP (no-soul) --"
    end_marker = "# -- TAIL_REJOIN --"
    start = src.index(start_marker) + len(start_marker)
    end = src.index(end_marker)
    block = src[start:end]
    assert "soul" not in block.lower(), "TOOL_LOOP block must never name soul hooks"


def _imported_module_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
                names.add((alias.name or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
                names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.name)
    return mods, names


def test_t3_sa3_modules_make_no_bare_provider_calls():
    for path in _SA3_MODULES:
        mods, names = _imported_module_names(path)
        for banned in (
            "httpx",
            "socket",
            "urllib",
            "subprocess",
            "threading",
            "asyncio",
            "src.interfaces",
            "src.llm",
            "src.external_gateway",
        ):
            assert not any(
                m == banned or m.startswith(banned + ".") for m in mods
            ), (path.name, banned)
        for banned_name in (
            "PresetRoutingBackend",
            "DeepSeekBackend",
            "OllamaBackend",
            "soul_context_adapter",
        ):
            assert banned_name not in names, (path.name, banned_name)
    raw = "".join(p.read_text(encoding="utf-8") for p in _SA3_MODULES)
    for banned in (
        "PresetRoutingBackend",
        "DeepSeekBackend",
        "OllamaBackend",
        "create_connection",
        "urlopen",
        "Popen",
    ):
        assert banned not in raw, banned


# -- T3-3: unjoined TAIL is banned: cancel/timeout/crash/unknown + late -----


def test_t3_cancel_is_not_join_bans_tail_and_isolates():
    pool = sa_pool.PoolStore()
    head = _head()
    generate = _FakeGenerate()
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        _serve(
            pool=pool,
            head=head,
            generate=generate,
            runner=_natural_runner({"h1": ("natural_note", "ok"), "h2": "cancel"}),
        )
    assert exc_info.value.status == 409
    assert exc_info.value.code == "tools_not_joined"
    # GEN1 ran, but TAIL never did: zero gen2 calls.
    assert [phase for (_s, _h, phase) in generate.calls] == ["gen1"]
    # Row quarantined: same-key retry is ISOLATED (ban resend), never replay.
    outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED
    assert outcome.http_status == 409


def test_t3_timeout_crash_unknown_tokens_ban_tail():
    for token in ("timeout", "crash", "weird-shape", None, 42):
        pool = sa_pool.PoolStore()
        head = _head()
        generate = _FakeGenerate()
        with pytest.raises(sa_loop.LoopRejected) as exc_info:
            _serve(
                pool=pool,
                head=head,
                generate=generate,
                runner=_natural_runner({"h1": ("natural_note", "ok"), "h2": token}),
            )
        assert exc_info.value.status == 409, token
        assert exc_info.value.code == "tools_not_joined", token
        assert [phase for (_s, _h, phase) in generate.calls] == ["gen1"], token
        outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
        assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED, token


def test_t3_late_result_after_timeout_stays_unknown():
    loop = sa_loop.ToolLoop(harness_context=("t3-harness-ctx",))
    loop.register("h-late")
    loop.timeout("h-late")
    fut = sa_loop.ControlledFuture()
    fut.set_result(("natural_note", "t3-too-late"))
    # A late result can never revive the settled handle (409, stays UNKNOWN).
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        loop.join("h-late", kind="natural_note", content="t3-too-late")
    assert exc_info.value.status == 409
    assert loop.is_fully_joined is False
    assert loop.join_evidence == ()


def test_t3_no_finally_release():
    src = (_REPO_ROOT / "src" / "soul_access" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    finally_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody]
    assert finally_nodes == [], "SA-3 T3: loop.py must contain zero finally clauses"


# -- T3-4: bidirectional mutation (escalation red / tail-block red / green) -


def test_t3_mutation_midloop_soul_escalation_turns_red():
    """Escalation probe: a runner that calls a soul hook mid-loop is RED."""
    soul = _soul_spies()
    generate = _FakeGenerate()

    def _rogue(handle_id, harness_context, gen1_text):
        soul["recall"].append(("mid-loop-escalation", handle_id))
        return ("natural_note", "t3-rogue-note")

    pool = sa_pool.PoolStore()
    head = _head()
    sa_loop.run_tool_request(
        snapshot=head,
        provenance=_provenance(head),
        holder=_holder(head),
        pool=pool,
        fingerprint="t3-fingerprint-0001",
        harness_context=("t3-harness-ctx",),
        handle_ids=("h1",),
        tool_runner=_rogue,
        generate=generate,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft("t3-draft", ()),
        tail_live=_live("tail"),
        soul=soul,
        created_utc="2026-11-11T17:00:00-05:00",
    )
    # RED: the detector catches the mid-loop soul call ...
    with pytest.raises(AssertionError):
        _assert_soul_silent(soul)
    # ... while the honest runner stays green (same gate, no soul contact).
    soul2 = _soul_spies()
    _serve(soul=soul2)
    _assert_soul_silent(soul2)


def test_t3_mutation_permanent_tail_block_turns_red():
    """Tail-block probe: a gate that always refuses TAIL turns legal RED."""

    def _never_tail(_evidence):
        raise sa_loop.LoopRejected(409, "tools_not_joined", "probe: tail banned")

    pool = sa_pool.PoolStore()
    head = _head()
    generate = _FakeGenerate()
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        sa_loop.run_tool_request(
            snapshot=head,
            provenance=_provenance(head),
            holder=_holder(head),
            pool=pool,
            fingerprint="t3-fingerprint-0001",
            harness_context=("t3-harness-ctx",),
            handle_ids=("h1", "h2"),
            tool_runner=_natural_runner(
                {"h1": ("natural_note", "ok-1"), "h2": ("natural_note", "ok-2")}
            ),
            generate=generate,
            make_tail_draft=_never_tail,
            tail_live=_live("tail"),
            soul=_soul_spies(),
            created_utc="2026-11-11T17:00:00-05:00",
        )
    assert exc_info.value.status == 409  # RED: legal fully-joined request refused.
    assert [phase for (_s, _h, phase) in generate.calls] == ["gen1"]  # no gen2.
    # The blocked row is quarantined (never left half-open for a resend).
    outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED


def test_t3_mutation_reverted_returns_green():
    """Reverted wiring: legal request green again, counters 2/2, soul mute."""
    soul = _soul_spies()
    generate = _FakeGenerate()
    pool, result, _gen, _soul = _serve(soul=soul, generate=generate)
    assert result.text == "t3-gen2-reply"
    assert (result.loads, result.generation_attempts) == (2, 2)
    assert result.tool_provider_calls == 2
    assert result.replayed is False
    _assert_soul_silent(soul)


# -- T3-5: controlled futures: cancel/timeout/crash races, all sync --------


def test_t3_controlled_future_cancel_and_late_race():
    pool = sa_pool.PoolStore()
    head = _head()
    generate = _FakeGenerate()
    fut = sa_loop.ControlledFuture()
    assert fut.cancel() is True

    def _runner(handle_id, harness_context, gen1_text):
        return fut

    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        _serve(pool=pool, head=head, generate=generate, runner=_runner, handles=("h1",))
    assert exc_info.value.status == 409
    # Late completion after settle only marks LATE: the loop stays cancelled.
    fut.set_result(("natural_note", "t3-late"))
    assert fut._late is True
    outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED


def test_t3_controlled_future_exception_is_crash_not_join():
    pool = sa_pool.PoolStore()
    head = _head()
    fut = sa_loop.ControlledFuture()
    fut.set_exception()

    def _runner(handle_id, harness_context, gen1_text):
        return fut

    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        _serve(pool=pool, head=head, runner=_runner, handles=("h1",))
    assert exc_info.value.code == "tools_not_joined"
    outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED


def test_t3_controlled_future_determined_result_joins():
    fut = sa_loop.ControlledFuture()
    fut.set_result(("natural_summary", "t3-future-summary"))
    pool = sa_pool.PoolStore()

    def _runner(handle_id, harness_context, gen1_text):
        return fut

    _pool, result, generate, soul = _serve(
        pool=pool, runner=_runner, handles=("h1",)
    )
    assert (result.loads, result.generation_attempts) == (2, 2)
    assert result.tool_provider_calls == 1
    assert len(result.join_evidence) == 1
    _assert_soul_silent(soul)


# -- T3-6: proxy request-local junction (hooks only, generation untouched) --


def test_t3_proxy_hooks_walk_tools_path_green_and_illegal_red():
    from src.llm import proxy as _proxy

    _proxy.sa3_phase_reset()
    assert _proxy.sa3_phase_current() is None
    with pytest.raises(Exception):
        _proxy.sa3_phase_enter("gen1")  # must start at head_load, never mid-path.
    walked = []
    for phase in ("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit"):
        walked.append(_proxy.sa3_phase_enter(phase, has_tools=True))
    assert walked == list(_TOOLS_PATH)
    assert _proxy.sa3_phase_transitions() == _TOOLS_PATH
    with pytest.raises(Exception):
        _proxy.sa3_phase_enter("gen1", has_tools=True)  # terminal: no restart.
    _proxy.sa3_phase_reset()
    assert _proxy.sa3_phase_current() is None
    assert _proxy.sa3_phase_transitions() == ()


def test_t3_proxy_generation_logic_untouched():
    src = (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # (a) No module-level soul_access import: the junction is lazy (function
    # level), so the module import graph is untouched.
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src.soul_access"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src.soul_access"), node.module
    # (b) No generation function references the junction: hooks exist, but no
    # generation path calls them (generation logic unchanged).
    watched = {
        "LLMProxy",
        "_build_messages_group",
        "_build_messages_private",
        "_parse_llm_output",
    }
    hits = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self):
            self._depth = 0

        def visit_ClassDef(self, node):
            if node.name in watched:
                self._depth += 1
                self.generic_visit(node)
                self._depth -= 1
            else:
                self.generic_visit(node)

        def visit_FunctionDef(self, node):
            if node.name in watched or self._depth > 0:
                self._depth += 1
                self.generic_visit(node)
                self._depth -= 1
            else:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Name(self, node):
            if "sa3_" in node.id and self._depth > 0:
                hits.append(node.id)
            self.generic_visit(node)

        def visit_Attribute(self, node):
            if "sa3_" in node.attr and self._depth > 0:
                hits.append(node.attr)
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert hits == [], hits


# -- T3-7: tail whitelist + provider cost separation + hard blocks ----------


def test_t3_tail_draft_whitelist_refuses_unlisted_natural():
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        sa_loop.build_tail_draft("t3-text", (("natural_evil", "x"),))
    assert exc_info.value.status == 400
    with pytest.raises(sa_loop.LoopRejected):
        sa_loop.build_tail_draft("", ())
    with pytest.raises(sa_loop.LoopRejected):
        sa_loop.build_tail_draft("t3-text", "not-a-tuple")
    draft = sa_loop.build_tail_draft("t3-text", (("natural_note", "n"),))
    assert draft.text == "t3-text"


def test_t3_unlisted_tool_result_never_joins_and_isolates():
    pool = sa_pool.PoolStore()
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        _serve(
            pool=pool,
            runner=_natural_runner({"h1": ("natural_evil", "x")}),
            handles=("h1",),
        )
    assert exc_info.value.code == "tools_not_joined"
    outcome = pool.claim(_dedup(), "t3-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED


def test_t3_provider_cost_not_hidden_in_generation_counters():
    handles = ("h1", "h2", "h3")
    pool, result, _gen, soul = _serve(handles=handles)
    assert result.tool_provider_calls == 3  # mid-loop provider cost, counted apart.
    assert result.generation_attempts == 2  # the two soul generations only.
    assert result.loads == 2  # HEAD + TAIL live reads only.
    _assert_soul_silent(soul)


def test_t3_hard_blocks_stop_network_and_prod_writes(tmp_path):
    # NOTE: same R1/R5 rationale as T1/T2 — this file keeps zero spawn call
    # sites, so the spawn probe is not repeated here (the shared oracle in
    # T0 covers the same conftest block). Socket / kill / prod-write probes
    # below prove the block is active inside T3 tests.
    import os
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa3_t3_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    ok_file = tmp_path / "t3-ok.txt"
    ok_file.write_text("t3", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t3"
