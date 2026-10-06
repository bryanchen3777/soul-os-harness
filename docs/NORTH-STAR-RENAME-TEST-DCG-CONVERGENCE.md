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

## 8. 剩餘六項（方法層收斂時未裁定；**application 已在 §13 完成**）

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
---

## 13. Application：六項逐項裁定

**限制（Bry 訂立）：**「原始問題」限制在各自 canonical DCG **已經真正提出過**的問題；
**不另外替候選詞創造一個更強的語義版本。**
**否則 Step 1 本身就會偷渡新問題。**

| Candidate | Step 1：中性化後的 residual | Step 3：tracked carrier | QPF | 裁定 |
|---|---|---|---|---|
| **Motive** | 無獨立 residual；#14 已還原為既有 goal / memory-derived structure | — | **0** | FAIL |
| **Decision** | 無獨立 residual；selection 已在 𝒯_A 內 | Agent / 𝒯_A / Branching | **0** | FAIL |
| **Expression** | 無獨立 causal / research residual；#15 為 composite reduction | Agent / World / Interaction / Memory | **0** | FAIL |
| **Commitment** | 「持續約束未來」未形成獨立於既有 causal machinery 的問題 | retained past-derived R → Memory | **0** | FAIL |
| **Self-Binding** | 同型；#15 明文「不能另立 self-constraint kind」 | Memory / 𝒯_A | **0** | FAIL |
| **Temporal Constraint** | **residual 存在**：「時間是否具有獨立 causal constraining role」 | **Temporal Semantics / Time Base ＋ #16 的 temporal-mechanism 問題** | **0** | FAIL |

### 13.1 Temporal Constraint 是六項中最需要 Step 3 的一項

**它的 FAIL 不是「Step 1 沒問題」，而是 Step 1 有 residual、Step 3 找得到 carrier。**

**這同時驗證了 §3 擴大的 Tracked 定義：convention candidate 也能承接問題，
不必等它升格成 construct 才算「沒有遺失」。**

> ⚠️ **這是六項中匹配最緊的一項，理由必須留下以便稽核：**
> residual 的原始措辭是「時間是否具有一個獨立於既有 Agent / World / Interaction 的
> causal constraining role」；DCG #16 記錄的是「temporal mechanism / causal clock
> 是否具有 causal effect，以及其 locus 在 𝒯_A / World / input 的哪一處」。
> **兩者是同一問題在 clock 層級的表述**，不是兩個相鄰問題。
> **若未來有人主張這是「merely related」，本項必須重跑。**

### 13.2 Agency 與 Self-Binding 的差別（不是高下之別）

| | decomposition 後的結果 |
|---|---|
| **Agency** | **真的冒出了 prospective-directedness**，所以 Round 3 一度看起來像 PASS |
| **Self-Binding** | **沒有形成新的 residual candidate** |

**這不是「Agency 比 Self-Binding 高級」，只是兩次 decomposition 的結果不同。**

---

## 14. 六項跑完後的分層

```
QUESTION
└─ Soul
   └─ QPF = 1 / PASS

VOCABULARY
├─ History                    （未跑 rename test）
├─ Lived Experience           （未跑 rename test）
└─ Awareness
   └─ QPF = 0 / FAIL           ← ⚠️ 見 §15.1

NOT ADMITTED
├─ Agency                     QPF = 0 / FAIL
├─ Motive                     QPF = 0 / FAIL
├─ Decision                   QPF = 0 / FAIL
├─ Expression                 QPF = 0 / FAIL
├─ Commitment                 QPF = 0 / FAIL
├─ Self-Binding               QPF = 0 / FAIL
└─ Temporal Constraint        QPF = 0 / FAIL
```

---

## 15. 🔴 三處範圍問題（**本輪不裁定，記為 open**）

### 15.1 🔴 Awareness 在 VOCABULARY，但 QPF = 0

**這是一個實質不一致，不是記錄問題。**

- **DCG #8** 把 Awareness 放進 VOCABULARY，其依據是「awareness 問題由解讀回答，
  不由結構回答」——**那是 structural / interpretive 的理由。**
- **DCG #19** 的 QPF 測的是另一件事：**該詞是否保住一個既有 vocabulary 無法承載的問題。**
  Awareness 的答案是 **否**。

> **⇒ 同一個詞在兩輪用不同判準得到不同層級的答案。**
> **依 §4 的 anti-slip「QPF result ≠ validity proof of the test」，
> 這不自動意味 Awareness 應離開 VOCABULARY；但它意味著
> 「Awareness 為何在 VOCABULARY」目前有兩個互不引用的理由。**

**本輪不裁定。**需另開一個極小的 decision：Awareness 的 vocabulary placement
以 DCG #8 的理由為準，還是以 #19 的 QPF 為準。

### 15.2 🔴 History 與 Lived Experience 從未跑過 rename test

**§14 的表把它們列在 VOCABULARY 之下，但沒有 QPF 值——因為它們沒有被測試過。**

DCG #19 的實例只有 **Soul、Awareness、Agency** 三個 anchor，加上本輪的六項。
**History 與 Lived Experience 從未進入任何一輪。**

> **⇒ 「vocabulary 層已完成 rename test application」這個說法目前不成立。
> 成立的說法是：「UNPLACED / OPEN 六項 ＋ Agency 已完成」。**

### 15.3 Branching 的 rename test 適用性未定義

`Branching` 在 **STRUCTURAL PROPERTIES** 層，不在 VOCABULARY 層。

> **Rename Test 的判準是為「vocabulary admission」設計的。
> 它是否適用於 structural property 層，目前未定義。**

---

## 16. Fixpoint（已成立，但有條件）

**六項全部 FAIL ⇒ 沒有任何新的 admitted vocabulary ⇒ dependency 不產生新的接管者
⇒ 沒有新的 downstream rename rerun。**

$$\boxed{ \text{No new vocabulary admission} \;\Rightarrow\; \text{current rename evaluation reaches fixpoint} }$$

> ### ⚠️ **此 fixpoint 條件於：tracked-question registry 與 canonical decomposition 均未改變。**
> **若 DCG #16 的 Temporal Semantics 升格為 convention，或任何 decomposition 被推翻，
> scope 內的 Rename Test 必須重跑。**

---

## 17. 真正被打穿的是什麼（**不是「六個都 FAIL」**）

$$\boxed{ \text{construct rejected} \;\not\Rightarrow\; \text{vocabulary rejected} }$$

**這不是從 Agency 一例推出的，而是逐項重新測過之後，結果才恰好全部落在 FAIL。**

**而 Soul 是唯一一個 residual question 找不到承載者的詞。**

> **Soul 被保留，不是因為 Soul 這個詞「重要」、神秘或直覺上不能拿掉；
> 而是因為移除它後，確實有一個既有 tracked ontology 無法承載的 research question 失去歸屬。**

**反過來：其他七個詞全部能被中性化，其問題要嘛沒有 residual，
要嘛已被既有 construct / relation / open candidate / convention candidate 接住。**

---

## 18. Non-Claims（本輪 application 部分）

**本輪沒有證明：**

- ❌ Rename Test 的 validity 已被證明（只有 consistency check）
- ❌ 這七個詞在一般語言、哲學或產品設計上「沒有價值」
- ❌ 任何 canonical placement 永遠正確
- ❌ 未來 upstream 變更不會使這批結果失效
- ❌ History / Lived Experience / Branching 的 placement 已通過 rename test（見 §15）
- ❌ Awareness 應離開 VOCABULARY（見 §15.1，**本輪未裁定**）