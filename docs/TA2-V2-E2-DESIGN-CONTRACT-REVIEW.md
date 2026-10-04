# TA-2 v2-E2 — Design Contract Review（對抗式）

> **被審標的**：`docs/TA2-V2-E2-ENGINEERING-RESOLUTION.md`（`97db36f`）
> **審查模式**：adversarial design review。**不是** implementation、**不是** E-Pilot、**不是** threshold selection。
> **審查者**：主大腦（初審）＋ 獨立 verifier（重建攻擊）
> **日期**：2026-10-04 13:0x
>
> ## 🔴 VERDICT: **FAIL**
> BLOCKER 1–5 使本 resolution 無法支撐 E-Pilot。

---

## §0 審查者自身的一個錯誤（先記錄）

主大腦初審時**未先 `git fetch`** 即宣告 commit `97db36f` 不存在。
它一直在 `origin/main`，是本地落後一個 commit。

> 這與本專案反覆出現的病同型：**沒驗就宣告不存在。**
> 與 A1 的恆等式 precision、0.63 門檻的跨母體移植、`Test PASS ≠ architecture` 是同一個外觀。

---

## §1 ✅ resolution 做對的部分（明確 PASS，不需修改）

- **§1／§2 information boundary 的列舉本身正確且可執行** —— 把 expected direction 從 evaluator 輸入移除，
  確實解掉「給 target 就洩漏」那一半。
- **§2 line 57–59 對 leakage 的定義在概念上正確**：
  「measurement system 主動提供」vs「evaluator 自行推斷」是**可用的工程判準**。
- **§3 退役 response-vs-response primitive、§8 退役 CORRECT/MISMATCHED 為 evaluator-facing category**
  —— 這兩步是對的，**真正解決了 cross-model review 的上半段兩難**。
- **§7 把 IRRELEVANT 從「stimulus 混淆」重新釘為 Factor 2，方向正確**。
- **§12 E3 的 N accounting 敘述正確**（只是缺收斂規則）。
- **§10 的不宣稱清單、§13 的狀態表誠實且與事實相符**；review 未發現文件陳述與 HEAD 內容不符。

---

## §2 🔴 BLOCKER

### BLOCKER 1 — aggregation function 未宣告，DiD 不可從宣告的 observable 重建

**§4 / §7 / §9**

§4 宣告 per-trial observable = `{A, B, TIE, AMBIGUOUS}`，並規定「AMBIGUOUS 不得當成 0」。
§7 宣告 estimand = `ΔR = RELEVANT_ON − RELEVANT_OFF`，`DiD = ΔR − ΔI`。
**兩者之間的 aggregation function 從未被宣告。**

**獨立驗證（verifier 實測 6 個候選函數，同一組資料）**：

| 候選 | 規則 | DiD | 狀態 |
|---|---|---|---|
| f1 | AMBIGUOUS 剔除；A/B=1、TIE=0 | **+0.2857** | 有限值 |
| f2 | AMBIGUOUS 剔除；A/B=1、**TIE=0.5** | **+0.1429** | 有限值 |
| f4 | 分母含 AMBIGUOUS（當成 0） | +0.2500 | **違反 §4 約束** |
| f6 | TIE 也剔除（分母 = n_A+n_B） | — | **OFF 兩格 undefined** |
| f7 | A-slot 佔有率（order-sensitive） | — | **OFF 兩格 undefined** |
| f8 | signed share（order-sensitive + decode） | — | **OFF 兩格 undefined** |

> **三個與 §4 相容的函數給出三個不同的 DiD；三個 order-sensitive 變體在 OFF 臂 undefined。**

**且這不是「少寫一個公式」——是未履行一項已指派的交付：**
`TA2-V2-E-DECISIONS-E-D1-E-D2-AND-GOVERNANCE.md` §3 交付定義第 2 項逐字要求本票回答
「**evaluator output 如何聚合成 ΔR / ΔI / DiD？**」——**本票未交付這一項。**

**minimal correction**：**只宣告，不新增規則**——寫出分子／分母的可執行表定式、AMBIGUOUS 的處理、
TIE 的數值、四個 cell 各自的候選 A/B 身分、replication 收斂規則。

### BLOCKER 2 — §4「AMBIGUOUS 不當成 0」**強迫 post-treatment selection**

**§4 line 92**

剔除 AMBIGUOUS 使 R 成為**後處理變數條件下的比率**。實測分母依 condition 變動（**8 / 4 / 5 / 4**），
而 AMBIGUOUS 率依 condition 變動（OFF 側 33–50%、ON 側 12%）。
**ΔR 的偏差方向未知。**

