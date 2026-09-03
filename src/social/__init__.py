"""
src/social — SI-2 Social Diffusion (多 Agent 社交擴散)

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03)

三大防線 + 最小 Schema:
  - 防線 3 Identity Firewall (src/social/identity_firewall.py): 身份認知防污染
    硬 gate — 他者事件只能作背景感知, 禁止內化/昇華 (最高優先)。
  - 防線 2 Privacy Visibility Gate (src/social/producer_gate.py): 發布端隱私
    守門 — 1:1 私聊 DM 預設 private, 攔截於廣播總線之外。
  - 防線 1 Ambient Perception Path (src/world/middleware.py 平行訂閱):
    社交事件只進 world_context [社交感知] 區塊, 不觸發 transmit。
  - 最小 Schema (src/social/schema.py + validation.py): SocialWorldEvent +
    薄驗證器 (fail-closed)。

Frozen Contract 邊界 (SI-2.1 §9): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 一律不動; 本模組全部為新文件。
"""
from .schema import (
    SPACE_LOUNGE,
    SPACE_SOUL_WALL,
    VISIBILITY_PUBLIC,
    VISIBILITY_PRIVATE,
    SOCIAL_EVENT_TYPES,
    EXTERNAL_OTHER_ACTION,
    VALID_SPACE_IDS,
    VALID_VISIBILITIES,
    CONTENT_MAX_CHARS,
    SUMMARY_MAX_CHARS,
    SocialWorldEvent,
)
from .validation import (
    SocialWorldEventValidationError,
    validate_social_world_event,
    is_private_on_bus,
)
from .identity_firewall import (
    IdentityVerdict,
    IdentityFirewall,
)
from .producer_gate import (
    ProducerVerdict,
    SocialEventProducerGate,
    PUBLIC_CHANNELS,
    MODE_GROUP,
    MODE_PRIVATE,
)
from .opportunity import (
    SocialOpportunity,
    SocialOpportunityBuffer,
)
from .aggregator import (
    CompactSocialState,
    SocialPerceptionAggregator,
    ANTI_FRAMING_HINT,
)

__all__ = [
    # schema
    "SPACE_LOUNGE",
    "SPACE_SOUL_WALL",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_PRIVATE",
    "SOCIAL_EVENT_TYPES",
    "EXTERNAL_OTHER_ACTION",
    "VALID_SPACE_IDS",
    "VALID_VISIBILITIES",
    "CONTENT_MAX_CHARS",
    "SUMMARY_MAX_CHARS",
    "SocialWorldEvent",
    # validation
    "SocialWorldEventValidationError",
    "validate_social_world_event",
    "is_private_on_bus",
    # identity firewall (防線 3)
    "IdentityVerdict",
    "IdentityFirewall",
    # producer gate (防線 2)
    "ProducerVerdict",
    "SocialEventProducerGate",
    "PUBLIC_CHANNELS",
    "MODE_GROUP",
    "MODE_PRIVATE",
    # SI-3 social opportunity (社交機會)
    "SocialOpportunity",
    "SocialOpportunityBuffer",
    # SI-3 compact aggregator (緊湊社交狀態聚合器)
    "CompactSocialState",
    "SocialPerceptionAggregator",
    "ANTI_FRAMING_HINT",
]
