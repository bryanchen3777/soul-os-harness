# LIFE-THREAD-M2-DETERMINISTIC-SUNSET 實作紀錄（確定性超期歸檔）

> 工單：`logs/LIFE-THREAD-M2-DETERMINISTIC-SUNSET-WORK-ORDER.md`
> 裁決：Owner 2026-10-21 00:23 —— **採納方案 A（激活 M2 的確定性超期歸檔）**、
> **拒絕方案 B（無腦調大 cap）**；要求「先在虛擬沙盒驗證收束性，完成後再行實施」。
> 本檔＝實作＋驗收紀錄（**未 commit**；由主大腦驗收後自行 commit）。

---

## 0. 一句話結論

**門檻三（連續 7 天可見性）的判準 1，在沙盒第 7 天轉綠：10/10 隻 agent 的 streak 計數器
達 7（實測 9/10 達 8、1/10 達 7）**；機制在生產現況下會對 **7/10** 隻 agent 生效
（外加 1 隻在 21.58 天時補一次，共 **8 筆** sunset），每一次的四項條件逐條成立，
**0 次**誤傷 age ≤ 21 天的線頭。

---

## 1. 改動檔案清單（6 個，全在工單 §2 的授權範圍內）

| # | 檔案 | 型態 | 行數變化 |
|---|---|---|---|
| 1 | `src/soul/life_thread_sunset.py` | **新增** | 純決策層（0 I/O、0 `src.*` import） |
| 2 | `src/soul/life_thread_orchestrator.py` | 修改 | **+156／−0**（接線層；**0 新增 `src.*` import**） |
| 3 | `docs/LIFE-THREAD-ENGINE-CONTRACT.md` | 修改 | §3.1 **新增一條**（既有條款逐字不動、§2.2 未觸碰） |
| 4 | `tests/soul/test_life_thread_m2_sunset.py` | **新增** | 守衛 G1~G7 ＋ 變異 M1~M5（13 支） |
| 5 | `tests/soul/test_sim_accel_streak_7d_sunset.py` | **新增** | 沙盒收束性（10 支） |
| 6 | `logs/LIFE-THREAD-M2-DETERMINISTIC-SUNSET-IMPLEMENTATION.md` | **新增** | 本檔 |

**未動**（工單 §2 的禁止清單，逐條複核）：`src/soul/life_thread_dissolution.py`（M2 純決策層）、
`src/soul/life_threads.py`（M1）、`src/soul/life_thread_dissolution_exec.py`、
`src/soul/life_thread_origins.py`（M4）、`src/paths.py`、`src/inner_life/**`、`data/**`、
`personas/**`、`configs/**`、既有測試檔（SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1／
SIM-ACCEL-STREAK-7D／M5-WIRING／M1／M3／M4 全部**逐位元未動**）。

---

## 2. 設計落點：為什麼「改動落點是接線層」而不是 M2

工單 §0.3 的三處技術性修正已全部落地：

1. **`archived` 狀態不存在** ⇒ sunset 的目標狀態**只有 `completed`**（M1 §2.2 值域不動）；
   `dormant` **不**作目標（INV-1：`dormant` 不催）。
2. **「3 次檢查點無新進展」照字面永不觸發** ⇒ 改採 **agent 層級訊號**：「該 agent 連續
   ≥3 槽未建立任何新線頭」（`slots_since_last_creation`）。
3. **M2 純決策層零改動** ⇒ `src/soul/life_thread_dissolution.py` 的 `git diff` **為空**
   （守衛 G7 以凍結 sha256 釘死：`cc397fe37b1f61aa1740fdcab65821555516396e2b422202903fe5de50c75ed9`，
   已與 `git show HEAD:src/soul/life_thread_dissolution.py` 的 blob 雜湊交叉驗證相等）。

**接線的形狀**（`src/soul/life_thread_orchestrator.py`，M2 評估呼叫點之後、M3 之前）：

