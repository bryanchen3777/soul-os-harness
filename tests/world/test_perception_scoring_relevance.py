"""
tests/world/test_perception_scoring_relevance.py — LIFE-THREAD-W2 評分結構守門

工單: docs/LIFE-THREAD-W2-PERCEPTION-SCORING-WORK-ORDER.md
出票: 主大腦 (Lin), 2026-10-03 / Owner 裁定方案 3「修評分結構本身」

背景
----
`final = 0.30·relevance + 0.20·novelty + 0.25·personal_significance
        + 0.10·emotional_significance + 0.15·temporal_significance
        + min(1.0, priority_boost·0.05)`

`relevance` 是權重最大的維度, 但 `news_event`（生產中實際存在的型別, 2026-09-21
起 534 次評估）從未被列入 `TYPE_BASELINE_RELEVANCE`, 只能吃未知型別預設值 0.10,
導致 final 結構性卡在 0.3450 < 門檻 0.35 ⇒ `accepted` 自 2026-09-21 起恆為 False。

本票修的是 **評分結構**（把 news_event 補進 baseline 表）, **不是門檻**。

本檔的職責是雙向守門:
  1. news_event 現在**能**通過（回歸守門: 不讓未來有人又把它從表裡刪掉）
  2. Bry 2026-08-07 / 2026-09-14 的裁定**依然成立**（天氣溫度波動、八卦仍被拒;
     門檻仍是 0.35; calendar/outside 的 baseline 與 boost 未動）

所有斷言都是**具體數值**, 不是「大概」「看起來」, 避免評分結構被無聲漂移。
"""
from __future__ import annotations

import pytest

from src.world.perception import (
    DEFAULT_ACCEPT_THRESHOLD,
    PRIORITY_BOOST_WEIGHT,
    SCORE_WEIGHTS,
    TYPE_BASELINE_RELEVANCE,
    WorldEvent,
    compute_scores,
    should_accept,
)

# 加權算式的獨立重算（不呼叫 PerceptionScores.final）, 確保測試不是在自證。
#
#   0.30·rel + 0.20·nov + 0.25·per + 0.10·emo + 0.15·tmp + pri·0.05
#
# 這四個 tuple 是「無 user context、novelty_count=1、temporal_salience=low、
# anticipatory_flavor=none、vulnerability_window=False、event_priority=0」
# 下的各維度地板分。
_FLOORS = {
    "rel": 0.30,  # type baseline
    "nov": 1.00,  # novelty_count = 1
    "per": 0.20,  # 沒有 user context 命中
    "emo": 0.20,  # 沒有 vulnerability_window / longing / anxious
    "tmp": 0.30,  # temporal_salience = "low"
}


def _expected_final(rel: float, *, emo: float = 0.20, pri: float = 0.0) -> float:
    """用 SCORE_WEIGHTS 獨立重算加權 final, 避免「拿實作驗實作」。"""
    per = _FLOORS["per"]
    tmp = _FLOORS["tmp"]
    nov = _FLOORS["nov"]
    weighted = (
        SCORE_WEIGHTS["relevance"] * rel
        + SCORE_WEIGHTS["novelty"] * nov
        + SCORE_WEIGHTS["personal_significance"] * per
        + SCORE_WEIGHTS["emotional_significance"] * emo
        + SCORE_WEIGHTS["temporal_significance"] * tmp
    )
    return min(1.0, weighted + pri * PRIORITY_BOOST_WEIGHT)


def _event(event_type: str) -> WorldEvent:
    """最小合法 WorldEvent（WorldEvent.__post_init__ 只驗 priority 是 int）。"""
    return WorldEvent(
        source="news",
        type=event_type,
        novelty_id=f"w2_test_{event_type}",
        ts="2026-10-03T00:00:00Z",
        summary="測試用世界事件",
    )


# ── 1. news_event 已納入 baseline 表（本票的唯一可執行改動）────────────────

def test_news_event_baseline_is_030() -> None:
    """news_event 必須在表裡, 且是 0.30（不再是未知型別的 0.10）。"""
    assert TYPE_BASELINE_RELEVANCE["news_event"] == 0.30


