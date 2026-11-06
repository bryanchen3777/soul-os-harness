"""EI-2B-REAL-CONTEXT-ISOLATED-ADAPTER-1 acceptance (16 scenarios).

RUN RULE (structural, non-negotiable): the OS environment MUST preset
SOUL_OS_DATA_DIR to a fresh isolated root BEFORE the pytest process starts,
so that proxy.py import-time captures (CONV_DIR, INNER_LIFE_DATA_DIR,
DEFAULT_DIARY_ROOT) bind to isolation, never production::

    $env:SOUL_OS_DATA_DIR = "<fresh-tmp-root>"
    .venv\\Scripts\\python.exe -m pytest tests/interfaces/test_harness_soul_context_adapter.py -q -p no:cacheprovider

Rationale: src.llm.proxy captures data_root() at IMPORT time (CONV_DIR =
data_root()/"conversations" + mkdir, proxy.py:73-74). The repo conftest
autouse fixture re-points SOUL_OS_DATA_DIR per-test, but it cannot rewind
import-time captures. verify_isolation() fail-closes when the launch
binding is production.

Zero paid LLM, zero network, zero production IO. Mock backends only.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import sys
import threading
from pathlib import Path

import pytest

from src.interfaces.soul_context_adapter import (
    ISOLATED_ONESHOT_ONLY,
    SHARED_RUNTIME_BLOCKED,
    IsolationError,
    SOUL_ALIAS_TO_AGENT,
    build_harness_messages,
    complete_with_backend,
    ensure_relationship_isolation,
    get_scope_provider,
    load_harness_persona,
    prefetch_scope_memory,
    resolve_agent_id,
    verify_isolation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_USER = "bryan"


@pytest.fixture(autouse=True)
def _clean_relationship_singleton():
    """Module-local singleton hygiene (this file only).

    21 test files repo-wide touch the relationships singleton; any of
    them may leave it pinned when OUR module runs later in the same
    pytest process. The adapter CORRECTLY refuses a foreign-pinned
    singleton (test_16 proves it), so without this fixture every
    build-calling test below would fail on other modules' leaks
    (observed: 09/12/14/15 red in full-suite, green standalone).
    Resetting here disturbs no live runtime -- this is the isolated
    test process, and the refusal path itself stays covered by test_16,
    which pins deliberately AFTER this setup reset.
    """
    from src.soul import relationships as _rel

    with _rel._manager_lock:
        _rel._manager_singleton = None
    yield
    with _rel._manager_lock:
        _rel._manager_singleton = None


# ── helpers ──────────────────────────────────────────────────────────

def _isolated_writes_root(tmp_path: Path) -> Path:
    root = tmp_path / "harness_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _make_memory(tmp_path: Path, name: str = "memory.db"):
    from src.memory.store import MemoryStore

    db = _isolated_writes_root(tmp_path) / name
    return MemoryStore(db_path=db)


def _seed_memory(store, session_id: str, role: str, content: str,
                 speaker: str = ""):
    store.append(session_id, role, content, speaker=speaker)


def _seed_fact(provider, subject: str, predicate: str, object_: str,
               source_pair: str, session_id: str = "seed"):
    from src.memory.sage.models import Fact

    fact = Fact(subject=subject, predicate=predicate, object=object_,
                source="user", session_id=session_id,
                source_pair=source_pair)
    fid = provider._writer.add_fact(fact)
    assert fid, "rule-based add_fact must accept the seeded fact"
    return fid


# (INTEGRITY-GATE-1 §3: production-tree scanning as a safety argument is
# forbidden. Isolation here is by pre-restriction -- launch preset plus
# per-test tmp roots -- and every refusal path has its own test below.)


# ── 1/2: genuine Rem / Ram persona loading ───────────────────────────

def test_01_rem_genuine_persona(tmp_path):
    agent_id, soul = load_harness_persona("rem")
    assert agent_id == "agent_rem"
    raw = (REPO_ROOT / "personas" / "agent_rem.md").read_text(encoding="utf-8")
    assert "你是雷姆" in raw  # repo source marker
    assert "你是雷姆" in soul  # genuine file content flows through
    assert "你是 Yua" not in soul  # no Yua fallback
    assert len(soul) > len(raw)  # build_system_prompt wrapping applied


def test_02_ram_genuine_persona(tmp_path):
    agent_id, soul = load_harness_persona("ram")
    assert agent_id == "agent_ram"
    raw = (REPO_ROOT / "personas" / "agent_ram.md").read_text(encoding="utf-8")
    assert "拉姆" in raw and "拉姆" in soul
    assert "你是 Yua" not in soul
    assert soul != load_harness_persona("rem")[1]  # Rem vs Ram isolated


# ── 3: unauthorized persona rejected ─────────────────────────────────

@pytest.mark.parametrize("alias", [
    "yua", "agent-rem", "agent_rem", "rem/backend-a", "../agent_rem",
    "", "REM", "agent_ram", None,
])
def test_03_unauthorized_soul_alias_rejected(alias):
    with pytest.raises(IsolationError):
        resolve_agent_id(alias)
    with pytest.raises(IsolationError):
        load_harness_persona(alias)


# ── 4: missing persona never falls back to Yua/generic ───────────────

def test_04_missing_persona_fail_closed_no_yua_fallback(tmp_path, monkeypatch):
    from src.llm import proxy as _proxy

    monkeypatch.setattr(_proxy, "SOUL_OS_PERSONAS_DIR",
                        tmp_path / "no_personas_here")
    with pytest.raises(IsolationError):
        load_harness_persona("rem")
    # Document the genuine hazard this refuses: the real loader falls back.
    genuine = _proxy.load_persona("agent_nonexistent_xyz")
    assert "agent_yua" in str(_proxy._AGENT_IDENTITY_RULES)  # fallback exists
    assert genuine  # genuine loader returns SOMETHING (fallback) ..
    with pytest.raises(IsolationError):
        load_harness_persona("agent_nonexistent_xyz")  # .. adapter refuses


# ── 5: SAGE temp-graph recall (genuine initialize + prefetch) ─────────

def test_05_sage_temp_graph_recall(tmp_path):
    root = _isolated_writes_root(tmp_path)
    provider = get_scope_provider(root, "rem", session_id="s05")
    _seed_fact(provider, subject="Bry", predicate="喜歡",
               object_="紅茶配藍莓司康", source_pair="bryan:agent_rem")
    out = prefetch_scope_memory(provider, "Bry 喜歡什麼紅茶",
                                session_id="s05", user_id=OWNER_USER,
                                agent_id="agent_rem")
    assert "紅茶" in out


# ── 6: cross-soul isolation via source_pair_filter ───────────────────

def test_06_cross_soul_memory_isolation(tmp_path):
    root = _isolated_writes_root(tmp_path)
    prem = get_scope_provider(root, "rem", session_id="s06")
    pram = get_scope_provider(root, "ram", session_id="s06")
    assert prem is not pram
    _seed_fact(prem, subject="Bry", predicate="私藏",
               object_="雷姆專屬肉桂捲配方", source_pair="bryan:agent_rem")
    ram_view = prefetch_scope_memory(pram, "肉桂捲配方", session_id="s06",
                                     user_id=OWNER_USER, agent_id="agent_ram")
    assert "肉桂捲" not in ram_view
    rem_view = prefetch_scope_memory(prem, "肉桂捲配方", session_id="s06",
                                     user_id=OWNER_USER, agent_id="agent_rem")
    assert "肉桂捲" in rem_view


# ── 7: same-query different-scope cache cannot pollute ────────────────

def test_07_same_query_cache_scope_isolation(tmp_path):
    from src.memory.sage.token_utils import PrefetchCache

    # Structural proof: the genuine cache key carries NO scope component.
    assert PrefetchCache._key(None, "  紅茶 ") == \
        PrefetchCache._key(None, "紅茶"), "key normalization sanity"
    root = _isolated_writes_root(tmp_path)
    prem = get_scope_provider(root, "rem", session_id="s07")
    pram = get_scope_provider(root, "ram", session_id="s07")
    _seed_fact(prem, subject="Bry", predicate="愛喝", object_="紅茶加蜂蜜",
               source_pair="bryan:agent_rem")
    _seed_fact(pram, subject="Bry", predicate="愛喝", object_="紅茶加檸檬",
               source_pair="bryan:agent_ram")
    q = "Bry 紅茶怎麼喝"
    out_rem = prefetch_scope_memory(prem, q, session_id="s07",
                                    user_id=OWNER_USER, agent_id="agent_rem")
    out_ram = prefetch_scope_memory(pram, q, session_id="s07",
                                    user_id=OWNER_USER, agent_id="agent_ram")
    assert "蜂蜜" in out_rem and "檸檬" not in out_rem
    assert "檸檬" in out_ram and "蜂蜜" not in out_ram
    # Re-query both: per-scope TTL caches must still serve their OWN scope.
    out_rem2 = prefetch_scope_memory(prem, q, session_id="s07",
                                     user_id=OWNER_USER, agent_id="agent_rem")
    assert "蜂蜜" in out_rem2 and "檸檬" not in out_rem2


# ── 8: private history isolation (LLMProxy P0 DI) ─────────────────────

def test_08_private_history_isolation(tmp_path):
    from src.eventbus.bus import SoulEventBus
    from src.llm.proxy import LLMProxy
    from tests._helpers.mock_llm_backend import MockLLMBackend

    root = _isolated_writes_root(tmp_path)
    conv = root / "conversations"
    mem = _make_memory(tmp_path)
    try:
        pa = LLMProxy(bus=SoulEventBus(), backend=MockLLMBackend(),
                      model="model-a", memory_store=mem,
                      conversation_dir=conv)
        assert pa._conversation_dir is not None  # isolation mode ON
        pa._append_private_history_instance("agent_rem", OWNER_USER, "user",
                                            "rem-only-seed-xyz")
        pa._append_private_history_instance("agent_ram", OWNER_USER, "user",
                                            "ram-only-seed-xyz")
        ha = pa._load_private_instance("agent_rem", OWNER_USER)
        hb = pa._load_private_instance("agent_ram", OWNER_USER)
        assert any("rem-only-seed-xyz" in m["content"] for m in ha)
        assert not any("ram-only-seed-xyz" in m["content"] for m in ha)
        assert any("ram-only-seed-xyz" in m["content"] for m in hb)
        assert not any("rem-only-seed-xyz" in m["content"] for m in hb)
        # Unknown scope in isolation mode: [] with NO production fallback.
        assert pa._load_private_instance("agent_rem", "stranger") == []
    finally:
        mem.close()


# ── 9: genuine prompt builder (+ canary through the real history path) ─

def test_09_genuine_prompt_builder_with_canary(tmp_path):
    from src.llm import proxy as _proxy

    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    canary_path = Path(_proxy.CONV_DIR) / f"{OWNER_USER}_agent_rem_private.json"
    from src.paths import production_data_root

    assert Path(_proxy.CONV_DIR).resolve() != \
        production_data_root().resolve(), \
        "launch binding must not be production (path compare only, no IO)"
    marker = "canary-assistant-selfline-xyz"
    import json as _json

    canary_path.parent.mkdir(parents=True, exist_ok=True)
    canary_written = False
    try:
        # Genuine _load_self_recent reads module-level CONV_DIR (import-time
        # capture, bounded to the isolated launch root per verify_isolation).
        canary_path.write_text(_json.dumps(
            [{"role": "assistant", "content": marker}],
            ensure_ascii=False), encoding="utf-8")
        canary_written = True
        # Genuine history path: seed an ASSISTANT turn. A user-role seed would
        # be dropped by the genuine 修法7 filter (proxy.py:1925: Bry offline,
        # i.e. default bry_latest_ts=0, -> stale Bry user turns filtered),
        # which is correct engine behavior, not a defect. The assistant turn
        # flows through the same genuine get_recent_with_meta call
        # (proxy.py:1879, session bryan/agent_rem).
        _seed_memory(mem, f"session_{OWNER_USER}_agent_rem", "assistant",
                     "rem-history-marker-xyz", speaker="agent_rem")
        ctx = build_harness_messages(
            soul_alias="rem", user_id=OWNER_USER, text="現在幾點",
            data_root=root, memory_store=mem,
            memory_context="rem-sage-context-xyz")
        msgs = ctx["messages"]
        assert msgs[0]["role"] == "system"
        blob = "\n".join(m.get("content", "") for m in msgs)
        assert "rem-history-marker-xyz" in blob  # via genuine get_recent_with_meta
        assert "rem-sage-context-xyz" in blob
        assert marker in blob  # via genuine _load_self_recent->_load_private
        assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "現在幾點"
    finally:
        mem.close()
        if canary_written and canary_path.exists():
            canary_path.unlink()


# ── 10: memory=None negative ──────────────────────────────────────────

def test_10_memory_none_refused(tmp_path):
    root = _isolated_writes_root(tmp_path)
    with pytest.raises(IsolationError):
        build_harness_messages(soul_alias="rem", user_id=OWNER_USER,
                               text="hi", data_root=root, memory_store=None)


# ── 11: SAGE-uninitialized negative ───────────────────────────────────

def test_11_sage_uninitialized_refused(tmp_path):
    from src.memory.sage.provider import SAGELiteProvider

    raw = SAGELiteProvider(profile_id="harness_uninit",
                           data_dir=str(tmp_path / "sage_uninit"))
    with pytest.raises(IsolationError):
        prefetch_scope_memory(raw, "hi", session_id="s",
                              user_id=OWNER_USER, agent_id="agent_rem")


# ── 12/13: A/B backend routing + identity stable across model switch ──

def test_12_13_backend_switch_and_identity_stable(tmp_path):
    from tests._helpers.mock_llm_backend import (
        MockLLMBackend, fixed_response_strategy)

    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    try:
        ctx = build_harness_messages(
            soul_alias="rem", user_id=OWNER_USER, text="早安",
            data_root=root, memory_store=mem)
        msgs = ctx["messages"]
        sys0 = msgs[0]["content"]
        ba = MockLLMBackend(response_strategy=fixed_response_strategy("A-resp"))
        bb = MockLLMBackend(response_strategy=fixed_response_strategy("B-resp"))
        ra = asyncio.run(complete_with_backend(msgs, ba, "model-rem-a"))
        rb = asyncio.run(complete_with_backend(msgs, bb, "model-rem-b"))
        assert (ra, rb) == ("A-resp", "B-resp")
        assert ba.call_count == 1 and bb.call_count == 1
        assert ba.last_model == "model-rem-a" and bb.last_model == "model-rem-b"
        # Same request reaches the right instance; identity identical.
        assert ba.last_messages[0]["content"] == sys0
        assert bb.last_messages[0]["content"] == sys0
    finally:
        mem.close()


def test_13_no_global_backend_mutation():
    import src.interfaces.soul_context_adapter as _adapter

    tree = ast.parse(Path(_adapter.__file__).read_text(encoding="utf-8"))
    src = ast.dump(tree)
    assert "set_llm_proxy" not in Path(_adapter.__file__).read_text(
        encoding="utf-8")
    assert "queue_prefetch" not in Path(_adapter.__file__).read_text(
        encoding="utf-8")
    _ = src
    import src.memory.sage.writer as _writer

    assert _writer.__dict__.get("_soul_os_llm_proxy") is None


# ── 14: production IO precluded by construction (no production scan) ───
#
# INTEGRITY-GATE-1 §3 forbids scanning the production tree as evidence.
# This test asserts pre-restriction instead: every readable/writable root
# the genuine call chain can touch (launch-captured CONV_DIR + inner-life
# dir, current data_root, relationships dir) is OUTSIDE the production
# root PATH. production_data_root() below is pure path computation --
# no production file is opened, listed, or stat'ed.

def test_14_no_production_io_by_construction(tmp_path):
    from src.paths import production_data_root

    prod = production_data_root().resolve()  # path only, no IO
    threads_before = len(threading.enumerate())
    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    try:
        ctx = build_harness_messages(
            soul_alias="ram", user_id=OWNER_USER, text="生產隔離探針",
            data_root=root, memory_store=mem, memory_context="ctx")
        rep = ctx["isolation"]
        for label, p in (
            ("current_data_root", rep.current_data_root),
            ("launch_data_root", rep.launch_data_root),
            ("module_conv_dir", rep.module_conv_dir),
            ("module_inner_life_dir", rep.module_inner_life_dir),
            ("relationship_dir", rep.relationship_dir),
        ):
            resolved = Path(p).resolve()
            assert resolved != prod, label
            assert prod not in resolved.parents, label
        assert rep.production_data_root and \
            Path(rep.production_data_root).resolve() == prod
        # Everything this build created stays under the tmp root.
        assert root.resolve() in \
            Path(rep.relationship_dir).resolve().parents
        from tests._helpers.mock_llm_backend import MockLLMBackend
        asyncio.run(complete_with_backend(ctx["messages"], MockLLMBackend(),
                                          "model-ram-a"))
    finally:
        mem.close()
    assert len(threading.enumerate()) == threads_before, \
        "background thread spawned"


# ── 15: no channel broadcast surface ──────────────────────────────────

def test_15_no_channel_broadcast_surface(tmp_path):
    import src.interfaces.soul_context_adapter as _adapter

    text = Path(_adapter.__file__).read_text(encoding="utf-8").lower()
    for token in ("gateway", "telegram", "voice", "websocket", "tts",
                  "publish", "broadcast", "speak(", "channel"):
        assert token not in text, f"adapter must not reference {token}"
    # Runtime proof, ORDER-INDEPENDENT: snapshot sys.modules, run a genuine
    # build + request-local mock complete, and require the import DELTA to
    # contain no channel-surface modules. (A global sys.modules scan would be
    # order-dependent: other tests in the same pytest process legitimately
    # import gateway/telegram. The delta attributes imports to THIS call
    # chain: pytest runs tests sequentially and this suite spawns no
    # background threads -- see the test_14 thread-count assertion.)
    forbidden_segments = {"gateway", "telegram", "voice", "websocket", "tts"}

    def _has_forbidden(name: str) -> bool:
        return any(seg in forbidden_segments for seg in name.lower().split("."))

    before = set(sys.modules)
    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    try:
        ctx = build_harness_messages(
            soul_alias="rem", user_id=OWNER_USER, text="頻道隔離探針",
            data_root=root, memory_store=mem, memory_context="ctx")
        from tests._helpers.mock_llm_backend import MockLLMBackend
        asyncio.run(complete_with_backend(ctx["messages"], MockLLMBackend(),
                                          "model-chan-a"))
    finally:
        mem.close()
    delta = sorted(m for m in set(sys.modules) - before if _has_forbidden(m))
    assert delta == [], \
        f"adapter call chain imported channel modules: {delta}"


# ── 16: unsafe helpers fail-closed (singleton pin + rebound proof) ────

def test_16_unsafe_helper_singleton_fail_closed_then_rebound(tmp_path):
    from src.soul import relationships as _rel
    from src.soul.relationships import BRYAN_ENTITY_ID

    root = _isolated_writes_root(tmp_path)
    # Pin the singleton to scope-A root (the genuine hazard: bare
    # get_relationships_manager() ignores data_dir once pinned).
    with _rel._manager_lock:
        _rel._manager_singleton = None
    mgr_a = _rel.get_relationships_manager(data_dir=str(root / "soul"))
    mgr_a.get_store("agent_rem").touch(BRYAN_ENTITY_ID, confidence_delta=0.6)
    mgr_still_a = _rel.get_relationships_manager(
        data_dir=str(root / "other_soul"))
    assert mgr_still_a is mgr_a, \
        "genuine bare call ignores data_dir once pinned (hazard proven)"
    mem = _make_memory(tmp_path)
    try:
        # Ownership rule: while a foreign owner holds the singleton, the
        # adapter REFUSES to steal it (never disturbs another runtime).
        with pytest.raises(IsolationError):
            build_harness_messages(
                soul_alias="ram", user_id=OWNER_USER, text="關係探針",
                data_root=root, memory_store=mem)
        with _rel._manager_lock:
            assert _rel._manager_singleton is mgr_a, \
                "refused build must leave the foreign singleton untouched"
        # Isolated one-shot reset, then the adapter rebounds per build and
        # verifies; scope-B sees its own bands.
        with _rel._manager_lock:
            _rel._manager_singleton = None
        ctx = build_harness_messages(
            soul_alias="ram", user_id=OWNER_USER, text="關係探針",
            data_root=root, memory_store=mem)
        assert Path(ctx["isolation"].relationship_dir).resolve() == \
            (root / "soul").resolve()
        blob = "\n".join(m.get("content", "") for m in ctx["messages"])
        # ram has no seeded Bry bond in this root -> no leaked rem band.
        assert "深度信任" not in blob
        with _rel._manager_lock:
            assert _rel._manager_singleton is None, \
                "successful build restores the prior (None) singleton"
    finally:
        mem.close()
        with _rel._manager_lock:
            _rel._manager_singleton = None


# ── 17: isolation preflight refuses without a preset environment ──────

def test_17_verify_isolation_refuses_without_preset_env(monkeypatch):
    # Raises BEFORE reset_data_root() (no global state touched); the
    # launch-preset run rule exists precisely because of this refusal.
    monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=True)
    with pytest.raises(IsolationError):
        verify_isolation()


# ── 18: exception mid-build leaves no wrong global singleton ──────────

def test_18_failed_build_restores_prior_singleton(tmp_path, monkeypatch):
    from src.llm import proxy as _proxy
    from src.soul import relationships as _rel

    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    with _rel._manager_lock:
        _rel._manager_singleton = None

    def _boom(*args, **kwargs):
        raise RuntimeError("boom-mid-build-xyz")

    monkeypatch.setattr(_proxy, "_build_messages_private", _boom)
    try:
        with pytest.raises(RuntimeError, match="boom-mid-build-xyz"):
            build_harness_messages(
                soul_alias="rem", user_id=OWNER_USER, text="例外探針",
                data_root=root, memory_store=mem)
        with _rel._manager_lock:
            assert _rel._manager_singleton is None, \
                "mid-build exception must not leave a bound singleton"
    finally:
        mem.close()
        with _rel._manager_lock:
            _rel._manager_singleton = None


# ── 19: sequential Rem + Ram builds in one process stay scoped ─────────

def test_19_sequential_rem_ram_builds_stay_scoped(tmp_path):
    from src.soul import relationships as _rel

    root = _isolated_writes_root(tmp_path)
    mem = _make_memory(tmp_path)
    try:
        ctx_rem = build_harness_messages(
            soul_alias="rem", user_id=OWNER_USER, text="早安",
            data_root=root, memory_store=mem)
        ctx_ram = build_harness_messages(
            soul_alias="ram", user_id=OWNER_USER, text="早安",
            data_root=root, memory_store=mem)
        assert ctx_rem["agent_id"] == "agent_rem"
        assert ctx_ram["agent_id"] == "agent_ram"
        assert ctx_rem["messages"][0]["content"] != \
            ctx_ram["messages"][0]["content"]
        for ctx in (ctx_rem, ctx_ram):
            assert Path(ctx["isolation"].relationship_dir).resolve() == \
                (root / "soul").resolve()
        with _rel._manager_lock:
            assert _rel._manager_singleton is None
    finally:
        mem.close()
        with _rel._manager_lock:
            _rel._manager_singleton = None


# ── 20: build on a bare empty root (no hidden production seeding) ─────

def test_20_build_on_bare_empty_root(tmp_path):
    root = tmp_path / "brand_new_root"  # nothing pre-seeded at all
    root.mkdir(parents=True, exist_ok=True)
    mem = _make_memory(tmp_path, name="bare_memory.db")
    try:
        ctx = build_harness_messages(
            soul_alias="rem", user_id=OWNER_USER, text="空根探針",
            data_root=root, memory_store=mem)
        msgs = ctx["messages"]
        assert msgs[0]["role"] == "system" and msgs[0]["content"].strip()
        assert msgs[-1] == {"role": "user", "content": "空根探針"}
    finally:
        mem.close()


# ── 21: shared/concurrent use is declared BLOCKED (executable) ────────

def test_21_shared_runtime_use_declared_blocked():
    assert ISOLATED_ONESHOT_ONLY is True
    assert "one-shot" in SHARED_RUNTIME_BLOCKED
    assert "process-global" in SHARED_RUNTIME_BLOCKED
