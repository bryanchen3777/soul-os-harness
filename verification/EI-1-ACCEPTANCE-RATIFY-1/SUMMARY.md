# EI-1-ACCEPTANCE-RATIFY-1 驗證證據（SUMMARY）

- 工單：EI-1-ACCEPTANCE-RATIFY-1（追認規格＋專屬雙向變異證據）
- 追認日期：2026-11-09；適用 commit：`f19aac1`
- 解譯器：`C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe`（Python 3.11）
- 每次 pytest 前新建 TEMP 作 `SOUL_OS_DATA_DIR`（形如 `$t = Join-Path $env:TEMP ('eir1_' + [Guid]::...); $env:SOUL_OS_DATA_DIR = $t`）
- 交付檔（未 commit，依工單禁令）：
  - `docs/EI-1-ACCEPTANCE-RATIFIED.md`（30 條 AC：C1–C7、G1–G6、I1–I4、P1–P6、S1–S5、N1–N2）
  - `tests/interfaces/test_ei1_acceptance_ratified.py`（88 tests，LF 換行，`hasCR=False` 實測）
- 原碼／既有測試／守衛唯讀；`git status --short` 全程僅上述 2 個 `??`，`data/` 零變動。

## 各階段結果

### 1. 聚焦（本工單新測試）
`pytest -q tests/interfaces/test_ei1_acceptance_ratified.py` → **88 passed**（3.09s）。
途中修過 7 處變異設計（皆為 mutant 寫法問題，原碼行為正確）：
C5 單點放寬殺不死→同題五層一併移除（raw 掃描／content／name／ensure_ascii／utf-8 surrogatepass）；
G1 同理雙層放寬；I3-tighten 斷言改 401（AttributeError 被 `except Exception → _unauth` 吃掉）；
S1-tighten 改去末欄 `n` 預設（定義期 TypeError，須包 `_load_mutant`）；
N1 斷言改「非模組公開符號為空」（子模組屬性係正常綁定）；
N2-tighten 改呼叫 `parse_request` 期望 NameError（Message 僅呼叫期使用）；
C5 `ensure_ascii` 替換去掉行尾註解（註解吞掉後續參數致 SyntaxError）。

### 2. tests/interfaces 全量
`pytest -q tests/interfaces` → **5 failed, 482 passed, 1 skipped**。
5 敗皆 `test_gap_*`（journal×3、runtime_port×2），與本工單無關：
以 `pytest -q tests/interfaces/test_journal.py tests/interfaces/test_runtime_port.py`（不載入本工單新檔）重跑，
同樣 5 failed／42 passed —— 完全一致，證為原有失敗。

### 3. 守衛 tests/infra
`pytest -q tests/infra` → **318 passed，1 failed**：
僅 `test_every_collectable_test_file_under_tests_is_git_tracked`，
原因唯一：本工單新測試檔未追蹤（工單禁 commit）。合入 main（`git add`）即轉綠，非本工單可解。

### 4. Frozen contract 守衛
`pytest -q tests/test_observability_completeness_1.py -k test_no_frozen_contract_file_touched` → **1 passed**。

### 5. 全庫 `pytest -q tests`
**7 failed, 8368 passed, 33 skipped, 32 subtests passed**（938.36s）。
FAILED nodeid（7）與歸因：
1. `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked`
   → 本工單未追蹤新檔（合入即解，見 §3）。
2. `tests/interfaces/test_journal.py::test_gap_backend_completed_unreachable`
3. `tests/interfaces/test_journal.py::test_gap_backend_dest_completion_rejected`
4. `tests/interfaces/test_journal.py::test_gap_cancel_absent`
5. `tests/interfaces/test_runtime_port.py::test_gap_no_side_effect_retry_still_uncertain`
6. `tests/interfaces/test_runtime_port.py::test_gap_decision_port_result_absent`
   → §2 已證原有（不載入新檔亦同敗）。
7. `tests/test_m5_4_5_3_diary_inner_life_integration.py::TestSectionG_DataRootIsolation::test_g2_default_data_root_unchanged`
   →  harness artifact：斷言拿 `data/soul`（生產路徑）對 TEMP 路徑，
   在工單強制 `SOUL_OS_DATA_DIR=TEMP` 下必敗；單跑該 node（不收集本工單新檔）同樣復現。
   repo 自帶文件（`tests/test_data_root_isolation.py:27-44`，Owner 2026-10-12 裁定「環境耦合，非邏輯缺陷」）
   已載明此類耦合。
附帶 `[SKIP-BASELINE] full-repo mode => FAIL`（skip-set DRIFT +2：
`test_27_symlink_escape_refused`、`test_allow_real_data_root_marker_opts_out`）：
同上根因——`requires_clean_process_env` 在 shell 層 `SOUL_OS_DATA_DIR` 存在時必然 skip，
基準線應為無 shell 變數環境下生成；與本工單新檔無關（新檔零 skip 標記）。

計數對帳：基線 8320（6+8281+33）＋ 本工單 88（全綠，無 FAILED 條目）＝ 8408 ＝
實測 8408（7+8368+33）✓；failed 差分 ＋1 恰為 hygiene（本工單新檔未追蹤）✓。
passed 差分 ＋87（naive ＋88 應得 8369，實測 8368）：差 1 落在既有 corpus 內，
無基線 nodeid 清單可定位，列為未決小差（見 Blockers）。

## 不做聲明（與工單一致）
無 EI-1 專屬原始工單；本追認 scope 成立 ≠ 功能驗收；未倒填日期／冒充授權；
未由 worker 關閉 EI-1；未寫 canonical／Notion；未 commit／push／合入 main；
`journal.py` 語義零改動；G1–G5 維持 must-stay-RED（未動）。

## Blockers / 需主大腦裁決
1. hygiene 守衛在合入前恆紅（預期內，合入 `git add` 即解）。
2. 全庫 passed 較 naive 加總少 1（8368 vs 8369）：既有 corpus 內 ±1，無基線 nodeid 無法定位；
   建議主大腦以基線 nodeid 清單做差分，或視為量級可忽略。
3. 全庫級 `[SKIP-BASELINE] DRIFT FAIL` 與 `test_g2` 在強制 TEMP harness 下恆觸發；
   若 Hard Gate 要求全庫 exit 0，須由主大腦決定 baseline 重寫／harness 調整（worker 無權）。
