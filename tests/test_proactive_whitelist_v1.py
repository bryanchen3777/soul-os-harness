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
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

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
        """Baseline: SoulScheduler.__init__ 沒有 proactive_agents 參數"""
        import inspect
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertNotIn(
            "proactive_agents", sig.parameters,
            "Baseline (v1) 期望 SoulScheduler.__init__ 沒有 proactive_agents 參數, 修法後才加"
        )
        print("[v1 baseline] SoulScheduler.__init__ 沒有 proactive_agents 參數")

    def test_b_baseline_no_proactive_agents_attribute(self):
        """Baseline: SoulScheduler instance 沒有 _proactive_agents 屬性"""
        self.assertFalse(
            hasattr(self.scheduler, "_proactive_agents"),
            f"Baseline (v1) 期望 scheduler 沒有 _proactive_agents, "
            f"實際屬性: {[k for k in vars(self.scheduler) if 'proactive' in k.lower()]}"
        )
        print("[v1 baseline] scheduler 沒有 _proactive_agents 屬性")

    def test_c_baseline_proxy_source_no_proactive_agents(self):
        """Baseline: scheduler.py source 沒有 proactive_agents / _proactive_agents 引用"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            "proactive_agents", scheduler_source,
            "Baseline (v1) 期望 scheduler.py 沒有 proactive_agents 引用, 修法後才加"
        )
        self.assertNotIn(
            "_proactive_agents", scheduler_source,
            "Baseline (v1) 期望 scheduler.py 沒有 _proactive_agents 引用, 修法後才加"
        )
        print("[v1 baseline] scheduler.py 沒有 proactive_agents / _proactive_agents 引用")

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

    def test_e_baseline_proactive_dm_fires_all_agents(self):
        """Baseline: _fire_proactive_dm 從 _all_agents 抽, 沒有 whitelist → 10 隻都觸發

        mock random.choice 永遠回傳 yua (第一隻, 代表 random 選中)
        修法前: yua callback 被呼叫 (沒有任何過濾)
        修法後: yua 是 non-whitelist, silent skip; ruka 才會被觸發
        """
        async def run():
            # random.choice 抽 yua (代表「random 命中 yua」)
            with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
                await self.scheduler._fire_proactive_dm()
            return self.proactive_record["called_with"]

        called = asyncio.run(run())
        # Baseline 期望: yua 被觸發 (沒有任何過濾)
        self.assertIn(
            "agent_yua", called,
            f"Baseline (v1) 期望 _fire_proactive_dm 觸發 yua, 實際: {called}"
        )
        print(f"[v1 baseline] _fire_proactive_dm 觸發 yua (無 whitelist 過濾): {called}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
