# P2 — Determinism Contract ＆ Temporal／Long-Running Experiment Audit

> **狀態**：Owner 2026-10-03 授權（Mode: AUDIT → 視 audit 結果最小實作）。
> **已進入 Phase**：1（Audit）✅ → 2（Contract）✅ → 3（歷史結論審查）✅ → 4（最小實作）見 §5。
> **不修改 production code 於 Phase 1-3**；Phase 4 僅針對 audit 找到的 contract gap。

---

## §1 核心問題與答案

> **temperature = 0 是否可以被 Soul OS 視為 determinism guarantee？**

**答案：不可以。** 這不是推測，是 2026-10-03 的實測（TA-2-A／TL-12 Gate 2）：

同 Soul、同一 probe、同一模型、同一 configuration、`temperature=0`，跑 3 次得到 **3 個不同輸出**（10/10 probe×arm 組合，`on_distinct=3`）。

`temperature = 0` 描述的是 **sampling configuration**，**不是** output stability 的保證。

---

## §2 三層區分（Owner 逐字，不得混為一件事）

```
Raw determinism            同條件 → 同 raw output
      ↓
Observation stability      raw text 不同，但經 observation layer 後關鍵 evidence 一致
      ↓
Causal attribution         ON/OFF 的差異可合理歸因於被控制的那個變因
```

**TL-12 真正需要的是第三層（causal attribution）。**

⚠️ **不得**因為 DeepSeek Flash 的 raw output 非 deterministic，就自動判定所有舊 TL 實驗失效。**也不得**因為某實驗的 determinism_verdict=PASS，就認定其結論成立——PASS 只證明第一層，而多數 TL 結論宣稱的是第二或第三層。

---

## §3 四級判定詞彙

| 判定 | 定義 | 充分條件 |
|---|---|---|
| `DETERMINISTIC` | 同一條件 N 次 run 的 raw output 逐字元一致 | raw 一致 |
| `STABLE_OBSERVATION` | raw 不一致，但 observation layer 的結構化 evidence 一致 | raw 不一致 **且** 結構化 evidence 一致 |
| `NON_DETERMINISTIC` | raw 不一致 **且** 結構化 evidence 不一致 | 兩層皆不穩 |
| `INCONCLUSIVE` | 樣本不足以判定（例：control group 無法分離 stochastic variation） | 雜訊地板高於效應量 |

**判定必須在「該實驗的主張所需層級」上做**，不可在較低層級判定後直接推論較高層級成立。

---

## §4 Audit 結果：各實驗盤點

### §4.1 🔴 跨實驗的系統性發現

**(a) `seed: 42` 不是 LLM seed。**

所有實驗記錄都含 `"seed": 42`（TL-1／TL-5／TL-6／TL-7），且 `run_tl*.py` 皆傳 `seed=42`。但該 seed 的**全部流向**是：

- `build_script(seed=seed)` → 產生 fixture 事件劇本
- `seed_soul(...)` → 寫 seeded persona／memory baseline

`make_real_llm_call()` **沒有 seed 參數**（`harness/runner.py` 內 `seed` 只出現在 `build_script` 與 `seed_soul` 的呼叫點）。

⇒ **記錄中的 `seed: 42` 具有誤導性：它讓人以為 LLM sampling 是可控的，實際上不是。** 這是本次 audit 最重要的發現之一。

**(b) 歷史實驗輸出不在 repo。**

`data/harness_out/` 僅有 `tl12/ta2a_temporal_ablation.json`（本日產出）。TL-1／TL-5／TL-6／TL-7 的實際執行結果不在版本控制內（記於 Notion）。⇒ **證據鏈無法從 repo 重建**，這本身就是 reproducibility 缺口。

**(c) 部分實驗的 determinism 是 vacuous（比對常數或 stub）。**

`harness/tl7.py:253`：`self._decision_llm = decision_llm or _StubDecisionLLM(decision="do_nothing")` — **預設是 stub，不是真 LLM**。
`harness/tl6.py`：全腳本 `expect_zero_transmits=True`、`total_transmits = 0` ⇒ determinism 比對的是**恆為 0 的常數**。

