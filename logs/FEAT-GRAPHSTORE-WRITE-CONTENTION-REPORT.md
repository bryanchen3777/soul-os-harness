# FEAT-GRAPHSTORE-WRITE-CONTENTION — 行程內寫鎖競爭：機制報告 ＋ 離線修正

> 工單：`logs/FEAT-GRAPHSTORE-WRITE-CONTENTION-WORK-ORDER.md`（2026-10-24 15:30 EDT 開立）
> 施工：worktree `%TEMP%\wlock_wt`（detached HEAD `b8c6f12`）
> 狀態：**機制已釐清 ＋ 離線修正已實作**；**生產尚未套用**（重啟才生效，且須 Owner 授權）。

---

## 0. 摘要（TL;DR）

1. **機制確認**：同一個 `data/memory/<agent>/graph.sqlite` 在**同一行程內**被**多個 `GraphStore` 實例**開啟（生產：48 個 live 實例 / 50 個 agent DB 路徑）。未滿批的寫交易**持續持有該 DB 的寫鎖** ⇒ 另一條連線寫同一個 DB 會一路等 `busy_timeout`(10s) 耗盡 ⇒ `database is locked`。
2. **10:36 的答案是「誰持鎖、持多久」**：寫鎖被**連續持有 ≥20.7 秒**（兩個完整的 busy_timeout 視窗：第一次失敗 10.0s，自癒重試再失敗 10.7s）。持鎖者是 **SAGE provider store（MemoryWriter 的寫入路徑）**；等鎖者是 **goal 掃描 store（`motive_provider`）**。兩者**同一個 DB 路徑**。
3. **關鍵時序根因**：SAGE flush 的週期是 **15s**（`run_server.py:2072`），**大於** `busy_timeout` **10s** ⇒ 等方在持鎖者 commit **之前**就超時 ⇒ 「鎖撐過 10 秒才浮現」。
4. **M-3 沙盒重現成功**：兩個實例同 DB ⇒ `database is locked`，**持鎖時長 10.601s**、逃逸總耗時 **21.217s**，與生產日誌（10.0s / 20.7s）**相差 ≤0.5s** ⇒ 機制假設成立。
5. **修正**：**候選乙＋丙**（寫入前先請「同一路徑上其它實例」立刻 commit ＋ 有界且 fail-closed 的仲裁）。修正後同一情境 **0.000155s** 完成、**0 次**鎖錯誤、**0 次**自癒。
6. 🔺 **候選甲（單一實例／單一連線）被既有 C2 守衛否決**（快照隔離契約）—— 詳見 §9。這是工單內部的張力，已標示。

---

## 1. 量測方法與紀律

