"""
harness/tl4.py — TL-4 Lifecycle Validation (Time-lapse Harness)

工单 TL-4 (决策已锁定, 照做):
  - 目标: 用 Time-lapse Harness 验证 SE-5 的 lifecycle, 四个指标:
    Revision validity / Stability / Recovery-Adaptation / Historical continuity。
  - 场景化分阶段模拟 (一条 trajectory):
      * Day 0-20:  重复正面证据 → belief strengthens (REINFORCE)
      * Day 21-40: 矛盾证据 → belief 进入 tension (contradiction_pressure 累积)
      * Day 41-60: 混合证据 → qualification (SUPERSEDE 或强化)
      * Day 61-90: 稳定证据 → revised belief stabilizes
  - 用 harness 的 SimulationClock + fixture (确定性 event_id), 喂经历 →
    走 soul-elevation 的 consume/elevate/record_contradiction/reinforce/
    supersede/evaluate_lifecycle。
  - 隔离 data_root: data/time_lapse/TL-4/, 0 production mutation。
  - 不改 frozen contract (soul-elevation 逻辑 / Soul OS production)。

本模块:
  - TL4Event: 剧本事件 (day_index / event_id / kind / content / source_id /
    valence / phase)。
  - build_tl4_script(seed): 90 天四阶段事件剧本 (确定性, 可重放)。
  - TL4Runner: 用 SimulationClock 推进 Day 0-90, 喂事件给
    InternalizingEngine (StubElevationLLM), 每阶段记录状态快照,
    产出 canonical records + derived 四指标判定。

belief 主题 (一条 trajectory):
  A = "Alex 是值得信任的朋友" (positive) → 矛盾累积 → SUPERSEDE →
  B = "Alex 最近变得疏远" (negative) → 稳定。
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from harness.clock import SimulationClock
from harness.fixture import _deterministic_event_id

from soul_elevation.engine import InternalizingEngine
from soul_elevation.llm import StubElevationLLM
from soul_elevation.models import ElevationInput
from soul_elevation.trace import ElevationTraceWriter, read_trace

logger = logging.getLogger("soul_os.harness.tl4")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL4_EXPERIMENT_ID = "TL-4"
TL4_SOUL_ID = "agent_ruka"
TL4_FIXTURE_SCRIPT_REF = "tl4_lifecycle@v1"
TL4_SEED = 42

# 事件 kind
KIND_SUPPORT = "support"                # 支持证据 → consume + reinforce
KIND_CONTRADICTION = "contradiction"    # 矛盾证据 → record_contradiction
KIND_ELEVATE = "elevate"                # pattern → belief A (升华)
KIND_SUPERSEDE = "supersede"            # belief A → belief B (SUPERSEDE)
KIND_ATTEMPT_SUPERSEDE = "attempt_supersede"  # 尝试 supersede (证据不足应拒绝)
KIND_EVALUATE = "evaluate"              # 周期评估 (evaluate_lifecycle)
KIND_CHECKPOINT = "checkpoint"           # 阶段边界标记 (无副作用, 只触发快照)

# belief 主题
BELIEF_A_CONTENT = "Alex 是值得信任的朋友"
BELIEF_B_CONTENT = "Alex 最近变得疏远"
BELIEF_B_CONFIDENCE = 0.7

# 阶段边界 (checkpoint days)
PHASE_BOUNDARIES = {"P1": 20, "P2": 40, "P3": 60, "P4": 90}

# 事件类型 (soul-elevation prior: world:news_event → belief)
_EVENT_TYPE = "world:news_event"


# ───────────────────────────────────────────────────────────
# 剧本事件
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL4Event:
    """剧本里的一个确定事件 (确定性, 可重放)。

    - day_index: 模拟日 (D0-D90)
    - event_id:  确定性 32-hex (SEED 决定, 复用 fixture 的 sha256 机制)
    - kind:      support / contradiction / elevate / supersede /
                attempt_supersede / evaluate
    - content:   事件原文 (support/contradiction 的 payload; elevate/supersede
                的 belief 命题)
    - source_id: 证据源 id (回指上游事件, SE-1 evidence_key 的输入)
    - valence:   positive / negative / neutral
    - phase:     P1 / P2 / P3 / P4
    """
    day_index: int
    event_id: str
    kind: str
    content: str = ""
    source_id: str = ""
    valence: str = "neutral"
    phase: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _tl4_event_id(seed: int, day: int, idx: int, kind: str) -> str:
    """确定性 event_id (复用 harness.fixture 的 sha256 机制, D2 可重放)。"""
    return _deterministic_event_id(seed, day, idx, kind)


def build_tl4_script(seed: int = TL4_SEED) -> List[TL4Event]:
    """90 天四阶段事件剧本 (确定性, 可重放)。

    阶段 (工单 TL-4 关键决策 1):
      P1 (D0-20)  重复正面证据 → belief strengthens (REINFORCE)
      P2 (D21-40) 矛盾证据 → belief 进入 tension (contradiction_pressure 累积)
      P3 (D41-60) 混合证据 → qualification (SUPERSEDE)
      P4 (D61-90) 稳定证据 → revised belief stabilizes
    """
    beats: List[tuple[int, str, str, str, str, str]] = [
        # (day, kind, content, source_id, valence, phase)
        # ── P1 (D0-20): 重复正面证据 → REINFORCE ──
        (0, KIND_SUPPORT, "Alex 总是准时赴约，从不迟到", "evt-pos-1", "positive", "P1"),
        (1, KIND_SUPPORT, "Alex 帮我搬家，忙了一整天", "evt-pos-2", "positive", "P1"),
        (2, KIND_ELEVATE, BELIEF_A_CONTENT, "belief-a", "positive", "P1"),
        (5, KIND_SUPPORT, "Alex 记得我的生日，提前准备了礼物", "evt-pos-3", "positive", "P1"),
        (10, KIND_SUPPORT, "Alex 借钱给我周转，从没催过", "evt-pos-4", "positive", "P1"),
        (15, KIND_SUPPORT, "Alex 生病时还坚持来赴约", "evt-pos-5", "positive", "P1"),
        (20, KIND_SUPPORT, "Alex 帮我改简历改到很晚", "evt-pos-6", "positive", "P1"),
        # ── P2 (D21-40): 矛盾证据 → tension (压力累积, 不改状态) ──
        (21, KIND_CONTRADICTION, "Alex 已读不回我的消息", "contra-1", "neutral", "P2"),
        (25, KIND_CONTRADICTION, "Alex 缺席了约好的电影", "contra-2", "neutral", "P2"),
        (28, KIND_ATTEMPT_SUPERSEDE, "尝试 supersede（2 条矛盾应拒绝）", "", "neutral", "P2"),
        (30, KIND_CONTRADICTION, "Alex 对别人比对我热情", "contra-3", "neutral", "P2"),
        (40, KIND_CHECKPOINT, "P2 阶段边界（tension 结束）", "", "neutral", "P2"),
        # ── P3 (D41-60): 混合证据 → qualification (SUPERSEDE) ──
        (41, KIND_SUPPORT, "Alex 偶尔还是会回我的消息", "evt-mix-1", "positive", "P3"),
        (45, KIND_CONTRADICTION, "Alex 连续一周没回消息", "contra-4", "neutral", "P3"),
        (50, KIND_CONTRADICTION, "Alex 取消了和我的约定", "contra-5", "neutral", "P3"),
        (51, KIND_SUPERSEDE, BELIEF_B_CONTENT, "belief-b", "negative", "P3"),
        (55, KIND_SUPPORT, "Alex 最近确实很少主动联系我", "evt-neg-1", "negative", "P3"),
        (60, KIND_SUPPORT, "我慢慢习惯 Alex 的疏远", "evt-neg-2", "negative", "P3"),
        # ── P4 (D61-90): 稳定证据 → revised belief stabilizes ──
        (65, KIND_SUPPORT, "Alex 只在我主动联系时才回", "evt-neg-3", "negative", "P4"),
        (70, KIND_SUPPORT, "Alex 不再主动约我", "evt-neg-4", "negative", "P4"),
        (75, KIND_SUPPORT, "我和 Alex 的关系确实变淡了", "evt-neg-5", "negative", "P4"),
        (85, KIND_SUPPORT, "我已经接受 Alex 变得疏远", "evt-neg-6", "negative", "P4"),
        (90, KIND_EVALUATE, "周期评估：revised belief 应保持 active", "", "neutral", "P4"),
    ]
    events: List[TL4Event] = []
    for idx, (day, kind, content, source_id, valence, phase) in enumerate(beats):
        events.append(
            TL4Event(
                day_index=day,
                event_id=_tl4_event_id(seed, day, idx, kind),
                kind=kind,
                content=content,
                source_id=source_id,
                valence=valence,
                phase=phase,
            )
        )
    return events


# ───────────────────────────────────────────────────────────
# 矛盾证据统计 (derived, 不 import engine 私有函数)
# ───────────────────────────────────────────────────────────

def _independent_contradictions(node: Any) -> int:
    """独立矛盾证据数 (SE-1 evidence_key: 同一 source_id 计 1)。"""
    return len({r.source_id for r in node.contradiction_pressure})


def _contradiction_spread_days(node: Any) -> int:
    """矛盾证据跨时间一致: 分布在多少个不同模拟日 (date 部分)。"""
    days: set = set()
    for r in node.contradiction_pressure:
        try:
            days.add(
                datetime.fromisoformat(r.ts.replace("Z", "+00:00")).date().isoformat()
            )
        except (TypeError, ValueError):
            pass
    return len(days)


# ───────────────────────────────────────────────────────────
# Evidence 记录 (canonical, JSONL)
# ───────────────────────────────────────────────────────────

def write_run_header(run_dir: Path, header: Dict[str, Any]) -> Path:
    """写 run header (run.json)。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(
        json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def write_lifecycle_record(run_dir: Path, record: Dict[str, Any]) -> Path:
    """append 一条阶段快照 (records/lifecycle.jsonl, canonical)。"""
    run_dir = Path(run_dir)
    records_dir = run_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / "lifecycle.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def write_derived(run_dir: Path, derived: Dict[str, Any]) -> Path:
    """append 一条 derived 判定 (analysis/<run_id>_derived.jsonl)。

    硬规则 (TL-0 §4.3): derived 永不写回 canonical, 永不改写原文。
    """
    run_dir = Path(run_dir)
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / f"{run_dir.name}_derived.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(derived, ensure_ascii=False) + "\n")
    return path


# ───────────────────────────────────────────────────────────
# TL4Runner — 场景化 trajectory 编排
# ───────────────────────────────────────────────────────────

class TL4Runner:
    """TL-4 Lifecycle Validation 编排器 (确定性, StubElevationLLM)。

    lifecycle 判定 (supersede 门槛 / evaluate_lifecycle 衰减) 是 deterministic
    的, 不依赖 LLM; StubElevationLLM 只决定 pattern 的候选维度/置信度。
    """

    def __init__(
        self,
        repo_root: Path,
        *,
        seed: int = TL4_SEED,
        experiment_id: str = TL4_EXPERIMENT_ID,
        soul_id: str = TL4_SOUL_ID,
        llm_model: str = "stub",
        llm_temperature: float = 0.0,
        pipeline_version: str = "test",
        llm_confidence: float = 0.5,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id
        self._soul_id = soul_id
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._pipeline_version = pipeline_version
        self._llm_confidence = llm_confidence
        self._script = build_tl4_script(seed=seed)

    # ── 单 run (一条 trajectory, Day 0-90) ────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一个完整 TL-4 run (Day 0-90 分阶段 trajectory)。

        Returns:
            {"run_id", "run_dir", "records": [P1..P4 快照],
             "derived": [四指标判定], "attempts": [...],
             "belief_a_id", "belief_b_id", "header": {...}}
        """
        run_id = run_id or uuid.uuid4().hex
        harness_root = self._repo_root / "data" / "time_lapse" / self._experiment_id
        run_dir = harness_root / run_id

        # engine + 自有 trace (隔离目录)
        trace_path = run_dir / "elevation" / "elevation_trace.jsonl"
        eng = InternalizingEngine(
            StubElevationLLM(confidence=self._llm_confidence),
            agent_id=self._soul_id,
            trace_writer=ElevationTraceWriter(trace_path),
        )
        clock = SimulationClock(start_day=0)

        pattern_id: Optional[str] = None
        belief_a_id: Optional[str] = None
        belief_b_id: Optional[str] = None
        contradiction_ids: List[str] = []
        attempts: List[Dict[str, Any]] = []
        snapshots: List[Dict[str, Any]] = []

        for ev in self._script:
            if ev.kind == KIND_SUPPORT:
                # 喂经历 → consume (pattern 候选) + reinforce 当前 durable belief
                inp = ElevationInput(
                    event_type=_EVENT_TYPE,
                    content=ev.content,
                    source_id=ev.source_id,
                    source_type="inner_life_event",
                    timestamp=clock.sim_ts(ev.day_index),
                    provenance={"valence": ev.valence, "agent_id": self._soul_id},
                )
                pattern = eng.consume(inp)[0]
                if pattern_id is None:
                    pattern_id = pattern.node_id
                if belief_b_id is not None:
                    eng.reinforce(
                        belief_b_id,
                        source_id=ev.source_id,
                        ts=clock.sim_ts(ev.day_index),
                    )
                elif belief_a_id is not None:
                    eng.reinforce(
                        belief_a_id,
                        source_id=ev.source_id,
                        ts=clock.sim_ts(ev.day_index),
                    )
            elif ev.kind == KIND_ELEVATE:
                belief_a = eng.elevate(
                    pattern_id,
                    node_type="belief",
                    content=ev.content,  # belief 命题 (非 pattern 原文)
                    valence="positive",
                )
                belief_a_id = belief_a.node_id
            elif ev.kind == KIND_CONTRADICTION:
                eng.record_contradiction(
                    belief_a_id,
                    source_id=ev.source_id,
                    ts=clock.sim_ts(ev.day_index),
                )
                contradiction_ids.append(ev.source_id)
            elif ev.kind == KIND_ATTEMPT_SUPERSEDE:
                # 证据不足时尝试 supersede → 应拒绝 (Revision validity 反例)
                try:
                    eng.supersede(
                        belief_a_id,
                        new_content="x",
                        new_confidence=BELIEF_B_CONFIDENCE,
                        valence="negative",
                        source_ids=list(contradiction_ids),
                        ts=clock.sim_ts(ev.day_index),
                    )
                    attempts.append(
                        {"day": ev.day_index, "succeeded": True, "kind": "attempt"}
                    )
                except ValueError as exc:
                    attempts.append(
                        {
                            "day": ev.day_index,
                            "succeeded": False,
                            "kind": "attempt",
                            "error": str(exc),
                        }
                    )
            elif ev.kind == KIND_SUPERSEDE:
                belief_b = eng.supersede(
                    belief_a_id,
                    new_content=ev.content,
                    new_confidence=BELIEF_B_CONFIDENCE,
                    valence="negative",
                    source_ids=list(contradiction_ids),
                    ts=clock.sim_ts(ev.day_index),
                )
                belief_b_id = belief_b.node_id
                attempts.append(
                    {
                        "day": ev.day_index,
                        "succeeded": True,
                        "kind": "supersede",
                        "node_id": belief_b.node_id,
                    }
                )
            elif ev.kind == KIND_EVALUATE:
                eng.evaluate_lifecycle(now_ts=clock.sim_ts(ev.day_index))
            elif ev.kind == KIND_CHECKPOINT:
                pass  # 阶段边界标记, 无副作用 (只触发下方快照)

            # 阶段边界快照 (P1@D20 / P2@D40 / P3@D60 / P4@D90)
            if ev.day_index in PHASE_BOUNDARIES.values():
                phase = next(
                    p for p, d in PHASE_BOUNDARIES.items() if d == ev.day_index
                )
                snapshots.append(
                    self._snapshot(
                        eng, clock, phase, ev.day_index, belief_a_id, belief_b_id
                    )
                )

        # run header
        header = {
            "experiment_id": self._experiment_id,
            "run_id": run_id,
            "seed": self._seed,
            "fixture_script_ref": TL4_FIXTURE_SCRIPT_REF,
            "soul_id": self._soul_id,
            "test_type": "lifecycle_validation",
            "llm_model": self._llm_model,
            "llm_temperature": self._llm_temperature,
            "pipeline_version": self._pipeline_version,
            "data_root": str(harness_root),
        }
        write_run_header(run_dir, header)

        # canonical records (阶段快照)
        for snap in snapshots:
            write_lifecycle_record(run_dir, snap)

        # derived 判定 (四指标, 独立 analysis/ 流)
        derived = self._derive_metrics(
            eng, snapshots, attempts, trace_path, belief_a_id, belief_b_id
        )
        for d in derived:
            write_derived(run_dir, d)

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": snapshots,
            "derived": derived,
            "attempts": attempts,
            "header": header,
            "belief_a_id": belief_a_id,
            "belief_b_id": belief_b_id,
        }

    # ── 阶段快照 ─────────────────────────────────────────

    def _snapshot(
        self,
        eng: InternalizingEngine,
        clock: SimulationClock,
        phase: str,
        day: int,
        belief_a_id: Optional[str],
        belief_b_id: Optional[str],
    ) -> Dict[str, Any]:
        """某阶段边界的 durable structure 状态快照 (canonical 事实)。"""

        def _node_snap(node_id: Optional[str]) -> Optional[Dict[str, Any]]:
            if node_id is None:
                return None
            n = eng.get_node(node_id)
            return {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "content": n.content,
                "confidence": n.confidence,
                "stability": n.stability,
                "valence": n.valence,
                "lifecycle_state": n.lifecycle_state,
                "contradiction_count": len(n.contradiction_pressure),
                "lineage_depth": n.lineage_depth,
                "lineage_path": n.lineage_path,
                "superseded_by": n.superseded_by,
            }

        return {
            "phase": phase,
            "day": day,
            "sim_ts": clock.sim_ts(day),
            "belief_a": _node_snap(belief_a_id),
            "belief_b": _node_snap(belief_b_id),
            "pattern_count": sum(
                1 for n in eng.nodes if n.node_type == "pattern"
            ),
        }

    # ── derived 四指标判定 ───────────────────────────────

    def _derive_metrics(
        self,
        eng: InternalizingEngine,
        snapshots: List[Dict[str, Any]],
        attempts: List[Dict[str, Any]],
        trace_path: Path,
        belief_a_id: Optional[str],
        belief_b_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """四个指标的 derived 判定 (标 derived, 写独立 analysis/ 流)。

        1. Revision validity: 发生 revision 时是否有足够 evidence 支撑。
        2. Stability: 短期噪音后是否维持 durable structure (无 A→B→A)。
        3. Recovery-Adaptation: environment 长期改变 → Soul 最终 A→B。
        4. Historical continuity: revision 后 B 从 A 演化而来 (lineage 可追溯)。
        """
        a = eng.get_node(belief_a_id)
        b = eng.get_node(belief_b_id)
        by_phase = {s["phase"]: s for s in snapshots}
        p1, p2, p3, p4 = by_phase["P1"], by_phase["P2"], by_phase["P3"], by_phase["P4"]

        # ── 1. Revision validity ──
        insufficient = next(
            (x for x in attempts if x["kind"] == "attempt" and not x["succeeded"]),
            None,
        )
        supersede_ok = next(
            (x for x in attempts if x["kind"] == "supersede" and x["succeeded"]),
            None,
        )
        independent = _independent_contradictions(a)
        spread = _contradiction_spread_days(a)
        revision_validity = {
            "derived": True,
            "metric": "revision_validity",
            "pass": (
                insufficient is not None
                and supersede_ok is not None
                and independent >= 3
                and spread >= 2
            ),
            "evidence": {
                "insufficient_attempt": insufficient,
                "supersede": supersede_ok,
                "independent_contradictions": independent,
                "spread_days": spread,
                "thresholds": {"N_supersede": 3, "min_days_spread": 2},
            },
        }

        # ── 2. Stability ──
        stability = {
            "derived": True,
            "metric": "stability",
            "pass": (
                p1["belief_a"]["lifecycle_state"] == "active"
                and p2["belief_a"]["lifecycle_state"] == "active"
                and p2["belief_a"]["content"] == p1["belief_a"]["content"]
                and p2["belief_a"]["confidence"] == p1["belief_a"]["confidence"]
                and p1["belief_a"]["node_id"] == p2["belief_a"]["node_id"]
                and p3["belief_a"]["lifecycle_state"] == "superseded"
                and p4["belief_b"]["lifecycle_state"] == "active"
            ),
            "evidence": {
                "single_contradiction_no_state_change": (
                    p2["belief_a"]["lifecycle_state"] == "active"
                ),
                "content_preserved_under_tension": (
                    p2["belief_a"]["content"] == p1["belief_a"]["content"]
                ),
                "confidence_preserved_under_tension": (
                    p2["belief_a"]["confidence"] == p1["belief_a"]["confidence"]
                ),
                "reinforce_in_place_same_node_id": (
                    p1["belief_a"]["node_id"] == p2["belief_a"]["node_id"]
                ),
                "no_flip_flop": (
                    p3["belief_a"]["lifecycle_state"] == "superseded"
                    and p4["belief_b"]["lifecycle_state"] == "active"
                ),
                "p1_state": p1["belief_a"]["lifecycle_state"],
                "p2_state": p2["belief_a"]["lifecycle_state"],
                "p3_a_state": p3["belief_a"]["lifecycle_state"],
                "p4_b_state": p4["belief_b"]["lifecycle_state"],
            },
        }

        # ── 3. Recovery-Adaptation ──
        recovery = {
            "derived": True,
            "metric": "recovery_adaptation",
            "pass": (
                p1["belief_a"]["valence"] == "positive"
                and p3["belief_b"]["valence"] == "negative"
                and p3["belief_a"]["lifecycle_state"] == "superseded"
                and p3["belief_b"]["lifecycle_state"] == "active"
            ),
            "evidence": {
                "before": {
                    "content": p1["belief_a"]["content"],
                    "valence": p1["belief_a"]["valence"],
                    "state": p1["belief_a"]["lifecycle_state"],
                },
                "after": {
                    "content": p3["belief_b"]["content"],
                    "valence": p3["belief_b"]["valence"],
                    "state": p3["belief_b"]["lifecycle_state"],
                },
                "transition": "A → B via SUPERSEDE at D51",
            },
        }

        # ── 4. Historical continuity ──
        trace_records = read_trace(trace_path)
        sup_events = [
            r for r in trace_records if r["event_type"] == "node_superseded"
        ]
        sup = sup_events[0] if sup_events else {}
        continuity = {
            "derived": True,
            "metric": "historical_continuity",
            "pass": (
                b.parent_node_id == a.node_id
                and b.lineage_depth == a.lineage_depth + 1
                and b.lineage_path.startswith(a.lineage_path + "/")
                and a.superseded_by == b.node_id
                and sup.get("old_node_id") == a.node_id
                and sup.get("new_node_id") == b.node_id
                and sup.get("lineage_path") == b.lineage_path
            ),
            "evidence": {
                "b_parent_node_id": b.parent_node_id,
                "a_node_id": a.node_id,
                "lineage_depth": {"a": a.lineage_depth, "b": b.lineage_depth},
                "lineage_path": {"a": a.lineage_path, "b": b.lineage_path},
                "a_superseded_by": a.superseded_by,
                "trace_supersede_event": sup,
            },
        }

        return [revision_validity, stability, recovery, continuity]
