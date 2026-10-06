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

## 3. Tracked 的正式定義（**含 self-carrier 排除，Owner 裁定**）

> $$\boxed{ Tracked_w(Q)\iff \exists T\neq w:\ T\text{ 明確承載與 }Q\text{ 相同的 research question} }$$

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

### 3.1 🔴 Self-carrier 排除（本輪新增，Owner 裁定）

**$T \neq w$ 不只是「名稱不同」。$T$ 必須是獨立的 tracked item，
而不是 $w$ 自己的 placement rationale、decision statement、
open-question note，或同一條裁定的任何改寫。**

> ### **A candidate's own canonical placement rationale, decision record, or
> self-referential open-question statement cannot serve as its Step-3 carrier.**
>
> **候選先自己提出一個問題，再用自己提出的問題當作 Step 3 的 carrier，
> 那不是 tracking，那是 self-reference。**

**實例（兩端都會被這個漏洞誤判）：**

| 詞 | 若允許 self-carriage 會怎樣 |
|---|---|
| **Soul** | 「causal vocabulary 是否足以表達研究對象？」出現在 Soul 自己的 DCG ⇒ Tracked = true ⇒ **QPF(Soul) = 0**（錯） |
| **Lived Experience** | 「causal history ≠ subjective experience」出現在 #9，而 #9 就是 Lived Experience 的 placement decision ⇒ **QPF = 0**（錯） |

> ### **本條的真正意義：一個東西不能充當「用來測試它是否保存該問題」的獨立 carrier。**
> **否則 Rename Test 可以靠 self-description 被系統性操縱。**

### 3.2 本澄清的影響範圍

**這是對 #19 frozen method 的方法層 clarification，不是兩個 application 的修正。**

依 #19 §7 的 dependency rule，`RenameResult(w)` 依賴 `TrackedQuestions`，
而後者語義變更 ⇒ 檢查受影響的 application。

**但只有「曾把 candidate 自身的 DCG / placement record 當成 carrier」的案例需要重做。**

| 案例 | carrier | 是否受影響 |
|---|---|---|
| **Soul** | 曾用自己的 DCG | **🔴 需依新語義確認** |
| **Lived Experience** | 曾用自己的 #9 | **🔴 需依新語義確認** |
| **Awareness** | 已 admission 的 relations | 不受影響 |
| **Agency** | **#12 的 prospective open candidate（不同 tracked item）** | **不受影響** |
| **其餘六項** | 無 residual 或由其他 item 承載 | 不受影響 |
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
│
├─ question-preserving vocabulary
│  └─ （見上方 QUESTION: Soul，QPF = 1 / PASS）
│
└─ descriptive family vocabulary
   ├─ History                    （未跑 rename test）
   ├─ Lived Experience           （未跑 rename test）
   └─ Awareness                  QPF = 0
      └─ QPF=0 不否決 placement；placement 由 DCG #8 支撐（見 §19）

NOT ADMITTED
   ⚠️ 六項的 NOT ADMITTED 理由目前懸空：QPF=0 已於 §19 被宣告不構成否決，
     而 descriptive family vocabulary 的準入判準尚未凍結（§21）。
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
---

## 19. 🔴 Vocabulary Status 與 QPF 的正交性（Owner 裁定）

> **Awareness remains in VOCABULARY under DCG #8's descriptive-family criterion;
> DCG #19 records QPF(Awareness)=0 as an orthogonal property, not as a
> vocabulary-placement veto.**
>
> **Awareness 依 DCG #8 的 descriptive family vocabulary 判準保留於 VOCABULARY；
> DCG #19 的 QPF(Awareness)=0 僅表示它不具 question-preserving force，
> 不構成其 vocabulary placement 的否決。**

$$\boxed{ Placement\ criterion \neq QPF\ measurement }$$

### 19.1 兩種 vocabulary 性質正式分開

```
VOCABULARY
├─ question-preserving vocabulary
│  └─ Soul
│     └─ 保護「沒有其他 carrier 能承載的問題」
│
└─ descriptive family vocabulary
   └─ Awareness
      └─ 為已定義結構提供自然語言的 family name
```

