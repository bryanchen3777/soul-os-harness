# TA-2 v2-A — 語法依附 feasibility 預登記契約

> **類型**：measurement feasibility spike / design validation 的**預登記契約**。
> **不是** TA-2 v2 implementation，**不改** Soul OS runtime，**不改** v1 契約。
> **授權依據**：Owner 2026-10-03 21:52 Architecture Review（`docs/TA2-GATE2-MEASUREMENT-CONTRACT-V2-DESIGN.md` §9）
> **上游工單**：`docs/TA2-V2A-FEASIBILITY-WORK-ORDER.md`
> **上游 v1 票**：TA-2-MEAS-AUDIT-1 CLOSED（`d859792`）。v1 契約**凍結**。

> 🔴 **Gate 2 狀態不變：INCONCLUSIVE。**
> 本契約下的**任何**輸出都**不得**被讀為 Gate 2 的 PASS／FAIL。所有量化欄位一律標記
> `feasibility_only: true`。

> 🔴 **本文件於任何 corpus 內容檢視之前寫定並 commit。**
> 下游 `harness/ta2v2a_attachment.py` 與 `harness/run_ta2v2a_feasibility.py`
> **必須逐字實作**本文件 §2／§3／§4。**看到結果後不得回頭修改本文件的規則表、詞表、
> 窗口大小、指標定義或門檻。** 若規則表在實際語料上失效，**記錄為缺陷、不修**。

---

## §1 目標與範圍

### §1.1 唯一要回答的問題（Owner 逐字）

> 語法依附是否能比 v1 lexical matching 更接近「**事件本身**被放進 temporal frame」？

### §1.2 範圍

把候選 A 實作為**疊在 frozen v1 規則表之上的 veto 濾層（A1）**，對已存在的 N=6 四臂
evidence corpus（`data/harness_out/tl12/ta2a_gate2_four_arm_real.json`，66 筆 observations）
做**離線重播**。

- **0 次 LLM 呼叫**、**0 次網路**、**不重跑**、**不改 N**、**不升 N=12**。
- 輸入：v1 規則表（**唯讀 import**）＋ 每筆 `raw_text`。
- 輸出：v2 frame 逐筆對照、token 層逐 token 判定、量化 feasibility 指標、逐筆 audit 表。

### §1.3 A2（rescue／recall）**明確不做**

讓 A 自己去偵測 v1 漏掉的 frame，等同**新 temporal classifier**，違反 Owner §9.3 的明文禁止。
故 **A1 結構上只可能降低 recall，不可能提高 recall**。這是刻意的設計限制，
**報告必須如實寫出**（AC9）。

### §1.4 與上游設計的關係

`docs/TA2-GATE2-MEASUREMENT-CONTRACT-V2-DESIGN.md` §2 已證：時間歸屬**只能在臂間對比中觀測**，
且 §9.6 指出 Gate 2 剩下的是 **measurement validity problem**。本票因此**只量 instrument 的
性質**（precision 頭寸、noise proxy、non-triviality、recall 損失），**不判定 Soul 的能力**。

---

## §2 依附規則表（工單 §3 **逐字搬入**，不得增刪）

對 v1 規則表中每一組候選命中，先做 **token 層**判斷，再做 **frame 層**彙總（§3）。
**順序敏感。**

**允許的最小分句手段**：以 `。！？；：，、` 切分小句。**這不是 parser、不是 dependency
framework**，不引入任何第三方套件（僅標準庫）。任何超出此手段的需求 → 記 `undecided`，
**不得自行加規則**。

| rule_id | 條件 | 動作 |
|---|---|---|
| `R1_ACTION_TOKEN` | 命中 token 屬 `ACTION_TOKENS`（見下） | `vetoed` |
| `R2_EXCLUDE_TOKEN` | v1 排除詞命中（`_EXCLUDE_FOR`，`harness/run_ta2a_gate2.py:226-229`） | `vetoed`（v1 本已續行，此處記錄 ledger 供 audit） |
| `R3_ATTRIBUTIVE_NOUN` | 命中 token 在小句內**直接修飾外觀類名詞**（窗口 ≤3 字，允許 `剛`／`的` 插入） | `vetoed` |
| `R4_SIMILE_FRAME` | 命中所在小句含 `SIMILE_MARKERS` | `vetoed` |
| `R5_NEGATION_FRAME` | 命中所在小句含 `NEGATION_MARKERS` | `vetoed` |
| `R6_NO_ATTACHMENT` | 以上皆不觸發，且命中 token 在其小句內**找不到任何依附標記** | `undecided` |
| `R7_ATTACHED` | 以上皆不觸發，且命中 token 在其小句內找到**依附標記** | `kept` |

