"""
tests/test_elevation_recall.py — DORMANT / SUPERSEDED 隻讀回憶查詢（C4-IMPL）

驗收（照 docs/ELEVATION-RECALL-CONTRACT.md §5，契約逐字即規格）:
  T1. DORMANT 可查：tmp store 寫 1 ACTIVE ＋ 1 DORMANT（含 query 子字串）；
      state_filter={"dormant"} → 只回 DORMANT，帶 lifecycle_state="dormant"＋lineage_path。
  T2. ACTIVE 優先（邊界不漏）：同上 store；state_filter 含 "active" →
      回 []（fail-closed；ACTIVE 是投影轄區，不經回憶）。
  T3. SUPERSEDED 隻讀：A（superseded，superseded_by=B）＋ B（active，
      parent_node_id=A）；查 A 的 query → 回 A（帶 superseded_by=B＋lineage_path）；
      複查 store：A 仍 superseded、B 無新增（0 寫入、0 遷移）。
  T4. essence 不動：essence DORMANT 合成節點 1 筆；查詢＋全 store 快照前後比對 →
      可查得；store 逐位元組一致（0 寫入）；reconsideration_candidate 未被觸碰。

合成 JSONL 全寫 tmp store_dir，不碰生產 data/elevation/。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inner_life.elevation_recall import (
    DORMANT_TONE_PREFIX,
    RECALL_HIT_KEYS,
    format_recall_hit,
    query_recall,
)

ALICE = "agent_alice"
DEFAULT = "default"

HIT_KEYS = {"node_id", "node_type", "content", "lifecycle_state", "lineage_path", "superseded_by"}


def _make_node(node_id, node_type, content, agent_id=ALICE,
               created_ts="2026-08-29T00:00:00.000000+00:00",
               lifecycle_state=None, parent_node_id=None,
               superseded_by=None, reconsideration_candidate=None):
    """構造一條 elevation node dict（欄位對齊 elevation_nodes.jsonl 記錄）。"""
    node = {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "agent_id": agent_id,
        "parent_node_id": parent_node_id,
        "lineage_depth": 0,
        "lineage_path": node_id,
        "created_ts": created_ts,
        "provenance_ref": None,
    }
    if lifecycle_state is not None:
        node["lifecycle_state"] = lifecycle_state
    if superseded_by is not None:
        node["superseded_by"] = superseded_by
    if reconsideration_candidate is not None:
        node["reconsideration_candidate"] = reconsideration_candidate
    return node


def _write_nodes(store_dir: Path, nodes):
    """把節點列表寫進 store_dir/elevation_nodes.jsonl（測試用，模擬 append-only store）。"""
    path = store_dir / "elevation_nodes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for n in nodes:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")


def _rmtree(path: Path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _snapshot(store_dir: Path):
    """全 store 快照：檔名 → 位元組（斷言 0 寫入用）。"""
    snap = {}
    for p in sorted(store_dir.iterdir()):
        if p.is_file():
            snap[p.name] = p.read_bytes()
    return snap


class TestElevationRecallContract(unittest.TestCase):
    """契約 §5 驗收 T1–T4"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="elevation_recall_test_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_T1_dormant_recallable(self):
        """T1: DORMANT 可查（只回 DORMANT，帶狀態標＋lineage_path）"""
        _write_nodes(self.tmp, [
            _make_node("n-active", "belief", "深夜加班讓人心累。"),
            _make_node("n-dormant", "belief", "深夜加班讓人心累，但那時很在意升遷。",
                       lifecycle_state="dormant"),
        ])
        hits = query_recall(ALICE, "心累", {"dormant"}, store_dir=self.tmp)
        self.assertEqual([h["node_id"] for h in hits], ["n-dormant"])
        hit = hits[0]
        self.assertEqual(set(hit.keys()), HIT_KEYS)
        self.assertEqual(hit["lifecycle_state"], "dormant")
        self.assertEqual(hit["lineage_path"], "n-dormant")
        self.assertEqual(hit["node_type"], "belief")
        # RecallHit 內容原文照錄，不加語氣前綴（語氣隔離在呈現層）
        self.assertEqual(hit["content"], "深夜加班讓人心累，但那時很在意升遷。")
        self.assertFalse(hit["content"].startswith(DORMANT_TONE_PREFIX))

    def test_T2_active_filter_fail_closed(self):
        """T2: state_filter 含 active → 回 []（fail-closed；ACTIVE 是投影轄區）"""
        _write_nodes(self.tmp, [
            _make_node("n-active", "belief", "深夜加班讓人心累。"),
            _make_node("n-dormant", "belief", "深夜加班讓人心累，但那時很在意升遷。",
                       lifecycle_state="dormant"),
        ])
        self.assertEqual(query_recall(ALICE, "心累", {"active"}, store_dir=self.tmp), [])
        self.assertEqual(
            query_recall(ALICE, "心累", {"dormant", "active"}, store_dir=self.tmp), [])
        self.assertEqual(
            query_recall(ALICE, "心累", {"weakening"}, store_dir=self.tmp), [])
        self.assertEqual(
            query_recall(ALICE, "心累", {"bogus"}, store_dir=self.tmp), [])

    def test_T3_superseded_readonly(self):
        """T3: SUPERSEDED 隻讀（帶 superseded_by＋lineage_path；0 寫入、0 遷移）"""
        _write_nodes(self.tmp, [
            _make_node("n-A", "belief", "成功就是升遷加薪。",
                       lifecycle_state="superseded", superseded_by="n-B"),
            _make_node("n-B", "belief", "成功是睡得好。",
                       parent_node_id="n-A"),
        ])
        before = _snapshot(self.tmp)
        hits = query_recall(ALICE, "升遷加薪", {"superseded"}, store_dir=self.tmp)
        self.assertEqual([h["node_id"] for h in hits], ["n-A"])
        hit = hits[0]
        self.assertEqual(hit["lifecycle_state"], "superseded")
        self.assertEqual(hit["superseded_by"], "n-B")
        self.assertEqual(hit["lineage_path"], "n-A")
        # 複查 store：A 仍 superseded、B 無新增（0 寫入、0 遷移）
        self.assertEqual(_snapshot(self.tmp), before)
        rows = [json.loads(l) for l in
                (self.tmp / "elevation_nodes.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        by_id = {r["node_id"]: r for r in rows}
        self.assertEqual(by_id["n-A"]["lifecycle_state"], "superseded")
        self.assertEqual(by_id["n-B"].get("lifecycle_state", "active"), "active")
        self.assertFalse((self.tmp / "elevation_projection_trace.jsonl").exists())

    def test_T4_essence_untouched(self):
        """T4: essence DORMANT 可查；store 逐位元組一致；reconsideration 未觸碰"""
        _write_nodes(self.tmp, [
            _make_node("n-ess", "essence", "我的核心是深夜裡的好奇心。",
                       lifecycle_state="dormant", reconsideration_candidate=False),
        ])
        before = _snapshot(self.tmp)
        hits = query_recall(ALICE, "好奇心", {"dormant"}, store_dir=self.tmp)
        self.assertEqual([h["node_id"] for h in hits], ["n-ess"])
        self.assertEqual(hits[0]["node_type"], "essence")
        self.assertEqual(_snapshot(self.tmp), before, "0 寫入：store 逐位元組一致")
        rows = [json.loads(l) for l in
                (self.tmp / "elevation_nodes.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()]
        self.assertFalse(rows[0]["reconsideration_candidate"])


class TestElevationRecallBoundaries(unittest.TestCase):
    """契約 §2 範圍邊界（缺省態／default 永不回憶／排序／fail-silent／語氣層）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="elevation_recall_edge_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_default_filter_covers_both_states(self):
        """state_filter 缺省＝兩者（dormant＋superseded 同回）"""
        _write_nodes(self.tmp, [
            _make_node("n-d", "belief", "共同關鍵字舊信念。", lifecycle_state="dormant"),
            _make_node("n-s", "value", "共同關鍵字被取代的價值。",
                       lifecycle_state="superseded", superseded_by="n-n"),
            _make_node("n-n", "value", "共同關鍵字新價值。"),
        ])
        hits = query_recall(ALICE, "共同關鍵字", store_dir=self.tmp)
        self.assertEqual(
            [(h["node_id"], h["lifecycle_state"]) for h in hits],
            [("n-d", "dormant"), ("n-s", "superseded")])

    def test_default_agent_never_recalls(self):
        """agent_id=='default' 永不回憶；world node 永不回憶"""
        _write_nodes(self.tmp, [
            _make_node("n-w", "belief", "共同關鍵字世界信念。",
                       agent_id=DEFAULT, lifecycle_state="dormant"),
            _make_node("n-d", "belief", "共同關鍵字舊信念。", lifecycle_state="dormant"),
        ])
        self.assertEqual(query_recall(DEFAULT, "共同關鍵字", store_dir=self.tmp), [])
        hits = query_recall(ALICE, "共同關鍵字", store_dir=self.tmp)
        self.assertEqual([h["node_id"] for h in hits], ["n-d"])

    def test_empty_query_and_bad_params_fail_silent(self):
        """空 query／空 agent／壞參數 → []，不 raise"""
        _write_nodes(self.tmp, [
            _make_node("n-d", "belief", "什麼都查得到的舊信念。", lifecycle_state="dormant"),
        ])
        self.assertEqual(query_recall(ALICE, "", {"dormant"}, store_dir=self.tmp), [])
        self.assertEqual(query_recall("", "舊信念", {"dormant"}, store_dir=self.tmp), [])
        self.assertEqual(query_recall(ALICE, "舊信念", set(), store_dir=self.tmp), [])
        self.assertEqual(query_recall(ALICE, "舊信念", "dormant", store_dir=self.tmp), [])
        self.assertEqual(
            query_recall(ALICE, "舊信念", {"dormant"}, store_dir=self.tmp / "nope"), [])

    def test_bad_lines_skipped_and_pattern_excluded(self):
        """壞行跳過；pattern 不回憶；他靈魂不回憶；排序 deterministic"""
        path = self.tmp / "elevation_nodes.jsonl"
        path.write_text(
            "{bad json line}\n"
            + json.dumps(_make_node("n-late", "belief", "排序關鍵字晚。",
                                    created_ts="2026-08-29T10:00:00+00:00",
                                    lifecycle_state="dormant")) + "\n"
            + json.dumps(_make_node("n-early", "belief", "排序關鍵字早。",
                                    created_ts="2026-08-29T01:00:00+00:00",
                                    lifecycle_state="dormant")) + "\n"
            + json.dumps(_make_node("n-pat", "pattern", "排序關鍵字模式。",
                                    lifecycle_state="dormant")) + "\n"
            + json.dumps(_make_node("n-bob", "belief", "排序關鍵字Bob。",
                                    agent_id="agent_bob",
                                    lifecycle_state="dormant")) + "\n",
            encoding="utf-8",
        )
        hits = query_recall(ALICE, "排序關鍵字", store_dir=self.tmp)
        self.assertEqual([h["node_id"] for h in hits], ["n-early", "n-late"])
        for h in hits:
            self.assertEqual(set(h.keys()), HIT_KEYS)

    def test_presentation_tone_layer(self):
        """呈現語氣：DORMANT 前綴／SUPERSEDED 降維句式（呈現層做，Hit 原文不動）"""
        dormant_hit = {"node_id": "d", "node_type": "belief", "content": "舊信念。",
                       "lifecycle_state": "dormant", "lineage_path": "d",
                       "superseded_by": None}
        rendered = format_recall_hit(dormant_hit)
        self.assertTrue(rendered.startswith(DORMANT_TONE_PREFIX))
        self.assertIn("舊信念。", rendered)
        sup_hit = {"node_id": "a", "node_type": "belief", "content": "A。",
                   "lifecycle_state": "superseded", "lineage_path": "a",
                   "superseded_by": "b"}
        self.assertEqual(format_recall_hit(sup_hit, "B。"), "我以前覺得「A。」，但後來發現「B。」。")

    def test_contract_key_registry(self):
        """RecallHit 鍵集合即契約 §2.2 六鍵"""
        self.assertEqual(
            set(RECALL_HIT_KEYS),
            {"node_id", "node_type", "content", "lifecycle_state",
             "lineage_path", "superseded_by"})


if __name__ == "__main__":
    unittest.main()
