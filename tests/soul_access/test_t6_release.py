"""SA-5 T6 release tests: sandbox fault matrix for the thin-entry release gate.

Decision-complete scope (SA-5): this file pins the SA-1 release gate plus its
two route evidences. No product file is touched by this ticket.

Release contract under test (``src/soul_access/release.py`` + the
``build_live_handler`` closure in ``src/soul_access/bridge.py``):

  open file ({"status": "open"}) -> new inbound admitted;
  closed file / missing file / unreadable / bad JSON / non-dict payload /
  any status != "open" -> new inbound refused fail-closed with
  LiveRefused(403, "forbidden") (actual frozen product behaviour; the ticket
  text saying "503" is a wording drift recorded in verification/soul-access/
  and left for the mastermind ruling -- this file pins the real 403).

  close -> reopen never resurrects an UNKNOWN isolation row: a quarantined
  pool row stays ISOLATED (409) however the gate flips; only a fresh key
  flows again once the gate reopens.

  two batches keep distinct route evidence: the no-tools batch travels
  head_load -> gen1 -> admit (loads == 1, attempts == 1) while the tools
  batch travels head_load -> gen1 -> tool_loop -> tail_rejoin -> gen2 ->
  admit (loads == 2, attempts == 2).

All test names carry the t6 marker: `pytest -q tests/soul_access -k t6`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created). The release file under test is
always a self-created tmp file (never data/**, never in-place writes).
Zero subprocess call sites in this file (R1/R5 rationale: the spawn probe
lives in the T0 shared oracle).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import loop as sa_loop  # noqa: E402
from src.soul_access import pool as sa_pool  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import snapshot as sa_snapshot  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402

OWNER = "t6-release-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"

_NO_TOOLS_PATH = ("head_load", "gen1", "admit")
_TOOLS_PATH = ("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit")


def _identity(**over):
    kw = {
        "request_id": "req-t6-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t6-0001",
        "idempotency_key": "key-t6-0001",
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
        "idempotency_key": "key-t6-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _live(tag="t6"):
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
        model="do-not-deploy-model-t6",
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        live=_live("head"),
        **over,
    )


def _envelope(head=None, tag="t6"):
    head = head if head is not None else _head()
    return sa_api.build_envelope(
        snapshot=head,
        messages=(("user", "t6 hello soul %s" % tag),),
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _live_loop():
    return sa_bridge.LiveLoop(bus=object(), llm=object(), middleware=object())


def _write_release(path: Path, payload) -> Path:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _refuse_code(exc) -> tuple:
    return (exc.status, exc.code)


# -- T6-1: sandbox fault matrix (open admits, every fault refuses 403) -------


def test_t6_release_open_admits_new_inbound(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    assert sa_release.is_live_inbound_open(release_path) is True
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-live-reply",
        release_path=release_path,
    )
    assert handler(_envelope()) == "t6-live-reply"


def test_t6_release_closed_refuses_403(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    assert sa_release.is_live_inbound_open(release_path) is False
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-must-not-leak",
        release_path=release_path,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")


@pytest.mark.parametrize(
    "payload",
    [
        "not-json{{{",
        "[1, 2, 3]",  # non-dict JSON.
        '{"status": "OPEN"}',  # wrong case: only "open" admits.
        '{"status": ""}',
        '{"other": 1}',  # missing status.
    ],
)
def test_t6_release_fault_content_refuses_403(tmp_path, payload):
    release_path = _write_release(tmp_path / "release.json", payload)
    assert sa_release.is_live_inbound_open(release_path) is False
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-must-not-leak",
        release_path=release_path,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")


def test_t6_release_missing_file_refuses_403(tmp_path):
    release_path = tmp_path / "release-missing.json"
    assert not release_path.exists()
    assert sa_release.is_live_inbound_open(release_path) is False
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-must-not-leak",
        release_path=release_path,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")


def test_t6_release_hot_read_no_restart(tmp_path):
    """Flipping the file takes effect on the very next call (hot-read)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-live-reply",
        release_path=release_path,
    )
    assert handler(_envelope()) == "t6-live-reply"
    _write_release(release_path, {"status": "closed"})
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    _write_release(release_path, {"status": "open"})
    assert handler(_envelope()) == "t6-live-reply"


# -- T6-2: close/reopen never resurrects an UNKNOWN isolation row ------------


