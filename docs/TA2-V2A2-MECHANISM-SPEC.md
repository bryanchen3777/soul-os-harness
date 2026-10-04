# A2 機制規格（預登記）— Local Event-Attachment Mechanism

> **Ticket**：TA-2-V2A2 — Local Event-Attachment Mechanism Feasibility（Owner 2026-10-03 22:35 授權）
> **Baseline**：`HEAD = 50dd1f6`（A1 負面基線，**保留不覆蓋**）
> **本檔目的**：Owner 工單把機制定義留給實作者。主大腦依 AGENTS.md「工單必須 decision-complete」**在此把它定死**。
> **執行者照本檔實作，不得自行重新設計機制。**

---

## §0 名稱紀律（Owner 明文）

- 正式名稱：**A2 — Local Event-Attachment Mechanism**
- ❌ **不得**稱為 "syntactic parsing" / "syntactic attachment" / dependency parsing
- 這是 **local / lexical-window heuristic**，不是解析器。報告與程式碼註解一律用 "local attachment"。

---

## §1 誠實前提：**這不是乾淨的預登記**（主大腦明文揭露）

A1 的執行者**已經看過**這 66 筆 corpus；主大腦我也看過。因此本規格**不是**對未見資料的預註冊。

**本輪的有效性不建立在「預登記」上，而建立在 GO-C 的結構性證據。** 抗過度擬合的保護措施是：

1. §5 的 **單一動詞佔比上限**（預登記）
2. Owner 工單的「no lunch special-case」「no per-observation rescue」
3. §6 的兩個 **mutation test**
4. §3 的「event verb 不得與 temporal token 同 span」硬約束

**任何人不得宣稱本輪為 pre-registered。**

---

## §2 A2 唯一要回答的問題

> 若把 A1 的 R6/R7 機制（話語標記共現）換成**針對 event ↔ temporal-noun 的 local attachment relation**，
> Candidate A 是否仍因 recall loss 而失去 measurement value？

**只有 R8 一條新規則。** 其餘規則沿用 A1 逐字不動。

---

## §3 R8 — Local Event-Attachment（**唯讀新增**）

### 定義

對每個 A1 已判定的 **temporal-side token** `t`（即 v1 命中詞）：

> 存在 **event-side token** `v`，使得 `v` 與 `t` 在**同一子句**內，且 `v` 與 `t` 之間**沒有其他漢字**（允許 0 個填充字元），或僅有 ≤1 個填充字元且該字元屬 `FILLER_CHARS`。

**`t` 與 `v` 皆不可為空、不可重疊。**

### 三個硬約束（違反即機制失效）

| 約束 | 內容 | 理由 |
|---|---|---|
| **C1 不自證** | `v` **不得**與 `t` 同 span；`v` **不得**是 `ACTION_TOKENS`（`睡`/`睡覺`/`入睡`/`就寢`） | 否則「去睡吧」會因 `睡` 既是 token 又是動詞而被 rescue，**直接打穿 GO-B** |
| **C2 詞類** | `v` 必須屬封閉的**活動動詞類** `EVENT_VERBS`；其餘一切詞**不得**作為 event-side | 這是語意類別，不是話語標記類別 |
| **C3 子句內** | `v` 與 `t` 必須同子句（沿用 A1 `CLAUSE_SEPARATORS`，逐字不動） | 跨子句共現不是 attachment |

### 封閉詞表（**逐字，不得增刪**）

```
# event-side：一般活動動詞類（非話語標記、非時間名詞）
EVENT_VERBS = {吃, 喝, 煮, 做, 用餐, 開會, 開, 聚, 聚會, 碰面, 見面, 上課,
               上班, 下班, 收工, 上工, 休息, 午休, 出發, 動身, 抵達, 到達,
               加班, 補, 熬, 收尾, 醒, 起床, 睡醒, 通勤, 忙, 忙完}

# 允許的填充字元（僅 C1/C3 之間的 ≤1 個字元）
FILLER_CHARS = {的, 了, 是, 要, 該, 就, 在, 個}
```

> **為什麼 `FILLER_CHARS` 只有 8 個字、且不含 到/這/那/吧**：
> 它們是**連接動詞與名詞的語法填充**，不是話語標記。任何加入 `到`／`這`／`吧`／`那` 的修訂
> **立即判 GO-C FAIL**（見 §6 mutation test 2）。

### R8 在兩段式架構中的位置

A1 的 `summarize()` 三元不變。R8 只改變 **token 層**判定：

- 若 R1／R2／R3／R4／R5 任一觸發 ⇒ 該 token 仍 `vetoed`（**R8 不得覆寫**，這保證 GO-B）
- 否則若 **R8 成立** ⇒ 該 token `kept`
- 否則落入 A1 原有的 R6／R7 判定（逐字不動）

