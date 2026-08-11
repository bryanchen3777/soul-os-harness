# soul_event_bus.py
# Soul OS — Phase 1: 靈魂事件總線 (Soul Event Bus)
#
# 架構核心：
#   - 基於 asyncio.PriorityQueue，確保高優先事件不被低優先事件卡住
#   - 訂閱者（Subscriber）透過 handler 函數掛載，完全解耦
#   - 支援 broadcast（廣播）與 point-to-point（點對點）路由
#   - 過期事件自動丟棄，防止積壓的 Tick 造成行為風暴
#   - 每個事件的處理都帶有完整的錯誤隔離，單一 handler 崩潰不影響其他訂閱者

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable, Dict, List, Optional, Set

from src.eventbus.schema import EventPriority, EventType, SoulEvent

logger = logging.getLogger("soul_os.event_bus")


# ─────────────────────────────────────────────
# 1. 訂閱者類型定義
# ─────────────────────────────────────────────

# 非同步 handler 的型別簽名：接收一個 SoulEvent，回傳 None
EventHandler = Callable[[SoulEvent], Awaitable[None]]


@dataclass
class Subscriber:
    """
    掛載在 Event Bus 上的訂閱者。

    subscriber_id : 識別碼，方便 debug 與取消訂閱
    handler       : 非同步處理函數，Bus 呼叫此函數傳遞事件
    event_filter  : 只接收哪些 EventType（None = 接收全部）
    target_filter : 只接收 target 符合自身 ID 或 'broadcast' 的事件
    """
    subscriber_id: str
    handler: EventHandler
    event_filter: Optional[Set[EventType]] = None
    target_filter: Optional[str] = None  # 通常設為 agent_id，例如 "agent_ruka"


# ─────────────────────────────────────────────
# 2. PriorityQueue 的排序包裝器
# ─────────────────────────────────────────────

@dataclass(order=True)
class _QueueItem:
    """
    asyncio.PriorityQueue 按照 (priority, sequence) 排序。
    加入 sequence 是為了在優先級相同時保持 FIFO 順序（timestamp 不夠精準）。
    event 本身標記為不參與排序比較。
    """
    priority: int
    sequence: int
    event: SoulEvent = field(compare=False)


# ─────────────────────────────────────────────
# 3. 靈魂事件總線
# ─────────────────────────────────────────────

