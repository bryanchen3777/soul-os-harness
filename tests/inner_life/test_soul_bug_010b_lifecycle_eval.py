"""SOUL-BUG-010-B 守衛：生命週期自動老化（evaluate_lifecycle_all）＋ night slot 接線。

工單：`logs/SOUL-BUG-010B-WORK-ORDER.md`。

本檔守的是**六個獨立維度**，每一條都對應一次變異實驗（轉紅即證明守衛有牙）：

  G1 錨點來自原始邊檔：value 節點有 active edge 時錨點 = 該 edge 的
     ``valid_from_ts``，**不是** ``created_ts``（否則 10 顆 value 會被誤傷）
  G2 ``last_support_ts`` 注入**不寫回**節點檔（Append-Only 紅線）
  G3 轉變事件 append 進 sidecar，且**多筆取最後一筆**
  G4 引擎豁免 essence：essence 永不轉 weakening
  G5 ``weakening`` 不影響 ``min_evidence``（防線二只排除 dormant）
  G6 失敗隔離：sidecar 寫入失敗時評估不 raise

⚠️ 每條「不得發生 X」都配「該發生的位置確實發生了」的對照組，避免「守衛綠是因為
什麼都沒發生」這種假綠。

紅線：本檔不得啟動伺服器、不得綁 :8000/:8765/:8766/:8767、不得對生產發真實請求、
不得殺任何行程；所有 store 一律 `tmp_path`，不碰 `data/**`。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    _inject_support_anchor,
    _support_anchor_by_node,
    evaluate_lifecycle_all,
)
from src.inner_life.lifecycle_overlay import (
    LIFECYCLE_EVENTS_FILENAME,
    load_lifecycle_events,
)

_ISO = "%Y-%m-%dT%H:%M:%S+00:00"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(_ISO)


def _node(
    agent: str,
    content: str,
    node_type: str = "pattern",
    cand: str = "value",
    *,
    node_id: str | None = None,
    created_days_ago: float = 0.0,
    last_support_days_ago: float | None = None,
) -> dict:
    nid = node_id or uuid.uuid4().hex
    return {
        "node_id": nid,
        "node_type": node_type,
        "content": content,
        "agent_id": agent,
        "candidate_node_type": cand if node_type == "pattern" else None,
        "confidence": 0.5,
        "stability": 0.0,
        "valence": "neutral",
        "created_ts": _iso(created_days_ago),
        "last_support_ts": _iso(
            created_days_ago if last_support_days_ago is None else last_support_days_ago
        ),
        "lineage_depth": 0,
        "lineage_path": [],
        "parent_node_id": None,
        "provenance_ref": None,
        "lifecycle_state": "active",
        "superseded_by": None,
    }


def _edge(agent: str, node_id: str, src: str, *, days_ago: float = 0.0, source_type="inner_life_event") -> dict:
    return {
        "edge_id": uuid.uuid4().hex,
        "node_id": node_id,
        "source_type": source_type,
        "source_id": src,
        "agent_id": agent,
        "weight": 1.0,
        "inner_life_event_id": src,
        "trigger_type": "diary:night",
        "valid_from_ts": _iso(days_ago),
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


def _events(store_dir: Path):
    return load_lifecycle_events(store_dir)


# ══ G1 錨點必須來自原始邊檔，不是節點欄位 ══════════════════════════


def test_g1_anchor_comes_from_edges_not_node_field():
    """G1：錨點 = 仍有效支持邊的最大 ``valid_from_ts``。

    這是整張票的核心。若錨點取自節點欄位 ``last_support_ts``
    （全庫無寫入者 ⇒ 恆等於 ``created_ts``），value 節點會被誤判成 weakening。
    """
    edges = [
        _edge("a", "n1", "s1", days_ago=10.0),
        _edge("a", "n1", "s2", days_ago=2.0),   # 最新支持
        _edge("a", "n2", "s3", days_ago=9.0),
        _edge("a", "n3", "s4", days_ago=1.0),
    ]
    # n3 的邊已失效 ⇒ 不算「仍有效支持」
    edges[3]["valid_until_ts"] = _iso(0.5)

    anchors = _support_anchor_by_node(edges)
    assert anchors["n1"] == _iso(2.0)
    assert anchors["n2"] == _iso(9.0)
    assert "n3" not in anchors, "失效的邊不該被當成有效支持"


def test_g1_value_node_with_fresh_support_does_not_weaken(tmp_path):
    """G1 的端到端對照：**持續被重新支持的 value 節點不得被誤傷成 weakening**。

    這正是生產實測的場景（value ``6a92c906b245``：81 條有效邊橫跨 4 天）。
    若守衛壞掉，生產上每隻 agent 的 1 顆 value 會被套上「我隱約覺得」。
    """
    store = tmp_path / "s"
    v = _node("agent_x", "持續被支持的核心價值", node_type="value",
              created_days_ago=8.0, last_support_days_ago=8.0)
    _write(store, [v], [_edge("agent_x", v["node_id"], "s1", days_ago=1.0)])

    events = evaluate_lifecycle_all(store_dir=store)
    assert events == [], f"value 節點被誤判成 weakening：{events}"


def test_g1_control_group_unrenewed_pattern_does_weaken(tmp_path):
    """G1 的對照組：同樣久、但**沒有任何仍有效支持邊**的 pattern **確實**該轉。

    沒有這條，G1 可能因為「什麼都不會轉」而綠。

    ⚠️ fixture 的 10.0 天不是拍脑袋：引擎對「無有效支持邊」的節點用
    ``created_ts + LIFECYCLE_GRACE_DAYS(3)`` 當錨點 ⇒ 距錨點的天數 =
    建立天數 − 3，故要滿 7 天門檻需**建立於 10 天前**（10 − 3 = 7）。
    """
    store = tmp_path / "s"
    p = _node("agent_x", "很久沒被支持過的規律", created_days_ago=10.0)
    _write(store, [p], [])

    events = evaluate_lifecycle_all(store_dir=store)
    assert len(events) == 1, f"應有 1 筆轉變，實得 {events}"
    assert events[0]["lifecycle_state_after"] == "weakening"
    assert events[0]["node_id"] == p["node_id"]


# ══ G2 Append-Only：注入不得寫回節點檔 ══════════════════════════════


def test_g2_injection_never_writes_the_nodes_file(tmp_path):
    """G2：``last_support_ts`` 的注入只存在於記憶體，節點檔位元組必須不變。"""
    store = tmp_path / "s"
    v = _node("agent_x", "核心價值", node_type="value", created_days_ago=8.0)
    _write(store, [v], [_edge("agent_x", v["node_id"], "s1", days_ago=1.0)])
    before = (store / NODES_FILENAME).read_bytes()

    evaluate_lifecycle_all(store_dir=store)

    assert (store / NODES_FILENAME).read_bytes() == before, (
        "evaluate_lifecycle_all 改寫了 elevation_nodes.jsonl —— Append-Only 紅線破"
    )


def test_g2_injection_uses_replace_not_attribute_assignment():
    """G2 的機制守衛：注入必須用 ``dataclasses.replace()``。

    ``ElevationNode`` 是 frozen dataclass ⇒ 直接賦值會拋 FrozenInstanceError
    （SOUL-BUG-010 挖出的同款地雷：舊碼那樣寫，R3 隔離從未真正執行過）。
    """
    from soul_elevation.models import ElevationNode

    n = ElevationNode(
        node_id="n1", node_type="value", content="c", confidence=0.5, stability=0.0,
        valence="neutral", agent_id="a", parent_node_id=None, lineage_depth=0,
        lineage_path=[], created_ts=_iso(8.0), provenance_ref=None,
        lifecycle_state="active", last_support_ts=_iso(8.0),
    )
    with pytest.raises(Exception):
        n.last_support_ts = _iso(1.0)  # type: ignore[misc]

    class _FakeEngine:
        _nodes = {"n1": n}

    eng = _FakeEngine()
    changed = _inject_support_anchor(eng, {"n1": _iso(1.0)})
    assert changed == 1
    assert eng._nodes["n1"].last_support_ts == _iso(1.0)


# ══ G3 轉變事件 append 進 sidecar ══════════════════════════════════


def test_g3_transitions_are_appended_to_the_sidecar(tmp_path):
    """G3：轉變事件必須 append 進 ``elevation_lifecycle_events.jsonl``。"""
    store = tmp_path / "s"
    p = _node("agent_x", "八天沒人理會的規律", created_days_ago=10.0)
    _write(store, [p], [])

    events = evaluate_lifecycle_all(store_dir=store)
    assert len(events) == 1
    stored = _events(store)
    assert len(stored) == 1
    assert stored[0]["node_id"] == p["node_id"]
    assert stored[0]["lifecycle_state_after"] == "weakening"
    assert stored[0]["lifecycle_state_before"] == "active"
    # 🔴 overlay 讀的是 `lifecycle_state` 這個 key（不是 before/after）——
    # 少了它，整批事件會被 `_reduce_events` 忽略，狀態永遠不生效。
    assert stored[0]["lifecycle_state"] == "weakening", (
        "事件缺少 overlay 實際讀取的 `lifecycle_state` 欄位"
    )
    assert "anchor_ts" in stored[0] and "reason" in stored[0]


def test_g3_last_event_wins_for_the_same_node(tmp_path):
    """G3：同一 node_id 多筆事件取**最後一筆**（append-only ⇒ 檔案順序即時間序）。

    這是 `lifecycle_overlay._reduce_events` 的既有語意，本票不得破壞。

    ⚠️ 33.0 天 = 錨點落在 30 天前（33 − grace 3 = 30）⇒ 第二次評估可轉 dormant。
    且引擎**逐級**轉移（一次評估最多一級）：第一次 active→weakening，
    第二次讀到 sidecar 的 weakening 才可再轉 dormant。
    """
    store = tmp_path / "s"
    p = _node("agent_x", "會被降級又會被升級的節點", created_days_ago=33.0)
    _write(store, [p], [])

    evaluate_lifecycle_all(store_dir=store)  # 30 天+ → weakening（逐級：先一級）
    evaluate_lifecycle_all(store_dir=store)  # 再評一次 → dormant

    stored = _events(store)
    assert len(stored) == 2, f"應累積 2 筆，實得 {len(stored)}"
    assert stored[-1]["lifecycle_state_after"] == "dormant"


# ══ G4 essence 豁免 ═══════════════════════════════════════════════


def test_g4_essence_is_never_weakened(tmp_path):
    """G4：essence 是鎖死層，**永不**因失去支持而 weakening。

    對照組：同條件下的 value 節點**確實會**轉。
    """
    store = tmp_path / "s"
    ess = _node("agent_x", "鎖死的內涵", node_type="essence", created_days_ago=60.0)
    val = _node("agent_x", "普通的價值", node_type="value", created_days_ago=60.0)
    _write(store, [ess, val], [])

    events = evaluate_lifecycle_all(store_dir=store)
    ids = {e["node_id"] for e in events}
    assert ess["node_id"] not in ids, "essence 被降級了（它是鎖死層）"
    assert val["node_id"] in ids, "對照組失效：value 節點同條件下竟未降級"


# ══ G5 weakening 不影響 min_evidence（防線二只排除 dormant）══════


def test_g5_weakening_still_counts_toward_min_evidence(tmp_path):
    """G5：**weakening 保留完整投票權**（只有 dormant／superseded 被排除）。

    語意邊界：`weakening` = 7 天無新支持，人類心智中「週一一次、下週二又一次」
    是標準的長期跨週習慣。連坐排除會推翻 ``prior.py:62``「old ≠ outdated」。
    """
    from src.inner_life.elevation_adapter import elevate_matured_patterns

    store = tmp_path / "s"
    p = _node("agent_x", "每個禮拜三都會去河堤走一圈", created_days_ago=8.0)
    edges = [_edge("agent_x", p["node_id"], f"s{i}", days_ago=8.0) for i in range(2)]
    _write(store, [p], edges)
    # 標記為 weakening（sidecar 帶入 overlay）
    (store / LIFECYCLE_EVENTS_FILENAME).write_text(
        json.dumps({
            "ts": _iso(0), "node_id": p["node_id"], "event": "lifecycle_transition",
            "lifecycle_state": "weakening",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    elevated = elevate_matured_patterns(store_dir=store)
    assert len(elevated) == 1, "weakening pattern 應仍能升格（它有完整投票權）"


def test_g5_control_group_dormant_is_excluded(tmp_path):
    """G5 的對照組：dormant pattern **不得**單獨把維度推過 min_evidence。"""
    from src.inner_life.elevation_adapter import elevate_matured_patterns

    store = tmp_path / "s"
    p = _node("agent_x", "沉睡的規律", created_days_ago=40.0)
    edges = [_edge("agent_x", p["node_id"], f"s{i}", days_ago=40.0) for i in range(2)]
    _write(store, [p], edges)
    (store / LIFECYCLE_EVENTS_FILENAME).write_text(
        json.dumps({
            "ts": _iso(0), "node_id": p["node_id"], "event": "lifecycle_transition",
            "lifecycle_state": "dormant",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert elevate_matured_patterns(store_dir=store) == [], (
        "dormant pattern 仍能升格 —— 防線二失效"
    )


# ══ G6 失敗隔離 ═══════════════════════════════════════════════════


def test_g6_sidecar_write_failure_does_not_raise(tmp_path, caplog):
    """G6：sidecar 寫入失敗時，評估**不得** raise（絕不阻斷主路徑）。

    ⚠️ `_append_jsonl` 自帶失敗隔離（OSError → warning → False），
    所以「寫入失敗」**不會**往上拋。本守衛因此斷言三件事：
      ① 不 raise；
      ② 事件**沒有**被落地（不可假裝寫成了）；
      ③ 有 warning 被記下（不可靜默）。
    """
    store = tmp_path / "s"
    p = _node("agent_x", "十天沒人理會的規律", created_days_ago=10.0)
    _write(store, [p], [])

    # 把 sidecar 路徑變成一個目錄 ⇒ append 必然失敗
    (store / LIFECYCLE_EVENTS_FILENAME).mkdir(parents=True, exist_ok=True)

    with caplog.at_level("WARNING"):
        result = evaluate_lifecycle_all(store_dir=store)  # 必須不 raise

    assert isinstance(result, list), "必須回傳 list（不 raise）"
    # 事件沒落地：sidecar 路徑是目錄，讀不出事件
    assert _events(store) == [], "寫入失敗卻看得到事件（不可能，但必須被斷言釘住）"
    assert any("寫入" in r.message or "failed" in r.message for r in caplog.records), (
        "寫入失敗必須留下 warning，不可靜默"
    )


def test_g6_missing_store_is_safe(tmp_path):
    """G6：store 不存在時回傳 []，不 raise。"""
    assert evaluate_lifecycle_all(store_dir=tmp_path / "nope") == []


# ══ night slot 接線（scheduler additive 分支）═════════════════════


def test_night_slot_wiring_is_an_additive_scheduler_branch():
    """接線守衛：生命週期評估必須是 scheduler 主迴圈的 **additive 分支**，
    且帶 at-most-once 冪等鍵（30s tick 會在 slot 窗內重入數十次）。

    反空轉設計：同時斷言主迴圈**確實呼叫**了它，以及冪等鍵欄位**確實存在**。
    """
    import ast

    from src.soul import scheduler as sched_mod

    src = Path(sched_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fire_lifecycle_eval"),
        None,
    )
    assert fn is not None, "缺少 _fire_lifecycle_eval"

    # at-most-once 冪等鍵
    assert "_last_lifecycle_eval_date" in src, "缺少 at-most-once 冪等鍵"

    # 主迴圈確實呼叫
    loop = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_loop"),
        None,
    )
    assert loop is not None
    called = {
        n.func.attr
        for n in ast.walk(loop)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "_fire_lifecycle_eval" in called, "主迴圈未呼叫 _fire_lifecycle_eval"

    # 只在 night slot 觸發
    body = ast.dump(fn)
    assert '"night"' in body, "必須只在 night slot 觸發"


def test_lifecycle_eval_is_fail_closed_at_the_scheduler():
    """接線守衛：scheduler 層的例外必須被接住（fail-closed，不阻斷主迴圈）。"""
    import ast
    import inspect

    from src.soul import scheduler as sched_mod

    src = Path(sched_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fire_lifecycle_eval"),
        None,
    )
    assert fn is not None
    has_try = any(isinstance(n, ast.Try) for n in ast.walk(fn))
    assert has_try, "_fire_lifecycle_eval 必須有 try/except（fail-closed）"
    assert "logger.warning" in inspect.getsource(sched_mod.SoulScheduler._fire_lifecycle_eval)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
