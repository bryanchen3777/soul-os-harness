# tests/inner_life/test_epistemic_action.py
# SIM-EPIS-PIGGYBACK-1 ① — 自主求知行動（Internal Epistemic Action）的守衛。
#
# 受測模組：src/inner_life/epistemic_action.py（純決策層：觸發判定 ＋ 多路徑展開 ＋ 閉合判定）
#
# 結構核心是 **Branching（set-valued transition）**：一條張力 ⇒ 多條候選路徑 ⇒
# 其中一條**實際命中證據**才閉合。本檔把這件事釘死，並釘死三條紅線：
#   ① 觸發原料只能來自「缺口／張力」狀態空間（**不是**「想念值」）；
#   ② 閉合必須有**實際證據**（不硬編故事）；
#   ③ 全程**零打擾**（不碰任何對用戶的發訊路徑）＋ **0 LLM**。
#
# 🔴 本檔不使用 subprocess / os.spawn* / os.system / os.popen（R5 白名單大小鎖死 22），
#    也不出現任何硬寫的服務位址（R3 守衛）。
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path

import pytest

from src.inner_life import epistemic_action as ea
from src.inner_life import piggyback_queue as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src" / "inner_life" / "epistemic_action.py"


# ── AST 守衛工具（只看「程式碼」，不看註解與 docstring）────────────────────
def _code_tokens(source: str):
    """取出模組裡的**可執行** token：識別字 ＋ 非 docstring 的字串常值。

    為什麼排除 docstring：模組 docstring 會**說明**它不做什麼（例如「不呼叫
    ``_publish_agency_trigger``」）；守衛要驗的是**行為**，不是說明。
    """
    tree = ast.parse(source)
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstring_nodes.add(id(body[0].value))

    tokens = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.arg):
            tokens.append(node.arg)
        elif isinstance(node, ast.alias):
            tokens.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.append(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_nodes:
                tokens.append(node.value)
    return tokens


def _module_tokens() -> list:
    return _code_tokens(MODULE_PATH.read_text(encoding="utf-8"))


# ── 測試用的小工具 ────────────────────────────────────────────────────────
def _tension(**overrides) -> ea.Tension:
    base = dict(
        tension_id="epistemic_gap:test0001",
        kind=ea.TENSION_KIND_EPISTEMIC_GAP,
        agent_id="agent_rem",
        subject="發熱的東西",
        summary="還不認得「發熱的東西」這件事",
        tags=("發熱", "東西"),
        world_keys=("發熱的東西",),
        source=ea.SOURCE_EPISTEMIC_APPRAISAL,
        created_at=1000.0,
        utterance="主人買了一個會發熱的東西",
    )
    base.update(overrides)
    return ea.Tension(**base)


def _probe(hit: bool, detail: str = "命中") -> ea.PathEvidence:
    return ea.PathEvidence(ea.PATH_MEMORY_RECALL, hit, detail)


def _probes(hit: bool, detail: str = "命中") -> dict:
    return {
        ea.PATH_MEMORY_RECALL: lambda t: ea.PathEvidence(ea.PATH_MEMORY_RECALL, hit, detail),
        ea.PATH_WORLD_VERIFY: lambda t: ea.PathEvidence(ea.PATH_WORLD_VERIFY, hit, detail),
        ea.PATH_SELF_REASON: lambda t: ea.PathEvidence(ea.PATH_SELF_REASON, hit, detail),
    }


# ══ G-A1：多路徑展開（Branching）═════════════════════════════════════════
def test_expand_paths_has_at_least_two_distinct_branches():
    paths = ea.expand_paths(_tension())
    assert len(paths) >= 2, "同一張力必須展開成至少兩條路徑"
    kinds = [path.kind for path in paths]
    assert len(set(kinds)) == len(kinds), "不得是同一路徑的變體"
    assert set(kinds) == set(ea.PATH_KINDS)


def test_expand_paths_keeps_branch_count_even_without_inputs():
    """分支數 ≥ 2 是**結構事實**：即使沒有可查輸入，分支仍在（只是不可查）。"""
    tension = _tension(tags=(), world_keys=())
    paths = ea.expand_paths(tension)
    assert len(paths) >= 2
    assert len({p.kind for p in paths}) == len(paths)
    available = [p for p in paths if p.available]
    assert len(available) >= 1
    assert any(p.kind == ea.PATH_SELF_REASON and p.available for p in paths)


def test_expand_paths_availability_follows_available_inputs():
    paths = {p.kind: p for p in ea.expand_paths(_tension())}
    assert paths[ea.PATH_MEMORY_RECALL].available is True
    assert paths[ea.PATH_WORLD_VERIFY].available is True
    assert paths[ea.PATH_SELF_REASON].available is True


def test_expand_paths_is_deterministic():
    tension = _tension()
    first = [(p.kind, p.available, p.reason) for p in ea.expand_paths(tension)]
    second = [(p.kind, p.available, p.reason) for p in ea.expand_paths(tension)]
    assert first == second


# ══ G-A2：觸發判定只認「未閉合張力」══════════════════════════════════════
def test_should_act_is_true_only_when_tension_exists():
    assert ea.should_act([]) is False
    assert ea.should_act(None) is False
    assert ea.should_act([_tension()]) is True


# ══ G-A3：沒有證據 ⇒ 不閉合（不硬編故事）════════════════════════════════
def test_close_tension_without_evidence_does_not_close(tmp_path):
    tension = _tension()
    closure = ea.close_tension(
        tension,
        probes=_probes(False, "查不到"),
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
        now=1000,
    )
    assert closure.closed is False
    assert closure.event_id is None, "無證據不得產生閉合事件"
    assert closure.resolved_marked is False
    assert closure.enqueued is False
    assert pq.load_items("agent_rem", tmp_path / "queue") == []
    assert ea.load_open_tensions("agent_rem", tmp_path / "tensions") == []


def test_close_tension_uses_first_branch_that_hits(tmp_path):
    tension = _tension()
    seen = []

    def _memory(t):
        seen.append(ea.PATH_MEMORY_RECALL)
        return ea.PathEvidence(ea.PATH_MEMORY_RECALL, False, "翻記憶無命中")

    def _world(t):
        seen.append(ea.PATH_WORLD_VERIFY)
        return ea.PathEvidence(ea.PATH_WORLD_VERIFY, True, "查世界事件命中 1 筆")

    def _self(t):
        seen.append(ea.PATH_SELF_REASON)
        return ea.PathEvidence(ea.PATH_SELF_REASON, False, "未收束")

    closure = ea.close_tension(
        tension,
        probes={
            ea.PATH_MEMORY_RECALL: _memory,
            ea.PATH_WORLD_VERIFY: _world,
            ea.PATH_SELF_REASON: _self,
        },
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
        now=1000,
    )
    assert closure.closed is True
    assert closure.evidence.path_kind == ea.PATH_WORLD_VERIFY
    assert seen == [ea.PATH_MEMORY_RECALL, ea.PATH_WORLD_VERIFY], "命中即停，不需再問後面的分支"


# ══ G-A4：有證據 ⇒ 三件產出齊備 ══════════════════════════════════════════
def test_close_tension_produces_event_resolved_state_and_one_thought(tmp_path):
    tension = _tension()
    tension_dir = tmp_path / "tensions"
    queue_dir = tmp_path / "queue"

    ea.upsert_open_tension(tension, tension_dir)
    assert len(ea.load_open_tensions("agent_rem", tension_dir)) == 1

    closure = ea.close_tension(
        tension,
        probes=_probes(True, "翻記憶命中 3 筆"),
        tension_store_dir=tension_dir,
        queue_store_dir=queue_dir,
        now=1000,
    )
    assert closure.closed is True
    # 產出 1：事件
    assert closure.event_id and len(closure.event_id) == 32
    # 產出 2：resolved 狀態
    assert closure.resolved_marked is True
    assert ea.load_open_tensions("agent_rem", tension_dir) == []
    # 產出 3：1 個可掛載小念頭
    assert closure.enqueued is True
    items = pq.load_items("agent_rem", queue_dir)
    assert len(items) == 1
    assert items[0].source == pq.PIGGYBACK_SOURCE_CLOSURE
    assert "發熱" in items[0].tags


# ══ G-A5：事件走既有 writer ＋ submission_gate 通道 ═══════════════════════
def test_closure_event_goes_through_existing_writer_and_gate(tmp_path):
    from src.inner_life.trace import NarrativeTraceWriter
    from src.inner_life.writer import InnerLifeWriter

    trace_path = tmp_path / "trace.jsonl"
    writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter(trace_path))

    submitted = []

    class _SpyGate:
        def submit(self, event_id, *args, **kwargs):
            submitted.append(event_id)
            return []

    closure = ea.close_tension(
        _tension(),
        probes=_probes(True, "翻記憶命中 3 筆"),
        writer=writer,
        gate=_SpyGate(),
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
        now=1000,
    )
    assert closure.closed is True
    assert closure.event_id in submitted, "閉合事件必須走既有 submission_gate 通道"

    # shadow log（既有 NarrativeTraceWriter 通道）裡有一筆
    lines = [line for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event_id"] == closure.event_id
    assert record["provenance"]["trigger_type"] == "system", "必須是合法 producer trigger"


def test_closure_event_trigger_type_is_a_valid_producer_trigger():
    from src.inner_life.submission_gate import VALID_PRODUCER_TRIGGER_TYPES

    assert "system" in VALID_PRODUCER_TRIGGER_TYPES


# ══ G-A6：觸發原料**不是**「想念值」═══════════════════════════════════════
def test_module_never_reads_the_longing_value():
    """🔴 守衛 9 的結構性保證：模組**完全沒有**「想念」通道的程式碼 token。"""
    offenders = [t for t in _module_tokens() if "longing" in t.lower()]
    assert offenders == [], f"自主求知行動不得讀取「想念值」：{offenders}"


def test_module_never_references_the_longing_threshold():
    offenders = [t for t in _module_tokens() if "longing_threshold" in t.lower()]
    assert offenders == [], f"本票不得修改／引用想念門檻：{offenders}"


def test_module_never_touches_scheduler():
    offenders = [t for t in _module_tokens() if "scheduler" in t.lower()]
    assert offenders == [], f"本機制不得依賴/修改 scheduler：{offenders}"


# ══ G-A7：零打擾（不碰任何對用戶的發訊路徑）══════════════════════════════
def test_module_never_touches_any_publish_path():
    banned = ("publish_agency_trigger", "agency_trigger", "proactive_dm", "publish")
    offenders = [
        t
        for t in _module_tokens()
        if any(token in t.lower() for token in banned)
    ]
    assert offenders == [], f"自主求知行動不得觸碰發訊路徑：{offenders}"


def test_module_never_lowers_a_threshold():
    """模組不得引入任何門檻常數（門檻是既有 frozen 面）。"""
    offenders = [t for t in _module_tokens() if "threshold" in t.lower()]
    assert offenders == [], f"本機制不得引入／降低門檻：{offenders}"


# ══ G-A8：0 LLM ═══════════════════════════════════════════════════════════
def test_module_makes_no_llm_calls():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    banned_modules = {"openai", "anthropic", "litellm", "httpx", "requests", "aiohttp"}
    banned_calls = {"complete", "acomplete", "chat", "chat_completion", "invoke", "generate"}

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0].lower())

    assert not (imported & banned_modules), f"不得引入 LLM/網路客戶端：{imported & banned_modules}"

    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                called.add(func.attr.lower())
            elif isinstance(func, ast.Name):
                called.add(func.id.lower())
    assert not (called & banned_calls), f"不得呼叫 LLM 介面：{called & banned_calls}"


