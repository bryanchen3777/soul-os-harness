"""
scripts/mcp_fixture_server.py — TS-3 真实 MCP Server fixture（生产级 stdio MCP Server）

用途（工单 TS-3：真实 MCP Server 端到端接入验证）：
  用官方 Python MCP SDK（mcp 2.x ``MCPServer``）实现的**真实 MCP Server**，
  走标准 MCP 协议 stdio transport（initialize 握手 → tools/list → tools/call）。
  作为 ToolRegistry 端到端验证的对端进程，工具刻意对齐 tool_registry 的
  §2.3 显式映射表，覆盖三大能力组 + 权限分级两端：

    - ``weather``            → observe_environment / auto_approved
    - ``time``               → observe_environment / auto_approved
    - ``search``             → observe_environment / auto_approved（delay_seconds>0
                              故意慢速——用於 5s 硬超時 + Fail-closed 降級測試）
    - ``memory_search``      → reflect_memory     / auto_approved
    - ``message_send``       → communicate        / ask_required
    - ``unclassifiable_op``  → 無法歸類工具（fail-closed 拒絕註冊實證）

安全边界：
  - 純本地計算（時間/記憶 stub/白名單目錄列舉），0 網路請求、0 外部副作用。
  - message_send 只寫入 server 自身 stdout（不發真實訊息），供權限分級驗證。
  - search 的慢速 sleep 上限 30s，可被 client 硬超時切斷。

啟動（由測試端以子進程方式拉起）：
    <venv>/Scripts/python.exe scripts/mcp_fixture_server.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server.mcpserver import MCPServer
    from mcp.server.models import InitializationOptions
except ImportError:  # pragma: no cover
    sys.stderr.write("mcp SDK 未安裝（pip install mcp）\n")
    raise

# ───────────────────────────────────────────────────────────
# 工具實現
# ───────────────────────────────────────────────────────────

_sent_messages: List[Dict[str, str]] = []


def _tool_weather(city: str = "Taipei") -> Dict[str, Any]:
    """天氣查詢（唯讀感知，auto_approved 對照）。"""
    return {
        "city": city,
        "temperature": 24,
        "conditions": "sunny",
        "humidity": 60,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _tool_time(timezone_name: str = "UTC") -> Dict[str, Any]:
    """當前時間（唯讀感知，auto_approved 對照）。"""
    return {
        "timezone": timezone_name,
        "now": datetime.now(timezone.utc).isoformat(),
    }


def _tool_memory_search(query: str = "") -> Dict[str, Any]:
    """記憶檢索（唯讀認知，reflect_memory 對照）。"""
    return {
        "query": query,
        "results": [
            {"note_id": "n1", "summary": f"與 {query or '日常'} 相關的記憶片段"},
            {"note_id": "n2", "summary": "過去某天的天氣很好"},
        ],
    }


def _tool_message_send(to: str, text: str) -> Dict[str, Any]:
    """發送訊息（敏感變更，ask_required 對照——只記錄不真發）。"""
    _sent_messages.append({"to": to, "text": text})
    return {"sent": True, "to": to, "text_length": len(text)}


def _tool_slow_weather(delay_seconds: float = 10.0) -> Dict[str, Any]:
    """故意慢速的查詢（search 工具底層）——用於 5s 硬超時 + Fail-closed 降級測試。"""
    import time as _time
    _time.sleep(min(max(delay_seconds, 0.1), 30.0))
    return {
        "query": "slow",
        "hits": 0,
        "note": "slow-response",
    }


def _tool_unclassifiable_op(code: str = "x") -> Dict[str, Any]:
    """無法歸類工具——驗證 §2.3 優先級 3 fail-closed 拒絕註冊。"""
    return {"op": code, "result": "internal"}


def _tool_list_directory(path: str = ".") -> Dict[str, Any]:
    """白名單目錄列舉（唯讀；根目錄被 ABSPATH 白名單限制）。"""
    allowed = {str(Path(__file__).resolve().parent)}
    target = Path(path).resolve()
    base = str(Path(__file__).resolve().parent)
    if not str(target).startswith(base):
        raise ValueError(f"path outside whitelist: {path}")
    names = [p.name for p in target.iterdir()][:20]
    return {"path": str(target), "entries": names}


# ───────────────────────────────────────────────────────────
# MCP Server 組裝（mcp 2.x MCPServer API）
# ───────────────────────────────────────────────────────────

def build_server() -> MCPServer:
    server = MCPServer(
        "soul-os-ts3-fixture",
        instructions="TS-3 真實 MCP Server fixture：純本地、唯讀優先。",
    )

    @server.tool()
    async def weather(city: str = "Taipei") -> Dict[str, Any]:
        """查詢指定城市的當前天氣（唯讀感知）。"""
        return _tool_weather(city)

    @server.tool()
    async def time(timezone_name: str = "UTC") -> Dict[str, Any]:
        """獲取指定時區的當前時間（唯讀感知）。"""
        return _tool_time(timezone_name)

    @server.tool()
    async def memory_search(query: str = "") -> Dict[str, Any]:
        """檢索記憶筆記（唯讀認知）。"""
        return _tool_memory_search(query)

    @server.tool()
    async def message_send(to: str, text: str) -> Dict[str, Any]:
        """發送一則訊息給接收者（敏感變更，需 Ask 確認）。"""
        return _tool_message_send(to, text)

    @server.tool()
    async def search(query: str = "", delay_seconds: float = 0.0) -> Dict[str, Any]:
        """網路搜尋（唯讀感知；delay_seconds>0 時故意慢速——用於超時降級驗證）。"""
        if delay_seconds > 0:
            return _tool_slow_weather(delay_seconds)
        return {
            "query": query,
            "hits": [
                {"title": f"關於 {query} 的搜尋結果一", "url": "https://example.com/1"},
                {"title": f"關於 {query} 的搜尋結果二", "url": "https://example.com/2"},
            ],
        }

    @server.tool()
    async def unclassifiable_op(code: str = "x") -> Dict[str, Any]:
        """內部操作（無法歸類 → 應被 registry 拒絕註冊）。"""
        return _tool_unclassifiable_op(code)

    return server


def main() -> None:
    server = build_server()
    # stdio transport：由父進程（測試端 ToolRegistry client）拉起並通信
    server.run(transport="stdio")


if __name__ == "__main__":
    main()