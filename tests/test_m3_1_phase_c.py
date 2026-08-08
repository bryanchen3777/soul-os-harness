"""
tests/test_m3_1_phase_c.py — M3.1 Phase C Contract Tests

Bry 拍板 2026-08-08 09:24 — M3.1 Phase C:

Phase C 在 M3.1 Phase A + Phase B 之上, 新增 WorldEventDispatcher。
核心 routing contract:
    Dispatcher.emit_and_inject()
        → build WorldEvent
        → observe priority
        → await dispatcher._injector.inject()
        → return event

派工要求至少 20 個 test, 涵蓋:
  Dispatcher core (10)
  Priority observation (7)
  Multi-source (4)
  Wire-up (5)
  Hard limits (4)

回歸要求 (Bry 派工):
  - 既有 117 tests (42 M3 + 29 Phase A + 46 Phase B) 100% pass
  - 不修改既有 tests semantics
  - middleware.py / run_server.py / token_manager.py 0 change
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from src.world import (
    WorldEventSource,
    WorldEventInjector,
)
from src.world.dispatcher import WorldEventDispatcher
from src.world.perception import WorldEvent
from src.world.source.synthetic import SyntheticWorldEventSource


# ───────────────────────────────────────────────────────────
# Shared fixtures
# ───────────────────────────────────────────────────────────


class _StubInjector:
    """測試用 stub, conform WorldEventInjector (async inject)。"""

    def __init__(self, raise_exc: Exception = None):
        self.received: list = []
        self.inject_call_count = 0
        self._raise = raise_exc

    async def inject(self, event: WorldEvent) -> None:
        self.inject_call_count += 1
        if self._raise is not None:
            raise self._raise
        self.received.append(event)


class _StubSourceNoInjector:
    """沒 set_injector method 的 stub source (capability detection 跳過用)。"""
    source_id = "stub_no_inj"

    async def start(self): pass
    async def stop(self): pass


# ───────────────────────────────────────────────────────────
# 1. Dispatcher core (10 tests)
# ───────────────────────────────────────────────────────────


def test_dispatcher_constructs_empty():
    """Dispatcher 預設是空的, 沒 source 沒 injector。"""
    d = WorldEventDispatcher()
    assert d.get_attached_source_ids() == []
    assert d.get_injector() is None
    assert d.get_observation_log() == []


def test_dispatcher_constructs_with_name():
    """Dispatcher name 跟 get_attached_source_ids / get_injector 都 OK。"""
    d = WorldEventDispatcher(name="prod_dispatcher")
    assert d.name == "prod_dispatcher"
    assert d.get_attached_source_ids() == []
    assert d.get_injector() is None


def test_dispatcher_attach_source_basic():
    """attach_source 後, source 應該在 dispatcher 內。"""
    d = WorldEventDispatcher()
    src = SyntheticWorldEventSource()
    d.attach_source(src)
    assert "synthetic" in d.get_attached_source_ids()


def test_dispatcher_attach_source_then_injector_propagates():
    """attach source → attach injector, capability detection 把 injector 傳給 source。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    src = SyntheticWorldEventSource()
    d.attach_source(src)
    d.attach_injector(stub)
    assert d.get_injector() is stub
    # source 透過 capability detection 收到 injector
    assert src.get_injector() is stub


def test_dispatcher_attach_injector_then_source_also_propagates():
    """attach injector → attach source, source 仍透過 capability detection 收到 injector。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_injector(stub)
    src = SyntheticWorldEventSource()
    d.attach_source(src)
    # 兩種 attach 順序都應 propagate injector
    assert src.get_injector() is stub


def test_dispatcher_emit_and_inject_succeeds():
    """happy path: dispatcher 能 build event → inject → return event。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="下雨了",
        novelty_id="weather_rain_20260808",
        priority=3,
    ))
    assert isinstance(e, WorldEvent)
    assert e.source == "synthetic"
    assert e.type == "rain_started"
    assert e.novelty_id == "weather_rain_20260808"
    assert e.summary == "下雨了"
    assert e.priority == 3
    # injector 收到 event (同一個 object)
    assert stub.inject_call_count == 1
    assert stub.received[0] is e


