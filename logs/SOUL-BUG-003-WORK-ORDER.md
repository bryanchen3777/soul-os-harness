# SOUL-BUG-003 — `/inject/tick` 端點契約已撤銷但 E2E 測試仍期待 `agent_speak`

**工單編號**：SOUL-BUG-003
**嚴重度**：**P3（架構清潔，非行為缺陷）**（2026-10-14 由 Bry 自 P2 下調）
**狀態**：**部分結案**（Bry 2026-10-14 授權本輪修復 E2E 契約斷言；`:171` 死碼與端點除役**仍未處理**）
**發現方式**：SOUL-TECH-DEBT-001 B 類 skipif 授權下的**拒絕跳過**（執行者判定為真缺陷而非環境耦合）
**涉及測試**：`tests/test_websocket_e2e.py::test_inject_tick_does_not_trigger_agent_speak`
（**已改寫**；原名 `test_inject_tick_triggers_agent_speak`）
**相關決策**：M5.7-2（`95891e8`）constraint M

> ## ✅ 已結案部分：E2E 測試改寫為負向契約斷言（Bry 2026-10-14 授權）
>
> **處置路線**：採 §七 的 **A′**，且 Bry 本輪明確指定「**保留端點**、只改測試」——
> 即端點除役（改 `src/io/gateway.py`）**不在本輪範圍**，`:171` 死碼亦**不在本輪範圍**。
>
> **改寫內容**（`tests/test_websocket_e2e.py`）：
> 1. 函式更名 `test_inject_tick_triggers_agent_speak` → `test_inject_tick_does_not_trigger_agent_speak`
>    （**名稱如實反映副作用**，沿用 SOUL-BUG-002 命名紀律）。
> 2. docstring 寫明 M5.7-2 契約變更歷史與 constraint M 出處。
> 3. 斷言由「正向等 speak」翻轉為**負向契約**：HTTP 200 + `injected is True`
>    + 觀察窗內 **0 筆非空 `agent_speak`**。
> 4. 移除原本的 3 次 retry（原測試等不到 speak 時重試，語意上已無意義）。
>
> **為何保留端點而不廢除**：端點本身是**防呆閘門**——哪天有人不小心把
> `consciousness.py:155` 的 SYSTEM_TICK 訂閱接回去，這支測試必須立刻轉紅。
> 保留端點 + 斷言「不產生 speak」＝ 把 M5.7-2 的架構約束釘成可執行契約。
>
> **變異實驗（綠燈不算證據）**：`MUT-A3` 在觀察窗前插一次 `/debug/broadcast`
> （會真的產生非空 speak）⇒ **轉紅 ✅**，證明 WS 收集迴圈與斷言皆非恆真式。
>
> **殘餘 flake 風險（如實揭露）**：isolated server 會起 scheduler
> （`scripts/run_server.py:1303`，`proactive_agents=["agent_ruka"]`），
> `_fire_heartbeat` / `_fire_proactive_dm` 為 30–60 分 / 2–4 小時隨機觸發。
> 本測試觀察窗僅 10 秒 ⇒ 誤觸發機率約 1%。此風險**與原正向測試同源**，
> 非本次改寫引入。
>
> **仍未結案**：`consciousness.py:171` 不可達死碼；`/inject/tick` 端點的最終處置
> （保留／標 deprecated／除役）仍待 Bry 裁示。

> ## ⬇️ 優先級下調至 P3（Bry 裁示 2026-10-14）
>
> **理由：SOUL-BUG-004-SINK（`f649224`）已讓本工單失去行為影響力。**
>
> `chrono_context` 已改為在 `_fire_intent` 產生發言意圖的當下 **just-in-time 現算**，
> 時間感知**徹底擺脫對 `SYSTEM_TICK` 廣播的依賴**。因此：
> - 本工單不再是「時間感知失效」的成因，而是**純粹的架構清潔**問題
>   （端點契約漂移 + `:171` 死碼）。
> - **當前保持現狀完全不影響生產運行**，不急於動手。
>
> **後續方向**：等未來整理對外 HTTP／WebSocket 測試矩陣時，
> 再從容決定「廢除該端點」或「重寫測試以符合現行契約」，並一併清理 `:171` 死碼。
>
> 路由已收斂為 **A′**（見 §七）：端點除役或標 deprecated ＋ E2E 測試改寫為契約斷言。

