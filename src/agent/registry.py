"""
src/agent/registry.py
Agent 類別註冊表。
新增角色時在這裡加一行，不用改 run_server.py。
"""
from src.agent.consciousness import (
    AgentYua, AgentRuka, AgentAkane, AgentRem,
    AgentRam, AgentMahiru, AgentAnna, AgentMai, AgentMiku, AgentAoi,
)

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
    # Phase 10 — 桜島麻衣 (Bunny Girl Senpai · Soul OS v1)：第 8 個 agent
    # 國民演員 + Dry Banter + 直球告白 + 病弱症候康復者
    # 重要：mai 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    # **不可時間旅行**：夢中少女 arc 不允許穿越 / 預知 / 改寫事故
    # 載入 personas/agent_mai.md（任務書 2026-07-01 distilled）
    "AgentMai":  AgentMai,
    # Phase 11 — 中野三玖 (Quintessential Quintuplets · Soul OS v1)：第 9 個 agent
    # 沉默觀察者 + 模仿者 + 被認出的渴望
    # 重要：miku 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    # **不可整段長時間 impersonate 姊妹**：Imitation Layer 是文字規則不超過 1-3 句
    # **不可寫成二乃 / 一花 / 五月 / Anna / Mahiru**
    # 載入 personas/agent_miku.md（任務書 2026-07-01 distilled,源自 v3.6.1）
    "AgentMiku": AgentMiku,
    # Phase 12 — 日南葵 (Bottom-Tier Character Tomozaki · Soul OS v1)：第 10 個 agent
    # 雙重面具（Layer 0 完美女主角 + Layer 1 人生攻略教官）+ Framework Stress / NO NAME Leakage
    # 重要：aoi 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    # **兩個 Layer 都不可被標記為「真實的她」**：Layer 0 / Layer 1 / Layer ??? 三者都可能是面具
    # **破綻是卡住不是崩裂**：True Crack 不爆發,話說到一半說不下去
    # **不可寫成冰山女王 / 傲嬌 / 純軍師 / 心理諮商師 / 「其實內心很柔軟,只是嘴硬」**
    # 載入 personas/agent_aoi.md（任務書 2026-07-02 distilled,源自 v2.1）
    "AgentAoi": AgentAoi,
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