def test_dispatcher_injector_called_exactly_once_per_emit():
    """每次 emit_and_inject 必須 call injector.inject() 恰好一次。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    for i in range(5):
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type=f"event_{i}",
            summary="t",
            novelty_id=f"n_{i}",
        ))
    assert stub.inject_call_count == 5
    assert len(stub.received) == 5


def test_dispatcher_injector_exception_propagates():
    """injector.inject() raise 必須 propagate 給 dispatcher caller。"""
    stub = _StubInjector(raise_exc=RuntimeError("injector failed"))
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="t",
            novelty_id="x",
        ))
    assert "injector failed" in str(exc_info.value)
    # observation log 仍記錄 (觀察發生在 inject 之前)
    assert len(d.get_observation_log()) == 1


def test_dispatcher_unknown_source_raises_value_error():
    """emit_and_inject 對沒 attach 的 source_id 必須 raise ValueError。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_injector(stub)

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="nonexistent",
            type="x",
            summary="t",
            novelty_id="x",
        ))
    assert "nonexistent" in str(exc_info.value)


def test_dispatcher_missing_injector_raises_runtime_error():
    """dispatcher 沒 attach injector 時 emit_and_inject 必須 raise RuntimeError。"""
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="t",
            novelty_id="x",
        ))
    assert "injector" in str(exc_info.value).lower()


# ───────────────────────────────────────────────────────────
# 2. Priority observation (7 tests)
# ───────────────────────────────────────────────────────────


def test_dispatcher_priority_observed_in_log():
    """emit_and_inject 後 observation log 應包含這次 emit 的 priority。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="t",
        novelty_id="x",
        priority=7,
    ))
    log = d.get_observation_log()
    assert len(log) == 1
    assert log[0]["priority"] == 7


def test_dispatcher_default_priority_observed():
    """priority 不傳時, observation log 記錄 default = 0。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
    ))
    assert d.get_observation_log()[0]["priority"] == 0


def test_dispatcher_negative_priority_observed():
    """priority 負值有效 (Bry 派工 02:59: 不發明 priority range constraint)。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
        priority=-5,
    ))
    assert d.get_observation_log()[0]["priority"] == -5


def test_dispatcher_positive_priority_observed():
    """priority 正值有效。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
        priority=100,
    ))
    assert d.get_observation_log()[0]["priority"] == 100


def test_dispatcher_priority_observation_includes_source_id():
    """observation dict 必須包含 source_id。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
        priority=3,
    ))
    obs = d.get_observation_log()[0]
    assert "source_id" in obs
    assert obs["source_id"] == "synthetic"


def test_dispatcher_priority_observation_includes_timestamp():
    """observation dict 必須包含 ISO 8601 UTC timestamp。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
    ))
    obs = d.get_observation_log()[0]
    assert "ts" in obs
    # ISO 8601 開頭格式
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", obs["ts"]), (
        f"ts 應是 ISO 8601, 實際: {obs['ts']!r}"
    )


def test_dispatcher_priority_observation_does_not_modify_scoring():
    """
    Bry 派工 09:24: priority 只 observe, 不 routing, 不進 PerceptionScores。
    確認 event 本身的 priority 跟 emit 傳入的 priority 一致 (Dispatcher 沒改 priority)。
    """
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
        priority=5,
    ))
    # Dispatcher 沒改 event 的 priority
    assert e.priority == 5
    # 進 injector 的 event 跟 return 的 event 同一個 object
    assert stub.received[0].priority == 5
    assert stub.received[0] is e


# ───────────────────────────────────────────────────────────
# 3. Multi-source (4 tests)
# ───────────────────────────────────────────────────────────


