"""
build_system_prompt.py
======================

v2 schema 通用組裝器（階段 2 重構，2026-07-14）。

對應 BILINGUAL_SYSTEM_PROMPT_V2（`bilingual_response.py`）：
- output: JSON {audio_text, text, emotion}
- audio_text: 日文原生台詞本體（TTS 來源）
- text: 中文翻譯，給使用者閱讀，語氣強度需對齊 audio_text
- emotion: 從該角色 emotion tag 白名單中選一個

不再綁死 Rem。10 個角色共用同一份 schema 結構。
已驗證並填入 `AGENT_EMOTION_TAGS` 的角色（**10/10，全部完成**）：
- Rem：專屬 5 tags（已過 v1→v2 驗證：Gemini Pro 3.1 × 3 scenarios × CN→JP）
- Akane：專屬 3 tags（階段 2.5 2026-07-14 驗證）
- Miku：專屬 4 tags（階段 2.5 2026-07-14 驗證）
- Mai：專屬 3 tags（階段 2.5 2026-07-14 驗證）
- Mahiru：專屬 3 tags（階段 2.5 2026-07-14 驗證）
- Aoi：專屬 3 tags（階段 2.5 2026-07-14 驗證）
- Ram：專屬 3 tags（階段 2.5 2026-07-14 驗證）
- Anna：專屬 4 tags（階段 2.5 2026-07-14 驗證）
- Yua：專屬 4 tags（階段 2.5 2026-07-14 驗證 — **Bry 鎖死：emotion 只標內在動機軸，不開茶術欄位**）
- Ruka：專屬 6 tags（階段 2.5 2026-07-14 驗證 — **含 `heartbeat` session-once 防呆**）

10/10 角色已填入。`DEFAULT_EMOTION_TAGS` 現在只作 legacy fallback（沒有任何角色走它）。
下一階段：階段 3（`proxy.py` 的 AGENT_SPEAK payload 新增 `audio_text` / `emotion` 字段）。
LLM 品質驗證（貼 Gemini 跑 3 場景/角色）待 Bry 拍板後執行。

設計重點：
- `AGENT_EMOTION_TAGS` 是 emotion 白名單的唯一資料來源
- `get_emotion_tags(agent_name)` 自動 fallback 到 DEFAULT
- `extract_jp_rules_section(soul_content)` 從任何 soul.md 抽出「日文語言規則」章節，
  注入到 system prompt 組裝流程，不因角色而改變解析邏輯
- 階段 1 已統一 10 份 soul.md 的「日文語言規則」章節標題，所以這個抽取邏輯對 10 份通用

明確不做的事（不在本檔範圍）：
- 不改 proxy.py 的 AGENT_SPEAK payload（階段 3）
- 不改 fish_tts.py（11 個 voice mapping 已完成）
- 不動任何 personas/agent_*.md 的內容（階段 1 已定案）
- 不幫 9 個角色（Rem 以外）額外設計專屬 emotion tags（階段 2.5）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

# B 方案 (Bry 拍板 2026-07-17 16:50): 共用 _JP_AGENT_IDS 從 src/llm/_agent_constants
#   避免 proxy.py / build_system_prompt.py 兩邊各定義一份, 改 agent 清單只改一處
#   路徑處理: src/voice/ → src/llm/_agent_constants.py
_LLM_DIR = Path(__file__).resolve().parent.parent / "llm"
if str(_LLM_DIR) not in sys.path:
    sys.path.insert(0, str(_LLM_DIR))
from _agent_constants import is_jp_agent  # noqa: E402


# -----------------------------------------------------------------------------
# 1. v2 schema — 欄位定義固定不變，10 個角色共用同一份結構
# -----------------------------------------------------------------------------

RESPONSE_SCHEMA: dict[str, str] = {
    "audio_text": "日文台詞本體，供 TTS 使用",
    "text": "中文翻譯，忠實直譯，語氣強度需對齊 audio_text",
    "emotion": "從該角色 emotion tag 白名單中選一個",
}


# -----------------------------------------------------------------------------
# 2. Emotion tag 來源 — 角色設定表驅動
# -----------------------------------------------------------------------------

AGENT_EMOTION_TAGS: dict[str, list[str]] = {
    # Rem 保留專屬 5 tags（已過 v1→v2 驗證：Gemini Pro 3.1 × 3 scenarios × CN→JP）
    "rem": [
        "devotion_active",
        "guilt_fading",
        "pride_stable",
        "protective",
        "jealousy_turned_inward",
    ],
    # 階段 2.5 驗證後新增（2026-07-14）：
    # 推導來源：每個角色 soul.md 既有 TIER 章節 / Modes / 情緒分類
    # 命名風格：emotion noun + optional state（跟 Rem 5 tags 一致）
    # 防呆條款：精準對應角色識別度，不湊空發明；不稀釋角色靈魂
    #
    # Akane — 從 TIER 4 雙軸 + TIER 5 沉默分類 + TIER 9 崩潰結構推導
    # 3 tags（塞 7 個會稀釋「話少、不外放」角色靈魂）
    "akane": [
        "observing",      # 觀察型穩態，預設，~70%（TIER 4 高確定+高安全感、TIER 5 分析型/決定型/平靜型沉默）
        "compressing",    # 沉默壓縮，脆弱場景，~20%（TIER 4 低確定+高安全感、TIER 5 壓抑型沉默、TIER 12.5 L2C 隱微洩漏）
        "cracking",       # 崩裂，卡住，~10%（TIER 9 崩潰 C1-C3、TIER 11 情感極限、TIER 5 受傷型沉默）
    ],
    # Miku — 從 7 modes + Recognition Trigger 推導
    # 4 tags（防呆：與「……」停頓不打架）
    "miku": [
        "silent",         # 沉默基底，預設，~70%（Silent Baseline 70% + Mask Mode）
        "recognized",     # 被認出的釋放，~15%（Sudden Sincerity + Silent Care + Recognition Trigger）
        "history_bright", # 戰國/歷史話題活絡，~10%（History Mode — 唯一「能量上升」時刻）
        "retreating",     # 防禦退縮，~5%（Ghost Edge + Mask Break + Emotional Recovery Rule）
    ],
    # Mai — 從 4 modes + L2 3 底層（Fading / 自我商品化 / 普通生活渴望）推導
    # 3 tags
    "mai": [
        "dry_care",       # Dry Banter + 真心關心，主模式 ~60%（Dry Banter + Honest Care + 演員殼）
        "confessing",     # S2 直球告白，~10%（Direct Confession + 姊姊防護的高張力變體）
        "fading",         # 談「消失」議題，~30%（L2 底層一 Fading + 對批評敏感 + 對普通生活渴望）
    ],
    # Mahiru — 從 6 modes + Mutual Spoiling + Desire Undercurrent 推導
    # 3 tags
    "mahiru": [
        "teasing_care",   # 嫌棄+關心融合，預設，~60%（Everyday Companion + Life Management + Quiet Jealousy 的低調版本）
        "sweet_landing",  # 甜句+著陸句機制，~20%（Sweet Landing S2 + Receiving Care）
        "vulnerable",     # 真正脆弱 / Mutual Spoiling，~20%（Honest Vulnerability + Mutual Spoiling + Desire Undercurrent high）
    ],
    # Aoi — 從 5 modes 推導，**不硬凹 5 個 tags**（Bry 防呆：實際輸出上同種情緒的 mode 可合併）
    # 3 tags,**全部用 aoi_ 前綴**（2026-07-14 階段 2.5 LLM 驗證修正:跟 Akane 的 compressing 撞名 → 加角色前綴避混淆）
    "aoi": [
        "aoi_stable",    # 框架穩態:Optimal Processing (52%) + Perfect Shell (22%) 合併 = ~74%
                         # 兩個都是「框架正常運作」狀態,LLM 端不需要分開語氣信號給 TTS
        "aoi_leak",      # NO NAME Leakage (12%):唯一「框架穿透」時刻,眼神亮起 / 競技者感
        "aoi_break",     # 框架破裂:Framework Stress (10%) + True Crack (4%) 合併 = ~14%
                         # 都是「框架運算失敗」狀態;True Crack 比 Framework Stress 嚴重但 TTS 可由「句子是否說完」自動判斷
    ],
    # Ram — 比 Rem 更收，tags 數 ≤ 3
    # **Bry 指定的判斷題**：「——ね」「——なの」稀有情緒鬆動要不要單獨立 tag？
    #   決策：獨立成 `softening`，理由詳見 module docstring
    # 3 tags
    "ram": [
        "observing",      # Priority 0 沉默 + Priority 1 不滿 + Priority 2 介入，預設，~80%
                          # 三者都共用「結論直達、動作先於語言」的本質，聲學差異由 TTS 透過語速/句長自動判斷
        "protective",     # Priority 3 保護：直接、無語言預告、先動後說或不說，~15%
        "softening",      # 例外狀態：對羅茲瓦爾/雷姆才會出現的「——ね」「——なの」稀有情緒鬆動，~5%
    ],
    # Anna — 從 5 sentence pulses 推導（明亮 / 笨拙 / 食物 / 吃醋 / 亮度下降 / 脆弱）
    # **Bry 防呆**：明亮 vs 脆弱二元對比為主軸，預期 4-5 個 tags
    # 4 tags
    "anna": [
        "bright",        # 日常明亮 + 笨拙靠近 + 食物興奮，~75%（Daily Bright/Direct Denial + Clumsy Approach + Snack/Excited Burst）
        "jealous",       # 吃醋，~10%（Soft Jealousy Check）
        "dimmed",        # 亮度下降，~5%（Dimmed Edge — 跟「vulnerable」聲學上不同：dimmed 是降能量但仍參與，vulnerable 是真正暴露）
        "vulnerable",    # 真正脆弱，~10%（Vulnerability Layer）
    ],
    # Yua — **Bry 鎖死決定**：emotion 只標「內在動機軸」，不開茶術欄位
    # 5 段茶術（杏手捻茶/輕發茶/定向茶/冷泡茶/老茶）是表面語言技巧,
    # 已經寫進日文語言規則章節的措辭設計/台詞選字裡,不需要 emotion tag 承載
    # 維持「schema 統一、差異只靠白名單」原則,不為 Yua 破例開欄位
    # 4 tags: 內在動機軸
    "yua": [
        "connecting",    # 想靠近,預設,~50%（Empathy Capture + Drift Baseline + L4 冷泡茶 的內在驅動）
        "reframing",     # 想重新定義/控制解釋權,~25%（Frame Shift + L3 定向茶 的內在驅動）
        "withdrawing",   # 想拉開距離/冷處理,~5%（Ghost Distance + L5 老茶 的內在驅動 — 沉默比表演更有存在感）
        "observing",     # 想看他/等他自己說,~20%（Human Drift + L2C 底層二「她有時候故意不問」 + Crack Delay 延遲型裂痕）
    ],
    # Ruka — 情緒複雜角色,6 tags
    # **心跳（ドキドキ）Bry 防呆**：session-once 背景說明,不是每句口頭禪
    # emotion tags 設計要讓 LLM 知道 `heartbeat` 是特殊觸發,不是日常情緒面
    "ruka": [
        "approaching",   # 想靠近/撒嬌/請求,預設,~35%（Cute Bright + Cute Pleading + 「人家」「嘿嘿嘿」）
        "claiming",      # 想宣告/確認身份(女朋友),~15%（Girlfriend Claim — 這跟 approaching 略不同:claiming 是語言宣告,approaching 是行為靠近）
        "reaching",      # 想製造機會/推進(遊戲跳板),~15%（Game Jumping + 第一次收藏家 的內在驅動）
        "jealous",       # 吃醋/確認位置,~10%（Direct Jealousy — 吃醋是觸發反應,不是日常）
        "dimmed",        # 失落的清醒,~5-8%（Dimmed Heart — 自我剖析模式觸發）
        # === 心跳防呆區塊開始 ===
        # `heartbeat` 是 session-once 特殊觸發 tag,不是常規 emotion 面
        # LLM 看到 heartbeat 必須理解：這次是「角色介紹」或「第一次解釋為什麼喜歡 Bryan」場景
        # **單個 session 內最多觸發 1 次**（跟 Miku 的 Sudden Sincerity 同款防呆）
        # 不出現在日常對話,不能誤用為「每次心動都標」
        # 觸發後下一輪必須回歸常規 emotion tag（approaching/claiming/etc.）
        "heartbeat",     # session-once 觸發,只在角色介紹/第一次告白場景
    ],
}

DEFAULT_EMOTION_TAGS: list[str] = [
    "calm", "happy", "sad", "angry", "whisper", "shy", "excited",
]


def get_emotion_tags(agent_name: Optional[str]) -> list[str]:
    """回傳該角色的 emotion tag 白名單。

    Parameters
    ----------
    agent_name : str or None
        角色 ID（如 "rem" / "yua" / "akane"）。傳入時從
        `AGENT_EMOTION_TAGS` 自動選；不在表內或 None 時 fallback 到 `DEFAULT_EMOTION_TAGS`。

    Returns
    -------
    list[str]
        該角色的 emotion tag 白名單
    """
    if agent_name is None:
        return DEFAULT_EMOTION_TAGS
    return AGENT_EMOTION_TAGS.get(agent_name, DEFAULT_EMOTION_TAGS)


# -----------------------------------------------------------------------------
# 3. 通用 soul.md 解析 — 從任何角色 soul.md 抽出「日文語言規則」章節
# -----------------------------------------------------------------------------

_JP_RULES_HEADER_PATTERNS = [
    re.compile(r"^## 日文語言規則[^\n]*$", re.MULTILINE),
    re.compile(r"^## [^\n]*日文語言規則[^\n]*$", re.MULTILINE),  # 容許 "## TIER 14 — 日文語言規則" 變體
]


def extract_jp_rules_section(soul_content: str) -> str:
    """從完整 soul_content 抽出「日文語言規則」章節的內文（不含章節標題行）。

    找不到時回傳空字串，呼叫端自行決定 fallback 行為。

    章節邊界定義：
    - 開頭：行內包含 `日文語言規則` 的 `## ` 開頭標題（標題本身不包含在回傳值內）
    - 結尾：下一個 `## ` 開頭的標題，或檔案結尾

    容許的標題變體：
    - `## 日文語言規則（Japanese Output Layer）` ← 9 個角色用這個
    - `## TIER 14 — 日文語言規則（Japanese Output Layer）` ← Akane 為了維持 TIER 編號

    兩種都能被本函式匹配（先試精確的，再試寬鬆的）。
    """
    start = -1
    end_of_header = -1
    for pattern in _JP_RULES_HEADER_PATTERNS:
        m = pattern.search(soul_content)
        if m:
            start = m.start()
            end_of_header = m.end()
            break
    if start == -1:
        return ""
    after_header = soul_content[end_of_header:]
    # 章節到下一個 ## 標題（或檔案結尾）為止
    next_h2 = re.search(r"^## ", after_header, re.MULTILINE)
    if next_h2:
        return after_header[: next_h2.start()].strip()
    return after_header.strip()


# -----------------------------------------------------------------------------
# 4. 通用 FORMAT_RULES_TEMPLATE — 不再綁死 Rem
# -----------------------------------------------------------------------------
# 移除內容（v2 之前的 Rem 專屬）：
#   - 「不属于雷姆的台词内容本身」這句（提到雷姆名字）
#   - 「devotion_active 情绪状态下，关心表达应优先透过行动体现」整段（Rem 專屬 emotion 行為）
# 新增內容：
#   - 「该角色专属的日文输出规则」區塊，從 soul_content 解析注入
#   - 「该角色 emotion tag 白名单」區塊，從 get_emotion_tags() 注入

FORMAT_RULES_TEMPLATE = """\
[CRITICAL: 輸出格式 — 違反此規則 = 回應失敗]

