"""
src/agency/state.py — Soul OS M5.2 Agency State

AgencyState 持有跟 Agency 4 個 stage 相關的最小 deterministic state。
M5.2 不接 scheduler / production, 全部 state 在 AgencyState 內。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class AgencyState:
    """
    M5.2 最小 deterministic state.

    Fields:
      - last_action_at:          上次實際 action 執行的時間 (Stage 1 action cooldown 用)
      - last_decision_at:        上次 decision 計算的時間 (Stage 2 decision cooldown 用)
      - is_dormant:              character 是否 dormant (Stage 1 拒絕)
      - is_busy:                 character 是否 busy (Stage 1 拒絕)
      - action_cooldown_seconds: action 後 minimum idle 時間 (Stage 1 用)
      - decision_cooldown_seconds: decision 計算 minimum 間隔 (Stage 2 用)

    M5.1 contract 對齊:
      - 全 deterministic, 沒有 random / external call
      - 不存敏感個資 (character_id 在 AgencyRunResult 而非 state)
      - 不存 perception (perception 是 Agency 輸入, 不是 state)
    """
    last_action_at: Optional[datetime] = None
    last_decision_at: Optional[datetime] = None
    is_dormant: bool = False
    is_busy: bool = False
    action_cooldown_seconds: int = 60
    decision_cooldown_seconds: int = 30
