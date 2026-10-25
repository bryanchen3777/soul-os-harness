"""tests/inner_life/test_elevation_revision_mount.py — 修訂鏈掛載守衛（G1～G14，**恰 14 筆**）。

工單：FEAT-MOUNT-REVISION-CHAIN-ELEVATION（Owner 2026-10-25 裁定）

被測：``src/inner_life/elevation_adapter.py::elevate_matured_patterns()`` 內的
**修訂鏈掛載**（分支 B）＋ ``src/inner_life/revision_chain.py``（選擇器／接線薄層）。

這一段做什麼
------------
讓「pattern 的新證據本來會被丟棄」的情境（分支 B：獨立證據湊不滿門檻 ⇒
``continue``）改成「既有 soul node 被修訂（depth-2）」：

- **掛載點**＝分支 B（``except ValueError`` 路徑）；
- **分支 A**（內容已被同維度 soul node 佔用）維持原行為（idempotent no-op）；
- **0 真實 LLM 成本**（``engine.revise()`` 走 ``StubElevationLLM``）；
- **例外邊界**：``apply_revision()`` 拋出的 ``KeyError``（接線錯誤）必須冒泡出
  ``elevate_matured_patterns()``，不得被寬鬆的 ``except Exception`` 吞掉。

紀律
----
- 🔴 **不依賴生產資料**、**不讀寫** ``data/**``（全部 tmp 沙盒）。
- 🔴 本檔不使用 ``subprocess``／``os.spawn*``／``os.system``／``os.popen``
  （``tests/infra/test_no_production_spawn_guard.py`` 的 R5 規則）。
- 🔴 不啟動伺服器、不綁任何埠、不連網、**0 真實 LLM 呼叫**（只用 ``StubElevationLLM``）。
- 🔺 **變異實驗的載入鉤子**：``SOUL_OS_ELEVATION_ADAPTER_PATH``／
  ``SOUL_OS_REVISION_CHAIN_PATH``——**只允許**指向 ``%TEMP%`` 下的副本
  （repo 檔全程不寫入；沿用 ``tests/test_eval_lifecycle_cli.py:40-78`` 的慣例）。
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 測試用 agent（**合成**，非生產 id）。
AGENT = "agent_rem"

#: 合成節點／邊的時間錨點（固定值 ⇒ 產出可逐位元比較）。
TS = "2026-10-20T00:00:00+00:00"

#: 🔺 G4 的 golden —— 基準取自 **掛載前** 的 ``elevation_adapter.py``
#: （``git show HEAD:src/inner_life/elevation_adapter.py``，由
#: ``%TEMP%/soulos_revision_mount/make_golden.py`` 產生）。
#:
#: 正規化＝把**隨機／時間**欄位換成固定佔位符（``node_id``／``edge_id``／
#: ``created_ts``／``valid_from_ts``／``lineage_path``），其餘欄位逐位元保留。
#: ⇒ 這是「golden 比較」，不是數量近似。
GOLDEN_JSON = """
{
"A": {"edges": [{"edge_id": "<eid>", "inner_life_event_id": "ev-A", "node_id": "p1", "source_id": "src-A", "source_type": "inner_life_event", "trigger_type": "diary:night", "valid_from_ts": "<ts>", "valid_until_ts": null, "weight": 1.0}], "nodes": [{"agent_id": "agent_rem", "candidate_node_type": null, "confidence": 0.73, "content": "命題甲", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 1, "lineage_path": "<path>", "node_id": "s1", "node_type": "belief", "parent_node_id": null, "provenance_ref": "<ref>", "stability": 0.4, "valence": "neutral"}, {"agent_id": "agent_rem", "candidate_node_type": "belief", "confidence": 0.5, "content": "命題甲", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 0, "lineage_path": "<path>", "node_id": "p1", "node_type": "pattern", "parent_node_id": null, "provenance_ref": "<ref>", "stability": 0.0, "valence": "neutral"}], "projection": [{"content": "命題甲", "node_id": "s1", "node_type": "belief"}]},
"C": {"edges": [{"edge_id": "<eid>", "inner_life_event_id": "ev-C1", "node_id": "p1", "source_id": "src-C1", "source_type": "inner_life_event", "trigger_type": "diary:night", "valid_from_ts": "<ts>", "valid_until_ts": null, "weight": 1.0}, {"edge_id": "<eid>", "inner_life_event_id": "ev-C2", "node_id": "p2", "source_id": "src-C2", "source_type": "inner_life_event", "trigger_type": "diary:night", "valid_from_ts": "<ts>", "valid_until_ts": null, "weight": 1.0}, {"edge_id": "<eid>", "inner_life_event_id": "ev-C1", "node_id": "<new-0>", "source_id": "src-C1", "source_type": "inner_life_event", "trigger_type": "diary:night", "valid_from_ts": "<ts>", "valid_until_ts": null, "weight": 1.0}, {"edge_id": "<eid>", "inner_life_event_id": "ev-C2", "node_id": "<new-0>", "source_id": "src-C2", "source_type": "inner_life_event", "trigger_type": "diary:night", "valid_from_ts": "<ts>", "valid_until_ts": null, "weight": 1.0}], "nodes": [{"agent_id": "agent_rem", "candidate_node_type": null, "confidence": 0.73, "content": "命題甲", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 1, "lineage_path": "<path>", "node_id": "s1", "node_type": "belief", "parent_node_id": null, "provenance_ref": "<ref>", "stability": 0.4, "valence": "neutral"}, {"agent_id": "agent_rem", "candidate_node_type": "belief", "confidence": 0.5, "content": "命題乙", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 0, "lineage_path": "<path>", "node_id": "p1", "node_type": "pattern", "parent_node_id": null, "provenance_ref": "<ref>", "stability": 0.0, "valence": "neutral"}, {"agent_id": "agent_rem", "candidate_node_type": "belief", "confidence": 0.5, "content": "命題丙", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 0, "lineage_path": "<path>", "node_id": "p2", "node_type": "pattern", "parent_node_id": null, "provenance_ref": "<ref>", "stability": 0.0, "valence": "neutral"}, {"agent_id": "agent_rem", "candidate_node_type": null, "confidence": 0.5, "content": "命題乙", "created_ts": "<ts>", "lifecycle_state": "active", "lineage_depth": 1, "lineage_path": "<path>", "node_id": "<new-0>", "node_type": "belief", "parent_node_id": "p1", "provenance_ref": "<ref>", "stability": 0.3, "valence": "neutral"}], "projection": [{"content": "命題甲", "node_id": "s1", "node_type": "belief"}, {"content": "命題乙", "node_id": "<new-0>", "node_type": "belief"}]}
}
"""
GOLDEN = json.loads(GOLDEN_JSON)

#: G11 的「掛載路徑不得直接呼叫 LLM」判定用（AST ``ast.Call`` 判定，不做子字串比對）。
BANNED_LLM_CALL_NAMES: frozenset = frozenset(
    {
        "classify",
        "complete",
        "chat",
        "chat_completion",
        "invoke",
        "generate",
        "ask",
        "call_llm",
        "create_llm_proxy",
        "llm",
    }
)

#: G11：掛載路徑不得 import 這些模組（LLM／網路）。
BANNED_IMPORT_PREFIXES: Tuple[str, ...] = (
    "src.llm",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "asyncio",
)


# ── 變異實驗的載入鉤子（%TEMP% 副本；repo 檔全程不寫入）──────────────────
def _load_override(env_name: str, module_name: str):
    """從 ``%TEMP%`` 副本載入模組；未設環境變數 ⇒ 回 ``None``（用 repo 真檔）。

    🔴 安全邊界：覆寫路徑必須在 ``%TEMP%`` 之下 ⇒ CI／生產永遠載入 repo 的真檔。
    """
    raw = os.environ.get(env_name)
    if not raw:
        return None
    path = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if temp_root not in path.parents:
        raise RuntimeError(
            f"{env_name} 只能指向 TEMP 根目錄下的副本（收到：{path}）"
        )
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    # 掛到 package 上 ⇒ ``from src.inner_life import xxx`` 取得**同一份**（單一真相）
    import src.inner_life

    setattr(src.inner_life, module_name.rsplit(".", 1)[-1], mod)
    return mod


# 🔺 載入順序：**先** adapter、**後** revision_chain
#    （``revision_chain`` 反向 import adapter 的 ``_node_candidate_dimension``）。
_adapter_override = _load_override(
    "SOUL_OS_ELEVATION_ADAPTER_PATH", "src.inner_life.elevation_adapter"
)
if _adapter_override is None:
    from src.inner_life import elevation_adapter as _adapter
else:  # pragma: no cover — 變異實驗路徑
    _adapter = _adapter_override

_rc_override = _load_override(
    "SOUL_OS_REVISION_CHAIN_PATH", "src.inner_life.revision_chain"
)
if _rc_override is None:
    from src.inner_life import revision_chain as _rc
else:  # pragma: no cover — 變異實驗路徑
    _rc = _rc_override

from soul_elevation import StubElevationLLM  # noqa: E402
from soul_elevation.engine import InternalizingEngine  # noqa: E402

from src.inner_life.emergent_projection import project_emergent  # noqa: E402

NODES_FILENAME = _adapter.NODES_FILENAME
EDGES_FILENAME = _adapter.EDGES_FILENAME
TRACE_FILENAME = _adapter.TRACE_FILENAME


# ── 合成圖譜（形狀與生產節點／證據邊一致）──────────────────────────────
def _soul(
    node_id: str,
    content: str,
    *,
    node_type: str = "belief",
    confidence: float = 0.73,
    depth: int = 1,
) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": node_type,
        "content": content,
        "confidence": confidence,
        "stability": 0.4,
        "valence": "neutral",
        "agent_id": AGENT,
        "parent_node_id": None,
        "lineage_depth": depth,
        "lineage_path": node_id,
        "created_ts": TS,
        "provenance_ref": None,
        "candidate_node_type": None,
    }


def _pattern(
    node_id: str,
    content: str,
    *,
    dim: str = "belief",
    state: str = "active",
) -> Dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": "pattern",
        "content": content,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "agent_id": AGENT,
        "parent_node_id": None,
        "lineage_depth": 0,
        "lineage_path": node_id,
        "created_ts": TS,
        "provenance_ref": None,
        "candidate_node_type": dim,
        "lifecycle_state": state,
    }


def _edge(node_id: str, source_id: str, event_id: str) -> Dict[str, Any]:
    return {
        "edge_id": f"edge-{node_id}-{source_id}",
        "node_id": node_id,
        "source_type": "inner_life_event",
        "source_id": source_id,
        "agent_id": AGENT,
        "weight": 1.0,
        "valid_from_ts": TS,
        "valid_until_ts": None,
        "inner_life_event_id": event_id,
        "trigger_type": "diary:night",
    }


def _build_store(
    store: Path, nodes: Sequence[Dict[str, Any]], edges: Sequence[Dict[str, Any]]
) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    (store / NODES_FILENAME).write_text(
        "".join(json.dumps(n, ensure_ascii=False) + "\n" for n in nodes),
        encoding="utf-8",
    )
    (store / EDGES_FILENAME).write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in edges),
        encoding="utf-8",
    )
    return store


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _store_nodes(store: Path) -> List[Dict[str, Any]]:
    return _read_jsonl(store / NODES_FILENAME)


def _store_edges(store: Path) -> List[Dict[str, Any]]:
    return _read_jsonl(store / EDGES_FILENAME)


def _store_bytes(store: Path) -> Dict[str, bytes]:
    """資料面 + 稽核 sidecar 的逐位元快照（用於「與掛載前逐位元相同」）。"""
    return {
        name: (store / name).read_bytes()
        for name in (NODES_FILENAME, EDGES_FILENAME, TRACE_FILENAME)
        if (store / name).exists()
    }


def _normalize(
    nodes: Sequence[Dict[str, Any]],
    edges: Sequence[Dict[str, Any]],
    projection: Sequence[Dict[str, Any]],
    input_node_ids: Sequence[str],
) -> Dict[str, Any]:
    """把隨機／時間欄位換成固定佔位符（golden 比較用）。"""
    known = set(input_node_ids)
    mapping: Dict[str, str] = {}
    counter = 0
    for rec in nodes:
        nid = rec.get("node_id")
        if nid in known:
            mapping[nid] = nid
        else:
            mapping[nid] = f"<new-{counter}>"
            counter += 1
    out_nodes = [
        {
            "node_id": mapping.get(rec.get("node_id"), rec.get("node_id")),
            "node_type": rec.get("node_type"),
            "content": rec.get("content"),
            "confidence": rec.get("confidence"),
            "stability": rec.get("stability"),
            "valence": rec.get("valence"),
            "agent_id": rec.get("agent_id"),
            "parent_node_id": mapping.get(
                rec.get("parent_node_id"), rec.get("parent_node_id")
            ),
            "lineage_depth": rec.get("lineage_depth"),
            "candidate_node_type": rec.get("candidate_node_type"),
            "lifecycle_state": rec.get("lifecycle_state", "active"),
            "created_ts": "<ts>",
            "provenance_ref": "<ref>",
            "lineage_path": "<path>",
        }
        for rec in nodes
    ]
    out_edges = [
        {
            "edge_id": "<eid>",
            "node_id": mapping.get(rec.get("node_id"), rec.get("node_id")),
            "source_type": rec.get("source_type"),
            "source_id": rec.get("source_id"),
            "weight": rec.get("weight"),
            "inner_life_event_id": rec.get("inner_life_event_id"),
            "trigger_type": rec.get("trigger_type"),
            "valid_from_ts": "<ts>",
            "valid_until_ts": rec.get("valid_until_ts"),
        }
        for rec in edges
    ]
    out_proj = [
        {
            "node_id": mapping.get(p.get("node_id"), p.get("node_id")),
            "node_type": p.get("node_type"),
            "content": p.get("content"),
        }
        for p in projection
    ]
    return {"nodes": out_nodes, "edges": out_edges, "projection": out_proj}


def _snapshot(store: Path, input_node_ids: Sequence[str]) -> Dict[str, Any]:
    return _normalize(
        _store_nodes(store),
        _store_edges(store),
        project_emergent(AGENT, store_dir=store, record_trace=False),
        input_node_ids,
    )


# ── 觀測鉤子（記錄 revise()／選擇器呼叫；可注入例外）────────────────────
class _ReviseSpy:
    """記錄 ``engine.revise()`` 的呼叫；可注入要拋的例外。"""

    def __init__(self, monkeypatch) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.raise_exc: Optional[BaseException] = None
        original = InternalizingEngine.revise
        spy = self

        def _spy(engine, node_id, new_content, new_confidence=None, **kwargs):
            spy.calls.append(
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
            if spy.raise_exc is not None:
                raise spy.raise_exc
            return original(engine, node_id, new_content, new_confidence, **kwargs)

        monkeypatch.setattr(InternalizingEngine, "revise", _spy)


@pytest.fixture
def revise_spy(monkeypatch) -> _ReviseSpy:
    return _ReviseSpy(monkeypatch)


@pytest.fixture
def selector_spy(monkeypatch) -> List[Dict[str, Any]]:
    """記錄 ``revision_chain.select_revision_targets()`` 的呼叫（守 G5「不觸發選取器」）。"""
    calls: List[Dict[str, Any]] = []
    original = _rc.select_revision_targets

    def _spy(nodes, edges, *, agent_id):
        targets = original(nodes, edges, agent_id=agent_id)
        calls.append(
            {
                "agent_id": agent_id,
                "node_count": len(nodes),
                "targets": list(targets),
            }
        )
        return targets

    monkeypatch.setattr(_rc, "select_revision_targets", _spy)
    return calls


# ── 場景（與驗收票的四場景實測同一形狀）──────────────────────────────
def _scenario_a(store: Path) -> Path:
    """A：內容與既有同維度 soul node **相同**（內容被佔用）→ 分支 A。"""
    return _build_store(
        store,
        [_soul("s1", "命題甲"), _pattern("p1", "命題甲")],
        [_edge("p1", "src-A", "ev-A")],
    )


def _scenario_b(store: Path) -> Path:
    """B：內容不同、獨立證據不足（1 < 2）→ 分支 B → 命中修訂。"""
    return _build_store(
        store,
        [_soul("s1", "命題甲"), _pattern("p1", "命題乙")],
        [_edge("p1", "src-B", "ev-B")],
    )


def _scenario_c(store: Path) -> Path:
    """C（對照）：內容不同且證據充足 → 引擎自己產 sibling soul node（depth 1）。"""
    return _build_store(
        store,
        [
            _soul("s1", "命題甲"),
            _pattern("p1", "命題乙"),
            _pattern("p2", "命題丙"),
        ],
        [_edge("p1", "src-C1", "ev-C1"), _edge("p2", "src-C2", "ev-C2")],
    )


def _scenario_d(store: Path) -> Path:
    """D：既有 soul node 已消化 K1，新 pattern 帶 K1+K2（K2 為新證據）→ 分支 B。"""
    return _build_store(
        store,
        [_soul("s1", "命題甲"), _pattern("p1", "命題乙")],
        [
            _edge("s1", "K1", "ev-1"),
            _edge("p1", "K1", "ev-1"),
            _edge("p1", "K2", "ev-2"),
        ],
    )


# ─────────────────────────────────────────────────────────────────────
# G1：未成熟 Pattern 不觸發修訂
# ─────────────────────────────────────────────────────────────────────
def test_g1_pattern_outside_candidate_set_never_drives_revision(
    tmp_path, revise_spy, selector_spy
):
    """G1 — **不在候選集**（休眠）與未成熟的 pattern 不得觸發 ``apply_revision()``。

    形狀：``a-dormant``（休眠 ⇒ SOUL-BUG-010 防線二擋掉、不進候選組）＋
    ``z-active``（候選組內）。兩顆 pattern **共用同一 source_id** ⇒ 引擎的
    獨立證據計數（同 source 只算 1 份）＝ 1 < 2 ⇒ 走**分支 B**。

    🔺 斷言的是「**誰**驅動了修訂」：修訂後的正文必須是候選組內那顆 pattern
    的正文。若掛載端沒把 pattern 集合收斂到候選組，選擇器會挑到
    ``node_id`` 較小的 ``a-dormant`` ⇒ 這支守衛轉紅。
    """
    store = _build_store(
        tmp_path / "elevation",
        [
            _soul("s1", "命題甲"),
            _pattern("a-dormant", "命題乙", state="dormant"),
            _pattern("z-active", "命題丙"),
        ],
        [
            _edge("a-dormant", "src-K2", "ev-dormant"),
            _edge("z-active", "src-K2", "ev-active"),
        ],
    )
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(out) == 1, "分支 B 應產一顆 depth-2 修訂節點"
    revised = out[0]
    assert revised.content == "命題丙", (
        "修訂必須由**候選集內**的 pattern 驅動（休眠 pattern 不得驅動修訂）"
    )
    assert revised.content != "命題乙"
    assert revised.lineage_depth == 2
    assert len(revise_spy.calls) == 1
    assert revise_spy.calls[0]["node_id"] == "s1"


# ─────────────────────────────────────────────────────────────────────
# G2：非同候選維度不修訂
# ─────────────────────────────────────────────────────────────────────
def test_g2_target_of_other_dimension_is_never_revised(
    tmp_path, revise_spy, selector_spy
):
    """G2 — 候選維度（value）與目標維度（belief）不符 ⇒ **不選、不修訂**。

    🔺 反vacuity：守衛必須真的走到選擇器（否則「沒修訂」可能只是因為掛載
    根本沒跑）⇒ 斷言 ``selector_spy`` 有被呼叫且回傳 0 個目標。
    """
    store = _build_store(
        tmp_path / "elevation",
        [_soul("s1", "命題甲"), _pattern("p1", "命題乙", dim="value")],
        [_edge("p1", "src-V", "ev-V")],
    )
    before = _store_bytes(store)
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(selector_spy) == 1, "分支 B 必須走選擇器（否則這條守衛是假綠）"
    assert selector_spy[0]["targets"] == [], "非同候選維度不得被選為修訂目標"
    assert revise_spy.calls == [], "非同候選維度不得被修訂"
    assert out == []
    assert _store_bytes(store) == before, "未命中的分支必須逐位元 no-op"


# ─────────────────────────────────────────────────────────────────────
# G3：信心度逐位元繼承（F5）
# ─────────────────────────────────────────────────────────────────────
def test_g3_revised_confidence_is_bitwise_inherited(tmp_path, revise_spy):
    """G3 — 修訂後新節點的 confidence ＝ ``compute_revise_confidence(old_node)``
    的結果（**逐位元相等**），不得重新計算、不得夾帶樁的 0.5。"""
    old_conf = 0.8123456789
    store = _build_store(
        tmp_path / "elevation",
        [_soul("s1", "命題甲", confidence=old_conf), _pattern("p1", "命題乙")],
        [_edge("p1", "src-B", "ev-B")],
    )
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(revise_spy.calls) == 1
    call = revise_spy.calls[0]
    expected = _rc.compute_revise_confidence(
        {"node_id": "s1", "content": "命題甲", "confidence": old_conf}
    )
    assert call["new_confidence"] == expected
    assert float(call["new_confidence"]).hex() == float(old_conf).hex(), (
        "信心度必須逐位元繼承（F5）"
    )
    assert call["new_confidence"] != 0.5, "不得回退到樁的 0.5"

    assert len(out) == 1
    assert out[0].confidence == old_conf
    assert float(out[0].confidence).hex() == float(old_conf).hex()
    persisted = [n for n in _store_nodes(store) if n["lineage_depth"] == 2]
    assert len(persisted) == 1
    assert float(persisted[0]["confidence"]).hex() == float(old_conf).hex()


# ─────────────────────────────────────────────────────────────────────
# G4：無異常時原昇華路徑行為 100% 保持（golden 比較）
# ─────────────────────────────────────────────────────────────────────
def test_g4_no_anomaly_path_matches_pre_mount_golden(tmp_path):
    """G4 — 分支 A（內容被佔用）與分支 C（成功路徑）的產出（節點、邊、投影）
    與**掛載前**逐位元相同（golden 比較，不是數量近似）。

    golden 的基準取自 ``git show HEAD:src/inner_life/elevation_adapter.py``
    （掛載前版本）⇒ 本守衛是真的在比「掛載前 vs 掛載後」。
    """
    store_a = _scenario_a(tmp_path / "A")
    _adapter.elevate_matured_patterns(store_dir=store_a)
    assert _snapshot(store_a, ["s1", "p1"]) == GOLDEN["A"]

    store_c = _scenario_c(tmp_path / "C")
    _adapter.elevate_matured_patterns(store_dir=store_c)
    assert _snapshot(store_c, ["s1", "p1", "p2"]) == GOLDEN["C"]


# ─────────────────────────────────────────────────────────────────────
# G5：分支 A 維持原行為且不觸發選取器
# ─────────────────────────────────────────────────────────────────────
def test_g5_branch_a_stays_idempotent_noop(tmp_path, revise_spy, selector_spy):
    """G5 — 內容被同維度 soul node 佔用 ⇒ 仍 ``continue``、**不觸發選擇器**、
    不產生任何 revision、投影不變。

    🔺 「不觸發選取器」是這條守衛的核心：若把掛載點搬到分支 A，選擇器就會
    被呼叫 ⇒ 這支守衛轉紅（即使該場景最後沒有真的修訂）。
    """
    store = _scenario_a(tmp_path / "elevation")
    before = _store_bytes(store)
    before_proj = project_emergent(AGENT, store_dir=store, record_trace=False)

    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert out == []
    assert selector_spy == [], "分支 A 不得觸發選擇器（掛在那裡＝功能 no-op）"
    assert revise_spy.calls == []
    assert _store_bytes(store) == before, "分支 A 必須逐位元 no-op"
    after_proj = project_emergent(AGENT, store_dir=store, record_trace=False)
    assert after_proj == before_proj, "分支 A 的投影不得變動"


# ─────────────────────────────────────────────────────────────────────
# G6：分支 B 命中才走修訂鏈
# ─────────────────────────────────────────────────────────────────────
def test_g6_branch_b_routes_through_revision_chain(tmp_path, revise_spy, selector_spy):
    """G6 — (a) 命中選擇器 ⇒ 走修訂鏈；(b) 未命中 ⇒ 行為與掛載前**逐位元相同**。"""
    # (a) 命中：既有同維度 soul node ＋ 內容不同的新 pattern。
    hit = _scenario_b(tmp_path / "hit")
    out = _adapter.elevate_matured_patterns(store_dir=hit)
    assert len(selector_spy) == 1
    assert len(selector_spy[0]["targets"]) == 1
    assert len(revise_spy.calls) == 1
    assert revise_spy.calls[0]["node_id"] == "s1"
    assert len(out) == 1
    assert out[0].lineage_depth == 2
    assert out[0].parent_node_id == "s1"

    # (b) 未命中：同維度沒有既有 soul node ⇒ 選擇器 0 目標 ⇒ 逐位元 no-op。
    revise_spy.calls.clear()
    selector_spy.clear()
    miss = _build_store(
        tmp_path / "miss",
        [_pattern("p1", "命題乙")],
        [_edge("p1", "src-M", "ev-M")],
    )
    before = _store_bytes(miss)
    out_miss = _adapter.elevate_matured_patterns(store_dir=miss)
    assert len(selector_spy) == 1
    assert selector_spy[0]["targets"] == []
    assert revise_spy.calls == []
    assert out_miss == []
    assert _store_bytes(miss) == before, "未命中的分支必須逐位元 no-op"


# ─────────────────────────────────────────────────────────────────────
# G7：場景 C 不受影響
# ─────────────────────────────────────────────────────────────────────
def test_g7_scenario_c_still_produces_depth1_sibling(tmp_path, revise_spy):
    """G7 — 證據充足時引擎自己產 sibling soul node（depth 1），流程與掛載前相同
    ⇒ **不得**走修訂鏈。"""
    store = _scenario_c(tmp_path / "elevation")
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(out) == 1
    soul = out[0]
    assert soul.node_type == "belief"
    assert soul.lineage_depth == 1, "場景 C 產的是 depth-1 sibling，不是 depth-2"
    assert soul.parent_node_id in {"p1", "p2"}
    assert revise_spy.calls == [], "場景 C 不得走修訂鏈"
    depths = sorted(n["lineage_depth"] for n in _store_nodes(store))
    assert depths == [0, 0, 1, 1], f"場景 C 的血緣深度分佈被改動：{depths}"


# ─────────────────────────────────────────────────────────────────────
# G8：場景 D 走修訂鏈
# ─────────────────────────────────────────────────────────────────────
def test_g8_scenario_d_produces_depth2_revision(tmp_path, revise_spy):
    """G8 — 既有 soul node 已消化 K1、新 pattern 帶 K1+K2 ⇒ 產 depth-2 節點，
    且血緣 depth ＝ 2、父節點 ＝ 被修訂的舊 soul node。"""
    store = _scenario_d(tmp_path / "elevation")
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(revise_spy.calls) == 1
    assert revise_spy.calls[0]["node_id"] == "s1"
    assert len(out) == 1
    revised = out[0]
    assert revised.lineage_depth == 2
    assert revised.parent_node_id == "s1"
    assert revised.node_type == "belief"
    assert revised.content == "命題乙"

    persisted = [n for n in _store_nodes(store) if n.get("lineage_depth") == 2]
    assert len(persisted) == 1
    assert persisted[0]["parent_node_id"] == "s1"


# ─────────────────────────────────────────────────────────────────────
# G9：INV-2 證據隔離
# ─────────────────────────────────────────────────────────────────────
def test_g9_new_node_sources_exclude_target_evidence(tmp_path, revise_spy):
    """G9 — 修訂後新節點的 source_ids **不得重複計入**目標節點的既有證據集合
    （否則同一顆 pattern 會被重複消化）。

    場景 D：目標 ``s1`` 已消化 ``K1``；新 pattern 帶 ``K1+K2`` ⇒ 新節點只能
    掛 ``K2``（``K1`` 已被 ``s1`` 消化）。
    """
    store = _scenario_d(tmp_path / "elevation")
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(out) == 1
    new_id = out[0].node_id
    new_sources = {
        e["source_id"] for e in _store_edges(store) if e["node_id"] == new_id
    }
    target_sources = {
        e["source_id"] for e in _store_edges(store) if e["node_id"] == "s1"
    }
    assert target_sources == {"K1"}
    assert new_sources == {"K2"}, (
        f"新節點的證據必須排除目標既有證據（實得 {new_sources}）"
    )
    assert not (new_sources & target_sources), "同一批證據不得被消化兩次"


# ─────────────────────────────────────────────────────────────────────
# G10：例外冒泡不被寬鬆 except 吞掉
# ─────────────────────────────────────────────────────────────────────
def test_g10_keyerror_from_revision_bubbles_out(tmp_path, revise_spy):
    """G10 — ``apply_revision()`` 拋 ``KeyError`` ⇒ 必須**冒泡出**
    ``elevate_matured_patterns()``（不得被維度級 ``except Exception`` 吞掉、
    不得只記 warning、不得被最外層失敗隔離吞掉）。"""
    store = _scenario_b(tmp_path / "elevation")
    before = _store_bytes(store)
    revise_spy.raise_exc = KeyError("node not found: 's1'")

    with pytest.raises(KeyError):
        _adapter.elevate_matured_patterns(store_dir=store)

    assert len(revise_spy.calls) == 1, "不得重試"
    assert _store_bytes(store) == before, "失敗不得留下半套節點／邊"


# ─────────────────────────────────────────────────────────────────────
# G11：零真實 LLM 成本
# ─────────────────────────────────────────────────────────────────────
def test_g11_mount_path_has_no_direct_llm_call(tmp_path, monkeypatch):
    """G11 — 掛載路徑**不得直接呼叫 LLM**（AST ``ast.Call`` 判定），且實跑一輪
    證明 **確實產出 depth-2 節點**且只經過 ``StubElevationLLM``（零真實呼叫）。"""
    # (a) AST：掛載函式體內不得有 LLM 呼叫點／LLM・網路 import。
    source = inspect.getsourcefile(_adapter)
    assert source, "被測模組沒有來源檔"
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))
    func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "elevate_matured_patterns":
            func = node
            break
    assert func is not None, "elevation_adapter.elevate_matured_patterns() 不存在"

    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                called = node.func.attr
            elif isinstance(node.func, ast.Name):
                called = node.func.id
            else:  # pragma: no cover — 本函式沒有其他 callable 形狀
                continue
            assert called not in BANNED_LLM_CALL_NAMES, (
                f"掛載路徑不得直接呼叫 LLM（發現 {called}）"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    alias.name == b or alias.name.startswith(b + ".")
                    for b in BANNED_IMPORT_PREFIXES
                ), f"掛載路徑不得 import {alias.name}"
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            assert not any(
                base == b or base.startswith(b + ".")
                for b in BANNED_IMPORT_PREFIXES
            ), f"掛載路徑不得 import {base}"

    # (b) 實跑：只經過 StubElevationLLM（離線、確定性）⇒ 零真實呼叫。
    calls: List[Tuple[str, str]] = []
    original = StubElevationLLM.classify

    def _counting(self, content, provenance, prior_node_type):
        calls.append((content, prior_node_type))
        return original(self, content, provenance, prior_node_type)

    monkeypatch.setattr(StubElevationLLM, "classify", _counting)

    store = _scenario_d(tmp_path / "elevation")
    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert len(out) == 1
    assert out[0].lineage_depth == 2, "掛載路徑必須真的產出 depth-2 節點"
    assert len(calls) == 1, "LLM 互動必須只經過 StubElevationLLM（離線）"
    assert calls[0][0] == "命題乙"


# ─────────────────────────────────────────────────────────────────────
# G12：投影面行為變更＝預期行為並鎖定
# ─────────────────────────────────────────────────────────────────────
def test_g12_projection_change_is_expected_and_locked(tmp_path):
    """G12 — depth-2 節點會進 EMERGENT（**改變可見命題**）⇒ 這是本功能**預期**的
    投影面行為變更，必須被守衛寫死（不是 bug、不是意外）。

    🔺 同時鎖住「父節點仍在投影裡」這個**中間狀態**：修訂不改寫舊節點，
    舊節點的 ``SUPERSEDED`` 狀態由生命週期 sidecar 負責 ⇒ 在那之前
    lineage 降維（``我以前覺得 A，但後來發現 B``）不會生效。此斷言的作用是
    「讓這個過渡狀態變成被寫死的 conscious 選擇」，而不是默默漂移。
    """
    store = _scenario_d(tmp_path / "elevation")
    before = project_emergent(AGENT, store_dir=store, record_trace=False)
    assert [p["content"] for p in before] == ["命題甲"]

    _adapter.elevate_matured_patterns(store_dir=store)

    after = project_emergent(AGENT, store_dir=store, record_trace=False)
    contents = [p["content"] for p in after]
    assert "命題乙" in contents, "depth-2 節點必須進 EMERGENT（預期行為變更）"
    assert contents == ["命題甲", "命題乙"], (
        f"投影內容形狀被改動：{contents}（父節點仍在投影＝sidecar 尚未標 SUPERSEDED 的"
        "過渡狀態；一旦生命週期 sidecar 標了 SUPERSEDED，這條會轉紅並需 consciously 更新）"
    )
    assert [p["node_type"] for p in after] == ["belief", "belief"]


# ─────────────────────────────────────────────────────────────────────
# G13：冪等：每個目標至多一條修訂鏈
# ─────────────────────────────────────────────────────────────────────
def test_g13_at_most_one_chain_per_target_and_rerun_is_idempotent(
    tmp_path, revise_spy
):
    """G13 — (a) 同一輪裡**每個目標至多一條修訂鏈**；(b) 同一份圖重跑一次 ⇒
    不得對同一目標產出第二條鏈。"""
    # (a) 兩顆同維度 pattern 都合格（min_evidence=3 ⇒ 引擎門檻不足 ⇒ 分支 B）
    #     ⇒ 選擇器必須只回傳**一個**目標（每個目標至多一條鏈）。
    store = _build_store(
        tmp_path / "one",
        [
            _soul("s1", "命題甲"),
            _pattern("p1", "命題乙"),
            _pattern("p2", "命題丙"),
        ],
        [_edge("p1", "src-1", "ev-1"), _edge("p2", "src-2", "ev-2")],
    )
    out = _adapter.elevate_matured_patterns(store_dir=store, min_evidence=3)
    assert len(revise_spy.calls) == 1, "同一目標不得被修訂兩次"
    assert len(out) == 1
    chains = [n for n in _store_nodes(store) if n.get("parent_node_id") == "s1"]
    assert len(chains) == 1, f"同一目標產出了 {len(chains)} 條修訂鏈"

    # (b) 冪等：同一份圖重跑一次 ⇒ 不得再產修訂鏈（該 pattern 的證據已被消化）。
    revise_spy.calls.clear()
    store_d = _scenario_d(tmp_path / "idem")
    _adapter.elevate_matured_patterns(store_dir=store_d)
    assert len(revise_spy.calls) == 1
    revise_spy.calls.clear()
    before = _store_bytes(store_d)
    out2 = _adapter.elevate_matured_patterns(store_dir=store_d)
    assert revise_spy.calls == [], "重跑不得再對同一目標產出修訂鏈"
    assert out2 == []
    assert _store_bytes(store_d) == before, "重跑必須是冪等 no-op"
    depth2 = [n for n in _store_nodes(store_d) if n.get("lineage_depth") == 2]
    assert len(depth2) == 1


# ─────────────────────────────────────────────────────────────────────
# G14：fail-closed
# ─────────────────────────────────────────────────────────────────────
def test_g14_apply_revision_false_keeps_original_behavior(
    tmp_path, revise_spy
):
    """G14 — ``apply_revision()`` 回 ``False``（引擎門檻不足）⇒ 維持原行為
    （continue），**不得部分寫入**（不得留下半套節點／邊），且不得重試。"""
    store = _scenario_b(tmp_path / "elevation")
    before = _store_bytes(store)
    revise_spy.raise_exc = ValueError("engine threshold not met")

    out = _adapter.elevate_matured_patterns(store_dir=store)

    assert out == []
    assert len(revise_spy.calls) == 1, "門檻不足不得重試"
    assert _store_bytes(store) == before, "fail-closed：不得留下半套節點／邊"
    assert len(_store_nodes(store)) == 2, "不得留下半套節點（原始 2 顆）"
    assert len(_store_edges(store)) == 1, "不得留下半套邊（原始 1 條）"


# ─────────────────────────────────────────────────────────────────────
# 反vacuity：守衛筆數（14 條一支都不能少）
# ─────────────────────────────────────────────────────────────────────
def test_00_guard_count_is_exactly_fourteen():
    """G1～G14 —— 恰 14 筆；少一支就是「有守衛被刪掉而沒人發現」。"""
    import inspect as _inspect

    names = [
        name
        for name, obj in globals().items()
        if name.startswith("test_g")
        and callable(obj)
        and getattr(obj, "__module__", "") == __name__
    ]
    assert len(names) == 14, f"守衛筆數應為 14，實得 {len(names)}：{sorted(names)}"
    expected = {f"test_g{i}_" for i in range(1, 15)}
    assert {name[:8] for name in names} == {e[:8] for e in expected}
