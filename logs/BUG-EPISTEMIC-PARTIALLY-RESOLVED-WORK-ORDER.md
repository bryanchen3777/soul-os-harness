# 工單：BUG-EPISTEMIC-PARTIALLY-RESOLVED

> **開立**：主大腦（2026-10-23 19:5x EDT）
> **依據裁定**：Owner (Bry) 2026-10-23 19:2x「**第二步：前置修復【BUG-EPISTEMIC-PARTIALLY-RESOLVED】——在 Phase 3 接線前，拔除 `epistemic_action.py` 的 Fail-Open 隱患**」。
> **與 INCIDENT-GOAL-DB-LOCKED 的關係**：兩票**互不依賴**（不同檔、不同性質）⇒ **並行執行**；本票不得因另一票而停滯，也不得干擾另一票的勘查。
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**。

---

## 0. 缺陷（主大腦已定位並實測，可覆核）

**缺陷 A（fail-open 靜默吞噬）**　`src/inner_life/epistemic_action.py:772-774`
```python
status = event.get("status")
if status not in ("open", "resolved"):
    status = "resolved"          # ← 封閉集合外的值被**默默當成「已消解」**
```
⇒ 任何**未知** status（含新引入的 `partially_resolved`）都會被靜默改寫成 `resolved` ⇒ **未消解張力被宣告消解**。

**缺陷 B（未消解張力掉落佇列）**　`src/inner_life/epistemic_action.py:820-821`（`load_open_tensions`）
```python
for record in load_tension_records(agent_id, store_dir):
    if record.get("status") != "open":
        continue
```
⇒ `partially_resolved` **既不在未閉合佇列裡，也沒被誠實標示** ⇒ **靜默損失**。

**缺陷 C（寫回時降級）**　`save_tension_records`（:800）以 `record.get("status") == "resolved"` 判斷 ⇒ 若記錄的 status 是 `partially_resolved`，會被寫成 `open` 事件 ⇒ **部分消解狀態在往返中被降級**。

**為什麼現在必修**：`CAMPAIGN-B-P2`（Phase 2，`cc0b78c`）已在沙盒使用 `status="partially_resolved"`（事件 `kind="tension_resolved"`、payload 帶 `partial_resolution`／`remaining_gap_count`／`decided`／`reason`）。**Phase 3／戰役二一旦把這種事件寫進生產，缺陷 A 會把它靜默宣告為「已消解」，缺陷 B 會讓它從未閉合佇列消失** ⇒ 未消解張力永久蒸發。Owner 指示：**Phase 3 接線前先拔除**。

---

## 1. 做法（決策已定，執行者照做）

### 1.1 擴充狀態枚舉（封閉集合）
在 `src/inner_life/epistemic_action.py` 新增模組層常數（命名與既有 `TENSION_EVENT_KINDS`／`TENSION_EVENT_FIELDS` 風格一致）：
```python
#: 張力狀態（**封閉集合**：讀取時遇到集合外的值 ⇒ 不靜默視為已消解，保留原值並告警）。
TENSION_STATUS_OPEN = "open"
TENSION_STATUS_RESOLVED = "resolved"
TENSION_STATUS_PARTIALLY_RESOLVED = "partially_resolved"
TENSION_STATUSES: Tuple[str, ...] = (
    TENSION_STATUS_OPEN,
    TENSION_STATUS_RESOLVED,
    TENSION_STATUS_PARTIALLY_RESOLVED,
)
```
🔴 `TENSION_EVENT_KINDS`（事件**型別**的封閉集合）**不得**變更——Phase 2 的事件 `kind` 仍是既有的 `tension_resolved` ✔，本次只擴充**狀態**值。

### 1.2 缺陷 A 的修法（fail-closed）
`load_tension_records` 內：
```python
status = event.get("status")
if status not in TENSION_STATUSES:
    logger.warning(
        "[epistemic-action] 未知張力狀態 %r（tension_id=%s）⇒ 保留原值，**不視為已消解**",
        status, event.get("tension_id"),
    )
```
⇒ **保留原值**（不再覆寫成 `resolved`）＋ **告警**（可觀測）⇒ 不再靜默。
🔴 **不得**把未知值改成 `resolved`、**不得**改成 `open`、**不得**丟棄該筆記錄。

