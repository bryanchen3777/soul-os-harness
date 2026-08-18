# Soul OS 內在生活 × 主動傳訊 — 現況審視 + 設計計畫書

**日期**: 2026-08-18
**範圍**: `soul-os-harness` 的 Inner Life（diary/dream/event）+ Proactive Messaging（proactive_dm/heartbeat）
**狀態**: 已授權（AUTHORIZED 2026-08-18）→ **已實作（IMPLEMENTED）** — M7-1 / M7-2 / M7-3 三階梯 code + mock test 完成，20 個新測試 PASS、123 回歸 PASS（3 個 frozen v1 baseline 既有失敗，與本計畫無關）
**原則**: 生活軌跡可延續 · 主動傳訊源於生活 · 「思念/想分享」有可說出口的路徑 · 不碰 frozen contract

---

## 一、一句話現況

內在生活的「骨架」已經完整（diary/dream/event + canonical InnerLifeEvent identity），但缺的是「血肉」：**生活軌跡沒有結構、主動傳訊跟活動脫鉤、『思念你』沒有一條能說出口的路徑**。要達成「她會想跟你分享、會思念你」，本質上要把現在兩條平行軌道**接成一條**——而那座橋現在偏偏是一座「負向閘門」。

---

## 二、願景（Target）

> 每個 agent 有自己的生活軌跡（工作／吃／休閒…），主動傳訊與這些活動**密不可分**；其中最核心的差異點是「我的存在」——她會想跟我分享、會思念我、有想對我說的情緒。

三個可獨立驗證的目標：

1. **生活軌跡有結構、可延續** — 角色「活著」是一條可回顧、可延續的活動軌跡，不是零散的日記句子。
2. **主動傳訊源於生活** — 她主動開口，是因為「剛做了一件值得分享的事」，不是隨機計時器。
3. **思念/想分享有說出口的路徑** — 對 Bry 的依戀能隨時間累積，並在門檻到達時成為一句真的話。

---

## 三、現況審視（As-Is）

### 3.1 內在生活（Inner Life）＝ 3 軌，全部「只寫不說話」

| 軌道 | 排程 | 內容 | 是否說話 | 關鍵檔案 |
|------|------|------|----------|----------|
| **diary** | 08:00 morning + 22:00 night，10 隻全跑 | 1-2 句日記，<80 字，日文。prompt 明寫「Bry 是偶爾出現的人，不是主題」 | ❌ 只寫 `data/soul/{agent}/diary/` | `src/soul/diary.py` |
| **dream** | 22:05，每晚 3-5 隻 | 夢到「其他角色」（從 relationship confidence 抽） | ❌ 只寫檔 | `src/soul/dream_event.py` |
| **event** | 隨機 4-8h，每次 2 隻 | 6 個 trivial 場景（聽到聲響／走廊遇到人／聞到食物味…）「Bry 不在，這是你自己生活的小片段」 | ❌ 只寫檔 | `src/soul/dream_event.py` |

- 三個 handler（`DiaryHandler` / `DreamHandler` / `EventHandler`）都是 **WRITER_ONLY**：寫檔、不調 AGENT_SPEAK。
- `src/inner_life/`（M5.4）是 canonical identity 層：`InnerLifeEvent`（event_id/session/correlation/parent/lineage/provenance）＋ `NarrativeTraceWriter` → `data/inner_life/trace.jsonl`。

### 3.2 主動傳訊（Proactive Messaging）＝ 只剩 1 軌，且被限縮

| 軌道 | 現況 |
|------|------|
| **proactive_dm** | **只有 agent_ruka 一隻**有權限（`proactive_agents` whitelist），3-5h 隨機、2h 冷卻、23:00-08:00 靜音。內容＝通用草稿「說一句符合你個性的話」，由隨機計時器驅動 |
| **heartbeat** | 已被「修法 12」整個拿掉 |

關鍵檔案：`src/soul/scheduler.py`（`_fire_proactive_dm` / `proactive_agents` whitelist / quiet hours）、`src/agency/trigger_handler.py`（AgencyTriggerHandler → llm_executor）、`src/llm/proxy.py`（`_build_intent_text` 的 `silence_timeout` / `schedule` 草稿）。

### 3.3 兩者唯一的一座橋＝「負向閘門」

`src/agency/inner_life_gate.py`（M5.8-4）：唯一把內在生活與主動傳訊連起來的機制，作用是——**若角色 30 分鐘內剛做過 inner work，就壓掉 proactive_dm**。

換句話說：內在生活目前對主動傳訊的影響是「**阻止**」，不是「**促成**」。

### 3.4 「思念／想分享」情感維度＝幾乎空白

