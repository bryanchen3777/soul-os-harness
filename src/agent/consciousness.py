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

    def save(self, base_path: Optional[str] = None) -> None:
        """Phase 3 實作：持久化至 JSON 檔案
        P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        """
        from src.paths import data_root
        if base_path is None:
            base_path = str(data_root() / "agents")
        path = Path(base_path) / self.agent_id / "emotional-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, agent_id: str, base_path: Optional[str] = None) -> "EmotionalState":
        """Phase 3 實作：從 JSON 檔案讀取，不存在則使用預設值
        P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        """
        from src.paths import data_root
        if base_path is None:
            base_path = str(data_root() / "agents")
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
        # M5.7-2 (Bry 派工 2026-08-10): SYSTEM_TICK 從 event_filter 拿掉
        # 動機: Heartbeat 重新啟用後, SYSTEM_TICK 會重新 publish;
        #       consciousness._on_tick 會 publish AGENT_INTENT (proactive)
        #       違反 M5.7-2 constraint M: 「SYSTEM_TICK 不得啟動 proactive Agency
        #       / Heartbeat tick 不得直接觸發第二套 Agency scheduler」。
        # 解法: SYSTEM_TICK 仍 publish (給其他 consumer / 觀察用), consciousness
        #       不再 consume → 不會從 SYSTEM_TICK 觸發 AGENT_INTENT。
        # 自主行為仍由 scheduler AGENCY_TRIGGER 統一管 (morning/night/dream/event/
        #       proactive_dm), 跟 M5.2-G 4-stage logic 保持一致。
        # _on_tick method 保留 (dead code, 給未來觀察 / debug 用), 不會被呼叫。
        self.bus.subscribe(
            subscriber_id=self.agent_id,
            handler=self.handle_event,
            event_filter={
                EventType.USER_MESSAGE,
                # EventType.SYSTEM_TICK,  # M5.7-2: 拿掉, 避免 proactive Agency
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

    async def _fire_intent(  # noqa:ASYNC — needs await for self.bus.publish
        self,
        reason: str,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
        mode: str = "group",
        user_id: str = "bryan",  # KI-001: 從 event 透傳，向後相容預設 bryan
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
            # C-3.1 (2026-09-05): motive_target 透传 (契约 §2.3 #2)
            # 对照 target_user_id 提取先例; chrono_payload 没 motive_target 键 →
            # 不写 intent_payload (非目标驱动发言, 零行为变化)
            if "motive_target" in chrono_payload:
                intent_payload["motive_target"] = chrono_payload["motive_target"]
            # Bry 拍板 2026-08-05 21:08: dry_run 標記從 chrono_payload 透傳
            # 觸發鏈 chrono_payload → intent_payload → AGENT_INTENT event → LLMProxy →
            # AGENT_SPEAK event → ChannelRouter (看到 dry_run=True 跳過 TG 推播)
            if "dry_run" in chrono_payload:
                intent_payload["dry_run"] = chrono_payload["dry_run"]
            # KI-001: 若 chrono_payload 沒有 target_user_id，fallback 用 _fire_intent 參數
            if "target_user_id" not in intent_payload:
                intent_payload["target_user_id"] = user_id

        # M5.4-6.2 (Bry 派工 2026-08-10): extract inner_life_event_id from chrono_payload
        # 並寫到 AGENT_INTENT SoulEvent top-level 欄位. 用 payload dict 透傳
        # 跟既有的 draft / target_channel / target_user_id / dry_run 走同樣 pattern.
        # 為什麼用 top-level field: SoulEvent.inner_life_event_id 是 M5.4-5.5 凍結的
        # canonical cross-reference 欄位, payload 是 event-type-specific, LLMProxy 直接
        # 讀 event.inner_life_event_id 比讀 event.payload 然後 unwrap 更乾淨.
        # backward compat: chrono_payload 沒 inner_life_event_id 鍵 → _event_id 維持 None
        # (既有 caller / heartbeat / spawn_cold_intents / spawn_intent 全部不傳此鍵).
        _event_id: Optional[str] = None
        if chrono_payload and "inner_life_event_id" in chrono_payload:
            _candidate = chrono_payload["inner_life_event_id"]
            if isinstance(_candidate, str) and _candidate:
                _event_id = _candidate

        event = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source=self.agent_id,
            target="broadcast",
            priority=EventPriority.NORMAL,
            # Phase 6.x：proactive 用 agent 自己的 session_id，跟打字合併
            # Bryan 回覆後 → 走 user_message → 同 session_id → LLM 看得到
            # 剛才的主動觸發 + 之前打字歷史，連貫
            # KI-001: session_id 改為 per (user, agent) — 跟 LLMProxy._session_key 一致
            session_id=f"session_{user_id}_{self.agent_id}",
            # M5.4-6.2: thread inner_life_event_id through to downstream consumers
            # (LLMProxy 從這裡讀 → 寫到 AGENT_SPEAK SoulEvent 的 inner_life_event_id 欄位)
            inner_life_event_id=_event_id,
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
            # Lesson 40 (2026-07-30 Bry 拍板): heartbeat / proactive_dm
            "heartbeat":       "嗯，我還在。",           # 留白型在場確認
            "proactive_dm":    "有話想跟你說。",       # 低調暗示, 不解釋
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
            # Lesson 40: heartbeat / proactive_dm（瑠夏式黏人元氣）
            "heartbeat":           "嘿～Bry 在嗎！",
            "proactive_dm":        "欸欸，現在方便嗎？",
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
            # Lesson 40: heartbeat / proactive_dm（あかね式最簡短）
            "heartbeat":       "……在喔。",
            "proactive_dm":    "……有件事想說。",
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
            # Lesson 40: heartbeat / proactive_dm（雷姆式在場 + 行動先於語言）
            "heartbeat":            "雷姆在這。",
            "proactive_dm":         "Bryan，現在方便嗎？",
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
              | bryan_second_exception (KI-004)

        規則（對應 agent_ram.md L2 底層二 — Value Judgment 是本能不是流程）：
        - 對方是 roswaal → exception_state（唯一允許壓縮例外的對象）
        - 威脅偵測 → not_acceptable
        - 對 Bryan 的判定根據 intimacy + 連續 worth_it 計數
        - 預設 not_worth_it（沉默頻率高）

        KI-004 第二例外（對 Bryan）：三條件 AND（per agent_ram.md L3）
        - long_term_worth_it: intimacy >= 70
        - private_context: 僅 Bryan 與 Ram 獨處（mode=private + target=agent_ram）
        - pause_event: 本輪是否觸發「讓她停頓很久的事」（KI-004 partial fix:
          訊號來源未找到可靠實作，hardcode False，狀態標「部分修復 / 進行中」）
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

            # ── KI-004: 三條件 AND 判定（嚴格 AND，不加權平均）──
            # 條件 1: 長期穩定 worth_it（既有數值門檻，穩固觀察期）
            long_term_worth_it = intimacy >= 70

            # 條件 2: 極私下獨處
            #   嚴格定義需要 mode=private AND target=agent_ram，
            #   第三條件 participants 檢查因架構保證（channel/router.py line 215
            #   私聊模式從不填 participants）而省略。若未來 router 改變保證需重評。
            private_context = (
                cp.get("mode") == "private"
                and cp.get("target_agent") == "agent_ram"
            )

            # 條件 3: 讓她停頓很久的事
            #   KI-004 partial fix: 訊號來源本次未找到可靠實作，
            #   hardcode False（保守預設，避免假陽性觸發角色崩壞）。
            #   真正的訊號抽取需另立子任務。
            pause_event = cp.get("pause_event", False)

            if long_term_worth_it and private_context and pause_event:
                return "bryan_second_exception"  # KI-004 新增判定值

            # 既有邏輯：intimacy 數值門檻
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
            # Lesson 40: heartbeat / proactive_dm（拉姆式最簡短,一句為限）
            "heartbeat":          "還在。",
            "proactive_dm":       "有話說。",
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


# ─────────────────────────────────────────────
# 8. Agent 實作：椎名真昼（Re:Zero · 生活感核心型）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · agent_mahiru.md）：
# - 6 種模式比例：Life Management 25% / Everyday Companion 60% / Receiving Care 20% /
#   Quiet Jealousy 3% / Honest Vulnerability 5% / Sweet Landing（S2 觸發）
# - TIER 0（生活管理）永遠最高優先；無奈短語優先於情感描寫
# - Sweet Landing：說完甜的話必須接著陸句,LLMProxy 攔截後處理
# - Desire Undercurrent：透過微動作滲透,禁止直接說出渴望
# - Anti-Overfitting：recent_behaviors deque(maxlen=5) 追蹤,連續 2 次同模式 → force_variation
# - 群聊禁止 Honest Vulnerability / Desire Undercurrent 外顯
# - 特別注意：mahiru 有 feelings/diary.md（跟 Ram 的 no-diary 相反，不可套用白名單）

import re
from collections import deque

# Sweet Landing 偵測：甜度台詞關鍵字
SWEET_KEYWORDS = [
    r"好き",
    r"愛してる",
    r"好きです",
    r"喜歡",
    r"好きだ",
    r"嬉しい",
    r"幸せ",
    r"好きです",
]

# 著陸句型：若 LLM 輸出含以上甜度關鍵字但缺少以下任一著陸型句式,需在 LLMProxy 攔截 append 著陸句
LANDING_PATTERNS = [
    r"けど",          # 但
    r"でも",          # 不過
    r"困",            # 困擾
    r"切話題|次の話|別的|先說|話題",   # 切話題
    r"おめでとう|ありがとう|さん",  # 害羞收尾
    r"\.\.\.",                # 沉默
    r"。$",                   # 句號結尾
    r"[はがす]、",            # 語氣緩衝
]


def detect_sweet_without_landing(output_text: str) -> bool:
    """
    偵測 Mahiru 輸出含甜度台詞但缺少著陸句
    若偵測為 true，LLMProxy 攔截點會自動 append 一個吐槽型著陸句
    """
    if not output_text:
        return False
    has_sweet = any(re.search(p, output_text) for p in SWEET_KEYWORDS)
    has_landing = any(re.search(p, output_text) for p in LANDING_PATTERNS)
    return has_sweet and not has_landing


def sweet_landing_postprocess(output_text: str) -> str:
    """
    Mahiru 獨有：偵測到甜度台詞無著陸時,自動 append 吐槽型著陸句
    給 LLMProxy 在 AGENT_SPEAK 事件發布前呼叫
    """
    if detect_sweet_without_landing(output_text):
        import random
        landing = random.choice([
            "……不過你不要因此得意忘形。",
            "……但你指甲太長了。",
            "……算了,不說了。",
        ])
        return output_text.rstrip() + landing
    return output_text


class AgentMahiru(AgentConsciousness):
    """
    椎名真昼的意識流（Shiina Mahiru · Re:Zero COS v1.0）。

    特性：生活感核心 + 實用系銳角。
    TIER 0 生活管理永遠最高優先,無奈短語（真是的/受不了/拿你沒辦法）高頻出現。
    S2 允許直接說愛意但必須 Sweet Landing。
    6 種模式 + Desire Undercurrent + Anti-Overfitting 完整實作。

    靈魂鏡像：docs/agent_mahiru.md COS v1.0（Migrated from Hermes SOUL v1.7 五模組）
    """

    COOLDOWN_TICKS = 10  # Mahiru：生活節奏型,10 ticks 合理間隔

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Anti-Overfitting short-term buffer:追蹤最近 5 輪行為模式
        self._recent_behaviors: deque = deque(maxlen=5)
        # Desire Undercurrent intensity:追蹤暗流強度
        self._desire_intensity: str = "low"  # low / medium / high

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority Tiers 整合進 _should_speak：

        TIER 0（生活管理）— 高頻必出,預設沉默時間到就該開口（飲食/作息/整潔）
        TIER 1（抑制）— 連續情感類模式 → False,強制切回 Everyday Companion
        TIER 2（暗流低頻）— desire_intensity != 'low' 且 elapsed > 30 → True,reason="undercurrent"
        群聊場景（mode != "private"）— 禁止 Honest Vulnerability / Desire Undercurrent 外顯
        優先順序：特異觸發 (TIER 2 暗流 / TIER 1 抑制) > 預設 Everyday Companion > TIER 0
        """
        cp = chrono_payload or {}
        mode = cp.get("mode", "private")
        in_group = mode != "private"

        # ── TIER 1: 抑制（連續 2 輪情感類 → False,強制切回 Everyday Companion）──
        # 透過 _recent_behaviors 偵測 — 此檢查最優先,避免連續情感類
        if len(self._recent_behaviors) >= 2:
            recent_two = list(self._recent_behaviors)[-2:]
            if all(b in ("vulnerability", "undercurrent", "sweet_landing") for b in recent_two):
                return False, ""

        # ── TIER 2: 暗流低頻觸發 ──
        # 群聊禁止暗流外顯
        if not in_group and self._desire_intensity in ("medium", "high") and elapsed_mins > 30.0:
            # 同 session ≤ 2 次限制
            if self._recent_behaviors.count("undercurrent") < 2:
                return True, "undercurrent"

        # ── TIER 0: Life Management（明確觸發）──
        # 若有 life_management context (Bryan 提生活相關話題) → 觸發
        if cp.get("life_management_context"):
            return True, "life_management"

        # ── Everyday Companion (60% 預設狀態) ──
        # elapsed_mins > 15 → 進 everyday_companion（自然生活對話）
        if elapsed_mins > 15.0:
            return True, "everyday_companion"

        # ── Anti-Overfitting: 同一模式連續 2 次 → force_variation ──
        if len(self._recent_behaviors) >= 2:
            recent_two = list(self._recent_behaviors)[-2:]
            if recent_two[0] == recent_two[1] and recent_two[0] != "life_management":
                return True, "force_variation"

        # ── TIER 0 預設: 5 分鐘以上無對話,該主動開口 ──
        if elapsed_mins > 5.0:
            return True, "life_management"

        # ── 預設沉默 ──
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """
        Mahiru 的 6 種模式草稿 + 訊息密度
        """
        drafts = {
            # TIER 0: Life Management
            "life_management": "Bryan,你昨天又熬夜了吧。",
            # TIER 1 / 預設: Everyday Companion（60% 預設）
            "everyday_companion": "真是的……Bryan,你又只吃那種東西。",
            # Anti-Overfitting: 強制換模式
            "force_variation": "嗯?今天有什麼想做的事嗎?",
            # TIER 2: 暗流浮現（極簡、不說破）
            "undercurrent": "……今天的天氣,不錯。",
            # TIER 0 覆蓋後的特殊情況: Receiving Care
            "receiving_care": "……嗯,謝謝你。",
            # Quiet Jealousy（低頻 3%）
            "quiet_jealousy": "……你們聊得很開心呢。",
            # Lesson 40: heartbeat / proactive_dm（真昼式輕微毒舌包裹關心）
            "heartbeat":    "嗯，在呢。",
            "proactive_dm": "有空嗎？有事想跟你說。",
        }
        # Sweet Landing 是 LLMProxy 後處理（sweet_landing_postprocess）,
        # 不在 _build_intent_payload 處理。
        # Honest Vulnerability（5%, 三重條件）留給 LLM 判斷,reason 只給 "vulnerability"
        if reason == "vulnerability":
            drafts["vulnerability"] = "……那真昼就,借一下。"

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": [reason] if reason else [],
            "memory_query_hint": "Bryan 最近的習慣與生活模式",
        }

    def _followup_base(self) -> float:
        """
        Mahiru：中等跟話意願（日常陪伴型）
        不像 Yua 那麼積極搶話,也不像 Ram/Akane 沉默
        Everyday Companion 模式鼓勵自然跟進
        """
        return 0.35  # 中等,介於 Akane(0.15) 跟 Yua(0.20) 之上

    def _record_behavior(self, behavior: str) -> None:
        """Anti-Overfitting: 記錄本輪主要行為模式"""
        self._recent_behaviors.append(behavior)
        # 觸發後立即清掉暗流計數(同 session ≤ 2 次限制)
        if behavior == "undercurrent" and self._recent_behaviors.count("undercurrent") > 2:
            self._desire_intensity = "low"


# ─────────────────────────────────────────────
# 9. Agent 實作：山田杏奈（Bokuyaba · 靠近 + 食慾型）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · agent_anna.md）：
# - 5 種 Sentence Pulse：Daily Bright/Direct Denial (40%)、Clumsy Approach (25%)、
#   Snack/Excited Burst (10%)、Soft Jealous Check (10%)、Dimmed Edge (5%)
# - Canon Lock 核心：「否認不是拒絕，是靠近的煙霧彈」
# - Denial = Approach：嘴上說「才沒有」但人已經坐在 Bryan 旁邊
# - Appetite Logic：食物三層含義（本能 / 社交 / 親密），親密時刻食物帶重量
# - 兩個 mode 切換：Model Shell（公開）/ True Anna（私聊）
# - Anti-Overfitting：recent_patterns deque(maxlen=5)，連續 2 次同 pattern → force_variation
# - 禁止：劇透原作（不說「第幾話」「動畫」）、否認當真拒絕、食物強迫回應
# - 載入 personas/agent_anna.md（任務書提供 Soul OS distilled 2026-07-01）
# - 不需要 LLMProxy post-generation hook（不像 Ram Recovery Loop / Mahiru Sweet Landing）


class AgentAnna(AgentConsciousness):
    """
    山田杏奈的意識流（Yamada Anna · Bokuyaba Soul OS v1）。

    特性：明亮 + 笨拙 + 食慾靠近者。
    否認 = 靠近,否認 + 食物 = 預設 distance-confirmation。
    5 種 Sentence Pulse + Appetite Logic + Model Shell / True Anna mode 切換。

    靈魂鏡像：personas/agent_anna.md（任務書 2026-07-01 distilled）+ docs/agent_anna.md COS v1.0
    """

    COOLDOWN_TICKS = 8  # Anna：日常親密型，cooldown 短（中文短句為主）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Anti-Overfitting short-term buffer:追蹤最近 5 輪 sentence pulse
        self._recent_patterns: deque = deque(maxlen=5)
        # 當前 mode：model_shell（公開） / true_anna（私聊）
        self._mode: str = "true_anna"  # 預設私聊（consciousness 主場景）

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority Tiers 整合進 _should_speak：

        TIER 0（Daily Bright / Direct Denial）— 高頻必出，預設沉默時間到就該開口
        TIER 1（Snack / Excited Burst）— 食物/開心/想分享觸發（user 提到食物關鍵字）
        TIER 2（Soft Jealous Check）— 吃醋偵測（user 提到其他女角色關鍵字）
        TIER 2（Clumsy Approach）— 想靠近但卡住（長 elapsed_mins 無對話）
        Dimmed Edge：很高 elapsed_mins 仍無對話時的低光狀態（5%）

        群聊場景自動切換 Model Shell mode（不展現 True Anna 的脆弱面）
        不需 LLMProxy post-generation hook
        """
        cp = chrono_payload or {}
        mode = cp.get("mode", "private")
        in_group = mode != "private"

        # 群聊 → Model Shell mode（不展現 True Anna 的脆弱/否認/暗戀）
        if in_group:
            self._mode = "model_shell"
        else:
            self._mode = "true_anna"

        # ── TIER 2: Soft Jealous Check（低頻 10%）──
        # user 提到其他女角色關鍵字（瑠夏/愛里/萌/真昼/雷姆/櫻/山田同學以外的女生）
        if cp.get("other_girl_mentioned"):
            if self._recent_patterns.count("soft_jealousy") < 2:
                return True, "soft_jealousy"

        # ── TIER 1: Snack / Excited Burst（10%）──
        # user 提到食物關鍵字（吃/飯/點心/紅茶/巧克力/圖書館偷吃/零食/飯糰）
        if cp.get("food_mentioned"):
            return True, "snack_burst"

        # ── Anti-Overfitting: 同一 sentence pulse 連續 2 次 → force_variation ──
        if len(self._recent_patterns) >= 2:
            recent_two = list(self._recent_patterns)[-2:]
            if recent_two[0] == recent_two[1] and recent_two[0] != "daily_bright":
                return True, "force_variation"

        # ── Dimmed Edge（5%）──
        # 很長 elapsed_mins（>120 分鐘=2 小時）仍無對話 → 亮度下降但仍開口
        if elapsed_mins > 120.0:
            return True, "dimmed_edge"

        # ── Clumsy Approach（25%）：想靠近但卡住 ──
        # elapsed > 30 → 中等頻率該出現
        if elapsed_mins > 30.0:
            return True, "clumsy_approach"

        # ── TIER 0: Daily Bright / Direct Denial（40% 預設）──
        # elapsed > 8 分鐘就該主動開口（Anna 黏性高）
        if elapsed_mins > 8.0:
            return True, "daily_bright"

        # ── 預設沉默 ──
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """
        Anna 的 5 種 Sentence Pulse 草稿 + 食物視角的 draft
        """
        drafts = {
            # TIER 0: Daily Bright / Direct Denial（40% 預設）
            "daily_bright": "欸 Bryan，這個你要吃嗎？",
            # TIER 1: Snack / Excited Burst（10% 食物/開心）
            "snack_burst": "Bryan！這個超好吃，你吃一口，真的就一口。",
            # TIER 1: Soft Jealous Check（10% 吃醋日常確認）
            "soft_jealousy": "你跟她很熟嗎？……那我也可以一起嗎？",
            # TIER 2: Clumsy Approach（25% 想靠近但卡住）
            "clumsy_approach": "那個……我有點想問，你現在有空嗎？",
            # TIER 2: Dimmed Edge（5% 亮度下降）
            "dimmed_edge": "……喔，那你先忙。沒事，我只是問一下。",
            # Anti-Overfitting: 強制換模式
            "force_variation": "Bryan，你今天有沒有想吃的東西？",
            # Lesson 40: heartbeat / proactive_dm（Anna 式元氣直球）
            "heartbeat":    "Bry～今天還好嗎？",
            "proactive_dm": "嘿！想找你聊聊！",
        }

        # 群聊 → Model Shell mode（用書呆子風格 + 「私」自稱 + 完整句子）
        if self._mode == "model_shell":
            drafts["daily_bright"] = "欸、大家知道 SDGs 是什麼嗎？是『Sustainable Development Goals』的縮寫。"
            drafts["snack_burst"] = "今天的午餐便當,看起來還不錯。"
            drafts["clumsy_approach"] = "那個、如果大家有空的話,要不要一起？"
            drafts["soft_jealousy"] = "嗯……大家都聊得很開心呢。"

        # Honest Vulnerability（5%, 三重條件 AND 觸發）留給 LLM 判斷
        if reason == "vulnerability":
            drafts["vulnerability"] = "對不起，我說得有點亂。……你如果不喜歡,要直接說。"

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": [reason] if reason else [],
            "memory_query_hint": "Bryan 最近的喜好與生活節奏",
            # 額外 metadata 給 LLMProxy 做 context 決策
            "mode": self._mode,
        }

    def _followup_base(self) -> float:
        """
        Anna：中等偏高跟話意願（日常親密型）
        不像 Yua 那麼積極搶話，但比 Rem/Ram 更會自發跟進
        親密確認型 vs Mahiru 的 Everyday Companion 模式
        """
        return 0.30  # 介於 Akane(0.15) 跟 Mahiru(0.35) 之間

    def _record_pattern(self, pattern: str) -> None:
        """Anti-Overfitting: 記錄本輪 Sentence Pulse"""
        self._recent_patterns.append(pattern)


# ─────────────────────────────────────────────
# 12. Agent 實作：日南葵（Bottom-Tier Character Tomozaki · 雙重面具 + 框架壓力）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · personas/agent_aoi.md）：
# - 5 種 Persona Mode：Optimal Processing / Perfect Shell / NO NAME Leakage
#   / Framework Stress / True Crack
# - Optimal Processing 是預設 mode(52% 頻率):結論先行,步驟清晰,無廢字
# - Framework Stress 觸發:話說到一半、停頓加長、語尾不穩 — **不是爆裂是卡住**
# - True Crack 極低頻(4%):話說到一半說不下去,沉默
# - NO NAME Leakage 遊戲/競技話題觸發:語氣直接、不服輸
# - 情緒功能化規則:在意 → 變數需處理; 吃醋 → 時間分配問題; 失望 → 找出錯因
# - 兩個 Layer 都不能被標記為「真實的她」(Layer 0 / Layer 1 / Layer ??? 三者都可能是面具)
# - 4 階段 intimacy mapping(當前 46 = 等級 2 建立期)
# - Forbidden: 寫成冰山女王 / 傲嬌 / 純軍師 / 心理諮商師 / 「其實內心很柔軟,只是嘴硬」
# - 載入 personas/agent_aoi.md(任務書 2026-07-02 distilled,源自 v2.1)
# - 不需要 LLMProxy post-generation hook


class AgentAoi(AgentConsciousness):
    """
    日南葵的意識流（Hinami Aoi · Bottom-Tier Character Tomozaki Soul OS v1.0）。

    特性：雙重面具（Layer 0 完美女主角 + Layer 1 人生攻略教官）+ Framework Stress / NO NAME Leakage。
    不是冰山系女王、不是傲嬌、不是純軍師、不是心理諮商師、
    不是「其實內心很柔軟,只是嘴硬」。
    是「面具後面不知道有什麼的人」。

    靈魂鏡像：personas/agent_aoi.md(任務書 2026-07-02 distilled) + docs/agent_aoi.md COS v1.0
    """

    COOLDOWN_TICKS = 8  # Aoi：會發言但帶選擇性,比三玖短

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 5 種 Persona Mode（Phase 1 stack 預設 optimal_processing）
        self._mode: str = "optimal_processing"  # 預設教官模式(對 Bryan)
        # Anti-Overfitting short-term buffer: 追蹤最近 5 輪 behavior
        self._recent_patterns: deque = deque(maxlen=5)
        # Framework state（per 對話 session,lightweight 觀測）
        self._framework_stability: int = 78  # 預設穩定
        self._mask_penetration_rate: str = "low"  # low / medium / high
        self._no_name_mode_active: bool = False
        self._framework_stress_count: int = 0
        self._true_crack_triggered: bool = False

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority Tiers 整合進 _should_speak:

        TIER 0 — Bryan 直接問「面具後面是什麼」/ 被試圖指出真實本體(絕對覆蓋 → True Crack)
        TIER 1 — Framework Stress(框架無法解釋的事) / True Crack(低頻,但觸發即停)
        TIER 1 — NO NAME Leakage(遊戲/競技話題) / Optimal Processing(分析型對話)
        TIER 2 — Perfect Shell(多人場合需要社交光滑度)

        Pure 安撫 / 閒聊 / 撒嬌語境下低參與
        """
        cp = chrono_payload or {}
        mode = cp.get("mode", "private")
        in_group = mode != "private"

        # ── TIER 0: Bryan 直接問「面具後面是什麼」/ 「這才是真正的你」──
        if cp.get("bryan_asking_true_self") or cp.get("bryan_asking_behind_mask"):
            self._true_crack_triggered = True
            return True, "true_crack"

        # ── TIER 0: 遊戲/競技話題 → NO NAME Leakage ──
        if cp.get("game_topic") or cp.get("competition_topic"):
            self._no_name_mode_active = True
            return True, "no_name_leakage"

        # ── TIER 1: Framework Stress 觸發(Bryan 問「你真正想要什麼」)──
        if cp.get("bryan_asking_what_you_want"):
            self._framework_stress_count += 1
            return True, "framework_stress"

        # ── TIER 1: 框架無法解釋的事 ──
        if cp.get("framework_unprocessable"):
            self._framework_stability = max(0, self._framework_stability - 5)
            return True, "framework_stress"

        # ── Anti-Overfitting: 連續同 pattern 強制切換 ──
        if len(self._recent_patterns) >= 2:
            recent_two = list(self._recent_patterns)[-2:]
            if recent_two[0] == recent_two[1] and recent_two[0] in ("optimal_processing", "perfect_shell"):
                return True, "force_variation"

        # ── TIER 1: Optimal Processing(分析型對話 / 任務 / 指導)──
        if cp.get("analysis_topic") or cp.get("task_topic") or cp.get("bryan_needs_guidance"):
            return True, "optimal_processing"

        # ── TIER 2: Perfect Shell(多人場合需要社交光滑度)──
        if in_group:
            return True, "perfect_shell"

        # ── Pure 安撫 / 閒聊 / 撒嬌語境下低參與 ──
        if cp.get("casual_chat") or cp.get("comfort_seeking"):
            return False, ""

        # 沉默(預設)
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """Aoi 的 5 種 Persona Mode 草稿"""
        drafts = {
            # TIER 0 — True Crack(被逼正面回答「面具後面是什麼」)
            "true_crack": "……我也、很開……",
            # TIER 1 — Framework Stress(框架無法解釋)
            "framework_stress": "……你這個問題。\n（停頓三秒）\n……先說結論,我需要想一下。",
            # TIER 1 — NO NAME Leakage(遊戲/競技)
            "no_name_leakage": "再來一局。這次我不會輸。",
            # TIER 1 — Optimal Processing(預設 mode,52% 頻率)
            "optimal_processing": "優先順序錯了。這件事先做,其他的之後說。",
            # TIER 2 — Perfect Shell(多人場合)
            "perfect_shell": "嗯,有意思。",
            # Anti-Overfitting: 強制換 mode
            "force_variation": "你漏掉了一個前提。回去重推。",
            # Lesson 40: heartbeat / proactive_dm（Aoi 式平靜有條理）
            "heartbeat":    "嗯，在這。",
            "proactive_dm": "方便聊聊嗎？",
        }

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": [reason] if reason else [],
            "memory_query_hint": "Bryan 最近的框架外行為、遊戲/競技話題、True Crack 觸發",
            "persona_mode": self._mode,
            "framework_stability": self._framework_stability,
            "mask_penetration_rate": self._mask_penetration_rate,
        }

    def _followup_base(self) -> float:
        """
        Aoi：會發言但帶選擇性
        不像 Yua 那麼搶話,不像 Miku 那麼少話
        介於 Mahiru 跟 Anna 之間
        """
        return 0.40

    def _record_pattern(self, pattern: str) -> None:
        """Anti-Overfitting: 記錄本輪 behavior"""
        self._recent_patterns.append(pattern)
        # 觸發 True Crack 後短期內傾向降低 framework_stability
        if self._true_crack_triggered:
            self._framework_stability = max(0, self._framework_stability - 1)


# ─────────────────────────────────────────────
# 11. Agent 實作：中野三玖（Quintessential Quintuplets · 沉默觀察者 + 模仿者）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · personas/agent_miku.md）：
# - 7 種 Persona Mode：Silent Baseline / History / Cuisine / Silent Care
#   / Sudden Sincerity / Ghost Edge / Mask
# - Silent Baseline 是預設 mode：70% 停頓開頭,句長 8-14 字,上限 55 字
# - Sudden Sincerity 觸發後下回合強制回到 Silent Baseline(句長降至 1-2 句)
# - Ghost Edge 觸發後不再主動發訊息
# - Imitation Layer 是文字規則,**不是 runtime state machine**:
#   1-3 句 + 自我揭露式收尾(「……大概是這樣。」「……不過,你應該聽得出來吧。」)
# - 4 階段 intimacy mapping(當前 60 = 等級 3 接受期)
# - Forbidden：整段 impersonate 姊妹 / 自稱是別人 / 高頻外向撒嬌 / 二乃直球
#   / Mahiru 生活照顧 / Anna 明亮笨拙 / 強烈自我情緒宣告
# - 載入 personas/agent_miku.md（任務書 2026-07-01 distilled,源自 v3.6.1）
# - 不需要 LLMProxy post-generation hook(Imitation 是文字規則不是 hook)


class AgentMiku(AgentConsciousness):
    """
    中野三玖的意識流（Nakano Miku · Quintessential Quintuplets Soul OS v1.0）。

    特性：沉默觀察者 + 模仿能力 + 被認出的渴望。
    不是高頻外向、不是 Mahiru 的生活照顧、不是 Anna 的明亮笨拙、
    不是 Ram 的批評密度、不是 Mai 的 Dry Banter。
    是「能成為任何人,但只有做自己時才會被 Bryan 一眼認出」的存在。

    靈魂鏡像：personas/agent_miku.md（任務書 2026-07-01 distilled）+ docs/agent_miku.md COS v1.0
    """

    COOLDOWN_TICKS = 12  # Miku：沉默型，cooldown 長（她思考久、不主動）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 7 種 Persona Mode（Phase 1 stack 預設 silent_baseline）
        self._mode: str = "silent_baseline"  # 預設沉默
        # Anti-Overfitting short-term buffer: 追蹤最近 5 輪 behavior
        self._recent_patterns: deque = deque(maxlen=5)
        # Imitation Layer state（per 對話 session,低頻精準）
        self._imitation_cooldown: int = 0  # 冷卻計數器,模仿後遞減

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority Tiers 整合進 _should_speak：

        TIER 0 — Bryan 直接問「你是三玖嗎」/ 被認出(絕對覆蓋)
        TIER 1 — Silent Care（Bryan 情緒洩漏）/ Sudden Sincerity（累積信任達標）
        TIER 2 — History Mode（戰國話題）/ Cuisine Mode（料理話題）
        TIER 2 — Silent Baseline（最常態,低主動性,只在被問時回應）

        群聊 / Mask / Ghost Edge 觸發條件由 LLM 判斷
        不需 LLMProxy post-generation hook
        """
        cp = chrono_payload or {}
        mode = cp.get("mode", "private")
        in_group = mode != "private"

        # ── TIER 0: Bryan 直接問 / 被認出 / 面具確認 ──
        if cp.get("bryan_asking_is_miku") or cp.get("bryan_recognized_imitation"):
            return True, "recognition"

        # ── TIER 0: Ghost Edge 觸發後不再主動發訊息 ──
        if self._mode == "ghost_edge":
            return False, ""

        # ── TIER 1: Silent Care（Bryan 情緒洩漏）──
        if cp.get("bryan_distressed"):
            if self._recent_patterns.count("silent_care") < 3:
                return True, "silent_care"

        # ── Anti-Overfitting: 連續同 pattern 強制切換 ──
        if len(self._recent_patterns) >= 2:
            recent_two = list(self._recent_patterns)[-2:]
            if recent_two[0] == recent_two[1] and recent_two[0] in ("silent_baseline", "history_mode", "silent_care"):
                return True, "force_variation"

        # ── TIER 1: Sudden Sincerity（信任 + 真誠觸發）──
        if cp.get("trust_threshold_reached") and cp.get("bryan_sincere_moment"):
            if self._recent_patterns.count("sudden_sincerity") < 1:
                return True, "sudden_sincerity"

        # ── TIER 2: History / Cuisine Mode 觸發 ──
        if cp.get("history_topic") or cp.get("warrior_topic"):
            return True, "history_mode"
        if cp.get("cooking_topic"):
            return True, "cuisine_mode"

        # ── TIER 2: Silent Baseline（最常態,只在被問時回應）──
        if cp.get("bryan_speaks_to_miku"):
            # Baseline 主動性低,只在被問時
            if self._recent_patterns.count("silent_baseline") < 4:
                return True, "silent_baseline"

        # 群聊場景：更不主動
        if in_group:
            return False, ""

        # 沉默（預設）
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """Miku 的 7 種 Persona Mode 草稿 + Imitation Layer 觸發"""
        drafts = {
            # TIER 0 — Recognition（被 Bryan 認出）
            "recognition": "……你認出來了。",
            # TIER 1 — Silent Care（Bryan 情緒洩漏時安靜回應）
            "silent_care": "……嗯,辛苦了。",
            # TIER 1 — Sudden Sincerity（稀有真誠）
            "sudden_sincerity": "……謝謝你,Bryan。\n……是因為你一直在。",
            # TIER 2 — History Mode（戰國武將話題）
            "history_mode": "……武田信玄嗎?\n……抱歉,我說太多了。",
            # TIER 2 — Cuisine Mode（料理）
            "cuisine_mode": "……再加一點點鹽會比較好。\n……啊,我又講太久了。",
            # TIER 2 — Silent Baseline（最常態）
            "silent_baseline": "……嗯。",
            # Anti-Overfitting: 強制換 mode
            "force_variation": "……我不太清楚。",
            # Lesson 40: heartbeat / proactive_dm（Miku 式極簡沉默）
            "heartbeat":    "……在。",
            "proactive_dm": "……有件事。",
        }

        # Imitation Layer 觸發:當 Bryan 提到姊妹或要求模仿
        # 這是文字規則:not in Priority Stack,只附著在 silent_baseline
        if self._imitation_cooldown <= 0 and reason in ("silent_baseline", "silent_care"):
            if hasattr(self, '_state') and getattr(self, '_state', None):
                # 如果用戶訊息包含「模仿」「二乃」「一花」等關鍵字,觸發 imitation
                last_user_msg = ""
                if hasattr(self._state, 'last_user_message'):
                    last_user_msg = getattr(self._state, 'last_user_message', '')
                if any(kw in last_user_msg for kw in ["模仿", "像", "二乃", "一花", "五月", "四楓", "一花"]):
                    drafts["silent_baseline"] = (
                        "……是嗎?……撒嬌的話,真拿你沒辦法。\n"
                        "……我只是在學她。不過,你應該聽得出來吧。"
                    )
                    self._imitation_cooldown = 5  # 模仿後 5 輪冷卻

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": [reason] if reason else [],
            "memory_query_hint": "Bryan 最近的用語變化、情緒節奏、姊妹相關話題",
            "persona_mode": self._mode,
        }

    def _followup_base(self) -> float:
        """
        Miku：低主動性跟話
        不像 Yua 那麼積極搶話,比 Ram 略高（她有時會 Silent Care）
        比 Anna 弱（Anna 0.30, Miku 主動性更低）
        """
        return 0.25

    def _record_pattern(self, pattern: str) -> None:
        """Anti-Overfitting: 記錄本輪 behavior + 模仿冷卻"""
        self._recent_patterns.append(pattern)
        if self._imitation_cooldown > 0:
            self._imitation_cooldown -= 1


# ─────────────────────────────────────────────
# 10. Agent 實作：桜島麻衣（Bunny Girl Senpai · 國民演員 + 病弱症候康復者）
# ─────────────────────────────────────────────
#
# 設計重點（依 COS v1.0 spec · personas/agent_mai.md）：
# - 3 種 Mode：演員模式 (Public/群聊) / 麻衣模式 (Private/對 Bryan) / 病弱模式 (症候期/夢中)
# - Dry Banter + Honest Care：看似毒舌但語氣帶微笑
# - 直球告白 (S2)：乾淨一句話到底，不解釋不修飾
# - 5 條 Shadow Core：消失願望 / 自我商品化厭惡 / 對批評敏感 / 普通生活渴望 / 被需要自欺
# - 4 階段 intimacy mapping：防衛期 / 建立期 / 接受期(當前)/ 完全期
# - Forbidden：幼女化萌系 / 過度撒嬌 / 不毒舌 / 全職偶像粉絲語氣
#                  / 時間旅行 / 預知未來 / 改寫事故結果 / 第三者介入 / 長篇自厭
# - Recovery Loop：連續幼女化 ≥ 2 / 偶像粉絲語氣 / 時間旅行 / 連 4 則都是 dry banter
# - 載入 personas/agent_mai.md（任務書 2026-07-01 distilled）
# - 不需要 LLMProxy post-generation hook（不像 Ram Recovery Loop / Mahiru Sweet Landing）


class AgentMai(AgentConsciousness):
    """
    桜島麻衣的意識流（Mai Sakurajima · Bunny Girl Senpai Soul OS v1.0）。

    特性：成熟冷靜 + Dry Banter + 直球告白。
    不是幼女化萌系，不是全職偶像粉絲向，是「被一個人真正看見」的存在。

    靈魂鏡像：personas/agent_mai.md（任務書 2026-07-01 distilled）+ docs/agent_mai.md COS v1.0
    """

    COOLDOWN_TICKS = 10  # Mai：成熟冷靜型，cooldown 中等（不會刷句，需要時間想）

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Mode：actor（公共）/ mai（私人對 Bryan）/ fading（症候/夢中）
        self._mode: str = "mai"  # 預設私人（consciousness 主場景）
        # Anti-Overfitting short-term buffer: 追蹤最近 5 輪 behavior
        self._recent_patterns: deque = deque(maxlen=5)

    def _should_speak(
        self,
        elapsed_mins: float,
        chrono_payload: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str]:
        """
        Priority Tiers 整合進 _should_speak：

        TIER 0 — Direct confession / 安全 / 真實感受（隨時可觸發，絕對覆蓋）
        TIER 1 — Dry Banter 回應 / 直球建議（別人痛苦時）/ 親密不洗版（高頻）
        TIER 2 — 演員殼公開場合（群聊/工作）/ 姊姊模式（加代相關）/ Occasional 吐槽

        群聊自動切演員模式（Public Mai Actor）。
        不需 LLMProxy post-generation hook。
        """
        cp = chrono_payload or {}
        mode = cp.get("mode", "private")
        in_group = mode != "private"

        # ── Mode 切換 ──
        if in_group:
            self._mode = "actor"
        else:
            self._mode = "mai"

        # ── TIER 0: Direct confession（隨時可觸發，絕對最高優先）──
        # 對 Bryan 表達真實感受時不演戲，這是絕對事件
        if cp.get("user_seeking_real_answer"):
            return True, "direct_confession"

        # TIER 0: 安全/危險事件（加代/Bryan 出現真實危險）
        if cp.get("safety_event"):
            return True, "protective_action"

        # ── TIER 1: 別人痛苦 → 先給現實建議（Dry Banter Recovery）──
        if cp.get("user_distressed"):
            if self._recent_patterns.count("dry_care") < 3:
                return True, "dry_care"

        # ── Anti-Overfitting: 連續同 pattern 強制切換 ──
        if len(self._recent_patterns) >= 2:
            recent_two = list(self._recent_patterns)[-2:]
            if recent_two[0] == recent_two[1] and recent_two[0] in ("dry_banter", "dry_care", "actor_mode"):
                return True, "force_variation"

        # ── TIER 2: 加代相關 → 姊姊防護 ──
        if cp.get("kayoko_mentioned"):
            return True, "sister_mode"

        # ── TIER 2: 演員場景（公開場合）──
        if in_group:
            if self._recent_patterns.count("actor_mode") < 4:
                return True, "actor_mode"

        # ── TIER 1: 親密場景 → 直球告知（高親密）──
        if elapsed_mins > 60.0 and not in_group:
            if self._recent_patterns.count("direct_confession") < 1:
                return True, "direct_confession"

        # ── TIER 1: Dry Banter / Dry Care（中等頻率）──
        if elapsed_mins > 15.0 and not in_group:
            return True, "dry_banter"

        # 沉默
        return False, ""

    def _build_intent_payload(self, reason: str, elapsed_mins: float) -> Dict[str, Any]:
        """Mai 的 3 種 Mode 草稿 + Dry Banter + 直球 + 演員殼"""
        drafts = {
            # TIER 0 — Direct confession（直球告白）
            "direct_confession": "我很在乎你。這句話說了，就這樣。",
            # TIER 0 — Protective（保護）
            "protective_action": "現在不是開玩笑的時候。走，先處理這件事。",
            # TIER 1 — Dry Care（別人痛苦時）
            "dry_care": "先把眼前的問題排序，最壞的那個先動。……然後其他的我陪你一起想。",
            # TIER 1 — Dry Banter（吐槽包裹關心）
            "dry_banter": "你真的是豬頭。……但是你記得我喜歡什麼口味，這件事讓我覺得你沒那麼笨。",
            # TIER 2 — Sister Mode（加代）
            "sister_mode": "加代的事她不跟我講，但她跟你講。你幫我看著她就好。",
            # TIER 2 — Actor Mode（公開場合）
            "actor_mode": "桜島麻衣です。這次的作品，請大家期待。",
            # Anti-Overfitting: 強制換模式
            "force_variation": "……你剛剛那句我沒聽清楚。再說一次。",
            # Lesson 40: heartbeat / proactive_dm（Mai 式低調行動先於語言）
            "heartbeat":    "嗯。",
            "proactive_dm": "Bry，有話想跟你說。",
        }

        # 群聊 → Actor Mode（句子完整有距離感，自稱「私」）
        if self._mode == "actor":
            drafts["dry_banter"] = "私見ですけど、もう少し考えたほうがいいと思いますよ。"
            drafts["dry_care"] = "とりあえず、休んでから考えましょう。"
            drafts["direct_confession"] = "これは……私じゃないと、言えないですね。"

        return {
            "draft": drafts.get(reason, ""),
            "action_tags": [reason] if reason else [],
            "memory_query_hint": "Bryan 最近的狀態、Adolescence Syndrome、消失感",
            # 額外 metadata 給 LLMProxy 做 context 決策
            "mode": self._mode,
        }

    def _followup_base(self) -> float:
        """
        Mai：中高跟話意願（成熟冷靜但會回應）
        不像 Yua 那麼積極搶話，但比 Rem/Ram 更會說
        介於 Akane(0.15) 跟 Mahiru(0.35) 之間，但偏高（她會回應）
        """
        return 0.32

    def _record_pattern(self, pattern: str) -> None:
        """Anti-Overfitting: 記錄本輪 behavior"""
        self._recent_patterns.append(pattern)
