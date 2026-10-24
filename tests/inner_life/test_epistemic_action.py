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
#
# 🔴 BUG-EPISTEMIC-PARTIALLY-RESOLVED（2026-10-23）：本檔新增**載入點覆寫**機制
#    （``MUTATION_MODULE_ENV``）供變異實驗使用 —— 它**只接受 ``%TEMP%`` 底下的
#    路徑**（repo 檔本身一律 ``RuntimeError`` 拒絕）⇒ 變異實驗永遠只能改副本，
#    repo 檔全程零寫入。
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from src.inner_life import piggyback_queue as pq

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 變異 harness 的**載入點覆寫**環境變數（🔴 只接受 ``%TEMP%`` 底下的路徑）。
#:
#: 用途：變異實驗（M1～M3）必須「改副本、repo 檔全程不寫入」⇒ harness 把
#: ``src/inner_life/epistemic_action.py`` 複製到 ``%TEMP%`` 下、就地改壞，
#: 再用這個變數把本檔的受測模組指到那份副本上。
MUTATION_MODULE_ENV = "SOUL_OS_EPISTEMIC_ACTION_MODULE_OVERRIDE"

#: 受測模組的 logger 名（未知狀態告警的辨識依據）。
MODULE_LOGGER_NAME = "soul_os.inner_life.epistemic_action"

#: 讀取端遇到**未知狀態**時的告警特徵字串（守衛據此判斷「有沒有被當成未知值」）。
UNKNOWN_STATUS_WARNING_MARK = "未知張力狀態"


def _resolve_override_path(raw: str) -> Path:
    """把載入點覆寫路徑解析成絕對路徑；**不在 ``%TEMP%`` 底下 ⇒ 一律拒絕**。"""
    target = Path(raw).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if not target.is_relative_to(temp_root):
        raise RuntimeError(
            f"載入點覆寫只接受 %TEMP% 底下的路徑（temp_root={temp_root}），收到：{target}"
        )
    return target


def _load_epistemic_action():
    """載入受測模組。

    預設＝repo 正式檔；變異 harness 可用 ``MUTATION_MODULE_ENV`` 指向 ``%TEMP%``
    底下的一份**改壞的副本**（repo 檔全程不寫入）。
    """
    raw = os.environ.get(MUTATION_MODULE_ENV)
    if not raw:
        from src.inner_life import epistemic_action

        return epistemic_action
    target = _resolve_override_path(raw)
    spec = importlib.util.spec_from_file_location("src.inner_life.epistemic_action", target)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"無法由 {target} 建立 module spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules["src.inner_life.epistemic_action"] = module
    spec.loader.exec_module(module)
    return module


ea = _load_epistemic_action()

_OVERRIDE_RAW = os.environ.get(MUTATION_MODULE_ENV)
MODULE_PATH = (
    _resolve_override_path(_OVERRIDE_RAW)
    if _OVERRIDE_RAW
    else REPO_ROOT / "src" / "inner_life" / "epistemic_action.py"
)


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


