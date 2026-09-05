"""
src/soul/mcp_stdio_client.py — TS-3 真實 MCP Server stdio client（IMPLEMENTATION）

定位（工單 TS-3：把 TS-2 tool_registry 從 Mock/Stub 推進到真實 MCP Server
端到端驗證）：

  - 用官方 Python MCP SDK（``mcp`` 2.x）的 ``stdio_client`` + ``ClientSession``
    實現**真實 stdio 子進程通訊**：進程啟動 → initialize 握手 → tools/list →
    tools/call → 關閉清理，全部是真實 MCP 協議。
  - 對外暴露 ToolRegistry 需要的 duck-type（``list_tools()`` / ``call_tool()``），
    是 ``tool_registry.MCPClient`` Protocol 的真實實現——**tool_registry.py 0 改動**。
  - 自帶 ``close()``：終止子進程、關閉 session/streams（進程清理，驗證「stdio
    進程啟動/握手/通訊/清理」）。
  - 可選 ``start_checker``：在 ``call_tool`` 前後用 callback 掛鉤觀察（測試用
    於驗證超時/斷線時主心跳不被阻塞）。

Frozen contract 邊界（0 change）：
  - 不改 tool_registry.py / actuator.py 的既有接口與語義（僅新增本适配器）。
  - 不 import ``src/work/roles.py``；不持有 LLM / EventBus / SpeakerToken。

安全邊界：
  - 由呼叫方（ToolRegistry / 測試）決定 spawn 哪個 server 進程；本模組只負責
    通訊與清理，不做任何檔案/網路副作用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.soul.mcp_stdio_client")

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import CallToolResult, TextContent
except ImportError:  # pragma: no cover
    ClientSession = None  # type: ignore
    StdioServerParameters = None  # type: ignore
    stdio_client = None  # type: ignore


# callback: (階段, 上下文 dict) → 可選 async。測試觀察掛鉤。
StartChecker = Callable[[str, Dict[str, Any]], Awaitable[None]]


class MCPStdioClientAdapter:
    """真實 MCP stdio client（ToolRegistry 可用的 duck-type 實現）。

    用法:
        client = MCPStdioClientAdapter(command="python", args=["-m", "..."], env={...})
        await client.connect()                      # 啟動進程 + initialize 握手
        tools = await client.list_tools()           # → list[Tool]（pydantic）
        result = await client.call_tool("weather", {"city": "Taipei"})
        await client.close()                        # 清理子進程

    與 ToolRegistry 的契約（``MCPClient`` Protocol）完全相容：
        - ``list_tools()`` → list 或 {"tools": [...]}（本實作回 list[Tool]）
        - ``call_tool(name, args)`` → 任意結構化結果（本實作轉成 dict / str）
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        *,
        start_checker: Optional[StartChecker] = None,
        transport_timeout: float = 10.0,
    ) -> None:
        if stdio_client is None:  # pragma: no cover
            raise RuntimeError("mcp SDK 未安裝（pip install mcp）")
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self._start_checker = start_checker
        self._transport_timeout = transport_timeout

        self._stack = AsyncExitStack()
        self._session: Optional[ClientSession] = None
        self._read: Any = None
        self._write: Any = None
        self._connected = False

    # ── 生命週期（啟動 / 清理）────────────────────────────

    async def connect(self) -> None:
        """啟動 MCP server 子進程並完成 initialize 握手（stdio transport）。"""
        if self._connected:
            return
        params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=dict(os.environ) if self._env is None else {**os.environ, **self._env},
            cwd=self._cwd,
        )
        if self._start_checker:
            await self._start_checker("spawning", {"command": self._command, "args": self._args})

        # stdio_client 是 async generator：換行分隔的 JSON-RPC over stdio
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._read, self._write = read, write
        session = await self._stack.enter_async_context(ClientSession(read, write))
        self._session = session
        await asyncio.wait_for(session.initialize(), timeout=self._transport_timeout)
        self._connected = True
        if self._start_checker:
            await self._start_checker("initialized", {
                "server_info": str(getattr(session, "server_info", "")),
                "protocol_version": str(getattr(session, "protocol_version", "")),
            })
        logger.info(
            "[MCPStdioClient] connected command=%s args=%s",
            self._command, self._args,
        )

    async def close(self) -> None:
        """關閉 session/streams 並終止子進程（進程清理，無殘留）。"""
        if not self._connected:
            return
        try:
            await self._stack.aclose()
        finally:
            self._connected = False
            self._session = None
            self._read = None
            self._write = None
        logger.info("[MCPStdioClient] closed")

    # ── ToolRegistry duck-type ─────────────────────────────

    async def list_tools(self) -> Any:
        """tools/list — 返回 list[Tool]（pydantic；ToolRegistry 的 getattr 抽取相容）。"""
        assert self._session is not None, "not connected"
        result = await self._session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """tools/call — 返回結構化結果（優先 structuredContent，其次 text）。"""
        assert self._session is not None, "not connected"
        if self._start_checker:
            await self._start_checker("calling", {"tool": name, "arguments": arguments})
        raw: CallToolResult = await self._session.call_tool(name, arguments)
        if self._start_checker:
            await self._start_checker("called", {"tool": name})
        return _extract_call_result(raw)

    # ── 內部狀態（測試/診斷）──────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def session(self) -> Optional[ClientSession]:
        return self._session


