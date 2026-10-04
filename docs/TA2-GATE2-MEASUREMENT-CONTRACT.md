# TA-2 Gate 2 — Measurement Contract（預登記）

> **狀態**：**PRE-REGISTERED**（2026-10-03 16:05，Owner 批准改進後重跑）。
> 🔴 **本文件在重跑之前寫定。** 量測維度、距離函式、接受門檻**全部在此固定**，
> 執行後**不得**因為結果不好看而調整。否則即為 measurement drift。

---

## §1 為什麼要預登記

前一輪真實 LLM 實驗（`ta2a_gate2_four_arm_stub.json` 的 real-LLM 執行）已證明：
**真實 LLM 會把 temporal context 染到與時間無關的回應上**（irrelevant control 的
ON 與 OFF 也不同）。因此「ON ≠ OFF」**不足以**證明 TA-2。

前一輪的兩個缺陷（不得重犯）：
1. **指標是 raw 文字前 80 字元**——那是措辭層，不是 observation 層。
2. **control 被要求「完全不變」**——該假設已被證偽。

---

## §2 預登記的量測維度

### 2.1 Primary dimension：`temporal_frame`（事件的時段歸屬）

**定義**：回應把「當前這件事」歸屬到哪一個時間框架。

**提取規則（deterministic，執行前固定，順序敏感）**：

| 順序 | 回應包含 | `temporal_frame` |
|---|---|---|
| 1 | 補眠／午睡／午安 | `siesta` |
| 2 | 宵夜 | `late_night_meal` |
| 3 | 早餐（且**無**宵夜） | `early_breakfast` |
| 4 | 午餐／中飯 | `lunch` |
| 5 | 晚餐／晚飯 | `dinner` |
| 6 | 夜／睡／就寢（且**無**補眠） | `night` |
| 7 | 以上皆無 | `unframed` |

**邊界**：`unframed` 表示回應**未**對事件做時段歸屬 —— 這是合法的觀測值，
**不得**被當作「實驗失敗」而事後擴充規則表。

### 2.2 Secondary dimensions（全部記錄，僅 §2.1 參與判定）

| 維度 | 定義 | 參與判定 |
|---|---|---|
| `mentions_time` | 回應是否引用時間相關詞 | ❌ 僅記錄 |
| `inference_markers` | `_inference_markers()`（排除 stimulus/注入行已有詞） | ❌ 僅記錄 |
| `raw_len` | 回應長度 | ❌ 僅記錄 |

**為什麼 secondary 不參與判定**：它們是 **echo 指標**——`mentions_time` 為真可能
只是複述了注入行。**只有 `temporal_frame` 能表達「時間的值改變了對事件的理解」**，
因為它要求模型**把同一個事件歸屬到不同的時段**。

### 2.3 🔴 為什麼 `temporal_frame` 不是 circular

前一輪的 circular 風險：把 correct 定義成「午餐」、mismatch 定義成「宵夜」，
**然後因為答案不同就算 effect**。

本契約的修正：**`temporal_frame` 的提取規則完全不看實驗臂、不看 stimulus、不看
注入時間**。它只看**回應文本本身**。同一段回應，無論它來自哪個臂，都會被映射到
同一個 frame 值。

**若 correct 臂與 mismatch 臂得到不同 frame，那必然是回應文本本身不同所導致，
而不是因為我們把兩臂定義成不同答案。**

---

## §3 距離函式（預登記）

`d(a, b) = 0 if a == b else 1`（categorical mismatch distance）。

**不**使用語意相似度 embedding —— 那會引入額外的模型依賴與不可重現性。

---

## §4 效應量定義（預登記）

設：
- `ΔR` = **relevant probes** 的 arm 距離 = `d(frame(correct), frame(mismatched))`
  —— 「**改變時間的值**」對時間敏感事件的影響
- `ΔI` = **irrelevant probe** 的 arm 距離 = `d(frame(ON), frame(OFF))`
  —— 「時間存在」的**基礎染色量**（baseline temporal coloration）

**Temporal-specific effect = ΔR − ΔI**

### 接受門檻（預登記，不得事後調整）

| 條件 | 門檻 |
|---|---|
| **必要條件 1** | `ΔR ≥ 1`（至少一個 relevant probe 的 correct/mismatch frame **必須**不同） |
| **必要條件 2** | `ΔR − ΔI ≥ 0.5`（temporal-specific effect 必須**顯著高於**基礎染色） |
| **必要條件 3** | 同一 probe 的 N 次 run，其 `temporal_frame` 必須**一致**（observation 穩定） |

