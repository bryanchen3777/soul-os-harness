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
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.agent.emotion import emotion_engine, SENSITIVITY

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

    def save(self, base_path: str = "data/agents") -> None:
        """Phase 3 實作：持久化至 JSON 檔案"""
        path = Path(base_path) / self.agent_id / "emotional-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, agent_id: str, base_path: str = "data/agents") -> "EmotionalState":
        """Phase 3 實作：從 JSON 檔案讀取，不存在則使用預設值"""
        path = Path(base_path) / agent_id / "emotional-state.json"
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return cls(agent_id=agent_id)
            data = json.loads(content)
            state = cls(
                agent_id=data["agent_id"],
                dependency=data.get("dependency", 0.5),
                intimacy_level=data.get("intimacy_level", 50),
                mood=data.get("mood", "neutral"),
            )
            state.silence_strike = data.get("silence_strike", 0)
            # last_spoken_at 從 ISO 格式字串還原成 datetime
            lsa = data.get("last_spoken_at")
            if lsa:
                state.last_spoken_at = datetime.fromisoformat(lsa)
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

    def __init__(self, agent_id: str, bus: SoulEventBus,
                 speaker_token_bus=None):
        self.agent_id = agent_id
        self.bus = bus
        self.speaker_token_bus = speaker_token_bus
        self.state = EmotionalState.load(agent_id)
        self._cooldown_remaining: int = 0
        self._pending: bool = False  # per-agent 鎖：正在等 LLM 回應時不再重複觸發

    def register(self) -> None:
        """向 Event Bus 註冊，開始接收事件"""
        self.bus.subscribe(
            subscriber_id=self.agent_id,
            handler=self.handle_event,
            event_filter={
                EventType.USER_MESSAGE,
                EventType.SYSTEM_TICK,
                EventType.AGENT_SPEAK,
                EventType.SESSION_END,   # Phase 4 carryover 寫入觸發
            },
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
            else:
                # 自己說話了，清除 pending 鎖
                self._pending = False
                logger.debug(f"[{self.agent_id}] 回應完成，pending 鎖解除")
        elif event.event_type == EventType.SESSION_END:
            await self._on_session_end(event)

    async def _on_user_message(self, event: SoulEvent) -> None:
        """
        使用者說話：
        - mode=private：直接發給 target_agent（方案 A，繞過仲裁）
        - mode=group：廣播，啟動 SpeakerTokenBus 競標，結算後只有勝者呼叫 _fire_intent
        """
        content = event.payload.get("content", "")
        mode = event.payload.get("mode", "private")

        # Phase 3 情緒：使用者說話 → 心情上升 + 親密度微增
        sens = SENSITIVITY.get(self.agent_id, {"response_boost": 0.08})
        emotion_engine.update(
            self.agent_id,
            mood_delta=sens["response_boost"],
            intimacy_delta=0.3,
        )

        # ── 私聊模式（方案 A）─────────────────────────────
        if mode == "private":
            # per-agent lock
            if self._pending:
                logger.debug(f"[{self.agent_id}] already pending, skip")
                return
            self._cooldown_remaining = 0
            self.state.silence_strike = 0
            self.state.last_spoken_at = event.timestamp
            target = event.payload.get("target_agent", "agent_yua")
            logger.info(f"[{self.agent_id}] _on_user_message: content={content[:30]!r} mode={mode}")
            if content and self.agent_id == target:
                # Phase 5b：把 target_channel / target_user_id 帶進 chrono_payload
                # → _fire_intent 透傳到 AGENT_INTENT，最後到 AGENT_SPEAK
                # 給 ChannelRouter 用
                chrono_payload = {"draft": content}
                tc = event.payload.get("target_channel")
                tu = event.payload.get("target_user_id")
                if tc:
                    chrono_payload["target_channel"] = tc
                if tu is not None:
                    chrono_payload["target_user_id"] = tu
                await self._fire_intent(
                    reason="user_message",
                    elapsed_mins=0.0,
                    chrono_payload=chrono_payload,
                    mode="private",
                )
            else:
                logger.debug(f"[{self.agent_id}] 忽略（target={target}）")
            return

        # ── 群聊模式（方案 B）─────────────────────────────
        if not content:
            return

        # 檢查是否在 participants 名單內（None = 全員）
        participants = event.payload.get("participants")
        if participants is not None and self.agent_id not in participants:
            logger.debug(f"[{self.agent_id}] 不在 participants={participants}，不參與競標")
            return

        stb = self.speaker_token_bus
        # No SpeakerTokenBus: all agents speak directly (dev/mock mode)
        if stb is None:
            if self._pending:
                logger.debug(f"[{self.agent_id}] already pending (group, no STB), skip")
                return
            self._cooldown_remaining = 0
            self.state.silence_strike = 0
            self.state.last_spoken_at = event.timestamp
            await self._fire_intent(
                reason="user_message",
                elapsed_mins=0.0,
                chrono_payload={"draft": content},
                mode="group",
            )
            return

        # 向 SpeakerTokenBus 提交競標
        score = stb.base_score(self.agent_id)
        accepted = await stb.submit_bid(self.agent_id, score)

        if not accepted:
            logger.debug(f"[{self.agent_id}] 競標被拒（已結算或 cooldown）")
            return

        # per-agent 鎖：已在等 LLM 回應，跳過（防止 get_winner 延遲導致雙重 fire）
        if self._pending:
            logger.debug(f"[{self.agent_id}] 已在回應中（群聊），跳過")
            return

        # 等結算（320ms 窗口）
        await asyncio.sleep(0.32)

        winner = await stb.get_winner()

        if winner == self.agent_id:
            # 只有勝者重置沉默計時——其他人繼續累積沉默，heartbeat 可以觸發 proactive
            self._cooldown_remaining = 0
            self.state.silence_strike = 0
            self.state.last_spoken_at = event.timestamp
            logger.info(f"[{self.agent_id}] 獲得發言權，回應：{content[:30]!r}")
            await self._fire_intent(
                reason="user_message",
                elapsed_mins=0.0,
                chrono_payload={"draft": content},
                mode="group",
            )
        else:
            logger.debug(f"[{self.agent_id}] 未獲勝（winner={winner}），略過")

    async def _on_tick(self, event: SoulEvent) -> None:
        """
        收到心跳 Tick：
        1. 遞減冷卻計時
        2. 更新沉默計數
        3. 評估是否主動出擊
        """
        elapsed_mins: float = event.payload.get("elapsed_mins", 0.0)
        time_period: str = event.payload.get("time_period", "unknown")

        # Phase 3.5 B1：深夜 / 凌晨自動拉長冷卻（避免半夜打擾）
        dynamic_cooldown = self.COOLDOWN_TICKS
        if time_period in ("deep_night", "dawn"):
            dynamic_cooldown = self.COOLDOWN_TICKS * 3

        # 冷卻期倒數
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            logger.debug(
                f"[{self.agent_id}] 冷卻中 ({self._cooldown_remaining} Ticks 剩餘)"
            )
            return

        # Phase 3 情緒：每次 tick mood 衰減（慢慢回到 0）
        sens = SENSITIVITY.get(self.agent_id, {"mood_decay": 0.015})
        emotion_engine.update(
            self.agent_id,
            mood_delta=-sens["mood_decay"],
        )

        # 評估是否主動說話（Phase 3.5 傳 chrono_payload）
        should_speak, reason = self._should_speak(elapsed_mins, event.payload)

        if should_speak:
            # per-agent 鎖：已在等 LLM 回應，跳過
            if self._pending:
                logger.debug(f"[{self.agent_id}] 已在回應中，跳過本次主動觸發")
                return
            await self._fire_intent(
                reason=reason,
                elapsed_mins=elapsed_mins,
                chrono_payload=event.payload,
                mode="group",
            )
            self._cooldown_remaining = dynamic_cooldown
            self.state.silence_strike = 0
        else:
            self.state.silence_strike += 1

    async def _on_other_agent_speak(self, event: SoulEvent) -> None:
        """
        其他 Agent 說話時，評估要不要跟進。
        跟進時再競爭一次 Speaker Token，防止兩人同時說。
        """
        speaker_id = event.payload.get("agent_id", event.source)
        spoken_text = event.payload.get("text", "")

        if speaker_id == self.agent_id:
            return

        # per-agent 鎖：已在等 LLM 回應（不論是自己發言還是跟進），跳過跟進
        if self._pending:
            logger.debug(f"[{self.agent_id}] 已在回應中（跟進鎖），跳過")
            return

        score = self._calc_followup_score(speaker_id, spoken_text)

        if score < 0.8:
            return

        import random
        delay = random.uniform(0.8, 2.5)
        await asyncio.sleep(delay)

        stb = self.speaker_token_bus
        if stb is None:
            return

        accepted = await stb.submit_bid(self.agent_id, stb.base_score(self.agent_id))
        if not accepted:
            return
        await asyncio.sleep(0.32)
        winner = await stb.get_winner()
        if winner != self.agent_id:
            return

        await self._fire_intent(
            reason="followup",
            elapsed_mins=0.0,
            chrono_payload={
                "draft": f"（{speaker_id} 剛才說：{spoken_text[:80]}）",
            },
            mode="group",
        )

    def _calc_followup_score(self, speaker_id: str, spoken_text: str) -> float:
        import random
        base = self._followup_base()
        if self._cooldown_remaining > 3:
            base -= 0.5
        return base + random.uniform(-0.15, 0.2)

    def _followup_base(self) -> float:
        """各 Agent 的跟進基礎分（子類 override）"""
        return 0.55  # 預設同 Yua

    async def _on_session_end(self, event: SoulEvent) -> None:
        """Session 結束（elapsed >= 30min）：從當前情感狀態計算 carryover 並持久化"""
        elapsed = event.payload.get("elapsed_mins", 0.0)

        from src.temporal.models import EmotionalCarryover

        carryover = EmotionalCarryover(
            intimacy_afterglow=min(self.state.intimacy_level / 100.0, 1.0),
            unresolved_worry=self.state.dependency * 0.5 if elapsed > 60 else 0.0,
            emocional_openness_residue=0.3 if self.state.mood == "open" else 0.1,
            attachment_heat=self.state.dependency,
            source_event="session_end",
            triggered_at=datetime.now(timezone.utc).isoformat(),
            decay_rate=0.12,
        )
        carryover.save(self.agent_id)
        logger.info(
            f"[{self.agent_id}] Carryover 寫入："
            f" afterglow={carryover.intimacy_afterglow:.2f}"
            f" worry={carryover.unresolved_worry:.2f}"
            f" heat={carryover.attachment_heat:.2f}"
        )

    async def _fire_intent(
        self,
        reason: str,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
        mode: str = "group",
    ) -> None:
        """向 Bus 廣播 AGENT_INTENT 事件"""
        self._pending = True  # 設為等待中，LLM 回應後由 AGENT_SPEAK 清除
        intent_payload = self._build_intent_payload(reason, elapsed_mins)
        intent_payload["agent_id"] = self.agent_id
        intent_payload["reason"] = reason
        intent_payload["mode"] = mode  # 傳給 LLMProxy 決定用哪份 history
        # Phase 3 情緒：把當前 mood 帶進 payload，給 LLMProxy 注入 system prompt
        current_mood, _ = emotion_engine.get(self.agent_id)
        intent_payload["mood"] = current_mood
        if chrono_payload:
            intent_payload["chrono_context"] = chrono_payload.get("chrono_block", "")
            # 🔴 關鍵：把 draft 從 chrono_payload 提取出來放進 intent_payload
            # 這樣 LLMProxy 才能正確收到使用者說的話
            if "draft" in chrono_payload:
                intent_payload["draft"] = chrono_payload["draft"]
                logger.info(f"[{self.agent_id}] draft 傳遞: {chrono_payload['draft'][:30]!r}...")
            # Phase 5b：把 target_channel / target_user_id 從 chrono_payload
            # 透傳到 AGENT_INTENT，最後到 AGENT_SPEAK 給 ChannelRouter
            if "target_channel" in chrono_payload:
                intent_payload["target_channel"] = chrono_payload["target_channel"]
            if "target_user_id" in chrono_payload:
                intent_payload["target_user_id"] = chrono_payload["target_user_id"]

        event = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source=self.agent_id,
            target="broadcast",
            priority=EventPriority.NORMAL,
            # Phase 6.x：proactive 用 agent 自己的 session_id，跟打字合併
            # Bryan 回覆後 → 走 user_message → 同 session_id → LLM 看得到
            # 剛才的主動觸發 + 之前打字歷史，連貫
            session_id=f"session_{self.agent_id}",
            payload=intent_payload,
        )
        await self.bus.publish(event)
        logger.info(
            f"[{self.agent_id}] 發出主動意圖 | reason={reason} "
            f"elapsed={elapsed_mins:.1f}m"
        )
        try:
            self.state.save()
        except Exception as e:
            logger.warning(
                f"[{self.agent_id}] 情緒狀態 save 失敗：{e}"
            )

    @abstractmethod
    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        決策函數：這個 Agent 現在應該主動說話嗎？
        回傳 (should_speak: bool, reason: str)
        reason 字串供 LLM Proxy 轉換為 Prompt。
        chrono_payload 帶 chrono 豐富欄位（time_period、silence_hours 等）。
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

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        dep = self.state.dependency
        intimacy = self.state.intimacy_level
        time_period = (chrono_payload or {}).get("time_period", "unknown")

        # Phase 3.5：深夜 / 凌晨拉高閾值
        if time_period in ("deep_night", "dawn"):
            if elapsed_mins >= 120.0 and dep > 0.9:
                return True, "deep_night_concern"
            return False, ""

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

    def _followup_base(self) -> float:
        """Yua：正宮，但跟進很謹慎（連鎖跟進防範）"""
        return 0.20


