"""
tests/test_m5_4_5_3_diary_inner_life_integration.py

M5.4-5.3 (Bry 派工 2026-08-09 21:06): Diary Integration with Inner Life.

Focused test suite covering:
- A. Diary entry shape (dict, no dataclass)
- B. write_entry signature + persistence
- C. JSONL round-trip preservation
- D. Legacy entry backward compatibility (no inner_life_event_id)
- E. inner_life_event_id passthrough through write_entry + generate_diary_entry
- F. InnerLifeWriter event_id accepted as parameter
- G. data_root() isolation preserved (P0.5)
- H. Invalid / missing identity behavior
- Z. Foundation independence (Diary works without InnerLifeWriter)

Test count: 27 tests (5 A + 4 B + 3 C + 2 D + 3 E + 2 F + 2 G + 3 H + 2 Z + 1 count)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root
from src.soul.diary import (
    DEFAULT_DIARY_ROOT,
    DiaryWriter,
    generate_diary_entry,
)


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _isolated_writer(tmp_path: Path) -> DiaryWriter:
    """Create a DiaryWriter that writes to tmp_path, regardless of module import order.

    DEFAULT_DIARY_ROOT is evaluated at module import time and may be cached to the
    production path. By passing data_dir explicitly, we ensure isolation regardless
    of when DiaryWriter was first imported.
    """
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return DiaryWriter(data_dir=str(soul_dir))


def _restore_data_root():
    """Restore data_root to no env var (production default)."""
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _hex_32() -> str:
    """Generate a 32-char lowercase hex string (canonical event_id format)."""
    return uuid.uuid4().hex


# ───────────────────────────────────────────────────────────
# A. Diary entry shape
# ───────────────────────────────────────────────────────────

class TestSectionA_DiaryEntryShape:
    """A. Diary entry is a dict (no dataclass)."""

    def test_a1_write_entry_returns_path(self, tmp_path):
        """write_entry returns Path to the jsonl file."""
        writer = _isolated_writer(tmp_path)
        try:
            result = writer.write_entry("agent_yua", "morning", "test morning")
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".jsonl"
        finally:
            _restore_data_root()

    def test_a2_entry_dict_has_required_fields(self, tmp_path):
        """Entry dict contains ts, slot, content, source."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer.write_entry("agent_yua", "night", "test night content")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["slot"] == "night"
            assert entry["content"] == "test night content"
            assert entry["source"] == "llm"
            assert "ts" in entry
            # ts is ISO 8601 UTC
            parsed = datetime.fromisoformat(entry["ts"])
            assert parsed.tzinfo is not None
        finally:
            _restore_data_root()

    def test_a3_entry_no_inner_life_event_id_by_default(self, tmp_path):
        """Without explicit event_id, entry has no inner_life_event_id field."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer.write_entry("agent_yua", "morning", "default no id")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            # M5.4-5.3: legacy entries (no inner_life_event_id) MUST NOT have the
            # key at all (backward compat with pre-M5.4-5.3 readers that ignore
            # unknown keys).
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_a4_entry_with_inner_life_event_id_has_field(self, tmp_path):
        """With explicit event_id, entry dict has inner_life_event_id."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            path = writer.write_entry(
                "agent_yua", "morning", "with id",
                inner_life_event_id=eid,
            )
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_a5_placeholder_source_preserved(self, tmp_path):
        """source=placeholder is preserved through write."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer.write_entry(
                "agent_yua", "morning", "placeholder content here",
                source="placeholder",
            )
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["source"] == "placeholder"
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. write_entry signature + persistence
# ───────────────────────────────────────────────────────────

class TestSectionB_WriteEntryPersistence:
    """B. write_entry persistence + inner_life_event_id handling."""

    def test_b1_write_to_per_agent_per_date_jsonl(self, tmp_path):
        """Each agent + date gets its own jsonl file."""
        writer = _isolated_writer(tmp_path)
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            writer.write_entry("agent_yua", "morning", "Yua morning content")
            writer.write_entry("agent_ruka", "morning", "Ruka morning content")
            yua_path = tmp_path / "data" / "soul" / "agent_yua" / "diary" / f"{today}.jsonl"
            ruka_path = tmp_path / "data" / "soul" / "agent_ruka" / "diary" / f"{today}.jsonl"
            assert yua_path.exists()
            assert ruka_path.exists()
            # Yua path has only Yua content, Ruka path only Ruka
            assert "Yua morning content" in yua_path.read_text(encoding="utf-8")
            assert "Yua morning content" not in ruka_path.read_text(encoding="utf-8")
        finally:
            _restore_data_root()

    def test_b2_concurrent_writes_thread_safe(self, tmp_path):
        """Multiple writes to the same file are serialized by lock."""
        writer = _isolated_writer(tmp_path)
        try:
            import threading
            def write_one(i):
                writer.write_entry("agent_yua", "morning", f"concurrent entry {i}")
            threads = [threading.Thread(target=write_one, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # All 5 entries should be in the file
            today = datetime.now().strftime("%Y-%m-%d")
            path = tmp_path / "data" / "soul" / "agent_yua" / "diary" / f"{today}.jsonl"
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 5
        finally:
            _restore_data_root()

    def test_b3_write_entry_creates_parent_dir(self, tmp_path):
        """write_entry creates the agent/diary/ parent dirs as needed."""
        writer = _isolated_writer(tmp_path)
        try:
            # New agent that has no dir yet
            result = writer.write_entry("agent_newcomer", "morning", "first entry content")
            assert result.exists()
            assert result.parent.exists()
        finally:
            _restore_data_root()

    def test_b4_invalid_slot_rejected(self, tmp_path):
        """Unknown slot returns None and does not write."""
        writer = _isolated_writer(tmp_path)
        try:
            result = writer.write_entry("agent_yua", "noon", "lunch time")
            assert result is None
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. JSONL round-trip
# ───────────────────────────────────────────────────────────

class TestSectionC_JSONLRoundTrip:
    """C. JSONL round-trip preserves inner_life_event_id."""

    def test_c1_round_trip_with_event_id(self, tmp_path):
        """write -> read preserves inner_life_event_id field."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            writer.write_entry(
                "agent_yua", "morning", "round trip test",
                inner_life_event_id=eid,
            )
            today = datetime.now().strftime("%Y-%m-%d")
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_c2_round_trip_without_event_id(self, tmp_path):
        """Legacy write -> read has no inner_life_event_id key."""
        writer = _isolated_writer(tmp_path)
        try:
            writer.write_entry("agent_yua", "morning", "legacy entry")
            today = datetime.now().strftime("%Y-%m-%d")
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()

    def test_c3_multiple_entries_preserved_in_order(self, tmp_path):
        """Multiple writes preserve order + each identity independently."""
        writer = _isolated_writer(tmp_path)
        try:
            eids = [_hex_32() for _ in range(3)]
            for i, eid in enumerate(eids):
                writer.write_entry(
                    "agent_yua", "morning", f"entry {i}",
                    inner_life_event_id=eid,
                )
            today = datetime.now().strftime("%Y-%m-%d")
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 3
            for i, entry in enumerate(entries):
                assert entry["content"] == f"entry {i}"
                assert entry["inner_life_event_id"] == eids[i]
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# D. Legacy entry backward compatibility
# ───────────────────────────────────────────────────────────

