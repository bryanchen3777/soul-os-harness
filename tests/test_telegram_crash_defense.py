"""
tests/test_telegram_crash_defense.py
方案 C + D (2026-09-08) — 崩溃机制纵深防御测试
根因: watchdog 误判 → 双实例 → 10 bot 抢 token → 409 Conflict →
     ptb network_retry_loop (max_retries=-1 无限重试) → 重试风暴 →
     httpx 连接池过载 → Windows IOCP 损坏 → access violation

C: error_callback 检测 Conflict(409) 计数 ≥3 → fail-closed 停止该 bot polling
D: get_updates_request 限制连接池 (connection_pool_size=5)
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from telegram.error import Conflict, TelegramError
from telegram.request import HTTPXRequest

from src.io.channels.telegram import TelegramAdapter


# === 方案 C: 409 Conflict 重试限制 ===

def test_conflict_three_times_stops_polling():
    """Conflict 连续 3 次 → fail-closed 停止该 bot polling (updater.stop 被调度)。"""
    adapter = TelegramAdapter(tokens={"yua": "test"})
    app = MagicMock()
    adapter._apps["yua"] = app
    cb = adapter._make_error_callback("yua")
    exc = Conflict("Conflict: terminated by other getUpdates request")

    with patch("src.io.channels.telegram.asyncio.create_task") as mock_ct:
        cb(exc)
        cb(exc)
        # 2 次: 未停止, 只警告
        assert not mock_ct.called
        assert adapter._conflict_counts["yua"] == 2

        cb(exc)
        # 3 次: 调度 updater.stop()
        assert mock_ct.called
        coro = mock_ct.call_args[0][0]
        assert coro is not None
        # 计数重置
        assert adapter._conflict_counts["yua"] == 0


def test_conflict_count_resets_after_stop():
    """停止后计数重置, 后续 Conflict 重新计数。"""
    adapter = TelegramAdapter(tokens={"yua": "test"})
    app = MagicMock()
    adapter._apps["yua"] = app
    cb = adapter._make_error_callback("yua")
    exc = Conflict("Conflict")

    with patch("src.io.channels.telegram.asyncio.create_task"):
        for _ in range(3):
            cb(exc)
    assert adapter._conflict_counts["yua"] == 0

    # 重置后重新计数
    cb(exc)
    assert adapter._conflict_counts["yua"] == 1


def test_non_conflict_error_does_not_stop():
    """非 Conflict 异常 → 不计数、不停止 (保持默认行为)。"""
    adapter = TelegramAdapter(tokens={"yua": "test"})
    app = MagicMock()
    adapter._apps["yua"] = app
    cb = adapter._make_error_callback("yua")

    with patch("src.io.channels.telegram.asyncio.create_task") as mock_ct:
        cb(TelegramError("network error"))
        cb(TelegramError("timed out"))
        assert not mock_ct.called
        assert adapter._conflict_counts.get("yua", 0) == 0


def test_error_callback_is_sync():
    """error_callback 必须是同步函数 (ptb 文档明确, 不能是 coroutine function)。"""
    adapter = TelegramAdapter(tokens={"yua": "test"})
    cb = adapter._make_error_callback("yua")
    assert not asyncio.iscoroutinefunction(cb)


# === 方案 D: get_updates 连接池限制 ===

@pytest.mark.asyncio
async def test_builder_uses_get_updates_request():
    """start() 的 builder 链包含 get_updates_request(HTTPXRequest(connection_pool_size=5))。"""
    with patch("src.io.channels.telegram.ApplicationBuilder") as MockBuilder:
        builder = MockBuilder.return_value
        builder.token.return_value = builder
        builder.get_updates_request.return_value = builder
        app = MagicMock()
        app.initialize = AsyncMock()
        app.start = AsyncMock()
        app.updater.start_polling = AsyncMock()
        builder.build.return_value = app

        adapter = TelegramAdapter(tokens={"yua": "test"})
        await adapter.start(on_message=AsyncMock())

        # D: get_updates_request 被调用且传了 HTTPXRequest 连接池限制
        builder.get_updates_request.assert_called_once()
        req = builder.get_updates_request.call_args[0][0]
        assert isinstance(req, HTTPXRequest)
        assert req._client_kwargs["limits"].max_connections == 5

        # C: start_polling 传了 error_callback
        app.updater.start_polling.assert_called_once()
        kwargs = app.updater.start_polling.call_args[1]
        assert "error_callback" in kwargs
        assert callable(kwargs["error_callback"])
