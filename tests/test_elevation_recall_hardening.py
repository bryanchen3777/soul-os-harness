"""
tests/test_elevation_recall_hardening.py — 回憶通道硬化守衛

工單：`logs/ELEVATION-RECALL-HARDENING-1-WORK-ORDER.md`（ELEVATION-RECALL-HARDENING-1）。

本檔守的是**三條獨立維度**，每一條都對應一次變異實驗（轉紅即證明守衛有牙）：

  MUT-H1 隔離守門    共用管線拿掉隔離守門 ⇒ 隔離節點被召回（轉紅）
  MUT-H2 overlay 旗標  旗標恆不附加 ⇒ 隔離節點被召回（轉紅；證明旗標真的被讀到）
  MUT-H3 詞元級比對   退回「整句子字串」⇒ 詞元級命中守衛轉紅
  MUT-H4 契約路徑    query_recall 也回隔離節點 ⇒ 契約路徑隔離守衛轉紅
  MUT-H5 go/no-go   門檻恆 True/False ⇒ 判定守衛轉紅

⚠️ 每條「不得發生 X」都配「該發生的位置確實發生了」的對照組：
  * 隔離守門的對照＝同一批節點**沒有** sidecar 時**確實被召回**（證明不是無差別清空）。
  * 旗標守衛的對照＝sidecar 裡**確實**有 quarantine 事件且節點 dict **確實**帶旗標。
  * 詞元級守衛的對照＝同一組 tags 走**整句子字串**路徑**確實不命中**。
  * go/no-go 守衛的對照＝四項全過的輸入**確實**回 GO。

紅線：本檔不得啟動伺服器、不得綁 :8000/:8765/:8766/:8767、不得對生產發真實請求、
不得殺任何行程、**不得使用 subprocess**（tests/infra 的 R5 白名單大小鎖死 22）；
所有 store 一律 `tmp_path`，不碰 `data/**`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from soul_elevation import ElevationNode

from src.inner_life.elevation_recall import (
    DORMANT_TONE_PREFIX,
    MAX_RECALL_TAGS,
    RECALL_HIT_KEYS,
    format_recall_hit,
    query_recall,
    query_recall_by_tags,
)
from src.inner_life.emergent_projection import (
    load_elevation_nodes,
    project_emergent,
)
from src.inner_life.lifecycle_overlay import (
    LIFECYCLE_EVENTS_FILENAME,
    apply_lifecycle_overlay,
    load_quarantined_node_ids,
)
from src.inner_life.recall_readiness import (
    GO_MIN_HIT_RATE,
    GO_MIN_RECALLABLE_POOL,
    GO_MIN_SUPERSEDED,
    go_no_go,
    hit_rate,
    pool_summary,
)

ALICE = "agent_alice"
OTHER = "agent_bob"
NODES_FILENAME = "elevation_nodes.jsonl"

HIT_KEYS = {
    "node_id", "node_type", "content", "lifecycle_state", "lineage_path", "superseded_by",
}


# ── 合成資料 ────────────────────────────────────────────────────────────


def _node(node_id, node_type="belief", content="深夜加班讓人心累。", agent_id=ALICE,
          created_ts="2026-08-29T00:00:00.000000+00:00", lifecycle_state=None, **extra):
    """構造一條 elevation node dict（欄位對齊 elevation_nodes.jsonl 記錄）。"""
    node = {
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
    if lifecycle_state is not None:
        node["lifecycle_state"] = lifecycle_state
    node.update(extra)
    return node


def _write_nodes(store_dir: Path, nodes) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / NODES_FILENAME).write_text(
        "\n".join(json.dumps(n, ensure_ascii=False) for n in nodes) + "\n",
        encoding="utf-8",
    )


def _write_events(store_dir: Path, events) -> None:
    """寫生命週期 sidecar（隔離旗標的唯一合法載體）。"""
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / LIFECYCLE_EVENTS_FILENAME).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def _quarantine_event(node_id, state="dormant"):
    return {
        "ts": "2026-10-19T19:30:00+00:00",
        "node_id": node_id,
        "event": "quarantine",
        "lifecycle_state": state,
        "reason": "SOUL-BUG-010 historical truncation victim",
    }


def _snapshot(store_dir: Path):
    """全 store 快照：檔名 → 位元組（斷言 0 寫入用）。"""
    return {
        p.name: p.read_bytes()
        for p in sorted(store_dir.iterdir())
        if p.is_file()
    }


# ══ ① 隔離守門（兩條檢索路徑）══════════════════════════════════════


def test_quarantined_node_never_recalled_by_query_recall(tmp_path):
    """🔴 MUT-H1/H4：隔離節點在契約路徑 ``query_recall`` 不可召回。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-clean", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
        _node("n-bad", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
    ])
    _write_events(store, [_quarantine_event("n-bad")])

    hits = query_recall(ALICE, "深夜加班", store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-clean"], (
        "隔離節點被契約路徑召回了 —— 隔離守門失效"
    )


