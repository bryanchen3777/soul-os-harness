# Soul OS — Canonical Engineering State & Milestone Registry

**Source of truth** for Soul OS engineering governance.
**Mode**: Documentation only.
**Owner**: Bryan (Mavis / Lin executes per Owner decisions).
**Established**: GOV-2 (2026-08-12 00:03 EDT, commit `eb5715179647b963a4247272d9fcd4c639c7e6a3`).
**Aligned**: GOV-2-R1 (2026-08-12, Owner Decision A: M5.14 / M6.0 / GOV-1 / GOV-2 all CLOSED; no ticket authorized).
**M6.0-5.6.1 progressed** (2026-08-12, M6.0-5.6.1 Budget Profile Registry CLOSED; D3 RESOLVED).
**M5.13-5 progressed** (2026-08-12, M5.13-5 Untouched-Entry Decay CLOSED; M5.13 series now FULLY CLOSED).
**M5.15 series progressed** (2026-08-12, M5.15-1 / M5.15-2 / M5.15-3 / M5.15-4 / M5.15-5 / M5.15-6 all CLOSED; M5.15-4 SUPERSEDED by M5.15-5; M5.15-6-PREFLIGHT + M5.15-6 RESUME Option 1 CLOSED; F1 + F2 + F3 + F4 all RESOLVED).
**M6.1 series progressed** (2026-08-13 → 2026-08-15, M6.1-0 / M6.1-1 / M6.1-2 / M6.1-3 / M6.1-3.1 / M6.1-3.2 / M6.1-3.3 / M6.1-4 / M6.1-5 / M6.1-5.1 / M6.1-5.2 / M6.1-5.3 / M6.1-6.0 / M6.1-6.0-C / M6.1-7 / M6.1-8 / M6.1-8.1 / **M6.1-8.2** all CLOSED; **M6.1-9 final audit DONE 2026-08-15 — 24h window INVALIDATED by LLM model misconfig, NOT CLOSED, new observation window required**). Signal half (Physical/Information/Social/Temporal) operational + VERIFIED WORKING. Life half (Personal/Agency/Expression) **Agency RE-ENABLED** in production (M6.1-8.2 10-phase gradual rollout, agents=10, /health=200) but Agency/Expression output BLOCKED by LLM 404 for most of 24h window (now fixed). Personal still DEFER per M6.1-6.0.
**M6.2 series progressed** (2026-08-14, M6.2-0 + **M6.2-1** both CLOSED). M6.2-0 confirmed async text/TTS separation already correct. M6.2-1 closed the actual gap (message_id correlation for rapid sequential messages), 5 production files + 1 test, 0 frozen contract change, 0 production data mutation, 11/11 new tests + 45/45 focused regression PASS. Per Quality > Quantity: NO M6.2-2 recommended.
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

- Current HEAD: `8466bc1` (docs(doc-1.3-r1) — Canonical Documentation State Reconciliation, 3 files: README.md + docs/architecture.png + logs/ENGINEERING_STATE.md)
- arch-v2 commit: `fa46433` (M6.1-aligned v2 diagram replacement; **distinct from Current HEAD**)
- M6.1-9.1 closeout commit: `eafbf24` (Restore True Phase-10 Agency Registration; **distinct from Current HEAD**)
- M6.1-9 partial commit: `362cf28` (Lived Context Formation Audit T+50min snapshot; **distinct from Current HEAD**)
- M6.2-1 registry sync commit: `9a64f14` (Per-Message TTS Correlation registry sync; **distinct from Current HEAD**)
- M6.2-1 impl commit: `965df92` (Per-Message TTS Correlation FEATURE; **distinct from Current HEAD**)
- M6.1-2 docs commit: `9e050f6` (Lived Context Canonical Boundary & Documentation)

(M6.1-8.2 — Controlled Production Agency Re-enable, IMPLEMENTATION / Option B / Gradual — `2a42521`, **distinct from Current HEAD**)
(M6.2-0 closed via out-of-repo closeout report `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md`, no in-repo file changes for READ-ONLY audit)
- M6.1-7 closeout commit: `bdf76ad` (Production Lived Context Evidence Reassessment; **distinct from Current HEAD**)
- M6.1-6.0-C closeout commit: `49adf46` (Personal Lived Context Architecture Decision Audit; **distinct from Current HEAD**)
- M6.1-5.1 impl commit: `9f8ece8` (RSS News Source; FEATURE, IMPLEMENTATION)
- M6.1-3.1 impl commit: `ac50256` (Open-Meteo Weather Source; FEATURE, IMPLEMENTATION)
- M6.1-2 docs commit: `9e050f6` (Lived Context Canonical Boundary & Documentation)
- M5.15-6 impl commit: `c2de02c` (Real-World Calendar Source Integration)
- M5.15-5 impl commit: `0aedbef` (Two-Layer Lineage Model + InnerLifeEvent.source_world_event_novelty_id)
- M5.15-3 impl commit: `b4b981a` (WorldEventSource → Event Bus canonical integration)
- GOV-2-R1 alignment commit: `3539de2` (Owner Decision A alignment; recorded in §9 CHANGE LOG; **distinct from Current HEAD**)
- GOV-2 establishment commit: `eb57151` (initial canonical state registry; superseded by GOV-2-R1)
- `origin/main` synced at HEAD
- Working tree: 0 modified, 20 baseline untracked artifacts preserved (M5.2-L + M5.4-5.x audit docs)

### Current status snapshot

| Milestone | Status | Latest commit | Last closeout | Notes |
|-----------|--------|---------------|---------------|-------|
| **M5.13** | **FULLY CLOSED** | `9501603` | `m5_13_5_untouched_decay_closeout.md` (out-of-repo) | M5.13-5 CLOSED 2026-08-12; M5.13 series fully closed (no remaining OPTIONAL/DEFERRED tickets) |
| **M6.0** | **FULLY CLOSED** | `3d1fae4` | `m6_0_5_6_1_budget_profile_closeout.md` (out-of-repo) | M6.0-5.6.1 CLOSED 2026-08-12; M6.0 series fully closed (D3 RESOLVED) |
| **M5.14** | **OFFICIALLY CLOSED** | `29deab7` | `m5_14_3_m6_0_3_f_correction_closeout.md` | Per D1 RESOLVED (Option A): chain officially closed, no M5.14-4 |
| **M5.15** | **F1 + F2 + F3 + F4 RESOLVED (M5.15-3 + M5.15-5 + M5.15-6 CLOSED)** | `c2de02c` | `m5_15_6_closeout.md` (out-of-repo) | M5.15-1 / M5.15-2 / M5.15-3 / M5.15-5 / M5.15-6-PREFLIGHT / M5.15-6 all CLOSED. M5.15-4 SUPERSEDED by M5.15-5. F5-F7 P3 no action. |
| **M6.0** | **CLOSED** | `540eac2` | `m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md` | M6.0-5.5-R1 is BLOCKED (credentials unavailable, correct by design) |
| **M6.1** | **Lived Context Awareness — Signal half LIVE (Physical via Open-Meteo / Information via RSS / Social via TG+Calendar), Agency RE-ENABLED in production (M6.1-9.1 True Phase-10 = 10/10 agents restored), M6.1-9 CLOSED 8/16 22:15 — two independent issues (LLM 404 + cooldown conflict) both fixed + production-verified (night slot 10/10 diary)** | `c6ccc36` (current HEAD) | `m6_1_9_1_closeout.md` + `m6_1_9_closeout.md` (out-of-repo) | M6.1-0 / M6.1-1 / M6.1-2 / M6.1-3 / M6.1-3.1 / M6.1-3.2 / M6.1-3.3 / M6.1-4 / M6.1-5 / M6.1-5.1 / M6.1-5.2 / M6.1-5.3 / M6.1-6.0 / M6.1-6.0-C / M6.1-7 / M6.1-8 / M6.1-8.1 / M6.1-8.2 / M6.1-9 partial / M6.1-9.1 / **M6.1-9 T+4h33m partial** all CLOSED. **M6.1-9 partial T+4h33m findings** (out-of-repo `m6_1_9_closeout.md`): **2 NEW InnerLifeEvents from Agency triggers** (diary:night for agent_yua at 8/14 22:00:11, dream:dream for agent_miku at 8/14 22:05:11, target yua). Inner life trace grew 553B (1 entry) → 1431B (3 entries) = +878B / +2 events. **M5.2 regression is RESOLVED** (was broken 6+ days from 8/8 21:13 to 8/14 22:00). 2 diary files UPDATED (agent_yua + agent_miku, 8KB each, mtime 22:00/22:05). 0 source code change, 0 frozen contract change, 0 other production data mutation. **Concerns**: (1) 9 of 10 agents' diary/dream LLM calls INTERRUPTED by 22:38 server restart (cause unknown, not via server_ops.ps1, TELEGRAM_BOT_YUA not set in restart shell); (2) Q1/Q2/Q4/Q6 still PENDING full 24h evidence; (3) diary file content LOCKED by running server. **Cron `m6_1_9_t24h_v2`** (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) will catch T+24h (8/15 19:27 EDT) and full 24h follow-up. |
| **GOV-1** | **CLOSED** | (docs only) | `gov_1_state_normalization_audit.md` (out-of-repo) | State normalization audit complete |
| **GOV-2** | **CLOSED** | `eb57151` | `logs/ENGINEERING_STATE.md` (this registry) | Canonical engineering state registry established |
| **GOV-2-R1** | **CLOSED** | (this commit) | (this document, alignment) | Owner Decision A alignment — canonical state now matches Notion |
| **M6.2** | **Text / TTS Response Path Separation — M6.2-0 + M6.2-1 both CLOSED (implementation complete, production deployment pending separate verification)** | `965df92` (impl) / `9a64f14` (registry) | `m6_2_1_text_tts_correlation_closeout.md` (out-of-repo) | M6.2-0 + M6.2-1 CLOSED at the **implementation level**. **Key finding (M6.2-0)**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(_synthesize_async)` in FishTTSHandler. TTS latency 0.5-6.4s (mean 3.3s). Transport: WebSocket + Telegram already separate text/audio via 2 distinct message types (`agent_speak` + `agent_audio_ready`). **Gap closed (M6.2-1)**: added `message_id` (SoulEvent.event_id UUID) end-to-end (AGENT_SPEAK → TTSService → AGENT_AUDIO_READY payload + ChannelRouter per-message correlation + WebSocket payload + static/index.html data-message-id). 5 production files + 1 test file, +131/-26 lines. 0 frozen contract change. 0 production data mutation. 11/11 new tests + 45/45 focused regression PASS. **Deployment status**: code is in main (`965df92` impl + `9a64f14` registry); production server still running pre-M6.2-1 build (started 8/14 20:21:18 EDT, M6.1-9.1 fix); production deployment of M6.2-1 is a separate concern from implementation closeout, requires explicit server restart decision (NOT in scope of M6.2-1). **Per Quality > Quantity**: NO M6.2-2 recommended (0 P0/P1/P2 remaining; 6 P3 explicitly out of scope per M6.2 work order). M6.1-9 PENDING 24h RUN-AND-COLLECT (cron `m6_1_9_followup` fires 2026-08-15 ~20:00 EDT). |

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
- **Status**: **RESOLVED** (M5.13-5 CLOSED 2026-08-12, commit `9501603`)
- **Resolution**: M5.13-5 implemented per Bry authorization (POST-M5.15 decision queue audit, D2 = first STILL VALID + WORTH DOING)
- **Description**: Add `created_at` fallback in `_decay_locked` so that never-touched entries (old, no `last_interaction_at`) decay from `created_at` with a 1.0-day grace threshold
- **Effect**:
  - M5.13 series now FULLY CLOSED (no remaining OPTIONAL/DEFERRED tickets)
  - Untouched entries decay from `created_at` after 1.0 day grace (preserves M5.13-2 strict 0.3 contract)
  - Legacy/malformed `created_at` entries: skip, no crash, deterministic
  - Touched entries: continue using `last_interaction_at` (M5.13-4.2 anchor unchanged)

### D3. M6.0-5.6.1 Budget profile registry proceed?

- **Source**: M6.0-5.6 closeout §K (`logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md`)
- **Status**: **RESOLVED** (M6.0-5.6.1 CLOSED 2026-08-12, commit `3d1fae4`)
- **Resolution**: M6.0-5.6.1 implemented per Bry authorization (POST-M5.15 decision queue audit, D3 = second STILL VALID + WORTH DOING)
- **Description**: Add `BudgetProfile` enum + `EvaluationBudgetConfig.from_profile()` factory for common cases (`chat` / `diary` / `dream`)
- **Effect**:
  - M6.0 series now FULLY CLOSED (no remaining OPTIONAL/DEFERRED tickets)
  - 3 named profiles: CHAT (3/2/5000/0.05, matches default), DIARY (2/1/3000/0.03), DREAM (1/1/2000/0.02)
  - Defaults unchanged (no silent override)
  - Existing callers continue to work (`EvaluationBudgetConfig()` still works)

### D3.5. M6.1-8.1 Minimal Agency Re-enable (P0 — M5.2 regression)

- **Source**: M6.1-8 closeout (`logs/m6_1_8_agency_reenable_investigation.md`)
- **Status**: **M6.1-8.1 CLOSED (fix PROVEN in isolated). M6.1-8.2 CLOSED (Option B / Gradual production rollout DONE). M6.1-9 PENDING 24h RUN-AND-COLLECT then audit**
- **Description**: Add 3 lines to `scripts/run_server.py` after `diary_callbacks_real[aid] = cb_real`:

  ```python
  for aid in agent_ids:
      scheduler.register(aid)
  ```

  M5.2 migration (commit `481ea41` 2026-08-08 21:11) removed `scheduler.register(aid, cb)` callsite (M5.2-I Phase 7) but M5.2-I Phase 8 changed iteration source to `_all_agents` without adding replacement. `_all_agents` permanently empty. 5 trigger paths silent-skip. 0 diary/dream/event/proactive_dm writes for 6+ days.
- **M6.1-8.1 ISOLATED VALIDATION** (CLOSED 2026-08-14 22:50 EDT, commit `d0c33da`):
  - Test file: `tests/test_m6_1_8_1_agency_reenable_isolated.py` (21 tests, 905 lines)
  - Results: 20 PASS / 1 XFAIL / 0 FAIL in 0.47s
  - 71/71 M5.2 series tests still PASS (no regression)
  - 0 production data mutation
  - 0 Telegram, 0 real LLM
  - 0 frozen contract change
  - 0 source code modification
  - F.2 = `run_server.py` missing `scheduler.register(...)` callsite (XFAIL pending M6.1-8.2 production fix)
  - All 5 trigger paths publish AGENCY_TRIGGER after fix
  - All 4 handlers correctly receive filtered trigger_type
- **M6.1-8.2 PRODUCTION ROLLOUT** (CLOSED 2026-08-14 19:27 EDT, commit `2a42521`):
  - **Bry 拍板 2026-08-14 19:12 EDT — Option B / Gradual**
  - 10-phase rollout: 1 → 2 → 3 → ... → 10 agents in 19:18:12 → 19:27:36 EDT (~10 min)
  - All 10 phases confirmed via `[Scheduler] 啟動 ✓ ... agents=N` log line
  - 0 ERROR/Traceback/CRITICAL during rollout
  - /health=200 stable across all 10 phases
  - 0 frozen contract change (15 contracts preserved)
  - 0 production data mutation
  - 0 handler/Agency/scheduler architecture change
  - `SOULOS_AGENCY_GRADUAL_AGENTS` env var: per-phase agent list (gitignored `.env`)
  - Final state: env var unset → M6.1-8.1 default (all 10 agents registered)
- **M6.1-9 LIVED CONTEXT FORMATION AUDIT** (CLOSED 2026-08-16 22:15 EDT — two independent issues both fixed + production-verified):
  - Trigger: 24h RUN-AND-COLLECT post-M6.1-8.2 (window 8/14 20:00 → 8/15 20:00 EDT)
  - Mode: READ-ONLY (0 source change, 0 production mutation, 0 commit/push)
  - Finding: Agency/Expression half (diary/dream) BLOCKED by LLM 404 (minimax-M2.7 uppercase) for most of window. InnerLifeEvent = 3 events/24h (target 30-50/day). Only 2/10 agents (yua + miku) produced diary/dream.
  - Signal half (Physical/Information): VERIFIED WORKING (news + weather emitting, perception evaluation functional, 604 accepted events).
  - Agency registration: CORRECT (10/10 agents, scheduler agents=10).
  - Memory + relationship integrity: OK (no corruption).
  - **Issue 1 — LLM 404**: Ollama Cloud model name case-sensitive; minimax-M2.7 → 404; fixed to minimax-m2.7, server restarted 8/15 18:54:32.
  - **Issue 2 — cooldown conflict (M5.2-H Phase 3 regression)**: handlers instantiated with `state=None` → single shared `AgencyState` → global `last_action_at` → only first agent produced diary when 10 triggered simultaneously. Fixed via per-agent `AgencyState` dict in 4 handlers (DiaryHandler `c526320` / DreamHandler `6d5112d` / AgencyTriggerHandler + EventHandler `af91271`). 75 tests PASS.
  - **Production verification**: night slot 8/16 22:00 EDT → 10/10 agents produced diary (trace.jsonl 10× diary:night, 4-6s apart, no cooldown decision=NO). server health=200.
  - Decision: M6.1-9 CLOSED.
  - Closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_final_audit.md` (out-of-repo)

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
| D14 | M5.13-3 multi-line format | M5.13-2 design | Cosmetic |

