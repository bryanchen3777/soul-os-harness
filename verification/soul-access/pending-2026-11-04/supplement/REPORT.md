# PENDING-1104 補件 A 階段 REPORT（固定槽 2026-11-12T03:00:00Z）

## 1. 運行紀錄
- command: `verification/.../supplement/run-slot-2026-11-12T030000Z/command.txt`
  （`.venv/Scripts/python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z --json`）
- UTC start: `run-slot-.../utc_start.txt`（2026-11-12T00:45:40Z，PowerShell 記錄時刻；CLI 內 `elapsed_seconds=0.422`）
- interpreter: `run-slot-.../interpreter.txt`（Python 3.11.15，repo `.venv`）
- HEAD: `run-slot-.../head.txt`（33f11265bce5125838712c824a902752d01131a5）
- dirty: `dirty_before.txt`（跑前）／`dirty_after.txt`（`git diff --stat`：僅 2 檔 +288 行）
- stdout: `run-slot-.../stdout.json`（**UTF-16 with BOM**，PowerShell `>` 轉向所致；
  尾部多一行 `[report] ...`，解析須 `encoding='utf-16'` + `JSONDecoder().raw_decode`）
- stderr: 空；exit_code: `3`（= PENDING_COHORT，預期內）
- 台帳：`EXPECTED_RETIREMENT_COHORTS` **一字未動**（新測試斷言 1302 筆）

## 2. 快照同源綁定
- 位置：`supplement/_snapshots/slot-2026-11-12T030000Z/`（唯讀 `Copy-Item` 自 `data/elevation/`）
- manifest：檔名／大小／SHA／排除規則（`_snapshots/.gitignore` 排除 `*.jsonl` 位元組，只提交 manifest）
- 五檔 SHA 與 `evidence.json:source_snapshot_sha256` **逐檔相等**：
  nodes `8b220b9a…2d47be2e9`／edges `f0189956…1282f777ae5`／
  lifecycle_events `4a7f0ad8…2782dfc39`／projection_trace `9d9f9dcd…c7f3d8954`／
  trace `8ad93ed0…464b62ebb`
- CLI 證據欄位：沙盒消費指紋 `sandbox_consumed_sha256`（評估三輸入：
  nodes／edges／lifecycle_events）與來源指紋對應三鍵**逐鍵相等**
 （trace 系二檔不進沙盒，不在消費集合內）；
  `sidecar_append_consistent=True`，附 `cli_sha256`／`adapter_info`／`engine_info`。

## 3. CLI 四項對帳（固定槽）
1. **sidecar 增量**：before 2975 → after 3181，**+206**；`sidecar_appended_events=206`，
   `sidecar_append_consistent=True`。transitions=206（90 pending + 97 pattern active→weakening + 19 pattern weakening→dormant）。
2. **轉變類型純度**：soul 型別 `belief 1 + value 89 + trait 0 + essence 0`；
   value+belief=90 **恰等於 pending 數**，trait/essence 恆等於 0 ⇒ PASS（無未核准退役）。
3. **關鍵反例節點**：`key_node_transitions=0`（維持 active，未轉 weakening）。
4. **Prompt 投影**：`weak_prefix_before=1066 → after=1156`，增量 **+90 = pending 筆數**；
   `prompt_diffs_unexplained=0` ⇒ 預期投影後果，無破線。
- verdict=`PENDING_COHORT`，exit=3（維持；90 筆 pending 與舊 ID 集合**完全相等**，見下節）。

## 4. 90 筆逐筆證據＋逐筆時間下界
- `per_id.json`：`{count:90, items:[…]}`，每筆含原文行對應的六項事實
  （found_in_graph／created_date=2026-11-04／created_eq_last_support／sp_inbound_zero／
  pre_state=active／observed_weakening_in_slot），90/90 全真；
  與 `evidence.json` 舊 90 ID 集合比對：**sorted 相等（equal=True）**。
- 錨點語義（已釐清）：90 筆錨點**全等於 `created_ts`**（仍有效邊最大 `valid_from` 即出生邊）；
  型別 1 belief + 89 value。
- `bounds.json`：`bound = anchor + 7d`（`>=` 浮點天數語義，`engine.py:evaluate_lifecycle`），
  錨點優先序：仍有效邊最大 valid_from → 注入 last_support → created+3d grace → None 跳過；
  標示為**推導值**（公式＋輸入＋來源行／SHA），非 observed 時刻。
- `boundary.json`：每筆全新沙盒三探針（bound−1µs 不轉／bound 轉／bound+1µs 轉），
  **270/270 pass，fail 0**。
- 重現：`.venv/Scripts/python.exe verification/soul-access/pending-2026-11-04/supplement/reproduce_a.py`
  （`--skip-boundary` 跳過 270 探針；預設全跑）。

## 5. 測試（規定序列，解譯器一律 repo `.venv`）
- `tests/test_eval_lifecycle_cli.py`：**142 passed**（含新增 `TestPending1104EvidenceBinding` 6 passed：
  AST 鍵存在／台帳數恆 1302／合成跑自洽／M-漏事件／M-換 SHA／邊界謂詞雙向）。
  ※ 途中曾觸發 `TestExpectedMetabolism::test_cli_does_not_import_decay_thresholds`
  （新證據碼函式內 `import soul_elevation`），已改為 `importlib.util.find_spec` 只取路徑指紋、
  不 import，被測行為不變；固定槽 CLI 已用最終碼重跑，數字逐項一致（見 §3）。
- `-k inner_life`：**831 passed, 2 skipped**。
- `test_tl4_lifecycle + tests/soul_access + tests/infra`：**537 passed**。
- 全庫 `-q tests`：**8872 passed, 32 skipped, 5 failed**（956s）；
  5 失敗全在 `tests/interfaces/test_journal.py::test_gap_*`（3）＋
  `test_runtime_port.py::test_gap_*`（2），為缺口分析測試（期待未實作的
  Journal.cancel／Decision／backend-completed 語義）。
  已在乾淨基線（stash 本票二檔後、HEAD=33f1126）重跑同 5 項：**同樣 5 failed** ⇒
  基線預存失敗，與本票改動無關（本票僅動 CLI 證據欄＋其測試）。
- 另：`tests/soul/test_sim_accel_streak_7d.py` 單跑 **10 passed**（49s；全庫首輪疑似被
  harness 輸出量截斷，故第二輪以 `--ignore` 跑其餘全庫＋該檔單跑補齊覆蓋）。
- 變異採 TEMP 副本＋行程內呼叫，未觸達生產；M- 系列皆轉紅（fail），符合預期。

## 6. 狀態
- 準備：READY（A 階段證據齊：快照／CLI 輸出／90 筆＋六事實／逐筆下界＋270 邊界／新測試全綠）。
- 核准：BLOCKED（待 Sol 明示核准；B 階段台帳增補本票不做）。
- 部署：BLOCKED（不阻準備）。
