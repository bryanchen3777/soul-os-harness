# INCIDENT-GOAL-DB-LOCKED — 根因報告

> **立案**：主大腦工單 `logs/INCIDENT-GOAL-DB-LOCKED-WORK-ORDER.md`（2026-10-23 19:4x EDT）
> **依據裁定**：Owner (Bryan) 2026-10-23 19:2x「立即立案排查生產鎖死」
> **本報告作者**：Worker（只讀勘查＋補丁提案）
> **勘查時段**：2026-10-23 19:31 ～ 21:45 EDT
> **分支**：補丁位於 **detached worktree** `%TEMP%\goal_patch_wt`（HEAD = `cc0b78c`），
> **未合入 main、未部署、未重啟**。

---

## 0. 一句話根因（兩層，必須分開看）

**層 1（已證明、可重現、補丁已修）**：
`scheduler._goal_scan_all()` 把 `try/except` 放在 **整個 46-agent 迴圈之外**
（`src/soul/scheduler.py:1744-1764`）⇒ **任何一個 agent 撞一次 `database is locked`，
就會中止其餘 45 個 agent 的掃描** ⇒ 單點故障被放大成 **Goal 主循環 100% 停擺**。
這就是「5 小時 600 筆告警、每一筆都讓整輪掃描歸零」的結構性放大器。

**層 2（觸發源，**未能證明**，見 §4 的誠實邊界）**：
**某一個** agent 的 `graph.sqlite` 存取在 2026-10-23 14:31:32 之後 **每 30 秒都**
拋 `sqlite3.OperationalError: database is locked`。證據顯示這個鎖 **不在檔案層**
（外部探針 12 連續取樣 × 50 個候選檔 = 0 命中），且 **不是資料本身壞掉**
（把生產資料整份複製到沙盒重播 = 0 失敗）⇒ 鎖在 **服務行程內部**（某條長命連線的
狀態），但 **在不動生產行程的前提下無法直接觀測**。

**⇒ 層 1 是本票必須修的缺陷（已修 + 有守衛 + 變異全紅）；層 2 需要一次
Owner 授權的重啟來清除行程內狀態（§7 重啟計畫），並且已由補丁加上「自癒 +
可診斷」保險絲。**

---

## 1. 症狀與區間（全部為本報告的實測數字）

| 項目 | 實測值 | 來源 |
|---|---|---|
| 症狀字串 | `[Goal] 主循环扫描异常 (fail-closed): OperationalError: database is locked` | `data/server_nohup.err` |
| 筆數 | **600 / 600 筆**（全檔） | `Select-String 'database is locked'` |
| 產生 logger | **1 種**：`soul_os.soul.scheduler`（600/600） | 同上，群組分析 |
| 首現時刻 | **2026-10-23 14:31:32.383** | 同上（**不是**工單寫的 14:32:02.554） |
| 末筆時刻 | **2026-10-23 19:43:43.495**（持續到勘查結束仍在長） | 同上 |
| 分鐘桶覆蓋 | **302 / 302 個分鐘桶，每桶 ≥1 筆**（14:31→19:43，零缺口） | 同上 |
| 穩態頻率 | **每分鐘 2 筆**（= 30 s 主迴圈每次迭代 1 筆） | 同上 |
| 對照區間 | 10/22 20:41（伺服器啟動）～ 14:31 = **0 筆** | 同上 |
| 其他路徑 | 同期 proactive_dm / 下次事件 / TTS / SAGE flush **全部正常** | 同上 |

> ⚠️ **工單 §0 的首現時刻需要更正**：實際首現是 **14:31:32.383**，
> 比工單記載的 14:32:02.554 **早 30.2 秒**。這個差異重要 —— 它把觸發點
> 拉回到「agent_yua 最後一次 graph 寫入（14:31:19.089）之後 13.3 秒」，
> 而不是 43 秒後。

---

## 2. 影響範圍（精確到路徑）

