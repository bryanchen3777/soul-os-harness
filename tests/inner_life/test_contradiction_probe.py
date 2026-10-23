# tests/inner_life/test_contradiction_probe.py
"""FEAT-SCHEMA-CONTRADICTION §4.1 T1～T6 — 矛盾探針（沙盒原型）守衛。

工單：logs/FEAT-SCHEMA-CONTRADICTION-RFC-WORK-ORDER.md
RFC：docs/FEAT-SCHEMA-CONTRADICTION-RFC.md

覆蓋：
  - **T1** 判準正確（同 agent ＋ 同 lineage 根 ＋ content 逐位元不同 ⇒ 1 組；
    content 逐位元相同 ⇒ 0 組；不同 lineage 根／不同 agent ⇒ 0 組）。
  - **T2** 時間門檻（``valid_from_ts <= created_ts`` ⇒ 不算候選；**先統一解析再比**
    ⇒ 微秒與無微秒、Z 與 +08:00 偏移都不會誤判）。
  - **T3** 欄位形態（``source_id``／``ts``／``event_identity``／``provenance_ref``
    逐字等於來源邊的對應欄位；``ts`` 取 ``valid_from_ts`` 而非 ``created_ts``）。
  - **T4** 純函式（同一輸入兩次呼叫結果逐位元相等；模組**無任何檔案 IO**；
    公開 API **不接受路徑參數**）。
  - **T5** 零新常數（AST：無任何數值常數、無權重命名識別字、無自訂門檻常數）。
  - **T6** 不寫引擎狀態（呼叫前後傳入節點的 ``contradiction_pressure`` 逐位元不變；
    模組**不得**引用 ``record_contradiction``）。

沙盒：全部使用**合成資料**（本檔**不依賴生產資料**、不讀 ``data/**``）。
🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.inner_life import contradiction_probe as probe

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src/inner_life/contradiction_probe.py"

#: 合成節點／邊的固定時間（ISO 8601 UTC）；無真實等待。
T_BASE = "2026-10-01T00:00:00+00:00"
T_LATER = "2026-10-05T12:00:00+00:00"


# ── 合成資料工廠（不碰生產）──────────────────────────────────────────────
def _node(
    node_id: str,
    *,
    node_type: str = "value",
    content: str = "命題甲",
    agent_id: str = "agent_akane",
    parent_node_id=None,
    created_ts: str = T_BASE,
    lifecycle_state: str = "active",
):
    """合成一個靈魂型節點（含 ``contradiction_pressure`` 欄位，供 T6 觀測）。"""
    lineage_path = (
        f"{parent_node_id}/{node_id}" if parent_node_id else node_id
    )
    return {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "agent_id": agent_id,
        "parent_node_id": parent_node_id,
        "lineage_depth": 1 if parent_node_id else 0,
        "lineage_path": lineage_path,
        "created_ts": created_ts,
        "provenance_ref": None,
        "lifecycle_state": lifecycle_state,
        "contradiction_pressure": [],
    }


def _edge(
    edge_id: str,
    *,
    node_id: str,
    source_id: str,
    valid_from_ts: str,
    valid_until_ts=None,
    inner_life_event_id=None,
    agent_id: str = "agent_akane",
    trigger_type: str = "diary:night",
):
    """合成一條支持邊。"""
    return {
        "edge_id": edge_id,
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": source_id,
        "agent_id": agent_id,
        "weight": 1.0,
        "valid_from_ts": valid_from_ts,
        "valid_until_ts": valid_until_ts,
        "inner_life_event_id": inner_life_event_id,
        "trigger_type": trigger_type,
    }


def _same_root_case(*, content_b: str = "命題乙", parent_b: str = "pat-1"):
    """同 agent、同 lineage 根、同一來源產生兩個靈魂節點。

    ``n-1`` 的**較晚支持邊**（``src-1``）落在 ``E(n-1)``；``n-2`` 的同一來源邊
    有效於其**建立當下**（故不在 ``E(n-2)``）⇒ 候選只從 ``n-1`` 方向產生 1 組。
    """
    nodes = [
        _node("pat-1", node_type="pattern", content="模式"),
        _node("n-1", parent_node_id="pat-1", content="命題甲"),
        _node("n-2", parent_node_id=parent_b, content=content_b,
              created_ts="2026-10-02T00:00:00+00:00"),
    ]
    edges = [
        _edge("e-1", node_id="n-1", source_id="src-1",
              valid_from_ts=T_LATER, inner_life_event_id="evt-1"),
        _edge("e-2", node_id="n-2", source_id="src-1",
              valid_from_ts="2026-10-02T00:00:00+00:00",
              inner_life_event_id="evt-1"),
    ]
    return nodes, edges


# ── T1：判準正確 ────────────────────────────────────────────────────────
class TestT1CriteriaCorrectness:
    def test_t1_same_agent_same_lineage_root_diff_content_yields_one_candidate(self):
        nodes, edges = _same_root_case()
        found = probe.find_candidate_contradictions(nodes, edges)
        assert len(found) == 1, f"應產出 1 組候選矛盾，實得 {len(found)}"
        cand = found[0]
        assert cand.node_id == "n-1"
        assert cand.counterpart_node_id == "n-2"
        assert cand.agent_id == "agent_akane"
        assert cand.node_type == "value"

    def test_t1_byte_identical_content_is_not_a_contradiction(self):
        """``content`` **逐位元相同** ⇒ 同命題再確認 ⇒ 0 組（已由 REINFORCE 鏈處理）。"""
        nodes, edges = _same_root_case(content_b="命題甲")
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t1_same_chain_descendant_counts_as_same_lineage(self):
        """「同鏈」：``N'`` 是 ``N`` 的子節點 ⇒ 同 lineage 根 ⇒ 1 組。"""
        nodes, edges = _same_root_case(parent_b="n-1")
        found = probe.find_candidate_contradictions(nodes, edges)
        assert len(found) == 1, f"同鏈應視為同根，實得 {len(found)}"

    def test_t1_different_lineage_root_is_not_a_counterpart(self):
        """不同 lineage 根 ⇒ **不是**對應節點 ⇒ 0 組。

        ⚠️ 這一條正是生產資料上 Q1＝0 的成因（靈魂節點一律是**各自** pattern 的
        子節點 ⇒ 兩顆靈魂節點幾乎不共根）。守衛**不得**放寬。
        """
        nodes, edges = _same_root_case(parent_b="pat-2")
        nodes.append(_node("pat-2", node_type="pattern", content="另一模式"))
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t1_different_agent_is_not_a_counterpart(self):
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-2":
                node["agent_id"] = "agent_ruka"
        for edge in edges:
            if edge["node_id"] == "n-2":
                edge["agent_id"] = "agent_ruka"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t1_agent_filter_restricts_to_one_agent(self):
        nodes, edges = _same_root_case()
        assert probe.find_candidate_contradictions(nodes, edges, agent_id="agent_akane")
        assert probe.find_candidate_contradictions(nodes, edges, agent_id="agent_ruka") == []

    def test_t1_superseded_node_is_out_of_scope(self):
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-1":
                node["lifecycle_state"] = "superseded"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t1_essence_does_not_participate_in_phase1_probe(self):
        """``essence`` 不參與 Phase 1 探針（工單 §2.1）。"""
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-1":
                node["node_type"] = "essence"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t1_pattern_node_is_not_a_counterpart(self):
        """``pattern`` 不是靈魂節點 ⇒ 不得被當成對應節點。"""
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-2":
                node["node_type"] = "pattern"
        assert probe.find_candidate_contradictions(nodes, edges) == []


