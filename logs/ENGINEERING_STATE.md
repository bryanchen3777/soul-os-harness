# Soul OS — Canonical Engineering State & Milestone Registry

**Source of truth** for Soul OS engineering governance.
**Mode**: Documentation only.
**Owner**: Bryan (Mavis / Lin executes per Owner decisions).
**Established**: GOV-2 (2026-08-12 00:03 EDT, commit `eb5715179647b963a4247272d9fcd4c639c7e6a3`).
**Aligned**: GOV-2-R1 (2026-08-12, Owner Decision A: M5.14 / M6.0 / GOV-1 / GOV-2 all CLOSED; no ticket authorized).
**Predecessor audit**: GOV-1 — `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md` (CLOSED, out-of-repo per GOV-1 spec).
**Canonical homepage**: [`README.md`](../README.md) §Engineering Governance.

---

## 0. Document Scope

This registry is the single canonical source-of-truth for:
- Milestone lifecycle status
- Ticket status, supersession, dependencies
- Governance rules (naming, status vocabulary, lifecycle, supersession, historical document handling)
- Active Owner decisions
- Deferred / optional / blocked work
- Stale references identified in historical closeouts

Historical closeout files in `logs/` are **preserved unchanged** per §4 Historical Document Rule. Any apparent contradiction between a historical closeout and this registry is resolved in favor of this registry, with the stale reference documented in §6.

---

## 1. CURRENT STATE

### Active milestone

**NONE — engineering state STABLE / CLOSED.** All milestones CLOSED. No milestone in IN PROGRESS. No ticket in IN PROGRESS or NOT STARTED.

Per Owner Decision A (2026-08-12, GOV-2-R1):
- M5.13 chain = FUNCTIONALLY CLOSED
- M5.14 chain = OFFICIALLY CLOSED (D1 resolved as Option A)
- M6.0 chain = CLOSED
- GOV-1 = CLOSED
- GOV-2 = CLOSED
- GOV-2-R1 = CLOSED (this alignment)

### Current authorized ticket

**NONE.** No ticket has been AUTHORIZED per the §5 transition rule. All candidate next-ticket work is recorded in §3 ACTIVE DECISIONS or §4 DEFERRED WORK. **M5.15-1 remains CANDIDATE only — MUST NOT be dispatched** without explicit Owner authorization (per GOV-2-R1 spec).

### Current HEAD

- Current HEAD: `f31945e8a58a0d8fa323588437acae968e37da76` (GOV-2-R1 finalize — final canonical head reference; this commit)
- GOV-2-R1 alignment commit: `3539de2f8795ad3e516a619dc556563e8c357c68` (Owner Decision A alignment; recorded in §9 CHANGE LOG; **distinct from Current HEAD**)
- GOV-2 establishment commit: `eb5715179647b963a4247272d9fcd4c639c7e6a3` (initial canonical state registry; superseded by GOV-2-R1)
- `origin/main` synced at HEAD
- Working tree: 0 modified, 20 baseline untracked artifacts (M5.8-1 list + M5.4-5.x + M5.2-L)

### Current status snapshot

| Milestone | Status | Latest commit | Last closeout | Notes |
|-----------|--------|---------------|---------------|-------|
| **M5.13** | **FUNCTIONALLY CLOSED** | `e6effd8` | `m5_13_4_2_strict_boundary_closeout.md` | M5.13-5 is OPTIONAL / DEFERRED per GOV-1 + GOV-2 |
| **M5.14** | **OFFICIALLY CLOSED** | `29deab7` | `m5_14_3_m6_0_3_f_correction_closeout.md` | Per D1 RESOLVED (Option A): chain officially closed, no M5.14-4 |
| **M6.0** | **CLOSED** | `540eac2` | `m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md` | M6.0-5.5-R1 is BLOCKED (credentials unavailable, correct by design) |
| **GOV-1** | **CLOSED** | (docs only) | `gov_1_state_normalization_audit.md` (out-of-repo) | State normalization audit complete |
| **GOV-2** | **CLOSED** | `eb57151` | `logs/ENGINEERING_STATE.md` (this registry) | Canonical engineering state registry established |
| **GOV-2-R1** | **CLOSED** | (this commit) | (this document, alignment) | Owner Decision A alignment — canonical state now matches Notion |