class SoulEventBus:
    """
    非同步優先級事件總線。

    使用方式：
        bus = SoulEventBus()
        bus.subscribe("heartbeat", heartbeat_handler, {EventType.USER_MESSAGE})
        await bus.start()
        await bus.publish(some_event)
        # ... 執行期間 ...
        await bus.stop()
    """

    def __init__(self, max_queue_size: int = 1000):
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._subscribers: List[Subscriber] = []
        self._sequence_counter: int = 0
        self._running: bool = False
        self._worker_task: Optional[asyncio.Task] = None

        # 統計數據（用於 Dashboard 監控）
        self._stats: Dict[str, int] = defaultdict(int)

    # ── 訂閱管理 ──────────────────────────────

    def subscribe(
        self,
        subscriber_id: str,
        handler: EventHandler,
        event_filter: Optional[Set[EventType]] = None,
        target_filter: Optional[str] = None,
    ) -> None:
        """
        向 Bus 註冊一個訂閱者。

        subscriber_id : 全系統唯一識別碼（例如 "agent_ruka"、"memory_middleware"）
        handler       : async def handler(event: SoulEvent) -> None
        event_filter  : 只處理哪些 EventType；傳入 None 表示接收全部類型
        target_filter : 只處理 target == target_filter 或 target == "broadcast" 的事件

        範例：
            # Memory Middleware 只關心進來的使用者訊息和 Agent 意圖
            bus.subscribe(
                "memory_middleware",
                memory_handler,
                event_filter={EventType.USER_MESSAGE, EventType.AGENT_INTENT},
            )

            # Agent 瑠夏只接收廣播或私發給她的事件
            bus.subscribe(
                "agent_ruka",
                ruka_handler,
                target_filter="agent_ruka",
            )
        """
        sub = Subscriber(
            subscriber_id=subscriber_id,
            handler=handler,
            event_filter=event_filter,
            target_filter=target_filter,
        )
        self._subscribers.append(sub)
        logger.info(f"[Bus] 訂閱者已掛載: {subscriber_id}")

    def unsubscribe(self, subscriber_id: str) -> None:
        """移除訂閱者"""
        before = len(self._subscribers)
        self._subscribers = [s for s in self._subscribers if s.subscriber_id != subscriber_id]
        removed = before - len(self._subscribers)
        logger.info(f"[Bus] 已移除訂閱者: {subscriber_id}（移除 {removed} 筆）")

    # ── 事件發布 ──────────────────────────────

    async def publish(self, event: SoulEvent) -> None:
        """
        將事件放入優先級佇列。

        這是非阻塞操作——發布者不等待事件被消費完成。
        事件的實際派發由 _worker 在背景處理。

        Failure modes (per M5.7-4 documentation hardening):

        1. Bus not started (self._running == False):
           - event dropped silently (no exception raised)
           - log warning (logger.warning)
           - caller continues normally
           - Reason: typical when caller publishes during shutdown window

        2. Queue full (asyncio.QueueFull from put_nowait):
           - event dropped silently (no exception raised)
           - log error (logger.error) — includes event_type, source, id
             so caller can identify which event was dropped
           - increment `dropped_queue_full` stat (observable via
             bus.get_stats())
           - caller continues normally
           - Reason: 1000-event queue overflow (theoretical only; current
             event rate << 16 events/s, queue cannot realistically fill)

        3. Other exceptions (event validation, bus state corruption, etc.):
           - PROPAGATE to caller (uncaught)
           - caller's responsibility: handle / log / continue
           - Per M5.7-4 + M5.7-3: Heartbeat's _loop body wraps publish()
             in try/except (catches Exception, NOT BaseException, so
             CancelledError still propagates correctly on shutdown)

        Architectural note:
        - We do NOT add timeout / retry / circuit-breaker (per M5.7-4
          "Do NOT add arbitrary timeout/retry infrastructure")
        - The current design is observable: dropped_queue_full stat +
          logger.error with event_type/source/id is enough for production
          monitoring without expanding scope
        - If queue-full becomes a real issue in the future, the fix
          should be a separate ticket (queue size increase OR
          subscriber backpressure, NOT a retry framework)
        """
        if not self._running:
            logger.warning(f"[Bus] 總線尚未啟動，事件被丟棄: {event.event_id}")
            return

        self._sequence_counter += 1
        item = _QueueItem(
            priority=event.priority if isinstance(event.priority, int) else event.priority.value,
            sequence=self._sequence_counter,
            event=event,
        )

        try:
            self._queue.put_nowait(item)
            self._stats["published"] += 1
            logger.debug(
                f"[Bus] 事件入隊 | type={event.event_type} "
                f"source={event.source} target={event.target} "
                f"priority={event.priority} id={event.event_id[:8]}"
            )
        except asyncio.QueueFull:
            self._stats["dropped_queue_full"] += 1
            # M5.7-4: explicit failure observability — include event_type
            # and source in error log so dropped events are traceable
            logger.error(
                f"[Bus] 佇列已滿，事件丟棄: type={event.event_type} "
                f"source={event.source} id={event.event_id[:8]} | "
                f"請檢查是否有訂閱者處理速度過慢"
            )

    # ── 生命週期 ──────────────────────────────

    async def start(self) -> None:
        """啟動 Bus Worker，開始消費佇列"""
        if self._running:
            logger.warning("[Bus] 總線已在運行中，重複啟動請求被忽略")
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker(), name="soul_event_bus_worker")
        logger.info("[Bus] 靈魂事件總線已啟動 ✓")

    async def stop(self, timeout: float = 5.0) -> None:
        """
        優雅停止 Bus：等待佇列清空（最多 timeout 秒），再取消 worker。
        """
        logger.info("[Bus] 正在停止總線，等待剩餘事件處理完畢...")
        self._running = False

        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"[Bus] 等待超時（{timeout}s），強制停止")

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        logger.info(
            f"[Bus] 總線已停止 | 統計: {dict(self._stats)}"
        )

    # ── 核心 Worker ───────────────────────────

    async def _worker(self) -> None:
        """
        背景消費者：不斷從 PriorityQueue 取出事件並派發給符合條件的訂閱者。

        每個 handler 在獨立的 Task 中執行，避免慢速 handler 阻塞整條隊列。
        """
        logger.info("[Bus Worker] 啟動，等待事件...")

        while self._running or not self._queue.empty():
            try:
                # 設定 timeout 讓 worker 可以定期檢查 _running 狀態
                item: _QueueItem = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            event = item.event

            # ── 過期檢查 ──
            if event.is_expired():
                self._stats["dropped_expired"] += 1
                logger.debug(
                    f"[Bus Worker] 過期事件丟棄: {event.event_type} "
                    f"id={event.event_id[:8]}"
                )
                self._queue.task_done()
                continue

            # ── 找出所有符合條件的訂閱者 ──
            matched = self._match_subscribers(event)
            self._stats["dispatched"] += 1

            sub_ids = [s.subscriber_id for s in matched]
            logger.info(f"[Bus] dispatch {event.event_type} target={event.target} -> {sub_ids}")
            if not matched:
                logger.debug(
                    f"[Bus Worker] 無訂閱者匹配: {event.event_type} "
                    f"target={event.target}"
                )
                self._queue.task_done()
                continue

            # ── 並發派發（每個 handler 獨立 Task）──
            dispatch_tasks = [
                asyncio.create_task(
                    self._safe_dispatch(sub, event),
                    name=f"dispatch_{sub.subscriber_id}_{event.event_id[:8]}"
                )
                for sub in matched
            ]
            await asyncio.gather(*dispatch_tasks)
            self._queue.task_done()

        logger.info("[Bus Worker] 已停止")

    def _match_subscribers(self, event: SoulEvent) -> List[Subscriber]:
        """
        根據事件的 event_type 和 target，篩選出應接收此事件的訂閱者。

        路由規則：
          1. 若訂閱者設定了 event_filter，事件類型必須在過濾集合中
          2. 若訂閱者設定了 target_filter：
             - 事件 target == "broadcast" → 接收（廣播給所有人）
             - 事件 target == subscriber 的 target_filter → 接收（點對點私訊）
             - 其他 → 不接收
        """
        matched = []
        for sub in self._subscribers:
            # 事件類型過濾
            if sub.event_filter and event.event_type not in sub.event_filter:
                continue

            # 目標路由過濾
            if sub.target_filter:
                if event.target != "broadcast" and event.target != sub.target_filter:
                    continue

            matched.append(sub)
        return matched

    async def _safe_dispatch(self, sub: Subscriber, event: SoulEvent) -> None:
        """
        帶錯誤隔離的 handler 呼叫。
        單一訂閱者的 handler 崩潰不會影響其他訂閱者。
        """
        try:
            await sub.handler(event)
            self._stats[f"handled_{sub.subscriber_id}"] += 1
        except Exception as e:
            self._stats["handler_errors"] += 1
            logger.error(
                f"[Bus] Handler 錯誤 | subscriber={sub.subscriber_id} "
                f"event={event.event_type} id={event.event_id[:8]} | "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )

    # ── 狀態查詢（供 Dashboard 使用）─────────

    def get_stats(self) -> Dict[str, int]:
        """回傳目前的統計快照"""
        return {
            **dict(self._stats),
            "queue_size": self._queue.qsize(),
            "subscriber_count": len(self._subscribers),
        }

    def get_subscribers(self) -> List[str]:
        """回傳目前所有訂閱者的 ID 清單"""
        return [s.subscriber_id for s in self._subscribers]
