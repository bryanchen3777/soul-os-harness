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
  6. 連續重建達門檻 ⇒ CRITICAL + 慢速續試（SLOW_RETRY_SECONDS），**永不放棄**
     （TG-STABILITY-1-HOTFIX D-3：不再自我停止監督）
  7. 非 409 TelegramError 記進 _last_errors 且含 traceback（有界）
  8. 單一 bot 啟動失敗不影響其他 bot，且失敗 bot 仍有 supervisor、
     半成品 app 已被拆（TG-STABILITY-1-HOTFIX D-1）
  9. 重建失敗 ⇒ 失敗的 new_app 被拆，連續失敗不得累積孤兒 app（HOTFIX D-2）
 10. _build_app builder 鏈仍含 get_updates_request(HTTPXRequest(connection_pool_size=5))
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
    HEALTHY_RESET_SECONDS,
    MAX_CONSECUTIVE_REBUILDS,
    REBUILD_BACKOFF_SECONDS,
    SLOW_RETRY_SECONDS,
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
    「重建後 poller 依然是死的」⇒ 監督層必須繼續重建, 直到達門檻後進入慢速續試期
    （D-3: 永不停止監督）。
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
    （模擬「重建後 poller 依然是死的」⇒ 監督層必須連續重建, 達門檻後轉慢速續試）。
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


