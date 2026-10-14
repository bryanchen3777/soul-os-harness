# -*- coding: utf-8 -*-
"""
SOUL-BUG-004（時區正規化部分，2026-10-13）— chrono 區塊的時鐘不得慢於 persona 時區。

背景：`build_temporal_context()` 直接取 `now.hour` 判定 `time_period` /
`vulnerability_window` / `sleep_pressure`，但**兩個呼叫端都傳 UTC**：
  - `src/heartbeat/engine.py:205`（SYSTEM_TICK 途徑，2026-09 起因 M5.7-2 已停用）
  - `src/agent/consciousness.py::_build_chrono_block`（SOUL-BUG-004-SINK 新接通）
而 `PersonaConfig.timezone` 預設是 **America/New_York**。

不轉換的實測後果（修復前，NY 實際時鐘 → 被判定的時段）：
    08:00 → afternoon   14:00 → evening   20:00 → deep_night
⇒ 角色會在紐約晚上 8 點對 Bry 說「深夜了」，這是**認知幻覺**，比沒有時間感知更糟。

本檔釘死的不變量：**傳入任何時區的 `now`，判定結果一律以 persona 時區為準。**
刻意不依賴固定 UTC offset（美東有 EDT／EST），斷言寫成
「傳入 UTC 的某個小時 == 換算出來的 persona 本地小時」。
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from src.temporal.core import build_temporal_context, compute_time_period
from src.temporal.models import EmotionalCarryover, PersonaConfig
from src.temporal.render import render_temporal_block


def _ctx(now: datetime, tz_name: str = "America/New_York"):
    cfg = PersonaConfig(persona_id="probe")
    assert str(cfg.timezone) == tz_name, (
        f"測試預期 persona 時區為 {tz_name}，實際為 {cfg.timezone}；"
        "若 default 時區被改，本測試的換算前提需一併更新。"
    )
    return build_temporal_context(
        persona_id="probe",
        last_msg_ts=None,
        current_stress=0,
        carryover=EmotionalCarryover(),
        config=cfg,
        now=now,
    )


# ─────────────────────────────────────────────────────────────────────
# 1. Bry 2026-10-13 點名的三個案例（UTC 12:00 / 18:00 / 00:00）
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "utc_hour, expected_local_hour, expected_period",
    [
        (12, 8, "morning"),     # UTC 12:00 → 紐約 08:00
        (18, 14, "afternoon"),  # UTC 18:00 → 紐約 14:00
        (0, 20, "evening"),     # UTC 00:00 → 紐約 20:00
    ],
)
def test_utc_input_is_localized_to_persona_timezone(
    utc_hour: int, expected_local_hour: int, expected_period: str
) -> None:
    utc_now = datetime(2026, 10, 14, utc_hour, 30, tzinfo=timezone.utc)
    ctx = _ctx(utc_now)

    local_hour = utc_now.astimezone(ZoneInfo("America/New_York")).hour
    assert local_hour == expected_local_hour, (
        "本測試的換算前提失效（美東 DST 邊界）："
        f"UTC {utc_hour:02d}:30 實際落在紐約 {local_hour:02d} 時"
    )
    assert ctx.time_period == expected_period, (
        f"UTC {utc_hour:02d}:30（紐約 {local_hour:02d}:30）應判為 "
        f"{expected_period}，實得 {ctx.time_period}。"
        "若這裡失敗，代表 build_temporal_context 又開始直接讀 UTC 的 now.hour。"
    )


# ─────────────────────────────────────────────────────────────────────
# 2. 全時段掃描：每一個 UTC 小時都必須等於對應的 persona 本地時段
# ─────────────────────────────────────────────────────────────────────


def test_every_utc_hour_maps_to_the_persona_local_time_period() -> None:
    """核心不變量：渲染出的 time_period 必須等於用「本地小時」算出的結果。"""
    mismatches = []
    for utc_hour in range(24):
        utc_now = datetime(2026, 10, 14, utc_hour, 15, tzinfo=timezone.utc)
        expected_local_hour = utc_now.astimezone(ZoneInfo("America/New_York")).hour
        expected_period = compute_time_period(expected_local_hour)

        ctx = _ctx(utc_now)
        if ctx.time_period != expected_period:
            mismatches.append(
                f"UTC {utc_hour:02d}:15 → 紐約 {expected_local_hour:02d}:15："
                f"預期 {expected_period}，實得 {ctx.time_period}"
            )

    assert not mismatches, (
        "build_temporal_context 未把 UTC 正規化為 persona 時區（SOUL-BUG-004）：\n  "
        + "\n  ".join(mismatches)
    )


# ─────────────────────────────────────────────────────────────────────
# 3. DST 邊界：同一個 UTC 小時在 EDT / EST 兩種情況下都必須對齊本地
# ─────────────────────────────────────────────────────────────────────


def test_dst_boundary_is_not_baked_into_the_logic() -> None:
    """7 月（EDT, UTC-4）與 1 月（EST, UTC-5）都必須對齊，不能寫死 offset。"""
    for utc_now in (
        datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),   # EDT → 08:00 local
        datetime(2027, 1, 14, 12, 0, tzinfo=timezone.utc),   # EST → 07:00 local
    ):
        local_hour = utc_now.astimezone(ZoneInfo("America/New_York")).hour
        ctx = _ctx(utc_now)
        assert ctx.time_period == compute_time_period(local_hour), (
            f"{utc_now.isoformat()}（本地 {local_hour:02d}:00）時段判定不符："
            f"實得 {ctx.time_period}"
        )


# ─────────────────────────────────────────────────────────────────────
# 4. 脆弱窗（22:00-04:00 persona 本地）必須在本地夜裡才開啟
# ─────────────────────────────────────────────────────────────────────


def test_vulnerability_window_follows_local_night_not_utc_night() -> None:
    """紐約 18:00（UTC 22:00）不該被算進脆弱窗；紐約 23:00（UTC 03:00）才該。"""
    # silence 6h > vulnerability_silence_min=4.0，讓窗口判定生效
    def _ctx_with_silence(now: datetime):
        cfg = PersonaConfig(persona_id="probe")
        return build_temporal_context(
            persona_id="probe",
            last_msg_ts=(now - timedelta(hours=6)).isoformat(),
            current_stress=0,
            carryover=EmotionalCarryover(),
            config=cfg,
            now=now,
        )

    ny_evening = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)  # 紐約 18:00
    ny_night = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)    # 紐約 23:00

    assert _ctx_with_silence(ny_evening).momentum.vulnerability_window is False, (
        "紐約 18:00 不該是脆弱窗（UTC 22:00 才是 —— 這正是本 bug 的形態）"
    )
    assert _ctx_with_silence(ny_night).momentum.vulnerability_window is True, (
        "紐約 23:00 應該落在 22:00-04:00 脆弱窗內"
    )


# ─────────────────────────────────────────────────────────────────────
# 5. 渲染層同步（防止有人只修 core 卻留下 render 的舊假設）
# ─────────────────────────────────────────────────────────────────────


def test_rendered_block_matches_the_localized_time_period() -> None:
    utc_now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)  # 紐約 08:00
    rendered = render_temporal_block(_ctx(utc_now))
    assert "time_period=morning" in rendered, (
        f"渲染結果未反映本地時段：\n{rendered}"
    )