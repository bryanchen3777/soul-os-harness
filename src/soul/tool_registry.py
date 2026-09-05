"""
src/soul/tool_registry.py — Soul OS TS-2 Tooling & MCP Contract（IMPLEMENTATION）

定位（照 docs/TOOLING-MCP-CONTRACT.md §2，TS-1 設計已鎖定）：
  獨立動態註冊表——MCP Tool 動態註冊 + 健康檢查 + 自動歸類至
  observe_environment / communicate / reflect_memory 三大能力組；
  ``capability.py`` 0 改動（``CAPABILITY_DEFINITIONS`` 保持靜態，投影時合併）。

四大鎖定契約（TS-2 工單關鍵決策）：
  1. **唯一入口**：``register_mcp_server`` 是唯一註冊入口；``unregister_mcp_server``
     是唯一註銷入口。``call`` 是 Actuator 調用工具的唯一入口——工具調用不繞過
     registry 直接觸達 MCP client（健康檢查 + 權限分級 + 降級統一生效）。
  2. **自動歸類三級規則**（§2.3）：顯式映射表 > 語義關鍵詞兜底 > 無法歸類拒絕註冊
     （fail-closed，無法歸類的工具無法確定權限語義，不能讓它悄悄進入能力組）。
  3. **健康檢查三態**（§2.4）：healthy / degraded / offline。offline → 該 server
     工具不投影（fail-silent）。**Decision 只看 3 個能力組，不看 N 個工具**。
  4. **5s 硬超時 + Fail-closed 平滑降級**（§4.2/§4.3）：外部 MCP 斷線/超時/異常 →
     降級至空結果或預設快取（帶 staleness 標註），絕不 crash、絕不自動重試風暴
     （預設 0 自動重試）、絕不阻塞主心跳（硬超時 + asyncio 協作）。

權限分級（§4.1）：
  - explicit 映射表含權限類：唯讀感知類工具 → ``auto_approved``；
    敏感變更類工具 → ``ask_required``。
  - 語義關鍵詞兜底 → 預設 ``ask_required``（無法確認唯讀性一律按敏感處理，
    fail-closed）。
  - ``ask_required`` 工具經 Ask 守門 stub（v1）：預設 stub 一律拒絕
    （Ask 未接通 → 不執行 → ``permission_denied``，等同 do_nothing）。

Frozen contract 邊界（0 change）：
  - 不 import ``src/work/roles.py``（DSH ROLE_CAPABILITIES 隔離，CA-1 Q7 死規則）。
  - 不改 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers /
    SAGE / EventBus / capability.py / decision.py / motive.py / scheduler.py。
  - 註冊表不持有 LLM / EventBus / SpeakerToken 引用（與 WorldEventSource ABC
    同款隔離：工具執行器無權 publish AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER）。

可觀測性：註冊/註銷/健康狀態變化/降級/權限拒絕記錄進自有 sidecar
``data/soul/tool_registry_trace.jsonl``（append-only，獨立 schema，
與 ``capability_projection_trace.jsonl`` 模式對齊），並打 logger。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger("soul_os.soul.tool_registry")

# ───────────────────────────────────────────────────────────
# 常量（TS-2 工單鎖定）
# ───────────────────────────────────────────────────────────

# 三大能力組（與 capability.py CAPABILITY_DEFINITIONS 對齊）
CAPABILITY_GROUP_OBSERVE = "observe_environment"
CAPABILITY_GROUP_COMMUNICATE = "communicate"
CAPABILITY_GROUP_REFLECT = "reflect_memory"
CAPABILITY_GROUPS: tuple = (
    CAPABILITY_GROUP_OBSERVE,
    CAPABILITY_GROUP_COMMUNICATE,
    CAPABILITY_GROUP_REFLECT,
)

# 健康三態（§2.4）
HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_OFFLINE = "offline"
HEALTH_STATES = (HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_OFFLINE)

# 權限分級（§4.1）
PERM_AUTO_APPROVED = "auto_approved"
PERM_ASK_REQUIRED = "ask_required"
PERM_CLASSES = (PERM_AUTO_APPROVED, PERM_ASK_REQUIRED)

# 硬超時（§4.3：工具調用是感知/認知路徑，超時必須遠小於主心跳週期）
DEFAULT_CALL_TIMEOUT_SECONDS = 5.0
DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS = 3.0
# 連續超時/異常達閾值 → offline（§2.4，預設 2 次，可配置）
DEFAULT_OFFLINE_AFTER_CONSECUTIVE_FAILURES = 2
# 預設 0 自動重試（§3.2 防重試風暴；重試需顯式配置且帶退避）
DEFAULT_MAX_AUTO_RETRIES = 0

# 自有 sidecar 文件名（掛在 data_root()/soul/ 下，與 capability trace 模式對齊）
TRACE_FILENAME = "tool_registry_trace.jsonl"


# ───────────────────────────────────────────────────────────
# §2.3 自動歸類：顯式映射表（canonical，TS-2 維護）
# ───────────────────────────────────────────────────────────

# 已知工具名 → 能力組（優先級 1）。
# v1 種子表照 TOOLING-MCP-CONTRACT §2.3 原文：
#   weather / calendar / news / web_search / time / search → observe_environment
#   message_send / telegram_send / dm_send → communicate
#   memory_search / diary_read / memory_retrieve → reflect_memory
EXPLICIT_GROUP_MAP: Dict[str, str] = {
    "weather": CAPABILITY_GROUP_OBSERVE,
    "calendar": CAPABILITY_GROUP_OBSERVE,
    "news": CAPABILITY_GROUP_OBSERVE,
    "web_search": CAPABILITY_GROUP_OBSERVE,
    "time": CAPABILITY_GROUP_OBSERVE,
    "search": CAPABILITY_GROUP_OBSERVE,
    "message_send": CAPABILITY_GROUP_COMMUNICATE,
    "telegram_send": CAPABILITY_GROUP_COMMUNICATE,
    "dm_send": CAPABILITY_GROUP_COMMUNICATE,
    "memory_search": CAPABILITY_GROUP_REFLECT,
    "diary_read": CAPABILITY_GROUP_REFLECT,
    "memory_retrieve": CAPABILITY_GROUP_REFLECT,
    # MS-1 D3（MS-2 落地）：多模态感知工具 → observe_environment（additive，
    # 既有 12 项 0 改动；audio-stream-mcp / camera-mcp 的 tool schema 描述
    # 若未命中此表，也會被 §2.3 語義兜底攔住，見 _OBSERVE_KEYWORDS）
    "mic_listen": CAPABILITY_GROUP_OBSERVE,
    "audio_transcribe": CAPABILITY_GROUP_OBSERVE,
    "stt": CAPABILITY_GROUP_OBSERVE,
    "camera_capture": CAPABILITY_GROUP_OBSERVE,
    "camera_snapshot": CAPABILITY_GROUP_OBSERVE,
    "image_capture": CAPABILITY_GROUP_OBSERVE,
}

# 顯式映射表含權限類（§4.1.1）：唯讀感知類 → auto_approved；
# 敏感變更類（可對外產生副作用）→ ask_required。
EXPLICIT_PERMISSION_MAP: Dict[str, str] = {
    "weather": PERM_AUTO_APPROVED,
    "calendar": PERM_AUTO_APPROVED,
    "news": PERM_AUTO_APPROVED,
    "web_search": PERM_AUTO_APPROVED,
    "time": PERM_AUTO_APPROVED,
    "search": PERM_AUTO_APPROVED,
    "memory_search": PERM_AUTO_APPROVED,
    "diary_read": PERM_AUTO_APPROVED,
    "memory_retrieve": PERM_AUTO_APPROVED,
    "message_send": PERM_ASK_REQUIRED,
    "telegram_send": PERM_ASK_REQUIRED,
    "dm_send": PERM_ASK_REQUIRED,
    # MS-1 D5（MS-2 落地）：mic/STT 唯讀感知 → auto_approved（與 weather/news
    # 同語義，家庭環境收音不產生外部副作用）；camera 隱私敏感（可能捕捉私密
    # 畫面）→ ask_required（additive，既有 12 項 0 改动）
    "mic_listen": PERM_AUTO_APPROVED,
    "audio_transcribe": PERM_AUTO_APPROVED,
    "stt": PERM_AUTO_APPROVED,
    "camera_capture": PERM_ASK_REQUIRED,
    "camera_snapshot": PERM_ASK_REQUIRED,
    "image_capture": PERM_ASK_REQUIRED,
}


# §2.3 語義關鍵詞兜底（優先級 2，description 匹配，大小寫不敏感）。
# 優先級順序：reflect > communicate > observe——讓「memory search」這類含
# 多組關鍵詞的描述落到最特異的記憶類，避免歧義誤歸類。
_OBSERVE_KEYWORDS = (
    "weather", "calendar", "news", "search", "time",
    "查询", "天氣", "天气", "日历", "日曆", "新闻", "新聞", "搜索", "时间", "時間",
    # MS-1 D3（MS-2 落地）：多模态关键词（description 语义兜底，大小写不敏感）。
    # additive：既有 12 词 0 改动；未入显式表的多模态工具靠这些词归入 observe。
    # 注：substring 匹配，故意用短词覆盖变体（listen/transcri/stt），
    # 避免 audio_transcribe 因描述用词变体漏网。
    "audio", "voice", "speech", "speak", "camera", "image", "vision", "stt",
    "麦克风", "麥克風", "语音", "語音", "声音", "聲音", "说话", "說話",
    "相机", "相機", "摄像头", "攝像頭", "画面", "畫面", "图像", "圖像",
    "listen", "transcri",
)
_COMMUNICATE_KEYWORDS = (
    "send", "message", "notify",
    "发送", "發送", "消息", "通知",
)
_REFLECT_KEYWORDS = (
    "memory", "diary", "recall",
    "记忆", "記憶", "日记", "日記", "回顾", "回顧",
)


def _classify_by_semantic_keywords(name: str, description: str) -> Optional[str]:
    """語義關鍵詞兜底：description（含工具名）命中最特異關鍵詞組 → 能力組。

    全部未命中 → None（進入優先級 3：無法歸類拒絕註冊）。
    """
    haystack = f"{name} {description}".lower()
    for kw in _REFLECT_KEYWORDS:
        if kw.lower() in haystack:
            return CAPABILITY_GROUP_REFLECT
    for kw in _COMMUNICATE_KEYWORDS:
        if kw.lower() in haystack:
            return CAPABILITY_GROUP_COMMUNICATE
    for kw in _OBSERVE_KEYWORDS:
        if kw.lower() in haystack:
            return CAPABILITY_GROUP_OBSERVE
    return None


def classify_tool(name: str, description: str) -> Optional[str]:
    """自動歸類三級規則（§2.3，註冊時一次性完成，運行中不重歸類）。

    優先級：
      1. 顯式映射表（canonical）
      2. 語義關鍵詞兜底（description 匹配）
      3. 均未命中 → None（fail-closed：拒絕註冊）

    Returns:
        能力組 id 或 None（無法歸類）。
    """
    if name in EXPLICIT_GROUP_MAP:
        return EXPLICIT_GROUP_MAP[name]
    return _classify_by_semantic_keywords(name, description)


def permission_class_for(name: str, group: Optional[str]) -> str:
    """權限分級（§4.1.1）：

    - 顯式映射表含權限類（唯讀感知 → auto_approved；發送/副作用 → ask_required）。
    - 語義兜底（或無法確定）→ 一律 ask_required（無法確認唯讀性 = 敏感，fail-closed）。
    """
    if name in EXPLICIT_PERMISSION_MAP:
        return EXPLICIT_PERMISSION_MAP[name]
    return PERM_ASK_REQUIRED


# ───────────────────────────────────────────────────────────
# 資料結構（§2.2，照設計示意）
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegisteredTool:
    """單一已註冊工具（註冊時歸類完成後不可變）。"""
    tool_id: str            # 唯一身份：f"{server_id}:{name}"（複合，防跨 server 衝突）
    server_id: str          # 所屬 MCP server 的 id
    name: str               # MCP tools/list 返回的工具名
    description: str        # 工具描述（來自 MCP schema，用於自動歸類）
    input_schema: dict      # MCP JSON Schema（tools/call 參數校驗用）
    capability_group: str   # 自動歸類結果：observe_environment | communicate | reflect_memory
    permission_class: str   # 權限分級：auto_approved | ask_required（§4.1）
    health: str = HEALTH_HEALTHY  # 快照用；運行期健康狀態以 server 級為準（§2.4）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "server_id": self.server_id,
            "name": self.name,
            "description": self.description,
            "capability_group": self.capability_group,
            "permission_class": self.permission_class,
            "health": self.health,
        }


@dataclass(frozen=True)
class ToolResult:
    """單次工具調用結果（§2.2 / §4.2.1）。"""
    ok: bool                                             # 調用是否成功
    data: Any = None                                     # 成功時結構化結果（observe → 感知資料；reflect → 記憶摘要）
    error: Optional[str] = None                          # 失敗原因（超時 / 斷線 / 異常 / 權限拒絕）
    degraded: bool = False                               # 是否來自降級路徑（空結果 / 預設快取）
    cached: bool = False                                 # 是否來自預設快取（staleness 由調用方標註）


class MCPClient(Protocol):
    """MCP server client 的鴨子型別介面（TS-3 接真實 MCP SDK 前的最小契約）。

    約定 async（MCP Python SDK 的 Session.call_tool / list_tools 皆 async）：
      - ``list_tools()`` → ``{"tools": [{name, description, inputSchema}]}``
        或 tools 陣列（兩種都相容）。
      - ``call_tool(name, arguments)`` → 任意結構化結果（工具自定義）。
    """
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict) -> Any: ...


class AskGate(Protocol):
    """Ask 守門（§4.1.2）：v1 stub 介面，TS-3 可換成真實確認 UI。"""
    def approve(self, tool: RegisteredTool, args: dict) -> bool: ...


class RejectingAskGate:
    """v1 預設 Ask 守門 stub：Ask 未接通 → 一律拒絕（fail-closed）。

    語意：ask_required 工具需要 Bryan 確認；v1 沒有真實確認 UI，
    未確認 = 不執行（§4.1.3，「Ask 被拒 / 超時未確認 → 等同 do_nothing」）。
    """

    def approve(self, tool: RegisteredTool, args: dict) -> bool:
        logger.warning(
            "[ToolRegistry] ask_required 工具被拒（v1 Ask stub 未接通 = fail-closed）: "
            f"tool_id={tool.tool_id}"
        )
        return False


# ───────────────────────────────────────────────────────────
# 側車 trace（append-only，可觀測性）
# ───────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root  # lazy import 避免 cycle
    return data_root() / "soul"


# ───────────────────────────────────────────────────────────
# ToolRegistry — 動態註冊表（§2.2 接口，唯一入口）
# ───────────────────────────────────────────────────────────

class ToolRegistry:
    """動態註冊表。

    不變式（§2.2）：
      1. ``register_mcp_server`` 是唯一註冊入口；``unregister_mcp_server`` 是唯一註銷入口。
      2. ``project_capabilities`` 只讀、deterministic，fail-silent（註冊表空 →
         只投影靜態 3 組，與現狀完全等價）。
      3. ``call`` 是工具調用唯一入口。
      4. 註冊表不持有 LLM / EventBus / SpeakerToken 引用。
    """

    def __init__(
        self,
        *,
        call_timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        health_check_timeout: float = DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS,
        offline_after_consecutive_failures: int = DEFAULT_OFFLINE_AFTER_CONSECUTIVE_FAILURES,
        max_auto_retries: int = DEFAULT_MAX_AUTO_RETRIES,
        ask_gate: Optional[AskGate] = None,
        store_dir: Optional[Any] = None,
    ) -> None:
        self._call_timeout = call_timeout
        self._health_check_timeout = health_check_timeout
        self._offline_threshold = max(1, int(offline_after_consecutive_failures))
        self._max_auto_retries = max(0, int(max_auto_retries))
        self._ask_gate: AskGate = ask_gate if ask_gate is not None else RejectingAskGate()
        self._store_dir: Path = _resolve_store_dir(store_dir)

        # server 級狀態（健康三態的權威來源，§2.4）
        self._servers: Dict[str, Dict[str, Any]] = {}
        # tool_id → RegisteredTool（註冊順序保序）
        self._tools: Dict[str, RegisteredTool] = {}
        # tool_id → 最近一次成功結果（§4.2.1 預設快取）
        self._last_success: Dict[str, Any] = {}
        self._last_success_at: Dict[str, str] = {}

    # ── 動態註冊 / 註銷（§2.2 唯一入口）─────────────────────

    async def register_mcp_server(self, server_id: str, client: Any) -> List[RegisteredTool]:
        """註冊一個 MCP server：連接 → tools/list → 逐工具自動歸類（§2.3）→ 註冊。

        - 任一工具無法歸類 → 該工具拒絕註冊（fail-closed），server 其餘工具照常註冊。
        - server 斷線 / list_tools 超時 → 整個 server 不註冊，標記 offline（§2.4）。
        - 已註冊 server 重新註冊 = 重連恢復：先清舊工具，成功後狀態回 healthy。

        Returns:
            本次註冊成功的工具列表（無法歸類的被拒工具不在此列）。
        """
        # 重連語意：清掉該 server 的舊工具與狀態（不記錄 unregistered，因為馬上重註冊）
        self._drop_server_tools(server_id)
        self._servers.pop(server_id, None)

        try:
            raw_tools = await asyncio.wait_for(
                client.list_tools(), timeout=self._health_check_timeout
            )
        except Exception as exc:  # 斷線 / 超時 / 異常 → offline（§2.4 規則 1）
            self._servers[server_id] = {
                "client": client,
                "health": HEALTH_OFFLINE,
                "consecutive_failures": 0,
                "reason": f"list_tools_failed: {type(exc).__name__}: {exc}",
            }
            self._trace("health_changed", {
                "server_id": server_id,
                "health": HEALTH_OFFLINE,
                "reason": self._servers[server_id]["reason"],
            })
            logger.warning(
                "[ToolRegistry] MCP server 註冊失敗（offline）: server_id=%s err=%s:%s",
                server_id, type(exc).__name__, exc,
            )
            return []

        items = self._normalize_tool_items(raw_tools)
        registered: List[RegisteredTool] = []
        for item in items:
            name = self._extract_name(item)
            if not name:
                self._reject(server_id, item, reason="tool_name_missing")
                continue
            description = self._extract_description(item)
            group = classify_tool(name, description)
            if group is None:
                # 優先級 3：無法歸類 → 拒絕註冊（fail-closed，§2.3.3）
                self._reject(
                    server_id, item,
                    reason=f"unclassifiable (name={name!r}, desc={description[:60]!r})",
                )
                continue
            perm = permission_class_for(name, group)
            tool = RegisteredTool(
                tool_id=f"{server_id}:{name}",
                server_id=server_id,
                name=name,
                description=description,
                input_schema=self._extract_schema(item),
                capability_group=group,
                permission_class=perm,
                health=HEALTH_HEALTHY,
            )
            self._tools[tool.tool_id] = tool
            registered.append(tool)
            self._trace("tool_registered", {
                "server_id": server_id,
                "tool_id": tool.tool_id,
                "name": tool.name,
                "capability_group": tool.capability_group,
                "permission_class": tool.permission_class,
            })
            logger.info(
                "[ToolRegistry] tool registered tool_id=%s group=%s perm=%s",
                tool.tool_id, tool.capability_group, tool.permission_class,
            )

        self._servers[server_id] = {
            "client": client,
            "health": HEALTH_HEALTHY,
            "consecutive_failures": 0,
            "reason": None,
        }
        if registered:
            self._trace("health_changed", {
                "server_id": server_id,
                "health": HEALTH_HEALTHY,
                "reason": "register_ok",
            })
        return registered

    def unregister_mcp_server(self, server_id: str) -> None:
        """註銷整個 server（斷線 / 主動移除）。註銷後該組工具不再投影。"""
        removed = [t for t in self._tools.values() if t.server_id == server_id]
        for t in removed:
            self._tools.pop(t.tool_id, None)
            self._last_success.pop(t.tool_id, None)
            self._last_success_at.pop(t.tool_id, None)
        self._servers.pop(server_id, None)
        self._trace("server_unregistered", {
            "server_id": server_id,
            "removed_tool_count": len(removed),
        })
        logger.info(
            "[ToolRegistry] server unregistered server_id=%s removed_tools=%d",
            server_id, len(removed),
        )

    # ── 查詢（§2.2）────────────────────────────────────────

    def list_tools(self, group: Optional[str] = None) -> List[RegisteredTool]:
        """按能力組過濾列出已註冊工具（group=None 列出全部）。註冊順序保序。"""
        if group is None:
            return list(self._tools.values())
        return [t for t in self._tools.values() if t.capability_group == group]

    def get_tool(self, tool_id: str) -> Optional[RegisteredTool]:
        """按 tool_id 取單個工具（Actuator 路由用）。"""
        return self._tools.get(tool_id)

    def health_snapshot(self) -> Dict[str, str]:
        """全量健康快照 {server_id: health}（healthy / degraded / offline）。"""
        return {sid: s["health"] for sid, s in self._servers.items()}

    def mark_offline(self, server_id: str, reason: str) -> None:
        """斷線 / 超時 → 標記 offline（§2.4），該 server 工具不投影。"""
        s = self._servers.get(server_id)
        if s is None:
            return
        if s["health"] != HEALTH_OFFLINE:
            s["health"] = HEALTH_OFFLINE
            s["reason"] = reason
            self._trace("health_changed", {
                "server_id": server_id,
                "health": HEALTH_OFFLINE,
                "reason": reason,
            })
            logger.warning(
                "[ToolRegistry] mark_offline server_id=%s reason=%s", server_id, reason,
            )

    def mark_healthy(self, server_id: str, reason: str = "operator") -> None:
        """手動恢復 healthy（外部重連成功的補救通道；正常恢復走 register_mcp_server）。"""
        s = self._servers.get(server_id)
        if s is None:
            return
        s["health"] = HEALTH_HEALTHY
        s["consecutive_failures"] = 0
        s["reason"] = reason
        self._trace("health_changed", {
            "server_id": server_id,
            "health": HEALTH_HEALTHY,
            "reason": reason,
        })

    # ── 投影合併（capability.py 0 改動，§2.5）───────────────

    def project_capabilities(self) -> List[Any]:
        """合併投影：靜態 3 組 + 動態 healthy/degraded 組工具（§2.5，fail-silent）。

        - 靜態 ``CAPABILITY_DEFINITIONS`` 永遠投影（註冊表空 → 與現狀完全等價）。
        - 動態：該組有 healthy/degraded 工具時，expression 附加工具明細
          （can 措辭：陳述「可以」，不陳述「應」）。
        - offline server 的工具不投影（該組回退到靜態 expression）。
        - 只讀、deterministic（靜態定義順序 + tool_id 排序）、fail-silent
          （任何異常 → 回退純靜態 3 組）。

        Returns:
            list[CapabilityDefinition]：與 capability.py 同構（id + expression）。
        """
        try:
            from src.soul.capability import CAPABILITY_DEFINITIONS, CapabilityDefinition
            merged: List[CapabilityDefinition] = []
            for cap in CAPABILITY_DEFINITIONS.values():
                dynamic = sorted(
                    (
                        t for t in self._tools.values()
                        if t.capability_group == cap.id and self._is_projected(t.server_id)
                    ),
                    key=lambda t: t.tool_id,
                )
                expr = cap.expression
                if dynamic:
                    tool_names = "、".join(t.name for t in dynamic)
                    expr = (
                        expr.rstrip()
                        + f"（{cap.id}可用工具：{tool_names}）"
                    )
                merged.append(CapabilityDefinition(id=cap.id, expression=expr))
            return merged
        except Exception as exc:
            # fail-silent：註冊表層面的投影壞掉 → 回退純靜態（與現狀等價），不 raise
            logger.warning(
                "[ToolRegistry] project_capabilities 降級為純靜態投影: %s:%s",
                type(exc).__name__, exc,
            )
            from src.soul.capability import CAPABILITY_DEFINITIONS
            return list(CAPABILITY_DEFINITIONS.values())

    # ── 調用（Actuator 唯一入口，§3 / §4）───────────────────

    async def call(
        self,
        tool_id: str,
        args: Dict[str, Any],
        *,
        permission_gate: str,
    ) -> ToolResult:
        """單次工具調用（Actuator 唯一入口）。

        - 工具不存在 → 降級（tool_not_found）。
        - server offline → 拒絕調用（server_offline，降級結果）。
        - 權限閘（§4.1）：ask_required 工具必須 permission_gate="ask_required"
          且 AskGate 批准，否則 permission_denied（fail-closed，等同 do_nothing）。
        - 硬超時（預設 5s，可配置）→ 超時即放棄 → 降級（§4.2）。
        - 任何未捕獲異常 → 降級（絕不 crash，絕不阻塞主心跳 §4.3）。
        - 降級：有最近一次成功結果 → 回傳預設快取（cached=True）；
          無快取 → 空結果（data=None）。自動重試預設 0（§3.2）。

        Args:
            tool_id: 目標工具 tool_id
            args: 工具參數 dict
            permission_gate: 呼叫方（Actuator）聲明的守門級別
                ("auto_approved" | "ask_required"，§4.1)

        Returns:
            ToolResult（永不 raise）
        """
        tool = self._tools.get(tool_id)
        if tool is None:
            return ToolResult(ok=False, data=None, error="tool_not_found", degraded=True)

        server = self._servers.get(tool.server_id)
        if server is None or server["health"] == HEALTH_OFFLINE:
            return ToolResult(
                ok=False, data=None,
                error=f"server_offline: {server.get('reason', '') if server else 'not_registered'}",
                degraded=True,
            )

        # 權限閘（§4.1）
        if tool.permission_class == PERM_ASK_REQUIRED:
            if permission_gate != PERM_ASK_REQUIRED:
                self._trace("permission_denied", {
                    "tool_id": tool_id, "reason": "ask_required_without_ask_gate",
                })
                return ToolResult(ok=False, data=None, error="permission_denied")
            try:
                approved = self._ask_gate.approve(tool, args)
            except Exception as exc:
                # Ask 守門本身壞掉 → 拒絕（fail-closed），不執行
                approved = False
                self._trace("permission_denied", {
                    "tool_id": tool_id, "reason": f"ask_gate_error: {type(exc).__name__}",
                })
            if not approved:
                return ToolResult(ok=False, data=None, error="permission_denied")

        # 執行（硬超時）
        attempt = 0
        while True:
            try:
                raw = await asyncio.wait_for(
                    self._invoke_tool(server["client"], tool, args),
                    timeout=self._call_timeout,
                )
                self._mark_success(tool, raw)
                return ToolResult(ok=True, data=raw, error=None, degraded=False, cached=False)
            except asyncio.TimeoutError:
                return self._degrade(
                    tool,
                    error=f"timeout_after_{self._call_timeout:g}s",
                    failure_phase="call_timeout",
                )
            except Exception as exc:
                # 自動重試（預設 0）：只在顯式配置 >0 時重試，且每次都走硬超時
                if attempt < self._max_auto_retries:
                    attempt += 1
                    logger.info(
                        "[ToolRegistry] call retry %d/%d tool_id=%s err=%s",
                        attempt, self._max_auto_retries, tool_id, exc,
                    )
                    continue
                return self._degrade(
                    tool,
                    error=f"{type(exc).__name__}: {exc}",
                    failure_phase="call_exception",
                )

    # ── 內部：執行 / 成功 / 降級 / 健康 ─────────────────────

    async def _invoke_tool(self, client: Any, tool: RegisteredTool, args: Dict[str, Any]) -> Any:
        return await client.call_tool(tool.name, args)

    def _mark_success(self, tool: RegisteredTool, data: Any) -> None:
        self._last_success[tool.tool_id] = data
        self._last_success_at[tool.tool_id] = _utcnow_iso()
        s = self._servers.get(tool.server_id)
        if s is not None:
            if s["health"] == HEALTH_OFFLINE:
                s["health"] = HEALTH_HEALTHY  # 恢復（§2.4 規則 3，重連成功）
                s["reason"] = "call_ok"
                self._trace("health_changed", {
                    "server_id": tool.server_id,
                    "health": HEALTH_HEALTHY,
                    "reason": "call_ok",
                })
            s["consecutive_failures"] = 0

    def _degrade(self, tool: RegisteredTool, *, error: str, failure_phase: str) -> ToolResult:
        """Fail-closed 平滑降級（§4.2）：絕不 crash、絕不重試風暴、不阻塞主心跳。

        連續失敗達閾值 → offline（該 server 工具不再投影）。
        有預設快取 → 回傳快取（cached=True，staleness 由呼叫方標註）；
        無快取 → 空結果（data=None）。
        """
        s = self._servers.get(tool.server_id)
        if s is not None:
            s["consecutive_failures"] = s.get("consecutive_failures", 0) + 1
            if s["consecutive_failures"] >= self._offline_threshold:
                if s["health"] != HEALTH_OFFLINE:
                    s["health"] = HEALTH_OFFLINE
                    s["reason"] = f"{failure_phase}: {error}"
                    self._trace("health_changed", {
                        "server_id": tool.server_id,
                        "health": HEALTH_OFFLINE,
                        "reason": s["reason"],
                    })
                    logger.warning(
                        "[ToolRegistry] server offline server_id=%s consecutive=%d reason=%s",
                        tool.server_id, s["consecutive_failures"], s["reason"],
                    )
            elif s["health"] == HEALTH_HEALTHY:
                s["health"] = HEALTH_DEGRADED
                s["reason"] = error
                self._trace("health_changed", {
                    "server_id": tool.server_id,
                    "health": HEALTH_DEGRADED,
                    "reason": error,
                })

        self._trace("call_degraded", {
            "tool_id": tool.tool_id,
            "server_id": tool.server_id,
            "phase": failure_phase,
            "error": error,
            "cached": tool.tool_id in self._last_success,
        })
        logger.warning(
            "[ToolRegistry] call degraded tool_id=%s phase=%s err=%s cached=%s",
            tool.tool_id, failure_phase, error, tool.tool_id in self._last_success,
        )

        if tool.tool_id in self._last_success:
            return ToolResult(
                ok=True, data=self._last_success[tool.tool_id],
                error=error, degraded=True, cached=True,
            )
        return ToolResult(ok=False, data=None, error=error, degraded=True, cached=False)

    # ── 內部：tools/list 解析（防禦性，fail-closed）─────────

    def _normalize_tool_items(self, raw: Any) -> List[Any]:
        """相容 MCP spec（{"tools": [...]}）與裸陣列兩種回傳形態。"""
        if isinstance(raw, dict):
            items = raw.get("tools", [])
            if not isinstance(items, list):
                items = []
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        return list(items)

    @staticmethod
    def _extract_name(item: Any) -> str:
        if isinstance(item, dict):
            v = item.get("name")
            return v if isinstance(v, str) and v.strip() else ""
        return getattr(item, "name", "") if isinstance(getattr(item, "name", ""), str) else ""

    @staticmethod
    def _extract_description(item: Any) -> str:
        if isinstance(item, dict):
            v = item.get("description")
            return v if isinstance(v, str) else ""
        v = getattr(item, "description", "")
        return v if isinstance(v, str) else ""

    @staticmethod
    def _extract_schema(item: Any) -> dict:
        if isinstance(item, dict):
            v = item.get("inputSchema") or item.get("input_schema")
            return v if isinstance(v, dict) else {}
        v = getattr(item, "input_schema", None) or getattr(item, "inputSchema", None)
        return v if isinstance(v, dict) else {}

    def _reject(self, server_id: str, item: Any, *, reason: str) -> None:
        name = self._extract_name(item) or "<missing>"
        self._trace("tool_rejected", {
            "server_id": server_id,
            "name": name,
            "reason": reason,
        })
        logger.warning(
            "[ToolRegistry] tool rejected (fail-closed) server_id=%s name=%s reason=%s",
            server_id, name, reason,
        )

    def _drop_server_tools(self, server_id: str) -> None:
        """移除某 server 的所有工具（重連註冊前的清理 + 註銷共用）。"""
        for t in list(self._tools.values()):
            if t.server_id == server_id:
                self._tools.pop(t.tool_id, None)
                self._last_success.pop(t.tool_id, None)
                self._last_success_at.pop(t.tool_id, None)

    def _is_projected(self, server_id: str) -> bool:
        """投影判定（§2.4）：offline 不投影；healthy / degraded 正常投影。"""
        s = self._servers.get(server_id)
        if s is None:
            return False
        return s["health"] in (HEALTH_HEALTHY, HEALTH_DEGRADED)

    # ── 側車 trace（append-only）──────────────────────────

    def _trace(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            path = self._store_dir / TRACE_FILENAME
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {"ts": _utcnow_iso(), "event_type": event_type, **payload}
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("[ToolRegistry] trace write failed (%s): %s", path, exc)


__all__ = [
    "AskGate",
    "CAPABILITY_GROUP_COMMUNICATE",
    "CAPABILITY_GROUP_OBSERVE",
    "CAPABILITY_GROUP_REFLECT",
    "CAPABILITY_GROUPS",
    "DEFAULT_CALL_TIMEOUT_SECONDS",
    "DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS",
    "DEFAULT_MAX_AUTO_RETRIES",
    "DEFAULT_OFFLINE_AFTER_CONSECUTIVE_FAILURES",
    "EXPLICIT_GROUP_MAP",
    "EXPLICIT_PERMISSION_MAP",
    "HEALTH_DEGRADED",
    "HEALTH_HEALTHY",
    "HEALTH_OFFLINE",
    "HEALTH_STATES",
    "PERM_ASK_REQUIRED",
    "PERM_AUTO_APPROVED",
    "PERM_CLASSES",
    "RegisteredTool",
    "RejectingAskGate",
    "TRACE_FILENAME",
    "ToolRegistry",
    "ToolResult",
    "classify_tool",
    "permission_class_for",
]
