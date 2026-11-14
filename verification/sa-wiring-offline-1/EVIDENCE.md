# SA-WIRING-OFFLINE-1 離線接線證據（未部署，APPROVAL-1 仍 FROZEN）

- HEAD: `f6bacb8c185ef08a3f973b232bfb3d20726389ed`（施工前後未動）
- 直譯器：一律 `.venv\Scripts\python.exe`（Python 3.11.15）

## 改動檔案（未提交；僅 T9 測試檔已 `git add` 以滿足 hygiene 守衛，未 commit、不 push）

| 檔案 | SHA-256（完工時） |
|---|---|
| `src/soul_access/wiring.py` | `F27B5E71…A0F03C`（全值見下方命令輸出） |
| `src/external_gateway/app.py` | `BEB84F62…F448FDC` |
| `scripts/run_server.py` | `BE178014…A2EEEE813F1` |
| `tests/soul_access/test_t9_offline_wiring.py`（新增，已 stage，未 commit） | `37E9EB59…95B8D07990` |
| `configs/sa/registry-a.offline.json`（新增候選，未 stage） | — |
| `configs/sa/registry-ab.offline.json`（新增候選，未 stage） | — |
| `configs/sa/presets.offline.json`（新增候選，未 stage） | — |

## 測試結果

- T9：`pytest -q tests/soul_access/test_t9_offline_wiring.py` → **16 passed**
- soul_access 目錄：`pytest -q tests/soul_access`（含 T8/T1/T7）→ **178 passed**
- AST 三目錄：`pytest -q tests/soul_access tests/interfaces tests/infra` → **1136 passed, 1 skipped**（初跑另有 1 hygiene 失敗，係新測試檔未追蹤；`git add` 該檔後重跑 hygiene 檔 **8 passed**；內容零變更，故三目錄視為全綠）
- 全庫：`pytest -q tests` → **9013 passed, 32 skipped, 2 failed**（16分19秒，`.venv`）
  1. `tests/soul/test_life_thread_wake_gate_m3.py::test_t7_non_python_dirs_still_zero_reference[configs]` —— 本票 3 個候選 JSON 使 `configs/` 非 `.py` 被掃描數 8 ≠ 釘死值 5；已實測新檔對 M3（`life_thread_wake_gate`）引用命中 `hits == []`（0 處）。計數 bump（5→8）會**擴大**覆蓋（`hits == []` 照跑 8 檔），非放寬；同檔已有 PACK-EXPAND／KEY-1／S4-W2 三次同模式先例。是否 bump 待主大腦裁決（見下方阻斷）。
  2. `tests/test_m3_1_phase_c.py::test_phase_a_b_files_unchanged_after_phase_c` —— 斷言 `git diff --stat scripts/run_server.py` 為空；本票工作樹改動使它非空。性質為未提交 artifact：commit（或 stage）後即空即綠；前例 `a2971f9 SA-LIVE-WIRING-1` 同樣改過此檔並合入。待協調者合入即消。

## 雙向變異（綠→紅→還原綠，零殘留，已 grep 確認無 `MUTATION-M` 殘留）

- M1 `wiring.py` 同 loop 守衛短路 → `test_t9_factory_same_loop_misuse_refuses_without_deadlock` FAILED（紅）→ 還原後綠
- M2 `app.py` 一致性閘門短路（`if False and not _sa_consistent_or_403(`）→ `test_t9_outer_drift_refuses_403_zero_forward` FAILED（紅，匹配測試仍綠）→ 還原後綠
- M3 `wiring.py` 引號解碼分支停用 → `test_t9_thin_quoted_reply_decoded_plain_untouched` FAILED（紅）→ 還原後綠；還原後 T9 **16 passed**

## CLI 沙盒快跑對帳（exit 0 / PASS）

