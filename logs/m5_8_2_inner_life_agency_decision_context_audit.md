# M5.8-2 — Inner Life → Agency Decision Context Audit

**Ticket:** M5.8-2 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY / ARCHITECTURE AUDIT
**Baseline:** `HEAD = 87153cf` (post M5.8-1) | `origin/main = 87153cf` (synced)
**Date:** 2026-08-10 20:55 EDT
**Auditor:** Mavis (M3) for Bry

---

## 0. Audit Charter

Bry 派工的核心問題:

> Can Soul OS currently move from:
>   Experience → Inner Life → Agency Decision → Action
> in a deterministic, observable, contract-safe way?

Bry 派工明確禁止:
- 修改 Stage 1 / 2 / 3 / 4 語意
- 為假設中的未來灑過濾網
- 改 frozen contract
- 引入新 scoring / LLM / vector / 新 memory infra
- 把 audit 擴張成實作

本 audit 的任務:**trace 真實 production runtime**,**不看 comment / dataclass 推論 capability**,
只在 production 真的可以跑通的 path 才算 existing。

---

## 1. Agency Runtime Map (4 stages)

來源:`src/agency/{agency,stages,state,trigger}.py` + 4 handler files
(frozen M5.2 contract, M5.2-G/H 後 production 路徑)

### 1.1 Stage 1 — `check_eligibility(state, now)` (`stages.py:60-82`)

**Input:** `AgencyState` (從 `stages.py:60-82` 接受 5 個 field, 從 `state.py:32-36`)

| 欄位 | 來源 | 用途 |
|------|------|------|
| `last_action_at: Optional[datetime]` | Stage 4 後寫入 (`agency.py:136`) | action cooldown 比對 |
| `last_decision_at: Optional[datetime]` | Stage 2 後寫入 (`agency.py:111`) | decision cooldown 比對 |
| `is_dormant: bool` | 外部 (目前無 producer 寫入) | 拒絕 |
| `is_busy: bool` | 外部 (目前無 producer 寫入) | 拒絕 |
| `action_cooldown_seconds: int = 60` | `state.py:36` 預設值 | action cooldown 計算 |
| `decision_cooldown_seconds: int = 30` | `state.py:37` 預設值 | decision cooldown 計算 |

**Output:** `EligibilityResult(enabled: bool, reason: str)`

**Production 行為:**
- `is_dormant` 永遠 `False` (沒有 producer 寫入)
- `is_busy` 永遠 `False` (沒有 producer 寫入)
- cooldown 邏輯正常運作

### 1.2 Stage 2 — `make_decision(eligibility, perception, state, now, trigger)` (`stages.py:88-166`)

**Input contract** (frozen M5.2-F + M5.2-G):

| 參數 | 來源 | Required? |
|------|------|-----------|
| `eligibility: EligibilityResult` | Stage 1 結果 | ✓ |
| `perception: Optional[Dict[str, Any]]` | 外部 (M3.4 perception layer) | perception-only path 必填 |
| `state: AgencyState` | 構造時注入 | ✓ |
| `now: datetime` | 呼叫方傳入 | ✓ |
| `trigger: Optional[TriggerEnvelope]` | scheduler `_publish_agency_trigger` | trigger-only path 必填 |

**M5.2-G hard rule** (`stages.py:113-117`): perception + trigger 至少一個,否則 `ValueError`。
Bry 拍板 2026-08-08 M5.2-F: 「不要 silent accept」。

**Perception-only path logic** (`stages.py:145-163`):
- `perception.accepted == False` → NO
- `perception.priority <= 0` → NO
- decision cooldown not yet elapsed → NO
- All pass → YES, decision_type="speak"

**Trigger-only path logic** (`stages.py:125-141`):
- decision cooldown not yet elapsed → NO
- Always YES, decision_type="speak"

**Production evidence — 4 handlers all use trigger-only path:**

| Handler | trigger_type | File:line | perception |
|---------|--------------|-----------|------------|
| AgencyTriggerHandler | "proactive_dm" | `trigger_handler.py:94-98` | `None` |
| EventHandler | "event" | `event_handler.py:118-123` | `None` |
| DreamHandler | "dream" | `dream_handler.py:147-152` | `None` |
| DiaryHandler | "morning" \| "night" | `diary_handler.py:149-154` | `None` |

**Grep evidence:**
```
grep "perception\s*=\s*\{" src/  → 0 matches
```
**perception dict 在 production source code 完全沒被構造過**。
Stage 2 的 perception-only path 在 production 是 **dead code** (測試覆蓋,production 沒人 call)。

### 1.3 Stage 3 — `select_action(decision_type)` (`stages.py:172-184`)

**Input:** decision_type (from Stage 2)
**Output:** action_type (1:1 mapping, fallback "speak")
**Frozen contract** — 永遠 deterministic,3 個 decision_type,speak 是 fallback。

### 1.4 Stage 4 — `execute_action_stub(action_type)` (`stages.py:190-201`)

**Frozen STUB** — `executed=True, reason="STUB: would publish AGENT_SPEAK for action_type={action_type}"`
Per M5.1 / M5.2 派工:**永遠 STUB**,production side effect 透過 handler executor 觸發。

### 1.5 Trace 記錄 (`agency.py:69-145`)

每個 stage 都 append `AgencyTraceEntry(timestamp, stage, input, output, reason)` 到 `self._trace`。
Stage 2 的 trace input 記錄:
- `perception_accepted: perception.get("accepted") if perception else None`
- `perception_priority: perception.get("priority") if perception else None`
- `trigger_type: trigger.trigger_type if trigger else None`

**Inner Life 欄位不在 trace input** — Stage 2 不知道 Inner Life 存在。

---

## 2. TriggerEnvelope 完整 audit (frozen M5.2-F)

來源:`src/agency/trigger.py`

### 2.1 Dataclass fields (`trigger.py:26-46`)