> **這句約束本身是病，不是解。**

**minimal correction**：把 AMBIGUOUS 宣告為**獨立的報告量**（與 R 並列輸出、明確聲明不參與 ΔR 的算術），
而不是被 R 吸收。

### BLOCKER 3 — 唯一與 OFF symmetry 相容的 observable **不含方向**，卻被當作 primary outcome

**§4 + §7**

E Design §1（Owner 逐字）要求「**方向一致**」。非方向性 map 無法區分方向正反，
**ΔR 可被「抄 context 的字」滿足**。

§12 line 241「Construct 不再依賴 correct-direction answer key」在**字面上成立、實質上空轉**——
它移除了 answer key，**同時也移除了方向**，然後宣稱 construct 存活。

**更尖銳**：唯一的方向性證據（§5 source-recovery）**沒有 OFF 對應物**（OFF 無 source 可 decode）。
**方向只在 symmetry 已被破壞的那一臂可測；symmetric 的那一臂測不到方向。兩者不可兼得。**

**minimal correction**：❌ **不得由 engineering 單方面決定** → 見 §5 的 Owner 決策事件。

### BLOCKER 4 — 候選 A/B 在四個 cell 的身分從未宣告

ΔI 由 IRRELEVANT_ON/OFF 兩格算出（§7 line 145），但 resolution **從未說這兩格的候選 context 是什麼**。

`ΔR − ΔI` 要求四格同尺，**該前提未建立**。§9 line 151「同一 scoring scale」是**未檢驗的宣稱**。

**minimal correction**：逐 cell 宣告 candidate A/B 的來源（stimulus metadata），並宣告四格 A/B 是否同型。

### BLOCKER 5 — §9 line 202 違反 repo 已成立的一等驗收條款

`TA2-V2A3-MEASUREMENT-WORK-ORDER.md` §6（Owner 2026-10-04 裁定）已把
「aggregation metric / denominator / population definition 的**實作與宣告一致性**」
列為 TA-2 measurement work 的**一等驗收條件**，並記錄先例：
A1 的 `_precision()` docstring 寫著正確公式、實作卻是恆等式，被稱為
「**本 repo 至今最貴的一次量測缺陷**」。

**resolution §9 line 202「每一層現在都能產生下一層需要的輸入」正是那條驗收條件專門要攔的、
尚未被檢驗的宣稱。**

**minimal correction**：把 §9 鏈條每一層標成 **DECLARED / UNDECLARED** 兩態；
未宣告者不得出現在「現在都能產生下一層需要的輸入」這句話裡。

---

## §3 🟠 HIGH CONCERN

| # | Section | 問題 | Minimal correction |
|---|---|---|---|
| 6 | §11 / §10 | **無 evaluator 可靠性檢查（人類一致度）**。§11 只列 pilot target。A-v2 死於 0/6；新 primitive 換了語意工作但同樣未測 | 把「雙人獨立標註 + 一致率」列為 E-Pilot 的**前置條件**（非 pilot 產出） |
| 7 | §53 | **與 E Design §1／§17 E0 直接衝突**。line 53 宣告 temporal evidence 是「被測量的 observable」，而 E Design §1 明文寫「**不是**：Soul 能不能說出『晚上』」、E0 要求「**不依賴** lexical temporal mention」 | 明文聲明本設計不依賴 lexical temporal mention，或撤回 §53 |
| 8 | §4 line 89 | **TIE 語意未宣告**（不是宣告錯的那個——是根本沒宣告）。導致「用了 context」與「忽略 context」不可分辨：兩者會編碼相同，對比會飽和 | 逐字宣告 TIE 的單一語意 |
| 9 | §12 line 237 | **order-randomization 在非方向性 observable 下是 vacuous**。`1[direction ∈ {A,B}]` 對 A/B 順序完全不變。要嘛空轉，要嘛意圖是 order-sensitive——而那正是 OFF undefined 的變體。**這是 resolution 的內部矛盾。** balance 要求與 seed 保管皆未宣告 | 宣告 balance 規則與 seed 保管；明示該 precondition 作用於哪個 observable |
| 10 | §6 | **OFF 臂無 matched-prompt 對照宣告**。E Design §2 要求 Same Prompt Path / Same Non-temporal Context，resolution 從未為 OFF 臂重申 ⇒ OFF 與 ON 的差異可能**不只**是 temporal context 的有無 | 引用並套用 E Design §2 的 same-prompt-path 要求於 OFF 臂 |
| 11 | §11 line 227 | **「source reconstruction 取代 semantic judgment」不可證偽**。「取代」沒有任何操作化測試。根因是：observable 只有**一個 4 值 forced-choice 輸出**，「這是哪個 context」與「哪個 context 更好解釋 R」**不是兩條通道，是同一個決策**。所以這是**結構性限制，不是經驗問題** | 把該項從 pilot target 改標為 observable 的結構性限制；或在 observable 預留與 manipulation 正交的通道（**此為設計決策，非工程可代決**） |