### §2.1 封閉詞表（**逐字，不許自行增補**）

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

> **主大腦自查修正（必須遵守）**：`午睡`／`補眠` **不是**動作詞。它們是 v1 `siesta` 幀的合法
> 觸發詞（`harness/run_ta2a_gate2.py:217`），若列入 `ACTION_TOKENS`，A1 會把整個 siesta 幀
> 系統性歸零——那不是修正污染，是**製造**污染。故明確切為 `COMPOUND_TIME_NOUNS`，
> `R1` **不得**作用於它們。
>
> **已知誘發點**：Owner 引用的污染原文是「去睡吧」與「夜色剛落下」，**兩者都不含
> `補眠`／`午睡`**。執行者**不得**因為看到 `siesta` 幀消失就擴大 `ACTION_TOKENS`——
> 那正是 measurement drift。

### §2.2 規則衝突的處理（**預登記，不得臨場判斷**）

1. `R1` 最高優先，命中即 `vetoed`，不再看其他規則。
2. `R3` 與 `R4` 皆成立時，以 `R3` 為 ledger 主記錄（`R3` 較具體），但 **兩者都寫進 ledger**。
3. `R2` 與其他規則同時成立時，全部寫入 ledger，`vetoed` 結果不變。
   （**預登記事實**：`R2` 在 v1 規則順序下**不可達**——`_FRAME_RULES` 先比 `siesta`（含 `補眠`）
   與 `late_night_meal`（含 `宵夜`），帶排除詞的文本永遠不會走到 `night`／`early_breakfast`。
   `R2` 因此是**防禦性規則**，作用只在 ledger 記錄，**不得**用它反推任何結論，也**不得**
   為了讓它「有作用」而調整 v1 規則順序（v1 凍結）。)
4. **任何詞表命中但小句無法切分**（例如全文無標點）→ `undecided`。
5. **不得**為了讓某筆結果好看而調整規則表、詞表或窗口大小。
6. **不得**因為「某幀全數消失」而擴大 `ACTION_TOKENS`——那會把真實幀殺成 `unframed`，
   屬 measurement drift。**照實回報。**

### §2.3 實作細節預登記（**在檢視任何 corpus 內容之前鎖定**）

工單定義了規則表的**語義**，以下把它還原成**可執行的確定程序**。這些細節同樣受
「看到結果不得修改」約束。

1. **分句**：以 `。！？；：，、` 切分。分隔符本身不屬於任何小句。小句 = 兩分隔符（或串首尾）
   之間的極大非分隔字元連續段，並去除前後空白。**無任何其他切分手段。**
2. **小句無法切分**：若 `raw_text` 完全不含上述任一分隔符 ⇒ 判定為「無法切分」，
   依 §2.2.4 處理。
3. **token 的小句歸屬與多occurrence**：對 token 在 `raw_text` 中的**所有**出現位置逐一分句判定，
   取最嚴格結果，優先序 `vetoed` > `kept` > `undecided`。audit 的 `span` 記錄**產生該結果的
   那一次**出現之小句摘要（≤40 字）。
4. **`R1` 不需要小句**：token 屬 `ACTION_TOKENS` 即 `vetoed`，不受「無法切分」影響。
   （與 §2.2.1「R1 最高優先、不再看其他規則」一致。註：在「無法切分」且同時含動作詞與非動作詞
   token 的情形下，本解讀與「整筆直接 undecided」在 frame 層結果**相同**。）
