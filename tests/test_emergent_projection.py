"""
tests/test_emergent_projection.py — 灵魂成长闭环 Emergent read-side projection

验收（照工单）:
  1. EMERGENT block 注入 prompt（该灵魂的 belief/value/trait/essence）。
  2. 不投影 pattern、不投影 agent_id="default"（其他灵魂的也不投影）。
  3. 可观测性: 投影的 node_id 有记录（sidecar + log）。
  4. seeded 回归不破坏: 无 emergent 数据时 prompt 与未实现时完全等价。
  5. Growth read 是 inference-time: 只读 elevation_nodes.jsonl,
     不写节点/证据数据（不重跑 elevate）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.inner_life.emergent_projection import (
    PROJECTABLE_NODE_TYPES,
    PROJECTION_TRACE_FILENAME,
    format_emergent_block,
    load_elevation_nodes,
    project_emergent,
)
from src.llm.proxy import _build_messages_group, _build_messages_private

ALICE = "agent_alice"
DEFAULT = "default"

SOUL_TYPES = ("belief", "value", "trait", "essence")


def _make_node(node_id, node_type, content, agent_id=ALICE, created_ts="2026-08-29T00:00:00.000000+00:00"):
    """构造一条 elevation node dict（字段对齐 elevation_nodes.jsonl 记录）。"""
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


def _write_nodes(store_dir: Path, nodes):
    """把节点列表写进 store_dir/elevation_nodes.jsonl（测试用，模拟 append-only store）。"""
    path = store_dir / "elevation_nodes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for n in nodes:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")


class TestEmergentProjectionFilter(unittest.TestCase):
    """验收 1+2: 投影过滤规则"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="emergent_proj_test_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_01_projects_only_own_soul_types(self):
        """只投影该灵魂自己的 belief/value/trait/essence"""
        _write_nodes(self.tmp, [
            _make_node("n-belief", "belief", "我相信真诚对话会塑造人。"),
            _make_node("n-value", "value", "我重视诚实。"),
            _make_node("n-trait", "trait", "我倾向于先倾听再开口。"),
            _make_node("n-essence", "essence", "我的核心是好奇心。"),
            # 不投影: pattern（注意到 ≠ 成为谁）
            _make_node("n-pattern", "pattern", "用户常在深夜提到工作压力。"),
            # 不投影: 其他灵魂 / default（world node）
            _make_node("n-bob", "belief", "Bob 的信念。", agent_id="agent_bob"),
            _make_node("n-default", "belief", "world node 的信念。", agent_id=DEFAULT),
        ])
        result = project_emergent(ALICE, store_dir=self.tmp)
        projected_types = sorted(p["node_type"] for p in result)
        self.assertEqual(
            projected_types, ["belief", "essence", "trait", "value"],
            "应投影且只投影 4 种 soul 类型",
        )
        ids = {p["node_id"] for p in result}
        self.assertNotIn("n-pattern", ids, "pattern 不得投影")
        self.assertNotIn("n-bob", ids, "其他灵魂的节点不得投影")
        self.assertNotIn("n-default", ids, 'agent_id="default" 的 world node 不得投影')
        # 返回形态: node_id + node_type + content
        for p in result:
            self.assertEqual(set(p.keys()), {"node_id", "node_type", "content"})
            self.assertIsInstance(p["content"], str)
            self.assertTrue(p["content"])

    def test_02_default_agent_never_projected(self):
        """agent_id="default" 无论 node_type 一律不投影"""
        _write_nodes(self.tmp, [_make_node("n-default", "belief", "world belief", agent_id=DEFAULT)])
        self.assertEqual(project_emergent(DEFAULT, store_dir=self.tmp), [])
        self.assertEqual(project_emergent(ALICE, store_dir=self.tmp), [])

    def test_03_missing_store_fail_silent(self):
        """store 目录不存在 / 空 → 回空, 不 raise"""
        empty_dir = self.tmp / "does_not_exist"
        self.assertEqual(project_emergent(ALICE, store_dir=empty_dir), [])
        self.assertEqual(format_emergent_block(ALICE, store_dir=empty_dir), "")
        self.assertEqual(load_elevation_nodes(empty_dir), [])

    def test_04_bad_lines_skipped_not_raised(self):
        """坏 JSON 行跳过, 不 raise, 好行仍投影"""
        path = self.tmp / "elevation_nodes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "{bad json line}\n"
            + json.dumps(_make_node("n-ok", "belief", "好的信念。")) + "\n",
            encoding="utf-8",
        )
        result = project_emergent(ALICE, store_dir=self.tmp)
        self.assertEqual([p["node_id"] for p in result], ["n-ok"])

    def test_05_deterministic_order_by_created_ts(self):
        """投影顺序 deterministic: created_ts asc + node_id asc"""
        _write_nodes(self.tmp, [
            _make_node("n-later", "belief", "晚的", created_ts="2026-08-29T10:00:00+00:00"),
            _make_node("n-earlier", "belief", "早的", created_ts="2026-08-29T01:00:00+00:00"),
        ])
        self.assertEqual(
            [p["node_id"] for p in project_emergent(ALICE, store_dir=self.tmp)],
            ["n-earlier", "n-later"],
        )

    def test_06_projectable_types_constant(self):
        """v1 投影白名单恰为 4 种 soul 类型（无 pattern）"""
        self.assertEqual(PROJECTABLE_NODE_TYPES, frozenset({"belief", "value", "trait", "essence"}))
        self.assertNotIn("pattern", PROJECTABLE_NODE_TYPES)