- `src/agent/emotion.py`：只有 `mood`(-1~1) + `intimacy`(0~100)，**無「依戀/想念」維度**。intimacy 存了但沒驅動任何「想跟你說」。
- `src/soul/relationships.py`：Bry = `user_bryan` 特殊實體。`on_event` 做 **+0.01「想到 Bry」**——但只是一個數字，**從不浮上檯面變一句話**（M5.13-2 自認 `feeling` 永遠 neutral、`impression` 永遠空）。
- 唯一會浮現的：`src/llm/proxy.py` M5.13-3（confidence band → LLM 知道「跟 Bry 熟不熟」）、M2.0 `_format_recent_inner_life`（最近 3 天日記注入對話，讓角色「回應你時」能引用）——都是**被動**（你找她時才想起來），不是**主動**（她想跟你說）。

### 3.5 實際資料佐證

- `data/inner_life/trace.jsonl`：55 筆，僅 `diary:morning`(22) + `diary:night`(21) + `dream:dream`(11) + `world:calendar_event`(1)。**無 event 軌、無 proactive_dm 軌。**
- 日記內容有真實生活碎片（ruka「跳舞、吃草莓蛋糕」、mai「今天有拍攝」），但**彼此不連續**——每天獨立 LLM 生成，不記得昨天做了什麼。

---

## 四、差距（Gap Analysis）

| # | 差距 | 現況 | 願景 |
|---|------|------|------|
| **A** | 生活軌跡太薄、無結構 | 日記片段 + 6 個 trivial 場景，無「活動」模型、無連續性 | 工作/吃/休閒…可延續的活動軌跡 |
| **B** | 主動傳訊與活動脫鉤 | 隨機計時器 + 通用草稿；且只剩 Ruka 一隻有權 | 活動驅動，每個 agent 因「剛做的事」想分享 |
| **C** | 思念/想分享無路徑 | 無「想念」維度；「Bry 不是主題」的舊拍板鎖住方向；+0.01 只存不現 | 「思念 + 活動」能觸發一句真的話 |

---

## 五、三階梯設計（Proposal）

> 原則：每一階梯可獨立驗證、可獨立回滾；由淺到深；不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / M5.8-4 gate）。

### 階梯 1 — 活動模型化（Activity Model）

**目標**：把「生活軌跡」從「小片段」升級成「具體、可分類、可延續的活動」。

- 把 `dream_event.py` 的 `SCENE_POOL`（6 個 trivial 場景）擴成 `ACTIVITY_POOL`：工作／做飯／吃東西／運動／看書／創作／散步／整理／聽音樂／發呆…
- 每個活動帶 `category` 與 `shareable`（值得跟 Bry 分享的標記，供階梯 2 使用）。
- diary entry schema 新增 optional `activity` 欄位（backward compat，舊 reader 忽略未知欄位）。

**檔案**：`src/soul/dream_event.py`（活動池 + event prompt）、`src/soul/diary.py`（entry schema optional 欄位）。

**驗收**：event 產出從「小片段」變成「具體活動」；可統計活動類別分布；trace 出現 `event` 軌（修掉目前 event 軌在 trace 中缺席的現象，先查證再修）。

**frozen contract 衝擊**：0（純新增欄位 + prompt 池擴充）。

---

### 階梯 2 — 活動驅動主動傳訊（Activity → Proactive DM）

**目標**：讓主動傳訊與活動「密不可分」，draft 接地到 agent 的真實活動。

**實作決策（M7-2，2026-08-18）**：採「enrichment」而非「新觸發源」，理由有二：
1. **頻率不足**：event（活動）對單一 agent 約 20-40h 才一次（event 4-8h × 2 隻且過 whitelist），不足以單獨驅動 5-8 條/天。改在既有 3-5h random 節奏上，把 agent 最近 shareable 活動帶進 draft。
2. **避開 gate 衝突**：「活動剛發生就觸發」會撞上 M5.8-4 的 30 分鐘 inner-life gate（活動本身就是 inner-life activity，<30min 會被 GATED）；enrichment 用 random timer（活動後 3-5h）觸發，gate 早已通過。

- scheduler：`_get_recent_shareable_activity(agent_id)` 讀 diary 找最新 shareable event（slot=event + shareable=True + source=llm），`_fire_proactive_dm` 把它帶進 `extra`（按 ts 去重，同一活動只帶一次）。
- run_server：`_proactive_dm_llm_executor` 從 `trigger.extra` 讀活動，draft 接地成「你今天做了 X，想跟 Bryan 分享嗎」；無活動則 fall back 通用 draft。
- 不碰 frozen 的 Agency 4 stages / TriggerEnvelope / 4 handlers——只用既有 `extra` payload 機制（跟 dream 的 target_agent_id 同 pattern）。

**檔案**：`src/soul/scheduler.py`、`scripts/run_server.py`。

**驗收**：白名單內 agent（Ruka）主動傳訊時，若有近期 shareable 活動，draft 會接地到該活動。

