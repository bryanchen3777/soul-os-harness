# 工單：FEAT-LIFECYCLE-CLI-TYPE-NEUTRAL — 台帳型別中立化＋4 顆 Trait 審核入帳＋唯讀探針 opt-in

- 開立：主大腦（Engineering Brain），2026-10-27 16:5x EDT
- 裁定來源：Owner（Bryan）2026-10-27 審定備案與三大裁定
- 死線：**2026-10-27 22:00 EDT 夜槽**（＝2026-10-28T02:00:28Z）；且
  **20:40 EDT 的台帳刷新 cron 會自行 `git add/commit/push` `scripts/eval_lifecycle_cli.py`
  與 `logs/ENGINEERING_STATE.md`** ⇒ 對這兩個檔的改動**必須在 20:40 前 commit**，否則會被
  刷新 job 以「chore(ledger)」名義捲入並 push。
- 禁跑窗：22:00–01:00 EDT 不得跑全庫回歸 ⇒ **全庫回歸必須在 22:00 前完成**。

---

## 目標

1. 判定層（`scripts/eval_lifecycle_cli.py`）從「硬寫 `node_type == "value"`」改為
   **「以台帳記載之型別為依歸」**（型別中立化），衰減門檻（7 天）與四條件**一字不改**。
2. 依 Owner 裁定，將明晚將退役的 **4 顆 trait** 節點**人工審核後補丁入帳**。
3. 把**所有**會對線上服務發起連線的測試探針改成 **opt-in**（預設離線、顯式授權）。

---

## 背景與已量得的事實（皆為實測，非推測）

### 明晚 4 顆 trait 的審核事實（唯讀生產圖譜，2026-10-27 量得）

| node_id | 型別 | agent | created_ts | last_support_ts | support_propagation 祖先邊 | 建立+7d（衰減門檻） | 10-27T02:00:28Z | 10-28T02:00:28Z |
|---|---|---|---|---|---|---|---|---|
| `c43b515f2fdb4ac0bc2855c60d59c2c9` | trait | agent_rem | 2026-10-20T02:05:19.975473Z | 同 created | 無 | 2026-10-27T02:05:19.975473Z | 未跨 | **已跨** |
| `57c164011e3e4065bdcd05b6eeaa9c28` | trait | agent_akane | 2026-10-20T02:05:23.242942Z | 同 created | 無 | 2026-10-27T02:05:23.242942Z | 未跨 | **已跨** |
| `575efe6b68ef42bf8a904c1eac0561dd` | trait | agent_ram | 2026-10-20T02:05:25.882525Z | 同 created | 無 | 2026-10-27T02:05:25.882525Z | 未跨 | **已跨** |
| `ee1ac76f4f25435fa55f3a231641108c` | trait | agent_ruka | 2026-10-20T02:05:28.553858Z | 同 created | 無 | 2026-10-27T02:05:28.553858Z | 未跨 | **已跨** |

⇒ 四顆皆滿足「四條件形狀」（建立後從未被背書／無 support_propagation 祖先邊／節點存在／
ts 可解析）⇒ 是**可解釋的預期代謝**，不是破線。

### 根因（為什麼現在會紅）

- `scripts/eval_lifecycle_cli.py:897`（`is_expected_metabolism`）與 `:954`
  （`_is_pending_retirement`）都硬寫 `node_type == "value"`；
  `:732`（`expected_retirement_sets_by_cohort`，四條件推導）亦同。
  ⇒ **belief／trait／essence 弱化在結構上不可能是 expected 或 pending ⇒ 永遠落在 breaks**
  ⇒ 每晚固定紅燈（告警疲勞）。
- 連帶根因：`soul-os-harness-backups/ledger_refresh.py:227`
  `nb = not_before or "2026-01-01T00:00:00+00:00"` ⇒ **新 cohort 的 `not_before` 預設值
  本身就是錯的**（2026-10-26 cohort 的誤植即由此而來，已於本輪前一票修好台帳值，
  但**產生器未修** ⇒ 明日會再犯）。此檔第 358–364 行只對「已存在」或「下一槽會退役」的
  cohort 帶入 `not_before`，其餘落到該 fallback。

---

## A. 判定層型別中立化（主大腦執行）

### A1. 台帳結構擴充

`EXPECTED_RETIREMENT_COHORTS` 的每個 cohort 條目**新增選用鍵** `"types"`：

