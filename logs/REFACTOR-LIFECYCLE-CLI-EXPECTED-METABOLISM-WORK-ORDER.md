# 工單：REFACTOR-LIFECYCLE-CLI-EXPECTED-METABOLISM

> **開立**：主大腦（2026-10-23 14:xx EDT）
> **依據裁定**：Owner (Bryan) 2026-10-23 裁定 —— 晨槽六項核定通過；10/24 夜槽紅線分岔採納「**選項（乙）改判準**」
> **裁決原意（逐字）**：
> - 「否決選項（甲）『只改判讀』、裁定執行選項（乙）『改判準』。」
> - 「嚴禁調整現有衰減門檻（Decay Thresholds 凍結）。」
> - 「嚴禁人為偽造任何 Pattern 支持邊或手動消紅。」
> - 「只有建立於 2026-10-11 且 last_support == created_ts（祖先支持邊為 0）的指定 Legacy Value 節點轉入弱化，才被歸納為 EXPECTED_METABOLISM。」
> - 「一旦出現非白名單內的 Value 弱化，或出現非預期 Trait/Essence 異動，CLI 必須堅決輸出 VERDICT: FAIL 與 exit code 1。」
> - 「准予另立工單 REFACTOR-LIFECYCLE-CLI-EXPECTED-METABOLISM，要求配備 M1～Mn 變異測試（包含白名單漏抓、非預期 Value 混入、純度誤判等變異株），跑通後經由全庫回歸合入主幹。」
> - 「允許在後續工單提交中，一併更新 ENGINEERING_STATE.md §0.1.52，將接縫期望模型修正（去重記憶體表延續）及本次裁決入庫留痕。」
>
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**。

---

## 目標

把 `scripts/eval_lifecycle_cli.py` 的生命週期判準層升級為**能區分「預期生命週期代謝」與「意外破線崩塌」**：

- 2026-10-11 建立、從未被再背書（`last_support_ts == created_ts`）、且無任何 `support_propagation` 邊指向的 **Legacy Value 節點**轉入 `weakening` ⇒ 分類為 `EXPECTED_METABOLISM`，**不算破線**。
- 任何**非**白名單內的 Value 弱化、或任何 Trait/Essence 異動 ⇒ 仍**堅決** `VERDICT: FAIL` + `exit code 1`（一票否決）。
- 目的是**消除告警疲勞**：不可把「CLI 硬性 FAIL 人為視為預期並忽略」，否則未來真崩塌會無從察覺。

---

## 範圍（動得到與動不到的檔案）

### 可動（本票唯一允許修改的 repo 檔，共 2 個）

| 檔案 | 動什麼 |
|---|---|
| `scripts/eval_lifecycle_cli.py` | 新增 2 個常數 + 2 個純函式；改 `assess()` 的判定分支；`main()` 串接；報告新增一段 |
| `tests/test_eval_lifecycle_cli.py` | 新增 1 個測試類 `TestExpectedMetabolism`（守衛測試） |

### 必須新建（但**不進 repo**）

