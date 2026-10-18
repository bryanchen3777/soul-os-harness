# -*- coding: utf-8 -*-
"""SOUL-BUG-006 Step 4：夢境端 A-i —— 消費端依 event_id 反查夢境正文。

背景（Owner 2026-10-17 授權的架構）
--------------------------------
夢境事件的 `extras` 實測只有 `all_agents_count` / `target_agent_id`
—— 純排程統計，**沒有敘事**。而夢境正文是在 `write_dream()` 內部才由 LLM
生成並寫入 `data/soul/<dreamer>/diary/<日期>.jsonl`（該筆帶
`inner_life_event_id`，M5.4-5.4）。

Step 1 把消費端改成 fail-closed 之後，夢境就**每次都被整批攔截**，
dream 的供給歸零。本檔釘住解法：消費端依 `event_id` 反查剛落盤的那筆正文。

生產資料實測（2026-10-17）：782 個 diary 檔、377 筆 dream 紀錄，
其中 **288 筆帶 `inner_life_event_id`** —— 反查在真實資料上成立。

邊界（與本檔同樣重要）
--------------------
`InnerLifeWriter` 是 Append-Only 唯一權威、無 update/patch API。
因此反查**只在 consume 當下解析**，事件物件一個字都不許被改。
注意 `SubmissionGate.verify()` 會 `writer.get_event(event_id)` 重新撈取
canonical 事件，所以「在呼叫端改記憶體中的 extras」這條路是**死的** ——
反查必須發生在 adapter 內部。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.inner_life import InnerLifeWriter, Provenance, TRIGGER_TYPE_DREAM_DREAM
from src.inner_life.elevation_adapter import (
    _DREAM_LOOKBACK_DAYS,
    _dream_narrative_from_diary,
    _resolve_narrative,
    run_elevation,
)

DREAM_TEXT = "薄暗い屋根裏、もう消えかけてる夕日の中で、誰かが小さく笑って"


@pytest.fixture()
def iso_root(tmp_path, monkeypatch):
    """獨立資料根；結束後還原。"""
    import src.paths as paths_mod

    root = tmp_path / "data"
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(root))
    paths_mod.reset_data_root()
    yield root
    paths_mod.reset_data_root()


def _make_event(writer: InnerLifeWriter, dreamer: str = "agent_rem", **extras):
    return writer.create_event(
        provenance=Provenance(
            trigger_type=TRIGGER_TYPE_DREAM_DREAM,
            actor_id=dreamer,
            source_system="dream",
            extras=extras or {"target_agent_id": "agent_yua", "all_agents_count": "10"},
        )
    )


def _write_dream_entry(root, dreamer: str, event_id: str, *, content: str = DREAM_TEXT,
                       day_offset: int = 0, slot: str = "dream") -> None:
    day = (datetime.now() - timedelta(days=day_offset)).strftime("%Y-%m-%d")
    path = root / "soul" / dreamer / "diary" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(), "slot": slot, "content": content, "source": "llm"}
    if event_id:
        entry["inner_life_event_id"] = event_id
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── 反查本體 ───────────────────────────────────────────────────────


def test_lookup_finds_the_dream_text_by_event_id(iso_root):
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", event.event_id)
    assert _dream_narrative_from_diary(event) == DREAM_TEXT


def test_lookup_returns_empty_when_event_id_does_not_match(iso_root):
    """🔴 不得用 target_agent_id 之類的 metadata 拼假敘事。"""
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", "some_other_event_id")
    assert _dream_narrative_from_diary(event) == ""


def test_lookup_ignores_non_dream_slots(iso_root):
    """同 event_id 但 slot 不是 dream（例如 diary）⇒ 不採用。"""
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", event.event_id, content="這是日間記事", slot="night")
    assert _dream_narrative_from_diary(event) == ""


def test_lookup_ignores_a_near_miss_event_id(iso_root):
    """🔴 精準反例：另一筆紀錄的 id **以目標 id 為前綴**。

    為什麼要這樣構造：`_dream_narrative_from_diary()` 有一道
    `if event_id not in line: continue` 的子字串快篩。若反例的 id 與目標
    id **完全無關**，那行根本不會進入 JSON 比對 —— 測試會因為「被快篩擋掉」
    而變綠，即使 JSON 比對根本壞掉也看不出來（這是變異實驗 MUT-02 抓到的）。
    只有「子字串快篩擋不住、但欄位不等」才能真正驗到欄位比對。
    """
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", event.event_id + "_OLD_1",
                       content="另一個事件的夢，不該被採用")
    assert _dream_narrative_from_diary(event) == ""


def test_lookup_is_bounded_and_does_not_reach_far_past(iso_root):
    """🔴 反查必須有界 —— 且**視窗大小不可由實作常數決定**。

    為什麼偏移寫死 10 天而不是 `_DREAM_LOOKBACK_DAYS + 1`：若測試跟著常數
    走，把常數從 3 放大到 99999 時「窗外」也會跟著放大，斷言就永遠成立
    （變異實驗 MUT-04 抓到的空轉）。所以偏移必須是**獨立於實作的常數**，
    並另外釘住視窗上限本身。
    """
    writer = InnerLifeWriter()
    event = _make_event(writer)
    # 視窗上限本身有界（不允許變成「掃全目錄」）
    assert 0 < _DREAM_LOOKBACK_DAYS <= 7, (
        f"反查視窗必須有界且短，實得 {_DREAM_LOOKBACK_DAYS} 天"
    )
    # 寫死 10 天前：不論視窗多大都不該被讀到
    _write_dream_entry(iso_root, "agent_rem", event.event_id, day_offset=10)
    assert _dream_narrative_from_diary(event) == ""
    # 視窗內則找得到
    _write_dream_entry(iso_root, "agent_rem", event.event_id, day_offset=1)
    assert _dream_narrative_from_diary(event) == DREAM_TEXT


def test_lookup_survives_corrupt_json_and_missing_dirs(iso_root):
    """壞行、缺目錄都不得拋例外 —— 反查是附加價值，絕不可讓主路徑失敗。"""
    writer = InnerLifeWriter()
    event = _make_event(writer)

    day = datetime.now().strftime("%Y-%m-%d")
    path = iso_root / "soul" / "agent_rem" / "diary" / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json at all\n", encoding="utf-8")
    assert _dream_narrative_from_diary(event) == ""

    # 根本沒有 diary 目錄
    other = _make_event(writer, dreamer="agent_nobody")
    assert _dream_narrative_from_diary(other) == ""


# ── extras 優先於反查 ──────────────────────────────────────────────


def test_resolve_prefers_extras_narrative_over_lookup(iso_root):
    """有 extras.narrative 時不必反查（省一次檔案 I/O）。"""
    writer = InnerLifeWriter()
    event = _make_event(
        writer,
        target_agent_id="agent_yua",
        all_agents_count="10",
        narrative="這是 extras 帶的敘事",
    )
    _write_dream_entry(iso_root, "agent_rem", event.event_id, content="不該被採用")
    assert _resolve_narrative(event) == "這是 extras 帶的敘事"


def test_non_dream_trigger_never_triggers_the_diary_lookup(iso_root):
    """world/diary 事件不該去掃夢境 diary（避免無謂 I/O 與誤採）。"""
    writer = InnerLifeWriter()
    event = writer.create_event(provenance=Provenance(
        trigger_type="world:rain_started", actor_id="agent_rem",
        source_system="narrative", extras={"world_source": "weather"},
    ))
    _write_dream_entry(iso_root, "agent_rem", event.event_id, content="不該被採用")
    assert _resolve_narrative(event) == ""


# ── 端到端 ────────────────────────────────────────────────────────


def test_dream_event_now_elevates_with_the_real_dream_text(iso_root, tmp_path):
    """🔴 本票驗收面：夢境事件重新產出節點，且 content 就是真實夢境正文。"""
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", event.event_id)

    nodes = run_elevation(event, [], store_dir=tmp_path / "elevation")
    assert len(nodes) == 1, "夢境事件帶反查正文後應產出 pattern"
    assert nodes[0].content == DREAM_TEXT
    assert "all_agents_count" not in nodes[0].content
    assert "target_agent_id" not in nodes[0].content


def test_lookup_does_not_mutate_the_canonical_event(iso_root, tmp_path):
    """🔴 Append-Only 邊界：反查**只解析**，事件物件一個字都不能被改。

    這很重要，因為 `SubmissionGate.verify()` 會 `writer.get_event(event_id)`
    重新撈取 canonical 事件 —— 就算在呼叫端改了記憶體中的 extras 也不會被看見，
    但那仍然會讓「已送出 trace 的事件」與「實際拿來昇華的內容」說不一致。
    """
    writer = InnerLifeWriter()
    event = _make_event(writer)
    _write_dream_entry(iso_root, "agent_rem", event.event_id)

    before = json.dumps(event.provenance.extras, ensure_ascii=False, sort_keys=True)
    _resolve_narrative(event)
    run_elevation(event, [], store_dir=tmp_path / "elevation")
    after = json.dumps(event.provenance.extras, ensure_ascii=False, sort_keys=True)

    assert before == after
    assert "narrative" not in event.provenance.extras


def test_dream_event_without_dream_entry_stays_fail_closed(iso_root, tmp_path):
    """查不到正文 ⇒ 仍舊不建節點（不得為了讓供給回來而放寬 fail-closed）。"""
    writer = InnerLifeWriter()
    event = _make_event(writer)
    assert run_elevation(event, [], store_dir=tmp_path / "elevation") == []
