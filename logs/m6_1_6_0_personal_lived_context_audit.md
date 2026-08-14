# M6.1-6.0 — Personal Lived Context Architecture Decision Audit — CLOSEOUT

**Mode**: READ-ONLY
**Date**: 2026-08-14 17:20 EDT
**Baseline**: HEAD = `9f8ece8` = origin/main
**Final**: HEAD = `9f8ece8` = origin/main (unchanged, READ-ONLY)
**Author**: Mavis (Lin)

---

## 0. Executive Verdict

# **DO NOT IMPLEMENT PERSONAL AT THIS TIME.**

**Whether Personal Lived Context should be implemented at all?** — **NO
(for now).**

Reasoning (evidence-based, see §1-§9):
1. **5 personal questions are nice-to-have, not critical** for Soul OS's
   primary purpose (companion + LLM context). None of Q1-Q5 are FULLY
   ANSWERABLE without new source (per M6.1-4 audit + this re-audit).
2. **No clear production use case**. M6.1-5 News (Information) just shipped.
   Calendar + Weather + News already provide objective Lived Context
   coverage. Personal adds subjective "Bry's day" tracking that hasn't
   been requested.
3. **Option C (dedicated source) is constrained by work order rules**:
   no wearable, no phone tracking, no browser, no GPS, no surveillance,
   no large infrastructure. The remaining "dedicated" options all reduce
   to manual input, which IS Option A.
4. **Option B (inference) has HIGH boundary risk**: inferring "Bry is
   working" from chat silence or absence of calendar events is
   surveillance-by-proxy. This violates Personal inference ≠ raw signal
   (per M3 design + M6.1-4 boundary rules).
5. **Option A (manual logging) is the minimum viable**, but requires
   Bry's active engagement. Without Bry's commitment to log, Option A
   produces no data either.
6. **Re-audit confirms M6.1-4 verdict**: PERSONAL REQUIRES NEW SIGNAL,
   and that signal is unavailable without violating work order rules.

**Recommended decision (per GOV-2 §2.8)**: **D — Defer Personal**.
Continue RUN-AND-COLLECT (Calendar + Weather + News accumulating). Revisit
Personal ONLY if Bry explicitly requests it (which would mean he commits
to manual logging under Option A).

---

## 1. Signal Inventory (8 signals, re-audited)

Per work order §2: Calendar, Telegram, Temporal, Memory, InnerLife,
WorldPerception, Weather, News. (M6.1-4 had 7 signals; M6.1-5 News is
new.)

### 1.1 Calendar (M5.15-6, LIVE)

| Property | Value |
|----------|-------|
| Source | Google Calendar via iCal/ICS public URL |
| Type | `calendar_event` |
| State QUALIFYING_TYPES | **YES** (in M5.9-2 whitelist) |
| WorldContext effect | ACCEPTED, score 0.575, always in top-3 |
| InnerLife effect | CREATES InnerLifeEvent (M5.4-5.5 bridge) |
| Diary trigger | DreamEvent, sometimes (per M5.2-H2) |
| Personal data provided | **Bry's scheduled meetings** (objective, future-looking) |
| Personal data NOT provided | actual activity at this moment, sleep, meal times, rhythm history |

**Current state** (perception_trace.jsonl):
- 15 events in trace (1 unique × 15 polls, since 8/13 21:18 EDT)
- 1 unique event: "Soul OS Calendar Test"
- Polling every 300s, lookahead 24h

### 1.2 Telegram (LIVE)

| Property | Value |
|----------|-------|
| Source | 10 Telegram bots (one per agent) |
| State | Channel router, not in WorldPerception |
| Personal data provided | **last_interaction_at**, **last_spoken_at** (per agent) |
| Personal data NOT provided | Bry's location, activity, schedule, who Bry is talking to (other than bots) |

**Current state** (from M6.1-4 audit, 2026-08-13 19:55 EDT):
- 10 agents with last_interaction_at ranging 2.9h-70h
- Most recent: agent_ruka (2.9h ago)
- Most stale: agent_rem (69h ago)
- This is AGENT-side, not BRY-side. It's "the agent last heard from Bry",
  not "Bry's personal activity".

