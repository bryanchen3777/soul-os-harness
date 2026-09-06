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
import sys
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
    stop() 為 interrupt 語意：清空待送出佇列（瀏覽器另收 state=IDLE 通知自行靜音）。
    """

    def __init__(self, loop, ws):
        self._loop = loop
        self._ws = ws
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._closed = False

    def start(self) -> None:
        if self._task is None and not self._closed:
            self._task = self._loop.create_task(self._sender())

    async def _sender(self) -> None:
        while True:
            chunk = await self._queue.get()
            try:
                await self._ws.send_bytes(chunk)
            except Exception:
                break

    # ── PCMAudioSink 相容介面（由 streamer 工作執行緒呼叫）──

    def open(self) -> None:
        self.start()

    def write(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, chunk)
        except RuntimeError:
            pass  # loop 關閉中

    def stop(self) -> None:
        """interrupt 語意：清空待送出佇列。"""
        if self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            pass

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Exception:
                break

    def close(self) -> None:
        self._closed = True
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
        """PTT 放開 / Auto-VAD 靜音：強制斷句，進入思考→回覆管線。"""
        print("[WS] ptt_stop")  # VC-1.5 診斷日誌
        if self.state != self.STATE_LISTENING:
            return
        await self._handle_utterance()

    async def on_pcm(self, chunk: bytes) -> None:
        """瀏覽器收音分片（Int16 PCM 16k mono）：餵能量 VAD；0.8s 靜音 → 斷句。"""
        if self.state != self.STATE_LISTENING:
            return
        samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
        events = self._vad.feed(samples.tolist())
        if self._vad.in_speech:
            self._frames.extend(samples.tolist())
        if "speech_end" in events:
            await self._handle_utterance()

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
        print(f"[WS] text {text[:40]}")  # VC-1.5 診斷日誌
        if self.state in (self.STATE_THINKING, self.STATE_SPEAKING):
            self._barge()
        await self._send_json({"type": "transcript", "role": "user", "text": text})
        await self._run_reply(text)

    # ── 管線 ──

    async def _handle_utterance(self) -> None:
        """斷句 → THINKING → ASR → 淨化 → 回覆管線。"""
        if self.state != self.STATE_LISTENING:
            return
        await self._set_state(self.STATE_THINKING)
        captured = self._frames
        self._frames = []
        self._vad.reset()
        if not captured:
            print("[UTT] start frames=0 (skip)")  # VC-1.5 診斷日誌
            await self._set_state(self.STATE_IDLE)
            return
        print(f"[UTT] start frames={len(captured)}")  # VC-1.5 診斷日誌
        pcm = (np.clip(np.asarray(captured, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        wav = pcm16_to_wav_bytes(pcm, sample_rate=VAD_SAMPLE_RATE)
        text = await asyncio.to_thread(self._asr.transcribe, wav)
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
                await self._send_json({"type": "error", "message": message})
                print(f"[UTT] asr-error status={status}")  # VC-1.5 診斷日誌
            else:
                print("[UTT] asr-empty (drop)")  # VC-1.5 診斷日誌
            await self._set_state(self.STATE_IDLE)
            return
        print(f"[UTT] asr-ok text={text[:40]}")  # VC-1.5 診斷日誌
        await self._send_json({"type": "transcript", "role": "user", "text": text})
        clean = await asyncio.to_thread(self._refiner.refine_speech_text, text)
        if not clean:
            # 雜音熔斷（DROP）：不打擾茜
            print("[UTT] refine-drop")  # VC-1.5 診斷日誌
            await self._set_state(self.STATE_IDLE)
            return
        await self._run_reply(clean)

    async def _run_reply(self, user_text: str) -> None:
        """THINKING 完成 → SPEAKING：LLM 串流 token → feed_text_piece → end_session。

        收尾以 create_task 並行執行（不阻塞 WS handler），interrupt 訊息可立即打斷；
        世代號確保被打斷的舊回合收尾不會覆蓋新回合狀態。
        """
        await self._set_state(self.STATE_SPEAKING)
        self._generation += 1
        gen = self._generation

        def _generate() -> str:
            # 同步阻塞段（LLM requests + TTS WS）在 dedicated thread 執行，不卡 asyncio 迴圈
            self._streamer.start()  # 釋放 interrupt 旗標（若有）
            parts: list[str] = []
            for token in self._brain.stream_respond(user_text):
                parts.append(token)
                self._streamer.feed_text_piece(token)
            self._streamer.end_session()
            return "".join(parts)

        async def _finish(gen: int) -> None:
            try:
                reply = await asyncio.to_thread(_generate)
            except Exception as exc:
                await self._send_json({"type": "error", "message": f"上游失敗: {exc}"})
                print(f"[UTT] reply-error {exc}")  # VC-1.5 診斷日誌
            else:
                reply = (reply or "").strip()
                if reply:
                    await self._send_json({"type": "transcript", "role": "akane", "text": reply})
                print(f"[UTT] reply chars={len(reply)}")  # VC-1.5 診斷日誌
            finally:
                if gen == self._generation:  # 仍是本世代 → 正常結束；被打斷則已由 interrupt 收尾
                    await self._set_state(self.STATE_IDLE)

        asyncio.create_task(_finish(gen))

    async def close(self) -> None:
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


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app = request.app
    cfg = app[VC_CONFIG_KEY]
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
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        await session.close()
        print(f"[WS] close peer={peer}")  # VC-1.5 診斷日誌
    return ws


def default_streamer_factory(config: dict) -> Callable[[AudioRelaySink], FishTTSLiveStreamer]:
    """生產 streamer factory：FishTTSLiveStreamer 以 AudioRelaySink 為 audio_player。"""
    fa = (config or {}).get("fish_audio") or {}

    def factory(sink: AudioRelaySink) -> FishTTSLiveStreamer:
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
    cfg = config or load_config()
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
    web_cfg = cfg.get("web") or {}
    host = web_cfg.get("host", "0.0.0.0")
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