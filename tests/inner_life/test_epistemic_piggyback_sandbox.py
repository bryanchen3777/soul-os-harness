# tests/inner_life/test_epistemic_piggyback_sandbox.py
# SIM-EPIS-PIGGYBACK-1 — 沙盒端到端（假時鐘 ＋ 記憶副本 ＋ 腳本化 LLM ＋ socket 攔阻）。
#
# 受測模組：src/inner_life/epistemic_action.py ＋ src/inner_life/piggyback_queue.py
#           ＋ src/inner_life/recall_prompt.py ＋ src/llm/proxy.py（接線面）
#
# ── 取證平面（SIM-TIME-001 同款）──────────────────────────────────────
#   1. **假時鐘**：所有「當下」都由測試注入（``now=``），0 sleep、0 等真實時間。
#   2. **記憶副本**：記憶庫寫進 ``tmp_path``（沙盒），生產 ``data/**`` 全程唯讀。
#   3. **0 真實 LLM**：本機制 0 模型呼叫；socket 攔阻確保 0 連線、0 網路。
#   4. **socket 攔阻**：任何對外連線都會被記錄並讓它失敗（不是只擋某個埠）。
#   5. **發訊攔阻**：``SoulEventBus.publish`` 被攔截並計數 ⇒ 「對用戶發訊數」可量。
#
# 🔴 本檔不使用 subprocess / os.spawn* / os.system / os.popen（R5 白名單大小鎖死 22），
#    也不出現任何硬寫的服務位址（R3 守衛）。
from __future__ import annotations

import contextlib
import hashlib
import json
import socket
import threading
import uuid
from pathlib import Path

import pytest

from src.inner_life import epistemic_action as ea
from src.inner_life import piggyback_queue as pq
from src.inner_life import recall_prompt as rp

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 沙盒假時鐘（epoch 秒）——固定值，0 真實等待。
FAKE_NOW = 1_700_000_000

#: 生產資料根（本機 repo 的 ``data/``）。
PRODUCTION_DATA = REPO_ROOT / "data"

#: 生產 sha256 對帳的**範圍**：本票有可能碰到的路徑（寫入面 ＋ 讀取面）。
#:
#: ⚠️ 為什麼不是整個 ``data/**``：生產服務正在跑，``data/memory/*/graph.sqlite``
#: 等檔案被鎖住，整包雜湊會不穩定（實測讀不到）。本檔只對「本票可能觸及的路徑」
#: 取 sha256，並把**讀不到的檔案數**一併回報（不假裝全量都比過）。
PRODUCTION_SCOPE_DIRS = (
    "data/inner_life",
    "data/elevation",
    "data/world",
    "data/conversations",
)
PRODUCTION_SCOPE_GLOBS = (
    "data/soul/*/relationships.json",
    "data/soul/*/memories.jsonl",
)


# ── 攔阻工具 ──────────────────────────────────────────────────────────────
@contextlib.contextmanager
def socket_guard():
    """攔截**所有**對外連線（記錄 ＋ 讓它失敗）。0 連線＝0 真實 LLM／0 網路。"""
    attempts = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def _blocked_connect(self, address, *args, **kwargs):
        attempts.append(address)
        raise OSError(f"[sandbox] 對外連線被攔截：{address!r}")

    def _blocked_connect_ex(self, address, *args, **kwargs):
        attempts.append(address)
        raise OSError(f"[sandbox] 對外連線被攔截：{address!r}")

    def _blocked_create(address, *args, **kwargs):
        attempts.append(address)
        raise OSError(f"[sandbox] 對外連線被攔截：{address!r}")

    socket.socket.connect = _blocked_connect
    socket.socket.connect_ex = _blocked_connect_ex
    socket.create_connection = _blocked_create
    try:
        yield attempts
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.create_connection = real_create