**Personal inference possibility**:
- If Bry is chatting actively → he's awake + has time
- If Bry is silent for hours → possibly working/focused/sleeping
- But: chat silence ≠ activity (could be away from phone but working)
- **HIGH boundary risk**: inferring "working" from chat silence is surveillance-by-proxy

### 1.3 Temporal / Chrono-Social Engine (LIVE)

| Property | Value |
|----------|-------|
| Source | 7 public functions (current_time, deviation_interpretation, etc.) |
| Personal data provided | **current time of day**, **day of week**, **historical deviation** |
| Personal data NOT provided | what Bry is doing, Bry's sleep schedule, Bry's meal times |

**Capabilities**:
- Know it's 17:20 EDT (Friday)
- Can compute "is this Bry's typical activity time?" (against historical pattern)
- Can compute "vulnerability window" (silence > N hours)
- BUT: "Bry's typical activity time" baseline is learned from chat interaction
  (which is indirect, not direct activity)

### 1.4 Memory (v1, LIVE)

| Property | Value |
|----------|-------|
| Source | Inner Life + Diary + Event → Memory pipeline |
| Personal data provided | **long-term relationships, agent-persona interactions** (NOT Bry's activity) |
| Personal data NOT provided | Bry's daily schedule, Bry's activity patterns |

Memory is about AGENT ↔ Bry relationships + agent experiences, NOT Bry's
own activity history.

### 1.5 Inner Life (M5.4-5.1, LIVE)

| Property | Value |
|----------|-------|
| Source | Diary + Dream + Event producers |
| InnerLifeEvent | 1 entry (Calendar event, 2026-08-13 02:49:22) |
| Carryover state | 10 agents, varies |
| Personal data provided | **agent's narrative about Bry's day** (second-person, not first-person) |
| Personal data NOT provided | direct Bry activity |

**InnerLife is second-person** ("the agent experienced Bry's presence
today"), not first-person ("I did X today"). Useful for narrative
continuity but not authoritative for Personal.

### 1.6 WorldPerception (M3, LIVE)

| Property | Value |
|----------|-------|
| Source | 4 WorldEvent sources (synthetic, calendar, weather, news) |
| Personal data provided | **environmental context** (weather, news, calendar) |
| Personal data NOT provided | Bry's activity |

WorldPerception provides the SCENE Bry is in (weather, news, scheduled
meetings), not Bry's BEHAVIOR in that scene.

### 1.7 Weather (M6.1-3.1, LIVE)

| Property | Value |
|----------|-------|
| Source | Open-Meteo |
| Type | `rain_started`, `weather_temp_change` |
| State QUALIFYING_TYPES | NO (filtered by accept gate) |
| WorldContext effect | ACCEPTED (rain_started), REJECTED (weather_temp_change) |
| Personal data provided | **environmental conditions** (rain, temp) |
| Personal data NOT provided | Bry's activity |

Weather is environmental, not personal. Could inform "Bry is inside vs
outside" inference, but that's speculative.

### 1.8 News (M6.1-5.1, LIVE, M6.1-5.3 just audited)

| Property | Value |
|----------|-------|
| Source | RSS feeds (BBC + NPR per production config) |
| Type | `news_event` |
| State QUALIFYING_TYPES | NO (filtered by accept gate, score 0.345 < 0.35) |
| WorldContext effect | RARELY (gate filters default) |
| Personal data provided | **global events happening** (informational) |
| Personal data NOT provided | Bry's activity |

News is information context, not personal.

---

## 2. Re-evaluation of 5 Personal Questions

| Q | Question | Best inference (no new source) | Confidence | VERDICT |
|---|----------|----------------------------------|------------|---------|
| Q1 | Is Bry currently working? | Calendar has meeting → probably. No meeting + chat silent → maybe. No calendar + recent chat → maybe not. Telegram active recently → awake. | **LOW** | PARTIALLY (calendar + chat) |
| Q2 | Is Bry close to dinner time? | Temporal knows current time. But no meal-time baseline. | **LOW** | PARTIALLY (time of day only) |
| Q3 | Is Bry's rhythm unusual? | Compare current chat activity vs historical (Temporal). But historical baseline is chat-derived, not activity-derived. | **LOW** | NOT (no activity baseline) |
| Q4 | Is Bry sleeping later recently? | Compare chat time-of-day distribution. But chat ≠ bedtime. | **LOW** | NOT (no bedtime data) |
| Q5 | What personal activity state? | No signal. | **0** | NOT (no data) |

**0/5 FULLY ANSWERABLE, 2/5 PARTIALLY, 3/5 NOT.**

This matches M6.1-4 verdict. M6.1-5 (News) did NOT change Personal capability
because News is INFORMATION, not PERSONAL.

---

## 3. Boundary Analysis

### 3.1 Per M6.1-4 + M3 design rules

```
Personal inference ≠ raw signal
Historical memory ≠ current personal state
Calendar commitment ≠ actual activity
Chat silence ≠ "working / sleeping / etc."
```

These rules PREVENT Option B (inference) from being reliable. Inference
is by definition a guess, not a measurement.

### 3.2 Why Option B is risky

| Signal | Looks like | Actually means | Risk |
|--------|------------|------------------|------|
| Telegram silent 3h | "Bry is working/focused" | "Bry is at lunch / in meeting / phone dead / etc." | Wrong inference → LLM says "你今天工作辛苦了" when Bry is on vacation |
| Calendar empty | "Bry is free" | "Bry is doing deep work / sick / on vacation" | Same |
| Chat at 3am | "Bry is night owl" | "Bry is one-time-up for a project" | Wrong baseline |
| Chat silent on weekend | "Bry is resting" | "Bry is at family event" | Same |

**Inference is brittle**. Each individual signal is weak evidence; combination can still be wrong.

### 3.3 What "minimum signal" would look like

The minimum signal that could answer Q1-Q5 reliably is a direct activity
log: "I am [working/resting/eating/sleeping/traveling]" entered by Bry
himself.

**This is Option A (manual logging)**.

No other option can produce reliable Personal answers without:
- Surveillance (forbidden by work order)
- Wearable (forbidden)
- Phone tracking (forbidden)
- Browser/GPS (forbidden)

So the only legitimate way to get Personal data is Bry explicitly telling the system.

---

## 4. Privacy Analysis

### 4.1 Option A (Manual logging)

| Property | Value |
|----------|-------|
| Data type | Bry's self-reported activity |
| Data location | `data/personal/log.jsonl` (proposed) |
| Who writes | Bry |
| Who reads | Soul OS agents (for LLM context) |
| Privacy | **High (Bry controls the data)** |
| Production safety | Low (no automatic writes, no surveillance) |
| Reproducibility | High (Bry can review/edit) |
| Observability | High (Bry sees what's in state) |

**Verdict**: Privacy-friendly. Bry controls what goes in.

### 4.2 Option B (Inference)

| Property | Value |
|----------|-------|
| Data type | Derived from other signals (chat, calendar) |
| Data location | None (computed at query time) |
| Who writes | System (implicit inference) |
| Who reads | Soul OS agents |
| Privacy | **LOW (Bry may not realize system is inferring personal state)** |
| Production safety | MEDIUM (inference can be wrong, embarrassing) |
| Reproducibility | LOW (inference is non-deterministic) |
| Observability | LOW (Bry can't see what was inferred) |

**Verdict**: Privacy-concerning. Bry should be told when system is making
personal inferences. Per work order, this is borderline surveillance.

### 4.3 Option C (Dedicated signal source)

| Property | Value |
|----------|-------|
| Data type | Whatever the source provides |
| Data location | `data/personal/<source>.jsonl` (proposed) |
| Who writes | External system or Bry |
| Who reads | Soul OS agents |
| Privacy | **DEPENDS on source** |
| Production safety | DEPENDS |
| Reproducibility | **DEPENDS** |

Per work order rules:
- ❌ wearable integration (high privacy risk)
- ❌ phone tracking (high privacy risk)
- ❌ browser tracking (high privacy risk)
- ❌ GPS tracking (high privacy risk)
- ❌ automatic surveillance (high privacy risk)
- ❌ large infrastructure (operational risk)

What survives:
- ✅ Bry-controlled manual input (== Option A)
- ✅ Read-only public APIs (e.g. Bry's published blog, public task list)
- ✅ Calendar integration (already in M5.15-6)

So **Option C reduces to Option A** within work order constraints. The
only difference would be "more sophisticated UI for manual input" (e.g.
mobile app, voice input), but that's "large infrastructure" by Bry's
definition.

**Verdict**: Within work order constraints, Option C is essentially Option A
with a fancier UI. No privacy benefit, more infrastructure cost.

---

## 5. Three Options Comparison

### 5.1 Option A: Manual activity logging

**What**: Bry tells the system what he's doing via simple commands or file
edits. Examples:
- `/status working` (chat command)
- Write to `~/.soul-os-status` (file watcher)
- Web UI button (if built)

**Cost**: Low. One new event type, one new source, one new LLM context
field. ~50-100 lines of code.

**Value**: HIGH (if Bry uses it). Direct, accurate, first-person data.

**Friction**: HIGH (Bry has to remember to log). If Bry forgets or stops
logging, data flow stops.

**Bry's commitment**: Required. Without Bry actively logging, capability = 0.

### 5.2 Option B: Inference from existing signals

**What**: System computes Personal answers from chat/calendar/temporal
patterns. No new data source.

**Cost**: Low. No new code needed. Existing signals used.

**Value**: LOW. Inference is brittle. Wrong inferences could be
embarrassing (e.g. LLM says "good morning, ready for work?" when Bry
is sick).

**Friction**: None for Bry. System does it all.

**Boundary risk**: HIGH. Per M3 design rules, Personal inference ≠ raw
signal. Bry may not realize system is making personal inferences.

**Verdict**: NOT recommended (privacy + correctness concerns).

### 5.3 Option C: Dedicated Personal signal source (within work order rules)

**What**: Some specific new source for Personal data. Within work order
constraints (no wearable, no phone, no browser, no GPS, no surveillance,
no large infrastructure), this reduces to:
- Bry-controlled manual input (== Option A with fancier UI)
- Public APIs Bry exposes (e.g. published calendar, public task list)

**Cost**: MEDIUM-HIGH. New code, new source, possibly new abstraction.

**Value**: Same as Option A (since it reduces to A within constraints).

**Friction**: Same as Option A.

**Verdict**: **No advantage over Option A** within work order rules.

---

## 6. Recommended Architecture

### 6.1 Primary recommendation: DEFER (Option D from M6.1-4)

**DO NOT implement Personal capability at this time.**

Reasons:
1. **No production use case**: News (M6.1-5) just shipped, focus on
   Information Lived Context first
2. **No Bry commitment**: Without Bry actively engaging, no option works
3. **M6.1-5.3 + M6.1-5.2 + M6.1-5.1 all need follow-up**: News is fresh,
   requires RUN-AND-COLLECT to validate
4. **Personal adds friction without clear benefit**: Each option has
   tradeoffs, no clear winner

### 6.2 If Bry wants Personal in future: Option A (Manual logging)

When Bry is ready:
- Add `data/personal/log.jsonl` (append-only, Bry-owned)
- Add new WorldEvent source: `ManualActivitySource`
- Add new event type: `personal_activity` (NOT in WORLD_QUALIFYING_TYPES,
  so no InnerLife pollution)
- Wire to LLM context as optional `personal_context` block
- Add `.env` config: `SOULOS_PERSONAL_LOG_PATH`
- Bry-controlled: Bry decides when to log, what to log, when to delete

This is **Option A scaled up** with proper LLM context integration.

### 6.3 If Bry wants minimal Personal NOW: hybrid A+B

A small "status" command in Telegram that Bry can use:
- `/status working` → append to `data/personal/log.jsonl`
- `/status` (no arg) → query current status
- LLM can see Bry's status when relevant

This is essentially Option A with chat-based UI. ~30 lines of code.

But: Bry must commit to using it. Without usage, no data.

---

## 7. Decision Criteria for Bry

| Criterion | A (Manual) | B (Inference) | C (Dedicated) | D (Defer) |
|-----------|-------------|----------------|----------------|-----------|
| Correctness | HIGH (if Bry logs accurately) | LOW (inference brittle) | HIGH (if explicit) | N/A |
| Privacy | HIGH (Bry controls) | LOW (system infers) | DEPENDS | HIGH (no data) |
| Observability | HIGH (Bry sees) | LOW (Bry doesn't see) | MEDIUM | HIGH (no data) |
| Reproducibility | HIGH (deterministic) | LOW (depends on patterns) | HIGH | N/A |
| Production safety | HIGH (no auto writes) | MEDIUM (auto inference) | MEDIUM | HIGH (no change) |
| Bry's commitment | REQUIRED (must log) | NONE | REQUIRED | NONE |
| Implementation cost | LOW (~50 LOC) | ZERO | MEDIUM (~150 LOC) | ZERO |
| Production use case | LOW (no demand) | LOW (no accuracy) | LOW (no demand) | HIGH (already RUN-AND-COLLECT) |
| **Total score** | **MEDIUM** | **LOW** | **MEDIUM** | **HIGH** |

**D (Defer) wins on aggregate**. A is the best option IF Bry commits. B and
C are not recommended.

---

## 8. Whether Personal Lived Context Should Be Implemented at All

# **NO, not at this time.**

### 8.1 Evidence FOR implementation

- Personal answers would feel more "alive" if Q1-Q5 were answerable
- Bry has expressed interest in M6.1-4 (audit ticket)
- News (M6.1-5) shows Soul OS is heading toward richer Lived Context

### 8.2 Evidence AGAINST implementation

- **No production use case yet**: Bry hasn't asked "今天有什麼新聞?" or
  "我今天工作辛不辛苦?" — the questions Personal would answer
- **News (M6.1-5) is fresh**: requires RUN-AND-COLLECT to validate, no
  bandwidth for new capability
- **Each option has significant tradeoffs**:
  - A requires Bry's daily commitment
  - B has privacy/correctness concerns
  - C is constrained to A by work order rules
- **3 out of 5 questions (Q3, Q4, Q5) are nice-to-have, not critical**:
  - Q3 (rhythm unusual): could be answered eventually via Memory
  - Q4 (sleeping later): requires activity data, not in any current signal
  - Q5 (activity state): requires activity data, not in any current signal
- **Q1, Q2 partially answerable now** (calendar + time of day). The
  current partial answers are sufficient for most use cases

### 8.3 If Bry insists: minimum viable = Option A

When Bry is ready, Option A is the minimum viable. Implementation:
1. New file: `data/personal/log.jsonl` (Bry-owned, append-only)
2. New source: `ManualActivitySource` reads the log on each poll
3. New event type: `personal_activity` (NOT in WORLD_QUALIFYING_TYPES)
4. New env var: `SOULOS_PERSONAL_LOG_PATH` (default disabled)
5. LLM context: optional `personal_context` block (only if recent entry)
6. ~50-100 lines of code, no frozen contract changes

But: this is still READ-ONLY for now. Implementation is a separate ticket.

---

## 9. Production Integrity (READ-ONLY)

| File | Status |
|------|--------|
| All production data | UNCHANGED |
| HEAD = origin/main = `9f8ece8` | EQUAL |
| Modified files | 0 |
| Staged files | 0 |
| Untracked | 20 (baseline preserved) |
| Commits | 0 |
| Pushes | 0 |

**0 audit-caused mutation. 0 source changes. READ-ONLY respected.**

---

## 10. Architectural Findings

### 10.1 Personal inference ≠ raw signal (frozen rule)

The M3 design has multiple boundary rules:
- Personal inference ≠ raw signal
- Historical memory ≠ current personal state
- Calendar commitment ≠ actual activity
- Chat silence ≠ "working / sleeping / etc."

These rules exist for **correctness AND privacy**:
- Correctness: inference is wrong sometimes, better to admit "don't know"
- Privacy: implicit inference can be surveillance-by-proxy

Bry's M6.1-4 work order explicitly invoked these rules. They remain
in force for M6.1-6.0.

### 10.2 What "Personal" means in Soul OS context

In Soul OS, "Personal" is the AGENT's perspective on Bry, not Bry's
self-report:
- Diary: agent writes "今天 Bry 看起來很累" (third-person narrative)
- Carryover: agent's residual state from last interaction
- Chrono-Social: agent's awareness of Bry's rhythm (from chat pattern)

None of these are FIRST-PERSON Personal data. They're all SECOND-PERSON
or THIRD-PERSON inferences.

For FIRST-PERSON Personal data, the only way is for Bry to tell the
system directly. That's Option A.

### 10.3 RUN-AND-COLLECT pattern (current value)

Soul OS already has 3 RUN-AND-COLLECT sources:
- Calendar (M5.15-6): 1 unique event × 15 polls = 15 trace entries
- Weather (M6.1-3.1): 1 per poll × ~7.5h = 15+ events
- News (M6.1-5): 19 events over 15 polls (1.27/poll mean)

These are the **3 active Lived Context signals**. They give the LLM:
- "今天是 [weather] [calendar event] 的日子" (calendar + weather)
- "現在有 [news] 發生" (news, if user asks)

Personal would add: "Bry 在 [activity]" — but this requires Bry's
explicit input, which no current signal provides.

### 10.4 The "no production use case" criterion

Soul OS has been running for ~3 weeks (since M5.15-6 Calendar shipped).
During that time:
- Calendar: 1 unique event (a test)
- Weather: working
- News: just activated (M6.1-5.2)
- Personal: not requested by any agent, any user, or any test

This is **strong evidence** that Personal is not currently needed. The
system works without it. Adding it would add complexity without clear
benefit.

---

## 11. Stop Conditions Check (per work order)

- [ ] Personal capability requires changing a frozen contract: **NO** (no Personal capability = no contract change)
- [ ] Production data would need modification: **NO**
- [ ] Large infrastructure would be required: **NO**

**No STOP conditions triggered.** Audit completed in READ-ONLY mode.

---

## 12. Final Classification

# **PERSONAL CAPABILITY: DEFER (D)**

- [x] Existing signal inventory completed (8 signals)
- [x] Minimal signal source identified (Option A if ever needed)
- [x] Boundary violations documented (Option B = surveillance-by-proxy)
- [x] Privacy risks documented (Option A = HIGH, B = LOW, C = DEPENDS)
- [x] Production unchanged (READ-ONLY)
- [x] No frozen contracts modified (no implementation)
- [x] No source changes (0 modified)
- [x] No commits, no pushes (HEAD == origin/main == 9f8ece8)

### 12.1 Direct answer to work order question

> "Whether Personal Lived Context should be implemented at all?"

# **NO — DEFER (D).**

Reasoning:
1. No production use case (3 weeks of running, no Personal demand)
2. Each option has significant tradeoffs
3. RUN-AND-COLLECT (Calendar + Weather + News) is the current focus
4. Q1-Q2 partially answerable now (sufficient for most use cases)
5. Q3-Q5 are nice-to-have, not critical
6. If Bry wants Personal later, Option A is the minimum viable

### 12.2 Recommended next ticket

Per GOV-2 §2.8 (Owner decision required):

- **D — Defer** (my recommendation) — no work, continue RUN-AND-COLLECT
- **A — Manual log implementation** (if Bry commits to logging) — separate
  implementation ticket
- **B — Inference** (NOT recommended, boundary concerns)
- **C — Dedicated source** (reduces to A within work order constraints)

If Bry chooses D (defer), the next ticket would be:
- **M6.1-7.0 — News RUN-AND-COLLECT analysis** (validate News in production
  over 1-2 weeks, then decide next direction)

Or if Bry wants to explore other directions:
- M6.1-7.x — Lived Context depth analysis (e.g. focus on Calendar/Weather
  data quality, add more feeds, etc.)

---

## 13. Files

- `C:\Users\bbfcc\gov_1_temp\m6_1_4_closeout.md` (prior Personal audit, referenced)
- `C:\Users\bbfcc\gov_1_temp\m6_1_5_3_final_closeout.md` (recent News lookback audit, referenced)
- `C:\Users\bbfcc\gov_1_temp\m6_1_5_2_closeout.md` (recent News activation, referenced)
- `C:\Users\bbfcc\gov_1_temp\m6_1_6_0_closeout.md` (this file)
