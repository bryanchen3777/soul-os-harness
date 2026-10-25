"""tests/inner_life/test_revision_chain_guard.py — G-1＋G-2 守衛 I1～I9（**恰 9 筆**）。

工單：logs/CAMPAIGN-B-BATTLE2-G1-G2-REVISION-CHAIN-WORK-ORDER.md

被測模組：``src/inner_life/revision_chain.py``（本票新增；**離線沙盒，未掛載**）

紀律
----
- 🔴 **本檔不依賴生產資料**、**不讀寫** ``data/**``（全部使用合成資料）。
- 🔴 本檔不使用 ``subprocess``／``os.spawn*``／``os.system``／``os.popen``（R5 護欄）。
- 🔴 本檔**不**啟動伺服器、不綁任何埠、不對生產發請求、**0 真實 LLM 呼叫**。
- 假引擎沿用 repo 既有樣式（``tests/test_c31_relational_expression.py:441`` 的
  ``SimpleNamespace`` ＋ 明確方法）⇒ **不 import** ``soul_elevation``（I7 同款禁令）。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from src.inner_life import revision_chain as rc


REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER_PATH = REPO_ROOT / "src/inner_life/elevation_adapter.py"

#: 測試用的 agent（**合成**，非生產 id）。
AGENT = "agent_rem"

#: I7 的禁用 import 前綴（含其子模組；**結構性**零 LLM 成本）。
BANNED_IMPORTS: Tuple[str, ...] = (
    "src.llm",
    "httpx",
    "requests",
    "socket",
    "subprocess",
    "urllib",
    "asyncio",
    "soul_elevation",
)

#: I8 的掛載契約**必須被檢查**的三個名稱（**內容與數量皆被釘死**）。
#:
#: 🔺 這個常數同時是 I8 的**反vacuity 錨點**：若有人把 I8 的 AST 檢查「放寬」
#: （例如移除 ``engine`` 這個名稱的檢查），本常數就跟著少一項 ⇒ I8 的第一道
#: 斷言（清單完整性）轉紅 ⇒ **放寬檢查這件事本身**會被看見。
MOUNT_CONTRACT_NAMES: Tuple[str, ...] = ("nodes", "edges", "engine")


# ── AST 工具（沿用 tests/inner_life/test_tension_closure_loop.py 同款樣式）──
def _effective_module_path(module: Any) -> Path:
    """模組**實際**被載入的來源檔（變異實驗下＝%TEMP% 底下的副本）。"""
    source = inspect.getsourcefile(module)
    assert source, f"模組 {module!r} 沒有來源檔"
    return Path(source).resolve()


def _imported_module_names(path: Path) -> Set[str]:
    """檔案內**所有** import 的模組名（含 ``from X import Y`` 的 ``X.Y`` 形式）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相對 import（本 repo 不用 ⇒ 略過）
                continue
            base = node.module or ""
            names.add(base)
            for alias in node.names:
                names.add(f"{base}.{alias.name}")
    return names


def _imports_any(names: Set[str], banned: str) -> bool:
    """是否 import 了 ``banned``（含其子模組）。"""
    return any(name == banned or name.startswith(banned + ".") for name in names)


def _mount_contract_names() -> Set[str]:
    """``elevation_adapter.elevate_matured_patterns()`` 函式體內出現的名稱集合。"""
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "elevate_matured_patterns":
            func = node
            break
    assert func is not None, "elevation_adapter.elevate_matured_patterns() 不存在"
    names: Set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


# ── 合成資料（形狀與生產節點／證據邊一致）────────────────────────────────
def _soul_node(
    node_id: str,
    *,
    node_type: str = "belief",
    content: str = "命題甲",
    confidence: float = 0.73,
    agent_id: str = AGENT,
) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "candidate_node_type": None,
        "content": content,
        "confidence": confidence,
        "valence": "neutral",
        "agent_id": agent_id,
        "parent_node_id": None,
        "lineage_depth": 0,
        "lineage_path": node_id,
    }


def _pattern(
    node_id: str,
    *,
    dimension: str = "belief",
    content: str = "命題乙",
    agent_id: str = AGENT,
) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": "pattern",
        "candidate_node_type": dimension,
        "content": content,
        "confidence": 0.5,
        "valence": "neutral",
        "agent_id": agent_id,
    }