@pytest.fixture
def publish_probe(monkeypatch):
    """攔截事件總線的對外發佈（AGENCY_TRIGGER／AGENT_SPEAK ＝ 對用戶發訊）。"""
    from src.eventbus.bus import SoulEventBus
    from src.eventbus.schema import EventType

    published = []
    user_facing = {EventType.AGENCY_TRIGGER, EventType.AGENT_SPEAK}

    async def _fake_publish(self, event):
        published.append(getattr(event, "event_type", None))

    monkeypatch.setattr(SoulEventBus, "publish", _fake_publish)
    return {"published": published, "user_facing": user_facing}


# ── 沙盒佈景 ──────────────────────────────────────────────────────────────
@pytest.fixture
def sandbox(tmp_path):
    """沙盒佈景：記憶副本 ＋ 張力承載 ＋ 話頭槽 ＋ 昇華圖譜 ＋ 世界日誌。"""
    paths = {
        "root": tmp_path,
        "memory": tmp_path / "memory",
        "tensions": tmp_path / "tensions",
        "queue": tmp_path / "queue",
        "elevation": tmp_path / "elevation",
        "world": tmp_path / "world" / "world_log",
        "soul": tmp_path / "soul",
    }
    for key in ("memory", "tensions", "queue", "elevation", "world", "soul"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


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
            created_at=float(FAKE_NOW),
        )
    )


def _write_relationships(soul_dir: Path, agent_id: str, entry: dict) -> None:
    agent_dir = soul_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "relationships.json").write_text(
        json.dumps({"others": {"user_bryan": entry}}, ensure_ascii=False),
        encoding="utf-8",
    )


def _run(sandbox, **overrides):
    """跑一輪自主求知行動（全部路徑指向沙盒）。"""
    from src.inner_life.trace import NarrativeTraceWriter
    from src.inner_life.writer import InnerLifeWriter

    writer = InnerLifeWriter(
        trace_writer=NarrativeTraceWriter(sandbox["root"] / "trace.jsonl")
    )
    params = dict(
        agent_id="agent_rem",
        utterance="主人買了一個會發熱的東西",
        now=FAKE_NOW,
        memory_dir=sandbox["memory"],
        world_dir=sandbox["world"],
        tension_store_dir=sandbox["tensions"],
        queue_store_dir=sandbox["queue"],
        elevation_store_dir=sandbox["elevation"],
        writer=writer,
    )
    params.update(overrides)
    agent_id = params.pop("agent_id")
    return ea.run_background_action(agent_id, **params)


# ══ 守衛 1：零打擾（對用戶發訊數 = 0）════════════════════════════════════
def test_guard1_zero_user_facing_messages(sandbox, publish_probe):
    """自主求知行動在背景跑完後，**對用戶的發訊數 = 0**（分母＝觸發的行動次數）。"""
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])

    with socket_guard() as attempts:
        closures = _run(sandbox)
        # 再跑一次（證明「重複觸發」也不會吵）
        closures += _run(sandbox)

    actions = 2
    user_facing = [
        evt for evt in publish_probe["published"] if evt in publish_probe["user_facing"]
    ]
    # 🔴 斷言順序：**先斷最具體的性質**（對用戶發訊數 = 0），再斷較寬的性質
    #    （0 對外連線）。否則「變異順帶開了 socket」會先觸發，讓紅燈指錯方向。
    assert len(user_facing) == 0, f"對用戶發訊數必須為 0，實得 {len(user_facing)}"
    assert len(closures) == actions, "兩輪都應該有閉合嘗試"
    assert attempts == [], f"0 對外連線（0 真實 LLM），實得 {len(attempts)}"


def test_guard1b_background_action_publishes_nothing_even_when_tension_closes(sandbox, publish_probe):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    closures = _run(sandbox)
    closed = [c for c in closures if c.closed]
    assert len(closed) == 1, "閉合成功（先證明閉合真的發生過，零打擾才有意義）"
    assert publish_probe["published"] == []


