"""
test_m7_1_activity_model.py
M7-1 (Bry 拍板 2026-08-18): 活動模型化

驗證:
  A. ACTIVITY_POOL 結構正確 (10 items, 每個有 name/category/shareable, 有 shareable 與非 shareable 混合)
  B. write_event 帶 mock LLM → entry 帶 activity/category/shareable (source=llm)
  C. write_event LLM 失敗 (placeholder) → 不帶 activity metadata
  D. write_event 的 prompt 已從「事件類型/場景」改成「活動」 (決策 #1 輕觸落地)
"""
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from src.paths import data_root, reset_data_root
from src.soul.dream_event import ACTIVITY_POOL, DreamEventWriter


def _isolated_writer(tmp_path: Path) -> DreamEventWriter:
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return DreamEventWriter(data_dir=str(soul_dir))


def _restore_data_root():
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _read_jsonl(tmp_path: Path, agent_id: str) -> list:
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
# A. ACTIVITY_POOL 結構
# ───────────────────────────────────────────────────────────

class TestActivityPool:
    def test_a1_activity_pool_has_ten_items(self):
        assert len(ACTIVITY_POOL) == 10

    def test_a2_each_activity_has_required_keys(self):
        for a in ACTIVITY_POOL:
            assert set(a.keys()) == {"name", "category", "shareable"}
            assert isinstance(a["name"], str) and a["name"]
            assert isinstance(a["category"], str) and a["category"]
            assert isinstance(a["shareable"], bool)

    def test_a3_mix_of_shareable_and_not(self):
        shareable = [a for a in ACTIVITY_POOL if a["shareable"]]
        not_shareable = [a for a in ACTIVITY_POOL if not a["shareable"]]
        assert len(shareable) > 0
        assert len(not_shareable) > 0


# ───────────────────────────────────────────────────────────
# B. write_event 活動 metadata 落地
# ───────────────────────────────────────────────────────────

class TestWriteEventActivity:
    def test_b1_write_event_llm_entry_has_activity_metadata(self, tmp_path, monkeypatch):
        """write_event (source=llm) 的 entry 帶 activity/category/shareable。"""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_call(*args, **kwargs):
                return "今日は穏やかに仕事を進めた。"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_call)

            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(writer.write_event("agent_yua"))

            entries = _read_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            e = entries[0]
            assert e["slot"] == "event"
            assert e["source"] == "llm"
            assert "activity" in e and e["activity"]
            assert "category" in e and e["category"]
            assert "shareable" in e and isinstance(e["shareable"], bool)
            # activity name 必須來自 ACTIVITY_POOL
            names = {a["name"] for a in ACTIVITY_POOL}
            assert e["activity"] in names
        finally:
            _restore_data_root()

    def test_b2_write_event_placeholder_no_activity_metadata(self, tmp_path, monkeypatch):
        """LLM 失敗 (placeholder) 不帶 activity metadata (只有 llm path 帶)。"""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            async def fake_llm_fail(*args, **kwargs):
                return None

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_fail)

            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(writer.write_event("agent_yua"))

            entries = _read_jsonl(tmp_path, "agent_yua")
            assert len(entries) == 1
            e = entries[0]
            assert e["slot"] == "event"
            assert e["source"] == "placeholder"
            assert "activity" not in e
        finally:
            _restore_data_root()

    def test_b3_write_event_prompt_uses_activity_not_event_type(self, tmp_path, monkeypatch):
        """prompt 已從「事件類型/場景」改成「活動」(決策 #1 輕觸)。"""
        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            from src.soul import dream_event as dream_mod

            captured = {}

            async def fake_llm_capture(system, user, *args, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return "test content"

            monkeypatch.setattr(dream_mod, "_call_minimax_for_dream_event", fake_llm_capture)

            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(writer.write_event("agent_yua"))

            assert "system" in captured and "user" in captured
            # 活動名稱有進 user prompt (LLM 知道要寫哪個活動)
            assert "活動" in captured["user"]
            assert "活動內容" in captured["user"]
            # 舊的「場景 / 事件類型」已移除
            assert "場景" not in captured["user"]
            assert "事件類型" not in captured["user"]
            # 決策 #1 輕觸: system 移除「Bry 不在」排除語, 改成「現在在做: <activity>」
            assert "Bry 不在" not in captured["system"]
            assert "現在在做" in captured["system"]
        finally:
            _restore_data_root()
