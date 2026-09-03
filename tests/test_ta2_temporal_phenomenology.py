"""
tests/test_ta2_temporal_phenomenology.py — TA-2 Subjective Temporal Phenomenology

验收锚点 (工单 TA-2, IMPLEMENTATION):
  - proxy.py 表达路径注入 TEMPORAL ANCHOR 三行 (group + private 两处)
  - decision.py Relevant Context 注入三行现象学锚点
  - 三态张力模型 (无感/牵挂/释然) 实现, 非连续公式 (离散状态, 无张力分数)
  - reflect-only 加权 (牵挂态第三行让 reflect 更自然, 绝不提升 transmit)
  - M5.13-3 亲密度 Band 复用 (牵挂资格判定 = 熟悉 >= 0.5, 资格判定非强度公式)
  - 不持久化 (每次现算, 0 新 schema)
  - 无 per-agent if/else (同一判定逻辑, 无 agent_id 分支)
  - 0 frozen contract 改动 (四块结构不变, 不碰 SE-5)
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul import temporal_phenomenology as tp
from src.llm import proxy

# ── 固定時鐘 fixture (EDT 夏季 = UTC-4) ──
# 傍晚 18:00 EDT = 22:00 UTC (2026-09-02 週三)
EVENING_TS = datetime(2026, 9, 2, 22, 0, 0, tzinfo=timezone.utc)
NOW_EVENING = int(EVENING_TS.timestamp())

# 上次互動: 3 小時前 (正常節奏內 → 無感)
LAST_3H = NOW_EVENING - 3 * 3600
# 上次互動: 2 天前 (明顯超出 → 牽掛)
LAST_2D = NOW_EVENING - 2 * 24 * 3600
# 上次互動: 10 天前 (遠超 → 釋然)
LAST_10D = NOW_EVENING - 10 * 24 * 3600

SOUL_NO_TIME = "你是測試角色。你是一個溫柔的人。說話簡短。"


def _system_content(messages):
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


class TestClassifyTemporalState(unittest.TestCase):
    """三态判定 (离散状态, 非连续公式)"""

    def test_never_interacted_calm(self):
        """從未互動 → 無感 (不寫推測性文字)"""
        self.assertEqual(tp.classify_temporal_state(0, NOW_EVENING), tp.STATE_CALM)

    def test_below_eligibility_calm(self):
        """認識 Band (0.4) < 熟悉 (0.5) → 無感 (陌生人沉默不構成張力)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, 0.4), tp.STATE_CALM
        )

    def test_normal_rhythm_calm(self):
        """間隔在正常節奏內 (< 24h) → 無感 (一切如常)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_3H, NOW_EVENING, 0.8), tp.STATE_CALM
        )

    def test_obvious_gap_tension(self):
        """間隔明顯超出 (24h ~ 7d) + 親密度夠 → 牽掛 (浮現張力)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, 0.8), tp.STATE_TENSION
        )

    def test_far_gap_resolved(self):
        """間隔遠超 (>= 7d) → 釋然 (張力消退但不遺忘)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_10D, NOW_EVENING, 0.8), tp.STATE_RESOLVED
        )

    def test_discrete_states_no_score(self):
        """非連續公式: 三態是離散狀態, 不是連續分數 (無張力數值)"""
        states = {
            tp.classify_temporal_state(ts, NOW_EVENING, 0.8)
            for ts in [LAST_3H, LAST_2D, LAST_10D]
        }
        self.assertEqual(states, {tp.STATE_CALM, tp.STATE_TENSION, tp.STATE_RESOLVED})

    def test_band_is_eligibility_not_intensity(self):
        """資格判定非強度公式: Band 只回答「夠不夠格牽掛」, 不參與強度計算。
        0.5 (熟悉) 與 0.9 (深度信任) 在相同間隔下 → 相同三態 (無強度差異)。"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, 0.5),
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, 0.9),
        )

    def test_no_agent_id_branch(self):
        """無 per-agent if/else: 判定函式不接收 agent_id, 相同輸入 → 相同三態"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, 0.8), tp.STATE_TENSION
        )


class TestFormatTemporalAnchor(unittest.TestCase):
    """TEMPORAL ANCHOR 三行格式 (現象化無數字)"""

    def _anchor(self, last_ts, confidence=None, event_ts=None):
        with patch.object(tp, "_get_bry_confidence", return_value=confidence):
            return tp.format_temporal_anchor("agent_yua", last_ts, NOW_EVENING, event_ts)

    def test_three_lines_structure(self):
        """三行結構 + 行標籤 (時間座標/體感經驗/關係時序)"""
        anchor = self._anchor(LAST_2D, 0.8, EVENING_TS)
        lines = anchor.splitlines()
        self.assertEqual(lines[0], "[TEMPORAL ANCHOR]")
        self.assertTrue(lines[1].startswith("- 時間座標："))
        self.assertTrue(lines[2].startswith("- 體感經驗："))
        self.assertTrue(lines[3].startswith("- 關係時序："))

    def test_time_coord_format(self):
        """時間座標: 精確時間 + Period/Day 現象化標籤"""
        anchor = self._anchor(LAST_2D, 0.8, EVENING_TS)
        self.assertIn("(Period: evening", anchor)
        self.assertIn("Day:", anchor)

    def test_no_numbers_in_phenomenology(self):
        """現象化無數字: 第三行不含「X 天」「X 小時」"""
        for last_ts, conf in [(LAST_3H, 0.8), (LAST_2D, 0.8), (LAST_10D, 0.8)]:
            anchor = self._anchor(last_ts, conf, EVENING_TS)
            timeline = next(
                l for l in anchor.splitlines() if l.startswith("- 關係時序：")
            )
            self.assertNotIn("天", timeline)
            self.assertNotIn("小時", timeline)

    def test_tension_state_reflect_flavor(self):
        """牽掛態: reflect-only 加權的情境呈現 (讓 reflect 更自然, 非指令)"""
        anchor = self._anchor(LAST_2D, 0.8, EVENING_TS)
        self.assertIn("這份在意讓你想起過去那些對話", anchor)

    def test_anti_transmit_phrase_present(self):
        """T1 防線: 第三行內嵌防 transmit 措辭 (三態都有)"""
        for last_ts, conf in [(LAST_3H, 0.8), (LAST_2D, 0.8), (LAST_10D, 0.8)]:
            anchor = self._anchor(last_ts, conf, EVENING_TS)
            self.assertIn("不代表", anchor)

    def test_no_transmit_suggestion(self):
        """禁止暗示 transmit 的措辭 (T1)"""
        for last_ts, conf in [(LAST_3H, 0.8), (LAST_2D, 0.8), (LAST_10D, 0.8)]:
            anchor = self._anchor(last_ts, conf, EVENING_TS)
            for forbidden in ["你很想聯絡他", "他是不是忘了你", "應該聯絡", "必須傳訊"]:
                self.assertNotIn(forbidden, anchor)

    def test_resolved_state_not_forgetting(self):
        """釋然 ≠ 遺忘: 珍惜仍在心中"""
        anchor = self._anchor(LAST_10D, 0.8, EVENING_TS)
        self.assertIn("珍惜仍在心中", anchor)

    def test_fail_silent(self):
        """fail-silent: 異常 → "" (不阻塞 prompt)"""
        with patch.object(tp, "_get_bry_confidence", side_effect=Exception("boom")):
            self.assertEqual(
                tp.format_temporal_anchor("agent_yua", LAST_2D, NOW_EVENING), ""
            )


class TestProxyInjection(unittest.TestCase):
    """proxy.py 表达路径注入 TEMPORAL ANCHOR (group + private)"""

    def _build_private(self, now, **overrides):
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
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "_load_self_recent", return_value=[]), \
             patch.object(proxy, "_format_recent_inner_life", return_value=""), \
             patch.object(proxy, "_format_relationship_block", return_value=""), \
             patch.object(proxy, "_format_capability_block", return_value=""), \
             patch.object(proxy, "_format_emergent_block", return_value=""), \
             patch.object(proxy, "_format_attachment_str", return_value=""), \
             patch.object(tp, "_get_bry_confidence", return_value=0.8), \
             patch("time.time", return_value=now):
            return proxy._build_messages_private(**args)

    def _build_group(self, now, **overrides):
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
             patch.object(tp, "_get_bry_confidence", return_value=0.8), \
             patch("time.time", return_value=now):
            return proxy._build_messages_group(**args)

    def test_private_injects_anchor(self):
        """私聊路徑注入 TEMPORAL ANCHOR 三行"""
        messages = self._build_private(
            NOW_EVENING,
            current_time=proxy._format_event_timestamp(EVENING_TS),
            event_ts=EVENING_TS,
            last_interaction_ts=LAST_2D,
        )
        content = _system_content(messages)
        self.assertIn("[TEMPORAL ANCHOR]", content)
        self.assertIn("- 時間座標：", content)
        self.assertIn("- 體感經驗：", content)
        self.assertIn("- 關係時序：", content)

    def test_group_injects_anchor(self):
        """群聊路徑注入 TEMPORAL ANCHOR"""
        messages = self._build_group(
            NOW_EVENING,
            current_time=proxy._format_event_timestamp(EVENING_TS),
            event_ts=EVENING_TS,
            last_interaction_ts=LAST_2D,
        )
        content = _system_content(messages)
        self.assertIn("[TEMPORAL ANCHOR]", content)

    def test_anchor_inside_temporal_block(self):
        """注入位置: 时间区块内 (TA-1 同区)"""
        messages = self._build_private(
            NOW_EVENING,
            current_time=proxy._format_event_timestamp(EVENING_TS),
            event_ts=EVENING_TS,
            last_interaction_ts=LAST_2D,
        )
        content = _system_content(messages)
        temporal_idx = content.find("## 當下時間")
        anchor_idx = content.find("[TEMPORAL ANCHOR]")
        self.assertGreater(temporal_idx, -1)
        self.assertGreater(anchor_idx, temporal_idx)

    def test_anchor_absent_when_no_current_time(self):
        """current_time 为空 → 时间区块不注入 → anchor 也不注入"""
        messages = self._build_private(NOW_EVENING, last_interaction_ts=LAST_2D)
        content = _system_content(messages)
        self.assertNotIn("[TEMPORAL ANCHOR]", content)


class TestDecisionInjection(unittest.TestCase):
    """decision.py Relevant Context 注入 TEMPORAL ANCHOR"""

    def _motive(self):
        from src.soul.motive import Motive, now_utc_iso
        return Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )

    def test_build_prompt_injects_anchor_in_context(self):
        """build_decision_prompt 传 temporal_anchor → Relevant context 注入"""
        from src.soul.decision import build_decision_prompt
        anchor = (
            "[TEMPORAL ANCHOR]\n"
            "- 時間座標：2026-09-02 18:00 (Period: evening, Day: Wednesday)\n"
            "- 體感經驗：傍晚時分，這一天正在緩慢安靜地收尾。\n"
            "- 關係時序：距離上次與 Bryan 對話已有明顯間隔，具有存在感，"
            "但這絕不代表必須主動聯絡。"
        )
        prompt = build_decision_prompt(
            self._motive(), provenance_desc="diary:night @ 2026-09-02",
            temporal_anchor=anchor,
        )
        self.assertIn("[TEMPORAL ANCHOR]", prompt)
        # 只进 Relevant context, 不进 Framing/Boundary (四块结构不变)
        self.assertIn("你心里有一个念头，已经成形", prompt)
        self.assertIn("现在有四个选择，只能选一个", prompt)
        self.assertIn('{"decision": "transmit" | "observe" | "reflect" | "do_nothing"', prompt)

    def test_build_prompt_default_none_no_injection(self):
        """默认 temporal_anchor=None → 不注入 (向后兼容)"""
        from src.soul.decision import build_decision_prompt
        prompt = build_decision_prompt(
            self._motive(), provenance_desc="diary:night @ 2026-09-02"
        )
        self.assertNotIn("TEMPORAL ANCHOR", prompt)

    def test_decide_motive_generates_anchor(self):
        """decide_motive 默认从 relationships.json 现算 TEMPORAL ANCHOR"""
        from src.paths import data_root, reset_data_root
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SOUL_OS_DATA_DIR"] = str(Path(tmp) / "data")
            reset_data_root()
            try:
                rel_path = data_root() / "soul" / "agent_yua" / "relationships.json"
                rel_path.parent.mkdir(parents=True, exist_ok=True)
                rel_path.write_text(
                    json.dumps({
                        "agent_id": "agent_yua",
                        "schema_version": "4.1",
                        "created_at": "2026-09-01T00:00:00+00:00",
                        "last_decay_at": "2026-09-01T00:00:00+00:00",
                        "others": {
                            "user_bryan": {
                                "impression": "温柔",
                                "feeling": "warm",
                                "confidence": 0.8,
                                "interaction_count": 10,
                                "last_interaction_at": "2026-08-31T00:00:00+00:00",
                                "last_updated": "2026-08-31T00:00:00+00:00",
                                "created_at": "2026-08-01T00:00:00+00:00",
                            }
                        },
                    }),
                    encoding="utf-8",
                )
                from src.soul.decision import decide_motive

                class FakeProxy:
                    def __init__(self):
                        self.calls = []

                    async def generate_text(
                        self, messages, agent_id="system",
                        max_tokens=200, temperature=0.7,
                    ):
                        self.calls.append(messages)
                        return '{"decision": "do_nothing", "reason": "安静"}'

                fake = FakeProxy()
                result = asyncio.run(
                    decide_motive(self._motive(), "agent_yua", llm_call=fake.generate_text)
                )
                self.assertEqual(result.decision, "do_nothing")
                prompt = fake.calls[0][0]["content"]
                self.assertIn("[TEMPORAL ANCHOR]", prompt)
            finally:
                if "SOUL_OS_DATA_DIR" in os.environ:
                    del os.environ["SOUL_OS_DATA_DIR"]
                reset_data_root()

    def test_decide_motive_no_relationship_no_anchor(self):
        """relationships.json 无 entry → 不注入 (fail-silent, 不编造)"""
        from src.paths import data_root, reset_data_root
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SOUL_OS_DATA_DIR"] = str(Path(tmp) / "data")
            reset_data_root()
            try:
                from src.soul.decision import decide_motive

                class FakeProxy:
                    def __init__(self):
                        self.calls = []

                    async def generate_text(
                        self, messages, agent_id="system",
                        max_tokens=200, temperature=0.7,
                    ):
                        self.calls.append(messages)
                        return '{"decision": "do_nothing", "reason": "安静"}'

                fake = FakeProxy()
                asyncio.run(
                    decide_motive(self._motive(), "agent_yua", llm_call=fake.generate_text)
                )
                prompt = fake.calls[0][0]["content"]
                self.assertNotIn("TEMPORAL ANCHOR", prompt)
            finally:
                if "SOUL_OS_DATA_DIR" in os.environ:
                    del os.environ["SOUL_OS_DATA_DIR"]
                reset_data_root()


class TestFrozenContract(unittest.TestCase):
    """0 frozen contract 改动 + TA-2 与 SE-5 解耦"""

    def test_no_soul_elevation_import(self):
        """TA-2 与 SE-5 解耦: temporal_phenomenology 不 import soul_elevation"""
        import inspect
        source = inspect.getsource(tp)
        # 实际 import 检查 (docstring 提及「不碰」不算碰)
        self.assertNotIn("import soul_elevation", source)
        self.assertNotIn("from soul_elevation", source)
        # 不写 soul-elevation 状态字段 (赋值/读取路径, 非 docstring 提及)
        self.assertNotIn("lifecycle_state =", source)
        self.assertNotIn("last_support_ts =", source)
        self.assertNotIn("contradiction_pressure =", source)

    def test_decision_prompt_four_blocks_unchanged(self):
        """build_decision_prompt 四块结构不变 (Framing/Motive/Context/Boundary)"""
        from src.soul.decision import build_decision_prompt
        prompt = build_decision_prompt(
            self._motive(), provenance_desc="diary:night @ 2026-09-02",
            temporal_anchor="[TEMPORAL ANCHOR]\n- 時間座標：x\n- 體感經驗：y\n- 關係時序：z",
        )
        # Framing
        self.assertIn("你心里有一个念头，已经成形", prompt)
        # Motive
        self.assertIn("你想告诉 bryan：我想告诉你今天的事", prompt)
        # Boundary (四元, 互斥单选)
        self.assertIn("现在有四个选择，只能选一个", prompt)
        self.assertIn("transmit — 现在把念头化为讯息，传给 Bry", prompt)
        self.assertIn("observe — 现在不传，先观察环境", prompt)
        self.assertIn("reflect — 现在不传，先回顾记忆", prompt)
        self.assertIn("do_nothing — 现在不传，安静度日", prompt)

    def _motive(self):
        from src.soul.motive import Motive, now_utc_iso
        return Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
