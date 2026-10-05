# North Star — Expression / Communication Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Expression 沒有 construct residual。**
> **同時凍結 𝒯_A invariance convention，並記錄一個 documented categorical gap。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### $$\boxed{ \text{Expression — no construct residual under current ontology} }$$

$$\boxed{ Expression,\ Commitment,\ SelfBinding,\ TemporalConstraint \not\rightarrow Construct }$$

**同時凍結：**

| | 狀態 |
|---|---|
| **Horn D — architecture-seeded, non-past-derived causal state** | 確實是一個 **documented causal category** |
| | 但 **Horn D ⇏ new construct** |

---

## 2. 🔴 Constraint 的完整分解（四支，exhaustive）

固定 𝒯_A 之後，時間變化只能來自 S_A、input 或 relations。因此 constraint 的 causal role 必須落在四處之一：

$$ \text{causal role of constraint} \rightarrow \begin{cases} A:\ \text{retained past-derived representation} & \rightarrow \textbf{Memory} \\ B:\ \text{World / other Agent ongoing causation} & \rightarrow \textbf{World / Interaction} \\ C:\ \text{direct modification of } \mathcal{T}_A & \rightarrow \textbf{not admitted} \\ D:\ \text{architecture-seeded, non-past-derived state} & \rightarrow \textbf{documented gap} \end{cases} $$

**主大腦 Round 4 指出此分解原本不是 exhaustive（缺少 Horn D），Owner Round 5 接受該修正。**

### 2.1 Horn D 確實裝得下東西

| 成員 | 溯及 Agent 的過去？ | 覆蓋 |
|---|---|---|
| World 輸入（sensor、news、時間） | ✘ | `External Exposure` ✔ |
| 另一 Agent 的 state | ✘ | `Agent↔Agent` / Interaction ✔ |
| **架構 seed 常數**（prompt template、model config、feature flag、quiet-hours、scheduler 常數） | **✘** | **無** |

**且在 Soul OS 有真實實例**：`life_thread_bootstrap.py` 的 `BOOTSTRAP_ENABLED_ENV` / `bootstrap_enabled`、
`motive_provider._is_quiet_hours`、以及 `tests/test_env_feature_flag_isolation.py` 存在的理由——
**環境層設定影響 runtime 行為，但它不是任何 Agent 的 past。**

---

## 3. 🔴 為什麼 Horn D 過不了 ISD

主大腦提出三個理由；**Owner 將第 ② 項寫成更正式的形式，此處採該版本：**

若 $$ S_A = (Seed,\ Memory,\ World\_context,\ \ldots) $$

則改變 Seed 通常就是 $$ S_A^X \neq S_A^Y $$

**於是不能同時保持 $$ ExistingDims(X) = ExistingDims(Y) $$。**

若 Agent 所承認的 state dimension 已經包含這部分，**Horn D 只是 Agent state 內的一類來源標記，不是獨立 structural dimension。**

$$\boxed{ ISD(HornD) = \mathrm{false} }$$

> **目前是有結構性理由的，不只是「沒找到 counterexample」。**

### 3.1 作用域也不能偷渡成 construct

```
global seed / local seed / agent-specific seed / shared seed
```

這些可能是真實差異。但單純 $$ scope(seed_1) \neq scope(seed_2) $$ 只是描述
**某個既有 causal operand 作用在哪些 state 上。**

若沒有另外證明 scope 本身具有獨立的 causal-functional machinery，它只是：
**範疇 / domain / cardinality / distribution / shape。**

$$\boxed{ CEG(scope) = \mathrm{false} \quad \text{（目前沒有獨立 structural witness）} }$$

**注意措辭：不是「scope 在原理上永遠不可能成為 structure」。**

---

## 4. 🔴 本題最重要的副產物：causal coverage ≠ vocabulary coverage

```
CAUSAL COVERAGE
Agent / World / Memory / Growth / Free Growth / Interaction
        │
        ├── covers many causal states
        │
        └── but not every causal-source category has a dedicated name
```

> ### $$\boxed{ \text{Unnamed} \neq \text{missing causal explanation} } $$

**Horn D 沒有 dedicated construct / relation，但它仍可被放進 Agent state 並由既有 𝒯_A 處理。**