```python
@dataclass
class TriggerEnvelope:
    trigger_type: str          # "proactive_dm" | "event" | "dream" | "morning" | "night" | ...
    agent_id: str              # 誰應該 act
    reason: str                # 為什麼現在 (例: "scheduler.proactive_dm")
    elapsed_mins: float = 0.0  # 距上次同類 trigger 的分鐘數
    timestamp: datetime = ...  # optional ISO
    extra: Dict[str, Any] = {}  # trigger-specific context
```

**`extra` 已經是 production 用的 extension point** (M5.2-H Phase 2 派工, Bry 拍板 2026-08-08):
- `dream_handler.py:128,139` 從 `envelope.extra` 拿 `target_agent_id` + `all_agents`
- `_publish_agency_trigger(extra=...)` 從 `src/soul/scheduler.py:177-227` 把 `extra` 透傳到 AGENCY_TRIGGER payload
- `TriggerEnvelope.from_payload` (`trigger.py:48-120`) 從 `payload.extra` 解出 `extra` dict (silent coerce `{}` if not dict)

### 2.2 4 trigger_type × extra convention

| trigger_type | extra convention | 證據 |
|--------------|------------------|------|
| proactive_dm | `{}` (no context) | `test_agency_trigger_negative_path.py:273-282` |
| event | `{}` (no context) | `test_agency_trigger_negative_path.py:286-301` |
| morning | `{}` (no context) | `test_agency_trigger_negative_path.py:304-318` |
| night | `{}` (no context) | `test_agency_trigger_negative_path.py:319-333` |
| dream | `{target_agent_id, all_agents}` | `src/soul/scheduler.py:498-505` + `dream_handler.py:128,139` |

### 2.3 Frozen contract 不變

- `trigger.py:48-120` `from_payload` classmethod **100% preserve** 既有 silent coerce 行為
- 任何 caller 加新 key 到 `payload.extra` 都會 silently 流到 `envelope.extra`
- **沒有人需要改 trigger.py** 來讓 Inner Life context 透過 extra 進 envelope

---

## 3. Inner Life State Audit (what's queryable)

### 3.1 5 個 read-side queryable surfaces (M5.4-5.7 frozen)

來源:`src/inner_life/trace_reader.py:50-49` (M5.4-5.7 派工, 2026-08-09)

| Method | 用途 | 對 Agency 可能 relevance |
|--------|------|--------------------------|
| `query_by_event_id(event_id)` | 找特定 event | Stage 4 executor trace 後驗 |
| `query_by_session_id(session_id)` | 找 session 內所有 event | 過去 N 分鐘 narrative 摘要 |
| `query_by_correlation_id(corr_id)` | 找 narrative group | 對話 session 群組追溯 |
| `query_by_lineage_path_prefix(prefix)` | 找 lineage subtree | 因果鏈追溯 (debug) |
| `query_by_ts_range(start, end)` | 找時間範圍內 event | 近期 narrative state |

**Constraint (frozen):**
- READ-ONLY (`trace_reader.py:9`)
- 沒 DB / embedding / vector / cache
- 缺檔 → empty list
- Malformed JSONL → skip + log warning (不污染 valid records)
- `data_root()` isolation (`trace_reader.py:57-60`)

### 3.2 InnerLifeEvent dataclass (`event.py:122-219`, frozen M5.4-5.1)

```
event_id (str, 32 hex, canonical identity, 不可重用)
session_id (Optional[str], runtime session anchor)
correlation_id (Optional[str], narrative group, NOT causation)
parent_event_id (Optional[str], causation chain)
ts (str, ISO 8601 UTC, immutable)
provenance (Provenance, structured WHO/WHAT/WHERE/WHY)
lineage_depth (int, 0 for root)
lineage_path (str, denormalized for efficient query)
```

**Immutability:** `frozen=True` (`event.py:122`) — modify = create new event with parent_event_id。
**Identity authority:** `InnerLifeWriter` 是 sole creator (`writer.py:102-199`)。
**Validation:** 7 個 validate_* functions in `identity.py`,IdentityValidationError on violation。

### 3.3 Provenance dataclass (`event.py:68-115`, frozen M5.4-5.1)

```
trigger_type (str, canonical vocabulary — "user_message" / "agent_reply" /
              "diary:morning" / "diary:night" / "dream:dream" / "dream:event" /
              "memory_fact" / "system")
actor_id (Optional[str], who caused — bryan / agent_rem / None)
source_system (str ∈ {memory, diary, dream, narrative, system}, 哪個 subsystem)
trace_ref (Optional[str], observability correlation)
extras (Dict[str, str], 全部 value 是 str,extensible)
```

### 3.4 SoulEvent top-level field (frozen M5.4-5.5)

`src/eventbus/schema.py:146-156`:
```python
inner_life_event_id: Optional[str] = Field(
    default=None,
    description=...canonical InnerLifeEvent reference
)
```

**目前的流通** (verified by grep):
1. `consciousness._fire_intent` (`consciousness.py:466-485`): 從 `chrono_payload` 讀 → 寫到 AGENT_INTENT top-level
2. `LLMProxy` (presumed, M5.4-5.5 派工): 從 AGENT_INTENT 讀 → 寫到 AGENT_SPEAK top-level
3. `MemoryMiddleware._on_agent_speak` (`memory/middleware.py:451-461`): 從 AGENT_SPEAK 讀 → 傳給 `provider.post_reply_commit`
4. `_proactive_dm_llm_executor` (`scripts/run_server.py:537-565`): 在 `_fire_intent` 之前 create InnerLifeEvent → 拿 event_id 透傳

**Gap:** `TriggerEnvelope.from_payload` (`trigger.py:48-120`) **不讀 SoulEvent 的 top-level `inner_life_event_id` 欄位**。
AGENCY_TRIGGER event 上有 `inner_life_event_id` (top-level),但 handler 構造 TriggerEnvelope 時 **這個欄位丟失**,只從 `event.payload` 解。

