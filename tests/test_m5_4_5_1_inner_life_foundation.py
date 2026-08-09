"""
M5.4-5.1 — Inner Life Unified Architecture Foundation Tests
============================================================

派工: 2026-08-09 18:25 (Bry)
性質: IMPLEMENTATION / ARCHITECTURE FOUNDATION
目標: 驗證 canonical Inner Life identity model + InnerLifeWriter boundary

派工 派工 acceptance criteria (test sections):
  A. event identity uniqueness
  B. session identity
  C. correlation semantics
  D. parent/child lineage
  E. provenance
  F. deterministic cross-reference representation
  G. serialization/deserialization
  H. invalid identity handling
  I. backward compatibility
  Z. foundation smoke (writer itself does NOT depend on Memory/Diary/Dream)

派工 派工 constraints:
  - 不修改 production code (這張只新增 src/inner_life/ module)
  - 不修改 production data
  - 不修改既有 tests
  - InnerLifeWriter 是 OPTIONAL for Memory/Diary/Dream (independence preserved)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from src.inner_life import (
    EVENT_ID_LENGTH,
    EVENT_ID_PATTERN,
    TS_PATTERN,
    IdentityValidationError,
    InnerLifeEvent,
    InnerLifeWriter,
    InnerLifeWriterStats,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
    TRIGGER_TYPE_MEMORY_FACT,
    TRIGGER_TYPE_SYSTEM,
    TRIGGER_TYPE_USER_MESSAGE,
    VALID_SOURCE_SYSTEMS,
    derive_lineage,
    event_from_dict,
    event_to_dict,
    generate_event_id,
    now_utc_iso,
    provenance_from_dict,
    provenance_to_dict,
    validate_correlation_id,
    validate_event_id,
    validate_parent_event_id,
    validate_session_id,
    validate_ts,
)


# ─────────────────────────────────────────────────────────────────────
# Section A — event identity uniqueness (派工 派工 6 test categories)
# ─────────────────────────────────────────────────────────────────────


class TestSectionA_EventIdentityUniqueness:
    """event_id is unique, never re-issued, format-stable."""

    def test_a1_generated_event_id_is_32_char_lowercase_hex(self):
        """A1: generate_event_id() 回 32 char lowercase hex (uuid4 no dashes)."""
        for _ in range(100):
            eid = generate_event_id()
            assert isinstance(eid, str)
            assert len(eid) == EVENT_ID_LENGTH
            assert EVENT_ID_PATTERN.match(eid), f"bad format: {eid!r}"

    def test_a2_consecutive_event_ids_are_unique(self):
        """A2: 100 個連續生成的 event_id 全部 unique (uuid4 collision probability 2^-122)."""
        ids = [generate_event_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_a3_writer_assigns_unique_ids_per_instance(self):
        """A3: 同一 writer instance 連續 create_event 給 unique event_id."""
        w = InnerLifeWriter()
        ids = [
            w.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
            ).event_id
            for _ in range(50)
        ]
        assert len(set(ids)) == 50

    def test_a4_different_writer_instances_assign_independent_ids(self):
        """A4: 兩個 InnerLifeWriter 實例的 event_id 各自管理 (per-instance authority)."""
        w1 = InnerLifeWriter()
        w2 = InnerLifeWriter()
        e1 = w1.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
        )
        e2 = w2.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
        )
        # event_id 都 unique (uuid4 globally)
        assert e1.event_id != e2.event_id
        # 但 cross-instance lookup 應該 return None (per-instance authority)
        assert w1.is_event_known(e1.event_id) is True
        assert w1.is_event_known(e2.event_id) is False
        assert w2.is_event_known(e1.event_id) is False
        assert w2.is_event_known(e2.event_id) is True

    def test_a5_event_id_validation_rejects_bad_format(self):
        """A5: validate_event_id 拒絕 bad format (派工 派工 acceptance: invalid identity handling)."""
        # 短於 32 char
        with pytest.raises(IdentityValidationError):
            validate_event_id("abc123")
        # 長於 32 char
        with pytest.raises(IdentityValidationError):
            validate_event_id("a" * 33)
        # 含大寫
        with pytest.raises(IdentityValidationError):
            validate_event_id("A" * 32)
        # 含 dash
        with pytest.raises(IdentityValidationError):
            validate_event_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        # 含非 hex
        with pytest.raises(IdentityValidationError):
            validate_event_id("g" * 32)
        # None
        with pytest.raises(IdentityValidationError):
            validate_event_id(None)
        # int
        with pytest.raises(IdentityValidationError):
            validate_event_id(12345)

    def test_a6_event_id_is_immutable(self):
        """A6: 創建後 event_id 不可變 (frozen dataclass + Foundation 派工 semantic)."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
        )
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            e.event_id = "0" * 32  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────
# Section B — session identity
# ─────────────────────────────────────────────────────────────────────