def gated_sleep(monkeypatch, slow_passes=0):
    """記錄退避值; 慢速期（>= SLOW_RETRY_SECONDS）改用可控 gate, 不真的等 300 秒。

    slow_passes: 允許前 N 次慢速睡眠「通過」（讓慢速期的重建真的跑一次）,
    之後的慢速睡眠一律卡在 gate 上 ⇒ 迴圈有界, 測試不會無限重建。
    回傳 (delays, gate)：delays 只收 >=1.0s 的退避值。
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
            await gate.wait()          # 卡住慢速期 ⇒ 由測試斷言後 cancel
            return
        await real_sleep(0)

    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)
    return delays, gate


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
async def test_backoff_sequence_ascends_in_fast_phase(monkeypatch):
    """快速期退避序列 = 1,2,4,8,16（重建後 poller 依然是死的 ⇒ 連續重建）。

    D-3: 達門檻後不再停止監督（見下一支測試）, 所以這裡只斷言快速期的數列。
    """
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter, default_running=False)

    await adapter.start(on_message=lambda *a: None)
    delays, _gate = gated_sleep(monkeypatch)

    old = adapter._apps["yua"]
    old.updater.running = False   # 重建後依然是死的 ⇒ 連續重建
    task = adapter._supervisors["yua"]

    try:
        # 1 次初始 + 5 次重建 = 6
        assert await wait_until(lambda: len(factory.apps) >= 6, timeout=2.0), (
            f"fast-phase rebuilds did not happen: apps={len(factory.apps)} "
            f"delays={delays}"
        )
        expected = [
            float(REBUILD_BACKOFF_SECONDS[i])
            for i in range(MAX_CONSECUTIVE_REBUILDS)
        ]
        assert [d for d in delays if d < SLOW_RETRY_SECONDS] == expected, (
            f"unexpected backoff sequence: {delays}"
        )
        assert adapter._rebuild_counts["yua"] >= MAX_CONSECUTIVE_REBUILDS
        # D-3: supervisor 永不放棄 ⇒ 仍在 _supervisors 且未被停掉
        assert "yua" in adapter._supervisors
        assert adapter._supervisors["yua"] is task
        assert not task.done()
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_backoff_caps_at_sixty_seconds(monkeypatch):
    """退避上限 60 秒: 就算重建次數繼續往上爬, 退避值也停在 60（不得無限成長）。

    註: 端到端跑不到 60s —— 連續重建達門檻後轉入慢速續試期
    （SLOW_RETRY_SECONDS, 見 D-3 測試）, 所以「60s 上限」以純函式層級驗證
    （_rebuild_backoff / test_backoff_helper_directly）。
    """
    adapter = make_adapter({"yua": "tok"})
    sequence = []
    for n in range(len(REBUILD_BACKOFF_SECONDS) + 3):
        adapter._rebuild_counts["yua"] = n
        sequence.append(adapter._rebuild_backoff("yua"))
    assert sequence == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0, 60.0]
    assert max(REBUILD_BACKOFF_SECONDS) == 60


@pytest.mark.asyncio
async def test_rebuild_limit_enters_slow_retry_and_keeps_supervising(
    monkeypatch, caplog
):
    """D-3: 連續重建達門檻 ⇒ 每次重試前 CRITICAL（持續可見）+ 間隔 SLOW_RETRY_SECONDS,
    supervisor **仍存活**（不得自我移除 / 永久放棄）。

    慢速期永續: 外部恢復 ⇒ 重建成功 ⇒ 健康歸零 ⇒ 回快速期（見下一支測試）。
    """
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter, default_running=False)

    with caplog.at_level(logging.CRITICAL, logger="soul_os.channels.telegram"):
        await adapter.start(on_message=lambda *a: None)
        # slow_passes=1: 讓慢速期的第 1 次重試真的跑完, 第 2 次卡在 gate（不真等 300s）
        delays, _gate = gated_sleep(monkeypatch, slow_passes=1)

        old = adapter._apps["yua"]
        old.updater.running = False
        task = adapter._supervisors["yua"]

        try:
            assert await wait_until(
                lambda: len(
                    [d for d in delays if d >= SLOW_RETRY_SECONDS]
                ) >= 2,
                timeout=3.0,
            ), f"never entered slow retry mode: {delays}"

            criticals = [
                r for r in caplog.records if r.levelno == logging.CRITICAL
            ]
            assert criticals, "no CRITICAL logged when entering slow retry"
            assert "consecutive rebuilds" in criticals[0].getMessage()
            assert "slow retry" in criticals[0].getMessage()
            # 慢速期「每一次」重試都 CRITICAL（不是只剩一行 log）
            assert len(criticals) >= 2, (
                f"slow retry must log CRITICAL every attempt: {len(criticals)}"
            )

            # 快速期 1,2,4,8,16 ⇒ 之後固定 300 秒
            assert [d for d in delays if d < SLOW_RETRY_SECONDS] == [1.0, 2.0, 4.0, 8.0, 16.0]
            slow = [d for d in delays if d >= SLOW_RETRY_SECONDS]
            assert set(slow) == {SLOW_RETRY_SECONDS}, f"slow interval: {slow}"

            # 慢速期的重建仍在嘗試（1 初始 + 5 快速 + 1 慢速 = 7）
            assert len(factory.apps) == 7

            # 永不放棄: supervisor 仍在, 未被移除、未被停掉
            assert "yua" in adapter._supervisors
            assert adapter._supervisors["yua"] is task
            assert not task.done()
        finally:
            await cleanup(adapter)

    # 收尾後 _rebuild_counts 不得被歸零（慢速期是靠健康歸零回到快速期, 不是靠放棄）
    assert adapter._rebuild_counts["yua"] >= MAX_CONSECUTIVE_REBUILDS


@pytest.mark.asyncio
async def test_slow_retry_recovers_to_fast_backoff_after_healthy(monkeypatch):
    """D-3: 慢速期遇到成功重建 ⇒ poller 活起來 ⇒ 既有健康歸零邏輯把 count 歸 0
    ⇒ 自動回到快速退避期（不得停在慢速期, 也不得永久躺平）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter, default_running=False)
    await adapter.start(on_message=lambda *a: None)

    delays = []
    gate = asyncio.Event()
    real_sleep = asyncio.sleep
    state = {"passes": 1}

    async def _fake_sleep(delay):
        d = float(delay)
        if d >= 1.0:
            delays.append(d)
        if d >= SLOW_RETRY_SECONDS:
            if state["passes"] > 0:
                state["passes"] -= 1
                # 外部恢復: 下一個重建出來的 poller 會活起來
                factory.default_running = True
                await real_sleep(0)
                return
            await gate.wait()
            return
        await real_sleep(0)

    monkeypatch.setattr(tg_module.asyncio, "sleep", _fake_sleep)
    # 「連續健康 600s 歸零」以注入 0s 驗證（時序可控, 不真等 600 秒）
    monkeypatch.setattr(tg_module, "HEALTHY_RESET_SECONDS", 0.0)

    old = adapter._apps["yua"]
    old.updater.running = False

    try:
        # 慢速期重試 ⇒ 重建成功且 poller 真的跑起來
        assert await wait_until(
            lambda: adapter._apps.get("yua") is not old
            and bool(adapter._apps["yua"].updater.running),
            timeout=3.0,
        ), f"slow retry never produced a live poller: delays={delays}"

        # 既有健康歸零邏輯 ⇒ count 歸 0
        assert await wait_until(
            lambda: adapter._rebuild_counts.get("yua", 1) == 0, timeout=3.0
        ), f"healthy window never reset rebuild count: {adapter._rebuild_counts}"

        # 再次死亡 ⇒ 下一次退避回到快速期第一格（1 秒）, 不是 300 秒
        delays.clear()
        adapter._apps["yua"].updater.running = False
        assert await wait_until(lambda: 1.0 in delays, timeout=3.0), (
            f"did not return to fast backoff: {delays}"
        )
        assert SLOW_RETRY_SECONDS not in delays
        assert "yua" in adapter._supervisors
    finally:
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
    # 直接落在慢速續試期（D-3）: 重試間隔 300s ⇒ 測試會在 sleep 期間 cancel
    adapter._rebuild_counts["yua"] = MAX_CONSECUTIVE_REBUILDS
    task = asyncio.create_task(adapter._supervise("yua"))      # 保留強參考
    assert await wait_until(lambda: "msg" in captured, timeout=2.0), (
        "supervisor never logged the death + last error"
    )
    await cleanup(adapter)

    assert "poller not running" in captured["msg"]
    assert "Connection reset by peer" in captured["msg"]


