# 工單：TA-2-V2A3-MEASUREMENT — Taxonomy Lock & Stage-3 Revalidation

> **類型**：taxonomy / reliability correction。**不是** A4，**不是** rule implementation，**不是** E。
> **授權**：Owner 2026-10-04 10:58
> **Gate 2**：全程維持 **INCONCLUSIVE**。
> **成本**：0 實驗 LLM calls、0 network、0 replay、0 corpus mutation。

---

## §0 本輪狀態（Owner 2026-10-04 10:55 裁定）

> **TA-2 v2-A3 = MEASUREMENT STOP / TAXONOMY NOT YET RELIABLE**
> **不是** GO E、**不是** A survived、**不是** A failed、**不是** 96 calls。

A3 的 GO E 建議已否決：其 `OUTSIDE_LOCAL ≥ 12/19` 建立在**被污染的母體**上，
且 19 這個分母混淆了「A 救不到正確答案」與「本來就不該救（v1 假陽性）」。

**保留的 evidence**：修正污染母體後，Candidate A 的方向**確實不像原 A3 所暗示的那麼悲觀**；
但 Stage 3 的 taxonomy agreement（partition 65.2%）不足以把這個方向變成工程結論。

**真正的 bottleneck**：不是 corpus 不夠，而是「什麼叫 local attachment」的
operational definition 還不夠硬。**本票就是去修那個定義。**

---

## §1 🔴 已作廢：門檻 0.63

> `0.63` = `12/19` 是**舊（被污染）母體的統計結果**，**不是 Candidate A 的構念門檻**。
> **作廢，不恢復。不得**因為比例相同就把它移植到新母體。
> **不得**自創 0.50／0.55／0.60 取代——那會重演「看到結果 → 找一個能裁決的門檻」。

## §2 Gate 2 改為 **validity gate**（不再是 verdict）

```
partition agreement < 70%  ->  Measurement INVALID -> STOP
                              （不是「A 或 E」）
partition agreement >= 70% ->  只有此時才有資格談 A/E threshold
```

> **一致率不是 A/E verdict，它是「裁決資料是否有資格成為 A/E evidence」的 validity gate。**
> 這兩者完全不同。分開寫，是為了避免「為了讓結果能裁決而調整 taxonomy」的反向壓力。

**用 partition（LOCAL vs OUTSIDE），不用五類 raw。**
真正的 A/E 問題只有「這筆 loss 是否需要 local attachment 以外的機制」。
L1/L2/L3 是 diagnostic subtype，**不應讓 subtype 分歧把 A/E reliability 一起拖垮**。

---

## §3 Locked taxonomy（Owner 已裁定，**本輪不得再解釋或放寬**）

完整規則見 **`docs/TA2-V2A3-ANNOTATOR-BRIEF.md`**。摘要：

| 規則 | 內容 |
|---|---|
| **Clause 定義** | 語法小句，由**完整述語**界定。兩個獨立述語 ⇒ 兩個 clause（即使同句逗號相連） |
| **R1** 連動述語 | `去吃飯，算宵夜` 是兩個 clause ⇒ **N1** |
| **R2** frame-noun 句 | `差不多是午餐時間了` 述及時刻、event 在另一 clause ⇒ **N1** |
| **R3** greeting-only | **條件**：`temporal-side triggers ⊆ {午安}` ⇒ **N1**。同時有 `補眠` 等 event-side activity expression 時，**R3 不得適用** |

### R3 護欄是對「適用域」的正式定義，**不是**對特定 obs_id 的例外

Bry 逐字：

> 判準必須描述語義條件，不能把某個 token 直接變成否決開關。
> `午安` 本身不構成對另一 event 的 local temporal attachment。
> 但 `補眠` 是 event-side activity expression，必須讓原本的 local-attachment 分析繼續處理。

> 這不是為了救兩筆資料，而是避免 **temporal trigger presence → categorical veto** 的錯誤。

---

## §3b 🔴 本輪**無法避免**的洩漏：R5 的例句取自語料

R5 的示意例句（`正好補眠`、`熬到現在，該補眠了`、`去吃飯，算宵夜`）**就是上一輪兩位標註者
分歧的那兩筆的文本**。若不給例句，R5 無法被 operationalize；若給了例句，
那兩筆就不再是獨立測量。

**處置（pre-declared，不得事後調整）**：

1. 例句**保留在 brief 中**（否則規則不可操作化）。
2. **凡與 R5 例句結構相符的觀測，一律標記為 `rule_constrained = R5`，
   不得計入「genuine independent judgment」。**
3. 報告中**必須**分開呈現：
   - **implementation consistency**（規則強制一致的部分）
   - **independent annotation reliability**（真正需要標註者自己判斷的部分）
4. **不得**把整體 agreement 數字直接當成 reliability 報出。

> 上一輪的 90.9% **不撤銷**——它確實符合事前寫死的 Gate 2。
> 但 closeout 必須明寫：**90.9% ＝ operational validity gate pass，
> 不等於 independent semantic reliability = 90.9%。**
> 當時真正未受規則決定的獨立邊界是 **2/2 不一致**。

---

## §3c 證據優先序（Owner 裁定）

> **ROW-LEVEL ANNOTATION DATA WINS.**

summary count 與 row-level 資料衝突時，**以逐列資料為準**，全部機械重算。
**不得**使用與逐列資料不符的標註者自報彙總。

> 上一輪第一位標註者自報 `23 筆 / N1=17`，但其**自己的表格只有 22 列、N1=16**。
> 本輪所有合計一律從逐列記錄機械重算。

---

## §3d 這是最後一次 taxonomy lock

