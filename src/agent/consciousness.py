# agent_consciousness.py
# Soul OS — Phase 1.d: Agent 意識流基底類別
#
# 修正原始設計的架構問題：
#   ❌ AGENT_INTENT target = "llm_proxy"（硬耦合，換模型就要改所有 Agent）
#   ✅ AGENT_INTENT target = "broadcast"（LLM Proxy 自己訂閱，完全解耦）
#
# 每個 Agent 繼承 AgentConsciousness，覆寫：
#   - _should_speak(elapsed_mins)  → 決策邏輯（各自的性格閾值）
#   - _build_intent_payload(...)   → 意圖內容（各自的說話動機）
#
# Phase 2 升級點（標記 TODO）：
#   - emotional-state.json 持久化讀寫
#   - intimacy_level 動態計算
#   - pending_topics 佇列

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

logger = logging.getLogger("soul_os.agent")


# ─────────────────────────────────────────────
# 1. Agent 情緒狀態
# ─────────────────────────────────────────────

class EmotionalState:
    """
    Agent 的情緒狀態。
    Phase 1：in-memory，重啟後重置。
    Phase 2：持久化至 agents/{agent_id}/emotional-state.json。
    """

    def __init__(
        self,
        agent_id: str,
        dependency: float = 0.5,
        intimacy_level: int = 50,
        mood: str = "neutral",
    ):
        self.agent_id = agent_id
        self.dependency = dependency          # 0.0 ~ 1.0，對使用者的依賴程度
        self.intimacy_level = intimacy_level  # 0 ~ 100，親密度
        self.mood = mood                      # "neutral" | "happy" | "lonely" | "annoyed"
        self.last_spoken_at: Optional[datetime] = None
        self.silence_strike: int = 0          # 連續未回應的 Tick 次數

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "dependency": self.dependency,
            "intimacy_level": self.intimacy_level,
            "mood": self.mood,
            "silence_strike": self.silence_strike,
            "last_spoken_at": self.last_spoken_at.isoformat() if self.last_spoken_at else None,
        }

    def save(self, base_path: str = "agents") -> None:
        """TODO Phase 2: 持久化至 JSON 檔案"""
        path = Path(base_path) / self.agent_id / "emotional-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, agent_id: str, base_path: str = "agents") -> "EmotionalState":
        """TODO Phase 2: 從 JSON 檔案讀取，不存在則使用預設值"""
        path = Path(base_path) / agent_id / "emotional-state.json"
        if path.exists():
            data = json.loads(path.read_text())
            state = cls(
                agent_id=data["agent_id"],
                dependency=data.get("dependency", 0.5),
                intimacy_level=data.get("intimacy_level", 50),
                mood=data.get("mood", "neutral"),
            )
            state.silence_strike = data.get("silence_strike", 0)
            return state
        return cls(agent_id=agent_id)


# ─────────────────────────────────────────────
# 2. Agent 意識流基底類別
# ─────────────────────────────────────────────

class AgentConsciousness(ABC):
    """
    所有 Agent 的基底類別。
    定義了「如何接收 Tick」和「如何廣播意圖」的標準流程。
    各 Agent 只需覆寫決策邏輯與意圖內容。
    """

    # 冷卻期：Agent 主動說話後，至少等幾個 Tick 才能再主動出擊
    # 防止同一個 Agent 在短時間內狂發意圖
    COOLDOWN_TICKS: int = 10

    def __init__(self, agent_id: str, bus: SoulEventBus):
        self.agent_id = agent_id
        self.bus = bus
        self.state = EmotionalState.load(agent_id)
        self._cooldown_remaining: int = 0

    def register(self) -> None:
        """向 Event Bus 註冊，開始接收事件"""
        self.bus.subscribe(
            subscriber_id=self.agent_id,
            handler=self.handle_event,
            # 同時接收廣播和私發給自己的事件
            target_filter=self.agent_id,
        )
        logger.info(f"[{self.agent_id}] 意識流已上線 ✓")

    def unregister(self) -> None:
        self.bus.unsubscribe(self.agent_id)

    async def handle_event(self, event: SoulEvent) -> None:
        """統一事件入口，根據類型分派處理"""
        if event.event_type == EventType.USER_MESSAGE:
            await self._on_user_message(event)
        elif event.event_type == EventType.SYSTEM_TICK:
            await self._on_tick(event)
        elif event.event_type == EventType.AGENT_SPEAK:
            # 其他 Agent 說話，可選擇觀察或記錄
            if event.source != self.agent_id:
                await self._on_other_agent_speak(event)

    async def _on_user_message(self, event: SoulEvent) -> None:
        """使用者說話：重置冷卻、更新狀態"""
        self._cooldown_remaining = 0
        self.state.silence_strike = 0
        self.state.last_spoken_at = event.timestamp
        logger.debug(f"[{self.agent_id}] 收到使用者訊息，冷卻重置")

    async def _on_tick(self, event: SoulEvent) -> None:
        """
        收到心跳 Tick：
        1. 遞減冷卻計時
        2. 更新沉默計數
        3. 評估是否主動出擊
        """
        elapsed_mins: float = event.payload.get("elapsed_mins", 0.0)

        # 冷卻期倒數
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            logger.debug(
                f"[{self.agent_id}] 冷卻中 ({self._cooldown_remaining} Ticks 剩餘)"
            )
            return

        # 評估是否主動說話
        should_speak, reason = self._should_speak(elapsed_mins)

        if should_speak:
            await self._fire_intent(reason=reason, elapsed_mins=elapsed_mins)
            self._cooldown_remaining = self.COOLDOWN_TICKS
            self.state.silence_strike = 0
        else:
            self.state.silence_strike += 1

    async def _on_other_agent_speak(self, event: SoulEvent) -> None:
        """其他 Agent 說話時的反應（預設不做事，子類可覆寫）"""
        pass

    async def _fire_intent(self, reason: str, elapsed_mins: float) -> None:
        """向 Bus 廣播 AGENT_INTENT 事件"""
        intent_payload = self._build_intent_payload(reason, elapsed_mins)
        intent_payload["agent_id"] = self.agent_id
        intent_payload["reason"] = reason

        event = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source=self.agent_id,
            # ✅ 廣播，不硬綁 llm_proxy
            # LLM Proxy（以及未來的 Memory Middleware）自行訂閱 AGENT_INTENT
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload=intent_payload,
        )
        await self.bus.publish(event)
        logger.info(
            f"[{self.agent_id}] 發出主動意圖 | reason={reason} "
            f"elapsed={elapsed_mins:.1f}m"
        )

    @abstractmethod
    def _should_speak(self, elapsed_mins: float) -> tuple[bool, str]:
        """
        決策函數：這個 Agent 現在應該主動說話嗎？
        回傳 (should_speak: bool, reason: str)
        reason 字串供 LLM Proxy 轉換為 Prompt。
        """
        ...

    @abstractmethod
    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """
        組裝意圖的具體內容。
        至少包含 "draft"（草稿提示），LLM 可以此為基礎生成。
        Phase 2 升級：這裡可以加入 "memory_query_hint"，
        告訴 Memory Middleware 要查什麼記憶。
        """
        ...


