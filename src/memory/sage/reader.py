from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Callable, Literal, Optional

import networkx as nx

from .models import Fact, ContextResult
from .graph_store import GraphStore

MAX_TOKENS_DEFAULT = 800
CHARS_PER_TOKEN = 4
RecallMode = Literal["precise", "balanced", "expansive"]

DEFAULT_SCORE_WEIGHTS = {
    "weight":     0.40,
    "recency":    0.30,
    "relevance":  0.20,
    "confidence": 0.10,
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class MemoryReader:
    """Reader v4: sigmoid 正規化 + diversity penalty + retrieval feedback hook"""

    def __init__(
        self,
        graph_store: GraphStore,
        score_weights: Optional[dict[str, float]] = None,
        on_retrieved: Optional[Callable[[ContextResult], None]] = None,
    ):
        self.store = graph_store
        self.weights = score_weights or DEFAULT_SCORE_WEIGHTS
        self.on_retrieved = on_retrieved

    def retrieve_context(
        self,
        query: str,
        top_k: int = 5,
        max_hops: int = 2,
        max_tokens: int = MAX_TOKENS_DEFAULT,
        min_weight: float = 0.1,
        mode: RecallMode = "balanced",
        boost_tags: Optional[list[str]] = None,
    ) -> ContextResult:
        if self.store.edge_count == 0:
            return ContextResult(facts=[], chains=[], summary="",
                                 token_estimate=0)

        keywords = self._extract_keywords(query)
        if not keywords:
            return self._fallback_recent(top_k, max_tokens)

        candidates = self._gather_candidates(keywords, min_weight)
        if not candidates:
            return self._fallback_recent(top_k, max_tokens)

        scored = self._score_and_normalize(candidates, keywords, boost_tags)
        diverse = self._apply_diversity_filter(scored, top_k, mode)

        if mode == "precise":
            top_facts = diverse[:min(top_k, 3)]
            chains = []
        elif mode == "expansive":
            top_facts = diverse[:top_k * 2]
            chains = self._build_chains(keywords, max_hops=3)
        else:
            top_facts = diverse[:top_k]
            chains = self._build_chains(keywords, max_hops)

        summary = self._build_summary(top_facts, chains, max_tokens, mode)
        scores_map = {f.fact_id: getattr(f, "_score", 0.0) for f in top_facts}
        result = ContextResult(
            facts=top_facts,
            chains=chains,
            summary=summary,
            token_estimate=len(summary) // CHARS_PER_TOKEN,
            retrieval_scores=scores_map,
        )

        if self.on_retrieved:
            self.on_retrieved(result)

        return result

    # ── 評分 ──────────────────────────────────────────────────

    def _score_and_normalize(
        self,
        facts: list[Fact],
        keywords: list[str],
        boost_tags: Optional[list[str]],
    ) -> list[Fact]:
        now = time.time()
        kw_lower = [k.lower() for k in keywords]
        boost_lower = [b.lower() for b in (boost_tags or [])]
        raw_scores: list[float] = []

        for f in facts:
            subj_lower = f.subject.lower()
            subject_weight = 0.5 if "user" in subj_lower else 0.3
            age_days  = (now - f.timestamp) / 86400
            recency   = math.exp(-age_days / 30.0)
            text      = f"{f.subject} {f.predicate} {f.object}".lower()
            relevance = sum(1 for kw in kw_lower if kw in text) / max(len(kw_lower), 1)
            boost = 1.5 if any(b in text for b in boost_lower) else 1.0

            raw = (
                self.weights["weight"]     * f.weight +
                self.weights["recency"]    * recency +
                self.weights["relevance"]   * relevance +
                self.weights["confidence"]  * f.confidence +
                subject_weight
            ) * boost
            raw_scores.append(raw)

        if not raw_scores:
            return facts
        mean_s = sum(raw_scores) / len(raw_scores)
        std_s  = max(
            ((sum((s - mean_s) ** 2 for s in raw_scores) / len(raw_scores)) ** 0.5),
            0.1,
        )

        result = []
        for fact, raw in zip(facts, raw_scores):
            normalized = _sigmoid((raw - mean_s) / std_s)
            fact._score = normalized  # type: ignore[attr-defined]
            result.append(fact)

        return sorted(result, key=lambda f: f._score, reverse=True)  # type: ignore

    def _apply_diversity_filter(
        self,
        facts: list[Fact],
        top_k: int,
        mode: RecallMode,
    ) -> list[Fact]:
        max_per_subject = 2 if mode == "precise" else 3
        max_per_pair    = 1 if mode == "balanced" else 2
        subject_count: dict[str, int] = defaultdict(int)
        pair_count: dict[tuple[str, str], int] = defaultdict(int)
        selected: list[Fact] = []

        for fact in facts:
            subj = fact.subject.lower()
            pair_key = (subj, fact.predicate.lower())
            if subject_count[subj] >= max_per_subject:
                continue
            if pair_count[pair_key] >= max_per_pair:
                continue
            selected.append(fact)
            subject_count[subj] += 1
            pair_count[pair_key] += 1
            if len(selected) >= top_k * 2:
                break

        return selected

    # ── 關鍵詞提取 ────────────────────────────────────────────

    def _extract_keywords(self, query: str) -> list[str]:
        import re
        stopwords = {
            "what", "who", "where", "when", "how", "why", "is", "are",
            "was", "were", "the", "a", "an", "i", "you", "he", "she",
            "they", "we", "do", "did", "does", "tell", "me", "about",
            "know", "can", "could", "would", "should", "has", "have",
            "had", "been", "be", "my", "your", "his", "her", "its",
            "嗎", "的", "我", "你", "他", "她", "是", "在", "有", "了",
        }
        tokens = re.findall(r"[一-鿿]{2,}|[a-zA-Z]{2,}", query)
        return [t for t in tokens if t.lower() not in stopwords][:6]

    # ── 候選集 ────────────────────────────────────────────────

    def _gather_candidates(
        self, keywords: list[str], min_weight: float
    ) -> list[Fact]:
        seen_ids: set[str] = set()
        candidates: list[Fact] = []
        for kw in keywords:
            for fact in self.store.search_by_entity(kw, min_weight=min_weight):
                if fact.fact_id not in seen_ids:
                    candidates.append(fact)
                    seen_ids.add(fact.fact_id)
        return candidates

    # ── 多跳鏈 ────────────────────────────────────────────────

    def _build_chains(
        self, keywords: list[str], max_hops: int
    ) -> list[list[Fact]]:
        chains: list[list[Fact]] = []
        seen_chain_keys: set[frozenset] = set()
        for kw in keywords[:3]:
            ego = self.store.get_ego_graph(kw, radius=max_hops)
            if ego.number_of_edges() == 0:
                continue
            edges_sorted = sorted(
                ego.edges(data=True),
                key=lambda x: x[2].get("weight", 0),
                reverse=True,
            )[:6]
            chain: list[Fact] = []
            chain_key_parts: set[str] = set()
            for u, v, data in edges_sorted:
                fid = data.get("fact_id", "")
                if fid in chain_key_parts:
                    continue
                chain_key_parts.add(fid)
                chain.append(Fact(
                    subject=u,
                    predicate=data.get("predicate", "related_to"),
                    object=v,
                    timestamp=data.get("timestamp", 0.0),
                    weight=data.get("weight", 1.0),
                    source=data.get("source", "user"),
                    fact_id=fid,
                    session_id=data.get("session_id", ""),
                ))
            chain_key = frozenset(f.fact_id for f in chain)
            if chain and chain_key not in seen_chain_keys:
                chains.append(chain)
                seen_chain_keys.add(chain_key)
        return chains

    # ── Summary ───────────────────────────────────────────────

    def _build_summary(
        self, facts: list[Fact], chains: list[list[Fact]],
        max_tokens: int, mode: RecallMode,
    ) -> str:
        budget_chars = max_tokens * CHARS_PER_TOKEN
        lines: list[str] = []
        used = 0
        if facts:
            header = "## Recalled Memory"
            lines.append(header)
            used += len(header)
            for f in facts:
                score = getattr(f, "_score", f.weight)
                confidence_tag = (
                    "high"   if score >= 0.7 else
                    "medium" if score >= 0.4 else
                    "low"
                )
                line = (f"- [{confidence_tag}] "
                        f"{f.subject} {f.predicate} {f.object} "
                        f"(score={score:.2f})")
                if used + len(line) > budget_chars * 0.65:
                    lines.append(f"  ... (+{len(facts) - facts.index(f)} more)")
                    break
                lines.append(line)
                used += len(line)
        if chains and mode != "precise":
            header = "\n## Causal Chains"
            if used + len(header) < budget_chars:
                lines.append(header)
                used += len(header)
                for chain in chains[:2]:
                    parts = [
                        f"{f.subject}→[{f.predicate}]→{f.object}"
                        for f in chain[:4]
                    ]
                    line = "- " + " ⟹ ".join(parts)
                    if used + len(line) > budget_chars:
                        break
                    lines.append(line)
                    used += len(line)
        return "\n".join(lines)

    def _fallback_recent(self, top_k: int, max_tokens: int) -> ContextResult:
        facts = self.store.get_all_facts(min_weight=0.5)[:top_k]
        summary = self._build_summary(facts, [], max_tokens, "precise")
        return ContextResult(
            facts=facts, chains=[], summary=summary,
            token_estimate=len(summary) // CHARS_PER_TOKEN,
        )