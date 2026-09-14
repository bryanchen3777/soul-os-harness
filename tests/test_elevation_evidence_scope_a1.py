"""ELEVATION-FIX-A-1 — 消化範圍局部化（契約 §3 方案 A／OQ-1 裁定）行為測試。

新範圍語意：``consumed_keys`` 由「agent 全域鍵集合」改為
「**（候選維度, agent）** 局部」——
  * **同一候選維度 + 同一 agent** 內，同一 ``(source_id, event_identity)``
    仍只計一次（INV-2 反重複消化**不變**）；
  * **不同候選維度之間不再互相餓死**（跨維度不吃證據）；
  * 不同 agent 之間本來就隔離（本檔一併釘住，防回歸）。

測試分節：
  A. INV-2：同候選+同 agent 的同一證據只計一次（含重複 ingest 的同鍵 pattern）
  B. 跨候選維度不再互相餓死（舊語意下會被吃掉的鍵，新語意下仍可計票）
  C. 跨 agent 不再互相餓死（agent 隔離不變量）
  D. 維度不可判定時的保守回退（絕不放寬去重）
  E. 真實 API 路徑：反覆升華需要「同維度 2 份**新**證據」
  F. 可觀測性：每維度已消化／已載入計數的 debug 記錄
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from soul_elevation import StubElevationLLM

from src.inner_life import (
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_DIARY_NIGHT,
)
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    _node_candidate_dimension,
    _rebuild_engine_for,
    elevate_matured_patterns,
    run_elevation,
)
from src.inner_life.emergent_projection import load_elevation_nodes

_SOUL_TYPES = {"belief", "value", "trait", "essence"}
_TS = "2026-09-14T00:00:00+00:00"


# ── 手工 store 建構器（精確控制「鍵 × 維度 × agent」的交叉）─────────────


def _node(
    node_id: str,
    node_type: str,
    agent_id: str,
    *,
    candidate: str | None = None,
    content: str = "stub:content",
) -> dict:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "agent_id": agent_id,
        "parent_node_id": None,
        "lineage_depth": 0,
        "lineage_path": node_id,
        "created_ts": _TS,
        "provenance_ref": None,
        "candidate_node_type": candidate,
    }


def _edge(
    edge_id: str,
    node_id: str,
    source_id: str,
    agent_id: str,
    *,
    event_id: str | None = None,
    trigger_type: str = "diary:night",
) -> dict:
    return {
        "edge_id": edge_id,
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": source_id,
        "agent_id": agent_id,
        "weight": 1.0,
        "valid_from_ts": _TS,
        "valid_until_ts": None,
        "inner_life_event_id": source_id if event_id is None else event_id,
        "trigger_type": trigger_type,
    }


def _write_store(store_dir: Path, nodes: list[dict], edges: list[dict]) -> Path:
    store_dir.mkdir(parents=True, exist_ok=True)
    with open(store_dir / NODES_FILENAME, "w", encoding="utf-8") as fh:
        for n in nodes:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")
    with open(store_dir / EDGES_FILENAME, "w", encoding="utf-8") as fh:
        for e in edges:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return store_dir


def _souls(store_dir: Path, agent_id: str | None = None) -> list[dict]:
    out = [n for n in load_elevation_nodes(store_dir) if n["node_type"] in _SOUL_TYPES]
    if agent_id is not None:
        out = [n for n in out if n["agent_id"] == agent_id]
    return out


def _loaded_keys(store_dir: Path, nodes, edges, agent_id: str) -> set:
    engine = _rebuild_engine_for(
        store_dir, nodes, edges, agent_id=agent_id, llm=StubElevationLLM()
    )
    return {(e.source_id, e.inner_life_event_id) for e in engine._edges}


# ── A. INV-2：同候選 + 同 agent 的同一證據只計一次 ─────────────────────


def test_a1_same_key_in_same_dimension_is_not_recounted(tmp_path):
    """value 維度：已消化鍵 K1 不得再計票——即使有第二顆同鍵 pattern（重複 ingest）。

    p1(K1) 是新 pattern、p2 是同一事件的第二顆 pattern（同鍵 K1）；
    soul s1 已引用 K1。→ 載入後只剩 p3(K2) → 獨立證據 1 < 2 → **不升華**。
    """
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("p2", "pattern", "agent_rem", candidate="value"),
        _node("p3", "pattern", "agent_rem", candidate="value"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-p2", "p2", "K1", "agent_rem"),  # 同一事件重複 ingest → 同鍵
        _edge("e-p3", "p3", "K2", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),  # 已消化
    ]
    _write_store(store, nodes, edges)

    assert _loaded_keys(store, nodes, edges, "agent_rem") == {("K2", "K2")}
    assert elevate_matured_patterns(store_dir=store) == []
    assert len(_souls(store, "agent_rem")) == 1  # 只有既有的 s1，未新增


def test_a2_same_dimension_two_fresh_keys_still_accumulate(tmp_path):
    """INV-2 不是「全面封鎖」：同維度**兩份新證據** → 仍可升華（N=2）。"""
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("p2", "pattern", "agent_rem", candidate="value"),
        _node("p3", "pattern", "agent_rem", candidate="value"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-p2", "p2", "K2", "agent_rem"),
        _edge("e-p3", "p3", "K3", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),  # K1 已消化
    ]
    _write_store(store, nodes, edges)

    elevated = elevate_matured_patterns(store_dir=store)
    assert len(elevated) == 1
    assert elevated[0].node_type == "value"
    assert elevated[0].agent_id == "agent_rem"
    # 新靈魂結構的證據邊回指 K2/K3（不含已消化的 K1）
    new_keys = {
        e["source_id"]
        for e in (
            json.loads(line)
            for line in (store / EDGES_FILENAME).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if e["node_id"] == elevated[0].node_id
    }
    assert new_keys == {"K2", "K3"}


# ── B. 跨候選維度不再互相餓死 ────────────────────────────────────────


def test_b1_consumed_key_in_one_dimension_does_not_starve_another(tmp_path):
    """value 消化 K1 → **不得**吃掉 trait 維度上同鍵的 pattern 證據。

    trait 維度：q1(K1)、q2(K2) 兩份獨立證據 → 新語意下 N=2 → trait 升華成功。
    （舊語意：K1 為 agent 全域已消化鍵 → q1 被剔除 → N=1 → 永遠卡住。）
    value 維度只有 p1(K1) 且已被 s1 消化 → N=0 → 不升（不重複消化）。
    """
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("q1", "pattern", "agent_rem", candidate="trait"),
        _node("q2", "pattern", "agent_rem", candidate="trait"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-q1", "q1", "K1", "agent_rem"),  # 跨維度共用同一事件
        _edge("e-q2", "q2", "K2", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),  # value 維度已消化 K1
    ]
    _write_store(store, nodes, edges)

    elevated = elevate_matured_patterns(store_dir=store)
    assert len(elevated) == 1
    assert elevated[0].node_type == "trait"
    assert elevated[0].agent_id == "agent_rem"
    # value 維度未因跨維度放寬而被重複消化
    assert [n["node_type"] for n in _souls(store, "agent_rem")] == ["value", "trait"]


def test_b2_old_semantics_would_have_starved_it(tmp_path):
    """同一 store 在新語意下 trait 的已載入證據 = 2（舊語意為 1）。

    這是對「方案 A 確實改變了載入結果」的直接斷言（不依賴 elevate 結果）。
    """
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("q1", "pattern", "agent_rem", candidate="trait"),
        _node("q2", "pattern", "agent_rem", candidate="trait"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-q1", "q1", "K1", "agent_rem"),
        _edge("e-q2", "q2", "K2", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),
    ]
    _write_store(store, nodes, edges)
    loaded = _loaded_keys(store, nodes, edges, "agent_rem")
    assert ("K1", "K1") in loaded  # trait 維度仍可見（舊語意會剔除）

    # 舊語意（agent 全域 consumed_keys）下應為 {("K2","K2")} —— 對照組
    soul_ids = {n["node_id"] for n in nodes if n["node_type"] in _SOUL_TYPES}
    old_consumed = {
        (e["source_id"], e["inner_life_event_id"])
        for e in edges
        if e["node_id"] in soul_ids and e["agent_id"] == "agent_rem"
    }
    old_loaded = {
        (e["source_id"], e["inner_life_event_id"])
        for e in edges
        if e["agent_id"] == "agent_rem"
        and e["node_id"] not in soul_ids
        and (e["source_id"], e["inner_life_event_id"]) not in old_consumed
    }
    assert old_loaded == {("K2", "K2")}
    assert loaded != old_loaded  # 新舊語意確實不同（此 store 為跨維度共用鍵）


# ── C. 跨 agent 不再互相餓死 ─────────────────────────────────────────


def test_c1_other_agents_consumption_does_not_starve_this_agent(tmp_path):
    """agent_rem 消化 K1 → agent_yua 的同鍵 pattern 仍可計票（agent 隔離）。"""
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("s1", "value", "agent_rem"),
        _node("y1", "pattern", "agent_yua", candidate="value"),
        _node("y2", "pattern", "agent_yua", candidate="value"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),
        _edge("e-y1", "y1", "K1", "agent_yua"),
        _edge("e-y2", "y2", "K2", "agent_yua"),
    ]
    _write_store(store, nodes, edges)

    elevated = elevate_matured_patterns(store_dir=store)
    assert len(elevated) == 1
    assert elevated[0].agent_id == "agent_yua"  # 只有 yua 達標
    assert elevated[0].node_type == "value"
    # agent_rem 未被升華（其唯一可見證據 K1 已被自己消化）
    assert [s["agent_id"] for s in _souls(store)] == ["agent_rem", "agent_yua"]


# ── D. 維度不可判定 → 保守回退（絕不放寬去重）────────────────────────


def test_d1_unknown_pattern_node_falls_back_to_agent_global_dedup(tmp_path):
    """邊指向不存在的 pattern 節點（維度不可判定）→ 沿用 agent 全域已消化語意。

    這保證局部化**只可能縮小**消化範圍，不可能因為節點缺損而放寬去重。
    """
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K2", "agent_rem"),
        _edge("e-ghost", "ghost-node", "K1", "agent_rem"),  # 節點記錄缺失
        _edge("e-s1", "s1", "K1", "agent_rem"),
    ]
    _write_store(store, nodes, edges)

    loaded = _loaded_keys(store, nodes, edges, "agent_rem")
    assert ("K1", "K1") not in loaded  # 保守回退：仍視為已消化
    assert ("K2", "K2") in loaded


def test_d2_candidate_dimension_helper_fallbacks():
    """維度解析：pattern 取 candidate；soul 退回 node_type；皆缺 → 空字串（不猜）。"""
    assert _node_candidate_dimension({"node_type": "pattern", "candidate_node_type": "trait"}) == "trait"
    assert _node_candidate_dimension({"node_type": "value", "candidate_node_type": None}) == "value"
    assert _node_candidate_dimension({"node_type": "value"}) == "value"
    assert _node_candidate_dimension({}) == ""


# ── E. 真實 API 路徑：反覆升華需要「同維度 2 份新證據」────────────────


def test_e1_repeated_elevation_needs_two_fresh_keys_in_same_dimension(tmp_path):
    """真實 consume 路徑：升華→1 份新證據仍不足→再 1 份才再次升華。

    釘住兩件事：
      * 反重複消化（INV-2）在同維度內持續有效（同一批證據不會被再計一次）；
      * 同維度累積到 2 份**新**證據即可**反覆**升華（不是一次性）。
    """
    writer = InnerLifeWriter()
    store = tmp_path / "elevation"

    def _consume_one() -> None:
        event = writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
                actor_id="agent_rem",
                source_system="narrative",
            )
        )
        nodes = run_elevation(event, [], store_dir=store)
        assert len(nodes) == 1 and nodes[0].node_type == "pattern"

    _consume_one()
    _consume_one()
    first = elevate_matured_patterns(store_dir=store)
    assert len(first) == 1 and first[0].node_type == "value"

    # 第 3 份證據：同維度只累積到 1 份未消化 → 不升（INV-2 未被放寬）
    _consume_one()
    assert elevate_matured_patterns(store_dir=store) == []

    # 第 4 份證據：同維度累積到 2 份未消化 → 再次升華（可反覆）
    _consume_one()
    second = elevate_matured_patterns(store_dir=store)
    assert len(second) == 1 and second[0].node_type == "value"
    assert second[0].node_id != first[0].node_id

    types = [n["node_type"] for n in load_elevation_nodes(store)]
    assert types.count("value") == 2
    assert types.count("pattern") == 4


# ── F. 可觀測性 ─────────────────────────────────────────────────────


def test_f1_debug_log_reports_per_dimension_scope(tmp_path, caplog):
    """每維度已消化／已載入計數以 DEBUG 記錄（0 生產噪音，可審計）。"""
    store = tmp_path / "elevation"
    nodes = [
        _node("p1", "pattern", "agent_rem", candidate="value"),
        _node("q1", "pattern", "agent_rem", candidate="trait"),
        _node("s1", "value", "agent_rem"),
    ]
    edges = [
        _edge("e-p1", "p1", "K1", "agent_rem"),
        _edge("e-q1", "q1", "K1", "agent_rem"),
        _edge("e-s1", "s1", "K1", "agent_rem"),
    ]
    _write_store(store, nodes, edges)

    with caplog.at_level(logging.DEBUG, logger="soul_os.inner_life.elevation_adapter"):
        _rebuild_engine_for(
            store, nodes, edges, agent_id="agent_rem", llm=StubElevationLLM()
        )
    hits = [r.getMessage() for r in caplog.records if "evidence scope" in r.getMessage()]
    assert len(hits) == 1
    assert "consumed_by_dimension={'value': 1}" in hits[0]
    # trait 維度未消化任何鍵 → 已載入 1（q1），value 維度已消化 1 → 已載入 0
    assert "'trait': 1" in hits[0]