```
M1 fold（整檔只 fold 一次）
  ↓
M2 批次評估（既有 evaluate_batch_dissolution，**0 改動**）
  ↓
★ 確定性超期歸檔（本票新增的唯一一段）
  ├─ slots_since = sunset.slots_since_last_creation(all_threads, now, tz)
  ├─ target     = sunset.pick_sunset_target(all_threads, now, active_count, cap, slots_since)
  ├─ M2 單筆裁決 = lt_diss.evaluate_thread_dissolution(target, now, ..., checkpoints_exhausted=True)
  │                 ⇒ target_status=completed／should_mutate=True／reason=checkpoints_exhausted
  ├─ 寫入       = lt_origins.apply_actions(agent_id, [{"op": "complete", ...}], dissolve_hook=<同一個 hook>)
  │                 ⇒ M1 append_transition(status=completed) ＋ **同一個** dissolve_hook
  └─ 釋放容量   = active_threads 移除該線頭（M3 的飽和判定立即看到釋放）
  ↓
M3 閘門 → M4 回合
```

### 2.1 三個「不得不如此」的工程約束（皆為既有護欄所迫，已登記）

| 約束 | 出處 | 本票的解法 |
|---|---|---|
| orchestrator 的 `src.*` import **恰 6 個葉模組**（多一個就紅） | `tests/soul/test_life_thread_m5_wiring.py::test_a1_orchestrator_import_whitelist_only` | 新模組以 **`importlib.import_module("src.soul.life_thread_sunset")` 惰性解析**（與本檔既有的 `src.inner_life.event` 同一手法）⇒ 葉模組數仍為 6 |
| orchestrator **不得**直接呼叫 `lt.append_transition`／`append_dissolved` | 同檔 `test_a0`（AST 掃描禁用呼叫） | 走 **M4 的 `apply_actions`**（＝ M1 寫入的唯一合法路徑）⇒ 狀態轉移與溶解 hook **同一條** |
| orchestrator **0 浮點字面量**、0 `score/weight/confidence/probab` 識別字 | 同檔 `test_a3` | 格式化交給 f-string 格式規格（`_format_age`），模組內**不寫浮點常數** |

### 2.2 工單內部不一致之處（已登記，照「明確邊界語意」實作）

工單 §3.1 同時給了 (a) 公式 `day_ordinal(t) * 2 + (0 if t.hour < 12 else 1)` 與
(b) 邊界語意「`21:59:59` **仍是 morning 槽**；`22:00:00` 起進 night 槽」——**兩者互斥**
（21:59:59 在 (a) 是 night 槽）。⇒ 本票採 **(b)**（工單要求「**必須寫成會紅的測試**」的那組
邊界），且 (b) 與工單 §0.5 的「槽間隔 14h／10h」一致：**槽＝從 08:00 起算的時間區間**。
⇒ `07:59:59` 屬**前一日的 night 槽**（`(day-1)*2 + 1`）、`21:59:59` 屬**當日的 morning 槽**
（`day*2 + 0`）。守衛 G1 把五個邊界時刻的期望值**寫死**（`date.toordinal()` 實測值）。

### 2.3 「slots_since」的兩處取值（工單未明示，照最保守者實作）

- 「**從未建立過**任何線頭」⇒ 回傳 `SUNSET_NO_CREATION_SLOTS`（極大值），
  **不是 0** —— 0 會被誤讀成「剛剛建立」而**放行** sunset（fail-closed 方向反了）。
- 「距上次新建 **0** 槽」⇒ `slot_index(now) − slot_index(last_created)` 的**下限 0**
  （now 早於最後一次建立時不得為負）。

---

## 3. 測試結果

### 3.1 守衛測試（`tests/soul/test_life_thread_m2_sunset.py`）

指令：`.venv\Scripts\python.exe -m pytest -q tests/soul/test_life_thread_m2_sunset.py`
（**解譯器＝repo 的 `.venv`**，Python 3.11.15）

```
13 passed in 1.41s
```