# ══ 守衛 2：張力確實被閉合（三件產出齊備）══════════════════════════════════
def test_guard2_tension_is_actually_closed(sandbox, publish_probe):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    closures = _run(sandbox)
    closed = [c for c in closures if c.closed]
    assert len(closed) == 1
    closure = closed[0]

    # 產出 1：epistemic_closure 事件（既有 writer 通道 → shadow log）
    assert closure.event_id and len(closure.event_id) == 32
    trace_files = list(sandbox["root"].rglob("trace.jsonl"))
    assert trace_files, "閉合事件必須走既有 NarrativeTraceWriter 通道"
    records = [
        json.loads(line)
        for line in trace_files[0].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(r.get("event_id") == closure.event_id for r in records)

    # 產出 2：張力標記 resolved
    assert closure.resolved_marked is True
    assert ea.load_open_tensions("agent_rem", sandbox["tensions"]) == []

    # 產出 3：1 個可掛載小念頭
    assert closure.enqueued is True
    items = pq.load_items("agent_rem", sandbox["queue"])
    assert len(items) == 1
    assert items[0].source == pq.PIGGYBACK_SOURCE_CLOSURE


def test_guard2b_closure_event_timestamp_uses_the_fake_clock(sandbox):
    """假時鐘是取證平面的一部分：事件的 ts 必須是注入的「當下」。"""
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    closures = _run(sandbox)
    closure = [c for c in closures if c.closed][0]
    trace_file = list(sandbox["root"].rglob("trace.jsonl"))[0]
    record = json.loads(trace_file.read_text(encoding="utf-8").splitlines()[0])
    assert record["ts"].startswith("2023-11-14"), record["ts"]


# ══ 守衛 3：不硬閉合（無證據 ⇒ 無 closure 事件）═══════════════════════════
def test_guard3_no_closure_event_without_evidence(sandbox, publish_probe):
    """無證據的張力 ⇒ **不**產生 closure 事件（防止為了有產出而編故事）。"""
    closures = _run(sandbox)
    assert closures, "有張力就會嘗試"
    assert all(c.closed is False for c in closures)
    assert all(c.event_id is None for c in closures)
    assert list(sandbox["root"].rglob("trace.jsonl")) == [], "不該有任何閉合事件"
    assert pq.load_items("agent_rem", sandbox["queue"]) == []
    assert len(ea.load_open_tensions("agent_rem", sandbox["tensions"])) == 1


# ══ 守衛 4：掛載正確性（相關 ⇒ 1；無關 ⇒ 0）══════════════════════════════
def test_guard4_piggyback_only_when_topic_is_relevant(sandbox, monkeypatch):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    monkeypatch.setenv(pq.FEATURE_ENV, "1")

    unrelated = rp.format_piggyback_block(
        "agent_rem", "天氣如何", store_dir=sandbox["queue"], now=FAKE_NOW
    )
    assert unrelated == "", "話題無關 ⇒ 不帶"

    related = rp.format_piggyback_block(
        "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=FAKE_NOW
    )
    assert related, "話題相關 ⇒ 帶出"
    assert rp.PIGGYBACK_BLOCK_HEADER in related
    assert "【回憶】" not in related, "話頭不得混入回憶區塊"


def test_guard4b_piggyback_block_absent_when_feature_flag_off(sandbox, monkeypatch):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    assert (
        rp.format_piggyback_block(
            "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=FAKE_NOW
        )
        == ""
    )


def test_guard4c_proxy_wiring_is_off_by_default(monkeypatch):
    """生產零行為變化：旗標預設關閉 ⇒ proxy 的接線輸出空字串。"""
    from src.llm import proxy as px

    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    assert px._format_piggyback_block("agent_rem", "任何話題") == ""


def test_guard4d_proxy_background_action_spawns_no_thread_when_off(monkeypatch):
    """旗標關閉 ⇒ 背景行動**完全不啟動**（0 執行緒、0 事件、0 寫入）。

    這是「生產零行為變化」最直接的證據：生產上這段接線是**死碼**。
    """
    from src.llm import proxy as px

    spawned = []
    original_start = threading.Thread.start

    def _spy_start(self, *args, **kwargs):
        spawned.append(self.name)
        return original_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", _spy_start)
    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    px._BACKGROUND_ACTION_STATE.pop("agent_rem", None)
    px._maybe_trigger_background_epistemic_action(
        "agent_rem", "主人買了一個會發熱的東西", last_interaction_ts=0
    )
    assert spawned == [], f"旗標關閉時不得啟動任何背景執行緒，實得 {spawned}"
    assert not px._BACKGROUND_ACTION_STATE.get("agent_rem", {}).get("running", False)


# ══ 守衛 5：過期丟棄 ══════════════════════════════════════════════════════
def test_guard5_expired_item_is_not_piggybacked(sandbox, monkeypatch):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    after = FAKE_NOW + pq.PIGGYBACK_TTL_SECONDS + 1
    assert (
        rp.format_piggyback_block(
            "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=after
        )
        == ""
    )


# ══ 守衛 6：不重複 ════════════════════════════════════════════════════════
def test_guard6_no_duplicate_piggyback(sandbox, monkeypatch):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    first = rp.format_piggyback_block(
        "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=FAKE_NOW
    )
    second = rp.format_piggyback_block(
        "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=FAKE_NOW
    )
    assert first
    assert second == "", "同一小念頭不得被帶出第二次"


# ══ 守衛 7：隔離不掛載 ═══════════════════════════════════════════════════
def test_guard7_quarantined_node_cannot_be_piggybacked(sandbox, monkeypatch):
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    monkeypatch.setenv(pq.FEATURE_ENV, "1")

    # 把入隊時掛上去的引用標成隔離 ⇒ 不得被帶出
    items = pq.load_items("agent_rem", sandbox["queue"])
    assert len(items) == 1
    quarantined = set(items[0].refs)

    picked = pq.pick_for_topic(
        "agent_rem",
        ["發熱"],
        store_dir=sandbox["queue"],
        quarantined_ids=quarantined,
        now=FAKE_NOW,
    )
    assert picked is None, "隔離節點／事件不得被帶出"


def test_guard7b_quarantined_ref_is_rejected_at_enqueue(sandbox, monkeypatch):
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    accepted = pq.enqueue(
        "agent_rem",
        tags=["發熱"],
        payload="隔離的念頭",
        source=pq.PIGGYBACK_SOURCE_CLOSURE,
        refs=["node_q1"],
        store_dir=sandbox["queue"],
        quarantined_ids={"node_q1"},
        now=FAKE_NOW,
    )
    assert accepted is False
    assert pq.load_items("agent_rem", sandbox["queue"]) == []


# ══ 守衛 8：不影響主動 DM 的發送決策 ═════════════════════════════════════
def _decision_prompt(agent_id: str) -> str:
    """組出「主動 DM 的發送決策輸入」（既有 Decision 專用 builder）。"""
    from src.soul.decision import (
        _build_emergent_summary,
        _build_memory_summary,
        _build_relationship_summary,
        _build_temporal_anchor,
        build_decision_prompt,
    )
    from src.soul.motive import make_motive

    motive = make_motive(
        motive_id="motive_sandbox_1",
        content="想跟主人說一件事",
        target="bryan",
        provenance_ref="",
        created_at="2026-10-21T00:00:00+00:00",
    )
    return build_decision_prompt(
        motive=motive,
        provenance_desc="",
        relationship_summary=_build_relationship_summary(agent_id),
        memory_summary=_build_memory_summary(agent_id, query=motive.content),
        emergent_summary=_build_emergent_summary(agent_id),
        current_time="2026-10-21 17:00 (Period: evening, Day: Wednesday)",
        temporal_anchor=_build_temporal_anchor(agent_id),
    )


def test_guard8_proactive_dm_decision_inputs_are_byte_identical(sandbox, monkeypatch):
    """本機制啟用前後，主動 DM 的**發送決策輸入**逐位元相同。"""
    from src.paths import reset_data_root

    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(sandbox["root"]))
    reset_data_root()
    _write_relationships(
        sandbox["soul"], "agent_rem", {"impression_tags": ["貼心"], "relational_band": "known"}
    )

    monkeypatch.delenv(pq.FEATURE_ENV, raising=False)
    before = _decision_prompt("agent_rem")

    # 啟用本機制，跑一整輪（含閉合、入隊）
    monkeypatch.setenv(pq.FEATURE_ENV, "1")
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    # 🔴 必須**真的走一次掛載呈現路徑**：掛載區塊的組裝（recall_prompt）才是
    #    「可能影響發訊決策」的那一段。若不呼叫它，守衛 8 就是盲腸守衛
    #    （變異了也不會紅）。
    rendered = rp.format_piggyback_block(
        "agent_rem", "那個發熱的東西是什麼", store_dir=sandbox["queue"], now=FAKE_NOW
    )
    assert rendered, "掛載區塊必須真的被組裝出來（否則守衛 8 是盲腸）"

    after = _decision_prompt("agent_rem")
    assert before == after, "本機制不得改變主動 DM 的發送決策輸入"