- **只有 Goal 主循環**：`scheduler._goal_scan_all()` 的 4 條腿
  （`apply_interrupt_signals` / `scheduled_wakeup_scan` / `scan_seeds` /
  `settle_relations`）**全部沒有執行到第 2 個 agent**。
  證據：整段期間 **0 筆** `[Seed] 生成 goal` / `[Goal] 喚醒` / `[RelSettle]` 日誌
  （全檔僅 40 筆，最後一筆在 **11:46:34**，早於首個鎖錯 2h45m）。
- **Goal 引擎的 4 個功能全停**：中斷信號（Bryan 缺席掛起／not_transmit streak／
  ABANDON 逾期）、SUSPENDED→ACTIVE 喚醒、9 源種子生成、關係演化沉澱。
- **不崩潰**：fail-closed（只 log warning）⇒ 主循環存活，其他排程路徑正常。
- **未受影響**：記憶寫入、LLM 推論、TTS、SAGE flush、calendar poll、TG/WS 閘道。

---

## 3. 根因鏈（程式碼層級，每一環都指得出檔案與行號）

```
scheduler.py:1747   for agent_id in self._all_agents:          # 46 個 agent
scheduler.py:1749     provider.apply_interrupt_signals()        # leg 1
scheduler.py:1750     provider.scheduled_wakeup_scan()         # leg 2
scheduler.py:1754     await GoalSeedProvider...scan_seeds()    # leg 3
scheduler.py:1760     settle_relations(agent_id)               # leg 4
scheduler.py:1761   except Exception as e:                     # ← 層 1 放大器：包在迴圈外
scheduler.py:1763     logger.warning(f"[Goal] 主循环扫描异常 (fail-closed): ...")
                       ↑ 只有 type + message：沒有 agent_id、沒有 traceback
```

**放大器（層 1）的三個後果，全部已證明：**

1. **單點 → 全局**：第 1 個撞鎖的 agent 中止整輪 ⇒ 其餘 45 個 agent 永遠掃不到。
2. **100% 而非間歇**：只要那 1 個 agent 每次都撞鎖，整輪就 100% 失敗 ——
   這正好解釋「每分鐘 2 筆、302 個分鐘桶零缺口」，也解釋「其他路徑全正常」。
3. **5 小時查不出地點**：告警字串沒有 `agent_id`、沒有 `exc_info` ⇒
   600 筆告警無法定位到任何 agent、任何行號。**這是本次事故最貴的一項
   可診斷性缺陷**，補丁一併修掉（見 §6 P1）。

**唯一會撞鎖的 agent 是誰 —— 仍未知（§4）。**

### 3.1 為什麼「只有 Goal 主循環」會鎖（Goal 路徑的 sqlite 形狀）

`src/goals/motive_provider.py:189` 與 `src/goals/seed_provider.py:190` 各自
`GraphStore(db_path=_goal_db_path(agent_id))`，**兩個 provider 各開一條獨立連線，
但指向同一個檔案** ⇒ **每個 agent 的 `graph.sqlite` 上有 ≥2 條 Goal 專屬長命連線**
（motive + seed），再加上 SAGE writer 的連線 ⇒ 46 個檔案 × ≥3 條連線。

而 Goal 路徑的 sqlite 面被沙盒插樁量測（`sqlite3.connect` + `set_trace_callback`）
確認為：

| 檔案 | 目標路徑的陳述 |
|---|---|
| `data/memory/<agent>/graph.sqlite` | `PRAGMA journal_mode=WAL`（**每條連線只跑一次**）+ `SELECT * FROM facts` + `SELECT * FROM goals` |
| `data/memory.db` | **0 次**（Goal 路徑完全不碰） |
| `data/agent_emotions.db` / `emotions.db` | **0 次** |

**⇒ 關鍵事實：Goal 路徑在穩態下對 agent 的 graph.sqlite **不做任何寫入**
（沒有 INSERT/UPDATE），只有讀取。所以它「撞鎖」的位置，必然是
**讀取陳述本身**或**連線開啟**（`PRAGMA journal_mode=WAL`）——
**而不是寫入排隊。**

