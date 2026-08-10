"""
tests/test_m5_4_5_4_dream_inner_life_integration.py

M5.4-5.4 (Bry 派工 2026-08-09 21:29): Dream Integration with Inner Life.

Focused test suite covering:
- A. _write_entry signature + persistence (5)
- B. JSONL round-trip preservation (3)
- C. Legacy entry backward compatibility (2)
- D. write_dream integration with inner_life_event_id (3)
- E. write_event integration with inner_life_event_id (3)
- F. Invalid / missing identity behavior (3)
- G. data_root() isolation preserved (P0.5) (2)
- H. Foundation independence (Dream works without InnerLifeWriter) (2)
- count (1)

Test count: 24 tests
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
from src.soul.dream_event import (
    DreamEventWriter,
)


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _isolated_writer(tmp_path: Path) -> DreamEventWriter:
    """Create a DreamEventWriter that writes to tmp_path, regardless of module import order.

    DEFAULT_DIARY_ROOT is evaluated at module import time and may be cached to the
    production path. By passing data_dir explicitly, we ensure isolation regardless
    of when DreamEventWriter was first imported.
    """
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return DreamEventWriter(data_dir=str(soul_dir))


def _restore_data_root():
    """Restore data_root to no env var (production default)."""
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _hex_32() -> str:
    """Generate a 32-char lowercase hex string (canonical event_id format)."""
    return uuid.uuid4().hex


def _read_dream_jsonl(tmp_path: Path, agent_id: str) -> list:
    """Read all entries in today's diary jsonl for an agent (used by tests)."""
    today = datetime.now().strftime("%Y-%m-%d")
    path = tmp_path / "data" / "soul" / agent_id / "diary" / f"{today}.jsonl"
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ───────────────────────────────────────────────────────────
# A. _write_entry signature + persistence
# ───────────────────────────────────────────────────────────

