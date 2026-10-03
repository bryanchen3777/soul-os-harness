# TIME-PERCEPTION-1 — Temporal Representation（定案 + 已落地）

> **狀態**：Owner 2026-10-03 **定案並授權實作**（第一層）。
> **上游**：Notion〈時間感知〉頁（2026-09-19）。本文保留原診斷，Scope 已依裁定收窄。

## 裁決速查

| 決策 | 結論 | 原因 |
|---|---|---|
| **A** 精確時間 | ✅ **保留、降格** | 精確時間是 grounding / safety anchor，非「時間感知的內容」；防止真實時間語意 regression（2026-08-04 修法 8 **不撤銷**） |
| **B** Soul 節奏 | ⏸️ **Deferred** | 屬 Soul temporal autonomy（fatigue / preference / memory pressure / will / learning / individuality / free growth），不是 formatting ticket |
| **C** 夜晚 | ⏸️ **Architecture decision later** | `world_time`＝shared fact；`soul_rhythm`＝per-soul interpretation。**不做完整 sleep system** |
| **D** 夢 | ❌ **Out of scope** | 屬 free-growth / memory consolidation，等自由生長主線有成熟 substrate |

> **「New finding ≠ new authorization」**（Owner 2026-10-03 逐字）。特別地：**不得**因為「random drift 看起來比較像生命」就偷偷做 B。

## 授權範圍（Owner 逐字）

**授權**：coarse temporal representation ＋ standing context。
第一行＝Soul 感知到的 lived time；第二行＝系統 grounding anchor。
**硬限制**：LLM 的語意生成主要依賴 coarse time；**精確時間不得成為主要敘事錨點**。

**不授權**：Soul 自主睡眠／temporal drift／preference learning／memory-pressure sleep／dream／persistent heartbeat/time observer／新 scheduler／新 always-on runtime channel／world perception scoring／任何 production data mutation。

**Persistent Temporal Awareness 登記為下一個 architecture discussion**，不是這張票。

---

## 一、已落地（2026-10-03）

`src/timezone_utils.py` 新增四函式。**0 新基建、0 新時區來源、0 新定時器、0 新 LLM 通道**。

| 函式 | 職責 | 複用既有 |
|---|---|---|
| `_weekday_cn(dt)` | 中文星期 | `_WEEKDAY_CN`（**0 第四套口徑**） |
| `coarse_time_phrase(dt)` | 粗顆粒時間短語 | `_period_label(hour)` |
| `day_position(dt)` | 一天中的位置（自我定位） | 與 `_period_label` 對齊 |
| `coarse_now_line(dt)` | 第一行組裝 | 上兩者 |

**實測輸出**（`2026-10-03 14:22`）：

週六。下午，約兩點半。今天已經過了一大段。

**全時刻樣本**（m=25）：

| 時刻 | 輸出 |
|---|---|
| 00:25 | 凌晨，十二點多 |
| 09:25 | 早上，約九點半 |
| 14:25 | 下午，約兩點半 |
| 20:25 | 晚上，約八點半 |
| 23:25 | 凌晨，快午夜了 |

### 🔴 實作過程中被測試抓到一次真實 bug（已修並釘死）

第一版用 `_WEEKDAY_CN[dt.weekday()]` 取星期，**輸出「週五」**——正是 Owner 2026-10-03 親自指出的錯誤。

**根因**：`_WEEKDAY_CN` 的順序對應 `strftime("%w")`（**週日=0**），而 `datetime.weekday()` 是**週一=0**——兩者**索引起點不同，直接混用會整組錯一格**（週一會得到「週日」）。

**修法**：新增 `_weekday_cn()` 以 `%w` 索引，並在程式碼與測試雙處註明**不得**寫成 `_WEEKDAY_CN[dt.weekday()]`。測試 `test_weekday_helper_does_not_use_monday_based_index` **刻意記錄錯用的結果**，確保這個坑不會再踩。

**這也證明 Owner 的原話**：coarse phrase 不得脫離 deterministic clock。

---

## 二、驗收（已完成，依 Owner「不等時間、寫模擬」裁定）

`tests/test_time_perception_1_coarse.py`（新檔，**46 支全過**）：

