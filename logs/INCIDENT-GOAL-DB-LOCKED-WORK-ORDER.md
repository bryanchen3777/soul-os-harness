# 工單：INCIDENT-GOAL-DB-LOCKED

> **開立**：主大腦（2026-10-23 19:4x EDT）
> **依據裁定**：Owner (Bryan) 2026-10-23 19:2x「立即立案排查生產鎖死」——最高優先級，**先找出 14:31 起鎖住 SQLite 的罪魁禍首**。
> **硬邊界（Owner 逐字）**：「定位根因並提出最小修復補丁，**禁止盲目重啟服務掩蓋現場**。」
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**。

---

## 0. 現象與影響（主大腦實測，數字可覆核）

- **症狀**：`[Goal] 主循环扫描异常 (fail-closed): OperationalError: database is locked`
- **頻率**：**每 30 秒 2 筆**（`scheduler.py` 主迴圈每次迭代都失敗 ⇒ **Goal 主循環 100% 停擺**，非間歇）
- **區間**：**2026-10-23 14:32:02.554 首現** → 19:19 仍持續，**連續 573 筆**（依分鐘計數：14:31 起 ×1，其後每分鐘 ×2）
- **對照**：同一份 `data/server_nohup.err`（涵蓋 10/22 20:41 → 至今）中，**10/22 20:41 → 14:31 為 0 筆** ⇒ 狀況**今天 14:31 起才出現**。
- **影響範圍（精確）**：**只有 Goal 主循環**。其餘排程路徑同期正常：16:48 `💬 proactive_dm 觸發: agent_ruka`、16:50 `✨ 下次事件: 2026-10-24 00:03`、TTS／SAGE flush／calendar poll 皆有。fail-closed ⇒ **不崩潰**，但 Goal 掃描這段期間**等同停擺**。
- **14:31 現場**：14:31:52 最後一次成功寫入是 `MemoryWriter mirror 成功 | agent=assistant`（agent_yua）＋ `[MemoryMiddleware] 寫入 graph | agent=agent_yua`；**第一個鎖錯在 14:32:02.554**（間隔 10 秒）。

---

## 1. 主大腦已排除的假設（**別重走這幾條**）

| 假設 | 排除依據 |
|---|---|
| 我的殘留 pytest 行程持鎖 | 行程全清單：只有 server 樹（26980/19440）、3 個 voice companion 樹、hindsight、我的 shell。**無任何 pytest／殘留行程** |
| 某個 `data/**` 檔案**持續**被寫鎖 | 寫鎖探針（`BEGIN IMMEDIATE` timeout=0.3 s、成功後立即 `ROLLBACK`、不寫資料）掃過 **`data/memory.db` ＋ 46 個 `data/memory/agent_*/graph.sqlite` ＝ 全部可寫**（19:3x）。⇒ **此刻沒有任何檔被寫鎖** |
| `import src.inner_life.epistemic_action` 的 import 期副作用 | 子行程匯入前後 `data/inner_life/epistemic_tensions.jsonl` 的 size/sha256/mtime **完全不變** |
| CLI 沙盒執行寫了生產 | 跑一次 `scripts/eval_lifecycle_cli.py --simulated-time …` 前後，張力承載 size/sha256/行數**完全不變** |
| 測試套件寫生產張力承載 | 全庫測試**無任何檔案**引用 `record_tension_opened`／`mark_tension_resolved`／`save_tension_records`／`tension_store`；且 `data/inner_life/epistemic_tensions.jsonl` 在 16:45:07 那筆的 `tension_id = relational_tension:6bfe56006414294a`（agent_rem）＝**生產 goal loop 自己的關係張力**（其後 10 秒 goal loop 即開始報鎖）⇒ 寫入者是生產端 |
| 「database is locked」是我的全庫回歸造成 | 警告**橫跨兩次回歸之間的空檔期**仍在（14:31→19:19 連續）⇒ **不是回歸造成的**。⚠️ 主大腦一度以 16:43–16:52 窗口懷疑是回歸所致，**實測分佈後推翻**（窗口挑選式歸因的陷阱） |

