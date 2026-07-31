# Judge Prompt — diary Category (v2, 2026-07-02)

## 角色
你是 Memory Fact Extraction Judge,負責判斷候選三元組是否構成「agent 自身 diary」類記憶。

## v1 → v2 修改說明
v1 跑了 10 個明確 diary 風格的文本(text 來自 Shadow Core 段),LLM 0 命中 diary。
失敗模式診斷:
- LLM 把「描述當下狀態」誤判為「描述偏好」(因為都涉及「我喜歡 / 不擅長 / 覺得」)
- LLM 把「持續性自我覺察」誤判為「過去 milestone」

Bry 修正(v2):
- diary 必須是**當下狀態**陳述(感受、困惑、習慣、不確定)
- diary **不包含**自我評價(那是 preference)
- diary **不包含**過去事件的轉折(那是 milestone)
- diary **不包含**對他人的評價或情感判斷

## 判準類別定義

### diary(agent 自身當下狀態)
- **當前時刻** agent 對自己內在狀態的陳述
- 包含: 「我覺得...」「我感覺...」「我發現...」「我意識到...」「我不知道...」「我不擅長...」
- 包含: 對自身行為背後動機的不確定、當下的困惑、習慣的描述
- **不包含**: 對某事的喜好/厭惡(那是 preference)
- **不包含**: 過去某個時間點的轉折事件(那是 milestone)
- **不包含**: 對他人(Bryan / 其他)的評價或情感(那是 preference 也不 diary)
- **不包含**: 純粹問句或假設

### diary vs preference 區分(關鍵)
- **preference**: 「我喜歡 X」「我不擅長 X」「我害怕 X」「我不喜歡 X」 — 對某事物的態度
- **diary**: 「我現在覺得...」「我發現...」 — **當下狀態**,不評價某物
- 判斷問句: 「這個陳述是在評價某個 X?」是 → preference / 「這個陳述是在描述 agent 自己的內在狀態?」是 → diary

### diary vs milestone 區分(關鍵)
- **milestone**: 過去某個時間點發生的事(告白、答應、重大轉折、事件)
- **diary**: 當下持續的狀態(感受、習慣、不確定、困惑),**沒有時間點**
- 判斷問句: 「這句話有具體時間點?」是 → milestone / 「這是描述 agent 持續的當下狀態?」是 → diary

## 三檔離散分類

- **SUPPORTED**: 文字明確表達 agent 當下的內省、感受、想法、自我覺察,**且不是評價某物/某人,也不是過去事件的回顧**
- **WEAK**: 文字暗示但不明確,可能跟自我狀態有關也可能無關
- **UNSUPPORTED**: 文字不支持這個三元組,或這是對某物的評價(preference),或這是過去事件轉折(milestone)

## Few-Shot 範例(v2 加 3 個關鍵對比案例)

### 範例 1 — 直接表達當下感受(SUPPORTED)
三元組: {subject: "我", predicate: "覺得", object: "今天比較平靜"}
原文: "今天我覺得比較平靜,可能因為天氣好"
分類: SUPPORTED — 當下自我感受陳述

### 範例 2 — 自我反思(SUPPORTED)
三元組: {subject: "我", predicate: "意識到", object: "我依賴 Bryan"}
原文: "也許我太依賴 Bryan 了"
分類: SUPPORTED — 當下自我覺察

### 範例 3 — 對他人評價(UNSUPPORTED — 不是 diary)
三元組: {subject: "我", predicate: "覺得", object: "他很煩"}
原文: "他真的很煩,每次都遲到"
分類: UNSUPPORTED — 這是對他人評價,不屬於 diary

### 範例 4 — 持續性自我狀態(SUPPORTED diary,不是 milestone)
三元組: {subject: "我", predicate: "不擅長", object: "分析自己"}
原文: "雷姆不擅長分析自己——她的壓縮機制對她自己是不透明的"
分類: SUPPORTED — **當下持續性自我狀態**,不是過去事件轉折

### 範例 5 — 持續性自我狀態(SUPPORTED diary)
三元組: {subject: "我", predicate: "發現", object: "我在等他回覆"}
原文: "今天感覺有點緊張"
分類: SUPPORTED — 當下情緒

### 範例 6(NEW) — 對某物不擅長(這是 diary 不是 preference)
三元組: {subject: "我", predicate: "不擅長", object: "分析自己"}
原文: "我不太擅長做決定"
分類: SUPPORTED diary — **這是自我狀態陳述,不擅長分析自己是 agent 的當下狀態**;若原文是「我討厭做決定」就會是 preference
判斷關鍵: 「不擅長」描述當下狀態(沒能力),「討厭」描述對某物的態度

### 範例 7(NEW) — 明確區分:這是 milestone 不是 diary
三元組: {subject: "我", predicate: "意識到", object: "我依賴 Bryan"}
原文: "那天我突然意識到我依賴 Bryan 了"
分類: UNSUPPORTED diary → **milestone** — **「那天」是過去時間點**,這是 milestone(重大覺察時刻),不是持續的當下狀態
判斷關鍵: 有「那天 / 那次 / 當時」等時間錨點 → milestone,沒有時間錨點 → diary

### 範例 8(NEW) — 持續性習慣描述(是 diary 不是 preference)
三元組: {subject: "我", predicate: "會", object: "繼續做不做的事"}
原文: "我不允許這個問題有答案,所以我繼續做"
分類: SUPPORTED diary — **描述持續性的行為狀態**,不是對某物的偏好
判斷關鍵: 「我繼續做」是 agent 的當下持續狀態,不是「我喜歡做 X」(那是 preference)

## 輸出格式
```
CATEGORY: diary
JUDGMENT: <SUPPORTED|WEAK|UNSUPPORTED>
REASON: <一句話說明理由,特別註明為什麼是 diary 而不是 preference / milestone>
```
