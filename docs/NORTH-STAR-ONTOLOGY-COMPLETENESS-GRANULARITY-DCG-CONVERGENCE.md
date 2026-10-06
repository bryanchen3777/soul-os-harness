# North Star — Ontology Completeness / Granularity Criterion（**DCG 收斂：Round 3 / 5，early convergence**）

> **Discussion Convergence Gate 產物**。Round 1–3 提前收斂，四項交付齊備。
> **Terminal State：UNIDENTIFIABLE UNDER CURRENT OBSERVATION SURFACE**
> **本輪不是「沒有第七項」，而是「construct 粒度不是 causal facts 能唯一決定的問題」。**
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

## 1. Decision

> $$\boxed{ \text{Causal ontology gives constraints on granularity; it does not supply its own unique granularity choice.} }$$

$$\boxed{ \text{Causal facts can constrain construct granularity, but do not uniquely determine it.} }$$

$$\boxed{ \text{Ontology Completeness cannot be established by the current causal ontology alone.} }$$

**Terminal State：`UNIDENTIFIABLE UNDER CURRENT OBSERVATION SURFACE`**

**不是因為沒想出夠好的 test，而是因為題目本身不是 causal facts 能唯一決定的問題。**

---

## 2. 🔴 IDC 的失敗，以及它的正確診斷

### 2.1 Proposed criterion（Round 2）

給定干預集合 $\mathcal I$，對候選 component $M$ 定義 intervention signature：

$$\Sigma(M) = \{\, O(I_M = x) \mid x \in Dom(M) \,\}$$

$$\boxed{ M \text{ 是獨立 causal module} \iff \begin{cases} \text{存在獨立 intervention handle} \\ \text{該 intervention 改變 downstream causal outcome} \\ \text{其 effect 不能由其他 module 的干預等價重現} \end{cases} }$$

### 2.2 Round 3 的技術修正：Clause ③ ≠ exogeneity

主大腦 Round 2 曾主張「clause ③ 嚴格化後就是 exogeneity / non-mediation」。
**Owner 指出此等價太強，並給出反例：**

$$ M \rightarrow X \rightarrow Y \quad\text{但同時}\quad M \rightarrow Z,\qquad O = (Y, Z) $$

即使 $X$ 完全中介 $M$ 對 $Y$ 的影響，`do(X=x)` **仍然不能**重現 `do(M=m)` 對 $Z$ 的 effect。

$$\Longrightarrow \text{mediated} \not\Rightarrow \text{effect fully reproducible by intervention on mediator} $$

**Clause ③ 的正確內容是：在指定 intervention / outcome surface 上，該 factor 的完整
causal signature 是否可由其他 intervention signature 模擬。這是 causal substitutability /
intervention-equivalence，不是 exogeneity。**

> **代價：主大腦 Round 2 的「嚴格化的 IDC 會收斂回 World」這個診斷，
> 建立在錯誤的 clause ③ 解讀之上，因此該診斷撤回。**
> **但結論（IDC 不給出 granularity）存活，且理由變得更好——見 §2.3。**

### 2.3 修正後仍然失敗，而且失敗得更清楚

$$causal\ distinguishability \;\downarrow\; intervention\ non\text{-}equivalence \;\downarrow\; \text{??}\; \downarrow\; new\ construct$$

**那個 `??` 仍然沒有出現。**因為一個 factor 可以 interventionally distinguishable、
causal effect 不可由另一 factor 完整模擬，**但仍然只是既有 construct 裡的一個 state coordinate。**

例：$AgentState = (temperature,\ memory,\ policy)$——三者都可能具有完全不同的
intervention signature，**但這不會讓 temperature / memory / policy 自動變成三個 constructs。**

> $$\boxed{ \text{causal distinguishability} \not\Rightarrow \text{construct-level distinction} }$$

**這不是 IDC 特有的缺陷。**

---

## 3. 🔴 本輪最深的結果：唯一粒度不存在

### 3.1 同一個 causal system 有無限多個「合理粒度」

設 $S = (a,b)$，則可以描述為：

| 粒度 | 描述 |
|---|---|
| 粗 | $S$（一個 state） |
| 中 | $(a,b)$（兩個 coordinates） |
| 細 | $(a_1, a_2, b_1, b_2, \dots)$ |

**這些描述可以保留完全相同的 causal behavior。**反向的 merge 也一樣：
$(a,b) \rightarrow c$，只要 downstream causal behavior 在選定 intervention surface 下仍被保留。

$$\boxed{ CausalFacts \not\Rightarrow UniqueGranularity }$$

**這個斷言比「我們沒有找到 criterion」強很多：即使 causal facts 完全已知，
仍然存在多個 causal-equivalent 但粒度不同的 representation。**

### 3.2 它解開了 #16 的 A / B

```
A：六項是真的完整
B：六項只是 boundary 太粗
```

**兩者可能同時與全部 causal facts 相容。**
粗 ontology 能容納 causal effect，不代表它錯；
細 ontology 能把同一 causal effect 拆得更細，不代表它比較真。

**這是為什麼 #16 無法自己解決。**

---

## 4. 🔴 主大腦補充：gates 允許 refinement，所以 ontology 是 extensible 而非 maximal

### 4.1 兩讀法的真正差異位置

