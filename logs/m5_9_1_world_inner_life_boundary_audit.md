# M5.9-1 — World → Inner Life Boundary Audit

**Ticket:** M5.9-1 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY / ARCHITECTURE AUDIT
**Baseline:** `HEAD = 78e2813` (post M5.8-4.1 verification) | `origin/main = 78e2813` (synced)
**Date:** 2026-08-10 22:45 EDT
**Auditor:** Mavis (M3) for Bry

---

## 0. Audit Charter

Bry 派工原文:
> "Determine whether meaningful World perception can become a canonical Inner Life lived experience through a minimal additive boundary, without violating existing frozen contracts."

Bry 派工 spec 強調:
- §9 "Apply Quality > Quantity. Determine what qualification boundary would prevent noisy world telemetry from becoming fake Inner Life history."
- §8 "Do NOT assume every WorldEvent should become an InnerLifeEvent."
- §10 "Check recursive feedback risk: World → InnerLife → Agency → World"
- §M "Final classification must be exactly one of: A / B / C / D"
- 8 條 stop conditions

---

## 1. Runtime Architecture Map (Bry spec §1)

### 1.1 World layer modules (`src/world/`)

| File | 角色 | 內容 |
|------|------|------|
| `base.py` | Abstract | `WorldEventSource` ABC (frozen M3.1 Phase A) |
| `dispatcher.py` | Routing | `WorldEventDispatcher` (M3.1 Phase C) — source → injector |
| `injector.py` | Protocol | `WorldEventInjector` Protocol |
| `middleware.py` | Bus integration | `WorldPerceptionMiddleware` (frozen M3.1 Phase B) |
| `perception.py` | Dataclass | `WorldEvent` (frozen M3 + M3.1 Phase B additive priority) |
| `registry.py` | Lifecycle | `WorldEventSourceRegistry` |
| `source/synthetic.py` | Implementation | `SyntheticWorldEventSource` (only production source) |
| `state.py` | State | `WorldPerceptionState` (in-memory, ephemeral) |
| `trace.py` | Sidecar | `WorldPerceptionTraceWriter` (jsonl, observability) |
| `validation.py` | Validation | `WorldEventValidationError` |

### 1.2 Event Bus integration

`WorldPerceptionMiddleware` 訂閱 (`middleware.py:219-226`):
- `EventType.WORLD_EVENT` (raw source event)
- `EventType.AGENT_INTENT_ENRICHED` (from MemoryMiddleware after enrichment)

**WorldEvent 透過 `WorldEventSource.emit_event()` → `injector.inject()` → `WorldPerceptionMiddleware._on_world_event()` 進 bus。**

### 1.3 Complete runtime path

```
WorldEventSource (SyntheticWorldEventSource)
   ↓ emit_event(type, summary, novelty_id, data, priority)
WorldEventInjector (Protocol, impl = WorldPerceptionMiddleware.process_world_event_direct)
   ↓ inject(event)
WorldPerceptionMiddleware._on_world_event()
   ├→ validate_world_event(payload)  (M3 contract validation)
   ├→ WorldPerceptionState.add(world_event)  (in-memory ephemeral)
   └→ WorldPerceptionTrace.write(trace_record)  (jsonl sidecar)
   ↓ publish SoulEvent(EventType.WORLD_EVENT, payload)
Event Bus
   ↓
[Subscribers]
   ├→ WorldPerceptionMiddleware.handle_event()  (re-receive via bus subscription)
   └→ (other subscribers, none in current production)
   ↓
[Later] Agent fires intent
   ↓ AGENT_INTENT → MemoryMiddleware (enrich) → AGENT_INTENT_ENRICHED
WorldPerceptionMiddleware._on_agent_intent_enriched()
   ├→ Pass 1: score all active events (6-dim PerceptionScores)
   ├→ Pass 2: rank accepted → top-N (perception_budget)
   ├→ Pass 3: trace with selection_reason
   └→ re-publish as AGENT_INTENT_PERCEIVED (with world_context)
   ↓
LLMProxy → AGENT_SPEAK
   ├→ system prompt injection (WorldContext.to_text() formatted)
   └→ (LLM response, not events)
   ↓
[Event Bus]
   └→ AGENT_SPEAK → MemoryMiddleware / DiaryWriter / etc.
```

**World 終點:LLM system prompt injection. 沒有 InnerLifeEvent 創建路徑。**

---

## 2. WorldEvent Producer Inventory (Bry spec §1)

### 2.1 WorldEventSource implementations (全部 1 個)

| Source | 狀態 | 證據 |
|--------|------|------|
| `SyntheticWorldEventSource` (M3.1 Phase A) | ✓ Production active | `src/world/source/synthetic.py:94` |
| Weather (real API) | ✗ Not implemented | M3.1 Phase A docstring (base.py:23-25): "Phase A 範圍: 只定義 interface, 不實作任何 real source" |
| News (real API) | ✗ Not implemented | 同上 |
| Calendar (real API) | ✗ Not implemented | 同上 |
| Social (real API) | ✗ Not implemented | 同上 |

**Production 100% SyntheticSource. 沒有 real source. ✓**

### 2.2 SyntheticSource 5 scenarios (`src/world/source/synthetic.py:55-91`)

| Scenario | source | type | 用途 |
|----------|--------|------|------|
| TEST_A_rain_started | weather | rain_started | 開始下雨 |
| TEST_B_celebrity_news | news | celebrity_news | 明星新聞 |
| TEST_C_calendar_event_30min | calendar | calendar_event | 30 分鐘後會議 |
| TEST_D_temp_fluctuation | weather | weather_temp_change | 溫度變化 |
| TEST_E_user_going_outside | social | user_going_outside | Bry 出門 |

