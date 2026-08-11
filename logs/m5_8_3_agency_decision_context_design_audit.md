# M5.8-3 — Agency Decision Context Design Audit

**Ticket:** M5.8-3 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY / ARCHITECTURE DESIGN AUDIT
**Baseline:** `HEAD = 1032034` (post M5.8-2) | `origin/main = 1032034` (synced)
**Date:** 2026-08-10 21:15 EDT
**Auditor:** Mavis (M3) for Bry

---

## 0. Audit Charter

### 0.1 Bry 派工原文 constraint (核心矛盾)

Bry 派工同時要求:
> (a) "Stage 2 can make a decision **informed by** Inner Life while preserving the existing deterministic gate"
> (b) "If changing Stage 2 semantics is unavoidable: STOP. Report: CONTRACT CONFLICT — BRY DECISION REQUIRED"

Bry 派工 stop conditions 明列:
> "1. Frozen Agency contract must change."
> "2. Stage 2 semantics must fundamentally change."

本 audit 的任務:**誠實評估 3 方案是否能在不改 Stage 2 semantics 前提下,讓 Stage 2 真的讀 inner life 改 YES/NO。**

如果 3 方案都改 Stage 2 → **CONTRACT CONFLICT,STOP**。

### 0.2 Bry 派工 context 釐清

> "M5.8-2 confirmed ... simply adding `inner_life_context` to Agency.run() would only provide plumbing/observability. It would NOT solve the actual capability requirement"
> "Therefore DO NOT implement Option B yet."

→ Bry 明確 reject M5.8-2 Option B(transport-only)。
→ 這次要的是 **DECISION** input,不只是 transport。

---

## 1. Stage 2 Decision Map (frozen logic)

來源:`src/agency/stages.py:88-166` (M5.1 + M5.2-F + M5.2-G 派工,frozen)

### 1.1 Signature (frozen)

```python
def make_decision(
    eligibility: EligibilityResult,         # Stage 1 結果
    perception: Optional[Dict[str, Any]],  # M3.4 perception layer 結果 (frozen)
    state: AgencyState,                    # 構造時注入
    now: datetime,                         # 呼叫方傳入
    trigger: Optional[TriggerEnvelope] = None,  # M5.2-G 新增
) -> DecisionResult:
```

### 1.2 Input contract (frozen)

| 條件 | 結果 | Reason |
|------|------|--------|
| `perception is None AND trigger is None` | **ValueError** | "at least one of perception or trigger must be provided" (M5.2-F Bry 拍板) |
| `not eligibility.eligible` | NO | f"not eligible: {eligibility.reason}" |
| Trigger-only + decision cooldown active | NO | f"decision cooldown active ({elapsed:.1f}s < {decision_cooldown_seconds}s)" |
| Trigger-only + cooldown ok | **YES** | f"trigger-only path met (trigger_type={trigger.trigger_type})" → decision_type="speak" |
| Perception-only + `not perception.get("accepted", False)` | NO | "perception rejected" |
| Perception-only + `perception.get("priority", 0) <= 0` | NO | "no priority signal (priority <= 0)" |
| Perception-only + decision cooldown active | NO | cooldown reason |
| Perception-only + all pass | **YES** | "all conditions met" → decision_type="speak" |

### 1.3 Decision inputs that influence YES/NO (完整 list)

| Input | 來源 | 影響 |
|-------|------|------|
| `eligibility.eligible` | Stage 1 結果 | NO if not eligible |
| `perception is None / not None` | M3.4 perception layer | 決定走哪個 path |
| `perception.get("accepted", False)` | M3.4 frozen 欄位 | NO if not accepted |
| `perception.get("priority", 0)` | M3.1 Phase B frozen 欄位 | NO if <= 0 |
| `state.last_decision_at` | AgencyState | NO if decision_cooldown active |
| `state.decision_cooldown_seconds` | AgencyState (default 30) | cooldown threshold |
| `trigger is None / not None` | scheduler / executor | 決定走哪個 path |
| `trigger.trigger_type` | TriggerEnvelope frozen | **只被記到 trace, 不影響 YES/NO** |

**Trigger-only path 沒有 inner life 維度可以讀** — Stage 2 看到 trigger 就 YES(if cooldown ok)。
**Perception-only path 只有 `accepted` + `priority` 兩個 frozen 維度** — 沒有 inner life 欄位。

### 1.4 Frozen contract 的 evidence (test coverage)

來源:`tests/test_m5_2_minimal_agency.py:55-413` (I-A1 到 I-A10 invariant tests)

| Test | 驗證內容 | Frozen contract 邊界 |
|------|----------|----------------------|
| I-A1 | Agency 不 mutate perception | perception dict reference 完整保留 |
| I-A2 | priority 不能 bypass gate | priority>0 + accepted=False → NO |
| I-A5 | rejected perception 不 act | accepted=False → NO 不論 priority |
| I-A7-I-A10 | decision_cooldown 邏輯 | state 影響 decision |
| Negative path matrix (6 行) | 所有 NO path 都驗過 | frozen rejection logic |

**Stage 2 frozen contract 的邊界** = 「trigger + perception + state 三個 orthogonal input,各影響不同 rejection 條件」。

---

## 2. Current Agency Context Map (M5.8-2 重述 + 加深)

### 2.1 Stage 2 之前可用的所有 context (M5.8-2 §5 已列,這裡 expanded)