def test_t6_release_reopen_does_not_resurrect_unknown_row(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    pool = sa_pool.PoolStore()
    head = _head()
    generate_calls = []

    def _generate(snapshot, holder, phase_name):
        generate_calls.append(phase_name)
        return "t6-%s" % phase_name

    cancelled = sa_loop.ControlledFuture()
    assert cancelled.cancel() is True

    def _runner(handle_id, harness_context, gen1_text):
        return cancelled

    # Drive one tools request whose handle cancels: unjoined -> quarantine.
    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        sa_loop.run_tool_request(
            snapshot=head,
            provenance=sa_schema.Provenance(
                created_utc="2026-11-11T17:00:00-05:00",
                snapshot_digest=sa_schema.snapshot_digest(head),
                transitions=_TOOLS_PATH,
            ),
            holder=sa_schema.new_holder(
                "sa6-live:req-t6-0001", (sa_schema.snapshot_digest(head),)
            ),
            pool=pool,
            fingerprint="t6-fingerprint-quarantine",
            harness_context=("t6-harness-ctx",),
            handle_ids=("h1",),
            tool_runner=_runner,
            generate=_generate,
            make_tail_draft=lambda evidence: sa_loop.build_tail_draft("t6-draft", ()),
            tail_live=_live("tail"),
            created_utc="2026-11-11T17:00:00-05:00",
        )
    assert exc_info.value.status == 409
    # Flip the gate closed -> open: the row must stay ISOLATED either way.
    _write_release(release_path, {"status": "closed"})
    assert sa_release.is_live_inbound_open(release_path) is False
    closed_claim = pool.claim(_dedup(), "t6-fingerprint-quarantine")
    assert closed_claim.disposition == sa_pool.DISPOSITION_ISOLATED
    assert closed_claim.http_status == 409
    _write_release(release_path, {"status": "open"})
    assert sa_release.is_live_inbound_open(release_path) is True
    reopened_claim = pool.claim(_dedup(), "t6-fingerprint-quarantine")
    assert reopened_claim.disposition == sa_pool.DISPOSITION_ISOLATED
    assert reopened_claim.http_status == 409
    # Only a fresh key flows again once the gate reopens (no resurrection,
    # no blanket block either).
    fresh = pool.claim(_dedup(idempotency_key="key-t6-fresh"), "t6-fp-fresh")
    assert fresh.disposition == sa_pool.DISPOSITION_NEW


# -- T6-3: two batches keep distinct route evidence --------------------------


def test_t6_two_batches_route_evidence_distinct():
    head = _head()
    holder = sa_schema.new_holder(
        "sa6-live:req-t6-0001", (sa_schema.snapshot_digest(head),)
    )
    no_tools = sa_bridge.admit_no_tools(
        snapshot=head,
        holder=holder,
        provenance=sa_schema.Provenance(
            created_utc="2026-11-11T17:00:00-05:00",
            snapshot_digest=sa_schema.snapshot_digest(head),
            transitions=_NO_TOOLS_PATH,
        ),
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-no-tools-reply",
    )
    assert no_tools.text == "t6-no-tools-reply"
    assert (no_tools.loads, no_tools.generation_attempts) == (1, 1)
    assert tuple(no_tools.provenance.transitions) == _NO_TOOLS_PATH

    pool = sa_pool.PoolStore()
    gen_calls = []

    def _generate(snapshot, holder, phase_name):
        gen_calls.append(phase_name)
        return "t6-%s" % phase_name

    tools = sa_loop.run_tool_request(
        snapshot=head,
        provenance=sa_schema.Provenance(
            created_utc="2026-11-11T17:00:00-05:00",
            snapshot_digest=sa_schema.snapshot_digest(head),
            transitions=_TOOLS_PATH,
        ),
        holder=sa_schema.new_holder(
            "sa6-live:req-t6-0002", (sa_schema.snapshot_digest(head),)
        ),
        pool=pool,
        fingerprint="t6-fingerprint-tools",
        harness_context=("t6-harness-ctx",),
        handle_ids=("h1",),
        tool_runner=lambda hid, ctx, gen1: ("natural_note", "t6-note"),
        generate=_generate,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft(
            "t6-clean-draft", (("natural_note", "t6-note"),)
        ),
        tail_live=_live("tail"),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    assert tools.text == "t6-gen2"
    assert (tools.loads, tools.generation_attempts) == (2, 2)
    assert tuple(tools.provenance.transitions) == _TOOLS_PATH
    assert tools.join_evidence != ()
    # Distinct routes: the tools batch travelled strictly more phases.
    assert set(no_tools.provenance.transitions) < set(tools.provenance.transitions)


# -- T6-4: bidirectional mutation (bypass red / forced-open red / green) ----


def test_t6_mutation_gate_bypass_turns_red_then_revert_green(tmp_path):
    """Relaxed mutant: a handler that skips the release check admits while
    closed (RED). The honest handler on the same closed file refuses (green).
    """
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})

    def _bypass_handler(envelope):
        holder = sa_api.build_holder(
            request_id=envelope.snapshot.identity.request_id,
            snapshot_digest_hex=envelope.provenance.snapshot_digest,
        )
        result = sa_bridge.admit_no_tools(
            snapshot=envelope.snapshot,
            holder=holder,
            provenance=envelope.provenance,
            live=_live_loop(),
            generate=lambda snapshot, holder, live: "t6-bypassed",
        )
        return result.text

    assert _bypass_handler(_envelope()) == "t6-bypassed"  # RED: gate skipped.
    honest = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-must-not-leak",
        release_path=release_path,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        honest(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")  # green again.


def test_t6_mutation_forced_open_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Tightened/loosened mutant: release forced open admits while closed
    (RED). Reverting the monkeypatch restores the 403 refusal (green)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    handler = sa_bridge.build_live_handler(
        live=_live_loop(),
        generate=lambda snapshot, holder, live: "t6-live-reply",
        release_path=release_path,
    )
    monkeypatch.setattr(sa_bridge, "is_live_inbound_open", lambda path: True)
    assert handler(_envelope()) == "t6-live-reply"  # RED: closed gate ignored.
    monkeypatch.undo()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert _refuse_code(exc_info.value) == (403, "forbidden")  # green again.


def test_t6_mutation_reverted_returns_green(tmp_path):
    """Reverted wiring: open gate admits, closed gate refuses, no leak."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    seen = []

    def _generate(snapshot, holder, live):
        seen.append((snapshot, holder))
        return "t6-reverted-reply"

    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=_generate, release_path=release_path
    )
    assert handler(_envelope()) == "t6-reverted-reply"
    assert len(seen) == 1
    _write_release(release_path, {"status": "closed"})
    with pytest.raises(sa_bridge.LiveRefused):
        handler(_envelope())
    assert len(seen) == 1  # refusal made zero generate calls.


# -- T6-5: AST gates (release purity / fail-closed shape) --------------------


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


def test_t6_release_module_is_stdlib_only_and_finally_free():
    path = _REPO_ROOT / "src" / "soul_access" / "release.py"
    mods, names = _imported_module_names(path)
    assert mods <= {"json", "pathlib", "__future__", "annotations"} or mods == {
        "json",
        "pathlib",
    } or (
        all(
            m.split(".")[0] in {"json", "pathlib", "__future__"} for m in mods
        )
    ), mods
    for banned in (
        "httpx",
        "socket",
        "urllib",
        "subprocess",
        "asyncio",
        "threading",
        "os",
        "sys",
        "src.interfaces",
        "src.llm",
        "src.memory",
        "src.agent",
        "src.external_gateway",
    ):
        assert not any(
            m == banned or m.startswith(banned + ".") for m in mods
        ), ("release.py", banned)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not [
        n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody
    ], "SA-5 T6: release.py must contain zero finally clauses"


def test_t6_bridge_handler_fails_closed_on_release():
    src = (_REPO_ROOT / "src" / "soul_access" / "bridge.py").read_text(
        encoding="utf-8"
    )
    assert "is_live_inbound_open" in src
    assert "LiveRefused(403" in src
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_live_handler":
            target = node
            break
    assert target is not None
    calls_403 = [
        n
        for n in ast.walk(target)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "LiveRefused"
    ]
    assert calls_403, "build_live_handler must construct a LiveRefused"
    raiselines = [
        n.lineno
        for n in ast.walk(target)
        if isinstance(n, ast.Raise)
    ]
    assert raiselines, "build_live_handler must raise fail-closed"
    mods, _names = _imported_module_names(
        _REPO_ROOT / "src" / "soul_access" / "bridge.py"
    )
    for banned in ("httpx", "socket", "urllib", "subprocess", "asyncio"):
        assert not any(
            m == banned or m.startswith(banned + ".") for m in mods
        ), ("bridge.py", banned)
