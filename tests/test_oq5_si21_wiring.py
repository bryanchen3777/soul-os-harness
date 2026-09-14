"""
tests/test_oq5_si21_wiring.py — OQ5-SI21-WIRING-1: SI-2.1 三道防線生產接線驗收

工單: OQ5-SI21-WIRING-1（把 SI-2.1 三道防線的歷史遺留缺口接線）
契約:
  - docs/SOCIAL-DIFFUSION-CONTRACT.md（SI-2.1）§4（防線 3）/ §5（防線 2）/ §7
  - docs/LIFE-THREAD-ENGINE-CONTRACT.md §11 OQ-5（缺口登記）

驗收項（全部 additive; 判定語意 0 變更）:
  A. 防線 3（Identity Firewall）生產接線
     - 既有組裝路徑（src/inner_life/firewall_wiring.build_submission_gate）
       dry-run 建構 → ``gate._identity_firewall is not None`` 且 ``_agent_id`` 正確
     - **同一輸入、注入前 vs 注入後的判定差異**（注入前第 6 步恆跳過 = 放行;
       注入後真的在判 → 他者 fail-closed 拒絕）
     - per-agent 分派器: 每個靈魂用自己的 firewall（避免把其餘靈魂的合法自我
       事件判成他者而整體誤擋）
     - fail-closed: 缺參數 / 建不出 firewall ⇒ **拒絕**, 絕不退回無 firewall 的 gate
  B. 防線 2（SocialEventProducerGate）＋ SOCIAL_WORLD_EVENT 生產端接線
     - gate 被實例化; 生產端 **受其節制**（只有 allowed 才 publish）
     - private / mode 缺失 / 矛盾訊號（group 卻定向 telegram 收件人）⇒ 不 publish
     - producer 缺 gate ⇒ 建構即 raise（缺參數 = 拒絕而非放行）
     - 產出的 payload 通過**消費者**（WorldPerceptionMiddleware 第一關）用的
       ``validate_social_world_event``
  C. 生產腳本確實含本票接線（靜態; 防止未來被無聲移除）

紅線: 0 服務重啟 / 0 連接埠綁定 / 0 asyncio server / 0 生產 data/** 寫入
      （全部組裝皆 tmp_path 隔離; data root 由 tests/conftest.py autouse 指向 tmp）
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life import (
    TRIGGER_TYPE_DIARY_NIGHT,
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    SubmissionGate,
)
from src.inner_life.firewall_wiring import (
    SYSTEM_AGENT_ID,
    FirewalledSubmissionGate,
    build_identity_firewall,
    build_submission_gate,
    build_system_submission_gate,
)
from src.social import SocialEventProducerGate
from src.social.producer import (
    CHANNEL_DM,
    SocialWorldEventProducer,
    resolve_channel_from_agent_speak,
)
from src.social.schema import CONTENT_MAX_CHARS, SPACE_LOUNGE, VISIBILITY_PUBLIC
from src.social.validation import is_private_on_bus, validate_social_world_event

ROOT = Path(__file__).resolve().parents[1]
BRYAN_TG_ID = "1696287850"


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────


def _trace_path(tmp_path: Path) -> Path:
    return tmp_path / "inner_life" / "trace.jsonl"


def _make_writer(tmp_path: Path) -> InnerLifeWriter:
    return InnerLifeWriter(
        trace_writer=NarrativeTraceWriter(trace_log_path=_trace_path(tmp_path))
    )


def _make_reader(tmp_path: Path) -> NarrativeTraceReader:
    return NarrativeTraceReader(trace_log_path=_trace_path(tmp_path))


def _create_event(writer: InnerLifeWriter, *, actor_id):
    """建 canonical InnerLifeEvent（producer trigger_type 合法 → 過第 5 步）。"""
    return writer.create_event(
        provenance=Provenance(
            trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
            actor_id=actor_id,
            source_system="diary",
        ),
    )


def _plain_gate(tmp_path: Path, writer: InnerLifeWriter) -> SubmissionGate:
    """接線前的生產形狀: 只有 writer= / trace_reader=。"""
    return SubmissionGate(writer=writer, trace_reader=_make_reader(tmp_path))


class _RecordingBus:
    """最小 bus 替身: 只記錄 publish（0 網路 / 0 非同步 server）。"""

    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:
        self.published.append(event)


def _agent_speak_event(
    *,
    agent_id: str = "agent_ruka",
    text: str = "大家早，今天天氣不錯。",
    mode=None,
    target_channel=None,
    target_user_id=None,
    reason: str = "user_message",
    is_stub: bool = False,
    dry_run: bool = False,
):
    """組一個 AGENT_SPEAK（欄位對齊 src/llm/proxy.py:3990-4017）。

    刻意「有值才寫 key」—— 這正是真實 payload 的行為（缺 mode 的路徑存在）。
    """
    payload = {
        "text": text,
        "agent_id": agent_id,
        "reason": reason,
        "is_stub": is_stub,
        "dry_run": dry_run,
    }
    if mode is not None:
        payload["mode"] = mode
    if target_channel is not None:
        payload["target_channel"] = target_channel
    if target_user_id is not None:
        payload["target_user_id"] = target_user_id
    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source=agent_id,
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=payload,
    )


def _make_producer(bus) -> SocialWorldEventProducer:
    return SocialWorldEventProducer(bus=bus, gate=SocialEventProducerGate())


def _run(coro):
    import asyncio

    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────
# A. 防線 3: 組裝路徑（dry-run, 0 服務啟動）
# ─────────────────────────────────────────────────────────────


def test_build_identity_firewall_fail_closed_on_missing_agent_id():
    """缺 agent_id ⇒ 建構即拒絕（fail-closed, 不得靜默跳過防線 3）。"""
    for bad in ("", "   ", None, 123):
        with pytest.raises(ValueError):
            build_identity_firewall(bad)


def test_production_assembly_injects_firewall(tmp_path):
    """既有組裝路徑 dry-run: gate 真的持有 firewall 且 agent_id 正確。"""
    writer = _make_writer(tmp_path)
    gate = build_submission_gate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        agent_id="agent_ruka",
        store_dir=tmp_path / "elevation",
    )
    assert gate._identity_firewall is not None
    assert gate._agent_id == "agent_ruka"
    assert gate._identity_firewall.current_agent_id == "agent_ruka"


def test_plain_gate_is_the_pre_wiring_shape(tmp_path):
    """接線前的生產形狀: 無 firewall ⇒ submission_gate.py:333 第 6 步恆跳過。"""
    writer = _make_writer(tmp_path)
    gate = _plain_gate(tmp_path, writer)
    assert gate._identity_firewall is None
    assert gate._agent_id is None


def test_system_gate_keeps_system_level_semantics(tmp_path):
    """系統／世界事件路徑: agent_id=None（EL-OWN-0 不變）＋ sentinel firewall。"""
    writer = _make_writer(tmp_path)
    gate = build_system_submission_gate(
        writer=writer, trace_reader=_make_reader(tmp_path)
    )
    assert gate._identity_firewall is not None
    assert gate._identity_firewall.current_agent_id == SYSTEM_AGENT_ID
    # agent_id=None → run_elevation 沿用 system-level 歸屬（行為與接線前一致）
    assert gate._agent_id is None


# ─────────────────────────────────────────────────────────────
# A2. 同一輸入: 注入前 vs 注入後的判定差異（核心證據）
# ─────────────────────────────────────────────────────────────


def test_same_input_verdict_differs_before_and_after_wiring(tmp_path):
    """同一顆他者事件: 注入前放行（恆跳過）; 注入後 fail-closed 拒絕（真的在判）。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_miku")  # 他者（本靈魂 = agent_ruka）

    before = _plain_gate(tmp_path, writer)
    after = build_submission_gate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        agent_id="agent_ruka",
        store_dir=tmp_path / "elevation",
    )

    v_before = before.verify(event.event_id)
    v_after = after.verify(event.event_id)

    assert v_before.accepted is True, "接線前 = 第 6 步恆跳過（靜默放行）"
    assert v_after.accepted is False, "接線後 = 他者事件必須 fail-closed 拒絕"
    assert "external_other_action" in v_after.reason
    assert after.get_stats()["identity_firewall_rejected"] == 1
    assert before.get_stats()["identity_firewall_rejected"] == 0


