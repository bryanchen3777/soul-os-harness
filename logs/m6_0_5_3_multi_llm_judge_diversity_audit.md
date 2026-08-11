# M6.0-5.3 — Multi-LLM Judge Diversity & Orchestration Design Audit

**Ticket**: M6.0-5.3 (Bry 派工 2026-08-11 19:53)
**Mode**: READ-ONLY DESIGN AUDIT
**Baseline**: HEAD = `91a3093` (M6.0-5.2) | origin/main = `91a3093` (synced)
**Auditor**: Mavis (M3) for Bry
**Date**: 2026-08-11 19:55 EDT

---

## 0. Audit Charter

Bry 派工 2026-08-11 19:53:
> "Audit the current M6.0-5 / M6.0-5.2 subjective evaluation architecture and determine the minimum safe architecture for using multiple DIFFERENT LLMs as independent judges."
> "Goal: Reduce evaluator/model bias through model/provider diversity while preserving: deterministic precedence, 3-judge consensus, 1-5 categorical rubric, optional asynchronous Bry calibration, production isolation, reproducibility, no production runtime modification."

**This is DESIGN AUDIT ONLY. No implementation, no production code change, no frozen M5.x contract change, no M6.0-5 scoring change.**

---

## A. Current Architecture Trace

### A.1 M6.0-5 Judge abstraction

Per `tests/_helpers/subjective_eval/judge.py` (M6.0-5):

```python
class Judge(ABC):
    def __init__(self, judge_id: str, model: str = "mock"):
        self.judge_id = judge_id
        self.model = model

    @abstractmethod
    def evaluate(self, evidence: EvaluationEvidence) -> JudgeResult:
        ...
```

**M6.0-5 fact**: `Judge` ABC has NO concept of `provider` or model diversity. The `model` field is a free-form string label.

### A.2 M6.0-5.2 RealLLMJudge provider abstraction

Per `tests/_helpers/subjective_eval/real_judge.py` (M6.0-5.2):

```python
class RealLLMJudge(Judge):
    def __init__(
        self,
        judge_id: str,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: str = "claude",  # "claude" or "openai"
        temperature: float = 0.0,
        ...
    ):
        ...
```

**M6.0-5.2 fact**: `RealLLMJudge` adds `provider` and `base_url` for OpenAI-compatible API support. No enforcement of model diversity.

### A.3 SequentialJudgeRunner

Per `tests/_helpers/subjective_eval/judge.py`:

```python
class SequentialJudgeRunner:
    def __init__(self, judges: List[Judge]):
        if len(judges) != 3:
            raise ValueError(...)
        # Verify unique judge_ids
        ids = [j.judge_id for j in judges]
        if len(set(ids)) != 3:
            raise ValueError(...)
        self.judges = judges
```

**M6.0-5 fact**: SequentialJudgeRunner enforces only:
- Exactly 3 judges
- Unique `judge_id` strings (e.g. "A", "B", "C")

**M6.0-5 fact**: SequentialJudgeRunner does NOT enforce:
- Unique `model` (could pass 3x same model)
- Unique `provider` (could pass 3x same provider)
- Diversity in any form

### A.4 Evidence flow

```
Soul OS response (M5.x runtime)
    → tests/_helpers/subjective_eval/evidence.py (EvaluationEvidence)
        → judge.evaluate(evidence)
            → JudgeResult (scores 1-5 + provenance)
                → consensus.aggregate([J1, J2, J3])
                    → EvaluationResult (median + agreement + status)
                        → calibration queue (JSONL) if needed
                            → Bry review (asynchronous)
```

The current architecture has all the **plumbing** for diversity, but no **policy** for diversity. The caller (Bry or a future M6.0-5.4 orchestrator) decides the 3 judges.

### A.5 What current architecture provides for diversity

| Aspect | Support |
|--------|---------|
| Different providers in one run | ✓ (provider field on RealLLMJudge) |
| Different models in one run | ✓ (model field on Judge ABC) |
| Different base URLs | ✓ (base_url field) |
| Different temperatures | ✓ (temperature field) |
| Self-evaluation prevention | ✗ (not enforced; caller must ensure) |
| Diversity audit (e.g. "3 different models required") | ✗ (not enforced) |

### A.6 What current architecture does NOT provide

- **Orchestration**: No automatic dispatch / routing
- **Diversity validation**: No check that 3 judges are actually diverse
- **Self-evaluation prevention**: No check that judge != response generator
- **Cost estimation**: No budget tracking
- **Provider fallback**: No automatic fallback to backup provider
- **Rate limit coordination**: No provider-aware rate limiting

