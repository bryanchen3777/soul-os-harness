# 工單：FEAT-SIM-HERMETIC-CLOCK（tests/test_sim_accel_thread.py 兩筆紅燈）

> 狀態：**已立項（主大腦 2026-10-30 00:4x EDT 實測定位）** — 待 Owner 裁示方向後排工。
> 🔴 本工單**尚未**實作任何改動；目前 repo 內有 2 筆**既有紅燈**（與 FEAT-UVICORN-ACCEPT-RESILIENCE 無關）。

## 目標

讓 `tests/test_sim_accel_thread.py` 的兩支守門**不再依賴生產語料的當下狀態**，
使「全庫回歸的綠」**不隨生產資料與 UTC 日界漂移**。

## 紅燈事實（主大腦 2026-10-30 實測，附證據）

### 1. 現象

```
FAILED tests/test_sim_accel_thread.py::test_sandbox_run_emits_exactly_one_new_event
FAILED tests/test_sim_accel_thread.py::test_seam_without_seeding_emits_all_36
  → assert report["seam_post"]["total_events"] == 1  ⇒ 實際 0
```

### 2. 與本次改動**無關**（A/B 證明）

`git stash` 暫離本次全部改動後，在 **HEAD 樹（358ac52）** 上重跑**同一組三筆**：

```
3 failed in 13.83s   （tests/inner_life/test_contradiction_backend_llm_guard.py::test_ensure_current_day_resets_the_judge_facing_counter
                     tests/test_sim_accel_thread.py::test_sandbox_run_emits_exactly_one_new_event
                     tests/test_sim_accel_thread.py::test_seam_without_seeding_emits_all_36）
```

⇒ 三筆在**未改動樹上一樣紅** ⇒ 不是 FEAT-UVICORN-ACCEPT-RESILIENCE 造成的。

### 3. 根因（已實測坐實，非推測）

模擬報告（temp 沙盒實跑，`scripts/sim_accel_thread_cli.py::run_simulation`）：

```
target = e81d777e-a5a7-4e80-a065-c68a0fa932b0（agent_mahiru）
  created_at  = 2026-10-15T12:00:50.016407+00:00
  updated_at  = 2026-10-30T02:02:33.848366+00:00   ← 10-29 夜槽（22:00 EDT＝10-30 02:00 UTC）剛寫過
  virtual_now = 2026-10-29T12:00:51.016407+00:00   ← ＝ created_at + 14d + 1s（虛擬時錨）
  idle = virtual_now − updated_at = −50502.8 s（−0.58 天，**負數**）
m2_active_advisory / m2_terminal = {should_mutate: false, advisory: true, reason: "time_horizon_exceeded", idle_days: null}
consolidation = {status: "skipped_not_terminal", reason: "should_mutate_not_true", llm_calls: 0}
seam_pre=0  seam_post=0  seam_again=0
```

因果鏈：
1. `scripts/sim_accel_thread_cli.py` 的**虛擬時鐘由 `created_at` 反推**（`created_at + 14d + 1s`），
   與生產語料的**當下**無關 ⇒ 一旦生產把該線頭的 `updated_at` 前移（夜槽的 transition 會寫 `updated_at`），
   虛擬時鐘就**落在語料之後**（時鐘倒置）。
2. 時鐘倒置 ⇒ `idle = virtual_now − updated_at` 為**負** ⇒ 溶解評估**無法計算閒置軸**（`idle_days: null`）
   ⇒ 只給**建議**（`should_mutate=false, advisory=true`）⇒ **不溶解**。
3. 不溶解 ⇒ 線頭不會轉 `completed`、不會有 `sage_fact_id`
   ⇒ 溶解執行層回 `skipped_not_terminal / should_mutate_not_true`
   ⇒ **接縫沒有可發射的線頭** ⇒ `seam_post = 0`（期望 1）。

⇒ **不是產品邏輯缺陷**（接縫的發射判準本身無日期視窗：`status == "completed"` ∧ `sage_fact_id` 非空
∧ 不在 `_ELEVATION_EMITTED`）；是**模擬器（sim CLI）與其守門**與**生產語料**耦合。

