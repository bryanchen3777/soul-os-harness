"""
src/social/producer_gate.py — SI-2.1 防線 2: Privacy Visibility Gate

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §5)

Producer 側守門: 什麼內容能上廣播總線 (私密 vs 公共)。

  核心規則 (SI-2.1 §5.1): 私密內容預設不擴散。
    - 與 Bryan 的 1:1 私聊 DM 是靈魂與 Owner 的私密空間, 預設 private,
      嚴格攔截於廣播總線之外 (不 publish SOCIAL_WORLD_EVENT)。
    - 只有公共頻道 (Soul Wall / 客廳群聊) 或顯式標記公開的動態才允許
      沉澱為社交事件。

  判定表 (fail-closed):
    | 來源頻道            | mode     | 判定                    | 結果 |
    |---------------------|----------|-------------------------|------|
    | 與 Bryan 的 1:1 DM  | private  | 預設 visibility=private | 攔截 |
    | 客廳群聊 (lounge)   | group    | visibility=public       | 允許 |
    | 靈魂牆 (soul_wall)  | group    | visibility=public       | 允許 |
    | 顯式標記公開的動態  | 任意     | 顯式 public flag        | 允許 |
    | 無法判定頻道性質    | 未知     | fail-closed             | 拒絕 |

  對齊既有 I/O 層 (0 改動): io/gateway.py 已有 mode="group"/"private" 雙模式 +
  is_private 標記; ProducerGate 直接消費這些既有信號, 不另建頻道模型。

  與防線 3 的關係 (SI-2.1 §5.3): 防線 2 管發布端 (隱私洩漏), 防線 3 管消費端
  (身份污染)。兩道防線正交, 缺一不可。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from .schema import (
    SPACE_LOUNGE,
    SPACE_SOUL_WALL,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
)

logger = logging.getLogger("soul_os.social.producer_gate")

# 公共頻道白名單 (SI-2.1 §5.1: 客廳群聊 / 靈魂牆)
PUBLIC_CHANNELS = frozenset({SPACE_LOUNGE, SPACE_SOUL_WALL})

# 對齊 io/gateway.py 的既有 mode 信號
MODE_GROUP = "group"
MODE_PRIVATE = "private"


@dataclass(frozen=True)
class ProducerVerdict:
    """防線 2 判定結果 (SI-2.1 §5.2)。

    - allowed=True: 允許沉澱為社交事件, visibility 為發布用可見性 (public)。
    - allowed=False: 攔截於廣播總線之外 (不 publish SOCIAL_WORLD_EVENT)。
    """
    allowed: bool
    visibility: str
    reason: str


class SocialEventProducerGate:
    """
    防線 2: 發布端隱私守門 (SI-2.1 §5.2)。

    用法:
        gate = SocialEventProducerGate()
        verdict = gate.evaluate(channel_mode="group", channel="lounge")
        # → ProducerVerdict(allowed=True, visibility="public", ...)
        verdict = gate.evaluate(channel_mode="private", channel="dm")
        # → ProducerVerdict(allowed=False, visibility="private", ...)
    """

    def evaluate(
        self,
        *,
        channel_mode: str,
        channel: str,
        explicit_public: bool = False,
    ) -> ProducerVerdict:
        """
        判定該頻道動態是否允許沉澱為社交事件 (fail-closed)。

        Args:
            channel_mode: "group" | "private" (對齊 io/gateway.py 既有雙模式;
                未知值 fail-closed 拒絕)。
            channel: "lounge" | "soul_wall" | "dm" (公共頻道白名單;
                未知值 fail-closed 拒絕)。
            explicit_public: 顯式標記公開的動態 (需顯式聲明, 預設不推斷)。
                為 True 時覆蓋 private 攔截 (SI-2.1 §5.1 判定表第 4 行)。

        Returns:
            ProducerVerdict (allowed + 發布用 visibility + reason)。
        """
        # 1. 顯式標記公開 → 允許 (需顯式聲明, 預設不推斷)
        if explicit_public:
            return ProducerVerdict(
                allowed=True,
                visibility=VISIBILITY_PUBLIC,
                reason=(
                    f"explicit_public=True (channel_mode={channel_mode!r}, "
                    f"channel={channel!r}) — 顯式公開, 允許沉澱"
                ),
            )

        # 2. 無法判定頻道性質 → fail-closed 拒絕
        if channel_mode not in (MODE_GROUP, MODE_PRIVATE):
            return ProducerVerdict(
                allowed=False,
                visibility=VISIBILITY_PRIVATE,
                reason=(
                    f"channel_mode {channel_mode!r} 無法判定 (未知 mode) — "
                    f"fail-closed 拒絕發布"
                ),
            )

        # 3. 1:1 私聊 DM (private) → 預設 private, 嚴格攔截於廣播總線之外
        if channel_mode == MODE_PRIVATE:
            return ProducerVerdict(
                allowed=False,
                visibility=VISIBILITY_PRIVATE,
                reason=(
                    f"channel_mode=private (channel={channel!r}) — 1:1 私聊預設 "
                    f"private, 攔截於廣播總線之外 (私密內容預設不擴散)"
                ),
            )

        # 4. 公共頻道 (group) → 允許, 但 channel 必須在白名單
        if channel in PUBLIC_CHANNELS:
            return ProducerVerdict(
                allowed=True,
                visibility=VISIBILITY_PUBLIC,
                reason=(
                    f"channel_mode=group, channel={channel!r} — 公共頻道, "
                    f"允許沉澱為社交事件"
                ),
            )

        # 5. group 但 channel 不在白名單 → fail-closed 拒絕
        return ProducerVerdict(
            allowed=False,
            visibility=VISIBILITY_PRIVATE,
            reason=(
                f"channel_mode=group 但 channel={channel!r} 不在公共頻道白名單 "
                f"{sorted(PUBLIC_CHANNELS)} — fail-closed 拒絕發布"
            ),
        )


__all__ = [
    "ProducerVerdict",
    "SocialEventProducerGate",
    "PUBLIC_CHANNELS",
    "MODE_GROUP",
    "MODE_PRIVATE",
]
