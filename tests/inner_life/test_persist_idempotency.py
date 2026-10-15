"""VALUE-DEDUP-A-1: _persist_result 呼叫點冪等（全合成 tmp store_dir）。

口徑：normalize = strip only（見 elevation_adapter._dedup_content_hash；
repo 內無既有全半形正規化工具，按工單 fallback）。

🔴 SOUL-BUG-001 修正（本檔兩支測試的邊斷言已於 Bry 2026-10-14 授權翻轉）：
`fb7ab63` 的節點冪等保證**完整保留**，但舊版在命中重複 key 時會把該節點
**連同其證據邊一併丟棄**。現已改為「節點合併 + 邊重綁 + 邊去重」。因此必須把
兩種語意分清楚，本檔就是那條界線：

- **同事件重跑（真冪等）**：同一個 ``(source_id, inner_life_event_id)`` 重複執行
  → 邊去重鍵相同 → 收斂。邊數**不增**。
  （由 ``test_elevate_mechanism.py::test_h1_same_event_twice_no_elevate``
  與本檔 ``test_same_event_reingest_edges_still_deduped`` 守住。）
- **跨事件同內容（證據累積）**：不同 ``event_id`` 產出相同 content → 節點合併成
  1 顆，但第 2 份獨立證據**必須**掛到同一顆 canonical 節點上 → 邊數**增加**。
  這正是打通 ``min_evidence=2`` 昇華閘門的機制。

下列兩支測試原本把「連邊一起丟」寫成預期結果，那是缺陷的副作用，不是 VALUE-DEDUP-A-1
的目的（其目的是「節點不膨脹」）。現已翻轉為正確語意。

- 同 agent + 同 node_type + 同 content（strip 後 sha256）第二輪：節點不增，
  但**新事件帶來新邊**（合併到既有 canonical 節點）。
- 不同 content / 不同 agent / 不同 node_type 正常落盤。
- 現有檔損壞 / 缺失時 fail-open 照寫（絕不丟合法產出）。
- 不動 engine、不動 consumed 去重、不動 anchor、不動 supersede。
"""
from __future__ import annotations

import dataclasses
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

    # 同內容（新 node_id、新 edge_id、**新 event_id**；含首尾空白變體，strip 口徑仍命中）
    n2 = _node("agent_ruka", "pattern", "  秋天的第一封信  ")
    e2 = _edge(n2.node_id, "agent_ruka")
    _persist([n2], [e2], tmp_path)
    # 節點冪等不變（VALUE-DEDUP-A-1 的核心保證）：仍是 1 顆。
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1
    # 邊從 1 → 2：第二輪帶來的是**另一個事件**（_edge 每次產生全新
    # source_id / inner_life_event_id），它是獨立的第 2 份證據，必須掛到既有
    # canonical 節點上。SOUL-BUG-001 修正前這裡是 1 —— 因為邊被連帶丟棄，
    # 導致跨事件證據永遠累積不到 min_evidence=2。
    edge_lines = _lines(tmp_path / EDGES_FILENAME)
    assert len(edge_lines) == 2
    # 兩條邊都必須掛在**同一顆**已落盤節點上（邊重綁，不是新長一顆節點）。
    kept_ids = {json.loads(ln)["node_id"] for ln in _lines(tmp_path / NODES_FILENAME)}
    assert {json.loads(ln)["node_id"] for ln in edge_lines} == kept_ids
    # 且兩份證據的 (source_id, inner_life_event_id) 必須相異 —— 否則就是重複邊。
    identities = {
        (json.loads(ln)["source_id"], json.loads(ln)["inner_life_event_id"])
        for ln in edge_lines
    }
    assert len(identities) == 2


def test_duplicate_node_coalesced_edges_rebound(tmp_path):
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
    # 節點：重複的不長、全新的照長 → 2 顆。
    assert len(node_lines) == 2
    contents = [json.loads(ln)["content"] for ln in node_lines]
    assert contents.count("重複內容") == 1
    assert "全新內容" in contents
    # 邊：3 條（舊有 1 + 重綁回來 1 + 新節點 1）。修正前是 2 條 —— 被跳過節點的
    # 邊連帶被丟，「重複內容」永遠只有 1 份證據。
    assert len(edge_lines) == 3
    kept_node_ids = {json.loads(ln)["node_id"] for ln in node_lines}
    for ln in edge_lines:
        assert json.loads(ln)["node_id"] in kept_node_ids
    # 「重複內容」這顆 canonical 節點底下必須有 2 份獨立證據（這是閘門打通的形狀）。
    dup_id = next(
        json.loads(ln)["node_id"]
        for ln in node_lines
        if json.loads(ln)["content"] == "重複內容"
    )
    dup_edges = [json.loads(ln) for ln in edge_lines if json.loads(ln)["node_id"] == dup_id]
    assert len(dup_edges) == 2
    assert len({(e["source_id"], e["inner_life_event_id"]) for e in dup_edges}) == 2