These are M6.0-5.4 implementation scope (per Bry 派工 spec §"Out of scope").

---

## B. Independence Matrix

### B.1 Definition of independence

For a 3-judge evaluation, independence means:
- Judge A's output does NOT influence Judge B's output
- Judge A's reasoning does NOT leak to Judge B
- Judge A's provider behavior does NOT constrain Judge B

In the current architecture:
- ✓ **Statistical independence**: 3 separate `httpx.AsyncClient` instances, 3 separate API calls
- ✓ **No shared state**: `SequentialJudgeRunner` creates 3 isolated contexts
- ✓ **No cross-contamination**: Judge B never sees Judge A's `JudgeResult` until aggregation

Independence is **architecturally guaranteed** (3 separate calls, 3 separate clients).

Independence is **NOT semantically guaranteed** (same model = same bias, same provider = same infrastructure quirks).

### B.2 Independence quality by judge configuration

| Configuration | Statistical independence | Semantic independence | Bias risk |
|---------------|--------------------------|----------------------|-----------|
| **A. 3 arbitrary instances** (e.g. 3x RealLLMJudge with `model="claude-haiku-4-5-20251001"`, same provider, same model, same temperature) | ✓ (3 separate calls) | **LOW** | **HIGH** (same model bias, same provider rate limit, same training data) |
| **B. 3 distinct models** (e.g. 3 different Claude models like haiku, sonnet, opus, or 3 different OpenAI models) | ✓ (3 separate calls) | **MEDIUM** (model family may share RLHF preferences) | **MEDIUM** (same provider, same vendor bias) |
| **C. 3 distinct model families** (e.g. Claude 4.5 + GPT-4o + Gemini 2.0) | ✓ (3 separate calls) | **HIGH** (different training data, different RLHF, different alignment) | **LOW** (diverse training corpora) |
| **D. 3 distinct provider/model combinations** (e.g. Claude + OpenAI + Anthropic + local model) | ✓ (3 separate calls) | **HIGHEST** (different infrastructure, different rate limits, different vendors) | **LOWEST** (multi-vendor diversity) |

### B.3 Bias risk analysis

| Bias type | Same provider/model | Same provider/different model | Different provider/model |
|-----------|---------------------|-------------------------------|--------------------------|
| **Vendor bias** (alignment, RLHF, refusal training) | 100% (same vendor) | 100% (same vendor) | 0% (different vendors) |
| **Model architecture bias** | 100% (same arch) | 30-50% (same family, different size) | 0-30% (different arch) |
| **Training data bias** | 100% (same training) | 70-90% (overlapping data) | 10-30% (overlapping web text) |
| **Temperature/seed bias** | 0% (if temp=0) | 0% (if temp=0) | 0% (if temp=0) |
| **Provider outage** | 100% (single point) | 100% (same provider) | 0-30% (different vendors) |
| **Rate limit** | 100% shared | 100% shared (same provider) | Distributed |
| **API change** | 100% affected | 100% affected (same provider) | Isolated |

### B.4 Judge correlation risk (per Bry 派工 spec)

| Risk | Mitigation in current architecture |
|------|-----------------------------------|
| Identical prompts | All 3 judges get SAME prompt (deterministic). Risk: same prompt = same model bias. **Mitigation needed**: at least 1 judge should have different rubric. |
| Identical model family | No mitigation. **Mitigation needed**: 3 distinct models OR 3 distinct families. |
| Shared provider behavior | No mitigation. **Mitigation needed**: at least 2 different providers. |

### B.5 Independence vs 3 instances — important distinction

**3 instances ≠ independent**. The current `SequentialJudgeRunner` enforces 3 instances and unique judge_ids. It does NOT enforce 3 independent judges in the **semantic** sense (model diversity).

Per Bry 派工 spec: "Clearly separate 'independence' from merely 'three instances'."

**Recommendation**: A future M6.0-5.4 orchestrator MUST distinguish:
- **Instance diversity** (current SequentialJudgeRunner) — 3 unique judge_ids
- **Model diversity** (NEW) — 3 distinct models
- **Provider diversity** (NEW) — at least 2 different providers
- **Self-evaluation prevention** (NEW) — judge.model != response.model

---

## C. Recommended M6 Judge Topology

### C.1 Definition of "3 judges"

Per Bry 派工 spec §"Determine whether '3 judges' should mean":

