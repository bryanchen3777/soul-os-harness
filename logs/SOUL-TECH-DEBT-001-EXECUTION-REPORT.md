# SOUL-TECH-DEBT-001 — 執行報告（階段性結案）

**工單**：SOUL-TECH-DEBT-001 測試套件環境修復與存量失敗清理
**負責人**：Lin（主大腦）｜**審批人**：Bryan
**上游**：SOUL-MEM-001 Phase 0（`f2afdd5` + `722244a`）
**日期**：2026-10-12

---

## 0. 一句話結論

在**零改動 `src/`、零改動 `personas/`、零白名單擴張、零 skip/xfail 掩蓋**四條紅線下，全庫紅燈由 **120 → 7（−94.2%）**，其中 **113 筆已清零**，殘餘 7 筆全部有明確歸屬（5 筆＝已立案生產缺陷 SOUL-BUG-002/003/004/005、2 筆＝順序相依且隔離即綠），達成「**零未分類紅燈**」的驗收線一。

> 本節為最終結論。§7 保留的是校準中途的快照（當時殘餘 50 筆），最終分流見 **§9**。

---

## 1. 工單前提的三處錯誤（開工前量測發現）

原始工單由外部 agent 開立，開工前逐項核對現況，**三處描述與 repo 實況不符**：

| # | 工單原文 | 實測 | 處置 |
|---|---|---|---|
| 1 | 「安裝 `pytest-asyncio`」 | **1.4.0 早已安裝於 `.venv`**。真正問題是預設 strict mode 要求 `@pytest.mark.asyncio`，而**全庫標記數為 0** | 改為在 `pytest.ini` 設 `asyncio_mode = auto`，**零安裝** |
| 2 | 隱含「補完 async 後 `bus` fixture 會跟著好」 | 兩者**獨立**。切到 auto mode 後 9 個 `fixture 'bus' not found` ERROR 依舊 | 分開處理 |
| 3 | 驗收「105 → 0」 | 解封 155 個**從未執行過**的 async 測試，失敗總數**不保證單調下降** | 改採「零未分類紅燈」三線驗收（Bry 拍板） |

**關鍵發現：那批報 ERROR 的測試原本是「寫成獨立腳本」的**（檔頭寫「執行方式：python test_xxx.py」），靠檔尾 `if __name__ == "__main__":` 自行餵 `tempfile.mkdtemp()` 與 `Path(__file__).parent.parent`。pytest 沒有 `__main__`，參數就變成找不到的 fixture。

---

## 2. 數字軌跡（`.venv\Scripts\python.exe -m pytest -q tests`）

| 階段 | failed | passed | errors | **紅燈** |
|---|---|---|---|---|
| ① 原狀（strict） | 105 | 6231 | 15 | **120** |
| ② `asyncio_mode = auto` | 91 | 6248 | 15 | **106** |
| ③ 補 `bus`/`tmp_dir`/`project_root` fixture | 94 | 6258 | **2** | **96** |
| ④ A 類第一批校準（37 筆） | 58 | 6294 | 2 | **60** |
| ⑤ A 類第二批校準（7 筆） | **48** | **6304** | **2** | **50** |
| ⑥ A′／B／C 類四批校準 | 19 | 6327 | 2 | **21** |
| ⑦ 新浮現 5 筆處理 ＋ 2 支看門狗 | **9** | **6341** | **0** | **9** |
| **⑧ 最終（全庫，主大腦親跑 `be1b124`）** | **7** | **6343** | **0** | **7** |

**最終（2026-10-13，`be1b124`）：`7 failed, 6343 passed, 23 skipped, 2 xfailed in 684.17s`**
⇒ **紅燈 120 → 7（−94.2%）**，且 7 筆**全部有明確歸屬**（見 §9），達成 Bry 的「零未分類紅燈」驗收線。

**failed 105 → 48（−57）｜errors 15 → 2（−13）｜passed 6231 → 6304（+73）｜紅燈 120 → 50（−58%）**

⚠️ **failed 在 ②→③ 曾由 91 升到 94**，這不是退步：15 個 ERROR 是「根本没跑」的測試，補上 fixture 後開始執行並暴露真實斷言不符。**紅燈總數（failed+errors）才是可比較指標。**

---

## 3. 已完成：環境修復

### 3.1 `pytest.ini` — `asyncio_mode = auto`
解封 **155 個 async 測試**（27 檔）。只影響測試執行方式，不觸碰生產碼。

### 3.2 `tests/conftest.py` — 3 個 fixture
| fixture | 語意來源 | 用途 |
|---|---|---|
| `bus` | `SoulEventBus()` 已啟動，teardown 優雅停止 | 6 個檔案宣告 `async def test_x(bus: ...)` 但全庫從未定義 |
| `tmp_dir` | ≡ 該測試 `__main__` 的 `tempfile.mkdtemp()` | 改由 pytest `tmp_path` 提供，每測試獨立、自動清理 |
| `project_root` | ≡ `Path(__file__).resolve().parent.parent` | repo 根 |

