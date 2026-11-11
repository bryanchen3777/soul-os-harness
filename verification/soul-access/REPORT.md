# SA-5 對帳報告（Soul 鏈第 6/7 票，整鏈 Hard Gate）

- 工單：SA-5（主大腦 Sol decision-complete 工單；執行者照做、不重裁）
- 範圍：新建 `tests/soul_access/test_t5_gates.py`、
  `tests/soul_access/test_t6_release.py`；更新
  `tests/infra/test_ei1_scope_registration_guard.py`（SA_FILES 第七 cohort）、
  `tests/test_observability_completeness_1.py`（allowed 加 11 筆＋逐票註記）；
  新增本目錄。**產品檔零改動**。
- 解譯器口徑：倉內 `.venv/Scripts/python.exe`（Python 3.11.15）。
- HEAD：`40caa44`（SA-4 已合入）。

## 1. 交付內容

| # | 檔案 | 異動 |
|---|---|---|
| 1 | `tests/soul_access/test_t5_gates.py` | 新建（23,787 bytes）：T5-1 無工具鏈 1/1、T5-2 工具鏈 2/2＋soul 靜默、T5-3 重播零新增、T5-4 乾淨提交＋哨兵零、T5-5 未 join 禁 TAIL＋隔離、T5-6 放寬／收緊／escalation 三向變異＋還原綠、T5-7 四組 AST 閘、T5-8 硬封鎖探針 |
| 2 | `tests/soul_access/test_t6_release.py` | 新建（18,293 bytes）：T6-1 開檔放行／關閉・缺檔・壞 JSON・非 dict・錯 status 皆 403、T6-2 關閉／重開不復活 UNKNOWN 隔離列、T6-3 兩批路由證據相異、T6-4 繞過閘／強制開門雙向變異＋還原綠、T6-5 release 純度＋fail-closed AST |
| 3 | `tests/infra/test_ei1_scope_registration_guard.py` | SA_FILES 第七 cohort（11 檔，摘要 `8910323e…52485`）＋子集／互斥／摘要／remainder 邏輯＋constant／digest／required／missing／adjacent-path 測試 |
| 4 | `tests/test_observability_completeness_1.py` | allowed 加 11 筆＋ SA-5 逐票註記；原摘要／原 baseline／frozen 摘要一律不變 |
| 5 | `verification/soul-access/` | 本目錄（對帳報告＋原始輸出） |

## 2. 票面字樣差異（主大腦已裁決，SA-6 同步）

- 票面寫「熱關新入站 503」；凍結產品行為（`src/external_gateway/app.py`、
  `src/soul_access/bridge.py:179` 明文）為 **`LiveRefused(403, "forbidden")`
  → HTTP 403**。產品檔禁動，故 T6 釘死實際 403。
- **Sol 判決（bg_e64965be，已採用）**：改票面字樣（503→403），不另開產品票，
  產品與 T6 不改。SA-6 runbook 收錄逐字句，T7-1／T7-6 把 403 釘死為綠。

## 3. 程式驗收（新增測試）

- `pytest -q tests/soul_access`：**119 passed**（T5 16 項＋T6 16 項全綠；解譯器 `.venv`）。
- `pytest -q tests/soul_access -k t5`／`-k t6`：皆 collect > 0（marker 命名約束滿足）。
- 三守衛（scope registration＋observability completeness＋no-production-spawn）：
  **140 passed**。

## 4. 雙向變異（in-file 綠→紅→綠，非空轉守衛）

| 變異 | 做法 | 結果 |
|---|---|---|
| T5 放寬指紋 | 去掉 backend 欄位，backend 漂移誤重播 | RED（誤 REPLAY）；正常閘同漂移→CONFLICT（green） |
| T5 收緊閘 | `decide` 強制拒（合法輪零寫） | RED（合法輪存零）；還原→5 sinks 全寫（green） |
| T5 escalation | runner 半途碰 soul hook | RED（`_assert_soul_silent` 咬）；誠實 runner 綠 |
| T5 還原 | 合法工具請求重跑 | GREEN，(2,2)，soul 靜默 |
| T6 繞過閘 | 跳過 release 檢查的 handler 關閉時放行 | RED（放行）；誠實 handler 同檔 403（green） |
| T6 強制開門 | monkeypatch `is_live_inbound_open` 恆 True | RED（關閉時放行）；還原→403（green） |
| T6 還原 | 開→放行／關→403 無洩漏 | GREEN，拒絕時零 generate 呼叫 |

## 5. 全庫回歸（`-q tests`，`.venv`，後台，耗時 998.37s）

- **8858 passed, 32 skipped, 32 subtests passed, 6 failed**。
- 6 項失敗＝G1–G5 五紅（預期，§7 基線重現）＋ hygiene 守衛 1 紅
  （offender 恰為本票 2 新檔 `test_t5_gates.py`／`test_t6_release.py`，
  單獨重跑已確認；合入後即綠，見 §8）。
- **零新增回歸**。

## 6. CLI 沙盒快跑對帳（`--simulated-time 2026-11-12T03:00:00Z`，exit 3）

- **VERDICT: PENDING_COHORT**（exit 3）——未核准的退役批次，非 PASS、非破線。
- 四項對帳：
  1. sidecar 增量：待主腦判讀（原始輸出見 `cli-output.txt`）。
  2. 轉變類型純度：value/belief/trait/essence 轉變數恆 0；pending 退役 90 筆
     （`pending_retirements_count = 90`，cohort date 2026-11-04，未核准）。
  3. 關鍵反例節點：`key_node 6a92c906b245 transitions = 0`（維持）。
  4. Prompt 投影：`prompt_diffs_unexplained = 0`（46 筆 diff 全由預期代謝解釋）。
- `graph_nodes = 4680`，`proven_pattern_dormancy_count = 19`，
  `weak_prefix 1066 -> 1156`。生產 `data/elevation/` 唯讀（自建 temp 沙盒寫入）。
- **CLI 不得自行核准**：`EXPECTED_RETIREMENT_COHORTS` 追加工單由主大腦依實測核准後生效。

## 7. 基線重現（`32a365c` worktree 同 nodeid 既有紅）

- worktree：`$env:TEMP/sa5-baseline-32a365c`（detached HEAD 32a365c，
  SA-0 之前一個 commit，前鏈 baseline；主倉 `.venv` 直譯器驅動）。
- 同 5 nodeid 全紅（0.65s）：
  `test_gap_backend_completed_unreachable`、
  `test_gap_backend_dest_completion_rejected`、`test_gap_cancel_absent`、
  `test_gap_no_side_effect_retry_still_uncertain`、
  `test_gap_decision_port_result_absent`（末項同為 ImportError，
  與全庫現場一致）。
- 結論：全庫 5 紅＝前鏈既有，不消紅、不冒綠。

## 8. Frozen Contract／意外

- Frozen Contract：Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／
  SAGE 寫入邏輯皆未碰；`data/**` 零寫入（T5-8 探針證實寫入被擋）。
- 意外：無（除 §2 票面字樣差異，已標記待判決）。

## 9. 合入後複驗（commit `f3da42e`，未 push）

- `pytest -q tests/infra/test_repo_hygiene_tracked_tests.py tests/soul_access
  tests/infra/test_ei1_scope_registration_guard.py
  tests/test_observability_completeness_1.py
  tests/infra/test_no_production_spawn_guard.py`：
  **267 passed**（hygiene 守衛合入後轉綠；§5 的唯一待合入紅已消除）。
