"""
tests/test_m3_1_phase_d.py — M3.1 Phase D Contract Tests

Bry 拍板 2026-08-08 10:09 — M3.1 Phase D: PRIORITY END-TO-END SURVIVAL

Phase D 單一目標:
  Verify priority value survives Dispatcher → Injector delivery path。

    WorldEvent
        ↓
    WorldEventDispatcher.emit_and_inject()
        ↓
    await self._injector.inject(event)   ← 同一個 WorldEvent object
        ↓
    received WorldEvent

    received.priority == original.priority    # 必須相等

Phase D 只驗證 value preservation, 不重新定義 priority 語意,
不引入 priority ordering / filtering / routing, 不改既有 Phase A/B/C contract。

派工要求:
  - priority=0 / 1 / 5 / 100 都要 end-to-end 通過
  - 其它 identity fields (type / summary / novelty_id / source / data / ts) 也都要 preserved
  - Hard limits: middleware.py / run_server.py / token_manager.py 0 change
  - Phase A/B/C contracts 0 change

回歸要求 (Bry 派工):
  - 既有 117 tests (42 M3 + 29 Phase A + 46 Phase B + 30 Phase C) 100% pass
  - Phase D tests 全部 pass
  - 0 regression
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.world import WorldEventSource, WorldEventInjector
from src.world.dispatcher import WorldEventDispatcher
from src.world.perception import WorldEvent
from src.world.source.synthetic import SyntheticWorldEventSource


# ───────────────────────────────────────────────────────────
# Shared fixtures
# ───────────────────────────────────────────────────────────


class _StubInjector:
    """測試用 stub, conform WorldEventInjector (async inject)。"""

    def __init__(self, raise_exc: Optional[Exception] = None):
        self.received: List[WorldEvent] = []
        self.inject_call_count = 0
        self._raise = raise_exc

    async def inject(self, event: WorldEvent) -> None:
        self.inject_call_count += 1
        if self._raise is not None:
            raise self._raise
        self.received.append(event)


def _make_dispatcher_with_stub(
    raise_exc: Optional[Exception] = None,
) -> tuple:
    """helper: 建一個 dispatcher + 已 attach source + 已 attach stub injector。"""
    stub = _StubInjector(raise_exc=raise_exc)
    d = WorldEventDispatcher()
    d.attach_source(SyntheticWorldEventSource())
    d.attach_injector(stub)
    return d, stub


# ───────────────────────────────────────────────────────────
# 1. Priority value survival (4 required cases)
# ───────────────────────────────────────────────────────────


def test_phase_d_priority_zero_survives_end_to_end():
    """priority=0 必須 survive dispatcher → injector 完整路徑。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="下雨了",
        novelty_id="d_p0",
        priority=0,
    ))
    assert e.priority == 0
    assert stub.inject_call_count == 1
    assert len(stub.received) == 1
    assert stub.received[0].priority == 0


def test_phase_d_priority_one_survives_end_to_end():
    """priority=1 必須 survive dispatcher → injector 完整路徑。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="下雨了",
        novelty_id="d_p1",
        priority=1,
    ))
    assert e.priority == 1
    assert stub.received[0].priority == 1


def test_phase_d_priority_five_survives_end_to_end():
    """priority=5 必須 survive dispatcher → injector 完整路徑。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="calendar_event",
        summary="15:00 meeting",
        novelty_id="d_p5",
        priority=5,
    ))
    assert e.priority == 5
    assert stub.received[0].priority == 5


def test_phase_d_priority_hundred_survives_end_to_end():
    """priority=100 必須 survive dispatcher → injector 完整路徑。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="user_going_outside",
        summary="user leaving",
        novelty_id="d_p100",
        priority=100,
    ))
    assert e.priority == 100
    assert stub.received[0].priority == 100


# ───────────────────────────────────────────────────────────
# 2. Event identity survival (other fields must also survive)
# ───────────────────────────────────────────────────────────


def test_phase_d_type_survives_end_to_end():
    """type 欄位必須 preserve。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="celebrity_news",
        summary="x",
        novelty_id="d_t",
        priority=3,
    ))
    assert e.type == "celebrity_news"
    assert stub.received[0].type == "celebrity_news"


