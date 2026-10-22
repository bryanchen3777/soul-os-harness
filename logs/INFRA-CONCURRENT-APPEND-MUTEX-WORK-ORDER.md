# 工單：INFRA-CONCURRENT-APPEND-MUTEX（Windows append-only 承載檔的併寫遺失）

> **狀態**：已實作並驗證（2026-10-22）。本檔是**決策完整**的工單本體 + 實測證據存檔。
> **Owner 裁決**（Bryan，2026-10-22）：**開立 P1 票 INFRA-CONCURRENT-APPEND-MUTEX，排入工程修復**；
> 拒絕放寬守衛、拒絕駝鳥。架構方向：**輕量互斥鎖，拒絕過度工程**（不採 Win32 長命 C handle 生命週期管理）。

---

## 目標

消除 Windows 上 append-only 承載檔（張力承載、世界日誌）併寫時的「**整筆無痕跡消失**」，
讓 `test_guard2_concurrent_appends_keep_every_record` 與 stdout 釋放測試在**滿載時 100% 穩定綠燈**。

---

## 根因（實測定案，非推論）

Windows 上 CPython 的 `open(path, "ab")` **不是原子追加**：它走 MSVCRT 的 `_O_APPEND`，
CRT 在每次寫入前用「**該 handle 自己快取的檔案大小**」seek 到 EOF。兩個先後開啟的 handle
各自快取到**同一個（過期）大小** ⇒ 寫到同一偏移 ⇒ **後者整筆覆蓋前者**。

樣態：可完整解析、**零損壞行**、`write()` 全部回傳成功 ⇒ 看起來像「整筆無痕跡消失」。

### 量測證據鏈

| 量測 | 結果 |
|---|---|
| 真遺失 vs 讀視圖過期（`os.fstat(fd).st_size`，handle-based 權威） | **真遺失**：檔案本身短 1～3 筆（10698 vs 應有 10880） |
| 1.5 s 後重讀 | **完全不收斂** ⇒ 不是讀視圖問題 |
| 「讀到視圖收斂為止」的重試修正 | **完全無效**（settled == immediate）⇒ 排除 |
| 現況寫法，6 個獨立行程真實負載、30 輪 | **10/30 輪遺失（33.3%）** |
| 行程內互斥 ＋ Win32 核心層追加，同條件 | **30/30 全對** |
| 互斥 ＋ `fstat`/`lseek` 自算偏移 | 8/8 輪檔案大小多 60 bytes ❌ ⇒ **不可用** |

⚠️ **不要用「自己算寫入位置」修 append 缺陷** —— 那正是把過期 metadata 當真相。

---

## 範圍

**動**：
- `src/fsio/__init__.py`（新）
- `src/fsio/atomic_append.py`（新）—— 原子追加原語
- `src/inner_life/epistemic_action.py` —— 張力承載寫入接線（＋ 修正被證偽的 docstring）
- `src/world/world_log.py` —— 世界日誌 binary append 接線
- `tests/inner_life/test_atomic_append.py`（新）—— 15 項守衛
- `tests/inner_life/test_epistemic_tension_store.py` —— guard1 的**持久化屏障觀察點升級**（不變式不變）
- `tests/world/test_world_log_phase_a.py` —— 世界日誌 H.2 的**觀察點擴充為兩條通道**（不變式不變）
- `tests/test_observability_completeness_1.py` —— `allowed` 清單逐票更新（+3 檔）

---

## 做法（決策已定，執行者照做）

### 1. 新增 `src/fsio/atomic_append.py`

- `append_lock = threading.Lock()` —— **行程內互斥鎖**（Owner 指定的那一層）。
- `append_bytes(path, blob)`：
  - **Windows**：`CreateFileW(access=FILE_APPEND_DATA, share=READ|WRITE|**DELETE**)`
    ＋ `WriteFile` ＋ `FlushFileBuffers` ＋ `CloseHandle`
    ⇒ **核心層保證寫在真正的檔尾**（跨行程也安全），且開檔帶 `FILE_SHARE_DELETE`
    （Windows 上不阻擋 pytest 清理 tmp 目錄）。
  - **其他平台**：維持 `open(path,"ab")`（POSIX 的 `O_APPEND` 本身就是原子的）。
  - **不持有長命 handle** ⇒ 無 handle 生命週期管理、無 LRU、無淘汰邏輯（Owner 明確拒絕的過度工程）。
  - 寫入失敗 **raise**（由呼叫端決定 fail-silent；原語義一致）。
- `uses_kernel_atomic_append()` —— 供守衛查詢「本次是否走核心層原子追加」。

