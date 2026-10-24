# 工單：FEAT-GRAPHSTORE-WRITE-CONTENTION（行程內寫鎖競爭 —— 量測先行 ＋ 機制對齊的修正）

> 開立：2026-10-24 15:30 EDT（主大腦；依 **Owner 2026-10-24 14:2x 裁定**：「將在後續排程維護中為 `graph_store.py` 引入 Agent 級寫入互斥鎖 **或** 提升 busy_timeout 排隊容忍度」）
> ⚠️ **主大腦在開立前完成機制複核 ⇒ 裁決的兩個選項都對不上實際機制**（證據見 §0）。本票因此**改為「量測先行 ＋ 依量測選定修正」**，並把**為什麼否決兩個選項**寫死，避免執行者去實現一個不對症的機制。
> 依賴：`logs/INCIDENT-GOAL-DB-LOCKED-*.md`（事故補丁 P1/P2）＋ `logs/FEAT-SQLITE-WAL-GOVERNANCE-WORK-ORDER.md`（治理已合入 `1aca261`）

---

## 0. 出發點（主大腦 2026-10-24 10:36～15:30 實測，不猜）

**現象**：2026-10-24 **10:36:00～10:36:32** 出現 **8 筆 `database is locked` ＋ 2 筆 `[Goal] 主循环扫描异常 (fail-closed)` ＋ 4 段 Traceback**（10:14 的觀測仍是全 0；之後 4.5 小時未再復發）。

| 事實 | 值 | 來源 |
|---|---|---|
| **層 1 自癒有效** | 每個鎖錯都是「重建連線後重試一次」即恢復（`[GraphStore] 鎖錯誤自癒`）⇒ **沒有**原事故那種千筆連鎖雪崩；Goal 掃描 **fail-closed** ⇒ **零資料損毀** | 服務日誌 |
| **觸發源：行程內雙寫者** | `10:36:03 [MemoryWriter] v1 store mirror 成功 n_facts_mirrored=5/5` → `10:36:11`／`10:36:32` Goal 掃描 `transition_goal` 報鎖錯 ⇒ **同一行程內兩個寫者** | 服務日誌時序 |
| **無外部 sqlite 程序** | 生產 `graph.sqlite-shm` 的 mtime 仍停在**服務啟動時刻 23:03:17** ⇒ 10:36 **沒有任何外部程序掛上過**（WAL 模式下 `mode=ro` 亦會改寫 `-shm`） | 主大腦實測 |
| **測試不碰生產** | `tests/conftest.py::_isolate_soul_os_data_root`（autouse）把 `SOUL_OS_DATA_DIR` 指向 tmp ＋ `reset_data_root()`；**全庫沒有任何測試使用 `allow_real_data_root` opt-out** ⇒ 全庫回歸不碰生產 sqlite | 主大腦實讀 ＋ grep |
| **同一實例內已序列化** | `GraphStore` 的 `@_locked`（`RLock`，**per-instance**）序列化所有 sqlite ＋ networkx 操作（KI-008 為防 ACCESS VIOLATION 而加） | `graph_store.py:49-55` |
| **C2「讀取不變寫者」已實作** | `_init_db` 在 `current_version == _SCHEMA_VERSION` 時**跳過** `INSERT OR REPLACE INTO schema_meta` ＋ `commit()` ⇒ 開一個已是現行版本的庫**不會**變成寫者 | `graph_store.py:200-211`；`tests/test_graph_store_c2_read_open.py` 檔頭（記載「僅此 INSERT 阻塞 11s → locked」） |
| 🔺 **busy_timeout 已是 10000 ms** | `PRAGMA busy_timeout=10000`（明文契約，`timeout=10.0` 同值） | `graph_store.py:145-149` |
| 🔺 **未滿批的寫交易會長期持有寫鎖** | `add_fact`／`upsert_goal`／`set_fact_dimensions`／`update_fact_weight`／`invalidate_fact` 一律 **`self._pending_writes += 1`**，**只在 `>= self.batch_size` 時才 `conn.commit()`** ⇒ 未滿批 ⇒ **交易不 commit ⇒ 該連線持續持有該 DB 的寫鎖** | `graph_store.py:492-495, 545-548, 576-579, 641-644, 779-782` |
| ⇒ **這解釋了「鎖撐過 10 秒才浮現」** | 鎖在 busy_timeout（10 s）耗盡後才拋 ⇒ **持鎖者不是「等鎖者」**，而是**另一條連線上未提交的寫交易** | 推論（待 M-2 驗證） |

