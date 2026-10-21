# 工單：SIM-EPIS-ACTIVATE-READY — 讓「自主求知行動」在生產上真的會發生（觸發點排程化 ＋ 張力承載 append-only）

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 00:50 EDT
> **Owner 裁決**：2026-10-21 19:20 —— 戰役 A 旗標維持預設 OFF，**待「張力 Pack 擴充」與「Append-Only 承載」就緒後再開**。
> **前置**：`82de5cb`（戰役 A 已合入，接線預設關閉）
> **⚠️ 本工單是主大腦依 2026-10-21 00:40~00:50 的實測，對上述裁決做的一處**前提修正**（見 §0.1）**。**

---

## 0. 主大腦的實測修正：點火前置**不是**「補 pack」

### 0.1 實測數據（2026-10-21 00:45，生產唯讀）

**（a）關係張力路徑的覆蓋率不受 pack 限制。** `read_bond_snapshot` 對 **10/10** 生產 agent 都回非 None（走 `relational_band` 判據，`impression_tags` 皆 None）。pack 只閘**第二來源**（缺口路徑），且 `epistemic_appraisal` 的 **V4 規則**（`civilization_base == "modern_earth"` ⇒ 直接 `None`）會讓**現代基底角色根本無法有 pack**。
⇒ **補 9 隻 pack 開的是第二來源，不是主來源。**

**（b）真實覆蓋率取決於「最近一次互動是否落在三態的張力窗內」。** 張力窗 ＝ `elapsed ∈ [_CALM_MAX_ELAPSED_SEC, _TENSION_MAX_ELAPSED_SEC)`（≈ **6 小時 ~ 7 天**）。

| agent | 真實最後互動 | 距今 | 張力數 | 判定 |
|---|---|---|---|---|
| yua／ruka | 2026-10-21T23:17 | 0.2 h | 0 | CALM（剛談過，**正確**） |
| **rem** | 2026-10-17 | **4.9 天** | **1** | **TENSION ✔ 唯一在窗內** |
| akane／mahiru／mai | 2026-09-22 | ≈29.5 天 | 0 | 超過 7 天窗 → RESOLVED |
| anna／miku／aoi | 2026-08-06~07 | ≈75~77 天 | 0 | 超過 7 天窗 → RESOLVED |
| ram | 2026-09-16 | ≈36 天 | 0 | 超過 7 天窗 → RESOLVED |

⇒ **生產現況 1/10 隻（rem）在張力窗內。** 其餘 9 隻不是缺陷 —— 是它們真的久未互動。

**（c）🔴 真正的結構性缺口：背景行動的觸發點。** 觸發掛在 `src/llm/proxy.py` 的**訊息處理路徑**（`_maybe_trigger_background_epistemic_action` 於組 prompt 時呼叫）。
⇒ **沒有人跟某 agent 講話 ⇒ 沒有訊息組裝 ⇒ 背景行動永遠不會觸發。**
⇒ 對 7 隻 29~77 天未互動的角色，**「自己想通一件事」在結構上不可能發生** —— 而這正是本機制存在的理由。
⇒ 這也是戰役 A 實作紀錄 §6 殘餘問題 3（觸發點是「閒置時」而非「槽後」；本票不得改 `src/soul/scheduler.py` ⇒ 需另開票）。
⇒ **這才是點火的第一前置**；「補 pack」是**第二來源的增值項**（可延後，且對 modern 角色會被 V4 bypass）。

### 0.2 觸發點現況（已讀碼確認）

- `proxy.py:4322` `last_interaction_ts = _get_last_interaction_ts(self._memory, agent_id)`（讀 memory DB 的 messages 表，**真實且最新**）→ `proxy.py:4327` 傳入。
- `proxy.py:1359` `def _maybe_trigger_background_epistemic_action(agent_id, utterance, *, last_interaction_ts: int = 0)`。
- `epistemic_action.py:691` `collect_tensions(..., last_interaction_ts: int = 0)`。
⇒ **只要呼叫端給真實值，路徑正確**（已實測 10/10 bond 可用）。**本工單不得改動這條資料流**，只補「無人在場時也能觸發」的那一段。

---

## 1. 目標

讓「自主求知行動」在**沒有任何對話流量**的情況下也能發生（排程自主觸發），且張力狀態承載**不會因多行程併寫而互相覆寫** ⇒ 達成 Bry 裁決 1 所要求的「Append-Only 承載就緒」與「張力 Pack 擴充」的**真正前置**。