| 守衛 | 內容 | 結果 |
|---|---|---|
| G1 | 槽索引五個邊界（+3 組延伸）**寫死期望值** | ✅ |
| G2 | `slots_since_last_creation`（含「從未建立」＝極大值） | ✅ |
| G3 | 複合條件四項邊界逐項（age 21 天整 False／21 天+1 秒 True／`active<cap` False／槽數 2 False、3 True／非最舊 False／非 active 一律 False） | ✅ |
| G4 | 決定性：同 `created_at` ⇒ 恆選 `thread_id` 字典序較小者（輸入順序**刻意不是**字典序）；跑 10 次一致 | ✅ |
| G5 | 一次評估最多釋放一條 | ✅ |
| G6 | 過期線頭 ⇒ sunset ⇒ **reflection ＋ fact ＋ `status=completed`**（沉澱 LLM 恰 1 次、SAGE 恰 1 次、`dissolved_at` 非 null、`sage_fact_id` 非空）；年輕線頭 ⇒ **0 sunset／M1 逐位元不變／0 LLM／0 SAGE** | ✅ |
| G7 | M2 純決策層零改動（凍結 sha256 ＋ 與 HEAD blob 交叉驗證） | ✅ |

### 3.2 沙盒收束性（`tests/soul/test_sim_accel_streak_7d_sunset.py`）

指令：`.venv\Scripts\python.exe -m pytest -q tests/soul/test_sim_accel_streak_7d_sunset.py -s`

```
10 passed in 9.82s
```

### 3.3 全庫回歸

指令：`.venv\Scripts\python.exe -m pytest -q tests`
（**解譯器＝repo 的 `.venv`**，Python 3.11.15）

```
1 failed, 6768 passed, 24 skipped, 27 subtests passed in 766.06s (0:12:46)
```

**唯一的 1 支紅燈＝`tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched`**
（訊息：`src/ 改動超出許可集: {'src/soul/life_thread_sunset.py', 'src/soul/life_thread_orchestrator.py'}`）。

🔴 **這是「工作樹範圍型護衛」，commit 後即轉綠 —— 與 repo 既定慣例一致，本票刻意不擴張它**：

- 該守衛以 `git diff --name-only HEAD` 取「工作樹 vs HEAD」的改動集合，**任何**新的 `src/`
  改動都會讓它轉紅 ⇒ 它守的是「**未 commit 的工作樹**不得越界」。
- **先例（同構、同處置）**：`c3520cb`（SOUL-BUG-006 Step 1）的 commit message 逐字：
  「第二道 `src_changes <= allowed` 屬**工作樹範圍型護衛**，**刻意不擴張許可集**」，
  其驗證結論逐字為「全庫 6519 passed / **1 failed（工作樹護衛，commit 後轉綠）** / …」。
  ⇒ 本票**不**修改 `tests/test_observability_completeness_1.py`（該檔不在工單 §2 授權範圍內），
  改為**沿用同一處置**：主大腦 commit 後 `git diff HEAD` 為空 ⇒ 該守衛自動轉綠。
- ⚠️ **給主大腦的提醒**：commit 時必須**同時**納入兩個 `src/` 檔案
  （`src/soul/life_thread_sunset.py`、`src/soul/life_thread_orchestrator.py`），
  否則工作樹仍留有 `src/` 改動 ⇒ 該守衛**仍會紅**。

其餘護欄的實測輸出（皆綠）：

- `[ROOT-SCRIPTS-GUARD] scanned=366 spawn_hits=0 kill_hits=0 address_hits=9 allowed_kills=1`
- `[SPAWN-GUARD-R5] scanned=366 spawn_sites=22 allowlisted=22`
  ⇒ 本票的兩個新測試檔**未新增任何 `subprocess.*` 呼叫點**（22 與基線相同；
  scanned 由 364 → 366 只反映多兩個檔案）。

---

## 4. 變異實驗（5 條，逐條轉紅）

**方法（不碰 repo 原始檔、不起子行程）**：機械式單點替換**生產原始碼文字**（替換必須命中
恰一次）→ 寫到 `tmp_path` → 以 `importlib` 載入成**獨立模組物件** → 用**與守衛測試同一段**
斷言去跑那份變異碼 ⇒ 必須拋 `AssertionError`。
🔴 為什麼**不用子行程跑 pytest**：repo 的行程護欄
`tests/infra/test_no_production_spawn_guard.py` 把 `tests/**` 內**每一個** `subprocess.*`
呼叫點釘成 `(檔案, 行號)` 白名單且**大小鎖定** ⇒ 本票不得新增子行程（既有同型條目見
`tests/soul/test_life_thread_origins_m4.py`）。

