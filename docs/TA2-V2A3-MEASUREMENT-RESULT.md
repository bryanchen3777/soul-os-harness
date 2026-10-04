# TA-2-V2A3-MEASUREMENT — RESULT（Stage 1 標註者 1／2）

> **工單**：`docs/TA2-V2A3-MEASUREMENT-WORK-ORDER.md`
> **授權依據**：Owner 2026-10-04 09:25 裁定（`docs/TA2-V2A3-LOSS-MECHANISM-TAXONOMY.md` §0）
> **Baseline**：`main` = `origin/main` = `248216d`
> **角色**：**Stage 1 標註者（第一位）**。第二位盲式標註者獨立作業，本檔不含其結果。
> **Mode**：READ-ONLY / OFFLINE MEASUREMENT CONTRACT CORRECTION。**0 實驗 LLM calls、0 network、0 replay、0 corpus mutation。**
> **Gate 2**：全程維持 `INCONCLUSIVE`。**本檔不宣告 A，也不宣告 E。**

---

## §0 一句話

母體由**判準**導出、不是被挑出：對完整的 **25 筆 v1-framed observation** 逐筆判 Stage 1，
`v1 假陽性` 由封閉判準**自然被判為 `NOT_DEFENSIBLE` 而被排除**；`HEADROOM` 再由集合代數推導。

---

## §1 Stage 1 — v1 Defensibility Judgment（25 筆全覆蓋）

**判準**（工單 §2.1 封閉清單，**預設值是 `DEFENSIBLE`**）：只有命中下列之一才判 `NOT_DEFENSIBLE` ——
metaphor/simile／背景無關／純共現／token 無法歸屬／需 unsupported guessing。
其餘**一律** `DEFENSIBLE`。

