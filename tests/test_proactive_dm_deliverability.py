"""
test_proactive_dm_deliverability.py
Proactive DM 三件修復 (Bry 拍板 2026-08-29) 驗證:

#1 可送達檢查提前: scheduler._fire_proactive_dm 在 publish AGENCY_TRIGGER 之前
   檢查 bryan_last_seen (統一信號源 bryan_last_seen.json)。
   🔴 DELIVERABILITY-RELEASE-1 (Owner 裁定 B, 2026-09-13): 該檢查已由
      「> 4h 就 skip, 不觸發 LLM」改為「量測 + 留一行 gate=G5 痕跡後照常放行」。
      本檔對應的斷言已依裁定改為釘死**新語意**（見
      `test_g5_passes_when_bryan_inactive_5h` 的 docstring）。
      router M0.5 throttle 仍保留（但其自 2026-08-29 起為不可達兜底）。
#2 統一信號源: web inbound (gateway) 也更新 bryan_last_seen。
#3 雙實例: server_ops.ps1 Stop-SoulOsServer 殺進程樹 (port listener + taskkill /T)。
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from src.paths import reset_data_root
from src.soul.scheduler import SoulScheduler

_ROOT = Path(__file__).resolve().parent.parent


def _write_bryan_last_seen(data_dir, hours_ago):
    """寫 data/state/bryan_last_seen.json, last_recv_ts = N 小時前。"""
    state_dir = Path(data_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_recv_ts": (
            datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        ).isoformat(),
        "last_recv_agent": "agent_yua",
        "last_recv_preview": "test",
    }
    (state_dir / "bryan_last_seen.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _make_scheduler(monkeypatch, data_dir):
    """建立可送達檢查測試用的 scheduler (longing 固定達標, 排除 M7 干擾)。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(data_dir)
    reset_data_root()
    bus = MagicMock()
    sched = SoulScheduler(
        bus=bus,
        proactive_agents=["agent_yua"],
        proactive_dm_cooldown_seconds=0,
        quiet_hours_start=0,
        quiet_hours_end=0,
    )
    sched._all_agents = ["agent_yua"]
    monkeypatch.setattr(sched, "_get_agent_longing", lambda agent_id: 0.5)
    return sched


class TestDeliverabilityGate:
    """#1: 可送達檢查提前到 scheduler (LLM 之前)。"""

    def test_g5_passes_when_bryan_inactive_5h(self, tmp_path, monkeypatch):
        """🔴 合法變更（DELIVERABILITY-RELEASE-1, Owner 裁定 B, 2026-09-13）。

        本測試原名 `test_skips_when_bryan_inactive_5h`，斷言「bryan_last_seen 5h 前
        → skip, 不 publish AGENCY_TRIGGER」。該斷言是「4h 硬阻斷」的行為釘子。

        Owner 已明確裁定 **B —— 放寬 4h 硬阻斷**，並已知悉這會改變「主動」的語意
        （不再是「趁你在場時接話」）。裁定原文含：
          「放寬 4h 硬阻斷 ＋ (a) 先唯讀量測 longing 曲線 (b) 單調遞增則加每角色
            每日上限 1 則 (c) 不變量 `daily_proactive_cap(silence=3d) ==
            daily_proactive_cap(silence=3h)` 測試釘死 (d) TA-2 措辭逐字不動、
            TA-2 狀態不得參與發起判定」

        因此本測試**依 Owner 裁定理當改變**（屬「經授權的語意變更」，非放寬斷言）：
        現在釘死的是**新語意** —— 5h 前仍**必須**留一行 `gate=G5` 放行痕跡、
        **必須**繼續 publish（不再中斷）。舊語意的回歸防護已由
        `tests/test_deliverability_release_1.py::TestStep1_G5NoLongerBlocks`
        以 AST 級斷言接手（G5 區塊內不得有任何 return）。
        """
        _write_bryan_last_seen(tmp_path, hours_ago=5)
        sched = _make_scheduler(monkeypatch, tmp_path)
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)
        try:
            asyncio.run(sched._fire_proactive_dm())
            assert ("agent_yua", "proactive_dm") in published, (
                f"G5 放寬後（Owner 裁定 B）應照常 publish, 實際 {published}"
            )
            assert sched._next_proactive_dm_time is not None, "仍應排下次"
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_g5_passthrough_trace_logged_when_bryan_inactive_5h(
        self, tmp_path, monkeypatch, caplog
    ):
        """G5 放行必須留可觀測痕跡（含 inactivity 小時數 + gate=G5）。"""
        import logging

        _write_bryan_last_seen(tmp_path, hours_ago=5)
        sched = _make_scheduler(monkeypatch, tmp_path)
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)
        try:
            with caplog.at_level(logging.INFO, logger="soul_os.soul.scheduler"):
                asyncio.run(sched._fire_proactive_dm())
            msgs = [r.getMessage() for r in caplog.records]
            hit = [m for m in msgs if "gate=G5" in m]
            assert len(hit) == 1, f"G5 必須留一行放行痕跡: {msgs}"
            assert "5.0h" in hit[0]
            assert len(hit[0]) <= 200
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_publishes_when_bryan_recent_1h(self, tmp_path, monkeypatch):
        """bryan_last_seen 1h 前 → 可送達 → publish。"""
        _write_bryan_last_seen(tmp_path, hours_ago=1)
        sched = _make_scheduler(monkeypatch, tmp_path)
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)
        try:
            asyncio.run(sched._fire_proactive_dm())
            assert ("agent_yua", "proactive_dm") in published, (
                f"可送達應 publish, 實際 {published}"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_publishes_when_bryan_never_seen_cold_start(self, tmp_path, monkeypatch):
        """冷啟動 (無 bryan_last_seen.json) → 不 skip (跟 router M0.5 一致)。"""
        sched = _make_scheduler(monkeypatch, tmp_path)
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)
        try:
            asyncio.run(sched._fire_proactive_dm())
            assert ("agent_yua", "proactive_dm") in published, (
                f"冷啟動應 publish, 實際 {published}"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_bryan_last_seen_minutes_none_when_no_file(self, tmp_path):
        """無 bryan_last_seen.json → _bryan_last_seen_minutes 回 None (冷啟動)。"""
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path)
        reset_data_root()
        try:
            sched = SoulScheduler()
            assert sched._bryan_last_seen_minutes() is None
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()