### 3.2 已量到的環境形狀（可能相關，但未證明因果）

- `data/memory.db`：**`journal_mode = delete`**（非 WAL），無 `-wal`/`-shm`/`-journal`。
  `src/agent/emotion.py:37` 的 `DB_PATH = data_root()/"memory.db"`，
  模組層級 singleton + `BEGIN IMMEDIATE` + `busy_timeout=5000`
  ⇒ **rollback-journal 模式下，寫者會擋住整個檔案**。
- `src/memory/store.py:29`：`sqlite3.connect(...)` **沒有 `timeout=`**
  ⇒ 倚賴 Python 預設 5.0 s（隱含值，未寫成契約）。
- **WAL 長期未 checkpoint**：`agent_yua` 6,147,072 B（5.86 MB），
  多數 agent 4,124,152 B，mtime 停在 **2026-10-23 03:34:57** ⇒
  wal-index 被長命讀取快照釘住，checkpoint 無法完成。
  **這是「某條連線持有過期讀取快照（SQLITE_BUSY_SNAPSHOT）」的間接徵候，
  但本次未能證實。**
- `data/memory/sage/graph.sqlite`：**`schema_meta.version = 4`**、**無 `goals` 表**
  ⇒ 對它的 GraphStore 開啟會跑完整 migration（寫入）。
  但 `sage` 的檔案 mtime 停在 **2026-07-18** ⇒ **生產上它從未被 Goal 路徑開啟過**
  ⇒ `sage` 不是本事故的觸發者（此假設已排除）。

---

## 4. 被鎖檔案與持有者：實測證據（**誠實邊界**）

### 4.1 探針 A：寫鎖探針（工單 §3 步驟 A.2 規定格式）

```
[probe] candidates: 50  samples=12 interval=4.0s
[probe] sample 1/12 2026-10-23 19:34:49 locked=0
...
[probe] sample 12/12 2026-10-23 19:35:34 locked=0
[probe] hit summary: {"n_samples": 12, "n_samples_with_any_lock": 0,
  "per_path_lock_hits": {<50 個候選檔全部為 0>}}
```

- 候選檔 50 個：`data/memory.db` + 46 個 `data/memory/agent_*/graph.sqlite`
  + `data/memory/sage/graph.sqlite` + `data/agent_emotions.db` + `data/emotions.db`
- 探針法：`sqlite3.connect(path, timeout=0.3, isolation_level=None)`
  → `PRAGMA busy_timeout=300` → `BEGIN IMMEDIATE` → 成功**立即 ROLLBACK**；
  失敗記錄例外。**全程 0 次 INSERT/UPDATE/DELETE、0 次 VACUUM / wal_checkpoint /
  journal_mode 變更。**
- 樣本數 12（≥10 ✓），間隔 4.0 s（3～5 s ✓），跨 19:34:49 ～ 19:35:34（45 s）。
- **結果：12/12 樣本、50/50 檔案、0 次命中。**

### 4.2 探針 B：重演 Goal 路徑「完全相同的開檔序列」

```
[live] targets: 47  samples=12 interval=4.0s
[live] sample 1/12 19:43:48 failures=1
    RED \data\memory\sage\graph.sqlite -> OperationalError: no such table: goals
...
[live] sample 12/12 19:44:32 failures=1
[live] SUMMARY: 12 samples, 12 samples with a failure
[live] per-path failure counts: {"\\data\\memory\\sage\\graph.sqlite": 12}
```

- 序列 = `sqlite3.connect(path, check_same_thread=False, timeout=10.0)`
  → `PRAGMA journal_mode=WAL` → `PRAGMA synchronous=NORMAL` → `PRAGMA cache_size=-8000`
  → `PRAGMA temp_store=MEMORY` → `SELECT * FROM facts WHERE weight > 0.01`
  → `SELECT * FROM goals WHERE agent_id = ?`（**與 `GraphStore` 的開檔序列逐字相同**）