| # | 變異 | 轉紅的守衛 | 紅燈實測（斷言訊息） |
|---|---|---|---|
| M1 | age 邊界 `>` → `>=`（`if not age > SUNSET_MAX…` → `>=`） | G3 | `age 恰好 21 天必須 False（嚴格大於）` |
| M2 | 移除「容量飽和」條件（`if agent_active_count < agent_cap:` → `if False:`） | G3 | `active_count < cap ⇒ 必須 False（沒有容量壓力就沒有釋放的必要）` |
| M3 | 「≥3 槽」改成「≥1 槽」（`SUNSET_MIN_NO_NEW_THREAD_SLOTS: int = 3` → `1`） | G3 | `assert 1 == 3`（常數釘死段先攔下） |
| M4 | 移除「只挑最舊」（`min(active, key=_oldest_key)` → `active[0]`） | G4 | `同 created_at 必須恆選 thread_id 字典序較小者：zzz-tie` |
| M5 | sunset 路徑跳過 `dissolve_hook`（`dissolve_hook=dissolve_hook` → `None`） | G6 | `必須留下非 null dissolved_at（沉澱已完成）` |

**殘留風險（誠實揭露）**：M1/M2/M3 是**純函式**變異（無 I/O、無 LLM、無生產路徑），
故「紅燈」由真實斷言失敗產生（**不是**預測）；M5 變異的是 orchestrator（接線層），
被解除防護的程式碼只會寫 `tmp_path` 內的隔離資料根（沙盒），**不可能**觸達生產或真實 LLM
（socket 攔阻＋真實 proxy 探針同時在場）。故本檔**未**使用 `pytest_runtest_call` 硬攔
（AGENTS.md 鐵律 7 的適用範圍是「移除某道閘門」且被解除防護的程式碼**會真的執行**的變異；
本 5 條皆屬純判定變異，硬攔只會製造「因攔截而紅」的假證據）。

---

## 5. 沙盒收束性對帳

### 5.1 平面（沿用 SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1）

- 假時鐘：`lt.now_utc_iso()` 換成可變假時鐘 ⇒ **0 `time.sleep`**（`time.sleep` 已攔阻自證）。
- 記憶 fork：生產 `data/soul/**` **唯讀**；模擬寫入全部落在 `tmp_path` 的隔離根
  （`SOUL_OS_DATA_DIR`）⇒ 生產 sha256 前後**逐位元相同**（§5.6）。
- **獨立 inode**：劇本 B 的起點由 `shutil.copy2` **實體複製**（**不用 `os.link`**），
  並以 `os.path.samefile` 硬斷言（2026-10-20 23:34 污染事故的教訓）。
- 腳本化 LLM 覆蓋 100% ＋ 真實 proxy 探針（命中即 raise）＋ `socket.create_connection` 攔阻
  ⇒ **0 真實外呼**。
- 世界事件：生產 `data/world/perception_trace.jsonl` 的 **30 筆**合格事件**平移**進模擬窗
  （每 slot 前 14 小時）⇒ M3 的世界碰撞喚醒路徑是「活的」（否則 10 隻 agent 只有 5 隻喚醒過，
  量到的是平面假象而非容量動態）。

### 5.2 劇本 A（順從型 LLM：可關一開一）逐 slot 明細（10 隻 agent 合計）

| slot | 段 | local 日期 | 建立數 | sunset 數 | active 分布 | streak min | streak max |
|---:|---|---|---:|---:|---|---:|---:|
| 00 | morning | 2026-10-21 | 7 | **7** | {1,2} | 1 | 5 |
| 01 | night | 2026-10-21 | 6 | 0 | {1,2} | 2 | 5 |
| 02 | morning | 2026-10-22 | 6 | 0 | {1,2} | 2 | 5 |
| 03 | night | 2026-10-22 | 8 | 0 | {1,2} | 2 | 5 |
| 04 | morning | 2026-10-23 | 6 | 0 | {1,2} | 2 | 5 |
| 05 | night | 2026-10-23 | 6 | 0 | {1,2} | 3 | 5 |
| 06 | morning | 2026-10-24 | 8 | 0 | {1,2} | 3 | 5 |
| 07 | night | 2026-10-24 | 6 | 0 | {1,2} | 4 | 5 |
| 08 | morning | 2026-10-25 | 6 | 0 | {1,2} | 4 | 5 |
| 09 | night | 2026-10-25 | 8 | 0 | {1,2} | 5 | 6 |
| 10 | morning | 2026-10-26 | 6 | 0 | {1,2} | 5 | 6 |
| 11 | night | 2026-10-26 | 6 | 0 | {1,2} | 6 | 7 |
| 12 | morning | 2026-10-27 | 8 | 0 | {1,2} | 6 | 7 |
| 13 | night | 2026-10-27 | 6 | 0 | {1,2} | **7** | **8** |

