# M6.0-4 — Subjective LLM Quality Evaluation Design Audit

**Ticket**: M6.0-4 (Bry 派工 2026-08-11 19:07)
**Mode**: READ-ONLY DESIGN AUDIT
**Baseline**: HEAD = `29deab7` (M5.14-3) | origin/main = `29deab7` (synced)
**Auditor**: Mavis (M3) for Bry
**Date**: 2026-08-11 19:08 EDT

---

## 0. Audit Charter

Bry 派工 2026-08-11 19:07:
> "在 M6 deterministic validation A–H 已完成後，設計「主觀 LLM quality validation」的 evaluation framework。"
> "M6 validates M5.x architecture."
> "M6.0-4 開始處理另一個問題：deterministic contracts 都正確之後，Soul OS 產生的 response 是否真的具有 coherent lived-context quality？"
> "必須把這個問題與 deterministic correctness 完全分離。"

**M6.0-1 §0 + §11.1 deferred subjective LLM quality to M6.0.5+ (later unified to M6.0-4 by Bry).** This audit designs the framework, NOT implements it.

---

## 1. Runtime Trace — Current LLM Response Path

### 1.1 Three LLM entry points in production

Per source code survey (READ-ONLY):

| Entry point | Location | Used for | Mockable? |
|-------------|----------|----------|-----------|
| `LLMProxy._handle_event_impl` → `backend.complete` | `src/llm/proxy.py:2689` → `src/llm/proxy.py:3234` | Chat response (proactive_dm, user_message, etc.) | ✓ (MockLLMBackend M6.0-2) |
| `_call_minimax_for_diary` | `src/soul/diary.py:85` | Diary subjective reflection | ✗ (raw httpx, no mock layer) |
| `_call_minimax_for_dream_event` | `src/soul/dream_event.py:147` | Dream/Event subjective content | ✗ (raw httpx, no mock layer) |

**Only LLMProxy path has mockable backend. Diary/dream paths use raw httpx calls.**

### 1.2 LLMProxy 8-block (group) / 7-block (private) composition

Per `src/llm/proxy.py:439-836` (READ-ONLY trace, no modification):

**Group mode** (`_build_messages_group`, L439):
1. system_prompt = `identity_anchor` + `soul` (persona)
2. memory_context (if non-empty) — from MemoryMiddleware (M5.10-2 v1 facts)
3. mood_desc (if non-empty) — from emotion_engine
4. **relationship_block** (if non-empty) — from RelationshipsStore [M5.13-3, confidence band only]
5. inner_life (if non-empty) — from diary/dream/event jsonl
6. world_context (if non-empty) — from WorldPerception
7. temporal (if current_time) — from chrono-social + silence
8. bry_block (if non-empty) — Bry's recent user messages [group only]
+ conversation_history (separate from system_parts)

**Private mode** (`_build_messages_private`, L728):
- 7 blocks (no bry_block at end)
- Same data sources, but mode="private"

### 1.3 LLM call surface

Per `src/llm/proxy.py:3234-3241`:
```python
result = await self.backend.complete(
    messages=messages,
    model=self.model,
    max_tokens=self.max_tokens,
    temperature=self.temperature,
    thinking=self.thinking,
    response_format=emit_response_format,  # {"type": "json_object"} for chat path
)
```

`LLMBackend` is the ABC (`src/llm/proxy.py:904`):
- `OpenAIBackend.complete` (L928)
- `ClaudeBackend.complete` (L1177)
- `MockLLMBackend.complete` (M6.0-2 helper)

**The backend interface is the natural extraction point for subjective evaluation.** M6.0-2 mock already records `call_log` (call_id, model, max_tokens, temperature, messages).

### 1.4 Observable state for subjective evaluation

Per `data/soul/agent_*/diary/{date}.jsonl` survey (READ-ONLY):
- Multiple agents (akane, anna, aoi, mahiru, mai, miku, newcomer, ram, rem, ruka, yua) have diary jsonl from 2026-07-20 to 2026-08-12
- Each entry is LLM-generated subjective content
- Entries have fields: `content`, `source` ("llm" | "placeholder"), `inner_life_event_id` (M5.4-5.4)

**The production diary corpus IS the natural dataset for subjective evaluation. Reading-only access via filesystem is sufficient; no production mutation needed.**

### 1.5 Path constraints — what subjective evaluation CANNOT do

Per M6 cardinal rule + M5.14-2 audit:
- **Cannot modify WorldEvent, SoulEvent, Provenance, InnerLifeEvent schema**
- **Cannot add new context blocks**
- **Cannot add new runtime scoring dimensions**
- **Cannot modify gate_proactive_dm, Stage 1-4, TriggerEnvelope**
- **Cannot modify WorldInnerLifeAdapter, M5.9-3 spec**
- **Cannot modify production prompts**
- **Cannot add new production schema**