> 若本輪仍出現新的實質語義邊界，**不得再加规則**。
> 應承認目前 taxonomy **不足以支撐 A/E 決策**，重新評估 measurement design，
> 而不是繼續「修到 agreement 過關」。

**理由**：一旦進入「不一致 → 加規則 → 再不一致 → 再加規則」的循環，
最後得到的是**為這批語料特製的 classifier**。那種東西的 agreement 數字會很漂亮，
但**沒有外部效度**。

---

## §4 關鍵驗收命題（Owner 逐字）

> 規則 operationalization 成功 **≠** 標註可靠性被證明。
>
> 若兩個 annotator 都被**同一條 deterministic rule** 強制產生相同 partition，100% 只能證明
> **implementation consistency = 100%**，**不能**證明 **independent annotation reliability = 100%**。
>
> 因此本輪的真正驗收是：**blind 分類者在規則明確後，仍能否獨立得到相同 partition。**

**這就是本輪的意義所在**——重點不是「規則鎖死後一致率變 100%」（那是必然的），
而是**規則的適用域判斷**（例如「`午安` 是否為唯一時間側」、「這是連動述語嗎」）
本身是否可靠。

**不接受**把 deterministic rule 造成的 100% 當作 reliability 證據。

---

## §5 🔴 盲式修正（本票必須解決的設計缺陷）

上一輪的兩處洩漏**由主大腦造成**：

1. 工單用「19／17／11」解釋問題，**同時**要求標註者不知道這三個數字
2. 叫標註者讀 A3 artifact 的 §0，而 §0 含 `L09=N1`、`L18/L19 = v1 false positives`

**本票的結構性修正**：

| 角色 | 可讀檔案 |
|---|---|
| **標註者** | **只有** `docs/TA2-V2A3-ANNOTATOR-BRIEF.md`。該檔**不含任何歷史結果、計數、門檻或結論** |
| **主大腦 / runner** | 本檔 + 全部歷史 |

**禁止事項**：標註者不得被要求讀任何含前輪分類結果或衍生數字的檔案。
「只讀某節」**不算**隔離（工具可能回傳節後內容）。**必須物理分離成不同檔案。**

標註者**主動揭露**任何非預期看到的分類結果或數字，並把自己在受影響項目上的判定
標記為 **不獨立**。**主大腦在算一致率時必須逐項標示哪些判定不獨立。**

---

## §6 交付物

| 檔案 | 動作 |
|---|---|
| `harness/ta2v2a3_taxonomy_lock.py` | 新檔：locked rules 的可執行定義（純函式、arm-blind）＋ Stage 1／Stage 3 判準 |
| `harness/run_ta2v2a3_revalidation.py` | 新檔：機械集合運算、agreement 計算、分子分母 manifest、Gate 判定 |
| `tests/test_ta2v2a3_revalidation.py` | 新檔：§7 驗收 ＋ mutation |
| `data/harness_out/tl12/ta2v2a3_revalidation.json` | 新檔（derived，`data/` 被 gitignore，與 A1／A2 一致） |
| `docs/TA2-V2A3-REVALIDATION-RESULT.md` | 新檔：Stage 1／Stage 3 逐筆表、一致率、Gate 判定 |

**不得**修改 A1／A2 實作、frozen v1、`src/**`、既有 A3 artifact、上一輪的 work order。

---

## §7 §6 一致性驗收（Owner 提升為一等項，延續）

- 每一個計數函式 **docstring 宣告的公式必須與實作逐字一致**（測試用**自己的 parser** 抽出分子／分母表定式並 `eval` 重算，再比對）
- 每個 denominator 在輸出中**明確印出實際分子／分母定義與數值**
- 提供**獨立重算路徑**（與主路徑不同程式碼）
- **mutation 必須真的紅**：
  - **M-denominator**：故意改分母
  - **M-partition**：故意改 LOCAL/OUTSIDE 切分

> 依據：A1 的 `_precision()` docstring 寫著正確公式、實作是 `len(rows)/len(rows)` 恆等式，
> 讓 `precision = 1.0` 被當成事實寫進下一張工單當前提。

---

## §8 硬性約束

- **0 實驗 LLM calls、0 network、0 new N、0 corpus mutation**、0 arm/probe/timestamp 修改
- **不得 replay corpus**、**不得**實作任何 A 規則、**不得**新增 marker list
- frozen v1 唯讀（sha256 必須維持 `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`）
- A1／A2 輸出**保留不覆蓋**
- 0 kill、0 restart、`:8000` 不寫入
- **不得** stage `clients/voice_companion/**`（51 項）與 13 項未追蹤 `docs/`
- **不得**恢復或引用 `0.63`

## §9 Out of Scope

A 規則實作／A4／E implementation／Gate 2 重跑／N=12／新 LLM call／新 corpus／
**自創百分比門檻**／新 taxonomy 類別／per-observation rescue／semantic judge／
dependency parser／修改 locked rules／回頭修改任何前輪的母體或門檻

## §10 Stop Conditions

提出第六類／修改 locked rule／恢復 `0.63` 或自創門檻／執行 LLM call／replay corpus／
提出新 marker list／per-observation rescue／把 deterministic 一致率當 reliability 證據／
讓標註者讀含前輪結論的檔案

## §11 誠實邊界

- **本票不是乾淨的 pre-registration**：語料已被看過。門檻與規則是 **prospective decision rule**，不是盲式預註冊。
- **規則鎖死後一致率必然上升**，那是 implementation consistency，**不是** reliability。
- **一致率達標只代表「有資格討論 A/E 門檻」，不代表 A 或 E 已被判定。**
- **本票不決定 A vs E。**它只決定量測是否有效。
- **Gate 2 全程維持 INCONCLUSIVE。**
