"""
src/io/tts.py
Soul OS — msedge-tts 整合（Phase 6.x TTS）

把 widget.html 期望的 GET /api/tts endpoint 接到 Microsoft Edge 線上 TTS。

鏈路：
  widget.html  →  fetch('/api/tts?voice=zh-TW-HsiaoChenNeural&text=...')
                →  IOGateway._tts_endpoint()
                →  edge_tts.Communicate(text, voice).stream()
                →  audio/mpeg (MP3) bytes stream 回傳
                →  widget 自動用 Web Audio 播放 + 驅動 Live2D 對嘴

設計：
  - 純 library，無 daemon，無狀態（除 in-memory cache）
  - voice 預設 zh-TW-HsiaoChenNeural（widget.html 預設值對齊）
  - text 上限 600 chars（widget 端先截，這裡 server 端保險再做一次）
  - 5 分鐘記憶體 cache（相同 voice+text 不重打 edge-tts）— heartbeat 60s
    主動觸發會用同樣的 draft，cache 避免重複打 Microsoft
  - edge-tts 任何錯誤回傳 None，gateway 端轉 404 + JSON（widget 自動 fallback 瀏覽器 TTS）
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import edge_tts

logger = logging.getLogger("soul_os.tts")

# 預設聲音 — 對齊 widget.html 的 NEURAL_VOICE 預設值
DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"

# text 上限（widget 端已先截 600，這裡 server 端保險再截）
MAX_TEXT_CHARS = 600

# 快取 TTL — heartbeat 60s 主動觸發時 draft 常常相同
CACHE_TTL_SECONDS = 300  # 5 分鐘


@dataclass
class TTSCacheEntry:
    """記憶體 cache entry"""
    mp3_bytes: bytes
    created_at: float = field(default_factory=time.time)


# in-memory cache: key = hash(voice + text)
_cache: dict[str, TTSCacheEntry] = {}


def _make_key(voice: str, text: str) -> str:
    """cache key — hash(voice + text)"""
    raw = f"{voice}::{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_cache_fresh(entry: TTSCacheEntry) -> bool:
    return (time.time() - entry.created_at) < CACHE_TTL_SECONDS


def _prune_cache() -> None:
    """刪掉過期 entries — 避免記憶體無限制長大"""
    now = time.time()
    expired = [k for k, v in _cache.items() if (now - v.created_at) >= CACHE_TTL_SECONDS]
    for k in expired:
        del _cache[k]
    if expired:
        logger.debug(f"[TTS] cache pruned {len(expired)} entries, remaining={len(_cache)}")


async def synthesize_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
    *,
    use_cache: bool = True,
) -> Optional[bytes]:
    """
    把 text 轉成 MP3 bytes（用 edge-tts Communicate stream）

    Args:
        text: 要唸的文字（會被截到 MAX_TEXT_CHARS）
        voice: msedge voice name（e.g. "zh-TW-HsiaoChenNeural"）
        use_cache: True = 用 5min in-memory cache

    Returns:
        MP3 bytes 或 None（失敗時）

    Raises:
        不 raise — 所有 edge-tts 錯誤都吞掉 log warning，回傳 None
    """
    # text 截斷
    text = (text or "").strip()
    if not text:
        logger.warning("[TTS] empty text, skip")
        return None
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        logger.debug(f"[TTS] text truncated to {MAX_TEXT_CHARS} chars")

    voice = voice or DEFAULT_VOICE

    # cache lookup
    key = _make_key(voice, text)
    if use_cache and key in _cache and _is_cache_fresh(_cache[key]):
        logger.debug(f"[TTS] cache hit | voice={voice} text_len={len(text)}")
        return _cache[key].mp3_bytes

    # edge-tts synthesize
    try:
        communicate = edge_tts.Communicate(text, voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            # chunk type: "audio" (MP3 bytes) or "WordBoundary" (metadata)
            if chunk.get("type") == "audio":
                chunks.append(chunk["data"])
        mp3_bytes = b"".join(chunks)

        if not mp3_bytes or len(mp3_bytes) < 100:
            logger.warning(
                f"[TTS] edge-tts returned empty/too-small audio | voice={voice} "
                f"text_len={len(text)} bytes={len(mp3_bytes)}"
            )
            return None

        # cache store
        if use_cache:
            _prune_cache()
            _cache[key] = TTSCacheEntry(mp3_bytes=mp3_bytes)
            logger.debug(f"[TTS] cached | voice={voice} bytes={len(mp3_bytes)}")

        logger.info(
            f"[TTS] synthesized | voice={voice} text_len={len(text)} bytes={len(mp3_bytes)}"
        )
        return mp3_bytes

    except asyncio.CancelledError:
        # 不要 swallow CancelledError — FastAPI 需要它做 cancel propagation
        raise
    except Exception as e:
        logger.warning(f"[TTS] edge-tts failed | voice={voice} text_len={len(text)} err={e!r}")
        return None


def get_cache_stats() -> dict:
    """debug 用 — 看 cache 狀態"""
    _prune_cache()
    return {
        "entries": len(_cache),
        "ttl_seconds": CACHE_TTL_SECONDS,
    }
