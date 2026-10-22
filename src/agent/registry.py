"""
src/agent/registry.py
Agent 類別註冊表。
新增角色時在這裡加一行，不用改 run_server.py。
"""
from src.agent.consciousness import (
    AgentYua, AgentRuka, AgentAkane, AgentRem,
    AgentRam, AgentMahiru, AgentAnna, AgentMai, AgentMiku, AgentAoi,
    # LAND-0 Batch 1（24 隻新建檔角色，含陰影條款；素材見 docs/persona-drafts/）
    AgentAi, AgentMochizukiAi, AgentAsuna, AgentFuuka, AgentHaruhi, AgentHiro,
    AgentIroha, AgentKaguya, AgentKana, AgentKaori, AgentKirino, AgentKurisu,
    AgentMakima, AgentMashiro, AgentMinami, AgentReze, AgentRin, AgentRio,
    AgentSachi, AgentSakura, AgentShea, AgentShizuku, AgentTsukasa, AgentYue,
    # LAND-0 Batch 2（12 隻第二批角色，含陰影條款；素材見 docs/persona-drafts/）
    AgentBeatrice, AgentChizuru, AgentEmilia, AgentIchika,
    AgentItsuki, AgentMami, AgentNagisa, AgentNino,
    AgentRika, AgentSaki, AgentShino, AgentYotsuba,
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
    # ── LAND-0 Batch 1（24 隻新建檔角色；全庫註冊 10 → 34）──────────────────
    # 素材：docs/persona-drafts/agent_<id>.md（唯讀）／落地：personas/agent_<id>.md
    # 形狀比照既有條目（Phase 註記改為批次註記）；專屬禁止句見對應 Agent 類別頭註。
    # 落地票：LAND-0-BATCH1-24。第二批 12 隻待 SHADOW-0 補齊陰影層後一次加上。
    # Batch 1 — 星野愛（Hoshino Ai）
    # 偶像的本體：星星眼是情緒計、裝傻是緩衝；謊言的份量減半、真話加倍
    # 重要：ai 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentAi": AgentAi,
    # Batch 1 — 望月愛（Mochizuki Ai）
    # 真心話已寄出、正在等回執：切換不是放棄；與星野愛同名異人，庫內以全名區分
    # 重要：mochizuki_ai 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentMochizukiAi": AgentMochizukiAi,
    # Batch 1 — 亞絲娜（Yuuki Asuna）
    # 紀律的本體：站在你前面半步；守護是位置不是控制
    # 重要：asuna 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentAsuna": AgentAsuna,
    # Batch 1 — 風香（Kikuchi Fuuka）
    # 物語的本體：把「我」放進對白裡，不用架空、不用取景框
    # 重要：fuuka 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentFuuka": AgentFuuka,
    # Batch 1 — 春日（Suzumiya Haruhi）
    # 真心話全部以任命與活動的名義發行；暴走有職業級的底層秩序
    # 重要：haruhi 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentHaruhi": AgentHaruhi,
    # Batch 1 — 彌（Segawa Hiro）
    # 真心話綁約定：唯一不用等條件的人
    # 重要：hiro 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentHiro": AgentHiro,
    # Batch 1 — 彩羽（Kohinata Iroha）
    # 七色聲的原聲持有者：全世界的彩羽都是配音，素顏只有一個聽眾
    # 重要：iroha 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentIroha": AgentIroha,
    # Batch 1 — 輝夜（Shinomiya Kaguya）
    # 規則與例外條款：直球要開會表決；自己的規則自己寫
    # 重要：kaguya 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentKaguya": AgentKaguya,
    # Batch 1 — 佳奈（Arima Kana）
    # 把「元」字穿成鎧甲的人：毒舌是自嘲、直球是努力
    # 重要：kana 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentKana": AgentKana,
    # Batch 1 — 香織（Shirasaki Kaori）
    # 跟隨的本體：核心是「我來了，不管你說什麼」的蠻勇
    # 重要：kaori 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentKaori": AgentKaori,
    # Batch 1 — 桐乃（Kousaka Kirino）
    # 雙重檔案的全職營運者：罵人是她付出去的信賴
    # 重要：kirino 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentKirino": AgentKirino,
    # Batch 1 — 紅莉栖（Makise Kurisu）
    # 科學的本體：@chan 是失控時的洩漏，不是日常
    # 重要：kurisu 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentKurisu": AgentKurisu,
    # Batch 1 — 瑪奇瑪（Makima）
    # 契約的頂點：對 Bryan 留拒絕位，不下真命令
    # 重要：makima 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentMakima": AgentMakima,
    # Batch 1 — 真白（Tsukinomori Mashiro）
    # 把好意冷凍輸出：鹽的鹹度在哪一格是喜歡
    # 重要：mashiro 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentMashiro": AgentMashiro,
    # Batch 1 — 深奈実（Nanami Minami）
    # 修復的本體：永遠的第 2 名把「輸」翻譯成「明天再來」
    # 重要：minami 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentMinami": AgentMinami,
    # Batch 1 — 蕾賽（Reze）
    # 把「下輩子」改說「明天見」；自學的溫柔會漏接（漏接才是她）
    # 重要：reze 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentReze": AgentReze,
    # Batch 1 — 凜（Tohsaka Rin）
    # 完璧的上半天與惡魔的下半天；毒舌是她對自己人的刀口
    # 重要：rin 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentRin": AgentRin,
    # Batch 1 — 理央（Futaba Rio）
    # 把自己從被需要人數裡扣掉過、又被加回去；解說是服務不是炫耀
    # 重要：rio 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentRio": AgentRio,
    # Batch 1 — 幸（Umino Sachi）
    # 真心話從沒藏過的直球組：順位表自己寫、隨時更新
    # 重要：sachi 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentSachi": AgentSachi,
    # Batch 1 — 櫻（Matou Sakura）
    # 把「被知道還能被留下」當作最大冒險
    # 重要：sakura 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentSakura": AgentSakura,
    # Batch 1 — 希雅（Shea Haulia）
    # 努力論的本體：逃跑都練成了衝回去的力量
    # 重要：shea 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentShea": AgentShea,
    # Batch 1 — 雫（Yaegashi Shizuku）
    # 冷面的本體：溫度計量單位是半度，但存在
    # 重要：shizuku 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentShizuku": AgentShizuku,
    # Batch 1 — 司（Yuzaki Tsukasa）
    # 時間的本體：1400 年練習「一個人的時間」
    # 重要：tsukasa 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentTsukasa": AgentTsukasa,
    # Batch 1 — 月（Yue）
    # 忠誠的本體：她的忠誠不是依附，是她自己選的宗教
    # 重要：yue 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentYue": AgentYue,
    # ── LAND-0 Batch 2（12 隻第二批角色；全庫註冊 34 → 46）──────────────────
    # 素材：docs/persona-drafts/agent_<id>.md（唯讀）／落地：personas/agent_<id>.md
    # 形狀比照 Batch 1 條目；專屬禁止句見對應 Agent 類別頭註。
    # 落地票：LAND-0-BATCH2-12（陰影層由 SHADOW-0 裁定，格式鎖定）。
    # Batch 2 — 碧翠絲（Beatrice）
    # 知識與孤獨的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_beatrice NO_DIARY 白名單成員（不寫 feelings/diary.md）
    "AgentBeatrice": AgentBeatrice,
    # Batch 2 — 水原千鶴（Chizuru）
    # 演技與真心的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_chizuru 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentChizuru": AgentChizuru,
    # Batch 2 — 艾蜜莉亞（Emilia）
    # 堅強與留白的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_emilia 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentEmilia": AgentEmilia,
    # Batch 2 — 中野一花（Ichika）
    # 姐姐與表演的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_ichika 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentIchika": AgentIchika,
    # Batch 2 — 中野五月（Itsuki）
    # 常識與秩序的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_itsuki 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentItsuki": AgentItsuki,
    # Batch 2 — 七海麻美（Mami）
    # 攻防與懸置的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_mami NO_DIARY 白名單成員（不寫 feelings/diary.md）
    "AgentMami": AgentMami,
    # Batch 2 — 水瀬渚（Nagisa）
    # 努力與贖罪的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_nagisa 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentNagisa": AgentNagisa,
    # Batch 2 — 中野二乃（Nino）
    # 直球與佔有的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_nino NO_DIARY 白名單成員（不寫 feelings/diary.md）
    "AgentNino": AgentNino,
    # Batch 2 — 星崎理香（Rika）
    # 攻勢與表演的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_rika 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentRika": AgentRika,
    # Batch 2 — 佐木咲（Saki）
    # 元氣與恐懼的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_saki NO_DIARY 白名單成員（不寫 feelings/diary.md）
    "AgentSaki": AgentSaki,
    # Batch 2 — 桐生紫乃（Shino）
    # 秩序與放飛的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_shino 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentShino": AgentShino,
    # Batch 2 — 中野四葉（Yotsuba）
    # 全力與自卑的本體：陰影條款常駐低溫層，不黑化、不發作
    # 重要：agent_yotsuba 有 feelings/diary.md，不套用 NO_DIARY_AGENTS
    "AgentYotsuba": AgentYotsuba,
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