**asyncio 層刻意不加**：`append_bytes` 內部無任何 `await`，同一 event loop 上的兩個 coroutine
不可能交錯 ⇒ asyncio lock 不帶來額外保證，卻會引入「lock 綁定特定 loop」的跨 loop 風險。
真正需要序列化的是**多執行緒** ⇒ `threading.Lock` 才是正確的那一層。

### 2. 接線

- `epistemic_action._append_tension_events`：`with open(path,"ab") …` ⇒ `append_bytes(path, blob)`。
- `world_log.WorldLogWriter.write`：同上。
- **docstring 必須更正**：舊宣稱「以 `ab`（`O_APPEND`）開檔 ⇒ 多行程併寫各自寫到檔尾，
  **不互相覆蓋**」在 Windows 上是**錯的**，已改寫為實測結論。

### 3. 守衛（15 項）

| 守衛 | 內容 |
|---|---|
| A1 | **結構**：生產路徑（張力承載）必須真的走 `append_bytes`（用 recorder monkeypatch 驗證） |
| A2 | **結構（確定性迴歸偵測器）**：Windows 上必須走核心層原子追加，不得退回 CRT `_O_APPEND` |
| B ×12 | **行為**：每輪 2 執行緒 × 30 筆 ⇒ 60 行、60 個可解析且 tension_id 唯一、**檔案位元組數 == 各紀錄位元組數之和**（位元級 ⇒ 覆蓋／截斷都抓到）、無 CR |
| C | **位元組契約**：世界日誌走原子追加且維持 LF-only |

---

## 驗收（完成的定義）

1. `test_guard2_concurrent_appends_keep_every_record` **滿載下穩定綠**（不再 flaky）。
2. 新守衛 15 項全綠。
3. **變異實驗雙向**：
   - `SOULOS_APPEND_MUTATION=legacy_crt_append` ⇒ **守衛必須轉紅**（已驗：A2 紅）。
   - 正常模式 ⇒ 全綠。
4. 全庫回歸（`.venv\Scripts\python.exe -m pytest -q tests`）**0 failed**。
5. 生產未受影響：`/health` 200、trampoline／`:8000`／VC 三實例 PID 皆未變、`data/**` 未被寫入。

---

## 測試

- 新增：`tests/inner_life/test_atomic_append.py`（15 項）。
- 升級：`tests/inner_life/test_epistemic_tension_store.py::test_guard1_..._durable`
  —— 觀察點從「原始碼含 `os.fsync`」改為「實際的持久化屏障被呼叫」
  （Windows＝`FlushFileBuffers`／POSIX＝`os.fsync`）。**不變式不變**（屏障沒被呼叫照樣轉紅）。
- 升級：`tests/world/test_world_log_phase_a.py::...::test_h2_writer_opens_the_shard_in_binary_mode`
  —— 觀察點擴充成兩條通道（`builtins.open` ＋ `atomic_append.append_bytes`），
  並追加「raw bytes 必須 LF-only」的位元組契約斷言。**不變式不變**。
- 變異實驗（主大腦手動執行，結果見 `logs/ENGINEERING_STATE.md` §0.1.36）：
  `SOULOS_APPEND_MUTATION=legacy_crt_append` ⇒ 守衛轉紅。

---

## 不做（Out of Scope）

1. **不**改約 25 個 text-mode append 點（`diary.py`／`life_threads.py`／`trace.py`／…）：
   它們是**單寫入者**路徑（每 agent 自己的檔案、排程循序寫入），無併發寫入者。
   原語已備妥，若未來出現多寫入者路徑可一鍵套用。**清單見 §0.1.36 ④。**
2. **不**引入新的第三方相依（`portalocker` 等）—— 原語只用 stdlib（`ctypes`）。
3. **不**採用 Win32 長命 C handle 生命週期管理（Owner 明確拒絕）。
4. **不**改動任何 frozen contract（`src/agency/stages.py` 等 10 筆凍結全部保留）。
5. **不**改 `data/**`、不重啟服務、不觸發人工事件。

---

## Frozen Contract 注意

`src/agency/stages.py`／`trigger_handler.py`／`event_handler.py`／`dream_handler.py`／
`diary_handler.py`／`src/inner_life/submission_gate.py`／`src/social/identity_firewall.py`／
`src/social/producer_gate.py`／`src/world/middleware.py`／`src/memory/sage/writer.py`／
`src/soul/decision.py` —— **本票一概不碰**。

---

## 回報格式

- 改動檔案清單（含新增）
- 測試結果：跑了哪些、過幾筆、有無失敗（**必須註明解譯器是 repo 的 `.venv`**）
- 變異實驗結果（轉紅了嗎？紅在哪一條？）
- 是否踩到 Frozen Contract / 意外行為
