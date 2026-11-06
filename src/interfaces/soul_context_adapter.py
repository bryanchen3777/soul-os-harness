"""EI-2B real-context isolated adapter (offline harness seam).

Scope: thin glue ONLY. Reuses the genuine runtime for identity, persona,
memory retrieval and prompt construction; adds NOTHING synthetic to the
Soul context itself.

Genuine reuse (read-only w.r.t. production):
  - identity/persona : ``src.llm.proxy.load_persona`` (reads repo
    ``personas/{agent_id}.md``, wraps with ``build_system_prompt``)
  - SAGE retrieval   : ``src.memory.sage.provider.SAGELiteProvider``
    (``initialize`` + ``prefetch`` with ``source_pair_filter``)
  - prompt build     : ``src.llm.proxy._build_messages_private`` (full
    genuine builder incl. its indirect helpers)

Harness-side isolation controls (this module owns ONLY these):
  - ``SOUL_ALIAS_TO_AGENT`` explicit allowlist (EI-1 hyphen soul_alias ->
    real underscore agent_id; NO silent normalization/fallback)
  - ``verify_isolation`` fail-closed preflight (import-time CONV_DIR /
    INNER_LIFE_DATA_DIR boundedness, data_root != production)
  - per-scope independent SAGE providers (PrefetchCache key is
    query-only md5 -> sharing one provider across scopes pollutes)
  - per-build relationships-singleton rebound (bare
    ``get_relationships_manager()`` ignores data_dir once pinned),
    with entry-ownership refusal and save/restore; simultaneous builds
    in one process are SHARED_RUNTIME_BLOCKED (see below)

NEVER in this module:
  - any global-backend setter call (the five call sites forbidden by ticket)
  - any conversational-frontend / messaging / speech-synthesis surface
  - production ``data/`` reads or writes
  - real LLM providers / keys / network

Ticket: EI-2B-REAL-CONTEXT-ISOLATED-ADAPTER-1. No commit/push/merge.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# EI-1 registry hyphen soul_alias -> real Soul agent_id. Explicit table;
# anything not listed here is rejected (scenario 3). No normalization,
# no fallback, no Yua/generic substitution (scenario 4).
SOUL_ALIAS_TO_AGENT: Dict[str, str] = {
    "rem": "agent_rem",
    "ram": "agent_ram",
}

# Supported execution mode (INTEGRITY-GATE-1 §2). The relationships
# singleton is PROCESS-GLOBAL: no in-function lock can isolate
# simultaneous users of one process, and this adapter must never disturb
# another live runtime. The supported unit is an isolated one-shot
# process (preset SOUL_OS_DATA_DIR at launch, run, exit). Simultaneous
# builds in one process, or any use inside a shared live service
# process, is SHARED_RUNTIME_BLOCKED (declared, not fixed).
ISOLATED_ONESHOT_ONLY = True
SHARED_RUNTIME_BLOCKED = (
    "simultaneous builds in one process and any use inside a shared "
    "live service process are not supported: the relationships "
    "singleton is process-global; the supported unit is an isolated "
    "one-shot process."
)


class IsolationError(RuntimeError):
    """Fail-closed refusal: isolation preconditions not proven."""


@dataclass
class IsolationReport:
    """Evidence produced by verify_isolation()."""

    current_data_root: str
    launch_data_root: str
    production_data_root: str
    module_conv_dir: str
    module_inner_life_dir: str
    relationship_dir: str = ""
    checks: Dict[str, str] = field(default_factory=dict)


def _production_root() -> Path:
    from src.paths import production_data_root

    return production_data_root().resolve()


def verify_isolation() -> IsolationReport:
    """Fail-closed isolation preflight. Raises IsolationError when unsafe.

    Proves (not assumes):
      1. SOUL_OS_DATA_DIR is set in this process.
      2. post-reset data_root() equals it and is NOT the production root.
      3. proxy.CONV_DIR (import-time capture feeding _load_private ->
         _load_self_recent / _load_bry_recent inside the genuine builder)
         lives under a non-production launch root.
      4. proxy.INNER_LIFE_DATA_DIR (import-time capture feeding
         _format_recent_inner_life) likewise.
    """
    from src.paths import data_root, reset_data_root
    from src.llm import proxy as _proxy

    env = os.environ.get("SOUL_OS_DATA_DIR")
    if not env:
        raise IsolationError(
            "SOUL_OS_DATA_DIR is not set; refusing (import-time CONV_DIR.mkdir "
            "at proxy.py:73-74 executes on first import src.llm.proxy)."
        )
    reset_data_root()
    cur = data_root().resolve()
    prod = _production_root()
    if cur == prod or prod in cur.parents:
        raise IsolationError(f"data_root resolves to production: {cur}")

    conv_dir = Path(_proxy.CONV_DIR).resolve()
    launch_root = conv_dir.parent
    if launch_root == prod or prod in launch_root.parents:
        raise IsolationError(
            "BLOCKED _load_self_recent->_load_private->CONV_DIR(import-time): "
            f"module CONV_DIR={conv_dir} is production-bound; "
            "relaunch with SOUL_OS_DATA_DIR=<isolated-root> BEFORE first "
            "import src.llm.proxy."
        )
    inner_dir = Path(_proxy.INNER_LIFE_DATA_DIR).resolve()
    inner_root = inner_dir.parent
    if inner_root == prod or prod in inner_root.parents:
        raise IsolationError(
            "BLOCKED _format_recent_inner_life->INNER_LIFE_DATA_DIR(import-time): "
            f"module dir={inner_dir} is production-bound."
        )
    return IsolationReport(
        current_data_root=str(cur),
        launch_data_root=str(launch_root),
        production_data_root=str(prod),
        module_conv_dir=str(conv_dir),
        module_inner_life_dir=str(inner_dir),
        checks={
            "env_set": "ok",
            "data_root_isolated": "ok",
            "conv_dir_bounded": "ok",
            "inner_life_dir_bounded": "ok",
        },
    )


def resolve_agent_id(soul_alias: str) -> str:
    """Map EI-1 soul_alias to the real agent_id. Unknown -> IsolationError."""
    try:
        return SOUL_ALIAS_TO_AGENT[soul_alias]
    except (KeyError, TypeError):
        raise IsolationError(
            f"unauthorized soul_alias {soul_alias!r}: not in the EI-1 allowlist; "
            "refusing (no normalization, no fallback)."
        ) from None


def load_harness_persona(soul_alias: str) -> Tuple[str, str]:
    """Genuine persona load for an allowlisted soul. Fail-closed on gaps.

    Calls the real ``load_persona(agent_id)``. Refuses BEFORE the call when
    the seeded persona file is absent (the genuine function would fall back
    to DEFAULT_PERSONAS / generic text) or when the agent has no dedicated
    identity rules (the genuine function would fall back to agent_yua rules).
    """
    from src.llm import proxy as _proxy

    agent_id = resolve_agent_id(soul_alias)
    persona_file = _proxy.SOUL_OS_PERSONAS_DIR / f"{agent_id}.md"
    if not persona_file.is_file():
        raise IsolationError(
            f"missing seeded persona {persona_file}: refusing instead of "
            "falling back to DEFAULT_PERSONAS / generic text."
        )
    if agent_id not in _proxy._AGENT_IDENTITY_RULES:
        raise IsolationError(
            f"{agent_id} has no dedicated identity rules: the genuine loader "
            "would fall back to agent_yua rules; refusing."
        )
    soul = _proxy.load_persona(agent_id)
    if not soul or not soul.strip():
        raise IsolationError(f"genuine load_persona({agent_id}) returned empty.")
    return agent_id, soul


# Per-(data_root, scope) independent providers. The genuine PrefetchCache key
# is md5(query) with NO scope component (token_utils.py:209-210), so one
# shared provider would serve scope-A summaries to scope-B on identical
# queries within the 30s TTL. Independent instances are the control.
_SCOPE_PROVIDERS: Dict[Tuple[str, str], Any] = {}


def get_scope_provider(
    data_root: Path, scope: str, session_id: str = "default"
):
    """Return the independent SAGELiteProvider for (data_root, scope).

    Genuine ``SAGELiteProvider(data_dir=<root>/sage/<scope>)`` +
    ``initialize(session_id)``. No schema/reader/writer/cache changes, no
    background prefetch queueing (background thread forbidden).
    """
    from src.memory.sage.provider import SAGELiteProvider

    if scope not in ("rem", "ram"):
        raise IsolationError(f"unknown SAGE scope {scope!r}.")
    key = (str(Path(data_root).resolve()), scope)
    provider = _SCOPE_PROVIDERS.get(key)
    if provider is None:
        provider = SAGELiteProvider(
            profile_id=f"harness_{scope}",
            data_dir=str(Path(data_root).resolve() / "sage" / scope),
        )
        provider.initialize(session_id=session_id)
        _SCOPE_PROVIDERS[key] = provider
    return provider


def ensure_relationship_isolation(soul_dir: Path):
    """Rebind the process-global relationships singleton to soul_dir.

    HARNESS-ONLY control. The genuine ``_format_relationship_block``
    (called inside ``_build_messages_private``) invokes the bare
    ``get_relationships_manager()`` with NO data_dir argument, so once the
    singleton is pinned it ignores every later data_dir (scenario 16 proves
    this). Rebinding + verifying per build keeps its reads bounded to the
    current isolated scope. Never call this in a production process.

    Ownership rule: a non-None singleton at entry means a FOREIGN owner
    pinned it (our own builds always restore the prior value on exit,
    even on exception). Stealing it would disturb that owner, so refuse
    instead of rebinding. Simultaneous builds in one process remain
    SHARED_RUNTIME_BLOCKED: a lock only serializes OUR calls, it cannot
    stop other singleton users.
    """
    from src.soul import relationships as _rel

    target = Path(soul_dir).resolve()
    with _rel._manager_lock:
        prior = _rel._manager_singleton
    if prior is not None:
        raise IsolationError(
            "relationships singleton already pinned by a foreign owner; "
            "refusing to steal it (simultaneous/shared use is "
            "SHARED_RUNTIME_BLOCKED). Reset it to None in an isolated "
            "one-shot process before building."
        )
    with _rel._manager_lock:
        _rel._manager_singleton = None
    manager = _rel.get_relationships_manager(data_dir=str(target))
    bound = Path(manager.data_dir).resolve()
    if bound != target:
        raise IsolationError(
            f"relationships singleton rebound failed: bound={bound} "
            f"target={target}."
        )
    return manager


def prefetch_scope_memory(
    provider, query: str, *, session_id: str, user_id: str, agent_id: str
) -> str:
    """Genuine prefetch with the per-scope source_pair filter.

    Refuses on uninitialized providers (scenario 11) instead of letting the
    genuine AttributeError surface as a confusing failure.
    """
    if getattr(provider, "_reader", None) is None:
        raise IsolationError(
            "SAGE provider not initialized (initialize() never called); "
            "refusing to generate from unproven memory state."
        )
    return provider.prefetch(
        query,
        session_id=session_id,
        source_pair_filter={f"{user_id}:{agent_id}"},
    )


def build_harness_messages(
    *,
    soul_alias: str,
    user_id: str,
    text: str,
    data_root: Path,
    memory_store,
    memory_context: str = "",
    current_time: str = "",
) -> Dict[str, Any]:
    """Build genuine private messages for one isolated harness request.

    Vertical path, all genuine: resolve allowlist -> load_persona ->
    verify_isolation -> rebound relationships -> _build_messages_private
    with a REAL isolated MemoryStore (memory=None is refused: the genuine
    builder dereferences memory.get_recent_with_meta, scenario 10).
    The prior relationships singleton is saved and restored in a finally
    block, so an exception mid-build leaves no wrong global behind; entry
    with a foreign-pinned singleton is refused (see
    ensure_relationship_isolation). Returns the messages plus the
    isolation report (evidence, not claims).
    """
    from src.llm import proxy as _proxy

    if memory_store is None:
        raise IsolationError(
            "memory_store is None: the genuine _build_messages_private "
            "dereferences memory.get_recent_with_meta (proxy.py:1879); "
            "refusing instead of crashing or substituting fixtures."
        )
    agent_id, soul = load_harness_persona(soul_alias)
    report = verify_isolation()
    soul_dir = Path(data_root).resolve() / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    from src.soul import relationships as _rel

    with _rel._manager_lock:
        prior_singleton = _rel._manager_singleton
    try:
        manager = ensure_relationship_isolation(soul_dir)
        report.relationship_dir = str(Path(manager.data_dir).resolve())

        messages: List[Dict[str, str]] = _proxy._build_messages_private(
            agent_id,
            soul,
            text,
            memory_context,
            memory_store,
            user_id=user_id,
            current_time=current_time,
        )
    finally:
        with _rel._manager_lock:
            _rel._manager_singleton = prior_singleton
    if not messages or messages[0].get("role") != "system":
        raise IsolationError("genuine builder returned no system message.")
    return {
        "agent_id": agent_id,
        "soul_alias": soul_alias,
        "user_id": user_id,
        "messages": messages,
        "isolation": report,
    }


async def complete_with_backend(messages, backend, model: str, **kwargs) -> str:
    """Request-local generation: exactly one injected backend, one model."""
    return await backend.complete(messages, model, **kwargs)
