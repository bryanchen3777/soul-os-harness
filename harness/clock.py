"""
harness/clock.py — SimulationClock (TL-1)

TL-0 规格 §3 / §6.5 (D2, 已拍板):
  - SimulationClock 是 harness-local 的模拟时钟: `advance(days=N)` 瞬间完成,
    模拟时间戳 (不是 wall clock)。
  - 只推进 harness 自己的时间线, 绝不触碰 production scheduler / 时钟。
  - Day 只做 checkpoint (T0=D0 / T15=D15 / T30=D30), 不是生命单位。

实现:
  - 内部维护 current_day (int, 从 0 起)。
  - sim_ts(day) 把 day 映射成 ISO 8601 UTC 字符串 (固定 epoch + day 偏移),
    供 InnerLifeEvent.ts 使用 (identity.py 的 TS_PATTERN 校验)。
  - label(day) 返回 "D{day}" (checkpoint 的 sim_ts 快照, 例 "D0"/"D15"/"D30")。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


# 固定 epoch: TL-1 fixture 的模拟时间基准 (2026-09-01T00:00:00+00:00)。
# 所有 fed events 的 ts 都从它偏移, 保证跨 run 确定性 (D2 scenario-deterministic)。
DEFAULT_EPOCH_ISO = "2026-09-01T00:00:00+00:00"


class SimulationClock:
    """harness-local 模拟时钟 (D2)。"""

    def __init__(self, start_day: int = 0, epoch_iso: str = DEFAULT_EPOCH_ISO) -> None:
        self._day = int(start_day)
        self._epoch = datetime.fromisoformat(epoch_iso.replace("Z", "+00:00"))
        if self._epoch.tzinfo is None:
            self._epoch = self._epoch.replace(tzinfo=timezone.utc)

    # ── 状态 ─────────────────────────────────────────────

    @property
    def day(self) -> int:
        """当前模拟日 (int)。"""
        return self._day

    def label(self, day: Optional[int] = None) -> str:
        """checkpoint 的 sim_ts 快照标签, 例 "D0" / "D15" / "D30"。"""
        d = self._day if day is None else int(day)
        return f"D{d}"

    # ── 推进 (瞬间完成, 不碰 production scheduler) ────────

    def advance(self, days: int = 1) -> int:
        """推进模拟时钟 N 天 (瞬间完成)。返回新的 current_day。"""
        if days < 0:
            raise ValueError(f"advance days 不可为负, got {days!r}")
        self._day += int(days)
        return self._day

    # ── 模拟时间戳 ───────────────────────────────────────

    def sim_ts(self, day: Optional[int] = None, hour: int = 0) -> str:
        """把 day (+可选 hour) 映射成 ISO 8601 UTC 字符串 (epoch + 偏移)。

        供 InnerLifeEvent.ts 使用 (identity.py TS_PATTERN 校验:
        YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00)。

        hour 是 TL-5 加的 additive 参数 (默认 0, 现有调用行为不变):
        心跳 tick 需要小时精度 (08:00 / 14:00 / 20:00 / 23:00 / 03:00)。
        """
        d = self._day if day is None else int(day)
        ts = self._epoch + timedelta(days=d, hours=int(hour))
        return ts.isoformat()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SimulationClock day={self._day} ({self.label()})>"
