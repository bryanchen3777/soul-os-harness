# 夜槽後「重大窗口」評估清單（待 Owner 裁定）

> 產出：主大腦 2026-10-24 17:0x EDT
> 對象：2026-10-24 22:00 夜槽**觀測完成之後**的那一次重啟窗口
> 窗口內容（依 Owner 2026-10-24 裁定「WAL 治理啟用與旗標啟用，統一併入夜槽驗收後的後續重大窗口」）：
> ① WAL 治理（TRUNCATE checkpoint）　② `SOUL_OS_TENSION_DRIVE_LOOP` 旗標啟用
> ③ 行程內寫鎖競爭修正（`graph_store.py` 仲裁器）

---

## 一、動手前的必要前提（全部滿足才可開窗）

| # | 前提 | 證據位置 | 目前狀態 |
|---|---|---|---|
| P1 | 22:00 夜槽已完成觀測，觀測票（`807cce2d`，23:13 EDT）已產出報告 | cron 排程 | ⏳ 待夜槽 |
| P2 | 夜槽期間**零意外紅燈**（ERROR/CRITICAL ＝ 0、寫鎖衝突未復發） | `data/server_nohup.err` | ⏳ |
| P3 | 三個合併到 `main` 的改動各自閘門全綠 | 各票 commit | ✅ Track B／寫鎖修正已合入（見下） |
| P4 | 重啟走**官方** `.\scripts\server_ops.ps1 restart`，且取得 Owner 明示授權 | — | ⏳ 待授權 |
| P5 | **不得**對執行過 `server_ops.ps1` 的背景 shell／task 呼叫 `task_stop`；不殺任何行程 | AGENTS.md 行程安全鐵律 11 | ✅ 紀律遵守 |
| P6 | 重啟使用**獨立中繼終端**、不帶 `-RedirectStandard*` | 過往事故教訓 | ⏳ |

## 二、三項改動各自的風險與「上線前/上線後」檢查

### ① WAL 治理（`checkpoint_all_live()`，TRUNCATE）

- **上線前**：確認 `busy_timeout=10000` 不變、**不**用 `@_locked`（會 rollback 未落盤寫入）、
  掛既有 30s wake（0 新計時器）、fail-silent、有界 8。
- **🔴 上線前必須回答的問題（Owner 已點名）**：
  **「WAL checkpoint 本身是寫者，會不會成為新的寫鎖競爭方？」**
  - 答：**會**。`wal_checkpoint(TRUNCATE)` 需要取得該 DB 的獨佔寫鎖。
  - 但既有防禦已就位：busy=1 時**不重試、fail-silent**，**不會卡死行程**；
    且它是**週期性**的短作業，不是「長時未提交寫交易」—— 與 10:36 事故的形狀不同
    （後者：持鎖者要等 15s flush 週期才 commit，而等方預算只有 10s）。
  - ⇒ 真正的對策是 **③ 寫鎖修正**（讓持鎖者提早 commit），不是為 WAL 治理再加防禦。
- **上線後量測（分母要寫清楚）**：
  - live `data/memory/*/graph.sqlite-wal` 的**位元組總和**（基準母體＝47 檔 live store，
    **不含**事故備份與歷史備份）—— 目標：下降且**不再增長**。
  - checkpoint 觸發時的 `busy=1` 次數 / 總觸發次數。
  - 寫鎖衝突次數（`database is locked`）—— 目標：**0**。

### ② `SOUL_OS_TENSION_DRIVE_LOOP` 旗標啟用（P5 資料面）

- **上線前**：`format_working_state_lines_production()` / `drive_loop_async_production()`
  的路徑不可注入契約仍在；`check_store_dir()` 的生產防注入 sha256 未變。
- **上線後量測**：`data/working_state.jsonl` 出現且**只寫它**；
  `working_state.jsonl` 的 gap 由 66 → 65；`proxy` 的讀取面逐位元讀回。
- **風險**：旗標開啟後，LTM 張力迴路會**每夜實際呼叫 LLM** ⇒ **這是花錢的事**
  ⇒ 必須取得 Owner 對**每晚預算**的明示授權（不是一次性授權）。
- **同時**：佇列是「每日 1 agent ＋ at-most-once」⇒ 觀察窗內**只會有一個 agent**。

### ③ 行程內寫鎖競爭修正（`graph_store.py` 仲裁器）

- **上線前**：候選甲（共用連線／單一實例）**已否決** —— 會讓既有
  `tests/test_graph_store_c2_read_open.py` 的「快照隔離」契約轉紅；
  採用 **乙＋丙**：保留各自的連線（隔離語意不變），
  但**同路徑寫入前先請持鎖者立刻 commit**，並且**有界等待 2.0s ＋ fail-closed**。
- **🔴 殘餘風險（必須寫進回報）**：
  仲裁器是在**持鎖者的 RLock 保護下** commit（`check_same_thread=False` ＋
  所有寫入方法皆 `@_locked`）⇒ 跨執行緒安全；但被仲裁的寫入者會被**提前 commit**
  ⇒ SAGE 的批次寫入最佳化在「有競爭的路徑上」會退化成逐筆 commit（**效能代價**，
  不是正確性問題）。
- **上線後量測**：`database is locked` 次數（目標 0）、每夜的 `GraphStoreWriteBusy`
  次數（fail-closed ⇒ 會讓該筆寫入失敗而不是卡住；**必須被監看**，若持續出現
  ⇒ 仲裁策略需要再調整，而不是放寬）。

## 三、建議的窗口順序（主大腦建議，待 Owner 拍板）

1. 先確認 22:00 夜槽觀測票回報**全綠**。
2. 取得 Owner 明示授權（重啟）。
3. 獨立中繼終端執行 `.\scripts\server_ops.ps1 restart`。
4. 重啟後**立即**複查三件事（這是 AGENTS.md 鐵律 11 要求的收尾複核）：
   `server_ops.ps1 status`、`netstat -ano | findstr :8000`、`data/server.pid` 三者一致。
5. 重啟後觀察 30 分鐘：ERROR/CRITICAL ＝ 0、`database is locked` ＝ 0、
   `working_state.jsonl` 有寫入、`graph.sqlite-wal` 位元組總和下降。

## 四、需要 Owner 拍板的點

| # | 事項 | 為什麼需要 Owner |
|---|---|---|
| D1 | 是否授權夜槽後重啟 | 重啟生產行程，必須明示授權 |
| D2 | **每晚 LLM 預算上限**（旗標啟用後 LTM 迴路每夜會真的呼叫） | **花錢的事** |
| D3 | 戰役二（Belief 長效保活／superseded 死鎖鏈路）的正式開打時點 | 已在「夜槽觀測完成後」，待觀測後確認 |
| D4 | 寫鎖修正是否併入同一窗口（而非獨立窗口） | 影響面與風險集中度 |