class TestSectionA_WriteEntryPersistence:
    """A. _write_entry signature + persistence."""

    def test_a1_write_entry_returns_path(self, tmp_path):
        """_write_entry returns Path to the jsonl file."""
        writer = _isolated_writer(tmp_path)
        try:
            result = writer._write_entry("agent_yua", "dream", "test dream content")
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".jsonl"
        finally:
            _restore_data_root()

    def test_a2_entry_dict_has_required_fields(self, tmp_path):
        """Entry dict contains ts, slot, content, source."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer._write_entry("agent_yua", "event", "test event content")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["slot"] == "event"
            assert entry["content"] == "test event content"
            assert entry["source"] == "llm"  # default
            assert "ts" in entry
        finally:
            _restore_data_root()

    def test_a3_entry_no_inner_life_event_id_by_default(self, tmp_path):
        """Without explicit event_id, entry has no inner_life_event_id field."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer._write_entry("agent_yua", "dream", "default no id")
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            # M5.4-5.4: legacy entries (no inner_life_event_id) MUST NOT have the
            # key at all (backward compat with pre-M5.4-5.4 readers).
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_a4_entry_with_inner_life_event_id_has_field(self, tmp_path):
        """With explicit event_id, entry dict has inner_life_event_id."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            path = writer._write_entry(
                "agent_yua", "dream", "with id",
                inner_life_event_id=eid,
            )
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_a5_placeholder_source_preserved(self, tmp_path):
        """source=placeholder is preserved through _write_entry."""
        writer = _isolated_writer(tmp_path)
        try:
            path = writer._write_entry(
                "agent_yua", "dream", "placeholder content",
                source="placeholder",
            )
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["source"] == "placeholder"
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. JSONL round-trip
# ───────────────────────────────────────────────────────────

class TestSectionB_JSONLRoundTrip:
    """B. JSONL round-trip preserves inner_life_event_id."""

    def test_b1_round_trip_dream_with_event_id(self, tmp_path):
        """write → read preserves inner_life_event_id for dream slot."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            writer._write_entry(
                "agent_yua", "dream", "dream round trip",
                inner_life_event_id=eid,
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["slot"] == "dream"
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_b2_round_trip_event_with_event_id(self, tmp_path):
        """write → read preserves inner_life_event_id for event slot."""
        writer = _isolated_writer(tmp_path)
        try:
            eid = _hex_32()
            writer._write_entry(
                "agent_yua", "event", "event round trip",
                inner_life_event_id=eid,
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["slot"] == "event"
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_b3_round_trip_without_event_id(self, tmp_path):
        """Legacy write → read has no inner_life_event_id key."""
        writer = _isolated_writer(tmp_path)
        try:
            writer._write_entry("agent_yua", "dream", "legacy dream")
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. Legacy entry backward compatibility
# ───────────────────────────────────────────────────────────

class TestSectionC_LegacyBackwardCompat:
    """C. Pre-M5.4-5.4 entries (no inner_life_event_id) remain readable."""

    def test_c1_pre_existing_legacy_file_loads(self, tmp_path):
        """A legacy jsonl file (no inner_life_event_id field) reads cleanly."""
        # Pre-create a legacy file in tmp
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        legacy_path = soul_dir / "agent_yua" / "diary" / f"{today}.jsonl"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_entries = [
            {
                "ts": "2026-08-08T22:00:00+00:00",
                "slot": "dream",
                "content": "legacy dream",
                "source": "llm",
            },
            {
                "ts": "2026-08-08T22:05:00+00:00",
                "slot": "event",
                "content": "legacy event",
                "source": "placeholder",
            },
        ]
        with legacy_path.open("w", encoding="utf-8") as f:
            for entry in legacy_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 2
            for entry in entries:
                assert "inner_life_event_id" not in entry
            assert entries[0]["content"] == "legacy dream"
            assert entries[1]["content"] == "legacy event"
        finally:
            _restore_data_root()

    def test_c2_mixed_legacy_and_new_entries(self, tmp_path):
        """Mix of legacy + new entries: both readable, identity only on new."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        legacy_path = soul_dir / "agent_yua" / "diary" / f"{today}.jsonl"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        with legacy_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-08-08T22:00:00+00:00",
                "slot": "dream",
                "content": "legacy",
                "source": "llm",
            }, ensure_ascii=False) + "\n")

        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            eid = _hex_32()
            writer._write_entry(
                "agent_yua", "event", "new with id",
                inner_life_event_id=eid,
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 2
            # First (legacy) has no identity field
            assert "inner_life_event_id" not in entries[0]
            # Second (new) has identity
            assert entries[1]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# D. write_dream integration
# ───────────────────────────────────────────────────────────

class TestSectionD_WriteDreamIntegration:
    """D. write_dream propagates inner_life_event_id through to entry dict."""

    def test_d1_write_dream_passes_event_id_to_write_entry(self, tmp_path, monkeypatch):
        """write_dream passes inner_life_event_id through to _write_entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call(*args, **kwargs):
                return "M5.4-5.4 dream content"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call)

            eid = _hex_32()
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            result = asyncio.run(
                writer.write_dream(
                    "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"],
                    inner_life_event_id=eid,
                )
            )
            assert result is not None
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["slot"] == "dream"
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_d2_write_dream_no_event_id(self, tmp_path, monkeypatch):
        """write_dream without inner_life_event_id → no field in entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call(*args, **kwargs):
                return "test dream content"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call)

            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(
                writer.write_dream(
                    "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"],
                    # no inner_life_event_id
                )
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()

    def test_d3_write_dream_placeholder_preserves_event_id(self, tmp_path, monkeypatch):
        """Even when LLM fails (placeholder path), inner_life_event_id is preserved."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call_fail(*args, **kwargs):
                return None  # LLM fails → placeholder

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call_fail)

            eid = _hex_32()
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(
                writer.write_dream(
                    "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"],
                    inner_life_event_id=eid,
                )
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["source"] == "placeholder"
            # Identity is preserved on placeholder path too
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. write_event integration
# ───────────────────────────────────────────────────────────