**⇒ 剩下的可能（由你判定與定位）**：
1. **鎖是「間歇／短持有」**——持有者每次只握有數秒～數十秒，恰在 Goal 掃描每次嘗試時都在 ⇒ 連續 5 小時每次都撞上（⇒ 需找出「誰在什麼時刻握有鎖」）。
2. **鎖在 `data/**` 之外**（主大腦的探針只覆蓋 `data/` 底下的 sqlite；`data/agent_emotions.db`／`data/emotions.db` 是 0 B，`_backup_stage0_20260718_160049/`、`data/time_lapse/**` 有大量舊 graph.sqlite，`scripts/data/**` 也有 ⇒ **請自行盤點全機所有可能被寫入的 sqlite 檔**）。
3. **錯誤並非來自檔案層的寫鎖**（例如：共用 singleton 連線處於 `in_transaction=True` 的異常狀態、或 `busy_timeout` 逾時來自另一個機制的逾時）。
4. **WAL checkpoint 卡死**（見 §2 觀察 3）。

---

## 2. 已交付給你的勘查成果（不必重做，但可覆核）

### 2.1 失敗路徑（code map）
```
scheduler.py:1733  _goal_scan_all()            ← 掛在主迴圈 30 s wake 上，try/except ⇒ 只 log warning（fail-closed）
  └─ GoalMotiveProvider.for_agent(agent_id).apply_interrupt_signals()   (scheduler.py:452)
  └─ GoalMotiveProvider.for_agent(agent_id).scheduled_wakeup_scan()     (scheduler.py:453)
  └─ GoalSeedProvider.for_agent(agent_id).scan_seeds()                  (scheduler.py:1754)
  └─ settle_relations(agent_id)                                         (scheduler.py:1760)
```
- `src/goals/motive_provider.py`：`_goal_db_path()` ＝ `data_root()/memory/<agent>/graph.sqlite`；`self._store` ＝ **lazy 開的 `GraphStore`**（`src/memory/sage/graph_store.py`）；sidecar ＝ `data_root()/memory/<agent>/goal_provider.json`。
- `src/goals/seed_provider.py`：同樣 lazy `GraphStore(_goal_db_path(agent_id))`。
- `src/social/relation_settlement.py`：`data_root()` 之下（**`src/social/*` 屬 `frozen_dirs` ⇒ 不得修改**）。

### 2.2 SQLite 連線設定地圖（全域）
| 檔 | 連線 | timeout / busy_timeout | 其他 |
|---|---|---|---|
| `src/memory/sage/graph_store.py:80-93` | `sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)` | **10.0 s** | `journal_mode=WAL`、`synchronous=NORMAL`、`cache_size=-8000`、`temp_store=MEMORY`；`_locked` RLock 串行化（KI-008 疤） |
| `src/memory/store.py:29` | `sqlite3.connect(db_path, check_same_thread=False)` | **未設 ⇒ Python 預設 5.0 s** | `data_root()/memory.db` |
| `src/agent/emotion.py:79,364-371` | `sqlite3.connect(db_path, check_same_thread=False)` | `PRAGMA busy_timeout = 5000` | `data_root()/memory.db`；**模組層 singleton 連線** ＋ `_WRITE_LOCK` ＋ `BEGIN IMMEDIATE`；已有大量「database is locked」疤（明寫「實測 2.09× 逾時 = 10.43 s」「7 角色序列化最壞 ~73 s」），且**明文規定「遇鎖必須上報，不得吞」** |

### 2.3 觀察 3：WAL 未 checkpoint（ Bry 排查重點 #1 的現場證據）
`data/memory/agent_*/graph.sqlite-wal` 大小：`agent_yua` **5.9 MB**（mtime 19:18:09）、`agent_akane`/`agent_anna`/`agent_mahiru`/`agent_rem`/`agent_ruka` **4.0 MB**、`agent_aoi`/`agent_mai`/`agent_miku`/`agent_ram` **3.9 MB**，其餘 0.2 MB。⇒ **WAL 長期未 checkpoint**（多數 agent 的 WAL 自 03:34 起未再 checkpoint）。

---

## 3. 排查計畫（依序執行；每一步都要有實測輸出）