5. **`R2` 的粒度**：`R2` 是 **frame 層級**的排除詞註記，不是 per-token 條件。若 v1 指派給
   frame `F` 且 `_EXCLUDE_FOR[F]` 命中 `raw_text`，則 `T` 中**每個** token 的判定都會被
   `R2` 命中。依 §2.2.3，此情形預期永不發生（ledger 應為 0 筆）。

   > 🔴 **修訂記錄 R-2（見 §2.5）**：本條早期版本寫「`R2` 只記 ledger、**不改變**主結果」。
   > 已依工單 §3 表「`R2_EXCLUDE_TOKEN` … 動作 = **`vetoed`**」修正為：`R2` 命中即 `vetoed`，
   > 優先序僅次於 `R1`。**因 `R2` 在 v1 規則順序下不可達（§2.2.3），此修正在 corpus 上
   > 完全不可觀測，不影響任何重播結果。**
6. **`R3` 的可執行定義**：在同一小句內，令 token 出現 span 為 `[t0, t1)`、外觀類名詞 `n` 的
   出現 span 為 `[n0, n1)`。`R3` 成立若符合任一情形：
   - **情形 A（重疊／複合詞）**：`n0 ≤ t0` 且 `[t0, t1)` 與 `[n0, n1)` 有重疊
     （涵蓋「夜色」：token `夜` 落在名詞 `夜色` 內）。
   - **情形 B（後接修飾）**：`n0 > t1`，且間隔子句 `clause[t1:n0]` 的**每個字元**都屬
     `{剛, 的}`，且 `n0 - t1 ≤ 3`（窗口 ≤3 字，允許 `剛`／`的` 插入）
     （涵蓋「夜的顏色」「夜的色澤」「夜的天」）。
7. **`R4`／`R5`／`R7` 的可執行定義**：小句字串是否以**子串**包含對應詞表任一項。
   marker 搜尋**以小句原字串為準**（含 token 自身的字元）。
   （已知限制：`就寢` 含 `就`，但 `R1` 最高優先已先 `vetoed`，故不影響結果。記錄不修。）
8. **判定優先序**：`R1` → （`R2` 註記）→ `R3` → `R4` → `R5` → `R6`／`R7`。
9. **`T` 的構造與順序**：對 v1 判定為 frame `F` 的 observation，
   `T = [k for k in _FRAME_RULES[F] 的觸發詞 tuple if k in raw_text]`
   （v1 的 `exclusive`／`exclude` 已由 v1 自身處理）。**順序 = v1 規則表中的宣告順序**
   （例如 `night` ⇒ `["夜", "睡", "就寢"]` 的宣告順序），以確保可重現。
10. **`T` 為空**（v1 已是 `unframed`）⇒ A1 判定直接為 `undecided`，v2 frame = `unframed`，
    **不進入規則表**。

### §2.4 v1 唯讀 import 清單

```python
from harness.run_ta2a_gate2 import _FRAME_RULES, _EXCLUDE_FOR, extract_temporal_frame, d
```

**不得修改** `harness/run_ta2a_gate2.py` 任何一行（`_FRAME_RULES:215-224`、`_EXCLUDE_FOR:226-229`、
`extract_temporal_frame:232-243`、`d:247-248`）。

> ⚠️ **預登記的實作約束（執行者回報，非缺陷）**：v1 的多數決 helper `_mode`
> （`harness/run_ta2a_gate2.py:377-379`）與 ΔR／ΔI／DiD 彙總（`:382-405`）**是 `main()` 內的
> 巢狀 closure／內聯程式碼，並非模組層級可 import 的函式**。v1 凍結 ⇒ 不得為了取得可 import
> 版本而修改該檔。因此重播腳本**逐字轉錄**這四段 frozen 公式（`_mode`、`dR=max(per_probe.values())`、
> `dI=d(mode(ON),mode(OFF))`、`did=dR-dI`、三門檻 `c1/c2/c3`），並在原始碼行號處標註出處。
> 轉錄**不得**改動任何常數、順序或門檻。`d()` 本身為模組層級，**必須 import**，不轉錄。

---

## §2.5 修訂記錄（**資料檢視前**的詮釋澄清）

### R-1：「小句無法切分」的定義（工單內部不一致，須裁決）

**發現**：工單自身存在**不可同真**的兩處要求。

| 出處 | 內容 |
|---|---|
| 工單 §3 衝突規則 4 | 「任何詞表命中但**小句無法切分**（**例如全文無標點**）→ `undecided`」 |
| 工單「測試」案例表 R3 正例 | 「大概是夜色剛落下…那種藍」 ⇒ 預期 **`vetoed`** |

