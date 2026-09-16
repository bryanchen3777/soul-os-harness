"""
tests/test_telegram_stall_detection.py
TG-STALL-DETECTION-1 (2026-09-16) — 「卡住但沒死」的輪詢偵測（真活性訊號）

事故指紋: pid 對 149.154.166.110:443 留下多條 CLOSE_WAIT、零 ERROR、零 traceback、
getUpdates 不再成功, 但 updater.running 仍是 True。
ptb 22.8 原始碼事實: Updater.running 只是純旗標, start_polling 的輪詢任務沒有
done-callback, network_retry_loop(max_retries=-1) 遇錯永不中止也不清旗標
⇒ 只讀 running 的健康判據判不出「卡住但沒死」。

本檔驗證（全程 mock / 行程內 fake, 0 真實 Telegram API 請求, 不用真實 token）:
  1. 真活性訊號: heartbeat wrapper 確實被 ptb 真實輪詢路徑呼叫
     （真實 Application/Updater; HTTP 只到行程內 fake do_request, 意外 URL 直接炸）
  2. 停滯偵測: running=True 但最近一次成功超過 STALL_THRESHOLD_SECONDS
     ⇒ 走既有 _rebuild_poller 重建, ERROR 日誌帶出距上次成功秒數 + 成功/失敗次數
  3. 健康不誤判: 持續有成功 getUpdates（含空 list = 沒有新訊息）⇒ 不得重建
  4. 從未成功過（last_poll_success is None）⇒ 以 app 建構時間起算, 超過門檻即重建
  5. wrapper 透明性: 回傳值/參數原樣穿透; 例外原樣重拋（型別與物件不變）
     且進既有 _last_errors; 成功與失敗計數互不污染
  6. snapshot_health() 曝露 per-agent 狀態（本票不寫任何檔案）
"""
import asyncio
import inspect
import json
import logging

import pytest

from telegram.error import TelegramError
from telegram.request import HTTPXRequest

import src.io.channels.telegram as tg_module
from src.io.channels.telegram import (
    MAX_CONSECUTIVE_REBUILDS,
    REBUILD_BACKOFF_SECONDS,
    SLOW_RETRY_SECONDS,
    STALL_THRESHOLD_SECONDS,
    TelegramAdapter,
)

TINY = 0.005                      # 監督週期注入值（不讓測試真的等 5 秒）
DEADLINE = 2.0                    # 非同步等待上限
#: 假 token（格式合法但不存在; 本檔所有 HTTP 都在行程內被 fake 攔下, 不會外流）
FAKE_TOKEN = "123456:AAHfakefakefakefakefakefakefakefake"


# ============================================================================
# 可注入時鐘（§1「可注入時鐘」: 生產路徑預設 time.monotonic, 測試注入假時鐘）
# ============================================================================

class FakeClock:
    """可控 monotonic 時鐘（測試不真的等 120 秒）。"""

    def __init__(self, start: float = 10_000.0):
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


# ============================================================================
# Fake ptb 物件（bot 必須是「可被實例屬性遮蔽」的普通物件）
# ============================================================================

class FakeBot:
    """可控制的 bot: get_updates 會被 _install_poll_heartbeat 包一層 wrapper。

    error 非 None ⇒ get_updates 拋出該例外（模擬輪詢失敗）。
    result ⇒ 成功時的回傳值（預設空 list = 成功但沒有新訊息）。
    """

    def __init__(self):
        self.args_seen = []
        self.kwargs_seen = []
        self.error = None
        self.result = []

    async def get_updates(self, *args, **kwargs):
        self.args_seen.append(args)
        self.kwargs_seen.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class FakeUpdater:
    """可控制的 updater: running 是純旗標（跟 ptb 一樣 —— 這正是本票的問題）。"""

    def __init__(self, app=None, starts_healthy=True):
        self.app = app
        self.starts_healthy = starts_healthy
        self.running = starts_healthy
        self.start_polling_calls = []
        self.stop_calls = 0

    async def start_polling(self, **kwargs):
        self.start_polling_calls.append(kwargs)
        self.running = self.starts_healthy

    async def stop(self):
        self.stop_calls += 1
        self.running = False


