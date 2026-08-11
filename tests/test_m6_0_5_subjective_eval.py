"""
tests/test_m6_0_5_subjective_eval.py
M6.0-5 (Bry 派工 2026-08-11 19:28): Subjective LLM Quality Evaluation tests.

16 test categories (per Bry spec):
  1. Evidence serialization
  2. Rubric validation
  3. Judge result validation
  4. Three independent judges
  5. No judge cross-contamination
  6. Median aggregation
  7. Agreement calculation
  8. High-disagreement calibration trigger
  9. Low-disagreement automatic result
 10. Calibration queue generation
 11. Deterministic precedence
 12. Production isolation
 13. Malformed judge output handling
 14. Missing dimension handling
 15. Invalid score handling
 16. Reproducibility

Mock/fake judges are deterministic, network-free, no real LLM calls.
All tests run in < 1s with pytest.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    RUBRIC_ANCHORS,
    RUBRIC_VERSION,
    DimensionName,
    validate_score,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    evidence_to_dict,
    evidence_from_dict,
    Judge,
    JudgeResult,
    FixedScoreJudge,
    ScriptedJudge,
    HighAgreementJudge,
    HighDisagreementJudge,
    SequentialJudgeRunner,
    EvaluationResult,
    aggregate,
    calculate_agreement,
    OVERALL_PASS_THRESHOLD,
    AGREEMENT_THRESHOLD,
    PASS,
    PARTIAL,
    FAIL,
    CalibrationItem,
    CalibrationQueue,
    CalibrationStatus,
    combine_deterministic_subjective,
    DET_OVERRIDES_SUBJECTIVE,
    EVALUATOR_VERSION,
)


# ── Shared test helpers ──

def _make_evidence(scenario_id: str = "test_scenario_1") -> EvaluationEvidence:
    """Build a deterministic evidence packet for tests."""
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="早安",
        composed_context="[System] 你是 Yua。\n[mood] happy\n",
        llm_response="早安 Bry！今天過得如何？",
        model="mock-llm-v1",
        temperature=0.85,
        state_snapshot={"mood": 0.5, "memory_facts_count": 3, "relationship_confidence": 0.85},
        prompt_version="prompt-v1",
        rubric_version=RUBRIC_VERSION,
        extra={"agent_id": "agent_yua", "user_id": "bryan"},
    )


def _make_3_judges_low_disagree():
    """3 judges that agree (all score 4)."""
    return [
        HighAgreementJudge(judge_id="judge-A", base=4),
        HighAgreementJudge(judge_id="judge-B", base=4),
        HighAgreementJudge(judge_id="judge-C", base=4),
    ]


def _make_3_judges_high_disagree():
    """3 judges that disagree strongly."""
    j_a = ScriptedJudge(judge_id="judge-A", script=[
        {dim: 5 for dim in EIGHT_DIMENSIONS},
    ])
    j_b = ScriptedJudge(judge_id="judge-B", script=[
        {dim: 1 for dim in EIGHT_DIMENSIONS},
    ])
    j_c = ScriptedJudge(judge_id="judge-C", script=[
        {dim: 3 for dim in EIGHT_DIMENSIONS},
    ])
    return [j_a, j_b, j_c]


# ── 1. Evidence serialization ──

class TestEvidenceSerialization(unittest.TestCase):
    """1. Evidence serialization — to_dict / from_dict round-trip."""

    def test_evidence_to_dict_has_all_fields(self):
        ev = _make_evidence()
        d = evidence_to_dict(ev)
        self.assertIn("scenario_id", d)
        self.assertIn("user_input", d)
        self.assertIn("composed_context", d)
        self.assertIn("llm_response", d)
        self.assertIn("state_snapshot", d)
        self.assertIn("model", d)
        self.assertIn("prompt_version", d)
        self.assertIn("temperature", d)
        self.assertIn("rubric_version", d)
        self.assertIn("extra", d)

    def test_evidence_roundtrip_preserves_all_fields(self):
        ev = _make_evidence()
        d = evidence_to_dict(ev)
        ev2 = evidence_from_dict(d)
        self.assertEqual(ev.scenario_id, ev2.scenario_id)
        self.assertEqual(ev.user_input, ev2.user_input)
        self.assertEqual(ev.composed_context, ev2.composed_context)
        self.assertEqual(ev.llm_response, ev2.llm_response)
        self.assertEqual(ev.state_snapshot, ev2.state_snapshot)
        self.assertEqual(ev.model, ev2.model)
        self.assertEqual(ev.prompt_version, ev2.prompt_version)
        self.assertEqual(ev.temperature, ev2.temperature)
        self.assertEqual(ev.rubric_version, ev2.rubric_version)
        self.assertEqual(ev.extra, ev2.extra)

    def test_evidence_json_serializable(self):
        import json as _json
        ev = _make_evidence()
        d = evidence_to_dict(ev)
        s = _json.dumps(d, ensure_ascii=False)
        self.assertIsInstance(s, str)
        d2 = _json.loads(s)
        self.assertEqual(d, d2)

    def test_evidence_from_dict_missing_field_raises(self):
        d = evidence_to_dict(_make_evidence())
        del d["scenario_id"]
        with self.assertRaises(KeyError):
            evidence_from_dict(d)

    def test_evidence_state_snapshot_must_be_dict(self):
        with self.assertRaises(TypeError):
            EvaluationEvidence(
                scenario_id="x", user_input="x", composed_context="x",
                llm_response="x", state_snapshot="not a dict",
                model="x", prompt_version="x", temperature=0.5,
                rubric_version=RUBRIC_VERSION,
            )


# ── 2. Rubric validation ──

class TestRubricValidation(unittest.TestCase):
    """2. Rubric validation — 8 dimensions, 1-5 categorical Likert."""

    def test_eight_dimensions_count(self):
        self.assertEqual(len(EIGHT_DIMENSIONS), 8)

    def test_eight_dimensions_names(self):
        expected = {
            "context_coherence", "temporal_appropriateness",
            "relationship_continuity", "memory_continuity",
            "emotional_continuity", "world_context_relevance",
            "character_persona_consistency", "lived_context_coherence",
        }
        self.assertEqual(EIGHT_DIMENSIONS, frozenset(expected))

    def test_rubric_anchors_1_to_5(self):
        self.assertEqual(set(RUBRIC_ANCHORS.keys()), {1, 2, 3, 4, 5})

    def test_validate_score_accepts_valid(self):
        for s in (1, 2, 3, 4, 5):
            self.assertEqual(validate_score(s), s)

    def test_validate_score_rejects_zero(self):
        with self.assertRaises(ValueError):
            validate_score(0)

    def test_validate_score_rejects_six(self):
        with self.assertRaises(ValueError):
            validate_score(6)

    def test_validate_score_rejects_string(self):
        with self.assertRaises(ValueError):
            validate_score("3")

    def test_validate_score_rejects_float(self):
        with self.assertRaises(ValueError):
            validate_score(3.5)

    def test_validate_score_rejects_negative(self):
        with self.assertRaises(ValueError):
            validate_score(-1)

    def test_validate_score_rejects_bool(self):
        # bool is technically int subclass; rubric must reject
        with self.assertRaises(ValueError):
            validate_score(True)


# ── 3. Judge result validation ──

class TestJudgeResultValidation(unittest.TestCase):
    """3. JudgeResult dataclass validation."""

    def test_valid_judge_result(self):
        jr = JudgeResult(
            judge_id="judge-A",
            model="mock",
            per_dimension_scores={dim: 4 for dim in EIGHT_DIMENSIONS},
            rationale="test",
        )
        self.assertEqual(jr.judge_id, "judge-A")
        self.assertEqual(len(jr.per_dimension_scores), 8)

    def test_judge_result_unknown_dimension_raises(self):
        with self.assertRaises(ValueError):
            JudgeResult(
                judge_id="judge-A",
                model="mock",
                per_dimension_scores={"unknown_dim": 4},
            )

    def test_judge_result_invalid_score_raises(self):
        with self.assertRaises(ValueError):
            JudgeResult(
                judge_id="judge-A",
                model="mock",
                per_dimension_scores={dim: 0 for dim in EIGHT_DIMENSIONS},
            )


# ── 4. Three independent judges ──

class TestThreeIndependentJudges(unittest.TestCase):
    """4. SequentialJudgeRunner runs 3 judges with same evidence."""

    def test_runner_requires_exactly_three_judges(self):
        with self.assertRaises(ValueError):
            SequentialJudgeRunner([FixedScoreJudge("a", 4)])
        with self.assertRaises(ValueError):
            SequentialJudgeRunner([
                FixedScoreJudge("a", 4),
                FixedScoreJudge("b", 4),
            ])
        with self.assertRaises(ValueError):
            SequentialJudgeRunner([
                FixedScoreJudge("a", 4),
                FixedScoreJudge("b", 4),
                FixedScoreJudge("c", 4),
                FixedScoreJudge("d", 4),
            ])

    def test_runner_requires_unique_judge_ids(self):
        with self.assertRaises(ValueError):
            SequentialJudgeRunner([
                FixedScoreJudge("dup", 4),
                FixedScoreJudge("dup", 4),
                FixedScoreJudge("unique", 4),
            ])

    def test_runner_runs_three_judges(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence()
        results = runner.run(ev)
        self.assertEqual(len(results), 3)
        for jr in results:
            self.assertEqual(len(jr.per_dimension_scores), 8)

    def test_runner_returns_results_in_submission_order(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        ids = [jr.judge_id for jr in results]
        self.assertEqual(ids, ["judge-A", "judge-B", "judge-C"])


# ── 5. No judge cross-contamination ──

class TestNoCrossContamination(unittest.TestCase):
    """5. Judges do not see each other's answers."""

    def test_each_judge_receives_only_evidence(self):
        # Use a ScriptedJudge that records its inputs
        class RecordingJudge(Judge):
            def __init__(self, judge_id, recording_list):
                super().__init__(judge_id, "mock-recording")
                self.recording = recording_list
                self.call_count = 0

            def evaluate(self, evidence):
                self.call_count += 1
                self.recording.append(evidence)
                return JudgeResult(
                    judge_id=self.judge_id,
                    model=self.model,
                    per_dimension_scores={dim: 4 for dim in EIGHT_DIMENSIONS},
                    rationale=f"recording call {self.call_count}",
                )

        recordings = []
        judges = [
            RecordingJudge("A", recordings),
            RecordingJudge("B", recordings),
            RecordingJudge("C", recordings),
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence()
        runner.run(ev)

        # Each judge saw the evidence exactly once
        self.assertEqual(len(recordings), 3)
        # Each recording IS the original evidence (not a previous JudgeResult)
        for recorded in recordings:
            self.assertIs(recorded, ev)
        # No judge saw another judge's JudgeResult
        for recorded in recordings:
            self.assertNotIsInstance(recorded, JudgeResult)


# ── 6. Median aggregation ──

class TestMedianAggregation(unittest.TestCase):
    """6. aggregate() computes per-dimension median across 3 judges."""

    def test_median_for_4_4_5(self):
        judges = [
            FixedScoreJudge("A", 4),
            FixedScoreJudge("B", 4),
            ScriptedJudge("C", [{dim: 5 for dim in EIGHT_DIMENSIONS}]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")

        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(agg.median_scores[dim], 4)
            self.assertEqual(agg.per_dimension_scores[dim], [4, 4, 5])

    def test_median_for_3_4_5(self):
        judges = [
            ScriptedJudge("A", [{dim: 3 for dim in EIGHT_DIMENSIONS}]),
            ScriptedJudge("B", [{dim: 4 for dim in EIGHT_DIMENSIONS}]),
            ScriptedJudge("C", [{dim: 5 for dim in EIGHT_DIMENSIONS}]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")

        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(agg.median_scores[dim], 4)  # median(3,4,5)=4
            self.assertEqual(agg.per_dimension_scores[dim], [3, 4, 5])

    def test_median_for_1_3_5(self):
        judges = [
            ScriptedJudge("A", [{dim: 1 for dim in EIGHT_DIMENSIONS}]),
            ScriptedJudge("B", [{dim: 3 for dim in EIGHT_DIMENSIONS}]),
            ScriptedJudge("C", [{dim: 5 for dim in EIGHT_DIMENSIONS}]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")

        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(agg.median_scores[dim], 3)
            self.assertEqual(agg.per_dimension_scores[dim], [1, 3, 5])


# ── 7. Agreement calculation ──

class TestAgreementCalculation(unittest.TestCase):
    """7. calculate_agreement() returns per-dim max_diff and counts."""

    def test_agreement_full(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = calculate_agreement(results, list(EIGHT_DIMENSIONS))
        self.assertEqual(agg["num_disagreements"], 0)
        self.assertEqual(agg["num_harmful"], 0)
        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(agg["per_dimension_max_diff"][dim], 0)

    def test_agreement_high(self):
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = calculate_agreement(results, list(EIGHT_DIMENSIONS))
        # All 8 dimensions have max_diff=4 (5-1=4)
        self.assertEqual(agg["num_disagreements"], 8)
        # 8 dimensions have score=1
        self.assertEqual(agg["num_harmful"], 8)

    def test_agreement_one_dim_off(self):
        # A=4, B=4, C=3 on one dim; rest all 4
        scripts = [
            {dim: 4 for dim in EIGHT_DIMENSIONS},
            {dim: 4 for dim in EIGHT_DIMENSIONS},
            {**{dim: 4 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 3},
        ]
        judges = [
            ScriptedJudge("A", [scripts[0]]),
            ScriptedJudge("B", [scripts[1]]),
            ScriptedJudge("C", [scripts[2]]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = calculate_agreement(results, list(EIGHT_DIMENSIONS))
        # 1 dimension with max_diff=1 → not disagreement (threshold=2)
        self.assertEqual(agg["num_disagreements"], 0)
        # memory_continuity has max_diff=1
        self.assertEqual(agg["per_dimension_max_diff"]["memory_continuity"], 1)


# ── 8. High-disagreement calibration trigger ──

class TestHighDisagreementCalibration(unittest.TestCase):
    """8. High disagreement → calibration_required=True."""

    def test_high_disagreement_triggers_calibration(self):
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        self.assertTrue(agg.calibration_required)

    def test_harmful_content_triggers_calibration(self):
        # Even if all 3 judges agree (all 1), harmful content triggers
        judges = [
            FixedScoreJudge("A", 1),
            FixedScoreJudge("B", 1),
            FixedScoreJudge("C", 1),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        self.assertTrue(agg.calibration_required)


# ── 9. Low-disagreement automatic result ──

class TestLowDisagreementAutomatic(unittest.TestCase):
    """9. Low disagreement (all agree) → calibration_required=False."""

    def test_full_agreement_no_calibration(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        self.assertFalse(agg.calibration_required)

    def test_full_agreement_status_pass(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        self.assertEqual(agg.overall_subjective_status, PASS)

    def test_partial_status_when_some_below_threshold(self):
        # 1 dimension below 3 (max_diff=0, all agree on 2)
        scripts = [
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
        ]
        judges = [
            ScriptedJudge("A", [scripts[0]]),
            ScriptedJudge("B", [scripts[1]]),
            ScriptedJudge("C", [scripts[2]]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        self.assertEqual(agg.overall_subjective_status, PARTIAL)
        # No disagreement (all agree), so no calibration from disagreement
        # But 2 < OVERALL_PASS_THRESHOLD=3 → still PARTIAL not PASS
        self.assertFalse(agg.calibration_required)


# ── 10. Calibration queue generation ──

class TestCalibrationQueue(unittest.TestCase):
    """10. CalibrationQueue add / load / update / pending."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "calibration.jsonl"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_creates_pending_item(self):
        queue = CalibrationQueue(self.path)
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test_cal_1")
        item = queue.add(agg)
        self.assertEqual(item.status, CalibrationStatus.PENDING)
        self.assertIsNotNone(item.created_at)

    def test_load_pending(self):
        queue = CalibrationQueue(self.path)
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        for i in range(3):
            agg = aggregate(runner.run(_make_evidence()), scenario_id=f"cal_{i}")
            queue.add(agg)
        pending = queue.load_pending()
        self.assertEqual(len(pending), 3)

    def test_update_changes_status(self):
        queue = CalibrationQueue(self.path)
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        agg = aggregate(runner.run(_make_evidence()), scenario_id="test")
        item = queue.add(agg)

        updated = queue.update(item.item_id, CalibrationStatus.REVIEWED, "looks ok")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, CalibrationStatus.REVIEWED)
        self.assertEqual(updated.reviewer_note, "looks ok")

    def test_update_unknown_id_returns_none(self):
        queue = CalibrationQueue(self.path)
        result = queue.update("nonexistent-id", CalibrationStatus.REVIEWED)
        self.assertIsNone(result)

    def test_item_roundtrip_via_dict(self):
        queue = CalibrationQueue(self.path)
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        agg = aggregate(runner.run(_make_evidence()), scenario_id="roundtrip")
        item = queue.add(agg)
        d = item.to_dict()
        item2 = CalibrationItem.from_dict(d)
        self.assertEqual(item.item_id, item2.item_id)
        self.assertEqual(item.status, item2.status)
        self.assertEqual(item.result.scenario_id, item2.result.scenario_id)
        self.assertEqual(item.result.median_scores, item2.result.median_scores)


# ── 11. Deterministic precedence ──

class TestDeterministicPrecedence(unittest.TestCase):
    """11. combine_deterministic_subjective applies Bry precedence rule."""

    def test_det_pass_subj_pass_yields_pass(self):
        judges = _make_3_judges_low_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")  # subj=PASS
        out = combine_deterministic_subjective(deterministic_pass=True, subjective_result=agg)
        self.assertEqual(out["final_status"], PASS)

    def test_det_pass_subj_partial_yields_partial(self):
        scripts = [
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
            {**{dim: 3 for dim in EIGHT_DIMENSIONS}, "memory_continuity": 2},
        ]
        judges = [
            ScriptedJudge("A", [scripts[0]]),
            ScriptedJudge("B", [scripts[1]]),
            ScriptedJudge("C", [scripts[2]]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")  # subj=PARTIAL
        out = combine_deterministic_subjective(deterministic_pass=True, subjective_result=agg)
        self.assertEqual(out["final_status"], PARTIAL)

    def test_det_pass_subj_fail_yields_partial(self):
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")  # subj=FAIL
        out = combine_deterministic_subjective(deterministic_pass=True, subjective_result=agg)
        self.assertEqual(out["final_status"], PARTIAL)

    def test_det_fail_subj_pass_yields_fail(self):
        # Subjective passes (all agree on 5) but deterministic fails
        judges = [
            HighAgreementJudge("A", base=5),
            HighAgreementJudge("B", base=5),
            HighAgreementJudge("C", base=5),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")  # subj=PASS
        out = combine_deterministic_subjective(deterministic_pass=False, subjective_result=agg)
        self.assertEqual(out["final_status"], FAIL)
        self.assertIn("overrides", out["precedence_rule_applied"])

    def test_det_fail_subj_fail_yields_fail(self):
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="test")
        out = combine_deterministic_subjective(deterministic_pass=False, subjective_result=agg)
        self.assertEqual(out["final_status"], FAIL)

    def test_subjective_never_overrides_deterministic_fail(self):
        # Even a "perfect" subjective evaluation cannot override det FAIL
        judges = [
            HighAgreementJudge("A", base=5),
            HighAgreementJudge("B", base=5),
            HighAgreementJudge("C", base=5),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="perfect")
        self.assertEqual(agg.overall_subjective_status, PASS)
        out = combine_deterministic_subjective(deterministic_pass=False, subjective_result=agg)
        self.assertEqual(out["final_status"], FAIL)
        self.assertTrue(DET_OVERRIDES_SUBJECTIVE)


# ── 12. Production isolation ──

class TestProductionIsolation(unittest.TestCase):
    """12. Subjective eval must not mutate production data."""

    REPO_ROOT = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
    PROD_FILES = [
        "data/soul/agent_yua/relationships.json",
        "data/soul/agent_ruka/relationships.json",
        "data/agents/agent_yua/carryover.json",
    ]

    def test_evaluation_runs_dont_mutate_production(self):
        # Capture production SHA256 before
        before = {}
        for rel in self.PROD_FILES:
            full = self.REPO_ROOT / rel
            if full.exists():
                before[rel] = (
                    __import__("hashlib").sha256(full.read_bytes()).hexdigest(),
                    full.stat().st_mtime,
                )

        # Run a full subjective evaluation
        judges = _make_3_judges_high_disagree()
        runner = SequentialJudgeRunner(judges)
        for i in range(3):
            ev = _make_evidence(f"isolation_test_{i}")
            results = runner.run(ev)
            agg = aggregate(results, scenario_id=f"isolation_test_{i}")
            # Use tempdir for calibration queue (test isolation)
            with tempfile.TemporaryDirectory() as td:
                queue = CalibrationQueue(Path(td) / "cal.jsonl")
                queue.add(agg)

        # Capture production SHA256 after
        after = {}
        for rel in self.PROD_FILES:
            full = self.REPO_ROOT / rel
            if full.exists():
                after[rel] = (
                    __import__("hashlib").sha256(full.read_bytes()).hexdigest(),
                    full.stat().st_mtime,
                )

        # Compare
        for rel in self.PROD_FILES:
            if rel in before:
                self.assertEqual(
                    before[rel], after[rel],
                    f"Production file {rel} was mutated by subjective eval!"
                )


# ── 13. Malformed judge output handling ──

class TestMalformedJudgeOutput(unittest.TestCase):
    """13. Malformed judge output (out-of-range, wrong type) handled safely."""

    def test_evaluate_rejects_score_zero(self):
        class ZeroJudge(Judge):
            def __init__(self):
                super().__init__("zero", "mock")

            def evaluate(self, evidence):
                return JudgeResult(
                    judge_id=self.judge_id,
                    model=self.model,
                    per_dimension_scores={dim: 0 for dim in EIGHT_DIMENSIONS},
                )

        with self.assertRaises(ValueError):
            ZeroJudge().evaluate(_make_evidence())

    def test_evaluate_rejects_score_seven(self):
        class SevenJudge(Judge):
            def __init__(self):
                super().__init__("seven", "mock")

            def evaluate(self, evidence):
                return JudgeResult(
                    judge_id=self.judge_id,
                    model=self.model,
                    per_dimension_scores={dim: 7 for dim in EIGHT_DIMENSIONS},
                )

        with self.assertRaises(ValueError):
            SevenJudge().evaluate(_make_evidence())


# ── 14. Missing dimension handling ──

class TestMissingDimensionHandling(unittest.TestCase):
    """14. If a judge returns < 8 dimensions, aggregate handles gracefully."""

    def test_judge_missing_one_dimension(self):
        # Judge C only returns 7 dimensions (missing memory_continuity)
        scripts = [
            {dim: 4 for dim in EIGHT_DIMENSIONS},
            {dim: 4 for dim in EIGHT_DIMENSIONS},
            {dim: 4 for dim in EIGHT_DIMENSIONS if dim != "memory_continuity"},
        ]
        judges = [
            ScriptedJudge("A", [scripts[0]]),
            ScriptedJudge("B", [scripts[1]]),
            ScriptedJudge("C", [scripts[2]]),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        agg = aggregate(results, scenario_id="missing_dim")
        # memory_continuity: only 2 scores → median still computed
        self.assertEqual(agg.per_dimension_scores["memory_continuity"], [4, 4])
        self.assertEqual(agg.median_scores["memory_continuity"], 4)
        # other dims: 3 scores
        for dim in EIGHT_DIMENSIONS:
            if dim != "memory_continuity":
                self.assertEqual(len(agg.per_dimension_scores[dim]), 3)


# ── 15. Invalid score handling ──

class TestInvalidScoreHandling(unittest.TestCase):
    """15. Invalid scores (negative, non-int) rejected at construction."""

    def test_score_negative_rejected(self):
        with self.assertRaises(ValueError):
            JudgeResult(
                judge_id="bad",
                model="mock",
                per_dimension_scores={dim: -1 for dim in EIGHT_DIMENSIONS},
            )

    def test_score_string_rejected(self):
        with self.assertRaises(ValueError):
            JudgeResult(
                judge_id="bad",
                model="mock",
                per_dimension_scores={"context_coherence": "good"},
            )

    def test_score_none_rejected(self):
        with self.assertRaises(ValueError):
            JudgeResult(
                judge_id="bad",
                model="mock",
                per_dimension_scores={dim: None for dim in EIGHT_DIMENSIONS},
            )

    def test_fixed_score_judge_rejects_out_of_range(self):
        with self.assertRaises(ValueError):
            FixedScoreJudge("bad", 0)
        with self.assertRaises(ValueError):
            FixedScoreJudge("bad", 6)


# ── 16. Reproducibility ──

class TestReproducibility(unittest.TestCase):
    """16. Same input → same output (deterministic, reproducible)."""

    def test_same_evidence_same_aggregate(self):
        # Build two identical aggregates from same evidence
        judges = _make_3_judges_low_disagree()
        runner1 = SequentialJudgeRunner(judges)
        runner2 = SequentialJudgeRunner(judges)
        ev1 = _make_evidence("repro_test")
        ev2 = _make_evidence("repro_test")
        results1 = runner1.run(ev1)
        results2 = runner2.run(ev2)
        agg1 = aggregate(results1, scenario_id="repro_test")
        agg2 = aggregate(results2, scenario_id="repro_test")
        self.assertEqual(agg1.median_scores, agg2.median_scores)
        self.assertEqual(agg1.overall_subjective_status, agg2.overall_subjective_status)
        self.assertEqual(agg1.calibration_required, agg2.calibration_required)

    def test_three_independent_runs_same_result(self):
        # Run the same scenario 3 times
        judges_factory = _make_3_judges_low_disagree
        results_list = []
        for _ in range(3):
            judges = judges_factory()
            runner = SequentialJudgeRunner(judges)
            results = runner.run(_make_evidence("triple_run"))
            results_list.append(aggregate(results, scenario_id="triple_run"))

        first = results_list[0]
        for other in results_list[1:]:
            self.assertEqual(first.median_scores, other.median_scores)
            self.assertEqual(first.overall_subjective_status, other.overall_subjective_status)
            self.assertEqual(first.calibration_required, other.calibration_required)

    def test_evaluator_version_constant(self):
        self.assertEqual(EVALUATOR_VERSION, "m6.0.5-2026-08-11")


if __name__ == "__main__":
    unittest.main()
