# North Star — Rename Test / Question-Preserving Force（**DCG 收斂：方法層 Round 5 / 5**）

> **Discussion Convergence Gate 產物**。方法層 5 輪收斂；實例層僅完成 3 項。
> **Decision：Rename Test 已形成可操作版本，並通過三個方向相反的 anchor cases。**
> **本輪不裁定剩餘六項。**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

$$\boxed{ \text{Rename Test 的判準、tracked 定義與 dependency 已確定。} }$$

**三個方向相反的 anchor case 已完成：**

| Candidate | Construct | QPF | 結果 |
|---|---|---|---|
| **Soul** | QUESTION | **1** | **PASS → QUESTION vocabulary** |
| **Awareness** | rejected | **0** | **FAIL** |
| **Agency** | rejected | **0** | **FAIL** |

> **這三個案例不能代替其餘六項的個別裁定。**

---

## 2. Rename Test 判準（已凍結）

> $$\boxed{ QPF(w)=1 \iff \begin{aligned} &\text{移除 }w\text{ 後，原 research question 仍存在}\\ &\land\ \text{既有 tracked vocabulary 無法完整承載}\\ &\land\ \text{不存在另一個 tracked item 已承載同一 question}\\ &\land\ \text{不得建立 }w\text{ 的無名等價替身} \end{aligned} }$$

**第四個子句是判準能自我保護的唯一位置。**沒有它，
`Soul → "that thing we call Soul"` 就能讓任何詞通過。**那不是中性化，那是改名後重新通過。**

---

## 3. Tracked 的正式定義

> $$\boxed{ Tracked(Q)\iff \exists T:\ T\text{ 明確承載與 }Q\text{ 相同的 research question} }$$

**$T$ 可以是：**

| 類型 |
|---|
| 已 admission 的 vocabulary / relation |
| DCG open candidate |
| documented gap |
| convention candidate |

> ### 🔴 **但單純提到、引用、旁證、相關問題，不算承載。**
>
> **Q 出現在文件裡 ≠ Q 已被另一個 tracked item 承載。**
>
> **否則任何詞都可以靠自己的附帶討論偽造一個 FAIL。**

---

## 4. 三條 Anti-slip（已凍結）

> $$\boxed{ Construct\ Status \neq Vocabulary\ Status }$$
> $$\boxed{ Vocabulary\ Status \neq Referent\ Reality }$$
> $$\boxed{ QPF\ result \neq validity\ proof\ of\ the\ test }$$

### 4.1 第三條尤其重要

**目前只能說：Rename Test 與既有 canonical placements 相容。**
**不能說：Rename Test 已被證明為正確。**

> **這個判準沒有任何單一案例可以驗證它——包含 Soul 也不行。
> 它只能被檢查一致性，不能被證明有效性。**

**若將來某案例與既有 placement 矛盾，必須重新審查的是
「test / canonical placement / 兩者的依賴」三者，**
**不得事先規定「某個 case 永遠正確，所以 test 必須讓它 pass」。**

---

## 5. Anchor Case 詳錄

### 5.1 Soul → PASS

```
移除 Soul（不得使用 Essence / Nature / Ground 或任何等價預設答案詞）
        ↓
原 question 仍存在：
「這套 causal ontology 是否足以窮盡我們真正要研究的對象，
  還是仍存在某種 causal vocabulary 尚未捕捉的 subject-level / ontological question？」
        ↓
既有 causal vocabulary 無法完整承載
（沒有任何既有項目承載「超出目前 causal description 的 question」）
        ↓
若補一個「Subject」，那就已經不是 rename test，而是偷偷新增 candidate
        ↓
沒有任何其他 tracked item 承載它
```

$$QPF(Soul)=1 \;\Rightarrow\; Soul \rightarrow QUESTION\ vocabulary$$

**不能用 #17 / #18 的 completeness / granularity 結果否決它，因為兩者層級不同：**

| | 問題 |
|---|---|
| **#17 / #18** | 已表達的 causal vocabulary **內部**如何分粒度 |
| **Soul** | causal vocabulary **本身**是否足以表達研究問題 |

