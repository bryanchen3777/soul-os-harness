"""
src/world/injector.py — Soul OS M3.1 Phase A

WorldEventInjector Protocol。

Bry 拍板 2026-08-08 01:57 — M3.1 Phase A 派工:

目的:
  WorldEventSource 不直接依賴 EventBus。
  Source 只知道 Injector。
  透過 Injector 注入 events → 達到 source 跟 bus / state 的隔離。

Phase A 範圍:
  - 定義 Protocol
  - WorldPerceptionMiddleware 未來 conform (Phase A 不改)
  - Phase A 測試用 mock injector
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .perception import WorldEvent


@runtime_checkable
class WorldEventInjector(Protocol):
    """
    WorldEvent 注入介面 (Protocol)。

    任何「能接收 WorldEvent 並送進 Soul OS World Awareness chain」的都 conform 這個 Protocol。
    Phase A 主要 implementation: WorldPerceptionMiddleware (process_world_event_direct)。
    Phase A 不強制 middleware 改名 / 改 signature — Protocol 是 contract, 不是
    binding requirement。

    Contract:
      - inject(event) 必須 async
      - event 必須是 WorldEvent 實例
      - 失敗 raise → caller (Source 內部 / Registry) 應 handle
    """

    async def inject(self, event: WorldEvent) -> None:
        """
        注入一個 WorldEvent 進 Soul OS World Awareness chain。

        對 WorldPerceptionMiddleware: 進 validation → state → trace。
        對其他實作: 可能是 mock / test double / 別的 listener。
        """
        ...