**Note**: M5.15-4 (cross-handler lineage), M5.15-5 (identity bridge), M5.15-6 (real-world source),
M5.13-5 (untouched-entry decay), and M6.0-5.6.1 (budget profile registry) have all been RESOLVED.
M5.15-4 is SUPERSEDED by M5.15-5. M5.15-6 resolved F2 P2. M5.13-5 resolved M5.13's last
remaining OPTIONAL ticket. M6.0-5.6.1 resolved M6.0's last remaining OPTIONAL ticket.

### 4.2 DEFERRED (explicitly postponed, requires authorization to start)

| ID | Work | Source | Why deferred |
|----|------|--------|--------------|
| D4 | M5.12-1 P2.2 / P2.6 | M5.12-1 | Stage 2 territory; needs Bry decision |
| D6 | M5.4-5.5 narrative trace dashboard | M5.4-6.4 | UI work, larger scope |
| D7 | M5.4-6.2 cross-handler lineage | M5.4-6.2 | Requires future design |
| D8 | M5.4-5.4 diary:night slot | M5.4-5.4 | Per memory |
| D9 | Stage 4.3 feeling/impression | M5.13-2 | Requires Stage 4.3 LLM producer |
| D10 | M6.0-5.1 Diary/Dream subjective | M6.0 | Raw httpx infrastructure |
| D11 | M6.0-5.3+ Multi-provider circuit breaker | M6.0 | Per memory |
| D12 | Cross-agent relationship projection | M5.13-2 | Different scope (new milestone) |
| D13 | chrono-social duplication | M5.13-2 | Cross-section concern |
| M6.1-9 | Lived Context Formation Audit | M6.1-7 / M6.1-8 | Per Quality > Quantity: only after M6.1-8.1 (Agency re-enable) + 24h+ RUN-AND-COLLECT, then verify multi-signal world_context |

**Note on D5 (Real-world API integration)**: RESOLVED by M5.15-6 (Calendar via iCal/ICS public feed).
D5 retired — first real-world source is the calendar, the integration pattern (M5.15-3 canonical
bus path + M5.15-5 Two-Layer Lineage Model + M5.15-6 SHA256 identity bridge) generalizes to
weather/news/social when those candidates are authorized.

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
| M5.13-4.2 | Strict relationship confidence boundary fix (FIX) | `e6effd8` | **CLOSED** | Producer-side per-entry decay anchor |
| M5.13-5 | Untouched-Entry Decay (FIX) | `9501603` | **CLOSED** | created_at fallback with 1.0-day grace; preserves M5.13-2 strict 0.3 contract; 14/14 new tests + 56/56 M5.13 suite + 105/105 adjacent regression; 0 frozen contract change; 0 production mutation. **CANONICAL LATEST.** |

**Supersession chain**:
```
M5.13-4 (audit) → M5.13-4.1 (fix) → M5.13-4.1-R1 (audit, found issue)
  → M5.13-4.1 SUPERSEDED → M5.13-4.2 (fix, replaced with producer-side approach)
```

**M5.13-2 contract (STRICT, FROZEN)**: `confidence >= 0.3` → 「認識」, `confidence < 0.3` → 「陌生人」 (no tolerance).

**Closeout logs** (latest canonical for M5.13 series):
- `logs/m5_13_4_2_strict_boundary_closeout.md` (M5.13-4.2 — producer-side per-entry anchor)
- `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md` (M5.13-5 — untouched-entry decay, out-of-repo)

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
| M6.0-5.6.1 | Budget Profile Registry | `3d1fae4` | **CLOSED** | `BudgetProfile` enum (CHAT/DIARY/DREAM) + `EvaluationBudgetConfig.from_profile()` factory. 29/29 new tests + 12/12 M6.0-5.6 + M6.0-5.6.1 manual regression. 0 frozen contract change (defaults 3/2/5000/0.05 preserved; CHAT profile == default; existing `EvaluationBudgetConfig()` still works). 0 production mutation. **CANONICAL LATEST.** |

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

### 5.5 M5.15 — WorldEventSource → Event Bus Canonical Integration

**Status**: F1 + F2 + F3 + F4 RESOLVED. F5-F7 P3 no action. M5.15 series all CLOSED.

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.15-1 | WorldEvent → Event Bus boundary audit (READ-ONLY) | (docs only) | **CLOSED** | 7 findings classified; F1 P1, F2-F4 P2, F5-F7 P3. Out-of-repo report. |
| M5.15-2 | WorldEvent → Event Bus architecture decision analysis (READ-ONLY) | (docs only) | **CLOSED** | Owner authorized Option A. Out-of-repo report. |
| M5.15-3 | WorldEventSource → Event Bus canonical integration (IMPLEMENTATION) | `b4b981a` | **CLOSED** | 31/31 new tests + 159/159 regression. 0 frozen contract. 0 production mutation. |
| M5.15-4 | Cross-Handler Lineage Propagation (IMPLEMENTATION) | (none) | **STOPPED + SUPERSEDED** | Per M5.15-5 decision: parent_event_id contract cannot reference non-InnerLifeEvent. **SUPERSEDED by M5.15-5.** |
| M5.15-5 | WorldEvent ↔ InnerLifeEvent Identity Bridge (IMPLEMENTATION) | `0aedbef` | **CLOSED** | 52/52 new tests + 285/285 regression. 1 additive frozen-contract amendment (InnerLifeEvent +1 Optional field `source_world_event_novelty_id`). 0 production mutation. |
| M5.15-6-PREFLIGHT | Real-world source architecture decision (READ-ONLY) | (docs only) | **CLOSED** | Owner authorized Calendar via iCal/ICS public feed (only qualifying candidate). 12 owner decisions Q1-Q12. Out-of-repo. |
| M5.15-6 | Real-World Calendar Source Integration (IMPLEMENTATION) | `c2de02c` | **CLOSED** | 55/55 new tests + 211/211 regression. 0 frozen contract change. 0 production mutation. **CANONICAL LATEST.** |

**Architecture decision** (M5.15-2 + M5.15-5 + M5.15-6 Owner Options A):
- Event Bus = canonical integration transport for WorldEvent downstream consumers
- New WorldEventSource MUST `bus.publish(SoulEvent(WORLD_EVENT, target="broadcast", priority=NORMAL, payload=world_event.to_payload()))`
- `inject()` / `process_world_event_direct()` RETAIN as deprecated backward-compat (per M5.15-2 spec §4)
- Single processing path per subscriber (no double perception, no recursive publish)
- novelty_id dedup preserved (no duplicate InnerLifeEvent)
- For sources with M3.1-incompatible identity strings (e.g., iCal UIDs with `@`/`.`/`-`):
  use `SHA256(identity)[:32]` as `novelty_id`, preserve original in `data["<source>_id"]`
  (M5.15-6 RESUME Option 1, 0 frozen contract change)