`bus` 的生命週期設計：`start()` 本身 idempotent（`bus.py:217` 有 `if self._running` 守衛），故會自行 `await bus.start()` 的測試不會重複啟動；`stop()` 對已停止的 bus 亦安全。

### 3.3 探測工具的一個自我修正
初版全庫掃描器報出 70 個「缺失 fixture」，但那是 `unittest.TestCase` 自訂斷言參數與 `@pytest.mark.parametrize` 參數的**誤判**。排除這兩類後真正缺失只有 3 個。
**未照初版結果亂定義 fixture** —— 亂定義會讓測試「空過」，正是 Bry 工單明禁的假陽性。

---

## 4. 已完成：A 類 44 筆過期斷言校準

**分三批並行、檔案所有權完全不重疊，執行者回報後由主大腦獨立複核。**

| 批次 | 檔案 | 筆數 | 結果 |
|---|---|---|---|
| A1 | 7 | 19 | ✅ |
| A2 | 3 | 11 | ✅ + 變異檢查 |
| A3 | 6 | 7 | ✅ 6 + 1 筆上呈 |
| 補漏 | 3 | 7 | ✅（第一次分診遺漏） |

### 4.1 核心原則：負向斷言升級為正向行為驗證

舊測試斷言「某 helper／常數／參數**不存在**」。Bry 後來刻意加入那些東西作為修法。校準**不是刪掉測試或改成 `True`**，而是：

- 斷言該物**存在，且被實際呼叫**（補上呼叫點檢查）
- 補上**邊界值斷言**（例：`STALE_THRESHOLD_SEC` 精確等於 1800，並在 1799/1801/0 三邊界驗證 `_is_bry_online`）
- 補上**順序斷言**（例：uvicorn pre-check 必須排在 `Start-Process` 之前）
- 補上**呼叫次數斷言**（例：`_is_bry_online` 恰好吃兩次 —— group 路徑 715、private 路徑 1470）

⇒ 未來若有人回退修法，測試仍會第一時間響起警報。

### 4.2 校準範例（每筆都有 `src/` 行號佐證）

| 測試 | 舊斷言 | 新斷言 | 證據 |
|---|---|---|---|
| `stale_filter_v1::test_c` | proxy 不應含 `STALE_THRESHOLD_SEC` | 存在且 **= 1800** + 三邊界行為 | `proxy.py:100` |
| `stale_filter_v1::test_d` | 不應有 `_is_bry_online` | 存在且恰呼叫 **2 次** | `proxy.py:919, 715, 1470` |
| `finish_length::test_a` | 不應有 `_safe_truncate_on_length` | 存在**且被呼叫** | `proxy.py:2800, 1900` |
| `m0_4::test_01` | `DIARY_MAX_CLEAN_CHARS == 50` | **= 80**，並註明 Bry 2026-08-06 21:30 拍板理由 | `diary.py:66` |
| `m1_6::test_01` | proxy 不應有 strip-action | 存在且套用於 `audio_text` 出口 | `proxy.py:2859, 3415` |
| `server_ops_v1::test_...no_uvicorn_pre_check` | 不應有 uvicorn pre-check | 存在**且排在 `Start-Process` 前** | `server_ops.ps1:67-81, 109` |

### 4.3 執行者主動拒絕盲目 re-bless（重要）

`test_seeded_personas_byte_identical_to_baseline` 斷言當前 persona 與快照 byte 相同。A3 執行者**主動停下回報**，未直接覆蓋快照。

主大腦獨立驗證後才動手：三個 persona 變更有標題與日期的 commit 在後，其中 `fb056b0` (2026-10-07, SOUL-REM-01) 將 Rem 壓縮 **−50.6%**，commit body 明列 Preserved/Removed/Moved-out 三段，且 **Canon Memory 12 beats 維持 byte-identical 且有專屬回歸套件 `tests/soul/test_rem_soul_regression.py` 保護**。重跑時 **7 份 persona 完全未動、僅 3 份變動**，與 commit 完全吻合。

### 4.4 執行者自我修正
A1 抓到一個**原本因巧合而通過**的測試（斷言 `_proactive_agents` 不存在，但真實屬性名為 `_proactive_agents_whitelist`），一併修正。
A2 抓到**自己寫錯的斷言**（em-dash `——` Bry 刻意保留以維持 mahiru 對話風格），改為斷言「刻意保留」。

---

## 5. 護欄：攔下真問題，並修正結構性脆弱