**建立總數 93 筆**（14 slot × 10 agent ＝ 140 次機會）。

### 5.3 每一次 sunset 的四項條件逐條對帳（劇本 A：7 筆，全部在 slot 00）

| # | agent | thread_id（前 8 碼） | created_at (UTC) | age_days | 條件① `age>21` | 條件② `active≥cap` | 條件③ `slots≥3` | 條件④ 最舊 active | M2 裁決 | reflection prompt |
|---|---|---|---|---:|---|---|---|---|---|---|
| 1 | agent_anna | 661ae92d | 2026-09-18T02:03:07Z | 33.41 | ✅ | ✅ 2≥2 | ✅ 43 | ✅ | `checkpoints_exhausted` → `completed` | ✅ |
| 2 | agent_aoi | b93d6910 | 2026-09-18T02:04:08Z | 33.41 | ✅ | ✅ 2≥2 | ✅ 55 | ✅ | 同上 | ✅ |
| 3 | agent_miku | 7c542bbc | 2026-09-18T02:03:52Z | 33.41 | ✅ | ✅ 2≥2 | ✅ 12 | ✅ | 同上 | ✅ |
| 4 | agent_ram | 364b05d1 | 2026-09-18T02:02:05Z | 33.42 | ✅ | ✅ 2≥2 | ✅ 12 | ✅ | 同上 | ✅ |
| 5 | agent_rem | d532ae07 | 2026-09-18T02:01:46Z | 33.42 | ✅ | ✅ 2≥2 | ✅ 47 | ✅ | 同上 | ✅ |
| 6 | agent_ruka | 39dd946f | 2026-09-18T02:00:55Z | 33.42 | ✅ | ✅ 2≥2 | ✅ 18 | ✅ | 同上 | ✅ |
| 7 | agent_yua | e1c043e9 | 2026-09-18T02:00:30Z | 33.42 | ✅ | ✅ 2≥2 | ✅ 67 | ✅ | 同上 | ✅ |

**7/7 筆的四項條件逐條成立 ⇒ 0 次誤判。**

### 5.4 streak 計數器逐日值（劇本 A，**每隻 agent 各自**）

| agent | 建立數 | 標題相異數 | 最終 streak | 判準 1（≥7） |
|---|---:|---:|---:|---|
| agent_akane | 9 | 9 | 8 | ✅ |
| agent_anna | 9 | 9 | 8 | ✅ |
| agent_aoi | 10 | 10 | 8 | ✅ |
| agent_mahiru | 9 | 9 | 8 | ✅ |
| agent_mai | 8 | 8 | **7** | ✅ |
| agent_miku | 10 | 10 | 8 | ✅ |
| agent_ram | 9 | 9 | 8 | ✅ |
| agent_rem | 9 | 9 | 8 | ✅ |
| agent_ruka | 10 | 10 | 8 | ✅ |
| agent_yua | 10 | 10 | 8 | ✅ |

**達 7 者 10/10**（9/10 達 8、1/10 達 7）⇒ **門檻三判準 1 在第 7 天轉綠**。

**標題多樣性 ＝ 1.0000**（每個 (agent, slot) 唯一標題；10/10 隻 agent 的 distinct/total ＝ 1.0）。

### 5.5 不誤傷統計

- age ≤ 21 天的線頭被 sunset 次數：**0 / 93 筆建立線頭 ＋ 20 顆起始 active 線頭**。
- 被 sunset 的 7 條線頭，age 全部 ∈ [33.41, 33.42] 天（**全部落在 21~33 天的明顯過期組**）。
- 劇本 B（起點＝劇本 A 終態，線頭皆 ≤ 7 天）⇒ **sunset 次數 0**（條件①不成立 ⇒ 不誤傷）。

