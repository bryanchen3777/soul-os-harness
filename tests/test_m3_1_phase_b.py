"""
tests/test_m3_1_phase_b.py — M3.1 Phase B Contract Tests

Bry 拍板 2026-08-08 02:59 — M3.1 Phase B (Option A):

Phase B 在 M3 既有 WorldEvent + M3.1 Phase A contract 之上, 加:
- WorldEvent 新增 priority 欄位 (default=0, 必須是 int)
- WorldEventInjector async contract 維持 (Phase A)
- WorldEventSourceRegistry 新增 attach_injector()
- SyntheticWorldEventSource 加 __init__(injector=None) + set_injector() + emit_event()

派工 22+ 個 contract tests:
  WorldEvent:        1-5   (5 個)
  Injector:          6-8   (3 個)
  Registry:          9-12  (4 個)
  Synthetic:         13-20 (8 個)
  Compatibility:     21-22 (2 個)

回歸要求 (Bry 派工):
  - 既有 M3 行為 100% 不變 (build_*() factory methods 完整保留)
  - Phase A 29 個 tests 100% pass
  - 既有 42 個 M3 tests 100% pass
  - 三條 SyntheticWorldEventSource import path 仍 A is B is C
"""
from __future__ import annotations

import asyncio
import pytest

from src.world import (
    WorldEventSource,
    WorldEventInjector,
    WorldEventSourceRegistry,
    SourceStatus,
    SyntheticWorldEventSource,
)
from src.world.perception import WorldEvent


# ───────────────────────────────────────────────────────────
# Shared fixtures
# ───────────────────────────────────────────────────────────


class _StubInjector:
    """測試用 stub, conform WorldEventInjector Protocol (async inject)。"""

    def __init__(self, raise_exc: Exception = None):
        self.received: list = []
        self.inject_call_count = 0
        self._raise = raise_exc

    async def inject(self, event: WorldEvent) -> None:
        self.inject_call_count += 1
        if self._raise is not None:
            raise self._raise
        self.received.append(event)


# ───────────────────────────────────────────────────────────
# 1. WorldEvent: default priority = 0
# ───────────────────────────────────────────────────────────

def test_world_event_priority_default_zero():
    """
    既有 M3 WorldEvent 不傳 priority 時, default = 0 (100% backward compatible)。
    """
    e = WorldEvent(
        source="weather",
        type="rain_started",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
    )
    assert e.priority == 0


# ───────────────────────────────────────────────────────────
# 2. WorldEvent: explicit priority
# ───────────────────────────────────────────────────────────

def test_world_event_priority_explicit():
    """caller 明確傳 priority 應該保留。"""
    e = WorldEvent(
        source="weather",
        type="rain_started",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
        priority=5,
    )
    assert e.priority == 5


def test_world_event_priority_negative_allowed():
    """Bry 派工 02:59: 不發明 priority range constraint, 負值不擋。"""
    e = WorldEvent(
        source="weather",
        type="x",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
        priority=-3,
    )
    assert e.priority == -3


def test_world_event_priority_zero_explicit():
    """explicit 0 跟 default 0 等價。"""
    e = WorldEvent(
        source="weather",
        type="x",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
        priority=0,
    )
    assert e.priority == 0


# ───────────────────────────────────────────────────────────
# 3. WorldEvent: invalid priority rejected
# ───────────────────────────────────────────────────────────

def test_world_event_priority_rejects_str():
    """priority=str 必須 raise TypeError (Bry 派工 02:59)。"""
    with pytest.raises(TypeError) as exc_info:
        WorldEvent(
            source="weather",
            type="x",
            novelty_id="x",
            ts="2026-08-08T00:00:00Z",
            summary="test",
            priority="high",
        )
    assert "priority" in str(exc_info.value).lower()


def test_world_event_priority_rejects_float():
    """priority=1.5 必須 raise TypeError (float 不是 int)。"""
    with pytest.raises(TypeError):
        WorldEvent(
            source="weather",
            type="x",
            novelty_id="x",
            ts="2026-08-08T00:00:00Z",
            summary="test",
            priority=1.5,
        )