```python
{
    "date": "2026-10-20",
    "not_before": "2026-10-28T02:00:00+00:00",
    "nodes": frozenset({ ...既有 value 節點..., "c43b515f2fdb4ac0bc2855c60d59c2c9", ... }),
    "types": {
        "c43b515f2fdb4ac0bc2855c60d59c2c9": "trait",
        "57c164011e3e4065bdcd05b6eeaa9c28": "trait",
        "575efe6b68ef42bf8a904c1eac0561dd": "trait",
        "ee1ac76f4f25435fa55f3a231641108c": "trait",
    },
}
```

規則（**必須寫成 docstring 與守衛**）：

1. `types` 的 key **必須**是同 cohort `nodes` 的子集；value 必須是
   `{"value", "belief", "trait", "essence"}` 之一。
2. **未列於 `types` 的節點 ⇒ 記載型別視為 `"value"`**（向後相容：既有 10 個 cohort
   全部是 value，不需要逐筆補寫）。
3. **fail-closed 方向**：節點在台帳但未列型別 ⇒ 記載型別 = `"value"` ⇒ 非 value 的事件
   **不可能**被放行成 expected ⇒ 只會更嚴、不會更鬆。

### A2. 判定層改為「以台帳記載之型別為依歸」

- `normalize_cohorts()`：輸出多帶 `"types"` 鍵（dict）。
- 新增 `approved_node_types(cohorts) -> {node_id: node_type}`（未列者 = `"value"`）。
- `is_expected_metabolism(event, expected_ids, not_before, types=None)`：
  - 型別條件由 `event.node_type != "value"` 改為
    `event.node_type != recorded_type(event.node_id)`（`types` 缺省時以
    `approved_node_types()` 推導；**不得**回退成「只認 value」）。
  - 其餘條件（`lifecycle_state == "weakening"` 嚴格、`node_id` 在核准清單、
    `ts >= not_before`）**一字不改**。
- `_is_pending_retirement(event, node_by_id, supported, ledger_dates, ...)`：
  - 型別條件改為「`node_type` 屬靈魂型 {value, belief, trait, essence}」
    （**不再只認 value**）。
  - **value 的一票否決條款必須保留**：value 型若其**建立日已在台帳內** ⇒ 仍是 **breaks**
    （台帳對該日的 value 集合已逐筆審過 ⇒ 未列入者視為被否決）。
  - **非 value 型一律 pending（待審）**，除非已逐筆入帳（⇒ A3 命中 expected）。
  - 其餘條件（節點存在、`last_support_ts == created_ts`、無 support_propagation 祖先邊、
    嚴格 weakening、ts 可解析帶時區）**一字不改**。
- `expected_retirement_sets_by_cohort()`（四條件推導，`:732`）**維持只推導 value**：
  非 value 不得被「自動推導」進核准集合 ⇒ 只能人工入帳（Owner 裁定）。

### A3. 4 顆 trait 的入帳（人工審核核准，補丁形式）

- **入帳位置**：cohort `date="2026-10-20"`（既有條目，**不新增 cohort**——台帳結構守衛要求
  `date` 唯一）。
- **加入**：`nodes` 加入 4 個 id；`types` 記 4 筆 `"trait"`。
- **`not_before` 不變**（`2026-10-28T02:00:00+00:00`）——與同 cohort 的 value 節點
  **同一退役槽**。理由：該 cohort 的規則是「建立日＋8 天 @02:00Z」；這 4 顆的實際衰減
  門檻是 2026-10-27T02:05:1x～28Z，而 10-27T02:00:28Z 槽**早於**門檻（未跨）、
  10-28T02:00:28Z 槽**晚於**門檻（已跨）⇒ 第一個可退役槽就是 10-28T02:00Z。
  **不得**為 trait 開「比 value 更早」的特例（會變成放寬）。
- **審核結論（寫入 canonical）**：4 顆皆為 trait、建立後從未被背書、無 support_propagation
  祖先邊、型別與台帳記載一致、退役時刻 ≥ 該 cohort 的 `not_before`
  ⇒ 核准入帳。

### A4. 守衛測試（`tests/test_eval_lifecycle_cli.py` 新增，**恰 10 支**）

