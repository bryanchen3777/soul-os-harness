# SIM-ACCEL-THREAD-DISSOLVE — 實作紀錄

> 工單：`logs/SIM-ACCEL-THREAD-DISSOLVE-WORK-ORDER.md`（2026-10-20）
> 執行者：Worker（分支工作階段 `mvs_e6913581fa29433cade433ba2567d424`）
> 狀態：**功能驗收全綠**；全庫回歸與 5 條變異證據見下。
> 解譯器口徑：全部數字來自 **repo 內 `.venv`**：`.venv\Scripts\python.exe`（Python 3.11）。

---

## 0. 一句話

增量發射機制面對**未來**的新線頭時，**不需要等真實生活推進 14 天**就能證明它 100% 暢通：
在沙盒 ＋ 虛擬時鐘 ＋ 腳本化 LLM 下，**35 筆歷史線頭零重發、本次恰好新增 1 個事件（第 36 筆）**，
新事件帶正文與感悟，`llm_calls == 1` 且 **0 次真實推理**，生產 27 個檔案 sha256 逐位元未動。

---

## 1. 🔴 前提更正：原工單 §0.2 / §3.1 步驟 5-6 的「虛擬時鐘讓線頭轉 completed」不成立

### 1.1 錯在哪

原工單 §0.2 宣稱：「命中存活軸（`age > 14 天`）⇒ `target_status = completed`、`should_mutate = True`」。
**實測為假**：`src/soul/life_thread_dissolution.py` 步驟 4（`:509-543`）明定，age／idle 兩軸在
`allow_soft_archive=False` 時**只是諮詢訊號**：

```python
if reason is not DissolutionReason.NONE:
    if allow_soft_archive is True:      # ← 只有顯式 opt-in 才會變更狀態（→ dormant）
        ...
    return DissolutionEvaluation(
        target_status=ThreadStatus.ACTIVE,
        reason=reason,
        should_mutate=False,             # ← advisory：預設不變更任何狀態
        reflection_prompt=None,
        extra_metadata=extra,            # ← extra["advisory"] is True
    )
```

⇒ **active 線頭永遠不會因時間流逝而轉 completed**。原工單的驗收條款
「`virtual_now = created_at + 14d + 1s` ⇒ `should_mutate is True`、reason = `TIME_HORIZON_EXCEEDED`」
只在**線頭已經是終態**（`completed`／`abandoned`）且 `dissolved_at is None` 時才成立
（M2 步驟 2 的真溶解路徑）。

### 1.2 怎麼發現的

主大腦 2026-10-20 22:50 的工單更正已指出；本 worker 亦在讀碼階段獨立撞到同一處
（讀 `life_thread_dissolution.py:393-408` 的決策序 docstring 與 `:489-543` 的實作時發現）。
本 worker **未在錯誤前提上打補丁**：整份 CLI 依更正後的槓桿重寫。

### 1.3 更正後的槓桿：**接線層的既有寫入路徑**（與工單更正 §3.1 步驟 5 有一處**不得不為**的偏差）

工單更正（2026-10-20 22:50）要求用「M4 的 LLM 動作」把線頭轉入終態。**本 worker 實作時發現
這條路徑在本 repo 的凍結契約下不可行**，證據如下（三條都是 repo 既有契約測試的失敗訊息）：

| 契約測試 | 契約內容 | 本票 CLI 的原始做法 | 結果 |
|---|---|---|---|
| `tests/soul/` M4 契約測試 `test_module_declares_m5_wiring_and_reuses_existing_channel` | **M4 入口的呼叫端集合 ＝ 精確等值 `{src/soul/life_thread_orchestrator.py}`** | `scripts/sim_accel_thread_cli.py` 直接呼叫 M4 入口 | 🔴 紅：`['scripts/sim_accel_thread_cli.py', 'src/soul/life_thread_orchestrator.py'] != ['src/soul/life_thread_orchestrator.py']` |
| `tests/soul/` M4 契約測試 `test_module_is_not_wired_into_production_paths` | **M4 的生產 importer 集合 ＝ 精確等值**（`scripts/run_server.py` ＋ 接線層 ＋ orchestrator） | 本檔 `import` 了 M4 | 🔴 紅：`Left contains one more item: 'scripts/sim_accel_thread_cli.py'` |
| `tests/soul/` M1 契約測試 `test_s2_1_module_not_imported_by_production_paths` | **M1 的生產 importer 集合 ＝ 精確等值**（M4 ＋ 接線層 ＋ orchestrator） | 本檔 `import` 了 M1 | 🔴 紅：`['scripts/sim_accel_thread_cli.py', ...] != [...]` |
| 同檔 `test_legacy_git_grep_false_green_is_fixed` | **文字層**：`git grep life_thread_origins -- src scripts configs` 的每一行都必須落在白名單或純文字例外清單 | 本檔的註解引用了該模組路徑 | 🔴 紅：`出現白名單外的 M4 引用` |