### 🔴 為什麼我否決 Owner 選項的兩個分支（**請勿重蹈**）

1. **❌ 提高 busy_timeout**：現在已是 **10000 ms**。在 **asyncio 行程**裡，sqlite 的 busy 等待是**同步阻塞** ⇒ 把它調大＝**讓事件迴圈停更久** ⇒ 可能把「一個 agent 寫失敗」升級為「整個行程停頓」。⇒ **維持 10000 的明文契約不動。**
2. **❌ 無界的外部 per-agent mutex**：互斥只序列化**呼叫者**，**無法讓「持有者」加速**；而真正的問題是**連線層的交易生命週期**。更糟：排程器主迴圈是 **30s tick** ⇒ 若一個寫入排隊等 10 秒，**tick 會疊加** ⇒ 把「撞鎖後快速失敗（fail-closed，資料安全）」變成「慢慢排隊（可能拖垮 tick）」⇒ **比撞鎖更糟**。
3. 🔺 **且互斥必須 keyed by DB 路徑，不是 per-instance**：`@_locked` 是 **per-instance** 的 ⇒ 若同一 agent 的 DB 有**多個 `GraphStore` 實例**（例：MemoryWriter 與 Goal 掃描各持一個），兩把 RLock 互不知情 ⇒ 必須「同一 DB 路徑只有一條連線／一個實例」或「按 DB 路徑取鎖」才有效。

### ⇒ 本票真正要回答的問題（量測先行）

**同一個 `graph.sqlite` 在生產行程內，同時存在幾個 `GraphStore` 實例？未滿批的寫交易把寫鎖持有多久？** 若實例數 > 1 且交易長期未 commit ⇒ **根因是「同一 DB 檔多條連線 ＋ 未滿批交易不 commit」**，而修正軸是：**① 同一 DB 路徑在行程內只有一條連線／一個實例**（⇒ 未提交交易被共用，不會跨連線互擋）**② 交易生命週期有界**（未滿批也定期 commit／flush，或縮短 batch）**③ 兩者並用**。**若實例數 == 1** ⇒ 修正軸只剩 ②。

---

## 1. 目標

**先量測、再修正**；產出一份可被 Owner 審定的《行程內寫鎖競爭機制報告》，並（僅在 M-3 成功重現時）實作**對症**的修正。

**為什麼**：目前的 P2「重建連線重試」是**被動兜底**（症狀被壓住，因仍會再發）；而要「徹底消除競爭」，必須知道**誰持鎖、持多久、為什麼不 commit**。

---

## 2. 做法（決策已定，執行者照做）

### 2.1 M-1：生產量測 —— 同一 DB 路徑的實例數 / 連線數（唯讀）

- 量測對象：生產行程內**同一個** `data/memory/<agent>/graph.sqlite` 的 `GraphStore` 實例數（`GraphStore` 已有 live 實例集合 ⇒ 可用它；或以 instrumentation 觀測）。
- 交付：**每個 agent 的實例數分佈**（min／中位／max／分布）＋ 哪些元件各自持有實例（MemoryWriter／Goal 掃描／其他）。
- 🔴 **純觀測**：不得為量測而改生產行為；不得開新的生產 sqlite 連線（會製造新的寫者）。

### 2.2 M-2：生產量測 —— 寫交易的持有時間與持鎖者

- 量測：`_pending_writes` 的分佈（滿批前累積幾筆？）＋ 一次寫交易從開到 commit 的**持有時長**分佈（p50／p95／max）＋ 持鎖者身分（呼叫點）。
- 🔴 必須能回答：**「10:36 那次是誰持鎖、持了多久」**。
- 🔴 若無法在生產取得（缺埋點）⇒ **允許**提出**最小侵入的埋點設計**（log-only、fail-silent）供主大腦審定，**但不得自行上線**。

### 2.3 M-3：沙盒重現（本票的關鍵關卡）