**Two-Layer Lineage Model** (M5.15-5):
- **Layer 1 (External Causality)**: `WorldEvent.novelty_id → InnerLifeEvent.source_world_event_novelty_id` (free string, no 32-hex, no existence check)
- **Layer 2 (Internal Lineage)**: `InnerLifeEvent.parent_event_id → lineage_depth / lineage_path` (M5.4-5.1 frozen preserved)
- 5 existing producers (Diary/Dream/Event/ProactiveDM/Conversation) keep source_world_event_novelty_id=None (backward compat 100%)

**M5.15-6 Identity Model** (RESUME Option 1, Bry authorization 2026-08-12 19:37):
- `WorldEvent.novelty_id = SHA256(VEVENT.UID)[:32]` (32-char lowercase hex, M3.1-compatible)
- `WorldEvent.data["ical_uid"] = VEVENT.UID` (exact original, preserved for traceability)
- `WorldEvent.data["ical_sequence"] = VEVENT.SEQUENCE` (if present, observability only — NOT in identity)
- SEQUENCE excluded from hash (Q6: same UID + different SEQUENCE → same hash → adapter dedupes)
- 128-bit collision space, deterministic, no timestamp/randomness mixed in

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M5.15-3: `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`
- M5.15-4 STOP: `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md` (SUPERSEDED, historical)
- M5.15-5: `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md` (canonical)
- M5.15-6-PREFLIGHT: `C:\Users\bbfcc\gov_1_temp\m5_15_6_preflight_architecture_decision.md` (canonical)
- M5.15-6 RESUME STOP: `C:\Users\bbfcc\gov_1_temp\m5_15_6_stop_report.md` (historical, M3.1 conflict)
- M5.15-6: `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md` (canonical, RESUME Option 1)

**M5.15-1 findings status** (post M5.15-6):
- **F1 P1** (source path doesn't publish to bus): **RESOLVED by M5.15-3** ✓
- **F2 P2** (real-world source integration): **RESOLVED by M5.15-6** ✓ (Calendar via iCal/ICS)
- **F3 P2** (cross-handler lineage): **RESOLVED by M5.15-5** ✓ (via Layer 1 + Layer 2)
- **F4 P2** (identity bridge): **RESOLVED by M5.15-5** ✓ (same new field, preserved by M5.15-6)
- **F5-F7 P3** (intentional, test-only): no action

**Next work**: None. M5.15 chain is complete (F1-F4 all RESOLVED, F5-F7 no action). Future
real-world source candidates (weather / news / social) can follow the M5.15-6 integration
pattern when Bry authorizes them.

---

### 5.6 M6.1 — Lived Context Awareness (Canonical Boundary & Documentation)

**Status**: Signal half LIVE (Physical via M6.1-3.1 Weather + Information via M6.1-5.1 News + Social via M5.15-6 Calendar + Temporal). Life half BLOCKED at Agency layer (M5.2 regression identified in M6.1-8). Frozen contracts 0 change. 0 production mutation. M6.1-8.1 PENDING Bry decision (3-line re-enable).

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.1-0 | Lived Context Awareness architecture audit (READ-ONLY) | (docs only) | **CLOSED** | 18 modules / 11 subsystems inventoried. Bry's 5 questions answered. Out-of-repo report. |
| M6.1-1 | Lived Context taxonomy & minimal architecture (READ-ONLY) | (docs only) | **CLOSED** | READY FOR M6.1-2 verdict. Canonical 5 contexts + 4-layer boundary + minimum provenance. 0 frozen contract change. Out-of-repo report. |
| M6.1-2 | Lived Context canonical boundary & documentation (IMPLEMENTATION) | `9e050f6` | **CLOSED** | Documentation-first implementation. README.md §7.1 added + this section + §9 change log. 0 source-code change, 0 frozen contract change, 0 production mutation, 0 new runtime abstraction. |
| M6.1-3 | Lived Context Evidence & Calendar Run-and-Collect Audit (READ-ONLY) | (docs only) | **CLOSED** | 1 Calendar event in perception_trace. 5 P2 gaps. Per Quality > Quantity: NO new milestone justified. |
| M6.1-3.1 | Open-Meteo Weather Source (IMPLEMENTATION) | `ac50256` | **CLOSED** | Physical Lived Context signal. `source_id="weather"`, `novelty_id=SHA256(...)[:32]`, types=`rain_started` / `weather_temp_change`, 1800s polling, 0 new dependencies. 406/406 broad regression PASS. 0 frozen contract change. |
| M6.1-3.2 | Live Weather Activation & Lived Context E2E Validation (READ-ONLY) | (docs only) | **CLOSED** | SOULOS_WEATHER_LOCATION=25.03,121.57 in .env. PHYSICAL LIVED CONTEXT OPERATIONAL. |
| M6.1-3.3 | Organic Weather Context Evaluation (READ-ONLY) | (docs only) | **CLOSED** | LLM E2E test (isolated tmp_path + real MINIMAX API) confirms physical context actually informs Soul interpretation. 4/4 tests PASS. |
| M6.1-4 | Personal Lived Context Capability Audit (READ-ONLY) | (docs only) | **CLOSED** | 0/5 FULLY ANSWERABLE. Q1 Q2 PARTIALLY, Q3 Q4 Q5 NOT. 8 signals inventoried. No first-person Personal data source. |
| M6.1-5 | Information Lived Context Capability Audit (READ-ONLY) | (docs only) | **CLOSED** | 0/5 ANSWERABLE. LLM training data cutoff (M2.7). No real News source. No Web/Search tool. |
| M6.1-5.1 | RSS News Source (IMPLEMENTATION) | `9f8ece8` | **CLOSED** | Information Lived Context signal. `source_id="news"`, `type="news_event"` (NOT in WORLD_QUALIFYING_TYPES), `novelty_id=SHA256(provider.url.published_at)[:32]`, 1800s polling, 2h lookback, 10 articles/poll cap, 30s timeout, 10000-entry FIFO dedup. Stdlib only (urllib + xml.etree.ElementTree + email.utils.parsedate_to_datetime). 92/92 tests + 469/469 regression. 0 frozen contract change. 8 working public feeds (Reuters/AP UNAVAILABLE, documented). |
| M6.1-5.2 | Live News Activation & E2E Validation (READ-ONLY) | (docs only) | **CLOSED** | SOULOS_NEWS_FEEDS=bbc_world|...,npr_top|... in .env. 4/4 LLM E2E tests PASS. INFORMATION LIVED CONTEXT: OPERATIONAL (with caveat: isolated E2E bypassed M3 accept gate). |
| M6.1-5.3 | News Lookback & Context Density Audit (READ-ONLY) | (docs only) | **CLOSED** | Critical finding: News default score 0.345 < 0.35 accept threshold → REJECTED. **KEEP 2h lookback**. M3 accept gate is binding constraint, not lookback. 15 polls over 7.5h, 19 events emitted (66.7% emit, 1.27/poll mean). |
| M6.1-6.0 | Personal Lived Context Architecture Decision (READ-ONLY) | (docs only) | **CLOSED** | 8 signals inventoried. 0/5 ANSWERABLE, 2 PARTIALLY, 3 NOT. Verdict: **DEFER (D)**. Options compared: A Manual / B Inference (= surveillance-by-proxy, violates M3 design rule) / C Dedicated (reduces to A under work order constraints) / D Defer. |
| M6.1-6.0-C | Personal Audit Closeout (DOCS) | `49adf46` | **CLOSED** | Personal = DEFER (D) verified. |
| M6.1-7 | Production Lived Context Evidence Reassessment (READ-ONLY) | `bdf76ad` | **CLOSED** | Verdict: **LIVED CONTEXT NOT YET FORMED**. World → Perception OPERATIONAL (3 sources, 2385 trace events). Perception → Lived Context SINGLE-SOURCE only (world_context = Weather only, 602/602 injects). Lived Context → Soul Interpretation INFLUENCING (1081 LLM responses had world_context). Soul Interpretation → Agency **BROKEN** (Scheduler `agents=0` since 8/8). Agency → Expression INACTIVE. M6.1 series 50% complete. |
| M6.1-8 | Agency Re-enable Investigation (READ-ONLY) | `f699d93` | **CLOSED** | **Root cause identified**: M5.2 migration (commit `481ea41`, 2026-08-08 21:11) removed `scheduler.register(aid, cb)` callsite (M5.2-I Phase 7) without replacement; M5.2-I Phase 8 changed iteration source to `_all_agents`; `_all_agents` permanently empty. 5 trigger paths (morning/night/dream/event/proactive_dm) silent-skip. 0 diary/dream/event/proactive_dm writes for 6+ days. **Documented regression** (M5.2-I I-9 sweep §2.2 marked "production 完全沒註冊" but treated as "API COMPAT"). **Fix proposed**: 3 lines in `run_server.py` (`for aid in agent_ids: scheduler.register(aid)`). 4 safety options for Bry (A direct / B gradual / C shadow / D test-isolated). |
| M6.1-8.1 | Agency Re-enable Isolated Validation (FIX / ISOLATED TEST ONLY) | `d0c33da` | **CLOSED** | **3-line fix PROVEN in isolated test env**: 21 tests (A baseline regression, B fix validation, C AGENCY_TRIGGER publication × 5 paths, D handler reception × 4 handlers, E safety verification, F regression test × 2, G diagnostic). **20 PASS / 1 XFAIL / 0 FAIL in 0.47s**. F.2 = `run_server.py` missing `scheduler.register(...)` callsite (XFAIL pending M6.1-8.2 production fix). 71/71 M5.2 series tests still PASS. 0 production data mutation. 0 Telegram. 0 real LLM. 0 frozen contract change. 0 source code modification. **Bry rollout decision still required** (A/B/C/D). |
| M6.1-8.2 | Controlled Production Agency Re-enable (IMPLEMENTATION / Option B / Gradual) | `2a42521` | **CLOSED** | **Bry 拍板 2026-08-14 19:12 EDT — Option B / Gradual**. 10-phase rollout (1→2→3→...→10 agents) in 19:18-19:27 EDT (~10 minutes). All 10 phases confirmed via `[Scheduler] 啟動 ✓ ... agents=N` log line. 0 ERROR/Traceback/CRITICAL during rollout. /health=200 stable. Final state: agents=10 (M5.2 regression fully recovered). `scripts/run_server.py` + `.env.example` modified (commit `2a42521`, +59 insertions). Env var `SOULOS_AGENCY_GRADUAL_AGENTS` controls per-phase agent list. 0 frozen contract change, 0 production data mutation, 0 handler/Agency/scheduler architecture change. 24h RUN-AND-COLLECT deferred to M6.1-9. |
| M6.1-9 | Lived Context Formation Audit (READ-ONLY) | `c526320` + `6d5112d` + `af91271` | **CLOSED (8/16 22:15 — two issues fixed + night slot 10/10 verified)** | T+50min partial audit (2026-08-14 20:06 EDT, 50 min post-M6.1-8.2 deploy). Out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` (19.7KB). Layers 1-2 (Signal/Perception) OPERATIONAL (9 new weather events since deploy, weather source polling 1800s + 0 dedup at perception phase). Layers 3-6 (Lived Context/Soul/Agency/Expression) WIRED but UNVERIFIED — 5/5 trigger paths active (proactive_dm/event/dream/diary morning/diary night), but 0 triggers fired yet (next window: 22:00 EDT night diary, ~2h from audit time). Q1-Q6 reassessed: Q1-Q3/Q5/Q6 ⏳ PENDING 24h evidence; Q4 ⏳ PENDING 24h evidence (last 1081 LLM responses from pre-M6.1-8.2 era, 0 since deploy). **P2 anomaly discovered (6.4)**: M6.1-8.2 closeout claimed 10/10 agents but actual state is 9/10 — `agent_aoi` missing from scheduler. `SOULOS_AGENCY_GRADUAL_AGENTS` env var still set with 9 specific names (Phase 9 set), Phase 10 unset transition incomplete. **M6.1-9.1 dispatched** to fix env var. 24h follow-up cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`) fires 2026-08-15 ~20:00 EDT for full 24h evidence collection. |
| M6.1-9.1 | Restore True Phase-10 Agency Registration (FIX / PRODUCTION CONFIGURATION) | (registry sync pending) | **CLOSED** | **Root cause**: stale `SOULOS_AGENCY_GRADUAL_AGENTS` env var from M6.1-8.2 Phase 10 launch shell persisted into restart, leaving scheduler in `[M6.1-8.2 Gradual] registered 9/10 agents` mode. `agent_aoi` (Phase 12 addition, in `configs/default.yaml` line 80-83 with `enabled: true` and `AgentAoi` in `src/agent/registry.py:8,49`) was missing. M6.1-8.2 closeout overcounted. **Fix**: configuration-only restart via `scripts/server_ops.ps1 stop` + `start` from clean PowerShell shell (no `SOULOS_AGENCY_GRADUAL_AGENTS` env var). Result: `[M6.1-8.2 Full] registered all 10 agents (M6.1-8.1 default)`, `agent_aoi` now registered, 5/5 trigger paths wired, /health=200, 0 ERROR/Traceback. **0 source code change**, **0 frozen contract change**, **0 production data mutation** (memory.db / perception_trace / shadow_log / inner_life / 12 relationships.json all byte-for-byte unchanged). 21/21 M6.1-8.1 regression PASS (F.2 now PASS, was XFAIL pre-fix). Pre-restart PIDs 10592+17640 (uv python, started 8/14 19:27:35 EDT). Post-restart PID 20752 (hermes-agent venv python, started 8/14 20:21:18 EDT). 0 duplicate registrations. M6.1-9 24h follow-up cron `m6_1_9_followup` continues unchanged, fires 2026-08-15 ~20:00 EDT to verify Lived Context formation with now-correct 10-agent registration. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (14.9KB). |