該例句**不含**任何分句標點 `。！？；：，、`（`…` 不在封閉清單內）。因此：

- 若「全文無標點」⇒ `undecided`，則工單自帶的 R3 正例**必然失敗**；
- 若「全文無標點」仍照常評估規則，則該正例得 `vetoed`（`夜色` 觸發 R3，`大概`／`那種`
  同時觸發 R4，兩者依 §2.2.2 全部寫入 ledger，主記錄 `R3`）。

**裁決：以可執行的案例表為準。** 理由：(1) 案例表是可直接驗收的具體契約；
(2) 規則表、詞表、窗口大小、優先序、真值表**完全未動**——本次只澄清「無法切分」的
**退化定義**，不改變任何規則的觸發條件。

**修訂後定義**：「小句無法切分」⇒ 命中 token 所在小句**為空／純空白**（退化情形，
實際上極少發生）。**全文無標點時，全文即為單一小句，`R2`–`R7` 照常評估。**

**時序保證**：本修訂發生於**任何 corpus 內容檢視之前**（尚未開啟
`ta2a_gate2_four_arm_real.json`、尚未讀任何 `raw_text`），且僅因工單**內部**不一致
而做，**未**因任何資料結果而做。規則表本身逐字未改。

> 若主大腦／Owner 認為應採另一讀法（無標點即 `undecided`），修正點為單一位置
> `harness/ta2v2a_attachment.py::judge_token_at` 的空小句分支，重跑即可。
> 但該讀法會使工單自帶的 R3 正例失敗。

### R-2：`R2` 命中時的動作

**發現**：契約 §2.3.5 早期版本說 `R2` 「只記 ledger、不改變主結果」，但工單 §3 表明列
`R2_EXCLUDE_TOKEN` 的**動作 = `vetoed`**。

**裁決：採工單表。** `R2` 命中即 `vetoed`，優先序僅次於 `R1`。

**影響為零**：`R2` 在 v1 規則順序下不可達（§2.2.3、§3.5），故此路徑在 corpus 上不可觀測，
不影響任何重播數字。此修訂同樣發生於**任何 corpus 內容檢視之前**。

---

## §3 判定輸出三元 ＋ 封閉詞表（供 AC5 機器比對）

### §3.1 判定三元

- `vetoed`：命中 token **不構成**「事件被放進 temporal frame」，該 token 的 frame 貢獻被否決。
- `kept`：命中 token 在其小句內**依附於事件**（有依附標記），frame 貢獻成立。
- `undecided`：規則表**無法判定**。**`undecided` 不算 `kept`**（見 §3.3）。

### §3.2 兩段式彙總（**預登記真值表，不得自行改寫**）

**第一段 — token 層**：對 v1 判定為 frame `F` 的每個 observation，用 v1 規則表（唯讀 import）
列出 `F` 的**所有實際命中 token**（§2.3.9），對 `T` 中**每個** token 套用 §2 規則表，
各得一個 token 層判定。

**第二段 — frame 層彙總**：

| 條件 | A1 判定 | v2 frame |
|---|---|---|
| `T` 中**所有** token 皆 `vetoed` | `vetoed` | `unframed` |
| `T` 中**存在任一** token 為 `kept` | `kept` | 保留 v1 frame `F` |
| 其餘（無 `kept`，且至少一個 `undecided`） | `undecided` | `unframed` |

`T` 為空（v1 已是 `unframed`）⇒ A1 判定直接為 `undecided`，v2 frame = `unframed`，
**不進入規則表**。

> 🔴 **這是修正後的正確設計**。原規格（frame 層級直接否決）有**假陰性缺陷**：v1 的 `night`
> 觸發詞是 `夜`／`睡`／`就寢`，一個回應若同時含**合法的 `夜`** 與**動作詞 `睡`**
> （例：「夜深了，該睡了」），frame 層級否決會把整筆判為 `vetoed`，**連合法的 `夜` 一起殺掉**。
> 這會製造 A1「沒有頭寸」的**假結論**。故必須兩段式。
>
> ⚠️ **特別警告**：**只有「所有 token 皆 vetoed」才判 `vetoed`**。若實作讓 frame 層在有
> `undecided` token 時直接判 `vetoed`，就是重犯了撤回的那個缺陷。

