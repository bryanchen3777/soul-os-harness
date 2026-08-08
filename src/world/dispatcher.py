"""
src/world/dispatcher.py — Soul OS M3.1 Phase C

WorldEventDispatcher: Source → Dispatcher → Injector 真正 routing 層。

Bry 拍板 2026-08-08 09:24 — M3.1 Phase C 派工:

Mission:
  在 M3.1 Phase A + Phase B 已建立的 Source / Injector / Registry 之上,
  新增一個**真正負責 routing 的 WorldEventDispatcher**。

Phase C 核心 routing contract:
    Source
        ↓
    Dispatcher.emit_and_inject()
        ↓
    build WorldEvent (Dispatcher 自己 build, 不走 source.emit_event())
        ↓
    observe priority (進 observation log, 不 routing)
        ↓
    await dispatcher._injector.inject(event)  ← 真正 delivery 在這
        ↓
    return event

Phase B 既有 routed path (保留, 兩條路徑並存):
    source.emit_event() → source._injector.inject() ← Phase B direct API

Phase C 新增 routed path:
    dispatcher.emit_and_inject() → dispatcher._injector.inject()

Phase C 三大職責:
    1. source registration
    2. priority observation
    3. event delivery to injector

Phase C 不做:
    - scheduler / cron / polling loop
    - queue / priority queue
    - state / dedup
    - scoring
    - retry
    - pub/sub bus
    - background task (asyncio.create_task / ensure_future)
    - 第二個 WorldEvent-like class
    - 修改 WorldEventSource ABC
    - 修改 WorldEventInjector Protocol signature
    - 把 priority 進 PerceptionScores / SCORE_WEIGHTS / accept_threshold
    - 修改 middleware.py / run_server.py / token_manager.py
    - 持有 WorldState

允許修改檔案: src/world/dispatcher.py (本檔)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base import WorldEventSource
from .injector import WorldEventInjector
from .perception import WorldEvent

logger = logging.getLogger("soul_os.world.dispatcher")


class WorldEventDispatcher:
    """
    M3.1 Phase C: Source 跟 Injector 之間的 routing 層。

    Bry 派工 2026-08-08 09:24:
    - 純 in-memory, 同步, 沒 background
    - 觀察 priority 但不 routing
    - failure propagate
    - capability detection 連接 source 跟 injector
    - 兩條路徑並存 (Phase B direct + Phase C routed)
    """

    def __init__(self, name: str = "default") -> None:
        """
        Args:
            name: dispatcher 識別名 (給 logging / debug, 不影響 routing 邏輯)
        """
        self.name = name
        self._sources: Dict[str, WorldEventSource] = {}
        self._injector: Optional[WorldEventInjector] = None
        self._observation_log: List[Dict[str, Any]] = []

    # ── source registration ──────────────────────────────────────

    def attach_source(self, source: WorldEventSource) -> None:
        """
        註冊 source 到 dispatcher。

        Phase C 邏輯:
        1. 存 source reference by source.source_id
        2. 如果 dispatcher 已有 injector:
           - 用 getattr(source, "set_injector", None) capability detection
           - 支援 set_injector 的 source → 呼叫 setter(injector) (Phase B direct API propagation)
           - 不支援 → 跳過 (不 raise)

        注意:
        - attach_source() 不啟動 source (lifecycle 是 Registry 職責)
        - set_injector() 只 propagate 給 source, 不影響 Dispatcher 自己的 routed delivery
        - 真正 Phase C delivery 是 dispatcher._injector, 不是 source._injector
        """
        source_id = source.source_id
        self._sources[source_id] = source
        logger.debug(
            f"[Dispatcher:{self.name}] attached source {source_id!r}"
        )

        # 如果 dispatcher 已有 injector, propagate 給 source (Phase B direct API 兼容)
        if self._injector is not None:
            self._propagate_injector_to_source(source, self._injector)

    def _propagate_injector_to_source(
        self,
        source: WorldEventSource,
        injector: Optional[WorldEventInjector],
    ) -> None:
        """
        透過 capability detection 把 injector 傳給 source。
        source 沒 set_injector method → 跳過 (不 raise)。
        set_injector() 內部 raise → log warning, 不 crash dispatcher。
        """
        setter = getattr(source, "set_injector", None)
        if not callable(setter):
            return
        try:
            setter(injector)
        except Exception as e:
            logger.warning(
                f"[Dispatcher:{self.name}] set_injector failed for "
                f"{source.source_id!r}: {type(e).__name__}: {e}"
            )

    # ── injector registration ──────────────────────────────────────

    def attach_injector(
        self,
        injector: Optional[WorldEventInjector],
    ) -> None:
        """
        設定 dispatcher 的 injector。

        然後 capability detection 把 injector 傳給所有已 attach sources
        (讓 Phase B direct API 也能 work)。

        Args:
            injector: WorldEventInjector 實作, 或 None (detach)

        Contract (Bry 派工 2026-08-08 09:24):
          - None = detach
          - 不影響已 attach sources 的 registration (sources 仍 in registry)
          - capability source 的 setter failure → log warning, 不 crash
          - 不 retry
        """
        self._injector = injector
        for source in self._sources.values():
            self._propagate_injector_to_source(source, injector)

        if injector is None:
            logger.info(
                f"[Dispatcher:{self.name}] injector detached "
                f"({len(self._sources)} source(s) still registered)"
            )
        else:
            logger.info(
                f"[Dispatcher:{self.name}] injector attached "
                f"(reaches {len(self._sources)} source(s))"
            )

    # ── emit_and_inject — Phase C 核心 routing ─────────────────────

    async def emit_and_inject(
        self,
        source_id: str,
        type: str,
        summary: str,
        novelty_id: str,
        data: Optional[Dict[str, Any]] = None,
        priority: int = 0,
    ) -> WorldEvent:
        """
        Phase C 核心 routing API: build WorldEvent → observe priority → await injector.

        Flow (Bry 派工 2026-08-08 09:24):
          1. 找 source by source_id
             - 不存在 → raise ValueError
          2. 檢查 self._injector
             - is None → raise RuntimeError (不 silent swallow)
          3. observe priority (進 observation log, 不 routing)
          4. build M3 WorldEvent (source 自動 = source_id)
             - 透過 WorldEvent.__post_init__ 自動 validate priority
          5. await self._injector.inject(event)
             - 失敗必須直接 propagate
             - 不 silent swallow, 不 retry, 不 fallback
          6. return event

        Args:
            source_id: 哪個 source emit (必須已 attach_source())
            type: 細分類型
            summary: 一句話客觀描述
            novelty_id: 同一事實識別 (M3 既有欄位, 沿用)
            data: optional 額外 payload, default = {}
            priority: 預設 0, 必須是 int (M3.1 Phase B contract)

        Returns:
            WorldEvent (M3 既有 class, 多 priority 欄位)

        Raises:
            ValueError: source_id 沒 register
            RuntimeError: injector 沒 attach
            Exception: injector.inject() raise (propagate 給 caller)
        """
        # 1. 找 source
        if source_id not in self._sources:
            raise ValueError(
                f"source_id {source_id!r} not attached to dispatcher {self.name!r}"
            )

        # 2. 檢查 injector
        if self._injector is None:
            raise RuntimeError(
                f"WorldEventDispatcher {self.name!r} has no injector attached; "
                f"call attach_injector() first"
            )

        # 3. observe priority (log only, 不 routing)
        self._observation_log.append({
            "source_id": source_id,
            "priority": priority,
            "type": type,
            "novelty_id": novelty_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        # 4. build M3 WorldEvent
        if data is None:
            data = {}
        event = WorldEvent(
            source=source_id,
            type=type,
            novelty_id=novelty_id,
            ts=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            data=data,
            priority=priority,
        )
        # __post_init__ 已自動 validate priority (Phase B contract)

        # 5. routed delivery (Phase C 真正 delivery 在這)
        # 必須 propagate, 不 silent swallow, 不 retry
        await self._injector.inject(event)

        # 6. return event
        return event

    # ── observation log accessor ────────────────────────────────────

    def get_observation_log(self) -> List[Dict[str, Any]]:
        """
        給 test / observability 看 priority observation 記錄。

        Returns:
            List of observation dicts, 每個 dict 包含:
              - source_id (str)
              - priority (int)
              - type (str)
              - novelty_id (str)
              - ts (str, ISO 8601 UTC)
        """
        return list(self._observation_log)

    # ── introspection helpers (test / debug) ────────────────────────

    def get_attached_source_ids(self) -> List[str]:
        """給 test / debug 看已 attach 的 source_ids。"""
        return list(self._sources.keys())

    def get_injector(self) -> Optional[WorldEventInjector]:
        """給 test / debug 看當前 injector (None = detached)。"""
        return self._injector

    def __repr__(self) -> str:
        return (
            f"<WorldEventDispatcher name={self.name!r} "
            f"sources={list(self._sources.keys())} "
            f"injector={'set' if self._injector is not None else 'None'}>"
        )