# ── 2. news_event 現在能通過門檻 ──────────────────────────────────────────

def test_news_event_reaches_threshold() -> None:
    """novelty_count=1 的 news_event 應 accepted=True, 且 final >= 0.35。"""
    scores = compute_scores(_event("news_event"), novelty_count=1)
    accepted, reason = should_accept(scores)
    assert scores.final() >= DEFAULT_ACCEPT_THRESHOLD
    assert accepted is True, reason


def test_news_event_final_is_0405() -> None:
    """釘死加權算式: 0.30·0.30 + 0.20·1.00 + 0.25·0.20 + 0.10·0.20 + 0.15·0.30 = 0.4050。"""
    scores = compute_scores(_event("news_event"), novelty_count=1)
    assert scores.final() == pytest.approx(0.4050, abs=1e-6)
    assert scores.final() == pytest.approx(_expected_final(0.30), abs=1e-6)


# ── 3. Bry 2026-08-07 拍板守門: 溫度波動 / 八卦仍被 reject ─────────────────

def test_weather_temp_change_still_rejected() -> None:
    """保護 2026-08-07 拍板 + 2026-09-14「天氣小事不值得傳訊」裁定。

    rel=0.05 → final = 0.3300 < 0.35 ⇒ accepted=False。
    """
    scores = compute_scores(_event("weather_temp_change"), novelty_count=1)
    accepted, reason = should_accept(scores)
    assert scores.final() == pytest.approx(0.3300, abs=1e-6)
    assert scores.final() == pytest.approx(_expected_final(0.05), abs=1e-6)
    assert accepted is False, reason


def test_celebrity_news_still_rejected() -> None:
    """同樣保護 2026-08-07 拍板: celebrity_news = 0.05 仍被拒。"""
    scores = compute_scores(_event("celebrity_news"), novelty_count=1)
    accepted, reason = should_accept(scores)
    assert scores.final() == pytest.approx(0.3300, abs=1e-6)
    assert accepted is False, reason


# ── 4. Frozen Contract 逐項釘死 ───────────────────────────────────────────

def test_threshold_unchanged() -> None:
    """本票的前提就是「不動門檻」（方案 3 與「下調門檻」的根本差異）。"""
    assert DEFAULT_ACCEPT_THRESHOLD == 0.35


def test_calendar_and_outside_baselines_unchanged() -> None:
    """2026-08-07 20:02 拍板的 type-based boost 對應的 baseline 不得變動。"""
    assert TYPE_BASELINE_RELEVANCE["calendar_event"] == 0.30
    assert TYPE_BASELINE_RELEVANCE["user_going_outside"] == 0.30


def test_score_weights_unchanged() -> None:
    """SCORE_WEIGHTS 五維權重屬 Frozen Contract（本票 0 改動）。"""
    assert SCORE_WEIGHTS == {
        "relevance": 0.30,
        "novelty": 0.20,
        "personal_significance": 0.25,
        "emotional_significance": 0.10,
        "temporal_significance": 0.15,
    }
    assert pytest.approx(sum(SCORE_WEIGHTS.values())) == 1.00
    assert PRIORITY_BOOST_WEIGHT == 0.05


# ── 5. 天氣不是「永遠不可見」, 只是「不該僅憑溫度波動可見」────────────────

def test_weather_with_context_can_pass() -> None:
    """vulnerability_window=True → emo=0.60 → final = 0.3700 >= 0.35。

    證明天氣事件在「有情感狀態進場」時仍可被看見, 2026-09-14 裁定保護的是
    「純溫度波動」而非「天氣永遠不可見」。
    """
    scores = compute_scores(
        _event("weather_temp_change"),
        novelty_count=1,
        vulnerability_window=True,
    )
    accepted, reason = should_accept(scores)
    assert scores.emotional_significance == 0.60
    assert scores.final() == pytest.approx(0.3700, abs=1e-6)
    assert scores.final() == pytest.approx(_expected_final(0.05, emo=0.60), abs=1e-6)
    assert accepted is True, reason