def test_guard8b_scheduler_and_decision_sources_are_untouched():
    """發訊決策的兩個 frozen 面必須**未被本票接入**。

    ⚠️ 本檔**不使用 subprocess**（R5 白名單大小鎖死 22）⇒ 這裡用「原始碼不含本票
    任何識別字」來證明本票沒有接進發訊決策鏈；實際的 diff 由工單回報附上。
    """
    markers = (
        "epistemic_action",
        "piggyback",
        "EPISTEMIC_PIGGYBACK",
        "format_piggyback_block",
    )
    for rel in ("src/soul/scheduler.py", "src/soul/decision.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for marker in markers:
            assert marker not in source, f"{rel} 出現本票識別字 {marker}（發訊決策面是 frozen）"


# ══ 守衛 9：不經過 0.3 門檻 ═════════════════════════════════════════════
def test_guard9_trigger_not_blocked_by_longing_threshold(sandbox, publish_probe):
    """以一個「想念值 = 0.05」的 agent 為例，缺口／張力路徑**仍能觸發**。"""
    from src.agent.emotion import compute_longing
    from src.soul.scheduler import LONGING_THRESHOLD

    longing = compute_longing(intimacy=100.0, silence_minutes=72.0)
    assert longing == pytest.approx(0.05, abs=1e-6)
    assert longing < LONGING_THRESHOLD, "本用例的前提：想念值確實低於門檻"

    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    closures = _run(sandbox)
    closed = [c for c in closures if c.closed]
    assert len(closed) == 1, "想念值低於門檻不得擋住自主求知行動"


def test_guard9b_longing_threshold_constant_is_not_modified():
    """🔴 本票不得修改 LONGING_THRESHOLD／LONGING_CHECK_INTERVAL_MINUTES。"""
    from src.soul.scheduler import LONGING_CHECK_INTERVAL_MINUTES, LONGING_THRESHOLD

    assert LONGING_THRESHOLD == 0.3
    assert LONGING_CHECK_INTERVAL_MINUTES == 30


# ══ 守衛 10：生產 sha256 前後逐位元相同 ══════════════════════════════════
def _production_manifest() -> tuple:
    """對「本票可能觸及」的生產路徑取 sha256 清單。

    回傳 ``({path: (sha256, mtime_ns)}, unreadable_count)``；讀不到的檔案
    （生產服務鎖住 sqlite）計入 unreadable，**不假裝比過**。
    """
    manifest = {}
    unreadable = 0
    seen = set()
    paths = []
    for rel_dir in PRODUCTION_SCOPE_DIRS:
        paths.extend(sorted((REPO_ROOT / rel_dir).rglob("*")))
    for pattern in PRODUCTION_SCOPE_GLOBS:
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    for path in paths:
        if not path.is_file():
            continue
        key = str(path.relative_to(REPO_ROOT))
        if key in seen:
            continue
        seen.add(key)
        try:
            stat = path.stat()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            unreadable += 1
            continue
        manifest[key] = (digest, stat.st_mtime_ns)
    return manifest, unreadable


#: 本票寫入面的識別字（若本票寫進生產，這些字串一定會出現在被改動的檔案裡）。
TICKET_MARKERS = ("epistemic_action", "epistemic_closure", "【話頭】", "PIGGYBACK")


def test_guard10a_data_root_is_isolated_from_production(sandbox, monkeypatch):
    """沙盒期間 ``data_root()`` 必須**不是** repo 的 ``data/``。"""
    from src.paths import data_root, reset_data_root

    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(sandbox["root"]))
    reset_data_root()
    assert Path(data_root()).resolve() != PRODUCTION_DATA.resolve()


