# North Star — Memory / Retention Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Memory 是 Construct。Construct Ontology 由五項擴為六項。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### $$\boxed{ \text{Memory}(E,R,A) \iff \text{PastDirected}(R,E) \ \land\ \text{CausallyAfforded}(R,A) }$$

**Memory 正式進入 Construct Ontology。**

准入理由（收窄後）：

> **不是「用了 function」所以成為 construct；而是它引入了一個此前 ontology
> 沒有的 causal-functional structure——一個帶有 past-directed provenance 的
> representation，作為 Agent transition machinery 的 difference-sensitive input。**

---

## 2. 兩個條件各來自不同的來源

| 條件 | 內容 | 語義來源 |
|---|---|---|
| **PastDirected(R, E)** | representation 與 past event 之間有 structural lineage | **provenance / producer** |
| **CausallyAfforded(R, A)** | ∃T ∈ 𝒯_A，∃r₁ ≠ r₂ ∈ Realizable(R)，使 T(r₁) ≠ T(r₂) | **Agent lineage / trajectory semantics** |
| **Realizable(R)** | producer 在 architecture contract 下可產生的 representation values | **representation producer** |

> **三者互相獨立，因此不存在 `Memory → 𝒯_A → Memory` 的循環。**

---

## 3. 🔴 Accessibility 的否定清單

**以下六項都不能單獨構成 Memory：**

```
❌ storage location
❌ state persistence
❌ parameter-list membership
❌ arbitrary codebase function 的 dataflow
❌ actual occurrence / use
❌ information-theoretic recoverability
```

**第六項是 Round 1 的陷阱，必須保留在清單裡。**

`current_state = f(all previous inputs)` 且 f 可逆，**不構成 Memory。**
因為 recoverability 會隨編碼選擇翻轉，是 observer-relative 的性質。

> **可還原性是描述的性質，不是系統的性質。**

---

## 4. 🔴 Record 與 Memory 的完全分割

$$ \text{Record}(E,R) \iff \text{PastDirected}(R,E) \ \land\ \neg\,\text{CausallyAfforded}(R,A) $$

$$ \text{Memory}(E,R,A) \iff \text{PastDirected}(R,E) \ \land\ \text{CausallyAfforded}(R,A) $$

**兩邊都要求 provenance。分割點精確落在 causal affordance 上。沒有第三類。**

| | Record | Memory |
|---|---|---|
| provenance-bearing | ✔ | ✔ |
| Agent-transition-level causal affordance | ✘ | ✔ |

> **因此：provenance 不足以區分 record 與 memory。只有 accessibility 能。**

---

## 5. 🔴 為什麼 audit script 不能創造 Memory

```
audit_life_thread_sage_seam.py
    ↓
read JSON
    ↓
build g[...]
```

即使 R ≠ R′ 導致 audit output 不同，**它不構成 Agent A 的 trajectory transition**：

- 不產生 Agent successor state
- 不推進 Agent lineage
- 不構成 Agent trajectory transition

$$ \Rightarrow f \notin \mathcal{T}_A $$

> **這個 boundary 是 causal-functional 的，不是檔名 blacklist。**
> 不是列 `audit.py ❌`／`test.py ❌`／`analysis.py ❌`，
> 而是問：**這個 function 是否屬於 Agent trajectory 的 transition relation？**

---

## 6. 🔴 disposition ≠ occurrence

```
Accessibility / affordance = disposition（能力）
Use                        = occurrence（事件）
```

若 R(E) 具備合法 causal affordance，但觀察期間從未被讀取：

> **Memory ＝ TRUE**

因為**不能用 occurrence 定義 Agent 的 structural property。**
**unexercised ≠ absent。** 這與 DCG #10 的 lineage semantics 一致。

**因此架構新增真正的 consumer 是在改變 causal structure，不是「宣告」Memory。**
這與 DCG #10 中「restore 可被設計成 continuation 或 new lineage」同構。

---

## 7. 🔴 語義豐富度不是 boundary

以下都可以是 Memory（只要 `PastDirected ∧ CausallyAfforded`）：

- `hash(E)`
- compressed summary（`diary/*.jsonl` 型態）
- random token T（只要 provenance = E）
- 不可讀、不可逆、不描述 E、無人類語義

> **Memory 與 semantic richness 無關。**

**否則我們會重新掉回 Round 1 的 recoverability trap。**

---