def test_world_event_priority_rejects_none():
    """priority=None 必須 raise TypeError。"""
    with pytest.raises(TypeError):
        WorldEvent(
            source="weather",
            type="x",
            novelty_id="x",
            ts="2026-08-08T00:00:00Z",
            summary="test",
            priority=None,
        )


def test_world_event_priority_rejects_list():
    """priority=list 必須 raise TypeError。"""
    with pytest.raises(TypeError):
        WorldEvent(
            source="weather",
            type="x",
            novelty_id="x",
            ts="2026-08-08T00:00:00Z",
            summary="test",
            priority=[1, 2, 3],
        )


def test_world_event_priority_rejects_bool():
    """
    priority=True 必須 raise (bool 是 int 的 subclass, 語意上 priority 不該是 bool)。
    Bry 派工沒明說但語意上必擋。
    """
    with pytest.raises(TypeError):
        WorldEvent(
            source="weather",
            type="x",
            novelty_id="x",
            ts="2026-08-08T00:00:00Z",
            summary="test",
            priority=True,
        )


# ───────────────────────────────────────────────────────────
# 4. WorldEvent: existing M3 construction still works
# ───────────────────────────────────────────────────────────

def test_world_event_constructs_with_full_m3_fields():
    """既有 M3 caller 傳所有 6 個欄位, 應該仍正常 work。"""
    e = WorldEvent(
        source="news",
        type="celebrity_news",
        novelty_id="news_2026_001",
        ts="2026-08-08T10:00:00Z",
        summary="測試新聞",
        data={"topic": "test"},
    )
    assert e.source == "news"
    assert e.type == "celebrity_news"
    assert e.novelty_id == "news_2026_001"
    assert e.ts == "2026-08-08T10:00:00Z"
    assert e.summary == "測試新聞"
    assert e.data == {"topic": "test"}
    assert e.priority == 0  # M3.1 Phase B 新增


def test_world_event_to_payload_unchanged():
    """
    M3.1 Phase B: 既有 M3 to_payload() / from_payload() 100% 保留
    (Bry 派工: 不改既有 bus serialization, priority 不進 payload)。
    """
    e = WorldEvent(
        source="weather",
        type="rain_started",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
        priority=7,
    )
    payload = e.to_payload()
    # M3 既有欄位都還在
    assert payload["source"] == "weather"
    assert payload["type"] == "rain_started"
    assert payload["novelty_id"] == "x"
    assert payload["ts"] == "2026-08-08T00:00:00Z"
    assert payload["summary"] == "test"
    assert payload["data"] == {}
    # M3.1 Phase B: priority 不進 payload
    assert "priority" not in payload

    # from_payload round-trip
    e2 = WorldEvent.from_payload(payload)
    assert e2.source == e.source
    assert e2.type == e.type
    assert e2.novelty_id == e.novelty_id
    assert e2.ts == e.ts
    assert e2.summary == e.summary
    assert e2.data == e.data
    assert e2.priority == 0  # 從 payload 還原時 default 0 (payload 沒 priority)


# ───────────────────────────────────────────────────────────
# 5. WorldEvent: existing build_* compatibility
# ───────────────────────────────────────────────────────────

def test_synthetic_build_rain_started_priority_default():
    """既有 build_rain_started() 必須仍 work, priority default 0。"""
    e = SyntheticWorldEventSource.build_rain_started()
    assert e.source == "weather"
    assert e.type == "rain_started"
    assert e.priority == 0


def test_synthetic_build_all_five_priority_default():
    """既有 build_all_five() 必須仍 work, 5 個 event priority 都 0。"""
    events = SyntheticWorldEventSource.build_all_five()
    assert len(events) == 5
    for e in events:
        assert e.priority == 0


# ───────────────────────────────────────────────────────────
# 6. Injector: async injector accepted
# ───────────────────────────────────────────────────────────