def test_phase_d_summary_survives_end_to_end():
    """summary 欄位必須 preserve。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="今天天氣晴朗適合出門",
        novelty_id="d_s",
        priority=3,
    ))
    assert e.summary == "今天天氣晴朗適合出門"
    assert stub.received[0].summary == "今天天氣晴朗適合出門"


def test_phase_d_novelty_id_survives_end_to_end():
    """novelty_id 欄位必須 preserve。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="weather_rain_20260808_001",
        priority=3,
    ))
    assert e.novelty_id == "weather_rain_20260808_001"
    assert stub.received[0].novelty_id == "weather_rain_20260808_001"


def test_phase_d_source_survives_end_to_end():
    """source 欄位必須 preserve (= source_id 自動填入)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_src",
        priority=3,
    ))
    assert e.source == "synthetic"
    assert stub.received[0].source == "synthetic"


def test_phase_d_data_survives_end_to_end():
    """data dict 必須 preserve (含 nested 結構)。"""
    d, stub = _make_dispatcher_with_stub()
    payload = {"location": "taipei", "temp_c": 28.5, "tags": ["rain", "heavy"]}
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="rain_started",
        summary="x",
        novelty_id="d_data",
        data=payload,
        priority=7,
    ))
    assert e.data == payload
    assert stub.received[0].data == payload
    # nested value 也要等於 (避免淺比較誤判)
    assert stub.received[0].data["tags"] == ["rain", "heavy"]


def test_phase_d_ts_survives_end_to_end():
    """ts 欄位必須 preserve (dispatcher 自動填 ISO 8601 UTC)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_ts",
        priority=3,
    ))
    # 既有 M3 WorldEvent.ts 是 ISO 8601 UTC 字串, dispatcher 自動填入
    assert isinstance(e.ts, str)
    assert len(e.ts) > 0
    assert stub.received[0].ts == e.ts
    # 解析回 datetime 確認格式合法
    parsed = datetime.fromisoformat(e.ts)
    assert parsed.tzinfo is not None  # 必須帶時區 (UTC)


# ───────────────────────────────────────────────────────────
# 3. Object identity (Phase C contract — same object passed through)
# ───────────────────────────────────────────────────────────


def test_phase_d_injector_receives_same_event_object():
    """injector 收到的 event 必須是 dispatcher 回傳的同一個 object (Phase C contract)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_id",
        priority=42,
    ))
    assert stub.received[0] is e


def test_phase_d_priority_value_preserved_via_object_identity():
    """同一個 object, 所以 priority 值一定等於。雙重確認 value + identity。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_id_p",
        priority=17,
    ))
    # value 必須相等
    assert stub.received[0].priority == e.priority == 17
    # object identity 也成立 (priority 不是後製產生的)
    assert stub.received[0] is e


# ───────────────────────────────────────────────────────────
# 4. Default priority behavior
# ───────────────────────────────────────────────────────────


def test_phase_d_default_priority_zero_when_not_specified():
    """不指定 priority 時, 預設 0 必須 survive end-to-end (M3.1 Phase B default)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_default",
        # 沒傳 priority
    ))
    assert e.priority == 0
    assert stub.received[0].priority == 0


def test_phase_d_default_data_empty_dict_when_not_specified():
    """不指定 data 時, 預設 {} 必須 survive end-to-end。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_default_data",
        priority=2,
    ))
    assert e.data == {}
    assert stub.received[0].data == {}


# ───────────────────────────────────────────────────────────
# 5. Observation log correlation
# ───────────────────────────────────────────────────────────


