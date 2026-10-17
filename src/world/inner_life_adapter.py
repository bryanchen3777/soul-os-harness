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

QUALIFICATION V1 RULE (SG-1 解冻 2026-08-29, Owner 授权, 仅 whitelist 扩展):
  - Type whitelist 扩到 5 types (加 news/weather):
    - news_event            (M6.1-5.1 RSS News, source=news)
    - rain_started          (M6.1-3.1 Open-Meteo, source=weather)
    - weather_temp_change   (M6.1-3.1 Open-Meteo, source=weather)
  - 0 其他逻辑改动 (qualify/dedup/create 全部不变)

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
from typing import Any, Dict, Optional

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
#
# SG-1 (Owner 授权 2026-08-29, 范围仅限 whitelist 扩展): 解冻 M5.9-3 whitelist,
# 加 news/weather 3 个类型, 让 world 事件走 World → InnerLife → InnerLifeEvent
# → Submission Gate → consume() 的正确路径 (降级 world→elevation 直通 adapter):
#   - news_event:          M6.1-5.1 RSS News Source (source=news)
#   - rain_started:        M6.1-3.1 Open-Meteo Weather (source=weather)
#   - weather_temp_change: M6.1-3.1 Open-Meteo Weather (source=weather)
# 0 其他逻辑改动 (qualify/dedup/create 全部不变)。
WORLD_QUALIFYING_TYPES: frozenset = frozenset({
    "calendar_event",
    "user_going_outside",
    # SG-1 解冻 (2026-08-29 Owner 授权, 仅 whitelist 扩展):
    "news_event",
    "rain_started",
    "weather_temp_change",
})


# ─────────────────────────────────────────────────────────────────────
# 環境敘事渲染（SOUL-BUG-006 Step 2，Owner 2026-10-17 裁示採第 2 案）
# ─────────────────────────────────────────────────────────────────────
#
# 為什麼需要它：昇華消費端 SOUL-BUG-006 Step 1 已改為 fail-closed ——
# 事件的 extras 若沒有 `narrative`，就不建立任何節點。而 world 事件的
# extras 實測只有 {world_source, world_type, world_novelty_id} 三個純
# metadata 鍵（反解 elevation_nodes.jsonl 的 content 得出），於是整批被攔截，
# **直接違反 EH-2.1 的 Owner 裁定**（陰晴風雨與主人行程屬共同生活的感知
# 邊界，必須放行進昇華鏈，「不得感知閹割」）。
#
# 裁定的唯一正解（Owner 原話方向）：**在生產端為環境事件賦予一段簡短、
# 具備自然語言描述的敘事字串**，讓它同時滿足 EH-2.1 與 fail-closed。
#
# 為什麼是確定性、0 LLM：
#   - 這是每個 world 事件都會走的路徑，走 LLM 等於把成本與延遲灌進熱路徑；
#     環境事實（下雨、幾度、行程）是**客觀描述**，不需要生成式 creativity。
#   - 同一事件必須永遠得到同一句敘事，否則節點冪等去重會失效
#     （SOUL-BUG-001 修的就是那條路徑）。
#
# 🔴 刻意不做的事：不要回傳 `weather_temperature_c=20.0` 這種 key=value 簿記。
#   `src/world/elevation_adapter.py::_world_event_content()` 目前正是那個形狀
#   （weather 分支產出 `summary (temperature=20°C, precipitation=0mm)`），
#   那是簿記不是敘事，本票的病根就在那裡。新的渲染器只產自然語言。

# 氣溫帶 → 自然語言形容詞（Owner 示範句「空氣微冷」的同一套分法）
_TEMP_BANDS_C: tuple[tuple[float, str], ...] = (
    (5.0, "寒冷"),
    (12.0, "微冷"),
    (20.0, "涼"),
    (27.0, "溫暖"),
)
_TEMP_BANDS_WARM: str = "炎熱"


