"""
test_ta1_temporal_orientation.py — TA-1: Restore Temporal Orientation & Continuity

Bry 拍板 2026-08-30 (工單 TA-1, IMPLEMENTATION / MINIMAL):
- conversation_elapsed 信號: last_interaction_at = max(last_user_ts, last_assistant_ts)
  (跨 session 取最大, 不跨 agent); 同一場對話 (elapsed < 15 分鐘) 不注入 continuity 行;
  從未互動整行省略; 現有「當下時間」事實保留
- TEMPORAL_EXPRESSION_RULE 放宽: 現象式時間允許 (早上/快中午/都下午了/這麼晚/週末),
  未經詢問不得主動報精確鐘點或日期; 不建模糊詞 whitelist; proxy 一條 precedence
  壓過 persona 絕對禁令 (不改 10 份人格檔); 不加 meta-instruction

驗收 (兩條行為測):
- temporal coherence: 固定時鐘 fixture 下, 時間訊號正確驅動時段感知
  (早上 fixture → 早上; 下午 fixture → 下午; 夜間 fixture → 晚上)
- temporal continuity: conversation_elapsed + last_interaction_period 注入,
  同一 fixture 連跑結果一致 (deterministic, 不是靠 prompt 硬編碼時間字樣)
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy

# ── 固定時鐘 fixture (EDT 夏季 = UTC-4) ──
# 早上 8:00 EDT = 12:00 UTC
MORNING_TS = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
# 下午 15:00 EDT = 19:00 UTC
AFTERNOON_TS = datetime(2026, 8, 5, 19, 0, 0, tzinfo=timezone.utc)
# 晚上 22:00 EDT = 02:00 UTC (次日)
NIGHT_TS = datetime(2026, 8, 6, 2, 0, 0, tzinfo=timezone.utc)

NOW_MORNING = int(MORNING_TS.timestamp())
NOW_AFTERNOON = int(AFTERNOON_TS.timestamp())
NOW_NIGHT = int(NIGHT_TS.timestamp())

# 上次互動: 3 小時前 (15:00 EDT 的 3 小時前 = 12:00 EDT = 中午)
LAST_INTERACTION_3H = NOW_AFTERNOON - 3 * 3600
# 上次互動: 2 天前 (15:00 EDT 的 2 天前 = 下午)
LAST_INTERACTION_2D = NOW_AFTERNOON - 2 * 24 * 3600
# 上次互動: 5 分鐘前 (同一場對話)
LAST_INTERACTION_5MIN = NOW_AFTERNOON - 5 * 60

# soul 文本: 不含任何時間字樣 (證明時段來自時間訊號, 不是 prompt 硬編碼)
SOUL_NO_TIME = "你是測試角色。你是一個溫柔的人。說話簡短。"


def _system_content(messages):
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


def _build_private(now, **overrides):
    """組 _build_messages_private 的標準參數 + mock 依賴 (fail-silent 全 mock 成空)"""
    args = dict(
        agent_id="agent_yua",
        soul=SOUL_NO_TIME,
        current_input="嗨",
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


def _build_group(now, **overrides):
    """組 _build_messages_group 的標準參數 + mock 依賴"""
    args = dict(
        agent_id="agent_yua",
        soul=SOUL_NO_TIME,
        current_input="嗨",
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
    fake_memory.get_group_history.return_value = []
    with patch.object(proxy, "_load_bry_recent", return_value=[]), \
         patch.object(proxy, "_format_recent_inner_life", return_value=""), \
         patch.object(proxy, "_format_relationship_block", return_value=""), \
         patch.object(proxy, "_format_capability_block", return_value=""), \
         patch.object(proxy, "_format_emergent_block", return_value=""), \
         patch.object(proxy, "_format_attachment_str", return_value=""), \
         patch("time.time", return_value=now):
        return proxy._build_messages_group(**args)


class TestTemporalCoherence(unittest.TestCase):
    """temporal coherence 行為測: 固定時鐘 fixture 驅動時段感知"""

    def test_morning_fixture(self):
        """早上 8:00 EDT fixture → 時段行是「早上」"""
        messages = _build_private(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        )
        content = _system_content(messages)
        self.assertIn("## 當下時間", content)
        self.assertIn("（早上）", content)
        self.assertNotIn("（晚上）", content)

    def test_afternoon_fixture(self):
        """下午 15:00 EDT fixture → 時段行是「下午」"""
        messages = _build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
        )
        content = _system_content(messages)
        self.assertIn("（下午）", content)
        self.assertNotIn("（早上）", content)

    def test_night_fixture(self):
        """晚上 22:00 EDT fixture → 時段行是「晚上」"""
        messages = _build_private(
            NOW_NIGHT,
            current_time=proxy._format_event_timestamp(NIGHT_TS),
            event_ts=NIGHT_TS,
        )
        content = _system_content(messages)
        self.assertIn("（晚上）", content)
        self.assertNotIn("（早上）", content)

    def test_soul_has_no_time_words(self):
        """soul 文本不含時間字樣 → 時段完全來自時間訊號"""
        for word in ("早上", "下午", "晚上", "早安", "晚安", "morning"):
            self.assertNotIn(word, SOUL_NO_TIME)

    def test_same_soul_different_fixtures(self):
        """同一 soul 在不同 fixture 下產生不同時段行 (時間訊號驅動, 非 prompt 硬編碼)"""
        morning = _system_content(_build_private(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        ))
        afternoon = _system_content(_build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
        ))
        self.assertIn("（早上）", morning)
        self.assertIn("（下午）", afternoon)
        self.assertNotEqual(morning, afternoon)


class TestTemporalContinuity(unittest.TestCase):
    """temporal continuity 行為測: conversation_elapsed + last_interaction_period"""

    def test_continuity_injected_3h(self):
        """上次互動 3 小時前 → 注入「距離上次互動已經 3 小時（上次互動在中午）」"""
        # 15:00 EDT 的 3 小時前 = 12:00 EDT = 中午
        messages = _build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=LAST_INTERACTION_3H,
        )
        content = _system_content(messages)
        self.assertIn("距離上次互動已經 3 小時", content)
        self.assertIn("（上次互動在中午）", content)

    def test_continuity_deterministic(self):
        """同一 fixture 連跑兩次 → 結果一致 (deterministic, 不是靠 prompt 硬編碼)"""
        def run():
            return _system_content(_build_private(
                NOW_AFTERNOON,
                current_time=proxy._format_event_timestamp(AFTERNOON_TS),
                event_ts=AFTERNOON_TS,
                last_interaction_ts=LAST_INTERACTION_3H,
            ))
        first = run()
        second = run()
        self.assertEqual(first, second)
        self.assertIn("距離上次互動已經 3 小時", first)

    def test_continuity_same_conversation_skipped(self):
        """elapsed < 15 分鐘 (同一場對話) → 不注入 continuity 行"""
        messages = _build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=LAST_INTERACTION_5MIN,
        )
        content = _system_content(messages)
        self.assertNotIn("距離上次互動", content)

    def test_continuity_never_interacted_skipped(self):
        """從未互動 (last_interaction_ts=0) → 整行省略"""
        messages = _build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=0,
        )
        content = _system_content(messages)
        self.assertNotIn("距離上次互動", content)

    def test_continuity_days(self):
        """elapsed >= 24h → 「X 天」"""
        messages = _build_private(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=LAST_INTERACTION_2D,
        )
        content = _system_content(messages)
        self.assertIn("距離上次互動已經 2 天", content)

    def test_continuity_in_group(self):
        """群聊模式同樣注入 continuity 行"""
        messages = _build_group(
            NOW_AFTERNOON,
            current_time=proxy._format_event_timestamp(AFTERNOON_TS),
            event_ts=AFTERNOON_TS,
            last_interaction_ts=LAST_INTERACTION_3H,
        )
        content = _system_content(messages)
        self.assertIn("距離上次互動已經 3 小時", content)


class TestTemporalExpressionPrecedence(unittest.TestCase):
    """TEMPORAL_EXPRESSION_RULE 放宽 precedence"""

    def test_precedence_injected(self):
        """時間區塊注入 precedence (現象式時間允許)"""
        messages = _build_private(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        )
        content = _system_content(messages)
        self.assertIn("[時間表達規則]", content)
        self.assertIn("現象式時間表達是允許的", content)
        self.assertIn("未經詢問，不要主動報出精確鐘點或日期", content)

    def test_precedence_overrides_persona_ban(self):
        """precedence 明確聲明優先於人格禁令"""
        messages = _build_private(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        )
        content = _system_content(messages)
        self.assertIn("優先於人格設定中任何「不得提及時間」的禁令", content)

    def test_no_meta_instruction(self):
        """不加「請說快中午了」這類 meta-instruction"""
        messages = _build_private(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        )
        content = _system_content(messages)
        self.assertNotIn("請說", content)
        self.assertNotIn("快中午了", content)

    def test_precedence_in_group_too(self):
        """群聊模式同樣注入 precedence"""
        messages = _build_group(
            NOW_MORNING,
            current_time=proxy._format_event_timestamp(MORNING_TS),
            event_ts=MORNING_TS,
        )
        content = _system_content(messages)
        self.assertIn("[時間表達規則]", content)


class TestLastInteractionTs(unittest.TestCase):
    """_get_last_interaction_ts: max(last_user_ts, last_assistant_ts), 跨 session 不跨 agent"""

    def _fake_memory(self, rows):
        m = MagicMock()
        m.conn.execute.return_value.fetchall.return_value = rows
        return m

    def test_max_of_user_and_assistant(self):
        """last_interaction = max(last_user_ts, last_assistant_ts)"""
        rows = [
            ("session_bryan_agent_yua", "user", "bryan", 1000),
            ("session_bryan_agent_yua", "assistant", "agent_yua", 2000),
            ("session_bryan_agent_yua", "assistant", "agent_yua", 3000),
        ]
        self.assertEqual(proxy._get_last_interaction_ts(self._fake_memory(rows), "agent_yua"), 3000)

    def test_user_later_than_assistant(self):
        """last_user_ts 較新時取 user"""
        rows = [
            ("session_bryan_agent_yua", "user", "bryan", 5000),
            ("session_bryan_agent_yua", "assistant", "agent_yua", 2000),
        ]
        self.assertEqual(proxy._get_last_interaction_ts(self._fake_memory(rows), "agent_yua"), 5000)

    def test_cross_session(self):
        """跨 session 取最大 (同 agent 多個 session)"""
        rows = [
            ("session_bryan_agent_yua", "user", "bryan", 1000),
            ("session_tg_agent_yua", "assistant", "agent_yua", 4000),
        ]
        self.assertEqual(proxy._get_last_interaction_ts(self._fake_memory(rows), "agent_yua"), 4000)

    def test_not_cross_agent(self):
        """不跨 agent: 其他 agent 的 session 不計入"""
        rows = [
            ("session_bryan_agent_yua", "user", "bryan", 1000),
            ("session_bryan_agent_akane", "assistant", "agent_akane", 9000),
        ]
        self.assertEqual(proxy._get_last_interaction_ts(self._fake_memory(rows), "agent_yua"), 1000)

    def test_never_interacted(self):
        """從未互動 → 0"""
        self.assertEqual(proxy._get_last_interaction_ts(self._fake_memory([]), "agent_yua"), 0)

    def test_exception_fail_silent(self):
        """查詢失敗 → 0 (fail-silent, 不阻塞 prompt)"""
        m = MagicMock()
        m.conn.execute.side_effect = Exception("db down")
        self.assertEqual(proxy._get_last_interaction_ts(m, "agent_yua"), 0)


class TestFormatContinuityStr(unittest.TestCase):
    """_format_continuity_str 純函式邊界"""

    def test_never_interacted(self):
        self.assertIsNone(proxy._format_continuity_str(0, NOW_AFTERNOON))

    def test_same_conversation(self):
        self.assertIsNone(proxy._format_continuity_str(NOW_AFTERNOON - 5 * 60, NOW_AFTERNOON))

    def test_hours(self):
        s = proxy._format_continuity_str(NOW_AFTERNOON - 3 * 3600, NOW_AFTERNOON)
        self.assertIn("3 小時", s)
        self.assertIn("（上次互動在中午）", s)

    def test_days(self):
        s = proxy._format_continuity_str(NOW_AFTERNOON - 2 * 24 * 3600, NOW_AFTERNOON)
        self.assertIn("2 天", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