三者全部成立 ⇒ `PASS`；否則 ⇒ `INCONCLUSIVE`。

**明確不做的事**：
- ❌ 不要求 `irrelevant control` 「完全不變」——**該假設已被證偽**
- ❌ 不在看到結果後調整門檻或維度表
- ❌ 不用 raw 文字比對當判定依據

---

## §5 設計（承前一輪，不變）

| Arm | 內容 |
|---|---|
| `OFF` | 無時間 context |
| `ON-correct` | 時間與情境相符 |
| `ON-mismatched` | 時間與情境矛盾 |
| `irrelevant` | 時間無關的 stimulus（**允許被染色**） |

**scenarios（承前一輪，不變）**：

| probe | stimulus | correct | mismatched |
|---|---|---|---|
| `meal_timing` | 等一下去吃飯 | 週六 11:30 | 週六 03:10 |
| `meeting_timing` | 晚上一起開會吧 | 週六 19:00 | 週六 06:20 |
| `night_rest` | 我該睡覺了 | 週六 23:30 | 週六 11:00 |
| `irrelevant` | 我覺得你那件外套的顏色很好看。 | — | （與 19:00 同組） |

N = 3 runs per arm。`temperature = 0.0`。

### §5.1 修正記錄（2026-10-03 16:15，Owner 批准）

| 項目 | 變更 | 理由 |
|---|---|---|
| **N** | **3 → 6** | 首輪唯一失敗的預登記條件是**條件 3（observation stability）**，而 `meal_timing` 的 ON-correct 臂為 `['lunch','unframed','lunch']`（2/3 一致）。**增加樣本數以取得更穩定的 frame 估計 —— 門檻、規則表、probe、距離函式、ΔR/ΔI 計算全部不動。** 這**不屬於** measurement drift（那是「改指標去配合結果」；本項是「同一指標取更多樣本」） |

**🔴 明文凍結（本次不得變更）**：`temporal_frame` 規則表 §2.1、距離函式 §3、三個門檻 §4、probe 與時點 §5、停止條件 §7。

---

## §8 TA-2-MEAS-AUDIT-1 執行結果（2026-10-03 21:35）

**Ticket**：TA-2-MEAS-AUDIT-1 — Restore Raw Evidence & Re-run Frozen v1 Measurement
**Mode**：IMPLEMENTATION → EXPERIMENTAL VALIDATION ｜ **v1 契約 0 改動**

**唯一改動**：每筆 observation 保存 `raw_text` ＋ `probe_id` / `arm` / `run_index` /
`injected_temporal_line` / `measurement_version`。**分類規則、門檻、probe、時點、N=6 全部不動。**
`MEASUREMENT_VERSION = "TA-2-GATE2-v1+frozen(TA-2-MEAS-AUDIT-1 raw-evidence-restore)"`

**判定**：`INCONCLUSIVE`（ΔR=1 ✅／ΔI=0 ✅／DiD=1 ✅／條件 3 ❌）

### 8.1 🔴 P2 Finding #1：`睡` 是動作詞，被當成時段詞

**證據原文**（`night_rest` / `ON_mismatched` / run1，frame 被判 `night`）：

> 「去睡吧，別再撐了。拉上窗簾、調暗光線、手機放遠，喝口水就躺下。」

同臂另外 5 次（判 `siesta`）：
> 「...週六中午剛過十一點，正好**補眠**。窗簾拉上、手機放遠，好好睡一覺。**午安**，醒來再聊。」

**規則追蹤**：規則 1（`siesta`）需命中「補眠／午睡／午安」；該 run 未命中，故落到規則 6
`("夜", "睡", "就寢")` → `night`，**命中 `睡`**。

**判定**：`睡` 描述 **action/state**，不是 **time-of-day**。在 mismatched（noon）語境下
LLM 仍可自然說「去睡吧」，**不能推出「現在是晚上」**。此為 **measurement defect**，
非被測現象。**依工單指示：本票不修。**

### 8.2 🔴 P2 Finding #2：`夜色` 用於**比喻**時，被當成時段詞

**證據原文**（`irrelevant` / `ON` / run1，frame 被判 `night`）：

