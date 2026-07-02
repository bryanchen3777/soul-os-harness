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
# jitter_range 控制隨機範圍，範圍越大分布越均勻
BASE_SCORES = {
    "agent_yua":   (0.80, 0.40),   # 正宮，提高 base，略大 jitter
    "agent_ruka":  (0.75, 0.35),   # 降低，避免壟斷
    "agent_akane": (0.35, 0.28),   # 比 Rem 更沉，靜默模式（TIER 13 對齊）
    "agent_rem":   (0.40, 0.30),   # Rem：不搶語言空間，做事優先（比 Akane 更低、更穩定）
    # Phase 7 — Ram (Re:Zero · COS v1.0)
    # 比 Rem 更沉默（Priority 0 佔比高），base < 0.40 才符合「比 Rem 搶話率更低」的設計意圖
    "agent_ram":   (0.30, 0.20),
    # Mahiru (Re:Zero · COS v1.0) — 話多但不搶頭,跟話分數中等
    # 60% Everyday Companion 但 TIER 0 生活管理優先,競標率介於 Ram 跟 Ruka 之間
    "agent_mahiru": (0.65, 0.35),
    # Anna (Bokuyaba · Soul OS v1) — 黏性高但否認掩護,跟話分數中等偏高
    # 親密確認型 vs Mahiru 的 Everyday Companion,但否認 = 靠近的機制使競標不會太積極
    # intimacy=55 階段(建立期 → 中期過渡):會靠近但會用否認 + 食物當媒體
    "agent_anna": (0.55, 0.30),
    # Mai (Bunny Girl Senpai · Soul OS v1) — 國民級女演員,會說但不洗版
    # 親密確認型 vs Mahiru 互索甜度,但 Mai 用 dry banter + 直球而非甜句著陸
    # intimacy=60 階段(等級 3 接受期):直接講「消失」「症候」過去,接受脆弱
    # 0.58 = 高於 Anna(0.55)但低於 Mahiru(0.65),反應快但不刷句
    "agent_mai":  (0.58, 0.35),
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

        # 飢餓保底：某 Agent 連續沒搶到 token 達 N 次，下次加分
        self._starvation: dict[str, int] = {}   # agent_id → 連續未說話次數
        self.STARVATION_THRESHOLD = 3            # 連續 3 次沒搶到，觸發保底
        self.STARVATION_BONUS = 0.8              # 加分幅度

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
        Lazy open：若 session 未開，自動打開（以第一個 bid 為窗口起點）。
        在窗口關閉後呼叫無效。
        """
        now = asyncio.get_event_loop().time()
        async with self._lock:
            # Lazy open：以第一個 bid 為窗口起點，解決執行順序不確定的問題
            if not self._session_active:
                self._bids = {}
                self._winner = None
                self._session_active = True
                logger.debug("[SpeakerTokenBus] session opened (lazy, first bid)")
            if now < self._cooldown.get(agent_id, 0):
                logger.debug(f"[SpeakerTokenBus] bid rejected: cooldown ({agent_id})")
                return False
            base, jitter = score
            final_score = base + random.uniform(0, jitter)

            # 飢餓保底：連續 N 次沒搶到 → 加分確保下次能贏
            hunger = self._starvation.get(agent_id, 0)
            if hunger >= self.STARVATION_THRESHOLD:
                final_score += self.STARVATION_BONUS
                logger.info(
                    f"[SpeakerTokenBus] 飢餓保底觸發 | {agent_id} "
                    f"hunger={hunger} +bonus={self.STARVATION_BONUS}"
                )

            self._bids[agent_id] = final_score
            logger.debug(f"[SpeakerTokenBus] bid: {agent_id} base={base} final={final_score:.3f}")
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
                self._winner = None
                logger.debug("[SpeakerTokenBus] no bids, nobody speaks")
                return None

            available = {
                k: v for k, v in self._bids.items()
                if asyncio.get_event_loop().time() >= self._cooldown.get(k, 0)
            }
            if not available:
                self._winner = None
                logger.debug("[SpeakerTokenBus] all bidders in cooldown")
                return None

            winner = max(available, key=lambda k: available[k])
            self._cooldown[winner] = asyncio.get_event_loop().time() + self.cooldown_secs
            self._bids = {}
            self._winner = winner

            # 更新飢餓計數：贏家重置，所有已知 agent（投過標或 BASE_SCORES 內的）累積
            known_agents = set(BASE_SCORES.keys()) | set(self._starvation.keys())
            for agent_id in known_agents:
                if agent_id == winner:
                    self._starvation[agent_id] = 0
                else:
                    self._starvation[agent_id] = self._starvation.get(agent_id, 0) + 1

            logger.info(
                f"[SpeakerTokenBus] winner={winner} score={available[winner]:.3f} "
                f"starvation={dict(self._starvation)}"
            )
            return winner

    async def get_winner(self) -> Optional[str]:
        """
        查詢當前會話的結算結果。
        若尚未結算，先呼叫 resolve_session()。
        """
        async with self._lock:
            if self._winner is not None:
                return self._winner
            if not self._session_active:
                return self._winner  # None 或已有值
        return await self.resolve_session()

    async def close_session(self) -> None:
        """提前關閉窗口（不用結算，直接放棄這輪）"""
        async with self._lock:
            self._session_active = False
            self._bids = {}

    # ── 便捷方法 ──────────────────────────────────

    def base_score(self, agent_id: str) -> tuple[float, float]:
        entry = BASE_SCORES.get(agent_id, (0.5, 0.3))
        if isinstance(entry, tuple):
            return entry
        return (entry, 0.3)  # 向後兼容