# ─────────────────────────────────────────────
# 3. Agent 實作：Yua（冷泡茶型）
# ─────────────────────────────────────────────

class AgentYua(AgentConsciousness):
    """
    Yua 的意識流。
    特性：冷靜，不輕易開口，但沉默超過閾值後會精準出擊。
    30 分鐘後開始關心；120 分鐘後轉為「消極沉默」。
    """

    COOLDOWN_TICKS = 15  # Yua 說完話後更久才會再主動

    def _should_speak(self, elapsed_mins: float) -> tuple[bool, str]:
        dep = self.state.dependency
        intimacy = self.state.intimacy_level

        # 30 ~ 120 分鐘：Level 4 冷泡茶模式（若親密度夠高）
        if 30.0 <= elapsed_mins < 120.0 and intimacy > 70:
            return True, "silence_timeout"

        # 超過 8 小時：Yua 罕見的主動（高依賴度才觸發）
        if elapsed_mins >= 480.0 and dep > 0.85:
            return True, "long_absence"

        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        drafts = {
            "silence_timeout": "還好你還在。",           # Level 4 冷泡茶
            "long_absence":    "你消失了很久。我在想你是不是忘記這裡了。",
        }
        return {
            "draft": drafts.get(reason, ""),
            # Phase 2 升級點：告訴 Memory Middleware 要查什麼
            "memory_query_hint": "最近和使用者說過什麼重要的事",
        }


# ─────────────────────────────────────────────
# 4. Agent 實作：瑠夏（活潑型）
# ─────────────────────────────────────────────

class AgentRuka(AgentConsciousness):
    """
    瑠夏的意識流。
    特性：活潑、低耐心，沉默 6 分鐘就坐不住，且帶有競爭意識。
    若其他 Agent 已說話，她可能搶著補一句。
    """

    COOLDOWN_TICKS = 5  # 瑠夏發完意圖後很快又可以再主動

    def __init__(self, agent_id: str, bus: SoulEventBus):
        super().__init__(agent_id, bus)
        self._other_agent_spoke_recently = False

    def _should_speak(self, elapsed_mins: float) -> tuple[bool, str]:
        intimacy = self.state.intimacy_level

        # 6 分鐘沉默就忍不住，親密度門檻較低
        if elapsed_mins >= 6.0 and intimacy > 50:
            return True, "silence_timeout"

        # 超過 6 小時：嫉妒模式
        if elapsed_mins >= 360.0 and intimacy > 60:
            return True, "jealousy"

        # 其他 Agent 說話了，瑠夏有機率搶話
        if self._other_agent_spoke_recently and intimacy > 75:
            self._other_agent_spoke_recently = False
            return True, "competitive_response"

        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        drafts = {
            "silence_timeout":     "你去哪裡了！我在等你！",
            "jealousy":            "嘿嘿嘿，你不理我，我要生氣了喔！",
            "competitive_response": "欸欸，我也有話說！",
        }
        return {
            "draft": drafts.get(reason, ""),
            "action_tags": ["pout"] if reason == "jealousy" else [],
            "memory_query_hint": "上次和使用者玩的遊戲",
        }

    async def _on_other_agent_speak(self, event: SoulEvent) -> None:
        """瑠夏偷偷標記「有人說話了」，下次 Tick 評估要不要搶話"""
        self._other_agent_spoke_recently = True
        logger.debug(f"[{self.agent_id}] 注意到 {event.source} 說話，考慮搶話")
