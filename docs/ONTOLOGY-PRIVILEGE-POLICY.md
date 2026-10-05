# Ontology Privilege Policy — 研究治理規則（**Governance Invariants 已凍結**）

> **本檔是 `docs/DISCUSSION-CONVERGENCE-GATE.md` 的後繼治理文件，處理 ontology-selection 與
> construct placement 的參數治理。**
>
> **本檔凍結的是治理規則本身，不是 A/B 的裁決。**
> **A（Gate-only）與 B（Privilege Principle）尚未選定；本檔只凍結它們被選時必須遵守的前提。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## §1 本檔的作用

**十八次 DCG 的結果把一個問題交給了 Owner：**在 causal structure 已確定的 admissible 格中，
Soul OS 要把哪一種 coarsening 視為研究上 privileged。

**本檔不回答那個問題。**它規定的是：**無論那個問題怎麼回答，被採用的規則必須自我宣告到什麼程度。**

```
CAUSAL FACTS
    ↓
C*（canonical causal quotient，DCG #18）
    ↓
ISD / CEG（合法 distinction 的邊界）
    ↓
OWNER POLICY（本檔規定其形式）
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

**理由：**若不如此，就會出現「這條規則要求所有參數自我宣告，但它自己的參數沒有宣告」的遞迴漏洞。

---

## §5 Decision State（截至本檔凍結時）

```
GOVERNANCE PREREQUISITE                    ← 已凍結（本檔 §2–§4）
        │
        ├─ Undeclared parameter → not frozen rule
        ├─ Parameter change → re-evaluate declared scope
        ├─ Unspecified scope → all placements
        └─ Self-declaration required
        │
        ▼
OWNER DECISION                             ← 尚未做出
        │
        ├─ A: Gate-only / Admissibility Closure
        │      └─ review trigger required（內容未決）
        │
        └─ B: Privilege Principle
               └─ principle + parameters + change procedure（內容未決）
```

> ### ⚠️ **A 與 B 都尚未選定。**
> **依 §2 第一條，兩者的參數目前都未被宣告，因此它們目前都不是 frozen rule。**
> **本檔不得被讀成「已選 A」或「已選 B」。**

---

## §6 A 與 B 被選時必須填入的內容（**要求，不是已填值**）

### 6.1 A — Gate-only / Admissibility Closure

$$ISD + CEG \;\Rightarrow\; \text{獲得保留資格}$$

**必須宣告：**

| # | 項目 | 狀態 |
|---|---|---|
| 1 | Review Trigger：何時重新打開「是否需要第二層 Privilege Principle」 | **未宣告** |
| 2 | Scope | **未宣告**（依 §3 預設為 all placements） |
| 3 | Semantics | **未宣告** |

**語義邊界（記錄以防被誤讀）：**
「暫不設第二層」**不等於**「永不設第二層」。若不宣告 review trigger，
A 會從暫緩靜默變成永久關閉——**這是 §2 第一條要防止的情形**。

### 6.2 B — Privilege Principle

$$ISD+CEG \;\rightarrow\; admissible\ distinctions \;\rightarrow\; Privilege\ Principle \;\rightarrow\; selected\ ontology$$

**必須宣告：**

| # | 項目 | 狀態 |
|---|---|---|
| 1 | Principle（採哪一把尺） | **未宣告** |
| 2 | Parameter set（MDL 的 code/prior？PA 的 target/loss？MSCD 的 research-relevant？Compositionality 的 operation signature？） | **未宣告** |
| 3 | Parameter semantics | **未宣告** |
| 4 | Who / what may change them | **未宣告** |
| 5 | What event permits change | **未宣告** |
| 6 | Whether prior construct decisions must be re-evaluated | **未宣告** |
| 7 | Scope | **未宣告**（依 §3 預設為 all placements） |

> **不得只寫「採用 MDL / MSCD / PA」。必須連同上表七項一起進入 canon。**
> **否則 `research-relevant` / `target` / `operation signature` 只是把未裁決問題藏進規範。**

---

## §7 A 與 B 的實質差別（記錄，因為它常被誤述）

**不是「A 沒規則 / B 有規則」。**兩者都是正式 policy，都必須自我宣告。

| | 把「哪些複雜度值得保留」交給 |
|---|---|
| **A — Gate-only** | **causal discovery** |
| **B — Privilege Principle** | **modeling policy** |

**對應的成長速率：**

$$A:\ \text{construct growth rate} \approx \text{causal richness of the system}$$
$$B:\ \text{construct growth rate} \approx \text{chosen privilege rule}$$

**A 的代價：**`ontology vocabulary may continue to grow`
**B 的代價：**規則可能壓掉真實但稀有的 causal distinction

---

## §8 本檔的變更程序

**本檔可由 Owner 或其明示授權的執行者變更，且變更時必須：**

1. 更新 §9 的 self-declaration
2. 重跑 scope 內的所有受影響規則（本檔 scope = all frozen ontology-selection / placement governance）
3. 在 commit message 中記錄：變更了哪一條 invariant、為什麼、受影響範圍

**本檔的任何變更都不得追溯合理化既有文件中的未宣告參數。**
若歷史文件含有未宣告參數，該處應被標記為 open，而非由本檔追認。

---

## §9 SELF-DECLARATION（本檔自身）

```
Parameters
    §2 四條 governance invariants
    §3 scope default rule
    §4 self-declaration requirement
    §8 本檔變更程序

Semantics
    §2 第一條：未宣告參數不構成 frozen rule
    §2 第三條：變更觸發 scope 內全部 placement 的重評估
    §3：執行者不得事後解釋 scope
    §4：governance document 適用同一規則

Scope
    all frozen ontology-selection / placement governance

Change procedure
    §8（Owner 或其明示授權的執行者；須更新本節並重跑 scope 內規則；
         不得追溯合理化歷史文件中的未宣告參數）
```

---

## §10 交叉引用

| 檔案 | 內容 |
|---|---|
| `docs/DISCUSSION-CONVERGENCE-GATE.md` | 研究討論多久必須收斂（**本檔的前身治理文件**） |
| `docs/NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md` | DCG #18，Terminal State = DEFERRED TO MODELING / RESEARCH-INTENT DECISION |
| `docs/NORTH-STAR-BRANCHING-SET-VALUED-TRANSITION-DCG-CONVERGENCE.md` | ISD / CEG 的凍結措辭（§14.1） |
| `docs/NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` | `Internal closure ≠ Ontology completeness` |

**18 份 DCG canonical 文件的分層表應指向本檔。**該對齊工作在 §5 的 Owner 裁決後執行，
因為插入內容取決於 A 還是 B。