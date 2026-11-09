# SKIP-BASELINE-DRIFT-FIX-1 證據包

- HEAD（施工前）: `a8cb4bcfa696aff6c7193865aac2829969a14fd3`
- 工作樹（施工前）: 乾淨（`git status --short` 空）
- 基線 SHA256（施工前）: `129F1A4A47DBCF8A7F79C137F9C2BD127D06560847454A569EDCBA4C10300B8`
- 解譯器唯一口徑: `C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe -B`
- Shell: PowerShell 5.1；每次跑前新建 TEMP 作 `SOUL_OS_DATA_DIR`（adapter 檔要求預設）

## 1. 兩條 reason 核實（FUP-2 §4 分列保留，不合併）

### Reason 1 — symlink 特權（與 env 無關）

- nodeid: `tests/interfaces/test_harness_soul_context_adapter.py::test_27_symlink_escape_refused`
- 舊 reason 逐字: `symlink creation needs privilege on this host`（`except OSError` 廣泛捕捉）
- 本機實測 errno: `winerror 1314`（見 `winerror_probe.py` 本目錄，輸出 `OSError(22, ...)`）
- 單跑重現: `1 skipped`（reason 逐字相同）

### Reason 2 — 歷史洩漏（與 symlink 無關，白名單自證）

- nodeid: `tests/test_data_root_isolation.py::test_allow_real_data_root_marker_opts_out`
- FUP-2 原文 reason: import 期 `SOUL_OS_DATA_DIR` 洩漏（`test_decay_worker_settlement.py:61`）致 clean-env 前提不成立
- 現況：洩漏源已改為 fixture（該檔 `:142`）；現行 `skipif`（本檔 `:45`）只在「啟動環境已有資料根」才成立
- 單跑重現: `3 passed`（乾淨啟動 env 下不再 skip —— 證明洩漏已除，舊 skip 條件只剩啟動環境相依）

## 2. 施工內容（主腦決策照做）

見工單 §做法 2–4。改動檔僅四個（見 `git diff --stat`）＋本證據目錄。

## 3. 測試結果

解譯器：`C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe -B`；
每次跑前新建 TEMP 作 `SOUL_OS_DATA_DIR`；`pytest -q tests -rs -p no:cacheprovider`。

- [x] 聚焦三檔＋`tests/infra` 全綠
  - `tests/test_data_root_isolation.py` ＋ `tests/infra/test_skip_baseline_guard.py`：`25 passed`
  - adapter 檔（預設隔離根）：`41 passed, 1 skipped`（僅 test_27，行 742，reason 逐字相同）
  - 三關鍵測試（27／27b／自證）：`2 passed, 1 skipped`
- [x] 1314／補償測試／變異全過（5 真 hook 變異證明：基準 STABLE 綠、＋1／−1 轉紅、差集清空 mutant 被殺、停用失敗設定 mutant 被殺）
- [x] 舊基線全庫：`6 failed, 8380 passed, 32 skipped`（`full_pre_update.log`，1221 行，952.06s）
  - DRIFT 恰為 `+ tests/interfaces/test_harness_soul_context_adapter.py::test_27_symlink_escape_refused`、無移除行（符合工單預期）
  - 6 failed 全為 FUP-2 已知既有：journal gap×3（`test_journal.py:578/586/592`）＋runtime_port gap×2（`test_runtime_port.py:407/412`）＋g2（`m5_4_5_3:523`）；hygiene 本輪已綠（FUP-2 時紅只因未追蹤新檔）
  - 自證測試不在 skip 清單（已轉實跑）
- [x] 基線 31→32（只加一筆，不重錄；未設 `SOUL_OS_WRITE_SKIP_BASELINE=1`）
  - 施工前 SHA256 `129F1A4A…00B8` → 施工後 `143EE575…27B18`；`count==len==32`、已排序、CRLF 維持 37 行
  - 插入位置：`tests/clients/…dead_token_for_rem` 之後／`tests/test_extract…` 之前（排序正確）
- [x] 新基線全庫：`[SKIP-BASELINE] skip-set STABLE skipped=32 baseline=32 added=0 removed=0`
  - `7 failed, 8379 passed, 32 skipped`（`full_post_update.log`，1235 行，953.03s）
  - 7 failed ＝ 6 已知既有 ＋ `TestDemotions.test_no_demote_within_90_days`（見下節：已證 pre-existing timing flake，非本票引入）
- [x] reverse／固定 shuffle（seed 2）集合一致（機制為 `tests/conftest.py:354` 既有 `SOUL_OS_TEST_ORDER` hook，本票未動）
  - reverse（`full_reverse.log`，1249 行，946.43s）：`[SKIP-BASELINE] skip-set STABLE skipped=32 baseline=32 added=0 removed=0`；`6 failed, 8380 passed, 32 skipped`（6 failed 全為已知既有，無 demotions）
  - shuffle seed 2（`full_shuffle_s2.log`，1215 行，1272.40s）：`STABLE skipped=32 baseline=32 added=0 removed=0`；`6 failed, 8380 passed, 32 skipped`（6 failed 全為已知既有，無 demotions）
  - 三輪 skip 集合逐筆對帳：`full_post_update.log`／`full_reverse.log`／`full_shuffle_s2.log` 的 32 筆 SKIPPED 行**兩兩完全相等**；symlink skip（`test_harness_soul_context_adapter.py:742`，reason 逐字相同）三輪皆在
- [x] opt-out 測試實跑通過（不再 skip；新基線全庫 skip 清單無 `test_allow_real_data_root_marker_opts_out`）
- [x] Frozen `src/interfaces/journal.py` 零差分（`git diff -- src/interfaces/journal.py` 空）；`data/**` 零寫入（`git status --short -- data/` 空）

### 3.1 `test_no_demote_within_90_days` 為既有 timing flake（非回滾條件）

- 失敗現場（`full_post_update.log`）：`should_demote('2026-08-11T20:01:03.930304+00:00', now=20:01:03.932934)` → `True`，相差 2.6ms 越過嚴格 `>` 邊界
- 根因：`src/social/relational_bands.py:220-223` 用 `elapsed.total_seconds() > _DEMOTE_SECONDS`（嚴格大於）；測試在相鄰兩行各取一次 `datetime.now()`，只有兩次落在同一 Windows 時鐘 tick（ε=0）才 pass，tick 邊界夾在中間即紅 —— 與本票四檔修改無任何資料／時序關聯（本票未動 `src/**`、未動該測試檔）
- 實證：單跑該測試連跑，**第 67 次復現失敗**（`FIRST_FAILURE_AT_TRY=67`，其餘 66 次 pass；修改後程式碼、未動檔，照樣紅）⇒ 認定為 pre-existing flake，不觸回滾
- 前輪（舊基線全庫）該測試恰好落在同一 tick 內而 pass；兩輪差異純屬 tick 運氣，非本票行為改變

## 4. 回滾條件檢查

額外差集／集合隨順序變動／錯誤被吞／mutant 未紅／非預期 I/O ⇒ 停止回報（本輪：待填）。