# ============================================================================
# §4 start() 隔離: 單一 bot 失敗不得拖垮整輪
#   + TG-STABILITY-1-HOTFIX D-1: 失敗的 bot 仍必須有 supervisor、半成品必須被拆
# ============================================================================

@pytest.mark.asyncio
async def test_start_isolates_failing_bot(monkeypatch, caplog):
    """yua 啟動失敗 ⇒ rem 不受影響; start() 不拋出。

    D-1 (HOTFIX): yua (a) 仍有 supervisor, (b) 半成品 app 已被拆,
    (c) 不再被 _apps 持有（⇒ 監督層會走重建）;
    頻道層並印出可 grep 的啟動摘要 WARNING。
    """
    adapter = make_adapter({"yua": "tok-a", "rem": "tok-b"})
    factory = FakeBuilderFactory(log=[])
    monkeypatch.setattr(tg_module, "ApplicationBuilder", factory)
    orig_build = TelegramAdapter._build_app
    yua_apps = []

    def _build(agent_id, token):
        app = orig_build(adapter, agent_id, token)
        if agent_id == "yua":
            yua_apps.append(app)
            if len(yua_apps) == 1:      # 只有開機那次失敗; 重建路徑維持正常
                app.initialize = _raising_async(RuntimeError("initialize failed"))
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)

    with caplog.at_level(logging.WARNING, logger="soul_os.channels.telegram"):
        await adapter.start(on_message=lambda *a: None)   # (a) 不得拋出

    yua_app, rem_app = factory.apps[0], factory.apps[1]
    try:
        # (b) 其他 bot 不受影響
        assert yua_app.updater.start_polling_calls == []      # yua 沒起來
        assert len(rem_app.updater.start_polling_calls) == 1  # rem 不受影響
        assert adapter._apps.get("rem") is rem_app

        # (c) yua 仍有 supervisor（不得永久無聲死亡）
        assert "yua" in adapter._supervisors
        assert not adapter._supervisors["yua"].done()
        assert "rem" in adapter._supervisors

        # (d) 半成品 app 已被拆（updater.stop / stop / shutdown 都被呼叫）
        assert yua_app.updater.stop_calls >= 1, "partial app updater.stop() not called"
        assert yua_app.stopped >= 1, "partial app stop() not called"
        assert yua_app.shutdown_calls >= 1, "partial app shutdown() not called"

        # (e) _apps 不再持有它 ⇒ 監督層看到 app is None ⇒ running=False ⇒ 重建
        assert adapter._apps.get("yua") is not yua_app
        assert yua_app not in adapter._apps.values()

        # 啟動摘要（唯一可 grep 格式）: 有失敗 ⇒ WARNING + 失敗清單
        summaries = [
            r.getMessage()
            for r in caplog.records
            if "[TG] start summary:" in r.getMessage()
        ]
        assert summaries, "no start summary logged"
        assert summaries[-1] == "[TG] start summary: 1/2 bots polling (failed: yua)"
        assert any(r.levelno == logging.WARNING for r in caplog.records
                   if "[TG] start summary:" in r.getMessage())

        # (f) 監督層確實把「app is None」判成未執行並重建成功
        #     （_supervise: _apps.get(agent_id) is None ⇒ running=False ⇒ 重建）
        fast_sleep(monkeypatch)
        assert await wait_until(
            lambda: len(yua_apps) >= 2
            and bool(yua_apps[1].updater.start_polling_calls),
            timeout=2.0,
        ), "supervisor never rebuilt the bot that failed its initial start"
        assert adapter._apps.get("yua") is yua_apps[1]
        assert adapter._rebuild_counts.get("yua", 0) >= 1
    finally:
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

    try:
        assert len(factory.apps) == 2
        # D-1: 失敗的 bot 也必須有 supervisor（舊行為 continue ⇒ 這裡會是 {}）
        assert set(adapter._supervisors) == {"yua", "rem"}
        # D-1: 半成品一律清場（不得留在 _apps 讓 stop() 收拾殘局）
        assert adapter._apps == {}
        for app in factory.apps:
            assert app.updater.start_polling_calls == []
            assert app.updater.stop_calls >= 1
            assert app.stopped >= 1
            assert app.shutdown_calls >= 1
    finally:
        await cleanup(adapter)


