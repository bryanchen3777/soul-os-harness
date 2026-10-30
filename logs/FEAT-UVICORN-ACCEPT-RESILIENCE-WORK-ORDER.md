# 工單：FEAT-UVICORN-ACCEPT-RESILIENCE

> 狀態：**已立項（Owner 2026-10-29 23:20 EDT 裁定）** — 代碼先行、驗收封裝、**待下一個維護窗口重啟生效**。
> 本工單**不含**任何生產重啟動作；重啟由主大腦在維護窗口統一執行。

## 目標

在 Windows 上讓 Uvicorn 的 accept 路徑對**良性網路噪聲**具韌性：
`WinError 64` / `WinError 10054` 發生時，**不關閉監聽 socket、不終止 accept 迴圈**，
改為記錄後**重新 arm accept**，讓服務保持可服務狀態。

目標狀態：服務不再因一次 accept 噪聲就被 watchdog 判定 `port_listen=False` 而**粗暴重啟**，
從而**不再斬斷執行中的排程槽**（10-29 夜槽即因此遺失 38 筆生命週期評估 ＋ 25 筆接縫）。

## 背景（主大腦已實測並讀過 CPython 原始碼，**勿重猜**）

### 1. 生產證據（5/5 樣本）
每個行程發生**恰好 1 次** accept 失敗，且時點落在**該行程生命的最後一刻**：

| # | accept 失敗時刻 | 後續 watchdog 重啟 |
|---|---|---|
| 1 | 10-28 20:26:51 | 10-28 20:28:07 |
| 2 | 10-29 14:57:22 | 10-29 14:58:04 |
| 3 | 10-29 20:29:19 | 10-29 20:33:03 |
| 4 | 10-29 22:01:23 | 10-29 22:03:03 |
| 5 | 10-29 23:02:32 | 10-29 23:03:03 |

- 斷掉的槽：10-29 **22:00 夜槽**（生命週期評估 38 筆未落地、接縫 25 筆遺失）。
- 每一次重啟路徑一致：`port_listen=False` → `killed PID` → `port 8000 released` → `launched Plan A`。

### 2. 根因（CPython 3.11.15 原始碼已讀；執行者不必再重現）
```
asyncio/windows_events.py:590  IocpProactor.accept()
  → ov.AcceptEx(...)  →  windows_events.py:596 finish_accept() → ov.getresult()
  → 拋 OSError: [WinError 64] 指定的網路名稱無法使用。
```
該例外被 **asyncio 自己 catch**，落到 `asyncio/proactor_events.py:861`：
```python
except OSError as exc:
    if sock.fileno() != -1:
        self.call_exception_handler({
            'message': 'Accept failed on a socket',
            'exception': exc,
            'socket': trsock.TransportSocket(sock),
        })
        sock.close()            # ← 真兇：asyncio 主動關掉「監聽 socket」
    elif self._debug:
        logger.debug("Accept failed on socket %r", sock, exc_info=True)
except exceptions.CancelledError:
    sock.close()
else:
    self._accept_futures[sock.fileno()] = f
    f.add_done_callback(loop)   # ← 只有 else 分支會「重新 arm accept」
```

**兩個決定性事實**：
1. **不是**「accept task 退出」而已 —— asyncio 會 **`sock.close()`**，把監聽 socket 從 OS 的
   LISTEN 清單中移除 ⇒ watchdog 的 `Get-NetTCPConnection -LocalPort 8000 -State Listen`
   查不到 ⇒ `port_listen=False` ⇒ 觸發重啟。
2. **`except OSError` 分支不重新 arm accept**（沒有 `add_done_callback(loop)`）⇒ 就算沒關閉 socket，
   accept 迴圈也不會再接新連線。

### 3. 🔴 機制更正（主大腦已查證，**直接影響實作方式**）
Owner 指示的「Windows 專屬 Exception Handler」**若只用 `loop.set_exception_handler` 實作則無效**：
例外是被 asyncio **內部 catch** 的，loop 的 exception handler 只影響**日誌輸出**，
`sock.close()` 照樣發生、accept 迴圈照樣不會重新 arm。

⇒ **本工單把「Exception Handler」定義為：accept 路徑上的例外攔截 ＋ 重新 arm**，
這才真的能達成 Owner 寫定的目標「杜絕 Accept Loop 退出、服務不失聰」。

