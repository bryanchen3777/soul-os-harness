# North Star — Branching / Set-valued Transition Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Branching 通過 ISD，不通過 CEG ⇒ NOT A CONSTRUCT。**
> **同時凍結 Causal Extension Gate (CEG)，使 construct 准入成為雙 gate。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> $$\boxed{ ISD(Branching)=\text{true} \qquad CEG(Branching)=\text{false} }$$

> ### **Branching / Set-valued Transition：NOT A CONSTRUCT**
>
> **正式地位：an admitted Agent transition relation 的 structural property / parameter。**

**它不是**：Agent ／ Agency ／ Indeterminism ／ Free Will ／ 新的 causal construct。

**同時凍結：**

> $$\boxed{ ISD = \text{independent distinction} \qquad CEG = \text{causal extension} }$$

**兩者不是重複。**ISD 負責發現新維度；CEG 負責阻止「既有結構的新 predicate」偽裝成新 construct。

---

## 2. Causal Extension Gate（CEG）正式凍結

> ### **Causal Extension Gate (CEG)**
>
> **A candidate passes CEG only if its distinguishing condition is not fully
> determined by the extensional structure of an already-admitted relation, but
> instead depends on an independently specifiable structural operand or process
> constraint that has its own causal-functional role.**
>
> **候選項目只有在其區分條件並非完全由既有 admitted relation 的 extensional structure
> 所決定，而是依賴一個可獨立指定、具有自身 causal-functional role 的
> structural operand 或 process constraint 時，才通過 CEG。**

```
Candidate
   │
   ├─ completely determined by
   │  existing relation / image
   │        ↓
   │       CEG ✘
   │
   └─ depends on independently specifiable
      structural operand / process constraint
            ↓
           CEG ✔
```

### 2.1 為何用 functional determination 而非 variable syntax

主大腦 Round 4 提案的 (b) free-variable scope ＋ (c) re-encoding 原文**未被採用**。
它們抓到正確的問題，但仍依賴「某個人怎麼把系統拆成 variables / operands」。

**DCG #11 已確立：representation / encoding 的選擇不能自己取得 ontology 地位。**
所以 parity flag 應被視為 Succ 的衍生資訊——**但不能靠「它看起來像 re-encoding」來判。
需要的是函數依賴本身。**

> **CEG 的 boundary 是 functional determination，不是 variable syntax。**

---

## 3. 🔴 兩個包裝式 statistic 一起死

### 3.1 even-successor-count

$$ C(T_A) = \bigl|\mathrm{Succ}_A(S)\bigr| \text{ is even} $$

**完全由 `Succ_A(S)` 決定 ⇒ CEG ✘**

### 3.2 parity flag（架構把它存成 state component）

```
𝒯_A
 ↓
state component: parity = f(Succ_A(S))
 ↓
transition reads it
```

**即使成為 operand，仍然滿足 `parity = f(Succ_A(S))`。
它沒有獨立 structural operand ⇒ CEG ✘**

> ### **這正好堵住「加一個 flag 就能創造 construct」的漏洞。**

---

## 4. 雙 gate 回溯測試

| Construct | ISD | CEG |
|---|---|---|
| **Agent** | ✔ | ✔ |
| **World** | ✔ | ✔ |
| **Memory** | ✔ | ✔ |
| **Growth** | ✔ | ✔ |
| **Free Growth** | ✔ | ✔ |
| **Interaction** | ✔ | ✔ |
| **Branching** | **✔** | **✘** |
| History | ✘ | — |
| Awareness | ✘ | — |
| Temporal Identity | ✘ | — |

> **六項既有 constructs 無一被誤殺。**
> **Branching 是 ISD 能捕捉、CEG 能排除的第一個純 relation-shape predicate。**
> **這反而證明兩個 gate 在做不同工作。**

---

## 5. 🔴 ISD 不足以單獨判定 construct status

