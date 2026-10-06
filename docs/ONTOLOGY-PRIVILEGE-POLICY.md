# Ontology Privilege Policy — 研究治理規則（**已裁定：A — Gate-only / Admissibility Closure**）

> **本檔是 `docs/DISCUSSION-CONVERGENCE-GATE.md` 的後繼治理文件，處理 ontology-selection 與
> construct placement 的參數治理。**
>
> **本檔凍結的是治理規則本身，以及 Owner 對 privilege policy 的裁定。**
> **Policy：Gate-only / Admissibility Closure。不套用第二層 coarsening 或 privilege principle。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## §1 本檔的作用

**十八次 DCG 的結果把一個問題交給了 Owner：**在 causal structure 已確定的 admissible 格中，
Soul OS 要把哪一種 coarsening 視為研究上 privileged。

**Owner 已裁定。**本檔記錄該裁定、它的參數、參數語義、scope 與變更程序。

```
CAUSAL FACTS
    ↓
C*（canonical causal quotient，DCG #18）
    ↓
ISD / CEG（合法 distinction 的邊界）
    ↓
ONTOLOGY PRIVILEGE POLICY = Gate-only（本檔裁定）
    ↓
privileged ontology
```

---

## §2 Governance Invariants（已凍結）

> ### $$\boxed{ \text{Undeclared parameter} \;\Rightarrow\; \text{not a frozen rule} }$$
>
> **未宣告的參數，不是一條已凍結的規則。**

任何被宣告為 frozen 的 principle / policy / parameter，都必須同步宣告：

> ### $$\boxed{ \text{Parameter} + \text{Semantics} + \text{Scope} + \text{Change\ Procedure} }$$

以及：

> ### $$\boxed{ \Delta\ Principle \;\lor\; \Delta\ Parameter \;\Rightarrow\; \text{Re-evaluate all placements within declared scope} }$$
>
> **原則或參數改變，必須重新評估其宣告 scope 內的所有 placement。**

---

## §3 Scope Rule（已凍結）

> **Every frozen parameter must declare its scope at freeze time.
> An undeclared parameter scope defaults to all placements.**
>
> **每個被凍結的參數必須在凍結當時宣告其 scope。未宣告的參數 scope，預設為 all placements。**

```
declared scope = P
    → 只有 P 需要重新評估

scope omitted
    → all placements 需要重新評估
```

> **執行者不得事後自行解釋「affected」的範圍。**

**理由（記錄以防未來被省略）：**若 scope 可由執行者當場判斷，
`Re-evaluate affected placements` 會被讀成「只重跑有人想到要重跑的」——
那正是 §2 第一條 invariant 要禁止的靜默縮窄。

---

## §4 Self-Declaration（已凍結）

> **Governance document 自己也適用上述規則，因此必須自我宣告。**

```
SELF-DECLARATION
├─ Parameters
├─ Semantics
├─ Scope
└─ Change procedure
```

**本層的 scope 凍結為：**

> ### $$\boxed{ \text{all frozen ontology-selection / placement governance} }$$
      §6.3 Gate-only 另宣告為 all construct-placement decisions after adoption
      §6.5 Vocabulary-placement governance scope = all frozen vocabulary-placement decisions
      §6.7 Lived Experience parameter 的 scope = all frozen placement decisions for Lived Experience（刻意不擴張至其他 kind）

**理由：**若不如此，就會出現「這條規則要求所有參數自我宣告，但它自己的參數沒有宣告」的遞迴漏洞。

---

## §5 Decision（Owner 已裁定）

> ### $$\boxed{ A \;\text{— Gate-only / Admissibility Closure} }$$

$$ISD + CEG \;\Rightarrow\; \text{causally legitimate} \;\Rightarrow\; \text{retain}$$

**不再追加「但這個合法 distinction 值不值得保留？」——那已經是 modeling preference，不是 causal finding。**

### 5.1 正式語義（**不得寫成永久形上學宣言**）

> **Gate-only is the current ontology privilege policy: no second-layer coarsening
> or privilege principle is applied beyond the frozen ISD + CEG admission gates.**
>
> **Gate-only 是目前的 ontology privilege policy：除了已凍結的 ISD + CEG admission gates
> 之外，不再套用任何第二層 coarsening 或 privilege principle。**

**❌ 不得寫成：**「causal legitimacy 永遠足夠。」
**✅ 應寫成：**「目前不套用第二層 privilege rule。」

### 5.2 為什麼選 A 而不是 B

**因為 B 必須額外引入一個非 causal、非唯一、且帶外生參數的 privilege rule。**
那會把十八輪拆出的「因果事實 vs. modeling choice」重新混回 ontology。

**且目前沒有足夠理由把任何一種 MDL / predictive / explanatory / compositional principle
提升成 Soul OS 的 ontology privilege authority。**

---

## §6 Policy 參數宣告（四項齊備）

### 6.1 Parameter

> $$\boxed{ \text{Review Trigger} = \text{a new refinement or candidate passes both ISD and CEG} }$$