### 4. 已排除的替代方案（**勿再提案**）
| 方案 | 排除理由（已實測/已讀碼） |
|---|---|
| 改用 Selector event loop | uvicorn 0.49 `loops/asyncio.py:9` 在 win32 且 `use_subprocess=False` **直接回傳 `asyncio.ProactorEventLoop`**；且 Windows 的 `SelectorEventLoop` **同樣使用 `IocpProactor`** ⇒ 換 loop 類別**換不掉**這條 accept 路徑。 |
| 只掛 `loop.set_exception_handler` | 見上，只改日誌，`sock.close()` 照跑。 |
| 複製上游 `_start_serving` 私有實作 | 私有 API 逐行複製 ＝ 版本耦合與漂移風險（上游一改就默默失效）。 |
| 縮短 watchdog 探測間隔 | 屬**另一層**（Owner 尚未裁示），且不解決 accept 迴圈已死的問題。 |

### 5. 已確認的接縫覆蓋範圍
- `asyncio.windows_events.IocpProactor` 是 Windows 上 **Proactor 與 Selector 兩種 loop 共同的 accept 實作**。
- uvicorn 0.49 於 win32 預設走 `asyncio.ProactorEventLoop`（`loops/asyncio.py`）。
⇒ **單一接縫即可全覆盖**，且**不需要**動 repo 既有的 event loop policy 偏好機制。

## 範圍（只動這些檔案）

- **新增** `src/io/accept_resilience.py` —— 核心：可單元測試的純邏輯 ＋ 安裝函式。
- **修改** `scripts/run_server.py` —— **僅** `__main__` 區塊：在 `uvicorn.run(...)` **之前**加 1 個 import ＋ 1 個呼叫。
- **新增** `tests/test_io_accept_resilience.py` —— 守衛（與既有 `tests/test_io_gateway.py` 同層級）。
- **不得**動 `scripts/server_ops.ps1`、`scripts/_watchdog.ps1`、`scripts/eval_lifecycle_cli.py`、`src/soul/**`。

## 做法（決策已定，執行者照做）

### 1. 接縫（seam）：`asyncio.windows_events.IocpProactor.accept`

補丁打在 **class-level 的 `IocpProactor.accept`**（回傳 `(conn, addr)` 的 Task），
**不是** `BaseProactorEventLoop._start_serving`。

理由：`_start_serving` 的 closure 不可從外部攔截；而 `accept` 是 accept 路徑唯一入口，
在其**內層**攔截失敗並重試，**完全不動**上游 `_start_serving` 的邏輯
⇒ 上游 `except OSError → sock.close()` 分支**永遠不會被觸發**（因為例外在內層就被攔下、不外拋）。

### 2. 新語意（逐條照做）

`install_accept_resilience()` 必須滿足：

- **(a) 只攔截 Windows 良性碼**：`OSError` 且 `winerror ∈ {64, 10054}`。
  其他任何 `OSError`（含 `winerror=10048` 綁埠衝突、`10013` 權限）**必須原樣 raise**，不得吞掉。
- **(b) 良性失敗時**：不關閉 listener socket、**不讓外層 future 進入例外狀態**；
  記 WARNING ＋ **重新 arm accept**（對同一 listener socket 重試）。
- **(c) 退避**：起始 `0.05s`、每次 ×2、**上限 `1.0s`** ⇒ 不得 busy-spin，也不得無限增長。
- **(d) 冪等**：重複 install 不得疊加 wrapper（第二次為 no-op，回 False 並記 INFO）。
- **(e) 非 Windows**：no-op（回 False、不 patch 任何類別、記 INFO）。
- **(f) 版本守門（fail-closed）**：安裝前檢查 `asyncio.windows_events.IocpProactor` 存在，
  且其 `accept` 的原始碼含 `AcceptEx`；**不符即拒絕安裝**（記 ERROR、**不得**默默套用）。
  ⇒ 上游形狀改變時「寧可失效，也不要錯誤行為」。
- **(g) 觀測可驗屍**：每次良性失敗記 WARNING，必須含 `winerror`、`backoff`、累計連續失敗次數。
  ⇒ 維護窗口重啟後可直接從 `data\server_nohup*.err` 數出「發生過幾次、服務是否存活下來」。

