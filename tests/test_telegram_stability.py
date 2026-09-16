"""
tests/test_telegram_stability.py
TG-STABILITY-1 (2026-09-15) — Telegram polling 存活監督與異常重建

事故: 2026-09-15 16:51-16:54 TG 入站輪詢靜默死亡 (日誌零 traceback), Telegram 端
累積 4 則 update 無人領取, 停服 1h40m, pid 對 149.154.166.110:443 留下 8 條
CLOSE_WAIT (socket 洩漏)。

本檔驗證:
  1. running=True→False ⇒ 監督層在預期週期內觸發重建
  2. 重建前舊 app 的 updater.stop()/stop()/shutdown() 都被呼叫, 且順序在新建構之前
  3. _closing=True ⇒ 不重建, supervisor 正常結束
  4. CancelledError 不觸發重建（關機語意）
  5. 退避序列 1,2,4,8,16,32,60,60…；409 fail-closed ⇒ 直接從 60s 起
  6. 連續重建 >5 次 ⇒ CRITICAL 且停止重試
  7. 非 409 TelegramError 記進 _last_errors 且含 traceback（有界）
  8. 單一 bot 啟動失敗不影響其他 bot
  9. _build_app builder 鏈仍含 get_updates_request(HTTPXRequest(connection_pool_size=5))
     （既有 test_telegram_crash_defense.py 斷言不得失效）
所有測試全程 mock, 不發出任何真實 Telegram API 請求。
"""
import asyncio
import logging

import pytest

from telegram.error import Conflict, TelegramError
from telegram.request import HTTPXRequest

import src.io.channels.telegram as tg_module
from src.io.channels.telegram import (
    MAX_CONSECUTIVE_REBUILDS,
    REBUILD_BACKOFF_SECONDS,
    TelegramAdapter,
)

TINY = 0.005          # 監督週期注入值（不讓測試真的等 5 秒）
DEADLINE = 1.0        # 非同步等待上限


class FakeUpdater:
    """可控制的 updater: running 可切換, stop/start_polling 是 AsyncMock 風格。

    starts_healthy=True 時 start_polling() 會讓 running 變 True（正常 poller）；
    False 則模擬「start_polling 回得來但 poller 仍然是死的」⇒ 監督層必須連續重建。
    """

    def __init__(self, app=None, starts_healthy=True):
        self.app = app
        self.starts_healthy = starts_healthy
        self.running = starts_healthy
        self.start_polling_calls = []
        self.stop_calls = 0
        self.stop_raises = None

    async def start_polling(self, **kwargs):
        self.start_polling_calls.append(kwargs)
        self.running = self.starts_healthy

    async def stop(self):
        self.stop_calls += 1
        self.running = False
        if self.stop_raises is not None:
            raise self.stop_raises


class FakeApp:
    """Fake Application: initialize/start/stop/shutdown + 可控制 updater。

    running: 新建 app 的 poller 是否會真的活起來。預設 True（正常）；設 False 可模擬
    「重建後 poller 依然是死的」⇒ 監督層必須繼續重建直到 fail-loud。
    """

    def __init__(self, name, log=None, running=True):
        self.name = name
        self._log = log if log is not None else []
        self.initialized = 0
        self.started = 0
        self.stopped = 0
        self.shutdown_calls = 0
        self.updater = FakeUpdater(self, starts_healthy=running)
        self.bot = object()
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
    """回傳帶 log 的 fake apps（模擬 ApplicationBuilder 的 builder 鏈）。

    default_running=False ⇒ 新建出來的 app 的 poller 依然是死的
    （模擬「重建後 poller 依然是死的」⇒ 監督層必須連續重建, 這是 fail-loud 路徑）。
    """

    def __init__(self, log=None, default_running=True):
        self.log = log if log is not None else []
        self.apps = []
        self.default_running = default_running
        self.get_updates_request_calls = 0

    def __call__(self):
        factory = self

        class _Builder:
            def token(self, t):
                return self

            def get_updates_request(self, req):
                factory.get_updates_request_calls += 1
                factory.last_request = req
                return self

            def build(self):
                name = f"app{len(factory.apps)}"
                app = FakeApp(name, factory.log, running=factory.default_running)
                factory.apps.append(app)
                return app

        return _Builder()