def _edge(
    node_id: str,
    source_id: str,
    *,
    event_id: str | None = None,
    agent_id: str = AGENT,
) -> Dict[str, Any]:
    return {
        "edge_id": f"edge-{node_id}-{source_id}",
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": source_id,
        "agent_id": agent_id,
        "weight": 1.0,
        "valid_from_ts": "2026-10-24T00:00:00+00:00",
        "valid_until_ts": None,
        "inner_life_event_id": event_id,
        "trigger_type": "diary:night",
    }


# ── 確定性假引擎（沿用 repo 既有樣式：SimpleNamespace ＋ 明確方法）─────────
def _fake_engine(
    *,
    old_node: Any = None,
    raise_value_error: bool = False,
) -> SimpleNamespace:
    """記錄 ``revise()`` 呼叫參數與次數的假引擎（**不 import** ``soul_elevation``）。

    刻意提供 ``record_contradiction``／``supersede``／``reinforce`` 三個**禁寫方法**
    ⇒ I9 才能斷言「它們**沒有**被呼叫」（而非「它們不存在」）。
    """
    calls: List[Dict[str, Any]] = []
    forbidden: List[str] = []

    def revise(node_id: str, new_content: str, new_confidence: Any, **kwargs: Any):
        calls.append(
            {
                "node_id": node_id,
                "new_content": new_content,
                "new_confidence": new_confidence,
                "valence": kwargs.get("valence"),
                "provenance": kwargs.get("provenance"),
                "source_ids": kwargs.get("source_ids"),
                "source_type": kwargs.get("source_type"),
            }
        )
        if raise_value_error:
            raise ValueError("engine threshold not met")
        return SimpleNamespace(node_id="node-revised")

    def get_node(node_id: str):
        if old_node is not None:
            return old_node
        raise KeyError(f"node not found: {node_id!r}")

    def _forbidden_call(name: str):
        def _call(*args: Any, **kwargs: Any):
            forbidden.append(name)
            return None

        return _call

    return SimpleNamespace(
        revise=revise,
        get_node=get_node,
        calls=calls,
        forbidden=forbidden,
        record_contradiction=_forbidden_call("record_contradiction"),
        supersede=_forbidden_call("supersede"),
        reinforce=_forbidden_call("reinforce"),
    )


def _target(**kwargs: Any) -> rc.RevisionTarget:
    defaults: Dict[str, Any] = {
        "node_id": "b1",
        "new_content": "命題乙",
        "new_confidence": 0.73,
        "old_source_ids": ("src-old",),
        "new_source_ids": ("src-new",),
        "valence": "neutral",
    }
    defaults.update(kwargs)
    return rc.RevisionTarget(**defaults)


# ── I1：篩選正確性 ────────────────────────────────────────────────────────
def test_i1_selects_only_existing_soul_node_of_same_dimension():
    """I1 — 給定「同維度已有 soul node ＋ 新成熟 pattern」⇒ 選出該 soul node；
    給定「**無**既有 soul node」⇒ **零回傳**（不得憑空造 depth-2）。"""
    # (a) 正例：同維度（belief）既有 soul node ＋ 同維度新成熟 pattern。
    nodes = [_soul_node("b1"), _pattern("p1", dimension="belief", content="命題乙")]
    edges = [_edge("p1", "src-new", event_id="ev-1")]
    targets = rc.select_revision_targets(nodes, edges, agent_id=AGENT)
    assert [target.node_id for target in targets] == ["b1"]

    # (b) 全無既有 soul node ⇒ 零回傳（不得憑空造 depth-2）。
    nodes = [_pattern("p1", dimension="belief", content="命題乙")]
    targets = rc.select_revision_targets(nodes, edges, agent_id=AGENT)
    assert targets == []

    # (c) 該維度沒有既有 soul node（別的維度才有）⇒ 零回傳。
    nodes = [
        _soul_node("b1", node_type="belief"),
        _pattern("p2", dimension="value", content="另一維度的新命題"),
    ]
    edges = [_edge("p2", "src-new", event_id="ev-1")]
    targets = rc.select_revision_targets(nodes, edges, agent_id=AGENT)
    assert targets == []


