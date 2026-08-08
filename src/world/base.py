"""
src/world/base.py — Soul OS M3.1 Phase A

WorldEventSource 抽象基底類別 (ABC)。

Bry 拍板 2026-08-08 01:57 — M3.1 Phase A 派工:

CONTRACT (硬約束):
  - source_id 是 source category (weather / news / calendar / social / synthetic)
  - start() 負責 source initialization
  - stop() 必須允許 future real source 做 cleanup
  - stop() contract 必須是 idempotent (多次呼叫安全)
  - source 不得直接取得 EventBus
  - source 不得 publish AGENT_INTENT
  - source 不得 publish AGENT_SPEAK
  - source 不得取得 SpeakerToken
  - source 不得呼叫 LLM
  - source 不得寫 Memory / SAGE / Diary / Dream
  - source 只能產生 WorldEvent
  - 不增加 event_id (novelty_id 已負責 event identity / dedup)

Phase A 範圍:
  - 只定義 interface
  - 不實作任何 real source (Weather / News / Calendar API 都不接)
  - SyntheticWorldEventSource 改 conform 這個 interface (A4)
  - Phase B/C 不在這次範圍

NOTE 位置說明:
  Bry 派工原文 reference path 為 src/world/source/base.py, 但因
  src/world/source.py 已是 module 且會跟 src/world/source/ subdirectory
  衝突 (Python module 跟同名 package 無法共存), 為避免動既有 M3
  source.py 結構, 採 sibling 扁平布局 src/world/base.py。
  既有 M3 tests / SyntheticWorldEventSource / build_* methods 全部不動。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .perception import WorldEvent


class WorldEventSource(ABC):
    """
    WorldEventSource 抽象基底類別。

    Lifecycle:
      1. __init__: 建立 source 物件 (此時可選注入 deps,但 M3.1 Phase A 不強制)
      2. start(): 由 Registry 呼叫,做 connection / webhook 設定 / 初始化
      3. (running) — Source 內部開始產生 WorldEvent,透過 Injector 注入
      4. stop(): 由 Registry 呼叫,做 cleanup,idempotent

    Source 透過 WorldEventInjector (Protocol) 注入 events, 不直接接 EventBus。
    Source 不能 generate AGENT_INTENT / AGENT_SPEAK / 寫 memory / call LLM。
    Source 只能「observe + emit WorldEvent」。

    設計理由 (M3.1 Architecture Review):
      - Source 沒有 bus reference → failure isolation 自然成立
      - Source 不能 generate AGENT_SPEAK → 類型系統 + 隔離共同擋住
      - Testable: 給 mock injector 就能測 source 不依賴 bus
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """
        Source category identifier。

        Convention: 跟 VALID_SOURCES 對齊
        (weather / news / calendar / social / synthetic)。
        """
        ...

    @abstractmethod
    async def start(self) -> None:
        """
        啟動 source。

        對 synthetic: no-op (沒有 background process)。
        對 real source: 建立 connection / 設定 webhook / 初始化。
        失敗應 raise, Registry 會 catch + 記錄到 health_snapshot。
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """
        關閉 source。

        Contract:
          - 必須 idempotent (多次呼叫安全, 不能 raise 已經 stopped 的 source)
          - 對 synthetic: no-op
          - 對 real source: close connection / cleanup resources
          - 失敗不 raise 即可 (Registry 仍視為 stopped, 不影響其他 source)
        """
        ...

    # ── WorldEvent 產生 (subclass implements 細節) ──
    # 注意: ABC 沒有強制 abstract emit(), 因為不同 source 觸發模式不同
    # (synthetic = 同步 build_*, real source = 異步 webhook / poll)
    # Source 透過 self._injector.inject(event) 注入 (由 subclass 在 __init__ 收 injector)
    #
    # Bry 派工 8/8 01:57: 這個 ABC 不強制 emit() shape,讓子 class 自由實作
    # 唯一的硬約束: 最終產物只能是 WorldEvent, 透過 Injector 注入

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} source_id={self.source_id!r}>"