| 檔案 | 位置 | 說明 |
|---|---|---|
| 變異 harness | `%TEMP%\expected_metabolism_mutations\mutation_harness.py` | 🔴 **必須放 %TEMP%**（R5：`tests/**` 禁用 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`） |

### 必須新建（進 repo）

| 檔案 | 說明 |
|---|---|
| `logs/REFACTOR-LIFECYCLE-CLI-EXPECTED-METABOLISM-WORK-ORDER.md` | 本工單本體（主大腦已開立，**執行者不要改它**） |
| `logs/ENGINEERING_STATE.md` | **追加** §0.1.53（詳見「文件登錄」） |

### 🔴 動不得

- `src/**`（任何檔案）—— 特別是 `src/inner_life/elevation_adapter.py`、`src/inner_life/lifecycle_overlay.py`、`src/inner_life/emergent_projection.py`。
- `C:\Users\bbfcc\.local\bin\soul-elevation\**`（另一個 repo；衰減門檻在那裡）。
- `data/**`（生產狀態，只讀）。
- `tests/test_observability_completeness_1.py` 的 `allowed` / `frozen_dirs` —— **本票不動 `src/**` ⇒ 不需要更新 `allowed`**（`allowed` 只約束 `src/**` 改動；`frozen_dirs` 11 筆不含 `scripts/`）。
- `%TEMP%\morning_obs\morning_audit.py` —— 🔴 **Owner 明令「量測工具留待後續維護窗口修正，禁止當前熱更」**。本票**不得**碰它。

---

## 做法（決策已定，執行者照做）

### 步驟 0：動工前的前置檢查（必須先做並回報）

```powershell
$repo='C:\Users\bbfcc\.local\bin\soul-os-harness'
# 0.1 確認 scripts/ 不是生產執行期相依（變異 in-place 才安全）
Select-String -Path "$repo\src\*\*.py","$repo\src\*\*\*.py" -Pattern 'scripts\.' -ErrorAction SilentlyContinue
# 0.2 確認工作樹乾淨、基線 commit
git -C $repo status --porcelain        # 必須空
git -C $repo rev-parse --short HEAD    # 必須是 bbcc17a（或其後續）
# 0.3 記錄 CLI 檔案的基準 sha256（變異還原要用）
(Get-FileHash "$repo\scripts\eval_lifecycle_cli.py" -Algorithm SHA256).Hash
```
若 0.1 顯示 `src/` 內有任何檔案 import `scripts/`，**停下回報**（變異 in-place 會影響生產行程）。

### 步驟 1：`scripts/eval_lifecycle_cli.py` —— 新增常數

在 `DEFAULT_KEY_NODE = "6a92c906b245"`（:69）之後新增：

```python
# ── 預期退役集合（Owner 2026-10-23 裁定：選項乙「改判準」）────────────────
# 語意：2026-10-11 建立的 Legacy Value 節點，因從未再被日常 Pattern 背書
# （last_support_ts == created_ts、祖先 support_propagation 邊＝0），
# 隨生命週期自然代謝而轉入 weakening —— 這是**預期代謝**，不是破線。
# 🔴 下列基線是 Owner 核准的「指定集合」；規則推導結果**不得超出**它。
EXPECTED_RETIREMENT_DATE = "2026-10-11"
EXPECTED_RETIREMENT_BASELINE = frozenset({
    "0b398afedf30486baa75047028b3319d",  # agent_akane
    "24bad790f8954dc2ae4711c4c1c6ffac",  # agent_aoi
    "56391bd5ce154d9f8ee09ef40280ec71",  # agent_miku
    "60a2d5e453914ac3bbbbc02cb549b076",  # agent_yua
    "6a92c906b245458cb0c4c64ea214f2eb",  # agent_mahiru  ★關鍵反例
    "7fcf5557b1994fd1977973f7dcdf5a38",  # agent_ram
    "8dd335036f21453b85c72d17eaf5b303",  # agent_rem
    "9f9b13498c334268aaf2e95429bd2228",  # agent_ruka
    "a8b48b0210e74363b506f5750d61d125",  # agent_mai
    "d0df567934274a9697feff6d1b0d9af5",  # agent_anna
})
```

> 這 10 顆的實測依據（主大腦 2026-10-23 實測，生產圖譜 153 顆 value 中**恰好** 10 顆符合，零誤抓）：
> 建立日全為 2026-10-11、`last_support_ts == created_ts`、`support_propagation` 邊數全為 0、`stability` 全為 0.3。

### 步驟 2：`scripts/eval_lifecycle_cli.py` —— 新增兩個純函式

放在 `assess()` 之前（純函式，**無 I/O、無副作用**）：

```python
def expected_retirement_set(nodes: list, edges: list) -> set:
    """推導「預期退役集合」。

    四條件全滿足、缺一不可：
      1. node_type == "value"
      2. created_ts 的日期部分 == EXPECTED_RETIREMENT_DATE
      3. last_support_ts == created_ts（建立後從未再被背書）
      4. 沒有任何 trigger_type == "support_propagation" 的邊指向它（祖先支持邊＝0）

    🔴 **不得**引入任何時間門檻算術（不得出現 timedelta／天數比較）——
    衰減門檻是凍結的，本判準只認「建立日 + 從未背書」這個**結構性事實**。
    """
    supported = {e.get("node_id") for e in edges
                 if isinstance(e, dict) and e.get("trigger_type") == "support_propagation"}
    out = set()
    for n in nodes:
        if not isinstance(n, dict) or n.get("node_type") != "value":
            continue
        created = n.get("created_ts") or ""
        if not isinstance(created, str) or created[:10] != EXPECTED_RETIREMENT_DATE:
            continue
        if (n.get("last_support_ts") or "") != created:
            continue
        nid = n.get("node_id")
        if not nid or nid in supported:
            continue
        out.add(nid)
    return out


def baseline_violation(expected: set) -> list:
    """推導集合若超出 Owner 核准的基線 ⇒ 規則被放寬 ⇒ **執行期自我否決**。

    這是「規則不得被悄悄放寬」的**執行期**保險（不只靠 CI）。
    """
    extra = sorted(x for x in expected if x not in EXPECTED_RETIREMENT_BASELINE)
    if extra:
        return ["⓪ 預期退役集合超出核准基線（規則被放寬）：%s" % extra]
    return []
```

### 步驟 3：`scripts/eval_lifecycle_cli.py` —— 改 `assess()`

`assess()` 現有簽章（:213）：

```python
def assess(*, new_events, prompt_diffs, key_node, weak_before, weak_after,
           graph_nodes=0, soul_types=SOUL_TYPES, eval_error="", undelivered=0) -> list
```

**新增一個 keyword-only 參數**（放在最後，預設空集合 ⇒ 舊呼叫點全部維持原行為）：

```python
           expected_retirement: frozenset = frozenset(),
```

**判定分支（取代現行 :256–273 的靈魂／非 pattern／key node／weak 四段）**，順序如下：

1. **保留原檢查不動**：`eval_error`（⓪）、`undelivered`（⓪）、空圖且 0 轉變（①）、`prompt_diffs`（④）。
2. **分類每一筆新轉變**：
   - `node_type == "pattern"` ⇒ 正常演化，**永遠不是破線**（保留現有行為：pattern 轉變從不報錯）。
   - `node_type in soul_types` 且 **同時**滿足：`node_type == "value"` 且 `lifecycle_state == "weakening"` 且 `node_id in expected_retirement` ⇒ 分類為 **EXPECTED_METABOLISM**。
   - 其餘任何 `node_type in soul_types`（belief/value/trait/essence，含非白名單的 value）⇒ 分類為 **UNEXPECTED** ⇒ **破線**。
   - `node_type` 既非 pattern 也非 soul_types（未知類型）⇒ 分類為 **UNEXPECTED** ⇒ 破線。
3. **目標狀態一律檢查**：`lifecycle_state in ("dormant", "superseded")` ⇒ **一律 UNEXPECTED**（即使 `node_id in expected_retirement`）⇒ 破線。
   語意：退役＝弱化，不是休眠/取代。
4. **報錯文案**（必須可被守衛測試用字串比對，見步驟 5）：
   - UNEXPECTED 的靈魂類轉變 ⇒ 追加 `"② 靈魂結構誤傷：%s"`（格式沿用現行的 `by_type` dict）
   - UNEXPECTED 的非 pattern 轉變 ⇒ 追加 `"② 出現非 pattern 轉變：%s"`
   - `dormant` / `superseded` 目標 ⇒ 追加 `"② 轉變目標含 %s"`（沿用現行文案）
   - **關鍵反例**：只有**不屬於** EXPECTED_METABOLISM 的 key node 轉變才報 `"③ 關鍵反例 %s 被轉變 %d 次"`；屬於 EXPECTED_METABOLISM 的 key node 轉變**不報破線**（但計入預期退役計數）。
   - **弱化包裝增量**：允許的增量 **＝ EXPECTED_METABOLISM 的筆數**。`weak_after - weak_before` 不等於預期退役筆數 ⇒ 報 `"④ 弱化包裝計數 %d → %d"`。等於則不報。
     （10/24 實測預期：`weak 1 → 10`、預期退役 9 筆 ⇒ 增量 9 ✔）
5. **回傳值**仍為 `list[str]`，空 list ＝ 全過。**既有守衛測試的既有斷言必須繼續成立**（例如 `test_soul_type_transition_is_flagged` 塞一筆 `{"node_id": "v1", "node_type": "value", ...}` 而**未**傳 `expected_retirement` ⇒ 仍必須報「靈魂結構誤傷」；`test_key_node_transition_is_flagged` 傳 pattern 型 key node 轉變 ⇒ 仍必須報「關鍵反例」）。

> ⚠️ **關鍵反例放行是本工單的推論條款**（依 Owner 2026-10-22 裁示「10 顆 legacy value（含關鍵反例 6a92c906）集體弱化」）。若 Owner 否決此條，10/24 夜槽仍會 FAIL。**執行者照做並在回報中標註此條為「主大腦推論，待裁」。**

### 步驟 4：`scripts/eval_lifecycle_cli.py` —— `main()` 串接與報告

在 `nodes = read_jsonl(sandbox / NODES_FILENAME)`（:320）之後新增：

```python
        edges = read_jsonl(sandbox / EDGES_FILENAME)
        expected_retirement = expected_retirement_set(nodes, edges)
        baseline_fails = baseline_violation(expected_retirement)
```

- 把 `baseline_fails` 併入最終 `fails`（`fails` 在 `assess()` 呼叫後組裝處）。
- 把 `expected_retirement=expected_retirement` 傳給 `assess(...)`。
- **JSON 輸出**（`--json`）新增兩個欄位：`"expected_retirement": sorted(expected_retirement)`、`"expected_retirement_count": len(expected_retirement)`。
- **文字報告**新增一段（位置：sidecar／轉變類型之後）：

```
  預期退役集合（EXPECTED_METABOLISM）= N 筆
    <每個 id 一行，附 node_type / agent_id>
    本次命中（轉入 weakening）= M 筆
```

- **既有四欄**（`returncode`／`eval_error`／`undelivered`／`VERDICT`）格式與位置**不得改動**（`tests/test_eval_lifecycle_cli.py` 與晨槽工具都在讀它們）。
- **不得**新增任何 CLI 參數、**不得**新增任何就地寫入路徑、**不得**改 `copy_graph` / `read_jsonl` / `_parse_simulated_time` / `prompt_snapshot` 的行為。

### 步驟 5：`tests/test_eval_lifecycle_cli.py` —— 新增守衛測試

在既有檔案**同一檔案**內新增測試類（🔴 **不要新建測試檔**：`tests/**` 新增 collectable 測試檔必須先 `git add`，而 worker 不得 `git add`）：

```python
class TestExpectedMetabolism(unittest.TestCase):
```

必備測試（每個都要有**具名斷言訊息**，失敗時能直接看出是哪條規則壞掉）：

| 測試名 | 斷言 |
|---|---|
| `test_retirement_set_derivation_is_exact` | 合成圖（10 顆合規 + 3 顆近似不合規：建立日不同 / `last_support` 較晚 / 有 support_propagation 邊）⇒ 推導結果**恰好**等於那 10 顆 |
| `test_retirement_set_never_exceeds_baseline` | 任意合成圖（含把 3 顆近似節點都做成合規的圖）⇒ 推導結果 ⊆ `EXPECTED_RETIREMENT_BASELINE` |
| `test_whitelisted_value_weakening_is_expected_not_failure` | 1 筆白名單 value→weakening ＋ `expected_retirement` 傳入 ⇒ `assess()` 回傳 `[]` |
| `test_non_whitelisted_value_weakening_fails` | 1 筆**非**白名單 value→weakening ⇒ 必須報「靈魂結構誤傷」 |
| `test_trait_or_essence_change_fails` | 1 筆 trait→weakening、1 筆 essence→weakening（即使傳入 `expected_retirement`）⇒ 必須報破線 |
| `test_key_node_in_retirement_set_is_not_a_break` | key node ＝ 白名單 id、value→weakening、`expected_retirement` 含它 ⇒ 不報「關鍵反例」 |
| `test_key_node_outside_retirement_set_still_fails` | key node ＝ 白名單 id、value→weakening、**未**傳 `expected_retirement` ⇒ 必須報「關鍵反例」 |
| `test_dormant_target_in_retirement_set_still_fails` | 白名單 id、value→**dormant** ⇒ 必須報破線（退役＝弱化，不是休眠） |
| `test_weak_prefix_delta_must_equal_expected_count` | 預期退役 1 筆但 `weak_after - weak_before == 2` ⇒ 必須報「弱化包裝計數」 |
| `test_weak_prefix_delta_zero_with_no_expected_is_ok` | 0 預期退役、weak 0→0 ⇒ 無失敗項 |
| `test_baseline_violation_is_self_checking` | 推導集合含一顆不在基線的 id ⇒ `baseline_violation()` 必須回報非空 |
| `test_cli_does_not_import_decay_thresholds` | AST：`scripts/eval_lifecycle_cli.py` 內**不得**出現 `timedelta`、`soul_elevation`、或任何天數門檻比較（衰減門檻凍結） |
| `test_no_src_module_imports_scripts_package` | AST：掃 `src/**/*.py`，不得有任何檔案 import `scripts.`（變異 in-place 才安全） |

> `assess()` 的既有測試（`TestAssessIsNotVacuous` 等 8 筆）**必須全部繼續綠**。

### 步驟 6：變異 harness（%TEMP%）

建立 `%TEMP%\expected_metabolism_mutations\mutation_harness.py`。**沿用本 repo 既有的變異 harness 作法**（參考 `%TEMP%\night_obs\cli_mutation_harness.py`、`%TEMP%\campaign_b_m6_harness.py`）：

- 每條變異：**複製** `scripts/eval_lifecycle_cli.py` 的備份 → in-place 套用變異 → 跑指定守衛測試 → 斷言**指定測試轉紅** → **位元還原**（還原後 sha256 必須 == 變異前 sha256，印出來）→ 下一條。
- 必須用 `try/finally` 保證任何例外路徑都會還原。
- 跑測試用 `.venv\Scripts\python.exe -m pytest -q <具體測試 id>`，**只跑指定測試**（不要跑全庫）。
- harness **必須**在最後印出「repo 檔案 sha256 與初始值相同」的證據。

**變異株清單（每條都要轉紅 + 位元還原）**：

| # | 變異內容 | 預期轉紅的守衛測試 |
|---|---|---|
| M1 | 移除 `last_support_ts == created_ts` 條件 | `test_retirement_set_derivation_is_exact`、`test_retirement_set_never_exceeds_baseline` |
| M2 | 把建立日條件改成「任意日期」（例如移除日期比對） | `test_retirement_set_derivation_is_exact`、`test_retirement_set_never_exceeds_baseline` |
| M3 | 移除「無 support_propagation 邊指向」條件 | `test_retirement_set_derivation_is_exact` |
| M4 | 把白名單 value 弱化誤判為破線（不放行） | `test_whitelisted_value_weakening_is_expected_not_failure` |
| M5 | 把**所有** value 弱化都視為預期（一律放行） | `test_non_whitelisted_value_weakening_fails` |
| M6 | 把 trait/essence 異動也視為預期 | `test_trait_or_essence_change_fails` |
| M7 | 移除 key node 的預期退役放行 | `test_key_node_in_retirement_set_is_not_a_break` |
| M8 | 移除「白名單內 dormant/superseded 仍算破線」規則 | `test_dormant_target_in_retirement_set_still_fails` |
| M9 | 放寬弱化包裝增量檢查（允許任意 delta） | `test_weak_prefix_delta_must_equal_expected_count` |
| M10 | 移除 `baseline_violation` 的執行期自我否決 | `test_baseline_violation_is_self_checking` |
| M11 | 把「規則不得含天數門檻」守衛的對應實作改壞（在推導函式內加入 `timedelta` 天數比較） | `test_cli_does_not_import_decay_thresholds` |

> M11 的設計意義：若有人日後「順手」把衰減門檻複製一份到判準層（例如「距今超過 N 天就算預期退役」），守衛必須立刻轉紅。**這是「衰減門檻凍結」的可執行化。**

### 步驟 7：沙盒實測對帳（驗收用，必須貼出實際輸出）

用 `.venv\Scripts\python.exe` 跑 **三個** 模擬時鐘（CLI 是唯讀來源 + 自建沙盒，不會寫生產）：

```powershell
$repo='C:\Users\bbfcc\.local\bin\soul-os-harness'
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-24T02:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-23_night.txt"
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-25T02:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-24_night.txt"
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-23T12:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-23_morning.txt"
```

**預期結果（必須逐項對上）**：

| 模擬時鐘 | 轉變 | 預期退役 | weak_prefix | VERDICT | exit code |
|---|---|---|---|---|---|
| `2026-10-24T02:00:00+00:00`（10/23 22:00 夜槽） | +17 全 pattern | 0 筆 | 1 → 1 | **PASS** | 0 |
| `2026-10-25T02:00:00+00:00`（10/24 22:00 夜槽） | +48＝pattern 39 ＋ **value 9** | **9 筆**（9 顆 value 全在白名單內） | 1 → **10** | **PASS** | 0 |
| `2026-10-23T12:00:00+00:00`（10/23 08:00 晨槽） | +7 全 pattern | 0 筆 | 1 → 1 | **PASS** | 0 |

> 基準（主大腦 2026-10-23 實測，**改判準前**）：第二列原本是 `VERDICT: FAIL` / exit 1（value 純度 9≠0、key_node transitions=1）。改判準後必須變成 PASS —— 這是本票的**核心交付證據**。
> ⚠️ 預測是「當前圖譜狀態」的推算；實測數字可能因圖譜已長而略有差異。**若實際轉變數與上表不同，執行者必須在回報中列出實際值，並確認差異來自圖譜演化（節點數不同）而非判準邏輯錯誤**（判準邏輯正確與否由守衛測試與 M1～M11 決定）。

### 步驟 8：文件登錄

`logs/ENGINEERING_STATE.md`：

1. **追加** §0.1.53（**不得覆寫**既有條目 —— canonical 不覆寫慣例），內容須包含：
   - **接縫期望模型更正**：§0.1.52 的「46 = 42 常態 + 4 一槽延遲補發」是錯的。真相＝`_ELEVATION_EMITTED` 是**行程內記憶體**的 at-most-once 登記表（`src/soul/life_thread_orchestrator.py:172`，契約 §4.6）⇒ 歷史的 35/35/42/42 全是「**重啟後首槽的全量重發**」；無重啟的槽只發「上一槽發射時刻之後新完成、且尚未發射過」的線頭。正確期望模型＝「接縫(槽) ＝ 上一槽發射時刻之後新完成、且尚未發射過的線頭數」，2026-10-23 晨槽 ＝ 4（實測 4）。集合閉包：期望 48 ＝ 本槽發射 4 ＋ 上一槽已發射 42 ＋ 窗口後新完成 2。
   - **本次裁定**：Owner 2026-10-23 採納「選項（乙）改判準」；預期退役集合語意（4 條件）、基線 10 顆 id、執行期自我否決（`baseline_violation`）、一票否決規則。
   - **10/24 夜槽預期**：value 9 筆屬預期代謝（weak_prefix 1→10），**零干預紀律**（禁偽造 Pattern 邊、禁手動消紅、禁為讓純度歸零而動門檻）。
2. 在 §0.1.52 條目**末尾追加一行**指引（append，不覆寫原內容），例：
   `> ⚠️ 2026-10-23 更正：接縫期望模型已修正（見 §0.1.53）——「42 常態」實為重啟造成的全量重發。`

---

## 驗收（完成的定義）

全部滿足才算完成：

1. `scripts/eval_lifecycle_cli.py` 的四欄（`returncode`／`eval_error`／`undelivered`／`VERDICT`）**格式與位置未變**，既有守衛測試 **8 筆全綠**。
2. 新增守衛測試 **13 筆全綠**（步驟 5 表格）。
3. 變異 **M1～M11 全部轉紅**，且每條變異後 **repo 檔案 sha256 與初始值逐位元相同**（harness 印出證據）。
4. 沙盒實測三個模擬時鐘的 **VERDICT 皆為 PASS**，且 **10/24 夜槽那列的 weak_prefix = 1 → 10、預期退役 = 9 筆**。
5. **全庫回歸**（`.venv\Scripts\python.exe -m pytest -q tests`）＝ **0 failed**（基線 7188 passed / 0 failed / 24 skipped / 32 subtests；本票新增 13 筆 ⇒ 預期 **7201 passed / 0 failed**）。
6. `logs/ENGINEERING_STATE.md` 已追加 §0.1.53，且 §0.1.52 只有 append 沒有覆寫。
7. **`src/**` 零改動**（`git diff --name-only HEAD` 不得出現任何 `src/` 開頭的檔案）、`data/**` 零改動、`soul-elevation` repo 零改動。
8. `%TEMP%\morning_obs\morning_audit.py` 零改動。

---

## 測試

```powershell
$repo='C:\Users\bbfcc\.local\bin\soul-os-harness'
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'

# 本票守衛測試（快速）
& "$repo\.venv\Scripts\python.exe" -m pytest -q "$repo\tests\test_eval_lifecycle_cli.py"

# 變異 harness（必須放 %TEMP%，R5）
& "$repo\.venv\Scripts\python.exe" "$env:TEMP\expected_metabolism_mutations\mutation_harness.py"

# 全庫回歸（🔴 避開生產槽：08:00／22:00 EDT 槽窗內不要跑）
& "$repo\.venv\Scripts\python.exe" -m pytest -q "$repo\tests"

# 沙盒實測對帳（三個模擬時鐘）
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-24T02:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-23_night.txt"
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-25T02:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-24_night.txt"
& "$repo\.venv\Scripts\python.exe" "$repo\scripts\eval_lifecycle_cli.py" --simulated-time '2026-10-23T12:00:00+00:00' --report "$env:TEMP\expected_metabolism_mutations\pred_2026-10-23_morning.txt"
```

🔴 **測試數字一律以 repo 的 `.venv\Scripts\python.exe` 為準**（PATH 上的全域 pytest 是另一個世界，收集數會對不上）。

🔴 **全庫回歸必須避開生產槽窗**（08:00 EDT 前 15 分 ～ 09:30 EDT、22:00 EDT 前 15 分 ～ 23:00 EDT）—— 磁碟與 CPU 競爭會拖慢生產排程。若被時段擋住，**照實回報並等待**，不得在槽窗內硬跑。

---

## 不做（Out of Scope）

- ❌ **不改** `src/**` 任何檔案（含 `elevation_adapter.py`、`lifecycle_overlay.py`、`emergent_projection.py`）。
- ❌ **不改** `soul-elevation` repo 任何檔案；**不動**任何衰減門檻（`engine.py` 的 7 天上界等）。
- ❌ **不改** `%TEMP%\morning_obs\morning_audit.py`（Owner 明令：留待後續維護窗口，當前禁止熱更）。
- ❌ **不**動 `tests/test_observability_completeness_1.py` 的 `allowed` / `frozen_dirs`（本票不動 `src/**` ⇒ 不需要）。
- ❌ **不**新增任何 CLI 參數、**不**新增任何就地寫入路徑、**不**動 `copy_graph` / `read_jsonl` / `_parse_simulated_time` / `prompt_snapshot` / `agent_ids`。
- ❌ **不**改 `WEAKENING_TONE_PREFIX`、`NON_PROJECTED_LIFECYCLE_STATES`、`PROJECTABLE_NODE_TYPES`、投影規則。
- ❌ **不**動 `data/**`、**不**重啟服務、**不**殺任何行程、**不**清空或繞過 `_ELEVATION_EMITTED`。
- ❌ **不**觸發 synthetic diary/dream、**不**跑 `scripts\manual_*.py`。
- ❌ **不** `git add` / `git commit` / `git push`（由主大腦處理）。
- ❌ **不**為了讓 CLI 報綠而放寬判準、**不**為了讓弱化包裝歸零而動程式碼／資料／門檻。
- ❌ **不**新增測試檔到 `tests/`（本票的守衛測試一律加進既有 `tests/test_eval_lifecycle_cli.py`）。

---

## Frozen Contract 注意

本票**不得**碰觸下列任何一項（`tests/test_observability_completeness_1.py` 的 `frozen_dirs` 11 筆）：

```
src/agency/stages.py            src/agency/trigger_handler.py
src/agency/event_handler.py      src/agency/dream_handler.py
src/agency/diary_handler.py      src/inner_life/submission_gate.py
src/social/identity_firewall.py  src/social/producer_gate.py
src/world/middleware.py          src/memory/sage/writer.py
src/soul/decision.py
```

另：契約 §4.6（`_ELEVATION_EMITTED` at-most-once 語意）**本票只讀不改** —— 本票改的是**判準層**（如何判定），不是**機制層**（何時發射）。

---

## 回報格式

1. **改動檔案清單**（repo 內逐條列，含行數增減；%TEMP% 的 harness 也要列）
2. **守衛測試結果**：新增 13 筆 + 既有 8 筆，各打了幾筆、紅幾筆
3. **變異結果**：M1～M11 逐條 →（轉紅的測試名）／還原後 sha256 是否逐位元相同
4. **沙盒實測對帳**：三個模擬時鐘的四欄（`returncode`／`eval_error`／`undelivered`／`VERDICT`）＋ 預期退役筆數 ＋ weak_prefix 前後值
5. **全庫回歸**：`.venv` 直譯器、`-q tests`、passed／failed／skipped 數字 ＋ 耗時
6. **文件**：§0.1.53 的段落標題 ＋ §0.1.52 的 append 行
7. **Frozen Contract 檢查**：`git diff --name-only HEAD` 完整輸出（必須沒有 `src/`、沒有 `data/`）
8. **主大腦推論待裁項**：「關鍵反例在預期退役集合內時不報破線」這條，逐字標明
9. **踩到的意外行為**（若有）

---

## 附註：為什麼這條工單是「改判準」而不是「放寬判準」

Owner 的核心駁斥是：**把「CLI 硬性 FAIL」人為視為「預期並忽略」會誘發告警疲勞，並摧毀守門價值**。因此本工單的設計必須同時滿足：

- **放行有界**：只有「10 顆指定 Legacy Value ＋ 四條件全滿足 ＋ 轉入 weakening」才放行；任何超出 ⇒ 破線。
- **放行可被推翻**：規則推導結果若超出核准基線 ⇒ CLI **執行期自我否決**（`baseline_violation`）⇒ 日後有人放寬規則，CI 與執行期都會攔。
- **放行不留死角**：M1～M11 變異株涵蓋「白名單漏抓」「非預期 Value 混入」「純度誤判」「門檻被複製進判準層」四類。
- **退役語意不含糊**：`dormant` / `superseded` 永遠是破線（退役＝弱化，不是休眠/取代）。
