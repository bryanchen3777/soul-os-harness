"""
src/work/schema.py
Work Contract domain schema（Phase 2A / 2D）。

純 Python domain，零 DSH coupling：
- 不 import 任何 DSH type / id
- owner / assigned_agents 存 role，不存 DSH agent/session id
- capability 名稱是 capability-neutral，不是 DSH tool 名

Canonical 來源（權威，不得修改）：
- docs/DSH-WORK-CONTRACT.md §3（Work Object schema）、§4（state machine）、§6（Handoff）
- docs/DSH-PERSISTENCE.md §3（WorkEvent log）

對齊既有模式：pydantic（參考 src/eventbus/schema.py 的 SoulEvent/EventType 寫法）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 eventbus 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# 1. 枚舉
# ─────────────────────────────────────────────

class WorkState(str, Enum):
    """
    Work 生命週期狀態（2A §4 唯一 authoritative source）。

    不得新增 reviewing / failed / waiting 等未列於 2A §4 的 state。
    """
    PROPOSED = "proposed"
    APPROVED = "approved"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class WorkEventType(str, Enum):
    """WorkEvent 的 event_type（2D §3）。"""
    STATE_TRANSITION = "state_transition"
    ARTIFACT_PRODUCED = "artifact_produced"
    EVIDENCE_PRODUCED = "evidence_produced"
    DECISION_MADE = "decision_made"
    APPROVAL_GRANTED = "approval_granted"
    GRANT_ISSUED = "grant_issued"


class ResultType(str, Enum):
    """
    HandoffResult 的 result_type（2A §6）。

    僅 artifact / evidence / decision。**不得有 approval**——
    Human Approval 走獨立的 Human authority path 寫入 approvals[]，
    不是 Specialist 的 result。
    """
    ARTIFACT = "artifact"
    EVIDENCE = "evidence"
    DECISION = "decision"


class HandoffStatus(str, Enum):
    """HandoffResult 的 status（2A §6）。"""
    DONE = "done"
    BLOCKED = "blocked"
    NEEDS_INPUT = "needs_input"


# ─────────────────────────────────────────────
# 2. 子結構
# ─────────────────────────────────────────────

class Provenance(BaseModel):
    """
    provenance（2A §3.3）：誰、用什麼能力、何時、消費/產出哪些 ref。

    capability 是 capability-neutral（如 "git.commit"），不是 DSH tool 名。
    """
    role: str
    capability: str
    timestamp: datetime = Field(default_factory=_utcnow)
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)


class ResumeState(BaseModel):
    """
    resume_state（2A §3.2 / 2D §4）：最小重建狀態，不是 DSH session snapshot。

    current_phase 是「現在卡在哪個 phase」；blocked 時是解除阻塞後要 resume 回的
    target state（通常是 in_progress 或 awaiting_review）。
    """
    current_phase: WorkState
    pending_handoffs: list[str] = Field(default_factory=list)
    last_artifact_refs: list[str] = Field(default_factory=list)
    idempotency_keys: list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────
# 3. 核心物件
# ─────────────────────────────────────────────

class WorkObject(BaseModel):
    """
    Work Object（2A §3）。versioned、JSON-serializable、不得引用任何 DSH type / id。

    artifacts / evidence / decisions / approvals 是四種 authority 不同的 result（2A §3.1）：
    - artifacts[]：內容產出（Specialist 產生，內容定址、可 hash、可回滾）
    - evidence[]：驗證證明（Tester/Auditor 產生，指向 artifact + 結論）
    - decisions[]：agent 的自主選擇（只記錄、供 audit，不 gate）
    - approvals[]：人類的明確授權（gate 邊界，不可被 agent 偽造）
    """
    schema_version: str = "1.0"
    work_id: str = Field(default_factory=lambda: str(uuid4()))
    objective: str
    state: WorkState
    owner: str  # role（chief | developer | ... | soul_identity）
    assigned_agents: list[str] = Field(default_factory=list)  # role，不是 DSH id
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)  # work_id
    provenance: Provenance
    resume_state: ResumeState


class WorkEvent(BaseModel):
    """
    WorkEvent（2D §3）：append-only event log 的一筆。

    current Work state = fold(events)。DSH session 不是 durable store，
    只是 execution 的 audit sidecar。
    """
    work_id: str
    event_type: WorkEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)
    provenance: Provenance


class HandoffResult(BaseModel):
    """
    HandoffResult（2A §6）：Specialist 回傳的結構化 result，不是 chat transcript。

    result_type 只能是 artifact / evidence / decision（不得有 approval）。
    """
    work_id: str
    role: str
    result_type: ResultType
    artifact_refs: list[str] = Field(default_factory=list)  # 內容定址 ref
    evidence_refs: list[str] = Field(default_factory=list)
    decision: dict[str, Any] = Field(default_factory=dict)
    status: HandoffStatus
    resume_hint: dict[str, Any] = Field(default_factory=dict)