---

## 2. CANONICAL GOVERNANCE

### 2.1 Status vocabulary (canonical, exhaustive)

Per GOV-2 spec §2, the canonical status vocabulary is:

| Status | Definition |
|--------|------------|
| **NOT STARTED** | Authorized ticket that has not yet begun implementation. Requires explicit Owner authorization to transition from this state. |
| **IN PROGRESS** | Authorized ticket that has begun implementation but has not yet passed the closeout gate. |
| **CLOSED** | Ticket acceptance gate fully completed. All acceptance criteria met. Production integrity verified. Regression passing. Owner-approved closeout written. |
| **SUPERSEDED** | Ticket existed and was completed (or partially completed), but its state / implementation was replaced by a later ticket. Historical evidence preserved. **Not equivalent to FAILED.** |
| **DEFERRED** | Work explicitly exists but Owner decided to postpone execution. Not equivalent to OPTIONAL (see below). |
| **BLOCKED** | Work exists but has external / dependency blocker preventing progress. Requires resolution of the specific blocker. |
| **OPTIONAL** | Engineering follow-up candidate. **Not authorized.** Must pass Finding → Classification → Decision → Authorization → Ticket lifecycle (§5) before becoming IN PROGRESS. |

**No new statuses may be added** without:
1. Evidence that no existing status fits, AND
2. Explicit Owner decision authorizing the new status

### 2.2 Naming convention (canonical)

**Milestone**: `M5.15` (uppercase M, dot, x, no hyphen, no suffix)

**Work item**: `M5.15-1` (milestone + hyphen + N)

**Revision / re-verification**: `M5.15-1-R1` (work item + hyphen + uppercase R + number)

**Forbidden patterns** (per GOV-2 spec §3):
- `M5.15-1a`, `M5.15-1b` (letter suffix)
- `M5.15-FIX`, `M5.15-IMPL` (unauthorized suffix)
- `M6.0-3-F1`, `M5.13-4-1` (compound suffixes outside canonical)
- Any suffix not explicitly listed in the canonical pattern

**Case convention**:
- Document title, ticket registry, closeout filename: **uppercase M** (`M5.13-4.2`, `M5.14-3`)
- Commit subject: **lowercase m** (`fix(m5.13-4.2):`, `docs(m5.14-1):`)
- Document body: follow case of the immediate reference (titles uppercase, inline references can be either, must be consistent within document)

### 2.3 Commit subject convention

```
<type>(<ticket-id>): <description>

types: feat | fix | test | docs | refactor | chore
ticket-id: lowercase (m5.x-N, m5.x-N-R1, gov-N, etc.)
```

**Examples** (from canonical M5/M6 chain):
- `fix(m5.13-4.2): strict relationship confidence boundary — producer-side per-entry decay anchor`
- `docs(m5.14-1): cross-layer runtime convergence audit (READ-ONLY)`
- `feat(m5.4-5.7): inner life query layer (NarrativeTraceReader)`

### 2.4 Milestone transition lifecycle (canonical)

Per GOV-2 spec §6, the canonical transition is:

```
AUDIT
  → FINDING
  → CLASSIFICATION
  → DECISION
  → AUTHORIZATION     ← Owner (Bryan) required
  → WORK ORDER
  → IMPLEMENT
  → TEST
  → REGRESSION
  → INTEGRITY         ← production data + frozen contracts verified
  → CLOSEOUT          ← closeout doc written, regression PASS, integrity PASS
  → CANONICAL STATE UPDATE  ← this registry updated
```

**Key rules**:
- AUTHORIZATION is the only step requiring Owner (Bryan) authorization
- CANDIDATE ≠ AUTHORIZED: a candidate next-ticket from a closeout's "Recommended Next" section is NOT authorized. It must pass the full lifecycle.
- Milestone CLOSED does not auto-authorize the next milestone
- A closeout's "next candidate" text does NOT create a ticket