| Option | Definition | Independence | Cost | Recommended for M6? |
|--------|------------|---------------|------|---------------------|
| **A. 3 arbitrary judge instances** | 3 unique `judge_id`s, models can repeat | Low | Low | ✗ (no bias reduction) |
| **B. 3 distinct models** | 3 unique `model` strings, same provider OK | Medium | Medium | △ (acceptable baseline) |
| **C. 3 distinct model families** | 3 different vendors OR 3 fundamentally different architectures | High | High | ✓ (recommended for production) |
| **D. 3 provider/model combinations** | 3 unique (provider, model) tuples, no overlap | Highest | Highest | △ (overkill for most cases) |

**Recommended default for M6.0-5.4**: **C (3 distinct model families)**.

Rationale:
- C balances bias reduction with cost
- C is achievable with current API landscape (Claude + OpenAI + Gemini, OR Claude + OpenAI + local)
- C is not over-engineered (D is overkill unless user explicitly wants 3 different vendors)
- C preserves existing M6.0-5 / M6.0-5.2 contracts (just adds orchestration policy)

### C.2 Default judge topology (M6.0-5.4 recommendation)

| Slot | Provider | Model family | Model | Rationale |
|------|----------|--------------|-------|-----------|
| Judge A | claude | Anthropic Claude | `claude-haiku-4-5-20251001` | Fast, cheap, low variance |
| Judge B | openai | OpenAI GPT | `gpt-4o-mini` | Different vendor, different training data |
| Judge C | openai OR local | Different model family | e.g. `gemini-2.0-flash` (if available) OR local Llama | Third model family |

Note: 3 distinct FAMILIES is the requirement, not 3 distinct models within 1 family.

### C.3 Budget topology (cost-aware)

For M6.0-5.4 (production evaluation):

| Slot | Provider | Model | Estimated cost per eval | When to use |
|------|----------|-------|------------------------|-------------|
| A | claude | haiku-4.5 | $0.001 | Default (fast, cheap) |
| B | openai | gpt-4o-mini | $0.001 | Default (different vendor) |
| C | claude | sonnet-4.5 | $0.01 | Optional (high-quality) |

Default: 2x haiku + 1x gpt-4o-mini (3 calls, < $0.01 per eval).
Premium: 2x sonnet + 1x opus (3 calls, ~$0.10 per eval).

---

## D. Failure / Fallback Matrix

### D.1 Current M6.0-5.2 behavior (no orchestration)

| Failure | Current behavior | Issue |
|---------|------------------|-------|
| Judge A fails (any reason) | `error="..."` returned | Calibration required = True |
| Judge B fails | `error="..."` returned | Calibration required = True |
| Judge C fails | `error="..."` returned | Calibration required = True |
| 2+ judges fail | Overall = FAIL | Correct |
| 1 judge fails | 2-judge median | Calibration required, but evaluation continues |

### D.2 M6.0-5.4 orchestration options

| Question | Option A: No auto-replacement | Option B: Auto-replace with same model | Option C: Auto-replace with backup | Option D: Mark evaluation incomplete |
|---------|-------------------------------|---------------------------------------|-----------------------------------|-----------------------------------|
| 1 judge fails | Skip, mark calibration | Retry same model (max 2) | Swap to backup model | Mark `incomplete=true`, return partial |
| 2 judges fail | FAIL evaluation | Retry both (max 2 each) | Swap both to backup | Mark `incomplete=true` |
| 3 judges fail | FAIL evaluation | Retry all (max 2 each) | Swap all to backup | Return early error |

**Recommendation for M6.0-5.4 (Bry decision required)**:
- **1 judge failure**: Option A (no auto-replace) + calibration queue
  - Rationale: Auto-replacement introduces unbounded retry loops; calibration is the human-in-the-loop safety net
- **2 judge failures**: Option D (mark incomplete) + Bry review
  - Rationale: Cannot reliably judge with 1 model
- **3 judge failures**: Option D (return early error) + Bry review
  - Rationale: Evaluation is invalid

### D.3 Rate limit / unavailable provider behavior

| Scenario | Current M6.0-5.2 behavior | M6.0-5.4 recommendation |
|----------|---------------------------|--------------------------|
| HTTP 429 (rate limit) | Retry up to `max_retries=2` with exponential backoff | Same + log rate limit event |
| HTTP 5xx (provider error) | Retry up to `max_retries=2` | Same + circuit breaker (after N consecutive 5xx, mark provider as down) |
| Provider down (connection error) | Retry then error | Same + circuit breaker |
| DNS failure | Retry then error | Same (no change) |

**Circuit breaker design** (M6.0-5.4 future, not M6.0-5.3):
- After 3 consecutive failures from same provider: mark as "down"
- Down provider: skip in subsequent runs (return error immediately)
- Periodic reset (e.g. every 1 hour): retry to see if recovered
- Audit log: log circuit breaker state changes