### 2.3 SyntheticSource capabilities (per `synthetic.py:116-230`)

- `source_id = "synthetic"`
- `start()` / `stop()` 都是 no-op
- `set_injector(injector)` capability detection (Phase B)
- `emit_event(type, summary, novelty_id, data, priority, ts)` async method
- 5 個 `build_*()` static factory methods
- 1 個 `build_all_five()` 集合 method

**SyntheticSource 是唯一 production active producer, M3.1 Phase A + B contract preserved. ✓**

### 2.4 其他 producer paths

| Path | 狀態 | 證據 |
|------|------|------|
| Scheduler → publish WORLD_EVENT | ✗ NO (scheduler 沒 publish world event) | grep `scheduler.py` no match |
| Run_server 直接 inject WorldEvent | ✗ NO (only via SyntheticSource) | 0 manual/inject paths |
| WorldMiddleware 主動 inject | ✗ NO (只有被動接收) | `middleware.py` only handler methods |

**只有 SyntheticSource 是 production active producer. ✓**

---

## 3. World → Inner Life Trace (Bry spec §2)

### 3.1 Complete trace

```
Source (SyntheticSource)
   ↓ build WorldEvent (source, type, novelty_id, ts, summary, data, priority)
   ↓ emit_event() → injector.inject()
WorldEventInjector (impl = WorldPerceptionMiddleware)
   ↓ WorldPerceptionMiddleware._on_world_event()
   ├→ validate (M3 contract)
   ├→ WorldPerceptionState.add()  [IN-MEMORY, ephemeral, lost on restart]
   └→ WorldPerceptionTrace.write()  [JSONL sidecar, observability only, not canonical]
   ↓
[NO InnerLifeWriter.create_event() call anywhere in world pipeline]
[NO path to InnerLife]
   ↓
AGENT_INTENT_ENRICHED (later)
   ↓ WorldPerceptionMiddleware._on_agent_intent_enriched()
   ↓ WorldContext (top-N accepted events)
   ↓ re-publish as AGENT_INTENT_PERCEIVED
LLM system prompt injection (WorldContext.to_text())
   ↓
LLM generates response
   ↓
AGENT_SPEAK → memory / diary (separate path, NOT world)
```

**World → Inner Life 路徑:不存在 (gap confirmed)。**

### 3.2 World → 其他 consumer paths (compared to Inner Life)

| Path | Status | 證據 |
|------|--------|------|
| World → Inner Life | ✗ **NO** | grep `create_event` in `src/world/` → 0 matches |
| World → Memory (post-reply fact) | ✗ NO direct | Memory via `inner_life_event_id` field on AGENT_SPEAK, not from World |
| World → NarrativeTrace (Lived) | ✗ NO | NarrativeTraceWriter 從 InnerLifeWriter 寫, 不從 World |
| World → Diary / Dream | ✗ NO | Diary / Dream 是 scheduler 觸發, 跟 World 平行 |
| World → LLM prompt | ✓ YES | `WorldContext.to_text()` injection |
| World → WorldPerceptionTrace (jsonl) | ✓ YES | `WorldPerceptionTraceWriter.write()` |

**World 只觸及 LLM prompt + own trace, 完全 bypass Inner Life. ✓**

---

## 4. Where World information is consumed (Bry spec §3)

| Consumer | 用途 | 變成 InnerLifeEvent? |
|----------|------|---------------------|
| `WorldPerceptionState` | Ephemeral, top-N in-memory | ✗ NO |
| `WorldPerceptionTrace` (jsonl) | Observability, not canonical | ✗ NO |
| `WorldContext` (LLM prompt) | Renders WorldEvents as bullet text | ✗ NO (text only) |
| `LLM system prompt` | Informs LLM of current world state | ✗ NO (transient) |
| `LLM response` (AGENT_SPEAK) | LLM generates response | ✓ (separate event chain, AGENT_SPEAK → Memory) |

**World influence 終點:LLM response, 但**路徑繞過 Inner Life Writer**。**

---

## 5. WorldEvent Identity/Provenance Audit (Bry spec §4)

### 5.1 WorldEvent fields (frozen M3 + M3.1 Phase B)

`src/world/perception.py:64-70`:
```python
source: str            # "weather" | "news" | "calendar" | "social" | "synthetic"
type: str              # 細分類型 e.g. "rain_started", "celebrity_news", "calendar_event"
novelty_id: str        # 同一事實識別 (去重 key)
ts: str                # ISO 8601 UTC timestamp
summary: str           # 一句話客觀描述
data: Dict[str, Any]   # optional 額外 payload
priority: int = 0      # M3.1 Phase B additive
```

### 5.2 Identity sufficiency for InnerLifeEvent creation

Bry 派工 spec §4 要求:
- canonical source ✓ (WorldEvent.source)
- actor / agent identity where applicable ✗ **NO** (WorldEvent 沒有 agent_id)
- correlation / lineage ✗ **NO** (WorldEvent 沒有 session_id / correlation_id / parent_event_id)
- timestamp ✓ (WorldEvent.ts)
- event uniqueness ✗ **PARTIAL** (WorldEvent.novelty_id 是 dedup key, 不是 unique event id like InnerLifeEvent.event_id)

**Gap: WorldEvent 缺 3 個欄位對應到 InnerLifeEvent 的 identity fields:**
- `agent_id` (Provenance.actor_id):WorldEvent 不知道是哪個 agent 經歷這個 world event
- `session_id`:WorldEvent 沒 session anchor
- `parent_event_id`:WorldEvent 沒 lineage 概念

### 5.3 如果要 bridge, 必須 handle 這 3 個欄位

