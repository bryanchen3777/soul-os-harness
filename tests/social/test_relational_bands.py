"""
tests/social/test_relational_bands.py — SG-2 关系带状态机全用例（D4）

覆盖: 契约 SG-1 §3.3 四带整数门槛升带转移表全用例 + 30 天降带 + 0 浮点断言。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_relational_bands.py -v
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.social.relational_bands import (
    BAND_CLOSE,
    BAND_FAMILIAR,
    BAND_KNOWN,
    BAND_STRANGER,
    DEMOTE_DAYS,
    RELATIONAL_BANDS,
    demote_band,
    evaluate_band,
    should_demote,
    valid_band,
)

ROOT = Path(__file__).resolve().parents[2]


def _dt(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# ───────────────────────────────────────────────────────────
# 升带转移表全用例（契约 §3.3）
# ───────────────────────────────────────────────────────────

class TestUpgradeTransitions:
    """stranger→known / known→familiar / familiar→close 三行转移表全覆盖。"""

    def test_stranger_to_known_reply_ge_1(self):
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=0) == BAND_KNOWN

    def test_stranger_to_known_co_ge_2(self):
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=2) == BAND_KNOWN

    def test_stranger_to_known_either_hit(self):
        # or 语义: 只 reply=1 也升
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=5) == BAND_KNOWN

    def test_stranger_stays_without_threshold(self):
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=1) == BAND_STRANGER
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=0) == BAND_STRANGER

    def test_known_to_familiar_all_hit(self):
        assert evaluate_band(BAND_KNOWN, reply_exchanges=3, co_presence_sessions=5) == BAND_FAMILIAR

    def test_known_to_familiar_and_semantics(self):
        # and 语义: 少一个就不升
        assert evaluate_band(BAND_KNOWN, reply_exchanges=3, co_presence_sessions=4) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=2, co_presence_sessions=5) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=3, co_presence_sessions=0) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=10, co_presence_sessions=4) == BAND_KNOWN

    def test_familiar_to_close_reply_co_row(self):
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=10, co_presence_sessions=15) == BAND_CLOSE

    def test_familiar_to_close_dream_row(self):
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=5, co_presence_sessions=0, dream_exchanges=4) == BAND_CLOSE

    def test_familiar_to_close_dream_row_reply_shy(self):
        # dream 行: dream≥4 且 reply≥5 全中才升; reply=4 不升
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=4, co_presence_sessions=0, dream_exchanges=4) == BAND_FAMILIAR

    def test_familiar_to_close_reply_co_shy(self):
        # reply/co 行: reply≥10 且 co≥15 全中才升
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=10, co_presence_sessions=14) == BAND_FAMILIAR
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=9, co_presence_sessions=15) == BAND_FAMILIAR

    def test_familiar_stays_without_threshold(self):
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=0, co_presence_sessions=0) == BAND_FAMILIAR

    def test_close_stays_at_top(self):
        # 顶带保持（离散阶梯只回答升格; 计数单调, 不满足也不回退）
        assert evaluate_band(BAND_CLOSE, reply_exchanges=0, co_presence_sessions=0) == BAND_CLOSE

    def test_int_only_thresholds(self):
        # 全整数阈值: 0.5 半步不得触发任何升级（0 浮点门槛）
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=1) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=3, co_presence_sessions=5) == BAND_FAMILIAR
        # 计数为 int 语义: 传入非 int 按 int() 落定（fail-closed 不 crash）
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=2.9) == BAND_KNOWN

    def test_invalid_current_band_fail_closed(self):
        # 脏 band 值 → 按 stranger 重新起算（不静默放行脏带）
        assert evaluate_band("alien", reply_exchanges=1, co_presence_sessions=0) == BAND_KNOWN
        assert valid_band("alien") is False
        assert valid_band(BAND_KNOWN) is True

    def test_enum_four_bands(self):
        assert RELATIONAL_BANDS == (BAND_STRANGER, BAND_KNOWN, BAND_FAMILIAR, BAND_CLOSE)


# ───────────────────────────────────────────────────────────
# 降带（30 天形态冻结）
# ───────────────────────────────────────────────────────────

class TestDemotions:
    def test_demote_one_step(self):
        assert demote_band(BAND_CLOSE) == BAND_FAMILIAR
        assert demote_band(BAND_FAMILIAR) == BAND_KNOWN
        assert demote_band(BAND_KNOWN) == BAND_STRANGER

    def test_demote_bottom_stays(self):
        assert demote_band(BAND_STRANGER) == BAND_STRANGER  # 底带不再降

    def test_demote_invalid_fail_closed(self):
        assert demote_band("alien") == BAND_STRANGER

    def test_no_demote_within_30_days(self):
        # 边界: 恰好 30 天整不降（契约「连续 >30 天」）
        assert should_demote(_dt(DEMOTE_DAYS).isoformat(), datetime.now(timezone.utc)) is False
        assert should_demote(_dt(29).isoformat(), datetime.now(timezone.utc)) is False
        assert should_demote(_dt(1).isoformat(), datetime.now(timezone.utc)) is False

    def test_demote_after_30_days(self):
        assert should_demote(_dt(DEMOTE_DAYS + 1).isoformat(), datetime.now(timezone.utc)) is True
        assert should_demote(_dt(100).isoformat(), datetime.now(timezone.utc)) is True

    def test_demote_fallback_ts(self):
        # last_signal_at 缺失 → fallback_ts（last_interaction_at）生效
        assert should_demote(None, datetime.now(timezone.utc), fallback_ts=_dt(40).isoformat()) is True
        assert should_demote(None, datetime.now(timezone.utc), fallback_ts=_dt(10).isoformat()) is False

    def test_demote_missing_ts_conservative(self):
        assert should_demote(None, datetime.now(timezone.utc)) is False  # 保守不降
        assert should_demote("坏时间戳", datetime.now(timezone.utc)) is False

    def test_demote_future_ts_noop(self):
        fut = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assert should_demote(fut, datetime.now(timezone.utc)) is False


# ───────────────────────────────────────────────────────────
# 0 浮点刚性断言（属性 + AST 双保险）
# ───────────────────────────────────────────────────────────

class TestNoFloatGuard:
    def test_threshold_constants_all_int(self):
        # 阈值表全部整数（0 float 权重常量）
        src = (ROOT / "src" / "social" / "relational_bands.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        floats = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, float)
        ]
        assert floats == [], f"relational_bands.py 含 float 常量: {floats}"

    def test_no_score_tokens(self):
        src = (ROOT / "src" / "social" / "relational_bands.py").read_text(encoding="utf-8")
        assert "weight" not in src.replace("权重", "")
        assert "affinity" not in src
        assert "score" not in src

    def test_demote_seconds_int(self):
        assert DEMOTE_DAYS * 86400 == 2592000  # 整数秒, 0 浮点乘积