---

## 一、現象

`/inject/tick` 發佈 `SYSTEM_TICK` 事件，但 **`agent_speak` 永遠不會到來**。

已排除的其他可能（執行者實測）：
- 服務啟動正常 ✅
- WebSocket 連線正常，**ping 有收到** ✅
- 僅缺 `agent_speak` ❌

## 二、根因

**`src/` 中沒有任何元件訂閱 `SYSTEM_TICK`：**

- `src/agent/consciousness.py:155` —— 消費邏輯**被註解掉**（M5.7-2，`95891e8`，constraint M）
- `src/agent/consciousness.py:171` —— **不可達死碼**

⇒ 該端點的契約已在生產中被**刻意撤銷**，但 `tests/test_websocket_e2e.py` 仍以舊契約撰寫。**此測試在任何配備完整的機器上都會失敗**，因此不得用 `skipif` 掩蓋。

## 三、為何不跳過（決策紀錄）

SOUL-TECH-DEBT-001 曾授權對 B 類加 `skipif`。執行者**主動拒絕**該測試的跳過授權，理由是：環境耦合可在配備完整的機器上轉綠，而此測試不會——它反映的是**契約與實作的背離**，屬架構層問題，需要 Owner 裁示而非環境開關。

此判斷經主大腦複核採納。

## 四、待裁示的核心問題（Bry 2026-10-12）

**該端點的現狀意圖為何？**兩條處置路徑方向相反：

| 路線 | 前提 | 處置 |
|---|---|---|
| **A：正式除役** | M5.7-2 的決策是長期的，端點已確定廢棄 | 正式移除端點（或標記 deprecated）+ 移除／重寫該 E2E 測試 |
| **B：恢復接線** | `consciousness.py:155` 的註解是**誤傷**，非刻意決策 | 恢復 `SYSTEM_TICK` 消費邏輯 + 清除 `:171` 死碼 |

**本工單在 Bry 拍板前不做任何實作**——兩條路會導向完全相反的程式碼結果，不可由執行層自行決定。

## 五、需一併釐清的附帶問題

`consciousness.py:171` 的不可達死碼是**獨立的衛生問題**，無論 A 或 B 都應處理——它要么該刪、要么該接上。建議不論主路線如何選定，都把死碼清理列為獨立子項。

## 六、驗收（依裁示方向而定）

- 路線 A：端點不存在或明確標記 deprecated；對應 E2E 測試移除或改寫為契約斷言；`:171` 死碼清除。
- 路線 B：`/inject/tick` 觸發 `agent_speak`；E2E 測試通過；`:171` 死碼清除。
- 兩條路線皆需：全庫回歸通過。

## 七、🔗 與 SOUL-BUG-004 的連動（Bry 2026-10-13 裁示後收斂）

本工單與 [SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md) **共享同一個根因位置**
（`src/agent/consciousness.py:155` 的 SYSTEM_TICK 註解）。

Bry 在 004 工單已明確裁示：

> **不要為了拿時間而把 SYSTEM_TICK 訂閱整個打開**——那會把 M5.7-2 想解決的問題帶回來。

⇒ 這**等同於已排除路線 B 的「直接恢復訂閱」版本**：時間感知必須走替代注入管道
（scheduler / heartbeat 主動注入），而不是重新訂閱 SYSTEM_TICK。

因此 §四 的兩條路線，收斂為：

- **A′（現行唯一相容路線）**：端點正式除役或標記 deprecated；
  E2E 測試改寫為「`/inject/tick` 不應再觸發 `agent_speak`」的**契約斷言**（負→正升級）；
  `:171` 死碼刪除。
- **仍待 Bry 確認的唯一一點**：`/inject/tick` 端點本身是否要從 `scripts/run_server.py` 移除，
  抑或保留但明標 deprecated（除供診斷注入 SYSTEM_TICK 外無其他作用）。

⚠️ 004 的子步「接通源端」若改採 scheduler 注入，**不應順帶復活本工單的端點契約**——
兩件事在程式碼上無交集，僅在設計意圖上有關。