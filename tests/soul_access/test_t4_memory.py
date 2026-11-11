"""SA-4 T4 dual-memory + B-firewall tests: tools stay in harness, soul stays clean.

Decision-complete scope (SA-4): this file + src/soul_access/admission.py +
harness/tool_memory.py are new; the proxy gains two gate call sites + one
junction block; the middleware gains one pre-commit gate. No writer / SAGE /
generation line was modified.

All test names carry the t4 marker: `pytest -q tests/soul_access -k t4`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created). Transport is in-process spies
(plain lists), never a port, never a real LLM call, never data/** writes.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.soul_access import admission as sa_admission  # noqa: E402
from harness import tool_memory as sa_toolmem  # noqa: E402

# -- shared fixtures ----------------------------------------------------------

SENTINEL = "T4-SENTINEL-7f3a9c"
TOOL_MARKERS = ("T4-TOOL-web_search", "T4-PARAM-q=quantum", SENTINEL)


def _spies(n=5):
    """Soul-side long-term sink spies: history/MemoryStore/SAGE/InnerLife/投影."""
    return [[] for _ in range(n)]


def _sink_texts(sinks) -> list:
    texts = []
    for sink in sinks:
        for record in sink:
            texts.append(repr(record))
    return texts


def _assert_sinks_zero(sinks, sentinels=(SENTINEL,)) -> None:
    for sink in sinks:
        for record in sink:
            blob = repr(record)
            for sentinel in sentinels:
                assert sentinel not in blob, "SA-4 T4: soul sink polluted: %r" % (blob,)


def _assert_sinks_clean_of_markers(sinks, markers=TOOL_MARKERS) -> None:
    for sink in sinks:
        for record in sink:
            blob = repr(record)
            for marker in markers:
                assert marker not in blob, "SA-4 T4: tool marker leaked: %r" % (blob,)


def _trusted_clean_ledger():
    return sa_admission.mint_admission()


# -- T4-0: whitelisted completion template (no LLM, whitelist-only) -----------

_EXPECTED_NOTES = {
    "查詢": "我幫你完成了這次查詢。",
    "整理": "我幫你完成了這次整理。",
    "檢查": "我幫你完成了這次檢查。",
    "修改": "我幫你完成了這次修改。",
}


def test_t4_completion_template_whitelist_exact():
    for kind, expected in _EXPECTED_NOTES.items():
        assert sa_admission.build_completion_note(kind) == expected


def test_t4_completion_template_rejects_unlisted():
    for bad in ("總結", "tool_result", "", "查詢 ", None, 42):
        with pytest.raises(sa_admission.AdmissionRejected) as exc_info:
            sa_admission.build_completion_note(bad)
        assert exc_info.value.status == 400


# -- T4-1: credential trust (service-minted; payload bools never consulted) ---


def test_t4_decide_has_no_payload_bool_parameter():
    params = inspect.signature(sa_admission.decide).parameters
    for banned in ("payload", "clean", "admit", "trusted", "is_clean", "allow"):
        assert banned not in params, banned


def test_t4_untrusted_tool_claim_denied():
    verdict = sa_admission.decide(
        user_text="hello",
        assistant_text="hi",
        tool_markers=TOOL_MARKERS,
        credential=None,
    )
    assert verdict.allowed is False
    assert verdict.code == "untrusted-tool-claim"
    # A forged (constructed-but-never-minted) credential is equally untrusted.
    forged = sa_admission.AdmissionCredential(nonce="forged-nonce")
    assert sa_admission.is_trusted(forged) is False
    verdict = sa_admission.decide(
        user_text="hello",
        assistant_text="hi",
        tool_markers=TOOL_MARKERS,
        credential=forged,
    )
    assert verdict.allowed is False
    assert verdict.code == "untrusted-tool-claim"


def test_t4_trusted_clean_round_allowed_and_legacy_open():
    credential = _trusted_clean_ledger()
    assert sa_admission.is_trusted(credential) is True
    verdict = sa_admission.decide(
        user_text="今天天氣如何？",
        assistant_text="今天天氣晴。",
        tool_markers=TOOL_MARKERS,
        credential=credential,
    )
    assert verdict.allowed is True and verdict.code == "clean"
    # Legacy no-tool round (no markers, no credential) still stores.
    verdict = sa_admission.decide(user_text="hi", assistant_text="hello")
    assert verdict.allowed is True


def test_t4_mistyped_candidates_fail_closed():
    credential = _trusted_clean_ledger()
    for bad in (None, 42, ["x"]):
        verdict = sa_admission.decide(
            user_text=bad, assistant_text="ok", credential=credential
        )
        assert verdict.allowed is False
    with pytest.raises(sa_admission.AdmissionRejected):
        sa_admission.decide(
            user_text="ok", assistant_text="ok", tool_markers="not-a-tuple"
        )


# -- T4-2: tool leak bans the WHOLE round (user included, zero writes) --------


def test_t4_tool_leak_in_assistant_bans_whole_round_zero():
    credential = _trusted_clean_ledger()
    sinks = _spies()
    result = sa_admission.commit_soul_round(
        round_key="t4-round-leak-assistant",
        sinks=sinks,
        user_text="幫我查一下",
        assistant_text="我用 T4-TOOL-web_search 查到 T4-SENTINEL-7f3a9c 的結果",
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=sa_admission.RoundLedger(),
    )
    assert result.allowed is False and result.code == "tool-leak"
    assert result.writes == 0
    assert all(len(sink) == 0 for sink in sinks)
    _assert_sinks_zero(sinks)


def test_t4_tool_leak_in_user_bans_whole_round_zero():
    credential = _trusted_clean_ledger()
    sinks = _spies()
    result = sa_admission.commit_soul_round(
        round_key="t4-round-leak-user",
        sinks=sinks,
        user_text="參數是 T4-PARAM-q=quantum 對嗎？",
        assistant_text="對的。",
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=sa_admission.RoundLedger(),
    )
    assert result.allowed is False and result.code == "tool-leak"
    assert all(len(sink) == 0 for sink in sinks)
    _assert_sinks_zero(sinks)


def test_t4_paraphrase_laundering_still_caught():
    """Laundering that carries a distinctive token is still a substring hit."""
    credential = _trusted_clean_ledger()
    verdict = sa_admission.decide(
        user_text="沒問題",
        assistant_text="換句話說，那份 T4-PARAM-q=quantum 的資料我已經看過了",
        tool_markers=TOOL_MARKERS,
        credential=credential,
    )
    assert verdict.allowed is False and verdict.code == "tool-leak"


def test_t4_harness_namespace_token_in_text_bans_round():
    credential = _trusted_clean_ledger()
    verdict = sa_admission.decide(
        user_text="看看 harness/tool-cache-1 的內容",
        assistant_text="好的",
        tool_markers=(),
        credential=credential,
    )
    assert verdict.allowed is False and verdict.code == "namespace-leak"


def test_t4_legit_tool_round_stores_clean_and_replay_zero_new():
    credential = _trusted_clean_ledger()
    ledger = sa_admission.RoundLedger()
    sinks = _spies()
    note = sa_admission.build_completion_note("查詢")
    first = sa_admission.commit_soul_round(
        round_key="t4-round-legit-1",
        sinks=sinks,
        user_text="幫我查一下量子計算",
        assistant_text=note,
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    assert first.allowed is True and first.code == "committed"
    assert first.writes == 5 and first.replayed is False
    assert all(len(sink) == 1 for sink in sinks)
    _assert_sinks_zero(sinks)
    _assert_sinks_clean_of_markers(sinks)
    # Replay: same key, zero new writes, original records intact.
    second = sa_admission.commit_soul_round(
        round_key="t4-round-legit-1",
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


def test_t4_round_context_gate_path():
    credential = _trusted_clean_ledger()
    sa_admission.reset_round_context()
    # Legacy (no context): clean texts pass.
    assert sa_admission.round_soul_write_allowed(user_text="hi", assistant_text="yo")
    # Service-set trusted context: clean passes, leak denied.
    sa_admission.set_round_context(tool_markers=TOOL_MARKERS, credential=credential)
    assert sa_admission.round_soul_write_allowed(
        user_text="hi", assistant_text=sa_admission.build_completion_note("整理")
    )
    assert not sa_admission.round_soul_write_allowed(
        user_text="hi", assistant_text="用了 T4-TOOL-web_search " + SENTINEL
    )
    # Untrusted context (markers, no credential): whole round denied.
    sa_admission.set_round_context(tool_markers=TOOL_MARKERS, credential=None)
    assert not sa_admission.round_soul_write_allowed(user_text="hi", assistant_text="yo")
    sa_admission.reset_round_context()
    assert sa_admission.get_round_context() == ((), None)


# -- T4-3: commit order + isolation (partial write isolates, no backfill) -----


def test_t4_decide_runs_before_first_sink_write():
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
    assert decide_line < first_append_line, "SA-4 T4: decide must precede writes"


def test_t4_partial_write_isolates_no_backfill():
    credential = _trusted_clean_ledger()
    ledger = sa_admission.RoundLedger()

    class _Boom(list):
        def append(self, item):
            raise RuntimeError("t4 simulated contact loss")

    sinks = [[], [], _Boom(), []]
    result = sa_admission.commit_soul_round(
        round_key="t4-round-partial-1",
        sinks=sinks,
        user_text="hi",
        assistant_text=sa_admission.build_completion_note("檢查"),
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    assert result.isolated is True and result.code == "partial-isolated"
    assert result.writes == 2  # the two writes before the loss stand, unrepaired.
    assert ledger.is_isolated("t4-round-partial-1") is True
    # Retry with the same key: zero new writes (no backfill, no repair attempt).
    again = sa_admission.commit_soul_round(
        round_key="t4-round-partial-1",
        sinks=[[], [], [], []],
        user_text="hi",
        assistant_text=sa_admission.build_completion_note("檢查"),
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    assert again.isolated is True and again.writes == 0
    # An unrelated key is unaffected.
    fresh = sa_admission.commit_soul_round(
        round_key="t4-round-partial-2",
        sinks=[[], []],
        user_text="hi",
        assistant_text="yo",
        credential=credential,
        ledger=ledger,
    )
    assert fresh.allowed is True and fresh.writes == 2


# -- T4-4: namespace separation (soul never reads harness N) -----------------


def test_t4_soul_may_not_read_harness_namespace():
    assert sa_admission.soul_may_read("harness/tool-cache-1") is False
    assert sa_admission.soul_may_read("harness/") is False
    assert sa_admission.soul_may_read("soul/history-1") is True
    assert sa_admission.soul_may_read("plain-key") is True


def test_t4_tool_memory_harness_roundtrip_and_soul_hard_block():
    store = sa_toolmem.ToolMemoryStore()
    key = store.record(
        "harness/tool-cache-1",
        path="/tools/web_search",
        params_template="q={query}",
        outcome="2 hits " + SENTINEL,
        failure_experience="timeout once, retried",
    )
    assert key == "harness/tool-cache-1"
    bundle = store.read_for_harness("harness/tool-cache-1")
    assert bundle["params_template"] == "q={query}"
    assert SENTINEL in bundle["outcome"]
    assert len(store) == 1 and store.keys() == ("harness/tool-cache-1",)
    # Soul-side read is a hard block even for an existing key.
    with pytest.raises(sa_toolmem.ToolMemoryDenied) as exc_info:
        store.read_for_soul("harness/tool-cache-1")
    assert exc_info.value.status == 403
    # Soul-namespace keys are refused at record time (no shared keys, ever).
    with pytest.raises(sa_toolmem.ToolMemoryDenied) as exc_info:
        store.record("soul/history-1", path="x")
    assert exc_info.value.status == 400
    with pytest.raises(sa_toolmem.ToolMemoryDenied):
        store.read_for_harness("harness/missing")
    # The harness record holding the sentinel never reaches soul sinks: the
    # T4-2/T4-3 zero-sink assertions above ran against these same sentinels.


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


def test_t4_sa4_modules_make_no_bare_provider_or_cross_reads():
    for path in (
        _REPO_ROOT / "src" / "soul_access" / "admission.py",
        _REPO_ROOT / "harness" / "tool_memory.py",
    ):
        mods, names = _imported_module_names(path)
        for banned in (
            "httpx",
            "socket",
            "urllib",
            "subprocess",
            "asyncio",
            "src.interfaces",
            "src.llm",
            "src.memory",
            "src.agent",
            "src.external_gateway",
        ):
            assert not any(
                m == banned or m.startswith(banned + ".") for m in mods
            ), (path.name, banned)
        raw = path.read_text(encoding="utf-8")
        for banned in ("socket.", "Popen", "urlopen", "create_connection"):
            assert banned not in raw, (path.name, banned)
    # tool_memory never names the soul side at all (namespace separation).
    raw = (_REPO_ROOT / "harness" / "tool_memory.py").read_text(encoding="utf-8")
    assert "soul_access" not in raw
    assert "admission" not in raw


# -- T4-5: policy-gate integration pins (proxy x2, middleware x1) -------------


def _proxy_source() -> str:
    return (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")


def test_t4_proxy_failure_branch_gate_precedes_user_write():
    src = _proxy_source()
    gate = src.index("失敗支線亦受禁存約束")
    branch_end = src.index("── REM-TEXT-1")
    assert gate < branch_end
    region = src[gate:branch_end]
    first_write = min(
        region.index("_append_group_user_instance"),
        region.index("_append_private_history_instance"),
    )
    assert "sa4_soul_write_allowed" in region[:first_write]


def test_t4_proxy_history_gate_precedes_all_writes():
    src = _proxy_source()
    gate = src.index("整輪禁存")
    region = src[gate:]
    first_write = min(
        region.index("_append_group_instance"),
        region.index("_append_private_history_instance"),
        region.index("_memory.append"),
    )
    assert "sa4_soul_write_allowed" in region[:first_write]


def test_t4_proxy_module_import_graph_untouched():
    tree = ast.parse(_proxy_source())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src.soul_access"), alias.name
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src.soul_access"), node.module


def _middleware_commit_async_source() -> tuple:
    src = (_REPO_ROOT / "src" / "memory" / "middleware.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    handler = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_on_agent_speak"
        ):
            handler = node
            break
    assert handler is not None
    closure = None
    for node in ast.walk(handler):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_commit_async"
        ):
            closure = node
            break
    assert closure is not None
    lines = src.splitlines(keepends=True)
    closure_src = "".join(lines[closure.lineno - 1 : closure.end_lineno])
    return (src, handler, closure, closure_src)


def test_t4_middleware_closure_captures_only_approved_text():
    _src, handler, closure, closure_src = _middleware_commit_async_source()
    assert "approved_user_text" in closure_src
    assert "approved_agent_text" in closure_src
    scrubbed = closure_src.replace("approved_user_text", "@@U@@").replace(
        "approved_agent_text", "@@A@@"
    )
    assert "user_text" not in scrubbed, "raw user_text leaks into _commit_async"
    assert "agent_text" not in scrubbed, "raw agent_text leaks into _commit_async"


def test_t4_middleware_gate_precedes_commit_task():
    src, handler, closure, _closure_src = _middleware_commit_async_source()
    lines = src.splitlines()
    gate_line = None
    task_line = None
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "round_soul_write_allowed":
                gate_line = node.lineno
            if isinstance(func, ast.Name) and func.id == "create_managed_task":
                task_line = node.lineno
    assert gate_line is not None and task_line is not None
    assert gate_line < closure.lineno < task_line


def test_t4_proxy_sa4_junction_round_trip():
    from src.llm import proxy as _proxy

    _proxy.sa4_round_reset()
    assert _proxy.sa4_soul_write_allowed(user_text="hi", assistant_text="yo") is True
    credential = sa_admission.mint_admission()
    _proxy.sa4_round_set(tool_markers=TOOL_MARKERS, credential=credential)
    assert _proxy.sa4_soul_write_allowed(
        user_text="hi", assistant_text=sa_admission.build_completion_note("修改")
    ) is True
    assert _proxy.sa4_soul_write_allowed(
        user_text="hi", assistant_text="leak " + SENTINEL
    ) is False
    _proxy.sa4_round_reset()


# -- T4-6: bidirectional mutation (false-allow red / false-deny red / revert) -


def test_t4_mutation_false_allow_turns_red_then_revert_green(monkeypatch):
    sinks = _spies()
    ledger = sa_admission.RoundLedger()
    credential = sa_admission.mint_admission()
    polluted = dict(
        round_key="t4-mutant-allow-1",
        sinks=sinks,
        user_text="hi",
        assistant_text="leak " + SENTINEL,
        tool_markers=TOOL_MARKERS,
        credential=credential,
        ledger=ledger,
    )
    # Mutant: gate forced open (simulates a dropped firewall).
    monkeypatch.setattr(
        sa_admission, "decide",
        lambda **kw: sa_admission.AdmissionDecision(True, "mutant", "forced"),
    )
    sa_admission.commit_soul_round(**polluted)
    with pytest.raises(AssertionError):  # RED: sentinels reached soul sinks.
        _assert_sinks_zero(sinks)
    # Revert: the same polluted round stores zero (green again).
    monkeypatch.undo()
    sinks2 = _spies()
    ledger2 = sa_admission.RoundLedger()
    result = sa_admission.commit_soul_round(
        **{**polluted, "round_key": "t4-mutant-allow-2", "sinks": sinks2,
           "ledger": ledger2}
    )
    assert result.allowed is False and result.code == "tool-leak"
    _assert_sinks_zero(sinks2)


def test_t4_mutation_false_deny_turns_red_then_revert_green(monkeypatch):
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
    sinks = _spies()
    result = sa_admission.commit_soul_round(
        round_key="t4-mutant-deny-1", sinks=sinks,
        ledger=sa_admission.RoundLedger(), **clean,
    )
    assert result.writes == 0
    with pytest.raises(AssertionError):  # RED: a legit round stored nothing.
        assert all(len(sink) == 1 for sink in sinks)
    # Revert: the legit round stores across all five sinks (green again).
    monkeypatch.undo()
    sinks2 = _spies()
    result2 = sa_admission.commit_soul_round(
        round_key="t4-mutant-deny-2", sinks=sinks2,
        ledger=sa_admission.RoundLedger(), **clean,
    )
    assert result2.allowed is True and result2.writes == 5
    _assert_sinks_zero(sinks2)
    _assert_sinks_clean_of_markers(sinks2)


def test_t4_hard_blocks_hold_during_mutation(monkeypatch):
    import os
    import socket

    monkeypatch.setattr(
        sa_admission, "decide",
        lambda **kw: sa_admission.AdmissionDecision(True, "mutant", "forced"),
    )
    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa4_t4_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    monkeypatch.undo()


# -- T4-7: purity + hard blocks ------------------------------------------------

_ALLOWED_ADMISSION_IMPORTS = {"__future__", "secrets", "threading", "dataclasses"}
_ALLOWED_TOOLMEM_IMPORTS = {"__future__", "dataclasses"}


def test_t4_sa4_modules_import_only_stdlib_allowlist():
    for path, allowed in (
        (_REPO_ROOT / "src" / "soul_access" / "admission.py",
         _ALLOWED_ADMISSION_IMPORTS),
        (_REPO_ROOT / "harness" / "tool_memory.py", _ALLOWED_TOOLMEM_IMPORTS),
    ):
        mods, _names = _imported_module_names(path)
        top_level = {m.split(".")[0] for m in mods}
        assert top_level <= allowed, (path.name, top_level - allowed)


def test_t4_hard_blocks_stop_network_and_prod_writes(tmp_path):
    import os
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa4_t4_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    ok_file = tmp_path / "t4-ok.txt"
    ok_file.write_text("t4", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t4"