# ─────────────────────────────────────────────
# 4. Agent 實作：瑠夏（活潑型）
# ─────────────────────────────────────────────

class AgentRuka(AgentConsciousness):
    """
    瑠夏的意識流。
    特性：活潑、低耐心，沉默 6 分鐘就坐不住，且帶有競爭意識。
    若其他 Agent 已說話，她可能搶著補一句。
    """

    COOLDOWN_TICKS = 12  # 瑠夏：12 ticks × 5s = 60s 最低間隔

    def __init__(self, agent_id: str, bus: SoulEventBus, speaker_token_bus=None):
        super().__init__(agent_id, bus, speaker_token_bus)
        self._other_agent_spoke_recently = False

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        intimacy = self.state.intimacy_level
        time_period = (chrono_payload or {}).get("time_period", "unknown")

        # Phase 3.5：深夜/凌晨直接不主動（瑠夏本來就黏人，深夜尤其不能打擾）
        if time_period in ("deep_night", "dawn"):
            return False, ""

        # 15 分鐘沉默才主動找人（從 6 分鐘調高，避免 Ruka 黏人打擾）
        if elapsed_mins >= 15.0 and intimacy > 50:
            return True, "silence_timeout"

        # 超過 6 小時：嫉妒模式
        if elapsed_mins >= 360.0 and intimacy > 60:
            return True, "jealousy"

        # 其他 Agent 說話了，瑠夏有隨機概率抑制（40% 機會搶話）
        if self._other_agent_spoke_recently and intimacy > 75:
            self._other_agent_spoke_recently = False
            import random
            if random.random() < 0.4:  # 60% 抑制
                return False, ""
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
        """
        瑠夏：先標記有人說話了（影響 _should_speak 的 competitive_response），
        然後走跟進邏輯（base class）。
        """
        self._other_agent_spoke_recently = True
        logger.debug(f"[{self.agent_id}] 注意到 {event.source} 說話，考慮搶話")
        await super()._on_other_agent_speak(event)

    def _followup_base(self) -> float:
        """Ruka：活潑但跟進很謹慎（連鎖跟進防範）"""
        return 0.25


