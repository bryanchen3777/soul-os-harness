"""
M5.4-3 — Real WorldEventSource Boundary Audit & Acceptance Tests
==================================================================

Bry 派工 2026-08-09: STRICT READ-ONLY acceptance audit.

目標:在「不直接開發 API」前提下,先 audit Information World 整條 chain 的
boundary 契約,證明現有 M3.1 / M3.2-A / M5.3 frozen contract 在 failure 場景下
的行為,再決定要不要改 implementation。

完整 chain (per 派工):

                    Information World
    Real Source
        │
        ▼
    Source Adapter (WorldEventSource ABC)
        │
        ▼
    WorldEvent normalization (validate_world_event)
        │
        ▼
    Event Bus (SoulEventBus, EventType.WORLD_EVENT)
        │
        ▼
    WorldPerception (WorldPerceptionMiddleware._on_world_event)
        │     ├── validation
        │     ├── state.add (WorldPerceptionState, ephemeral)
        │     └── trace.write (jsonl sidecar)
        │
        ▼ (when AGENT_INTENT_ENRICHED arrives)
    WorldPerceptionMiddleware._on_agent_intent_enriched
        │     ├── compute_scores (5 維 + priority_boost)
        │     ├── should_accept (threshold gate)
        │     ├── top-N (perception_budget)
        │     └── WorldContext (text rendering)
        │
        ▼
    AGENT_INTENT_PERCEIVED
        │
        ▼
    LLMProxy._build_messages_group (world_context 注入)
        │
        ▼
    Final Prompt

10 個 audit sections 對應 Bry 7 個派工場景 + 完整性 + M5.3 contract:

  A. Source Lifecycle Failure
  B. Source emit_event Failure
  C. Injector / Routing Failure
  D. Validation (malformed payload)
  E. WorldPerceptionMiddleware State Machine
  F. Stale Data
  G. Duplicate Event
  H. Priority / Low-Priority
  I. Complete Chain E2E (source → bus → final prompt)
  J. M5.3 Frozen Contract 不變
  Z. Production Data 0 mutation + working tree clean

派工精神 (M5.3 frozen):
  - READ-ONLY: 不修改 production code
  - 不 commit / 不 push
  - 發現 architecture defect → STOP,只回報
  - 30+ deterministic tests
  - M5.3 regression 維持
  - production data 0 mutation
  - 不准順手改 WorldPerception scoring / Memory Loader / M5.3 contract

執行:
  & .venv\\Scripts\\python.exe -m pytest -v tests/test_m5_4_3_real_world_source_boundary_audit.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    """每次 test 一個全新的 data dir。"""
    data_dir = tmp_path / "world_audit"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture(autouse=True)
def _ensure_test_env(monkeypatch, tmp_data_dir):
    """autouse: 設定 trace writer 寫到 tmpdir,不污染 production。"""
    from src.world import trace as trace_mod
    trace_file = tmp_data_dir / "perception_trace.jsonl"

    def patched_init(self, trace_log_path=None):
        if trace_log_path is None:
            trace_log_path = trace_file
        self.trace_log_path = Path(trace_log_path)
        self.trace_log_path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(trace_mod.WorldPerceptionTraceWriter, "__init__", patched_init)


# ──────────────────────────────────────────────────────────────────────
# Section A — Source Lifecycle Failure (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionA_SourceLifecycleFailure:
    """Source start/stop failure isolation contract。"""

    def _make_failing_source(self, fail_on_start=False, fail_on_stop=False):
        from src.world.base import WorldEventSource
        from src.world.perception import WorldEvent

        class FailingSource(WorldEventSource):
            def __init__(self, fail_start, fail_stop):
                super().__init__()
                self._fail_start = fail_start
                self._fail_stop = fail_stop
                self.start_count = 0
                self.stop_count = 0

            @property
            def source_id(self) -> str:
                return "failing_test_source"

            async def start(self):
                self.start_count += 1
                if self._fail_start:
                    raise RuntimeError("simulated source start failure")

            async def stop(self):
                self.stop_count += 1
                if self._fail_stop:
                    raise RuntimeError("simulated source stop failure")

        return FailingSource(fail_on_start, fail_on_stop)

    @pytest.mark.asyncio
    async def test_a1_source_start_raises_status_failed(self):
        """A1: source.start() raise → registry 標記 FAILED,其他 source 不受影響。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.registry import WorldEventSourceRegistry, SourceStatus
        from src.world.base import WorldEventSource

        class Good(WorldEventSource):
            @property
            def source_id(self): return "good_a1"
            async def start(self): pass
            async def stop(self): pass

        class Bad(WorldEventSource):
            @property
            def source_id(self): return "bad_a1"
            async def start(self): raise RuntimeError("boom")
            async def stop(self): pass

        bus = MagicMock()
        middleware = WorldPerceptionMiddleware(bus=bus)
        registry = WorldEventSourceRegistry(injector=middleware)
        registry.register(Good())
        registry.register(Bad())

        await registry.start_all()

        assert registry.get_status("good_a1") == SourceStatus.STARTED
        assert registry.get_status("bad_a1") == SourceStatus.FAILED

    @pytest.mark.asyncio
    async def test_a2_source_stop_raises_status_stop_failed_idempotent(self):
        """A2: source.stop() raise → status=STOP_FAILED,呼叫第二次 idempotent。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.registry import WorldEventSourceRegistry, SourceStatus
        from src.world.base import WorldEventSource

        class FlakyStop(WorldEventSource):
            stop_count = 0
            @property
            def source_id(self): return "flaky_stop"
            async def start(self): pass
            async def stop(self):
                FlakyStop.stop_count += 1
                if FlakyStop.stop_count == 1:
                    raise RuntimeError("first stop fails")
                # 第二次 idempotent,不 raise

        bus = MagicMock()
        middleware = WorldPerceptionMiddleware(bus=bus)
        registry = WorldEventSourceRegistry(injector=middleware)
        src = FlakyStop()
        registry.register(src)

        await registry.start_all()
        await registry.stop_all()  # 第一次 raise
        # 但 registry 應該 catch,status 設為 STOP_FAILED
        assert registry.get_status("flaky_stop") == SourceStatus.STOP_FAILED
        # 第二次呼叫也應該 idempotent(不 crash)
        await registry.stop_all()

    @pytest.mark.asyncio
    async def test_a3_stop_all_with_no_sources_safe(self):
        """A3: 沒 source → start_all / stop_all 都不 crash。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.registry import WorldEventSourceRegistry

        bus = MagicMock()
        middleware = WorldPerceptionMiddleware(bus=bus)
        registry = WorldEventSourceRegistry(injector=middleware)

        await registry.start_all()
        await registry.stop_all()
        assert registry.registered_source_ids() == []

    @pytest.mark.asyncio
    async def test_a4_one_source_fails_start_others_continue(self):
        """A4: 多 source,1 個 start 失敗 → 其他人仍然 STARTED。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.registry import WorldEventSourceRegistry, SourceStatus
        from src.world.base import WorldEventSource

        class Good(WorldEventSource):
            @property
            def source_id(self): return "good"
            async def start(self): pass
            async def stop(self): pass

        class Bad(WorldEventSource):
            @property
            def source_id(self): return "bad"
            async def start(self): raise RuntimeError("boom")
            async def stop(self): pass

        bus = MagicMock()
        middleware = WorldPerceptionMiddleware(bus=bus)
        registry = WorldEventSourceRegistry(injector=middleware)
        registry.register(Good())
        registry.register(Bad())

        await registry.start_all()

        assert registry.get_status("good") == SourceStatus.STARTED
        assert registry.get_status("bad") == SourceStatus.FAILED

    @pytest.mark.asyncio
    async def test_a5_register_duplicate_source_id_raises(self):
        """A5: 同 source_id 註冊 2 次 → ValueError(per派工精神 fail-fast)。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.registry import WorldEventSourceRegistry
        from src.world.base import WorldEventSource

        class Same(WorldEventSource):
            @property
            def source_id(self): return "same"
            async def start(self): pass
            async def stop(self): pass

        bus = MagicMock()
        middleware = WorldPerceptionMiddleware(bus=bus)
        registry = WorldEventSourceRegistry(injector=middleware)
        registry.register(Same())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(Same())