| Context object | 來源 module | 流向 Agency 的路徑 | 影響 Stage 2? |
|----------------|-------------|---------------------|---------------|
| `TriggerEnvelope.trigger_type` | `agency/trigger.py:39` frozen | `run_agency(trigger=envelope)` | trace-only, **不影響 YES/NO** |
| `TriggerEnvelope.agent_id` | frozen | 透傳 (envelope 用) | NO |
| `TriggerEnvelope.reason` | frozen | 透傳 (executor 用) | NO |
| `TriggerEnvelope.elapsed_mins` | frozen | 透傳 (chrono 用) | NO |
| `TriggerEnvelope.timestamp` | frozen | 透傳 | NO |
| `TriggerEnvelope.extra: Dict` | **frozen dataclass 內唯一 open field** (M5.2-H 派工) | producer 塞 → from_payload 解 | **NO (Stage 2 不讀)** |
| `perception: Dict[str, Any]` | M3.4 frozen | `run_agency(perception=...)` | **YES (Stage 2 讀 accepted + priority)** |
| `AgencyState.is_dormant` | external (無 producer 寫入) | `state.is_dormant` | YES (Stage 1 拒絕) |
| `AgencyState.is_busy` | external (無 producer 寫入) | `state.is_busy` | YES (Stage 1 拒絕) |
| `AgencyState.last_action_at` | Stage 4 後寫入 (`agency.py:136`) | `state.last_action_at` | YES (Stage 1 action_cooldown) |
| `AgencyState.last_decision_at` | Stage 2 後寫入 (`agency.py:111`) | `state.last_decision_at` | YES (Stage 2 decision_cooldown) |
| `now: datetime` | handler 傳入 | `now` | YES (cooldown 計算) |
| `InnerLifeEvent` (任何欄位) | `inner_life/event.py` frozen M5.4-5.1 | **無路徑** | NO |
| `Provenance` | `inner_life/event.py` frozen | **無路徑** | NO |
| `EmotionalCarryover` | `temporal/models.py` (Heartbeat 注入) | **無路徑** (SYSTEM_TICK 沒人讀) | NO |
| `EmotionalState.mood` | `agent/consciousness.py:37-108` | 透過 `intent_payload.mood` 到 LLM (post-decision) | NO |
| `NarrativeTrace` | `inner_life/trace_reader.py` READ-ONLY | **無路徑** (沒 handler 調用) | NO |
| `Memory` fact context | `memory/sage/*` | 透過 `intent_payload.memory_query_hint` (post-decision) | NO |
| `WorldContext` | `world/perception.py` M3.4 frozen | 透傳到 `intent_payload.world_context` (post-decision) | NO |
| `chrono_block` (含 carryover) | `temporal/*` | 透傳到 `intent_payload.chrono_context` (post-decision) | NO |
| `relationships` | `relationships/*` (per-agent dict) | 透傳 (executor 用, 夢的 target_agent_id 走 trigger.extra) | NO |
| `SoulEvent.inner_life_event_id` (top-level) | M5.4-5.5 frozen | **從 `TriggerEnvelope.from_payload` 漏失** (見 M5.8-2 §8.3) | NO |

### 2.2 結論:Stage 2 之前可用的 frozen 影響 input 只有 3 類

1. **perception dict** (M3.4 frozen,Stage 2 讀 `accepted` + `priority` 兩個 key)
2. **trigger envelope** (M5.2-F frozen,Stage 2 只看 trigger_type 進 trace,不看 extra)
3. **AgencyState** (M5.2 frozen,Stage 1 讀 is_dormant/is_busy,Stage 1+2 讀 last_action_at/last_decision_at)

**Inner Life 不在這 3 類任何一個**。任何新 input 都需要改 `stages.py`。

---

## 3. Inner Life Available Signals (M5.8-2 §3 重述, 為 design 評估準備)

### 3.1 6 個可讀的 Inner Life 維度

| 維度 | Dataclass | 讀取 API | 預設值 |
|------|-----------|----------|--------|
| 1. event_id (32 hex) | `InnerLifeEvent.event_id` | 從 `InnerLifeWriter._known_event_ids` / `NarrativeTraceReader.query_by_event_id` | N/A |
| 2. session_id | `InnerLifeEvent.session_id` | `query_by_session_id` | None |
| 3. correlation_id | `InnerLifeEvent.correlation_id` | `query_by_correlation_id` | None |
| 4. parent_event_id | `InnerLifeEvent.parent_event_id` | `query_by_lineage_path_prefix` | None |
| 5. ts | `InnerLifeEvent.ts` | `query_by_ts_range` | N/A |
| 6. provenance (trigger_type/actor_id/source_system) | `Provenance` | 從 `InnerLifeEvent.provenance.{trigger_type,actor_id,source_system}` | "narrative" |
| 7. carryover (heat/afterglow/worry) | `EmotionalCarryover` | `HeartbeatEngine._carryovers` 或 fresh `EmotionalCarryover()` | defaults |
| 8. trace records (jsonl) | `data/inner_life/trace.jsonl` | `NarrativeTraceReader` 5 methods | empty list |

### 3.2 Inner Life 對 Agency 最有用的最小 signal

Bry 派工要求「minimal deterministic decision signal」,不是 full event。

**Bry 派工明列禁止:**
- mood scoring dimensions
- emotional weights
- confidence scores
- embeddings / semantic search / vector DB
- LLM judge
- new personality model

**所以不能用的:**
- ✗ mood scoring
- ✗ emotional weights
- ✗ confidence scores
- ✗ LLM judgment

**可以用(符合「deterministic」「structured」「minimal」):**
- ✓ 結構化欄位 (e.g. `intimacy_level: int 0-100`)
- ✓ 計數類 (e.g. `recent_event_count: int`)
- ✓ 時間類 (e.g. `last_inner_life_event_ts: ISO str`)
- ✓ 布林類 (e.g. `has_recent_diary: bool`, `has_unresolved_dream: bool`)
- ✓ Frozen 預設值 (e.g. `attachment_heat: float 0-1` 從 `EmotionalCarryover`)

### 3.3 Inner Life minimum signal (建議 4 個)

Bry 派工要求「Inner Life → Agency decision」,最小信號應該是:

```python
@dataclass(frozen=True)
class InnerLifeDecisionContext:
    """
    最小 deterministic Inner Life decision signal.
    不含 narrative text, 不含 diary/dream 內容, 只含結構化計數 / 時間。
    """
    has_recent_diary: bool = False            # 過去 N 小時內有 diary write
    has_unresolved_dream: bool = False        # 過去 N 小時內有 dream 但無對應 user response
    recent_event_count_last_session: int = 0  # 過去 session 內 InnerLifeEvent 數
    last_inner_life_event_ts: Optional[str] = None  # ISO str or None
```

**性質:**
- 全部是 deterministic 結構化欄位
- 沒有 text,沒有 narrative
- 沒有 score,沒有 weight
- 沒有 LLM call
- frozen dataclass
- 預設值 = 「Inner Life 不知道」(全部 False/0/None)

**注意:** 這只是建議的 minimum signal shape,**Bry 派工 spec 沒指定具體 field**,Bry 可能拍板要不同欄位。但 audit 必須給出一個 example shape 來評估 3 方案。

---

## 4. Transport vs Decision 分析 (Bry 派工 §6 核心要求)