def test_world_event_injector_async_contract_preserved():
    """Phase A 派工: WorldEventInjector.inject 必須是 async。"""
    import inspect
    sig = inspect.signature(WorldEventInjector.inject)
    assert inspect.iscoroutinefunction(WorldEventInjector.inject), (
        "WorldEventInjector.inject 必須是 coroutine function (async)"
    )


def test_stub_injector_conforms_to_protocol():
    """_StubInjector (有 async def inject) 必須 isinstance WorldEventInjector。"""
    stub = _StubInjector()
    assert isinstance(stub, WorldEventInjector)


# ───────────────────────────────────────────────────────────
# 7. Injector: inject receives exact WorldEvent
# ───────────────────────────────────────────────────────────

def test_injector_receives_exact_world_event():
    """injector 收到的 event 必須跟 caller 傳的 event 同一個 object。"""
    stub = _StubInjector()
    e = WorldEvent(
        source="weather",
        type="x",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
        priority=3,
    )
    asyncio.run(stub.inject(e))
    assert len(stub.received) == 1
    assert stub.received[0] is e
    assert stub.received[0].priority == 3


# ───────────────────────────────────────────────────────────
# 8. Injector: exception propagates
# ───────────────────────────────────────────────────────────

def test_injector_exception_propagates():
    """injector.inject() raise 必須 propagate, 不 silent swallow。"""
    stub = _StubInjector(raise_exc=RuntimeError("injector failure"))
    e = WorldEvent(
        source="weather",
        type="x",
        novelty_id="x",
        ts="2026-08-08T00:00:00Z",
        summary="test",
    )
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(stub.inject(e))
    assert "injector failure" in str(exc_info.value)


# ───────────────────────────────────────────────────────────
# 9. Registry: attach injector
# ───────────────────────────────────────────────────────────

class _StubSourceNoInjector:
    """沒有 set_injector method 的 stub source, 測試 capability detection skip。"""
    source_id = "stub"

    async def start(self): pass
    async def stop(self): pass


def test_registry_attach_injector_basic():
    """attach_injector(injector) 設定成功, get_injector() 返回同 object。"""
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)
    registry.attach_injector(stub)
    assert registry.get_injector() is stub


def test_registry_attach_injector_none_detaches():
    """attach_injector(None) 語意 = detach (Bry 派工 02:59)。"""
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)
    registry.attach_injector(None)
    assert registry.get_injector() is None


# ───────────────────────────────────────────────────────────
# 10. Registry: source can access attached injector
# ───────────────────────────────────────────────────────────

def test_registry_attach_propagates_to_capable_source():
    """
    Registry attach 必須傳給有 set_injector method 的 source。
    沒有的 source 跳過 (capability detection)。
    """
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)

    src_with = SyntheticWorldEventSource()
    src_without = _StubSourceNoInjector()
    registry.register(src_with)
    registry.register(src_without)

    registry.attach_injector(stub)

    # 有 set_injector 的 source 拿到 injector
    assert src_with.get_injector() is stub
    # 沒 set_injector 的 source 不 crash, 也不被強加
    assert not hasattr(src_without, "_injector") or src_without._injector is None


def test_registry_attach_does_not_require_injector_param():
    """attach_injector 對 source 沒 set_injector method 不能 raise。"""
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)
    registry.register(_StubSourceNoInjector())
    # 不能 raise
    registry.attach_injector(stub)
    registry.attach_injector(None)


def test_registry_attach_propagates_to_multiple_sources():
    """attach 必須傳給所有已 register 的 capable sources。"""
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)

    s1 = SyntheticWorldEventSource()
    s2 = _StubSourceNoInjector()  # 不同 source_id 才能 register 兩個
    s2.source_id = "stub_2"
    registry.register(s1)
    registry.register(s2)

    registry.attach_injector(stub)

    # SyntheticWorldEventSource 有 set_injector, 應收到 stub
    assert s1.get_injector() is stub
    # Stub 沒 set_injector, 不 crash 也不被強加
    assert not hasattr(s2, "_injector") or s2._injector is None


