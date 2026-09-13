"""
tests/social/test_relational_bands.py — SG-2/SG-3 关系带状态机全用例（D4）

覆盖: SG-3 §5 重标定后的四带整数门槛升带转移表全用例 + 90 天降带
      + 0 浮点断言（形态冻结, 数值重标定）。

SG-3 §8.1 清单对应: T1（升带转移表全 class）/ T2（降带 90 天）/ T3（整数秒断言）
                    / T4（No-Scoring AST 断线, 应 0 改动续绿）。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_relational_bands.py -v
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.social import relational_bands as rb
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
# 升带转移表全用例（SG-3 §5.1, 取代 SG-1 §3.3 数值）
# ───────────────────────────────────────────────────────────

class TestSG3ThresholdValues:
    """SG-3 §5 门槛逐值钉死（形态冻结: 全整数, 0 浮点 / 0 加权）。"""

    def test_known_thresholds_exact(self):
        # stranger → known: co ≥ 1 或 reply ≥ 1
        assert rb._KNOWN_THRESHOLDS == {
            "reply_exchanges": 1,
            "co_presence_sessions": 1,
        }
        assert rb._KNOWN_MODE == "or"

    def test_familiar_thresholds_exact(self):
        # known → familiar: co ≥ 2 且 reply ≥ 2
        assert rb._FAMILIAR_THRESHOLDS == {
            "reply_exchanges": 2,
            "co_presence_sessions": 2,
        }
        assert rb._FAMILIAR_MODE == "and"

    def test_close_thresholds_exact_and_dream_row_removed(self):
        # familiar → close: co ≥ 4 且 reply ≥ 4（单行）
        assert rb._CLOSE_THRESHOLDS == (
            ({"reply_exchanges": 4, "co_presence_sessions": 4}, "and"),
        )
        # OQ-4: dream 行已删除（R_dream ≡ 0 → 保留即 INV-5 违规）
        assert len(rb._CLOSE_THRESHOLDS) == 1
        assert all(
            "dream_exchanges" not in thresholds
            for thresholds, _mode in rb._CLOSE_THRESHOLDS
        )


class TestUpgradeTransitions:
    """stranger→known / known→familiar / familiar→close 三行转移表全覆盖。"""

    def test_stranger_to_known_reply_ge_1(self):
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=0) == BAND_KNOWN

    def test_stranger_to_known_co_ge_1(self):
        # SG-3 §5.1: co 门槛 2 → 1（旧表下实测 co=1 的 10 条对子永远差 1）
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=1) == BAND_KNOWN

    def test_stranger_to_known_either_hit(self):
        # or 语义: 只 reply=1 也升; 只 co=1 也升
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=9) == BAND_KNOWN
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=9) == BAND_KNOWN

    def test_stranger_stays_without_threshold(self):
        assert evaluate_band(BAND_STRANGER, reply_exchanges=0, co_presence_sessions=0) == BAND_STRANGER

    def test_known_to_familiar_all_hit(self):
        assert evaluate_band(BAND_KNOWN, reply_exchanges=2, co_presence_sessions=2) == BAND_FAMILIAR

    def test_known_to_familiar_and_semantics(self):
        # and 语义: 少一个就不升
        assert evaluate_band(BAND_KNOWN, reply_exchanges=2, co_presence_sessions=1) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=1, co_presence_sessions=2) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=2, co_presence_sessions=0) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=9, co_presence_sessions=1) == BAND_KNOWN

    def test_familiar_to_close_reply_co_row(self):
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=4, co_presence_sessions=4) == BAND_CLOSE

    def test_familiar_to_close_dream_row_removed(self):
        # OQ-4（Owner 核准）: dream 行删除 → 再大的 dream_exchanges 也不升带
        assert evaluate_band(
            BAND_FAMILIAR, reply_exchanges=5, co_presence_sessions=0, dream_exchanges=4
        ) == BAND_FAMILIAR
        assert evaluate_band(
            BAND_FAMILIAR, reply_exchanges=99, co_presence_sessions=0, dream_exchanges=999
        ) == BAND_FAMILIAR
        # 反向: 同一组计数下, 达标行仍照升（证明删的是 dream 行, 不是整个门）
        assert evaluate_band(
            BAND_FAMILIAR, reply_exchanges=4, co_presence_sessions=4, dream_exchanges=0
        ) == BAND_CLOSE

    def test_familiar_to_close_shy(self):
        # reply/co 行: co≥4 且 reply≥4 全中才升
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=4, co_presence_sessions=3) == BAND_FAMILIAR
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=3, co_presence_sessions=4) == BAND_FAMILIAR

    def test_familiar_stays_without_threshold(self):
        assert evaluate_band(BAND_FAMILIAR, reply_exchanges=0, co_presence_sessions=0) == BAND_FAMILIAR

    def test_close_stays_at_top(self):
        # 顶带保持（离散阶梯只回答升格; 计数单调, 不满足也不回退）
        assert evaluate_band(BAND_CLOSE, reply_exchanges=0, co_presence_sessions=0) == BAND_CLOSE

    def test_int_only_thresholds(self):
        # 全整数阈值: 0 浮点门槛; 计数为 int 语义（传入非 int 按 int() 落定, 不 crash）
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=1) == BAND_KNOWN
        assert evaluate_band(BAND_KNOWN, reply_exchanges=2, co_presence_sessions=2) == BAND_FAMILIAR
        assert evaluate_band(BAND_STRANGER, reply_exchanges=1, co_presence_sessions=1.9) == BAND_KNOWN

    def test_invalid_current_band_fail_closed(self):
        # 脏 band 值 → 按 stranger 重新起算（不静默放行脏带）
        assert evaluate_band("alien", reply_exchanges=1, co_presence_sessions=0) == BAND_KNOWN
        assert valid_band("alien") is False
        assert valid_band(BAND_KNOWN) is True

    def test_enum_four_bands(self):
        assert RELATIONAL_BANDS == (BAND_STRANGER, BAND_KNOWN, BAND_FAMILIAR, BAND_CLOSE)


# ───────────────────────────────────────────────────────────
# 降带（90 天, SG-3 §5.2; 形态冻结 = 每次至多降 1 带 + 底带不降）
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

    def test_demote_days_is_90(self):
        # SG-3 §5.2: 30 → 90
        assert DEMOTE_DAYS == 90

    def test_no_demote_within_90_days(self):
        # 边界: 恰好 90 天整不降（SG-3 §5.2「连续 > 90 天」）
        assert should_demote(_dt(DEMOTE_DAYS).isoformat(), datetime.now(timezone.utc)) is False
        assert should_demote(_dt(89).isoformat(), datetime.now(timezone.utc)) is False
        assert should_demote(_dt(44).isoformat(), datetime.now(timezone.utc)) is False
        assert should_demote(_dt(1).isoformat(), datetime.now(timezone.utc)) is False

    def test_demote_after_90_days(self):
        assert should_demote(_dt(DEMOTE_DAYS + 1).isoformat(), datetime.now(timezone.utc)) is True
        assert should_demote(_dt(300).isoformat(), datetime.now(timezone.utc)) is True

    def test_demote_fallback_ts(self):
        # last_signal_at 缺失 → fallback_ts（last_interaction_at）生效
        assert should_demote(None, datetime.now(timezone.utc), fallback_ts=_dt(91).isoformat()) is True
        assert should_demote(None, datetime.now(timezone.utc), fallback_ts=_dt(10).isoformat()) is False

    def test_demote_missing_ts_conservative(self):
        assert should_demote(None, datetime.now(timezone.utc)) is False  # 保守不降
        assert should_demote("坏时间戳", datetime.now(timezone.utc)) is False

    def test_demote_future_ts_noop(self):
        fut = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        assert should_demote(fut, datetime.now(timezone.utc)) is False


# ───────────────────────────────────────────────────────────
# 0 浮点刚性断言（属性 + AST 双保险, SG-3 §8.1 T4 = 0 改动续绿）
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
        assert DEMOTE_DAYS * 86400 == 7776000  # 整数秒, 0 浮点乘积（90 × 86400）


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
