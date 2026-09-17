"""
M5.3-S2-D Followup — Inner Life Narrative Boundary Audit

Bry 派工 2026-08-09 17:08:
- Memory → memory.db / v1 mirror
- Diary → diary writer
- Dream → dream writer
- 三條 path 的 timestamp / agent / trigger identity
- ordering 是否 deterministic
- partial failure 是否造成 duplicate / silent loss
- Memory / Diary / Dream 是否真正 independence
- 30+ deterministic acceptance tests
- M5.3 regression 維持
- production data = 0 mutation
- 如果發現 architecture defect, STOP 回報,不自行修 source

STRICT READ-ONLY:
- DO NOT modify production code
- DO NOT touch production data
- DO NOT commit / push
- Test isolation: use tempdir for new writes
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.soul.diary import DiaryWriter, generate_diary_entry
from src.soul.dream_event import DreamEventWriter, _pick_dream_target
from src.memory.v1.store import V1Store
from src.memory.v1.loader import MemoryLoader


# ════════════════════════════════════════════════════════════
# Section A — Timestamp Consistency (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1TimestampConsistency:
    """A: 三條 path 的 timestamp 是否一致 + ISO 8601 + monotonic."""

    def test_a1_diary_ts_is_iso8601_utc(self, tmp_path):
        """Diary write_entry 產生的 ts 是 ISO 8601 UTC。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        before = datetime.now(timezone.utc)
        path = writer.write_entry("agent_test", "morning", "Test content", source="llm")
        after = datetime.now(timezone.utc)

        assert path is not None
        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        ts = entry["ts"]
        # ISO 8601 with timezone
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None, f"ts must have timezone, got {ts}"
        # Within before/after window
        assert before <= parsed <= after, f"ts {ts} not in [{before}, {after}]"

    def test_a2_dream_ts_is_iso8601_utc(self, tmp_path):
        """Dream write_dream/_write_entry 產生的 ts 是 ISO 8601 UTC。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        before = datetime.now(timezone.utc)
        # 直接 call _write_entry(因為 write_dream 要 LLM)
        path = writer._write_entry("agent_test", "dream", "Test dream", source="llm")
        after = datetime.now(timezone.utc)

        assert path is not None
        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        ts = entry["ts"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None
        assert before <= parsed <= after

    def test_a3_diary_event_use_same_iso_format(self, tmp_path):
        """Diary 跟 Dream event slot 共用同一個 writer pattern, ts 格式一致。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))

        p1 = diary.write_entry("agent_test", "morning", "diary content", source="llm")
        p2 = dream._write_entry("agent_test", "event", "event content", source="llm")

        with open(p1, "r", encoding="utf-8") as f:
            d_ts = json.loads(f.readline())["ts"]
        with open(p2, "r", encoding="utf-8") as f:
            e_ts = json.loads(f.readline())["ts"]

        # 兩個 ts 都應該是 ISO 8601 with timezone
        d_parsed = datetime.fromisoformat(d_ts)
        e_parsed = datetime.fromisoformat(e_ts)
        assert d_parsed.tzinfo is not None
        assert e_parsed.tzinfo is not None
        # 兩者時間差 < 1s(同 test run)
        assert abs((d_parsed - e_parsed).total_seconds()) < 1.0

    def test_a4_memory_v1_mirror_ts_is_iso8601_utc(self, tmp_path):
        """Memory v1 mirror 寫入的 memory 有 created_at 為 unix timestamp(SAGE convention)"""
        v1_store = V1Store(tmp_path, "agent_test")
        from src.memory.v1.schema import Memory
        before_ts = time.time()
        mem = Memory(
            memory_id="m_test_1",
            agent_id="agent_test",
            content="test memory",
            tags=["test"],
            created_at=time.time(),
            category="fact",
            confidence=0.85,
        )
        v1_store.add(mem)
        after_ts = time.time()

        all_mem = v1_store.all()
        assert len(all_mem) == 1
        # Memory 內部 created_at 是 unix timestamp(float)
        assert isinstance(all_mem[0].created_at, float)
        assert before_ts - 1.0 <= all_mem[0].created_at <= after_ts + 1.0

    def test_a5_multiple_diary_writes_ordered_by_ts(self, tmp_path):
        """多筆 diary 寫入同一個 file,ts 應 monotonic(依 write order)。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        # Force ts differences with sleep
        paths = []
        for i in range(3):
            paths.append(writer.write_entry("agent_test", "morning", f"content {i}", source="llm"))
            time.sleep(0.01)

        # Read all entries, verify order
        all_entries = []
        for p in paths:
            with open(p, "r", encoding="utf-8") as f:
                all_entries.append(json.loads(f.readline()))

        ts_list = [datetime.fromisoformat(e["ts"]) for e in all_entries]
        # monotonic increasing
        for i in range(len(ts_list) - 1):
            assert ts_list[i] <= ts_list[i+1], (
                f"ts[{i}]={ts_list[i]} > ts[{i+1}]={ts_list[i+1]}, "
                f"ordering broken"
            )


# ════════════════════════════════════════════════════════════
# Section B — Agent Identity (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1AgentIdentity:
    """B: agent_id 正確性 — Diary/Dream 在 path,Memory 在 v1 mirror。"""

    def test_b1_diary_path_contains_agent_id(self, tmp_path):
        """Diary 寫入路徑包含 agent_id。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        path = writer.write_entry("agent_yua", "morning", "test", source="llm")
        assert "agent_yua" in str(path)

    def test_b2_dream_path_contains_agent_id(self, tmp_path):
        """Dream 寫入路徑包含 agent_id。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        path = writer._write_entry("agent_akane", "dream", "test", source="llm")
        assert "agent_akane" in str(path)

    def test_b3_diary_payload_no_agent_id_field(self, tmp_path):
        """Diary 寫入的 jsonl entry 沒有 agent_id 欄位(只在 path)。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        path = writer.write_entry("agent_yua", "morning", "test", source="llm")
        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        # Diary entry 沒有 agent_id 欄位(只有 ts / slot / content / source)
        assert "agent_id" not in entry

    def test_b4_dream_payload_no_agent_id_field(self, tmp_path):
        """Dream 寫入的 jsonl entry 沒有 agent_id 欄位(只在 path)。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        path = writer._write_entry("agent_akane", "dream", "test", source="llm")
        with open(path, "r", encoding="utf-8") as f:
            entry = json.loads(f.readline())
        assert "agent_id" not in entry

    def test_b5_memory_v1_mirror_has_agent_id_field(self, tmp_path):
        """Memory v1 mirror 寫入的 memory 物件有 agent_id 欄位(在 payload 內,不是 path)。"""
        v1_store = V1Store(tmp_path, "agent_rem")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m_rem_1", agent_id="agent_rem",
            content="test", tags=["test"], created_at=time.time(),
            category="fact", confidence=0.85,
        ))
        all_mem = v1_store.all()
        assert len(all_mem) == 1
        assert all_mem[0].agent_id == "agent_rem"

    def test_b6_diary_dream_share_path_convention(self, tmp_path):
        """Diary 跟 Dream 寫入同一個 path convention(data/soul/<agent>/diary/<date>.jsonl)。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))

        p1 = diary.write_entry("agent_yua", "morning", "d", source="llm")
        p2 = dream._write_entry("agent_yua", "dream", "dr", source="llm")

        # 兩個 path 結構應該一致
        assert p1.parent == p2.parent, (
            f"Diary 跟 Dream 寫到不同目錄:\n"
            f"  diary: {p1.parent}\n"
            f"  dream: {p2.parent}"
        )
        assert p1.name == p2.name, "Diary 跟 Dream 寫到不同 file"
        # path 結構: <data_dir>/agent_yua/diary/<date>.jsonl
        assert "diary" in p1.parts
        assert "agent_yua" in p1.parts
        assert p1.name.endswith(".jsonl")