# ══ G-P3：張力狀態是**封閉集合**（BUG-EPISTEMIC-PARTIALLY-RESOLVED）════════
#
# 背景：CAMPAIGN-B-P2（Phase 2）已在沙盒使用 ``status="partially_resolved"``。
# Phase 3／戰役二一旦把這種事件寫進生產，舊的**讀取端**會有三個缺陷：
#   A（fail-open 靜默吞噬）：集合外的 status 被默默改成 ``resolved``
#      ⇒ 未消解張力被宣告消解，**無錯誤、無警告**；
#   B（掉落佇列）：``load_open_tensions`` 只收 ``status == "open"``
#      ⇒ ``partially_resolved`` 從未閉合佇列消失；
#   C（寫回降級）：``save_tension_records`` 只認 ``status == "resolved"``
#      ⇒ ``partially_resolved`` 往返後被寫成 ``open``。
#
# 🔴 合成 fixture 必須與**生產事件同形**（欄位齊備，含 ``ts``）：2026-10-23 實測，
#    新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺。
def _production_shaped_event(
    *,
    agent_id: str = "agent_rem",
    tension_id: str = "drive_tension:care_001",
    status: str = "partially_resolved",
    decided: bool = False,
    ts: float = 1_700_000_000.0,
) -> dict:
    """一筆**與生產同形**的張力事件（頂層欄位齊備，含 ``ts``）。

    形狀對齊 ``src/inner_life/tension_closure_loop.py::_tension_event``
    （CAMPAIGN-B-P2 沙盒寫出的那一種）：頂層＝``TENSION_EVENT_FIELDS``；payload 帶
    ``partial_resolution``／``remaining_gap_count``／``decided``／``reason``。
    """
    payload = {
        "agent_id": agent_id,
        "bond": {"eligible": False, "basis": "P4_none", "impression_tags": []},
        "created_at": ts,
        "kind": "drive_tension",
        "last_interaction_ts": 1_699_000_000,
        "session_context": "",
        "source": "drive_scoring",
        "subject": "care",
        "summary": "care 驅動張力（資訊缺口 3 筆／未決矛盾 0 筆）",
        "tags": [],
        "tension_id": tension_id,
        "utterance": "",
        "world_keys": [],
        "drive_snapshot": {
            "drive_code": "care",
            "temporal_state": "tension",
            "decay_bucket": "recent",
            "info_gap_count": 3,
            "unresolved_count": 0,
            "source_rows": [],
        },
    }
    if status == "open":
        kind = ea.TENSION_EVENT_OPENED
    else:
        kind = ea.TENSION_EVENT_RESOLVED
        payload["resolved_at"] = ts
        payload["partial_resolution"] = 1.0 if decided else 0.5
        payload["remaining_gap_count"] = 0 if decided else 2
        payload["decided"] = decided
        payload["reason"] = "no_open_info_gap" if decided else "partial_gap"
    return {
        "ts": ts,
        "agent_id": agent_id,
        "tension_id": tension_id,
        "kind": kind,
        "subject": "care",
        "status": status,
        "payload": payload,
    }


def _write_events(store_dir: Path, events) -> Path:
    """把事件寫成 append-only 承載檔（**與生產同一個檔名**、同一種寫法）。"""
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / ea.TENSION_STORE_EVENTS_FILENAME
    blob = "".join(
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n" for event in events
    )
    path.write_text(blob, encoding="utf-8")
    return path


def _read_events(store_dir: Path) -> list:
    path = store_dir / ea.TENSION_STORE_EVENTS_FILENAME
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── P3-T1：部分消解的張力**留在未閉合佇列**（缺陷 B）─────────────────────
def test_p3_t1_partially_resolved_stays_in_the_open_queue(tmp_path):
    """P3-T1 — ``partially_resolved`` 進未閉合佇列。

    缺陷 B：``load_open_tensions`` 只收 ``status == "open"`` ⇒ 部分消解的張力會從
    未閉合清單**消失**（未消解的張力永久蒸發）。
    """
    store = tmp_path / "tensions"
    _write_events(store, [_production_shaped_event()])

    open_ids = [t.tension_id for t in ea.load_open_tensions("agent_rem", store)]
    assert "drive_tension:care_001" in open_ids, (
        "部分消解仍未閉合 ⇒ 必須留在未閉合佇列裡（缺陷 B：掉落佇列＝未消解張力蒸發）"
    )


# ── P3-T2：讀取端**保留** partially_resolved（缺陷 A）─────────────────────
def test_p3_t2_read_preserves_partially_resolved_status(tmp_path, caplog):
    """P3-T2 — 讀回來的 status 必須仍是 ``partially_resolved``。

    兩條斷言：
      ① **原值保留**（不得被改成 ``resolved``）——缺陷 A 的直接反證；
      ② 它是**已知的**狀態（封閉集合內）⇒ 不得被讀成「未知狀態」而告警。
         （② 是 M3 變異「從 ``TENSION_STATUSES`` 移除 ``partially_resolved``」的
         守門：移除之後它就變成未知值，每一筆真實事件都會噴告警。）
    """
    store = tmp_path / "tensions"
    _write_events(store, [_production_shaped_event()])

    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER_NAME):
        records = ea.load_tension_records("agent_rem", store)

    assert len(records) == 1, f"應讀回 1 筆，實得 {len(records)} 筆"
    assert records[0]["status"] == "partially_resolved", (
        "讀取端必須**原樣保留** partially_resolved（不得被靜默改寫成 resolved）"
    )
    unknown_warnings = [
        record
        for record in caplog.records
        if UNKNOWN_STATUS_WARNING_MARK in record.getMessage()
    ]
    assert unknown_warnings == [], (
        "partially_resolved 屬於封閉集合 ⇒ 不得被讀成「未知狀態」而告警："
        f"{[r.getMessage() for r in unknown_warnings]}"
    )


