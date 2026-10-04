# TA-2 V2-A3 — Loss-Mechanism Taxonomy（READ-ONLY DIAGNOSTIC 結果）

> **Ticket**：TA-2-V2A3（Owner 2026-10-04 05:54 授權）
> **Baseline**：`main` = `origin/main` = `5969636`（A1 `50dd1f6` / A2 `f3d8846` / A2 spec `22cbc7d`）
> **Mode**：READ-ONLY / OFFLINE DIAGNOSTIC。**沒有** rule implementation、**沒有** replay corpus、**沒有** LLM call。
> **Gate 2**：全程維持 **INCONCLUSIVE**，本檔不含任何 Gate 2 判定。
> **角色**：主要標註者（primary annotator）。第二標註者（盲式）尚未執行，見 §6。

---

## §0 🔴 Owner 最終裁定（2026-10-04 09:25）— **本檔的 GO E 建議已被否法**

> **A3 正式狀態 = `STOP / INCONCLUSIVE FOR A-vs-E`**
> **不是** GO E，**不是** A survives。

本檔 §7／§8 由主要標註者提出的 **GO E 建議不成立**。

| 項目 | 裁定 |
|---|---|
| L09 的 clause 懺4例 | **語法 clause**（兩個完整述語 = 兩個小句） |
| L09 分類 | **N1** — 不翻成 LOCAL |
| L18／L19 | **v1 false positives，不是 A 的 headroom loss** |
| 正確的 future headroom population | **17 筆** |
| 原 A3 的 19-row denominator | **不得事後修改** |
| raw agreement 11/19 = 57.9% | **< 70% 停止線** |
| A3 能否宣布 GO E？ | **不能** |
| A3 能否宣布 A survives？ | **不能** |
| Gate 2 | **INCONCLUSIVE** |
| A4 | **不授權** |
| E / 96 calls | **不進行** |

### 為什麼已知 17 筆正確，仍不用它回頭改判

因為那是 **post-hoc denominator change**：先看 19 筆結果 → 發現 2 筆讓 GO E 過門檻 →
發現那 2 筆是 v1 假陳性 → 拿掉 → 得到 11/17 → 宣布 A 存活。

**即使這個 change 在科學上合理，也不能假装它是原本 A3 的 prospective decision。**

> **17 筆是正確的 future population，但 12/19 這條 stop rule 不得用它回頭改判本輪。**

### 主大腦追加的診斷

**「A2 只救 2/19 → Candidate A coverage 不足」這個 inference 目前被母體污染，不可成立。**
因為 19 筆裡至少有一部分**不是「A 應該救但沒救」，而是 v1 本身就是 false positive**。

下一張票是 **measurement contract correction**（`TA-2-V2A3-MEASUREMENT`），**不是 A4**。

---

## §1 已撤銷前提（不引用）

「已知 pollution proxies precision = 1.0」**已撤銷**，真值 **5/7 = 0.714**。
本檔任何推論**未**引用 1.0。唯一出現該數字的地方是 §8 引述 A2 JSON 的 `owner_stop_condition_triggered` 原文（紀錄，非證據）。

---

## §2 標註母體的獨立重算（19 筆已自證）

母體定義：`v1_frame != "unframed"` 且 `A2 a2_frame == "unframed"`。

| 項目 | 值 |
|---|---|
| corpus 總觀測 | 66（`data/harness_out/tl12/ta2a_gate2_four_arm_real.json`，唯讀） |
| v1 非 unframed | **25** |
| A2 非 unframed | **6** |
| **v1→A2 loss（本檔母體）** | **19** |
| A2 歷史 JSON `metrics.recall_loss_vs_v1_A2` | 19（**一致**） |
| 19 筆 obs_id 集合 vs A2 audit 集合 | **完全相同**（逐筆比對 0 mismatch） |
| A1 在這 19 筆上的 frame | 全部 `unframed`（A1 frame 非 unframed 者 0 筆） |
| v1 frame 分布 | `lunch` 3、`late_night_meal` 5、`night` 7、`siesta` 4 |
| A2 失敗路徑分布 | `R6_NO_ATTACHMENT` only 12；`R1+R6` 3；`R1` only 2；`R4` 1；`R3+R4` 1 |
| A2 R8 實際救回 | **2 / 25**（皆 `meal_timing/ON_correct`，皆 `吃`＋`午餐` 鄰接） |

