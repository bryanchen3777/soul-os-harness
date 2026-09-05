"""
scripts/audio_stream_mcp.py — MS-2 audio-stream-mcp：麦克风语音感知薄 MCP server（IMPLEMENTATION）

定位（照 docs/MULTIMODAL-PERCEPTION-CONTRACT.md §4，MS-1 D4/D7/D8）：
  独立 stdio MCP server 进程（不 import 进主进程）——faster-whisper ASR 模型
  内存/推理与主进程隔离（R5）。仅实现 initialize / tools/list / tools/call /
  shutdown（stdio transport，由 mcp SDK MCPServer 承接）。

  工具（§4.2 schema）：
    - mic_listen      ：ソフト门控采样（VAD 能量门控 + 单次硬上限 4s），返回
                        wav_ref/duration/has_speech/peak_level；采样完成立刻释放
                        音频装置（无后台常驻）。
    - audio_transcribe：本地 faster-whisper small 转写 wav_ref → text/language/duration。

  MS-3.1 additive（会话模式，工单 MS-3.1；既有 mic_listen/audio_transcribe 0 行为变化）：
    - voice_session_start：开启采集会话（PCM 缓冲 + VAD 静音状态机 + 30s 硬超时
      定时器）→ 返回 session_id；
    - voice_session_feed ：推入 PCM16 Base64 分片；连续静音 ≥1.5s → silence_detected；
                          损坏/空白片 → 错误码 + 会话 fail-closed 释放；
    - voice_session_stop ：提取完整 PCM → AudioService.process_audio_stream（ASR 注入 +
      MS-3 路由判定，100% 走 InputRouter）→ {status, route: USER_MESSAGE|AMBIENT|DROP, text}。
    资源生命周期：30s 硬超时 janitor 自动清理；硬件/解码异常 fail-closed 错误码 + 清理，
    绝不抛未捕获异常阻断主循环；判定链 bus=None（0 EventBus 运行时，0 旁路发布）。

  运行规范（§4.3 硬约束）：
    - 单次调用（single-shot）：mic_listen 与 audio_transcribe 是两次独立调用，
      不合并长调用；无流式/长连接。
    - 5s 硬超时：采样 ≤4s（留 1s 收尾返回）；客户端 registry.call 侧另有 5s 超时。
    - 无状态清理：wav 写入 server 私有 OS temp 目录；audio_transcribe 消费后
      立即删除（含异常路径 finally）；进程退出兜底清理；主进程不持有本 server
      内部状态。
    - fail-closed 降级：工具异常 → 抛 RuntimeError → MCP isError → 客户端
      ToolRegistry 降级路径处理（不阻塞主循环）。

  感知边界不变量（MS-1 D1，锁死）：
    - 本进程 0 import EventBus / SpeakerToken / LLM——不做任何发声/递归/自激。
    - 结果只通过 MCP 返回结构化数据，由 Actuator observe 路径以 Ambient
      Observation 注入 Perception/Context；严禁直通 USER_MESSAGE。

  可测试性（非生产行为）：
    - SOUL_AUDIO_MOCK=1：用 numpy 合成正弦波代替真实麦克风（无声卡环境跑通全链路）；
      SOUL_AUDIO_MOCK_SILENT=1：合成静音（验证 has_speech=false 门控）。
    - SOUL_ASR_MOCK=1：转写返回固定文本（不下载模型）。
    - 生产默认两者皆 0 → 真实 sounddevice + faster-whisper small。

  启动（由 ToolRegistry 客户端以 stdio 子进程拉起）：
    <venv>/Scripts/python.exe scripts/audio_stream_mcp.py
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# MS-3.1 additive：会话工具的路由判定需复用 src/voice 逻辑层（gate / input_router /
# audio_service），与本仓其他 scripts 一致地 bootstrap 仓库根到 sys.path。
# （纯 additive：既有 mic_listen / audio_transcribe 单发路径 0 行为变化。）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    sys.stderr.write("mcp SDK 未安裝（pip install mcp）\n")
    raise

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

logger = logging.getLogger("soul_os.audio_stream_mcp")

# ───────────────────────────────────────────────────────────
# 配置（§4 D8：采样硬上限 4s，静音门控）
# ───────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
MAX_SAMPLE_SECONDS = 4.0
DEFAULT_SAMPLE_SECONDS = 3.0
# VAD 能量门控：peak 绝对值低于此 → 判定无语音（静音，不产生转写，防洪泛）
SILENCE_PEAK_THRESHOLD = 0.02

# ── MS-3.1 会话模式常量（voice_session_*，additive）────────────────
# 工单锁定：sample_rate=16000 / max_duration_sec=30.0（安全上限防挂起）/
# energy_threshold=0.01（VAD 静音门控 RMS 阈值）；连续静音 ≥1.5s → silence_detected。
VOICE_SESSION_DEFAULT_SAMPLE_RATE = 16000
VOICE_SESSION_DEFAULT_MAX_SEC = 30.0          # 会话硬超时上限（防挂起）
VOICE_SESSION_MIN_MAX_SEC = 0.1               # 允许调用方传更小值（测试/短会话）
VOICE_SESSION_DEFAULT_ENERGY_THRESHOLD = 0.01
VOICE_SILENCE_DETECT_SEC = 1.5                # 连续静音超此 → silence_detected=True
VOICE_SEGMENT_MAX_SEC = 8.0                   # §4.2 单分片硬上限（契约 MAX_SEGMENT_SECONDS）
VOICE_SESSION_DEADLINE_SLACK_SEC = 0.5        # janitor tick 间隔
VOICE_ASR_TIMEOUT_SEC = 45.0                  # ASR 硬超时（fail-closed → DROP）

# 可测试性开关（生产默认关闭）
AUDIO_MOCK = os.environ.get("SOUL_AUDIO_MOCK", "0") == "1"
AUDIO_MOCK_SILENT = os.environ.get("SOUL_AUDIO_MOCK_SILENT", "0") == "1"
ASR_MOCK = os.environ.get("SOUL_ASR_MOCK", "0") == "1"

# ASR 模型（§4.1 / D7：本地 faster-whisper small，CPU int8）
ASR_MODEL_SIZE = os.environ.get("SOUL_ASR_MODEL", "small")

# 私有 OS temp 工作目录（进程级；退出兜底清理）
_TEMP_DIR = Path(tempfile.mkdtemp(prefix="soul-os-audio-stream-mcp-"))

# ASR 模型懒加载缓存（进程生命周期内复用；主进程不持有）
_MODEL: Any = None


def _cleanup_temp() -> None:
    shutil.rmtree(_TEMP_DIR, ignore_errors=True)


atexit.register(_cleanup_temp)


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ───────────────────────────────────────────────────────────
# 采集层（sounddevice；mock 合成用于无声卡测试）
# ───────────────────────────────────────────────────────────

def _record_audio_real(duration: float, sr: int) -> "np.ndarray":
    """sounddevice 采样。采样完成立刻释放音频装置（无后台流/句柄残留）。"""
    import sounddevice as sd

    frames = int(sr * duration)
    rec = sd.rec(frames, samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    # 显式停流/释放（rec + wait 完成后底层流已停，这里兜底确保释放）
    sd.stop()
    return rec[:, 0]


def _record_audio_mock(duration: float, sr: int, silent: bool = False) -> "np.ndarray":
    if np is None:  # pragma: no cover
        raise RuntimeError("numpy 未安裝（mock 採樣需要 numpy）")
    n = int(sr * duration)
    if silent:
        return np.zeros(n, dtype=np.float32)
    t = np.arange(n) / sr
    # 220Hz 正弦模拟人声（peak 明显高于静音阈值）
    return (0.3 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)


def _record_audio(duration: float) -> "np.ndarray":
    dur = min(max(float(duration), 1.0), MAX_SAMPLE_SECONDS)
    if AUDIO_MOCK:
        return _record_audio_mock(dur, SAMPLE_RATE, silent=AUDIO_MOCK_SILENT)
    return _record_audio_real(dur, SAMPLE_RATE)


def _peak_level(data: "np.ndarray") -> float:
    if np is None or data is None or data.size == 0:
        return 0.0
    return float(np.max(np.abs(data)))


# ───────────────────────────────────────────────────────────
# ASR 层（faster-whisper small；mock 用于不下载模型）
# ───────────────────────────────────────────────────────────

def _transcribe_real(wav_path: Path) -> tuple:
    """faster-whisper small 本地转写（CPU int8；模型懒加载）。"""
    from faster_whisper import WhisperModel  # 本地 import：不占主进程 import 面

    global _MODEL
    if _MODEL is None:
        _MODEL = WhisperModel(ASR_MODEL_SIZE, device="cpu", compute_type="int8")
    segments, info = _MODEL.transcribe(str(wav_path), beam_size=1)
    text = "".join(s.text for s in segments).strip()
    language = getattr(info, "language", "unknown")
    return text, language


def _transcribe_mock(wav_path: Path) -> tuple:
    """mock 转写：不下载模型、不碰 CUDA，返回固定确定性文本。"""
    return "（模擬轉寫）測試環境無 ASR 模型時的確定性輸出", "zh"


async def _transcribe(wav_path: Path) -> tuple:
    if ASR_MOCK:
        return _transcribe_mock(wav_path)
    # 真实转写在 to_thread 跑，避免阻塞 MCP event loop
    return await asyncio.to_thread(_transcribe_real, wav_path)


# ───────────────────────────────────────────────────────────
# MS-3.1 会话模式（voice_session_start / feed / stop，additive）
# ───────────────────────────────────────────────────────────

def _real_now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class VoiceSessionState:
    """单会话采集状态：PCM 缓冲 + VAD 静音状态机 + 超时 deadline。"""

    session_id: str
    sample_rate: int
    energy_threshold: float
    max_duration_sec: float
    deadline_ms: int
    created_ms: int
    buffer: bytearray = field(default_factory=bytearray)  # 原始 PCM16 字节
    bytes_received: int = 0
    continuous_silence_ms: float = 0.0   # VAD：连续静音累积（ms）
    silence_detected: bool = False       # 连续静音 ≥1.5s → True（保持）
    rms_latest: float = 0.0
    peak_latest: float = 0.0
    closed: bool = False


class VoiceSessionRegistry:
    """进程级会话注册表（单进程 stdio MCP server；隔离实体音频资源引用）。

    资源生命周期约束（工单决策 2）：
      - 30s 硬超时 → expire() 自动清理（不残留内存缓冲/挂起线程）；
      - remove() 幂等出表并释放缓冲；进程退出 atexit 兜底 clear()。
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, VoiceSessionState] = {}

    def start(
        self,
        session_id: str,
        sample_rate: int,
        energy_threshold: float,
        max_duration_sec: float,
        now_ms: int,
    ) -> VoiceSessionState:
        st = VoiceSessionState(
            session_id=session_id,
            sample_rate=sample_rate,
            energy_threshold=energy_threshold,
            max_duration_sec=max_duration_sec,
            deadline_ms=now_ms + int(max_duration_sec * 1000),
            created_ms=now_ms,
        )
        self._sessions[session_id] = st
        return st

    def get(self, session_id: str) -> Optional[VoiceSessionState]:
        return self._sessions.get(session_id)

    def is_active(self, session_id: str) -> bool:
        return session_id in self._sessions

    def remove(self, session_id: str) -> Optional[VoiceSessionState]:
        """幂等出表并释放 PCM 缓冲（防重复 stop 双处理 / 残留）。"""
        st = self._sessions.pop(session_id, None)
        if st is not None:
            st.buffer.clear()
            st.closed = True
        return st

    def expire(self, now_ms: Optional[int] = None) -> int:
        """30s 硬超时清理：返回清理条数（惰性 + janitor 共用）。"""
        now = now_ms if now_ms is not None else _real_now_ms()
        stale = [sid for sid, st in self._sessions.items() if now > st.deadline_ms]
        for sid in stale:
            self.remove(sid)
        if stale:
            logger.info(f"[VoiceSession] 硬超时清理 {len(stale)} 个会话（不残留）")
        return len(stale)

    def clear(self) -> int:
        n = len(self._sessions)
        for sid in list(self._sessions):
            self.remove(sid)
        if n:
            logger.info(f"[VoiceSession] 注册表清空 {n} 个会话（进程退出/测试兜底）")
        return n

    def active_count(self) -> int:
        return len(self._sessions)


