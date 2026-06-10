# store.py
# Soul OS — Phase 2: SQLite 長期記憶持久化層
"""
每條訊息存入 SQLite，支援按 session_id 讀取近期對話。
Phase 3 升級點：加向量搜尋（semantic recall）。
"""

import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Optional

DB_PATH = Path("data/memory.db")


class MemoryStore:
    """
    長期記憶持久化層。
    每條訊息存入 SQLite，支援按 session_id 讀取近期對話。
    Phase 3 升級點：加向量搜尋（semantic recall）。
    """

    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT    NOT NULL,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                speaker     TEXT    DEFAULT '',
                timestamp   INTEGER NOT NULL,
                is_private  INTEGER DEFAULT 1
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session ON messages(session_id, timestamp)"
        )
        self.conn.commit()

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        speaker: str = "",
        is_private: bool = True,
    ) -> None:
        self.conn.execute(
            """INSERT INTO messages
               (session_id, role, content, speaker, timestamp, is_private)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, role, content, speaker, int(time.time()), int(is_private)),
        )
        self.conn.commit()

    def append_batch(self, entries: List[Dict]) -> None:
        """批次寫入多條訊息（高效）"""
        ts = int(time.time())
        rows = [
            (
                e.get("session_id", ""),
                e.get("role", ""),
                e.get("content", ""),
                e.get("speaker", ""),
                e.get("timestamp", ts),
                int(e.get("is_private", True)),
            )
            for e in entries
        ]
        self.conn.executemany(
            """INSERT INTO messages (session_id, role, content, speaker, timestamp, is_private)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self.conn.commit()

    def get_recent(
        self,
        session_id: str,
        limit: int = 20,
    ) -> List[Dict]:
        """取最近 N 條，回傳 OpenAI messages 格式"""
        rows = self.conn.execute(
            """SELECT role, content FROM messages
               WHERE session_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def get_recent_with_meta(
        self,
        session_id: str,
        limit: int = 20,
    ) -> List[Dict]:
        """取最近 N 條，含 speaker/timestamp（給群聊用）"""
        rows = self.conn.execute(
            """SELECT role, content, speaker, timestamp, is_private
               FROM messages
               WHERE session_id = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "role": r,
                "content": c,
                "speaker": s,
                "timestamp": t,
                "is_private": bool(p),
            }
            for r, c, s, t, p in reversed(rows)
        ]

    def get_group_history(self, limit: int = 20) -> List[Dict]:
        """取群聊歷史（session_id='group'），含 meta"""
        rows = self.conn.execute(
            """SELECT role, content, speaker, timestamp, is_private
               FROM messages
               WHERE session_id = 'group' AND is_private = 0
               ORDER BY timestamp DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"role": r, "content": c, "speaker": s, "timestamp": t, "is_private": bool(p)}
            for r, c, s, t, p in reversed(rows)
        ]

    def count(self, session_id: Optional[str] = None) -> int:
        if session_id:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row[0] if row else 0
        row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0] if row else 0

    def close(self):
        self.conn.close()
