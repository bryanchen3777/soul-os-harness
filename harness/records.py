"""
harness/records.py — GrowthProbeRecord schema + run header + JSONL 写入 (TL-1)

TL-0 规格 §4 (D1, 已拍板):
  - canonical evidence 只存现有 pipeline 的原始输出 (原文照存, 不解析/打分/改写)。
  - derived 解析标 derived, 写独立 analysis/ 流, 永不回写 canonical。
  - harness 簿记 (experiment_id / run_id / seed / checkpoint / sim_ts / stimulus /
    experience-sequence hash) 是实验元数据, 正常入库。
  - append-only JSONL, 一条 record = 一个 run 在一个 checkpoint 的 probe 结果。

布局 (§7.1):
  <run_id>/run.json              — run header (每 run 一条)
  <run_id>/records/<checkpoint>.jsonl — probe records (每 checkpoint 一条)
  <run_id>/raw/                  — raw outputs (LLM 原文, 可选)
  <run_id>/analysis/             — derived 解析 (与 canonical 物理分离)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ───────────────────────────────────────────────────────────
# Run header (§4.2)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RunHeader:
    """每 run 一条 (放 <run_id>/run.json)。"""
    experiment_id: str
    run_id: str
    seed: int
    fixture_script_ref: str
    soul_id: str
    llm_model: str
    llm_temperature: float
    pipeline_version: str
    data_root: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────
# Probe record (§4.2 canonical 字段)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GrowthProbeRecord:
    """每 checkpoint 一条 (canonical evidence, 全为原文/事实)。

    规范约束 (§4.2): emergent_snapshot / motive_text / decision_text 是
    「原文契约」— 任何解析/分类/打分/翻译/摘要过的内容不得放进这三个字段。
    """
    checkpoint: str                 # "T0" / "T15" / "T30"
    sim_ts: str                     # "D0" / "D15" / "D30"
    stimulus: str                   # probe 原文 (三 checkpoint 逐字相同)
    experience_sequence_hash: str   # SHA256(ordered fed events) 累积摘要
    experience_event_count: int     # 自 T0 以来 fed 事件个数 (T0=0)
    emergent_snapshot: str          # interpretation LLM 原文 (未解析)
    motive_text: str                # motive.content 原文 (无 motive 则空)
    decision_text: str              # decision LLM 原文 (未走到 decision 则空)
    reached_action: bool            # decision=transmit → True (观察事实)
    probe_ts: str                   # 本 record 写入时间 (ISO, harness 簿记)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────
# JSONL 写入 (append-only)
# ───────────────────────────────────────────────────────────

def write_run_header(run_dir: Path, header: RunHeader) -> Path:
    """写 run header (<run_id>/run.json)。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(
        json.dumps(header.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def append_probe_record(run_dir: Path, record: GrowthProbeRecord) -> Path:
    """append 一条 probe record (<run_id>/records/<checkpoint>.jsonl)。"""
    run_dir = Path(run_dir)
    records_dir = run_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / f"{record.checkpoint}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return path


def write_raw_output(run_dir: Path, checkpoint: str, name: str, content: str) -> Path:
    """写 raw output (<run_id>/raw/<checkpoint>_<name>.txt)。"""
    run_dir = Path(run_dir)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{checkpoint}_{name}.txt"
    path.write_text(content or "", encoding="utf-8")
    return path


def append_derived(run_dir: Path, derived: Dict[str, Any]) -> Path:
    """append 一条 derived 解析 (<run_id>/analysis/<run_id>_derived.jsonl)。

    硬规则 (§4.3): derived 层永不写回 canonical store, 永不改写原文。
    """
    run_dir = Path(run_dir)
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / f"{run_dir.name}_derived.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(derived, ensure_ascii=False) + "\n")
    return path


def read_probe_records(run_dir: Path) -> List[Dict[str, Any]]:
    """读回一个 run 的全部 probe records (按 checkpoint 顺序)。"""
    run_dir = Path(run_dir)
    records: List[Dict[str, Any]] = []
    for checkpoint in ("T0", "T15", "T30"):
        path = run_dir / "records" / f"{checkpoint}.jsonl"
        if not path.exists():
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records