**本票不點火**（旗標仍缺省 OFF）⇒ 生產零行為變化。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `src/inner_life/epistemic_action.py`（**修改**） | 張力狀態承載改 **append-only sidecar**（單一 JSON ⇒ append-only） |
| 2 | `src/inner_life/epistemic_tension_scheduler.py`（**新增**） | 獨立的排程 tick（**不**改 `src/soul/scheduler.py`） |
| 3 | `src/llm/proxy.py`（**最小改動**） | 背景行動觸發點改為「訊息路徑 ＋ 排程路徑」二者共用同一入口（避免雙重觸發） |
| 4 | `tests/inner_life/test_epistemic_tension_store.py`（**新增**） | append-only 承載守衛 ＋ 變異 |
| 5 | `tests/inner_life/test_epistemic_scheduled_trigger.py`（**新增**） | 排程觸發守衛 ＋ 變異 |
| 6 | `logs/SIM-EPIS-ACTIVATE-READY-IMPLEMENTATION.md`（**新增**） | 實作紀錄 |

**不得動**：`src/soul/scheduler.py`、`src/soul/decision.py`、`src/inner_life/epistemic_appraisal.py`（**V4 不放寬**）、`src/inner_life/piggyback_queue.py` 的佇列語意、`src/agent/**`、`src/memory/**`、`data/**`、`personas/**`、`configs/**`（**含 `configs/packs/**`**：pack 擴充另開票）、frozen contracts。

## 3. 做法（決策已定）

### 3.1 張力狀態承載改 append-only sidecar

- 現況：`<store>/epistemic_tensions.json`（單一 JSON，**非 append-only** ⇒ 多行程併寫互相覆寫；戰役 A 殘餘問題 4）。
- **改為**：append-only JSONL（每行一筆事件），例如 `<store>/epistemic_tensions.jsonl`。
  - 事件型別（封閉集合，**不得**自行擴充）：`tension_opened`／`tension_resolved`／`tension_thought_enqueued`。
  - 每筆含：`ts`／`agent_id`／`tension_id`／`kind`／`subject`／`status`／`payload`（其餘欄位沿用既有 `Tension.to_dict()` 形狀）。
  - **讀取語意**：fold 全部事件 ⇒ 依 `tension_id` 取**最後一筆**（與 `lifecycle_overlay._reduce_events` 同一思路）⇒ 等價於現況的 dict 覆蓋，但**寫入不再互相覆蓋**。
  - **舊檔相容**：讀取時若舊的 `epistemic_tensions.json` 存在，**一次性**折算成事件寫入 JSONL 後**改名為 `.migrated`**（**不得刪除**）。
  - **寫入必須原子**：append 單行 ⇒ 單次 `write()` ＋ `flush()` ＋ `fsync()`；**多行程安全**（`O_APPEND` 語意）。
- **不得**改變 `Tension` dataclass 的對外形狀（既有測試與漂移守衛依賴它）。

### 3.2 獨立的排程 tick（不動 `src/soul/scheduler.py`）

- 新增 `src/inner_life/epistemic_tension_scheduler.py`：一個**純函式** `tick(now) -> List[agent_id]`，對**每隻生產 agent** 判定「是否落在張力窗內且有未閉合張力」⇒ 回傳該輪應觸發背景行動的 agent 清單。
- **觸發節奏**：與既有排程風格一致（可重用 `src/timezone_utils.py::LOCAL_TZ`）；節流常數**沿用** `proxy.py::BACKGROUND_ACTION_MIN_INTERVAL_SECONDS`（300 秒、**逐 agent**）⇒ **不得**另立一套節流。
- **零 LLM、零網路、不得阻塞**：tick 本身只做讀取與判定。
- **不得**觸發任何對用戶的發訊路徑（`_publish_agency_trigger`／`proactive_dm` 一律不碰）。
- **接線方式**：由**排程器層級**呼叫（若 `src/soul/scheduler.py` 不可動 ⇒ 走既有可掛載的閒置 tick；**具體掛點由你自行定位並在報告中說明**，但**必須**證明：① 與 proxy 的訊息路徑**不重複觸發**（同一 agent 在同一節流窗內只跑一次）② 排程路徑能在**無對話流量**時觸發。

### 3.3 proxy 觸發點收斂（最小改動）