### 5.1 攔截
`tests/infra/test_no_production_spawn_guard.py::test_no_pinned_service_address_in_tests` 轉紅，報 `tests/test_plan_a_launcher_v1.py:128` 硬寫 `http://127.0.0.1:8000`（AGENTS.md §5 明禁）。

### 5.2 根因：白名單以「檔案 + 行號」為鍵
`git show HEAD` 確認該字面值**原在第 94 行、本次未被改動**，純粹因上方淨增 34 行而漂移。同一脆弱性一次爆三處：

| 白名單 | 記錄 | 實際 | 來源 |
|---|---|---|---|
| address | `test_plan_a_launcher_v1.py:94` | 128 | 本次工作 |
| spawn | `test_plan_a_launcher_v1.py:68` | 102 | 本次工作 |
| spawn | `test_life_thread_sim_time.py:487/517` | 508/538 | **既有技術債** |

### 5.3 處置（未擴張任何白名單）
改行號前**逐一驗證豁免前提仍成立**，特別是 spawn 豁免理由「`skipTest()` 在前，斷言檔案不存在故不會啟動任何東西」——確認 `skipTest()` 仍在 subprocess 之前（99-100 行 vs 102 行），且檔案現存故永遠 skip。

修正後：`spawn_sites=22 allowlisted=22`｜`address_hits=9 allowlist=9`｜`kill_hits=0 allowlist=1`｜**`tests/infra` 64 passed**。三個 `EXPECTED_*_ALLOWLIST_SIZE` 鎖定值全部未變。

### 5.4 建議立項（Bry 已記錄）
`SOUL-INFRA-GUARD-001`：將護欄白名單改為以「函式名／AST 節點／內容雜湊」為鍵，消除行號錨定的結構性脆弱與警報疲勞。

---

## 6. 四條紅線驗收（主大腦獨立複核）

```
✅ src/            零改動
✅ personas/       零改動
✅ configs/ data/  零改動
✅ scripts/        僅既有未追蹤的 _watchdog_hidden.vbs（非本次產生）
✅ 本次改動的測試檔 無任何新增 skip / xfail
✅ 護欄白名單大小   address=9  kill=1  subprocess=22（全數未變）
```

改動規模：**22 files changed, +1058 / −515**，全部落在測試與測試配置層。

---

## 7. 殘餘 50 筆：完整歸屬（零未分類）

### A′ 9 筆 — **又是過期斷言**（主動更正先前的錯誤分類）

最初歸為「B 環境耦合」，但查實際失敗原因後否證：

| 測試 | 筆數 | 實際原因 |
|---|---|---|
| `test_plan_a_launcher_v2` | 2 | 斷言 hermes venv 路徑；Bry 2026-08-15 已改用 `soul-os-harness\.venv`（hermes venv 缺 networkx） |
| `test_server_ops_python_path_v2` | 2 | 同上 |
| `test_log_backup_fix_v2` | 5 | 斷言 `Move-Item`／`yyyyMMdd_HHmmss` 在 `Start-SoulOsServer` 內；CRASH-OBS-1（2026-09-10）已拆至 `_nohup_log_rotation.ps1` |

⚠️ 若照原分類加 `skipif`，會**以「環境問題」之名再藏一批過期斷言** —— 正是本工單要消滅的病。

### B 9 筆 — 真環境耦合（需 `skipif` 守門員）
- `tests/clients/test_voice_brain_actions.py`（5）— **待 Bry 裁示**：報「找不到動畫檔 `rem_head_pat_enjoy.mp4`」。若資產本應存在 → 真缺失，須補資產並立 SOUL-BUG；若本就不在這台機器 → 才可 skipif。兩者處置相反，未自行決定。
- `tests/test_m1_5_night_400_e2e.py`（2）、`tests/test_websocket_e2e.py`（1）— 需服務運行
- `tests/test_data_root_isolation.py`（1）— 依賴真實資料根佈局

### C 32 筆 — 真實邏輯待判定（**尚未逐案鑑識，不預告結論**）

```
 7  test_elevate_mechanism.py            ← 昇降證據機制，建議第一條追查線
 3  test_m5_3_s2_retrieval_diagnostic.py
 3  test_translate.py
 2  test_elevation_evidence_scope_a1.py
 2  test_event_generation_v2.py          ← 缺 mock_backend fixture
 2  test_m3_1_phase_c.py / phase_d.py
 1  ×13（c31 / chrono / group_history_framing_v2 / m2_0_inner_life_v2 /
        m3_1_phase_b / m3_2 / m5_3_s2_b / memory_middleware /
        proactive_whitelist_v2 / self_check_v1 / timezone_fix_v1）
```

**必須明說**：這 32 筆我尚未逐案看 traceback，因此**不宣稱其中有 N 個真 Bug**。SOUL-MEM-001 那 44 筆也曾「看起來像真問題」，結果全是過期斷言 —— 同樣的誤判不能犯第二次。

