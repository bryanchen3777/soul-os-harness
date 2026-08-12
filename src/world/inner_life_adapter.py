"""
src/world/inner_life_adapter.py — Soul OS M5.9-3 World → Inner Life Adapter

M5.9-3 (Bry 派工 2026-08-10): World → Inner Life Adapter Implementation
Mode: MINIMAL ADDITIVE / Implementation

設計動機 (from M5.9-1 + M5.9-2 audit):
  - World layer 是 perception / context only (M3 frozen)
  - Inner Life 是 canonical lived experience (M5.4-5.1 frozen)
  - 5 個現有 InnerLifeEvent producer 都是 "Soul acted" semantic
  - World 是 "Soul observed" semantic — quality > quantity 過濾必要
  - 從 M5.9-2 設計的 v1 rule 確認: type whitelist 是 smallest safe surface

核心原則 (Bry 派工 2026-08-10 spec):
  - World → Inner Life bridge 必須 deterministic, observable, fail-closed
  - Only 2 approved WorldEvent types qualify (calendar_event + user_going_outside)
  - All other types fail-closed (NO, no silent conversion)
  - 不得 fabricate identity, 不得 create duplicate InnerLifeEvent
  - 不得引入 LLM / semantic / vector / scoring
  - 不得修改 frozen contracts (WorldEvent, InnerLifeEvent, Provenance,
    Event Bus, Agency Stage 1-4, TriggerEnvelope, NarrativeTrace,
    InnerLifeWriter identity authority)

QUALIFICATION V1 RULE (M5.9-2 design):
  - Type whitelist (2 types):
    - calendar_event
    - user_going_outside
  - Source whitelist rejected (redundant with type, less precise)
  - Other 6 candidate dimensions (B/C/D/F/G/H) rejected per evidence
  - 0 LLM / 0 scoring / 0 semantic

DEDUP V1 RULE (M5.9-2 design):
  - In-memory Dict[str, str] mapping novelty_id → event_id
  - FIFO eviction at MAX 1000 entries
  - 0 persistent state, 0 external storage
  - Lost on restart acceptable (per "no replay/backfill" spec)

IDENTITY V1 SPEC (M5.9-2 design):
  - actor_id = None (system-level, per Provenance docstring "None for system")
  - session_id = None (no session concept for world events)
  - correlation_id = None (no narrative group)
  - parent_event_id = None (root event, 5 個現有 producer 全部 None)
  - source_system = "narrative" (cross-system bucket in VALID_SOURCE_SYSTEMS)
  - trigger_type = "world:<type>" per-type (跟既有 "diary:morning" / "dream:dream" 一致)
  - extras = {world_source, world_type, world_novelty_id} (all str per Provenance validation)

FROZEN CONTRACTS 不動:
  - WorldEvent (frozen M3 + M3.1 Phase B additive priority)
  - InnerLifeEvent (frozen M5.4-5.1)
  - Provenance (frozen M5.4-5.1)
  - InnerLifeWriter sole creator (frozen M5.4-5.1)
  - NarrativeTraceWriter (frozen M5.4-5.6)
  - Event Bus contract (frozen M3.1)
  - Agency Stage 1-4 (frozen M5.1 + M5.2)
  - TriggerEnvelope (frozen M5.2-F)
  - VALID_SOURCE_SYSTEMS frozenset (5 values: memory/diary/dream/narrative/system)

本模組 additively 引入:
  - WORLD_QUALIFYING_TYPES frozenset
  - WorldQualificationDecision enum
  - qualify_world_event() pure function
  - WorldInnerLifeAdapter class (buses subscription + dedup + create)
  - 訂閱 EventType.WORLD_EVENT through existing bus pattern

OUT OF SCOPE:
  - WorldEvent contract change
  - InnerLifeEvent contract change
  - Provenance schema change
  - 引入 LLM / semantic / vector / scoring
  - 修改 WorldPerceptionMiddleware
  - 修改 InnerLifeWriter
  - 修改 4 handlers / Stage 1-4
  - 修改 Event Bus
  - persistent dedup storage
  - historical replay / backfill / migration
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    Provenance,
)
from src.inner_life.writer import InnerLifeWriterStats
from src.world.perception import WorldEvent

logger = logging.getLogger("soul_os.world.inner_life_adapter")


# ─────────────────────────────────────────────────────────────────────
# Constants (deterministic v1 rule, per M5.9-2 design)
# ─────────────────────────────────────────────────────────────────────

# M5.9-2 v1 type whitelist: smallest safe surface.
# 2 types only, based on 5 synthetic scenarios evidence:
#   - calendar_event: TEST_C YES (30-min meeting = Soul action implication)
#   - user_going_outside: TEST_E YES (explicit actor involvement in data.actor)
# All other types: NO (fail-closed, including unknown types)
WORLD_QUALIFYING_TYPES: frozenset = frozenset({
    "calendar_event",
    "user_going_outside",
})


# M5.9-2 v1 dedup max size: bounded FIFO.
# 1000 entries covers ~ 1 calendar event/day + 1 social event/hour
# for 2-3 weeks of history. Lost on restart acceptable (no replay).
WORLD_DEDUP_MAX_SIZE: int = 1000


# ─────────────────────────────────────────────────────────────────────
# Qualification decision + result (observability per M5.9-2 spec)
# ─────────────────────────────────────────────────────────────────────

class WorldQualificationDecision(str, Enum):
    """
    Producer-side qualification decision (M5.9-3).

    M5.9-2 spec §5: Unknown WorldEvent types must NOT silently
    become InnerLifeEvent. This enum is the explicit verdict.

    States:
      YES                  = type in whitelist, may create InnerLifeEvent
      NO_TYPE_NOT_QUALIFYING = type not in whitelist (including unknown),
                              fail-closed, no InnerLifeEvent created
    """
    YES = "yes"
    NO_TYPE_NOT_QUALIFYING = "no_type_not_qualifying"


@dataclass
class WorldQualificationResult:
    decision: WorldQualificationDecision
    reason: str
    world_type: str  # observed type (for observability)


# ─────────────────────────────────────────────────────────────────────
# Pure qualification function (READ-ONLY, no side effects)
# ─────────────────────────────────────────────────────────────────────

def qualify_world_event(world_event: WorldEvent) -> WorldQualificationResult:
    """
    M5.9-3 v1 deterministic qualification.

    Rule (M5.9-2 design §4.1):
      1. type IN WORLD_QUALIFYING_TYPES → YES
      2. type NOT IN whitelist (including unknown) → NO_TYPE_NOT_QUALIFYING

    Why type whitelist:
      - Smallest safe surface (2 types)
      - 4/5 strict match + 1/5 conservative (TEST_A rain_started MAYBE → NO)
      - Quality > Quantity preserved
      - 0 LLM / 0 scoring / 0 semantic
      - 0 frozen contract change

    Why fail-closed:
      - Per Bry spec §5: Unknown WorldEvent types must NOT silently
        become InnerLifeEvent
      - Per-event extension requires explicit whitelist update (safer
        than implicit acceptance)

    Determinism:
      - 1 dimension (type)
      - 1 rule (whitelist membership)
      - No time-of-day evaluation
      - No external state
      - No random / LLM / scoring
      - Same input → same output

    Returns:
        WorldQualificationResult with decision + reason + world_type.

    Does NOT:
      - Create InnerLifeEvent
      - Call InnerLifeWriter
      - Read WorldPerceptionState / Trace
      - Read conversation / diary / dream content
      - Use LLM / semantic / vector
    """
    world_type = world_event.type
    if not isinstance(world_type, str) or not world_type:
        # Defensive: missing/invalid type → fail-closed
        return WorldQualificationResult(
            decision=WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING,
            reason=f"missing or invalid world_event.type: {world_type!r}",
            world_type="<missing>" if not world_type else world_type,
        )

    if world_type in WORLD_QUALIFYING_TYPES:
        return WorldQualificationResult(
            decision=WorldQualificationDecision.YES,
            reason=f"type {world_type!r} in WORLD_QUALIFYING_TYPES",
            world_type=world_type,
        )

    return WorldQualificationResult(
        decision=WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING,
        reason=f"type {world_type!r} not in WORLD_QUALIFYING_TYPES",
        world_type=world_type,
    )


# ─────────────────────────────────────────────────────────────────────
# Adapter (subscribe + dedup + create)
# ─────────────────────────────────────────────────────────────────────

class WorldInnerLifeAdapter:
    """
    M5.9-3: World → Inner Life Adapter.

    Subscribes to EventType.WORLD_EVENT on existing bus. For each
    WorldEvent, runs qualification + dedup check, then calls
    InnerLifeWriter.create_event() exactly once per unique
    qualifying novelty_id.

    Pattern: matches M5.4-6.1/6.2 producer pattern (call existing
    InnerLifeWriter, no second creator, no new architecture).

    Lifecycle:
      1. __init__: inject inner_life_writer (mandatory)
      2. register(bus): subscribe to WORLD_EVENT on bus
      3. unregister(bus): unsubscribe
      4. handle_event(event): per-WORLD_EVENT entry point
         a. parse WorldEvent from event.payload
         b. qualify_world_event(world_event)
         c. if NO → log debug, return
         d. if YES → dedup check
         e. if duplicate → log debug, return existing event_id
         f. if new → inner_life_writer.create_event()
         g. record in dedup dict (FIFO eviction)
    """

    def __init__(
        self,
        inner_life_writer: InnerLifeWriter,
        dedup_max_size: int = WORLD_DEDUP_MAX_SIZE,
    ):
        """
        Args:
            inner_life_writer: 必填, sole canonical InnerLifeEvent creator
                                (per M5.4-5.1 frozen)
            dedup_max_size:    dedup dict max entries, default 1000
                                (per M5.9-2 design §4.2)
        """
        if inner_life_writer is None:
            raise ValueError(
                "inner_life_writer 必填, InnerLifeWriter is sole canonical "
                "creator (per M5.4-5.1 frozen)"
            )
        self._writer: InnerLifeWriter = inner_life_writer
        self._dedup: Dict[str, str] = {}  # novelty_id -> event_id
        self._dedup_max_size: int = dedup_max_size
        # Observability counters
        self._stats = {
            "events_received": 0,
            "qualifying_yes": 0,
            "non_qualifying": 0,
            "duplicates_skipped": 0,
            "events_created": 0,
            "create_failures": 0,
        }
        logger.info(
            f"[M5.9-3 WorldInnerLifeAdapter] initialized "
            f"dedup_max_size={dedup_max_size} "
            f"qualifying_types={sorted(WORLD_QUALIFYING_TYPES)}"
        )

    def register(self, bus: SoulEventBus) -> None:
        """
        Subscribe to WORLD_EVENT on bus. Reuse existing bus pattern.
        """
        bus.subscribe(
            subscriber_id="world_inner_life_adapter",
            handler=self.handle_event,
            event_filter={EventType.WORLD_EVENT},
        )
        logger.info(
            "[M5.9-3 WorldInnerLifeAdapter] subscribed to WORLD_EVENT ✓"
        )

    def unregister(self, bus: SoulEventBus) -> None:
        bus.unsubscribe("world_inner_life_adapter")

    async def handle_event(self, event: SoulEvent) -> None:
        """
        Per-WORLD_EVENT entry point.

        1. Parse WorldEvent from event.payload
        2. qualify_world_event(world_event)
        3. NO → log debug, return
        4. YES → dedup check
        5. duplicate → return existing event_id
        6. new → inner_life_writer.create_event()
        7. record dedup, log info
        """
        if event.event_type != EventType.WORLD_EVENT:
            return

        self._stats["events_received"] += 1

        # 1. Parse WorldEvent
        try:
            world_event = WorldEvent.from_payload(event.payload)
        except Exception as e:
            logger.warning(
                f"[M5.9-3] WorldEvent.from_payload 失敗, skip "
                f"(不影響主路徑): {type(e).__name__}: {e}"
            )
            return

        # 2. Qualify
        qual = qualify_world_event(world_event)
        if qual.decision != WorldQualificationDecision.YES:
            self._stats["non_qualifying"] += 1
            logger.debug(
                f"[M5.9-3] skip: {qual.reason} "
                f"world_novelty_id={world_event.novelty_id}"
            )
            return

        # YES: track qualifying
        self._stats["qualifying_yes"] += 1

        # 3. Dedup check
        if self._is_duplicate(world_event.novelty_id):
            self._stats["duplicates_skipped"] += 1
            existing_event_id = self._dedup[world_event.novelty_id]
            logger.debug(
                f"[M5.9-3] dedup hit: novelty_id={world_event.novelty_id} "
                f"existing_event_id={existing_event_id}"
            )
            return

        # 4. Create InnerLifeEvent (M5.9-2 spec §6)
        try:
            event_obj = self._create_inner_life_event(world_event)
        except Exception as e:
            self._stats["create_failures"] += 1
            logger.warning(
                f"[M5.9-3] InnerLifeWriter.create_event 失敗, skip "
                f"(不影響主路徑): {type(e).__name__}: {e}"
            )
            return

        # 5. Record in dedup
        self._record_dedup(world_event.novelty_id, event_obj.event_id)
        self._stats["events_created"] += 1
        logger.info(
            f"[M5.9-3] InnerLifeEvent created ✓ "
            f"event_id={event_obj.event_id} "
            f"world_novelty_id={world_event.novelty_id} "
            f"world_type={world_event.type}"
        )

    def _create_inner_life_event(self, world_event: WorldEvent) -> InnerLifeEvent:
        """
        Build Provenance + call InnerLifeWriter.create_event().

        Per M5.9-2 spec §6:
          - trigger_type = "world:<type>"
          - actor_id = None
          - source_system = "narrative"
          - extras = {world_source, world_type, world_novelty_id}
          - session_id / correlation_id / parent_event_id = None

        M5.15-5 (Bry 派工 2026-08-12 19:14): Cross-system identity bridge.
        Sets source_world_event_novelty_id = WorldEvent.novelty_id to establish
        explicit Layer 1 external causality (WorldEvent → InnerLifeEvent).
        Independent from parent_event_id (which remains None for root events;
        M5.4-5.1 frozen).
        0 change to existing 4 fields (provenance / session_id / correlation_id /
        parent_event_id). 0 change to lineage_depth / lineage_path semantics.
        """
        return self._writer.create_event(
            provenance=Provenance(
                trigger_type=f"world:{world_event.type}",
                actor_id=None,
                source_system="narrative",
                trace_ref=None,
                extras={
                    "world_source": str(world_event.source),
                    "world_type": str(world_event.type),
                    "world_novelty_id": str(world_event.novelty_id),
                },
            ),
            session_id=None,
            correlation_id=None,
            parent_event_id=None,
            # M5.15-5: explicit external causality (Layer 1)
            # WorldEvent.novelty_id free string, no 32-hex format, no existence check
            source_world_event_novelty_id=world_event.novelty_id,
        )

    # ── Dedup helpers (FIFO bounded) ──

    def _is_duplicate(self, novelty_id: str) -> bool:
        return novelty_id in self._dedup

    def _record_dedup(self, novelty_id: str, event_id: str) -> None:
        """
        FIFO eviction at max size. Deterministic.
        """
        if len(self._dedup) >= self._dedup_max_size:
            # FIFO: oldest first
            oldest = next(iter(self._dedup))
            del self._dedup[oldest]
        self._dedup[novelty_id] = event_id

    def get_stats(self) -> Dict[str, int]:
        """Observability counters."""
        return dict(self._stats)

    def get_dedup_size(self) -> int:
        """Current dedup dict size (for test / observability)."""
        return len(self._dedup)