> **ISD 只問：有沒有新的 structural distinction？**
> **CEG 再問：這個 distinction 是否具有自己的 causal-functional structural role，
> 而不是既有 relation 的描述性 property？**

**純 cardinality / parity / degree / shape predicate 不因為能區分系統就自動取得 construct status。**

**Re-encoding 不取得新的 ontology 地位。**

---

## 6. Succ 的正式定義（繼承 DCG #12）

> $$\mathrm{Succ}_A(S),\quad S = \text{architecture-defined complete Agent state}$$

**Succ 必須始終指 actual Agent transition relation**，不是所有 theoretically admissible paths。
（Interpretation A 會使 `|Succ|=3` 配固定 policy 退化成 affordance 計數，witness 失效。）

---

## 7. 四個案例的最終結果

| Case | Branching | 說明 |
|---|---|---|
| **A** `Succ_A(S)={S₁}` | ✘ | `|Succ| = 1` |
| **B** `Succ_A(S)={S₁,S₂}` | ✔ | 純 set-valued 形態；無獨立 role |
| **C** external resolver | ✔ | **Branching 為真，但 resolver ≠ Branching** |
| **D** random selector | ✔ | Branching 為真；隨機是額外資訊，非 branching 所蘊含 |

### 7.1 Case C 的完整結構

```
S
 ↓
Succ_A(S) = {S₁, S₂}
 ↓
external resolver
 ↓
realized successor
```

> **Branching ✔，但 resolver ≠ Branching。**
> **resolver 若自己具有 independently specifiable causal structure，那是另一個 candidate。**
> **不能把它反灌進 Branching——那會回到「把多個結構打包後用 umbrella 名字取得 ontology 地位」。**

---

## 8. 十三次 DCG 後的分層

```
QUESTION
────────────────────────
Soul
└─ question-preserving vocabulary

CONSTRUCTS
────────────────────────
Agent
World
Memory
Growth
Free Growth
Interaction

STRUCTURAL PROPERTIES
────────────────────────
Branching / Set-valued Transition

RELATIONS
────────────────────────
Temporal Identity
Event ↝ Agent
External Exposure
Agent–Capability
Agent–World
Agent ↔ Agent
...

VOCABULARY
────────────────────────
History
Lived Experience
Awareness

UNPLACED / OPEN
────────────────────────
Agency
├─ construct placement: rejected
└─ vocabulary placement: not yet determined
```

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

> 🔴 **此層的成員已於 DCG #14 更新：`Motive` 與 `Decision`
> 亦為 construct placement rejected + vocabulary placement not yet determined。**
> **現況分層見 `docs/NORTH-STAR-MOTIVE-DECISION-BOUNDARY-DCG-CONVERGENCE.md` §8。**

### 8.1 🔴 Vocabulary 也有准入條件

DCG #3 已確立：**question-preserving force 是 Soul 被保留為 vocabulary 的理由。**
所以 vocabulary 不是「不是 construct 的詞的收容所」，它有自己的 gate。

> ### **A gate 的 failure，不會自動變成 B gate 的 pass。**

**`ISD(Agency) = false` 不蕴含 `VocabularyStatus(Agency) = pass`。**
**這與 #11 的 `recoverability ≠ representation` 屬於同一族：
一個 gate 的失敗不構成另一個 gate 的證據。**

### 8.2 Agency 的兩個已知結果必須分開保存

| 性質 | 值 |
|---|---|
| `ISD(Agency)` | **false**（DCG #12 已決） |
| `VocabularyStatus(Agency)` | **OPEN**（rename test 尚未做） |

> **兩者不能互相替代。「不是 construct」不是一個完整的 ontology placement。**

**未來處理 Agency vocabulary 所需的 rename test：**

> **拿掉「Agency」這個詞後，是否仍然保留一個值得獨立保存的研究問題？**
>
> **若否，連 vocabulary 都不需要。若是，才正式進 VOCABULARY。**