- 既有 `_maybe_trigger_background_epistemic_action(agent_id, utterance, *, last_interaction_ts)` 保持**簽名與語意**；只把**節流狀態**（`_BACKGROUND_ACTION_STATE`）改成**兩個觸發點共用**（訊息路徑 ＋ 排程路徑）⇒ 同一 agent 在同一節流窗內**只會跑一次**（既有 per-agent 節流語意已由戰役 A 修好，**不得退化**）。

### 3.4 守衛測試 ＋ 變異實驗

**守衛**
1. **append-only**：連續寫入 N 筆後，fold 出來的最後狀態正確；**N 筆都在檔內**（不再互相覆蓋）。
2. **多行程併寫**：兩個進程同時 append ⇒ **N 筆全部保留**（沙盒內以兩個 `os.fork`-free 的執行緒/進程模擬；**不得**用 `subprocess`／`os.spawn*`）。
3. **舊檔遷移**：舊 `epistemic_tensions.json` 存在時 ⇒ 折算後 `.migrated` 保留、JSONL 等價、**舊檔未被刪除**。
4. **排程觸發**：無任何對話流量（memory DB 訊息數 = 0）時，tick 仍回傳落在張力窗內的 agent。
5. **不重複觸發**：訊息路徑與排程路徑在同一節流窗內 ⇒ 背景行動**只跑一次**。
6. **零發訊**：tick 與背景行動**不產生**任何對用戶的發訊（事件總線攔截計數 = 0）。
7. **旗標預設關閉** ⇒ 排程路徑**不啟動**任何執行緒（生產零行為變化）。

**變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）**

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 承載退回單一 JSON 覆寫 | 守衛 1／2 |
| 2 | append 不 flush/fsync（掉電遺失） | 守衛 1（末筆可見） |
| 3 | 遷移時刪除舊檔 | 守衛 3 |
| 4 | 排程 tick 不觸發（只留訊息路徑） | 守衛 4 |
| 5 | 節流退回全域（多角色互相排隊） | 守衛 5 |
| 6 | 排程路徑繞過旗標 | 守衛 7 |
| 7 | 排程路徑觸發發訊 | 守衛 6 |

## 4. 驗收

1. `pytest -q tests/inner_life/test_epistemic_tension_store.py tests/inner_life/test_epistemic_scheduled_trigger.py` **全綠**。
2. **7 條變異全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
3. **全庫 `pytest -q tests` 0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
4. **生產零行為變化**：旗標缺省 OFF ⇒ 排程路徑不啟動、**不改** `data/**`（sha256 前後相同）、服務未重啟。
5. 實作紀錄寫 `logs/SIM-EPIS-ACTIVATE-READY-IMPLEMENTATION.md`（或全文列於回報），內含**排程掛點的說明**與**「無對話流量也能觸發」的實測證據**。

## 5. 不做（Out of Scope）

- **不得**點火（旗標維持缺省 OFF；啟用另需 Owner 授權）。
- **不得**放寬 `epistemic_appraisal` 的 V4（`modern_earth ⇒ None`）—— **-pack 擴充另開票**。
- **不得**改 `src/soul/scheduler.py`／`src/soul/decision.py`。
- **不得**新增任何對用戶的發訊路徑。
- 不得改 `Tension` dataclass 的對外形狀。
- 不得寫入 `data/**`（本票的沙盒寫入一律落 `tmp_path`）；不得重啟服務、不得動任何行程、不得跑 `scripts/manual_*.py`。
- `tests/**` 內不得使用 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`（R5 白名單大小鎖死 22）；不得硬寫生產服務位址（R3）。
- 🔴 **R5 護欄白名單以 `(path, lineno)` 配對** ⇒ 若要在**已被列入白名單的檔案**加註解，**必須讓被監看呼叫的行號不動**；**不得**為讓行號對上而改白名單。
- **不得 commit／push**（主大腦驗收後執行）。

## 6. 回報格式

1. 改動檔案清單。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：7 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
4. **排程掛點說明**：掛在哪裡、為何能在無對話流量時觸發、與訊息路徑如何避免重複觸發（附實測證據）。
5. **append-only 承載的併寫實測**：兩執行者併寫 N 筆，末筆與全部筆數的驗證數字。
6. 生產 sha256 前後對帳（零行為變化）。
7. 是否有踩到 Frozen Contract／意外行為；**本票未解決的殘餘問題**（例：pack 擴充仍未做 ⇒ 缺口路徑仍只覆蓋有 pack 的 agent）。
