# CRASH ROOT CAUSE — 2026-09-08 access violation

> 診斷日期：2026-09-08（主大腦 DSH 直接診斷，0 程式碼改動）
> 事件：生產伺服器 2026-09-08 02:28 崩潰，watchdog 重啟 10 次全失敗鎖死，伺服器宕機約 14 小時。

---

## ⚠️ 更正（2026-09-13）

> **本區塊由工單 CRASH-F1-DOCFIX 於 2026-09-13 追加。原文（含全部原始整理與日誌摘錄）一律保留於下方，未刪除。**

### 被更正的結論

本文件的「崩潰點 ＝ `_overlapped.GetQueuedCompletionStatus`（IOCP／C 擴充套件層）」**正式降級為「未證實的執行緒快照解讀」**，不再作為根因主張。

### 三點理由（直接採信 `docs/CRASH-F1-SYMBOLS-1.md` 的符號化結論）

1. **全樣本普查：`.pyd` 作為 faulting module 的次數為 0。**
   45 天、245 次崩潰的**全樣本**中，`_overlapped.pyd` / `_asyncio.pyd` / `select.pyd` / `_socket.pyd` 作為 **faulting module 各 0 次**。唯一一次 `.pyd` 事件來自**另一條 27 模組的小進程**，與主服務崩潰無關。11 個 fault offset **全部**落在 `python311.dll` 的三個熱區內。
   → 因此「C 擴充套件層／IOCP 完成埠損壞」在全樣本層級**無任何一次支持**。

2. **09-08 那次與其後所有崩潰是同一個根因。**
   本文件所依據的 09-08 崩潰位址 **`0xB5C07`**，與 CRASH-F1 dump 的崩潰位址 **`0xB5BDA`** 只差 **45 bytes**，同屬**熱區 B**。而熱區 B 已符號化為 **`PyCode_Addr2Line`（`0xB5BCC`）＋ `advance`（`0xB5F00`）**——即 faulthandler 傾印器的走訪路徑。
   → 09-08 那次不是「另一個 IOCP 根因」，而是**同一個根因（faulthandler 傾印器自身）**的早期實例。

3. **faulthandler 不知道自己哪條執行緒觸發 AV。**
   `faulthandler` 的行為是「崩潰時傾印**全部**執行緒」。它輸出的是一份**執行緒快照**，**不標示**哪條執行緒觸發了 access violation，也不保證被引用為「崩潰執行緒」的那條就是觸發者。CRASH-F1 dump 的 `ExceptionStream` 已直接證明：真正的崩潰執行緒是 **`faulthandler_thread` 自己**（TID 13096）。
   → 本文件把 `faulthandler.log` 的**某條執行緒**（uvicorn 主事件迴圈）當成崩潰執行緒，**是快照解讀，不是崩潰歸屬**。

### 仍然有效的貢獻（保留）

本文件對 `faulthandler.log` / `watchdog.log` / `server_nohup.err` / ptb 原始碼的**整理與摘錄本身仍然有效**，後續工單仍可直接引用：

- 30+ 條執行緒同時卡在 Telegram `getUpdates` 鏈的**觀測**（本身是事實）。
- 9/6–9/8 watchdog 反覆 `port_listen=False procs=0 -> restart` ＋ `launched Plan A` 的**日誌摘錄**（本身是事實）。
- 「watchdog 誤判 → 雙實例 → Telegram 409 重試風暴」的**機制敘述**：**未被推翻，但也未被證實**，且**不是** CRASH-F1 系列崩潰（`0xA0` 存取）的成因——兩者是**分開的**問題，不應再混為一談。

### 權威依據

- `docs/CRASH-F1-SYMBOLS-1.md` —— `python311.pdb` 符號化（**GUID/Age 雙側相符、`PdbUnmatched = 0`**）：11 個 fault offset → 具名函式；崩潰執行緒 9 框架完整具名回溯；`mov rcx,[rcx+0xA0]`（`RCX = 0`）↔ `PyCodeObject._co_linearray @ +0xA0` ↔ `ExceptionInformation[1] = 0xA0` 的指令層閉合。
- 修復已於 **`ed4169a`** 上線並重啟（移除三處 `dump_traceback_later` ＋ marker 機制；`_heartbeat_dumper` 改 `all_threads=False`）。

---

## 一句話根因

**watchdog 反覆誤判「伺服器已死」→ 反覆 Plan A 啟動新實例 → 雙實例短暫共存 → 兩個進程的 10 個 Telegram bot 同時 getUpdates 同一批 token → 409 Conflict 重試風暴 → 30+ 執行緒並發 httpx 請求 → httpcore 連線池 socket 損壞 → Windows IOCP 完成埠損壞 → `GetQueuedCompletionStatus` access violation。**

這**不是**歷史已知的 sqlite 並發問題（RLock 已修），而是 **watchdog 誤判 + 雙實例 + Telegram getUpdates 並發** 的連鎖崩潰。

---

## 證據鏈

### 1. 崩潰點：Windows IOCP 完成埠輪詢（C 擴充套件層）

> ⚠️ **本段結論已於 2026-09-13 被推翻，見文首更正。** 全樣本普查中 `_overlapped.pyd` 等作為 faulting module **各 0 次**；09-08 的 `0xB5C07` 與 CRASH-F1 的 `0xB5BDA` 同屬熱區 B（`PyCode_Addr2Line`／`advance`）→ 同一個根因。以下為**未證實的執行緒快照解讀**，原文保留。

