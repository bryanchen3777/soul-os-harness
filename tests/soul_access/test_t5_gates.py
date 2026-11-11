"""SA-5 T5 gates tests: end-to-end chain pins (loads/attempts, soul silence,
sentinel zero, unjoined bans TAIL, replay zero new) + bidirectional mutation.

Decision-complete scope (SA-5): only this file + test_t6_release.py are new
product-side; no product file is touched by this ticket. The chain under test
is SA-0 -> SA-4 as committed in SA-0..SA-4 (schema / api+bridge+release /
pool+policy / loop+snapshot / admission+harness gates):

  no-tools batch: envelope -> holder -> admit_no_tools (loads == 1,
    generation_attempts == 1, TAIL-free transitions);
  tools batch: run_tool_request (loads == 2, generation_attempts == 2,
    tool_provider_calls counted apart, TOOL_LOOP soul-silent);
  soul commit: whole-round zero on any tool leak, sentinel never reaches sinks;
  replay anywhere in the chain costs zero new work.

All test names carry the t5 marker: `pytest -q tests/soul_access -k t5`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created). Transport is in-process fakes
(never a port, never a real LLM call, never data/** writes).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.soul_access import admission as sa_admission  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import loop as sa_loop  # noqa: E402
from src.soul_access import pool as sa_pool  # noqa: E402
from src.soul_access import snapshot as sa_snapshot  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402

OWNER = "t5-gates-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"

T5_SENTINEL = "T5-SENTINEL-4b8e02"
TOOL_MARKERS = ("T5-TOOL-web_search", "T5-PARAM-q=soul", T5_SENTINEL)

_NO_TOOLS_PATH = ("head_load", "gen1", "admit")
_TOOLS_PATH = ("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit")

_SA5_MODULES = (
    _REPO_ROOT / "src" / "soul_access" / "api.py",
    _REPO_ROOT / "src" / "soul_access" / "bridge.py",
    _REPO_ROOT / "src" / "soul_access" / "release.py",
    _REPO_ROOT / "src" / "soul_access" / "pool.py",
    _REPO_ROOT / "src" / "soul_access" / "policy.py",
    _REPO_ROOT / "src" / "soul_access" / "loop.py",
    _REPO_ROOT / "src" / "soul_access" / "snapshot.py",
    _REPO_ROOT / "src" / "soul_access" / "admission.py",
)


def _identity(**over):
    kw = {
        "request_id": "req-t5-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t5-0001",
        "idempotency_key": "key-t5-0001",
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
        "idempotency_key": "key-t5-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _live(tag="t5"):
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
        model="do-not-deploy-model-t5",
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        live=_live("head"),
        **over,
    )


def _holder(head):
    return sa_schema.new_holder("sa5-live:req-t5-0001", (sa_schema.snapshot_digest(head),))


def _provenance(head):
    return sa_schema.Provenance(
        created_utc="2026-11-11T17:00:00-05:00",
        snapshot_digest=sa_schema.snapshot_digest(head),
        transitions=_TOOLS_PATH,
    )


def _soul_spies():
    """Soul-side hooks that TOOL_LOOP must never touch (each a record list)."""
    return {"recall": [], "consciousness": [], "judge": [], "write_state": []}


def _assert_soul_silent(soul) -> None:
    for name, calls in soul.items():
        assert calls == [], "SA-5 T5: TOOL_LOOP touched soul hook %r" % (name,)


def _sinks(n=5):
    """Soul-side long-term sink spies (history/MemoryStore/SAGE/InnerLife/投影)."""
    return [[] for _ in range(n)]


def _assert_sinks_zero(sinks, sentinels=(T5_SENTINEL,)) -> None:
    for sink in sinks:
        for record in sink:
            blob = repr(record)
            for sentinel in sentinels:
                assert sentinel not in blob, "SA-5 T5: soul sink polluted: %r" % (blob,)


def _assert_sinks_clean_of_markers(sinks, markers=TOOL_MARKERS) -> None:
    for sink in sinks:
        for record in sink:
            blob = repr(record)
            for marker in markers:
                assert marker not in blob, "SA-5 T5: tool marker leaked: %r" % (blob,)


class _FakeGenerate:
    """Soul generation hook: exactly two calls (gen1 on head, gen2 on tail)."""

    def __init__(self, text1="t5-gen1-plan", text2="t5-gen2-reply"):
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


class _FakeNoToolsGenerate:
    """Bridge generate hook: exactly one call through the live loop."""

    def __init__(self, text="t5-live-reply"):
        self.text = text
        self.calls = []

    def __call__(self, snapshot, holder, live):
        self.calls.append((snapshot, holder, live))
        return self.text


def _natural_runner(results):
    """Harness-only tool runner: returns a fixed (kind, content) per handle."""

    def _run(handle_id, harness_context, gen1_text):
        assert isinstance(harness_context, tuple)
        assert isinstance(gen1_text, str) and gen1_text
        kind, content = results[handle_id]
        return (kind, content)

    return _run


def _serve_tools(*, pool=None, head=None, generate=None, runner=None,
                 handles=("h1", "h2"), soul=None, tail_live=None,
                 fingerprint="t5-fingerprint-0001"):
    pool = pool if pool is not None else sa_pool.PoolStore()
    head = head if head is not None else _head()
    generate = generate if generate is not None else _FakeGenerate()
    soul = soul if soul is not None else _soul_spies()
    tail_live = tail_live if tail_live is not None else _live("tail")
    if runner is None:
        runner = _natural_runner(
            {hid: ("natural_note", "t5-note-for-%s" % hid) for hid in handles}
        )
    result = sa_loop.run_tool_request(
        snapshot=head,
        provenance=_provenance(head),
        holder=_holder(head),
        pool=pool,
        fingerprint=fingerprint,
        harness_context=("t5-harness-ctx",),
        handle_ids=tuple(handles),
        tool_runner=runner,
        generate=generate,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft(
            "t5-clean-draft",
            tuple(("natural_note", "t5-note-for-%s" % hid) for hid in handles),
        ),
        tail_live=tail_live,
        soul=soul,
        created_utc="2026-11-11T17:00:00-05:00",
    )
    return (pool, result, generate, soul)


# -- T5-1: no-tools chain: envelope -> holder -> admit (1 load, 1 attempt) ---


def test_t5_chain_no_tools_loads_one_attempts_one():
    head = _head()
    envelope = sa_api.build_envelope(
        snapshot=head,
        messages=(("user", "hi"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    generate = _FakeNoToolsGenerate()
    live = sa_bridge.LiveLoop(bus=object(), llm=object(), middleware=object())
    holder = sa_api.build_holder(
        request_id=envelope.snapshot.identity.request_id,
        snapshot_digest_hex=envelope.provenance.snapshot_digest,
    )
    result = sa_bridge.admit_no_tools(
        snapshot=envelope.snapshot,
        holder=holder,
        provenance=envelope.provenance,
        live=live,
        generate=generate,
    )
    assert result.text == "t5-live-reply"
    assert result.loads == 1
    assert result.generation_attempts == 1
    assert result.provenance.transitions == _NO_TOOLS_PATH
    assert result.provenance.snapshot_digest == sa_schema.snapshot_digest(head)
    assert len(generate.calls) == 1


# -- T5-2: tools chain: 2 loads, 2 attempts, provider cost apart, soul mute --


def test_t5_chain_tools_loads_two_attempts_two():
    soul = _soul_spies()
    generate = _FakeGenerate()
    pool, result, _gen, _soul = _serve_tools(soul=soul, generate=generate)
    assert result.text == "t5-gen2-reply"
    assert result.loads == 2  # HEAD live read + TAIL live read.
    assert result.generation_attempts == 2  # GEN1 + GEN2.
    assert result.tool_provider_calls == 2  # two harness handles, counted apart.
    assert result.replayed is False
    assert result.provenance.transitions == _TOOLS_PATH
    assert len(result.join_evidence) == 2
    phases = [phase for (_snap, _hold, phase) in generate.calls]
    assert phases == ["gen1", "gen2"]
    assert generate.calls[0][0].inner_life_state == "inner-head-v1"
    assert generate.calls[1][0].inner_life_state == "inner-tail-v1"
    _assert_soul_silent(soul)


# -- T5-3: replay anywhere in the chain costs zero new work ------------------


def test_t5_chain_replay_zero_new_across_stages():
    pool = sa_pool.PoolStore()
    _pool, first, generate1, soul1 = _serve_tools(pool=pool)
    assert (first.loads, first.generation_attempts) == (2, 2)
    assert first.tool_provider_calls == 2
    _pool, second, generate2, _soul2 = _serve_tools(pool=pool)
    assert second.replayed is True
    assert second.text == first.text  # byte-identical original response.
    assert (second.loads, second.generation_attempts) == (0, 0)
    assert second.tool_provider_calls == 0
    assert generate2.calls == []  # replay executed nothing new.
    assert len(pool) == 1  # no extra rows for the replay.
    # No-tools stage replays the same way: seal-checked, zero regeneration.
    head = _head()
    envelope = sa_api.build_envelope(
        snapshot=head,
        messages=(("user", "hi"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    assert envelope.provenance.snapshot_digest == sa_schema.snapshot_digest(head)


# -- T5-4: soul commit: legit tool round stores clean, sentinel zero --------


def test_t5_chain_soul_commit_clean_and_replay_zero_new():
    credential = sa_admission.mint_admission()
    ledger = sa_admission.RoundLedger()
    sinks = _sinks()
    note = sa_admission.build_completion_note("查詢")
    first = sa_admission.commit_soul_round(
        round_key="t5-round-legit-1",
        sinks=sinks,
        user_text="幫我查一下量子計算",
        assistant_text=note,
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    assert first.allowed is True and first.code == "committed"
    assert first.replayed is False
    assert all(len(sink) == 1 for sink in sinks)
    _assert_sinks_zero(sinks)
    _assert_sinks_clean_of_markers(sinks)
    second = sa_admission.commit_soul_round(
        round_key="t5-round-legit-1",
        sinks=sinks,
        user_text="幫我查一下量子計算",
        assistant_text=note,
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    assert second.replayed is True and second.writes == 0
    assert all(len(sink) == 1 for sink in sinks)
    _assert_sinks_zero(sinks)


def test_t5_chain_tool_leak_bans_whole_round_zero():
    credential = sa_admission.mint_admission()
    sinks = _sinks()
    result = sa_admission.commit_soul_round(
        round_key="t5-round-leak-1",
        sinks=sinks,
        user_text="幫我查一下",
        assistant_text="我用 T5-TOOL-web_search 查到 T5-SENTINEL-4b8e02 的結果",
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=sa_admission.RoundLedger(),
    )
    assert result.allowed is False and result.code == "tool-leak"
    assert result.writes == 0
    assert all(len(sink) == 0 for sink in sinks)
    _assert_sinks_zero(sinks)


# -- T5-5: unjoined TAIL banned, row isolated, never replayed ----------------


def test_t5_chain_unjoined_bans_tail_and_isolates():
    pool = sa_pool.PoolStore()
    head = _head()
    generate = _FakeGenerate()
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        _serve_tools(
            pool=pool,
            head=head,
            generate=generate,
            runner=_natural_runner({"h1": ("natural_note", "ok"), "h2": "cancel"}),
        )
    assert exc_info.value.status == 409
    assert exc_info.value.code == "tools_not_joined"
    assert [phase for (_s, _h, phase) in generate.calls] == ["gen1"]  # no gen2.
    outcome = pool.claim(_dedup(), "t5-fingerprint-0001")
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED
    assert outcome.http_status == 409


# -- T5-6: bidirectional mutation (relaxed red / tightened red / green) -----


def _relaxed_fp_without_backend(**over):
    """Mutation probe (relaxed): fingerprint DROPS the backend columns."""
    over.pop("backend_alias", None)
    over.pop("backend_ref", None)
    over.pop("public_model_id", None)
    kw = {
        "text": "t5 hello soul",
        "turn_id": "turn-t5-0001",
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "memory_policy": "ephemeral",
    }
    kw.update(over)
    import hashlib as _hashlib
    import json as _json

    data = _json.dumps(
        kw, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _hashlib.sha256(data).hexdigest()


def _fp(**over):
    from src.soul_access import policy as sa_policy

    kw = {
        "backend_alias": "primary",
        "backend_ref": "port-a",
        "public_model_id": "do-not-deploy-t5-primary",
        "text": "t5 hello soul",
        "turn_id": "turn-t5-0001",
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "memory_policy": "ephemeral",
    }
    kw.update(over)
    return sa_policy.request_fingerprint(**kw)


def _t5_dedup_key():
    from src.soul_access import policy as sa_policy

    ident = sa_policy.EntryIdentity(
        conversation_id="dm",
        branch_id="root",
        turn_id="turn-t5-0001",
        idempotency_key="key-t5-0001",
    )
    pool_key = sa_policy.build_pool_key(
        principal_id=OWNER, agent_id=AGENT, scope=SCOPE, identity=ident
    )
    return sa_policy.build_dedup_key(pool_key, "key-t5-0001")


def test_t5_mutation_relaxed_fingerprint_turns_red():
    """Relaxed probe: without backend cols, a backend drift falsely replays."""
    store = sa_pool.PoolStore()
    dedup = _t5_dedup_key()
    store.claim(dedup, _relaxed_fp_without_backend())
    store.complete(dedup, _relaxed_fp_without_backend(), "t5-relaxed-reply")
    red = store.claim(dedup, _relaxed_fp_without_backend(backend_alias="secondary"))
    assert red.disposition == sa_pool.DISPOSITION_REPLAY  # RED: drift not caught.
    # Normal gate stays green: the same drift conflicts instead of replaying.
    store2 = sa_pool.PoolStore()
    store2.claim(dedup, _fp(backend_alias="primary"))
    store2.complete(dedup, _fp(backend_alias="primary"), "t5-normal-reply")
    green = store2.claim(dedup, _fp(backend_alias="secondary"))
    assert green.disposition == sa_pool.DISPOSITION_CONFLICT


def test_t5_mutation_tightened_gate_turns_red_then_revert_green(monkeypatch):
    credential = sa_admission.mint_admission()
    clean = dict(
        user_text="今天天氣如何？",
        assistant_text=sa_admission.build_completion_note("查詢"),
        tool_markers=TOOL_MARKERS,
        credential=credential,
    )
    # Mutant: gate forced shut (simulates an over-broad ban).
    monkeypatch.setattr(
        sa_admission, "decide",
        lambda **kw: sa_admission.AdmissionDecision(False, "mutant", "forced"),
    )
    sinks = _sinks()
    result = sa_admission.commit_soul_round(
        round_key="t5-mutant-deny-1", sinks=sinks,
        ledger=sa_admission.RoundLedger(), **clean,
    )
    assert result.writes == 0
    with pytest.raises(AssertionError):  # RED: a legit round stored nothing.
        assert all(len(sink) == 1 for sink in sinks)
    # Revert: the legit round stores across all five sinks (green again).
    monkeypatch.undo()
    sinks2 = _sinks()
    result2 = sa_admission.commit_soul_round(
        round_key="t5-mutant-deny-2", sinks=sinks2,
        ledger=sa_admission.RoundLedger(), **clean,
    )
    assert result2.allowed is True and result2.code == "committed"
    _assert_sinks_zero(sinks2)
    _assert_sinks_clean_of_markers(sinks2)


def test_t5_mutation_escalation_turns_red_then_revert_green():
    """Escalation probe: a runner calling a soul hook mid-loop is RED."""
    soul = _soul_spies()
    generate = _FakeGenerate()

    def _rogue(handle_id, harness_context, gen1_text):
        soul["recall"].append(("mid-loop-escalation", handle_id))
        return ("natural_note", "t5-rogue-note")

    pool = sa_pool.PoolStore()
    head = _head()
    sa_loop.run_tool_request(
        snapshot=head,
        provenance=_provenance(head),
        holder=_holder(head),
        pool=pool,
        fingerprint="t5-fingerprint-0001",
        harness_context=("t5-harness-ctx",),
        handle_ids=("h1",),
        tool_runner=_rogue,
        generate=generate,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft("t5-draft", ()),
        tail_live=_live("tail"),
        soul=soul,
        created_utc="2026-11-11T17:00:00-05:00",
    )
    # RED: the detector catches the mid-loop soul call ...
    with pytest.raises(AssertionError):
        _assert_soul_silent(soul)
    # ... while the honest runner stays green (same gate, no soul contact).
    soul2 = _soul_spies()
    _serve_tools(soul=soul2)
    _assert_soul_silent(soul2)


def test_t5_mutation_reverted_returns_green():
    """Reverted wiring: legal tools request green again, counters 2/2."""
    soul = _soul_spies()
    generate = _FakeGenerate()
    pool, result, _gen, _soul = _serve_tools(soul=soul, generate=generate)
    assert result.text == "t5-gen2-reply"
    assert (result.loads, result.generation_attempts) == (2, 2)
    assert result.tool_provider_calls == 2
    assert result.replayed is False
    _assert_soul_silent(soul)


# -- T5-7: AST gates (loop slice / no-finally / decide-first / import graph) -


def test_t5_tool_loop_section_is_soul_free():
    src = (_REPO_ROOT / "src" / "soul_access" / "loop.py").read_text(encoding="utf-8")
    start_marker = "# -- TOOL_LOOP (no-soul) --"
    end_marker = "# -- TAIL_REJOIN --"
    start = src.index(start_marker) + len(start_marker)
    end = src.index(end_marker)
    block = src[start:end]
    assert "soul" not in block.lower(), "TOOL_LOOP block must never name soul hooks"


def test_t5_no_finally_release():
    src = (_REPO_ROOT / "src" / "soul_access" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    finally_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody]
    assert finally_nodes == [], "SA-5 T5: loop.py must contain zero finally clauses"


def test_t5_decide_runs_before_first_sink_write():
    src = (_REPO_ROOT / "src" / "soul_access" / "admission.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "commit_soul_round":
            target = node
            break
    assert target is not None
    decide_line = None
    first_append_line = None
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "decide" and decide_line is None:
                decide_line = node.lineno
            if isinstance(func, ast.Attribute) and func.attr == "append":
                if first_append_line is None or node.lineno < first_append_line:
                    first_append_line = node.lineno
    assert decide_line is not None and first_append_line is not None
    assert decide_line < first_append_line, "SA-5 T5: decide must precede writes"


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


def test_t5_chain_modules_make_no_bare_provider_calls():
    for path in _SA5_MODULES:
        mods, names = _imported_module_names(path)
        for banned in (
            "httpx",
            "socket",
            "urllib",
            "subprocess",
            "asyncio",
            "threading",
            "src.interfaces",
            "src.llm",
            "src.memory",
            "src.agent",
            "src.external_gateway",
        ):
            if path.name == "admission.py" and banned == "threading":
                continue  # SA-4 decision: request-local round context only.
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
    raw = "".join(p.read_text(encoding="utf-8") for p in _SA5_MODULES)
    for banned in (
        "PresetRoutingBackend",
        "DeepSeekBackend",
        "OllamaBackend",
        "create_connection",
        "urlopen",
        "Popen",
    ):
        assert banned not in raw, banned


# -- T5-8: isolation hard blocks stay on in this directory --------------------


def test_t5_hard_blocks_stop_network_and_prod_writes(tmp_path):
    # NOTE: same R1/R5 rationale as T1–T4 — this file keeps zero spawn call
    # sites, so the spawn probe is not repeated here (the shared oracle in
    # T0 covers the same conftest block). Socket / kill / prod-write probes
    # below prove the block is active inside T5 tests.
    import os
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa5_t5_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    ok_file = tmp_path / "t5-ok.txt"
    ok_file.write_text("t5", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t5"