## 8. 🔴 retention duration 不是 Memory boundary

```
day 1    record exists
day 20   record exists
day 30   record decayed
```

**day 30 並不因此失去 Memory。**

> ### **decay ≠ forgetting**

架構事實佐證：`data/soul/agent_akane/relationships.json` 中
`user_bryan` 目前 `confidence: 0.0`、`interaction_count: 73`、
`last_interaction_at: 2026-09-22`，且 `last_decay_at` 存在。

**內容被 decay 過，但仍在、仍被讀。decayed ≠ forgotten。**
只有明確的 `invalidated_at` 才構成移除。

**否則我們會把一個工程參數（retention policy）偷升格成 construct 邊界。**

---

## 9. 🔴 Memory 與 History 的分界

```
History = Event ↝ Agent
          只是 causal graph 的 projection
          成員全部已經在 graph 裡
          → VOCABULARY / RELATION

Memory  = past-directed representation
        + 在 Agent transition system 中的 functional causal role
        → CONSTRUCT
```

Memory 多出的結構**不是另一條 causal edge**，
而是 **transition system 對某一類 past-directed representation 所具有的 functional role**。

它回答一個 causal graph 原本沒有直接表達的問題：

> **「這個 past-derived representation 的不同 realizations，
> 是否具有改變 Agent transition 的能力？」**

它可以在以下全部相同時做出區分：

```
same Event ancestry
same Agent
same storage
same provenance

R 不具 causal affordance  → Record
R 具 causal affordance    → Memory
```

> ### 濃縮版
> **History says that a past event causally belongs to an Agent's later trajectory;
> Memory says that a past-directed representation has a causal-functional role
> within that Agent's transition machinery.**

---

## 10. 🔴 與 CA-2 的結構同構

```
CA-2     Agent–Capability
              ↓
         representation
              ↓
         causal functional role

Memory   Past-derived representation
              ↓
         causal functional role
```

**兩者都不靠「感覺像 cognition」成立。**
它們都靠 **representation 在 Agent transition architecture 中
具有可區分的 functional causal role**。

這也解釋了為什麼 cognition entry 被 #8 排除後，CA-2 仍可存在。

---

## 11. 🔴 Memory → Growth → Free Growth 的正確依賴方向

```
Memory    ── 使 past representation 可被利用
   ↓
Growth    ── revision of existing past representation
   ↓
Free Growth ── revision is content-grounded
```

> ### **不要寫成「每一個 Memory 都會產生 Growth」——不成立。**

正確形式：

$$ \text{FreeGrowth} \Rightarrow \text{Growth} $$

且 Free Growth 所需的 past representation **必然**滿足 Memory 的結構
（能「causally influence revision formation」即已蕴含 difference-making capacity）。

> **Memory 是 Free Growth 的 structural prerequisite，不是它的結果。**
> 這是從定義推出的，不是我們附加的規定。

---

## 12. 🔴 十一次 DCG 後的最終分層