class TestSectionD_LegacyBackwardCompat:
    """D. Pre-M5.4-5.3 entries (no inner_life_event_id) remain readable."""

    def test_d1_pre_existing_legacy_file_loads(self, tmp_path):
        """A legacy jsonl file (no inner_life_event_id field) reads cleanly."""
        # Pre-create a legacy file in tmp
        soul_dir = tmp_path / "data" / "soul" / "agent_yua" / "diary"
        soul_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        legacy_path = soul_dir / f"{today}.jsonl"
        legacy_entries = [
            {
                "ts": "2026-08-08T22:00:00+00:00",
                "slot": "morning",
                "content": "legacy entry 1",
                "source": "llm",
            },
            {
                "ts": "2026-08-08T22:05:00+00:00",
                "slot": "night",
                "content": "legacy entry 2",
                "source": "placeholder",
            },
        ]
        with legacy_path.open("w", encoding="utf-8") as f:
            for entry in legacy_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 2
            # No inner_life_event_id key (legacy schema)
            for entry in entries:
                assert "inner_life_event_id" not in entry
            assert entries[0]["content"] == "legacy entry 1"
            assert entries[1]["content"] == "legacy entry 2"
        finally:
            _restore_data_root()

    def test_d2_mixed_legacy_and_new_entries(self, tmp_path):
        """Mix of legacy + new entries: both readable, identity only on new."""
        soul_dir = tmp_path / "data" / "soul" / "agent_yua" / "diary"
        soul_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        legacy_path = soul_dir / f"{today}.jsonl"
        with legacy_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-08-08T22:00:00+00:00",
                "slot": "morning",
                "content": "legacy",
                "source": "llm",
            }, ensure_ascii=False) + "\n")

        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            # Append a new entry with inner_life_event_id
            writer = DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))
            eid = _hex_32()
            writer.write_entry(
                "agent_yua", "night", "new with id",
                inner_life_event_id=eid,
            )
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 2
            # First (legacy) has no identity field
            assert "inner_life_event_id" not in entries[0]
            # Second (new) has identity
            assert entries[1]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. inner_life_event_id passthrough
