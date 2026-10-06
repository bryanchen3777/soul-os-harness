# North Star — Ontology Selection / Modeling Principle（**DCG 收斂：Terminal State**）

> **Discussion Convergence Gate 產物**。Round 1–5 完成，四項交付齊備。
> **Terminal State：DEFERRED TO MODELING / RESEARCH-INTENT DECISION**
> **本輪確立了一個 canonical 對象，也確立了它無法決定 ontology。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---


> 🔴 **Ontology Privilege Policy：Gate-only / Admissibility Closure（Owner 已裁定）**
>
> **Admission ＝ ISD + CEG；不套用第二層 coarsening 或 privilege principle。**
> **Review Trigger ＝ a new refinement or candidate passes both ISD and CEG；
> 該事件只觸發 Owner-level policy review，不自動切換到第二層。**
>
> **本檔的 construct / relation / vocabulary placement 為 Gate-only 下的 current state；
> 政策若變更，scope 內的 placement 必須重跑。**
>
> **政策全文：`docs/ONTOLOGY-PRIVILEGE-POLICY.md`**

## 1. Decision / Terminal State

> ### **Causal structure can determine a canonical boundary of causal distinguishability,
> but it does not by itself determine which coarser representation should receive
> independent ontological privilege.**
>
> **因果結構可以決定一個 canonical 的因果可區分邊界，但不能單獨決定較粗的哪些表示
> 應獲得獨立的 ontology 地位。**

$$\boxed{ C^\* \;\text{是固定 intervention semantics 下的 canonical causal quotient} }$$
$$\boxed{ Causal\ facts \rightarrow canonical\ causal\ distinctions }$$
$$\boxed{ Causal\ facts \not\rightarrow unique\ ontology\ granularity }$$
$$\boxed{ \text{Ontology selection therefore contains a non-causal modeling choice} }$$

**Terminal State：`DEFERRED TO MODELING / RESEARCH-INTENT DECISION`**

---

## 2. 🔴 十八次 DCG 的完整鏈

```
causal facts
    ↓
causal distinctions
    ↓
canonical quotient / boundary
    ↓
possible coarsenings
    ↓
   ✕ causal facts no longer uniquely choose
    ↓
research / modeling principle
    ↓
privileged ontology
```

> ### **「客觀因果結構」與「研究者選擇的 ontology 粒度」不是同一層問題。**
>
> **但後者不是「任意」——它受到因果結構嚴格約束，只是在約束所留下的選擇空間內，
> 仍需要一個額外的 selection rule。**

---

## 3. 🔴 這才真正解開 #16 的 A / B

```
#16
A：六項是真的完整
B：六項只是 boundary 太粗
→ 當時判為不可分辨

#17
CausalFacts ⇏ UniqueGranularity

#18
CausalFacts → Canonical Distinguishability Boundary
        ⇏ Unique Privileged Granularity
```

**A / B 並不是「誰掌握更多 causal facts」的問題。**
**它是在 canonical causal boundary 上選哪個 quotient / representation。而這個最後選擇
需要研究者額外指定的 modeling principle。**

**#16 的「不可分辨」被 #18 取代為一個更精確的描述：邊界可分辨，格中的元素不可分辨。**

---

## 4. 三個候選判準的失敗模式

### 4.1 Predictive Adequacy

**需要指定** Target Space + Prediction Task + Loss/Utility。**三者皆外生。**

$$\text{MDL applies} \not\Rightarrow \text{MDL settles ontological privilege}$$

它回答「哪個 model 給出最短的總描述」，我們問「哪個 causal partition 應有獨立的
ontology 地位」。**兩個問題不同。**

**可以出現：$O_1$ 更短更壓縮；$O_2$ 更長，但揭露一個因果上重要、只是資料中少見的 distinction。
此時 MDL 偏向 $O_1$——但 MDL 沒有算錯，是我們從未授權「壓縮率」等同「ontology privilege」。**

### 4.2 MDL

| 版本 | premise-free？ | 可用？ |
|---|---|---|
| Universal / Kolmogorov | ✔ 理論上接近 machine-independent | **✘ 不可計算，無法裁決實例** |
| Practical MDL | ✘ 需要選 Code / ModelClass / Prior / Cost | ✔ 可計算 |

$$\text{Ranking}_{C_1}(O_1,O_2) \neq \text{Ranking}_{C_2}(O_1,O_2)$$

$$\boxed{ \text{MDL 自身的 neutrality 未被建立，但它即使完全 neutral 也不會自動成為 ontology truth criterion} }$$

**精確表述（Owner 修正）：Ontology 可以作為 compression / model-selection object，
但「可被壓縮」不是 ontology 應該被選擇的理由。**

### 4.3 Invariance

$$\boxed{ \text{Invariance} \neq \text{Granularity Selector} }$$

**循環的具體形式：**

$$\operatorname{Aut}(C_{\text{coarse}}) \neq \operatorname{Aut}(C_{\text{refined}})$$

| 模型 | invariance 對 Memory-X / Memory-Y 的裁決 |
|---|---|
| 粗模型（X、Y 對稱） | 這個區分不是結構性的 ⇒ **拒絕 refinement** |
| 細模型（X、Y 被區分） | aut 群縮小 ⇒ refinement 看起來又 invariant |

> **Invariance 會系統性地拒絕一切能打破對稱性的細分——
> 而那個細分正是能證明自己結構性的那個。**

---

## 5. 🔴 $C^\*$：本輪真正找到的 canonical 對象

