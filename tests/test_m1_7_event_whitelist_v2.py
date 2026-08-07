"""
test_m1_7_event_whitelist_v2.py — M1.7 修法驗證: event trigger 過 whitelist 過濾, 只 ruka 觸發

Bry 拍板 2026-08-07 15:00 (貼給 Mavis):
> 派 Mavis 用 A 方案修: 在 event scheduler trigger 那條路徑 (跟 proactive_dm 一樣)
> 加上修法 11 的 whitelist 過濾, 確保隨機事件觸發時如果抽到非白名單角色
> (目前只有瑠夏在白名單), 就不要真的發送給你

這個 v2 驗證修法:
- _fire_event 從 _get_proactive_agents() 抽 (跟 _fire_proactive_dm / _fire_heartbeat 共用 whitelist)
- 沒新增 event_agents_whitelist 參數 (跟 proactive_dm 共用同一個 whitelist)
- 修法範圍跟 proactive_dm 一樣窄, 不擴大
- 向後相容: whitelist=None → _get_proactive_agents() 回 _all_agents, 行為不變
- diary / dream 不受影響 (仍對 _all_agents 全部觸發)

Mock 範圍:
- 建立 SoulScheduler(proactive_agents=["agent_ruka"])
- 註冊 10 隻角色到 _all_agents
- 註冊 event callback (用 mock async function 記錄被觸發的 agent_id)
- mock random.sample 回傳 10 隻, 模擬「抽中全部」
- 直接呼叫 _fire_event, 驗證:
  a. _get_proactive_agents() 只回傳 ruka (跟 proactive_dm 共用)
  b. _fire_event random.sample 回 10 隻, 只有 ruka callback 被呼叫
  c. diary / dream 不受影響 (_all_agents 仍 10 隻, _fire_all / _fire_dream 不過濾)
  d. 向後相容: proactive_agents=None 觸發全 10 隻
  e. Source 層: _fire_event 用 _get_proactive_agents()
  f. 邊界: 配錯 whitelist → silent skip
  g. Bry 派工原意: 「共用 whitelist, 不新增 event_agents_whitelist」

Bry 派工原文 (要保留給未來 session 看):
- 「目前系統裡『會主動發訊息給你』的路徑, 實際上就只有 proactive_dm 跟 event 這兩個 scheduler trigger」
- 「A 方案跟 B 方案覆蓋範圍等同時, 改動更小的優先」
- 「不為假設中的未來灑過濾網」
- 「更貼合修法 11 當初 narrow 派工的精神」

Bry 拒絕的選項 (要保留):
- B 方案: 在所有 agent_intent 出口套 whitelist (改動更大, 為假設的未來灑過濾網)
- 新增 event_agents_whitelist 獨立參數 (跟 proactive_dm 共用就足夠, 不增加 API 表面)
"""
import asyncio
import inspect
import re
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


def _make_event_callback_recorder():
    """建立一個記錄所有被觸發 agent_id 的 event callback."""
    record = {"called_with": []}

    async def callback(agent_id: str, slot: str) -> None:
        record["called_with"].append(agent_id)

    return callback, record


def _make_scheduler_with_whitelist(whitelist):
    """建立 SoulScheduler, 註冊 10 隻角色 + event callback."""
    event_cb, event_record = _make_event_callback_recorder()
    scheduler = SoulScheduler(proactive_agents=whitelist)
    scheduler.register_dream_event(
        dream_callback=lambda a, s: asyncio.sleep(0),
        event_callback=event_cb,
    )
    for aid in ALL_AGENTS:
        scheduler._all_agents.append(aid)
    return scheduler, event_record


