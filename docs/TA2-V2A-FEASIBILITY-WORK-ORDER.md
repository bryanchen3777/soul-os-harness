# 工單：TA-2 v2-A — 語法依附 feasibility prototype / measurement audit

> **類型**：**measurement feasibility spike / design validation**。**不是** TA-2 v2 implementation。
> **授權依據**：Owner 2026-10-03 21:52 Architecture Review（`docs/TA2-GATE2-MEASUREMENT-CONTRACT-V2-DESIGN.md` §9）
> **上游**：TA-2-MEAS-AUDIT-1 CLOSED（`d859792`）。v1 契約**凍結**。
> **Gate 2 狀態**：本票**不得**改變 Gate 2 判定。Gate 2 仍為 **INCONCLUSIVE**。

---

## 目標

回答**一個問題**（Owner 逐字）：

> 語法依附是否能比 v1 lexical matching 更接近「**事件本身**被放進 temporal frame」？

做法：把候選 A 實作為**疊在 frozen v1 規則表之上的 veto 濾層**，對已存在的 N=6 四臂 evidence corpus 做**離線重播**，量出它的 precision 頭寸與 non-triviality。

---

## 為什麼是「veto 濾層」而不是新分類器（設計決策，已拍板）

Owner 明文禁止「新 temporal classifier」。因此候選 A **不得**自行偵測時段。

- **A1（本次唯一實作範圍）= precision 濾層**：輸入 v1 `extract_temporal_frame` 的候選命中，套用依附規則**否決（veto）**不成立者。
- **A2（rescue / recall）= 明確 out of scope**：讓 A 自己偵測 v1 漏掉的 frame，等同新分類器，違反 Owner 限制。**本次不做，記為已知限制。**

因此 A1 **只可能降低 recall，不可能提高 recall**。這是刻意的，報告必須如實寫出。

---

## 範圍（要動的檔案）

| 檔案 | 動作 |  Ownership |
|---|---|---|
| `docs/TA2-V2A-FEASIBILITY-CONTRACT.md` | **新檔**：預登記契約（規則表、門檻、停止條件） | 執行者 |
| `harness/ta2v2a_attachment.py` | **新檔**：A1 veto 規則（arm-blind 純函式） | 執行者 |
| `harness/run_ta2v2a_feasibility.py` | **新檔**：離線重播（0 LLM 呼叫） | 執行者 |
| `tests/test_ta2v2a_attachment.py` | **新檔**：規則案例表測試 | 執行者 |
| `data/harness_out/tl12/ta2v2a_feasibility.json` | **新檔**：量化輸出（derived，非 canonical） | 執行者 |
| `data/harness_out/tl12/ta2v2a_audit.md` | **新檔**：逐筆 audit 表 ＋ veto ledger | 執行者 |

**其餘一律不動**（見 Out of Scope）。

---

## 做法（決策已定，執行者照做）

### §1 凍結基線的取得（先做）

```powershell
cd C:\Users\bbfcc\.local\bin\soul-os-harness
git rev-parse HEAD
.venv\Scripts\python.exe -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('data/harness_out/tl12/ta2a_gate2_four_arm_real.json').read_bytes()).hexdigest())"
.venv\Scripts\python.exe -c "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('harness/run_ta2a_gate2.py').read_bytes()).hexdigest())"
```

把兩個 sha256 寫進契約 §7，並在重播結束後**重算比對**（AC1）。

corpus 結構（已由主大腦核實）：

- `results` 下 4 個 probe：`meal_timing` / `meeting_timing` / `night_rest` / `_control`
- 前三者各 3 臂（`OFF` / `ON_correct` / `ON_mismatched`），`_control` 2 臂（`ON` / `OFF`）
- 每臂 dict 含 `observations`（list，N=6），每筆含 `raw_text`、`probe_id`、`arm`、`run_index`、`injected_temporal_line`、`temporal_frames`
- **合計 66 筆 observations**（54 ＋ 12）；本票**不重跑、不改 N**。

### §2 寫預登記契約（`docs/TA2-V2A-FEASIBILITY-CONTRACT.md`）

**必須在任何 corpus 內容檢視之前完成。**契約至少含：

1. §1 目標與範圍（含 A2 為何不做）
2. §2 依附規則表（下節逐字搬入）
3. §3 判定輸出三元：`vetoed` / `kept` / **`undecided`**
4. §4 量化指標定義（§3）
5. §5 驗收條件（AC1–AC9）
6. §6 停止條件
7. §7 凍結基線的兩個 sha256 ＋ git HEAD
8. §8 誠實邊界

