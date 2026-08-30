"""
harness/runner.py — TL-1 run 编排 (隔离 data_root + 执行序列 + determinism) (TL-1)

TL-0 规格 §6.7 / §7 (D7, 已拍板):
  - 隔离 data_root: SOUL_OS_DATA_DIR → data/time_lapse/<experiment_id>/<run_id>/,
    0 production mutation (跑前跑后 production data_root 逐档 byte-hash 0 diff)。
  - 每 run 执行序列 (§6.7):
      1. 建立 run 隔离 data_root
      2. 初始化 seeded Soul (persona + seeded memory baseline)
      3. T0: probe → GrowthProbeRecord(T0)
      4. SimulationClock: 依序喂 D1-D15 事件
      5. T15: probe → GrowthProbeRecord(T15)
      6. SimulationClock: 依序喂 D16-D30 事件
      7. T30: probe → GrowthProbeRecord(T30)
      8. 产出 canonical records + raw outputs; derived 解析另行产出
      9. 决定性验证: 同 fixture 再跑 run_2、run_3 → §5 比对
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.paths import data_root, reset_data_root

from .clock import SimulationClock
from .fixture import (
    TL1_EXPERIMENT_ID,
    TL1_FIXTURE_SCRIPT_REF,
    TL1_SEED,
    TL1_SOUL_ID,
    TL1_STIMULUS,
    FixtureEvent,
    build_script,
    experience_sequence_hash,
    inject_event,
    seed_soul,
)
from .observer import Observer
from .probe import GrowthProbe
from .records import (
    GrowthProbeRecord,
    RunHeader,
    append_derived,
    append_probe_record,
    read_probe_records,
    write_raw_output,
    write_run_header,
)

logger = logging.getLogger("soul_os.harness.runner")

# 隔离 data_root 的挂载点 (production data/ 之下, harness 唯一写区, §7.1)
TIME_LAPSE_DIR_NAME = "time_lapse"

# 0 mutation 验证时跳过的 harness 写区 (data/time_lapse/)
_MUTATION_SKIP_DIRS = {TIME_LAPSE_DIR_NAME}

# 0 mutation 验证时跳过的 production server 运行时文件 (并发活动, 非 harness 写入):
# *.log / *.err / *.pid / *.txt / *.bak / *.old / *.tmp — production server
# (heartbeat_trace.log / faulthandler.log 等) 在 harness 实验期间可能并发写入。
# harness 的 0 mutation 契约只约束「production 数据文件」, 不约束 server 日志。
_MUTATION_SKIP_EXTS = {
    ".log", ".err", ".pid", ".txt", ".bak", ".old", ".tmp",
}


# ───────────────────────────────────────────────────────────
# 0 mutation 验证 (§7.2 规则 3)
# ───────────────────────────────────────────────────────────

def snapshot_data_root_hashes(data_root_dir: Path) -> Dict[str, str]:
    """对 production data_root 列出逐档 byte hash (跳过 harness 写区)。

    Returns:
        {相对路径: sha256 hex} — 只含 run 前已存在的文件。
    """
    data_root_dir = Path(data_root_dir)
    snapshot: Dict[str, str] = {}
    if not data_root_dir.exists():
        return snapshot
    for path in sorted(data_root_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root_dir)
        if rel.parts and rel.parts[0] in _MUTATION_SKIP_DIRS:
            continue
        if path.suffix.lower() in _MUTATION_SKIP_EXTS:
            continue
        snapshot[rel.as_posix()] = _sha256_file(path)
    return snapshot


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zero_mutation(
    data_root_dir: Path, before: Dict[str, str]
) -> Dict[str, Any]:
    """run 系列结束后重算 production data_root hash, 与 before 比对。

    Returns:
        {"pass": bool, "diff": {path: (before_hash, after_hash)}, "added": [...]}
    """
    after = snapshot_data_root_hashes(data_root_dir)
    diff: Dict[str, tuple[str, str]] = {}
    for path, h_before in before.items():
        h_after = after.get(path)
        if h_after != h_before:
            diff[path] = (h_before, h_after or "<missing>")
    # 新增文件 (run 前不存在, run 后出现, 且不在 harness 写区)
    added = sorted(set(after.keys()) - set(before.keys()))
    return {"pass": not diff and not added, "diff": diff, "added": added}


# ───────────────────────────────────────────────────────────
# LLM call 工厂
# ───────────────────────────────────────────────────────────

def make_real_llm_call(
    model: Optional[str] = None, temperature: float = 0.0
) -> Callable[..., Any]:
    """构造真实 LLM call (temperature=0, TL-0 §5.1)。

    用 configs.loader.create_llm_backend (不需要 bus), 直接调 backend.complete。
    temperature 强制 0 (忽略调用方传入值, 保证 probe path 一律 temperature=0)。
    """
    from configs.loader import create_llm_backend, load_config

    cfg = load_config()
    backend = create_llm_backend(cfg)
    resolved_model = model or cfg.get("llm", {}).get("model", "gpt-4o-mini")

    async def llm_call(
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        try:
            # TL-0 §5.1 规则 2: probe path (及 run 期间 pipeline 的 LLM 呼叫)
            # 一律 temperature=0。忽略调用方传入值 (decide_motive 内部传 0.3),
            # 强制 0, 保证系列内采样方差最小化。
            return await backend.complete(
                messages,
                model=resolved_model,
                max_tokens=max_tokens,
                temperature=0.0,
            )
        except Exception as e:  # noqa: BLE001 — fail-closed: 失败返回 None
            logger.warning(
                f"[TL-1] LLM call 失败 (fail-closed=None): "
                f"{type(e).__name__}: {e}"
            )
            return None

    return llm_call


def make_stub_llm_call(
    responses: Optional[Dict[str, List[Optional[str]]]] = None,
) -> Callable[..., Any]:
    """构造 stub LLM call (确定性, 测试用)。

    responses: {"interpretation": [...], "decision": [...]} 响应队列;
               缺省用确定性默认响应。
    """
    responses = responses or {}
    interp_queue = list(responses.get("interpretation", []))
    decision_queue = list(responses.get("decision", []))
    calls: List[Dict[str, Any]] = []

    async def llm_call(
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        calls.append(
            {
                "messages": messages,
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        content = messages[-1]["content"] if messages else ""
        if "has_motive" in content and "decision" not in content:
            return interp_queue.pop(0) if interp_queue else '{"has_motive": false}'
        if "decision" in content:
            return decision_queue.pop(0) if decision_queue else (
                '{"decision": "not_transmit", "reason": "stub"}'
            )
        return None

    llm_call.calls = calls  # type: ignore[attr-defined]
    return llm_call


# ───────────────────────────────────────────────────────────
# TL-1 Runner
# ───────────────────────────────────────────────────────────

class TL1Runner:
    """TL-1 Same-Stimulus Longitudinal Growth Test 编排器。"""

    def __init__(
        self,
        repo_root: Path,
        llm_call: Callable[..., Any],
        seed: int = TL1_SEED,
        experiment_id: str = TL1_EXPERIMENT_ID,
        soul_id: str = TL1_SOUL_ID,
        llm_model: str = "unknown",
        llm_temperature: float = 0.0,
        pipeline_version: str = "unknown",
    ) -> None:
        self._repo_root = Path(repo_root)
        self._llm_call = llm_call
        self._seed = seed
        self._experiment_id = experiment_id
        self._soul_id = soul_id
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._pipeline_version = pipeline_version
        self._observer = Observer()
        self._script = build_script(seed=seed)

    # ── 单 run ───────────────────────────────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一个完整 run (§6.7 序列 1-8)。

        Returns:
            {"run_id", "run_dir", "records": [T0, T15, T30], "header": {...}}
        """
        run_id = run_id or uuid.uuid4().hex
        harness_root = self._repo_root / "data" / TIME_LAPSE_DIR_NAME / self._experiment_id
        run_dir = harness_root / run_id

        # 1. 建立 run 隔离 data_root (SOUL_OS_DATA_DIR → run_dir)
        os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
        reset_data_root()
        isolated_root = data_root()

        # 2. 初始化 seeded Soul (persona baseline + seeded memory baseline)
        seed_soul(isolated_root, agent_id=self._soul_id)

        # 3-7. 执行序列 (T0 probe → D1-D15 → T15 probe → D16-D30 → T30 probe)
        clock = SimulationClock(start_day=0)
        probe = GrowthProbe(agent_id=self._soul_id, llm_call=self._llm_call)

        fed_events: List[FixtureEvent] = []
        records: List[Dict[str, Any]] = []

        # T0 (D0, 喂任何事件前)
        t0 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T0",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t0)

        # 喂 D1-D15
        for ev in self._script:
            if ev.day_index > 15:
                break
            inject_event(isolated_root, ev, clock.sim_ts(ev.day_index), self._soul_id)
            fed_events.append(ev)
        clock.advance(15)

        # T15 (D15)
        t15 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T15",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t15)

        # 喂 D16-D30
        for ev in self._script:
            if ev.day_index <= 15:
                continue
            inject_event(isolated_root, ev, clock.sim_ts(ev.day_index), self._soul_id)
            fed_events.append(ev)
        clock.advance(15)

        # T30 (D30)
        t30 = self._probe_and_record(
            probe=probe,
            clock=clock,
            checkpoint="T30",
            fed_events=list(fed_events),
            run_dir=run_dir,
        )
        records.append(t30)

        # 8. run header
        header = RunHeader(
            experiment_id=self._experiment_id,
            run_id=run_id,
            seed=self._seed,
            fixture_script_ref=TL1_FIXTURE_SCRIPT_REF,
            soul_id=self._soul_id,
            llm_model=self._llm_model,
            llm_temperature=self._llm_temperature,
            pipeline_version=self._pipeline_version,
            data_root=str(isolated_root),
        )
        write_run_header(run_dir, header)

        # derived 解析 (独立 analysis/ 流, 不回写 canonical)
        for rec in records:
            derived = self._observer.observe(rec)
            append_derived(run_dir, derived)

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "header": header.to_dict(),
        }

    def _probe_and_record(
        self,
        probe: GrowthProbe,
        clock: SimulationClock,
        checkpoint: str,
        fed_events: List[FixtureEvent],
        run_dir: Path,
    ) -> Dict[str, Any]:
        """执行一次 probe + 写 canonical record + raw output。"""
        import asyncio

        output = asyncio.run(
            probe.run(
                stimulus=TL1_STIMULUS,
                checkpoint=checkpoint,
                sim_ts=clock.label(),
                fed_events=fed_events,
            )
        )

        record = GrowthProbeRecord(
            checkpoint=checkpoint,
            sim_ts=clock.label(),
            stimulus=output.stimulus,
            experience_sequence_hash=experience_sequence_hash(fed_events),
            experience_event_count=len(fed_events),
            emergent_snapshot=output.emergent_snapshot,
            motive_text=output.motive_text,
            decision_text=output.decision_text,
            reached_action=output.reached_action,
            probe_ts=output.motive.created_at if output.motive else _utcnow_iso(),
        )
        append_probe_record(run_dir, record)
        write_raw_output(run_dir, checkpoint, "emergent", output.emergent_snapshot)
        write_raw_output(run_dir, checkpoint, "motive", output.motive_text)
        write_raw_output(run_dir, checkpoint, "decision", output.decision_text)
        return record.to_dict()

    # ── run 系列 (D2 determinism) ────────────────────────

    def run_series(self, n_runs: int = 3) -> Dict[str, Any]:
        """同 fixture 连跑 n_runs 次, 做 determinism 比对 (§5)。

        Returns:
            {"runs": [...], "determinism": {...}, "mutation": {...}}
        """
        production_root = self._repo_root / "data"

        # 0 mutation 验证: run 前快照
        before = snapshot_data_root_hashes(production_root)

        runs: List[Dict[str, Any]] = []
        for i in range(n_runs):
            logger.info(f"[TL-1] run {i + 1}/{n_runs} 开始")
            run = self.run_once()
            runs.append(run)
            logger.info(f"[TL-1] run {i + 1}/{n_runs} 完成: {run['run_id']}")

        # determinism 比对 (跨 run, 每 checkpoint)
        determinism = self._observer.derive_determinism(
            [{"run_id": r["run_id"], "records": r["records"]} for r in runs]
        )

        # 0 mutation 验证: run 后重算
        mutation = verify_zero_mutation(production_root, before)

        return {
            "runs": runs,
            "determinism": determinism,
            "mutation": mutation,
        }


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
