# test_voice_session_mcp.py
# Soul OS — MS-3.1 设备层音频采集与 MCP 会话工具对接（voice_session_start/feed/stop）
#
# 设计文档：docs/MS-3-VOICE-INTERACTION-CONTRACT.md §4（会话模式 / VAD 断句 /
#           §2.4 不变量）/ 工单 MS-3.1。
#
# 验收（4 剧本 + 硬断言，全 mock：合成 PCM/Base64 + 注入 ASR 回调，0 实体音频驱动）：
#   剧本 1 正常对话：Start → Feed 带唤醒词 → Stop → USER_MESSAGE 含转写文字
#   剧本 2 环境杂音：Start → Feed 无唤醒背景音 → Stop → AMBIENT 无直穿
#   剧本 3 超时清理：Start → 不 Stop 超时 → 会话自动过期、状态重置 inactive
#   剧本 4 非法分片：Feed 损坏 Base64/空数据 → 错误码 + 会话平稳释放
# 运行：.\.venv\Scripts\python.exe -m pytest tests/tools/test_voice_session_mcp.py -v
#       （精确路径，不全局收集）

import asyncio
import base64
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import numpy as np
import pytest

import audio_stream_mcp as mcp
from src.voice.audio_service import AudioStreamResult, process_audio_stream
from src.voice.gate import RouteDecision, extract_features


# ─────────────────────────────────────────────────────────────
# 工具（合成 PCM → Base64；确定性时钟）
# ─────────────────────────────────────────────────────────────

def synth_pcm_b64(seconds: float = 0.2, sr: int = 16000, amp: float = 0.3,
                  silent: bool = False) -> str:
    """合成 PCM16 → Base64：220Hz 正弦模拟人声（RMS >> 0.01 阈值）；silent=True 全零。"""
    n = max(int(sr * seconds), 1)
    if silent:
        x = np.zeros(n, dtype=np.float32)
    else:
        t = np.arange(n) / sr
        x = (amp * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
    i16 = (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)
    return base64.b64encode(i16.tobytes()).decode("ascii")


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """每笔测试前清空会话注册表、恢复默认 ASR 注入点。"""
    mcp._SESSION_REGISTRY.clear()
    monkeypatch.setattr(mcp, "_SESSION_ASR_FN", None)
    yield
    mcp._SESSION_REGISTRY.clear()


def _start(max_sec: float = 30.0, now_ms=None) -> dict:
    """开启会话；不传 now_ms 用真实时钟（与 feed/stop 默认一致，避免跨时钟误过期）。"""
    now = mcp._real_now_ms() if now_ms is None else now_ms
    return mcp.voice_session_start_impl(max_duration_sec=max_sec, now_ms=now)


def _stop(sid: str, now_ms=None) -> dict:
    now = mcp._real_now_ms() if now_ms is None else now_ms
    return asyncio.run(mcp.voice_session_stop_impl(sid, now_ms=now))


# ─────────────────────────────────────────────────────────────
# 剧本 1：正常对话 — Start → Feed 带唤醒词 → Stop → USER_MESSAGE 含转写
# ─────────────────────────────────────────────────────────────

def test_scene1_wake_word_user_message(monkeypatch):
    monkeypatch.setattr(mcp, "_SESSION_ASR_FN", lambda pcm: "嘿 Yua 帮我查一下天气")
    r = _start()
    assert r["status"] == "active"
    sid = r["session_id"]
    assert mcp._SESSION_REGISTRY.is_active(sid)

    f = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.2))
    assert f["status"] == "buffered"
    assert f["bytes_received"] == 6400          # 0.2s × 16000 × 2B
    assert f["silence_detected"] is False
    assert f["rms_latest"] > 0.01               # VAD 有语音能量

    out = _stop(sid)
    assert out["status"] == "completed"
    assert out["route"] == "USER_MESSAGE"       # 带唤醒词 + 命令 → gate 升级
    assert out["text"] == "嘿 Yua 帮我查一下天气"
    assert out["has_speech"] is True
    assert not mcp._SESSION_REGISTRY.is_active(sid)   # 资源已释放


# ─────────────────────────────────────────────────────────────
# 剧本 2：环境杂音 — Start → Feed 无唤醒背景音 → Stop → AMBIENT 无直穿
# ─────────────────────────────────────────────────────────────

def test_scene2_bg_noise_ambient(monkeypatch):
    monkeypatch.setattr(mcp, "_SESSION_ASR_FN", lambda pcm: "今天天气真不错啊")
    r = _start()
    sid = r["session_id"]
    f1 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.3))
    f2 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.3))
    assert f1["status"] == f2["status"] == "buffered"

    out = _stop(sid)
    assert out["status"] == "completed"
    assert out["route"] == "AMBIENT"            # 无唤醒锚点 + 无上下文 → 不直穿
    assert out["route"] != "USER_MESSAGE"
    assert out["text"] == "今天天气真不错啊"     # AMBIENT 回传转写（供 observe 路径）


