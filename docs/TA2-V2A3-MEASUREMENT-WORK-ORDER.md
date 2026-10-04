# 工單：TA-2-V2A3-MEASUREMENT — Headroom Population Definition

> **類型**：**measurement contract correction**。**不是** A4，**不是** rule implementation，**不是** E。
> **授權依據**：Owner 2026-10-04 09:25 裁定（`docs/TA2-V2A3-LOSS-MECHANISM-TAXONOMY.md` §0）
> **Gate 2**：全程維持 **INCONCLUSIVE**，與本票結果無關。
> **成本**：0 實驗 LLM calls、0 network、0 replay、0 corpus mutation。

---

## §1 要修的東西

A3 的 19-row denominator **混淆了兩件事**：

> 「A 救不到正確答案」 vs 「本來就不該救（v1 假陽性）」

`L18`／`L19` 是 `irrelevant` 對照組的比喻污染（`「剛入夜的那種深藍」`／`「夜色剛落下…那種藍」`）。
v1 誤判為 `night`；A2 **正確地沒有 rescue**。那不是 headroom 損失。

計入 `OUTSIDE_LOCAL` 會**灌水、偏袒 GO E**。兩位標註者剔除後都落到 11。

---

## §2 🔴 母體必須是「判準」，不是「清單」

**這是本票唯一真正重要的設計決定。**

**絕對不可以**由主大腦事後挑出 17 個 obs_id 組成新母體。那只是換個形式的 post-hoc denominator change。

**正確做法**：把母體定義成一個**可被任何人獨立套用的判準**，並且**套用在完整的 v1-framed 母集合（25 筆）上**——
讓「v1 假陽性」自然被**排除**，而不是被**刪掉**。

### §2.1 Stage 1 — v1 Defensibility Judgment（對全部 25 筆 v1-framed）

對每一筆 v1 判定為非 `unframed` 的 observation，只看 **raw text ＋ v1 frame**，回答一個是非題：

> **v1 給這筆的 frame，在證據上是否站得住？** `DEFENSIBLE` / `NOT_DEFENSIBLE`

**判為 `NOT_DEFENSIBLE` 的情形（封閉清單）**：

- metaphor / simile（時段詞用於比喻或顏色描述）
- temporal wording 只是在描述背景，與目標事件無關
- 純 lexical co-occurrence，無任何可指出的 event↔temporal 關係
- temporal token 無法歸屬到目標 event
- 需要 unsupported semantic guessing 才成立

**其他一律 `DEFENSIBLE`。**

**關鍵盲化要求**：

- 標註者**不得**看到 A1／A2／A3 的分類結果、per-token 規則命中、或 A2 的 rescue 紀錄
- 標註者**不得**知道哪一筆是 A2 的 survivor（survivor set 由 runner **機械計算**，不由標註者判斷）
- 標註者**不得**知道本工單的 19／17 兩個數字

### §2.2 Stage 2 — Headroom Derivation（機械計算，**非判斷**）

```
v1_framed      = { obs : v1_frame != "unframed" }                     # 預期 25 筆
a2_survivors   = { obs ∈ v1_framed : a2_frame != "unframed" }
defensible     = { obs : 標註者判 DEFENSIBLE }                        # Stage 1 產出
HEADROOM       = defensible \ a2_survivors
```

**`HEADROOM` 是推導出來的，不是挑出來的。** runner 必須同時輸出 `v1_framed`、`a2_survivors`、`defensible`、`HEADROOM` 四個集合與計數，供交叉驗算。

### §2.3 Stage 3 — Headroom Taxonomy（沿用 A3 的封閉五類）

對 `HEADROOM` 每筆套用 A3 的分類空間，**一類**：

| 類 | 定義 |
|---|---|
| **L1** | event-side 活動動詞／述語 ↔ temporal noun，同 clause，直接局部關係 |
| **L2** | 時間詞參與 local temporal/frame noun phrase（如 `午餐時間`），且該 phrase 與 event 的關係**仍可局部觀察**（不得只是名詞並置） |
| **L3** | event predicate 與 temporal expression 同 clause，有**可指出的具體** event↔temporal 關係，範圍可比 L1/L2 寬（**僅同句共現不足**） |
| **N1** | attribution 需跨 clause／discourse／遠距離關係 |
| **N0** | 無可辯護的 local relation |

**分類空間 CLOSED。** 不得新增第六類、不得造子類、不得在看到彙總數字後修改。

---

## §3 clause 定義（Owner 已裁定，**本票起固定**）

> **clause = 語法小句**，由**完整述語**界定。
> 「夜深了」與「該睡了」是**兩個完整述語** ⇒ 兩個 clause。
> **不採** A1 frozen v1 的逗號切分，**不採**「句界才算 clause」。

本票所有 clause 判斷一律依此。**不得**在分類途中改採其他慣例。

---

## §4 盲式雙標註（工單 §5 沿用）

兩位標註者**各自獨立**完成 Stage 1 與 Stage 3，彼此**不可見**。

- 第二次標註的輸出**在盲式期間不落檔**
- 分歧**逐筆保留、不仲裁、不多數決、不取較嚴格者**，標 `DISAGREED`
- 一致率**不得**用來決定 A 或 E，只用來決定「可否進入 stop rule」

**一致率分兩個口徑，都要報**：