**Canonical taxonomy** (M6.1-1, updated M6.1-3.1 / M6.1-5.1):

| Context | What it covers | Current sources | Current status |
|---------|----------------|------------------|-----------------|
| **Physical** | Bry's body / environment (weather, location, sunlight) | Open-Meteo Weather (M6.1-3.1) | ✓ LIVE |
| **Information** | News / web / search results / external data | RSS News (M6.1-5.1) | ✓ LIVE (with M3 accept gate caveat, 0 emits to world_context) |
| **Social** | Telegram messages, calendar events, cross-agent interactions | Telegram + Calendar (M5.15-6) | ✓ LIVE |
| **Personal** | Bry's habits (meal/sleep/activity), preferences, identity | NONE for Bry-as-person | **DEFERRED** (M6.1-6.0) |
| **Temporal** (cross-cutting) | When; touches ALL other contexts | System clock + Bry's last_msg_ts | ✓ LIVE (Chrono-Social Engine) |

**Canonical 4-layer boundary** (M6.1-1):

```
[Layer 1: Signal]            raw input from source
                             Telegram / Calendar iCal / System clock /
                             (future: Weather API / News API / Web Search)
                                       ↓
[Layer 2: Perception]        validation + scoring + dedup + fact extraction
                             WorldPerceptionMiddleware (M3) +
                             MemoryMiddleware (M5.10) +
                             Chrono-Social Engine (Phase 3.5) +
                             MultiAgentRelationshipsManager (M5.13)
                                       ↓
[Layer 3: Lived Context]     aggregated per-context blocks, formatted for LLM
                             (de-facto implementation: src/llm/proxy.py:_build_messages_*)
                             block order: identity → memory → mood →
                             relationship → inner_life → world → temporal
                                       ↓
[Layer 4: Interpretation]    LLM call + response
                             LLMProxy (M6.0-5.6) + AGENT_SPEAK emit
```

**Boundary invariant** (M6.1-1, canonical):
> External integration / tool output / raw WorldEvent ≠ Lived Context.

Calendar / Telegram / Weather / Web / Search / News / Messaging = **signal producers** (Layer 1).
Lived Context = aggregated personal-context state from Perception output (Layer 3).

**Existing aggregation** (M6.1-2 decision): `src/llm/proxy.py:_build_messages_group()` and
`_build_messages_private()` is the **de-facto Lived Context aggregator**. Block order:
identity → memory → mood → relationship → inner_life → world → temporal.
**No new `LivedContextAggregator` runtime wrapper created** — documentation-only labeling.

**Minimum provenance** (M6.1-1, all already in place):
- `source` (per signal)
- `observed_at` / timestamp (per signal)
- `freshness` (implicit via `last_msg_ts`, chrono-social `deviation_interpretation`)
- `confidence` (per relationship, M5.13-2)
- `signal_vs_derived` (implicit via LLM context block separation)

No new scoring dimensions added. No new metadata fields required.

**Frozen-contract impact** (per M6.1-1 audit): **0 changes**. 15 contracts preserved
(M3 WorldEvent, M3.1 ABC, M3.1 Bus, M5.4-5.1 InnerLifeEvent, M5.4-5.1 parent_event_id,
M5.4-5.1 lineage, M5.4-5.5 SoulEvent.inner_life_event_id, M5.9-2 WORLD_QUALIFYING_TYPES,
M5.9-3 WorldInnerLifeAdapter, M5.10 Memory, M5.13-2 strict 0.3, M5.15-3 canonical bus path,
M5.15-5 source_world_event_novelty_id, M5.15-6 identity model, VALID_SOURCES).

**Missing capabilities** (DEFERRED, requires Owner authorization to start):
- ~~Real Weather source~~ — **RESOLVED by M6.1-3.1** ✓
- ~~Real News source~~ — **RESOLVED by M6.1-5.1** ✓ (with M3 accept gate caveat per M6.1-5.3)
- Personal life-rhythm tracking (Personal, requires data source decision) — **DEFERRED per M6.1-6.0**
- Environment→emotion reasoning (Personal, requires explicit pipeline) — **DEFERRED per M6.1-6.0**
- LivedContextAggregator (CAPABILITY — currently de-facto, no concrete behavioral need)
- **Agency re-enable** — **M6.1-8.1 PENDING** (3-line fix, awaiting Bry decision)

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M6.1-0: `C:\Users\bbfcc\gov_1_temp\m6_1_0_lived_context_awareness_audit.md`
- M6.1-1: `C:\Users\bbfcc\gov_1_temp\m6_1_1_lived_context_taxonomy_audit.md` (canonical)

**Next work**: None authorized. M6.1-2 documentation is canonical. Future M6.1-* capability
tickets (weather / news / personal-rhythm) require Owner authorization per §2.8 decision boundary
and must follow the M5.15-6 integration pattern (architecture decision → implementation →
closeout).

### 5.7 M6.2 — Text / TTS Response Path Separation

**Status**: M6.2-0 + M6.2-1 both CLOSED. Async text/TTS separation already correct in production
(M6.2-0 audit); M6.2-1 closed the actual gap (message_id correlation for rapid sequential
messages from same agent). 0 frozen contract change. 0 production data mutation. 5 production
files + 1 test file. Per Quality > Quantity: NO M6.2-2 recommended (0 P0/P1/P2 remaining;
6 P3 out of scope per M6.2 work order — TTS cancellation, streaming, queue, etc.).

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.2-0 | Text / TTS Response Path Separation Architecture Audit (READ-ONLY) | (docs only) | **CLOSED** | 18 sections, 25.6KB audit. **Key finding**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(self._synthesize_async(...))` in `FishTTSHandler._on_agent_speak` (`src/llm/fish_tts_handler.py:266-273`). TTS latency 0.5-6.4s (mean 3.3s, evidence from `data/tts/` filesystem + `trace.log`). Transport: WebSocket 2 distinct message types (`agent_speak` + `agent_audio_ready`); Telegram 2 adapter methods (`send_message` + `send_voice`). **Identified gap (P2)**: `AGENT_AUDIO_READY` payload missing `message_id`/`correlation_id`. ChannelRouter `_pending_voice_target[agent_id]` last-write-wins (race condition for concurrent messages). Web client `lastAudioByAgent[agentId]` overwrites. **M6.2-1 RECOMMENDED**: 5 files, +14/-6 lines, 0 frozen contract change. 0 P0, 0 P1, 1 P2 (correlation gap), 6 P3. |
| M6.2-1 | Per-Message TTS Correlation Minimal Implementation (IMPLEMENTATION) | `965df92` | **CLOSED** | **Gap closed end-to-end via `message_id = SoulEvent.event_id` UUID**. 5 production files + 1 test file. `src/voice/tts_service.py`: `synthesize_and_store(..., message_id: Optional[str] = None)` — message_id added to AGENT_AUDIO_READY payload + return dict. `src/llm/fish_tts_handler.py`: extracts `event.event_id` in `_on_agent_speak`, passes to `_synthesize_async` and TTSService. `src/io/channels/router.py`: `_pending_voice_target` keyed by `message_id` (per-message), with `_pending_voice_target_legacy` for `agent_id` fallback when message_id is None. `src/io/gateway.py`: WS `agent_speak` + `agent_audio_ready` payloads include `message_id`. `static/index.html`: `lastAudioByMessageId` cache, `attachReplayButtonToMessage` + `replayMessageAudio`, `data-message-id` attribute on rendered messages. **11/11 new tests + 45/45 focused regression PASS** in 0.47s. 0 frozen contract change. 0 production data mutation. **No P0/P1/P2 remaining**. Per Quality > Quantity: NO M6.2-2 recommended. M6.1-9 PENDING 24h RUN-AND-COLLECT. |

**Architectural findings (retained from M6.2-0)**:

1. **Text/TTS separation was already correct** — TTS is fire-and-forget. No new infrastructure needed.
2. **Transport is already decoupled** — WebSocket 2 message types, Telegram 2 adapter methods.
3. **Real gap was correlation key** — `AGENT_AUDIO_READY` had no message_id; client could only do
   last-write-wins per agent. Rapid sequential messages from same agent could have wrong audio attached.
4. **M6.2-1 minimal fix** — Reuse `SoulEvent.event_id` (UUID) as correlation key. 0 new schema,
   0 new infrastructure, 0 frozen contract change.

**Out-of-scope per M6.2 work order (P3, NOT implemented)**:
- TTS cancellation
- TTS streaming
- TTS queue / broker
- TTS provider change
- WebSocket protocol redesign
- Telegram architecture redesign

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M6.2-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md`
- M6.2-1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md`
- M6.2-1 test file: `tests\test_m6_2_1_text_tts_correlation.py` (in-repo, 11 tests, 21KB)

**Next work**: None authorized. M6.2-1 closed the P2 correlation gap. Per Quality > Quantity,
M6.1-9 (Lived Context Formation Audit, READ-ONLY, PENDING 24h RUN-AND-COLLECT) is the
recommended next ticket — it depends on production evidence accumulation from M6.1-8.2 Agency
re-enable (started 2026-08-14 19:27 EDT).

---

### 5.8 DSH Multi-Agent MVP — Domain Core（前置設計 + MVP implementation）

**Status**: **MVP COMPLETE / ACCEPTED**（MVP Contract Gate 8/8 PASS，bypass discovered = NO）。

DSH Multi-Agent MVP 是 Soul OS 遷入 DeepSeek Harness 的前置 domain core。四份 canonical contract（2A–2D）+ MVP-1~7 全部 commit，經 authority / durability / recovery / single-writer / E2E / cross-MVP gate 驗收。

| Phase | 內容 | Commit |
|-------|------|--------|
| 2A–2D | Architecture Contracts（Work / Workspace / Authority / Persistence） | `f5fb0cd` |
| MVP-1 | Work Contract + Durable Store | `9a8b7a2` |
| MVP-2 | DSH Adapter Boundary（ports + bridge） | `99b95db` |
| MVP-3 | Chief + Specialist Roles + single-writer | `a1a150f` |
| MVP-4 | Workflow / Handoff | `61b26c3` |
| MVP-5 | Authority Boundary（capability + approval + identity seam） | `015139e` |
| MVP-6 | Recovery / Resume（durable authority + single-writer） | `8bb8d91` |
| MVP-7 | E2E Vertical Slice（2A–2D 完整閉環） | `d7877e0` |

**Canonical contracts**（`docs/`）：
- `DSH-WORK-CONTRACT.md`（2A Work / Execution Boundary）
- `DSH-WORKSPACE-DESIGN.md`（2B Workspace / Git / Worktree）
- `DSH-HUMAN-AUTHORITY.md`（2C Human Authority）
- `DSH-PERSISTENCE.md`（2D Persistence / Recovery / Resume）
- `DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate 10/10 PASS）

