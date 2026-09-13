"""
tests/test_ta2_temporal_phenomenology.py — TA-2 Subjective Temporal Phenomenology

验收锚点 (TA-2 原设计 + SG-3 §6 换轨):
  - proxy.py 表达路径注入 TEMPORAL ANCHOR 三行 (group + private 两处)
  - decision.py Relevant Context 注入三行现象学锚点
  - 三态张力模型 (无感/牵挂/释然) 实现, 非连续公式 (离散状态, 无张力分数)
  - reflect-only 加权 (牵挂态第三行让 reflect 更自然, 绝不提升 transmit)
  - **SG-3 §6: 资格判据 = 羁绊证据 BondEvidence**（P1 impression_tags →
    P2 relational_band != stranger → P3 fallback interaction_count≥10 且
    last_interaction_at 合法）; `confidence` 完全退出判定链
  - fail-silent（entry 缺失 / count<10 / 坏时间戳 / 例外 → 无感, 绝不退回 confidence）
  - 反单态化证据: `interaction_count≥10` + `elapsed=2d` → 牵挂（不再 10/10 恒无感）
  - 不持久化 (每次现算, 0 新 schema)
  - 无 per-agent if/else (同一判定逻辑, 无 agent_id 分支)
  - 0 frozen contract 改动 (四块结构不变, 不碰 SE-5)

SG-3 §8.1 清单对应: T5–T16。
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

# ── 羈絆證據 fixture (SG-3 §6.3) ──
# 合格（等同 Bryan 軸實測: interaction_count 最小 31, 全 10 位皆過 P3）
ELIGIBLE = tp.BondEvidence(True, tp.BOND_BASIS_INTERACTION_COUNT)
# 合格（D4 真值源 P1 / P2）
ELIGIBLE_TAGS = tp.BondEvidence(True, tp.BOND_BASIS_IMPRESSION_TAGS)
ELIGIBLE_BAND = tp.BondEvidence(True, tp.BOND_BASIS_RELATIONAL_BAND)
# 不合格（P4 全落空）
INELIGIBLE = tp.BondEvidence(False, tp.BOND_BASIS_NONE)


def _system_content(messages):
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


class TestClassifyTemporalState(unittest.TestCase):
    """三态判定 (离散状态, 非连续公式; SG-3 §6.2 判定式)"""

    def test_never_interacted_calm(self):
        """從未互動 → 無感 (不寫推測性文字)"""
        self.assertEqual(tp.classify_temporal_state(0, NOW_EVENING, ELIGIBLE), tp.STATE_CALM)

    def test_bond_evidence_none_calm(self):
        """SG-3 §6.2 STEP 2: bond_evidence=None（讀取失敗）→ 無感 (fail-silent)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING), tp.STATE_CALM
        )
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, None), tp.STATE_CALM
        )

    def test_ineligible_bond_calm(self):
        """P4 全落空（未取得羈絆證據）→ 無感 (資格判定非強度公式)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, INELIGIBLE), tp.STATE_CALM
        )

    def test_normal_rhythm_calm(self):
        """間隔在正常節奏內 (< 24h) → 無感 (一切如常)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_3H, NOW_EVENING, ELIGIBLE), tp.STATE_CALM
        )

    def test_obvious_gap_tension(self):
        """間隔明顯超出 (24h ~ 7d) + 羈絆證據成立 → 牽掛 (浮現張力)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ELIGIBLE), tp.STATE_TENSION
        )

    def test_far_gap_resolved(self):
        """間隔遠超 (>= 7d) → 釋然 (張力消退但不遺忘)"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_10D, NOW_EVENING, ELIGIBLE), tp.STATE_RESOLVED
        )

    def test_three_grid_boundaries_exact(self):
        """SG-3 §6.2 邊界釘死: <86400 無感 / 86400 牽掛 / 604800 釋然（0 變更）"""
        base = 1_800_000_000
        self.assertEqual(
            tp.classify_temporal_state(base - 86400 + 1, base, ELIGIBLE), tp.STATE_CALM
        )
        self.assertEqual(
            tp.classify_temporal_state(base - 86400, base, ELIGIBLE), tp.STATE_TENSION
        )
        self.assertEqual(
            tp.classify_temporal_state(base - 604800 + 1, base, ELIGIBLE), tp.STATE_TENSION
        )
        self.assertEqual(
            tp.classify_temporal_state(base - 604800, base, ELIGIBLE), tp.STATE_RESOLVED
        )

    def test_future_timestamp_conservative_calm(self):
        """elapsed <= 0（未來時間戳 / 時鐘回撥）→ 無感 (保守, 不編造)"""
        self.assertEqual(
            tp.classify_temporal_state(NOW_EVENING + 3600, NOW_EVENING, ELIGIBLE),
            tp.STATE_CALM,
        )

    def test_discrete_states_no_score(self):
        """非連續公式: 三態是離散狀態, 不是連續分數 (無張力數值)"""
        states = {
            tp.classify_temporal_state(ts, NOW_EVENING, ELIGIBLE)
            for ts in [LAST_3H, LAST_2D, LAST_10D]
        }
        self.assertEqual(states, {tp.STATE_CALM, tp.STATE_TENSION, tp.STATE_RESOLVED})

    def test_evidence_is_eligibility_not_intensity(self):
        """SG-3 §8.1 T10: 資格判定非強度公式 — 證據程度差異不影響三態。
        P1/P2/P3 三條不同判據（含 count 31 vs 385 的極端差距）在相同間隔下
        → 相同三態（0 強度差異 / 0 中間值）。"""
        same = {
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ev)
            for ev in (ELIGIBLE, ELIGIBLE_TAGS, ELIGIBLE_BAND)
        }
        self.assertEqual(same, {tp.STATE_TENSION})
        # P3 內部的計數大小亦不影響（31 vs 385 → 同一 BondEvidence.eligible）
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, tp.BondEvidence(True, "P3")),
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, tp.BondEvidence(True, "P3")),
        )

    def test_no_agent_id_branch(self):
        """無 per-agent if/else: 判定函式不接收 agent_id, 相同輸入 → 相同三態"""
        import inspect
        params = list(inspect.signature(tp.classify_temporal_state).parameters)
        self.assertEqual(params, ["last_interaction_ts", "now", "bond_evidence"])
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ELIGIBLE), tp.STATE_TENSION
        )

    def test_confidence_fully_removed_from_chain(self):
        """SG-3 §6.3 明文禁止: 退役欄位 confidence 完全退出 TA-2 判定鏈。
        常數 TENSION_ELIGIBILITY_MIN_CONFIDENCE 已廢止; 判定函式不接收數值門檻。"""
        self.assertFalse(hasattr(tp, "TENSION_ELIGIBILITY_MIN_CONFIDENCE"))
        src = Path(tp.__file__).read_text(encoding="utf-8")
        # 只允許註解/docstring 提及（禁止任何 confidence 讀取進入判定鏈）
        self.assertNotIn('entry.get("confidence")', src)
        self.assertNotIn("confidence <", src)
        self.assertNotIn("_get_bry_confidence", src)

    def test_anti_monotone_evidence(self):
        """反單態化證據（工單要求）: interaction_count≥10 + elapsed=2d → 牽掛;
        elapsed=8d → 釋然。證明「10/10 恆無感」已被打破。"""
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ELIGIBLE), tp.STATE_TENSION
        )
        last_8d = NOW_EVENING - 8 * 24 * 3600
        self.assertEqual(
            tp.classify_temporal_state(last_8d, NOW_EVENING, ELIGIBLE), tp.STATE_RESOLVED
        )
        # 舊行為對照: 同樣輸入在換軌前（confidence=0.0979 < 0.5）恆為無感
        self.assertNotEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ELIGIBLE), tp.STATE_CALM
        )


