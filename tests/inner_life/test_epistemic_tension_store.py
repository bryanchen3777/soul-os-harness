"""SIM-EPIS-ACTIVATE-READY：張力狀態承載（append-only）的守衛測試。

守衛對應（工單 §3.4 變異實驗）：

- **守衛 1**（append-only ＋ 末筆可見 ＋ 掉電不遺失）⇒ 變異 1（退回單一 JSON
  覆寫）、變異 2（append 不 flush/fsync）必須轉紅。
- **守衛 2**（多執行者併寫不互相覆寫）⇒ 變異 1 必須轉紅。
- **守衛 3**（舊檔遷移：改名保留、**不得刪除**）⇒ 變異 3（遷移時刪除舊檔）必須轉紅。

🔴 本檔**不使用** ``subprocess``／``os.spawn*``／``os.system``／``os.popen``
（R5 白名單大小鎖死）；併寫以**執行緒**模擬兩個寫入者（各持自己的檔案句柄）。
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
from pathlib import Path

import pytest

from src.inner_life import epistemic_action as ea


def _tension(
    agent_id: str = "agent_x",
    tension_id: str = "t-1",
    kind: str = ea.TENSION_KIND_RELATIONAL,
) -> ea.Tension:
    """一條最小張力（原料齊全 ⇒ 可 to_dict/from_dict 往返）。"""
    return ea.Tension(
        tension_id=tension_id,
        kind=kind,
        agent_id=agent_id,
        subject="沈默",
        summary="兩天沒講話了",
        tags=("tag_a",),
        world_keys=(),
        source=ea.SOURCE_TEMPORAL_PHENOMENOLOGY,
        created_at=1000.0,
        utterance="",
        session_context="",
        last_interaction_ts=900,
        bond=ea.BondSnapshot(True, "P2_relational_band", ("warm",)),
    )


# ── 守衛 1：append-only ＋ 末筆可見 ＋ 掉電不遺失 ──────────────────────────
def test_guard1_tension_store_is_append_only_and_durable(tmp_path, monkeypatch):
    """N 筆寫入 ⇒ 檔內 N 行（不再互相覆蓋）；fold 出來的最後狀態正確；append 必須有持久化屏障。"""
    store = tmp_path / "tensions"

    # 🔴 2026-10-22（INFRA-CONCURRENT-APPEND-MUTEX）：append 路徑已改走
    #    ``src.fsio.atomic_append``，持久化屏障依平台不同
    #    （Windows：``FlushFileBuffers``；POSIX：``os.fsync``）。
    #    ⇒ **不變式不變**（append 必須有掉電不遺失的持久化屏障），
    #    只是**觀察點跟著實作走**（不是放寬：屏障沒被呼叫照樣轉紅）。
    durability_calls: list[object] = []
    real_fsync = os.fsync

    def _spy_fsync(fd):
        durability_calls.append(("fsync", fd))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _spy_fsync)
    if os.name == "nt":
        from src.fsio import atomic_append

        real_flush = atomic_append._k32.FlushFileBuffers

        def _spy_flush(handle):
            durability_calls.append(("flush", handle))
            return real_flush(handle)

        monkeypatch.setattr(atomic_append._k32, "FlushFileBuffers", _spy_flush)

    n = 5
    for index in range(n):
        assert ea.upsert_open_tension(_tension(tension_id=f"t-{index}"), store) is True
    # 末筆狀態變更：t-0 已閉合
    assert ea.mark_tension_resolved(_tension(tension_id="t-0"), store, now=2000.0) is True

    path = ea.tension_store_path(store)
    assert path.is_file(), "承載檔必須存在"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n + 1, (
        f"append-only：{n + 1} 筆寫入後檔內應有 {n + 1} 行（每筆一行），實得 {len(lines)} 行"
    )

    events = [json.loads(line) for line in lines]
    for event in events:
        assert set(event.keys()) == set(ea.TENSION_EVENT_FIELDS), (
            f"事件欄位形狀必須封閉：{sorted(event.keys())}"
        )
        assert event["kind"] in ea.TENSION_EVENT_KINDS
        assert event["agent_id"] == "agent_x"

    # fold：每個 tension_id 取最後一筆（t-0 resolved，其餘 open）
    records = {
        record["tension"]["tension_id"]: record
        for record in ea.load_tension_records("agent_x", store)
    }
    assert set(records) == {f"t-{index}" for index in range(n)}
    assert records["t-0"]["status"] == "resolved", "fold 必須取最後一筆（末筆可見）"
    assert records["t-1"]["status"] == "open"

    # 末筆可見：獨立檔案句柄讀到的最後一行就是最後一筆事件
    with open(path, "r", encoding="utf-8") as handle:
        last_line = handle.read().splitlines()[-1]
    assert json.loads(last_line)["kind"] == ea.TENSION_EVENT_RESOLVED

    # 掉電不遺失：append 路徑必須有持久化屏障（Windows=FlushFileBuffers / POSIX=os.fsync）
    assert durability_calls, "append 路徑必須有持久化屏障（掉電不遺失）"


# ── 守衛 2：多執行者併寫不互相覆寫 ─────────────────────────────────────────
def test_guard2_concurrent_appends_keep_every_record(tmp_path):
    """兩個寫入者同時 append ⇒ 每一筆都在檔內（單一 JSON 覆寫會丟掉大半）。"""
    store = tmp_path / "tensions"
    workers = 2
    per_worker = 30
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def _worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=10)
            for index in range(per_worker):
                ea.upsert_open_tension(
                    _tension(agent_id=f"agent_{worker_id}", tension_id=f"w{worker_id}-{index}"),
                    store,
                )
        except BaseException as exc:  # noqa: BLE001 — 記下來給斷言看
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"併寫執行緒不可失敗：{errors!r}"
    assert not any(thread.is_alive() for thread in threads), "併寫執行緒必須收束"

    total = workers * per_worker
    lines = ea.tension_store_path(store).read_text(encoding="utf-8").splitlines()
    assert len(lines) == total, (
        f"併寫：{total} 筆（{workers} 執行者 × {per_worker} 筆）必須全部保留，實得 {len(lines)} 行"
    )
    ids = {json.loads(line)["tension_id"] for line in lines}
    assert len(ids) == total, "併寫後每一筆的 tension_id 都必須可見"
    for worker_id in range(workers):
        got = {t.tension_id for t in ea.load_open_tensions(f"agent_{worker_id}", store)}
        assert got == {f"w{worker_id}-{index}" for index in range(per_worker)}


# ── 守衛 3：舊檔遷移（改名保留、不得刪除）──────────────────────────────────
def test_guard3_legacy_store_is_migrated_and_never_deleted(tmp_path):
    """舊檔存在 ⇒ 一次性折算成事件；舊檔**改名保留**（.migrated），**不得刪除**。"""
    store = tmp_path / "tensions"
    store.mkdir(parents=True)
    legacy = ea.legacy_tension_store_path(store)
    legacy.write_text(
        json.dumps(
            {
                "agents": {
                    "agent_x": [
                        {
                            "tension": _tension(tension_id="legacy-1").to_dict(),
                            "status": "open",
                            "resolved_at": None,
                        },
                        {
                            "tension": _tension(tension_id="legacy-2").to_dict(),
                            "status": "resolved",
                            "resolved_at": 1234.5,
                        },
                    ]
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert legacy.is_file()

    records = ea.load_tension_records("agent_x", store)
    assert len(records) == 2, "舊檔的每一條紀錄都必須折算過來"
    statuses = {record["tension"]["tension_id"]: record["status"] for record in records}
    assert statuses == {"legacy-1": "open", "legacy-2": "resolved"}

    # 舊檔**改名保留**（不得刪除）
    assert not legacy.exists(), "舊檔必須被改名（不是被刪除）"
    migrated = store / (ea.TENSION_STORE_FILENAME + ea.TENSION_STORE_MIGRATED_SUFFIX)
    assert migrated.is_file(), "舊檔必須以 .migrated 保留在原目錄"
    assert migrated.stat().st_size > 0, "保留的舊檔不可是空檔"

    # JSONL 與舊檔等價
    events = [
        json.loads(line)
        for line in ea.tension_store_path(store).read_text(encoding="utf-8").splitlines()
    ]
    assert len(events) == 2

    # 冪等：重複讀取**不**重複折算
    ea.load_tension_records("agent_x", store)
    assert len(
        ea.tension_store_path(store).read_text(encoding="utf-8").splitlines()
    ) == 2, "遷移必須是一次性的"


# ── 漂移守衛：Tension dataclass 的對外形狀不得改動 ──────────────────────────
def test_tension_dataclass_shape_is_unchanged():
    """既有測試與漂移守衛依賴 ``Tension`` 的對外形狀 ⇒ 鎖死欄位順序與名稱。"""
    assert [f.name for f in dataclasses.fields(ea.Tension)] == [
        "tension_id",
        "kind",
        "agent_id",
        "subject",
        "summary",
        "tags",
        "world_keys",
        "source",
        "created_at",
        "utterance",
        "session_context",
        "last_interaction_ts",
        "bond",
    ]


# ── 漂移守衛：事件型別是封閉集合 ───────────────────────────────────────────
def test_tension_event_kinds_are_a_closed_set():
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


# ── 守衛：舊檔不存在時不得憑空生出事件檔 ───────────────────────────────────
def test_store_read_is_side_effect_free_when_empty(tmp_path):
    store = tmp_path / "tensions"
    assert ea.load_tension_records("agent_x", store) == []
    assert ea.load_open_tensions("agent_x", store) == []
    assert not ea.tension_store_path(store).exists(), "純讀取不可建立承載檔"