**所以這個 gap 準確的名字是 `documented categorical gap`，而不是「ontology 缺了一個 construct」。**

> ### **「我們缺一個名稱」不等於「我們發現一個新 construct」。**
> **causal category ≠ construct。**

**（本條由 Owner 於 Round 5 提出，主大腦未在前四輪中區分這兩者。）**

---

## 5. 十五次 DCG 在 action / expression 線上的完整收束

```
Expression           → composite reduction，無 residual
Commitment           → 若有 retained past-derived R 則為 Memory
Self-Binding         → 同上；不能另立「self-constraint」kind
Temporal Constraint  → Memory / World / Interaction，direct 𝒯_A-replacement 不在 canon 內

Horn D              → exists as a documented causal category
                    → ⇏ new construct
```

### 5.1 #14 的 collateral clarification

#14 當時已知 $$ ISD(Motive)=false $$、$$ ISD(Decision)=false $$，
但留有一個未完成問題：**它們既然不是 Memory，causal source 到底算什麼？**

現在可補上 **causal-source taxonomy**（不是替 #14 增加 construct）：

```
Motive / Decision
├─ past-derived component                  → Memory
├─ World / temporal input                  → World / External Exposure
├─ other-Agent input                       → Interaction
└─ architecture-seeded selection constraints → Horn D
```

> **#14 的 reduction 沒有錯；當時只是把「不是新 construct」誤讀成「已經完整歸位」。**

---

## 6. 副產物一：𝒯_A invariance convention（凍結，中英雙版）

> ### **𝒯_A 為固定的 Agent transition schema，作用於架構定義的完整 Agent state；
> 時間中的行為變化歸因於 state、input 或既有 relations 的變化，
> 而不是 𝒯_A schema 本身被替換。**
>
> **𝒯_A is a fixed Agent transition schema over the architecture-defined complete
> Agent state; temporal changes in realized behavior are attributed to changes in
> state, input, or admitted relations, not to replacement of the transition schema itself.**

**這是 ontology convention，不是 metaphysical claim。**

### 6.1 它不是「𝒯_A 必須 time-invariant」

Owner 修正：下列兩件事必須分開——

| | 內容 | 是否破壞 DCG #10 |
|---|---|---|
| **A** | transition schema 本身換了 | **是** |
| **B** | 同一 schema 因 state 不同產生不同 successor | **否** |

$$\text{time-varying realized behavior} \not\Rightarrow \text{time-varying } \mathcal{T}_A $$

**固定 𝒯_A 反而允許 growth、memory、interaction 正常工作。**

### 6.2 Branching 不需重跑

**Branching 從來就是 state-relative 的**：$$ |Succ_A(S)| > 1 $$
不需要 $$ \forall S,\ |Succ_A(S)| > 1 $$。

```
S₁ → one successor
S₂ → three successors
S₃ → one successor
```

完全可以是一個固定 𝒯_A 的系統。**Branching 的 state-relative 性質與 𝒯_A schema 是否 time-invariant 是兩個不同問題。DCG #13 的 CEG 結論不受影響。**

---

## 7. 副產物二：Unnamed category（凍結狀態）

> **Architecture-seeded, non-past-derived causal state**

```
status:     documented gap
construct:  not established
relation:   not established
ISD:        no current witness
CEG:        not reached as an independent candidate
```

> ### **還沒形成 candidate，就不應該先跑 CEG。**

---

## 8. 十五次 DCG 後的分層

```
QUESTION
    Soul
└─ question-preserving vocabulary

CONSTRUCTS
    Agent / World / Memory / Growth / Free Growth / Interaction

STRUCTURAL PROPERTIES
    Branching / Set-valued Transition

RELATIONS
    Temporal Identity
    Event ↝ Agent
    External Exposure
    Agent–Capability
    Agent–World
    Agent ↔ Agent

VOCABULARY
    History
    Lived Experience
    Awareness

UNPLACED / OPEN
    Agency
    Motive
    Decision
    Expression
    Commitment
    Self-Binding
    Temporal Constraint
    └─ construct placement: rejected
       └─ vocabulary placement: not yet determined

DOCUMENTED GAPS
    Architecture-seeded, non-past-derived causal state (Horn D)
    └─ construct: not established
       └─ relation: not established
```

---

## 9. Open Questions