### 3.5 EmotionalCarryover (`src/temporal/models.py`,被 Heartbeat 使用)

從 `heartbeat/engine.py:64, 211-215` 可知:
- `HeartbeatEngine._carryovers: Dict[agent_id, EmotionalCarryover]`
- 啟動時 load + apply_decay(`engine.py:102-106`)
- Tick 計算 chrono_ctx 時 inject carryover (`engine.py:217-224`)
- chrono_ctx 寫進 SYSTEM_TICK payload 的 `attachment_heat` 欄位 (`engine.py:241`)

**Gap:** consciousness 在 M5.7-2 從 SYSTEM_TICK filter 拿掉 (`consciousness.py:147-156`)。
SYSTEM_TICK 還在 publish,chrono 計算 + carryover inject 還在跑,但 **沒有人讀 SYSTEM_TICK** (除了 heartbeat 自己的 _on_any_speak / _on_user_message 維護 timer)。
**carryover 計算 + inject 到 chrono_ctx 是 dead computation in production** (M5.8-1 P2.5)。

### 3.6 EmotionalState in consciousness (`consciousness.py:37-108`)

Agent 在 consciousness 內有自己的 `EmotionalState`:
```python
dependency: float          # 0.0-1.0
intimacy_level: int        # 0-100
mood: str                  # "neutral" | "happy" | "lonely" | "annoyed"
last_spoken_at: Optional[datetime]
silence_strike: int        # 連續未回應 tick 次數
```

**流向:**
- `consciousness._fire_intent` 把 `mood` 帶進 `intent_payload` (`consciousness.py:434-435`)
- LLMProxy 拿 `intent_payload.mood` 注入 system prompt
- EmotionalState 透過 `emotion_engine.update` 從 USER_MESSAGE / tick 累積 (`consciousness.py:191-196, 314-318`)

**Gap:** EmotionalState **不流到 Agency.run / run_agency**。Agency 對每個 agent 的 mood / dependency / intimacy 完全無感。

---

## 4. Integration Matrix

### 4.1 從「Inner Life state」到「Agency 收到什麼」

| Inner Life state | 產生路徑 | 是否流到 TriggerEnvelope? | 是否流到 Agency.run? | 是否影響 Stage 2 decision? |
|------------------|----------|---------------------------|---------------------|---------------------------|
| InnerLifeEvent.event_id | `InnerLifeWriter.create_event` (sole creator) | ✗ (`from_payload` 不讀 top-level field) | ✗ (Stage 2 不看 envelope) | ✗ |
| EmotionalCarryover (heat, afterglow, worry) | `HeartbeatEngine._loop` 注入 SYSTEM_TICK | ✗ (SYSTEM_TICK 不在 AGENCY_TRIGGER 鏈) | ✗ | ✗ |
| EmotionalState.mood | `consciousness._fire_intent` → `intent_payload.mood` | ✗ (intent_payload 是 LLM 端) | ✗ | ✗ |
| EmotionalState.intimacy / dependency | consciousness 內部,只用於 `_should_speak` | ✗ | ✗ | ✗ |
| NarrativeTrace (過去 events) | `NarrativeTraceWriter` 寫到 trace.jsonl | ✗ (沒人從 envelope 觸發 query) | ✗ | ✗ |
| Provenance.trigger_type | create_event 必填 | ✗ (只寫 InnerLifeEvent, 不在 AGENCY_TRIGGER 鏈) | ✗ | ✗ |
| session_id / correlation_id | create_event 必填 | ✗ | ✗ | ✗ |

**結論:** Inner Life 的 6 個維度中, **0 個** 透過任何 production 路徑抵達 Agency 4 stages。
所有 Inner Life 整合目前都繞過 Agency(直接從 LLMProxy / MemoryMiddleware 那一側消化)。

### 4.2 從「Agency 4 stages」實際看到的 inputs

| Stage | 唯一 inputs (production) | Inner Life 觸及? |
|-------|---------------------------|------------------|
| Stage 1 | `state.is_dormant / is_busy / last_action_at` + `now` | ✗ |
| Stage 2 trigger-only | `eligibility + state + now + trigger` (perception=None) | ✗ (trigger 沒有 inner_life_event_id) |
| Stage 2 perception-only | (production dead code) | ✗ (perception 沒人構造) |
| Stage 3 | `decision_type` | ✗ |
| Stage 4 (STUB) | `action_type` | ✗ |

**結論:** Agency 4 stages 在 production 完全 inner-life-blind。

### 4.3 真實的「Inner Life 影響」路徑 (繞過 Agency)

1. **`_proactive_dm_llm_executor`** (`run_server.py:511-575`): 在 `run_agency` **之前** create InnerLifeEvent → 拿 event_id 透傳到 `_fire_intent` → `AGENT_INTENT.inner_life_event_id` → LLMProxy → `AGENT_SPEAK.inner_life_event_id` → Memory
2. **ConversationQualification.promote** (`conversation_qualification/qualifier.py`): 在 SESSION_END 觸發時 create InnerLifeEvent → 透過 NarrativeTraceWriter 寫 trace
3. **Memory writeback** (`memory/middleware.py:451-461`): AGENT_SPEAK 後 5s cooldown → `provider.post_reply_commit(inner_life_event_id=...)` → MemoryWriter.extract_and_write

**這 3 條路徑 100% 在 Agency 4 stages 之外**,Agency 只負責 deterministic gating (Stage 1-3) + 留下 STUB trace (Stage 4)。

---

## 5. Reusable Existing Mechanisms (5 條)

### 5.1 TriggerEnvelope.extra (M5.2-H 派工 2026-08-08,frozen payload-side extension)