def test_quarantined_node_never_recalled_by_query_recall_by_tags(tmp_path):
    """🔴 MUT-H1/H4：隔離節點在詞元級路徑 ``query_recall_by_tags`` 也不可召回。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-clean", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
        _node("n-bad", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
    ])
    _write_events(store, [_quarantine_event("n-bad")])

    hits = query_recall_by_tags(ALICE, ["加班", "心累"], store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-clean"], (
        "隔離節點被詞元級路徑召回了 —— 隔離守門失效"
    )


def test_control_group_same_nodes_without_sidecar_are_recalled(tmp_path):
    """反假綠：同一批節點**沒有** sidecar 時**確實被召回**。

    沒有這條，隔離守衛可能因為「什麼都回不來」而綠。
    """
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-clean", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
        _node("n-bad", "belief", "深夜加班讓人心累。", lifecycle_state="dormant"),
    ])

    assert [h["node_id"] for h in query_recall(ALICE, "深夜加班", store_dir=store)] == [
        "n-bad", "n-clean",
    ]
    assert [h["node_id"] for h in
            query_recall_by_tags(ALICE, ["加班"], store_dir=store)] == ["n-bad", "n-clean"]


def test_control_group_quarantine_ids_really_reach_the_recall_layer(tmp_path):
    """反假綠：sidecar 裡的 quarantine 事件**確實**讓節點被列入隔離集合。

    沒有這條，MUT-H2（隔離判定恆為空集合）會讓隔離守衛**假綠**。
    """
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-bad", "belief", "污染正文。", lifecycle_state="dormant")])
    _write_events(store, [_quarantine_event("n-bad")])

    assert load_quarantined_node_ids(store) == {"n-bad"}


# ══ ② 自然老化節點仍可召回（守門不得連坐）════════════════════════════


def test_non_quarantined_dormant_node_recallable_by_both_paths(tmp_path):
    """非隔離的四層 dormant 節點，兩條路徑都召回得到。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-dormant", "value", "以前很在意把週末留給家人。",
              lifecycle_state="dormant"),
        _node("n-superseded", "belief", "以前覺得成功就是升遷。",
              lifecycle_state="superseded", superseded_by="n-new"),
    ])

    hits = query_recall(ALICE, "以前", store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-dormant", "n-superseded"]

    tagged = query_recall_by_tags(ALICE, ["週末", "升遷"], store_dir=store)
    assert [h["node_id"] for h in tagged] == ["n-dormant", "n-superseded"]
    assert set(tagged[0].keys()) == HIT_KEYS
    assert tagged[0]["lifecycle_state"] == "dormant"


def test_pattern_nodes_still_never_recalled(tmp_path):
    """契約不變：pattern 不回憶（兩條路徑皆然）。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-pat", "pattern", "深夜加班讓人心累。", lifecycle_state="dormant"),
    ])

    assert query_recall(ALICE, "深夜加班", store_dir=store) == []
    assert query_recall_by_tags(ALICE, ["加班"], store_dir=store) == []


def test_illegal_state_filter_still_fails_closed(tmp_path):
    """契約不變：``state_filter`` 含非法態仍回 ``[]``（兩條路徑皆然）。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-d", "belief", "深夜加班。", lifecycle_state="dormant")])

    for bad in ({"active"}, {"weakening"}, {"bogus"}, set(), {"dormant", "active"}, "dormant"):
        assert query_recall(ALICE, "深夜加班", bad, store_dir=store) == []
        assert query_recall_by_tags(ALICE, ["加班"], bad, store_dir=store) == []


# ══ ③ 詞元級比對語意 ════════════════════════════════════════════════


def test_token_level_union_hit_with_chinese_node(tmp_path):
    """🔴 MUT-H3：詞元級 union 命中（合成中文節點 + 合成 tags）。

    對照組在下一條：同樣的 tags 走「整句子字串」**確實不命中**。
    """
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-zh", "belief", "深夜加班很辛苦", lifecycle_state="dormant")])

    hits = query_recall_by_tags(ALICE, ["心累", "加班"], store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-zh"], (
        "union 語意失效：任一 tag 命中即應算命中"
    )
    # 對照：整句子字串（「心累」＋「加班」串成一句）**不**是內容的子字串。
    assert "心累加班" not in "深夜加班很辛苦"


def test_token_level_is_not_whole_sentence_substring(tmp_path):
    """🔴 MUT-H3 的直接對照：整句子字串路徑**確實不命中**同一組資料。

    沒有這條，MUT-H3（比對退回整句子字串）可能因為「本來就命中」而假綠。
    """
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-zh", "belief", "深夜加班很辛苦", lifecycle_state="dormant")])

    whole_sentence = "心累加班"
    assert whole_sentence not in "深夜加班很辛苦"
    assert query_recall(ALICE, whole_sentence, store_dir=store) == []
    # 同一批詞元走詞元級路徑 ⇒ 命中（兩條路徑語意**不同**且**可分辨**）。
    assert query_recall_by_tags(ALICE, ["心累", "加班"], store_dir=store) != []


def test_token_level_normalization_handles_fullwidth_punct_space_case(tmp_path):
    """詞元級正規化：全形／標點／空白／大小寫變體仍命中。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-en", "belief", "Coffee Break，咖啡休息", lifecycle_state="dormant"),
        _node("n-zh", "belief", "深夜加班，很辛苦。", lifecycle_state="dormant"),
    ])

    # 全形英字 + 大小寫變體
    assert query_recall_by_tags(ALICE, ["ＣＯＦＦＥＥ"], store_dir=store) != []
    assert query_recall_by_tags(ALICE, ["coffee"], store_dir=store) != []
    # 標點／空白：tag 帶標點與空白，內容帶標點
    assert query_recall_by_tags(ALICE, ["加班，"], store_dir=store) != []
    assert query_recall_by_tags(ALICE, [" 加班 "], store_dir=store) != []
    # 全形逗號 vs 半形逗號
    assert query_recall_by_tags(ALICE, ["咖啡休息"], store_dir=store) != []


def test_tag_cap_twelve_takes_first_twelve(tmp_path):
    """tags 上限 12 生效（傳 20 個只取前 12）。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-13", "belief", "內容只含 tag13 這個標記", lifecycle_state="dormant"),
        _node("n-12", "belief", "內容只含 tag12 這個標記", lifecycle_state="dormant"),
    ])

    tags13 = ["tag%02d" % i for i in range(1, 14)]   # 第 13 個在第 13 位
    assert MAX_RECALL_TAGS == 12
    # 第 13 個 tag 被上限截掉 ⇒ 含 tag13 的節點不得命中
    hits13 = query_recall_by_tags(ALICE, tags13, store_dir=store)
    assert "n-13" not in [h["node_id"] for h in hits13], "第 13 個 tag 應被上限截掉"
    # 前 12 個裡的第 12 個仍被採用 ⇒ 命中（證明上限是「前 12」而非「前 11」）
    tags12 = ["tag%02d" % i for i in range(1, 13)]
    assert [h["node_id"] for h in
            query_recall_by_tags(ALICE, tags12, store_dir=store)] == ["n-12"]