```
new candidate / refinement
        ↓
        ISD
        ↓
        CEG
        ↓
      PASS
        ↓
  Review Trigger
        ↓
Owner 可重新決定：
  ・Gate-only 是否繼續
  ・或引入第二層 Privilege Principle
```

> ### 🔴 **Gate pass 不會自動觸發 B。**
> **它只觸發「要不要重新檢查目前的 privilege policy」。**
>
> **因此 Gate-only 不會偷偷變成「第一次選 A，以後永遠 A」：**
> ```
> A 現在有效
>     ↓
> 發現真正的新 causal distinction（通過 ISD + CEG）
>     ↓
> 重新開 Owner-level policy review
> ```

### 6.2 Semantics

| 項目 | 語義 |
|---|---|
| **Admission** | ISD + CEG 兩道 gate |
| **Second-layer privilege rule** | **None** |
| **Trigger consequence** | **Owner-level review of whether Gate-only remains, or a second-layer Privilege Principle should be introduced** |

**Trigger 的後果是「重開 policy review」，不是「自動切換到 B」。**

### 6.3 Scope

> $$\boxed{ \text{all construct-placement decisions arising after adoption of Gate-only} }$$

**理由（已寫進檔案）：**避免某個新 refinement 被事後說成「不是這次 affected」。

### 6.4 Change Procedure

1. 本 policy 的變更由 **Owner** 決定。
2. 變更時必須更新 §9 的 self-declaration，並依 §2 第三條重跑 §6.3 所宣告 scope 內的所有
   construct-placement decisions。
3. commit message 必須記錄：變更了哪一個 parameter、為什麼、scope 內重跑了什麼。
4. **本 policy 的任何變更都不得追溯合理化既有文件中的未宣告參數**；歷史文件若含未宣告參數，
   應標記為 open，而非由本 policy 追認。

---


### 6.5 Vocabulary-Placement Governance Scope（Owner 於 DCG #20 裁定）

> **⚠️ §6.3 的 scope 維持 construct-only，不擴張到 vocabulary。**
> **否則會把 construct admission gate 偷偷變成 vocabulary admission gate，
> 與 DCG #20 的結果正面矛盾。**
> **Gate-only / ISD / CEG 不適用於 vocabulary placement。**

**Scope：**

$$\boxed{ all\ frozen\ vocabulary\text{-}placement\ decisions }$$

**Semantics：**

| # | 規則 |
|---|---|
| 1 | 每一個 frozen vocabulary placement 必須有 **declared rationale** |
| 2 | rationale **不自動構成** substantive admission gate |
| 3 | **QPF 不構成 placement veto** |
| 4 | **construct rejection 不構成 placement approval** |
| 5 | canonical case 的 rationale **不自動成為** universal gate |
| 6 | Vocabulary placement 受本檔 §2–§4 的 governance invariants 約束 |

### 6.6 Vocabulary Governance Status（DCG #20 R4 裁定：partial governance completion）

| 詞 | Substantive rationale | Governance parameter | 狀態 |
|---|---|---|---|
| **Soul** | QPF / question-preserving（#3 §2） | ✔ QPF | **Governable / parameterized** |
| **Awareness** | Agent–X relation expansion（#8 §5） | ✔ 但「testable」未形式化 | **Governable / partially formalized** |
| **History** | projection | ✘ | **Placement retained, admission rule OPEN** |
| **Lived Experience** | question-preserving（#9 §7） | **✔ QPF = 1**（#19 §30.2，**已宣告，見 §6.7**） | **Governable / parameterized** |

> **⚠️ History 與 Lived Experience 不得被稱為「已 frozen 但 non-compliant」——
> 那會直接違反 §2 第一條 `Undeclared parameter ⇒ not a frozen rule`。**

> **🔴 已知邊界：**DCG #20 確立 **Vocabulary privilege 不能偷偷冒充 causal entailment**。
> 目前未識別出 universal vocabulary-admission gate；
> 已存在的是 per-kind substantive rules，它們是 **stipulations with declared scope**。


### 6.7 Question-Preserving Vocabulary 的 per-term parameter 宣告（Lived Experience）

> $$\boxed{ LivedExperience\ 的\ placement\ parameter = QPF(LivedExperience)=1 }$$

**⚠️ 這不是新增 vocabulary gate，也不是把 QPF 提升成 universal admission criterion。
它是 Question-Preserving Vocabulary 這個 kind 的 per-term substantive rule，與 Soul 同型。**

#### Parameter

$$\boxed{ Parameter = QPF(LivedExperience),\ \ 目前值 = 1 }$$

**依 DCG #19 §30 的 frozen method，該值的來源：**

| 條件 | 結果 |
|---|---|
| 移除候選後原問題仍存活 | ✔ subject-level question survives |
| 沒有其他**獨立** tracked item 承載 | ✔ no independent carrier |
| 不以候選自己的 placement rationale 自我承載 | ✔ 依 #19 §3.1 self-carrier 排除 |
| 不需要建立無名等價替身 | ✔ |

#### Semantics（**直接引用 #19 的既有 frozen semantics，不另造規則**）

> **QPF = 1 iff removing the term leaves the original research question alive,
> no independent tracked item already carries that same question,
> and no nameless equivalent substitute is introduced.**