def make_adapter(tokens, log=None, interval=TINY):
    return TelegramAdapter(tokens=tokens, supervise_interval=interval)


def install_builder(monkeypatch, adapter, default_running=True):
    """把 module 級 ApplicationBuilder 換成可控 fake；回傳 (builder_factory, log 清單)。"""
    log = []
    factory = FakeBuilderFactory(log=log, default_running=default_running)
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    return factory, log


async def wait_until(pred, timeout=DEADLINE, interval=0.005):
    """有界非同步等待（避免 flaky 的固定 sleep）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return bool(pred())


async def cleanup(adapter):
    """收掉所有殘留 supervisor task（避免 task 洩漏到其他測試）。"""
    for task in list(adapter._supervisors.values()):
        task.cancel()
    for task in list(adapter._supervisors.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    adapter._supervisors.clear()


def fast_sleep(monkeypatch, delays=None):
    """把模組內 asyncio.sleep 換成「記錄延遲值 + 立即讓出」的版本。

    監督週期（<1.0s）與退避（>=1.0s）都只記錄不真等 ⇒ 測試不會真的睡 1..60 秒,
    但仍能斷言真實的退避數列。回傳 recorded list。
    """
    recorded = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay):
        d = float(delay)
        recorded.append(d)
        if delays is not None and d >= 1.0:
            delays.append(d)
        await real_sleep(0)

    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)
    return recorded


# ============================================================================
# §1 _build_app 單一建構路徑
# ============================================================================

@pytest.mark.asyncio
async def test_build_app_uses_get_updates_request(monkeypatch):
    """_build_app 的 builder 鏈仍含 get_updates_request(HTTPXRequest(pool=5))。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    app = adapter._build_app("yua", "tok")

    assert factory.get_updates_request_calls == 1
    req = factory.last_request
    assert isinstance(req, HTTPXRequest)
    assert req._client_kwargs["limits"].max_connections == 5
    # handler 有掛上（/tts + message handler）
    assert len(app.handlers) == 2


@pytest.mark.asyncio
async def test_start_and_rebuild_share_single_build_path(monkeypatch):
    """初始啟動與重建都走 _build_app（同一建構路徑, 只此一處建 builder）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)
    calls = []
    orig = adapter._build_app

    def _spy(agent_id, token):
        calls.append(agent_id)
        return orig(agent_id, token)

    monkeypatch.setattr(adapter, "_build_app", _spy)

    await adapter.start(on_message=lambda *a: None)
    try:
        assert calls == ["yua"]
        first = adapter._apps["yua"]
        fast_sleep(monkeypatch)      # 縮短退避等待
        first.updater.running = False
        assert await wait_until(lambda: len(factory.apps) >= 2), "no rebuild happened"
        assert calls == ["yua", "yua"]  # 重建也走同一路徑
    finally:
        await cleanup(adapter)


# ============================================================================
# §3 核心: 存活監督 ⇒ 偵測死亡並重建
# ============================================================================

@pytest.mark.asyncio
async def test_running_false_triggers_rebuild(monkeypatch):
    """updater.running 由 True 變 False ⇒ 監督層在預期週期內觸發重建。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        first = adapter._apps["yua"]
        assert first.updater.running is True
        assert first.initialized == 1

        fast_sleep(monkeypatch)          # 縮短退避等待
        # poller 死亡（模擬靜默死亡: running 變 False, 無例外）
        first.updater.running = False

        assert await wait_until(lambda: len(factory.apps) >= 2), (
            "supervisor did not rebuild after updater.running=False"
        )
        new_app = factory.apps[1]
        assert new_app is not first
        assert new_app.initialized == 1
        assert new_app.started == 1
        assert len(new_app.updater.start_polling_calls) == 1
        assert "error_callback" in new_app.updater.start_polling_calls[0]
        assert adapter._apps["yua"] is new_app
        assert adapter._rebuild_counts["yua"] == 1
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_running_true_does_not_rebuild(monkeypatch):
    """健康（running=True）時監督層不做任何重建。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        await asyncio.sleep(TINY * 6)  # 跑過好幾輪監督
        assert len(factory.apps) == 1
        assert adapter._rebuild_counts.get("yua", 0) == 0
        assert factory.apps[0].stopped == 0
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_healthy_window_resets_rebuild_counter(monkeypatch):
    """連續健康 >=600s ⇒ _rebuild_counts 歸 0。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        adapter._rebuild_counts["yua"] = 4
        adapter._healthy_since["yua"] = (
            asyncio.get_running_loop().time() - tg_module.HEALTHY_RESET_SECONDS - 1
        )
        assert await wait_until(
            lambda: adapter._rebuild_counts["yua"] == 0
        ), "rebuild counter was not reset after healthy window"
        assert adapter._apps["yua"].updater.running is True
    finally:
        await cleanup(adapter)