判定依據三支皆唯讀：`harness/run_ta2a_gate2.py::extract_temporal_frame`（frozen v1，sha256 未變）、
`harness/ta2v2a2_event_attachment.py::apply_a2`、`harness/ta2v2a_attachment.py::apply_a1`。
**未修改**任何一支。corpus 檔案未被寫入（`git status --porcelain data/harness_out/tl12` 空）。

> **重算陷阱（供第二標註者避開）**：corpus 的 `results[probe][arm].observations` 是 **dict**（含
> `raw_text` / `temporal_frame` / …），**不是** str。對整個 dict 直接餵 `extract_temporal_frame`
> 會得到全 66 筆 `unframed` 的假結果，母體會被誤算成 0。本檔第一次嘗試即踩到，已修正。

---

## §3 標註判準（先於分類固定，供第二標註者複用）

分類空間為工單 §3 的 **CLOSED 五類**，本檔**未**新增、**未**造子類、**未**在看到彙總數字後修改。

**（a）clause 判準（本檔採「述語依附」而非 A1 的逗號切分）**
A1 `clause_spans()` 以 `。！？；：，、` 切段，會把「時間狀語 + 述語」也切開。本檔改用**語法**判準：

* **同一 clause**：時間成分為**述語的前置狀語**，或時間成分**即為該述語的論元／補語**（`算X`、`是X`）。
  例：`週六中午剛過十一點，正好補眠` ＝ 一個 clause（狀語 + 述語 `補眠`）；`凌晨快四點的飯，算宵夜` ＝ 一個 clause（主語 + 述語 `算`）。
* **跨 clause**：`，`兩側**各自帶述語**的並列小句（asyndeton），需靠語用因果／省略主語回指才連得起來。
  例：`夜深了，該睡了` ＝ 兩個 clause。

**（b）local 的充分條件（核心）**
frame-carrying 的時間成分必須**被述及於該 event 本身**（`算宵夜` 述及「這頓飯」、`正好補眠` 以時間狀語修飾 nap），
而**不是**述及別的東西（時鐘時刻、語氣、問候語）。

**（c）分類門檻**
* 時間成分**述及 event**、且關係**同 clause 可指出** → **L3**（本母體無 L1／L2 命中）。
* 時間成分述及的是**時鐘時刻／問候語／心情**，要連到 event 必須跨小句或跨句 → **N1**。
* **temporal-side 與 event-side 塌縮成同一個詞**（`睡` 既是事件又是 frame 觸發詞），或 frame 側是
  比喻／定語用法而**根本沒有 event 共現** → **N0**。
* **僅同句共現不構成 L3**（工單明文）。以下分類皆已逐筆排除「只因在同一句」就判 L。

**（d）硬性排除（工單 §3）**
本檔**未**實作任何救援規則、**未**提出新 marker list、**未**用 LLM judge、**未**做 per-case rescue。
凡「event-side／temporal-side 寫不出具體 relation」者，一律落 N 類。

---

## §4 19 列分類表

`event_side` / `temporal_side` 兩欄為該筆的候選成分；`relation_evidence` 引原文。