class FakeApp:
    """Fake Application: initialize/start/stop/shutdown + 可控制 updater/bot。"""

    def __init__(self, name, log=None, running=True):
        self.name = name
        self._log = log if log is not None else []
        self.initialized = 0
        self.started = 0
        self.stopped = 0
        self.shutdown_calls = 0
        self.updater = FakeUpdater(self, starts_healthy=running)
        self.bot = FakeBot()
        self.handlers = []

    def _rec(self, what):
        self._log.append(f"{self.name}:{what}")

    def add_handler(self, handler):
        self.handlers.append(handler)

    async def initialize(self):
        self.initialized += 1
        self._rec("initialize")

    async def start(self):
        self.started += 1
        self._rec("start")

    async def stop(self):
        self.stopped += 1
        self._rec("stop")

    async def shutdown(self):
        self.shutdown_calls += 1
        self._rec("shutdown")


class FakeBuilderFactory:
    """回傳帶 log 的 fake apps（模擬 ApplicationBuilder 的 builder 鏈）。"""

    def __init__(self, log=None, default_running=True):
        self.log = log if log is not None else []
        self.apps = []
        self.default_running = default_running

    def __call__(self):
        factory = self

        class _Builder:
            def token(self, t):
                return self

            def get_updates_request(self, req):
                return self

            def build(self):
                name = f"app{len(factory.apps)}"
                app = FakeApp(name, factory.log, running=factory.default_running)
                factory.apps.append(app)
                return app

        return _Builder()


def make_adapter(tokens, clock=None, interval=TINY):
    return TelegramAdapter(
        tokens=tokens, supervise_interval=interval, monotonic_clock=clock
    )


def install_builder(monkeypatch, adapter, default_running=True):
    log = []
    factory = FakeBuilderFactory(log=log, default_running=default_running)
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    return factory, log


async def wait_until(pred, timeout=DEADLINE, interval=0.005):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return bool(pred())


async def cleanup(adapter):
    for task in list(adapter._supervisors.values()):
        task.cancel()
    for task in list(adapter._supervisors.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    adapter._supervisors.clear()


def fast_sleep(monkeypatch):
    """把模組內 asyncio.sleep 換成「記錄 + 立即讓出」（不真等退避 1..60 秒）。"""
    recorded = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay):
        recorded.append(float(delay))
        await real_sleep(0)

    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)
    return recorded


def gated_sleep(monkeypatch, slow_passes=0):
    """記錄退避值; 慢速期（>= SLOW_RETRY_SECONDS）改用可控 gate（不真等 300 秒）。

    slow_passes: 允許前 N 次慢速睡眠通過; 之後一律卡在 gate ⇒ 迴圈有界。
    """
    delays = []
    gate = asyncio.Event()
    state = {"passes": int(slow_passes)}
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay):
        d = float(delay)
        if d >= 1.0:
            delays.append(d)
        if d >= SLOW_RETRY_SECONDS:
            if state["passes"] > 0:
                state["passes"] -= 1
                await real_sleep(0)
                return
            await gate.wait()
            return
        await real_sleep(0)

    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)
    return delays, gate


def error_messages(caplog, needle):
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and needle in r.getMessage()
    ]


# ============================================================================
# §1 真活性訊號: 真實 ptb 輪詢路徑 ⇒ wrapper（離線, 0 外部請求）
# ============================================================================