# ============================================================================
# §4b TG-STABILITY-1-HOTFIX D-2: 重建失敗 / 被 cancel ⇒ 半成品 app 必須被拆
# ============================================================================

@pytest.mark.asyncio
async def test_rebuild_failure_tears_down_failed_new_app(monkeypatch):
    """D-2: 重建在 initialize 失敗 ⇒ 失敗的 new_app 必須被拆（否則 = 孤兒 session）。

    舊行為: except 內直接 return, 而 new_app 從未寫入 _apps ⇒ stop() 永遠清不到它。
    """
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    orig_build = TelegramAdapter._build_app
    built = []

    def _build(agent_id, token):
        app = orig_build(adapter, agent_id, token)
        built.append(app)
        app.initialize = _raising_async(RuntimeError("initialize failed"))
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)
    delays, _gate = gated_sleep(monkeypatch)     # 不真等退避, 慢速期卡 gate

    old = adapter._apps["yua"]
    old.updater.running = False                  # poller 已死 ⇒ 監督層重建

    try:
        assert await wait_until(lambda: len(built) >= 1, timeout=2.0), (
            "rebuild never attempted"
        )
        orphan = built[0]
        assert await wait_until(
            lambda: orphan.stopped >= 1 and orphan.shutdown_calls >= 1,
            timeout=2.0,
        ), "failed rebuild app was never torn down (orphan session leak)"

        # 三步拆除都必須發生在失敗的 new_app 上
        assert orphan.updater.stop_calls >= 1
        assert orphan.stopped >= 1
        assert orphan.shutdown_calls >= 1

        # 孤兒從未被寫進 _apps（所以 stop() 清不到 ⇒ 必須在 except 內主動拆）
        assert adapter._apps.get("yua") is old
        assert orphan not in adapter._apps.values()

        # 舊 app 的拆除（step 1 既有行為）不受影響
        assert old.stopped >= 1
        assert delays and delays[0] == 1.0
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_five_consecutive_rebuild_failures_leave_no_orphan_apps(monkeypatch):
    """D-2: 連續 5 次重建失敗（失敗點在 start_polling: initialize/start 已跑過）
    ⇒ 不得累積任何未被拆的孤兒 app —— 每個被建構的 app 都必須被拆過。

    審計實測: 舊行為 5 次失敗 ⇒ 5 個孤兒 app（stopped=0 / shutdown_calls=0）,
    每個 bot 每輪最多洩漏 5×2 個 client。
    """
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    orig_build = TelegramAdapter._build_app
    built = []

    def _build(agent_id, token):
        app = orig_build(adapter, agent_id, token)
        built.append(app)
        app.updater.start_polling = _raising_async(
            RuntimeError("start_polling failed")
        )
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)
    delays, _gate = gated_sleep(monkeypatch)

    old = adapter._apps["yua"]
    old.updater.running = False

    try:
        assert await wait_until(lambda: len(built) >= 5, timeout=3.0), (
            f"expected 5 failed rebuild attempts, got {len(built)}"
        )
        for i, app in enumerate(built[:5]):
            # 失敗點在 start_polling ⇒ initialize()/start() 已經跑過（真的連過線）
            assert app.initialized >= 1, f"app{i} never initialized"
            assert app.started >= 1, f"app{i} never started"
            # 零孤兒: 三步拆除都必須發生
            assert app.updater.stop_calls >= 1, f"orphan app{i}: updater.stop() not called"
            assert app.stopped >= 1, f"orphan app{i}: stop() not called"
            assert app.shutdown_calls >= 1, f"orphan app{i}: shutdown() not called"
            assert app not in adapter._apps.values(), f"orphan app{i} left in _apps"

        # 快速期 5 次退避, 之後進慢速續試（D-3）且卡在 gate
        assert [d for d in delays if d < SLOW_RETRY_SECONDS] == [1.0, 2.0, 4.0, 8.0, 16.0]
        # 失敗從未覆蓋現役 app
        assert adapter._apps.get("yua") is old
    finally:
        await cleanup(adapter)