### 4. 隱含的治理病徵

「上一回合全庫回歸 7582 passed / 0 failed」是在 **10-29 18:0x EDT（＝10-29 22:0x UTC）** 取得的；
本次在 **10-30 00:1x EDT（＝10-30 04:1x UTC）** 取得 **7592 passed / 3 failed**。
⇒ **repo 的「綠」有時效性**：它會隨生產語料與 UTC 日界漂移 ⇒ 這是「假綠」家族的一員
（綠不是因為守門有效，而是因為當下資料剛好對得上）。

## 範圍（待裁示後才動手）

- `scripts/sim_accel_thread_cli.py`（虛擬時鐘導出 ＋ 目標挑選）
- `tests/test_sim_accel_thread.py`（兩支守門的期望值）
- 該檔自帶的變異守衛 M1–M5（`SIM_ACCEL_MUTANT`）**必須仍全數轉紅**（不可因為改了時鐘而失效）。

## 處置選項（**待 Owner 裁示**，主大腦已備妥分析但不自做決定）

### 選項 A（主大腦推薦）：模擬器**去生產耦合**（hermetic）
沙盒**不再複製生產 `life_threads.jsonl`**，改由 sim CLI **自產**一組固定的線頭 fixture
（created_at／updated_at 全部相對於**固定的虛擬基準日**），目標線頭由 fixture 指定。
⇒ 優點：守門**與生產資料、UTC 日界完全無關** ⇒ 「綠」不再有時效性（根治假綠家族）。
⇒ 代價：改動較大（sim CLI 的 fixture 生成 ＋ 兩支守門的期望值 ＋ M1–M5 變異錨點可能要跟著調整）。

### 選項 B（最小改動）：讓虛擬時鐘**不得落後於語料**
`virtual_now = max(created_at + 14d + 1s, latest(updated_at) + 1s)`（語料內最新 `updated_at`）。
⇒ 優點：改動小（1～2 行），兩支守門立刻恢復綠。
⇒ 代價：**仍耦合生產語料**（語料若變動，時錨仍會動）⇒ 只是把「時鐘倒置」這個症狀治了，
未根治「綠有時效性」。

### 選項 C（不推薦）：放寬守門期望值
把 `seam_post == 1` 放寬成 `>= 0` 或 `in (0, 1)`。
⇒ ❌ 違反 Owner 裁定「**寧可誠實移除功能，也不放寬判準讓模組假裝能跑**」：
這會讓「接縫有沒有發射新事件」這個守門**永遠不會紅** ⇒ 等於消滅守門本身。

## 驗收（完成的定義）

- [ ] 兩支守門在**生產語料變動**（夜槽跑過、線頭被寫過）之後仍**穩定綠**。
- [ ] 兩支守門在**不同 UTC 日**（至少 2 個不同日界）仍**穩定綠**（可用凍結時鐘的變異實測證明）。
- [ ] 該檔 M1–M5 變異**仍全數轉紅**（每條都有實際轉紅 output）。
- [ ] 全庫回歸 0 failed、skip 集合零漂移（added=0／removed=0）。
- [ ] 若採選項 A：sim CLI **不再讀生產 `life_threads.jsonl`**（守門須能自證這一點）。

## 不做（Out of Scope）

- **不得**改產品側的溶解／接縫判準（`src/soul/life_thread_dissolution.py`／`life_thread_orchestrator.py`）
  —— 本工單修的是**模擬器與守門**，不是產品規則。
- **不得**放寬守門期望值（見選項 C）。
- **不得**動 `data/` 下任何檔案；不得殺行程；不得重啟服務；不得跑 `scripts/manual_*.py`。

## 回報格式

1. 改動檔案清單。
2. 選項 A/B 的落地方案與「綠不再有時效性」的**可驗證證據**（至少 2 個不同 UTC 日界 ＋ 一次生產語料變動後）。
3. M1–M5 變異逐條轉紅 output。
4. 全庫回歸數字（聲明解譯器、skip 漂移）。
5. 是否踩到 Frozen Contract（逐項）。
