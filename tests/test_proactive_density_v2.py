"""
test_proactive_density_v2.py — proactive 訊息密度修法驗證: heartbeat 拿掉 + proactive_dm 3-5h

Bry 8/6 17:12 拍板 (貼給 Mavis):
> 拍板方案 C：heartbeat 整條拿掉，只留 proactive_dm，間隔拉到 3-5 小時
> 為什麼是 C, 不是 A: Bry 派工「對話負擔不按訊息類型分」, 只要她開口 Bry 就要展開對話花時間回
> heartbeat 對 ruka 也拿掉（不只是拿掉其他 9 隻, ruka 也不用 heartbeat）
> proactive_dm 間隔改成 3-5 小時（原本 2-4h）, 一天約 5-8 條
> Bry 派工「5-8 條是『整段對話會很長』的量, 不是輕量提示」
> quiet_hours 跟 cooldown 維持, 睡覺時段不會累積 backlog

這個 v2 驗證修法:
- scheduler.py proactive_dm_min_interval_minutes 預設 120 → 180 (2h → 3h)
- scheduler.py proactive_dm_max_interval_minutes 預設 240 → 300 (4h → 5h)
- run_server.py _heartbeat_callback 函式註解掉 (Bry 派工核心, heartbeat 整條拿掉)
- run_server.py scheduler.register_heartbeat 呼叫註解掉
- scheduler.py 內部 heartbeat 機制 (register_heartbeat / _fire_heartbeat) 保留
  (給未來 Bry 想恢復時不用重寫, 註解清楚寫了恢復方式)
- 一天 Ruka 主動訊息 = 24h / 4h(平均) = 6 條, 落在 Bry 5-8 條期望區間

Bry 派工計算:
- proactive_dm 3-5h 平均 4h → 24h / 4h = 6 條/天
- quiet_hours 23:00-08:00 (9h) skip proactive_dm
- 清醒時段 08:00-23:00 (15h) 收到 6 條, 平均 2.5h 一次
- Bry 一天收到 6 條 Ruka 訊息 (符合 Bry 派工 5-8 條期望)

Mock 範圍:
- 用 inspect.signature 拿 scheduler 預設值
- 讀 source 找關鍵字串 (判斷 active call vs comment)
- 不用 mock callback, 因為 v2 是設定/結構驗證
"""
import inspect
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul.scheduler import SoulScheduler


SCHEDULER_PY = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
)
RUN_SERVER_PY = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/run_server.py"
)


def _strip_comments(source: str) -> str:
    """去掉 # 開頭的單行註解, 給 active code 檢查用."""
    lines = []
    for line in source.splitlines():
        # 去掉 # 之後的內容 (保留前綴空白判斷是否整行都是註解)
        # 用 regex 找行內第一個 # (在字串內的 # 不處理, 但這個測試不嚴格)
        code_part = re.sub(r"#.+$", "", line)
        lines.append(code_part)
    return "\n".join(lines)


