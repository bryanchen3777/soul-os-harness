"""
src/work/persistence.py
Authority persistence — approval/grant registry 的 durable append-only log（2D §3 / §6）。

把 authority boundary 的 approval/grant registry 從 in-memory 提升為 durable state：
- `AuthorityEvent`：authority 的 durable event（approval_granted / grant_issued /
  approval_revoked / grant_consumed）。
- `AuthorityStore`：append-only JSONL log（複用 WorkStore 的 append-only 模式）。
  current registry = fold(events)（由 `AuthorityManager.resume()` 執行）。

核心原則（2D §1 / §6）：
> Soul OS owns the durable work truth. DSH owns ephemeral execution.
> approvals / capability grants 是 immutable durable records。

本模組只負責「durable log 的 append + read」，不 import authority.py 的 model
（fold 重建 Approval / CapabilityGrant 由 authority.py 的 resume() 執行），
避免 circular import。純 Python domain，零 DSH coupling：
- 不 import 任何 DSH type / id
- capability 名稱是 capability-neutral（非 DSH tool 名）

Canonical 來源（權威，不得修改）：
- docs/DSH-PERSISTENCE.md §3（WorkEvent log）、§6（approvals/grants immutable durable）
- docs/DSH-HUMAN-AUTHORITY.md §2（Approval schema）、§6（provenance chain）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.paths import data_root

from .bridge import DURABLE_WRITER, is_durable_writer

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 schema.py 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


class AuthorityEventType(str, Enum):
    """authority durable event 的 event_type（2D §3 的 approval_granted / grant_issued，
    加上撤銷 / 消費兩類 authority state 變更）。"""
    APPROVAL_GRANTED = "approval_granted"
    GRANT_ISSUED = "grant_issued"
    APPROVAL_REVOKED = "approval_revoked"
    GRANT_CONSUMED = "grant_consumed"


class AuthorityEvent(BaseModel):
    """authority durable log 的一筆（append-only）。

    payload 承載完整序列化物件（approval / grant），fold 時 last-write-wins：
    - approval_granted / approval_revoked → payload["approval"] = Approval dump
    - grant_issued / grant_consumed → payload["grant"] = CapabilityGrant dump
    """
    event_type: AuthorityEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class NoAuthorityStoreError(RuntimeError):
    """AuthorityManager.resume() 被呼叫但未注入 AuthorityStore。"""


class NotDurableWriterError(PermissionError):
    """非 durable writer 的 actor 嘗試寫 authority durable state（single-writer rule 違反）。"""


class AuthorityStore:
    """append-only AuthorityEvent JSONL store（複用 WorkStore 的 append-only 模式）。

    只提供 append（寫）與 read_events（讀），無 update / delete API。
    corrupt row 跳過並留 log，不修改原檔。

    路徑約定：
    - 單一真相：data_root() / "authority" / "authority_events.jsonl"
    - 傳入 data_dir 可覆寫（測試隔離用），預設 data_root() / "authority"
    """

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = data_root() / "authority"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.data_dir / "authority_events.jsonl"

    def append(self, event: AuthorityEvent, actor: str) -> None:
        """append 一筆 AuthorityEvent（append-only，不可改不可刪）。

        single-writer enforcement（2D §1）：actor 必須明確提供（無 default），
        且必須是 durable writer（kernel）。非 kernel 的 actor 呼叫寫入 →
        拋 NotDurableWriterError。這是 durable write boundary 的強制檢查，
        即使直接 import AuthorityStore 也不能 bypass。
        """
        if not is_durable_writer(actor):
            raise NotDurableWriterError(
                f"actor={actor!r} is not the durable writer; "
                f"only {DURABLE_WRITER!r} may write durable authority state"
            )
        with open(self.store_file, "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def read_events(self) -> list[AuthorityEvent]:
        """全檔掃描，回傳所有 event（按 append 順序）。corrupt row 跳過留 log。"""
        if not self.store_file.exists():
            return []
        events: list[AuthorityEvent] = []
        with open(self.store_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    events.append(AuthorityEvent(**data))
                except (ValueError, TypeError) as e:
                    # corrupt row：不修改原檔（append-only），跳過並留 log
                    logger.warning(
                        "[AuthorityStore] corrupt row in %s: %s", self.store_file, e
                    )
        return events