- **46 個 `agent_*` 資料庫：12/12 樣本、0 次 "database is locked"。**
- 唯一失敗的是 `sage`，而其錯誤是 **`no such table: goals`**（**另一個錯誤**，
  與本事故無關；且 §3.2 已證 `sage` 未被 Goal 路徑開啟過）。

### 4.3 沙盒重播（生產資料整份複製，單一行程）

- 複製 `data/memory/**`（59.1 MB，含每個 `.sqlite` + `-wal` + `-shm`）到
  `%TEMP%\goal_sbx1`，`SOUL_OS_DATA_DIR` 指向沙盒。
- 重播 `_goal_scan_all()` 的 4 條腿 × **50 個目錄**：
  **0 失敗**（`[sandbox] failures: 0`）。
- **⇒ 資料本身不是觸發源；觸發源是服務行程的執行期狀態。**

### 4.4 結論（誠實陳述）

| 命題 | 狀態 |
|---|---|
| 鎖不在檔案層（外部可見的寫鎖） | **已證明**（探針 A：12/12 樣本、50/50 檔案、0 命中） |
| 鎖不是資料壞掉 / 版本不符造成 | **已證明**（探針 B 46/46 全綠；沙盒 50/50 全綠） |
| 鎖是「其他行程持有寫鎖」 | **已排除**（若成立，探針 A 必命中） |
| 鎖是「同一行程內某條長命連線的狀態」 | **高度可能，但未證明**（需要在行程內觀測） |
| 鎖是 SQLITE_BUSY_SNAPSHOT（過期讀取快照） | **可能**，間接徵候 = WAL 長期不 checkpoint；未證明 |

> **依工單「若卡住」條款**：寫鎖探針 10 次連續取樣一次都抓不到 ⇒
> **回報「鎖不在檔案層」這個事實 + 完整取樣輸出（§4.1）**，
> **不硬編根因**。本報告上半部（層 1）是**程式碼層級證明 + 可重現**的，
> 下半部（層 2）**明確標記為未證明**，兩者不混用。

---

## 5. 14:31 觸發點對齊

| 時刻 | 事件 | 來源 |
|---|---|---|
| 14:31:19.089 | `[MemoryMiddleware] 寫入 graph \| agent=agent_yua`（**最後一次成功的 graph 寫入**） | log |
| 14:31:19.106 | `[INTIMACY-GROWTH] agent=agent_yua` | log |
| 14:31:32.383 | **第一個 `database is locked`**（相隔 **13.294 秒**） | log |
| 14:31:32.572 | `SAGE flush: 1/90 個 store 已提交 pending`（**flush 正常 ⇒ 非 flush 卡死**） | log |
| 14:31:47 / 14:32:02 | `SAGE flush` 持續正常 | log |

**對齊分析：**
1. 觸發點緊接在 **agent_yua 的 graph 寫入**之後 13.3 秒；該次寫入把
   `agent_yua` 的 WAL 推到 6.1 MB。**在 WAL 模式下，一個 commit 可能觸發
   PASSIVE checkpoint；若 checkpoint 把 WAL 重置（reset），任何持有較舊讀取
   快照的連線會拿到 `SQLITE_BUSY_SNAPSHOT`（Python 訊息同為 "database is locked"，
   且 **不重試、不佔用檔案寫鎖 ⇒ 外部探針抓不到**）。**
   **這條因果鏈符合全部四項觀察，但本次沒有行程內觀測，無法證實。**
2. `SAGE flush` 在首錯前後都正常 ⇒ **不是 flush 迴圈卡死**。
3. 14:31 之前 10/22 20:41 起 0 筆 ⇒ **不是長期存在的老問題**；
   也因此排除「與本機全庫回歸有關」（主大腦已推翻的窗口歸因）。

