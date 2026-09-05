"""
vad_listener.py — sounddevice 常駐音訊流採集 + VAD 靜音斷句器（VC-1 模組 4；VC-1.1 輸入側改造）。

- VoiceActivityDetector：能量型 VAD（RMS > energy_threshold → speech；
  靜音累計 ≥ silence_threshold_sec → speech_end 斷句）。
- VADListener：包裝音訊串流（sounddevice 懶載入），speech_start 時若茜正在
  說話 → 觸發 Barge-in 打斷；speech_end 時把語音片段送 STT → on_transcript。
  STT 通路（VC-1.1）：asr_service（FishASRService）可注入 — utterance 結束
  → float32 樣本量化 PCM16 → pcm16_to_wav_bytes → transcribe → 文字。
  既有 stt_engine（callable）注入介面保留（測試給 fake）；本地 whisper 路徑已移除。

離線可測：detector / stt_engine / asr_service / stream_factory / playing_check / 回呼全部可注入。
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional


# ─────────────────────────────────────────────────────────────
# 能量型 VAD
# ─────────────────────────────────────────────────────────────

class VoiceActivityDetector:
    """能量型 VAD 靜音斷句器。

    事件：speech_start（由靜→語）、speech_end（靜音達標斷句）。
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        silence_threshold_sec: float = 0.8,
        energy_threshold: float = 0.015,
    ):
        self.sample_rate = sample_rate
        self.silence_threshold_sec = silence_threshold_sec
        self.energy_threshold = energy_threshold
        self._in_speech = False
        self._silence_accum = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def is_speech(self, samples) -> bool:
        """單幀判定：RMS 能量 > 門檻 → 語音。輸入可為 list/tuple/numpy array。"""
        n = 0
        total = 0.0
        for s in samples:
            v = float(s)
            total += v * v
            n += 1
        if n == 0:
            return False
        rms = math.sqrt(total / n)
        return rms > self.energy_threshold

    def feed(self, samples) -> List[str]:
        """餵入一幀樣本，回傳本幀事件列表（可能為空）。"""
        events: List[str] = []
        speech = self.is_speech(samples)
        dur = len(samples) / float(self.sample_rate)
        if speech:
            self._silence_accum = 0.0
            if not self._in_speech:
                self._in_speech = True
                events.append("speech_start")
        else:
            if self._in_speech:
                self._silence_accum += dur
                if self._silence_accum >= self.silence_threshold_sec:
                    self._in_speech = False
                    self._silence_accum = 0.0
                    events.append("speech_end")
            else:
                self._silence_accum = 0.0
        return events

    def reset(self) -> None:
        self._in_speech = False
        self._silence_accum = 0.0


# ─────────────────────────────────────────────────────────────
# 常駐監聽器
# ─────────────────────────────────────────────────────────────

class VADListener:
    """常駐音訊採集 + VAD 斷句 + Barge-in 偵測 + STT 轉錄。

    - stt_engine：callable(語音片段) -> str（可注入；測試給 fake，優先於 asr_service）
    - asr_service：FishASRService（可注入）— utterance 結束 → pcm→wav → transcribe
      （本地 whisper 路徑已移除，改由 Fish Audio 官方 ASR 提供輸入側轉錄）
    - on_transcript：callable(text)，斷句完成時收到轉錄文本
    - on_barge_in：callable()，Bryan 在茜說話時開口 → 觸發 streamer.interrupt()
    - playing_check：callable() -> bool，茜是否正在播放語音
    - stream_factory：callable(listener) -> stream（具 start/stop）；None → sounddevice InputStream
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        detector: Optional[VoiceActivityDetector] = None,
        stt_engine: Optional[Callable] = None,
        asr_service: Optional[object] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        stream_factory: Optional[Callable[["VADListener"], object]] = None,
        playing_check: Optional[Callable[[], bool]] = None,
    ):
        cfg = config or {}
        vad = cfg.get("vad") or {}
        self.config = cfg
        self.sample_rate = int(vad.get("sample_rate", 16000))
        self.detector = detector or VoiceActivityDetector(
            sample_rate=self.sample_rate,
            silence_threshold_sec=float(vad.get("silence_threshold_sec", 0.8)),
            energy_threshold=float(vad.get("energy_threshold", 0.015)),
        )
        self.stt_engine = stt_engine
        self.asr_service = asr_service
        self.on_transcript = on_transcript
        self.on_barge_in = on_barge_in
        self.stream_factory = stream_factory
        self.playing_check = playing_check or (lambda: False)

        self._stream = None
        self._frames_buffer: List[float] = []
        self._running = False

    # ── 測試/共用的幀處理入口 ──

    def feed_frame(self, samples) -> List[str]:
        """餵入一幀樣本：驅動 VAD、Barge-in 判定與斷句轉錄。回傳事件列表。"""
        events: List[str] = []
        for event in self.detector.feed(samples):
            if event == "speech_start":
                self._frames_buffer = []
                if self.playing_check():
                    self._fire_barge_in()
                    events.append("barge_in")
                events.append("speech_start")
            elif event == "speech_end":
                self._fire_transcript()
                events.append("speech_end")
        if self.detector.in_speech:
            self._frames_buffer.extend(samples)
        return events

    # ── 串流生命週期（sounddevice 懶載入） ──

    def start(self) -> None:
        if self._running:
            return
        if self.stream_factory is not None:
            stream = self.stream_factory(self)
        else:
            import sounddevice as sd  # 懶載入

            frames = max(1, int(self.sample_rate * 0.02))
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=frames,
                callback=lambda indata, frames, time, status: self.feed_frame(indata[:, 0].tolist()),
            )
        self._stream = stream
        self._stream.start()
        self._running = True

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass
            self._stream = None
        self.detector.reset()
        self._frames_buffer = []

    # ── 內部 ──

    def _fire_barge_in(self) -> None:
        if self.on_barge_in is None:
            return
        try:
            self.on_barge_in()
        except Exception:
            pass

    def _fire_transcript(self) -> None:
        if not self._frames_buffer:
            return
        captured = list(self._frames_buffer)
        self._frames_buffer = []
        if self.on_transcript is None:
            return
        try:
            text = self._transcribe(captured)
        except Exception:
            text = ""
        text = (text or "").strip()
        if text:
            self.on_transcript(text)

    def _transcribe(self, captured) -> str:
        if self.stt_engine is not None:
            return self.stt_engine(captured)
        if self.asr_service is not None:
            return self._transcribe_via_asr(captured)
        return ""

    def _transcribe_via_asr(self, captured) -> str:
        """Fish Audio 官方 ASR：float32 樣本 → PCM16 → WAV → transcribe（VC-1.1 輸入側）。"""
        import numpy as np  # 懶載入

        try:  # pragma: no cover - 導入路徑相容
            from .stt_service import pcm16_to_wav_bytes
        except ImportError:  # pragma: no cover
            from stt_service import pcm16_to_wav_bytes

        samples = np.asarray(captured, dtype=np.float32)
        pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        wav = pcm16_to_wav_bytes(pcm, sample_rate=self.sample_rate)
        return self.asr_service.transcribe(wav)