# ── T2：時間門檻 ────────────────────────────────────────────────────────
class TestT2TimeThreshold:
    def test_t2_edge_before_creation_is_not_evidence(self):
        nodes, edges = _same_root_case()
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_from_ts"] = "2026-09-30T00:00:00+00:00"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t2_edge_at_creation_moment_is_not_evidence(self):
        """``valid_from_ts == created_ts`` ⇒ 建立之**前**／當下 ⇒ 不算候選。"""
        nodes, edges = _same_root_case()
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_from_ts"] = T_BASE
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t2_expired_edge_is_not_evidence(self):
        """``valid_until_ts`` 非 None ⇒ 已被取代 ⇒ 不在 ``E(N)``。"""
        nodes, edges = _same_root_case()
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_until_ts"] = "2026-10-06T00:00:00+00:00"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t2_microsecond_variants_are_normalized_before_comparing(self):
        """無微秒 vs 有微秒：**先統一解析再比** ⇒ 同一瞬間 ⇒ 不算候選。

        ⚠️ 純字串比較會在此誤判（``'+'`` < ``'.'`` ⇒ 邊看起來「晚於」節點）。
        """
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-1":
                node["created_ts"] = "2026-10-05T00:00:00+00:00"
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_from_ts"] = "2026-10-05T00:00:00.000000+00:00"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t2_timezone_offsets_are_normalized_before_comparing(self):
        """``Z`` 與 ``+08:00`` 偏移代表同一瞬間 ⇒ 不算候選。"""
        nodes, edges = _same_root_case()
        for node in nodes:
            if node["node_id"] == "n-1":
                node["created_ts"] = "2026-10-05T08:00:00+08:00"
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_from_ts"] = "2026-10-05T00:00:00Z"
        assert probe.find_candidate_contradictions(nodes, edges) == []

    def test_t2_unparsable_timestamp_is_treated_as_not_established(self):
        """壞時間戳 ⇒ 該配對不成立（不猜、不放寬）。"""
        nodes, edges = _same_root_case()
        for edge in edges:
            if edge["node_id"] == "n-1":
                edge["valid_from_ts"] = "not-a-timestamp"
        assert probe.find_candidate_contradictions(nodes, edges) == []


