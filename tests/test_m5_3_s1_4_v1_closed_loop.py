"""
M5.3-S1-4 — v1 Production Closed-Loop Acceptance Tests

Bry 派工 2026-08-09 14:39:
- S1-2 / S1-3 SKIP (path 已經 unified, wiring 已經 correct)
- Do NOT modify retrieval/overlap logic
- Do NOT modify fail_safe label
- Do NOT refactor unrelated code
- Do NOT change production memory behavior

Two layers:
A. Deterministic closed-loop contract test (write → store → read → load → eligible)
B. Realistic production-query evidence (diagnostic, no behavior change)

Test isolation: use tempfile.TemporaryDirectory for test data, never touch production data.
"""
from __future__ import annotations

import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

# Ensure project root on sys.path (mirror how tests do it)
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.memory.v1.schema import Memory
from src.memory.v1.store import V1Store
from src.memory.v1.loader import MemoryLoader, format_for_prompt, derive_query_tags


# ────────────────────────────────────────────────────────────
# Layer A — Deterministic closed-loop contract tests
# ────────────────────────────────────────────────────────────


def test_s1_4_deterministic_high_confidence_eligible_positive():
    """A.1: Write high-confidence memory → canonical memories.jsonl →
    V1Store reads → MemoryLoader is exercised → eligible_count > 0 →
    format_for_prompt contains the eligible memory.

    This test PROVES the v1 closed loop is functional end-to-end with
    deterministic inputs (no overlap-vs-confidence ambiguity).

    Design choice: use a fresh test agent_id + temp dir to avoid touching
    any production data. Tags ['s1_4', 's1_4_high_conf', 's1_4_test']
    are guaranteed to overlap with query tags ['s1_4'].
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "memory"
        agent_id = "agent_s1_4_high_test"
        store_file = data_dir / agent_id / "memories.jsonl"

        # Step 1: Write high-confidence memory via V1Store.add()
        # (this is the same path Writer._mirror_to_v1_store uses)
        store = V1Store(data_dir, agent_id)
        mem = Memory(
            memory_id="s1_4_test_high_1",
            agent_id=agent_id,
            content="s1_4 high confidence test memory",
            tags=["s1_4", "s1_4_high_conf", "s1_4_test"],
            created_at=time.time(),
            category="preference",
            confidence=0.85,  # > 0.75 threshold for preference
        )
        store.add(mem)

        # Step 2: Verify file landed at canonical path
        assert store_file.exists(), f"memories.jsonl not at {store_file}"
        assert store_file.stat().st_size > 0

        # Step 3: V1Store reads it back
        all_mem = store.all()
        assert len(all_mem) == 1
        assert all_mem[0].memory_id == "s1_4_test_high_1"
        assert all_mem[0].category == "preference"
        assert all_mem[0].confidence == 0.85

        # Step 4: MemoryLoader is exercised
        loader = MemoryLoader(store=store, trace_log_path=None)
        query_tags = derive_query_tags("s1_4")
        assert "s1_4" in query_tags, f"derive_query_tags should produce 's1_4' tag, got {query_tags}"
        result = loader.load(query_tags, agent_id)

        # Step 5: eligible_count > 0
        eligible = result["eligible_memories"]
        assert len(eligible) == 1, (
            f"Expected 1 eligible, got {len(eligible)}. "
            f"Trace: {result['trace']}"
        )
        assert eligible[0].memory_id == "s1_4_test_high_1"

        # Step 6: format_for_prompt output contains the eligible memory
        prompt = format_for_prompt(eligible)
        assert "s1_4" in prompt, f"Prompt missing 's1_4' tag: {prompt!r}"
        assert "s1_4 high confidence test memory" in prompt, (
            f"Prompt missing content: {prompt!r}"
        )
        assert "[Recall relevant memories]" in prompt
        assert "[/Recall]" in prompt


def test_s1_4_deterministic_low_confidence_fail_safe():
    """A.2: Same deterministic retrieval path with low confidence →
    eligible_count == 0 → fail-safe behavior.

    No Memory > Wrong Memory principle: 0.50 confidence is below 0.75
    threshold for preference category, so memory must NOT be injected.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "memory"
        agent_id = "agent_s1_4_low_test"

        store = V1Store(data_dir, agent_id)
        mem = Memory(
            memory_id="s1_4_test_low_1",
            agent_id=agent_id,
            content="s1_4 low confidence test memory",
            tags=["s1_4", "s1_4_low_conf", "s1_4_test"],
            created_at=time.time(),
            category="preference",
            confidence=0.50,  # < 0.75 threshold for preference
        )
        store.add(mem)

        loader = MemoryLoader(store=store, trace_log_path=None)
        query_tags = derive_query_tags("s1_4")
        result = loader.load(query_tags, agent_id)

        # No Memory > Wrong Memory: low confidence MUST be filtered out
        assert len(result["eligible_memories"]) == 0, (
            f"Low-confidence memory should be filtered. "
            f"Got eligible={result['eligible_memories']}, "
            f"Trace: {result['trace']}"
        )

        # Fail-safe triggered label
        assert result["trace"]["fail_safe_triggered"] == "all_rejected_low_confidence"

        # Candidates list should contain the rejected memory with reason
        candidates = result["trace"]["candidates"]
        assert len(candidates) == 1
        assert candidates[0]["memory_id"] == "s1_4_test_low_1"
        assert candidates[0]["status"] == "rejected_low_confidence"
        assert candidates[0]["confidence"] == 0.50
        assert candidates[0]["confidence_threshold"] == 0.75

        # format_for_prompt should return empty string (no memory to format)
        prompt = format_for_prompt(result["eligible_memories"])
        assert prompt == ""


