"""
test_m1_7_event_whitelist_v1.py — M1.7 baseline: event trigger 沒有過 whitelist 過濾

Bry 拍板 2026-08-07 15:00 (貼給 Mavis):
> 這次抓到的是修法 11 真正的漏洞 — 中午 12:30 的 anna+ram 事件觸發,
> 才是 whitelist 真正沒擋到的地方
> 判斷: A 方案就足夠, 不用做到 B
> 派 Mavis 用 A 方案修: 在 event scheduler trigger 那條路徑 (跟 proactive_dm 一樣)
> 加上修法 11 的 whitelist 過濾

這個 v1 驗證現狀 (before M1.7 修法):
- scheduler._fire_event 從 self._all_agents 隨機抽 2 隻, 沒有過 whitelist 過濾
- 即使 proactive_agents=["agent_ruka"] (只有 ruka 在白名單),
  _fire_event 仍會觸發非白名單角色 (因為從 _all_agents 抽)
- 12:30 案例就是這樣炸的: anna + ram 不在 whitelist, 但 event 從 _all_agents 抽出她們,
  觸發 agent_intent → agent_speak → TG 發送給 Bry

Mock 範圍:
- 建立 SoulScheduler(proactive_agents=["agent_ruka"])
- 註冊 10 隻角色到 _all_agents
- 註冊 event callback (用 mock async function 記錄被觸發的 agent_id)
- mock random.sample 回傳 10 隻, 模擬「抽中全部」
- 直接呼叫 _fire_event, 驗證:
  a. 修法前 v1 期望: 10 隻角色都被觸發 (因為從 _all_agents 抽, 沒過濾)
  b. 修法後 v2 期望: 只有 ruka 被觸發, 其他 9 隻 silent skip
  c. 向後相容: proactive_agents=None 時仍觸發全 10 隻
  d. diary / dream 不受影響 (仍對 _all_agents 全部觸發)

Bry 派工原文 (要保留給未來 session 看):
- 「目前系統裡『會主動發訊息給你』的路徑, 實際上就只有 proactive_dm 跟 event 這兩個 scheduler trigger」
- 「A 方案跟 B 方案覆蓋範圍等同時, 改動更小的優先」
- 「不為假設中的未來灑過濾網」
- 「更貼合修法 11 當初 narrow 派工的精神」
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


def _make_event_callback_recorder():
    """建立一個記錄所有被觸發 agent_id 的 event callback (signature: async def cb(agent_id, slot))."""
    record = {"called_with": []}

    async def callback(agent_id: str, slot: str) -> None:
        record["called_with"].append(agent_id)

    return callback, record


def _make_scheduler_with_whitelist(whitelist):
    """建立 SoulScheduler, 註冊 10 隻角色 + event callback."""
    event_cb, event_record = _make_event_callback_recorder()
    scheduler = SoulScheduler(proactive_agents=whitelist)
    scheduler.register_dream_event(dream_callback=lambda a, s: asyncio.sleep(0),
                                   event_callback=event_cb)
    for aid in ALL_AGENTS:
        scheduler._all_agents.append(aid)
    return scheduler, event_record


class TestM17EventWhitelistBaseline(unittest.TestCase):
    """驗證現狀 (before M1.7 修法) — event 從 _all_agents 抽, 沒過 whitelist"""

    def setUp(self):
        # 配 ruka whitelist, 模擬修法 11 的設定
        self.scheduler, self.event_record = _make_scheduler_with_whitelist(WHITELIST_RUKA)

    def test_a_baseline_event_fires_all_agents(self):
        """M1.7 已套用: _fire_event 從 _all_agents 抽, 但 filter 過 whitelist → 只觸發 ruka

        本測試原本寫成 v1 baseline (期望 10 隻全部被觸發, 即修法前沒過濾)。
        Bry 拍板 2026-08-07 15:00 M1.7 之後:
        - src/soul/scheduler.py:936 `candidates = self._get_proactive_agents()`
        - src/soul/scheduler.py:937 `agents = [a for a in raw_picks if a in candidates]`
        - src/soul/scheduler.py:1148 `_get_proactive_agents()` = whitelist ∩ _all_agents

        M5.2-I Phase 6 (Bry 拍板 2026-08-08) 之後觸發 = publish AGENCY_TRIGGER,
        不再呼叫 event callback (scheduler.py:924-927), 所以這裡改驗 publish。
        """
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        async def run():
            with patch("src.soul.scheduler.random.sample", return_value=list(ALL_AGENTS)), \
                 patch.object(self.scheduler, "_publish_agency_trigger", fake_publish):
                await self.scheduler._fire_event()
            return published

        published = asyncio.run(run())
        self.assertEqual(
            sorted(published), [("agent_ruka", "event")],
            f"M1.7 (Bry 拍板 2026-08-07 15:00): 配 whitelist=['agent_ruka'] 時 "
            f"_fire_event 只應該 publish ruka, 實際: {published}"
        )
        print(f"[M1.7 已套用] _fire_event 抽 10 隻但只 publish ruka: {published}")

    def test_b_baseline_event_ignores_whitelist(self):
        """M1.7 已套用: 抽到非白名單角色 (yua/anna) 全被 filter, 0 隻 publish

        本測試原本寫成 v1 baseline (期望 yua+anna 都被觸發, 即修法前不過濾)。
        Bry 拍板 2026-08-07 15:00 M1.7 之後 scheduler.py:937 過濾掉非 candidates,
        全部被 filter 掉時走 scheduler.py:938-953 的 silent skip 分支 (只排下次時間)。
        """
        published = []

        async def fake_publish(agent_id, trigger_type, extra=None):
            published.append((agent_id, trigger_type))

        async def run():
            with patch("src.soul.scheduler.random.sample",
                       return_value=["agent_yua", "agent_anna"]), \
                 patch.object(self.scheduler, "_publish_agency_trigger", fake_publish):
                await self.scheduler._fire_event()
            return published

        published = asyncio.run(run())
        self.assertEqual(
            published, [],
            f"M1.7: 抽到的 yua/anna 都不在 whitelist, 應該全部被 filter (silent skip), "
            f"實際 publish: {published}"
        )
        print(f"[M1.7 已套用] _fire_event 抽到非白名單 yua/anna → 0 隻 publish (silent skip)")

    def test_c_baseline_proxy_source_event_no_whitelist(self):
        """M1.7 已套用: scheduler.py _fire_event 用 _get_proactive_agents() 過濾"""
        scheduler_source = Path(
            "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
        ).read_text(encoding="utf-8")
        # 找 _fire_event 函式
        import re
        match = re.search(r"async def _fire_event.*?(?=async def |\n    # ─)", scheduler_source, re.DOTALL)
        self.assertIsNotNone(match, "找不到 _fire_event 函式, 邏輯可能變了")
        fire_event_source = match.group(0)
        # M1.7 之後: 仍從 self._all_agents 抽 (跟修法 11 heartbeat pattern 對齊)
        self.assertIn(
            "self._all_agents", fire_event_source,
            "M1.7: _fire_event 仍從 self._all_agents 抽 (scheduler.py:934)"
        )
        # M1.7 之後: 過濾用 _get_proactive_agents() (scheduler.py:936)
        self.assertIn(
            "_get_proactive_agents", fire_event_source,
            "M1.7 (Bry 拍板 2026-08-07 15:00): _fire_event 應該用 _get_proactive_agents() "
            "過濾 (scheduler.py:936) — 找不到就是修法被回退"
        )
        self.assertIn(
            "if a in candidates", fire_event_source,
            "M1.7: _fire_event 應該把 raw_picks 過濾成 candidates 交集 (scheduler.py:937)"
        )
        print("[M1.7 已套用] _fire_event 從 self._all_agents 抽 + _get_proactive_agents() 過濾")


if __name__ == "__main__":
    unittest.main(verbosity=2)
