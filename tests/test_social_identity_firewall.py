"""
tests/test_social_identity_firewall.py — SI-2.1 防线 3: Identity Firewall

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §4)

验收项 (绝对不变量):
  - classify: SELF_ACTION / EXTERNAL_OTHER_ACTION / SYSTEM_ACTION
  - verify_internalizable: 他者 → False (fail-closed 拒绝内化/升华);
    自己 → True; 系统事件 (None) → True (维持现状)
  - 红线: 他者事件不 consume / 不 elevate / 不写 SAGE (由 SubmissionGate
    第 6 步 + 本 firewall 的 verify_internalizable=False 保证)
"""
from __future__ import annotations

import pytest

from src.social import (
    EXTERNAL_OTHER_ACTION,
    IdentityFirewall,
    IdentityVerdict,
)


# ───────────────────────────────────────────────────────────
# 1. classify
# ───────────────────────────────────────────────────────────

def test_classify_self_action():
    """actor_id == current_agent_id → SELF_ACTION (自己, 正常内化路径)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.classify("agent_ruka") == IdentityVerdict.SELF_ACTION


def test_classify_external_other_action():
    """actor_id != current_agent_id → EXTERNAL_OTHER_ACTION (他者)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.classify("agent_miku") == IdentityVerdict.EXTERNAL_OTHER_ACTION


def test_classify_system_action():
    """actor_id is None → SYSTEM_ACTION (系统事件, 维持现状)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.classify(None) == IdentityVerdict.SYSTEM_ACTION


def test_classify_non_str_actor_fail_closed():
    """非 str actor_id (身份不明) → 视为他者 (fail-closed, 不内化)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.classify(12345) == IdentityVerdict.EXTERNAL_OTHER_ACTION


def test_classify_empty_string_is_other():
    """空字符串 actor_id 不是自己 → 他者 (fail-closed)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.classify("") == IdentityVerdict.EXTERNAL_OTHER_ACTION


def test_current_agent_id_required():
    """current_agent_id 必填且为非空 str。"""
    with pytest.raises(ValueError):
        IdentityFirewall(current_agent_id="")
    with pytest.raises(ValueError):
        IdentityFirewall(current_agent_id=None)  # type: ignore[arg-type]


# ───────────────────────────────────────────────────────────
# 2. verify_internalizable (绝对不变量)
# ───────────────────────────────────────────────────────────

def test_self_action_internalizable():
    """不变量 3: 自己经历 → 允许正常内化路径。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.verify_internalizable("agent_ruka") is True


def test_external_other_action_not_internalizable():
    """不变量 1+2: 他者事件 → fail-closed 拒绝内化/升华。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.verify_internalizable("agent_miku") is False


def test_system_action_internalizable():
    """不变量 3: 系统事件 (None) → 维持现状 (既有 world:* 路径不受影响)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.verify_internalizable(None) is True


# ───────────────────────────────────────────────────────────
# 3. tag (observability)
# ───────────────────────────────────────────────────────────

def test_tag_external_other_action():
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.tag("agent_miku") == EXTERNAL_OTHER_ACTION


def test_tag_none_for_self_and_system():
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.tag("agent_ruka") is None
    assert fw.tag(None) is None


# ───────────────────────────────────────────────────────────
# 4. 红线语义 (SI-2.1 §4.4 禁止路径)
# ───────────────────────────────────────────────────────────

def test_external_other_never_internalizable_any_actor():
    """红线: 任何他者 actor_id 都不可内化 (不 consume / 不 elevate / 不写 SAGE)。"""
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    for other in ("agent_miku", "agent_yua", "agent_rem", "bryan", "system_x"):
        assert fw.verify_internalizable(other) is False, f"他者 {other!r} 应被拒绝"


def test_identity_verdict_enum_values():
    """IdentityVerdict 枚举值对齐契约。"""
    assert IdentityVerdict.SELF_ACTION.value == "self_action"
    assert IdentityVerdict.EXTERNAL_OTHER_ACTION.value == EXTERNAL_OTHER_ACTION
    assert IdentityVerdict.SYSTEM_ACTION.value == "system_action"