### 5.6 生產 sha256 前後對帳

- 生產 `data/soul/**/life_threads.jsonl`：**10 個檔案**，沙盒前後 sha256 **逐位元相同**
  （`PROD_UNCHANGED True`）。
- 所有寫入都在 `tmp_path`（`SOUL_OS_DATA_DIR` 隔離）⇒ 生產 **0 寫入**。

### 5.7 總耗時

| 階段 | 耗時（秒） |
|---|---:|
| 劇本 A（14 slot × 10 agent） | 5.45 |
| 劇本 B（起點＝A 終態） | 1.16 |
| 劇本 B'（起點＝生產現況） | 3.06 |
| **合計** | **9.67**（上限 60） |

**0 `time.sleep`**（假時鐘；`time.sleep` 已攔阻自證）。

---

## 6. 明確結論

1. **門檻三判準 1 是否在沙盒第 7 天轉綠？** → **是**。劇本 A 的 streak 計數器
   **10/10 達 7**（實測 9/10 ＝ 8、1/10 ＝ 7），標題多樣性維持 1.0000。
2. **本機制在生產現況下會對幾隻 agent 生效？** → **7/10 隻立即生效**（第一個適用 slot，
   age ≈ 33.4 天），外加 **1 隻**（agent_mai）在 age 21.58 天時補一次 ⇒ 合計 **8 筆**
   sunset，與工單 §0.3 的預期（7/10）**一致**。
3. **誤傷風險** → 實測 **0 筆**（條件①是硬閘門：age ≤ 21 天永不 sunset）。

---

## 7. 🔴 工單 §3.6 對帳項 2 的票面校正（已登記，**未自行改工單**）

工單 §3.6 對帳項 2 寫「劇本 B：**sunset 不得發生**（因為條件 2「容量飽和」與條件 3 在反事實
劇本中的表現，須以實測說明；若劇本 B 反而 sunset，須在報告中解釋並調整條件）」。

**實測事實**：sunset 的四項條件**與 LLM 行為完全無關**（條件①看 `created_at`、②看 active 數、
③看「距上次新建的槽數」、④看最舊）⇒ 只要**起點**是生產現況（10/10 飽和、7/10 最舊線頭
age > 21 天、距上次新建 ≥ 12 槽），**第一個 slot 就會 sunset**，**與劇本 A／B 无关**。
⇒ 「劇本 B 不 sunset」**在生產起點下不可達**。

本票的處理（**不動工單、不動條件**）：

1. **劇本 B 的起點改為劇本 A 的終態**（7 天後，線頭皆 ≤ 7 天）⇒ 滿足「劇本 B 不 sunset」
   這條驗收；其語意（不誤傷年輕線頭）**正是**機制最需要被證明的性質。
2. **另附** `test_scenario_b_from_production_start_sunsets_are_accounted`：以生產現況為起點
   跑同一個反事實劇本，把「真的會 sunset」連同四項條件**逐條釘死**（7 筆在 slot 0 ＋
   agent_mai 1 筆在 slot 3、age 21.58 天），**不藏著**。
3. 反事實對照仍成立：以生產起點跑的劇本 B **不會**轉綠（max streak ＝ 5 < 7）。

---

## 8. Frozen Contract / 意外行為

