# SIM-EPIS-ACTIVATE-READY 實作紀錄（排程自主觸發 ＋ 張力承載 append-only）

- 工單：`logs/SIM-EPIS-ACTIVATE-READY-WORK-ORDER.md`
- 分支：`main`（HEAD `d037fed`）
- **本票不點火**：旗標 `SOUL_OS_EPISTEMIC_PIGGYBACK` 維持缺省 OFF ⇒ 生產零行為變化。
- **未 commit／未 push**（主大腦驗收後執行）。新檔已 `git add` 進 index
  （repo 自身的 `tests/infra/test_repo_hygiene_tracked_tests.py` 要求 `tests/**`
  的可收集測試檔必須在 index 內；`git add` ≠ commit）。

---

## 1. 改動檔案清單（6 個，全部在工單 §2 的寫入權內）

| # | 檔案 | 動作 | 內容 |
|---|---|---|---|
| 1 | `src/inner_life/epistemic_action.py` | 修改 | 張力狀態承載改 **append-only JSONL**；舊檔一次性遷移（改名 `.migrated`）；新增 `read_last_interaction_ts` |
| 2 | `src/inner_life/epistemic_tension_scheduler.py` | 新增 | 獨立排程 tick（純函式 `tick(now)` ＋ 共用節流觸發 ＋ daemon 執行緒） |
| 3 | `src/llm/proxy.py` | 修改（最小） | 節流狀態抽成 `_claim_background_action_slot`（兩個觸發點共用）；新增排程路徑入口 `_maybe_trigger_background_epistemic_action_scheduled`；檔尾**模組層級掛點** |
| 4 | `tests/inner_life/test_epistemic_tension_store.py` | 新增 | 守衛 1／2／3 ＋ 漂移守衛（6 支） |
| 5 | `tests/inner_life/test_epistemic_scheduled_trigger.py` | 新增 | 守衛 4／5／6／7 ＋ 漂移守衛（8 支） |
| 6 | `logs/SIM-EPIS-ACTIVATE-READY-IMPLEMENTATION.md` | 新增 | 本檔 |

**未動**：`src/soul/scheduler.py`、`src/soul/decision.py`、`src/inner_life/epistemic_appraisal.py`
（**V4 不放寬**）、`src/inner_life/piggyback_queue.py` 的佇列語意、`src/agent/**`、
`src/memory/**`、`configs/**`（含 `configs/packs/**`）、`data/**`、`personas/**`、
任何 frozen contract。

---

## 2. 張力承載改 append-only（工單 §3.1）

- 新承載：`<store>/epistemic_tensions.jsonl`（**每行一筆事件**）。
- 事件型別（**封閉集合**）：`tension_opened`／`tension_resolved`／`tension_thought_enqueued`。
- 事件欄位（封閉形狀）：`ts`／`agent_id`／`tension_id`／`kind`／`subject`／`status`／
  `payload`（`payload` 沿用既有 `Tension.to_dict()` 形狀 ＋ 保留額外欄位
  `resolved_at`／`thought_payload`／`thought_tags`／`migration_digest`）。
- **讀取語意**：fold 全部事件 ⇒ 每個 `tension_id` 取**最後一筆**（與
  `lifecycle_overlay._reduce_events` 同一思路）⇒ 等價於舊版 dict 覆蓋。
- **寫入原子**：`open(path, "ab")`（`O_APPEND`）＋ **單次** `write()` ＋ `flush()` ＋
  `os.fsync()` ⇒ 多行程併寫不互相覆寫、掉電不遺失。
- **舊檔相容**：讀取時若 `epistemic_tensions.json` 存在 ⇒ 以其內容 sha256 當遷移標記，
  **一次性**折算成事件寫入 JSONL，舊檔**改名**為 `epistemic_tensions.json.migrated`
  （**不得刪除**；若 `.migrated` 已存在則續號 `.migrated.1`、`.2`…，不覆寫）。
- `Tension` dataclass 的**對外形狀未動**（欄位名與順序不變；守衛
  `test_tension_dataclass_shape_is_unchanged` 釘死）。

## 3. 排程自主觸發（工單 §3.2）

### 3.1 純函式 `tick(now) -> List[agent_id]`

- **只讀、零 LLM、零網路、零寫入**：只做讀取與判定，不觸發任何東西。
- 判定（兩者皆須成立）：
  1. **落在張力窗內**：距上次互動 `elapsed ∈ [24h, 7d)`（＝
     `temporal_phenomenology._CALM_MAX_ELAPSED_SEC` / `_TENSION_MAX_ELAPSED_SEC`，
     私有常數不可 import ⇒ 本模組自帶一份並以漂移守衛釘死）。
  2. **有未閉合張力**：承載裡已開啟未閉合的張力，或**當下的關係張力**
     （窗內 ＋ 有羈絆證據 ⇒ 這條關係張力此刻尚未閉合）。