| # | obs_id | probe | arm | v1_frame | A2_frame | class | event_side | temporal_side | relation_evidence | rationale |
|---|---|---|---|---|---|---|---|---|---|---|
| L01 | `meal_timing/ON_correct/run1` | meal_timing | ON_correct | lunch | unframed | **N1** | `吃飽`（第 3 句） | `午餐`（`差不多是午餐時間了`，第 2 句） | 原文：`週六中午十一點半，差不多是午餐時間了。…吃飽再繼續過週末吧。` | `午餐時間` 句是**繫詞句，述及的是「時鐘時刻」而非吃飯事件**；吃的事件（`吃飽`）在第 3 句。L2 的必要條件是「該 phrase 與 event 的關係**仍可局部觀察**」——此處不可觀察，關係需跨句建立。故不判 L2，判 N1。 |
| L02 | `meal_timing/ON_correct/run4` | meal_timing | ON_correct | lunch | unframed | **N1** | `吃飽`（第 3 句） | `午餐`（第 2 句） | 原文：`差不多是午餐時間了。等一下想吃什麼？吃飽一點，下午才有精神。` | 同 L01：繫詞句述及時刻，event 在另一句。`下午` 亦與 `吃飽` 跨句。 |
| L03 | `meal_timing/ON_correct/run5` | meal_timing | ON_correct | lunch | unframed | **N1** | `吃飽`（第 3 句） | `午餐`（第 2 句） | 同 L02（raw text 逐字相同） | 同 L01／L02。 |
| L04 | `meal_timing/ON_mismatched/run1` | meal_timing | ON_mismatched | late_night_meal | unframed | **L3** | `去吃飯`（＋時段狀語 `這個時間`） | `宵夜` | 原文：`這個時間去吃飯，算宵夜還是提早的早餐？` | frame 成分 `宵夜` 由述語 `算` **直接述及同一 clause 內的吃飯事件**，且時段狀語 `這個時間` 就在同一小句。屬「同 clause 的明確 event↔temporal 分類」，非僅共現。 |
| L05 | `meal_timing/ON_mismatched/run2` | meal_timing | ON_mismatched | late_night_meal | unframed | **L3** | `凌晨快四點的飯`（clause 主語所指的那頓飯） | `宵夜` | 原文：`凌晨快四點的飯，算宵夜還是早餐？` | `算` 述及的是同一 clause 主語（那頓飯）；句中已有時間成分 `凌晨快四點`。frame 與 event 的分類關係在同一 clause 完成，具體可指出。 |
| L06 | `meal_timing/ON_mismatched/run3` | meal_timing | ON_mismatched | late_night_meal | unframed | **L3** | `凌晨快四點的飯` | `宵夜` | 原文：`凌晨快四點的飯，算宵夜還是早餐啊？` | 同 L05。 |
| L07 | `meal_timing/ON_mismatched/run4` | meal_timing | ON_mismatched | late_night_meal | unframed | **L3** | `去吃飯`（＋`這個時間`） | `宵夜` | 原文：`這個時間去吃飯，算宵夜，也算早早餐。` | 同 L04，且 `算` 帶兩個並列 frame 論元，關係更明確。 |
| L08 | `meal_timing/ON_mismatched/run5` | meal_timing | ON_mismatched | late_night_meal | unframed | **L3** | `凌晨快四點這頓飯` | `宵夜` | 原文：`凌晨快四點這頓飯，算宵夜，也算提早的早餐。` | 同 L05／L06。 |
| L09 | `night_rest/ON_correct/run0` | night_rest | ON_correct | night | unframed | **N1** | `睡`（`該睡了`） | `夜`（`夜深了`） | 原文：`是啊，夜深了，該睡了。` | `，`兩側各自帶述語（`夜深了` / `該睡了`），是兩個 clause；`夜深了` 述及的是**夜的狀態**而非睡的事件，二者的連結是語用因果（所以該睡），需跨 clause 推論。且 `睡` 另被 `R1_ACTION_TOKEN` 否決，event-side 本身不穩。 |
| L10 | `night_rest/ON_correct/run2` | night_rest | ON_correct | night | unframed | **N1** | `睡`（`去睡吧`） | `深夜`（`該收尾的深夜了`） | 原文：`嗯，聽起來是該收尾的深夜了。別再撐了，去睡吧。` | `深夜` 為繫詞句述及「收尾時刻」，event 在**下一句**；跨句。 |
| L11 | `night_rest/ON_correct/run3` | night_rest | ON_correct | night | unframed | **N1** | `睡`（`該睡了`） | `深夜`（`深夜了`） | 原文：`嗯，該睡了。深夜了，別再撐著，讓今天慢慢收尾吧。` | `深夜了` 為獨立小句且述及時刻／狀態，event 在前一句；跨句。 |
| L12 | `night_rest/ON_correct/run4` | night_rest | ON_correct | night | unframed | **N0** | `睡` | `睡`（**同一詞**） | 原文：`嗯，該睡了。今天到這裡就好，別再撐了。…好好睡一覺。晚安，好夢。` | v1 的 night 由 `睡` 觸發（A2 亦以 `R1_ACTION_TOKEN` 否決）。此處 **temporal-side 與 event-side 塌縮為同一詞**：不存在獨立的時間成分附著於該事件。night 讀法須由「睡覺」推得時段，屬**需語意猜測**，非可指出的 local relation。`晚安`／`今天` 皆在不同小句。 |
| L13 | `night_rest/ON_mismatched/run0` | night_rest | ON_mismatched | siesta | unframed | **N1** | `睡`（`去睡吧` / `好好睡一覺`，前文） | `午安` | 原文：`那就去睡吧。週六中午十一點多，…好好睡一覺。  \n午安，也晚安。` | `午安` 是**獨立問候語**，不述及任何 event；siesta 歸屬要靠「前文陳述的時鐘時刻」＋「問候」＋「睡」三者跨句組合。 |
| L14 | `night_rest/ON_mismatched/run1` | night_rest | ON_mismatched | night | unframed | **N0** | `睡` | `睡`（**同一詞**） | 原文：`去睡吧，別再撐了。…睡個好覺，醒來再繼續今天。晚安（雖然是中午）。` | 同 L12：唯一觸發詞是動作詞 `睡`（`R1_ACTION_TOKEN` 否決），無獨立時間成分在同 clause 附著。括註「雖然是中午」是話語層反諷，更證明 night 歸屬並非 local 可觀察。 |
| L15 | `night_rest/ON_mismatched/run2` | night_rest | ON_mismatched | siesta | unframed | **L3** | `補眠`（＝補+眠，nap 述語） | `中午剛過十一點`（狀語）＋ `補眠` | 原文：`週六中午剛過十一點，正好補眠。` | 同一 clause 內，`中午剛過十一點` 作前置時間狀語修飾述語 `正好補眠`，且 `正好` 明確標示「此刻正合適」。event 與時間的關係具體可指出。 |
| L16 | `night_rest/ON_mismatched/run4` | night_rest | ON_mismatched | siesta | unframed | **N1** | `去睡`（`好好去睡吧`，上一句） | `午安` | 原文：`知道，週六中午剛過十一點。…好好去睡吧。  \n午安，祝你好眠。` | `午安` 為獨立問候語小句，不述及 event；時鐘時刻、event、frame 詞分處三句。 |
| L17 | `night_rest/ON_mismatched/run5` | night_rest | ON_mismatched | siesta | unframed | **N1** | `睡`（`好好睡一覺`） | `午安` | 原文：`…別再撐了。  \n午安，好好睡一覺，醒來再聊。` | `午安` 與 `好好睡一覺` 同句，但 `午安` 為**獨立感嘆／問候成分**，不作為該述語的時間狀語；僅同句共現，工單明文排除。siesta 依賴前文「週六中午十一點多」。 |
| L18 | `irrelevant/ON/run1` | irrelevant | ON | night | unframed | **N0** | **無**（全句無事件述語） | `夜`（`剛入夜`） | 原文：`…但被你這麼一說，我好像也有一件——大概是剛入夜的那種深藍，袖口沾著一點路燈剛亮起來的暖黃。` | `夜` 用於**外套顏色的比喻**（`R4_SIMILE_FRAME`）。屬背景描述／metaphor，raw evidence 中**無 event 共現**，無可指出的 local relation。 |
| L19 | `irrelevant/ON/run2` | irrelevant | ON | night | unframed | **N0** | **無**（全句無事件述語） | `夜色`（`夜色剛落下`） | 原文：`如果我有外套，大概是夜色剛落下、路燈一盞盞亮起時的那種藍，邊緣帶一點暖黃。` | 同 L18：`夜色` 為**定語位置**（`R3_ATTRIBUTIVE_NOUN` ＋ `R4_SIMILE_FRAME`），比喻藍色，無 event 共現。 |

