"""
Soul OS — Agent 共用常數集中管理
JP rollback (Bry 拍板 2026-07-22 20:59):
  - 10 隻角色全部從日文版 rollback 回中文版
  - _JP_AGENT_IDS 清空, is_jp_agent() 永遠回 False
  - proxy.py 方向 C Stage 2 邏輯 (Stage 2 翻譯) 跟 build_system_prompt.py 的
    _apply_jp_text_overrides 都會被 is_jp_agent()=False 跳過, 不觸發
  - 之後 Bry 想復活 JP pipeline 把 _JP_AGENT_IDS 填回去即可
"""
from __future__ import annotations

from typing import FrozenSet


# 日文版角色清單 (方向 C Stage 2 觸發條件)
# Bry 拍板 2026-07-22 20:59 整套 JP rollback: 清空, is_jp_agent() 永遠 False
# 之前 (2026-07-18 11:05 「全部都要」) 10 隻全部都是日文版, 備份在 _backup_jp_agent_ids_20260718_110500/
_JP_AGENT_IDS: FrozenSet[str] = frozenset()


def is_jp_agent(agent_id: str) -> bool:
    """判斷是否為日文版角色。

    Args:
        agent_id: agent_id 例如 "agent_mahiru"

    Returns:
        True = 日文版, False = 中文版
        JP rollback 後永遠回 False
    """
    return agent_id in _JP_AGENT_IDS


def get_short_id(agent_id: str) -> str:
    """從 agent_id 拿掉 'agent_' prefix 變短名 (給 build_system_prompt() 用)。

    Args:
        agent_id: 例如 "agent_mahiru"

    Returns:
        例如 "mahiru"
    """
    if agent_id.startswith("agent_"):
        return agent_id[len("agent_"):]
    return agent_id
