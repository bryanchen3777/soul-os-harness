"""
src/social/identity_firewall.py — SI-2.1 防線 3: Identity Firewall

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §4)

身份認知防污染硬 gate (最高優先, 不可妥協):

  絕對不變量 (SI-2.1 §4.1):
    不變量 1: 外部他者事件 (actor_id != current_agent_id) 只能作為「客廳環境背景
              感知」(Ambient Perception, 經 World Context 注入), 絕對禁止被靈魂
              內化為自身情景記憶 (不 consume 進 soul-elevation pattern, 不寫 SAGE)。
    不變量 2: 外部他者事件更嚴禁昇華 (elevate) 為自身性格或信念
              (不產 belief / value / trait / essence 節點)。
    不變量 3: actor_id == current_agent_id (自己經歷) 才允許走正常內化路徑;
              actor_id is None (系統事件) 維持現狀 (既有 world:* 路徑)。

  標籤 (SI-2.1 §4.2): EXTERNAL_OTHER_ACTION = "external_other_action"
    - 打在 SubmissionVerdict.reason / trace / observability 記錄
    - 語意: 該事件是「外部他者的行為」, 不是本靈魂的經歷

  本組件獨立可測, 不依賴 inner_life (SubmissionGate 在 verify() 第 6 步注入調用)。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from .schema import EXTERNAL_OTHER_ACTION

logger = logging.getLogger("soul_os.social.identity_firewall")


class IdentityVerdict(str, Enum):
    """身份判定結果 (SI-2.1 §4.3)。"""

    SELF_ACTION = "self_action"                    # actor_id == current_agent_id (自己)
    EXTERNAL_OTHER_ACTION = EXTERNAL_OTHER_ACTION  # actor_id != current_agent_id (他者)
    SYSTEM_ACTION = "system_action"                # actor_id is None (系統事件)


class IdentityFirewall:
    """
    防線 3: 身份認知防污染硬 gate (SI-2.1 §4.3)。

    用法:
        firewall = IdentityFirewall(current_agent_id="agent_ruka")
        verdict = firewall.classify("agent_miku")          # EXTERNAL_OTHER_ACTION
        ok = firewall.verify_internalizable("agent_miku")  # False (fail-closed 拒絕)
    """

    def __init__(self, current_agent_id: str) -> None:
        """
        Args:
            current_agent_id: 本靈魂的 agent_id (e.g. "agent_ruka")。
                判定基準: actor_id == current_agent_id → 自己; 否則 → 他者。
        """
        if not isinstance(current_agent_id, str) or not current_agent_id.strip():
            raise ValueError(
                f"current_agent_id 必填且為非空 str, got: {current_agent_id!r}"
            )
        self.current_agent_id = current_agent_id
        logger.info(
            f"[IdentityFirewall] initialized current_agent_id={current_agent_id}"
        )

    def classify(self, actor_id: Optional[str]) -> IdentityVerdict:
        """
        判定 actor_id 的身份歸屬 (SI-2.1 §4.3 第 6 步 a-d):

          - actor_id == current_agent_id  → SELF_ACTION (自己, 正常內化路徑)
          - actor_id != current_agent_id  → EXTERNAL_OTHER_ACTION (他者, fail-closed)
          - actor_id is None              → SYSTEM_ACTION (系統事件, 維持現狀)

        防禦性: 非 str 的 actor_id (e.g. int) 視為他者 (fail-closed, 不內化)。
        """
        if actor_id is None:
            return IdentityVerdict.SYSTEM_ACTION
        if not isinstance(actor_id, str):
            # 非 str 身份不明 → fail-closed 視為他者 (不內化)
            return IdentityVerdict.EXTERNAL_OTHER_ACTION
        if actor_id == self.current_agent_id:
            return IdentityVerdict.SELF_ACTION
        return IdentityVerdict.EXTERNAL_OTHER_ACTION

    def verify_internalizable(self, actor_id: Optional[str]) -> bool:
        """
        該 actor_id 的事件是否允許走內化路徑 (consume / elevate)。

        SI-2.1 §4.3: 只有 SELF_ACTION 可內化; EXTERNAL_OTHER_ACTION 依契約
        fail-closed 拒絕; SYSTEM_ACTION 依不變量 3 維持現狀 (既有 world:* 路徑
        不受影響, 允許)。

        Returns:
            True  → 可內化 (自己 / 系統事件維持現狀)
            False → 禁止內化/昇華 (外部他者, 防線 3 紅線)
        """
        verdict = self.classify(actor_id)
        if verdict == IdentityVerdict.EXTERNAL_OTHER_ACTION:
            logger.warning(
                f"[IdentityFirewall] EXTERNAL_OTHER_ACTION: actor_id={actor_id!r} "
                f"!= current_agent_id={self.current_agent_id!r} — "
                f"他者事件禁止內化/昇華 (防線 3)"
            )
            return False
        return True

    def tag(self, actor_id: Optional[str]) -> Optional[str]:
        """
        回標籤字串 (observability 用): 他者 → EXTERNAL_OTHER_ACTION;
        自己 / 系統 → None (無標籤, 正常路徑)。
        """
        if self.classify(actor_id) == IdentityVerdict.EXTERNAL_OTHER_ACTION:
            return EXTERNAL_OTHER_ACTION
        return None


__all__ = [
    "IdentityVerdict",
    "IdentityFirewall",
    "EXTERNAL_OTHER_ACTION",
]