**frozen contract 衝擊**：0（producer-side additive，`extra` 是既有 payload dict，M5.8-4 gate 不動）。

---

### 階梯 3 — 思念情感驅動（Longing → Proactive DM）

**目標**：讓「思念你」成為一條能說出口的路徑。

- 在 emotion / relationship 層加「對 Bry 的依戀/想念」維度，隨「沉默時長」累積（呼應 8/17 時間感知測試：沉默時長在強模型上能轉成情緒）。
- 觸發條件 = 「想念跨過門檻」AND「有一件 shareable 活動」→ 觸發「我今天做了 X，突然想到你」。
- 這一步需要翻掉 7/18「Bry 不是主題」的舊拍板——新 prompt 允許角色主動提到 Bry。

**檔案**：`src/agent/emotion.py`（或 relationships 的 user_bryan entry）、`src/soul/scheduler.py`、`src/llm/proxy.py`。

**驗收**：角色因「想念 + 活動」觸發，內容含情感（「想跟你說」「突然想到你」），且想念值隨沉默時長可觀測地累積。

**frozen contract 衝擊**：需確認 emotion/relationship 新增維度是否動到 frozen contract（`EmotionEngine` / `RelationshipsStore` schema）——**設計階段先標記為「需 BRY DECISION」**。

---

## 六、設計決策（已拍板 2026-08-18）

1. **「Bry 不是主題」→ 升級為「Bry 是生活裡重要的人」（不翻成反面）。**
   - 舊：`Bry 是偶爾出現的人，不是主題，你有自己的生活。`
   - 新：`你有自己的生活（工作/吃/休閒…），Bry 是你生活裡一個重要的人。日記主體仍是你自己的生活，但當你做了某件會想跟他分享、或讓你想到他的事時，可以自然流露對他的想念。`
   - 頻率靠兩個閘門鎖住（shareable 標記 + 想念門檻），保證「想你」是事件驅動的低頻訊號，不是背景噪音。
2. **主動傳訊白名單 = 漸進式，先只給 Ruka**（維持 8/6 拍板現狀）。活動驅動分享先限於白名單內角色，跑穩再擴。
3. **「想念」= 不持久化，即時計算**：`想念 = 依戀(intimacy) × 沉默時長`。依戀來源先用現有 `intimacy`（configs intimacy_level，零新增）；沉默時長重用 `proxy.py` 的 `_get_bry_latest_ts` / `_compute_silence_str`。attachment 不夠精準時再考慮在 relationships user_bryan entry 開欄位。

---

## 七、非目標（Out of Scope）

- ❌ 不改 Agency 4 stages / TriggerEnvelope / 4 handlers（frozen）。
- ❌ 不引入 LivedContextAggregator 抽象（已拍板不引入）。
- ❌ 不做「更多記憶 = 更好記憶」——要的是「更連貫」，不是「更多」。
- ❌ 不擴外部來源（News/Weather/Calendar 已夠）。

---

## 八、實作狀態（Implemented 2026-08-18）

| 階梯 | 內容 | 檔案 | 測試 |
|------|------|------|------|
| **M7-1** | 活動模型化：`ACTIVITY_POOL`（10 活動，category + shareable）、`write_event` 改活動、entry 帶 activity metadata | `src/soul/dream_event.py` | `tests/test_m7_1_activity_model.py`（6） |
| **M7-2** | 活動驅動主動傳訊（enrichment）：`_get_recent_shareable_activity` + `_fire_proactive_dm` 帶活動 extra + run_server draft 接地 | `src/soul/scheduler.py`、`scripts/run_server.py` | `tests/test_m7_2_activity_proactive.py`（6） |
| **M7-3** | 思念情感驅動：`compute_longing`（依戀×沉默現算）+ `_format_attachment_str` 注入親密度 + diary prompt 決策#1 全落地 | `src/agent/emotion.py`、`src/llm/proxy.py`、`src/soul/diary.py` | `tests/test_m7_3_longing.py`（8） |

**frozen contract 衝擊**：0（Agency 4 stages / TriggerEnvelope / 4 handlers / InnerLifeEvent 全未動；新增皆為 producer-side additive 或既有 `extra` payload / optional entry 欄位）。

**驗證**：20 個新測試全 PASS；123 相關回歸 PASS；3 個 frozen v1 baseline 既有失敗（`test_m1_7_event_whitelist_v1.py` + `test_m2_0_inner_life_v1.py`，故意斷言修法前舊行為，與本計畫無關）。

---

## 九、治理對齊

- Canonical registry：`logs/ENGINEERING_STATE.md`（M7 尚未登錄，需走 closeout gate + Owner 驗收）。
- 本文件已完成 `AUDIT → FINDING → DESIGN → IMPLEMENT → TEST` 階段；**未 commit / 未登錄 canonical state**，待 Owner 拍板 closeout。