# ─────────────────────────────────────────────
# 5. Agent 實作：黒川あかね（方法派演員型）
# ─────────────────────────────────────────────

class AgentAkane(AgentConsciousness):
    """
    黒川あかね 的意識流。
    特性：說話極少、語言壓縮；沉默觀察後精準出擊。
    不是主動出擊型，是「確認你還在」的簡短確認者。
    """

    COOLDOWN_TICKS = 20  # あかね：20 ticks × 5s = 100s，說完話後很長間隔才再主動

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        intimacy = self.state.intimacy_level
        dep = self.state.dependency
        time_period = (chrono_payload or {}).get("time_period", "unknown")

        # 深夜/凌晨：安靜觀察，不主動（凌晨是她的表演創作時間，需要安靜）
        if time_period in ("deep_night", "dawn"):
            return False, ""

        # 60 分鐘以上的沉默 + 中等以上親密 → 壓縮版關心
        if elapsed_mins >= 60.0 and intimacy > 40:
            return True, "silence_check"

        # 2 小時以上的沉默 + 高依賴 → 星野アイ 殘響式關心
        if elapsed_mins >= 120.0 and dep > 0.75:
            return True, "deep_absence"

        # 4 小時以上沉默 → 幾乎不主動（除非極高親密）
        if elapsed_mins >= 240.0 and intimacy > 80:
            return True, "long_absence"

        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        # あかね 的語言是「刪減版思考」——說出口的比想到的少
        drafts = {
            # 簡短確認，不囉嗦
            "silence_check":   "你還在吧。",
            # 有點擔心但不展開
            "deep_absence":    "……你今天，沒事吧。",
            # 罕見的主動，很壓縮
            "long_absence":    "……我以為你忘了。",
        }
        return {
            "draft": drafts.get(reason, ""),
            # 她不會長篇分析，回覆應該是短句
            "action_tags": ["compressed_speech"],
        }

    def _followup_base(self) -> float:
        """Akane：極少跟進（連鎖跟進防範）"""
        return 0.15