# ── P3-T3：未知 status **不被當成已消解**（缺陷 A）────────────────────────
def test_p3_t3_unknown_status_is_preserved_and_warned(tmp_path, caplog):
    """P3-T3 — 塞一個 ``"bogus_status"`` ⇒ 讀回來仍是它，而且**必須告警**。

    缺陷 A：舊碼把集合外的 status 默默改成 ``resolved`` ⇒ 未消解張力被宣告消解，
    **無錯誤、無警告**。修法是 fail-closed：**保留原值 ＋ 告警**。
    """
    store = tmp_path / "tensions"
    _write_events(store, [_production_shaped_event(status="bogus_status")])

    with caplog.at_level(logging.WARNING, logger=MODULE_LOGGER_NAME):
        records = ea.load_tension_records("agent_rem", store)

    assert len(records) == 1, "未知狀態的紀錄**不得被丟棄**（必須保留原值）"
    record = records[0]
    assert record["status"] == "bogus_status", (
        "未知狀態必須**保留原值**（fail-closed）：不得被靜默改寫成 resolved"
    )
    assert record["status"] != ea.TENSION_STATUS_RESOLVED, (
        "帶著 resolved_at 戳記也不得被當成『已消解』語意"
    )
    warnings = [
        r for r in caplog.records if UNKNOWN_STATUS_WARNING_MARK in r.getMessage()
    ]
    assert warnings, "未知狀態必須**告警**（可觀測）——不得靜默吞掉"


# ── P3-T4：寫回**不降級**（缺陷 C）────────────────────────────────────────
def test_p3_t4_save_round_trip_does_not_downgrade_partially_resolved(tmp_path):
    """P3-T4 — ``partially_resolved`` 往返一次後**不得**變成 ``open``。

    缺陷 C：``save_tension_records`` 只認 ``status == "resolved"`` ⇒ 部分消解的
    紀錄在寫回時被降級成 ``open``（部分消解的事實被抹平）。
    """
    source = tmp_path / "source"
    _write_events(source, [_production_shaped_event()])
    records = ea.load_tension_records("agent_rem", source)
    assert records[0]["status"] == "partially_resolved"

    target = tmp_path / "target"
    assert ea.save_tension_records("agent_rem", records, target) is True

    reread = ea.load_tension_records("agent_rem", target)
    assert len(reread) == 1, f"應讀回 1 筆，實得 {len(reread)} 筆"
    assert reread[0]["status"] == "partially_resolved", (
        "寫回不得把 partially_resolved 降級成 open（缺陷 C）"
    )

    # 事件層級也要對：kind 仍是既有的 kind、頂層欄位形狀不變、status 原樣。
    events = _read_events(target)
    assert len(events) == 1, f"應寫出 1 筆事件，實得 {len(events)} 筆"
    assert events[0]["kind"] == ea.TENSION_EVENT_RESOLVED, (
        "事件**型別**不得變（封閉集合）"
    )
    assert events[0]["status"] == "partially_resolved"
    assert set(events[0].keys()) == set(ea.TENSION_EVENT_FIELDS), (
        f"事件欄位形狀必須封閉：{sorted(events[0].keys())}"
    )


# ── P3-T5：狀態是封閉集合；事件**型別**不變 ──────────────────────────────
def test_p3_t5_tension_statuses_is_a_closed_set():
    """P3-T5 — ``TENSION_STATUSES`` 是封閉集合；``TENSION_EVENT_KINDS`` 一個字都不變。"""
    assert ea.TENSION_STATUSES == ("open", "resolved", "partially_resolved"), (
        "狀態必須是封閉集合（讀取端才有辦法辨認『未知值』）"
    )
    assert ea.TENSION_STATUS_OPEN == "open"
    assert ea.TENSION_STATUS_RESOLVED == "resolved"
    assert ea.TENSION_STATUS_PARTIALLY_RESOLVED == "partially_resolved"
    # 🔴 事件**型別**的封閉集合不得變動（本次只擴充「狀態」值）。
    assert ea.TENSION_EVENT_KINDS == (
        "tension_opened",
        "tension_resolved",
        "tension_thought_enqueued",
    )
    assert set(ea.TENSION_EVENT_FIELDS) == {
        "ts",
        "agent_id",
        "tension_id",
        "kind",
        "subject",
        "status",
        "payload",
    }


