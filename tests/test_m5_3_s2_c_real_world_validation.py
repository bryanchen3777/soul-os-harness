"""
M5.3-S2-C — Post-Fix Real-World Retrieval Validation

Bry 派工 2026-08-09 15:36:
- 100+ production-like query corpus across 7 categories
- Verify S2-B normalization improvement persists in expanded corpus
- 5 gates: No Regression / NL Improvement / FP Safety / Observability / Production
- DO NOT modify production code
- DO NOT commit, DO NOT push
- DO NOT touch production data

Test isolation: read-only access to data/memory/agent_rem/. No writes.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.memory.v1.store import V1Store
from src.memory.v1.loader import MemoryLoader, derive_query_tags


# ────────────────────────────────────────────────────────────
# C2 — 100+ production-like query corpus (7 categories)
# ────────────────────────────────────────────────────────────

# A. Identity (12 queries)
CAT_A_IDENTITY = [
    "雷姆 是 什麼 種族",
    "雷姆 是 鬼族 嗎",
    "雷姆 的 種族",
    "雷姆 出生 時 有 什麼 特別",
    "雷姆 出生時 有 角 嗎",
    "雷姆 的 角 怎麼 來 的",
    "雷姆 小時候 是 怎樣 的",
    "雷姆 的 本名",
    "雷姆 跟 姊姊 有 什麼 不一樣",
    "雷姆 跟 姊姊 的 種族",
    "Ram 跟 雷姆 的 種族",
    "雷姆 是 怎樣 的 一個 人",
]

# B. Relationship (12 queries)
CAT_B_RELATIONSHIP = [
    "雷姆 跟 拉姆 是 什麼 關係",
    "雷姆 和 姊姊 的 關係",
    "雷姆 和 Ram 是 雙胞胎 嗎",
    "雷姆 跟 Ram 姐姐 的 關係",
    "雷姆 和 姊姊 以前 相處 怎麼樣",
    "她 跟 誰 住 在 一起",
    "她 和 羅茲瓦爾 是 什麼 關係",
    "雷姆 跟 昴 的 關係",
    "雷姆 跟 昴 怎麼 認識 的",
    "雷姆 對 昴 的 感情",
    "雷姆 跟 Bryan 的 關係",
    "雷姆 和 雷姆 有 什麼 連結",
]

# C. Preference (12 queries)
CAT_C_PREFERENCE = [
    "雷姆 喜歡 吃 什麼",
    "她 平常 喜歡 吃 什麼",
    "她 不 喜歡 什麼",
    "雷姆 有 什麼 習慣",
    "雷姆 平常 會 做 什麼",
    "雷姆 喜歡 誰",
    "雷姆 喜歡 Bryan 嗎",
    "雷姆 喜歡 在 什麼 時候 跟 Bryan 在一起",
    "雷姆 平常 都 做 什麼",
    "雷姆 最 喜歡 的 食物",
    "雷姆 喜歡 的 東西",
    "雷姆 有 什麼 偏好",
]

# D. Biography / Events (12 queries)
CAT_D_BIOGRAPHY = [
    "雷姆 小時候 發生過 什麼",
    "她 出生時 有 什麼 特別",
    "那次 事故 發生 了 什麼",
    "她 為什麼 會 變成 現在 這樣",
    "雷姆 過去 的 經歷",
    "雷姆 的 重要 事件",
    "雷姆 小時候 的 故事",
    "雷姆 小時候 的 創傷",
    "雷姆 為什麼 被 認為 不吉祥",
    "那 一天 發生 了 什麼 事",
    "雷姆 和 姊姊 小時候 被 怎樣 對待",
    "雷姆 和 姊姊 為什麼 被 村裡 的 人 討厭",
]

# E. Natural Conversational (22 queries — most important)
CAT_E_NATURAL = [
    "你 還 記得 雷姆 小時候 發生過 什麼 嗎",
    "雷姆 以前 是不是 有 一段 很 不 好 的 經歷",
    "她 跟 她 姊姊 以前 相處 得 怎麼 樣",
    "你 記得 她 為什麼 會 害怕 那 件 事 嗎",
    "雷姆 平常 最 喜歡 吃 什麼",
    "雷姆 是 個 怎樣 的 人",
    "雷姆 跟 你 講過 她 小時候 的 事 嗎",
    "雷姆 跟 昴 的 感情 是 怎樣 的",
    "雷姆 最 想 守護 的 人 是 誰",
    "雷姆 為什麼 願意 為 昴 犧牲",
    "雷姆 害怕 什麼",
    "雷姆 的 角 是 怎麼 斷 的",
    "雷姆 為什麼 不 喜歡 被 提起 小時候 的 事",
    "雷姆 跟 姊姊 為什麼 是 鬼族",
    "雷姆 跟 姊姊 小時候 被 怎樣 對待",
    "你 能 告訴 我 雷姆 跟 姊姊 小時候 發生 什麼 嗎",
    "雷姆 現在 住 哪裡",
    "雷姆 跟 Ram 姐姐 住 在 一起 嗎",
    "雷姆 的 姊姊 Ram 現在 在 哪裡",
    "雷姆 跟 你 最近 怎麼 樣",
    "雷姆 今天 想 做 什麼",
    "雷姆 平常 的 習慣 是 什麼",
]

# F. Paraphrase (15 queries — same intent, different phrasings)
CAT_F_PARAPHRASE = [
    "她 住 在 哪",
    "她 現在 住 什麼 地方",
    "她 平常 是 在 什麼 地方 生活",
    "她 的 居住地 是 哪裡",
    "她 住 羅茲瓦爾 公館 嗎",
    "雷姆 的 住處",
    "雷姆 的 住所",
    "她 喜歡 吃 什麼",
    "她 最 愛 的 食物",
    "她 的 飲食 偏好",
    "雷姆 喜歡 的 食物",
    "雷姆 愛 吃 什麼",
    "她 跟 誰 最 親近",
    "對 她 來說 最 重要 的 人 是 誰",
    "她 的 姊姊 是 誰",
]

# G. Negative (22 queries — must NOT match Rem memories)
CAT_G_NEGATIVE = [
    "Python 怎麼 安裝",
    "今天 紐約 天氣 如何",
    "Kubernetes 怎麼 部署",
    "Eminem 的 歌曲 有 哪些",
    "比特幣 現在 多少 錢",
    "復仇者聯盟 的 劇情",
    "哈利波特 的 魔法 有 哪些",
    "JavaScript async await 怎麼 用",
    "React useState 怎麼 用",
    "機器學習 入門",
    "深度學習 的 原理",
    "iPhone 最新 型號",
    "Tesla 電動車 價格",
    "美國 總統 是 誰",
    "東京 的 天氣 如何",
    "貓咪 的 照片",
    "狗 的 品種",
    "咖啡 的 種類",
    "茶 的 歷史",
    "紅酒 的 產地",
    "棒球 規則",
    "籃球 賽事",
]

ALL_CORPUS = (
    CAT_A_IDENTITY
    + CAT_B_RELATIONSHIP
    + CAT_C_PREFERENCE
    + CAT_D_BIOGRAPHY
    + CAT_E_NATURAL
    + CAT_F_PARAPHRASE
    + CAT_G_NEGATIVE
)
# Total: 12 + 12 + 12 + 12 + 22 + 15 + 22 = 107

CATEGORY_MAP = {
    "A_identity": CAT_A_IDENTITY,
    "B_relationship": CAT_B_RELATIONSHIP,
    "C_preference": CAT_C_PREFERENCE,
    "D_biography": CAT_D_BIOGRAPHY,
    "E_natural": CAT_E_NATURAL,
    "F_paraphrase": CAT_F_PARAPHRASE,
    "G_negative": CAT_G_NEGATIVE,
}


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────


def _run_query(loader, query, agent_id):
    """Run a single query and return detailed breakdown per C5 spec."""
    tags = derive_query_tags(query)
    result = loader.load(tags, agent_id)
    trace = result["trace"]
    candidates = trace.get("candidates", [])

    status_counts = Counter(c.get("status", "unknown") for c in candidates)

    # C5 rejection breakdown classification
    n_candidates = len(candidates)
    if n_candidates == 0:
        rejection_class = "NO_CANDIDATE"
    else:
        n_low_conf = status_counts.get("rejected_low_confidence", 0)
        n_no_v11 = status_counts.get("rejected_no_v11_metadata", 0)
        n_selected = status_counts.get("selected", 0)
        n_other = n_candidates - n_low_conf - n_no_v11 - n_selected
        if n_selected > 0:
            rejection_class = "ELIGIBLE"
        elif n_no_v11 == n_candidates:
            rejection_class = "NO_V11_METADATA"
        elif n_low_conf == n_candidates:
            rejection_class = "LOW_CONFIDENCE"
        else:
            rejection_class = f"OTHER_REJECTION(low={n_low_conf},no_v11={n_no_v11},other={n_other})"

    return {
        "query": query,
        "query_tags": tags,
        "candidate_count": n_candidates,
        "selected_count": status_counts.get("selected", 0),
        "rejected_low_confidence": status_counts.get("rejected_low_confidence", 0),
        "rejected_no_v11_metadata": status_counts.get("rejected_no_v11_metadata", 0),
        "eligible_count": trace.get("eligible_count", 0),
        "by_tags_empty": 1 if n_candidates == 0 else 0,
        "fail_safe_triggered": trace.get("fail_safe_triggered"),
        "rejection_class": rejection_class,
    }


def _summarize(results):
    """Build summary stats for a list of query results."""
    n = len(results)
    n_with_candidates = sum(1 for r in results if r["candidate_count"] > 0)
    n_eligible = sum(1 for r in results if r["eligible_count"] > 0)
    n_by_tags_empty = sum(r["by_tags_empty"] for r in results)
    n_rej_low = sum(r["rejected_low_confidence"] for r in results)

    rejection_classes = Counter(r["rejection_class"] for r in results)

    return {
        "n": n,
        "candidate_hit": n_with_candidates,
        "candidate_hit_rate": n_with_candidates / n if n > 0 else 0,
        "eligible": n_eligible,
        "eligible_rate": n_eligible / n if n > 0 else 0,
        "by_tags_empty": n_by_tags_empty,
        "by_tags_empty_rate": n_by_tags_empty / n if n > 0 else 0,
        "rejected_low_confidence": n_rej_low,
        "rejection_classes": dict(rejection_classes),
    }


# ────────────────────────────────────────────────────────────
# Main test
# ────────────────────────────────────────────────────────────


def test_s2_c_real_world_validation_full(capsys):
    """S-2-C: 107 production-like queries across 7 categories.

    Captures retrieval metrics, rejection breakdown, per-category stats.
    """
    data_dir = Path("data/memory")
    agent_id = "agent_rem"

    v1_store = V1Store(data_dir, agent_id)
    all_mem = v1_store.all()
    assert len(all_mem) > 0

    loader = MemoryLoader(store=v1_store, trace_log_path=None)

    # Run all queries
    all_results = []
    for query in ALL_CORPUS:
        all_results.append(_run_query(loader, query, agent_id))

    # Per-category stats
    cat_stats = {}
    for cat_name, cat_queries in CATEGORY_MAP.items():
        cat_results = [r for r in all_results if r["query"] in cat_queries]
        cat_stats[cat_name] = _summarize(cat_results)

    # Overall summary
    overall = _summarize(all_results)

    # Negative-only stats (for Gate C)
    neg_results = [r for r in all_results if r["query"] in CAT_G_NEGATIVE]
    neg_stats = _summarize(neg_results)
    n_neg_eligible = sum(1 for r in neg_results if r["eligible_count"] > 0)
    neg_eligible_rate = n_neg_eligible / len(neg_results) if neg_results else 0

    # Natural-only stats (for Gate B)
    nat_results = [r for r in all_results if r["query"] in CAT_E_NATURAL]
    nat_stats = _summarize(nat_results)

    # Print diagnostic table
    with capsys.disabled():
        print()
        print("=" * 100)
        print("M5.3-S2-C Real-World Validation — 107 queries, 7 categories")
        print(f"Agent: {agent_id} | Total memories in store: {len(all_mem)}")
        print("=" * 100)

        print("\n--- Per-Category Stats ---")
        print(f"{'Category':<22} {'N':>4} {'CandHit':>10} {'Eligible':>10} {'ByTagsEmpty':>14} {'RejLowConf':>12}")
        for cat_name, stats in cat_stats.items():
            print(
                f"{cat_name:<22} "
                f"{stats['n']:>4} "
                f"{stats['candidate_hit']}/{stats['n']} = {stats['candidate_hit_rate']*100:>5.1f}%  "
                f"{stats['eligible']}/{stats['n']} = {stats['eligible_rate']*100:>5.1f}%  "
                f"{stats['by_tags_empty']:>3}/{stats['n']} = {stats['by_tags_empty_rate']*100:>5.1f}%  "
                f"{stats['rejected_low_confidence']:>5}"
            )

        print(f"\n--- OVERALL ---")
        print(f"  Total queries:           {overall['n']}")
        print(f"  Candidate hit:           {overall['candidate_hit']}/{overall['n']} = {overall['candidate_hit_rate']*100:.1f}%")
        print(f"  Eligible:                {overall['eligible']}/{overall['n']} = {overall['eligible_rate']*100:.1f}%")
        print(f"  by_tags_empty:           {overall['by_tags_empty']}/{overall['n']} = {overall['by_tags_empty_rate']*100:.1f}%")
        print(f"  rejected_low_confidence: {overall['rejected_low_confidence']}")

        print(f"\n--- Rejection Breakdown (C5) ---")
        for cls, count in sorted(overall["rejection_classes"].items(), key=lambda x: -x[1]):
            print(f"  {cls}: {count}")

        print(f"\n--- Natural-language (Gate B) ---")
        print(f"  candidate_hit: {nat_stats['candidate_hit']}/{nat_stats['n']} = {nat_stats['candidate_hit_rate']*100:.1f}%")
        print(f"  eligible:      {nat_stats['eligible']}/{nat_stats['n']} = {nat_stats['eligible_rate']*100:.1f}%")

        print(f"\n--- Negative (Gate C) ---")
        print(f"  false_positive (eligible): {n_neg_eligible}/{len(neg_results)} = {neg_eligible_rate*100:.1f}%")

        print()
        print("=" * 100)

    # ════════════════════════════════════════════════════════
    # C9 GATE EVALUATION
    # ════════════════════════════════════════════════════════

    gate_results = {
        "A_no_regression": "PENDING (regression in C7/C8)",
        "B_natural_lang_improvement": "PENDING",
        "C_false_positive_safety": "PENDING",
        "D_observability": "PENDING",
        "E_production_safety": "PENDING (verified in baseline)",
    }

    # Gate B: Natural-lang improvement persistence
    # Per Bry: natural-lang candidate hit >= 75% AND eligible >= 70%
    if nat_stats["candidate_hit_rate"] >= 0.75 and nat_stats["eligible_rate"] >= 0.70:
        gate_results["B_natural_lang_improvement"] = "PASS"
    else:
        gate_results["B_natural_lang_improvement"] = (
            f"FAIL (cand_hit={nat_stats['candidate_hit_rate']*100:.1f}%, "
            f"eligible={nat_stats['eligible_rate']*100:.1f}%)"
        )

    # Gate C: False positive safety (hard requirement = 0)
    if n_neg_eligible == 0:
        gate_results["C_false_positive_safety"] = "PASS"
    else:
        gate_results["C_false_positive_safety"] = f"FAIL ({n_neg_eligible} FPs)"

    # Gate D: Observability — all queries must be classifiable
    unknown_count = overall["rejection_classes"].get("OTHER_REJECTION(low=0,no_v11=0,other=0)", 0)
    # Plus any "unknown" status from candidates
    unknown_status = 0
    for r in all_results:
        for s in r.get("rejection_class", "").lower():
            if "unknown" in r["rejection_class"]:
                unknown_status += 1
                break
    if unknown_count == 0 and unknown_status == 0:
        gate_results["D_observability"] = "PASS"
    else:
        gate_results["D_observability"] = (
            f"PARTIAL (other={unknown_count}, unknown_status={unknown_status})"
        )

    # Gate E: Production safety — verified in baseline snapshot
    gate_results["E_production_safety"] = "PASS (verified in C1 baseline)"

    with capsys.disabled():
        print("\n--- Gate Summary ---")
        for gate, status in gate_results.items():
            print(f"  {gate}: {status}")

    # Sanity: test ran all 107 queries
    assert len(all_results) == 107
    for r in all_results:
        assert "rejection_class" in r
        assert "eligible_count" in r