⇒ **`scripts/**` 下的沙盒 CLI 不得呼叫 M4 入口、不得 import M1／M4、連註解裡的模組路徑都不行。**

**改走 orchestrator 的 `run_slot_pipeline`（唯一的合法 M4 呼叫端）為何也不可行**：
目標線頭（`agent_mahiru` 兩條 active 線頭）的 `origin_type` ＝ **`world_collision`**
⇒ 其 M4 prompt 需要**世界感知種子**；沙盒內沒有 `data/world/perception_trace.jsonl`
⇒ `build_origin_prompt` 建不出 prompt ⇒ `prompt_available=False` ⇒ **0 LLM、0 轉入**。

⇒ **本票實際採用的路徑（＝原工單 §3.1 步驟 8 的既有 API 做法）**：

```
M2 諮詢訊號（虛擬時點 created_at + 14 天 + 1 秒）⇒ 存活軸命中（advisory，不變更狀態）
M1.append_transition(agent_id, thread_id, "completed", updated_at=virtual_now)   ← 既有 API
  └─ 接線層 dissolve_hook（旗標 LIFE_THREAD_CONSOLIDATION_ENABLED 守門）
       └─ _consolidate_once → M2 步驟 2（真溶解，should_mutate=True）
            └─ 執行層 consolidate_terminal_thread(llm_call=腳本化 proxy)
                 └─ 事實寫入沙盒 SAGE → sage_fact_id 回填 M1
接縫（旗標 LIFE_THREAD_ELEVATION_ENABLED=1）→ maybe_emit_elevation_events → 第 36 個事件
```

**M1／M4／M2／接線層一律經 orchestrator 的合法別名取得**（`orch.lt`／`orch.lt_origins`／
`orch.lt_diss`／`orch.lt_wiring`）⇒ 本檔的 import 面只剩 orchestrator 與 `src.inner_life.*`，
四條契約全綠（見 §5.2）。

> **偏差的影響評估**：被省略的只有「M4 的 LLM 動作決定轉入終態」這一步；
> **接縫增量機制本身（M2 真溶解 → 執行層沉澱 → SAGE 事實 → M1 回填 → 接縫發射）
> 100% 走的是生產接線層的同一條路**，未做任何簡化或繞過。

### 1.4 虛擬時鐘的**新角色**

虛擬時點仍為 `created_at + 14 天 + 1 秒`（`age_seconds = 1209601`），但它的作用是
**讓槽位時間可控**（讓 M2 的 age 軸在確定性的一點上命中），**不是**靠它讓線頭完成。
目標線頭的挑選條件也隨之修正（原條件「`created_at + 14d + 1s` 已過於 now」在
「虛擬時鐘由 `created_at` 反推」的前提下是循環的、恆不成立）⇒ 改為
「`status == "active"` 且 `dissolved_at is None`、且 `created_at` **最舊**」（可重現、確定性）。

---

## 2. 改動檔案清單（3 個，全在工單授權範圍內）

| # | 檔案 | 行數（實測） | 說明 |
|---|---|---|---|
| 1 | `scripts/sim_accel_thread_cli.py`（新增） | 1081 | 兩階段沙盒 CLI：父行程備沙盒 ＋ 算生產 sha256 before → 換行程 → 子行程自我檢查 → 跑模擬 → 對帳 |
| 2 | `tests/test_sim_accel_thread.py`（新增） | 570 | 12 條守衛測試 ＋ 5 條變異實驗的載體 |
| 3 | `logs/SIM-ACCEL-THREAD-DISSOLVE-IMPLEMENTATION.md`（新增） | 475 | 實作紀錄 |

**未動**（工單 §2 明列不得動）：`src/soul/life_thread_orchestrator.py`、
`src/soul/life_thread_dissolution.py`、`src/soul/life_thread_dissolution_exec.py`、
`src/soul/life_threads.py`、`src/paths.py`、`src/inner_life/**`、`data/**`、
`personas/**`、`configs/**`、`docs/**`。

**版本控制**：三個檔案已 `git add`（**未 commit、未 push**）⇒ 理由：
repo 既有守衛 `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked`
要求 `tests/**` 底下**可被 pytest 收集的 `.py` 必須已被 git 追蹤**（該守衛自己的訊息也寫
「Fix by tracking the file (git add <path>)」）；同 repo 的另一張票（SIM-ACCEL-RECALL-30D）
亦採同樣做法。**commit 由主大腦驗證後自行執行。**

---

## 3. 沙盒化紀律（逐條；與 `scripts/eval_lifecycle_cli.py` 同款）