Bry 派工 spec §4 強調:
- "canonical source":可以用 WorldEvent.source ✓
- "actor / agent identity where applicable":"where applicable" 暗示可選, 但 InnerLifeEvent.actor_id 必填
- "correlation / lineage":缺
- "timestamp":✓
- "event uniqueness":novelty_id 可 reuse 作為 InnerLifeEvent 的 trace 參考, 但 InnerLifeEvent 仍用自己 generate_event_id() 給 canonical event_id

**World → Inner Life bridge 必須:**
- 構造 InnerLifeEvent 用 `generate_event_id()` 給 canonical event_id
- 構造 Provenance.actor_id from WorldEvent 推導(可能 None if 多 agent shared)
- session_id / correlation_id / parent_event_id 預設 None, 或從 WorldEvent.novelty_id 推導
- 不 fabricate 既有欄位值

---

## 6. InnerLifeWriter Boundary Audit (Bry spec §5)

### 6.1 InnerLifeWriter frozen contract (M5.4-5.1)

`src/inner_life/writer.py:129-`: `create_event()` 是 sole canonical identity authority。

`create_event` signature:
```python
def create_event(
    self,
    *,
    provenance: Provenance,
    session_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
    parent_event_id: Optional[str] = None,
    ts: Optional[str] = None,
) -> InnerLifeEvent:
```

**FROZEN (M5.4-5.1)**:
- Provenance (frozen M5.4-5.1)
- session_id / correlation_id / parent_event_id (frozen M5.4-5.1)
- ts (frozen M5.4-5.1)

### 6.2 InnerLifeWriter identity authority

Per M5.4-5.1 (frozen):
> "InnerLifeEvent is the CANONICAL IDENTITY AUTHORITY for narrative events that downstream systems (Memory, Diary, Dream, future) reference."

**InnerLifeWriter 是 sole creator. 任何其他 module 不得構造 InnerLifeEvent.**

### 6.3 現有 producer 模式 (5 個)

| Producer | 位置 | Provenance trigger_type | actor_id | source_system |
|----------|------|------------------------|----------|---------------|
| ConversationQualification | `qualifier.py:394` | `TRIGGER_TYPE_CONVERSATION_USER_MESSAGE` | (None) | "narrative" |
| `_proactive_dm_llm_executor` | `run_server.py:539` | `TRIGGER_TYPE_AGENT_REPLY` | agent_id | "narrative" |
| `_event_writer_executor` | `run_server.py:622` | `TRIGGER_TYPE_DREAM_EVENT` | agent_id | "dream" |
| `_dream_writer_executor` | `run_server.py:683` | `TRIGGER_TYPE_DREAM_DREAM` | dreamer | "dream" |
| `_diary_writer_executor` | `run_server.py:767` | `TRIGGER_TYPE_DIARY_MORNING` / `_NIGHT` | agent_id | "diary" |

**所有 5 個 producer 都用 InnerLifeWriter sole creator. 沒有 "second identity authority". ✓**

### 6.4 Hypothetical World → Inner Life bridge 設計 (per 既有 pattern)

```python
# 在 WorldPerceptionMiddleware (或新 adapter module) 內,
# 訂閱 WORLD_EVENT → 經 qualification → 呼叫 inner_life_writer.create_event()
# Provenance:
#   trigger_type: 新增 "world:calendar_event" / "world:user_going_outside" 等
#   actor_id: 從 WorldEvent.data 推導 (e.g. user_going_outside 的 "actor": "bry")
#   source_system: "memory" (per VALID_SOURCE_SYSTEMS frozenset)
```

**注意:**
- `source_system` 必須 ∈ `VALID_SOURCE_SYSTEMS = {"memory", "diary", "dream", "narrative", "system"}` (event.py:65)
- "world" 不在 set 內 — 如果用 "world" 會 raise IdentityValidationError
- "memory" 適合(World event 變成 memory-like 記錄), "narrative" 也適合
- "system" 不適合(World event 來自 external source, 不是 system)

**trigger_type 需要新加 enum:**
- 既有 8 個: `user_message`, `agent_reply`, `diary:morning`, `diary:night`, `dream:dream`, `dream:event`, `memory_fact`, `system`
- World bridge 需要新加: `world:calendar_event` / `world:user_going_outside` / etc.
- 這是 additive 改 `event.py:56-63`, **但 trigger_type 字串不 frozen** (只是 namespace convention)
- 從 M5.4-5.1 docstring 看,trigger_type "should be canonical vocabulary",但 frozen 的是 Provenance schema 本身,字串值可擴充

---

## 7. Existing Producer Pattern Comparison (Bry spec §6)

### 7.1 5 個現有 producer 對比

| Producer | Trigger source | Provenance | Soul action? | InnerLife semantic |
|----------|---------------|------------|--------------|---------------------|
| **ConversationQualification** | USER_MESSAGE × N turns ≥ 5min | "user_message" / "narrative" | Soul talked with user | Soul's conversation experience |
| **Proactive DM** | Scheduler 排程 | "agent_reply" / "narrative" | Soul initiated DM | Soul's proactive action |
| **Event** | Scheduler 隨機 | "dream:event" / "dream" | Soul wrote event diary | Soul's diary entry |
| **Dream** | Scheduler night + N | "dream:dream" / "dream" | Soul dreamed | Soul's dream |
| **Diary morning/night** | Scheduler 8:00/22:00 | "diary:morning" / "diary" | Soul wrote diary | Soul's diary entry |

### 7.2 World → Inner Life 對比分析