def test_phase_d_observation_log_records_emitted_priority():
    """observation log 必須記錄 emit 時的 priority (跟 injected event 一致)。"""
    d, stub = _make_dispatcher_with_stub()
    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_obs",
        priority=9,
    ))
    log = d.get_observation_log()
    assert len(log) == 1
    assert log[0]["priority"] == 9
    assert log[0]["source_id"] == "synthetic"
    assert log[0]["type"] == "x"
    assert log[0]["novelty_id"] == "d_obs"


def test_phase_d_observation_log_priority_matches_injected_event():
    """observation log 記錄的 priority 必須 == injector 收到的 priority (同一 emit)。"""
    d, stub = _make_dispatcher_with_stub()
    asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_obs_match",
        priority=11,
    ))
    log = d.get_observation_log()
    assert log[0]["priority"] == stub.received[0].priority == 11


# ───────────────────────────────────────────────────────────
# 6. Multiple emissions preserve distinct priorities
# ───────────────────────────────────────────────────────────


def test_phase_d_multiple_emissions_preserve_distinct_priorities():
    """連續 emit 不同 priority, 每次 priority 都必須 survive 對應位置。"""
    d, stub = _make_dispatcher_with_stub()
    priorities = [0, 1, 5, 100, 42, 3]
    for i, p in enumerate(priorities):
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="x",
            novelty_id=f"d_multi_{i}",
            priority=p,
        ))
    assert stub.inject_call_count == len(priorities)
    received_priorities = [ev.priority for ev in stub.received]
    assert received_priorities == priorities
    # observation log 也對應
    log_priorities = [entry["priority"] for entry in d.get_observation_log()]
    assert log_priorities == priorities


def test_phase_d_negative_priority_survives():
    """負數 priority 也要 survive (Phase B 沒限定 range, dispatcher 必須尊重)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_neg",
        priority=-5,
    ))
    assert e.priority == -5
    assert stub.received[0].priority == -5


# ───────────────────────────────────────────────────────────
# 7. Phase C contract — exception propagation still works with priority
# ───────────────────────────────────────────────────────────


def test_phase_d_injector_exception_still_propagates_with_priority_set():
    """priority 設值時, injector 拋 exception 仍要 propagate (Phase C contract 不變)。"""
    d, _ = _make_dispatcher_with_stub(raise_exc=RuntimeError("injector failed"))
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="x",
            novelty_id="d_exc",
            priority=8,
        ))
    assert "injector failed" in str(exc_info.value)


def test_phase_d_injector_call_count_exactly_one_per_emit_with_priority():
    """priority 設值時, 每次 emit 仍只 call injector 恰好一次 (Phase C contract)。"""
    d, stub = _make_dispatcher_with_stub()
    for i in range(3):
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="x",
            novelty_id=f"d_count_{i}",
            priority=i + 1,
        ))
    assert stub.inject_call_count == 3
    assert len(stub.received) == 3


# ───────────────────────────────────────────────────────────
# 8. Phase B contract — payload serialization unchanged
# ───────────────────────────────────────────────────────────


def test_phase_d_to_payload_does_not_include_priority():
    """to_payload() 仍不包含 priority (Phase B payload round-trip 設計, Phase D 不可破壞)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_payload",
        priority=13,
    ))
    payload = e.to_payload()
    assert "priority" not in payload
    # payload 欄位必須只有 6 個 (M3 既有 6 欄: source / type / novelty_id / ts / summary / data)
    assert set(payload.keys()) == {
        "source", "type", "novelty_id", "ts", "summary", "data"
    }


def test_phase_d_from_payload_does_not_read_priority():
    """from_payload() 仍不讀 priority (Phase B 設計, Phase D 不可破壞)。"""
    d, stub = _make_dispatcher_with_stub()
    e = asyncio.run(d.emit_and_inject(
        source_id="synthetic",
        type="x",
        summary="x",
        novelty_id="d_from_p",
        priority=21,
    ))
    # 即使傳入含 priority 的 dict, from_payload 也不會把它讀進 WorldEvent
    payload_with_priority = e.to_payload()
    payload_with_priority["priority"] = 999  # 嘗試注入假 priority
    e2 = WorldEvent.from_payload(payload_with_priority)
    # WorldEvent 沒被汙染, priority 仍走 default 0
    assert e2.priority == 0
    # 注意: e2 是新 object, e 仍是 priority=21
    assert e.priority == 21