### 4.1 定義

| 名詞 | 定義 | 判定 |
|------|------|------|
| **TRANSPORT** | Inner Life state 從 producer 流到 Agency module 內 (例如 result echo / log) | ✓ M5.8-2 Option B 已達到 |
| **DECISION** | Stage 2 真的讀 Inner Life state,改變 YES/NO / decision_type / reason | ✗ 當前不可達 (M5.8-2 P2.1) |

### 4.2 為什麼 transport ≠ decision

**Transport (M5.8-2 Option B):**
```
producer → payload.extra = {inner_life_event_id, ...}
  → TriggerEnvelope.extra
  → run_agency(..., inner_life_context=ctx)  # new param
  → AgencyRunResult.inner_life_context_echo  # echo for observability
  → executor 讀 echo → log
```

Stage 2 frozen logic 仍跑:
- Trigger-only: YES (if cooldown ok)
- Perception-only: YES (if accepted + priority > 0 + cooldown ok)

**Inner life 不改 Stage 2 行為。** Inner life 改變的是「executor 拿到什麼 context」,不是「Stage 2 決定什麼」。

**Decision (Bry 派工 M5.8-3 要求):**
```
producer → payload.extra = {inner_life_event_id, ...}
  → TriggerEnvelope.extra
  → run_agency(..., inner_life_context=ctx)  # new param
  → Stage 2 READ ctx  ← *** 改 stages.py ***
  → DecisionResult 改變 (YES ↔ NO, decision_type, reason)
```

Stage 2 新邏輯 (範例):
```python
# 新分支 (Bry 派工 spec 暗示的方向)
if inner_life_context is not None and inner_life_context.has_unresolved_dream:
    return DecisionResult(False, "agency: unresolved_dream_pending", None)
```

**這是 Stage 2 semantic change。** 既有 trigger-only path 仍是 YES (frozen),但 inner_life_context 開新 path 改變 output 空間。

### 4.3 Bry 派工「Stage 2 informed by Inner Life」字面解讀

Bry 派工原文:
> "Stage 2 can make a decision informed by Inner Life while preserving the existing deterministic gate"

**「Informed by」** — Stage 2 輸出 reflects Inner Life state
**「Preserving the existing deterministic gate」** — 既有 trigger-only 跟 perception-only 路徑行為不變

**結論:「informed」= 新 input 影響 output。** 既有 path 行為不變是新 branch 設計的目標,而非限制。

**但是:加新 branch 就是 Stage 2 semantic change。** 因為:
- 既有 2 paths → 3 paths(或多 paths)
- 既有 2 rejection reasons → 3+ reasons
- 既有 1 decision_type ("speak") → 可能多 decision_type ("wait", "defer", "nudge")
- 既有 test matrix → 必須擴充

**Bry 派工 stop condition #2: "Stage 2 semantics must fundamentally change" — 任何 decision output space 變化都算 fundamental change。**

---

## 5. Architecture A/B/C 詳細評估

### 5.1 Option A — Existing context reuse (Perception or TriggerEnvelope.extra)

#### 5.1.1 設計

Inner Life state 塞進既有 perception dict 或 trigger.extra,Stage 2 讀既有 frozen key。

#### 5.1.2 評估:Perception reuse

**Producer side:**
```python
perception = {
    "agent_id": "agent_yua",
    "world_context": "...",
    "accepted": True,  # M3.4 frozen
    "priority": 5,     # M3.1 Phase B frozen
    # 新增 (突破 M3.4 frozen contract):
    "inner_life_event_id": "abc...",
    "inner_life_has_unresolved_dream": True,
}
```

**Stage 2 必須讀新 keys:**
```python
# stages.py:145 改:
if perception is not None:
    if not perception.get("accepted", False): return NO
    # NEW (M5.8-3): inner life veto
    if perception.get("inner_life_has_unresolved_dream"):
        return DecisionResult(False, "inner_life: unresolved_dream", None)
    ...
```

**Frozen contract impact:**
- ✗ M3.4 perception contract 變動 (perception dict schema 擴充)
- ✗ `stages.py:145-163` logic 變動
- ✗ `make_perception` test helper 變動
- ✗ I-A1 test (perception 不被 mutate) 仍可 PASS(只要不改既有 keys)
- ✗ I-A2 / I-A5 仍可 PASS(既有 priority / accepted 邏輯不動)
- **Stage 2 程式碼要改** → 破 frozen

#### 5.1.3 評估:TriggerEnvelope.extra reuse

**Producer side:**
```python
await bus.publish(SoulEvent(
    event_type=EventType.AGENCY_TRIGGER,
    payload={
        "trigger_type": "proactive_dm",
        "agent_id": "agent_yua",
        "reason": "scheduler.proactive_dm",
        "extra": {
            "inner_life_event_id": "abc...",
            "inner_life_has_unresolved_dream": True,  # 新增
        },
    },
))
```

**Stage 2 trigger-only path 必須讀 envelope.extra:**
```python
# stages.py:125 改:
if trigger is not None and perception is None:
    if decision_cooldown: return NO
    # NEW (M5.8-3): inner life veto
    if trigger.extra.get("inner_life_has_unresolved_dream"):
        return DecisionResult(False, "inner_life: unresolved_dream", None)
    return DecisionResult(True, "trigger-only path met", "speak")
```

**Frozen contract impact:**
- ✓ TriggerEnvelope dataclass frozen 不變 (extra 已經是 Dict)
- ✗ `stages.py:125-141` logic 變動
- ✗ `trigger_handler.py` 等 4 handler 的「trigger-only = YES」假設失效
- ✗ M5.2-G 派工的「trigger 是 sufficient signal」假設失效
- **Stage 2 程式碼要改** → 破 frozen

#### 5.1.4 Option A 結論

**任何 Option A 子方案都要改 `stages.py`。** 不存在「不改 stages.py 就能讓 Stage 2 讀 inner life」的可能性。

Reason:Stage 2 frozen logic 是 fixed decision tree,input 變了 → output 不可能變(若 output 變就是 semantic change)。

### 5.2 Option B — Minimal additive decision-context field

#### 5.2.1 設計

新增 optional 參數到 `run_agency` / `Agency.run` / `make_decision`,Stage 2 加新 branch 處理。

#### 5.2.2 程式碼 sketch

