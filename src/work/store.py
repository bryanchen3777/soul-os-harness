"""
src/work/store.py
WorkStore — append-only WorkEvent JSONL log（2D §3）。

不變性（複用 src/memory/v1/store.py 模式）：
- 寫入：append-only（只能新增，不可改不可刪）
- 讀取：全檔掃描（fold 時按 work_id 過濾）
- corrupt row：跳過並留 log，不修改原檔

路徑約定：
- 單一真相：data_root() / "work" / "work_events.jsonl"
- 傳入 data_dir 可覆寫（測試隔離用），預設 data_root() / "work"

current Work state = fold(events)。DSH session 不是 durable store。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.paths import data_root

from .schema import (
    Provenance,
    ResumeState,
    WorkEvent,
    WorkEventType,
    WorkObject,
    WorkState,
)

logger = logging.getLogger(__name__)


class WorkNotFoundError(KeyError):
    """fold 找不到指定 work_id 的任何 event。"""


def fold_events(events: list[WorkEvent]) -> WorkObject:
    """
    把一組（已按 append 順序）的 WorkEvent fold 成 current WorkObject。

    重建規則：
    - state：最後一筆 state_transition 的 `to`
    - objective / owner / assigned_agents / dependencies：第一筆 state_transition
      （creation event）payload 的 seed 欄位
    - artifacts / evidence / decisions / approvals：各自 event_type 累積
    - provenance：第一筆 event 的 provenance（creator）
    - resume_state：最小重建
        - current_phase：blocked 時 = 進入 blocked 前的 phase，否則 = state
        - last_artifact_refs：最後一筆 artifact_produced 的 provenance.output_refs
        - pending_handoffs / idempotency_keys：MVP-1 不從 WorkEvent 推導，預設 []
    """
    if not events:
        raise WorkNotFoundError("no events to fold")

    work_id = events[0].work_id
    state: WorkState | None = None
    blocked_from: WorkState | None = None  # 進入 blocked 前的 phase（僅 final=blocked 時有意義）
    seed: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    last_artifact_refs: list[str] = []
    provenance: Provenance | None = None
    first_transition_seen = False

    for event in events:
        if provenance is None:
            provenance = event.provenance

        if event.event_type == WorkEventType.STATE_TRANSITION:
            payload = event.payload
            to = payload.get("to")
            from_ = payload.get("from")
            if to is not None:
                state = WorkState(to)
            if not first_transition_seen:
                first_transition_seen = True
                seed = {
                    "objective": payload.get("objective", ""),
                    "owner": payload.get("owner", ""),
                    "assigned_agents": payload.get("assigned_agents", []),
                    "dependencies": payload.get("dependencies", []),
                }
            if state == WorkState.BLOCKED:
                if from_ is not None:
                    blocked_from = WorkState(from_)
            else:
                blocked_from = None  # 已離開 blocked（或正常 transition）
        elif event.event_type == WorkEventType.ARTIFACT_PRODUCED:
            artifacts.append(event.payload.get("artifact", event.payload))
            if event.provenance and event.provenance.output_refs:
                last_artifact_refs = list(event.provenance.output_refs)
        elif event.event_type == WorkEventType.EVIDENCE_PRODUCED:
            evidence.append(event.payload.get("evidence", event.payload))
        elif event.event_type == WorkEventType.DECISION_MADE:
            decisions.append(event.payload.get("decision", event.payload))
        elif event.event_type == WorkEventType.APPROVAL_GRANTED:
            approvals.append(event.payload.get("approval", event.payload))
        # GRANT_ISSUED：immutable durable record，不 fold 進 WorkObject 欄位（2D §6）

    if state is None:
        raise ValueError(f"no state_transition event for work_id={work_id}")

    current_phase = blocked_from if blocked_from is not None else state
    resume_state = ResumeState(
        current_phase=current_phase,
        pending_handoffs=[],
        last_artifact_refs=last_artifact_refs,
        idempotency_keys=[],
    )

    return WorkObject(
        work_id=work_id,
        objective=seed.get("objective", ""),
        owner=seed.get("owner", ""),
        assigned_agents=seed.get("assigned_agents", []),
        dependencies=seed.get("dependencies", []),
        state=state,
        artifacts=artifacts,
        evidence=evidence,
        decisions=decisions,
        approvals=approvals,
        provenance=provenance or Provenance(role="unknown", capability="unknown"),
        resume_state=resume_state,
    )


class WorkStore:
    """append-only WorkEvent JSONL store。

    接受可選 data_dir（測試隔離用），預設 data_root() / "work"。
    只提供 append（寫）與 fold（讀），無 update / delete API。
    """

    def __init__(self, data_dir: Path | str | None = None):
        if data_dir is None:
            data_dir = data_root() / "work"
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.data_dir / "work_events.jsonl"

    def append(self, event: WorkEvent) -> None:
        """append 一筆 WorkEvent（append-only，不可改不可刪）。"""
        with open(self.store_file, "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def fold(self, work_id: str) -> WorkObject:
        """把指定 work_id 的所有 event fold 成 current WorkObject。"""
        events = self._read_events(work_id)
        if not events:
            raise WorkNotFoundError(f"no events for work_id={work_id}")
        return fold_events(events)

    def _read_events(self, work_id: str) -> list[WorkEvent]:
        """全檔掃描，回傳指定 work_id 的 event（按 append 順序）。corrupt row 跳過留 log。"""
        if not self.store_file.exists():
            return []
        events: list[WorkEvent] = []
        with open(self.store_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = WorkEvent(**data)
                    if event.work_id == work_id:
                        events.append(event)
                except (ValueError, TypeError) as e:
                    # corrupt row：不修改原檔（append-only），跳過並留 log
                    logger.warning(
                        "[WorkStore] corrupt row in %s: %s", self.store_file, e
                    )
        return events
