"""
test_ta1_simulation_day.py — TA-1 模拟测试: SimulationClock 模拟一整天

工單 TA-1 (Bry 拍板 2026-08-30): 用 Time-lapse Harness 的 SimulationClock 模擬一整天
(早上 8:00 → 中午 11:45 → 下午 15:30 → 晚上 22:30), 每個時段餵同一 probe
(「在嗎」), 驗證靈魂的時間感知 (temporal coherence + continuity)。

驗收:
- temporal coherence: 固定時鐘 fixture 下, 時間訊號正確驅動時段感知
  (早上 → 早上訊號, 不道晚安; 中午 → 中午訊號, 不問早餐;
   下午 → 下午訊號, 不問早餐; 晚上 → 晚上訊號, 不道早安)
- temporal continuity: conversation_elapsed + last_interaction_period 注入
- silence bug 修復: _get_bry_latest_ts 取到真實值 → 沉默時長行注入
  (修復前 suffix 拼錯 → bry_latest_ts 恆 0 → 沉默時長行從未注入)

SimulationClock 用法 (harness/clock.py, TL-1):
- SimulationClock(start_day=0) = 模擬的一天 (day 0 = 2026-09-01T00:00:00+00:00)
- 四個時段是「同一天」內的不同時刻 (EDT 夏季 = UTC-4):
  早上 8:00 EDT = 12:00 UTC / 中午 11:45 EDT = 15:45 UTC
  下午 15:30 EDT = 19:30 UTC / 晚上 22:30 EDT = 02:30 UTC (次日)
- 每個時段餵同一 probe「在嗎」, 驗證 system prompt 時間區塊 (LLM 生成
  符合時間回應的輸入訊號) 正確驅動時段感知
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness.clock import SimulationClock
from src.llm import proxy

# ── SimulationClock: 模擬的一天 (day 0 = 2026-09-01 UTC) ──
SIM_CLOCK = SimulationClock(start_day=0)
SIM_DAY_LABEL = SIM_CLOCK.label(0)          # "D0"
SIM_DAY_ISO = SIM_CLOCK.sim_ts(0)            # "2026-09-01T00:00:00+00:00"

# ── 四個時段 fixture (EDT 夏季 = UTC-4, 同一天) ──
# 早上 8:00 EDT = 12:00 UTC
MORNING_TS = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
# 中午 11:45 EDT = 15:45 UTC
NOON_TS = datetime(2026, 9, 1, 15, 45, 0, tzinfo=timezone.utc)
# 下午 15:30 EDT = 19:30 UTC
AFTERNOON_TS = datetime(2026, 9, 1, 19, 30, 0, tzinfo=timezone.utc)
# 晚上 22:30 EDT = 02:30 UTC (次日)
NIGHT_TS = datetime(2026, 9, 2, 2, 30, 0, tzinfo=timezone.utc)

NOW_MORNING = int(MORNING_TS.timestamp())
NOW_NOON = int(NOON_TS.timestamp())
NOW_AFTERNOON = int(AFTERNOON_TS.timestamp())
NOW_NIGHT = int(NIGHT_TS.timestamp())

# 同一 probe: 每個時段餵同一句話
PROBE = "在嗎"

# 上次互動: 3 小時前 (每個時段的 3 小時前)
LAST_INTERACTION_3H = NOW_AFTERNOON - 3 * 3600
# Bry 上次說話: 5 小時前 (沉默時長, 不在線)
BRY_LAST_5H = NOW_AFTERNOON - 5 * 3600

# soul 文本: 不含任何時間字樣 (證明時段來自時間訊號, 不是 prompt 硬編碼)
SOUL_NO_TIME = "你是測試角色。你是一個溫柔的人。說話簡短。"


def _system_content(messages):
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


def _build_private(now, **overrides):
    """組 _build_messages_private 的標準參數 + mock 依賴 (fail-silent 全 mock 成空)"""
    args = dict(
        agent_id="agent_yua",
        soul=SOUL_NO_TIME,
        current_input=PROBE,
        memory_context="",
        memory=MagicMock(),
        mood=0.0,
        user_id="bryan",
        current_time="",
        event_ts=None,
        bry_latest_ts=0,
        world_context="",
        germ_anchor=None,
        last_interaction_ts=0,
    )
    args.update(overrides)
    fake_memory = args["memory"]
    fake_memory.get_recent_with_meta.return_value = []
    with patch.object(proxy, "_load_self_recent", return_value=[]), \
         patch.object(proxy, "_format_recent_inner_life", return_value=""), \
         patch.object(proxy, "_format_relationship_block", return_value=""), \
         patch.object(proxy, "_format_capability_block", return_value=""), \
         patch.object(proxy, "_format_emergent_block", return_value=""), \
         patch.object(proxy, "_format_attachment_str", return_value=""), \
         patch("time.time", return_value=now):
        return proxy._build_messages_private(**args)


class TestSimulationDayTemporalCoherence(unittest.TestCase):
    """temporal coherence: SimulationClock 模擬一天, 四個時段餵同一 probe"""

    def _run_period(self, now, ts):
        """餵同一 probe「在嗎」, 回傳 system prompt 內容"""
        return _system_content(_build_private(
            now,
            current_time=proxy._format_event_timestamp(ts),
            event_ts=ts,
        ))

    def test_morning_8am(self):
        """早上 8:00 → 時段行「早上」, 不道晚安 (不含晚上/凌晨訊號)"""
        content = self._run_period(NOW_MORNING, MORNING_TS)
        self.assertIn("## 當下時間", content)
        self.assertIn("（早上）", content)
        self.assertNotIn("（晚上）", content)
        self.assertNotIn("（凌晨）", content)

    def test_noon_1145(self):
        """中午 11:45 → 時段行「中午」, 不問早餐 (不含早上訊號)"""
        content = self._run_period(NOW_NOON, NOON_TS)
        self.assertIn("（中午）", content)
        self.assertNotIn("（早上）", content)
        self.assertNotIn("（晚上）", content)

    def test_afternoon_1530(self):
        """下午 15:30 → 時段行「下午」, 不問早餐 (不含早上訊號)"""
        content = self._run_period(NOW_AFTERNOON, AFTERNOON_TS)
        self.assertIn("（下午）", content)
        self.assertNotIn("（早上）", content)
        self.assertNotIn("（晚上）", content)

    def test_night_2230(self):
        """晚上 22:30 → 時段行「晚上」, 不道早安 (不含早上/中午訊號)"""
        content = self._run_period(NOW_NIGHT, NIGHT_TS)
        self.assertIn("（晚上）", content)
        self.assertNotIn("（早上）", content)
        self.assertNotIn("（中午）", content)

    def test_same_probe_all_periods(self):
        """同一 probe「在嗎」餵四個時段 → 時段訊號各不相同 (時間驅動, 非 probe 驅動)"""
        periods = {}
        for label, now, ts in [
            ("早上", NOW_MORNING, MORNING_TS),
            ("中午", NOW_NOON, NOON_TS),
            ("下午", NOW_AFTERNOON, AFTERNOON_TS),
            ("晚上", NOW_NIGHT, NIGHT_TS),
        ]:
            content = self._run_period(now, ts)
            # 每個時段都注入對應時段行
            self.assertIn(f"（{label}）", content)
            periods[label] = content
        # 四個時段訊號互不相同 (不是同一份 prompt 硬編碼)
        self.assertNotEqual(periods["早上"], periods["晚上"])
        self.assertNotEqual(periods["中午"], periods["下午"])

    def test_simulation_clock_day_anchor(self):
        """SimulationClock 錨定模擬的一天 (D0 = 2026-09-01 UTC)"""
        self.assertEqual(SIM_DAY_LABEL, "D0")
        self.assertEqual(SIM_DAY_ISO, "2026-09-01T00:00:00+00:00")
        # 四個時段都在模擬日 D0 的 EDT 視角內 (08:00 → 22:30 EDT)
        local_morning = MORNING_TS.astimezone(proxy.LOCAL_TZ)
        local_night = NIGHT_TS.astimezone(proxy.LOCAL_TZ)
        self.assertEqual(local_morning.hour, 8)
        self.assertEqual(local_night.hour, 22)


class TestSimulationDayContinuity(unittest.TestCase):
    """temporal continuity: conversation_elapsed + last_interaction_period"""

    def test_continuity_injected(self):
        """上次互動 3 小時前 → 注入「距離上次互動已經 3 小時（上次互動在{period}）」"""
        content = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=LAST_INTERACTION_3H,
        ))
        self.assertIn("距離上次互動已經 3 小時", content)
        self.assertIn("（上次互動在", content)

    def test_continuity_deterministic(self):
        """同一 fixture 連跑兩次 → 結果一致 (deterministic)"""
        def run():
            return _system_content(_build_private(
                NOW_AFTERNOON,
                current_time=proxy._format_event_timestamp(AFTERNOON_TS),
                event_ts=AFTERNOON_TS,
                last_interaction_ts=LAST_INTERACTION_3H,
            ))
        self.assertEqual(run(), run())

    def test_continuity_same_conversation_skipped(self):
        """elapsed < 15 分鐘 (同一場對話) → 不注入 continuity 行"""
        content = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=NOW_AFTERNOON - 5 * 60,
        ))
        self.assertNotIn("距離上次互動", content)


class TestSimulationDaySilenceFix(unittest.TestCase):
    """silence bug 修復: _get_bry_latest_ts 取到真實值 → 沉默時長行注入"""

    def test_silence_injected_when_offline(self):
        """Bry 5 小時前說話 (不在線) → 注入沉默時長行"""
        content = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            bry_latest_ts=BRY_LAST_5H,
        ))
        self.assertIn("距離 Bry 上次跟你說話已經 5 小時", content)

    def test_silence_skipped_when_online(self):
        """Bry 10 分鐘前說話 (在線) → 不注入沉默時長行 (講了反而多餘)"""
        content = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            bry_latest_ts=NOW_AFTERNOON - 10 * 60,
        ))
        self.assertNotIn("距離 Bry 上次跟你說話", content)

    def test_silence_skipped_never_spoke(self):
        """Bry 從未講過話 (bry_latest_ts=0) → 不注入沉默時長行"""
        content = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            bry_latest_ts=0,
        ))
        self.assertNotIn("距離 Bry 上次跟你說話", content)

    def test_get_bry_latest_ts_suffix_fixed(self):
        """suffix 修復: f\"_{agent_id}\" 匹配 session_bryan_agent_yua 格式"""
        # SQL: SELECT session_id, timestamp FROM messages WHERE role='user' AND speaker='bryan'
        rows = [
            ("session_bryan_agent_yua", 1000),
            ("session_1696287850_agent_yua", 2000),
            ("session_bryan_agent_akane", 9000),  # 不跨 agent
        ]
        m = MagicMock()
        m.conn.execute.return_value.fetchall.return_value = rows
        # 修復後: 只算 agent_yua 的 session, 取 max = 2000 (不跨到 akane 的 9000)
        self.assertEqual(proxy._get_bry_latest_ts(m, "agent_yua"), 2000)

    def test_get_bry_latest_ts_old_suffix_zero(self):
        """舊 suffix f\"_agent_{agent_id}\" 對真實格式 0 匹配 (bug 根因)"""
        rows = [
            ("session_bryan_agent_yua", 1000),
        ]
        m = MagicMock()
        m.conn.execute.return_value.fetchall.return_value = rows
        old_suffix = "_agent_agent_yua"
        matches = [ts for sid, ts in rows if sid.endswith(old_suffix) and ts]
        self.assertEqual(matches, [])