### 1.3 缺陷 B 的修法（未消解張量保留生命週期）
`load_open_tensions` 的篩選條件改為：
```python
if record.get("status") not in (TENSION_STATUS_OPEN, TENSION_STATUS_PARTIALLY_RESOLVED):
    continue
```
⇒ `partially_resolved` **留在未閉合佇列**（可被下一輪重新推演）✔。

### 1.4 缺陷 C 的修法（寫回不降級）
`save_tension_records` 內：把 `status` 映射為事件 kind/status 時，`partially_resolved` 必須**原樣保留**（事件 `kind="tension_resolved"`、`status="partially_resolved"`），**不得**被寫成 `open`。
🔴 `mark_tension_resolved()`（:839）語意**不變**（它本來就是「轉為完全消解」的路徑）✔。

### 1.5 其他約束
- **不得**改 `TENSION_EVENT_FIELDS`（事件欄位封閉形狀）、`TENSION_PAYLOAD_EXTRA_KEYS`、任何既有事件 kind 的語意。
- **不得**改 `epistemic_action` 的**其他**行為（分支展開、閉合判定、話頭入隊、SAGE 寫入）。
- **不得**在**本票**引入 `partially_resolved` 的**生產寫入路徑**（那是 Phase 3 的事）——本票只修**讀取端**的正確性。

---

## 2. 守衛測試（新增，**擴充既有檔**，不得新建 `tests/**` 下的 collectable `.py`）

擴充 `tests/inner_life/test_epistemic_action.py`（或既有最貼切的守衛檔），新增至少 **6 筆**，每筆都要有具名斷言訊息：

| 編號 | 測試 | 斷言 |
|---|---|---|
| P3-T1 | `partially_resolved` 進未閉合佇列 | `load_open_tensions()` **必須**包含 `status="partially_resolved"` 的張力 |
| P3-T2 | 讀取保留 `partially_resolved` | `load_tension_records()` 回傳的 `status` **必須**是 `partially_resolved`（**不得**被改成 `resolved`） |
| P3-T3 | 未知 status **不**被當成已消解 | 塞一個 `"bogus_status"` ⇒ 讀回的 status **必須**仍是 `"bogus_status"`，**且** `resolved_at` 不得被當成已消解語意；**且**必須產生 warning（用 `assertLogs` 或注入 spy 斷言） |
| P3-T4 | 寫回不降級 | `save_tension_records()` 往返一筆 `partially_resolved` ⇒ 重新讀回的 status 仍是 `partially_resolved`（**不得**變 `open`） |
| P3-T5 | 封閉集合常數 | `TENSION_STATUSES == ("open","resolved","partially_resolved")`；且 `TENSION_EVENT_KINDS` **不變**（既有 3 個） |
| P3-T6 | 既有語意零回歸 | `open` 與 `resolved` 兩種既有狀態的行為**逐字不變**（既有用例全綠） |

🔴 **既有測試不得刪除或弱化**；若因新語意必須調整既有期望值，**只可調整期望值並在 docstring 註明**，且必須逐條列出（工單回報格式第 6 項）。

---

## 3. 變異實驗（M1～M3，每條**必須轉紅**）

