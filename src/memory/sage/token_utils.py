"""
Token 優化工具集：
- TokenBudget：追蹤剩餘 token 預算
- SummaryCompressor：壓縮 facts 為高密度摘要
- PrefetchCache：短時 query cache（TTL）
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional

from .models import Fact, ContextResult

CHARS_PER_TOKEN = 4


# ── Token Budget ──────────────────────────────────────────────

class TokenBudget:
    """追蹤並強制執行 token 預算"""

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self._used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._used)

    @property
    def remaining_chars(self) -> int:
        return self.remaining * CHARS_PER_TOKEN

    def consume(self, text: str) -> bool:
        """嘗試消耗 text 的 token，超出預算回傳 False"""
        cost = len(text) // CHARS_PER_TOKEN + 1
        if cost > self.remaining:
            return False
        self._used += cost
        return True

    def estimate(self, text: str) -> int:
        return len(text) // CHARS_PER_TOKEN + 1

    def reset(self) -> None:
        self._used = 0

    def __repr__(self) -> str:
        return f"TokenBudget({self._used}/{self.max_tokens})"


# ── Summary Compressor ────────────────────────────────────────

class SummaryCompressor:
    """
    將 ContextResult 壓縮為高密度、token 受控的字串。
    三種壓縮策略：
      - group_by_subject：相同 subject 的 facts 合併為一行
      - strongest_chain_only：chains 只輸出最強一條
      - truncate_with_count：超出預算時顯示剩餘筆數而非截斷
    """

    def compress(
        self,
        result: ContextResult,
        budget: TokenBudget,
        include_chains: bool = True,
    ) -> str:
        if result.is_empty:
            return ""

        lines: list[str] = []

        # === 記憶區（按 subject 分組合併）===
        grouped = self._group_by_subject(result.facts)
        mem_header = "## Memory"
        if budget.consume(mem_header):
            lines.append(mem_header)

        shown = 0
        for subj, facts in grouped.items():
            line = self._format_subject_group(subj, facts)
            if budget.consume(line):
                lines.append(line)
                shown += 1
            else:
                remaining_count = len(grouped) - shown
                if remaining_count > 0:
                    lines.append(f"  ... (+{remaining_count} subjects)")
                break

        # === 因果鏈區（只輸出最強一條）===
        if include_chains and result.chains:
            best_chain = self._pick_strongest_chain(result.chains)
            if best_chain:
                chain_header = "\n## Key Chain"
                if budget.consume(chain_header):
                    lines.append(chain_header)
                    chain_line = self._format_chain(best_chain)
                    if budget.consume(chain_line):
                        lines.append(chain_line)

        return "\n".join(lines)

    def estimate_tokens(self, result: ContextResult) -> int:
        """快速估算 ContextResult 的 token 數（不實際壓縮）"""
        total_chars = sum(
            len(f"{f.subject} {f.predicate} {f.object}")
            for f in result.facts
        )
        for chain in result.chains[:1]:
            total_chars += sum(
                len(f"{f.subject}{f.predicate}{f.object}") for f in chain
            )
        return total_chars // CHARS_PER_TOKEN + 20  # 20 token header overhead

    # ── 內部方法 ──────────────────────────────────────────────

    def _group_by_subject(
        self, facts: list[Fact]
    ) -> dict[str, list[Fact]]:
        """按 subject 分組，同 subject 排在一起"""
        groups: dict[str, list[Fact]] = {}
        for f in facts:
            groups.setdefault(f.subject, []).append(f)
        # 按第一條 fact 的 weight 排序（最重要的 subject 優先）
        return dict(
            sorted(groups.items(),
                   key=lambda kv: max(f.weight for f in kv[1]),
                   reverse=True)
        )

    def _format_subject_group(self, subject: str, facts: list[Fact]) -> str:
        """
        單個 subject 的所有 facts 壓縮為一行：
        'Alice: likes coffee(0.9), lives_in NYC(0.8)'
        """
        parts = [
            f"{f.predicate} {f.object}({f.weight:.1f})"
            for f in sorted(facts, key=lambda f: f.weight, reverse=True)[:4]
        ]
        return f"- {subject}: {', '.join(parts)}"

    def _pick_strongest_chain(
        self, chains: list[list[Fact]]
    ) -> Optional[list[Fact]]:
        """選出平均 weight 最高的 chain"""
        if not chains:
            return None
        return max(
            chains,
            key=lambda c: sum(f.weight for f in c) / max(len(c), 1)
        )

    def _format_chain(self, chain: list[Fact]) -> str:
        """因果鏈格式：A→[rel]→B→[rel]→C"""
        parts = [f"{f.subject}→[{f.predicate}]→{f.object}" for f in chain[:4]]
        return "- " + " ⟹ ".join(parts)


# ── Prefetch Cache ─────────────────────────────────────────────

@dataclass
class _CacheEntry:
    summary: str
    created_at: float = field(default_factory=time.time)


class PrefetchCache:
    """
    短時 query → summary cache。
    相同 query 在 TTL 秒內回傳快取，避免重複圖遍歷。
    """

    def __init__(self, ttl_seconds: float = 30.0, max_size: int = 50):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._cache: dict[str, _CacheEntry] = {}

    def get(self, query: str) -> Optional[str]:
        key = self._key(query)
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.time() - entry.created_at > self.ttl:
            del self._cache[key]
            return None
        return entry.summary

    def set(self, query: str, summary: str) -> None:
        # LRU eviction：超過 max_size 時刪最舊的
        if len(self._cache) >= self.max_size:
            oldest_key = min(
                self._cache, key=lambda k: self._cache[k].created_at
            )
            del self._cache[oldest_key]
        self._cache[self._key(query)] = _CacheEntry(summary=summary)

    def invalidate(self) -> None:
        """有新 fact 寫入時清除所有快取"""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)

    def _key(self, query: str) -> str:
        return hashlib.md5(query.strip().lower().encode()).hexdigest()