---

## 6. 最小修復補丁（detached worktree，**未合入 main**）

**位置**：`%TEMP%\goal_patch_wt`（`git worktree add --detach … HEAD`，HEAD = `cc0b78c`）
**Diff**：3 檔 +128/−12，另新增 1 個守衛測試檔（12,205 bytes）
（完整 diff：`%TEMP%\goal_db_locked_patch.diff`）

### P1 — `src/soul/scheduler.py::_goal_scan_all`（**層 1 放大器**）
- **改動**：`try/except` 由「迴圈外」下沉到「**每個 agent 內**」；
  告警加入 `agent={agent_id}` 與 `exc_info=True`。
- **理由**：一個 agent 的鎖不得吃掉其餘 45 個 agent 的掃描；且事故證明
  「無 agent_id / 無 traceback」的告警等於沒有告警。
- **語意影響**：fail-closed 契約 **不變**（仍只 log warning、仍不阻斷主循環）；
  唯一變化是**故障隔離粒度**由「整輪」細化到「單 agent」——
  這是**可用性增加**，且與既有 `settle_relations` / `scan_seeds` 內部
  fail-closed 的設計同構。0 新定時器、0 新通道、0 觸碰 frozen_dirs。

### P2 — `src/memory/sage/graph_store.py`（層 2 的自癒保險絲）
- **P2a `_locked` 裝飾器**：捕捉 `sqlite3.OperationalError` 且訊息含
  `locked`/`busy` ⇒ **重建連線**（rollback + close + 重新 open）⇒ **重試一次**。
  - **語意影響**：本類所有寫入語句皆為 `INSERT OR REPLACE` / `UPDATE`（冪等），
    且鎖錯誤發生在寫入「尚未生效」時 ⇒ 重試不重複套用副作用。
    非鎖錯誤照舊上拋（守衛 T4b 釘死「不得吞例外」）。
    `flush` / `close` **排除**在自癒之外（失敗必須上拋，否則資料遺失會變靜默成功）。
  - **代價**：重建連線會遺失舊連線上未 commit 的批次（≤19 筆 facts），
    並以 WARNING 記錄 `dropped_pending`。
- **P2b `_get_conn`**：顯式 `PRAGMA busy_timeout=10000`。
  **不改 `timeout=10.0`**（值相同，只是把隱含值寫成契約）。
- **P2c `flush()`**：commit 失敗 ⇒ **明確 rollback**（釋放寫鎖）⇒
  `_pending_writes` 歸零 ⇒ 重拋。
  - **理由**：`flush_all_live()` 的 `except Exception: continue`（graph_store.py:926-928）
    會**靜默吞掉** flush 失敗 ⇒ 若 commit 失敗時不 rollback，這條連線會
    **永久持有寫鎖**且現場無任何告警。**這是「永久鎖」最直接的機制性缺口。**
  - **語意影響**：flush 失敗時資料由「留在 pending 等下輪重試」變成
    「回滾並記 WARNING」—— 這是刻意的取捨（寧可丟失未落盤批次，
    也不讓壞連線永久持鎖），且丟失量被明確記錄。

### P3 — `src/memory/store.py:29`（工單 §3 C 明確點名的方向）
- **改動**：`sqlite3.connect(...)` 後顯式 `PRAGMA busy_timeout=5000`。
- **理由**：工單指定「把等待預算寫成契約，不倚賴直譯器預設值」，
  對齊 `src/agent/emotion.py:370` 的既有做法。
- **語意影響**：**等值**（Python 預設就是 5000 ms），僅顯式化。
  ⚠️ `data/memory.db` 是 `delete` journal 模式（非 WAL），本補丁**不改變**
  其 journal mode（工單禁止）。