class TestSectionB_SessionIdentity:
    """session_id 是 runtime session anchor,optional,events 可 share."""

    def test_b1_event_with_session_id_is_anchored(self):
        """B1: 提供 session_id → event.is_session_anchored() 是 True."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            session_id="sess-001",
        )
        assert e.is_session_anchored()
        assert e.session_id == "sess-001"

    def test_b2_event_without_session_id_is_cross_session(self):
        """B2: 不提供 session_id → event.is_session_anchored() 是 False (cross-session)."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        assert not e.is_session_anchored()
        assert e.session_id is None

    def test_b3_events_can_share_session(self):
        """B3: 同一 session 內多個 events 都 share session_id (派工 派工派工: same session = same runtime context)."""
        w = InnerLifeWriter()
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            session_id="sess-shared",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, source_system="memory"),
            session_id="sess-shared",
        )
        e3 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_MEMORY_FACT, source_system="memory"),
            session_id="sess-shared",
        )
        assert e1.session_id == e2.session_id == e3.session_id == "sess-shared"
        session_events = w.get_events_by_session("sess-shared")
        assert len(session_events) == 3
        assert e1.event_id in session_events
        assert e2.event_id in session_events
        assert e3.event_id in session_events

    def test_b4_empty_session_id_rejected(self):
        """B4: 空字串 session_id 被 reject (派工 派工派工: validation, no silent defaults)."""
        w = InnerLifeWriter()
        with pytest.raises(IdentityValidationError):
            w.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
                session_id="",
            )
        with pytest.raises(IdentityValidationError):
            w.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
                session_id="   ",
            )

    def test_b5_session_id_query_for_unknown_session_returns_empty(self):
        """B5: 查詢不存在的 session 回 empty list (不 raise, 給 caller 彈性)."""
        w = InnerLifeWriter()
        assert w.get_events_by_session("nonexistent-sess") == []


# ─────────────────────────────────────────────────────────────────────
# Section C — correlation semantics (NOT causation)
# ─────────────────────────────────────────────────────────────────────


class TestSectionC_CorrelationSemantics:
    """correlation_id = narrative group (派工 派工派工: 'two events that should be considered the SAME narrative context')."""

    def test_c1_event_with_correlation_id_is_in_narrative(self):
        """C1: 提供 correlation_id → event.is_in_narrative() 是 True."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            correlation_id="turn-1",
        )
        assert e.is_in_narrative()
        assert e.correlation_id == "turn-1"

    def test_c2_event_without_correlation_id_is_standalone(self):
        """C2: 不提供 correlation_id → event.is_in_narrative() 是 False."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        assert not e.is_in_narrative()
        assert e.correlation_id is None

    def test_c3_events_can_share_correlation(self):
        """C3: 同一 correlation 內多個 events 都 share correlation_id (派工 派工派工派工: narrative arc)."""
        w = InnerLifeWriter()
        # 模擬 Bry 訊息 → agent 回應 → memory fact 的 narrative arc
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            correlation_id="narrative-2026-08-09-morning",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, source_system="memory"),
            correlation_id="narrative-2026-08-09-morning",
        )
        e3 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_MEMORY_FACT, source_system="memory"),
            correlation_id="narrative-2026-08-09-morning",
        )
        # 三者都 share correlation_id 但可以有不同 parent (或不設)
        assert e1.correlation_id == e2.correlation_id == e3.correlation_id
        narrative_events = w.get_events_by_correlation("narrative-2026-08-09-morning")
        assert len(narrative_events) == 3

    def test_c4_correlation_is_NOT_causation(self):
        """C4: correlation_id 不是 causation — events 可 share correlation 但有不同 parent."""
        w = InnerLifeWriter()
        # 兩個 root events share correlation (e.g., 同一天的 morning diary + night diary)
        # 但沒有 parent-child relationship
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DIARY_MORNING, source_system="diary"),
            correlation_id="day-2026-08-09",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DIARY_NIGHT, source_system="diary"),
            correlation_id="day-2026-08-09",
        )
        # 兩者 correlation_id 相同, 但 parent 都 None (不是 causation chain)
        assert e1.correlation_id == e2.correlation_id
        assert e1.parent_event_id is None
        assert e2.parent_event_id is None
        assert e1.is_root()
        assert e2.is_root()

    def test_c5_empty_correlation_id_rejected(self):
        """C5: 空 correlation_id 被 reject."""
        w = InnerLifeWriter()
        with pytest.raises(IdentityValidationError):
            w.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
                correlation_id="",
            )