$$\boxed{ A \text{ 與 } B \text{ 的差異只在「用哪一組 gates」，不在「用哪一個 ontology」} }$$

**所有 causal facts 在兩種讀法下完全相同。**不同的是我們挑選准入規則的方式。
而 gate 是我們**選的**，不是 causal structure 推出的。**所以 A / B 不是事實問題，是規則問題。**

### 4.2 而我們的 gates 事實上允許 refinement

把 Memory 沿一個**因果 operative 的軸**細分成 Memory-X / Memory-Y：

| gate | 判定 | 理由 |
|---|---|---|
| **ISD** | ✔ | 兩者在 Memory / Growth / … 上相同，卻在新軸上不同 ⇒ 通過 |
| **CEG** | ✔ | 若該軸不可由既有 vocabulary 表達，它引入新的 causal-functional role ⇒ 通過 |

> ### **⟹ 六個 constructs 是一個 stopping point，不是一個 maximum。**
> **⟹ #16 的「internal closure」從未建立，而 Owner 在 #16 的措辭收緊是正確的。**

**這把 #16 的非結論（internal closure ≠ completeness）從「epistemic 上的保留」
變成「機制上的事實」。**

**注意條件**：只有在 refinement 軸本身具有**可區分的 causal role** 時才被 admission。
任意的 partition 沒有 causal role，仍被 CEG 排除。**所以 gates 不是完全鬆的——
它排除任意切分，但允許因果上有意義的細分。**

---

## 5. 🔴 Invariance 的真正位置

Invariance 可以作為 **necessity / robustness 條件候選**，因為它能排掉：

- implementation artifact
- naming artifact
- storage-location artifact
- encoding artifact

**但它不能單獨決定：**

- which causal-equivalent systems count as the same
- which refinement is ontologically privileged

> **Invariance 相對於「選定的同構類」而言是 invariant。
> 而「哪些變換算同構」本身就是一個選擇。把問題推高了一層，沒有消掉。**

---

## 6. 缺少的是什麼

原本我們在找：

$$\boxed{ causal\ criterion }$$

而現在的答案比較像：

$$\boxed{ causal\ constraints \;\oplus\; external\ modeling\ principle }$$

外部原則的可能候選：minimum description length ／ explanatory usefulness ／
intervention sufficiency ／ predictive adequacy ／ compositionality ／
stability under a specified class of transformations。

**但這些都不再是「純 causal ontology 自己推出的真理」。**
**它們是選擇 ontology 粒度的 research criterion——是 research intent，不是 engineering。**

---

## 7. Open Questions

- **用什麼 external modeling principle 選擇 ontology 粒度** → **research intent，屬 Owner 決策**
- **Temporal Semantics 是否升格為 convention** → 仍為 convention candidate
- **7 項 UNPLACED/OPEN 的 rename test** → 未啟動
- **是否存在一組 gates 使得 refinement 被擋下** → 本輪未處理

---

## 8. Non-Claims

**本輪沒有證明：**

- ❌ 六項是完整的 ontology
- ❌ 第七 construct 不存在
- ❌ 沒有任何可行的 completeness criterion
- ❌ 所有 refinement 都會被 gates 通過（只有因果 operative 的軸會）
- ❌ causal facts 對 construct 粒度毫無約束力（**它們能排除 causal impossible，並證明真實的 causal difference**）
- ❌ 實用原則（MDL / usefulness / adequacy）是唯一可用的外部原則

---

## 9. 三輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1–2** | 提出 IDC。**主大腦主張 clause ③ = exogeneity，並據此宣稱嚴格化的 IDC 會收斂回 World** |
| **Round 3** | **Owner 修正 clause ③ ≠ exogeneity**（$M\to X\to Y$ 且 $M\to Z$ 的反例）⇒ 主大腦該診斷撤回。修正後 IDC 仍失敗，但失敗理由更乾淨。**建立 `CausalFacts ⇏ UniqueGranularity`。**主大腦補充 gates 允許 refinement ⇒ ontology 是 extensible 而非 maximal |

---

## 10. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R2** | 「clause ③ 嚴格化後就是 exogeneity / non-mediation」 | **等價太強。**即使效果被中介，多出口的 signature 仍不可由單一 mediator 重現。Owner R3 修正。**依此建立的「嚴格 IDC 收斂回 World」診斷同步撤回** |
| 2 | **R2** | 「Construct 粒度完全不是 causal 問題」 | **太絕對。**Owner R3 修正為：`Causal facts can constrain construct granularity, but do not uniquely determine it.` **採用修正版** |
| 3 | **R2** | 把三個反例當成三個獨立問題 | **它們是同一個問題的三種樣子。**三者在「缺少從 causal distinction 到 construct privilege 的橋」這一點上閉合 |

> ### 第 1 項的方法論價值
> **主大腦 Round 2 用一個錯誤解讀建立了一個看似精確的定理（clause ③ = exogeneity ⇒ 收斂回 World）。
> 修正之後結論（IDC 不給出 granularity）仍然成立，但理由完全不同。**
>
> **這說明：當一個論證把候選準則「嚴格化」到一個熟悉概念（exogeneity）時，
> 要先確認那個嚴格化本身成立——否則整個下游推論都建在鬆的地基上，
> 即使結論碰巧是對的。**