- 張力狀態判定**沿用上游** `classify_temporal_state`（不另立一套規則）；
  `STATE_TENSION` **直接引用上游常數**（不可能漂移）。
- **節流常數沿用** `proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS`（300 秒、逐 agent）
  ⇒ 本模組**不另立**一套節流（守衛 `test_throttle_interval_is_shared_with_proxy`）。
- **時區**：elapsed 以 **epoch 秒**計算（時區無關），與上游
  `classify_temporal_state(last_interaction_ts: int, now: int, …)` 的介面一致；
  節奏為固定間隔（非 wall-clock slot）⇒ **不需要** `LOCAL_TZ`。

### 3.2 「最後互動」的資料源

`relationships.json` 的 `last_interaction_at`（唯讀），**不是** memory DB 的 messages 表。

理由（工單 §3.2 的守衛 4 明訂「memory DB 訊息數 = 0 時，tick 仍要回傳」）：
判斷必須只依賴**關係狀態**本身，否則「沒有任何對話流量」時根本無從判斷。
生產交叉驗證（本票實測，2026-10-21 23:37Z）：

| agent | `last_interaction_at`（relationships.json） | elapsed（天） | 窗內？ |
|---|---|---|---|
| agent_rem | 2026-10-17T02:39:43Z | **4.88** | ✅ |
| agent_yua / agent_ruka | 2026-10-21T23:17Z | 0.00 | ❌（CALM） |
| agent_akane / agent_mahiru / agent_mai | 2026-09-22 | 29.5~29.8 | ❌（過窗） |
| agent_anna / agent_aoi / agent_miku | 2026-08-06~07 | 75.3~77.0 | ❌（過窗） |
| agent_ram | 2026-09-16 | 35.9 | ❌（過窗） |

`tick(now)` 在生產資料上的實測輸出：**`['agent_rem']`**（與主大腦 §0.1 的表一致）。

### 3.3 排程掛點（工單 §6.4）

**掛點＝`src/llm/proxy.py` 的模組層級（檔尾）**。

- **為何不在 `src/soul/scheduler.py`**：工單明令不得動它。既有可掛載的閒置 tick
  （`HeartbeatEngine` 的 `SYSTEM_TICK`）在**沒有任何用戶端連線時就不發 tick**
  （`engine.py` 的 `_loop` 只在有連線時廣播）⇒ 不能保證「無對話流量也能觸發」，
  故不採用。
- **為何模組層級可行**：`scripts/run_server.py` 啟動時會匯入 `src.llm.proxy`
  ⇒ 排程 tick **隨服務啟動**，完全不依賴任何一則訊息組裝。
- **旗標守衛**：掛點只在 `SOUL_OS_EPISTEMIC_PIGGYBACK` 開啟時啟動執行緒；
  缺省 OFF ⇒ **不啟動任何執行緒**（生產零行為變化）。
- **不重複觸發**：訊息路徑與排程路徑**共用** `proxy._BACKGROUND_ACTION_STATE` 的
  per-agent 節流槽（`_claim_background_action_slot`）⇒ 同一 agent 在同一節流窗內
  **只會跑一次**。

### 3.4 「無對話流量也能觸發」的實測證據

沙盒（隔離資料根，**零生產接觸**）：記憶庫 `messages` 表 **0 則訊息** ＋
`relationships.json` 設為 2 天前互動 ⇒ 匯入 `src.llm.proxy`（啟動模擬）：

```
{"memory_db_message_count": 0,
 "tick_result": ["agent_probe"],
 "thread_fired_without_any_message": true,
 "tension_store_lines_after_thread": 1,
 "tension_store_events": ["tension_opened"],
 "explicit_scheduled_pass_fired_same_window": [],
 "message_path_same_window_added_lines": 0,
 "total_store_lines_final": 1}
```

讀法：

1. `memory_db_message_count = 0` ⇒ **完全沒有對話流量**。
2. `tick_result = ["agent_probe"]` ⇒ 純函式 tick 仍回傳窗內 agent（守衛 4）。
3. `thread_fired_without_any_message = true` ＋ 承載多出 1 筆 `tension_opened`
   ⇒ **排程執行緒自己觸發了背景行動**（不是任何訊息觸發的）。
4. `explicit_scheduled_pass_fired_same_window = []` ⇒ 同一節流窗內，
   排程路徑**不會**重複觸發。
5. `message_path_same_window_added_lines = 0` ⇒ 訊息路徑與排程路徑**共用節流**
   ⇒ 背景行動只跑一次（守衛 5）。