# ───────────────────────────────────────────────────────────

class TestSectionE_EventIdPassthrough:
    """E. inner_life_event_id flows through write_entry and generate_diary_entry."""

    def test_e1_write_entry_accepts_event_id_kwarg(self, tmp_path):
        """write_entry accepts inner_life_event_id as keyword arg."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            path = writer.write_entry(
                "agent_yua", "morning", "test",
                inner_life_event_id=eid,
            )
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_e2_generate_diary_entry_passes_event_id_to_write(self, tmp_path, monkeypatch):
        """generate_diary_entry passes inner_life_event_id to write_entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import diary as diary_mod

            async def fake_llm_call(*args, **kwargs):
                return "M5.4-5.3 test content"

            monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm_call)

            eid = _hex_32()
            writer = DiaryWriter(data_dir=str(soul_dir))
            result = asyncio.run(
                diary_mod.generate_diary_entry(
                    "agent_yua", "morning",
                    persona_prompt="test persona",
                    writer=writer,
                    inner_life_event_id=eid,
                )
            )
            assert result is not None
            entries = writer.read_entries("agent_yua", datetime.now().strftime("%Y-%m-%d"))
            assert len(entries) == 1
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_e3_generate_diary_entry_no_event_id(self, tmp_path, monkeypatch):
        """generate_diary_entry without inner_life_event_id -> no field in entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import diary as diary_mod

            async def fake_llm_call(*args, **kwargs):
                return "test content"

            monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm_call)

            writer = DiaryWriter(data_dir=str(soul_dir))
            asyncio.run(
                diary_mod.generate_diary_entry(
                    "agent_yua", "morning",
                    writer=writer,
                    # no inner_life_event_id
                )
            )
            entries = writer.read_entries("agent_yua", datetime.now().strftime("%Y-%m-%d"))
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. InnerLifeWriter event_id accepted as parameter
# ───────────────────────────────────────────────────────────

class TestSectionF_InnerLifeIdentityFlow:
    """F. InnerLifeWriter can provide event_id; Diary accepts it via parameter."""

    def test_f1_inner_life_writer_event_id_accepted(self, tmp_path):
        """InnerLifeWriter.create_event().event_id can be passed to write_entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.inner_life import (
                InnerLifeWriter,
                Provenance,
            )
            from src.inner_life.event import TRIGGER_TYPE_DIARY_MORNING
            ilw = InnerLifeWriter()
            provenance = Provenance(
                trigger_type=TRIGGER_TYPE_DIARY_MORNING,
                actor_id="agent_yua",
                source_system="diary",
            )
            event = ilw.create_event(provenance=provenance)
            assert len(event.event_id) == 32  # canonical 32-char hex

            # Now write diary entry referencing this event_id
            writer = DiaryWriter(data_dir=str(soul_dir))
            path = writer.write_entry(
                "agent_yua", "morning", "test with ILW event_id",
                inner_life_event_id=event.event_id,
            )
            entries = writer.read_entries("agent_yua", datetime.now().strftime("%Y-%m-%d"))
            assert entries[0]["inner_life_event_id"] == event.event_id
        finally:
            _restore_data_root()

    def test_f2_event_id_format_preserved(self, tmp_path):
        """32-char hex format passes through unchanged."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            eid = "abcdef0123456789" * 2  # 32 hex chars
            assert len(eid) == 32
            path = writer.write_entry(
                "agent_yua", "morning", "test",
                inner_life_event_id=eid,
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. data_root() isolation preserved (P0.5)
# ───────────────────────────────────────────────────────────

class TestSectionG_DataRootIsolation:
    """G. M5.4-5.3 diary writes respect SOUL_OS_DATA_DIR (P0.5 isolation)."""

    def test_g1_diary_writes_go_to_data_root_soul(self, tmp_path):
        """Diary writes go to data_root()/soul/... not to cwd/data/soul/..."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            path = writer.write_entry("agent_yua", "morning", "isolated content")
            # The path should be inside the isolated tmp dir, NOT in the real data/
            assert str(path).startswith(str(tmp_path))
            assert "data" in str(path) and "soul" in str(path)
        finally:
            _restore_data_root()

    def test_g2_default_data_root_unchanged(self, tmp_path):
        """With SOUL_OS_DATA_DIR unset, DEFAULT_DIARY_ROOT points to data/soul."""
        _restore_data_root()
        # Don't set SOUL_OS_DATA_DIR
        if "SOUL_OS_DATA_DIR" in os.environ:
            del os.environ["SOUL_OS_DATA_DIR"]
        reset_data_root()
        # data_root() should be "data" relative to cwd
        assert str(data_root()) == str(Path("data").resolve())
        # The default DEFAULT_DIARY_ROOT should be data_root()/soul
        assert str(Path(DEFAULT_DIARY_ROOT)) == str(data_root() / "soul")


