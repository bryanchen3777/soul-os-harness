"""
test_proactive_whitelist_v2.py — proactive whitelist 修法驗證: 白名單生效, 只 ruka 觸發

Bry 拍板 2026-08-06 16:xx (貼給 Mavis):
> 策略調整：只留 Ruka（瑠夏）有主動生活/主動傳訊功能，其他 9 個角色改回純被動
> 確認改完之後，只有 Ruka 會自己找上門，其他角色你不主動找他們就不會收到任何主動訊息
> 修法 7/8/9 邏輯不用動，這些是觸發之後的內容組裝邏輯

這個 v2 驗證修法:
- SoulScheduler.__init__ 新增 proactive_agents 參數
- _proactive_agents_whitelist 屬性儲存白名單
- _get_proactive_agents() lazy 算白名單 ∩ _all_agents
- _fire_heartbeat / _fire_proactive_dm 只從 candidates (白名單) 抽
- 雙重保險: 即使 random.sample / random.choice mock 回傳非白名單 agent, 也會被過濾掉
- 向後相容: proactive_agents=None 維持現狀 (全 10 隻都能觸發)
- diary / dream / event 不受影響 (仍對 _all_agents 全部觸發)

Mock 範圍:
- 註冊 10 隻角色到 _all_agents (模擬 run_server.py 完整啟動)
- proactive_agents=["agent_ruka"] → 白名單只列 ruka
- mock random.sample / random.choice 模擬「random 命中非白名單」, 驗證會被過濾
- 直接呼叫 _fire_heartbeat / _fire_proactive_dm, 驗證:
  a. _get_proactive_agents() 只回傳 ruka
  b. _fire_heartbeat 即使 random.sample 回 10 隻, 也只有 ruka callback 被呼叫
  c. _fire_proactive_dm random.choice 回 yua (非白名單) → silent skip, 無 callback
  d. _fire_proactive_dm random.choice 回 ruka (白名單) → ruka callback 被呼叫
  e. diary / dream / event 不受 whitelist 影響 (全 10 隻都還在 _all_agents)
  f. 向後相容: proactive_agents=None 維持全 10 隻觸發
  g. Source 層: scheduler.py 有 proactive_agents / _proactive_agents_whitelist
  h. 邊界: 配錯 whitelist (列了不存在的 agent) → log warning, silent skip
"""
import asyncio
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul.scheduler import SoulScheduler


# 10 隻角色 (跟 configs/default.yaml agents 列表一致)
ALL_AGENTS = [
    "agent_yua", "agent_ruka", "agent_akane", "agent_rem",
    "agent_ram", "agent_mahiru", "agent_anna", "agent_mai",
    "agent_miku", "agent_aoi",
]
WHITELIST_RUKA = ["agent_ruka"]


def _make_callback_recorder():
    """建立一個記錄所有被觸發 agent_id 的 async callback."""
    record = {"called_with": []}

    async def callback(agent_id: str) -> None:
        record["called_with"].append(agent_id)

    return callback, record


def _make_scheduler_with_whitelist(whitelist):
    """建立 SoulScheduler, 註冊 10 隻角色 + 兩個 callback (heartbeat + proactive_dm)."""
    heartbeat_cb, heartbeat_record = _make_callback_recorder()
    proactive_cb, proactive_record = _make_callback_recorder()
    scheduler = SoulScheduler(proactive_agents=whitelist)
    scheduler.register_heartbeat(heartbeat_cb)
    scheduler.register_proactive_dm(proactive_cb)
    for aid in ALL_AGENTS:
        scheduler._all_agents.append(aid)
    return scheduler, heartbeat_record, proactive_record