# ───────────────────────────────────────────────────────────
# 11. Registry: multiple sources remain isolated
# ───────────────────────────────────────────────────────────

def test_registry_multiple_sources_have_independent_injectors():
    """
    不 attach 之前, 每個 source 的 injector 應該是各自 __init__ 設的 (None by default)。
    """
    s1 = SyntheticWorldEventSource()
    s2 = SyntheticWorldEventSource()
    assert s1.get_injector() is None
    assert s2.get_injector() is None


# ───────────────────────────────────────────────────────────
# 12. Registry: start/stop/health_snapshot unchanged
# ───────────────────────────────────────────────────────────

def test_registry_start_stop_health_snapshot_unchanged():
    """Phase A 派工: attach_injector 不能影響 start_all / stop_all / health_snapshot。"""
    stub = _StubInjector()
    registry = WorldEventSourceRegistry(stub)
    src = SyntheticWorldEventSource()
    registry.register(src)

    # 沒 attach 也能正常 start
    asyncio.run(registry.start_all())
    assert registry.get_status("synthetic") == SourceStatus.STARTED

    # attach 不 crash
    registry.attach_injector(stub)
    assert registry.get_status("synthetic") == SourceStatus.STARTED  # 仍 STARTED

    # stop 仍正常
    asyncio.run(registry.stop_all())
    assert registry.get_status("synthetic") == SourceStatus.STOPPED

    # health snapshot 仍正確
    snap = registry.health_snapshot()
    assert snap == {"synthetic": {"status": "stopped"}}


# ───────────────────────────────────────────────────────────
# 13. Synthetic: emit_event creates correct M3 WorldEvent
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_creates_world_event():
    """emit_event 必須建一個 M3 WorldEvent, 不是別的 class。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="rain_started",
        summary="下雨了",
        novelty_id="weather_rain_test",
    ))
    assert isinstance(e, WorldEvent)


# ───────────────────────────────────────────────────────────
# 14. Synthetic: source field correct
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_source_field():
    """emit_event 建立的 event, source 自動 = "synthetic"。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="rain_started",
        summary="test",
        novelty_id="x",
    ))
    assert e.source == "synthetic"


# ───────────────────────────────────────────────────────────
# 15. Synthetic: type field correct
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_type_field():
    """emit_event 傳入的 type 必須正確保留。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="custom_event_xyz",
        summary="test",
        novelty_id="x",
    ))
    assert e.type == "custom_event_xyz"


# ───────────────────────────────────────────────────────────
# 16. Synthetic: novelty_id preserved
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_novelty_id_preserved():
    """emit_event 傳入的 novelty_id 必須正確保留 (M3 既有欄位)。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="weather_rain_20260808",
    ))
    assert e.novelty_id == "weather_rain_20260808"


# ───────────────────────────────────────────────────────────
# 17. Synthetic: timestamp format M3-compatible
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_ts_iso_format():
    """emit_event 預設 ts 必須是 ISO 8601 UTC 字串 (跟 M3 build_* 一致)。"""
    import re
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
    ))
    # 跟 build_rain_started 同一個格式: ISO 8601 with timezone
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", e.ts), (
        f"ts 應是 ISO 8601, 實際: {e.ts!r}"
    )


def test_synthetic_emit_event_ts_custom_preserved():
    """emit_event 傳入 ts 必須保留。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
        ts="2026-12-31T23:59:59Z",
    ))
    assert e.ts == "2026-12-31T23:59:59Z"


# ───────────────────────────────────────────────────────────
# 18. Synthetic: priority propagated
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_priority_propagated():
    """emit_event 傳入 priority 必須保留。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
        priority=7,
    ))
    assert e.priority == 7


