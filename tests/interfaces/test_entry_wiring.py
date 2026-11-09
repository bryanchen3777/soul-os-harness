"""EI-2B-PROGRAM-2 entry wiring tests: three claim points + O1/O2/O3 gates.

Offline only: caller-opened SQLite in tmp files, fake buses/adapters,
tmp data root (conftest autouse). No server, no ports, no real API/LLM.
"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.interfaces import coordinator as coord
from src.interfaces.coordinator import (
    FLAG_ENV_VAR,
    coordinator_enabled,
    open_admission_context,
    prepare_publish,
)
from src.voice.gate import RouteDecision, RouteOutcome, RoutingFeatures

T0 = 1_700_000_000.0
SOUL = "agent_yua"


class FakeBus:
    def __init__(self):
        self.publishes = []

    async def publish(self, event):
        self.publishes.append(event)


class FakeMgr:
    def __init__(self):
        self.sent = []
        self.count = 0

    async def broadcast(self, payload):
        self.sent.append(payload)


class FakeAdapter:
    channel_id = "telegram"

    def __init__(self):
        self.sent = []

    async def send(self, *, agent_id, text, user_id):
        self.sent.append((agent_id, text, user_id))
        return True


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ctx(tmp_path, name="w.db", epoch=1):
    conn = _open(tmp_path / name)
    return open_admission_context(conn, epoch=epoch)


def _enable(monkeypatch):
    monkeypatch.setenv(FLAG_ENV_VAR, "1")
    assert coordinator_enabled() is True


def _voice_outcome(text="hello voice"):
    return RouteOutcome(
        RouteDecision.USER_MESSAGE, 1.0, "test", "test",
        RoutingFeatures(text=text, owner_ok=True), int(T0 * 1000),
    )


def _speak(agent_id=SOUL, request_id=None, channel="telegram", user_id=1696287850,
           tts_enabled=False, audio_text=""):
    payload = {
        "agent_id": agent_id,
        "text": "hi",
        "audio_text": audio_text,
        "emotion": "",
        "reason": "user_message",
        "mode": "private",
        "target_channel": channel,
        "target_user_id": user_id,
        "tts_enabled": tts_enabled,
    }
    if request_id is not None:
        payload["soul_request_id"] = request_id
    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source=agent_id,
        target="broadcast",
        priority=EventPriority.NORMAL,
        session_id="session_x_%s" % agent_id,
        payload=payload,
    )


# -- constructor compat --------------------------------------------------------


def test_entries_accept_admission_kwarg(tmp_path):
    from src.io.channels.router import ChannelRouter
    from src.io.gateway import IOGateway
    from src.llm.fish_tts_handler import FishTTSHandler
    from src.voice.input_router import VoiceInputRouter

    bus = FakeBus()
    ctx = _ctx(tmp_path, "ctor.db")
    gw = IOGateway(bus, admission=ctx)
    assert gw._admission is ctx
    assert IOGateway(bus)._admission is None
    rt = ChannelRouter(bus, admission=ctx)
    assert rt._admission is ctx
    assert ChannelRouter(bus)._admission is None
    vr = VoiceInputRouter(admission=ctx)
    assert vr.admission is ctx
    assert VoiceInputRouter().admission is None
    fh = FishTTSHandler(bus, output_dir=tmp_path / "tts", admission=ctx)
    assert fh.admission is ctx


# -- voice claim point ---------------------------------------------------------


async def test_voice_flag_off_publishes_without_id(tmp_path):
    from src.voice.input_router import VoiceInputRouter

    assert coordinator_enabled() is False
    bus = FakeBus()
    vr = VoiceInputRouter(bus=bus, admission=_ctx(tmp_path, "v-off.db"))
    out = await vr._dispatch(_voice_outcome())
    assert out.decision == RouteDecision.USER_MESSAGE
    assert len(bus.publishes) == 1
    assert "soul_request_id" not in bus.publishes[0].payload


async def test_voice_flag_on_allow_stamps_id(tmp_path, monkeypatch):
    from src.paths import data_root
    from src.voice.input_router import VoiceInputRouter

    _enable(monkeypatch)
    bus = FakeBus()
    ctx = _ctx(tmp_path, "v-on.db")
    vr = VoiceInputRouter(bus=bus, admission=ctx)
    out = await vr._dispatch(_voice_outcome())
    assert out.decision == RouteDecision.USER_MESSAGE
    assert len(bus.publishes) == 1
    rid = bus.publishes[0].payload.get("soul_request_id")
    assert isinstance(rid, str) and rid.startswith("in-")
    assert coord._read_request_by_id(ctx.journal, rid)["state"] == "executing"
    # Admit sits before the persistent touch write, and allow keeps it.
    assert (data_root() / "state" / "bryan_last_seen.json").is_file()


async def test_voice_flag_on_deny_is_ambient_no_publish_no_touch(tmp_path, monkeypatch):
    from src.paths import data_root
    from src.voice.input_router import VoiceInputRouter

    _enable(monkeypatch)
    ctx = _ctx(tmp_path, "v-deny.db")
    holder = _open(tmp_path / "v-deny-holder.db")
    # Same file, separate connection: pre-hold the soul lease.
    import sqlite3 as _s

    hc = _s.connect(str(tmp_path / "v-deny.db"), timeout=10.0)
    from src.interfaces.soul_lease import SoulLeaseStore
    from src.interfaces.soul_lease import own_process_create_time as _own_ct

    import os as _os
    import time as _time

    # Holder clock must be live: admit uses real time, a T0 stamp is long expired.
    SoulLeaseStore(hc).acquire(
        SOUL, "tok-holder", "in-holder", "nonce-holder",
        _os.getpid(), _own_ct(), 1, _time.time(), holder_entry="web",
    )
    hc.close()
    void_holder = holder  # noqa: F841 - documents the separate-connection hold.
    bus = FakeBus()
    vr = VoiceInputRouter(bus=bus, admission=ctx)
    out = await vr._dispatch(_voice_outcome("second voice"))
    assert out.decision == RouteDecision.AMBIENT
    assert bus.publishes == []
    assert vr.user_message_count == 0
    assert not (data_root() / "state" / "bryan_last_seen.json").exists()


# -- router inbound claim point ------------------------------------------------


async def test_router_inbound_allow_then_deny(tmp_path, monkeypatch):
    from src.io.channels.router import ChannelRouter

    _enable(monkeypatch)
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
    bus = FakeBus()
    ctx = _ctx(tmp_path, "r.db")
    rt = ChannelRouter(bus, admission=ctx)
    rt._outbox = []
    saves = []
    rt._save_last_tg_user_global = lambda u, a: saves.append(("global", u, a))
    rt._save_bryan_last_seen = lambda a, t: saves.append(("seen", a, t))
    rt._touch_intimacy_clock = lambda a, c, t: saves.append(("touch", a, c))
    await rt.inbound("yua", "hello tg", 1696287850, channel="telegram")
    assert len(bus.publishes) == 1
    rid = bus.publishes[0].payload.get("soul_request_id")
    assert isinstance(rid, str) and rid.startswith("in-")
    # Admit sits before the first persistent side effect; allow keeps them.
    assert saves and saves[0][0] == "global"

    # Second soul-holder blocks: deny = log + return (no touch, no publish).
    import sqlite3 as _s2

    hc = _s2.connect(str(tmp_path / "r.db"), timeout=10.0)
    from src.interfaces.soul_lease import SoulLeaseStore
    from src.interfaces.soul_lease import own_process_create_time as _own_ct2

    import os as _os2
    import time as _time2

    # Live clock (admit uses real time); loses to the first inbound's live
    # lease, so the row still names a live holder and the deny path is hit.
    SoulLeaseStore(hc).acquire(
        SOUL, "tok-holder2", "in-holder2", "nonce-holder2",
        _os2.getpid(), _own_ct2(), 1, _time2.time(), holder_entry="web",
    )
    hc.close()
    bus2 = FakeBus()
    rt._bus = bus2
    saves.clear()
    await rt.inbound("yua", "second tg", 1696287850, channel="telegram")
    assert bus2.publishes == []
    assert saves == []


async def test_router_inbound_flag_off_has_no_id(tmp_path, monkeypatch):
    from src.io.channels.router import ChannelRouter

    assert coordinator_enabled() is False
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
    bus = FakeBus()
    rt = ChannelRouter(bus, admission=_ctx(tmp_path, "r-off.db"))
    rt._outbox = []
    rt._save_last_tg_user_global = lambda *a: None
    rt._save_bryan_last_seen = lambda *a: None
    rt._touch_intimacy_clock = lambda *a: None
    await rt.inbound("yua", "hello tg", 1696287850, channel="telegram")
    assert len(bus.publishes) == 1
    assert "soul_request_id" not in bus.publishes[0].payload


# -- O1 / O2 / O3 fail-closed --------------------------------------------------


async def test_gateway_o1_gate(tmp_path, monkeypatch):
    from src.io.gateway import IOGateway

    _enable(monkeypatch)
    monkeypatch.chdir(tmp_path)
    bus = FakeBus()
    ctx = _ctx(tmp_path, "o1.db")
    gw = IOGateway(bus, admission=ctx)
    gw.manager = FakeMgr()

    # Unregistered id: zero send.
    await gw._on_agent_speak(_speak(request_id="in-ghost"))
    assert gw.manager.sent == []

    # Background (no id): pass through.
    await gw._on_agent_speak(_speak())
    assert len(gw.manager.sent) == 1

    # Live registration: pass through.
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="hi")
    assert out.should_publish is True
    await gw._on_agent_speak(_speak(request_id=ident.request_id))
    assert len(gw.manager.sent) == 2


async def test_router_o2_gate(tmp_path, monkeypatch):
    from src.io.channels.router import ChannelRouter

    _enable(monkeypatch)
    bus = FakeBus()
    ctx = _ctx(tmp_path, "o2.db")
    rt = ChannelRouter(bus, admission=ctx)
    rt._outbox = []
    adapter = FakeAdapter()
    rt.register(adapter)
    rt._should_push_to_bry = lambda agent_id: True

    await rt._on_agent_speak(_speak(request_id="in-ghost"))
    assert adapter.sent == []

    await rt._on_agent_speak(_speak())
    assert len(adapter.sent) == 1

    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="tg", text="hi")
    assert out.should_publish is True
    await rt._on_agent_speak(_speak(request_id=ident.request_id))
    assert len(adapter.sent) == 2


async def test_router_fallback_hold_retains_without_auto_forward(tmp_path, monkeypatch):
    from src.io.channels.router import ChannelRouter

    _enable(monkeypatch)
    bus = FakeBus()
    ctx = _ctx(tmp_path, "fb.db")
    rt = ChannelRouter(bus, admission=ctx)

    class NoConn:
        count = 0

    rt._gateway_manager = NoConn()
    rt._should_push_to_bry = lambda agent_id: True
    calls = []
    orig_flush = rt._maybe_flush_outbox

    async def _no_auto_flush():
        calls.append("flush")

    rt._maybe_flush_outbox = _no_auto_flush  # must stay uncalled in hold mode
    adapter = FakeAdapter()
    rt.register(adapter)
    # Background proactive speak, web target, nobody online, no last user.
    await rt._on_agent_speak(_speak(channel="web", user_id=None))
    assert len(rt._outbox) == 1
    assert adapter.sent == []
    assert calls == []
    rt._maybe_flush_outbox = orig_flush


async def test_fish_o3_gate(tmp_path, monkeypatch):
    import src.llm.fish_tts_handler as fish_mod
    from src.llm.fish_tts_handler import FishTTSHandler

    _enable(monkeypatch)
    bus = FakeBus()
    ctx = _ctx(tmp_path, "o3.db")
    fh = FishTTSHandler(bus, output_dir=tmp_path / "tts", admission=ctx)
    tasks = []
    monkeypatch.setattr(fish_mod, "create_managed_task", lambda coro: tasks.append(coro))

    await fh._on_agent_speak(_speak(request_id="in-ghost", tts_enabled=True, audio_text="hi audio"))
    assert tasks == []

    await fh._on_agent_speak(_speak(tts_enabled=True, audio_text="hi audio"))
    assert len(tasks) == 1
    for c in tasks:
        c.close()


# -- background attribution ----------------------------------------------------


def test_diary_and_dream_carry_non_request_mark(tmp_path):
    from src.soul.diary import DiaryWriter
    from src.soul.dream_event import DreamEventWriter

    dm = DiaryWriter(data_dir=str(tmp_path / "soul"))
    path = dm.write_entry("agent_yua", "morning", "a quiet line", source="llm")
    assert path is not None
    row = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["non_request_write"] is True

    dw = DreamEventWriter(data_dir=str(tmp_path / "soul2"))
    dpath = dw._write_entry("agent_yua", "night", "a dream line", source="llm")
    assert dpath is not None
    drow = json.loads(dpath.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert drow["non_request_write"] is True


def test_background_modules_never_import_coordinator():
    for rel in ("src/soul/diary.py", "src/soul/dream_event.py"):
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        assert not any("coordinator" in m or "soul_lease" in m for m in mods), rel
        assert "non_request_write" in Path(rel).read_text(encoding="utf-8"), rel


def test_gateway_stamp_is_conditional_ast():
    """Flag-off byte-identical: the soul_request_id stamp only runs when set."""
    tree = ast.parse(Path("src/io/gateway.py").read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            src = ast.dump(node.test)
            if "_soul_request_id" in src and "None" in src:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Subscript):
                        found = True
    assert found, "conditional soul_request_id stamp missing in gateway.py"


# -- PROGRAM-3 §8/§9: busy signals + retry keys + rejected retention ---------

async def test_router_inbound_busy_sends_message_and_retains_input(tmp_path, monkeypatch):
    """TG deny with a live holder: explicit busy message (retry_after=1),
    zero publish / zero touch, raw input retained under the stable key."""
    from src.interfaces.coordinator import count_rejected_inputs
    from src.interfaces.coordinator import derive_retry_key
    from src.io.channels.router import ChannelRouter

    _enable(monkeypatch)
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
    ctx = _ctx(tmp_path, "busy-tg.db")
    rt = ChannelRouter(bus=FakeBus(), admission=ctx)
    rt._outbox = []
    rt._save_last_tg_user_global = lambda *a: None
    rt._save_bryan_last_seen = lambda *a: None
    rt._touch_intimacy_clock = lambda *a: (_ for _ in ()).throw(AssertionError("touch on deny"))
    adapter = FakeAdapter()
    rt.register(adapter)
    # Pre-hold the soul lease on a separate connection (live clock).
    import os as _os
    import time as _time

    from src.interfaces.soul_lease import SoulLeaseStore
    from src.interfaces.soul_lease import own_process_create_time as _own_ct

    hc = _open(tmp_path / "busy-tg.db")
    SoulLeaseStore(hc).acquire(
        SOUL, "tok-busy", "in-busy", "nonce-busy",
        _os.getpid(), _own_ct(), 1, _time.time(), holder_entry="web",
    )
    hc.close()
    await rt.inbound("yua", "second tg busy", 1696287850, channel="telegram", update_id=99)
    # Explicit busy message, never success, never queued.
    assert len(adapter.sent) == 1
    agent_id, text, user_id = adapter.sent[0]
    assert "retry" in text.lower() and "1 second" in text
    assert user_id == 1696287850
    # Raw input retained under the stable bot-namespace + update_id key.
    key = derive_retry_key("tg", SOUL, "telegram:99")
    assert count_rejected_inputs(ctx, key) == 1


async def test_router_inbound_busy_without_adapter_stays_silent(tmp_path, monkeypatch):
    """Busy with no adapter registered: deny stays silent but safe."""
    from src.io.channels.router import ChannelRouter

    _enable(monkeypatch)
    monkeypatch.setenv("TELEGRAM_OWNER_ID", "1696287850")
    ctx = _ctx(tmp_path, "busy-noad.db")
    rt = ChannelRouter(bus=FakeBus(), admission=ctx)
    rt._outbox = []
    rt._save_last_tg_user_global = lambda *a: None
    rt._save_bryan_last_seen = lambda *a: None
    rt._touch_intimacy_clock = lambda *a: None
    import os as _os2
    import time as _time2

    from src.interfaces.soul_lease import SoulLeaseStore
    from src.interfaces.soul_lease import own_process_create_time as _own_ct2

    hc = _open(tmp_path / "busy-noad.db")
    SoulLeaseStore(hc).acquire(
        SOUL, "tok-b2", "in-b2", "nonce-b2",
        _os2.getpid(), _own_ct2(), 1, _time2.time(), holder_entry="web",
    )
    hc.close()
    await rt.inbound("yua", "busy no adapter", 1696287850, channel="telegram", update_id=100)
    assert rt._bus.publishes == []


async def test_voice_busy_state_carries_retry_after_and_retains(tmp_path, monkeypatch):
    """Voice deny: AMBIENT (the busy state) names retry_after=1; input kept."""
    from src.interfaces.coordinator import count_rejected_inputs
    from src.voice.input_router import VoiceInputRouter

    _enable(monkeypatch)
    ctx = _ctx(tmp_path, "busy-v.db")
    import os as _os3
    import time as _time3

    from src.interfaces.soul_lease import SoulLeaseStore
    from src.interfaces.soul_lease import own_process_create_time as _own_ct3

    hc = _open(tmp_path / "busy-v.db")
    SoulLeaseStore(hc).acquire(
        SOUL, "tok-bv", "in-bv", "nonce-bv",
        _os3.getpid(), _own_ct3(), 1, _time3.time(), holder_entry="web",
    )
    hc.close()
    bus = FakeBus()
    vr = VoiceInputRouter(bus=bus, admission=ctx)
    out = await vr._dispatch(_voice_outcome("busy voice"))
    assert out.decision == RouteDecision.AMBIENT
    assert "retry_after=1" in out.reason
    assert bus.publishes == []
    # Voice has no stable turn id at this boundary: UUID key, documented
    # non-dedup. At least one rejected row exists for this soul.
    import sqlite3 as _s

    rows = _s.connect(str(tmp_path / "busy-v.db")).execute(
        "SELECT retry_key, reason FROM rejected_inputs WHERE soul_alias=?", (SOUL,)
    ).fetchall()
    assert len(rows) == 1 and rows[0][0].startswith("uuid-") and rows[0][1] == "soul_busy"
    assert count_rejected_inputs(ctx, rows[0][0]) == 1


def test_gateway_busy_frame_wired_ast():
    """WS deny renders the 409 frame (holder class + retry_after=1) back on
    the socket and retains the input: static pin, plus render unit below."""
    src = Path("src/io/gateway.py").read_text(encoding="utf-8")
    assert "send_text(json.dumps(_busy_frame_w(_outcome)" in src
    assert "_record_rejected_w(" in src
    assert "idempotency_key=_ws_retry_key" in src


def test_busy_frame_render_unit():
    from src.interfaces.coordinator import AdmissionOutcome, BusyBody, busy_frame

    out = AdmissionOutcome(
        disposition="IN_PROGRESS", http_status=409, code="soul_busy",
        should_publish=False, busy=BusyBody(holder_entry="tg", holder_epoch=7),
    )
    frame = busy_frame(out)
    assert frame == {
        "type": "soul_busy", "code": "soul_busy",
        "holder_entry": "tg", "holder_epoch": 7, "retry_after": 1,
    }