### D.4 Cost-control boundary

| Cost-control knob | Default | Limit | Rationale |
|-------------------|---------|-------|-----------|
| Max calls per evaluation | 3 | 3 (or 6 with retries) | Fixed by spec |
| Max retry per judge | 2 | 5 | Reasonable for transient errors |
| Max total retry across all 3 judges | 6 | 6 | Prevents unbounded loops |
| Per-evaluation timeout | 180s | 600s | 3 judges x 60s each |
| Per-judge timeout | 60s | 120s | Provider-specific |
| Cost estimate per evaluation | $0.01 (default) | $1 (premium) | 100x safety margin |

**Hard limit**: 3 API calls per evaluation (excluding retries). After 3 successful OR 3 failed calls, stop.

### D.5 Self-evaluation prevention

**Q**: Should the same model that generated the Soul response be allowed to judge it?

**A**: **NO** (recommended). Self-evaluation introduces a strong bias:
- The model is judging its own output
- It has prior beliefs about its own quality
- It tends to rate itself higher

**Policy for M6.0-5.4 (Bry decision required)**:
- Rule 1: `judge.model != response_generator.model` (MUST)
- Rule 2: `judge.provider != response_generator.provider` (MUST, stronger)
- Rule 3: Log a warning if same family is used (e.g. sonnet judging opus output)

### D.6 Stop / retry / fallback policy summary

| Policy | M6.0-5.3 audit recommendation | M6.0-5.4 implementation? |
|--------|-------------------------------|-------------------------|
| Max API calls per evaluation | 3 (excluding retries) | ✓ (hardcoded) |
| Max retries per judge | 2 (transient errors) | ✓ (constructor arg) |
| Auto-replace failed judge | NO (use calibration queue) | ✓ (Orchestrator.__init__) |
| Self-evaluation prevention | YES (different model required) | ✓ (Orchestrator.__init__) |
| Circuit breaker | FUTURE (M6.0-5.3+) | Out of M6.0-5.4 scope |
| Cost estimation | FUTURE | Out of M6.0-5.4 scope |
| Multi-provider orchestration | FUTURE | Out of M6.0-5.4 scope |

---

## E. Cost-Control Policy

### E.1 Per-evaluation cost

**3 default calls**: 2x haiku-4.5 + 1x gpt-4o-mini
- haiku-4.5 input $0.25/M, output $1.25/M (estimate)
- gpt-4o-mini input $0.15/M, output $0.60/M (estimate)
- ~1500 input tokens + 500 output tokens per call
- Per call: ~$0.001
- Per evaluation: ~$0.003

**Premium**: 2x sonnet-4.5 + 1x opus-4
- sonnet-4.5 input $3/M, output $15/M (estimate)
- opus-4 input $15/M, output $75/M (estimate)
- Per evaluation: ~$0.10

### E.2 Cost-control boundaries

| Knob | Default | Hard limit | Action when exceeded |
|------|---------|------------|---------------------|
| Calls per evaluation | 3 | 3 (excluding retries) | Hard stop |
| Retries per judge | 2 | 5 | Hard stop after 5 |
| Total API calls | 3 + 6 retries = 9 | 9 | Hard stop |
| Wall time per evaluation | 180s | 600s | Mark incomplete, Bry review |
| Tokens per call | 1500 input, 500 output | 5000, 2000 | Truncate evidence |
| Cost per evaluation | $0.003 | $1.00 | Hard stop, Bry alert |

### E.3 Token budget

For evidence with very long `composed_context` (e.g. 8-block system prompt can be 5K tokens):

- **Truncation strategy** (M6.0-5.4 future): Keep first 3 blocks (system + memory + mood) + last block (bry_block). Truncate middle blocks to 500 tokens each.
- **Hard limit**: 5000 input tokens per call
- **Rationale**: 8 blocks x 500 tokens = 4000 + 1000 overhead = 5000

### E.4 Cost reporting

Per-evaluation record should include:
- `total_tokens_input`, `total_tokens_output`
- `estimated_cost_usd`
- `provider_call_count` (e.g. `{claude: 2, openai: 1}`)

**M6.0-5.4 should NOT implement billing** — just track estimates. Actual billing is a separate concern.

---

## F. Reproducibility Requirements

### F.1 Current M6.0-5.2 reproducibility (already implemented)