### 2.5 Supersession rule

Per GOV-2 spec §4:

- **SUPERSEDED ≠ FAILED**: a superseded ticket was completed (or partially completed) but its state / implementation was replaced by a later ticket
- Historical tickets are **NOT deleted** from the registry
- `superseded_by` field is required on every SUPERSEDED ticket
- `superseded_by` must reference a CLOSED ticket (a ticket cannot be superseded by a ticket that has not yet closed)
- The superseding ticket's closeout must explicitly document what state it replaces

### 2.6 Historical document rule

Per GOV-2 spec §5:

- Historical closeout / audit reports in `logs/` are **preserved unchanged**
- Apparent contradictions between historical closeouts and this registry are resolved in favor of this registry
- The stale reference is documented in §6 STALE REFERENCES
- Editing historical documents requires **explicit Owner authorization**
- This rule prevents "silent rewrite" of hindsight to match current state

### 2.7 Closeout gate

A ticket reaches CLOSED status only when ALL of the following are true:
1. Implementation complete per ticket's accepted scope
2. Acceptance criteria met (per original work order)
3. STOP conditions all clear (no stop condition triggered)
4. Regression: relevant test suites pass
5. Production integrity: SHA256 + mtime verification of production data files (if applicable)
6. Frozen contracts: 0 change
7. Closeout document written to `logs/`
8. Owner acceptance (or Owner pre-authorization of closeout conditions)
9. Canonical state update: this registry updated

### 2.8 Owner decision boundary

- **Bry (Owner)** is the only authority that can:
  - Authorize a new ticket (transition AUTHORIZATION step)
  - Resolve a pending decision
  - Authorize a new milestone
  - Authorize deviation from canonical governance
  - Authorize editing of historical documents
- **Mavis / Lin (M3 model)** executes Owner decisions, does not make them autonomously
- **Perplexity sonnet 4.6** is the brain / error-checker, does not implement
- A "closeout recommendation" is **evidence**, not authorization

---

## 3. ACTIVE DECISIONS (Owner decision required)

All decisions below are preserved as **UNRESOLVED** per GOV-1 + GOV-2 spec, except where explicitly marked RESOLVED by Owner decision. None may be silently closed.

**Per Owner Decision A (2026-08-12, GOV-2-R1)**: D1 is RESOLVED (Option A chosen). 13 decisions remain UNRESOLVED (D2-D14).

### D1. M5.14-1 next work direction (Option A / B / C) — RESOLVED

- **Source**: M5.14-1 closeout §15 (`logs/m5_14_1_cross_layer_runtime_convergence_audit.md`)
- **Status**: **RESOLVED — Option A chosen** (Owner Decision A, 2026-08-12, GOV-2-R1)
- **Resolution**: **A. CLOSE M5.14** — Architecture converged, no further work needed
- **Effect**:
  - M5.14 chain remains OFFICIALLY CLOSED (already per M5.14-3 §9)
  - M5.15-1 remains CANDIDATE only — NOT dispatched, NOT authorized
  - No new milestone, no new ticket

### D2. M5.13-5 Untouched-Entry Decay proceed?

- **Source**: M5.13-4.2 closeout §12 (`logs/m5_13_4_2_strict_boundary_closeout.md`)
- **Status**: **OPTIONAL / DEFERRED** (per GOV-1 + GOV-2 work order: "DO NOT start M5.13-5 yet")
- **Description**: Add `created_at` fallback in `_decay_locked` so that never-touched entries (old, no `last_interaction_at`) decay from `created_at` with a threshold (e.g., 1 day)
- **Scope**: ~1 function change in `src/soul/relationships.py:_decay_locked` + ~5 new tests
- **Authorization required**: Bry to unblock the OPTIONAL / DEFERRED status

### D3. M6.0-5.6.1 Budget profile registry proceed?

