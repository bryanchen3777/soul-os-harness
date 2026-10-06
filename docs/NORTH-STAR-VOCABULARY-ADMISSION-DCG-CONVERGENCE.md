# North Star — Vocabulary Admission / Descriptive Role Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Terminal State：INCONCLUSIVE — no universal vocabulary-admission gate identified under current ontology**
> **本輪確立：Vocabulary privilege 不能偷偷冒充 causal entailment。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Terminal State

> $$\boxed{ \textbf{INCONCLUSIVE — no universal vocabulary-admission gate identified under current ontology} }$$

**措辭強度經 Owner 收緊：identified ≠ proven impossible。**

**⚠️ 本檔不得寫成「Vocabulary 永遠沒有 admission criterion」。**
只能寫：目前未識別出 universal vocabulary-admission gate。

---

## 2. 🔴 Conclusion 4（本輪最根本的結果）：兩者是不同輸入域

**Construct gate 處理的是：**

$$P(\text{causal structure})$$

**Vocabulary placement 處理的是：**

$$P(\text{linguistic / naming choice})$$

因此目前沒有從 causal structure 直接得到「researcher should privilege this name」的 bridge：

$$\boxed{ CausalStructure \;\Rightarrow\; Describability }$$
$$\boxed{ CausalStructure \;\not\Rightarrow\; VocabularyPrivilege }$$

### 2.1 🔴 Scope 限定（Owner 收緊，不可刪）

**不要寫成普遍形上學定理。安全版本：**

> **Under the current formalization, vocabulary membership is not represented as a
> predicate over the causal state-space, so the admitted causal structure provides
> no input from which a unique vocabulary privilege can be entailed.**

**我們證明的是「目前模型的輸入域沒有這個 bridge」，
不是「語言哲學的宇宙定律」。**

---

## 3. Conclusion 1：三型皆無法由 admitted structure 唯一推出

$$\text{V1 / V2 / V3} \;\not\Rightarrow\; \text{vocabulary admission}$$

**R3 逐型結果：**

| 型 | 詞 | Denotation 可由結構定義？ | Admission 由結構唯一推出？ |
|---|---|---|---|
| **V1 Descriptive Family** | Awareness | ✔（給定 grouping） | **✘** grouping 選擇不由結構決定 |
| **V2 Projection / Derived** | History | ✔（給定 projection） | **✘** composition 選擇不由結構決定 |
| **V3 Unresolved Reference** | Lived Experience | **✘** referent 尚未被結構定義 | **✘** |

**V2 最重要：History 可以有完全客觀、deterministic 的 denotation，
同時仍然是 modeling choice 才被命名成 History。**

> **這就是 #17 / #18 的同一刀，只是從 construct 降到 vocabulary。**

---

## 4. Conclusion 2：No universal gate ≠ No substantive rules

$$\boxed{ No\ Universal\ Gate \neq No\ Substantive\ Rules }$$

**已存在的 per-kind substantive rules：**

| 詞 | 規則 | 出處 |
|---|---|---|
| **Soul** | 「Soul may name the research object and preserve the research question, but Soul itself may never serve as evidence for an ontological conclusion.」＋ enforcement target 是 claims 而非 identifiers | **DCG #3 §2／§2.1** |
| **Awareness** | 「An "awareness of X" phrase is only shorthand for an explicitly defined, testable Agent–X relation. The phrase itself never constitutes evidence or a construct.」 | **DCG #8 §5** |

**DCG #8 §5 明文聲稱它「直接擋住無限制生成的 vocabulary tree」：
`world / social / emotional / temporal / capability awareness …`**

### 4.1 🔴 這兩條不是 entailment，是 **stipulation with declared scope**

