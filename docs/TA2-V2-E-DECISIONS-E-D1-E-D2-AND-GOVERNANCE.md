# TA-2 v2-E — 架構裁定 E-D1 / E-D2 與治理界線

> **裁定者**：Owner（Bryan）2026-10-04 12:43
> **前置**：`docs/TA2-V2-E-CROSS-MODEL-REVIEW-RESULT.md`（E2 = BLOCK，兩 reviewer 一致）
> **本檔為 canonical 決策記錄。**

---

## §1 Decision E-D1 — Causal estimand

> **E 的 causal estimand 保留 DiD / causal contrast。**
> **Pairwise 不得成為 E 的替代 estimand**，最多只能作為 **evaluator-level scoring primitive**。

### 這解掉了 §3.2 的設計矛盾

原設計中 §4（四條件 + DiD）與 §7／§8（純 pairwise）不相容——OFF 未進入 measurement path，
`ΔI = ON − OFF` 無法從 pairwise preferences 算出。

**裁決後的量測架構（canonical）**：

```
Generation
    ↓
Response pair / response set
    ↓
Evaluator scoring            ← pairwise 只在這一層
    ↓
Per-trial measurement
    ↓
Condition-level aggregation
    ↓
ΔR / ΔI / DiD                ← causal estimand 只在這一層
```

> **Pairwise 不是 causal estimand。** 它只能回答：
> 「在這一個 evaluation trial 裡，A/B 哪一個比較符合預先定義的判斷標準？」
> aggregation 才形成 relevant contrast / baseline-context effect / DiD。

---

## §2 Decision E-D2 — Evaluator information boundary **暫不決定**

> **目前不得決定 evaluator 應該看到多少 expected temporal direction。**
> 因為這正是 E2 BLOCK 暴露出的核心未解 design question。

**這是 engineering problem，不是 Owner decision。** 由 Engineering Brain 收斂。

**先前拋給 Owner 的「要不要告訴 evaluator expected direction」問題已撤回。**

---

## §3 狀態

| 項目 | 狀態 |
|---|---|
| TA-2 Gate 2 | **INCONCLUSIVE** |
| A-v2 local-attachment measurement | **STOPPED** |
| E Design | **BLOCKED / REVISION REQUIRED（E2）** |
| E-Pilot | **NOT AUTHORIZED** |
| Threshold | **NOT DEFINED** |

**下一張票是 `E-DESIGN-REVISION / E2 RESOLUTION`，不是 E-Pilot。**

該票只處理兩件事：
1. **Evaluator information boundary** — evaluator 究竟可以看到什麼？哪些資訊構成 leakage？如何讓「direction」有定義但不提供 answer key？
2. **Measurement architecture reconciliation** — DiD = causal estimand；pairwise = scoring primitive；OFF 如何進入 measurement path；evaluator output 如何聚合成 ΔR / ΔI / DiD？

### 該票的禁止事項

- ❌ 不改 threshold
- ❌ 不跑 LLM
- ❌ 不寫 runtime
- ❌ 不做 E-Pilot
- ❌ 不引入新的 scoring rule
- ❌ 不重新開始 A-v2
- ❌ **不新增 Rule 6+**

---

## §4 🔴 治理界線（正式採納）

> ### Engineering ambiguity is Engineering Brain's responsibility.
> ### Product / Soul behavior intent is Owner's decision.

### 角色分工

| 角色 | 職責 | 不該做什麼 |
|---|---|---|
| **Bry** | Owner / Architect / Final Authority —— **決定「我們要證明什麼現象」** | 不該被要求做 measurement engineering 決策 |
| **Engineering Brain** | **決定「怎麼嚴格證明這個現象」** —— construct / measurement / blinding / scoring / aggregation / DiD / noise / control / threshold readiness | 不該把工程問題上拋 Owner |
| **Implementation Agent** | **把已決定的方法實作出來** | 不該自行發明設計 |

### 決策事件格式（Owner 只需要回答事件結果）

當需要 Owner 決策時，**只提交事件結果**，不提交技術選項：

> **我們希望 Soul 的時間感知驗證，證明哪一件事情？**

選項形態：

```
A — 是，這就是我們要證明的
B — 不完全是，我希望證明的是另一種現象
C — 我無法從這個描述判斷
```

**不得**問「要用 X measurement architecture 哪一種？」——那是 engineering question。

### 判別規則

遇到問題時先自問：

1. 這是「**Bry 想要什麼**」的問題，還是「**怎麼做到**」的問題？
2. 是「怎麼做到」→ **自己決定**
3. 是「Bry 想要什麼」→ **才交給 Owner**
4. 兩者混在一起 → **由 Engineering Brain 拆開**，只把真正需要 Owner 意志的那一部分轉成決策事件

### 本輪的具體應用

以下問題**已從 Owner 決策清單移除**，因為它們是 engineering questions：

- R1／R2／R3／R5 的 rule scope 爭議（TA-2 v2-A 期間）
- R5 predicatehood 邊界
- evaluator 應看到多少 expected temporal direction（E2）

---

## §5 新增獨立檢查項：Cross-section Measurement Consistency

> **canonical design 本身也需要 consistency audit。**

**不得**只驗證「每一節自己看起來合理」，**必須驗證跨節的量測鏈**：

```
Construct
   ↓
Scoring object
   ↓
Per-trial observable
   ↓
Aggregation
   ↓
Estimand
   ↓
Threshold
```

**每一層都必須能產生下一層需要的東西。**

> 這正是 `ΔI ↔ pairwise` 斷裂被抓出來的地方。
> 該缺陷由主大腦在落檔時自查發現——canonical design 未被交叉檢查，
> 是與前六輪同型的病（契約宣告一件事，另一節測另一件事，無人交叉檢查）。

---

## §6 已採用的既有原則

- `New finding ≠ new authorization`
- `MiniMax report ≠ fact`
- `Test PASS ≠ architecture correctness`
- `ROW-LEVEL DATA WINS`（原始證據 > agent summary）
- 一致率 gate 必須**分層計算**（overall + independent-subset）
- 任何跨母體移植的門檻必須**重新推導**