### §3.3 `undecided` 刻意不算 `kept`（且必須逐 token 揭露）

理由：無法判定時若算命中，A1 的 precision 指標會被不可解釋的樣本稀釋。
**但報告必須逐 token 揭露**——這樣「丟掉」究竟是因為 `睡` 被殺，還是因為 `夜` 找不到
依附標記，一目了然。**這正是 token 層存在的理由：診斷價值，而非更高的分數。**

> **預期的真實後果（不是缺陷，是 spike 要量出來的答案）**：
> 「夜深了，該睡了」這類回應，`睡` 會被 `R1` 否決，但 `夜` 未必能找到 `ATTACHMENT_MARKERS`
> （`夜深了` 小句內無 marker）→ 該 token 判 `undecided` → 整筆判 `undecided` → 丟失 frame。
> **執行者不得為了救回這種情形而往 `ATTACHMENT_MARKERS` 加詞。** 照實回報「純規則依附濾層
> 有這個 recall 損失」即可——這對 A→E 決策是有效資訊。

### §3.4 封閉詞表（與 §2.1 **逐字相同**，重列供 AC5 機器比對）

`tests/test_ta2v2a_attachment.py` 會解析本文件**所有** `NAME = {...}` 圍欄區塊，
斷言每一個都與 `harness/ta2v2a_attachment.py` 的模組常數**逐字一致**，且兩個區塊彼此相同。

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

### §3.5 `R2` 的誠實說明（預登記）

`R2_EXCLUDE_TOKEN` 在 v1 規則表順序下**實際不可達**。`R2` 因此是**防禦性規則**，
作用只在 ledger 記錄。**不得**用它反推任何結論；**不得**為了讓它「有作用」而調整 v1 規則順序
（v1 凍結）。因此 §5 AC6 對 `R2` 採**規則述語單元測試**（正例：`exclude_hit=True` ⇒
`vetoed`＋rule `R2`；反例：`exclude_hit=False` ⇒ 不記 `R2`），**不**構造不可能在 corpus 出現的案例。

---

## §4 量化指標定義（**全部沿用 frozen v1 公式，不得重寫**）

所有輸出標記 `feasibility_only: true`。**本節任何數字都不得被讀為 Gate 2 判定。**

### §4.1 輸出結構

| 欄位 | 定義 |
|---|---|
| `llm_calls` | 恆為 `0` |
| `feasibility_only` | 恆為 `true` |
| `v1_frames_non_unframed` | `v1_frame != "unframed"` 的筆數 |
| `v2_frames_non_unframed` | `v2_frame != "unframed"` 的筆數 |
| `frame_changed` | `v1_frame != v2_frame` 的筆數 |
| `noise_proxy` | `frame_changed / v1_frames_non_unframed`（分母 0 時記 `null`） |
| `non_trivial` | `v2_frames_non_unframed > 0` |
| `recall_loss_upper_bound` | `v1_frames_non_unframed - v2_frames_non_unframed`（A1 丟棄的真陽性上限） |
| `precision_sleep_contamination` | 見 §4.2 |
| `precision_night_simile_contamination` | 見 §4.2 |
| `dR` / `dI` / `did` | 以 v2 frame 重算，逐字沿用 v1（§2.4 轉錄，`d()` import） |
| `c1_dR_ge_1` / `c2_did_ge_0_5` / `c3_observation_stable` | 以 v2 frame 重算，**逐字沿用 v1 三門檻與順序**；**只輸出布林值，不輸出 PASS/FAIL 判定** |

### §4.2 precision on 已知污染集（**預登記的集合定義**）

Owner 在設計 §9.4 逐字鎖定兩類污染。corpus 中以 v1 frame 與封閉詞表**機械地**定義對應集合：

- `pollution_sleep` := `{obs : v1_frame == "night" 且 raw_text 命中任一 ACTION_TOKENS}`
  （對應「去睡吧」：動作詞 `睡` 被當成 time-of-day）
