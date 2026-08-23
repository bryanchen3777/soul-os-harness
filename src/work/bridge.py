"""
src/work/bridge.py
DSH Adapter Boundary — bridge protocol（Python ↔ TypeScript 的 message contract）。

language-neutral（JSON-serializable）message format，讓 Soul Kernel 與 DSH Adapter
（以及未來任何 runtime substrate）用同一套 message 交換。

Canonical 來源（權威，不得修改）：
- docs/DSH-SOUL-OS-MIGRATION-PLAN.md §3.2（crossing event 必帶欄位）
- docs/DSH-WORK-CONTRACT.md §7（DSH Adapter Mapping Boundary）
- docs/DSH-PERSISTENCE.md §1（Soul OS owns durable work truth; DSH owns ephemeral execution）

single-writer rule：kernel 是唯一 writer（DSH 側只讀不寫 durable state）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 schema.py 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


class BridgeMessageType(str, Enum):
    """三種 message type（migration plan §3.2 / work order）。"""
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"


# ─────────────────────────────────────────────
# single-writer rule（2D §1 / migration plan §3.2）
# ─────────────────────────────────────────────

# kernel 是 durable state 的唯一 writer；DSH 側只讀不寫 durable state。
DURABLE_WRITER = "kernel"

# DSH 側的 actor 一律是 read-only（不寫 durable state）。
READ_ONLY_ACTORS = frozenset({"dsh_adapter", "dsh_session", "dsh_runtime"})


def is_durable_writer(actor: str) -> bool:
    """回傳 actor 是否為 durable state 的唯一 writer（kernel）。

    single-writer rule：Soul OS owns the durable work truth; DSH owns ephemeral
    execution。只有 kernel 能寫 durable state；DSH 側（adapter / session / runtime）
    只讀不寫。
    """
    return actor == DURABLE_WRITER


def derive_idempotency_key(
    *,
    work_id: str,
    role: str,
    result_type: str,
    refs: list[str] | None = None,
    decision: dict[str, Any] | None = None,
) -> str:
    """Canonical handoff idempotency key（2D §4：idempotency_keys 避免重啟後重複執行）。

    idempotency_key = SHA-256(work_id | role | result_type | refs | decision)
    - refs = artifact_refs（artifact）或 evidence_refs（evidence），排序後正規化
      （ref 順序不影響語意，同內容必須同 key）
    - decision = 決策 dict，sort_keys + 固定 separator 正規化（dict 順序無關）

    純函式、language-neutral（Python ↔ TypeScript 共用同一公式），future DSH
    Adapter 可在另一側重現同一 key。同內容 → 同 key；不同內容 → 不同 key
    （dedup 只吞 identical retry，不吞不同結果）。
    """
    refs_part = ",".join(sorted(set(refs or [])))
    decision_part = json.dumps(
        decision or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    payload = f"{work_id}|{role}|{result_type}|{refs_part}|{decision_part}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BridgeMessage(BaseModel):
    """
    bridge message envelope（migration plan §3.2）。

    所有 crossing event 必帶：event_id、timestamp、actor、source、
    causation/reference、schema_version。

    - causation：Soul causal truth（指向造成此 message 的 Soul event_id）。
    - reference：外部 reference（如 DSH sessionId），**不是** Soul identity 或
      causal truth（migration plan §3.2：DSH sessionId 可作 reference，
      但不可當 Soul identity 或 causal truth）。
    """
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = "1.0"
    message_type: BridgeMessageType
    actor: str  # role（kernel | chief | developer | human | ...），不是 DSH id
    source: str  # soul_kernel | dsh_adapter | world | ...
    timestamp: datetime = Field(default_factory=_utcnow)
    causation: str | None = None  # Soul causal truth（event_id）
    reference: str | None = None  # 外部 reference（如 DSH sessionId），非 causal truth
    payload: dict[str, Any] = Field(default_factory=dict)
