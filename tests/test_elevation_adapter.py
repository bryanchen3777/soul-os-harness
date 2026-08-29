"""
soul-elevation 整合进 Soul OS（adapter seam）— 测试

工单：把独立 repo `soul-elevation` 经 `src/inner_life/elevation_adapter.py` 接入，
映射 InnerLifeEvent + Memory → ElevationInput，喂给 InternalizingEngine 产出升华节点，
结果存 data/elevation/。adapter 是只读消费者，不改任何 frozen contract。

测试分节：
  A. InnerLifeEvent → ElevationInput 映射正确
  B. Memory（v1 / SAGE）→ ElevationInput 映射正确
  C. run_elevation() 喂给 InternalizingEngine 产出节点
  D. 升华结果存储到 data/elevation/（trace + nodes + edges）
  E. 触发点接线（InnerLifeEvent 写入后调用，不改写入逻辑）
  F. 失败隔离（升华失败不阻断写路径）
  G. frozen contract 只读红线（adapter 不引用 InnerLifeWriter 写入路径）
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.inner_life import InnerLifeWriter, Provenance
from src.inner_life.elevation_adapter import (
    ELEVATION_DIR_NAME,
    EDGES_FILENAME,
    NODES_FILENAME,
    TRACE_FILENAME,
    ElevationObserver,
    inner_life_event_to_input,
    run_elevation,
    sage_fact_to_input,
    v1_memory_to_input,
)
from src.memory.sage.models import Fact
from src.memory.v1.schema import Memory

_ADAPTER_SRC = Path(__file__).resolve().parents[1] / "src" / "inner_life" / "elevation_adapter.py"


# ── fixtures ─────────────────────────────────────────────────────────


def _make_event(trigger_type: str = "diary:night", extras: dict | None = None) -> "InnerLifeWriter":
    writer = InnerLifeWriter()
    event = writer.create_event(
        provenance=Provenance(
            trigger_type=trigger_type,
            actor_id="agent_rem",
            source_system="narrative",
            extras=extras or {},
        ),
    )
    return event


def _make_memory(**overrides) -> Memory:
    data = dict(
        memory_id="mem-0001",
        agent_id="agent_rem",
        content="我重视自由",
        tags=["preference"],
        created_at=1756000000.0,
        category="preference",
        confidence=0.9,
        inner_life_event_id=None,
    )
    data.update(overrides)
    return Memory(**data)


def _make_fact(**overrides) -> Fact:
    data = dict(
        subject="user",
        predicate="likes",
        object="freedom",
        timestamp=1756000000.0,
        confidence=0.8,
        source="user",
        fact_id="fact-0001",
        source_pair="bryan:agent_rem",
        inner_life_event_id=None,
    )
    data.update(overrides)
    return Fact(**data)


# ── A. InnerLifeEvent → ElevationInput ───────────────────────────────


def test_inner_life_event_to_input_mapping():
    event = _make_event("diary:night", extras={"world_type": "news_event"})
    inp = inner_life_event_to_input(event)

    assert inp.event_type == "diary:night"
    assert inp.source_id == event.event_id
    assert inp.source_type == "inner_life_event"
    assert inp.timestamp == event.ts
    assert inp.provenance["trigger_type"] == "diary:night"
    assert inp.provenance["source_system"] == "narrative"
    assert inp.provenance["agent_id"] == "agent_rem"  # actor_id 落到 agent_id
    assert inp.provenance["inner_life_event_id"] == event.event_id
    assert inp.provenance["world_type"] == "news_event"  # extras 并入 provenance
    assert inp.content.startswith("diary:night")


def test_inner_life_event_to_input_agent_override():
    event = _make_event()
    inp = inner_life_event_to_input(event, agent_id="agent_yua")
    assert inp.provenance["agent_id"] == "agent_yua"


# ── B. Memory（v1 / SAGE）→ ElevationInput ───────────────────────────


def test_v1_memory_to_input_mapping():
    mem = _make_memory()
    inp = v1_memory_to_input(mem)

    assert inp.source_type == "v1_memory"
    assert inp.source_id == "mem-0001"
    assert inp.content == "我重视自由"
    assert inp.event_type == "memory_fact"
    # created_at=1756000000.0 → ISO 8601 UTC
    expected_ts = datetime.fromtimestamp(1756000000.0, tz=timezone.utc).isoformat()
    assert inp.timestamp == expected_ts
    assert inp.provenance["agent_id"] == "agent_rem"
    assert inp.provenance["category"] == "preference"
    assert inp.provenance["confidence"] == 0.9
    assert inp.provenance["tags"] == ["preference"]


def test_sage_fact_to_input_mapping():
    fact = _make_fact()
    inp = sage_fact_to_input(fact)

    assert inp.source_type == "sage_fact"
    assert inp.source_id == "fact-0001"
    assert inp.content == "user likes freedom"
    assert inp.event_type == "memory_fact"
    expected_ts = datetime.fromtimestamp(1756000000.0, tz=timezone.utc).isoformat()
    assert inp.timestamp == expected_ts
    assert inp.provenance["agent_id"] == "agent_rem"  # 从 source_pair "bryan:agent_rem"
    assert inp.provenance["confidence"] == 0.8


# ── C. run_elevation() 产出节点 ──────────────────────────────────────


def test_run_elevation_produces_nodes(tmp_path):
    event = _make_event("diary:night")
    mem = _make_memory()

    nodes = run_elevation(event, [mem], store_dir=tmp_path / "elevation")

    assert isinstance(nodes, list)
    assert len(nodes) >= 2  # 事件 1 个 + memory 1 个
    for n in nodes:
        assert n.node_type in {"belief", "value", "trait", "essence"}
        assert 0.0 <= n.confidence <= 1.0
        assert n.node_id  # 32-hex 非空


def test_run_elevation_empty_memory_facts_still_produces_event_node(tmp_path):
    event = _make_event("diary:night")
    nodes = run_elevation(event, [], store_dir=tmp_path / "elevation")
    assert len(nodes) == 1  # 只有事件自身产出的节点


# ── D. 存储到 data/elevation/ ────────────────────────────────────────


def test_run_elevation_stores_to_elevation_dir(tmp_path):
    event = _make_event("diary:night")
    mem = _make_memory()
    store_dir = tmp_path / "elevation"

    nodes = run_elevation(event, [mem], store_dir=store_dir)

    trace_file = store_dir / TRACE_FILENAME
    nodes_file = store_dir / NODES_FILENAME
    edges_file = store_dir / EDGES_FILENAME

    assert trace_file.exists()
    assert nodes_file.exists()
    assert edges_file.exists()

    # trace 记录含 node_created 审计事件（soul-elevation 自有 store）
    trace_records = [json.loads(l) for l in trace_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert any(r.get("event_type") == "node_created" for r in trace_records)

    # 节点文件含完整节点（含 content，可回查升华命题）
    node_records = [json.loads(l) for l in nodes_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(node_records) == len(nodes)
    assert all("content" in r and "node_type" in r and "confidence" in r for r in node_records)

    # 证据边文件含 source_id 回指（原文可回查）
    edge_records = [json.loads(l) for l in edges_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert edge_records
    assert all("source_id" in r and "node_id" in r for r in edge_records)


def test_default_store_dir_resolves_to_data_root_elevation(tmp_path, monkeypatch):
    from src import paths

    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    paths.reset_data_root()

    event = _make_event()
    nodes = run_elevation(event, [], store_dir=None)

    assert (tmp_path / "data" / ELEVATION_DIR_NAME / TRACE_FILENAME).exists()
    assert len(nodes) == 1
    paths.reset_data_root()


# ── E. 触发点接线（InnerLifeEvent 写入后调用）─────────────────────────


def test_elevation_observer_wiring_after_write(tmp_path):
    """证明「写入 → 写入后调用 run_elevation → 产出节点」的触发点接线。"""
    writer = InnerLifeWriter()
    event = writer.create_event(
        provenance=Provenance(
            trigger_type="diary:night",
            actor_id="agent_rem",
            source_system="narrative",
        ),
    )

    # 写入之后：观察者接在写路径之后（不改 InnerLifeWriter 写入逻辑）
    observer = ElevationObserver(store_dir=tmp_path / "elevation")
    nodes = observer.on_event_written(event, [_make_memory()])

    assert len(nodes) >= 2
    assert (tmp_path / "elevation" / TRACE_FILENAME).exists()


def test_elevation_observer_disabled_is_noop(tmp_path):
    event = _make_event()
    observer = ElevationObserver(enabled=False, store_dir=tmp_path / "elevation")
    nodes = observer.on_event_written(event, [])
    assert nodes == []
    assert not (tmp_path / "elevation" / TRACE_FILENAME).exists()


# ── F. 失败隔离 ─────────────────────────────────────────────────────


def test_run_elevation_failure_isolation(tmp_path):
    """不可识别对象（非 InnerLifeEvent/Memory/Fact）→ 不 raise，返回 []。"""
    event = _make_event()
    nodes = run_elevation(event, [42], store_dir=tmp_path / "elevation")
    assert nodes == []


def test_observer_failure_isolation(tmp_path):
    class _BoomLLM:
        def classify(self, content, provenance, prior_node_type):
            raise RuntimeError("boom")

    event = _make_event()
    observer = ElevationObserver(llm=_BoomLLM(), store_dir=tmp_path / "elevation")
    nodes = observer.on_event_written(event, [])
    assert nodes == []


# ── G. frozen contract 只读红线 ──────────────────────────────────────

import ast


def _adapter_ast() -> ast.Module:
    return ast.parse(_ADAPTER_SRC.read_text(encoding="utf-8"))


def _assert_no_import(tree: ast.Module, forbidden: set[str]) -> None:
    """断言 adapter 未 import 任何 forbidden 名称（精确到 AST，不看 docstring）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert not (imported & forbidden), (
                f"adapter 不得 import frozen 写入路径符号：{imported & forbidden}"
            )


def _assert_no_method_call(tree: ast.Module, forbidden: set[str]) -> None:
    """断言 adapter 未调用任何 forbidden 方法名（精确到 AST，不看 docstring）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in forbidden:
                raise AssertionError(f"adapter 不得调用 {func.attr}()")
            if isinstance(func, ast.Name) and func.id in forbidden:
                raise AssertionError(f"adapter 不得调用 {func.id}()")


def test_adapter_does_not_import_or_call_inner_life_writer():
    """adapter 是只读消费者，不得 import/调用 InnerLifeWriter 写入路径。"""
    tree = _adapter_ast()
    _assert_no_import(tree, {"InnerLifeWriter", "InnerLifeWriterStats"})
    _assert_no_method_call(tree, {"create_event", "_append_trace"})


def test_adapter_does_not_reference_sage_write_path():
    """adapter 不得 import/触发 SAGE 写入逻辑（MemoryEvolution / apply_correction / add_fact）。"""
    tree = _adapter_ast()
    _assert_no_import(tree, {"MemoryEvolution", "MemoryWriter"})
    _assert_no_method_call(tree, {"apply_correction", "add_fact", "update_weight", "remove_fact"})