$$\boxed{ \#3\ §2 \ \text{與}\ \#8\ §5 = \text{stipulations with declared scope} }$$

**它們不是從 ontology 推出的必然規則——它們是被宣告的規則，然後宣告其 scope。**

**這就是為什麼 role-indexed 治理是唯一可行形式：
每個 kind 的 substantive rule 都是 stipulation，
而每個 stipulation 依 governance invariants 必須自行宣告
Parameter + Semantics + Scope + Change Procedure。**

---

## 5. Conclusion 3：No universal gate ≠ No governance

**Vocabulary placement 受統一 governance process 約束，
但不因此得到一個 universal causal filter。**

**Governance 架構（Owner 裁定）：**

```
Ontology / Placement Governance
│
├─ Construct placement
│  └─ ISD + CEG
│     └─ Gate-only policy
│        └─ scope: all construct-placement decisions after adoption
│
└─ Vocabulary placement
   └─ Process governance + per-kind substantive rules
      ├─ declared rationale required
      ├─ parameter / semantics / scope / change procedure
      └─ no universal causal admission gate
```

> ### **🔴 Gate-only 的 scope 維持 construct-only，不擴張到 vocabulary。**
> **否則會把 construct admission gate 偷偷變成 vocabulary admission gate，
> 與本輪結果正面矛盾。**

$$\boxed{ Construct = filtered\ admission } \qquad \boxed{ Vocabulary = governed\ recording + per-kind\ rules } $$

---

## 6. Conclusion 5：Content Ceiling

**Vocabulary privilege 是 naming / modeling layer 的決定，
不是目前 causal ontology 能自行推出的事實。**

---

## 7. Conclusion 6：Governance Status（Owner 裁定 A — partial governance completion）

| 詞 | Substantive rationale | Governance parameter | 狀態 |
|---|---|---|---|
| **Soul** | QPF / question-preserving（#3 §2） | **✔ QPF（#19 凍結）** | **Governable / parameterized** |
| **Awareness** | Agent–X relation expansion（#8 §5） | **✔ 但「testable」未形式化** | **Governable / partially formalized** |
| **History** | projection | **✘** | **Placement retained, admission rule OPEN** |
| **Lived Experience** | unresolved subject-level reference | **✔ QPF（#19 §30.2，QPF=1）** | **Placement 已移入 QUESTION 層（#19 §30.4）；governance parameter 尚未宣告** |

> ### 🔴 DCG #19 §30 的後續修正（本檔 §7.1 已被取代）
>
> **Lived Experience 的 QPF = 1**（#19 §30.2）。
> **QPF(LE)=1 使 QPF 成為它的 governance parameter 候選——這推翻本檔 §7.1 的「無可宣告 parameter」。**
>
> **⚠️ 但 parameter 尚未依 invariant 2 宣告（parameter + semantics + scope + change procedure）。
> 依 invariant 1，在宣告之前它**尚非 frozen rule**。**
>
> **Placement 已移入 QUESTION 層**（#19 §30.4），因為 #9 保留它的理由正是「該部分沒有 identification power」——那是一個 open question。
>
> **本檔 §6.6 的對應列亦已更新。**

### 7.1 不得把 History / Lived Experience 稱為「已 frozen 但 non-compliant」

**那會直接違反 invariant 1：`Undeclared parameter ⇒ not a frozen rule`。**

**正確狀態：**

```
History
├─ canonical semantic description: preserved
├─ vocabulary placement: legacy / under governance review
└─ frozen admission rule: NOT ESTABLISHED
```

**不是 reject、不是 delete、不是推翻 #9。**
**只是 #9 的 placement 沒有足夠的治理 parameter，
不能被重新表述成一條已凍結的 vocabulary-admission rule。**

### 7.2 🔴 Awareness 有一個未封口的小洞

$$AwarenessOf(X) \Rightarrow X\ \text{must expand to an explicitly defined, testable Agent–X relation}$$

**「explicitly defined」可對照 admitted RELATIONS 清單檢查。
但「testable」沒有形式定義。**

> ### **Awareness governance = partially formalized。**
> **「testable」必須被記錄為未宣告詞，不得默認成 frozen parameter semantics。**

---

## 8. 🔴 本輪真正打穿的地方

> $$\boxed{ \textbf{Vocabulary privilege 不能偷偷冒充 causal entailment.} }$$

**這與 #17 / #18 是同一條大原則，在另一個 layer 再次被驗出來。**

**而「新例外」的入口也被封住了：**

```
New vocabulary principle
        ↓
不是因為發現了它就自動有效
        ↓
必須成為 declared stipulation
        ↓
Parameter + Semantics + Scope + Change Procedure
```

---

## 9. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 提出 `Declared Semantic Role ∧ Non-Construct`，觀察到三型（V1/V2/V3）。**立即自我否定**：`Descriptive Role ⇒ Admission` 太寬，幾乎任何詞都能被包成 family descriptor |
| **Round 2** | 收緊 `mere alias` ＝ **與單一 admitted entity / mechanism 的 denotational identity**（History 因此得救）。**否決 `proper subset ⇒ admission`**（`EverythingExceptX` 反例 + partitioning game）。提出 role-indexed 方向 |
| **Round 3** | 逐型打掉「boundary 由 admitted structure 決定 ⇒ admission」。提出 falsification 形式 |
| **Round 4** | **Owner 裁定 governance scope 擴張到 vocabulary placement，但 Gate-only scope 維持 construct-only。**裁定 A（partial governance completion），禁止為 History / Lived Experience 虛構 parameter。**主大腦的 compliance 檢查發現 #3 §2 與 #8 §5 是既有的 substantive rules，駁回「Vocabulary = process-only」** |
| **Round 5** | Falsification check：先窮舉候選 V，再給出**輸入域結構性理由**。**結論：Vocabulary privilege 與 causal entailment 是不同輸入域** |

---

## 10. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R2** | 「History 是 alias clause 的反例」 | **alias 定義錯了**。若 alias ＝「可由既有東西定義出來」，projection 也會被殺。Owner R2 收緊為 denotational identity with a **single** admitted entity。**反例撤回** |
| 2 | **R3** | `proper subset ⇒ admission` | **`EverythingExceptX` 反例**：它滿足 proper subset。**而且是 partitioning game，撞上 DCG #13 已凍結的 anti-slip。Owner R2 否決** |
| 3 | **R4** | 「Vocabulary 不是可過濾的機構，只是可稽核的機構」 | **太強。**#3 §2 與 #8 §5 都是 substantive 的 kind-level 規則，且 #8 §5 明文宣稱擋住 vocabulary tree 增殖。**主大腦自行收回** |
| 4 | **R3** | 「Temporal Constraint 的問題已由 #19 處理」 | **越界。**#19 那一輪才剛把 `Tracked(Q) ≠ Vocabulary Admission` 拆開，等於立刻合回去。Owner R2 修正：**residual question 的 carrier 已由 #19 記錄；vocabulary placement 本身仍屬 #20 的問題域** |
| 5 | **R4** | 「三個 vocabulary 共享 rejection record 形式」 | **premise 不成立。**#8 / #9 的三項 canonical 理由完全不同。**已在 DCG #19 §24.1 撤回** |

> ### 第 3 項的方法論價值
> **五輪都在找 vocabulary gate，結果其中兩條 gate 早就在 DCG #3 與 #8 裡。**
> **在既有 canon 裡找規則，比推導新規則更可靠——
> 而且本輪證明了：既有規則與新推出的規則形式不同（stipulation vs entailment）。**

---

## 11. Non-Claims

**本輪沒有證明：**

- ❌ Vocabulary 永遠沒有 admission criterion
- ❌ 語言學事實在普遍形上學上不可能被 causal structure 蘊含
- ❌ 上述候選 V 的排除是窮盡的
- ❌ 未來不會引入新的 naming / modeling principle
- ❌ per-kind substantive rules 可以被推廣成 universal gate
- ❌ 「testable」已有可用定義
- ❌ Awareness governance 已完全合規

> ### 最終被封住的只有一句：
> **Vocabulary privilege 不能偷偷冒充 causal entailment。**

---

## 12. 交叉引用

| 檔案 | 內容 |
|---|---|
| `docs/ONTOLOGY-PRIVILEGE-POLICY.md` | Gate-only policy、governance invariants、vocabulary-placement governance scope |
| `docs/NORTH-STAR-RENAME-TEST-DCG-CONVERGENCE.md` | DCG #19，QPF 判準與 application |
| `docs/NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md` | DCG #18，`CausalFacts ⇏ UniqueGranularity` |
| `docs/NORTH-STAR-SOUL-ONTOLOGY-DCG-CONVERGENCE.md` | DCG #3，**Soul 的 substantive rule（§2）** |
| `docs/NORTH-STAR-AWARENESS-BOUNDARY-DCG-CONVERGENCE.md` | DCG #8，**Awareness 的 substantive rule（§5）** |
| `docs/NORTH-STAR-LIVED-EXPERIENCE-DCG-CONVERGENCE.md` | DCG #9，History / Lived Experience 的 placement 理由 |