**核心原則**：Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.

**Accepted limitations / future hardening**（NOT resolved，下一階段不得誤認成已解決）：
1. `WorkObject.approvals[]` 目前不是 approval durable truth（approval 的 durable truth 在 AuthorityStore）
2. `requires_human_approval` 尚未 enforcement（state machine 只宣告不 gate，authority 由 capability policy 層執行）
3. `DURABLE_WRITER="kernel"` 目前仍是 convention-level identity（self-attested 字串，非 security-level）
4. Python object graph 的 public/mangled escape hatch 是已知 limitation（no-true-private）

**MA 治理鏈（MA-0 → MA-4-R1）**：
- MA-0 Architecture Audit ✅
- MA-1 Adapter Boundary ✅ READY FOR IMPLEMENTATION
- MA-2 Migration Architecture ✅ READY FOR MIGRATION PLAN
- MA-3 Migration Decomposition ✅ READY FOR IMPLEMENTATION
- MA-4 Build Plan ❌ BLOCKED → MA-4-R1 修復（authority trust establishment + resume idempotency）→ Independent Review PASS → **IMPLEMENTATION AUTHORIZED**
- P0-1 Minimal Work Execution Adapter → Independent Adversarial Review（READ-ONLY）→ **READY FOR PHASE 0 GATE**
- P1-Preflight Hardening（M1/M2/M3）→ Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**
- P1 Decomposition（Execution Routing）→ Review #1 BLOCKED（2 項）→ 修正 → Re-review → **READY FOR IMPLEMENTATION**
- P1-A Execution Target Contract（Domain Core 側）→ Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**

**P0-1 成果**（commit `ece757b`）：`src/work_adapter/`（bridge.py + execution.py）、`dsh_adapter/soul-dsh-adapter.mjs`（mock TS adapter）、`tests/test_work_adapter.py`（27 tests）。230 tests 全綠。8/8 Hard Checks PASS + 10 acceptance gates PASS。`git diff 26e1e49 -- src/work` 為空（Domain Core 十一模組零改動）；`src/` 全樹零 DSH import；無 durable write bypass；No-DSH Survival 實測成立。

**Phase 0 Gate（Contract / E2E / No-DSH）**：→ **Phase 0 CLOSED**（commit `ece757b`）。

**P1-Preflight 成果**（commit `34e91d4`）：M1 `HandoffStatus`→WorkState 語義（blocked/needs_input → state_transition(current→blocked)，不照記產出）、M2 `result_type`↔capability anchor 驗證（堵 event 類型/provenance 錯記）、M3 bridge error contract 統一（binary I/O + 主執行緒 decode，`UnicodeDecodeError`→`BridgeExecutionError`）。239 tests 全綠。8/8 Hard Checks PASS + bypass=NO。

**P1 Decomposition 成果**（`docs/DSH-P1-EXECUTION-ROUTING.md`，commit 待 landing）：P1-A~P1-E 分解，鎖定 P1-A Execution Target Contract（`ExecutionShape` capability-neutral + shape 由 Soul 推導 + adapter 只 translate）。Review #1 BLOCKED（A2 resume discriminator 未鎖 + A5 防火牆無 mechanism）→ 修正 → Re-review READY。

**P1-A 成果**（commit 待 landing）：`src/work/schema.py` 新增 `ExecutionShape` enum（single_shot / multi_stage / continuous，capability-neutral）+ `src/work/workflow.py` 新增 `derive_execution_shape`（dependencies 非空→multi_stage，其餘→single_shot；continuous 不實作，待 P1-D）+ `src/work_adapter/execution.py` payload 新增 `execution_shape`。249 tests 全綠（239 + 10 new）。8/8 PASS + bypass=NO。`src/work/` 授權集 {kernel.py, schema.py, workflow.py}；零 DSH import 不變。

**Phase 1 剩餘 backlog（CAN-DEFER / 後續 phase）**：
1. refs content-address 驗證（需 artifact store；P1-B 決定與 store 同批落地）
2. production adapter 依 MA-4 §1.1 移出 repo 為獨立 package（P1-E）
3. grant() reject `expires_at=None` / e2e 改用 `issue_hmac_context` / durable nonce registry（MA-4-R1 承接）
4. blocked handoff 重試 dedup（crash-after-write 場景，P1-D）
5. continuous 觸發條件（P1-D goal resume semantics，derive 現只回 single_shot）
6. multi_stage workflow script authorship（P1-D 前置決策 A6，未解）
7. stale test `tests/test_soul_md_loader.py`（import 已移除的 `SOUL_OS_OVERRIDE`，pre-existing，非 P1-A 範圍）

**Next work**: P1-B（Artifact / Reference Boundary，ref 定址與 store 同批落地決策）→ P1-C（DSH Execution Routing 第一期，single_shot subagent）。

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
- `logs/m5_13_4_2_strict_boundary_closeout.md` (M5.13-4.2)
- `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md` (M5.13-5, out-of-repo, CANONICAL LATEST)

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

**M5.15**:
- All closeout reports out-of-repo per M5.13-3.1 lesson (no `git stash -u + drop`):
  - M5.15-1 boundary audit: `C:\Users\bbfcc\gov_1_temp\m5_15_1_boundary_audit.md`
  - M5.15-2 decision analysis: `C:\Users\bbfcc\gov_1_temp\m5_15_2_decision_analysis.md`
  - M5.15-3 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`
  - M5.15-3 implementation report: `C:\Users\bbfcc\gov_1_temp\m5_15_3_implementation_report.md`
  - M5.15-4 STOP report: `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md` (SUPERSEDED, historical preserved)
  - M5.15-5 decision analysis: `C:\Users\bbfcc\gov_1_temp\m5_15_5_decision_analysis.md`
  - M5.15-5 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md`
  - M5.15-5 implementation report: `C:\Users\bbfcc\gov_1_temp\m5_15_5_implementation_report.md`
  - M6.0-5.6.1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_0_5_6_1_closeout.md` (canonical, Budget Profile Registry)
  - M5.15-6-PREFLIGHT decision: `C:\Users\bbfcc\gov_1_temp\m5_15_6_preflight_architecture_decision.md`
  - M5.15-6 RESUME STOP report: `C:\Users\bbfcc\gov_1_temp\m5_15_6_stop_report.md` (historical, M3.1 conflict)
  - M5.15-6 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md` (canonical, RESUME Option 1)

**M6.1** (per M5.13-3.1 lesson, all closeout reports out-of-repo unless otherwise noted):
- M6.1-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_1_0_lived_context_awareness_audit.md`
- M6.1-1 taxonomy audit: `C:\Users\bbfcc\gov_1_temp\m6_1_1_lived_context_taxonomy_audit.md` (canonical)
- M6.1-3 evidence audit: `C:\Users\bbfcc\gov_1_temp\m6_1_3_evidence_audit.md`
- M6.1-3.2 weather activation: `C:\Users\bbfcc\gov_1_temp\m6_1_3_2_closeout.md`
- M6.1-3.3 weather evaluation: `C:\Users\bbfcc\gov_1_temp\m6_1_3_3_weather_evaluation.md`
- M6.1-4 personal audit: `C:\Users\bbfcc\gov_1_temp\m6_1_4_personal_audit.md`
- M6.1-5 information audit: `C:\Users\bbfcc\gov_1_temp\m6_1_5_information_audit.md`
- M6.1-5.1 news impl closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_5_1_closeout.md`
- M6.1-5.2 news activation: `C:\Users\bbfcc\gov_1_temp\m6_1_5_2_closeout.md`
- M6.1-5.3 news lookback: `C:\Users\bbfcc\gov_1_temp\m6_1_5_3_closeout.md` + `C:\Users\bbfcc\gov_1_temp\m6_1_5_3_final_closeout.md`
- M6.1-6.0 personal decision: `C:\Users\bbfcc\gov_1_temp\m6_1_6_0_closeout.md`
- M6.1-7 production evidence: `logs\m6_1_7_production_lived_context_evidence.md` (in-repo, M6.1-7 audit closeout)
- M6.1-8 agency re-enable: `logs\m6_1_8_agency_reenable_investigation.md` (in-repo, M6.1-8 audit closeout)
- M6.1-8.1 isolated validation: `C:\Users\bbfcc\gov_1_temp\m6_1_8_1_closeout.md` (out-of-repo per M5.13-3.1)
- M6.1-8.1 test file: `tests\test_m6_1_8_1_agency_reenable_isolated.py` (in-repo, 21 tests, 905 lines)
- M6.1-8.2 controlled rollout: `C:\Users\bbfcc\gov_1_temp\m6_1_8_2_closeout.md` (out-of-repo per M5.13-3.1)

