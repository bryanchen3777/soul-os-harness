"""
M5.3-S2-A — Retrieval / Overlap Root-Cause Diagnostic

Bry 派工 2026-08-09 14:59:
- S2-A = root cause confirmation, READ-ONLY diagnostic
- DO NOT modify production source code
- DO NOT commit, DO NOT push
- DO NOT touch KI-005
- DO NOT touch SAGE
- DO NOT change MemoryMiddleware other behaviors
- DO NOT refactor

This test file is a CONTROLLED diagnostic. It:
- Reads production data/memory/agent_rem/memories.jsonl (read-only)
- Runs 50+ production-like queries across 5 categories
- Runs 20 exact-tag vs 20 natural-language controlled comparison
- Runs confidence isolation test (0.85 / 0.75 / 0.50)
- Runs negative (false-positive) tests
- Captures retrieval breakdown statistics
- Emits diagnostic table for Bry to review

Verdict logic:
- CONFIRMED root cause if:
  - exact_tag_hit_rate >> natural_language_hit_rate
  - candidate generation shows structural mismatch pattern
- NOT CONFIRMED if:
  - candidate generation works correctly across the board
"""
from __future__ import annotations

import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.memory.v1.schema import Memory
from src.memory.v1.store import V1Store
from src.memory.v1.loader import MemoryLoader, format_for_prompt, derive_query_tags


# ────────────────────────────────────────────────────────────
# 50+ production-like queries across 5 categories
# ────────────────────────────────────────────────────────────

CATEGORY_A_IDENTITY = [
    # Direct character identity questions
    "雷姆 是 什麼 種族",          # A.1 — what race
    "雷姆 是 鬼族",                # A.2 — exact memory tag
    "雷姆 是 鬼族 嗎",             # A.3 — yes/no framing
    "雷姆 種族",                   # A.4 — short
    "雷姆 出生 時 有 什麼 特別",   # A.5 — what special at birth
    "雷姆 出生時有 一點點角",       # A.6 — exact memory content/tags
    "雷姆 角 怎麼 回事",           # A.7 — what about her horn
    "雷姆 出生時 有 角 嗎",         # A.8 — horn at birth?
    "雷姆 住在 哪裡",              # A.9 — where lives
    "Ram 位置 罗兹瓦尔公馆",       # A.10 — exact memory
    "拉姆 在 哪裡",                # A.11 — Ram where
    "レム 種族",                   # A.12 — Japanese Rem
]

CATEGORY_B_RELATIONSHIP = [
    # Relationship questions
    "雷姆 和 姊姊",                # B.1
    "雷姆 跟 Ram 姐姐",            # B.2
    "雷姆 和 Ram 是 什麼 關係",     # B.3
    "雷姆 對 昴 的 感情",           # B.4
    "雷姆 和 姊姊 的 關係",         # B.5
    "雷姆 姊姊",                   # B.6
    "雷姆和姊姊 被村裡的人認為 不吉祥",  # B.7 — exact content
    "姊姊的角 斷於 那一晚",         # B.8
    "姊姊 評價 很厲害",            # B.9
    "姊姊 比較",                   # B.10
    "Ram 跟 雷姆",                 # B.11
    "雷姆 跟 Bryan 關係",          # B.12
]

CATEGORY_C_PREFERENCE = [
    # Preferences / actions
    "雷姆 喜歡 吃 什麼",           # C.1
    "雷姆 習慣 晚睡",              # C.2
    "雷姆 決定 吃 牛肉麵",         # C.3
    "雷姆 動作 沒有 走遠",         # C.4
    "雷姆 陪伴 Bryan",             # C.5
    "雷姆 將頭靠在肩上 Bryan",     # C.6
    "雷姆 摸 Bryan 的 頭",         # C.7
    "雷姆 在想 Bryan 想 做什么",   # C.8
    "雷姆 表達 認同 Bryan",        # C.9
    "Bryan 應該 休息",             # C.10
    "湯頭 是 牛骨 熬的",           # C.11
    "我們 打算 出去 吃",           # C.12
]

