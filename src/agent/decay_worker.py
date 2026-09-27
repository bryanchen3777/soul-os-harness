"""src/agent/decay_worker.py — INTIMACY-GROWTH-2 衰減 worker 的**惰性接縫**.

## 為什麼有這個模組

原本這組程式碼住在 `scripts/run_server.py`（`:453-588`）。搬出來的理由是
**可隔離驗證**：`scripts/run_server.py` 匯入時就會做真實檔案 I/O（faulthandler
開檔、`data_root()`、載入 configs…），而 `import emotion` 也會在匯入當下就
`mkdir` / `sqlite3.connect` / `CREATE TABLE` / `commit`（`src/agent/emotion.py:1190`
→ `:363` / `:364` / `:374-381` / `:398`）。把 worker 留在服務入口檔裡，等於
「要測 worker 就得先接受整條服務啟動路徑的副作用」。

本模組是**惰性接縫**：匯入它不得有任何副作用。

## 🔴 匯入時零副作用（硬契約，由 `tests/soul/test_decay_worker_import_inert.py`
##    以**全新子直譯器**證明）

模組層級**只允許**：

  - `import asyncio` / `logging` / `threading`
  - `_DECAY_WORKER_TASK = None`
  - `_DECAY_WORKER_CANCEL = threading.Event()`
  - def / class 定義

**不得**出現：`open()` / `data_root()` / `EmotionEngine()` / `load_dotenv()` /
`create_task` / 任何網路匯入 / 任何模組層級的 `from src.agent.emotion import ...`。

**評估器由呼叫端在「呼叫時」注入** —— 這是搬遷時刻意保留的既有惰性：
`run_server.py:486` 與 `:2001-2004` 原本就是**函式內**才 import emotion 的名字，
搬過來之後必須維持同樣的惰性，否則本模組一被 import 就會連帶 import
`src.agent.emotion`（連鎖觸發上述 DB 副作用）。

## 契約（搬遷時逐字保留，不得升級）

  - **CANCEL CONTRACT —— 不得升級。** `_shutdown_decay_worker()` 的順序是
    「先 set 旗標 → 讀 task → `None` 就早退 → 清參照 → `cancel()` 並吞掉
    `CancelledError`」。日誌字串**如實**（`"將於下一安全邊界停止"`）——
    **絕不**宣稱一個已在執行的 SQLite 呼叫被中斷。`asyncio.to_thread()` 底層是
    `run_in_executor`（`concurrent.futures` 執行緒），`task.cancel()` 只能取消
    `await` 點，**無法中斷已執行的同步函式**（Python 既有語意）。
  - **零參數同步評估器。** `_run_decay_evaluation_sync()` 維持零參數：既有測試
    以零參數 stub 替換它，改簽名會讓那些 stub 收到未預期的引數而使測試假紅。
  - **旗標以模組層級讀取。** 執行緒看見的必須是「當前」旗標，故不把旗標當參數
    傳進評估執行緒；`decay_evaluate_eligible_agents(should_stop=...)` 只收到
    `_DECAY_WORKER_CANCEL.is_set` 這個**回呼**（emotion.py 不反向依賴本模組）。
"""
from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger("soul_os.server")

#: 15s 迴圈內判斷「worker 是否空閒」的依據。預設 `None` ＝ 從未排入，
#: 即視為空閒。只在 **event loop 執行緒**上讀寫（loop body 與 done-callback），
#: 故不需要額外鎖。
#:
#: 🔴 測試注意（反空洞）：本模組搬到 `src.agent.decay_worker` 之後，
#: `monkeypatch.setattr(run_server_mod, "_DECAY_WORKER_TASK", ...)` 綁到的是
#: **沒有人讀的名字**，stub 會靜默變成 no-op、測試會**空洞地通過**。
#: 所有遷移過來的測試必須明確 re-target 到本模組。
_DECAY_WORKER_TASK = None

#: 🔴 **執行緒可見**的取消旗標（INTIMACY-GROWTH-2 執行邊界修正）。
#:
#: 為什麼需要它 —— 已證實的缺陷：`asyncio.to_thread()` 底層是
#: `run_in_executor` ⇒ `concurrent.futures` 執行緒。`task.cancel()` **只能取消
#: `await` 點**，**無法中斷已執行的同步函式**（Python 既有語意）。故只靠
#: `task.cancel()`，`_shutdown_decay_worker()` 回傳後執行緒仍在跑、仍可 commit。
#:
#: 語意：`set()` ＝「請在下一個安全邊界停止」。
#: 一旦 set，**本次 shutdown 內永不清除**（進程即將結束）；
#: `_maybe_schedule_decay_worker()` 以它作為「關閉中，不得再排」的閘門 ——
#: 這是必要的，因為 shutdown 後 `_DECAY_WORKER_TASK` 指向的 task 已 `done()`，
#: 單靠 task 參照的 guard **不再成立**（會允許排入第二個 worker）。
#:
#: `threading.Event` 的 `is_set()` 在 CPython 內為原子讀取，且此旗標只做
#: 單向 set（永不 clear），故 event loop 執行緒與 worker 執行緒併讀無競態。
_DECAY_WORKER_CANCEL = threading.Event()