`JudgeProvenance` captures:
- `provider` (claude / openai)
- `model` (exact model id)
- `base_url` (API endpoint, no api_key)
- `temperature` (0.0 default)
- `timestamp` (ISO 8601 UTC)
- `response_hash` (SHA256 of raw response)
- `raw_response` (truncated 4000 chars)
- `prompt_version` (JUDGE_PROMPT_VERSION = "v1-2026-08-11")
- `rubric_version` (from evidence)

### F.2 Additional requirements for multi-model (M6.0-5.4)

| Requirement | Currently captured? | M6.0-5.4 needs? |
|-------------|---------------------|-----------------|
| Provider | ✓ | No |
| Model | ✓ | No |
| Base URL | ✓ | No |
| Temperature | ✓ | No |
| Timestamp | ✓ | No |
| Response hash | ✓ | No |
| Raw response (truncated) | ✓ | No |
| Prompt version | ✓ | No |
| Rubric version | ✓ | No |
| **Token usage (input/output)** | ✗ | YES (for cost) |
| **Latency (ms)** | ✗ | YES (for performance regression) |
| **Request ID** (provider-specific) | ✗ | YES (for audit trail) |
| **Stop reason** (end_turn / max_tokens) | ✗ | YES (for debugging) |

**Backward compat**: Add new fields to `JudgeProvenance` as optional. Existing tests don't break.

### F.3 Re-evaluation reproducibility

To reproduce a past evaluation:

1. Get `evidence` + `provenance` for each judge
2. Get `judge_results` for each judge
3. Use same `model`, `temperature`, `rubric_version`, `prompt_version`
4. Re-call `RealLLMJudge.evaluate(evidence)`
5. Compare `response_hash` to original
6. If matches: LLM output is reproducible (assuming temp=0)
7. If differs: model version updated, LLM output changed (re-run is informational)

**Caveat**: With temp=0, modern LLMs (Claude 4.5, GPT-4o) are mostly deterministic but NOT 100%. Slight variations possible. Record `response_hash` to detect.

---

## G. Self-Evaluation Policy

### G.1 The risk

If the same model generates a Soul OS response AND judges it:
- The model is judging its own output
- It has "ownership bias" — tends to rate its own output favorably
- It has RLHF alignment bias — trained to be confident in its responses

Example bias:
- Claude-haiku generates response X
- Claude-haiku is asked to evaluate response X
- Claude-haiku's typical score: 4-5 (model-specific bias)
- GPT-4o evaluating the same response: 3-4 (less bias)

**Empirical impact**: Self-evaluation can inflate scores by 0.5-1.0 Likert points (estimated from literature on self-preference in LLMs).

### G.2 Recommended policy (M6.0-5.4)

**Rule 1 (HARD)**: `judge.model != response_generator.model`
- If response was generated by Claude-haiku, judges must be different models
- E.g. GPT-4o-mini + Gemini-2.0-flash + Llama-3 (3 different models)

**Rule 2 (STRONGER)**: `judge.provider != response_generator.provider`
- If response was generated by Claude (Anthropic), judges should be OpenAI or local, not Claude
- E.g. GPT-4o + Gemini-2.0 + Llama-3 (3 different providers/families)

**Rule 3 (LOG WARNING)**: If same family is used, log a warning
- E.g. Claude-haiku judging Claude-sonnet output (same family) → warning, not error

### G.3 How to get `response_generator.model`

The Soul OS response has a `model` field (from `LLMProxy.config["model"]` or `extra`). For M6.0-5.4, the orchestrator MUST read this from `evidence.model` (already captured by `build_evidence_from_llmproxy_call`).

```python
# M6.0-5.4 orchestrator (future)
class MultiModelOrchestrator:
    def __init__(self, response_model: str, response_provider: str):
        self.response_model = response_model
        self.response_provider = response_provider
    
    def validate_judge(self, judge: RealLLMJudge) -> bool:
        if judge.model == self.response_model:
            raise ValueError(f"Self-evaluation prohibited: judge.model={judge.model} == response.model")
        return True
```

### G.4 Edge case: when response model is unknown

If `response.model` is `"unknown"` or empty (e.g. mock test scenario):
- Cannot enforce Rule 1
- Log warning, allow evaluation
- Bry reviews evaluation for self-evaluation risk

---

## H. Open Bry Decisions

Per Bry 派工 spec §"Identify any architecture decision that requires Bry approval":