命令：`.venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --source-dir verification/soul-access/pending-2026-11-04/supplement/_snapshots/slot-2026-11-12T030000Z --simulated-time 2026-11-12T03:00:00Z`
（另以 `--json` 重跑，`EXIT:0`，JSON 存於 `cli-slot-2026-11-12T030000Z.json`，讀取時注意 PowerShell 轉向為 UTF-16 且尾端附一行 `[report]`）

- 快照 manifest 五檔 SHA-256 全符（核對 OK）
- ① sidecar：before 2975 → after 3181，transitions=206，append_consistent=True，no_op=False（真轉變，非 no-op）
- ② 類型純度：soul_type={belief:1, value:89, trait:0, essence:0}；90 筆 weakening 全數命中預期代謝（expected_hits_count=90，expected_retirement_count=1485 台帳內 Legacy Value 退役），pending=0，pending_cohorts=[] → PASS（非 FAIL、非 PENDING_COHORT）
- ③ 關鍵反例節點：key_node_transitions=0（維持 active）
- ④ Prompt 投影：prompt_diffs_unexplained=0；weak_prefix 1066→1156（+90 = expected 90 + pending 0 ✓）；全部 diff 皆 explained_by_expected_metabolism=True
- verdict=PASS，exit_code=0，failures=[]，mode=sandbox-only，source_is_repo_production=False

## release 缺檔（施工前後唯讀確認，保持關閉）

- `configs/sa/registry.json`：不存在（False）
- `configs/sa/presets.json`：不存在（False）
- `release/sa.json`：不存在（False）
- T9 內 release 矩陣＋開→關→開熱讀測試綠（沙盒內臨時開門，生產側零寫入）

## 意外行為

- M1 施工中發現：同 loop 誤用情境下 `backend.complete(...)` 先於 `send` 的守衛執行，導致 FakeBackend 已記一次調用；已按票面「同 loop 誤用直接拒絕」語義把守衛前移至 `complete` 之前（零調用、無懸空 coroutine），T9 重跑 16/16。

## 未驗事項

- R2 重驗（2026-11-14，主腦 CONDITIONAL 裁決 Q1 准許 5→8 落地後，解譯器 `.venv` Python 3.11.15）：
  - 兩守衛重跑 `pytest -q tests/soul/test_life_thread_wake_gate_m3.py tests/test_m3_1_phase_c.py`：396 passed＋1 failed；唯一失敗為 Q2 凍結守衛 `test_phase_a_b_files_unchanged_after_phase_c`（工作區 `scripts/run_server.py` 未提交差異所致，commit 前預期紅，本票不提交故保留）。
  - 全庫 `pytest -q tests --tb=short`：1 failed，9014 passed，32 skipped，32 subtests passed，耗時 982.41s，exit=1；唯一失敗同為上述 Q2（Q1 configs 5→8 已綠、前票「2 failed」收斂為 1）。
  - 判定：Q1 解決；全庫剩餘 1 failed 全部定性為「本票未提交工作差異觸發的 Q2 凍結守衛預期紅」，合入（commit）清空 `git diff` 後即綠。CONDITIONAL 待協調者複核，主腦未授權寫 ACCEPTED。

## 阻斷（需主大腦 Sol 裁決，不自行改守衛）

- Q1：`tests/soul/test_life_thread_wake_gate_m3.py:790` 的 `_M3_NON_PY_SCANNED_COUNTS["configs"]` 是否由 5 bump 至 8（附本票格式註解＋已驗 `hits == []`）？備選：Sol 另給豁免作法。worker 不自行改守衛常數。
- Q2：`scripts/run_server.py` 的 M3.1 phase-C 凍結斷言在本票合入後即綠（commit 清空 `git diff`），是否按前例（a2971f9）視為預期內？是則本票在合入前全庫維持「2 failed 已定性」狀態交付。
- canonical（`logs/ENGINEERING_STATE.md`）追加待 Q1/Q2 裁決後再寫，避免寫入未定案的驗收結論。
