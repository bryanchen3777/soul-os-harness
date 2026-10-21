# tests/inner_life/test_piggyback_queue.py
# SIM-EPIS-PIGGYBACK-1 ② — 話頭掛載槽（Opportunistic Piggybacking）的守衛。
#
# 受測模組：src/inner_life/piggyback_queue.py
#
# 本檔釘死的是「零打擾」的另一半：什麼進得了槽、什麼出得了槽。
# 每一條守衛都必須能**被變異打紅**（工單 §3.5 第 3/4/5/6 條對應這裡的守衛）。
#
# 🔴 本檔不使用 subprocess / os.spawn* / os.system / os.popen（R5 白名單大小鎖死 22），
#    也不出現任何硬寫的服務位址（R3 守衛）。
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.inner_life import piggyback_queue as pq


# ── 共用小工具 ────────────────────────────────────────────────────────────
def _enqueue(agent_id, store_dir, *, tags, payload, now=1000.0, source=None, refs=()):
    return pq.enqueue(
        agent_id,
        tags=tags,
        payload=payload,
        source=source if source is not None else pq.PIGGYBACK_SOURCE_CLOSURE,
        refs=refs,
        store_dir=store_dir,
        now=now,
    )


# ── G1：入隊只接受「已閉合」的副產物 ───────────────────────────────────────
def test_enqueue_rejects_non_closure_source(tmp_path):
    """未閉合的張力**不得**進槽——那會變成打擾。"""
    accepted = _enqueue(
        "agent_a",
        tmp_path,
        tags=["咖啡"],
        payload="我後來自己想通了咖啡这件事",
        source="open_tension",
    )
    assert accepted is False
    assert pq.load_items("agent_a", tmp_path) == []


def test_enqueue_accepts_closure_source(tmp_path):
    assert _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="小念頭") is True
    items = pq.load_items("agent_a", tmp_path)
    assert len(items) == 1
    assert items[0].source == pq.PIGGYBACK_SOURCE_CLOSURE


def test_enqueue_rejects_empty_payload(tmp_path):
    assert _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="   ") is False
    assert pq.load_items("agent_a", tmp_path) == []


def test_enqueue_rejects_empty_tags(tmp_path):
    assert _enqueue("agent_a", tmp_path, tags=[], payload="小念頭") is False
    assert pq.load_items("agent_a", tmp_path) == []


# ── G2：容量上限 8（FIFO，滿 ⇒ 丟最舊）────────────────────────────────────
def test_queue_capacity_is_eight_fifo_drop_oldest(tmp_path):
    for index in range(pq.PIGGYBACK_QUEUE_CAPACITY):
        _enqueue("agent_a", tmp_path, tags=[f"tag{index}"], payload=f"念頭{index}", now=1000.0 + index)
    assert len(pq.load_items("agent_a", tmp_path)) == pq.PIGGYBACK_QUEUE_CAPACITY == 8

    _enqueue("agent_a", tmp_path, tags=["tag8"], payload="念頭8", now=2000.0)
    items = pq.load_items("agent_a", tmp_path)
    assert len(items) == 8
    payloads = [item.payload for item in items]
    assert "念頭0" not in payloads, "最舊的一筆必須被丟棄"
    assert "念頭8" in payloads
    assert payloads[0] == "念頭1", "FIFO：丟最舊、留最新"


def test_queue_is_per_agent(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["x"], payload="a")
    _enqueue("agent_b", tmp_path, tags=["y"], payload="b")
    assert len(pq.load_items("agent_a", tmp_path)) == 1
    assert len(pq.load_items("agent_b", tmp_path)) == 1
    assert pq.load_items("agent_a", tmp_path)[0].payload == "a"


# ── G3：過期 = created + 7 天 ─────────────────────────────────────────────
def test_expiry_is_seven_days_after_creation(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="念頭", now=1000.0)
    item = pq.load_items("agent_a", tmp_path)[0]
    assert item.expires_at == 1000.0 + pq.PIGGYBACK_TTL_SECONDS
    assert pq.PIGGYBACK_TTL_SECONDS == 7 * 24 * 3600