@pytest.mark.asyncio
async def test_real_ptb_polling_loop_calls_heartbeat_wrapper(monkeypatch):
    """真實 Application/Updater 的輪詢路徑（Updater.start_polling →
    network_retry_loop → polling_action_cb → `await self.bot.get_updates(...)`）
    確實經過 heartbeat wrapper —— 全程離線。

    離線手法: 只把 HTTP 出口（HTTPXRequest.do_request）換成行程內 fake,
    其餘全是真實 ptb 物件 ⇒ 真實的 Bot.get_updates 有被執行到。
    fake 對「非預期 URL」直接 AssertionError ⇒ 任何試圖打 api.telegram.org
    的行為都會讓本測試炸掉, 而不是靜默外流。
    """
    from telegram.ext import ApplicationBuilder

    transport = []

    async def _fake_do_request(self, url, method, request_data=None, **kwargs):
        transport.append((method, url))
        if url.endswith("/getMe"):
            result = {
                "id": 1, "is_bot": True, "first_name": "Fake", "username": "fake_bot",
            }
        elif url.endswith("/deleteWebhook"):
            result = True
        elif url.endswith("/getUpdates"):
            result = []
        else:
            raise AssertionError(f"unexpected outbound URL (no real network allowed): {url}")
        return 200, json.dumps({"ok": True, "result": result}).encode()

    monkeypatch.setattr(HTTPXRequest, "do_request", _fake_do_request)

    app = ApplicationBuilder().token(FAKE_TOKEN).build()
    adapter = TelegramAdapter(tokens={"yua": FAKE_TOKEN})
    adapter._install_poll_heartbeat("yua", app)
    adapter._app_built_at["yua"] = adapter._now()

    # (a) 實例屬性遮蔽已生效（不是類別方法）
    assert "get_updates" in app.bot.__dict__, "instance attribute shadowing not applied"
    assert app.bot.get_updates is app.bot.__dict__["get_updates"]
    assert app.bot.get_updates is not type(app.bot).get_updates
    assert inspect.getattr_static(app.bot, "get_updates") is app.bot.get_updates

    await app.initialize()
    await app.start()
    try:
        await app.updater.start_polling(error_callback=lambda exc: None)
        for _ in range(400):
            if adapter._poll_success_counts.get("yua", 0) >= 2:
                break
            await asyncio.sleep(0.01)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

    # (b) 真實輪詢迴圈命中 wrapper（成功回傳空 list 也算一次成功）
    assert adapter._poll_success_counts.get("yua", 0) >= 2, (
        f"ptb polling path never reached the heartbeat wrapper: "
        f"transport={transport}"
    )
    assert adapter._last_poll_success.get("yua") is not None
    assert adapter._poll_failure_counts.get("yua", 0) == 0

    # (c) 0 真實外部請求: 所有 outbound 都落在行程內 fake, 且只有預期端點
    endpoints = {url.rsplit("/", 1)[-1] for _m, url in transport}
    assert endpoints <= {"getMe", "deleteWebhook", "getUpdates"}, transport
    assert sum(1 for _m, url in transport if url.endswith("/getUpdates")) >= 2


# ============================================================================
# §2 停滯判定 ⇒ 走既有 _rebuild_poller
# ============================================================================

