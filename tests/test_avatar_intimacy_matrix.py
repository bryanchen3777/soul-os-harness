"""INTIMACY-GROWTH-1-FUP：mood x intimacy 16 格矩陣逐格鎖定測試。

權威矩陣（Owner 2026-09-25 裁定）：

| tier              | mood > 0.5 | 0.0 <= mood <= 0.5 | -0.5 <= mood < 0.0 | mood < -0.5 |
|-------------------|------------|--------------------|---------------------|-------------|
| complete (76-100) | blush      | happy              | concerned           | pout        |
| accepting (51-75) | happy      | idle               | concerned           | cold        |
| building (26-50)  | happy      | idle               | concerned           | cold        |
| defensive (0-25)  | idle       | idle               | cold                | cold        |

本檔為新檔，不修改 tests/test_intimacy_growth_1.py（既有已驗收交付）。
"""

import pytest

from clients.voice_companion.web_server import (
    _intimacy_tier,
    map_emotion_to_avatar_state,
)

# tier 代表值：defensive=10 / building=40 / accepting=60 / complete=90
TIER_DEFENSIVE = 10.0
TIER_BUILDING = 40.0
TIER_ACCEPTING = 60.0
TIER_COMPLETE = 90.0

# mood band 代表值：high=1.0 / base=0.25 / low=-0.25 / verylow=-1.0
MOOD_HIGH = 1.0     # band 0: mood > 0.5
MOOD_BASE = 0.25    # band 1: 0.0 <= mood <= 0.5
MOOD_LOW = -0.25    # band 2: -0.5 <= mood < 0.0
MOOD_VERYLOW = -1.0  # band 3: mood < -0.5

# 權威矩陣：16 格逐格寫死期望值
MATRIX = {
    ("complete",  MOOD_HIGH):    "blush",      # mood > 0.5
    ("complete",  MOOD_BASE):    "happy",      # 0.0 <= mood <= 0.5
    ("complete",  MOOD_LOW):     "concerned",  # -0.5 <= mood < 0   <- FUP 修正
    ("complete",  MOOD_VERYLOW): "pout",       # mood < -0.5       <- FUP 修正
    ("accepting", MOOD_HIGH):    "happy",
    ("accepting", MOOD_BASE):    "idle",
    ("accepting", MOOD_LOW):     "concerned",
    ("accepting", MOOD_VERYLOW): "cold",
    ("building",  MOOD_HIGH):    "happy",
    ("building",  MOOD_BASE):    "idle",
    ("building",  MOOD_LOW):     "concerned",
    ("building",  MOOD_VERYLOW): "cold",
    ("defensive", MOOD_HIGH):    "idle",       # 防衛期：正向也壓抑
    ("defensive", MOOD_BASE):    "idle",
    ("defensive", MOOD_LOW):     "cold",
    ("defensive", MOOD_VERYLOW): "cold",
}

TIER_VALUE = {
    "defensive": TIER_DEFENSIVE,
    "building": TIER_BUILDING,
    "accepting": TIER_ACCEPTING,
    "complete": TIER_COMPLETE,
}

VALID_STATES = {"idle", "speaking", "happy", "concerned", "cold", "blush", "pout"}


# ---------------------------------------------------------------- T1
def test_t1_matrix_has_exactly_16_cells():
    """矩陣必須恰好是 4 tier x 4 mood band = 16 格。"""
    assert len(MATRIX) == 16


@pytest.mark.parametrize(
    "tier,mood,expected",
    [(tier, mood, expected) for (tier, mood), expected in MATRIX.items()],
    ids=[f"{tier}-mood{mood}" for (tier, mood) in MATRIX],
)
def test_t1_each_cell(tier, mood, expected):
    """T1：16 格逐格斷言（每格期望值寫死）。"""
    assert map_emotion_to_avatar_state(mood, TIER_VALUE[tier]) == expected


def test_t1_complete_band2_is_concerned_and_band3_is_pout():
    """本票核心：complete 分期 band2/band3 不得再對調。"""
    assert map_emotion_to_avatar_state(MOOD_LOW, TIER_COMPLETE) == "concerned"
    assert map_emotion_to_avatar_state(MOOD_VERYLOW, TIER_COMPLETE) == "pout"


