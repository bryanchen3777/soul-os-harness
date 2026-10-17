# -*- coding: utf-8 -*-
"""SOUL-BUG-006 Step 2：world 生產端的**自然語言環境敘事**（Owner 2026-10-17 裁示採第 2 案）。

為什麼有這個檔
--------------
Step 1 把昇華消費端改成 fail-closed：事件的 extras 沒有 `narrative` 就不建立節點。
而 world 事件的 extras 實測只有 `{world_source, world_type, world_novelty_id}` 三個
純 metadata 鍵 ⇒ **整批被攔截**，直接違反 EH-2.1 的 Owner 裁定（陰晴風雨與主人
行程屬共同生活的感知邊界，必須放行進昇華鏈，「不得感知閹割」）。

Owner 裁示的示範句是「今日氣溫降低，且伴隨陰雨，空氣微冷」。本檔釘住的方向：
- **自然語言**，不是 `weather_temperature_c=20.0` 這種 key=value 簿記；
- **確定性、0 LLM**（同一事件必須永遠得到同一句，否則節點冪等去重失效）；
- **取不到就不發明**（回空字串，消費端 fail-closed 照常生效）。

與既有契約的關係
--------------
`tests/test_m5_15_5_world_inner_life_identity_bridge.py::test_j2_provenance_extras_unchanged`
以逐鍵等值釘死 extras 形狀 —— 已隨本票**刻意更新**（附裁示依據）。
本檔獨立驗證渲染行為本身，不重複那份契約。
"""
from __future__ import annotations

import pytest

from src.inner_life import InnerLifeWriter
from src.inner_life.elevation_adapter import run_elevation
from src.world.inner_life_adapter import (
    WorldInnerLifeAdapter,
    _describe_temperature,
    world_event_narrative,
)
from src.world.perception import WorldEvent


def _we(**kw) -> WorldEvent:
    base = dict(
        source="weather",
        type="rain_started",
        novelty_id="nov_1",
        ts="2026-10-17T12:00:00+00:00",
        summary="雨開始下了",
        data={},
    )
    base.update(kw)
    return WorldEvent(**base)


# ── 決定性與「不發明」的基本保證 ─────────────────────────────────────


def test_same_event_always_renders_the_same_narrative():
    """同一事件必須永遠得到同一句 —— 節點冪等去重（SOUL-BUG-001）靠這個。"""
    we = _we(data={"weather_temperature_c": 12.5})
    first = world_event_narrative(we)
    assert first == world_event_narrative(_we(data={"weather_temperature_c": 12.5}))
    assert first


def test_unknown_type_falls_back_to_summary_and_never_invents():
    """未列入白名單的型別：有 summary 就用它本身，沒有就回 ""。"""
    assert world_event_narrative(_we(type="mystery_type")) == "雨開始下了"
    # 空 summary ⇒ 空敘事，**不塞佔位字串**（讓 fail-closed 照常生效）
    assert world_event_narrative(_we(type="mystery_type", summary="   ")) == ""


def test_empty_calendar_summary_yields_no_narrative():
    assert world_event_narrative(_we(source="calendar", type="calendar_event", summary="")) == ""


# ── 各型別的自然語言形狀 ─────────────────────────────────────────────


def test_rain_started_renders_a_sentence_not_key_value_pairs():
    narrative = world_event_narrative(
        _we(type="rain_started", data={"weather_temperature_c": 9.0})
    )
    assert "雨" in narrative
    assert "微冷" in narrative            # 9°C 落在 (5, 12] 帶
    assert narrative.endswith("。")
    # 🔴 核心不變量：不得含任何 key=value 簿記
    for mark in ("weather_temperature_c", "=", "weather_source", "world_type"):
        assert mark not in narrative, f"敘事不得含簿記 {mark!r}：{narrative!r}"


@pytest.mark.parametrize(
    "temp,expected_word",
    [(0.0, "寒冷"), (5.0, "寒冷"), (9.0, "微冷"), (18.0, "涼"), (27.0, "溫暖"), (33.0, "炎熱")],
)
def test_temperature_bands_map_to_natural_words(temp, expected_word):
    assert _describe_temperature(temp) == expected_word


def test_temperature_band_rejects_non_numeric_rather_than_guessing():
    assert _describe_temperature("很冷") == ""
    assert _describe_temperature(None) == ""


def test_temp_change_describes_the_change():
    narrative = world_event_narrative(
        _we(type="weather_temp_change", data={"weather_temperature_c": 30.0})
    )
    assert "炎熱" in narrative
    assert "=" not in narrative


def test_calendar_and_going_outside_mention_the_human_context():
    cal = world_event_narrative(
        _we(source="calendar", type="calendar_event", summary="和三玖的會議")
    )
    assert cal == "行程上寫著：和三玖的會議。"

    out = world_event_narrative(
        _we(source="social", type="user_going_outside", summary="")
    )
    assert out == "主人出門了。"


# ── extras 組裝：3 鍵不變，narrative 純附加 ──────────────────────────


def test_build_extras_keeps_the_three_legacy_keys_and_adds_narrative():
    extras = WorldInnerLifeAdapter._build_extras(
        _we(data={"weather_temperature_c": 9.0})
    )
    assert extras["world_source"] == "weather"
    assert extras["world_type"] == "rain_started"
    assert extras["world_novelty_id"] == "nov_1"
    assert extras["narrative"].startswith("外面下起了雨")


def test_build_extras_omits_narrative_key_when_there_is_nothing_to_say():
    """沒有敘事就**不放那個鍵** —— 絕不放空字串當假敘事。"""
    extras = WorldInnerLifeAdapter._build_extras(
        _we(source="calendar", type="calendar_event", summary="")
    )
    assert "narrative" not in extras
    assert len(extras) == 3


# ── 端到端：環境事件重新通過 fail-closed（Step 2 的真正回報）───────────


def test_environment_event_now_survives_the_consumer_fail_closed(tmp_path):
    """🔴 本票的驗收面：`rain_started` 帶著真實敘事，**重新**在昇華鏈產生節點。

    Step 1 之前這條路徑被 fail-closed 整批攔截（EH-2.1 違規）。
    """
    writer = InnerLifeWriter()
    adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
    we = _we(data={"weather_temperature_c": 9.0})
    ev = adapter._create_inner_life_event(we)

    nodes = run_elevation(ev, [], store_dir=tmp_path / "elevation")
    assert len(nodes) == 1, "環境事件帶敘事後應重新產出 pattern 節點"
    assert nodes[0].node_type == "pattern"
    assert nodes[0].content == ev.provenance.extras["narrative"]
