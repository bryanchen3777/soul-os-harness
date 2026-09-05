"""
src/soul/actuator.py — Soul OS TS-2 Tooling Volition Gate（IMPLEMENTATION）

定位（照 docs/TOOLING-MCP-CONTRACT.md §3，TS-1 設計已鎖定）：
  Actuator 是 Decision 四元與工具執行之間的**唯一派發點**——Decision 只選
  「組」（observe / reflect / transmit），Actuator 按 Motive 內容路由到組內
  具體工具（``tool_registry.get_tool`` + ``call``）。

本模組兌現 SM-4 空轉決策（``src/soul/motive.py:29-30`` / ``decision.py:58``：
  observe / reflect 的執行邏輯是後續工單）——現在 Decision 選 observe →
  Actuator 派發 observe_environment 組單次工具調用 → 結果回寫 world_context
  感知；Decision 選 reflect → 派發 reflect_memory 組單次工具調用 → 結果回寫
  記憶摘要認知。

0 自主遞迴硬規則（§3.2，鎖死）：
  1. dispatch 是純函數式單次調用：一次 Decision 批准 = 一次工具調用 =
     一次結果回流，結束。無「工具結果 → 再決策 → 再工具」鏈式結構。
  2. 工具結果**不產生新的 Motive / Decision 循環**；結果只進 flowback
     （感知 / 認知），不進入任何工具路由邏輯。
  3. Actuator **無權 publish AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER**——
     不持有 EventBus / SpeakerToken / LLM 引用（與 WorldEventSource ABC
     同款隔離，``src/world/base.py:13-18`` 模式）。
  4. Decision 提示詞不因工具增多而膨脹：永遠只看 3 個能力組（聚合原則 §2.3）。
  5. 不做無腦 ReAct 循環：無多輪鏈式執行。

結果回流（§3.3）：
  - observe 成功 → 構造 WorldEvent 寫入 WorldPerceptionState（ephemeral，
    24h novelty window 復用）→ 經既有感知路徑進入認知；失敗 → 不注入
    （感知缺失靜默，不等同 crash）。
  - reflect 成功 → 交給注入的 memory_sink（記憶摘要認知）；失敗 → 不注入。
  - 兩種回流都不直接寫 InnerLifeEvent / SAGE（§3.3 污染防護）。

Frozen contract 邊界（0 change）：
  不改 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers /
  SAGE / EventBus / capability.py / decision.py / motive.py / scheduler.py。
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.soul.tool_registry import (
    CAPABILITY_GROUP_OBSERVE,
    CAPABILITY_GROUP_REFLECT,
    HEALTH_OFFLINE,
    PERM_ASK_REQUIRED,
    PERM_AUTO_APPROVED,
    RegisteredTool,
    ToolResult,
    ToolRegistry,
)

logger = logging.getLogger("soul_os.soul.actuator")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

# Decision 四元 → 能力組映射（§3.1；transmit 走既有 Expression 路徑，不在此派發）
_ACTION_TO_GROUP = {
    "observe": CAPABILITY_GROUP_OBSERVE,
    "reflect": CAPABILITY_GROUP_REFLECT,
}

# Motive 內容關鍵詞 → 工具名路由表（Actuator 層展開工具明細，§3.1）。
# 執行時按 Motive 內容路由到組內具體工具；無命中 → 組內第一個可用工具。
# MS-1 D3（MS-2 落地，additive）：多模态路由——「聽/語音/麦克风」→ mic_listen；
# 「看/相机/画面」→ camera_capture。用詞組避免單字誤路由（如「聽聽看」）。
_ROUTE_KEYWORDS: tuple = (
    (("天氣", "天气", "weather", "rain", "temperature", "气温", "氣溫"), "weather"),
    (("日历", "日曆", "calendar", "会议", "會議", "日程", "schedule"), "calendar"),
    (("新闻", "新聞", "news", "资讯", "資訊"), "news"),
    (("搜索", "search", "查一下", "找一下", "web"), "web_search"),
    (("时间", "時間", "time", "几点", "幾點"), "time"),
    (("记忆", "記憶", "memory", "回忆", "回憶", "之前", "想不起"), "memory_search"),
    (("日记", "日記", "diary", "昨天", "前天"), "diary_read"),
    (("麦克风", "麥克風", "语音", "語音", "收音", "voice", "speech", "stt",
      "聽一聽", "聽聽看", "听一听", "环境声音", "環境聲音"), "mic_listen"),
    (("相机", "相機", "摄像头", "攝像頭", "画面", "畫面", "camera", "vision",
      "看一看", "看看房间", "看看客廳", "看看客厅", "拍一张", "拍一張"), "camera_capture"),
)

# 工具名 → WorldEvent.source（VALID_SOURCES 白名單：weather/news/calendar/social/synthetic）
# MS-1 D2（MS-2 落地）：多模态管道認領新 source（audio_input / camera_capture），
# 取代 synthetic 兜底——保留「耳朵/眼睛」語義；未知工具仍 fallback synthetic。
_SOURCE_HINT_MAP = (
    (("weather",), "weather"),
    (("calendar",), "calendar"),
    (("news",), "news"),
    (("mic_listen", "audio_transcribe", "stt"), "audio_input"),
    (("camera_capture", "camera_snapshot", "image_capture"), "camera_capture"),
)


# ───────────────────────────────────────────────────────────
# 回流 sink 型別
# ───────────────────────────────────────────────────────────

MemorySink = Callable[[ToolResult, str], None]  # (reflect 結果, agent_id) → 記憶摘要認知


# ───────────────────────────────────────────────────────────
# Actuator
# ───────────────────────────────────────────────────────────

class Actuator:
    """observe / reflect 專屬執行器（§3，TS-2 實現）。

    用法:
        registry = ToolRegistry(...)
        await registry.register_mcp_server("weather-server", client)
        actuator = Actuator(registry, perception_state=WorldPerceptionState())
        result = await actuator.dispatch(decision, motive, agent_id="agent_yua")

    不持有 bus / SpeakerToken / LLM（§3.2 硬規則 3）。單次調用，結果不回環。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        perception_state: Optional[Any] = None,
        memory_sink: Optional[MemorySink] = None,
    ) -> None:
        """
        Args:
            registry: 動態註冊表（工具調用唯一入口，§2.2 不變式 3）
            perception_state: WorldPerceptionState（observe 結果回流的目標，
                可為 None——無注入時觀察結果僅 trace，不 crash）
            memory_sink: reflect 結果的記憶摘要消費者（可為 None——無消費者時
                僅 trace，不 crash）
        """
        self._registry = registry
        self._perception_state = perception_state
        self._memory_sink = memory_sink

    # ── 派發（§3.1 唯一派發點）────────────────────────────

    async def dispatch(
        self,
        decision: Any,
        motive: Any,
        agent_id: str = "",
    ) -> Optional[ToolResult]:
        """按 Decision 四元派發單次調用（Actuator 唯一入口）。

        - observe → observe_environment 組工具調用，結果回流 world_context 感知
        - reflect → reflect_memory 組工具調用，結果回流記憶摘要認知
        - transmit → 不在此派發（走既有 Expression 路徑），返回 None
        - do_nothing → 不執行（合法主動選擇），返回 None
        - 單次調用，結果不回環到工具路由（§3.2）

        Returns:
            ToolResult（observe / reflect 執行結果）或 None（未執行）。
            永不 raise：任何異常 → 降級空結果（fail-closed）。
        """
        action = self._decision_action(decision)
        return await self._dispatch_action(action, motive, agent_id)

    async def execute_observe(
        self,
        motive: Any,
        agent_id: str = "",
    ) -> Optional[ToolResult]:
        """observe 單次執行入口（TS-2.1 scheduler 接線用）。

        強制走 observe_environment 組單次工具調用，結果回流 world_context 感知。
        與 ``dispatch(decision=observe)`` 行為完全一致（同款路由 / 權限 / 回流），
        0 自主遞迴（§3.2）、無權 publish。永不 raise（fail-closed）。
        """
        return await self._dispatch_action("observe", motive, agent_id)

    async def execute_reflect(
        self,
        motive: Any,
        agent_id: str = "",
    ) -> Optional[ToolResult]:
        """reflect 單次執行入口（TS-2.1 scheduler 接線用）。

        強制走 reflect_memory 組單次工具調用，結果回流記憶摘要（memory_sink）。
        與 ``dispatch(decision=reflect)`` 行為完全一致（同款路由 / 權限 / 回流），
        0 自主遞迴（§3.2）、無權 publish。永不 raise（fail-closed）。
        """
        return await self._dispatch_action("reflect", motive, agent_id)

    async def _dispatch_action(
        self,
        action: str,
        motive: Any,
        agent_id: str = "",
    ) -> Optional[ToolResult]:
        """內部：按 action 派發單次調用（dispatch / execute_observe / execute_reflect 共用）。"""
        group = _ACTION_TO_GROUP.get(action)
        if group is None:
            # transmit（既有路徑）/ do_nothing（合法不執行）/ 未知 → 不在此派發
            return None

        try:
            tool = self.route(group, motive)
            if tool is None:
                # 組內無可用工具 → 降級空結果（等同沒感知/沒回顧），不 crash
                logger.info(
                    "[Actuator] 無可用工具 group=%s agent=%s → 降級空結果（no_tool_for_group）",
                    group, agent_id,
                )
                return ToolResult(
                    ok=False, data=None, error="no_tool_for_group", degraded=True,
                )

            args = self._build_args(tool, motive)
            permission_gate = (
                PERM_AUTO_APPROVED
                if tool.permission_class == PERM_AUTO_APPROVED
                else PERM_ASK_REQUIRED
            )
            result = await self._registry.call(
                tool.tool_id, args, permission_gate=permission_gate,
            )
            # 結果回流（§3.3）：單次行動結束後的唯一去向——感知 / 認知，
            # 不產生新的工具調用（0 自主遞迴）。
            self._flowback(action, tool, result, agent_id)
            return result
        except Exception as exc:
            # fail-closed 兜底（§4.3 降級兜底）：絕不 crash、不阻塞主心跳
            logger.warning(
                "[Actuator] dispatch 異常（降級為空結果）: action=%s err=%s:%s",
                action, type(exc).__name__, exc,
            )
            return ToolResult(
                ok=False, data=None, error=f"{type(exc).__name__}: {exc}", degraded=True,
            )

    # ── 路由（執行器層展開工具明細，§3.1）─────────────────

    def route(self, group: str, motive: Any) -> Optional[RegisteredTool]:
        """按 Motive 內容路由到組內具體工具。

        優先級：
          1. Motive 內容命中關鍵詞表 → 對應工具（名字匹配，healthy/degraded 皆可）
          2. 組內第一個可調用工具（非 offline）
          3. 無 → None（組內無可用工具）
        """
        tools = [
            t for t in self._registry.list_tools(group=group)
            if not self._is_offline(t)
        ]
        if not tools:
            return None

        content = ""
        try:
            content = str(getattr(motive, "content", "") or "")
        except Exception:
            content = ""
        content_lower = content.lower()

        for keywords, target in _ROUTE_KEYWORDS:
            for kw in keywords:
                if kw.lower() in content_lower:
                    hit = self._find_tool_by_name(tools, target)
                    if hit is not None:
                        return hit
                    break  # 命中關鍵詞但無對應工具 → 換下一組關鍵詞

        return tools[0]

    # ── 權限（§4.1）──────────────────────────────────────

    @staticmethod
    def permission_gate_for(tool: RegisteredTool) -> str:
        """工具權限類 → 呼叫方守門級別（ask_required 工具必須走 Ask 通道）。"""
        if tool.permission_class == PERM_ASK_REQUIRED:
            return PERM_ASK_REQUIRED
        return PERM_AUTO_APPROVED

    # ── 內部：回流（§3.3）─────────────────────────────────

    def _flowback(self, action: str, tool: RegisteredTool, result: ToolResult, agent_id: str) -> None:
        """單次行動的結果回流（感知 / 認知）。0 自主遞迴：不觸發新工具調用。

        - observe + ok → 寫入 WorldPerceptionState（帶 stale 標註 if cached）
        - observe + 失敗 → 不注入（感知缺失靜默，§4.2 降級語義）
        - reflect + ok → 交給 memory_sink（記憶摘要認知）
        - reflect + 失敗 → 不注入
        """
        if action == "observe":
            if result.ok and self._perception_state is not None:
                try:
                    event = self._to_world_event(tool, result)
                    self._perception_state.add(event)
                    logger.info(
                        "[Actuator] observe 結果回流 world_context event=%s agent=%s "
                        "cached=%s",
                        event.novelty_id, agent_id, result.cached,
                    )
                except Exception as exc:
                    # 回流失敗不 crash（感知缺失靜默）
                    logger.warning(
                        "[Actuator] observe 回流失敗（不注入）: err=%s:%s",
                        type(exc).__name__, exc,
                    )
            return

        if action == "reflect":
            if result.ok and self._memory_sink is not None:
                try:
                    self._memory_sink(result, agent_id)
                    logger.info(
                        "[Actuator] reflect 結果回流記憶摘要 agent=%s cached=%s",
                        agent_id, result.cached,
                    )
                except Exception as exc:
                    logger.warning(
                        "[Actuator] reflect 回流失敗（不注入）: err=%s:%s",
                        type(exc).__name__, exc,
                    )

    def _to_world_event(self, tool: RegisteredTool, result: ToolResult) -> Any:
        """把 observe 工具結果轉成 WorldEvent（objective fact 語義）。

        source 對齊 VALID_SOURCES 白名單（weather/news/calendar/social/synthetic）；
        cached 結果帶 staleness 標註進 summary（避免把舊資料當新感知，§4.2.1）。
        """
        from src.world.perception import WorldEvent  # lazy import 避免 cycle

        source = self._source_for(tool.name)
        ts = datetime.now(timezone.utc).isoformat()
        summary = self._summarize(tool, result)
        if result.cached:
            summary = f"[快取 {ts}] {summary}"
        data: Dict[str, Any] = {}
        if isinstance(result.data, dict):
            data = dict(result.data)
        elif isinstance(result.data, (str, int, float, bool)) or result.data is None:
            data = {"value": result.data}
        else:  # 其他結構化結果 → 原樣放進 data（不丟棄）
            data = {"payload": result.data}
        data["tool_id"] = tool.tool_id
        data["cached"] = result.cached

        return WorldEvent(
            source=source,
            type=f"tool_{tool.name}",
            novelty_id=self._content_novelty_id(tool.name, data, ts),
            ts=ts,
            summary=summary[:300],
            data=data,
            priority=0,
        )

    @staticmethod
    def _content_novelty_id(tool_name: str, data: Dict[str, Any], ts: str) -> str:
        """MS-1 D9 內容級 novelty_id（additive，無特徵時 fallback 維持 ``tool:ts``）。

        - STT（audio_transcribe / stt）：``"stt:" + SHA256(normalize(text))[:12]``
          —— 同一句話重複出現 → 同一 id → novelty 衰減生效（句級去重）。
        - Camera（camera_capture / camera_snapshot / image_capture）：
          ``"cam:" + scene_tag + ":" + utc_date`` —— 場景語義桶 + 日桶
          （單幀像素 hash 因光照噪點失真，故用粗分類 + 日桶去重）。
        - Fallback：工具未提供內容特徵（text / scene_tag）→ 維持既有 ``tool:ts``
          （不破壞既有路徑，已 100% 保留）。
        """
        if "text" in data and isinstance(data["text"], str) and data["text"].strip():
            norm = Actuator._normalize_transcript(data["text"])
            digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
            return f"stt:{digest}"
        if "scene_tag" in data and isinstance(data["scene_tag"], str) and data["scene_tag"].strip():
            utc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return f"cam:{data['scene_tag'].strip()}:{utc_date}"
        return f"{tool_name}:{ts}"

    @staticmethod
    def _normalize_transcript(text: str) -> str:
        """D9 normalize：小寫 + Unicode NFKC（全形→半形）+ 去標點空白。"""
        norm = unicodedata.normalize("NFKC", text).lower()
        return re.sub(r"[\s\W_]+", "", norm, flags=re.UNICODE)

    @staticmethod
    def _source_for(tool_name: str) -> str:
        name_lower = tool_name.lower()
        for keywords, source in _SOURCE_HINT_MAP:
            for kw in keywords:
                if kw in name_lower:
                    return source
        return "synthetic"

    @staticmethod
    def _summarize(tool: RegisteredTool, result: ToolResult) -> str:
        data = result.data
        if isinstance(data, dict):
            # 取常用欄位做一句話摘要；沒有 → 取 description 開頭
            for key in ("summary", "text", "description", "result", "message"):
                v = data.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:200]
        if isinstance(data, str) and data.strip():
            return data.strip()[:200]
        return tool.description.strip()[:200] or f"{tool.name} 的觀察結果"

    # ── 內部：工具參數 / 離線判定 ─────────────────────────

    @staticmethod
    def _build_args(tool: RegisteredTool, motive: Any) -> Dict[str, Any]:
        """v1 極簡參數推斷：查詢/記憶類帶 motive 內容當 query，其餘空 dict。

        TS-3 接真實工具後由工具 input_schema 校驗取代。
        """
        args: Dict[str, Any] = {}
        if tool.capability_group == CAPABILITY_GROUP_REFLECT:
            content = getattr(motive, "content", "")
            if isinstance(content, str) and content.strip():
                args["query"] = content.strip()[:200]
        elif any(k in tool.name for k in ("search", "web_search")):
            content = getattr(motive, "content", "")
            if isinstance(content, str) and content.strip():
                args["query"] = content.strip()[:200]
        return args

    @staticmethod
    def _find_tool_by_name(tools: list, target: str) -> Optional[RegisteredTool]:
        for t in tools:
            if t.name == target or t.name.endswith(f":{target}"):
                return t
        return None

    def _is_offline(self, tool: RegisteredTool) -> bool:
        return self._registry.health_snapshot().get(tool.server_id) == HEALTH_OFFLINE

    @staticmethod
    def _decision_action(decision: Any) -> str:
        """從 DecisionResult（或字串）取四元 action，防禦式解析。"""
        if isinstance(decision, str):
            return decision
        try:
            v = getattr(decision, "decision", "")
            return v if isinstance(v, str) else ""
        except Exception:
            return ""


__all__ = [
    "Actuator",
    "MemorySink",
]