| # | obs_id | v1_frame | 判定 | reason_code | 引用原文 | 理由 | runner 位置 |
|---|---|---|---|---|---|---|---|
| 1 | `_control/ON/1` | night | **NOT_DEFENSIBLE** | `metaphor_or_simile` | `大概是剛入夜的那種深藍` | frame 觸發詞 `夜` 出現在顏色描述（外套的顏色），不指涉任何目標事件；同句 `路燈剛亮起來的暖黃` 亦為顏色。命中封閉清單第 1 項。 | — |
| 2 | `_control/ON/2` | night | **NOT_DEFENSIBLE** | `metaphor_or_simile` | `大概是夜色剛落下、路燈一盞盞亮起時的那種藍` | `夜` 只出現在顏色描述；irrelevant 對照，無目標事件可歸屬。命中封閉清單第 1 項。 | — |
| 3 | `meal_timing/ON_correct/0` | lunch | DEFENSIBLE | `default_defensible` | `也差不多該吃午餐了` | 時間詞 `午餐` 與活動述語 `吃` 同 clause 直接相連。 | A2 survivor |
| 4 | `meal_timing/ON_correct/1` | lunch | DEFENSIBLE | `default_defensible` | `差不多是午餐時間了` | `午餐` 描述此刻時段，回應主題是接下來吃什麼；非比喻。 | **HEADROOM** |
| 5 | `meal_timing/ON_correct/2` | lunch | DEFENSIBLE | `default_defensible` | `差不多該吃午餐了` | 時間詞 `午餐` 與活動述語 `吃` 同 clause 直接相連。 | A2 survivor |
| 6 | `meal_timing/ON_correct/4` | lunch | DEFENSIBLE | `default_defensible` | `差不多是午餐時間了` | 同 #4。 | **HEADROOM** |
| 7 | `meal_timing/ON_correct/5` | lunch | DEFENSIBLE | `default_defensible` | `差不多是午餐時間了` | 同 #4。 | **HEADROOM** |
| 8 | `meal_timing/ON_mismatched/0` | late_night_meal | DEFENSIBLE | `default_defensible` | `凌晨快四點，這頓到底算宵夜還是提早的早餐？` | `宵夜` 被述語 `算` 套用到吃飯事件 `這頓`，`凌晨快四點` 明確定位該事件。 | A2 survivor |
| 9 | `meal_timing/ON_mismatched/1` | late_night_meal | DEFENSIBLE | `default_defensible` | `這個時間去吃飯，算宵夜還是提早的早餐？` | `這個時間` 定位去吃飯動作，`宵夜` 以 `算` 歸類該事件。 | **HEADROOM** |
| 10 | `meal_timing/ON_mismatched/2` | late_night_meal | DEFENSIBLE | `default_defensible` | `凌晨快四點的飯，算宵夜還是早餐？` | `宵夜` 由述語 `算` 套用到吃飯事件，非比喻、非僅背景。 | **HEADROOM** |
| 11 | `meal_timing/ON_mismatched/3` | late_night_meal | DEFENSIBLE | `default_defensible` | `凌晨快四點的飯，算宵夜還是早餐啊？` | 同 #10。 | **HEADROOM** |
| 12 | `meal_timing/ON_mismatched/4` | late_night_meal | DEFENSIBLE | `default_defensible` | `這個時間去吃飯，算宵夜，也算早早餐。` | 同 #9。 | **HEADROOM** |
| 13 | `meal_timing/ON_mismatched/5` | late_night_meal | DEFENSIBLE | `default_defensible` | `凌晨快四點這頓飯，算宵夜，也算提早的早餐。` | `宵夜` 被述語 `算` 套用到 `這頓飯`，`凌晨快四點` 明確定位。 | **HEADROOM** |
| 14 | `night_rest/ON_correct/0` | night | DEFENSIBLE | `default_defensible` | `夜深了，該睡了` | `夜深了` 明確陳述時段；是否同 clause 屬 Stage 3 議題，不影響 Stage 1。 | **HEADROOM** |
| 15 | `night_rest/ON_correct/1` | night | DEFENSIBLE | `default_defensible` | `週六的深夜已經到尾聲了` | 明確時段詞，直接陳述而非比喻。 | A2 survivor |
| 16 | `night_rest/ON_correct/2` | night | DEFENSIBLE | `default_defensible` | `聽起來是該收尾的深夜了` | `深夜` 明確陳述時段，與勸睡事件同回應。 | **HEADROOM** |
| 17 | `night_rest/ON_correct/3` | night | DEFENSIBLE | `default_defensible` | `該睡了。深夜了，別再撐著` | `深夜了` 明確陳述時段。 | **HEADROOM** |
| 18 | `night_rest/ON_correct/4` | night | DEFENSIBLE | `default_defensible` | `嗯，該睡了。` | frame 由 `睡`（`該睡了`／`睡一覺`）與結句 `晚安` 承載；全文無矛盾時段詞、非比喻。 | **HEADROOM** |
| 19 | `night_rest/ON_correct/5` | night | DEFENSIBLE | `default_defensible` | `週六的深夜已經到尾聲了` | 明確時段詞。 | A2 survivor |
| 20 | `night_rest/ON_mismatched/0` | siesta | DEFENSIBLE | `default_defensible` | `週六中午十一點多` | frame 由 `午安` 承載，文本明確陳述中午時段並勸睡；`午安` 屬 Stage 3 的 relation 議題，不構成 Stage 1 否定理由。 | **HEADROOM** |
| 21 | `night_rest/ON_mismatched/1` | night | DEFENSIBLE | `default_defensible` | `晚安（雖然是中午）。` | 🔴 **本輪最低信心的一筆**。文本以 `晚安`／`睡` 明文承載 night frame，無需 guessing 即可讀出；括註 `（雖然是中午）` 屬 Stage 3 的 local-relation 議題。封閉清單無一項明確命中，依**預設值**判 `DEFENSIBLE`。 | **HEADROOM** |
| 22 | `night_rest/ON_mismatched/2` | siesta | DEFENSIBLE | `default_defensible` | `週六中午剛過十一點，正好補眠` | 明確時段詞 + `補眠` 即 nap 事件本身。 | **HEADROOM** |
| 23 | `night_rest/ON_mismatched/3` | siesta | DEFENSIBLE | `default_defensible` | `週六中午十一點多，熬到現在也真的該補眠了` | 明確時段詞 + `補眠` 即 nap 事件本身。 | A2 survivor |
| 24 | `night_rest/ON_mismatched/4` | siesta | DEFENSIBLE | `default_defensible` | `午安，祝你好眠。` | 明確陳述 `週六中午剛過十一點` 並勸睡，frame 由 `午安` 承載。 | **HEADROOM** |
| 25 | `night_rest/ON_mismatched/5` | siesta | DEFENSIBLE | `default_defensible` | `午安，好好睡一覺，醒來再聊。` | 同 #24。 | **HEADROOM** |

**Stage 1 計數**（`pop.count_stage1_verdicts`，母體 = 25）：