1. **唯一寫入目標 ＝ 沙盒**（`SOUL_OS_DATA_DIR` 指向 `%TEMP%` 之下）。
   **沒有任何就地寫入旗標**（AGENTS.md 行程安全鐵律 12）。
2. 🔴 **沙盒副本一律 `shutil.copy2` 實體複製；零硬連結、零符號連結、零 inode 共享**
   （SIM-ACCEL-RECALL-30D 事故的根因就是「沙盒＝來源」的 inode 共享：
   硬連結讓 CLI 的 append 直接落在生產 inode 上 ⇒ 805 筆假事件 ＋ 513 顆節點狀態被改）。
   本票的 `prepare_sandbox()` 全部走 `shutil.copy2`；**沒有** `os.link`／`symlink`。
   ⇒ 另加兩道可驗證的證據：
   - **inode 層級實測**（見 §4.8）：5 個沙盒檔與生產檔的 `st_ino` **全部不同**，
     雙側 `st_nlink` 皆為 1；
   - **守衛測試** `test_sandbox_copies_are_independent_inodes`（見 §5.1 第 12 條）。
3. **0 真實 LLM 呼叫**：
   - M5（接線層沉澱）：LLM proxy 注入 `ScriptedM5Proxy`（回 M2 沉澱契約 JSON）；
   - `ScriptedM5Proxy` 對**非預期 prompt** 一律 `raise`（杜絕真實推理與花費）。
4. **0 網路、0 伺服器、0 埠**；不 import `subprocess`／`requests`／`httpx`／`socket`／
   `uvicorn`／`fastapi`（有守衛測試逐字掃描）。
5. **生產檔案只讀**：sha256 前後逐位元對帳，不符即 exit 4；
   **每一條變異實驗也獨立複核一次**（見 §6.1）。
6. 沙盒內容：`soul/<agent>/life_threads.jsonl`（**全部 10 個 agent**，全域 35 條已發射線頭要能重現）、
   `memory/<agent>/graph.sqlite(+-wal)`（**只有目標 agent**，供事實寫入 ＋ 感悟回讀）、
   `elevation/`（節點／邊；**不複製** 40MB 的 trace）。

### 3.1 Windows 上的換行程機制（實測，2026-10-20）

| 機制 | 結果 |
|---|---|
| `os.execve` / `os.spawnve`（帶**自訂 env 區塊**） | 🔴 **access violation `0xC0000005`**，子行程根本沒起來 |
| `os.execv`（繼承 env） | 可用，但**會吞掉子行程退出碼**（子行程 exit 3 ⇒ 父行程 exit 0）⇒ CLI 的失敗契約失效 |
| **`os.spawnv(P_WAIT)`**（本票採此） | ✅ 新行程 ＋ 繼承 env ＋ **回傳子行程退出碼** |

⇒ 隔離語意與工單指定的 `os.execve` **完全相同**（env 在子行程**啟動前**就決定；
`data_root()` 的行程期快取是新的）。argv 只放 `[exe, "-m", "sim_accel_thread_cli"]`
（`_spawnv` 不做引號處理，含空白的 argv 會被截斷），其餘參數全走 env
（`SIM_ACCEL_PHASE`／`SIM_ACCEL_SANDBOX`／`SIM_ACCEL_AGENT`／`SIM_ACCEL_SLOT`／`SIM_ACCEL_TARGET_THREAD`）。

### 3.2 三道隔離閘（`assert_sandbox_isolated`，純函式、可單獨測試）

1. 沙盒必須在 `tempfile.gettempdir()` 之下；
2. `SOUL_OS_DATA_DIR` 必須**逐字**等於沙盒；
3. `data_root()`（**行程期快取**）解析出的路徑必須**逐字**等於沙盒。

🔴 三道閘都是「同一個值 vs 同一個值」⇒ 不會出現 2026-10-19 事故那種
「兩個各自會漂移的值互相比」而恆為 False 的假通過。

### 3.3 CLI 介面（工單 §3.4）

```
.venv\Scripts\python.exe scripts\sim_accel_thread_cli.py --agent mahiru [--target-thread <id>]
                                                             [--out-dir <dir>] [--slot morning|night] [--json]
```

- `--agent`：可省略 `agent_` 前綴（`mahiru` ⇒ `agent_mahiru`）。
- `--target-thread`：指定目標線頭；不給則挑 `created_at` **最舊**的 active 線頭。
- `--out-dir`：沙盒根（預設 `%TEMP%\sim_accel\thread_<UTC時間戳>`）。
- `--json`：**stdout 只印純 JSON**（報告全文）；所有診斷訊息一律走 **stderr**
  （父行程與子行程都如此 ⇒ stdout 可直接被 `ConvertFrom-Json` 解析）。
