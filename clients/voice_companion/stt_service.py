"""
stt_service.py — Fish Audio 官方 ASR 服務（VC-1.1 模組 1，輸入側）。

- POST https://api.fish.audio/v1/asr（multipart），Headers: Authorization: Bearer <api_key>
- multipart：files={"audio": ("speech.wav", wav_bytes, "audio/wav")}、data={"language": "zh"}、timeout=10.0
- 回傳 200 → resp.json().get("text","").strip()；空輸入 / 非 200 / 網路異常 / 解析失敗 → ""（0 崩潰）

🔺 模型選擇走 **HTTP header `model`**（2026-10-24 實測釘死）：
  OpenAPI 的 `/v1/asr` 只把 `model` 定義為 header 參數（enum: transcribe-1 / transcribe-1-pro），
  request body **無 model 欄位**（body 放會被忽略）；header 缺或拼錯**不報錯**，
  會被當成 `transcribe-1` 計費（實測：未帶 header 時回應無 `<|speaker:N|>` 標記）。
  `transcribe-1-pro` 會在 text 前綴 `<|speaker:0|>` 這類說話者標記，
  且預設保留 `[laughter]` / `[高興]` 這類情緒標記 ⇒ 本模組兩者都處理掉，
  讓 VC 的轉錄文字維持「純文字」契約（與舊的 transcribe-1 一致）。

離線可測：session（具 post(url, files=, data=, headers=, timeout=) 介面）可注入；requests 懶載入。
"""

from __future__ import annotations

import io
import re
import wave
from typing import Optional

DEFAULT_ASR_ENDPOINT = "https://api.fish.audio/v1/asr"

# 預設 ASR 模型：Fish 官方推薦（長錄音、多說話者、保留情緒標記）。
# 延遲實測（2.4 秒中文音訊）：transcribe-1-pro 0.28s vs transcribe-1 0.27s ⇒ 無額外延遲。
DEFAULT_ASR_MODEL = "transcribe-1-pro"

# transcribe-1-pro 專屬欄位（transcribe-1 收到這些欄位會 400 invalid_parameter，
# 故只在 pro 模式送出）。tag_audio_events=false ⇒ 移除 `[laughter]` / `[高興]` 等情緒標記。
PRO_ONLY_FIELDS = ("tag_audio_events",)

# `<|speaker:0|>` 這類說話者標記（transcribe-1-pro 專有輸出）⇒ 移除以維持純文字契約。
_SPEAKER_MARKER_RE = re.compile(r"<\|speaker:\d+\|>")


def strip_speaker_markers(text: str) -> str:
    """移除 transcribe-1-pro 的 `<|speaker:N|>` 標記，並把殘留空白正規化。

    - 單說話者（VC 正常情況）⇒ 通常無標記，輸出原樣。
    - 多說話者 ⇒ 標記被移除，文字內容保留（VC 契約：回傳純轉錄文字）。
    """
    if not text:
        return ""
    cleaned = _SPEAKER_MARKER_RE.sub(" ", text)
    cleaned = re.sub(r"[ \t\u3000]{2,}", " ", cleaned)
    return cleaned.strip()


def pcm16_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """16-bit 單聲道裸 PCM → WAV bytes（stdlib wave 包 header）。

    VAD 產出的是裸 PCM16（float32 樣本量化），Fish ASR API 需要 wav 容器。
    """
    if not pcm_bytes:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class FishASRService:
    """Fish Audio 官方 ASR：WAV bytes → 中文轉錄文字。

    - session: 可注入的 HTTP session（具 post(url, files=, data=, headers=, timeout=) 介面）
    - transcribe: 空輸入回 ""；任何失敗 / 非 200 → ""，絕不拋出（0 崩潰主迴圈）
    - last_error / last_status（VC-1.4 additive）：失敗原因透通給上層 —
      成功 200 → 兩者清空；非 200 → last_error={"status": code, "message": body 前 200 字}、
      last_status=code；例外 → {"status": 0, "message": str(e)}。不回傳給呼叫端，
      transcribe 回傳合約維持不變。
    """

    def __init__(self, api_key: str = "", endpoint: str = DEFAULT_ASR_ENDPOINT, session=None,
                 model: str = DEFAULT_ASR_MODEL):
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model or DEFAULT_ASR_MODEL
        self._session = session
        self.last_error: Optional[dict] = None
        self.last_status: Optional[int] = None

    @classmethod
    def from_config(cls, config: dict) -> "FishASRService":
        """依 config fish_audio 區段組裝（api_key 由 env_config 解析後帶入）。"""
        fa = (config or {}).get("fish_audio") or {}
        return cls(
            api_key=fa.get("api_key", ""),
            endpoint=fa.get("asr_endpoint", DEFAULT_ASR_ENDPOINT),
            model=fa.get("asr_model", DEFAULT_ASR_MODEL),
        )

    def _ensure_session(self):
        if self._session is None:
            import requests  # 懶載入

            self._session = requests.Session()
        return self._session

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAV bytes → 轉錄文字；空輸入 / 非 200 / 網路異常 / 解析失敗 → ""（0 崩潰）。

        失敗原因記入 last_error / last_status（透通用），回傳合約不變。
        """
        if not wav_bytes:
            return ""
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            # 模型選擇＝HTTP header（body 無此欄位；放 body 會被忽略並被計為 transcribe-1）
            if self.model:
                headers["model"] = self.model
            data = {"language": "zh"}
            # tag_audio_events 等欄位只有 transcribe-1-pro 接受；transcribe-1 收到會 400
            if self.model == "transcribe-1-pro":
                for key in PRO_ONLY_FIELDS:
                    data[key] = "false"
            resp = self._ensure_session().post(
                self.endpoint,
                files={"audio": ("speech.wav", wav_bytes, "audio/wav")},
                data=data,
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code != 200:
                body = getattr(resp, "text", "") or ""
                self.last_error = {"status": resp.status_code, "message": body[:200]}
                self.last_status = resp.status_code
                return ""
            data = resp.json()
        except Exception as exc:
            self.last_error = {"status": 0, "message": str(exc)}
            self.last_status = 0
            return ""
        self.last_error = None
        self.last_status = None
        text = data.get("text") if isinstance(data, dict) else None
        return strip_speaker_markers((text or "").strip())