# ============================================================================
# §3 重建流程: 先徹底拆除舊的（socket/session 回收）, 再建新的
# ============================================================================

@pytest.mark.asyncio
async def test_rebuild_tears_down_old_app_before_new_build(monkeypatch):
    """重建必須呼叫舊 app 的 updater.stop()/stop()/shutdown(), 且順序在新建構之前。"""
    adapter = make_adapter({"yua": "tok"})
    factory, log = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        old = adapter._apps["yua"]
        fast_sleep(monkeypatch)      # 縮短退避等待
        old.updater.running = False

        assert await wait_until(lambda: len(factory.apps) >= 2)

        # (a) 三步都被呼叫過（這裡就是 CLOSE_WAIT / HTTPX session 回收點）
        assert old.updater.stop_calls >= 1, "old updater.stop() not called"
        assert old.stopped >= 1, "old app.stop() not called"
        assert old.shutdown_calls >= 1, "old app.shutdown() not called"

        # (b) 順序: 舊拆除三步都必須早於新 app 的建構
        new = factory.apps[1]
        events = [e for e in log if e.startswith(f"{old.name}:") or e.startswith(f"{new.name}:")]
        idx_init = events.index(f"{new.name}:initialize")
        assert events.index(f"{old.name}:stop") < idx_init
        assert events.index(f"{old.name}:shutdown") < idx_init
        assert events[:idx_init] == [
            f"{old.name}:initialize",
            f"{old.name}:start",
            f"{old.name}:stop",
            f"{old.name}:shutdown",
        ], f"unexpected teardown/build order: {events}"
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_teardown_is_idempotent(monkeypatch):
    """拆除步驟個別失敗不得中斷整個拆除流程（idempotent）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        old = adapter._apps["yua"]
        old.updater.stop_raises = RuntimeError("session already closed")
        fast_sleep(monkeypatch)      # 縮短退避等待
        old.updater.running = False

        assert await wait_until(lambda: len(factory.apps) >= 2)
        # 即使 updater.stop() 炸了, stop()/shutdown() 仍必須被呼叫
        assert old.stopped >= 1
        assert old.shutdown_calls >= 1
    finally:
        await cleanup(adapter)


# ============================================================================
# §5 關閉語意
# ============================================================================

@pytest.mark.asyncio
async def test_closing_prevents_rebuild(monkeypatch):
    """_closing=True ⇒ 不重建, supervisor 正常結束。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    old = adapter._apps["yua"]
    task = adapter._supervisors["yua"]

    adapter._closing = True          # 模擬關機中
    old.updater.running = False      # poller 已死 ⇒ 若無 _closing 檢查就會重建

    assert await wait_until(lambda: task.done()), "supervisor did not exit"
    assert not task.cancelled() and task.exception() is None  # 正常結束, 非例外
    assert len(factory.apps) == 1, "rebuilt while closing!"
    assert old.updater.stop_calls == 0
    assert old.stopped == 0
    await cleanup(adapter)