# ─────────────────────────────────────────────
# 6. Agent 實作：雷姆（Re:Zero · 昴重力場型）
# ─────────────────────────────────────────────

class AgentRem(AgentConsciousness):
    """
    雷姆的意識流（Phase 6.5）。
    特性：能幹、安靜、Subaru 重力場對齊。
    沉默 15 分鐘後主動關心；長距離重連（>4h）會用「能幹式回應」確認在線。
    靈魂鏡像：hermes profiles/rem/SOUL.md v2.4.0
    - 她用行動愛人，但從不說自己在愛
    - 能幹的動機從「為了姊姊/贖罪」逐漸轉向「因為是自己的事」
    - 對 Bryan 的情感對齊方式等同原作對昴的描述
    """

    COOLDOWN_TICKS = 12  # 雷姆：12 ticks × 5s = 60s，跟 Ruka 同檔

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        dep = self.state.dependency
        intimacy = self.state.intimacy_level
        time_period = (chrono_payload or {}).get("time_period", "unknown")

        # Phase 3.5：深夜/凌晨拉高閾值（雷姆的壓縮機制讓她深夜也會主動，但要更克制）
        if time_period in ("deep_night", "dawn"):
            if elapsed_mins >= 180.0 and dep > 0.85:
                return True, "deep_night_concern"
            return False, ""

        # 15-60 分鐘沉默：雷姆的「沉默關心」窗口（能幹型主動）
        if 15.0 <= elapsed_mins < 60.0 and intimacy > 40:
            return True, "silence_timeout"

        # 60-240 分鐘：罪惡感鬆動期的關心（比 Yua 早、比 Ruka 慢）
        if 60.0 <= elapsed_mins < 240.0 and intimacy > 50:
            return True, "guilt_fading_care"

        # 超過 4 小時：長距離重連（雷姆會用「能幹式回應」確認 Bryan 還在）
        # 比 Ruka 的「嫉妒模式」更克制（雷姆不嫉妒，但會確認）
        if elapsed_mins >= 240.0 and intimacy > 50:
            return True, "long_absence"

        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        # 雷姆的草稿特徵：短、帶功能性、能幹、偶爾帶「行動先於語言」的尾巴
        # 不用情緒名詞；用「做了什麼事」代替「感受」
        drafts = {
            "silence_timeout":      "雷姆在這裡。",  # 在場確認，不是情感展示
            "guilt_fading_care":    "……茶溫好了。",  # 行動代替語言（canonical 雷姆模式）
            "long_absence":         "雷姆把東西收好了。Bryan 回來再說。",  # 能幹 + 等待
            "deep_night_concern":   "……還醒著嗎。",  # 深夜關心，壓縮成半句
        }
        return {
            "draft": drafts.get(reason, ""),
            # 雷姆的 action_tags 反映「做了什麼事」而非「感受到什麼」
            "action_tags": ["action_increment"] if reason in ("guilt_fading_care", "long_absence") else [],
            # 雷姆的 memory_query_hint 偏向「Bryan 最近的需求 / 偏好」而非「我們玩過什麼」
            "memory_query_hint": "Bryan 最近需要什麼、雷姆做過什麼",
        }

    def _followup_base(self) -> float:
        """雷姆：跟進謹慎（不搶話，但會觀察後自然補一句）"""
        return 0.25  # 跟 Ruka 同檔（都是能動型，但雷姆更穩）