`data/faulthandler.log` 崩潰執行緒 `0x0000589c`（uvicorn 主事件迴圈）：

```
asyncio/windows_events.py:825 in _poll
  → _overlapped.GetQueuedCompletionStatus(self._iocp, ms)   ← access violation 發生點
  → asyncio base_events run_forever
  → uvicorn server run
  → run_server.py:1465
```

`GetQueuedCompletionStatus` 是 Windows IOCP 完成埠的 C 層輪詢。access violation 在此 = **IOCP 完成埠或關聯的 overlapped 結構已被損壞**（典型 use-after-free / 跨執行緒操作）。

### 2. 並發來源：30+ 執行緒同時做 Telegram getUpdates

`faulthandler.log` 中 **30+ 個執行緒**（0x000046f4, 0x00001720, 0x00004790, 0x00002df8, 0x000020ac, 0x00004340, 0x00001f74, 0x00004f30, 0x00005e04, 0x000059cc, 0x00001dbc, 0x00005f70, 0x00005484, 0x000013e8, 0x00002334, 0x000038f0, 0x000024b0, 0x000044f8, 0x00004f24, 0x00004ba4, 0x00002d14, 0x00001e74, 0x00006134, 0x0000509c, 0x000043e4, 0x000064b4…）**全部卡在同一條鏈**：

```
telegram/ext/_updater.py:340 polling_action_cb
  → _extbot.py:680 get_updates
    → _bot.py:4767 get_updates
      → _bot.py:712 _post → _do_post
        → _baserequest.py:198 post → _request_wrapper
          → _httpxrequest.py:279 do_request
            → httpcore connection_pool handle_async_request
              → socket.py:519 family / ssl.py:930 write   ← 崩潰點
```

10 個 bot 正常只有 10 個 polling 執行緒。**30+ 個 = 多實例（≥2 進程 × 10 bot）+ 409 重試風暴**。

### 3. 觸發因素：watchdog 反覆誤判重啟（9/6-9/8）

`watchdog.log` 顯示 9/6-9/8 反覆 `port_listen=False procs=0 -> restart` + `launched Plan A (PID xxx)`：

```
[2026-09-06 05:03] post-e22374c N=1/10 port_listen=False procs=0 -> restart → PID 26188
[2026-09-06 06:03] post-e22374c N=2/10 ... -> restart → PID 18156
[2026-09-06 06:18] post-e22374c N=3/10 ... -> restart → PID 8120
[2026-09-06 09:08] post-e22374c N=4/10 ... -> restart → PID 640
[2026-09-06 12:03] post-cbe877e N=1/10 ... -> restart → PID 21852
[2026-09-06 16:18] post-b9a7f14 N=1/10 ... -> restart → PID 19808
[2026-09-06 16:48] post-723d577 N=1/10 ... -> restart → PID 3256
[2026-09-06 18:03] post-2d22bce N=1/10 ... -> restart → PID 9812
[2026-09-06 21:23] post-2d22bce N=2/10 ... -> restart → PID 24988
[2026-09-06 23:43] post-2d22bce N=3/10 ... -> restart
```

**每次 Plan A 啟動新實例時，若舊實例程序尚未完全退出（或 watchdog 誤判時舊實例其實還活著），就造成雙實例短暫共存**。兩個進程的 10 個 bot 同時 getUpdates 同一批 token → Telegram 回 409 Conflict → ptb `network_retry_loop` 重試 → 重試風暴。

### 4. 為何 RLock 修復（fb28b8c）後仍崩

8/18-8/25 的 access violation 被歸因 sqlite 並發並以 RLock 修復。但**真正的崩潰源一直是「watchdog 誤判 → 雙實例 → Telegram 並發」**——RLock 只修了 sqlite 那一條，httpx/socket 並發崩潰從未根除。9/8 再次觸發。

---

## 修復建議

| # | 建議 | 類型 | 說明 |
|---|------|------|------|
| A | **watchdog 啟動窗口保護** | additive | 伺服器啟動需 30s+（載入 10 bot），watchdog 在「啟動窗口」內不應判定崩潰。現況 `port_listen=False procs=0` 在啟動初期即觸發誤判。 |
| B | **Plan A 前殺程序樹 + 等 port 釋放** | additive | 啟動新實例前確保舊實例完全退出（先例：proactive-dm-dual-instance-fix 的 server_ops 殺程序樹）。杜絕雙實例窗口。 |
| C | **getUpdates 409 重試限制** | 需評估 | ptb `network_retry_loop` 對 409 Conflict 的重試可能過激，需限制重試次數/退避，避免風暴。 |
| D | **長期：httpx 連線池並發保護** | 需評估 | 或減少 polling 並發（如 bot 分組輪詢），從根上降低 socket 並發壓力。 |

**建議優先 A + B**（additive、直接消除雙實例根因），C/D 作為後續加固。

---

## 0 程式碼改動聲明

本診斷全程唯讀（讀 faulthandler.log / watchdog.log / server_nohup.err / ptb 原始碼 / telegram.py），未修改任何 src/ 程式碼，未 commit，未 push。