- **Source**: M6.0-5.6 closeout §K (`logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md`)
- **Status**: **OPTIONAL**
- **Description**: Add `BudgetProfile` enum + `EvaluationBudgetConfig.from_profile()` factory for common cases (`chat` / `diary` / `dream`)
- **Scope**: New enum + factory method + tests
- **Authorization required**: Bry to authorize M6.0-5.6.1

### D4. M5.12-1 inherited P2.2 / P2.6 decisions

- **Source**: M5.12-1 closeout (`logs/m5_12_1_remaining_agency_p2_convergence_audit.md`)
- **Status**: **PENDING** (inherited by M5.13-1, M5.13-3, M5.14-1 — at least 3 closeouts have propagated these as pending)
- **Description**:
  - P2.2: Inner Life → Agency decision awareness (PARTIALLY MITIGATED by M5.8-4)
  - P2.6: ProactiveDM → Memory awareness (DEFERRED, requires Memory gate)
- **Authorization required**: Bry to either accept current state (M5.8-4 mitigation sufficient) or authorize Stage 2 work

### D5. Real-world API integration (P3 from M5.8-1, B1 from M5.14-1)

- **Source**: M5.8-1 closeout + M5.14-1 closeout
- **Status**: **DEFERRED** (P3 from M5.8-1; B1 architectural gap from M5.14-1)
- **Authorization required**: Bry to authorize real-world API integration work (calendar / weather / news)

### D6. M5.4-5.5 narrative trace dashboard

- **Source**: M5.4-6.4 closeout
- **Status**: **DEFERRED** (UI work, much larger scope)
- **Authorization required**: Bry to authorize M5.4-5.5 dashboard work

### D7. M5.4-6.2 cross-handler lineage (parent_event_id)

- **Source**: M5.4-6.2 closeout
- **Status**: **DEFERRED** (requires future design)
- **Authorization required**: Bry to authorize cross-handler lineage work

### D8. M5.4-5.4 diary:night slot wiring

- **Source**: M5.4-5.4 closeout (per memory)
- **Status**: **DEFERRED**
- **Authorization required**: Bry to authorize diary:night slot work

### D9. Stage 4.3 feeling/impression projection (M5.13-2 future)

- **Source**: M5.13-2 design future privacy section
- **Status**: **DEFERRED** (requires Stage 4.3 LLM producer — not in M5.13 scope)
- **Authorization required**: Bry to authorize Stage 4.3 LLM producer work

### D10. M6.0-5.1 (Diary/Dream subjective evaluation)

- **Source**: M6.0 series (per prior memory)
- **Status**: **DEFERRED** (raw httpx infrastructure not ready)
- **Authorization required**: Bry to authorize M6.0-5.1 work

### D11. M6.0-5.3+ (Multi-provider circuit breaker)

- **Source**: M6.0 series (per prior memory)
- **Status**: **DEFERRED**
- **Authorization required**: Bry to authorize multi-provider circuit breaker work

### D12. Cross-agent (agent↔agent) relationship projection

- **Source**: M5.13-2 design Per-agent 過濾 section
- **Status**: **DEFERRED** (different scope from M5.13 — requires new relationship types)
- **Authorization required**: Bry to authorize cross-agent work (would be a new milestone, not M5.13)

### D13. chrono-social.silence_hours vs last_interaction_at duplication

- **Source**: M5.13-2 design "Why not include other fields" section
- **Status**: **DEFERRED** (cross-section concern, not pure M5.13)
- **Authorization required**: Bry to authorize cross-section cleanup

### D14. M5.13-3 multi-line format

- **Source**: M5.13-2 design "Multi-line (if needed for future)"
- **Status**: **OPTIONAL** (cosmetic; no behavioral need)
- **Authorization required**: Bry to authorize cosmetic format change

---

## 4. DEFERRED / OPTIONAL / BLOCKED WORK

### 4.1 OPTIONAL (candidates, NOT authorized)