# ── T3：欄位形態 ────────────────────────────────────────────────────────
class TestT3FieldShape:
    def test_t3_candidate_fields_are_verbatim_from_the_source_edge(self):
        nodes, edges = _same_root_case()
        found = probe.find_candidate_contradictions(nodes, edges)
        assert len(found) == 1
        cand = found[0]
        source_edge = next(e for e in edges if e["edge_id"] == "e-1")
        assert cand.source_id == source_edge["source_id"]
        assert cand.ts == source_edge["valid_from_ts"]
        assert cand.event_identity == source_edge["inner_life_event_id"]
        assert cand.provenance_ref == source_edge["inner_life_event_id"]

    def test_t3_ts_is_valid_from_ts_not_created_ts(self):
        """``ts`` 取邊的 ``valid_from_ts``（**不是**節點的 ``created_ts``）。"""
        nodes, edges = _same_root_case()
        cand = probe.find_candidate_contradictions(nodes, edges)[0]
        assert cand.ts == T_LATER
        assert cand.ts != T_BASE

    def test_t3_candidate_is_frozen(self):
        nodes, edges = _same_root_case()
        cand = probe.find_candidate_contradictions(nodes, edges)[0]
        with pytest.raises(Exception):
            cand.ts = "2099-01-01T00:00:00+00:00"


# ── T4：純函式 ──────────────────────────────────────────────────────────
class TestT4PureFunction:
    def test_t4_same_input_twice_is_bitwise_identical(self):
        nodes, edges = _same_root_case()
        first = probe.find_candidate_contradictions(nodes, edges)
        second = probe.find_candidate_contradictions(nodes, edges)
        assert repr(first) == repr(second)
        assert first == second

    def test_t4_input_order_does_not_change_the_multiset(self):
        nodes, edges = _same_root_case()
        forward = probe.find_candidate_contradictions(nodes, edges)
        backward = probe.find_candidate_contradictions(nodes[::-1], edges[::-1])
        assert sorted(forward, key=lambda c: (c.node_id, c.counterpart_node_id)) == sorted(
            backward, key=lambda c: (c.node_id, c.counterpart_node_id)
        )

    def test_t4_public_api_takes_no_path_parameter(self):
        """公開 API **不接受路徑** ⇒ 函式本身無檔案 IO 依賴。"""
        signature = inspect.signature(probe.find_candidate_contradictions)
        param_names = list(signature.parameters)
        assert param_names == ["nodes", "edges", "agent_id"], param_names
        for name in param_names:
            assert not any(
                token in name.lower() for token in ("path", "file", "dir", "root")
            ), f"探針不得接受路徑參數：{name}"

    def test_t4_module_has_no_file_io_dependency(self):
        """AST：模組內無 ``open``／``Path``／``read_text``／``os``／``pathlib``。"""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        forbidden_names = {
            "open", "Path", "read_text", "read_bytes", "write_text",
            "write_bytes", "glob", "iglob",
        }
        forbidden_modules = {
            "os", "os.path", "pathlib", "io", "shutil", "glob", "subprocess", "tempfile",
        }
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                offenders.append(node.id)
            if isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                offenders.append(node.attr)
            if isinstance(node, ast.Import):
                offenders.extend(
                    a.name for a in node.names if a.name in forbidden_modules
                )
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module in forbidden_modules:
                    offenders.append(node.module)
        assert not offenders, f"探針不得有檔案 IO 依賴：{sorted(set(offenders))}"