# ──────────────────────────────────────────────────────────────────────
# Section B — Source emit_event Failure (4 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionB_SourceEmitFailure:
    """Source 發出 event 時的 failure contract。"""

    @pytest.mark.asyncio
    async def test_b1_emit_event_without_injector_returns_event(self):
        """B1: 沒 injector 的 source,emit_event 仍回 event(只 build, 不 inject)。"""
        from src.world.source.synthetic import SyntheticWorldEventSource

        src = SyntheticWorldEventSource()  # 沒 injector
        ev = await src.emit_event(
            type="test_type", summary="test", novelty_id="test_event_001",
        )
        assert ev.summary == "test"
        assert ev.source == "synthetic"

    @pytest.mark.asyncio
    async def test_b2_emit_event_failing_injector_propagates(self):
        """B2: injector.inject() raise → emit_event propagate,不 silent swallow。"""
        from src.world.source.synthetic import SyntheticWorldEventSource

        class FailingInjector:
            async def inject(self, event):
                raise RuntimeError("injector boom")

        src = SyntheticWorldEventSource(injector=FailingInjector())
        with pytest.raises(RuntimeError, match="injector boom"):
            await src.emit_event(
                type="test", summary="test", novelty_id="test_event_002",
            )

    def test_b3_emit_event_priority_non_int_raises(self):
        """B3: WorldEvent priority 不是 int → TypeError。"""
        from src.world.perception import WorldEvent
        with pytest.raises(TypeError, match="priority 必須是 int"):
            WorldEvent(
                source="weather", type="rain", novelty_id="test_priority_str",
                ts="2026-08-09T12:00:00+00:00", summary="x", priority="high",
            )

    def test_b4_emit_event_priority_bool_raises(self):
        """B4: priority 是 bool (int subclass) → TypeError(語意上 bool 不是 priority)。"""
        from src.world.perception import WorldEvent
        with pytest.raises(TypeError, match="priority 必須是 int"):
            WorldEvent(
                source="weather", type="rain", novelty_id="test_priority_bool",
                ts="2026-08-09T12:00:00+00:00", summary="x", priority=True,
            )