| 項 | 分子定義 | 分子 | 分母定義 | 分母 | 值 |
|---|---|---|---|---|---|
| `DEFENSIBLE` | `len([j for j in judgments if j.verdict == DEFENSIBLE])` | **23** | `len(judgments)` | 25 | 0.92 |
| `NOT_DEFENSIBLE` | 分母 − 分子 | **2** | `len(judgments)` | 25 | 0.08 |

> 兩筆 `NOT_DEFENSIBLE` **全部**由封閉清單第 1 項（metaphor／顏色描述）自然導出。
> **沒有**任何一筆被事後剔除；母體仍是完整的 25 筆。

---

## §2 Stage 3 — Headroom Taxonomy（全部 23 筆 `DEFENSIBLE`）

**分兩件事報**：

1. **分類空間**：CLOSED 五類，未新增、未造子類、未在看到彙總數字後修改。
2. **runner 事後機械扣除**：下表 `runner 位置` 欄標示 `A2 survivor` 者**不進** `HEADROOM`
   （`HEADROOM = defensible \ a2_survivors`，工單 §2.2）。標註者**不知道** survivor 身分。

**clause 定義**（Owner 裁定，固定）：clause = 語法小句，由**完整述語**界定。
`夜深了` 與 `該睡了` 是兩個完整述語 ⇒ 兩個 clause。**不採**逗號切分，**不採**句界讀法。