**Status:** Production-ready, 已經在 dream 觸發鏈使用
**Mechanism:** producer (scheduler / executor) 把 context 放進 `payload.extra` → `from_payload` 透傳到 `envelope.extra` → handler 讀
**Limitation:** Stage 1-4 不讀 `envelope.extra` — handler 必須自己讀,或在 call `run_agency` 前用
**對 Inner Life 的適用度:** **高** — 可以加 `extra={"inner_life_event_id": eid, "carryover_snapshot": {...}, "recent_event_summary": "..."}`

### 5.2 SoulEvent.inner_life_event_id top-level field (M5.4-5.5 frozen)

**Status:** Production-ready, AGENT_INTENT/AGENT_SPEAK 都有
**Mechanism:** Producer 在 SoulEvent top-level 設 inner_life_event_id,consumer 從 top-level 讀
**Gap:** `TriggerEnvelope.from_payload` 沒有讀 top-level field — handler 構造 envelope 時丟失
**對 Inner Life 的適用度:** **中** — 需要修 `trigger.py` (1 行加 reading, 但 trigger.py 是 frozen 嗎? — yes,per M5.2-F)
**結論:** 不能動 trigger.py 讓它讀 top-level,只能在 handler side 顯式讀 + 塞 envelope.extra

### 5.3 AgencyRunResult.trigger (M5.2-G additive)

**Status:** Production-ready, 在 `agency.py:38, 79, 144` 已經有
**Mechanism:** `run_agency` 接受 trigger → 結果保留 trigger reference 在 `AgencyRunResult.trigger`
**對 Inner Life 的適用度:** **高** — handler 可以讀 result.trigger.extra 拿到 envelope 帶的所有 context
**不需要新合約** — 已經有了

### 5.4 NarrativeTraceReader 5 query methods (M5.4-5.7 frozen)

**Status:** READ-ONLY, 沒被任何 handler 調用
**Mechanism:** `query_by_session_id / correlation_id / ts_range` 都能拿到「過去 narrative state」
**對 Inner Life 的適用度:** **中** — handler 可以在 call `run_agency` 前 query 一次,塞 envelope.extra 帶進 decision context (但 Stage 2 還是不讀)
**Limit:** 只能影響「是否 call run_agency」,不能影響 Stage 2 內部邏輯

### 5.5 consciousness._fire_intent 內 `intent_payload.mood` (既有 LLM 路徑)

**Status:** Production 路徑 (`consciousness.py:434-435`)
**Mechanism:** mood 從 EmotionalState 抽出,塞 intent_payload,LLMProxy 收
**對 Inner Life 的適用度:** **N/A** — 這條是 LLM 端 prompt injection,不是 Agency 端

---

## 6. A/B/C 三方案評估

### Option A — Reuse existing path (TriggerEnvelope.extra)

**Scope:** Producer side 把 inner_life context 塞 `payload.extra`,handler 構造 envelope 時自動繼承。
**Contract change:** 0
**對 Stage 1-4 的影響:** 0 (Stage 2 不讀 envelope.extra)
**實際 gain:** Producer 端可以在 publish AGENCY_TRIGGER 前 query InnerLifeWriter / NarrativeTraceReader → 帶進 envelope.extra → handler 可以讀 (例如 log / executor 帶進 LLM prompt)
**Lost:** **Stage 2 的 deterministic decision 不被 inner life 影響** — 這條路徑只是「把 inner life context 帶過 trigger 邊界」,不是「讓 inner life 改變 agency 的 YES/NO」

**Bry 派工問題的答案:** 「Inner Life → Agency Decision」這條 deterministic 鏈沒有建立,但「Inner Life 跟 Agency decision 一起流動」這條 observability 鏈可以建立。

### Option B — Minimal additive boundary on Agency.run

**Scope:** 新增 optional 參數到 `run_agency` / `Agency.run` (例如 `inner_life_context: Optional[Dict] = None`),**不改 Stage 1-4 邏輯** (他們忽略新參數,只 log trace)。
**Contract change:** 0 (新參數 default None,所有既有 caller 不傳也不會 break)
**對 Stage 1-4 的影響:** 0 (新參數只在 `AgencyRunResult` 加 echo field `inner_life_context_echo: Optional[Dict]`,handler 讀得到)
**實際 gain:** handler / executor 可以在 call `run_agency` 前 query inner life → 帶進 `run_agency(..., inner_life_context=ctx)` → result 帶 `inner_life_context_echo` → executor / observability 讀
**Lost:** **Stage 2 deterministic 依然 inner-life-blind** — inner life 還是只能 post-decision observability,不能進 YES/NO gate

**Bry 派工問題的答案:** 「Inner Life 跟 Agency decision 一起流動」這條鏈以 **canonical typed boundary** 建立,executor 跟 observability 有乾淨的 read point,Stage 2 仍然 frozen。

### Option C — Significant Agency redesign

**Scope:** 改 Stage 2 讀 inner_life_event_id / carryover,mood 等;改 TriggerEnvelope 帶 structured InnerLifeContext
**Contract change:** ✗ — M5.2 frozen contract 必須破
**對 Stage 1-4 的影響:** Stage 2 logic 變
**實際 gain:** Inner Life 可以影響 YES/NO
**Lost:** **Frozen contract 破壞** — Bry 派工明確禁止

**Bry 派工問題的答案:** 這條路可以讓 Inner Life 真正影響 Agency decision,**但違反 M5.8-2 stop conditions**「Inner Life → Agency requires changing a frozen contract」。

---

## 7. 推薦方案 — Option B (minimal additive boundary)

### 7.1 為什麼不是 Option A?

Option A 完全 zero-contract-change,聽起來最安全。但 Bry 派工 question 是「Inner Life → Agency Decision 完整鏈能不能建立」,Option A 只建立了「Inner Life state 跟 trigger envelope 一起流動」,**沒建立 Agency 對 inner life 的 read point**。

