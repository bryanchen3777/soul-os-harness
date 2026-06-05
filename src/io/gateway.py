"""
src/io/gateway.py
Soul OS — Phase 4 I/O Gateway：WebSocket 廣播 AGENT_SPEAK 給前端
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent

logger = logging.getLogger("soul_os.gateway")


# ── 靜態前端（開發用，直接在瀏覽器看）──
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
        logger.info(f"[Gateway] 新連線，目前 {len(self._connections)} 個客戶端")

    def disconnect(self, ws: WebSocket):
        self._connections.discard(ws)
        logger.info(f"[Gateway] 斷線，剩 {len(self._connections)} 個客戶端")

    async def broadcast(self, payload: dict):
        if not self._connections:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        dead = set()
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)


class IOGateway:
    """
    訂閱 Event Bus 的 AGENT_SPEAK，
    把每條訊息廣播給所有 WebSocket 客戶端。
    """

    def __init__(self, bus: SoulEventBus):
        self.bus = bus
        self.manager = ConnectionManager()
        self.app = FastAPI(title="Soul OS Gateway")
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
            """開發用：直接注入 SYSTEM_TICK 到 bus，繞過 Heartbeat timing"""
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
            # 等待鏈路完成（Agent → Intent → Token → LLM → Speak）
            await asyncio.sleep(6.0)
            return {"injected": True, "elapsed_mins": elapsed_mins}

        @self.app.get("/debug/broadcast")
        async def debug_broadcast():
            """直接廣播一條測試訊息，繞過 LLM 鏈路"""
            from src.eventbus.schema import SoulEvent
            fake_event = SoulEvent(
                event_type=EventType.AGENT_SPEAK,
                source="agent_yua",
                target="broadcast",
                payload={"agent_id": "agent_yua", "text": "還好你還在。（Yua 冷泡茶模式）"},
            )
            await self._on_agent_speak(fake_event)
            return {"broadcast": True}

        @self.app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await self.manager.connect(ws)
            try:
                # 心跳：每 20s ping 一次，確認連線存活
                while True:
                    try:
                        await asyncio.wait_for(ws.receive_text(), timeout=20.0)
                    except asyncio.TimeoutError:
                        await ws.send_text(json.dumps({"type": "ping"}))
            except WebSocketDisconnect:
                self.manager.disconnect(ws)

    async def _on_agent_speak(self, event: SoulEvent):
        ts = event.timestamp.isoformat() if hasattr(event, "timestamp") and event.timestamp else datetime.now(timezone.utc).isoformat()
        payload = {
            "type": "agent_speak",
            "agent_id": event.payload.get("agent_id", event.source),
            "text": event.payload.get("text", ""),
            "timestamp": ts,
            "session_id": getattr(event, "session_id", ""),
        }
        logger.info(f"[Gateway] broadcasting: {payload}")
        await self.manager.broadcast(payload)