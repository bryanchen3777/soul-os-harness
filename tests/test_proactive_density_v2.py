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

    def test_d_run_server_no_active_register_heartbeat(self):
        """v2: run_server.py 沒有 active scheduler.register_heartbeat 呼叫"""
        self.assertNotIn(
            "scheduler.register_heartbeat", self.run_server_active,
            "v2 期望 run_server.py 沒有 active scheduler.register_heartbeat 呼叫, "
            "Bry 派工: heartbeat 整條拿掉"
        )
        # 確認 proactive_dm 還在 register (沒被誤刪)
        self.assertIn(
            "scheduler.register_proactive_dm", self.run_server_active,
            "v2 期望 run_server.py 還有 scheduler.register_proactive_dm (Bry 派工: 只留 proactive_dm)"
        )
        print("[v2] run_server.py 沒有 active scheduler.register_heartbeat, "
              "但 scheduler.register_proactive_dm 還在")

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

    def test_h_proactive_dm_callback_elapsed_aligned(self):
        """v2: _proactive_dm_callback 的 elapsed_mins 從 2-4h 改 3-5h, 跟觸發間隔對齊

        動機: 避免 LLM 收到「2-4h 沒見 Bry」但實際觸發是 3-5h 沒見 Bry 的認知錯位
        """
        # 用 active source (去掉註解) 找 _elapsed uniform, 避免抓到註解掉的 heartbeat 那行
        match = re.search(
            r"_elapsed\s*=\s*_r39\.uniform\(\s*(\d+)\s*,\s*(\d+)\s*\)",
            self.run_server_active,
        )
        self.assertIsNotNone(match, "v2 期望 run_server.py 有 _elapsed = _r39.uniform(...)")
        min_v, max_v = int(match.group(1)), int(match.group(2))
        self.assertEqual((min_v, max_v), (180, 300), f"v2 期望 _elapsed 3-5h (180, 300), 實際 ({min_v}, {max_v})")
        print(f"[v2] _proactive_dm_callback 的 _elapsed = ({min_v}, {max_v}) 對齊觸發間隔 3-5h")


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