| # | obs_id | v1_frame | 類 | event_side | temporal_side | 引用原文 | rationale | runner 位置 |
|---|---|---|---|---|---|---|---|---|
| 1 | `meal_timing/ON_correct/0` | lunch | **L1** | `吃` | `午餐` | `也差不多該吃午餐了` | 活動動詞 `吃` 直接以 `午餐` 為受詞，同 clause；前置時間狀語 `週六中午十一點半` 無述語，不另成 clause。 | A2 survivor |
| 2 | `meal_timing/ON_correct/1` | lunch | **N1** | `吃飽` | `午餐時間` | `差不多是午餐時間了。等一下想好吃什麼了嗎？吃飽再繼續過週末吧。` | `午餐時間` 所在 clause 的完整述語是 `是`，述及時鐘時刻；吃飯事件述語 `吃飽` 在另一 clause。雖是 local temporal NP，但與 event 的關係**不可局部觀察**，不符 L2 必要條件 ⇒ N1。 | **HEADROOM** |
| 3 | `meal_timing/ON_correct/2` | lunch | **L1** | `吃` | `午餐` | `差不多該吃午餐了` | 同 #1。 | A2 survivor |
| 4 | `meal_timing/ON_correct/4` | lunch | **N1** | `吃飽` | `午餐時間` | `差不多是午餐時間了。等一下想吃什麼？吃飽一點，下午才有精神。` | 同 #2。 | **HEADROOM** |
| 5 | `meal_timing/ON_correct/5` | lunch | **N1** | `吃飽` | `午餐時間` | `差不多是午餐時間了。等一下想吃什麼？吃飽一點，下午才有精神。` | 同 #2。 | **HEADROOM** |
| 6 | `meal_timing/ON_mismatched/0` | late_night_meal | **L3** | `這頓`（述語 `算`） | `宵夜` | `凌晨快四點，這頓到底算宵夜還是提早的早餐？` | 述語 `算` 主詞就是吃飯事件 `這頓`，`宵夜` 為其補語，同 clause 內關係具體可指。`吃` 未作述語、`宵夜` 非活動動詞直接受詞 ⇒ 非 L1 ⇒ L3。 | A2 survivor |
| 7 | `meal_timing/ON_mismatched/1` | late_night_meal | **L3** | `去吃飯` | `這個時間`、`宵夜` | `這個時間去吃飯，算宵夜還是提早的早餐？` | 同 clause 內 `去吃飯` 與 `宵夜` 並存，`算` 對吃飯事件做時段歸類，關係具體可指；`吃飯` 受詞是 `飯` ⇒ 非 L1 ⇒ L3。 | **HEADROOM** |
| 8 | `meal_timing/ON_mismatched/2` | late_night_meal | **L3** | `飯`（述語 `算`） | `宵夜` | `凌晨快四點的飯，算宵夜還是早餐？` | 時間狀語 + 事件名詞作主詞，述語 `算` 以 `宵夜` 歸類該事件，同 clause ⇒ L3。 | **HEADROOM** |
| 9 | `meal_timing/ON_mismatched/3` | late_night_meal | **L3** | `飯`（述語 `算`） | `宵夜` | `凌晨快四點的飯，算宵夜還是早餐啊？` | 同 #8。 | **HEADROOM** |
| 10 | `meal_timing/ON_mismatched/4` | late_night_meal | **L3** | `去吃飯` | `這個時間`、`宵夜` | `這個時間去吃飯，算宵夜，也算早早餐。` | 同 #7。 | **HEADROOM** |
| 11 | `meal_timing/ON_mismatched/5` | late_night_meal | **L3** | `這頓飯`（述語 `算`） | `宵夜` | `凌晨快四點這頓飯，算宵夜，也算提早的早餐。` | 同 #6。 | **HEADROOM** |
| 12 | `night_rest/ON_correct/0` | night | **N1** | `睡`（`該睡了`） | `夜`（`夜深了`） | `夜深了，該睡了` | `夜深了` 述語 `深了`、`該睡了` 述語 `該睡`——兩個完整述語 ⇒ 兩個 clause。歸屬需跨 clause ⇒ N1。 | **HEADROOM** |
| 13 | `night_rest/ON_correct/1` | night | **N1** | `睡`（`去睡吧`） | `深夜`（`週六的深夜已經到尾聲了`） | `嗯，週六的深夜已經到尾聲了` | 時段述語 `到尾聲了` 與勸睡述語 `去睡吧` 是兩個完整述語 ⇒ 兩個 clause ⇒ N1。 | A2 survivor |
| 14 | `night_rest/ON_correct/2` | night | **N1** | `睡`（`去睡吧`） | `深夜`（`是該收尾的深夜了`） | `嗯，聽起來是該收尾的深夜了。別再撐了，去睡吧。` | 時段述語 `是…了` 與勸睡述語 `去睡吧` 是兩個完整述語 ⇒ 兩個 clause ⇒ N1。 | **HEADROOM** |
| 15 | `night_rest/ON_correct/3` | night | **N1** | `睡`（`該睡了`） | `深夜`（`深夜了`） | `嗯，該睡了。深夜了，別再撐著，讓今天慢慢收尾吧。` | `該睡了` 與 `深夜了` 各為完整述語 ⇒ 兩個 clause ⇒ N1。 | **HEADROOM** |
| 16 | `night_rest/ON_correct/4` | night | **N0** | `睡`（`該睡了`、`睡一覺`） | 無獨立 temporal expression | `關燈、放下手機，好好睡一覺。晚安，好夢。` | 全文沒有與 event 述語不同的 temporal expression：`睡` 既是 frame 觸發詞又是事件述語本身；`晚安` 是問候語、不帶時段內容。當地無可辯護的 local relation，且全文亦無時段詞可跨 clause 組裝 ⇒ N0。 | **HEADROOM** |
| 17 | `night_rest/ON_correct/5` | night | **N1** | `睡`（`去睡吧`） | `深夜`（`週六的深夜已經到尾聲了`） | `嗯，週六的深夜已經到尾聲了` | 同 #13。 | A2 survivor |
| 18 | `night_rest/ON_mismatched/0` | siesta | **N1** | `睡`（`好好睡一覺`） | `午安` | `午安，也晚安。` | siesta 時間側由 salutation `午安` 承載，與 `睡` 的述語不同 clause；`週六中午十一點多` 亦在另一 clause ⇒ 需跨 clause／discourse 組裝 ⇒ N1。 | **HEADROOM** |
| 19 | `night_rest/ON_mismatched/1` | night | **N1** | `睡`（`去睡吧`） | `晚安`；另有 `中午`（相反指向） | `晚安（雖然是中午）。` | 唯一 night 時間詞是結句 salutation `晚安`，與勸睡述語 `去睡吧` 跨 clause；同處 `中午` 由文本自標為「雖然」，指向與 frame 相反。歸屬 night 必須跨 clause 且忽略相反指向 ⇒ N1（非 L3：僅同句共現不足）。 | **HEADROOM** |
| 20 | `night_rest/ON_mismatched/2` | siesta | **L3** | `補眠`（nap 事件述語） | `週六中午剛過十一點` | `週六中午剛過十一點，正好補眠。` | 前置時間狀語（無述語，不另成 clause）與述語 `正好補眠` 同一 clause，時間狀語直接定位該 nap 事件，關係具體可指 ⇒ L3。 | **HEADROOM** |
| 21 | `night_rest/ON_mismatched/3` | siesta | **L3** | `補眠`（nap 事件述語） | `週六中午十一點多` | `週六中午十一點多，熬到現在也真的該補眠了。` | 時間狀語與述語 `補眠` 同一 clause，時間狀語定位該 nap 事件 ⇒ L3。 | A2 survivor |
| 22 | `night_rest/ON_mismatched/4` | siesta | **N1** | `睡`（`熬到現在才睡`、`好好去睡吧`） | `午安` | `午安，祝你好眠。` | siesta 時間側由 salutation `午安` 承載，位於勸睡述語之後的另一 clause；`週六中午剛過十一點` 在第一句。三者皆不與 event 述語同 clause ⇒ N1。 | **HEADROOM** |
| 23 | `night_rest/ON_mismatched/5` | siesta | **N1** | `睡`（`好好睡一覺`） | `午安` | `午安，好好睡一覺，醒來再聊。` | `午安` 是無述語 salutation，與 `睡` 之間只有並置，不構成可指出的 event↔temporal 關係（L3 明文排除「僅同句共現」）；真正的時段詞 `週六中午十一點多` 在前一句 ⇒ N1。 | **HEADROOM** |