⇒ 這兩者的 `determinism PASS` **不構成任何 LLM determinism 證據**。

### §4.2 逐實驗表

| 實驗 | LLM | temperature | seed 傳 LLM | N runs | determinism 實際比對 | 結論是否依賴 | production mutation | 當前有效性 |
|---|---|---|---|---|---|---|---|---|
| **TL-1** | ✅ 真（`make_real_llm_call`） | 0.0 | ❌ 否 | 3 | `decision_parsed`（per checkpoint） | **是** — `change_verdict=INTERPRETATION_DECISION_CHANGED`（Level 2 Growth）宣稱前需 D2 PASS | 0 | **Needs Revalidation** |
| **TL-5** | ✅ 真 | 0.0 | ❌ 否 | 3 | 每 tick 的 decision | 待確認 | 0 | **Needs Revalidation** |
| **TL-6** | ❌ 無 LLM 參數 | — | — | 3 | transmit 數（**恆 0**） | 結論是「零搶話」 | 0 | **Vacuous**（比對常數） |
| **TL-7** | ❌ **預設 stub** | — | — | 3 | stub decision（**恆 do_nothing**） | — | 0 | **Vacuous**（非 LLM） |
| **TL-9/10/11** | ❌ 有 `_Stub*` 類別 | — | — | — | 待個別確認 | — | — | **待確認** |
| **TL-12** | ✅ 真 | 0.0 | ❌ 否 | 3 | `decision_parsed` + echo/inference 三層區分 | **是** — 需要 causal attribution | 0 | **INCONCLUSIVE**（已記錄） |

### §4.3 TL-1 結論依賴性審查（Owner 指定重點）

**TL-1 當時的宣稱**：
> 「D2 determinism PASS（3 runs decision 一致）」＋「`change_verdict = INTERPRETATION_DECISION_CHANGED = Level 2 Growth proven`」

**重新審查**：

TL-1 的 Level-2 結論依賴兩件事：
1. **T0 → T15 → T30 的 interpretation trajectory 確實改變**（這是主張本身）
2. **D2 determinism PASS**（這是主張的可靠性前提）

第 1 項與第 2 項**在方法論上是不同的層級**：
- 若 raw text 有 stochasticity，但 **structured trajectory（`decision_parsed` × 3 checkpoints）** 在多次 run 之間穩定 ⇒ **Level-2 結論仍可能成立**（屬 `STABLE_OBSERVATION`）。
- 若 trajectory 本身不穩 ⇒ 必須降級為 **Needs Revalidation**。

**現有 evidence 不足以判定哪一種**，因為：
- TL-1 的實際執行結果**不在 repo**（§4.1(b)），
- 當時的 model／backend 與現行 `deepseek-v4.1-flash` @ Ollama Cloud **不同**，
- `derive_determinism()` 只回 `PASS`／`BLOCKED`，**從未區分「raw 不穩但 observation 穩」**這種情況。

⇒ **TL-1 不被推翻，也不被保護。判定為 `Needs Revalidation`，需重跑一次並以 §3 四級詞彙重新判讀。** 重跑成本可控（`run_tl1.py` 既有，N=3），且**不需要等真實時間**（`SimulationClock`）。

---

## §5 Phase 4：最小實作（僅針對 audit 找到的 contract gap）

**找到的實際 gap**：`derive_determinism()` 只回 `PASS`／`BLOCKED`，**無法表達 `STABLE_OBSERVATION`**。這直接造成 TL-12 的 INCONCLUSIVE——raw 不穩被當成全面失敗，但 observation 層是否穩定**從未被檢查**。

**最小修正**（不建立第二套 framework，沿用 `observer.py` 既有元件）：
- `observer.py` 新增 `classify_experiment_determinism()`：同時回報 **raw 層**與 **observation 層**的一致性，並依 §3 給出四級判定。
- `derive_determinism()` **行為逐字元不變**（既有呼叫者不受影響）。
- 實驗記錄的 `seed` 欄位**加註語意**（fixture seed，非 LLM seed），不改值、不刪欄位。