#### Scope

$$\boxed{ Scope = \text{all frozen placement decisions for Lived Experience} }$$

> **⚠️ 刻意不擴張。**不擴成 `all Question vocabulary`，也不擴成 `all vocabulary placement`。
> **否則一個 LE 的 parameter 會不必要地影響 Soul、Awareness、History 等不同 kind。**

#### Change Procedure

| # | 規則 |
|---|---|
| 1 | **只有 Owner** 可變更 Lived Experience 的 placement parameter |
| 2 | 若 QPF(LivedExperience) 或其適用 semantics 改變，必須重新評估上述 scope 內的 Lived Experience placement |
| 3 | 若 #19 的 frozen methodology、tracked-question registry 或候選 decomposition 發生會影響此 parameter 的變更，依 #19 的 dependency rule **重新計算 QPF；不能靠舊值延續** |
| 4 | 變更文件必須記錄：變更原因、parameter、scope、重新評估結果 |

#### 邊界（防止 QPF 被升格）

> **QPF(Lived Experience) = 1 不表示「凡是 QPF=1 的詞都必須進 QUESTION」。**
> **也不表示「QPF 是 universal vocabulary admission gate」。**
> **它只表示：這個已選定為 question-preserving kind 的 term，其 placement 由 QPF 這個已宣告 parameter 治理。**

**這維持 DCG #20 的 Terminal State：`No universal vocabulary-admission gate identified`，
同時允許 per-kind substantive governance。**

## §7 A 的實質取捨（Owner 已接受）

$$\boxed{ A:\ \text{系統決定 ontology 的成長速度} }$$

**所有真正通過 ISD + CEG 的 causal distinction 都可以進來。**

**優點**

$$\text{不把研究目的偷偷寫進 ontology} \qquad \text{不壓掉稀有但 causal-legitimate 的結構}$$

**代價**

$$\text{ontology vocabulary 可能持續成長}$$

**這不是缺陷，而是 A 明確選擇承擔的 policy consequence。**

**相對地，B 的形態是：**

$$\text{研究者選的 privilege rule} \;\rightarrow\; \text{決定哪些 complexity 被壓掉}$$

---

## §8 尚未採用的路徑（記錄，非 frozen rule）

**B — Privilege Principle 已被 Owner 明確不選。**

| # | B 被選時必須宣告 | 狀態 |
|---|---|---|
| 1 | Principle | 不適用（未選 B） |
| 2 | Parameter set | 不適用 |
| 3 | Parameter semantics | 不適用 |
| 4 | Who / what may change them | 不適用 |
| 5 | What event permits change | 不適用 |
| 6 | Whether prior construct decisions must be re-evaluated | 不適用 |
| 7 | Scope | 不適用 |

**若未來改選 B，必須重新填滿上表七項。依 §2 第一條，在此之前 B 不是 frozen rule。**

---

## §9 SELF-DECLARATION（本檔自身）

```
Parameters
    §2 四條 governance invariants
    §3 scope default rule
    §4 self-declaration requirement
    §6 Gate-only policy 的四項（Parameter / Semantics / Scope / Change Procedure）
    §6.5 Vocabulary-placement governance scope ＋ §6.6 governance status ＋ §6.7 Lived Experience parameter（QPF）四項宣告
    §9 本 self-declaration 本身的變更程序

Semantics
    §2 第一條：未宣告參數不構成 frozen rule
    §2 第三條：變更觸發 scope 內全部 placement 的重評估
    §3：執行者不得事後解釋 scope
    §4：governance document 適用同一規則
    §5.1：Gate-only 是「目前政策」，不是永久形上學宣言
    §6.1：Gate pass 只觸發 Owner-level policy review，不自動切換到 B

Scope
    all frozen ontology-selection / placement governance
    （§6.3 Gate-only 的 scope 另為 all construct-placement decisions
      arising after adoption of Gate-only）

Change procedure
    §6.4（Owner 決定；須更新本節並重跑 §6.3 scope 內的 placement 決策；
          不得追溯合理化歷史文件中的未宣告參數）
```

---

## §10 交叉引用

| 檔案 | 內容 |
|---|---|
| `docs/DISCUSSION-CONVERGENCE-GATE.md` | 研究討論多久必須收斂（**本檔的前身治理文件**） |
| `docs/NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md` | DCG #18，Terminal State = DEFERRED TO MODELING / RESEARCH-INTENT DECISION（**本檔即其裁決結果**） |
| `docs/NORTH-STAR-BRANCHING-SET-VALUED-TRANSITION-DCG-CONVERGENCE.md` | ISD / CEG 的凍結措辭（§14.1） |
| `docs/NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` | `Internal closure ≠ Ontology completeness` |

**18 份 DCG canonical 文件的分層表已統一加上指向本檔的引用。**

---

## §11 本裁定**不**做的事

**本次裁定只是選定未來如何 privilege，沒有改變 ISD、沒有改變 CEG，
也沒有改寫 DCG #1–#18 的任何裁定。**

**不需重跑任何既有 construct placement。**