- `pollution_night_simile` := `{obs : v1_frame == "night" 且 raw_text 命中任一 SIMILE_MARKERS}`
  （對應「夜色剛落下」：比喻／外觀修飾語境）

`precision_X` := `|{obs ∈ pollution_X : v2_frame == "unframed"}| / |pollution_X|`
（分母 0 時記 `null`）。即「v2 成功否決的比例」。

> **誠實邊界**：這是**已知污染集的 proxy**，非逐字比對 Owner 的兩個示例句。
> 報告必須**列出**每個集合實際命中的 observation 原文，讓讀者自行判斷 proxy 是否成立。
> **不得**因為命中率低就調整集合定義。

### §4.3 輔助觀察（**僅記錄，不設門檻**）

- 3 個 probe 中有幾個的 `ON_correct` 臂在 v2 下**仍保有 frame**。
- `night_rest` 的 `siesta` 幀是否存活（`COMPOUND_TIME_NOUNS` 不受 `R1` 作用，故預期存活；
  若消失，**照實回報**）。

### §4.4 token 層丟失歸因表（**必交付**）

把每一筆 v2 判為 `unframed` 但 v1 非 `unframed` 的 observation，依丟失原因分兩類：

- **類別 `all_vetoed`**：`T` 中**所有** token 皆 `vetoed` ⇒ A1 判 `vetoed`。
- **類別 `no_attachment_undecided`**：無 `kept` 且**至少一個** `undecided` ⇒ A1 判 `undecided`，
  其中 token 層原因是「找不到依附標記」（`R6`）。

**這兩者的區別就是 A1 損失的性質**：前者是 A1 設計上要做的 precision 過濾（預期、可辯護）；
後者是規則表的 recall 損失（代價）。**兩者都不得為了好看而被合併或重分類。**

### §4.5 veto ledger

依 `rule_id` 統計 token 層觸發次數（注意：同一 token 可有多個 rule 記錄，依 §2.2.2／.3）。

---

## §5 驗收條件（AC1–AC9）

| AC | 條件 | 驗證方式 |
|---|---|---|
| **AC1** | 凍結基線：重播前後 `run_ta2a_gate2.py` 與 corpus 兩檔 sha256 **一致**；§7 三項全對得上 | 重播前後各算一次 sha256 ＋ `git rev-parse HEAD` 比對 |
| **AC2** | `git status` 確認 `run_ta2a_gate2.py`、`harness/tl12_temporal.py`、`harness/observer.py`、v1 契約文件**皆無改動** | `git status --short` ＋ `git diff --stat` |
| **AC3** | 0 次 LLM 呼叫：重播腳本不得 import／呼叫 `llm_call`、不得開 network | 原始碼檢查（無 `llm_call`／`requests`／`http`／`socket` import）＋ 輸出標記 `llm_calls: 0` |
| **AC4** | arm-blindness：重播過程**不得**讀取 `arm`／`probe_id`／`injected_temporal_line`；這三欄只可由**獨立的 audit 步驟**寫入 | 重播核心函式簽章只收 `list[str]`（純 `raw_text`），回傳 token 層／frame 層判定；audit 步驟為**另一個函式**，負責 join 索引→中繼資料。報告說明隔離方式 |
| **AC5** | 規則表完整性：`harness/ta2v2a_attachment.py` 的詞表與本文件 §2.1／§3.4 **逐字一致** | `tests/test_ta2v2a_attachment.py` 解析本文件所有 `NAME = {...}` 圍欄，與模組常數逐一比對 |
| **AC6** | 測試覆蓋 §2 每條 rule_id 至少一例**正例與一例反例**（`R2` 依 §3.5 採述語單元測試）；`harness/ta2v2a_attachment.py` 的測試 **≥ 8 passed** | 測試名稱含 rule_id 標記；`.venv\Scripts\python.exe -m pytest -q tests/test_ta2v2a_attachment.py` |
| **AC7** | audit 表涵蓋 **全部 66 筆**，`raw_text` **無截斷** | audit md 逐筆列 `raw_text` 全文；JSON 記錄筆數與 corpus 筆數相等 |
| **AC8** | 輸出含 `feasibility_only: true`，且**不含**任何 Gate 2 PASS/FAIL 判定 | 輸出 JSON／md 全文檢查：無 `PASS`／`FAIL` 判定鍵，無 `verdict` 欄位 |
| **AC9** | 誠實報告：必須明確聲明 A1 **不能提高 recall**（只能 veto），並列出 A2 為何不做 | audit md §限制 段落明文聲明 |