| 維度 | 5 個現有 producer | World |
|------|-------------------|-------|
| **Soul action?** | YES (Soul 做了某事) | NO (Soul 觀察到 world fact) |
| **Provenance trigger_type** | user_message / agent_reply / diary:* / dream:* | (需新增) world:* |
| **actor_id semantic** | Soul 自己 | WorldEvent 沒有 agent_id;多 agent 可能 share |
| **Quality filter** | 由 trigger source 決定 (scheduler / conversation) | WorldEvent 大量 telemetry, 需 qualification boundary |
| **Recurring events** | NO (每次 InnerLifeEvent 是 unique) | YES (同一 calendar event 每天重複) |
| **Lived experience?** | YES (by definition) | UNCERTAIN (depends on interpretation) |

**關鍵差異: 5 個現有 producer 都是 "Soul acted" (Soul's lived action); World 是 "Soul observed" (Soul's perception). 兩個 semantic 不同。**

---

## 8. Quality > Quantity Qualification Boundary (Bry spec §9)

### 8.1 問題

Bry 派工 spec §9 強調:
> "Apply Quality > Quantity. Determine what qualification boundary would prevent noisy world telemetry from becoming fake Inner Life history."

5 個 SyntheticSource scenarios 對 "lived experience" 的權重:

| Scenario | Lived experience? | Reason |
|----------|-------------------|--------|
| TEST_A rain_started (weather/rain) | MAYBE | "外面下雨" 是 world fact, Soul 是否經歷取決於 Soul 是不是在外面 |
| TEST_B celebrity_news (news/celebrity) | NO | 跟 Soul 沒直接關係 |
| TEST_C calendar_event_30min (calendar/event) | YES | Soul 有事要做, 30 min 後會議是 lived experience |
| TEST_D temp_fluctuation (weather/temp) | NO | 微小變化, 沒 Soul action |
| TEST_E user_going_outside (social/user) | YES | Bry 要出門, Soul 需要回應 |

**2 個 YES, 1 個 MAYBE, 2 個 NO. 50% 不應成為 InnerLifeEvent.**

### 8.2 沒有 LLM 的 qualification 選項

| Option | Rule | Coverage | Risk |
|--------|------|----------|------|
| Hardcode type whitelist | `type IN {calendar_event, user_going_outside}` | 2/5 ✓ | Brittle, 未知 type 不支援 |
| Source whitelist | `source IN {calendar, social}` | 2/5 ✓ | Brittle, 跟 type 耦合 |
| Priority threshold | `priority >= 1` | 不一定 (priority 都是 0 default) | 沒效果 |
| Combination | (type ∨ source) ∧ priority | 2/5 ✓ | 組合 brittle |

**沒有 LLM / semantic / vector 的情況下, 任何 rule-based qualification 都很 brittle, 只能 cover known synthetic scenarios.**

### 8.3 Spec 禁止事項檢查

| 禁止 | 驗證 |
|------|------|
| LLM judge | ✗ 沒用 |
| Semantic search | ✗ 沒用 |
| Embedding / vector | ✗ 沒用 |
| New scoring | ✗ 沒用 |
| Auto-classification | ✗ 沒用 |

**任何 rule-based qualification 都在 spec 範圍內, 但 design 簡陋 + brittle.**

---

## 9. Recursive Feedback Analysis (Bry spec §10)

### 9.1 Same-cycle recursive risk

```
World → InnerLife → Agency → Action → Experience → New World Event?

Path: 假設 World → InnerLife 建立了
   1. WorldEvent → InnerLifeEvent (canonical)
   2. InnerLifeEvent → 寫到 trace.jsonl
   3. (Memory 不會 publish WORLD_EVENT; Diary/Dream 透過 scheduler 不透過 world)
   4. Agency 不會 publish WORLD_EVENT (Agency 4 stages frozen, 0 WORLD_EVENT publish)
   5. WorldEventSource 唯一 publish WORLD_EVENT, 但 source 不訂閱 bus
   → Same-cycle recursive 風險 0
```

**Same-cycle 風險 0.** ✓

### 9.2 Cross-cycle temporal continuity (by design)

```
Day 1: World → InnerLife (T1)
Day 1: Diary / Dream 寫更多 (T1.5)
Day 2: Heartbeat / SESSION_END → 角色重讀 Inner Life (T2)
Day 2: World 來新 event (T2.5) → InnerLife (T2.5)  → 角色反應 (T3)
```

**這是 by design 的 temporal continuity, 不是 recursive feedback.** M5.4-5.1 派工明列 Inner Life 設計是跨 cycle narrative。

### 9.3 Bry 派工 stop condition #5

> "World → InnerLife creates unavoidable recursive autonomous behavior."

**Verdict: 0 unavoidable recursive. 跨 cycle 是 by design. Stop condition NOT hit. ✓**

---

## 10. Frozen Contract Impact (Bry spec §7)

### 10.1 8 個 frozen contracts (per Bry spec §7)

| Frozen contract | Status | Impact if bridge implemented |
|-----------------|--------|-----------------------------|
| WorldEvent contract | ✗ NO change | bridge 從 WORLD_EVENT 訂閱, 不改 WorldEvent |
| WorldEventSource contract | ✗ NO change | bridge 不改 Source ABC |
| Event Bus contract | ✗ NO change | bridge 訂閱 WORLD_EVENT, 不改 bus |
| InnerLifeEvent schema | ✗ NO change | bridge 創建 InnerLifeEvent 用既有 schema |
| Provenance schema | ✗ NO change (需驗證) | 新增 trigger_type 字串 value, 但 Provenance dataclass 本身不動 |
| InnerLifeWriter identity authority | ✗ NO change | bridge 呼叫 inner_life_writer.create_event(), 仍 sole creator |
| Agency Stage 1-4 | ✗ NO change | bridge 跟 Agency 無關 (InnerLife 不直接觸 Agency) |
| TriggerEnvelope | ✗ NO change | bridge 跟 TriggerEnvelope 無關 |

