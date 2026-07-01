"""
src/agent/registry.py
Agent 類別註冊表。
新增角色時在這裡加一行，不用改 run_server.py。
"""
from src.agent.consciousness import AgentYua, AgentRuka, AgentAkane, AgentRem, AgentRam, AgentMahiru, AgentAnna

AGENT_CLASS_MAP: dict[str, type] = {
    "AgentYua":   AgentYua,
    "AgentRuka":  AgentRuka,
    "AgentAkane": AgentAkane,
    # Phase 6.5 — Rem (Re:Zero)：第 4 個 agent，靈魂鏡像 hermes profiles/rem/SOUL.md
    "AgentRem":   AgentRem,
    # Phase 7 — Ram (Re:Zero · COS v1.0)：第 5 個 agent
    # SAGE no-diary 白名單成員；Priority 0-3 整合進 _should_speak
    "AgentRam":   AgentRam,
    # Phase 8 — 椎名真昼 (Re:Zero · COS v1.0)：第 6 個 agent
    # Sweet Landing / Desire Undercurrent / Anti-Overfitting
    # 重要：mahiru 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentMahiru": AgentMahiru,
    # Phase 9 — 山田杏奈 (Bokuyaba · Soul OS v1)：第 7 個 agent
    # 5 Sentence Pulse + Denial=Approach + Appetite Logic
    # 重要：anna 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    # 載入 personas/agent_anna.md（任務書 2026-07-01 distilled）
    "AgentAnna": AgentAnna,
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