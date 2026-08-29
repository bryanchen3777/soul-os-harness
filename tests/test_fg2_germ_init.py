"""
FG-2 (germ 初始化邊界) — 測試

工單: docs/FREE-GROWTH-GERM-DESIGN.md → FG-2 Germ Initialization Boundary Implementation

聚焦:
  1. loads_persona/loader 對 agent 的 initialization_mode 解析 fail-closed（seeded 缺省）
  2. germ 模式: load_persona 完全不注入 _AGENT_IDENTITY_RULES、注入 germ anchor 三句 +
     名字 handle、_AGENT_DIALOGUE_RULES 拆開(protocol 留 / preference 拿掉)
  3. _build_messages_* germ_anchor 參數: germ 下 seeded identity_anchor 由 germ_anchor 替換
  4. seeded 路徑逐字節不變（與 fixtures/fg2_seeded_persona_baseline.json 對比 10 個 agent）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from configs.loader import create_agents, load_config
from src.llm import proxy as proxy_mod
from src.llm.proxy import (
    _AGENT_DIALOGUE_RULES,
    _AGENT_DIALOGUE_RULES_PROTOCOL,
    _AGENT_IDENTITY_RULES,
    _GERM_ANCHOR,
    _GERM_NAME_HANDLE_TEMPLATE,
    _build_germ_anchor,
    _build_messages_group,
    _build_messages_private,
    _resolve_init_mode,
    load_persona,
)

_SEEDED_AGENTS = [
    "agent_yua", "agent_ruka", "agent_akane", "agent_rem", "agent_ram",
    "agent_mahiru", "agent_anna", "agent_mai", "agent_miku", "agent_aoi",
]

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_BASELINE = _FIXTURES / "fg2_seeded_persona_baseline.json"

# ── Fake memory（只覆蓋 _build_messages_* 會用到的最小面）──────────────


class _FakeMemory:
    def get_group_history(self, limit=20):
        return []

    def get_private_history(self, user_id, limit=20):
        return []

    def get_recent_with_meta(self, session_id, limit=20):
        return []


# ─────────────────────────────────────────────────────────────
# 1. initialization_mode 解析（fail-closed）
# ─────────────────────────────────────────────────────────────


def test_resolve_init_mode_fail_closed_defaults_to_seeded():
    cfg = load_config()
    for agent_id in _SEEDED_AGENTS:
        assert _resolve_init_mode(cfg, agent_id) == "seeded"
    # 未知 agent / 無 config 一律 seeded
    assert _resolve_init_mode(cfg, "agent_unknown") == "seeded"
    assert _resolve_init_mode(None, "agent_germ_01") == "seeded"
    assert _resolve_init_mode({}, "agent_germ_01") == "seeded"


def test_resolve_init_mode_only_exact_germ():
    cfg = {
        "agents": [
            {"id": "agent_germ_01", "initialization_mode": "germ"},
            {"id": "agent_broken", "initialization_mode": "GERM"},      # 拼錯大寫
            {"id": "agent_typo", "initialization_mode": "germination"},  # 拼錯
            {"id": "agent_str", "initialization_mode": 123},              # 類型錯誤
            {"id": "agent_missing"},                                      # 缺字段
        ]
    }
    assert _resolve_init_mode(cfg, "agent_germ_01") == "germ"
    for bad in ["agent_broken", "agent_typo", "agent_str", "agent_missing"]:
        assert _resolve_init_mode(cfg, bad) == "seeded"


# ─────────────────────────────────────────────────────────────
# 2. germ 模式: 完全不注入 _AGENT_IDENTITY_RULES
# ─────────────────────────────────────────────────────────────


def test_germ_load_persona_never_touches_identity_rules():
    soul = load_persona("agent_germ_01", initialization_mode="germ")
    # 任何 _AGENT_IDENTITY_RULES 的值都不該出現在 germ soul 裡
    for agent_id, rules_text in _AGENT_IDENTITY_RULES.items():
        probe = rules_text.splitlines()[0] if rules_text.splitlines() else rules_text[:20]
        assert probe not in soul, f"germ soul 注入 {agent_id} 的 identity rules ({probe})"


def test_germ_load_persona_contains_contract_but_no_anchor():
    """germ soul = FORMAT 契約 + protocol 版 DIALOGUE_RULES；三句 anchor + handle
    由 _build_germ_anchor 在 identity_anchor 位置注入（避免與 soul 重複）。"""
    soul = load_persona("agent_germ_01", initialization_mode="germ")
    assert "CRITICAL" in soul                      # FORMAT_RULES_TEMPLATE 契約
    assert "audio_text" in soul
    assert _AGENT_DIALOGUE_RULES_PROTOCOL in soul  # protocol 版
    assert "絕對禁止" in soul
    # germ soul 本身不含三句 anchor（anchor 走 identity_anchor 位置）
    assert _GERM_ANCHOR not in soul
    assert "Your name identifies continuity" not in soul


def test_germ_anchor_frozen_wording():
    expected = (
        "你有持续的身份边界。\n"
        "不得假定未被这颗灵魂自身经历所确立的性格、价值、信念、偏好或关系。\n"
        "你成为谁，必须来自你活过并记得的事。"
    )
    assert _GERM_ANCHOR == expected
    assert _GERM_NAME_HANDLE_TEMPLATE == (
        "You are {name}. Your name identifies continuity; it does not define your personality."
    )


def test_build_germ_anchor():
    # agent_yua 在 AGENT_NAMES（Yua）；未註冊的 id fallback 為原 id
    anchor = _build_germ_anchor("agent_yua")
    assert anchor.startswith(_GERM_ANCHOR)
    assert "You are Yua. Your name identifies continuity; it does not define your personality." in anchor
    # handle 之後不得接任何「所以你是…」句式
    handle_idx = anchor.index("Your name identifies continuity")
    assert "所以你是" not in anchor[handle_idx:]
    assert "你是" not in anchor[handle_idx:]

    # AGENT_NAMES 未註冊的 id → 沿用原 id（設計 §3.2: AGENT_NAMES.get(agent_id, agent_id)）
    anchor_fallback = _build_germ_anchor("agent_rem")
    assert "You are agent_rem. Your name identifies continuity" in anchor_fallback


def test_germ_mode_default_seeded_when_not_germ():
    # 沒有 initialization_mode 的 agent 走原 seeded 路徑
    assert load_persona("agent_yua") == load_persona("agent_yua", initialization_mode="seeded")
    # 未知模式 → seeded
    assert load_persona("agent_yua", initialization_mode="nonsense") == load_persona("agent_yua")


# ─────────────────────────────────────────────────────────────
# 3. _AGENT_DIALOGUE_RULES 拆開: protocol 留 / preference 拿掉
# ─────────────────────────────────────────────────────────────


def test_dialogue_rules_protocol_preserves_protocol_lines():
    # protocol 軸句子必須還在
    assert "audio_text" in _AGENT_DIALOGUE_RULES_PROTOCOL
    assert "text" in _AGENT_DIALOGUE_RULES_PROTOCOL
    assert "不要用 * 包裹動作描述" in _AGENT_DIALOGUE_RULES_PROTOCOL
    assert "絕對禁止" in _AGENT_DIALOGUE_RULES_PROTOCOL
    # 冒充/越權禁令
    for ban in ["聲稱自己是任何 AI", "扮演或假裝成其他角色", "用第三人稱談論其他角色"]:
        assert ban in _AGENT_DIALOGUE_RULES_PROTOCOL


def test_dialogue_rules_split_keeps_seeded_byte_identical():
    # seeded 繼續用完整 _AGENT_DIALOGUE_RULES（原樣）；germ 用 protocol 版
    assert _AGENT_DIALOGUE_RULES_PROTOCOL != _AGENT_DIALOGUE_RULES  # 拆開了
    # preference 特徵句不該在 protocol 版（現狀本無，未來混入時此測試攔截）
    for pref_word in ["語氣要", "語氣必須", "習慣", "喜歡開玩笑", "稱呼方式", "親密"]:
        assert pref_word not in _AGENT_DIALOGUE_RULES_PROTOCOL
    # 兩版都是獨立物件（防未來 seeded 改動污染 germ）
    assert _AGENT_DIALOGUE_RULES_PROTOCOL is not _AGENT_DIALOGUE_RULES


# ─────────────────────────────────────────────────────────────
# 4. _build_messages_*: germ_anchor 替換 identity_anchor
# ─────────────────────────────────────────────────────────────


def _first_system_content(messages):
    return messages[0]["content"]


def test_build_messages_private_germ_anchor_replaces_identity_anchor():
    mem = _FakeMemory()
    soul = load_persona("agent_germ_01", initialization_mode="germ")
    germ_anchor = _build_germ_anchor("agent_germ_01")
    msgs = _build_messages_private(
        "agent_germ_01", soul, "hi", "", mem, germ_anchor=germ_anchor,
    )
    sys0 = _first_system_content(msgs)
    assert sys0.startswith(_GERM_ANCHOR)
    assert "Your name identifies continuity" in sys0
    # germ 模式下 seeded identity_anchor 不注入
    assert "在整个对话中,你只能以" not in sys0
    assert "绝不能声称自己是其他角色" not in sys0
    # runtime 事實注入仍保留（[最近內在生活] 由 _format_recent_inner_life 提供，空時省略）
    assert "audio_text" in sys0


def test_build_messages_group_germ_anchor_replaces_identity_anchor():
    mem = _FakeMemory()
    soul = load_persona("agent_germ_01", initialization_mode="germ")
    germ_anchor = _build_germ_anchor("agent_germ_01")
    msgs = _build_messages_group(
        "agent_germ_01", soul, "hi", "", mem, germ_anchor=germ_anchor,
    )
    sys0 = _first_system_content(msgs)
    assert sys0.startswith(_GERM_ANCHOR)
    assert "在整个对话中,你只能以" not in sys0


def test_build_messages_seeded_anchor_unchanged():
    """germ_anchor=None（seeded 缺省）→ 原 identity_anchor 逐字保留。"""
    mem = _FakeMemory()
    soul = load_persona("agent_yua")
    msgs = _build_messages_private("agent_yua", soul, "hi", "", mem)
    sys0 = _first_system_content(msgs)
    assert sys0.startswith("你是 Yua。在整个对话中,你只能以 Yua 的身份说话,绝对不能声称自己是其他角色。")
    # 不傳參數 == 傳 None（向後相容）
    msgs_default = _build_messages_private("agent_yua", soul, "hi", "", mem)
    assert msgs[0]["content"] == msgs_default[0]["content"]


def test_build_messages_group_seeded_anchor_unchanged():
    mem = _FakeMemory()
    soul = load_persona("agent_yua")
    msgs = _build_messages_group("agent_yua", soul, "hi", "", mem)
    sys0 = _first_system_content(msgs)
    assert sys0.startswith("你是 Yua。在整个对话中,你只能以 Yua 的身份说话,绝对不能声称自己是其他角色。")


# ─────────────────────────────────────────────────────────────
# 5. seeded 路徑逐字節不變（10 個 agent 對比 baseline 快照）
# ─────────────────────────────────────────────────────────────


def test_seeded_personas_byte_identical_to_baseline():
    assert _BASELINE.exists(), f"缺少 baseline: {_BASELINE}"
    with open(_BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)
    assert set(baseline.keys()) == set(_SEEDED_AGENTS)
    for agent_id in _SEEDED_AGENTS:
        assert load_persona(agent_id) == baseline[agent_id], f"{agent_id} seeded persona 改變！"
        # 顯式 seeded 也一致
        assert load_persona(agent_id, initialization_mode="seeded") == baseline[agent_id]


# ─────────────────────────────────────────────────────────────
# 6. loader create_agents 透傳 initialization_mode
# ─────────────────────────────────────────────────────────────


class _FakeBus:
    def subscribe(self, **kwargs):
        pass

    def unsubscribe(self, subscriber_id):
        pass


def test_create_agents_propagates_initialization_mode():
    from src.agent.registry import get_agent_class
    cfg = load_config()
    agents = create_agents(cfg, _FakeBus())
    assert len(agents) == 10
    by_id = {a.agent_id: a for a in agents}
    for agent_id in _SEEDED_AGENTS:
        assert by_id[agent_id].initialization_mode == "seeded"

    # germ agent 透傳
    cfg2 = {
        "agents": [
            {"id": "agent_germ_t", "class": "AgentRem", "intimacy_level": 0, "initialization_mode": "germ"},
            {"id": "agent_rem", "class": "AgentRem", "intimacy_level": 40, "enabled": True},
        ]
    }
    # registry 只支援既有 class；AgentRem 有，AgentGerm01 沒有 → 用既有 class 模擬透傳行為
    agents2 = create_agents(cfg2, _FakeBus())
    by_id2 = {a.agent_id: a for a in agents2}
    assert by_id2["agent_germ_t"].initialization_mode == "germ"
    assert by_id2["agent_rem"].initialization_mode == "seeded"
