"""
src/agent/registry.py
Agent 類別註冊表。
新增角色時在這裡加一行，不用改 run_server.py。
"""
from src.agent.consciousness import AgentYua, AgentRuka, AgentAkane, AgentRem

AGENT_CLASS_MAP: dict[str, type] = {
    "AgentYua":   AgentYua,
    "AgentRuka":  AgentRuka,
    "AgentAkane": AgentAkane,
    # Phase 6.5 — Rem (Re:Zero)：第 4 個 agent，靈魂鏡像 hermes profiles/rem/SOUL.md
    "AgentRem":   AgentRem,
    # "AgentAoi":   AgentAoi,  ← 之後加角色只要在這裡新增
}


def get_agent_class(class_name: str) -> type:
    """
    依 class 名稱取得 Agent 類別。
    未註冊的 class 拋出 ValueError，清楚告知要在哪裡註冊。
    """
    cls = AGENT_CLASS_MAP.get(class_name)
    if cls is None:
        raise ValueError(
            f"未知的 Agent class：{class_name}，"
            f"請在 src/agent/registry.py 的 AGENT_CLASS_MAP 註冊"
        )
    return cls