| 紀律 | 實際作法 |
|---|---|
| **生產觀測唯讀** | **未**對生產 sqlite 開任何新連線（連 `mode=ro` 都沒有）。M-1／M-2 全部來自**既有埋點**（`GraphStore.flush_all_live()` 的 INFO 摘要行）與**既有服務日誌**（`data/server_nohup.err`）。 |
| 生產檔案 | 只讀（`FileShare.ReadWrite` 開啟 live log，因服務端持鎖）。**未寫、未改名、未刪除** `data/**` 任何檔案。 |
| 沙盒隔離 | M-3 與全部變異實驗都在 `%TEMP%` 的**自建沙盒**進行（`tempfile.mkdtemp` / 複製樹）。 |
| 0 網路 / 0 LLM / 0 真實行程操作 | 全票 0 次外部呼叫、0 次真實 LLM、**未**殺任何行程、**未**重啟服務。 |
| 解譯器口徑 | 一律 `"C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe"`（repo 的 `.venv`，Python 3.11.15）。 |
| 失敗數口徑 | 以 pytest **returncode 為主閘門**；失敗數只從耗時摘要行取；找不到 `failed` token 一律當 0；並要求 `passed > 0`。 |
| 變異隔離 | 變異 harness 放 `%TEMP%\p5_wlock_scripts\mutations\`；**改副本模式**（worktree 檔全程不寫入，收尾 sha256 相同）。 |

---

## 2. M-1 — 生產量測：同一 DB 路徑的實例數 / 連線數（唯讀）

### 2-1. 實例總數（來源：既有埋點 `flush_all_live()` 的摘要行）

`GraphStore.flush_all_live()` 每輪會印 `[Server] SAGE flush: <n_flushed>/<n_live> 個 store 已提交 pending`，其中 `n_live` ＝ **行程內 live `GraphStore` 實例數**（`GraphStore._live_stores` 這個 `WeakSet` 的大小）。⇒ **這是既有埋點，零新增、零行為改變。**

| 量 | 分子 | 分母 | 單位 | 來源 |
|---|---|---|---|---|
| `n_live`（live 實例數） | **48** | **90** | 個實例／每輪 flush | `data/server_nohup.err`，2026-10-23 23:03 → 2026-10-24 15:14 的 90 筆 flush 行 |
| `n_live` 的分佈 | 48（**100%** 的樣本） | 90 | — | 同上（min＝median＝max＝48） |
| `n_flushed`（該輪有 pending 的實例數） | 1（60 筆）／2（30 筆） | 90 | 個實例／輪 | 同上 |
| 磁碟上的 agent DB 路徑數 | **50** | — | 個路徑 | `data/memory/` 的 agent 目錄數 |

### 2-2. 哪些元件共用同一個 DB 路徑（靜態對照，讀碼）

`_goal_db_path(agent_id)` ＝ `data_root/memory/<agent_id>/graph.sqlite`（`src/goals/motive_provider.py:113-115`）；而 `SAGELiteProvider._db_path()` ＝ `self.data_dir / "graph.sqlite"`，`data_dir` ＝ `data_root()/memory/<agent_id>`（`src/memory/middleware.py:106-110, 196-201`）⇒ **兩條路徑字面相同**。

| 元件 | 建立點 | DB 路徑 | 生命週期 |
|---|---|---|---|
| `SAGELiteProvider` | `src/memory/sage/provider.py:478` | `data/memory/<agent>/graph.sqlite` | 長期（每 agent 惰性） |
| `GoalMotiveProvider` | `src/goals/motive_provider.py:189` | **同一條** | 長期（惰性） |
| `GoalSeedProvider` | `src/goals/seed_provider.py:190` | **同一條** | 長期（惰性） |
| `PeriodicNarrative` | `src/goals/narrative_sublimator.py:240` | **同一條** | 短期（每次呼叫 create+close） |
| decision | `src/soul/decision.py:478`（frozen） | **同一條** | 短期 |
| life_thread_orchestrator | `src/soul/life_thread_orchestrator.py:526` | **同一條** | 短期 |

⇒ **同一條 DB 路徑最多有 6 個建立點（3 長期 ＋ 3 短期）。**

### 2-3. 決定性證據：同一路徑上確實同時活著 ≥2 個實例

用既有日誌的兩組標記做交集（皆為既有埋點，非本票新增）：

| 集合 | 判定依據（既有日誌行） | 分子 | 分母 |
|---|---|---|---|
| 有 `motive_provider` store 的 agent | `[Goal] 唤醒: goal=<id> agent=<agent>` | **34** | 50 條 agent 路徑 |
| 有 SAGE provider store 的 agent | `[MemoryMiddleware] enrich \| agent=<agent>` | **2** | 50 |
| ↑ 的交叉驗證 | 另兩種既有標記（`v1 Loader 注入 \| agent=`、以及任何 `MemoryMiddleware] ... agent=` 行） | **2 / 1 / 2**（三種標記） | 50 |
| **兩集合交集**（⇒ 同一路徑上 ≥2 個長期實例） | — | **2**（`agent_ruka`、`agent_yua`） | 34 |
| 10:36 撞鎖的 agent | `database is locked` 的 `db=` 路徑 | **2**（`agent_yua`、`agent_ruka`） | — |

⇒ **撞鎖的 2 個 agent ＝ 同一路徑上有 ≥2 個實例的 2 個 agent（2/2 完全吻合）。**
⇒ 且 10:30～10:40 這段期間內**有 SAGE 寫入的 agent 也恰好只有這 2 個**（`agent_yua`、`agent_ruka`）。

### 2-4. M-1 結論

| 量 | 分子 | 分母 | 單位 |
|---|---|---|---|
| 同一 DB 路徑上的**長期**實例數（會對話的 agent） | **2** | 2 | 個實例／路徑 |
| 同一 DB 路徑上的**長期**實例數（只有 goal 掃描的 agent） | 1 | 32 | 個實例／路徑 |
| 同一 DB 路徑上的**短期**實例數（每次呼叫即開即關） | ≤3 | — | 個實例／路徑 |
| 行程內 live 實例總數 | 48 | 90 輪 | 個 |
| 磁碟上的 agent DB 路徑總數 | 50 | — | 條 |

🔴 **誠實註記**：`n_live`＝48 是**下限**（`WeakSet` 只登記活著的實例；「短期」實例在 close 前不在其中，且部分元件的 store 是在日誌窗口之外才建立）。

---

## 3. M-2 — 生產量測：寫交易持有時間與持鎖者

### 3-1. 誰持鎖（waiter 側，來自 traceback）

服務日誌 `data/server_nohup.err` 的 traceback 逐字指出呼叫鏈：

```
src/soul/scheduler.py:1762  _goal_scan_all
  → src/goals/motive_provider.py:658  scheduled_wakeup_scan
    → src/memory/sage/graph_store.py:830  transition_goal
      → sqlite3.OperationalError: database is locked