---

## §5 計數與門檻算術

| 類 | 定義摘要 | 筆數 | obs |
|---|---|---|---|
| L1 | Direct Local Attachment | **0** | — |
| L2 | Local Noun-Phrase Attachment | **0** | — |
| L3 | Local Clause Attachment | **6** | L04, L05, L06, L07, L08, L15 |
| N1 | Non-Local / Discourse-Dependent | **9** | L01, L02, L03, L09, L10, L11, L13, L16, L17 |
| N0 | No Defensible Local Attachment | **4** | L12, L14, L18, L19 |
| **合計** | | **19** | ✔ 等於母體 |

```
LOCAL        = L1 + L2 + L3 = 0 + 0 + 6 = 6
OUTSIDE_LOCAL = N1 + N0     = 9 + 4 = 13
校验          6 + 13 = 19   ✔
```

### 門檻裁決（工單 §4 pre-specified stop rule）

```
OUTSIDE_LOCAL = 13
13 >= 12  ->  ✅ GO E
```

## 🔴 裁決：**GO E**

（`OUTSIDE_LOCAL` 為 13，超過 12/19 門檻 1 筆。門檻以整數筆數判讀，未做任何調整。）

---

## §6 標註者信度（主大腦於兩份到齊後填入，2026-10-04）

第二標註者以**盲式**獨立完成同一份分類（未讀本檔任何分類結果；其自陳並列出全部閱讀檔案）。
下表為**主大腦**逐筆接合，**未仲裁、未多數決、未取較嚴格者**。

