"""
tests/test_work_ports.py
Soul OS — DSH Multi-Agent MVP-2：DSH Adapter Boundary（ports + bridge）。

驗收（對照 logs/DSH-MVP-2-WORK-ORDER.md）：
- ports 不 import 任何 DSH type（anti-lock-in）
- ports 是結構型別（Protocol），adapter 不需繼承即可被 kernel 使用
- bridge protocol 是 language-neutral（JSON-serializable）
- bridge message envelope 必帶 event_id / timestamp / actor / source /
  causation/reference / schema_version
- 三種 message type：request / response / event
- single-writer rule 明確（kernel 唯一 writer）

執行：pytest tests/test_work_ports.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.work import bridge as bridge_mod
from src.work import ports as ports_mod
from src.work.bridge import (
    DURABLE_WRITER,
    READ_ONLY_ACTORS,
    BridgeMessage,
    BridgeMessageType,
    is_durable_writer,
)
from src.work.ports import (
    CapabilityResult,
    Checkpoint,
    Effect,
    ExperienceQuery,
    Intent,
    SoulExperience,
    SoulExperienceStore,
    SoulPresentationPort,
    SoulProjection,
    SoulRuntimePort,
    SoulRuntimeSnapshot,
    SoulWorldPort,
    Stimulus,
    WorldEvidence,
    WorldQuery,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

_DSH_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+[^\n]*(?:dsh|cordis)", re.IGNORECASE | re.MULTILINE
)


def _source_of(module) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


# ─────────────────────────────────────────────
# 1. ports 不 import DSH（anti-lock-in）
# ─────────────────────────────────────────────

def test_ports_do_not_import_dsh():
    """ports.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(ports_mod)), (
        "ports.py 不得 import DSH / Cordis type"
    )


def test_bridge_does_not_import_dsh():
    """bridge.py 不得 import 任何 DSH / Cordis type。"""
    assert not _DSH_IMPORT_RE.search(_source_of(bridge_mod)), (
        "bridge.py 不得 import DSH / Cordis type"
    )


def test_ports_data_types_serialize_without_dsh_strings():
    """ports 的資料型別序列化後不含任何 DSH type/id 字串。"""
    objs = [
        Stimulus(kind="message", payload={"text": "hi"}),
        Effect(kind="speak"),
        SoulRuntimeSnapshot(state={"phase": "idle"}),
        Checkpoint(state={"phase": "idle"}),
        WorldQuery(kind="weather"),
        WorldEvidence(kind="weather", data={"temp": 20}),
        Intent(capability="git.commit"),
        CapabilityResult(capability="git.commit", status="done"),
        SoulExperience(kind="diary", content={"text": "..."}),
        ExperienceQuery(kind="history"),
        SoulProjection(kind="wall", content={"state": "idle"}),
    ]
    for obj in objs:
        assert "dsh" not in obj.model_dump_json().lower()


# ─────────────────────────────────────────────
# 2. ports 是結構型別（Protocol）
# ─────────────────────────────────────────────

class _FakeRuntime:
    """不繼承任何 base class 的 adapter 實作（anti-lock-in 驗證）。"""
    def receive(self, stimulus): return []
    def tick(self, now): return []
    def recover(self, checkpoint): return {}
    def snapshot(self): return SoulRuntimeSnapshot()


class _FakeWorld:
    def observe(self, query): return []
    def act(self, intent, policy): return CapabilityResult(capability="x", status="done")


class _FakeStore:
    def append(self, experience): return None
    def query(self, query): return []
    def checkpoint(self): return Checkpoint()


class _FakePresentation:
    def publish(self, projection): return None


def test_ports_are_structural_protocols():
    """adapter 不需繼承 base class 即可滿足 port（runtime_checkable）。"""
    assert isinstance(_FakeRuntime(), SoulRuntimePort)
    assert isinstance(_FakeWorld(), SoulWorldPort)
    assert isinstance(_FakeStore(), SoulExperienceStore)
    assert isinstance(_FakePresentation(), SoulPresentationPort)


def _method_names(proto) -> set[str]:
    """回傳 Protocol 上宣告的方法名（不含 dunder / 內建屬性）。"""
    return {
        name
        for name, value in proto.__dict__.items()
        if callable(value) and not name.startswith("__")
    }


def test_ports_declare_expected_methods():
    """四個 port 各宣告 migration plan §3.2 規定的方法。"""
    assert _method_names(SoulRuntimePort) == {
        "receive", "tick", "recover", "snapshot",
    }
    assert _method_names(SoulWorldPort) == {"observe", "act"}
    assert _method_names(SoulExperienceStore) == {"append", "query", "checkpoint"}
    assert _method_names(SoulPresentationPort) == {"publish"}


# ─────────────────────────────────────────────
# 3. bridge message round-trip
# ─────────────────────────────────────────────

def test_bridge_message_roundtrip():
    """BridgeMessage 序列化 → 反序列化 round-trip（language-neutral）。"""
    msg = BridgeMessage(
        message_type=BridgeMessageType.REQUEST,
        actor="kernel",
        source="soul_kernel",
        causation="soul-event-1",
        reference="dsh-session-42",
        payload={"work_id": "work-1", "intent": "spawn"},
    )
    data = json.loads(msg.model_dump_json())
    msg2 = BridgeMessage(**data)

    assert msg2.event_id == msg.event_id
    assert msg2.schema_version == "1.0"
    assert msg2.message_type == BridgeMessageType.REQUEST
    assert msg2.actor == "kernel"
    assert msg2.source == "soul_kernel"
    assert msg2.causation == "soul-event-1"
    assert msg2.reference == "dsh-session-42"
    assert msg2.payload == {"work_id": "work-1", "intent": "spawn"}


def test_bridge_message_types_exactly_three():
    """message type 只能是 request / response / event。"""
    assert {m.value for m in BridgeMessageType} == {"request", "response", "event"}


def test_bridge_envelope_has_required_fields():
    """envelope 必帶 event_id / timestamp / actor / source / causation/reference / schema_version。"""
    required = {"event_id", "timestamp", "actor", "source", "causation", "reference", "schema_version"}
    assert required <= set(BridgeMessage.model_fields)


def test_bridge_reference_is_not_causal_truth():
    """reference（如 DSH sessionId）與 causation（Soul causal truth）是獨立欄位。"""
    msg = BridgeMessage(
        message_type=BridgeMessageType.EVENT,
        actor="kernel",
        source="soul_kernel",
        causation="soul-event-1",
        reference="dsh-session-42",
    )
    # DSH sessionId 只作 reference，不是 Soul causal truth
    assert msg.reference == "dsh-session-42"
    assert msg.causation == "soul-event-1"
    assert msg.reference != msg.causation


# ─────────────────────────────────────────────
# 4. single-writer rule
# ─────────────────────────────────────────────

def test_single_writer_rule_kernel_only():
    """kernel 是 durable state 的唯一 writer；DSH 側只讀不寫。"""
    assert DURABLE_WRITER == "kernel"
    assert is_durable_writer("kernel") is True
    assert is_durable_writer("dsh_adapter") is False
    assert is_durable_writer("dsh_session") is False
    assert is_durable_writer("dsh_runtime") is False


def test_read_only_actors_are_not_durable_writers():
    """READ_ONLY_ACTORS 內的所有 actor 都不是 durable writer。"""
    assert READ_ONLY_ACTORS == {"dsh_adapter", "dsh_session", "dsh_runtime"}
    for actor in READ_ONLY_ACTORS:
        assert is_durable_writer(actor) is False