def test_same_event_reingest_edges_still_deduped(tmp_path):
    """🔴 真冪等的正面守衛：同一事件重複執行，邊**不得**膨脹。

    這是 SOUL-BUG-001 修復最關鍵的不變量 —— 邊去重鍵
    ``(node_id, source_id, inner_life_event_id)`` 必須與引擎消費鍵同構，
    否則「跨事件累積」的代價會是「同事件重複也會累積」⇒ 憑空造出假證據。
    """
    n1 = _node("agent_ruka", "pattern", "同一件事")
    e1 = _edge(n1.node_id, "agent_ruka")
    _persist([n1], [e1], tmp_path)

    # 同一個事件再來一次（新 node_id 假設不同，但 event 身份相同）
    n2 = _node("agent_ruka", "pattern", "同一件事")
    e2 = _edge(n2.node_id, "agent_ruka")
    # EvidenceEdge 是 frozen dataclass → 用 replace 改寫事件身份
    e2 = dataclasses.replace(
        e2, inner_life_event_id=e1.inner_life_event_id, source_id=e1.source_id
    )
    _persist([n2], [e2], tmp_path)

    assert len(_lines(tmp_path / NODES_FILENAME)) == 1
    assert len(_lines(tmp_path / EDGES_FILENAME)) == 1


def test_same_source_different_event_both_edges_kept(tmp_path):
    """🔴 邊去重鍵必須與引擎消費鍵 ``(source_id, inner_life_event_id)`` **完全同構**。

    同 ``source_id`` 但**不同** ``inner_life_event_id`` ⇒ SE-1 視為**兩份獨立證據**
    （``evidence_key = (source_id, event_identity)``），因此兩條邊都必須落盤。

    這支測試是 MUT-3 變異實驗逼出來的：把 ``_edge_identity`` 的
    ``inner_life_event_id`` 拿掉後，全套測試**不會轉紅** —— 因為先前沒有任何測試
    構造出「同 source、不同 event 身分」的邊。漏掉該欄位會讓落盤去重**比引擎更窄**，
    引擎會算 2 的獨立證據被落盤層壓成 1 ⇒ 靜默證據損失。

    守的是**不變量**（鍵與引擎同構），不是實作形狀。
    """
    n1 = _node("agent_ruka", "pattern", "同一個來源")
    e1 = _edge(n1.node_id, "agent_ruka")
    e2 = _edge(n1.node_id, "agent_ruka")
    # 只改事件身分，source_id 保持相同
    e2 = dataclasses.replace(e2, source_id=e1.source_id)
    assert e1.inner_life_event_id != e2.inner_life_event_id, "前提：兩條事件身分應相異"
    assert e1.source_id == e2.source_id, "前提：兩條 source 應相同"

    _persist([n1], [e1, e2], tmp_path)

    edge_lines = _lines(tmp_path / EDGES_FILENAME)
    assert len(edge_lines) == 2, (
        f"同 source 不同 event 應是 2 份獨立證據，兩條邊都要落盤；實得 {len(edge_lines)}。"
        f"若為 1，代表邊去重鍵漏了 inner_life_event_id（比引擎窄）→ 靜默證據損失。"
    )


def test_unrebindable_canonical_drops_edges_fail_safe(tmp_path):
    """canonical 不可知（磁碟有缺 node_id 的壞列）→ 退回丟邊，絕不產生懸空邊。

    fail-safe 方向必須是**保守**的：寧可丟證據（回到 fb7ab63 行為），
    也不能把邊指向一個不存在的節點。
    """
    # 先手寫一列缺 node_id 的壞節點（內容與後來的節點相同）
    bad = {"agent_id": "agent_ruka", "node_type": "pattern", "content": "壞列內容"}
    (tmp_path / NODES_FILENAME).write_text(
        json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    n_new = _node("agent_ruka", "pattern", "壞列內容")
    e_new = _edge(n_new.node_id, "agent_ruka")
    _persist([n_new], [e_new], tmp_path)

    # 壞列沒有可用 canonical → 節點不寫入，邊也不寫入（維持舊行為）
    assert len(_lines(tmp_path / NODES_FILENAME)) == 1  # 只有那列壞的
    assert _lines(tmp_path / EDGES_FILENAME) == []
    # 沒有任何邊指向不存在的 node_id
    node_ids = {
        json.loads(ln).get("node_id")
        for ln in _lines(tmp_path / NODES_FILENAME)
        if ln.strip()
    }
    for ln in _lines(tmp_path / EDGES_FILENAME):
        assert json.loads(ln)["node_id"] in node_ids


def test_canonical_choice_is_deterministic(tmp_path):
    """同 key 多列時，canonical 必為**檔案順序第一列**（確定性、可重現）。"""
    rows = [
        {"node_id": "node-first", "agent_id": "agent_ruka", "node_type": "pattern",
         "content": "多列內容"},
        {"node_id": "node-second", "agent_id": "agent_ruka", "node_type": "pattern",
         "content": "多列內容"},
    ]
    (tmp_path / NODES_FILENAME).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )

    n_new = _node("agent_ruka", "pattern", "多列內容")
    e_new = _edge(n_new.node_id, "agent_ruka")
    _persist([n_new], [e_new], tmp_path)

    edge_lines = _lines(tmp_path / EDGES_FILENAME)
    assert len(edge_lines) == 1
    # 新證據邊必須綁到第一列，不是第二列、也不是新節點。
    assert json.loads(edge_lines[0])["node_id"] == "node-first"
    # 節點數不增（VALUE-DEDUP-A-1 保留）
    assert len(_lines(tmp_path / NODES_FILENAME)) == 2


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