---

## §6 停止條件

**本節只預登記「流程／安全」停止條件。任何依賴資料的門檻一律不預登記**——
否則看到結果後就會有「差一點就達標」的誘因。**資料難看不是停止理由，是要回報的答案。**

1. 凍結基線（§7 任一項）在重播前後不一致 ⇒ **停止並回報**，不繼續。
2. 模組常數與本文件 §2.1／§3.4 不一致（AC5 失敗）⇒ **停止並回報**。
3. 重播路徑出現任何 LLM 呼叫或網路存取痕跡 ⇒ **停止並回報**。
4. 發現必須修改 `harness/run_ta2a_gate2.py`、v1 契約或任何 `src/**` 才能完成 ⇒ **停止並回報**。
5. 規則表在實際語料上大量 `undecided`、某幀全數消失、或已知污染未被解決
   ⇒ **不是停止條件，照實回報並記為規則表缺陷（記錄不修）**。
6. 任何行程／服務操作需求（重啟、kill、綁生產埠）⇒ **停止並回報**，自行不得執行。

---

## §7 凍結基線（於 corpus 內容檢視**之前**取得）

| 項目 | 值 |
|---|---|
| `git rev-parse HEAD` | `b14262f4a62e04faaca3fccaf45c5eaad889a45f` |
| `sha256(data/harness_out/tl12/ta2a_gate2_four_arm_real.json)` | `f5690acbe05efa08e464ae54b6bb0a73c8f76130dbed7a2b7dd87208c1fcffe1` |
| `sha256(harness/run_ta2a_gate2.py)` | `bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253` |

corpus 結構（由主大腦核實，本票**不重跑、不改 N**）：

- `results` 下 4 個 probe：`meal_timing` / `meeting_timing` / `night_rest` / `_control`
- 前三者各 3 臂（`OFF` / `ON_correct` / `ON_mismatched`），`_control` 2 臂（`ON` / `OFF`）
- 每筆 observation 含 `raw_text`、`probe_id`、`arm`、`run_index`、`injected_temporal_line`、
  `temporal_frames`
- **合計 66 筆 observations**（54 ＋ 12）

**重播結束後必須重算兩個 sha256 並比對（AC1）。**

---

## §8 誠實邊界

1. **A1 不能提高 recall。** 結構上它只能 `vetoed`，不可能 `rescue`。丟失的真陽性上限見
   §4.1 `recall_loss_upper_bound`。（AC9）
2. **A2（rescue）不做**：等同新 temporal classifier，違反 Owner §9.3。
3. **這不是 Gate 2 判定。** 全部輸出 `feasibility_only: true`。Gate 2 仍 **INCONCLUSIVE**。
4. **v2 若讓 `siesta` 幀消失，不得擴大 `ACTION_TOKENS`**（§2.1、§2.2.6）。
5. **「夜深了，該睡了」類情形會被丟失**，且**不得**為救它而擴大 `ATTACHMENT_MARKERS`
   （§3.3）。這是**預期的**recall 損失，是有效資訊。
6. **`R2` 不可達**（§2.2.3、§3.5），ledger 應為 0 筆；**不得**用它反推結論。
7. **precision 集合是 proxy**（§4.2），不是逐字比對 Owner 的示例句。
8. **中文分句是脆弱的**：本濾層是字串切分，不是 parser。`…`、`—`、換行等**非**分隔符
   不會切分小句（封閉清單不含它們），故跨這類標點的 marker 可能被誤算為同小句。
   **記錄不修。**
9. **N=6 極小**，且四臂結果是**真實 LLM 於單一情境**的產物。不得把本票數字外推為
   Soul 的時間認知能力陳述。
10. **轉錄的 v1 公式**（§2.4）未經獨立重構驗證，僅以行號標註出處。
11. **不得**由本票輸出推導候選 E 的授權；E 須另開 ticket 並經 Owner 批准
    （上游設計 §9.7）。