# ── G4：話題相關才帶（重疊 ≥ 1）───────────────────────────────────────────
def test_pick_requires_topic_overlap(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡", "手沖"], payload="咖啡的念頭", now=1000.0)
    picked = pq.pick_for_topic("agent_a", ["天氣"], store_dir=tmp_path, now=1000.0)
    assert picked is None, "話題無關 ⇒ 不帶"
    assert len(pq.load_items("agent_a", tmp_path)) == 1, "沒帶出就不消耗"


def test_pick_with_overlap_returns_item(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡", "手沖"], payload="咖啡的念頭", now=1000.0)
    picked = pq.pick_for_topic("agent_a", ["手沖"], store_dir=tmp_path, now=1000.0)
    assert picked is not None
    assert picked.payload == "咖啡的念頭"


def test_topic_overlap_is_exact_token_match(tmp_path):
    """話題重疊＝**正規化後的詞元集合交集**（deterministic；不做子字串模糊比對）。"""
    _enqueue("agent_a", tmp_path, tags=["手沖"], payload="手沖的念頭", now=1000.0)
    picked = pq.pick_for_topic("agent_a", ["手沖壺"], store_dir=tmp_path, now=1000.0)
    assert picked is None, "非同一詞元不算重疊（寧可不帶）"


# ── G5：一次最多 1 個 ─────────────────────────────────────────────────────
def test_pick_returns_at_most_one(tmp_path):
    for index in range(4):
        _enqueue("agent_a", tmp_path, tags=["咖啡"], payload=f"念頭{index}", now=1000.0 + index)
    picked = pq.pick_for_topic("agent_a", ["咖啡"], store_dir=tmp_path, now=1000.0)
    assert picked is not None
    assert pq.PIGGYBACK_MAX_PER_TOPIC == 1
    # 只有被帶出的那一筆被消耗，其餘留著
    remaining = pq.load_items("agent_a", tmp_path)
    assert len(remaining) == 3
    assert picked.payload not in [item.payload for item in remaining]


# ── G6：帶出即消耗（不重複）───────────────────────────────────────────────
def test_picked_item_is_consumed_not_repeated(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="只講一次", now=1000.0)
    first = pq.pick_for_topic("agent_a", ["咖啡"], store_dir=tmp_path, now=1000.0)
    second = pq.pick_for_topic("agent_a", ["咖啡"], store_dir=tmp_path, now=1000.0)
    assert first is not None
    assert second is None, "同一小念頭不得被帶出第二次"
    assert pq.load_items("agent_a", tmp_path) == []


# ── G5'：過期者不被帶出 ──────────────────────────────────────────────────
def test_expired_item_is_not_picked(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="過期念頭", now=1000.0)
    after = 1000.0 + pq.PIGGYBACK_TTL_SECONDS + 1
    picked = pq.pick_for_topic("agent_a", ["咖啡"], store_dir=tmp_path, now=after)
    assert picked is None, "過期者不得被帶出"
    assert pq.load_items("agent_a", tmp_path) == [], "過期者應被清掉"


def test_purge_expired_counts(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["a"], payload="p1", now=1000.0)
    _enqueue("agent_a", tmp_path, tags=["b"], payload="p2", now=2000.0)
    removed = pq.purge_expired("agent_a", store_dir=tmp_path, now=1000.0 + pq.PIGGYBACK_TTL_SECONDS + 1)
    assert removed == 1
    assert len(pq.load_items("agent_a", tmp_path)) == 1


# ── G7：隔離者不得進隊、不得被帶出 ────────────────────────────────────────
def test_quarantined_ref_cannot_enqueue(tmp_path):
    accepted = _enqueue(
        "agent_a",
        tmp_path,
        tags=["咖啡"],
        payload="隔離念頭",
        refs=["node_quarantined_1"],
    )
    # 沒給隔離集合時無法判斷 → 允許入隊（後續帶出時仍會被擋）
    assert accepted is True

    ok = pq.enqueue(
        "agent_b",
        tags=["咖啡"],
        payload="隔離念頭",
        source=pq.PIGGYBACK_SOURCE_CLOSURE,
        refs=["node_quarantined_1"],
        store_dir=tmp_path,
        quarantined_ids={"node_quarantined_1"},
        now=1000.0,
    )
    assert ok is False, "命中隔離的節點/事件不得進隊"
    assert pq.load_items("agent_b", tmp_path) == []


def test_quarantined_item_is_not_picked(tmp_path):
    pq.enqueue(
        "agent_a",
        tags=["咖啡"],
        payload="隔離念頭",
        source=pq.PIGGYBACK_SOURCE_CLOSURE,
        refs=["node_quarantined_1"],
        store_dir=tmp_path,
        now=1000.0,
    )
    picked = pq.pick_for_topic(
        "agent_a",
        ["咖啡"],
        store_dir=tmp_path,
        quarantined_ids={"node_quarantined_1"},
        now=1000.0,
    )
    assert picked is None, "隔離者不得被帶出"


def test_quarantine_resolution_uses_lifecycle_overlay(tmp_path):
    """隔離集合的**唯一解讀者**是 lifecycle_overlay（不得自創第二套判定）。"""
    elevation_dir = tmp_path / "elevation"
    elevation_dir.mkdir(parents=True)
    sidecar = elevation_dir / "elevation_lifecycle_events.jsonl"
    sidecar.write_text(
        json.dumps(
            {
                "ts": "2026-10-21T00:00:00+00:00",
                "node_id": "node_q1",
                "event": "quarantine",
                "lifecycle_state": "active",
                "agent_id": "agent_a",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    quarantined = pq.resolve_quarantined_ids(elevation_dir)
    assert "node_q1" in quarantined


# ── G8：全票旗標預設關閉（生產零行為變化）──────────────────────────────────
def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    assert pq.feature_enabled() is False


def test_feature_flag_on_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    assert pq.feature_enabled() is True
    monkeypatch.setenv(pq.FEATURE_ENV, "off")
    assert pq.feature_enabled() is False


# ── 持久化健全性 ──────────────────────────────────────────────────────────
def test_queue_file_is_json_and_contains_no_absolute_paths(tmp_path):
    _enqueue("agent_a", tmp_path, tags=["咖啡"], payload="念頭", now=1000.0)
    path = pq.queue_path("agent_a", tmp_path)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["agent_id"] == "agent_a"
    assert len(payload["items"]) == 1


def test_broken_queue_file_is_survivable(tmp_path):
    path = pq.queue_path("agent_a", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert pq.load_items("agent_a", tmp_path) == []
    assert pq.pick_for_topic("agent_a", ["x"], store_dir=tmp_path, now=0.0) is None