_SESSION_REGISTRY = VoiceSessionRegistry()
atexit.register(_SESSION_REGISTRY.clear)


def _decode_pcm_chunk(b64_value: str) -> tuple:
    """Base64 → PCM16 → float32 1-D（[-1,1]）。

    返回 (ndarray|None, error_code|None)；error_code ∈ {EMPTY_CHUNK, PCM_DECODE_ERROR}。
    fail-closed：损坏 Base64 / 空 / 奇数长度 → 错误码，绝不抛未捕获异常。
    """
    if b64_value is None or str(b64_value) == "":
        return None, "EMPTY_CHUNK"
    try:
        raw = base64.b64decode(str(b64_value), validate=True)
    except Exception:
        return None, "PCM_DECODE_ERROR"
    if not raw:
        return None, "EMPTY_CHUNK"
    if len(raw) % 2 != 0:
        return None, "PCM_DECODE_ERROR"
    if np is None:  # pragma: no cover
        return None, "PCM_DECODE_ERROR"
    i16 = np.frombuffer(raw, dtype=np.int16)
    return (i16.astype(np.float32) / 32768.0), None


def _rms(x: Any) -> float:
    if np is None or x is None or x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x.astype(np.float32)))))


def _peak(x: Any) -> float:
    if np is None or x is None or x.size == 0:
        return 0.0
    return float(np.max(np.abs(x)))


