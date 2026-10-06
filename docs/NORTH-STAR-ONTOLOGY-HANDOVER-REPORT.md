# North Star Ontology — Handover Report

> **Purpose.** Self-contained briefing for DSH on the state of the Soul OS
> causal-ontology program after twenty Discussion Convergence Gates.
>
> **This document authorizes nothing.** It reports what has been decided, what
> remains open, and the rules that any successor must not violate.
> No implementation, prompt, threshold, evaluator, runtime or architecture change
> is authorized by this file or by anything it summarises.

- **Repository:** `soul-os-harness`, branch `main`
- **HEAD at time of writing:** `3829521`
- **DCG series span:** `0ca15f9` (DCG #1) → `3829521`
- **Canonical artifacts:** 20 DCG documents + 2 governance documents, all under `docs/`
- **Language convention:** English decision statements, Chinese section headers and
  context. Both are canonical; neither is a translation of the other.

---

## 1. What this program is

Twenty consecutive rounds of the Discussion Convergence Gate (DCG), a
research-discussion protocol that converges within at most five rounds and must
leave four deliverables each time (Conclusions / Open Questions / Decision-Next
Step / Non-Claims).

**The program produced two things, not one:**

1. A causal ontology for Soul OS — six constructs, six relations, a layering,
   and admission rules.
2. **A method that reduces natural-language action vocabulary to causal
   structure**, together with a demonstrated limit on that method's completeness.

**The second is the more important artifact.** The words *agency*, *motive*,
*decision*, *expression*, *commitment*, *self-binding*, *temporal constraint* were
each put through the method and each decomposed into existing causal structure
plus shape properties, with no independent construct remaining.

---

## 2. The frozen ontology

Canonical layering, from `docs/NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` §6:

```
GOVERNANCE / MODELING LAYER
    Ontology Privilege Policy: Gate-only / Admissibility Closure
    ├─ Admission: ISD + CEG
    ├─ Second-layer privilege rule: none
    ├─ Review Trigger: a new refinement/candidate passes both ISD and CEG
    └─ Change: Owner; re-evaluate all placements within declared scope
    (policy full text: docs/ONTOLOGY-PRIVILEGE-POLICY.md)

—— 以上為 governance / modeling layer，不屬於 causal ontology ——

QUESTION
├─ Soul
│  └─ QPF = 1（#19 §5.1；governance parameter 已宣告）
└─ Lived Experience
   └─ QPF = 1（#19 §30.2；governance parameter 已宣告 ⇒ Governable）

CONSTRUCTS
    Agent / World / Memory / Growth / Free Growth / Interaction

STRUCTURAL PROPERTIES
    Branching / Set-valued Transition

RELATIONS
    Temporal Identity
    Event ↝ Agent
    External Exposure
    Agent–Capability
    Agent–World
    Agent ↔ Agent

VOCABULARY
    ├─ Awareness    placement source: DCG #8（descriptive family）
    │                parameter: Agent–X relation expansion（#8 §5）
    │                parameter 已完整宣告 ⇒ fully formalized（§6.8）
    └─ History      placement source: DCG #9（relation-derived set）
                     QPF = 0（#19 §30.1）
                     admission rule: OPEN（privilege 非結構決定）

UNPLACED / OPEN（QPF 已測；無 declared placement rationale）
    Agency / Motive / Decision / Expression
    Commitment / Self-Binding / Temporal Constraint
    （QPF≠0 不構成 vocabulary rejection；見 DCG #19 §25）

DOCUMENTED GAPS
    Architecture-seeded, non-past-derived causal state (Horn D)
    └─ construct: not established ｜ relation: not established

DOCUMENTED CONVENTION CANDIDATES
    𝒯_A invariance convention      (frozen, DCG #15)
    Temporal Semantics / Time Base  (candidate, DCG #16)
```

### 2.1 The six constructs

| Construct | Substance |
|---|---|
| **Agent** | Defined by instantiation plus a logical-instantiation-lineage; the transition relation is 𝒯_A |
| **World** | Causally continuing process whose transition dynamics are **not** reducible to any Agent trajectory |
| **Memory** | `PastDirected(R,E) ∧ CausallyAfforded(R,A)` — a *retained representation* playing a causal-functional role in 𝒯_A |
| **Growth** | Revision of an existing past representation without new external input, causally participating in future mapping |
| **Free Growth** | Growth plus content-grounding: past representation content causally influences revision formation |
| **Interaction** | Reciprocal causal coupling between separately instantiated Agents |

### 2.2 All five are architecture-relative

Every construct depends on architecture-defined quantities. **Persistence is
always persistence under the chosen lineage semantics**, never an
architecture-independent truth. This is a discipline, not an apology.

---

## 3. The frozen rules

### 3.1 ISD Gate — Independent Structural Distinction

> A candidate earns construct status only when it introduces an **independently
> specifiable structural distinction** that is **not determined by** the existing
> construct/relation dimensions and therefore **partitions otherwise equivalent
> causal systems** into different classes.

Frozen at `b87b3f1`. Note: it does **not** mean "no new causal edge"; DCG #11
proved construct admission never required one (Memory added no edge, only a
new operand).

### 3.2 CEG Gate — Causal Extension

> A candidate passes CEG only if its distinguishing condition is **not fully
> determined by the extensional structure of an already-admitted relation**, but
> depends on an **independently specifiable structural operand or process
> constraint that has its own causal-functional role**.

Frozen at `8265745`, wording corrected in `db82c1b`. Written as *functional
determination* rather than variable syntax, because a criterion that depends on
how someone decomposes the system is a description, not a structure.

**Merged package-statistic counterexamples:** even-successor-count and a parity
flag stored as a state component are both fully determined by the image of an
admitted relation, so neither passes CEG.

### 3.3 Gate-only / Admissibility Closure

> `ISD + CEG ⇒ causally legitimate ⇒ retain`. **No second-layer coarsening or
> privilege principle is applied.**

Frozen at `3eb4bff`. Formally worded as **the current** policy, never as a
permanent metaphysical claim. Its single parameter is the **Review Trigger**: a
new refinement or candidate passing both ISD and CEG opens an Owner-level review
of whether Gate-only remains. **A gate pass does not automatically switch to a
second layer.**

### 3.4 Governance invariants

Frozen at `8e172e5`, in `docs/ONTOLOGY-PRIVILEGE-POLICY.md` §2–§4:

```
1. Undeclared parameter  ⇒  not a frozen rule
2. Any frozen principle / policy / parameter must declare
     Parameter + Semantics + Scope + Change Procedure
3. Δ Principle ∨ Δ Parameter  ⇒  re-evaluate all placements
                                  within declared scope
4. A governance document is subject to the same rules and must
   self-declare
```

**Scope rule (§3):** every frozen parameter declares its scope at freeze time;
an undeclared parameter scope defaults to all placements. The executor may not
retroactively interpret "affected". *Reason, recorded in the file:* if scope could
be judged case by case, "re-evaluate affected placements" would be read as
"re-evaluate only what someone thought to re-run" — exactly the silent narrowing
invariant 1 exists to forbid.

**Two parallel institutions, deliberately not merged:**

| | Construct placement | Vocabulary placement |
|---|---|---|
| Regime | ISD + CEG + Gate-only | per-kind substantive rules + governance process |
| Can it exclude? | **Yes** | **No — it can only require documentation** |
| Declared scope | `all construct-placement decisions after adoption` | `all frozen vocabulary-placement decisions` |

**Gate-only's scope stays construct-only.** Extending it to vocabulary would
quietly turn the construct admission gate into a vocabulary admission gate,
directly contradicting DCG #20.

### 3.5 Rename Test — question-preserving force

> `QPF(w)=1` iff, after removing `w`: the original research question still
> exists, the existing tracked vocabulary cannot fully carry it, **no other
> tracked item already carries that same question**, and no nameless equivalent
> substitute may be introduced.

**Tracked includes filed open candidates, documented gaps and convention
candidates — not only admitted vocabulary.** The purpose is to prevent a question
from being *lost*, not to prevent it from being *unadmitted*.

**Self-carrier exclusion (§3.1):** `T ≠ w`, and `T` must be an independent tracked
item — not `w`'s own placement rationale, decision record or self-referential
open-question note. Otherwise a candidate proposes a question and then uses it as
its own carrier, which is self-reference, not tracking.

---

## 4. What the twenty rounds established

### 4.1 The terminal result

> **Causal structure determines describability, not vocabulary privilege.**

Under the current formalization, vocabulary membership is not represented as a
predicate over the causal state space, so the admitted causal structure provides
no input from which a unique vocabulary privilege can be entailed.

Every candidate criterion failed for the *same* reason — the criterion's verdict
depended on a modeling choice it was meant to adjudicate:

| Candidate | Failure |
|---|---|
| ISD | Any shape-predicate passes |
| CEG | Vulnerable to packaged statistics until rewritten functionally |
| IDC | No bridge from causal distinction to construct privilege |
| Invariance | `Aut(C)` depends on `C`'s granularity — circular |
| MDL | Needs a code class; shorter ≠ privileged |
| Predictive Adequacy | Needs target / task / loss |

### 4.2 Internal closure is not ontology completeness

> `Internal closure ≠ Ontology completeness`

Two readings remain and are **not distinguishable from inside the framework**:
the ontology is genuinely complete, or its boundaries are too coarse and the
apparent completeness is an artifact of categories being too broad. **No criterion
has been identified that distinguishes them, and identifying one would require a
criterion not derived from the ontology itself.**

### 4.3 The program produced a positive finding alongside the negative one

- **Six constructs**, admitted through a dual gate that excludes packaged statistics.
- **A canonical causal quotient** `C*`: states indistinguishable under all
  interventions, unique up to isomorphism, conditioned on fixed intervention
  semantics.
- **Two named questions that no existing vocabulary can carry** (Soul, Lived
  Experience) — the only terms with question-preserving force.
- **A documented categorical gap**: architecture-seeded, non-past-derived causal
  state has no construct and no relation, yet is causally operative.

---

## 5. Governance status per placement

| Item | Layer | Governance parameter | Status |
|---|---|---|---|
| Six constructs | CONSTRUCTS | ISD + CEG + Gate-only | Governable / parameterized |
| Soul | QUESTION | QPF | Governable / parameterized |
| Lived Experience | QUESTION | QPF | Governable / parameterized |
| Awareness | VOCABULARY | Agent–X relation expansion; `testable` borrows #9 §6 operational sense | **Fully formalized** |
| **History** | VOCABULARY | **none** | **Placement retained; admission rule NOT ESTABLISHED** |
| **Branching** | STRUCTURAL PROPERTIES | **none** | **Placement retained; no declared placement basis** |
| **Six relations** | RELATIONS | **none** | **No relation-admission regime exists** |
| Seven UNPLACED/OPEN terms | — | none | Construct rejected; vocabulary OPEN |

**History is the only admitted vocabulary item with no declared governance of any
kind.** Branching and the six relations share its condition: placed without a
declared admission basis. *The three categories of problem are still distinct* —
History's is a placement-principle question, Branching's begins as a scope
question, the relations' is an unmapped regime.

---

## 6. Open items, in the order the Owner set

### Priority 1 — Existing placement legitimacy (proposed as DCG #21)

Whether a canonical placement that never had an explicit admission event needs
**retrospective justification**, or whether vocabulary operates under **default
continuity** with a declared removal mechanism.

**History cannot be asked for an admission criterion until this is settled**, because
asking presupposes retrospective justification.

Note that A/B fixes only one axis. Whether the burden is **symmetric** between
admission and removal is a second, unfilled axis; its unfilled corner is a trap in
which nothing is ever settled.

### Priority 2 — Branching's placement basis and Rename Test applicability

Keep OPEN. There is no scope declaration extending the Rename Test to
STRUCTURAL PROPERTIES, so it should not be applied there yet. Whether
Structural Properties enjoys the same membership regime as Vocabulary is itself
part of what this question should discover.

### Priority 3 — Not started

- Temporal Semantics promotion from convention candidate to convention.
- A completeness / granularity criterion that does not take the existing ontology
  as its own premise.

---

## 7. Rules a successor must not violate

These are frozen and repeatedly enforced during the series. Violating any of them
invalidates downstream work.

1. **Gate failure is not another gate's pass.** A candidate failing ISD does not
   thereby qualify for vocabulary; and because no vocabulary admission gate is
   declared, **absence of a gate means no admission, not silent admission**.
2. **A declaration cannot be inferred from a later decision.** Candidate cannot be
   copied by analogy from any single canonical case's rationale.
3. **A distinction whose truth value depends on how the system is described is a
   property of the description.** Recoverability, storage location, encoding
   choice, and state granularity have each been excluded on this basis.
4. **A name never inherits the semantics of the concept it was coined for.**
   *Essence / Nature / Ground / Soul Seed / Free Will* were all rejected as candidate
   names for this reason.
5. **`identified` is not `proven impossible`.** No candidate criterion was proved
   nonexistent; they were shown to lack a witness under current formalization.
6. **Never declare nonexistence before enumerating known candidates.** This error
   occurred twice in the series — once by rigorizing a criterion without checking
   the rigorization, and once by ruling that no universal property existed while the
   answer (`C*`) had been proposed two rounds earlier.
7. **A criterion that can be gamed by how one decomposes the system is syntax, not
   structure.**
8. **A gate may not admit its own object.** The Step-3 carrier must be independent
   of the candidate being tested.
9. **`QPF = 0` is not a vocabulary veto, and construct rejection is not a
   vocabulary approval.**
10. **Edits must be verified against the file's actual contents.** A line-range
    splice in this series silently deleted three layers of the canonical layering
    table and shipped across four commits before being caught. See §8.

---

## 8. Known defect in the artifact chain, now repaired

At commit `2cfb6e5` the canonical layering table lost **CONSTRUCTS**,
**STRUCTURAL PROPERTIES** and **RELATIONS** — an editing error by the main brain,
not a research decision. It shipped across four commits and was caught while
preparing this handover.

Restored verbatim from `d756157` in commit `3829521`, with a correction note in
section 10 of the affected file. **If you rely on any layering table read between
`2cfb6e5` and `c072a55`, re-read it at `3829521` or later.**

---

## 9. File index

### Governance (read these two first)

| File | Lines | Contents |
|---|---|---|
| `docs/ONTOLOGY-PRIVILEGE-POLICY.md` | 437 | Gate-only policy; four governance invariants; scope rule; self-declaration; vocabulary governance scope; per-term declarations §6.7 / §6.8 |
| `docs/DISCUSSION-CONVERGENCE-GATE.md` | 102 | The DCG protocol itself: round limit, four deliverables, "a DCG authorizes no construction" |

### Master layering

| File | Contents |
|---|---|
| `docs/NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` | §6 canonical layering (**read this for current state**) |

### DCG series, in order

| # | File |
|---|---|
| 1 | `NORTH-STAR-BOOTSTRAP-FREE-GROWTH-DCG-CONVERGENCE.md` |
| 2 | `NORTH-STAR-SOULNESS-BOUNDARY-DCG-CONVERGENCE.md` |
| 3 | `NORTH-STAR-SOUL-ONTOLOGY-DCG-CONVERGENCE.md` |
| 4 | `NORTH-STAR-GROWTH-ADAPTATION-DCG-CONVERGENCE.md` |
| 5 | `NORTH-STAR-FREE-GROWTH-DCG-CONVERGENCE.md` |
| 6 | `NORTH-STAR-MULTIPLE-SOULS-SHARED-WORLD-DCG-CONVERGENCE.md` |
| 7 | `NORTH-STAR-SHARED-WORLD-DCG-CONVERGENCE.md` |
| 8 | `NORTH-STAR-AWARENESS-BOUNDARY-DCG-CONVERGENCE.md` |
| 9 | `NORTH-STAR-LIVED-EXPERIENCE-DCG-CONVERGENCE.md` |
| 10 | `NORTH-STAR-IDENTITY-CONTINUITY-DCG-CONVERGENCE.md` |
| 11 | `NORTH-STAR-MEMORY-RETENTION-BOUNDARY-DCG-CONVERGENCE.md` |
| 12 | `NORTH-STAR-AGENCY-ACTION-BOUNDARY-DCG-CONVERGENCE.md` |
| 13 | `NORTH-STAR-BRANCHING-SET-VALUED-TRANSITION-DCG-CONVERGENCE.md` |
| 14 | `NORTH-STAR-MOTIVE-DECISION-BOUNDARY-DCG-CONVERGENCE.md` |
| 15 | `NORTH-STAR-EXPRESSION-COMMUNICATION-BOUNDARY-DCG-CONVERGENCE.md` |
| 16 | `NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` |
| 17 | `NORTH-STAR-ONTOLOGY-COMPLETENESS-GRANULARITY-DCG-CONVERGENCE.md` |
| 18 | `NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md` |
| 19 | `NORTH-STAR-RENAME-TEST-DCG-CONVERGENCE.md` |
| 20 | `NORTH-STAR-VOCABULARY-ADMISSION-DCG-CONVERGENCE.md` |

**Where the most load-bearing reasoning lives:** DCG #11 §2 (Memory's definition and
the recoverability exclusion), DCG #13 §2 (CEG's wording), DCG #14 §2 (the Decision
reduction), DCG #19 §3.1 and §30 (self-carrier exclusion; second application batch),
DCG #20 §2 (input-domain result).