class TestProactiveWhitelistRukaOnly(unittest.TestCase):
    """驗證修法 — proactive_agents=["agent_ruka"] 只讓 ruka 觸發"""

    def setUp(self):
        self.scheduler, self.heartbeat_record, self.proactive_record = (
            _make_scheduler_with_whitelist(WHITELIST_RUKA)
        )

    def test_a_whitelist_param_in_signature(self):
        """v2: SoulScheduler.__init__ 有 proactive_agents 參數"""
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertIn(
            "proactive_agents", sig.parameters,
            "v2 期望 SoulScheduler.__init__ 有 proactive_agents 參數"
        )
        # 確認 default 是 None (向後相容)
        self.assertIsNone(
            sig.parameters["proactive_agents"].default,
            "v2 期望 proactive_agents 預設 None (向後相容)"
        )
        print("[v2] SoulScheduler.__init__ 有 proactive_agents 參數, default=None")

    def test_b_whitelist_attribute_set(self):
        """v2: scheduler 實例有 _proactive_agents_whitelist 屬性, 值 = 傳入的白名單"""
        self.assertTrue(hasattr(self.scheduler, "_proactive_agents_whitelist"))
        self.assertEqual(
            self.scheduler._proactive_agents_whitelist, WHITELIST_RUKA,
            f"v2 期望 _proactive_agents_whitelist == {WHITELIST_RUKA}, "
            f"實際: {self.scheduler._proactive_agents_whitelist}"
        )
        print(f"[v2] _proactive_agents_whitelist = {self.scheduler._proactive_agents_whitelist}")

    def test_c_get_proactive_agents_filters_to_whitelist(self):
        """v2: _get_proactive_agents() 只回傳白名單 ∩ _all_agents (= [ruka])"""
        eligible = self.scheduler._get_proactive_agents()
        self.assertEqual(
            eligible, ["agent_ruka"],
            f"v2 期望 _get_proactive_agents() == ['agent_ruka'], 實際: {eligible}"
        )
        print(f"[v2] _get_proactive_agents() = {eligible}")

    def test_d_heartbeat_filters_non_whitelisted(self):
        """v2: _fire_heartbeat 即使 random.sample 回 10 隻, 只有 ruka callback 被呼叫

        模擬場景: random 抽中 10 隻 (mock 回傳全名單), 但 scheduler 必須過濾掉非白名單
        修法前: 10 隻 callback 都被呼叫
        修法後: 只有 ruka callback 被呼叫, 其他 9 隻 silent skip
        """
        async def run():
            # random.randint(1,2) 預設回 1, n=min(1, 1)=1, picks=mock 回 1 隻
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await self.scheduler._fire_heartbeat()
            return self.heartbeat_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 只有 ruka 被觸發
        self.assertEqual(
            called, ["agent_ruka"],
            f"v2 期望 _fire_heartbeat 只觸發 ruka, 實際觸發: {called}"
        )
        print(f"[v2] _fire_heartbeat 只觸發: {called} (其他 9 隻被 whitelist 過濾)")

    def test_e_proactive_dm_filters_non_whitelisted_choice(self):
        """v2: _fire_proactive_dm random.choice 回 yua (非白名單) → silent skip, 無 callback

        模擬場景: random 抽中 yua (mock random.choice 回 yua), scheduler 必須過濾
        修法前: yua callback 被呼叫
        修法後: 沒有任何 callback 被呼叫 (random.choice 回的非白名單, 雙重保險過濾)
        """
        async def run():
            with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
                await self.scheduler._fire_proactive_dm()
            return self.proactive_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 沒有任何 callback 被呼叫 (yua 是非白名單, 被過濾)
        self.assertEqual(
            called, [],
            f"v2 期望 _fire_proactive_dm (yua 非白名單) 不觸發任何人, 實際: {called}"
        )
        print(f"[v2] _fire_proactive_dm yua (非白名單) silent skip, called={called}")

    def test_f_proactive_dm_fires_whitelisted_agent(self):
        """v2: _fire_proactive_dm random.choice 回 ruka (白名單) → ruka callback 被呼叫

        模擬場景: random 抽中 ruka, scheduler 必須正確觸發
        修法前: ruka callback 被呼叫 (因為原本就沒過濾)
        修法後: ruka callback 還是被呼叫 (因為 ruka 在白名單, 應該觸發)
        """
        # 重置 record (因為 setUp 已建好 scheduler, 但要乾淨環境)
        scheduler, _, proactive_record = _make_scheduler_with_whitelist(WHITELIST_RUKA)

        async def run():
            with patch("src.soul.scheduler.random.choice", return_value="agent_ruka"):
                await scheduler._fire_proactive_dm()
            return proactive_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: ruka callback 被呼叫
        self.assertEqual(
            called, ["agent_ruka"],
            f"v2 期望 _fire_proactive_dm (ruka 白名單) 觸發 ruka, 實際: {called}"
        )
        print(f"[v2] _fire_proactive_dm ruka (白名單) 觸發: {called}")

    def test_g_diary_dream_event_unaffected(self):
        """v2: diary / dream / event 不受 whitelist 影響, 全 10 隻都還在 _all_agents

        動機: Bry 拍板「只影響 proactive_dm / heartbeat, diary/dream/event 仍對全角色觸發」
        驗證: scheduler._all_agents 仍包含全部 10 隻, 沒被 whitelist 過濾
        """
        self.assertEqual(
            len(self.scheduler._all_agents), 10,
            f"v2 期望 _all_agents 仍包含 10 隻角色, 實際: {len(self.scheduler._all_agents)}"
        )
        # 確認 10 隻都在
        for aid in ALL_AGENTS:
            self.assertIn(
                aid, self.scheduler._all_agents,
                f"v2 期望 {aid} 仍在 _all_agents (diary/dream/event 不受 whitelist 影響)"
            )
        print(f"[v2] _all_agents 仍包含全部 {len(self.scheduler._all_agents)} 隻角色 (diary/dream/event 完整)")

    def test_h_backward_compat_no_whitelist(self):
        """v2: 向後相容 — proactive_agents=None 維持現狀, 全部 10 隻都能觸發

        動機: SoulScheduler() 不傳參數時, 預設行為不能被破壞
              (測試程式碼 / 第三方 caller 可能直接實例化不傳 whitelist)
        """
        scheduler_no_wl, heartbeat_record, _ = _make_scheduler_with_whitelist(None)
        # sanity check: _proactive_agents_whitelist 應該是 None
        self.assertIsNone(scheduler_no_wl._proactive_agents_whitelist)

        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await scheduler_no_wl._fire_heartbeat()
            return heartbeat_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 10 隻全部觸發 (向後相容)
        self.assertEqual(
            sorted(called), sorted(ALL_AGENTS),
            f"v2 期望 proactive_agents=None 時 10 隻都觸發, 實際 {len(called)} 隻: {called}"
        )
        print(f"[v2] 向後相容: proactive_agents=None 觸發全部 {len(called)} 隻")

    def test_i_proxy_source_has_proactive_agents(self):
        """v2: scheduler.py source 有 proactive_agents / _proactive_agents_whitelist"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "proactive_agents", scheduler_source,
            "v2 期望 scheduler.py source 有 proactive_agents 引用"
        )
        self.assertIn(
            "_proactive_agents_whitelist", scheduler_source,
            "v2 期望 scheduler.py source 有 _proactive_agents_whitelist 屬性"
        )
        print("[v2] scheduler.py source 有 proactive_agents / _proactive_agents_whitelist")

    def test_j_misconfigured_whitelist_logs_warning(self):
        """v2: 配錯 whitelist (列了不存在的 agent) → log warning, silent skip

        動機: 配錯白名單是 Bry 容易踩的坑 (例: typo "agent_ruku"), 必須有可見信號
        修法: _get_proactive_agents() 發現 whitelist ∩ _all_agents 是空, log warning
        """
        # 白名單列了不存在的 agent_xxx (typo 模擬)
        scheduler_bad, heartbeat_record, _ = _make_scheduler_with_whitelist(["agent_xxx_typo"])

        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await scheduler_bad._fire_heartbeat()
            return heartbeat_record["called_with"]

        # 應該 silent skip, 沒 callback 被呼叫
        called = asyncio.run(run())
        self.assertEqual(
            called, [],
            f"v2 期望配錯 whitelist 觸發 0 隻, 實際: {called}"
        )
        print(f"[v2] 配錯 whitelist (['agent_xxx_typo']) silent skip, called={called}")


class TestProactiveWhitelistMultiAgent(unittest.TestCase):
    """驗證白名單可設定多隻角色 (為未來擴展預留)"""

    def test_k_multi_agent_whitelist(self):
        """v2: 白名單可列多隻角色 (未來擴展用, 這次 Bry 拍板只列 ruka)

        模擬未來場景: Bry 確認 ruka 穩了, 決定加 yua
        proactive_agents=["agent_ruka", "agent_yua"]
        驗證 _get_proactive_agents() 回傳 [ruka, yua], 其他 8 隻還是被過濾
        """
        multi_whitelist = ["agent_ruka", "agent_yua"]
        scheduler, heartbeat_record, _ = _make_scheduler_with_whitelist(multi_whitelist)

        eligible = scheduler._get_proactive_agents()
        self.assertEqual(
            sorted(eligible), sorted(multi_whitelist),
            f"v2 期望 multi whitelist {multi_whitelist}, 實際: {eligible}"
        )

        # 模擬 random.sample 回 10 隻, 過濾後應該只 ruka + yua 被觸發
        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await scheduler._fire_heartbeat()
            return heartbeat_record["called_with"]

        called = asyncio.run(run())
        self.assertEqual(
            sorted(called), sorted(multi_whitelist),
            f"v2 期望 multi whitelist 觸發 {multi_whitelist}, 實際: {called}"
        )
        print(f"[v2] multi whitelist 觸發: {called}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