| 編號 | 斷言 |
|---|---|
| T1 | 台帳結構：每個 cohort 若有 `types`，其 key ⊆ 同 cohort `nodes`、value ∈ {value,belief,trait,essence}、且**不得**出現未知型別 |
| T2 | 型別中立：台帳記載 `trait` 的節點，其 trait 弱化事件（ts ≥ not_before）⇒ **expected**（不得落 breaks） |
| T3 | 型別不符不得放行：台帳記載 `trait` 的節點，若事件型別是 `value` ⇒ **不得** expected（fail-closed） |
| T4 | 非 value 未入帳 ⇒ **pending**（不是 breaks）：營造一個未入帳的 trait 弱化事件 ⇒ `pending_count ≥ 1` 且 `break_count == 0` |
| T5 | value 一票否決保留：value 型弱化、節點未入帳、但**建立日在台帳內** ⇒ 仍是 **breaks** |
| T6 | 非 value 不得被自動推導進核准集合：`expected_retirement_sets_by_cohort()` 對含 trait 的圖譜**不得**推導出 trait |
| T7 | not_before 一致性：同 cohort 內所有型別共用**同一個** `not_before`（不得為 trait 開小灶） |
| T8 | pending 必須大聲回報：CLI 輸出含 `pending_cohorts` 與**節點 id**（非 value 亦然） |
| T9 | 本票交付物守衛：台帳必須含該 4 個 id，且 `types` 皆為 `"trait"` |
| T10 | 門檻凍結（既有守衛延伸）：判準層不得引入「距今 N 天」算術（既有 `test_cli_does_not_import_decay_thresholds` 必須仍綠） |

### A5. 變異實驗（TM-1～TM-8，**每條必須精準轉紅**）

- TM-1：`is_expected_metabolism` 忽略台帳型別（只要在核准清單就放行）⇒ **T3 轉紅**
- TM-2：pending 的型別中立性改回「只認 value」⇒ **T4 轉紅**
- TM-3：移除 value 的一票否決條款（建立日在台帳內也判 pending）⇒ **T5 轉紅**
- TM-4：`approved_node_types()` 一律回 `"value"`（不讀台帳 `types`）⇒ **T2 轉紅**
- TM-5：移除 `ts >= not_before` 檢查 ⇒ **T7／既有提前早衰守衛轉紅**
- TM-6：移除 pending 的「無 support_propagation 祖先邊」條件 ⇒ 既有守衛轉紅
- TM-7：從台帳移除 4 顆 trait 之一 ⇒ **T9 轉紅**
- TM-8：四條件推導的條件 1 由 `value` 改為「所有型別」⇒ **T6 轉紅**

harness 規格（照 §既有慣例）：放 `%TEMP%`、**只改副本**、repo 全程唯讀、每條變異前斷言
副本 sha256 不同、結束後比對 repo sha256、轉紅判定用 `re.findall(r'FAILED (\S+)')`
再 `.split('::')[-1]`、anchor 需處理 **CRLF**。

### A6. 刷新器修正（repo 外：`soul-os-harness-backups/ledger_refresh.py`）

1. **`rewrite_entry()` 必須保留既有 cohort 區塊的 `"types"` 鍵**（否則人工入帳的型別會被
   20:40 刷新抹掉）。重建 block 時把原 `types` 原樣帶入；且 `types` 的 key 必須 ⊆
   合併後的 `nodes`，否則 **中止並報錯**（不得靜默丟棄）。
2. **新 cohort 的 `not_before` fallback 修正**：`"2026-01-01T00:00:00+00:00"` ⇒
   **建立日＋8 天 @02:00:00Z**（與既有 cohort 同一規則；`2026-01-01` 是誤植根源）。
3. **非 value 嚴禁自動入帳**：現有 `structural_members()`／`sim_retiring_value_nodes()`
   已只取 value ⇒ 保持；另**加印「待審清單」**（下一槽會退役但非 value 的節點 id 與 agent）
   供值守端人工審核（**只印，不入帳**）。
4. 不得改動該檔的 git 行為（`git add/commit/push` 維持原樣）。

---

## B. 唯讀探針 opt-in（worker 執行，檔案與 A 段不重疊）

### B1. 三個探針檔改為 env 驅動（預設離線）

統一 env 變數：`SOUL_OS_LIVE_SERVICE_BASE_URL`，**預設空字串**。

| 檔案 | 現況 | 改法 |
|---|---|---|
| `tests/test_private_chat.py` | `SERVER_URL = "http://localhost:8000"`、`WS_URL = "ws://localhost:8000/ws"`（L29/L30） | 兩者改由 env 推導（預設空字串 ⇒ 無目標）；`pytest.mark.skipif` 必須引用**同一個** env（既有 `SOUL_OS_LIVE_PRIVATE_CHAT` 閘門**保留**，兩者皆須成立） |
| `tests/test_plan_a_launcher_v1.py:128` | `httpx.get("http://127.0.0.1:8000/health")` | base URL 由 env 提供（預設空字串）；未設定 ⇒ `self.skipTest(...)`（**不得** fail：沒有授權目標就沒有東西可探） |
| `tests/test_server_ops_python_path_v1.py:79` | 同上 | 同上 |