- 退出碼：全部斷言通過 ⇒ **0**；斷言失敗 ⇒ 1；找不到合適目標線頭 ⇒ 2；
  沙盒不在 temp 之下／env 漂移 ⇒ 3；生產 sha256 不符 ⇒ 4。

**實測**（`--json`）：

```
RC=0  ok=True  checks=10  failed=0
seeded=35  seam_pre=0  seam_post=1  seam_again=0
identical=True  files=27  real_llm=0
```

### 3.5 🔴 本票踩到並修正的一條凍結契約：T54（執行層的 import 面）

第一版的 `tests/test_sim_accel_thread.py` **直接 `import` 了 `src.soul.life_thread_dissolution_exec`**
（為了直接測 M3／M4 兩條執行層語意）⇒ 觸發凍結契約
`tests/soul/test_life_thread_dissolution_exec.py::test_t54_importers_are_the_wiring_module_and_its_two_test_files`：

> 「全庫（src/scripts/tests/clients）import 執行層的檔案＝**精確集合等值**：
> 1 個生產檔（接線層）＋ 2 個測試檔。**多一個或少一個都紅**。」

該契約的用意是「誰在生產呼叫付費沉澱路徑」必須收斂到單一檔案 ⇒ 新增第 4 個 importer 即違約。

**修正**（未動契約測試檔、未動執行層）：

- 本檔**不再直接 import 執行層**；改經**唯一合法的 importer**取得：
  `from src.soul import life_thread_consolidation_wiring as lt_wiring` → `lt_wiring.lt_exec`
  （接線層本來就 `from src.soul import life_thread_dissolution_exec as lt_exec`）。
- 變異實驗的變異體是「**來源字串替換後在獨立命名空間 exec**」⇒ **沒有 import 陳述句**，
  AST 掃描看不到它 ⇒ 變異機制本身不違約。
- 修正後：`pytest -q tests/soul/test_life_thread_dissolution_exec.py` ⇒ **113 passed**（T53／T54 綠）。

⇒ 教訓：**新增測試檔前先查「誰被允許 import 這個模組」的契約測試**；本 repo 已有此類契約。

### 3.6 兩處必要的工程判斷（非工單原文，說明理由）

1. **事實必須真的寫進沙盒的 `graph.sqlite`**，不能只回一個假的 `fact_id`。
   理由：接縫的感悟是 `_elevation_reflection()` → `GraphStore.get_fact(fact_id).object`
   **唯讀回讀**；若事實不在庫裡，感悟恆為空字串 ⇒ 工單 §4.3 的
   「新事件帶有 reflection」在數學上不可能成立。故事實寫入走**生產接線層的同一條路**
   （`_write_fact` → `MemoryWriter.add_fact`），只是路徑落在沙盒。
2. **沉澱後必須 flush SAGE 的 pending writes**（`GraphStore.flush_all_live()`，公開 API）。
   理由：`GraphStore` 的寫入是**延遲 commit**（MEM-VISIBILITY-C2：「快照隔離，flush 後才可見」）
   ⇒ 事實已寫入但未 commit 時，接縫那條**另一條連線**讀不到 ⇒ 感悟空字串。
   這是**生產同樣的行為**（生產的感悟多半也是空的）；本票為滿足 §4.3 而在沙盒內顯式 flush。

---

## 4. 沙盒實測（`--agent mahiru`，沙盒 `%TEMP%\sim_accel_final`）

### 4.1 M2 判定（三次裁決，逐次不同語意）

| 取樣點 | 線頭狀態 | `should_mutate` | `target_status` | `reason` | `advisory` |
|---|---|---|---|---|---|
| `created_at + 14d + 1s`（推進前） | `active` | **False** | `active` | `time_horizon_exceeded` | **True** |
| 溶解當下（已轉終態、未溶解） | `completed` | **True** | `completed` | `time_horizon_exceeded`（`terminal_reason` 覆寫） | False |
| 溶解當下（`terminal_reason=None`，**接線層實際值**） | `completed` | True | `completed` | `goal_achieved` | False |

> 註：接線層實際呼叫 `evaluate_thread_dissolution(..., terminal_reason=None)` ⇒ reason 是
> `goal_achieved`。工單 §4.1 要求的 `reason=TIME_HORIZON_EXCEEDED` 由**同一個函式**的
> `terminal_reason="time_horizon_exceeded"` 覆寫參數取得（既有 API，`_OVERRIDE_REASON_VALUES` 合法值）。
> 兩者都記在 `sim_accel_result.json`（`m2_terminal` 與 `m2_terminal_default_reason`）。

### 4.2 虛擬時點與 14 天邊界

```
created_at  = 2026-10-13T12:01:08.988917+00:00
virtual_now = 2026-10-27T12:01:09.988917+00:00
age_seconds = 1209601  (= 14 * 86400 + 1)
```