### 未動的契約（明文）
`graph_store.py` 的 `timeout=10.0`、`journal_mode=WAL`、`synchronous=NORMAL`、
`cache_size=-8000`、`temp_store=MEMORY`、`_BATCH_SIZE=20`、
`emotion.py` 的 `BUSY_TIMEOUT_MS=5000` 與 `_WRITE_LOCK` 語意、
`frozen_dirs` 全部（尤其 `src/social/*`）、任何衰減門檻 —— **0 變更**。

---

## 7. 解除當前卡住狀態的重啟計畫（**只出方案，不執行**）

> 🔴 **實際重啟須 Owner 明示授權**，且只能走 `.\scripts\server_ops.ps1 restart`。
> 🔴 **22:00 EDT 夜槽不得被擾動** ⇒ 建議 **23:00 之後**執行。
> 🔴 本報告作者 **未執行**任何重啟、未殺任何行程。

### 為什麼需要重啟（而不是只靠程式碼）
層 2 的鎖在**服務行程內的連線狀態**裡（外部探針 12/12 樣本抓不到任何檔案寫鎖）。
**行程外無法清除它；唯一能清除行程內連線狀態的手段就是重啟該行程。**
重啟後：所有長命連線（含可能帶著過期讀取快照的那條）全部重建 ⇒
層 2 的狀態被清除；層 1 的放大器則由補 patch 永久消除。

### 會丟失什麼
- **SAGE 未 flush 的 pending facts**：每個 store 最多 `_BATCH_SIZE-1 = 19` 筆
  （`add_fact` 批次緩衝，graph_store.py:405-408）。正常情況下 15 s 的
  `SAGE flush` 迴圈會把它們落盤 ⇒ **正常重啟時幾乎為 0**；只有當 flush 本身
  卡住時才會有實際損失。
- **Goal sidecar 的「未落盤狀態」**：無。`goal_provider.json` 是一般檔案
  （atomic write + replace），已落盤的不會丟。
- **進行中的 LLM 推論 / TTS**：會被中止（既有 `server_ops.ps1 restart` 語意）。

### 不會丟失什麼
- **已落盤的 WAL 內容**：`graph.sqlite` 的已 commit 資料在 `-wal` 內，
  重啟時 SQLite 會自動 recovery ⇒ **不丟**。
- **`data/**/*.jsonl`（記憶、perception、diary、tension…）**：不丟。
- **`data/memory.db`、`relationships.json`、sidecar**：不丟。

### 為什麼這不是「盲重啟」
1. **有根因**：層 1（放大器）已定位到行號並有可重現的守衛測試；
   層 2 已用三組獨立探針收斂到「行程內連線狀態」並明確標記為未證明。
2. **有補丁**：工作樹內 4 處改動 + 7 條守衛測試 + 6 條變異全紅。
3. **有備份**：重啟前先把 `data/memory/*.sqlite` + `-wal` + `-shm`
   與 `data/memory.db` 複製到 `data/_incident_backup_<UTC 時間戳>/`
   （**這是唯一能保住未 checkpoint WAL 的手段**，也讓重啟可逆）。
4. **有回滾步驟**：補丁在 **detached worktree**、main **未動** ⇒
   回滾 = `git worktree remove %TEMP%\goal_patch_wt`（0 影響 main）。
   若需撤銷已合併的補丁：`git revert <commit>`。

### 執行步驟（待 Owner 授權）
1. **備份**（唯讀生產 → 寫入新目錄，不覆寫既有檔案）：
   `data/memory/*/graph.sqlite{,-wal,-shm}` + `data/memory.db` → `data/_incident_backup_<ts>/`
2. **記錄現場**（重啟前的 golden trace）：
   `Select-String 'database is locked' data/server_nohup.err | Measure-Object`（筆數）
   + `data/memory/*/graph.sqlite-wal` 的大小清單。
3. **授權後重啟**：`.\scripts\server_ops.ps1 restart`（**只用這條路徑**）。
4. **重啟後立即複核**（工單鐵律 11 的三一致）：
   `server_ops.ps1 status` + `netstat -ano | findstr :8000` + `data/server.pid`。
