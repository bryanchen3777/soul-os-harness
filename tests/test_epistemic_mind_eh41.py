"""EH-4.1 驗收測試 — 認知評估算子與當輪探詢姿態（Phase 1：讀側感知 + 當輪姿態）。

工單：EH-4.1（步驟 3）；契約：``docs/EH-4-EPISTEMIC-MIND-CONTRACT.md``
§1.2~§1.6 / §2.2~§2.4（含 EH-4-AMEND-1 / AMEND-2）。

測試項目（工單 Test A~E）：
  A 原生工具免疫驗證（FSA-0；核心）
  B 物理特徵分流 vs 禁品名查表驗證（AC-1~AC-6；核心）
  C 口語無介詞階梯驗證（SL-1~SL-5 ＋ IT-7/IT-8 靜默）
  D 當輪關切守門驗證（IT-7~IT-9）
  E 現代角色 Bypass 防回歸（D-INV-2／S8；0 差量運算）

附帶（契約附錄 B 靜態斷言 S1／S11／S13／S14／S15／S16 的落地斷言）：
  F 實體優先級：P1 受詞／食材永不升為 entity_key（FSA-2／S9）
  G 模板字面 0 世界觀名詞（S11）＋ L2 可執行碼 0 世界觀名詞（S1）
  H 雙端同構：文字端與語音端 Horizon Block 字串完全相同（ISO-2）

註：本檔為 tests/ 測試資料，允許出現世界觀名詞（D-INV-1）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.inner_life import epistemic_appraisal as ea
from src.inner_life.epistemic_appraisal import (
    _AXIS_KEYS,
    _TEMPLATE_ANCHOR,
    _TEMPLATE_CONCERN,
    AppraisalInput,
    appraise,
    clear_pack_cache,
    format_epistemic_horizon_delta,
    load_epistemic_pack,
)

_AGENT_REM = "agent_rem"
#: 契約 §1.3 D-INV-2 的 7 位現代原生角色（src/memory/sage/horizon.py:61-69）
_MODERN_NATIVE_AGENTS = (
    "agent_akane",
    "agent_mai",
    "agent_anna",
    "agent_aoi",
    "agent_miku",
    "agent_ruka",
    "agent_yua",
)


@pytest.fixture(autouse=True)
def _isolate_pack_cache(tmp_path_factory, monkeypatch):
    """pack 快取為進程內 dict（契約 §1.6）→ 每個測試前後清空，保證隔離。

    EH-4.2 追加：同時隔離 SAGE ``data_root`` —— ``appraise()`` 的 S3「已內化鎖定」
    會讀 ``data_root()/memory/<agent>/graph.sqlite``；不隔離則本套件會隨生產資料庫
    狀態漂移（非 hermetic 測試）。
    """
    from src.paths import reset_data_root

    clear_pack_cache()
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path_factory.mktemp("eh41_data")))
    reset_data_root()
    yield
    clear_pack_cache()
    reset_data_root()


def _record(utterance: str):
    return appraise(AppraisalInput(agent_id=_AGENT_REM, utterance=utterance))


# ─────────────────────────────────────────────────────────────────────
# Test A — 原生工具免疫驗證（FSA-0；NT-1~NT-6／S13）
# ─────────────────────────────────────────────────────────────────────


def test_a_native_tools_are_immune():
    """公館本來就有的器具 → 立即 FSA（0 對撞、0 錨點、0 姿態）。"""
    for utterance in (
        "我用鐵鍋炒了菜",          # 鐵鍋 ∈ labor.native_tools
        "我拿風箱鼓風",            # 風箱 ∈ energy/labor.native_tools
        "我用鐵鍋炒了菜，鍋子燙得很",  # 同時命中 heating ＋ hot_shell（NT-6 的關鍵情形）
        "我用烘爐烤了餅",          # 烘爐 ∈ energy.native_tools
    ):
        assert appraise(AppraisalInput(agent_id=_AGENT_REM, utterance=utterance)) is None, utterance
        assert format_epistemic_horizon_delta(_AGENT_REM, utterance) == "", utterance


def test_a_immunity_pool_equals_four_axis_union():
    """S13／NT-1：判定池 == 四軸 native_tools ∪ native_basis（去重保序）。"""
    pack = load_epistemic_pack(_AGENT_REM)
    assert pack is not None
    expected = []
    for key in _AXIS_KEYS:
        axis = pack.axes[key]
        for term in tuple(axis.native_tools) + tuple(axis.native_basis):
            if term not in expected:
                expected.append(term)
    assert pack.native_immunity_terms == tuple(expected)
    # 單字詞條防護（子字串誤傷）：每一詞條長度 ≥ 2
    assert all(len(t) >= 2 for t in pack.native_immunity_terms)
    # 反例鋼印：單字詞條絕不得存在（「鍋」「箱」「爐」會誤傷「氣炸鍋」「黑箱子」）
    assert not {"鍋", "箱", "爐"} & set(pack.native_immunity_terms)


# ─────────────────────────────────────────────────────────────────────
# Test B — 物理特徵分流 vs 禁品名查表（AC-1~AC-6／S15；核心）
# ─────────────────────────────────────────────────────────────────────


def test_b_secondary_feature_routing_and_no_name_lookup():
    # A：氣流／風聲 → forced_air_heat
    rec_a = _record("我用這個機器弄，裡面一直吹熱風呼呼響")
    assert rec_a is not None
    assert rec_a.anchor_class == "forced_air_heat"
    assert "風箱" in rec_a.anchor and "熱道" in rec_a.anchor
    assert {"airflow", "fan_noise"} <= set(rec_a.features)

    # B：封閉腔體 ＋ 玻璃門 → enclosed_heat（與 A 錨點不同）
    rec_b = _record("我用這個機器弄，透過玻璃門看著食物變熱")
    assert rec_b is not None
    assert rec_b.anchor_class == "enclosed_heat"
    assert "封閉石爐" in rec_b.anchor
    assert rec_b.anchor != rec_a.anchor

    # C：0 次級物理特徵（純取得句）→ EH-4.2 UR-2 靜默邊界（FSA-1 嚴格化）：
    #    取得動詞（「買了」）只證明主人添了新物，**不是物理特徵證據** →
    #    嚴格回 None（0 錨點、0 烘爐回退、0 隱喻注入、0 無特徵腦補）。
    rec_c = _record("我買了一台氣炸鍋")
    assert rec_c is None
    assert format_epistemic_horizon_delta(_AGENT_REM, "我買了一台氣炸鍋") == ""
    # 兩個特徵分流錨點仍互不相同（INV-18 單一模板化的反例鋼印）
    assert rec_a.anchor != rec_b.anchor


def test_b_four_anchor_classes_are_distinct():
    """S15：四類錨點類別各自命中且錨點互不相同；單特徵 → 回退（AC-3）。"""
    cases = {
        "forced_air_heat": "我用氣炸鍋弄豆腐，它一直吹熱風",
        "enclosed_heat": "我用微波爐熱了湯，玻璃門後面亮著、定時一響就好",
        "open_flat_heat": "我用平底電熱鍋炒菜，一下子就熟",
        "cold_preservation": "我買了一台冰箱，裡面的菜冰著、放著不壞",
    }
    anchors = []
    classes = {}
    for expected_class, utterance in cases.items():
        rec = _record(utterance)
        assert rec is not None, utterance
        classes[expected_class] = rec.anchor_class
        anchors.append(rec.anchor)
    assert classes == {k: k for k in cases}, classes
    assert len(set(anchors)) == len(anchors), anchors

    # 僅 heating、0 次級特徵 → anchor_class == "" 且回退 analogy_anchors[0]（AC-3）
    pack = load_epistemic_pack(_AGENT_REM)
    fallback = pack.axes["energy_dynamics"].analogy_anchors[0]
    rec_fallback = _record("我用那個爐子，好燙")
    assert rec_fallback is not None
    assert rec_fallback.anchor_class == ""
    assert rec_fallback.anchor.startswith("似" + fallback + "，卻無")


def test_b_no_appliance_name_table_in_operator():
    """AC-2／S15：L2 可執行碼 0「現代器具名 ↔ 錨點」對照表。"""
    source = Path(ea.__file__).read_text(encoding="utf-8")
    for appliance in ("氣炸鍋", "微波爐", "電熱鍋", "冰箱", "洗碗機", "烤箱"):
        assert appliance not in source, appliance


# ─────────────────────────────────────────────────────────────────────
# Test C — 口語無介詞階梯（SL-1~SL-5／S14）＋ 背景靜默（IT-8）
# ─────────────────────────────────────────────────────────────────────


def test_c_spoken_ladder_extracts_subject_without_concern():
    utterance = "氣炸鍋很方便"
    rec = _record(utterance)
    assert rec is not None
    assert rec.extraction_ladder == "spoken_subject"
    assert rec.entity_terms == ("氣炸鍋",)
    assert rec.anchor  # 成功生成本體隱喻

    delta = format_epistemic_horizon_delta(_AGENT_REM, utterance)
    assert "[本體參照]" in delta
    # IT-7 G1 未過（純評價、主人未在場操作）→ 關切段為空（嚴禁伴隨發問）
    assert "[當下的關切]" not in delta
    assert _TEMPLATE_CONCERN not in delta


def test_c_sl1_strips_demonstratives_keeps_modifier():
    """SL-1：去指示詞／量詞，但**不去語義修飾語**（黑箱子 ≠ 箱子）。"""
    rec = _record("那個黑箱子很好用")
    assert rec is not None
    assert rec.entity_terms == ("黑箱子",)
    assert rec.extraction_ladder == "spoken_subject"


def test_c_conservative_aborts():
    """UR-1／UR-3／UR-4：階梯 3、代詞指代、純存在句 → 整段 None（0 錨點亦 0 發問）。"""
    for utterance in (
        "那個東西很方便",   # SL-2 代詞／占位詞 → 階梯 3
        "今天天氣好",       # 0 候選 → 階梯 3
        "辦公室有微波爐",   # 存在句、0 評價／0 物理特徵 → FSA-1(b)
    ):
        assert appraise(AppraisalInput(agent_id=_AGENT_REM, utterance=utterance)) is None, utterance
        assert format_epistemic_horizon_delta(_AGENT_REM, utterance) == "", utterance


# ─────────────────────────────────────────────────────────────────────
# Test D — 當輪關切守門（IT-7~IT-9／S16）
# ─────────────────────────────────────────────────────────────────────


def test_d_in_person_hazard_injects_single_concern():
    utterance = "我正在用這個會吹熱風的機器弄晚餐，好燙"
    rec = _record(utterance)
    assert rec is not None
    assert rec.anchor_class == "forced_air_heat"
    assert rec.hazard_suspected is True

    delta = format_epistemic_horizon_delta(_AGENT_REM, utterance)
    # 本體隱喻 + 一句以內的女僕安全關切
    assert "[本體參照]" in delta
    assert "[當下的關切]" in delta
    # IT-9 一句上限：關切段**恰出現一次**（0 連問、0 二段追問）
    assert delta.count(_TEMPLATE_CONCERN) == 1
    assert delta.count("[當下的關切]") == 1
    assert delta.count("？") == 0  # 模板為單句關切，非連環盤問


def test_d_concern_gate_is_g1_and_g2():
    """IT-7：G1（在場操作）∧ G2（危害／職責）才注入；其餘只注入類比段。"""
    pack = load_epistemic_pack(_AGENT_REM)
    assert pack is not None

    # G2 成立（duty）但 G1 不成立（純評價）→ 不注入
    assert "[當下的關切]" not in format_epistemic_horizon_delta(_AGENT_REM, "氣炸鍋很方便")
    # IT-8 背景提及鋼印 → 不注入
    assert "[當下的關切]" not in format_epistemic_horizon_delta(_AGENT_REM, "我買了一台氣炸鍋")
    # G1 ∧ G2 成立 → 注入
    assert "[當下的關切]" in format_epistemic_horizon_delta(
        _AGENT_REM, "我正在用這個會吹熱風的機器弄晚餐，好燙"
    )


# ─────────────────────────────────────────────────────────────────────
# Test E — 現代角色 Bypass 防回歸（D-INV-2／S8；0 差量運算）
# ─────────────────────────────────────────────────────────────────────


def test_e_modern_native_agents_bypass_with_zero_appraisal(monkeypatch):
    calls = []
    real_appraise = ea.appraise

    def _spy(inp):
        calls.append(inp.agent_id)
        return real_appraise(inp)

    monkeypatch.setattr(ea, "appraise", _spy)
    for agent_id in _MODERN_NATIVE_AGENTS:
        assert load_epistemic_pack(agent_id) is None
        assert format_epistemic_horizon_delta(agent_id, "我用氣炸鍋弄了豆腐，它一直吹熱風") == ""
        assert format_epistemic_horizon_delta(agent_id, "我正在用這個會吹熱風的機器弄晚餐，好燙") == ""
    # 0 差量運算（前置短路；契約 §8 D-4 採 (a)）
    assert calls == []


def test_e_other_non_modern_agent_without_pack_also_bypasses():
    """無 pack 的非現代角色（第二個異界角色尚未建包）→ bypass，不誤掛阻力。"""
    assert load_epistemic_pack("agent_ram") is None
    assert format_epistemic_horizon_delta("agent_ram", "我用氣炸鍋弄了豆腐，它一直吹熱風") == ""
    assert format_epistemic_horizon_delta("", "我用鐵鍋炒了菜") == ""


# ─────────────────────────────────────────────────────────────────────
# F — 實體優先級（FSA-2／S9）：P1 受詞／食材永不升為 entity_key
# ─────────────────────────────────────────────────────────────────────


def test_f_objects_stay_background_terms():
    rec = _record("我用氣炸鍋弄了豆腐，它自己吹出熱風")
    assert rec is not None
    assert rec.entity_terms == ("氣炸鍋",)
    assert "豆腐" in rec.background_terms
    assert "豆腐" not in rec.entity_terms
    # 荒謬類比鋼印：錨點主詞永不得是食材
    assert "豆腐" not in rec.anchor

    # 純食材句（無器具候選）→ 階梯 3
    assert appraise(AppraisalInput(agent_id=_AGENT_REM, utterance="今天煮了豆腐湯")) is None


# ─────────────────────────────────────────────────────────────────────
# G — 反硬編碼靜態斷言（S1／S11）
# ─────────────────────────────────────────────────────────────────────


def test_g_templates_and_operator_contain_no_worldview_nouns():
    pack = load_epistemic_pack(_AGENT_REM)
    terms = tuple(pack.native_immunity_terms) + tuple(pack.forbidden_output_terms)
    for template in (_TEMPLATE_ANCHOR, _TEMPLATE_CONCERN):
        for term in terms:
            assert term not in template, (term, template)

    # S1：L2 可執行碼 0 世界觀名詞（含註解；比契約更嚴）。
    # 唯一豁免＝契約 §2.2 通用特徵詞表本身即含的**通用感官詞**（例：「焦味」同時是
    # hot_shell 觸發詞與 pack 詞條）；該詞表由契約強制，不屬「世界觀名詞硬編碼」。
    lexicon_words = {t for _k, triggers, _a in ea._FEATURE_LEXICON for t in triggers}
    source = Path(ea.__file__).read_text(encoding="utf-8")
    leaked = [t for t in terms if t in source and t not in lexicon_words]
    assert leaked == [], leaked
    # 契約附錄 B S1 的硬性字面清單 → 0 命中
    assert not re.search(r"羅茲瓦爾|柴火|魔石|烘爐|風箱", source)


# ─────────────────────────────────────────────────────────────────────
# H — 雙端同構（ISO-1／ISO-2／ISO-4）
# ─────────────────────────────────────────────────────────────────────


def test_h_text_and_voice_horizon_blocks_are_identical():
    from clients.voice_companion.akane_voice_brain import format_voice_horizon_block
    from src.llm.proxy import _format_horizon_block

    for utterance in (
        "我用氣炸鍋弄了豆腐，它自己吹出熱風",
        "我正在用這個會吹熱風的機器弄晚餐，好燙",
        "我用鐵鍋炒了菜",
        "",
    ):
        text_block = _format_horizon_block(_AGENT_REM, session_context="", utterance=utterance)
        voice_block = format_voice_horizon_block(_AGENT_REM, session_context="", utterance=utterance)
        assert text_block == voice_block, utterance


def test_h_voice_end_does_not_implement_appraisal():
    """ISO-1／ISO-4：clients/ 非測試碼 0 差量實作、0 類別常數（只透傳）。"""
    source = Path("clients/voice_companion/akane_voice_brain.py").read_text(encoding="utf-8")
    for forbidden in (
        "epistemic_appraisal",
        "appraise(",
        "anchor_class",
        "forced_air_heat",
        "enclosed_heat",
        "open_flat_heat",
        "cold_preservation",
    ):
        assert forbidden not in source, forbidden


def test_h_proxy_injects_delta_only_when_utterance_present():
    """additive 契約：utterance 預設 "" → 0 行為變化（不注入差量段）。"""
    from src.llm.proxy import _format_horizon_block

    baseline = _format_horizon_block(_AGENT_REM, session_context="")
    assert "[本體參照]" not in baseline
    with_delta = _format_horizon_block(
        _AGENT_REM, session_context="", utterance="我用氣炸鍋弄了豆腐，它自己吹出熱風"
    )
    assert "[本體參照]" in with_delta
    assert with_delta.startswith(baseline)  # 既有段位序不動（負向約束在前、Idiolect 在後）
    # 現代原生角色 → 整塊為空（既有 D3 bypass 不受影響）
    assert _format_horizon_block("agent_akane", session_context="", utterance="我用氣炸鍋弄了豆腐") == ""
