"""SIM-EPIS-ACTIVATE-READY：排程自主觸發的守衛測試。

守衛對應（工單 §3.4 變異實驗）：

- **守衛 4**（排程觸發：無任何對話流量時 tick 仍回傳落在張力窗內的 agent）
  ⇒ 變異 4（排程 tick 不觸發、只留訊息路徑）必須轉紅。
- **守衛 5**（不重複觸發：訊息路徑 ＋ 排程路徑共用節流；且節流**逐 agent**）
  ⇒ 變異 5（節流退回全域）必須轉紅。
- **守衛 6**（零發訊：排程路徑對用戶的發訊數 = 0）⇒ 變異 7（排程路徑觸發發訊）
  必須轉紅。
- **守衛 7**（旗標缺省關閉 ⇒ 排程路徑**不啟動任何執行緒**）
  ⇒ 變異 6（排程路徑繞過旗標）必須轉紅。

🔴 本檔**不使用** ``subprocess``／``os.spawn*``／``os.system``／``os.popen``
（R5 白名單大小鎖死）；執行緒只用於**本行程內**的觸發攔截。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.inner_life import epistemic_tension_scheduler as sched
from src.inner_life import piggyback_queue as pq
from src.llm import proxy as px


def _write_relationship(
    root: Path,
    agent_id: str,
    *,
    last_interaction_ts: int,
    band: str = "known",
    interaction_count: int = 42,
) -> None:
    """寫一份 relationships.json（**唯一**的關係狀態來源）。"""
    agent_dir = root / "soul" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "relationships.json").write_text(
        json.dumps(
            {
                "others": {
                    "user_bryan": {
                        "relational_band": band,
                        "interaction_count": interaction_count,
                        "last_interaction_at": datetime.fromtimestamp(
                            last_interaction_ts, tz=timezone.utc
                        ).isoformat(),
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _empty_memory_db(root: Path) -> int:
    """建立一個**沒有任何訊息**的記憶庫，回傳訊息數（= 0）。"""
    db_path = root / "memory.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
            "speaker TEXT, content TEXT, timestamp TEXT)"
        )
        conn.commit()
        count = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    finally:
        conn.close()
    return count


@pytest.fixture
def publish_probe(monkeypatch):
    """攔截事件總線的對外發佈（AGENCY_TRIGGER／AGENT_SPEAK ＝ 對用戶發訊）。"""
    from src.eventbus.bus import SoulEventBus
    from src.eventbus.schema import EventType

    published: list[object] = []
    user_facing = {EventType.AGENCY_TRIGGER, EventType.AGENT_SPEAK}

    async def _fake_publish(self, event):
        published.append(getattr(event, "event_type", None))

    monkeypatch.setattr(SoulEventBus, "publish", _fake_publish)
    return {"published": published, "user_facing": user_facing}


# ── 漂移守衛：張力窗常數必須與上游三態現象學一致 ───────────────────────────
def test_tension_window_constants_match_upstream():
    """排程模組自帶一份張力窗常數（上游的是私有的、不可 import）⇒ 漂移由本測試釘死。"""
    from src.soul import temporal_phenomenology as tp

    assert sched.CALM_MAX_ELAPSED_SEC == tp._CALM_MAX_ELAPSED_SEC
    assert sched.TENSION_MAX_ELAPSED_SEC == tp._TENSION_MAX_ELAPSED_SEC
    assert sched.CALM_MAX_ELAPSED_SEC == 24 * 3600
    assert sched.TENSION_MAX_ELAPSED_SEC == 7 * 24 * 3600
    assert sched.STATE_TENSION == tp.STATE_TENSION


# ── 漂移守衛：節流常數必須沿用 proxy 的單一來源 ─────────────────────────────
def test_throttle_interval_is_shared_with_proxy():
    """節流常數**沿用** ``proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS``（不得另立一套）。"""
    assert sched.min_interval_seconds() == px.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS


# ── 守衛 4：排程觸發（無對話流量也能觸發）──────────────────────────────────
def test_guard4_tick_finds_tension_agent_without_any_conversation(monkeypatch):
    """記憶庫訊息數 = 0（無任何對話流量）時，tick 仍回傳落在張力窗內的 agent。"""
    from src.paths import data_root

    root = Path(data_root())
    now = int(time.time())
    _write_relationship(root, "agent_x", last_interaction_ts=now - 2 * 24 * 3600)
    _write_relationship(root, "agent_calm", last_interaction_ts=now - 60)
    _write_relationship(root, "agent_over", last_interaction_ts=now - 30 * 24 * 3600)

    message_count = _empty_memory_db(root)
    assert message_count == 0, "本測試的前提就是「沒有任何對話流量」"

    # 註冊表固定 ⇒ 判定對象可預期（另一支測試覆蓋目錄掃描回退路徑）。
    monkeypatch.setattr("src.soul.motive.get_agent_ids", lambda: frozenset({"agent_x"}))

    assert sched.tick(now) == ["agent_x"], "兩天前互動的 agent 必須落在張力窗內"

    # 邊界：1 分鐘前互動（CALM）與 30 天前互動（已過窗）都不觸發。
    monkeypatch.setattr(
        "src.soul.motive.get_agent_ids",
        lambda: frozenset({"agent_x", "agent_calm", "agent_over"}),
    )
    assert sched.tick(now) == ["agent_x"], "只有落在 [24h, 7d) 窗內的 agent 會被回傳"


def test_guard4_resolve_agents_falls_back_to_relationship_directory(monkeypatch):
    """註冊表為空時，agent 清單由**生產資料面**（relationships.json）決定。"""
    from src.paths import data_root

    root = Path(data_root())
    _write_relationship(root, "agent_scan", last_interaction_ts=int(time.time()))
    monkeypatch.setattr("src.soul.motive.get_agent_ids", lambda: frozenset())
    assert "agent_scan" in sched.resolve_agents()


# ── 守衛 5：不重複觸發（兩個觸發點共用節流；且逐 agent）────────────────────
def test_guard5_message_and_scheduled_paths_share_one_throttle_slot(monkeypatch):
    """同一 agent 在同一節流窗內 ⇒ 背景行動只跑一次；且節流**逐 agent**。"""
    monkeypatch.setenv(pq.FEATURE_ENV, "1")

    calls: list[tuple[str, str]] = []
    finished = threading.Event()

    def _fake_run(agent_id, utterance, now_ts, last_interaction_ts):
        # 鏡像真實實作的 finally（running 歸零）⇒ 測的是**節流窗**，不是 running 旗標。
        try:
            calls.append((agent_id, utterance))
        finally:
            px._BACKGROUND_ACTION_STATE.get(agent_id, {})["running"] = False
            finished.set()

    monkeypatch.setattr(px, "_run_background_epistemic_action", _fake_run)
    px._BACKGROUND_ACTION_STATE.clear()

    # ① 訊息路徑觸發
    px._maybe_trigger_background_epistemic_action("agent_x", "喂，最近好嗎", last_interaction_ts=100)
    assert finished.wait(5), "訊息路徑必須真的跑起背景行動"
    finished.clear()
    assert len(calls) == 1

    # ② 同一節流窗內，排程路徑**不得**再觸發
    started = px._maybe_trigger_background_epistemic_action_scheduled(
        "agent_x", last_interaction_ts=100
    )
    assert started is False, "同一 agent 在同一節流窗內只跑一次"
    assert not finished.wait(0.3), "排程路徑不得重複觸發同一 agent"
    assert len(calls) == 1

    # ③ 節流**逐 agent**：另一隻 agent 不受 agent_x 排隊影響
    px._BACKGROUND_ACTION_STATE.clear()
    calls.clear()
    px._maybe_trigger_background_epistemic_action("agent_x", "喂", last_interaction_ts=100)
    assert finished.wait(5)
    finished.clear()
    started_other = px._maybe_trigger_background_epistemic_action_scheduled(
        "agent_y", last_interaction_ts=100
    )
    assert started_other is True, "節流必須逐 agent（全域節流會讓多角色互相排隊）"
    assert finished.wait(5)
    assert [call[0] for call in calls] == ["agent_x", "agent_y"]


# ── 守衛 6：零發訊（排程路徑對用戶的發訊數 = 0）────────────────────────────
def test_guard6_scheduled_pass_emits_no_user_facing_message(
    monkeypatch, tmp_path, publish_probe
):
    """排程路徑跑完一輪後，**事件總線攔截計數 = 0**（對用戶發訊數 = 0）。"""
    from src.paths import data_root

    root = Path(data_root())
    now = int(time.time())
    _write_relationship(root, "agent_x", last_interaction_ts=now - 2 * 24 * 3600)
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    monkeypatch.setattr("src.soul.motive.get_agent_ids", lambda: frozenset({"agent_x"}))

    finished = threading.Event()
    real_run = px._run_background_epistemic_action

    def _recording_run(*args, **kwargs):
        try:
            real_run(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(px, "_run_background_epistemic_action", _recording_run)
    px._BACKGROUND_ACTION_STATE.clear()

    fired = sched.run_scheduled_pass(
        now=now, agents=["agent_x"], tension_store_dir=tmp_path / "tensions"
    )
    assert fired == ["agent_x"], "排程路徑必須真的觸發了背景行動"
    assert finished.wait(15), "背景行動必須跑完"

    user_facing = [
        event for event in publish_probe["published"] if event in publish_probe["user_facing"]
    ]
    assert len(user_facing) == 0, f"對用戶發訊數必須為 0，實得 {len(user_facing)}"
    assert publish_probe["published"] == [], (
        f"排程路徑不得發佈任何事件，實得 {publish_probe['published']}"
    )


# ── 守衛 7：旗標缺省關閉 ⇒ 不啟動任何執行緒 ────────────────────────────────
def test_guard7_flag_off_starts_no_thread(monkeypatch, tmp_path):
    """旗標缺省關閉 ⇒ 排程路徑**不啟動任何執行緒**（生產零行為變化）。"""
    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    sched.stop_scheduler_thread()

    assert sched.feature_enabled() is False
    assert sched.ensure_scheduler_started() is False
    assert sched.scheduler_thread_running() is False
    assert not any(
        thread.name == sched.THREAD_NAME for thread in threading.enumerate()
    ), "旗標關閉時不得存在排程執行緒"

    # 旗標開啟才啟動（空 agent 清單 ⇒ 不碰任何資料）
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    try:
        assert sched.start_scheduler_thread(agents=[], tension_store_dir=str(tmp_path)) is True
        assert sched.scheduler_thread_running() is True
        assert any(thread.name == sched.THREAD_NAME for thread in threading.enumerate())
    finally:
        sched.stop_scheduler_thread()
    assert sched.scheduler_thread_running() is False


def test_guard7_flag_off_means_scheduled_pass_is_a_no_op(monkeypatch, tmp_path):
    """旗標關閉 ⇒ ``run_scheduled_pass`` 不判定、不觸發（回傳空清單）。

    ⚠️ 前提必須成立：該 agent **確實**落在張力窗內（否則這支測試會因為
    「本來就沒東西可觸發」而假綠，變異「排程路徑繞過旗標」就轉不了紅）。
    """
    from src.paths import data_root

    root = Path(data_root())
    now = int(time.time())
    _write_relationship(root, "agent_x", last_interaction_ts=now - 2 * 24 * 3600)

    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    triggered: list[tuple[str, int]] = []
    assert (
        sched.run_scheduled_pass(
            now=now,
            agents=["agent_x"],
            tension_store_dir=tmp_path / "tensions",
            trigger=lambda agent_id, last_ts: triggered.append((agent_id, last_ts)) or True,
        )
        == []
    )
    assert triggered == [], "旗標關閉時排程路徑不得觸發任何 agent"

    # 反向證明：同一組參數在旗標**開啟**時確實會觸發（⇒ 上面的空結果源自旗標，
    # 不是因為「本來就沒東西可觸發」）。
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    assert (
        sched.run_scheduled_pass(
            now=now,
            agents=["agent_x"],
            tension_store_dir=tmp_path / "tensions",
            trigger=lambda agent_id, last_ts: triggered.append((agent_id, last_ts)) or True,
        )
        == ["agent_x"]
    )
    assert [agent_id for agent_id, _ in triggered] == ["agent_x"]

