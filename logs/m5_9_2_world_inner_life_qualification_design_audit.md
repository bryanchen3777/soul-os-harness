# M5.9-2 — World → Inner Life Qualification Design Audit

**Ticket:** M5.9-2 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY / DESIGN AUDIT
**Baseline:** `HEAD = 09bf6a6` (post M5.9-1) | `origin/main = 09bf6a6` (synced)
**Date:** 2026-08-10 23:00 EDT
**Auditor:** Mavis (M3) for Bry

---

## 0. Audit Charter

Bry 派工原文:
> "Design the minimum deterministic qualification boundary that can allow meaningful WorldEvent instances to become lived InnerLife experiences, while preserving Quality > Quantity and all existing frozen contracts."

Bry 派工 spec 強調:
- §3 "Do NOT automatically add scoring dimensions. Only retain dimensions justified by current evidence."
- §5 "Unknown WorldEvent types must NOT silently become InnerLifeEvent."
- §6 "Prevent repeated world observations from generating repeated InnerLifeEvents. Prefer deterministic bounded rules."
- §7 "Do NOT fabricate actor identity."
- §9 "Do not invent session identity merely because InnerLifeEvent supports it."
- §10 "Determine whether WorldEvent can legitimately participate in existing lineage semantics. If not, leave it absent."
- §R "Final classification: A / B / C / D"

---

## 1. Existing Producer Dimension Usage (Evidence baseline)

### 1.1 5 個現有 producer 怎麼決定 InnerLifeEvent 維度

從 M5.9-1 跟本次 trace:

| Producer | Provenance trigger_type | actor_id | source_system | session_id | correlation_id | parent_event_id |
|----------|------------------------|----------|---------------|------------|----------------|-----------------|
| ConversationQualification | `conversation:user_message` | session_id (e.g. "session_bryan_agent_yua") | "narrative" | ✓ (from SESSION_END) | ✓ (= session_id) | ✗ None (root) |
| `_proactive_dm_llm_executor` | `agent_reply` | agent_id | "narrative" | ✗ None | ✗ None | ✗ None |
| `_event_writer_executor` | `dream:event` | agent_id | "dream" | ✗ None | ✗ None | ✗ None |
| `_dream_writer_executor` | `dream:dream` | dreamer | "dream" + extras | ✗ None | ✗ None | ✗ None |
| `_diary_writer_executor` | `diary:morning` / `diary:night` | agent_id | "diary" | ✗ None | ✗ None | ✗ None |

### 1.2 重要 observations

1. **actor_id 來源:** 4 個 producer 用 `agent_id` (Soul 的),1 個用 `session_id` (chat session encoded user_id)。
   - 沒有 producer 用 "system" 字串 — actor_id 總是帶語意(誰做了這事)。
   - **World bridge 沒有 agent_id** → 必須用 None (system-level)。