# ── I2：F5 防護 ───────────────────────────────────────────────────────────
def test_i2_revise_confidence_preserves_old_value_bitwise():
    """I2 — ``compute_revise_confidence(old)`` **逐位元**等於 ``old["confidence"]``；
    且當 ``old["confidence"] != 0.5`` 時結果**不得**是 ``0.5``。"""
    for raw in (0.73, 0.8123456789, 0.25):
        old = {"node_id": "b1", "confidence": raw}
        got = rc.compute_revise_confidence(old)
        assert got == raw
        assert got.hex() == float(raw).hex(), "信心度必須逐位元保留"
    old = {"node_id": "b1", "confidence": 0.73}
    assert rc.compute_revise_confidence(old) != 0.5, "不得回退到樁的 0.5"


# ── I3：G-2 承接 ──────────────────────────────────────────────────────────
def test_i3_provenance_carries_old_lineage():
    """I3 — ``build_revise_provenance()`` 必含 ``old_node_id``／``old_content``／
    ``old_confidence``／``old_source_ids``（**逐筆**）／``new_source_ids``（**逐筆**）。"""
    old = {"node_id": "b1", "content": "命題甲", "confidence": 0.73}
    provenance = rc.build_revise_provenance(
        old,
        old_source_ids=["src-a", "src-b"],
        new_source_ids=["src-c"],
    )
    assert provenance["old_node_id"] == "b1"
    assert provenance["old_content"] == "命題甲"
    assert provenance["old_confidence"] == 0.73
    assert tuple(provenance["old_source_ids"]) == ("src-a", "src-b")
    assert tuple(provenance["new_source_ids"]) == ("src-c",)


# ── I4：對象限制 ──────────────────────────────────────────────────────────
def test_i4_essence_and_pattern_are_never_revision_targets():
    """I4 — ``essence`` 與 ``pattern`` **永不**出現在結果內（首批排除，保守邊界）。"""
    nodes = [
        _soul_node("e1", node_type="essence", content="內涵命題"),
        _pattern("p1", dimension="essence", content="候選命題"),
        _pattern("p2", dimension="belief", content="另一候選命題"),
    ]
    edges = [
        _edge("p1", "src-new-1", event_id="ev-1"),
        _edge("p2", "src-new-2", event_id="ev-2"),
    ]
    targets = rc.select_revision_targets(nodes, edges, agent_id=AGENT)
    target_ids = {target.node_id for target in targets}
    assert "e1" not in target_ids, "essence 不得被發起 revise()"
    assert "p1" not in target_ids, "pattern 不得被發起 revise()"
    assert "p2" not in target_ids, "pattern 不得被發起 revise()"
    assert targets == []


# ── I5：no-op 邊界 ────────────────────────────────────────────────────────
def test_i5_no_op_boundaries_return_nothing():
    """I5 — 無新成熟 pattern、或新 pattern 內容與既有內容**正規化後相同**
    ⇒ **零回傳**（不製造無意義改寫）。"""
    # (a) 沒有任何新成熟 pattern。
    assert rc.select_revision_targets([_soul_node("b1", content="命題甲")], [], agent_id=AGENT) == []

    # (b) 新 pattern 的證據已在目標節點的既有證據集合內 ⇒ 不是「新成熟」證據。
    nodes = [_soul_node("b1", content="命題甲"), _pattern("p1", content="命題乙")]
    edges = [
        _edge("b1", "src-old", event_id="ev-0"),
        _edge("p1", "src-old", event_id="ev-0"),
    ]
    assert rc.select_revision_targets(nodes, edges, agent_id=AGENT) == []

    # (c) 內容正規化（去首尾空白）後相同 ⇒ 不得觸發改寫。
    nodes = [
        _soul_node("b1", content="命題甲"),
        _pattern("p1", content="  命題甲  "),
    ]
    edges = [_edge("p1", "src-new", event_id="ev-1")]
    assert rc.select_revision_targets(nodes, edges, agent_id=AGENT) == []


# ── I6：引擎接線 fail-closed ─────────────────────────────────────────────
def test_i6_apply_revision_is_fail_closed_without_retry():
    """I6 — 引擎拋 ``ValueError`` ⇒ ``apply_revision()`` 回 ``False`` 且**不中斷**呼叫端；
    **不得重試**（呼叫次數 ＝ 1）。"""
    engine = _fake_engine(
        old_node=SimpleNamespace(node_id="b1", content="命題甲", confidence=0.73),
        raise_value_error=True,
    )
    assert rc.apply_revision(engine, _target()) is False
    assert len(engine.calls) == 1, "門檻不足不得重試"