# ══ G-A9：漂移守衛（鏡像上游不得漂移）════════════════════════════════════
def test_bond_snapshot_reader_matches_upstream_reader(tmp_path, monkeypatch):
    """本模組的 relationships.json 讀取必須與上游 ``_read_bond_evidence`` 一致。"""
    from src.soul import temporal_phenomenology as tp

    cases = [
        {"impression_tags": ["貼心"], "relational_band": "stranger", "interaction_count": 0},
        {"impression_tags": [], "relational_band": "known", "interaction_count": 0},
        {"impression_tags": [], "relational_band": "close", "interaction_count": 99},
        {"impression_tags": [], "relational_band": "stranger", "interaction_count": 12,
         "last_interaction_at": "2026-10-01T00:00:00+00:00"},
        {"impression_tags": [], "relational_band": "stranger", "interaction_count": 12,
         "last_interaction_at": "not-a-timestamp"},
    ]
    for entry in cases:
        rel_dir = tmp_path / "soul" / "agent_rem"
        rel_dir.mkdir(parents=True, exist_ok=True)
        (rel_dir / "relationships.json").write_text(
            json.dumps({"others": {"user_bryan": entry}}), encoding="utf-8"
        )
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        from src.paths import reset_data_root

        reset_data_root()
        upstream = tp._read_bond_evidence("agent_rem")
        mine = ea.read_bond_snapshot("agent_rem")
        assert (upstream is None) == (mine is None), f"存在性不一致：{entry}"
        if upstream is not None:
            assert upstream.eligible == mine.eligible, f"eligible 不一致：{entry}"
            assert upstream.basis == mine.basis, f"basis 不一致：{entry}"