# ── P3-T6：既有語意零回歸 ────────────────────────────────────────────────
def test_p3_t6_existing_open_and_resolved_semantics_are_unchanged(tmp_path):
    """P3-T6 — ``open``／``resolved`` 兩種既有狀態的行為**逐字不變**。"""
    store = tmp_path / "tensions"
    _write_events(
        store,
        [
            _production_shaped_event(tension_id="t-open", status="open"),
            _production_shaped_event(
                tension_id="t-resolved", status="resolved", decided=True
            ),
        ],
    )

    records = ea.load_tension_records("agent_rem", store)
    statuses = {r["tension"]["tension_id"]: r["status"] for r in records}
    assert statuses == {"t-open": "open", "t-resolved": "resolved"}, (
        f"既有兩態的讀取語意不得改變：{statuses}"
    )

    open_ids = [t.tension_id for t in ea.load_open_tensions("agent_rem", store)]
    assert open_ids == ["t-open"], (
        f"只有 open 進未閉合佇列；resolved 不得進：{open_ids}"
    )

    # 寫回往返：既有兩態不得被改寫（``resolved`` 仍是 resolved，``open`` 仍是 open）。
    target = tmp_path / "target"
    assert ea.save_tension_records("agent_rem", records, target) is True
    reread = {
        r["tension"]["tension_id"]: r["status"]
        for r in ea.load_tension_records("agent_rem", target)
    }
    assert reread == {"t-open": "open", "t-resolved": "resolved"}, (
        f"既有兩態的寫回語意不得改變：{reread}"
    )


# ── P3-T7：extras 不得污染張力本體（13 個正規欄位）──────────────────────
def test_p3_t7_payload_extras_do_not_pollute_the_tension_body(tmp_path):
    """P3-T7 — 帶 extras 的事件讀回後，**張力本體的鍵數 == 13**。

    ``Tension`` 的既有契約＝13 個正規欄位（``Tension.to_dict()`` 的鍵集；漂移守衛
    見 ``tests/inner_life/test_epistemic_tension_store.py``）。Phase 2 的思考結果
    ＋ 驅動快照都是**保留 payload 鍵** ⇒ 不得污染本體。
    """
    store = tmp_path / "tensions"
    _write_events(store, [_production_shaped_event()])

    records = ea.load_tension_records("agent_rem", store)
    assert len(records) == 1, f"應讀回 1 筆，實得 {len(records)} 筆"
    body = records[0]["tension"]
    canonical = set(_tension().to_dict().keys())
    assert len(canonical) == 13, (
        f"Tension 的正規欄位應為 13 個，實得 {len(canonical)} 個：{sorted(canonical)}"
    )
    assert set(body.keys()) == canonical, (
        "張力本體必須恆為 Tension 的 13 個正規欄位："
        f"多出={sorted(set(body.keys()) - canonical)}"
        f" 缺少={sorted(canonical - set(body.keys()))}"
    )
    assert len(body) == 13, f"張力本體的鍵數必須 == 13，實得 {len(body)}"
    for key in ea.TENSION_RESOLUTION_EXTRA_KEYS:
        assert key not in body, f"extras 不得污染張力本體：{key}"


