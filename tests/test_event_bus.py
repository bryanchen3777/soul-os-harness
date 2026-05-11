# test_event_bus.py
# Soul OS — Phase 1: 整合測試與使用範例
#
# 執行方式：
#   pip install pydantic
#   python test_event_bus.py

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus import SoulEventBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test")


# ─────────────────────────────────────────────
# 模擬各模組的 Handler
# ─────────────────────────────────────────────

async def memory_middleware_handler(event: SoulEvent) -> None:
    """模擬 Memory Middleware：攔截 USER_MESSAGE 和 AGENT_INTENT，查詢記憶"""
    logger.info(
        f"  [MemoryMiddleware] 收到 {event.event_type} | "
        f"from={event.source} | "
        f"text='{event.payload.get('text', '')}'"
    )
    # 實際實作中：這裡會查詢 SQLite FTS5，把記憶注入 Prompt


async def llm_proxy_handler(event: SoulEvent) -> None:
    """模擬 LLM Proxy：接收 LLM_REQUEST，呼叫外部 API"""
    logger.info(
        f"  [LLMProxy] 收到生成請求 | "
        f"correlation={event.correlation_id} | "
        f"prompt_preview='{str(event.payload.get('prompt', ''))[:40]}...'"
    )
    # 實際實作中：這裡會呼叫 OpenAI / Claude API


async def agent_ruka_handler(event: SoulEvent) -> None:
    """模擬 Agent 瑠夏：只接收廣播或私發給她的事件"""
    target_info = "（廣播）" if event.target == "broadcast" else "（私訊）"
    logger.info(
        f"  [Agent:瑠夏] 收到 {event.event_type} {target_info} | "
        f"from={event.source}"
    )


async def io_gateway_handler(event: SoulEvent) -> None:
    """模擬 I/O Gateway：接收 AGENT_SPEAK 和 AGENT_ACTION，送往外部"""
    if event.event_type == EventType.AGENT_SPEAK:
        text = event.payload.get("text", "")
        tts = event.payload.get("tts_enabled", False)
        logger.info(
            f"  [I/O Gateway] 輸出文字 | "
            f'text="{text}" | TTS={"開啟" if tts else "關閉"}'
        )
    elif event.event_type == EventType.AGENT_ACTION:
        action = event.payload.get("action", "")
        logger.info(f"  [I/O Gateway] 執行動作 | action={action}")


async def error_monitor_handler(event: SoulEvent) -> None:
    """模擬錯誤監控模組"""
    logger.warning(
        f"  [ErrorMonitor] 系統錯誤 | "
        f"module={event.payload.get('module')} | "
        f"msg={event.payload.get('message')}"
    )


# ─────────────────────────────────────────────
# 測試場景
# ─────────────────────────────────────────────

async def test_basic_routing(bus: SoulEventBus) -> None:
    """測試 1：基礎廣播路由"""
    logger.info("\n── 測試 1: 使用者傳訊息（廣播給所有訂閱者）──")

    user_msg = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="user_bryan",
        target="broadcast",
        priority=EventPriority.HIGH,
        payload={"text": "瑠夏你在嗎？", "platform": "app"},
        session_id="session_001",
    )
    await bus.publish(user_msg)
    await asyncio.sleep(0.1)  # 讓 Worker 有時間處理


async def test_point_to_point(bus: SoulEventBus) -> None:
    """測試 2：點對點私訊（只有 agent_ruka 收到）"""
    logger.info("\n── 測試 2: 私訊 Agent 瑠夏（點對點）──")

    private_msg = SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_ruka",
        target="user_bryan",        # 指定接收者，不廣播
        priority=EventPriority.NORMAL,
        payload={
            "text": "嘿嘿嘿，我贏了！不能拒絕喔",
            "action_tags": ["game_jump", "cute"],
            "tts_enabled": True,
        },
        session_id="session_001",
        correlation_id="some_prior_event_id",
    )
    await bus.publish(private_msg)
    await asyncio.sleep(0.1)


async def test_heartbeat_tick(bus: SoulEventBus) -> None:
    """測試 3：心跳 Tick（低優先級，有 TTL）"""
    logger.info("\n── 測試 3: Heartbeat Tick（低優先，60 秒後過期）──")

    tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        target="broadcast",
        priority=EventPriority.LOW,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        payload={"tick_count": 42, "elapsed_mins": 12.5},
    )
    await bus.publish(tick)
    await asyncio.sleep(0.1)


