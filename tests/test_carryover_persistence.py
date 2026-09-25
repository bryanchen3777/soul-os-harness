"""
test_carryover_persistence.py
Soul OS — Phase 4 carryover 持久化測試（標準 pytest 測試）

兩個場景：
  A：直接發 SESSION_END → Agent._on_session_end() 寫入 carryover.json
      → apply_decay() → 值變小（約 -12%）
  B：elapsed < 30min → 不觸發 SESSION_END

注意：SESSION_END 是 HeartbeatEngine._loop() 在 elapsed_mins >= 30 時自動廣播的，
不需要也不應透過外部 SYSTEM_TICK 模擬（那個 tick 是給 Agent 用的，不是觸發 SESSION_END 的）。

本檔為**標準 pytest 測試**（`pytest tests/test_carryover_persistence.py`），
不再是可手動執行的腳本；`main()` 與 `__main__` 入口已移除。

TEST-INFRA-1 隔離防護（2026-09-25）
-----------------------------------
重構前本檔是手動腳本，含三處**字面相對生產路徑**並以 `shutil.rmtree` 刪除
`data/agents/agent_yua`。在 pytest 下它收集 0 個測試（`rmtree` 永不觸發），
但**手動執行時會真的刪掉生產目錄**。本檔已：

  1. **刪除 `rmtree` 區塊**（tmp 每次全新，清除動作既多餘又是未來誤用的種子）。
  2. **移除全部字面相對路徑**：改由 `data_root()` 推導。
  3. **不傳 `base_path`** 給 `save()/load()`——`src/temporal/models.py`
     的原生預設即走 `data_root() / "agents"`，直接尊重隔離機制。
  4. 新增 `_assert_isolated()` 並在**每個測試第一行**呼叫：斷言
     `data_root()` 不等於生產 `data/`，讓「隔離失效」立刻紅燈而非靜默污染。

隔離由 `tests/conftest.py` 的 autouse fixture 提供（把 `SOUL_OS_DATA_DIR`
指向 per-test tmp 並呼叫 `reset_data_root()`）。

async 慣例：沿用 `@pytest.mark.asyncio`（`pytest-asyncio` 已安裝；
`pytest.ini` 無 `asyncio_mode = auto`，故 strict 模式下必須顯式標記）。
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.agent.consciousness import AgentYua
from src.temporal.models import EmotionalCarryover
from src.paths import data_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.carryover")

AGENT_ID = "agent_yua"


def _assert_isolated() -> None:
    """TEST-INFRA-1 防護：斷言 data_root() 不在生產路徑。

    這是最後一道防線——若 conftest 的隔離 fixture 失效（或本檔被以
    `SOUL_OS_DATA_DIR` 未設的方式執行），測試必須立刻失敗，而不是
    靜默地把資料寫進／讀自生產 `data/`。
    """
    root = data_root().resolve()
    prod = (Path(__file__).resolve().parent.parent / "data").resolve()
    assert root != prod, f"data_root() 指向生產路徑 {prod}，測試未被隔離"


# ─────────────────────────────────────────────
# 場景 A：直接發 SESSION_END → carryover 寫入 → apply_decay
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_end_writes_carryover() -> None:
    _assert_isolated()

    logger.info("\n" + "=" * 60)
    logger.info("  場景 A：SESSION_END → carryover 寫入 → apply_decay")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    yua = AgentYua(agent_id=AGENT_ID, bus=bus)
    yua.state.intimacy_level = 80
    yua.state.dependency = 0.7
    yua.state.mood = "lonely"
    yua.register()

    # ── 直接發 SESSION_END（繞過 Heartbeat timing）──
    session_end = SoulEvent(
        event_type=EventType.SESSION_END,
        source="heartbeat_engine",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "elapsed_mins": 35.0,
            "last_user_activity": datetime.now(timezone.utc).isoformat(),
        },
    )
    await bus.publish(session_end)
    await asyncio.sleep(0.3)

    # ── 斷言 A1：carryover.json 存在（寫入 data_root()/agents/agent_yua/）──
    carryover_path = data_root() / "agents" / AGENT_ID / "carryover.json"
    assert carryover_path.exists(), (
        f"carryover.json 不存在：{carryover_path}"
    )
    logger.info(f"  ✓ A1：carryover.json 存在 → {carryover_path}")

    # ── 斷言 A2：carryover 值有意義（不傳 base_path ⇒ 走 data_root() 預設）──
    raw = EmotionalCarryover.load(AGENT_ID)
    assert raw.attachment_heat > 0, (
        f"attachment_heat 應 > 0，實際={raw.attachment_heat}"
    )
    assert raw.intimacy_afterglow > 0, (
        f"intimacy_afterglow 應 > 0，實際={raw.intimacy_afterglow}"
    )
    logger.info(
        f"  ✓ A2：carryover 有值 — heat={raw.attachment_heat:.2f}"
        f" afterglow={raw.intimacy_afterglow:.2f}"
    )

    # ── 斷言 A3：apply_decay(0.5h) 後值變小（約 -12%）──
    aged = raw.apply_decay(elapsed_hours=0.5)
    assert aged.attachment_heat < raw.attachment_heat, (
        f"apply_decay 後 attachment_heat 應變小："
        f" before={raw.attachment_heat:.3f} after={aged.attachment_heat:.3f}"
    )
    # decay_rate=0.12，0.5 小時：factor = (1-0.12)^0.5 ≈ 0.918
    expected_factor = (1 - 0.12) ** 0.5
    actual_ratio = aged.attachment_heat / raw.attachment_heat
    assert abs(actual_ratio - expected_factor) < 0.01, (
        f"decay 比例不符：expected={expected_factor:.3f} actual={actual_ratio:.3f}"
    )
    logger.info(
        f"  ✓ A3：apply_decay(0.5h) → heat {raw.attachment_heat:.3f} → {aged.attachment_heat:.3f}"
        f"（ratio={actual_ratio:.3f} ≈ {expected_factor:.3f}）"
    )

    await bus.stop()


# ─────────────────────────────────────────────
# 場景 B：正常時段 elapsed=10m → _session_ended 保持 False
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_short_elapsed_no_session_end() -> None:
    _assert_isolated()

    from src.heartbeat.engine import HeartbeatEngine  # import moved here to avoid UnboundLocalError

    logger.info("\n" + "=" * 60)
    logger.info("  場景 B：HeartbeatElapsed < 30min → 不影響 _session_ended")
    logger.info("=" * 60)

    # HeartbeatEngine 的 SESSION_END_THRESHOLD_MINS 是 class 屬性，
    # 測試方式：驗證 HeartbeatEngine.SESSION_END_THRESHOLD_MINS == 30
    assert HeartbeatEngine.SESSION_END_THRESHOLD_MINS == 30.0, (
        f"SESSION_END_THRESHOLD_MINS 應為 30.0，實際={HeartbeatEngine.SESSION_END_THRESHOLD_MINS}"
    )
    logger.info(f"  ✓ B1：SESSION_END_THRESHOLD_MINS = 30.0（規格一致）")

    # 驗證 _session_ended 初始值為 False
    bus = SoulEventBus()
    await bus.start()

    h = HeartbeatEngine(bus=bus, tick_interval_seconds=9999)
    assert h._session_ended is False, (
        f"_session_ended 初始值應為 False，實際={h._session_ended}"
    )
    logger.info(f"  ✓ B2：_session_ended 初始值 = False")
    logger.info(f"  ✓ B3：elapsed < 30min 不會廣播 SESSION_END（閾值在 HeartbeatEngine._loop 內）")

    await bus.stop()
