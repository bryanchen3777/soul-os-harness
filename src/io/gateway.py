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
        self._setup_routes()

    def register(self):
        self.bus.subscribe(
            "io_gateway",
            self._on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )

    def _setup_routes(self):

        @self.app.get("/", response_class=HTMLResponse)
        async def root():
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

        # ── Phase 6.x TTS：msedge-tts endpoint for Live2D widget ──
        # widget.html 呼叫：fetch('/api/tts?voice=zh-TW-HsiaoChenNeural&text=...')
        # 預期回應：audio/mpeg (MP3) bytes
        @self.app.get("/api/tts")
        async def api_tts(voice: str = "", text: str = ""):
            """
            線上 TTS — 把文字轉 MP3 回傳給 Live2D widget 播放

            - voice 沒給 → 預設 zh-TW-HsiaoChenNeural
            - text 為空 / edge-tts 失敗 → 回 404（widget 自動 fallback 瀏覽器 TTS）
            - 5 分鐘 in-memory cache（避免 heartbeat 重複觸發同樣 draft）
            """
            from src.io.tts import synthesize_speech, DEFAULT_VOICE

            if not text or not text.strip():
                return {"error": "text is required"}, 400

            mp3 = await synthesize_speech(
                text=text,
                voice=voice or DEFAULT_VOICE,
            )
            if mp3 is None:
                # 故意回 404 + JSON（不是 500）— widget 端 404 走 fallback 路徑
                return {"error": "tts synthesis failed", "voice": voice or DEFAULT_VOICE}, 404

            # 回 MP3 stream
            from fastapi.responses import Response
            return Response(
                content=mp3,
                media_type="audio/mpeg",
                headers={
                    "Content-Length": str(len(mp3)),
                    "Cache-Control": "public, max-age=300",  # 配合 server 端 cache TTL
                },
            )
        # ── Phase 6.x TTS end ─────────────────────────────

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
                                participants = msg.get("group_members") or msg.get("participants") or ["agent_yua", "agent_ruka", "agent_akane"]
                                target = "broadcast"
                            else:
                                participants = None
                                target = msg.get("target_agent", "agent_yua")
                            logger.info(f"[Gateway] USER_MESSAGE mode={mode} target={target} participants={participants}")
                            user_event = SoulEvent(
                                event_type=EventType.USER_MESSAGE,
                                source=msg.get("user_id", "anonymous"),
                                target=target,
                                priority=EventPriority.HIGH,
                                payload={
                                    "content": msg.get("content", ""),
                                    "user_id": msg.get("user_id", "anonymous"),
                                    "target_agent": target,
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
            group_file = _Path("data/conversations/group_chat.json")
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
            private_file = _Path("data/conversations") / f"bryan_{agent_id}_private.json"
            if private_file.exists():
                try:
                    private_file.unlink()
                    result["files"].append(str(private_file))
                    result["cleared"] = True
                except Exception as e:
                    result["error"] = str(e)
            
            # 清除群聊中該 Agent 的私人訊息標記
            group_file = _Path("data/conversations/group_chat.json")
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
        ts = event.timestamp.isoformat() if hasattr(event, "timestamp") and event.timestamp else datetime.now(timezone.utc).isoformat()
        payload = {
            "type": "agent_speak",
            "agent_id": event.payload.get("agent_id", event.source),
            "text": event.payload.get("text", ""),
            "timestamp": ts,
            "session_id": getattr(event, "session_id", ""),
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