Bry 派工原文:
> "Identify whether any existing context object can be reused without creating a new abstraction."
> "Determine the smallest possible integration boundary."

Option B 是「最小 integration boundary」 — **additive optional param**。

### 7.2 Option B 設計 sketch (M5.8-3 implementation 派工時驗證)

```python
# stages.py / agency.py — 純 additive, default None, 不改既有行為
def run_agency(
    state: AgencyState,
    perception: Optional[Dict[str, Any]],
    now: datetime,
    trigger: Optional[TriggerEnvelope] = None,
    inner_life_context: Optional[Dict[str, Any]] = None,  # NEW: optional
) -> AgencyRunResult:
    return Agency(state).run(perception, now, trigger=trigger, inner_life_context=inner_life_context)
```

```python
# agency.py — AgencyRunResult additive
@dataclass
class AgencyRunResult:
    eligibility: EligibilityResult
    decision: DecisionResult
    action_type: Optional[str] = None
    execution: Optional[ExecutionResult] = None
    trace: List[AgencyTraceEntry] = field(default_factory=list)
    trigger: Optional[TriggerEnvelope] = None
    # NEW: optional echo of inner_life_context for observability
    inner_life_context_echo: Optional[Dict[str, Any]] = None
```

**Stage 1-4 邏輯完全不變**,只是把 `inner_life_context` 從入口接住 → 放到 `AgencyRunResult.inner_life_context_echo`。
Handler 端:

```python
# trigger_handler.py (示意, 不在本 audit 修改)
context = {
    "inner_life_event_id": envelope.extra.get("inner_life_event_id"),
    "carryover_snapshot": envelope.extra.get("carryover_snapshot"),
    "recent_event_count": envelope.extra.get("recent_event_count"),
}
result = run_agency(
    state=self.agency.state,
    perception=None,
    now=now,
    trigger=envelope,
    inner_life_context=context,  # NEW: additive
)
# result.inner_life_context_echo 在 log / observability 可讀
```

### 7.3 Option B 的 contract 邊界 (audit 結論)

| 既有 frozen contract | Option B 是否影響? |
|----------------------|---------------------|
| Stage 1 eligibility semantics | ✗ (不動) |
| Stage 2 decision semantics | ✗ (不動) |
| Stage 3 selection semantics | ✗ (不動) |
| Stage 4 STUB semantics | ✗ (不動) |
| TriggerEnvelope frozen (M5.2-F) | ✗ (不動) |
| InnerLifeEvent frozen (M5.4-5.1) | ✗ (不動) |
| Provenance frozen (M5.4-5.1) | ✗ (不動) |
| NarrativeTraceReader READ-ONLY (M5.4-5.7) | ✗ (不動) |
| SoulEvent.inner_life_event_id top-level (M5.4-5.5) | ✗ (不動) |
| TriggerEnvelope.from_payload silent coerce (M5.2-Q-4) | ✗ (不動) |

**Option B 是 100% additive,不破任何 frozen contract。**

### 7.4 Option B 帶來的 observability 改善

1. **Trace:** `AgencyRunResult.inner_life_context_echo` 可寫到 trace entry (新增 optional field)
2. **Handler log:** Decision log 可以印 inner_life_event_id (canonical identity)
3. **Executor:** LLM executor 拿到 `result.inner_life_context_echo` 可以注入 system prompt(沿用既有 mood pattern)

### 7.5 Option B 不做的事(明確)

- ✗ **不**讓 Stage 2 讀 inner_life_event_id 改變 YES/NO
- ✗ **不**讓 emotion_engine / EmotionalCarryover 直接影響 cooldown
- ✗ **不**新增 scoring / priority / weighting 邏輯
- ✗ **不**改 trigger envelope frozen fields
- ✗ **不**讓 handler 自動 query NarrativeTraceReader(那是 producer 端的責任)

---

## 8. Provenance / Correlation Path Analysis

### 8.1 目前 Inner Life identity 在 Agency 鏈的 traceability

```
1. InnerLifeWriter.create_event
   ↓ 回傳 event_id (32 hex)
2. (Option B producer side) publisher 從 event_id 放 payload.extra = {"inner_life_event_id": eid}
   ↓
3. AGENCY_TRIGGER event on bus (payload.extra 帶 eid)
   ↓
4. AgencyTriggerHandler.handle_event 收到 SoulEvent
   ↓
5. TriggerEnvelope.from_payload 從 event.payload 解出 envelope.extra (含 eid)  ← 既有 frozen path, ✓
   ↓
6. handler 構造 run_agency(trigger=envelope) → envelope.extra 在,Stage 2 不讀  ← Option A 邊界
   ↓
7. (Option B) handler 也構造 inner_life_context={inner_life_event_id: eid, ...} → run_agency(..., inner_life_context=ctx)
   ↓
8. AgencyRunResult.inner_life_context_echo 帶 eid  ← Option B observability
   ↓
9. handler log "decision=YES inner_life_event_id=<32hex>"  ← 完整 identity preserved
```

### 8.2 Correlation path (跟 M5.5-2 對齊)

- AGENT_INTENT.inner_life_event_id (top-level): 既有 LLMProxy / Memory 路徑 ✓
- AGENCY_TRIGGER payload.extra.inner_life_event_id: producer side additive, 跟既有 dream 模式一致
- AGENT_SPEAK.inner_life_event_id (top-level): Memory middleware 讀 ✓
- InnerLifeEvent.parent_event_id: v1 chain, 跟 Agency 無直接關係

**Identity 在 Option B 100% 保留** — `event_id` 從 InnerLifeWriter 產出後不變,純透傳到 envelope.extra + result echo + log。

### 8.3 Gap identified

`TriggerEnvelope.from_payload` (`trigger.py:48-120`) **不讀 SoulEvent top-level `inner_life_event_id` 欄位**,只從 `event.payload` 解。
但這是 trigger.py frozen — 改它就破 frozen contract。
**結論:** trigger.py 不改;producer side 改用 `payload.extra.inner_life_event_id` 模式 (跟 dream 的 `payload.extra.target_agent_id` 模式一致)。