# ── P3-T8：extras 由**記錄一級欄位**暴露（值正確、型別正確）──────────────
def test_p3_t8_record_exposes_resolution_extras_as_first_class_fields(tmp_path):
    """P3-T8 — 記錄的一級欄位可直接讀到 4 個 extras（值正確、型別正確）。

    Phase 3 的消費者**只能**從這裡讀：``Tension`` 只有 13 個欄位，沒有容器放它們。
    """
    store = tmp_path / "tensions"
    _write_events(store, [_production_shaped_event()])

    records = ea.load_tension_records("agent_rem", store)
    assert len(records) == 1, f"應讀回 1 筆，實得 {len(records)} 筆"
    record = records[0]

    assert isinstance(record["partial_resolution"], float), (
        "partial_resolution 應為 float，實得 "
        f"{type(record['partial_resolution']).__name__}"
    )
    assert record["partial_resolution"] == 0.5

    assert isinstance(record["remaining_gap_count"], int) and not isinstance(
        record["remaining_gap_count"], bool
    ), (
        "remaining_gap_count 應為 int，實得 "
        f"{type(record['remaining_gap_count']).__name__}"
    )
    assert record["remaining_gap_count"] == 2

    assert isinstance(record["decided"], bool), (
        f"decided 應為 bool，實得 {type(record['decided']).__name__}"
    )
    assert record["decided"] is False

    assert isinstance(record["reason"], str), (
        f"reason 應為 str，實得 {type(record['reason']).__name__}"
    )
    assert record["reason"] == "partial_gap"


# ── P3-T9：寫回往返後 extras **不丟失**（本追加任務的核心）────────────────
def test_p3_t9_round_trip_preserves_resolution_extras(tmp_path):
    """P3-T9 — ``save_tension_records`` → ``load_tension_records`` 後 extras 仍在。

    這是追加任務的核心：``Tension.to_dict()`` 只輸出 13 個正規欄位 ⇒ 寫回時必須
    **從記錄把那 4 欄帶進 payload**，否則一次往返就把部分消解的進度抹掉。
    """
    source = tmp_path / "source"
    _write_events(source, [_production_shaped_event()])
    records = ea.load_tension_records("agent_rem", source)
    assert len(records) == 1, f"應讀回 1 筆，實得 {len(records)} 筆"

    target = tmp_path / "target"
    assert ea.save_tension_records("agent_rem", records, target) is True

    reread = ea.load_tension_records("agent_rem", target)
    assert len(reread) == 1, f"應讀回 1 筆，實得 {len(reread)} 筆"
    for key in ea.TENSION_RESOLUTION_EXTRA_KEYS:
        assert records[0][key] is not None, (
            f"來源記錄的 {key} 必須有值（否則往返斷言會空轉而『意外全綠』）"
        )
        assert reread[0][key] == records[0][key], (
            f"寫回往返後 {key} 不得丟失：{records[0][key]!r} → {reread[0][key]!r}"
        )
        assert key not in reread[0]["tension"], (
            f"{key} 是保留 payload 鍵 ⇒ 不得出現在張力本體裡"
        )

    # 事件層級：寫出的事件 payload 必須真的帶著那 4 欄。
    events = _read_events(target)
    assert len(events) == 1, f"應寫出 1 筆事件，實得 {len(events)} 筆"
    for key in ea.TENSION_RESOLUTION_EXTRA_KEYS:
        assert key in events[0]["payload"], f"寫出的事件 payload 必須帶著 {key}"
    assert set(events[0].keys()) == set(ea.TENSION_EVENT_FIELDS), (
        f"事件欄位形狀必須封閉：{sorted(events[0].keys())}"
    )


# ── 變異 harness 的載入點覆寫：只准 ``%TEMP%`` ───────────────────────────
def test_mutation_load_point_override_rejects_paths_outside_temp():
    """載入點覆寫**只接受** ``%TEMP%`` 底下的路徑；其餘一律 ``RuntimeError`` 拒絕。

    這是「變異實驗不得寫入 repo 檔」的**執行期硬攔**：repo 檔自己、repo 底下任何
    路徑都不可被當成變異副本。

    🔴 這裡刻意用 ``REPO_ROOT`` 底下的路徑當反例，**不用** ``MODULE_PATH``：
       變異 harness 會把 ``MODULE_PATH`` 指到 ``%TEMP%`` 底下的副本（那本來就是
       合法的覆寫目標），拿它當反例會在變異執行期自己轉紅。
    """
    with pytest.raises(RuntimeError):
        _resolve_override_path(str(REPO_ROOT / "src" / "inner_life" / "epistemic_action.py"))
    with pytest.raises(RuntimeError):
        _resolve_override_path(str(REPO_ROOT))
    with pytest.raises(RuntimeError):
        _resolve_override_path(str(Path(tempfile.gettempdir()).parent))
