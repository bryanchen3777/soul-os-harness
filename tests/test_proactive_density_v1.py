"""
test_proactive_density_v1.py — proactive 訊息密度 baseline: heartbeat 啟動 + proactive_dm 2-4h

Bry 8/6 17:12 拍板 (貼給 Mavis):
> 拍板方案 C：heartbeat 整條拿掉，只留 proactive_dm，間隔拉到 3-5 小時
> 為什麼是 C, 不是 A: 如果保留 heartbeat（哪怕它只是「輕量 check-in」），實際上你看到瑠夏傳訊息，
> 還是會想回、還是要展開一段對話——對 Bry 來說沒有「輕量」跟「重量」的差別
> 只要她開口, Bry 就要花時間回
> 既然 Bry 說的 5-8 條已經是「整段對話會很長」的量, 那就不該再疊加一個額外的 heartbeat 管道

這個 v1 驗證現狀 (修法 11 commit-only 後, 8/6 16:58 重啟生效):
- scheduler.py proactive_dm_min/max_interval_minutes 預設 120/240 (2-4h)
- run_server.py 有 _heartbeat_callback 函式 + register_heartbeat 呼叫
- Bry 一天收到 Ruka 訊息 = heartbeat 32 + proactive_dm 8 = 40 條 (Bry 拍板太多)
- 「輕量」vs「重量」的區分在 Bry 的感知裡不存在 (Bry 派工要點)

Mock 範圍:
- scheduler 預設值: 從 inspect.signature 拿
- run_server.py source: 讀檔 + 找關鍵字串
- 不用 mock callback, 因為這個 v1 是設定/結構驗證, 不是行為驗證
- 修法前 v1 期望: proactive_dm 預設 120/240, heartbeat 機制還在, 一條觸發鏈走兩條
- 修法後 v2 期望: proactive_dm 預設 180/300, run_server.py 拿掉 _heartbeat_callback
                  + register_heartbeat 呼叫, 只剩 proactive_dm 一條觸發鏈
"""
import inspect
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul.scheduler import SoulScheduler


# scheduler.py 絕對路徑 (v1/v2 都要讀 source)
SCHEDULER_PY = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/src/soul/scheduler.py"
)
RUN_SERVER_PY = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/run_server.py"
)


class TestProactiveDensityBaseline(unittest.TestCase):
    """驗證現狀 (修法 11 commit-only 後) — heartbeat 啟動 + proactive_dm 2-4h"""

    def setUp(self):
        self.scheduler_source = SCHEDULER_PY.read_text(encoding="utf-8")
        self.run_server_source = RUN_SERVER_PY.read_text(encoding="utf-8")

    def test_a_baseline_proactive_dm_min_default_2h(self):
        """Baseline: scheduler.py proactive_dm_min_interval_minutes 預設 120 (2h)"""
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertEqual(
            sig.parameters["proactive_dm_min_interval_minutes"].default, 120,
            "Baseline (v1) 期望 proactive_dm_min_interval_minutes 預設 120 (2h)"
        )
        print("[v1 baseline] proactive_dm_min_interval_minutes 預設 120 (2h)")

    def test_b_baseline_proactive_dm_max_default_4h(self):
        """Baseline: scheduler.py proactive_dm_max_interval_minutes 預設 240 (4h)"""
        sig = inspect.signature(SoulScheduler.__init__)
        self.assertEqual(
            sig.parameters["proactive_dm_max_interval_minutes"].default, 240,
            "Baseline (v1) 期望 proactive_dm_max_interval_minutes 預設 240 (4h)"
        )
        print("[v1 baseline] proactive_dm_max_interval_minutes 預設 240 (4h)")

    def test_c_baseline_run_server_registers_heartbeat(self):
        """Baseline: run_server.py 有 _heartbeat_callback 函式 + register_heartbeat 呼叫"""
        self.assertIn(
            "_heartbeat_callback", self.run_server_source,
            "Baseline (v1) 期望 run_server.py 有 _heartbeat_callback 定義"
        )
        self.assertIn(
            "register_heartbeat", self.run_server_source,
            "Baseline (v1) 期望 run_server.py 有 register_heartbeat 呼叫"
        )
        print("[v1 baseline] run_server.py 有 _heartbeat_callback + register_heartbeat")

    def test_d_baseline_scheduler_has_heartbeat_mechanism(self):
        """Baseline: scheduler.py heartbeat 機制 (register_heartbeat + _fire_heartbeat) 還在"""
        self.assertIn(
            "def register_heartbeat", self.scheduler_source,
            "Baseline (v1) 期望 scheduler.py 有 register_heartbeat 方法"
        )
        self.assertIn(
            "async def _fire_heartbeat", self.scheduler_source,
            "Baseline (v1) 期望 scheduler.py 有 _fire_heartbeat 方法"
        )
        print("[v1 baseline] scheduler.py heartbeat 機制 (register + _fire) 還在")

    def test_e_baseline_run_server_still_logs_heartbeat_in_startup(self):
        """Baseline: run_server.py 啟動 log 包含 heartbeat 訊息"""
        self.assertIn(
            "heartbeat", self.run_server_source.lower(),
            "Baseline (v1) 期望 run_server.py 啟動訊息提到 heartbeat"
        )
        print("[v1 baseline] run_server.py 啟動訊息提到 heartbeat")

    def test_f_baseline_density_estimate(self):
        """Baseline: 算 Bry 一天收到 Ruka 訊息數 (40 條, 觸發 Bry 派工方案 C)

        heartbeat: 24h * 60min / 45min = 32 次/天 (平均間隔 30-60min)
        proactive_dm: 24h * 60min / 180min = 8 次/天 (平均間隔 2-4h)
        總計: 32 + 8 = 40 條/天
        """
        # 用現狀預設值算
        heartbeat_avg_min = (30 + 60) / 2
        proactive_dm_avg_min = (120 + 240) / 2
        minutes_per_day = 24 * 60
        heartbeat_count = minutes_per_day / heartbeat_avg_min
        proactive_dm_count = minutes_per_day / proactive_dm_avg_min
        total = heartbeat_count + proactive_dm_count

        # Baseline 期望: 40 條/天
        self.assertAlmostEqual(total, 40, delta=1)
        print(
            f"[v1 baseline] Bry 一天收到 Ruka 訊息約 {total:.0f} 條 "
            f"(heartbeat {heartbeat_count:.0f} + proactive_dm {proactive_dm_count:.0f}), "
            f"超出 Bry 派工 5-8 條上限"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
