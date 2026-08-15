"""src/voice/tts_service.py

TTS 共用服務（B 方案 2026-07-15 Bry 拍板）

設計目標：
  - Fish TTS 寫完 mp3 後，emit 一個 `AGENT_AUDIO_READY` 事件
  - 任何 channel（web / telegram / 未來）訂閱這個事件，自行決定怎麼消費音訊
  - 統一介面：路徑、URL、list_recent、cleanup
  - 跟 fish_tts.py 解耦 — TTSService 只管「檔案管理 + 事件廣播」

介面：
  - tts_service.synthesize_and_store(agent_id, mp3_bytes) -> dict
    - 寫 mp3 到 output_dir/{agent_id}/{ts}.mp3
    - 自動 emit AGENT_AUDIO_READY 事件
    - 回傳 {audio_path, audio_url, ts}
  - tts_service.get_audio_url(agent_id, ts) -> str
  - tts_service.list_recent(agent_id, limit=1) -> list[dict]
  - tts_service.cleanup_older_than(hours=24)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soul_os.tts_service")


class TTSService:
    """
    TTS 檔案 + 事件管理（單例模組）

    設計：
      - __init__ 接受 bus（注入 EventBus）+ output_dir + public_url_prefix
      - synthesize_and_store() 寫檔 + emit AGENT_AUDIO_READY
      - 路徑產出: data/tts/{agent_id}/{ISO_ts}.mp3
        - URL 產出: {public_url_prefix}/{agent_id}/{ts}.mp3
          - public_url_prefix 預設 "/api/tts/audio"（Gateway 端對應 endpoint）
    """

    def __init__(
        self,
        bus: Any,  # 避開循環 import,型別用 Any
        # P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        output_dir: Optional[Path] = None,
        public_url_prefix: str = "/api/tts/audio",
    ) -> None:
        self.bus = bus
        # P0.5 (Bry 派工 2026-08-09 19:48): resolve via data_root() for test isolation
        from src.paths import data_root
        if output_dir is None:
            output_dir = data_root() / "tts"
        self.output_dir = Path(output_dir)
        self.public_url_prefix = public_url_prefix.rstrip("/")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ───────────────────────────────────────
    # 路徑 / URL 工具
    # ───────────────────────────────────────
    def _make_filename(self) -> str:
        """ISO UTC ts,微秒精度避免碰撞"""
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")

    def get_audio_path(self, agent_id: str, ts: Optional[str] = None) -> Path:
        """產出絕對路徑 (data/tts/{agent_id}/{ts}.mp3)"""
        if ts is None:
            ts = self._make_filename()
        return self.output_dir / agent_id / f"{ts}.mp3"

    def get_audio_url(self, agent_id: str, ts: str) -> str:
        """對外公開 URL (e.g. /api/tts/audio/agent_mahiru/20260715T...mp3)"""
        return f"{self.public_url_prefix}/{agent_id}/{ts}.mp3"

    # ───────────────────────────────────────
    # 寫檔 + 事件
    # ───────────────────────────────────────
    async def synthesize_and_store(
        self,
        *,
        agent_id: str,
        mp3_bytes: bytes,
        emotion: str = "",
        text_preview: str = "",
        # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
        # 從上游 AGENT_SPEAK 的 event_id 透傳,讓 AGENT_AUDIO_READY 跟
        # 原始 text message 配對。None = backward compat (不傳 message_id)。
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        寫 mp3 到磁碟 + emit AGENT_AUDIO_READY 事件

        Returns:
          {
            "agent_id": str,
            "ts": str,                  # ISO ts
            "audio_path": str,           # 絕對路徑
            "audio_url": str,            # 公開 URL
            "emotion": str,
            "size": int,
            "message_id": str | None,    # M6.2-1: correlation to text message
          }
        """
        ts = self._make_filename()
        out_path = self.get_audio_path(agent_id, ts)
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(mp3_bytes)
        except Exception as e:
            logger.warning(
                f"[TTSService] 寫檔失敗 {out_path}: {type(e).__name__}: {e}"
            )
            return {
                "agent_id": agent_id,
                "ts": ts,
                "audio_path": str(out_path),
                "audio_url": "",
                "emotion": emotion,
                "size": 0,
                "message_id": message_id,
                "error": str(e),
            }

        audio_url = self.get_audio_url(agent_id, ts)
        size = len(mp3_bytes)
        logger.info(
            f"[TTSService] wrote {out_path} | agent={agent_id} "
            f"emotion={emotion!r} size={size} url={audio_url} message_id={message_id}"
        )

        # ── emit AGENT_AUDIO_READY ──
        # ChannelRouter / web gateway 訂閱,各自決定怎麼播
        try:
            if self.bus is not None:
                # 動態 import 避免循環
                from src.eventbus.schema import EventType, SoulEvent, EventPriority
                event = SoulEvent(
                    event_type=EventType.AGENT_AUDIO_READY,
                    source=agent_id,
                    target="broadcast",
                    priority=EventPriority.NORMAL,
                    payload={
                        "agent_id": agent_id,
                        "ts": ts,
                        "audio_path": str(out_path),
                        "audio_url": audio_url,
                        "emotion": emotion,
                        "size": size,
                        "text_preview": text_preview[:120],
                        # M6.2-1: 透傳上游 AGENT_SPEAK 的 event_id,給
                        # ChannelRouter (per-message pair) 跟 web client
                        # (attach replay button to specific text) 用
                        "message_id": message_id,
                    },
                )
                await self.bus.publish(event)
                logger.debug(
                    f"[TTSService] AGENT_AUDIO_READY published | "
                    f"agent={agent_id} url={audio_url} message_id={message_id}"
                )
        except Exception as e:
            # 事件廣播失敗不影響主流程
            logger.warning(
                f"[TTSService] AGENT_AUDIO_READY publish 失敗: "
                f"{type(e).__name__}: {e}"
            )

        return {
            "agent_id": agent_id,
            "ts": ts,
            "audio_path": str(out_path),
            "audio_url": audio_url,
            "emotion": emotion,
            "size": size,
            "message_id": message_id,
        }

    # ───────────────────────────────────────
    # 查詢
    # ───────────────────────────────────────
    def list_recent(self, agent_id: str, limit: int = 1) -> List[Dict[str, Any]]:
        """
        列出 agent 最近 N 個 mp3（按檔名時間戳排序,新到舊）

        Returns:
          [
            {"ts": str, "audio_path": str, "audio_url": str, "size": int},
            ...
          ]
        """
        agent_dir = self.output_dir / agent_id
        if not agent_dir.exists():
            return []
        files = sorted(
            agent_dir.glob("*.mp3"),
            key=lambda p: p.name,
            reverse=True,  # ts 格式 yyyymmddT... 字典序 = 時間序
        )
        results = []
        for f in files[:limit]:
            ts = f.stem  # 去掉 .mp3
            results.append({
                "ts": ts,
                "audio_path": str(f),
                "audio_url": self.get_audio_url(agent_id, ts),
                "size": f.stat().st_size,
            })
        return results

    # ───────────────────────────────────────
    # 清理
    # ───────────────────────────────────────
    def cleanup_older_than(self, hours: int = 24) -> int:
        """
        刪除超過 N 小時的 mp3（避免長期累積佔磁碟）

        Returns: 刪除數量
        """
        import time
        cutoff = time.time() - (hours * 3600)
        deleted = 0
        for mp3 in self.output_dir.rglob("*.mp3"):
            try:
                if mp3.stat().st_mtime < cutoff:
                    mp3.unlink()
                    deleted += 1
            except Exception as e:
                logger.warning(
                    f"[TTSService] cleanup 失敗 {mp3}: "
                    f"{type(e).__name__}: {e}"
                )
        if deleted:
            logger.info(
                f"[TTSService] cleanup_older_than({hours}h) "
                f"deleted={deleted}"
            )
        return deleted
