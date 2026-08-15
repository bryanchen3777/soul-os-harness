"""
src/io/gateway.py
Soul OS — Phase 4 I/O Gateway：WebSocket + 靜態檔案服務
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

# P0.5 (Bry 派工 2026-08-09 19:48): use data_root() so test subprocess can
# redirect via SOUL_OS_DATA_DIR. Production: defaults to "data/tts",
# "data/conversations/..." unchanged.
from src.paths import data_root

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.agent.emotion import emotion_engine

logger = logging.getLogger("soul_os.gateway")

# 找出 static 目錄（在 import 時解析完成）
import os as _os
_GATEWAY_DIR = Path(__file__).resolve().parent  # src/io/
_REPO_ROOT_CANDIDATES = [
    _GATEWAY_DIR.parent.parent,  # 從 src/io/ 往上兩層
    Path.cwd(),                  # current working directory
]
_STATIC_INDEX = None
_UI_HTML = None
_STATIC_DIR = None

for _candidate in _REPO_ROOT_CANDIDATES:
    _static = _candidate / "static"
    _index = _static / "index.html"
    if _index.exists():
        _STATIC_DIR = _static
        _STATIC_INDEX = _index
        _UI_HTML = _STATIC_INDEX.read_text(encoding="utf-8")
        logger.info(f"[Gateway] Loaded static UI from {_STATIC_INDEX} ({len(_UI_HTML)} bytes)")
        logger.info(f"[Gateway] Static root: {_STATIC_DIR}")
        break

if _UI_HTML is None:
    logger.warning("[Gateway] static/index.html not found, using DEMO_HTML fallback")

DEMO_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Soul OS — Live</title>
  <style>
    body { background: #0d0d0d; color: #e0e0e0; font-family: monospace; padding: 2rem; }
    #log { max-height: 80vh; overflow-y: auto; }
    .yua   { color: #a78bfa; }
    .ruka  { color: #f472b6; }
    .system{ color: #6b7280; font-size: 0.85em; }
    .msg   { margin: 0.4rem 0; }
  </style>
</head>
<body>
  <h2>Soul OS — Live Feed</h2>
  <div id="log"></div>
  <script>
    const ws = new WebSocket(`ws://${location.host}/ws`);
    const log = document.getElementById('log');
    ws.onopen = () => {
      const div = document.createElement('div');
      div.className = 'msg system';
      div.textContent = '[connected]';
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      console.log('WebSocket connected');
    };
    ws.onmessage = (e) => {
      const d = JSON.parse(e.data);
      console.log('WS msg:', d);
      if (d.type === 'ping') return;
      const div = document.createElement('div');
      const isYua = (d.agent_id || '').includes('yua');
      const isRuka = (d.agent_id || '').includes('ruka');
      div.className = `msg ${isYua ? 'yua' : isRuka ? 'ruka' : 'system'}`;
      const ts = d.timestamp ? new Date(d.timestamp).toLocaleTimeString() : '';
      div.textContent = `[${ts}] ${d.agent_id ?? 'system'}: ${d.text ?? JSON.stringify(d)}`;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
    };
    ws.onclose = () => {
      const div = document.createElement('div');
      div.className = 'msg system';
      div.textContent = '[disconnected]';
      log.appendChild(div);
    };
  </script>
</body>
</html>
"""


def _default_group_members() -> list[str]:
    """
    UI → Backend 群聊 /group_members 的 fallback:
    從 configs/default.yaml 動態讀所有 enabled 的 agent_id,
    不再寫死 4 個 (Phase 7/8/9 加 Ram/Mahiru/Anna 後,前端動到 7 個,此函式同步對齊)。
    """
    try:
        from configs.loader import load_config
        cfg = load_config()
        return [
            a["id"] for a in cfg.get("agents", [])
            if a.get("enabled", True) and "id" in a
        ]
    except Exception as e:
        logger.warning(f"[Gateway] _default_group_members fallback failed: {e}, using 4-agent hardcoded list")
        return ["agent_yua", "agent_ruka", "agent_akane", "agent_rem"]