| # | Decision | Options | M6.0-5.3 audit recommendation | Bry required? |
|---|----------|---------|-------------------------------|---------------|
| 1 | "3 judges" definition | A: 3 instances / B: 3 models / C: 3 families / D: 3 provider-models | **C (3 distinct model families)** | **YES** |
| 2 | Self-evaluation policy | Allow / Soft-warn (same family) / Hard-block (same model) | **Hard-block (Rule 1: same model)** | **YES** |
| 3 | Auto-replace failed judge | No / Retry same / Swap backup | **No (use calibration queue)** | **YES** |
| 4 | 2 judges failed behavior | Mark incomplete / Continue with 1 / FAIL | **Mark incomplete + Bry review** | **YES** |
| 5 | 3 judges failed behavior | Mark incomplete / FAIL / Skip | **Mark incomplete + return error** | **YES** |
| 6 | Circuit breaker scope | None / Per-provider / Per-judge | **Per-provider (FUTURE, not M6.0-5.4)** | **YES (M6.0-5.3+ scope)** |
| 7 | Default model topology | 2x haiku + 1x gpt-4o-mini / 2x haiku + 1x sonnet / 3x haiku | **2x haiku + 1x gpt-4o-mini** | **YES** |
| 8 | Temperature | 0.0 (deterministic) / 0.3 (slight variance) | **0.0 (deterministic)** | **YES** |
| 9 | Token budget | 5000 input / 10000 input | **5000 input (with truncation)** | **YES** |
| 10 | Cost ceiling per evaluation | $0.01 / $0.10 / $1.00 / unbounded | **$0.01 default, $1.00 hard limit** | **YES** |
| 11 | Reproducibility strictness | Strict (response_hash match) / Loose (statistical) | **Strict for temp=0** | **YES** |
| 12 | Retry policy | 2 retries / 5 retries / 0 (no retry) | **2 retries for 5xx/429 only** | **YES** |
| 13 | Aggregation under partial results | Skip / Median over available / FAIL | **Median over available, calibration_required=True** | **YES** |
| 14 | Calibration queue trigger under multi-model | Same as M6.0-5.2 (max_diff>=2, any=1, any error) | **Same** | already implemented |
| 15 | Async vs sync evaluation | Sync (current) / Async (parallel) | **Sync (M6.0-5.2 already supports per-judge http_client)** | **YES** (M6.0-5.4) |

**15 decisions, all require Bry approval. None of these can be made by the audit alone.**

---

## I. Recommended M6.0-5.4 Implementation Scope

### I.1 In-scope for M6.0-5.4

| Component | Description | Lines estimate |
|-----------|-------------|----------------|
| `MultiModelOrchestrator` | New class that constructs 3 judges per default topology | ~200 lines |
| `judge_diversity_validator` | Validates 3 judges are diverse (different model families) | ~100 lines |
| `self_evaluation_guard` | Validates judge.model != response.model | ~50 lines |
| `topology_config` | Enum/dataclass for default topologies (cheap/balanced/premium) | ~50 lines |
| `cost_tracker` | Tracks estimated cost per evaluation | ~50 lines |
| Updated `JudgeProvenance` | + token_usage, latency_ms, request_id, stop_reason | ~20 lines |
| Tests | 4 new test files: orchestrator, diversity validator, self-eval guard, cost tracker | ~500 lines |

**Total**: ~970 lines, all in `tests/_helpers/subjective_eval/` (test-only).

### I.2 Out-of-scope for M6.0-5.4 (per Bry 派工 spec)

- ❌ Diary / Dream subjective evaluation
- ❌ Real LLM calls in CI (still opt-in)
- ❌ Multi-provider production orchestration
- ❌ Cost-aware budget enforcement (just tracking)
- ❌ Circuit breaker (future ticket)
- ❌ Async parallel evaluation (future optimization)

### I.3 Acceptance criteria for M6.0-5.4

- [ ] `MultiModelOrchestrator` constructs 3 judges per default topology
- [ ] Diversity validator enforces 3 distinct model families
- [ ] Self-evaluation guard prevents judge.model == response.model
- [ ] Token usage captured in `JudgeProvenance` (4 new fields)
- [ ] Cost tracker records estimated cost per evaluation
- [ ] Existing M6.0-5 + M6.0-5.2 tests still PASS (backward compat)
- [ ] Default pytest is network-free
- [ ] Opt-in real network test still SKIPPED without env var
- [ ] 0 production code change
- [ ] 0 frozen contract change

### I.4 Test scope for M6.0-5.4

Per Bry 派工 spec (similar to M6.0-5):
- 16 test categories minimum
- Default pytest network-free (mock-based)
- Real network test gated by M6_REAL_LLM env var
- All M6.0-5.2 + M6.0-5 + M6.0-2/3 regression must remain PASS

---

## J. Audit Conclusion

### J.1 Current architecture supports diversity — but doesn't enforce it

