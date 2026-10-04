# TA-2 v2-E — Cross-Model Design Review 結果與 Gate 判定

> **日期**：2026-10-04 12:34
> **問卷**：`docs/TA2-V2-E-CROSS-MODEL-QUESTIONNAIRE.md`（`80dd8be`，逐字固定，兩 reviewer 同一份）
> **被審文件**：`docs/TA2-V2-E-MEASUREMENT-DESIGN.md`（`b9ae005`）
> **Reviewer 1**：Perplexity / Claude（外部）
> **Reviewer 2**：Gemini（外部，經 Owner 提供）
> **狀態**：**E2 = BLOCK（兩 reviewer 一致）→ E Design 不得進入 implementation**

---

## §1 Gate 結果總表

| Gate | Reviewer 1 (Claude) | Reviewer 2 (Gemini) | 綜合 |
|---|---|---|---|
| **E0** Construct validity | **BLOCK** | CONCERN | 🔴 **BLOCK**（見 §3.1） |
| **E1** Causal contrast | CONCERN | PASS | 🔴 **CONCERN**（見 §3.2，含主大腦自查發現） |
| **E2** Evaluation independence | **BLOCK** | **BLOCK** | 🔴 **BLOCK（一致）** |
| **E3** Unit of analysis | CONCERN | PASS | 🟡 CONCERN（見 §3.3） |
| **E4** Measurement integrity | CONCERN | CONCERN | 🟡 **CONCERN（一致）** |
| **E5** Threshold readiness | CONCERN | PASS | 🟡 CONCERN（見 §3.4） |

> **兩位 reviewer 都回報 `Does the design require a new design decision? = YES`**
> **兩位都回報 `Threshold numbers proposed: NONE`** ✔ 問卷的禁止條款被遵守

---

## §2 一致 BLOCK：E2 — 條件洩漏使 blinding 形式化

兩位 reviewer **獨立地**、用不同措辭，指出**同一個缺陷**。

### Reviewer 1 的論證鏈（較完整）

> 「Temporal relation target」這個欄位是兩難：
> - 若它陳述了 expected interpretation（例如「afternoon → lunch-like」），evaluator 只需要問「哪個 response 符合 target」——
>   **那就是一個用 LLM 實作的 temporal-attachment classifier，也就是 A-v2 的失敗被搬進 evaluator。**
> - 若它不陳述，「哪個比較受 temporal context 影響」就**沒有** correct/mismatched direction，
>   因為兩個 response 都受時間影響。解碼後的 `P(correct-direction preference)` **就不在量 direction**。
>
> §11 禁止用 generation 端的 answer schema 決定 evaluator 的答案，**而 target 欄位看起來正是在做這件事**。
> 文件從未說明 target 從哪裡來。

並補上第二層攻擊：

> Opaque labels 與 A/B 順序隨機化**只藏起 label 名稱與位置**。
> **response 本身帶著條件**：時間詞、語域、猶豫，或 context 不合時的語無倫次（例如下午出現「宵夜」）。
> 文件從未說 evaluator 是否看到 context 字串。
> 若看到 ⇒ 條件可直接還原。若看不到 ⇒ 它只能從 lexical content 判斷，**而那正是 E0 說不得依賴的東西**。
> 且設計**沒有任何 evaluator 的可靠性檢查**（人類一致度、self-consistency、order-swap stability）。

### Reviewer 2 的獨立確認

> 即使有 randomized A/B labels 與 blinding，**在不同 temporal context 下生成的 response 本身就帶有
> semantic 或 lexical markers，讓 evaluator 能還原條件， 使 blinding 形式化而非實質化。**

### 主大腦的獨立驗證（非複述）

我同意這個 blocker，而且認為它**不是可辯護的**。理由：

A-v2 的失敗型態是「測量層必須自己理解中文語義」。E 的 evaluator 換成 LLM 之後，
它面對的是**「哪一段文本比較像是被放進了不對的時間框」**——這與「判斷 `夜深了` 是不是 clause」
是同一個認知工作，只是難度可能較低。**而「難度較低」是假設，不是證據。**

A-v2 的前例正是：我們**假設**一個構念可以被 operationalize，寫了規則，結果 0/6。

> **因此 E2 的正確處置不是「argue 掉這個 blocker」，而是：這個 precondition 本身必須被測量。**
> 這正是 E2 這道 gate 存在的理由——它不該被辯論通過，它該被**跑**。

### 隱含的兩難（這是需要裁決的真正問題）

兩位 reviewer 的論證其實共同構成一個**閉合的兩難**：

```
target 陳述 expected direction  →  答案洩漏，evaluator 退化成 classifier
target 不陳述 expected direction  →  「correct direction」無定義，P(correct-direction) 不在量 direction
```

**設計在這一點上是 underdetermined 的**，而這不是 reviewer 的問題，是設計文件的缺口。

---

## §3 分歧與主大腦判讀

### 3.1 E0：Claude BLOCK vs Gemini CONCERN → 我採 BLOCK

Claude 的論證（construct circularity）成立：

> §1／§2 把 construct 定義成「可觀測、方向一致的 interpretation 改變」。
> **唯一的 operation 是 evaluator 的 verdict，所以 construct 是循環的。**
> §6 讓 0/1/2、binary、pairwise 三選一未定，§7 推薦 pairwise，§8 用 A/B/TIE/AMBIGUOUS——
> **這三個選項給出不同的 construct**，因為被評分的物件不同（單一 response／response pair／偏好）。
> §9 的 effect metric **只對 pairwise 有定義**。