> ### **QPF=0 ≠ Vocabulary Status=FAIL**
> **兩者在 #19 中從未被形式綁死。**

**#8 與 #19 因此不是兩個互相競爭的理由，而是兩條不同的 vocabulary property。**

### 19.2 #19 自身定位的收緊

> **#19 可以安全宣稱的只有：它判定一個詞是否具有 question-preserving force。**
> **它不得默認成「QPF=0 ⇒ 該詞不能存在於任何 vocabulary layer」。**

**否則就會觸發 #19 自己的 anti-slip：`QPF result ≠ validity proof of the test`。**

---

## 20. 🔴🔴 但這條規則產生了一個直接後果：**那六項的 NOT ADMITTED 理由消失了**

**這是本輪必須記錄的推論後果，不是新的爭議。**

| | 狀態 |
|---|---|
| **本輪之前** | 六項的 NOT ADMITTED 由 **QPF=0** 支持 |
| **本輪裁決** | **QPF=0 不構成 vocabulary placement 的否決** |
| **⇒ 現在** | **六項的 NOT ADMITTED 沒有被引用的理由** |

**它們需要下列其中之一，否則 NOT ADMITTED 是懸空的：**

- **(a)** 明文聲明：這六項**從未提出過 descriptive-family claim**，因此沒有第二條准入路徑可用
- **(b)** 一條 **descriptive family vocabulary 的准入判準**，而它們不通過

**本輪未裁定。記為 open。**

---

## 21. 🔴 descriptive family vocabulary 目前沒有准入判準

**這與 §20 是同一個問題的兩面。**

若「descriptive family vocabulary」是有效的第二條准入路徑，則**幾乎任何為既有家族命名的詞都合格**：

| 詞 | 它命名的家族 |
|---|---|
| Awareness | Agent–X relations |
| **Decision** | 𝒯_A 內部的選擇機制 |
| **Motive** | goal / memory-derived structure |
| **Temporal** | 全部時間相關結構 |
| **Causality** | 全部因果結構 |

**⇒ 若不補判準，§13 的六項 FAIL 都可以用 descriptive-family 路徑重新入場，
而且理由與 Awareness 完全相同。**

**這正是「一個 gate 的 failure 不會自動變成另一個 gate 的 pass」的對稱版本：
沒有 gate 的 pass，就沒有 NOT ADMITTED。**

### 21.1 主大腦提出的候選判準 —— 🔴 **已撤回，見 §24.1**

**觀察：目前 VOCABULARY 的三項共享一個形式——它們是 rejection records。**

| 詞 | 它記錄的 construct-status boundary |
|---|---|
| **Awareness** | Agent–X relations 明確**被拒絕**成為 construct |
| **History** | `Event↝Agent` 的 projection，**被拒絕**成為 construct |
| **Lived Experience** | **被拒絕**，且需要 subject-level discriminator |

**而 Motive / Decision / Expression / Commitment / Self-Binding / Temporal Constraint
是 reductions——它們的內容被吸收進既有 constructs，不是 boundary 記錄。**

> **候選判準：VOCABULARY 承載 *rejection records*——
> 記錄一個已被畫下、且被記錄下來的 construct-status boundary；
> 而非單純「為既有家族命名」。**

**⚠️ 這個候選判準自己也還有洞，我必須講清楚：**
1. 它事後合理化了一個 DCG #8 當時並未明文的分類
2. 它需要再說明「被吸收」與「被拒絕」的界線在哪裡——`History` 是 projection（被吸收？）
   卻仍在 VOCABULARY，**這條判準必須解釋為什麼 History 不是 reduction**
3. 它尚未被測試過能否排除任何具體候選

**⇒ 這是提案，需 Owner 裁決，且需回頭檢查 DCG #8 的原始理由是否被擴張。**

---

## 22. 三個 open item 的最新狀態

| # | 項目 | 狀態 |
|---|---|---|
| ① | **Awareness** | **✅ CLOSED** — 保留 VOCABULARY；QPF=0 為正交性質，非否決 |
| ② | **History / Lived Experience 的 Rename Test** | **OPEN** — 確實未跑，且不得由 Awareness 的處理方式類推 |
| ③ | **Branching 的 Rename Test 適用性** | **OPEN** — 屬 STRUCTURAL PROPERTIES 層，目前無 scope declaration，**不應硬套** |

