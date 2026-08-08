"""
src/world/state.py — Soul OS M3 Phase 1

WorldPerceptionState (Bry 拍板 2026-08-07 19:40 + 2026-08-07 20:02 hardening):

LIFECYCLE INVARIANTS (P4 hardening):
  - EPHEMERAL: 只存在 process lifetime, restart 後完全清空 (Test 9 驗證)
  - NO PERSISTENCE: 不進 SAGE / v1 / diary / dream / 任何長期 memory
  - NO CROSS-RESTART: 每次 server boot = 新 state, 沒有「上次留下來的世界」
  - BOUNDED RETENTION: max_active_events cap (default 200) 防止 memory leak
  - TTL / EXPIRY: 每個 event 有 novelty_window (default 24h) 自動過期
  - NO GROWTH: novelty_index 跟著 prune 自動 decrement, 不會無限增長

Thread-safety: 假設 asyncio single-thread, 不加 lock。

NOT a memory system:
  - 沒有持久化
  - 沒有 query interface
  - 沒有跟 SAGE / v1 / diary 互動
  - 不是為長期 recall 設計
  - 只是 ephemeral scratch space, 給 WorldPerceptionMiddleware 算 top-N 用
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional, Tuple

from .perception import WorldEvent

logger = logging.getLogger("soul_os.world.state")


def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 ts to datetime. Assume UTC (validation 層保證)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class WorldPerceptionState:
    """
    In-memory state of recent world events (ephemeral)。

    LIFECYCLE (P4 拍板 2026-08-07 20:02):
    - Lifecycle 起點: __init__() 創建, 從空 state 開始
    - Lifecycle 終點: 物件被 GC (process exit) 或顯式 clear()
    - Server restart = 新 state 物件, 完全無歷史
    - 沒有 hot reload / no warm start / no persistence

    設計:
    - active_events: deque, 持有 (event, perceived_at) tuple, 按 perceived_at 排序
    - novelty_index: dict[novelty_id, count_in_window] — 用於快速查重
    - expiry: 超過 novelty_window 的 event 自動從 active_events 移除
    - max_active_events: deque 上限, 防止 memory leak (FIFO eviction)

    公開 API:
    - add(event): 加新 event, 順便 prune 過期
    - get_active_events(now): 拿現在還沒過期的所有 events
    - get_novelty_count(novelty_id, now): 拿同一 novelty_id 在 window 內的次數
    - clear(): 全部清空 (給測試 / 維護用)
    - snapshot(): 回目前 state 的 summary (給 observability 用, 不是對外 query)

    記憶體 bound 計算:
    - max_active_events = 200
    - 每個 WorldEvent ≈ 1KB (summary + data + novelty_id)
    - 200 * 1KB = 200KB upper bound (不會 leak)

    不持久化。Server restart 後 = 空。這是 Bry 拍板的「in-memory, process lifetime」,不是記憶基礎設施。
    """

    def __init__(
        self,
        novelty_window: timedelta = timedelta(hours=24),
        max_active_events: int = 200,
    ):
        """
        Args:
            novelty_window: 超過這個時間的 event 視為過期 (Bry 拍板: config, 不寫死)
                            Phase 1 預設 24h (跟 brief 一致, 但 caller 可調)
            max_active_events: active_events deque 上限, 防止記憶體爆掉
        """
        self.novelty_window = novelty_window
        self.max_active_events = max_active_events
        # (event, perceived_at_iso) — perceived_at 是 state 收到 event 的時間,
        # 不是 event.ts (event.ts 是事件本身發生時間, 可能跟現在差很多)
        self._active: Deque[Tuple[WorldEvent, datetime]] = deque(maxlen=max_active_events)
        # novelty_id -> count in window
        self._novelty_index: Dict[str, int] = {}

        # observability counters
        self._total_added: int = 0
        self._total_expired: int = 0
        self._total_rejected_validation: int = 0

    # ── Mutators ─────────────────────────────────────────

    def add(self, event: WorldEvent, perceived_at: Optional[datetime] = None) -> int:
        """
        加新 event 到 state, 順便 prune 過期。

        Returns:
            novelty_count in window (新增後的次數, 包含這次)。
            Caller 用這個值算 novelty score。
        """
        if perceived_at is None:
            perceived_at = datetime.now(timezone.utc)
        self._prune(perceived_at)
        self._active.append((event, perceived_at))
        self._novelty_index[event.novelty_id] = self._novelty_index.get(event.novelty_id, 0) + 1
        self._total_added += 1
        return self._novelty_index[event.novelty_id]

    def clear(self) -> None:
        """全部清空 (測試 / 維護用)。"""
        self._active.clear()
        self._novelty_index.clear()
        self._total_added = 0
        self._total_expired = 0
        self._total_rejected_validation = 0

    def record_validation_reject(self) -> None:
        """記一次 validation reject (給 observability)。"""
        self._total_rejected_validation += 1

    # ── Queries ───────────────────────────────────────────

    def get_active_events(self, now: Optional[datetime] = None) -> List[WorldEvent]:
        """拿現在還沒過期的 events (按 perceived_at 排序, 舊 → 新)。"""
        if now is None:
            now = datetime.now(timezone.utc)
        self._prune(now)
        return [ev for ev, _ in self._active]

    def get_novelty_count(self, novelty_id: str, now: Optional[datetime] = None) -> int:
        """拿同一 novelty_id 在 window 內的次數 (0 表示不在 window 內)。"""
        if now is None:
            now = datetime.now(timezone.utc)
        self._prune(now)
        return self._novelty_index.get(novelty_id, 0)

    def get_state_size(self, now: Optional[datetime] = None) -> int:
        """目前 active event 數量。"""
        if now is None:
            now = datetime.now(timezone.utc)
        self._prune(now)
        return len(self._active)

    def snapshot(self) -> dict:
        """回目前 state 的 summary (給 observability, 不是對外 query interface)。"""
        return {
            "active_events": self.get_state_size(),
            "total_added": self._total_added,
            "total_expired": self._total_expired,
            "total_rejected_validation": self._total_rejected_validation,
            "novelty_window_seconds": self.novelty_window.total_seconds(),
            "max_active_events": self.max_active_events,
        }

    # ── Internal ──────────────────────────────────────────

    def _prune(self, now: datetime) -> None:
        """
        移除過期 events (perceived_at + window < now)。

        注意: prune 也會把 _novelty_index 中對應的 count 減下來
        (避免重複計算)。
        """
        while self._active:
            ev, perceived_at = self._active[0]
            age = now - perceived_at
            if age > self.novelty_window:
                self._active.popleft()
                # 對應 novelty_index count 減 1
                nid = ev.novelty_id
                if nid in self._novelty_index:
                    self._novelty_index[nid] -= 1
                    if self._novelty_index[nid] <= 0:
                        del self._novelty_index[nid]
                self._total_expired += 1
            else:
                break  # 已經按時間排序, 後面都是新的