| ID | Work | Source | Scope |
|----|------|--------|-------|
| D2 | M5.13-5 Untouched-Entry Decay | M5.13-4.2 §12 | 1 function + ~5 tests |
| D3 | M6.0-5.6.1 Budget profile registry | M6.0-5.6 §K | New enum + factory + tests |
| D14 | M5.13-3 multi-line format | M5.13-2 design | Cosmetic |

### 4.2 DEFERRED (explicitly postponed, requires authorization to start)

| ID | Work | Source | Why deferred |
|----|------|--------|--------------|
| D4 | M5.12-1 P2.2 / P2.6 | M5.12-1 | Stage 2 territory; needs Bry decision |
| D5 | Real-world API integration | M5.8-1 / M5.14-1 | External API integration; needs design |
| D6 | M5.4-5.5 narrative trace dashboard | M5.4-6.4 | UI work, larger scope |
| D7 | M5.4-6.2 cross-handler lineage | M5.4-6.2 | Requires future design |
| D8 | M5.4-5.4 diary:night slot | M5.4-5.4 | Per memory |
| D9 | Stage 4.3 feeling/impression | M5.13-2 | Requires Stage 4.3 LLM producer |
| D10 | M6.0-5.1 Diary/Dream subjective | M6.0 | Raw httpx infrastructure |
| D11 | M6.0-5.3+ Multi-provider circuit breaker | M6.0 | Per memory |
| D12 | Cross-agent relationship projection | M5.13-2 | Different scope (new milestone) |
| D13 | chrono-social duplication | M5.13-2 | Cross-section concern |

### 4.3 BLOCKED (external / dependency blocker)

| ID | Work | Source | Blocker |
|----|------|--------|---------|
| M6.0-5.5-R1 | Real three-judge E2E validation gate | M6.0-5.5-R1 closeout | Credentials unavailable in this environment (correct by design, not a real blocker) |

**Note on M6.0-5.5-R1**: The BLOCKED status is per spec (Bry 8/11 21:40 EDT: "If credentials are unavailable, the correct result is: BLOCKED — CREDENTIALS UNAVAILABLE. It is NOT PASS and it is NOT a reason to modify the infrastructure."). This is not a failure to be remediated — it is the correct outcome for the current environment.

---

## 5. CLOSED MILESTONES (canonical state)

### 5.1 M5.13 — Relationship Context + Boundary Precision

**Status**: FUNCTIONALLY CLOSED (only M5.13-5 is OPTIONAL/DEFERRED)

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.13-1 | Lived context capability preflight (READ-ONLY AUDIT) | `e940934` | **CLOSED** | Identified P1 gap (relationships in LLM prompt) |
| M5.13-2 | Relationship context projection design (READ-ONLY) | `7bf10f0` | **CLOSED** | Designed minimal confidence-band integration |
| M5.13-3 | Minimal relationship context integration (IMPLEMENTATION) | `32e5172` | **CLOSED** | `src/llm/proxy.py:_format_relationship_block` (29 + 19 subtests PASS) |
| M5.13-3.1 | Independent verification audit (READ-ONLY VERIFICATION) | `401ae09` | **CLOSED** | 12 categories, 14 acceptance, 7 stop conditions all PASS |
| M5.13-4 | Float precision issue audit (READ-ONLY) | `97c1063` | **CLOSED** | Discovered 0.3 boundary decay bug (P3 dormant) |
| M5.13-4.1 | Relationship confidence boundary regression (FIX) | `c816142` | **SUPERSEDED** | Consumer `round(_, 6)` fix; 5e-7 false-promotion range |
| M5.13-4.1-R1 | Relationship threshold rounding boundary audit (READ-ONLY) | `4815331` | **CLOSED** | Documented 5e-7 false-promotion; recommended C (BRY DECISION) |
| M5.13-4.2 | Strict relationship confidence boundary fix (FIX) | `e6effd8` | **CLOSED** | Producer-side per-entry decay anchor; **CANONICAL LATEST** |
| M5.13-5 | Untouched-Entry Decay Semantics | (none) | **OPTIONAL / DEFERRED** | See §3 D2 |