| 項目 | 結果 |
|---|---|
| M2 純決策層（`life_thread_dissolution.py`） | **零改動**（凍結 sha256 ＋ HEAD blob 交叉驗證） |
| M1（`life_threads.py`） | **零改動**：未新增欄位、未新增事件型別、未改值域（`archived` **不存在**；`dormant` **不**作目標） |
| §2.2 值域 | **逐字未動**（`active / dormant / completed / abandoned`） |
| §3.1 | **純附加**一條（既有三條逐字不動） |
| 容量門檻 | **未調大** `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`、**未調小**任何既有門檻（方案 B 已被拒絕） |
| 旗標 | **未新增**旗標、**未改動**既有旗標預設值（sunset 是**預設啟用**的確定性機制） |
| 溶解路徑 | sunset **走同一個 `dissolve_hook`**（＝ 其他終態線頭那條）⇒ **不是**靜默改狀態 |
| 生產資料 | `data/**` **0 寫入**（sha256 前後逐位元相同） |
| 行程 | **0 啟停**、**0 kill**、未綁任何埠、未對生產發真實請求 |
| 意外行為 | **無**。唯一「非顯式要求」的觀察：orchestrator 的 summary 在**真的 sunset 時**多出\n  `sunset` 鍵（與既有的 `elevation_event_created` 同一模式：**只在真的發生時**才出現；未發生時\n  三鍵 dict **逐字不變** ⇒ `test_life_thread_bootstrap.py` 的鍵集合斷言仍綠） |
| 全庫回歸 | **1 failed / 6768 passed / 24 skipped**。唯一紅燈是 `test_observability_completeness_1.py`\n  的**工作樹範圍型護衛**（`git diff HEAD` 含未 commit 的 `src/` 改動）⇒ **commit 後轉綠**，\n  與 `c3520cb`（SOUL-BUG-006）的**同一處置**（該票亦刻意不擴張許可集）。見 §3.3。 |

---

## 9. 已知限制與風險（含**花錢**項，須回報 Owner）

1. **🔴 生產 `.env` 已開啟 `LIFE_THREAD_CONSOLIDATION_ENABLED=1`**（`configs.loader.load_config()`
   會載入 `.env`）⇒ **生產的 sunset 會真的走完整溶解路徑**（reflection LLM ＋ SAGE fact）。
   - **成本**：每次 sunset ＝ **1 次沉澱 LLM 呼叫**（`reasoning_effort="none"`；執行層
     `CONSOLIDATION_TIMEOUT_SECONDS=120`）。本次量測的生產起點 ⇒ **8 筆 sunset ≈ 8 次呼叫**。
   - **上限**（既有護欄）：`MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY = 3`、
     `MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY = 200` ⇒ **不會失控**。
   - 🔴 **這是花錢事項**：Owner 已在 2026-10-21 00:23 裁決「採納方案 A」，但**成本面**由本檔
     首次量化（上限 ≤ 3 次／agent／日、≤ 200 次／全行程／日），**請主大腦回報 Owner 確認**。
2. **旗標 OFF 時**（例如測試環境）：sunset 仍會把線頭轉為 `completed`，但**沉澱被延後**
   —— 與「LLM 自己關閉的線頭」**完全同待遇**（既有 catch-up 旗標會在之後補做沉澱）。
   ⇒ sunset **沒有**繞過任何既有路徑，也**沒有**製造新的非對稱。
3. **沙盒與生產的差異**：沙盒的 LLM 是腳本化的（production 的 LLM 決策基率未知）⇒
   本檔證明的是「**容量一旦釋放，連續 7 天可見性就有路徑**」，不是「生產的 LLM 一定會配合」。
4. **既有 SIM-ACCEL-STREAK-7D 測試檔的敘事已過時**（不可動，僅登記）：該檔的斷言
   `set(transitioned) <= stub_completes` 只涵蓋 **M4 回合**的轉移，**不含** sunset 的轉移
   ⇒ 仍綠；但它「每一次狀態轉移都來自 LLM 決策」的**敘事**在本票後已不成立
   （sunset 是**刻意新增**的自動路徑）。
5. **`sunset` 條件中的「最舊」是 per-agent 判定**：若同一 agent 有兩條同時過期的 active 線頭，
   機制**一次只釋放一條**（§3.1 純函式 4）⇒ 需要多個 slot 才會逐條釋放（實測：劇本 B' 中
   agent_mai 的第二筆在 slot 3 才被釋放）。

---

## 10. 待主大腦裁示／執行

1. **commit**（本票**未** commit、未 push；已 `git add` 6 個檔案，讓 repo 的
   `tests/infra/test_repo_hygiene_tracked_tests.py`（新測試檔必須被 git 追蹤）保持綠）。
2. **回報 Owner**：§9.1 的成本面（每次 sunset ＝ 1 次沉澱 LLM 呼叫，上限 3 次／agent／日）。
3. **生產啟用**：本票**不**改任何旗標預設值；sunset 隨 orchestrator 上線即生效（預設啟用）。