**新增 open item：**

| # | 項目 | 狀態 |
|---|---|---|
| ④ | **descriptive family vocabulary 的准入判準** | **OPEN** — 見 §21。**在此判準凍結前，§13 六項的 NOT ADMITTED 理由懸空** |

---

## 23. Non-Claims（新增於 §19–§22）

**本輪沒有證明：**

- ❌ descriptive family vocabulary 應該有自己的准入判準
- ❌ §21.1 的 rejection-record 判準成立
- ❌ §21.1 的候選判準沒有把 DCG #8 的原始理由擴張
- ❌ 六項 NOT ADMITTED 的理由已被補上（**它們目前是懸空的**）
- ❌ History 與 Lived Experience 的 QPF 值
- ❌ Awareness 的 VOCABULARY placement 受到 QPF 支持（**它由 #8 支持，與 QPF 無關**）
---

## 24. 🔴 Owner 裁定：六項退回 `VOCABULARY PLACEMENT = OPEN`

### 24.1 先撤掉主大腦 §21.1 的 premise

> **「三個 VOCABULARY 共享同一形式（rejection record）」這個前提不成立。**

| 詞 | canonical 理由（回頭查 #8 / #9） |
|---|---|
| **Awareness** | **descriptive family vocabulary**：已定義 Agent–X relations 的自然語言家族名 |
| **History** | **relation-derived set / projection**：causal graph 的 projection，不是 relation family |
| **Lived Experience** | 保留 **subject-level semantics**，因為目前沒有可識別 discriminator |

> $$\boxed{ \text{Awareness / History / Lived Experience 並沒有共同的 vocabulary-admission form} }$$

**⚠️ 因此主大腦 §21.1 的「rejection record」候選判準撤回。**
**把三者都叫 rejection record 會把 #8、#9 當時不同的理由事後壓成同一類——
那是新的 modeling choice，不是從既有 DCG 必然推出的。**

**而且該判準的第 2 個洞（History 是 projection，為何算 record）本來就答不出來。**

### 24.2 六項的狀態修正

**QPF=0 既不是 vocabulary rejection 的依據，construct rejection 也不是。**
**既然沒有任何 vocabulary-admission criterion 存在，那麼它們從未被「考慮並拒絕」——
它們是未被考慮。**

$$\boxed{ \text{VOCABULARY PLACEMENT} = \text{OPEN} \quad\text{而非}\quad \text{REJECTED} }$$

**⚠️ 這不是「推翻六項」，而是撤回一個缺乏 admission criterion 支撐的 rejection。**
**而依 §26 對稱規則，禁止事後創造一個剛好把它們排出去的 gate——那是 post-hoc rationalization。**

### 24.3 🔴 連帶後果：**Agency 必須回到 OPEN**

**這是主大腦先前遺漏、且本輪因六項修正而浮現的。**

我的 §14 把 Agency 寫成 `vocabulary placement: rejected (QPF=0)`。
**那與 §24.2 的邏輯完全相同，而且同樣錯。**

**#19 只測量 QPF，它不決定 placement。**（§19.1 已凍結：
`Placement criterion ≠ QPF measurement`。）

| 詞 | QPF | 有無 declared placement rationale | 正確狀態 |
|---|---|---|---|
| **Awareness** | 0 | **✔ 有**（DCG #8 descriptive family） | **IN VOCABULARY** |
| **Agency** | 0 | **✘ 無** | **OPEN**（不是 rejected） |
| **其餘六項** | 0 | **✘ 無** | **OPEN** |

> ### **⇒ 沒有任何一個候選的 vocabulary placement 是「因 QPF=0 而被拒絕」。**
> **因為 QPF 不是 placement 判準。**

---

## 25. 🔴 對稱的雙向封堵（Owner 裁定）

> $$\boxed{ \text{No declared vocabulary-admission criterion} \;\Rightarrow\; \text{no candidate gains vocabulary admission by analogy} }$$

**這同時封住兩個方向：**

