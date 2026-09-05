"""
tests/test_ms2_multimodal_perception.py — MS-2 Multimodal Perception Implementation 測試

覆蓋（工單 MS-2 驗收）：
  1. tool_registry 三處 additive：EXPLICIT_GROUP_MAP / EXPLICIT_PERMISSION_MAP /
     _OBSERVE_KEYWORDS（既有 12 工具映射 0 改動、fail-closed 不放寬）。
  2. VALID_SOURCES additive：audio_input / camera_capture 加入；既有 5 source
     仍在（回歸確認「無測試斷言恰好 5 source」：本檔只做存在性斷言）。
  3. 感知分類表 additive：TYPE_KEYWORDS / TYPE_BASELINE_RELEVANCE 新增
     voice_transcript / ambient_audio / camera_scene。
  4. 兩個薄 MCP server（audio-stream-mcp / camera-mcp）端到端：
     真實 stdio 子進程（mock 模式）→ tools/list → tools/call →
     單次調用 / 無狀態清理（消費即刪）/ fail-closed。
  5. 感知邊界不變量：observe 路徑回流 WorldEvent（Ambient Observation）——
     語音/視覺結果進 Perception/Context，不直通 USER_MESSAGE、不自激回環；
     MCP server 進程 0 import EventBus/SpeakerToken/LLM。
  6. D9 novelty_id 內容級哈希：同句同 id（stt:sha256）、同場景同日同桶
     （cam:scene:date）、無特徵 fallback tool:ts。

Frozen contract：0 change（不 import / 不改 Agency / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE / EventBus）。

環境假設：audio/camera server 以 mock 模式啟動（SOUL_AUDIO_MOCK=1 /
SOUL_ASR_MOCK=1 / SOUL_CAMERA_MOCK=1），不依賴真實麥克風/相機/ASR 模型。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul.tool_registry import (  # noqa: E402
    CAPABILITY_GROUP_OBSERVE,
    EXPLICIT_GROUP_MAP,
    EXPLICIT_PERMISSION_MAP,
    PERM_ASK_REQUIRED,
    PERM_AUTO_APPROVED,
    ToolRegistry,
    classify_tool,
    permission_class_for,
)
from src.soul.mcp_stdio_client import MCPStdioClientAdapter  # noqa: E402
from src.world.perception import (  # noqa: E402
    TYPE_BASELINE_RELEVANCE,
    TYPE_KEYWORDS,
    VALID_SOURCES,
)

PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
AUDIO_SERVER = str(PROJECT_ROOT / "scripts" / "audio_stream_mcp.py")
CAMERA_SERVER = str(PROJECT_ROOT / "scripts" / "camera_mcp.py")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_root(tmp_path: Path):
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    yield data_root()
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


# ────────────────────────────────────────────────────────────
# 1. tool_registry 三處 additive
# ────────────────────────────────────────────────────────────

class TestToolRegistryAdditive:
    def test_explicit_group_map_multimodal_tools(self):
        """EXPLICIT_GROUP_MAP additive：6 個多模态工具 → observe_environment。"""
        multimodal = {
            "mic_listen", "audio_transcribe", "stt",
            "camera_capture", "camera_snapshot", "image_capture",
        }
        for name in multimodal:
            assert classify_tool(name, "") == CAPABILITY_GROUP_OBSERVE, name
            assert EXPLICIT_GROUP_MAP[name] == CAPABILITY_GROUP_OBSERVE, name

    def test_existing_12_tool_mapping_unchanged(self):
        """既有 12 工具映射 0 改動（additive 不變式）。"""
        expected = {
            "weather": CAPABILITY_GROUP_OBSERVE,
            "calendar": CAPABILITY_GROUP_OBSERVE,
            "news": CAPABILITY_GROUP_OBSERVE,
            "web_search": CAPABILITY_GROUP_OBSERVE,
            "time": CAPABILITY_GROUP_OBSERVE,
            "search": CAPABILITY_GROUP_OBSERVE,
            "message_send": "communicate",
            "telegram_send": "communicate",
            "dm_send": "communicate",
            "memory_search": "reflect_memory",
            "diary_read": "reflect_memory",
            "memory_retrieve": "reflect_memory",
        }
        for name, group in expected.items():
            assert EXPLICIT_GROUP_MAP[name] == group, name

    def test_explicit_permission_map_multimodal_tools(self):
        """EXPLICIT_PERMISSION_MAP additive：mic/STT → auto_approved；
        camera（隱私）→ ask_required。"""
        assert permission_class_for("mic_listen", None) == PERM_AUTO_APPROVED
        assert permission_class_for("audio_transcribe", None) == PERM_AUTO_APPROVED
        assert permission_class_for("stt", None) == PERM_AUTO_APPROVED
        assert permission_class_for("camera_capture", None) == PERM_ASK_REQUIRED
        assert permission_class_for("camera_snapshot", None) == PERM_ASK_REQUIRED
        assert permission_class_for("image_capture", None) == PERM_ASK_REQUIRED
        assert EXPLICIT_PERMISSION_MAP["mic_listen"] == PERM_AUTO_APPROVED
        assert EXPLICIT_PERMISSION_MAP["camera_capture"] == PERM_ASK_REQUIRED

    def test_existing_permission_map_unchanged(self):
        """既有 12 工具權限 0 改動。"""
        assert EXPLICIT_PERMISSION_MAP["weather"] == PERM_AUTO_APPROVED
        assert EXPLICIT_PERMISSION_MAP["message_send"] == PERM_ASK_REQUIRED
        assert EXPLICIT_PERMISSION_MAP["memory_search"] == PERM_AUTO_APPROVED

    def test_observe_keywords_semantic_fallback(self):
        """_OBSERVE_KEYWORDS additive：含 audio/voice/camera/麦克风 等描述的
        未入表工具 → 語義兜底 observe（§2.3 優先級 2）。"""
        cases = [
            ("capture_sound", "Record ambient audio via microphone 麦克风录音"),
            ("voice_capture", "Listen to voice input 语音采集"),
            ("snap", "Take a photo with the camera 相机拍照"),
            ("speech_recognize", "Speech recognition 语音识别 STT"),
            ("see_vision", "Vision capture 图像画面感知"),
        ]
        for name, desc in cases:
            assert classify_tool(name, desc) == CAPABILITY_GROUP_OBSERVE, (name, desc)

    def test_fail_closed_not_relaxed(self):
        """fail-closed 不放寬：完全無關描述仍拒絕註冊（None）。"""
        assert classify_tool("foo_bar", "a completely unrelated description") is None
        assert classify_tool("x123", "") is None

    def test_unclassified_semantic_tool_perm_fail_closed_ask(self):
        """語義兜底（未入權限表）→ ask_required（fail-closed 權限，§4.1.1）。"""
        assert permission_class_for("vision_scan", CAPABILITY_GROUP_OBSERVE) == PERM_ASK_REQUIRED


# ────────────────────────────────────────────────────────────
# 2. VALID_SOURCES additive
# ────────────────────────────────────────────────────────────

class TestValidSourcesAdditive:
    def test_new_sources_present(self):
        assert "audio_input" in VALID_SOURCES
        assert "camera_capture" in VALID_SOURCES

    def test_existing_5_sources_still_present(self):
        """既有 5 source 0 改動（additive 不變式；回歸「無恰好 5 source 斷言」）。"""
        for src in ("weather", "news", "calendar", "social", "synthetic"):
            assert src in VALID_SOURCES, src
        # 只做存在性斷言，不斷言總數（既有測試無「恰好 5」斷言，照樣式回歸）

    def test_world_event_accepts_new_sources(self):
        """WorldEvent 可用新 source 建構（純資料結構無新校驗）。"""
        from src.world.perception import WorldEvent

        for src in ("audio_input", "camera_capture"):
            ev = WorldEvent(
                source=src, type="tool_x", novelty_id="n1",
                ts="2026-09-05T00:00:00+00:00", summary="s",
            )
            assert ev.source == src

    def test_new_types_in_perception_tables(self):
        """感知分類表 additive：voice_transcript / ambient_audio / camera_scene。"""
        for t in ("voice_transcript", "ambient_audio", "camera_scene"):
            assert t in TYPE_KEYWORDS, t
            assert t in TYPE_BASELINE_RELEVANCE, t
        # 既有 5 type 仍在
        for t in ("rain_started", "weather_temp_change", "celebrity_news",
                  "calendar_event", "user_going_outside"):
            assert t in TYPE_KEYWORDS, t


# ────────────────────────────────────────────────────────────
# 3. D9 novelty_id 內容級哈希（actuator）
# ────────────────────────────────────────────────────────────

class TestNoveltyIdContentHash:
    @staticmethod
    def _actuator():
        from src.soul.actuator import Actuator

        return Actuator  # staticmethod 供 _content_novelty_id / _normalize_transcript 使用

    def test_stt_same_sentence_same_id(self):
        from src.soul.actuator import Actuator

        ts = "2026-09-05T00:00:00+00:00"
        id1 = Actuator._content_novelty_id("audio_transcribe", {"text": "  今天 天氣 很好！ "}, ts)
        id2 = Actuator._content_novelty_id("stt", {"text": "今天天氣很好"}, ts)
        assert id1 == id2                       # normalize 後同句 → 同 id
        assert id1.startswith("stt:") and len(id1) == 4 + 12

    def test_stt_different_sentence_different_id(self):
        from src.soul.actuator import Actuator

        ts = "2026-09-05T00:00:00+00:00"
        id1 = Actuator._content_novelty_id("audio_transcribe", {"text": "今天天氣很好"}, ts)
        id2 = Actuator._content_novelty_id("audio_transcribe", {"text": "明天會下雨"}, ts)
        assert id1 != id2

    def test_camera_scene_day_bucket(self):
        from src.soul.actuator import Actuator

        id1 = Actuator._content_novelty_id("camera_capture", {"scene_tag": "empty_room"}, "t1")
        id2 = Actuator._content_novelty_id("camera_snapshot", {"scene_tag": "empty_room"}, "t2")
        assert id1 == id2                       # 同場景同日 → 同桶
        assert id1 == "cam:empty_room:" + __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%d")

    def test_camera_different_scene_different_bucket(self):
        from src.soul.actuator import Actuator

        id1 = Actuator._content_novelty_id("camera_capture", {"scene_tag": "empty_room"}, "t1")
        id2 = Actuator._content_novelty_id("camera_capture", {"scene_tag": "person"}, "t1")
        assert id1 != id2

    def test_fallback_tool_ts_when_no_content_feature(self):
        from src.soul.actuator import Actuator

        ts = "2026-09-05T00:00:00+00:00"
        assert Actuator._content_novelty_id("weather", {"temp": 21}, ts) == f"weather:{ts}"
        assert Actuator._content_novelty_id("audio_transcribe", {"text": "   "}, ts) == f"audio_transcribe:{ts}"

    def test_normalize_transcript(self):
        from src.soul.actuator import Actuator

        assert Actuator._normalize_transcript(" Hello, 世界！ ") == "hello世界"
        assert Actuator._normalize_transcript("ＡＢＣｄｅｆ") == "abcdef"   # 全形→半形


# ────────────────────────────────────────────────────────────
# 4. 兩個薄 MCP server：端到端（mock 模式）+ 註冊歸類 + 感知邊界
# ────────────────────────────────────────────────────────────

def _audio_client() -> MCPStdioClientAdapter:
    return MCPStdioClientAdapter(
        PYTHON, [AUDIO_SERVER],
        env={"SOUL_AUDIO_MOCK": "1", "SOUL_ASR_MOCK": "1"},
    )


def _camera_client() -> MCPStdioClientAdapter:
    return MCPStdioClientAdapter(
        PYTHON, [CAMERA_SERVER],
        env={"SOUL_CAMERA_MOCK": "1", "SOUL_CAMERA_MOCK_TAG": "empty_room"},
    )


class TestAudioStreamMCP:
    def test_tools_list_contract(self):
        """tools/list：mic_listen + audio_transcribe（§4.2 schema）。"""
        async def scenario():
            client = _audio_client()
            await client.connect()
            try:
                tools = await client.list_tools()
                names = [t.name for t in tools]
                assert "mic_listen" in names
                assert "audio_transcribe" in names
            finally:
                await client.close()

        _run(scenario())

    def test_mic_listen_returns_schema(self):
        """mic_listen：wav_ref/duration/has_speech/peak_level（静音门控有值）。"""
        async def scenario():
            client = _audio_client()
            await client.connect()
            try:
                result = await client.call_tool("mic_listen", {"duration_seconds": 1.0})
                payload = result["result"] if isinstance(result, dict) and "result" in result else result
                assert "wav_ref" in payload and payload["wav_ref"].endswith(".wav")
                assert 0 < payload["duration"] <= 4.0
                assert isinstance(payload["has_speech"], bool)
                assert 0.0 <= payload["peak_level"] <= 1.0
            finally:
                await client.close()

        _run(scenario())

    def test_transcribe_consumes_wav_and_is_stateless(self):
        """audio_transcribe：消費 wav_ref → text；消費後二次調用 fail-closed
        （無狀態清理：文件已刪，不可重複消費）。"""
        async def scenario():
            client = _audio_client()
            await client.connect()
            try:
                m = await client.call_tool("mic_listen", {"duration_seconds": 1.0})
                wav_ref = m["result"]["wav_ref"]
                r = await client.call_tool("audio_transcribe", {"wav_ref": wav_ref})
                payload = r["result"]
                assert isinstance(payload["text"], str) and payload["text"].strip()
                assert payload["language"] == "zh"
                assert payload["duration"] > 0

                # 消費即刪：同一 wav_ref 再轉寫 → fail-closed 錯誤
                with pytest.raises(Exception):
                    await client.call_tool("audio_transcribe", {"wav_ref": wav_ref})
            finally:
                await client.close()

        _run(scenario())

    def test_transcribe_missing_wav_ref_fail_closed(self):
        """不存在的 wav_ref（含路徑穿越嘗試）→ 錯誤（fail-closed，不 crash）。"""
        async def scenario():
            client = _audio_client()
            await client.connect()
            try:
                with pytest.raises(Exception):
                    await client.call_tool("audio_transcribe", {"wav_ref": "../etc/passwd"})
                with pytest.raises(Exception):
                    await client.call_tool("audio_transcribe", {"wav_ref": "no_such.wav"})
            finally:
                await client.close()

        _run(scenario())


class TestCameraMCP:
    def test_tools_list_contract(self):
        """tools/list：camera_capture（§4.2 schema）。"""
        async def scenario():
            client = _camera_client()
            await client.connect()
            try:
                tools = await client.list_tools()
                names = [t.name for t in tools]
                assert "camera_capture" in names
            finally:
                await client.close()

        _run(scenario())

    def test_camera_capture_returns_schema(self):
        """camera_capture：image_ref/scene_tag/captured_at；mock 強制 tag。"""
        async def scenario():
            client = _camera_client()
            await client.connect()
            try:
                r = await client.call_tool("camera_capture", {})
                payload = r["result"]
                assert "image_ref" in payload and payload["image_ref"].endswith(".jpg")
                assert payload["scene_tag"] == "empty_room"
                assert "captured_at" in payload
            finally:
                await client.close()

        _run(scenario())


class TestMCPNotification:
    """感知邊界不變量：MCP server 進程 0 import EventBus/SpeakerToken/LLM——
    語音/視覺只能在 observe 路徑回流，無自激回環能力。"""

    @staticmethod
    def _server_source(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_server_source_has_no_volition_facilities(self):
        """audio/camera server 源碼 0 import EventBus / SpeakerToken / Motive /
        Decision / SAGE / USER_MESSAGE 通道——0 自主遞迴、無發聲權（§3.2 硬規則）。

        檢查層級：**import 語句**（不檢查 docstring 註解——設計文件允許描述
        「不碰什麼」；檢查的是實際引用）。
        """
        import re

        volition_imports = [
            r"^\s*(from|import)\s+[^\n]*(eventbus|speaker)\b",
            r"^\s*(from|import)\s+[^\n]*(sage\.writer|agency\.stages)\b",
            r"^\s*(from|import)\s+[^\n]*(motive|decision)\b",
            r"^\s*(from|import)\s+[^\n]*USER_MESSAGE",
        ]
        for path in (Path(AUDIO_SERVER), Path(CAMERA_SERVER)):
            src = self._server_source(Path(path))
            for pattern in volition_imports:
                assert not re.search(pattern, src, re.MULTILINE), (
                    f"{path.name} 含 volition 設施 import: {pattern}"
                )

    def test_observe_path_routes_multimodal_to_perception(self, isolated_root):
        """端到端：audio/camera server 註冊進 ToolRegistry → mic/stt 歸
        observe+auto_approved；camera 歸 observe+ask_required（D5 權限）。"""
        async def scenario():
            reg = ToolRegistry()
            audio = _audio_client()
            cam = _camera_client()
            try:
                # 分開 connect + 註冊，避免同一 event loop 雙 stdio 後 close 的
                # anyio cancel scope 跨 task 問題（跟 test_ts3 模式：單 server 場景）
                await audio.connect()
                audio_tools = await reg.register_mcp_server("audio-stream", audio)
                await audio.close()

                await cam.connect()
                cam_tools = await reg.register_mcp_server("camera", cam)
                await cam.close()
            finally:
                if audio.connected:
                    await audio.close()
                if cam.connected:
                    await cam.close()

            by_name = {t.name: t for t in audio_tools + cam_tools}
            assert set(by_name) == {"mic_listen", "audio_transcribe", "camera_capture"}
            for name in ("mic_listen", "audio_transcribe"):
                assert by_name[name].capability_group == CAPABILITY_GROUP_OBSERVE
                assert by_name[name].permission_class == PERM_AUTO_APPROVED
            assert by_name["camera_capture"].capability_group == CAPABILITY_GROUP_OBSERVE
            assert by_name["camera_capture"].permission_class == PERM_ASK_REQUIRED
            return by_name

        tools = _run(scenario())
        assert len(tools) == 3

    def test_observe_dispatch_flows_to_world_event_not_user_message(self, isolated_root):
        """端到端：Actuator observe 派發 camera_capture（ask_required，經
        approving gate 批准）→ 結果回流 WorldPerceptionState（Ambient
        Observation），source=camera_capture——走感知路徑，不直通 USER_MESSAGE
        （0 USER_MESSAGE 通道觸碰），且 0 自主遞迴（單次 dispatch = 單次調用）。"""
        from src.soul.actuator import Actuator
        from src.soul.decision import DecisionResult
        from src.soul.motive import Motive
        from src.world.state import WorldPerceptionState

        class _ApprovingGate:
            def __init__(self):
                self.asked: List[tuple] = []

            def approve(self, tool, args):
                self.asked.append((tool.tool_id, args))
                return True

        class _FakeCamClient:
            def __init__(self):
                self.calls: List[tuple] = []

            async def list_tools(self):
                return {"tools": [{
                    "name": "camera_capture",
                    "description": "Capture a single frame from the camera 相机抓拍",
                    "inputSchema": {"type": "object", "properties": {}},
                }]}

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return {"scene_tag": "empty_room", "image_ref": "frame_x.jpg",
                        "captured_at": "2026-09-05T00:00:00Z"}

        async def scenario():
            reg = ToolRegistry(ask_gate=_ApprovingGate())
            client = _FakeCamClient()
            await reg.register_mcp_server("camera", client)
            state = WorldPerceptionState()
            actuator = Actuator(reg, perception_state=state)

            motive = Motive(
                motive_id="m1", content="看看房间现在什么情况", target="bryan",
                provenance_ref="evt1", created_at="2026-09-05T00:00:00+00:00",
            )
            decision = DecisionResult(
                decision="observe", transmit=False, reason="test",
                motive_id="m1", motive_content=motive.content, provenance_ref="evt1",
            )
            result = await actuator.dispatch(decision, motive)
            assert result is not None and result.ok is True
            assert client.calls == [("camera_capture", {})]  # 單次調用（0 自主遞迴）
            events = state.get_active_events()
            assert len(events) == 1
            ev = events[0]
            assert ev.source == "camera_capture"      # D2：認領新 source
            assert ev.novelty_id.startswith("cam:")   # D9：場景語義桶
            return ev

        _run(scenario())

    def test_audio_source_mapping_in_actuator(self):
        """_source_for：mic/stt → audio_input；camera → camera_capture；
        未知 → synthetic fallback（向後相容）。"""
        from src.soul.actuator import Actuator

        assert Actuator._source_for("mic_listen") == "audio_input"
        assert Actuator._source_for("audio_transcribe") == "audio_input"
        assert Actuator._source_for("camera_capture") == "camera_capture"
        assert Actuator._source_for("weather") == "weather"
        assert Actuator._source_for("unknown_tool") == "synthetic"


# ────────────────────────────────────────────────────────────
# 5. 回歸：既有 tool_registry 測試相容性
# ────────────────────────────────────────────────────────────

class TestRegressionCompatibility:
    def test_registry_register_audio_server_groups_correctly(self, isolated_root):
        """真實 audio server 註冊：mic_listen/audio_transcribe 歸 observe +
        auto_approved，可被 observe 組列出。"""
        async def scenario():
            reg = ToolRegistry()
            client = _audio_client()
            try:
                await client.connect()
                tools = await reg.register_mcp_server("audio-stream", client)
            finally:
                await client.close()
            groups = reg.project_capabilities()
            assert any(cap.id == CAPABILITY_GROUP_OBSERVE for cap in groups)
            names = {t.name for t in tools}
            assert names == {"mic_listen", "audio_transcribe"}
            observe = reg.list_tools(group=CAPABILITY_GROUP_OBSERVE)
            assert {t.name for t in observe} >= names
            return tools

        tools = _run(scenario())
        assert len(tools) == 2