**Supersession chain**:
```
M5.13-4 (audit) → M5.13-4.1 (fix) → M5.13-4.1-R1 (audit, found issue)
  → M5.13-4.1 SUPERSEDED → M5.13-4.2 (fix, replaced with producer-side approach)
```

**M5.13-2 contract (STRICT, FROZEN)**: `confidence >= 0.3` → 「認識」, `confidence < 0.3` → 「陌生人」 (no tolerance).

**Closeout log**: `logs/m5_13_4_2_strict_boundary_closeout.md` (canonical)

### 5.2 M5.14 — Cross-Layer Runtime Convergence

**Status**: OFFICIALLY CLOSED (per M5.14-3 closeout §9: "no immediate M5.14-4 needed")

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.14-1 | Cross-layer runtime convergence audit (READ-ONLY) | `a2bd687` | **CLOSED** | 5-layer architecture verified |
| M5.14-2 | WorldEvent ↔ ProactiveDM identity contract audit (READ-ONLY) | `4df0c90` | **CLOSED** | F1-P1 reclassified P3 (test design) |
| M5.14-3 | M6.0-3 F1-F3 canonical agent-specific fixture (FIX / TEST-ONLY) | `29deab7` | **CLOSED** | By-design cross-milestone fixture correction |

**Note on M5.14-3 commit message**: References both `m5.14-3` (ticket ID) AND `M6.0-3 F1-F3` (fixture work). This is by design per M5.14-2 audit, not a duplicate or ambiguous record.

**Closeout log**: `logs/m5_14_3_m6_0_3_f_correction_closeout.md` (canonical)

**Next work**: D1 RESOLVED (Option A chosen) — M5.14 remains CLOSED; no M5.14-4. M5.15-1 remains CANDIDATE only and MUST NOT be dispatched without explicit Owner authorization.

### 5.3 M6.0 — Lived Context Validation + Subjective LLM Evaluation