---

## 8. 本報告未驗證的事項

- C 類 32 筆**未逐案鑑識**，無法判定真偽。（**已於 §9 更新：32 筆已全部鑑識完畢**）
- A2 批次執行者**未做變異實驗**（依 AGENTS.md 規則 7，本機無安全隔離環境可還原修法）。抗回退性為結構論證（成對邊界斷言）而非實測。
- 最終數字為 CJK 口徑估算（沿用 SOUL-MEM-001 §1.1 聲明）。
- 全庫回歸耗時約 10 分鐘，數字取自 `.venv\Scripts\python.exe`（PATH 上的全域 pytest 為另一個世界）。
- `tests/clients/test_voice_brain_actions.py` 動畫資產歸屬待裁示，未擅自 skip。

---

## 9. 最終狀態與 7 筆殘餘紅燈的完整歸屬（2026-10-13，主大腦親跑）

### 9.1 定案數字

```
$ .venv\Scripts\python.exe -m pytest -q tests
7 failed, 6343 passed, 23 skipped, 2 xfailed, 27 subtests passed in 684.17s
```

commit：`be1b124`（test-only；`src/`、`data/`、`personas/`、`configs/` 零改動）

### 9.2 七筆逐筆分流（**零未分類**）

| # | 測試 | 歸屬 | 證據 |
|---|---|---|---|
| 1 | `test_m5_3_s2_b_normalization.py::test_s2_b_4_negative_zero_false_positive` | **SOUL-BUG-002** | 工單已立案；單 token 零區辨力放行 |
| 2 | `test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_4_negative_no_false_positive` | **SOUL-BUG-002** | 同一缺陷的另一種回報 |
| 3 | `test_chrono_integration.py::test_chrono_block_reaches_llm_prompt` | **SOUL-BUG-004** | chrono 到不了 prompt（雙端皆斷） |
| 4 | `test_timezone_fix_v1.py::test_format_localized_weekday_matches_calendar` | **SOUL-BUG-005** | 星期索引錯一格 |
| 5 | `test_websocket_e2e.py::test_inject_tick_triggers_agent_speak` | **SOUL-BUG-003** | 端點契約已撤銷 |
| 6 | `test_m2_0_inner_life_v2.py::test_e_build_messages_group_with_real_inner_life` | **D 類：順序相依** | **隔離執行即綠**（見 9.3） |
| 7 | `test_self_check_v1.py::test_self_check_v1` | **D 類：順序相依** | **隔離執行即綠**（見 9.3） |

⇒ **5 筆＝已立案的生產缺陷**（002／003／004／005），**2 筆＝測試間狀態污染**。
**沒有任何一筆屬於「不知道為什麼紅」。**

### 9.3 D 類的證據（主大腦親驗）

```
$ .venv\Scripts\python.exe -m pytest -q tests/test_m2_0_inner_life_v2.py tests/test_self_check_v1.py
13 passed in 3.38s
```

兩檔單獨跑全綠 ⇒ 失敗由**更早的測試遺留全域狀態**造成，非自身邏輯錯誤。
`pytest-randomly` 未安裝（僅 `pytest` 9.1.1 ＋ `pytest-asyncio` 1.4.0）⇒ 不是隨機順序，是**固定的污染來源**。

⚠️ **未完成的排查**：找出是哪一支測試污染了這兩支，需要整庫二分（每輪約 11 分鐘）。
本工單不擅自開這條線，已如實記錄為待辦。

### 9.4 看門狗（Bry 2026-10-13 裁示）

`tests/test_soul_bug_watchdogs.py` 兩支 `@pytest.mark.xfail(strict=True)`：
`test_elevation_pattern_evidence_accumulation_must_reach_two`（SOUL-BUG-001）、
`test_chrono_context_must_reach_prompt`（SOUL-BUG-004）。
`--runxfail` 實測兩者皆**因正確理由**失敗，非假哨兵。

### 9.5 一個由本次工作暴露的 repo 衛生紅燈（已隨 commit 解除）

提交前 `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked`
會因 `tests/test_soul_bug_watchdogs.py` 未被追蹤而紅燈。
⇒ **護欄自己攔到了未提交狀態**，這正是它存在的價值。`be1b124` 提交後解除。

### 9.6 相依性提醒（給未來的修復者）

SOUL-BUG-001 若修復，`tests/test_elevate_mechanism.py` 與 `tests/test_elevation_evidence_scope_a1.py`
共 6 筆已校準到「持久層去重」現行語意的斷言**必須跟著回退**（工單 §六）。
同理 SOUL-BUG-005 修復時，`tests/test_timezone_fix_v1.py` 內嵌行號的斷言訊息需一併更新。