| 口徑 | 對象 |
|---|---|
| **raw agreement** | Stage 1 的 `DEFENSIBLE`／`NOT_DEFENSIBLE` 判定，25 筆 |
| **partition agreement** | Stage 3 的 `LOCAL` vs `OUTSIDE_LOCAL` 分區，headroom 筆數 |
| taxonomy agreement | Stage 3 的五類全等，headroom 筆數 |

---

## §5 決策門檻（**prospective decision rule**，結果出來前鎖死）

依序執行，**任一關不通即 STOP，且不得進入下一關**：

### Gate 0 — 一致率閘

```
Stage 1 raw agreement < 70%  →  STOP / INCONCLUSIVE
```

（Owner 2026-10-04：raw agreement < 70% → A 與 E **都不判**。）

### Gate 1 — 最小樣本數護欄

```
n = |HEADROOM|
n < 8  →  STOP / INCONCLUSIVE（樣本過小，百分比門檻無意義）
```

> **這條是 A2 教訓的直接應用**：當時只有 2 筆 rescue，40% 門檻卻觸發了 GO-C FAIL。
> **n=2 的百分比門檻是雜訊。** 本票不得重演。

### Gate 2 — A-vs-E 裁決（僅在 Gate 0、1 都通過時）

```
OUTSIDE_LOCAL / n >= 0.63  →  GO E
OUTSIDE_LOCAL / n <  0.63  →  Candidate A 保有「最後一次」implementation feasibility
                              該次再失敗 → GO E
```

> `0.63` 對應 Owner 原 stop rule 的 `12/19 = 63.16%`，**比例不變，只換母體**。
> 以整數筆數判讀。`n = 17` 時，整數切點為 **`OUTSIDE_LOCAL ≥ 11`**。

**沒有 A4。** 本票**不得**實作任何 A 規則、**不得**提出新 marker list。

---

## §6 🔴 新增為一等驗收項（Owner 2026-10-04 裁定）

自本票起，以下項目**一律**列為 TA-2 measurement work 的一等驗收條件：

> **aggregation metric / denominator / population definition 的「實作」與「宣告」一致性**

具體要求：

- runner 中每一個計數函式，其 **docstring 描述的公式必須與實作逐字一致**
- 每一個 denominator 必須在輸出中**明確印出其實際分子／分母定義與數值**
- 必須提供一個**獨立重算路徑**（與主路徑不同程式碼）比對結果
- 交付前必須執行 **mutation：故意改壞 denominator 或分子，驗證驗收測試會紅**

> 依據：A1 的 `_precision()` docstring 寫著正確公式，實作卻是 `len(rows)/len(rows)` 恆等式，
> 導致 `precision = 1.0` 被當成事實寫進 A2 工單作為前提。**這是本 repo 至今最貴的一次量測缺陷。**

---

## §7 交付物

| 檔案 | 動作 |
|---|---|
| `harness/run_ta2v2a3_measurement.py` | 新檔：集合機械計算 ＋ 指標重算 ＋ mutation 測試載體 |
| `harness/ta2v2a3_population.py` | 新檔：Stage 1／Stage 3 判準的**可執行**定義（純函式、arm-blind） |
| `tests/test_ta2v2a3_measurement.py` | 新檔：§6 的一致性驗收 ＋ mutation |
| `data/harness_out/tl12/ta2v2a3_measurement.json` | 新檔：四個集合、計數、Gate 判定 |
| `docs/TA2-V2A3-MEASUREMENT-RESULT.md` | 新檔：Stage 1／Stage 3 分類表 ＋ 一致率 ＋ 裁決 |

**不得**修改 A1／A2 實作、frozen v1、`src/**`、既有 A3 artifact。

---

## §8 硬性約束

- **0 實驗 LLM calls、0 network、0 new N、0 corpus mutation**、0 arm/probe/timestamp 修改
- **不得 replay corpus**、**不得**實作任何 A 規則、**不得**新增 marker list
- frozen v1 唯讀（sha256 必須維持 `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`）
- A1／A2 輸出**保留不覆蓋**
- 0 kill、0 restart、`:8000` 不寫入
- **不得** stage `clients/voice_companion/**`（51 項）與 13 項未追蹤 `docs/`
- 只 stage §7 列出的檔案

## §9 Out of Scope

A 規則實作／A4／E implementation／Gate 2 重跑／N=12／新 LLM call／新 corpus／調門檻／新 taxonomy 類別／per-observation rescue／semantic judge／dependency parser／修改 §3 clause 定義／回頭修改 A3 的 19-row denominator

## §10 Stop Conditions

提出第六類／修改 A 規則／執行 LLM call／replay corpus／提出新 marker list／改動 Gate 0/1/2 任一門檻／對 19-row denominator 做事後修改／把 v1 假陽性事後刪出母體（必須靠 §2.1 判準自然排除）／實作 rescue 規則

---

## §11 誠實邊界

- **本票不是乾淨的 pre-registration**：25 筆 corpus 已被看過。本票的門檻是 **prospective decision rule**，不是盲式預註冊。
- **Gate 0 可能直接 STOP。** 若 Stage 1 一致率 < 70%，本票的結論就是「母體定義仍不確定」，A 與 E 都不判。
- **Gate 1 可能直接 STOP。** 若 `|HEADROOM| < 8`，即使 Gate 0 通過也無法定案。
- **本票不保證 A 存活，也不保證 GO E。** 它只保證母體是由**可複用的判準**導出，而不是事後挑選。
- **Gate 2 全程維持 INCONCLUSIVE。**
