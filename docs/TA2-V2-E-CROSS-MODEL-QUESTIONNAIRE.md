TA-2 v2-E — Cross-Model DESIGN AUDIT（固定問卷）

You are performing a DESIGN AUDIT, not a design brainstorm.
Your job is to find whether the design still contains holes that its authors have
not seen. You are NOT invited to redesign, extend, or improve the design.

## The artifact under review

`docs/TA2-V2-E-MEASUREMENT-DESIGN.md` — TA-2 v2-E Controlled Measurement Layer.

Read it in full before answering. Also read, for context only:
- `docs/TA2-V2-CLOSEOUT-A-V2-STOPPED.md` (why this design exists)

## Why it exists (background, not a question)

A previous measurement path (A-v2) used a rule-based "local temporal attachment"
extractor in Chinese. Two blind annotators agreed on 0 of 6 items that genuinely
required semantic judgment. The design under review exists to avoid requiring the
measurement layer to make that kind of judgment.

## Output format — use EXACTLY this, nothing else

E0: PASS / CONCERN / BLOCK
E1: PASS / CONCERN / BLOCK
E2: PASS / CONCERN / BLOCK
E3: PASS / CONCERN / BLOCK
E4: PASS / CONCERN / BLOCK
E5: PASS / CONCERN / BLOCK

Critical blockers:
1.
2.
3.

Non-blocking concerns:
1.
2.

Hidden assumptions:
1.
2.

Does the design require a new design decision?
YES / NO

If YES:
state only the decision question.
Do NOT propose the decision.

Threshold numbers proposed:
NONE

## Questions

### E0 — Construct validity
Does TCS actually measure "temporal context changes interpretation", or could it
instead be measuring temporal wording, lexical overlap, or the evaluator's own
sensitivity to time words? Note the design explicitly left its scoring scale
unfixed (§6). Is an unfixed scoring scale compatible with a valid construct, or
does E0 fail until the scale is chosen?

### E1 — Causal contrast
Are OFF / CORRECT / MISMATCHED / IRRELEVANT sufficient to separate a temporal
causal effect from a generic "any extra context makes the model think more"
effect? Does the DiD structure actually correspond to the causal contrast the
document claims, or is there a hidden confound?

### E2 — Evaluation independence (attack this hardest)
Attack each of these specifically:
- A/B randomization: can it be defeated in practice?
- Opaque labels: what actually prevents condition recovery?
- Is the evaluator genuinely blind, or formally blind?
- Can the evaluator infer the condition from the stimulus alone?
- Can the evaluator infer the condition from response style or lexical overlap?
- Is the evaluator silently performing another "temporal attachment classifier",
  just one implemented by an LLM?
- §8 says the evaluator sees a "Temporal relation target". Does that field leak
  the expected interpretation, and if so does blinding become merely formal?

If E2 is BLOCK, this design cannot proceed to implementation.

### E3 — Unit of analysis
Is `stimulus × temporal contrast pair` a defensible primary unit of analysis?
Keep distinct: replication, independent observation, pairwise trial, evaluator
decision. Is there a path by which a later analysis would again treat N LLM
calls as N independent samples?

### E4 — Measurement integrity
Attack: raw response preservation, evaluator output preservation, randomization
reproducibility, condition decoding, aggregation reproducibility, production
isolation, leakage, evaluator/generator separation. The question is not "will
results look good" but "if results look good, can we prove they are not a
measurement artifact".

### E5 — Threshold readiness
Are effect / noise / control quantities sufficiently defined that a threshold
could later be derived from evidence? **Answer only about readiness.**
You must NOT propose any threshold number. If you are inclined to name a number,
you have failed this section.

## Hard rules for you

- Do not propose any threshold number. Any number you emit here is out of scope.
- Do not propose rules, mechanisms, or features for the design.
- If the design needs a decision you cannot make, state the decision QUESTION
  only, and do not answer it.
- "PASS" means you found no blocking hole. It is not an endorsement.
- Disagreement with the authors' framing is expected and wanted.
  Do not be agreeable.