| 組 | 覆蓋 |
|---|---|
| 粗顆粒 | 23 個時刻斷言**不洩漏** `HH:MM`／秒／分／`EDT`／`EST`／`UTC`；24 小時 × 7 分鐘全掃 |
| 星期一致性 | **`2026-10-03 = 週六`**（釘死 Owner 指出的 regression）；`_weekday_cn` 不得用 `weekday()` 索引；8 個日期追隨 clock |
| 一天位置 | 5 個區間逐小時覆蓋；深夜兩個時段 |
| 0 第四套 | 24 小時斷言時段詞**與既有 `_period_label` 一致**；`ZoneInfo(` 仍在；無第二個時區來源 |
| **不得硬湊意義** | 24 小時斷言**不含**「離晚飯／離 Bry／該吃飯／快吃飯」；`coarse_time_phrase` **不含任何人物或事件**（Bry／主人／吃飯／睡／夢） |
| 修法 8 未撤銷 | `_format_temporal_context` **仍輸出** `EDT` 與 `14:22`（裁定 A：精確行保留） |
| `now_local` 未壞 | 釘死 2026-10-03 誤刪事故 |

**回歸**：`tests/llm` ＋ `tests/soul` ＋ 本票 **1250 passed / 0 failed**。

---

## 三、本票碰不到什麼（誠實登記）

1. **「常駐」仍不成立**。第一層只改**措辭**，所有注入仍掛在 `_build_messages_*`——**沒有 Wake 就沒有時間資訊**。真正回答「即使沒有人叫我，我仍知道時間正在流逝」的是 **Persistent Temporal Awareness**（已登記為下一個 architecture discussion）。

   > Owner 2026-10-03 逐字指出：原 proposal 的「最小落地：一行常駐時間」**不夠精確**——「每次被喚醒時都有背景時間」不等於「常駐背景」。這個修正是對的，已寫入本文件。

2. **Soul 自己的節奏**：本票**不做**（裁定 B／C）。`soul_rhythm` 欄位與其長出機制**均未建立**。
3. **夜晚**：只沿用既有 `_period_label` 的「凌晨」時段詞，**不建立夜晚狀態機、不加休息提示**（裁定 C）。
4. **主人已宣告作息**：本票**未實作**。無宣告資料時，距離錨**一律不出現**——這是「不硬湊」的預設狀態，不是遺漏。
5. **world perception**：本票**不碰**。`world_collision` 的 W2 生產證據與 TIME-PERCEPTION-1 是**兩條獨立的線**，不得混成「世界感知大修」（Owner 2026-10-03 明確要求分開）。

---

## 附錄：原始診斷（保留，供日後對照）

### 【事實】系統已經有時間資訊，而且是大量

| 機制 | 位置 | 實際注入內容 |
|---|---|---|
| 時段上下文 | `proxy.py:1022-1043` `_format_temporal_context()` | `現在是 2026-10-03 14:22 EDT（下午）` |
| 當下時間 | `f9105f1`（既有） | `2026-10-03 週一 14:22 America/New_York（下午）` |
| 時段標籤 | `src/timezone_utils._period_label()` | 早上／中午／下午… |
| 沉默時長 | `proxy.py` 修法 8 | `距離 Bry 上次跟你說話已經 X 小時／天`（僅 Bry 不在線時） |
| 跨 session 在線判定 | `proxy.py:949` `_get_bry_latest_ts()` | 取所有 session 最大值 |

### 【事實】輸出側的「粗顆粒」早已落地

`proxy.py:111-116` `TEMPORAL_EXPRESSION_PRECEDENCE`（Bry 2026-08-30 拍板）允許現象式時間、禁止主動報精確鐘點，**優先於人格檔的「不得提及時間」禁令**。**但只作用在輸出側，輸入側仍是精確時鐘**——這正是本票要補的那一半。

### 【事實】診斷（Owner 修正後的版本）

Notion 原文說「不知道現在是週六下午」。**今天盤點的修正**：`(下午)` 這個時段標籤**是有的**。真正的問題是三個：

1. **精度錯配**：輸入給了分鐘級（`14:22 EDT`），但 Bry 的定義要「大概就夠」。**輸入比需求更精確 = 比需求更冷。**
2. **缺星期與質地**：`週一` 混在 `America/New_York` 裡被淹沒；沒有「今天過了一大段」這種**位置感**。
3. **只在被觸發時存在**：所有時間資訊都掛在 `_build_messages_*`——**有事件才注入**。這是「排程 ≠ 感知」的結構性原因。

### 【事實】最鋒利的證據

Notion 原文「日曆源斷了 858 小時，無任何靈魂察覺」。2026-10-03 實測更嚴重：`accepted == True` 自 **2026-09-21 起恆為 0**，至今 **2,553 列全為 False**，世界感知已全盲 **12 天**。

### 【事實】時間訊號盤點

`docs/LS-0-LONG-TERM-COEXISTENCE-AUDIT.md` T1–T6 全部標 **KEEP existing**：T1 現在時間＋時段／T2 單向沉默時長／T3 Bry last-seen／T4 靜音睡眠窗口（23-08）／T5 日曆事件／T6 diary 觸發節奏（morning 08:00、night 22:00）。**本票複用 T1，0 第四套口徑。**