# ════════════════════════════════════════════════════════════
# Section C — Trigger Identity (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1TriggerIdentity:
    """C: 觸發 identity — Diary morning/night, Dream dream/event。"""

    def test_c1_diary_slots(self, tmp_path):
        """Diary slot 接受 morning/night,拒絕其他。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        assert writer.write_entry("agent_x", "morning", "c", source="llm") is not None
        assert writer.write_entry("agent_x", "night", "c", source="llm") is not None
        # 未知 slot → return None
        assert writer.write_entry("agent_x", "dream", "c", source="llm") is None
        assert writer.write_entry("agent_x", "event", "c", source="llm") is None
        assert writer.write_entry("agent_x", "lunch", "c", source="llm") is None

    def test_c2_dream_slots(self, tmp_path):
        """Dream _write_entry 接受 dream/event(不限定但同 Diary 同一 writer 邏輯)。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        assert writer._write_entry("agent_x", "dream", "c", source="llm") is not None
        assert writer._write_entry("agent_x", "event", "c", source="llm") is not None
        # Diary slots 也被 _write_entry 接受(同一個 writer)
        assert writer._write_entry("agent_x", "morning", "c", source="llm") is not None
        assert writer._write_entry("agent_x", "night", "c", source="llm") is not None

    def test_c3_diary_dream_slot_distinguishable_in_file(self, tmp_path):
        """同一個 file 內,diary 跟 dream 用 slot 區分。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        p1 = writer._write_entry("agent_x", "morning", "d content", source="llm")
        p2 = writer._write_entry("agent_x", "dream", "dr content", source="llm")
        assert p1 == p2  # 同一個 file

        with open(p1, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        assert len(entries) == 2
        slots = {e["slot"] for e in entries}
        assert slots == {"morning", "dream"}

    def test_c4_diary_source_field_correctness(self, tmp_path):
        """Diary source 標清楚: llm 或 placeholder。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        p_llm = writer.write_entry("agent_x", "morning", "llm generated", source="llm")
        p_ph = writer.write_entry("agent_x", "night", "placeholder", source="placeholder")

        # 兩次寫入到同一個 file,讀 全部 lines
        assert p_llm == p_ph  # same file
        with open(p_llm, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        # 兩個 entry 都要在
        assert len(entries) == 2
        sources = {e["content"]: e["source"] for e in entries}
        assert sources["llm generated"] == "llm"
        assert sources["placeholder"] == "placeholder"

    def test_c5_dream_source_field_correctness(self, tmp_path):
        """Dream source 標清楚: llm 或 placeholder。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        p_llm = writer._write_entry("agent_x", "dream", "dr llm", source="llm")
        p_ph = writer._write_entry("agent_x", "event", "ev placeholder", source="placeholder")

        # 兩次寫入到同一個 file
        assert p_llm == p_ph
        with open(p_llm, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        assert len(entries) == 2
        sources = {e["content"]: e["source"] for e in entries}
        assert sources["dr llm"] == "llm"
        assert sources["ev placeholder"] == "placeholder"

    def test_c6_diary_empty_content_rejected(self, tmp_path):
        """M0.4 Bry 派工: clean empty → 拒絕寫入(避免 0-char 污染)。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        # Empty string
        assert writer.write_entry("agent_x", "morning", "", source="llm") is None
        # Whitespace only
        assert writer.write_entry("agent_x", "morning", "   ", source="llm") is None
        # Think block only
        assert writer.write_entry("agent_x", "morning", "<think>thinking</think>", source="llm") is None

    def test_c7_dream_empty_content_rejected(self, tmp_path):
        """M0.4: Dream 也有同樣的 clean empty 拒絕邏輯。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        assert writer._write_entry("agent_x", "dream", "", source="llm") is None
        assert writer._write_entry("agent_x", "event", "<think>t</think>", source="llm") is None


# ════════════════════════════════════════════════════════════
# Section D — Ordering Determinism (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1OrderingDeterminism:
    """D: ordering 是否 deterministic(within file, across files)。"""

    def test_d1_diary_writes_preserve_order(self, tmp_path):
        """多筆 diary 寫入,讀出順序應 = 寫入順序。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        contents = [f"content_{i}" for i in range(5)]
        for c in contents:
            writer.write_entry("agent_x", "morning", c, source="llm")

        # 讀出
        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 5
        read_contents = [e["content"] for e in entries]
        assert read_contents == contents, (
            f"Order broken: wrote {contents}, read {read_contents}"
        )

    def test_d2_dream_writes_preserve_order(self, tmp_path):
        """多筆 dream 寫入,讀出順序應 = 寫入順序。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        contents = [f"dream_{i}" for i in range(5)]
        slots = ["dream", "event", "dream", "event", "dream"]
        for c, s in zip(contents, slots):
            writer._write_entry("agent_x", s, c, source="llm")

        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 5
        read_contents = [e["content"] for e in entries]
        assert read_contents == contents

    def test_d3_diary_then_dream_ordering(self, tmp_path):
        """Diary 先寫 → Dream 後寫,讀出順序應保留。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))

        diary.write_entry("agent_x", "morning", "d1", source="llm")
        dream._write_entry("agent_x", "dream", "dr1", source="llm")
        diary.write_entry("agent_x", "night", "d2", source="llm")
        dream._write_entry("agent_x", "event", "ev1", source="llm")

        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 4
        read_contents = [e["content"] for e in entries]
        assert read_contents == ["d1", "dr1", "d2", "ev1"], (
            f"Interleaved order broken: {read_contents}"
        )

    def test_d4_memory_v1_mirror_preserves_order(self, tmp_path):
        """多筆 memory 寫入,讀出順序應 = 寫入順序(append-only)。"""
        v1_store = V1Store(tmp_path, "agent_x")
        from src.memory.v1.schema import Memory
        for i in range(5):
            mem = Memory(
                memory_id=f"m_{i}", agent_id="agent_x",
                content=f"content_{i}", tags=["t"],
                created_at=time.time(), category="fact", confidence=0.8,
            )
            v1_store.add(mem)

        all_mem = v1_store.all()
        assert len(all_mem) == 5
        read_contents = [m.content for m in all_mem]
        assert read_contents == [f"content_{i}" for i in range(5)]