| # | obs_id | 主要標註者 | 第二標註者 | 一致？ | 影響 LOCAL/OUTSIDE 分區？ |
|---|---|---|---|---|---|
| L01 | `meal_timing/ON_correct/run1` | N1 | N1 | ✅ | — |
| L02 | `meal_timing/ON_correct/run4` | N1 | N1 | ✅ | — |
| L03 | `meal_timing/ON_correct/run5` | N1 | N1 | ✅ | — |
| L04 | `meal_timing/ON_mismatched/run1` | L3 | L1 | ❌ **DISAGREED** | ❌ 否（同為 LOCAL，僅子家族不同） |
| L05 | `meal_timing/ON_mismatched/run2` | L3 | L1 | ❌ **DISAGREED** | ❌ 否（同為 LOCAL） |
| L06 | `meal_timing/ON_mismatched/run3` | L3 | L1 | ❌ **DISAGREED** | ❌ 否（同為 LOCAL） |
| L07 | `meal_timing/ON_mismatched/run4` | L3 | L1 | ❌ **DISAGREED** | ❌ 否（同為 LOCAL） |
| L08 | `meal_timing/ON_mismatched/run5` | L3 | L1 | ❌ **DISAGREED** | ❌ 否（同為 LOCAL） |
| L09 | `night_rest/ON_correct/run0` | N1 | L3 | ❌ **DISAGREED** | ✅ **是 — 唯一決策樞紐** |
| L10 | `night_rest/ON_correct/run2` | N1 | N1 | ✅ | — |
| L11 | `night_rest/ON_correct/run3` | N1 | N1 | ✅ | — |
| L12 | `night_rest/ON_correct/run4` | N0 | N1 | ❌ **DISAGREED** | ❌ 否（同為 OUTSIDE） |
| L13 | `night_rest/ON_mismatched/run0` | N1 | N1 | ✅ | — |
| L14 | `night_rest/ON_mismatched/run1` | N0 | N1 | ❌ **DISAGREED** | ❌ 否（同為 OUTSIDE） |
| L15 | `night_rest/ON_mismatched/run2` | L3 | L3 | ✅ | — |
| L16 | `night_rest/ON_mismatched/run4` | N1 | N1 | ✅ | — |
| L17 | `night_rest/ON_mismatched/run5` | N1 | N1 | ✅ | — |
| L18 | `irrelevant/ON/run1` | N0 | N0 | ✅ | — |
| L19 | `irrelevant/ON/run2` | N0 | N0 | ✅ | — |

