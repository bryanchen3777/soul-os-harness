# 全庫 skip 集合分類報告 — 2026-10-27（量測先行・第三棒基準）

> 來源：`%TEMP%\skip_run1.txt`（2026-10-27 20:05 EDT，`pytest -q -rs` 全量輸出，解譯器＝repo `.venv`）⇒ **7504 passed / 0 failed / 27 skipped**。

> 分類依 Bry 2026-10-27 審定備案：**靜態合約型**（離線 CI／本機單元環境下**恆定** skip，不得因收集順序或機器負載跳動）vs **條件翻動型**（socket／本機埠／外部服務／全域快取與單例／生產資料內容／外部資產存在性）。

## 一、靜態合約型 Skip（17 筆）

| # | 測試檔 | 行 | skip 理由（逐字） | 類型 |
|---|---|---|---|---|
| 1 | `tests/test_extract_and_judge_context_bug.py` | 80 | Requires live LLM API access; opt-in with SOUL_TEST_REAL_LLM=1 + OLLAMA_API_KEY (active provider: ollama) | 外部 opt-in（真實 LLM 預算） |
| 2 | `tests/test_identity.py` | 102 | 需要明確指定 SOUL_OS_LIVE_SERVICE_BASE_URL（隔離實例）；預設 skip，避免測試打生產 :8000 | 外部 opt-in（線上服務唯讀探針） |
| 3 | `tests/test_m5_4_1_inner_life_narrative_audit.py` | 410 | POSIX permissions not available on Windows | 平台／OS 標記 |
| 4 | `tests/test_m5_4_1_inner_life_narrative_audit.py` | 424 | POSIX permissions not available on Windows | 平台／OS 標記 |
| 5 | `tests/test_m6_0_5_2_real_judge_optin.py` | 90 | M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real network test. | 外部 opt-in（真實三裁判） |
| 6 | `tests/test_m6_0_5_5_real_three_judge_e2e.py` | 560 | M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E. | 外部 opt-in（真實三裁判） |
| 7 | `tests/test_m6_0_5_5_real_three_judge_e2e.py` | 411 | M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E. | 外部 opt-in（真實三裁判） |
| 8 | `tests/test_m6_0_5_5_real_three_judge_e2e.py` | 533 | M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E. | 外部 opt-in（真實三裁判） |
| 9 | `tests/test_m6_0_5_5_real_three_judge_e2e.py` | 586 | M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E. | 外部 opt-in（真實三裁判） |
| 10 | `tests//test_plan_a_launcher_v1.py` | 133 | 未設定 SOUL_OS_LIVE_SERVICE_BASE_URL ⇒ 沒有授權目標，無從探測（預設離線；這不是失敗） | 外部 opt-in（線上服務唯讀探針） |
| 11 | `tests/test_private_chat.py` | 113 | 需要明確授權線上服務（SOUL_OS_LIVE_PRIVATE_CHAT + SOUL_OS_LIVE_SERVICE_BASE_URL）；預設 skip，避免測試打生產服務 | 外部 opt-in（線上服務唯讀探針） |
| 12 | `tests/test_private_chat.py` | 131 | 需要明確授權線上服務（SOUL_OS_LIVE_PRIVATE_CHAT + SOUL_OS_LIVE_SERVICE_BASE_URL）；預設 skip，避免測試打生產服務 | 外部 opt-in（線上服務唯讀探針） |
| 13 | `tests/test_private_chat.py` | 149 | 需要明確授權線上服務（SOUL_OS_LIVE_PRIVATE_CHAT + SOUL_OS_LIVE_SERVICE_BASE_URL）；預設 skip，避免測試打生產服務 | 外部 opt-in（線上服務唯讀探針） |
| 14 | `tests/test_private_chat.py` | 167 | 需要明確授權線上服務（SOUL_OS_LIVE_PRIVATE_CHAT + SOUL_OS_LIVE_SERVICE_BASE_URL）；預設 skip，避免測試打生產服務 | 外部 opt-in（線上服務唯讀探針） |
| 15 | `tests//test_server_ops_python_path_v1.py` | 84 | 未設定 SOUL_OS_LIVE_SERVICE_BASE_URL ⇒ 沒有授權目標，無從探測（預設離線；這不是失敗） | 外部 opt-in（線上服務唯讀探針） |
| 16 | `tests/test_work_p1c1_routing.py` | 749 | live DSH test is opt-in (SOULOS_ALLOW_LIVE_DSH_TESTS=1); default OFF because it sends real requests to the production DSH service, which project rule #5 forbids in ordinary test runs | 外部 opt-in（線上 DSH 節點） |
| 17 | `tests/test_work_p1c2_integration.py` | 552 | live DSH test is opt-in (SOULOS_ALLOW_LIVE_DSH_TESTS=1); default OFF because it sends real requests to the production DSH service, which project rule #5 forbids in ordinary test runs | 外部 opt-in（線上 DSH 節點） |