**M6.2** (per M5.13-3.1 lesson, all closeout reports out-of-repo unless otherwise noted):
- M6.2-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md` (READ-ONLY, no in-repo file changes)
- M6.2-1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md` (out-of-repo per M5.13-3.1)
- M6.2-1 test file: `tests\test_m6_2_1_text_tts_correlation.py` (in-repo, 11 tests, 21KB)
- M6.1-9 partial audit (T+50min): `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` (out-of-repo, 19.7KB)
- M6.1-9.1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (out-of-repo, 14.9KB, configuration-only fix)
- M6.1-9 partial T+4h33m closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_closeout.md` (out-of-repo, 13.6KB, 24h window incomplete)
- M6.1-9 full T+24h follow-up: PENDING via cron `m6_1_9_t24h_v2` (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) — TRUE T+24h mark is 8/15 19:27 EDT
- M6.1-9.2 closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_2_closeout.md` (out-of-repo, 18.2KB, READ-ONLY forensic audit, 0 source code change)

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
| 2026-08-12 (GOV-2-R1 finalize) | Established canonical head reference `f31945e8a58a0d8fa323588437acae968e37da76` (GOV-2-R1 finalize commit). GOV-2-R1 alignment commit = `3539de2f8795ad3e516a619dc556563e8c357c68` (distinct value; not off-by-one). 26/27 GOV-2 consistency checks PASSED + 1 obsolete assertion (pre-commit HEAD check expected `e6effd8`; obsolete after subsequent governance commits). Note: this row is a historical record; the **current** canonical head reference is recorded in §1. | Mavis / Lin | GOV-2-R1 finalize |
| 2026-08-12 (M5.15-3) | WorldEventSource → Event Bus canonical integration (commit `b4b981a7b24678779551bccca2f4b6eb4dd20b3e`). 31/31 new tests + 159/159 regression PASS. 0 frozen contract change. 0 production mutation. M5.15 chain F1 RESOLVED; F2/F3/F4 remain CANDIDATEs (per M5.15-1 audit + M5.15-2 decision). 3 files changed: `src/world/source/synthetic.py` (+114 -6), `scripts/run_server.py` (+51), `tests/test_m5_15_3_canonical_bus_integration.py` (new +1089). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`. | Mavis / Lin | M5.15-3 |
| 2026-08-12 (M5.15-4) | Cross-Handler Lineage Propagation STOPPED. M5.15-4 inspection discovered parent_event_id contract (M5.4-5.1 frozen) requires parent to be a known InnerLifeEvent.event_id (writer.py:172 + 32-hex format check identity.py:195). Cannot reference SoulEvent.event_id (UUID with dashes) or WorldEvent.novelty_id (free string) without modifying frozen contract. 3 of 7 Stop conditions triggered. STOP report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md`. Bry Decision: 0 implementation, await M5.15-5 architecture decision. | Mavis / Lin | M5.15-4 |
| 2026-08-12 (M5.15-5) | WorldEvent ↔ InnerLifeEvent Identity Bridge (commit `0aedbef25cf0cb8ba793a7620833ec6cfdb70db8`). 52/52 new tests + 285/285 regression PASS. 1 additive frozen-contract amendment (M5.4-5.1 InnerLifeEvent +1 Optional field `source_world_event_novelty_id: Optional[str] = None`). 13 frozen contracts preserved (parent_event_id / lineage_depth / lineage_path / correlation_id / provenance all 0 change). 0 production mutation. Two-Layer Lineage Model established: Layer 1 External Causality (WorldEvent → InnerLifeEvent, free string) + Layer 2 Internal Lineage (InnerLifeEvent → InnerLifeEvent, M5.4-5.1 frozen preserved). M5.15-4 SUPERSEDED by M5.15-5 (per GOV-2 §2.5). F1 + F3 + F4 RESOLVED. F2 CANDIDATE (M5.15-6). 10 files changed: 6 source + 4 test. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md`. | Mavis / Lin | M5.15-5 |
| 2026-08-12 (M5.15-6) | Real-World Calendar Source Integration (commit `c2de02c`). 55/55 new tests + 211/211 regression PASS. 0 frozen contract change across 15 contracts. RESUME Option 1 (Bry authorization 2026-08-12 19:37): `novelty_id = SHA256(VEVENT.UID)[:32]`, `data["ical_uid"] = exact UID`, `data["ical_sequence"] = VEVENT.SEQUENCE` (observability only). IcalCalendarSource (src/world/source/calendar_ical.py): polling-driven (300s default), env-gated via SOULOS_CALENDAR_ICAL_URL, 24h lookahead default, parent-only RRULE (Q5), CANCELLED skipped (Q7), 1 URL = 1 source (Q9), library icalendar (PyPI MIT), HTTP via urllib stdlib + asyncio.run_in_executor (non-blocking), 30s timeout, MAX_EVENTS_PER_POLL=500 cap, failure observable (log+skip+retry, never crash, never silent). 4 files changed. F1 + F2 + F3 + F4 all RESOLVED. M5.15 series all CLOSED. | Mavis / Lin | M5.15-6 |
| 2026-08-12 (M5.13-5) | Untouched-Entry Decay (commit `9501603796ac250de95e19dc0fa2b543f81d95da`). 14/14 new tests + 56/56 M5.13 full suite + 105/105 adjacent regression PASS. 0 frozen contract change. 0 production mutation. **M5.13 series now FULLY CLOSED** (no remaining OPTIONAL/DEFERRED tickets). Implementation: 1 constant `UNTOUCHED_DECAY_GRACE_DAYS = 1.0` + 1 function `_decay_locked` extended with `created_at` fallback. Decay anchor priority: (1) `last_interaction_at` (M5.13-4.2 existing), (2) `created_at` (M5.13-5 new, only when `last_interaction_at` is None AND `created_at` is > 1.0 day old), (3) None (legacy/malformed → skip, no crash). Touched entries: continue using `last_interaction_at` (M5.13-4.2 unchanged). Preserves M5.13-2 strict 0.3 contract (grace period ensures `ensure_relationship(0.3) → get()` returns 0.3). 2 files changed: `src/soul/relationships.py` (+36 -6), `tests/test_m5_13_5_untouched_decay.py` (NEW +471, 14 tests in 7 sections A-G). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md`. D2 RESOLVED. | Mavis / Lin | M5.13-5 |
| 2026-08-12 (M6.0-5.6.1) | Budget Profile Registry (commit `3d1fae4a86b5b62ab1edd5688203f27fe3c36a36`). 29/29 new tests + 12/12 M6.0-5.6 + M6.0-5.6.1 manual regression PASS. 0 frozen contract change. 0 production mutation. **M6.0 series now FULLY CLOSED** (no remaining OPTIONAL/DEFERRED tickets; M6.0-5.5-R1 remains BLOCKED per spec). Implementation: 1 `BudgetProfile` str-Enum (CHAT/DIARY/DREAM) + 1 `_BUDGET_PROFILE_VALUES` dict (frozen profile → tuple) + 1 `EvaluationBudgetConfig.from_profile()` @classmethod factory. Profile values: CHAT (3/2/5000/0.05, == default for no-op migration), DIARY (2/1/3000/0.03, smaller budget for high volume), DREAM (1/1/2000/0.02, smallest budget for low observable). Defaults preserved 100% (CHAT == `EvaluationBudgetConfig()`). `from_profile()` rejects non-`BudgetProfile` inputs (raw string, None, int, other enum) with `TypeError`. Profile-derived configs are frozen + hashable. 3 files changed: `tests/_helpers/subjective_eval/multi_model_runner.py` (+100), `tests/_helpers/subjective_eval/__init__.py` (+3 -1), `tests/test_m6_0_5_6_1_budget_profile.py` (NEW +386, 29 tests in 7 sections A-G). Pre-existing test collection issue (`tests/` lacks `__init__.py`, 5 M6.0.x tests fail to collect via pytest — per M5.15-6 closeout §6.1 known finding) verified via direct script, not introduced by D3. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_0_5_6_1_closeout.md`. D3 RESOLVED. | Mavis / Lin | M6.0-5.6.1 |
| 2026-08-12 (M5.15-6) | Real-World Calendar Source Integration (commit `c2de02c`). 55/55 new tests + 211/211 regression PASS. 0 frozen contract change across 15 contracts (M3 WorldEvent, M3.1 ABC, M3.1 Bus, M5.4-5.1 InnerLifeEvent 9 fields, M5.4-5.1 parent_event_id, M5.4-5.1 lineage, M5.9-2 QUALIFYING_TYPES, M5.9-3 Adapter, M5.15-3 canonical bus path, M5.15-5 source_world_event_novelty_id, VALID_SOURCES, _NOVELTY_ID_RE all unchanged). RESUME Option 1 (Bry authorization 2026-08-12 19:37): `novelty_id = SHA256(VEVENT.UID)[:32]`, `data["ical_uid"] = exact UID`, `data["ical_sequence"] = VEVENT.SEQUENCE` (observability only). SEQUENCE excluded from hash (Q6: same UID + different SEQUENCE → same hash → adapter dedupes). 0 production mutation (yua/relationships.json 10095B81... unchanged). IcalCalendarSource (src/world/source/calendar_ical.py): polling-driven (300s default), env-gated via SOULOS_CALENDAR_ICAL_URL, 24h lookahead default, parent-only RRULE (Q5), CANCELLED skipped (Q7), 1 URL = 1 source (Q9), library icalendar (PyPI MIT), HTTP via urllib stdlib + asyncio.run_in_executor (non-blocking), 30s timeout, MAX_EVENTS_PER_POLL=500 cap, failure observable (log+skip+retry, never crash, never silent). 4 files changed: src/world/source/calendar_ical.py (NEW, +476), src/world/source/__init__.py (+14), scripts/run_server.py (+65), tests/test_m5_15_6_calendar_ical_source.py (NEW, +1105). All 12 critical regression tests A-L PASS. 7 pre-existing baseline failures unchanged (M3.1 Phase B/C/D + M2.0); 1 pre-existing test ordering issue (test_m5_4_5_3::test_g2 env var setup). F1 + F2 + F3 + F4 all RESOLVED. M5.15 series all CLOSED. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md`. | Mavis / Lin | M5.15-6 |
| 2026-08-13 (M6.1-2) | Lived Context Canonical Boundary & Documentation (commit `9e050f6`). Documentation-first implementation. 2 files changed: `README.md` (+54 §7.1 "Lived Context Boundary (M6.1)" sub-section, 4-layer architecture + 5 contexts + boundary invariant + capability positioning), `logs/ENGINEERING_STATE.md` (+74 §5.6 M6.1 milestone table + canonical taxonomy + 4-layer boundary + boundary invariant + missing capabilities + this §9 change log entry + §1 status snapshot row). 0 source-code change, 0 frozen contract change (15 contracts preserved: M3/M3.1/M5.4-5.1/M5.4-5.5/M5.9-2/M5.9-3/M5.10/M5.13-2/M5.15-3/M5.15-5/M5.15-6/VALID_SOURCES), 0 production mutation, 0 new runtime abstraction. **No `LivedContextAggregator` wrapper created** — `src/llm/proxy.py:_build_messages_group()` and `_build_messages_private()` is the de-facto Lived Context aggregator (block order: identity → memory → mood → relationship → inner_life → world → temporal). M6.1-0 / M6.1-1 READ-ONLY audits out-of-repo. Frozen contracts 0 change verified via diff. Production data byte-for-byte unchanged (memory.db / perception_trace / shadow_log / relationships / diary / dream / emotion persistence all not touched). 20 baseline untracked artifacts preserved. | Mavis / Lin | M6.1-2 |
| 2026-08-13 (M6.1-3.1) | Open-Meteo Weather Source (commit `ac50256`). Physical Lived Context signal source. 4 files changed: `src/world/source/open_meteo.py` (NEW, +417), `src/world/source/__init__.py` (+7), `scripts/run_server.py` (+34), `tests/test_m6_1_3_1_open_meteo_weather.py` (NEW, +805, 50+ tests). 0 frozen contract change (VALID_SOURCES already includes `"weather"`, types `rain_started` / `weather_temp_change` fit existing M3 WorldEvent schema, `source_id = "weather"` per M3.1 contract). Stdlib only (urllib + json, no new dependencies). 1800s polling default (min 60s), 30s HTTP timeout, deterministic `novelty_id = SHA256(f"weather.{lat:.2f}_{lon:.2f}.{hour}.{state}")[:32]`. State bucket: binary `rain` (precipitation >= 0.1mm OR WMO code in {51-67, 80-82, 95-99}) / `no_rain`. Provider identity preserved in `data["weather_provider"] = "open_meteo"`. 406/406 broad regression PASS. 5 pre-existing baseline failures (M3.1 Phase B/C/D + M2.0) + 5 M6.0.x test collection issues unchanged (not introduced). | Mavis / Lin | M6.1-3.1 |
| 2026-08-13 (M6.1-5.1) | RSS News Source (commit `9f8ece8`). Information Lived Context signal source. 5 files changed: `src/world/source/news_rss.py` (NEW, +663), `src/world/source/__init__.py` (+7), `scripts/run_server.py` (+34), `tests/test_m6_1_5_1_news_rss.py` (NEW, +1591, 92 tests in 17 sections A-Q), `requirements.txt` (no new deps). 0 frozen contract change (VALID_SOURCES already includes `"news"`, `celebrity_news` baseline 0.05; new `news_event` type does NOT extend WORLD_QUALIFYING_TYPES → no InnerLifeEvent impact per M5.9-2 minimal scope). Stdlib only (urllib + xml.etree.ElementTree + email.utils.parsedate_to_datetime). 1800s polling, 2h lookback default, 10 articles/poll cap, 30s HTTP timeout, 10000-entry in-memory FIFO dedup. Deterministic `novelty_id = SHA256(f"{provider}.{canonical_url}.{published_at_iso}")[:32]`. 8 working public feeds (BBC World/Top, NASA Breaking, Hacker News, Guardian, Ars Technica, NPR Top, Al Jazeera); Reuters + AP UNAVAILABLE (documented, 0 implementation impact). 92/92 focused tests + 469/469 broad regression PASS. 5 pre-existing baseline failures + 5 M6.0.x collection issues + 11 M6.0.x errors unchanged. | Mavis / Lin | M6.1-5.1 |
| 2026-08-13 (M6.1-6.0-C) | Personal Lived Context Architecture Decision Audit Closeout (commit `49adf46`). 1 file changed: `logs/m6_1_6_0_personal_lived_context_audit.md` (in-repo, +596 insertions, canonical closeout). Verdict: **DEFER (D)**. 0/5 FULLY ANSWERABLE, 2 PARTIALLY, 3 NOT. 8 signals inventoried (Calendar / Telegram / Temporal / Memory / Inner Life / WorldPerception / Weather / News). 3 options compared: A Manual (~50 LOC, requires Bry active commitment, HIGH privacy) / B Inference (LOW cost, but surveillance-by-proxy, violates M3 design rule "Personal inference ≠ raw signal") / C Dedicated (~150 LOC, requires wearable/phone/browser/GPS/surveillance/large infra, out of work order scope, reduces to A). Verdict: D wins on aggregate (no cost, no risk, no production use case yet). Bry 拍板 2026-08-13 21:16: "Personal = DEFER" (Information Lived Context priority 1st round). | Mavis / Lin | M6.1-6.0-C |
| 2026-08-14 (M6.1-7) | Production Lived Context Evidence Reassessment (commit `bdf76ad`). 1 file changed: `logs/m6_1_7_production_lived_context_evidence.md` (in-repo, +620 insertions). READ-ONLY audit, 0 source/test/prod modification, 0 commit/push during audit, production data byte-for-byte unchanged. Verdict: **LIVED CONTEXT NOT YET FORMED**. Q1-Q6 reassessment: Q1 PARTIAL (Weather only), Q2 PARTIAL (no Personal signal), Q3 NO EVIDENCE (multi-signal never observed), Q4 YES (1081 LLM responses had world_context = 31.4%), Q5 PARTIAL (InnerLife ✓, Diary ✗), Q6 NO EVIDENCE. Architecture findings: 0 P0, 0 P1, 2 P2 (Agency triggers disabled, world_context single-source), 2 P3 (News gate filter not in ops docs, Scheduler `agents=0` decision not documented). Production trace: 2385 perception_trace entries (15 calendar + 1752 weather + 122 news), 602 weather context_injected, 1081 LLM responses with world_context. Per-agent shadow distribution: mahiru 167, mai 94, akane 92, yua 74, anna 67, ruka 67, rem 64, ram 45, aoi 42, miku 39. Last diary file: 2026-08-08 08:00:28 (6+ days ago). Scheduler log: `morning=08:00:00 night=22:00:00 prob=1.0 agents=0` (since 8/8). 20 baseline untracked artifacts preserved. | Mavis / Lin | M6.1-7 |
| 2026-08-14 (M6.1-8) | Agency Re-enable Investigation (commit `f699d93`). 1 file changed: `logs/m6_1_8_agency_reenable_investigation.md` (in-repo, +581 insertions, 18 sections, 581 lines). READ-ONLY architecture investigation, 0 source/test/prod/config mutation, 0 commit/push during audit, 20 baseline untracked artifacts preserved. **Root cause identified**: M5.2 migration (commit `481ea41`, 2026-08-08 21:11 EDT, "refactor(m5.2): migrate scheduler triggers to agency event bridge") removed `scheduler.register(aid, cb)` callsite in `run_server.py` (M5.2-I Phase 7) but M5.2-I Phase 8 changed iteration source to `_all_agents` in `scheduler.py:_fire_all` without adding a replacement `register(aid)` call. Result: `SoulScheduler._all_agents` permanently empty (default `[]`). 5 of 6 trigger paths (morning / night / dream / event / proactive_dm) silent-skip at `if not self._all_agents: return` (scheduler.py:560/620/921) or via `_get_proactive_agents() returns []` (L696-697). Heartbeat separately disabled by 修法 12 (Bry 8/6 17:12, INTENTIONAL). 0 diary/dream/event/proactive_dm writes for 6+ days (8/8 21:13 → 8/14 22:38 EDT). 4 handlers (AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler) all correctly wired to bus, all correctly filter trigger_type, all waiting for AGENCY_TRIGGER that never comes (operational but starved). **Documented regression** (M5.2-I Phase I-9 sweep `logs/m5_2_i_i9_callback_dependency_sweep.md` 2026-08-08 17:50 EDT §2.2 explicitly noted "production 完全沒註冊" but treated as "API COMPAT" PASS — false equivalence of "interface preserved" vs "behavior preserved"). **Fix proposed**: 3 lines in `run_server.py` after `diary_callbacks_real[aid] = cb_real` — `for aid in agent_ids: scheduler.register(aid)`. 0 frozen contract change, 0 production data change, 0 M3/M5.4-5.x modification. 4 safety options for Bry decision (A direct / B gradual / C shadow / D test-isolated). M6.1-8.1 PENDING Bry 拍板. M6.1-9 DEFERRED (after M6.1-8.1). | Mavis / Lin | M6.1-8 |
| 2026-08-14 (M6.1-8 registry sync) | Canonical engineering state registry updated for M6.1 series progress. 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 progress row, +§1.1 M6.1 milestone row updated, +§1.1 Current HEAD section updated to `f699d93`, +§5.6 M6.1 milestone table extended with 13 new ticket rows M6.1-3 through M6.1-8 + 2 pending rows M6.1-8.1 / M6.1-9, +§5.6 canonical taxonomy updated (Physical/Information now LIVE, Personal DEFERRED, Temporal LIVE), +§5.6 missing capabilities updated (Weather + News RESOLVED, Personal DEFERRED, Agency re-enable M6.1-8.1 PENDING), +§7 closeout log list extended with 13 M6.1 closeout references, +§9 change log extended with 5 new entries M6.1-3.1 / M6.1-5.1 / M6.1-6.0-C / M6.1-7 / M6.1-8). | Mavis / Lin | M6.1-8 registry sync |
| 2026-08-14 (M6.1-8.1) | Agency Re-enable Isolated Validation (commit \d0c33da\). 1 file changed: \	ests/test_m6_1_8_1_agency_reenable_isolated.py\ (NEW, +905 lines, 21 tests in 7 sections A-G). **STRICT 0 PRODUCTION ACTIVATION**: 0 source code changes, 0 production data mutation, 0 production config change, 0 production server restart, all test writes isolated to \	mp_path\ via \SOUL_OS_DATA_DIR\, mock executors only (no real LLM/Telegram), frozen contracts 0 change. **3-line minimal fix PROVEN CORRECT** in isolated test env: 20 PASS / 1 XFAIL (F.2 = future-proof regression test, awaiting M6.1-8.2 production fix) / 0 FAIL in 0.47s. 71/71 M5.2 series tests still PASS (regression verified). Sections: A baseline regression reproduction (4 tests) / B minimal fix validation (3 tests) / C AGENCY_TRIGGER publication x 5 paths (5 tests, 12 total events) / D handler reception x 4 handlers (4 tests, 10 total handler invokes) / E safety verification (2 tests, no production mutation, no Telegram, no LLM) / F regression test (2 tests, F.1 PASS, F.2 XFAIL pending M6.1-8.2) / G diagnostic summary (1 test). All 12 acceptance criteria MET. No stop conditions triggered. Closeout out-of-repo at \C:\\Users\\bbfcc\\gov_1_temp\\m6_1_8_1_closeout.md\. **Bry rollout decision still required** for production (Option A direct + 24h monitor / B gradual / C shadow / D test-isolated per M6.1-8 §7.2). | Mavis / Lin | M6.1-8.1 || 2026-08-14 (M6.1-8.2) | Controlled Production Agency Re-enable (commit `2a42521`, Bry approved Option B / Gradual). 2 files changed: `scripts/run_server.py` (+59 lines, env-var-driven gradual registration block) + `.env.example` (+17 lines, M6.1-8.2 docs). 0 frozen contract change (15 contracts preserved). 0 production data mutation. 0 handler/Agency/scheduler architecture change. 0 Lived Context / Personal / News / M3 / M5.4-5.x modification. **10-phase gradual rollout** in 19:18:12 to 19:27:36 EDT (~10 min): Phase 1 (1 agent, ruka) -> 2 (2 agents) -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 (full). All 10 phases confirmed via `[Scheduler] start ... agents=N` log line. 0 ERROR/Traceback/CRITICAL during rollout. /health=200 stable. Final state: agents=10 (M5.2 regression fully recovered, was agents=0 for 6+ days since 8/8 21:11). Env var `SOULOS_AGENCY_GRADUAL_AGENTS` controls per-phase agent list (gitignored `.env`). Final state: env var unset -> M6.1-8.1 default (all 10 agents registered). **STRICT 0 PRODUCTION DATA MUTATION**: 0 writes to memory.db / relationships.json / diary / dream / inner_life during rollout. Perception trace +9 weather events (M6.1-3.1 polling, unrelated to this ticket). 24h RUN-AND-COLLECT deferred to M6.1-9 (first evidence window 22:00 EDT today, full 24h by 8/15 19:30 EDT). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_8_2_closeout.md`. | Mavis / Lin | M6.1-8.2 |


---

| 2026-08-14 (M6.2-0) | Text / TTS Response Path Separation Architecture Audit (READ-ONLY, in-repo registry sync only). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.2 progress row, +§5.7 M6.2 milestone table [created], +§1.1 Current HEAD section updated, +§7 M6.2 closeout log list [created], +§9 change log extended with M6.2-0 entry). Audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md` (18 sections, 25.6KB). **Key finding**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(self._synthesize_async(...))` in `FishTTSHandler._on_agent_speak` (`src/llm/fish_tts_handler.py:266-273`). TTS latency 0.5-6.4s (mean 3.3s, evidence from `data/tts/` filesystem + `trace.log`). Transport: WebSocket 2 distinct message types (`agent_speak` + `agent_audio_ready`); Telegram 2 adapter methods (`send_message` + `send_voice`). NO coupled JSON response in production. **Identified gap (P2)**: `AGENT_AUDIO_READY` payload missing `message_id`/`correlation_id`. ChannelRouter `_pending_voice_target[agent_id]` last-write-wins (race condition for concurrent messages). Web client `lastAudioByAgent[agentId]` overwrites (replay button points to "latest" not specific message). 0 frozen contract change. 0 P0, 0 P1, 1 P2 (correlation gap), 6 P3 (informational). M6.2-1 RECOMMENDED. | Mavis / Lin | M6.2-0 |
| 2026-08-14 (M6.2-1) | Per-Message TTS Correlation Minimal Implementation (commit `965df92`). 6 files changed: `src/voice/tts_service.py` (+5 -1, `synthesize_and_store(..., message_id: Optional[str] = None)`), `src/llm/fish_tts_handler.py` (+8 -2, extracts `event.event_id` in `_on_agent_speak` as message_id, passes to `_synthesize_async` and TTSService), `src/io/channels/router.py` (+12 -3, `_pending_voice_target` keyed by `message_id` per-message, with `_pending_voice_target_legacy[agent_id]` fallback), `src/io/gateway.py` (+6 -1, WS `agent_speak` + `agent_audio_ready` payloads include `message_id`), `static/index.html` (+85 -15, `lastAudioByMessageId` cache, `attachReplayButtonToMessage`, `replayMessageAudio`, `data-message-id` attribute), `tests/test_m6_2_1_text_tts_correlation.py` (NEW, +656 lines, 11 tests in 6 sections A-F). **+771/-31 net insertions**. 0 frozen contract change. 0 production data mutation. 11/11 new tests + 45/45 focused regression (M6.2-1 + M6.1-8.1 + M5.2 H3) PASS in 0.47s. Pre-existing baseline failures (test_event_bus.py 7 errors, test_io_gateway.py 2 failed, test_m1_6_audio_action_baseline.py 2 failed) verified unchanged by stashing M6.2-1 changes and re-running. **Acceptance criteria all met**: text independent of TTS latency, message_id end-to-end, rapid-message race regression covered, backward compat verified (test_d1 + test_d2). NO P0/P1/P2 remaining. Per Quality > Quantity: **NO M6.2-2 recommended**. M6.1-9 PENDING 24h RUN-AND-COLLECT. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md`. | Mavis / Lin | M6.2-1 |
| 2026-08-14 (M6.1-9 partial T+50min) | Lived Context Formation Audit partial snapshot (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` 19.7KB, 0 source code changes, 0 production data mutation, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 row updated to reflect M6.1-9 partial + agent_aoi missing anomaly, +§5.6 M6.1-9 row updated from PENDING to PARTIAL, +§7 closeout log list extended with M6.1-9 partial reference, +§9 change log this entry). **M6.1-9 partial T+50min findings**: Layers 1-2 (Signal/Perception) OPERATIONAL — 9 new weather events received since M6.1-8.2 deploy (all dedup at perception phase, same `novelty_id=fbe338b2de9ae8c94f0dd78db37bdac5` = same hour bucket + no_rain state, expected behavior). Layers 3-6 (Lived Context/Soul/Agency/Expression) WIRED but UNVERIFIED — 5/5 trigger paths active (proactive_dm whitelist=['agent_ruka'], event 4-8h, dream 22:05, diary morning 08:00 + night 22:00), 0 trigger firings yet (next window: 22:00 EDT night diary ~2h away). 0 source/test/prod/config mutation. Q1-Q6 reassessed: Q1-Q3/Q5/Q6 ⏳ PENDING 24h evidence; Q4 ⏳ PENDING 24h evidence (last 1081 LLM responses from pre-M6.1-8.2 era, 0 since deploy). **P2 anomaly discovered (6.4)**: M6.1-8.2 closeout claimed 10/10 agents but actual state is 9/10. `agent_aoi` missing from scheduler registration despite `configs/default.yaml` line 80-83 `enabled: true` and `AgentAoi` in `src/agent/registry.py:8,49`. `SOULOS_AGENCY_GRADUAL_AGENTS` env var still set with 9 specific names (M6.1-8.2 Phase 9 set), Phase 10 unset transition incomplete. Diagnosis: env var was set in process shell at M6.1-8.2 deploy, not in .env (which has it commented out); M6.1-8.2 closeout overstates final state. **M6.1-9.1 PENDING** to fix env var (add aoi to gradual list OR unset for true Phase 10). **M6.1-9-R1 (T+24h) PENDING** via cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`, fires 2026-08-15 ~20:00 EDT) — will re-collect evidence, verify trigger firings, confirm Q1-Q6 with 24h data, and either dispatch M6.1-9.1 (aoi fix) or close M6.1 series. | Mavis / Lin | M6.1-9 partial |
| 2026-08-14 (M6.1-9.1) | Restore True Phase-10 Agency Registration (FIX / PRODUCTION CONFIGURATION, configuration-only). 0 production source code files changed. 1 file changed: `logs/ENGINEERING_STATE.md` (this registry sync). **Root cause**: stale `SOULOS_AGENCY_GRADUAL_AGENTS` env var from M6.1-8.2 Phase 10 launch shell persisted into restart, leaving scheduler in `[M6.1-8.2 Gradual] registered 9/10 agents` mode (agent_aoi missing). .env file was already correct (var commented out, full re-enable documented) — the env var was set in the PowerShell process shell that originally launched the M6.1-8.2 Phase 10 server (8/14 19:27:35 EDT, PIDs 10592+17640) and the unset operation never persisted. **Fix**: `.\scripts\server_ops.ps1 stop` (killed PIDs 10592+17640 cleanly) + `.\scripts\server_ops.ps1 start` (started fresh server via hermes-agent venv python PID 20752 at 8/14 20:21:18 EDT). Clean PowerShell shell has no `SOULOS_AGENCY_GRADUAL_AGENTS` env var → default Phase-10 path executes → 10/10 agents registered including `agent_aoi`. **Result**: `[M6.1-8.2 Full] registered all 10 agents (M6.1-8.1 default)`, `agent_aoi` registered, 5/5 trigger paths wired (M5.2-G AgencyTriggerHandler + M5.2-H EventHandler + M5.2-H2 DreamHandler + M5.2-H3 DiaryHandler), /health=200, 0 ERROR/Traceback, 0 duplicate registrations. **0 source code change**, **0 frozen contract change**, **0 production data mutation** (memory.db 5398528 bytes unchanged, perception_trace.jsonl 1687980 bytes hash EE77E8A7... unchanged, shadow_log.jsonl hash 7FC0A1BC... unchanged, inner_life/trace.jsonl 553 bytes hash 7C5633E7... unchanged, all 12 relationships.json files mtime + hash preserved at 8/13-8/14 18:29 EDT). **21/21 M6.1-8.1 regression PASS** (test_f2 now PASS, was XFAIL pre-fix because run_server.py now correctly calls `scheduler.register(_aid)` for all 10 agents in default Phase-10 path). M6.1-9 24h follow-up cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`) continues unchanged, fires 2026-08-15 ~20:00 EDT to verify Lived Context formation with now-correct 10-agent registration. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (14.9KB). **0/7 stop conditions triggered**. | Mavis / Lin | M6.1-9.1 |

| 2026-08-15 (M6.1-9 T+4h33m partial) | Lived Context Formation Audit partial T+4h33m snapshot (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_closeout.md` 13.6KB, 0 source code changes, 0 production data mutation other than intended Agency trigger outputs, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 row updated with T+4h33m partial findings, +§7 closeout log list extended, +§9 change log this entry). **M6.1-9 T+4h33m findings**: **2 NEW InnerLifeEvents from Agency triggers** (entry 2: `916cbddf05334881b64cf8f3d8e09fd4` diary:night for agent_yua at 8/14 22:00:11 EDT, entry 3: `2c7627b62a7b49a7a0fd8dbce0562d4b` dream:dream for agent_miku at 8/14 22:05:11 EDT, target yua, all_agents_count 10). Inner life trace grew 553B (1 entry) → 1431B (3 entries) = +878B / +2 events. **M5.2 regression RESOLVED** (was broken 6+ days from 8/8 21:13 to 8/14 22:00). 2 diary files UPDATED: `data/soul/agent_yua/diary` mtime 8/14 22:00:11 EDT (8KB), `data/soul/agent_miku/diary` mtime 8/14 22:05:11 EDT (8KB). Diary file content LOCKED by running server (cannot read directly). 9 of 10 agents' diary/dream LLM calls INTERRUPTED by 22:38 server restart (cause unknown, not via server_ops.ps1, TELEGRAM_BOT_YUA not set in restart shell). Q1/Q2/Q4/Q6 PENDING full 24h evidence. **Cron `m6_1_9_t24h_v2`** (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) set for TRUE T+24h follow-up at 8/15 19:27 EDT. | Mavis / Lin | M6.1-9 T+4h33m partial |
| 2026-08-15 (M6.1-9.2) | Server Crash Investigation READ-ONLY forensic audit (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_2_closeout.md` 18.2KB, 0 source code changes, 0 production data mutation, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§7 closeout log list extended with M6.1-9.2 reference, +§9 change log this entry). **M6.1-9.2 root cause finding (REPLACES prior TTS-stuck hypothesis)**: `python.exe` (uv-managed CPython 3.11) is segfaulting with `0xc0000005` ACCESS VIOLATION in `python311.dll`. 6 WER crashes in 8/14 21:32 - 8/15 00:59 (Event 1000 + 1001, Application log). Two distinct fault offsets: `0x1c51db` (3 pre-M6.2-1-era crashes) and `0x26656c`/`0x266588` (3 post-M6.2-1-era crashes) — offset changes correlate with code changes (memory layout shift). 5 evidence pieces (per Bry P0 requirements): (1) Windows Event Viewer: 6 ACCESS_VIOLATION in python311.dll ✓; (2) Process exit code: `0xc0000005` (segfault, NOT clean exit) ✓; (3) Memory before restart: 19.2 MB (no leak) ✓; (4) Thread count before restart: 4 threads (no leak) ✓; (5) Process exists at restart: NO (process dead) ✓. **Bry's TTS hypothesis REJECTED**: M6.2-1 (commit `965df92` 8/14 19:08) is functionally correct; TTS has 180s `requests.post` timeout (`src/llm/fish_tts_handler.py:420`); 22:00/22:05 TTS calls were killed by 22:38 restart, not the cause of crashes. **M6.2-1 is NOT causal** — crashes pre-date M6.2-1 (plan_a_launcher.log shows hundreds of launches since 8/4). **Anomaly detected**: 2 Python interpreters running simultaneously (PID 3832 hermes-agent venv as parent of PID 2104 uv Python, the actual run_server.py owning port 8000). **Root cause classification**: C extension memory corruption (likely uvicorn + anyio + asyncio.create_task fire-and-forget pattern). 7 hypotheses evaluated: TTS blocking (REJECTED), uvicorn hang (REJECTED), external termination (REJECTED), memory exhaustion (REJECTED), resource exhaustion (REJECTED), launcher failure (REJECTED), C extension memory corruption (CONFIRMED). **Watchdog N=4/10**: 4 restarts already happened (PIDs 15756, 16836, 4052, 6724 via Plan A launcher). Will stop auto-restart at N=10. Bry needs to investigate before N=10. | Mavis / Lin | M6.1-9.2 |

| 2026-08-23 (DSH MVP) | DSH Multi-Agent MVP COMPLETE / ACCEPTED（MVP Contract Gate 8/8 PASS，bypass discovered = NO）。2A–2D contracts（`f5fb0cd`）+ MVP-1~7（`9a8b7a2` → `d7877e0`）全 commit。4 條 non-blocking discrepancy 記為 accepted limitations / future hardening（§5.8）。Next: DSH-MA-0 — Multi-Agent Environment Architecture & Adapter Boundary Audit。 | Mavis / Lin | DSH MVP |
| 2026-08-23 (DSH MA) | DSH MA-0~MA-4-R1 治理鏈閉合：MA-0 Audit → MA-1 Adapter Boundary → MA-2 Migration Architecture → MA-3 Migration Decomposition → MA-4 Build Plan（BLOCKED）→ MA-4-R1 修復（HMAC authority trust establishment + durable-log idempotency dedup）→ Independent Review PASS → IMPLEMENTATION AUTHORIZED。R1 新增 41 tests（203 passed）。3 條 non-blocking hardening 列入 Phase 0 backlog（expires_at=None 拒絕 / e2e 改用 issue_hmac_context / durable nonce registry）。 | Mavis / Lin | DSH MA-4-R1 |
| 2026-08-23 (DSH P0-1) | Phase 0 Minimal Work Execution Adapter 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY FOR PHASE 0 GATE**。新檔 `src/work_adapter/`（bridge.py + execution.py）+ `dsh_adapter/soul-dsh-adapter.mjs`（mock TS）+ `tests/test_work_adapter.py`（27 tests）。230 tests 全綠。8/8 Hard Checks PASS。`git diff 26e1e49 -- src/work` 為空（Domain Core 零改動）、src/ 全樹零 DSH import、無 durable write bypass、No-DSH Survival 實測成立。**Phase 0 Gate → Phase 0 CLOSED**（commit `ece757b`）。 | Mavis / Lin | DSH P0-1 |
| 2026-08-23 (DSH P1-Preflight) | Phase 1 前置 hardening 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。M1 `HandoffStatus`→WorkState 語義（blocked/needs_input → state_transition，不照記產出）、M2 `result_type`↔capability anchor 驗證、M3 bridge error contract 統一（binary I/O + 主執行緒 decode）。239 tests 全綠（230 + 9 new）。8/8 Hard Checks PASS + bypass=NO。`src/work/` 僅 kernel.py 可動，其餘十模組零改動；零 DSH import 不變。commit `34e91d4`。 | Mavis / Lin | DSH P1-Preflight |
| 2026-08-23 (DSH P1 Decomposition) | P1 Execution Routing Decomposition（`docs/DSH-P1-EXECUTION-ROUTING.md`）→ Review #1 BLOCKED（2 項：A2 resume discriminator 未鎖 + A5 防火牆無 mechanism）→ 修正（7 處）→ Re-review → **READY FOR IMPLEMENTATION**。P1-A~P1-E 分解，鎖定 ExecutionShape capability-neutral + shape 由 Soul 推導 + adapter 只 translate。 | Mavis / Lin | DSH P1 Decomposition |
| 2026-08-23 (DSH P1-A) | Execution Target Contract（Domain Core 側）實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。`src/work/schema.py` 新增 `ExecutionShape` enum + `src/work/workflow.py` 新增 `derive_execution_shape`（continuous 不實作，待 P1-D）+ `execution.py` payload 新增 `execution_shape`。249 tests 全綠（239 + 10 new）。8/8 PASS + bypass=NO。零 DSH import 不變。 | Mavis / Lin | DSH P1-A |

**End of canonical state registry. Next update requires Owner authorization per §2.4 lifecycle.**
