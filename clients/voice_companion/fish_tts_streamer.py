"""
fish_tts_streamer.py — Fish Audio TTS 串流播放器 + Barge-in 打斷（VC-1 模組 3）。

- POST https://api.fish.audio/v1/tts，Headers: Authorization: Bearer <api_key>
- Payload: {"text": clause_text, "reference_id": voice_id, "format": "mp3"}
- 多執行緒播放佇列（enqueue → 播放執行緒依序播）
- Barge-in：interrupt() → 立即停止音訊輸出 + 清空佇列 + 取消排隊中的請求

離線可測：session（HTTP）與 audio_device（播放/停止）均可注入；
sounddevice / requests 一律懶載入。
"""

from __future__ import annotations

import queue
import threading
from typing import List, Optional

DEFAULT_ENDPOINT = "https://api.fish.audio/v1/tts"


class FishTTSError(RuntimeError):
    """Fish Audio API / 播放失敗。"""


# ─────────────────────────────────────────────────────────────
# 預設音訊輸出（sounddevice 懶載入）
# ─────────────────────────────────────────────────────────────

class SoundDeviceAudio:
    """sounddevice 播放器：mp3 bytes → (miniaudio 解碼) → sd.play。

    未安裝 miniaudio 時拋出明確錯誤；安裝於 clients/voice_companion/requirements.txt。
    """

    def play(self, chunk: bytes) -> None:
        import numpy as np
        import sounddevice as sd

        try:
            import miniaudio
        except ImportError as exc:  # pragma: no cover - 依賴缺省提示
            raise FishTTSError(
                "播放 mp3 需要 miniaudio 解碼器：pip install miniaudio（或注入自訂 audio_device）"
            ) from exc
        decoded = miniaudio.decode(chunk)  # mp3 → PCM
        samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(samples, samplerate=decoded.sample_rate)

    def stop(self) -> None:
        import sounddevice as sd

        sd.stop()


# ─────────────────────────────────────────────────────────────
# 串流播放器主體
# ─────────────────────────────────────────────────────────────

class FishTTSStreamer:
    """Fish Audio API 串流客戶端 + 音訊播放器 + Barge-in 打斷。

    - session:      可注入的 HTTP session（具 post(url, json=, headers=, timeout=) 介面）
    - audio_device: 可注入的播放裝置（具 play(bytes) / stop() 介面）
    - interrupt():  立即停止播放、清空佇列、取消後續請求（Barge-in 入口）
    """

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "",
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = "",
        session=None,
        audio_device=None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.endpoint = endpoint
        self.model = model
        self._session = session
        self._audio = audio_device or SoundDeviceAudio()

        self.interrupt_event = threading.Event()
        self._cancel_event = threading.Event()
        self._queue: "queue.Queue[bytes]" = queue.Queue()
        self._player_thread: Optional[threading.Thread] = None
        self._playing = False
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: dict) -> "FishTTSStreamer":
        fa = (config or {}).get("fish_audio") or {}
        return cls(
            api_key=fa.get("api_key", ""),
            voice_id=fa.get("voice_id", ""),
            endpoint=fa.get("endpoint", DEFAULT_ENDPOINT),
            model=fa.get("model", ""),
        )

    # ── HTTP 合成 ──

    def _ensure_session(self):
        if self._session is None:
            import requests  # 懶載入

            self._session = requests.Session()
        return self._session

    def synthesize(self, clause_text: str) -> bytes:
        """呼叫 Fish Audio TTS，回傳 mp3 bytes。取消旗標已設 → 直接放棄。"""
        if self._cancel_event.is_set():
            return b""
        payload = {
            "text": clause_text,
            "reference_id": self.voice_id,
            "format": "mp3",
            "model": self.model,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        session = self._ensure_session()
        resp = session.post(self.endpoint, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise FishTTSError(f"Fish Audio TTS HTTP {resp.status_code}: {getattr(resp, 'text', '')[:200]}")
        return resp.content

    # ── 播放佇列 ──

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def enqueue_audio(self, audio_bytes: bytes) -> None:
        if self._cancel_event.is_set():
            return
        self._queue.put(audio_bytes)

    def speak(self, clause_text: str) -> None:
        """合成 → 入隊（同步調用；interrupt 後為 no-op，不再發出 HTTP 請求）。"""
        if self._cancel_event.is_set():
            return
        audio = self.synthesize(clause_text)
        if audio:
            self.enqueue_audio(audio)

    # ── 播放執行緒 ──

    def start(self) -> None:
        """啟動播放執行緒（重置 interrupt/cancel 旗標）。"""
        if self._player_thread is not None and self._player_thread.is_alive():
            return
        self.interrupt_event.clear()
        self._cancel_event.clear()
        self._player_thread = threading.Thread(target=self._player_loop, daemon=True, name="fish-tts-player")
        self._player_thread.start()

    def _player_loop(self) -> None:
        while not self.interrupt_event.is_set():
            try:
                chunk = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if self.interrupt_event.is_set():
                self._drain_queue()
                break
            self._set_playing(True)
            try:
                self._audio.play(chunk)
            except Exception:
                pass  # 播放失敗不癱瘓佇列
            finally:
                self._set_playing(False)
                self._queue.task_done()
        self._drain_queue()

    def stop(self) -> None:
        """完全關閉播放：interrupt + 結束執行緒 + 清佇列。"""
        self.interrupt_event.set()
        if self._player_thread is not None:
            try:
                self._player_thread.join(timeout=2.0)
            except RuntimeError:
                pass
            self._player_thread = None
        self._drain_queue()
        self._audio.stop()
        self._set_playing(False)

    close = stop  # 語意別名

    # ── Barge-in 打斷 ──

    def interrupt(self) -> None:
        """Barge-in 入口（監聽器偵測到 Bryan 重新說話時呼叫）：
        1. 立即停止當前音訊流輸出（sd.stop）
        2. 清空待播放的音訊佇列
        3. 取消正在排隊的 HTTP 請求（合成/入隊 no-op）
        """
        self.interrupt_event.set()
        self._cancel_event.set()
        self._audio.stop()
        self._drain_queue()
        self._set_playing(False)

    def resume(self) -> None:
        """打斷後恢復（監聽器回到聆聽時呼叫）。"""
        self.interrupt_event.clear()
        self._cancel_event.clear()

    # ── 內部 ──

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

    def _set_playing(self, flag: bool) -> None:
        with self._lock:
            self._playing = flag

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    # 供偵測/測試使用的佇列快照
    def pending_chunks(self) -> List[bytes]:
        items: List[bytes] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        for item in items:
            self._queue.put(item)
        return items