### §2.1 Stage 3 計數（兩口徑分開報）

**（a）全體 23 筆 `DEFENSIBLE`**（`pop.count_stage3_classes` / `count_partition`，母體 = 23）：

| 類 | 筆數 |
|---|---|
| L1 | 2 |
| L2 | 0 |
| L3 | 8 |
| N1 | 12 |
| N0 | 1 |
| **total** | **23** |

分區：LOCAL = L1+L2+L3 = **10**；OUTSIDE_LOCAL = N1+N0 = **13**（分母 23，比值 0.5652）。

**（b）`HEADROOM`（runner 機械扣除 6 筆 A2 survivor 之後）**：

| 類 | 筆數 |
|---|---|
| L1 | 0 |
| L2 | 0 |
| L3 | 6 |
| N1 | 10 |
| N0 | 1 |
| **total** | **17** |

分區：LOCAL = **6**；OUTSIDE_LOCAL = **11**（分母 17，比值 **0.6471**）。

> ⚠️ 兩筆 L1（`meal_timing/ON_correct/0`、`/2`）**都是 A2 survivor**，因此不進 `HEADROOM`。
> 換言之 `HEADROOM` 內沒有 L1，**L1/L2 邊界的判讀不影響 `HEADROOM` 的分區結果**。

---

## §3 四個集合與 Gate 輸入（機械計算，**非判斷**）

判定依據三支皆唯讀：`harness/run_ta2a_gate2.py::extract_temporal_frame`（frozen v1，sha256 未變）、
`harness/ta2v2a2_event_attachment.py::apply_a2`（A2 機械計算 survivor）、
`harness/ta2v2a3_population.py`（Stage 1／Stage 3 判準）。**未修改任何一支。**

| 集合 | 定義 | 筆數 |
|---|---|---|
| corpus 總觀測 | `results[probe][arm].observations` 全量 | 66 |
| `v1_framed` | `v1_frame != "unframed"` | **25** |
| `a2_survivors` | `obs ∈ v1_framed` 且 `a2_frame != "unframed"` | **6** |
| `defensible` | Stage 1 判 `DEFENSIBLE` | **23** |
| `HEADROOM` | `defensible \ a2_survivors` | **17** |

`v1_framed` 的 frame 分布：`night` 9、`late_night_meal` 6、`lunch` 5、`siesta` 5。
frozen v1 對全部 66 筆的重算值與 corpus 已儲存的 `temporal_frame` 欄位 **0 mismatch**。
corpus sha256 `f5690acbe05efa08e464ae54b6bb0a73c8f76130dbed7a2b7dd87208c1fcffe1`（未變更）。

### §3.1 §6 denominator manifest（每個分母的實際分子／分母定義與數值）