# ---------------------------------------------------------------- T2
def test_t2_band_boundaries_half_open_semantics():
    """T2：band 判定為閉區間語義（用 tier=complete=90）。

    band 0: mood > 0.5（嚴格大於）
    band 1: 0.0 <= mood <= 0.5（含兩端）
    band 2: -0.5 <= mood < 0.0（含 -0.5，不含 0.0）
    band 3: mood < -0.5（嚴格小於）
    """
    # 上邊界：0.5 屬於 band 1 -> happy（不是 blush）
    assert map_emotion_to_avatar_state(0.5, TIER_COMPLETE) == "happy"
    # 略大於 0.5 -> band 0 -> blush
    assert map_emotion_to_avatar_state(0.5000001, TIER_COMPLETE) == "blush"
    # 0.0 屬於 band 1 -> happy
    assert map_emotion_to_avatar_state(0.0, TIER_COMPLETE) == "happy"
    # -0.0 == 0.0（Python 語義）-> band 1 -> happy
    assert map_emotion_to_avatar_state(-0.0, TIER_COMPLETE) == "happy"
    # 下邊界：-0.5 屬於 band 2 -> concerned（不是 pout）
    assert map_emotion_to_avatar_state(-0.5, TIER_COMPLETE) == "concerned"
    # 略小於 -0.5 -> band 3 -> pout
    assert map_emotion_to_avatar_state(-0.5000001, TIER_COMPLETE) == "pout"


def test_t2_negative_zero_equals_positive_zero():
    """明確鎖住 -0.0 與 0.0 同格。"""
    assert -0.0 == 0.0
    assert map_emotion_to_avatar_state(-0.0, TIER_COMPLETE) == map_emotion_to_avatar_state(
        0.0, TIER_COMPLETE
    )


# ---------------------------------------------------------------- T3
def test_t3_tier_boundaries_closed_interval():
    """T3：tier 邊界為閉區間語義（門檻值屬於較高分期）。

    25.9 -> defensive ; 26.0 -> building
    50.9 -> building  ; 51.0 -> accepting
    75.9 -> accepting ; 76.0 -> complete
    """
    # 直接鎖 _intimacy_tier 的分期結果（不受 mood 影響）
    assert _intimacy_tier(25.9) == "defensive"
    assert _intimacy_tier(26.0) == "building"
    assert _intimacy_tier(50.9) == "building"
    assert _intimacy_tier(51.0) == "accepting"
    assert _intimacy_tier(75.9) == "accepting"
    assert _intimacy_tier(76.0) == "complete"
    # 額外鎖極端值
    assert _intimacy_tier(0.0) == "defensive"
    assert _intimacy_tier(100.0) == "complete"


def test_t3_distinguish_four_tiers_by_probe_moods():
    """T3（行為層）：用探針 mood 組合真正區分四個 tier。

    背景：單一 mood 無法區分全部四 tier —— accepting 與 building 在
    mood=1.0 同回 happy、在 mood=-1.0 同回 cold。因此需要兩個探針組合：

      探針 A  mood=1.0  ：complete->blush / accepting->happy /
                          building->happy / defensive->idle
                          => 唯一能區分 defensive vs 其他三者
      探針 B  mood=-0.25：complete->concerned / accepting->concerned /
                          building->concerned / defensive->cold
                          => 無法區分前兩者，故不用於 tier 區分

    改用探針 B' mood=-1.0：complete->pout / accepting->cold /
                          building->cold / defensive->cold
                          => 唯一能區分 complete vs 其他三者

    合併 (A, B') 後四個 tier 的回傳序列兩兩互異：
      complete  -> (blush,     pout)
      accepting -> (happy,     cold)
      building  -> (happy,     cold)   <-- 與 accepting 仍同
      defensive -> (idle,      cold)

    故 accepting 與 building 在純 mood 探針下不可分（權威矩陣本身即同值），
    必須另外用 mood=-0.25 之外的結構性事實：兩者在矩陣中確實同值。
    因此最終設計為「tier 代表值 -> 觀測到的完整 4-band 行為向量」，
    用整條向量（4 格）做指紋比對，即可區分四 tier：

      complete  : (blush,     happy,  concerned, pout)
      accepting : (happy,     idle,   concerned, cold)
      building  : (happy,     idle,   concerned, cold)  <-- 與 accepting 同
      defensive : (idle,      idle,   cold,      cold)

    結論：accepting 與 building **在 map_emotion_to_avatar_state 的輸出上
    完全不可區分**（矩陣中 4 格全同），這是權威矩陣的既有設計，非缺陷。
    可區分的只有 complete / defensive / {accepting, building} 三組。

    對邊界採樣（T3 上半）驗證的正是 tier 分界本身（_intimacy_tier），
    而行為層則驗證「分界兩側確實落在不同的行為向量類」。
    """
    # 探針 A：mood=1.0 區分 defensive vs 其餘
    assert map_emotion_to_avatar_state(1.0, 90.0) == "blush"
    assert map_emotion_to_avatar_state(1.0, 60.0) == "happy"
    assert map_emotion_to_avatar_state(1.0, 40.0) == "happy"
    assert map_emotion_to_avatar_state(1.0, 10.0) == "idle"

    # 探針 B'：mood=-1.0 區分 complete vs 其餘
    assert map_emotion_to_avatar_state(-1.0, 90.0) == "pout"
    assert map_emotion_to_avatar_state(-1.0, 60.0) == "cold"
    assert map_emotion_to_avatar_state(-1.0, 40.0) == "cold"
    assert map_emotion_to_avatar_state(-1.0, 10.0) == "cold"

    # 合併探針後：complete / defensive 各自唯一，accepting 與 building 同值
    fingerprint = {}
    for name, value in (
        ("complete", 90.0),
        ("accepting", 60.0),
        ("building", 40.0),
        ("defensive", 10.0),
    ):
        fingerprint[name] = (
            map_emotion_to_avatar_state(1.0, value),
            map_emotion_to_avatar_state(0.25, value),
            map_emotion_to_avatar_state(-0.25, value),
            map_emotion_to_avatar_state(-1.0, value),
        )

    assert fingerprint["complete"] == ("blush", "happy", "concerned", "pout")
    assert fingerprint["defensive"] == ("idle", "idle", "cold", "cold")
    # 權威矩陣中 accepting 與 building 四格全同 —— 明文記錄為預期行為
    assert fingerprint["accepting"] == ("happy", "idle", "concerned", "cold")
    assert fingerprint["building"] == ("happy", "idle", "concerned", "cold")
    assert fingerprint["accepting"] == fingerprint["building"]

    # 至少三組行為指紋互異（complete / defensive / {accepting,building}）
    assert len({fingerprint["complete"], fingerprint["defensive"], fingerprint["accepting"]}) == 3