---

## 9. Recursive Feedback Analysis

### 9.1 Same-cycle 風險 (Agency 觸發 Agency)

**路徑:**
```
Stage 4 executor (LLM / writer)
  ↓ publish AGENT_SPEAK / DIARY_WRITE
MemoryMiddleware._on_agent_speak → inner_life_event_id 透傳 → Memory fact
  ↓
_inner_life_writer.create_event 失敗 / 任何 event publish
  ↓
不可能 publish AGENCY_TRIGGER (4 handler 都 filter trigger_type, 只 consume AGENCY_TRIGGER)
  ↓
AgencyTriggerHandler 收到 AGENCY_TRIGGER → run_agency → 是 next cycle,不是 same cycle
```

**M5.2-H invariant I11 (Bry 派工 2026-08-08):** 1 trigger → max 1 writer call
**Stage 1 action_cooldown_seconds=60 / decision_cooldown_seconds=30:** 60s 內同一 agent 不能再 act

**Same-cycle 遞迴風險:** ✗ 已被 4 個機制防止
1. Agency 4 stages 不 publish AGENCY_TRIGGER (handlers 才 publish)
2. handler 跟 writer 解耦 (writer 失敗不觸發 handler)
3. M5.2-H I11: 1 trigger → 1 writer call
4. Stage 1 action_cooldown: 60s guard

### 9.2 Cross-cycle 風險 (temporal continuity, NOT recursive feedback)

**允許的路徑 (by design):**
```
Day 1: trigger_event_1 → create_event_1 (event_id=abc) → executor → Memory fact
Day 1: SESSION_END → qualifier.promote → create_event_2 (correlation_id=session_1, parent=abc)
Day 1: SESSION_END → AGENCY_TRIGGER trigger_type="morning" → handler (今天不會再 fire 因 cooldown)
Day 2: trigger_event_3 → handler.query(trace_by_correlation=session_1) → 帶 context → Agency decision
```

**這是 M5.4-5.1 派工的 Inner Life design** — cross-cycle 是 by design 的 temporal continuity,不是 recursive feedback bug。

**Recursive feedback vs Temporal continuity 區別:**

| 屬性 | Recursive feedback (BAD) | Temporal continuity (BY DESIGN) |
|------|--------------------------|--------------------------------|
| 時間 | same cycle (ms 級) | cross cycle (hours/days 級) |
| 觸發鏈 | A → B → A (immediate) | A (day1) → A' (day2), A' 不是 A 觸發的 |
| Identity 共享 | 同一個 event_id 自我 reference | 不同 event_id 透過 correlation/parent 串 |
| Mitigation | cooldown 60s, writer 失敗隔離 | 沒需要 — 這是 narrative 設計 |

**Option B 不增加 recursive feedback 風險** — inner_life_context 是 passive data,handler 不會主動用 context 去 publish 新的 AGENCY_TRIGGER。

### 9.3 Bry 派工 stop condition 檢查

Bry 派工 stop conditions:
- 「an existing hidden autonomous bypass is discovered」 — **NO**。已驗證 4 handler × 4 trigger_type 互不 overlap,M5.2-H invariants 都保留。
- 「recursive same-cycle execution」 — **NO**。M5.2-H I11 + cooldown 雙重防護,Option B 不動這兩條。

---

## 10. Frozen Contract Impact

| Frozen contract | M5.8-2 Option B 影響 |
|-----------------|----------------------|
| `Stage 1: check_eligibility(state, now)` (M5.1) | ✗ 不動 |
| `Stage 2: make_decision(...)` (M5.1 + M5.2-F + M5.2-G) | ✗ 不動 |
| `Stage 3: select_action(decision_type)` (M5.1) | ✗ 不動 |
| `Stage 4: execute_action_stub` (M5.1 + M5.2) | ✗ 不動 |
| `TriggerEnvelope` (M5.2-F) | ✗ 不動 |
| `TriggerEnvelope.from_payload` (M5.2-Q-4) | ✗ 不動 |
| `_publish_agency_trigger(extra=...)` (M5.2-H Phase 2) | ✗ 不動 |
| `InnerLifeEvent` (M5.4-5.1) | ✗ 不動 |
| `Provenance` (M5.4-5.1) | ✗ 不動 |
| `InnerLifeWriter` sole creator (M5.4-5.1) | ✗ 不動 |
| `NarrativeTraceWriter` (M5.4-5.6) | ✗ 不動 |
| `NarrativeTraceReader` READ-ONLY (M5.4-5.7) | ✗ 不動 |
| `SoulEvent.inner_life_event_id` top-level (M5.4-5.5) | ✗ 不動 |
| `ConversationQualification` v1 policy (M5.6-2) | ✗ 不動 |
| `Heartbeat._loop` exception isolation (M5.7-4) | ✗ 不動 |
| `AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler` 4 handlers (M5.2-G/H) | ✗ 不動 |

**結論: 0 frozen contract 受影響。**

---

## 11. P0/P1/P2/P3 Findings

### P0 — Correctness / Production Integrity

**0 findings.** M5.8-2 audit 沒發現 P0 issue。

### P1 — Architecture Integrity

**0 findings.** 4-stage contract, frozen 4 handlers, identity propagation 全部完整。

### P2 — Capability Gap

#### P2.1 — Agency 對 Inner Life state 完全 inner-life-blind (核心 finding)

**證據:** §4.1 matrix 顯示 6 個 Inner Life 維度中 0 個流到 Agency 4 stages。
**Root cause:** Stage 2 frozen,TriggerEnvelope frozen,沒人構造 perception dict。
**對 Bry 派工核心問題的答案:** 當前架構 **不支援** 「Inner Life 影響 Agency decision」這條 deterministic 鏈。
**選項:** Option B (additive boundary) 是最小 fix,但仍不改變 Stage 2 — inner life 只能 post-decision observability。
**如果要 inner life 改 YES/NO:** 必須動 Stage 2 → 破 frozen → 違反 M5.8-2 stop conditions。

