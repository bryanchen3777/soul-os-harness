# North Star — Motive / Decision Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Motive 與 Decision 皆不通過 ISD，不進 Construct Ontology。**
> **兩者的 vocabulary placement 皆為 OPEN，本輪不裁決。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

$$\boxed{ ISD(Motive) = \mathrm{false} \qquad ISD(Decision) = \mathrm{false} }$$

| Candidate | Construct | Vocabulary |
|---|---|---|
| **Motive** | ❌ rejected under current formulation | **OPEN** |
| **Decision** | ❌ rejected under current formulation | **OPEN** |

> ### **這兩個不能因為 construct failure 自動轉成 vocabulary pass。**
> （DCG #13 §8.1：A gate 的 failure 不會自動變成另一個 gate 的 pass。）

---

## 2. 🔴 Decision 的核心 reduction

```
Decision
   ↓
selection within T_A
   + existing context / relations
   + existing Memory-derived content
   ↓
no independent causal dimension remains
```

形式化：**Decision 的 denotation 是一個由已 admission 維度組成的合取式，再加上 𝒯_A 本身。**

$$ \text{Decision} \equiv \underbrace{\text{selection within } \mathcal{T}_A}_{\text{absorbed by Agent}} \land \underbrace{\text{Motive}}_{\text{Memory-derived}} \land \underbrace{\text{Memory}}_{\text{admitted}} \land \underbrace{\text{Agent–Agent}}_{\text{admitted}} \land \underbrace{\text{Event}\to\text{Agent}}_{\text{admitted}} \land \underbrace{\text{World temporal}}_{\text{admitted}} $$

### 2.1 這個 failure 是結構性的

> **若候選詞的 denotation 完全是既有 admitted dimensions 的函數，
> 則不存在一對「在所有已 admission 維度上等價、但在此候選上不同」的系統。
> 因為候選詞在那兩個系統上的取值必定相同。**
>
> **⇒ ISD 失敗是這個定義的必然結果，不需要另外找反例。**

**這與 DCG #9 中 History 的失敗是同一個形式。兩者現在有完全相同的正式地位。**

---

## 3. 三個層次終於完全分開

```
Motive
└─ existing goal / memory-derived structure

Decision
└─ selection machinery inside T_A

T_A
└─ Agent 的 admitted transition structure
```

**本輪沒有發現新 construct，而是發現這兩個自然語言詞分別貼在既有結構上。**

| 層 | 判定 |
|---|---|
| **Decision 的 selection machinery** | 屬於 𝒯_A 內部，不是新的 ontology layer |
| **Selection outcome** | 是 𝒯_A 的 realized successor |
| **Selection feedback** | 若形成 past-derived、causally effective state ⇒ **Memory**，不是新的 Decision construct |
| **LLM selector 的存在** | **不創造 Decision ontology**（凡是產生 Agent successor 的 mechanism 本來就是 𝒯_A） |

---

## 4. 🔴 Motive 的鏈

```
Goal ledger
   ↓
goal.title
   ↓
Motive
   ↓
pending pool
```

**Motive 沒有留下新的 causal operand / dependency。**

架構事實（主大腦 Round 1 讀取 `src/goals/motive_provider.py`）：

| 觀察 | 依據 |
|---|---|
| Motive 從 ledger 裝配，非隱 state | `assemble_candidate()` / `GoalMotiveProvider` |
| 裝配規則為**純結構規則** | 「≤1/心跳; 24h 配額 + N=3 輪替 + streak=2，純結構規則」 |
| **Motive 的 content 就是 `goal.title`** | `make_motive(..., content=goal.title, ...)` |
| 完成後走**既有** provenance 鏈 | `sediment_completion` → InnerLifeEvent → SAGE |

> ### **`content = goal.title` 是 Motive 與 Goal 在內容層不可區分的直接證據。**

---

## 5. Decision 的架構事實

架構事實由 **Owner 於 Round 4 讀取 `src/soul/decision.py`** 提供：

| 項目 | 實際位置 | 判定 |
|---|---|---|
| `decide_motive()` 的 conditioning sources | motive / provenance / memory_summary / relationship_summary / emergent_summary / current_time / temporal_anchor | 全部既有 |
| `DecisionResult` | output / observability state | 非 ontology |
| `decision.reason` | observability metadata | 非 causal operand |
| `parse_decision_output()` fail-closed | transition rule 的 fallback | 無新 operand |
| temperature / max_tokens / JSON schema / action enum | implementation constraints | 不因存在而成為 construct |
| `decision_not_transmit_streak` | past-derived causal state | **Memory** |
| LLM stochasticity | transition branching | **Branching property**（DCG #13） |

---

## 6. 🔴 `reason` 的 case-independent convergence

`reason` 的歸屬未被裁決，但**不影響本輪結論**：

| `reason` 的實際狀態 | 結果 |
|---|---|
| **未被後續讀取** | causally inert ⇒ 零 content，reduction 無損 |
| **被後續讀取並影響後續** | 由 past decision outcomes 導出 + 有 causal affordance ⇒ **Memory** |

> **兩個可能世界都不產生新的 Decision dimension。**
> **因此 reduction 的成立不依賴 `reason` 的歸屬。**

---

## 7. 🔴 Decision 與 Agency 的差別

| | Agency（#12） | Motive / Decision（#14） |
|---|---|---|
| 分解後是否留下**可獨立追問的新候選** | **✔ 是**：Branching、prospective candidate | **✘ 否** |
| 結果 | umbrella，留下 Branching 與 prospective 兩條線 | 整個貼在 𝒯_A 上，無 residual |
| 留下的問題 | 「Branching 值不值得成為 construct」 | **沒有提出任何新的 structural candidate** |

