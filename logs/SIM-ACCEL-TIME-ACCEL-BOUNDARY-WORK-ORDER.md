# 工單：SIM-ACCEL 時序加速邊界加固（sim_accel_thread 目標挑選排除時序倒置線頭）

> 狀態：**已立項，Owner 2026-10-30 12:3x EDT 核准**（既有紅燈另案處置，不阻斷當晚夜槽）
> 預定窗口：**下週例行窗口**（Owner 裁定：列入技術債梳理清單，於下週例行窗口進行時序加速邊界加固）
> 來源：`logs/ENGINEERING_STATE.md` §0.1.113 §7（既有紅燈盤點）＋ 全庫回歸 2026-10-30 實測

---

## 工單：SIM-ACCEL 時序加速邊界加固

### 目標

讓 `scripts/sim_accel_thread_cli.py` 的加速目標挑選**永不選到「虛擬時點落後於真實時間軸」的線頭**，使接縫的 at-most-once 守衛測試（`seam_post == 1`）在生產資料持續演進下仍穩定成立。

### 範圍

- `scripts/sim_accel_thread_cli.py`
  - `pick_target_thread()`（目前挑 `created_at` **最舊**的 active 線頭）
  - 目標挑選後 `virtual_now` 的可用性守門
- `tests/test_sim_accel_thread.py`
  - 新增／修正目標挑選的守衛斷言
- **不動**：`src/soul/life_thread_dissolution.py`、`src/soul/life_thread_orchestrator.py`、`src/soul/life_thread_consolidation_wiring.py`、`src/soul/life_threads.py`

### 做法（決策已定，執行者照做）

1. **保留 `virtual_now` 的既有契約**：仍是 `created_at + 14 天 + 1 秒`。
   ⚠️ `tests/test_sim_accel_thread.py::test_virtual_now_is_created_at_plus_14_days_plus_one_second`
   已斷言 `virtual_now - created == timedelta(days=14, seconds=1)` 且 `age_seconds == 14*86400+1`
   ⇒ **不得**改動此公式（否則該守衛測試會轉紅）。

2. **在 `pick_target_thread()` 加入「可加速性」判準**（唯一行為改動）：
   線頭 `t` 為**可加速目標** ⇔
   `t.status == "active"` **且** `t.dissolved_at is None` **且**
   `parse_iso(t.created_at) + timedelta(days=MAX_ACTIVE_DURATION_DAYS) + timedelta(seconds=BOUNDARY_EXTRA_SECONDS) > parse_iso(t.updated_at)`
   （即：虛擬時點必須**晚於**該線頭的真實最後更新；否則 idle 軸會變成負數）。

3. **挑選順序**：在可加速者中，取 `created_at` **最舊**者（`created_at` 相同則以 `thread_id` 字典序破）—— 維持既有「最舊優先、確定性可重現」的性質，只多一層可行性過濾。

4. **無可加速者時**：拋 `SimTargetError`，訊息須明列每個候選者的
   `thread_id / created_at / updated_at / virtual_now` 與落後量，格式：
   `[refuse] 沒有可加速的 active 線頭：N 個候選者的 virtual_now 都落後於真實 updated_at`
   ⇒ **明確報錯，禁止靜默挑一個時序倒置者**（靜默降級會讓測試變成假綠）。

5. **`--target-thread` 指定路徑**：仍必須命中 `active` 且 `dissolved_at is None`；若該指定者的
   `virtual_now ≤ updated_at`（時序倒置）⇒ 同樣拋 `SimTargetError`
   `[refuse] 指定的線頭 <id> 的 virtual_now 落後於真實 updated_at，無法加速`
   ⇒ **不得**靜默改挑別的線頭（既有 `[refuse] 指定的線頭 ... 不是 active／未溶解的線頭` 語意不變）。

6. **報告欄位**：在 `report["target"]` 增列 `updated_at`（已存在時沿用）與
   `virtual_now_after_latest_update: true`（布林），
   並在 `report["target_selection"]` 記錄候選者總數、可加速者數、被排除者及其落後量
   ⇒ 讓「為何挑這條」在報告中可稽核。

### 驗收（完成的定義）

- `pytest -q tests/test_sim_accel_thread.py` **全綠**（目前 2 failed / 10 passed → 0 failed / 12 passed）。
- `pytest -q`（repo 的 `.venv`）**0 failed**。
- **新增守衛測試**（守的是「不得再挑到時序倒置者」）：
  1. `report["target"]["virtual_now"] > report["target"]["updated_at"]`（字串轉 ISO 後比較）；
  2. 構造一個 `updated_at > virtual_now` 的候選者，斷言它**不會**被選中
     （單元級：直接測 `pick_target_thread()` 的挑選結果，而非整條鏈路）；
  3. 全部候選者皆時序倒置時，斷言拋 `SimTargetError` 且訊息含「沒有可加速」。