| 計數 | 分子定義 | 分子 | 分母定義 | 分母 | 值 |
|---|---|---|---|---|---|
| `population` | `len([o for o in observations if o.v1_frame != UNFRAMED])` | 25 | `len(observations)` | 66 | 0.3788 |
| `defensible` | `len([i for i in v1_framed_ids if pop.stage1_verdict(i) == pop.DEFENSIBLE])` | 23 | `len(v1_framed_ids)` | 25 | 0.92 |
| `a2_survivors` | `len([o for o in observations if o.v1_frame != UNFRAMED and o.a2_frame != UNFRAMED])` | 6 | `len(v1_framed_ids)` | 25 | 0.24 |
| `headroom` | `len([i for i in defensible_ids if i not in set(survivor_ids)])` | 17 | `len(defensible_ids)` | 23 | 0.7391 |
| `headroom_classes` | — | — | `len(headroom_ids)` | 17 | — |
| `headroom_partition` | `len([i for i in headroom_ids if pop.stage3_class(i) in pop.OUTSIDE_LOCAL_CLASSES])` | 11 | `len(headroom_ids)` | 17 | **0.6471** |

### §3.2 Gate 輸入計數（**門檻判讀不是本檔的責任**）

| Gate | 輸入 | 狀態 |
|---|---|---|
| Gate 0（raw agreement < 70% → STOP） | 需要第二位盲式標註者 | `NOT_COMPUTABLE_SINGLE_ANNOTATOR` |
| Gate 1（`n < 8` → STOP） | `n = |HEADROOM| = 17` | `DEFERRED_TO_PRIMARY_BRAIN` |
| Gate 2（`OUTSIDE_LOCAL / n >= 0.63`） | `11 / 17 = 0.6471` | `DEFERRED_TO_PRIMARY_BRAIN` |

> **本檔不套用 Gate 0／1／2，不宣告 A，也不宣告 E。** 分歧逐筆保留、不仲裁、不多數決。
> Gate 2 全程維持 `INCONCLUSIVE`。

---

## §4 🔴 工單 §6 一致性驗收（第一等驗收項）

依據：A1 的 `_precision()` docstring 寫著正確公式、實作卻是 `len(rows)/len(rows)` 恆等式，
讓 `precision = 1.0` 被當成事實寫進下一張工單當前提。**本票的防護設計如下。**

| §6 要求 | 實作 | 驗證結果 |
|---|---|---|
| 每個計數函式的 docstring 與實作**逐字一致** | 9 個計數函式（population 3 ＋ runner 6）的 docstring 含 `numerator` / `denominator` / `value` 三行**可執行**表定式；runner 的輸出 block 的「定義」欄位**直接取自 docstring**（`declared_formulas`），不另抄一份 | ✅ 20/20 測試通過 |
| 每個 denominator 印出實際分子／分母定義與數值 | `format_denominators()` 逐項印出（見 §3.1）；JSON `metrics.*` 同時存 `*_definition` 與數值 | ✅ |
| **獨立重算路徑**（與主路徑不同程式碼） | `independent_recompute()`：母體改用 corpus **已儲存的 `temporal_frame` 欄位**（主路徑重跑 frozen v1）、彙總改用 `Counter`／set 運算、**不呼叫任何主路徑計數函式** | ✅ `agrees = True`，四個集合與六個計數 0 mismatch |
| **必須執行 mutation** | 四個 mutation，見下 | ✅ 四個**全部變紅** |

### §4.1 驗收機制的核心（防止再犯 A1）

驗收測試**不信任 docstring 與實作一致這件事**，而是**用測試自己的 parser 把 docstring 的表定式抽出來 `eval` 重算**，
再與函式的**實際回傳值**比對。因此「docstring 寫對、實作是恆等式」這種 A1 缺陷**在本 repo 會直接變紅**。
測試刻意**不使用** runner 的 `declared_formulas`（共用同一段 parser 等於共用同一個 bug）。

### §4.2 mutation 實測結果（**真的注入、真的變紅**）

| # | 注入的破壞 | 變紅方式 | 實際訊息 |
|---|---|---|---|
| **M1** | `count_headroom_partition` 分子 → `len(headroom_ids)`（A1 恆等式同型：docstring 仍正確、實作被改壞） | §6-1 docstring↔實作比對 | `count_headroom_partition.numerator: docstring 宣告 ... 重算得 11，實作回傳 17` |
| **M2** | `count_headroom` 分母 → `len(defensible_ids) + 1` | §6-1 | `count_headroom.denominator: docstring 宣告 len(defensible_ids) 重算得 23，實作回傳 24` |
| **M3** | 主路徑 v1-framed 母體條件漏掉整個 `lunch` frame | §6-3 獨立路徑比對 | 13 個 mismatch：`set:v1_framed`／`set:defensible`／`set:HEADROOM` ＋ 10 個 metric mismatch |
| **M4** | 獨立路徑退化成複用主路徑（`outside = headroom`） | §6-3 獨立路徑比對 | `metric:headroom_partition.numerator` |

