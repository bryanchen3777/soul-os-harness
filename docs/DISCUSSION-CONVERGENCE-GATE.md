# Discussion Convergence Gate（DCG）— Soul OS 研究討論治理規則

> **正式採納**：Owner（Bryan）2026-10-04 15:36
> **Notion 同步**：《Soul OS 專案狀態（單一事實來源）》＋《Soul OS 路線圖與里程碑》
> **本檔為 repo 內的工程治理副本** —— 因為這條規則**直接約束 Agent 行為**，只放 Notion 時新 session 讀不到。
> **適用範圍**：所有 Soul OS research discussion。

---

## §1 輪數上限

> **預設最多 5 rounds。可以提前收斂。第 5 輪必須產生結論。**

- 提前收斂 **優於** 用滿 5 輪
- 到第 5 輪仍未收斂 ⇒ **強制產出結論**，未解決部分列入 Open Questions
- **不得**因為「還能問下一個好問題」而延長

---

## §2 🔴 核心禁令

> ## **Open Question ≠ invitation to continue the same discussion.**

**未解決的問題是交付物的一部分，不是繼續的許可。**

一個 Open Question 被記錄下來，這輪就已經**有效地結束了**。
下一輪若要重開同一個討論，必須有**新的輸入**（新 evidence、新架構裁定、新授權），
**不能只因為問題還沒答案。**

---

## §3 每次收斂必須留下的四項

```
1. Conclusions          已確定的事
2. Open Questions       未確定的事（記錄即交付，不構成延長許可）
3. Decision / Next Step 下一個動作，或明確的「不動」
4. Non-Claims           明確拒絕聲明的命題
```

> **四項缺一不算收斂。**
> **Non-Claims 尤其不可省略** —— 它是防止下一輪把限制推回成主張的那一道閘。

---

## §4 邊界：DCG **不**授權任何施工

> **DCG 只限制研究討論的壽命，不構成任何授權。**

以下**一律不**因 DCG 而發生：

- ❌ implementation
- ❌ pilot
- ❌ runtime change
- ❌ architecture change
- ❌ issue / ticket
- ❌ threshold 選擇

**這些仍走原本的 Owner / Gate 流程。**

> **Discussion ≠ Authorization.**
> 收斂得再乾淨，也不構成施工許可。

---

## §5 這條規則的來源（誠實記錄）

**CA-2 construct 辯論遠遠超過 5 輪。**
本規則是從那個失控裡長出來的，不是外部移植的最佳實踐。

其中被 Bry 當場抓出、而我自己沒有抓到的錯誤包括：

| # | 我寫下的 | 實際問題 |
|---|---|---|
| 1 | 「Soul 的行為在架構相關處是 representation-mediated 的」 | `∀m∈M\*, P(m)` 滑成 `P(T)` |
| 2 | 五個「mechanism dimensions」 | 混了三種本體：機制／觀察／架構 |
| 3 | L1 攻擊 alternative | L1 其實是 hypothesis-side falsification |
| 4 | 「沒有第二種纏繞」 | 缺 **under current mechanism ontology** 限定 |
| 5 | `0.63` 移植 | 門檻不能跨母體 |

> **這些不是靠自查發現的。每一個都是靠追問被逼出來的。**
> DCG 的作用不是讓對話變短，是**逼出一個必須產出結論的邊界**，
> 讓「繼續問好問題」不能無限延長。

---

## §6 與既有治理規則的關係

| 規則 | 管什麼 |
|---|---|
| **DCG**（本檔） | 研究討論**多久**必須收斂 |
| Owner / Engineering Brain 分界 | **誰**決定什麼 |
| Construct / Ground truth freeze | **什麼**已被定案，不得再漂移 |
| 「New finding ≠ new authorization」 | 新發現不構成新授權 |
| **Ontology Privilege Policy**（`docs/ONTOLOGY-PRIVILEGE-POLICY.md`） | 被採用的 ontology-selection 規則**必須宣告什麼**（參數、語義、scope、變更程序） |

**五者互不取代。** 討論可以在 5 輪內收斂而仍然不授權任何施工；
也可以在第 5 輪產出結論，而結論本身是「不動」。

**兩者分工：**本檔規定研究討論**多久**必須收斂；
`ONTOLOGY-PRIVILEGE-POLICY.md` 規定被採用的選擇規則**必須自我宣告到什麼程度**。
**後者不取代前者——一個可以在五輪內收斂的討論，其結論仍必須遵守後者的宣告要求。**
