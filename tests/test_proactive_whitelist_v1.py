"""
test_proactive_whitelist_v1.py — proactive whitelist 修法 baseline: 沒有 whitelist filter

Bry 拍板 2026-08-05 21:08 [TEMP-EMERGENCY-STOP]: DISABLE_PROACTIVE=true 整個 skip scheduler start
(連環轟炸應急處理)。

Bry 8/6 16:xx 派工 (貼給 Mavis):
> 策略調整：只留 Ruka（瑠夏）有主動生活/主動傳訊功能，其他 9 個角色改回純被動
> 目前 DISABLE_PROACTIVE 是全域開關，需要改成角色白名單機制
> 修法 7/8/9 邏輯不用動，這些是觸發之後的內容組裝邏輯

這個 v1 驗證現狀 (before whitelist 修法):
- scheduler._fire_heartbeat / _fire_proactive_dm 從 self._all_agents 隨機抽, 沒有 whitelist 過濾
- 任何註冊的 agent 都有機會被觸發 (10 隻角色)
- scheduler.py 沒有 proactive_agents 參數 / _proactive_agents 屬性

Mock 範圍:
- 建立 SoulScheduler (不傳 proactive_agents, 用預設)
- 註冊 10 隻角色的 heartbeat + proactive_dm callback (用 mock async function 記錄被觸發的 agent_id)
- mock random.sample / random.choice 回傳 10 隻角色, 模擬「抽到全名單」
- 直接呼叫 _fire_heartbeat / _fire_proactive_dm, 驗證所有 10 隻的 callback 都被觸發
- 修法前 v1 期望: 10 隻角色都被觸發, 沒有任何 whitelist 過濾
- 修法後 v2 期望: 只有 ruka 被觸發, 其他 9 隻 callback 沒被呼叫
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.paths import reset_data_root
from src.soul.scheduler import SoulScheduler


# 10 隻角色 (跟 configs/default.yaml agents 列表一致)
ALL_AGENTS = [
    "agent_yua", "agent_ruka", "agent_akane", "agent_rem",
    "agent_ram", "agent_mahiru", "agent_anna", "agent_mai",
    "agent_miku", "agent_aoi",
]


def _make_callback_recorder():
    """建立一個記錄所有被觸發 agent_id 的 async callback."""
    record = {"called_with": []}

    async def callback(agent_id: str) -> None:
        record["called_with"].append(agent_id)

    return callback, record


class TestProactiveWhitelistBaseline(unittest.TestCase):
    """驗證現狀 (before whitelist 修法) — 所有 10 隻角色都能被觸發"""

    def setUp(self):
        self.heartbeat_cb, self.heartbeat_record = _make_callback_recorder()
        self.proactive_cb, self.proactive_record = _make_callback_recorder()
        # 不傳 proactive_agents, 用預設 (預設應該是 None = 不過濾, 維持現狀)
        self.scheduler = SoulScheduler()
        self.scheduler.register_heartbeat(self.heartbeat_cb)
        self.scheduler.register_proactive_dm(self.proactive_cb)
        # 手動塞 10 隻進 _all_agents (模擬 run_server.py 註冊完的情境)
        for aid in ALL_AGENTS:
            self.scheduler._all_agents.append(aid)

    def test_a_baseline_no_proactive_agents_param(self):
        """修法 11 已套用: SoulScheduler.__init__ 有 proactive_agents 參數

        本測試原本寫成 v1 baseline (斷言沒有這個參數)。
        Bry 拍板 2026-08-06 16:xx 修法 11 之後:
        - src/soul/scheduler.py:167 `proactive_agents: Optional[List[str]] = None,`
        - src/soul/scheduler.py:206 `self._proactive_agents_whitelist = proactive_agents`
        """
        import inspect
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertIn(
            "proactive_agents", sig.parameters,
            "修法 11 (Bry 拍板 2026-08-06 16:xx): SoulScheduler.__init__ 應該有 "
            "proactive_agents 參數 (scheduler.py:167) — 找不到就是修法被回退"
        )
        # 預設值必須是 None = 不過濾 (向後相容)
        self.assertIsNone(
            sig.parameters["proactive_agents"].default,
            "proactive_agents 預設值應該是 None (向後相容: 不傳 = 不過濾, 維持修法前行為)"
        )
        print("[修法 11 已套用] SoulScheduler.__init__ 有 proactive_agents 參數 (預設 None)")

    def test_b_baseline_no_proactive_agents_attribute(self):
        """修法 11 已套用: SoulScheduler instance 有 _proactive_agents_whitelist 屬性

        本測試原本寫成 v1 baseline (斷言沒有 _proactive_agents 屬性)。
        正式屬性名是 _proactive_agents_whitelist (scheduler.py:206), 沒有裸的 _proactive_agents。
        """
        self.assertTrue(
            hasattr(self.scheduler, "_proactive_agents_whitelist"),
            f"修法 11: scheduler 應該有 _proactive_agents_whitelist 屬性 "
            f"(scheduler.py:206), 實際屬性: "
            f"{[k for k in vars(self.scheduler) if 'proactive' in k.lower()]}"
        )
        # 屬性名帶 whitelist, 不是裸的 _proactive_agents
        self.assertFalse(
            hasattr(self.scheduler, "_proactive_agents"),
            "屬性正式名是 _proactive_agents_whitelist, 不是裸的 _proactive_agents"
        )
        # 沒傳參數 → None = 不過濾 (向後相容)
        self.assertIsNone(
            self.scheduler._proactive_agents_whitelist,
            "沒傳 proactive_agents 時應該是 None (= 不過濾, 維持修法前行為)"
        )
        print("[修法 11 已套用] scheduler 有 _proactive_agents_whitelist 屬性 (預設 None)")

    def test_c_baseline_proxy_source_no_proactive_agents(self):
        """修法 11 已套用: scheduler.py source 有 proactive_agents / _get_proactive_agents"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "proactive_agents", scheduler_source,
            "修法 11: scheduler.py 應該有 proactive_agents 參數 (scheduler.py:167)"
        )
        self.assertIn(
            "_proactive_agents_whitelist", scheduler_source,
            "修法 11: scheduler.py 應該有 _proactive_agents_whitelist 屬性 (scheduler.py:206)"
        )
        self.assertIn(
            "def _get_proactive_agents", scheduler_source,
            "修法 11: scheduler.py 應該有 _get_proactive_agents() helper (scheduler.py:1148)"
        )
        print("[修法 11 已套用] scheduler.py 有 proactive_agents / _get_proactive_agents()")

    def test_d_baseline_heartbeat_fires_all_agents(self):
        """Baseline: _fire_heartbeat 從 _all_agents 抽, 沒有 whitelist → 10 隻都觸發

        mock random.sample 永遠回傳 10 隻, 模擬「random 抽中全部」
        修法前: 10 隻 callback 都被呼叫 (沒有任何過濾)
        修法後: 只有 ruka 被呼叫, 其他 9 隻 silent skip
        """
        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await self.scheduler._fire_heartbeat()
            return self.heartbeat_record["called_with"]

        called = asyncio.run(run())
        # Baseline 期望: 10 隻全部被觸發
        self.assertEqual(
            sorted(called), sorted(ALL_AGENTS),
            f"Baseline (v1) 期望 _fire_heartbeat 觸發全部 10 隻, 實際觸發 {len(called)} 隻: {called}"
        )
        print(f"[v1 baseline] _fire_heartbeat 觸發全部 {len(called)} 隻: {sorted(called)}")

    def test_e_proactive_dm_fires_only_whitelisted_ruka(self):
        """ruka-only whitelist (Bry 2026-08 拍板) — 兩半都要斷言。

        舊名 `test_e_baseline_proactive_dm_fires_all_agents` 早於 Bry 的
        ruka-only 決策, 名稱已成為謊言 → 本版**改名**為真實行為
        (`..._fires_only_whitelisted_ruka`), 不是偷偷反向。

        白名單收斂後, `_fire_proactive_dm` 從 whitelist 閘門到 publish 之間
        還有四道閘門, 全部是 Bry 刻意加的 production 閘門 (非 bug):
          G4  靜音時段 23:00-08:00               (scheduler._is_quiet_hours)
          G7  M7-longing 想念門檻                 (LONGING_THRESHOLD = 0.3)
          G7b 每角色每日 DM 上限                  (DELIVERABILITY-RELEASE-1)
          G10/G11 inner-life gate + SM-3 Decision (fail-closed, scheduler.py:303-306)
        任一不通過都是 `return` 早退 → 0 AGENCY_TRIGGER。舊測試只 mock 了
        longing 一道, 其餘三道靠真實時鐘/真實 data/真實 LLM 判定 ⇒
        在任何非「剛好白天 + Bry 剛好沒回話」的時段都拿不到 publish。
        修的是**測試自己**: 顯式打開這四道閘門, 讓本測試回到它真正要驗的命題。
        一律用既有方法當 seam 做 patch, 不改 production、不繞過真實閘門語意。

        強化重點: 同時斷言「ruka 會發」與「非白名單角色不會發」
        (單獨任一半都是弱斷言)。
        """
        from datetime import datetime
        from unittest.mock import AsyncMock
        from src.eventbus.schema import EventType
        from src.timezone_utils import get_local_tz
        from src.soul.scheduler import LONGING_THRESHOLD

        whitelist = ["agent_ruka"]
        # 白天、非靜音時段（固定 14:00，避開 23:00-08:00 wrap-around）
        daytime = datetime(2026, 8, 12, 14, 0, 0, tzinfo=get_local_tz())

        def _build():
            """白名單 = ruka-only 的 scheduler + AGENCY_TRIGGER bus capture。"""
            captured = []

            class _CapturingBus:
                async def publish(self, event):
                    if event.event_type == EventType.AGENCY_TRIGGER:
                        captured.append(event)

                async def start(self):
                    pass

                async def stop(self):
                    pass

            sched = SoulScheduler(bus=_CapturingBus(), proactive_agents=whitelist)
            for aid in ALL_AGENTS:
                sched._all_agents.append(aid)
            return sched, captured

        def _fire(choice):
            """四道閘門全開的前提下, 讓 random.choice 抽到 `choice`, 回傳實際發生的 agent_id。"""
            sched, captured = _build()

            async def run():
                with patch("src.soul.scheduler.random.choice", return_value=choice), \
                     patch("src.soul.scheduler.now_local", return_value=daytime), \
                     patch.object(sched, "_get_agent_longing",
                                  return_value=LONGING_THRESHOLD + 0.5), \
                     patch.object(sched, "_proactive_daily_cap_allows", return_value=True), \
                     patch.object(sched, "_inner_life_gate_check",
                                  new=AsyncMock(return_value=True)), \
                     patch.object(sched, "_decision_check",
                                  new=AsyncMock(return_value=True)):
                    await sched._fire_proactive_dm()

            asyncio.run(run())
            return [e.payload["agent_id"] for e in captured]

        # (a) 正向: 白名單內的 ruka 會被發 proactive_dm
        fired = _fire("agent_ruka")
        self.assertEqual(
            fired, ["agent_ruka"],
            f"ruka 在白名單內, _fire_proactive_dm 應該 publish 1 個 AGENCY_TRIGGER, 實際: {fired}"
        )

        # (b) 反向: 非白名單角色即使被 random 抽中也必須被擋 (double-insurance)
        for blocked in ("agent_yua", "agent_akane", "agent_rem", "agent_miku"):
            fired = _fire(blocked)
            self.assertEqual(
                fired, [], f"{blocked} 不在白名單 {whitelist}, 必須被擋下, 實際: {fired}"
            )
        print("[ruka-only] proactive_dm: ruka fires=['agent_ruka'], "
              "non-whitelisted agents blocked (抽樣 4 隻)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
