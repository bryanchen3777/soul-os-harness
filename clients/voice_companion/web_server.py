"""
web_server.py — 黑川茜 Web 語音伴侶伺服器（VC-1.3）。

把 VC-1 擴充為「瀏覽器 Web 介面」：麥克風收音與揚聲器放音都在瀏覽器端
（getUserMedia / Web Audio），伺服器只跑 VAD→ASR→茜大腦→TTS-Live→音訊中繼。
區網內任何瀏覽器開網址即用。終端版 akane_live.py 保持不變。

拓撲：
    瀏覽器(收音16k PCM→WS) ⇄ aiohttp WebSocket ⇄ Python 管線(VAD→FishASR→淨化→茜LLM串流→FishTTS-Live)
                              ⇄ 44.1k PCM音訊分片 → 瀏覽器 WebAudio 播放；瀏覽器偵測到你開口 → WS interrupt → 即時打斷

WS 協定：
- client→server：binary = Int16 PCM 16kHz mono 分片；JSON {"type":"ptt_start"|"ptt_stop"}；
  JSON {"type":"interrupt"}；JSON {"type":"text","text":...}（打字 fallback）
- server→client：JSON {"type":"state","state":IDLE|LISTENING|THINKING|SPEAKING}；
  JSON {"type":"transcript","role":"user"|"akane","text":...}；JSON {"type":"error","message":...}；
  binary = Int16 PCM 44.1kHz mono 播放分片

管線重用既有模組（0 改動）：vad_listener 能量 VAD → stt_service.FishASRService →
asr_refiner → akane_voice_brain（LLM 串流）→ fish_tts_live.FishTTSLiveStreamer
（audio_player 注入 AudioRelaySink，取代 sounddevice sink）→ AudioRelaySink 轉送瀏覽器。

入口：python -m clients.voice_companion.web_server [--port N]（啟動列印區網可用網址）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
from aiohttp import WSMsgType, web

try:  # pragma: no cover - 導入路徑相容
    from .akane_voice_brain import AkaneVoiceBrain
    from .asr_refiner import AsrRefiner
    from .env_config import resolve_config
    from .fish_tts_live import DEFAULT_LIVE_ENDPOINT, FishTTSLiveStreamer
    from .stt_service import FishASRService, pcm16_to_wav_bytes
    from .vad_listener import VoiceActivityDetector
    from .web_ui import HTML_PAGE
except ImportError:  # pragma: no cover
    from akane_voice_brain import AkaneVoiceBrain
    from asr_refiner import AsrRefiner
    from env_config import resolve_config
    from fish_tts_live import DEFAULT_LIVE_ENDPOINT, FishTTSLiveStreamer
    from stt_service import FishASRService, pcm16_to_wav_bytes
    from vad_listener import VoiceActivityDetector
    from web_ui import HTML_PAGE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

VAD_SAMPLE_RATE = 16000   # 瀏覽器收音分片率（Int16 PCM mono）
OUT_SAMPLE_RATE = 44100   # 播放分片率（Int16 PCM mono）

# VC-1.5：HTTPS 模式（自簽憑證目錄；certs/ 已 gitignore）
CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"
HTTPS_SELF_SIGNED_HINT = "⚠️ 自簽憑證：瀏覽器會顯示「連線不是私人連線」— 選「繼續前往」即可，之後麥克風可用。"

# aiohttp AppKey（應用級相依容器，避免 str key warning）
VC_CONFIG_KEY = web.AppKey("vc_config", dict)
VC_BRAIN_KEY = web.AppKey("vc_brain", object)
VC_REFINER_KEY = web.AppKey("vc_refiner", object)
VC_ASR_KEY = web.AppKey("vc_asr", object)
VC_STREAMER_FACTORY_KEY = web.AppKey("vc_streamer_factory", object)


def load_config(path: Optional[str] = None) -> dict:
    """載入客戶端 config.json（預設為模組旁 config.json）。"""
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def lan_ips() -> List[str]:
    """偵測區網 IPv4 清單（不含 127.*）。"""
    try:
        import socket

        addrs = socket.gethostbyname_ex(socket.gethostname())[2]
    except Exception:  # pragma: no cover - 區網偵測失敗不致命
        addrs = []
    return [a for a in addrs if a and not a.startswith("127.")]


def lan_urls(port: int, scheme: str = "http") -> List[str]:
    """列印用網址清單：本機 + 區網 IP（scheme: http / https）。"""
    urls = [f"{scheme}://127.0.0.1:{port}"]
    urls += [f"{scheme}://{a}:{port}" for a in lan_ips()]
    return urls


# ─────────────────────────────────────────────────────────────
# HTTPS 模式（VC-1.5）：自簽憑證產生 + SSLContext
# ─────────────────────────────────────────────────────────────

def make_self_signed_cert(cert_path: Path, key_path: Path, names: Optional[List[str]] = None) -> None:
    """用 cryptography 產生自簽憑證（CN=主機名/IP，SAN 含 127.0.0.1 與偵測到的區網 IP）。

    產出 PEM 格式 cert.pem + key.pem（無密碼）。測試以 tmp_path 驗證可被 ssl 讀取。
    """
    import datetime
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    names = names or (["127.0.0.1"] + lan_ips())
    if not names:
        names = ["127.0.0.1"]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    san: List[x509.GeneralName] = []
    for n in names:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            san.append(x509.DNSName(n))
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, names[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def build_ssl_context(cert_path: Path = CERT_PATH, key_path: Path = KEY_PATH):
    """包裝 cert/key PEM → ssl.SSLContext（PROTOCOL_TLS_SERVER）。"""
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    return ctx


# ─────────────────────────────────────────────────────────────
# AudioRelaySink：TTS-Live PCM 分片 → 瀏覽器 WS binary（取代 sounddevice sink）
# ─────────────────────────────────────────────────────────────

class AudioRelaySink:
    """把 FishTTSLiveStreamer 產出的 PCM 分片轉送給 WS 客戶端（thread-safe）。

    介面對齊 fish_tts_live.PCMAudioSink（open/write/stop/close）：FishTTSLiveStreamer
    的工作執行緒呼叫 write；以 loop.call_soon_threadsafe 放進 asyncio 佇列，
    sender task（asyncio 迴圈內）依序 await ws.send_bytes() 送出。
    背壓防禦（VC-2.3-03）：佇列設 maxsize（預設 256 分片，約 25s），溢位丟棄最舊分片防 OOM。
    排空等待（VC-2.3-03）：drain() 異步等待所有排入分片完全發送至 WS，徹底解決尾字截斷。
    stop() 為 interrupt 語意：清空待送出佇列（瀏覽器另收 state=IDLE 通知自行靜音）。
    """

    DEFAULT_MAXSIZE = 256

    def __init__(self, loop, ws, maxsize: int = DEFAULT_MAXSIZE):
        self._loop = loop
        self._ws = ws
        self._maxsize = maxsize
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._sending = False
        self._written_chunks = 0
        self._finished_chunks = 0
        self.first_chunk_time: Optional[float] = None
        self._drained_event: asyncio.Event = asyncio.Event()
        self._drained_event.set()

    def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = self._loop.create_task(self._sender())

    async def _sender(self) -> None:
        while True:
            try:
                chunk = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                self._sending = True
                await self._ws.send_bytes(chunk)
            except Exception:
                break
            finally:
                self._sending = False
                self._finished_chunks += 1
                if self._finished_chunks >= self._written_chunks and self._queue.empty():
                    self._drained_event.set()

    # ── PCMAudioSink 相容介面（由 streamer 工作執行緒呼叫）──

    def open(self) -> None:
        self.start()

    def write(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        if self.first_chunk_time is None:
            self.first_chunk_time = time.perf_counter()
        self._written_chunks += 1
        self._drained_event.clear()
        try:
            self._loop.call_soon_threadsafe(self._enqueue, chunk)
        except RuntimeError:
            pass  # loop 關閉中

    def _enqueue(self, chunk: bytes) -> None:
        if self._closed:
            return
        if self._queue.full():
            try:
                self._queue.get_nowait()
                self._finished_chunks += 1
                print("[RELAY] queue overflow, dropped oldest frame")
            except Exception:
                pass
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            self._finished_chunks += 1

    async def drain(self, timeout: float = 10.0) -> bool:
        """等待所有已排入的 PCM 分片完全由 WebSocket 送出（解決尾字截斷 VC-2.3-03）。"""
        if self._closed or (self._finished_chunks >= self._written_chunks and self._queue.empty() and not self._sending):
            return True
        try:
            await asyncio.wait_for(self._drained_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            print("[RELAY] drain timeout")
            return False

    def stop(self) -> None:
        """interrupt 語意：清空待送出佇列。"""
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._do_stop)
        except RuntimeError:
            pass

    def _do_stop(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._finished_chunks += 1
            except Exception:
                break
        self._finished_chunks = max(self._finished_chunks, self._written_chunks)
        self._drained_event.set()

    def close(self) -> None:
        self._closed = True
        self._finished_chunks = max(self._finished_chunks, self._written_chunks)
        self._drained_event.set()
        task, self._task = self._task, None
        if task is not None:
            try:
                self._loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass


# ─────────────────────────────────────────────────────────────
# WebSession：單一 WS 連線的語音管線（狀態機 + 上游組件）
# ─────────────────────────────────────────────────────────────

class WebSession:
    """單一 WS 連線的語音管線。

    狀態機：IDLE → LISTENING（ptt_start / Auto-VAD）→ THINKING（斷句後思考）
    → SPEAKING（LLM 串流 feed TTS-Live）→ IDLE。
    一次只處理一個 utterance：進行中收到新語音 → 先 interrupt 再開新回合（天然 barge-in）。
    """

    STATE_IDLE = "IDLE"
    STATE_LISTENING = "LISTENING"
    STATE_THINKING = "THINKING"
    STATE_SPEAKING = "SPEAKING"
    MAX_HISTORY_TURNS = 10  # 對話記憶：保留最近 N 輪（每輪 user+assistant 兩條）
    MAX_FRAME_BYTES = 64 * 1024  # 64 KB 邊界防禦（VC-2.3-05）
    MAX_UTTERANCE_SAMPLES = VAD_SAMPLE_RATE * 30  # 30 秒上限（16k mono 480k samples，約 960 KB）

    def __init__(self, ws, *, config, brain, refiner, asr, streamer, detector, sink):
        self._ws = ws
        self._config = config
        self._brain = brain
        self._refiner = refiner
        self._asr = asr
        self._streamer = streamer
        self._vad = detector
        self._sink = sink
        self.state = self.STATE_IDLE
        self._frames: List[float] = []
        self._generation = 0  # 回合世代：打斷後遞增，舊回合收尾不得覆蓋狀態
        self._history: List[dict] = []  # 每連線對話歷史（role user/assistant，供 LLM 承接前文）
        self._utterance_task: Optional[asyncio.Task] = None
        self._reply_task: Optional[asyncio.Task] = None
        self._streamer.start()

    # ── WS 事件入口（全部在 asyncio 迴圈內）──

    async def on_ptt_start(self) -> None:
        """PTT 按下 / Auto-VAD 語音開始：若進行中 → 先 interrupt 再開新回合。"""
        print("[WS] ptt_start")  # VC-1.5 診斷日誌
        if self.state in (self.STATE_THINKING, self.STATE_SPEAKING):
            self._barge()
        if self.state != self.STATE_LISTENING:
            await self._set_state(self.STATE_LISTENING)

    async def on_ptt_stop(self) -> None:
        """PTT 放開 / Auto-VAD 靜音：強制斷句，進入思考→回覆管線。

        以 create_task 非同步執行，確保 WS handler 迴圈不被 ASR/Refiner 阻塞，
        interrupt 與新輸入可即時打斷。
        """
        print("[WS] ptt_stop")  # VC-1.5 診斷日誌
        if self.state != self.STATE_LISTENING:
            return
        if self._utterance_task and not self._utterance_task.done():
            self._utterance_task.cancel()
        self._utterance_task = asyncio.create_task(self._handle_utterance())

    async def on_pcm(self, chunk: bytes) -> None:
        """瀏覽器收音分片（Int16 PCM 16k mono）：餵能量 VAD；0.8s 靜音 → 斷句。"""
        if self.state != self.STATE_LISTENING:
            return
        if len(chunk) > self.MAX_FRAME_BYTES:
            print(f"[WS] pcm frame exceeded {self.MAX_FRAME_BYTES} bytes ({len(chunk)}), dropped")
            return
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        events = self._vad.feed(samples.tolist())
        if self._vad.in_speech:
            self._frames.extend(samples.tolist())
            if len(self._frames) >= self.MAX_UTTERANCE_SAMPLES:
                print(f"[UTT] max utterance limit reached ({len(self._frames)} samples / 30s), forcing speech_end")
                if self._utterance_task and not self._utterance_task.done():
                    self._utterance_task.cancel()
                self._utterance_task = asyncio.create_task(self._handle_utterance())
                return
        if "speech_end" in events:
            if self._utterance_task and not self._utterance_task.done():
                self._utterance_task.cancel()
            self._utterance_task = asyncio.create_task(self._handle_utterance())

    async def on_interrupt(self) -> None:
        """瀏覽器打斷（Bryan 在茜說話時開口）：立即中斷合成/播放，回 IDLE（瀏覽器靜音）。"""
        print("[WS] interrupt")  # VC-1.5 診斷日誌
        self._barge()
        await self._set_state(self.STATE_IDLE)

    async def on_text(self, text: str) -> None:
        """打字 fallback：不走 VAD/ASR，直接進 LLM 串流 → TTS。"""
        text = (text or "").strip()
        if not text:
            return
        if len(text) > self.MAX_FRAME_BYTES:
            print(f"[WS] text exceeded {self.MAX_FRAME_BYTES} chars ({len(text)}), truncated")
            text = text[:self.MAX_FRAME_BYTES]
        print(f"[WS] text {text[:40]}")  # VC-1.5 診斷日誌
        if self.state in (self.STATE_LISTENING, self.STATE_THINKING, self.STATE_SPEAKING):
            self._barge()
        self._generation += 1
        gen = self._generation
        t_start = time.perf_counter()
        await self._send_json({"type": "transcript", "role": "user", "text": text})
        await self._run_reply(text, gen=gen, t_start=t_start, t_asr_done=t_start)

    async def on_ping(self) -> None:
        """心跳保活（VC-2.1）：前端每 25 秒送 ping，回傳 pong 防 NAT/隧道斷線。"""
        await self._send_json({"type": "pong"})

    # ── 管線 ──

    async def _handle_utterance(self) -> None:
        """斷句 → THINKING → ASR → 淨化 → 回覆管線。"""
        if self.state != self.STATE_LISTENING:
            return
        t_start = time.perf_counter()
        t_asr_done = None
        self._generation += 1
        gen = self._generation
        await self._set_state(self.STATE_THINKING)
        captured = self._frames
        self._frames = []
        self._vad.reset()
        if not captured:
            print("[UTT] start frames=0 (skip)")  # VC-1.5 診斷日誌
            if gen == self._generation:
                await self._set_state(self.STATE_IDLE)
            return
        print(f"[UTT] start frames={len(captured)}")  # VC-1.5 診斷日誌
        pcm = (np.clip(np.asarray(captured, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        wav = pcm16_to_wav_bytes(pcm, sample_rate=VAD_SAMPLE_RATE)
        try:
            text = await asyncio.to_thread(self._asr.transcribe, wav)
            t_asr_done = time.perf_counter()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = ""
            print(f"[UTT] asr-exception {exc}")

        if gen != self._generation:
            print(f"[UTT] asr-cancelled gen={gen} curr={self._generation}")
            return
        text = (text or "").strip()
        if not text:
            # VC-1.4 失敗透通：ASR 有錯誤（402 額度 / 網路例外）→ 顯示給使用者，不再靜默 DROP；
            # last_error 為 None（真雜音/靜音）才維持既有 DROP 靜默
            err = getattr(self._asr, "last_error", None)
            if err:
                status = int(err.get("status", 0) or 0)
                body = (err.get("message") or "")[:200]
                message = f"語音辨識失敗（HTTP {status}）：{body}"
                if status == 402:
                    message += "（請檢查 Fish API 額度）"
                if gen == self._generation:
                    await self._send_json({"type": "error", "message": message})
                print(f"[UTT] asr-error status={status}")  # VC-1.5 診斷日誌
            else:
                print("[UTT] asr-empty (drop)")  # VC-1.5 診斷日誌
            if gen == self._generation:
                await self._set_state(self.STATE_IDLE)
            return
        if gen != self._generation:
            return
        print(f"[UTT] asr-ok text={text[:40]}")  # VC-1.5 診斷日誌
        await self._send_json({"type": "transcript", "role": "user", "text": text})

        try:
            clean = await asyncio.to_thread(self._refiner.refine_speech_text, text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            clean = ""
            print(f"[UTT] refiner-exception {exc}")

        if gen != self._generation:
            print(f"[UTT] refiner-cancelled gen={gen} curr={self._generation}")
            return
        if not clean:
            # 雜音熔斷（DROP）：不打擾茜
            print("[UTT] refine-drop")  # VC-1.5 診斷日誌
            if gen == self._generation:
                await self._set_state(self.STATE_IDLE)
            return
        if gen != self._generation:
            return
        await self._run_reply(clean, gen=gen, t_start=t_start, t_asr_done=t_asr_done)

    async def _run_reply(
        self,
        user_text: str,
        gen: Optional[int] = None,
        t_start: Optional[float] = None,
        t_asr_done: Optional[float] = None,
    ) -> None:
        """THINKING 完成 → SPEAKING：LLM 串流 token → feed_text_piece → end_session。

        收尾以 create_task 並行執行（不阻塞 WS handler），interrupt 訊息可立即打斷；
        世代號確保被打斷的舊回合收尾不會覆蓋新回合狀態。
        """
        if gen is None:
            self._generation += 1
            gen = self._generation
        elif gen != self._generation:
            return

        if t_start is None:
            t_start = time.perf_counter()
        if t_asr_done is None:
            t_asr_done = t_start

        if hasattr(self._sink, "first_chunk_time"):
            self._sink.first_chunk_time = None
        await self._set_state(self.STATE_SPEAKING)

        def _generate() -> str:
            # 同步阻塞段（LLM requests + TTS WS）在 dedicated thread 執行，不卡 asyncio 迴圈
            self._streamer.start()  # 釋放 interrupt 旗標（若有）
            hist = list(self._history)  # 對話快照：本回合結束前由 _finish 更新
            parts: list[str] = []
            for token in self._brain.stream_respond(user_text, history=hist):
                parts.append(token)
                self._streamer.feed_text_piece(token)
            self._streamer.end_session()
            return "".join(parts)

        async def _finish(task_gen: int) -> None:
            try:
                reply = await asyncio.to_thread(_generate)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if task_gen == self._generation:
                    await self._send_json({"type": "error", "message": f"上游失敗: {exc}"})
                    print(f"[UTT] reply-error {exc}")  # VC-1.5 診斷日誌
            else:
                if task_gen != self._generation:
                    return
                # VC-2.3-03: 排空音訊分片，確保 WebSocket 底層完全送出，徹底消除尾字截斷
                if hasattr(self._sink, "drain"):
                    await self._sink.drain()
                if task_gen != self._generation:
                    return
                reply = (reply or "").strip()
                if reply:
                    await self._send_json({"type": "transcript", "role": "akane", "text": reply})
                    if task_gen == self._generation:  # 本世代正常結束 → 寫入對話記憶（被 barge-in 打斷的半截回覆不入記憶）
                        self._history.append({"role": "user", "content": user_text})
                        self._history.append({"role": "assistant", "content": reply})
                        cap = self.MAX_HISTORY_TURNS * 2
                        if len(self._history) > cap:
                            self._history = self._history[-cap:]
                t_done = time.perf_counter()
                total_ms = (t_done - t_start) * 1000.0
                asr_ms = ((t_asr_done - t_start) * 1000.0) if t_asr_done is not None else 0.0
                first_audio_ms = (
                    ((self._sink.first_chunk_time - t_start) * 1000.0)
                    if getattr(self._sink, "first_chunk_time", None) is not None
                    else total_ms
                )
                print(f"[UTT] reply chars={len(reply)}")  # VC-1.5 診斷日誌
                print(
                    f"[LATENCY] asr={asr_ms:.1f}ms first_audio={first_audio_ms:.1f}ms "
                    f"total={total_ms:.1f}ms chars={len(reply)}"
                )  # VC-2.3-05 延遲可觀測性
            finally:
                if task_gen == self._generation:  # 仍是本世代 → 正常結束；被打斷則已由 interrupt 收尾
                    await self._set_state(self.STATE_IDLE)

        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
        self._reply_task = asyncio.create_task(_finish(gen))

    async def close(self) -> None:
        self._barge()
        if self._utterance_task and not self._utterance_task.done():
            self._utterance_task.cancel()
            try:
                await self._utterance_task
            except (asyncio.CancelledError, Exception):
                pass
            self._utterance_task = None
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            try:
                await self._reply_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reply_task = None
        try:
            self._streamer.close()
        except Exception:
            pass
        self._sink.close()

    # ── 內部 ──

    def _barge(self) -> None:
        """打斷當前回合（新語音 / 新文字 / 顯式 interrupt 共用）。"""
        self._streamer.interrupt()
        self._frames = []
        self._vad.reset()
        self._generation += 1  # 舊回合收尾失去狀態控制權
        if self._utterance_task and not self._utterance_task.done():
            self._utterance_task.cancel()
            self._utterance_task = None
        if self._reply_task and not self._reply_task.done():
            self._reply_task.cancel()
            self._reply_task = None

    async def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        await self._send_json({"type": "state", "state": state})

    async def _send_json(self, obj: dict) -> None:
        try:
            await self._ws.send_json(obj)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# aiohttp 應用
# ─────────────────────────────────────────────────────────────

async def index_handler(request: web.Request) -> web.Response:
    return web.Response(text=HTML_PAGE, content_type="text/html", charset="utf-8")


def is_loopback_host(host: str) -> bool:
    """判斷 host 是否為本機迴路位址（loopback）。"""
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1")


def validate_host_and_token(host: str, token: Optional[str] = None) -> None:
    """VC-2.3-05：非 loopback 主機（如 0.0.0.0）綁定時必須配置 VOICE_WEB_TOKEN。"""
    if not is_loopback_host(host):
        if not token or not str(token).strip():
            raise RuntimeError(
                f"Binding to non-loopback host '{host}' requires VOICE_WEB_TOKEN to be set."
            )


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    app = request.app
    cfg = app[VC_CONFIG_KEY]
    web_cfg = cfg.get("web") or {}

    # VC-2.3-05: 檢查 Allowed Origins（若有配置）
    allowed_origins_raw = web_cfg.get("allowed_origins") or os.environ.get("VOICE_WEB_ALLOWED_ORIGINS") or ""
    if allowed_origins_raw:
        allowed = [o.strip() for o in allowed_origins_raw.split(",") if o.strip()]
        origin = request.headers.get("Origin")
        if not origin or origin not in allowed:
            return web.Response(status=403, text="Forbidden: Origin not allowed")

    # VC-2.3-05: 檢查 Token 鑑權（若有配置）
    configured_token = web_cfg.get("token") or os.environ.get("VOICE_WEB_TOKEN") or ""
    if configured_token:
        client_token = request.query.get("token", "")
        if client_token != configured_token:
            return web.Response(status=401, text="Unauthorized: Invalid token")

    ws = web.WebSocketResponse(max_msg_size=WebSession.MAX_FRAME_BYTES)
    await ws.prepare(request)
    peer = request.remote or "?"
    print(f"[WS] connect peer={peer}")  # VC-1.5 診斷日誌
    sink = AudioRelaySink(request.loop, ws)
    sink.start()
    streamer = app[VC_STREAMER_FACTORY_KEY](sink)
    vad_cfg = (cfg.get("vad") or {})
    detector = VoiceActivityDetector(
        sample_rate=int(vad_cfg.get("sample_rate", VAD_SAMPLE_RATE)),
        silence_threshold_sec=float(vad_cfg.get("silence_threshold_sec", 0.8)),
        energy_threshold=float(vad_cfg.get("energy_threshold", 0.015)),
    )
    session = WebSession(
        ws,
        config=cfg,
        brain=app[VC_BRAIN_KEY],
        refiner=app[VC_REFINER_KEY],
        asr=app[VC_ASR_KEY],
        streamer=streamer,
        detector=detector,
        sink=sink,
    )
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await session.on_pcm(msg.data)
            elif msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except (ValueError, TypeError):
                    continue
                mtype = data.get("type")
                if mtype == "ptt_start":
                    await session.on_ptt_start()
                elif mtype == "ptt_stop":
                    await session.on_ptt_stop()
                elif mtype == "interrupt":
                    await session.on_interrupt()
                elif mtype == "text":
                    await session.on_text(data.get("text", ""))
                elif mtype == "ping":
                    await session.on_ping()
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        await session.close()
        print(f"[WS] close peer={peer}")  # VC-1.5 診斷日誌
    return ws


class RestPCMWebStreamer:
    """REST TTS 實驗音源（--tts rest）：收完整句 → Fish REST mp3 → miniaudio 解 44100 PCM → relay sink。

    實作與 FishTTSLiveStreamer 相同的 feed API（start/feed_text_piece/end_session/interrupt/close
    /is_playing/queue_size/pending_chunks/last_error），WebSession 免改動。
    用途：隔離「live 合成音源」vs「瀏覽器播放層」——若 REST 音源經同一播放管線聽起來正常，
    即證實問題在 live 音源；反之則在播放層。
    """

    def __init__(self, api_key: str = "", voice_id: str = "", model: str = "s2.1-pro-free", audio_player: Optional[object] = None):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model or "s2.1-pro-free"
        self._audio = audio_player
        self._pieces: List[str] = []
        self._worker: Optional[threading.Thread] = None
        self._interrupted = False
        self._closed = False
        self.last_error: Optional[Exception] = None
        self._chunks: List[bytes] = []

    def _synth_pcm(self, text: str) -> bytes:
        import array
        import miniaudio  # 懶載入
        from clients.voice_companion.fish_tts_streamer import FishTTSStreamer

        st = FishTTSStreamer(api_key=self.api_key, voice_id=self.voice_id, model=self.model)
        mp3 = st.synthesize(text)  # REST mp3 bytes（既有、實測 200）
        sf = miniaudio.decode(
            mp3, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=44100
        )
        # DecodedSoundFile.samples = int 串列（SIGNED16 樣本值）→ 打包成 PCM16LE bytes
        return array.array("h", sf.samples).tobytes()

    def _run(self) -> None:
        text = "".join(self._pieces)
        if not text or self._interrupted or self._closed:
            return
        try:
            pcm = self._synth_pcm(text)
            if pcm and not (self._interrupted or self._closed):
                self._chunks.append(pcm)
                if self._audio is not None:
                    self._audio.write(pcm)
        except Exception as exc:
            if not (self._interrupted or self._closed):
                self.last_error = exc

    def start(self) -> None:
        self._interrupted = False
        self._closed = False
        self.last_error = None
        self._pieces = []
        self._chunks = []

    def feed_text_piece(self, piece: str) -> None:
        if self._interrupted or self._closed:
            return
        self._pieces.append(piece)

    def end_session(self) -> None:
        if self._interrupted or self._closed or self._worker is not None:
            return
        import threading  # 懶載入

        worker = threading.Thread(target=self._run, daemon=True, name="rest-pcm-web-tts")
        self._worker = worker
        worker.start()
        worker.join(timeout=90)  # REST 合成為整句一次性，等待完成
        self._worker = None
        self._pieces = []

    def interrupt(self) -> None:
        self._interrupted = True
        try:
            if self._audio is not None:
                self._audio.stop()
        except Exception:
            pass
        self._chunks = []

    def stop(self) -> None:
        self._closed = True
        self._interrupted = True
        try:
            if self._audio is not None:
                self._audio.close()
        except Exception:
            pass
        self._chunks = []

    close = stop  # 語意別名

    @property
    def is_playing(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def queue_size(self) -> int:
        return len(self._chunks)

    def pending_chunks(self) -> List[bytes]:
        return list(self._chunks)


def default_streamer_factory(config: dict) -> Callable[[AudioRelaySink], FishTTSLiveStreamer]:
    """生產 streamer factory：依 fish_audio.mode 選音源（live=官方 SDK；rest=REST mp3 解碼實驗）。"""
    fa = (config or {}).get("fish_audio") or {}

    def factory(sink: AudioRelaySink) -> FishTTSLiveStreamer:
        if (fa.get("mode") or "live") == "rest":
            return RestPCMWebStreamer(  # type: ignore[return-value]
                api_key=fa.get("api_key", ""),
                voice_id=fa.get("voice_id", ""),
                model=fa.get("model", "s2.1-pro-free"),
                audio_player=sink,
            )
        return FishTTSLiveStreamer(
            api_key=fa.get("api_key", ""),
            voice_id=fa.get("voice_id", ""),
            model=fa.get("model", "s2.1-pro-free"),
            endpoint=fa.get("tts_ws_endpoint", DEFAULT_LIVE_ENDPOINT),
            live_format=fa.get("live_format", "pcm"),
            audio_player=sink,
        )

    return factory


def build_app(
    config: Optional[dict] = None,
    *,
    brain=None,
    refiner=None,
    asr=None,
    streamer_factory: Optional[Callable] = None,
) -> web.Application:
    """建立 aiohttp 應用。組件全可注入（測試用 fake，0 網路）。

    未注入時依 config 建立生產組件：AkaneVoiceBrain（llm_stream 走 config llm 端點）、
    AsrRefiner.from_config、FishASRService.from_config、FishTTSLiveStreamer（relay sink）。
    """
    import copy
    cfg = copy.deepcopy(config) if config is not None else load_config()
    if brain is None:
        brain = AkaneVoiceBrain(
            config=cfg,
            persona_file=str(REPO_ROOT / "personas" / "agent_akane.md"),
        )
    if refiner is None:
        refiner = AsrRefiner.from_config(cfg)
    if asr is None:
        asr = FishASRService.from_config(cfg)
    if streamer_factory is None:
        streamer_factory = default_streamer_factory(cfg)

    app = web.Application()
    app[VC_CONFIG_KEY] = cfg
    app[VC_BRAIN_KEY] = brain
    app[VC_REFINER_KEY] = refiner
    app[VC_ASR_KEY] = asr
    app[VC_STREAMER_FACTORY_KEY] = streamer_factory
    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", websocket_handler)
    return app


# ─────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────

def main(argv: Optional[list] = None) -> int:
    """python -m clients.voice_companion.web_server [--port N] [--https]

    啟動時套用 env_config.resolve_config（.env + 環境變數覆寫，api_key 只來自環境）。
    --https（或 config web.https=true）：HTTPS 模式 — 檢查/產生自簽憑證
    （clients/voice_companion/certs/，已 gitignore）並以 https:// 提供服務。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    # Windows 主控台（cp950/Big5）列印 emoji/中文會 UnicodeEncodeError → 強制 UTF-8 + 容錯編碼
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    port = None
    if "--port" in args:
        i = args.index("--port")
        if i + 1 < len(args):
            port = int(args[i + 1])
    cfg = load_config()
    cfg = resolve_config(cfg)
    if "--tts" in args:  # VC-1.6 音源實驗切換：--tts rest（REST mp3 解碼）／預設 live（官方 SDK）
        i = args.index("--tts")
        if i + 1 < len(args):
            (cfg.setdefault("fish_audio", {})).setdefault("mode", "live")
            cfg["fish_audio"]["mode"] = args[i + 1]
    web_cfg = cfg.get("web") or {}
    host = web_cfg.get("host", "127.0.0.1")
    if "--host" in args:
        i = args.index("--host")
        if i + 1 < len(args):
            host = args[i + 1]
    token = web_cfg.get("token") or os.environ.get("VOICE_WEB_TOKEN")
    validate_host_and_token(host, token)
    port = port or int(web_cfg.get("port", 8765))
    https = "--https" in args or bool(web_cfg.get("https", False))
    ssl_ctx = None
    if https:
        if not (CERT_PATH.is_file() and KEY_PATH.is_file()):
            print("[TLS] 產生自簽憑證 →", CERT_DIR)
            make_self_signed_cert(CERT_PATH, KEY_PATH)
        ssl_ctx = build_ssl_context(CERT_PATH, KEY_PATH)
    scheme = "https" if https else "http"
    print("黑川茜 Web 語音伴侶已啟動（VC-1.5）— Ctrl-C 結束")
    if https:
        print(HTTPS_SELF_SIGNED_HINT)
    for url in lan_urls(port, scheme=scheme):
        print(f"  {url}")
    app = build_app(cfg)
    web.run_app(app, host=host, port=port, ssl_context=ssl_ctx)
    return 0


if __name__ == "__main__":
    sys.exit(main())