def test_non_stranger_bands_match_upstream():
    from src.soul import temporal_phenomenology as tp

    assert ea._NON_STRANGER_BANDS == tp._NON_STRANGER_BANDS


# ══ G-A10：7 天語意與三態現象學一致（只走公開 API）═══════════════════════
def test_piggyback_ttl_matches_temporal_phenomenology_seven_day_boundary():
    """掛載槽的 7 天 TTL 與「牵挂 → 释然」的 7 天邊界是同一個語意。

    ⚠️ 本測試**只**用上游**公開**的 ``classify_temporal_state`` 實測邊界，
    **不引用**上游的私有常數（工單 §3.2 明文）。
    """
    from src.soul.temporal_phenomenology import (
        STATE_RESOLVED,
        STATE_TENSION,
        BondEvidence,
        classify_temporal_state,
    )

    bond = BondEvidence(eligible=True, basis="P1_impression_tags")
    now = 1_000_000
    last = now - (7 * 24 * 3600) + 1
    assert classify_temporal_state(last, now, bond) == STATE_TENSION
    last = now - (7 * 24 * 3600)
    assert classify_temporal_state(last, now, bond) == STATE_RESOLVED
    assert pq.PIGGYBACK_TTL_SECONDS == 7 * 24 * 3600


# ══ G-A11：張力來源只有兩個既有通道 ═════════════════════════════════════
def test_collect_tensions_finds_epistemic_gap_from_appraisal():
    """通道 1：``epistemic_appraisal`` 判定的未閉合資訊缺口。"""
    tensions = ea.collect_tensions(
        "agent_rem", utterance="主人買了一個會發熱的東西", now=1000
    )
    kinds = [t.kind for t in tensions]
    assert ea.TENSION_KIND_EPISTEMIC_GAP in kinds
    gap = next(t for t in tensions if t.kind == ea.TENSION_KIND_EPISTEMIC_GAP)
    assert gap.source == ea.SOURCE_EPISTEMIC_APPRAISAL
    assert gap.tags, "張力必須帶可查的詞元"
    assert len(ea.expand_paths(gap)) >= 2


