# North Star — Architecture Seed / Substrate Boundary（**DCG 收斂：Terminal State**）

> **Discussion Convergence Gate 產物**。Round 1–5 完成，四項交付齊備。
> **Terminal State：UNIDENTIFIABLE UNDER CURRENT OBSERVATION SURFACE**
> **本輪未新增 construct，也未封閉 ontology。它撞到 ontology 自身的 epistemic ceiling。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### $$\boxed{ \text{Ontology causal completeness is not identifiable from the current ontology alone.} }$$

$$\boxed{ Internal\ closure \neq Ontology\ completeness }$$

**本題的終點不是「第七個 construct 沒找到」。**
**是我們第一次撞到 ontology 自身的 epistemic ceiling。**

---

## 2. 🔴 本輪成立的 reduction

$$\text{CausalEffect}(\Delta I) \;\Rightarrow\; \Delta S_A \;\lor\; \Delta World \;\lor\; \Delta Interaction$$

而 ISD 要求：

$$ExistingDims(X)=ExistingDims(Y) \;\land\; C(X)\neq C(Y)$$

**因此對目前這套 ontology：**

$$\boxed{ ISD(\Delta I)=\mathrm{false} \quad\text{（至少沒有可成立的 witness）} }$$

**主大腦 Round 5 曾把這一步寫成「CEG 對任何候選都必然失敗」。Owner 收緊為：**

> 這對目前這套 causal vocabulary 與 boundary conventions 下的 **internal reduction** 成立；
> **不能升格成「所以 ontology 在六項上 causal-complete」。**

---

## 3. 🔴 A / B 兩讀法不可分辨

| 讀法 | 內容 | 結論 |
|---|---|---|
| **A** | ontology 真的完整 | 所有 causal difference 確實落在六項 ⇒ **六項封閉** |
| **B** | ontology boundary 太粗 | 「落在既有維度」只是因為那些維度包得太大 ⇒ **refine 之後會出現新 construct** |

**目前沒有一個獨立於 ontology 的 criterion 能區分 A / B。**

**而 (B) 有具體實例：DCG #7 的 World 定義過於寬。**
一個 clock、scheduler、ambient temperature、filesystem、random generator
**全部字面滿足「causally continuing process not reducible to any Agent trajectory」。**

> **⚠️ 這個實例只是提出，本輪未證明 DCG #7 的 World 過寬。**
> **但它顯示「ontology 顯得 causal-complete」可能只是因為它容易解釋一切。**

---

## 4. 正式 Conclusions

### 4.1 Architecture provenance

$$Architecture\ origin \neq independent\ causal\ structure$$

**目前沒有 ISD witness。**若 $$S_A=(Seed,\ Memory,\ World\_context,\dots)$$，
改變 Seed 就是 $$S_A^X\neq S_A^Y$$ ⇒ 無法保持 `ExistingDims` 相等（DCG #15 §3 的論證直接適用）。

### 4.2 Temporal Semantics

**暫記為 `DOCUMENTED CONVENTION CANDIDATE`，不是 ontology law。**

```
Temporal constructs are evaluated relative to a chosen
temporal semantics / time base.
```

**凍結前仍待確定四件事：**什麼東西算 Time Base／semantic time base 與 causal time
mechanism 是否同一對象／temporal predicates 是否應把 time base 當顯式參數／
causal clock 若存在如何與 𝒯_A、World 區分。

### 4.3 Temporal Mechanism

**已確認具有 causal possibility。**
但其 causal effects 在目前 ontology 中皆可 reduction 到：

$$Agent \;\lor\; World \;\lor\; Interaction$$

**因此沒有形成已證實的新 construct。**

### 4.4 Invocation

**不能因為 stateless 就排除 input 或 process。**

| Invocation 的形態 | 歸位 |
|---|---|
| 作為 $I_A$ 的一部分被讀取 | 𝒯_A |
| 由 continuing process 產生 | World |
| 由另一 Agent 施加 | Interaction |

**沒有獨立 witness。**

### 4.5 Meta-result

> $$\boxed{\text{這套六構造 ontology 是真 causally complete，還是因 Agent / World 邊界太粗而呈現 closure？}}$$