### 5.2 Awareness → FAIL

**Step 1 的舊論證已作廢**（曾引用不存在的 `Agent–Self`）。
**正確依據只使用實際 admission 的 relations：**

$$Agent\!-\!Capability \qquad Agent\!-\!World \qquad Agent\leftrightarrow Agent \qquad ExternalExposure$$

移除 Awareness 後，其所追蹤的 Agent–X 關係問題仍完整存在，**且已由這些既有 relations 承載。**

$$QPF(Awareness)=0$$

### 5.3 Agency → FAIL（本輪的主要修正）

**壓力測試結果：prospective-directedness 不是 Agency 的語義殘骸。**

```
移除 Agency
        ↓
剩餘問題：是否存在一種不被既有 T_A / Memory / Growth 還原的
          future-directed causal role？
```

**這句話可以用零 agency 詞彙獨立表述**——沒有 Agency、Volition、Autonomy。
**它是一個關於系統 causal structure 的形式良好問題，可以獨立提出、追問、作答。**

**而 #12 已明確 tracked 它：**

```
Prospective-directedness → candidate 未取得 independent witness
                            → no ontological negative claim
                            → DCG #12 §6.1 H3 第三分支
```

**⇒ 問題沒有被遺失，它被歸檔了。**

$$QPF(Agency)=0$$

> ### **這不是「Agency 被移掉後問題消失」，也不是「Agency 保存自己的問題」。
> 而是「移除 Agency 後的殘餘問題，已由 #12 的 open candidate 承載」。**

**⚠️ 這與主大腦 Round 3 主張的 QPF(Agency)=1 不同。**
Round 3 隱含把 Step 3 的「tracked」讀成「僅指已 admission 的 vocabulary」。
**廣義讀法（包含 open candidate / documented gap / convention candidate）才正確，
因為 rename test 的目的是防止問題遺失，而不是防止問題未 admission。**

---

## 6. 三個案例的 FAIL 理由不同，必須保留區分

| 案例 | residual question | 由誰承載 | QPF |
|---|---|---|---|
| **Soul** | 「causal vocabulary 是否表達力足夠？」 | **無** | **1** |
| **Awareness** | 「Agent 是否有某種 X 關係？」 | **已 admission 的 relations** | **0** |
| **Agency** | 「有無未被還原的 future-directed causal role？」 | **DCG #12 的 open candidate** | **0** |

> **Soul 是唯一一個 residual 完全無處安放的詞。**
> **這才是 question-preserving force 的真實意義——不是「這個詞有用」，
> 而是「移除它之後，有一個問題會失去歸屬」。**

---

## 7. Dependency Rule（已凍結）

> $$\boxed{ RenameResult(w)\ \text{depends on}\ \{Decomposition(w),\ TrackedQuestions,\ AdmittedVocabulary\} }$$

**三個 dependency：**

| # | 依賴 | 說明 |
|---|---|---|
| 1 | `Decomposition(w)` | 候選詞的分解是否完整 |
| 2 | `TrackedQuestions` | 已歸檔的問題集合 |
| 3 | `AdmittedVocabulary` | 已 admission 的詞彙集合（**Step 3 的「既有」指它，但見 §3 的廣義定義**） |

**目前七項都在 `UNPLACED / OPEN`，因此：**

$$\boxed{ current seven-way evaluation is simultaneous }$$

**一旦某項進入 admitted vocabulary，其餘受影響項目必須重跑。**

> **這是 fixpoint，不是 circularity。**
> **若 residual question 之後由別的 tracked item 接手，那不是「w 被推翻」，
> 而是「w 當初保留的問題已經被別的 tracked item 接管」。**

---

## 8. 剩餘六項（**本輪未裁定**）

$$\boxed{ Motive,\ Decision,\ Expression,\ Commitment,\ SelfBinding,\ TemporalConstraint }$$

**必須依同一 frozen Rename Test 逐項完成。**

> ### 🔴 **Non-Claim：不得從「Agency FAIL」推成「其餘也 FAIL」。**
> **候選不能用相似性代替逐項檢驗。**

