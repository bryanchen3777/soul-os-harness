"""
MemoryMiddleware — Soul OS Phase 2.0
連接 Soul Event Bus 與 vendored SAGE-lite 圖譜記憶

設計：
  - 每個 agent_id 維護一個獨立的 SAGELiteProvider（獨立 graph）
  - 訂閱三種事件：
      USER_MESSAGE        → 暫存 user_text（key = (session_id, agent_id)）
      AGENT_INTENT        → prefetch + 注入 memory_context，re-publish 為 ENRICHED
      AGENT_SPEAK         → post_reply_commit 寫入 graph（全寫，含觀察）
  - prefetch 是 sync；用 asyncio.to_thread 包
  - post_reply_commit 是 async；直接 await
  - 避免 AGENT_INTENT 無限迴圈：re-publish 為新的 AGENT_INTENT_ENRICHED event type
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.memory.sage import SAGELiteProvider

# β2.1 (Bry 拍板 2026-08-02 21:48): LLMProxy 引用 (Optional, 給 type hint 用)
# 實際 import 延遲到 _maybe_generate_event 內部避免循環引用
# (LLMProxy 可能 import 跟 memory 相關的東西, 雖然目前沒, 但保險起見 lazy)

# Bry 拍板 2026-07-18 Stage 1.2: 把 format_for_prompt 拉到模組頂
# 之前 lazy import 區塊範圍太大, NameError 被 try/except 吞掉, Loader 永遠注入失敗
from src.memory.v1.loader import format_for_prompt, derive_query_tags

# Bry 拍板 2026-07-18 Stage 4.1: 整合 relationships (角色靜態關係圖)
# USER_MESSAGE 觸發 target_agent 對 Bry touch (+0.05)
# AGENT_SPEAK 觸發 speaker 對 session 內其他 agent touch (+0.02)
# 詳細設計: src/soul/relationships.py
# Stage 4.2 diary / 4.3 dynamic 互動不包含, 留待後續 stage
try:
    from src.soul.relationships import get_relationships_manager
    _RELATIONSHIPS_AVAILABLE = True
except ImportError as _rel_err:
    # 模組缺失不影響 prod, 但要明顯 log
    logger_init = logging.getLogger("soul_os.memory.middleware")
    logger_init.warning(
        f"[MemoryMiddleware] Stage 4.1 relationships 模組 import 失敗, "
        f"略過整合不影響 prod: {_rel_err}"
    )
    _RELATIONSHIPS_AVAILABLE = False

# Perplexity 拍板 (Bry 轉, 2026-07-02): Bry §14 最小端到端接線
# 只對 agent_rem 開啟 v1 Loader 注入, 其他 agent 走原路徑
# 不動 judge / writer, 不順手接其他 agent
#
# Bry 拍板 2026-07-18 Stage 3: Loader 白名單從單一值改 frozenset, 循序開啟
# - 順序 (Mavis 16:00 推論 + Bry 接受): Rem → Yua → Mahiru/Anna/Mai → Akane → Aoi/Miku → Ruka → Ram
# - Stage 3 第一步: Rem 已是 Perplexity 7/2 拍的預設, 加 Yua 為第二隻
# - 不一次開全部 9 隻: 觀察每隻命中數, persona 沒漏字, 才進下一隻
LOADER_ENABLED_FOR_AGENTS = frozenset({"agent_rem", "agent_yua"})

logger = logging.getLogger("soul_os.memory.middleware")


class MemoryMiddleware:
    """
    Bus subscriber，介接 SAGE-lite 圖譜記憶。

    每個 agent_id 一個 SAGELiteProvider（lazy init）。
    data_dir 結構：
        {data_dir}/{agent_id}/graph.sqlite
    """

    def __init__(
        self,
        bus: SoulEventBus,
        data_dir: str = "data/memory",
        # β2.1 (Bry 拍板 2026-08-02 21:48): 事件生成用 LLMProxy 參考
        # 沒傳就 skip 事件生成 (向後相容, 測試不依賴真實 LLM)
        llm_proxy: Optional["LLMProxy"] = None,
        # β2.1: 事件 jsonl 寫入路徑, 預設 data/events/{YYYY-MM-DD}.jsonl
        events_dir: str = "data/events",
    ):
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._providers: Dict[str, SAGELiteProvider] = {}
        # key = session_id → user_text（等 AGENT_SPEAK 配對寫入）
        # Phase 2 假設單 session 單 agent；Phase 4 多 agent 同一 session 時
        # 需改成 (session_id, agent_id) 並設計配對策略
        self._pending_user_text: Dict[str, str] = {}

        # β2.1: 事件生成跟寫檔依賴
        self._llm_proxy = llm_proxy
        self._events_dir = Path(events_dir)
        self._events_dir.mkdir(parents=True, exist_ok=True)

        # Phase 4：寫入節流，防止 N² 寫入爆炸
        # 多 agent 同時說話時（Speaker Token 釋放後 queue 觸發連發），
        # 同一 agent 5s 內的 AGENT_SPEAK 只寫一次
        self._last_commit: Dict[str, datetime] = {}
        self.COMMIT_COOLDOWN_SECS = 5.0

        # Perplexity Bry §14: Loader sidecar trace log
        # 給 Bry 事後核對 trace 跟 Rem 回應是否一致 (Bry §14 第 3 點)
        self._loader_trace_path = self.data_dir / "loader_trace.jsonl"
        self._loader_trace_path.parent.mkdir(parents=True, exist_ok=True)
        # Lazy Loader instance
        self._loader = None

        # Bry 拍板 2026-07-18 Stage 4.1: 角色靜態關係圖整合
        # session_agents: 追蹤該 session 內出現過的 agent, 給 AGENT_SPEAK 觸發
        # 角色之間關係用 (4.3 動態互動會更精細, 4.1 第一刀先靠 session 共現)
        self._session_agents: Dict[str, set] = {}
        # 角色對 Bry 關係靠 user_id (TG: 1696287850, web: bryan_test 等)
        # 統一視為 BRYAN_ENTITY_ID (Stage 4.1 簡化)
        self._relationships_manager = (
            get_relationships_manager(data_dir="data/soul")
            if _RELATIONSHIPS_AVAILABLE else None
        )

    def register(self) -> None:
        """向 Event Bus 註冊，開始接收三種事件。"""
        self.bus.subscribe(
            subscriber_id="memory_middleware",
            handler=self.handle_event,
            event_filter={
                EventType.USER_MESSAGE,
                EventType.AGENT_INTENT,
                EventType.AGENT_SPEAK,
            },
        )
        logger.info(
            f"[MemoryMiddleware] 已掛載，data_dir={self.data_dir} ✓"
        )

    def _get_provider(self, agent_id: str) -> SAGELiteProvider:
        """Lazy init：每個 agent 一個獨立 SAGELiteProvider。"""
        if agent_id not in self._providers:
            agent_dir = self.data_dir / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            provider = SAGELiteProvider(
                profile_id=agent_id,
                data_dir=str(agent_dir),
            )
            provider.initialize(session_id="default")
            self._providers[agent_id] = provider
            logger.info(f"[MemoryMiddleware] 建立新 provider for {agent_id}")
        return self._providers[agent_id]

    def _get_loader(self, agent_id: str):
        """Perplexity Bry §14: Lazy init v1 Loader (per-agent v1 store)。"""
        if self._loader is not None:
            return self._loader
        from src.memory.v1.store import V1Store
        from src.memory.v1.loader import MemoryLoader
        v1_data_dir = self.data_dir  # v1 store 跟 SAGE 共用 data_dir
        # per-agent v1 store (跟 SAGE 一樣, per-agent dir)
        v1_store = V1Store(v1_data_dir, agent_id)
        # 單一 loader 跨 agent 共享 (Loader 內 store 各自獨)
        # 但每個 agent loader 行為一樣, 共享 trace log
        self._loader = MemoryLoader(store=v1_store, trace_log_path=self._loader_trace_path)
        return self._loader

    def _derive_query_tags(self, query: str) -> List[str]:
        """Perplexity Bry §14 + §15: 委派給 loader 的共享 helper。

        Bry §15 spec: 直接複用同一份切詞邏輯 (不要重寫一份),
        loader.derive_query_tags 是 single source of truth,
        這裡只委派避免兩份不同步。
        """
        return derive_query_tags(query)

    def _append_loader_trace(
        self,
        event: SoulEvent,
        query: str,
        query_tags: List[str],
        load_result: dict,
        context_len: int,
    ) -> None:
        """Perplexity Bry §14: 寫 sidecar trace 給 Bry 事後核對 Rem 回應是否用到記憶。"""
        try:
            trace = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                # Bry 拍板 2026-07-18 Stage 3: sidecar trace 寫真實觸發的 agent_id
                # (Rem 預設, Yua 第二隻) 而非常量
                "agent_id": event.payload.get("agent_id", "unknown"),
                "session_id": event.session_id,
                "event_id": event.event_id,
                "query": query[:200],
                "query_tags": query_tags,
                "eligible_count": load_result.get("trace", {}).get("eligible_count", 0),
                "fail_safe_triggered": load_result.get("trace", {}).get("fail_safe_triggered"),
                "candidates": load_result.get("trace", {}).get("candidates", []),
                "context_len_after_loader": context_len,
            }
            with open(self._loader_trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[MemoryMiddleware] sidecar trace 寫入失敗: {e}")

    # ── 事件分派 ─────────────────────────────────────────────

    async def handle_event(self, event: SoulEvent) -> None:
        if event.event_type == EventType.USER_MESSAGE:
            await self._on_user_message(event)
        elif event.event_type == EventType.AGENT_INTENT:
            await self._on_agent_intent(event)
        elif event.event_type == EventType.AGENT_SPEAK:
            await self._on_agent_speak(event)

    async def _on_user_message(self, event: SoulEvent) -> None:
        """暫存 user_text，等對應 session 的 AGENT_SPEAK 來配對寫入。"""
        session_id = event.session_id or "_no_session"
        text = event.payload.get("text", "")
        if text:
            self._pending_user_text[session_id] = text
            logger.debug(
                f"[MemoryMiddleware] 暫存 user_text | session={session_id}"
            )

        # Bry 拍板 2026-07-18 Stage 4.1: target_agent 對 Bry touch
        # 4.1 範圍: 角色對 Bry 認知累積 (其他 agent 對 Bry 留待 4.3 動態互動)
        target_agent = event.payload.get("target_agent") or event.target
        if (
            self._relationships_manager
            and target_agent
            and target_agent != "broadcast"
            and target_agent.startswith("agent_")  # 排除 user 開頭的 source
        ):
            try:
                self._relationships_manager.on_user_message(target_agent)
            except Exception as _rel_err:
                # 「拒絕問, 強制讀」: 不影響 prod, 但要明顯 log
                logger.warning(
                    f"[MemoryMiddleware] Stage 4.1 relationships touch 失敗, "
                    f"不影響 prod: {_rel_err}"
                )

    async def _on_agent_intent(self, event: SoulEvent) -> None:
        """prefetch → 注入 memory_context → re-publish 為 AGENT_INTENT_ENRICHED。

        為什麼用新 event type 而不是 flag：避免 re-publish 造成無限迴圈
        （若 LLMProxy 跟 MemoryMiddleware 都訂閱 AGENT_INTENT，
         MemoryMiddleware 處理完 re-publish，自己又會收到）。
        """
        agent_id = event.payload.get("agent_id")
        if not agent_id:
            # 修 KI-006 前置:不再靜默吞掉,留可追蹤異常
            logger.warning(
                f"[MemoryMiddleware] _on_agent_intent event missing agent_id, "
                f"source={event.source!r}, session={event.session_id!r}"
            )
            agent_id = f"unknown:{event.source}"
        # Perplexity Bry §23 spec (2026-07-02) Bug 1 修法 (a):
        # - draft 優先 (Bry 真實 user 對話)
        # - draft 為空時 fallback 用 memory_query_hint (persona 設計的 RAG 模板)
        # - 之前順序顛倒導致 query 永遠是 "Bryan 最近需要什麼、雷姆做過什麼" (per Rem persona template)
        #   而不是 Bry 真實對話內容, Loader 永遠 fail-safe
        query = (
            event.payload.get("draft")
            or event.payload.get("memory_query_hint")
            or event.payload.get("text", "")
        )
        if not query:
            query = f"{agent_id} conversation"

        provider = self._get_provider(agent_id)

        # prefetch 是 sync；包進 thread executor 不阻塞 event loop
        context = await asyncio.to_thread(
            provider.prefetch, query, session_id=event.session_id or "default"
        )

        # Perplexity Bry §14: 最小端到端接線
        # Bry 拍板 2026-07-18 Stage 3: Loader 白名單改 frozenset (Rem + Yua)
        # 其他 agent 走原路徑, 不順手接
        # - 從 query 文字做極簡 tokenization 當 query_tags (lower-case split 空白)
        # - 跑 Loader.load 拿 eligible memories
        # - format_for_prompt 塞進 context 字串
        # - sidecar trace log 給 Bry 事後核對
        if agent_id in LOADER_ENABLED_FOR_AGENTS and query:
            # Bry 拍板 2026-07-18 Stage 1.2: 縮小 try/except 範圍
            # 之前整段 (含 _get_loader / derive_query_tags / format_for_prompt) 都被包,
            # NameError 被吞掉 Loader 永遠注入失敗。改為只包 loader.load() 呼叫,
            # 讓 import 錯誤跟 code bug 真的 crash 到主路徑 (不靜默)
            query_tags = self._derive_query_tags(query)
            try:
                loader = self._get_loader(agent_id)
                load_result = await asyncio.to_thread(
                    loader.load,
                    query_tags,
                    agent_id,
                )
            except Exception as _loader_err:
                # Loader 失敗不影響主路徑, 但要明顯 log
                logger.warning(
                    f"[MemoryMiddleware] v1 Loader 失敗, 不影響 {agent_id} 主路徑: {_loader_err}"
                )
                load_result = None

            if load_result is not None:
                eligible = load_result["eligible_memories"]
                if eligible:
                    # Bry 拍板 2026-07-18 Stage 1.3: eligible>0 時顯式 INFO log
                    # 方便 Bry 即時確認 Loader 真的有注入 (不用翻 sidecar trace)
                    loader_block = format_for_prompt(eligible)
                    context = (context + "\n\n" + loader_block).strip() if context else loader_block
                    logger.info(
                        f"[MemoryMiddleware] v1 Loader 注入 | "
                        f"agent={agent_id} | "
                        f"eligible={len(eligible)} | "
                        f"context_len={len(context)}"
                    )
                else:
                    # Loader 跑了但 fail-safe, 仍要 log 知道有觸發
                    logger.debug(
                        f"[MemoryMiddleware] v1 Loader fail-safe | "
                        f"agent={agent_id} | "
                        f"eligible=0 | "
                        f"fail_safe={load_result['trace'].get('fail_safe_triggered')}"
                    )
                # Sidecar trace: 給 Bry 事後核對 Rem 的回應是否用到記憶
                self._append_loader_trace(
                    event=event,
                    query=query,
                    query_tags=query_tags,
                    load_result=load_result,
                    context_len=len(context),
                )

        # 把記憶注入 payload，re-publish 為新事件
        event.payload["memory_context"] = context

        # β2.1 (Bry 拍板 2026-08-02 21:48): 事件背景生成 + 寫檔
        # 範圍限定 pilot: 僅 agent_akane + reason=heartbeat 觸發
        # 不符合條件或 LLMProxy 沒注入 → 靜默 skip, 不影響主路徑
        event_text = await self._maybe_generate_event(event)
        if event_text:
            event.payload["event"] = event_text
            event.payload["event_meta"] = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "agent_id": agent_id,
                "reason": event.payload.get("reason"),
                "model": (
                    self._llm_proxy.model
                    if self._llm_proxy is not None
                    else None
                ),
            }
            await self._write_event_log(event, event_text)

        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source=event.source,
            target=event.target,
            priority=event.priority,
            payload=event.payload,
            session_id=event.session_id,
            correlation_id=event.correlation_id or event.event_id,
        )
        await self.bus.publish(enriched)

        logger.info(
            f"[MemoryMiddleware] enrich | agent={agent_id} | "
            f"query='{query[:30]}' | context_len={len(context)}"
        )

    async def _on_agent_speak(self, event: SoulEvent) -> None:
        """AGENT_SPEAK 來了 → 配對暫存的 user_text → 寫入 graph。

        採「全寫」策略：包含其他 agent 的 speak 也寫進自己的 graph，
        建立社交記憶（Yua 的 graph 也會記得瑠夏說過什麼）。

        Phase 4 加節流：同 agent 5s 內只寫一次，防 N² 寫入爆炸。
        """
        agent_id = event.payload.get("agent_id")
        if not agent_id:
            # 修 KI-006 前置:不再靜默吞掉,留可追蹤異常
            logger.warning(
                f"[MemoryMiddleware] _on_agent_speak event missing agent_id, "
                f"source={event.source!r}, session={event.session_id!r}"
            )
            agent_id = f"unknown:{event.source}"
        session_id = event.session_id or "_no_session"

        # Bry 拍板 2026-07-18 Stage 4.1: 記 session 內 agent + 觸發角色之間 touch
        # 4.1 範圍: 角色之間共現會 confidence 微升
        # 4.3 動態互動會更精細 (LLM 抽 stance/情緒), 4.1 先用共現當底
        if self._relationships_manager and agent_id.startswith("agent_"):
            self._session_agents.setdefault(session_id, set()).add(agent_id)
            try:
                session_agents_list = list(self._session_agents[session_id])
                self._relationships_manager.on_agent_speak(
                    speaker_agent_id=agent_id,
                    session_agents=session_agents_list,
                )
            except Exception as _rel_err:
                logger.warning(
                    f"[MemoryMiddleware] Stage 4.1 relationships on_agent_speak 失敗, "
                    f"不影響 prod: {_rel_err}"
                )

        # Phase 4 節流：同 agent 在 COMMIT_COOLDOWN_SECS 內的 AGENT_SPEAK 跳過寫入
        now = datetime.now(timezone.utc)
        last = self._last_commit.get(agent_id)
        if last and (now - last).total_seconds() < self.COMMIT_COOLDOWN_SECS:
            logger.debug(
                f"[Memory] {agent_id} 寫入節流（距上次 {(now-last).total_seconds():.1f}s），跳過"
            )
            return
        self._last_commit[agent_id] = now

        # 配對同一 session 的 user_text
        user_text = self._pending_user_text.pop(session_id, "")

        agent_text = event.payload.get("text", "")
        if not agent_text:
            logger.debug(
                f"[MemoryMiddleware] AGENT_SPEAK 沒 text，跳過寫入 | "
                f"agent={agent_id}"
            )
            return

        provider = self._get_provider(agent_id)
        await provider.post_reply_commit(
            session_id, user_text, agent_text
        )
        logger.info(
            f"[MemoryMiddleware] 寫入 graph | agent={agent_id} | "
            f"user_len={len(user_text)} | agent_len={len(agent_text)}"
        )

        # Bry §11 shadow mode hook (2026-07-02):
        # 並行掛一條 v6 observation 路徑, 完全不改 prod 行為。
        # 包 try/except 確保 shadow 自己異常不影響 prod 路徑。
        try:
            from src.memory.shadow import maybe_observe
            await maybe_observe(
                text=agent_text,
                agent_id=agent_id,
                speaker=event.source or "",
                context=user_text,
                heuristic_facts=None,  # 現有 heuristic 由 provider 自己跑, 不傳入避免雙重計算
            )
        except Exception as _shadow_err:
            logger.warning(f"[MemoryMiddleware] shadow hook 異常,不影響 prod: {_shadow_err}")

    # ───────────────────────────────────────────────────────────
    # β2.1 (Bry 拍板 2026-08-02 21:48): 事件背景生成
    # 範圍限定 pilot: 僅 agent_akane + reason=heartbeat
    # 失敗靜默 skip, 不影響主路徑 (「拒絕問, 強制讀」原則)
    # ───────────────────────────────────────────────────────────

    async def _maybe_generate_event(
        self,
        event: SoulEvent,
    ) -> Optional[str]:
        """
        β2.1 事件生成 hook.

        Returns:
            事件描述字串 (一句話 + tag), 失敗/不符合條件 → None.
        """
        agent_id = event.payload.get("agent_id", "")
        reason = event.payload.get("reason", "")
        # β2.1 pilot 範圍: 僅 agent_akane + heartbeat
        if agent_id != "agent_akane" or reason != "heartbeat":
            return None
        if self._llm_proxy is None:
            return None

        # 組事件生成 prompt
        try:
            from src.llm.proxy import _format_event_timestamp
            current_time_str = _format_event_timestamp(event.timestamp)
        except Exception:
            current_time_str = "時間未知"

        mood = event.payload.get("mood", 0.0)
        # 從 emotion_engine 拿 intimacy (跟 consciousness.py 對齊)
        try:
            from src.agent.emotion import emotion_engine
            _m, intimacy = emotion_engine.get(agent_id)
        except Exception:
            intimacy = 50.0

        system_prompt = (
            "你是一個世界觀敘事者。基於以下角色狀態, "
            "生成一句「這角色現在的處境」描述 (10-30 字)。\n\n"
            f"角色: 黒川あかね (agent_akane)\n"
            f"當下時間: {current_time_str}\n"
            f"觸發原因: heartbeat\n"
            f"最近情緒: mood={mood:.2f}, intimacy={intimacy:.0f}\n\n"
            "請生成一行, 格式為:\n"
            "[場所:...] [對象:...] [情緒:...]  {一句話場景描述}\n\n"
            "要求:\n"
            "- 場所、對象、情緒 三個 tag 都要有\n"
            "- 一句話場景描述不超過 30 字\n"
            "- heartbeat 是輕量在場確認, "
            "場景描述要簡單、平淡、有「剛才還在/還沒離開」感\n"
            "- 用繁體中文"
        )
        user_prompt = "請生成。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            event_text = await self._llm_proxy.generate_event_text(
                messages, agent_id=agent_id
            )
            if event_text:
                logger.info(
                    f"[MemoryMiddleware] β2.1 事件生成成功 | "
                    f"agent={agent_id} content={event_text[:40]!r}"
                )
            return event_text
        except Exception as e:
            logger.warning(
                f"[MemoryMiddleware] β2.1 事件生成失敗, "
                f"不影響主路徑 | agent={agent_id} err={e}"
            )
            return None

    async def _write_event_log(
        self,
        event: SoulEvent,
        event_text: str,
    ) -> None:
        """
        β2.1: 寫事件到 data/events/{YYYY-MM-DD}.jsonl (Asia/Taipei 日期).
        用 asyncio.to_thread 避免阻塞 event loop.
        失敗靜默 log warning, 不影響主路徑.
        """
        import json as _json
        # β2.1 寫死 hours=8 改成從 src.timezone_utils 拿 LOCAL_TZ
        # (Bry 派工 2026-08-03 18:21: 統一時區來源, 不再各檔案 hardcode)
        from src.timezone_utils import LOCAL_TZ
        local_date = event.timestamp.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
        events_file = self._events_dir / f"{local_date}.jsonl"

        log_entry = {
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "agent_id": event.payload.get("agent_id"),
            "reason": event.payload.get("reason"),
            "content": event_text,
            "model": (
                self._llm_proxy.model
                if self._llm_proxy is not None
                else None
            ),
        }

        def _write_atomic() -> None:
            events_file.parent.mkdir(parents=True, exist_ok=True)
            with open(
                events_file, "a", encoding="utf-8", newline=""
            ) as f:
                f.write(
                    _json.dumps(log_entry, ensure_ascii=False) + "\n"
                )

        try:
            await asyncio.to_thread(_write_atomic)
            logger.info(
                f"[MemoryMiddleware] β2.1 事件已寫入 | "
                f"file={events_file.name} "
                f"agent={event.payload.get('agent_id')} "
                f"content={event_text[:40]!r}"
            )
        except Exception as e:
            logger.warning(
                f"[MemoryMiddleware] β2.1 事件寫入失敗, "
                f"不影響主路徑: {e}"
            )

    # ── 維護 ─────────────────────────────────────────────────

    def shutdown(self) -> None:
        """收尾所有 provider 的 SQLite connection。"""
        for agent_id, provider in self._providers.items():
            try:
                provider.shutdown()
                logger.info(f"[MemoryMiddleware] shutdown {agent_id}")
            except Exception as e:
                logger.error(
                    f"[MemoryMiddleware] shutdown {agent_id} 失敗: {e}"
                )
        self._providers.clear()
        self._pending_user_text.clear()

    def get_stats(self) -> Dict:
        """回傳所有 agent 的圖譜健康指標。"""
        return {
            agent_id: provider.stats()
            for agent_id, provider in self._providers.items()
        }