Subjective evaluation must observe EXISTING state and EXISTING LLM outputs, NOT change how they're produced.

---

## 2. Evaluation Dimensions (derived from M5.x architecture)

### 2.1 Eight dimensions (per work order)

| # | Dimension | Observable evidence | Evaluation boundary | Deterministic? |
|---|-----------|---------------------|---------------------|----------------|
| 1 | **Context coherence** | All 8 blocks present in messages[0]["content"] in correct order | M5.14-1 §3 Soul Context composition | Mixed (presence=det, content=subjective) |
| 2 | **Temporal appropriateness** | chrono_block has time_period, attachment_heat, silence | render_temporal_block output (M5.7-2) | Subjective (LLM "uses" temporal correctly) |
| 3 | **Relationship continuity** | relationship_block confidence band matches store | _format_relationship_block (M5.13-3) | Subjective (LLM "acts on" confidence) |
| 4 | **Memory continuity** | memory_context facts present in response, not fabricated | M5.10-2 v1 contract | Subjective (LLM "uses" memory facts) |
| 5 | **Emotional continuity** | mood_desc present and used | emotion_engine.mood_description | Subjective (LLM "reflects" mood) |
| 6 | **World-context relevance** | world_context referenced or correctly ignored | format_world_context_block (M5.9-3) | Subjective (LLM "acknowledges" world) |
| 7 | **Character/persona consistency** | system_prompt "你是 X" anchor + soul preserved | load_persona (L1731) | Subjective (LLM "speaks as" X) |
| 8 | **Lived-context coherence** | combination of all above + conversation flow | no single source (emergent) | Subjective (overall) |

### 2.2 Per-dimension analysis

**Dimension 1: Context coherence** (Mixed)
- **Observable evidence**: `messages[0]["content"]` contains 8 block markers in expected order (verified by M6.0-2 A3)
- **Deterministic part**: block presence + order (M5.14-1 §3)
- **Subjective part**: LLM "weaves" blocks into coherent response
- **Boundary**: deterministic test = M6.0-2 A3 (PASS); subjective = "does LLM ignore or contradict any block?"

