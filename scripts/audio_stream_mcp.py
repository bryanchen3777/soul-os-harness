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
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    sys.stderr.write("mcp SDK 未安裝（pip install mcp）\n")
    raise

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None  # type: ignore

# ───────────────────────────────────────────────────────────
# 配置（§4 D8：采样硬上限 4s，静音门控）
# ───────────────────────────────────────────────────────────

SAMPLE_RATE = 16000
MAX_SAMPLE_SECONDS = 4.0
DEFAULT_SAMPLE_SECONDS = 3.0
# VAD 能量门控：peak 绝对值低于此 → 判定无语音（静音，不产生转写，防洪泛）
SILENCE_PEAK_THRESHOLD = 0.02

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

    return server


def main() -> None:
    build_server().run(transport="stdio")


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()