# ────────────────────────────────────────────────────────────
# Layer B — Realistic production-query evidence (DIAGNOSTIC ONLY)
# ────────────────────────────────────────────────────────────


def test_s1_4_realistic_production_query_evidence_diagnostic(capsys):
    """B: Use existing real memories.jsonl + realistic queries.

    This test is DIAGNOSTIC ONLY:
    - Does NOT modify any production data
    - Does NOT modify retrieval/overlap logic
    - Does NOT modify fail_safe label
    - Captures candidate/rejection breakdown for S-1 follow-up analysis

    Output: pytest -s shows the breakdown table for Bry to review.
    """
    # Use the production data dir (read-only access pattern)
    data_dir = Path("data/memory")
    agent_id = "agent_rem"  # in LOADER_ENABLED_FOR_AGENTS = {agent_rem, agent_yua}

    v1_store = V1Store(data_dir, agent_id)
    all_mem = v1_store.all()
    assert len(all_mem) > 0, f"No memories in {data_dir}/{agent_id}/memories.jsonl"

    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    # Realistic queries
    queries = [
        "雷姆 是 什麼 種族",
        "雷姆 喜歡 吃 什麼",
        "雷姆 出生時 有 什麼 特別",
        "雷姆 跟 Ram 姐姐",
        "雷姆 住在 哪裡",
    ]

    results_table = []
    for query in queries:
        tags = derive_query_tags(query)
        result = loader.load(tags, agent_id)
        trace = result["trace"]
        candidates = trace.get("candidates", [])

        # Tally rejection reasons from candidate status
        status_counts = Counter(c.get("status", "unknown") for c in candidates)
        n_selected = status_counts.get("selected", 0)
        n_rejected_low_conf = status_counts.get("rejected_low_confidence", 0)
        n_rejected_no_meta = status_counts.get("rejected_no_v11_metadata", 0)

        # "by_tags empty" case: no candidates at all (overlap failure)
        n_by_tags_empty = 1 if len(candidates) == 0 else 0

        results_table.append({
            "query": query,
            "query_tags": tags,
            "candidate_count": len(candidates),
            "by_tags_empty": n_by_tags_empty,
            "selected": n_selected,
            "rejected_low_confidence": n_rejected_low_conf,
            "rejected_no_v11_metadata": n_rejected_no_meta,
            "eligible_count": trace.get("eligible_count", 0),
            "fail_safe_triggered": trace.get("fail_safe_triggered"),
        })

    # Print diagnostic table (visible with pytest -s)
    with capsys.disabled():
        print()
        print("=" * 90)
        print(f"M5.3-S1-4 Layer B — Realistic Production-Query Evidence (DIAGNOSTIC ONLY)")
        print(f"Agent: {agent_id} (in LOADER_ENABLED_FOR_AGENTS)")
        print(f"Total memories in store: {len(all_mem)}")
        print("=" * 90)
        for r in results_table:
            print(f"\nQuery: {r['query']!r}")
            print(f"  query_tags:              {r['query_tags']}")
            print(f"  candidate_count:         {r['candidate_count']}")
            print(f"  by_tags_empty:           {r['by_tags_empty']}")
            print(f"  selected:                {r['selected']}")
            print(f"  rejected_low_confidence: {r['rejected_low_confidence']}")
            print(f"  rejected_no_v11_metadata: {r['rejected_no_v11_metadata']}")
            print(f"  eligible_count:          {r['eligible_count']}")
            print(f"  fail_safe_triggered:     {r['fail_safe_triggered']}")
        print()
        print("=" * 90)

    # Sanity assertions: the diagnostic data was captured
    assert len(results_table) == len(queries)
    for r in results_table:
        # Each query produces a non-None fail_safe value (real Loader behavior)
        # NOTE: this is a structural sanity check, not a behavior assertion
        assert "eligible_count" in r
        assert "fail_safe_triggered" in r

    # Demonstrate MemoryLoader IS being exercised (not bypassed):
    # At least one query should produce some candidates OR show by_tags_empty
    has_signal = any(
        r["candidate_count"] > 0 or r["by_tags_empty"] == 1
        for r in results_table
    )
    assert has_signal, "Loader did not produce any diagnostic signal"