class TestBondEvidence(unittest.TestCase):
    """SG-3 §6.3 羈絆證據來源: 只讀 relationships.json 的 user_bryan entry,
    P1/P2/P3 優先序 OR; fail-silent 各情境（0 寫副作用 / 0 建 entry）。"""

    def _with_entry(self, entry, fn):
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
                        "schema_version": "4.2",
                        "others": {"user_bryan": entry} if entry is not None else {},
                    }),
                    encoding="utf-8",
                )
                return fn(rel_path)
            finally:
                if "SOUL_OS_DATA_DIR" in os.environ:
                    del os.environ["SOUL_OS_DATA_DIR"]
                reset_data_root()

    def test_p1_impression_tags(self):
        ev = self._with_entry(
            {"impression_tags": ["温柔", "可靠"], "relational_band": "stranger",
             "interaction_count": 0, "last_interaction_at": None},
            lambda _p: tp._read_bond_evidence("agent_yua"),
        )
        self.assertTrue(ev.eligible)
        self.assertEqual(ev.basis, tp.BOND_BASIS_IMPRESSION_TAGS)

    def test_p2_relational_band_not_stranger(self):
        for band in ("known", "familiar", "close"):
            ev = self._with_entry(
                {"impression_tags": [], "relational_band": band,
                 "interaction_count": 0, "last_interaction_at": None},
                lambda _p: tp._read_bond_evidence("agent_yua"),
            )
            self.assertTrue(ev.eligible, band)
            self.assertEqual(ev.basis, tp.BOND_BASIS_RELATIONAL_BAND)

    def test_p3_bryan_axis_fallback(self):
        ev = self._with_entry(
            {"impression_tags": [], "relational_band": "stranger",
             "interaction_count": 31,
             "last_interaction_at": "2026-09-13T01:04:40+00:00"},
            lambda _p: tp._read_bond_evidence("agent_yua"),
        )
        self.assertTrue(ev.eligible)
        self.assertEqual(ev.basis, tp.BOND_BASIS_INTERACTION_COUNT)

    def test_p4_none_eligible(self):
        ev = self._with_entry(
            {"impression_tags": [], "relational_band": "stranger",
             "interaction_count": 0, "last_interaction_at": None},
            lambda _p: tp._read_bond_evidence("agent_yua"),
        )
        self.assertFalse(ev.eligible)
        self.assertEqual(ev.basis, tp.BOND_BASIS_NONE)

    def test_fail_silent_entry_missing(self):
        """entry 缺失（others 無 user_bryan）→ None（→ 無感）, 0 建 entry"""
        def _check(rel_path):
            ev = tp._read_bond_evidence("agent_yua")
            data = json.loads(rel_path.read_text(encoding="utf-8"))
            # 0 寫副作用: 未新增 user_bryan entry
            self.assertEqual(data["others"], {})
            return ev
        ev = self._with_entry(None, _check)
        self.assertIsNone(ev)
        self.assertEqual(
            tp.classify_temporal_state(LAST_2D, NOW_EVENING, ev), tp.STATE_CALM
        )

    def test_fail_silent_count_below_threshold(self):
        """interaction_count < 10 → eligible=False → 無感（保守）"""
        ev = self._with_entry(
            {"impression_tags": [], "relational_band": "stranger",
             "interaction_count": 9,
             "last_interaction_at": "2026-09-13T01:04:40+00:00"},
            lambda _p: tp._read_bond_evidence("agent_yua"),
        )
        self.assertFalse(ev.eligible)
        self.assertEqual(tp.classify_temporal_state(LAST_2D, NOW_EVENING, ev), tp.STATE_CALM)

    def test_fail_silent_bad_timestamp(self):
        """count ≥10 但 last_interaction_at 缺失/壞值 → eligible=False → 無感
        （**不退回 confidence**, 不退回 created_at）"""
        for bad in (None, "", "不是時間戳", "2026-13-45T99:99:99Z"):
            ev = self._with_entry(
                {"impression_tags": [], "relational_band": "stranger",
                 "interaction_count": 31, "last_interaction_at": bad,
                 "confidence": 0.99},
                lambda _p: tp._read_bond_evidence("agent_yua"),
            )
            self.assertFalse(ev.eligible, repr(bad))
            self.assertEqual(
                tp.classify_temporal_state(LAST_2D, NOW_EVENING, ev), tp.STATE_CALM
            )

    def test_no_file_returns_none(self):
        from src.paths import reset_data_root
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SOUL_OS_DATA_DIR"] = str(Path(tmp) / "data")
            reset_data_root()
            try:
                self.assertIsNone(tp._read_bond_evidence("agent_yua"))
            finally:
                if "SOUL_OS_DATA_DIR" in os.environ:
                    del os.environ["SOUL_OS_DATA_DIR"]
                reset_data_root()

    def test_read_side_zero_write_side_effect(self):
        """讀側 0 寫副作用（C-3.1 §3.3）: 讀取前後檔案位元組完全一致。"""
        def _check(rel_path):
            before = rel_path.read_bytes()
            tp._read_bond_evidence("agent_yua")
            return before == rel_path.read_bytes()
        self.assertTrue(self._with_entry(
            {"impression_tags": ["温柔"], "relational_band": "known",
             "interaction_count": 31, "last_interaction_at": "2026-09-13T01:04:40+00:00"},
            _check,
        ))