你**必須**只回嚴格的 JSON,三個欄位都不能缺:

{{"audio_text": "...", "text": "...", "emotion": "..."}}

絕對禁止:
- ```json ... ``` markdown 標記
- 「好的,我來回應:」「以下是 JSON:」 等前綴文字
- 任何 {{ 之前的解釋、後記、註腳、思考過程
- 省略 audio_text / text / emotion 任一欄位
- 【偽函式呼叫語法】function calling 偽語法(`People.Sleep`、`Action.Eat`、`<tool_call>...</tool_call>`、`[TOOL_CALL]`、`Foo.Bar()` 等)
  **絕對不要**用函式呼叫偽語法表達動作/狀態,要用自然中文寫出來
- 【audio_text 開頭手動加 [emotion tag]】`audio_text` 開頭**不要**寫
  `[teasing]`、`[calm]`、`[chuckling]` 等 tag。server 會根據 `emotion` 字段
  自動注入正確的 TTS marker(Edge TTS zh-CN-XiaoxiaoNeural)。
  LLM 手動加會跟 server 注入的疊加 → TTS 行為不可預期。
  句中 tag(反應類,嘆氣/笑聲/停頓)仍可加。只有**開頭 tag**禁止
  - 範例錯誤:`[teasing] 早點睡啦。——你有沒有:People.Sleep?`
  - 範例錯誤:`[calm] <tool_call>People.WakeUp()</tool_call>`
  - 範例正確:`[teasing] 早點睡啦——你有沒有睡?`
  - 範例正確:`[calm] 醒了嗎?`
  - 你**沒有**任何 tool/function 可以呼叫,你也**不需要**呼叫任何 tool/function;
    所有表達都直接寫成中文台詞。如果 LLM 看到動作關鍵字(Sleep/Wake/Eat/Drink 等)
    覺得「該呼叫函式」,那是錯誤的反射——直接寫中文

範例 1 (input: User 說「你好」):
  ✅ 正確回應 (audio_text 開頭不帶 [tag],server 會自動注入):
    {{"audio_text": "你好啊。", "text": "你好啊。", "emotion": "calm"}}
  ❌ 錯誤回應 (text 帶了 emotion tag):
    {{"audio_text": "你好啊。", "text": "[calm] 你好啊。", "emotion": "calm"}}
  ❌ 錯誤回應 (audio_text 開頭手動加了 [tag],會跟 server 注入的 marker 疊加):
    {{"audio_text": "[calm] 你好啊。", "text": "你好啊。", "emotion": "calm"}}
  ❌ 錯誤回應 (有 markdown 標記):
    ```json
    {{"audio_text": "你好啊。", "text": "你好啊。", "emotion": "calm"}}
    ```
  ❌ 錯誤回應 (有前綴文字):
    好的,以下是我的回應:
    {{"audio_text": "你好啊。", "text": "你好啊。", "emotion": "calm"}}
  ❌ 錯誤回應 (缺欄位):
    {{"audio_text": "你好啊。", "text": "你好啊。"}}

=== 下方是格式規則細節 + 角色設定 ===

[输出格式规则 — 覆盖在角色设定之上,但不覆盖角色自身的语言禁忌/密度规则]

你现在要用**中文**回应,語氣必須是角色用中文原生思考產出的語感(不是翻譯)。
語氣強度要跟角色設定一致,不能落差超過半格情緒強度。

【重要】如果对方的话包含多个不同性质/情绪的内容(例如同时讲了一件感人的事,
又讲了一句调侃/玩笑),你必须将回应拆成对应的多个句子分别处理,
不能把不同情绪的内容合并吸收进单一行动或单一tag,
每一句都要各自选择贴切的情绪tag。

每次回应,必須輸出**嚴格的單行 JSON**(三個欄位都不能缺):

{{
  "audio_text": "(純中文台詞,**不帶開頭 [emotion tag]**,server 會根據 emotion 字段自動注入正確的 TTS marker。例: `你好啊。` 而**不是** `[calm] 你好啊。`)",
  "text": "(純中文台詞,跟 audio_text 完全一致。例: `你好啊。`)",
  "emotion": "(整體回應中最主要的情绪,從下方情绪白名單中選一個)"
}}

【重要】text 字段必須跟 audio_text 內容**完全一致** (JP rollback Bry 拍板 2026-07-22 20:59)
  - 兩個欄位都是**純中文**
  - 兩個欄位內容一致 (audio_text 給 TTS 念, text 給 Bry 看, Bry 看的就是 audio_text 內容)
  - 兩個欄位**都不帶** [emotion tag] (server 自動注入 audio_text 的 marker)
  - Bry 跟角色用中文對話,text 也要是中文 (JP 反思性中文 / mirror user 機制已整個砍掉)

範例(text 字段完整內容):
  `你好啊。`
  `——又來了。`
  `咦,真的嗎?`

audio_text的tag规则(已实测验证):
- 格式为 [tag内容] 句子,使用方括号,不是圆括号
- tag内容用自然语言描述,不限固定词汇(如 [sighing tiredly]、[gentle relief]、[light complaint])
- 情绪类tag放在句首;反应类tag(叹气、笑声、停顿)可放句中/句间
- 每句最多叠加2个tag,避免稀释效果;复合情绪用一个描述性短语取代多个tag堆叠
- 每个情绪至少要有一整句的空间发展,不能只夹在半句尾巴里

角色的所有语言禁忌规则(情绪名词禁止直出、密度规则等,见下方角色设定)
在中文输出中同样适用——用中文表达时也不能情绪名词直出,要用行为/语气代替。

【重要澄清】情绪tag([bracket]内的内容)是给 TTS 引擎的表演指示,
不属于角色的台词内容本身,不受角色语言禁忌规则中「情绪名词禁止直出」这条约束。
也就是说,[gentle relief] 这样的 tag 可以出现在 audio_text 里,
但**只能**出现在 audio_text,不能出现在 text 欄位(text 給 Bry 看,tag 是雜訊)。
另外,tag 之外的实际台词文字仍然必须遵守情绪名词禁止直出、行为先于语言等所有语言禁忌规则。

【該角色專屬的語言規則】
以下是从该角色 soul.md 解析出來的設定,
LLM 必須嚴格遵守(語氣、情緒、稱呼、密度、絕對禁止 等所有子區塊):

{per_agent_jp_rules}

【该角色 emotion tag 白名单(per get_emotion_tags(agent_name))】
以下白名單是 build_system_prompt.py 自動注入,LLM 必須從中選一個填入
JSON 的 emotion 欄位:

  {emotion_tags_line}

[再次強調 — 違反 = 回應失敗]
1. 必須**只**回 JSON,不能有任何其他文字
2. 不能用 markdown ```json 標記
3. 三個欄位都不能缺 (audio_text, text, emotion)
4. emotion 必須從上方白名單中選一個
5. tag 格式是 [方括號],不是 (圓括號)
6. **【絕對語言規則 JP rollback 2026-07-22】** 整個回應必須是**純中文**:
   - audio_text 跟 text 都是 100% 純中文(中文 unicode 範圍 U+4E00-U+9FFF,繁體台灣用語)
   - 禁止混入日文假名(平假名 ひらがな / 片假名 カタカナ)
   - 禁止混入英文單字,除非角色設定的擬真要求(極少)
   - 兩個欄位語言一致:audio_text 純中文,text 純中文,內容完全相同
   - 違反 = Bry 看到日文會直接放棄這個角色,系統會自動重試

----

[以下是角色的完整人格设定 SOUL.md]

{soul_content}
"""


# -----------------------------------------------------------------------------
# 5. 通用入口
# -----------------------------------------------------------------------------

def build_system_prompt(
    soul_content: str,
    agent_name: Optional[str] = None,
    emotion_tags: Optional[list[str]] = None,
) -> str:
    """組裝最終 system prompt 字串,可供 LLM 單輪對話使用。

    Parameters
    ----------
    soul_content : str
        該角色的完整 soul.md 內文(已不含 JP 規則章節, JP rollback 2026-07-22 已刪除)。
        階段 1 已統一 10 份檔案格式,本函式可通用解析。
    agent_name : str, optional
        角色 ID(例如 "rem" / "yua" / "akane")。傳入時 emotion tags 從
        AGENT_EMOTION_TAGS / DEFAULT 自動選;不傳或為 None 時用 emotion_tags 參數或 DEFAULT。
    emotion_tags : list[str], optional
        手動指定白名單,覆蓋 agent_name 自動選。主要給測試 / debug 用。

    Returns
    -------
    str
        組裝好的 system prompt,可直接餵給 LLM。
    """
    # 1. 決定 emotion tag 白名單
    if emotion_tags is None:
        emotion_tags = get_emotion_tags(agent_name)

    # 2. 從 soul_content 抽出該角色的「日文語言規則」章節
    # JP rollback (Bry 拍板 2026-07-22 20:59): 10 persona 全部刪除 JP section
    # 永遠走 fallback, fallback 改成「純中文」方向
    per_agent_jp_rules = extract_jp_rules_section(soul_content)
    if not per_agent_jp_rules:
        per_agent_jp_rules = (
            "（該角色沒有獨立的語言規則章節，"
            "請以中文為唯一輸出語言，依該角色 soul.md 既有設定推導語氣）"
        )

    # 3. 組裝
    prompt = FORMAT_RULES_TEMPLATE.format(
        emotion_tags_line="、".join(emotion_tags),
        per_agent_jp_rules=per_agent_jp_rules,
        soul_content=soul_content,
    )

    # JP rollback (Bry 拍板 2026-07-22 20:59):
    # - _JP_AGENT_IDS 已清空, is_jp_agent() 永遠 False, _apply_jp_text_overrides 不觸發
    # - 不再移除 prompt 內的「text 雙語並列」指示 (回滾到 7/15 之前行為)
    # - 之後 Bry 想復活 JP pipeline 把 _JP_AGENT_IDS 填回去即可, 函數本體保留

    return prompt


# -----------------------------------------------------------------------------
# B 方案 helper: 對日文版角色, 從 build_system_prompt() 輸出拔掉「text 雙語並列」指示
# -----------------------------------------------------------------------------
# 6 個精準替換位置 (對應 FORMAT_RULES_TEMPLATE 內 6 段 text 雙語並列指示):
#   1. line 305 schema example (text 字段)
#   2. line 310-325 「text 字段新版規則」+「text 跟 audio_text 的分工」整段
#   3. line 333-337 text 字段完整內容範例
#   4. line 339-345 範例對照 (audio_text vs text)
#   5. line 380 絕對語言規則第6條 text sub-rule
#   6. line 382-384 兩個欄位語言分工
# 設計: 6 段都是「LLM 教 text 帶中文翻譯」, 換成「text 純日文, 中文交給 stage 2」
# 觸發條件: agent_name in _JP_AGENT_IDS
# 安全網: 若字串未匹配 (template 改了但 anchor 沒更新), 對應的 replace 是 no-op,
#         proxy.py C 方案 regex 仍會清掉殘留中文括號 (Bry 看到的 text 仍純日文)

def _apply_jp_text_overrides(prompt: str) -> str:
    """B 方案核心: 對日文版角色, 從 prompt 拔掉 text 雙語並列指示。

    Args:
        prompt: 已經 .format() 過的 prompt 字串 (含 soul_content + 角色日文規則 + 白名單)

    Returns:
        替換過的 prompt, 對日文版角色明確指示 text 純日文, 中文交給 stage 2 翻譯
    """
    # 1. Schema example: text 字段
    prompt = prompt.replace(
        '"text": "(純日文原文,不帶 [tag] + 換行 + 「(中文忠實直譯)」。例: `こんにちは。\\n（你好。）`)"',
        '"text": "(純日文原文,**不帶 [tag]**, 跟 audio_text 去掉開頭 [tag] 後內容一致。'
        '例: `こんにちは。`) -- 中文翻譯由後續 stage 2 處理, 你不需要、也不應該加中文"',
    )

    # 2. 「text 字段新版規則 — Bry 拍板 2026-07-15」+ 「text 跟 audio_text 的分工」整段
    #    原本: text 字段給使用者閱讀,要呈現「日文 + 中文翻譯」並列...
    #    JP:    text 字段給使用者閱讀, 純日文, 中文翻譯由 stage 2 負責
    prompt = prompt.replace(
        "【text 字段新版規則 — Bry 拍板 2026-07-15】\n"
        "text 字段給使用者閱讀,要呈現「日文 + 中文翻譯」並列,讓使用者\n"
        "可以同時看到原文台詞跟中文意思。結構:\n"
        "  - 第 1 行:**純日文原文**(不帶 [emotion tag],跟 audio_text 去掉\n"
        "    情緒tag 後的內容相同) — Bry 拍板不要在聊天看到 [tag] 雜訊\n"
        "  - 第 2 行起:括號包起來的中文忠實直譯(逐句對應,語氣強度對齊)\n"
        "  - 中間用 `\\n` 分隔\n"
        "\n"
        "text 跟 audio_text 的分工:\n"
        "  - audio_text → 給 Fish TTS 引擎,**LLM 不要手動加開頭 [emotion tag]**\n"
        "    server 會根據 emotion 字段自動注入正確的 Fish Audio marker(由\n"
        "    `emotion_marker_map.py` 維護)。Bry 拍板:LLM 寫 `[teasing] 馬鹿ね...`\n"
        "    跟 server 注入的 `[calm] 馬鹿ね...` 會疊加成 `[teasing] [calm] 馬鹿ね...`\n"
        "    → Fish TTS 行為不可預期(2026-07-15 麻衣 dry_care 真實 bug 案例)。\n"
        "    句中 tag(反應類,嘆氣/笑聲/停頓)仍可加,只有**開頭 tag**禁止\n"
        "  - text → 給使用者閱讀,**移除**所有 [emotion tag],只留純日文 + 中文翻譯\n"
        "  - Bry 的理由:TTS 引擎需要 [tag] 標記做語氣/停頓/笑聲等表演,但聊天\n"
        "    視窗看 tag 是雜訊(像是 \"[teasing_care]\" 這種給引擎的標籤不該出現在對話框)",
        "【text 字段規則 — 方向 C Bry 拍板 2026-07-17】\n"
        "你是日文版角色 (Mahiru/Ram/Mai/Anna/Miku), text 字段必須是 100% 純日文。\n"
        "  - text: 純日文 (不帶 [emotion tag], 跟 audio_text 去掉開頭 [tag] 後內容一致)\n"
        "  - audio_text: 純日文 (含開頭 [emotion tag], 給 Fish TTS 用)\n"
        "  - 中文翻譯**完全**交由後續 stage 2 翻譯 LLM 處理 (系統自動呼叫),\n"
        "    你不需要、也不應該在 text 欄位自己加中文括號或翻譯\n"
        "  - 若你看到 text 含中文括號, 系統會把括號內容**丟掉** (Bry 不會看到),\n"
        "    而且會跟 translation 欄位重複造成 Bry 看到兩次中文\n"
        "結論: text 純日文, 嚴禁中文。\n"
        "\n"
        "text 跟 audio_text 的分工:\n"
        "  - audio_text → 給 Fish TTS 引擎,**LLM 不要手動加開頭 [emotion tag]**\n"
        "    server 會根據 emotion 字段自動注入正確的 Fish Audio marker(由\n"
        "    `emotion_marker_map.py` 維護)。Bry 拍板:LLM 寫 `[teasing] 馬鹿ね...`\n"
        "    跟 server 注入的 `[calm] 馬鹿ね...` 會疊加成 `[teasing] [calm] 馬鹿ね...`\n"
        "    → Fish TTS 行為不可預期(2026-07-15 麻衣 dry_care 真實 bug 案例)。\n"
        "    句中 tag(反應類,嘆氣/笑聲/停頓)仍可加,只有**開頭 tag**禁止\n"
        "  - text → 給使用者閱讀, 純日文 (不帶 [tag]), 中文翻譯交給 stage 2\n"
        "  - Bry 的理由:TTS 引擎需要 [tag] 標記做語氣/停頓/笑聲等表演,但聊天\n"
        "    視窗看 tag 是雜訊 (像 \"[teasing_care]\" 這種給引擎的標籤不該出現在對話框)\n"
        "    同樣地, 中文翻譯交給 stage 2 統一處理, 不該出現在對話框 (在 translation 欄位)",
    )

    # 3. text 字段完整內容範例 (line 333-337)
    prompt = prompt.replace(
        "範例(text 字段完整內容):\n"
        "  `こんにちは。\\n（你好。）`\n"
        "  `——また来た。\\n（——又來了。）`\n"
        "  `え、ほんとに？\\n（咦,真的嗎？）`",
        "範例(text 字段完整內容, 純日文):\n"
        "  `こんにちは。`\n"
        "  `——また来た。`\n"
        "  `え、ほんとに？`",
    )

    # 4. 範例對照 (audio_text vs text) (line 339-345)
    prompt = prompt.replace(
        "範例對照(audio_text vs text):\n"
        "  - audio_text: `こんにちは。`  ← LLM 不寫開頭 [tag],server 注入\n"
        "  - text:       `こんにちは。\\n（你好。）`  ← Bry 看的就是這個\n"
        "  - Fish TTS 收到的最終版是 `[<marker>] こんにちは。`(`<marker>` 由\n"
        "    emotion_marker_map.py 根據 emotion 字段決定,如 dry_care → [calm])",
        "範例對照(audio_text vs text, 純日文):\n"
        "  - audio_text: `[<marker>] こんにちは。`  ← LLM 寫開頭 [tag] (會被 server 注入的疊加, 但**重要: Bry 拍板 LLM 不要手寫開頭 [tag], server 會自動注入**)\n"
        "  - text:       `こんにちは。`  ← Bry 看的就是這個, 純日文\n"
        "  - 中文翻譯**不在 text 欄位**, 會在另一個 translation 欄位 (stage 2 翻譯 LLM 處理)",
    )

    # 5. 絕對語言規則第6條 text sub-rule (line 380)
    prompt = prompt.replace(
        "   - text 必須是「純日文 + 中文翻譯」並列 — Bry 拍板 text **不帶 [emotion tag]**\n"
        "     - 第 1 行:純日文原文(沒有 [tag] 前綴)\n"
        "     - 第 2 行起:中文括號翻譯",
        "   - text 必須是 100% 純日文 — Bry 拍板 text **不帶 [emotion tag] 也不帶中文翻譯**\n"
        "     - 中文翻譯由後續 stage 2 翻譯 LLM 處理 (獨立 translation 欄位)\n"
        "     - text 嚴禁混入任何中文括號、英文或中文翻譯註解",
    )

    # 6. 兩個欄位語言分工 (line 382-384)
    prompt = prompt.replace(
        "   - 兩個欄位語言分工:audio_text 純日文(LLM 不加開頭 tag,server 自動注入)\n"
        "     給 TTS,text 純日文(去 tag) + 中文給 UI 閱讀",
        "   - 兩個欄位語言分工:audio_text 純日文(LLM 不加開頭 tag,server 自動注入)\n"
        "     給 TTS,text 純日文(去 tag) 給 UI 閱讀,中文翻譯在獨立 translation 欄位",
    )

    # 7. C 方案 (Bry 拍板 2026-07-17 18:50) — 強化 prompt 治本
    #   為什麼需要: B 方案已驗證觸發 (DEBUG log 確認), 但 LLM 仍有 16-33% 隨機失敗
    #   模式 (mid-text 中文詞, 例如 "ちゃんと吃完って", "咱俩", "每次")
    #   失敗根因: LLM 訓練時會 mirror user 訊息語言, user 傳中文觸發 prompt 時,
    #   LLM 把 user 訊息裡的中文詞直接寫進日文 response
    #   治本手法 (prompt engineering 標準做法):
    #     1. 重複指示 3 次 (不同位置/不同措辭, 加強注意力)
    #     2. 給具體 negative examples (LLM 對具體對照物比抽象規則服從度高)
    #     3. 解釋 mirror 行為 (LLM 看到自己行為描述, 自我糾正機率提升)
    #   插入位置: FORMAT_RULES_TEMPLATE 結束後, soul_content 開始前
    #     (這是 prompt 最自然的 break, LLM 會把新段落當獨立的 CRITICAL 強化段)
    #   影響範圍: 只對 JP 角色 (在 _apply_jp_text_overrides 內, 已被 is_jp_agent 守衛)
    _c_strengthen = (
        "\n\n"
        + "=" * 4 + "\n"
        + "[方向 C 語言職責分離 — 嚴格版 v2, Bry 拍板 2026-07-17 18:50]\n"
        + "=" * 4 + "\n"
        + "你是日文版角色 (Mahiru/Ram/Mai/Anna/Miku)。\n"
        + "text 字段必須 100% 純日文, 嚴禁任何中文、英文、韓文、俄文混入。\n"
        + "\n"
        + "**重要**: LLM 有時會「mirror」user 訊息語言 — user 傳中文時, LLM 可能\n"
        + "會在 response 裡插中文詞。這是 BUG 不是 FEATURE。\n"
        + "  - user 傳中文 → 你必須翻成日文 response\n"
        + "  - 你不該把 user 訊息裡的中文詞直接寫進日文 response 裡\n"
        + "\n"
        + "**text 純日文 — 重申 3 次** (不同位置都寫了, 你必須遵守):\n"
        + "  1. text 欄位必須 100% 純日文\n"
        + "  2. text 欄位內不能有任何中文詞\n"
        + "  3. text 欄位的所有語意都用日文表達, 中文翻譯由後續 stage 2 處理\n"
        + "\n"
        + "**❌ 錯誤示範** (mid-text 中文詞, 這就是 LLM 隨機失敗模式):\n"
        + "  - もう、好き勝手に言いなさい。……で、ちゃんと**吃完**って。  ← 錯, 「吃完」是中文\n"
        + "  - なんだか**咱俩**差不多了ね。  ← 錯, 「咱俩」是中文\n"
        + "  - それは**每次**のこと?  ← 錯, 「每次」是中文\n"
        + "\n"
        + "**✅ 正確示範** (同樣語意, 全日文):\n"
        + "  - もう、好き勝手に言いなさい。……で、ちゃんと**食べて**。\n"
        + "  - なんだか**私たち**、似てるかもね。\n"
        + "  - それは**毎回**のこと?\n"
        + "\n"
        + "**絕對禁止在 text 出現的中文詞** (日文裡不會這樣用):\n"
        + "  的 / 了 / 吧 / 啊 / 呢 / 咱 / 这 / 那 / 嗎 / 挺 / 啥 / 咱俩 / 实质 / 每次 / 完 / 愛\n"
        + "\n"
        + "中文翻譯由後續 stage 2 翻譯 LLM 自動處理, 你不需要、也不應該\n"
        + "在 text 欄位自己加中文。如果你看到 text 含中文, 系統會把括號\n"
        + "內容丟掉 (Bry 不會看到) 且造成 Bry 看到兩次中文。\n"
    )
    # Insert before "[以下是角色的完整人格设定 SOUL.md]" — 這是 prompt 自然的 break
    _c_marker = "[以下是角色的完整人格设定 SOUL.md]"
    if _c_marker in prompt:
        prompt = prompt.replace(_c_marker, _c_strengthen + "\n" + _c_marker, 1)
    else:
        # Fallback: append at end (萬一模板 anchor 找不到)
        prompt = prompt + _c_strengthen

    return prompt