# ─────────────────────────────────────────────────────────────
# 剧本 3：超时清理 — Start → 不 Stop 超时 → 会话自动过期、状态重置 inactive
# ─────────────────────────────────────────────────────────────

def test_scene3_timeout_auto_cleanup():
    now0 = 1_000_000
    r = mcp.voice_session_start_impl(max_duration_sec=0.1, now_ms=now0)
    sid = r["session_id"]
    assert mcp._SESSION_REGISTRY.is_active(sid)     # 超时前 active

    expired = mcp._SESSION_REGISTRY.expire(now_ms=now0 + 500)   # 0.5s 后 → 超时
    assert expired == 1
    assert not mcp._SESSION_REGISTRY.is_active(sid)             # 状态重置 inactive

    # 过期后 stop → 错误码（Fail-closed，不残留、不误判空会话）
    out = _stop(sid, now_ms=now0 + 500)
    assert out["status"] == "error"
    assert out["error_code"] == "SESSION_NOT_FOUND"


def test_scene3b_timeout_lazy_expire_on_feed():
    now0 = 2_000_000
    r = mcp.voice_session_start_impl(max_duration_sec=0.1, now_ms=now0)
    sid = r["session_id"]
    # 不主动 expire，靠 feed 惰性清理触发
    f = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.1), now_ms=now0 + 500)
    assert f["status"] == "error"
    assert f["error_code"] == "SESSION_NOT_FOUND"
    assert not mcp._SESSION_REGISTRY.is_active(sid)


# ─────────────────────────────────────────────────────────────
# 剧本 4：非法分片容错 — Feed 损坏 Base64 / 空数据 → 错误码 + 会话平稳释放
# ─────────────────────────────────────────────────────────────

def test_scene4_bad_base64_fail_closed():
    r = _start()
    sid = r["session_id"]
    out = mcp.voice_session_feed_impl(sid, "!!!not-valid-base64!!!")
    assert out["status"] == "error"
    assert out["error_code"] == "PCM_DECODE_ERROR"
    assert out["session_id"] == sid
    assert not mcp._SESSION_REGISTRY.is_active(sid)    # 会话平稳释放

    # 已释放 → 后续操作得到 SESSION_NOT_FOUND（无悬挂）
    out2 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.1))
    assert out2["error_code"] == "SESSION_NOT_FOUND"


def test_scene4b_empty_chunk_fail_closed():
    r = _start()
    sid = r["session_id"]
    out = mcp.voice_session_feed_impl(sid, "")
    assert out["status"] == "error"
    assert out["error_code"] == "EMPTY_CHUNK"
    assert not mcp._SESSION_REGISTRY.is_active(sid)

    out2 = _stop(sid)
    assert out2["error_code"] == "SESSION_NOT_FOUND"


def test_scene4c_odd_length_fail_closed():
    r = _start()
    sid = r["session_id"]
    odd = base64.b64encode(b"\x00\x01\x02").decode("ascii")   # 3 字节 → 非偶数
    out = mcp.voice_session_feed_impl(sid, odd)
    assert out["status"] == "error"
    assert out["error_code"] == "PCM_DECODE_ERROR"
    assert not mcp._SESSION_REGISTRY.is_active(sid)


# ─────────────────────────────────────────────────────────────
# 硬断言：无唤醒关键词 + 无上下文 → 100% 降级 AMBIENT/DROP，绝不越权 USER_MESSAGE
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "今天天气真不错",          # 无 name/wake
    "我觉得这个结局好烂",      # 电视/叙述
    "唉,又忘带钥匙了",         # 自言自语
    "啦啦啦啦啦",              # 纯噪音
    "你昨天去哪了",            # 他人对话（只有第二人称，无锚点）
])
def test_ms3_invariant_no_wake_no_context_never_user_message(monkeypatch, text):
    monkeypatch.setattr(mcp, "_SESSION_ASR_FN", lambda pcm, t=text: t)
    r = _start()
    sid = r["session_id"]
    mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.2))
    out = _stop(sid)
    assert out["route"] in ("AMBIENT", "DROP"), f"{text!r} 越权升级 USER_MESSAGE!"
    assert out["route"] != "USER_MESSAGE"


# ─────────────────────────────────────────────────────────────
# process_audio_stream（逻辑层）fail-closed / 判定链单测
# ─────────────────────────────────────────────────────────────