def voice_session_start_impl(
    sample_rate: int = VOICE_SESSION_DEFAULT_SAMPLE_RATE,
    max_duration_sec: float = VOICE_SESSION_DEFAULT_MAX_SEC,
    energy_threshold: float = VOICE_SESSION_DEFAULT_ENERGY_THRESHOLD,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """初始化采集会话：PCM 缓冲 + VAD 状态机 + 会话超时定时器（deadline）。

    参数 clamp（安全上限防挂起）：max_duration_sec 钳到 [0.1s, 30.0s]；
    sample_rate 钳到 [8000, 48000]；energy_threshold 钳到 [0.0, 1.0]。
    非数值/越界离谱参数 → 错误码 INVALID_PARAMETER，不创建会话。
    """
    now = now_ms if now_ms is not None else _real_now_ms()
    _SESSION_REGISTRY.expire(now)

    try:
        sr = int(sample_rate)
        dur = float(max_duration_sec)
        energy = float(energy_threshold)
    except (TypeError, ValueError):
        return {"status": "error", "error_code": "INVALID_PARAMETER",
                "message": f"参数非法：sample_rate={sample_rate!r} "
                           f"max_duration_sec={max_duration_sec!r} energy_threshold={energy_threshold!r}"}
    if not (8000 <= sr <= 48000):
        return {"status": "error", "error_code": "INVALID_PARAMETER",
                "message": f"sample_rate 越界（8000..48000）：{sr}"}
    if not (0.0 <= energy <= 1.0):
        return {"status": "error", "error_code": "INVALID_PARAMETER",
                "message": f"energy_threshold 越界（0..1）：{energy}"}
    dur = min(max(dur, VOICE_SESSION_MIN_MAX_SEC), VOICE_SESSION_DEFAULT_MAX_SEC)

    sid = uuid.uuid4().hex[:12]
    st = _SESSION_REGISTRY.start(sid, sr, energy, dur, now)
    logger.info(
        f"[VoiceSession] 会话开启 sid={sid} sr={sr} energy={energy} "
        f"max={dur}s deadline_ms={st.deadline_ms}"
    )
    return {
        "session_id": sid,
        "status": "active",
        "max_duration_sec": round(dur, 3),
        "energy_threshold": round(energy, 4),
    }


def voice_session_feed_impl(
    session_id: str,
    pcm_chunk_base64: str,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """推入 PCM 分片：损坏/空白片 → 错误码 + 会话 fail-closed 释放（不残留）。"""
    now = now_ms if now_ms is not None else _real_now_ms()
    _SESSION_REGISTRY.expire(now)
    st = _SESSION_REGISTRY.get(session_id)
    if st is None:
        return {"status": "error", "error_code": "SESSION_NOT_FOUND",
                "session_id": session_id,
                "message": "会话不存在或已超时过期（30s 硬超时自动清理，不残留）"}

    try:
        raw_bytes = base64.b64decode(str(pcm_chunk_base64), validate=True)
    except Exception:
        _SESSION_REGISTRY.remove(session_id)
        return {"status": "error", "error_code": "PCM_DECODE_ERROR",
                "session_id": session_id,
                "message": "Base64 解码失败（损坏分片）→ 会话 fail-closed 释放"}

    if not raw_bytes:
        _SESSION_REGISTRY.remove(session_id)
        return {"status": "error", "error_code": "EMPTY_CHUNK",
                "session_id": session_id,
                "message": "空分片（0 字节）→ 会话 fail-closed 释放"}
    if len(raw_bytes) % 2 != 0:
        _SESSION_REGISTRY.remove(session_id)
        return {"status": "error", "error_code": "PCM_DECODE_ERROR",
                "session_id": session_id,
                "message": "PCM16 分片长度非偶数 → 会话 fail-closed 释放"}

    chunk_sec = len(raw_bytes) / 2.0 / st.sample_rate
    if chunk_sec > VOICE_SEGMENT_MAX_SEC:
        # §4.2 单分片硬上限：拒绝该分片（不销毁会话，调用方可重新切分）
        return {"status": "error", "error_code": "SEGMENT_TOO_LONG",
                "session_id": session_id,
                "message": f"单分片 {chunk_sec:.1f}s > {VOICE_SEGMENT_MAX_SEC}s（§4.2 上限）→ 拒绝"}

    if np is None:  # pragma: no cover
        return {"status": "error", "error_code": "INTERNAL_ERROR",
                "session_id": session_id, "message": "numpy 不可用"}

    chunk, _err = _decode_pcm_chunk(str(pcm_chunk_base64))
    dur_ms = chunk.size / st.sample_rate * 1000.0
    st.rms_latest = _rms(chunk)
    st.peak_latest = _peak(chunk)
    if st.rms_latest >= st.energy_threshold:
        st.continuous_silence_ms = 0.0          # 有语音 → 重置静音累积
        st.silence_detected = False
    else:
        st.continuous_silence_ms += dur_ms      # 静音累积
        if st.continuous_silence_ms >= VOICE_SILENCE_DETECT_SEC * 1000.0:
            st.silence_detected = True          # 连续静音 ≥1.5s → 标记

    st.buffer += raw_bytes
    st.bytes_received = len(st.buffer)
    return {
        "status": "buffered",
        "bytes_received": st.bytes_received,
        "silence_detected": st.silence_detected,
        "rms_latest": round(st.rms_latest, 5),
        "peak_latest": round(st.peak_latest, 5),
    }


async def voice_session_stop_impl(
    session_id: str, now_ms: Optional[int] = None
) -> Dict[str, Any]:
    """关闭会话：提取完整 PCM → AudioService.process_audio_stream（ASR 注入 +
    MS-3 路由判定）→ 释放资源。fail-closed：任何异常 → 错误码 + 清理，不抛断主循环。"""
    now = now_ms if now_ms is not None else _real_now_ms()
    _SESSION_REGISTRY.expire(now)
    # 先值拷贝 PCM（remove 会释放缓冲），再幂等出表防重复 stop 双处理
    st = _SESSION_REGISTRY.get(session_id)
    if st is None:
        return {"status": "error", "error_code": "SESSION_NOT_FOUND",
                "session_id": session_id,
                "message": "会话不存在或已超时过期（30s 硬超时自动清理，不残留）"}
    pcm_bytes = bytes(st.buffer)
    st = _SESSION_REGISTRY.remove(session_id)

    try:
        if not pcm_bytes:
            pcm = None
        else:
            if np is None:  # pragma: no cover
                return {"status": "error", "error_code": "INTERNAL_ERROR",
                        "session_id": session_id, "message": "numpy 不可用"}
            pcm = (
                np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
                / 32768.0
            )

        from src.voice.audio_service import process_audio_stream  # 惰性：仅 stop 路径

        asr_fn = _SESSION_ASR_FN if _SESSION_ASR_FN is not None else _transcribe_pcm_stream
        result = await process_audio_stream(
            pcm,
            asr_fn,
            config=_get_voice_config(),
            router=_get_session_router(),
            sample_rate=st.sample_rate,
            energy_threshold=st.energy_threshold,
            in_conversation=False,        # 设备进程无对话上下文通道（§2.4 不变量源头）
            device_ref="voice-session",
            asr_timeout_sec=VOICE_ASR_TIMEOUT_SEC,
        )
        logger.info(
            f"[VoiceSession] 关闭 sid={session_id} route={result.route} "
            f"bytes={st.bytes_received} score={result.address_score:.1f} "
            f"stage={result.stage} text={result.text[:40]!r}"
        )
        return {
            "status": "completed",
            "route": result.route,        # USER_MESSAGE / AMBIENT / DROP
            "text": result.text,
            "session_id": session_id,
            "has_speech": result.has_speech,
            "address_score": result.address_score,
            "stage": result.stage,
            "reason": result.reason,
            "asr_error": result.asr_error,
        }
    except Exception as e:  # fail-closed：绝不抛未捕获异常阻断 MCP 主循环
        logger.exception("[VoiceSession] stop 处理异常 → fail-closed 错误码")
        return {"status": "error", "error_code": "INTERNAL_ERROR",
                "session_id": session_id, "message": f"{e!r}"}


def _transcribe_pcm_stream(pcm: Any) -> str:
    """会话模式转写（MCP 进程内）：mock 返回确定性文本；真实路径写私有 temp wav
    走 faster-whisper small（消费后立即删除，对齐 audio_transcribe 无状态清理）。"""
    if ASR_MOCK:
        return _transcribe_mock(None)[0]
    import soundfile as sf
    wav_path = _TEMP_DIR / f"session_audio_{_utc_ts()}_{uuid.uuid4().hex[:8]}.wav"
    sf.write(str(wav_path), pcm, SAMPLE_RATE)
    try:
        text, _lang = _transcribe_real(wav_path)
        return text
    finally:
        wav_path.unlink(missing_ok=True)


# 会话 ASR 注入点：默认 _transcribe_pcm_stream；测试可替换为确定文本回调（mock 模式）
_SESSION_ASR_FN: Optional[Callable[[Any], str]] = None

_VOICE_CONFIG: Any = None
_SESSION_ROUTER: Any = None


def _get_voice_config() -> Any:
    """VoiceGateConfig 懒加载（env 覆盖生效；失败 → 默认值，fail-closed）。"""
    global _VOICE_CONFIG
    if _VOICE_CONFIG is None:
        try:
            from src.voice.gate import VoiceGateConfig
            _VOICE_CONFIG = VoiceGateConfig.from_env()
        except Exception as e:  # pragma: no cover
            logger.warning(f"[VoiceSession] VoiceGateConfig 加载失败用默认：{e!r}")
            from src.voice.gate import VoiceGateConfig
            _VOICE_CONFIG = VoiceGateConfig()
    return _VOICE_CONFIG


def _get_session_router() -> Any:
    """VoiceInputRouter(bus=None) 懒加载：判定链 100% 走 InputRouter（gate + §4.5 防洪
    + trace）；无 bus 环境只 log 不发布（0 旁路注入，MCP 进程 0 EventBus 运行时）。"""
    global _SESSION_ROUTER
    if _SESSION_ROUTER is None:
        try:
            from src.voice.input_router import VoiceInputRouter
            _SESSION_ROUTER = VoiceInputRouter(bus=None, config=_get_voice_config())
        except Exception as e:  # pragma: no cover — 注入失败 → gate.route 纯函数兜底
            logger.warning(f"[VoiceSession] InputRouter 注入失败用 gate.route 兜底：{e!r}")
            _SESSION_ROUTER = None
    return _SESSION_ROUTER


# ── 会话超时 janitor（后台定时器，防挂起/残留）────────────────────
_JANITOR_TASK: Any = None


async def _session_janitor_loop(interval_sec: float = VOICE_SESSION_DEADLINE_SLACK_SEC) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        _SESSION_REGISTRY.expire(_real_now_ms())


def _ensure_janitor() -> None:
    """在 MCP event loop 内启动 30s 硬超时清理任务（幂等）。

    同步/无 loop 上下文（单测直调 impl）→ 跳过，由每次工具调用惰性 expire 兜底。
    """
    global _JANITOR_TASK
    if _JANITOR_TASK is not None and not _JANITOR_TASK.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _JANITOR_TASK = loop.create_task(_session_janitor_loop())


# ───────────────────────────────────────────────────────────
# MCP Server 组装
# ───────────────────────────────────────────────────────────

def build_server() -> MCPServer:
    server = MCPServer(
        "soul-os-audio-stream",
        instructions="audio-stream-mcp：麦克风语音感知（Ambient Observation）。"
                     "语音转写只作环境感知回流，不直通对话。",
    )

    @server.tool()
    async def mic_listen(duration_seconds: float = DEFAULT_SAMPLE_SECONDS) -> Dict[str, Any]:
        """Listen to ambient audio via microphone. Voice input captured as ambient
        observation. 通过麦克风采集周围环境声音（语音感知），单次采样上限 4 秒。
        """
        dur = min(max(float(duration_seconds), 1.0), MAX_SAMPLE_SECONDS)
        data = await asyncio.to_thread(_record_audio, dur)
        peak = _peak_level(data)
        has_speech = peak >= SILENCE_PEAK_THRESHOLD
        wav_filename = f"audio_{_utc_ts()}.wav"
        wav_path = _TEMP_DIR / wav_filename

        import soundfile as sf
        sf.write(str(wav_path), data, SAMPLE_RATE)

        # 返回结构化结果；wav 保留在 server 私有 temp，供 audio_transcribe 消费
        return {
            "wav_ref": wav_filename,
            "duration": round(dur, 2),
            "has_speech": has_speech,
            "peak_level": round(peak, 4),
        }

    @server.tool()
    async def audio_transcribe(wav_ref: str) -> Dict[str, Any]:
        """Transcribe recorded audio (wav_ref from mic_listen) to text using local
        ASR. 将录音转写为文字（本地语音识别 STT）。
        """
        if not wav_ref or not isinstance(wav_ref, str):
            raise RuntimeError("wav_ref 必須是 mic_listen 返回的非空字串")
        # 路径安全：只允许 server 私有 temp 目录内的文件（防路径穿越）
        name = os.path.basename(wav_ref)
        wav_path = _TEMP_DIR / name
        if not wav_path.is_file():
            raise RuntimeError(f"wav_ref 不存在（可能已消费或超时清理）: {wav_ref}")

        import soundfile as sf
        try:
            info = sf.info(str(wav_path))
            audio_duration = round(float(info.frames) / float(info.samplerate), 2)
            text, language = await _transcribe(wav_path)
            return {
                "text": text,
                "language": language,
                "duration": audio_duration,
                "wav_ref": name,
            }
        finally:
            # 无状态清理：消费后立即删除（含异常路径）
            wav_path.unlink(missing_ok=True)

    @server.tool()
    async def voice_session_start(
        sample_rate: int = VOICE_SESSION_DEFAULT_SAMPLE_RATE,
        max_duration_sec: float = VOICE_SESSION_DEFAULT_MAX_SEC,
        energy_threshold: float = VOICE_SESSION_DEFAULT_ENERGY_THRESHOLD,
    ) -> Dict[str, Any]:
        """Start a session-mode voice capture (MS-3). 开启会话式语音采集：初始化
        PCM 缓冲 + VAD 静音状态机 + 30s 硬超时定时器。返回 session_id 供
        voice_session_feed / voice_session_stop 使用。既有 mic_listen 单发路径不变。"""
        _ensure_janitor()
        return voice_session_start_impl(sample_rate, max_duration_sec, energy_threshold)

    @server.tool()
    async def voice_session_feed(session_id: str, pcm_chunk_base64: str) -> Dict[str, Any]:
        """Feed a PCM16 chunk (base64) into the voice session. 推入语音分片
        （PCM 16-bit Mono Base64）；连续静音 ≥1.5s → silence_detected=True。
        损坏 Base64 / 空数据 / 奇数长度 → 错误码 + 会话 fail-closed 释放。"""
        _ensure_janitor()
        return voice_session_feed_impl(session_id, pcm_chunk_base64)

    @server.tool()
    async def voice_session_stop(session_id: str) -> Dict[str, Any]:
        """Stop & finalize the voice session: ASR + MS-3 routing decision. 关闭会话：
        提取完整 PCM → ASR 转写 → MS-3 路由判定（无唤醒+无上下文 100% 降级
        AMBIENT/DROP）→ 释放资源。返回 {status, route, text}。"""
        _ensure_janitor()
        return await voice_session_stop_impl(session_id)

    return server


def main() -> None:
    build_server().run(transport="stdio")


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()