@pytest.mark.asyncio
async def test_stalled_poller_triggers_rebuild_with_diagnostic_log(monkeypatch, caplog):
    """running=True 但最近一次成功超過門檻 ⇒ 判停滯 ⇒ 重建, 且 ERROR 一眼可判。

    這是本票的核心: 舊判據（只看 running）在這種狀態下會永遠認定健康。
    """
    clock = FakeClock()
    adapter = make_adapter({"yua": "tok"}, clock=clock)
    factory, _ = install_builder(monkeypatch, adapter)

    with caplog.at_level(logging.ERROR, logger="soul_os.channels.telegram"):
        await adapter.start(on_message=lambda *a: None)
        try:
            first = adapter._apps["yua"]
            assert first.updater.running is True

            # 真實成功一次（回傳空 list: 成功但沒有新訊息）
            assert await first.bot.get_updates(offset=None, timeout=10) == []
            assert adapter._poll_success_counts["yua"] == 1
            success_at = adapter._last_poll_success["yua"]
            assert success_at == clock.value

            fast_sleep(monkeypatch)                       # 不真等退避
            clock.advance(STALL_THRESHOLD_SECONDS + 5.0)   # 卡住: running 仍為 True

            assert await wait_until(lambda: len(factory.apps) >= 2), (
                "stalled poller (updater.running=True) was never rebuilt"
            )

            # 停滯重建走既有路徑: 先拆舊再建新
            new_app = factory.apps[1]
            assert adapter._apps["yua"] is new_app
            assert first.stopped >= 1 and first.shutdown_calls >= 1
            assert new_app.updater.start_polling_calls
            # 停滯重建計入同一計數器與同一退避（語意不變）
            assert adapter._rebuild_counts["yua"] == 1
            # 新 app 的活性從零起算
            assert adapter._last_poll_success["yua"] is None

            stalled = error_messages(caplog, "STALLED")
            assert stalled, "no ERROR logged for a stalled poller"
            msg = stalled[0]
            assert "125.0" in msg, f"missing seconds-since-last-success: {msg}"
            assert "poll_success_count=1" in msg, msg
            assert "poll_failure_count=0" in msg, msg
        finally:
            await cleanup(adapter)


@pytest.mark.asyncio
async def test_healthy_poller_with_empty_updates_is_never_rebuilt(monkeypatch):
    """持續有成功 getUpdates（含空 list）⇒ 累積時間再長也不得重建。"""
    clock = FakeClock()
    adapter = make_adapter({"yua": "tok"}, clock=clock)
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        first = adapter._apps["yua"]
        for _ in range(5):
            clock.advance(STALL_THRESHOLD_SECONDS / 2.0)     # 單次永不超過門檻
            assert await first.bot.get_updates() == []       # 成功但沒有新訊息
            await asyncio.sleep(TINY * 3)                    # 讓監督層跑幾輪

        assert adapter._poll_success_counts["yua"] == 5
        assert adapter._poll_failure_counts.get("yua", 0) == 0
        assert adapter._last_poll_success["yua"] == clock.value
        assert adapter._rebuild_counts.get("yua", 0) == 0
        assert len(factory.apps) == 1, "healthy poller was rebuilt!"
        assert first.stopped == 0
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_never_succeeded_poller_stalls_after_threshold(monkeypatch, caplog):
    """last_poll_success is None（本 app 從未成功）⇒ 以 app 建構時間起算,
    超過門檻即判停滯 —— 不得被當成「永遠健康」。"""
    clock = FakeClock()
    adapter = make_adapter({"yua": "tok"}, clock=clock)
    factory, _ = install_builder(monkeypatch, adapter)

    with caplog.at_level(logging.ERROR, logger="soul_os.channels.telegram"):
        await adapter.start(on_message=lambda *a: None)
        try:
            first = adapter._apps["yua"]
            assert adapter._last_poll_success.get("yua") is None
            assert first.updater.running is True

            fast_sleep(monkeypatch)
            clock.advance(STALL_THRESHOLD_SECONDS + 1.0)

            assert await wait_until(lambda: len(factory.apps) >= 2), (
                "poller that never succeeded was treated as healthy forever"
            )

            stalled = error_messages(caplog, "STALLED")
            assert stalled, "no ERROR logged for a never-succeeded poller"
            assert "never" in stalled[0], stalled[0]
            assert "poll_success_count=0" in stalled[0], stalled[0]
        finally:
            await cleanup(adapter)


# ============================================================================
# §2b 有界性: 活性訊號永遠是 None 時, 重建仍走既有退避 + 慢速期（非熱迴圈）
# ============================================================================

