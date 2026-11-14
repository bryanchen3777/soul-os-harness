# SA-DEPLOY-PREFLIGHT-1 gate results (evidence, not claims)
candidate: 6fdd38f | interpreter: .venv/Scripts/python.exe (Python 3.11.15)

## Hard Gate 1 — AST / 範圍 / 行程 / 生產寫入 / 接線守衛
- command: `.venv\Scripts\python.exe -m pytest -q tests/soul_access tests/infra`
- result: **555 passed in 119.56s, exit 0**
  （含 T0-T8 全系、接線 T8、scope registration、observability、no-production-spawn、
  spawn/kill/address 掃描守衛皆綠；SKIP-BASELINE 行為 observe-only 非全庫模式）
- status: GREEN

## Hard Gate 2 — 雙向變異
- collect: `.venv\Scripts\python.exe -m pytest tests/soul_access -k mutation --collect-only -q`
  => **32/162 collected (130 deselected)**，收集數 > 0 符合票載。
- 執行：32 項已含於 Gate 1 的 555 全綠內；每項內建 綠→紅→還原綠 斷言
  （`*_turns_red_then_revert_green` / `*_reverted_*_green`），無 DUD、無中途停止。
- status: GREEN（放寬／收緊均轉紅、還原綠；未觸發 STOP 條件）

## Hard Gate 3 — 全庫回歸（保留原始結果，五紅基線比對）
- command: `.venv\Scripts\python.exe -m pytest -q tests`
- result: **5 failed, 8974 passed, 32 skipped, 32 subtests passed in 974.15s (0:16:14), exit 1**
- 失敗逐項（與 `five-red-baseline.txt` 逐項比對，nodeid＋原因皆一致，零新增）：
  1. `tests/interfaces/test_journal.py::test_gap_backend_completed_unreachable`
     — `assert 'transport_sent' == 'completed'`（journal 不標 backend 完成；基線同因）
  2. `tests/interfaces/test_journal.py::test_gap_backend_dest_completion_rejected`
     — `JournalError: bad dest`（基線同因）
  3. `tests/interfaces/test_journal.py::test_gap_cancel_absent`
     — `AttributeError: 'Journal' object has no attribute 'cancel'`（基線同因）
  4. `tests/interfaces/test_runtime_port.py::test_gap_no_side_effect_retry_still_uncertain`
     — `assert [] == [1]`（基線同因）
  5. `tests/interfaces/test_runtime_port.py::test_gap_decision_port_result_absent`
     — `ImportError: cannot import name 'Decision'`（基線同因）
- SKIP-BASELINE（全庫模式）：`skip-set STABLE skipped=32 baseline=32 added=0 removed=0`。
- status: GREEN（零新增回歸；相同五紅≠全綠，不解除部署門禁；未 skip/xfail/刪測試/放寬斷言）

## Hard Gate 4 — CLI 沙盒對帳（固定槽 --simulated-time 2026-11-12T03:00:00Z）
- commands:
  `.venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z` => exit 0
  `.venv\Scripts\python.exe scripts/eval_lifecycle_cli.py --simulated-time 2026-11-12T03:00:00Z --json` => exit 0
- 來源快照：source_dir = `data/elevation`（唯讀），source_is_repo_production = True；
  唯一寫入 = 自建 temp 沙盒（本輪 `lifecycle_cli_sandbox_*`）；生產零寫入。
- 四項原始輸出（`cli-2026-11-12.json`，乾淨 UTF-8；逐字原始另存 `cli-2026-11-12.raw.txt`）：
  1. sidecar 增量：**3397 -> 3397（新增 0 筆）**
  2. 類型純度：belief/value/trait/essence = **0/0/0/0**（恆等於 0）；
     台賬 EXPECTED 1485 筆（22 cohort，含 LEDGER-1105 的 11-05 93 筆），本輪 hits=0 / pending=0。
  3. 關鍵反例：`6a92c906b245` transitions=0，維持 active。
  4. Prompt：diffs total 0，unexplained 0；weak_prefix 1249 -> 1249（增量 0 = expected 0 + pending 0）。
- verdict: **PASS（exit 0），no_op_note=True，graph_nodes=5183（非空）**
  => 冪等 no-op（該轉的都已轉完），不是失敗。
- 與上一票同槽差異（誠實記錄，非破線）：
  SA-6（11-11）同槽為 PENDING_COHORT exit 3（沙盒 +206）；本次 sidecar 基線已是 3397
  （+216），生產尾部 ts `2026-11-13T03:00:20Z` = 11-12 夜槽（22:00 EST）真實排程寫入。
  即：上一票預測的轉變已由生產夜排程實現，本輪同槽重放自然是 no-op PASS。
  11-06～11-08 未自動入帳（pending=0），符合票載。
- status: GREEN（PASS；無新 pending，無 FAIL；11-06～11-08 不入帳符合預期）

## 五紅基線（tests/interfaces，待全庫完成後對帳）
基線（SA-5/SA-6 既定）：5 failed（test_gap_*：journal 3 + runtime_port 2）+ 41 passed。
相同五紅只能證明「零新增回歸」，不冒充全綠或解除部署門禁。

## Frozen / 越界檢查
- 本票改動：僅 `docs/SA-6-RUNBOOK.md`（增補§7，不改批次）、`logs/ENGINEERING_STATE.md`
  （追加本票狀態）、新增 `verification/sa-deploy-preflight-1/`。
- 產品碼／配置／data/** 零寫入；未殺行程、未清殘留、未啟伺服器、未綁埠、零模型請求。
- Frozen（Agency四階段/TriggerEnvelope/InnerLifeEvent/四handlers/SAGE寫入/衰減門檻/
  T6 403/EXPECTED_RETIREMENT_COHORTS）全部未碰。