def test_tag_length_gate_and_empty_tags_fail_closed(tmp_path):
    """tag 守門：正規化後長度 < 2 的 tag 不採用；全無效 ⇒ 回 ``[]``。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-d", "belief", "深夜加班。", lifecycle_state="dormant")])

    assert query_recall_by_tags(ALICE, ["深"], store_dir=store) == []
    assert query_recall_by_tags(ALICE, ["，"], store_dir=store) == []
    assert query_recall_by_tags(ALICE, [], store_dir=store) == []
    assert query_recall_by_tags(ALICE, None, store_dir=store) == []
    assert query_recall_by_tags(ALICE, ["深", "加班"], store_dir=store) != []


# ══ ④ 契約路徑語意零改變 ════════════════════════════════════════════


def test_query_recall_contract_semantics_unchanged(tmp_path):
    """契約路徑逐項不變：簽章／比對語意／回傳形狀／排序／失敗靜默／0 寫入。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-active", "belief", "深夜加班讓人心累。"),
        _node("n-dormant", "belief", "深夜加班讓人心累，但那時很在意升遷。",
              lifecycle_state="dormant"),
        _node("n-B", "belief", "深夜加班讓人心累。",
              created_ts="2026-08-29T10:00:00+00:00", lifecycle_state="superseded",
              superseded_by="n-C"),
    ])
    before = _snapshot(store)

    hits = query_recall(ALICE, "心累", {"dormant"}, store_dir=store)
    assert [h["node_id"] for h in hits] == ["n-dormant"]
    assert set(hits[0].keys()) == HIT_KEYS == set(RECALL_HIT_KEYS)
    assert hits[0]["lifecycle_state"] == "dormant"
    assert hits[0]["content"] == "深夜加班讓人心累，但那時很在意升遷。"
    assert not hits[0]["content"].startswith(DORMANT_TONE_PREFIX)

    # 排序：created_ts asc + node_id asc
    both = query_recall(ALICE, "心累", store_dir=store)
    assert [h["node_id"] for h in both] == ["n-dormant", "n-B"]

    # 失敗靜默（壞參數／不存在的 store 一律回 []，絕不 raise）
    assert query_recall(ALICE, "", {"dormant"}, store_dir=store) == []
    assert query_recall("", "心累", {"dormant"}, store_dir=store) == []
    assert query_recall(ALICE, "心累", set(), store_dir=store) == []
    assert query_recall(ALICE, "心累", store_dir=store / "nope") == []

    # 0 寫入
    assert _snapshot(store) == before