def _extract_call_result(raw: CallToolResult) -> Any:
    """把 mcp CallToolResult 轉成 ToolRegistry 可存入 ToolResult.data 的結構。

    - 優先 ``structured_content``（dict）——保留結構化資料。
    - 否則把 content 文字串接成 {"text": "..."}（或純 str）。
    - is_error → 拋 RuntimeError（由 ToolRegistry 降級路徑處理）。
    """
    if getattr(raw, "is_error", False):
        texts = _text_content(raw)
        raise RuntimeError(f"MCP tool error: {texts or 'unknown'}")
    structured = getattr(raw, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    texts = _text_content(raw)
    if len(texts) == 1:
        return {"text": texts[0]}
    if texts:
        return {"text": "\n".join(texts)}
    return {"text": ""}


def _text_content(raw: CallToolResult) -> List[str]:
    out: List[str] = []
    for block in getattr(raw, "content", []) or []:
        if isinstance(block, TextContent):
            out.append(block.text)
        elif isinstance(block, dict):
            t = block.get("text")
            if isinstance(t, str):
                out.append(t)
    return out


# ───────────────────────────────────────────────────────────
# RawStdioMCPClient — 手寫 stdio JSON-RPC client
# ───────────────────────────────────────────────────────────

# MCP stdio transport：每行一條 JSON-RPC 訊息（newline-delimited）。
# 手寫實現（工單 TS-3 允許），自管子進程 → 可取得 proc 句柄做斷線/超時實驗。
_DEFAULT_PROTOCOL_VERSION = "2024-11-05"


class RawStdioMCPClient:
    """手寫 stdio JSON-RPC client（真實 MCP 協議，自管進程生命周期）。

    優勢 vs SDK adapter：
      - 直接持有 ``self.proc``（asyncio subprocess）——斷線測試可 kill 進程；
      - 全手動 JSON-RPC（initialize / notifications/initialized / tools/list /
        tools/call），協議真實且可檢查。
    同時保持 ToolRegistry duck-type（list_tools / call_tool）。
    """

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        *,
        protocol_version: str = _DEFAULT_PROTOCOL_VERSION,
        response_timeout: float = 10.0,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = env
        self._cwd = cwd
        self._protocol_version = protocol_version
        self._response_timeout = response_timeout

        self.proc: Any = None          # asyncio.subprocess.Process（測試可 kill）
        self._seq = 0
        self._initialized = False
        self.negotiated_version: Optional[str] = None
        self.server_info: Optional[Dict[str, Any]] = None

    # ── 生命週期 ─────────────────────────────────────────

    async def connect(self) -> None:
        """spawn 子進程 → initialize 握手 → notifications/initialized。"""
        env = dict(os.environ) if self._env is None else {**os.environ, **self._env}
        self.proc = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=self._cwd,
        )
        resp = await self._request("initialize", {
            "protocolVersion": self._protocol_version,
            "capabilities": {},
            "clientInfo": {"name": "soul-os-ts3", "version": "0.1.0"},
        })
        result = resp.get("result", {})
        self.negotiated_version = result.get("protocolVersion")
        self.server_info = result.get("serverInfo")
        await self._notify("notifications/initialized")
        self._initialized = True

    async def close(self) -> None:
        """終止子進程（先 stdin EOF 優雅關閉，再 terminate/kill 兜底）。"""
        if self.proc is None:
            self._initialized = False
            return
        try:
            if self.proc.stdin and self.proc.returncode is None:
                self.proc.stdin.close()
        except Exception:
            pass
        if self.proc.returncode is None:
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, Exception):
                try:
                    self.proc.terminate()
                    await asyncio.wait_for(self.proc.wait(), timeout=3.0)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
        self._close_pipes()
        self._initialized = False

    async def kill(self) -> None:
        """強殺子進程（斷線實驗用）。"""
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
        self._close_pipes()
        self._initialized = False

    def _close_pipes(self) -> None:
        """關閉 stdin/stdout/stderr 管道（避免 event loop 清理警告）。"""
        if self.proc is None:
            return
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if pipe is None:
                continue
            try:
                pipe.close()
            except Exception:
                pass

    @property
    def connected(self) -> bool:
        return self._initialized and self.proc is not None and self.proc.returncode is None

    # ── ToolRegistry duck-type ────────────────────────────

    async def list_tools(self) -> Any:
        """tools/list → list[dict]（name/description/inputSchema）。"""
        resp = await self._request("tools/list", {})
        return resp.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """tools/call → 結構化結果（structuredContent 優先，其次 text）。"""
        resp = await self._request("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        result = resp.get("result", {})
        if result.get("isError"):
            texts = _raw_text_content(result.get("content") or [])
            raise RuntimeError(f"MCP tool error: {texts or 'unknown'}")
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            return sc
        texts = _raw_text_content(result.get("content") or [])
        return {"text": "\n".join(texts)} if texts else {"text": ""}

    # ── 內部：JSON-RPC ────────────────────────────────────

    async def _request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        assert self.proc is not None and self.proc.returncode is None, "process not alive"
        self._seq += 1
        msg = {
            "jsonrpc": "2.0",
            "id": self._seq,
            "method": method,
            "params": params,
        }
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        assert self.proc.stdin is not None
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()
        assert self.proc.stdout is not None
        raw = await asyncio.wait_for(
            self.proc.stdout.readline(), timeout=self._response_timeout
        )
        if not raw:
            raise ConnectionError("MCP server stdout closed (process died?)")
        decoded = json.loads(raw.decode("utf-8"))
        if decoded.get("error"):
            raise RuntimeError(f"MCP JSON-RPC error: {decoded['error']}")
        return decoded

    async def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        line = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(line)
        await self.proc.stdin.drain()


def _raw_text_content(blocks: List[Any]) -> List[str]:
    out: List[str] = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            t = b.get("text")
            if isinstance(t, str):
                out.append(t)
    return out


__all__ = ["MCPStdioClientAdapter", "RawStdioMCPClient", "StartChecker"]