5. **驗證層 1 已修**（若補丁已合併部署）：重啟後 10 分鐘內
   `data/server_nohup.err` 內 `[Goal] 主循环扫描异常` 應為 **0 筆**。
6. **驗證層 2 是否復發**：觀察 30 分鐘；若 30 分鐘內再度出現
   `[Goal] 主循环扫描异常 ... database is locked`，
   **則層 2 未被重啟清除**（推測為檔案層問題）⇒ 立即回報並啟動
   「逐 agent 二分定位」（補丁已讓告警帶 `agent_id` + traceback，
   下一輪事故可在 **1 分鐘內**定位到具體 agent 與行號）。

---

## 8. 守衛測試與變異結果

**守衛測試檔**：`tests/infra/test_goal_db_locked_guard.py`（工作樹內，7 條）

| 編號 | 守衛 | 對應變異 |
|---|---|---|
| T1 | per-agent 故障隔離（撞鎖不吃掉其他 agent） | M1 |
| T2 | 告警帶 `agent_id` 與 traceback | M2 |
| T3 | `flush()` commit 失敗 ⇒ rollback + pending 歸零 | M3 |
| T4 | 鎖錯誤 ⇒ 重建連線後重試一次 | M4 |
| T4b | 非鎖錯誤 **不得** 被吞 | （反向守衛） |
| T5 | `GraphStore` 顯式 `PRAGMA busy_timeout=10000`（AST 層） | M6 |
| T5b | `MemoryStore` 顯式 `PRAGMA busy_timeout=5000`（AST 層） | M5 |

**變異 harness**：`%TEMP%\goal_mutation_harness.py`（工單 §4.6：
放 %TEMP%、**改副本**、repo 檔全程不寫入）
**執行結果**：

```
[baseline] T1 rc=0  T4 rc=0
[M1] src/soul/scheduler.py :: test_T1_goal_scan_isolates_a_locked_agent -> rc=1 RED_OK
[M2] src/soul/scheduler.py :: test_T2_goal_scan_warning_carries_agent_and_traceback -> rc=1 RED_OK
[M3] src/memory/sage/graph_store.py :: test_T3_flush_rolls_back_when_commit_fails -> rc=1 RED_OK
[M4] src/memory/sage/graph_store.py :: test_T4_lock_error_rebuilds_connection_and_retries -> rc=1 RED_OK
[M5] src/memory/store.py :: test_T5b_memory_store_sets_explicit_busy_timeout -> rc=1 RED_OK
[M6] src/memory/sage/graph_store.py :: test_T5_graph_store_sets_explicit_busy_timeout -> rc=1 RED_OK
[sha256] repo/worktree 受保護檔案前後逐位元相同: True
[mutations] all_red=True
```

> **M5/M6 的教訓**：第一版守衛用「查 `PRAGMA busy_timeout` 的**值**」來斷言，
> 結果 **M5/M6 NOT RED** —— 因為 `sqlite3.connect(timeout=X)` **本來就會**把
> busy_timeout 設成 `X*1000`，拿掉顯式 PRAGMA 值不變。
> **⇒ 守衛改成 AST 層斷言「原始碼確實發出了那條 PRAGMA」**，
> 這才真正釘住「等待預算被寫成契約」這個契約本身。
> （對應工單 §7.3 的提醒：合成 fixture 必須與生產同形。）

---

## 9. 明確排除的假設（含依據）

