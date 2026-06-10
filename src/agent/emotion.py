"""
emotion.py
Soul OS — Phase 3: 情緒引擎（SQLite 持久化）

設計：
  - 共用 data/memory.db，加一張 agent_emotions 表
  - 各 agent 有自己的 mood_decay / response_boost 敏感度
  - mood clamp(-1.0, 1.0)，intimacy clamp(0.0, 100.0)
  - 提供 mood_description() 給 LLMProxy 注入到 system prompt

資料表：
  agent_emotions (
    agent_id   TEXT PRIMARY KEY,
    mood       REAL DEFAULT 0.0,
    intimacy   REAL DEFAULT 50.0,
    updated_at TEXT  (ISO timestamp)
  )
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple

logger = logging.getLogger("soul_os.emotion")

DB_PATH = Path("data/memory.db")

# 各 agent 對情緒變化的敏感度
# - response_boost：每次 user_message 給的 mood 增量（正向）
# - mood_decay：每次自然 heartbeat tick 給的 mood 衰減（負向）
SENSITIVITY: dict[str, dict[str, float]] = {
    "agent_yua":   {"mood_decay": 0.015, "response_boost": 0.08},
    "agent_ruka":  {"mood_decay": 0.025, "response_boost": 0.12},  # 更敏感
    "agent_akane": {"mood_decay": 0.010, "response_boost": 0.06},
}


class EmotionEngine:
    """情緒引擎：管理各 agent 的 mood / intimacy（SQLite 持久化）"""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_emotions (
                agent_id   TEXT PRIMARY KEY,
                mood       REAL DEFAULT 0.0,
                intimacy   REAL DEFAULT 50.0,
                updated_at TEXT
            )
        """)
        self.conn.commit()

    def get(self, agent_id: str) -> Tuple[float, float]:
        """讀出 (mood, intimacy)；沒有就回預設值 (0.0, 50.0)"""
        cur = self.conn.execute(
            "SELECT mood, intimacy FROM agent_emotions WHERE agent_id = ?",
            (agent_id,),
        )
        row = cur.fetchone()
        if row is None:
            return (0.0, 50.0)
        return (float(row[0]), float(row[1]))

    def update(
        self,
        agent_id: str,
        mood_delta: float = 0.0,
        intimacy_delta: float = 0.0,
    ) -> Tuple[float, float]:
        """clamp 後寫回，回傳更新後的 (mood, intimacy)"""
        cur_mood, cur_intimacy = self.get(agent_id)
        new_mood = max(-1.0, min(1.0, cur_mood + mood_delta))
        new_intimacy = max(0.0, min(100.0, cur_intimacy + intimacy_delta))
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO agent_emotions (agent_id, mood, intimacy, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                mood = excluded.mood,
                intimacy = excluded.intimacy,
                updated_at = excluded.updated_at
        """, (agent_id, new_mood, new_intimacy, now))
        self.conn.commit()
        return (new_mood, new_intimacy)

    def reset(self, agent_id: str) -> None:
        """清回初始值（debug 用）"""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT INTO agent_emotions (agent_id, mood, intimacy, updated_at)
            VALUES (?, 0.0, 50.0, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                mood = 0.0,
                intimacy = 50.0,
                updated_at = excluded.updated_at
        """, (agent_id, now))
        self.conn.commit()
        logger.info(f"[Emotion] {agent_id} reset to defaults")

    @staticmethod
    def mood_description(mood: float) -> str:
        """把 mood 數值翻成 LLM 看得到的中文描述"""
        if mood > 0.5:
            return "你現在心情很好，語氣輕鬆自然"
        if mood >= 0.0:
            return ""  # 0 ~ 0.5 不注入
        if mood >= -0.5:
            return "你有點悶，話比平時少一點"
        return "你心情很差，話變得更短、更冷"


# 模組層級 singleton — 各處 `from src.agent.emotion import emotion_engine`
emotion_engine = EmotionEngine()