- **Expression / Commitment / Self-Binding / Temporal Constraint 的 vocabulary placement**
  → **未做 rename test。**construct failure 不自動轉成 vocabulary pass。
- **Architecture-seeded, non-past-derived causal state 要不要成為正式候選**
  → **尚未決定。**
- **下一題：DCG #16 — Architecture Seed / Substrate Boundary**
  核心問題：architecture-seeded causal state 到底只是 Agent state 的來源類別，
  還是具有獨立 structural role？

> ### 🔴 **開 #16 的紀律**
> **不能直接把 `Soul Seed` 當 candidate——那會把研究名稱再次偷偷變成證據。**

---

## 10. Non-Claims

**本輪沒有證明：**

- ❌ Expression / Commitment / Self-Binding / Temporal Constraint 在所有可能架構下都不可能成為 construct
- ❌ Horn D 永遠沒有 ISD witness
- ❌ Architecture-seeded state 永遠只能是 Agent state
- ❌ 𝒯_A 在所有可能 ontology 中都必須固定（凍結的是 **Soul OS current canon convention**）
- ❌ Seed 不可能在未來形成獨立 structural role
- ❌ Scope 永遠不可能成為 structure

> ### 真正留下的：
> **不是一個第七 construct，而是一個我們終於正式承認存在的「未命名 causal-source 類別」。**

---

## 11. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 四成分（Production / Encoding / Externalization / Reception）逐一歸位。**指出「一個事件屬於兩條 trajectory」不是新結構，它是 `Event↝Agent` 的內容**（World ＝ non-Agent-image ⇒ message 是 Agent artifact）。**留下 Encoding 缺口** |
| **Round 2** | 接受 canon-gap catch（收回偷渡的 axiom）。提出 ΔE 四出口。**補上 Case D1 的內部切分**：Case D 殘餘是「行為可區分、因果不可區分」，而 causal ontology 刻意不追蹤它。提出 commitment = Memory 的一般論證 |
| **Round 3** | 接受「persistent ≠ World」與「World / Memory 不互斥」。**接受 R 那個 catch：Memory 需要 $R$，「任何 past influence」偷換不了它。**提出 𝒯_A time-invariance 問題 |
| **Round 4** | **凍結 𝒯_A invariance convention。**修正「𝒯_A 必須 time-invariant」的論證形式（A vs B）。**確認 Branching 不需重跑。**三分叉仍非 exhaustive |
| **Round 5** | **Horn D 補入，分解成為四支 exhaustive。**Owner 給出 Horn D 失敗 ISD 的形式版本與 causal coverage ≠ vocabulary coverage 的區分。**收斂** |

---

## 12. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 「emitted message 是 Agent artifact，不是 World」，並用它完成 reduction | **循環論證。**我們從未凍結「𝒯_A 的 transition outcome 是否包含 external emission」。Owner R2 駁回。**我收回該 axiom 的用法**（但保留其地位為待凍結問題） |
| 2 | **R1** | 把 World / Memory / Interaction 排成互斥三選一 | **它們是同一 artifact 的兩個不同維度，不是互斥類別。**火可同時是 World 與某 Agent 的 Memory。Owner R3 駁回 |
| 3 | **R2** | 「任何約束在被作用的 state 上都是 past-directed ⇒ 就是 Memory」 | **漏掉 $R$。**PastDirected ＋ causal effect 不推出 Memory，因為 Memory 需要一個 **retained representation**。Owner R3 駁回，**此條收回** |
| 4 | **R3** | 「𝒯_A 必須 time-invariant，否則 #10 自我解構」 | **推論形式錯。**混了「schema 換掉」與「同一 schema 因 state 不同」兩件事。Owner R4 修正。**但 #3 逼出的問題是對的**——它揭示了從未被明文凍結的承重前提 |
| 5 | **R5 前** | 三分叉 exhaustive | **漏掉 Horn D。**由主大腦自行發現並提出，Owner 接受 |

> ### 第 4 項是本輪最重要的教訓
> **一個錯誤的論證可以逼出一個正確的問題。**
> **「𝒯_A 必須 time-invariant」這個推論是錯的，
> 但「𝒯_A 的 schema 與 realized behavior 被混為一談」這個問題是真的，
> 而它比原本的題目更值得凍結。**