### §3 A1 veto 規則表（**逐字實作，不得增刪**）

對 v1 規則表中每一組候選命中，先做 token 層判斷，再做分句層判斷。**順序敏感。**

**允許的最小分句手段**：以 `。！？；：，、` 切分小句。**這不是 parser、不是 dependency framework**，不引入任何第三方套件。任何超出此手段的需求 → 記 `undecided`，**不得自行加規則**。

| rule_id | 條件 | 動作 |
|---|---|---|
| `R1_ACTION_TOKEN` | 命中 token 屬 `ACTION_TOKENS`（見下） | `vetoed` |
| `R2_EXCLUDE_TOKEN` | v1 排除詞命中（`_EXCLUDE_FOR`，`harness/run_ta2a_gate2.py:226-229`） | `vetoed`（v1 本已續行，此處記錄 ledger 供 audit） |
| `R3_ATTRIBUTIVE_NOUN` | 命中 token 在小句內**直接修飾外觀類名詞**（窗口 ≤3 字，允許 `剛`/`的` 插入） | `vetoed` |
| `R4_SIMILE_FRAME` | 命中所在小句含 `SIMILE_MARKERS` | `vetoed` |
| `R5_NEGATION_FRAME` | 命中所在小句含 `NEGATION_MARKERS` | `vetoed` |
| `R6_NO_ATTACHMENT` | 以上皆不觸發，且命中 token 在其小句內**找不到任何依附標記** | `undecided` |
| `R7_ATTACHED` | 以上皆不觸發，且命中 token 在其小句內找到**依附標記** | `kept` |

**封閉詞表（逐字，不許自行增補）**：

```
# 純動作／狀態詞：作為動詞或身體狀態，**不是** time-of-day
ACTION_TOKENS       = {睡, 睡覺, 入睡, 就寢}

# 複合時段名詞：本身即含時段意義，**不**列入 ACTION_TOKENS
# （否則會誤殺整個 siesta 幀——主大腦自查修正，見下方說明）
COMPOUND_TIME_NOUNS = {午睡, 補眠, 宵夜, 早餐, 午餐, 晚餐, 晚飯, 中飯, 午安}

COLOR_APPEARANCE_NOUNS = {藍, 色, 顏色, 色澤, 天, 光, 夜色}
SIMILE_MARKERS      = {好像, 像是, 彷彿, 好似, 大概, 像是那種, 那種}
NEGATION_MARKERS    = {不是, 並非, 並不, 不在}
ATTACHMENT_MARKERS  = {就, 吧, 改, 換, 挪, 推, 順延, 延到, 到, 這, 那, 現在, 待會, 剛才, 這次}
```

> **主大腦自查修正（必須遵守）**：`午睡`／`補眠` **不是**動作詞。它們是 v1 `siesta` 幀的合法觸發詞（`harness/run_ta2a_gate2.py:217`），若列入 `ACTION_TOKENS`，A1 會把整個 siesta 幀系統性歸零——那不是修正污染，是**製造**污染。故明確切為 `COMPOUND_TIME_NOUNS`，`R1` **不得**作用於它們。
>
> 這一條是本工單的已知誘發點：owner 引用的污染原文是「去睡吧」與「夜色剛落下」，**兩者都不含 `補眠`／`午睡`**。執行者不得因為看到 `siesta` 幀消失就擴大 `ACTION_TOKENS`——那正是 measurement drift。

**規則衝突的處理（預登記，不得臨場判斷）**：

1. `R1` 最高優先，命中即 `vetoed`，不再看其他規則。
2. `R3` 與 `R4` 皆成立時，以 `R3` 為 ledger 主記錄（`R3` 較具體），但 **兩者都寫進 ledger**。
3. `R2` 與其他規則同時成立時，全部寫入 ledger，`vetoed` 結果不變。（註：如上，`R2` 目前不可達）
4. **任何詞表命中但小句無法切分**（例如全文無標點）→ `undecided`。
5. **不得**為了讓某筆結果好看而調整規則表、詞表或窗口大小。
6. **不得**因為「某幀全數消失」而擴大 `ACTION_TOKENS`——那會把真實幀殺成 `unframed`，屬 measurement drift。照實回報。

### §4 A1 的輸出語意

對每筆 observation：

- 輸入：v1 `extract_temporal_frame(raw_text)` 的結果 + raw_text
- 逐字套用 §3 規則表
- 輸出三元之一：`kept`（保留 v1 frame）／`vetoed`（退回 `unframed`）／`undecided`
- **A1 的 frame 定義**：`kept` → v1 frame；`vetoed` 與 `undecided` → `unframed`