CATEGORY_D_EVENT_BIOGRAPHY = [
    # Events / biography
    "雷姆 出生時 發生 什麼",       # D.1
    "雷姆 過去 發生過 什麼",       # D.2
    "雷姆 重要 事件",              # D.3
    "雷姆 在 事故中 活下來",       # D.4
    "雷姆 過去 經歷",              # D.5
    "雷姆 故事 重要 經歷",         # D.6
    "雷姆 為什麼 被 認為 不吉祥",   # D.7
    "Ram 的 角 怎麼 回事",         # D.8
    "雷姆 在 故事 裡",             # D.9
    "姊姊 比較 就算只剩一角",       # D.10
    "拉姆 比較",                   # D.11
    "雷姆 不可愛",                 # D.12
]

CATEGORY_E_NATURAL_LANGUAGE = [
    # Full natural language sentences
    "我想 知道 雷姆 平常 喜歡 吃 什麼",     # E.1
    "可以 告訴 我 雷姆 和 姐姐 之間 的 關係 嗎",  # E.2
    "雷姆 以前 是不是 住在 某個 地方",      # E.3
    "雷姆 跟 Ram 姐姐 是 什麼 種族 啊",     # E.4
    "雷姆 喜歡 吃 牛肉 嗎",                # E.5
    "為什麼 雷姆 跟 姊姊 被 認為 不吉祥 啊",  # E.6
    "雷姆 跟 Bryan 關係 怎麼 樣",          # E.7
    "你 可以 告訴 我 雷姆 故事 嗎",        # E.8
    "雷姆 最近 在 做 什麼",                # E.9
    "告訴 我 雷姆 的 種族",                # E.10
    "雷姆 出生時 是不是 有 角",            # E.11
    "雷姆 跟 昴 是 什麼 關係 啊",          # E.12
]

ALL_QUERIES = (
    CATEGORY_A_IDENTITY
    + CATEGORY_B_RELATIONSHIP
    + CATEGORY_C_PREFERENCE
    + CATEGORY_D_EVENT_BIOGRAPHY
    + CATEGORY_E_NATURAL_LANGUAGE
)
# Total: 12 * 5 = 60 queries

# ────────────────────────────────────────────────────────────
# Controlled comparison: 20 exact-tag vs 20 natural-language
# ────────────────────────────────────────────────────────────

# Pairs: each pair is (exact_tag_query, natural_language_query)
# If retrieval/overlap is broken, exact_tag should hit, natural should miss
COMPARISON_PAIRS = [
    # (exact_tag, natural_language)
    ("雷姆和姊姊",                       "雷姆 跟 姊姊"),                       # P.1
    ("雷姆出生時有",                     "雷姆 出生 時 有"),                     # P.2
    ("姊姊的角",                          "姊姊 的 角"),                          # P.3
    ("雷姆和姊姊 被村裡的人認為",        "雷姆 和 姊姊 被 村裡 人 認為"),        # P.4
    ("不吉祥",                            "不 吉祥"),                              # P.5
    ("在事故中",                          "在 事故 中"),                          # P.6
    ("活下來",                            "活 下來"),                              # P.7
    ("位置",                              "位置 在 哪裡"),                        # P.8
    ("牛骨熬的",                          "牛骨 熬的"),                            # P.9
    ("習慣晚睡",                          "習慣 晚 睡"),                          # P.10
    ("陪伴",                              "陪伴 著"),                              # P.11
    ("決定吃",                            "決定 吃"),                              # P.12
    ("表達認同",                          "表達 認同"),                            # P.13
    ("執行動作",                          "執行 動作"),                            # P.14
    ("雷姆 不可愛",                       "雷姆 不 可愛"),                         # P.15
    ("將頭靠在肩上",                      "將 頭 靠 在 肩上"),                     # P.16
    ("bryan",                              "Bryan"),                                # P.17
    ("bryan的頭",                          "Bryan 的 頭"),                          # P.18
    ("雷姆和姊姊",                       "雷姆 和 姊姊"),                         # P.19 — overlap but tokens differ
    ("在故事裡",                          "在 故事 裡"),                          # P.20
]


# ────────────────────────────────────────────────────────────
# Layer 1 — Production-like corpus (60 queries)
# ────────────────────────────────────────────────────────────


