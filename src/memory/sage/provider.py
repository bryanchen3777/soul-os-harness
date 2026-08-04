"""
SAGELiteProvider — vendored from hermes-sage-memory v0.1.3
Phase 2.0: 去掉 Hermes MemoryProvider ABC 相依，純化為 soul-os-harness 的記憶服務

保留的核心方法（給 MemoryMiddleware 用）：
  - initialize(profile_id, data_dir)  改用顯式 data_dir，不靠 hermes_home
  - prefetch(query, session_id)       sync 查詢，回傳可注入 prompt 的字串
  - sync_turn(user, assistant, sid)   sync 寫入
  - post_reply_commit(sid, user, ai)  async 寫入（內部用 run_in_executor）
  - system_prompt_block()             健康指標字串
  - shutdown()                        收尾

移除的 Hermes-only 方法：
  - get_tool_schemas / handle_tool_call   （Hermes tool API）
  - on_memory_write / on_pre_compress     （Hermes hook）
  - on_session_switch / on_session_end / on_turn_start
  - save_config / get_config_schema       （Hermes config UI）
  - get_write_health
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from .graph_store import GraphStore
from .writer import MemoryWriter
from .reader import MemoryReader
from .evolution import MemoryEvolution
from .models import Fact, ContextResult
from .token_utils import TokenBudget, SummaryCompressor, PrefetchCache

logger = logging.getLogger("soul_os.sage")

# Phase 7 — Ram (Re:Zero · COS v1.0) no-diary 差異化
# 任務書「Ram 沒有 feelings/diary.md」：soul-os-harness 的 SAGE 對應語意為
# 「Ram 的對話不寫入 graph.sqlite facts」，情感狀態由 emotional-state.json 表達。
# 注意：其他 agent 的 sync_turn/post_reply_commit 行為不受影響（回歸測試必跑）。
NO_DIARY_AGENTS: set[str] = {"agent_ram"}


class SAGELiteProvider:
    """soul-os-harness 相容的 SAGE-lite 記憶服務

    與 hermes-sage-memory v0.1.3 adapter.py 的差異：
    - 不繼承 Hermes MemoryProvider ABC
    - 移除所有 Hermes-only hooks
    - 路徑解析：data_dir 顯式傳入（不再依賴 ~/.hermes）
    - profile_id 直接是建構子參數
    """

    PROVIDER_NAME = "sage_lite"

    def __init__(
        self,
        profile_id: str = "default",
        data_dir: Optional[str] = None,
        top_k: int = 5,
        max_hops: int = 2,
        max_tokens: int = 800,
        recall_mode: str = "balanced",
    ):
        self.profile_id = profile_id
        self.data_dir = Path(data_dir) if data_dir else None
        self.top_k = top_k
        self.max_hops = max_hops
        self.max_tokens = max_tokens
        self.recall_mode = recall_mode
        self._session_id: str = ""
        self._store: Optional[GraphStore] = None
        self._writer: Optional[MemoryWriter] = None
        self._reader: Optional[MemoryReader] = None
        self._evolution: Optional[MemoryEvolution] = None
        self._turn_count: int = 0
        self._compressor = SummaryCompressor()
        self._cache = PrefetchCache(ttl_seconds=30.0, max_size=50)
        self._write_failures: list[dict] = []

    # ── 生命週期 ──────────────────────────────────────────────

    def initialize(self, session_id: str = "default") -> None:
        """Lazy-init components。session_id 可在之後切換。"""
        self._session_id = session_id
        self._init_components()

    def _db_path(self) -> Path:
        """每個 profile 獨立 graph.sqlite 檔。"""
        if self.data_dir is None:
            raise ValueError(
                "SAGELiteProvider.data_dir is not set; "
                "pass it to constructor or call initialize() with a data_dir"
            )
        return self.data_dir / "graph.sqlite"

    def _init_components(self) -> None:
        if self._store:
            self._store.close()
        self._store = GraphStore(db_path=self._db_path())
        # Bry 拍板 2026-07-18 Stage 1.6: 傳 profile_id 給 writer, 讓 v1 mirror 知道歸屬哪個 agent
        self._writer = MemoryWriter(
            self._store,
            default_session_id=self._session_id,
            agent_id=self.profile_id,
        )
        self._reader = MemoryReader(
            self._store,
            on_retrieved=self._on_memory_retrieved,
        )
        self._evolution = MemoryEvolution(self._store)

    def shutdown(self) -> None:
        if self._store:
            self._store.flush()
            self._store.close()

    # ── Prefetch（sync — MemoryMiddleware 會包 asyncio.to_thread）──

    def prefetch(
        self,
        query: str,
        *,
        session_id: str,
        boost_tags: Optional[list[str]] = None,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): source_pair 過濾白名單
        # 格式: set of "<user_id>:<agent_id>", 例 {"bryan:agent_ruka"}
        # reader 撈事實時, 過濾掉 source_pair 非空且不在這個 set 內的事實
        # (避免 ram/miku/yua 撈到 Bry-mai/Bry-ruka 私域喇稱)
        # None = 不過濾 (向後相容)
        # Bry 拍板防呆: 空 source_pair (既有資料) 一律視為可見, 不被過濾
        source_pair_filter: Optional[set[str]] = None,
    ) -> str:
        """查詢相關記憶，回傳 token-bounded 字串。

        Empty graph 或無匹配時回傳空字串。
        相同 query 在 TTL 內會走快取。
        """
        cached = self._cache.get(query)
        if cached is not None:
            return cached

        result = self._reader.retrieve_context(
            query,
            top_k=self.top_k,
            max_hops=self.max_hops,
            max_tokens=self.max_tokens,
            mode=self.recall_mode,
            boost_tags=boost_tags,
            source_pair_filter=source_pair_filter,
        )
        if result.is_empty:
            return ""

        budget = TokenBudget(self.max_tokens)
        summary = self._compressor.compress(result, budget)
        self._cache.set(query, summary)
        return summary

    def queue_prefetch(self, query: str, *, session_id: str) -> None:
        """背景 thread 版 prefetch（給非同步管線用，soul-os Phase 2 不一定會用到）。"""
        t = threading.Thread(
            target=self.prefetch,
            kwargs={"query": query, "session_id": session_id},
            daemon=True,
        )
        t.start()

    # ── 寫入（sync 與 async 兩種） ─────────────────────────────

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): 寫入帶 source_pair 標記
        source_pair: Optional[str] = None,
    ) -> None:
        """
        Sync 寫入。soul-os MemoryMiddleware 會包 asyncio.to_thread。

        Phase 7 — no-diary 白名單：agent_ram 的對話不寫入 graph.sqlite，
        情感狀態由 AgentConsciousness._on_session_end → emotional-state.json 表達。
        其他 agent 行為不受影響（回歸測試驗證）。
        """
        if not self._writer:
            return
        # Phase 7: NO_DIARY_AGENTS 白名單攔截
        if self.profile_id in NO_DIARY_AGENTS:
            logger.debug(
                f"[SAGE] sync_turn skipped (no-diary agent): profile={self.profile_id}"
            )
            return
        self._writer.write_turn(
            user_content, assistant_content,
            session_id=session_id, source_pair=source_pair,
        )
        self._turn_count += 1
        self._cache.invalidate()
        if self._turn_count % 20 == 0:
            self._evolution.run_scheduled_decay()
            self._evolution.auto_resolve_conflicts()

    async def post_reply_commit(
        self,
        session_id: str,
        last_user_msg: str,
        agent_reply: str,
        # 修法 1 (Bry 拍板 2026-08-03 22:xx, 方案 B): 寫入帶 source_pair 標記
        # middleware._on_agent_speak 從 event.payload 拿 target_user_id + agent_id 組成
        # 例: "bryan:agent_ruka" = Bry 跟 ruka 的對話事實
        source_pair: Optional[str] = None,
    ) -> None:
        """
        Async 寫入（內部已用 run_in_executor，不會阻塞 event loop）。

        這是 MemoryMiddleware 在 AGENT_SPEAK 階段呼叫的方法。

        Phase 7 — no-diary 白名單：與 sync_turn 對齊，agent_ram 跳過圖譜寫入。

        Bry 拍板 2026-07-18 Stage 2.1: NO_DIARY agents 仍跑 v1 mirror (skip_graph=True),
        理由: v1 mirror 是結構化備忘, 跟 diary (graph.sqlite) 是不同概念, Ram 不寫 diary
        仍可以有 v1 facts。
        """
        # Phase 7 + Bry 拍板 Stage 2.1: NO_DIARY_AGENTS 跳 graph 寫入, 但仍 mirror
        if self.profile_id in NO_DIARY_AGENTS:
            logger.debug(
                f"[SAGE] post_reply_commit no-diary: profile={self.profile_id} "
                f"(跳 graph 寫入, 仍 v1 mirror)"
            )
            loop = asyncio.get_event_loop()
            from functools import partial
            await loop.run_in_executor(
                None,
                partial(self._writer.write_turn, skip_graph=True, source_pair=source_pair),
                last_user_msg, agent_reply, session_id,
            )
            self._cache.invalidate()
            return
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._writer.write_turn,
            last_user_msg,
            agent_reply,
            session_id,
            source_pair,
        )
        self._cache.invalidate()

        if self._turn_count % 20 == 0:
            await loop.run_in_executor(
                None, self._evolution.run_scheduled_decay
            )
            await loop.run_in_executor(
                None, self._evolution.auto_resolve_conflicts
            )
        self._turn_count += 1

    # ── 健康指標 ──────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        if not self._store:
            return ""
        s = self._store.stats()
        return (
            f"[SAGE-lite Memory] "
            f"{s['active_facts']} active facts | "
            f"{s['node_count']} entities | "
            f"avg confidence {s['avg_weight']:.2f} | "
            f"profile: {self.profile_id}"
        )

    def stats(self) -> dict:
        """公開統計資訊，供 MemoryMiddleware / Dashboard 使用。"""
        if not self._store:
            return {"profile": self.profile_id, "active_facts": 0}
        s = self._store.stats()
        s["profile"] = self.profile_id
        return s

    # ── 內部 hook ─────────────────────────────────────────────

    def _on_memory_retrieved(self, result: ContextResult) -> None:
        """Post-retrieval hook: 低分 facts 自動輕微 decay。"""
        for fact in result.facts:
            score = result.retrieval_scores.get(fact.fact_id, 0.0)
            if score < 0.2 and not fact.is_anchor:
                self._evolution.apply_correction(
                    fact.fact_id, "decay",
                    delta=0.02,
                    reason="low_retrieval_score",
                )

    def get_write_health(self) -> dict:
        return {
            "total_write_failures": len(self._write_failures),
            "recent_failures": self._write_failures[-5:],
            "store_stats": self._store.stats() if self._store else {},
        }
