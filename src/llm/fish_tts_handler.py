"""
src/llm/fish_tts_handler.py
Soul OS — Phase 4 語言層改造：Fish TTS 自動觸發 Handler
        + Phase 5：emotion tag → Fish Audio [bracket] marker 注入

鏈路：
  AGENT_SPEAK event (階段 3 proxy.py 發出)
    → FishTTSHandler._on_agent_speak (訂閱端)
      → 檢查 tts_enabled (既有開關)
      → 從 payload 取 agent_id / audio_text / emotion
      → 查 AGENT_ID_TO_VOICE_KEY 對應到 fish_tts.VOICES 的 alias
      → fire-and-forget task: _synthesize_async()
        → Phase 5: 查 emotion_marker_map 拿 Fish [bracket] marker
        → prefix marker 到 audio_text (marker 為 None 時不插)
        → asyncio.to_thread() 跑阻塞 HTTP
        → 直接打 Fish Audio API（不呼叫 fish_tts.synthesize()，
          因為它用 sys.exit() 處理錯誤會殺掉 handler process）
        → 寫 mp3 到 data/tts/{agent_id}/{ts}.mp3

明確不做的事（per 階段 4 + 階段 5 施工書）：
  - 不改 fish_tts.py 任何一行（包括 synthesize() 內部合成邏輯）
  - 不改 proxy.py payload 結構 / 階段 3 白名單邏輯
  - 不改 emotion tags 定義本身（階段 2.5 已 30/30 驗證）
  - 不在這層處理 heartbeat session 邊界（上游 soul.md + 白名單驗證已守住）
  - 不處理 Live2D 動作觸發

emotion marker 機制（per 階段 5 設計）：
  - fish_tts.py 用 s2.1-pro-free model → 走 S2.1-Pro [方框] 語法
  - 對應表存在獨立模組 emotion_marker_map.py（分離關注點）
  - emotion 為 None / 空字串 / 沒對應 → 不插 marker,送原始 audio_text
  - 不插 [calm] 等預設值（避免程式擅自做隱性判斷）
  - emotion → marker 由 Bry 拍板的 37 條對應決定（38 tag - Miku silent 留白）
  - 語音好聽與否 Bry 自己聽覺驗收，本層只做結構驗證

Aoi 命名不對稱：
  fish_tts.VOICES 沒有 `aoi_voice`，Aoi 對應 `hinami_voice`
  （日南葵）。這個對應寫在 AGENT_ID_TO_VOICE_KEY 內顯式記錄。

非阻塞設計：
  - 訂閱 callback 是 sync（bus.subscribe 要求）— 內部立刻
    asyncio.create_task(self._synthesize_async(...)) 後 return
  - _synthesize_async 內用 asyncio.to_thread() 把阻塞的
    requests.post 丟到 thread pool
  - 主對話流程不 await TTS 完成，使用者立即看到文字回應
  - 整條鏈路 try/except SystemExit + Exception 包好，
    失敗 log warning 不 raise
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.async_utils import create_managed_task
from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.llm.emotion_marker_map import resolve_marker

logger = logging.getLogger("soul_os.fish_tts_handler")


# ──────────────────────────────────────────────────────────────────
# 1. agent_id → fish_tts.VOICES alias 對應表
#    顯式寫死，避免將來加角色時漏掉 mapping
#    fish_tts.VOICES keys: rem_voice / rem_zh_voice / mahiru_voice /
#                          yua_voice / akane_voice / ruka_voice /
#                          mai_voice / ram_voice / miku_voice /
#                          anna_voice / hinami_voice
# ──────────────────────────────────────────────────────────────────
AGENT_ID_TO_VOICE_KEY: Dict[str, str] = {
    "agent_rem":    "rem_voice",       # Re:Zero — レム
    "agent_ram":    "ram_voice",       # Re:Zero — ラム
    "agent_yua":    "yua_voice",       # ユア
    "agent_ruka":   "ruka_voice",      # 更科瑠夏
    "agent_akane":  "akane_voice",     # 黒川茜
    "agent_mahiru": "mahiru_voice",    # 椎名まひる
    "agent_mai":    "mai_voice",       # 櫻島麻衣
    "agent_miku":   "miku_voice",      # 中野三玖
    "agent_anna":   "anna_voice",      # 山田杏奈
    "agent_aoi":    "hinami_voice",    # 日南葵（Aoi = 日南葵，fish_tts 用 hinami_voice 命名）
}

# Fish Audio API endpoint（跟 fish_tts.py API_URL 一樣 — 不重新發明）
# 從 fish_tts module 動態 import；失敗就 hard-code 當 fallback
_FISH_API_URL: Optional[str] = None
_fish_tts_loaded = False


def _load_fish_tts_module():
    """
    動態 import fish_tts 模組（VOICES + resolve_voice_id + load_api_key + API_URL）

    fish_tts.py 在 C:\\Users\\bbfcc\\Downloads\\voice\\ 內，
    soul-os-harness 不在同個 package，需要 sys.path 注入。

    失敗不 raise — handler 還是要 register（不讓整個 server 掛掉），
    只是實際合成時會 fail 並 log。
    """
    global _FISH_API_URL, _fish_tts_loaded
    if _fish_tts_loaded:
        return
    try:
        # Phase 5.5（2026-07-14）：voice/ 從 Downloads 搬到 soul-os-harness/src/voice/
        # 用相對路徑自動找,Bry 換機器/換目錄不用改
        _voice_dir = str(Path(__file__).resolve().parent.parent / "voice")
        if _voice_dir not in sys.path:
            sys.path.insert(0, _voice_dir)
        import fish_tts  # type: ignore
        _FISH_API_URL = fish_tts.API_URL
        _fish_tts_loaded = True
        logger.info(f"[FishTTS] fish_tts module loaded, API_URL={_FISH_API_URL}")
    except Exception as e:
        logger.warning(
            f"[FishTTS] fish_tts module 載入失敗: {type(e).__name__}: {e}. "
            f"TTS 合成會全部失敗但不影響主對話"
        )
        _fish_tts_loaded = True  # 標記為已嘗試，避免每次 event 都重試 import


def _resolve_voice_id(agent_id: str) -> Optional[str]:
    """
    從 agent_id 查 fish_tts.VOICES alias → 32-char Fish reference_id

    Returns:
        32-char reference_id 或 None（agent_id 沒對應時）
    """
    voice_key = AGENT_ID_TO_VOICE_KEY.get(agent_id)
    if not voice_key:
        return None
    try:
        import fish_tts  # type: ignore
        return fish_tts.resolve_voice_id(voice_key)
    except Exception as e:
        logger.warning(f"[FishTTS] resolve_voice_id({voice_key}) 失敗: {e}")
        return None


def _load_api_key() -> Optional[str]:
    """從 fish_tts 模組拿 API key（避免重複 .env 解析邏輯）"""
    try:
        import fish_tts  # type: ignore
        return fish_tts.load_api_key()
    except SystemExit as e:
        # fish_tts.load_api_key() 內找不到 key 會 sys.exit()
        # 這裡把它轉成 None，不殺 handler
        logger.warning(f"[FishTTS] load_api_key failed: {e}")
        return None
    except Exception as e:
        logger.warning(f"[FishTTS] load_api_key error: {type(e).__name__}: {e}")
        return None


# ──────────────────────────────────────────────────────────────────
# 2. FishTTSHandler class
# ──────────────────────────────────────────────────────────────────
class FishTTSHandler:
    """
    訂閱 AGENT_SPEAK → 自動觸發 Fish TTS 合成

    Usage:
        handler = FishTTSHandler(bus=bus, output_dir=Path("data/tts"))
        handler.register()
    """

    def __init__(
        self,
        bus: SoulEventBus,
        # P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        output_dir: Optional[Path] = None,
        *,
        enabled: bool = True,
    ):
        self.bus = bus
        # P0.5 (Bry 派工 2026-08-09 19:48): resolve via data_root() for test isolation
        from src.paths import data_root
        if output_dir is None:
            output_dir = data_root() / "tts"
        self.output_dir = Path(output_dir)
        self.enabled = enabled
        self._fish_tts_available = False  # import 成功才標 True
        # 啟動時嘗試載入 fish_tts module
        _load_fish_tts_module()
        self._fish_tts_available = _fish_tts_loaded and _FISH_API_URL is not None
        if self.enabled and self._fish_tts_available:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                logger.info(
                    f"[FishTTS] Handler initialized, output_dir={self.output_dir}"
                )
            except Exception as e:
                logger.warning(f"[FishTTS] output_dir 建立失敗: {e}")

        # 階段 5.5+ (Bry 拍板 2026-07-15): 用 TTSService 取代內部寫檔邏輯
        # - TTSService 統一路徑/URL/事件廣播
        # - 不再各自寫檔,所有 channel 透過 AGENT_AUDIO_READY 事件訂閱
        # - 動態 import 避免循環（src.voice.tts_service 會用到 SoulEvent）
        try:
            from src.voice.tts_service import TTSService
            self.tts_service = TTSService(
                bus=self.bus,
                output_dir=self.output_dir,
                public_url_prefix="/api/tts/audio",
            )
            logger.info("[FishTTS] TTSService wired in")
        except Exception as e:
            logger.warning(
                f"[FishTTS] TTSService 載入失敗,fallback 舊寫檔邏輯: "
                f"{type(e).__name__}: {e}"
            )
            self.tts_service = None

    def register(self) -> None:
        """向 bus 註冊 AGENT_SPEAK 訂閱"""
        if not self.enabled:
            logger.info("[FishTTS] Handler disabled, skip subscribe")
            return
        self.bus.subscribe(
            subscriber_id="fish_tts_handler",
            handler=self._on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )
        logger.info("[FishTTS] Subscribed to AGENT_SPEAK")

    # ── Sync callback（bus.subscribe 要求 async def，這裡用 sync 邏輯立刻 fire-and-forget）──
    async def _on_agent_speak(self, event: SoulEvent) -> None:
        """
        收到 AGENT_SPEAK 後的入口

        - 檢查 tts_enabled（False 跳過）
        - 取 agent_id / audio_text / emotion / message_id (M6.2-1)
        - 立刻 asyncio.create_task 丟背景，return 不等 TTS 完成
        """
        try:
            payload = event.payload
            # 1. 既有開關檢查
            tts_enabled = payload.get("tts_enabled", False)
            if not tts_enabled:
                logger.debug(
                    f"[FishTTS] tts_enabled=False, skip | "
                    f"agent={payload.get('agent_id')}"
                )
                return

            # 2. 必要欄位檢查
            agent_id = payload.get("agent_id") or event.source
            audio_text = payload.get("audio_text")
            if not audio_text or not audio_text.strip():
                logger.warning(
                    f"[FishTTS] audio_text 為空, skip | agent={agent_id}"
                )
                return

            emotion = payload.get("emotion", "")
            text = payload.get("text", "")

            # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
            # 從 AGENT_SPEAK event_id 抽出 message_id,透傳給 TTSService
            # → AGENT_AUDIO_READY payload → ChannelRouter / web client
            # 讓 audio 對應到「同一則 text」而不是「同一個 agent 的最新一筆」
            message_id = getattr(event, "event_id", None) or payload.get("message_id")

            # 3. log 觀察（Phase 5：emotion 會查 marker 注入 text）
            logger.info(
                f"[FishTTS] trigger | agent={agent_id} "
                f"audio_len={len(audio_text)} emotion={emotion!r} "
                f"message_id={message_id[:8] if message_id else 'None'} "
                f"(Phase 5: marker 會在 _synthesize_async 內 prefix 進 text)"
            )

            # 4. fire-and-forget — 不 await task，不卡主流程
            # KI-007: 改受管任務（保存強引用 + done 回調捕獲異常），
            # 避免 fire-and-forget Task 被 GC 提前回收導致 C 擴展記憶體損壞
            create_managed_task(
                self._synthesize_async(
                    agent_id=agent_id,
                    audio_text=audio_text,
                    emotion=emotion,
                    text_preview=text,
                    message_id=message_id,
                )
            )
        except Exception as e:
            # 訂閱 callback 不能 raise（會被 bus._safe_dispatch 接住，
            # 但這裡再防一層保險）
            logger.warning(f"[FishTTS] _on_agent_speak error: {e}")

    # ── Async 實際合成 ──
    async def _synthesize_async(
        self,
        *,
        agent_id: str,
        audio_text: str,
        emotion: str,
        text_preview: str,
        # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
        # 從 _on_agent_speak 透傳進來,再傳給 TTSService.synthesize_and_store
        message_id: Optional[str] = None,
    ) -> None:
        """
        真正呼叫 Fish Audio API 的 async 函式

        流程：
          0. Phase 5: 查 emotion_marker_map 拿 Fish [bracket] marker
                       marker 為 None → 不插,送原始 audio_text
          1. 查 voice_id（agent_id → alias → 32-char ref）
          2. load_api_key
          3. asyncio.to_thread() 跑阻塞 requests.post
          4. 寫 mp3 到 output_dir (含 message_id 透傳)
          5. 任何錯誤 log warning 不 raise
        """
        try:
            # 0. Phase 5: emotion marker 注入
            marker = resolve_marker(agent_id, emotion)
            if marker:
                final_text = f"{marker} {audio_text}"
                logger.info(
                    f"[FishTTS] marker applied | agent={agent_id} "
                    f"emotion={emotion!r} marker={marker!r} "
                    f"text_len={len(audio_text)} -> {len(final_text)}"
                )
            else:
                final_text = audio_text
                if emotion:
                    # 有 emotion 但查不到對應 → 走 Bry 拍板的 fallback（不插,送原 audio_text）
                    logger.info(
                        f"[FishTTS] no marker for emotion | agent={agent_id} "
                        f"emotion={emotion!r} (fallback: 送原始 audio_text)"
                    )
                # emotion 為 None / 空字串 → 不 log（沒情緒標記是正常情況）

            # 1. voice mapping
            voice_id = _resolve_voice_id(agent_id)
            if not voice_id:
                logger.warning(
                    f"[FishTTS] agent_id={agent_id!r} 沒對應 voice mapping "
                    f"(AGENT_ID_TO_VOICE_KEY 沒這個 key)，跳過"
                )
                return
            voice_key = AGENT_ID_TO_VOICE_KEY[agent_id]

            # 2. api key
            api_key = _load_api_key()
            if not api_key:
                logger.warning(
                    f"[FishTTS] FISH_API_KEY 載入失敗，跳過合成 | agent={agent_id}"
                )
                return

            # 3. 阻塞 HTTP 丟 thread pool（不卡 event loop）
            #    Phase 5: 傳 final_text（含 marker）給 Fish API
            mp3_bytes = await asyncio.to_thread(
                _call_fish_api_blocking,
                text=final_text,
                voice_id=voice_id,
                voice_key=voice_key,
                api_key=api_key,
            )

            if not mp3_bytes:
                logger.warning(
                    f"[FishTTS] API 回傳空 audio | agent={agent_id} "
                    f"voice={voice_key}"
                )
                return

            # 4. 寫檔 + emit AGENT_AUDIO_READY (階段 5.5+ 2026-07-15)
            #    改用 TTSService 取代舊的 out_path.write_bytes 直接寫入
            #    - 統一檔案路徑/URL/事件廣播
            #    - ChannelRouter / web gateway 訂閱 AGENT_AUDIO_READY 自動播
            if self.tts_service is not None:
                await self.tts_service.synthesize_and_store(
                    agent_id=agent_id,
                    mp3_bytes=mp3_bytes,
                    emotion=emotion,
                    text_preview=text_preview,
                    # M6.2-1: 透傳 message_id 到 AGENT_AUDIO_READY
                    message_id=message_id,
                )
            else:
                # fallback: 舊的直接寫檔邏輯（TTSService 載入失敗時的退路）
                out_path = self._make_output_path(agent_id)
                try:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(mp3_bytes)
                    logger.info(
                        f"[FishTTS] wrote {out_path} | "
                        f"agent={agent_id} voice={voice_key} emotion={emotion!r} "
                        f"bytes={len(mp3_bytes)} (fallback mode)"
                    )
                except Exception as e:
                    logger.warning(
                        f"[FishTTS] 寫檔失敗 {out_path}: {type(e).__name__}: {e}"
                    )

        except Exception as e:
            # 整層包好 — TTS 失敗不影響主對話
            logger.warning(
                f"[FishTTS] _synthesize_async 失敗 | agent={agent_id} "
                f"{type(e).__name__}: {e}"
            )

    def _make_output_path(self, agent_id: str) -> Path:
        """產出檔案路徑：data/tts/{agent_id}/{ISO_ts}.mp3"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        return self.output_dir / agent_id / f"{ts}.mp3"


