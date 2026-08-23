"""
src/work/ports.py
DSH Adapter Boundary — anti-lock-in ports（Soul OS kernel 使用的介面）。

這些 port 是 Soul Kernel 依賴的「自己的」概念，**不 import 任何 DSH type**：
- 換掉 DSH（或換成其他 runtime substrate）時，kernel 只需換 adapter，不需重寫。
- 所有 port 的資料型別都是 JSON-serializable（language-neutral），
  可跨 Python ↔ TypeScript 邊界傳遞。

Canonical 來源（權威，不得修改）：
- docs/DSH-SOUL-OS-MIGRATION-PLAN.md §3.2（anti-lock-in interfaces）
- docs/DSH-WORK-CONTRACT.md §7（DSH Adapter Mapping Boundary）

port 用 typing.Protocol（結構型別）：kernel 依賴「形狀」而非具體實作，
adapter 不需繼承任何 base class 即可被 kernel 使用（anti-lock-in）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """UTC 帶時區的現在時間（與 schema.py 的 timestamp 慣例一致）。"""
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# 1. 資料型別（JSON-serializable，language-neutral）
# ─────────────────────────────────────────────

class Stimulus(BaseModel):
    """kernel 接收的刺激（SoulRuntimePort.receive 的輸入）。"""
    stimulus_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "world"  # world | human | agency | ...
    timestamp: datetime = Field(default_factory=_utcnow)


class Effect(BaseModel):
    """kernel 產生的 effect（receive / tick 的輸出）。"""
    effect_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    target: str = "presentation"  # presentation | world | store | ...
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class SoulRuntimeSnapshot(BaseModel):
    """snapshot() 的輸出：kernel 狀態快照。"""
    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = "1.0"
    state: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class Checkpoint(BaseModel):
    """recover() 的輸入：最小重建狀態（不是 DSH session snapshot）。"""
    checkpoint_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_version: str = "1.0"
    state: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class WorldQuery(BaseModel):
    """SoulWorldPort.observe 的輸入。"""
    query_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class WorldEvidence(BaseModel):
    """SoulWorldPort.observe 的輸出。"""
    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    source: str = "world"
    timestamp: datetime = Field(default_factory=_utcnow)


class Intent(BaseModel):
    """SoulWorldPort.act 的輸入：capability-neutral 意圖（不是 DSH tool 名）。"""
    intent_id: str = Field(default_factory=lambda: str(uuid4()))
    capability: str  # capability-neutral，如 "git.commit"，不是 DSH tool 名
    action: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class CapabilityResult(BaseModel):
    """SoulWorldPort.act 的輸出。"""
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    capability: str
    status: str  # done | blocked | denied | ...
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class SoulExperience(BaseModel):
    """SoulExperienceStore.append 的輸入：一個生命經歷。"""
    experience_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    content: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class ExperienceQuery(BaseModel):
    """SoulExperienceStore.query 的輸入（history / memory / trace）。"""
    kind: str  # history | memory | trace
    params: dict[str, Any] = Field(default_factory=dict)


class SoulProjection(BaseModel):
    """SoulPresentationPort.publish 的輸入：呈現給外界的 projection。"""
    projection_id: str = Field(default_factory=lambda: str(uuid4()))
    kind: str
    content: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


# ─────────────────────────────────────────────
# 2. Ports（typing.Protocol，結構型別）
# ─────────────────────────────────────────────

@runtime_checkable
class SoulRuntimePort(Protocol):
    """kernel 的 runtime 介面（migration plan §3.2）。"""
    def receive(self, stimulus: Stimulus) -> list[Effect]: ...
    def tick(self, now: datetime) -> list[Effect]: ...
    def recover(self, checkpoint: Checkpoint) -> dict[str, Any]: ...
    def snapshot(self) -> SoulRuntimeSnapshot: ...


@runtime_checkable
class SoulWorldPort(Protocol):
    """kernel 的 world 介面（migration plan §3.2）。"""
    def observe(self, query: WorldQuery) -> list[WorldEvidence]: ...
    def act(self, intent: Intent, policy: dict[str, Any]) -> CapabilityResult: ...


@runtime_checkable
class SoulExperienceStore(Protocol):
    """kernel 的 experience store 介面（migration plan §3.2）。"""
    def append(self, experience: SoulExperience) -> None: ...
    def query(self, query: ExperienceQuery) -> list[SoulExperience]: ...
    def checkpoint(self) -> Checkpoint: ...


@runtime_checkable
class SoulPresentationPort(Protocol):
    """kernel 的 presentation 介面（migration plan §3.2）。"""
    def publish(self, projection: SoulProjection) -> None: ...