@pytest.mark.asyncio
async def test_cancelled_error_does_not_rebuild(monkeypatch):
    """監督 task 被 cancel（關機）⇒ 絕不觸發重建, CancelledError 往外拋。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    try:
        old = adapter._apps["yua"]
        task = adapter._supervisors["yua"]
        old.updater.running = False
        await asyncio.sleep(0)  # 讓 task 開始跑

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        await asyncio.sleep(TINY * 4)
        assert len(factory.apps) == 1, "cancel triggered a rebuild!"
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_cancel_during_backoff_sleep_does_not_rebuild(monkeypatch):
    """退避睡眠途中被 cancel ⇒ 不完成重建（不退避期間復活）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    entered_sleep = asyncio.Event()
    real_sleep = asyncio.sleep

    async def _observable_sleep(delay):
        # 只觀察, 不阻塞: 沿用真實 sleep, 但讓測試知道有無退避動作
        if delay >= 1.0:
            entered_sleep.set()
        await real_sleep(0 if delay < 1.0 else TINY * 2)

    await adapter.start(on_message=lambda *a: None)
    monkeypatch.setattr(tg_module.asyncio, "sleep", _observable_sleep)

    old = adapter._apps["yua"]
    task = adapter._supervisors["yua"]
    old.updater.running = False

    assert await wait_until(lambda: entered_sleep.is_set()), (
        "supervisor never entered backoff"
    )
    assert len(factory.apps) == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    monkeypatch.setattr(tg_module.asyncio, "sleep", real_sleep)
    await real_sleep(0)
    assert len(factory.apps) == 1, "rebuild completed despite cancellation"
    assert adapter._apps["yua"] is old
    await cleanup(adapter)


@pytest.mark.asyncio
async def test_stop_cancels_supervisors_and_is_idempotent(monkeypatch):
    """stop(): 先取消 supervisor 再停 app；可重複呼叫（idempotent）。"""
    adapter = make_adapter({"yua": "tok", "rem": "tok2"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    apps = dict(adapter._apps)
    tasks = list(adapter._supervisors.values())
    assert len(tasks) == 2 and all(not t.done() for t in tasks)

    await adapter.stop()

    assert adapter._closing is True
    assert all(t.done() for t in tasks)
    assert adapter._supervisors == {}
    for app in apps.values():
        assert app.updater.stop_calls == 1
        assert app.stopped == 1
        assert app.shutdown_calls == 1

    # idempotent: 再停一次不得拋出、不得重複拆除
    await adapter.stop()
    for app in apps.values():
        assert app.updater.stop_calls == 1
        assert app.shutdown_calls == 1


@pytest.mark.asyncio
async def test_stop_does_not_swallow_cancelled_error(monkeypatch):
    """stop() 不得吞掉 CancelledError（關機語意）。"""
    adapter = make_adapter({"yua": "tok"})
    install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    app = adapter._apps["yua"]
    app.updater.stop_raises = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await adapter.stop()
    await cleanup(adapter)


# ============================================================================
# §3 退避序列 + 重建上限
# ============================================================================

@pytest.mark.asyncio
async def test_backoff_sequence_ascends_and_caps_at_60(monkeypatch):
    """退避序列 = 1,2,4,8,16,32…（上限 60 秒；>5 次由 fail-loud 擋下）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter, default_running=False)

    backoffs = []
    await adapter.start(on_message=lambda *a: None)
    fast_sleep(monkeypatch, delays=backoffs)

    old = adapter._apps["yua"]
    old.updater.running = False   # 重建後依然是死的 ⇒ 連續重建
    task = adapter._supervisors["yua"]

    try:
        assert await wait_until(lambda: task.done(), timeout=2.0), (
            "supervisor did not stop after exceeding rebuild limit"
        )
        expected = [
            float(REBUILD_BACKOFF_SECONDS[i])
            for i in range(MAX_CONSECUTIVE_REBUILDS)
        ]
        assert backoffs == expected, f"unexpected backoff sequence: {backoffs}"
        assert adapter._rebuild_counts["yua"] == MAX_CONSECUTIVE_REBUILDS
        # 建構上限: 1 次初始 + 5 次重建 = 6
        assert len(factory.apps) == 6
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_backoff_caps_at_sixty_seconds(monkeypatch):
    """退避上限 60 秒: 就算重建次數繼續往上爬, 退避值也停在 60（不得無限成長）。

    註: 端到端跑不到 60s —— 連續重建 >5 次會被 fail-loud 擋下（見上一支測試）,
    所以「60s 上限」以純函式層級驗證（_rebuild_backoff / test_backoff_helper_directly）。
    """
    adapter = make_adapter({"yua": "tok"})
    sequence = []
    for n in range(len(REBUILD_BACKOFF_SECONDS) + 3):
        adapter._rebuild_counts["yua"] = n
        sequence.append(adapter._rebuild_backoff("yua"))
    assert sequence == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0]
    assert max(REBUILD_BACKOFF_SECONDS) == 60


@pytest.mark.asyncio
async def test_rebuild_limit_logs_critical_and_stops_retrying(monkeypatch, caplog):
    """連續重建 >5 次 ⇒ logger.critical 且停止監督該 bot（fail-loud）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter, default_running=False)

    await adapter.start(on_message=lambda *a: None)
    fast_sleep(monkeypatch)

    old = adapter._apps["yua"]
    old.updater.running = False
    task = adapter._supervisors["yua"]

    with caplog.at_level(logging.CRITICAL, logger="soul_os.channels.telegram"):
        assert await wait_until(lambda: task.done(), timeout=2.0), (
            "supervisor did not stop after exceeding rebuild limit"
        )

    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert criticals, "no CRITICAL logged when giving up"
    assert "consecutive rebuilds" in criticals[0].getMessage()

    assert adapter._supervisors == {}          # 已停止監督, 不再重試
    assert len(factory.apps) == 6              # 1 初始 + 5 重建, 第 6 次不執行

    # 再等一段時間, 確認真的不再重建
    await asyncio.sleep(TINY * 6)
    assert len(factory.apps) == 6, "kept retrying after fail-loud stop"
    await cleanup(adapter)


@pytest.mark.asyncio
async def test_conflict_failclosed_rebuild_starts_at_max_backoff(monkeypatch):
    """409 fail-closed 觸發的重建 ⇒ 直接從最大退避 60 秒開始。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    delays = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay):
        delays.append(float(delay))
        await real_sleep(0)

    await adapter.start(on_message=lambda *a: None)
    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)

    cb = adapter._make_error_callback("yua")
    exc = Conflict("Conflict: terminated by other getUpdates request")
    try:
        raise exc
    except Conflict:
        for _ in range(3):
            cb(exc)

    assert "yua" in adapter._conflict_failclosed

    old = adapter._apps["yua"]
    old.updater.running = False
    task = adapter._supervisors["yua"]

    try:
        for _ in range(10):
            await real_sleep(0)
            if delays and delays[0] >= 1.0:
                break
        backoffs = [d for d in delays if d >= 1.0]
        assert backoffs, "no backoff observed"
        assert backoffs[0] == 60.0, (
            f"409 fail-closed must start at max backoff, got {backoffs[0]}"
        )
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_backoff_helper_directly():
    """_rebuild_backoff 純函式: 1,2,4,8,16,32,60,60…, 409 ⇒ 60。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    got = []
    for n in range(len(REBUILD_BACKOFF_SECONDS) + 2):
        adapter._rebuild_counts["yua"] = n
        got.append(adapter._rebuild_backoff("yua"))
    assert got == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]

    adapter._rebuild_counts["yua"] = 0
    adapter._conflict_failclosed.add("yua")
    assert adapter._rebuild_backoff("yua") == 60.0