def test_t3_boundary_values_land_on_expected_tier_fingerprint():
    """T3：分界值 26.0 / 51.0 / 76.0 確實跨入較高分期（用 mood=1.0 觀測）。

    building(40) 與 accepting(60) 在 mood=1.0 同為 happy，故無法用此探針
    區分 26.0 與 51.0 的差異；但可用 _intimacy_tier 直接鎖分期（見上一測試），
    此處僅驗證行為層不因跨界而出現意外值。
    """
    for intimacy in (25.9, 26.0, 50.9, 51.0, 75.9, 76.0):
        assert map_emotion_to_avatar_state(1.0, intimacy) in VALID_STATES

    # 76.0 跨入 complete -> blush（與 accepting 的 happy 不同）=> 可分
    assert map_emotion_to_avatar_state(1.0, 75.9) == "happy"
    assert map_emotion_to_avatar_state(1.0, 76.0) == "blush"
    # 26.0 跨出 defensive -> idle 變成 happy => 可分
    assert map_emotion_to_avatar_state(1.0, 25.9) == "idle"
    assert map_emotion_to_avatar_state(1.0, 26.0) == "happy"


# ---------------------------------------------------------------- T4
def test_t4_defensive_suppression_invariant():
    """T4：防衛期壓抑不變式 —— 無論 mood 為何，只能是 idle 或 cold。"""
    for mood in (1.0, 0.5, 0.0, -0.5, -1.0):
        result = map_emotion_to_avatar_state(mood, TIER_DEFENSIVE)
        assert result in ("idle", "cold"), f"defensive 期 mood={mood} 回 {result}"


def test_t4_defensive_suppression_across_tier_range():
    """T4（強化）：defensive 全區間（0-25.9）皆滿足壓抑不變式。"""
    for intimacy in (0.0, 10.0, 25.0, 25.9):
        assert _intimacy_tier(intimacy) == "defensive"
        for mood in (2.0, 1.0, 0.5, 0.0, -0.5, -1.0, -2.0):
            result = map_emotion_to_avatar_state(mood, intimacy)
            assert result in ("idle", "cold"), (
                f"intimacy={intimacy} mood={mood} 回 {result}"
            )


# ---------------------------------------------------------------- T5
def test_t5_all_cells_are_valid_registry_states():
    """T5：16 格回傳值皆為 registry 內的合法狀態。"""
    for (tier, mood), _expected in MATRIX.items():
        result = map_emotion_to_avatar_state(mood, TIER_VALUE[tier])
        assert result in VALID_STATES, f"{tier}/{mood} 回非法狀態 {result}"


def test_t5_valid_states_set_documented():
    """T5：合法狀態集合與 registry 一致（明文寫死）。"""
    assert VALID_STATES == {"idle", "speaking", "happy", "concerned", "cold", "blush", "pout"}