---

## §4 🟡 MEDIUM CONCERN

- **12**：§12 的 replication 收斂規則仍缺席（多個 generation replication 如何收斂成 condition-level 量值未定義）
- **13**：OFF cell **沒有宣告的 null**。OFF 值只能被差分，無法獨立驗證為有效對照
- **14**：OFF 候選 pair 為 counterfactual 的**可比性未論證**——resolution 宣告 OFF 與 ON 是「完全相同的 protocol」，
  卻未論證 counterfactual pair 與 administered pair 在測量上可比

---

## §5 🔴 Construct circularity 檢查（Bry 指定）—— **未避免**

**精確 interface 落在 §3 → §4/§7 那一步：**

```
(i)   manipulation = 注入 candidate context A 或 B
(ii)  唯一觀測 = 「A/B 哪個更能解釋 R」
(iii) 在 OFF symmetry 約束下唯一可用的 aggregation
      把 (ii) collapse 成「evaluator 有沒有選 A 或 B」
```

> **measurement 的兩個選項，就是 manipulation 的兩個物件。**
> 因此只要 manipulation 留下**任何**痕跡（**包括純字面痕跡**），measurement 必然 register 它。
> 這條鏈上**不存在任何不是 manipulation 詞彙內容之函數的觀測通道**。

E 設計原本的逃生門是那個 **contrast**（「哪一個 context」），**§4 的 aggregation 把它丟掉了**。

> **Bry 指定的檢查失敗。**

---

## §6 OFF symmetry 逐項

| # | 項目 | 判定 |
|---|---|---|
| 1 | 相同 evaluator-visible information class | **形式 PASS / 實質未測**。ON trial 的 {A,B} 含真 source（1-of-2），OFF trial 的 {A,B} 不含（0-of-2）——evaluator 原則上能分辨「可追溯」與「泛用」response。**resolution 從未說 DiD 在吸收哪一個不對稱。** |
| 2 | 相同 scoring protocol | **PASS**（文字上一致） |
| 3 | 相同 output schema | **PASS** |
| 4 | OFF 不需 source label 即可產生 per-trial observable | **條件式 PASS，且循環**。只在非方向性 aggregation 下成立；任何方向性 aggregation 對 OFF 回 None。**用一個未宣告的 aggregation 去證 OFF symmetry，邏輯上循環。** |
| 5 | OFF 在 aggregation 不被用不同 metric | **條件式 PASS**。因函數未宣告而不可檢驗 |

---

## §7 需要 Owner 決策（**唯一一項**）

B1／B3／B5／§5 共同指向同一個問題，而它**不是 engineering 問題**：

> E Design §1 的 construct（Owner 逐字）要求「**方向一致**」的改變。
> resolution 唯一能與 OFF symmetry 共存的 observable **不承載方向**。
> 要嘛 construct 被改寫成一個量值命題（那會改變 TA-2 要證明的東西），
> 要嘛 estimand 必須是方向性的（那 OFF 臂需要一個 resolution 未提供的方向性錨點）。
> **兩條路都無法由 Engineering Brain 單方面選擇。**

> ### 決策事件 E-01
>
> **我們希望 Soul 的時間感知驗證，證明的是「有方向且方向一致的解釋改變」，
> 還是「有可觀測的解釋量變化」？**
>
> - **A** — 是，這就是我們要證明的（方向一致性是核心條件）
> - **B** — 不完全是，我希望證明的是另一種現象
> - **C** — 我無法從這個描述判斷

**主大腦不回答此問題。**

---

## §8 狀態

| 項目 | 狀態 |
|---|---|
| TA-2 Gate 2 | **INCONCLUSIVE** |
| A-v2 local-attachment measurement | **STOPPED** |
| E Design（`b9ae005`） | **BLOCKED at E2** |
| E2 Engineering Resolution（`97db36f`） | **FAIL — 5 BLOCKER，0 可直接進入 E-Pilot** |
| E-Pilot | **NOT AUTHORIZED** |
| Threshold | **NOT DEFINED** |

**本 review 未提出任何 threshold 數字、未新增 scoring system、未新增 Rule 6+、未做 E-Pilot implementation。**