**本輪不裁決此項，且不得預先授予 question-preserving 地位。**

### 8.3 Soul 同步移入 QUESTION 層

`Soul` 與 `Agency` 同樣不得預先列於 VOCABULARY。
**Soul 的 vocabulary 地位由 DCG #3 的 rename test 確立（question-preserving force 成立），
因此它的位置記於 QUESTION 層，不重複列入 VOCABULARY。**
## 9. Open Questions

**只剩新 candidate，皆不屬於本題：**

- **Successor-set Resolution** — resolver 若具有 independently specifiable causal-functional role
- **其他位於 transition machinery 內、且具有獨立 causal-functional role 的 structural dimensions**

> **它們都必須重新跑 `ISD + CEG`。**
> **不能從 DCG #13 順手繼承 construct status。**

- **Agency 的 rename test**（DCG #12 遺留，見 §8 標記）

---

## 10. Non-Claims

**本輪沒有證明：**

- ❌ Branching 不存在
- ❌ set-valued transition 在任何 architecture 都不是有意義的
- ❌ Branching 不值得測量
- ❌ Branching 不可能在未來與另一個新 structural dimension 組成 construct
- ❌ indeterminism 不存在
- ❌ Agency 不存在

> ### 只裁定一句：
> **Branching 本身只是已 admitted 的 Agent transition relation 的 structural property，
> 沒有獨立 causal-functional extension，因此不進 Construct Ontology。**

---

## 11. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 主張 Branching 為 structural parameter，理由是「shape ≠ causal」與「不能 localize」。**兩個理由皆為歸納模式，非準則** |
| **Round 2** | 提出互斥陷阱：ISD 通過的讀法失敗於 role gate，有 role 的讀法失敗於 ISD。**但「role」措辭會誤殺 Memory**（Memory 也在既有 𝒯_A 裡有 causal role） |
| **Round 3** | Structural Role Gate 更名 **CEG (Causal Extension Gate)**，改以「是否新增 causal dependency / process rule」為準。識別包裝式 statistic 漏洞（even-count） |
| **Round 4** | 提出 free-variable scope ＋ re-encoding 雙子句，可排除 even-count 與 parity flag。**但仍依賴 variable syntax，且 (c) 屬「看起來像 re-encoding」的語意判斷** |
| **Round 5** | **CEG 凍結為 functional determination 版本。**Branching ISD ✔ / CEG ✘ ⇒ NOT A CONSTRUCT。雙 gate 成立 |

---

## 12. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 「shape boundary 不足以成為 construct」 | **是從六個案例歸納出的模式，不是準則。**World 與 Interaction 皆可作反例。Owner Round 2 駁回 |
| 2 | **R1** | 「Branching 不 localize，所以不是 construct」 | 同上。**localization 不是現有 ontology 的共同必要條件。**Owner Round 2 駁回 |
| 3 | **R2** | 用「structural role」當 gate | **「role 不能依賴既有結構」的讀法會錯殺 Memory / Free Growth / Interaction**——它們都在既有 𝒯_A 裡有 causal role。Owner Round 3 更名並改寫 |
| 4 | **R4** | 以 free-variable scope ＋ re-encoding 作為 CEG 條文 | **仍依賴「某個人怎麼切分 variables / operands」**，是 syntax 不是 function；且 (c) 是語意判斷。Owner Round 5 改為 functional determination |
| 5 | **R1** | 認為 ISD Gate 本身「錯了」 | **修正：ISD 沒有錯，它測的是 structural independence，缺的是 structural role。**需 companion gate，非改寫 ISD。Owner Round 2 確立 |

> **第 4 項是本輪最重要的教訓：即使抓到正確的問題，
> 判準仍可能依賴於「描述的切分方式」而非「系統的函數結構」。
> 這與 DCG #11 是同一個病：**描述的選擇不能自己取得結構地位。**