- **生產資料零寫入**：守衛測試須保留現有 `production_sha_snapshot()` 比對（不得放寬）。

### 測試

- `pytest -q tests/test_sim_accel_thread.py`
- `pytest -q tests/soul/test_life_thread_sim_m2_catchup.py tests/soul/test_life_thread_sim_time.py`（鄰近的時序相關守衛）
- `pytest -q`（全庫回歸，`.venv\Scripts\python.exe`；**回報數字必須聲明解譯器**）

### 不做（Out of Scope）

- **不改** `life_thread_dissolution.py` 的語意（age／idle 兩軸互相獨立、邊界嚴格大於 `>`、
  active 降級為 advisory、`allow_soft_archive` 預設 `False`、每日配額不抑制 `should_mutate`）
  —— 那是 M2 的 frozen contract。
- **不改** `virtual_now = created_at + 14d + 1s` 這個公式（見做法 1）。
- **不改**接縫的 at-most-once 機制與 `_ELEVATION_EMITTED` 的行為。
- **不**為了讓測試變綠而放寬 `seam_post == 1`、`seam_pre >= 35` 的斷言。
- **不**修 `data/**` 下的任何生產資料（線頭狀態是生產狀態，發現異常只回報）。
- **不**在本票內處理生命週期台帳（`EXPECTED_RETIREMENT_COHORTS`，見 §0.1.113）。

### Frozen Contract 注意

- **M2 溶解語意**（`src/soul/life_thread_dissolution.py` 開頭「核心裁定」五條）全部凍結。
- **接縫 at-most-once**（`life_thread_orchestrator._ELEVATION_EMITTED`）凍結。
- **`virtual_now` 契約**（`created_at + 14 天 + 1 秒`）凍結。
- **M5 沉澱寫入路徑**（`LIFE_THREAD_CONSOLIDATION_ENABLED` 守門的 `dissolve_hook`）凍結。

### 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗，**須聲明解譯器＝repo `.venv`**）
- 目標挑選前後對照（原本挑到哪條 / 修正後挑到哪條 / 為何）
- 是否踩到 Frozen Contract / 意外行為
- 生產資料是否被寫入（應為「否」，附 `production_sha_snapshot()` 比對結果）

---

## 附：2026-10-30 根因實測證據（唯讀重演，供執行者複核）

以 `agent_mahiru`（測試檔的 `AGENT_ID`）在 temp 沙盒重演 `run_simulation(seed_registry=False)`：

| 觀測項 | agent_mahiru（失敗） | agent_yua（對照，成功） |
|---|---|---|
| 目標線頭 | `e81d777e-…`（created 2026-10-15T12:00:50Z） | `b027e02c-…`（created 2026-10-22T12:00:07Z） |
| 真實 `updated_at` | **2026-10-30T02:02:33Z** | 2026-10-30T02:01:35Z |
| `virtual_now`（created+14d+1s） | **2026-10-29T12:00:51Z**（**落後真實 updated_at**） | 2026-11-05T12:00:08Z（領先） |
| `m2_terminal` | `target_status=active`、`should_mutate=false`、`advisory=true`、`has_reflection_prompt=false` | `target_status=completed`、`should_mutate=true`、`advisory=false`、`has_reflection_prompt=true` |
| `consolidation` | `status=skipped_not_terminal`、`reason=should_mutate_not_true`、`llm_calls=0` | `status=consolidated`、`llm_calls=1` |
| `seam_post.total_events` | **0**（期望 1） | 1 |

機制：`virtual_now` 落後於真實 `updated_at` ⇒ `idle = virtual_now − updated_at` 為**負** ⇒ idle 軸不可能超限；
終態路徑拿不到 `should_mutate` ⇒ 沉澱被跳過 ⇒ 沒有 `sage_fact_id` ⇒
接縫的發射條件（`status == completed` ∧ `sage_fact_id` 非空 ∧ 未在 `_ELEVATION_EMITTED`）不成立 ⇒ `seam_post == 0`。

⚠️ 該線頭自 2026-10-15 起每晚仍被夜槽追加 `updated` 事件（2026-10-30T02:02:33Z 為最新），
故「挑最舊的 active 線頭」會隨生產演進**愈來愈容易**挑到時序倒置者 ⇒ 這是**會持續惡化**的既有問題，非偶發。