### 3. 必須可單元測試（**不得**綁真實 socket）

核心拆成**可注入依賴**的純函式（**簽名已定，照做**）：

```python
BENIGN_ACCEPT_WINERRORS = frozenset({64, 10054})

def is_benign_accept_error(exc: BaseException) -> bool:
    """僅 True 當 exc 是 OSError 且 winerror ∈ BENIGN_ACCEPT_WINERRORS。"""

def make_resilient_accept(
    *,
    accept_impl,                       # (listener) -> Awaitable[tuple]  （原 accept）
    is_benign=is_benign_accept_error,
    sleep=asyncio.sleep,               # 可注入的退避睡眠
    initial_backoff=0.05,
    max_backoff=1.0,
    on_benign=None,                    # (exc, backoff, consecutive) -> None（觀測用）
) -> Callable[[Any], Awaitable[tuple]]:
    """
    回傳 accept wrapper：
      - 呼叫 accept_impl(listener) 並 await；
      - 良性失敗 → sleep(backoff) → 重試（backoff ×2 成長，封頂 max_backoff）；
      - 非良性失敗 → 原樣 raise（不得吞）。
    """
```

⇒ 守門可用 **fake `accept_impl`**（回傳可控 Task）＋ **fake `sleep`**（記錄退避序列）
在**完全無網路、無真實 socket**的情況下驗證全部語意。

### 4. 接線（`scripts/run_server.py`）

- 在 `__main__` 區塊、**`uvicorn.run(...)` 之前**呼叫 `install_accept_resilience()`。
- 🔴 **模組層級不得有任何 patch 副作用**（非 `__main__` 匯入時必須乾淨 ⇒ 既有
  `tests/test_crash_obs3_faulthandler_enable.py` 以 `exec` 匯入本檔，守衛不得轉紅）。
- 🔴 **不得**改動 `uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="info")`
  的任何參數、順序或呼叫位置。
- 🔴 **不得**改動既有 event loop policy 機制（`_resolve_event_loop` / `_apply_event_loop_policy` / CRASH-F1）。

## 驗收（完成的定義）

- [ ] **G1 良性判定**：`winerror=64` → True；`winerror=10054` → True；
      `winerror=10048` / `10013` / 其他 → False；**無 `winerror` 的 `OSError` → False**。
- [ ] **G2 良性失敗不殺迴圈**：fake `accept_impl` 第一次拋良性錯、第二次成功
      ⇒ 外層**不 raise**、listener **未被關閉**、`accept_impl` **被呼叫 ≥ 2 次**。
- [ ] **G3 退避有上限**：連續失敗 N 次的 `sleep` 序列為 `0.05, 0.1, 0.2, …` 並**封頂於 1.0**。
- [ ] **G4 非良性必須原樣 raise**（不得被吞）⇒ 這是「不放寬判準」方向的守門。
- [ ] **G5 冪等**：連續 install 兩次 ⇒ 第二次 no-op，wrapper **只疊一次**。
- [ ] **G6 版本守門 fail-closed**：`IocpProactor.accept` 被換成不含 `AcceptEx` 的假實作
      ⇒ install **拒絕安裝**，且該類別行為**未被改變**。
- [ ] **G7 非 Windows no-op**：模擬非 win32 ⇒ install 回 False 且**未 patch 任何類別**。
- [ ] **G8 接線不變性**：`scripts/run_server.py` 的 `uvicorn.run(...)` 呼叫與參數**逐位元不變**，
      且 `install_accept_resilience()` 呼叫點**在 `uvicorn.run` 之前**（AST 驗證）。
- [ ] **G9 既有護欄零轉紅**：`tests/infra/`、`tests/integration/`、`tests/test_crash_obs3_faulthandler_enable.py`、
      `tests/test_self_check_v2.py` 全綠；全庫回歸 **0 failed**。
- [ ] **G10 守門三件套**：母體完整性（守門確實掃到目標）＋ 違規為空 ＋ **合成樣本會紅**（見變異）。

## 測試（雙向變異，**每條必須轉紅**）