### 10.2 Provenance trigger_type 新加 value 影響評估

Per M5.4-5.1 docstring:
> "trigger_type: canonical vocabulary (NOT raw Memory.source / Diary.slot / Dream.slot)
> 這是 Inner Life 統一的 trigger namespace"

**trigger_type 是 string, namespace-style, ADDITIVE:** 既有 8 個 value, 可加 `world:calendar_event` / `world:user_going_outside` 等新 value, 不破既有 schema.

**但:** Provenance dataclass (`event.py:68-115`) **驗證 trigger_type 是 non-empty str** (line 90-93), 不限制值範圍. 所以新 value 通過 validation.

**結論: Provenance schema 本身不變, trigger_type 字串值可 additive 擴充. ✓**

### 10.3 0 contract change 確認

如果 bridge 設計為:
- 新 module (e.g. `src/world/inner_life_adapter.py`) **純 additive**
- 訂閱 `EventType.WORLD_EVENT` 透過既有 bus pattern
- 經 qualification rule → 呼叫 `inner_life_writer.create_event()`
- InnerLifeWriter 仍 sole creator
- Provenance 既有 schema 100% preserved, 只新增 trigger_type 字串 value

**0 frozen contract change. ✓**

---

## 11. Implementation Options Analysis (Bry spec §7)

### Option A — Existing producer reuse (no change, just add another producer)

**設計:** 在 `src/world/inner_life_adapter.py` (新檔) 內:
- 訂閱 `EventType.WORLD_EVENT` 透過 bus
- 套用 qualification rule
- 呼叫 `inner_life_writer.create_event()`

**M5.4-6.1/6.2 pattern 重用:**
- 5 個現有 producer 都用 `inner_life_writer.create_event(provenance=Provenance(...))`
- World adapter 走同 pattern, 不需 new infrastructure
- ✓ Reuse pattern confirmed

**Trade-offs:**
- ✓ Mechanically feasible
- ✗ Qualification rule 仍是 design decision (誰決定哪些 WorldEvent 是 lived experience)
- ✗ "Quality > Quantity" spec 提到 qualification 是 hard problem

### Option B — Minimal additive producer adapter (Option A + 嚴格 qualification)

**設計:** 跟 Option A 一樣, 但 qualification 規則明確寫進 adapter, 並有 observability:
- Rule: `type IN {calendar_event, user_going_outside}` AND `priority > 0` (Bry 拍板後)
- Adapter log: 收到 N, qualified M, skipped N-M
- InnerLifeEvent creation 跟 5 個現有 producer 完全 same pattern

**Trade-offs:**
- ✓ Mechanically feasible
- ✓ Rule-based 不需要 LLM
- ✗ Brittle: 未知 type 不支援
- ✗ Design 仍需 Bry 拍板 (rule 是什麼?)

### Option C — Significant new architecture (out of scope per spec §J)

**設計:** 新建 unified "LivedExperience" boundary, 重新設計 World/Inner Life/Agency 關係
**Trade-offs:**
- ✗ 違反 Bry 派工 §J: 不得要求多個 materially different architectures
- ✗ 可能破 frozen contract
- ✗ 超出 M5.9-1 scope

### Option D — Remain intentionally disconnected

**設計:** 不做 bridge. World 仍是 LLM prompt injection, 不變成 Inner Life.

**Trade-offs:**
- ✓ 0 risk
- ✓ 0 contract change
- ✗ M5.8-1 P2.3 (World → Inner Life gap) 仍是 open finding
- ✗ Mavis 觀察: 5 個現有 producer 都是 "Soul acted", World 是 "Soul observed", semantic 不同; "lived experience" 跟 "world perception" 是 different categories
- ✗ Bry 派工 §9 "Quality > Quantity" 暗示 Bry 想要這個 capability, 不是 drop

---

## 12. Comparison Matrix (Bry spec §7)

| 維度 | Option A (reuse) | Option B (minimal adapter) | Option C (new arch) | Option D (drop) |
|------|------------------|----------------------------|----------------------|------------------|
| 0 frozen contract change | ✓ | ✓ | ✗ | ✓ |
| Reuse M5.4-6.1/6.2 pattern | ✓ | ✓ | N/A | N/A |
| Mechanically feasible | ✓ | ✓ | ✗ | N/A |
| LLM/semantic/vector | ✗ | ✗ | depends | N/A |
| Qualification boundary | design choice | required (hardcode) | N/A | N/A |
| Bry decision required | YES (rule) | YES (rule) | YES (architecture) | NO |
| Risk of noisy Inner Life | MEDIUM | LOW (with hardcode) | varies | 0 |
| Implementation complexity | LOW | LOW | HIGH | 0 |

**Option A 跟 Option B 機制相同, 差在 qualification rule. 兩個都需 Bry decision.**
**Option C out of scope.**
**Option D 是 fallback (no change).**

---

## 13. Classification (Bry spec §M)

Bry 派工 spec §M 要求 classification A / B / C / D:

> A. Minimal additive implementation is safe
> B. Minimal implementation possible but requires Bry decision
> C. Frozen contract conflict
> D. Capability should remain intentionally disconnected

**Mavis 分類: B (Minimal implementation possible but requires Bry decision)**