```
QUESTION
    Soul
    └─ question-preserving vocabulary
    └─ no evidentiary force

CAUSAL CONSTRUCTS
    Agent
    World
    Memory          ← 本輪新增
    Growth
    Free Growth
    Interaction

CAUSAL RELATIONS
    Temporal Identity
    Event ↝ Agent
    External Exposure
    Agent–Capability
    Agent–World
    Agent ↔ Agent

DESCRIPTIVE VOCABULARY
    History
    Lived Experience
    Awareness
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

> **本輪真正新增的不是「記憶這個詞」，
> 而是 `past-directed representation × Agent transition machinery`
> 這個此前沒有被 ontology 單獨刻畫的 causal-functional structure。**

---

## 13. Open Questions

- `life_threads.jsonl` 的實際 runtime consumer 是否進入 𝒯_A **尚未完整 trace**，
  因此其 Memory status **暫不宣告**。
  現況：滿足 PastDirected，尚未證實 CausallyAfforded ⇒ **Record；Memory status pending**
- `Realizable(R)` 在特定 architecture 下如何具體取得 → **architecture / measurement 問題**
- Memory 的 causal affordance 如何做獨立 measurement → **measurement 問題**
- Memory 是否必須由 Agent 自己維護，抑或 external substrate 進入 𝒯_A 即足夠
  → **本輪未決。本檔不記錄傾向。**（見 §14.3）

---


## 14. 措辭問題（皆已於 DCG #11 閉環後由 Owner 裁決）

### 14.1 准入規則的正式措辭：Independent Structural Distinction Gate（Owner 裁決）

**這一條修正了本專案長期口頭使用、但從未正式寫下的一條規則。**

原規則寫作「**只有增加一個新的 causal structure，才足以新增 construct**」。該措辭有兩個問題：

1. 「causal structure」易被字面讀成新節點／新因果邊／新 process，
   而 #11 已證明：**新增 construct 不要求新增 causal edge / node。**
2. 「不能還原為既有 construct / relation vocabulary」把
   「**可以用舊詞描述**」誤當成「**完全由舊結構決定**」。
   這兩件事不同；混淆會誤殺 World / Free Growth / Interaction。

**Owner 裁決：前兩版皆非最終答案。採 ISD Gate。**

> ### **Canonical Construct Admission Rule — ISD Gate**
>
> **A candidate earns construct status only when it introduces an
> independently specifiable structural distinction that is not determined by the
> existing construct/relation dimensions and therefore partitions otherwise
> equivalent causal systems into different classes.**
>
> **中文：候選項目只有在引入一個可獨立指定、不能由既有 construct / relation
> 維度決定的結構性差異，並因此能把原本在既有 causal description 下等價的
> 系統分成不同類別時，才取得 construct 地位。**

**正式定義：**

ISD(C) 成立，當且僅當存在 X、Y 滿足：
`ExistingDims(X) = ExistingDims(Y)` 且 `C(X) ≠ C(Y)`

即：**若存在兩個在既有 ontology 描述下相同、但在候選 construct 上不同的
causal systems，則候選項目具有獨立 structural distinction。**

**核心測試：**

```
Existing vocabulary
        ↓
能否已經決定 candidate 的真假？
    │
    ├─ YES → 只是 projection / composition / vocabulary
    │         → 不新增 construct
    │
    └─ NO
        ↓
   存在兩個 systems：
   在既有 dimensions 上完全相同，
   但 candidate 不同
        ↓
   candidate 引入新 structural distinction
        ↓
   → 可以成為 construct