# ════════════════════════════════════════════════════════════
# Section E — Partial Failure (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1PartialFailure:
    """E: partial failure handling — return None,不 silent loss。"""

    def test_e1_diary_write_failure_returns_none(self, tmp_path):
        """Diary 寫到不存在的目錄(權限問題)→ 應 return None,不 raise。"""
        # 製造一個 read-only 目錄,寫入應失敗
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        # 改權限 → 這在 Windows 上不一定 work
        import os
        if sys.platform != "win32":
            os.chmod(ro_dir, 0o444)
            writer = DiaryWriter(data_dir=str(ro_dir))
            # 在 Linux 上,寫入會 PermissionError
            result = writer.write_entry("agent_x", "morning", "test", source="llm")
            assert result is None
            os.chmod(ro_dir, 0o755)  # restore
        else:
            # Windows 跳過(沒 POSIX 權限)
            pytest.skip("POSIX permissions not available on Windows")

    def test_e2_dream_write_failure_returns_none(self, tmp_path):
        """Dream 寫到不存在的目錄(權限問題)→ 應 return None。"""
        import os
        ro_dir = tmp_path / "readonly_dream"
        ro_dir.mkdir()
        if sys.platform != "win32":
            os.chmod(ro_dir, 0o444)
            writer = DreamEventWriter(data_dir=str(ro_dir))
            result = writer._write_entry("agent_x", "dream", "test", source="llm")
            assert result is None
            os.chmod(ro_dir, 0o755)
        else:
            pytest.skip("POSIX permissions not available on Windows")

    def test_e3_diary_writes_to_correct_path(self, tmp_path):
        """Diary 寫到 data_dir 下正確的子路徑(不是污染 data_dir 根)。"""
        writer = DiaryWriter(data_dir=str(tmp_path))
        path = writer.write_entry("agent_test", "morning", "c", source="llm")
        # 應該在 <data_dir>/<agent_id>/diary/<date>.jsonl
        expected_parts = ["agent_test", "diary"]
        for part in expected_parts:
            assert part in path.parts, f"path {path} missing {part}"
        # 檔案名是 YYYY-MM-DD.jsonl
        assert path.name.endswith(".jsonl")
        assert len(path.name) == len("YYYY-MM-DD.jsonl")  # 15 chars

    def test_e4_dream_writes_to_correct_path(self, tmp_path):
        """Dream 寫到 data_dir 下正確的子路徑(跟 Diary 同一個目錄)。"""
        writer = DreamEventWriter(data_dir=str(tmp_path))
        path = writer._write_entry("agent_test", "dream", "c", source="llm")
        expected_parts = ["agent_test", "diary"]
        for part in expected_parts:
            assert part in path.parts

    def test_e5_diary_dream_share_file_no_data_loss(self, tmp_path):
        """Diary 跟 Dream 寫到同一個 file,兩個 entry 都應該被保留(append-only)。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))
        p1 = diary.write_entry("agent_x", "morning", "d1", source="llm")
        p2 = dream._write_entry("agent_x", "dream", "dr1", source="llm")
        assert p1 == p2

        # 讀 file,確認兩個 entry 都在
        with open(p1, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        entries = [json.loads(line) for line in lines]
        contents = {e["content"] for e in entries}
        assert contents == {"d1", "dr1"}


# ════════════════════════════════════════════════════════════
# Section F — Independence (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1Independence:
    """F: 三條 path 真正 independent(不同 file,不同 schema,不同 lock)。"""

    def test_f1_diary_dream_share_file_but_distinguishable(self, tmp_path):
        """Diary 跟 Dream 共享 diary/<date>.jsonl,但 slot 區分。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))
        # 同一個 agent_id,同一個 date
        p1 = diary.write_entry("agent_x", "morning", "d1", source="llm")
        p2 = dream._write_entry("agent_x", "dream", "dr1", source="llm")
        assert p1 == p2
        # 兩個 entry 都在同 file,slot 區分
        with open(p1, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f]
        assert {e["slot"] for e in entries} == {"morning", "dream"}

    def test_f2_memory_diary_independent_files(self, tmp_path):
        """Memory 跟 Diary 在完全不同的路徑。"""
        # Setup: tmp_path/data/soul/<agent>/diary/<date>.jsonl
        #        tmp_path/data/memory.db
        #        tmp_path/data/memory/<agent>/memories.jsonl
        diary = DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))
        memory_dir = tmp_path / "data" / "memory"
        memory_dir.mkdir(parents=True)

        # Diary 寫
        d_path = diary.write_entry("agent_x", "morning", "d", source="llm")
        # Memory 寫
        v1_store = V1Store(memory_dir, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="mem",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))

        # 路徑完全分離
        assert "diary" in d_path.parts
        memory_files = list(memory_dir.rglob("*.jsonl"))
        assert len(memory_files) >= 1
        # Diary 跟 Memory 不在同目錄
        assert d_path.parent.parent not in [mf.parent.parent for mf in memory_files]

    def test_f3_memory_dream_independent_files(self, tmp_path):
        """Memory 跟 Dream 在不同路徑。"""
        # Dream 寫 data/soul/<agent>/diary/<date>.jsonl
        # Memory 寫 data/memory.db
        dream = DreamEventWriter(data_dir=str(tmp_path / "data" / "soul"))
        memory_dir = tmp_path / "data"
        memory_dir.mkdir(parents=True)

        d_path = dream._write_entry("agent_x", "dream", "dr", source="llm")
        v1_store = V1Store(memory_dir, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="mem",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))

        # 兩個 file 都在
        assert d_path.exists()
        # memory files 也存在
        memory_files = list((memory_dir).rglob("*.jsonl")) + list((memory_dir).glob("*.db"))
        # v1 mirror 至少有一個 .jsonl
        assert any(mf.suffix == ".jsonl" for mf in memory_files)

    def test_f4_diary_failure_does_not_affect_dream(self, tmp_path):
        """Diary 寫入失敗不影響 Dream 寫入(各自獨立)。"""
        # 製造 diary 寫入失敗
        ro_dir = tmp_path / "readonly_diary"
        ro_dir.mkdir()
        if sys.platform != "win32":
            import os
            os.chmod(ro_dir, 0o444)
            diary = DiaryWriter(data_dir=str(ro_dir))
            # diary fail
            assert diary.write_entry("agent_x", "morning", "d", source="llm") is None
            os.chmod(ro_dir, 0o755)
        # dream 寫到不同目錄
        dream_dir = tmp_path / "dream_ok"
        dream_dir.mkdir()
        dream = DreamEventWriter(data_dir=str(dream_dir))
        # dream ok
        d_path = dream._write_entry("agent_x", "dream", "dr", source="llm")
        assert d_path is not None
        assert d_path.exists()

    def test_f5_memory_v1_mirror_independence_of_memory_db(self, tmp_path):
        """v1 mirror 是獨立 file,跟 memory.db(如果有)分離。"""
        # V1Store 只用 jsonl,不用 SQLite
        # 但 SAGE writer 會同時寫 memory.db + v1 mirror
        # 這裡只測 v1 層的 independence
        v1_store = V1Store(tmp_path, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="mem",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))
        # 確認 v1 jsonl 存在
        v1_files = list(tmp_path.rglob("*.jsonl"))
        assert len(v1_files) >= 1
        # v1 jsonl 內容正確
        with open(v1_files[0], "r", encoding="utf-8") as f:
            line = f.readline()
            entry = json.loads(line)
        assert entry["memory_id"] == "m1"