# ============================================================================
# §2 例外透出: _last_errors 含 traceback
# ============================================================================

def test_non_conflict_telegram_error_recorded_with_traceback():
    """非 409 的 TelegramError 也記進 _last_errors, 且含完整 traceback。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    cb = adapter._make_error_callback("yua")

    try:
        raise TelegramError("Bad Gateway")
    except TelegramError as exc:
        cb(exc)

    bucket = adapter._last_errors["yua"]
    assert len(bucket) == 1
    assert "Traceback (most recent call last)" in bucket[0]
    assert "TelegramError" in bucket[0]
    assert "Bad Gateway" in bucket[0]
    # 非 409 不得影響 409 計數與 fail-closed 語意
    assert adapter._conflict_counts.get("yua", 0) == 0
    assert "yua" not in adapter._conflict_failclosed


def test_last_errors_is_bounded_to_five():
    """每 bot 只保留最近 5 筆（不得無限成長）。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    cb = adapter._make_error_callback("yua")
    for i in range(9):
        try:
            raise TelegramError(f"err-{i}")
        except TelegramError as exc:
            cb(exc)

    bucket = adapter._last_errors["yua"]
    assert len(bucket) == 5
    assert "err-8" in bucket[-1]
    assert "err-4" in bucket[0]      # 最舊的留下的是第 5 筆（err-4）
    assert "err-3" not in bucket[0]