> **這是兩者在 vocabulary 問題上的實質差異：
> Agency 值得做 rename test，因為它的分解產生了新問題；
> Decision 的分解什麼都沒產生。**
>
> **但這仍然不是 vocabulary failure 的裁定。**

---

## 8. 十四次 DCG 後的分層

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
    ├─ construct placement: rejected
    └─ vocabulary placement: not yet determined

    Motive
    ├─ construct placement: rejected
    └─ vocabulary placement: not yet determined

    Decision
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

> **本層的成員由本輪更新。DCG #13 §8 的版本只含 Agency，該處已加交叉引用指回本檔。**
>
> 🔴 **此層在 DCG #15 再次更新：另加入 `Expression` / `Commitment` /
> `Self-Binding` / `Temporal Constraint`（皆 construct rejected ＋ vocabulary OPEN），
> 並新增 `DOCUMENTED GAPS` 一層。**
> **現況分層見 `docs/NORTH-STAR-EXPRESSION-COMMUNICATION-BOUNDARY-DCG-CONVERGENCE.md` §8。**

---

## 9. Open Questions

- **Motive vocabulary status：OPEN**
- **Decision vocabulary status：OPEN**

> **這兩項不能因為 construct failure 自動轉成 vocabulary pass。**
> **依 DCG #3 的 rename test（拿掉詞後是否仍保留一個值得獨立保存的研究問題）另行處理，本輪不裁決。**

---

## 10. Non-Claims

**本輪沒有證明：**

- ❌ Decision 在所有可能 architecture 都不可能成為 construct
- ❌ Motive 在所有可能 architecture 都不可能成為 construct
- ❌ LLM selection 沒有 causal reality
- ❌ current implementation 是唯一可能的 Decision architecture
- ❌ Decision 不值得研究
- ❌ Motive 不值得保留作研究語彙

> ### 真正封住的只有一句：
> **在 current architecture / current formulation 下，
> Motive 與 Decision 都沒有獨立 structural dimension。**

---

## 11. 十四次 DCG 在 action 線上的收束

```
Agency
   ↓
umbrella
   ↓
Branching / Closure / History / Prospective
          ↓
      分別落入既有維度或未成立候選

Branching
   ↓
T_A 的 structural property
   ↓
CEG rejected

Motive
   ↓
existing Goal / Memory-derived structure
   ↓
no ISD

Decision
   ↓
selection inside T_A
   ↓
no ISD
```

> **這四題不是「一直失敗」。**
> **真正發生的是：自然語言的 action vocabulary 正在被逐層還原成
> causal ontology 裡更小、更精確的結構。**
>
> **本輪終點：Motive 不是一個新 causal kind。Decision 也不是。**

---

## 12. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 封掉「same S but different M」（與 DCG #12 case C 同型矛盾）。提出 M 非 S 之函數時的五個出口，全部已有名字。架構實證：`content = goal.title` |
| **Round 2** | 指出 **X/Y 不是 valid ISD witness**（$g_1 \neq g_2 \Rightarrow \mathcal{T}_A^X \neq \mathcal{T}_A^Y$，兩者已在 admitted 維度上不同）。提出「新增 operand」通則 |
| **Round 3** | 接受三刀：X/Y 無效、implementation ≠ structure、**「new operand」過強**。確認 **𝒯_A 在 canon 中已是 intensional**（DCG #11 的 `T(r₁,·) ≠ T(r₂,·)` 隱含） |
| **Round 4** | Owner 讀取 `decision.py`，提供 Decision 側架構事實。確認 `reason` 為 observability metadata |
| **Round 5** | **Decision 與 Motive 的 reduction 成立。**`reason` 雙分支收斂，reduction 不依賴其歸屬。兩者 construct placement rejected，vocabulary OPEN |

---

## 13. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 提出 `Selection State Feedback` 為候選 | **它滿足 `PastDirected ∧ CausallyAfforded` ⇒ 就是 Memory。**Round 2 撤回。**撤回本身正確，但同一輪我又提出下一個未測候選** |
| 2 | **R2** | 「候選要新增 **operand**，不是新增 **function**」 | **過強。**理論上可以在不新增 operand 的情況下加入新的 causal coupling law（如 `y' = F(x,z) + G(x,z)`）。**Owner Round 3 駁回。此條收回** |
| 3 | **R2** | 把 R2 的結論稱為「本輪真正產生的通則」 | **我在同一份回覆裡已自陳「仍是歸納」，卻仍將其升格為通則。寫下自我批評不等於修正論證。**這是 DCG #13 R1 的同型重複 |
| 4 | **R3** | 以「沒有新 operand」作為 Decision 的排除理由 | **理由錯誤。**正確理由是「它是 intensional 𝒯_A 的內部結構」——一個已 admission construct 的內部結構。Owner 預先指出此區分 |
| 5 | **R1** | 以 `data/soul/` 內的架構證據支持結構性結論 | **未查 `decision.py` 就推論 Decision 側。**Round 1 已主動標記此缺口，Round 4 由 Owner 補上 |

> ### 第 3 項是本輪最重要的教訓
> **我已經識別出一條規則的弱點，卻仍然把它當成產出發布。**
> **識別風險與修正論證是兩件事；只做前者等於沒做。**
>
> 這與 DCG #13 R4→R5「不可把未證明 exhaustive 的分支當 exhaustive」屬於同一族：
> **知道自己論證不足，卻照樣輸出那個論證。**