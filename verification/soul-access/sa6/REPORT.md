# SA-6 對帳報告（Soul 鏈第 7/7 票：分批放行準備，整鏈 Hard Gate）

- 工單：SA-6（主大腦 Sol decision-complete 工單；執行者照做、不重裁）。
- 範圍：新建 `tests/soul_access/test_t7_rollout.py`、
  `docs/SA-6-RUNBOOK.md`、本目錄。**產品檔零改動**；T6、CLI 台帳、守衛、
  Frozen Contract 一律未碰。
- 解譯器口徑：倉內 `.venv/Scripts/python.exe`（Python 3.11.15）。
- HEAD：`8bc9600`（SA-5 已合入）。
- 狀態：**準備完成；部署 BLOCKED（見 §8）。**

## 1. 交付內容

| # | 檔案 | 異動 |
|---|---|---|
| 1 | `tests/soul_access/test_t7_rollout.py` | 新建（17 項）：T7-1 同 handler 開→關→開＋5 種壞內容／缺檔皆 403 零 generate、T7-2 真實 PoolStore/loop UNKNOWN 隔離經開關不復活、T7-3 A 批 4 放行＋16 拒絕、T7-4 B 批新增 16／併集 20／快照外檔案編輯無效、T7-5 批次歸屬與 tools 路徑無關、T7-6 放寬／收緊／early-B 三向變異＋全 oracle 還原綠、T7-7 無 spawn／socket／kill／產品路徑 AST＋批次摘要釘死 |
| 2 | `docs/SA-6-RUNBOOK.md` | 新建：採用裁決（503→403）、A/B 批次、放行順序、回滾、接線缺口（BLOCKED 理由）、驗收對應 |
| 3 | `verification/soul-access/sa6/` | 本目錄（對帳報告＋`evidence.jsonl` 104 筆＋CLI 原始 JSON） |

## 2. 票面字樣裁決（已採用，不在本票改動）

- 凍結產品行為（`src/external_gateway/app.py:200`、
  `src/soul_access/bridge.py:179` 明文）為 **`LiveRefused(403, "forbidden") →
  HTTP 403**。本票禁動產品檔，故 T7 釘死實際 403。
- 採用決定（主大腦 Sol 裁定，轉述——逐字原文由主大腦持有）：
  **改票面字樣（503→403），不另開產品票，不改 T6。**
  未來把期望改回 503 的編輯會被 T7-1／T7-6 直接咬紅。

## 3. 程式驗收（新增測試）

- `pytest -q tests/soul_access -k t7`：**17 passed**。
- `pytest -q tests/soul_access`：**136 passed**（T0–T7 全綠；零新增紅）。
- 三守衛（scope registration＋observability completeness＋no-production-spawn）：
  **140 passed**。

## 4. 雙向變異（in-file 綠→紅→綠，非空轉守衛）

| 變異 | 做法 | 結果 |
|---|---|---|
| 放寬（常開） | `is_live_inbound_open` 恆 True，關閉時照樣放行 | RED（放行＋generate 1）；還原→403 零 generate（green） |
| 收緊（常閉） | 恆 False，開啟時照樣拒絕 | RED（403）；還原→放行（green） |
| early-B | Phase A 提前安裝 A∪B 名單 | RED（B 路由被 admit，oracle 咬 AssertionError）；revert 安裝→綠 |
| 還原全 oracle | R 關拒／R 開行／B-phase-A 矩陣端到端 | GREEN，20 筆（4 admit＋16 refused-403） |
| 變異未遂守衛 | 任一變異未轉紅即 `MUTATION DUD -- STOP` | 全程未觸發 |

## 5. 全庫回歸（`-q tests`，`.venv`，後台）

- `pytest -q tests`（`.venv`，後台，耗時 973.67s）：
  **8875 passed, 32 skipped, 32 subtests passed, 6 failed**。
- 6 項失敗＝G1–G5 五紅（前鏈既有，見 §7）＋ hygiene 守衛 1 紅
  （offender 恰為本票新檔 `test_t7_rollout.py` 未追蹤，
  合入後即綠，SA-5 §8 同形；合入後複驗見下）。
- **零新增回歸**（相對 SA-5 的 8858 passed，增量恰為 T7 的 17 項）。

### 合入後複驗（待填 commit，見 §9）

## 6. CLI 沙盒快跑對帳（`--simulated-time 2026-11-12T03:00:00Z`，exit 3）

- **VERDICT: PENDING_COHORT**（exit 3）——未核准的退役批次，非 PASS、非破線。
- 四項對帳：
  1. sidecar 增量：2975 → 3181（＋206 筆轉變；`no_op_note = False`，圖非空故非 no-op）。
  2. 轉變類型純度：pattern 116（正常代謝）＋value 89＋belief 1＝206；
     soul 型 90 筆恰等於 pending 退役 90 筆（cohort date 2026-11-04）；
     trait／essence 恆 0。
  3. 逐 ID 核驗：90 筆 pending ID 與核准台帳 1302 筆**交集為 0**（逐 ID 查
     `approved_retirement_ids()`），故未核准——CLI 不得自行核准，
     台帳追加工單由主大腦依實測核准後生效。
  4. 關鍵反例節點：`key_node 6a92c906b245 transitions = 0`（維持）。
  5. Prompt 投影：`prompt_diffs_unexplained = 0`（46 筆 diff 全由預期代謝解釋）。
- `graph_nodes = 4680`，`proven_pattern_dormancy_count = 19`
  （rejected 0），`weak_prefix 1066 -> 1156`。`undelivered = 0`，`failures = []`。
- 生產 `data/elevation/` 唯讀（`source_is_repo_production = True`，
  寫入僅自建 temp 沙盒 `lifecycle_cli_sandbox_*`）。
- 原始輸出：本目錄 `cli-2026-11-12.json`。

## 7. 基線重現（HEAD `8bc9600` 同 nodeid 既有紅）

- 同 5 nodeid 全紅（0.67s）：
  `test_gap_no_side_effect_retry_still_uncertain`、
  `test_gap_decision_port_result_absent`、
  `test_gap_backend_completed_unreachable`、
  `test_gap_backend_dest_completion_rejected`、
  `test_gap_cancel_absent`。
- 結論：全庫若現此 5 紅＝前鏈既有，不消紅、不冒綠。

## 9. 合入後複驗（本地 commit `d3f809e`，未 push）

- `pytest -q tests/infra/test_repo_hygiene_tracked_tests.py tests/soul_access
  tests/infra/test_ei1_scope_registration_guard.py
  tests/test_observability_completeness_1.py
  tests/infra/test_no_production_spawn_guard.py`：
  **284 passed**（hygiene 守衛合入後轉綠；§5 的唯一待合入紅已消除）。

## 10. Frozen Contract／部署狀態

- Frozen Contract：Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／
  SAGE 寫入邏輯皆未碰；`data/**` 零寫入（T7-7 AST 探針無產品路徑寫入點）。
- 部署：**BLOCKED**——倉內無「在線 handler＋真實 generate＋固定絕對路徑
  release 檔」組合佈線，亦無核准的生產絕對路徑（見 RUNBOOK §5）。
  沙盒驗的是決策邏輯，不是生產接線。接線方案與生產路徑核准下達前，
  任何真實放行操作一律禁止。
- 意外：無（除 §2 票面字樣，已採用裁決）。
