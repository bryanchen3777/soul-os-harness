"""
tests/social/test_sg3_impression_tags.py — SG-3 §7.2 W1 impression_tags 接線 (NEW)

覆盖（工单 SG-3-IMPL E 项）:
  1. W1 接線: `write_dream` 的 `update_impression` 呼叫端**實際上傳**
     `impression_tags`（由同一次 LLM 調用的回傳文本確定性抽取, 0 新增 LLM 調用）
  2. W1 target 白名單: **排除 `user_bryan`**（SG-3 §7.2）
  3. **INV-9 Privacy Gate（消費端對稱, fail-closed）**:
     `is_private=True` / `source_mode=="private"` / TG 1:1 channel → 0 tag 產生
  4. §7.4 規範: ≤5 項 / ≤12 字符/項 / 去重保留寫入序 / 空值不覆寫 /
     非 str 丟棄 / 0 新增 schema 欄位（OQ-6: 不加獨立時間戳）
  5. 0 新增 LLM 調用 / 0 schema 欄位 / 0 新時間戳欄位

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_sg3_impression_tags.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from unittest.mock import patch

from src.paths import reset_data_root
from src.soul import dream_event as dream_mod
from src.soul.dream_event import (
    _MAX_IMPRESSION_TAG_CHARS,
    _MAX_IMPRESSION_TAGS,
    DreamEventWriter,
    _extract_impression_tags,
    _impression_tags_allowed,
    _is_w1_tag_target,
    _normalize_impression_tags,
)
from src.soul.motive import set_agent_ids

AGENT = "agent_yua"
TARGET = "agent_rem"
BRYAN = "user_bryan"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)
    set_agent_ids([AGENT, TARGET])
    yield tmp_path
    set_agent_ids([])
    reset_data_root()


def _writer(tmp_path: Path) -> DreamEventWriter:
    return DreamEventWriter(data_dir=str(tmp_path / "data" / "soul"), api_key="test-key")


def _rel_entry(tmp_path: Path, other: str) -> dict:
    path = tmp_path / "data" / "soul" / AGENT / "relationships.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["others"][other]


def _run_dream(tmp_path: Path, *, target: str = TARGET, impression: str = "温柔、可靠",
               dream_text: str = "夢の内容", **kwargs):
    """跑 write_dream（LLM 以確定性 stub 取代; 第 2 次調用回 impression）。"""
    calls = []

    async def _fake_llm(system, user, api_key=None, **kw):
        calls.append({"system": system, "user": user})
        # 第 1 次 = 夢境本文; 第 2 次 = impression 抽取
        return dream_text if len(calls) == 1 else impression

    with patch.object(dream_mod, "_call_llm_for_dream_event", _fake_llm):
        writer = _writer(tmp_path)
        asyncio.run(writer.write_dream(AGENT, target, [AGENT, target], **kwargs))
    return calls


# ───────────────────────────────────────────────────────────
# 1. W1 接線 + 0 新增 LLM 調用
# ───────────────────────────────────────────────────────────

class TestW1Wiring:
    def test_write_dream_uploads_impression_tags(self, iso_env):
        tmp = iso_env
        calls = _run_dream(tmp, impression="温柔、可靠、安静")
        entry = _rel_entry(tmp, TARGET)
        assert entry["impression"] == "温柔、可靠、安静"
        assert entry["impression_tags"] == ["温柔", "可靠", "安静"]

    def test_no_extra_llm_call(self, iso_env):
        """0 新增 LLM 調用: 夢境 1 次 + impression 1 次 = 2 次（與改動前一致）"""
        tmp = iso_env
        calls = _run_dream(tmp)
        assert len(calls) == 2

    def test_single_phrase_impression_yields_single_tag(self, iso_env):
        tmp = iso_env
        _run_dream(tmp, impression="優しい人")
        assert _rel_entry(tmp, TARGET)["impression_tags"] == ["優しい人"]

    def test_empty_impression_no_tags_and_no_overwrite(self, iso_env):
        """空 impression → 不寫 impression → tags 亦不寫（0 假資料）"""
        tmp = iso_env
        _run_dream(tmp, impression="")
        entry = _rel_entry(tmp, TARGET)
        assert entry["impression"] == ""
        assert entry.get("impression_tags", []) == []

    def test_no_schema_change(self, iso_env):
        """OQ-6: 不加 impression_tags 獨立時間戳欄位（schema 維持 4.2）"""
        tmp = iso_env
        _run_dream(tmp)
        entry = _rel_entry(tmp, TARGET)
        assert "impression_tags_updated_at" not in entry
        assert set(entry.keys()) == {
            "impression", "feeling", "confidence", "interaction_count",
            "last_interaction_at", "last_updated", "created_at", "objective",
            "impression_tags", "relational_band", "band_updated_at",
            "last_relation_update_ref",
        }
        raw = json.loads(
            (tmp / "data" / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8")
        )
        assert raw["schema_version"] == "4.2"


# ───────────────────────────────────────────────────────────
# 2. W1 target 白名單（排除 user_bryan）
# ───────────────────────────────────────────────────────────

class TestW1TargetWhitelist:
    def test_user_bryan_excluded(self):
        assert _is_w1_tag_target(BRYAN) is False
        assert _is_w1_tag_target("bryan") is False

    def test_registered_peer_allowed(self):
        assert _is_w1_tag_target(TARGET) is True

    def test_unregistered_target_rejected_when_registry_present(self):
        set_agent_ids([AGENT, TARGET])
        try:
            assert _is_w1_tag_target("agent_ghost") is False
        finally:
            set_agent_ids([])

    def test_registry_empty_allows_non_bryan(self):
        """註冊表為空（無 roll call 可比對）→ 只套明文紅線（排 Bryan）, 不阻斷 W1。"""
        set_agent_ids([])
        assert _is_w1_tag_target(TARGET) is True

    def test_empty_target_rejected(self):
        assert _is_w1_tag_target("") is False
        assert _is_w1_tag_target(None) is False  # type: ignore[arg-type]

    def test_dream_of_bryan_writes_no_tags(self, iso_env):
        """即使對 user_bryan 做夢（理論上 _pick_dream_target 不會選）→ 0 tag。"""
        tmp = iso_env
        set_agent_ids([AGENT, TARGET, BRYAN])
        try:
            _run_dream(tmp, target=BRYAN, impression="温柔、可靠")
            entry = _rel_entry(tmp, BRYAN)
            assert entry["impression"] == "温柔、可靠"  # 既有 impression 行為不變
            assert entry.get("impression_tags", []) == []  # W1 排除: 0 tag
        finally:
            set_agent_ids([AGENT, TARGET])


# ───────────────────────────────────────────────────────────
# 3. INV-9 Privacy Gate（fail-closed, 消費端對稱）
# ───────────────────────────────────────────────────────────

class TestINV9PrivacyGate:
    def test_gate_unit_private_flags(self):
        assert _impression_tags_allowed(is_private=True) is False
        assert _impression_tags_allowed(source_mode="private") is False
        assert _impression_tags_allowed(source_mode="PRIVATE") is False
        assert _impression_tags_allowed(source_mode="dm") is False
        assert _impression_tags_allowed(channel="tg_dm") is False
        assert _impression_tags_allowed(channel="tg_1:1") is False
        # 公開 / 自身內在活動（W1 夢境）→ 允許
        assert _impression_tags_allowed() is True
        assert _impression_tags_allowed(source_mode="public", channel="lounge") is True

    def test_is_private_source_produces_zero_tags(self, iso_env):
        tmp = iso_env
        _run_dream(tmp, impression="温柔、可靠", is_private=True)
        assert _rel_entry(tmp, TARGET).get("impression_tags", []) == []

    def test_private_mode_produces_zero_tags(self, iso_env):
        tmp = iso_env
        _run_dream(tmp, impression="温柔、可靠", source_mode="private")
        assert _rel_entry(tmp, TARGET).get("impression_tags", []) == []

    def test_tg_dm_channel_produces_zero_tags(self, iso_env):
        tmp = iso_env
        _run_dream(tmp, impression="温柔、可靠", channel="tg_dm")
        assert _rel_entry(tmp, TARGET).get("impression_tags", []) == []

    def test_private_source_does_not_overwrite_existing_tags(self, iso_env):
        """INV-9 fail-closed + 空值不覆寫: 私聊來源不得覆寫既有 tags。"""
        tmp = iso_env
        set_agent_ids([AGENT, TARGET])
        writer = _writer(tmp)
        writer_path = tmp / "data" / "soul" / AGENT / "relationships.json"
        writer_path.parent.mkdir(parents=True, exist_ok=True)
        writer_path.write_text(json.dumps({
            "agent_id": AGENT, "schema_version": "4.2",
            "others": {TARGET: {
                "impression": "", "feeling": "neutral", "confidence": 0.0,
                "interaction_count": 0, "last_interaction_at": None,
                "last_updated": "2026-09-01T00:00:00+00:00",
                "created_at": "2026-09-01T00:00:00+00:00",
                "objective": {"reply_exchanges": 0, "co_presence_sessions": 0,
                              "dream_exchanges": 0, "last_signal_at": None},
                "impression_tags": ["既有標籤"], "relational_band": "known",
                "band_updated_at": None, "last_relation_update_ref": None,
            }},
        }, ensure_ascii=False), encoding="utf-8")
        _run_dream(tmp, impression="温柔、可靠", is_private=True)
        assert _rel_entry(tmp, TARGET)["impression_tags"] == ["既有標籤"]

    def test_public_source_still_writes_tags(self, iso_env):
        """對照組: 非私聊來源仍正常寫 tags（gate 不誤殺 W1 主路徑）。"""
        tmp = iso_env
        _run_dream(tmp, impression="温柔、可靠", source_mode="public", channel="lounge")
        assert _rel_entry(tmp, TARGET)["impression_tags"] == ["温柔", "可靠"]

    def test_static_inv9_marker_in_source(self):
        """INV-9 紅線可追溯: 源碼明示 INV-9 + fail-closed 語義。"""
        src = (ROOT / "src" / "soul" / "dream_event.py").read_text(encoding="utf-8")
        assert "INV-9" in src
        assert "fail-closed" in src


# ───────────────────────────────────────────────────────────
# 4. §7.4 規範（純函數: ≤5 / ≤12 / 去重保序 / 空值 / 非 str）
# ───────────────────────────────────────────────────────────

class TestTagSpec:
    def test_cap_five_items(self):
        raw = ["a", "b", "c", "d", "e", "f", "g"]
        assert _normalize_impression_tags(raw) == ["a", "b", "c", "d", "e"]
        assert _MAX_IMPRESSION_TAGS == 5

    def test_cap_twelve_chars_per_item(self):
        long_tag = "12345678901234567890"
        out = _normalize_impression_tags([long_tag])
        assert out == ["123456789012"]
        assert len(out[0]) == _MAX_IMPRESSION_TAG_CHARS == 12

    def test_dedupe_preserves_first_occurrence_order(self):
        raw = ["b", "a", "b", "c", "a", "d"]
        assert _normalize_impression_tags(raw) == ["b", "a", "c", "d"]

    def test_empty_and_whitespace_dropped(self):
        assert _normalize_impression_tags(["", "  ", "\n", "ok"]) == ["ok"]
        assert _normalize_impression_tags(["", "   "]) == []

    def test_non_string_dropped(self):
        assert _normalize_impression_tags(["ok", 123, None, ["x"], {"y": 1}]) == ["ok"]

    def test_non_list_returns_empty(self):
        assert _normalize_impression_tags(None) == []
        assert _normalize_impression_tags("温柔") == []
        assert _normalize_impression_tags(123) == []

    def test_quotes_and_punctuation_stripped(self):
        assert _normalize_impression_tags(["「温柔」", "可靠。"]) == ["温柔", "可靠"]

    def test_extract_from_delimited_text(self):
        assert _extract_impression_tags("温柔、可靠/安静 沉穩") == ["温柔", "可靠", "安静", "沉穩"]
        assert _extract_impression_tags("温柔;可靠;安静") == ["温柔", "可靠", "安静"]

    def test_extract_handles_empty(self):
        assert _extract_impression_tags("") == []
        assert _extract_impression_tags(None) == []
        assert _extract_impression_tags("   ") == []

    def test_extract_no_delimiter_single_tag(self):
        # 月明かりに響く懐かしい足音 = 13 字符 → 截到 12
        assert _extract_impression_tags("月明かりに響く懐かしい足音") == ["月明かりに響く懐かしい足"]

    def test_open_set_no_case_folding(self):
        """open set: 不做大小寫轉換 / 詞形還原（No-Scoring 精神）"""
        assert _normalize_impression_tags(["Warm", "warm"]) == ["Warm", "warm"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