⚠️ `tests/test_private_chat.py` 頂部有註解說明「import 刻意放在位址常數之後，因為
`ADDRESS_ALLOWLIST` 以 (檔案, 行號) 為鍵」⇒ 改完後該約束消失，註解要一併更新（不得留
過期理由）。

### B2. `tests/infra/test_no_production_spawn_guard.py`

- `ADDRESS_ALLOWLIST` 移除 **4 筆**（`test_private_chat.py` ×2、
  `test_plan_a_launcher_v1.py` ×1、`test_server_ops_python_path_v1.py` ×1）
  ⇒ `EXPECTED_ADDRESS_ALLOWLIST_SIZE` 9 → **3**。
- 保留的 3 筆（非線上連線）：`tests/clients/test_vc_perf_opt_1.py`、
  `tests/test_infra_no_pattern_process_kill.py`、`tests/test_websocket_e2e.py`
  （PRODUCTION_PORT 紅線，只用來**拒絕**撥號）。
- 移除條目時**必須寫下理由**（不是「已修好」，而是「已改為 env 驅動 opt-in」）。

### B3. `tests/infra/test_live_service_probes_are_opt_in.py`（強化既有護衛）

- **R1 強化（目前是盲腸，必須修）**：現在只追**一層**模組常數，
  `test_identity.py` 的 `WS_URL = (LIVE_BASE.replace(...))` **追不到** `LIVE_BASE` 的
  env 讀取 ⇒ 該檔是「**因為掃不到而通過**」，不是因為它真的合規。
  ⇒ 改為**遞迴追蹤**模組常數（含 `Name` → 常數 → 常數，含 visited 集合防環）。
- **R2 擴大**：由「只檢查 `tests/test_identity.py`」改為「**掃描 `tests/**` 全部檔案**的
  程式碼字串常數不得含生產服務位址」，docstring 與註解不算；例外僅限 B2 保留的 3 筆
  （以 (檔案, 行號) 或 (檔案, 說明) 明確列舉）。
- **R3 自測**：合成樣本必須被 R1 抓到（既有 R3 的合成樣本要能穿透**遞迴**追蹤）。
- 新增 **R4**：http(s) 目標（`httpx.get`／`urllib.request.urlopen`）若由 env 決定，
  同樣要求「無非空預設 ＋ 同一 env 被 skipif 引用」⇒ 涵蓋 B1 的兩個 `/health` 探針。

### B4. 變異實驗（KM-B1～KM-B4，每條精準轉紅）

- KM-B1：破壞 R1 的**遞迴**常數追蹤（退回一層）⇒ 必須轉紅
- KM-B2：移除「非空預設」判斷 ⇒ 轉紅
- KM-B3：移除「同一 env 被 skipif 引用」判斷 ⇒ 轉紅
- KM-B4：把硬寫位址塞回任一探針檔 ⇒ 轉紅

harness 規格同 A5（%TEMP%、只改副本、repo 唯讀、sha256 前后比對、CRLF anchor）。

---

## 不做（Out of Scope）

- 不得動衰減門檻（7 天）與四條件的**任何**語意。
- 不得動 `src/inner_life/**`（引擎與生命週期語意）、`personas/`、`configs/`、`data/**`。
- 不得改 `soul_elevation` 外部套件。
- 不得重啟生產服務（重啟須 Owner 明示授權，且只走 `scripts\server_ops.ps1`）。
- 不得對任何行程動手（禁 `Stop-Process`／`taskkill`／`kill`）。
- worker 不得 `git add`／`git commit`／`git push`。
- 不得**偽造**任何節點／邊／台帳條目；台帳只准「逐節點 id 有界」地補丁。
- 本票**不**處理 `lifecycle_state` 未回寫、`epistemic_tensions` 重複 id、
  `support_propagation` 槽增量 0、50+ 模組環境變數治理（各自另票）。

## 回報格式

1. 改動檔案清單（逐檔：改了什麼、為什麼）
2. 守衛測試結果（跑了哪些、幾筆過、有無失敗）
3. **變異實驗結果（每條變異：轉紅＝哪支守衛）**
4. 台帳 4 顆 trait 的入帳前後對照（id／型別／not_before）
5. 明晚槽預測（CLI 以 `2026-10-28T02:00:28Z` 跑的 VERDICT 與四項對帳數字）
6. 有沒有踩到 Frozen Contract／意外行為
7. 未做的部分與原因