### §6.1 兩種一致率（必須分開看）

| 口徑 | 數值 | 說明 |
|---|---|---|
| **原始一致率** | **11/19 = 57.9%** | 低於工單 §5 預設的 70% 停止線 |
| **分區一致率**（LOCAL vs OUTSIDE） | **18/19 = 94.7%** | 8 筆分歧中有 **5 筆是 L1↔L3 子家族標籤**，不移動分區；2 筆為 N0↔N1（同屬 OUTSIDE） |

**8 筆分歧中，只有 L09 一筆會翻轉 LOCAL/OUTSIDE 分區。**

### §6.2 分歧的性質：定義驅動，不是證據驅動

- **L04–L08**（5 筆）：兩人都認為是 LOCAL，僅在「顯式繫詞 `算` 繫結」算 L1（直接動詞相鄰）還是 L3（同 clause 繫結）不同。**不影響裁決。**
- **L12、L14**（2 筆）：兩人都認為在 local premise 之外，只是 N0（無可辯護關係）vs N1（需跨句）。**不影響裁決。**
- **L09**（1 筆）：`「是啊，夜深了，該睡了。」`
  - 主要標註者 **N1**：`，` 兩側各自帶述語（`深了` / `該睡了`），依其預設判準屬並列小句 → 跨 clause
  - 第二標註者 **L3**：採**句界**讀法（`。！？`），兩者同句 → local attachment
  - **這是 clause 粒度定義差異，不是對 raw evidence 的不同解讀。**

### §6.3 三種 clause 慣例皆導向 GO E

| clause 慣例 | LOCAL | OUTSIDE | 裁決 |
|---|---|---|---|
| A1/frozen v1 逗號級（`、，。！？；：`） | 0–1 | 18–19 | **GO E** |
| 句界級（`。！？`） | 7 | 12 | **GO E**（剛好達線） |
| 主要標註者混合判準 | 6 | 13 | **GO E** |

翻案為「A 的最後一次機會」需要**兩個條件同時成立**：採句界讀法 **且** 將 L09 判為 LOCAL。

### §6.4 🔴 母體定義的已知偏差（主大腦揭露，兩位標註者皆已各自指出）

19 筆中有 **2 筆（L18、L19）來自 `irrelevant` 對照組的比喻污染**
（`「剛入夜的那種深藍」`／`「夜色剛落下…那種藍」`），是 **v1 假陽性**。
A 正確地**沒有**救回它們——那不是 headroom 損失，是 v1 的錯。

但把它們計入 `OUTSIDE_LOCAL` 會**灌水計數、偏袒 GO E**：
- 主要標註者：剔除後 `OUTSIDE` 13 → **11**
- 第二標註者：剔除後 `OUTSIDE` 12 → **11**

**兩者在 17 筆母體下都會落到門檻之下。** 12/19 這條 stop rule 把「A 救不到」與「本來就不該救」混為一談。