6. `total_store_lines_final = 1` ⇒ 全程只跑了 1 次背景行動。

掛點本身的實測（兩個全新行程，只差旗標）：

```
flag=off → threads: ["MainThread"]                              （無排程執行緒）
flag=on  → threads: ["MainThread", "epistemic-tension-scheduler"]（有排程執行緒）
```

---

## 4. 守衛測試 ＋ 變異實驗（工單 §3.4）

守衛：

| 守衛 | 內容 | 落點 |
|---|---|---|
| 1 | append-only：N 筆寫入 ⇒ N 行都在檔內；fold 的最後狀態正確；末筆可見；append 必須 `fsync` | `test_guard1_tension_store_is_append_only_and_durable` |
| 2 | 多執行者併寫（2 × 30）⇒ 60 筆全部保留 | `test_guard2_concurrent_appends_keep_every_record` |
| 3 | 舊檔遷移：`.migrated` 保留、JSONL 等價、舊檔未被刪除、冪等 | `test_guard3_legacy_store_is_migrated_and_never_deleted` |
| 4 | 記憶庫訊息數 = 0 時，tick 仍回傳窗內 agent | `test_guard4_tick_finds_tension_agent_without_any_conversation` |
| 5 | 訊息路徑 ＋ 排程路徑同一節流窗只跑一次；且節流**逐 agent** | `test_guard5_message_and_scheduled_paths_share_one_throttle_slot` |
| 6 | 排程路徑對用戶發訊數 = 0（事件總線攔截計數 = 0） | `test_guard6_scheduled_pass_emits_no_user_facing_message` |
| 7 | 旗標缺省關閉 ⇒ 不啟動任何執行緒；且排程路徑是 no-op | `test_guard7_flag_off_starts_no_thread` / `test_guard7_flag_off_means_scheduled_pass_is_a_no_op` |

變異（每條都**實際改檔 → 跑守衛 → 轉紅 → 逐位元還原**；還原後 sha256 相同）：

| # | 變異內容 | 轉紅的守衛 | 紅燈實測 |
|---|---|---|---|
| 1 | append 寫入改為**整份覆寫**（`open(path, "ab")` → `"wb"`）⇒ 後寫者覆蓋先寫者 | 守衛 1 ＋ 守衛 2 | `AssertionError: 併寫：60 筆（2 執行者 × 30 筆）必須全部保留，實得 1 行`（`assert 1 == 60`）；守衛 1 同報 `len(lines) == n+1` 失敗 |
| 2 | append **不 flush／不 fsync** | 守衛 1 | `AssertionError: append 路徑必須呼叫 os.fsync（掉電不遺失）`（`assert []`） |
| 3 | 遷移時**刪除**舊檔（`replace(.migrated)` → `unlink()`） | 守衛 3 | `AssertionError: 舊檔必須以 .migrated 保留在原目錄`（`assert False is_file()`） |
| 4 | 排程 tick **不觸發**（`tick()` 直接回 `[]`） | 守衛 4 | `AssertionError: 兩天前互動的 agent 必須落在張力窗內`（`assert [] == ['agent_x']`） |
| 5 | 節流**退回全域**（`_BACKGROUND_ACTION_STATE.setdefault("__global__", …)`） | 守衛 5 | `AssertionError: 節流必須逐 agent（全域節流會讓多角色互相排隊）`（`assert False is True`） |
| 6 | 排程路徑**繞過旗標**（`run_scheduled_pass` 拿掉 `feature_enabled()` 檢查） | 守衛 7 | `AssertionError: assert ['agent_x'] == []`（旗標關閉仍觸發） |
| 7 | 排程路徑**觸發發訊**（在 `_trigger_agent` 內 publish `AGENCY_TRIGGER`） | 守衛 6 | `AssertionError: 對用戶發訊數必須為 0，實得 1`（`assert 1 == 0`） |

> 變異 1 的說明：工單寫「退回單一 JSON 覆寫」。本次以**寫入原語**（append → 整份覆寫）
> 施加，捕捉到的性質正是舊版單一 JSON 承載的「後寫者覆蓋先寫者」；若改寫成
> 「整份 JSON 覆寫＋改讀取面」，會連帶改掉讀取語意（那是另一個變異）。守衛 1／2 的
> 紅燈皆為**真實斷言失敗**（非攔截）。
>
> 變異 6 的第一版守衛太弱（該測試當時沒有落在窗內的 agent ⇒ 變異繞過旗標也轉不了
> 紅）。已補強：先寫入「2 天前互動」的關係資料，並加上**反向證明**（同一組參數在
> 旗標開啟時確實會觸發）⇒ 變異 6 才真的轉紅。