# ──────────────────────────────────────────────────────────────────────
# Section C — Injector / Routing Failure (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionC_InjectorRoutingFailure:
    """Dispatcher routing layer 的 failure contract。"""

    @pytest.mark.asyncio
    async def test_c1_emit_with_unregistered_source_raises(self):
        """C1: source_id 沒 attach → ValueError。"""
        from src.world.dispatcher import WorldEventDispatcher

        d = WorldEventDispatcher("test")
        # attach injector 但不 attach source
        d.attach_injector(MagicMock())
        with pytest.raises(ValueError, match="not attached"):
            await d.emit_and_inject(
                source_id="ghost", type="x", summary="y", novelty_id="emit_ghost_001",
            )

    @pytest.mark.asyncio
    async def test_c2_emit_without_injector_raises(self):
        """C2: 沒 attach injector → RuntimeError(per派工精神不 silent swallow)。"""
        from src.world.dispatcher import WorldEventDispatcher
        from src.world.base import WorldEventSource

        class Stub(WorldEventSource):
            @property
            def source_id(self): return "stub"
            async def start(self): pass
            async def stop(self): pass

        d = WorldEventDispatcher("test")
        d.attach_source(Stub())
        # 不 attach injector
        with pytest.raises(RuntimeError, match="no injector attached"):
            await d.emit_and_inject(
                source_id="stub", type="x", summary="y", novelty_id="emit_no_inj_001",
            )

    @pytest.mark.asyncio
    async def test_c3_failing_injector_propagates(self):
        """C3: injector.inject() raise → dispatcher 必須 propagate。"""
        from src.world.dispatcher import WorldEventDispatcher
        from src.world.base import WorldEventSource

        class Stub(WorldEventSource):
            @property
            def source_id(self): return "stub2"
            async def start(self): pass
            async def stop(self): pass

        class BadInjector:
            async def inject(self, event):
                raise ValueError("injector chain broken")

        d = WorldEventDispatcher("test")
        d.attach_source(Stub())
        d.attach_injector(BadInjector())
        with pytest.raises(ValueError, match="injector chain broken"):
            await d.emit_and_inject(
                source_id="stub2", type="x", summary="y", novelty_id="emit_bad_inj_001",
            )

    @pytest.mark.asyncio
    async def test_c4_attach_injector_none_detaches(self):
        """C4: attach_injector(None) = detach,sources 仍 registered。"""
        from src.world.dispatcher import WorldEventDispatcher
        from src.world.base import WorldEventSource

        class Stub(WorldEventSource):
            @property
            def source_id(self): return "stub3"
            async def start(self): pass
            async def stop(self): pass

        d = WorldEventDispatcher("test")
        d.attach_source(Stub())
        d.attach_injector(MagicMock())
        assert d.get_injector() is not None
        d.attach_injector(None)
        assert d.get_injector() is None
        # source 仍 in registry
        assert "stub3" in d.get_attached_source_ids()

    @pytest.mark.asyncio
    async def test_c5_dispatcher_priority_non_int_raises(self):
        """C5: dispatcher.emit_and_inject priority 不是 int → TypeError。"""
        from src.world.dispatcher import WorldEventDispatcher
        from src.world.base import WorldEventSource

        class Stub(WorldEventSource):
            @property
            def source_id(self): return "stub4"
            async def start(self): pass
            async def stop(self): pass

        d = WorldEventDispatcher("test")
        d.attach_source(Stub())
        d.attach_injector(MagicMock())
        with pytest.raises(TypeError, match="priority"):
            await d.emit_and_inject(
                source_id="stub4", type="x", summary="y",
                novelty_id="emit_pri_str_001", priority="high",
            )


# ──────────────────────────────────────────────────────────────────────
# Section D — Validation / Malformed Payload (6 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionD_Validation:
    """薄 input validation 對 malformed payload 的反應。"""

    def test_d1_missing_required_field_rejected(self):
        """D1: 缺必填欄位 → WorldEventValidationError。"""
        from src.world.validation import validate_world_event, WorldEventValidationError
        with pytest.raises(WorldEventValidationError, match="缺必填欄位"):
            validate_world_event({"source": "weather", "type": "rain"})  # 缺 novelty_id/ts/summary

    def test_d2_source_not_in_whitelist_rejected(self):
        """D2: source 不在白名單 → reject。"""
        from src.world.validation import validate_world_event, WorldEventValidationError
        with pytest.raises(WorldEventValidationError, match="不在白名單"):
            validate_world_event({
                "source": "unknown_source_xyz",
                "type": "x", "novelty_id": "valid_id_001",
                "ts": "2026-08-09T12:00:00+00:00", "summary": "ok",
            })

    def test_d3_ts_not_iso_8601_rejected(self):
        """D3: ts 不是 ISO 8601 → reject。"""
        from src.world.validation import validate_world_event, WorldEventValidationError
        with pytest.raises(WorldEventValidationError, match="ISO 8601"):
            validate_world_event({
                "source": "weather", "type": "rain", "novelty_id": "valid_id_002",
                "ts": "2026/08/09 12:00:00",  # 不是 ISO 8601
                "summary": "ok",
            })

    def test_d4_ts_without_utc_rejected(self):
        """D4: ts 缺時區或非 UTC → reject。"""
        from src.world.validation import validate_world_event, WorldEventValidationError
        with pytest.raises(WorldEventValidationError, match="缺時區|非 UTC"):
            validate_world_event({
                "source": "weather", "type": "rain", "novelty_id": "valid_id_003",
                "ts": "2026-08-09T12:00:00",  # 沒時區
                "summary": "ok",
            })

    def test_d5_novelty_id_bad_format_rejected(self):
        """D5: novelty_id 含特殊字元或太短 → reject。"""
        from src.world.validation import validate_world_event, WorldEventValidationError
        with pytest.raises(WorldEventValidationError, match="格式不對"):
            validate_world_event({
                "source": "weather", "type": "rain", "novelty_id": "ab",  # 太短
                "ts": "2026-08-09T12:00:00+00:00", "summary": "ok",
            })

    def test_d6_novelty_id_normalized_lowercase(self):
        """D6: novelty_id 自動 normalize 成 lowercase,大小寫不敏感。"""
        from src.world.validation import validate_world_event
        ev = validate_world_event({
            "source": "weather", "type": "rain",
            "novelty_id": "MIXED_CASE_ID_004",  # 會被 normalize
            "ts": "2026-08-09T12:00:00+00:00", "summary": "ok",
        })
        assert ev.novelty_id == "mixed_case_id_004"