### 步驟 A：確定「被鎖的檔案」與「持有者」
1. **盤點全機**可能被寫入的 sqlite 檔（不只 `data/`）：用 `Get-ChildItem -Recurse` 掃 repo、`%TEMP%`、`%APPDATA%`、voice companion 的資料目錄、`C:\hindsight`；對每個檔案記 size/mtime/`-wal`/`-shm` 大小。
2. **寫鎖探針（唯讀、不寫資料）**：對每個候選檔 `sqlite3.connect(path, timeout=0.3, isolation_level=None)` → `PRAGMA busy_timeout=300` → `BEGIN IMMEDIATE` → 成功則**立刻 `ROLLBACK`**、失敗則記錄「🔴 寫鎖」與完整例外。**必須連續取樣 ≥ 10 次、間隔 3～5 秒**（主大腦只取樣 1 次就得到「全部可寫」⇒ **間歇持鎖是目前的最佳假設**）⇒ 用連續取樣把「持有者存在的那個時間窗」抓出來。
   - 🔴 **開鎖探針的紅線**：`BEGIN IMMEDIATE` 若成功**必須立刻 ROLLBACK**；**不得**在任何候選檔上做 INSERT/UPDATE/DELETE；**不得**對生產 DB 做 `VACUUM`／`wal_checkpoint`／`PRAGMA journal_mode` 變更。
3. **持有者定位**：把步驟 2 抓到的時間窗與 `data/server_nohup.err` 對齊（哪一支 agent 回合／SAGE flush／voice 事件落在同一秒）⇒ 推出持有鎖的**程式路徑**（`GraphStore.write_turn`？`emotion._write_tx`？`memory/store.py`？）。
4. **只在必要時**用 OS 級工具列「持有該檔 handle 的行程」（例如 `handle.exe` 若本機可用；**不可用就改用時間窗對齊＋程式碼走查**）。

### 步驟 B：確認「誰在 14:31 動手」
- 比對 14:25–14:35 的 `data/server_nohup.err`（已交付片段：14:31:52 最後一次 `agent_yua` graph 寫入；14:31:47／14:32:02 `SAGE flush: 1/90 個 store 已提交 pending`）。
- 對 14:31 前後的 **`data/memory/agent_yua/graph.sqlite`／`-wal`／`-shm`** 做 **位元快照**（複製到 `%TEMP%` 後才開啟，**不可在原地開啟**）⇒ 看 WAL 內是否有未 checkpoint 的殘留交易。
- 檢查 **3 個 voice companion 行程**（PID 26108/12792、19500/7716、19008/16612）在 14:31 前後有沒有對 `data/**` 的寫入痕跡（它們的 `--config clients/voice_companion/profiles/*.json` 指向的資料目錄要一併盤點）。

### 步驟 C：最小修復補丁（**只在沙盒驗證，不得部署**）
- 目標：讓「長事務／未關閉連線／WAL 不 checkpoint」三者中**真正的根因**被修掉，且**不得**改變既有語意。
- 修補方向（依根因擇一或組合，**不得盲改**）：
  - 若為**例外逸出導致交易未 rollback** ⇒ 在對應寫入路徑補 `finally: rollback()`（`GraphStore` 的交易包裝／`emotion._write_tx`）。
  - 若為**缺 `busy_timeout`** ⇒ 為 `src/memory/store.py:29` 顯式補 `PRAGMA busy_timeout`（對齊 `emotion.py` 的既有做法：把等待預算寫成契約，不倚賴直譯器預設值）。⚠️ **不得**動 `graph_store.py` 的 `timeout=10.0`（已被多處文件引用為契約值）。
  - 若為**WAL 不 checkpoint** ⇒ 補 checkpoint 策略（**不得**改 `journal_mode`／`synchronous` 的既有值）。
- 🔴 **不得**改動：`frozen_dirs` 11 筆（尤其 `src/social/*`）、`src/memory/sage/graph_store.py` 的 `timeout=10.0` 與 WAL/synchronous/cache_size 設定、任何衰減門檻。
- 🔴 **不得**改 `src/agent/emotion.py` 的 `BUSY_TIMEOUT_MS=5000`（既有契約）與 `_WRITE_LOCK` 語意。
- 補丁必須**附守衛測試**（新斷言必須能抓住回歸）**＋變異實驗 M1～Mn（每條必須轉紅）**。

### 步驟 D：解除當前卡住狀態的**方案**（**只出方案，不執行**）
- 若根因是「某行程內的交易卡死」，**單靠程式碼修補無法解除現況**（需重啟該行程）⇒ 你必須寫出**精確的重啟計畫**：會丟失什麼（暫時未 flush 的 pending）／不會丟失什麼（已落盘的 WAL／JSONL）／為什麼這不是「盲重啟」（有根因、有補丁、有備份、有回滾步驟）⇒ **實際重啟需 Owner 明示授權**（AGENTS.md：重啟須 Owner 授權，且只能走 `scripts\server_ops.ps1`）。

