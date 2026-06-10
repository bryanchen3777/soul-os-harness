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

        # === Phase 4: FTS5 全文搜尋（content-table 模式，BM25 排序）===
        # 啟動時強制重建：換 tokenizer 設定（unicode61→trigram）時自動生效
        self.conn.execute("DROP TABLE IF EXISTS messages_fts")
        self.conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='id',
                tokenize='trigram'
            )
        """)
        # 同步 trigger：新增/刪除/更新自動維護 FTS 索引
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
            END
        """)
        self.conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
            END
        """)
        # 全量同步（冪等：只補漏，已同步的 rowid 不再插入）
        self.conn.execute("""
            INSERT INTO messages_fts(rowid, content)
            SELECT id, content FROM messages
            WHERE id NOT IN (SELECT rowid FROM messages_fts)
        """)
        # content-table 模式需要顯式 rebuild 才會建 segment index
        # 否則 docsize=0，MATCH 全部 0 hits
        self.conn.execute(
            "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
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

    def search(
        self,
        query: str,
        exclude_session_id: str = "",
        top_k: int = 3,
    ) -> List[Dict]:
        """Phase 4: FTS5 全文搜尋（BM25 相關度排序）

        Args:
            query: 搜尋字串（自然語句會自動切成 trigram OR 邏輯）
            exclude_session_id: 排除的 session_id（避免注入當下對話）
            top_k: 最多回傳幾筆

        Returns:
            list of dict，含 id / session_id / role / content /
            speaker / timestamp / is_private
        """
        if not query or not query.strip():
            return []
        fts_query = self._build_fts_query(query)
        cols = (
            "id", "session_id", "role", "content",
            "speaker", "timestamp", "is_private",
        )
        try:
            rows = self.conn.execute(
                """SELECT m.id, m.session_id, m.role, m.content,
                          m.speaker, m.timestamp, m.is_private
                   FROM messages_fts f
                   JOIN messages m ON m.id = f.rowid
                   WHERE messages_fts MATCH ?
                     AND (? = '' OR m.session_id != ?)
                   ORDER BY f.rank
                   LIMIT ?""",
                (fts_query, exclude_session_id,
                 exclude_session_id, top_k),
            ).fetchall()
        except Exception:
            # FTS5 query 語法錯誤時安全降級（回傳空 list）
            return []
        return [dict(zip(cols, row)) for row in rows]

    def _build_fts_query(self, query: str) -> str:
        """把 query 切成 3-char sliding window，FTS5 OR 連起來。

        為什麼需要：FTS5 trigram 預設 AND 邏輯，長自然語句任一
        3-char trigram 不在 corpus 就 0 hits。OR 邏輯才能對真實
        user 輸入 work。

        規則：
          - 切出所有 3-char 連續子字串（含空白/標點的 trigram 跳過）
          - 英文詞（3+ 字元）整詞加入
          - 去重後 OR 連接
          - fallback：空結果時回傳整句 phrase query
        """
        import re
        trigrams = []
        for i in range(len(query) - 2):
            chunk = query[i:i + 3]
            if re.search(r"[\s\W]", chunk):
                continue
            trigrams.append(f'"{chunk}"')

        # 英文詞另外加（避免 "yua" 被切成 2-char 拋棄）
        words = re.findall(r"[a-zA-Z]{3,}", query)
        for w in words:
            trigrams.append(f'"{w}"')

        if not trigrams:
            return f'"{query}"'  # fallback 整句 phrase

        seen = set()
        unique = [t for t in trigrams if not (t in seen or seen.add(t))]
        return " OR ".join(unique)

    def close(self):
        self.conn.close()