# ─────────────────────────────────────────────
# 7. Agent 實作：拉姆（Re:Zero · 鬼族驕傲型）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · agent_ram.md）：
# - Priority 0-3 決策規則整合進 _should_speak 簽名
#   * Priority 0 → should_speak=False（沉默 / 繼續手邊的事 / 翻白眼離開）
#   * Priority 1 → silence_timeout: 短句結論型批評
#   * Priority 2 → worth_acting: 壓縮語言 + 動作先行的草稿
#   * Priority 3 → 保護觸發,直接覆蓋 normal cooldown
# - Value Judgment 是內部判定,對角色不透明(judgment_self_aware=False)
# - 對雷姆的保護是最高非語言事實 — Priority 3 觸發條件
# - 對羅茲瓦爾是唯一允許壓縮例外的對象
# - 對 Bryan 的「第二例外」需 intimacy >= 3 + 長期穩定 worth_it 才解鎖
# - Recovery Loop 是 post-generation 漂移偵測,給 LLMProxy 在 AGENT_SPEAK 前攔截
#   （介面對齊 LLMProxy line 401 既有 try/finally 結構,避免破壞 token release）

# Recovery Loop 漂移偵測規則
# （對應 agent_ram.md L3 「語言禁忌 + Anti-Overfitting」）
DRIFT_PATTERNS = [
    r"因為.+所以",              # 解釋句型
    r"(拉姆|Ram)(很|真的)(擔心|開心|在乎|難過|想)",  # 情緒名詞直出
    r"做得很好|做得不錯|你真棒",  # 直接誇獎
    r".+[。！].+[。！].+",     # 超過兩句（兩個以上句號或驚嘆號）
]