#### P2.2 — `TriggerEnvelope.from_payload` 不讀 SoulEvent top-level `inner_life_event_id` (notification only, 不需 fix)

**證據:** `trigger.py:48-120` 只解 `event.payload` dict,不讀 event 本身的 top-level field。
**影響:** 當 bus publish `AGENCY_TRIGGER(inner_life_event_id=xxx)`,handler 構造 envelope 時 `event_id` 丟失。
**現有 workaround:** producer side 放 `payload.extra.inner_life_event_id` (跟 dream `payload.extra.target_agent_id` 同模式)。
**結論:** 已有 workaround,不需 fix。但這是 design quirk,值得文件化。

#### P2.3 — EmotionalCarryover 注入 SYSTEM_TICK payload 是 dead computation (M5.8-1 P2.5 已有, 此 audit 確認)

**證據:** `heartbeat/engine.py:217-241` chrono_ctx 計算 + carryover inject 還在跑,但 `consciousness.py:147-156` M5.7-2 從 event_filter 拿掉 SYSTEM_TICK。
**影響:** 沒人讀 SYSTEM_TICK,所有 chrono + carryover 計算都是 wasted CPU。
**Out of scope for M5.8-2** — M5.8-1 P2.5 已有。

#### P2.4 — Perception-only path 在 production 是 dead code

**證據:** `grep "perception\s*=\s*\{" src/` 0 matches;`run_agency(perception=None, ...)` 在 4 production handlers 全是 None。
**影響:** Stage 2 perception-only branch (M5.1 contract) 在 production 從未被觸發。
**Frozen:** Stage 2 仍然兩條路徑 (perception-only / trigger-only),Bry 不願刪除任何一條 (per 修法 11 精神)。
**Out of scope for M5.8-2** — 屬於 documentation / cleanup 性質。

### P3 — Documentation / Cleanup

#### P3.1 — `stages.py:107` "perception-only path" comment 是 Stage 2 內部註解,但 production 沒人用

**證據:** comment 描述 perception path,但 grep 證實 production 從未構造 perception dict。
**修法:** 改 comment 標「currently production-dead, kept for frozen M5.1 contract」
**性質:** 純文件修正,跟 Option B 無關。

#### P3.2 — `agency.py:8` "Stage 4 is STUB only" docstring 已經正確

**證據:** M5.1 派工明列 STUB 設計,docstring 一致, **不需要改**。
**P3 結論:** 1 個 minor doc fix,Option B 不動。

---

## 12. Regression Baseline

- M5.8-2 是 STRICT READ-ONLY,**0 source modification**,regression 應該維持 M5.7-4 closeout baseline `392/392 PASS`。
- 焦點測試(M5.5-2 / M5.6-2 / M5.7-2 / M5.7-4)在 M5.8-1 closeout 已驗 `66/66 PASS`,Option B 不修這 4 個 module,維持 PASS。

**Regression status (本 audit 沒重跑 — M5.8-1 closeout 已驗證):**
- M5.8-1 closeout: M5.x 焦點測試 `66/66 PASS in 12.78s`
- M5.8-2 預期: 維持 `66/66 PASS`(本 audit 沒改 source,沒新增 production code)

---

## 13. Production Integrity

| 項目 | 狀態 |
|------|------|
| Source modification | **0** |
| memory.db mutation | **0** |
| diary/dream/event data mutation | **0** |
| InnerLifeEvent create / replay | **0** |
| relationship mutation | **0** |
| Frozen contract change | **0** |
| Production data migration | **0** |
| Existing untracked artifacts | **20 preserved** |

---

## 14. Git State

```
HEAD: 87153cf (M5.8-1 closeout, audit-only commit)
origin/main: 87153cf (synced)
Working tree: 20 pre-existing untracked artifacts preserved
Audit file: logs/m5_8_2_inner_life_agency_decision_context_audit.md (本檔案, 寫入但未 commit)
```

---

## 15. Core Question Answer (Bry 派工)

> Can Soul OS currently move from:
>   Experience → Inner Life → Agency Decision → Action
> in a deterministic, observable, contract-safe way?

**答案分三層:**

### 15.1 「Inner Life 跟 Agency 同一個 trigger cycle 內 deterministic 流動」

**NO** — Stage 2 不讀 inner_life_event_id, 不讀 carryover, 不讀 mood。Frozen contract 4 stages 在 production 是 inner-life-blind。

**但可觀察的 (目前已存在):**
- LLM executor 拿 `intent_payload.mood` 注入 LLM system prompt (繞過 Agency, 從 consciousness 直接到 LLMProxy)
- `_proactive_dm_llm_executor` 在 `run_agency` **之前** create InnerLifeEvent, event_id 透傳到 AGENT_INTENT → AGENT_SPEAK → Memory (繞過 Agency 4 stages)

**Inner Life 跟 Agency 4 stages 解耦** — Inner Life 走 LLM 端 prompt injection, Agency 走 deterministic gate。Bry 派工 desired 「Inner Life 影響 Agency decision」這條 deterministic chain **不存在** in current architecture。

### 15.2 「最小 integration boundary」

**Option B** — additive optional `inner_life_context: Optional[Dict] = None` on `run_agency` / `Agency.run`, default None, **不破任何 frozen contract**。

Boundary 性質:
- 入口: `run_agency(..., inner_life_context=ctx)`
- 出口: `AgencyRunResult.inner_life_context_echo: Optional[Dict]`
- 4 stages 邏輯: **完全不變**,只接住新參數 → 放到 result
- handler 端: 可以從 envelope.extra 構造 context,塞進 run_agency
- executor 端: 從 result.inner_life_context_echo 讀 → inject LLM prompt / observability