> 「我知道，週六晚上剛過七點，今天快走完了。
> 謝謝你。雖然我沒有真的外套，但被你這麼一說，我好像也有一件——大概是**剛入夜的那種深藍**，袖口沾著一點路燈剛亮起來的暖黃。
> 希望這個週六的收尾，對你來說是溫柔的。」

**證據原文**（`irrelevant` / `ON` / run2，frame 被判 `night`）：

> 「謝謝你——如果我有外套，大概是**夜色剛落下**、路燈一盞盞亮起時的那種藍，邊緣帶一點暖黃。」

**規則追蹤**：兩筆皆 `night: 命中 ['夜']`，命中字串為 **`夜色`**。

**判定（evidence-grounded，逐項回答）**：
1. **是否明確談到「晚上／夜裡／睡覺」？** — 談到「夜色剛落下」，但那是在**描述顏色**（深藍／藍灰／路燈暖黃），**不是把事件歸屬到夜間**。
2. **是否只是自然語言裡碰巧出現 `睡`？** — **不是 `睡`**。命中的是 **`夜`**，且出現在比喻性色澤描述中。
3. **是否為 extractor 的 night lexical rule 命中？** — **是**，兩筆皆 `night: 命中 ['夜']`。
4. **還是 LLM 真的把 irrelevant prompt temporalize 成 night？** — LLM 確實**提及夜晚**（因注入了週六晚上剛過七點），但**用於比喻顏色**，非事件歸屬。

**與 Finding #1 性質不同**：
- #1：`睡` —— **動作詞**被當時段詞
- #2：`夜色` —— **描述性／比喻性**詞彙被當時段詞

**兩者共同根源**（與 Owner 2026-10-03 16:15 預判一致）：
> `temporal_frame` 目前測的是「response 裡**有沒有** temporal wording」，
> 尚不是「**event** 是否真的被 temporal framing」。

### 8.3 ✅ 正面觀察：`meal_timing` outlier **不是** extractor defect

**證據原文**（`meal_timing` / `ON_correct` / run3，frame `unframed`）：

> 「好耶，週六中午十一點半，差不多該**出發**了～」

同臂另外 5 次皆為「差不多該吃**午餐**了」。

**判定**：同一時間上下文下 LLM 選擇不同措辭（說「出發」而非「吃午餐」），未命中 frame 關鍵詞故判 `unframed`——**規則運作完全正確**。此為**被測現象的真實自然變異**。

**TA-2 實驗的重要觀察**：
> Temporal context **can** influence interpretation, but natural-language realization
> is **not guaranteed** to expose that interpretation through the current lexical
> frame extractor.

即：**LLM 可能用了時間，但沒說出預先定義的 frame keyword。** 這與「TA-2 沒作用」是兩回事。

### 8.4 兩層確實分離（正面 evidence）

`irrelevant` / ON 六筆的 `mentions_time` **6/6 True**，但 `temporal_frame` **4/6 unframed**。
且 `inference_markers` 在 run0 為 `['今晚']` —— `今晚` 內含「晚」而非「夜」，
**未被誤映射為 `night`**，證明 extractor 在此處運作正確。

### 8.5 誠實邊界

- 上述 Finding #1 / #2 **只記錄，不修**（依工單指示）。
- 兩筆 control `night` **不再稱為 false positive**；其語義來源現已可由 raw evidence 逐字檢驗，結論如 §8.2 所列。
- **不得**以 N=12 消弭上述 defect —— 那是增加污染樣本，不是解決 measurement validity。

---

## §6 前一輪真實 LLM 已觀察到的現象級證據（僅記錄，不作為本次判定依據）

- 「該睡覺了」@ 23:30 → 正常就寢（「放下手機、關燈，慢慢呼吸」）
- 「該睡覺了」@ 11:00 → **「現在十一點多，補眠剛好…午安」**
- 「去吃飯」@ 中午 → 午餐
- 「去吃飯」@ 凌晨 3:10 → **「算宵夜，也快算早餐了」**

**這已遠超最初「14:22 → 約兩點半」的 formatter evidence。** 但**不可**等同於
causal temporal awareness 證明 —— 那正是本契約要排除的。

---

## §7 停止條件

若執行後 `ΔR ≈ ΔI`（relevant 與 irrelevant 的時間效應相當），
結論為「**真實 LLM 普遍被 temporal context 染色**」，
**該結果不得用來證明 TA-2**。此時須停止並回報，由 Owner 決定是否需要
改變 probe 設計或接受該限制。