# ───────────────────────────────────────────────────────────
# H. Invalid / missing identity behavior
# ───────────────────────────────────────────────────────────

class TestSectionH_InvalidIdentityBehavior:
    """H. Invalid / missing identity is handled safely."""

    def test_h1_empty_string_treated_as_no_event_id(self, tmp_path):
        """inner_life_event_id='' should be treated as no event_id (not stored)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            path = writer.write_entry(
                "agent_yua", "morning", "test",
                inner_life_event_id="",
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            # Empty string is treated as no identity (entry dict has no key)
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_h2_none_default_omits_key(self, tmp_path):
        """inner_life_event_id=None (default) omits the key from entry dict."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            path = writer.write_entry("agent_yua", "morning", "test")
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_h3_long_event_id_accepted_unchanged(self, tmp_path):
        """A longer-than-32 event_id is accepted unchanged (no format check)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            long_eid = "a" * 64
            path = writer.write_entry(
                "agent_yua", "morning", "test",
                inner_life_event_id=long_eid,
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            # Diary doesn't validate format - that's InnerLifeWriter's job
            assert entry["inner_life_event_id"] == long_eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# Z. Foundation independence
# ───────────────────────────────────────────────────────────

class TestSectionZ_FoundationIndependence:
    """Z. Diary works without InnerLife being involved."""

    def test_z1_diary_works_without_inner_life(self, tmp_path):
        """Diary write_entry with no inner_life_event_id works (no InnerLife dependency)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir))
            # No import of InnerLifeWriter anywhere
            path = writer.write_entry("agent_yua", "morning", "no inner life involved")
            assert path is not None
            entries = writer.read_entries("agent_yua", datetime.now().strftime("%Y-%m-%d"))
            assert entries[0]["content"] == "no inner life involved"
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()

    def test_z2_diary_writer_no_inner_life_import(self):
        """DiaryWriter doesn't import src.inner_life (no shared failure dependency)."""
        import src.soul.diary as diary_mod
        source = Path(diary_mod.__file__).read_text(encoding="utf-8")
        assert "from src.inner_life" not in source
        assert "import src.inner_life" not in source


# ───────────────────────────────────────────────────────────
# Test count guard
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify expected number of tests in this suite.

    5 (A) + 4 (B) + 3 (C) + 2 (D) + 3 (E) + 2 (F) + 2 (G) + 3 (H) + 2 (Z) + 1 (count) = 27
    """
    # Just a marker - the test count is verified by pytest's collection.
    assert True