def _record_breakdown(loader, query, agent_id, all_mem_count):
    """Run a single query and return detailed breakdown."""
    tags = derive_query_tags(query)
    result = loader.load(tags, agent_id)
    trace = result["trace"]
    candidates = trace.get("candidates", [])

    status_counts = Counter(c.get("status", "unknown") for c in candidates)
    return {
        "query": query,
        "query_tags": tags,
        "candidate_count": len(candidates),
        "by_tags_empty": 1 if len(candidates) == 0 else 0,
        "selected": status_counts.get("selected", 0),
        "rejected_low_confidence": status_counts.get("rejected_low_confidence", 0),
        "rejected_no_v11_metadata": status_counts.get("rejected_no_v11_metadata", 0),
        "eligible_count": trace.get("eligible_count", 0),
        "fail_safe_triggered": trace.get("fail_safe_triggered"),
        "candidate_ids": [c.get("memory_id", "?")[:8] for c in candidates[:5]],
        "candidate_overlap_scores": [
            c.get("overlap_score", 0) for c in candidates[:5]
        ],
    }


def test_s2_a_1_production_like_corpus_diagnostic(capsys):
    """S2-A-1: Run 60 production-like queries across 5 categories.

    Output: full breakdown table + per-category statistics.
    Verdict: candidate hit rate per category, eligible rate, pattern detection.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    all_mem = v1_store.all()
    assert len(all_mem) > 0, f"No memories at {data_dir}/{agent_id}/"

    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    results = []
    for query in ALL_QUERIES:
        results.append(_record_breakdown(loader, query, agent_id, len(all_mem)))

    # Per-category statistics
    category_map = {
        "A_identity": CATEGORY_A_IDENTITY,
        "B_relationship": CATEGORY_B_RELATIONSHIP,
        "C_preference": CATEGORY_C_PREFERENCE,
        "D_event_biography": CATEGORY_D_EVENT_BIOGRAPHY,
        "E_natural_language": CATEGORY_E_NATURAL_LANGUAGE,
    }

    # Print diagnostic table
    with capsys.disabled():
        print()
        print("=" * 100)
        print(f"M5.3-S2-A-1 Layer 1 — Production-Like Corpus Diagnostic")
        print(f"Agent: {agent_id} | Total memories in store: {len(all_mem)}")
        print(f"Total queries: {len(ALL_QUERIES)}")
        print("=" * 100)

        for cat_name, cat_queries in category_map.items():
            cat_results = [
                r for r in results
                if r["query"] in cat_queries
            ]
            n = len(cat_results)
            n_with_cand = sum(1 for r in cat_results if r["candidate_count"] > 0)
            n_eligible = sum(1 for r in cat_results if r["eligible_count"] > 0)
            n_by_tags_empty = sum(r["by_tags_empty"] for r in cat_results)
            n_rej_low_conf = sum(r["rejected_low_confidence"] for r in cat_results)
            print(f"\n--- {cat_name} ({n} queries) ---")
            print(f"  with_candidates:        {n_with_cand}/{n} = {n_with_cand/n*100:.1f}%")
            print(f"  by_tags_empty:          {n_by_tags_empty}/{n} = {n_by_tags_empty/n*100:.1f}%")
            print(f"  rejected_low_conf:      {n_rej_low_conf}/{n}")
            print(f"  eligible:               {n_eligible}/{n} = {n_eligible/n*100:.1f}%")

            # Per-query detail
            for r in cat_results:
                cand_summary = f"cand={r['candidate_count']}"
                if r["candidate_count"] > 0:
                    cand_summary += f" [ids={r['candidate_ids']}, scores={r['candidate_overlap_scores']}]"
                print(
                    f"  '{r['query'][:40]}': "
                    f"tags={r['query_tags'][:6]}... "
                    f"eligible={r['eligible_count']} "
                    f"{cand_summary} "
                    f"rej_low_conf={r['rejected_low_confidence']}"
                )

        # Overall rollup
        n_total = len(results)
        n_with_cand = sum(1 for r in results if r["candidate_count"] > 0)
        n_eligible = sum(1 for r in results if r["eligible_count"] > 0)
        n_by_tags_empty = sum(r["by_tags_empty"] for r in results)
        n_rej_low_conf = sum(r["rejected_low_confidence"] for r in results)
        n_rej_no_meta = sum(r["rejected_no_v11_metadata"] for r in results)
        print()
        print("=" * 100)
        print(f"OVERALL ROLLUP ({n_total} queries)")
        print(f"  with_candidates:    {n_with_cand}/{n_total} = {n_with_cand/n_total*100:.1f}%")
        print(f"  by_tags_empty:      {n_by_tags_empty}/{n_total} = {n_by_tags_empty/n_total*100:.1f}%")
        print(f"  rejected_low_conf:  {n_rej_low_conf}/{n_total}")
        print(f"  rejected_no_meta:   {n_rej_no_meta}/{n_total}")
        print(f"  eligible:           {n_eligible}/{n_total} = {n_eligible/n_total*100:.1f}%")
        print("=" * 100)

    # Sanity: this is a diagnostic, all 60 queries should produce a result
    assert len(results) == 60
    for r in results:
        assert "eligible_count" in r
        assert "fail_safe_triggered" in r


# ────────────────────────────────────────────────────────────
# Layer 2 — Exact-tag vs natural-language controlled comparison
# ────────────────────────────────────────────────────────────


def test_s2_a_2_exact_tag_vs_natural_language(capsys):
    """S2-A-2: 20 pairs of exact-tag query vs natural-language query.

    Hypothesis: if retrieval/overlap is broken, exact-tag should hit,
    natural-language should miss.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    exact_results = []
    natural_results = []
    pair_results = []

    for i, (exact_q, natural_q) in enumerate(COMPARISON_PAIRS, 1):
        exact = _record_breakdown(loader, exact_q, agent_id, 0)
        natural = _record_breakdown(loader, natural_q, agent_id, 0)
        exact_results.append(exact)
        natural_results.append(natural)
        pair_results.append({
            "pair_id": i,
            "exact_query": exact_q,
            "natural_query": natural_q,
            "exact_candidates": exact["candidate_count"],
            "natural_candidates": natural["candidate_count"],
            "exact_eligible": exact["eligible_count"],
            "natural_eligible": natural["eligible_count"],
            "exact_tags": exact["query_tags"],
            "natural_tags": natural["query_tags"],
        })

    n = len(pair_results)
    n_exact_hit = sum(1 for p in pair_results if p["exact_candidates"] > 0)
    n_natural_hit = sum(1 for p in pair_results if p["natural_candidates"] > 0)
    n_exact_eligible = sum(1 for p in pair_results if p["exact_eligible"] > 0)
    n_natural_eligible = sum(1 for p in pair_results if p["natural_eligible"] > 0)

    exact_hit_rate = n_exact_hit / n
    natural_hit_rate = n_natural_hit / n
    delta = exact_hit_rate - natural_hit_rate

    with capsys.disabled():
        print()
        print("=" * 100)
        print("M5.3-S2-A-1 Layer 2 — Exact-Tag vs Natural-Language Controlled Comparison")
        print(f"Pairs: {n}")
        print("=" * 100)
        for p in pair_results:
            print(f"\n  Pair P.{p['pair_id']}:")
            print(f"    EXACT  : {p['exact_query']!r}")
            print(f"      tags: {p['exact_tags']}")
            print(f"      candidates: {p['exact_candidates']}, eligible: {p['exact_eligible']}")
            print(f"    NATURAL: {p['natural_query']!r}")
            print(f"      tags: {p['natural_tags']}")
            print(f"      candidates: {p['natural_candidates']}, eligible: {p['natural_eligible']}")
        print()
        print("=" * 100)
        print("ROLLOUP")
        print(f"  Exact-tag hit rate:     {n_exact_hit}/{n} = {exact_hit_rate*100:.1f}%")
        print(f"  Natural-lang hit rate:  {n_natural_hit}/{n} = {natural_hit_rate*100:.1f}%")
        print(f"  Delta:                  {delta*100:+.1f}pp")
        print()
        print(f"  Exact-tag eligible:     {n_exact_eligible}/{n}")
        print(f"  Natural-lang eligible:  {n_natural_eligible}/{n}")
        print()
        if delta > 0.20:
            print("  VERDICT SIGNAL: Large delta → exact-tag clearly outperforms natural-language.")
            print("  → retrieval/overlap mismatch LIKELY.")
        elif delta > 0.05:
            print("  VERDICT SIGNAL: Moderate delta → partial mismatch.")
        else:
            print("  VERDICT SIGNAL: Small delta → retrieval works similarly for both.")
            print("  → mismatch NOT confirmed by this layer.")
        print("=" * 100)

    # Sanity: 20 pairs
    assert len(pair_results) == 20


