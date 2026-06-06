"""
src/agent/speaker_token.py
Soul OS — Speaker Token Bus：多 Agent 搶答仲裁
"""
import asyncio
import logging
import random
from typing import Optional

logger = logging.getLogger("soul_os.speaker_token")

# 各 Agent 的 base_score（Ruka 最積極，Yua 次之，Akane 慢熱）
BASE_SCORES = {
    "agent_yua":   0.7,
    "agent_ruka":  0.9,
    "agent_akane": 0.5,
}

# 搶答視窗：收到 USER_MESSAGE 後，Agent 有這麼多 ms 提交 bid
BID_WINDOW_MS = 300


class SpeakerTokenBus:
    """
    仲裁多個 Agent 搶答，防止同時說話。

    流程：
    1. USER_MESSAGE 到來時，呼叫 open_session() 打開競標窗口
    2. 各 Agent 在窗口內呼叫 submit_bid() 提交自己的分數
    3. 窗口關閉後，呼叫 resolve_session() 決定勝者並設定 cooldown
    """

    def __init__(self, cooldown_secs: float = 4.0):
        self._lock = asyncio.Lock()
        self._cooldown: dict[str, float] = {}   # agent_id → 可再次說話的時間戳
        self.cooldown_secs = cooldown_secs

        # 當前競標會話
        self._session_active = False
        self._bids: dict[str, float] = {}
        self._winner: Optional[str] = None  # 緩存結算結果，確保所有 Agent 拿到同一個 winner
        self._resolve_future: asyncio.Future | None = None

    # ── 對外 API ──────────────────────────────────

    async def open_session(self) -> None:
        """打開一個競標窗口，讓各 Agent 提交分數"""
        async with self._lock:
            self._bids = {}
            self._winner = None
            self._session_active = True
            logger.debug("[SpeakerTokenBus] session opened")

    async def submit_bid(self, agent_id: str, score: float) -> bool:
        """
        Agent 提交競標。
        在競標窗口關閉前呼叫才有效。
        返回 True 表示已登記，False 表示窗口已關閉或 agent 在 cooldown。
        """
        now = asyncio.get_event_loop().time()
        async with self._lock:
            if not self._session_active:
                logger.debug(f"[SpeakerTokenBus] bid rejected: session closed ({agent_id})")
                return False
            if now < self._cooldown.get(agent_id, 0):
                logger.debug(f"[SpeakerTokenBus] bid rejected: cooldown ({agent_id})")
                return False
            # 分數加 jitter
            final_score = score + random.uniform(0, 0.3)
            self._bids[agent_id] = final_score
            logger.debug(f"[SpeakerTokenBus] bid: {agent_id} score={final_score:.3f}")
            return True

    async def resolve_session(self) -> Optional[str]:
        """
        關閉窗口，結算。返回勝出的 agent_id，或 None（全部在 cooldown）。
        冪等：若已結算過，直接返回緩存的 winner。
        """
        async with self._lock:
            if self._winner is not None:
                return self._winner
            if not self._session_active:
                return None
            self._session_active = False

        if not self._bids:
            async with self._lock:
                self._winner = None
            logger.debug("[SpeakerTokenBus] no bids, nobody speaks")
            return None

        now = asyncio.get_event_loop().time()
        available = {k: v for k, v in self._bids.items() if now >= self._cooldown.get(k, 0)}
        if not available:
            async with self._lock:
                self._winner = None
            logger.debug("[SpeakerTokenBus] all bidders in cooldown")
            return None

        winner = max(available, key=lambda k: available[k])
        self._cooldown[winner] = now + self.cooldown_secs
        self._bids = {}
        async with self._lock:
            self._winner = winner
        logger.info(f"[SpeakerTokenBus] winner={winner} score={available[winner]:.3f}")
        return winner

    async def get_winner(self) -> Optional[str]:
        """
        查詢當前會話的結算結果。
        若尚未結算，先呼叫 resolve_session()。
        """
        async with self._lock:
            if self._winner is not None:
                return self._winner
            if not self._session_active and not self._bids:
                return None
        return await self.resolve_session()

    async def close_session(self) -> None:
        """提前關閉窗口（不用結算，直接放棄這輪）"""
        async with self._lock:
            self._session_active = False
            self._bids = {}

    # ── 便捷方法 ──────────────────────────────────

    def base_score(self, agent_id: str) -> float:
        return BASE_SCORES.get(agent_id, 0.5)