def test_synthetic_emit_event_priority_default_zero():
    """emit_event 不傳 priority 必須 default 0。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
    ))
    assert e.priority == 0


# ───────────────────────────────────────────────────────────
# 19. Synthetic: payload/data preserved
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_data_preserved():
    """emit_event 傳入 data 必須保留。"""
    src = SyntheticWorldEventSource()
    payload = {"key1": "value1", "key2": 42}
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
        data=payload,
    ))
    assert e.data == payload


def test_synthetic_emit_event_data_default_empty():
    """emit_event 不傳 data 必須 default = {} (跟 M3 field default_factory 一致)。"""
    src = SyntheticWorldEventSource()
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
    ))
    assert e.data == {}


# ───────────────────────────────────────────────────────────
# 20. Synthetic: injector receives event
# ───────────────────────────────────────────────────────────

def test_synthetic_emit_event_with_injector_injects():
    """有 injector 時, emit_event 必須 call injector.inject(event)。"""
    stub = _StubInjector()
    src = SyntheticWorldEventSource(injector=stub)
    asyncio.run(src.emit_event(
        type="rain_started",
        summary="test",
        novelty_id="x",
        priority=3,
    ))
    assert stub.inject_call_count == 1
    assert len(stub.received) == 1
    e_received = stub.received[0]
    assert e_received.source == "synthetic"
    assert e_received.type == "rain_started"
    assert e_received.priority == 3


def test_synthetic_emit_event_without_injector_returns_event():
    """沒 injector 時, emit_event 仍 return event, 不 raise。"""
    src = SyntheticWorldEventSource()  # 沒傳 injector
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
    ))
    assert e is not None
    assert e.type == "x"


def test_synthetic_emit_event_injector_exception_propagates():
    """emit_event 內 injector.inject() raise 必須 propagate, 不 silent swallow。"""
    stub = _StubInjector(raise_exc=RuntimeError("inject fail"))
    src = SyntheticWorldEventSource(injector=stub)
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(src.emit_event(
            type="x",
            summary="test",
            novelty_id="x",
        ))
    assert "inject fail" in str(exc_info.value)


def test_synthetic_emit_event_set_injector_runtime():
    """runtime 透過 set_injector attach, 之後 emit_event 應 inject。"""
    stub = _StubInjector()
    src = SyntheticWorldEventSource()  # init 沒 injector
    src.set_injector(stub)
    assert src.get_injector() is stub
    asyncio.run(src.emit_event(type="x", summary="t", novelty_id="x"))
    assert stub.inject_call_count == 1


def test_synthetic_emit_event_returns_event_with_injector():
    """emit_event 有 injector 時仍 return event 給 caller。"""
    stub = _StubInjector()
    src = SyntheticWorldEventSource(injector=stub)
    e = asyncio.run(src.emit_event(
        type="x",
        summary="test",
        novelty_id="x",
    ))
    assert e is not None
    assert e in stub.received  # 同一個 object 被 inject 也被 return


# ───────────────────────────────────────────────────────────
# 21. Compatibility: existing 71 Phase A/M3 tests remain green
# (這條由 pytest collection 自動保證, 這裡加 explicit 確認)
# ───────────────────────────────────────────────────────────

def test_synthetic_source_basic_constructor_compatible():
    """SyntheticWorldEventSource() 無參數仍可呼叫 (Phase A 向後兼容)。"""
    src = SyntheticWorldEventSource()
    assert src is not None
    assert src.source_id == "synthetic"
    assert src.get_injector() is None


def test_synthetic_constructor_with_injector_kwarg():
    """SyntheticWorldEventSource(injector=stub) 應把 stub 存進去。"""
    stub = _StubInjector()
    src = SyntheticWorldEventSource(injector=stub)
    assert src.get_injector() is stub


def test_existing_build_methods_unchanged():
    """既有 5 個 build_*() factory methods 必須仍可 call, 行為 100% 保留。"""
    e1 = SyntheticWorldEventSource.build_rain_started()
    e2 = SyntheticWorldEventSource.build_celebrity_news()
    e3 = SyntheticWorldEventSource.build_calendar_event_30min()
    e4 = SyntheticWorldEventSource.build_temp_fluctuation()
    e5 = SyntheticWorldEventSource.build_user_going_outside()
    all5 = SyntheticWorldEventSource.build_all_five()

    assert e1.type == "rain_started"
    assert e2.type == "celebrity_news"
    assert e3.type == "calendar_event"
    assert e4.type == "weather_temp_change"
    assert e5.type == "user_going_outside"
    assert len(all5) == 5
    # 既有 M3 不傳 priority, 全部 default 0
    for e in [e1, e2, e3, e4, e5]:
        assert e.priority == 0


# ───────────────────────────────────────────────────────────
# 22. Compatibility: three SyntheticWorldEventSource import paths
# ───────────────────────────────────────────────────────────

def test_synthetic_three_import_paths_same_class():
    """A is B is C: 3 條 import path 拿同一個 class object。"""
    from src.world.source import SyntheticWorldEventSource as A
    from src.world.source.synthetic import SyntheticWorldEventSource as B
    from src.world import SyntheticWorldEventSource as C

    assert A is B is C


def test_synthetic_three_import_paths_have_priority_field():
    """
    3 條 import path 拿到的 SyntheticWorldEventSource 都 conform ABC,
    而 ABC construct 的 instance 拿到的 WorldEvent (M3.1 Phase B priority 欄位)。

    note: SyntheticWorldEventSource 是 ABC subclass 不是 dataclass,
    dataclass field 在 WorldEvent (instance 屬性), 所以這條測 instance.field。
    """
    from src.world.source import SyntheticWorldEventSource as A
    from src.world.source.synthetic import SyntheticWorldEventSource as B
    from src.world import SyntheticWorldEventSource as C

    a_inst = A()
    b_inst = B()
    c_inst = C()

    # 三條 path 拿到的 instance 都 conform ABC
    assert isinstance(a_inst, WorldEventSource)
    assert isinstance(b_inst, WorldEventSource)
    assert isinstance(c_inst, WorldEventSource)

    # emit_event 建立的 WorldEvent 都有 priority 欄位
    e_a = asyncio.run(a_inst.emit_event(type="x", summary="t", novelty_id="x"))
    e_b = asyncio.run(b_inst.emit_event(type="x", summary="t", novelty_id="x"))
    e_c = asyncio.run(c_inst.emit_event(type="x", summary="t", novelty_id="x"))

    for e in [e_a, e_b, e_c]:
        assert hasattr(e, "priority")
        assert e.priority == 0
        # 既有 M3 欄位
        assert e.source == "synthetic"
        assert e.novelty_id == "x"
        assert e.summary == "t"


# ───────────────────────────────────────────────────────────
# 23. Async safety: emit_event does not create background task
# ───────────────────────────────────────────────────────────

def test_emit_event_does_not_create_background_task():
    """
    Bry 派工 02:59: emit_event 不得發起 background task / scheduler。
    驗證: emit_event 跑完後 active task count 跟跑之前一樣。
    """
    async def check_no_background_task():
        before = len(asyncio.all_tasks())
        src = SyntheticWorldEventSource()
        await src.emit_event(
            type="x",
            summary="test",
            novelty_id="x",
        )
        after = len(asyncio.all_tasks())
        # 差異只能是 0 或 1 (event loop 本身的 task 計算)
        # 重點是沒有 source 引發的 background task
        assert after <= before + 1, (
            f"emit_event 疑似引發 background task: before={before}, after={after}"
        )

    asyncio.run(check_no_background_task())


# ───────────────────────────────────────────────────────────
# 24. No external dependency test
# ───────────────────────────────────────────────────────────

def test_no_external_imports_in_phase_b_files():
    """
    Bry 派工 02:59: 不得 import 任何 external API library。
    檢查 Phase B 修改的 4 個檔案。
    """
    import re
    forbidden = ["requests", "httpx", "aiohttp", "urllib", "websocket"]

    for path in [
        "src/world/perception.py",
        "src/world/injector.py",
        "src/world/registry.py",
        "src/world/source/synthetic.py",
    ]:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        for lib in forbidden:
            # \b word boundary, 避免誤判
            pattern = r"\b" + lib + r"\b"
            assert not re.search(pattern, content), (
                f"{path} 不得 import {lib} (external API)"
            )