⇒ 存活軸邊界是**嚴格大於**（`>`）：14 天整（`1209600`）**不算**超限，`+1` 秒才算。
（守衛測試 `test_m2_age_boundary_is_strictly_greater` 以 `updated_at = created_at + 13d`
排除無進展軸，單獨驗存活軸邊界。）

### 4.3 接縫數量

| 階段 | 事件數 | 說明 |
|---|---|---|
| 登記表播種 | **35** | 沙盒內「`completed` 且有 `sage_fact_id`」的線頭（生產全域 35 條） |
| 接縫「前」掃描（10 個 agent） | **0** | 35 筆歷史線頭**零重發**（逐 agent 全 0） |
| 接縫「後」掃描 | **1** | 第 36 筆（新推進的那條線頭） |
| 接縫「再」掃描 | **0** | at-most-once 成立 |
| 登記表大小 | 35 → **36** | 只多 1 筆 |

不預先播種（模擬全新行程）⇒ 接縫發射**全部 36 筆**（守衛測試
`test_seam_without_seeding_emits_all_36`）。

### 4.4 新事件內容（實測片段）

```
事件 thread = b3eba984-1406-449d-8721-3aec3328b932
  正文（narrative，193 字）：電視開著，播的是巴西第二輪投票前那場辯論，兩個人不講政策，只顧著互相人身
  攻擊。真昼一邊聽一邊把抽屜推回去，順手把遙控器的電池換成新的，舊的兩顆丟進「舊」那一格——反正還能
  撐一陣子。手寫清單上補了一行：遙控器用三號兩顆。……
  感悟（reflection，44 字）：把擱著的事收拾完之後，其實沒有什麼大不了——我只是把手上一直拖著的那
  一樣東西放回它該在的位置。
```

正文取自該線頭 M1 的 `narrative_content`（**未經 LLM**）；感悟取自沙盒 SAGE 事實的 `object`。

### 4.5 LLM 呼叫

```
llm_calls = { m4_scripted: 1, m5_scripted: 1, total_scripted: 2, real_llm_calls: 0 }
consolidation = { status: "consolidated", llm_calls: 1, reason: "ok",
                  fact_confidence: 1.0, fact_origin: "lived_experience" }
```

### 4.6 生產 sha256 對帳

```
生產比對範圍：data/soul/**/life_threads.jsonl ＋ data/memory/**/graph.sqlite ＋ data/elevation/*.jsonl
比對檔案數：27
每次沙盒執行的 before → after：identical = True（27/27 逐位元相同）
跨多次執行的獨立對帳（sim_accel_run1 / sim_accel_dbg2 / sim_accel_final）：差異 0
```

> ⚠️ **觀察到的生產檔案變動（非本沙盒造成，證據如下）**：
> 執行期間生產 `data/elevation/{nodes,edges,trace,projection_trace}.jsonl` 有變動（mtime `2026-10-21T03:18:19Z`）。
> 內容比對顯示：生產新增的是 **`agent_yua`**（日文內容）與 **`agent_id="default"`**（天氣事實）
> 節點 ⇒ **生產排程自身**的寫入；而本沙盒新增的是 **`agent_mahiru`** 的 2 個節點
> （沉澱文字 ＋ 線頭正文），且**寫在沙盒的 `elevation/` 內**（沙盒 `elevation_nodes.jsonl` 516 行
> ＝ 生產拷貝 514 行 ＋ 2 行新增）。每次執行的 before→after 皆為 27/27 相同
> ⇒ **本沙盒 0 次寫入生產**。

### 4.8 🔴 inode 層級的獨立複核（回應 SIM-ACCEL-RECALL-30D 事故）

該事故的根因是「沙盒＝來源」的 **inode 共享**（硬連結）⇒ append 落在生產 inode 上。
本票的沙盒一律 `shutil.copy2`（實體複製），實測結果：

```
檔案                                    prod ino            sbx ino             判定
life_threads.jsonl                      8162774325194777   7599824371477235   ✅ 獨立 inode
graph.sqlite                            121034239985744398  7036874418058468   ✅ 獨立 inode
elevation_nodes.jsonl                   2251799815226123   6473924464638051   ✅ 獨立 inode
elevation_edges.jsonl                   5629499534530959   5629499534506355   ✅ 獨立 inode
elevation_lifecycle_events.jsonl        4785074606250883   7036874418059956   ✅ 獨立 inode

共用 inode 的檔案數 = 0（必須為 0）；雙側 st_nlink 皆為 1
```

⇒ 另有一條**守衛測試**把該性質釘死：`test_sandbox_copies_are_independent_inodes`
（斷言 `st_ino` 不同 ＋ 雙側 `st_nlink == 1`）。

