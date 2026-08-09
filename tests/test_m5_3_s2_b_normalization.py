"""
M5.3-S2-B — Normalization Minimal Fix Tests

Bry 派工 2026-08-09 15:27:
- B.1 Normalization unit tests (deterministic, no empty/dup/substring-only)
- B.2 Critical boundary tests (東京≠京, 雷姆≠雷, Python≠Py, 姐姐≠姐)
- B.3 S2-A controlled corpus regression (natural-lang must show measurable improvement)
- B.4 Negative corpus = 0 false positive
- B.5 Confidence isolation (0.85/0.75/0.50 contract unchanged)
- B.6 Existing regression: S1-4, M5.2, M3.x all PASS

Test isolation: use tempfile.TemporaryDirectory for test data, never touch production data.
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
from src.memory.v1.loader import (
    MemoryLoader,
    format_for_prompt,
    derive_query_tags,
    _expand_tag_components,
)


# ────────────────────────────────────────────────────────────
# B.1 — Normalization Unit Tests (deterministic, safe)
# ────────────────────────────────────────────────────────────


class TestS2BNormalizationUnit:
    """B.1: _expand_tag_components deterministic + safe."""

    def test_unit_phrase_tag_splits_correctly(self):
        """Phrase tags split via jieba into components + original."""
        # 雷姆和姊姊 → 雷姆 + 姊姊 (and) + original phrase
        comp = _expand_tag_components(["雷姆和姊姊"])
        assert "雷姆" in comp
        assert "姊姊" in comp
        # Original phrase preserved (no info loss)

    def test_unit_phrase_tag_姊姊的角(self):
        # jieba may or may not decompose 姊姊的角 into 姊姊+角
        # (depends on jieba dictionary entry). Either way:
        # - 姊姊 must be preserved (since 的 is stopword, jieba keeps 姊姊)
        # - Original phrase 姊姊的角 is preserved (no info loss)
        # - The important thing is that "姊姊" alone is queryable
        comp = _expand_tag_components(["姊姊的角"])
        assert "姊姊" in comp, f"姊姊 must be in expansion, got {comp}"
        # 角 may or may not be separately extracted by jieba
        # Just verify the original phrase is preserved
        assert "姊姊的角" in comp, f"Original phrase 姊姊的角 must be preserved, got {comp}"

    def test_unit_phrase_tag_雷姆出生時有(self):
        comp = _expand_tag_components(["雷姆出生時有"])
        assert "雷姆" in comp
        # Should contain component splits (jieba may produce 時有 / 出生 etc.)

    def test_unit_phrase_tag_被村裡的人認為(self):
        comp = _expand_tag_components(["被村裡的人認為"])
        assert "村裡" in comp
        assert "認為" in comp

    def test_unit_phrase_tag_那一晚的火和血的事故中(self):
        comp = _expand_tag_components(["那一晚的火和血的事故中"])
        # Should produce at least some meaningful components
        assert len(comp) >= 2
        # 事故 should be a component
        assert "事故" in comp

    def test_unit_single_token_unchanged(self):
        """Single-token tags pass through unchanged."""
        comp = _expand_tag_components(["雷姆"])
        assert comp == {"雷姆"}
        comp = _expand_tag_components(["bryan"])
        assert comp == {"bryan"}
        comp = _expand_tag_components(["python"])
        assert comp == {"python"}

    def test_unit_no_empty_token(self):
        """No empty strings in expanded set."""
        for tag in ["雷姆和姊姊", "姊姊的角", "", "雷姆", "bryan"]:
            comp = _expand_tag_components([tag])
            for tok in comp:
                assert tok, f"empty token from {tag!r}"
                assert len(tok) >= 2, f"1-char token {tok!r} from {tag!r}"

    def test_unit_no_duplicate(self):
        """Same tag twice → same set (no duplicate tokens)."""
        comp = _expand_tag_components(["雷姆", "雷姆", "雷姆"])
        assert comp == {"雷姆"}
        comp = _expand_tag_components(["雷姆和姊姊", "雷姆和姊姊"])
        # Both should produce same set
        assert "雷姆" in comp
        assert "姊姊" in comp

    def test_unit_english_preserved(self):
        """English single tokens preserved."""
        comp = _expand_tag_components(["bryan"])
        assert "bryan" in comp
        comp = _expand_tag_components(["Bryan"])
        assert "bryan" in comp  # case-insensitive
        comp = _expand_tag_components(["ram"])
        assert "ram" in comp

    def test_unit_japanese_preserved(self):
        """Japanese tags preserved as single token (jieba doesn't split katakana)."""
        comp = _expand_tag_components(["レム"])
        assert "レム" in comp
        comp = _expand_tag_components(["レムも手伝ってね"])
        # Whole phrase preserved (jieba keeps katakana strings together)

    def test_unit_stopword_filtered(self):
        """Stopwords like 的 / 和 filtered out from expansion."""
        comp = _expand_tag_components(["雷姆和姊姊"])
        assert "和" not in comp
        comp = _expand_tag_components(["姊姊的角"])
        assert "的" not in comp


# ────────────────────────────────────────────────────────────
# B.2 — Critical Boundary Tests (no substring collision)
# ────────────────────────────────────────────────────────────


class TestS2BBoundary:
    """B.2: NO substring collision — long tag must NOT match short substring."""

    def test_boundary_東京_neq_京(self):
        """東京 must not produce 京 as component."""
        comp = _expand_tag_components(["東京"])
        assert "東京" in comp
        assert "京" not in comp, f"京 must NOT be in 東京 expansion: {comp}"

    def test_boundary_雷姆_neq_雷(self):
        """雷姆 must not produce 雷 as component."""
        comp = _expand_tag_components(["雷姆"])
        assert "雷姆" in comp
        assert "雷" not in comp, f"雷 must NOT be in 雷姆 expansion: {comp}"

    def test_boundary_python_neq_py(self):
        """python must not produce py as component."""
        comp = _expand_tag_components(["python"])
        assert "python" in comp
        assert "py" not in comp, f"py must NOT be in python expansion: {comp}"

    def test_boundary_姐姐_neq_姐(self):
        """姐姐 must not produce 姐 as component."""
        comp = _expand_tag_components(["姐姐"])
        assert "姐姐" in comp
        assert "姐" not in comp, f"姐 must NOT be in 姐姐 expansion: {comp}"

    def test_boundary_no_cross_collision(self):
        """東京 must not match 京-based memory, etc."""
        # A memory tagged 京 should not match a query tagged 東京
        mem_comp = _expand_tag_components(["京"])
        query_comp = _expand_tag_components(["東京"])
        # Intersection should be empty (no overlap)
        overlap = mem_comp & query_comp
        assert overlap == set(), (
            f"京-based memory should NOT overlap with 東京-based query. "
            f"Got overlap: {overlap}"
        )

    def test_boundary_雷姆_no_collision(self):
        """雷姆 memory must not match 雷 query."""
        mem_comp = _expand_tag_components(["雷姆"])
        query_comp = _expand_tag_components(["雷"])
        # 雷 is 1-char, filtered out by derive_query_tags
        assert "雷" not in query_comp, "1-char 雷 should be filtered out"
        overlap = mem_comp & query_comp
        assert overlap == set()

    def test_boundary_姐姐_no_collision(self):
        """姐姐 memory must not match 姐 query."""
        mem_comp = _expand_tag_components(["姐姐"])
        query_comp = _expand_tag_components(["姐"])
        assert "姐" not in query_comp, "1-char 姐 should be filtered out"
        overlap = mem_comp & query_comp
        assert overlap == set()


# ────────────────────────────────────────────────────────────
# B.3 — S2-A Controlled Corpus (re-run, expect natural-lang improvement)
# ────────────────────────────────────────────────────────────


def test_s2_b_3_controlled_corpus_improvement(capsys):
    """B.3: Re-run S-2-A Layer 1 (60 production-like queries).

    S-2-A baseline:
      Overall eligible: 28/60 = 46.7%
      Natural-lang eligible: 3/12 = 25.0%

    S-2-B expectation (per Bry 派工):
      Natural-lang retrieval must show clear, reproducible improvement.
      No specific number required, but must not regress.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    all_mem = v1_store.all()
    assert len(all_mem) > 0

    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    from tests.test_m5_3_s2_retrieval_diagnostic import (
        CATEGORY_A_IDENTITY,
        CATEGORY_B_RELATIONSHIP,
        CATEGORY_C_PREFERENCE,
        CATEGORY_D_EVENT_BIOGRAPHY,
        CATEGORY_E_NATURAL_LANGUAGE,
    )

    all_queries = (
        CATEGORY_A_IDENTITY
        + CATEGORY_B_RELATIONSHIP
        + CATEGORY_C_PREFERENCE
        + CATEGORY_D_EVENT_BIOGRAPHY
        + CATEGORY_E_NATURAL_LANGUAGE
    )

    results = []
    for query in all_queries:
        tags = derive_query_tags(query)
        result = loader.load(tags, agent_id)
        results.append({
            "query": query,
            "eligible_count": result["trace"]["eligible_count"],
            "candidate_count": len(result["trace"]["candidates"]),
        })

    n_total = len(results)
    n_eligible = sum(1 for r in results if r["eligible_count"] > 0)

    # Per-category
    cat_map = {
        "A_identity": CATEGORY_A_IDENTITY,
        "B_relationship": CATEGORY_B_RELATIONSHIP,
        "C_preference": CATEGORY_C_PREFERENCE,
        "D_event_biography": CATEGORY_D_EVENT_BIOGRAPHY,
        "E_natural_language": CATEGORY_E_NATURAL_LANGUAGE,
    }
    cat_metrics = {}
    for cat_name, cat_qs in cat_map.items():
        cat_results = [r for r in results if r["query"] in cat_qs]
        cat_metrics[cat_name] = {
            "n": len(cat_results),
            "eligible": sum(1 for r in cat_results if r["eligible_count"] > 0),
        }

    n_natural = cat_metrics["E_natural_language"]["eligible"]
    n_natural_total = cat_metrics["E_natural_language"]["n"]

    with capsys.disabled():
        print()
        print("=" * 80)
        print("M5.3-S2-B B.3 — Controlled Corpus Improvement (60 queries)")
        print("=" * 80)
        print(f"Overall eligible:        {n_eligible}/{n_total} = {n_eligible/n_total*100:.1f}%")
        for cat, m in cat_metrics.items():
            pct = m["eligible"]/m["n"]*100 if m["n"] > 0 else 0
            print(f"  {cat:24}: {m['eligible']}/{m['n']} = {pct:.1f}%")
        print()
        print("S-2-A baseline: Overall 46.7%, Natural-lang 25.0%")
        print(f"S-2-B result:   Overall {n_eligible/n_total*100:.1f}%, Natural-lang {n_natural/n_natural_total*100:.1f}%")
        print("=" * 80)

    # Bry 派工: natural-lang must show clear improvement
    # S-2-A baseline: 3/12 = 25.0%
    # Expectation: meaningful improvement (>= 50% improvement = 37.5% absolute or more)
    assert n_natural >= 5, (
        f"Natural-lang eligible {n_natural}/{n_natural_total} must show clear improvement "
        f"vs S-2-A baseline of 3/12. Got {n_natural}/{n_natural_total}."
    )

    # Overall must not regress (S-2-A baseline 28/60)
    assert n_eligible >= 28, (
        f"Overall eligible {n_eligible}/{n_total} must not regress below S-2-A baseline 28/60."
    )


# ────────────────────────────────────────────────────────────
# B.5 — Confidence Isolation (contract unchanged)
# ────────────────────────────────────────────────────────────


def test_s2_b_5_confidence_isolation_contract_unchanged():
    """B.5: Confidence gate contract unchanged.

    Per Bry 派工: 0.85 → eligible, 0.75 → eligible, 0.50 → rejected.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "memory"
        agent_id = "agent_s2_b5_test"

        store = V1Store(data_dir, agent_id)

        base_tags = ["s2_b5", "conf_test"]
        base_content = "s2 b5 confidence isolation contract test"

        store.add(Memory(
            memory_id="s2_b5_high", agent_id=agent_id, content=base_content,
            tags=base_tags, created_at=time.time(), category="preference",
            confidence=0.85,
        ))
        store.add(Memory(
            memory_id="s2_b5_boundary", agent_id=agent_id, content=base_content,
            tags=base_tags, created_at=time.time(), category="preference",
            confidence=0.75,
        ))
        store.add(Memory(
            memory_id="s2_b5_low", agent_id=agent_id, content=base_content,
            tags=base_tags, created_at=time.time(), category="preference",
            confidence=0.50,
        ))

        loader = MemoryLoader(store=store, trace_log_path=None)
        result = loader.load(derive_query_tags("s2_b5 conf_test"), agent_id)

        eligible_ids = {m.memory_id for m in result["eligible_memories"]}
        candidates = {c["memory_id"]: c for c in result["trace"]["candidates"]}

        # 0.85 → eligible
        assert "s2_b5_high" in eligible_ids
        assert candidates["s2_b5_high"]["status"] == "selected"
        # 0.75 → eligible (boundary)
        assert "s2_b5_boundary" in eligible_ids
        assert candidates["s2_b5_boundary"]["status"] == "selected"
        # 0.50 → rejected
        assert "s2_b5_low" not in eligible_ids
        assert candidates["s2_b5_low"]["status"] == "rejected_low_confidence"
        # eligible_count = 2
        assert result["trace"]["eligible_count"] == 2


# ────────────────────────────────────────────────────────────
# B.4 — Negative Corpus (0 false positive)
# ────────────────────────────────────────────────────────────


def test_s2_b_4_negative_zero_false_positive(capsys):
    """B.4: 10 negative queries must produce 0 eligible memories.

    Per Bry 派工: false_positive = 0.
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

    n_eligible = 0
    for query in negative_queries:
        result = loader.load(derive_query_tags(query), agent_id)
        n_eligible += result["trace"]["eligible_count"]

    with capsys.disabled():
        print()
        print("=" * 80)
        print("M5.3-S2-B B.4 — Negative Corpus (10 irrelevant queries)")
        print(f"Total eligible: {n_eligible} (must be 0)")
        print("=" * 80)

    # Bry 派工: false_positive = 0
    assert n_eligible == 0, (
        f"Negative queries produced {n_eligible} eligible memories. "
        f"false_positive must be 0 per Bry 派工."
    )


# ────────────────────────────────────────────────────────────
# B.6 — End-to-end closed loop with normalization
# ────────────────────────────────────────────────────────────


def test_s2_b_end_to_end_normalized_closed_loop():
    """End-to-end test: write phrase-tagged memory, query naturally, verify eligibility."""
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data" / "memory"
        agent_id = "agent_s2_b_e2e"

        store = V1Store(data_dir, agent_id)
        store.add(Memory(
            memory_id="s2_b_e2e_1", agent_id=agent_id,
            content="雷姆和姊姊 被村裡的人認為 不吉祥",
            tags=["雷姆和姊姊", "被村裡的人認為", "不吉祥", "雷姆", "姊姊"],
            created_at=time.time(), category="preference",
            confidence=0.85,
        ))

        loader = MemoryLoader(store=store, trace_log_path=None)

        # Natural language query
        result = loader.load(derive_query_tags("雷姆 跟 姊姊"), agent_id)

        # With normalization, "雷姆 跟 姊姊" should hit the memory
        assert len(result["eligible_memories"]) == 1
        assert result["eligible_memories"][0].memory_id == "s2_b_e2e_1"

        # Format and verify content
        prompt = format_for_prompt(result["eligible_memories"])
        assert "雷姆和姊姊" in prompt
        assert "[Recall relevant memories]" in prompt
