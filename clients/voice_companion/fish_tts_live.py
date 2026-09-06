"""
fish_tts_live.py — Fish Audio WebSocket TTS-Live 即時串流（VC-1.2 模組；VC-1.6 換官方 SDK 傳輸）。

- Endpoint: wss://api.fish.audio/v1/tts/live（由官方 fish-audio-sdk 驅動）。
- 真實連線層 = fish_audio_sdk.WebSocketSession.tts(TTSRequest, text_iter, backend=model)：
  官方 SDK 內部自行送 start → text×N → CloseEvent(=stop)，並 yield audio 分片 bytes。
  實測（2026-09-05）：同 key/voice/model 經 SDK 合成 102,400 bytes PCM；websocket-client
  自送 msgpack 會被伺服器「start 後空 ack 即斷線」拒收 → VC-1.6 全面改用 SDK。
- 音訊格式：pcm（raw PCM16LE 44100Hz mono）→ audio_player 邊收邊播。
- Barge-in：interrupt() → 關閉目前 SDK session（中止合成）＋停止播放＋清空佇列＋狀態重置；
  下一回合 start() 後重開全新 session。

離線可測：session_factory（回傳具 tts(request, text_iter, backend) 的假 SDK session）與
audio_player（open/write/stop/close）均可注入；fish-audio-sdk / sounddevice 一律懶載入。
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, List, Optional

DEFAULT_LIVE_ENDPOINT = "wss://api.fish.audio/v1/tts/live"
DEFAULT_MODEL = "s2.1-pro-free"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHUNK_LENGTH = 300
DEFAULT_LATENCY = "normal"
# 相容保留（舊 flush 語意由 SDK 改為 chunk_length 緩衝合成；此常數不再參與協定）
FLUSH_PUNCTUATION = "，。、！？…\n"


class FishTTSError(RuntimeError):
    """Fish Audio TTS-Live 協定 / 連線失敗。"""


# ─────────────────────────────────────────────────────────────
# 預設音訊輸出（sounddevice 懶載入）
# ─────────────────────────────────────────────────────────────

class PCMAudioSink:
    """sounddevice 播放器：PCM16LE bytes → OutputStream(44100, 1ch, int16).write()。

    收到每個 audio 分片立即 write（邊生邊播）；未安裝 sounddevice 時拋出明確錯誤。
    """

    def __init__(self, samplerate: int = DEFAULT_SAMPLE_RATE, channels: int = 1, dtype: str = "int16"):
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self._stream = None

    def open(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd  # 懶載入

        self._stream = sd.OutputStream(
            samplerate=self.samplerate, channels=self.channels, dtype=self.dtype
        )
        self._stream.start()

    def write(self, chunk: bytes) -> None:
        self.open()
        if not chunk:
            return
        self._stream.write(chunk)

    def stop(self) -> None:
        """立即停止輸出（interrupt 用；stream 保留，下次 session 可復用）。"""
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                pass

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None


# ─────────────────────────────────────────────────────────────
# TTS-Live 串流器主體（官方 fish-audio-sdk 傳輸）
# ─────────────────────────────────────────────────────────────

class FishTTSLiveStreamer:
    """Fish Audio WebSocket TTS-Live 串流客戶端 + PCM 邊收邊播 + Barge-in。

    真實連線（VC-1.6）：
    - 每回合（utterance）建立一個 fish_audio_sdk.WebSocketSession；
    - daemon worker thread 驅動 session.tts(TTSRequest(...), text_iter, backend=model)，
      逐 audio 分片依序 audio_player.write()（邊生邊播）；
    - 內部 thread-safe queue 供給 text_iter：feed_text_piece(piece) push、end_session() 收尾。
    - interrupt()：關閉目前 SDK session（中斷合成）＋ audio_player.stop()＋清佇列＋狀態重置。

    注入點：
    - session_factory(api_key): 回傳具 tts(request, text_iter, backend) 的物件
      （預設 fish_audio_sdk.WebSocketSession；離線測試注入假 SDK session）
    - audio_player: 具 open() / write(bytes) / stop() / close() 介面
      （AudioRelaySink 為 web 版注入；PCMAudioSink 為預設本機播放）
    - interrupt_event: 可注入 threading.Event（外部攔截 / 測試用）

    API 表面（與既有 FishTTSStreamer 相容，akane_live / web_server 呼叫點不變）：
    - speak(clause_texts): 開 session → 逐句餵送 → 收尾（同步；邊收邊播）
    - feed_text_piece(piece: str): 串流 token 模式 — push 進隊列；無活動 session 自動開新；
      配合 end_session() 收尾
    - end_session(): 串流 feed 收尾 — 停止餵送 → 等 worker 播完 → 關 SDK session
    - interrupt(): Barge-in 入口 — 置位 interrupt_event、關閉 session、停止播放、清空佇列、狀態重置
    - close() / stop(): 完全關閉（SDK session + 播放器 + 狀態）
    - start(): 釋放 interrupt 旗標，允許新 session
    - is_playing / queue_size / pending_chunks(): 與 REST streamer 同語意的唯讀查詢
    """

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "",
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_LIVE_ENDPOINT,
        live_format: str = "pcm",
        socket_factory: Optional[Callable[[], object]] = None,  # 相容保留（VC-1.6 起棄用，傳入會被忽略）
        audio_player: Optional[object] = None,
        interrupt_event: Optional[threading.Event] = None,
        session_factory: Optional[Callable[[str], object]] = None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model or DEFAULT_MODEL
        self.endpoint = endpoint
        self.live_format = live_format or "pcm"

        self._session_factory = session_factory or self._default_session_factory
        self._audio = audio_player or PCMAudioSink()
        self.interrupt_event = interrupt_event or threading.Event()

        self._feed_queue: "queue.Queue[str]" = queue.Queue()
        self._current_session = None  # 活動 SDK session（utterance 期間）
        self._worker: Optional[threading.Thread] = None  # 活動 utterance worker
        self._chunks: List[bytes] = []  # 音訊佇列（收到即播；interrupt/結束清空）
        self._playing = False
        self._interrupted = False
        self._closed = False
        self.last_error: Optional[Exception] = None
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config: dict) -> "FishTTSLiveStreamer":
        """依 config fish_audio 區段組裝（api_key 由 env_config 解析後帶入）。"""
        fa = (config or {}).get("fish_audio") or {}
        return cls(
            api_key=fa.get("api_key", ""),
            voice_id=fa.get("voice_id", ""),
            model=fa.get("model", DEFAULT_MODEL),
            endpoint=fa.get("tts_ws_endpoint", DEFAULT_LIVE_ENDPOINT),
            live_format=fa.get("live_format", "pcm"),
        )

    # ── 內部：SDK session／worker ──

    def _default_session_factory(self, api_key: str) -> object:
        """官方 fish-audio-sdk WebSocketSession（httpx_ws 傳輸，實證可用）。"""
        from fish_audio_sdk import WebSocketSession  # 懶載入

        return WebSocketSession(api_key)

    def _text_iter(self):
        """blocking text 供給器：feed push 的字串逐個 yield；None 或 interrupt/close → 結束。"""
        while not (self._interrupted or self._closed):
            try:
                piece = self._feed_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if piece is None:
                return
            yield piece

    def _sdk_request(self):
        from fish_audio_sdk import TTSRequest  # 懶載入

        return TTSRequest(
            text="",
            reference_id=self.voice_id,
            format=self.live_format,
            sample_rate=DEFAULT_SAMPLE_RATE,  # 明確 44100：None 讓伺服器預設，避免樣本率不確定（怪聲）
            chunk_length=DEFAULT_CHUNK_LENGTH,
            latency=DEFAULT_LATENCY,
        )

    def _run_worker(self, session: object, backend: str) -> None:
        """daemon thread：驅動 SDK tts generator → 依序寫 audio_player；例外不 crash 呼叫端。"""
        gen = None
        try:
            gen = session.tts(self._sdk_request(), self._text_iter(), backend=backend)
            for chunk in gen:
                if self._interrupted or self._closed:
                    break
                if chunk:
                    self._play(chunk)
        except Exception as exc:
            if not (self._interrupted or self._closed):
                self.last_error = exc
                try:
                    self._audio.stop()
                except Exception:
                    pass
        finally:
            if gen is not None:
                try:
                    gen.close()
                except Exception:
                    pass
            with self._lock:
                self._chunks.clear()
                if self._current_session is session:
                    self._current_session = None
                self._worker = None
            if not (self._interrupted or self._closed):
                try:
                    session.close()
                except Exception:
                    pass
            self._set_playing(False)

    def _open_session(self) -> object:
        """開新 utterance：SDK session + daemon worker；回傳 session。"""
        session = self._session_factory(self.api_key)
        with self._lock:
            self._current_session = session
            self._chunks.clear()
            self._feed_queue = queue.Queue()
            worker = threading.Thread(
                target=self._run_worker, args=(session, self.model), daemon=True, name="fish-tts-live-worker"
            )
            self._worker = worker
        worker.start()
        return session

    def _finish_utterance(self, join_timeout: float = 60.0) -> None:
        """餵送結束（None）→ join worker → 關 SDK session → 狀態重置。"""
        try:
            self._feed_queue.put(None)
        except Exception:
            pass
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=join_timeout)
        with self._lock:
            sess = self._current_session
            self._current_session = None
            self._worker = None
            self._chunks.clear()
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        self._set_playing(False)

    # ── 對外 API ──

    def speak(self, clause_texts: List[str]) -> None:
        """開 session → 逐句餵送 → 收尾（同步；邊收邊播）。

        interrupt / close 後為 no-op；已有活動 session 時 no-op（避免重入）。
        SDK 例外在 worker 內記錄 last_error，不 crash 主迴圈。
        """
        if self._interrupted or self._closed:
            return
        with self._lock:
            if self._current_session is not None:
                return  # 已有 session 進行中，避免重入
        try:
            self._open_session()
        except Exception as exc:
            self.last_error = exc
            return
        try:
            for clause in clause_texts:
                self._feed_queue.put(clause)
        finally:
            self._finish_utterance()

    def feed_text_piece(self, piece: str) -> None:
        """串流 token 模式：push 進隊列（SDK 依 chunk_length 緩衝合成）。

        無活動 session 時自動開新 session；interrupt / close 後 no-op。
        """
        if self._interrupted or self._closed:
            return
        try:
            if self._current_session is None:
                self._open_session()
            self._feed_queue.put(piece)
        except Exception as exc:
            self.last_error = exc

    def end_session(self) -> None:
        """串流 feed 收尾：停止餵送 → 等 worker 播完 → 關 SDK session。"""
        if self._current_session is None:
            return
        self._finish_utterance()

    def interrupt(self) -> None:
        """Barge-in 入口（監聽器偵測到 Bryan 重新說話時呼叫）：
        1. 置位 interrupt_event
        2. 關閉目前 SDK session（中止 server 合成，worker 因 recv 中斷而退出）
        3. 停止當前音訊輸出
        4. 清空音訊佇列、播放狀態重置；interrupt 後 speak()/feed 為 no-op
        """
        self.interrupt_event.set()
        self._interrupted = True
        with self._lock:
            sess = self._current_session
            self._current_session = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        try:
            self._audio.stop()
        except Exception:
            pass
        with self._lock:
            self._chunks.clear()
        self._set_playing(False)

    def start(self) -> None:
        """啟動（與 REST streamer 相容）：釋放 interrupt 旗標，允許新 session。"""
        self._interrupted = False
        self.interrupt_event.clear()
        self.last_error = None

    def stop(self) -> None:
        """完全關閉：關 SDK session + 停止/關閉播放器 + 清佇列 + 狀態重置。"""
        self._closed = True
        self.interrupt_event.set()
        self._interrupted = True
        with self._lock:
            sess = self._current_session
            self._current_session = None
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        try:
            self._audio.close()
        except Exception:
            pass
        with self._lock:
            self._chunks.clear()
        self._set_playing(False)

    close = stop  # 語意別名（akane_live 等呼叫點使用 close()）

    # ── 播放 ──

    def _play(self, audio: bytes) -> None:
        self._chunks.append(audio)
        self._set_playing(True)
        try:
            self._audio.write(audio)
        except Exception:
            pass  # 播放失敗不癱瘓 session
        finally:
            self._set_playing(False)

    @property
    def queue_size(self) -> int:
        return len(self._chunks)

    def pending_chunks(self) -> List[bytes]:
        """佇列快照（供偵測/測試使用）。"""
        return list(self._chunks)

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def _set_playing(self, flag: bool) -> None:
        with self._lock:
            self._playing = flag