在**沙盒**內構造：**兩個 `GraphStore` 實例指向同一 DB**，實例 A 寫入未滿批（不 commit）⇒ 實例 B 寫入 ⇒ **必須重現 `sqlite3.OperationalError: database is locked`（或 busy_timeout 耗盡）**。
- 🔺 **若 M-3 重現不出 ⇒ 機制假設錯了 ⇒ 立即停止並回報**（**不得**硬做修正、不得為了讓修正「看起來有效」而造一個重現不出來的守衛）。
- 重現必須**量測持鎖時長**（預期 ≈ busy_timeout 的量級），以證明「鎖撐過 10 秒」這個關鍵特徵。

### 2.4 M-4：依 M-1～M-3 的結果選定並實作修正

| 候選 | 適用條件 | 邊界 |
|---|---|---|
| **候選甲：同一 DB 路徑單一實例／單一連線**（行程內登錄表，以 DB 路徑為 key） | M-1 顯示實例數 > 1 | 🔴 必須處理 `close()` 的**共享生命週期**（一個元件 close 不得關掉別人的連線）⇒ 需要引用計數或「不可由外部 close」的語意 |
| **候選乙：交易生命週期有界**（未滿批也定期 commit，或縮短 batch） | M-1 顯示實例數 == 1，或 M-2 顯示持有時長過長 | 🔴 不得改 `flush()` 的既有 rollback 語意（GOAL-DB-LOCKED-1）；不得讓寫入**更頻繁 commit** 而破壞批次效能或未提交資料的可見性語意 |
| **候選丙：有界等待的 per-DB-path 鎖**（keyed by DB 路徑） | 僅當甲、乙 都不足以消除碰撞 | 🔴 **等待必須有界**（< `busy_timeout`）且 **fail-closed**（取不到就放棄、不排隊）⇒ **不得**讓 30s tick 疊加 |

⚠️ **選擇必須由量測支持，並在報告中寫明「為什麼選它、為什麼不選另外兩個」**。

### 2.5 邊界（硬性）
1. 🔴 **不得**提高 `busy_timeout`（維持 `PRAGMA busy_timeout=10000` 的明文契約）。
2. 🔴 **不得**引入**無界排隊**的鎖；任何等待都必須**有界 ＋ fail-closed**。
3. 🔴 **不得**改 schema 契約、不得改 `_SCHEMA_VERSION`、不得改 `flush()` 的既有 rollback 語意。
4. 🔴 **不得**動 `src/memory/sage/writer.py`（**frozen**）⇒ 若修正必須動它 ⇒ **停止並回報主大腦**。
5. 🔴 **不得**動 11 個 `frozen_dirs` 檔；`src/memory/sage/graph_store.py` **可動**（已在 observability `allowed` 內）但觸及面須最小。
6. 🔴 **不得**重啟服務、不得部署、不得殺行程、不得寫生產（除 M-1／M-2 的唯讀觀測）。
7. 🔴 0 真實 LLM、0 網路；不得對生產 sqlite 開**新的**連線（會製造新的寫者）。
8. 🔴 不得 `git add`／`git commit`／`git push`（**絕對禁止 `git add .`**）。

---

## 3. 守衛（新增檔 `tests/infra/test_graph_store_write_contention_guard.py`，**恰 8 筆** W1～W8）