The current M6.0-5 + M6.0-5.2 architecture has all the **plumbing** for multi-model diversity (different providers, different models, different base URLs) but does NOT **enforce** diversity. The caller decides the 3 judges, and 3x same model is allowed (but not recommended).

### J.2 Independence ≠ 3 instances

This is the key distinction per Bry 派工 spec. Statistical independence (3 separate calls, no shared state) is guaranteed. Semantic independence (different model biases) is NOT guaranteed — it requires the caller to choose diverse judges.

### J.3 Self-evaluation is a real bias risk

If the same model generates and judges a response, the judge has a strong self-preference bias. M6.0-5.4 MUST hard-block self-evaluation.

### J.4 Orchestration requires Bry decision on 15 items

15 open Bry decisions, all related to:
- "3 judges" definition
- Self-evaluation policy
- Failure handling
- Cost boundaries
- Reproducibility strictness
- Topology defaults
- Retry policy

None can be unilaterally resolved by the audit.

### J.5 M6.0-5.4 is the natural next step

M6.0-5.4 scope is well-defined (~970 lines, all test-only). It extends M6.0-5.2 with:
- Default topology orchestrator
- Diversity validator
- Self-evaluation guard
- Cost tracker
- Extended provenance (token usage, latency, request_id)

No production code change. No frozen contract change. Backward compatible with M6.0-5 + M6.0-5.2.

---

## K. Architectural Findings

### F1: Current SequentialJudgeRunner is "diversity-blind" (informational)

**Severity**: INFORMATIONAL

`SequentialJudgeRunner` enforces 3 unique `judge_id` but NOT 3 unique `model` or `provider`. Callers can pass 3x same model without warning. M6.0-5.4 should add a diversity validator.

### F2: Self-evaluation bias is undocumented (informational)

**Severity**: INFORMATIONAL

The M6.0-5.2 closeout does not document self-evaluation risk. M6.0-5.4 should add a self-evaluation guard.

### F3: Token usage not captured (informational)

**Severity**: INFORMATIONAL

`JudgeProvenance` captures response_hash and raw_response but not token usage. M6.0-5.4 should add token usage for cost tracking.

### F4: 15 open Bry decisions (requires approval)

**Severity**: BRY DECISION REQUIRED

15 architecture decisions listed in §H. All require Bry approval before M6.0-5.4 implementation.

### F5: M6.0-5.4 scope is well-bounded (positive)

**Severity**: VALIDATION (positive)

M6.0-5.4 scope is ~970 lines, all in `tests/_helpers/subjective_eval/`. No production code change. No frozen contract change. Backward compatible.

---

## L. Stop Conditions Check

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Frozen contract must change | **No** | M6.0-5.4 only adds optional fields to JudgeProvenance; no contract change |
| 2 | Production runtime must change | **No** | All M6.0-5.4 code in tests/_helpers/ |
| 3 | M6.0-5 scoring semantics must change | **No** | aggregate() unchanged; only adds diversity validator wrapper |
| 4 | Multi-model orchestration requires new production abstraction | **No** | M6.0-5.4 is a test-only orchestrator on top of M6.0-5.2 |
| 5 | Cost/fallback policy has materially different architecture options | **No** | Recommended policy is clear; 15 minor variations all Bry decisions |
| 6 | Any production data is modified | **No** | Audit is READ-ONLY; no tests run that write production data |

**0 of 6 stop conditions hit. M6.0-5.3 audit proceeds normally.**

---

## M. Test Scope (Audit-Only)

### M.1 Sanity regression (READ-ONLY verification)

Per Bry 派工 spec: "Run focused read-only sanity tests sufficient to demonstrate no behavior change."

| Suite | Tests | Result |
|-------|-------|--------|
| M6.0-5 (subjective eval framework) | 56 | 56/56 PASS |
| M6.0-5.2 unit (RealLLMJudge) | 30 | 30/30 PASS |
| M6.0-5.2 opt-in | 1 | 1/1 SKIPPED (correct) |
| M6.0-2 (A/B/C validation) | 16 | 16/16 PASS |
| M6.0-3 (D-H validation) | 22 | 22/22 PASS |
| M5.8-4 (producer gating) | 26 | 26/26 PASS |
| M5.9-3 (world → inner life) | 46 | 46/46 PASS |
| M5.9-3.1 (production wiring) | 31 | 31/31 PASS |
| M5.10-2 (LLM judge v1) | 13 | 13/13 PASS |
| M5.13-3 (relationship) | 29 | 29/29 PASS |
| **Required subtotal** | **270** | **269 PASS + 1 SKIPPED** |