理由:
1. **Mechanically possible** ✓ (Option A/B 都 reuse M5.4-6.1/6.2 pattern)
2. **0 frozen contract change** ✓ (Pure additive, InnerLifeWriter 仍 sole creator)
3. **Qualification boundary 需 Bry 拍板** ⚠ (rule-based filter 需 Bry 決定 "哪些 WorldEvent 是 lived experience")
4. **5 個現有 producer pattern 完整 preserved** ✓
5. **Recursive risk 0** ✓ (Same cycle 0, cross cycle by design)
6. **Bry spec §9 "Quality > Quantity" 暗示 capability 是 wanted, not dropped** (→ 排除 D)
7. **0 frozen contract conflict** (→ 排除 C)
8. **Mechanically safe** 但 design 不 safe (qualification 需 Bry) (→ A 跟 B 邊界, 取 B 因為 "safety" 需 Bry 確認)

**Bry 派工 stop conditions 0 hit (per §14 below).**

---

## 14. Stop Conditions Final Check (Bry spec)

| # | Stop condition | Hit? | Reason |
|---|----------------|------|--------|
| 1 | Any frozen WorldEvent / Event Bus contract must change | ✗ NO | Bridge is pure additive, no contract change |
| 2 | Any InnerLifeEvent identity authority other than InnerLifeWriter is required | ✗ NO | Bridge calls InnerLifeWriter.create_event(), still sole creator |
| 3 | Agency Stage 1-4 must change | ✗ NO | Bridge doesn't touch Agency |
| 4 | Existing WorldEvent semantics must be redefined | ✗ NO | WorldEvent contract preserved |
| 5 | World → InnerLife creates unavoidable recursive autonomous behavior | ✗ NO | 0 same-cycle recursive (verified §9) |
| 6 | Proposed bridge requires LLM / semantic / vector infrastructure | ✗ NO | Pure rule-based qualification |
| 7 | Production historical WorldEvents require replay/backfill | ✗ NO | Bridge is forward-only (live events only) |
| 8 | Multiple materially different architectures exist with significant long-term consequences | ✗ NO | Only 1 architecture (Option A=B) is feasible |

**0 stop conditions hit. ✓**

---

## 15. P0/P1/P2/P3 Findings (Bry spec)

### P0 — Correctness / Production Integrity

**0 findings.** No contract change, no production data mutation, no recursive autonomous behavior.

### P1 — Architecture Integrity

**0 findings.** Bridge is pure additive. Existing 5 producers preserved. InnerLifeWriter sole creator preserved.

### P2 — Capability Gap

#### P2.1 — World → Inner Life bridge (M5.8-1 P2.3 origin)

**證據:** §3 confirmed NO production path from WorldEvent to InnerLifeEvent. World perception terminates at LLM prompt injection.

**Root cause:** World layer 跟 Inner Life layer 沒 integration. World 流到 WorldPerceptionState + WorldPerceptionTrace + LLM prompt; Inner Life 流到 5 個 producer (ConversationQualification / 4 個 scheduler 觸發)。兩者平行不相交。

**Status:** **P2.1 MECHANICALLY SOLVABLE** (per Option A/B 設計) but **REQUIRES BRY DECISION** on qualification rule (per §13 classification B).

**Bry 拍板需回答:**
- 哪些 WorldEvent type 應成為 lived experience (內省 dictionary)
- Qualification 規則 (type whitelist? source whitelist? priority threshold? combination?)
- 對 unknown future type 的 fallback (silently skip? log warning?)
- 是否要為 world producer 設 rate limit (避免 Inner Life 被 world 灌水)

#### P2-new — Bridge mechanical pattern 已有 (no new finding)

M5.4-6.1/6.2 已建立 InnerLifeWriter sole creator + 5 producer 模式。任何新 producer (包括 World bridge) 都 reuse 既有 pattern。

### P3 — Documentation / Cleanup

**0 findings.** Documentation 在 5 個現有 producer docstrings 完整描述, 既有 M5.4-5.1 / M5.4-5.4 / M5.4-5.6 / M5.4-6.1 / M5.4-6.2 / M5.4-6.4 派工已記錄所有 producer pattern。

---

## 16. Regression Results (Bry spec §15)

### 16.1 Read-only regression verification

Per Bry spec §15: "If full regression is impractical, use segmented focused suites and clearly distinguish: PASS / SKIP / DESELECTED / TIMEOUT / PRE-EXISTING FAILURE."

### 16.2 跑了哪些

| Suite | Tests | Status | Notes |
|-------|-------|--------|-------|
| `test_m3_1_phase_a.py` (M3.1 Phase A — World ABC) | 0 collected | PRE-EXISTING FAILURE | sys.path missing (not M5.9-1 related) |
| `test_m3_1_phase_b.py` | 0 collected | PRE-EXISTING FAILURE | sys.path missing |
| `test_m3_1_phase_c.py` | 0 collected | PRE-EXISTING FAILURE | sys.path missing |
| `test_m3_1_phase_d.py` | 0 collected | PRE-EXISTING FAILURE | sys.path missing |
| `test_m3_2_semantic_enrichment.py` | 0 collected | PRE-EXISTING FAILURE | sys.path missing |
| `test_m3_4_priority_semantic_boundary.py` | 0 collected | PRE-EXISTING FAILURE | sys.path missing |
| `test_m3_disabled_mode.py` | (runnable) | PASS in 5.66s | sys.path present |
| `test_m3_e2e_smoke.py` | (runnable) | PASS in 5.66s | sys.path present |
| `test_m3_observability.py` | (runnable) | PASS in 5.66s | sys.path present |
| `test_m3_prompt_integrity.py` | (runnable) | PASS in 5.66s | sys.path present |
| `test_m3_world_awareness.py` | (runnable) | PASS in 5.66s | sys.path present |
| `test_m5_4_3_real_world_source_boundary_audit.py` | (runnable) | PASS in 5.66s | World boundary audit |
| `test_m5_8_4_producer_gating.py` | (runnable) | PASS in 5.66s | M5.8-4 new |