```

⇒ **等方（waiter）＝ `motive_provider` 的 store**，寫入點是 `transition_goal`（`graph_store.py:830`，該行是 `SELECT state FROM goals ...` 之後的 `fetchone()`；後續的 `UPDATE goals ...` 才是真正要寫鎖的那一步）。

### 3-2. 持鎖者（holder）

⇒ **`SAGELiteProvider` 的 store**（MemoryWriter 的寫入路徑 `writer.py:397 → store.add_fact`）。證據鏈：

1. `add_fact` 是「未滿批不 commit」路徑（`graph_store.py:492-495`）⇒ **寫交易開啟 ⇒ 該連線持續持有該 DB 的寫鎖**。
2. 10:35:47.379 / 10:35:48.602 / 10:35:50.195 / 10:36:03.716 的 `[MemoryWriter] v1 store mirror 成功` 都落在 `agent_yua` 的鎖窗口內 ⇒ 該路徑的 SAGE store **正在寫**。
3. 等方（`motive_provider` store）與 SAGE store **同一條 DB 路徑**（§2-2）。
4. 工單 §0 的既有結論：**無外部 sqlite 程序**（`-shm` mtime 未變）⇒ 持鎖者**必在行程內**。

### 3-3. 持了多久（關鍵數字）

`transition_goal` 的第一次嘗試在 **10:36:00.934** 失敗（自癒日誌），⇒ 該次嘗試約於 **10:35:50.9** 開始 ⇒ **被阻塞 ≈10.0s**（＝ 一個完整 busy_timeout 視窗）。自癒（重建連線＋重試一次）**又**阻塞到 **10:36:11.661** 才拋出 ⇒ **寫鎖被連續持有 ≈20.7 秒**。

| agent | 第一次失敗（耗時 ≈ busy_timeout） | 最終拋出 | **寫鎖連續持有** |
|---|---|---|---|
| `agent_yua` | 10:36:00.934（≈10.0s） | 10:36:11.661 | **≈20.7s** |
| `agent_ruka` | 10:36:22.270（≈10.0s） | 10:36:32.931 | **≈20.6s** |

（自癒日誌的 `dropped_pending=0` ⇒ 等方自己的批次是空的 ⇒ 它的鎖**不是**自己造成的 ⇒ 持鎖者是**別的實例**。）

### 3-4. 為什麼「撐過 10 秒」：flush 週期 > busy_timeout

| 量 | 值 | 單位 | 來源 |
|---|---|---|---|
| `busy_timeout` | **10000** | ms | `graph_store.py:252`（明文 PRAGMA） |
| SAGE flush 週期 | **15.0** | s | `scripts/run_server.py:2072`（`_SAGE_FLUSH_INTERVAL_SECS = 15.0`） |
| 實際 flush tick 間隔（忙碌時段） | ≈17 | s | 由相鄰 flush 行的時間戳反推（10:35:42.094 → 10:36:33.199 ＝ 51.1s ≈ 3 tick） |

⇒ **持鎖者要等到下一次 flush 才 commit（15～17s），而等方在 10s 就超時** ⇒ 等方**比鎖被釋放早 5～7 秒**就爆掉 ⇒ 這就是「鎖撐過 10 秒才浮現」的成因。
⇒ 而鎖一釋放，等方已經進入第二次（自癒重試）嘗試 ⇒ 又是一個 10s 視窗 ⇒ **總計 20.7s**。

### 3-5. `_pending_writes` 的量測

| 量 | 分子 | 分母 | 單位 | 來源 |
|---|---|---|---|---|
| 撞鎖當下持鎖者的 pending | **3** | 20（batch_size） | 筆 | 沙盒 M-3 實測（`pending_a_before=3`） |
| 每輪 flush 有 pending 的實例數 | 1～2 | 48（live） | 個／輪 | 生產日誌 90 筆 |

⇒ 持鎖時**距滿批還很遠**（3/20 ＝ 15%）⇒ commit 只會由 flush 週期（15s）觸發 ⇒ **這就是「未滿批 ⇒ 長期持有寫鎖」的量化證據**。

---

## 4. M-3 — 沙盒重現（**本票的關鍵關卡：已通過**）

harness：`%TEMP%\p5_wlock_scripts\m3_repro.py`（0 生產資料、0 網路、0 LLM）。

### 4-1. 修正前（worktree 原始 `graph_store.py`）

```
=== M-3 raw sqlite baseline ===
  outcome        : locked
  err            : database is locked
  t_block (s)    : 10.619