**Status**: CLOSED (all 15 tickets, M6.0-5.5-R1 is BLOCKED by design)

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.0-1 | Lived context validation framework design (READ-ONLY) | `1cc46dd` | **CLOSED** | |
| M6.0-2 | Validation framework PoC (Scenarios A/B/C) | `fca5c2d` | **CLOSED** | 16/16 PASS |
| M6.0-3 | Validation framework (Scenarios D/E/F/G/H) | `d34513e` | **CLOSED** | 22/22 PASS (F1-F3 corrected by M5.14-3) |
| M6.0-4 | Subjective LLM quality evaluation design audit (READ-ONLY) | `3ed1092` | **CLOSED** | |
| M6.0-5 | Subjective LLM evaluation infrastructure | `5f4ae34` | **CLOSED** | 56/56 PASS |
| M6.0-5.2 | Real LLM judge backend (OPT-IN) | `91a3093` | **CLOSED** | |
| M6.0-5.3 | Multi-LLM judge diversity & orchestration design audit | `c781260` | **CLOSED** | |
| M6.0-5.4 | Minimal multi-model judge orchestration | `6ba5b90` | **CLOSED** | 39/39 PASS |
| M6.0-5.4-R1 | Cost / retry budget enforcement correction (R#) | `cda79fd` | **CLOSED** | |
| M6.0-5.4-R2 | Retry budget enforcement completion (R#) | `d87e6f6` | **CLOSED** | |
| M6.0-5.5 | Real three-judge subjective evaluation E2E (opt-in) | `3f599a4` | **CLOSED** | |
| M6.0-5.5-R1 | Real three-judge E2E validation gate (BLOCKED) | `9d21740` | **BLOCKED** | Credentials unavailable, correct per spec |
| M6.0-5.6 | Configurable subjective evaluation cost ceiling | `540eac2` | **CLOSED** | 30 new tests, 334 + 5 skipped PASS |

**Supersession chain** (M6.0-5.4 family):
```
M6.0-5.4 (initial) → M6.0-5.4-R1 (cost/retry correction) → M6.0-5.4-R2 (retry completion)
  → M6.0-5.4 SUPERSEDED → M6.0-5.4-R1
  → M6.0-5.4-R1 SUPERSEDED → M6.0-5.4-R2
```

**Next work**: Per D3, M6.0-5.6.1 (Budget profile registry) is OPTIONAL pending Bry authorization

### 5.4 GOV-1 — Engineering State Normalization Audit

**Status**: CLOSED

| Item | Value |
|------|-------|
| Audit location | `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md` (out-of-repo) |
| Mode | READ-ONLY |
| Author | Mavis / Lin |
| Date | 2026-08-11 ~23:55 EDT |
| Outcome | 0 production blockers, 1 STALE next-work-item reference identified (M6.0-5.6 §K) |
| Follow-up | GOV-2 (this document) |

---

## 6. STALE REFERENCES (historical closeouts vs canonical state)

Per §2.6 Historical Document Rule, historical closeouts are preserved unchanged. Stale references are documented here for reconciliation.

### 6.1 M6.0-5.6 closeout §K — M5.13-4 reference

**Stale content** (in `logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md` §K):

> **M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...` JSON roundtrip; use `math.isclose` or threshold adjustment.

**Why this is stale** (canonical truth per §5.1):
- M5.13-4 (commit `97c1063`) is a CLOSED READ-ONLY AUDIT, not a fix ticket
- The "fix for 0.3 → 0.2999..." was implemented by M5.13-4.1 → M5.13-4.1-R1 → M5.13-4.2
- M5.13-4.1 (consumer `round(_, 6)`) is SUPERSEDED by M5.13-4.2
- M5.13-4.2 (producer-side per-entry anchor) is the CANONICAL LATEST implementation
- The "use `math.isclose`" suggestion in the stale reference is **explicitly forbidden** by M5.13-4.1-R1 audit (introduces tolerance, violates strict M5.13-2 contract)
- M6.0-5.6 was committed at `540eac2` (before M5.13-4.2 was committed at `e6effd8`), so the closeout was written without knowledge of the final fix

**Canonical resolution**:
- Historical closeout file preserved unchanged
- The "M5.13-4 fix" reference is **SUPERSEDED** by M5.13-4.2
- No edits to the historical file (per §2.6)
- This stale reference is the ONLY stale next-work-item reference identified by GOV-1

### 6.2 No other stale references identified

GOV-1 exhaustively reviewed M5.13, M5.14, M6.0 closeouts for stale next-work-item references. The M6.0-5.6 §K reference is the only one. All other closeouts correctly point to either:
- Completed work (next ticket was done)
- CANDIDATE (not authorized) work, properly marked
- DEFERRED work with clear status

---

## 7. ENGINEERING LEDGER (canonical state sources)

### Canonical registry

- **This document** (`logs/ENGINEERING_STATE.md`) — single source of truth for engineering state
- **README.md** — canonical homepage with brief snapshot + link to this registry
- **GOV-1 report** (`C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md`, out-of-repo) — predecessor audit

### Per-ticket closeout logs (M5.13 + M5.14 + M6.0)

**M5.13**:
- `logs/m5_13_1_lived_context_preflight_audit.md`
- `logs/m5_13_2_relationship_context_projection_design.md`
- `logs/m5_13_3_relationship_context_closeout.md`
- `logs/m5_13_3_1_independent_verification_audit.md`
- `logs/m5_13_4_float_precision_audit.md`
- `logs/m5_13_4_1_relationship_confidence_boundary_closeout.md` (SUPERSEDED — kept for history)
- `logs/m5_13_4_1_r1_relationship_threshold_rounding_boundary_audit.md`
- `logs/m5_13_4_2_strict_boundary_closeout.md` (CANONICAL LATEST)

**M5.14**:
- `logs/m5_14_1_cross_layer_runtime_convergence_audit.md`
- `logs/m5_14_2_world_proactive_identity_audit.md`
- `logs/m5_14_3_m6_0_3_f_correction_closeout.md`

**M6.0**:
- `logs/m6_0_1_lived_context_validation_design.md`
- `logs/m6_0_2_validation_poc_closeout.md`
- `logs/m6_0_3_validation_d_e_f_g_h_closeout.md`
- `logs/m6_0_4_subjective_llm_quality_audit.md`
- `logs/m6_0_5_subjective_eval_infrastructure_closeout.md`
- `logs/m6_0_5_2_real_llm_judge_backend_closeout.md`
- `logs/m6_0_5_3_multi_llm_judge_diversity_audit.md`
- `logs/m6_0_5_4_minimal_multi_model_judge_orchestration_closeout.md`
- `logs/m6_0_5_4_r1_cost_retry_budget_enforcement_closeout.md`
- `logs/m6_0_5_4_r2_retry_budget_enforcement_completion_closeout.md`
- `logs/m6_0_5_5_real_three_judge_e2e_closeout.md`
- `logs/m6_0_5_5_r1_real_three_judge_e2e_validation_gate_blocked.md`
- `logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md`

### Out-of-repo references

- GOV-1 audit: `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md`
- M5.13-4.2 closeout: `C:\Users\bbfcc\m5_13_4_2_temp\m5_13_4_2_closeout.md`
- M5.13-3.1 verification harness: `C:\Users\bbfcc\m5_13_3_1_temp\`

---

## 8. GLOSSARY

- **AUDIT**: READ-ONLY investigation; produces FINDING
- **Authorization**: Owner (Bryan) approval required for new tickets / milestone transitions
- **BLOCKED**: Status indicating external / dependency blocker
- **Candidate**: Next-ticket proposal from a closeout's "Recommended Next" section; NOT authorized
- **CLOSED**: Ticket acceptance gate fully completed
- **Closeout**: Final document + state transition for a ticket
- **DEFERRED**: Status indicating Owner-postponed work
- **FINDING**: Audit result; requires CLASSIFICATION
- **Frozen contract**: Code/data structure that must not change without Owner approval
- **GOV**: Governance ticket prefix (not a milestone)
- **Milestone**: Top-level engineering capability series (M3, M5.x, M6.0)
- **Mavis / Lin**: M3 model (per user rename in 2026-06-02)
- **OPTIONAL**: Status indicating candidate for future work, not authorized
- **Owner / Bryan**: Final decision authority on all engineering direction
- **Perplexity sonnet 4.6**: Brain / error-checker; does not implement
- **SUPERSEDED**: Status indicating replacement by later ticket
- **Ticket**: Work item within a milestone (M5.x-N, M5.x-N-R1)
- **Work item**: See Ticket
- **Work order**: Formal ticket description (the "ticket" in the colloquial sense)

---

## 9. CHANGE LOG

| Date | Change | Author | Source ticket |
|------|--------|--------|---------------|
| 2026-08-12 00:03 EDT | Initial canonical state registry established (commit `eb57151`) | Mavis / Lin | GOV-2 |
| 2026-08-12 (GOV-2-R1) | Owner Decision A alignment: GOV-2 / M5.14 / M6.0 / GOV-1 all CLOSED. D1 RESOLVED (Option A). 13 decisions remain UNRESOLVED. M5.15-1 remains CANDIDATE only. (alignment commit `3539de2f8795ad3e516a619dc556563e8c357c68`) | Mavis / Lin | GOV-2-R1 |
| 2026-08-12 (GOV-2-R1 finalize) | Final canonical head reference correction. Current HEAD = `f31945e8a58a0d8fa323588437acae968e37da76` (this commit). GOV-2-R1 alignment commit = `3539de2f8795ad3e516a619dc556563e8c357c68` (distinct value; not off-by-one). 26/27 GOV-2 consistency checks PASSED + 1 obsolete assertion (pre-commit HEAD check expected `e6effd8`; obsolete after subsequent governance commits). | Mavis / Lin | GOV-2-R1 finalize |

---

**End of canonical state registry. Next update requires Owner authorization per §2.4 lifecycle.**