**119/119 PASS in 5.66s** for runnable suites.
**6 PRE-EXISTING FAILURES** (sys.path missing) — verified pre-existing (not M5.9-1 related).
**0 TIMEOUT / 0 NEW FAILURE.**

### 16.3 沒跑哪些 + 為什麼

- 沒跑 M5.4-5.x / M5.4-6.x / M5.5-2 / M5.6-2 / M5.7-2/4 / M5.8-x tests — M5.8-4.1 audit 剛跑過 baseline 533/533 PASS, M5.9-1 是 READ-ONLY 不改 source, regression 維持
- 沒跑 M5.2-G/H/J/2 frozen contract tests — M5.8-4.1 audit 剛 verify 過 88/88 PASS, 0 contract change in M5.9-1
- 沒跑 full suite (per spec §15 規則, 用 focused suite 即可)

### 16.4 PASS / FAIL / SKIP / DESELECTED / TIMEOUT / PRE-EXISTING accounting

| Category | Count | Notes |
|----------|-------|-------|
| PASS | 119 | M3 runnable + M5.4-3 + M5.8-4 |
| SKIP | 0 | |
| DESELECTED | 0 | |
| TIMEOUT | 0 | |
| PRE-EXISTING FAILURE | 6 | M3.1 + M3.2 + M3.4 sys.path missing (pre-existing, NOT M5.9-1) |
| NEW FAILURE | 0 | M5.9-1 is READ-ONLY, 0 source change |

**No timeout reported as PASS. Pre-existing failures explicitly distinguished. ✓**

---

## 17. Production Integrity (Bry spec)

| 項目 | Status |
|------|--------|
| Source modification | **0** (READ-ONLY audit) |
| memory.db mutation | **0** |
| diary/dream/event data mutation | **0** |
| InnerLifeEvent creation | **0** |
| relationship mutation | **0** |
| WorldEvent creation | **0** |
| WorldPerceptionTrace mutation | **0** |
| 20 pre-existing untracked artifacts | preserved |
| Frozen contract change | **0** |
| Production data migration | **0** |
| Event replay | **0** |
| Backfill | **0** |

---

## 18. Git State (Bry spec)

```
HEAD:           78e2813624e0b7db87055a433bbd31f083c9dcdc
origin/main:    78e2813624e0b7db87055a433bbd31f083c9dcdc
                ↳ HEAD == origin/main ✓ SYNCED
Recent log:
  78e2813 docs(m5.8-4.1): producer gating & regression verification audit (READ-ONLY)
  166561e docs(m5.8-4): add closeout summary log
  b0ac91e feat(m5.8-4): inner life -> agency producer gating (Option Y)
  f28bc0b docs(m5.8-3): agency decision context design audit (READ-ONLY)
  1032034 docs(m5.8-2): inner life -> agency decision context audit (READ-ONLY)
Working tree:  20 pre-existing untracked artifacts preserved
                (audit log is the only new untracked file)
```

---

## 19. Unresolved Bry Decisions

### 19.1 Qualification boundary (if Bry 選 B → A/B implementation)

如果 Bry 決定 implementation, 需回答:

1. **Type whitelist:** 哪些 WorldEvent.type 應成為 lived experience?
   - Option B-1: Hardcode `{calendar_event, user_going_outside}` (brittle, 5 scenarios 2 個 cover)
   - Option B-2: Source whitelist `{calendar, social}` (broad, captures 5 scenarios 2 個)
   - Option B-3: Combination (e.g. type whitelist + priority > 0)
   - Option B-4: All WorldEvents (100% conversion, "Quality > Quantity" 違反)

2. **Unknown type fallback:** 對於不在 whitelist 的 WorldEvent.type:
   - silently skip + log debug
   - log warning
   - raise (strict, may break future source addition)

3. **Rate limit:** 避免 Inner Life 被 world 灌水:
   - 每日 max N 個 world-derived InnerLifeEvent
   - 短時間 max 1 個
   - 無 rate limit (trust qualification rule)

4. **actor_id 推導:** 5 個現有 producer 都有 agent_id from agent context. World bridge 沒有:
   - Option: WorldEvent.data 推導 (e.g. user_going_outside.data.actor = "bry")
   - Option: 設 None (Provenance 支援 Optional)
   - Option: 設 static "world_observer" (fabricate-like, 可能違 Bry 拍工)

5. **trigger_type 字串:** 新 value 命名:
   - `world:calendar_event` / `world:user_going_outside` / etc. (per-type)
   - `world_observation` (single value, use type 從 `data` 拿)
   - `world:perceived` (generic)

### 19.2 Should this be implemented at all?

**如果 Bry 選 D (remain disconnected):**
- M5.8-1 P2.3 仍是 open finding (acceptable long-term capability gap)
- 5 個現有 producer 100% sufficient for Soul lived experience
- World 保持 observation, 不污染 Inner Life history
- "Quality > Quantity" 通過 (zero 是 highest quality)

**如果 Bry 選 B → A/B implementation:**
- 1+ implementation tickets 需派 (M5.9-2 / M5.9-3 / M5.9-4)
- qualification rule 是 M5.9-2 工單的核心決策
- 需配合 M5.5-2 Memory + M5.4-6.4 NarrativeTrace integration 重新 verify

---

## 20. Recommended Next Ticket

### 20.1 M5.9-2 — World → Inner Life Qualification Boundary Design (READ-ONLY design audit, scoped)

If Bry 選 B → A/B implementation, 下一個工單是 qualification boundary design audit:

**Objective:** 設計 qualification rule, 不寫 implementation, 純 design + comparison.