**分歧處理（pre-specified，工單 §5）**：分歧**逐筆保留、不仲裁、不多數決、不取較嚴格者**，
兩種分類同時留在紀錄並標 `DISAGREED`。若一致率 < 70%，**回報分歧並停止**，
不得據此決定 A 或 E。

### 已知的高分歧風險點（誠實揭露，非事後調整）

我的分類**唯一**會讓裁決翻轉的敏感點是 **L01–L03（`午餐時間` 組，3 筆）**：
工單 §3 的 L2 定義**以 `午餐時間` 為例**，但 L2 的**必要條件**是「該 phrase 與 event 的關係**仍可局部觀察**」。
我讀到的 raw text 裡，`差不多是午餐時間了` 是**繫詞句、述及時鐘時刻**，吃飯事件在**另一句**，
故我判 N1 而非 L2。若第二標註者判 L2，`LOCAL` 將由 6 升至 9、`OUTSIDE_LOCAL` 由 13 降至 10，
門檻會翻成「A 的最後一次機會」。

**我沒有為了讓裁決成立而調整這 3 筆**，理由是 L2 的必要條件在 raw text 上確實不成立。
此敏感點在此明文揭露，交由主大腦／Owner 依 §5 分歧流程裁決，**不由我單方仲裁**。

---

## §7 A2 機制侷限的明確發現

### 7.1 三個必答問題

**Q1：19 筆 loss 裡是否真的存在多於一種 local-attachment 機制家族？**
**否。** 19 筆中的 LOCAL 只有 6 筆，且 **6 筆全部落在 L3、屬同一家族**：
「同一 clause 內，frame 成分由 `算`／`正好` 這類述語**直接述及事件**」。
**L1 = 0、L2 = 0**。不存在第二種 local 機制家族。

**Q2：A2 是否只測到一種很窄的 local 機制？**
**是，而且極窄。** A2 的 R8 只實作一種 pattern：`**(event verb) + gap ≤ 1 個 filler + (temporal token)**`
（`_r8_event_side_candidates` 的 `gap_len <= 1`）。在 25 筆 v1-framed 中只命中 **2 筆**，
且兩筆**都是** `吃`＋`午餐` 鄰接、**都在** `meal_timing/ON_correct`。
A2 JSON 自陳的 `go_c_mechanism_validity` 亦記錄 `lunch_share_of_rescues = 1.0`、
`other_frame_recovery_exists = false`。

**Q3：證據支持還是否決 Candidate A 作為一個機制類別？**
**否決（作為「足以覆蓋 loss 的機制類別」）。** 三條證據：

1. **覆蓋率**：Candidate A 的 local premise 在 19 筆 loss 中至多覆蓋 **6/19 ≈ 32%**。
2. **pattern 不對齊**：我能找到 local relation 的 6 筆，其關係是
   `算宵夜`（述語述及 frame）／`正好補眠`（狀語修飾 frame 詞），
   **沒有一筆**是 R8 實作的「動詞與時間名詞相鄰」。也就是說
   **即使 R8 不再那麼窄，它量測的也不是這 6 筆的機制**。這同時是「R8 太窄」與「A 的 coverage 不足」兩件事。
3. **母體大半本來就不是 local**：13/19 的失敗**無法**靠放寬 local window 救回，
   因為那些觀測裡根本沒有可附著的 event（比喻／問候／動作詞塌縮）或需要跨句推理。

### 7.2 「R8 太窄」與「A 的 premise 不足」的區分

兩者**同時成立，但比重不同**：

* **R8 太窄**：成立。R8 只覆蓋 adjacency pattern，錯過 L15（`中午剛過十一點`＋`正好補眠`）這種
  狀語＋述語型 local attachment。