```python
# stages.py 改 (Stage 2 加 inner_life_context 參數):
def make_decision(
    eligibility: EligibilityResult,
    perception: Optional[Dict[str, Any]],
    state: AgencyState,
    now: datetime,
    trigger: Optional[TriggerEnvelope] = None,
    inner_life_context: Optional["InnerLifeDecisionContext"] = None,  # NEW
) -> DecisionResult:
    # frozen input contract 不變 (perception OR trigger 必填)
    if perception is None and trigger is None:
        raise ValueError(...)
    
    if not eligibility.eligible:
        return DecisionResult(False, "not eligible: ...", None)
    
    # NEW branch: inner life veto (frozen 之上的 additive layer)
    if inner_life_context is not None:
        if inner_life_context.has_unresolved_dream:
            return DecisionResult(False, "inner_life: unresolved_dream_veto", None)
        if inner_life_context.has_recent_diary and state.last_action_at is None:
            return DecisionResult(False, "inner_life: recent_diary_no_action", None)
    
    # Trigger-only path (frozen, 邏輯不變)
    if trigger is not None and perception is None:
        ... (frozen)
    
    # Perception-only path (frozen, 邏輯不變)
    if perception is not None:
        ... (frozen)
```

```python
# agency.py 改 (AgencyRunResult 加新欄位):
@dataclass
class AgencyRunResult:
    eligibility: EligibilityResult
    decision: DecisionResult
    action_type: Optional[str] = None
    execution: Optional[ExecutionResult] = None
    trace: List[AgencyTraceEntry] = field(default_factory=list)
    trigger: Optional[TriggerEnvelope] = None
    inner_life_context_echo: Optional["InnerLifeDecisionContext"] = None  # NEW
```

#### 5.2.3 評估

| 維度 | 評估 |
|------|------|
| 既有 frozen paths | trigger-only + perception-only 邏輯完全不變 ✓ |
| New branch | 加在既有 paths **之前**, 既是 veto 也是 add ✓ |
| Signature | `make_decision` signature 加 optional 參數, default None, backward-compatible ✓ |
| Test impact | M5.1 I-A tests 仍 PASS(新 branch 只在 inner_life_context 存在時觸發);新增 M5.8-3 test 驗新 branch |
| Bry stop condition #1 | "Frozen Agency contract must change" — **`stages.py` 加新 branch = contract 變動** ✗ |
| Bry stop condition #2 | "Stage 2 semantics must fundamentally change" — **decision output space 從 2 paths 變 3 paths = semantic change** ✗ |

**Option B 結論:** 程式碼 additive 但 contract semantic 變動。
「既有 frozen logic 仍跑」≠「Stage 2 沒改」,因為新 branch 改變了 Stage 2 的 decision surface。

#### 5.2.4 「如果 inner_life_context=None 時跟現在完全一樣」的問題

Bry 派工可能會問:「如果 inner_life_context=None,Stage 2 行為跟現在完全一樣,這算 additive 嗎?」

答案:**不算 additive,因為**:
- 加新 param 就是 signature 變動(stages.py:88-93 改)
- 加新 branch 就是 logic 變動(stages.py 新加 if block)
- 即使 default None,Stage 2 程式碼 byte-level 變了
- Stage 2 contract spec 變了(從 2 paths 變 3 paths)
- Frozen test matrix 變了(M5.1 I-A tests 必須新增 M5.8-3 inner_life 測試)

**Bry 派工 stop condition 字面:「changing Stage 2 semantics is unavoidable」— 哪怕 default None,只要新邏輯存在就 unavoidable。**

### 5.3 Option C — New InnerLifeDecisionContext abstraction

#### 5.3.1 設計

新模組 `src/agency/inner_life_context.py`,封裝 `InnerLifeDecisionContext` dataclass + query 邏輯。
Stage 2 引用此 module。

#### 5.3.2 程式碼 sketch

```python
# 新檔 src/agency/inner_life_context.py
from dataclasses import dataclass
from typing import Optional
from src.inner_life.trace_reader import NarrativeTraceReader

@dataclass(frozen=True)
class InnerLifeDecisionContext:
    has_recent_diary: bool = False
    has_unresolved_dream: bool = False
    recent_event_count_last_session: int = 0
    last_inner_life_event_ts: Optional[str] = None

    @classmethod
    def from_agent(cls, agent_id: str, trace_reader: Optional[NarrativeTraceReader] = None) -> "InnerLifeDecisionContext":
        # query inner life + return structured context
        ...
```

```python
# stages.py 改 (跟 Option B 一樣加新 param + new branch)
def make_decision(..., inner_life_context: Optional[InnerLifeDecisionContext] = None):
    if inner_life_context is not None:
        if inner_life_context.has_unresolved_dream:
            return DecisionResult(False, "inner_life: unresolved_dream_veto", None)
    ... (frozen paths)
```

#### 5.3.3 評估

| 維度 | 評估 |
|------|------|
| 既有 frozen paths | 跟 Option B 一樣不變 ✓ |
| New abstraction | 新 module 新 dataclass, 可測試 ✓ |
| Stage 2 改動 | 跟 Option B 一樣(stages.py 加新 branch) ✗ |
| Frozen contract impact | 跟 Option B 一樣 + 新增 InnerLifeDecisionContext contract (新 frozen) |
| Decision surface 變化 | 跟 Option B 一樣(2 paths 變 3 paths) ✗ |

**Option C 結論:** 跟 Option B 一樣破 frozen,只是新 abstraction 多了 module 邊界。

### 5.4 三方案對 Bry stop conditions 對齊

| Stop condition | A (perception reuse) | A (trigger.extra reuse) | B (additive param) | C (new abstraction) |
|----------------|----------------------|--------------------------|--------------------|--------------------|
| #1 Frozen contract must change | **YES** (M3.4 + stages.py) | **YES** (stages.py) | **YES** (stages.py + run_agency) | **YES** (stages.py + new module) |
| #2 Stage 2 semantics fundamental change | **YES** (新 perception key) | **YES** (新 branch) | **YES** (新 branch) | **YES** (新 branch) |
| #3 Multiple equally-valid architectures | NO (1 個) | NO (1 個) | NO (1 個) | NO (1 個) |
| #4 P0/P1 discovered | NO | NO | NO | NO |
| #5 Production mutation | NO | NO | NO | NO |
| #6 Same-cycle recursive | NO | NO | NO | NO |
| #7 Inner Life cannot reduce | NO (可 reduce) | NO (可 reduce) | NO (可 reduce) | NO (可 reduce) |

