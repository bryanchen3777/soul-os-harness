# REPORT-B：PENDING-1104 B 階段（台帳追加 11-04 cohort 90 ID）

- 基線 HEAD：`578f3a2`（B 階段改動前乾淨；工作區僅本單兩檔改動）。
- 主腦裁決：A 閉環、核准進入 B、限封存 90 ID；不是部署核准。完工仍未部署（生產接線核准另票）。
- 同快照同槽：快照 `verification/soul-access/pending-2026-11-04/supplement/_snapshots/slot-2026-11-12T030000Z/`、
  槽 `2026-11-12T03:00:00+00:00`，CLI 以 `--source-dir` 指向快照（不讀生產 `data/elevation/`）。

## 改動（2 檔）

- `scripts/eval_lifecycle_cli.py`：`EXPECTED_RETIREMENT_COHORTS` 新增 `2026-11-04` cohort（90 節點；
  89 value 預設不記 `types`，1 belief `e140a13e03f54dd28868dca9c5f8c516` 記 `types`；
  逐節點 `bounds` 90 筆原樣取 `bounds.json`；`not_before=2026-11-12T03:00:00+00:00`），
  按建立日排序於 11-03／11-09 之間。單一 hunk、191 insertions／0 deletions。
- `tests/test_eval_lifecycle_cli.py`：守衛 `test_ledger_count_still_1302` 更名為
  `test_ledger_count_now_1392_after_1104_cohort`，釘死總數 1392、新 cohort 形狀
  （90 節點／退役窗／1 belief／bounds 全覆蓋）、其餘聯集 1302 且零重疊。
- `git diff --numstat` 見 `git_diff_numstat.txt`（本單僅上列兩檔；另有無關 untracked，不屬本單）。

## 台帳差集（舊集合不變證明）

（`ledger_diff.json`／`ledger_diff_ids.json`；以模組載入新舊台帳比對）

- old 1302 ⊆ new 1392，deletions＝[]，diff 90 ＝ `per_id.json` 90 ID（逐字相等）。
- 新 cohort：`nodes` 90、`bounds` 90、`not_before` 11-12 槽、`types` 僅 1 筆 belief。
- cohort 日期列：10-11 … 11-03、**11-04**、11-09、11-10（時間序插入，無尾部追加）。

## 前後對帳（`baseline.stdout.json` → `after.stdout.json`，`reconciliation.json`）

- verdict `PENDING_COHORT` → `PASS`；exit 3 → 0。
- `pending_cohorts` 1（90 ID）→ 0；`pending 90 → 0`；`expected_hits_count 0 → 90`；
  `expected_retirement_count 1302 → 1392`（增量＝差集 90，逐字驗證）；
  `derived 1239 → 1329`（＋90）。
- 引擎行為不變：`transitions` 206＝206；`sidecar_appended_events` 206 筆逐位元相同
  （`sidecar_appended_sha256` 前後一致，`sidecar_after_sha256` 前後一致，
  `sidecar_append_consistent` 皆 true）。
- 型別／狀態／時刻不變：`soul_type_transitions` belief 1／value 89／trait 0／essence 0 前後相同；
  `by_node_type`、`by_to_state` 前後相同。
- Prompt：`prompt_diffs` 46 筆前後逐位元相同；weak 1066→1156 前後相同（增量 90）；
  `prompt_diffs_unexplained` 0＝0。
- 關鍵反例節點 `6a92c906b245`：`key_node_transitions` 0＝0（仍 active，未轉 weakening）。

## 測試

- CLI 測試類＋AST 守衛：`pytest -q tests/test_eval_lifecycle_cli.py tests/infra` →
  520 passed、5 subtests passed、exit 0（含新 1392 守衛）。解譯器：repo `.venv`。
- 全庫回歸：`pytest -q tests`（repo `.venv`，background）→
  `5 failed, 8882 passed, 32 skipped, 32 subtests passed`（16分24秒），詳 `full_regression.txt`。
  5 紅全為既有 `test_gap_*`（`test_journal` 3 筆＋`test_runtime_port` 2 筆，與基線一致），
  無新增失敗 ⇒ 不退回。

## 變異（TEMP 副本＋行內呼叫＋全合成事件；repo 只讀；`mutations.txt`）

- M1 漏 ID（刪 belief 節點）：`is_expected_metabolism` True→False，轉紅。
- M2 錯型（belief 記成 value）：True→False，轉紅。
- M3 下界後移（belief 逐筆 bound 推未來）：到期事件被擋，轉紅，證明逐節點下界 load-bearing；
  互補：真實模組拒絕早產事件（ts＜bound → False），無提前放行。
- 還原後重載 repo 原件：belief／value 皆 True，轉綠；前後 repo 檔案位元組一致（腳本內斷言）。
- 本 session 已親自重跑 `mvs_mut.py`，輸出即 `mutations.txt`（ALL RED-THEN-GREEN）。

## Frozen Contract／意外行為

- 未動：A 階段證據、快照、引擎、判準、`data/**`、Frozen Contract 所列各項。
- 無意外行為：四項對帳全等；變異三紅一綠符合預期；無行程／埠／網路／服務操作。
- 備註：任務中段工作區曾出現一份未提交的 386-insertions 台帳編輯（缺 `types`），後被未知方還原；
  本 session 以乾淨基線重做為 191 行純新增（零刪除），`git diff` 可驗。HEAD 全程 `578f3a2` 未變。

## 交付狀態

- 完工仍未部署：生產接線核准另票，不得宣稱已部署。