def test_conflict_also_recorded_in_last_errors():
    """409 Conflict 同樣被記錄（且 409 語意不變）。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    cb = adapter._make_error_callback("yua")
    try:
        raise Conflict("Conflict")
    except Conflict as exc:
        cb(exc)
    assert len(adapter._last_errors["yua"]) == 1
    assert "Conflict" in adapter._last_errors["yua"][0]


@pytest.mark.asyncio
async def test_supervise_surfaces_last_error_in_log(monkeypatch):
    """監督層在判定 poller 死亡時, 把 _last_errors 內容帶進 logger.error（例外透出）。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"}, supervise_interval=TINY)
    cb = adapter._make_error_callback("yua")
    try:
        raise TelegramError("Connection reset by peer")
    except TelegramError as exc:
        cb(exc)

    captured = {}
    orig_error = tg_module.logger.error

    def _spy(msg, *a, **k):
        captured["msg"] = str(msg)
        return orig_error(msg, *a, **k)

    monkeypatch.setattr(tg_module.logger, "error", _spy)

    adapter._apps["yua"] = FakeApp("app0", running=False)
    adapter._rebuild_counts["yua"] = MAX_CONSECUTIVE_REBUILDS  # 立刻 fail-loud
    task = asyncio.create_task(adapter._supervise("yua"))      # 保留強參考
    assert await wait_until(lambda: "msg" in captured, timeout=2.0), (
        "supervisor never logged the death + last error"
    )
    await cleanup(adapter)

    assert "poller not running" in captured["msg"]
    assert "Connection reset by peer" in captured["msg"]


# ============================================================================
# §4 start() 隔離: 單一 bot 失敗不得拖垮整輪
# ============================================================================

@pytest.mark.asyncio
async def test_start_isolates_failing_bot(monkeypatch):
    """yua 啟動失敗 ⇒ rem 仍被 start_polling; start() 本身不拋出。"""
    adapter = make_adapter({"yua": "tok-a", "rem": "tok-b"})
    factory = FakeBuilderFactory(log=[])
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    orig_build = TelegramAdapter._build_app

    def _build(agent_id, token):
        app = orig_build(adapter, agent_id, token)
        if agent_id == "yua":
            app.initialize = _raising_async(RuntimeError("initialize failed"))
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)
    await adapter.start(on_message=lambda *a: None)   # 不得拋出

    assert len(factory.apps) == 2
    yua_app, rem_app = factory.apps
    assert yua_app.updater.start_polling_calls == []      # yua 沒起來
    assert len(rem_app.updater.start_polling_calls) == 1  # rem 不受影響
    assert "yua" not in adapter._supervisors
    assert "rem" in adapter._supervisors
    await cleanup(adapter)


def _raising_async(exc):
    async def _inner(*a, **k):
        raise exc
    return _inner


@pytest.mark.asyncio
async def test_start_does_not_raise_when_all_bots_fail(monkeypatch):
    """全部 bot 都炸 ⇒ start() 仍不得拋出（通道層吸收）。"""
    adapter = make_adapter({"yua": "tok-a", "rem": "tok-b"})
    factory = FakeBuilderFactory(log=[])
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    orig_start = TelegramAdapter._build_app

    def _build(agent_id, token):
        app = orig_start(adapter, agent_id, token)
        app.initialize = _raising_async(RuntimeError(f"boom-{agent_id}"))
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)
    await adapter.start(on_message=lambda *a: None)

    assert adapter._supervisors == {}
    for app in factory.apps:
        assert app.updater.start_polling_calls == []


# ============================================================================
# callback 契約（不得破壞既有 ptb 要求）
# ============================================================================

def test_error_callback_is_sync_and_tolerates_missing_traceback():
    """error_callback 仍是同步函數；沒有 __traceback__ 也不得炸。"""
    adapter = TelegramAdapter(tokens={"yua": "tok"})
    cb = adapter._make_error_callback("yua")
    assert not asyncio.iscoroutinefunction(cb)
    cb(TelegramError("no traceback attached"))
    assert len(adapter._last_errors["yua"]) == 1