=== M-3 GraphStore-level ===
  outcome           : locked
  err               : database is locked
  t_block           : 10.60065320006106     <- 持鎖時長
  t_total           : 21.217062500072643    <- 逃逸總耗時（含自癒重試）
  pending_a_before  : 3
  pending_a_after   : 3
  selfheal_events   : 1
  same_conn         : False                 <- 兩個實例 = 兩條連線
RESULT: reproduced=True
```

**raw sqlite baseline 的 4 次獨立量測**：10.681 / 10.548 / 10.619 / 10.633 s（min 10.548、max 10.681、mean ≈10.62）⇒ **持鎖時長 ≈ busy_timeout 的量級** ✔（工單 §2.3 要求的特徵）。

### 4-2. 與生產日誌的對帳（**這是機制成立的決定性證據**）

| 量 | 沙盒（M-3） | 生產（10:36 日誌） | 差 |
|---|---|---|---|
| 第一次失敗前的阻塞 | **10.601 s** | ≈10.0 s | ≈0.6s |
| 逃逸總耗時（自癒重試完） | **21.217 s** | ≈20.7 s | ≈0.5s |
| 持鎖者 pending | 3 筆（未滿批） | — | — |
| 兩個實例是否同一條連線 | **否**（`same_conn=False`） | 否（兩條連線） | 一致 |

⇒ **沙盒逐位元重現了生產的時間特徵 ⇒ 機制假設成立 ⇒ 得以進入 M-4。**

### 4-3. 修正後（同一 harness，`--expect-fixed`）

```
=== M-3 raw sqlite baseline ===
  outcome        : locked      <- 機制本身仍然可重現（守衛有牙）
  t_block (s)    : 10.633
=== M-3 GraphStore-level ===
  outcome           : no_error
  t_total           : 0.00015540001913905144   <- 21.217s -> 0.000155s
  pending_a_after  : 0                          <- 仲裁確實請持鎖者 commit 了
  selfheal_events   : 0                         <- 0 次鎖錯誤自癒
  same_conn         : False                     <- 連線仍然分離（C2 快照隔離保留）
