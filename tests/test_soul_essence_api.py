"""
tests/test_soul_essence_api.py — 靈魂本質面板（工單 #2）後端 + 頭像驗收

验收（照工單）:
  1. GET /api/soul/essence/{agent_id} 回該靈魂的 belief/value/trait/essence
     （按 node_type 分組；agent_id="default" 的 world node 永不展示；
     pattern 不展示；缺 node_id/content 的行跳過）。
  2. projection sidecar 可觀測：最近一次投影的 projected_node_ids 能讀回。
  3. 失敗隔離：store 不存在 / 壞行 → 空結構，不 raise、不 404。
  4. 缺失頭像補齊：10 隻靈魂 + bryan + group 的頭像都存在且是合法 PNG。
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.io.gateway import _collect_soul_essence, _latest_projection_for


def _node(node_id, node_type, content, agent_id="agent_alice", created_ts="2026-08-29T00:00:00.000000+00:00"):
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
        "created_ts": created_ts,
        "provenance_ref": None,
    }


def _write_nodes(store_dir: Path, nodes) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    with open(store_dir / "elevation_nodes.jsonl", "w", encoding="utf-8") as fh:
        for n in nodes:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")


def _write_projection(store_dir: Path, records) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    with open(store_dir / "elevation_projection_trace.jsonl", "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestCollectSoulEssence(unittest.TestCase):
    """验收 1+2+3: 節點過濾 / 分組 / 投影 sidecar / 失敗隔離"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="soul_essence_test_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_01_filters_and_groups_by_node_type(self):
        """只取該靈魂自己的 belief/value/trait/essence，按 node_type 分組"""
        _write_nodes(self.tmp, [
            _node("n-belief", "belief", "我相信真誠對話會塑造人。", agent_id="agent_alice"),
            _node("n-value", "value", "我重視誠實。", agent_id="agent_alice"),
            _node("n-trait", "trait", "我傾向先傾聽再開口。", agent_id="agent_alice"),
            _node("n-essence", "essence", "我的核心是好奇心。", agent_id="agent_alice"),
            # 不展示: pattern / 其他靈魂 / default
            _node("n-pattern", "pattern", "用戶常在深夜提到工作壓力。", agent_id="agent_alice"),
            _node("n-bob", "belief", "Bob 的信念。", agent_id="agent_bob"),
            _node("n-default", "belief", "world node 的信念。", agent_id="default"),
            # 不展示: 缺 content
            _node("n-empty", "value", "   ", agent_id="agent_alice"),
            _node("n-nocontent", "value", None, agent_id="agent_alice"),
        ])
        result = _collect_soul_essence("agent_alice", store_dir=self.tmp)
        self.assertEqual(result["agent_id"], "agent_alice")
        essence = result["essence"]
        self.assertEqual(
            sorted(essence.keys()), ["belief", "essence", "trait", "value"]
        )
        self.assertEqual([n["node_id"] for n in essence["belief"]], ["n-belief"])
        self.assertEqual([n["node_id"] for n in essence["value"]], ["n-value"])
        self.assertEqual([n["node_id"] for n in essence["trait"]], ["n-trait"])
        self.assertEqual([n["node_id"] for n in essence["essence"]], ["n-essence"])
        # 不包含被排除的類型 / 其他 agent / pattern / default
        all_ids = [n["node_id"] for t in essence.values() for n in t]
        self.assertNotIn("n-pattern", all_ids)
        self.assertNotIn("n-bob", all_ids)
        self.assertNotIn("n-default", all_ids)
        self.assertNotIn("n-empty", all_ids)
        self.assertNotIn("n-nocontent", all_ids)

    def test_02_default_agent_never_shown(self):
        """agent_id="default" 無論 node_type 一律不展示（含查詢者自身是 default）"""
        _write_nodes(self.tmp, [
            _node("n-d1", "belief", "world belief", agent_id="default"),
            _node("n-d2", "value", "world value", agent_id="default"),
        ])
        result_default = _collect_soul_essence("default", store_dir=self.tmp)
        self.assertEqual(
            {t: len(v) for t, v in result_default["essence"].items()},
            {"belief": 0, "value": 0, "trait": 0, "essence": 0},
        )
        result_alice = _collect_soul_essence("agent_alice", store_dir=self.tmp)
        self.assertEqual(
            {t: len(v) for t, v in result_alice["essence"].items()},
            {"belief": 0, "value": 0, "trait": 0, "essence": 0},
        )

    def test_03_missing_store_fail_silent(self):
        """store 不存在 → 空結構 + projection=None，不 raise"""
        missing = self.tmp / "does_not_exist"
        result = _collect_soul_essence("agent_alice", store_dir=missing)
        self.assertEqual(
            {t: len(v) for t, v in result["essence"].items()},
            {"belief": 0, "value": 0, "trait": 0, "essence": 0},
        )
        self.assertIsNone(result["projection"])

    def test_04_bad_lines_skipped_other_rows_kept(self):
        """壞 JSON 行跳過，好行仍分組"""
        store = self.tmp / "elevation_nodes.jsonl"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(
            "{bad json line}\n"
            + json.dumps(_node("n-ok", "belief", "好的信念。", agent_id="agent_alice"))
            + "\n",
            encoding="utf-8",
        )
        result = _collect_soul_essence("agent_alice", store_dir=self.tmp)
        self.assertEqual([n["node_id"] for n in result["essence"]["belief"]], ["n-ok"])

    def test_05_projection_sidecar_latest_per_agent(self):
        """投影 sidecar：每個 agent 回最近一次（ts 最大 / 檔案最後一筆），
        含 projected_node_ids（projection sidecar 可觀測）"""
        _write_nodes(self.tmp, [
            _node("n-1", "belief", "第一顆信念。", agent_id="agent_alice"),
        ])
        _write_projection(self.tmp, [
            {
                "ts": "2026-08-29T01:00:00.000000+00:00",
                "event_type": "emergent_projected",
                "agent_id": "agent_alice",
                "projected_node_ids": ["n-old-1"],
                "projected_node_types": ["belief"],
                "node_count": 1,
            },
            {
                "ts": "2026-08-29T02:00:00.000000+00:00",
                "event_type": "emergent_projected",
                "agent_id": "agent_alice",
                "projected_node_ids": ["n-1", "n-later"],
                "projected_node_types": ["belief", "value"],
                "node_count": 2,
            },
            # 其他 agent 的投影不混入
            {
                "ts": "2026-08-29T03:00:00.000000+00:00",
                "event_type": "emergent_projected",
                "agent_id": "agent_bob",
                "projected_node_ids": ["n-bob-x"],
                "projected_node_types": ["belief"],
                "node_count": 1,
            },
        ])
        result = _collect_soul_essence("agent_alice", store_dir=self.tmp)
        proj = result["projection"]
        self.assertIsNotNone(proj)
        self.assertEqual(proj["ts"], "2026-08-29T02:00:00.000000+00:00")
        self.assertEqual(proj["projected_node_ids"], ["n-1", "n-later"])
        self.assertEqual(proj["node_count"], 2)

    def test_06_projection_sidecar_missing_file_none(self):
        """sidecar 檔案不存在 → projection=None"""
        _write_nodes(self.tmp, [_node("n-1", "belief", "信念。", agent_id="agent_alice")])
        result = _collect_soul_essence("agent_alice", store_dir=self.tmp)
        self.assertIsNone(result["projection"])

    def test_07_latest_projection_helper_direct(self):
        """_latest_projection_for：反向掃描第一筆 = 最近一次；無匹配 → None"""
        _write_projection(self.tmp, [
            {"ts": "t1", "event_type": "emergent_projected", "agent_id": "agent_alice",
             "projected_node_ids": ["a"], "node_count": 1},
            {"ts": "t2", "event_type": "emergent_projected", "agent_id": "agent_bob",
             "projected_node_ids": ["b"], "node_count": 1},
        ])
        self.assertEqual(_latest_projection_for(self.tmp, "agent_bob")["ts"], "t2")
        self.assertIsNone(_latest_projection_for(self.tmp, "agent_absent"))
        self.assertIsNone(_latest_projection_for(self.tmp / "nope", "agent_alice"))