# Display name 映射（Phase 2 動態 UI）
# Source: 第一優先是這個 dict;fallback 是從 agent_id 去掉 "agent_" 前綴並 title-case
# 新增 agent 時在此加一行（最低成本維護路徑）
AGENT_DISPLAY_NAMES: dict[str, str] = {
    "agent_yua":    "Yua",
    "agent_ruka":   "更科瑠夏",   # Sarashina Ruka
    "agent_akane":  "黒川あかね",  # Kurokawa Akane
    "agent_rem":    "雷姆",        # Rem（顯示用繁中）
    "agent_ram":    "Ram",         # 拉姆
    "agent_mahiru": "真昼",        # 椎名真昼
    "agent_anna":   "杏奈",        # 山田杏奈
    "agent_mai":    "麻衣",        # 桜島麻衣
    "agent_miku":   "三玖",        # 中野三玖
    "agent_aoi":    "葵",          # 日南葵
}


def _list_agents() -> list[dict]:
    """
    Phase 2: 回傳所有 enabled agent 的 metadata 給 UI。
    與 _default_group_members 同源（都從 configs/default.yaml 的 agents 清單讀）,
    確保 UI 顯示的 agent 數 == fallback group_members 數。
    """
    try:
        from configs.loader import load_config
        cfg = load_config()
        agents_cfg = cfg.get("agents", [])
        result = []
        for a in agents_cfg:
            if not a.get("enabled", True):
                continue
            if "id" not in a:
                continue
            aid = a["id"]
            result.append({
                "id": aid,
                "name": AGENT_DISPLAY_NAMES.get(aid, aid.replace("agent_", "").title()),
                "class": a.get("class", ""),
                "intimacy_level": a.get("intimacy_level", 0),
                "enabled": True,
                "persona_path": f"personas/{aid}.md",  # 假設 runtime 都讀 personas/（legacy docs/ 例外）
            })
        return result
    except Exception as e:
        logger.warning(f"[Gateway] _list_agents failed: {e}")
        return []


class ConnectionManager:
    """管理所有 WebSocket 連線"""

    def __init__(self):
        self._connections: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.add(ws)
        logger.info(f"[Gateway] 連線，目前 {len(self._connections)} 個客戶端")

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        logger.info(f"[Gateway] 離線，剩 {len(self._connections)} 個客戶端")

    async def broadcast(self, payload: dict):
        # Write to trace.log directly
        try:
            with open('trace.log', 'a', encoding='utf-8') as f:
                f.write(f'[BCAST] broadcast called, connections={len(self._connections)}\n')
                f.flush()
        except:
            pass
        conn_count = len(self._connections)
        logger.info(f'[Gateway] broadcast called, connections={conn_count}')
        if not self._connections:
            logger.info('[Gateway] broadcast early return: no connections')
            try:
                with open('trace.log', 'a', encoding='utf-8') as f:
                    f.write('[BCAST] early return: no connections\n')
                    f.flush()
            except:
                pass
            return
        msg = json.dumps(payload, ensure_ascii=False)
        logger.info(f'[Gateway] broadcast sending to {conn_count} clients: {msg[:80]}')
        dead = set()
        for ws in self._connections:
            try:
                with open('trace.log', 'a', encoding='utf-8') as f:
                    f.write(f'[SEND] about to send_text to ws, msg_len={len(msg)}\n')
                    f.flush()
                await ws.send_text(msg)
                with open('trace.log', 'a', encoding='utf-8') as f:
                    f.write(f'[SEND] send_text completed, ws={ws}\n')
                    f.flush()
                logger.info(f'[Gateway] broadcast sent to client')
                try:
                    with open('trace.log', 'a', encoding='utf-8') as f:
                        f.write('[BCAST] sent to client\n')
                        f.flush()
                except:
                    pass
            except Exception as e:
                logger.warning(f'[Gateway] broadcast send error: {e}')
                with open('trace.log', 'a', encoding='utf-8') as f:
                    f.write(f'[SEND] send_text error: {e}\n')
                    f.flush()
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)
        logger.info(f'[Gateway] broadcast done, dead={len(dead)}')

    @property
    def count(self) -> int:
        return len(self._connections)