# ── I7：零 LLM 成本（結構性）──────────────────────────────────────────────
def test_i7_module_has_no_llm_or_network_import_path():
    """I7 — AST 檢查本模組**不得** import ``src.llm``／``httpx``／``requests``／
    ``socket``／``subprocess``／``urllib``／``asyncio``／**``soul_elevation``**。"""
    path = _effective_module_path(rc)
    names = _imported_module_names(path)
    for banned in BANNED_IMPORTS:
        assert not _imports_any(names, banned), f"{path.name} 不得 import {banned}"


# ── I8：掛載契約對齊 ──────────────────────────────────────────────────────
def test_i8_mount_contract_is_aligned_with_elevation_adapter():
    """I8 — ``elevation_adapter.elevate_matured_patterns()`` 函式體內確有
    ``nodes``／``edges``／``engine`` 三個可用名稱 ⇒ **下一片掛載不需重構**；
    且本模組簽章的輸入型別與之一致。"""
    # (a) 反vacuity：I8 自身的檢查清單不得被掏空（否則「放寬檢查」會無聲變綠）。
    assert set(MOUNT_CONTRACT_NAMES) == {"nodes", "edges", "engine"}

    # (b) 實質：adapter 的函式體內確有這三個可用名稱。
    found = _mount_contract_names()
    assert set(MOUNT_CONTRACT_NAMES) <= found, (
        f"elevate_matured_patterns() 函式體缺少掛載所需名稱："
        f"{sorted(set(MOUNT_CONTRACT_NAMES) - found)}"
    )

    # (c) 本模組簽章的輸入型別與之一致：nodes／edges 皆為「mapping 序列」，
    #     agent_id 為 keyword-only。
    params = inspect.signature(rc.select_revision_targets).parameters
    assert list(params) == ["nodes", "edges", "agent_id"]
    assert params["agent_id"].kind is inspect.Parameter.KEYWORD_ONLY
    for name in ("nodes", "edges"):
        annotation = str(params[name].annotation)
        assert "Sequence" in annotation and "Mapping" in annotation, (
            f"{name} 的型別必須是 mapping 序列（與 adapter 的節點／邊清單一致）"
        )


# ── I9：血緣形狀 ──────────────────────────────────────────────────────────
def test_i9_engine_call_carries_the_expected_lineage_shape():
    """I9 — ``revise()`` 被呼叫時：``node_id`` ＝ 舊節點 id、``new_confidence``
    ＝ 舊 confidence、provenance 含舊命題與新 source_ids、``source_ids`` 含新
    source_ids；且**不得**呼叫 ``record_contradiction``／``supersede``／``reinforce``。

    🔺 ``new_confidence`` 與 ``compute_revise_confidence(old)`` 比對（而非直接與
    ``old["confidence"]`` 比對）：F5 的**逐位元等式**由 **I2** 釘死；此處釘的是
    「引擎收到的信心度**來自** F5 函式」（因此模組內若寫死 0.5 仍會被本檔抓到）。
    """
    old_node = _soul_node("b1", content="命題甲", confidence=0.73)
    nodes = [old_node, _pattern("p1", dimension="belief", content="命題乙")]
    edges = [
        _edge("b1", "src-old", event_id="ev-0"),
        _edge("p1", "src-new", event_id="ev-1"),
    ]
    targets = rc.select_revision_targets(nodes, edges, agent_id=AGENT)
    assert len(targets) == 1
    target = targets[0]
    assert target.old_source_ids == ("src-old",)
    assert target.new_source_ids == ("src-new",)

    engine = _fake_engine(
        old_node=SimpleNamespace(node_id="b1", content="命題甲", confidence=0.73),
    )
    assert rc.apply_revision(engine, target) is True
    assert len(engine.calls) == 1
    call = engine.calls[0]

    assert call["node_id"] == "b1", "修訂的必須是**舊**節點 id"
    assert call["new_content"] == "命題乙"
    assert call["new_confidence"] == rc.compute_revise_confidence(old_node)
    assert call["valence"] == "neutral"
    assert call["source_type"] == "inner_life_event"
    assert tuple(call["source_ids"]) == ("src-new",)

    provenance = call["provenance"]
    assert provenance["old_node_id"] == "b1"
    assert provenance["old_content"] == "命題甲"
    assert provenance["old_confidence"] == 0.73
    assert tuple(provenance["new_source_ids"]) == ("src-new",)

    assert engine.forbidden == [], "G-3 的寫入路徑一律不得被呼叫"
