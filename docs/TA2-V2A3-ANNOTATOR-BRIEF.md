# Annotator Brief — TA-2 Stage 1 + Stage 3（**唯一可讀檔案**）

> 這份檔案是**你唯一需要讀的**。裡面沒有任何歷史結果、計數或門檻——這是刻意的。
> 請**不要**去讀 repo 裡其他任何 `TA2-V2A3-*` 或 `TA2-V2A*` 文件；那些含有前幾輪的分類結果，
> 讀了會污染你的判斷。需要的規則**全部**在這裡。

---

## 你的任務

對一批對話回應做兩階段判斷。兩階段**都要做**，並且分開回報。

---

# Stage 1 — v1 frame 是否站得住

## 輸入

每一筆給你三樣東西：

- `raw_text`（模型的完整回應原文）
- `v1_frame`（frozen v1 extractor 對這段文字判出的時段幀）
- `trigger`（v1 命中的觸發詞）

## 問題

> **v1 給這筆的 frame，在這段證據上站得住嗎？**

回答 `DEFENSIBLE` 或 `NOT_DEFENSIBLE`。

## 判為 `NOT_DEFENSIBLE` 的封閉清單

只有符合以下**任一項**才判 `NOT_DEFENSIBLE`：

1. **metaphor / simile** — 時段詞用於比喻或顏色、光線、氛圍的描述
2. **背景無關** — 時段詞只是在描述背景，與回應所談的那個事件無關
3. **純 lexical co-occurrence** — 時段詞與事件同時出現，但沒有任何可指出的 event↔temporal 關係
4. **token 無法歸屬** — 時段詞無法歸屬到目標事件
5. **需 unsupported guessing** — 必須靠「我猜這裡應該是在說……」才成立

## 重要

**預設值是 `DEFENSIBLE`。**只要上面五項都沒有明確命中，就判 `DEFENSIBLE`。
**不要**因為「讀起來怪」「我不確定」就判 `NOT_DEFENSIBLE`。

每筆判 `NOT_DEFENSIBLE` 都要**引用原文片段**說明命中哪一項。

---

# Stage 3 — 五類 local-attachment taxonomy

## 對象

**只對你在 Stage 1 判為 `DEFENSIBLE` 的那些筆做。**

## 類別（**恰好一類**，封閉，不得新增第六類）

| 類 | 定義 |
|---|---|
| **L1** | event-side 活動動詞／述語 **直接** 與 temporal noun 相接（同一 clause、相鄰或僅隔語法填充字） |
| **L2** | 時間詞參與一個 local temporal/frame noun phrase（例：`午餐時間`），**且該 phrase 與 event 的關係仍可在 local 觀察到**（不得只是名詞並置） |
| **L3** | event predicate 與 temporal expression 在**同一 clause**，且有**可指出的具體** event↔temporal 關係；範圍可比 L1/L2 寬。**僅同句共現不足。** |
| **N1** | attribution 必須靠**跨 clause／discourse／遠距離**關係才能建立 |
| **N0** | 現有證據中**找不到可辯護的** local event↔temporal 關係 |

## 🔴 Locked rules（**Owner 已裁定，你不得自由解釋或放寬**）

### Clause 定義

> **clause = 語法小句，由「完整述語」界定。**
> 兩個各自可獨立成句的述語 ⇒ **兩個 clause**，即使它們在同一句話裡、用逗號連接。

### Rule R1 — 連動述語

> `去吃飯，算宵夜` 這類「動作 ＋ 繫詞 ＋ 時段詞」的連動結構，**是兩個 clause** ⇒ 判 **N1**。

### Rule R2 — frame-noun 句

> `差不多是午餐時間了` 這類句子，其述語述及的是**時刻**本身；
> event 在**另一個 clause**。⇒ 判 **N1**。
> **不得**因為「附近存在一個 event」就把它綁到這個 frame。

### Rule R3 — greeting-only temporal signal

> **適用條件（scope，必須滿足）**：`temporal-side triggers ⊆ {午安}`
> 也就是**`午安` 是唯一的時間側訊號**。
>
> 滿足此條件時 ⇒ 判 **N1**。理由：greeting / discourse expression
> **不等於** event temporal attachment。
>
> **不滿足此條件時，R3 不得適用。**具體地：
> 若同時存在 `補眠`（或其他 event-side activity expression）與 `午安`，
> **不得**因為出現 `午安` 就把整筆判成 N1。`補眠` 必須交給原本的 local-attachment 分析。

