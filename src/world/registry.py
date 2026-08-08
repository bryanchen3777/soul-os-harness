"""
src/world/registry.py — Soul OS M3.1 Phase A

WorldEventSourceRegistry。

Bry 拍板 2026-08-08 01:57 — M3.1 Phase A 派工:

Registry 負責:
  - register(source)
  - start_all()
  - stop_all()
  - health_snapshot()

Contract:
  register():
    - source_id 重複 → raise ValueError
    - register 本身不要 start source
  start_all():
    - 按 registration order 啟動
    - 每個 source 個別 try/except
    - 某一 source start failure 不得阻止其他 source start
    - 記錄 source status
  stop_all():
    - 所有 source 都嘗試 stop
    - 某一 source stop failure 不得阻止其他 source stop
    - idempotent
    - 記錄 source status
  health_snapshot():
    至少能回答:
      {
        source_id: {
          "status": "started" | "failed" | "stopped"
        }
      }

不要加入:
  - 複雜 metrics
  - scheduler
  - retry engine
  - background supervisor
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List

from .injector import WorldEventInjector
from .perception import WorldEvent
from .base import WorldEventSource

logger = logging.getLogger("soul_os.world.registry")


class SourceStatus(str, Enum):
    REGISTERED = "registered"     # 已被 register 但還沒 start
    STARTED = "started"            # 成功 start
    FAILED = "failed"              # start 拋 exception
    STOPPED = "stopped"            # 成功 stop
    STOP_FAILED = "stop_failed"    # stop 拋 exception (但仍視為 stopped)


class WorldEventSourceRegistry:
    """
    Lifecycle manager for WorldEventSources。

    不負責 routing (routing 走 WorldPerceptionMiddleware / Injector)。
    只管: register, start_all, stop_all, health_snapshot。

    Source 透過 WorldEventInjector 介面注入 events, 不直接接 bus。
    """

    def __init__(self, injector: WorldEventInjector):
        """
        Args:
            injector: WorldEventInjector Protocol 實作
                      (Phase A 是 WorldPerceptionMiddleware, future 是其他)
        """
        self._injector = injector
        self._sources: Dict[str, WorldEventSource] = {}
        self._status: Dict[str, SourceStatus] = {}

    # ── register ──────────────────────────────────────

    def register(self, source: WorldEventSource) -> None:
        """
        加入 source 到 registry, 但不啟動。

        Raises:
            ValueError: source_id 重複註冊
        """
        if source.source_id in self._sources:
            raise ValueError(
                f"source_id {source.source_id!r} already registered"
            )
        self._sources[source.source_id] = source
        self._status[source.source_id] = SourceStatus.REGISTERED
        logger.info(f"[SourceRegistry] registered {source.source_id!r}")

    # ── start_all ──────────────────────────────────────

    async def start_all(self) -> None:
        """
        啟動所有已註冊的 source (按 registration order)。

        Failure isolation: 一個 source start 拋 exception 不影響其他。
        失敗的 source status 為 FAILED, 不再 retry (Phase A 不加 retry engine)。
        """
        for source_id, source in list(self._sources.items()):
            try:
                await source.start()
                self._status[source_id] = SourceStatus.STARTED
                logger.info(f"[SourceRegistry] started {source_id!r}")
            except Exception as e:
                self._status[source_id] = SourceStatus.FAILED
                logger.warning(
                    f"[SourceRegistry] start failed for {source_id!r}: "
                    f"{type(e).__name__}: {e} — 其他 source 繼續 start"
                )

    # ── stop_all ───────────────────────────────────────

    async def stop_all(self) -> None:
        """
        關閉所有 source (idempotent)。

        Failure isolation: 一個 source stop 拋 exception 不影響其他。
        重複呼叫 stop_all 安全 (status 已經是 STOPPED 也不會 crash)。
        """
        for source_id, source in list(self._sources.items()):
            try:
                await source.stop()
                self._status[source_id] = SourceStatus.STOPPED
                logger.info(f"[SourceRegistry] stopped {source_id!r}")
            except Exception as e:
                self._status[source_id] = SourceStatus.STOP_FAILED
                logger.warning(
                    f"[SourceRegistry] stop failed for {source_id!r}: "
                    f"{type(e).__name__}: {e} — 其他 source 繼續 stop"
                )

    # ── health_snapshot ─────────────────────────────────

    def health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        給 observability: 每個 source 的 status。

        至少能回答 (Bry 派工):
            {
                source_id: {
                    "status": "started" | "failed" | "stopped"
                }
            }

        Returns:
            Dict[source_id, Dict[status_key, status_value]]
        """
        return {
            source_id: {"status": self._status[source_id].value}
            for source_id in self._sources
        }

    # ── introspection (for test / debug) ─────────────

    def registered_source_ids(self) -> List[str]:
        """給測試 / debug: 列出所有已註冊的 source_id。"""
        return list(self._sources.keys())

    def get_status(self, source_id: str) -> SourceStatus:
        """給測試 / debug: 拿單一 source 的 status。"""
        return self._status[source_id]