* **A 的 local premise coverage 不足**：**更關鍵**。即使 R8 擴充到涵蓋 §4 標為 L3 的 6 筆，
  仍有 **13 筆**落在 local premise 的邊界之外。R8 的窄是**實作層**問題，
  A 的 coverage 不足是**機制類別層**問題——後者才決定 GO E。

### 7.3 失敗路徑與分類的對應（診斷價值）

A2 對這 19 筆的失敗路徑，與本檔分類**高度對齊**：

| A2 失敗路徑 | 筆數 | 本檔分類 | 解讀 |
|---|---|---|---|
| `R6_NO_ATTACHMENT` only | 12 | 6×L3 + 6×N1 | R6 擋下的 12 筆裡，**一半有 local relation、一半沒有**——單看 `undecided` 無法區分，這正是本票的貢獻。 |
| `R1_ACTION_TOKEN` ＋ `R6` | 3 | 3×N1 | `睡` 動作詞否決；event-side 本身不穩。 |
| `R1_ACTION_TOKEN` only | 2 | 2×N0 | temporal-side 與 event-side 塌縮。 |
| `R4_SIMILE_FRAME`（±`R3`） | 2 | 2×N0 | 比喻／定語，無 event 共現。 |

---

## §8 Production Integrity 與 Git State

| 檢查 | 結果 |
|---|---|
| 實驗 LLM calls | **0**（本票未 import `llm_call`、未發任何請求） |
| network calls | **0** |
| corpus replay | **0**（未執行任何 replay 入口） |
| corpus mutation | **0**。`git status --porcelain data/harness_out/tl12` **空輸出**；7 個既有檔案皆未被寫入 |
| frozen v1 sha256 | `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`（**與工單要求一致，維持不變**） |
| R1–R8 | **未修改** |
| A1 / A2 實作 | **未修改**（唯讀 import） |
| arm / probe / timestamp | **未修改** |
| 行程操作 | **0 kill、0 restart**。本票**未與任何服務互動** |
| `:8000` | 未寫入、未連線 |
| 暫存腳本 | 全部置於 `$env:TEMP\ta2a3_probe*.py`（**未留在 repo 根目錄**，不會被 pytest 收集） |
| 本票產出 | **1 份**新 artifact：`docs/TA2-V2A3-LOSS-MECHANISM-TAXONOMY.md`（未 commit） |
| 未動 Owner 資產 | `clients/voice_companion/**` 51 項未提交改動**未 stage、未修改**；`docs/` 13 項未追蹤檔**未 stage、未修改** |
| `git status --porcelain` 總行數 | 開工前 **129** → 交付時 **130**，差額恰為本檔這 1 個新增未追蹤檔。**其餘 129 項完全未動**（含 Owner 的 `clients/voice_companion/**` 與 `docs/` 13 項未追蹤檔） |
| `main` / `origin/main` | `5969636`（**本票未 commit、未改動任何 ref**） |

---

## §9 未解決問題

1. **第二標註者信度未知。** 一致率與分歧列尚不可得；§6 的敏感點（L01–L03 會翻轉門檻）必須由第二標註者獨立裁決。
2. **L01–L03 的 L2 vs N1 判準爭議。** 工單以 `午餐時間` 為 L2 範例，但其必要條件在這批 raw text 上不成立。此為**判準詮釋**問題，不是分類漏洞；依 §5 不由我仲裁。
3. **`irrelevant` probe 的 2 筆 N0 性質。** L18/L19 屬 v1 的 false positive（比喻當成 frame），它們計入 19 筆 loss 但**不應被解讀為「A 該救回的觀測」**。若只統計「真陽性」的 17 筆，OUTSIDE_LOCAL = 11，恰好落在門檻另一側——**本檔仍以工單定義的 19 筆為準**（母體未經我調整）。
4. **A1 `_precision()` 缺陷未修。** 真值 5/7 = 0.714 已於 A2 JSON 記錄；本票為唯讀診斷，**未修改** A1。