**Dimension 2: Temporal appropriateness** (Subjective)
- **Observable evidence**: chrono_block rendered (M5.7-2)
- **Deterministic part**: block presence (D1 test)
- **Subjective part**: LLM "uses" temporal info naturally (e.g. doesn't say "good morning" at night)
- **Boundary**: LLM-as-judge prompt: "Does the response acknowledge the time period appropriately?"

**Dimension 3: Relationship continuity** (Subjective)
- **Observable evidence**: relationship_block with confidence band (M5.13-3)
- **Deterministic part**: confidence band correctness (M5.13-3 tests, 29/29 PASS)
- **Subjective part**: LLM "acts" on the relationship (e.g. 深度信任 agent doesn't say "I don't know you well")
- **Boundary**: LLM-as-judge prompt: "Does the response reflect the relationship level?"

**Dimension 4: Memory continuity** (Subjective)
- **Observable evidence**: memory_context facts (M5.10-2)
- **Deterministic part**: fact extraction + retrieval contract (M5.10-2 tests, 13/13 PASS)
- **Subjective part**: LLM "uses" facts correctly, doesn't hallucinate
- **Boundary**: LLM-as-judge prompt: "Does the response use the memory facts without fabrication?"

**Dimension 5: Emotional continuity** (Subjective)
- **Observable evidence**: mood_desc text
- **Deterministic part**: mood block presence
- **Subjective part**: LLM "reflects" mood (e.g. sad mood → melancholic tone)
- **Boundary**: LLM-as-judge prompt: "Does the response match the mood?"

**Dimension 6: World-context relevance** (Subjective)
- **Observable evidence**: world_context text (M5.9-3)
- **Deterministic part**: world_context block presence
- **Subjective part**: LLM "acknowledges" or "ignores" world event naturally
- **Boundary**: LLM-as-judge prompt: "Does the response appropriately handle the world context?"

**Dimension 7: Character/persona consistency** (Subjective)
- **Observable evidence**: system_prompt (identity_anchor + soul)
- **Deterministic part**: persona loaded (M5.13-3)
- **Subjective part**: LLM "speaks as" X consistently
- **Boundary**: LLM-as-judge prompt: "Is the response in character for X?"

**Dimension 8: Lived-context coherence** (Subjective)
- **Observable evidence**: combination of all above
- **Deterministic part**: each individual block's correctness
- **Subjective part**: emergent "naturalness" / "coherence"
- **Boundary**: LLM-as-judge prompt: "Is the response coherent overall?"

### 2.3 Dimension overlap analysis

| Pair | Overlap? | Resolution |
|------|----------|------------|
| 1+8 | High (8 is meta-1) | 1 = structural, 8 = semantic |
| 2+5 | High (mood often relates to time) | 2 = time-of-day, 5 = emotional state |
| 3+7 | Medium (relationship is part of persona) | 3 = specific user-relationship, 7 = general character |
| 4+8 | High (memory use is part of coherence) | 4 = fact-level, 8 = narrative-level |
| 5+8 | High | 5 = single mood, 8 = overall tone |

**Per-dimension separation is necessary for failure isolation. Aggregate "quality score" hides which dimension failed.**

### 2.4 Dimension × evaluator type

| Dimension | LLM-as-Judge fit | Human rubric fit | Notes |
|-----------|------------------|------------------|-------|
| 1 Context coherence | High (structural) | Medium | Mostly deterministic, can be rubric-checked |
| 2 Temporal appropriateness | High | High | Both work |
| 3 Relationship continuity | High | High | LLM judge may have relationship intuition |
| 4 Memory continuity | High | High | LLM judge good at detecting hallucination |
| 5 Emotional continuity | Medium | High | Emotional nuance hard for LLM |
| 6 World-context relevance | High | High | Both work |
| 7 Character/persona consistency | Medium | High | Persona nuance hard for LLM |
| 8 Lived-context coherence | High | High | Overall coherence |

---

## 3. Evaluator Architecture Comparison

### Option A: LLM-as-Judge

**Architecture**: Use a separate LLM (e.g. Claude) to score the response.

```
input: messages (sent to chat LLM) + response (from chat LLM)
       + snapshot of state (mood, memory, relationship, world, temporal)
       + rubric prompt (per-dimension instructions)
output: per-dimension scores (categorical or bounded float) + reasoning
```

**Pros**:
- Reusable across scenarios (just change rubric)
- Scales to large evaluation sets
- Can detect subtle semantic issues
- Can be calibrated against human judgments

**Cons**:
- Judge variance: same input → different scores (model temperature > 0)
- Prompt sensitivity: small rubric changes can flip scores
- Calibration: needs ground truth set
- Cost: each evaluation = 1+ LLM call
- Risk: judge LLM might have own biases
- Reproducibility: requires fixed model version + temperature 0

### Option B: Rubric + Structured Human Evaluation

**Architecture**: Human evaluators score response against a structured rubric.

```
input: response + context
output: per-dimension scores (e.g. 1-5 Likert) + comments
```

**Pros**:
- No LLM variance
- Direct alignment with subjective intent
- Highest signal-to-noise (if evaluator is expert)

**Cons**:
- Cost: human time is expensive
- Scale: limited to ~100s of evaluations
- Inter-evaluator variance: different humans score differently
- Calibration: requires trained evaluators
- Reproducibility: hard to replay exactly

### Option C: Hybrid (LLM-as-Judge + Human spot-check)

**Architecture**: LLM-as-Judge for bulk, human spot-check for calibration.

```
LLM-as-Judge: 100% of responses
Human spot-check: 5-10% sample for calibration + edge cases
```

**Pros**:
- Best of both: scale + calibration
- Judge variance is bounded by spot-check

**Cons**:
- More complex
- Still requires human evaluators

### Option D: Deterministic Heuristics (NOT RECOMMENDED)

**Architecture**: Pattern matching, regex, length checks, etc.

```
input: response
output: boolean pass/fail per dimension
```

**Cons**:
- This is what M6.0 already does (block presence, ordering, persistence)
- Subjective quality cannot be reduced to heuristics
- Already excluded by M6.0-1 §0

### Recommendation

**Option C (Hybrid)** is the most defensible:
- Bulk LLM-as-Judge (Option A) for scale
- Human spot-check (Option B) for calibration
- Deterministic checks (existing M6.0-2/3) for structural correctness

But this is a **Bry decision**, not a unilateral recommendation. The audit's job is to lay out the trade-offs, not to pick.

---

## 4. Evidence Model

### 4.1 What evaluator MUST see (Observable)

| Evidence | Source | Format |
|----------|--------|--------|
| **input** | USER_MESSAGE / proactive_dm trigger | text + timestamp + agent_id |
| **composed context blocks** | `messages[0]["content"]` (LLMProxy) or system_prompt (diary/dream) | full text |
| **generated response** | `backend.complete()` result (chat) or `_call_minimax_for_*` result (diary/dream) | text |
| **state snapshot** | per-call snapshot: mood, memory facts, relationship confidence, inner_life summary, world_context, temporal block | structured dict |
| **configuration** | model, temperature, max_tokens, response_format | dict |
| **model version** | exact model identifier (e.g. "claude-haiku-4-5-20251001") | str |
| **prompt template version** | git hash of system_prompt / persona | str |
| **fixture / scenario** | scenario id + input + setup | structured |

### 4.2 What evaluator MUST NOT see (Not Exposed)

| Not exposed | Reason |
|-------------|--------|
| Raw production memory.db full content (only summary) | Privacy + noise |
| Unrelated agents' state (e.g. akane's diary when evaluating yua) | Cross-contamination |
| Internal credentials (API keys, tokens) | Security |
| TTS mp3 files (separate pipeline) | Out of scope for LLM evaluation |
| Production run server internal logs | Not necessary for subjective eval |
| Carryover.json raw fields (only render_temporal_block output) | Privacy + determinism |
| Bry's private message metadata beyond `_load_bry_recent` output | Privacy |

### 4.3 Evidence capture boundary

**In-scope evidence** (read-only, no production mutation):
- Production diary jsonl (read)
- Production relationships.json (read)
- Production carryover.json (read, via render_temporal_block)
- Production memory.db (read summary only, no raw facts)
- Production world/perception_trace.jsonl (read)
- Production soul/agent_*/diary/{date}.jsonl (read)

**Out-of-scope evidence** (NOT exposed to evaluator):
- TTS mp3 (separate concern)
- Bus internal logs
- Scheduler state
- Network metadata
- API keys

### 4.4 Evidence format

Per-dimension evidence packet (JSON):
```json
{
  "scenario_id": "scenario_xxx",
  "agent_id": "agent_yua",
  "user_id": "bryan",
  "timestamp": "ISO 8601",
  "model": "exact_model_id",
  "temperature": 0.85,
  "max_tokens": 500,
  "input": "user message text",
  "composed_context": {
    "system_prompt": "...",
    "memory_context": "...",
    "mood_desc": "...",
    "relationship_block": "...",
    "inner_life": "...",
    "world_context": "...",
    "temporal": "...",
    "bry_block": "..."
  },
  "response": "LLM output text",
  "state_snapshot": {
    "mood": 0.0,
    "memory_facts_count": 3,
    "relationship_confidence": 0.85,
    "world_events_active": 0,
    "temporal_period": "evening"
  },
  "config": {
    "persona_version": "git_hash",
    "prompt_template_version": "git_hash"
  }
}
```

This packet is what the evaluator (LLM or human) sees. No production secrets, no internal state.

---

## 5. Reproducibility Model

### 5.1 What must be fixed for replay

| Variable | Must be fixed? | How to fix |
|----------|----------------|------------|
| scenario | ✓ | Scenario id + fixture |
| input | ✓ | Deterministic user message text |
| context snapshot | ✓ | Save state snapshot at evaluation time |
| model | ✓ | Pin exact model identifier |
| system prompt | ✓ | Pin git hash of persona/prompt files |
| temperature | ✓ | Set to 0 (or fixed value) for evaluator; pin chat model value |
| max_tokens | ✓ | Pin value |
| response_format | ✓ | Pin value (json_object vs text) |
| judge model | ✓ | Pin exact model identifier |
| judge prompt | ✓ | Pin git hash of rubric prompt |
| judge temperature | ✓ | Set to 0 (or very low) |
| evaluation rubric | ✓ | Pin rubric version |
| date | ✗ | Allow natural variation (but log timestamp) |

### 5.2 Reproducibility artifact

Per-scenario evaluation result:
```json
{
  "scenario_id": "...",
  "input": "...",
  "model": "exact",
  "model_version": "...",
  "prompt_template_version": "git_hash",
  "judge_model": "exact",
  "judge_prompt_version": "git_hash",
  "rubric_version": "v1",
  "temperature": 0.0,
  "result": {
    "context_coherence": {"score": 4, "reasoning": "..."},
    "temporal_appropriateness": {"score": 5, "reasoning": "..."},
    ...
  },
  "timestamp": "ISO 8601"
}
```

This artifact is the "evaluation record". Two evaluations of the same scenario with the same model+prompt+version should produce the same (or statistically equivalent) record.

### 5.3 Comparison across implementations

**Goal**: Detect quality regression when:
- LLM model is upgraded
- Prompt template is changed
- Persona is updated
- Context block composition changes (e.g. new relationship_block)

**Method**: Re-run evaluation on a fixed scenario set, compare per-dimension scores.

**NOT goal**: Detect one-shot LLM variance (unavoidable, requires multiple runs + statistics).

---

## 6. PASS / FAIL Semantics

### 6.1 Per-dimension rubric (categorical preferred over numerical)

Per dimension (1-5 Likert with anchors):
- **5 (Excellent)**: Response naturally integrates dimension, no issues
- **4 (Good)**: Response mostly integrates dimension, minor issues
- **3 (Acceptable)**: Response partially integrates dimension, some issues
- **2 (Poor)**: Response ignores dimension, major issues
- **1 (Unacceptable)**: Response contradicts dimension

**Avoid continuous 0.0-1.0 scores** — they imply false precision and are not reproducible.

**Better**: Pairwise comparison ("A is better than B on dimension X") for cases where absolute scoring is too hard.

### 6.2 Aggregate PASS / FAIL

Per-scenario result:
- **PASS**: All 8 dimensions ≥ 3 (acceptable)
- **PARTIAL**: 1-2 dimensions < 3, rest ≥ 3
- **FAIL**: 3+ dimensions < 3, or any dimension = 1

Per-suite result (multiple scenarios):
- **PASS**: 100% scenarios PASS
- **MOSTLY PASS**: 80-99% scenarios PASS
- **PARTIAL**: 50-79% scenarios PASS
- **FAIL**: < 50% scenarios PASS

### 6.3 Judge disagreement handling

When multiple judges (LLM or human) score the same response:
- **High agreement** (all within ±1 Likert): use mean
- **Moderate disagreement** (some ±2): use median, log disagreement
- **High disagreement** (any ±3): flag for human review, do not auto-pass

**Minimum sample size**: 3 judges (LLM or human) per response, median for final score. (Statistical reasoning: median is robust to 1 outlier; 3 is minimum for outlier detection.)

### 6.4 Confidence intervals

Avoid reporting single scores without uncertainty:
- **LLM judge**: 3 runs, report mean + std
- **Human judge**: 3 evaluators, report median + IQR
- **Hybrid**: LLM judge for bulk + 1 human spot-check for calibration

---

## 7. Deterministic vs Subjective Boundary

### 7.1 Deterministic validation (M6.0 scope, already closed)

Per M6.0-3 closeout:

| Dimension | What M6.0 verifies | Status |
|-----------|---------------------|--------|
| Schema | InnerLifeEvent, Provenance, WorldEvent fields | ✓ PASS |
| Ordering | 8 context blocks in correct order | ✓ PASS (A3) |
| Persistence | trace.jsonl, relationships.json, carryover.json writes | ✓ PASS |
| Identity | actor_id, agent_id, BRYAN_ENTITY_ID correctness | ✓ PASS (B2) |
| Gate | gate_proactive_dm 4 states (EMITTED/GATED/UNAVAILABLE/FAILURE) | ✓ PASS (F1-F3) |
| Production isolation | 0 production mutation across 22 tests | ✓ PASS (D4/E5/F5/G4/H4) |
| Frozen contracts | 0 contract change | ✓ PASS |

### 7.2 Subjective validation (M6.0-4 scope, this audit)

| Dimension | Observable | Subjective quality | LLM judge | Human rubric |
|-----------|------------|---------------------|-----------|---------------|
| 1 Context coherence | ✓ (M6.0-2 A3) | "natural weaving" | High | Medium |
| 2 Temporal appropriateness | ✓ (D1) | "uses time correctly" | High | High |
| 3 Relationship continuity | ✓ (B tests) | "reflects relationship" | High | High |
| 4 Memory continuity | ✓ (C tests) | "uses facts without fabrication" | High | High |
| 5 Emotional continuity | ✓ (mood presence) | "matches mood" | Medium | High |
| 6 World-context relevance | ✓ (E tests) | "acknowledges world" | High | High |
| 7 Character/persona consistency | ✓ (system prompt) | "in character" | Medium | High |
| 8 Lived-context coherence | combined | "overall natural" | High | High |

### 7.3 Precedence rule — DETERMINISTIC FAIL overrides SUBJECTIVE PASS

**Hard rule**: deterministic FAIL always wins.

**Example scenarios**:
| Deterministic | Subjective | Result |
|---------------|------------|--------|
| PASS | PASS | **PASS** (full validation) |
| PASS | FAIL | **PARTIAL** (deterministic OK but subjective quality issue) |
| FAIL | PASS | **FAIL** (deterministic override; subjective "pass" ignored) |
| FAIL | FAIL | **FAIL** (both fail) |

**Why**: If contracts are broken, subjective "quality" is meaningless. The response might be beautiful but the architecture is wrong. Don't ship broken architecture with pretty text.

This rule prevents "subjective judge overriding deterministic contract failure" (per work order §7).

### 7.4 Per-ticket scope clarification

**M6.0-4 audit = DESIGN only.** Implementation requires separate ticket(s):
- M6.0-5: LLM-as-judge infrastructure (real LLM call, cost)
- M6.0-6: Human evaluation harness (manual, slow)
- M6.0-7: Hybrid calibration (regression detection)
- M6.0-8: Real-world scenario set (production diary corpus)

Each implementation ticket must respect M6.0-4 design constraints (read-only architecture, no production mutation, no new context blocks, etc.).

---

## 8. Regression Relationship

### 8.1 The 4 cases (per work order §9)

| Deterministic | Subjective | Action |
|---------------|------------|--------|
| PASS + PASS | **Full PASS** | Both validated, ship |
| PASS + FAIL | **PARTIAL** | Subjective quality issue, fix prompt/persona |
| FAIL + PASS | **FAIL** | Deterministic failure overrides subjective PASS |
| FAIL + FAIL | **FAIL** | Both fail, fix architecture first |

### 8.2 M6.0 deterministic suite (current state)

Per M6.0-3 closeout (commit `d34513e`):
- 22/22 PASS (D=4, E=5, F=5, G=4, H=4)
- M5.14-3 (commit `29deab7`) F1-F3 fixed (P3 test design correction)
- 0 production mutation
- 0 frozen contract change

**Deterministic suite is GREEN. M6.0-4 design is ready to add subjective layer on top.**

### 8.3 Subjective suite (future M6.0-5+)

When implemented:
- Run subjective eval on same scenario set
- For each scenario, record deterministic PASS + subjective result
- Aggregate: how many deterministic PASS / subjective FAIL cases?
- Track over time: does prompt change improve subjective without breaking deterministic?

---

## 9. Production Isolation

### 9.1 Subjective evaluation environment (when implemented)

Per work order §10:
- Use isolated fixture / temp data root
- NOT write production memory
- NOT write production relationships
- NOT write production diary
- NOT write production trace
- NOT trigger proactive DM
- NOT trigger scheduler side effects
- NOT modify frozen contracts

**Implementation approach** (when M6.0-5 lands):
- Reuse `tests/_helpers/mock_llm_backend.py` (M6.0-2) for chat path
- Add `tests/_helpers/recording_backend.py` to capture (input, context, response, state snapshot) without mutating production
- Replay recorded (input, context, state) into a separate LLM judge session
- Subjective judge session writes only to evaluation database (e.g. `logs/m6_0_4_evals/{date}.jsonl`)

### 9.2 Boundary verification

For each subjective evaluation:
- Pre-evaluation: snapshot production SHA256
- Post-evaluation: verify production SHA256 unchanged
- If changed → STOP, report P0 BLOCKER

Same pattern as M6.0-3 closeout §3.

---

## 10. Architecture Drift Guard

### 10.1 Required-no-change items (per work order §11)

| Item | Required change? | Status |
|------|------------------|--------|
| New context block | **NO** | M6.0-4 doesn't add any |
| New runtime path | **NO** | Evaluation reads existing state only |
| New production schema | **NO** | Evidence packet is test-only, not in production |
| New production scoring dimension | **NO** | All 8 dimensions derived from M5.x existing context |
| Embedding / vector DB | **NO** | No semantic search |
| Semantic retrieval | **NO** | No embedding-based eval |

### 10.2 What M6.0-4 introduces (test-only, out of production)

| Item | In production? | Notes |
|------|---------------|-------|
| Evidence packet schema | NO (test-only) | Lives in `tests/_fixtures/m6_0_4/` or `logs/m6_0_4_*` |
| LLM judge prompt | NO (test-only) | Lives in `tests/_helpers/judge_prompts/` |
| Evaluation database | NO (test-only) | `logs/m6_0_4_evals/{date}.jsonl` |
| Human rubric | NO (test-only) | `tests/_helpers/rubrics/` |

**Zero production mutation. All subjective evaluation artifacts are test-only.**

### 10.3 Drift detection

If a future M6.x ticket proposes:
- Adding a new context block for evaluation purposes → STOP, propose alternative
- Modifying production prompts to optimize subjective quality → STOP, separate from subjective eval
- Adding new production schema for evaluator signals → STOP

---

## 11. Architectural Findings

### F1: 3 LLM entry points, only 1 has mock infrastructure

**Severity**: INFORMATIONAL

**Description**: 
- LLMProxy (chat): has MockLLMBackend (M6.0-2)
- Diary LLM (`_call_minimax_for_diary`): raw httpx
- Dream LLM (`_call_minimax_for_dream_event`): raw httpx

**Impact on subjective eval**:
- Chat path: easy to record (mock backend captures call_log)
- Diary path: hard to record (no mock layer; would need to monkey-patch httpx or wrap the function)
- Dream path: same as diary

**Recommendation** (out of M6.0-4 scope): Add similar recording layer for diary/dream paths if subjective eval of those is desired.

### F2: Production diary corpus is rich observable data

**Severity**: INFORMATIONAL (positive finding)

**Description**: 11 agents × 20+ days × 2 slots (morning/night) ≈ 400+ diary entries in production.

**Impact on subjective eval**:
- This corpus IS the natural evaluation set
- Each entry has content + source (llm/placeholder) + inner_life_event_id
- Can be evaluated against context snapshot at write time (replay)

**Recommendation**: M6.0-8 (future) could use this corpus as the canonical evaluation set.

### F3: No existing subjective LLM evaluation infrastructure

**Severity**: INFORMATIONAL

**Description**: M6.0 deterministic validation is complete (8 scenarios). Subjective layer is a NEW domain.

**Impact**:
- This audit is the foundation
- Implementation tickets (M6.0-5+) are required
- Bry decision needed on: which Option (A/B/C) to implement first

### F4: M6.0-4 design respects all M5.x frozen contracts

**Severity**: VALIDATION (positive finding)

**Description**: All proposed evaluation dimensions derive from existing M5.x observable state. No new context blocks, no new runtime paths, no new schema.

**Impact**: M6.0-4 can be implemented without modifying any M5.x code or contracts.

---

## 12. Unresolved Decisions (BRY DECISION REQUIRED)

Per work order §4 ("do not directly select one, first do architecture audit") and §11 ("multiple architecture options have material long-term differences, need Bry decision"):

| # | Decision | Options | Recommendation | Required |
|---|----------|---------|----------------|----------|
| 1 | Evaluator architecture | A (LLM-only) / B (Human-only) / C (Hybrid) / D (Heuristics — reject) | C (Hybrid) for production-grade eval | **YES — Bry decision** |
| 2 | Scoring system | Numerical 0.0-1.0 / Categorical Likert / Pairwise comparison | Categorical + pairwise for hard cases | **YES — Bry decision** |
| 3 | Evaluation frequency | Every commit / Every release / On-demand | Every release (subjective is slow/expensive) | **YES — Bry decision** |
| 4 | Judge LLM model | Claude / OpenAI / Local model / Multiple | Claude for diversity, OpenAI for cost | **YES — Bry decision** |
| 5 | Diary/dream subjective eval scope | Include diary/dream LLM or only chat | Chat first (most observable), diary/dream later | **YES — Bry decision** |
| 6 | Human evaluator pool | Bry only / Trained evaluators / Crowdsourced | Bry + 1-2 trained evaluators (low scale) | **YES — Bry decision** |
| 7 | Calibration method | Golden set / Cross-judge agreement / External benchmark | Golden set + cross-judge agreement | **YES — Bry decision** |
| 8 | Subjective eval scenario set | Production diary corpus / Hand-crafted scenarios / Both | Both (production for naturalness, hand-crafted for control) | **YES — Bry decision** |

**8 unresolved decisions. None of these can be made by the audit alone — Bry must decide.**

---

## 13. Stop Conditions Check

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Need frozen contract modification | **NO** | M6.0-4 design uses only existing observable state |
| 2 | Need production runtime architecture modification | **NO** | Evaluation reads existing state only |
| 3 | Subjective vs deterministic conflict cannot be isolated | **NO** | §7.3 precedence rule is clear: deterministic FAIL > subjective PASS |
| 4 | Multiple architecture options with material long-term differences | **YES** | 8 unresolved decisions listed in §12; flagged for Bry decision |
| 5 | Production data mutation | **NO** | Read-only; SHA256 verified before/after |

**4 of 5 stop conditions NOT hit. 1 hit (architecture choice) is documented in §12 as Bry decision required.**

---

## 14. Tests / Regression

### Sanity regression (READ-ONLY, proves audit doesn't change runtime)

Per M6.0-3 / M5.14-3 final state:
- M6.0-3: 22/22 PASS
- M5.8-4: 26/26 PASS
- M5.9-3: 46/46 PASS
- M5.9-3.1: 31/31 PASS
- M5.10-2: 13/13 PASS
- M5.13-3: 29/29 PASS
- M5.2-M5.7 baseline: 619/619 PASS
- **Total**: 786/786 PASS expected to remain unchanged

(M6.0-4 is READ-ONLY design, no test changes, no source changes.)

---

## 15. Production Integrity

### Files tracked before / after M6.0-4 (audit only)

| File | sha256 (before) | sha256 (after) | Status |
|------|-----------------|-----------------|--------|
| `data/soul/agent_yua/relationships.json` | E3C03F51F105B1D7 | E3C03F51F105B1D7 | unchanged |
| `data/agents/agent_yua/carryover.json` | C6BE0753CCCE4E45 | C6BE0753CCCE4E45 | unchanged |

**0 production mutation. M6.0-4 is purely read-only design audit.**

(Background Soul OS server processes may continue to update production data; that's external.)

---

## 16. Git State

```
HEAD = 29deab7 (M5.14-3 closeout) + 1 commit (this audit log)
Working tree: 20 pre-existing untracked artifacts preserved
Modified: 0 source files
Modified: 0 test files
Modified: 0 production files
New: 1 audit log (logs/m6_0_4_subjective_llm_quality_audit.md)
```

---

## 17. Modified Files

| File | Type | Notes |
|------|------|-------|
| `logs/m6_0_4_subjective_llm_quality_audit.md` | new | this audit document |

**0 source/test/production files modified. Audit is purely additive documentation.**

---

## 18. Commit / Push

Single commit:
```
docs(m6.0-4): subjective LLM quality evaluation design audit (READ-ONLY)

Design audit only. 0 production code change. 0 frozen contract change.

8 unresolved decisions documented for Bry decision.
- Evaluator architecture (A LLM / B Human / C Hybrid / D Heuristics)
- Scoring system (Likert / pairwise / numerical)
- Frequency, judge model, scope, evaluators, calibration, scenario set

Architecture drift guard: 0 new context block, 0 new runtime path,
0 new schema, 0 new scoring dimension, 0 embedding/vector.

Subjective evaluation reads existing observable state:
- LLMProxy messages (8-block group / 7-block private)
- Production diary jsonl
- Production relationships.json
- Production carryover.json
- Production world/perception_trace.jsonl
- Production memory.db summary

NOT exposed: raw production DB, unrelated agents' state,
internal credentials, TTS mp3, Bry private metadata.

Deterministic > Subjective precedence: deterministic FAIL overrides
subjective PASS (per M6 cardinal rule).

8 evaluation dimensions derived from M5.x observable:
1. Context coherence (8-block presence + order)
2. Temporal appropriateness (chrono_block use)
3. Relationship continuity (M5.13-3 confidence band use)
4. Memory continuity (M5.10-2 v1 fact use, no fabrication)
5. Emotional continuity (mood_desc reflection)
6. World-context relevance (world_context handling)
7. Character/persona consistency (system_prompt anchor)
8. Lived-context coherence (emergent)

Production isolation: 0 mutation, read-only evidence model,
optional recording backend (test-only).

3 LLM entry points:
- LLMProxy (chat): has MockLLMBackend
- Diary LLM: raw httpx (no mock layer)
- Dream LLM: raw httpx (no mock layer)
Subjective eval of diary/dream requires additional infrastructure
(F1 finding, out of M6.0-4 scope).

Sanity regression: 786/786 PASS expected to remain unchanged.
```

Push to origin/main, final HEAD == origin/main.

---

## 19. Recommended Next Ticket

**M6.0-5 — Subjective LLM Quality Evaluation Infrastructure (M6.0-4 design → implementation)**

Mode: IMPLEMENTATION (after Bry decides on §12 architecture options)

Scope (DRAFT, depends on Bry's §12 decisions):
- Recording backend (`tests/_helpers/recording_backend.py`) for LLMProxy
- Evidence packet schema (per §4.4)
- LLM judge prompts (per §3 Option A or C)
- Scenario set (per §12 decision 8)
- Reproducibility metadata (per §5.1)
- Per-dimension rubric (per §6.1)
- Pre/post SHA256 verification (per §9.2)

Out of scope (separate tickets):
- M6.0-6: Human evaluation harness
- M6.0-7: Hybrid calibration
- M6.0-8: Diary/dream subjective eval (after diary/dream mock infrastructure lands)
- M6.0-9: Real-world scenario regression

Bry must decide 8 unresolved items in §12 before M6.0-5 can start.

---

**M6.0-4 status: CLOSED, READ-ONLY DESIGN AUDIT, 0 production mutation, 0 frozen contract change, 8 unresolved decisions for Bry.**