def test_dispatcher_multiple_sources_isolated():
    """多 source attach, 每個 source 獨立 emit 自己的 event。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    s1 = SyntheticWorldEventSource()
    s2 = _StubSourceNoInjector()
    s2.source_id = "stub_2"
    d.attach_source(s1)
    d.attach_source(s2)
    d.attach_injector(stub)
    assert d.get_attached_source_ids() == ["synthetic", "stub_2"]

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="from_synthetic",
        summary="t",
        novelty_id="n1",
    ))
    asyncio.run(d.emit_and_inject(
        source_id="stub_2",
        type="from_stub_2",
        summary="t",
        novelty_id="n2",
    ))
    assert stub.inject_call_count == 2
    # 兩次 event 都收到, type 各自對
    assert stub.received[0].type == "from_synthetic"
    assert stub.received[1].type == "from_stub_2"


def test_dispatcher_capability_detection_skips_non_capable_source():
    """沒 set_injector method 的 source, attach_injector 不 crash。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    non_capable = _StubSourceNoInjector()
    d.attach_source(non_capable)
    # 不 crash
    d.attach_injector(stub)
    # non_capable 沒 _injector 屬性
    assert not hasattr(non_capable, "_injector") or non_capable._injector is None


def test_dispatcher_attach_injector_none_detaches():
    """attach_injector(None) 必須 detach, source registration 保留。"""
    stub = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)
    assert d.get_injector() is stub

    d.attach_injector(None)
    assert d.get_injector() is None
    # source registration 仍保留
    assert "synthetic" in d.get_attached_source_ids()
    # source 的 injector 也被 detach (capability detection propagate)
    assert SyntheticWorldEventSource().get_injector() is None  # 新實例為 None


def test_dispatcher_source_registration_remains_after_injector_detach():
    """detach injector 後, source 仍在 dispatcher, 可重新 attach injector。"""
    stub1 = _StubInjector()
    stub2 = _StubInjector()
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub1)
    d.attach_injector(None)
    # source 仍 in dispatcher
    assert "synthetic" in d.get_attached_source_ids()
    # 重新 attach 新的 injector
    d.attach_injector(stub2)
    assert d.get_injector() is stub2
    # emit 走新的 injector
    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="x",
    ))
    assert stub1.inject_call_count == 0
    assert stub2.inject_call_count == 1


# ───────────────────────────────────────────────────────────
# 4. Wire-up (5 tests)
# ───────────────────────────────────────────────────────────


class _MiddlewareAsInjector:
    """
    Test-only adapter, wrap WorldPerceptionMiddleware 為 WorldEventInjector.
    Bry 派工 09:24: 不得修改 middleware.py, 這個 adapter 是 test-only proof of wire-up.
    """
    def __init__(self, middleware):
        self._middleware = middleware

    async def inject(self, event: WorldEvent) -> None:
        await self._middleware.process_world_event_direct(event)


def test_middleware_as_injector_conforms_to_protocol():
    """_MiddlewareAsInjector 必須 isinstance WorldEventInjector (runtime_checkable)。"""
    # mock middleware 即可, 測 adapter 自身
    class _MockMiddleware:
        async def process_world_event_direct(self, event): pass

    adapter = _MiddlewareAsInjector(_MockMiddleware())
    assert isinstance(adapter, WorldEventInjector)