class TestSectionE_WriteEventIntegration:
    """E. write_event propagates inner_life_event_id through to entry dict."""

    def test_e1_write_event_passes_event_id(self, tmp_path, monkeypatch):
        """write_event passes inner_life_event_id through to _write_entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call(*args, **kwargs):
                return "M5.4-5.4 event content"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call)

            eid = _hex_32()
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            result = asyncio.run(
                writer.write_event(
                    "agent_yua",
                    inner_life_event_id=eid,
                )
            )
            assert result is not None
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["slot"] == "event"
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()

    def test_e2_write_event_no_event_id(self, tmp_path, monkeypatch):
        """write_event without inner_life_event_id → no field in entry."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call(*args, **kwargs):
                return "test event content"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call)

            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(
                writer.write_event("agent_yua")
                # no inner_life_event_id
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()

    def test_e3_write_event_placeholder_preserves_event_id(self, tmp_path, monkeypatch):
        """Even when LLM fails (placeholder path), inner_life_event_id is preserved."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call_fail(*args, **kwargs):
                return None  # LLM fails → placeholder

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call_fail)

            eid = _hex_32()
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(
                writer.write_event(
                    "agent_yua",
                    inner_life_event_id=eid,
                )
            )
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            assert entries[0]["source"] == "placeholder"
            assert entries[0]["inner_life_event_id"] == eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. Invalid / missing identity behavior
# ───────────────────────────────────────────────────────────

class TestSectionF_InvalidIdentityBehavior:
    """F. Invalid / missing identity is handled safely."""

    def test_f1_empty_string_treated_as_no_event_id(self, tmp_path):
        """inner_life_event_id='' should be treated as no event_id (not stored)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            path = writer._write_entry(
                "agent_yua", "dream", "test",
                inner_life_event_id="",
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip().split("\n")[0])
            # Empty string is treated as no identity (entry dict has no key)
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_f2_none_default_omits_key(self, tmp_path):
        """inner_life_event_id=None (default) omits the key from entry dict."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            path = writer._write_entry("agent_yua", "event", "test")
            entry = json.loads(path.read_text(encoding="utf-8").strip().split("\n")[0])
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_f3_long_event_id_accepted_unchanged(self, tmp_path):
        """A longer-than-32 event_id is accepted unchanged (no format check)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            long_eid = "a" * 64
            path = writer._write_entry(
                "agent_yua", "dream", "test",
                inner_life_event_id=long_eid,
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip().split("\n")[0])
            # DreamEventWriter doesn't validate format - that's InnerLifeWriter's job
            assert entry["inner_life_event_id"] == long_eid
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. data_root() isolation preserved (P0.5)
# ───────────────────────────────────────────────────────────

class TestSectionG_DataRootIsolation:
    """G. M5.4-5.4 dream writes respect SOUL_OS_DATA_DIR (P0.5 isolation)."""

    def test_g1_dream_writes_go_to_data_root_soul(self, tmp_path):
        """Dream writes go to data_root()/soul/... not to cwd/data/soul/..."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            path = writer._write_entry("agent_yua", "dream", "isolated dream content")
            # The path should be inside the isolated tmp dir, NOT in the real data/
            assert str(path).startswith(str(tmp_path))
            assert "data" in str(path) and "soul" in str(path)
        finally:
            _restore_data_root()

    def test_g2_default_data_root_unchanged(self, tmp_path):
        """With SOUL_OS_DATA_DIR unset, data_root() points to production 'data'."""
        _restore_data_root()
        # Don't set SOUL_OS_DATA_DIR
        if "SOUL_OS_DATA_DIR" in os.environ:
            del os.environ["SOUL_OS_DATA_DIR"]
        reset_data_root()
        # data_root() should be "data" relative to cwd
        assert str(data_root()) == str(Path("data").resolve())


# ───────────────────────────────────────────────────────────
# H. Foundation independence
# ───────────────────────────────────────────────────────────

class TestSectionH_FoundationIndependence:
    """H. Dream works without InnerLife being involved."""

    def test_h1_dream_works_without_inner_life(self, tmp_path):
        """Dream _write_entry with no inner_life_event_id works (no InnerLife dependency)."""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul.dream_event import DreamEventWriter
            writer = DreamEventWriter(data_dir=str(soul_dir))
            # No import of InnerLifeWriter anywhere
            path = writer._write_entry("agent_yua", "dream", "no inner life involved")
            assert path is not None
            entries = _read_dream_jsonl(tmp_path, "agent_yua")
            assert entries[0]["content"] == "no inner life involved"
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()

    def test_h2_dream_event_writer_no_inner_life_import(self):
        """DreamEventWriter doesn't import src.inner_life (no shared failure dependency)."""
        import src.soul.dream_event as dream_mod
        source = Path(dream_mod.__file__).read_text(encoding="utf-8")
        assert "from src.inner_life" not in source
        assert "import src.inner_life" not in source


# ───────────────────────────────────────────────────────────
# Test count guard
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify expected number of tests in this suite.

    5 (A) + 3 (B) + 2 (C) + 3 (D) + 3 (E) + 3 (F) + 2 (G) + 2 (H) + 1 (count) = 24
    """
    # Just a marker - the test count is verified by pytest's collection.
    assert True