def test_recall_hit_presentation_layer_unchanged():
    """呈現層逐字不動（契約 §3.1 語氣隔離）。"""
    hit = {"node_id": "d", "node_type": "belief", "content": "舊信念。",
           "lifecycle_state": "dormant", "lineage_path": "d", "superseded_by": None}
    assert format_recall_hit(hit).startswith(DORMANT_TONE_PREFIX)
    sup = dict(hit, lifecycle_state="superseded", superseded_by="b")
    assert format_recall_hit(sup, "B。") == "我以前覺得「舊信念。」，但後來發現「B。」。"


# ══ ⑤ overlay additive ＋ 旗標不外洩 ═════════════════════════════════


def test_overlay_is_additive_for_non_quarantined_nodes(tmp_path):
    """非隔離節點的 dict **不多任何 key**（additive 成立）。"""
    nodes = [{"node_id": "n1", "node_type": "value", "content": "x"}]
    out = apply_lifecycle_overlay(nodes, [
        {"node_id": "n1", "lifecycle_state": "weakening"},
    ])
    assert set(out[0].keys()) == {"node_id", "node_type", "content", "lifecycle_state"}
    assert "quarantined" not in out[0]

    # 零事件 ⇒ 逐位元 no-op
    assert apply_lifecycle_overlay(nodes, []) == nodes