變異必須在 **%TEMP% 影子副本／記憶體**進行（**repo 檔全程唯讀**），
且被解除防護的程式碼**不可能真的執行到生產**（只能在 fake 依賴下被觸發）。

| # | 變異 | 必須轉紅的守門 |
|---|---|---|
| M1 | 把良性分支改回「原樣 raise（不重試）」 | G2 |
| M2 | 拿掉「重新 arm」（重試一次即放棄） | G2 |
| M3 | 拿掉退避上限（backoff 無限增長） | G3 |
| M4 | 把良性判定放寬成「所有 OSError 都忽略」 | **G4**（證明沒有放寬判準） |
| M5 | 從白名單拿掉 10054 | G1 |
| M6 | 拿掉版本守門（形狀不符仍照套） | G6 |
| M7 | 拿掉冪等保護（重複 install 疊 wrapper） | G5 |

- 🔴 **不得**只靠「它應該會失敗」的預測 —— 必須貼出**實際轉紅 output**。
- 🔴 **不得**開真實監聽 socket、**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`、**不得**對生產埠發真實請求。
- 🔴 **不得**啟動伺服器、**不得**殺任何行程（不得 Stop-Process／taskkill／pkill／task_stop）。
- 🔴 **不得**跑 `scripts/manual_*.py`。

## 測試（回歸）

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_io_accept_resilience.py
.venv\Scripts\python.exe -m pytest -q tests/infra tests/integration
.venv\Scripts\python.exe -m pytest -q tests          # 全庫
```
- **必須**用 repo 內 `.venv\Scripts\python.exe`（PATH 上的全域 pytest 是另一個世界）。
- 全庫回歸要求：**0 failed**、**skip 集合零漂移**（added=0／removed=0）、並**聲明解譯器**。

## 不做（Out of Scope）

- **不得**改 watchdog（`scripts/_watchdog.ps1`）的探測間隔或判定邏輯（另一層，Owner 尚未裁示）。
- **不得**改 `scripts/server_ops.ps1` 的啟動方式。
- **不得**改動 uvicorn 套件本身（不 sitecustomize、不改 `.venv` 內任何檔案）。
- **不得**在本工單重啟生產服務 ⇒ 重啟由主大腦在**下一個維護窗口**統一執行。
- **不得**改 event loop policy 的既有偏好機制。
- **不得**新增任何第三方依賴（只用 stdlib ＋ 既有 uvicorn／asyncio）。
- **不得**寫入或改寫 `data/` 下任何檔案。

## Frozen Contract 注意

- `uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="info")` 的參數與位置。
- event loop policy 偏好機制（`_resolve_event_loop` / `_apply_event_loop_policy`，CRASH-F1）。
- `src.paths.data_root()` 的環境變數語意。
- AGENCY 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯。

## 維護窗口重啟指引（**主大腦**執行，非本工單範圍）

1. 重啟走官方 `.\scripts\server_ops.ps1 restart`（**須 Owner 明示授權**；主大腦不得自行對生產行程動手）。
2. 重啟後驗收：
   - ① `data\server_nohup.err` 出現 accept-resilience 安裝 INFO；
   - ② `netstat -ano | findstr :8000` 仍為 LISTEN；
   - ③ `data\server.pid` 與 listener PID 一致。
3. 🔴 **重啟後 `_ELEVATION_EMITTED` 清零** ⇒ 下一槽接縫**全量重發**（預期 ≈78 筆）。
   這是既有已知行為，**非本工單缺陷**，但必須在重啟後的對帳中依此設定期望值。

## 回報格式

1. 改動檔案清單（逐檔一句話說明改什麼）。
2. G1～G10 逐條：**守衛全綠 output 摘要**。
3. M1～M7 逐條：**變異轉紅 output**（改什麼 ⇒ 具體轉紅證據）。
4. 相鄰守衛與全庫回歸結果（**聲明解譯器為 repo 內 `.venv`**、collected / passed / failed / skipped、skip 集合漂移）。
5. 是否踩到 Frozen Contract（逐項確認）。
6. 🔴 **明確聲明**：本工單**未**觸碰 `data/` 下任何檔案、**未**重啟服務、**未**殺任何行程、
   **未** commit／push、**未**跑 `scripts/manual_*.py`、**未**開任何真實監聽 socket。