> ⚠️ 這是**刻意的保守選擇**：`undecided` 不算 `kept`。理由：無法判定時若算命中，A1 的 precision 指標會被不可解釋的樣本稀釋。報告必須揭露 `undecided` 的全部原文。

### §5 量化指標（全部用 **frozen v1 公式**，不得重寫）

- **precision on 已知污染集**：`睡` 污染（`night_rest` 臂的 `睡`／`就寢` 命中）與 `夜` 比喻污染，v2 須判為 `vetoed`
- **noise proxy**：`|v1_frame ≠ v2_frame|` 的筆數佔非 `unframed` v1 筆數的比例
- **non-triviality（預登記門檻，硬地板）**：`v2_frame ≠ unframed` 的筆數 **> 0**
  - 若為 **0** ⇒ A1 雖通過精度檢查卻是**無用濾層**，判定為 **假通過（false pass）**，必須在報告中明寫
  - 輔助觀察（**僅記錄，不設門檻**）：3 個 probe 中有幾個的 `ON_correct` 臂仍保有 frame、`night_rest` 的 `siesta` 幀是否存活
- **recall 不可提升的量化後果**：`noise proxy` 的分母（v1 非 `unframed` 筆數）與 v2 非 `unframed` 筆數之差，即 A1 丟棄的真陽性上限。報告須寫出此數字
- **ΔR / ΔI / DiD**：以 v2 frame 重算，**直接沿用** `harness/run_ta2a_gate2.py` 的既有函式（import，不得複製改寫）
- **condition 3（observation stability）**：以 v2 frame 逐 probe 重算，逐字沿用 v1 契約的三門檻與順序

**這些是 feasibility 指標，不是 Gate 2 判定。輸出必須標記 `feasibility_only: true`。**

### §6 逐筆 audit 表（`ta2v2a_audit.md`）

每筆 observation 一列，至少含：

| 欄位 | 說明 |
|---|---|
| `obs_id` | probe_id + arm + run_index |
| `v1_frame` / `v2_frame` | 對照 |
| `A1_verdict` | `kept` / `vetoed` / `undecided` |
| `rule_id` | 觸發的規則（多值） |
| `span` | 命中 token 與小句摘錄（≤40 字） |
| `raw_text` | **完整原文**（不得截斷） |

另需兩份彙總表：**veto ledger**（依 rule_id 統計）與 **undecided 清單**（全部原文）。

---

## 驗收（完成的定義）

- **AC1** 凍結基線：重播前後 `run_ta2a_gate2.py` 與 corpus 兩檔 sha256 **一致**；契約 §7 三項全對得上
- **AC2** `git status` 確認 `run_ta2a_gate2.py`、`harness/tl12_temporal.py`、`harness/observer.py`、v1 契約文件**皆無改動**
- **AC3** 0 次 LLM 呼叫：重播腳本不得 import／呼叫 `llm_call`、不得開 network；輸出標記 `llm_calls: 0`
- **AC4** arm-blindness：重播過程**不得**讀取 `arm`／`probe_id`／`injected_temporal_line`；audit 產出中這三欄只可由**獨立的 audit 步驟**寫入。需在報告中說明如何隔離
- **AC5** 規則表完整性：`harness/ta2v2a_attachment.py` 的詞表與 `docs/TA2-V2A-FEASIBILITY-CONTRACT.md` §3 **逐字一致**（可測試驗證）
- **AC6** `tests/test_ta2v2a_attachment.py` 覆蓋 §3 每條 rule_id 至少一例 **正例與一例反例**；`harness/ta2v2a_attachment.py` 的測試 **≥ 8 passed**
- **AC7** audit 表涵蓋 **全部 66 筆**，`raw_text` 無截斷
- **AC8** 輸出含 `feasibility_only: true`，且**不含**任何 Gate 2 PASS/FAIL 判定
- **AC9** 誠實報告：必須明確聲明 A1 **不能提高 recall**（只能 veto），並列出 A2 為何不做

---

## 測試

**要寫**：`tests/test_ta2v2a_attachment.py`（規則案例表，純函式，**不得呼叫 LLM**、不得連 `:8000`）。

案例表至少含（依 §3 規則表）：

| case | 輸入 | 預期 |
|---|---|---|
| R1 正例 | 「去睡吧，別再撐了。」 | `vetoed` |
| R3 正例 | 「大概是夜色剛落下…那種藍」 | `vetoed` |
| R3 反例 | 「那就中午吧」 | `kept` 或 `undecided`（**依規則實算，不得硬寫**） |
| R5 正例 | 「不是晚上，應該是中午才對」 | `vetoed` |
| R6 正例 | 命中詞孤立、無任何 marker | `undecided` |
| R1 反例 | 「午睡一下比較好」 | **不得** `vetoed`（`午睡` 是 `COMPOUND_TIME_NOUNS`） |