async def test_priority_ordering(bus: SoulEventBus) -> None:
    """
    測試 4：優先級排序驗證
    同時投入 LOW（Tick）+ HIGH（使用者訊息），驗證 HIGH 先被處理。
    """
    logger.info("\n── 測試 4: 優先級排序（HIGH 應在 LOW 之前處理）──")

    results: list[str] = []

    async def track_handler(event: SoulEvent) -> None:
        results.append(f"{event.priority}:{event.event_type}")

    # 暫時掛載追蹤用的 handler
    bus.subscribe("priority_tracker", track_handler)

    low_tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        priority=EventPriority.LOW,
        payload={"tick_count": 99},
    )
    high_msg = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="user_bryan",
        priority=EventPriority.HIGH,
        payload={"text": "緊急！"},
    )

    # 刻意先投入 LOW，後投入 HIGH
    await bus.publish(low_tick)
    await bus.publish(high_msg)
    await asyncio.sleep(0.2)

    bus.unsubscribe("priority_tracker")

    # 驗證結果
    if results:
        logger.info(f"  處理順序: {results}")
        high_before_low = any("HIGH" in r for r in results[:2])
        logger.info(f"  HIGH 是否優先被處理: {'✓ 通過' if high_before_low else '✗ 失敗'}")


async def test_expired_event(bus: SoulEventBus) -> None:
    """測試 5：過期事件應被自動丟棄"""
    logger.info("\n── 測試 5: 過期事件自動丟棄 ──")

    expired_tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        priority=EventPriority.LOW,
        # 設定已過期的時間（-1 秒）
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        payload={"tick_count": 0, "elapsed_mins": 999},
    )
    await bus.publish(expired_tick)
    await asyncio.sleep(0.1)
    logger.info("  過期事件已投入（預期不會觸發任何 handler）")


async def test_agent_action(bus: SoulEventBus) -> None:
    """測試 6：實體動作指令（為未來機器人鋪路）"""
    logger.info("\n── 測試 6: Agent 動作指令 ──")

    action = SoulEvent(
        event_type=EventType.AGENT_ACTION,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={"action": "servo_wave", "params": {"speed": 0.8, "repeat": 2}},
    )
    await bus.publish(action)
    await asyncio.sleep(0.1)


async def test_error_event(bus: SoulEventBus) -> None:
    """測試 7：系統錯誤事件路由至錯誤監控"""
    logger.info("\n── 測試 7: 系統錯誤事件 ──")

    error_event = SoulEvent(
        event_type=EventType.SYSTEM_ERROR,
        source="llm_proxy",
        target="broadcast",
        priority=EventPriority.CRITICAL,
        payload={
            "module": "llm_proxy",
            "error_type": "APIRateLimitError",
            "message": "OpenAI API 速率限制，已排入重試佇列",
        },
    )
    await bus.publish(error_event)
    await asyncio.sleep(0.1)


# ─────────────────────────────────────────────
# 主程式
# ─────────────────────────────────────────────

async def main() -> None:
    logger.info("=" * 55)
    logger.info("  Soul OS — Phase 1 Event Bus 整合測試")
    logger.info("=" * 55)

    bus = SoulEventBus()

    # ── 掛載所有模組訂閱者 ──
    bus.subscribe(
        "memory_middleware",
        memory_middleware_handler,
        event_filter={EventType.USER_MESSAGE, EventType.AGENT_INTENT},
    )
    bus.subscribe(
        "llm_proxy",
        llm_proxy_handler,
        event_filter={EventType.LLM_REQUEST},
    )
    bus.subscribe(
        "agent_ruka",
        agent_ruka_handler,
        target_filter="agent_ruka",   # 接收廣播或私發給她的事件
    )
    bus.subscribe(
        "io_gateway",
        io_gateway_handler,
        event_filter={EventType.AGENT_SPEAK, EventType.AGENT_ACTION},
    )
    bus.subscribe(
        "error_monitor",
        error_monitor_handler,
        event_filter={EventType.SYSTEM_ERROR},
    )

    # ── 啟動 Bus ──
    await bus.start()

    # ── 執行測試場景 ──
    await test_basic_routing(bus)
    await test_point_to_point(bus)
    await test_heartbeat_tick(bus)
    await test_priority_ordering(bus)
    await test_expired_event(bus)
    await test_agent_action(bus)
    await test_error_event(bus)

    # ── 最終統計 ──
    await asyncio.sleep(0.2)
    logger.info("\n── 統計快照 ──")
    stats = bus.get_stats()
    for key, value in stats.items():
        logger.info(f"  {key}: {value}")

    logger.info(f"\n  訂閱者清單: {bus.get_subscribers()}")

    await bus.stop()
    logger.info("\n✓ 所有測試完成")


if __name__ == "__main__":
    asyncio.run(main())
