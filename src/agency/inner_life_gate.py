"""
src/agency/inner_life_gate.py — Soul OS M5.8-4 Inner Life → Agency Producer Gating

M5.8-4 (Bry 派工 2026-08-10): Inner Life → Agency Producer Gating
Mode: MINIMAL ADDITIVE BOUNDARY (Option Y from M5.8-3 audit)

設計動機:
  M5.8-2 / M5.8-3 確認: Agency Stage 1-4 frozen 4-stage logic 完全 inner-life-blind。
  若要 inner life 影響 Agency 觸發, 唯一 contract-safe 方式是 producer-side gating
  (M5.8-3 Bry 拍板 Option Y)。

  Producer-side gating = 在 publish AGENCY_TRIGGER 之前, 查 Inner Life trace,
  若有近期 inner life activity (代表 agent 剛做過 inner work), 可選擇性 skip
  publish 給 Agency 4-stage pipeline。

核心原則 (Bry 派工 2026-08-10):
  - Inner Life 影響的是 "Should an Agency trigger be emitted?"
  - 不是 "How should Agency Stage 2 decide?"
  - Agency Stage 1-4 frozen 完全不動
  - 不得 fabricate identity / create InnerLifeEvent / read conversation content
  - 不得使用 LLM / semantic / vector
  - 不得發明 mood score / confidence score / weighting
  - 必須 deterministic / observable / fail-safe / backward-compatible

V1 rule:
  對 proactive_dm trigger_type:
    1. 讀 narrative trace for this agent, bounded to last GATE_QUERY_WINDOW_HOURS。
    2. 找該 agent (provenance.actor_id == agent_id) 最近的 InnerLifeEvent。
    3. 沒 trace file / 沒 agent events → UNAVAILABLE (fail-open = emit)。
    4. query exception → FAILURE (fail-open = emit)。
    5. malformed record (no event_id or ts) → FAILURE (fail-open = emit)。
    6. (now - last_event.ts) < GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES
       → GATED (skip publish, log observable)。
    7. 否則 → EMITTED (publish normally)。

  其他 4 trigger_type (event / dream / morning / night) 不受 gate 影響:
    - 這些 trigger 本身就是 inner-life activity (寫 diary / dream)
    - 不是 inner-life consumer
    - 各自有 scheduler throttling / quiet hours
    - 既有 behavior 100% 保留

FROZEN CONTRACTS 不動:
  - Agency 4 stages (Stage 1-4 frozen M5.1 + M5.2)
  - TriggerEnvelope frozen (M5.2-F)
  - TriggerEnvelope.from_payload frozen (M5.2-Q-4)
  - _publish_agency_trigger signature frozen (M5.2-G)
  - 4 handlers (AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler)
  - InnerLifeEvent frozen (M5.4-5.1)
  - Provenance frozen (M5.4-5.1)
  - NarrativeTraceReader READ-ONLY (M5.4-5.7)
  - SoulEvent.inner_life_event_id top-level (M5.4-5.5)

本模組 additively 引入:
  - GateDecision enum (4 states: EMITTED / GATED / UNAVAILABLE / FAILURE)
  - GateResult dataclass (frozen, deterministic)
  - gate_proactive_dm() pure function (READ-ONLY query, no write)

OUT OF SCOPE:
  - Stage 1-4 modification
  - TriggerEnvelope redesign
  - LLM-based decision
  - semantic / vector / embedding
  - new emotional model
  - scheduler throttling change
  - scheduler quiet hours change
  - unrelated refactor
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("soul_os.agency.inner_life_gate")


# ─────────────────────────────────────────────────────────────────────
# Constants (deterministic v1 rule)
# ─────────────────────────────────────────────────────────────────────

# M5.8-4 v1: 30-minute cooldown by last InnerLifeEvent for proactive_dm.
#
# Why 30 min:
#   - Defensive default; tunable per-env later.
#   - 跟 scheduler._proactive_dm_min_interval_minutes 預設值 orthogonal:
#     scheduler cooldown = "上次 proactive_dm 到現在多久"
#     inner life gate = "上次任何 inner life activity 到現在多久"
#   - 30 min ≈ 角色剛做完 inner work, 短時間不該再主動打擾 user。
#
# Bry 派工 spec:
#   - v1 必須 deterministic → 固定常數, no env random
#   - v1 必須 observable → log 時印這個 threshold
#   - v1 必須 fail-safe → 不可擋掉既有 path
GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES = 30


# M5.8-4 v1: Bounded query window.
#
# 為什麼要 bound: 避免 O(n) full-history scan, 對 long-running runtime 有 scaling risk。
# 24h 是合理上限 (Inner Life events per agent per day 預期 < 100)。
# 對 proactive_dm cooldown 30min 來說, 24h 涵蓋率足夠 (遠超 30min)。
# Bry 派工 spec: deterministic → bound 必須是固定常數。
GATE_QUERY_WINDOW_HOURS = 24


# ─────────────────────────────────────────────────────────────────────
# Gate decision + result (observability per Bry spec §8)
# ─────────────────────────────────────────────────────────────────────

class GateDecision(str, Enum):
    """
    Producer-side gate decision (M5.8-4).

    Bry 派工 spec §8: 至少能區分:
      - trigger emitted
      - trigger gated
      - no Inner Life context
      - lookup failure

    對應:
      - EMITTED     = 正常 publish
      - GATED       = 過去 N 分鐘有 inner life, suppress
      - UNAVAILABLE = 沒 trace file / 沒 agent events, fail-open = emit
      - FAILURE     = query exception / malformed record, fail-open = emit
    """
    EMITTED = "emitted"
    GATED = "gated_inner_life_activity"
    UNAVAILABLE = "gate_unavailable"
    FAILURE = "gate_failure"


@dataclass(frozen=True)
class GateResult:
    """
    Result of gate_proactive_dm().

    Bry 派工 spec §8: observable, 4 個 distinct states。
    """
    decision: GateDecision
    reason: str
    # 觀察用 metadata (不影響 decision, pure observability):
    last_event_id: Optional[str] = None   # 32 hex canonical identity (observed, not fabricated)
    last_event_ts: Optional[str] = None   # ISO 8601 UTC
    elapsed_minutes: Optional[float] = None  # only set when GATED / EMITTED with history


# ─────────────────────────────────────────────────────────────────────
# Pure gate function (READ-ONLY, no side effects)
# ─────────────────────────────────────────────────────────────────────

def gate_proactive_dm(
    agent_id: str,
    now: datetime,
    trace_reader: Optional["NarrativeTraceReader"] = None,
    min_interval_minutes: int = GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES,
    query_window_hours: int = GATE_QUERY_WINDOW_HOURS,
) -> GateResult:
    """
    M5.8-4: Producer-side gate for proactive_dm AGENCY_TRIGGER.

    Pure function (READ-ONLY, no side effects, no InnerLifeEvent creation).
    Called by scheduler._publish_agency_trigger() BEFORE bus.publish().

    Args:
        agent_id:        觸發 proactive_dm 的 agent (e.g. "agent_yua")
        now:             當前時間 (從 caller 傳入, 確保 determinism)
        trace_reader:    Optional NarrativeTraceReader (None = auto-construct via data_root())
        min_interval_minutes: cooldown threshold (default 30, tunable for testing)
        query_window_hours:   bounded query window (default 24, tunable for testing)

    Returns:
        GateResult with decision + reason + observability metadata.

    Rule (deterministic, observable, fail-safe):
      1. Bounded query: query_by_ts_range(start, end) over last query_window_hours.
      2. Filter by provenance.actor_id == agent_id.
      3. No records → UNAVAILABLE (fail-open = emit).
      4. Query exception → FAILURE (fail-open = emit).
      5. Malformed record (no event_id or no ts) → FAILURE (fail-open = emit).
      6. (now - last_event.ts) < min_interval_minutes → GATED.
      7. Otherwise → EMITTED.

    Fail-open semantics (per Bry spec §7):
      - UNAVAILABLE / FAILURE 都 preserve existing Agency execution path
        (caller 收到後應 fall-through 到原本 publish)。
      - This is NOT a security gate; this is context-aware rate limit on
        proactive_dm specifically. Inner Life query 失敗絕對不擋 trigger。

    Why this rule is safe:
      - InnerLifeEvent is canonical identity of inner-life activity.
      - last_event.ts 是從 trace.jsonl 讀出, observed, not fabricated.
      - 30 min 是 defensive default, 可經參數 tune.
      - 其他 4 trigger_type (event / dream / morning / night) 完全不受影響 —
        它們本身是 inner-life activity, 有自己 scheduler throttling.

    Does NOT:
      - Create InnerLifeEvent
      - Read conversation / diary / dream text
      - Use LLM / semantic / vector
      - Modify Stage 1-4 frozen logic
      - Modify TriggerEnvelope frozen schema
      - Modify 4 handlers' acceptance semantics
    """
    # Lazy import to avoid circular import (inner_life may not be ready at module load)
    from src.inner_life.trace_reader import NarrativeTraceReader

    if trace_reader is None:
        trace_reader = NarrativeTraceReader()

    # Validate inputs
    if not isinstance(agent_id, str) or not agent_id:
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"agent_id 必須是非空 str, got: {agent_id!r}",
        )

    if not isinstance(now, datetime):
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"now 必須是 datetime, got: {type(now).__name__}",
        )

    # Normalize now to UTC for ts comparison
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Bounded query (deterministic)
    try:
        window_start = (now - timedelta(hours=query_window_hours)).isoformat()
        window_end = now.isoformat()
        all_records = trace_reader.query_by_ts_range(
            start=window_start, end=window_end
        )
    except Exception as e:
        # 4. Query exception → FAILURE (fail-open = emit)
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"query exception: {type(e).__name__}: {e}",
        )

    # Defensive: all_records must be a list
    if not isinstance(all_records, list):
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"query result 不是 list, got: {type(all_records).__name__}",
        )

    # 2. Filter by agent_id via provenance.actor_id
    # Trace records = event_to_dict() shape (per M5.4-5.7):
    #   {event_id, session_id, correlation_id, parent_event_id, ts,
    #    provenance: {trigger_type, actor_id, source_system, ...}, ...}
    agent_records = []
    for r in all_records:
        if not isinstance(r, dict):
            continue
        provenance = r.get("provenance")
        if not isinstance(provenance, dict):
            continue
        if provenance.get("actor_id") == agent_id:
            agent_records.append(r)

    # 3. No records for this agent → UNAVAILABLE (fail-open = emit)
    if not agent_records:
        return GateResult(
            decision=GateDecision.UNAVAILABLE,
            reason=f"no InnerLifeEvent for agent_id={agent_id} in last {query_window_hours}h",
        )

    # 4. Find most recent (by ts, sort for determinism — append order may not be ts order)
    def _safe_ts(r: dict) -> str:
        ts = r.get("ts", "")
        return ts if isinstance(ts, str) else ""

    agent_records_sorted = sorted(agent_records, key=_safe_ts)
    last_record = agent_records_sorted[-1]

    last_event_id = last_record.get("event_id")
    last_event_ts = last_record.get("ts")

    # 5. Malformed record check
    if not isinstance(last_event_id, str) or not last_event_id:
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"last record missing event_id (malformed trace)",
            last_event_id=None,
            last_event_ts=last_event_ts if isinstance(last_event_ts, str) else None,
        )
    if not isinstance(last_event_ts, str) or not last_event_ts:
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"last record missing ts (malformed trace)",
            last_event_id=last_event_id,
            last_event_ts=None,
        )

    # 6. Compute elapsed
    try:
        last_ts = datetime.fromisoformat(last_event_ts.replace("Z", "+00:00"))
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        elapsed = (now - last_ts).total_seconds() / 60.0
    except (ValueError, TypeError) as e:
        return GateResult(
            decision=GateDecision.FAILURE,
            reason=f"ts parse exception: {type(e).__name__}: {e}",
            last_event_id=last_event_id,
            last_event_ts=last_event_ts,
        )

    # 7. Gate check
    if elapsed < min_interval_minutes:
        return GateResult(
            decision=GateDecision.GATED,
            reason=(
                f"agent_id={agent_id} last InnerLifeEvent elapsed="
                f"{elapsed:.1f}min < {min_interval_minutes}min threshold"
            ),
            last_event_id=last_event_id,
            last_event_ts=last_event_ts,
            elapsed_minutes=elapsed,
        )

    # 8. Past threshold → emit normally
    return GateResult(
        decision=GateDecision.EMITTED,
        reason=(
            f"agent_id={agent_id} last InnerLifeEvent elapsed="
            f"{elapsed:.1f}min >= {min_interval_minutes}min threshold"
        ),
        last_event_id=last_event_id,
        last_event_ts=last_event_ts,
        elapsed_minutes=elapsed,
    )
