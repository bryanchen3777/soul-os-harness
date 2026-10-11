"""VALUE-DEDUP-A-1: _persist_result 呼叫點冪等（全合成 tmp store_dir）。

口徑：normalize = strip only（見 elevation_adapter._dedup_content_hash；
repo 內無既有全半形正規化工具，按工單 fallback）。

- 同 agent + 同 node_type + 同 content（strip 後 sha256）第二輪跳過
  （nodes 行數不增、邊不增；僅跳過該 node＋其邊）。
- 不同 content / 不同 agent / 不同 node_type 正常落盤。
- 現有檔損壞 / 缺失時 fail-open 照寫（絕不丟合法產出）。
- 不動 engine、不動 consumed 去重、不動 anchor、不動 supersede。
"""
from __future__ import annotations

import json
import pathlib
import sys
import uuid

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from soul_elevation import ElevationNode, EvidenceEdge  # noqa: E402

from src.inner_life.elevation_adapter import (  # noqa: E402
    EDGES_FILENAME,
    NODES_FILENAME,
    _persist_result,
)

_TS = "2026-10-11T00:00:00+00:00"


def _nid() -> str:
    return uuid.uuid4().hex


def _node(agent: str, node_type: str, content: str) -> ElevationNode:
    nid = _nid()
    return ElevationNode(
        node_id=nid,
        node_type=node_type,  # type: ignore[arg-type]
        content=content,
        confidence=0.5,
        stability=0.1,
        valence="neutral",
        agent_id=agent,
        parent_node_id=None,
        lineage_depth=0,
        lineage_path=nid,
        created_ts=_TS,
        provenance_ref=None,
    )


def _edge(node_id: str, agent: str) -> EvidenceEdge:
    eid = _nid()
    return EvidenceEdge(
        edge_id=eid,
        node_id=node_id,
        source_type="inner_life_event",
        source_id=f"src-{eid[:8]}",
        agent_id=agent,
        weight=1.0,
        valid_from_ts=_TS,
        valid_until_ts=None,
        inner_life_event_id=f"evt-{eid[:8]}",
        trigger_type="diary:night",
    )


def _persist(nodes, edges, store_dir) -> None:
    assert _persist_result(store_dir, nodes, edges) is None


def _lines(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_same_content_second_round_skipped(tmp_path):
    n1 = _node("agent_ruka", "pattern", "秋天的第一封信")
    e1 = _edge(n1.node_id, "agent_ruka")
    _persist([n1], [e1], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 1

    # 同內容（新 node_id、新 edge_id；含首尾空白變體，strip 口徑仍命中）
    n2 = _node("agent_ruka", "pattern", "  秋天的第一封信  ")
    e2 = _edge(n2.node_id, "agent_ruka")
    _persist([n2], [e2], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 1


def test_partial_skip_only_dup_node_and_its_edges(tmp_path):
    n_dup = _node("agent_ruka", "pattern", "重複內容")
    _persist([n_dup], [_edge(n_dup.node_id, "agent_ruka")], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1

    n_dup2 = _node("agent_ruka", "pattern", "重複內容")
    n_new = _node("agent_ruka", "pattern", "全新內容")
    e_dup2 = _edge(n_dup2.node_id, "agent_ruka")
    e_new = _edge(n_new.node_id, "agent_ruka")
    _persist([n_dup2, n_new], [e_dup2, e_new], tmp_path)

    node_lines = _lines(tmp_path / NODES_FILENAME)
    edge_lines = _lines(tmp_path / EDGES_FILENAME)
    assert len(node_lines) == 2
    assert len(edge_lines) == 2
    contents = [json.loads(ln)["content"] for ln in node_lines]
    assert contents.count("重複內容") == 1
    assert "全新內容" in contents
    kept_node_ids = {json.loads(ln)["node_id"] for ln in node_lines}
    for ln in edge_lines:
        assert json.loads(ln)["node_id"] in kept_node_ids


def test_different_agent_or_type_still_appends(tmp_path):
    n1 = _node("agent_ruka", "pattern", "同文不同歸屬")
    _persist([n1], [_edge(n1.node_id, "agent_ruka")], tmp_path)

    # 同 content＋同 type 但不同 agent → 照寫
    n2 = _node("agent_mika", "pattern", "同文不同歸屬")
    _persist([n2], [_edge(n2.node_id, "agent_mika")], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 2
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 2

    # 同 agent＋同 content 但不同 node_type → 照寫
    n3 = _node("agent_ruka", "belief", "同文不同歸屬")
    _persist([n3], [_edge(n3.node_id, "agent_ruka")], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 3
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 3


def test_fail_open_missing_file_writes(tmp_path):
    assert not (tmp_path / NODES_FILENAME).exists()
    n1 = _node("agent_ruka", "pattern", "缺檔照寫")
    _persist([n1], [_edge(n1.node_id, "agent_ruka")], tmp_path)
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 1


def test_fail_open_corrupt_file_writes(tmp_path):
    (tmp_path / NODES_FILENAME).write_bytes(b"\x93\xfa\xff not-json {{{\n\x00\x01\n")
    n1 = _node("agent_ruka", "pattern", "損壞照寫")
    _persist([n1], [_edge(n1.node_id, "agent_ruka")], tmp_path)
    raw = (tmp_path / NODES_FILENAME).read_text(encoding="utf-8", errors="replace")
    assert "損壞照寫" in raw
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 1