# ────────────────────────────────────────────────────────────
# Layer 3 — Confidence isolation (controlled memories)
# ────────────────────────────────────────────────────────────


def test_s2_a_3_confidence_isolation():
    """S2-A-3: 3 controlled memories with same content but different confidence.

    Same deterministic tag overlap, only confidence varies.
    Expected:
      - 0.85 → eligible
      - 0.75 → eligible
      - 0.50 → rejected_low_confidence
    If this test PASSes, it proves confidence filter itself works correctly.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "memory"
        agent_id = "agent_s2_a3_test"

        store = V1Store(data_dir, agent_id)

        # Three memories with same content/tags, different confidence
        base_tags = ["s2_a3", "conf_test", "shared"]
        base_content = "s2 a3 confidence isolation test"

        mem_high = Memory(
            memory_id="s2_a3_high",
            agent_id=agent_id,
            content=base_content,
            tags=base_tags,
            created_at=time.time(),
            category="preference",  # threshold 0.75
            confidence=0.85,  # above threshold
        )
        mem_boundary = Memory(
            memory_id="s2_a3_boundary",
            agent_id=agent_id,
            content=base_content,
            tags=base_tags,
            created_at=time.time(),
            category="preference",
            confidence=0.75,  # exactly at threshold
        )
        mem_low = Memory(
            memory_id="s2_a3_low",
            agent_id=agent_id,
            content=base_content,
            tags=base_tags,
            created_at=time.time(),
            category="preference",
            confidence=0.50,  # below threshold
        )

        store.add(mem_high)
        store.add(mem_boundary)
        store.add(mem_low)

        loader = MemoryLoader(store=store, trace_log_path=None)
        query_tags = derive_query_tags("s2_a3 conf_test")
        result = loader.load(query_tags, agent_id)

        eligible = result["eligible_memories"]
        eligible_ids = {m.memory_id for m in eligible}

        candidates = result["trace"]["candidates"]
        cand_by_id = {c["memory_id"]: c for c in candidates}

        # Verify candidate generation: all 3 should appear
        assert len(candidates) == 3, (
            f"Expected 3 candidates, got {len(candidates)}. "
            f"Candidates: {candidates}"
        )

        # Verify confidence filter behavior
        # 0.85 → eligible
        assert "s2_a3_high" in eligible_ids, (
            f"0.85 confidence should be eligible. Eligible: {eligible_ids}"
        )
        assert cand_by_id["s2_a3_high"]["status"] == "selected", (
            f"0.85 confidence status should be 'selected', got "
            f"{cand_by_id['s2_a3_high']['status']}"
        )

        # 0.75 → eligible (boundary)
        assert "s2_a3_boundary" in eligible_ids, (
            f"0.75 confidence should be eligible (at threshold). Eligible: {eligible_ids}"
        )
        assert cand_by_id["s2_a3_boundary"]["status"] == "selected", (
            f"0.75 confidence status should be 'selected', got "
            f"{cand_by_id['s2_a3_boundary']['status']}"
        )

        # 0.50 → rejected_low_confidence
        assert "s2_a3_low" not in eligible_ids, (
            f"0.50 confidence should NOT be eligible. Eligible: {eligible_ids}"
        )
        assert cand_by_id["s2_a3_low"]["status"] == "rejected_low_confidence", (
            f"0.50 confidence status should be 'rejected_low_confidence', got "
            f"{cand_by_id['s2_a3_low']['status']}"
        )

        # eligible_count should be 2 (high + boundary)
        assert result["trace"]["eligible_count"] == 2, (
            f"Expected eligible_count=2, got {result['trace']['eligible_count']}"
        )


# ────────────────────────────────────────────────────────────
# Layer 4 — Negative (false-positive) tests
# ────────────────────────────────────────────────────────────


def test_s2_a_4_negative_no_false_positive(capsys):
    """S2-A-4: Negative tests — irrelevant queries should NOT return rem memories.

    Queries about: Python code, weather, random English, unrelated anime.
    Expected: candidate_count = 0 OR eligible_count = 0.

    This validates that any retrieval fix doesn't introduce false positives.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    negative_queries = [
        "Python 是 什麼 程式語言",
        "今天 天氣 怎麼 樣",
        "Eminem 唱過 哪些 歌",
        "Python list comprehension",
        "JavaScript async await",
        "Kubernetes 部署",
        "比特幣 價格",
        "歐洲 足球 賽事",
        "復仇者聯盟 劇情",
        "哈利波特 魔法",
    ]

    results = []
    for query in negative_queries:
        r = _record_breakdown(loader, query, agent_id, 0)
        results.append(r)

    n = len(results)
    n_with_cand = sum(1 for r in results if r["candidate_count"] > 0)
    n_eligible = sum(1 for r in results if r["eligible_count"] > 0)

    with capsys.disabled():
        print()
        print("=" * 100)
        print("M5.3-S2-A-1 Layer 4 — Negative / False-Positive Tests")
        print(f"Queries: {n}")
        print("=" * 100)
        for r in results:
            print(
                f"  '{r['query']}': tags={r['query_tags']} "
                f"candidates={r['candidate_count']} eligible={r['eligible_count']} "
                f"fail_safe={r['fail_safe_triggered']}"
            )
        print()
        print(f"  with_candidates: {n_with_cand}/{n}")
        print(f"  eligible:        {n_eligible}/{n}")
        if n_with_cand == 0 and n_eligible == 0:
            print("  → 0 false positives. Negative test PASSES.")
        else:
            print(f"  ⚠️  {n_with_cand} queries found candidates, {n_eligible} eligible.")
            print("  → May indicate over-broad matching (or normal jieba behavior).")
        print("=" * 100)

    # Negative test assertion: at most a small number should hit
    # (some jieba token matches with common words like 什麼 might happen)
    # But eligible should be 0 (none of these should pass confidence filter
    # for matching memory content)
    assert n_eligible == 0, (
        f"Negative queries should NOT produce eligible memories. "
        f"Got {n_eligible} eligible from irrelevant queries: "
        f"{[r['query'] for r in results if r['eligible_count'] > 0]}"
    )