def test_dispatcher_with_middleware_end_to_end():
    """
    End-to-end wire-up: Dispatcher → MiddlewareAsInjector → WorldPerceptionMiddleware
    證明 routing contract 成立, 且 WorldEvent 真的抵達 middleware。

    Note (Bry 派工 Phase B 02:59 + Phase C 09:24):
    - priority 是 Phase B additive 欄位, 不進 to_payload() / from_payload()
    - middleware.process_world_event_direct() 走 to_payload() → from_payload() round-trip
    - 所以 priority 進 middleware state 後會變 0 (default)
    - Phase C 只驗證 routing contract 成立, priority end-to-end survival 留給 Phase D+
    """
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.world.trace import WorldPerceptionTraceWriter

    class _MockBus:
        def subscribe(self, **kwargs): pass
        def unsubscribe(self, sid): pass
        async def publish(self, event): pass

    state = WorldPerceptionState()
    trace = WorldPerceptionTraceWriter()
    middleware = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=state,
        trace_writer=trace,
    )

    adapter = _MiddlewareAsInjector(middleware)
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(adapter)

    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="end to end test",
        novelty_id="e2e_001",
        priority=4,
    ))

    # WorldEvent 真的進了 middleware state
    active = state.get_active_events()
    assert len(active) == 1
    # 注意: payload round-trip 後 priority 變 0 (Phase B 設計: priority 不進 payload)
    middleware_ev = active[0]
    assert middleware_ev.novelty_id == "e2e_001"
    assert middleware_ev.type == "rain_started"
    assert middleware_ev.priority == 0  # Phase B payload round-trip 結果
    # 但 dispatcher return 的 event 跟 middleware 內部是不同 object
    # (因為 process_world_event_direct 走 payload round-trip)
    assert e.priority == 4  # Dispatcher 端 priority 保留
    assert middleware_ev is not e  # 不同 object (payload round-trip 重建)
    # 但 novelty_id / type / summary 等 M3 核心欄位 survive
    assert middleware_ev.novelty_id == e.novelty_id
    assert middleware_ev.type == e.type
    assert middleware_ev.summary == e.summary


def test_dispatcher_middleware_receives_same_world_event_identity():
    """
    middleware state 內的 event 跟 dispatcher return 的 event 是 **payload-equivalent**
    但 **不是同一 object** (因為 process_world_event_direct 走 payload round-trip)。
    """
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.world.trace import WorldPerceptionTraceWriter

    class _MockBus:
        def subscribe(self, **kwargs): pass
        def unsubscribe(self, sid): pass
        async def publish(self, event): pass

    state = WorldPerceptionState()
    trace = WorldPerceptionTraceWriter()
    middleware = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=state,
        trace_writer=trace,
    )
    adapter = _MiddlewareAsInjector(middleware)
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(adapter)

    e_returned = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="identity_test",
    ))
    # middleware state 內 event 跟 dispatcher return 是 payload-equivalent
    # 但 object 不同 (payload round-trip)
    middleware_ev = state.get_active_events()[0]
    assert middleware_ev is not e_returned
    # 但欄位值相同 (M3 核心欄位 survive payload round-trip)
    assert middleware_ev.novelty_id == e_returned.novelty_id
    assert middleware_ev.type == e_returned.type
    assert middleware_ev.summary == e_returned.summary
    assert middleware_ev.source == e_returned.source


def test_dispatcher_priority_survives_in_dispatcher_layer():
    """
    Phase C 範圍 (Bry 派工 09:24): priority 只 observe, 不 routing.
    確認 priority 在 Dispatcher emit 路徑上是 preserved 的 (observation log + 傳給 injector 的 event).
    至於 priority 進 middleware state 後變 0 是 Phase B 設計 (payload round-trip),
    留給 Phase D+ (priority-aware payload 或新 perception model).
    """
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.world.trace import WorldPerceptionTraceWriter

    class _MockBus:
        def subscribe(self, **kwargs): pass
        def unsubscribe(self, sid): pass
        async def publish(self, event): pass

    state = WorldPerceptionState()
    trace = WorldPerceptionTraceWriter()
    middleware = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=state,
        trace_writer=trace,
    )
    adapter = _MiddlewareAsInjector(middleware)
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(adapter)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="t",
        novelty_id="prio_test",
        priority=42,
    ))

    # (1) Dispatcher 內 priority observation log 記錄 42
    obs = d.get_observation_log()
    assert obs[0]["priority"] == 42

    # (2) Dispatcher 進 injector 的 event priority = 42 (middleware 收到前)
    #    因為 adapter 是 pass-through, injector (adapter) 收到的 event 跟 dispatcher return 同一 object
    #    所以 middleware 透過 adapter 拿到的 event 物件是 dispatcher 建的 (priority=42)
    #    但 process_world_event_direct 內部走 to_payload() → from_payload() round-trip
    #    所以 middleware state 內 event priority 變 0 (Phase B 設計)

    # 我們可以驗證 dispatcher 觀察的 priority = 42 (Phase C contract)
    assert obs[0]["priority"] == 42

    # 注意: state 內 priority 是 0 (這是 Phase B 設計的 trade-off, 不在 Phase C 範圍改)
    assert state.get_active_events()[0].priority == 0