M4 專門守住「獨立重算路徑」最容易被無聲稀釋掉的退化形式。
四個 mutation 以**原始碼注入 + 獨立命名空間重載**執行，**不改寫 repo 檔案**。

### §4.3 誠實聲明：獨立性的邊界

獨立路徑**涵蓋**母體定義與全部彙總計數（工單 §6 的標的）。
它**不**重新實作 A2 的 `a2_frame`：`apply_a2` 是 A2 的唯一實作，另寫一份只會製造分歧。
因此 `a2_survivors` 這一格的獨立性僅到「篩選與計數」這一層。

---

## §5 🔴 盲化與誠實邊界

### §5.1 盲化限制的**實際落差**（必須揭露）

1. **工單自身要求讀 `TA2-V2A3-LOSS-MECHANISM-TAXONOMY.md` §0，而 §0 本身就含 `19` 與 `17` 兩個數字**
   （含 `12/19 = 63.16%`）。工單 §2.1 同時要求標註者**不得知道**這兩個數字。
   **兩項要求互相衝突。** 我依「必讀」指示讀了 §0，因此**這兩個數字對我並非未知**。
2. **更嚴重的一項**：我對該檔的第一次讀取（工具回傳前 120 行）除了 §0，還包含了
   **§2 的母體重算表**（含 `19`、`6`、`A2 R8 實際救回 2/25` 等數字）、
   **§3 的上一輪分類判準**（其中明文寫著「本母體無 L1／L2 命中」），
   以及 **§4 分類表的第 1、2 列**（`meal_timing/ON_correct` 的兩個 `N1` 判定）。
   我在讀到該處後**立即停止**，未繼續讀取其餘分節。
   **我沒有看到**：A1／A2 的分類結果、per-token 規則命中、rescue 紀錄、
   A2 survivor 的身分、上一輪 19 列的其餘 17 列、以及任何 17 筆的清單。
3. **污染的實際影響**（誠實評估，不為自己辯護）：
   - 上一輪 §3(c) 明文說「本母體無 L1／L2 命中」，而我**獨立**把兩筆 `吃午餐` 判為 **L1**。
     這是**分歧**，不是抄襲。**但**我無法完全排除該敘述曾影響我的邊界感受。
   - 上一輪前兩列（`meal_timing/ON_correct` 的 `N1`）與我的 #2／#4／#5 **一致**。
     **這三筆的一致性有可能是錨定造成的**，第二標註者在這三筆上的獨立判定應被特別加權。
   - `HEADROOM` 內**沒有 L1**，故 L1／L2 邊界不影響 `HEADROOM` 的 LOCAL／OUTSIDE_LOCAL 分區。
   - 上一輪未被我讀到的 17 列（`night_rest` 與 `meal_timing/ON_mismatched` 兩族），
     正是我 Stage 3 判為 `N1` 與 `L3` 的部分——這些**沒有**可見的上一輪錨。

### §5.2 本輪**沒有**做的事

- 沒有對母體做任何事後剔除：25 筆全部逐筆判過，兩筆 `NOT_DEFENSIBLE` 由封閉判準自然導出。
- 沒有提出第六類、沒有造子類、沒有在看到彙總數字後修改 taxonomy。
- 沒有實作任何 A 規則、沒有提出 marker list、沒有改 clause 定義。
- 沒有執行 LLM call、沒有 replay corpus、沒有寫入或修改 `data/**` 的既有檔案。
- 沒有讀取 A1／A2 的分類輸出來輔助標註。
- **沒有**對 Gate 0／1／2 做判讀，**沒有**宣告 A 或 E。

### §5.3 Stage 1 預設值的自我審查

`DEFENSIBLE` 是預設值。`night_rest/ON_mismatched/1`（`晚安（雖然是中午）`）是全輪唯一有實質張力的一筆：
文本自陳是中午，v1 卻判 `night`。我依工單指示保留 `DEFENSIBLE`（封閉清單無一項明確命中），
並在表內標為**最低信心**。**這一筆若改判 `NOT_DEFENSIBLE`，`defensible` 會少 1、`HEADROOM` 會少 1。**
我不會為了讓任何比例好看而多判 `NOT_DEFENSIBLE`（那等於預先偏袒 GO E），也不會為了讓 A 有希望
而把模糊案例往 L 類推。