### 4.9 每一條變異實驗的生產 sha256 獨立複核

SIM-ACCEL-RECALL-30D 的教訓：「**綠燈不算證據**，變異實驗跑完必須獨立複核生產 sha256」。
本票對 **5 條變異 ＋ 1 條對照組**逐案做前後對帳（每次都比對生產 27 個檔案）：

| 案例 | pytest 退出碼 | 生產 sha256 變動 | 結論 |
|---|---|---|---|
| `advisory_as_dissolution` | 1（紅，符合預期） | **0** | ✅ 生產未動 |
| `at_most_once_removed` | 1（紅，符合預期） | **0** | ✅ 生產未動 |
| `llm_shape_accepted` | 1（紅，符合預期） | **0** | ✅ 生產未動 |
| `no_llm_guard_removed` | 1（紅，符合預期） | **0** | ✅ 生產未動 |
| `sandbox_check_removed` | 1（紅，符合預期） | **0** | ✅ 生產未動 |
| （對照組・無變異） | 0（綠，符合預期） | **0** | ✅ 生產未動 |

### 4.10 🔴 必須主動回報的副作用：5 次 python.exe 崩潰 dump（我的實驗造成）

本 worker 在**確定換行程機制**時，對 `os.execve`／`os.spawnve`（帶自訂 env 區塊）做了最小化實測，
在這台 Windows 上兩者都會 **access violation（`0xC0000005`）**。該崩潰被機器的
**OS 崩潰收集器**接住並落地：

```
data\logs\crash_dump_collector.log
  [2026-10-20 23:08:02] INFO collected AppCrash_python.exe_... → data\crash_dumps\... (3 筆)
  [2026-10-20 23:13:02] INFO collected AppCrash_python.exe_... → data\crash_dumps\... (2 筆)

data\crash_dumps\python.exe.23364.dmp   2026-10-21 03:03:41Z  59065585 bytes
data\crash_dumps\python.exe.17928.dmp   2026-10-21 03:07:05Z  26565583 bytes
data\crash_dumps\python.exe.1884.dmp    2026-10-21 03:07:47Z  26553289 bytes
data\crash_dumps\python.exe.11592.dmp   2026-10-21 03:08:33Z  26598351 bytes
data\crash_dumps\python.exe.14252.dmp   2026-10-21 03:08:36Z  26622927 bytes
```

⇒ **5 個 dump、合計約 165 MB**，時間點與本 worker 的 `os.execve`／`os.spawnve` 實驗**逐筆吻合**
（23:07-23:13 local）。**本 worker 未刻意寫入／改名／刪除任何 `data/**` 檔案**；
這些 dump 是作業系統的崩潰報告機制所產生。
同一批收集器的保留策略在 23:08:03 與 23:13:03 **修剪了 5 組 2026-09-12 的舊收集**
（收集器自己的行為，非本 worker 所為）。

🔴 依 AGENTS.md 行程安全鐵律 8（`data/**` 是生產狀態，發現疑似殘留**只回報**、
由主大腦裁決）⇒ **本 worker 不清理、不改名、不刪除這些 dump**，僅回報。

**這也是「`os.execve` 在本機不可用」的直接證據**：同一個 API 讓 python.exe 崩潰 5 次。
本 ticket 最終採 `os.spawnv(P_WAIT)`（不崩潰、且回傳子行程退出碼）。

---

## 5. 測試結果

### 5.1 新測試檔

```
.venv\Scripts\python.exe -m pytest -q tests/test_sim_accel_thread.py
→ 11 passed in 3.40s
```

| # | 測試 | 守護的語意 |
|---|---|---|
| 1 | `test_m2_age_boundary_is_strictly_greater` | 存活軸邊界是 `>`（14 天整不算、`+1` 秒才算） |
| 2 | `test_m2_active_advisory_never_mutates` | 🔴 active 線頭命中存活軸 ⇒ **只是諮詢訊號**（工單更正新增） |
| 3 | `test_m2_terminal_thread_is_real_dissolution` | 終態線頭 ⇒ 真溶解（`should_mutate is True`） |
| 4 | `test_sandbox_run_emits_exactly_one_new_event` | 🔴 35 筆零重發 ＋ 只新增 1 個 ＋ 正文/感悟非空 ＋ 生產 sha256 未動 |
| 5 | `test_seam_without_seeding_emits_all_36` | 不播種 ⇒ 全部 36 筆 |
| 6 | `test_virtual_now_is_created_at_plus_14_days_plus_one_second` | 虛擬時點 ＝ `created_at + 14d + 1s` |
| 7 | `test_exec_layer_skips_when_llm_absent` | 🔴 依賴缺席 ⇒ `skipped_no_llm`、0 呼叫、0 寫入 |
| 8 | `test_exec_layer_rejects_malformed_llm_shape` | 🔴 形狀不符 ⇒ `parse_failed`、**不得寫入事實** |
| 9 | `test_child_refuses_when_env_drifts` | 🔴 env 漂移 ⇒ 子行程拒絕執行 |
| 10 | `test_scripted_llm_rejects_unexpected_prompt` | 腳本化 LLM 100% 覆蓋（非預期 prompt 必須 raise） |
| 11 | `test_cli_has_no_forbidden_imports` | CLI 不 import 任何網路／程序建立 API |
| 12 | `test_sandbox_copies_are_independent_inodes` | 🔴 沙盒副本必須是**獨立 inode**（RECALL-30D 事故根因的直接守衛） |