```
「Awareness 有 descriptive-family rationale」
        ↓
  ❌ 不能自動複製成其他候選的 admission
  （否則 Decision / Motive / Temporal 都能用同一路徑重新進場）

「QPF = 0」
        ↓
  ❌ 不能自動變成 vocabulary rejection
  （否則 #19 就變成 placement veto，違反 §19.1 的正交性）
```

> **這是「一個 gate 的 failure 不會自動變成另一個 gate 的 pass」的完整對稱版本：
> 沒有 gate 的 pass，也沒有 REJECTED。**

---

## 26. 🔴 #19 的自我評估：QPF 目前不預測任何 placement

**把 25 的結果放在一起看，會得到一個必須明說的事實：**

| | QPF | placement |
|---|---|---|
| **Soul** | **1** | IN QUESTION |
| **Awareness** | 0 | IN VOCABULARY（依 #8） |
| **Agency** | 0 | **OPEN** |
| **其餘六項** | 0 | **OPEN** |

> ### **QPF 目前不預測任何 placement 結果。**
> **Soul 的 QPF=1 與它的 placement 相關，但那是它唯一一個案例，
> 而且 §4.3 已凍結「QPF result ≠ validity proof of the test」。**

**⇒ 這是 #19 的貢獻，也是它不能當 admission gate 的原因。**
**#19 測量了一個與 placement 正交的性質。這正是 DCG #20 存在的理由。**

---

## 27. 更新後的分層

```
VOCABULARY（每一項各有自己的 declared placement rationale）
├─ Awareness          placement source: DCG #8（descriptive family）      QPF = 0
├─ History            placement source: DCG #9（relation-derived set）    未測
└─ Lived Experience   placement source: DCG #9（subject-level）           未測

UNPLACED / OPEN（QPF 已測；無 declared placement rationale）
├─ Agency                  QPF = 0（residual 由 #12 open candidate 承載）
├─ Motive                  QPF = 0（無 residual）
├─ Decision                QPF = 0（無 residual）
├─ Expression              QPF = 0（無 residual）
├─ Commitment              QPF = 0（無 residual）
├─ Self-Binding            QPF = 0（無 residual）
└─ Temporal Constraint     QPF = 0（residual 由 convention candidate 承載）

QUESTION
└─ Soul                    QPF = 1
```

> **注意：`UNPLACED / OPEN` 不再意味「construct rejected 但 vocabulary pending」，
> 而是「QPF 已測，但沒有任何 placement decision 存在」。**

---

## 28. DCG #20（已提議，未啟動）

> **DCG #20 — Vocabulary Admission / Descriptive Role Boundary**
>
> **唯一問題：什麼條件下，一個沒有獨立 causal structure 的詞，
> 可以取得正式 VOCABULARY placement？**
>
> **開題時必須先防三種偷渡：**
> 1. `QPF ≠ vocabulary admission`
> 2. `construct rejection ≠ vocabulary admission`
> 3. **`某一 canonical case 的 rationale ≠ universal admission gate`**
>
> **三個既有 case 作為約束案例，不是答案：**
>
> | 案例 | canonical rationale |
> |---|---|
> | Awareness | descriptive family |
> | History | relation-derived projection |
> | Lived Experience | subject-level / interpretive unresolved reference |
>
> **若找不到共同必要條件，完全可以得到：**
> $$\boxed{ \text{Vocabulary 沒有單一 admission criterion；不同 vocabulary kind 需要不同、明確宣告的 placement rationale} }$$
>
> **這比硬造一個 universal vocabulary gate 更乾淨。**

---

## 29. Non-Claims（新增於 §24–§28）

**本輪沒有證明：**

- ❌ 任何候選的 vocabulary placement 被「拒絕」
- ❌ rejection record 是一個有效的 vocabulary 形式（**該提案已撤回**）
- ❌ Awareness / History / Lived Experience 有共同准入判準
- ❌ DCG #20 一定能找到共同必要條件
- ❌ QPF 與 placement 完全無關（**Soul 是唯一 QPF=1 且 placement 相關的案例，但不足以支持預測力**）
- ❌ 任何 canonical rationale 可以推廣成普遍 gate
---