# ────────────────────────────────────────────────────────────
# Layer 5 — Memory tag inspection (informational)
# ────────────────────────────────────────────────────────────


def test_s2_a_5_memory_tag_structure_inspection(capsys):
    """S2-A-5: Inspect the actual memory tag structure to confirm the
    hypothesis that memory tags are phrase-based.

    This is informational, not a pass/fail test.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    all_mem = v1_store.all()

    # Categorize tags by character length
    tag_lens = Counter()
    phrase_examples = []
    single_examples = []

    for m in all_mem:
        for tag in m.tags:
            tag_lens[len(tag)] += 1
            if len(tag) >= 3 and len(phrase_examples) < 10:
                phrase_examples.append((m.memory_id[:8], tag, m.content[:40]))
            if len(tag) <= 1 and len(single_examples) < 5:
                single_examples.append((m.memory_id[:8], tag, m.content[:40]))

    with capsys.disabled():
        print()
        print("=" * 100)
        print("M5.3-S2-A-1 Layer 5 — Memory Tag Structure Inspection")
        print(f"Total memories: {len(all_mem)}")
        print(f"Tag length distribution:")
        for length in sorted(tag_lens.keys()):
            print(f"  len={length}: {tag_lens[length]} tags")
        print()
        print("Phrase tag examples (len>=3):")
        for mid, tag, content in phrase_examples[:10]:
            print(f"  [{mid}] tag={tag!r} content={content!r}")
        print()
        print("Single-char tag examples:")
        for mid, tag, content in single_examples[:5]:
            print(f"  [{mid}] tag={tag!r} content={content!r}")
        print("=" * 100)

    # Sanity
    assert len(all_mem) > 0