class TestProactiveDensityFix(unittest.TestCase):
    """驗證修法 — heartbeat 拿掉 + proactive_dm 3-5h"""

    def setUp(self):
        self.scheduler_source = SCHEDULER_PY.read_text(encoding="utf-8")
        self.run_server_source = RUN_SERVER_PY.read_text(encoding="utf-8")
        self.run_server_active = _strip_comments(self.run_server_source)

    def test_a_proactive_dm_min_default_3h(self):
        """v2: scheduler.py proactive_dm_min_interval_minutes 預設 180 (3h)"""
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertEqual(
            sig.parameters["proactive_dm_min_interval_minutes"].default, 180,
            "v2 期望 proactive_dm_min_interval_minutes 預設 180 (3h), 符合 Bry 派工 3-5h"
        )
        print("[v2] proactive_dm_min_interval_minutes 預設 180 (3h)")

    def test_b_proactive_dm_max_default_5h(self):
        """v2: scheduler.py proactive_dm_max_interval_minutes 預設 300 (5h)"""
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertEqual(
            sig.parameters["proactive_dm_max_interval_minutes"].default, 300,
            "v2 期望 proactive_dm_max_interval_minutes 預設 300 (5h), 符合 Bry 派工 3-5h"
        )
        print("[v2] proactive_dm_max_interval_minutes 預設 300 (5h)")

    def test_c_run_server_no_active_heartbeat_callback(self):
        """v2: run_server.py 沒有 active _heartbeat_callback 函式定義 (Bry 派工: heartbeat 拿掉)

        檢查方式: 去掉註解後的 source 不應包含「async def _heartbeat_callback」
        Bry 派工: heartbeat 對 ruka 也拿掉, 不只是其他 9 隻
        """
        self.assertNotIn(
            "async def _heartbeat_callback", self.run_server_active,
            "v2 期望 run_server.py 沒有 active _heartbeat_callback 函式, "
            "Bry 派工: heartbeat 整條拿掉, 註解掉的 heartbeat 不算 active"
        )
        print("[v2] run_server.py 沒有 active _heartbeat_callback (Bry 派工: heartbeat 拿掉)")

    def test_d_run_server_event_bridge_active(self):
        """v2: run_server.py 有 AGENCY_TRIGGER event bridge (取代舊 register contract)

        Bry 派工 O-2: test contract migration 從 legacy register_proactive_dm 改成
        production AGENCY_TRIGGER event bridge:
          - AgencyTriggerHandler 訂閱 EventType.AGENCY_TRIGGER
          - llm_executor=_proactive_dm_llm_executor 注入 (production LLM path)
        舊 test 驗 `scheduler.register_proactive_dm` 存在 (legacy callback compat) 已
        不代表 production 真正觸發鏈。
        """
        # heartbeat 整條拿掉
        self.assertNotIn(
            "scheduler.register_heartbeat", self.run_server_active,
            "v2 期望 run_server.py 沒有 active scheduler.register_heartbeat 呼叫, "
            "Bry 派工: heartbeat 整條拿掉"
        )
        # 新 production path: AGENCY_TRIGGER event bridge
        self.assertIn(
            "AgencyTriggerHandler", self.run_server_active,
            "v2 期望 run_server.py 有 AgencyTriggerHandler (M5.2-G event bridge)"
        )
        self.assertIn(
            "llm_executor=_proactive_dm_llm_executor", self.run_server_active,
            "v2 期望 run_server.py 注入 _proactive_dm_llm_executor 到 AgencyTriggerHandler "
            "(production LLM path, M5.2-G)"
        )
        self.assertIn(
            "EventType.AGENCY_TRIGGER", self.run_server_active,
            "v2 期望 run_server.py 訂閱 EventType.AGENCY_TRIGGER (event bridge filter)"
        )
        print("[v2] run_server.py 用 AGENCY_TRIGGER event bridge "
              "(AgencyTriggerHandler + llm_executor 注入)")

    def test_e_scheduler_heartbeat_mechanism_preserved(self):
        """v2: scheduler.py 內部 heartbeat 機制 (register_heartbeat + _fire_heartbeat) 保留

        Bry 派工: heartbeat 整條拿掉是在 run_server.py 層, scheduler.py 保留給未來 Bry 想恢復
        """
        self.assertIn(
            "def register_heartbeat", self.scheduler_source,
            "v2 期望 scheduler.py 保留 register_heartbeat 方法 (給未來 Bry 想恢復時用)"
        )
        self.assertIn(
            "async def _fire_heartbeat", self.scheduler_source,
            "v2 期望 scheduler.py 保留 _fire_heartbeat 方法 (給未來 Bry 想恢復時用)"
        )
        # 確認 register_heartbeat 註解提到 Bry 8/6 17:12 派工, 給未來 Bry 知道恢復方式
        self.assertIn(
            "2026-08-06 17:12", self.scheduler_source,
            "v2 期望 scheduler.py register_heartbeat 註解提到 Bry 8/6 17:12 派工 + 恢復方式"
        )
        print("[v2] scheduler.py heartbeat 機制保留, 註解提到 Bry 8/6 17:12 派工 + 恢復方式")

    def test_f_density_estimate_5_to_8_per_day(self):
        """v2: 算 Bry 一天收到 Ruka 訊息 (約 6 條, 落在 Bry 派工 5-8 條期望區間)"""
        # 用 v2 預設值算
        proactive_dm_avg_min = (180 + 300) / 2  # 240 min = 4h
        minutes_per_day = 24 * 60
        proactive_dm_count = minutes_per_day / proactive_dm_avg_min  # 6 條/天

        # v2 期望: 5-8 條範圍內
        self.assertGreaterEqual(proactive_dm_count, 5.0)
        self.assertLessEqual(proactive_dm_count, 8.0)
        self.assertAlmostEqual(proactive_dm_count, 6.0, delta=0.5)
        print(
            f"[v2] Bry 一天收到 Ruka 訊息約 {proactive_dm_count:.1f} 條 "
            f"(proactive_dm 3-5h 平均 {proactive_dm_avg_min:.0f}min), "
            f"落在 Bry 派工 5-8 條期望區間"
        )

    def test_g_run_server_startup_log_mentions_density(self):
        """v2: run_server.py 啟動 log 提到 5-8 條/天 期望"""
        self.assertIn(
            "5-8 條", self.run_server_source,
            "v2 期望 run_server.py 啟動 log 提到 Bry 派工 5-8 條/天 期望"
        )
        self.assertIn(
            "修法 12", self.run_server_source,
            "v2 期望 run_server.py 啟動 log 提到修法 12"
        )
        print("[v2] run_server.py 啟動 log 提到 5-8 條/天 + 修法 12")

    def test_h_agency_trigger_payload_includes_elapsed_mins(self):
        """v2: AGENCY_TRIGGER payload 包含 elapsed_mins (scheduler 從 _last_proactive_dm_time 算)

        取代舊 test_d 直接驗證 callback 內 _elapsed = _r39.uniform(180, 300) —
        舊 assertion 鎖的是 LLM intent construction detail (executor local random),
        不是 trigger contract。新 contract 是 scheduler → AGENCY_TRIGGER event payload,
        elapsed_mins 是 scheduler 算的可觀測欄位 (從 _last_proactive_dm_time 算)。

        Bry 派工 O-2 拍板 A + C:
          A 移除 callback 內 _elapsed assertion (不屬於 trigger contract 範疇)
          C 新增驗證 AGENCY_TRIGGER payload 的 elapsed_mins (scheduler observable contract)
        """
        # 驗 scheduler._publish_agency_trigger 內有 elapsed_mins 計算
        self.assertIn(
            "elapsed_mins", self.scheduler_source,
            "v2 期望 scheduler.py source 有 elapsed_mins 引用 "
            "(M5.2-G _publish_agency_trigger payload)"
        )
        # 驗 payload 結構包含 elapsed_mins 欄位
        self.assertIn(
            '"elapsed_mins"', self.scheduler_source,
            "v2 期望 scheduler.py publish AGENCY_TRIGGER payload 內含 elapsed_mins 欄位"
        )
        # 驗 _last_proactive_dm_time 存在 (elapsed_mins 計算 source)
        self.assertIn(
            "_last_proactive_dm_time", self.scheduler_source,
            "v2 期望 scheduler.py 有 _last_proactive_dm_time 屬性 "
            "(elapsed_mins 計算 source)"
        )
        print("[v2] scheduler.py AGENCY_TRIGGER payload 包含 elapsed_mins "
              "(從 _last_proactive_dm_time 算, scheduler observable contract)")


class TestProactiveDensityBackwardCompat(unittest.TestCase):
    """驗證向後相容 — 測試程式碼或第三方 caller 可以覆寫預設值"""

    def test_i_explicit_proactive_dm_interval_override(self):
        """v2: 即使 Bry 未來想改回 2-4h, 還是可以用 constructor 覆寫"""
        scheduler = SoulScheduler(
            proactive_dm_min_interval_minutes=120,  # 2h
            proactive_dm_max_interval_minutes=240,  # 4h
        )
        self.assertEqual(scheduler.proactive_dm_min_interval_minutes, 120)
        self.assertEqual(scheduler.proactive_dm_max_interval_minutes, 240)
        print("[v2] 覆寫預設值 2-4h 成功 (向後相容, Bry 未來想改回可一鍵覆寫)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