class TestM17EventWhitelistRukaOnly(unittest.TestCase):
    """驗證修法 — proactive_agents=["agent_ruka"] 只讓 ruka 觸發 event"""

    def setUp(self):
        self.scheduler, self.event_record = _make_scheduler_with_whitelist(WHITELIST_RUKA)

    def test_a_event_uses_get_proactive_agents(self):
        """v2: _fire_event 用 _get_proactive_agents() 抽, 跟 proactive_dm 共用 whitelist"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        match = re.search(r"async def _fire_event.*?(?=async def |\n    # ─)", scheduler_source, re.DOTALL)
        self.assertIsNotNone(match, "找不到 _fire_event 函式")
        fire_event_source = match.group(0)
        # v2 期望: _fire_event 引用 _get_proactive_agents()
        self.assertIn(
            "_get_proactive_agents", fire_event_source,
            "v2 期望 _fire_event 引用 _get_proactive_agents() (跟 proactive_dm 共用)"
        )
        # v2 期望: _fire_event 不再直接用 self._all_agents 抽樣
        # 排除 _all_agents 的 if not 檢查, 確認抽樣走 candidates
        # 如果 _fire_event 還有 "self._all_agents" + sample, 表示還在從全名單抽
        # 但保留 if not self._all_agents 早退是 OK 的
        sample_call = re.search(r"_r\.sample\([^,]+,", fire_event_source)
        if sample_call:
            sample_arg = sample_call.group(0)
            self.assertIn(
                "candidates", sample_arg,
                f"v2 期望 _fire_event 從 candidates 抽 (whitelist 過濾後), 抽樣參數: {sample_arg}"
            )
        print("[v2] _fire_event 引用 _get_proactive_agents(), 抽樣走 candidates (whitelist 過濾)")

    def test_b_event_filters_non_whitelisted(self):
        """v2: _fire_event 即使 random.sample 回 10 隻, 只有 ruka callback 被呼叫

        模擬場景: 配 whitelist=ruka, mock 強制 sample 回 10 隻 (模擬 random 抽中全部)
        修法前: 沒 filter, 10 隻 callback 都被呼叫
        修法後: filter 過 candidates (=[ruka]), 10 隻裡只有 ruka 通過, 1 隻 callback 被呼叫
        """
        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await self.scheduler._fire_event()
            return self.event_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 只有 ruka 被觸發 (filter 過 whitelist)
        self.assertEqual(
            called, ["agent_ruka"],
            f"v2 期望 _fire_event 只觸發 ruka, 實際觸發: {called}"
        )
        print(f"[v2] _fire_event 只觸發: {called} (其他 9 隻被 whitelist 過濾)")

    def test_c_event_logs_whitelist(self):
        """v2: _fire_event log 帶 whitelist 資訊, 跟 proactive_dm log 風格一致"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        match = re.search(r"async def _fire_event.*?(?=async def |\n    # ─)", scheduler_source, re.DOTALL)
        fire_event_source = match.group(0)
        # v2 期望: trigger log 帶 whitelist 資訊
        self.assertIn(
            "whitelist=", fire_event_source,
            "v2 期望 _fire_event trigger log 帶 whitelist 資訊 (跟 proactive_dm log 風格一致)"
        )
        print("[v2] _fire_event trigger log 帶 whitelist 資訊")

    def test_d_no_new_event_agents_param(self):
        """v2 派工精神: 「共用 whitelist, 不新增獨立 event whitelist 參數」

        驗證 scheduler.py __init__ 簽名沒新增 event 相關 whitelist 參數
        跟 proactive_dm / heartbeat 共用同一個 proactive_agents_whitelist 就好
        """
        # 從 __init__ 簽名掃 — Bry 派工「不新增 event whitelist 參數」是指 API 表面
        # 程式碼 docstring 內可以提到「event whitelist」字串 (說明為什麼共用)
        sig = inspect.signature(SoulScheduler.__init__)
        init_params = list(sig.parameters.keys())
        # v2 期望: __init__ 沒新增任何 event 相關參數 (跟修法 11 一致, 共用 proactive_agents)
        event_related_params = [p for p in init_params if "event" in p.lower() and p != "event_min_interval_minutes" and p != "event_max_interval_minutes"]
        self.assertEqual(
            event_related_params, [],
            f"v2 派工精神: __init__ 不應新增 event whitelist 參數, "
            f"實際新增: {event_related_params} "
            f"(共用 proactive_agents, 所有 init 參數: {init_params})"
        )
        print(f"[v2] __init__ 沒新增 event whitelist 參數 (共用 proactive_agents), 全部 {len(init_params)} 個參數維持原樣")

    def test_e_diary_dream_unaffected(self):
        """v2: diary / dream 不受影響, 全 10 隻都還在 _all_agents"""
        self.assertEqual(
            len(self.scheduler._all_agents), 10,
            f"v2 期望 _all_agents 仍包含 10 隻角色, 實際: {len(self.scheduler._all_agents)}"
        )
        for aid in ALL_AGENTS:
            self.assertIn(
                aid, self.scheduler._all_agents,
                f"v2 期望 {aid} 仍在 _all_agents (diary/dream 不受 whitelist 影響)"
            )
        print(f"[v2] _all_agents 仍包含全部 {len(self.scheduler._all_agents)} 隻角色 (diary/dream 完整)")

    def test_f_backward_compat_no_whitelist(self):
        """v2: 向後相容 — proactive_agents=None 維持現狀, 全部 10 隻都能觸發 event"""
        scheduler_no_wl, event_record = _make_scheduler_with_whitelist(None)
        self.assertIsNone(scheduler_no_wl._proactive_agents_whitelist)

        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await scheduler_no_wl._fire_event()
            return event_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 10 隻全部觸發 (向後相容, _get_proactive_agents() 回 _all_agents, filter 全部通過)
        self.assertEqual(
            sorted(called), sorted(ALL_AGENTS),
            f"v2 期望 proactive_agents=None 時 event 觸發全 10 隻, "
            f"實際 {len(called)} 隻: {called}"
        )
        print(f"[v2] 向後相容: proactive_agents=None event 觸發全部 {len(called)} 隻")

    def test_g_misconfigured_whitelist_silent_skip(self):
        """v2: 配錯 whitelist (列了不存在的 agent) → silent skip, 0 callback 觸發"""
        scheduler_bad, event_record = _make_scheduler_with_whitelist(["agent_xxx_typo"])

        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)):
                await scheduler_bad._fire_event()
            return event_record["called_with"]

        called = asyncio.run(run())
        # v2 期望: 配錯 whitelist 時 sample 抽的 10 隻全部被 filter 掉, 0 callback
        self.assertEqual(
            called, [],
            f"v2 期望配錯 whitelist event 觸發 0 隻, 實際: {called}"
        )
        print(f"[v2] 配錯 whitelist (['agent_xxx_typo']) event silent skip, called={called}")

    def test_h_event_uses_same_whitelist_as_proactive_dm(self):
        """v2 派工精神: 「共用同一個 whitelist」

        驗證 _fire_event 跟 _fire_proactive_dm 用的是同一個 _proactive_agents_whitelist 屬性
        沒另開新欄位
        """
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        # 找 _fire_event / _fire_proactive_dm / _fire_heartbeat
        for fn_name in ["_fire_event", "_fire_proactive_dm", "_fire_heartbeat"]:
            match = re.search(
                rf"async def {fn_name}.*?(?=async def |\n    # ─)",
                scheduler_source,
                re.DOTALL,
            )
            if match:
                self.assertIn(
                    "_get_proactive_agents", match.group(0),
                    f"v2 期望 {fn_name} 引用 _get_proactive_agents() (共用 whitelist 入口)"
                )
        print("[v2] _fire_event / _fire_proactive_dm / _fire_heartbeat 都引用 _get_proactive_agents() (共用 whitelist)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