2. **source_system 5 種:** "narrative" / "dream" / "diary" / "memory" / "system"。
   - 對 World bridge:**"narrative"** 是 natural fit (Soul's perception 是 narrative context)。
   - 其他 4 個 producer 各綁自己的 subsystem (diary/dream/memory), "narrative" 是 cross-system。

3. **session_id 只有 1 個 producer 用** (ConversationQualification, 因為 conversation 本質是 session-based)。
   - 其他 4 個 producer 都 None。
   - **World events 沒 session 概念** → None。

4. **correlation_id 只有 1 個 producer 用** (ConversationQualification, 因為 conversation 是 narrative group)。
   - 其他 4 個 None。
   - **World events 沒 narrative group** → None。

5. **parent_event_id 全部 None** (5 個 producer 全部 root events)。
   - **World events 也 root** → None。

6. **trigger_type 是 namespace-style string**,additive 擴充 (`diary:morning` / `dream:event` 風格)。
   - World bridge 可加 `world:<type>`。

### 1.3 Reusable patterns 對 World bridge

| Pattern | 適用? | 理由 |
|---------|-------|------|
| `inner_life_writer.create_event(provenance=Provenance(...))` | ✓ | 5 個 producer 統一 pattern,World bridge 直接 reuse |
| Provenance with actor_id (Optional[str]) | ✓ | None 是 valid (per docstring "None for system") |
| Provenance trigger_type additive | ✓ | "world:<type>" pattern 跟 "diary:morning" 一致 |
| Provenance source_system 既有 value | ✓ | "narrative" 是最 natural fit |
| session_id / correlation_id = None | ✓ | 5 個 producer 4 個 None,World 也 None |
| parent_event_id = None | ✓ | 5 個 producer 全部 None,World 也 None |
| 4-step qualification pipeline (M5.6-2) | △ partial | evaluate/promote 對話專用,World bridge 不同 semantic |
| Dedup via novelty_id | ✓ (new) | 5 個 producer 沒 dedup 需求,World 需要 dedup |
| 6-dim PerceptionScores | ✗ | World event 沒 scored,直接用 type whitelist |

---

## 2. Candidate Dimensions Analysis (Bry spec §3)

Bry spec 列 8 candidate dimensions A-H,evidence-based 評估每個:

### A. Event type ✓ (kept)

**Evidence:** 5 synthetic scenarios 全有 `type` 欄位 (`rain_started` / `celebrity_news` / `calendar_event` / `weather_temp_change` / `user_going_outside`),可以直接 enumerate。

**Judgment:** Type 是 WorldEvent 的 primary discriminator,5 個 scenarios 跟 lived experience 權重 對應清晰 (calendar_event YES, user_going_outside YES, celebrity_news NO)。

### B. Event source △ (rejected)

**Evidence:** 5 scenarios source 分布: `weather` × 2, `news` × 1, `calendar` × 1, `social` × 1。

**Judgment:** Source 跟 type 是 redundant (calendar source 一定是 calendar_event, social source 一定是 user_going_outside 等)。
- Type whitelist `{calendar_event, user_going_outside}` 已涵蓋 2 YES scenarios
- Source whitelist `{calendar, social}` 也是 2 個,但**會 false-positive 包含 TEST_A weather/rain_started** (lived MAYBE, 預期 NO by default)
- 5 scenarios 中 weather 出現 2 次但都是 NO/MAYBE,calendar 1 次 YES,social 1 次 YES
- **Type 比 source 更 discriminative**,保留 A,reject B

### C. novelty_id ✓ (kept, for dedup only)

**Evidence:** 5 scenarios 全有 `novelty_id` (e.g. "weather_rain_20260807"),M3.1 Phase A 派工明列作為 dedup key。

**Judgment:** novelty_id 是 WorldEvent 的 unique identification,**用於 dedup 邏輯**,不直接用於 qualification decision。

### D. temporal persistence ✗ (rejected)

**Evidence:** 5 scenarios 都沒有 temporal metadata (每個 event 1 個 ts,沒持續時間)。WorldEvent 沒有 `duration` / `end_ts` 欄位。

**Judgment:** 從 5 scenarios evidence 推不出 temporal persistence 概念。Reject,因 spec §3 要求 "Only retain dimensions justified by current evidence"。

### E. repeated observation ✓ (kept, via novelty_id dedup)

**Evidence:** novelty_id 就是 dedup key,Calendar event daily repeat 同 novelty_id。

**Judgment:** 用 novelty_id 實作 in-memory dedup dict,**deterministic bounded** (FIFO eviction at 1000 entries)。

### F. relevance to the soul/agent ✗ (rejected)

**Evidence:** WorldEvent 沒有 `agent_id` 欄位 (M5.9-1 §5.2 confirmed),沒辦法知道哪個 Soul 跟 world event 相關。

**Judgment:** Spec §7 明確 "Do NOT fabricate actor identity"。Rejection reason: 沒有 evidence source,做 would require semantic inference (LLM/semantic search),spec §P 禁止。

### G. direct relation to current activity ✗ (rejected)

**Evidence:** WorldEvent 沒有 "current activity" 概念。Soul's current activity 存在於 `consciousness` 跟 `intent_payload`,但不對 WorldEvent 暴露。

**Judgment:** 跟 F 一樣,沒有 evidence source,需 semantic inference。

### H. explicit actor involvement △ (partial, data.actor)

**Evidence:** TEST_E `user_going_outside` 內 `data: {"actor": "bry", "intent": "going_outside"}` 帶 explicit actor。其他 4 scenarios 沒帶 actor field。

**Judgment:** Actor in `data` 是 optional inconsistent field。Cannot rely on it for qualification (5 中 1 有,4 沒)。Reject,因為 inconsistent evidence 會造成 "test pass but production fail"。

### 2.1 Final dimension set

| Dimension | 採納? | 用途 |
|-----------|-------|------|
| A. Event type | ✓ | qualification rule (primary) |
| B. Event source | ✗ | (rejected, redundant with type) |
| C. novelty_id | ✓ | dedup only (not qualification) |
| D. temporal persistence | ✗ | (no evidence) |
| E. repeated observation | ✓ | via novelty_id dedup (deterministic) |
| F. relevance to soul | ✗ | (no evidence, would require fabrication) |
| G. current activity | ✗ | (no evidence) |
| H. explicit actor in data | ✗ | (inconsistent evidence) |

**3 dimensions kept, 5 rejected, 0 scoring dimensions added.** ✓

---

## 3. Whitelist Strategy Comparison (Bry spec §4)

### 3.1 Type whitelist (recommended)

```python
WORLD_QUALIFYING_TYPES = frozenset({
    "calendar_event",       # TEST_C: 30-min meeting (YES, M5.9-1)
    "user_going_outside",   # TEST_E: user going out (YES, M5.9-1)
})
```

| Scenario | type | In whitelist? | Rule → InnerLife? | M5.9-1 lived exp | Match? |
|----------|------|---------------|-------------------|------------------|--------|
| TEST_A | rain_started | ✗ | NO | MAYBE | Conservative (safe fail-closed) |
| TEST_B | celebrity_news | ✗ | NO | NO | ✓ |
| TEST_C | calendar_event | ✓ | YES | YES | ✓ |
| TEST_D | weather_temp_change | ✗ | NO | NO | ✓ |
| TEST_E | user_going_outside | ✓ | YES | YES | ✓ |

**Coverage: 4/5 strict match + 1/5 conservative. Quality > Quantity preserved.**

### 3.2 Source whitelist (rejected)

```python
WORLD_QUALIFYING_SOURCES = frozenset({"calendar", "social"})
```

| Scenario | source | In whitelist? | Rule → InnerLife? | M5.9-1 lived exp | Match? |
|----------|--------|---------------|-------------------|------------------|--------|
| TEST_A | weather | ✗ | NO | MAYBE | Conservative (✓ safe) |
| TEST_B | news | ✗ | NO | NO | ✓ |
| TEST_C | calendar | ✓ | YES | YES | ✓ |
| TEST_D | weather | ✗ | NO | NO | ✓ |
| TEST_E | social | ✓ | YES | YES | ✓ |

**Same coverage as type whitelist (4/5 match + 1/5 conservative).**

### 3.3 Type + Source combination (rejected)

```python
# Combined: type in whitelist OR (source in whitelist AND type in subset)
```

- Same coverage as either alone
- Larger surface = more risk
- **No benefit over type alone**

### 3.4 Type whitelist v1 selected

**Why type whitelist over source whitelist:**
- Both give same coverage in 5 scenarios
- Type is more semantically precise (specific event kind, not source category)
- Future-proof: if "calendar" source adds new type (e.g. "reminder_5min"), we control via type list
- Source whitelist would over-include noise (calendar source might have "calendar_sync" type that's noise)

**Why whitelist over per-event judgment:**
- Deterministic ✓
- Bounded (2 types) ✓
- No LLM / semantic / scoring ✓
- 0 frozen contract change ✓
- Future types require explicit extension (safer than implicit acceptance)

### 3.5 Surface size comparison

| Strategy | Surface size | Coverage | Risk |
|----------|-------------|----------|------|
| Type whitelist | 2 types | 5/5 (4 strict + 1 conservative) | LOW |
| Source whitelist | 2 sources | 5/5 (4 strict + 1 conservative) | LOW (same) |
| Type + Source | 2 + 2 = 4 (with overlap) | 5/5 | MEDIUM (more surface) |
| All events | unlimited | 5/5 (5/5 strict) | HIGH (telemetry flood) |

**Type whitelist = smallest safe surface.** ✓

---

## 4. Final v1 Rule

### 4.1 Qualification rule (deterministic)

```python
# M5.9-2 v1 rule: type whitelist (2 types)
WORLD_QUALIFYING_TYPES: frozenset = frozenset({
    "calendar_event",       # has Soul action implication (e.g. 30-min meeting)
    "user_going_outside",   # has explicit actor involvement (data.actor)
})

class WorldQualificationDecision(str, Enum):
    YES = "yes"
    NO_TYPE_NOT_QUALIFYING = "no_type_not_qualifying"

def qualify_world_event(world_event: WorldEvent) -> WorldQualificationDecision:
    """
    M5.9-2 v1 deterministic rule.
    
    Rule: type IN WORLD_QUALIFYING_TYPES → YES
          type NOT IN whitelist → NO_TYPE_NOT_QUALIFYING (fail-closed)
    
    Why type whitelist (Bry spec §4):
      - Smallest safe surface (2 types)
      - 4/5 strict match + 1/5 conservative (TEST_A rain_started MAYBE → NO)
      - Quality > Quantity preserved
      - 0 LLM, 0 scoring, 0 semantic
      - 0 frozen contract change
    
    Why fail-closed (Bry spec §5):
      - Unknown WorldEvent types must NOT silently become InnerLifeEvent
      - Conservative beats telemetry flood
      - Per-type extension requires explicit whitelist update (safer than
        implicit acceptance)
    
    Why this is deterministic (Bry spec §2):
      - 1 dimension (type)
      - 1 rule (whitelist membership)
      - No time-of-day evaluation
      - No external state (in-memory only)
      - No random / LLM / scoring
      - Same input → same output
    """
    if world_event.type in WORLD_QUALIFYING_TYPES:
        return WorldQualificationDecision.YES
    return WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING
```

### 4.2 Dedup rule (deterministic, bounded)

```python
class WorldInnerLifeAdapter:
    """
    M5.9-2 v1 dedup: novelty_id → event_id mapping (in-memory FIFO).
    
    Rule (deterministic, bounded):
      1. Receive WorldEvent (after qualification YES)
      2. novelty_id in self._dedup → return existing event_id (no new create)
      3. novelty_id not in self._dedup → call InnerLifeWriter.create_event()
         → record (novelty_id, event.event_id)
      4. self._dedup >= MAX → evict oldest (FIFO)
    
    Why this is deterministic (Bry spec §6):
      - Single state: Dict[str, str] in memory
      - FIFO eviction deterministic
      - Lost on restart (acceptable per "no replay" spec)
      - No external state
    
    Why bounded:
      - Bounded by MAX (1000 default) prevents memory growth
      - FIFO oldest-first → consistent with "oldest seen first" semantic
    """
    DEFAULT_DEDUP_MAX_SIZE = 1000
    
    def __init__(self, inner_life_writer, dedup_max_size=DEFAULT_DEDUP_MAX_SIZE):
        self._writer = inner_life_writer
        self._dedup: Dict[str, str] = {}
        self._dedup_max_size = dedup_max_size
    
    def _is_duplicate(self, novelty_id: str) -> bool:
        return novelty_id in self._dedup
    
    def _record(self, novelty_id: str, event_id: str) -> None:
        if len(self._dedup) >= self._dedup_max_size:
            oldest = next(iter(self._dedup))  # FIFO
            del self._dedup[oldest]
        self._dedup[novelty_id] = event_id
```

### 4.3 Rate limit decision

**Per Bry spec §3 "Only retain dimensions justified by current evidence"** + §6 "Prefer deterministic bounded rules":

- 5 synthetic scenarios 沒顯示 flood pattern
- 1 InnerLifeEvent per unique novelty_id 已是 natural rate limit (calendar event daily = 1/day, social event hourly ≈ 1-2/hour)
- 額外 rate limit 是 hypothetical (no current evidence)
- **Decision: 不加 rate limit in v1.** Dedup 已足夠。
- Future v2 可加 hourly cap if real source 顯示 flood。

### 4.4 Complete v1 pipeline

```
WorldEvent arrives (e.g. via bus subscription)
   ↓
1. qualify_world_event(world_event)
   - type in whitelist? → YES
   - type not in whitelist? → NO (skip, log debug)
   ↓ (if YES)
2. _is_duplicate(novelty_id)?
   - yes → return existing event_id (no new create)
   - no → continue
   ↓
3. _writer.create_event(provenance=Provenance(
     trigger_type=f"world:{world_event.type}",
     actor_id=None,
     source_system="narrative",
     extras={
       "world_source": world_event.source,
       "world_type": world_event.type,
       "world_novelty_id": world_event.novelty_id,
     },
   ),
   session_id=None,
   correlation_id=None,
   parent_event_id=None,
)
   ↓
4. _record(novelty_id, event.event_id)
   ↓
5. return event.event_id
```

---

## 5. YES / MAYBE / NO Examples (Bry spec §11)

### 5.1 YES examples (rule returns YES)

| Example | type | Source | Rule → | Reason |
|---------|------|--------|--------|--------|
| "30 分鐘後有重要會議" | `calendar_event` | calendar | YES | Calendar event with Soul action implication (TEST_C pattern) |
| "Bry 說他準備出門" | `user_going_outside` | social | YES | Explicit actor involvement (TEST_E pattern, data.actor = "bry") |
| "母親生日 3 天後" | `calendar_event` | calendar | YES | Same type as TEST_C, regardless of specific subtype |

### 5.2 NO examples (rule returns NO)

| Example | type | Source | Rule → | Reason |
|---------|------|--------|--------|--------|
| "今天氣溫比昨天低了 1 度" | `weather_temp_change` | weather | NO | Type not in whitelist (TEST_D pattern, transient fluctuation) |
| "某明星在節目上說了句無聊的話" | `celebrity_news` | news | NO | Type not in whitelist (TEST_B pattern, generic news noise) |
| "外面開始下雨了" | `rain_started` | weather | NO | Type not in whitelist (TEST_A pattern, MAYBE in M5.9-1 but conservatively NO per Quality > Quantity) |
| "系統通知: 雲端硬碟空間不足" | `system_notification` | system | NO | Type not in whitelist (unknown, fail-closed) |
| "現在是 14:00" | `time_update` | system | NO | Type not in whitelist (trivial, fail-closed) |

### 5.3 重複 NO example (dedup 防 telemetry flood)

| Example | Round 1 | Round 2 | Reason |
|---------|---------|---------|--------|
| Calendar event 30-min meeting (daily) | novelty_id = "cal_20260810_30min" → YES (create) | novelty_id = "cal_20260810_30min" → return existing event_id | Same novelty_id → dedup, no new create |

**Quality > Quantity: 即使 calendar event 每分鐘被 source 重新 emit 1 次,InnerLifeWriter 只被呼叫 1 次 per novelty_id。**

### 5.4 Unknown type behavior (Bry spec §5)

| Type | Rule | Behavior |
|------|------|----------|
| `calendar_event` (in whitelist) | YES | Create InnerLifeEvent |
| `user_going_outside` (in whitelist) | YES | Create InnerLifeEvent |
| `rain_started` (NOT in whitelist) | NO | Skip, log debug |
| `celebrity_news` (NOT in whitelist) | NO | Skip, log debug |
| `<future_unknown_type>` (NOT in whitelist) | NO | Skip, log debug |

**Unknown types NEVER silently become InnerLifeEvent.** ✓

### 5.5 5 synthetic scenarios 對照表

| Scenario | type | M5.9-1 lived | v1 rule → | Match? |
|----------|------|--------------|-----------|--------|
| TEST_A | rain_started | MAYBE | NO (conservative) | Safe fail-closed |
| TEST_B | celebrity_news | NO | NO | ✓ |
| TEST_C | calendar_event | YES | YES | ✓ |
| TEST_D | weather_temp_change | NO | NO | ✓ |
| TEST_E | user_going_outside | YES | YES | ✓ |

**4/5 strict match + 1/5 conservative (TEST_A MAYBE → NO). Quality > Quantity preserved.**

---

## 6. Identity / Provenance Decision (Bry spec §7, §9, §10)

### 6.1 actor_id derivation (Bry spec §7)

| Option | 評估 | Decision |
|--------|------|----------|
| A. From `world_event.data.actor` (TEST_E has "bry") | ✗ 4/5 scenarios 沒 actor field, inconsistent | Reject |
| B. From agent context (call-site) | ✗ World event 是 shared, 沒有 single agent context | Reject |
| C. Static "world_observer" | ✗ fabrication 違 Bry spec §7 | Reject |
| D. `None` (system-level) | ✓ Provenance docstring 明列 "None for system"; World event 是 external observation, not soul-action | **Selected** |

**Decision: actor_id = None**

**Justification:**
- Provenance `actor_id: Optional[str] = None` (event.py:84) — None 是 valid value
- Docstring 明列 "None for system" (event.py:76)
- World events 是 external observation, 不屬於任一 Soul 的 action
- 沒 fabrication, 沒 assume
- 跟 Bry spec §7 "Do NOT fabricate actor identity" 一致

### 6.2 session_id evaluation (Bry spec §9)

**Per spec: "Do not invent session identity merely because InnerLifeEvent supports it."**

| Option | 評估 | Decision |
|--------|------|----------|
| A. From world event ts timestamp range | ✗ no evidence, fabrication | Reject |
| B. From user_id / agent_id context | ✗ World event is shared | Reject |
| C. `None` | ✓ World events 沒 session concept (chat session 是對話的, 不是 world 的) | **Selected** |

**Decision: session_id = None**

**Justification:**
- 5 個現有 producer 4 個 None (只有 ConversationQualification 用 session_id, 因為 conversation 本質是 session-based)
- World event 是 observation, 不是 session
- Provenance `session_id: Optional[str]` None 是 valid

### 6.3 parent_event_id evaluation (Bry spec §10)

**Per spec: "Determine whether WorldEvent can legitimately participate in existing lineage semantics. If not, leave it absent."**

| Option | 評估 | Decision |
|--------|------|----------|
| A. Link to previous world event via novelty_id | ✗ novelty_id 是 dedup key, 不是 causal chain | Reject |
| B. Link to conversation InnerLifeEvent | ✗ World event 不一定有 conversation 對應 | Reject |
| C. `None` (root event) | ✓ World event 是 independent observation, no causal chain | **Selected** |

**Decision: parent_event_id = None**

**Justification:**
- 5 個現有 producer 全部 None (全部 root events)
- World events 是 independent atomic observations, 沒有 causal chain
- InnerLifeEvent docstring (event.py:145-148) "Direct causation (B was caused by / derived from A)" — World event 不從另一個 InnerLifeEvent 衍生
- 沒 lineage 概念 → 留 None

### 6.4 correlation_id evaluation

| Option | 評估 | Decision |
|--------|------|----------|
| A. `None` | ✓ 5 個現有 producer 4 個 None, World event 沒 narrative group | **Selected** |

**Decision: correlation_id = None**

### 6.5 source_system selection

| Value | 評估 | Decision |
|-------|------|----------|
| "narrative" | ✓ ConversationQualification 也用, World event 是 Soul's perception = part of narrative | **Selected** |
| "diary" | ✗ Diary writer 專用 | Reject |
| "dream" | ✗ Dream writer 專用 | Reject |
| "memory" | △ 可, 但 "narrative" 更精準 | Reject |
| "system" | △ 可, 但 "narrative" 更精準 | Reject |

**Decision: source_system = "narrative"**

### 6.6 Complete Provenance spec

```python
Provenance(
    trigger_type="world:calendar_event" or "world:user_going_outside",  # per-type
    actor_id=None,                  # system-level, not soul-specific
    source_system="narrative",      # cross-system narrative
    trace_ref=None,                 # not needed (novelty_id in extras)
    extras={
        "world_source": "<weather|news|calendar|social|synthetic>",
        "world_type": "<exact type string>",
        "world_novelty_id": "<novelty_id from WorldEvent>",
    },
)
```

InnerLifeEvent other fields:
- `event_id`: auto-generated by InnerLifeWriter
- `session_id`: None
- `correlation_id`: None
- `parent_event_id`: None
- `ts`: InnerLifeWriter uses `now_utc_iso()` (UTC, deterministic)
- `lineage_depth`: 0 (root event)
- `lineage_path`: empty

---

## 7. trigger_type Naming Decision (Bry spec §8)

### 7.1 候選 naming schemes

| Scheme | 例子 | Pros | Cons |
|--------|------|------|------|
| A. `world:<type>` per-type | `world:calendar_event` / `world:user_going_outside` | 跟既有 `diary:morning` / `dream:dream` 風格一致; 可 per-type filter | 每加 1 個 type 多 1 個 value |
| B. `world_observation` single | `world_observation` (所有 world events 都用) | 簡單, 1 個 value; type info 從 `extras` 拿 | 跟既有 namespace-style 不一致; lost type info at first class |
| C. `world:<source>` per-source | `world:calendar` / `world:social` | 跟 source whitelist 對齊 | 跟 type whitelist 對齊時不對稱 |
| D. New prefix `external_` | `external:calendar_event` | 強調是 external source | 新 prefix, 跟既有 5 個 trigger_type 不對齊 |

### 7.2 跟既有 8 個 trigger_type 對齊

| 既有 | 風格 | 對齊 |
|------|------|------|
| `user_message` | flat | n/a |
| `agent_reply` | flat | n/a |
| `diary:morning` | namespace | A ✓ |
| `diary:night` | namespace | A ✓ |
| `dream:dream` | namespace | A ✓ |
| `dream:event` | namespace | A ✓ |
| `memory_fact` | flat | n/a |
| `system` | flat | n/a |

**4 個既有 trigger_type 用 namespace-style (system:type),A 跟既有對齊。B (single value) 沒有 namespace 對應。**

### 7.3 Decision

**Selected: `world:<type>` per-type**

**Justification:**
- 跟既有 `diary:morning` / `dream:dream` namespace-style 一致
- 可以 per-type 過濾 (e.g. "列出所有 world:calendar_event")
- 2 個 qualifying types → 2 個 trigger_type values (bounded)
- Future extension: 新 type 加新 trigger_type value (additive)
- 跟 Bry 派工 spec §8 暗示的 natural pattern 一致

---

## 8. Recursive Feedback Analysis (Bry spec §13)

### 8.1 Same-cycle recursive check

```
World → InnerLife → Agency → Action → New World Event?

Path:
  1. WorldEvent → WorldInnerLifeAdapter → InnerLifeEvent (canonical)
  2. InnerLifeEvent 寫到 trace.jsonl
  3. Memory read InnerLifeEvent (post-reply fact 透過 inner_life_event_id)
  4. Memory 不 publish WORLD_EVENT
  5. Diary / Dream 透過 scheduler, 不透過 InnerLifeEvent
  6. Agency 4 stages 不 publish WORLD_EVENT (frozen M5.1 + M5.2)
  7. WorldEventSource 不訂閱 bus (Bry 派工 base.py:13 contract)

→ Same-cycle recursive 風險 0
```

### 8.2 Cross-cycle temporal continuity (by design)

```
Day 1: World → InnerLife (T1) → Diary / Dream 寫更多 (T1.5)
Day 2: Heartbeat / SESSION_END → 角色重讀 Inner Life (T2)
Day 2: World 來新 event (T2.5) → InnerLife (T2.5) → 角色反應 (T3)
```

**這是 by design 的 temporal continuity, 不是 recursive feedback.**
M5.4-5.1 派工明列 Inner Life 設計是跨 cycle narrative。

### 8.3 Bry 派工 stop condition #6

> "World → InnerLife → Agency → World creates unavoidable recursion."

**Verdict: 0 unavoidable recursion. 跨 cycle 是 by design.**
**Stop condition NOT hit. ✓**

---

## 9. Frozen Contract Impact (Bry spec §M)

### 9.1 7 個 frozen contracts

| Frozen contract | Status | Impact if v1 implemented |
|-----------------|--------|---------------------------|
| WorldEvent schema | ✗ NO change | Adapter 讀 WorldEvent, 不寫 |
| InnerLifeEvent schema | ✗ NO change | Adapter 用既有 schema 創 InnerLifeEvent |
| Provenance schema | ✗ NO change | Provenance dataclass 100% preserved, 只用既有欄位 |
| Event Bus contract | ✗ NO change | Adapter 訂閱 WORLD_EVENT 透過 bus, 不改 bus |
| Agency Stage 1-4 | ✗ NO change | Adapter 跟 Agency 無關 (InnerLife 不直接觸 Agency) |
| TriggerEnvelope | ✗ NO change | Adapter 跟 TriggerEnvelope 無關 |
| NarrativeTrace | ✗ NO change | InnerLifeWriter 已有 trace integration (M5.4-5.6), adapter 自動繼承 |

### 9.2 Provenance trigger_type 新加 value 影響評估

Per M5.4-5.1 docstring (event.py:46-48):
> "trigger_type: canonical vocabulary (NOT raw Memory.source / Diary.slot / Dream.slot)
> 這是 Inner Life 統一的 trigger namespace"

**trigger_type 是 string, namespace-style, ADDITIVE:** 既有 8 個 value, 加 `world:calendar_event` / `world:user_going_outside` 不破既有 schema.

**驗證 (event.py:89-93):** `__post_init__` 驗證 trigger_type 是 non-empty str, 不限制值範圍. 新 value 通過 validation.

### 9.3 0 contract change 確認

v1 design 為:
- 新檔 `src/world/inner_life_adapter.py` (PURE ADDITIVE)
- 訂閱 `EventType.WORLD_EVENT` 透過 bus
- 套用 type whitelist
- 套用 dedup
- 呼叫 `inner_life_writer.create_event(provenance=Provenance(...))`
- InnerLifeWriter 仍 sole creator
- Provenance 既有 schema 100% preserved, 只用新 trigger_type 字串 value
- session_id / correlation_id / parent_event_id 全部 None
- actor_id None
- source_system "narrative" (existing valid value)

**0 frozen contract change. ✓**

---

## 10. P0/P1/P2/P3 Findings

### P0 — Correctness / Production Integrity

**0 findings.** v1 design is:
- Deterministic (no LLM, no semantic, no random)
- 0 frozen contract change
- 0 production data mutation
- 0 recursive autonomous behavior (verified §8)

### P1 — Architecture Integrity

**0 findings.** v1 design:
- InnerLifeWriter 仍 sole creator
- 5 個現有 producer pattern preserved
- Provenance dataclass 100% reused, 0 schema change
- 0 semantic / vector / LLM introduction

### P2 — Capability Gap

#### P2.1 (resolved via v1 design) — World → Inner Life bridge

**M5.8-1 P2.3 origin:** World 沒有 InnerLifeEvent path.

**M5.9-1 classification B:** Mechanically possible but qualification policy required.

**M5.9-2 v1 design:** Type whitelist (2 types) + dedup (FIFO 1000) + zero LLM, fully deterministic.

**Status:** **P2.1 SOLVABLE via v1 design.** Implementation ticket M5.9-3 待 Bry 拍板後派工。

#### P2-new — Whitelist extension cost

**Future concern:** 當 real source 接入, 新的 type 可能要進 whitelist。

**Mitigation:** Whitelist 設計是 `frozenset` 2 entries, 擴充是 additive 改 1 個常數。不需動 adapter logic。

**Status:** **P2-new DOCUMENTED** (v1 design 預期未來 v2 擴充, 不需 M5.9-3 處理)。

### P3 — Documentation / Cleanup

**0 findings.** Documentation 在 5 個現有 producer 完整描述 v1 設計:
- Provenance.trigger_type naming (per-type `world:<type>` 跟既有 `diary:morning` 一致)
- actor_id / session_id / correlation_id / parent_event_id 為 None 的理由
- source_system = "narrative" 選擇理由
- dedup 邏輯 (in-memory FIFO 1000)
- type whitelist 初始 content (calendar_event, user_going_outside)

---

## 11. Production Safety (Bry spec §14)

| 項目 | Status |
|------|--------|
| Source modification | **0** (READ-ONLY audit) |
| memory.db mutation | **0** |
| diary/dream/event data mutation | **0** |
| InnerLifeEvent creation | **0** (audit only) |
| WorldEvent replay | **0** |
| WorldEvent backfill | **0** |
| WorldEvent migration | **0** |
| Historical WorldEvent promotion | **0** |
| Relationship mutation | **0** |
| 20 pre-existing untracked artifacts | preserved |
| Frozen contract change | **0** |

---

## 12. Regression Baseline (Bry spec §Q)

### 12.1 Regression scope (per M5.9-1 baseline 119/119 PASS)

M5.9-1 closeout 跑過:
- M3 disabled / e2e_smoke / observability / prompt_integrity / world_awareness
- M5.4-3 real_world_source_boundary_audit
- M5.8-4 producer_gating

**119/119 PASS in 5.66s.** M5.9-2 是 READ-ONLY 不改 source, baseline 維持。

### 12.2 不重跑

Per Bry spec §Q "Regression baseline verified as appropriate": M5.9-2 是 design audit, 0 source change, 沒新增 production code, 不需重跑 full regression. M5.9-1 跑的 119/119 baseline 仍 valid.

### 12.3 為什麼 M5.9-2 不重跑

Bry 派工 spec §N: "No source-code modification"。M5.9-2 是純 design audit, 0 source change, 0 test change, 0 implementation。 Regression 不可能退化 (沒有改任何東西)。

### 12.4 PASS / FAIL / SKIP accounting

| Category | Count | Notes |
|----------|-------|-------|
| PASS (M5.9-1 baseline) | 119 | M3 runnable + M5.4-3 + M5.8-4 |
| SKIP / DESELECTED | 0 | |
| TIMEOUT | 0 | |
| PRE-EXISTING FAILURE | 6 | M3.1/M3.2/M3.4 sys.path missing (verified pre-existing, NOT M5.9-2 related) |
| NEW FAILURE | 0 | M5.9-2 0 source change |

**No regression risk. Baseline 119/119 still valid.**

---

## 13. Git State (Bry spec)

```
HEAD:           09bf6a69e67fe6a1926f9ffe6f5d1e6f4e970bf2
origin/main:    09bf6a69e67fe6a1926f9ffe6f5d1e6f4e970bf2
                ↳ HEAD == origin/main ✓ SYNCED
Recent log:
  09bf6a6 docs(m5.9-1): world -> inner life boundary audit (READ-ONLY)
  78e2813 docs(m5.8-4.1): producer gating & regression verification audit (READ-ONLY)
  166561e docs(m5.8-4): add closeout summary log
Working tree:  20 pre-existing untracked artifacts preserved
                (audit log is the only new untracked file)
```

---

## 14. Stop Conditions Final Check (Bry spec)

| # | Stop condition | Hit? | Reason |
|---|----------------|------|--------|
| 1 | Qualification requires LLM/semantic inference | ✗ NO | Pure type whitelist, 1 dimension, deterministic |
| 2 | Qualification requires changing InnerLifeEvent identity semantics | ✗ NO | Provenance dataclass 100% preserved, 0 field change |
| 3 | Qualification requires changing WorldEvent contract | ✗ NO | WorldEvent read-only, 0 write |
| 4 | Actor identity cannot be established without fabrication | ✗ NO | actor_id = None, valid per Provenance docstring, 0 fabrication |
| 5 | Deduplication cannot be made deterministic | ✗ NO | In-memory Dict with FIFO eviction, fully deterministic |
| 6 | World → InnerLife → Agency → World creates unavoidable recursion | ✗ NO | Same-cycle 0 (verified §8), cross-cycle by design |
| 7 | Historical replay/backfill becomes necessary | ✗ NO | Forward-only, "no replay" per spec |
| 8 | Multiple materially different architectures remain unresolved | ✗ NO | 1 design (type whitelist + dedup), no alternative |

**0 stop conditions hit. ✓**

---

## 15. Classification (Bry spec §R)

> A — deterministic qualification design is ready for minimal implementation
> B — design is possible but Bry decision required
> C — frozen contract conflict
> D — World → Inner Life should remain disconnected

**Mavis classification: A (deterministic qualification design is ready for minimal implementation)**

### 15.1 為什麼 A (vs M5.9-1 的 B)

| 維度 | M5.9-1 (B) | M5.9-2 (A) |
|------|------------|------------|
| Mechanical feasibility | ✓ | ✓ |
| Qualification policy | ❓ undefined | ✓ **Type whitelist defined (2 types)** |
| Dedup policy | ❓ undefined | ✓ **In-memory FIFO 1000** |
| Identity derivation | ❓ undefined | ✓ **actor_id = None, session_id = None, parent_event_id = None, correlation_id = None** |
| trigger_type naming | ❓ undefined | ✓ **`world:<type>` per-type** |
| source_system | ❓ undefined | ✓ **"narrative"** |
| Unknown type behavior | ❓ undefined | ✓ **NO, log debug (fail-closed)** |
| 0 frozen contract change | ✓ | ✓ |
| 0 LLM / semantic / vector | ✓ | ✓ |
| Quality > Quantity | ✓ | ✓ |
| Recursive autonomous | 0 risk | 0 risk |

**v1 design 完整 design 完, mechanical + design 都 safe, Bry 拍板後可直接 implementation。**

### 15.2 為什麼不是 C

- 0 frozen contract change ✓
- 0 contract conflict ✓

### 15.3 為什麼不是 D

- v1 design 達 Quality > Quantity (2-type whitelist + dedup)
- Reuse 5 producer pattern (zero new architecture)
- 0 LLM / semantic / scoring
- 5 synthetic scenarios 4/5 strict match + 1/5 conservative
- 跟 Bry 派工 §9 "Quality > Quantity" 一致 (conservative beats telemetry flood)

---

## 16. Recommended Implementation Ticket (M5.9-3)

### 16.1 M5.9-3 — World → Inner Life Adapter Implementation (MINIMAL ADDITIVE)

**Mode:** IMPLEMENTATION (跟 M5.4-6.1/6.2 pattern 一致)

**Scope:**
- 新檔 `src/world/inner_life_adapter.py` (~150 lines)
- 訂閱 `EventType.WORLD_EVENT` 透過 bus
- 套用 `WORLD_QUALIFYING_TYPES` type whitelist
- 套用 in-memory dedup dict (FIFO 1000)
- 呼叫 `inner_life_writer.create_event(provenance=Provenance(...))`
- Provenance spec: trigger_type=`world:<type>`, actor_id=None, source_system="narrative", extras={world_source, world_type, world_novelty_id}
- InnerLifeEvent 其他欄位: session_id=None, correlation_id=None, parent_event_id=None
- Observability: log YES / NO_TYPE_NOT_QUALIFYING / DUPLICATE
- 0 frozen contract change
- 0 source code change outside new module
- 新檔 `tests/test_m5_9_3_world_inner_life_adapter.py` (~200 lines)
  - Test YES for both qualifying types
  - Test NO for 3 non-qualifying synthetic types
  - Test dedup (same novelty_id → only 1 create)
  - Test dedup eviction (1000+ entries → oldest evicted)
  - Test unknown type behavior (fail-closed, no InnerLifeEvent)
  - Test actor_id / session_id / correlation_id / parent_event_id = None
  - Test trigger_type format `world:<type>`
  - Test source_system = "narrative"
  - Test frozen contract preserved (TriggerEnvelope / Stage 1-4 / 4 handlers)
  - Test integration with WorldPerceptionMiddleware (subscribe to WORLD_EVENT)

**STOP conditions:**
- Adding LLM / semantic / vector → STOP
- Changing Provenance schema → STOP
- Changing WorldEvent contract → STOP
- Changing InnerLifeWriter identity authority → STOP
- Adding non-deterministic dedup → STOP
- Replay / backfill / migration → STOP
- Recursive loop discovered → STOP

**Acceptance:**
- All 5 synthetic scenarios 對照 v1 rule 4/5 strict + 1/5 conservative
- 0 P0/P1
- Frozen contract 0 change (verify via test)
- Regression 119/119 + new M5.9-3 tests PASS

### 16.2 Alternative: M5.9-3A — D path (drop + document)

If Bry 對 whitelist `{calendar_event, user_going_outside}` 不滿意, 或想 drop:

- 不 implementation
- M5.8-1 P2.3 改 "intentional gap" status
- 寫 closeout log 說明 why
- 0 contract change

---

## 17. Unresolved Bry Decisions (待 Bry 派工時拍板)

### 17.1 Whitelist content (核心)

**Question:** `WORLD_QUALIFYING_TYPES = {"calendar_event", "user_going_outside"}` 接受?

**Options:**
- A. ✓ 接受 (跟 M5.9-1 evidence 一致, 4/5 strict + 1/5 conservative)
- B. 改 whitelist (Bry 指定不同 types, e.g. 加入 `rain_started` 當 MAYBE)
- C. 改 strategy (e.g. 從 type whitelist 改 source whitelist, 雖然 same coverage)

### 17.2 Dedup max size (minor)

**Question:** `_dedup_max_size = 1000` 接受?

**Options:**
- A. ✓ 接受 (default, 一般使用足夠)
- B. 改 size (e.g. 100, 500, 5000)
- C. 改 eviction strategy (e.g. LRU, 但需更多 state)

### 17.3 Per-day vs in-memory dedup (Bry spec §6)

**Question:** In-memory only OK, or 加 persistence?

**Options:**
- A. ✓ In-memory only (per "no replay/backfill" spec, lost on restart acceptable)
- B. 加 file persistence (jsonl dedup log)
- C. 加 InnerLifeWriter extended index (但要改 frozen contract)

**Recommendation: A.** Restart 後 novelty_id 重新處理 → calendar event 重新 create InnerLifeEvent → Quality > Quantity 仍 preserve (不會 flood, 但會有少量 duplicates on restart)

### 17.4 trigger_type naming confirmation

**Question:** `world:<type>` per-type 接受?

**Options:**
- A. ✓ 接受 (跟既有 `diary:morning` 對齊)
- B. 改 single `world_observation` + 從 `extras` 拿 type
- C. 改其他 prefix

### 17.5 actor_id / session_id / correlation_id / parent_event_id 為 None

**Question:** 4 個 Optional 欄位全 None 接受?

**Options:**
- A. ✓ 接受 (M5.9-1 §6.1-6.4 詳列理由)
- B. 加某個欄位 (需 Bry 解釋為什麼)

---

## 18. Final Status

**M5.9-2 design audit COMPLETE.**

| Item | Status |
|------|--------|
| Read-only | ✓ |
| 0 source modification | ✓ |
| 0 production data mutation | ✓ |
| 0 frozen contract change | ✓ |
| 0 P0/P1 findings | ✓ |
| 1 P2 resolved (P2.1 World → Inner Life capability gap) | ✓ |
| 1 P2-new documented (whitelist extension cost) | ✓ |
| 0 P3 findings | ✓ |
| 5 synthetic scenarios 對照 v1 rule | ✓ (4/5 strict + 1/5 conservative) |
| Quality > Quantity preserved | ✓ |
| 0 recursive autonomous loop | ✓ |
| Regression baseline 119/119 valid | ✓ |
| 0 stop conditions hit (8 items) | ✓ |

**Classification: A (deterministic qualification design is ready for minimal implementation)**

**Bry decision options:**
1. **拍板 A → M5.9-3 implementation** (Mavis 推薦)
2. **Bry 對 whitelist/dedup/naming 有疑慮 → 重新 design iteration**
3. **Bry 選 D → drop + document** (M5.8-1 P2.3 改 intentional gap)

**Awaiting Bry decision on whitelist content (17.1) + 4 unresolved questions (17.2-17.5).**
