"""
src/async_utils.py
Soul OS — 受管背景任務工具（止血工單 KI-007）

背景：
  M6.1-9.2 法醫審計定位出 production server 反覆 segfault 的根因分類為
  「uvicorn + anyio + asyncio.create_task fire-and-forget 的 C 擴展記憶體損壞」。
  fire-and-forget 的 `asyncio.create_task(...)` 不保存引用時，Task 可能在
  運行途中被 GC 回收，連帶提前釋放 anyio/httpcore 等 C 擴展仍在使用的
  同步原語物件 → python311.dll ACCESS VIOLATION (0xc0000005)。

修法（最小改動）：
  把 fire-and-forget 的 `asyncio.create_task(coro)` 換成
  `create_managed_task(coro)`，此函式：
    1. 保存強引用（Task 存活期間登記於模組級 _MANAGED_TASKS set，
       避免被 GC 提前回收）
    2. done 回調裡捕獲並記錄異常（避免「Task exception was never retrieved」
       靜默丟失，也避免異常在 GC 階段觸發二次存取）

範圍約束：
  - 不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent /
    4 handlers / SAGE 寫入邏輯）
  - 不重構 god-file（proxy.py / consciousness.py / scheduler.py）
  - 不改 handler / Agency / scheduler 架構
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger("soul_os.async_utils")

# 模組級強引用集合：受管 Task 存活期間都在此登記，防止被 GC 提前回收。
_MANAGED_TASKS: Set[asyncio.Task] = set()


def _on_managed_task_done(task: asyncio.Task) -> None:
    """done 回調：摘除強引用 + 捕獲異常（只記錄，不 raise）。"""
    _MANAGED_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "受管背景任務異常(未傳播) task=%s exc=%r",
            task.get_name(),
            exc,
        )


def create_managed_task(
    coro: Coroutine[Any, Any, Any],
    *,
    name: Optional[str] = None,
) -> asyncio.Task:
    """建立受管背景任務（asyncio.create_task 的引用安全版本）。

    與 asyncio.create_task 行為等價，但額外保證：
      - 強引用保存（不會被 GC 提前回收，避免 C 擴展記憶體損壞）
      - done 回調捕獲異常（不會靜默丟失）
    """
    task = asyncio.create_task(coro, name=name)
    _MANAGED_TASKS.add(task)
    task.add_done_callback(_on_managed_task_done)
    return task