**未固定 scale 與 construct 無效是可以共存的**——前提是 construct 必須**獨立於 scale** 被陳述。
這份設計**沒有**做到。所以 E0 BLOCK。

Gemini 只把 scale 未固定列為 non-blocking，是**較弱的判讀**。

### 3.2 E1：Claude CONCERN vs Gemini PASS → 我採 CONCERN，且**主大腦自查發現真實內部矛盾**

Claude 找到一個 Gemini 漏掉的結構性問題，我回頭查證設計文件，**確認它是真的**：

> `ΔI = ON − OFF` **需要 OFF 臂**。
> 但 §7／§8 的 pairwise 比較**只有 A 與 B（CORRECT vs MISMATCHED）**，
> **OFF 從未出現在任何 pairwise 比較中**。
> ⇒ **DiD 無法從 pairwise preferences 算出。**
> 而 §9 的 causal object 正是 `P(correct-direction preference)`。

**§4（四條件 + DiD）與 §7／§8（純 pairwise）在量測層面不相容。**

> 🔴 **這是我的落檔失敗，不是 reviewer 的發現。**
> 我把 Owner 的設計逐字轉錄成 canonical 文件，**沒有檢查各節之間的量測層相容性**。
> 這與這六輪的病相同：契約宣告了一件事，實作／其餘各節測的是另一件事，而沒有交叉檢查。

Claude 另外指出的 E1 問題（我認為皆成立，但屬次要）：
- `ΔI = ON − OFF` 未說明用哪一個 ON 臂
- IRRELEVANT 不在 DiD 公式內，卻在 §10 用於**不同 stimulus 類別**（電影例）→ 混淆 stimulus class 與 control status
- CORRECT vs MISMATCHED 同時改變 context 內容與 stimulus-context fit ⇒ evaluator 可能只是在反應 **incongruity**
- 各臂的 context 長度／類型未說明 matched
- 哪個 context 算 CORRECT 是 stimulus 作者定義的 ⇒ **direction 是 author-defined**

### 3.3 E3：Claude CONCERN vs Gemini PASS → 採 CONCERN，但 Gemini 的 PASS 很薄

Gemini 幾乎沒有處理 unit of analysis（只重複了 scale 問題），其 PASS 沒有實質支撐。

Claude 的指出一致成立：
- 「**應優先是**」是偏好，不是規則
- 同類 stimulus 內很可能相關（meal variants）
- **缺少把 replications 收斂成 pair-level outcome 的規則**
- **TIE 與 AMBIGUOUS 的處理規則缺席**
- **evaluator re-runs 是第二層未被處理的 non-independence**
- 「N calls = N samples」的回頭路徑，取決於日後選的 aggregation rule

### 3.4 E5：Claude CONCERN vs Gemini PASS → 採 CONCERN

Claude 指出的問題是結構性的：
- **effect metric 只在 pairwise 形式下有定義**
- **noise model 被推到 §18（E-Pilot）**，本階段尚未存在
- **control metric 在不同 stimulus 上**⇒「Relevant > Irrelevant」**沒有共同單位**
- 「Minimum Practical Effect」**沒有陳述來源**

### 3.5 E4：兩位一致 CONCERN，角度互補

- **Claude（操作面）**：randomization seed 如何儲存？誰持有 decoding map？model 與 prompt version 是否 pin？
  **generator 與 evaluator 是否為不同 model？** §14 保留在 metadata 的 exact timestamp **若傳給 evaluator 就是洩漏路徑**。
- **Gemini（概念面）**：§11／§17 的 integrity 層是概念列舉，**沒有指定任何技術強制機制**來保證 production isolation。

---

## §4 Gate 最終判定

> # 🔴 **E Design NOT PASSED — E2 = BLOCK（一致）**
> **不得進入 implementation。不得開 E-Pilot 工單。**

| 項目 | 狀態 |
|---|---|
| TA-2 Gate 2 | **INCONCLUSIVE**（不變） |
| E Design | **NOT PASSED — blocked at E2** |
| E-Pilot | **NOT AUTHORIZED** |
| Threshold | **NOT DEFINED**（兩 reviewer 均未提出數字 ✔） |

---

## §5 需要 Owner 裁決的問題（**主大腦不代答**）

兩位 reviewer 各自用不同措辂問了**同一個**問題。合併為一個：

> ### 🔴 裁決問題
>
> **Evaluator 可以被告知多少關於「預期 temporal direction」的資訊，
> 使得「correct direction」對 evaluator 而言有定義，
> 同時又**不洩漏**哪一個 response 來自哪一個 condition？**

這個問題的兩難（見 §2）：**給了 target 就洩漏，不給 target 就没有 direction 可量。**
設計在這一點上 underdetermined。**這需要一個設計決策，不是量測工作。**

**主大腦不提議答案。**任何回答都必須由 Owner 給出。

### 附帶需要裁決的第二項

> §4 的四條件 + DiD 與 §7／§8 的純 pairwise 在量測層不相容（§3.2）。
> **DiD 與 pairwise 哪一個是 E 的主體量測？** 抑或兩者需要不同的 measurement object？

---

## §6 誠實邊界

- 本檔**不**修改 E Design，**不**修補上述缺口，**不**提出替代規則。
- 兩份 review 皆**未**提出 threshold 數字；本檔亦**未**選擇任何門檻。
- 本檔**不**宣稱 E 的 construct 為假，只宣稱**這個設計尚未通過 E2**。
- **Gate 2 全程維持 INCONCLUSIVE。**
- 本輪 **0 LLM calls**（review 由外部模型執行，非本 repo 的 Soul backend）、0 production mutation。
