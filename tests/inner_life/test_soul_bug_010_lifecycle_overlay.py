"""SOUL-BUG-010 守衛：生命週期讀取面收斊 ＋ 防線二 ＋ R3 邊中和還原。

工單：`logs/SOUL-BUG-010-WORK-ORDER.md`（v2）。

本檔守的是**五條獨立維度**，每一條都對應一次變異實驗（轉紅即證明守衛有牙）：

  M1 收斂點      `load_elevation_nodes` 真的套用 lifecycle overlay
  M2 對外 API    `_collect_soul_essence` 不回傳 dormant / superseded
  M3 防線二      dormant pattern 不得參與昇華計票
  M4 語意邊界    **weakening 仍保有完整投票權**（不得連坐排除）
  M5 R3 還原     成功路徑必須把中和的邊還原（舊碼從不還原）

⚠️ 每條「不得發生 X」都配「該發生的位置確實發生了」的對照組：

  * M2 對照＝同一批節點**沒有** sidecar 時三顆全回傳（證明不是無差別清空）。
  * M3 對照＝同樣兩份證據、狀態改成 weakening／無狀態時**確實升格**。
  * M4 對照＝weakening pattern 真的能被 `load_elevation_nodes` 讀到 weakening
    （證明 M3 綠不是因為 sidecar 根本沒生效）。
  * M5 對照＝在 `engine.elevate` 進入點觀察到 sentinel **確實已被套上**，
    再於呼叫結束後觀察到 sentinel **已全部消失**——兩頭都量，避免「守衛綠
    是因為從來沒中和過」這種假綠。

紅線：本檔不得啟動伺服器、不得綁 :8000/:8765/:8766/:8767、不得對生產發真實請求、
不得殺任何行程；所有 store 一律 `tmp_path`，不碰 `data/**`。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from src.inner_life import elevation_adapter as ea
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    elevate_matured_patterns,
)
from src.inner_life.emergent_projection import (
    NON_PROJECTED_LIFECYCLE_STATES,
    load_elevation_nodes,
)
from src.inner_life.lifecycle_overlay import (
    LIFECYCLE_EVENTS_FILENAME,
    TERMINAL_OR_DORMANT_LIFECYCLE_STATES,
    VALID_LIFECYCLE_STATES,
    apply_lifecycle_overlay,
    load_lifecycle_events,
)

_R3_SENTINEL = "_eh2_r3_blocked"


# ── 圖譜合成（沿用 SOUL-BUG-009 守衛的既有慣例：繞過 run_elevation 直寫節點/邊）──


def _pattern(agent: str, content: str, cand: str = "value") -> dict:
    return {
        "node_id": uuid.uuid4().hex,
        "node_type": "pattern",
        "content": content,
        "agent_id": agent,
        "candidate_node_type": cand,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": "2026-10-17T00:00:00+00:00",
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
    }


def _edge(agent: str, node_id: str, src: str, source_type="inner_life_event") -> dict:
    return {
        "edge_id": uuid.uuid4().hex,
        "node_id": node_id,
        "source_type": source_type,
        "source_id": src,
        "agent_id": agent,
        "weight": 1.0,
        "inner_life_event_id": src,
        "trigger_type": "diary:night",
        "valid_from_ts": "2026-10-17T00:00:00+00:00",
        "valid_until_ts": None,
    }


def _write(store_dir: Path, nodes, edges) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / NODES_FILENAME).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )
    (store_dir / EDGES_FILENAME).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in edges) + "\n",
        encoding="utf-8",
    )


def _quarantine(store_dir: Path, mapping: dict) -> None:
    """寫生命週期 sidecar（隔離降級的唯一合法載體，節點檔本身保持位元組不變）。"""
    lines = [
        json.dumps(
            {
                "ts": "2026-10-19T00:00:00+00:00",
                "node_id": node_id,
                "event": "quarantine",
                "lifecycle_state": state,
                "reason": "SOUL-BUG-010 test fixture",
            },
            ensure_ascii=False,
        )
        for node_id, state in mapping.items()
    ]
    (store_dir / LIFECYCLE_EVENTS_FILENAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _values(store_dir: Path):
    return [
        n for n in load_elevation_nodes(store_dir) if n.get("node_type") == "value"
    ]


# ══ ① overlay 純函式：邊界語意 ══════════════════════════════════════


def test_overlay_last_event_wins_and_is_pure(tmp_path):
    """同 node_id 多筆事件取**檔案順序最後一筆**（append-only ⇒ 檔案順序即時間序）。

    同時驗證**純函式**：回傳新 dict，呼叫端傳入的 dict 不得被原地改動
    ——`load_elevation_nodes` 的回傳物件會被 5 個消費端共享，現場改就是跨呼叫污染。
    """
    original = {"node_id": "n1", "node_type": "value", "content": "x"}
    events = [
        {"node_id": "n1", "lifecycle_state": "weakening"},
        {"node_id": "n1", "lifecycle_state": "dormant"},
    ]
    out = apply_lifecycle_overlay([original], events)
    assert out[0]["lifecycle_state"] == "dormant"
    assert "lifecycle_state" not in original, "apply_lifecycle_overlay 原地改動了呼叫端的 dict"
    assert out[0] is not original


def test_overlay_ignores_invalid_events():
    """非法事件必須被忽略，否則會污染 `_normalize_lifecycle_state` 的 additive 語意
    （未知值缺省 → active；把未知值塞進節點會讓語意漂移）。"""
    nodes = [{"node_id": "n1"}]
    assert apply_lifecycle_overlay(nodes, [{"node_id": "n1", "lifecycle_state": "zombie"}]) == nodes
    assert apply_lifecycle_overlay(nodes, [{"lifecycle_state": "dormant"}]) == nodes
    assert apply_lifecycle_overlay(nodes, [{"node_id": "", "lifecycle_state": "dormant"}]) == nodes


def test_overlay_is_byte_for_byte_noop_without_events(tmp_path):
    """反假綠：零 sidecar 時 overlay 必須是**逐位元 no-op**。"""
    nodes = [{"node_id": "n1", "content": "a"}, {"node_id": "n2", "content": "b"}]
    assert apply_lifecycle_overlay(nodes, []) == nodes


def test_load_lifecycle_events_is_fail_silent(tmp_path):
    """缺檔 → []；單行壞 JSON → 跳過該行；**絕不 raise**。"""
    assert load_lifecycle_events(tmp_path / "nonexistent") == []

    store = tmp_path / "s"
    store.mkdir()
    (store / LIFECYCLE_EVENTS_FILENAME).write_text(
        'not-json{{{\n{"node_id": "n1", "lifecycle_state": "dormant"}\n', encoding="utf-8"
    )
    events = load_lifecycle_events(store)
    assert [e["node_id"] for e in events] == ["n1"]

    assert load_lifecycle_events(None) == []


def test_lifecycle_state_constants_are_identical():
    """🔴 三處狀態集必須恆等，防止 SOUL-BUG-011 式的「常數分裂」重演：
    overlay 不得 import emergent_projection（會循環），代價就是兩份定義，
    緩解手段是這條守衛——任一邊改動都會被擋下。
    """
    assert TERMINAL_OR_DORMANT_LIFECYCLE_STATES == NON_PROJECTED_LIFECYCLE_STATES
    assert TERMINAL_OR_DORMANT_LIFECYCLE_STATES == frozenset({"dormant", "superseded"})
    assert VALID_LIFECYCLE_STATES == frozenset(
        {"active", "weakening", "dormant", "superseded"}
    )


# ══ M1 收斂點：單一入口套用，5 個消費端同時生效 ═════════════════════


def test_m1_loader_applies_overlay(tmp_path):
    """M1：`load_elevation_nodes` 必須帶入 sidecar 狀態。

    **這是 M2／M3／M4 能成立的前提**——收斂點壞了，下游全部失效。
    """
    store = tmp_path / "s"
    p = _pattern("agent_rem", "週三下班繞路買了桂花糕")
    _write(store, [p], [])
    assert _values(store) == []  # pattern 不是 value

    _quarantine(store, {p["node_id"]: "dormant"})
    node = next(n for n in load_elevation_nodes(store) if n["node_id"] == p["node_id"])
    assert node["lifecycle_state"] == "dormant"


def test_m1_loader_unchanged_without_sidecar(tmp_path):
    """反假綠：沒有 sidecar 時讀取面行為必須與改動前逐位元相同。"""
    store = tmp_path / "s"
    p = _pattern("agent_rem", "週三下班繞路買了桂花糕")
    _write(store, [p], [])
    node = load_elevation_nodes(store)[0]
    assert "lifecycle_state" not in node


# ══ M2 對外 API：不洩漏終態 ═══════════════════════════════════════


def _essence_store(tmp_path: Path, *, states: dict) -> Path:
    store = tmp_path / "s"
    nodes = [
        {
            **_pattern("agent_rem", f"靈魂層內容 {i}"),
            "node_type": "value",
            "candidate_node_type": None,
        }
        for i in range(3)
    ]
    _write(store, nodes, [])
    if states:
        _quarantine(store, states)
    return store


def test_m2_gateway_hides_dormant_and_superseded(tmp_path):
    """🔴 M2：`/api/soul/essence/{agent_id}` 是全庫**唯一**沒有生命週期過濾的
    對外端點（SOUL-AUDIT-013-RO 實測）。dormant／superseded 照樣回傳
    = 把已判定失效的靈魂重新洩漏到外部查詢面。"""
    from src.io.gateway import _collect_soul_essence

    nodes = [
        {**_pattern("agent_rem", "還在用的價值"), "node_type": "value"},
        {**_pattern("agent_rem", "沉睡的價值"), "node_type": "value"},
        {**_pattern("agent_rem", "被取代的價值"), "node_type": "value"},
    ]
    store = tmp_path / "s"
    _write(store, nodes, [])
    _quarantine(
        store,
        {nodes[1]["node_id"]: "dormant", nodes[2]["node_id"]: "superseded"},
    )

    out = _collect_soul_essence("agent_rem", store_dir=store)
    assert [x["content"] for x in out["essence"]["value"]] == ["還在用的價值"]


def test_m2_gateway_control_group_all_three_without_sidecar(tmp_path):
    """M2 的對照組：同一批節點**沒有** sidecar 時三顆全回傳。

    沒有這條，M2 可能因為「gateway 從來不讀任何東西」而綠。
    """
    from src.io.gateway import _collect_soul_essence

    nodes = [
        {**_pattern("agent_rem", "還在用的價值"), "node_type": "value"},
        {**_pattern("agent_rem", "沉睡的價值"), "node_type": "value"},
        {**_pattern("agent_rem", "被取代的價值"), "node_type": "value"},
    ]
    store = tmp_path / "s"
    _write(store, nodes, [])

    out = _collect_soul_essence("agent_rem", store_dir=store)
    assert len(out["essence"]["value"]) == 3


# ══ M3／M4 防線二：分級消費的語意邊界 ══════════════════════════════


def _elevate_fixture(tmp_path: Path, state: str | None) -> Path:
    """兩份獨立證據的單顆 value-candidate pattern —— 恰好達 min_evidence=2。"""
    store = tmp_path / "s"
    p = _pattern("agent_rem", "每個禮拜三都會去河堤走一圈")
    edges = [_edge("agent_rem", p["node_id"], f"src-{i}") for i in range(2)]
    _write(store, [p], edges)
    if state:
        _quarantine(store, {p["node_id"]: state})
    return store


def test_m3_dormant_pattern_cannot_elevate_alone(tmp_path):
    """M3：dormant pattern 不得單獨把某維度推過 min_evidence。

    防線二的全部意義：陳年廢棄的 pattern 不能繼續為新靈魂背書。
    """
    store = _elevate_fixture(tmp_path, "dormant")
    assert elevate_matured_patterns(store_dir=store) == []
    assert _values(store) == []


def test_m4_weakening_pattern_keeps_full_voting_right(tmp_path):
    """🔴 M4（語意邊界，最容易寫錯的那條）：**weakening 仍可正常升格**。

    `weakening` = 7 天無新支持，人類心智中「週一一次、下週二又一次」是標準的
    長期跨週習慣。若把 weakening 連坐排除，系統就只剩「一週內密集成癮的短暫
    衝動」能升格——直接推翻 soul-elevation `prior.py:62`「old ≠ outdated」。
    """
    store = _elevate_fixture(tmp_path, "weakening")
    assert len(elevate_matured_patterns(store_dir=store)) == 1
    assert len(_values(store)) == 1


def test_m4_control_group_sidecar_actually_reaches_the_engine(tmp_path):
    """M4 的對照組：證明 weakening **確實有被讀進引擎**。

    否則 M4 可能因為「sidecar 根本沒生效、狀態一律 active」而綠——
    那就等於沒測到分級消費。
    """
    store = _elevate_fixture(tmp_path, "weakening")
    captured = {}
    real = ea._rebuild_engine_for

    def spy(*args, **kwargs):
        engine = real(*args, **kwargs)
        captured["engine"] = engine
        return engine

    ea._rebuild_engine_for = spy
    try:
        elevate_matured_patterns(store_dir=store)
    finally:
        ea._rebuild_engine_for = real

    states = {n.lifecycle_state for n in captured["engine"]._nodes.values()}
    # 引擎內含 pattern（weakening，sidecar 帶入）與本趟新建的 value 靈魂節點（active），
    # 故斷言 weakening **確實在集合內**，而非集合恰好等於 {weakening}。
    assert "weakening" in states, f"引擎看不到 weakening，sidecar 未生效：{states}"


def test_m3_control_group_no_sidecar_still_elevates(tmp_path):
    """M3 的對照組：同樣兩份證據、無任何狀態標記時**確實升格**。

    沒有這條，M3 可能因為「證據本來就不夠」而綠。
    """
    store = _elevate_fixture(tmp_path, None)
    assert len(elevate_matured_patterns(store_dir=store)) == 1
    assert len(_values(store)) == 1


def test_superseded_pattern_also_excluded(tmp_path):
    """superseded 與 dormant 同屬終態，一併不得參與計票。"""
    store = _elevate_fixture(tmp_path, "superseded")
    assert elevate_matured_patterns(store_dir=store) == []


# ══ M5 R3 邊中和：成功路徑必須還原 ══════════════════════════════════


def test_m5_r3_neutralized_edges_are_restored_after_success(tmp_path, monkeypatch):
    """🔴 M5：R3 計票隔離的「暫時失效」必須**真的暫時**。

    舊碼兩處缺陷：
      ① `_select_anchor_representative` 在 `try` 之外，可拋 KeyError；
      ② 成功路徑**從不還原**。而 `engine` 是 per-agent_id 重建、跨候選維度
      重用（`:1157` 建一次，`:1203` 迴圈所有維度共用）⇒ 維度 A 成功昇華會讓
      維度 B/C 的計票少算那些邊。累積且靜默。

    **雙頭觀察**（避免假綠）：
      - 在 `engine.elevate` 進入點量一次 ⇒ sentinel 確實已被套上；
      - 在整趟呼叫結束後再量一次 ⇒ sentinel 已全部消失。
    """
    store = tmp_path / "s"
    blocked = _pattern("agent_rem", "從 assimilate 事實累積出來的規律")
    normal = _pattern("agent_rem", "自己慢慢看見的做事節奏")
    blocked_src = "sage-src-1"
    edges = [
        _edge("agent_rem", blocked["node_id"], blocked_src, source_type="sage_fact"),
        _edge("agent_rem", blocked["node_id"], blocked_src + "-b", source_type="sage_fact"),
        _edge("agent_rem", normal["node_id"], "own-src-1"),
        _edge("agent_rem", normal["node_id"], "own-src-2"),
    ]
    _write(store, [blocked, normal], edges)

    monkeypatch.setattr(
        ea, "_load_fact_origins", lambda agent_id: {blocked_src: ea._ORIGIN_ASSIMILATED}
    )

    captured = {}
    real = ea._rebuild_engine_for

    def spy(*args, **kwargs):
        engine = real(*args, **kwargs)
        captured["engine"] = engine
        real_elevate = engine.elevate

        def counting_elevate(*a, **kw):
            captured["sentinel_at_entry"] = sum(
                1 for e in engine._edges if e.valid_until_ts == _R3_SENTINEL
            )
            return real_elevate(*a, **kw)

        engine.elevate = counting_elevate
        return engine

    monkeypatch.setattr(ea, "_rebuild_engine_for", spy)

    elevated = elevate_matured_patterns(store_dir=store)

    assert elevated, "控制組失效：本 fixture 應能昇華 1 顆 value"
    assert captured["sentinel_at_entry"] > 0, (
        "觀察組失效：elevate 進入點根本沒有 sentinel，"
        "M5 守衛會因為『從未中和過』而假綠"
    )
    leaked = [
        e.node_id
        for e in captured["engine"]._edges
        if e.valid_until_ts == _R3_SENTINEL
    ]
    assert leaked == [], f"R3 中和的邊在成功路徑後仍留著 sentinel：{leaked}"


def test_m5_r3_restored_even_when_anchor_selection_raises(tmp_path, monkeypatch):
    """M5 的第二個分支：錨點選取拋例外時也必須還原。

    這一條專門釘「`try` 必須覆蓋 `_select_anchor_representative` 本身」。
    舊碼該呼叫在 `try` 之外，KeyError 會讓已中和的邊永遠留在 sentinel 狀態。
    """
    store = tmp_path / "s"
    blocked = _pattern("agent_rem", "從 assimilate 事實累積出來的規律")
    edges = [
        _edge("agent_rem", blocked["node_id"], f"sage-src-{i}", source_type="sage_fact")
        for i in range(2)
    ]
    _write(store, [blocked], edges)
    monkeypatch.setattr(
        ea, "_load_fact_origins", lambda agent_id: {"sage-src-0": ea._ORIGIN_ASSIMILATED}
    )

    captured = {}
    real = ea._rebuild_engine_for

    def spy(*args, **kwargs):
        engine = real(*args, **kwargs)
        captured["engine"] = engine
        return engine

    monkeypatch.setattr(ea, "_rebuild_engine_for", spy)
    monkeypatch.setattr(
        ea,
        "_select_anchor_representative",
        lambda *a, **kw: (_ for _ in ()).throw(KeyError("boom")),
    )

    assert elevate_matured_patterns(store_dir=store) == []

    leaked = [
        e.node_id
        for e in captured["engine"]._edges
        if e.valid_until_ts == _R3_SENTINEL
    ]
    assert leaked == [], f"錨點選取拋例外後 sentinel 洩漏：{leaked}"


# ══ Append-Only 紅線 ═══════════════════════════════════════════════


def test_overlay_never_writes_nodes_file(tmp_path):
    """反假綠：整套 overlay 機制**不得**改寫 `elevation_nodes.jsonl`。

    隔離降級的唯一合法載體是 sidecar；若節點檔位元組變動，Append-Only 紅線即破。
    """
    store = tmp_path / "s"
    p = _pattern("agent_rem", "每個禮拜三都會去河堤走一圈")
    _write(store, [p], [])
    before = (store / NODES_FILENAME).read_bytes()

    _quarantine(store, {p["node_id"]: "dormant"})
    load_elevation_nodes(store)

    assert (store / NODES_FILENAME).read_bytes() == before


# ══ 雙軌讀取封堵：去重索引必須走 SSOT loader ═══════════════════════


def _dedup_index(store_dir: Path):
    from src.inner_life.elevation_adapter import _load_existing_dedup_index

    return _load_existing_dedup_index(store_dir)


def test_dedup_index_uses_ssot_loader_not_its_own_open(tmp_path, monkeypatch):
    """🔴 雙軌讀取封堵：去重索引**不得**自己開檔。

    舊碼 `elevation_adapter._load_existing_dedup_index` 自帶 `open(NODES_FILENAME)`，
    是全庫僅存的第二條節點讀取路徑。即使它當下不消費 lifecycle_state，
    它仍是側門：未來任何依賴節點狀態的邏輯接在這條路徑上，就會繞過 overlay
    靜默看到過期狀態。

    驗證方式不是「看程式碼」，而是**讓 loader 失效**：
    若索引仍能從檔案算出結果 ⇒ 它沒走 loader ⇒ 守衛轉紅。
    """
    store = tmp_path / "s"
    p = _pattern("agent_rem", "每個禮拜三都會去河堤走一圈")
    _write(store, [p], [])

    import src.inner_life.emergent_projection as ep

    monkeypatch.setattr(ep, "load_elevation_nodes", lambda *a, **k: [])

    assert _dedup_index(store) == {}, (
        "去重索引仍能從檔案算出結果 ⇒ 它沒有走 SSOT loader，側門未封堵"
    )


def test_dedup_index_semantics_preserved(tmp_path):
    """收斂後語意必須逐位元等價：檔案順序第一列勝出、壞列記 None 可補位。"""
    store = tmp_path / "s"
    store.mkdir(parents=True)
    rows = [
        {"node_id": "n1", "node_type": "value", "agent_id": "agent_rem", "content": "A"},
        {"node_type": "value", "agent_id": "agent_rem", "content": "B"},  # 壞列：缺 node_id
        {"node_id": "n3", "node_type": "value", "agent_id": "agent_rem", "content": "A"},
        {"node_id": "n4", "node_type": "value", "agent_id": "agent_rem", "content": "C"},
    ]
    (store / NODES_FILENAME).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    (store / EDGES_FILENAME).write_text("", encoding="utf-8")

    idx = _dedup_index(store)
    from src.inner_life.elevation_adapter import _dedup_content_hash

    def _entry(content: str):
        return idx[("agent_rem", "value", _dedup_content_hash(content))]

    # A 的兩列：檔案順序第一列 n1 勝出（n3 不該取代）
    assert _entry("A") == "n1"
    # B 只有壞列 ⇒ canonical 為 None
    assert _entry("B") is None
    # C 正常
    assert _entry("C") == "n4"


def test_dedup_index_fail_open_on_missing_store(tmp_path):
    """缺檔 → 空 dict（不 raise）。"""
    assert _dedup_index(tmp_path / "nonexistent") == {}


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])