> **`R2` 的誠實說明（主大腦自查）**：`R2_EXCLUDE_TOKEN` 在 v1 規則表順序下**實際不可達**——`_FRAME_RULES` 先比 `siesta`（含 `補眠`）與 `late_night_meal`（含 `宵夜`），所以帶排除詞的文本永遠不會走到 `night`／`early_breakfast`。`R2` 因此是**防禦性規則**，作用只在 ledger 記錄，**不得**用它反推任何結論，也**不得**為了讓它「有作用」而調整 v1 規則順序（v1 凍結）。

> **反例不得為了讓測試通過而放寬規則。**若某反例在規則表下算出來是 `vetoed`，就寫 `vetoed`，並在報告中記為**規則表的已知缺陷**（記錄不修）。

**要跑**：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_ta2v2a_attachment.py
.venv\Scripts\python.exe -m pytest -q harness -p no:cacheprovider
```

回報時**必須同時聲明解釋器為 `.venv\Scripts\python.exe`**（全域 pytest 數字不同，禁止混用）。

---

## Out of Scope（明確不做）

- ❌ **不得**修改 `harness/run_ta2a_gate2.py` 任何一行（含規則表、ΔR/ΔI/DiD、門檻）
- ❌ **不得**修改 `harness/tl12_temporal.py`、`harness/clock.py`、`harness/observer.py`（含 `derive_determinism`）
- ❌ **不得**修改 `docs/TA2-GATE2-MEASUREMENT-CONTRACT.md`（v1 契約凍結）
- ❌ **不得**修改任何 `src/**` — **Soul OS runtime 一律不動**
- ❌ **不得**重跑 LLM、**不得**改 N、**不得**升 N=12
- ❌ **不得**實作候選 A2（rescue）、候選 E、候選 C 的任何 arm
- ❌ **不得**引入 semantic parser、LLM judge、dependency framework、**新 temporal classifier**
- ❌ **不得**引入任何第三方套件（僅標準庫）
- ❌ **不得**改動 `data/**` 既有檔案（只可新增 `data/harness_out/tl12/ta2v2a_*`）
- ❌ **不得** commit `clients/voice_companion/**`（Owner 未提交的 VC 線改動）

---

## 🔴 行程與生產紀律（全體適用）

- **不得殺任何行程**（`Stop-Process`／`taskkill`／`pkill` 一律禁止）
- **不得**重啟服務、**不得**碰 `:8000`／`:8765`／`:8766`／`:8767`
- 本票**不需要**任何重啟。若你認為需要，**停下回報**，不要自行執行
- 測試一律用 isolated data_root，**不得**寫入 `data/soul/**`、`data/elevation/**`

---

## Frozen Contract 注意

以下本票**不得**觸碰：

- `harness/run_ta2a_gate2.py` 的 `_FRAME_RULES`（`harness/run_ta2a_gate2.py:215-224`）— v1 時段詞表
- `harness/run_ta2a_gate2.py` 的 `_EXCLUDE_FOR`（`:226-229`）
- `harness/run_ta2a_gate2.py` 的 `extract_temporal_frame`（`:232-243`）— v1 extractor，**只讀取用，不得修改**
- `harness/observer.py` 的 `derive_determinism` — 只允許 additive
- v1 契約的三門檻與 `docs/TA2-GATE2-MEASUREMENT-CONTRACT.md` 全文

---

## 回報格式

1. **改動檔案清單**（新增／修改，逐檔標明行數）
2. **AC1–AC9 逐項狀態**（PASS / FAIL / 未達，附證據）
3. **測試結果**：指令、`.venv` 解釋器聲明、passed / failed 筆數
4. **量化輸出摘要**：precision on 已知污染集、noise proxy、non-triviality、ΔR/ΔI/DiD、condition 3 — **並標明 `feasibility_only`**
5. **規則表的已知缺陷**（記錄不修）
6. **是否踩到 Frozen Contract**：逐項回答，附 `git status` 與 diff 摘要
7. **服務狀態**：`/health` 是否仍為 200、pid 與 StartTime 是否未變（**只觀察不操作**）
8. **commit SHA 與 push 狀態**（只 stage 本工單列出的檔案）

**若你在執行中發現任何與本工單假設不符的事**（例如規則表在實際語料上大量 `undecided`）：**照實回報，不要自行調整規則表或門檻。**