```

**為何不再稱為 irreducibility gate：**
真正檢查的不是「這個詞不能用舊詞描述」，
而是「**這個 structural distinction 是否能被既有 dimensions 完全決定**」。
兩者在功能上差一個量級。

---

### 14.1.1 🔴 六項 construct 的統一回溯檢查

| Construct | ISD Gate | 理由 |
|---|---|---|
| **Agent** | ✔ | lineage ＋ instantiation 結構，非既有維度可決定 |
| **World** | ✔ | **可用** Agent 描述（「不是任何 Agent 的 dynamics」），但**這不等於由 Agent 決定**。兩個 process 都可有 persistence、causality、state transition，卻在「transition dynamics 是否屬於某個 Agent trajectory 的 reducible image」上不同。新增的是 **causal process ownership / non-Agent-image dynamics** 這個 process-structural distinction。「補集」只是表示方式 |
| **Memory** | ✔ | `PastDirected(R,E) + CausallyAffordance(R,A)`。可構造兩個系統：same provenance、same event history、same Agent、same representation existence，**但 R 在 𝒯_A 中 causally inert**。既有 vocabulary 無法分開，Memory 可以 |
| **Growth** | ✔ | 無新 external input 條件下的 revision 與 subsequent causal participation，是新的 transition-level structure |
| **Free Growth** | ✔ | `FreeGrowth ⇒ Growth` 不妨礙入列：**construct 不要求完全不引用既有 construct，否則任何 refinement 都不可能存在。**兩個系統 `Growth = 1`，但 past content 是否參與 revision formation 不同 ⇒ 新增 **revision formation 對 past representation content 的 causal dependence** |
| **Interaction** | ✔ | 兩個系統在 Agent、World、一般 Agent-Agent causality 上都成立，但一個 reciprocal、一個非 reciprocal ⇒ 新增 **coupling topology**。不是 `Agent↔Agent` 換名字 |
| **History** | ✘ | `ExternalExposure(E,A) ∧ E ↝ A_later` 一旦 relation 給定，真假即被完全決定，**不存在「相同但 History 不同」的情形** ⇒ projection |
| **Awareness** | ✘ | Agent–X relation 給定後，沒有額外 structural bit 可再區分兩個系統 ⇒ descriptive family vocabulary |
| **Temporal Identity** | ✘ | architecture-defined relation |

> **六項現有 constructs 經統一標準回溯檢查後全部保留。**
> **不需 rollback DCG #7 / #5 / #6，不需重開任何 DCG。**
>
> **本輪是修正准入規則的 meta-definition，不是推翻 Memory。**
> **Memory 仍是十一輪裡第一次真正新增的 construct。**

---

### 14.1.2 🔴 Anti-slip：可用舊詞描述 ≠ 由舊結構決定

```
Old vocabulary can describe it   ≠   Old structure determines it
```

**「能用既有詞彙寫出公式」從來不是 irreducibility 的反證。**
真正的反證是：

> **candidate 的真假已被既有 structural dimensions 完全決定。**

**這是拿去打 DCG #12 的尺。**
### 14.2 §3 否定清單漏列 recoverability

Conclusions 的否定清單原列了 storage / state / parameter-list / occurrence /
codebase dataflow，**漏列 information-theoretic recoverability**。
§7 文字已保留該警告，清單應與之一致。

**Owner 裁定：同意。§3 已補入該項，並統一為以下措辭——**

> storage location / state persistence / parameter-list membership /
> codebase dataflow / occurrence / information-theoretic recoverability
> **都不能單獨構成 Memory。**

### 14.3 OQ 內記錄傾向

原收斂稿在 Open Questions 中寫「本輪已偏向後者」。
**Open Question 不得承載 decision。**要嘛寫成已決，要嘛純記為 open。
本檔 §13 採後者，不保留傾向。**Owner 已確認此修正符合 DCG 規則。**

---

## 15. Non-Claims

**本輪沒有證明：**

- ❌ Memory = human remembering
- ❌ Memory = conscious recollection
- ❌ Memory implies awareness
- ❌ Memory implies understanding
- ❌ Memory implies Soulness
- ❌ Memory must be semantically meaningful
- ❌ Memory must be persistent forever
- ❌ Memory 必須存放在 `data/soul/<agent_id>/`
- ❌ `life_threads.jsonl` 已經被證明是 Memory

> **最後一點必須保留：**
> **架構上存在 provenance，不等於已經存在 Memory；
> 必須再有 Agent-transition-level causal affordance。**

---

## 16. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | Representation 條件成立，但內容不是 information content 而是 **provenance**。`data/soul/agent_akane/` 架構事實：`Fact` 帶 `source`／`event_time`／`merged_from`／`inner_life_event_id`／`invalidated_at`；`diary/` 有真實遺忘缺口（07-28、07-29 缺） |
| **Round 2** | 收緊為 **accessibility**，且必須定義為 reachability 而非 storage。但主大腦提出的 `R ∈ dom(f)` 被判定只是 syntactic plumbing |
| **Round 3** | 改為 **Level 3 causal affordance**；主大腦指出量化域錯誤（`f(R)==17` 反例）與 `f` 未限於 Agent machinery（audit script 反例）；並提出 **循環性風險** |
| **Round 4** | `PossibleValues` 收成 `Realizable(R)`（producer 決定，非 consumer 決定）；`𝒯_A` 由 logical instantiation lineage 獨立定義，切斷循環。**Computability limitation ≠ definition failure** |
| **Round 5** | **裁定 (B)：Memory 進 Construct Ontology。**准入理由收窄為 introduction of a new causal-functional structure |

---

## 17. 🔴 本 DCG 主大腦的自我修正

| # | 輪次 | 主大腦主張 | 被何推翻 |
|---|---|---|---|
| 1 | **R1** | 以 `data/soul/agent_akane/` 內的 provenance 欄位與檔案佈局作為 **Memory 存在的證據** | **Owner Round 2 指出 storage location 不決定 agent-side status**。這些欄位只支持 PastDirected，**不支持 Memory** |
| 2 | **R2** | `Accessible(R) ≝ ∃f, R ∈ dom(f)` | **Owner Round 3 指出這只是 syntactic admissibility**，`f` 必須限於 Agent trajectory transition，且 witness 值須限於 Realizable(R) |
| 3 | **R4** | 把准入判準押在 decidability 上 | **Round 4 前即撤回**。Decidability 是架構 × 實例的性質，不是 construct 的性質。**World 本身即不可判定卻已在清單內** |

> **第 1 項是本輪最重要的教訓：架構事實可以支撐一個條件，
> 但不能支撐它之上的所有推論。provenance 欄位存在 ≠ Memory 存在。**