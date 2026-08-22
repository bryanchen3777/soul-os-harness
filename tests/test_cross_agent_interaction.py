"""
tests/test_cross_agent_interaction.py
Cross-Agent Interaction (2026-08-22) — Layer 1/2/3 + board

工單驗收:
  Layer 1: prompt 含 top 3 cross-agent confidence band
  Layer 2: shared_event 觸發後, 兩隻的 diary 都有 event entry, interactions.jsonl 有記錄
  Layer 3: cross_chat 觸發後, 3 輪對話記錄到 interactions.jsonl
           **測試證明不 publish AGENT_INTENT/AGENCY_TRIGGER (迴圈防護)**
  限頻:   cross_chat / shared_event 有冷卻, 不會連續觸發
  board:  /api/soul/interactions 回傳正確 JSON (新到舊)

不測:
  - 真實 LLM call (一律 mock)
  - 不碰 frozen contract (Agency 4-stage / TriggerEnvelope / 4 handlers)
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.paths import data_root, reset_data_root
from src.soul.dream_event import (
    ACTIVITY_POOL,
    CHAT_FALLBACK_TURN1,
    CHAT_FALLBACK_TURN2,
    CHAT_FALLBACK_TURN3,
    DreamEventWriter,
)
from src.soul.scheduler import SoulScheduler

# ───────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────


def _isolate(tmp_path: Path) -> Path:
    """SOUL_OS_DATA_DIR → tmp_path/data, 回傳 soul dir。"""
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return soul_dir


def _restore():
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _read_interactions(tmp_path: Path) -> list:
    path = tmp_path / "data" / "soul" / "interactions.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _read_diary(tmp_path: Path, agent_id: str) -> list:
    today = datetime.now().strftime("%Y-%m-%d")
    path = tmp_path / "data" / "soul" / agent_id / "diary" / f"{today}.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _make_writer(tmp_path: Path) -> DreamEventWriter:
    return DreamEventWriter(data_dir=str(tmp_path / "data" / "soul"))


def _make_scheduler(tmp_path: Path, bus=None) -> SoulScheduler:
    sched = SoulScheduler(bus=bus)
    for aid in ("agent_yua", "agent_ruka", "agent_akane"):
        sched.register(aid)
    return sched


async def _fake_llm_ok(system, user, api_key, **kwargs):
    return "今天和對方一起散步了。"


async def _fake_llm_none(system, user, api_key, **kwargs):
    return None


# ───────────────────────────────────────────────────────────
# Layer 1: cross-agent awareness (top 3 confidence band)
# ───────────────────────────────────────────────────────────


def _make_mgr(others: dict, bry_conf: float = 0.85):
    mock_mgr = MagicMock()
    mock_store = MagicMock()
    mock_store.get.return_value = {
        "impression": "", "feeling": "neutral", "confidence": bry_conf,
        "interaction_count": 0, "last_interaction_at": None,
        "last_updated": "2026-08-22", "created_at": "2026-01-01",
    }
    mock_store.get_all.return_value = others
    mock_mgr.get_store.return_value = mock_store
    return mock_mgr


def _format_block(agent_id: str, mock_mgr) -> str:
    from src.llm.proxy import _format_relationship_block
    with patch(
        "src.soul.relationships.get_relationships_manager",
        return_value=mock_mgr,
    ):
        return _format_relationship_block(agent_id)


def _others():
    base = {
        "impression": "", "feeling": "neutral", "interaction_count": 0,
        "last_interaction_at": None, "last_updated": "2026-08-22",
        "created_at": "2026-01-01",
    }
    return {
        "user_bryan": {**base, "confidence": 0.99},   # 排除 (Bry)
        "agent_yua": {**base, "confidence": 0.9},
        "agent_ruka": {**base, "confidence": 0.7},
        "agent_akane": {**base, "confidence": 0.5},
        "agent_mai": {**base, "confidence": 0.2},      # 第 4 高, 不該出現
    }


class TestLayer1Top3:
    def test_l1_injects_top3_excludes_bryan_and_4th(self):
        block = _format_block("agent_yua", _make_mgr(_others()))
        # top 3 by confidence: yua(0.9), ruka(0.7), akane(0.5)
        assert "你跟 agent_yua 的關係" in block
        assert "你跟 agent_ruka 的關係" in block
        assert "你跟 agent_akane 的關係" in block
        # 第 4 高 (mai 0.2) 不在 top3
        assert "agent_mai" not in block
        # user_bryan 不會以「你跟 user_bryan」出現
        assert "你跟 user_bryan" not in block
        # Bry block 仍在
        assert "[你跟 Bry 的關係]" in block

    def test_l1_band_labels_known_stranger(self):
        base = {
            "impression": "", "feeling": "neutral", "interaction_count": 0,
            "last_interaction_at": None, "last_updated": "2026-08-22",
            "created_at": "2026-01-01",
        }
        others = {
            "user_bryan": {**base, "confidence": 0.99},
            "agent_ruka": {**base, "confidence": 0.6},   # >= 0.3 → 認識
            "agent_mai": {**base, "confidence": 0.2},    # < 0.3 → 陌生人
        }
        block = _format_block("agent_yua", _make_mgr(others))
        assert "你跟 agent_ruka 的關係：認識" in block
        assert "你跟 agent_mai 的關係：陌生人" in block

    def test_l1_only_bry_no_others_section(self):
        others = {
            "user_bryan": {
                "impression": "", "feeling": "neutral", "confidence": 0.85,
                "interaction_count": 0, "last_interaction_at": None,
                "last_updated": "2026-08-22", "created_at": "2026-01-01",
            }
        }
        block = _format_block("agent_yua", _make_mgr(others))
        assert block == "[你跟 Bry 的關係]\n  熟悉度: 親密"
        assert "你跟其他角色的關係" not in block

    def test_l1_malformed_others_skipped_fail_silent(self):
        base = {
            "impression": "", "feeling": "neutral", "interaction_count": 0,
            "last_interaction_at": None, "last_updated": "2026-08-22",
            "created_at": "2026-01-01",
        }
        others = {
            "user_bryan": {**base, "confidence": 0.85},
            "agent_bad_type": "not a dict",                        # 非 dict → skip
            "agent_bad_conf": {**base, "confidence": "high"},      # 非 number → skip
            "agent_ok": {**base, "confidence": 0.4},
        }
        block = _format_block("agent_yua", _make_mgr(others))
        assert "你跟 agent_ok 的關係" in block
        assert "agent_bad_type" not in block
        assert "agent_bad_conf" not in block
        assert "你跟 Bry 的關係" in block

    def test_l1_get_all_not_dict_fail_silent(self):
        mock_mgr = MagicMock()
        mock_store = MagicMock()
        mock_store.get.return_value = {
            "impression": "", "feeling": "neutral", "confidence": 0.85,
            "interaction_count": 0, "last_interaction_at": None,
            "last_updated": "2026-08-22", "created_at": "2026-01-01",
        }
        mock_store.get_all.return_value = MagicMock()  # 非 dict → skip
        mock_mgr.get_store.return_value = mock_store
        block = _format_block("agent_yua", mock_mgr)
        assert block == "[你跟 Bry 的關係]\n  熟悉度: 親密"

    def test_l1_wired_into_group_and_private(self):
        from src.llm.proxy import _build_messages_group, _build_messages_private

        def memory():
            m = MagicMock()
            m.get_group_history.return_value = []
            m.get_recent_with_meta.return_value = []
            return m

        mock_mgr = _make_mgr(_others())
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            group = _build_messages_group(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=memory(),
            )
            private = _build_messages_private(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=memory(),
            )
        for sys_content in (group[0]["content"], private[0]["content"]):
            assert "你跟其他角色的關係" in sys_content
            assert "你跟 agent_yua 的關係" in sys_content
            assert "你跟 agent_ruka 的關係" in sys_content
            assert "你跟 agent_akane 的關係" in sys_content

    def test_l1_reads_store_get_all(self):
        """Layer 1 資料源 = RelationshipsStore.get_all() = relationships.json 的 others dict。"""
        mock_mgr = _make_mgr(_others())
        _format_block("agent_yua", mock_mgr)
        mock_mgr.get_store.return_value.get_all.assert_called_once()

    def test_l1_real_store_roundtrip(self, tmp_path):
        """真實 store (檔案) → cross-agent lines 出現。"""
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID
        store_dir = tmp_path / "rels"
        store = RelationshipsStore(agent_id="agent_yua", data_dir=store_dir)
        store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.85)
        store.ensure_relationship("agent_ruka", initial_confidence=0.6)
        store.ensure_relationship("agent_akane", initial_confidence=0.9)
        store.ensure_relationship("agent_mai", initial_confidence=0.1)

        mock_mgr = MagicMock()
        mock_mgr.get_store.return_value = store
        block = _format_block("agent_yua", mock_mgr)
        # top3: akane(0.9), ruka(0.6), mai(0.1) — mai 雖低仍算 top3 (只有 3 個 others)
        assert "你跟 agent_akane 的關係：認識" in block
        assert "你跟 agent_ruka 的關係：認識" in block
        assert "你跟 agent_mai 的關係：陌生人" in block


# ───────────────────────────────────────────────────────────
# Layer 2: shared_event — writer + scheduler
# ───────────────────────────────────────────────────────────


class TestSharedEventWriter:
    def test_l2_write_shared_event_writes_diary(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_ok)
            writer = _make_writer(tmp_path)
            activity = ACTIVITY_POOL[4]  # 看書 (leisure, non-shareable) — 任意取
            asyncio.run(writer.write_shared_event("agent_yua", "agent_ruka", activity))
            entries = _read_diary(tmp_path, "agent_yua")
            assert len(entries) == 1
            e = entries[0]
            assert e["slot"] == "event"
            assert e["source"] == "llm"
            assert e["activity"] == activity["name"]
            assert e["category"] == activity["category"]
        finally:
            _restore()

    def test_l2_write_shared_event_returns_content(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_ok)
            writer = _make_writer(tmp_path)
            activity = {"name": "散步", "category": "leisure", "shareable": True}
            path, content = asyncio.run(
                writer.write_shared_event("agent_yua", "agent_ruka", activity)
            )
            assert path is not None and path.exists()
            assert content  # 非空, 給 interactions.jsonl 用
        finally:
            _restore()

    def test_l2_write_shared_event_llm_fail_fallback(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_none)
            writer = _make_writer(tmp_path)
            activity = {"name": "做飯", "category": "food", "shareable": True}
            path, content = asyncio.run(
                writer.write_shared_event("agent_yua", "agent_ruka", activity)
            )
            entries = _read_diary(tmp_path, "agent_yua")
            assert entries[0]["source"] == "placeholder"
            assert "agent_ruka" in content
            assert "做飯" in content
        finally:
            _restore()

    def test_l2_write_shared_event_prompt_mentions_partner(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            captured = {}

            async def fake_capture(system, user, *args, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return "內容"

            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_capture)
            writer = _make_writer(tmp_path)
            activity = {"name": "看書", "category": "leisure", "shareable": False}
            asyncio.run(writer.write_shared_event("agent_yua", "agent_ruka", activity))
            assert "agent_ruka" in captured["system"]
            assert "看書" in captured["user"]
        finally:
            _restore()


class TestSharedEventScheduler:
    def test_l2_fire_shared_event_records_and_diaries(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            from src.soul import scheduler as sched_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_ok)
            monkeypatch.setattr(dream_mod, "get_dream_event_writer", lambda: _make_writer(tmp_path))
            # 固定抽樣 + 固定活動, 測試 deterministic
            monkeypatch.setattr(
                sched_mod.random, "sample",
                lambda seq, n: ["agent_yua", "agent_ruka"][:n],
            )
            activity = {"name": "散步", "category": "leisure", "shareable": True}
            monkeypatch.setattr(sched_mod.random, "choice", lambda seq: activity)

            sched = _make_scheduler(tmp_path)
            asyncio.run(sched._fire_shared_event())

            records = _read_interactions(tmp_path)
            assert len(records) == 1
            r = records[0]
            assert r["type"] == "shared_event"
            assert r["agents"] == ["agent_yua", "agent_ruka"]
            assert r["activity"] == "散步"
            assert r["content"]
            assert "ts" in r
            # 兩隻的 diary 都有 event entry
            assert len(_read_diary(tmp_path, "agent_yua")) == 1
            assert len(_read_diary(tmp_path, "agent_ruka")) == 1
        finally:
            _restore()

    def test_l2_cooldown_rescheduled_6_to_12h(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            from src.soul import scheduler as sched_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_ok)
            monkeypatch.setattr(dream_mod, "get_dream_event_writer", lambda: _make_writer(tmp_path))
            monkeypatch.setattr(
                sched_mod.random, "sample",
                lambda seq, n: ["agent_yua", "agent_ruka"][:n],
            )
            activity = {"name": "散步", "category": "leisure", "shareable": True}
            monkeypatch.setattr(sched_mod.random, "choice", lambda seq: activity)

            sched = _make_scheduler(tmp_path)
            asyncio.run(sched._fire_shared_event())
            assert sched._next_shared_event_time is not None
            from src.timezone_utils import now_local
            delta_min = (sched._next_shared_event_time - now_local()).total_seconds() / 60.0
            assert 6 * 60 <= delta_min <= 12 * 60, (
                f"shared_event 冷卻應在 6-12h, 實際 {delta_min:.1f} min"
            )
        finally:
            _restore()

    def test_l2_timer_none_false_and_start_init(self, tmp_path):
        _isolate(tmp_path)
        try:
            from src.timezone_utils import now_local
            sched = SoulScheduler()
            assert sched._is_shared_event_time(now_local()) is False
            assert sched._is_cross_chat_time(now_local()) is False

            async def _run():
                await sched.start()
                try:
                    assert sched._next_shared_event_time is not None
                    assert sched._next_cross_chat_time is not None
                    # 落在 6-12h 內
                    d1 = (sched._next_shared_event_time - now_local()).total_seconds() / 60.0
                    d2 = (sched._next_cross_chat_time - now_local()).total_seconds() / 60.0
                    assert 6 * 60 <= d1 <= 12 * 60
                    assert 6 * 60 <= d2 <= 12 * 60
                finally:
                    await sched.stop()

            asyncio.run(_run())
        finally:
            _restore()

    def test_l2_no_bus_publish_shared_event(self, tmp_path, monkeypatch):
        """shared_event 只 call LLM + 寫檔, 不 publish 任何 bus 事件。"""
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            from src.soul import scheduler as sched_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_ok)
            monkeypatch.setattr(dream_mod, "get_dream_event_writer", lambda: _make_writer(tmp_path))
            monkeypatch.setattr(
                sched_mod.random, "sample",
                lambda seq, n: ["agent_yua", "agent_ruka"][:n],
            )
            monkeypatch.setattr(
                sched_mod.random, "choice",
                lambda seq: {"name": "散步", "category": "leisure", "shareable": True},
            )
            bus = MagicMock()
            sched = _make_scheduler(tmp_path, bus=bus)
            asyncio.run(sched._fire_shared_event())
            bus.publish.assert_not_called()
        finally:
            _restore()


# ───────────────────────────────────────────────────────────
# Layer 3: cross_chat — writer + scheduler + 迴圈防護
# ───────────────────────────────────────────────────────────


class TestCrossChatWriter:
    def test_l3_turn1_prompt_open(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            captured = {}

            async def fake_capture(system, user, *args, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return "你好呀"

            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_capture)
            writer = _make_writer(tmp_path)
            asyncio.run(writer.generate_chat_turn("agent_yua", "agent_ruka", turn=1))
            assert "正在跟 agent_ruka 聊天" in captured["system"]
            assert "開場" in captured["system"]
        finally:
            _restore()

    def test_l3_turn2_prompt_echo_partner(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            captured = {}

            async def fake_capture(system, user, *args, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return "嗯嗯"

            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_capture)
            writer = _make_writer(tmp_path)
            asyncio.run(writer.generate_chat_turn("agent_ruka", "agent_yua", turn=2, partner_message="最近好嗎"))
            assert "正在跟 agent_yua 聊天" in captured["system"]
            assert "agent_yua 說" in captured["user"]
            assert "最近好嗎" in captured["user"]
        finally:
            _restore()

    def test_l3_turn3_prompt_close(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            captured = {}

            async def fake_capture(system, user, *args, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return "下次再聊"

            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_capture)
            writer = _make_writer(tmp_path)
            asyncio.run(writer.generate_chat_turn("agent_yua", "agent_ruka", turn=3, partner_message="好呀"))
            assert "收尾" in captured["system"]
            assert "agent_ruka 說" in captured["user"]
        finally:
            _restore()

    def test_l3_llm_fail_fallback_placeholders(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            from src.soul import dream_event as dream_mod
            monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", _fake_llm_none)
            writer = _make_writer(tmp_path)
            m1 = asyncio.run(writer.generate_chat_turn("agent_yua", "agent_ruka", turn=1))
            m2 = asyncio.run(writer.generate_chat_turn("agent_ruka", "agent_yua", turn=2))
            m3 = asyncio.run(writer.generate_chat_turn("agent_yua", "agent_ruka", turn=3))
            assert m1 == CHAT_FALLBACK_TURN1
            assert m2 == CHAT_FALLBACK_TURN2
            assert m3 == CHAT_FALLBACK_TURN3
        finally:
            _restore()


class TestCrossChatScheduler:
    def _fake_firections(self, tmp_path, monkeypatch, chat_lines=None, llm=None):
        """共用: 固定抽樣 + fake writer (記錄 calls) + fake LLM。"""
        from src.soul import dream_event as dream_mod
        from src.soul import scheduler as sched_mod
        if llm is None:
            llm = _fake_llm_ok
        monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", llm)
        monkeypatch.setattr(
            sched_mod.random, "sample",
            lambda seq, n: ["agent_yua", "agent_ruka"][:n],
        )
        calls = []

        class FakeWriter:
            async def generate_chat_turn(self, speaker_id, partner_id, turn, partner_message=""):
                calls.append((speaker_id, partner_id, turn, partner_message))
                if chat_lines is not None:
                    return chat_lines.pop(0)
                return f"{speaker_id}-turn{turn}"

        monkeypatch.setattr(dream_mod, "get_dream_event_writer", lambda: FakeWriter())
        return calls

    def test_l3_fire_cross_chat_3_rounds(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            lines = ["你好呀", "你也是", "那下次再聊"]
            calls = self._fake_firections(tmp_path, monkeypatch, chat_lines=lines)
            sched = _make_scheduler(tmp_path)
            asyncio.run(sched._fire_cross_chat())

            records = _read_interactions(tmp_path)
            assert len(records) == 1
            r = records[0]
            assert r["type"] == "cross_chat"
            assert r["agents"] == ["agent_yua", "agent_ruka"]
            msgs = r["messages"]
            assert len(msgs) == 3
            # 3 輪: A 開 → B 回 → A 收
            assert [m["agent"] for m in msgs] == ["agent_yua", "agent_ruka", "agent_yua"]
            assert [m["content"] for m in msgs] == ["你好呀", "你也是", "那下次再聊"]
            # 剛好 3 次 LLM call
            assert len(calls) == 3
            # cross_chat 是封閉事件: 不寫 diary
            assert _read_diary(tmp_path, "agent_yua") == []
            assert _read_diary(tmp_path, "agent_ruka") == []
        finally:
            _restore()

    def test_l3_loop_protection_no_publish(self, tmp_path, monkeypatch):
        """關鍵: cross_chat 不得 publish AGENT_INTENT / AGENCY_TRIGGER / 任何 bus 事件。"""
        _isolate(tmp_path)
        try:
            self._fake_firections(tmp_path, monkeypatch, chat_lines=["a", "b", "c"])
            bus = MagicMock()
            sched = _make_scheduler(tmp_path, bus=bus)

            # 若 cross_chat 誤 publish → 直接 raise, 測試失敗
            async def _boom(*a, **k):
                raise AssertionError("cross_chat 不得 publish AGENCY_TRIGGER")

            async def _boom2(*a, **k):
                raise AssertionError("cross_chat 不得 publish AGENT_INTENT")

            monkeypatch.setattr(sched, "_publish_agency_trigger", _boom)
            monkeypatch.setattr(sched, "_publish_agent_intent", _boom2)

            asyncio.run(sched._fire_cross_chat())
            bus.publish.assert_not_called()
        finally:
            _restore()

    def test_l3_llm_fail_still_3_messages(self, tmp_path, monkeypatch):
        """LLM 全掛 → fallback placeholder, 仍有 3 條訊息可記錄。"""
        _isolate(tmp_path)
        try:
            self._fake_firections(tmp_path, monkeypatch, llm=_fake_llm_none)
            sched = _make_scheduler(tmp_path)
            asyncio.run(sched._fire_cross_chat())
            r = _read_interactions(tmp_path)[0]
            assert len(r["messages"]) == 3
            assert all(m["content"] for m in r["messages"])
        finally:
            _restore()

    def test_l3_relationship_touch_after_chat(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            self._fake_firections(tmp_path, monkeypatch, chat_lines=["a", "b", "c"])
            fake_mgr = MagicMock()
            with patch(
                "src.soul.relationships.get_relationships_manager",
                return_value=fake_mgr,
            ):
                sched = _make_scheduler(tmp_path)
                asyncio.run(sched._fire_cross_chat())
            # 兩隻都 touch (A→[A,B], B→[A,B])
            fake_mgr.on_agent_speak.assert_any_call("agent_yua", ["agent_yua", "agent_ruka"])
            fake_mgr.on_agent_speak.assert_any_call("agent_ruka", ["agent_yua", "agent_ruka"])
        finally:
            _restore()

    def test_l3_cooldown_rescheduled_6_to_12h(self, tmp_path, monkeypatch):
        _isolate(tmp_path)
        try:
            self._fake_firections(tmp_path, monkeypatch, chat_lines=["a", "b", "c"])
            sched = _make_scheduler(tmp_path)
            asyncio.run(sched._fire_cross_chat())
            from src.timezone_utils import now_local
            delta_min = (sched._next_cross_chat_time - now_local()).total_seconds() / 60.0
            assert 6 * 60 <= delta_min <= 12 * 60
        finally:
            _restore()


# ───────────────────────────────────────────────────────────
# Board: GET /api/soul/interactions
# ───────────────────────────────────────────────────────────


def _write_interactions_file(tmp_path, records: list):
    path = tmp_path / "data" / "soul" / "interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_board_endpoint_newest_first(tmp_path):
    _isolate(tmp_path)
    try:
        from fastapi.testclient import TestClient
        from src.io.gateway import IOGateway
        _write_interactions_file(tmp_path, [
            {"ts": "2026-08-22T01:00:00+00:00", "type": "shared_event",
             "agents": ["agent_yua", "agent_ruka"], "activity": "散步", "content": "今天一起散步"},
            {"ts": "2026-08-22T02:00:00+00:00", "type": "cross_chat",
             "agents": ["agent_yua", "agent_akane"],
             "messages": [{"agent": "agent_yua", "content": "hi"}, {"agent": "agent_akane", "content": "yo"}, {"agent": "agent_yua", "content": "bye"}]},
            {"ts": "2026-08-22T03:00:00+00:00", "type": "shared_event",
             "agents": ["agent_mai", "agent_miku"], "activity": "看書", "content": "一起看書"},
        ])
        app = IOGateway(None).app
        client = TestClient(app)
        resp = client.get("/api/soul/interactions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["interactions"]) == 3
        # 新到舊
        assert data["interactions"][0]["ts"].startswith("2026-08-22T03")
        assert data["interactions"][1]["type"] == "cross_chat"
        assert data["interactions"][2]["ts"].startswith("2026-08-22T01")
        # limit 生效
        resp2 = client.get("/api/soul/interactions?limit=1")
        assert resp2.json()["count"] == 1
        assert resp2.json()["interactions"][0]["ts"].startswith("2026-08-22T03")
    finally:
        _restore()


def test_board_endpoint_no_file(tmp_path):
    _isolate(tmp_path)
    try:
        from fastapi.testclient import TestClient
        from src.io.gateway import IOGateway
        client = TestClient(IOGateway(None).app)
        resp = client.get("/api/soul/interactions")
        assert resp.status_code == 200
        assert resp.json() == {"interactions": [], "count": 0}
    finally:
        _restore()


# ───────────────────────────────────────────────────────────
# Frozen contract: scheduler 既有 API surface 不變
# ───────────────────────────────────────────────────────────


def test_scheduler_existing_api_surface_unchanged():
    sched = SoulScheduler()
    for name in (
        "register", "register_dream_event", "register_heartbeat",
        "register_proactive_dm", "start", "stop",
    ):
        assert hasattr(sched, name), f"SoulScheduler.{name} missing"
    assert isinstance(sched._all_agents, list)