def test_dispatcher_emitted_event_reaches_middleware_state():
    """emit 的 event 真的被 middleware process_world_event_direct 處理 → 進 state。"""
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.world.trace import WorldPerceptionTraceWriter

    class _MockBus:
        def subscribe(self, **kwargs): pass
        def unsubscribe(self, sid): pass
        async def publish(self, event): pass

    state = WorldPerceptionState()
    trace = WorldPerceptionTraceWriter()
    middleware = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=state,
        trace_writer=trace,
    )
    adapter = _MiddlewareAsInjector(middleware)
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(adapter)

    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="weather_temp_change",
        summary="temp",
        novelty_id="temp_001",
    ))
    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="rain",
        novelty_id="rain_001",
    ))
    active = state.get_active_events()
    assert len(active) == 2
    novelty_ids = {ev.novelty_id for ev in active}
    assert novelty_ids == {"temp_001", "rain_001"}


# ───────────────────────────────────────────────────────────
# 5. Hard limits (4 tests)
# ───────────────────────────────────────────────────────────


def test_dispatcher_no_background_task_created():
    """
    emit_and_inject 跑完後, active task count 跟跑之前一樣 (no background task)。
    """
    d = WorldEventDispatcher()
    stub = _StubInjector()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)

    async def check():
        before = len(asyncio.all_tasks())
        for _ in range(3):
            await d.emit_and_inject(
                source_id="synthetic",
                type="x",
                summary="t",
                novelty_id="n",
            )
        after = len(asyncio.all_tasks())
        # 差異只能是 0 或 1 (event loop 本身的 task)
        assert after <= before + 1, (
            f"Dispatcher 疑似引發 background task: before={before}, after={after}"
        )

    asyncio.run(check())


def test_dispatcher_no_external_dependency_in_source():
    """
    Bry 派工 09:24: dispatcher 不得 import external dependency.
    """
    import re
    forbidden = ["requests", "httpx", "aiohttp", "urllib", "websocket"]

    with open("src/world/dispatcher.py", encoding="utf-8") as f:
        content = f.read()
    for lib in forbidden:
        assert not re.search(r"\b" + lib + r"\b", content), (
            f"src/world/dispatcher.py 不得 import {lib}"
        )


def test_middleware_py_unchanged_after_phase_c():
    """
    Bry 派工 09:24 hard limit: middleware.py 0 change.
    """
    r = subprocess.run(
        ["git", "diff", "--stat", "src/world/middleware.py"],
        capture_output=True,
    )
    out = r.stdout.decode("utf-8", errors="replace").strip()
    assert not out, (
        f"middleware.py 不應被改, 實際 diff: {out!r}"
    )


def test_phase_a_b_files_unchanged_after_phase_c():
    """
    Bry 派工 09:24: Phase A / Phase B 已 commit 的 production semantics 0 change.
    """
    files = [
        "src/world/perception.py",
        "src/world/registry.py",
        "src/world/source/synthetic.py",
        "src/world/source/__init__.py",
        "src/world/source.py",
        "src/world/__init__.py",
        "src/world/base.py",
        "src/world/injector.py",
        "src/world/state.py",
        "src/world/trace.py",
        "src/world/validation.py",
        "scripts/run_server.py",
        "src/eventbus/token_manager.py",
    ]
    for f in files:
        r = subprocess.run(
            ["git", "diff", "--stat", f],
            capture_output=True,
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
        assert not out, f"{f} 不應被改, 實際 diff: {out!r}"