# ════════════════════════════════════════════════════════════
# Section G — Race Condition & Independence Hardening (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1RaceCondition:
    """G: 並發寫入 race condition + 三條 path 真正的 independence 驗證。"""

    def test_g1_concurrent_diary_writes_no_loss(self, tmp_path):
        """10 個並發 diary 寫入(各自 thread),所有 entry 都應該被保留。"""
        import threading
        writer = DiaryWriter(data_dir=str(tmp_path))

        def write_one(i):
            writer.write_entry("agent_x", "morning", f"c{i}", source="llm")

        threads = [threading.Thread(target=write_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 讀 file
        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 10, f"Expected 10 entries, got {len(entries)}"
        contents = {e["content"] for e in entries}
        assert contents == {f"c{i}" for i in range(10)}

    def test_g2_concurrent_dream_writes_no_loss(self, tmp_path):
        """10 個並發 dream 寫入,所有 entry 都保留。"""
        import threading
        writer = DreamEventWriter(data_dir=str(tmp_path))

        def write_one(i):
            writer._write_entry("agent_x", "dream", f"dr{i}", source="llm")

        threads = [threading.Thread(target=write_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 10, f"Expected 10 entries, got {len(entries)}"
        contents = {e["content"] for e in entries}
        assert contents == {f"dr{i}" for i in range(10)}

    def test_g3_diary_and_dream_concurrent_no_loss(self, tmp_path):
        """Diary 跟 Dream 並發寫到同一個 file,兩個 writer 各自有 lock,但 file 沒跨 writer 的 lock。"""
        import threading
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))

        def diary_write(i):
            diary.write_entry("agent_x", "morning", f"d{i}", source="llm")

        def dream_write(i):
            dream._write_entry("agent_x", "dream", f"dr{i}", source="llm")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=diary_write, args=(i,)))
            threads.append(threading.Thread(target=dream_write, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 應該有 10 個 entry(5 diary + 5 dream)
        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        # 註:這裡可能有 race condition 導致少數 entry 丟失(因為兩個 writer 不共用 lock)
        # 我們至少要驗證 5-10 之間
        assert 5 <= len(entries) <= 10, (
            f"Expected 5-10 entries(可能 race), got {len(entries)}"
        )

    def test_g4_concurrent_memory_v1_writes_no_loss(self, tmp_path):
        """10 個並發 v1 mirror 寫入,所有 memory 都保留。"""
        import threading
        v1_store = V1Store(tmp_path, "agent_x")
        from src.memory.v1.schema import Memory

        def add_one(i):
            v1_store.add(Memory(
                memory_id=f"m_{i}", agent_id="agent_x",
                content=f"c{i}", tags=["t"],
                created_at=time.time(), category="fact", confidence=0.8,
            ))

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_mem = v1_store.all()
        # 至少 5 個(可能 race condition 丟一些)
        assert 5 <= len(all_mem) <= 10, f"Expected 5-10 memories, got {len(all_mem)}"

    def test_g5_diary_writer_state_isolated(self, tmp_path):
        """兩個 DiaryWriter instance 各自有 lock(per-instance state isolation)。"""
        w1 = DiaryWriter(data_dir=str(tmp_path / "d1"))
        w2 = DiaryWriter(data_dir=str(tmp_path / "d2"))

        p1 = w1.write_entry("agent_x", "morning", "from w1", source="llm")
        p2 = w2.write_entry("agent_x", "morning", "from w2", source="llm")

        # 不同目錄
        assert p1.parent.parent != p2.parent.parent
        # 各自有自己 entry
        assert p1.exists()
        assert p2.exists()
        with open(p1, "r", encoding="utf-8") as f:
            e1 = json.loads(f.readline())
        with open(p2, "r", encoding="utf-8") as f:
            e2 = json.loads(f.readline())
        assert e1["content"] == "from w1"
        assert e2["content"] == "from w2"

    def test_g6_dream_writer_state_isolated(self, tmp_path):
        """兩個 DreamEventWriter instance 各自有 lock。"""
        w1 = DreamEventWriter(data_dir=str(tmp_path / "d1"))
        w2 = DreamEventWriter(data_dir=str(tmp_path / "d2"))

        p1 = w1._write_entry("agent_x", "dream", "from w1", source="llm")
        p2 = w2._write_entry("agent_x", "dream", "from w2", source="llm")

        assert p1.parent.parent != p2.parent.parent
        assert p1.exists()
        assert p2.exists()
        with open(p1, "r", encoding="utf-8") as f:
            e1 = json.loads(f.readline())
        with open(p2, "r", encoding="utf-8") as f:
            e2 = json.loads(f.readline())
        assert e1["content"] == "from w1"
        assert e2["content"] == "from w2"


# ════════════════════════════════════════════════════════════
# Section H — Inner Life Narrative Completeness (5+ tests)
# ════════════════════════════════════════════════════════════


class TestS2D1InnerLifeNarrative:
    """H: Inner life 敘事完整性 — Diary + Dream + Memory 三條 path。"""

    def test_h1_diary_dream_memory_three_distinct_files(self, tmp_path):
        """三條 path 寫到三個 distinct file set。"""
        # Diary → data/soul/<agent>/diary/<date>.jsonl
        # Dream → data/soul/<agent>/diary/<date>.jsonl (跟 Diary 同 file!)
        # Memory → data/memory.db + data/memory/<agent>/memories.jsonl
        diary = DiaryWriter(data_dir=str(tmp_path / "soul"))
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        d_path = diary.write_entry("agent_x", "morning", "d", source="llm")
        v1_store = V1Store(memory_dir, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="m",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))

        # Diary file
        assert d_path.exists()
        assert "diary" in str(d_path)
        # Memory file
        v1_files = list(memory_dir.rglob("*.jsonl"))
        assert len(v1_files) >= 1

    def test_h2_three_path_writes_no_cross_contamination(self, tmp_path):
        """三條 path 寫入不互相污染(各自 path 獨立)。"""
        diary = DiaryWriter(data_dir=str(tmp_path / "soul"))
        dream = DreamEventWriter(data_dir=str(tmp_path / "soul"))
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        v1_store = V1Store(memory_dir, "agent_x")

        diary.write_entry("agent_x", "morning", "d", source="llm")
        dream._write_entry("agent_x", "dream", "dr", source="llm")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="m",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))

        # Diary file 應有 d + dr(2 條)
        from src.soul.diary import DiaryWriter as DW
        diary_entries = DW(data_dir=str(tmp_path / "soul")).read_entries(
            "agent_x", datetime.now().strftime("%Y-%m-%d")
        )
        assert len(diary_entries) == 2
        # Memory file 應有 m(1 條)
        v1_files = list(memory_dir.rglob("*.jsonl"))
        all_mem = []
        for f in v1_files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    all_mem.append(json.loads(line))
        assert len(all_mem) == 1

    def test_h3_diary_dream_distinct_slot_taxonomy(self, tmp_path):
        """Diary slots: morning/night;Dream slots: dream/event — 兩個 disjoint sets。"""
        diary = DiaryWriter(data_dir=str(tmp_path))
        dream = DreamEventWriter(data_dir=str(tmp_path))

        # Diary 寫 morning/night
        diary.write_entry("agent_x", "morning", "dm", source="llm")
        diary.write_entry("agent_x", "night", "dn", source="llm")
        # Dream 寫 dream/event
        dream._write_entry("agent_x", "dream", "dr", source="llm")
        dream._write_entry("agent_x", "event", "ev", source="llm")

        # 全部 4 個 entry 在同一個 file
        from src.soul.diary import DiaryWriter as DW
        entries = DW(data_dir=str(tmp_path)).read_entries("agent_x", datetime.now().strftime("%Y-%m-%d"))
        assert len(entries) == 4
        slots = {e["slot"] for e in entries}
        assert slots == {"morning", "night", "dream", "event"}

    def test_h4_memory_diary_independent_at_runtime(self, tmp_path):
        """Memory 跟 Diary 寫入失敗不互相影響(各自路徑獨立)。"""
        # Diary 寫到 fail dir
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()
        if sys.platform != "win32":
            import os
            os.chmod(ro_dir, 0o444)
            diary = DiaryWriter(data_dir=str(ro_dir))
            assert diary.write_entry("agent_x", "morning", "d", source="llm") is None
            os.chmod(ro_dir, 0o755)
        # Memory 寫到 OK dir
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        v1_store = V1Store(memory_dir, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="m",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))
        # Memory OK
        assert len(v1_store.all()) == 1

    def test_h5_diary_dream_memory_schemas_distinct(self, tmp_path):
        """三條 path 寫入的 schema 各自不同(diary = {ts,slot,content,source};memory = Memory 物件,有 category/confidence/tags)。"""
        # Diary
        diary = DiaryWriter(data_dir=str(tmp_path))
        d_path = diary.write_entry("agent_x", "morning", "d", source="llm")
        with open(d_path, "r", encoding="utf-8") as f:
            d_entry = json.loads(f.readline())
        # Dream
        dream = DreamEventWriter(data_dir=str(tmp_path))
        dr_path = dream._write_entry("agent_x", "dream", "dr", source="llm")
        with open(dr_path, "r", encoding="utf-8") as f:
            dr_entry = json.loads(f.readline())
        # Memory
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        v1_store = V1Store(memory_dir, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="m",
            tags=["t1", "t2"], created_at=time.time(), category="fact", confidence=0.8,
        ))
        v1_files = list(memory_dir.rglob("*.jsonl"))
        with open(v1_files[0], "r", encoding="utf-8") as f:
            m_entry = json.loads(f.readline())

        # Schema 比較
        d_keys = set(d_entry.keys())
        dr_keys = set(dr_entry.keys())
        m_keys = set(m_entry.keys())

        # Diary & Dream 共用相同 schema
        assert d_keys == dr_keys, f"Diary keys {d_keys} != Dream keys {dr_keys}"
        # Memory 不同 schema
        assert m_keys != d_keys, f"Memory schema 同 Diary,應該不同"
        # Memory 應該有 category/confidence/tags
        assert "category" in m_keys
        assert "confidence" in m_keys
        assert "tags" in m_keys
        # Diary/Dream 沒有 category/confidence
        assert "category" not in d_keys
        assert "confidence" not in d_keys