def test_guard10b_new_modules_contain_no_hardcoded_data_path():
    """新模組不得出現任何指向 repo ``data/`` 的路徑字面值。"""
    import ast

    for rel in ("src/inner_life/epistemic_action.py", "src/inner_life/piggyback_queue.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value.replace("\\", "/")
                assert not literal.startswith("data/"), f"{rel} 出現 data/ 路徑字面值"
                assert "soul-os-harness" not in literal, f"{rel} 出現 repo 絕對路徑"


def test_guard10c_production_data_never_receives_this_ticket(sandbox, publish_probe):
    """生產資料對帳（歸因式）。

    ⚠️ **生產服務正在跑**（本機服務每分鐘寫 heartbeat／conversation／elevation／
    world log）⇒ 對帳必須把「服務自己的 churn」與「本票造成的寫入」分開。
    判準（**確定性**）：被改動的生產檔案裡**不得出現本票的識別字**
    （``epistemic_action`` / ``epistemic_closure`` / ``【話頭】`` / ``PIGGYBACK``）。
    服務自身的 churn 數量如實回報（不混算、不假裝是 0）。
    """
    before, unreadable_before = _production_manifest()
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    after, unreadable_after = _production_manifest()

    service_churn = []
    ticket_writes = []
    for key, (digest, _mtime) in after.items():
        prev = before.get(key)
        if prev is not None and prev[0] == digest:
            continue
        service_churn.append(key)
        path = REPO_ROOT / key
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in content for marker in TICKET_MARKERS):
            ticket_writes.append(key)

    assert ticket_writes == [], f"本票寫進了生產資料：{ticket_writes}"
    assert unreadable_after == unreadable_before