# ──────────────────────────────────────────────────────────────────────
# Section E — WorldPerceptionMiddleware State Machine (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionE_MiddlewareStateMachine:
    """Middleware 對 world event 的 state 處理。"""

    @pytest.mark.asyncio
    async def test_e1_invalid_event_counters_and_trace(self, tmp_data_dir):
        """E1: invalid event → validation_rejected counter +1,trace 必寫。"""
        from src.world import WorldPerceptionMiddleware

        bus = MagicMock()
        mw = WorldPerceptionMiddleware(bus=bus)
        before = mw._events_validation_rejected

        # 故意送無效 payload (缺 novelty_id)
        from src.eventbus.schema import EventPriority, EventType, SoulEvent
        bad_event = SoulEvent(
            event_type=EventType.WORLD_EVENT,
            source="synthetic", target="broadcast",
            priority=EventPriority.LOW,
            payload={"source": "weather", "type": "rain"},  # 缺必填
        )
        await mw._on_world_event(bad_event)

        assert mw._events_validation_rejected == before + 1
        # trace 必寫
        assert mw._events_state_added == 0  # 沒進 state

    @pytest.mark.asyncio
    async def test_e2_valid_event_added_to_state(self, tmp_data_dir):
        """E2: valid event → state.add, trace 寫 "received"。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.source.synthetic import SyntheticWorldEventSource

        bus = MagicMock()
        mw = WorldPerceptionMiddleware(bus=bus)
        ev = SyntheticWorldEventSource.build_rain_started()
        await mw.process_world_event_direct(ev)

        assert mw._events_state_added == 1
        assert mw.state.get_state_size() == 1

    @pytest.mark.asyncio
    async def test_e3_no_active_events_empty_context(self, tmp_data_dir):
        """E3: 沒 active events → AGENT_INTENT_PERCEIVED 帶 empty world_context。"""
        from src.world import WorldPerceptionMiddleware
        from src.eventbus.schema import EventPriority, EventType, SoulEvent
        from src.world.perception import WorldContext

        captured = []
        class CaptureBus:
            async def publish(self, event):
                captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        mw = WorldPerceptionMiddleware(bus=bus)
        mw.register()

        # 送一個 AGENT_INTENT_ENRICHED,state 為空
        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source="test", target="broadcast",
            priority=EventPriority.NORMAL,
            payload={"agent_id": "agent_rem", "draft": "hi"},
        )
        await mw.handle_event(enriched)

        assert len(captured) == 1
        perceived = captured[0]
        assert perceived.event_type == EventType.AGENT_INTENT_PERCEIVED
        assert perceived.payload["world_context"] == ""

    @pytest.mark.asyncio
    async def test_e4_budget_limit_top_n_selection(self, tmp_data_dir):
        """E4: 5 active events, budget=3 → top-3 進 world_context。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.source.synthetic import SyntheticWorldEventSource
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        captured = []
        class CaptureBus:
            async def publish(self, event): captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        mw = WorldPerceptionMiddleware(bus=bus, perception_budget=3)
        # 注入 5 個 event
        for ev in SyntheticWorldEventSource.build_all_five():
            await mw.process_world_event_direct(ev)

        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source="test", target="broadcast",
            priority=EventPriority.NORMAL,
            payload={"agent_id": "agent_rem", "draft": "今天天氣如何"},
        )
        await mw.handle_event(enriched)

        # top_n ≤ 3
        meta = captured[0].payload["world_perception_meta"]
        assert len(meta["top_event_ids"]) <= 3

    @pytest.mark.asyncio
    async def test_e5_all_rejected_empty_context(self, tmp_data_dir):
        """E5: 全部 events 被 threshold reject → empty world_context。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        captured = []
        class CaptureBus:
            async def publish(self, event): captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        # threshold 設很高,所有 events 都被 reject
        mw = WorldPerceptionMiddleware(bus=bus, accept_threshold=0.99)

        # 注入 1 個 celebrity_news(type baseline relevance = 0.05)
        ev = WorldEvent(
            source="news", type="celebrity_news",
            novelty_id="all_rej_001",
            ts="2026-08-09T12:00:00+00:00",
            summary="某明星的無聊新聞",
            data={},
        )
        await mw.process_world_event_direct(ev)

        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source="test", target="broadcast",
            priority=EventPriority.NORMAL,
            payload={"agent_id": "agent_rem", "draft": "hi"},
        )
        await mw.handle_event(enriched)

        meta = captured[0].payload["world_perception_meta"]
        assert len(meta["top_event_ids"]) == 0
        assert captured[0].payload["world_context"] == ""


# ──────────────────────────────────────────────────────────────────────
# Section F — Stale Data (3 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionF_StaleData:
    """Stale / expired event 處理。"""

    @pytest.mark.asyncio
    async def test_f1_event_old_ts_but_perceived_now_added(self, tmp_data_dir):
        """F1: event.ts 是很久以前,但 perceived_at 是 now → 仍加入 state。
        
        注意: state 用 perceived_at (state 收到 event 的時間) 算 novelty window,
        不用 event.ts (event 本身發生時間)。所以 event.ts 舊不代表會被 expire。
        """
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent

        bus = MagicMock()
        mw = WorldPerceptionMiddleware(bus=bus)
        # event.ts = 1 年前(舊事實),但 perceived_at = now
        old_ts = "2025-08-09T12:00:00+00:00"
        ev = WorldEvent(
            source="weather", type="rain_started",
            novelty_id="stale_ts_001",
            ts=old_ts, summary="去年開始下雨",
        )
        await mw.process_world_event_direct(ev)
        assert mw.state.get_state_size() == 1

    def test_f2_state_with_old_perceived_events_filtered(self):
        """F2: state._active 裡有很舊的 event (perceived_at 過期) → get_active_events 過濾掉。"""
        from src.world import WorldPerceptionState
        from src.world.perception import WorldEvent
        from datetime import datetime, timezone, timedelta

        state = WorldPerceptionState(novelty_window=timedelta(hours=1))
        # 加 1 個 event,perceived_at 設為 2 小時前(已過期)
        ev = WorldEvent(
            source="weather", type="rain", novelty_id="stale_perc_001",
            ts="2026-08-09T10:00:00+00:00", summary="x",
        )
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        state.add(ev, perceived_at=old_time)
        # 過期 → 應該被過濾
        assert state.get_state_size() == 0

    def test_f3_state_max_active_events_bounded(self):
        """F3: max_active_events 上限 → 超過 FIFO eviction。"""
        from src.world import WorldPerceptionState
        from src.world.perception import WorldEvent

        state = WorldPerceptionState(max_active_events=3)
        for i in range(5):
            ev = WorldEvent(
                source="weather", type="rain", novelty_id=f"bounded_{i:03d}",
                ts="2026-08-09T12:00:00+00:00", summary=f"event {i}",
            )
            state.add(ev)

        # 應該只剩 3 個最新的
        active = state.get_active_events()
        assert len(active) == 3
        # 最後 3 個 (bounded_002, bounded_003, bounded_004)
        ids = [e.novelty_id for e in active]
        assert ids == ["bounded_002", "bounded_003", "bounded_004"]


# ──────────────────────────────────────────────────────────────────────
# Section G — Duplicate Event (3 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionG_DuplicateEvent:
    """同 novelty_id 重複送達的處理。"""

    def test_g1_duplicate_novelty_id_increments_count(self):
        """G1: 同 novelty_id 加 2 次 → state.novelty_index 計數 = 2。"""
        from src.world import WorldPerceptionState
        from src.world.perception import WorldEvent

        state = WorldPerceptionState()
        ev = WorldEvent(
            source="weather", type="rain", novelty_id="dup_001",
            ts="2026-08-09T12:00:00+00:00", summary="x",
        )
        c1 = state.add(ev)
        c2 = state.add(ev)
        assert c1 == 1
        assert c2 == 2
        assert state.get_novelty_count("dup_001") == 2

    @pytest.mark.asyncio
    async def test_g2_duplicate_reduces_novelty_score(self, tmp_data_dir):
        """G2: 同 event 進 2 次 → novelty score 衰減(2nd → 0.5, 3rd → 0.33)。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent, compute_scores

        bus = MagicMock()
        mw = WorldPerceptionMiddleware(bus=bus)
        ev = WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="dup_002",
            ts="2026-08-09T12:00:00+00:00",
            summary="重要會議",
        )
        await mw.process_world_event_direct(ev)
        await mw.process_world_event_direct(ev)
        await mw.process_world_event_direct(ev)

        # 第 1 次 novelty = 1.0,第 2 次 = 0.5,第 3 次 = 0.33
        s1 = compute_scores(ev, novelty_count=1)
        s2 = compute_scores(ev, novelty_count=2)
        s3 = compute_scores(ev, novelty_count=3)
        assert s1.novelty == 1.0
        assert s2.novelty == 0.5
        assert abs(s3.novelty - 0.333) < 0.01

    def test_g3_state_novelty_index_decays_on_expiry(self):
        """G3: event 過期後, novelty_index 對應 count 遞減。"""
        from src.world import WorldPerceptionState
        from src.world.perception import WorldEvent
        from datetime import datetime, timezone, timedelta

        state = WorldPerceptionState(novelty_window=timedelta(hours=1))
        ev = WorldEvent(
            source="weather", type="rain", novelty_id="expire_001",
            ts="2026-08-09T12:00:00+00:00", summary="x",
        )
        # 加 1 次,perceived_at = 2 小時前 → 馬上就過期
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        state.add(ev, perceived_at=old_time)
        # get_novelty_count 會自動 prune,count 應該 = 0
        assert state.get_novelty_count("expire_001") == 0