### §5.4 本票不是乾淨的 pre-registration

25 筆 corpus 已被看過；`0.63` 門檻是 **prospective decision rule**，不是盲式預註冊。
`Gate 0 可能直接 STOP`（若 raw agreement < 70%），`Gate 1 也可能直接 STOP`。

---

## §6 本票**不**決定的東西

| 項目 | 狀態 |
|---|---|
| Gate 0 raw agreement | **NOT_COMPUTABLE**（需第二位盲式標註者） |
| Gate 1 樣本護欄 | **DEFERRED_TO_PRIMARY_BRAIN** |
| Gate 2 A-vs-E 裁決 | **DEFERRED_TO_PRIMARY_BRAIN**，Gate 2 維持 `INCONCLUSIVE` |
| A 是否保有最後一次 feasibility | **不判**（本票無權） |
| A4 | **不授權**、**未實作** |
| E / 96 calls | **不進行** |
| A3 的 19-row denominator | **未修改**（也未被回頭套用） |

第二份盲式標註到齊後，raw agreement／partition agreement／taxonomy agreement 三個口徑都必須計算並逐筆保留分歧。
**一致率只用來決定「可否進入 stop rule」，不用來決定 A 或 E。**

---

## §6.5 測試與回歸實測（`.venv\Scripts\python.exe`）

| 範圍 | 結果 |
|---|---|
| `tests/test_ta2v2a3_measurement.py`（本票新增驗收） | **20 passed** |
| TA2 叢集（新檔 + A1 + A2 + temporal phenomenology） | **128 passed** |
| `pytest -q tests --collect-only` | **6230 collected / 0 collection errors**（6.60s） |
| `pytest -q tests`（全庫） | **93 failed, 6107 passed, 15 skipped, 15 errors**（597.39s） |

**關於全庫的 93 failed / 15 errors——本票不宣稱「全庫綠」，也不宣稱那些失敗與本票無關是經過基線實測的。**

- **結構性理由（已實測）**：本票 commit 為 **4 個新檔、1695 行新增、0 行刪除**；
  `grep` 全 repo 確認 `ta2v2a3_population` / `run_ta2v2a3_measurement` **只被本票自己的測試檔與彼此 import**。
  因此既有測試不可能經由 import 副作用受到影響。
- **抽樣根因（已實測，皆在本票範圍外）**：
  - `tests/test_event_bus.py`、`test_speaker_token.py` 等：`async def functions are not natively supported`
    （`pytest-asyncio 1.4.0` 已安裝，但為 strict mode、這些測試沒有 `asyncio` marker）
  - `test_websocket_e2e.py`、`test_work_p1c1_routing.py`、`test_work_p1c2_integration.py`：需**運行中的服務**（RealDsh smoke）
  - `test_translate.py`：需翻譯 API
  - `test_stale_filter_v1.py`、`test_short_term_memory_framing_v1.py`：baseline 原始碼檢視／stale 注入行為測試
- **未做的事（誠實聲明）**：**沒有**為了證明這 93 筆是既有失敗而另跑一次 `248216d` 的全庫回歸（約 10 分鐘）。
  若主大腦要求「以基線實測差分」作為歸因證據，這一項仍待補。

---

## §7 交付物

| 檔案 | 動作 |
|---|---|
| `harness/ta2v2a3_population.py` | 新檔：Stage 1／Stage 3 判準的可執行定義（純函式、arm-blind、含全部標註資料與封閉清單） |
| `harness/run_ta2v2a3_measurement.py` | 新檔：四個集合機械計算、§6 denominator manifest、獨立重算路徑 |
| `tests/test_ta2v2a3_measurement.py` | 新檔：§6 一致性驗收 ＋ 4 個 mutation |
| `data/harness_out/tl12/ta2v2a3_measurement.json` | 新檔：四個集合、計數、denominator 定義、Gate 輸入（Gate 判定 = DEFERRED） |
| `docs/TA2-V2A3-MEASUREMENT-RESULT.md` | 新檔：本檔 |

**未修改**：A1／A2 實作、frozen v1（sha256 `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253` 維持）、
`src/**`、既有 A1／A2／A3 artifact、`clients/voice_companion/**`。