class TestRealMemoryDb(unittest.TestCase):
    """真實 memory.db 端到端: _get_bry_latest_ts 取到真實值 (production data 只讀)"""

    DB_PATH = Path(__file__).parent.parent / "data" / "memory.db"

    @unittest.skipUnless(DB_PATH.exists(), "data/memory.db 不存在 (CI 環境無 production data)")
    def test_real_db_get_bry_latest_ts(self):
        """真實 memory.db: _get_bry_latest_ts 取到真實值 (>0), 沉默時長行可注入"""
        from src.memory.store import MemoryStore
        memory = MemoryStore()
        try:
            bry_latest_ts = proxy._get_bry_latest_ts(memory, "agent_yua")
            self.assertGreater(bry_latest_ts, 0,
                               "修復後應取到真實值 (修復前 suffix 拼錯恆 0)")
            # 用取到的真實值 + 模擬 now (3h 後) 驗證沉默時長行注入
            silence_str = proxy._compute_silence_str(bry_latest_ts, bry_latest_ts + 3 * 3600)
            self.assertIsNotNone(silence_str)
            self.assertIn("3 小時", silence_str)
            # TA-1 對照: last_interaction_ts 也取到真實值
            last_ts = proxy._get_last_interaction_ts(memory, "agent_yua")
            self.assertGreater(last_ts, 0)
        finally:
            memory.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