---

## 10. Relationship to the DSH contracts

The natural interface is `docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`, whose frozen
boundary statement is:

> **Soul OS owns the durable work truth. DSH owns ephemeral execution.**
> **DSH orchestration ≠ Soul orchestration.**

**This program sits entirely on the Soul side and touches none of the DSH
execution contracts.** Its output is a vocabulary and a set of governance rules
for reasoning about agents, not an execution protocol.

**Practical consequences for DSH:**

- Nothing in this program defines a DSH work item, artifact, or job.
- If DSH needs to *emit* any of these terms (construct names, vocabulary
  distinctions, `Memory` / `Growth` / `Branching` classifications) into a work
  contract, that is a **new construction decision** and requires its own
  authorization. This report authorizes none.
- The distinction between `Event ↝ Agent` and `Memory` is worth respecting if DSH
  ever reasons about agent state: **an event belonging to an agent's past is not
  the same thing as a retained representation that the agent's transition machinery
  can use.**
- If DSH needs to record an agent's own recollection, `Agent` in this program is
  architecture-relative, and persistence is only meaningful under a declared
  lineage semantics.

---

## 11. Change control

| Change type | Authority |
|---|---|
| Ontology admission or removal | Owner, through a DCG |
| Privilege policy or its parameters | Owner, per `ONTOLOGY-PRIVILEGE-POLICY.md` §6.4 |
| Governance invariants or their scopes | Owner, per §8 |
| Implementation, runtime, thresholds, evaluators | **Not authorized by this program** |

Any change to a declared parameter re-evaluates all placements within its declared
scope. Scope may not be narrowed after the fact.