| # | 內容 |
|---|---|
| W1 | **沙盒重現**：兩個實例指向同一 DB，A 持未滿批交易、B 寫入 ⇒ **必須**觀察到 busy／locked（否則守衛本身失效 ⇒ **必須**讓守衛紅） |
| W2 | **同實例不變**：同一實例內的寫入仍被 `@_locked` 串行化（既有語意未破） |
| W3 | **修正有效**：在沙盒重現情境下套用修正 ⇒ **不再**出現 `database is locked`（或鎖等待時間**有界且遠小於** busy_timeout） |
| W4 | **不讓 tick 疊加**：任何等待都有界（**斷言等待上界 < busy_timeout**）且取不到時 **fail-closed**（回 None／丟出可處理例外，不排隊） |
| W5 | **共享生命週期**（若採候選甲）：一個元件 `close()` **不得**關掉其他元件仍在用的連線 |
| W6 | **既有契約未動**：`busy_timeout` 仍為 10000；`flush()` 的 rollback 語意仍在；`_SCHEMA_VERSION` 未變 |
| W7 | **fail-silent**：寫入失敗時**不得**讓例外冒泡到排程器主循環（沿用既有 fail-closed 慣例） |
| W8 | **0 新副作用**：修正**不得**新增定時器／背景 task／額外連線（同一 DB 仍只有 N 條連線） |

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| P1 | 移除互斥／單例機制（回到兩條連線同時寫） | W3 |
| P2 | 把等待改成**無界**（`while` 重試） | W4 |
| P3 | 失敗時讓例外冒泡 | W7 |
| P4 | 讓 `close()` 無條件關閉共享連線 | W5 |
| P5 | 把 `busy_timeout` 調高（> 10000） | W6 |
| P6 | 讓修正**每輪開新連線** | W8 |
| P7 | 移除 `_locked`（同一實例內不再序列化） | W2 |

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 4 檔**：`src/memory/sage/graph_store.py`（修正）、`tests/infra/test_graph_store_write_contention_guard.py`（新增）、`logs/FEAT-GRAPHSTORE-WRITE-CONTENTION-REPORT.md`（新增）、`logs/ENGINEERING_STATE.md`（追加條目）。（`graph_store.py` 已在 observability `allowed` 內 ⇒ **不需要**改 `allowed`。）多一檔少一檔都**停止並回報**。
2. 新守衛 **8 筆全綠**；變異 **P1～P7 全數轉紅**（**每條恰好打紅預期的那一個守衛**）。
3. **M-1／M-2／M-3 的實測數字**（每個寫分子／分母／單位）＋ **M-3 的重現證據**（含持鎖時長量測）。
4. **修正選型理由**（為什麼選它、為什麼不選另外兩個候選）。
5. 全庫回歸：期望＝**當下 HEAD 的基線 ＋ 8 筆**（🔴 基線必須在合入當下重算）。
6. `logs/ENGINEERING_STATE.md` 追加條目必須寫明：① **機制全鏈**（C2 已實作 → 未滿批交易不 commit → 跨連線互擋 → busy_timeout 耗盡才浮現）② **實例數／持鎖時長的實測** ③ **修正選型與被否決的選項及理由** ④ **busy_timeout 維持 10000 的理由**（asyncio 阻塞事件迴圈）⑤ **fail-closed 與有界等待** ⑥ 誠實聲明：**本票不改變生產行為，直到主大腦審定並授權重啟**；且**不保證消除**未來同型事件（根因若在更上層，例如長時間未提交的外部寫入者）。
7. 🔴 **誠實聲明（必寫）**：本票是**機制釐清 ＋ 離線修正**；**生產上尚未套用**（重啟才生效，且須 Owner 授權）；**不宣稱已消除**行程內寫鎖競爭。

## 5. 測試
- 定向：`pytest -q tests/infra/test_graph_store_write_contention_guard.py` ⇒ 預期 **8 passed / 0 failed**。
- 既有守衛不得被削弱：`pytest -q tests/infra/test_goal_db_locked_guard.py tests/infra/test_graph_store_concurrency.py tests/test_graph_store_c2_read_open.py tests/goals/test_goal_engine.py`（**以實際存在的檔案為準**，缺檔請在報告中寫明）。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests`。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**。
- 🔴 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765-8767`；`tests/**` 禁用 `subprocess`／`os.spawn*`／`os.system`／`os.popen`。

## 6. 不做（Out of Scope）
- **不**提高 `busy_timeout`；**不**引入無界排隊；**不**動 `src/memory/sage/writer.py`。
- **不**重啟、不部署、不寫生產、不打真實 LLM、不連網。
- 不 commit／push／git add（由主大腦合入時處理）。
- 不處理召回池／superseded 主線（那是另一票）。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/` 5 檔、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。本票**不動**它們。

## 8. 回報格式
- 4 檔範圍 ＋ numstat。
- **M-1／M-2／M-3 的實測數字**（分子／分母／單位）＋ M-3 的重現證據與持鎖時長。
- **修正選型理由** ＋ 被否決的選項與理由 ＋ **`busy_timeout` 維持 10000 的理由**。
- 新守衛 8 筆 ＋ 變異 P1～P7 逐條。
- `logs/ENGINEERING_STATE.md` 追加條目內容摘要。
- **誠實聲明**（機制釐清 ＋ 離線修正；生產未套用；不宣稱已消除）。
- 未做與明文排除。
- 若遇工單內部矛盾或缺資訊：**標示並附理由回報**，不要自行改選解法。