RESULT: fixed=True
```

---

## 5. M-4 — 修正選型：為什麼選它、為什麼不選另外兩個

### 5-1. 量測支持什麼

- **M-1**：同一 DB 路徑上**確實同時活著 ≥2 個實例**（2/2 撞鎖 agent 完全吻合）⇒ 根因是「同一 DB 檔多條連線 ＋ 未滿批交易不 commit」。
- **M-2**：寫鎖被連續持有 **≈20.7s**，而等方的預算只有 **10s**；持鎖者要等到 **15s** 的 flush 週期才 commit ⇒ **等方必定輸**。
- **M-3**：沙盒可重現、且修正後可驗證。

⇒ **真正的修正軸**必須做到兩件事：**(a) 讓等方不必用完 10s 的 busy 預算；(b) 任何等待都有界且 fail-closed。**

### 5-2. 採用：**候選乙 ＋ 候選丙**

> **機制**：行程內「同一 DB 路徑」有一個仲裁器（`_PathArbiter`，以正規化後的 DB 路徑為 key）。
> **任何寫入前**先向仲裁器詢問「同一路徑上**其它實例**裡誰持有開啟中的寫交易（`_pending_writes > 0`）」；
> **若有 ⇒ 請它立刻 `commit`**（有界等待 `_PATH_ARBITER_WAIT_SECS = 2.0s`）⇒ 寫鎖被釋放 ⇒
> **本次寫入不必撞 sqlite 的 busy_timeout**。取不到（持鎖者忙／commit 失敗）⇒ **fail-closed**：丟出
> `GraphStoreWriteBusy`（`Exception` 子類 ⇒ 排程器 per-agent `except Exception` 仍能隔離）⇒ **不排隊**。

**為什麼這是對症的**：它直接消掉「等方輸在 10s < 15s」這個不對稱 —— **不是**去等更久，而是**讓持鎖者提前放鎖**。

| 邊界 | 落實 |
|---|---|
| 🔴 不提高 `busy_timeout` | `PRAGMA busy_timeout=10000` 一字未動（W6 守衛 + 變異 P5） |
| 🔴 無界排隊 | 仲裁等待 **2.0s** 上界；sqlite 端 busy_timeout 維持 10s；**兩者疊加上界 12s < 30s tick** ⇒ tick 不疊加 |
| 🔴 fail-closed | 取不到就丟 `GraphStoreWriteBusy`（可處理例外）⇒ 排程器 fail-closed |
| 🔴 不改 schema 契約 | `_SCHEMA_VERSION` 未動、無新 DDL |
| 🔴 不改 `flush()` rollback 語意 | `flush()` 一字未改（GOAL-DB-LOCKED-1 保留）；仲裁的 commit 失敗路徑**額外**做了 rollback ＋ pending 歸零（與 `flush()` 同語意） |
| 🔴 不動 `src/memory/sage/writer.py` | 一行未動（frozen） |
| 🔴 0 新定時器／背景 task／額外連線 | 只加了一個行程內的仲裁器（dict ＋ `RLock`）；**0 條**新連線、**0** 個新執行緒（W8 守衛 + 變異 P6） |

**關於「更頻繁 commit」的顧慮（工單對候選乙的紅線）**：本修正**只在發生競爭時**才請持鎖者提前 commit；**無競爭時批次行為完全不變**（仍是滿 20 筆才 commit、或 15s flush）⇒ **批次效能與可見性語意在無競爭路徑上逐位元不變**。

### 5-3. 被否決的選項與理由

| 選項 | 否決理由 |
|---|---|
| ❌ **提高 `busy_timeout`**（Owner 選項分支之一） | 現值**已是 10000ms**。在 **asyncio 行程**裡 sqlite 的 busy 等待是**同步阻塞**事件迴圈 ⇒ 調大＝讓事件迴圈停更久 ⇒ 可能把「一個 agent 寫失敗」升級為「整個行程停頓」。而且它**治標不治本**：持鎖者仍要 15s 才放鎖，調到 30s 只是把「等方輸」變成「等方等更久」，並讓 30s tick 可能疊加 ⇒ **比撞鎖更糟**。**維持 10000 的明文契約不動。** |
| ❌ **無界的外部 per-agent mutex**（Owner 選項分支之二） | 互斥只序列化**呼叫者**，**無法讓持有者加速** —— 而真正的問題是連線層的交易生命週期。且排程器主迴圈是 **30s tick** ⇒ 若一個寫入排隊等 10s，**tick 會疊加** ⇒ 把「撞鎖後快速失敗（fail-closed，資料安全）」變成「慢慢排隊（可能拖垮 tick）」⇒ **比撞鎖更糟**。另：互斥必須 keyed by **DB 路徑**（`@_locked` 是 per-instance，同一 DB 的兩個實例互不知情）。 |
| ❌ **候選甲（同一 DB 路徑單一實例／單一連線）** | 🔺 **被既有守衛否決**：`tests/test_graph_store_c2_read_open.py:74` 明確斷言「同一路徑上的第二個實例**看不到** writer 尚未 commit 的列（快照隔離）」，且該檔檔頭自述「**C2 不讓 Reader 看見 Writer 尚未 commit 的列（快照隔離）— 那不是 bug**」。共用連線 ⇒ reader 就是 writer 的同一條連線 ⇒ 快照隔離消失 ⇒ **既有守衛轉紅**。而工單 §4 要求「既有守衛不得被削弱」、§5 明確列出 `tests/test_graph_store_c2_read_open.py` 為必跑項目。⇒ **甲與 C2 契約互斥**，見 §9。 |

---

## 6. 守衛（`tests/infra/test_graph_store_write_contention_guard.py`，恰 8 筆）

| # | 內容 | 斷言重點 |
|---|---|---|
| **W1** | **沙盒重現**（把仲裁關掉 ＝ 修正前的舊機制） | 必須觀察到 `database is locked`；且**第一次失敗的延遲 ≥ 0.5×busy_timeout** ⇒ 重現「鎖撐過 10 秒」；否則**守衛本身失效 ⇒ 紅** |
| **W2** | **同實例不變** | 在 networkx 雙寫點插探針：兩執行緒的寫入**不得重疊**（max concurrent ＝ 1）⇒ `@_locked` 仍序列化 |
| **W3** | **修正有效** | 同一情境下寫入**不得**出現 `database is locked`，且耗時 **< 2.0s**（遠小於 busy_timeout） |
| **W4** | **有界等待 ＋ fail-closed** | `_PATH_ARBITER_WAIT_SECS < busy_timeout`；持鎖者的鎖被佔住時，競爭者必須在**有界**時間內**放棄**並丟出 fail-closed 例外（不是等 10s、不是 `database is locked`、不是排隊） |
| **W5** | **共享生命週期** | 一個元件 `close()` 後，**同一路徑上其它實例的連線仍可用**（可寫可讀） |
| **W6** | **既有契約未動** | `PRAGMA busy_timeout` ＝ 10000；原始碼保留顯式 PRAGMA；`flush()` 的 commit 失敗 ⇒ rollback ＋ pending 歸零；`_SCHEMA_VERSION` ＝ 9 |
| **W7** | **fail-silent** | 真實 `SoulScheduler._goal_scan_all` 遇 `GraphStoreWriteBusy` **不得**拋出（主循環不得中斷）且必須留下 fail-closed 告警 |
| **W8** | **0 新副作用** | 同一 DB 路徑開 2 個實例 ＋ 做**一次無競爭**的寫入 ⇒ **恰好 2 條** sqlite 連線（不得更多）；執行緒數不變（0 新 task／計時器）。🔺 刻意用**無競爭**情境：若這裡有競爭，既有「鎖錯誤自癒」會重建連線（GOAL-DB-LOCKED-1）⇒ 多算一條 ⇒ 會被 P1 連帶打紅（弱變異）。 |

🔺 **W1 與 W3 為什麼是同一情境的兩面**（工單 §3 的 W1／W3 在同一層斷言會互斥）：
讓 B 寫成功**正是修正的目的** ⇒ 若 W1 也用「開著修正的 GraphStore 層」斷言「必須觀察到 locked」，W1 必然轉紅。
因此 **W1 把仲裁關掉**（＝修正前的舊機制）來證明「機制本身仍可重現、守衛有牙」，**W3 開著仲裁**證明「修正有效」。兩者合起來才是完整證據鏈。

---

## 7. 變異實驗（P1～P7）

harness：`%TEMP%\p5_wlock_scripts\mutations\run_mutations.py`（**改副本模式**：worktree 檔全程不寫入，收尾 sha256 與初始值相同）。
報告：`%TEMP%\p5_wlock_scripts\mutations\mutation_report.{json,md}`。

| # | 變異 | 應轉紅 | 實測 |
|---|---|---|---|
| **P1** | 移除寫入路徑的仲裁（回到兩條連線同時寫） | W3 | **RED-OK** |
| **P2** | 把仲裁的等待改成無界（`acquire()` 不帶 timeout） | W4 | **RED-OK** |
| **P3** | 失敗時的例外改成 `BaseException` ⇒ `except Exception` 接不住（冒泡） | W7 | **RED-OK** |
| **P4** | `close()` 無條件把同路徑其它實例的連線一起關掉 | W5 | **RED-OK** |
| **P5** | 把 `busy_timeout` 調高（10000 → 30000） | W6 | **RED-OK** |
| **P6** | 修正每輪開新連線 | W8 | **RED-OK** |
| **P7** | 移除 `@_locked`（同一實例內不再序列化） | W2 | **RED-OK** |

**每一條都恰好打紅預期的那一個守衛**（其餘 7 筆仍綠）⇒ 無弱變異。

---

## 8. 全庫回歸

解譯器＝**repo 的 `.venv`**（Python 3.11.15）；兩輪皆為 `pytest -q tests`；cwd＝各自的 tree。
基線樹 ＝ **worktree 的完整複製**（含 `data/`、`personas/`、`clients/`、`configs/`、`.git`），
但 `src/memory/sage/graph_store.py` 以 `git show HEAD:…` **逐位元還原**、且**不含**本票新增的守衛檔
⇒ 基線＝**當下 HEAD `b8c6f12`**。

| 樹 | failed | passed | skipped | errors | 耗時 | returncode |
|---|---|---|---|---|---|---|
| **改動前**（乾淨 HEAD `b8c6f12` 完整複製） | 48 | **7,284** | 80 | 11 | 770.5s（0:12:48） | 1 |
| **改動後**（本票最終樹） | 48 | **7,292** | 80 | 11 | **779.3s（0:12:57）** | 1 |

⇒ **passed ＋8 ＝ 本票新增的 8 筆守衛**（工單 §4.5 的「基線 ＋ 8 筆」✅）；
**failed／errors／skipped 的數量逐項相同**；**失敗／錯誤名稱逐筆比對：新增 0、消失 0**
⇒ **既有守衛未被削弱**（含 `tests/infra/test_goal_db_locked_guard.py`、
`tests/test_graph_store_concurrency.py`、`tests/test_graph_store_c2_read_open.py`、
`tests/infra/test_wal_governance_guard.py` 這 4 檔定向守衛 **22 passed**）。
🔴 那 48 failed ＋ 11 errors **全部是 worktree 環境性失敗**（worktree 位於 `%TEMP%` 之下、
`.git` 是檔不是目錄 ⇒ 以 git index 為基準的守衛無法解析），在**乾淨 HEAD 的基線回歸裡逐筆相同**；
本票新增的 2 個 untracked 檔**未**增加任何新失敗。

---

## 9. 🔺 工單內部張力（標示並附理由）

**張力**：工單 §0 與 §2.4 把「**候選甲（同一 DB 路徑單一實例／單一連線）**」列為首要修正軸，
但**既有守衛 `tests/test_graph_store_c2_read_open.py` 與甲互斥**：

| 條目 | 內容 |
|---|---|
| 既有守衛的斷言 | `tests/test_graph_store_c2_read_open.py:74`：`assert cnt == 0, f"reader 看見未 commit 的列（違反快照隔離）"` —— 同一路徑上的第二個 `GraphStore` **必須看不到** writer 尚未 commit 的列。 |
| 該檔的自述 | 「**C2 不讓 Reader 看見 Writer 尚未 commit 的列（快照隔離）— 那不是 bug**」；「**本檔守住，不得誤讀**」。 |
| 甲為何與它互斥 | 甲＝共用連線 ⇒ 「reader」就是 writer 的同一條連線 ⇒ 快照隔離消失 ⇒ **該守衛轉紅**。 |
| 工單自身的紅線 | §4.5「既有守衛不得被削弱」＋ §5 明列 `tests/test_graph_store_c2_read_open.py` 為必跑項目；§2.5.3「不得改 schema 契約」。 |
| 處置 | ⇒ **不採甲**，改採**乙＋丙**（§5-2）。本票**未**修改 C2 守衛、**未**改 C2 語意；C2 守衛在修正前後**皆綠**（見 §11）。 |

**另一處張力（已用可執行的方式消解）**：工單 §3 的 **W1 與 W3 在同一層斷言會互斥**（見 §6 末）⇒ 已用「W1 關仲裁 / W3 開仲裁」的方式讓兩條同時成立，且兩條都仍然有牙。

---

## 10. 🔴 誠實聲明

- 本票是**機制釐清 ＋ 離線修正**。
- **生產尚未套用**：修正在 `src/memory/sage/graph_store.py`，**要重啟服務才會生效**，且**須 Owner 授權重啟**。本票**未**重啟、**未**部署。
- **不宣稱已消除**行程內寫鎖競爭。本修正**消除的是「同一行程內、同一 DB 路徑、多實例」這一類**競爭（已量測：21.2s → 0.000155s）；**若根因在更上層**（例如**外部行程**的長時間未提交寫入者、或 sqlite 檔案層的其它寫入者），**本修正不保證**能避免同型事件。
- 本修正**不**處理**跨行程**競爭（`busy_timeout` 仍是 10s 的明文契約）。
- M-2 的「持鎖者身分」是**由證據鏈推得**（同一路徑的另一實例 ＋ 無外部程序 ＋ 該實例在鎖窗口內有寫入），**不是**由生產埋點直接讀出的；生產**目前沒有**「誰持鎖、持多久」的埋點 ⇒ **最小侵入的埋點設計（log-only、fail-silent）**留待主大腦審定（**本票未自行上線**）。

---

## 11. 未做 / 明文排除

- **未**提高 `busy_timeout`（維持 10000 的明文契約）。
- **未**引入無界排隊的鎖（仲裁等待上界 2.0s ＋ fail-closed）。
- **未**動 `src/memory/sage/writer.py`（frozen）、**未**動 11 個 `frozen_dirs` 檔、**未**改 schema 契約、**未**改 `_SCHEMA_VERSION`、**未**改 `flush()` 的 rollback 語意。
- **未**動 `scripts/run_server.py`（flush 週期 15s 未動 ⇒ 變更面限於 `graph_store.py`）。
- **未**重啟、**未**部署、**未**殺任何行程、**未**寫生產（`data/**` 唯讀觀測）、0 真實 LLM、0 網路、0 對生產 sqlite 的新連線。
- **未** `git add` / `git commit` / `git push`（**絕對無** `git add .`）。
- **未**新增定時器／背景 task／額外連線。
- **未**處理召回池／superseded 主線（另一票）。

---

## 12. 產出物

| 產出物 | 路徑 |
|---|---|
| 修正 | `src/memory/sage/graph_store.py`（worktree `%TEMP%\wlock_wt`） |
| 新守衛 | `tests/infra/test_graph_store_write_contention_guard.py`（8 筆 W1～W8） |
| 本報告 | `logs/FEAT-GRAPHSTORE-WRITE-CONTENTION-REPORT.md` |
| 工程狀態條目 | `logs/ENGINEERING_STATE.md`（追加） |
| 差異檔 | `%TEMP%\p5_wlock_scripts\p5_wlock_patch.diff`（tracked 變更）＋ `p5_wlock_patch_newfile.diff`（新增守衛檔） |
| M-3 量測腳本 | `%TEMP%\p5_wlock_scripts\m3_repro.py` |
| 變異 harness | `%TEMP%\p5_wlock_scripts\mutations\run_mutations.py` |
| 變異報告 | `%TEMP%\p5_wlock_scripts\mutations\mutation_report.{json,md}` |