# ──────────────────────────────────────────────────────────────────
# 3. 阻塞 HTTP 呼叫（給 asyncio.to_thread 跑）
#    複製 fish_tts.synthesize() 的核心 requests.post 邏輯
#    但**不呼叫 fish_tts.synthesize()**（它裡面用 sys.exit 處理錯誤）
#    也不改 fish_tts.py 任何一行
# ──────────────────────────────────────────────────────────────────
def _call_fish_api_blocking(
    *,
    text: str,
    voice_id: str,
    voice_key: str,
    api_key: str,
    timeout: int = 180,
) -> Optional[bytes]:
    """
    同步呼叫 Fish Audio /v1/tts — 回傳 mp3 bytes 或 None

    等同 fish_tts.synthesize() 的 requests.post 部分，但：
      - 失敗不 sys.exit()，回傳 None
      - 不寫檔（handler 層自己寫）
    """
    import requests  # lazy import

    if _FISH_API_URL is None:
        logger.warning("[FishTTS] _FISH_API_URL 為 None，無法呼叫")
        return None

    try:
        r = requests.post(
            _FISH_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "model": "s2.1-pro-free",  # 跟 fish_tts.DEFAULT_MODEL 一致
            },
            json={"text": text, "reference_id": voice_id, "format": "mp3"},
            timeout=timeout,
        )
        if r.status_code != 200:
            logger.warning(
                f"[FishTTS] API failed | status={r.status_code} "
                f"voice={voice_key} body={r.text[:200]}"
            )
            return None
        return r.content
    except Exception as e:
        logger.warning(
            f"[FishTTS] requests.post 錯誤 | {type(e).__name__}: {e}"
        )
        return None