### 5.2 必跑現有測試（凍結契約）

```
.venv\Scripts\python.exe -m pytest -q tests/soul/test_life_thread_dissolution_exec.py
→ 113 passed（T53／T54 綠：執行層的生產 importer 集合仍精確等於 3 檔）

.venv\Scripts\python.exe -m pytest -q tests/soul/test_life_thread_origins_m4.py \
    tests/soul/test_life_threads_m1.py
→ 4 條本票直接觸及的契約測試全綠：
   - test_module_is_not_wired_into_production_paths（M4 生產 importer 精確等值）
   - test_legacy_git_grep_false_green_is_fixed（M4 文字層引用白名單）
   - test_module_declares_m5_wiring_and_reuses_existing_channel（M4 入口唯一呼叫端）
   - test_s2_1_module_not_imported_by_production_paths（M1 生產 importer 精確等值）
   ⇒ 本 CLI 的 import 面只剩 orchestrator ＋ src.inner_life.*（見 §1.3）
```

### 5.3 全庫回歸（repo 的 `.venv`）

```
.venv\Scripts\python.exe -m pytest -q tests
→ 1 failed, 6745 passed, 24 skipped, 27 subtests passed in 740.40s (0:12:20)
```

**唯一的紅燈不是本票造成的**：

```
FAILED tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched
  AssertionError: src/ 改動超出許可集: {'src/inner_life/recall_prompt.py', 'src/llm/proxy.py'}
```

⇒ 該測試用 `git diff --name-only HEAD` 掃**整個工作樹**的 `src/` 改動；
本 worker **未改任何 `src/**` 檔案**（`git diff --name-only HEAD -- src/` 的兩個檔
是**另一張票 SIM-ACCEL-RECALL-30D** 的未提交改動）。
⇒ 本票造成的紅燈（M4／M1 的 importer 精確等值、M4 入口唯一呼叫端、M4 文字層引用、
T54 執行層 importer 集合、hygiene 未追蹤檔）**已全部修綠**（見 §1.3、§3.5、§5.2）。

```
.venv\Scripts\python.exe -m pytest -q tests
→ 見 §6「回報摘要」
```

---

## 6. 變異實驗（5 條，逐條轉紅）

**機制**：來源做**字串替換**後在**獨立命名空間 exec** ⇒ 變異體**不動 repo 內任何檔案**
（不在磁碟上改 frozen contract），也**不可能**觸達生產。
執行方式：`$env:SIM_ACCEL_MUTANT='<變異名>'; .venv\Scripts\python.exe -m pytest -q tests/test_sim_accel_thread.py::<守衛測試>`。

| # | 變異內容 | 轉紅的測試名 | 紅燈實測輸出（摘要） |
|---|---|---|---|
| M1 | **讓 M2 的 advisory 誤發成真溶解**：諮詢分支的 `should_mutate=False` → `True`（`life_thread_dissolution.py` 諮詢回傳區塊） | `test_m2_active_advisory_never_mutates` | `AssertionError: advisory 不得變更任何狀態` ／ `E assert True is False` ／ `+ where True = DissolutionEvaluation(..., extra_metadata={'advisory': True, ..., 'age_days': 14}).should_mutate` |
| M2 | **移除 at-most-once**：接縫的 `if thread_id in _ELEVATION_EMITTED: continue` → `if False: continue`（`life_thread_orchestrator.py`） | `test_sandbox_run_emits_exactly_one_new_event` | `AssertionError: 歷史線頭不得重發` ／ `E assert 35 == 0`（35 筆全被重發） |
| M3 | **讓「LLM 回傳形狀不符」被誤接受**：敘述文字只檢查 `value is not None`（`life_thread_dissolution_exec.py::_narrative_from`） | `test_exec_layer_rejects_malformed_llm_shape` | `AssertionError: 形狀不符必須 parse_failed（實際 consolidated）` ／ `E assert 'consolidated' == 'parse_failed'` |
| M4 | **移除「依賴缺席就跳過」的閘門**：`if llm_call is None:` → `if False:`（`life_thread_dissolution_exec.py`） | `test_exec_layer_skips_when_llm_absent` | `AssertionError: assert 'llm_failed' == 'skipped_no_llm'` ／ `WARNING … llm error … type=TypeError`（`None(...)` 立刻炸，**0 網路**） |
| M5 | **移除沙盒 env 漂移的自我檢查**：`if actual != expected:` → `if False:`（`sim_accel_thread_cli.py::assert_sandbox_isolated`） | `test_child_refuses_when_env_drifts` | `Failed: DID NOT RAISE Exception` |