@pytest.mark.asyncio
async def test_cancel_during_rebuild_tears_down_partial_app(monkeypatch):
    """D-2 窄窗: 重建途中被 cancel ⇒ 半成品 new_app 仍必須被拆, 之後才重拋
    CancelledError（關機語意不變）。"""
    adapter = make_adapter({"yua": "tok"})
    factory, _ = install_builder(monkeypatch, adapter)

    await adapter.start(on_message=lambda *a: None)
    fast_sleep(monkeypatch)

    entered = asyncio.Event()
    gate = asyncio.Event()
    orig_build = TelegramAdapter._build_app
    built = []

    def _build(agent_id, token):
        app = orig_build(adapter, agent_id, token)
        built.append(app)

        async def _blocking_initialize():
            app.initialized += 1
            entered.set()
            await gate.wait()

        app.initialize = _blocking_initialize
        return app

    monkeypatch.setattr(adapter, "_build_app", _build)

    old = adapter._apps["yua"]
    task = adapter._supervisors["yua"]
    old.updater.running = False

    try:
        assert await wait_until(lambda: entered.is_set(), timeout=2.0), (
            "rebuild never entered initialize()"
        )
        partial = built[0]

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert partial.updater.stop_calls >= 1
        assert partial.stopped >= 1
        assert partial.shutdown_calls >= 1
        assert partial not in adapter._apps.values()
    finally:
        gate.set()
        await cleanup(adapter)


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