## 二、條件翻動型 Skip（10 筆）

| # | 測試檔 | 行 | skip 理由（逐字） | 機制與處置 |
|---|---|---|---|---|
| 1 | `tests/clients/test_voice_brain_actions.py` | 599 | Client visual assets (.mp4) not bundled in harness git repo | 外部資產存在性（`.mp4` 動作變體不在 harness repo）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_CLIENT_AVATAR_DIR` |
| 2 | `tests/clients/test_voice_brain_actions.py` | 786 | Client visual assets (.mp4) not bundled in harness git repo | 外部資產存在性（`.mp4` 動作變體不在 harness repo）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_CLIENT_AVATAR_DIR` |
| 3 | `tests/clients/test_voice_brain_actions.py` | 1045 | Client visual assets (.mp4) not bundled in harness git repo | 外部資產存在性（`.mp4` 動作變體不在 harness repo）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_CLIENT_AVATAR_DIR` |
| 4 | `tests/clients/test_voice_brain_actions.py` | 1071 | Client visual assets (.mp4) not bundled in harness git repo | 外部資產存在性（`.mp4` 動作變體不在 harness repo）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_CLIENT_AVATAR_DIR` |
| 5 | `tests/clients/test_voice_brain_actions.py` | 1088 | Client visual assets (.mp4) not bundled in harness git repo | 外部資產存在性（`.mp4` 動作變體不在 harness repo）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_CLIENT_AVATAR_DIR` |
| 6 | `tests/test_data_root_isolation.py` | 75 | Another test module leaked SOUL_OS_DATA_DIR into the pytest process at import time (no teardown), so the clean-env precondition this whitelist self-proof depends on does not hold | **收集順序相依**：模組層級 `skipif` 在 import 期求值；全庫唯一 import 期 env 寫入者（`tests/soul/test_decay_worker_settlement.py:61`）若先被收集 ⇒ env 已汙染 ⇒ 本支跳過；機制＝🔴 **順序相依（唯一）**；處置＝D1 移除模組層級寫入 ⇒ 轉為恆執行 |
| 7 | `tests/test_m1_5_night_400_baseline.py` | 77 | server log not found | 生產日誌內容（`重啟後 400 數 < 5`）；機制＝時間／生產內容相依；處置＝D2 → `SOUL_OS_NIGHT_400_BASELINE` |
| 8 | `tests/test_m1_5_night_400_fix.py` | 186 | git 查不到, 跳過 | 生產日誌內容 ＋ git 歷史（`M1.5 修法還沒 commit`／`git 查不到`）；機制＝時間／生產內容相依；處置＝D2 → `SOUL_OS_NIGHT_400_FIX_CHECK` |
| 9 | `tests/test_plan_a_launcher_v1.py` | 106 | v1 baseline: _start_plan_a.ps1 已存在 (C:\Users\bbfcc\.local\bin\soul-os-harness\scripts\_start_plan_a.ps1), 跳過 | 外部檔案存在性（`_start_plan_a.ps1` 已存在 ⇒ v1 基準失效）；機制＝資產存在性相依；處置＝D2 → `SOUL_OS_PLAN_A_LAUNCHER_PATH` |
| 10 | `tests/world/test_phase_b_world_event_id.py` | 454 | future_source 不在 VALID_SOURCES，只有 state 直注路徑可達 evaluated | 圖譜內容（`future_source` 不在 VALID_SOURCES、未於 state 節點被 evaluated）；機制＝生產內容相依；處置＝D2 → `SOUL_OS_PHASE_B_WORLD_LIVE` |

## 三、skip 數時間線（歸檔回歸輸出，count 欄為實測）

| 時間（EDT） | 歸檔檔 | passed | skipped | 備註 |
|---|---|---|---|---|
| 10-21 23:04 | final_regression.txt | 7077 | 24 | |
| 10-22 07:02 / 07:44 | regression_r24.txt / r24b.txt | 7076 | 24 | |
| 10-22 22:22 / 22:40 | campaign_b_full_regression.txt(2) | 7152 / 7154 | 24 | |
| 10-22 23:08 / 23:43 | featchema_full_regression.txt / campaign_b_optionA | 7184 / 7188 | 24 | |
| 10-23 21:02 / 21:17 | goal_merge_regression.txt(2) | 7238 / 7240 | 24 | |
| 10-25 01:12 | full_regression_2026-10-25.txt | 7446 | 24 | |
| 10-25 10:40 / 11:02 / 11:22 | full_regression_2026-10-25_mount/final/v3 | 7464 / 7465 / 7469 | 24 | |
| 10-26 16:30 / 18:27 | full_regress_1026.txt / 1026b.txt | 7465 / 7469 | 24 | |
| 10-27 02:47 | regress_final.txt | 7481 | 25 | ＋1：線上探針改 opt-in（能力移除，**刻意**） |
| 10-27 17:17 / 17:39 | regress_097.txt / regress_097_final.txt | 7496 | 27 | ＋2：兩個 `/health` 探針改 opt-in（**刻意**） |
| 10-27 18:56 | regress_emotion.txt | 7490 | 31 | ⚠️ **中途狀態**（同輪 **8 failed**：emotion 守衛 G1/G2、盤點守衛 i2、repo 衛生守衛、m7 接縫 ×2、m7 self-turn、observability 凍結契約）⇒ 該輪**不是穩定基準** |
| 10-27 19:26 | regress_emotion_final2.txt | 7504 | 27 | 收斂後 |
| 10-27 20:05 | skip_run1.txt | 7504 | 27 | 本報告基準 |

**⇒ 24 連續穩定 16 輪（10-21 23:04 → 10-26 18:27）；10-27 的變動全部有可指認原因**：＋1／＋2 是**刻意的能力移除**（線上探針改 opt-in），31 那輪是**修到一半的過渡狀態**。

## 四、「先前漂移的那 4 筆」元兇——可回溯性與實測結論

**⚠️ 誠實結論（分兩件事）**：

1. **ID 不可回溯**：10-27 那幾輪（17:17／17:39／18:56／19:26／20:05）的歸檔輸出**只留計數**，沒有 `-rs` 的逐條 skip 明細（只有 20:05 那輪有 `SKIPPED [n]` 明細）⇒ **無法回溯取得那 4 筆的測試 ID**。

2. **位置考古法已證偽**：主大腦嘗試用 `pytest -q` 進度點陣（每行 72 個狀態字元）＋ 行尾百分比反推總數來還原位置映射 ⇒ **不可信**：① 各輪的收集集合不同（17:40 新增守衛檔 ⇒ 7531 vs 7504）② 百分比推總數的誤差 ±14 筆 ③ 映射結果把明顯不會 skip 的測試（`test_translate.py` 等）判成 skip ⇒ **證偽，棄用**。

3. **但機制已被定位並實測重現**（這比回溯 ID 更有價值）：

   - **實驗 A**（洩漏者先被收集，與全庫收集順序同構）：
     `pytest -q tests/soul/test_decay_worker_settlement.py tests/test_data_root_isolation.py`
     ⇒ **19 passed, 1 skipped**（`test_allow_real_data_root_marker_opts_out` 被跳過）

   - **實驗 B**（洩漏者後被收集，順序反轉）：
     `pytest -q tests/test_data_root_isolation.py tests/soul/test_decay_worker_settlement.py`
     ⇒ **1 failed, 19 passed**（同一支**執行且轉紅**，失敗訊息＝「白名單測試不應被 autouse fixture 設定 SOUL_OS_DATA_DIR」，但實際是**別的模組在 import 期把它設了**）

   - ⇒ **後果比「skip 漂移」更嚴重：同一支守衛測試會因收集順序而一邊綠一邊紅。**

4. **順序相依性的完整邊界（實測）**：把 5 個條件檔 ＋ `test_data_root_isolation.py` 一起跑，**兩個收集順序各跑一次 ⇒ skip 數相同（11 = 11）** ⇒ 在**沒有匯漏者**的前提下，條件型 skip **全部與收集順序無關** ⇒ **全庫唯一的順序翻轉點就是那個 import 期 env 寫入者**，而 D1 會把它徹底移除。

5. **機制（為什麼會這樣）**：`tests/test_data_root_isolation.py:45` 的 `skipif` 是**模組層級**的 ⇒ 在 **import 期（收集期）**求值；而 `tests/soul/test_decay_worker_settlement.py:61` 的模組層級 env 寫入**也在收集期發生**、且**從不還原** ⇒ 誰先被收集決定了那支守衛是跳過還是執行。🔺 關鍵：`tests/soul/…` 在字母序上**早於** `tests/test_…` ⇒ 全庫收集順序下**必然**先被收集 ⇒ ⇒ 該支在**每次全庫回歸都必然被跳過**（這也解釋了它為何長期只出現在 skip 清單裡）。

## 五、結論（可直接當驗收基準）

- **靜態合約型 17 筆** ＋ **條件翻動型 10 筆** ＋ 未分類 0 筆 ＝ 合計 27 筆（與實測 27 一致）。

- **靜態合約型**：全部是 env opt-in（外部授權閘門）或平台標記 ⇒ 離線環境**恆定 skip**，**不會**因收集順序或負載跳動。

- **條件翻動型**：其中**只有 1 筆與收集順序相依**（`test_data_root_isolation.py`，已實測 A/B 兩序），其餘 9 筆是**環境／生產內容相依**（資產存在性 6 ＋ 生產內容 3）⇒ 與順序無關，但**會隨生產現況變動**。

- **處置**：D1 拔除唯一的 import 期 env 寫入 ⇒ 順序翻轉點消失；D2 把 9 筆環境相依 skip 改成 **明確 env opt-in**（預設空字串 ⇒ 固定理由）⇒ 加上 skip 基準快照守衛 ⇒ **skip 集合零漂移**。