**變異安全性說明**：
- M1／M2／M3／M5 皆為**純邏輯**變異（不碰 I/O、不碰網路），且變異體只存在於記憶體
  （**不動 repo 內任何檔案**、**不做任何檔案系統操作**、**不 monkeypatch 他人的複製函式**
  ⇒ 不會重演 RECALL-30D 的「共用 inode」事故機制）；
- M4 的解除防護路徑在 `llm_call is None` 時會立刻 `TypeError`（`None` 不可呼叫）⇒
  **不可能**打到外部 API；
- 全部 5 條變異**沒有任何一條觸達生產**：變異體的 `data_root()` 仍是沙盒，
  且**每條變異跑完都獨立複核過生產 sha256（27 檔 0 變動，見 §4.9）**。

---

## 7. Frozen Contract 檢查

| 對象 | 狀態 |
|---|---|
| `src/soul/life_thread_orchestrator.py` | **未改**（變異僅在記憶體中） |
| `src/soul/life_thread_dissolution.py` | **未改** |
| `src/soul/life_thread_dissolution_exec.py` | **未改** |
| `src/soul/life_threads.py`／`src/paths.py`／`src/inner_life/**` | **未改** |
| `data/**` | **未改**（27 檔 sha256 before/after 逐位元相同） |
| `personas/**`／`configs/**`／`docs/**` | **未改** |
| 旗標預設值 | **未改**（`LIFE_THREAD_ELEVATION_ENABLED`／`LIFE_THREAD_CONSOLIDATION_ENABLED` 只在**沙盒子行程的 env** 設為 `1`；`LIFE_THREAD_CATCHUP_ENABLED`／`LIFE_THREAD_BOOTSTRAP_ENABLED`／`LIFE_THREAD_WHIM_ENABLED` 明確設空） |
| **T54（執行層 import 面恰為 3 檔）** | 🔴 第一版測試檔直接 import 執行層 ⇒ 紅燈；已改經接線層取得 ⇒ **恢復綠**（見 §3.5） |
| 行程安全 | 0 殺行程、0 重啟、0 綁埠、0 真實請求、0 真實 LLM；未 commit／未 push |

---

## 8. 已知限制

1. 🔴 **與工單更正 §3.1 步驟 5 的偏差**：轉入終態**不是**由「M4 的 LLM 動作」觸發，
   而是走**原工單 §3.1 步驟 8 的既有 API 路徑**（M1 `append_transition` ＋ 接線層 hook）。
   原因是 repo 內三條凍結契約把「M4 入口的呼叫端」與「M1／M4 的生產 importer 集合」
   釘死為**精確等值**，`scripts/**` 的沙盒 CLI 都不在名單內（完整證據見 §1.3）。
   被省略的只有「誰決定轉入終態」這一步；**接縫增量機制本身走的全是生產接線層的同一條路**。
2. **感悟（reflection）非空需要事實已 commit**：本票在沙盒內顯式 `flush_all_live()`；
   生產未做同樣的事 ⇒ 生產的感悟多半是空字串（`reflection` 是純伴隨 metadata，
   `elevation_adapter._event_content()` 只認 `narrative`）。**本票未改生產行為**。
3. **`os.spawnv` 的 argv 不做引號處理**：argv 只放 `[exe, "-m", 模組名]`；
   若 `sys.executable` 的路徑含空白字元，本機的 `_spawnv` 會截斷 ⇒
   參數一律走 env（非 argv）以避開。
4. **目標線頭的挑選依賴生產的 active 線頭**：若生產的 `agent_mahiru` 兩條 active 線頭
   都在本次被推進完（皆 `completed`），CLI 會以 exit 2 明確拒絕（不靜默改用別的線頭）。
5. **`os.execve` 在本 Windows build 必崩**（`0xC0000005`，並留下 5 個 crash dump，見 §4.10）
   ⇒ 與工單字面指定不同；改用 `os.spawnv`，隔離語意等價且多保住退出碼。
6. **本 worker 曾造成 5 個 `data/crash_dumps/*.dmp`**（`os.execve` 實驗的副作用，§4.10）
   ⇒ 依鐵律 8 **只回報、不清理**，由主大腦裁決。