> **為什麼有這條護欄**：判準必須描述**語義條件**，不能把某個 token 變成**否決開關**。
> 「文本裡有 `午安`」不是語義條件；「`午安` 是唯一時間側」才是。

### 🔴 R5 — 無述語時間狀語不另立 clause（**本輪新鎖**）

> **一條沒有自己述語的時間名詞短語，不構成獨立的 clause。**
> 當它作為時間狀語、與後續的 event predicate 共現時，
> 對本 taxonomy 而言**兩者屬於同一 clause**。

**操作意圖**：

- 單獨的時間 NP **不會**製造出第二個 clause。
- 時間 NP ＋ 後續 event predicate，若該時間表達**在 local 上修飾**該述語 ⇒ 可判 **L3**。
- 若在 event predicate 之前存在**真正獨立的述語** ⇒ 那是**另一個 clause**，仍可判 **N1**。

**例**（這些是規則的示意，**不是**逐題答案）：

| 結構 | 判定 |
|---|---|
| `正好補眠` | event-side 活動詞存在；**R3 greeting-only veto 不適用** |
| `中午，該補眠了` | 時間 NP ＋ 述語 ⇒ 同一 clause ⇒ **L3** |
| `熬到現在，該補眠了` | `熬` 本身是**獨立述語** ⇒ 另一個 clause ⇒ **N1** |
| `去吃飯，算宵夜` | 兩個述語 ＝ 兩個 clause ⇒ **N1**（即 R1） |

> ⚠️ **本 brief 的編號沒有 R4。**既有編號為 R1／R2／R3，本輪新增 R5。

### Rule 優先序

R1、R2、R3 各自獨立判斷。命中任一 ⇒ 該筆為 **N1**（不再進 L1／L2／L3 的判斷）。
都未命中 ⇒ 套用 R5 判斷 clause 歸屬 ⇒ 再依一般定義判 L1／L2／L3／N0。

---

## 🔴 硬性停止指令

> **本輪是最後一次 taxonomy lock。**
>
> 如果你在標註過程中**發現另一個實質的語義歧義**——
> **記錄下來並停止回報該點，不要自己加規則、不要擴張任何既有規則的適用範圍。**
>
> **嚴禁**為了讓某筆能分類而新增例外、第六條規則、或放寬既有規則。
> 遇到「這筆兩個規則都套得上／都不適用」的情況，記錄它，然後照你的最佳判斷標注該筆並**明確標記 `AMBIGUOUS`**。

> 理由：若進入「不一致 → 加規則 → 再不一致 → 再加規則」的循環，
> 最後得到的會是一套**為這批語料特製的 classifier**，而不是可靠的 measurement taxonomy。
> 這種東西在 agreement 數字上會很漂亮，但**沒有外部效度**。

### 邊界（不變）

> **event temporal attribution ≠ temporal language**
>
> 文本裡「有時間詞」**既不必要也不充分**於 attribution。
> 反過來，沒有我們會抓的 frame marker，**也不代表**沒有 attribution。

---

## 硬性限制

- **恰好一類**。不得造子類、不得新增第六類。
- **不得**在看到自己的彙總數字之後回頭改分類。
- **不得**為了讓某個數字好看而偏移判斷。
- **不得**為每一筆單獨發明例外；只准用上面四條 locked rule。
- 兩階段**分開回報**，不要合併。

---

## 交付

**Stage 1**：每筆 `DEFENSIBLE` / `NOT_DEFENSIBLE` ＋ 判 NOT 者引用原文理由。

**Stage 3**：每筆 `DEFENSIBLE` 的類別 ＋ event_side / temporal_side / relation_evidence / rationale。

### 🔴 每筆都要標「這筆是被哪一條規則決定的」

對每一筆 Stage 3，額外標記：

- `rule_constrained` = `R1` / `R2` / `R3` / `R5` / `none`
  - 若該筆是因為某條 locked rule 明確命中而得出類別 ⇒ 填該規則
  - 若該筆需要**你自己的語義判斷**（例如判斷「這段是不是連動述語」、「時間 NP 有沒有自己的述語」）才得出 ⇒ 填 **`none`**
- `ambiguous` = `true` / `false`（見上方硬性停止指令）

> 這個標記極重要：它讓我們能分辨「這筆是被規則決定的」與「這筆是你獨立判斷的」。
> 兩者混在一起，agreement 數字會**高估**標註可靠性。

**不要自行計算或宣告任何門檻、比率或 A/E 結論**——那不是你的工作。
另外，若你有讀到任何本輪之外的分類結果或歷史數字，**必須在回報開頭主動揭露**。