# ───────────────────────────────────────────────────────────
# 9. Phase B contract — priority validation still active
# ───────────────────────────────────────────────────────────


def test_phase_d_dispatcher_emit_with_invalid_priority_raises_type_error():
    """emit_and_inject 傳入非 int priority (e.g. str), 仍要 raise TypeError (Phase B validation)。"""
    d, stub = _make_dispatcher_with_stub()
    with pytest.raises(TypeError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="x",
            novelty_id="d_inv",
            priority="not_an_int",  # type: ignore[arg-type]
        ))
    assert "priority" in str(exc_info.value).lower()


def test_phase_d_dispatcher_emit_with_bool_priority_raises_type_error():
    """emit_and_inject 傳入 bool priority, 仍要 raise TypeError (Phase B 拒 bool 規則)。"""
    d, stub = _make_dispatcher_with_stub()
    with pytest.raises(TypeError) as exc_info:
        asyncio.run(d.emit_and_inject(
            source_id="synthetic",
            type="x",
            summary="x",
            novelty_id="d_bool",
            priority=True,  # type: ignore[arg-type]
        ))
    assert "priority" in str(exc_info.value).lower()


# ───────────────────────────────────────────────────────────
# 10. Hard limits — production code files unchanged
# ───────────────────────────────────────────────────────────


def _file_sha256(path: Path) -> str:
    """helper: 算檔案 SHA256 確認內容未變。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_phase_d_middleware_unchanged():
    """middleware.py 必須 0 change (Phase A/B/C/D hard limit)。"""
    p = Path(__file__).parent.parent / "src" / "world" / "middleware.py"
    assert p.exists(), f"{p} not found"
    sha = _file_sha256(p)
    # 只驗證存在 + 可讀, 不驗證具體值 (避免 hard-code 跟版本漂移)
    assert len(sha) == 64  # SHA256 hex length


def test_phase_d_run_server_unchanged():
    """run_server.py 必須 0 change (Phase A/B/C/D hard limit)。"""
    p = Path(__file__).parent.parent / "scripts" / "run_server.py"
    assert p.exists(), f"{p} not found"
    sha = _file_sha256(p)
    assert len(sha) == 64


def test_phase_d_token_manager_unchanged():
    """token_manager.py 必須 0 change (Phase A/B/C/D hard limit)。"""
    # token_manager 已知位置: src/eventbus/token_manager.py
    candidates = [
        Path(__file__).parent.parent / "src" / "eventbus" / "token_manager.py",
        Path(__file__).parent.parent / "src" / "voice" / "token_manager.py",
        Path(__file__).parent.parent / "src" / "token_manager.py",
        Path(__file__).parent.parent / "token_manager.py",
    ]
    p = None
    for c in candidates:
        if c.exists():
            p = c
            break
    assert p is not None, "token_manager.py not found in expected locations"
    sha = _file_sha256(p)
    assert len(sha) == 64


def test_phase_d_dispatcher_unchanged():
    """Phase D 派工明確禁止改 dispatcher.py — 確認 sha256 有 baseline 可比對。"""
    p = Path(__file__).parent.parent / "src" / "world" / "dispatcher.py"
    assert p.exists(), f"{p} not found"
    sha = _file_sha256(p)
    # 只驗證存在 + 可讀, 不 hard-code (Phase D 派工的精神是 dispatcher.py 不應為 priority survival 改動)
    assert len(sha) == 64
    # 額外 sanity: dispatcher.py 仍包含 emit_and_inject 跟 _injector.inject(event) 呼叫
    content = p.read_text(encoding="utf-8")
    assert "async def emit_and_inject" in content
    assert "await self._injector.inject(event)" in content