## 30. Application（第二批）：History 與 Lived Experience

**約束（#19 §13）：**「原始問題」限制在各自 canonical DCG 真正提出過的問題，
不另外創造更強的語義版本。

**依據原文（DCG #9）：**
- §1 —「History 只是對 causal graph 的一種 projection。它沒有創造新的 causal mechanism。」
- §7 —「『E is in A's causal history』不等於『A subjectively lived through E』。
  後者保留 subject-level 語義，而該部分**目前沒有 identification power**。」

### 30.1 History → QPF = 0

| Step | 結果 |
|---|---|
| 1 中性化 | 移除 `History`；不用 `Causal History` / `Event Set` 等替身 |
| 2 問題是否存活 | **不存活為 open question。**「哪些事件屬於 A 的 past」已被 §1 的公式**回答**：`{E \| ExternalExposure(E,A) ∧ E↝A_later}` |
| 3 是否有**獨立** carrier | **✔** `ExternalExposure` 與 `Event↝Agent` 兩個已 admission relations |
| 4 無名替身 | 不需要 |

> **它是 derived-projection 那一端的 canonical case：問題已被回答，不是被保留。**
> **這與 #20 R3 一致——projection 可有完全客觀的 denotation，同時仍是 modeling choice 才被命名。**

### 30.2 Lived Experience → QPF = 1

| Step | 結果 |
|---|---|
| 1 中性化 | 移除 `Lived Experience`；不用 `Subjective Experience` / `Felt Experience` |
| 2 問題是否存活 | **✔ 存活。**#9 §7 明確保留「causal incorporation ≠ subjective lived experience」，且該部分**沒有 identification power**——這是一個**未解問題**，不是已回答的 projection |
| 3 是否有**獨立** carrier | **✘ 沒有。**六個 constructs 都不涵蓋 subjective experience；`Awareness` 的 expansion rule（#8 §5）要求展開成**已定義的** Agent–X relation，而「主觀經歷」不是已定義 relation |
| 4 無名替身 | 不需要 |

> ### **§3.1 的 self-carrier 排除在此直接生效。**
> **#9 §7 描述這個問題，但 #9 就是 Lived Experience 的 placement decision 本身，
> 不是另一個獨立 tracked item ⇒ 不能作為自己的 carrier。**

### 30.3 🔴 與 Soul 的關係：兩個 PASS，但理由不同

$$\boxed{ QPF(Soul)=1 \neq \text{（理由）} \neq \text{（理由）} \neq QPF(LivedExperience)=1 }$$

| | 保留的問題 | 層級 |
|---|---|---|
| **Soul** | causal vocabulary 是否足以表達研究對象？ | **ontology / vocabulary 的表達 ceiling** |
| **Lived Experience** | causal incorporation 是否等於 subjective lived experience？ | **subject-level phenomenon 是否有可識別 discriminator** |

> **兩者不是同一個問題。它們是兩個獨立的 question-preserving vocabulary。**

### 30.4 連帶結果：Lived Experience 的 placement 改變

**Lived Experience 從 VOCABULARY 移入 QUESTION 層。**

**理由：#9 保留它的理由就是「該部分沒有 identification power」——
那正是一個 open question，而 QUESTION 層是 question-preserving vocabulary 的所在。**

> ### ⚠️ **但它的 governance parameter 尚未宣告。**
> **QPF(Lived Experience) = 1 使 QPF 成為它的 parameter 候選，
> 但依 invariant 2 仍需宣告 parameter + semantics + scope + change procedure。
> 在宣告之前，依 invariant 1，它尚不是 frozen rule。**
> **這與 Soul 的狀態相同（Soul 的 QPF 已宣告為 parameter），但宣告尚未完成。**

### 30.5 不受本次澄清影響的案例

**Agency 的 carrier 是 DCG #12 的 prospective-directedness open candidate，
那是與 Agency 不同的 tracked item ⇒ 不受 §3.1 影響。**

**Awareness 的 carrier 是已 admission 的 relations ⇒ 不受影響。**

**其餘六項（無 residual，或由其他 item 承載）⇒ 不受影響。**