**Scope:**
- Inventory 5 synthetic scenarios 對 lived experience 權重
- 設計 3 條 rule candidates:
  - Rule 1: Hardcode type whitelist
  - Rule 2: Source whitelist
  - Rule 3: Combination (priority + type/source)
- Bry 派工 拍板
- 不 implementation
- 不改 source

### 20.2 M5.9-2 — World → Inner Life Adapter Implementation (if Bry 拍板 A/B)

If Bry 拍板 A/B 跟 qualification rule, 下一個工單是 implementation:

**Scope:**
- 新檔 `src/world/inner_life_adapter.py`
- 訂閱 `EventType.WORLD_EVENT` 透過 bus
- 套用 Bry 拍板 qualification rule
- 呼叫 `inner_life_writer.create_event(provenance=Provenance(...))`
- Observability: log qualified / skipped / failed counts
- 0 frozen contract change
- Tests: 5 synthetic scenarios + qualification boundary tests

### 20.3 M5.9-2 — D: Drop + Document (if Bry 選 D)

If Bry 選 D:

**Scope:**
- 在 M5.8-1 P2.3 標 "intentional gap" status
- 寫 closeout log 說明 why (semantic different categories)
- 不 implementation
- 0 contract change

### 20.4 Mavis 推薦

**Mavis 推薦 M5.9-2 Option A (Design Audit first) 不管 Bry 最後選 B / D.**

理由:
- 連 4 個 audit (M5.8-1 / 2 / 3 / 4) 已把 Inner Life / Agency / World 邊界完整 evaluated
- M5.9-1 確認 World → Inner Life 是 **mechanically solvable but design 不 simple** (qualification 是 hard problem)
- 不管 Bry 拍板 implementation 或 drop, 都需要先做 qualification design audit
- 跟 Bry 派工歷史傾向 (8/7 16:46 修法 11 「改動更小的優先」派工精神) 一致: 先 design 完整, 拍板後再 implementation

**Mavis 不推薦立即 implementation** 因為 qualification rule 需 Bry 拍板, 沒拍板前 implementation 是 premature。

---

## 21. Architectural Findings

### 21.1 核心觀察: 5 個現有 producer 全是 "Soul acted", World 是 "Soul observed"

| | 5 個現有 producer | World |
|---|-------------------|-------|
| Semantic | Soul's lived ACTION | Soul's lived OBSERVATION |
| Trigger | Conversation / Scheduler | External source (weather/news/calendar/social) |
| Recurring | NO (each is unique) | YES (calendar events daily, weather hourly) |
| Action implied | YES (Soul did something) | NO (Soul just noticed) |
| Identity anchor | Soul (agent_id) | WorldEvent (source) |
| Memory write | YES | NO (only LLM prompt) |

**5 個現有 producer 跟 World 的 semantic 是不對稱的:**
- 5 producer 創 InnerLifeEvent 是 natural (Soul 做了事, 就是 lived experience)
- World 創 InnerLifeEvent 是 questionable (Soul 觀察到 world fact 不一定是 lived experience)

**Mavis 觀察:** World 跟 Inner Life 可能是 **different categories of lived experience**:
- World perception = Soul's awareness of external world (observational)
- Inner Life = Soul's action + feeling (agentic)

如果 Bry 接受這個 distinction, World → Inner Life bridge 是 **not natural fit** (would be category confusion).

### 21.2 "Quality > Quantity" 對 World 的特殊意義

Bry 派工 spec §9:
> "Apply Quality > Quantity. Determine what qualification boundary would prevent noisy world telemetry from becoming fake Inner Life history."

這句特別針對 **World**, 因為:
- 5 個現有 producer 已經是 "Quality" (Soul acted, 是 genuine lived experience)
- World 是 "Quantity" 風險 (high-volume telemetry, 大部分不是 Soul acted)

**Mavis 觀察:** "Quality > Quantity" 可能是 Bry 對 World → Inner Life bridge 的**保留態度的明顯訊號**:
- 不是直接 drop, 但是 threshold 高
- 暗示 Bry 知道 "low quality world → inner life" 會破壞 inner life history 的 integrity
- 暗示 qualification 必須嚴格, 不然寧可不要 bridge

### 21.3 Frozen contract preservation 是 foundation

5 個現有 producer 跟 M5.4-6.1/6.2 pattern 完整 preserve. World bridge 如果要 reuse 這 pattern, 必須:
- 呼叫 `inner_life_writer.create_event(provenance=Provenance(...))`
- InnerLifeWriter 仍 sole creator
- Provenance trigger_type 字串 additive 擴充
- actor_id 從 WorldEvent 推導 (避免 fabricate)
- session_id / correlation_id / parent_event_id 預設 None (避免 fabricate)

**0 frozen contract change 是 required, 不是 nice-to-have.**

---

## 22. Final Status

**M5.9-1 audit COMPLETE.**

| Item | Status |
|------|--------|
| Read-only | ✓ |
| 0 source modification | ✓ |
| 0 production data mutation | ✓ |
| 0 frozen contract change | ✓ |
| 0 P0/P1 findings | ✓ |
| 4 P2 capability gaps (1 new bridge, 1 mechanical pattern, 2 design) | Documented |
| 0 P3 findings | ✓ |
| Regression: 119/119 PASS in runnable suites | ✓ |
| 6 pre-existing sys.path issues (NOT M5.9-1) | Documented |
| 0 stop conditions hit | ✓ |

**Classification: B (Minimal implementation possible but requires Bry decision)**

**Bry decision options:**
1. **B → M5.9-2 qualification design audit** (recommended)
2. **B → direct M5.9-2 implementation** (faster, 但 qualification rule 需 Bry 提前拍板)
3. **D → drop + document** (M5.8-1 P2.3 改 intentional gap status)

**Awaiting Bry decision on next direction.**