**明確不做**：不換模型、不改 sampling、不建立第二套 reproducibility 框架、不重跑全部歷史實驗。

---

## §6 誠實邊界

- 本 audit **只盤點 repo 內可查證的部分**。TL-1／TL-5／TL-6／TL-7 的**實際執行結果不在 repo**，故其「當時觀察到什麼」只能從 Notion 記錄與程式碼推斷，**不能從 repo 重建**。
- 「Needs Revalidation」是**狀態描述**，不是「結論錯誤」。
- 本 audit **未**推翻任何歷史結論，也**未**保護任何歷史結論。

---

## §7 TL-1 Revalidation 結果（2026-10-03 15:05）

執行器：`harness/run_tl1_revalidation.py`（additive，未修改既有 `run_tl1.py`）。
輸出：`data/harness_out/tl1/tl1_revalidation.json`（derived 邊界）。
參數：`deepseek-v4.1-flash` ／ `temperature=0.0` ／ `n_runs=3` ／ `pipeline 5c0e954`。

### 7.1 三層結果

| 層 | 結果 |
|---|---|
| ① Raw determinism | **`NON_DETERMINISTIC`** |
| ② Observation stability | **`STABLE_OBSERVATION`** |
| ③ Causal attribution | **`NOT_ASSESSED`**（TL-1 無 counterfactual 設計） |
| legacy `determinism_verdict` | `PASS` |

### 7.2 🔴 最終判定：**`STABLE_OBSERVATION`**

**依契約 §2 核心原則，判定必須在「該實驗的主張所需層級」上做**：
TL-1 的**主張**＝「Level-2 Growth proven」＝ `change_verdict == INTERPRETATION_DECISION_CHANGED`。
**主張層在 3/3 run 一致**（且見 §7.4 跨兩批共 6/6 一致）⇒ 主張可重現。

### 7.3 附加發現（**不影響**主張判定，但比 Notion 原描述更細緻）

**trajectory 形狀跨 run 不唯一**（stance／concern／attribution 三元組逐字元不一致）：

```
run0: T0:concerned|external → T15:concerned|external → T30:concerned|uncertain
run1: T0:concerned|uncertain → T15:concerned|external → T30:concerned|external
run2: T0:concerned|uncertain → T15:concerned|external → T30:concerned|external
```

變動集中於 **T0 的 attribution**（external↔uncertain）與 **T30 的 attribution**。
**stance 與 concern 在三個 checkpoint 皆穩定為 `concerned` / `alex`。**

⇒ **結論可重現，但演變路徑的細節不唯一。** 這**不推翻** Notion 原本的
「擔心 → 自我懷疑 → 接受」描述，**而是把它從「唯一路徑」修正為「主張可重現、
路徑非唯一解」**。

### 7.4 跨兩批共 6 次 run 的主張層一致性

| 批次 | change_verdict |
|---|---|
| 首跑（run_tl1_revalidation 首版） | 3/3 `INTERPRETATION_DECISION_CHANGED/L2` |
| 修正後重跑 | 3/3 `INTERPRETATION_DECISION_CHANGED/L2` |
| **合計** | **6/6 一致** |

### 7.5 🔴 過程中修正的兩個自身錯誤（記錄以防重蹈）

**(a) mutation 快照傳錯路徑 → 假陽性。**
首跑報 `production mutation: FAIL`，diff 為 `data/heartbeats/telegram_channel.json` 與
`data/state/event_loop_alive.json` ——**兩者皆為活服務心跳檔**。根因：呼叫
`snapshot_data_root_hashes(REPO)` 而非 `snapshot_data_root_hashes(REPO/"data")`，
使 `_is_mutation_skipped` 的相對路徑全部失配（`data/heartbeats/*` vs 預期 `heartbeats/*`）。
實測：傳 `REPO` 得 **29,284** 檔（誤判），傳 `REPO/"data"` 得 **236** 檔（正確）。
**harness 既有的 mutation 檢查一直是對的；錯在呼叫端。**