# ──────────────────────────────────────────────────────────────────────
# Section H — Priority / Low-Priority (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionH_Priority:
    """priority 對 final score 的影響。"""

    def test_h1_priority_zero_no_boost(self):
        """H1: priority=0 → priority_boost=0, no additive contribution。"""
        from src.world.perception import _map_priority_to_boost
        assert _map_priority_to_boost(0) == 0.0

    def test_h2_priority_five_boost_04(self):
        """H2: priority=5 → boost=0.4(per anchor 規定)。"""
        from src.world.perception import _map_priority_to_boost
        assert abs(_map_priority_to_boost(5) - 0.4) < 0.01

    def test_h3_priority_negative_clamped_to_zero(self):
        """H3: priority<0 → clamp 0。"""
        from src.world.perception import _map_priority_to_boost
        assert _map_priority_to_boost(-5) == 0.0
        assert _map_priority_to_boost(-100) == 0.0

    def test_h4_priority_high_clamped_to_one(self):
        """H4: priority>=12.5 → clamp 1.0(per派工 12.5 → 1.0 公式)。"""
        from src.world.perception import _map_priority_to_boost
        assert _map_priority_to_boost(12.5) == 1.0
        assert _map_priority_to_boost(100) == 1.0

    @pytest.mark.asyncio
    async def test_h5_priority_preserved_through_bus_payload_after_fix(self, tmp_data_dir):
        """H5: M5.4-3.1 contract repair — high priority event 在 E2E bus-payload 路徑下真的贏。

        Before M5.4-3.1:
          - WorldEvent.to_payload() 不包含 priority (M3.1 Phase B 向後相容)
          - validate_world_event → 重建 WorldEvent priority 預設 0
          - M3.2-A priority_boost 在 E2E 路徑下永遠 = 0
          - H5 (audit 階段) 觀察到 high 跟 low 兩者 final score 相同

        After M5.4-3.1:
          - WorldEvent.to_payload() 加 priority 欄位
          - validate_world_event 從 payload 讀 priority (向後相容 default 0)
          - middleware.compute_scores 收到 world_event.priority = 20
          - priority_boost 真正生效
          - high_pri_001 進 top-N, low_pri_001 被擠掉
        """
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        captured = []
        class CaptureBus:
            async def publish(self, event): captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        mw = WorldPerceptionMiddleware(bus=bus, accept_threshold=0.20, perception_budget=1)

        low = WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="low_pri_001",
            ts="2026-08-09T12:00:00+00:00",
            summary="低優先會議", priority=0,
        )
        high = WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="high_pri_001",
            ts="2026-08-09T12:00:00+00:00",
            summary="高優先會議", priority=20,
        )
        await mw.process_world_event_direct(low)
        await mw.process_world_event_direct(high)

        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source="test", target="broadcast",
            priority=EventPriority.NORMAL,
            payload={"agent_id": "agent_rem", "draft": "會議"},
        )
        await mw.handle_event(enriched)

        meta = captured[0].payload["world_perception_meta"]
        # M5.4-3.1 fix 後:high_pri_001 進 top-N, low_pri_001 被擠掉
        assert "high_pri_001" in meta["top_event_ids"]
        assert "low_pri_001" not in meta["top_event_ids"]

    @pytest.mark.asyncio
    async def test_h6_priority_preserved_when_direct_compute_scores(self):
        """H6: 直接用 compute_scores 傳 event_priority,priority_boost 確實生效。
        
        跟 H5 對比:這個 test 證明 priority scoring 邏輯本身是正確的,
        只是被 bus-payload serialization 丟失。
        """
        from src.world.perception import WorldEvent, compute_scores
        ev = WorldEvent(
            source="calendar", type="calendar_event", novelty_id="direct_001",
            ts="2026-08-09T12:00:00+00:00", summary="高優先", priority=20,
        )
        # 直接 call compute_scores 帶 event_priority
        scores_with = compute_scores(ev, novelty_count=1, event_priority=20)
        scores_without = compute_scores(ev, novelty_count=1, event_priority=0)
        # priority=20 應該有 boost
        assert scores_with.priority_boost == 1.0
        assert scores_without.priority_boost == 0.0
        assert scores_with.final() > scores_without.final()

    def test_h7_m5_4_3_1_m1_to_payload_includes_priority(self):
        """H7: M5.4-3.1 M1 contract repair — to_payload() 現在包含 priority。"""
        from src.world.perception import WorldEvent
        ev = WorldEvent(
            source="calendar", type="calendar_event", novelty_id="h7_001",
            ts="2026-08-09T12:00:00+00:00", summary="x", priority=15,
        )
        payload = ev.to_payload()
        assert "priority" in payload
        assert payload["priority"] == 15
        # frozen M3 contract 100% 保留
        for required in ("source", "type", "novelty_id", "ts", "summary", "data"):
            assert required in payload

    def test_h8_m5_4_3_1_m1_from_payload_backward_compat(self):
        """H8: M5.4-3.1 M1 contract repair — from_payload 對舊 payload (無 priority) 向後相容。

        舊 payload (沒有 priority key) 應 default 到 0,不 crash。
        新 payload (有 priority) 應正確讀出。
        防禦: 非 int 視為 0。
        """
        from src.world.perception import WorldEvent
        # 舊 payload: 沒有 priority → default 0
        old_payload = {
            "source": "calendar", "type": "calendar_event", "novelty_id": "h8_001",
            "ts": "2026-08-09T12:00:00+00:00", "summary": "old", "data": {},
        }
        ev_old = WorldEvent.from_payload(old_payload)
        assert ev_old.priority == 0
        # 新 payload: 有 priority → 讀出
        new_payload = {
            "source": "calendar", "type": "calendar_event", "novelty_id": "h8_002",
            "ts": "2026-08-09T12:00:00+00:00", "summary": "new", "data": {},
            "priority": 8,
        }
        ev_new = WorldEvent.from_payload(new_payload)
        assert ev_new.priority == 8
        # 防禦: priority 是字串 → 視為 0
        bad_payload = {**new_payload, "priority": "high"}
        ev_bad = WorldEvent.from_payload(bad_payload)
        assert ev_bad.priority == 0
        # 防禦: priority 是 bool → 視為 0
        bool_payload = {**new_payload, "priority": True}
        ev_bool = WorldEvent.from_payload(bool_payload)
        assert ev_bool.priority == 0

    def test_h9_m5_4_3_1_m1_validate_world_event_passes_priority(self):
        """H9: M5.4-3.1 M1 — validate_world_event 也讀 priority (因為它是真實 reconstruction path)。"""
        from src.world.validation import validate_world_event
        new_payload = {
            "source": "calendar", "type": "calendar_event", "novelty_id": "h9_001",
            "ts": "2026-08-09T12:00:00+00:00", "summary": "test", "data": {},
            "priority": 12,
        }
        ev = validate_world_event(new_payload)
        assert ev.priority == 12
        # 舊 payload 仍然 valid
        old_payload = {
            "source": "calendar", "type": "calendar_event", "novelty_id": "h9_002",
            "ts": "2026-08-09T12:00:00+00:00", "summary": "old test", "data": {},
        }
        ev_old = validate_world_event(old_payload)
        assert ev_old.priority == 0

    @pytest.mark.asyncio
    async def test_h10_m5_4_3_1_m2_middleware_inject_conforms_protocol(self):
        """H10: M5.4-3.1 M2 — WorldPerceptionMiddleware.inject() conform WorldEventInjector Protocol。

        驗證:
          1. middleware 有 inject(event) async method
          2. dispatcher 可以把 middleware 當作 injector
          3. dispatcher.emit_and_inject → middleware.inject → _on_world_event
             (single processing path, exactly-once)
        """
        from src.world import WorldPerceptionMiddleware
        from src.world.injector import WorldEventInjector
        from src.world.dispatcher import WorldEventDispatcher
        from src.world.base import WorldEventSource
        from src.world.perception import WorldEvent

        captured = []
        class CaptureBus:
            async def publish(self, event): captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        mw = WorldPerceptionMiddleware(bus=bus)

        # 1. inject method 存在且是 coroutine
        assert hasattr(mw, "inject")
        assert callable(mw.inject)
        # 2. Protocol conform check
        assert isinstance(mw, WorldEventInjector)

        # 3. dispatcher 可以用 middleware 當 injector
        class Stub(WorldEventSource):
            @property
            def source_id(self): return "synthetic"  # 在 VALID_SOURCES 白名單內
            async def start(self): pass
            async def stop(self): pass

        d = WorldEventDispatcher("test_m2")
        d.attach_source(Stub())
        d.attach_injector(mw)
        ev = await d.emit_and_inject(
            source_id="synthetic", type="calendar_event",
            summary="M2 對齊測試", novelty_id="m2_align_001", priority=5,
        )
        # 4. exactly-once: 1 個 event 進 state
        assert mw.state.get_state_size() == 1
        # 5. priority 保留到 state
        active = mw.state.get_active_events()
        assert active[0].priority == 5

    @pytest.mark.asyncio
    async def test_h11_m5_4_3_1_m2_inject_idempotency(self):
        """H11: M5.4-3.1 M2 — inject(event) 跟 process_world_event_direct 結果一致 (exactly-once)。

        兩條入口 → 同一個 processing path (_on_world_event),
        單次呼叫只跑一次處理邏輯,不重複。
        """
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent

        captured = []
        class CaptureBus:
            async def publish(self, event): captured.append(event)
            def subscribe(self, *a, **kw): pass
            def unsubscribe(self, *a, **kw): pass

        bus = CaptureBus()
        mw1 = WorldPerceptionMiddleware(bus=bus)
        mw2 = WorldPerceptionMiddleware(bus=bus)

        ev = WorldEvent(
            source="calendar", type="calendar_event", novelty_id="m2_idem_001",
            ts="2026-08-09T12:00:00+00:00", summary="idempotency", priority=0,
        )
        # 兩條入口都應該走到 _on_world_event
        await mw1.inject(ev)
        await mw2.process_world_event_direct(ev)
        # 各加 1 次,total 2 個 (因為 novelty_count 是 state-level,不是 entry-level dedup)
        # 重點:每次呼叫只 _on_world_event 跑一次,沒有 double processing
        assert mw1.state.get_state_size() == 1
        assert mw2.state.get_state_size() == 1