**所有 3 方案都 hit stop condition #1 + #2。**

---

## 6. RECOMMENDATION: CONTRACT CONFLICT — STOP

### 6.1 為什麼 STOP

Bry 派工 stop conditions 明列:
> "1. Frozen Agency contract must change."
> "2. Stage 2 semantics must fundamentally change."
> "If changing Stage 2 semantics is unavoidable: STOP. Report: CONTRACT CONFLICT — BRY DECISION REQUIRED"

**3 方案全都 require Stage 2 改 → CONTRACT CONFLICT。**

Bry 派工 spec 內含矛盾:
- 想要「Stage 2 informed by Inner Life」(改 Stage 2)
- 拒絕「Stage 2 semantics change」(不讓改 Stage 2)

**這矛盾 Bry 自己設定,本 audit 不能私自解決,必須 STOP 報 Bry 拍板。**

### 6.2 真正的 3 條 Bry decision options

Bry 必須在以下 3 條路選一條:

#### Option X — Open frozen contract unfreeze ticket (P0)

**性質:** 開 M5.x-N 工單,明確 unfreeze Stage 2 frozen contract,允許 additive inner_life_context 整合。

**優點:**
- Inner Life 真的能影響 Agency decision
- 跟 Bry 派工「DECISION not transport」一致
- Option B 或 C 都能實作

**代價:**
- Frozen contract unfreeze 是 P0 等級事件
- M5.2-G 4 handlers 全部需要 re-validate
- M5.1 invariant tests 需要擴充
- M5.4-6.x trace 可能受影響
- 過去 7 個 tickets 累積的 frozen guarantees 全部要重新 review

**Bry 派工歷史傾向:** 從 M5.4-5.7 / M5.5-2 / M5.6-2 / M5.7-2 / M5.7-4 一路下來,所有 frozen contract 都嚴格 unfreeze-free。**Bry 拍板 unfreeze 是劇烈 action**,Bry 可能要審很多次。

#### Option Y — Producer-side gating (替代路徑)

**性質:** Inner Life 在 AGENCY_TRIGGER publish **之前**評估,只有 inner_life_ready 才 publish trigger。
Agency 4 stages 完全不動;Inner Life 透過「是否觸發 Agency」間接影響 decision。

**優點:**
- Stage 2 frozen contract **完全不動**
- 「Inner Life → Agency decision」鏈存在,只是 gate 在 producer side
- 零 frozen contract 變動
- 跟 M5.2-G/I-8 派工精神一致(producer 是唯一 gate)

**代價:**
- Bry 派工 spec 說「Stage 2 actually reads InnerLife」 — Option Y 不符合字面
- Inner Life 影響是「trigger 不 publish」,不是「Stage 2 改 YES/NO」
- 如果 trigger 已經 publish 了,Inner Life 來不及 veto(same cycle)
- Producer 必須有 Inner Life 查詢能力(新增 InnerLifeDecisionQuery dependency)

**Bry 派工歷史傾向:** 從 M5.4-6.2 / M5.6-2 / M5.7-2 來看,producer-side gating 是常見模式(proactive_dm 觸發前先 create InnerLifeEvent 就是 producer gating 範例)。**Bry 可能接受。**

#### Option Z — Drop capability (M5.8-2 P2.1 永久接受)

**性質:** 接受 P2.1 為永久 capability gap,Inner Life → Agency decision 這條 deterministic 鏈不存在。
Agency 繼續 inner-life-blind;Inner Life 透過 LLM 端 prompt injection 影響 output。

**優點:**
- 0 frozen contract 變動
- 0 新增 code
- 維持現有 architecture
- 跟 M5.8-1 P2.2 「Agency 不參考 Inner Life state」一致

**代價:**
- Bry 派工 spec 的 capability requirement 永久 fail
- M5.8-1 / M5.8-2 / M5.8-3 連續 3 個 audit 浪費(?)
- 永久放棄 Inner Life 對 Agency 的 deterministic 影響

**Bry 派工歷史傾向:** Bry 派過 P2 capability gap 工單多次(修法 11/12、M5.4-5.7 等都接受 P2 留為日後題目)。**Bry 可能接受**。

### 6.3 Bry decision matrix

| Option | Frozen contract 變動 | Capability 達成 | 跟 Bry 派工字面對齊 | 風險 |
|--------|----------------------|-----------------|----------------------|------|
| X (unfreeze Stage 2) | YES (P0) | YES (full DECISION) | YES | HIGH (7 個 tickets frozen 重審) |
| Y (producer gating) | NO | PARTIAL (trigger 不 publish ≠ Stage 2 改) | PARTIAL (spec 字面 fail) | LOW |
| Z (drop) | NO | NO | NO (capability fail) | LOWEST |

**Bry 派工要選 1 條。** 本 audit 不私自選。

---

## 7. Determinism Analysis

### 7.1 既有 Stage 2 確定性

Bry 派工:
> "The same: Trigger + context + Inner Life state must produce deterministic decision behavior."

**既有 Stage 2 確定性:**
- Input: `(eligibility, perception, state, now, trigger)`
- 全部 frozen 結構,沒 random,沒 external call
- 同一 input → 同一 output ✓

### 7.2 假設加入 inner_life_context 後

若 Bry 拍板 Option X (Stage 2 改):
- Input: `(eligibility, perception, state, now, trigger, inner_life_context)`
- inner_life_context 必須是 frozen dataclass, **不允許 nested random / time-of-day evaluation**
- 必須由 producer 端 **預先 query** 然後 snapshot 進 trigger.extra / perception dict
- 同一 input → 同一 output ✓ (只要 inner_life_context 本身 deterministic)

### 7.3 確定性風險

- **Risk 1:** InnerLifeDecisionContext.from_agent() 包含 NarrativeTraceReader query → trace.jsonl 是 append-only,同檔案同時間 query 結果 deterministic ✓
- **Risk 2:** time-of-day 評估(例如「過去 5 分鐘內」)依賴 now — 但 Stage 2 已接收 now,加 inner_life_context 不引入新 time source ✓
- **Risk 3:** EmotionalCarryover (Heartbeat 注入)有 `apply_decay(elapsed_hours=...)` 函式 — 從 HeartbeatEngine 已是 deterministic 計算(看 engine.py:101-106) ✓