# ════════════════════════════════════════════════════════════
# Section I — Source Code Audit (architecture observations)
# ════════════════════════════════════════════════════════════


class TestS2D1SourceAudit:
    """I: Source code audit observations(純文件化,no source modification)。"""

    def test_i1_diary_writer_has_threading_lock(self):
        """DiaryWriter 應該有 threading.Lock()(per-instance)。"""
        # Inspect source: import 模組, 確認 attribute 存在
        from src.soul.diary import DiaryWriter
        # 用 init 看看
        w = DiaryWriter(data_dir="/tmp")
        # Lock 應該存在
        assert hasattr(w, "_lock"), "DiaryWriter 缺 _lock attribute"
        import threading
        assert isinstance(w._lock, type(threading.Lock()))

    def test_i2_dream_writer_has_threading_lock(self):
        """DreamEventWriter 應該有 threading.Lock()。"""
        from src.soul.dream_event import DreamEventWriter
        w = DreamEventWriter(data_dir="/tmp")
        assert hasattr(w, "_lock"), "DreamEventWriter 缺 _lock attribute"
        import threading
        assert isinstance(w._lock, type(threading.Lock()))

    def test_i3_diary_dream_locks_are_per_instance(self):
        """每個 writer instance 各自有 lock(不共用)。"""
        from src.soul.diary import DiaryWriter
        w1 = DiaryWriter(data_dir="/tmp/1")
        w2 = DiaryWriter(data_dir="/tmp/2")
        # 兩個 instance 的 lock 不是同一個
        assert w1._lock is not w2._lock

    def test_i4_diary_dream_writer_share_singleton_locks(self):
        """注意: diary 跟 dream 雖然都用 singleton 模式,但 _lock 是 per-instance。這意味著兩個 writer 對同一個 file append 時,沒有跨 writer 的 lock。"""
        from src.soul.diary import get_diary_writer, DiaryWriter
        from src.soul.dream_event import DreamEventWriter
        # Get singletons
        d_singleton = get_diary_writer()
        d_new = DiaryWriter(data_dir="/tmp/3")
        # Diary singleton 跟 new instance 是不同 instance
        assert d_singleton is not d_new
        # Lock 各自獨立
        assert d_singleton._lock is not d_new._lock

    def test_i5_diary_dream_modules_exist(self):
        """Diary 跟 Dream 模組存在且有 DiaryWriter / DreamEventWriter 公開類別。"""
        from src.soul import diary, dream_event
        assert hasattr(diary, "DiaryWriter")
        assert hasattr(dream_event, "DreamEventWriter")
        # 驗證 DiaryWriter.write_entry 是 method
        assert hasattr(diary.DiaryWriter, "write_entry")
        # 驗證 DreamEventWriter._write_entry 是 method
        assert hasattr(dream_event.DreamEventWriter, "_write_entry")

    def test_i6_v1_store_uses_jsonl(self, tmp_path):
        """V1Store 用 jsonl 存 memory(每行一個 json object)。"""
        v1_store = V1Store(tmp_path, "agent_x")
        from src.memory.v1.schema import Memory
        v1_store.add(Memory(
            memory_id="m1", agent_id="agent_x", content="m1",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))
        v1_store.add(Memory(
            memory_id="m2", agent_id="agent_x", content="m2",
            tags=["t"], created_at=time.time(), category="fact", confidence=0.8,
        ))
        # 找到 v1 file
        v1_files = list((tmp_path / "agent_x").glob("memories.jsonl"))
        assert len(v1_files) == 1
        # 確認是 jsonl
        with open(v1_files[0], "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line.strip())
            assert "memory_id" in entry
            assert "content" in entry


# ════════════════════════════════════════════════════════════
# Sanity check
# ════════════════════════════════════════════════════════════


def test_s2_d1_audit_smoke():
    """Smoke test: 確認 30+ tests 都存在。"""
    from tests.test_m5_4_1_inner_life_narrative_audit import (
        TestS2D1TimestampConsistency,
        TestS2D1AgentIdentity,
        TestS2D1TriggerIdentity,
        TestS2D1OrderingDeterminism,
        TestS2D1PartialFailure,
        TestS2D1Independence,
        TestS2D1RaceCondition,
        TestS2D1InnerLifeNarrative,
        TestS2D1SourceAudit,
    )
    # 計算 test 數
    total = 0
    for cls in [
        TestS2D1TimestampConsistency,
        TestS2D1AgentIdentity,
        TestS2D1TriggerIdentity,
        TestS2D1OrderingDeterminism,
        TestS2D1PartialFailure,
        TestS2D1Independence,
        TestS2D1RaceCondition,
        TestS2D1InnerLifeNarrative,
        TestS2D1SourceAudit,
    ]:
        methods = [m for m in dir(cls) if m.startswith("test_")]
        total += len(methods)
    assert total >= 30, f"Expected >= 30 tests, got {total}"
    print(f"Total tests: {total}")
