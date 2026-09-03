"""
tests/test_social_producer_gate.py — SI-2.1 防线 2: Privacy Visibility Gate

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §5)

验收项 (判定表, fail-closed):
  - 1:1 私聊 DM (private) → 拦截于广播总线之外 (不 publish SOCIAL_WORLD_EVENT)
  - 客厅群聊 (lounge, group) → 允许 (visibility=public)
  - 灵魂墙 (soul_wall, group) → 允许 (visibility=public)
  - 显式标记公开 (explicit_public=True) → 允许 (需显式声明, 默认不推断)
  - 无法判定频道性质 (未知 mode / 未知 channel) → fail-closed 拒绝
"""
from __future__ import annotations

from src.social import (
    MODE_GROUP,
    MODE_PRIVATE,
    SPACE_LOUNGE,
    SPACE_SOUL_WALL,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    SocialEventProducerGate,
)


def _gate() -> SocialEventProducerGate:
    return SocialEventProducerGate()


# ───────────────────────────────────────────────────────────
# 1. 1:1 私聊 DM → 拦截 (核心规则: 私密内容默认不扩散)
# ───────────────────────────────────────────────────────────

def test_private_dm_blocked():
    """与 Bryan 的 1:1 私聊 DM (private) → 严格拦截于广播总线之外。"""
    verdict = _gate().evaluate(channel_mode=MODE_PRIVATE, channel="dm")
    assert verdict.allowed is False
    assert verdict.visibility == VISIBILITY_PRIVATE
    assert "私聊" in verdict.reason or "private" in verdict.reason


def test_private_dm_any_channel_blocked():
    """private mode 下无论 channel 名都拦截 (fail-closed)。"""
    for channel in ("dm", "lounge", "soul_wall", "unknown"):
        verdict = _gate().evaluate(channel_mode=MODE_PRIVATE, channel=channel)
        assert verdict.allowed is False, f"private mode channel={channel!r} 应拦截"


# ───────────────────────────────────────────────────────────
# 2. 公共频道 → 允许
# ───────────────────────────────────────────────────────────

def test_lounge_group_allowed():
    """客厅群聊 (lounge, group) → 允许沉淀为社交事件 (visibility=public)。"""
    verdict = _gate().evaluate(channel_mode=MODE_GROUP, channel=SPACE_LOUNGE)
    assert verdict.allowed is True
    assert verdict.visibility == VISIBILITY_PUBLIC


def test_soul_wall_group_allowed():
    """灵魂墙 (soul_wall, group) → 允许沉淀为社交事件 (visibility=public)。"""
    verdict = _gate().evaluate(channel_mode=MODE_GROUP, channel=SPACE_SOUL_WALL)
    assert verdict.allowed is True
    assert verdict.visibility == VISIBILITY_PUBLIC


# ───────────────────────────────────────────────────────────
# 3. 显式标记公开 → 允许 (需显式声明, 默认不推断)
# ───────────────────────────────────────────────────────────

def test_explicit_public_overrides_private():
    """显式 public flag 覆盖 private 拦截 (SI-2.1 §5.1 判定表第 4 行)。"""
    verdict = _gate().evaluate(
        channel_mode=MODE_PRIVATE, channel="dm", explicit_public=True,
    )
    assert verdict.allowed is True
    assert verdict.visibility == VISIBILITY_PUBLIC


def test_explicit_public_group_allowed():
    verdict = _gate().evaluate(
        channel_mode=MODE_GROUP, channel=SPACE_LOUNGE, explicit_public=True,
    )
    assert verdict.allowed is True
    assert verdict.visibility == VISIBILITY_PUBLIC


def test_no_explicit_public_by_default():
    """默认不推断公开: 不传 explicit_public 时 private 仍拦截。"""
    verdict = _gate().evaluate(channel_mode=MODE_PRIVATE, channel="dm")
    assert verdict.allowed is False


# ───────────────────────────────────────────────────────────
# 4. 无法判定频道性质 → fail-closed 拒绝
# ───────────────────────────────────────────────────────────

def test_unknown_mode_rejected():
    """未知 mode → fail-closed 拒绝发布。"""
    verdict = _gate().evaluate(channel_mode="secret_mode", channel=SPACE_LOUNGE)
    assert verdict.allowed is False
    assert verdict.visibility == VISIBILITY_PRIVATE


def test_group_unknown_channel_rejected():
    """group 但 channel 不在公共频道白名单 → fail-closed 拒绝。"""
    verdict = _gate().evaluate(channel_mode=MODE_GROUP, channel="secret_room")
    assert verdict.allowed is False
    assert verdict.visibility == VISIBILITY_PRIVATE


def test_empty_mode_rejected():
    verdict = _gate().evaluate(channel_mode="", channel=SPACE_LOUNGE)
    assert verdict.allowed is False


# ───────────────────────────────────────────────────────────
# 5. 防线 2 与防线 3 正交 (SI-2.1 §5.3)
# ───────────────────────────────────────────────────────────

def test_gate_is_producer_side_only():
    """ProducerGate 只判定发布端 (allowed/visibility), 不涉及内化判定。"""
    verdict = _gate().evaluate(channel_mode=MODE_GROUP, channel=SPACE_LOUNGE)
    assert verdict.allowed is True
    # 发布端允许 ≠ 消费端可内化 — 防线 3 由 IdentityFirewall 独立负责
    from src.social import IdentityFirewall
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    assert fw.verify_internalizable("agent_miku") is False