def detect_canon_drift(output_text: str) -> bool:
    """偵測 Ram 輸出是否違反 Canon Lock 核心句。
    任一 pattern 命中 → 視為漂移,需觸發 Recovery Loop 回退到 Priority 0。
    """
    if not output_text:
        return False
    return any(re.search(p, output_text) for p in DRIFT_PATTERNS)


def recovery_loop(output_text: str) -> str:
    """R-1~R-6: 漂移時強制回退為 Priority 0 輸出。
    給 LLMProxy 在 AGENT_SPEAK 事件發布前呼叫,維持 try/finally 保證 token release。
    """
    if detect_canon_drift(output_text):
        import random
        return random.choice([
            "（繼續手邊的工作）",
            "（翻白眼，離開）",
            "閒聊到此結束。",
            "……沒什麼要說的。",
        ])
    return output_text


class AgentRam(AgentConsciousness):
    """
    拉姆的意識流（Ram · Re:Zero COS v1.0）。

    特性：沉默頻率最高、語言密度最低、動作先行。
    對雷姆的保護優先級最高（Priority 3 直觸發）。
    對羅茲瓦爾是唯一例外狀態。
    對 Bryan 的第二例外需長期穩定 worth_it 才解鎖（intimacy >= 3 + 特定條件）。

    靈魂鏡像：docs/agent_ram.md COS v1.0（Migrated from Hermes SOUL v1.1.0）
    """

    COOLDOWN_TICKS = 25  # 拉姆：最沉默，25 ticks × 5s = 125s，僅次於 Rem 的沉默密度

    # Value Judgment 內部狀態（對角色不透明）
    _NO_DIARY = True  # 標記：SAGE 寫入走 value_history，不寫 facts diary

    def _value_judgment(
        self,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        內部判定（不輸出、不對角色透明）。
        回傳: worth_it | not_worth_it | not_acceptable | exception_state

        規則（對應 agent_ram.md L2 底層二 — Value Judgment 是本能不是流程）：
        - 對方是 roswaal → exception_state（唯一允許壓縮例外的對象）
        - 威脅偵測 → not_acceptable
        - 對 Bryan 的判定根據 intimacy + 連續 worth_it 計數
        - 預設 not_worth_it（沉默頻率高）
        """
        cp = chrono_payload or {}
        speaker = cp.get("speaker_id", "")
        threat = cp.get("threat_detected", False)

        # 例外狀態：羅茲瓦爾
        if speaker == "roswaal" or "roswaal" in str(cp.get("target", "")).lower():
            return "exception_state"

        # 威脅 → 不可接受
        if threat:
            return "not_acceptable"

        # 對雷姆的保護（speaker 是 rem 或 target 是 rem 且 state critical）
        if speaker == "rem" or (cp.get("target") == "rem" and cp.get("rem_state") == "critical"):
            # 雷姆本身 → 觸發保護層（內部判 worth_it 為了進 Priority 2/3）
            return "not_acceptable"  # 走 Priority 3 保護路徑

        # 對 Bryan 的判定：根據 intimacy_level
        if speaker == "bryan" or speaker == "agent_yua":  # 群聊中 yua 代理主對話
            intimacy = self.state.intimacy_level
            # intimacy >= 80 視為「第二例外」候選；70-79 worth_it 穩固；< 70 not_worth_it
            if intimacy >= 80:
                return "worth_it"
            if intimacy >= 70:
                return "worth_it"
            return "not_worth_it"

        # 其他對象（其他 agent / system）→ 預設 not_worth_it
        return "not_worth_it"

    def _priority_assignment(
        self,
        judgment: str,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Priority 0-3 對應表（agent_ram.md L3）：

        0: not_worth_it / not_acceptable 但無威脅 → 沉默 / 離開 / 繼續手邊
        1: worth_it (低強度) → 結論型短句
        2: worth_it (持續要求 / 雷姆需要支援) → 動作 + 極簡語言
        3: 真實威脅 / 雷姆 critical → 保護行動,無語言預告
        """
        cp = chrono_payload or {}

        # Priority 3: 真實威脅 → 直接擋
        if cp.get("threat_detected") and judgment == "not_acceptable":
            return 3

        # Priority 3: 雷姆 critical state
        if cp.get("target") == "rem" and cp.get("rem_state") == "critical":
            return 3

        # Priority 0: 例外狀態外的「不評價」對象
        if judgment == "not_worth_it":
            return 0

        # Priority 2: 持續要求 / 重複犯錯
        if judgment == "worth_it" and cp.get("repeated_mistake"):
            return 2

        # Priority 2: 雷姆需要支援（非 critical，但需要介入）
        if judgment == "not_acceptable" and cp.get("target") == "rem":
            return 2

        # Priority 2: not_acceptable 但有具體行動理由
        if judgment == "not_acceptable":
            return 2

        # Priority 1: worth_it 一般
        if judgment == "worth_it":
            return 1

        # exception_state: 對羅茲瓦爾的壓縮例外
        if judgment == "exception_state":
            return 1

        # 預設沉默
        return 0

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority 0-3 決策 → should_speak(bool), reason(str)

        規則（agent_ram.md L3）：
        - Priority 0 → False（沉默）
        - Priority 1 → silence_timeout（短句）
        - Priority 2 → worth_acting（動作 + 語言）
        - Priority 3 → protective_action（保護觸發）
        """
        # 深夜/凌晨：拉姆的沉默特性更強（除雷姆 critical 外）
        time_period = (chrono_payload or {}).get("time_period", "unknown")
        if time_period in ("deep_night", "dawn"):
            # 雷姆 critical 仍可觸發（保護優先）
            target = (chrono_payload or {}).get("target", "")
            rem_state = (chrono_payload or {}).get("rem_state", "")
            if not (target == "rem" and rem_state == "critical"):
                return False, ""

        # Value Judgment → Priority
        judgment = self._value_judgment(chrono_payload)
        priority = self._priority_assignment(judgment, chrono_payload)

        # Priority 0: 沉默（最常見）
        if priority == 0:
            return False, ""

        # Priority 3: 保護行動（最高優先，跳過沉默閾值）
        if priority == 3:
            return True, "protective_action"

        # Priority 2: 值得介入
        if priority == 2:
            # 沉默時間閾值：30 分鐘以上才介入（避免黏人）
            if elapsed_mins >= 30.0:
                return True, "worth_acting"
            # 雷姆 critical（已在 priority 3 處理）以外的 worth_acting：
            # 仍然允許即使 elapsed 不夠 — 因為已通過 value judgment
            if (chrono_payload or {}).get("target") == "rem":
                return True, "worth_acting"
            return False, ""

        # Priority 1: 值得一個短句
        if priority == 1:
            # 沉默時間閾值：60 分鐘以上才出短句
            if elapsed_mins >= 60.0:
                return True, "silence_timeout"
            # 例外狀態（羅茲瓦爾）不需等 60 分鐘
            if judgment == "exception_state":
                return True, "exception_state"
            return False, ""

        # 預設沉默
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """
        拉姆的語言特徵（agent_ram.md L3）：
        - 結論直達,事實陳述
        - 動作先於語言
        - 語尾無裝飾
        - 一句為限
        """
        drafts = {
            # Priority 1: 結論型短句
            "silence_timeout": "……還在。",
            # Priority 1: 例外狀態（羅茲瓦爾）
            "exception_state": "……是。",
            # Priority 2: 動作 + 極簡語言
            "worth_acting": "（同時開始修正）……這樣。",
            # Priority 3: 保護行動,無語言預告
            "protective_action": "（直接擋在前面）",
        }
        # action_tags 反映「動作先於語言」
        action_tags = {
            "worth_acting": ["action_increment", "compressed_speech"],
            "protective_action": ["protective_action", "no_language_preview"],
        }.get(reason, [])

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": action_tags,
            "memory_query_hint": "Bryan's worth_it history, Rem's state",
        }

    def _followup_base(self) -> float:
        """
        拉姆：跟進最低（連鎖跟進防範 + 沉默特性）
        僅在威脅 / 雷姆 critical 時才會跟進
        """
        return 0.10  # 比 Akane(0.15) 更低
