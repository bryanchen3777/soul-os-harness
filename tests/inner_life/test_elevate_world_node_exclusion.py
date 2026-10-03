"""P0 regression: world nodes (agent_id="default") must never be elevated.

2026-10-03, Owner ruling: 「沉澱成信念需要自己的經歷，光看完就沉澱是不合理的」

`elevate_matured_patterns()` is an **independent** path from the Submission Gate
(`run_server.py::_elevate_check`, EL-DD-2). EH-2.1 R1 only guards `consume()`.
Before the fix, world (default) patterns were promoted to belief/value/trait —
measured 4,125 nodes, 2,804 of them agent_id="default", incl. 910 beliefs whose
content was raw news headlines.

Isolated: synthetic nodes/edges in a tmp dir. No production data, no waiting.
"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.inner_life.elevation_adapter import elevate_matured_patterns  # noqa: E402
from src.inner_life.emergent_projection import (  # noqa: E402
    DEFAULT_AGENT_ID,
    project_emergent,
)


def _nid() -> str:
    return uuid.uuid4().hex


def _pattern(agent: str, content: str) -> dict:
    return {
        "node_id": _nid(),
        "node_type": "pattern",
        "content": content,
        "agent_id": agent,
        "candidate_node_type": "belief",
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": 1.0,
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
    }


def _edge(src: str, node_id: str, agent: str) -> dict:
    # EvidenceEdge 真實簽章（soul_elevation/models.py:159-176）：
    # 必填 edge_id / node_id / source_type / source_id / agent_id / weight /
    # valid_from_ts / valid_until_ts。source_type 不含 "world_event"，
    # 故用 inner_life_event（世界事件在該路徑的合法表示）。
    return {
        "edge_id": _nid(),
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": src,
        "agent_id": agent,
        "weight": 1.0,
        "inner_life_event_id": src,
        "trigger_type": "diary:night",
        "valid_from_ts": "2026-10-03T00:00:00+00:00",
        "valid_until_ts": None,
    }


def _build(tmp_path: pathlib.Path, agents, evidence: int = 2) -> None:
    nodes, edges = [], []
    for agent, content in agents:
        ids = []
        for _ in range(evidence):
            p = _pattern(agent, content)
            nodes.append(p)
            ids.append(p["node_id"])
        for i, pid in enumerate(ids):
            edges.append(_edge(f"src-{agent}-{i}-{pid[:6]}", pid, agent))
    (tmp_path / "elevation_nodes.jsonl").write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "elevation_edges.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in edges) + "\n",
        encoding="utf-8",
    )


NEWS = "Trump hails 'historic' deal for US to control 65bn barrels of Venezuela's oil"
LIVED = "Bry 提到秋天的第一封信，讓我記得要在回覆前先想好怎麼說"


def test_world_only_produces_zero_nodes(tmp_path):
    """只有 world(default) 節點時，昇華必須產出 0 個節點。"""
    _build(tmp_path, [(DEFAULT_AGENT_ID, NEWS), (DEFAULT_AGENT_ID, NEWS + " (2)")])
    out = elevate_matured_patterns(store_dir=tmp_path)
    assert out == [], f"world 節點不該被昇華，卻產出 {len(out)} 個：{[n.node_type for n in out]}"


def test_world_skipped_while_agent_still_elevated(tmp_path):
    """混合情境：world 不昇華，但 per-agent 不得被誤傷。"""
    _build(
        tmp_path,
        [(DEFAULT_AGENT_ID, NEWS), ("agent_ruka", LIVED)],
        evidence=3,
    )
    out = elevate_matured_patterns(store_dir=tmp_path)
    by_agent = {}
    for n in out:
        by_agent.setdefault(n.agent_id, []).append(n.node_type)
    assert DEFAULT_AGENT_ID not in by_agent, f"world 不該被昇華：{by_agent[DEFAULT_AGENT_ID]}"
    assert "agent_ruka" in by_agent, "per-agent 昇華路徑被誤傷（修正不得破壞正常路徑）"


def test_all_world_returns_empty_not_error(tmp_path):
    """全部都是 world 時應回 []（含 early-return），不得 raise。"""
    _build(tmp_path, [(DEFAULT_AGENT_ID, NEWS)] * 4, evidence=1)
    out = elevate_matured_patterns(store_dir=tmp_path)
    assert out == []


def test_read_side_invariant_still_holds(tmp_path):
    """read 側對稱性：world 節點永不投影（既有不變式，不得被本次修正破壞）。"""
    _build(tmp_path, [(DEFAULT_AGENT_ID, NEWS), ("agent_ruka", LIVED)], evidence=2)
    assert project_emergent(DEFAULT_AGENT_ID, store_dir=tmp_path) == []
