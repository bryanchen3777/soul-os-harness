# North Star — Agency / Action Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Agency 不通過 ISD Gate，不進 Construct Ontology。**
> **本輪同時發現一個獨立 structural dimension（Branching），placement 延至新題目。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### **Agency 不通過 ISD Gate。**
> **不存在已建立的獨立 Agency structural dimension。**
>
> **Agency → NOT A CONSTRUCT**

**同時正式保留：**

| | 狀態 |
|---|---|
| **Branching / Set-valued Transition** | 獨立 structural dimension **已發現**；construct placement **延至新題目** |
| **Prospective-directedness** | candidate **未建立**獨立 witness；**no ontological negative claim** |

---

## 2. 🔴 Agency 的分解結果

| 候選組成 | 結構地位 | ISD |
|---|---|---|
| **Branching**（set-valued 𝒯_A） | 真的新 structural dimension | **✔ 通過** |
| Closure locus（𝒯_A 內外） | DCG #10 既有 lineage semantics | ✘ 既有 |
| History-sensitivity | `Memory ∧ Growth` | ✘ 既有 |
| **Prospective-directedness** | candidate 未取得 independent witness | ✘ 未建立 |
| first-person／intention／選擇的語感 | 無結構內容 | ✘ |

> ### **Agency ＝ 一個把多個不同 structural dimensions、既有 relations
> 與 human semantic umbrella 壓縮在一起的詞。**
>
> **目前沒有任何一個可以獨立支撐它的 Agency dimension。**

**因此 Agency 的失敗不是「定義寫得不好」。**
而是**目前能讓這個詞工作的內容，已經被拆解成別的 structural dimensions / relations。**

---

## 3. 🔴 三個被封死的等式

```
internal causation      ≠ Agency    ✔ 它是 Agent transition causality
deterministic selection ≠ Agency    ✔ determinism 與 agency 正交
multiple possible paths ≠ Agency    ✔ Branching ≠ choice
```

**由第二條推出的完整形式：**

> ### **deterministic ≠ non-agentive**
> ### **indeterministic ≠ agentive**
>
> **自由意志不是隨機性。任何把 indeterminism 放進 Agency 定義的嘗試，
> 都會把「不確定」偷換成「自主」。**

---

## 4. 🔴 Branching 的正式地位

> ### **Branching / Set-valued Transition ＝ 獨立 structural dimension。**
> ### **Branching ≠ Agency。Branching ≠ choice。Branching ≠ indeterminism。Branching ≠ free will。**

`Succ(S)` 的**唯一合法讀法是 Interpretation B：actual Agent transition relation。**

- **Interpretation B** — `Succ(S)` = the Agent transition relation can actually evolve into ⇒ `|Succ(S)| > 1` 才是 branching
- **Interpretation A** — `Succ(S)` = all successors the architecture permits ⇒ `|Succ(S)| = 3` 但 runtime 仍是 `S → left`，那是 **affordance 數量，不是 branching**

> ### ⚠️ **Branching 的 ISD 成立條件（load-bearing）**
>
> **Branching 的 ISD witness 只在 Interpretation B 下成立。**
> **若採 Interpretation A，|Succ(S)| 退化成 affordance 計數，witness 立即失效。**
>
> **任何未來處理 Branching 的題目，必須先固定這個定義，否則結論不成立。**

---

## 5. 🔴 Anti-slips（本輪凍結）

```
semantic target          ≠ prospective structure
|Branching|              ≠ indeterminism
|Branching|              ≠ Agency
label / prompt wording   ≠ structural status
```

**共同形式：可觀察／可命名的表層，不決定結構地位。**

**`goal = 17` 與 `checksum = 17` 可以保持完全相同的 causal structure。**
**所以 semantic target ≠ structural prospective relation。**

> ### **這是 DCG #11 的 `recoverability ≠ representation` 再推一格。**

---

## 6. 🔴 Prospective-Directedness 的 epistemic 狀態

**必須精確記錄，因為它與 Agency 的狀態不同：**

| | 狀態 |
|---|---|
| **Prospective-directedness** | **no current independent witness ⇒ not admitted ⇒ NOT proven impossible** |
| **Agency** | current candidate decomposition yields no independent dimension ⇒ **fails ISD as currently formulated** |

### 6.1 已成立的分支

**H1 — past-derived**

$$ \text{PastDirected}(C,E) \Rightarrow C \Rightarrow \text{Memory} $$

若某 state component 的內容曾因 past event 而被修訂，它即溯自過去，
**「內容指向未來」不改變它溯自過去這件事。**
⇒ prospective-directedness 被 Memory 吸收，無 residual。

**H2 — semantic target**

若 future-directedness 只來自欄位名稱／prompt wording／observer 對 content 的解讀，
則 `goal = 17` 與 `checksum = 17` 可保持相同 causal structure。
⇒ 失敗。

### 6.2 🔴 未完成的分支（**本輪最重要的 epistemic 修正**）

**H3 被提出為：**

`Target(C)` 只能是 observer 指派、由 𝒯_A 推導、或不存在。