- harness **必須放 `%TEMP%`**（`tests/**` 禁 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`），且採「**改副本**、repo 檔全程不寫入」模式（2026-10-23 實測：in-place 還原會以 `OSError: [Errno 22]` 失敗，把 repo 檔留在變異狀態）⇒ 守衛測試的載入點覆寫**只接受 `%TEMP%` 底下的路徑**，其餘一律 `RuntimeError` 拒絕。
- 守尾必印「repo 檔 sha256 == 初始值」。

| # | 變異 | 應轉紅 |
|---|---|---|
| M1 | `load_open_tensions` 的條件改回只收 `"open"` | P3-T1 |
| M2 | `load_tension_records` 對集合外 status 改回 `status = "resolved"`（fail-open 復原） | P3-T3 |
| M3 | 從 `TENSION_STATUSES` 移除 `partially_resolved`（⇒ 它變成未知值 ⇒ 走 fail-open 分支） | P3-T2（＋ P3-T5） |

---

## 4. 邊界（硬性）

1. 🔴 **不得**改 `src/**` 以外的任何 `src/` 檔；`src/inner_life/epistemic_action.py` 是本票**唯一**可改的 `src/` 檔（它已在 `tests/test_observability_completeness_1.py` 的 `allowed` 集合內 ⇒ **不需要**更新 `allowed`；`frozen_dirs` 11 筆**不得**動）。
2. 🔴 **不得**接任何排程器／handler／`src/llm/proxy.py`；**不得**新增生產寫入路徑；**不得**觸發 synthetic diary/dream。
3. 🔴 **不得**寫 `data/**`、**不得**重啟服務、**不得**殺任何行程（禁 Stop-Process／taskkill／pkill）。
4. 🔴 **不得跑全庫回歸**——本票的全庫回歸由**主大腦**在合入前執行（避免與生產搶資源、也避免與 INCIDENT-GOAL-DB-LOCKED 勘查並行）。你只跑**受影響的守衛測試檔**。
5. 🔴 **今晚 22:00 EDT 夜槽不得被擾動**：21:45 EDT 起停止任何長時間操作。
6. 變異 harness 必須放 `%TEMP%`（見 §3）。
7. 不得 `git add`／`git commit`／`git push`（由主大腦處理）。
8. 測試數字一律用 repo 的 `.venv\Scripts\python.exe`。
9. 合成 fixture 必須與生產事件**同形**（欄位齊備，含 `ts`）——2026-10-23 實測：新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺。

---

## 5. 驗收（完成的定義）

1. **P3-T1～P3-T6 ＋ 既有守衛全綠**（`.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_epistemic_action.py`；回報須寫明 collected 數與解譯器路徑）。
2. **M1～M3 全部轉紅** ＋ repo 檔 sha256 逐位元相同。
3. `git diff --name-only HEAD` **恰好**等於：`src/inner_life/epistemic_action.py` ＋ `tests/inner_life/test_epistemic_action.py`（若你改的是別的守衛檔，以你回報為準，但**不得新建 `tests/**` 下的 collectable `.py`**）。
4. `data/**` **零改動**（位元組對帳）。
5. `frozen_dirs` 11 筆**零觸碰**；`allowed` **不需**變更（若你發現需要變更，**停下回報**）。

---

## 6. 回報格式

1. **缺陷 A/B/C 各自的修法摘要**（一行一項）。
2. **守衛測試**：P3-T1～P3-T6 ＋ 既有各打了幾筆、紅幾筆。
3. **變異**：M1～M3 逐條 →（轉紅的測試名）／repo 檔 sha256 是否逐位元相同。
4. **既有測試的期望值調整清單**（逐條；沒有就寫「無」）。
5. **Frozen 檢查**：`git diff --name-only HEAD` 完整輸出 ＋ `data/**` 位元組對帳結果。
6. **未做與明文排除**（含「未合 main、未部署、未重啟、未跑全庫回歸」的自證）。
7. **未解決疑點 / 需 Owner 裁示的項目**。

---

## 7. 給執行者的一句提醒

**這個缺陷的危險之處正是「它看起來是對的」**：fail-open 的預設讓 `partially_resolved` 被宣告成「已消解」，**沒有任何錯誤、沒有任何警告** ⇒ 這正是本 repo 反覆吃過的坑（守衛測試要能抓到、變異要能轉紅、報告要誠實揭露）。**不要只寫「讀取端已支援 partially_resolved」就交差 —— 必須同時證明「未知 status 不再被靜默」與「寫回不降級」。**