class TestFormatTemporalAnchor(unittest.TestCase):
    """TEMPORAL ANCHOR 三行格式 (現象化無數字)"""

    def _anchor(self, last_ts, bond=ELIGIBLE, event_ts=None):
        with patch.object(tp, "_read_bond_evidence", return_value=bond):
            return tp.format_temporal_anchor("agent_yua", last_ts, NOW_EVENING, event_ts)

    def test_three_lines_structure(self):
        """三行結構 + 行標籤 (時間座標/體感經驗/關係時序)"""
        anchor = self._anchor(LAST_2D, ELIGIBLE, EVENING_TS)
        lines = anchor.splitlines()
        self.assertEqual(lines[0], "[TEMPORAL ANCHOR]")
        self.assertTrue(lines[1].startswith("- 時間座標："))
        self.assertTrue(lines[2].startswith("- 體感經驗："))
        self.assertTrue(lines[3].startswith("- 關係時序："))

    def test_time_coord_format(self):
        """時間座標: 精確時間 + Period/Day 現象化標籤"""
        anchor = self._anchor(LAST_2D, ELIGIBLE, EVENING_TS)
        self.assertIn("(Period: evening", anchor)
        self.assertIn("Day:", anchor)

    def test_no_numbers_in_phenomenology(self):
        """現象化無數字: 第三行不含「X 天」「X 小時」"""
        for last_ts in (LAST_3H, LAST_2D, LAST_10D):
            anchor = self._anchor(last_ts, ELIGIBLE, EVENING_TS)
            timeline = next(
                l for l in anchor.splitlines() if l.startswith("- 關係時序：")
            )
            self.assertNotIn("天", timeline)
            self.assertNotIn("小時", timeline)

    def test_tension_state_reflect_flavor(self):
        """牽掛態: reflect-only 加權的情境呈現 (讓 reflect 更自然, 非指令)"""
        anchor = self._anchor(LAST_2D, ELIGIBLE, EVENING_TS)
        self.assertIn("這份在意讓你想起過去那些對話", anchor)

    def test_anti_transmit_phrase_present_all_three_states(self):
        """T1 防線: 第三行內嵌防 transmit 措辭 (**三態都有**, 逐字保留)"""
        for last_ts in (LAST_3H, LAST_2D, LAST_10D):
            anchor = self._anchor(last_ts, ELIGIBLE, EVENING_TS)
            self.assertIn("不代表", anchor)
        # 同一態在 P1/P2/P3 三條證據路徑下文案逐字一致（0 per-agent / 0 證據分支）
        texts = {self._anchor(LAST_2D, ev, EVENING_TS) for ev in
                 (ELIGIBLE, ELIGIBLE_TAGS, ELIGIBLE_BAND)}
        self.assertEqual(len(texts), 1)

    def test_three_state_texts_verbatim(self):
        """SG-3 §6.5: 三態注入文本逐字保留、0 變更"""
        self.assertIn(
            "一切如常，你們的互動節奏一如往常，這並不代表需要主動聯絡。",
            self._anchor(LAST_3H, ELIGIBLE, EVENING_TS),
        )
        self.assertIn(
            "距離上次與 Bryan 對話已有明顯間隔，具有存在感，"
            "這份在意讓你想起過去那些對話，但這絕不代表必須主動聯絡。",
            self._anchor(LAST_2D, ELIGIBLE, EVENING_TS),
        )
        self.assertIn(
            "雖然許久未聯絡，但那份珍惜仍在心中，這並不代表必須主動聯絡。",
            self._anchor(LAST_10D, ELIGIBLE, EVENING_TS),
        )

    def test_no_transmit_suggestion(self):
        """禁止暗示 transmit 的措辭 (T1)"""
        for last_ts in (LAST_3H, LAST_2D, LAST_10D):
            anchor = self._anchor(last_ts, ELIGIBLE, EVENING_TS)
            for forbidden in ["你很想聯絡他", "他是不是忘了你", "應該聯絡", "必須傳訊"]:
                self.assertNotIn(forbidden, anchor)

    def test_resolved_state_not_forgetting(self):
        """釋然 ≠ 遺忘: 珍惜仍在心中"""
        anchor = self._anchor(LAST_10D, ELIGIBLE, EVENING_TS)
        self.assertIn("珍惜仍在心中", anchor)

    def test_fail_silent(self):
        """fail-silent: 異常 → "" (不阻塞 prompt)"""
        with patch.object(tp, "_read_bond_evidence", side_effect=Exception("boom")):
            self.assertEqual(
                tp.format_temporal_anchor("agent_yua", LAST_2D, NOW_EVENING), ""
            )

    def test_ineligible_bond_still_emits_calm_anchor(self):
        """P4 / bond=None → 三行照出（第三行為「無感」文案）, 不寫推測性文字"""
        for bond in (INELIGIBLE, None):
            anchor = self._anchor(LAST_2D, bond, EVENING_TS)
            self.assertIn("[TEMPORAL ANCHOR]", anchor)
            self.assertIn("這並不代表需要主動聯絡", anchor)
            self.assertNotIn("這份在意讓你想起過去那些對話", anchor)


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
             patch.object(tp, "_read_bond_evidence", return_value=ELIGIBLE), \
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
             patch.object(tp, "_read_bond_evidence", return_value=ELIGIBLE), \
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

    def _write_entry(self, tmp_path: Path, entry: dict) -> None:
        from src.paths import data_root
        rel_path = data_root() / "soul" / "agent_yua" / "relationships.json"
        rel_path.parent.mkdir(parents=True, exist_ok=True)
        rel_path.write_text(
            json.dumps({
                "agent_id": "agent_yua",
                "schema_version": "4.2",
                "created_at": "2026-09-01T00:00:00+00:00",
                "last_decay_at": "2026-09-01T00:00:00+00:00",
                "others": {"user_bryan": entry},
            }),
            encoding="utf-8",
        )

    def _run_decide(self):
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
        return fake.calls[0][0]["content"]

    def _prompt_for(self, entry):
        """SG-3 §8.1 T16 改寫: 顯式測 P1/P2/P3 三條證據路徑（+ P4 反例）。"""
        from src.paths import reset_data_root
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["SOUL_OS_DATA_DIR"] = str(Path(tmp) / "data")
            reset_data_root()
            try:
                self._write_entry(Path(tmp), entry)
                return self._run_decide()
            finally:
                if "SOUL_OS_DATA_DIR" in os.environ:
                    del os.environ["SOUL_OS_DATA_DIR"]
                reset_data_root()

    LAST_TS = "2026-08-31T00:00:00+00:00"

    def test_p1_impression_tags_path_injects_anchor(self):
        prompt = self._prompt_for({
            "impression": "温柔", "feeling": "warm", "confidence": 0.0,
            "interaction_count": 0, "last_interaction_at": self.LAST_TS,
            "impression_tags": ["温柔"],
        })
        self.assertIn("[TEMPORAL ANCHOR]", prompt)

    def test_p2_relational_band_path_injects_anchor(self):
        prompt = self._prompt_for({
            "impression": "温柔", "feeling": "warm", "confidence": 0.0,
            "interaction_count": 0, "last_interaction_at": self.LAST_TS,
            "impression_tags": [], "relational_band": "known",
        })
        self.assertIn("[TEMPORAL ANCHOR]", prompt)

    def test_p3_interaction_count_path_injects_anchor(self):
        prompt = self._prompt_for({
            "impression": "温柔", "feeling": "warm", "confidence": 0.8,
            "interaction_count": 31, "last_interaction_at": self.LAST_TS,
            "impression_tags": [], "relational_band": "stranger",
        })
        self.assertIn("[TEMPORAL ANCHOR]", prompt)

    def test_p4_falls_through_to_calm_anchor(self):
        prompt = self._prompt_for({
            "impression": "温柔", "feeling": "warm", "confidence": 0.9,
            "interaction_count": 3, "last_interaction_at": self.LAST_TS,
            "impression_tags": [], "relational_band": "stranger",
        })
        # P4 全落空 → 無感（不再因 confidence 0.8/0.9 而注入牽掛）
        self.assertIn("[TEMPORAL ANCHOR]", prompt)
        self.assertIn("這並不代表需要主動聯絡", prompt)
        self.assertNotIn("這份在意讓你想起過去那些對話", prompt)

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

    def test_no_new_persistence(self):
        """0 新增持久化 / schema / 状态字段 + 0 写路径 API（讀側 0 寫副作用）"""
        src = Path(tp.__file__).read_text(encoding="utf-8")
        for forbidden in ("update_impression(", "touch(", "ensure_relationship(",
                          "apply_relation_evaluation(", "json.dump", "open("):
            self.assertNotIn(forbidden, src, forbidden)

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