---

## 4. 邊界（硬性）

1. 🔴 **不得重啟任何服務**（含 voice companion）；**不得**用 `Stop-Process`／`taskkill`／`pkill`／`wmic process delete` 殺任何行程；**不得**對執行過 `server_ops.ps1` 的背景 shell／task 呼叫 `task_stop`。
2. 🔴 **不得**寫入／修改 `data/**` 與任何生產 DB（含 WAL／SHM 檔）；讀取生產 sqlite 前**必須先複製 `.db` ＋ `-wal` ＋ `-shm` 到 `%TEMP%`** 再開啟。
3. 🔴 **不得**改 `src/**`（本票為**只讀勘查**：根因定位 ＋ 補丁提案 ＋ 沙盒驗證 ⇒ 補丁可提交為**分支／工作樹**上的改動，但**不得**合入 `main`、**不得**部署、**不得**重啟）。
4. 🔴 **不得**跑全庫回歸（會與生產搶資源）；本票的驗證只跑**新增的守衛測試檔**（`.venv\Scripts\python.exe -m pytest -q <該檔>`）。
5. 🔴 **今晚 22:00 EDT 夜槽不得被擾動**：21:45 EDT 起停止任何長時間操作；槽後（23:00 後）才可做耗時實驗。
6. 變異 harness **必須放 `%TEMP%`**（`tests/**` 禁 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`），且採「**改副本**、repo 檔全程不寫入」模式（2026-10-23 主大腦實測：in-place 還原會以 `OSError: [Errno 22]` 失敗，把 repo 檔留在變異狀態）。
7. 不得 `git add`／`git commit`／`git push`（由主大腦處理）；工作樹若因勘查而改動，必須在回報中列明並保持可還原。
8. **測試數字一律用 repo 的 `.venv\Scripts\python.exe`**。

---

## 5. 交付物（缺一不可）

1. **根因報告**：`logs/INCIDENT-GOAL-DB-LOCKED.md`
   - 症狀與區間（實測數字）、影響範圍（精確到路徑）、**根因鏈**（哪個行程／哪段程式／哪個交易）；
   - 「被鎖的檔案」與「持有者」的**實測證據**（連續取樣輸出、持有者行程的 PID＋命令列）；
   - 14:31 觸發點的對齊分析；
   - 明確寫出**已排除的假設**與排除依據（避免下一个人重走）。
2. **最小修復補丁**（分支／工作樹上，**不合入 main**）：diff ＋ 每處改動的理由 ＋ 對既有語意的影響評估。
3. **守衛測試**（新檔或既有檔擴充）＋ **變異 M1～Mn 全紅**（repo 檔 sha256 逐位元相同）。
4. **解除卡住狀態的重啟計畫**（只出方案，不執行；需 Owner 授權）。
5. **回報**：改動檔案清單、實測數據（每個寫分子／分母／單位）、測試與變異結果、未解決的疑點、需要 Owner 裁示的項目。

---

## 6. 回報格式

1. **根因一句話**（含檔案＋行程＋函式層級）。
2. **實測證據表**：被鎖檔案／持有者 PID／時間窗／取樣次數與命中率。
3. **14:31 觸發點分析**。
4. **補丁 diff 摘要** ＋ 守衛測試筆數 ＋ 變異 M1～Mn 逐條轉紅結果。
5. **重啟計畫**（不執行）。
6. **未解決疑點 / 需 Owner 裁示**。
7. **未做與明文排除**（含「未重啟、未殺行程、未寫 data、未合 main、未部署」的自證）。

---

## 7. 給執行者的三句提醒（2026-10-23 的坑）

1. **不要只取樣一次就下結論**：主大腦取樣 1 次得到「46 個 DB 全部可寫」⇒ 差點誤判「沒有鎖」。**間歇持鎖**要靠連續取樣抓。
2. **不要用單一時間窗口歸因**：主大腦差點把 16:43–16:52 的警告歸因成「我的回歸造成」，**實測分佈後推翻**（警告橫跨回歸空檔期）⇒ **先看全區間分佈，再談因果**。
3. **守衛測試的合成 fixture 必須與生產同形**；新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺。