def test_process_audio_stream_silent_pcm_drops():
    silent = np.zeros(1600, dtype=np.float32)

    async def _run():
        return await process_audio_stream(
            silent, asr_fn=lambda pcm: "不应被调用",
            sample_rate=16000, energy_threshold=0.01,
        )

    res = asyncio.run(_run())
    assert isinstance(res, AudioStreamResult)
    assert res.decision == RouteDecision.DROP
    assert res.route == "DROP"
    assert res.has_speech is False


def test_process_audio_stream_asr_error_fail_closed():
    pcm = (0.3 * np.sin(2 * np.pi * 220.0 * np.arange(3200) / 16000)).astype(np.float32)

    def broken_asr(_pcm):
        raise RuntimeError("whisper 挂了")

    async def _run():
        return await process_audio_stream(
            pcm, asr_fn=broken_asr, sample_rate=16000,
        )

    res = asyncio.run(_run())
    assert res.decision == RouteDecision.DROP      # fail-closed：ASR 坏不误升
    assert res.has_speech is False                 # 语音判定被抹平
    assert "whisper" in res.asr_error


def test_process_audio_stream_via_input_router(monkeypatch):
    """判定链 100% 走 InputRouter（bus=None 无发布）：USER_MESSAGE 判定可观测计数。"""
    from src.voice.input_router import VoiceInputRouter
    router = VoiceInputRouter(bus=None)
    pcm = (0.3 * np.sin(2 * np.pi * 220.0 * np.arange(3200) / 16000)).astype(np.float32)

    async def _run():
        return await process_audio_stream(
            pcm, asr_fn=lambda p: "嘿 Yua 帮我查一下天气",
            router=router, sample_rate=16000,
        )

    res = asyncio.run(_run())
    assert res.route == "USER_MESSAGE"
    assert router.user_message_count == 1          # 判定走 InputRouter 计数
    assert router.ambient_count == 0


# ─────────────────────────────────────────────────────────────
# VAD 静音检测 / 分片约束单测
# ─────────────────────────────────────────────────────────────

def test_feed_silence_detected_after_1_5s(monkeypatch):
    r = _start()
    sid = r["session_id"]
    # 3 × 0.6s 静音 = 1.8s 连续静音 ≥ 1.5s → silence_detected=True
    for _ in range(3):
        f = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.6, silent=True))
    assert f["status"] == "buffered"
    assert f["silence_detected"] is True
    assert f["rms_latest"] == 0.0


def test_feed_voice_resets_silence_counter(monkeypatch):
    r = _start()
    sid = r["session_id"]
    mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.6, silent=True))    # 0.6s 静音
    f = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.6, silent=True))
    assert f["silence_detected"] is False          # 1.2s < 1.5s
    f2 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.2))            # 有语音 → 重置
    assert f2["silence_detected"] is False
    f3 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.6, silent=True))
    f4 = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.6, silent=True))
    assert f4["silence_detected"] is False         # 重置后重新累积 1.2s


def test_feed_segment_too_long_rejected_session_kept():
    r = _start()
    sid = r["session_id"]
    # 9s 分片 > 8s（§4.2 上限）→ 拒绝但不销毁会话
    out = mcp.voice_session_feed_impl(sid, synth_pcm_b64(9.0))
    assert out["status"] == "error"
    assert out["error_code"] == "SEGMENT_TOO_LONG"
    assert mcp._SESSION_REGISTRY.is_active(sid)
    f = mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.1))             # 可继续
    assert f["status"] == "buffered"


def test_session_empty_stop_drops():
    r = _start()
    sid = r["session_id"]
    out = _stop(sid)
    assert out["status"] == "completed"
    assert out["route"] == "DROP"                  # 无语音数据 → 无语音能量 → DROP
    assert not mcp._SESSION_REGISTRY.is_active(sid)


# ─────────────────────────────────────────────────────────────
# mock 模式：默认 ASR mock 路径（SOUL_ASR_MOCK 语义）不依赖实体驱动
# ─────────────────────────────────────────────────────────────

def test_session_mock_asr_default_text(monkeypatch):
    """ASR_MOCK=1 时默认转写返回确定性文本；无锚点 → AMBIENT（守门保持）。"""
    monkeypatch.setattr(mcp, "ASR_MOCK", True)
    r = _start()
    sid = r["session_id"]
    mcp.voice_session_feed_impl(sid, synth_pcm_b64(0.2))
    out = _stop(sid)
    assert out["status"] == "completed"
    assert "模擬轉寫" in out["text"]
    assert out["route"] in ("AMBIENT", "DROP")     # 固定 mock 文本无唤醒词 → 不升级
    assert out["route"] != "USER_MESSAGE"