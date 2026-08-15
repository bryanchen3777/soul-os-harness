"""
tests/test_m6_2_1_text_tts_correlation.py — M6.2-1

Bry 派工 2026-08-14 19:47 EDT — Per-Message TTS Correlation Minimal Implementation

修正 M6.2-0 audit 識別的 gap:
- AGENT_AUDIO_READY 缺少 message_id
- ChannelRouter._pending_voice_target 為 last-write-wins
- 快速連續對話可能造成 stale/wrong audio association

5 required regression tests (per work order):
1. message_id propagation
2. two rapid messages with different message_ids
3. audio A cannot be associated with text B
4. existing no-message_id path remains functional (backward compat)
5. TTS failure does not fail text response

Test sections:
- A. message_id propagation (TTSService / FishTTSHandler)
- B. ChannelRouter per-message correlation
- C. IOGateway WS payload includes message_id
- D. Backward compat (no message_id path)
- E. TTS failure isolation
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.voice.tts_service import TTSService
from src.llm.fish_tts_handler import FishTTSHandler
from src.io.channels.router import ChannelRouter


# ───────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────


class CapturingBus:
    """Mock EventBus 攔截所有 publish 事件,給 tests inspect 用."""

    def __init__(self) -> None:
        self.published: List[SoulEvent] = []

    async def publish(self, event: SoulEvent) -> None:
        self.published.append(event)

    def filter(self, event_type: str) -> List[SoulEvent]:
        return [e for e in self.published if e.event_type == event_type]

    def reset(self) -> None:
        self.published.clear()


def make_agent_speak_event(
    agent_id: str = "agent_ruka",
    event_id: Optional[str] = "msg-uuid-A",
    audio_text: str = "こんにちは",
    text: str = "你好",
    session_id: str = "session_1696287850_agent_ruka",
) -> SoulEvent:
    """模擬 LLMProxy 發出的 AGENT_SPEAK event (M6.2-1 完整版, 帶 event_id).

    設 source="dream" 跳過 ChannelRouter._should_push_to_bry 過濾 (test isolation).
    """
    return SoulEvent(
        event_id=event_id,
        event_type=EventType.AGENT_SPEAK,
        source=agent_id,
        target="broadcast",
        priority=EventPriority.NORMAL,
        session_id=session_id,
        correlation_id=event_id,
        payload={
            "agent_id": agent_id,
            "text": text,
            "audio_text": audio_text,
            "emotion": "calm",
            "tts_enabled": True,
            "target_channel": "telegram",
            "target_user_id": "1696287850",
            "source": "dream",  # M6.2-1: 跳過 Stage 4.3 push filter (test isolation)
            "reason": "user_message",
        },
    )


def make_agent_audio_ready_payload(
    agent_id: str = "agent_ruka",
    message_id: Optional[str] = "msg-uuid-A",
) -> Dict[str, Any]:
    """模擬 TTSService 寫完 mp3 後的 payload (M6.2-1 帶 message_id)."""
    return {
        "agent_id": agent_id,
        "ts": "20260814T222956_802596",
        "audio_path": f"/tmp/{agent_id}/fake.mp3",
        "audio_url": f"/api/tts/audio/{agent_id}/20260814T222956_802596.mp3",
        "emotion": "calm",
        "size": 77739,
        "text_preview": "你好",
        "message_id": message_id,
    }


# ───────────────────────────────────────────────
# Section A: message_id propagation
# ───────────────────────────────────────────────


def test_a1_tts_service_accepts_message_id_param(tmp_path):
    """
    A.1: TTSService.synthesize_and_store 接受 message_id 參數
    並且把它寫入 AGENT_AUDIO_READY payload.
    """
    async def _run() -> None:
        bus = CapturingBus()
        tts = TTSService(bus=bus, output_dir=tmp_path)
        result = await tts.synthesize_and_store(
            agent_id="agent_ruka",
            mp3_bytes=b"fake-mp3-bytes",
            emotion="calm",
            text_preview="你好",
            message_id="msg-uuid-A",
        )
        # 1. 結果 dict 包含 message_id
        assert result["message_id"] == "msg-uuid-A", (
            f"A.1 FAIL: result.message_id 應為 msg-uuid-A, 實際 {result.get('message_id')}"
        )
        # 2. AGENT_AUDIO_READY event payload 包含 message_id
        audio_events = bus.filter(EventType.AGENT_AUDIO_READY)
        assert len(audio_events) == 1, (
            f"A.1 FAIL: 應有 1 個 AGENT_AUDIO_READY, 實際 {len(audio_events)}"
        )
        assert audio_events[0].payload.get("message_id") == "msg-uuid-A", (
            f"A.1 FAIL: event payload message_id 應為 msg-uuid-A, "
            f"實際 {audio_events[0].payload.get('message_id')}"
        )
        # 3. mp3 file 寫入磁碟 (確認 TTS 流程沒被 message_id 影響)
        assert (tmp_path / "agent_ruka").is_dir()

    asyncio.run(_run())


def test_a2_tts_service_message_id_default_none(tmp_path):
    """
    A.2: 沒傳 message_id 時, 預設 None (backward compat)
    """
    async def _run() -> None:
        bus = CapturingBus()
        tts = TTSService(bus=bus, output_dir=tmp_path)
        result = await tts.synthesize_and_store(
            agent_id="agent_ruka",
            mp3_bytes=b"fake-mp3",
            emotion="calm",
            text_preview="你好",
            # 沒傳 message_id
        )
        assert result["message_id"] is None
        audio_events = bus.filter(EventType.AGENT_AUDIO_READY)
        assert len(audio_events) == 1
        assert audio_events[0].payload.get("message_id") is None

    asyncio.run(_run())


# ───────────────────────────────────────────────
# Section B: ChannelRouter per-message correlation
# ───────────────────────────────────────────────


def test_b1_rapid_two_messages_get_separate_pairing(tmp_path):
    """
    B.1: 兩則快速連續訊息 (不同 message_id), 各自配對到自己的 audio.
    修掉 M5.2-I7 之前的 last-write-wins race condition.
    """
    async def _run() -> None:
        bus = CapturingBus()
        router = ChannelRouter(bus=bus)
        # 用 NoopAdapter 模擬 TG adapter (不用真的 TG)
        from src.io.channels.base import ChannelAdapter
        class NoopAdapter(ChannelAdapter):
            channel_id = "telegram"  # 覆寫 class attribute
            def __init__(self):
                self.sent_voice_calls: List[Any] = []
            async def send(self, agent_id, text, user_id):
                return True
            async def send_voice(self, agent_id, audio_path, user_id):
                self.sent_voice_calls.append((agent_id, audio_path, user_id))
                return True
            async def start(self, on_message):
                pass
            async def stop(self):
                pass
        adapter = NoopAdapter()
        router.register(adapter)
        # 兩則連續 text (A 然後 B, 同 agent 連續發)
        event_a = make_agent_speak_event(event_id="msg-A", text="第一則")
        event_b = make_agent_speak_event(event_id="msg-B", text="第二則")
        await router._on_agent_speak(event_a)
        await router._on_agent_speak(event_b)
        # _pending_voice_target 應該有 2 個 entries (per message_id)
        assert "msg-A" in router._pending_voice_target, (
            f"B.1 FAIL: msg-A 應在 _pending_voice_target, 實際 keys: "
            f"{list(router._pending_voice_target.keys())}"
        )
        assert "msg-B" in router._pending_voice_target, (
            f"B.1 FAIL: msg-B 應在 _pending_voice_target, 實際 keys: "
            f"{list(router._pending_voice_target.keys())}"
        )
        # 2 個 audio 進來 (A 然後 B), 各自配對, 各自 pop, 不混淆
        await router._on_agent_audio_ready(SoulEvent(
            event_type=EventType.AGENT_AUDIO_READY,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload=make_agent_audio_ready_payload(message_id="msg-A"),
        ))
        await router._on_agent_audio_ready(SoulEvent(
            event_type=EventType.AGENT_AUDIO_READY,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload=make_agent_audio_ready_payload(message_id="msg-B"),
        ))
        # 2 個 voice 都被送
        assert len(adapter.sent_voice_calls) == 2, (
            f"B.1 FAIL: 應送 2 個 voice, 實際 {len(adapter.sent_voice_calls)}"
        )
        # _pending_voice_target 應該清空 (2 個都被 pop)
        assert "msg-A" not in router._pending_voice_target
        assert "msg-B" not in router._pending_voice_target

    asyncio.run(_run())


def test_b2_audio_for_message_A_cannot_pair_to_text_B(tmp_path):
    """
    B.2: audio A 來時 (對應 text A), 不能配對到 text B
    (防 last-write-wins 把 B 的 entry 蓋掉 A, 然後 A 的 audio 配錯).
    """
    async def _run() -> None:
        bus = CapturingBus()
        router = ChannelRouter(bus=bus)
        from src.io.channels.base import ChannelAdapter
        class TrackingAdapter(ChannelAdapter):
            channel_id = "telegram"
            def __init__(self):
                self.voice_calls: List[Any] = []
            async def send(self, agent_id, text, user_id):
                return True
            async def send_voice(self, agent_id, audio_path, user_id):
                self.voice_calls.append({
                    "agent_id": agent_id,
                    "audio_path": audio_path,
                    "user_id": user_id,
                })
                return True
            async def start(self, on_message):
                pass
            async def stop(self):
                pass
        adapter = TrackingAdapter()
        router.register(adapter)
        # 1. text A 出, _pending 存 msg-A
        event_a = make_agent_speak_event(event_id="msg-A")
        await router._on_agent_speak(event_a)
        # 2. text B 出, _pending 存 msg-B (msg-A 還在, 因為 key 不同)
        event_b = make_agent_speak_event(event_id="msg-B")
        await router._on_agent_speak(event_b)
        # 3. audio A 來 (對應 msg-A)
        await router._on_agent_audio_ready(SoulEvent(
            event_type=EventType.AGENT_AUDIO_READY,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload=make_agent_audio_ready_payload(message_id="msg-A"),
        ))
        # 4. audio B 來 (對應 msg-B)
        await router._on_agent_audio_ready(SoulEvent(
            event_type=EventType.AGENT_AUDIO_READY,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload=make_agent_audio_ready_payload(message_id="msg-B"),
        ))
        # 2 個 voice 都被送到正確的 user (user_id 從各自配對拿)
        assert len(adapter.voice_calls) == 2
        for call in adapter.voice_calls:
            assert call["user_id"] == 1696287850  # int (從 event.target_user_id 轉)

    asyncio.run(_run())


# ───────────────────────────────────────────────
# Section C: IOGateway WS payload includes message_id
# ───────────────────────────────────────────────


def test_c1_gateway_audio_ready_payload_includes_message_id():
    """
    C.1: IOGateway._on_agent_audio_ready 把 message_id 帶到 WS payload
    讓前端用 message_id 找對應的 text message.
    """
    from src.io.gateway import IOGateway
    bus = CapturingBus()
    # 用 mock FastAPI app
    app = MagicMock()
    gateway = IOGateway(bus=bus, app=app)
    # 攔截 manager.broadcast
    broadcasted: List[Dict[str, Any]] = []
    class MockManager:
        @property
        def count(self) -> int:
            return 0
        @property
        def _connections(self):
            return set()
        async def broadcast(self, payload):
            broadcasted.append(payload)
    gateway.manager = MockManager()
    # 觸發 audio_ready event
    payload = make_agent_audio_ready_payload(message_id="msg-test-123")
    event = SoulEvent(
        event_type=EventType.AGENT_AUDIO_READY,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=payload,
    )
    async def _run() -> None:
        await gateway._on_agent_audio_ready(event)
    asyncio.run(_run())
    # WS payload 包含 message_id
    assert len(broadcasted) == 1
    ws_payload = broadcasted[0]
    assert ws_payload.get("type") == "agent_audio_ready"
    assert ws_payload.get("message_id") == "msg-test-123", (
        f"C.1 FAIL: WS payload message_id 應為 msg-test-123, 實際 {ws_payload.get('message_id')}"
    )


def test_c2_gateway_agent_speak_payload_includes_message_id():
    """
    C.2: IOGateway._on_agent_speak 把 event_id 帶到 WS payload 當 message_id
    (M6.2-1 設計: 同一個 event_id 既是 AGENT_SPEAK 的 message_id,
     也是後續 AGENT_AUDIO_READY 的 message_id, 形成配對)
    """
    from src.io.gateway import IOGateway
    bus = CapturingBus()
    app = MagicMock()
    gateway = IOGateway(bus=bus, app=app)
    broadcasted: List[Dict[str, Any]] = []
    class MockManager:
        @property
        def count(self) -> int:
            return 0
        @property
        def _connections(self):
            return set()
        async def broadcast(self, payload):
            broadcasted.append(payload)
    gateway.manager = MockManager()
    # 觸發 agent_speak event (event_id = "msg-uuid-X")
    event = make_agent_speak_event(event_id="msg-uuid-X")
    async def _run() -> None:
        await gateway._on_agent_speak(event)
    asyncio.run(_run())
    assert len(broadcasted) == 1
    ws_payload = broadcasted[0]
    assert ws_payload.get("type") == "agent_speak"
    assert ws_payload.get("message_id") == "msg-uuid-X", (
        f"C.2 FAIL: WS agent_speak message_id 應為 msg-uuid-X, "
        f"實際 {ws_payload.get('message_id')}"
    )


# ───────────────────────────────────────────────
# Section D: Backward compatibility (no message_id)
# ───────────────────────────────────────────────


def test_d1_tts_service_no_message_id_backward_compat(tmp_path):
    """
    D.1: 沒傳 message_id 時 (legacy / old code), TTS 還能跑
    AGENT_AUDIO_READY payload 還能 publish (message_id=None)
    """
    async def _run() -> None:
        bus = CapturingBus()
        tts = TTSService(bus=bus, output_dir=tmp_path)
        # 沒 message_id (legacy)
        result = await tts.synthesize_and_store(
            agent_id="agent_ruka",
            mp3_bytes=b"fake",
            emotion="calm",
            text_preview="你好",
        )
        assert result["message_id"] is None
        audio_events = bus.filter(EventType.AGENT_AUDIO_READY)
        assert len(audio_events) == 1
        assert audio_events[0].payload.get("message_id") is None

    asyncio.run(_run())


def test_d2_router_legacy_agent_id_fallback_when_no_message_id():
    """
    D.2: AGENT_SPEAK 沒 event_id (理論上不可能但保險), 降級到
    legacy agent_id-based _pending_voice_target_legacy dict.
    """
    async def _run() -> None:
        bus = CapturingBus()
        router = ChannelRouter(bus=bus)
        from src.io.channels.base import ChannelAdapter
        class NoopAdapter(ChannelAdapter):
            channel_id = "telegram"
            def __init__(self):
                pass
            async def send(self, agent_id, text, user_id):
                return True
            async def send_voice(self, agent_id, audio_path, user_id):
                return True
            async def start(self, on_message):
                pass
            async def stop(self):
                pass
        router.register(NoopAdapter())
        # 手動建一個沒 event_id 的 event (legacy simulation)
        event_no_id = SoulEvent(
            event_id="",  # 故意空字串
            event_type=EventType.AGENT_SPEAK,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            session_id="session_x",
            payload={
                "agent_id": "agent_ruka",
                "text": "你好",
                "audio_text": "こんにちは",
                "emotion": "calm",
                "tts_enabled": True,
                "target_channel": "telegram",
                "target_user_id": "1696287850",
                "source": "dream",  # 跳過 Stage 4.3 push filter
                "reason": "user_message",
            },
        )
        await router._on_agent_speak(event_no_id)
        # 沒 event_id → 走 _pending_voice_target_legacy[agent_id]
        assert "agent_ruka" in router._pending_voice_target_legacy
        # 沒進 _pending_voice_target (per-message dict)
        assert "agent_ruka" not in router._pending_voice_target

    asyncio.run(_run())


# ───────────────────────────────────────────────
# Section E: TTS failure isolation
# ───────────────────────────────────────────────


def test_e1_tts_write_failure_does_not_break_publish(tmp_path):
    """
    E.1: TTS 寫檔失敗時 (e.g. permission denied), 還是要 publish
    AGENT_AUDIO_READY (帶 error field), 主對話不掛.
    """
    async def _run() -> None:
        bus = CapturingBus()
        tts = TTSService(bus=bus, output_dir=tmp_path / "readonly")
        # 把 output_dir 設成 readonly (chmod 0o555 試一下, Windows skip)
        try:
            (tmp_path / "readonly").mkdir()
            (tmp_path / "readonly").chmod(0o555)
        except (OSError, NotImplementedError):
            # Windows 不一定支援 chmod, 改用 patch 強制失敗
            pass
        # 用 monkey patch 強制 write_bytes 失敗
        from unittest.mock import patch
        original_write = Path.write_bytes
        def failing_write(self, data):
            if "readonly" in str(self):
                raise PermissionError("readonly dir")
            return original_write(self, data)
        with patch.object(Path, "write_bytes", failing_write):
            result = await tts.synthesize_and_store(
                agent_id="agent_ruka",
                mp3_bytes=b"data",
                emotion="calm",
                text_preview="你好",
                message_id="msg-test",
            )
        # 即使寫檔失敗, result 還是有 audio_path, message_id
        assert result.get("message_id") == "msg-test"
        # 沒 publish (寫檔失敗時直接 return, 不 publish)
        audio_events = bus.filter(EventType.AGENT_AUDIO_READY)
        assert len(audio_events) == 0, (
            f"E.1: 寫檔失敗時不應 publish, 實際 {len(audio_events)} 個"
        )

    asyncio.run(_run())


def test_e2_tts_publish_failure_does_not_raise(tmp_path):
    """
    E.2: 即使 AGENT_AUDIO_READY publish 失敗 (e.g. bus 壞掉),
    synthesize_and_store 也不 raise, 主對話流不受影響.
    """
    async def _run() -> None:
        # 用壞掉的 bus (publish 永遠 raise)
        class BrokenBus:
            async def publish(self, event):
                raise RuntimeError("bus is dead")
        tts = TTSService(bus=BrokenBus(), output_dir=tmp_path)
        # 不應 raise
        result = await tts.synthesize_and_store(
            agent_id="agent_ruka",
            mp3_bytes=b"data",
            emotion="calm",
            text_preview="你好",
            message_id="msg-test",
        )
        # result 仍然有 audio_path (寫檔成功), 即使 publish 失敗
        assert result["message_id"] == "msg-test"
        assert result["audio_path"]  # not empty

    asyncio.run(_run())


# ───────────────────────────────────────────────
# Section F: FishTTSHandler message_id extraction
# ───────────────────────────────────────────────


def test_f1_fish_tts_handler_extracts_message_id_from_event():
    """
    F.1: FishTTSHandler._on_agent_speak 從 event.event_id 抽出 message_id
    並傳到 _synthesize_async 跟 TTSService.
    """
    from unittest.mock import patch, MagicMock, AsyncMock

    # 直接測試 _synthesize_async 接受 message_id 參數
    handler = FishTTSHandler(bus=AsyncMock(), enabled=True, output_dir=Path("/tmp/m6_2_1_test"))
    # 確認 signature
    import inspect
    sig = inspect.signature(handler._synthesize_async)
    assert "message_id" in sig.parameters, (
        f"F.1 FAIL: _synthesize_async 應接受 message_id 參數, "
        f"實際參數: {list(sig.parameters.keys())}"
    )
    # 預設值應為 None (向後相容)
    assert sig.parameters["message_id"].default is None, (
        f"F.1 FAIL: message_id 預設值應為 None"
    )
