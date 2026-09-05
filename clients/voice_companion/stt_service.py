"""
stt_service.py — Fish Audio 官方 ASR 服務（VC-1.1 模組 1，輸入側）。

- POST https://api.fish.audio/v1/asr（multipart），Headers: Authorization: Bearer <api_key>
- multipart：files={"audio": ("speech.wav", wav_bytes, "audio/wav")}、data={"language": "zh"}、timeout=10.0
- 回傳 200 → resp.json().get("text","").strip()；空輸入 / 非 200 / 網路異常 / 解析失敗 → ""（0 崩潰）

離線可測：session（具 post(url, files=, data=, headers=, timeout=) 介面）可注入；requests 懶載入。
"""

from __future__ import annotations

import io
import wave
from typing import Optional

DEFAULT_ASR_ENDPOINT = "https://api.fish.audio/v1/asr"


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
    """

    def __init__(self, api_key: str = "", endpoint: str = DEFAULT_ASR_ENDPOINT, session=None):
        self.api_key = api_key
        self.endpoint = endpoint
        self._session = session

    @classmethod
    def from_config(cls, config: dict) -> "FishASRService":
        """依 config fish_audio 區段組裝（api_key 由 env_config 解析後帶入）。"""
        fa = (config or {}).get("fish_audio") or {}
        return cls(
            api_key=fa.get("api_key", ""),
            endpoint=fa.get("asr_endpoint", DEFAULT_ASR_ENDPOINT),
        )

    def _ensure_session(self):
        if self._session is None:
            import requests  # 懶載入

            self._session = requests.Session()
        return self._session

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAV bytes → 轉錄文字；空輸入 / 非 200 / 網路異常 / 解析失敗 → ""（0 崩潰）。"""
        if not wav_bytes:
            return ""
        try:
            resp = self._ensure_session().post(
                self.endpoint,
                files={"audio": ("speech.wav", wav_bytes, "audio/wav")},
                data={"language": "zh"},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10.0,
            )
        except Exception:
            return ""
        if resp.status_code != 200:
            return ""
        try:
            data = resp.json()
        except Exception:
            return ""
        text = data.get("text") if isinstance(data, dict) else None
        return (text or "").strip()