class TestUnifiedSignalSource:
    """#2: 統一信號源 — web inbound 也更新 bryan_last_seen。"""

    def test_touch_and_read_roundtrip(self, tmp_path):
        """touch_bryan_last_seen 寫檔 → read_bryan_last_seen 讀回 (剛寫入 ~0 分鐘前)。"""
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path)
        reset_data_root()
        try:
            from src.io.channels.bryan_state import (
                touch_bryan_last_seen,
                read_bryan_last_seen,
            )
            assert touch_bryan_last_seen("agent_yua", "hello bry") is True
            last = read_bryan_last_seen()
            assert last is not None
            age_min = (datetime.now(timezone.utc) - last).total_seconds() / 60.0
            assert 0 <= age_min < 1, f"剛寫入應 ~0 分鐘前, 實際 {age_min}"
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_gateway_web_inbound_touches_bryan_last_seen(self):
        """gateway.py 的 web WS USER_MESSAGE 處理處呼叫 touch_bryan_last_seen。"""
        src = (_ROOT / "src/io/gateway.py").read_text(encoding="utf-8")
        assert "touch_bryan_last_seen" in src, "gateway.py 應呼叫 touch_bryan_last_seen"
        assert "ws_full_agent" in src, "gateway.py 應傳 ws_full_agent"

    def test_router_save_writes_same_file(self, tmp_path):
        """router._save_bryan_last_seen 寫入 bryan_state 同一個檔案 (統一信號源)。"""
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path)
        reset_data_root()
        try:
            from src.io.channels.router import ChannelRouter
            from src.io.channels.bryan_state import read_bryan_last_seen
            router = ChannelRouter(bus=MagicMock())
            router._save_bryan_last_seen("agent_yua", "hi")
            assert read_bryan_last_seen() is not None, (
                "router._save_bryan_last_seen 應寫入 bryan_state 同一個檔案"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()


class TestDualInstance:
    """#3: 雙實例 / restart 競態修復。"""

    def test_server_ops_stop_kills_process_tree(self):
        """server_ops.ps1 Stop-SoulOsServer 用 taskkill /T 殺進程樹 + port listener。"""
        src = (_ROOT / "scripts/server_ops.ps1").read_text(encoding="utf-8")
        assert "taskkill /PID" in src, "Stop 應用 taskkill /T 殺進程樹"
        assert "LocalPort 8000" in src, "Stop 應先殺 port 8000 listener"

    def test_server_ops_start_waits_for_port_release(self):
        """server_ops.ps1 Start-SoulOsServer 啟動前等 port 8000 釋放。"""
        src = (_ROOT / "scripts/server_ops.ps1").read_text(encoding="utf-8")
        assert "port 8000 still listening" in src, "Start 應等 port 釋放再啟動"