def test_quarantine_ids_use_latest_event(tmp_path):
    """隔離判定以**最新事件**為準（last-wins，與 overlay 的狀態語意一致）。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n1", "belief", "x")])

    _write_events(store, [
        _quarantine_event("n1", "dormant"),
        {"node_id": "n1", "lifecycle_state": "weakening"},
    ])
    assert load_quarantined_node_ids(store) == set(), "最新事件不是隔離 ⇒ 不算隔離"

    _write_events(store, [
        {"node_id": "n1", "lifecycle_state": "weakening"},
        _quarantine_event("n1", "dormant"),
    ])
    assert load_quarantined_node_ids(store) == {"n1"}


def test_node_dict_stays_engine_constructible(tmp_path):
    """🔴 節點 dict 的**形狀**必須維持引擎可吃（``ElevationNode(**n)``）。

    這是隔離旗標「不掛在節點 dict 上」的結構性理由：
    ``elevation_adapter._rebuild_engine_for`` 以 ``ElevationNode(**n)`` 建引擎，
    而 ``ElevationNode`` 是外部套件的 frozen dataclass（欄位固定）——節點 dict
    多一個 key 就會讓整條昇華路徑爆掉（SOUL-BUG-010 的 M4 守衛轉紅、M3 假綠）。
    """
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-a", "belief", "還在用的信念。")])
    _write_events(store, [_quarantine_event("n-a", "weakening")])

    for node in load_elevation_nodes(store):
        ElevationNode(**node)   # 不得 TypeError


def test_quarantine_flag_does_not_leak_into_other_consumers(tmp_path):
    """污染守衛：隔離資訊**不**洩漏到其他消費端（``project_emergent`` 輸出逐位元相同）。

    設計：把隔離事件的 ``lifecycle_state`` 設成 ``weakening``——這是唯一能讓
    被隔離節點**同時**進入主動投影的態 ⇒ 若隔離標記外洩，投影輸出就會多出 key。
    """
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-a", "belief", "還在用的信念。"),
        _node("n-b", "belief", "被隔離但仍在投影範圍的信念。"),
    ])
    _write_events(store, [_quarantine_event("n-b", "weakening")])

    projected = project_emergent(ALICE, store_dir=store, record_trace=False)
    assert [p["node_id"] for p in projected] == ["n-a", "n-b"]
    for p in projected:
        assert set(p.keys()) == {"node_id", "node_type", "content"}, (
            "投影輸出洩漏了隔離標記：%s" % sorted(p.keys())
        )
        assert "quarantined" not in json.dumps(p, ensure_ascii=False)
    # 隔離資訊**確實**存在（證明上面不是因為隔離判定沒生效而綠）
    assert load_quarantined_node_ids(store) == {"n-b"}


def test_quarantine_flag_does_not_leak_into_recall_hits(tmp_path):
    """旗標不進回憶結果（六鍵，形狀不變）。"""
    store = tmp_path / "s"
    _write_nodes(store, [_node("n-a", "belief", "深夜加班。", lifecycle_state="dormant")])
    _write_events(store, [])

    hit = query_recall(ALICE, "深夜加班", store_dir=store)[0]
    assert set(hit.keys()) == HIT_KEYS
    assert "quarantined" not in hit


# ══ ⑥ go/no-go 判定 ════════════════════════════════════════════════


def _synthetic_pool_and_probes():
    """合成一個**四項全過**的回憶池與探針集（供 go/no-go 守衛用）。"""
    nodes = [
        _node("n-d%d" % i, "belief", "主題 %d 的舊信念內容" % i,
              lifecycle_state="dormant") for i in range(5)
    ] + [
        _node("n-s", "belief", "被取代的舊信念", lifecycle_state="superseded",
              superseded_by="n-new"),
    ]
    probes = [(ALICE, ["主題"])] * 4 + [(ALICE, ["不相干的詞"])] * 16
    return nodes, probes


def test_go_no_go_returns_go_when_all_four_checks_pass():
    """🔴 MUT-H5 的對照組：四項全過**確實**回 GO（證明門檻不是恆 False）。"""
    nodes, probes = _synthetic_pool_and_probes()
    summary = pool_summary(nodes)
    hit = hit_rate(nodes, probes)
    verdict = go_no_go(summary, hit)

    assert summary["recallable"] == 6
    assert summary["recallable_by_state"]["superseded"] == 1
    assert hit["probes_total"] == 20
    assert hit["probes_hit"] == 4
    assert hit["hit_rate"] == 0.20
    assert verdict["verdict"] == "GO", verdict["reasons"]
    assert verdict["reasons"] == []
    assert all(c["pass"] for c in verdict["checks"].values())


def test_go_no_go_no_go_when_pool_is_empty():
    """池為空 ⇒ NO-GO（且理由指向池不足）。"""
    summary = pool_summary([])
    hit = hit_rate([], [(ALICE, ["主題"])])
    verdict = go_no_go(summary, hit)

    assert verdict["verdict"] == "NO-GO"
    assert verdict["checks"]["recallable_pool"]["pass"] is False
    assert any("可回憶池" in r for r in verdict["reasons"])


def test_go_no_go_no_go_when_pool_below_threshold():
    """池差一顆 ⇒ NO-GO（門檻邊界）。"""
    nodes, probes = _synthetic_pool_and_probes()
    nodes = nodes[: GO_MIN_RECALLABLE_POOL - 1]   # 4 顆（門檻 5）
    verdict = go_no_go(pool_summary(nodes), hit_rate(nodes, probes))

    assert verdict["verdict"] == "NO-GO"
    assert verdict["checks"]["recallable_pool"]["value"] == GO_MIN_RECALLABLE_POOL - 1
    assert verdict["checks"]["recallable_pool"]["pass"] is False


def test_go_no_go_no_go_when_superseded_missing():
    """池中沒有 superseded ⇒ NO-GO（門檻邊界）。"""
    nodes, probes = _synthetic_pool_and_probes()
    nodes = [n for n in nodes if n["lifecycle_state"] != "superseded"]
    verdict = go_no_go(pool_summary(nodes), hit_rate(nodes, probes))

    assert verdict["verdict"] == "NO-GO"
    assert verdict["checks"]["superseded_in_pool"]["value"] == 0
    assert verdict["checks"]["superseded_in_pool"]["pass"] is False


def test_go_no_go_no_go_when_hit_rate_below_threshold():
    """命中率未達標 ⇒ NO-GO（門檻邊界）。"""
    nodes, probes = _synthetic_pool_and_probes()
    probes = [(ALICE, ["主題"])] * 2 + [(ALICE, ["不相干的詞"])] * 18   # 2/20 = 10%
    verdict = go_no_go(pool_summary(nodes), hit_rate(nodes, probes))

    assert hit_rate(nodes, probes)["hit_rate"] < GO_MIN_HIT_RATE
    assert verdict["verdict"] == "NO-GO"
    assert verdict["checks"]["hit_rate"]["pass"] is False


def test_go_no_go_no_go_when_quarantined_node_was_hit():
    """命中了隔離節點 ⇒ NO-GO（硬不變式：命中必須 100% 非隔離）。"""
    nodes, probes = _synthetic_pool_and_probes()
    # 把其中一顆標成隔離（守門生效 ⇒ 實際不會被命中；此處直接餵進 hit dict，
    # 驗證判定層本身會擋）。
    hit = hit_rate(nodes, probes)
    hit["match_pairs_quarantined"] = 1
    hit["non_quarantined_hit_ratio"] = 0.5
    verdict = go_no_go(pool_summary(nodes), hit)

    assert verdict["verdict"] == "NO-GO"
    assert verdict["checks"]["non_quarantined_only"]["pass"] is False


def test_hit_rate_counts_quarantined_matches_as_counterfactual(tmp_path):
    """``hit_rate`` 的反事實計數：若無隔離守門，那些節點會被命中。

    守門生效時實際命中的是 0 顆隔離節點；反事實數字則告訴我們「守門擋掉多少」。
    """
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-bad", "belief", "深夜加班很辛苦", lifecycle_state="dormant"),
    ])
    _write_events(store, [_quarantine_event("n-bad")])

    nodes = load_elevation_nodes(store)
    hit = hit_rate(nodes, [(ALICE, ["加班"])], quarantined_ids=load_quarantined_node_ids(store))

    assert hit["probes_total"] == 1
    assert hit["probes_hit"] == 0, "隔離節點不得被算成命中"
    assert hit["match_pairs"] == 0
    assert hit["match_pairs_quarantined"] == 1, "反事實計數應顯示守門擋掉了它"
    assert hit["non_quarantined_hit_ratio"] == 1.0


def test_pool_summary_reports_pool_maturity(tmp_path):
    """``pool_summary`` 的成熟度欄位（隔離／pattern 排除／可回憶池）。"""
    store = tmp_path / "s"
    _write_nodes(store, [
        _node("n-pat", "pattern", "模式內容", lifecycle_state="dormant"),
        _node("n-bad", "belief", "隔離污染內容", lifecycle_state="dormant"),
        _node("n-ok", "belief", "自然老化內容", lifecycle_state="dormant"),
    ])
    _write_events(store, [_quarantine_event("n-bad")])

    summary = pool_summary(load_elevation_nodes(store),
                           load_quarantined_node_ids(store))
    assert summary["total_nodes"] == 3
    assert summary["quarantined"] == 1
    assert summary["excluded_pattern"] == 1
    assert summary["blocked_by_quarantine"] == 1
    assert summary["recallable"] == 1
    assert summary["recallable_by_state"]["dormant"] == 1