> ⚠️ **R8 不得改變 A1 的 R6／R7。** R8 是**優先於** R6／R7 的**新增分支**，不是替換。
> 這樣 A1 的 `undecided` 語義在 A2 中**保持可比**，A1 vs A2 的差異可逐筆歸因。

---

## §4 主動承認的、**不得修復**的後果

以下是 A2 的**預期代價**，主大腦**預先登記**為可接受。執行者**不得**為了消除它們而改規則：

1. **「深夜已經到尾聲了」會從 `kept` 退回 `undecided`。**
   `尾聲` 不在 `EVENT_VERBS`、`到` 不在 `FILLER_CHARS` ⇒ 該 frame 在 A2 消失，
   儘管它在 A1 存活。**這是正確的**：A1 的存活是靠話語標記 `到`，那正是 A1 要被推翻的機制。
2. **「差不多是午餐時間了」會維持 `undecided`。** 沒有活動動詞，R8 不觸發。
   **不得**為了救它而把 `時間` 加進 `EVENT_VERBS` 或 `FILLER_CHARS`。
3. **A2 的 frame 數可能少於 A1。** 若發生，如實報告；**frame 少不等於機制差**。

> **A2 若在 frame 數少於 A1 的情況下通過 GO-C，那是正確結果，不是失敗。**

---

## §5 抗過度擬合硬門檻（預登記數值）

| 指標 | 門檻 | 判定 |
|---|---|---|
| **單一 event verb 佔比** | 任一 `EVENT_VERBS` 元素若佔「被 R8 救回的 frame」**> 40%** | **GO-C FAIL**（該詞是 magic word） |
| **lunch 專屬化** | 若 R8 救回的 frame 中 `lunch` 佔 **> 60%** 且其他幀無任何恢復 | **GO-C FAIL** |
| **子句外救回** | R8 命中若有任何一筆 `v` 與 `t` 不同子句 | **機制違規，STOP** |

> 這三條是 **Bry 要求的「不得只救 lunch」「不得換一套 magic words」的量化版本**。

---

## §6 Mutation test（Owner 指定，必須實作）

| # | 注入 | 預期 |
|---|---|---|
| **M1** | 讓 R8 **永遠不成立**（把 event-side 條件設為恆 False） | 驗收測試**必須變紅**：lunch retention 退回 A1 的 0/5，GO-A 失敗 |
| **M2** | 把 R8 還原成**話語標記共現**（把 event-side 判定改成「子句含 `到`／`這`／`那`／`吧`」） | **契約測試必須拒絕**：靜態斷言模組內不存在 discourse-marker attachment 常數 ＋ 行為測試「有話語標記但無相鄰活動動詞」必須判 `undecided` |

**M2 的靜態斷言**：A2 模組**不得**出現 `ATTACHMENT_MARKERS` 或任何等价的話語標記集合被用於 attachment 判定。測試以 AST 掃描斷言。

> 依 Bry 的紀律：**「測試存在」不等於「測試在 enforcement contract」**。
> 這兩個 mutation test 就是把 A2 的契約從文件宣告變成**可執行強制**。

---

## §7 預登記輸出（執行者必須全部填滿）

- v1 frame retention / A2 frame retention / 與 A1 的差異
- precision proxies（sleep-action、night-metaphor/simile）
- recall-loss breakdown（vetoed / undecided / **R8-rescued**）
- noise proxy
- ΔR / ΔI / DiD
- **每個被 R8 救回的 frame 必須列出 `v`（event-side token）與 `t`（temporal token）及其相對位置** ← 這是 GO-C 的證據本體
- §5 三條門檻的實測值

---

## §8 邊界（Owner 逐字，重申）

> event temporal attribution ≠ temporal language

A2 的目的**不是**找更多 temporal words，而是判斷一個時間表達**是否在結構上依附於被理解的事件**。

**負面答案是明確允許的。** 若 A2 失敗，如實報 FAIL，**不得在重播過程中修機制**。
Gate 2 **全程維持 INCONCLUSIVE**，與 A2 結果無關。

**A2 不是「再給 A 一次機會」**——它在驗證 Candidate A 的 measurement premise 是否成立。失敗即進 E。

---

## §9 凍結與紀律

- frozen v1（`harness/run_ta2a_gate2.py`）**唯讀**，sha256 必須維持 `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`
- 同一份 66-observation corpus，**0 LLM calls、0 network、不增 N、不改 arm/probe/timestamp/response**
- A1 輸出**保留不覆蓋**；A2 另存新檔
- `src/**` 不動；0 kill、0 restart、`:8000` 唯讀
- Git：只 stage A2 範圍檔案。**repo 內有 Owner 未提交的 VC 線改動（`clients/voice_companion/**` 51 項）與 13 項未追蹤 `docs/`，一律不得 stage、不得清理**
  - Owner 工單寫的 "working tree clean" **僅指 A2 範圍路徑乾淨**，全 repo 先天不乾淨