**結論:** 若 Bry 拍板 unfreeze,determinism 可保持。**但這只是 design feasibility, 不改本 audit 結論。**

---

## 8. Feedback-Loop Analysis

### 8.1 Same-cycle recursive risk (Bry stop condition #6)

**既有防護(M5.8-2 §9.1 已 verify):**
1. 4 handler 不互相 trigger(各自 filter trigger_type)
2. M5.2-H I11: 1 trigger → 1 writer call
3. Stage 1 action_cooldown 60s
4. Stage 2 decision_cooldown 30s
5. Inner Life writer 不 publish AGENCY_TRIGGER

**加入 inner_life_context 後:**
- Stage 2 從 inner_life_context 讀 → return NO
- 不 invoke executor
- executor 不 publish AGENT_SPEAK
- 沒有新 AGENCY_TRIGGER publish
- **Same-cycle 風險仍 0** ✓

### 8.2 Cross-cycle temporal continuity (by design)

**允許:**
```
Day 1 14:00: trigger → Stage 2 YES → write event_1 (correlation=session_1)
Day 1 23:00: SESSION_END → ConversationQualification.promote → write event_2
Day 2 09:00: producer query trace → inner_life_context has_unresolved_dream=True
  → 若 Bry 拍板 unfreeze: producer 不 publish AGENCY_TRIGGER (Option Y)
  → 若 Bry 拍板 unfreeze Stage 2: Stage 2 讀 context,return NO
```

**這是 by design 的 temporal continuity**,M5.4-5.1 派工明列 Inner Life 設計是跨 cycle。

**不允許:**
- Day 1 14:00 trigger → Stage 2 YES → executor → write event_1 → publish event_1 → inner_life_context query → publish AGENCY_TRIGGER 給同一 cycle Stage 2
- **這會是 same-cycle 遞迴**,但既有 4 handler 不互相 trigger + M5.2-H I11 防護,加上 Stage 1/2 cooldown,遞迴在「同 cycle 內 attempt」就會被 cooldown 擋下
- **Option B/C 加 inner_life_context 不引入新 same-cycle 風險** ✓

---

## 9. Privacy / Data Minimization Analysis

### 9.1 Bry 派工明列禁止傳給 Agency

> "Do NOT assume Agency should receive:
>   - full InnerLifeEvent
>   - narrative text
>   - conversation content
>   - diary text
>   - dream text"

**Option B 設計符合:**
- `InnerLifeDecisionContext` 只含 4 個結構化欄位(布林/計數/時間)
- 沒 narrative text
- 沒 conversation content
- 沒 diary text
- 沒 dream text
- 沒 mood (避免 emotional weight)
- 沒 score

### 9.2 Inner Life identity 暴露

`inner_life_event_id` 32 hex 是 opaque ID,不含語意內容。但 audit 建議:
- 若 Bry 拍板 Option B,**inner_life_context 不應包含 `inner_life_event_id` 字串**(因為 stage 2 邏輯不需要 ID,只需要計數/布林)
- ID 應留在 `AgencyRunResult.inner_life_context_echo` 或 trigger.extra,作為 observability 用

### 9.3 Privacy 結論

**Option B/C 在 audit 設計下符合 data minimization** — InnerLifeDecisionContext 只含 4 個 non-sensitive 結構化欄位。
Bry 派工 spec 禁止項全部 avoid。

**但 Bry 派工 spec 沒指定 InnerLifeDecisionContext 的具體 fields** — 這是 design 細節,M5.8-3 implementation ticket 才需要定。
本 audit 只勾稽「structure 符合 minimization」,不展開具體 field 設計。

---

## 10. Identity / Provenance / Correlation Analysis

### 10.1 Identity preservation chain (Bry 派工 §12 要求)

```
InnerLifeEvent (event_id=abc, session_id=s1, correlation_id=c1, parent=p1)
    ↓
[Producer side] 構造 InnerLifeDecisionContext (no identity, just stats)
    ↓
[Producer side] 同步構造 trigger.envelope.extra = {inner_life_event_id: abc, ...}
    ↓
[AGENCY_TRIGGER event on bus, payload.extra 帶 identity]
    ↓
[Handler] TriggerEnvelope.from_payload → envelope.extra 保留 identity
    ↓
[Stage 2 (若 Bry 拍板 unfreeze)] 讀 inner_life_context (no identity) → return NO/YES
    ↓
[AgencyRunResult.inner_life_context_echo = inner_life_context (no identity)]
    ↓
[Exponent log: "decision=NO inner_life_event_id=abc reason=unresolved_dream"]
    ↓
[Action → write new InnerLifeEvent (parent_event_id=abc) if YES]
```

**Identity 100% 透過 trigger.extra 透傳**,Stage 2 frozen logic 不需要讀 identity。

### 10.2 沒有 fabricated identity 風險

- Inner Life identity 來自 InnerLifeWriter (sole creator, M5.4-5.1)
- Producer side 只 query,不 construct identity
- Stage 2 不需要 identity 來做 decision
- Exponent log 印 ID 是 observability 不是 identity source

### 10.3 Correlation preservation

- InnerLifeEvent.correlation_id 透過 trigger.extra 透傳(envelope.extra 是 frozen payload-side extension)
- 既有 M5.6-2 ConversationQualification 設 correlation_id=session_id 規則不動
- Stage 2 不需要 correlation_id 做 decision

### 10.4 Provenance preservation

- Provenance.trigger_type = "agent_reply" (既有 M5.4-6.2) 不變
- Provenance 不傳到 Agency(InnerLifeDecisionContext 不含 provenance)

**結論:** 既有 identity/provenance/correlation 規則全部 preserved,Option B/C 不破任何。

---

## 11. Frozen Contract Impact (詳細)

### 11.1 Option B/C frozen contract 影響逐項