class TestEmergentObservability(unittest.TestCase):
    """验收 3: 投影的 node_id 有记录（sidecar）"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="emergent_proj_obs_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_01_projection_writes_sidecar_with_node_ids(self):
        _write_nodes(self.tmp, [
            _make_node("n-belief", "belief", "A belief."),
            _make_node("n-value", "value", "A value."),
        ])
        project_emergent(ALICE, store_dir=self.tmp, record_trace=True)
        sidecar = self.tmp / PROJECTION_TRACE_FILENAME
        self.assertTrue(sidecar.exists(), "投影应落一条 sidecar 审计记录")
        lines = [json.loads(l) for l in sidecar.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["event_type"], "emergent_projected")
        self.assertEqual(rec["agent_id"], ALICE)
        self.assertEqual(sorted(rec["projected_node_ids"]), ["n-belief", "n-value"])
        self.assertEqual(sorted(rec["projected_node_types"]), ["belief", "value"])
        self.assertEqual(rec["node_count"], 2)

    def test_02_record_trace_false_writes_nothing(self):
        _write_nodes(self.tmp, [_make_node("n-belief", "belief", "A belief.")])
        project_emergent(ALICE, store_dir=self.tmp, record_trace=False)
        sidecar = self.tmp / PROJECTION_TRACE_FILENAME
        self.assertFalse(sidecar.exists())

    def test_03_no_projection_no_sidecar(self):
        """无投影时不落审计（避免空记录噪音）"""
        project_emergent(ALICE, store_dir=self.tmp, record_trace=True)
        sidecar = self.tmp / PROJECTION_TRACE_FILENAME
        self.assertFalse(sidecar.exists())

    def test_04_read_only_no_growth_write(self):
        """Growth read 是 inference-time: 投影不写节点/证据数据"""
        nodes_path = self.tmp / "elevation_nodes.jsonl"
        _write_nodes(self.tmp, [_make_node("n-belief", "belief", "A belief.")])
        before = nodes_path.read_text(encoding="utf-8")
        project_emergent(ALICE, store_dir=self.tmp, record_trace=True)
        format_emergent_block(ALICE, store_dir=self.tmp)
        self.assertEqual(nodes_path.read_text(encoding="utf-8"), before, "不得改写 elevation_nodes.jsonl")
        self.assertFalse((self.tmp / "elevation_edges.jsonl").exists(), "不得写证据边")


class TestEmergentBlockFormatting(unittest.TestCase):
    """EMERGENT block 格式"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="emergent_proj_fmt_"))
        self.addCleanup(lambda: _rmtree(self.tmp))

    def test_01_block_contains_projected_contents(self):
        _write_nodes(self.tmp, [
            _make_node("n-belief", "belief", "我相信真诚对话会塑造人。"),
            _make_node("n-value", "value", "我重视诚实。"),
        ])
        block = format_emergent_block(ALICE, store_dir=self.tmp)
        self.assertIn("[EMERGENT]", block)
        self.assertIn("- [belief] 我相信真诚对话会塑造人。", block)
        self.assertIn("- [value] 我重视诚实。", block)
        # anti-runaway: 不把 emergent 当支持自己的证据
        self.assertIn("不要引用来支持自己", block)

    def test_02_no_projection_returns_empty(self):
        self.assertEqual(format_emergent_block(ALICE, store_dir=self.tmp), "")


