# A3 工單 — Loss-Mechanism Taxonomy（READ-ONLY DIAGNOSTIC）

> **Ticket**：TA-2-V2A3（Owner 2026-10-04 05:54 授權）
> **Baseline**：A1 `50dd1f6` ／ A2 `f3d8846` ／ A2 spec `22cbc7d`
> **Mode**：READ-ONLY / OFFLINE DIAGNOSTIC。**不是** rule implementation。
> **Gate 2**：全程維持 **INCONCLUSIVE**，與本票結果無關。

---

## §0 術語紀律（Owner 2026-10-04 P3 裁定）

**自本票起，統一使用：**

- ✅ **prospective decision rule**
- ✅ **pre-specified stop rule**
- ❌ **不得**使用 "pre-registration" / "pre-registered"

理由：本 repo 的 66-observation corpus **已被看過**（A1 執行者與主大腦皆已檢視），因此任何「預登記」說法都不精確。歷史 commit 訊息不修改（那是紀錄），但**新文件一律用新術語**。

A2 spec `22cbc7d` §1 已載明「不是 clean pre-registration」，本條為其正式後續。

---

## §1 撤銷的前提（Owner 明文）

> **GO-B「precision = 1.0」前提正式撤銷。真值 = 5/7 = 0.714。**

A1 的 `_precision()` 為 `len(rows)/len(rows)` 恆等式（`harness/run_ta2v2a_feasibility.py`），恆回傳 1.0。
責任：主大腦未自行重算即轉述，Owner 將其寫入 A2 工單作為前提。**該前提不成立。**

本票**不得**引用「已知 pollution proxies precision = 1.0」。

---

## §2 目標

對 A2 已產生的 **19 筆 v1→A2 frame loss** 做逐筆機制分類，回答唯一問題：

> 這 19 筆 loss 中，有多少仍具有 Candidate A 所需的 **local event↔temporal attachment** 結構？

核心待答疑慮：

> **A2 只是 R8 太窄，還是 Candidate A 所謂的 local event-attachment 本身就沒有足夠 coverage？**

---

## §3 分類空間（**CLOSED，不可擴充**）

| 類 | 定義 | 必要條件 |
|---|---|---|
| **L1** Direct Local Attachment | event-side 活動動詞/述語 ↔ temporal noun，同 clause 且有明確局部關係 | 必須指出 event-side、temporal-side、精確 local relation、為何是 local |
| **L2** Local Noun-Phrase Attachment | 時間詞參與較大的 temporal/frame noun phrase（如 `午餐時間`），且該 phrase 與 event 的關係**仍可局部觀察** | **不得**只是名詞並置 |
| **L3** Local Clause Attachment | event predicate 與 temporal expression 同 clause，存在**可指出的具體** event↔temporal 關係，範圍可比 L1/L2 寬 | **僅同句共現不足** |
| **N1** Non-Local / Discourse-Dependent | attribution 需跨 clause／discourse／遠距離關係 | 須說明為何 local premise 無法涵蓋 |
| **N0** No Defensible Local Attachment | 現有 raw evidence 中無可合理指出的 local relation | 含純 lexical 共現、metaphor/simile、背景描述、需語意猜測 |

### 分類紀律

- **每筆 loss 恰好一個 primary class**
- ❌ 不得新增第六類、不得造子類、**不得在看到彙總數字後修改 taxonomy**
- 必須引用既有 raw text
- ❌ **不得**實作任何能救回該觀測的規則、不得提新 marker list、不得用 LLM judge
- ❌ **不得**把「出現時間詞」當成 temporal attribution

---

## §4 決策門檻（**pre-specified stop rule**）

```
LOCAL       = L1 + L2 + L3
OUTSIDE_LOCAL = N1 + N0        (兩者必須加總為 19)

OUTSIDE_LOCAL >= 12/19  →  GO E
OUTSIDE_LOCAL <= 11/19  →  Candidate A 保有「最後一次」implementation feasibility 機會
                            該次再失敗 → GO E
```

> **沒有 A4。** 門檻以**整數筆數**判讀，不以百分比。

---

## §5 主大腦新增：盲式第二標註者（read-only，零 experiment 成本）

**為什麼加**：A1／A2 的失敗都源於「儀器是壞的，但沒人獨立驗過儀器」。
本票的分類者若同時是唯一讀者，分類結果就是單一標註、**無標註者間信度**。
在它決定「要不要花 96 calls」之前，先用最低成本買到一個信度數字。

**做法**：

1. 主要標註者（worker）逐筆分類，產出 19 列分類表。
2. **第二標註者**在**看不到第一份分類**的條件下獨立重做一次分類（`verifier` 角色）。
3. 產出 **agreement 表**：19 筆中兩者分類相同者數量、不一致者**逐筆列出並保留分歧**。
4. **分歧不得被仲裁抹平。**兩種分類都留在紀錄中，並在最終報告中標明該筆為 `DISAGREED`。

**邊界**：

- 這**不是**新增 experiment LLM call。96-call 預算指的是觀察生成呼叫；分類所需的是閱讀判斷，本身不可避免。
- 成本：不產生新 observation、不改 corpus、不碰任何 frozen 檔案。
- 若一致率低（< 70%），**回報分歧並停止**，不得靠多數決或「取較嚴格者」決定 A 或 E。

---

## §6 硬性約束

- **0 實驗 LLM calls、0 network、0 new N、0 corpus mutation**、0 arm/probe/timestamp 修改
- **不得** replay corpus、**不得**修改 R1–R8、**不得**修改 A1/A2 實作
- frozen v1 唯讀（sha256 必須維持 `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`）
- 僅唯讀取用 evidence
- 0 kill、0 restart、`:8000` 不寫入
- **不得** stage Owner 的 `clients/voice_companion/**`（51 項）與 13 項未追蹤 `docs/`
- 本票原則上**只產出一份診斷 artifact**；不動任何實作

## §7 Out of Scope

A3 implementation rules／改 R7／改 R8／E implementation／Gate 2 重跑／N=12／新 LLM call／新 corpus／調門檻／新 taxonomy 類別／per-observation rescue／semantic judge／dependency parser／refactor

## §8 Stop Conditions（觸及即停並回報）

- 提出第六類
- 修改 R1–R8
- 執行 LLM call
- replay corpus
- 提出新 marker list
- 改動 12/19 決策邊界
- 進行 per-case rescue
- 把 lexical 出現當作 attachment
- 實作任何 A3 rescue 規則

---

## §9 Final Report 必含

19 列分類表（`obs_id | probe | arm | raw_text | v1_frame | A2_frame | class | event_side | temporal_side | relation_evidence | rationale`）／各類計數／LOCAL 與 OUTSIDE_LOCAL 算術／**標註者信度表與分歧列**／raw evidence 範例／**A2 機制侷限的明確發現**／門檻裁決（GO E 或 A 的最後一次機會）／production integrity／Git state／modified files／未解決問題

> **負面答案是明確允許的。** 若分類結果是 19 筆大半屬 N1/N0，如實報 → GO E。
> 不得為了讓 A 活下去而在分類時把模糊案例往 L 類推。