class TestSoulEssenceEndpoint(unittest.TestCase):
    """验收 1: HTTP endpoint 形狀（monkeypatch 資料收集，避免依賴真實 data/）"""

    def test_endpoint_returns_grouped_shape(self):
        canned = {
            "agent_id": "agent_yua",
            "essence": {
                "belief": [{"node_id": "b1", "node_type": "belief", "content": "信念A", "confidence": 0.5, "created_ts": "t"}],
                "value": [],
                "trait": [],
                "essence": [],
            },
            "projection": {"ts": "t2", "projected_node_ids": ["b1"], "projected_node_types": ["belief"], "node_count": 1},
        }
        with patch("src.io.gateway._collect_soul_essence", return_value=canned) as mock_fn:
            from src.io.gateway import IOGateway
            from src.eventbus import SoulEventBus
            from fastapi.testclient import TestClient

            gw = IOGateway(SoulEventBus())
            client = TestClient(gw.app)
            resp = client.get("/api/soul/essence/agent_yua")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["agent_id"], "agent_yua")
            self.assertEqual(body["essence"]["belief"][0]["content"], "信念A")
            self.assertEqual(body["projection"]["projected_node_ids"], ["b1"])
            mock_fn.assert_called_once_with("agent_yua")

    def test_endpoint_unknown_agent_not_404(self):
        """未知 agent_id → 200 + 空結構（不 404）"""
        from src.io.gateway import IOGateway
        from src.eventbus import SoulEventBus
        from fastapi.testclient import TestClient

        gw = IOGateway(SoulEventBus())
        client = TestClient(gw.app)
        resp = client.get("/api/soul/essence/agent_ghost")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            {t: len(v) for t, v in body["essence"].items()},
            {"belief": 0, "value": 0, "trait": 0, "essence": 0},
        )
        self.assertIsNone(body["projection"])


class TestAvatarFiles(unittest.TestCase):
    """验收 4: 缺失頭像補齊（10 靈魂 + bryan + group 都合法 PNG）"""

    AVATARS = [
        "yua", "ruka", "akane", "rem", "ram", "mahiru", "anna", "mai", "miku", "aoi",
        "bryan", "group",
    ]

    def setUp(self):
        self.avatars_dir = Path(__file__).resolve().parent.parent / "static" / "avatars"
        self.assertTrue(self.avatars_dir.is_dir(), "static/avatars 目錄應存在")

    def test_all_avatars_exist_and_valid_png(self):
        for name in self.AVATARS:
            path = self.avatars_dir / f"{name}.png"
            self.assertTrue(path.is_file(), f"缺少頭像 {name}.png")
            data = path.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n", f"{name}.png 不是 PNG")
            w, h, bd, ct = struct.unpack(">IIBB", data[16:26])
            self.assertEqual((w, h), (128, 128), f"{name}.png 應為 128x128")
            self.assertEqual((bd, ct), (8, 6), f"{name}.png 應為 8-bit RGBA")


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