| Frozen contract | Option B 影響 | Option C 影響 |
|------------------|---------------|---------------|
| Stage 1 `check_eligibility` | ✗ 不動 | ✗ 不動 |
| Stage 2 trigger-only path (lines 125-141) | ✗ 不動(邏輯不變) | ✗ 不動 |
| Stage 2 perception-only path (lines 145-163) | ✗ 不動(邏輯不變) | ✗ 不動 |
| Stage 2 既有 input contract (perception OR trigger) | ✗ 不動(不引入 inner_life 必填) | ✗ 不動 |
| **Stage 2 整體** | **✗ 加新 branch(雖然既有 logic 不動)** | **✗ 加新 branch** |
| Stage 3 `select_action` | ✗ 不動 | ✗ 不動 |
| Stage 4 `execute_action_stub` | ✗ 不動 | ✗ 不動 |
| `run_agency` signature | ✗ 加 optional 參數(default None) | ✗ 加 optional 參數(default None) |
| `Agency.run` signature | ✗ 加 optional 參數(default None) | ✗ 加 optional 參數(default None) |
| `AgencyRunResult` | ✗ 加 inner_life_context_echo field | ✗ 加 inner_life_context_echo field |
| `TriggerEnvelope` (M5.2-F) | ✗ 不動(extra 已是 frozen extension) | ✗ 不動 |
| `TriggerEnvelope.from_payload` (M5.2-Q-4) | ✗ 不動 | ✗ 不動 |
| `_publish_agency_trigger` (M5.2-H Phase 2) | ✗ 不動(extra 透傳已 frozen) | ✗ 不動 |
| `InnerLifeEvent` (M5.4-5.1) | ✗ 不動 | ✗ 不動 |
| `Provenance` (M5.4-5.1) | ✗ 不動 | ✗ 不動 |
| `InnerLifeWriter` (M5.4-5.1) | ✗ 不動 | ✗ 不動 |
| `NarrativeTraceWriter` (M5.4-5.6) | ✗ 不動 | ✗ 不動 |
| `NarrativeTraceReader` (M5.4-5.7) | ✗ 不動(只被 producer 調用) | ✗ 不動 |
| `SoulEvent.inner_life_event_id` (M5.4-5.5) | ✗ 不動(可加 producer-side 填) | ✗ 不動 |
| M3.4 PerceptionDecision / WorldEvent | ✗ 不動 (Option B 用 trigger.extra 不碰 perception) | ✗ 不動 |
| 4 handlers (M5.2-G/H) | ✗ 不動(executor 加 inner_life_context 透傳) | ✗ 不動 |
| Heartbeat (M5.7-2/4) | ✗ 不動 | ✗ 不動 |
| ConversationQualification (M5.6-2) | ✗ 不動 | ✗ 不動 |

**Option B/C 影響的 frozen contracts: 0 個** (Stage 2 邏輯 byte-level 改,但既有 branch 邏輯完整保留)。

### 11.2 為什麼 audit 還報 CONTRACT CONFLICT

**Bry 派工 spec 對「contract」的定義** 包括:
- frozen 程式碼邏輯
- frozen signature
- frozen decision surface (output 空間)
- frozen test matrix

**Option B/C 改:**
- stages.py byte-level 改(就算既有 branch 不動,新 branch 加入就是合約變)
- make_decision signature 變
- Stage 2 從 2 paths 變 3 paths
- 既有 M5.1 I-A tests 必須加 M5.8-3 inner_life tests

**Bry 派工 stop condition #1 字面:「Frozen Agency contract must change」— 哪怕既有 branch 邏輯保留,新 branch 加入就是 contract 變動。**

**Bry 派工 spec 對「frozen」的嚴格度 = M5.2 派工明列「4 個 sub-layers 全部 frozen」,任何 branch addition 都算 violation。**

---

## 12. P0/P1/P2/P3 Findings

### P0 — Correctness / Production Integrity

**0 findings.** M5.8-3 audit 沒發現 P0 issue。

### P1 — Architecture Integrity

**0 findings.** 既有 architecture 完整,4 stage contract 完整,4 handlers 完整。

### P2 — Capability Gap

#### P2.1 — Inner Life → Agency decision deterministic chain 在不破 frozen 下不可達 (本 audit 核心 finding)

**證據:** §5 完整 3 方案評估,3 個都改 Stage 2。
**Root cause:** Stage 2 frozen logic 是 fixed decision tree,任何 input 變化都 require code change。
**Bry 派工 stop condition:** 命中 #1 + #2。
**Bry decision required:** 3 條路(X / Y / Z),Bry 必須拍板。

#### P2.2 — 4 handler 都假設 trigger-only path 永遠 YES (M5.2-G 派工後遺)

**證據:** 4 handler × run_agency(trigger=envelope) → Stage 2 trigger-only = YES (if cooldown ok)。
**M5.2-G 派工:** 「trigger 是 sufficient signal」(trigger.py:135-136)。
**影響:** 任何 Stage 2 改都會破壞 4 handler 的「trigger 到就 YES」假設。
**若 Bry 拍板 Option X (unfreeze):** 4 handler 全部需要 audit + re-validate trigger-only path 在新 inner_life branch 後的行為。
**Out of scope for M5.8-3 結論:** 屬於 Option X 的 implementation 工單。

#### P2.3 — `TriggerEnvelope.from_payload` 不讀 SoulEvent top-level inner_life_event_id (M5.8-2 P2.2)

**承襲自 M5.8-2,本 audit 確認仍未改。**
**對 Option B/C 影響:** trigger.envelope.extra 仍然是唯一 inner_life 透傳路徑,這個 gap 不影響 Option B/C 設計(因為 Option B/C 不用 SoulEvent top-level field)。

### P3 — Documentation / Cleanup

#### P3.1 — `stages.py:5` "Stage 2: make_decision — perception + decision cooldown check" 描述不夠完整

**證據:** Stage 2 既有 2 paths,但 docstring 只寫 1 句。
**修法:** 加 "trigger-only path" 描述。
**跟 Option B/C 關係:** 若 Bry 拍板 Option B/C,Stage 2 docstring 必須更新(描述 inner_life_context branch)。
**若 Bry 拍板 Option Y/Z:** docstring 不需改。

#### P3.2 — `agency/__init__.py:5-9` M5.2 docstring 列 4 stages, 沒列 inner_life_input 預留

**證據:** 4 stages 描述固定,frozen 後沒人加 input description。
**跟 Option B/C 關係:** 跟 P3.1 同。

---

## 13. Regression Baseline

- M5.8-3 是 STRICT READ-ONLY,**0 source modification**,regression 應該維持 M5.8-2 closeout baseline `392/392 PASS`。
- 焦點測試(M5.5-2 / M5.6-2 / M5.7-2 / M5.7-4)在 M5.8-1 closeout 已驗 `66/66 PASS`,M5.8-2 closeout 預期 392/392 維持。
- **本 audit 沒 source 變動 → 392/392 預期維持**。