class TestEmergentBlockInPrompt(unittest.TestCase):
    """验收 1+4: EMERGENT block 注入 prompt; seeded 回归不破坏"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="emergent_proj_prompt_"))
        self.addCleanup(lambda: _rmtree(self.tmp))
        self._data_root_patch = patch("src.paths.data_root", return_value=self.tmp)
        self._data_root_patch.start()
        self.addCleanup(self._data_root_patch.stop)
        self.soul = (
            "# 角色設定\n"
            "你是一個有靈魂的角色, 跟 Bry 互動。\n"
        )
        self.fake_memory = MagicMock()
        # 让 memory 读取返回空, 避免无关注入干扰断言
        self.fake_memory.get_group_history.return_value = []
        self.fake_memory.get_recent_with_meta.return_value = []

    def _write_alice_nodes(self):
        _write_nodes(self.tmp / "elevation", [
            _make_node("n-belief", "belief", "我相信真诚对话会塑造人。"),
            _make_node("n-essence", "essence", "我的核心是好奇心。"),
        ])

    def test_01_group_prompt_injects_emergent_after_identity(self):
        """群聊: system 含 [EMERGENT], 位置在 identity(角色設定) 之后"""
        self._write_alice_nodes()
        messages = _build_messages_group(
            agent_id=ALICE,
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        content = sys_msgs[0]["content"]
        self.assertIn("[EMERGENT]", content)
        self.assertIn("我相信真诚对话会塑造人。", content)
        self.assertIn("我的核心是好奇心。", content)
        # 语义边界: IDENTITY(角色設定) → EMERGENT → 其餘
        self.assertLess(content.index("# 角色設定"), content.index("[EMERGENT]"))

    def test_02_private_prompt_injects_emergent(self):
        """私聊: 同样注入 EMERGENT"""
        self._write_alice_nodes()
        messages = _build_messages_private(
            agent_id=ALICE,
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        self.assertEqual(len(sys_msgs), 1)
        content = sys_msgs[0]["content"]
        self.assertIn("[EMERGENT]", content)
        self.assertIn("我相信真诚对话会塑造人。", content)

    def test_03_seeded_regression_no_emergent_data(self):
        """seeded 回归: 没有 emergent 数据时, prompt 与未实现时完全等价（无 [EMERGENT]）"""
        # 不写任何节点 → data_root()/elevation 不存在 → 无投影
        messages = _build_messages_group(
            agent_id=ALICE,
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
        )
        sys_msgs = [m for m in messages if m["role"] == "system"]
        content = sys_msgs[0]["content"]
        self.assertNotIn("[EMERGENT]", content)
        # identity + soul 不受影响
        self.assertIn("# 角色設定", content)
        self.assertIn("你是一個有靈魂的角色, 跟 Bry 互動。", content)

    def test_04_seeded_with_only_world_nodes_no_injection(self):
        """仅 world node（agent_id=default）→ 不注入（不污染 seeded 人格）"""
        _write_nodes(self.tmp / "elevation", [
            _make_node("n-default", "belief", "world belief", agent_id=DEFAULT),
        ])
        messages = _build_messages_private(
            agent_id=ALICE,
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
        )
        content = [m for m in messages if m["role"] == "system"][0]["content"]
        self.assertNotIn("[EMERGENT]", content)
        self.assertNotIn("world belief", content)

    def test_05_germ_mode_still_injects_on_top_of_germ_anchor(self):
        """germ 模式: emergent 投影叠加在 germ_anchor 之上（EMERGENT = 人格）"""
        self._write_alice_nodes()
        germ_anchor = "你是一颗刚萌芽的种子。"
        messages = _build_messages_group(
            agent_id=ALICE,
            soul=self.soul,
            current_input="",
            memory_context="",
            memory=self.fake_memory,
            world_context="",
            germ_anchor=germ_anchor,
        )
        content = [m for m in messages if m["role"] == "system"][0]["content"]
        self.assertIn("你是一颗刚萌芽的种子。", content)  # germ anchor 仍在
        self.assertIn("[EMERGENT]", content)             # emergent 叠加
        self.assertLess(content.index("你是一颗刚萌芽的种子。"), content.index("[EMERGENT]"))


def _rmtree(path: Path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
