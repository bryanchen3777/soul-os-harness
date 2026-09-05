"""
fish_tts_live.py — Fish Audio WebSocket TTS-Live 即時串流（VC-1.2 模組）。

- Endpoint: wss://api.fish.audio/v1/tts/live，Headers: Authorization: Bearer <api_key>, model: <model_name>
- 序列化：MessagePack（msgpack）。所有 client→server 與 server→client 訊息都是 msgpack map。
- Client→Server 事件（依序）：
    1. start：{"event":"start","request":{text:"",format,chunk_length,reference_id,latency}}
    2. text ：{"event":"text","text":"<分句文字>"}（可連續多個，server 依 chunk_length 緩衝合成）
    3. flush：{"event":"flush"}（強制立即合成緩衝文字，互動場景降低延遲）
    4. stop ：{"event":"stop"}（結束 session；server 合成完殘餘後回 finish 才斷線）
- Server→Client 事件：
    1. audio ：{"event":"audio","audio":<binary bytes>}（可多個，需依序播放/拼接）
    2. finish：{"event":"finish","reason":"stop"|"error"}（session 結束，連線隨後關閉）
- 音訊格式：pcm（raw PCM16LE，44100Hz mono）→ sounddevice OutputStream 邊收邊播。
- Barge-in：interrupt() → 立即關閉目前 WS 連線（中止合成）＋停止播放＋清空佇列＋狀態重置；
  下一句 speak() 重開全新 session（interrupt 後 speak 為 no-op）。

離線可測：socket_factory（回傳具 send/recv/close 的假 WS）與 audio_player（open/write/stop/close）
均可注入；msgpack / websocket-client / sounddevice 一律懶載入。
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

DEFAULT_LIVE_ENDPOINT = "wss://api.fish.audio/v1/tts/live"
DEFAULT_MODEL = "s2.1-pro-free"
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_CHUNK_LENGTH = 300
DEFAULT_LATENCY = "normal"


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
# TTS-Live 串流器主體
# ─────────────────────────────────────────────────────────────

class FishTTSLiveStreamer:
    """Fish Audio WebSocket TTS-Live 串流客戶端 + PCM 邊收邊播 + Barge-in。

    - socket_factory:  可注入的 WS factory（無參數呼叫回傳具 send(bytes) / recv() / close() 的物件；
                       預設以 websocket-client 建立 wss 連線）
    - audio_player:    可注入的播放器（具 open() / write(bytes) / stop() / close() 介面）
    - interrupt_event: 可注入的 threading.Event（預設自建；外部攔截 / 測試用）

    API 表面（與既有 FishTTSStreamer 相容，akane_live 呼叫點不變）：
    - speak(clause_texts: list[str]): 開 session → 逐句 text → flush → stop → 等 finish（同步；邊收邊播）
    - interrupt(): Barge-in 入口 — 置位 interrupt_event、立即關閉 WS、停止播放、清空佇列、狀態重置
    - close() / stop(): 完全關閉（WS + 播放器 + 狀態）
    - start(): 釋放 interrupt 旗標，允許新 session（akane_live.run 呼叫點）
    - is_playing / queue_size / pending_chunks(): 與 REST streamer 同語意的唯讀查詢
    """

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "",
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_LIVE_ENDPOINT,
        live_format: str = "pcm",
        socket_factory: Optional[Callable[[], object]] = None,
        audio_player: Optional[object] = None,
        interrupt_event: Optional[threading.Event] = None,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model or DEFAULT_MODEL
        self.endpoint = endpoint
        self.live_format = live_format or "pcm"

        self._socket_factory = socket_factory
        self._audio = audio_player or PCMAudioSink()
        self.interrupt_event = interrupt_event or threading.Event()

        self._current_ws = None
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
            live_format=fa.get("live_format", "pcm"),
        )

    # ── 連線 ──

    def _default_socket_factory(self):
        """websocket-client 建立 wss 連線（headers 帶 Bearer 與 model）。"""
        from websocket import create_connection  # 懶載入

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "model": self.model,
        }
        return create_connection(self.endpoint, header=headers, timeout=10)

    def _connect(self):
        factory = self._socket_factory or self._default_socket_factory
        ws = factory()
        if ws is None:
            raise FishTTSError("socket factory 回傳 None，無法建立 TTS-Live session")
        return ws

    def _send(self, ws, msg: dict) -> None:
        import msgpack  # 懶載入

        ws.send(msgpack.packb(msg))

    def _close_socket(self, ws) -> None:
        if ws is None:
            return
        if getattr(ws, "closed", False):
            return
        try:
            ws.close()
        except Exception:
            pass

    # ── 對外 API ──

    def speak(self, clause_texts: List[str]) -> None:
        """開 session → 逐句 text → flush → stop → 等 finish（同步，邊收邊播）。

        interrupt / close 後為 no-op；WS 例外優雅關閉並記錄 last_error，不 crash 主迴圈。
        """
        if self._interrupted or self._closed:
            return
        if self._current_ws is not None:
            return  # 已有 session 進行中，避免重入

        ws = None
        self._chunks.clear()
        try:
            ws = self._connect()
            self._current_ws = ws
            self._send(ws, {"event": "start", "request": self._start_request()})
            for clause in clause_texts:
                self._send(ws, {"event": "text", "text": clause})
            self._send(ws, {"event": "flush"})
            self._send(ws, {"event": "stop"})
            self._recv_until_finish(ws)
        except Exception as exc:  # 網路/協定例外 → 記錄，不 crash
            self.last_error = exc
        finally:
            self._close_socket(ws)
            self._current_ws = None
            self._chunks.clear()

    def interrupt(self) -> None:
        """Barge-in 入口（監聽器偵測到 Bryan 重新說話時呼叫）：
        1. 置位 interrupt_event（同執行緒的 speak recv 循環立即退出）
        2. 立即關閉目前 WS 連線（中止 server 合成）
        3. 停止當前音訊輸出
        4. 清空音訊佇列、播放狀態重置；interrupt 後 speak() 為 no-op
        """
        self.interrupt_event.set()
        self._interrupted = True
        ws = self._current_ws
        self._current_ws = None
        self._close_socket(ws)
        try:
            self._audio.stop()
        except Exception:
            pass
        self._chunks.clear()
        self._set_playing(False)

    def start(self) -> None:
        """啟動（與 REST streamer 相容）：釋放 interrupt 旗標，允許新 session。"""
        self._interrupted = False
        self.interrupt_event.clear()
        self.last_error = None

    def stop(self) -> None:
        """完全關閉：關 WS + 停止/關閉播放器 + 清佇列 + 狀態重置。"""
        self._closed = True
        self.interrupt_event.set()
        self._interrupted = True
        ws = self._current_ws
        self._current_ws = None
        self._close_socket(ws)
        try:
            self._audio.close()
        except Exception:
            pass
        self._chunks.clear()
        self._set_playing(False)

    close = stop  # 語意別名

    # ── 協定 ──

    def _start_request(self) -> dict:
        return {
            "text": "",
            "format": self.live_format,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "chunk_length": DEFAULT_CHUNK_LENGTH,
            "reference_id": self.voice_id,
            "latency": DEFAULT_LATENCY,
        }

    def _recv_until_finish(self, ws) -> None:
        """接收循環：audio → 立即播放；finish → session 結束；interrupt/例外 → 優雅退出。"""
        import msgpack  # 懶載入

        while not self.interrupt_event.is_set() and not self._closed:
            try:
                data = ws.recv()
            except Exception:
                return  # 連線已關閉（如 interrupt 觸發的 close）→ 優雅結束
            if data is None:
                return
            try:
                msg = msgpack.unpackb(data)
            except Exception:
                continue  # 無法解析的分片跳過，不癱瘓 session
            if not isinstance(msg, dict):
                continue
            event = msg.get("event")
            if event == "audio":
                audio = msg.get("audio")
                if audio:
                    self._play(audio)
            elif event == "finish":
                if msg.get("reason") == "error":
                    self.last_error = FishTTSError(f"TTS-Live finish reason=error: {msg}")
                    try:
                        self._audio.stop()
                    except Exception:
                        pass
                return  # session 結束，連線隨後由 speak 關閉

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