---

## 5. 驗收對照

| 工單 §4 | 結果 |
|---|---|
| 1. 兩支新測試檔全綠 | ✅ **14 passed**（`.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_epistemic_tension_store.py tests/inner_life/test_epistemic_scheduled_trigger.py`） |
| 2. 7 條變異全部轉紅 | ✅ 見 §4 表 |
| 3. 全庫 `pytest -q tests` 0 failed | ✅ **6899 passed / 24 skipped / 27 subtests passed / 0 failed**，748.39s（0:12:28），解譯器＝repo 的 `.venv`（`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`） |
| 4. 生產零行為變化 | ✅ 旗標缺省 OFF ⇒ 無執行緒；`data/**` sha256 前後**只有服務自己的寫入**（見 §7）；未重啟服務、未動任何行程 |
| 5. 實作紀錄 | ✅ 本檔 |

> ⚠️ 全庫回歸第一次跑出 6 紅，其中 5 紅屬於同工作樹的另一個 worker（戰役 C 第一階段：
> `src/agent/**` 的 ImportError 與 allowed 集合）⇒ 已由對方修好；第 6 紅是
> `tests/infra/test_repo_hygiene_tracked_tests.py`（新增的 `tests/**` 檔必須在 git
> index 內 ⇒ 已 `git add` 新檔，**未 commit**）。最後一次全庫回歸為 **0 failed**。


---

## 6. 生產 sha256 前後對帳（零行為變化）

全庫回歸前後對 `data/**`（7313 → 7328 個檔案）逐檔 sha256 比對：

- **本票程式在生產的唯一寫入面**（＝張力承載 ＋ 話頭佇列）：
  `data/inner_life/epistemic_tensions.jsonl` **不存在**，
  `data/inner_life/*.piggyback.json` **不存在** ⇒ **本票的程式從未寫入生產**
  （旗標缺省 OFF ⇒ 連檔案都沒被建立）。
- 35 個 sha256 變更 ＋ 16 個新增 ＋ 1 個刪除，**全部是「活著的生產服務」自己寫的**：
  - 新增 15 個 `data/tts/agent_{ruka,yua}/*.mp3`（TTS 產物）＋
    `data/world/world_log/2026-10-22.jsonl`（日誌換日）＋ 刪除 10-21 的日誌檔；
  - `data/conversations/*`、`data/agents/*/emotional-state.json`、
    `data/memory/*`、`data/soul/agent_{ruka,yua}/relationships.json`
    （＝ 服務正在跟 ruka／yua 對話的最後寫入時間 2026-10-21T20:21:41 起的活動）；
  - `data/server_nohup.log`（20:26:47）、`data/logs/watchdog.log`、
    `data/elevation/*`（night slot 評估）、`data/inner_life/trace.jsonl`
    ＝ 服務自己的常駐寫入。
- **關鍵佐證**：上述 5 個 trace 檔在 baseline 之後**追加的位元組**裡，
  `epistemic`／`tension`／`piggyback`／`epistemic_closure` 四個關鍵字命中數
  **全部為 0** ⇒ 本票的機制**沒有**在生產留下任何痕跡。
- 服務**未重啟**、未動任何行程（全程無 `Stop-Process`／`taskkill`／`pkill`）。

---

## 7. 本票未解決的殘餘問題

1. **pack 擴充仍未做**（另開票）⇒ 缺口路徑（channel 1，`epistemic_appraisal`）仍只覆蓋
   **有 pack 且未被 V4 bypass** 的 agent；`modern_earth` 角色（rem／yua／ruka 等）
   仍走不到缺口路徑 ⇒ 排程觸發對它們只走**關係張力路徑**。
2. **排程觸發點不在既有排程器**（`src/soul/scheduler.py` 不可動）⇒ 走 proxy 模組層級的
   daemon 執行緒（旗標開啟時）。若日後要併回既有 night slot，需另開票並處理
   「同一 agent 兩個觸發點」的節流歸屬。
3. **張力窗常數自帶一份**（上游為私有常數）⇒ 以漂移守衛釘死；上游若改常數，守衛會紅。
4. **舊版單一 JSON 承載的遷移是「首次讀取時」發生** ⇒ 沒有人讀過就不會遷移（舊檔原樣
   留著，不會被刪）⇒ 安全但延後。
5. `logs/SIM-EPIS-PIGGYBACK-1-IMPLEMENTATION.md` §6 殘餘問題 4（「張力狀態承載是單一
   JSON 檔，非 append-only」）**由本票解決**；該檔不在本票寫入權內，未更新（需主大腦
   或後續票同步）。