> ### **此 trichotomy 並未被形式上證明 exhaustive。**
>
> 理論上仍可能存在：`C → explicit architectural relation → future-state structure`，
> 而該 relation **不是 observer 命名、不是 C 的 value semantics、也不是 𝒯_A 自己推導的**。
>
> **因此本輪的正確結論是「沒有找到」，不是「不可能存在」。**
>
> **這是 epistemic limitation ≠ ontological conclusion。**

### 6.3 架構實例的實際歸類

Soul OS 的 goal state：

```
src/goals/seed_provider.py:559    facts = get_all_facts()        ← 由 past facts 播種
src/soul/life_thread_origins.py:640   reason = "no_active_goal"  ← 可缺席
```

**由 past facts 播種 ⇒ 溯自 past events ⇒ 落在 H1。**
**`no_active_goal` 意味著它可缺席——這與「記憶式可選」一致，
而非「結構性必要」。**

> **Soul OS 的 goal state 是一個內容朝前的 Memory，不是前瞻結構。**
> **（此為候選架構證據之分類，非本輪已凍結之裁定。）**

---

## 7. 十二次 DCG 後的 Construct Ontology

```
CAUSAL CONSTRUCTS
    Agent
    World
    Memory
    Growth
    Free Growth
    Interaction

（十二次 DCG 之後仍為六項。）

DISCOVERED BUT NOT PLACED
    Branching / Set-valued Transition
        → independent structural dimension
        → construct placement DEFERRED to a separate topic
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

> **十二次 DCG 沒有新增 construct，但新增了一個已確立、尚未安置的 structural dimension。**
> **這兩件事必須分開記錄：前者是本體狀態，後者是待辦。**

---

## 8. Open Questions

- **Branching / Set-valued Transition 是否值得獨立成為 construct**
  → **新題目，不屬於 DCG #12**
- **若未來發現真正獨立的 structural Target relation，是否產生新的 construct**
  → **新題目，不回填 Agency**
- **Agency 是否具有 question-preserving force，值得保留為 vocabulary**
  → **若需要，另開題做 rename test。本輪不裁決。**

---

## 9. Non-Claims

**本輪沒有證明：**

- ❌ Agency 不存在
- ❌ Agent 沒有 agency
- ❌ deterministic system 沒有 agency
- ❌ branching system 有 agency
- ❌ goal-directed system 有 agency
- ❌ **prospective structure 不可能存在**
- ❌ future-directed state 一定只是 Memory
- ❌ 意志、選擇、控制在哲學上已被解決

> ### 真正被封住的只有一句：
> **「Agency」目前不是一個可以用獨立 structural dimension 支撐的 construct。**

---

## 10. 🔴 DCG #12 最值得留下的一句

> ### **可檢驗的新結構，不會因為人類給它一個熟悉的高階名字，
> 就自動繼承那個名字的語義。**

```
Branching          → Branching
Prospective?       → 待證
Agency             → umbrella
```

**而不是：**

```
Branching + history + goal  ↓  Agency
```

**這是 #12 真正打穿的地方。**

---

## 11. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 主張 `Agency = InternalClosure ∧ LineageHistorySensitivity`，並給出 ISD 見證對。**但 `Succ(S)` 未定義**，且未察覺兩個 conjunct 皆可被既有維度決定 |
| **Round 2** | **兩次 entailment 檢查**：(a) history-sensitivity ≡ `Memory ∧ Growth`；(b) closure locus ＝ 𝒯_A membership（DCG #10 既有）。**ISD 失敗。**唯一通過者為 Branching |
| **Round 3** | 提出 prospective-directedness 候選，舉 `src/goals/` 架構實例。**命名錯誤：把它往 Agency 上靠是 Round 1 同型錯誤** |
| **Round 4** | Prospective-Directedness Dilemma（**H1 / H2 / H3**）。**H1、H2 成立；H3 的 trichotomy 未被證明 exhaustive** |
| **Round 5** | **Agency 排除出 Construct Ontology。**Branching 保留為已發現 dimension，placement 延後。Prospective 記為 not proven impossible |

---

## 12. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | `Succ(S)` 直接當作 branching 證據 | **未定義 Succ。**採 Interpretation A 時整個證據失效（Owner Round 3 抓出） |
| 2 | **R1→R2** | 兩 conjunct 皆為新維度 | 皆被 `𝒯_A` 與 `Memory ∧ Growth` 決定，ISD 失敗（主大腦自行查出） |
| 3 | **R3** | prospective-directedness 可作為 Agency 的 closure dimension | **H1 顯示它是 Memory；且把它命名為 Agency 是 R1 同型錯誤。**主大腦於 R4 自行收回 |
| 4 | **R4** | H1/H2/H3 三分支構成完整案例分析 | **H3 的 trichotomy 未被證明 exhaustive。Owner Round 5 修正 epistemic 強度：應為「no current witness」而非「impossible」 |

> **第 4 項是本輪最重要的教訓：**
> **把一個未證明 exhaustive 的分支當作 exhaustive 使用，會把「我沒找到」
> 升格成「它不存在」——這是 `epistemic limitation ≠ ontological conclusion`
> 的一個內部版本，而且是我自己反覆犯的同一類錯誤。**