@pytest.mark.asyncio
async def test_never_succeeded_rebuilds_stay_bounded_by_existing_backoff(
    monkeypatch, caplog
):
    """wrapper 從未被呼叫（`last_poll_success` 永為 None）⇒ 每一輪停滯重建都走
    **既有**退避 1,2,4,8,16 與既有慢速期（SLOW_RETRY_SECONDS, 卡 gate 不真等）,
    且日誌帶出 `poll_success_count=0` 供診斷 —— 不得新增未經退避的重建路徑。"""
    clock = FakeClock()
    adapter = make_adapter({"yua": "tok"}, clock=clock)
    factory, _ = install_builder(monkeypatch, adapter)   # 新建 app 的 poller 都是活的

    with caplog.at_level(logging.ERROR, logger="soul_os.channels.telegram"):
        await adapter.start(on_message=lambda *a: None)
        delays, _gate = gated_sleep(monkeypatch, slow_passes=0)   # 慢速期一律卡住
        try:
            # 5 次快速期重建（每次都要重新越過停滯門檻才會再判停滯）
            for i in range(MAX_CONSECUTIVE_REBUILDS):
                clock.advance(STALL_THRESHOLD_SECONDS + 1.0)
                assert await wait_until(
                    lambda n=i: len(factory.apps) >= n + 2, timeout=DEADLINE
                ), f"stall rebuild #{i + 1} never happened: delays={delays}"

            assert [d for d in delays if d < SLOW_RETRY_SECONDS] == [
                float(REBUILD_BACKOFF_SECONDS[i])
                for i in range(MAX_CONSECUTIVE_REBUILDS)
            ], f"backoff sequence changed: {delays}"

            # 第 6 次 ⇒ 既有慢速期（300s, 卡在 gate ⇒ 有界, 不是熱迴圈）
            clock.advance(STALL_THRESHOLD_SECONDS + 1.0)
            assert await wait_until(
                lambda: any(d >= SLOW_RETRY_SECONDS for d in delays),
                timeout=DEADLINE,
            ), f"never entered slow retry mode: {delays}"
            assert len(factory.apps) == MAX_CONSECUTIVE_REBUILDS + 1, (
                "slow-retry rebuild ran without waiting its interval"
            )

            stalled = error_messages(caplog, "STALLED")
            assert stalled, "no ERROR logged on the never-succeeded stall path"
            assert "poll_success_count=0" in stalled[-1], stalled[-1]
            assert "last_poll_success=never" in stalled[-1], stalled[-1]

            # 活性訊號從頭到尾沒出現過（wrapper 沒被呼叫）
            assert adapter._poll_success_counts.get("yua", 0) == 0
            assert adapter._last_poll_success["yua"] is None
        finally:
            await cleanup(adapter)


# ============================================================================
# §2c 安全: 重建不得吃掉 Owner 待領取的 update
# ============================================================================

@pytest.mark.asyncio
async def test_start_and_rebuild_never_drop_pending_updates(monkeypatch):
    """start() 與重建都不得傳 `drop_pending_updates=True`。

    ptb 22.8 的 `Updater.start_polling(..., drop_pending_updates=None, ...)` 預設
    None（falsy）⇒ `_bootstrap` 只 `delete_webhook(drop_pending_updates=None)`,
    **不會**丟棄待領取的 update。若我們哪天改成 True, 「停滯 ⇒ 自動重建」就會
    吃掉 Owner 的訊息 ⇒ 這支測試是那條紅線的迴歸護欄。
    """
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        fast_sleep(monkeypatch)
        factory.apps[0].updater.running = False          # 觸發一次重建
        assert await wait_until(lambda: len(factory.apps) >= 2)

        calls = [
            kwargs
            for app in factory.apps
            for kwargs in app.updater.start_polling_calls
        ]
        assert len(calls) >= 2, f"expected start + rebuild calls, got {calls}"
        for kwargs in calls:
            assert "drop_pending_updates" not in kwargs, kwargs
            assert kwargs.get("drop_pending_updates") in (None, False), kwargs
            assert kwargs.get("error_callback") is not None, kwargs
    finally:
        await cleanup(adapter)