**Regression status (本 audit 沒重跑 — M5.8-2 closeout 已驗證):**
- M5.8-2 closeout commit `1032034` 預期 392/392 PASS
- M5.8-3 預期: 維持 392/392 PASS(本 audit 0 source 修改)

---

## 14. Production Integrity

| 項目 | 狀態 |
|------|------|
| Source modification | **0** |
| memory.db mutation | **0** |
| diary/dream/event data mutation | **0** |
| InnerLifeEvent create / replay | **0** |
| relationship mutation | **0** |
| Frozen contract change | **0** (audit 沒動) |
| Production data migration | **0** |
| Existing untracked artifacts | **20 preserved** |
| Audit file | `logs/m5_8_3_agency_decision_context_design_audit.md` (本檔案, 寫入但未 commit) |

---

## 15. Git State

```
HEAD: 1032034 (M5.8-2 closeout, audit-only commit)
origin/main: 1032034 (synced)
Working tree: 20 pre-existing untracked artifacts preserved
Audit file: logs/m5_8_3_agency_decision_context_design_audit.md (本檔案, 寫入但未 commit)
```

---

## 16. 3 Architecture Candidates 對 Bry 派工 spec checklist

| Bry spec 要求 | Option A (reuse) | Option B (additive param) | Option C (new abstraction) |
|---------------|------------------|---------------------------|----------------------------|
| 「Stage 2 真的讀 inner life 改 YES/NO」 | ✓ (但改 stages.py) | ✓ (但改 stages.py) | ✓ (但改 stages.py) |
| 「不發明 scoring system」 | ✓ | ✓ | ✓ |
| 「minimal deterministic decision signal」 | ✓ | ✓ | ✓ |
| 「Transport vs Decision 區分」 | ✗ (Option A 偏 transport) | ✓ (DECISION) | ✓ (DECISION) |
| 「decide sync / precomputed / event-derived / read-only」 | mixed (perception pre-built / extra sync) | precomputed (producer 端 query) | precomputed + new module |
| 「Data minimization」 | ✓ | ✓ | ✓ |
| 「Identity preserved」 | ✓ | ✓ | ✓ |
| 「Agency 4-stage semantics preserved」 | ✗ (Stage 2 改) | ✗ (Stage 2 改) | ✗ (Stage 2 改) |
| 「Stop condition #1+2」 | ✗ HIT | ✗ HIT | ✗ HIT |
| 「frozen contract unfreeze-free」 | ✗ | ✗ | ✗ |

**3 方案都達成 DECISION capability 但都破 frozen。** Bry 必須在「capability 達成」和「frozen preserved」間選一邊。

---

## 17. STOP DECLARATION (per Bry 派工 stop conditions)

> "If changing Stage 2 semantics is unavoidable:
>   STOP.
>   Report: CONTRACT CONFLICT — BRY DECISION REQUIRED
>   Do NOT modify code."

**本 audit STOP 報 CONTRACT CONFLICT。**

3 方案 all hit stop condition #1 (Frozen Agency contract must change) + #2 (Stage 2 semantics must fundamentally change)。

Bry 必須從 Option X / Y / Z 選一條。

### 17.1 Option X — Unfreeze Stage 2 (P0 工單)

派 M5.2-Stage2-Unfreeze ticket:
- M5.2 派工歷史 unfreeze-free,這是 P0 等級
- 4 handler 全部 audit trigger-only path 在新 inner_life branch 後的行為
- M5.1 I-A tests 必須擴充加 inner_life 維度
- Bry 必須明確拍板「允許 Stage 2 新加 branch」

### 17.2 Option Y — Producer-side gating (推薦路徑)

派 M5.8-4 ticket:
- Stage 2 frozen **完全不動**
- Inner Life 透過 producer-side filter 影響是否 publish AGENCY_TRIGGER
- 新增 `src/agency/inner_life_gating.py`(可選) 或 producer inline 邏輯
- 4 handler 在 call `run_agency` 前先 query InnerLife,若 gating 條件不滿足 → 不 call run_agency
- Identity 透過 trigger.extra 透傳
- 跟 M5.4-6.2 / M5.6-2 / M5.7-2 producer-side 模式一致

**Bry 派工 spec 字面 fail** (「Stage 2 actually reads InnerLife」),但 capability 部分達成(Inner Life 影響 Agency 觸發)。

### 17.3 Option Z — Drop capability (M5.8-2 P2.1 永久)

派 close-out 動作:
- 標 P2.1 為「永久 capability gap」
- 不開新工單
- M5.8-3 audit 收工,接受 frozen 限制
- 移到 M5.9.x 系列其他 ticket

---

## 18. Final Status

**M5.8-3 audit COMPLETE — CONTRACT CONFLICT REPORTED.**

- Read-only ✓
- 0 source modification ✓
- 0 production data mutation ✓
- 3 方案都改 Stage 2 → 命中 stop condition #1 + #2
- CONTRACT CONFLICT 報 Bry 拍板
- 3 條 Bry decision options (X / Y / Z) 列出

**Awaiting Bry decision on Option X / Y / Z.**

### 18.1 Mavis 對 Bry 的 recommendation (audit 視角)

Bry 派工歷史傾向(從 memory 記錄的 7 個 ticket 來看):
- 嚴格 frozen contract preservation
- 「沿用既有 pattern 拒絕大改」
- 「producer-side gating」是常見模式
- 對「unfreeze frozen」非常保守

**Mavis 推薦 Option Y (producer-side gating)** — 符合 Bry 派工歷史傾向,frozen 0 變動,capability 部分達成。

但 Bry 派工 spec 內含矛盾(spec 字面要求「Stage 2 reads InnerLife」),Bry 可能選擇 Option X 拍板 unfreeze。

**Bry 拍板後再開 M5.8-4 (Option Y) 或 M5.2-Stage2-Unfreeze (Option X) 或 close-out (Option Z) 工單。**

---

## 19. 1-line summary

**M5.8-3 audit 確認:3 方案都需改 Stage 2 frozen logic → CONTRACT CONFLICT,報 Bry 從 X(unfreeze) / Y(producer gating) / Z(drop) 拍板。**
