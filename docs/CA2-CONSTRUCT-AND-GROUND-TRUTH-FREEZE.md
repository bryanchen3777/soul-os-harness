# CA-2 — Construct Boundary 與 Ground-Truth Machinery（**FROZEN**）

> **Freeze 裁定者**：Owner（Bryan）+ Engineering Brain，2026-10-04 14:27
> **狀態**：
> - **CA-2 Construct Boundary：FROZEN**
> - **CA-2 Ground-Truth Machinery：FROZEN**
> - **CA-2 Measurement / Evaluator / Threshold：OPEN**
>
> **本檔不含 measurement design、不含 evaluator、不含 threshold、不授權任何 pilot。**

---

## §1 Construct（**FROZEN**）

> ### CA-2
>
> 研究 Soul 是否能**辨識並適當運用其 architecture-defined authorship boundary**。
>
> 英文原句：
> > CA-2 investigates whether a Soul can recognize and appropriately apply the
> > boundaries of what it can author within its own architectural state and affordance space.

### 核心 = **ACTIVATION + RECOGNITION**

**Knowledge State 是 control axis，不是核心 construct。**
它的作用是防止把「不知道世界」誤讀成「不知道自己的 capability」。

### 刻意不包含（construct 層面已切掉）

❌ boundary 必須 self-generated
❌ boundary 必須未被 prompt 提供
❌ boundary 必須屬於 identity / personality ownership
❌ boundary 必須導致 action selection
❌ boundary 必須由 tool call 證明
❌ physical write implementation detail
❌ decision authority

---

## §2 Ground-Truth Machinery（**FROZEN**）

### 三步推導（無研究者判斷）

```
1. Persistent-store transition
   指名一個「架構上持久化 store 的具體狀態變化」
            ↓
2. Necessary-cause closure
   列出該 transition 的所有必要原因
            ↓
3. Architecture-derived classification
   全部必要原因都在 Soul action space 內？ → CAN
   否則                                        → CANNOT
```

### §2.1 Author 的定義（FROZEN）

> ## **Author = 該 transition 的所有必要原因，都落在 Soul 自己的行動空間內。**

**不是** physical write（implementation detail）
**不是** decision authority（那會把「貢獻者」也算成 author）
**是** necessary-cause closure

### §2.2 三個關鍵推論（FROZEN）

| Transition | 必要原因集合 | 判定 |
|---|---|---|
| 提交這段經歷 | {Soul submits} | **CAN** — Soul author |
| 閘門接受 | {Soul submits, 閘門放行} | **非 Soul 空間** → 不在 Soul space |
| Pattern 持久化 | {Soul submits, 閘門, SAGE 判定, writer} | **非 Soul 空間** → 不在 Soul space |
| 另一個 Soul 的 InnerLifeEvent | {A 的訊息, **B 的 interpretation**, B 的系統} | **A 不是 author** |

### §2.3 Soul 自己的 state-transition space 是**推導結果**，不是前提假設

這取代了「authorable state-transition boundary」那個需要預先定義的詞。
**分類是確定的，沒有可被任意切割的自由度。**

### §2.4 Ground-truth classification（FROZEN）

```
CAN              architecture mapping，Soul 有完整 authoring path
CANNOT           architecture mapping，無完整 authoring path
NO_SOLE_AUTHOR   → exclude，不是 capability class
UNREACHABLE      → evaluator eligibility problem，不是 capability class
```

> **`CAN_BUT_UNKNOWN` 已刪除。** 它原本把「不知道世界現在是什麼」與
> 「不知道自己能不能做什麼」塞進同一個 state，是分類錯誤。
> 判準：**那個 unknown 是在講世界（正交），還是在講自己（就是本 construct）？**

---

## §3 Observation Dimensions（FROZEN 為維度；operationalization **OPEN**）

| 軸 | 內容 |
|---|---|
| **ACTIVATION** | capability-boundary consideration 有沒有**自發**進入 cognition？ |
| **RECOGNITION** | 若進入，邊界判斷是否正確？ |
| **KNOWLEDGE STATE** | 對 world / 當前狀態的陳述是否與 ground truth 一致？（**control axis**） |

### §3.1 Recognition 的方向性（FROZEN，結構性需求，不是 pass threshold）

```
CAN     + correct           ✔
CAN     + under-estimate    （以為不能，其實能）
CANNOT  + correct           ✔
CANNOT  + over-estimate     （以為能，其實不能）  ← 有實際行為風險
```

**兩種誤差不得壓成同一個 "incorrect"。** CANNOT 上的 over-estimate 會真的造成傷害。

### §3.2 Activation 的優先性（FROZEN）

> **直接追問「你能不能做 X」得到的正確答案，最多算 E1 baseline，不是 CA-2 核心 evidence。**
> **只有 spontaneous activation 才有研究價值。**

與 TA-2 的同構：