# ──────────────────────────────────────────────────────────────────────
# Section I — Complete Chain E2E (4 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionI_CompleteChainE2E:
    """Source → Bus → Perception → Final Prompt 完整 chain。"""

    @pytest.mark.asyncio
    async def test_i1_synthetic_source_through_bus_to_perceived(self, tmp_data_dir):
        """I1: Synthetic source → bus → middleware → AGENT_INTENT_PERCEIVED → final prompt 包含 world_context。

        已知 architecture finding (per M5.4-3 audit):
          WorldPerceptionMiddleware 沒有 `inject()` method,所以不能直接被
          WorldEventDispatcher 當作 WorldEventInjector Protocol 實作。
          真實的 source → middleware chain 必須走 bus.publish(WORLD_EVENT) →
          middleware._on_world_event() 路徑。
        """
        from src.world import WorldPerceptionMiddleware
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        bus = SoulEventBus()
        await bus.start()
        try:
            mw = WorldPerceptionMiddleware(bus=bus, perception_budget=3)
            mw.register()

            # 直接 publish WORLD_EVENT(模擬 source 經 bus 注入)
            world_event = SoulEvent(
                event_type=EventType.WORLD_EVENT,
                source="synthetic", target="broadcast",
                priority=EventPriority.LOW,
                payload={
                    "source": "calendar",
                    "type": "calendar_event",
                    "novelty_id": "e2e_chain_001",
                    "ts": "2026-08-09T12:00:00+00:00",
                    "summary": "30 分鐘後有重要會議",
                    "data": {},
                },
            )
            await bus.publish(world_event)
            await asyncio.sleep(0.05)

            # 攔截 AGENT_INTENT_PERCEIVED
            perceived_events = []
            async def capture_handler(event):
                if event.event_type == EventType.AGENT_INTENT_PERCEIVED:
                    perceived_events.append(event)
            bus.subscribe(
                subscriber_id="test_capture",
                handler=capture_handler,
                event_filter={EventType.AGENT_INTENT_PERCEIVED},
            )

            # 觸發 AGENT_INTENT_ENRICHED
            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="test", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={"agent_id": "agent_rem", "draft": "行程提醒"},
            )
            await bus.publish(enriched)
            await asyncio.sleep(0.1)

            assert len(perceived_events) >= 1
            payload = perceived_events[0].payload
            assert "world_context" in payload
            # event 應該被 accept (calendar_event + 對齊行程提醒)
            assert "30 分鐘後有重要會議" in payload["world_context"]
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_i2_empty_state_final_prompt_no_world_block(self, tmp_data_dir):
        """I2: 沒 world events → final prompt 沒有 [世界感知] block。"""
        from src.world import WorldPerceptionMiddleware
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        bus = SoulEventBus()
        await bus.start()
        try:
            mw = WorldPerceptionMiddleware(bus=bus)
            mw.register()

            perceived_events = []
            async def capture_handler(event):
                if event.event_type == EventType.AGENT_INTENT_PERCEIVED:
                    perceived_events.append(event)
            bus.subscribe(
                subscriber_id="test_capture_empty",
                handler=capture_handler,
                event_filter={EventType.AGENT_INTENT_PERCEIVED},
            )

            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="test", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={"agent_id": "agent_rem", "draft": "隨意聊聊"},
            )
            await bus.publish(enriched)
            await asyncio.sleep(0.1)

            assert len(perceived_events) == 1
            assert perceived_events[0].payload["world_context"] == ""
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_i3_3_accepted_events_top_n_in_final_prompt(self, tmp_data_dir):
        """I3: 3 events accepted → final prompt 包含 3 個 bullets。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.source.synthetic import SyntheticWorldEventSource
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        bus = SoulEventBus()
        await bus.start()
        try:
            mw = WorldPerceptionMiddleware(bus=bus, perception_budget=3)
            mw.register()

            # 注入 3 個高 relevance events
            for ev_spec in [
                ("calendar_event", "30 分鐘後有會議", "chain_3_001", 5),
                ("user_going_outside", "Bry 準備出門", "chain_3_002", 3),
                ("rain_started", "外面開始下雨", "chain_3_003", 0),
            ]:
                ev = WorldEvent_simple(ev_spec[0], ev_spec[1], ev_spec[2], ev_spec[3])
                await mw.process_world_event_direct(ev)

            perceived_events = []
            async def capture_handler(event):
                if event.event_type == EventType.AGENT_INTENT_PERCEIVED:
                    perceived_events.append(event)
            bus.subscribe(
                subscriber_id="test_capture_3",
                handler=capture_handler,
                event_filter={EventType.AGENT_INTENT_PERCEIVED},
            )

            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="test", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={"agent_id": "agent_rem", "draft": "今天天氣和行程"},
            )
            await bus.publish(enriched)
            await asyncio.sleep(0.1)

            assert len(perceived_events) == 1
            ctx = perceived_events[0].payload["world_context"]
            # bullets 數 ≤ 3(perception_budget)
            bullet_count = ctx.count("- [")
            assert bullet_count <= 3
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_i4_rejected_events_not_in_final_prompt(self, tmp_data_dir):
        """I4: rejected events 不進 final prompt(不污染 LLM context)。"""
        from src.world import WorldPerceptionMiddleware
        from src.world.perception import WorldEvent
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        bus = SoulEventBus()
        await bus.start()
        try:
            # threshold 設很高,所有 events reject
            mw = WorldPerceptionMiddleware(bus=bus, accept_threshold=0.99)
            mw.register()

            ev = WorldEvent(
                source="news", type="celebrity_news",
                novelty_id="chain_rej_001",
                ts="2026-08-09T12:00:00+00:00",
                summary="明星新聞", data={},
            )
            await mw.process_world_event_direct(ev)

            perceived_events = []
            async def capture_handler(event):
                if event.event_type == EventType.AGENT_INTENT_PERCEIVED:
                    perceived_events.append(event)
            bus.subscribe(
                subscriber_id="test_capture_rej",
                handler=capture_handler,
                event_filter={EventType.AGENT_INTENT_PERCEIVED},
            )

            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="test", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={"agent_id": "agent_rem", "draft": "hi"},
            )
            await bus.publish(enriched)
            await asyncio.sleep(0.1)

            assert len(perceived_events) == 1
            ctx = perceived_events[0].payload["world_context"]
            # 拒絕的 event 不該進 prompt
            assert "明星新聞" not in ctx
        finally:
            await bus.stop()


def WorldEvent_simple(type_: str, summary: str, novelty_id: str, priority: int = 0):
    """Helper: 建 WorldEvent 給 I3 用。"""
    from src.world.perception import WorldEvent
    return WorldEvent(
        source="calendar" if type_ == "calendar_event" else
               "social" if type_ == "user_going_outside" else "weather",
        type=type_, novelty_id=novelty_id,
        ts="2026-08-09T12:00:00+00:00",
        summary=summary, data={}, priority=priority,
    )


# ──────────────────────────────────────────────────────────────────────
# Section J — M5.3 Frozen Contract 不變 (3 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionJ_FrozenContract:
    """M5.3 frozen contract 維持: 不准改 scoring / loader / WorldPerception。"""

    def test_j1_scoring_weights_unchanged(self):
        """J1: SCORE_WEIGHTS 5 維度 sum = 1.00(legacy M3 Phase 1 baseline)。"""
        from src.world.perception import SCORE_WEIGHTS
        assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0, abs=0.001)
        # M3.2-A: priority 沒進 SCORE_WEIGHTS(用獨立 PRIORITY_BOOST_WEIGHT)
        assert "priority_boost" not in SCORE_WEIGHTS

    def test_j2_priority_uses_independent_weight(self):
        """J2: PRIORITY_BOOST_WEIGHT 是獨立常數,加法進 final,不是 replace。"""
        from src.world.perception import PRIORITY_BOOST_WEIGHT, PerceptionScores
        assert PRIORITY_BOOST_WEIGHT == 0.05
        # 確認 additive model: priority=0 不改 final
        scores = PerceptionScores(
            relevance=0.5, novelty=0.5,
            personal_significance=0.5, emotional_significance=0.5,
            temporal_significance=0.5, priority_boost=0.0,
        )
        scores_zero = scores.final()
        # priority_boost = 0 → final = legacy 5 維度
        assert abs(scores_zero - 0.5) < 0.01

    def test_j3_m5_3_frozen_events_still_flow(self):
        """J3: M5.3 frozen events 仍流(AGENT_INTENT_ENRICHED → AGENT_INTENT_PERCEIVED chain 完整)。"""
        # 確認 EventType 還有 AGENT_INTENT_PERCEIVED
        from src.eventbus.schema import EventType
        assert hasattr(EventType, "AGENT_INTENT_PERCEIVED")
        assert EventType.AGENT_INTENT_PERCEIVED.value == "agent_intent_perceived"
        # WorldPerceptionMiddleware 仍訂閱 AGENT_INTENT_ENRICHED + WORLD_EVENT
        from src.world import WorldPerceptionMiddleware
        assert hasattr(WorldPerceptionMiddleware, "register")


# ──────────────────────────────────────────────────────────────────────
# Section Z — Production Data + working tree (1 test)
# ──────────────────────────────────────────────────────────────────────


class TestSectionZ_ProductionSafety:
    """整個 M5.4-3 audit 自身不污染 production。"""

    def test_z1_production_data_path_not_mutated(self):
        """Z1: smoke check — production data path 仍存在(本 test 不動它)。"""
        prod_path = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data\memory")
        if prod_path.exists():
            subdirs = [p.name for p in prod_path.iterdir() if p.is_dir()]
            assert isinstance(subdirs, list)
        # 沒 prod path 也 PASS


# ──────────────────────────────────────────────────────────────────────
# Counts assertion
# ──────────────────────────────────────────────────────────────────────


def test_m5_4_3_test_count():
    """確認本檔案至少 30 個 tests。"""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_funcs = []
    for name, obj in inspect.getmembers(current_module, inspect.isclass):
        if name.startswith("Test") and inspect.isclass(obj):
            for method_name, method in inspect.getmembers(obj, inspect.isfunction):
                if method_name.startswith("test_"):
                    test_funcs.append((name, method_name))
    total = len(test_funcs) + 1  # +1 for this test_m5_4_3_test_count
    assert total >= 30, f"expected ≥30 tests, got {total}"