def _run_decay_evaluation_sync() -> dict:
    """在**執行緒**中執行（經 `asyncio.to_thread`），不阻塞 event loop。

    只做三件事：開始時再檢查一次旗標（即時讀取）、檢查**取消旗標**、呼叫公開 API。
    刻意**不**在此包 try/except —— 例外由呼叫端的 `_decay_worker_main()`
    統一記 WARNING，避免同一條錯誤被記兩次。

    🔴 取消旗標以**模組層級**讀取（`_DECAY_WORKER_CANCEL`），**不**走參數 ——
    執行緒看見的必須是「當前」旗標，且既有測試以零參數 stub 替換本函式，
    改簽名會讓那些 stub 收到未預期的引數（實測會直接拋
    `takes 0 positional arguments but 1 was given`），使 worker 提前結束、
    反而製造出假的併發。零參數是既有契約。

    🔴 **評估器在此「呼叫時」才 import**（惰性）—— 本模組匯入時不得連帶
    匯入 `src.agent.emotion`（見模組 docstring 的零副作用契約）。
    """
    from src.agent.emotion import decay_enabled, decay_evaluate_eligible_agents

    # 🔴 取消邊界 **先於** DB 評估：關閉中 ⇒ 這一輪不得再碰 DB。
    if _DECAY_WORKER_CANCEL.is_set():
        logger.info("[Server] intimacy decay worker 已收到取消訊號，略過本輪評估")
        return {"evaluated": 0, "applied": 0, "reason": "CANCELLED", "results": {}}

    if not decay_enabled():
        # 🔴 雙重檢查 #2：排入與真正開始之間旗標被關掉 ⇒ 本輪直接放棄。
        logger.info("[Server] intimacy decay worker 開始前旗標已 OFF，略過本輪")
        return {"evaluated": 0, "applied": 0, "reason": "FLAG_OFF", "results": {}}
    # 🔴 第二道閘門（逐角色）：把取消意圖**注入**為回呼，讓它能在**每個角色
    #    開始之前**生效。第一道（上方）只擋在整批評估之前 —— 實測缺陷：
    #    首名角色已進 SQLite 等鎖時 set 旗標，該名可在不可中斷的 DB 操作完成後
    #    繼續，但**第二至第七名仍會被評估、扣分**。
    #    以回呼注入而非 import 旗標 ⇒ emotion.py 不新增跨模組耦合。
    return decay_evaluate_eligible_agents(should_stop=_DECAY_WORKER_CANCEL.is_set)


async def _decay_worker_main() -> dict:
    """**單一**衰減 worker：把 DB 評估丟到執行緒，讓 event loop 自由。"""
    try:
        return await asyncio.to_thread(_run_decay_evaluation_sync)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 例外只記 WARNING，任務不得死
        logger.warning(f"[Server] intimacy decay worker 評估錯誤: {e}")
        return {"evaluated": 0, "applied": 0, "reason": "ERROR", "results": {}}


def _maybe_schedule_decay_worker() -> bool:
    """空閒才排入下一個 worker；**前一輪未結束就略過本輪**。

    Returns: `True` 代表本輪排入了 worker；`False` 代表略過（在途 / 關閉中 / 無法排入）。
    """
    global _DECAY_WORKER_TASK
    # 🔴 關閉中 ⇒ 永不排入。 **必要**：shutdown 後 `_DECAY_WORKER_TASK` 的 task
    #    已 `done()`，下方 `not task.done()` guard **不再成立**，只靠 task 參照
    #    會允許排入第二個 worker（已實測：task2 is task1 = False，在途 = 2）。
    if _DECAY_WORKER_CANCEL.is_set():
        logger.debug("[Server] intimacy decay worker 關閉中，略過排入")
        return False
    task = _DECAY_WORKER_TASK
    if task is not None and not task.done():
        # 🔴 不排第二個、不建立無界佇列 —— 本輪直接略過。
        logger.debug("[Server] intimacy decay worker 仍在途，略過本輪")
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    task = asyncio.create_task(_decay_worker_main())
    _DECAY_WORKER_TASK = task
    task.add_done_callback(_on_decay_worker_done)
    return True


def _on_decay_worker_done(task) -> None:
    """done-callback：只做觀測，**絕不** re-raise（否則會冒出 "never retrieved"）。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"[Server] intimacy decay worker 例外: {exc}")


async def _shutdown_decay_worker() -> None:
    """關閉時取消 worker —— **並讓取消意圖到達執行緒**，且**如實**記錄日誌。

    🔴 修正的缺陷（auditor 實測，非推論）：修正前本函式只做 `task.cancel()`，
    回傳後 —— (Q1) 執行緒仍在跑且與 `task.cancelled()` 同時成立；
    (Q2) 該執行緒仍 commit（DB 0 → 20，首輪甚至 42）；
    (Q3) `_maybe_schedule_decay_worker()` 仍回 `True` ⇒ 同時在途 worker = 2。

    修正：
      1. **先** `set()` 取消旗標 —— 執行緒在下一個安全邊界（進入 DB 評估前）
         看得到，這才是「取消意圖可達執行緒」。
      2. 保留既有「清空 `_DECAY_WORKER_TASK` 參照」行為（既有測試
         `test_shutdown_cancels_worker_cleanly` 的契約）。**Q3 不依賴此參照** ——
         task 已 `done()` ⇒ `not task.done()` guard 本就不成立，擋不住第二個
         worker；真正擋住它的是第 1 點的旗標閘門（`_maybe_schedule_decay_worker`
         的 `_DECAY_WORKER_CANCEL.is_set()` 分支）。
      3. 日誌**不得**宣稱「停止 ✓」—— 只證明了 task 結束，未證明執行緒結束。

    🔴 旗標一旦 set，**本次 shutdown 內永不清除**（進程即將結束）；不發明 reset 語意。
    """
    global _DECAY_WORKER_TASK
    # 1. 先傳遞取消意圖（對已進入同步評估的執行緒，於下一安全邊界生效）。
    _DECAY_WORKER_CANCEL.set()
    task = _DECAY_WORKER_TASK
    if task is None:
        return
    _DECAY_WORKER_TASK = None
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info(
        "[Server] intimacy decay worker task 已取消；底層執行緒若已進入同步評估，"
        "將於下一安全邊界停止"
    )