| 假設 | 排除依據 |
|---|---|
| 殘留 pytest / 殘留行程持鎖 | 行程全清單：server 樹 26980→19440、3 組 voice companion、hindsight、我的 shell。**0 個 pytest**；且持鎖行程必須是 **python**，全清單已涵蓋 |
| 持續性檔案寫鎖 | 探針 A：12/12 樣本 × 50 檔 = **0 命中**（19:34:49–19:35:34） |
| 資料本身損壞 / schema 版本不符 | 探針 B：46/46 agent DB × 12/12 樣本 = 0 鎖；沙盒 50/50 = 0 失敗；46 個 agent DB 的 `schema_meta.version` **全部 = 9** |
| `sage` 庫（version 4）造成 | `sage/graph.sqlite` mtime 停在 2026-07-18 ⇒ **生產上從未被 Goal 路徑開啟過**；其失敗訊息也是 `no such table: goals`（**非** "database is locked"） |
| `data/memory.db` 被 Goal 路徑寫入 | 插樁量測：Goal 路徑對 `data/memory.db` 的 connect 與陳述數 = **0** |
| 是我的全庫回歸造成 | 告警橫跨回歸空檔期連續存在（14:31→19:43，302 個分鐘桶零缺口） |
| CLI 沙盒寫生產 | 本次未執行 CLI 沙盒；且 `data/**` 全部 sha256/mtime 未被本次勘查改動（見 §10 自證） |

---

## 10. 自證（未做什麼）

- **未重啟**任何服務（含 voice companion）。
- **未殺**任何行程（無 `Stop-Process` / `taskkill` / `pkill` / `wmic process delete`，
  對任何背景 task **未呼叫** `task_stop`）。
- **未寫入 / 改名 / 刪除** `data/**` 任何檔案（含 `-wal` / `-shm` / `-journal`）。
  唯讀讀取生產 sqlite 一律 **先複製 `.db` + `-wal` + `-shm` 到 %TEMP%** 再開啟
  （§4.3 的沙盒、schema 檢查皆如此）；唯二直接開啟生產檔的是工單 §3 步驟 A.2
  明文設計的兩個探針（A：只 `BEGIN IMMEDIATE`+立即 `ROLLBACK`；
  B：只 `PRAGMA`+`SELECT`），兩者皆 0 次寫入、0 次 DDL、0 次 checkpoint。
- **未改 `src/**` 在 main 上**；補丁只在 detached worktree。
- **未 `git add` / `git commit` / `git push`**。
- **未跑全庫回歸**；只跑了新增的守衛測試檔與變異 harness 的單檔節點。
- **未部署、未重啟**。

---

## 11. 需要 Owner 裁示 / 未解決疑點

1. **🔴 重啟授權**：解除層 2 需要一次 `server_ops.ps1 restart`（§7）。
   建議 **23:00 EDT 之後**（避開 22:00 夜槽）。是否授權、何時授權，請裁示。
2. **補丁是否合入 main**：本票**未合入**；是否採納 P1（per-agent 隔離）＋
   P2（自癒）＋ P3（顯式 busy_timeout），請裁示。
   ⚠️ P2a 的自癒會在重建連線時丟失該 store 未 commit 的批次（≤19 筆），
   **這是刻意的取捨**（避免壞連線永久持鎖），若 Owner 認為應改為
   「只重建不丟資料」（例如把 pending 落到旁路檔），需另行裁示。
3. **層 2 仍未證明**：若重啟後 30 分鐘內復發 ⇒ 層 2 是檔案層問題，
   需啟動逐 agent 二分定位（補丁已讓告警帶 `agent_id` + traceback，
   定位成本已從「5 小時無解」降到「1 分鐘」）。
4. **WAL 未 checkpoint（agent_yua 5.86 MB / 多數 4.1 MB）**：
   這是獨立於本事故的長期健康問題（wal-index 被長命讀取快照釘住）。
   本票**未提補丁**（工單未授權改 `journal_mode` / `synchronous`）。
   是否另開一張票處理 checkpoint 策略，請裁示。
5. **`data/memory.db` 仍是 `delete` journal 模式**：
   rollback-journal 模式下寫者會擋住整個檔案，且
   `emotion.py`（singleton ＋ `BEGIN IMMEDIATE`）與 `store.py` 兩條寫入路徑
   共用它。本票只在 `store.py` 補了顯式 busy_timeout，**未改 journal mode**
   （工單禁止）。是否另案處理，請裁示。