def _describe_temperature(temp_c: Any) -> str:
    """把攝氏溫度轉成自然語言形容詞。回 "" 給非數值（不猜、不發明）。"""
    try:
        value = float(temp_c)
    except (TypeError, ValueError):
        return ""
    for ceiling, word in _TEMP_BANDS_C:
        if value <= ceiling:
            return word
    return _TEMP_BANDS_WARM


def world_event_narrative(world_event: WorldEvent) -> str:
    """把 WorldEvent 渲染成**自然語言**敘事（確定性、0 LLM）。

    Owner 裁示的示範句是「今日氣溫降低，且伴隨陰雨，空氣微冷」——
    重點在於它是**一句人話**，不是欄位列舉。本函式照這個方向實作：
    每個 type 一個句型，盡量把可得的客觀細節（氣溫帶、降雨）併進同一句。

    回 "" 給「真的沒有任何可敘述內容」的情況 —— 呼叫端不得硬塞佔位字串，
    讓消費端的 fail-closed 照常生效。**寧可少一顆節點，也不要假敘事。**
    """
    summary = str(getattr(world_event, "summary", "") or "").strip()
    data = getattr(world_event, "data", None) or {}
    event_type = str(getattr(world_event, "type", "") or "")

    temp_word = _describe_temperature(data.get("weather_temperature_c"))
    temp_phrase = ""
    if temp_word and event_type in ("rain_started", "weather_temp_change"):
        raw = data.get("weather_temperature_c")
        temp_phrase = f"，氣溫 {float(raw):g} 度，空氣{temp_word}"

    if event_type == "rain_started":
        # 🔴 刻意**不**把 summary 綴在後面當括號註記。
        #   渲染出的句子本身已經完整；綴上原始 summary 等於把 observation text
        #   又搬回圖譜（與 M5.9-3 的邊界衝突），也讓句子變成半人半簿記。
        return f"外面下起了雨{temp_phrase}。"
    if event_type == "weather_temp_change":
        return f"今天的天氣有變化{temp_phrase}。" if temp_phrase else "今天的天氣有變化。"
    if event_type == "calendar_event":
        return f"行程上寫著：{summary}。" if summary else ""
    if event_type == "user_going_outside":
        return f"主人出門了：{summary}。" if summary else "主人出門了。"
    if event_type == "news_event":
        return f"聽到一則新聞：{summary}。" if summary else ""

    # 未列入白名單的型別：交出 summary 本身（若它像人話）。
    # 空 summary 就回 ""，不發明。
    return summary


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
            ＋ SOUL-BUG-006 Step 2（Owner 2026-10-17 裁示採第 2 案）附加的
            `narrative`（自然語言環境敘事）。3 個原有鍵**原樣不變**，
            narrative 為空時**不放該鍵**。
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
                extras=self._build_extras(world_event),
            ),
            session_id=None,
            correlation_id=None,
            parent_event_id=None,
            # M5.15-5: explicit external causality (Layer 1)
            # WorldEvent.novelty_id free string, no 32-hex format, no existence check
            source_world_event_novelty_id=world_event.novelty_id,
        )

    @staticmethod
    def _build_extras(world_event: WorldEvent) -> dict:
        """組 InnerLifeEvent 的 extras：3 個原有 metadata 鍵 **原樣保留**，
        另外附加 SOUL-BUG-006 Step 2 的自然語言 `narrative`。

        - 既有 3 鍵一個字都沒改（M5.15-5 J.2 契約），
          新增的是**純附加**欄位。
        - `narrative` 為空時**不放這個鍵**（不放佔位字串），
          讓消費端 fail-closed 照常生效 —— 那是刻意的，不是漏掉。
        """
        extras = {
            "world_source": str(world_event.source),
            "world_type": str(world_event.type),
            "world_novelty_id": str(world_event.novelty_id),
        }
        narrative = world_event_narrative(world_event)
        if narrative:
            extras["narrative"] = narrative
        return extras

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