def test_same_input_submit_differs_before_and_after_wiring(tmp_path):
    """同一顆他者事件: 接線後 submit() 直接 []（不 consume）, 並計入拒絕。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_miku")
    after = build_submission_gate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        agent_id="agent_ruka",
        store_dir=tmp_path / "elevation",
    )
    assert after.submit(event.event_id) == []
    stats = after.get_stats()
    assert stats["rejected"] == 1
    assert stats["identity_firewall_rejected"] == 1
    assert stats["consumed"] == 0


def test_wired_gate_still_accepts_self_event(tmp_path):
    """0 誤擋: 自己的經歷（actor_id == current_agent_id）照常通過。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_ruka")
    gate = build_submission_gate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        agent_id="agent_ruka",
        store_dir=tmp_path / "elevation",
    )
    assert gate.verify(event.event_id).accepted is True


def test_wired_gate_still_accepts_system_event(tmp_path):
    """0 誤擋: 系統事件（actor_id=None → SYSTEM_ACTION）維持現狀。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id=None)
    gate = build_submission_gate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        agent_id="agent_ruka",
        store_dir=tmp_path / "elevation",
    )
    assert gate.verify(event.event_id).accepted is True


# ─────────────────────────────────────────────────────────────
# A3. per-agent 分派器（防「單一 firewall 誤擋其餘 9 個靈魂」）
# ─────────────────────────────────────────────────────────────


def test_dispatcher_builds_one_firewalled_gate_per_agent(tmp_path):
    writer = _make_writer(tmp_path)
    dispatcher = FirewalledSubmissionGate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        store_dir=tmp_path / "elevation",
    )
    ruka = dispatcher.gate_for("agent_ruka")
    miku = dispatcher.gate_for("agent_miku")

    assert ruka is not miku
    assert ruka._identity_firewall is not None and miku._identity_firewall is not None
    assert ruka._agent_id == "agent_ruka" and miku._agent_id == "agent_miku"
    assert ruka._identity_firewall.current_agent_id == "agent_ruka"
    assert miku._identity_firewall.current_agent_id == "agent_miku"
    # 系統路徑: 沿用 base gate（與接線前行為一致）, agent_id=None
    assert dispatcher.system_gate._agent_id is None
    # 同一個 agent 重複查詢 → 同一顆（不快取爆炸）
    assert dispatcher.gate_for("agent_ruka") is ruka


def test_dispatcher_does_not_misclassify_other_agents_own_events(tmp_path):
    """關鍵回歸防線: A 靈魂的自我事件, 在 B 靈魂的 gate 上才是他者。

    若接線誤用「單一 firewall」, 則 B 靈魂的自我事件會被 A 的 firewall 判成他者
    → 9/10 靈魂昇華鏈整體誤擋（本測試釘死此風險）。
    """
    writer = _make_writer(tmp_path)
    dispatcher = FirewalledSubmissionGate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        store_dir=tmp_path / "elevation",
    )
    miku_event = _create_event(writer, actor_id="agent_miku")

    assert dispatcher.gate_for("agent_miku").verify(miku_event.event_id).accepted is True
    assert dispatcher.gate_for("agent_ruka").verify(miku_event.event_id).accepted is False


def test_dispatcher_submit_routes_by_agent_id(tmp_path):
    """submit(agent_id=X) 走 X 的 gate: 自己的過、他者的擋（路由不是裝飾）。"""
    writer = _make_writer(tmp_path)
    dispatcher = FirewalledSubmissionGate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        store_dir=tmp_path / "elevation",
    )
    miku_event = _create_event(writer, actor_id="agent_miku")

    # 以 agent_miku 名義提交 → 自己的事件 → 過防火牆（進入 consume）
    miku_gate = dispatcher.gate_for("agent_miku")
    ruka_gate = dispatcher.gate_for("agent_ruka")
    dispatcher.submit(miku_event.event_id, agent_id="agent_miku")
    assert miku_gate.get_stats()["accepted"] == 1
    assert miku_gate.get_stats()["identity_firewall_rejected"] == 0
    # 以 agent_ruka 名義提交同一顆事件 → 他者 → fail-closed []
    assert dispatcher.submit(miku_event.event_id, agent_id="agent_ruka") == []
    assert ruka_gate.get_stats()["identity_firewall_rejected"] == 1
    assert dispatcher.get_stats()["refused"] == 0


def test_dispatcher_fail_closed_when_gate_cannot_be_built(tmp_path, monkeypatch):
    """fail-closed: 建不出 firewalled gate ⇒ 拒絕（[]）, 絕不退回無 firewall 的 gate。"""
    import src.inner_life.firewall_wiring as fw

    def _boom(**kwargs):
        raise RuntimeError("simulated assembly failure")

    monkeypatch.setattr(fw, "build_submission_gate", _boom)
    writer = _make_writer(tmp_path)
    dispatcher = FirewalledSubmissionGate(
        writer=writer,
        trace_reader=_make_reader(tmp_path),
        store_dir=tmp_path / "elevation",
    )
    event = _create_event(writer, actor_id="agent_ruka")
    assert dispatcher.submit(event.event_id, agent_id="agent_ruka") == []
    assert dispatcher.get_stats()["refused"] == 1


# ─────────────────────────────────────────────────────────────
# B. 防線 2: 生產端受 gate 節制（fail-closed）
# ─────────────────────────────────────────────────────────────


def test_producer_requires_gate_and_bus():
    """缺參數 ⇒ 拒絕而非放行（沒有守門人的 producer 不得存在）。"""
    with pytest.raises(ValueError):
        SocialWorldEventProducer(bus=_RecordingBus(), gate=None)
    with pytest.raises(ValueError):
        SocialWorldEventProducer(bus=None, gate=SocialEventProducerGate())


@pytest.mark.parametrize(
    "payload,expected",
    [
        # 1:1 私聊 → private / dm（gate BLOCK）
        ({"mode": "private", "target_channel": "telegram"}, ("private", CHANNEL_DM)),
        # 公共頻道 → group / lounge（gate ALLOW）
        ({"mode": "group"}, ("group", SPACE_LOUNGE)),
        # mode 缺失 / 未知 → 無法判定（gate fail-closed BLOCK）
        ({}, (None, None)),
        ({"mode": "sideways"}, ("sideways", None)),
        # 矛盾訊號: 宣稱 group 卻定向 telegram 收件人 → 無法判定 → BLOCK
        (
            {"mode": "group", "target_channel": "telegram", "target_user_id": BRYAN_TG_ID},
            (None, None),
        ),
    ],
)
def test_resolve_channel_is_fail_closed(payload, expected):
    assert resolve_channel_from_agent_speak(payload) == expected


def test_private_agent_speak_never_publishes(tmp_path):
    """1:1 私聊內容預設不擴散（SI-2.1 §5.1 紅線）。"""
    bus = _RecordingBus()
    producer = _make_producer(bus)
    event = _agent_speak_event(mode="private", target_channel="telegram",
                               target_user_id=BRYAN_TG_ID)
    _run(producer.on_agent_speak(event))
    assert bus.published == []
    assert producer.get_stats()["published"] == 0
    assert producer.get_stats()["blocked"] == 1


def test_contradictory_group_plus_telegram_never_publishes(tmp_path):
    """陷阱防護: mode=group 但定向 telegram 收件人（admin 測試端點的真實形狀）。

    proxy.py:3576 的 ``mode`` 預設是 "group"; admin 端點 spawn_cold_intents /
    spawn_intent 不傳 mode 且帶 target_channel=telegram + Bry chat_id
    → 若以 mode 單獨判定公開, 會把 1:1 私聊內容廣播出去。本測試釘死此路徑。
    """
    bus = _RecordingBus()
    producer = _make_producer(bus)
    event = _agent_speak_event(
        mode="group", target_channel="telegram", target_user_id=BRYAN_TG_ID
    )
    _run(producer.on_agent_speak(event))
    assert bus.published == []


def test_missing_mode_never_publishes():
    """mode 缺失 → 無法判定頻道性質 → BLOCK（不得繼承 proxy 的 "group" 預設）。"""
    bus = _RecordingBus()
    producer = _make_producer(bus)
    _run(producer.on_agent_speak(_agent_speak_event(mode=None)))
    assert bus.published == []


def test_public_lounge_agent_speak_publishes_contract_shaped_event():
    """公共頻道 → gate ALLOW → 產出一顆契約形狀正確的 SOCIAL_WORLD_EVENT。"""
    bus = _RecordingBus()
    producer = _make_producer(bus)
    _run(producer.on_agent_speak(_agent_speak_event(mode="group")))

    assert len(bus.published) == 1
    published = bus.published[0]
    assert published.event_type == EventType.SOCIAL_WORLD_EVENT
    assert published.target == "broadcast"
    assert published.priority == EventPriority.LOW  # schema.py:341
    assert published.actor_id == "agent_ruka"
    assert published.source == "agent_ruka"

    payload = published.payload
    assert payload["space_id"] == SPACE_LOUNGE
    assert payload["visibility"] == VISIBILITY_PUBLIC
    assert payload["priority"] == 0
    assert payload["actor_id"] == "agent_ruka"
    # 消費者（WorldPerceptionMiddleware._on_social_world_event 第一關）會用同一支驗證器
    restored = validate_social_world_event(payload)
    assert restored.actor_id == "agent_ruka"
    assert restored.space_id == SPACE_LOUNGE
    # 防線 2 的契約違例檢查（private 不得出現在 bus 上）
    assert is_private_on_bus(payload) is False


def test_same_input_public_vs_private_producer_diff():
    """同一結構的輸入: mode=group → 1 顆; mode=private → 0 顆（gate 真的在節制）。"""
    bus_public = _RecordingBus()
    _run(_make_producer(bus_public).on_agent_speak(_agent_speak_event(mode="group")))
    bus_private = _RecordingBus()
    _run(_make_producer(bus_private).on_agent_speak(_agent_speak_event(mode="private")))
    assert len(bus_public.published) == 1
    assert len(bus_private.published) == 0


def test_disabled_producer_publishes_nothing():
    """未接線／停用的語意: 0 生產端（連 public 也不發）。"""
    bus = _RecordingBus()
    producer = SocialWorldEventProducer(
        bus=bus, gate=SocialEventProducerGate(), enabled=False
    )
    _run(producer.on_agent_speak(_agent_speak_event(mode="group")))
    assert bus.published == []


def test_producer_skips_stub_dry_run_and_empty_text():
    bus = _RecordingBus()
    producer = _make_producer(bus)
    _run(producer.on_agent_speak(_agent_speak_event(mode="group", is_stub=True)))
    _run(producer.on_agent_speak(_agent_speak_event(mode="group", dry_run=True)))
    _run(producer.on_agent_speak(_agent_speak_event(mode="group", text="   ")))
    assert bus.published == []


def test_producer_truncates_long_content_to_contract_limit():
    bus = _RecordingBus()
    producer = _make_producer(bus)
    _run(producer.on_agent_speak(_agent_speak_event(mode="group", text="あ" * 900)))
    assert len(bus.published) == 1
    content = bus.published[0].payload["content"]
    assert len(content) == CONTENT_MAX_CHARS
    validate_social_world_event(bus.published[0].payload)  # 不得 raise


def test_emit_explicit_public_flag_is_passed_through_unchanged():
    """gate 的 §5.1 第 4 行（顯式公開）語意未被本票改動（只透傳 + 顯式空間）。"""
    bus = _RecordingBus()
    producer = _make_producer(bus)
    event = _run(
        producer.emit(
            actor_id="agent_ruka",
            channel_mode="private",
            channel=CHANNEL_DM,
            space_id=SPACE_LOUNGE,
            content="顯式公開測試",
            explicit_public=True,
        )
    )
    assert event is not None and len(bus.published) == 1
    assert bus.published[0].payload["visibility"] == VISIBILITY_PUBLIC
    assert bus.published[0].payload["space_id"] == SPACE_LOUNGE


def test_emit_fail_closed_when_space_is_not_a_valid_space():
    """channel="dm" 不是合法 space_id ⇒ payload 驗證 fail-closed 拒絕（不捏造空間）。"""
    bus = _RecordingBus()
    producer = _make_producer(bus)
    event = _run(
        producer.emit(
            actor_id="agent_ruka",
            channel_mode="private",
            channel=CHANNEL_DM,
            content="沒有空間的公開動態",
            explicit_public=True,
        )
    )
    assert event is None
    assert bus.published == []
    assert producer.get_stats()["validation_rejected"] == 1


def test_emit_publish_failure_is_isolated():
    """publish 例外不得外洩（失敗隔離, 不影響 AGENT_SPEAK 主路徑）。"""

    class _BoomBus:
        async def publish(self, event):
            raise RuntimeError("bus down")

    producer = _make_producer(_BoomBus())
    result = _run(
        producer.emit(
            actor_id="agent_ruka",
            channel_mode="group",
            channel=SPACE_LOUNGE,
            content="test",
        )
    )
    assert result is None
    assert producer.get_stats()["failed"] == 1


# ─────────────────────────────────────────────────────────────
# C. 生產腳本確實含本票接線（靜態; 0 執行 / 0 服務啟動）
# ─────────────────────────────────────────────────────────────


def test_production_script_contains_both_wirings():
    script = ROOT / "scripts" / "run_server.py"
    source = script.read_text(encoding="utf-8")
    ast.parse(source, filename=str(script))  # 語法仍然合法

    # 防線 3: per-agent firewall 接線
    assert "FirewalledSubmissionGate(" in source
    assert "firewall_wiring" in source
    # 防線 2 + SOCIAL_WORLD_EVENT 生產端
    assert "SocialWorldEventProducer(" in source
    assert "SocialEventProducerGate()" in source
    assert "EventType.AGENT_SPEAK" in source
    # 缺口登記時 grep identity_firewall scripts/ 命中 0 → 現在必須 > 0
    assert "identity_firewall" in source