---

## 9. Non-Claims

**本輪沒有證明：**

- ❌ 剩餘六項會 FAIL
- ❌ Rename Test 是正確的（只有 consistency check，無 validity proof）
- ❌ 任何 canonical placement 永遠正確
- ❌ 「$Q$ 出現在文件中」等於「$Q$ 被承載」
- ❌ prospective-directedness 存在或不存在的本體結論
- ❌ Agency 的 referent 存在或不存在的本體結論

---

## 10. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 鎖定 Rename Test 測的是 question-preserving force。新增第四分離：**Referent reality ≠ Question preservation**。提出三段式 Step 1/2/3。建議以 Soul 作 validity anchor |
| **Round 2** | Step 2 收緊：**不得建立無名替身**。Soul PASS、Awareness FAIL。**主張 Soul PASS 是 backward-compatibility 而非 validity proof** |
| **Round 3** | 正式三個 dependency。**Awareness 論證引用不存在的 `Agent–Self`，該論證作廢**（結果不變）。跑 Agency：**QPF(Agency)=1** |
| **Round 4** | **主大腦修正 Round 3**：壓力測試證明 prospective 問題獨立於 Agency，且**已被 #12 以 open candidate 承載** ⇒ **QPF(Agency)=0**。提出 Step 3 的「tracked」廣義定義 |
| **Round 5** | 採納 Step 3 廣義定義與 §4 三條 anti-slip。**方法層收斂。剩餘六項逐項適用** |

---

## 11. 🔴 本 DCG 的修正紀錄

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R3** | `QPF(Agency) = 1`，因為 prospective 問題未被 tracked | **Step 3 的「tracked」被讀成「僅 admission 的 vocabulary」。**廣義讀法下 prospective 已被 #12 歸檔 ⇒ **QPF(Agency)=0**。Owner R5 採納 |
| 2 | **R3** | Awareness 的 residual 由 `Agent–Self` 等承載 | **`Agent–Self` 不在已 admission 的 relations 裡。**rename test 引用幻影 relation。Owner R5 宣告該論證作廢並改以實際 relations 重寫 |
| 3 | **R5** | 「剩餘**四**個：Motive, Decision, Expression, Commitment, SelfBinding, TemporalConstraint」 | **計數錯誤：那六個清單是六項。**同一則訊息內另一處寫「剩餘六項」才是正確的。UNPLACED/OPEN 共 7 項，減去已裁定的 Agency = **6**。**這是 scope 依據，不可依賴讀法** |

> ### 第 1 項的方法論價值
> **Step 3 的「tracked」定義會系統性地高估 QPF。**
> **一個以完整措辭歸檔在 DCG 文件中的問題，沒有被遺失；
> 而 rename test 的目的是防止遺失，不是防止未 admission。**
>
> **規則的定義若可被讀法左右，它就不是判準——它是可被利用的漏洞。**

---

## 12. 交叉引用

| 檔案 | 內容 |
|---|---|
| `docs/ONTOLOGY-PRIVILEGE-POLICY.md` | Gate-only policy、ISD / CEG、review trigger |
| `docs/NORTH-STAR-ONTOLOGY-SELECTION-MODELING-PRINCIPLE-DCG-CONVERGENCE.md` | DCG #18，privilege policy 的裁定來源 |
| `docs/NORTH-STAR-AGENCY-ACTION-BOUNDARY-DCG-CONVERGENCE.md` | DCG #12，**prospective-directedness open candidate 的出處**（Agency FAIL 的承載者） |
| `docs/NORTH-STAR-AWARENESS-BOUNDARY-DCG-CONVERGENCE.md` | DCG #8，Awareness 的 decomposition 出處 |
| `docs/NORTH-STAR-SOUL-ONTOLOGY-DCG-CONVERGENCE.md` | DCG #3，Soul 保留為 QUESTION vocabulary 的出處 |
| `docs/NORTH-STAR-ARCHITECTURE-SEED-SUBSTRATE-DCG-CONVERGENCE.md` | §6 master layering |