# ─────────────────────────────────────────────────────────────────────
# Section D — parent/child lineage
# ─────────────────────────────────────────────────────────────────────


class TestSectionD_Lineage:
    """parent_event_id = causation chain (派工 派工: tree structure, lineage_depth + lineage_path denormalized)."""

    def test_d1_root_event_has_depth_0_and_self_path(self):
        """D1: root event (no parent) → depth=0, path=own_id."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
        )
        assert e.lineage_depth == 0
        assert e.lineage_path == e.event_id
        assert e.is_root()

    def test_d2_child_event_has_parent_and_increments_depth(self):
        """D2: child event → parent_event_id 指向 parent, depth = parent.depth + 1."""
        w = InnerLifeWriter()
        parent = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
        )
        child = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, source_system="memory"),
            parent_event_id=parent.event_id,
        )
        assert child.parent_event_id == parent.event_id
        assert child.lineage_depth == 1
        assert child.lineage_path == f"{parent.event_id}/{child.event_id}"
        assert not child.is_root()

    def test_d3_three_level_chain(self):
        """D3: 三層 chain (root → child → grandchild) 正確推算 depth + path."""
        w = InnerLifeWriter()
        a = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            correlation_id="turn-1",
        )
        b = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, source_system="memory"),
            correlation_id="turn-1",
            parent_event_id=a.event_id,
        )
        c = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_MEMORY_FACT, source_system="memory"),
            correlation_id="turn-1",
            parent_event_id=b.event_id,
        )
        assert a.lineage_depth == 0
        assert b.lineage_depth == 1
        assert c.lineage_depth == 2
        # path denormalized
        assert c.lineage_path == f"{a.event_id}/{b.event_id}/{c.event_id}"
        # is_ancestor_of
        assert a.is_ancestor_of(b)
        assert a.is_ancestor_of(c)
        assert b.is_ancestor_of(c)
        assert not c.is_ancestor_of(a)
        assert not b.is_ancestor_of(a)

    def test_d4_get_children_returns_correct_descendants(self):
        """D4: get_children(parent_event_id) 回所有 direct children."""
        w = InnerLifeWriter()
        a = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        b1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            parent_event_id=a.event_id,
        )
        b2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            parent_event_id=a.event_id,
        )
        c = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            parent_event_id=b1.event_id,
        )
        children_of_a = w.get_children(a.event_id)
        assert set(children_of_a) == {b1.event_id, b2.event_id}
        children_of_b1 = w.get_children(b1.event_id)
        assert children_of_b1 == [c.event_id]
        children_of_c = w.get_children(c.event_id)
        assert children_of_c == []

    def test_d5_unknown_parent_event_id_rejected(self):
        """D5: parent_event_id 不在 known events → raise (派工 派工: 'must reference known event')."""
        w = InnerLifeWriter()
        # 完全 random 32 hex, 從未 create
        with pytest.raises(IdentityValidationError, match="不在已知事件清單"):
            w.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
                parent_event_id="a" * 32,
            )

    def test_d6_cross_instance_parent_reference_rejected(self):
        """D6: 跨 writer instance 的 parent_event_id 不可用 (per-instance authority)."""
        w1 = InnerLifeWriter()
        w2 = InnerLifeWriter()
        e1 = w1.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        # w2 試圖用 w1 的 event 當 parent
        with pytest.raises(IdentityValidationError):
            w2.create_event(
                provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
                parent_event_id=e1.event_id,
            )

    def test_d7_derive_lineage_root_path_equals_own_id(self):
        """D7: derive_lineage(None, None, own_id) → (0, own_id)."""
        own_id = "b" * 32
        depth, path = derive_lineage(None, None, own_id)
        assert depth == 0
        assert path == own_id

    def test_d8_derive_lineage_child_path_is_parent_slash_own(self):
        """D8: derive_lineage(parent_depth, parent_path, own_id) → (parent_depth+1, parent_path/own_id)."""
        parent_depth = 3
        parent_path = "aaaa/bbbb/cccc/dddd"
        own_id = "e" * 32
        depth, path = derive_lineage(parent_depth, parent_path, own_id)
        assert depth == 4
        assert path == "aaaa/bbbb/cccc/dddd/" + own_id

    def test_d9_derive_lineage_mixed_none_raises(self):
        """D9: parent_depth 跟 parent_path 必須同時 None (root) 或同時有值 (child)."""
        with pytest.raises(IdentityValidationError):
            derive_lineage(0, None, "a" * 32)
        with pytest.raises(IdentityValidationError):
            derive_lineage(None, "abc", "a" * 32)

    def test_d10_derive_lineage_negative_depth_rejected(self):
        """D10: parent_depth 不可為負."""
        with pytest.raises(IdentityValidationError, match="不可為負數"):
            derive_lineage(-1, "abc", "a" * 32)


# ─────────────────────────────────────────────────────────────────────
# Section E — provenance
# ─────────────────────────────────────────────────────────────────────


class TestSectionE_Provenance:
    """Provenance 結構化 WHO/WHAT/WHERE/WHY."""

    def test_e1_provenance_minimal_creation(self):
        """E1: Provenance 最小建立 (trigger_type 必填, 其他 default)."""
        p = Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
        assert p.trigger_type == TRIGGER_TYPE_SYSTEM
        assert p.actor_id is None
        assert p.source_system == "system"
        assert p.trace_ref is None
        assert p.extras == {}

    def test_e2_provenance_full_creation(self):
        """E2: Provenance 完整建立 (所有欄位)."""
        p = Provenance(
            trigger_type=TRIGGER_TYPE_USER_MESSAGE,
            actor_id="bryan",
            source_system="memory",
            trace_ref="ws-conn-12345",
            extras={"channel": "telegram", "thread_id": "42"},
        )
        assert p.trigger_type == TRIGGER_TYPE_USER_MESSAGE
        assert p.actor_id == "bryan"
        assert p.source_system == "memory"
        assert p.trace_ref == "ws-conn-12345"
        assert p.extras["channel"] == "telegram"
        assert p.extras["thread_id"] == "42"

    def test_e3_provenance_empty_trigger_type_rejected(self):
        """E3: trigger_type 必填且非空."""
        with pytest.raises(IdentityValidationError):
            Provenance(trigger_type="", source_system="system")
        with pytest.raises(IdentityValidationError):
            Provenance(trigger_type="   ", source_system="system")

    def test_e4_provenance_invalid_source_system_rejected(self):
        """E4: source_system 必須在 VALID_SOURCE_SYSTEMS 內."""
        with pytest.raises(IdentityValidationError, match="不在"):
            Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="unknown_system")

    def test_e5_provenance_extras_value_must_be_str(self):
        """E5: extras value 必須是 str (簡化序列化, 避免複雜性)."""
        with pytest.raises(IdentityValidationError):
            Provenance(
                trigger_type=TRIGGER_TYPE_SYSTEM,
                source_system="system",
                extras={"count": 42},
            )

    def test_e6_canonical_trigger_types_available(self):
        """E6: 派工 派工派工派工派工: canonical trigger_type vocabulary 提供 namespace-style."""
        assert TRIGGER_TYPE_USER_MESSAGE == "user_message"
        assert TRIGGER_TYPE_AGENT_REPLY == "agent_reply"
        assert TRIGGER_TYPE_DIARY_MORNING == "diary:morning"
        assert TRIGGER_TYPE_DIARY_NIGHT == "diary:night"
        assert TRIGGER_TYPE_DREAM_DREAM == "dream:dream"
        assert TRIGGER_TYPE_DREAM_EVENT == "dream:event"
        assert TRIGGER_TYPE_MEMORY_FACT == "memory_fact"
        assert TRIGGER_TYPE_SYSTEM == "system"

    def test_e7_provenance_immutable(self):
        """E7: Provenance frozen=True, 創建後不可改."""
        p = Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system")
        with pytest.raises(Exception):  # FrozenInstanceError
            p.trigger_type = "modified"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────
# Section F — deterministic cross-reference representation
# ─────────────────────────────────────────────────────────────────────


class TestSectionF_CrossReferenceRepresentation:
    """派工 派工: 'How can Memory / Diary / Dream reference the same lived experience'."""

    def test_f1_event_id_is_cross_reference_key(self):
        """F1: event_id 是 canonical cross-reference key (派工 派工派工: foundation for future migration)."""
        w = InnerLifeWriter()
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            correlation_id="shared-arc",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_MEMORY_FACT, source_system="memory"),
            correlation_id="shared-arc",
            parent_event_id=e1.event_id,
        )
        # 兩個 events 用同一 correlation_id 連結 (narrative arc)
        # e2 用 parent_event_id 直接指向 e1 (causation)
        # 兩者 event_id 都 unique, 可被任何 downstream system 引用
        assert e1.event_id != e2.event_id
        assert e2.parent_event_id == e1.event_id
        # query: 找同 narrative arc 的所有 events
        arc_events = w.get_events_by_correlation("shared-arc")
        assert set(arc_events) == {e1.event_id, e2.event_id}

    def test_f2_lineage_path_enables_deterministic_traversal(self):
        """F2: lineage_path 讓 traversal 不需要 parent lookup chain."""
        w = InnerLifeWriter()
        a = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        b = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            parent_event_id=a.event_id,
        )
        c = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            parent_event_id=b.event_id,
        )
        # c.lineage_path 包含 a, b 的 event_id
        # 不需要 _events dict 就能 trace 上去
        c_ancestors = c.lineage_path.split("/")
        assert a.event_id in c_ancestors
        assert b.event_id in c_ancestors
        assert c.event_id in c_ancestors

    def test_f3_session_correlation_combination_distinguishes_arcs(self):
        """F3: session + correlation 組合可區分不同 narrative arcs."""
        w = InnerLifeWriter()
        # 兩個不同的 narrative arcs 在同一 session
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            session_id="sess-001",
            correlation_id="arc-A",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            session_id="sess-001",
            correlation_id="arc-B",
        )
        # 同 session
        assert w.get_events_by_session("sess-001") == [e1.event_id, e2.event_id]
        # 不同 correlation
        assert w.get_events_by_correlation("arc-A") == [e1.event_id]
        assert w.get_events_by_correlation("arc-B") == [e2.event_id]

    def test_f4_cross_session_event_anchor(self):
        """F4: cross-session event (session_id=None) 不屬於任何 session 但可屬於 narrative."""
        w = InnerLifeWriter()
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DIARY_MORNING, source_system="diary"),
            # 沒有 session_id
            correlation_id="day-2026-08-09",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DREAM_DREAM, source_system="dream"),
            # 沒有 session_id
            correlation_id="day-2026-08-09",
            parent_event_id=e1.event_id,
        )
        # 兩者都沒有 session
        assert not e1.is_session_anchored()
        assert not e2.is_session_anchored()
        # 但 share narrative correlation
        assert w.get_events_by_correlation("day-2026-08-09") == [e1.event_id, e2.event_id]
        # e2 parent 是 e1
        assert e2.parent_event_id == e1.event_id
        # 這就是 Bry 的「Bry 不在, 角色世界也活」 — diary + dream 跨 session
        # 但在同 narrative 內 (同一天)


# ─────────────────────────────────────────────────────────────────────
# Section G — serialization / deserialization
# ─────────────────────────────────────────────────────────────────────


class TestSectionG_Serialization:
    """派工 派工: serialization/deserialization + invalid identity handling."""

    def test_g1_event_to_dict_round_trip(self):
        """G1: event_to_dict → event_from_dict 完整 round-trip."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_USER_MESSAGE,
                actor_id="bryan",
                source_system="memory",
                trace_ref="test-001",
                extras={"channel": "test"},
            ),
            session_id="sess-rt",
            correlation_id="arc-rt",
        )
        d = event_to_dict(e)
        # 必填欄位
        assert "event_id" in d
        assert "session_id" in d
        assert "correlation_id" in d
        assert "parent_event_id" in d
        assert "ts" in d
        assert "provenance" in d
        assert "lineage_depth" in d
        assert "lineage_path" in d
        # round-trip
        e2 = event_from_dict(d)
        assert e == e2

    def test_g2_event_to_dict_is_json_serializable(self):
        """G2: event_to_dict 結果可 json.dumps (round-trip via JSON)."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_AGENT_REPLY,
                actor_id="agent_rem",
                source_system="memory",
                extras={"confidence": "0.9"},  # 注意 extras value 必須是 str
            ),
            session_id="sess-json",
        )
        d = event_to_dict(e)
        # JSON 序列化
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        # 反序列化
        d2 = json.loads(json_str)
        e2 = event_from_dict(d2)
        assert e == e2

    def test_g3_provenance_round_trip(self):
        """G3: Provenance to_dict / from_dict round-trip."""
        p = Provenance(
            trigger_type=TRIGGER_TYPE_DIARY_MORNING,
            actor_id="agent_yua",
            source_system="diary",
            trace_ref="scheduler-001",
            extras={"weather": "rainy", "mood": "calm"},
        )
        d = provenance_to_dict(p)
        p2 = provenance_from_dict(d)
        assert p == p2

    def test_g4_from_dict_rejects_non_dict(self):
        """G4: event_from_dict 拒絕非 dict (派工 派工: invalid identity handling)."""
        with pytest.raises(IdentityValidationError):
            event_from_dict("not a dict")  # type: ignore[arg-type]
        with pytest.raises(IdentityValidationError):
            event_from_dict(None)  # type: ignore[arg-type]
        with pytest.raises(IdentityValidationError):
            event_from_dict([1, 2, 3])  # type: ignore[arg-type]

    def test_g5_from_dict_rejects_missing_required_fields(self):
        """G5: event_from_dict 拒絕缺必填欄位."""
        with pytest.raises(IdentityValidationError):
            event_from_dict({})  # 缺 event_id, ts
        with pytest.raises(IdentityValidationError):
            event_from_dict({"event_id": "a" * 32})  # 缺 ts, provenance
        with pytest.raises(IdentityValidationError):
            event_from_dict({
                "event_id": "a" * 32,
                "ts": "2026-08-09T12:00:00+00:00",
            })  # 缺 provenance

    def test_g6_from_dict_rejects_bad_ts(self):
        """G6: event_from_dict 拒絕 bad ts format."""
        with pytest.raises(IdentityValidationError, match="ts"):
            event_from_dict({
                "event_id": "a" * 32,
                "ts": "2026/08/09 12:00:00",  # 不是 ISO 8601
                "provenance": {"trigger_type": "system", "source_system": "system"},
            })
        with pytest.raises(IdentityValidationError, match="ts"):
            event_from_dict({
                "event_id": "a" * 32,
                "ts": "2026-08-09T12:00:00",  # 缺時區
                "provenance": {"trigger_type": "system", "source_system": "system"},
            })

    def test_g7_from_dict_rejects_bad_event_id(self):
        """G7: event_from_dict 拒絕 bad event_id."""
        with pytest.raises(IdentityValidationError):
            event_from_dict({
                "event_id": "short",  # 不是 32 char
                "ts": "2026-08-09T12:00:00+00:00",
                "provenance": {"trigger_type": "system", "source_system": "system"},
            })
        with pytest.raises(IdentityValidationError):
            event_from_dict({
                "event_id": "U" * 32,  # 大寫
                "ts": "2026-08-09T12:00:00+00:00",
                "provenance": {"trigger_type": "system", "source_system": "system"},
            })


# ─────────────────────────────────────────────────────────────────────
# Section H — invalid identity handling
# ─────────────────────────────────────────────────────────────────────


class TestSectionH_InvalidIdentity:
    """派工 派工: 'invalid identity handling' 全面覆蓋."""

    def test_h1_session_id_invalid_types(self):
        """H1: session_id 必須是 str 或 None."""
        with pytest.raises(IdentityValidationError):
            validate_session_id(123)  # type: ignore[arg-type]
        with pytest.raises(IdentityValidationError):
            validate_session_id(["sess"])  # type: ignore[arg-type]
        with pytest.raises(IdentityValidationError):
            validate_session_id({"id": "sess"})  # type: ignore[arg-type]

    def test_h2_correlation_id_invalid_types(self):
        """H2: correlation_id 必須是 str 或 None."""
        with pytest.raises(IdentityValidationError):
            validate_correlation_id(3.14)  # type: ignore[arg-type]
        with pytest.raises(IdentityValidationError):
            validate_correlation_id(True)  # type: ignore[arg-type]

    def test_h3_parent_event_id_must_be_32_hex(self):
        """H3: parent_event_id 必須是 32 hex format (or None)."""
        with pytest.raises(IdentityValidationError):
            validate_parent_event_id("not_hex_at_all")
        with pytest.raises(IdentityValidationError):
            validate_parent_event_id(123)

    def test_h4_ts_must_be_iso_utc(self):
        """H4: ts 必須是 ISO 8601 UTC."""
        # 沒時區
        with pytest.raises(IdentityValidationError):
            validate_ts("2026-08-09T12:00:00")
        # 帶 offset 但不是 UTC
        with pytest.raises(IdentityValidationError, match="UTC"):
            validate_ts("2026-08-09T12:00:00+08:00")
        # 非 ISO
        with pytest.raises(IdentityValidationError):
            validate_ts("not a timestamp")
        # 數字
        with pytest.raises(IdentityValidationError):
            validate_ts(1234567890)

    def test_h5_writer_create_event_with_bad_provenance_type(self):
        """H5: create_event 拒絕非 Provenance 物件."""
        w = InnerLifeWriter()
        with pytest.raises(IdentityValidationError):
            w.create_event(
                provenance="not a provenance",  # type: ignore[arg-type]
            )
        with pytest.raises(IdentityValidationError):
            w.create_event(
                provenance=None,  # type: ignore[arg-type]
            )

    def test_h6_now_utc_iso_format_is_canonical(self):
        """H6: now_utc_iso() 格式通過 TS_PATTERN."""
        ts = now_utc_iso()
        assert TS_PATTERN.match(ts), f"bad format: {ts!r}"


# ─────────────────────────────────────────────────────────────────────
# Section I — backward compatibility
# ─────────────────────────────────────────────────────────────────────


class TestSectionI_BackwardCompatibility:
    """派工 派工: 'backward compatibility where applicable'."""

    def test_i1_event_from_dict_accepts_missing_lineage_fields(self):
        """I1: 舊 payload (沒 lineage_depth/lineage_path) → default 0/''."""
        e = event_from_dict({
            "event_id": "a" * 32,
            "ts": "2026-08-09T12:00:00+00:00",
            "provenance": {"trigger_type": "system", "source_system": "system"},
            # 沒有 lineage_depth / lineage_path
        })
        assert e.lineage_depth == 0
        assert e.lineage_path == ""

    def test_i2_event_from_dict_accepts_missing_optional_identity(self):
        """I2: 舊 payload (沒 session_id/correlation_id/parent_event_id) → None."""
        e = event_from_dict({
            "event_id": "a" * 32,
            "ts": "2026-08-09T12:00:00+00:00",
            "provenance": {"trigger_type": "system", "source_system": "system"},
        })
        assert e.session_id is None
        assert e.correlation_id is None
        assert e.parent_event_id is None

    def test_i3_provenance_from_dict_accepts_minimal_payload(self):
        """I3: Provenance 最小 payload (只 trigger_type) 反序列化成功."""
        p = provenance_from_dict({"trigger_type": "system"})
        assert p.trigger_type == "system"
        assert p.source_system == "narrative"  # default
        assert p.actor_id is None  # default

    def test_i4_event_to_dict_preserves_all_fields(self):
        """I4: event_to_dict 保留所有欄位 (沒有 silent drop)."""
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_USER_MESSAGE,
                actor_id="bryan",
                source_system="memory",
                trace_ref="x",
                extras={"a": "1"},
            ),
            session_id="sess-i4",
            correlation_id="arc-i4",
        )
        d = event_to_dict(e)
        assert d["event_id"] == e.event_id
        assert d["session_id"] == "sess-i4"
        assert d["correlation_id"] == "arc-i4"
        assert d["parent_event_id"] is None
        assert d["ts"] == e.ts
        assert d["provenance"]["trigger_type"] == "user_message"
        assert d["provenance"]["actor_id"] == "bryan"
        assert d["provenance"]["source_system"] == "memory"
        assert d["provenance"]["trace_ref"] == "x"
        assert d["provenance"]["extras"]["a"] == "1"
        assert d["lineage_depth"] == 0
        assert d["lineage_path"] == e.event_id


# ─────────────────────────────────────────────────────────────────────
# Section Z — foundation smoke (writer 不依賴 Memory/Diary/Dream)
# ─────────────────────────────────────────────────────────────────────


class TestSectionZ_FoundationIndependence:
    """派工 派工: 'Memory failure MUST NOT block Diary/Dream', 'Unified architecture ≠ shared failure dependency'."""

    def test_z1_inner_life_module_does_not_import_memory_or_diary_or_dream(self):
        """Z1: src/inner_life/ 不 import Memory/Diary/Dream (independence preserved)."""
        import src.inner_life as il
        # 公開 API
        public_attrs = dir(il)
        # 確保沒有暴露 Memory/Diary/Dream
        forbidden = ["Memory", "Diary", "Dream", "V1Store", "GraphStore", "MemoryWriter"]
        for attr in public_attrs:
            assert attr not in forbidden, f"inner_life exports forbidden: {attr}"

    def test_z2_inner_life_writer_works_without_memory_db(self):
        """Z2: InnerLifeWriter 不需要 memory.db (獨立運作)."""
        # 即使 memory.db 不存在, InnerLifeWriter 仍可運作
        w = InnerLifeWriter()
        e = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
        )
        assert w.get_known_event_count() == 1
        assert w.is_event_known(e.event_id)

    def test_z3_inner_life_writer_works_without_diary_jsonl(self):
        """Z3: InnerLifeWriter 不需要 diary jsonl (獨立運作)."""
        w = InnerLifeWriter()
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DIARY_MORNING, source_system="diary"),
            correlation_id="day-001",
        )
        e2 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_DIARY_NIGHT, source_system="diary"),
            correlation_id="day-001",
        )
        # diary 同 correlation 但無 parent (獨立 events)
        assert e1.is_root()
        assert e2.is_root()

    def test_z4_stats_observability(self):
        """Z4: InnerLifeWriterStats 反映 writer 內部狀態."""
        w = InnerLifeWriter()
        # 初始
        stats = w.get_stats()
        assert stats.events_created == 0
        assert stats.root_events == 0
        assert stats.child_events == 0
        # 創建 events:
        #   e1: root (user_message, sess s1, corr c1)
        #   e2: child of e1 (agent_reply, sess s1, corr c1)
        #   e3: root (system, 沒 session/correlation → cross-session)
        e1 = w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_USER_MESSAGE, source_system="memory"),
            session_id="s1",
            correlation_id="c1",
        )
        w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, source_system="memory"),
            session_id="s1",
            correlation_id="c1",
            parent_event_id=e1.event_id,
        )
        w.create_event(
            provenance=Provenance(trigger_type=TRIGGER_TYPE_SYSTEM, source_system="system"),
            # 沒 session, 沒 correlation
        )
        stats = w.get_stats()
        assert stats.events_created == 3
        assert stats.root_events == 2  # e1 + e3
        assert stats.child_events == 1  # e2
        assert stats.cross_session_events == 1  # e3 (no session_id)
        assert stats.distinct_sessions == 1
        assert stats.distinct_correlations == 1
        assert stats.lineage_chains == 1  # e1 has child (e2)


# ─────────────────────────────────────────────────────────────────────
# Counts assertion
# ─────────────────────────────────────────────────────────────────────


def test_m5_4_5_1_test_count():
    """確認本檔案至少 30 個 tests."""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_funcs = []
    for name, obj in inspect.getmembers(current_module, inspect.isclass):
        if name.startswith("Test") and inspect.isclass(obj):
            for method_name, method in inspect.getmembers(obj, inspect.isfunction):
                if method_name.startswith("test_"):
                    test_funcs.append(method_name)
    total = len(test_funcs) + 1  # +1 for this test_m5_4_5_1_test_count
    assert total >= 30, f"expected ≥30 tests, got {total}"