def test_collect_tensions_finds_relational_tension_from_temporal_state(tmp_path, monkeypatch):
    """通道 2：``temporal_phenomenology`` 的張力／牵挂狀態。"""
    from src.paths import reset_data_root

    rel_dir = tmp_path / "soul" / "agent_rem"
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "relationships.json").write_text(
        json.dumps(
            {"others": {"user_bryan": {"impression_tags": ["貼心"], "relational_band": "known"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()

    now = 1_700_000_000
    tensions = ea.collect_tensions(
        "agent_rem", now=now, last_interaction_ts=now - 2 * 24 * 3600
    )
    relational = [t for t in tensions if t.kind == ea.TENSION_KIND_RELATIONAL]
    assert relational, "兩天未互動 ＋ 有羈絆證據 ⇒ 應有牵挂張力"
    tension = relational[0]
    assert tension.source == ea.SOURCE_TEMPORAL_PHENOMENOLOGY
    assert "bryan" in [tag.lower() for tag in tension.tags]
    assert len(ea.expand_paths(tension)) >= 2


def test_collect_tensions_is_empty_without_tension_sources():
    tensions = ea.collect_tensions("agent_rem", utterance="今天天氣不錯", now=1000)
    assert tensions == []


# ══ G-A12：背景行動端到端（假時鐘、記憶副本）═════════════════════════════
def _write_memory(memory_dir: Path, agent_id: str, content: str, tags) -> None:
    from src.memory.v1.schema import Memory
    from src.memory.v1.store import V1Store

    store = V1Store(memory_dir, agent_id)
    store.add(
        Memory(
            memory_id=str(uuid.uuid4()),
            agent_id=agent_id,
            content=content,
            tags=list(tags),
            created_at=1000.0,
        )
    )


def test_run_background_action_closes_gap_when_memory_actually_hits(tmp_path):
    memory_dir = tmp_path / "memory"
    _write_memory(memory_dir, "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    closures = ea.run_background_action(
        "agent_rem",
        utterance="主人買了一個會發熱的東西",
        now=1000,
        memory_dir=memory_dir,
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
    )
    closed = [c for c in closures if c.closed]
    assert len(closed) == 1
    assert closed[0].evidence.path_kind == ea.PATH_MEMORY_RECALL
    assert closed[0].event_id
    assert pq.load_items("agent_rem", tmp_path / "queue")


def test_run_background_action_leaves_gap_open_without_evidence(tmp_path):
    """沒有任何證據 ⇒ 張力維持（不硬閉合、不產生事件）。"""
    closures = ea.run_background_action(
        "agent_rem",
        utterance="主人買了一個會發熱的東西",
        now=1000,
        memory_dir=tmp_path / "memory",
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
    )
    assert closures, "有張力就會嘗試（觸發判定為 True）"
    assert all(c.closed is False for c in closures)
    assert all(c.event_id is None for c in closures)
    assert pq.load_items("agent_rem", tmp_path / "queue") == []
    assert len(ea.load_open_tensions("agent_rem", tmp_path / "tensions")) == 1


def test_self_reason_closes_relational_tension_after_the_seven_day_window(
    tmp_path, monkeypatch
):
    """SELF_REASON：張力隨時間自行消退（牵挂 → 释然）⇒ 閉合。"""
    from src.paths import reset_data_root

    rel_dir = tmp_path / "soul" / "agent_rem"
    rel_dir.mkdir(parents=True, exist_ok=True)
    (rel_dir / "relationships.json").write_text(
        json.dumps(
            {"others": {"user_bryan": {"impression_tags": ["貼心"], "relational_band": "known"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()

    now = 1_700_000_000
    last = now - 2 * 24 * 3600
    tension_dir = tmp_path / "tensions"

    first = ea.run_background_action(
        "agent_rem",
        now=now,
        last_interaction_ts=last,
        memory_dir=tmp_path / "memory",
        tension_store_dir=tension_dir,
        queue_store_dir=tmp_path / "queue",
    )
    assert all(c.closed is False for c in first), "兩天前還在牵挂，不該閉合"
    assert len(ea.load_open_tensions("agent_rem", tension_dir)) == 1

    # 時鐘推進到 8 天後 ⇒ 三態已轉「释然」⇒ SELF_REASON 命中
    later = now + 6 * 24 * 3600
    second = ea.run_background_action(
        "agent_rem",
        now=later,
        last_interaction_ts=last,
        memory_dir=tmp_path / "memory",
        tension_store_dir=tension_dir,
        queue_store_dir=tmp_path / "queue",
    )
    closed = [c for c in second if c.closed]
    assert len(closed) == 1
    assert closed[0].evidence.path_kind == ea.PATH_SELF_REASON
    assert ea.load_open_tensions("agent_rem", tension_dir) == []


# ══ G-A13：失敗隔離 ═══════════════════════════════════════════════════════
def test_run_background_action_is_fail_silent_on_probe_exception(tmp_path):
    def _boom(tension):
        raise RuntimeError("probe exploded")

    closures = ea.run_background_action(
        "agent_rem",
        utterance="主人買了一個會發熱的東西",
        now=1000,
        memory_dir=tmp_path / "memory",
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
        probes={
            ea.PATH_MEMORY_RECALL: _boom,
            ea.PATH_WORLD_VERIFY: _boom,
            ea.PATH_SELF_REASON: _boom,
        },
    )
    assert all(c.closed is False for c in closures)


def test_close_tension_with_bad_probe_returning_none(tmp_path):
    closure = ea.close_tension(
        _tension(),
        probes={ea.PATH_MEMORY_RECALL: lambda t: None},
        tension_store_dir=tmp_path / "tensions",
        queue_store_dir=tmp_path / "queue",
        now=1000,
    )
    assert closure.closed is False
