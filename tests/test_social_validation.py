"""
tests/test_social_validation.py — SI-2.1 Social Diffusion Contract: 薄验证器

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §3.4 / §6.2)

验收项 (fail-closed):
  - 必填字段缺失 → 拒绝
  - space_id 白名单 (lounge | soul_wall), 未知值拒绝
  - visibility 白名单 (public | private)
  - event_type v1 白名单, 未知值拒绝
  - content <= 200 / summary <= 500
  - ts ISO 8601 UTC (复用 _validate_timestamp 规则)
  - novelty_id 格式 (复用 _NOVELTY_ID_RE 规则)
  - priority 必须 int (拒绝 str/float/bool)
  - is_private_on_bus 契约违例检测
"""
from __future__ import annotations

import pytest

from src.social import (
    SPACE_LOUNGE,
    SPACE_SOUL_WALL,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    SocialWorldEventValidationError,
    is_private_on_bus,
    validate_social_world_event,
)


def _valid_payload(**overrides) -> dict:
    base = {
        "actor_id": "agent_miku",
        "space_id": SPACE_LOUNGE,
        "visibility": VISIBILITY_PUBLIC,
        "event_type": "greeting",
        "content": "大家好",
        "novelty_id": "social_greeting_miku_001",
        "ts": "2026-09-03T00:00:00Z",
        "summary": "agent_miku 向大家打了招呼",
    }
    base.update(overrides)
    return base


# ───────────────────────────────────────────────────────────
# 1. 合法 payload 通过
# ───────────────────────────────────────────────────────────

def test_valid_payload_accepted():
    ev = validate_social_world_event(_valid_payload())
    assert ev.actor_id == "agent_miku"
    assert ev.space_id == SPACE_LOUNGE
    assert ev.visibility == VISIBILITY_PUBLIC
    assert ev.event_type == "greeting"
    assert ev.priority == 0  # 默认 0


def test_valid_payload_priority_explicit():
    ev = validate_social_world_event(_valid_payload(priority=3))
    assert ev.priority == 3


def test_valid_payload_soul_wall():
    ev = validate_social_world_event(_valid_payload(space_id=SPACE_SOUL_WALL))
    assert ev.space_id == SPACE_SOUL_WALL


def test_valid_payload_all_event_types():
    for et in ("greeting", "share", "reply", "mood", "activity"):
        ev = validate_social_world_event(_valid_payload(event_type=et))
        assert ev.event_type == et


# ───────────────────────────────────────────────────────────
# 2. 必填字段 (fail-closed)
# ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("missing", [
    "actor_id", "space_id", "visibility", "event_type",
    "content", "novelty_id", "ts", "summary",
])
def test_missing_required_field_rejected(missing):
    payload = _valid_payload()
    del payload[missing]
    with pytest.raises(SocialWorldEventValidationError):
        validate_social_world_event(payload)


def test_non_dict_payload_rejected():
    with pytest.raises(SocialWorldEventValidationError):
        validate_social_world_event("not-a-dict")


# ───────────────────────────────────────────────────────────
# 3. space_id 白名单 (未知值 fail-closed)
# ───────────────────────────────────────────────────────────

def test_unknown_space_id_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="space_id"):
        validate_social_world_event(_valid_payload(space_id="secret_room"))


# ───────────────────────────────────────────────────────────
# 4. visibility 白名单
# ───────────────────────────────────────────────────────────

def test_unknown_visibility_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="visibility"):
        validate_social_world_event(_valid_payload(visibility="secret"))


def test_private_visibility_valid_at_validation():
    """validation 层允许 private (防线 2 在 producer 侧拦截; bus 上出现 private
    由 middleware 的 is_private_on_bus 契约检查丢弃)。"""
    ev = validate_social_world_event(_valid_payload(visibility=VISIBILITY_PRIVATE))
    assert ev.visibility == VISIBILITY_PRIVATE


# ───────────────────────────────────────────────────────────
# 5. event_type v1 白名单 (未知值 fail-closed)
# ───────────────────────────────────────────────────────────

def test_unknown_event_type_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="event_type"):
        validate_social_world_event(_valid_payload(event_type="hack"))


# ───────────────────────────────────────────────────────────
# 6. content / summary 长度上限
# ───────────────────────────────────────────────────────────

def test_content_too_long_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="content"):
        validate_social_world_event(_valid_payload(content="x" * 201))


def test_content_200_ok():
    ev = validate_social_world_event(_valid_payload(content="x" * 200))
    assert len(ev.content) == 200


def test_summary_too_long_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="summary"):
        validate_social_world_event(_valid_payload(summary="x" * 501))


def test_summary_500_ok():
    ev = validate_social_world_event(_valid_payload(summary="x" * 500))
    assert len(ev.summary) == 500


# ───────────────────────────────────────────────────────────
# 7. ts ISO 8601 UTC (复用 _validate_timestamp 规则)
# ───────────────────────────────────────────────────────────

def test_ts_missing_timezone_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="ts"):
        validate_social_world_event(_valid_payload(ts="2026-09-03T00:00:00"))


def test_ts_non_utc_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="ts"):
        validate_social_world_event(_valid_payload(ts="2026-09-03T08:00:00+08:00"))


def test_ts_invalid_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="ts"):
        validate_social_world_event(_valid_payload(ts="not-a-timestamp"))


# ───────────────────────────────────────────────────────────
# 8. novelty_id 格式 (复用 _NOVELTY_ID_RE 规则)
# ───────────────────────────────────────────────────────────

def test_novelty_id_invalid_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="novelty_id"):
        validate_social_world_event(_valid_payload(novelty_id="ab"))  # 太短


def test_novelty_id_bad_chars_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="novelty_id"):
        validate_social_world_event(_valid_payload(novelty_id="Social-Event!"))


def test_novelty_id_normalized():
    ev = validate_social_world_event(_valid_payload(novelty_id="SOCIAL_GREETING_001"))
    assert ev.novelty_id == "social_greeting_001"  # lowercase normalized


# ───────────────────────────────────────────────────────────
# 9. priority 必须 int (拒绝 str/float/bool)
# ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["5", 5.0, True, None, [1]])
def test_priority_non_int_rejected(bad):
    with pytest.raises(SocialWorldEventValidationError, match="priority"):
        validate_social_world_event(_valid_payload(priority=bad))


# ───────────────────────────────────────────────────────────
# 10. data 必须 dict
# ───────────────────────────────────────────────────────────

def test_data_non_dict_rejected():
    with pytest.raises(SocialWorldEventValidationError, match="data"):
        validate_social_world_event(_valid_payload(data="nope"))


# ───────────────────────────────────────────────────────────
# 11. is_private_on_bus (契约违例检测)
# ───────────────────────────────────────────────────────────

def test_is_private_on_bus_true_for_private():
    """SI-2.1 §3.4: visibility=private 出现在 bus 上 = 契约违例。"""
    assert is_private_on_bus(_valid_payload(visibility=VISIBILITY_PRIVATE)) is True


def test_is_private_on_bus_false_for_public():
    assert is_private_on_bus(_valid_payload(visibility=VISIBILITY_PUBLIC)) is False


def test_is_private_on_bus_false_when_missing():
    payload = _valid_payload()
    del payload["visibility"]
    assert is_private_on_bus(payload) is False