$$s_1 \sim s_2 \iff \forall I,\ O(I(s_1)) = O(I(s_2))$$

$$\boxed{ C^\* = \text{causal quotient：所有干預下不可區分的 states 的等價類} }$$

- 由 $C$ 決定
- 對 relabeling invariant
- 唯一（在適當同構意義下）
- 是**最細的 causally legitimate 分割**

> ### ⚠️ 但 $C^\*$ **不是無條件 canonical**
> $$ \boxed{ Intervention\ semantics \not\equiv causal\ facts\ alone} $$
>
> **它條件於干預類別與 outcome / observation semantics。
> 而那些是架構宣告的，不是由 causal facts 唯一推出的。**

**這是本輪最後剩下的外生點。**

### 5.1 兩個已修正的錯誤

| 主大腦的說法 | 修正 |
|---|---|
| $2^{\lvert\text{atoms}\rvert}$ 個合法分割 | **是 Bell number $B_n$**（set partitions），不是 $2^n$ |
| $L(C) = \{\text{全部 arbitrary merges}\}$ 都 legitimate | **不是每個形式上的 partition 都自動具有 ontology legitimacy** |

**正確流程：**

```
Causal structure
      ↓
canonical finest causal distinguishability quotient C*
      ↓
possible coarsenings / representations
      ↓
需要額外原則決定哪些 coarsening 值得被 privileged
```

---

## 6. 四種情況

| | 內容 | 結果 |
|---|---|---|
| **A** | causal facts 足以區分 refinement | 有客觀 constraint |
| **B** | causal facts 對 refinement **對稱** | **invariant criterion 不能打破 symmetry（真 theorem）** |
| **C** | 多個 refinement 相容、無一被 privileged | underdetermined |
| **D** | $C$ 固定時，legitimate 分割構成一個 canonical 格 | **問題是在格中選元素，不是決定格本身** |

**B 是本輪可證明的定理：causal symmetry 無法被內生打破。**
**D 是它的結構後果：不可打破性之所以終局，正因為合法性的邊界早已被 $C$ 畫定。**

---

## 7. Open Question

**Soul OS 要選哪一種 coarsening / modeling principle，作為研究上 privileged ontology？**

**這已經不是 causal discovery 問題。屬 Owner 的 modeling / research-intent 決策。**

---

## 8. Non-Claims

**本輪沒有證明：**

- ❌ 不存在任何合理的 ontology-selection principle
- ❌ $C^\*$ 就是 Soul OS 應採用的 ontology
- ❌ 所有 $C^\*$ 的 coarsenings 都 equally legitimate
- ❌ intervention semantics 本身完全不能由更高階理論限制
- ❌ 六項 constructs 是唯一應被保留的粒度

---

## 9. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | Predictive Adequacy ≠ ontology-independent criterion（需 target / task / loss）。預測 MDL 會把問題轉移到 code / model class |
| **Round 2** | 修正主大腦的「ontology 不能被當成 code」。**確認 `MDL applies ⇏ MDL settles ontological privilege`。**阻止「ontology selection 完全不是 fact-question」這個過強的 meta-conclusion |
| **Round 3** | Invariance 的 $G = \operatorname{Aut}(C)$ 不需先驗選擇，但 $\operatorname{Aut}(C)$ 依賴 $C$ 的粒度 ⇒ 循環。**此處主大腦錯言「不存在 canonical 對象」——Round 5 收回** |
| **Round 4** | **阻止主大腦的 $\forall F$ 否定命題**（那是對所有 criterion 的否定，需要 meta-theorem 而主大腦沒有）。建立 causal symmetry 不能被內生打破的真 theorem。提出 universal property 路線作為最後一刀 |
| **Round 5** | **指出 $C^\*$ 存在且 canonical**（推翻 Round 3 的否定判斷）。建立 admissible 格 vs 格中元素的區別。**Owner 修正 Bell number 與「每個 partition 都 legitimate」兩個錯誤** |

---

## 10. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R3** | 「不存在一個既外部、又 premise-free、又能唯一決定粒度的判準」 | **過強。**那是對所有 criterion 的否定命題，需要真正的 meta-theorem。**Owner R4 攔住。結論保留、措辭收回** |
| 2 | **R3** | 「每個判準的裁決都取決於一個它應該裁決的 modeling choice」 | **過強。**$C^\*$ 不依賴 modeling choice。**主大腦於 R5 自行收回** |
| 3 | **R3** | 「不存在任何由 causal structure 唯一刻畫的 universal property」 | **直接錯誤。**$C^\*$ 就是。**主大腦 R3 提出、又在 R5 推翻自己**——且推翻時未先做 §7 那個 existence check。**這是 DCG #17 §10「嚴格化／推廣前先驗證」的直接重演** |
| 4 | **R5** | 「atoms 的任意合併有 $2^{\lvert\text{atoms}\rvert}$ 個，且全部 legitimate」 | **數量錯**（是 Bell number $B_n$），**且 legitimacy 是獨立問題**。Owner R5 修正 |

> ### 第 3 項是本輪最重要的自我修正
> **我在 Round 3 對 Bry 的 universal-property 提議回了「不存在」，
> 卻沒有先檢查 $C^\*$ 是否就是那個例子。**
> **我在同一個研究裡花兩輪處理「未驗就宣告不存在」，然後自己又做了一次。**
> **這不是新錯誤，是既有錯誤的重演。**
>
> **可推廣的處置：當有人提出一個「是否存在 X」的問題時，
> 先窮舉我知道的候選 X，再回答不存在。不要從「我試過的都不行」推出不存在。**