**Option B 的限制 (明說):**
- 不改 Stage 2 YES/NO gate
- 不讓 emotion / carryover 直接影響 cooldown
- 不加新 scoring / weighting
- 不破 trigger.py / stages.py / handler 任一 frozen contract

### 15.3 「如果要真正讓 Inner Life 影響 Agency decision YES/NO」

**需要 Option C** — 改 Stage 2 讀 inner_life_event_id / carryover / mood, 改 TriggerEnvelope 帶 structured InnerLifeContext。

**這違反 M5.8-2 stop conditions** 「Inner Life → Agency requires changing a frozen contract」。

**Bry 派工原文呼應:**
> "Out of scope:
>   - implementation
>   - Agency redesign
>   - new scoring system
>   - LLM integration
>   - new memory infrastructure"

**Option C 是明確 out-of-scope,本 audit 不展開設計。**

---

## 16. Recommended Next Ticket (M5.8-3 candidate)

### M5.8-3 — Inner Life → Agency Observability Boundary (Option B Implementation)

**Mode:** MINIMAL ADDITIVE IMPLEMENTATION
**Scope:**
1. `src/agency/agency.py`: 新增 optional `inner_life_context: Optional[Dict[str, Any]] = None` to `Agency.run` and `run_agency`, default None
2. `src/agency/agency.py`: 新增 `inner_life_context_echo: Optional[Dict[str, Any]] = None` to `AgencyRunResult`
3. `src/agency/agency.py`: `Agency.run` 內 `return AgencyRunResult(..., inner_life_context_echo=inner_life_context)`
4. 4 handlers: 從 `envelope.extra` 構造 `inner_life_context` 帶進 `run_agency(..., inner_life_context=ctx)` (additive, optional)
5. handler decision log: 多印 `inner_life_context_echo` (preserves canonical identity)
6. Tests: M5.8-3 test file 驗 (a) default None 不影響既有 (b) context 帶進 echo (c) Stage 2 不讀 context (frozen) (d) 4 handler 各 1 個 test

**STOP conditions:**
- 任何 frozen contract 變動 → STOP
- Stage 2 logic 改 → STOP
- TriggerEnvelope 變動 → STOP
- recursive feedback 風險出現 → STOP

**Expected regression:** 392/392 維持,新增 M5.8-3 test PASS。

### Alternative 候選 (Bry 派工時拍板)

- **Alternative X:** Skip Option B, 走 Option C (改 Stage 2 讀 inner life) — 違反 stop conditions,**不推薦**
- **Alternative Y:** 不動 Agency, 把 inner_life 觀察範圍擴大到 M5.8-1 P2.x 系列 (Memory LLM Judge 看 Diary/Dream / Heartbeat carryover 接 consumer / etc.) — 跟 M5.8-2 主題脫鉤, 屬 M5.9.x 候選
- **Alternative Z:** 收工,不開 M5.8-3 (architecture 已達 stable baseline,Inner Life → Agency 整合留待 Bry 重新派工)

---

## 17. Bry Decision Required?

**YES** — 但只需要 1 個小決策:**Option B 是不是 wanted direction?**

派工選項:
- A. **拍板 Option B (M5.8-3 派工)** — additive boundary, 100% contract-safe, observability gain
- B. **Reject Option B, 開 Option C (M5.8-3 改寫 Stage 2 讀 inner life)** — 破 frozen, 不推薦
- C. **Reject 整條路, M5.8-2 收工後等下個主題** — 不開 M5.8-3

派工拍板後再開 M5.8-3 implementation ticket。

---

## 18. Architectural Findings Summary

1. **Agency 在 production 對 Inner Life 完全 inner-life-blind** — 4 stages 都不讀 inner_life_event_id / carryover / mood / narrative trace。
2. **Inner Life 影響路徑目前繞過 Agency** — 透過 LLM 端 prompt injection (`intent_payload.mood`) + AGENT_INTENT/AGENT_SPEAK top-level field (`inner_life_event_id`)。
3. **frozen 4 handler × frozen 4 stage + frozen TriggerEnvelope** = 完整的 deterministic gate, 任何 Inner Life 整合都必須 additive。
4. **`TriggerEnvelope.extra` 是 M5.2-H 已建立的 extension point** — 任何 trigger_type 都可以帶 context (dream 已 demonstrate)。
5. **`TriggerEnvelope.from_payload` 不讀 SoulEvent top-level field** — producer 必須用 `payload.extra.inner_life_event_id` 模式 (跟 dream 模式一致)。
6. **Option B 是 100% additive, 0 frozen contract 變動** — 最小 integration boundary, 但只能 post-decision observability,不能改 Stage 2 YES/NO。
7. **Option C 可讓 inner life 改 YES/NO 但破 frozen** — 違反 M5.8-2 stop conditions, 明確 out-of-scope。
8. **Recursive feedback 已 4 重防護** — handler/writer 解耦, M5.2-H I11, cooldown 60s/30s, Option B 不增加風險。
9. **Identity 100% canonical preserved** — event_id 從 InnerLifeWriter 產出後透傳到 envelope.extra / result echo, 不變。
10. **P0=0, P1=0, P2=4, P3=2** — 沒 P0/P1, P2 都是 capability gap 不需立即修, P3 都是 doc cleanup。

---

## 19. Final Status

**M5.8-2 audit complete.**
- Read-only ✓
- 0 source modification ✓
- 0 production data mutation ✓
- 4 handler × 4 stage × frozen contract 全部 verify ✓
- Option B 推薦 + 1 個 Bry decision (拍板 / reject / 跳過)
- Recommended next: **M5.8-3 (Option B implementation)** 等 Bry 派工, 派工後再 commit + push 本 audit log

**Awaiting Bry decision on M5.8-3 direction.**