**回答它需要一個不以現有 ontology 自身為前提的獨立 completeness / granularity criterion。**
**這不是再找一個 Expression、Agency、Time、Action 詞可以解的。**

---

## 5. 十六次 DCG 在 action / expression 線上的鏈

```
#12 Agency
    ↓ decompose
#13 Branching
    ↓ structural property, not construct
#14 Motive / Decision
    ↓ reduction, no independent dimension
#15 Expression / Commitment / Self-Binding
    ↓ composite reduction + documented categorical gap
#16 Temporal Mechanism / Invocation
    ↓ all observed causal effects reduce to admitted dimensions
    ↓ BUT internal closure cannot prove external completeness
```

> **這不是「第七個 construct 沒找到」。這是我們第一次撞到 ontology 自身的 epistemic ceiling。**

---

## 6. 十六次 DCG 後的分層

```

GOVERNANCE / MODELING LAYER
    Ontology Privilege Policy: Gate-only / Admissibility Closure
    ├─ Admission: ISD + CEG
    ├─ Second-layer privilege rule: none
    ├─ Review Trigger: a new refinement/candidate passes both ISD and CEG
    └─ Change: Owner; re-evaluate all placements within declared scope
    (政策全文：docs/ONTOLOGY-PRIVILEGE-POLICY.md)

—— 以上為 governance / modeling layer，不屬於 causal ontology ——

QUESTION
├─ Soul
│  └─ QPF = 1（#19 §5.1；governance parameter 已宣告）
└─ Lived Experience
   └─ QPF = 1（#19 §30.2；governance parameter 已宣告 ⇒ Governable）

VOCABULARY
    ├─ Awareness          placement source: DCG #8（descriptive family）
    │                     parameter: Agent–X relation expansion（#8 §5）
    │                     ⚠️ 「testable」未形式化 ⇒ partially formalized
    └─ History            placement source: DCG #9（relation-derived set）
                          QPF = 0（#19 §30.1）
                          admission rule: OPEN（privilege 非結構決定）
    （Vocabulary privilege 不能冒充 causal entailment；見 DCG #20 §2）

UNPLACED / OPEN（QPF 已測；無 declared placement rationale）
    Agency                  QPF=0   construct: rejected / vocabulary: OPEN
    Motive                  QPF=0   construct: rejected / vocabulary: OPEN
    Decision                QPF=0   construct: rejected / vocabulary: OPEN
    Expression              QPF=0   construct: rejected / vocabulary: OPEN
    Commitment              QPF=0   construct: rejected / vocabulary: OPEN
    Self-Binding            QPF=0   construct: rejected / vocabulary: OPEN
    Temporal Constraint     QPF=0   construct: rejected / vocabulary: OPEN
    （QPF≠0 不構成 vocabulary rejection；見 DCG #19 §25 的對稱封堵）

DOCUMENTED GAPS
    Architecture-seeded, non-past-derived causal state (Horn D)
    └─ construct: not established
       └─ relation: not established

DOCUMENTED CONVENTION CANDIDATES
    𝒯_A invariance convention            (frozen, DCG #15)
    Temporal Semantics / Time Base        (candidate, DCG #16)

OPEN EPISTEMIC QUESTION
    已被 Owner 裁定：見上方 GOVERNANCE / MODELING LAYER
    （DCG #17 / #18 已把它取代為「canonical boundary 可決定、privileged granularity 不可唯一決定」）
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

> 🔴 **此 open question 已在 DCG #17 / #18 被實質取代，請以那兩份為準。**
>
> **DCG #17**：`CausalFacts ⇏ UniqueGranularity`
> **DCG #18**：`CausalFacts → Canonical Distinguishability Boundary`，但 `⇏ Unique Privileged Granularity`；
> 並找到 **$C^\*$**（canonical causal quotient，條件於固定 intervention semantics）。
>
> **A / B 的真正形態是「在 canonical boundary 上選哪個 quotient / representation」，
> 而不是「不可分辨」。Terminal State 已改為 `DEFERRED TO MODELING / RESEARCH-INTENT DECISION`。**
>
> **現況見 `docs/NORTH-STAR-ONTOLOGY-COMPLETENESS-GRANULARITY-DCG-CONVERGENCE.md`
> 與 `docs/NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md`。**

---

## 7. Open Question

**這套六構造 ontology 究竟是真的 causally complete，還是只是因為 Agent / World 的
boundary 足夠寬而呈現出 closure？**

**回答它需要一個不以現有 ontology 自己作為前提的獨立 completeness / granularity criterion。**

> ### 🔴 下一題應改題目層級
> **DCG #17 — Ontology Completeness / Granularity Criterion**
> **唯一問題：要用什麼「不偷用自身 ontology」的判準，
> 判斷「六項封閉」是真完整，還是只是邊界太粗？**
>
> **這會是第一次真正從「找 construct」轉成「驗 ontology 本身」。**

---

## 8. Non-Claims

**本輪沒有證明：**

- ❌ 六個 constructs 是宇宙級完整 ontology
- ❌ 第七 construct 不可能存在
- ❌ World 的廣義 boundary 一定正確
- ❌ Temporal mechanism 在所有架構下都不是 construct
- ❌ CEG 在任何可能的 ontology refinement 下都必然拒絕新 structure
- ❌ 目前 ontology 已經達到 metaphysical completeness

---

## 9. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 拆 A（provenance）vs B（causal substrate）。封死「provenance label 不同 ≠ causal structure 不同」。**並自行察覺差一步要用 Essence／Nature／Ground 命名尚未證明的對象** |
| **Round 2** | 接受主大腦的「時間基改變判定 → 不是 witness」為**循環論證**（用依賴 time base 的 construct 證明 time base 不結構）。提出 `Temporal Semantics ≠ Temporal Mechanism` |
| **Round 3** | 接受 `not stored as data ≠ not input` 的收窄修正。接受 `stateless ≠ no dynamics`。指出 DCG #7 字面上使 clock 成為 World |
| **Round 4** | **凍結 `implementation boundary ≠ causal ontology boundary`。**降級主大腦的「第四支」為候選機制而非候選 construct。給出 `ΔI → {ΔS_A, ΔWorld, ΔInteraction, 0}` |
| **Round 5** | **收斂到 internal closure。**主大腦一度推成「CEG 必拒任何候選」，**Owner 收緊為 internal reduction**，並正式建立 `Internal closure ≠ Ontology completeness` 與 A/B 不可分辨 |

---

## 10. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 「時間基改變 Growth/Memory 判定 ⇒ 不是 ISD witness」 | **循環論證。**同一 trajectory 在兩種時間分割下可得不同判定而系統不變。**用依賴 time base 的 construct 證明 time base 不結構。**Owner R2 駁回，結論整條撤回 |
| 2 | **R1** | 「stateless rule 無 dynamics ⇒ 不是 World process」 | **DCG #7 從未要求 process 具有 internal state。**$t\bmod 5=0$ 的 emitter 是 time-indexed causal process。Owner R3 駁回 |
| 3 | **R2** | 「execution rule 不是 input，因為沒有東西把它當資料讀取」 | **定義太窄。**𝒯_A: $S_A\times I_A\to S_A$，而 $I_A(t)$ 可以是 event occurrence 而非 stored data。**架構旁證：TriggerEnvelope 明定為 Scheduler→Agency bridge input，HeartbeatEngine 持續產生 SYSTEM_TICK。**Owner R3 駁回 |
| 4 | **R5** | 「CEG 對任何候選都必然失敗」 | **措辭過強。**對目前 ontology 的 internal reduction 成立，但不能升格為 ontology completeness。**Owner 收緊** |
| 5 | **R1** | 差一步用 `Essence` / `Nature` / `Ground` 命名 B | **主大腦自行察覺並放棄。**因為這些詞預設了「架構對 agent 有本質性貢獻」，而那正是尚未證明的東西。**又一次把 umbrella 名稱偷渡成結論的衝動** |

> ### 第 5 項是本輪最重要的自我修正
> **寫這一輪時腦中浮現過 `Essence`／`Nature`／`Ground` 三個詞，全部被主大腦自己擋下。**
> **理由與 DCG #12 完全相同：名字不能預設它所命名的對象的性質。**
> **開 #16 的紀律由 Bry 訂立（不得直接用 `Soul Seed`），主大腦自己加了一條：**
> **也不得用預設了答案的詞。**