| | TA-2 | CA-2 |
|---|---|---|
| 進入 cognition 的東西 | temporal information | capability-boundary consideration |
| 污染來源 | explicit temporal prompt | explicit capability question |
| 有效的 evidence | spontaneous activation | spontaneous boundary activation |

### §3.3 Knowledge State 的計分方式（FROZEN 原則）

> **比對 ground truth 計「準不準」，不比「講了幾次」。**

理由：**「我需要確認一下」有飽和路徑。** 對所有事情都說「我需要確認」可以同時看起來
謹慎、有自我模型、情境敏感。**這是 CA-2 版的 generic refusal gaming。**

---

## §4 Item Admissibility（FROZEN）

一個東西有資格成為 CA-2 item，必須通過：

```
1. 指名 persistent store
2. 指名該 store 的具體 state transition
3. 能列出該 transition 的必要原因
4. 能判斷必要原因是否全部落在 Soul action space
5. 排除 NO_SOLE_AUTHOR
6. 排除 UNREACHABLE
7. 不要求 Soul 使用研究者 label
```

**第 7 條**：Soul 只自然作答（例如「我可以提交這段經歷，但最後是否被保存不是我能決定的」）。
**Evaluator 才做 mapping。** 若要求 Soul 輸出 `CAN`／`CANNOT`，等於讓 Soul 的輸出格式
就是 ground truth 結構本身——**那正是 §2.1 那條反循環規則的違反。**

---

## §5 Scope Exclusions（FROZEN）

| 排除項 | 理由 |
|---|---|
| **Access / read-side capability** | 沒有與 authorship 等價的 counterfactual primitive。放進來會立刻引入 access permission / data existence / availability / epistemic access，重新膨脹 construct |
| **Cross-Soul inner-state authorship** | A 可造成 causal influence，但不因此取得 authorship（推導結果，非價值判斷） |
| **Identity / personality ownership** | 屬 selfhood 議題，**刻意不碰** |
| **Agency / action selection** | `Capability Awareness ─► Motive ─► Decision ─► Agency`，**不是** `─► Action` |
| **物理寫入實作細節** | implementation detail，Soul 不需要知道 |
| **Downstream causal influence** | 與 authorship 分開 |

---

## 🔴 §6 未 freeze，且已記錄的警告

> **以下全部 OPEN：**
>
> - Activation 如何 observable
> - Recognition 如何從自然語言 response 判定
> - Knowledge State 如何測
> - stimulus 如何構造
> - control / contrast condition
> - evaluator
> - scoring
> - threshold
> - sample size
> - pilot design

### §6.1 🚨 最危險的一條已記錄警告

> **「沒有 verbalized boundary」不等於「沒有 internal consideration」。**
>
> Soul 沒有說「我不能……」，**不能**直接推論 capability boundary 沒有進入 cognition。
>
> **Activation 是要觀測的 construct dimension，但如何 operationalize Activation 尚未決定。**
>
> ⚠️ **不得因為 Activation 重要，就偷偷把它推成「只有明講 capability 才算 activation」。**
> **那是 measurement decision，不是 construct decision——會重演 TA-2。**

### §6.2 Freeze 邊界的精確陳述

> **可以 freeze 的是：construct boundary ＋ ground-truth machinery。**
> **不能 freeze 的是：evaluator、scoring、threshold、pass/fail。**

---

## §7 跨 Capability 方法論（TA-2 → CA-2 繼承）

### §7.1 單向版本（已確立）

> **context provision ≠ awareness**
> - TA-2：系統有沒有把時間告訴 Soul ≠ temporal cognition
> - CA-2：系統有沒有把 capability 邊界告訴 Soul ≠ capability awareness

### §7.2 🔴 雙向版本（**禁止兩種方向的偷換**）

```
External context ──may influence──▶ Observed behavior ──▶ Evidence ──▶ Construct interpretation

❌ 不能推：有 external context  ⇒  沒有 awareness
❌ 不能推：沒有 external context ⇒  就是 internal cognition
```

> **第二個禁止同樣重要。**「沒有被 supply，所以一定是 cognition」與
> 「被 supply，所以一定不是 cognition」**是同一個錯誤的兩個方向。**

---

## §8 本 freeze 的方法論註記

> **不是**「七輪都沒有依賴研究者的判斷」（不可證明的 meta-claim）。
>
> 而是：
> **「研究者原本需要做的判斷，逐步被轉化成可由 architecture、causal structure
> 與明確 exclusion rules 推導的判定。」**
>
> 後者可被 audit，前者不可。

**這是 Soul OS 這一輪實際找到的東西：不是「正確答案」，
而是一套讓答案不再由 Owner 當下直覺決定的 machinery。**

---

## §9 下一階段（**OPEN，且不得在本檔決定**）

> 在這個已 freeze 的 construct 上，
> **什麼 observation 才足以證明 ACTIVATION 與 RECOGNITION，
> 而不重新把 prompt、evaluator 或 measurement design 塞回 construct 裡？**

**不開票、不實作、不進 pilot。**