# ── T5：零新常數 ────────────────────────────────────────────────────────
class TestT5NoNewConstants:
    """模組內**不得**有任何權重／門檻常數（門檻一律沿用引擎既有常數）。"""

    @staticmethod
    def _tree() -> ast.AST:
        return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))

    def test_t5_no_numeric_constant_at_all(self):
        """無任何數值常數 ⇒ 模組不可能自造門檻／權重係數。"""
        tree = self._tree()
        numbers = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float, complex))
            and not isinstance(node.value, bool)
        ]
        assert not numbers, f"探針不得有任何數值常數：{numbers}"

    def test_t5_no_weight_named_identifier(self):
        tree = self._tree()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
        offenders = sorted(n for n in names if "weight" in n.lower())
        assert not offenders, f"不得有權重命名的識別字：{offenders}"

    def test_t5_no_self_defined_threshold_constant(self):
        """模組層級不得有門檻／門檻語彙的自訂常數。"""
        tree = self._tree()
        threshold_words = ("threshold", "limit", "weight", "score", "min_count", "max_count")
        offenders = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                if any(word in target.id.lower() for word in threshold_words):
                    offenders.append(target.id)
        assert not offenders, f"不得自訂門檻常數：{offenders}"

    def test_t5_thresholds_are_referenced_not_redefined(self):
        """模組不得重新定義引擎既有門檻常數名。"""
        tree = self._tree()
        engine_constants = {
            "LIFECYCLE_N_SUPERSEDE",
            "LIFECYCLE_ESSENCE_N_SUPERSEDE",
            "LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD",
            "LIFECYCLE_ESSENCE_CONFIDENCE_DELTA",
            "LIFECYCLE_ESSENCE_RECONSIDERATION_MIN_EVIDENCE",
        }
        defined = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
        assert not (defined & engine_constants), "不得重定義引擎既有門檻常數"


# ── T6：不寫引擎狀態 ────────────────────────────────────────────────────
class TestT6DoesNotWriteEngineState:
    def test_t6_input_node_contradiction_pressure_is_unchanged(self):
        """呼叫前後，傳入節點的 ``contradiction_pressure`` **逐位元不變**。"""
        nodes, edges = _same_root_case()
        target = nodes[1]
        before = repr(target["contradiction_pressure"])
        probe.find_candidate_contradictions(nodes, edges)
        assert repr(target["contradiction_pressure"]) == before
        assert target["contradiction_pressure"] == []

    def test_t6_real_engine_node_object_is_unchanged(self):
        """用**真實** ``ElevationNode``（frozen）餵入 ⇒ 其累積器不得被寫動。"""
        from soul_elevation.models import ElevationNode

        node = ElevationNode(
            node_id="n-1",
            node_type="value",
            content="命題甲",
            confidence=0.5,
            stability=0.0,
            valence="neutral",
            agent_id="agent_akane",
            parent_node_id="pat-1",
            lineage_depth=1,
            lineage_path="pat-1/n-1",
            created_ts=T_BASE,
            provenance_ref=None,
        )
        nodes, edges = _same_root_case()
        engine_nodes = [node] + [n for n in nodes if n["node_id"] != "n-1"]
        found = probe.find_candidate_contradictions(engine_nodes, edges)
        assert len(found) == 1
        assert node.contradiction_pressure == ()

    def test_t6_module_never_references_record_contradiction(self):
        """AST：模組不得引用 ``record_contradiction``（也不得 import 引擎模組）。"""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "record_contradiction":
                offenders.append(node.attr)
            if isinstance(node, ast.Name) and node.id == "record_contradiction":
                offenders.append(node.id)
            if isinstance(node, ast.Constant) and node.value == "record_contradiction":
                offenders.append("string:'record_contradiction'")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "record_contradiction":
                        offenders.append(alias.name)
                if node.module == "soul_elevation.engine":
                    offenders.append(node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "soul_elevation.engine":
                        offenders.append(alias.name)
        assert not offenders, f"原型不得呼叫／引用 record_contradiction：{offenders}"

    def test_t6_probe_does_not_import_the_engine(self):
        """模組不得 import 引擎（⇒ 結構上不可能寫引擎狀態）。"""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            if isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        offenders = [m for m in imported if m.startswith("soul_elevation")]
        assert not offenders, f"原型不得依賴引擎套件：{offenders}"