def test_guard10b_sandbox_writes_only_into_tmp(tmp_path, sandbox):
    """沙盒寫入必須只落在 tmp_path（不得寫進生產 data/）。"""
    _write_memory(sandbox["memory"], "agent_rem", "主人提到那個會發熱的東西", ["發熱"])
    _run(sandbox)
    produced = {p for p in sandbox["root"].rglob("*") if p.is_file()}
    assert produced, "沙盒應該有产出（記錄都在 tmp 底下）"
    for path in produced:
        assert str(path).startswith(str(tmp_path)), path


# ══ 關係張力路徑（假時鐘推進 ⇒ 張力自行消退）══════════════════════════════
def test_relational_tension_self_resolves_after_seven_days(sandbox, publish_probe, monkeypatch):
    """張力／牵挂 → 释然 ⇒ SELF_REASON 命中 ⇒ 閉合（全程 0 發訊）。"""
    from src.paths import reset_data_root

    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(sandbox["root"]))
    reset_data_root()
    _write_relationships(
        sandbox["soul"], "agent_rem", {"impression_tags": ["貼心"], "relational_band": "known"}
    )

    last_interaction = FAKE_NOW - 2 * 24 * 3600
    first = _run(sandbox, utterance="", last_interaction_ts=last_interaction)
    assert all(c.closed is False for c in first), "兩天前還在牵挂，不該閉合"

    later = FAKE_NOW + 6 * 24 * 3600
    second = _run(sandbox, utterance="", last_interaction_ts=last_interaction, now=later)
    closed = [c for c in second if c.closed]
    assert len(closed) == 1
    assert closed[0].evidence.path_kind == ea.PATH_SELF_REASON
    assert publish_probe["published"] == []