**(b) 判定邏輯違反契約自身原則。**
首版要求 `trajectory_stable` 為必要條件，但 trajectory 三元組**比 TL-1 的主張更嚴**。
**拿比主張更嚴的條件否決一個成立的主張，等於違背 §2。** 已改為以 `claim_stable`
（change_verdict 一致）為主張層判準，trajectory 降級為**獨立記錄的附加發現**。

---

## §8 ✅ Owner 驗收措辭（2026-10-03 15:51，逐字定案）

> **TL-1 Revalidation — ACCEPTED / `STABLE_OBSERVATION`**
> **無 P0 / P1。** 有一個已正確記錄的 **P2 evidence limitation**：attribution
> trajectory non-unique，但它**不阻塞** TL-1 的既定 claim。

**逐字採用的對外描述**：

> **TL-1's Level-2 Growth claim is reproducible at the observation layer across three independent real-LLM runs. Raw expression is non-deterministic, and detailed attribution trajectories are not unique. Causal attribution is not assessed because TL-1 has no counterfactual design.**

**🔴 明確不得採用的描述**：❌ `DETERMINISTIC`／❌ `FULLY PROVEN`／❌ `CAUSALLY PROVEN`

**Notion 既有描述的降級**：若把「擔心 → 自我懷疑 → 接受」寫成一條**唯一、固定的
temporal trajectory**，該描述應降級為：

> **observable growth claim is reproducible, while the detailed attribution trajectory is not unique.**

**這不是否定 TL-1，而是把 claim 精準化。**

**逐維度穩定性（實測 3 run）**：

| 維度 | 狀態 | 說明 |
|---|---|---|
| `stance` | **stable** | 三個 checkpoint 全部 `concerned` |
| `concern` | **stable** | 三個 checkpoint 全部 `alex` |
| `attribution` | **unstable** | T0／T30 在 `external`↔`uncertain` 間波動 |

### §8.1 本次驗收的方法論價值

**沒有「修掉不穩定」，而是把「什麼叫穩定」定義正確了。**

舊思維鏈：`LLM output 不完全相同 → determinism FAIL → experiment invalid`

新思維鏈：`Raw expression → 可能變動 → Observation layer → 主張需要的 observable state → 是否穩定？`

**核心表述（本次最有價值的方法論結果）**：
> **expression may vary; observable lived-state trajectory must be evidentially stable.**

**與 TA-2 的直接一致性**：不能要求 LLM 每個 token／wording deterministic，
**必須定義 experiment claim 所需的 evidence layer**。

---

## §9 下一主線：TA-2 Gate 2（corrected behavioral validation）

**TL-1 Revalidation → CLOSED。** 依 dependency graph，下一張是 **TA-2 Gate 2**。

**TA-2 比 TL-1 多一個關鍵 requirement**：

| | TL-1 | **TA-2 Gate 2** |
|---|---|---|
| 需要 | stability（重複執行一致） | stability ＋ **counterfactual attribution** |
| 設計 | 同 probe 重跑 N 次 | **ON vs OFF** 的差異須可歸因於 temporal context |

**TA-2 不能只做 TL-1 那種 repeated-run stability。** 必須保留 counterfactual 設計，
且採**更強的四臂版本**：

```
OFF              無時間 context（對照基準）
ON-correct       與情境相符的時間（例：早上 ＋「去吃飯」）
ON-mismatched    與情境矛盾的時間（例：凌晨 3 點 ＋「吃晚飯」）
irrelevant       時間存在但 stimulus 與時間無關（自然變動雜訊地板）
```

**為什麼 `ON-correct` vs `ON-mismatched` 是關鍵**：單純 ON vs OFF 的差異，可能只是
「時間被提到了」這種表面效應（echo）。而**正確 vs 矛盾的時間必須產生不同結果**，
才證明**時間的「值」本身參與了推理**，而不只是時間的存在被複述。

這與 §8.1 的 echo/inference 區分**直接銜接**：我們已經定義了「複述注入行裡
已有的詞」不算推理證據。`ON-mismatched` 是這個區分的**行為層版本**。
