"""Synthetic soul context + stub LLM backend (EI-2B API-only slice).

Pure stdlib, no IO, no network, no subprocess. Never imports Gateway,
Telegram, Voice, Consciousness, SAGE, Memory runtime, EventBus, or any
real LLM provider. All soul fixtures are injected by the caller (tests);
production personas, memory, and credentials are never read here.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Dict, List, Optional

from .schema import InterfaceError


@dataclass(frozen=True)
class SoulFixture:
    """Injected synthetic identity fixture for one agent."""

    agent_id: str
    persona_excerpt: str
    persona_source: str
    persona_sha256: str
    facts: tuple = ()


@dataclass(frozen=True)
class ContextProvenance:
    agent_id: str
    persona_source: str
    persona_sha256: str
    fact_count: int


@dataclass(frozen=True)
class StubGeneration:
    response_id: str
    body: str
    memory_evidence: Optional[str]
    provenance: ContextProvenance


class SyntheticSoulStore:
    """In-memory synthetic memory. Per-agent facts, no persistence."""

    def __init__(self) -> None:
        self._fixtures: Dict[str, SoulFixture] = {}

    def add(self, fixture: SoulFixture) -> None:
        if not isinstance(fixture, SoulFixture):
            raise InterfaceError(500, "invalid_fixture", None, "Bad synthetic fixture.")
        if not fixture.agent_id or not fixture.persona_excerpt:
            raise InterfaceError(500, "invalid_fixture", None, "Bad synthetic fixture.")
        self._fixtures[fixture.agent_id] = fixture

    def load_context(self, agent_id: str):
        try:
            fx = self._fixtures[agent_id]
        except KeyError:
            raise InterfaceError(
                500, "soul_context_blocked", "soul_alias",
                "No synthetic soul context for agent %r; full runtime wiring "
                "is out of scope for this slice." % (agent_id,),
            )
        prov = ContextProvenance(
            agent_id=fx.agent_id,
            persona_source=fx.persona_source,
            persona_sha256=fx.persona_sha256,
            fact_count=len(fx.facts),
        )
        return fx, prov


@dataclass
class StubBackend:
    """Deterministic stub LLM. Records every call for isolation asserts."""

    authorized_backend_refs: tuple
    calls: List[dict] = field(default_factory=list)

    def generate(self, *, agent_id, backend_ref, public_model_id,
                 user_text, fixture, provenance, memory_policy):
        if backend_ref not in self.authorized_backend_refs:
            raise InterfaceError(403, "forbidden", "backend",
                                 "Backend is not authorized.")
        call = {
            "agent_id": agent_id,
            "backend_ref": backend_ref,
            "public_model_id": public_model_id,
            "fact_count": provenance.fact_count,
            "memory_policy": memory_policy,
        }
        self.calls.append(call)
        response_id = "stub-resp-%s-%d" % (agent_id, len(self.calls))
        facts_bit = ";".join(fixture.facts) if fixture.facts else "(no-facts)"
        body = "[%s via %s/%s] %s | persona=%s facts=%s" % (
            agent_id, backend_ref, public_model_id,
            user_text[:120], provenance.persona_sha256[:12], facts_bit[:200],
        )
        if memory_policy == "interactive_candidate":
            evidence = "syn-mem:%s:%d" % (agent_id, provenance.fact_count)
        else:
            evidence = None
        return StubGeneration(
            response_id=response_id, body=body,
            memory_evidence=evidence, provenance=provenance,
        )