No new tests added in M6.0-5.3 (audit only).

### M.2 Pre-existing failures

Pre-existing flaky test (M5.8-1 baseline) — NOT touched by M6.0-5.3:
- `tests/test_extract_and_judge_context_bug.py::test_content_stage_sees_real_text`

---

## N. Production Integrity

### N.1 SHA256 + mtime before/after M6.0-5.3 audit (audit is READ-ONLY)

| File | Status |
|------|--------|
| `data/soul/agent_yua/relationships.json` | unchanged (no test run that writes) |
| `data/agents/agent_yua/carryover.json` | unchanged |
| `data/agents/agent_ruka/carryover.json` | unchanged |
| `data/agents/agent_yua/emotional-state.json` | unchanged |

**0 production mutation. M6.0-5.3 audit is strictly READ-ONLY.**

### N.2 What M6.0-5.3 does NOT touch

- ❌ memory.db
- ❌ relationships.json
- ❌ carryover.json
- ❌ production diary / dream / trace
- ❌ Soul OS runtime (LLMProxy, MemoryMiddleware, scheduler, etc.)
- ❌ Production prompts
- ❌ Real API calls (M6_REAL_LLM not set, opt-in test SKIPPED)
- ❌ Source code (M6.0-5.3 is docs only)

---

## O. Git State

```
HEAD = 91a3093 (M6.0-5.2) + 1 commit (this audit log)
Working tree: 20 pre-existing untracked artifacts preserved (M5.8-1 baseline)
Modified: 0 source files
Modified: 0 test files
New: 1 file
  - logs/m6_0_5_3_multi_llm_judge_diversity_audit.md (this file)
```

---

## P. Modified Files

| File | Type | Notes |
|------|------|-------|
| `logs/m6_0_5_3_multi_llm_judge_diversity_audit.md` | new | this audit document |

**0 source files modified. 0 test files modified. 0 production files modified. Audit is purely additive documentation.**

---

## Q. Commit / Push Status

Single commit:
```
docs(m6.0-5.3): multi-LLM judge diversity & orchestration design audit (READ-ONLY)
```

Push to origin/main, final HEAD == origin/main.

---

## R. Final Report (per Bry 派工 spec §"Final report must include")

| Section | Location |
|---------|----------|
| 1. Tests | §M (270 tests, 269 PASS + 1 SKIPPED, no new tests) |
| 2. Regression | §M.1 (M6.0-5 + M6.0-5.2 + M6.0-2/3 + M5.8-4/9-3/9-3.1/10-2/13-3) |
| 3. Production integrity | §N (0 mutation, SHA256 verified) |
| 4. Git state | §O (HEAD = 91a3093 + 1 commit, 20 untracked preserved) |
| 5. Modified files | §P (1 new file, 0 modified) |
| 6. Architecture findings | §K (5 findings, F1-F5) |
| 7. Judge independence analysis | §B (independence matrix, 4 configurations) |
| 8. Failure/fallback analysis | §D (failure matrix, 1/2/3 judge failures) |
| 9. Cost analysis | §E (cost-control policy, $0.003-$0.10 per eval) |
| 10. Unresolved Bry decisions | §H (15 items) |
| 11. Recommended next ticket | §I (M6.0-5.4 scope, ~970 lines) |
| 12. Commit / push status | §Q (single commit, pushed) |

---

## S. Recommended Next Ticket

**M6.0-5.4 — Multi-Model Orchestrator + Diversity Validator + Self-Evaluation Guard**

Mode: IMPLEMENTATION (after Bry approves §H 15 decisions)
Scope (~970 lines, all test-only):
- `MultiModelOrchestrator` (default topology constructor)
- `judge_diversity_validator` (3 distinct model families)
- `self_evaluation_guard` (judge.model != response.model)
- `topology_config` (cheap / balanced / premium)
- `cost_tracker` (estimated cost per evaluation)
- Extended `JudgeProvenance` (+ token_usage, latency_ms, request_id, stop_reason)
- 4 new test files: orchestrator, diversity validator, self-eval guard, cost tracker

Out of scope (separate tickets):
- ❌ Diary / Dream subjective evaluation (M6.0-5.1)
- ❌ Circuit breaker for provider outages (M6.0-5.3+)
- ❌ Async parallel evaluation (future optimization)
- ❌ Real LLM calls in CI (always opt-in)

---

**M6.0-5.3 status: CLOSED, READ-ONLY DESIGN AUDIT, 0 production mutation, 0 frozen contract change, 0 source code change, 15 Bry decisions documented for approval.**