class IOGateway:
    """
    訂閱 Event Bus 的 AGENT_SPEAK，
    將文字廣播給所有 WebSocket 客戶端。
    同時提供靜態檔案服務（頭像、CSS 等）。
    """

    def __init__(self, bus: SoulEventBus, app: FastAPI = None):
        self.bus = bus
        self.manager = ConnectionManager()
        self.app = app or FastAPI(title="Soul OS Gateway")
        # Live2D 已移除（Bry 拍板 2026-07-14）— 純文字 + STT 介面
        self._setup_routes()

    def register(self):
        self.bus.subscribe(
            "io_gateway",
            self._on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )
        # 階段 5.5+ (B 方案 2026-07-15 Bry 拍板):
        # 訂閱 AGENT_AUDIO_READY — TTSService 寫完 mp3 後會廣播這個事件
        # gateway 收到後,透過 WS broadcast 給所有 web client
        # (前端收到 type=agent_audio_ready 事件,自動 fetch mp3 播)
        # 這樣 web 端不用 poll /api/tts/recent
        self.bus.subscribe(
            "io_gateway_audio",
            self._on_agent_audio_ready,
            event_filter={EventType.AGENT_AUDIO_READY},
        )

    async def _on_agent_audio_ready(self, event: SoulEvent) -> None:
        """
        收到 TTSService 寫完 mp3 事件 → 透過 WS broadcast 給 web client
        (Telegram 由 ChannelRouter 訂閱,各自處理)
        """
        payload = event.payload
        # 廣播給所有 web client (前端收到後自動 fetch + 播)
        await self.manager.broadcast({
            "type": "agent_audio_ready",
            "agent_id": payload.get("agent_id"),
            "ts": payload.get("ts"),
            "audio_url": payload.get("audio_url"),
            "audio_path": payload.get("audio_path"),
            "emotion": payload.get("emotion"),
            "size": payload.get("size"),
            "timestamp": event.timestamp.isoformat() if event.timestamp else None,
            "session_id": getattr(event, "session_id", ""),
            # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
            # 透傳 message_id 到前端,讓 attachReplayButton 用 message_id
            # 而非 "latest" 對應到正確的 text message
            # backward compat: 沒 message_id 時 (理論上不會發生) 用 None
            "message_id": payload.get("message_id"),
        })
        logger.info(
            f"[Gateway] audio ready broadcast | agent={payload.get('agent_id')} "
            f"url={payload.get('audio_url')}"
        )

    def _setup_routes(self):

        @self.app.get("/", response_class=HTMLResponse)
        async def root():
            # 每次 GET / 都重讀 static/index.html（dev-friendly — 改 HTML 不用重啟 server）
            # _UI_HTML module-level cache 在開發迭代中累積太多 bug，犧牲一點 I/O 換可迭代性。
            # 部署環境可以加 if 條件或 reverse proxy cache 處理。
            # Live2D 已移除 — 不再注入 LIVE2D_CONFIG
            if _STATIC_INDEX and _STATIC_INDEX.exists():
                try:
                    html = _STATIC_INDEX.read_text(encoding="utf-8")
                    return HTMLResponse(html, media_type="text/html; charset=utf-8")
                except Exception as e:
                    logger.warning(f"[Gateway] 重讀 index.html 失敗: {e}")
            if _UI_HTML:
                return HTMLResponse(_UI_HTML, media_type="text/html; charset=utf-8")
            return DEMO_HTML

        @self.app.get("/health")
        async def health():
            return {
                "status": "ok",
                "connections": self.manager.count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        @self.app.get("/agents")
        async def list_agents():
            """
            Phase 2 動態 UI:回傳所有 enabled agent 的 metadata。

            同源:跟 _default_group_members 一樣讀 configs/default.yaml,
            確保 UI 顯示的數量 == fallback group_members 數量。

            Response: JSON array of {id, name, class, intimacy_level, enabled, persona_path}
            """
            return {"agents": _list_agents()}

        @self.app.post("/inject/tick")
        async def inject_tick(elapsed_mins: float = 35.0, time_period: str = "morning"):
            """手動觸發直接注入 SYSTEM_TICK 到 bus，測試 Heartbeat timing"""
            from src.eventbus.schema import EventPriority, EventType, SoulEvent
            tick = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="manual_inject",
                target="broadcast",
                priority=EventPriority.LOW,
                payload={
                    "tick_count": 999,
                    "elapsed_mins": elapsed_mins,
                    "time_period": time_period,
                    "vulnerability_window": False,
                    "silence_hours": round(elapsed_mins / 60.0, 2),
                    "attachment_heat": 0.3,
                    "chrono_block": "",
                },
            )
            await self.bus.publish(tick)
            # 等管線跑完（Agent → Intent → Token → LLM → Speak）
            await asyncio.sleep(6.0)
            return {"injected": True, "elapsed_mins": elapsed_mins}

        @self.app.get("/debug/broadcast")
        async def debug_broadcast():
            """直接送一條測試訊息繞過 LLM 管線"""
            from src.eventbus.schema import SoulEvent
            fake_event = SoulEvent(
                event_type=EventType.AGENT_SPEAK,
                source="agent_yua",
                target="broadcast",
                payload={
                    "agent_id": "agent_yua",
                    "text": "嗨你好。這是 Yua 冷泡茶模式。[MiniMax-style mock]",
                },
            )
            await self._on_agent_speak(fake_event)
            return {"broadcast": True}

        @self.app.get("/debug/emotion/{agent_id}")
        async def debug_emotion(agent_id: str):
            """Phase 3 情緒狀態查詢：回傳 mood / intimacy / updated_at"""
            from fastapi import HTTPException
            valid = {"agent_yua", "agent_ruka", "agent_akane"}
            if agent_id not in valid:
                raise HTTPException(status_code=404, detail=f"unknown agent_id: {agent_id}")
            mood, intimacy = emotion_engine.get(agent_id)
            # 取 updated_at
            cur = emotion_engine.conn.execute(
                "SELECT updated_at FROM agent_emotions WHERE agent_id = ?",
                (agent_id,),
            )
            row = cur.fetchone()
            updated_at = row[0] if row else None
            return {
                "agent_id": agent_id,
                "mood": round(mood, 3),
                "intimacy": round(intimacy, 2),
                "mood_description": emotion_engine.mood_description(mood),
                "updated_at": updated_at,
            }

        @self.app.get("/inject/yua")
        async def inject_yua():
            """直接讓 Yua 說句話（走真實 LLM）測試用"""
            try:
                from src.eventbus.schema import SoulEvent, EventPriority, EventType
                intent = SoulEvent(
                    event_type=EventType.AGENT_INTENT,
                    source="agent_yua",
                    target="broadcast",
                    priority=EventPriority.NORMAL,
                    payload={
                        "agent_id": "agent_yua",
                        "reason": "silence_timeout",
                        "draft": "還好你還在。",
                        "mode": "private",
                        "memory_query_hint": "",
                        "chrono_context": "",
                    },
                )
                await self.bus.publish(intent)
                # MiniMax takes ~2-3s; wait before returning so browser stays connected
                await asyncio.sleep(8.0)
                return {"yua_intent_fired": True}
            except Exception as e:
                import traceback
                return {"error": str(e), "trace": traceback.format_exc()}

        # ── /api/tts/audio endpoint (B 方案 2026-07-15 Bry 拍板) ──
        # FishTTSHandler 透過 TTSService 寫 mp3 到 data/tts/{agent_id}/{ts}.mp3
        # 這 endpoint 把 mp3 開放給 web/telegram 讀
        @self.app.get("/api/tts/audio/{agent_id}/{filename}")
        async def serve_tts_audio(agent_id: str, filename: str):
            """
            serve mp3 給前端/telegram client

            安全檢查:
              - agent_id 必須是已知角色（防 path traversal 列出整個磁碟）
              - filename 必須符合 ts 格式 + .mp3 結尾
            """
            from fastapi.responses import FileResponse
            from pathlib import Path
            import re

            # 防 path traversal
            if not re.match(r"^agent_[a-z_]+$", agent_id):
                raise HTTPException(status_code=400, detail="invalid agent_id")
            if not re.match(r"^\d{8}T\d{6}_\d{6}\.mp3$", filename):
                raise HTTPException(status_code=400, detail="invalid filename")

            mp3_path = data_root() / "tts" / agent_id / filename
            if not mp3_path.exists():
                raise HTTPException(status_code=404, detail="audio not found")
            return FileResponse(
                mp3_path,
                media_type="audio/mpeg",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        # ── /api/tts/recent endpoint ──
        # 前端可以 poll 拿最新 mp3（不需要 AGENT_AUDIO_READY 事件訂閱也能播）
        @self.app.get("/api/tts/recent")
        async def list_recent_tts(agent_id: str, limit: int = 1):
            """
            列出某 agent 最近 N 個 mp3 (新到舊)
            """
            from src.voice.tts_service import TTSService
            svc = TTSService(
                bus=None,  # list mode 不 emit
                output_dir=data_root() / "tts",
                public_url_prefix="/api/tts/audio",
            )
            results = svc.list_recent(agent_id=agent_id, limit=limit)
            return {"agent_id": agent_id, "count": len(results), "audios": results}

        # ── /api/tts endpoint — Edge TTS 即時合成 (Bry 拍板 2026-07-22 20:59) ──
        # JP rollback: Fish TTS 關了, 改 Edge TTS zh-CN-XiaoxiaoNeural 提供瀏覽器端中文語音
        # ?text= 直接合成, ?voice= 預設 zh-CN-XiaoxiaoNeural
        @self.app.get("/api/tts")
        async def edge_tts_synthesize(
            text: str,
            voice: str = "zh-CN-XiaoxiaoNeural",
            rate: str = "+0%",
            pitch: str = "+0Hz",
        ):
            """
            Edge TTS 即時合成 — 回傳 mp3 bytes

            Args:
                text: 要合成的文字
                voice: Edge TTS voice name, 預設 zh-CN-XiaoxiaoNeural
                rate: 語速, 預設 +0% (正常)
                pitch: 音調, 預設 +0Hz (正常)

            Returns:
                audio/mpeg (mp3)
            """
            from fastapi.responses import Response
            try:
                import edge_tts
            except ImportError:
                raise HTTPException(status_code=500, detail="edge-tts not installed")
            if not text or not text.strip():
                raise HTTPException(status_code=400, detail="text required")
            # Edge TTS 限制單次 text < 5000 chars
            if len(text) > 5000:
                text = text[:5000]
            try:
                communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
                mp3_bytes = b""
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        mp3_bytes += chunk["data"]
                if not mp3_bytes:
                    raise HTTPException(status_code=500, detail="Edge TTS returned empty audio")
                return Response(
                    content=mp3_bytes,
                    media_type="audio/mpeg",
                    headers={"Cache-Control": "public, max-age=3600"},
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"[Gateway] Edge TTS failed: {e}")
                raise HTTPException(status_code=500, detail=f"TTS failed: {e}")

        @self.app.get("/_admin/fast_forward")
        async def admin_fast_forward(minutes: float = 35.0):
            """
            觸發測試捷徑模擬 last_user_activity 往前設定
            不允許在正式環境（SOUL_ENV=production）使用
            """
            import os
            if os.getenv("SOUL_ENV", "dev") == "production":
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="disabled in production")
            heartbeat = getattr(self.app.state, "_heartbeat", None)
            if heartbeat is None:
                return {"error": "heartbeat not exposed on app.state"}
            from datetime import timedelta, datetime, timezone
            heartbeat.last_user_activity = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            return {"fast_forwarded": True, "minutes": minutes}

        @self.app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await self.manager.connect(ws)
            try:
                # 心跳：每 20s ping 一次確認連線存活
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.receive_text(), timeout=20.0)
                    except asyncio.TimeoutError:
                        await ws.send_text(json.dumps({"type": "ping"}))
                        continue

                    # 將 client 訊息轉發到 bus（client → bus）
                    try:
                        msg = json.loads(raw)
                        logger.info(f"[Gateway] WS raw msg: {str(msg)[:200]}")
                        if msg.get("type") == "USER_MESSAGE":
                            from src.eventbus.schema import SoulEvent, EventPriority, EventType
                            mode = msg.get("mode", "private")
                            # Bug 3 fix: 統一 group_members → participants，並確保預設全員
                            if mode == "group":
                                participants = msg.get("group_members") or msg.get("participants") or _default_group_members()
                                target = "broadcast"
                            else:
                                participants = None
                                target = msg.get("target_agent", "agent_yua")
                            # JP rollback (Bry 拍板 2026-07-22 20:59): Plan A 砍掉
                            # 不再 user 中文先翻日文, user_message 直接送 LLMProxy
                            # (LLM 跑中文 persona 會回中文, 跟 user 對話一致)
                            raw_content = msg.get("content", "")
                            translated_content = raw_content
                            logger.info(f"[Gateway] USER_MESSAGE mode={mode} target={target} participants={participants}")
                            # Bry §28 spec: WebSocket 事件加 session_id, 向 Telegram 格式看齊
                            # session_id = session_{user_id}_{full_agent_id}
                            # 跟 LLMProxy._session_key(agent_id, user_id) 對齊, 確保 history 連續
                            ws_user_id = msg.get("user_id", "anonymous")
                            ws_target = msg.get("target_agent", "agent_yua")
                            # 確保 full_agent_id 格式 (跟 router.py 一致)
                            ws_full_agent = ws_target if ws_target.startswith("agent_") else f"agent_{ws_target}"
                            ws_session_id = f"session_{ws_user_id}_{ws_full_agent}"
                            logger.info(f"[Gateway] WS session_id={ws_session_id} (user={ws_user_id}, agent={ws_full_agent})")
                            user_event = SoulEvent(
                                event_type=EventType.USER_MESSAGE,
                                source=ws_user_id,
                                target=ws_target,
                                priority=EventPriority.HIGH,
                                session_id=ws_session_id,
                                payload={
                                    "content": raw_content,                # JP rollback: 中文 user_message 直接送 LLM
                                    "text": raw_content,
                                    "user_id": ws_user_id,
                                    "target_user_id": ws_user_id,  # Bry §28 spec: 給 LLMProxy 讀
                                    "target_agent": ws_full_agent,
                                    "mode": mode,
                                    "participants": participants,
                                },
                            )
                            await self.bus.publish(user_event)
                            content_preview = str(msg.get("content", ""))[:30]
                            logger.info(f"[Gateway] USER_MESSAGE mode={mode} participants={participants}: {content_preview}")
                    except (json.JSONDecodeError, Exception) as e:
                        logger.warning("[Gateway] WS message parse error: " + str(e))
            except WebSocketDisconnect:
                self.manager.disconnect(ws)

        # 掛載靜態檔案目錄（頭像、CSS 等）
        if _STATIC_DIR is not None:
            try:
                self.app.mount("/avatars", StaticFiles(directory=str(_STATIC_DIR / "avatars")), name="avatars")
                self.app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
                logger.info(f"[Gateway] Static files mounted: /avatars, /static")
            except Exception as e:
                logger.warning(f"[Gateway] Failed to mount static files: {e}")


        @self.app.post("/memory/clear/group")
        async def clear_group_memory():
            """清除群組聊天的記憶"""
            import json as _json
            from pathlib import Path as _Path
            
            result = {"cleared": False, "file": ""}
            group_file = data_root() / "conversations" / "group_chat.json"
            if group_file.exists():
                try:
                    group_file.unlink()
                    result["cleared"] = True
                    result["file"] = str(group_file)
                except Exception as e:
                    result["error"] = str(e)
            
            logger.info(f"[Gateway] clear_group_memory result={result}")
            return result

        @self.app.post("/memory/clear/{agent_id}")
        async def clear_agent_memory(agent_id: str):
            """清除指定 Agent 的記憶（私人對話歷史）"""
            import json as _json
            from pathlib import Path as _Path
            
            result = {"agent_id": agent_id, "cleared": False, "files": []}
            
            # 清除私人對話歷史
            private_file = data_root() / "conversations" / f"bryan_{agent_id}_private.json"
            if private_file.exists():
                try:
                    private_file.unlink()
                    result["files"].append(str(private_file))
                    result["cleared"] = True
                except Exception as e:
                    result["error"] = str(e)
            
            # 清除群聊中該 Agent 的私人訊息標記
            group_file = data_root() / "conversations" / "group_chat.json"
            if group_file.exists():
                try:
                    history = _json.loads(group_file.read_text(encoding="utf-8"))
                    original_count = len(history)
                    # 移除該 agent 的私人訊息標記（is_private=True 且 speaker=agent_id）
                    history = [m for m in history if not (m.get("is_private") and m.get("speaker") == agent_id)]
                    if len(history) < original_count:
                        group_file.write_text(_json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
                        result["files"].append(str(group_file))
                        result["cleared"] = True
                except Exception as e:
                    result["error"] = str(e)
            
            logger.info(f"[Gateway] clear_memory agent={agent_id} result={result}")
            return result
    async def _on_agent_speak(self, event: SoulEvent):
        # hotfix #11 (2026-07-16 Bry 拍板):
        # proxy.py finally 區塊會補發 stub AGENT_SPEAK 觸發 consciousness._pending reset
        # stub 帶 is_stub=True,IOGateway 這裡要 skip 廣播 (避免 Bry 在 web 看到空訊息)
        if event.payload.get("is_stub"):
            logger.debug(
                f"[Gateway] stub AGENT_SPEAK, skip broadcast | "
                f"agent={event.payload.get('agent_id') or event.source} "
                f"reason={event.payload.get('stub_reason', 'unknown')}"
            )
            return

        ts = event.timestamp.isoformat() if hasattr(event, "timestamp") and event.timestamp else datetime.now(timezone.utc).isoformat()
        # Bry 拍板 2026-07-18 10:50: payload 內 text 已是整合後的 (proxy.py 整合)
        # 詳見 proxy.py L1854 整合邏輯 — 確保 IOGateway / ChannelRouter / Memory 三路都收到同份 text
        payload = {
            "type": "agent_speak",
            "agent_id": event.payload.get("agent_id", event.source),
            "text": event.payload.get("text", ""),
            "timestamp": ts,
            "session_id": getattr(event, "session_id", ""),
            # 階段 3+ (Bry 拍板 2026-07-15): 補上 audio_text + emotion
            # 之前只 broadcast text,client 端拿不到 TTS 素材
            # 補上後前端可以直接用 audio_text 播放 + 顯示 emotion 圖示
            # 整合後 audio_text 仍是純日文 (TTS 不念中文), Bry 拍板 2026-07-18
            "audio_text": event.payload.get("audio_text", ""),
            "emotion": event.payload.get("emotion", ""),
            # 方向 C (Bry 拍板 2026-07-17 20:15): 補上 translation
            # 之前 proxy.py L2014 寫進 event.payload 但 gateway 序列化時漏帶
            # WS / TG client 收到的 broadcasting 沒 translation, 整條日文+中文並列設計失效
            # 修法: event.payload.get("translation"), 預設 None (中文版角色 / 翻譯失敗)
            "translation": event.payload.get("translation"),
            # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
            # 把 AGENT_SPEAK 的 event_id 帶到 WS client,讓前端用 message_id
            # 把 audio 對應到「同一則 text」而不是「同一個 agent 的最新一筆」
            # backward compat: 沒 event_id 時 (理論上不會發生) 用 None
            "message_id": getattr(event, "event_id", None),
        }
        # Write to trace.log directly for debugging
        try:
            with open('trace.log', 'a', encoding='utf-8') as f:
                f.write('[GW] _on_agent_speak ENTER\n')
                f.write(f'[GW] self={self}\n')
                f.write(f'[GW] manager={getattr(self, "manager", None)}\n')
                f.write(f'[GW] payload={payload}\n')
                f.flush()
        except Exception as ex:
            pass
        logger.info("[Gateway] broadcasting: " + str(payload))
        try:
            if self.manager is None:
                logger.error('[Gateway] self.manager is None!')
            else:
                logger.info(f'[Gateway] calling broadcast, manager count={self.manager.count}')
                await self.manager.broadcast(payload)
                logger.info('[Gateway] broadcast completed')
                # Extra trace: verify broadcast actually ran
                with open('trace.log', 'a', encoding='utf-8') as f:
                    f.write(f'[AFT] broadcast done, manager._connections={len(self.manager._connections)}\n')
                    f.flush()
        except Exception as e:
            logger.error(f'[Gateway] broadcast error: {e}')
            with open('trace.log', 'a', encoding='utf-8') as f:
                f.write(f'[AFT] broadcast error: {e}\n')
                f.flush()
