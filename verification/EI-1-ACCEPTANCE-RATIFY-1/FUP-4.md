# EI-1-ACCEPTANCE-RATIFY-1 FUP-4 補件記錄（單點）

- 工單：EI-1-ACCEPTANCE-RATIFY-1-FUP-4（主腦點名的唯一剩餘缺件：N2 foreign-import 同一驗收 oracle）
- 適用 commit：`f19aac1`；解譯器唯一口徑：`.venv` Python 3.11
  （`C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe -B`）；
  每次 pytest 前新建 TEMP 目錄作 `SOUL_OS_DATA_DIR`（見 §2／§3 的 `SOUL_OS_DATA_DIR=` 實測值）。
- 改動範圍（僅此三檔）：`tests/interfaces/test_ei1_acceptance_ratified.py`
  （N2 foreign-import 一處，`:1972–2005`）、`docs/EI-1-ACCEPTANCE-RATIFIED.md`
  （N2 證據格加一句對齊）、本檔新增；
  `verification/.../FUP-2.md`、`FUP-3.md`、`SUMMARY.md` 凍結未動。
- `git status --short` 全程僅上述 `??`（見 §4）；`data/` 零變動；未 commit／add／合入。

## 1. 前後對比（舊寫法 vs 新同一 callable）

缺件：`test_n2_mut_loosen_foreign_import`（舊 `:1993–1998`）的殺死證據是
反向斷言 `any("src.agent.registry" in b for b in bad)`（只證 mutant 生效的
AST 報錯，未證同一驗收 oracle 殺死 mutant）；正常版對照是另一行內聯斷言，
非同一 callable。

| | 舊寫法 | 新寫法（本票） |
|---|---|---|
| 驗收 callable | 無（兩處內聯 `assert _check_imports_clean(...) ...`） | 新增 `_oracle_no_foreign_import(text, rel)`（`:1972–1974`），內部斷言乾淨性檢查結果為空 |
| 正常版 | `assert _check_imports_clean(base, ...) == []`（對照行） | `_oracle_no_foreign_import(base, ...)` 通過（綠，`:2001`）；`test_n2_import_clean` 六檔迴圈亦改呼叫同一 oracle（`:1982`） |
| mutant 版 | `assert any("src.agent.registry" in b for b in bad)` 即視為殺死 | 同一 oracle 包 `pytest.raises(AssertionError)` 捕獲其失敗即殺死（紅，`:2004–2005`）；原 `any(...)` 保留為診斷行並標註「失敗原因（非殺死證據）」（`:2003`） |
| mutant 注入 | `base + "\nimport src.agent.registry  # MUTANT-N2-LOOSEN\n"` | 未動（注入方式零變更） |

未動（工單明列）：`:2008–` `test_n2_mut_tighten_drop_import`、
`:2013–2031` 匯入前硬攔見證（另一測試）、`:1826` 原測試、其餘 59 mut、
Frozen must-stay-RED（journal×3、runtime_port×2，原 nodeid／原斷言）。

## 2. 聚焦測試結果

- `pytest -q tests/interfaces/test_ei1_acceptance_ratified.py`
  （`SOUL_OS_DATA_DIR=C:\Users\bbfcc\AppData\Local\Temp\fup4_focus_1aab6121c8f8498e8ad13378d76ed2fb`）
  → **92 passed**（數量不變，只改寫法；`[SKIP-BASELINE] observe-only` skipped=0）。
- N2 三態成立：正常六檔綠（同一 oracle）／mutant 同一 oracle 紅
  （`pytest.raises(AssertionError)` 捕獲）／診斷行僅說明注入位置。

## 3. interfaces 回歸

- `pytest -q tests/interfaces`
  （`SOUL_OS_DATA_DIR=C:\Users\bbfcc\AppData\Local\Temp\fup4_iface_fb018915d2914878b09695b53123fa8d`）
  → **5 failed, 486 passed, 1 skipped**（42.55s；`[SKIP-BASELINE] observe-only` skipped=1）。
- 5 敗全為已知 `test_gap_*`（與 SUMMARY §2／FUP-3 §4 基線同；journal×3、runtime_port×2），
  無新增失敗：
  - `tests/interfaces/test_journal.py::test_gap_backend_completed_unreachable`
  - `tests/interfaces/test_journal.py::test_gap_backend_dest_completion_rejected`
  - `tests/interfaces/test_journal.py::test_gap_cancel_absent`
  - `tests/interfaces/test_runtime_port.py::test_gap_no_side_effect_retry_still_uncertain`
  - `tests/interfaces/test_runtime_port.py::test_gap_decision_port_result_absent`
- passed 486 與 FUP-3 同（本票零新增測試，只改寫法）；全庫未跑
  （工單明示：上一輪全庫 945s 且本輪只改一測試，interfaces 回歸已足）。

## 4. hygiene 追蹤檢查

- `git check-ignore -v` 對測試檔／文件檔／本檔：無忽略（exit 1，實測）。
- `git add --dry-run` 三檔可加（exit 0，實測；worker 無權 add，合入即解——沿用 FUP-3 §8 口徑）。
- `git status --short` 僅三條 `??`（測試檔、文件檔、`verification/`），無其他變動；
  repo 頭為 `f19aac1`，追蹤檔 1348 筆。
- `data/` 零變動：`data/` 由 `.gitignore:42` 忽略，`git status --short -- data` 為空；
  兩輪 pytest 的 `SOUL_OS_DATA_DIR` 均為自建 TEMP（§2／§3），生產 `data/` 唯讀未寫。

## 5. 文件對齊

`docs/EI-1-ACCEPTANCE-RATIFIED.md` N2 證據格 M-LOOSEN 欄加一句：
「FUP-4 起殺死由同一 oracle `_oracle_no_foreign_import` 承載，
`any(...)` 僅為失敗原因診斷」。其餘格未動。

## Blockers / 需主大腦裁決

無新增阻斷。本票完成後 EI-1 唯一剩餘缺件已補，可再申請終判。
hygiene 合入前恆紅（`git add` 即解，worker 無權）——沿用既有口徑，非本票新債。