# ============================================================================
# §1 wrapper 透明性
# ============================================================================
@pytest.mark.asyncio
async def test_heartbeat_wrapper_is_transparent(monkeypatch):
    """成功值/參數原樣穿透; 例外原樣重拋（同型別、同物件）且進 _last_errors。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    install_builder(monkeypatch, adapter)
    app = adapter._build_app("yua", "tok")
    bot = app.bot

    # 實例屬性已遮蔽類別方法（wrapper 在實例 __dict__ 內, 不是類別方法）
    assert "get_updates" in bot.__dict__
    assert bot.get_updates is not FakeBot.get_updates
    assert inspect.getattr_static(bot, "get_updates") is bot.get_updates

    # --- 成功: 參數穿透 + 回傳值同一物件 ---
    sentinel = object()
    bot.result = sentinel
    before = adapter._now()
    got = await bot.get_updates(offset=7, timeout=10, allowed_updates=None)
    after = adapter._now()
    assert got is sentinel, "return value was not passed through unchanged"
    assert bot.kwargs_seen[-1] == {
        "offset": 7, "timeout": 10, "allowed_updates": None,
    }
    assert adapter._poll_success_counts["yua"] == 1
    assert before <= adapter._last_poll_success["yua"] <= after
    assert adapter._poll_failure_counts.get("yua", 0) == 0

    # --- 失敗: 例外型別/物件不變, 原樣重拋（絕不吞掉）---
    boom = TelegramError("Connection reset by peer")
    bot.error = boom
    with pytest.raises(TelegramError) as excinfo:
        await bot.get_updates(offset=8)
    assert excinfo.value is boom, "wrapper did not re-raise the original exception"
    assert type(excinfo.value) is TelegramError

    assert adapter._poll_failure_counts["yua"] == 1
    assert adapter._poll_success_counts["yua"] == 1, "a failure was counted as success"
    bucket = adapter._last_errors["yua"]
    assert "Traceback (most recent call last)" in bucket[-1]
    assert "Connection reset by peer" in bucket[-1]

    # --- 之後成功: 計數與活性恢復 ---
    bot.error = None
    bot.result = []
    before = adapter._now()
    assert await bot.get_updates() == []
    assert adapter._poll_success_counts["yua"] == 2
    assert adapter._last_poll_success["yua"] >= before


@pytest.mark.asyncio
async def test_heartbeat_install_failure_is_loud_not_silent(monkeypatch, caplog):
    """app.bot 無法被遮蔽時必須 ERROR 明示（不得靜默回到「只看 running」）。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    app = FakeApp("app0")
    app.bot = object()          # object() 沒有 __dict__ ⇒ 遮蔽不可能

    with caplog.at_level(logging.ERROR, logger="soul_os.channels.telegram"):
        adapter._install_poll_heartbeat("yua", app)

    assert error_messages(caplog, "heartbeat"), (
        "install failure was silent — heartbeat must be fail-loud"
    )


# ============================================================================
# §3 對外曝露（本票不寫檔）
# ============================================================================

@pytest.mark.asyncio
async def test_snapshot_health_exposes_per_agent_state(monkeypatch):
    """snapshot_health() 回傳 per-agent 的活性/計數/重建次數。"""
    adapter = TelegramAdapter(tokens={"yua": "tok", "rem": "tok2"})
    install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        assert await adapter._apps["yua"].bot.get_updates() == []

        snap = adapter.snapshot_health()
        assert set(snap) == {"yua", "rem"}

        assert snap["yua"]["last_poll_success"] is not None
        assert snap["yua"]["poll_success_count"] == 1
        assert snap["yua"]["poll_failure_count"] == 0
        assert snap["yua"]["running"] is True
        assert snap["yua"]["rebuild_count"] == 0

        assert snap["rem"]["last_poll_success"] is None
        assert snap["rem"]["poll_success_count"] == 0
        assert snap["rem"]["running"] is True
    finally:
        await cleanup(adapter)
