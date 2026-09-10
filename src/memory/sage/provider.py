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
import time
from functools import partial
from pathlib import Path
from typing import Any, Optional

from .graph_store import GraphStore
from .writer import MemoryWriter
from .reader import MemoryReader
from .evolution import MemoryEvolution, REINFORCEMENT_DELTA
from .models import Fact, ContextResult
from .token_utils import TokenBudget, SummaryCompressor, PrefetchCache

logger = logging.getLogger("soul_os.sage")

# Phase 7 — Ram (Re:Zero · COS v1.0) no-diary 差異化
# 任務書「Ram 沒有 feelings/diary.md」：soul-os-harness 的 SAGE 對應語意為
# 「Ram 的對話不寫入 graph.sqlite facts」，情感狀態由 emotional-state.json 表達。
# 注意：其他 agent 的 sync_turn/post_reply_commit 行為不受影響（回歸測試必跑）。
NO_DIARY_AGENTS: set[str] = {"agent_ram"}

# EH-3 (Write-Side Assimilation): 句法定義標記 — 使用者本輪文本需含「解釋／定義性」
# 句法才觸發 assimilated 打標。純句法詞, 0 hardcoded tech keywords（無「插電／機器／
# 科技／電腦／家電」等實體詞 — 打標依據是「定義句的形式」而非「定義了什麼」）。
_ASSIMILATION_SYNTAX_MARKERS: tuple[str, ...] = (
    "就是", "是個", "是一个", "是一種", "是一种",
    "用來", "用来", "所謂", "意思是", "指的", "指的是",
)


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
        # M5.10-2: 先建 reader (跟 writer 共用同一 GraphStore), 再傳給 writer
        # 順序不可調換 — writer._memory_reader 需要 reader instance
        self._reader = MemoryReader(
            self._store,
            on_retrieved=self._on_memory_retrieved,
        )
        # Bry 拍板 2026-07-18 Stage 1.6: 傳 profile_id 給 writer, 讓 v1 mirror 知道歸屬哪個 agent
        # M5.10-2: 傳 _reader 讓 _extract_facts_llm 在 call LLM Judge 前先取 v1 context
        self._writer = MemoryWriter(
            self._store,
            default_session_id=self._session_id,
            agent_id=self.profile_id,
            memory_reader=self._reader,
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
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 跟 post_reply_commit 對齊, sync / async 兩條路徑都吃 canonical event_id
        inner_life_event_id: Optional[str] = None,
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
            inner_life_event_id=inner_life_event_id,
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
        # M5.5-2 (Bry 派工 2026-08-10): canonical InnerLifeEvent reference
        # 從 AGENT_SPEAK SoulEvent top-level field (M5.4-5.5 frozen) 透傳
        # Memory 是 consumer, 絕不 create_event() 建立新的 InnerLifeEvent
        # - proactive_dm 路徑 (M5.4-6.2): canonical event_id 從 executor 傳來
        # - USER_MESSAGE / heartbeat / 等路徑: None → MemoryWriter fallback synthetic UUID
        inner_life_event_id: Optional[str] = None,
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
            await loop.run_in_executor(
                None,
                partial(
                    self._writer.write_turn,
                    skip_graph=True,
                    source_pair=source_pair,
                    inner_life_event_id=inner_life_event_id,
                ),
                last_user_msg, agent_reply, session_id,
            )
            self._cache.invalidate()
            return
        loop = asyncio.get_event_loop()
        # MEM-WIRING-1 (P0 BUGFIX): run_in_executor 只收位置參數, 直接位置傳遞會把
        # source_pair（第 5 位置參數）錯綁成 write_turn 的第 4 位置參數 skip_graph；
        # 非空字串 truthy → skip_graph=True → 誤跳 graph.sqlite 萃取落庫。
        # 修法: partial 以 keyword 繫結 source_pair / inner_life_event_id,
        #       前三個位置參數 (user_content, assistant_content, session_id) 保留原序。
        # EH-3: 捕獲 write_turn 返回的新增 fact_id 清單（不可丟棄）供 post-commit hook。
        fact_ids = await loop.run_in_executor(
            None,
            partial(
                self._writer.write_turn,
                source_pair=source_pair,
                inner_life_event_id=inner_life_event_id,
            ),
            last_user_msg,
            agent_reply,
            session_id,
        )
        # EH-3 (Write-Side Assimilation): post-commit hook — 對「user 解釋句」萃取的
        # fact 打 assimilated/aware + learned_at。同步 GraphStore 呼叫維持在
        # run_in_executor 的 worker 執行緒內（不在主 asyncio thread 觸發 SQLite 寫入）。
        # Fail-silent: hook 內部任何例外僅 warning, 不中斷主流程。
        if fact_ids:
            await loop.run_in_executor(
                None,
                partial(
                    self._tag_explanatory_assimilations,
                    fact_ids,
                    last_user_msg,
                ),
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

    # ── EH-3: Write-Side Assimilation（寫側內化閉環）───────────────

    def _tag_explanatory_assimilations(
        self,
        fact_ids: list[str],
        user_text: str,
    ) -> None:
        """EH-3 post-commit hook：對「user 解釋句」萃取的 fact 打 assimilated 標記。

        同步方法, 由 post_reply_commit 以 run_in_executor 包覆在 worker 執行緒內
        執行（GraphStore.set_fact_dimensions 為同步 DB/圖更新, 維持 thread-affinity,
        不在主 asyncio thread 呼叫, 避免阻塞 event loop）。

        Filter chain（任一不滿足 → Early Return no-op）:
          1. 現代原生白名單（horizon.MODERN_NATIVE_AGENTS, 直接引用禁止重複維護）:
             白名單角色現代常識是固有常識, 維持預設 lived_experience。
          2. 特殊模式: 本輪 0 個新增 fact_ids（skip_graph / no-diary / 0 萃取
             天然為空）→ no-op。
          3. 句法定義標記: user 文本需含解釋／定義性句法（如「X 就是個……的箱子」）
             → 才觸發打標。日常使用句（如「我剛用氣炸鍋弄了豆腐」）不含定義詞 → 不打標。

        打標僅作用於 source == 'user' 的 fact（User-Only Source — 不對 Assistant
        自身回覆／推論萃取（source == 'inference'）的 fact 打標）:
          origin=assimilated / horizon_state=aware / learned_at=time.time() (float)。

        Fail-silent: 任何未預期例外僅記錄 logger.warning, 嚴禁中斷主流程或
        回滾既有 Graph 寫入。
        """
        # Gate 1: 現代原生白名單（契約 §6.1 D3 分流）
        try:
            from .horizon import is_modern_native
        except Exception as exc:  # noqa: BLE001 — fail-silent: import 失敗視為不 bypass
            logger.warning(
                f"[SAGE] EH-3 horizon import failed: {type(exc).__name__}: {exc}"
            )
            is_modern_native = None
        if is_modern_native is not None and is_modern_native(self.profile_id):
            logger.debug(
                f"[SAGE] EH-3 skip (modern native): profile={self.profile_id}"
            )
            return
        # Gate 2: 本輪無新增 fact（skip_graph / no-diary / 0 萃取）→ no-op
        if not fact_ids:
            return
        # Gate 3: 句法定義標記 — 解釋／定義性句法才打標（0 hardcoded tech keywords:
        # 依據是「定義句的形式」, 不是「定義了哪個科技實體」）
        if not any(marker in user_text for marker in _ASSIMILATION_SYNTAX_MARKERS):
            logger.debug(
                f"[SAGE] EH-3 no syntax clue, skip tagging: profile={self.profile_id}"
            )
            return
        if self._store is None:
            return
        try:
            for fact_id in fact_ids:
                fact = self._store.get_fact(fact_id)
                if fact is None or fact.source != "user":
                    # User-Only Source: assistant/inference fact 不打標
                    continue
                self._store.set_fact_dimensions(
                    fact_id=fact_id,
                    origin="assimilated",
                    horizon_state="aware",
                    learned_at=time.time(),  # float Unix timestamp (契約 §3.2)
                )
            # 強制 commit: set_fact_dimensions 只在 batch_size 達標時才自動 commit,
            # 讀側 Idiolect 檢索（retrieve_idiolect）開新 sqlite 連接, 未 commit 的
            # UPDATE 讀不到 → 閉環斷裂。flush 維持在 worker 執行緒內（@_locked 安全）。
            self._store.flush()
            logger.info(
                f"[SAGE] EH-3 assimilated tagging ok | profile={self.profile_id} | "
                f"synced={len(fact_ids)}"
            )
        except Exception as exc:  # noqa: BLE001 — fail-silent: 絕不中斷主流程
            logger.warning(
                f"[SAGE] EH-3 assimilated tagging failed "
                f"(profile={self.profile_id}): {type(exc).__name__}: {exc}"
            )

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
        """
        Post-retrieval hook (M7-forgetting 擴充, Bry 拍板 2026-08-19):
        高分 facts 強化、低分 facts 輕微 decay。

        呼應「越常想起越記得牢, 不再提起就淡忘」:
          - score >= 0.5 → reinforce (+REINFORCEMENT_DELTA)
          - score <  0.2 → decay (-0.02)
        anchor 不動 (固定保留)。
        """
        for fact in result.facts:
            if fact.is_anchor:
                continue
            score = result.retrieval_scores.get(fact.fact_id, 0.0)
            if score >= 0.5:
                self._evolution.apply_correction(
                    fact.fact_id, "reinforce",
                    delta=REINFORCEMENT_DELTA,
                    reason="high_retrieval_score",
                )
            elif score < 0.2:
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
