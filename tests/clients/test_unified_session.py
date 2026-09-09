"""
tests/clients/test_unified_session.py — VC-2.5 跨介面統一短期會話流驗收測試。

對應工單 §4 六個剛性測試 + 1 個 brain 整合測試：
- Test 1：跨端雙向 Append 與持久化（TG/VC channel 標記、時間序追加）
- Test 2：MICRO 微時差（<300s）
- Test 3：SHORT 短時差（300~7200s）
- Test 4：CIRCADIAN_NIGHT 深夜作息（22:00~07:00 跨越）
- Test 5：LONG_NEW_DAY 跨夜重聚截斷（>8h，history 為空）
- Test 6：Token 預算上限保護（≤600）
- Test 7：AkaneVoiceBrain 讀取端整合（anchor 注入 + session history 取代）

執行：.venv\\Scripts\\python.exe -m pytest tests/clients/test_unified_session.py -v
全離線：0 網路、0 LLM、0 碰真實 data/。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.session_store import (
    DialogueTurn,
    SessionStore,
    TOKEN_BUDGET,
    _estimate_tokens,
)


def _write_turns(store, agent_id, user_id, turns):
    """以指定 timestamp 直接寫入（測試用，繞過 append_turn 的 time.time）。"""
    store._save_turns(agent_id, user_id, turns)


def _ts(y, mo, d, h, mi):
    """本地時間 → epoch（naive datetime.timestamp 用本地時區，與 time.localtime 一致）。"""
    return dt.datetime(y, mo, d, h, mi).timestamp()


class TestCrossChannelAppendAndPersistence:
    def test_tg_then_vc_append_in_order(self, tmp_path):
        store = SessionStore(tmp_path)
        store.append_turn("agent_akane", "user_bryan", "user", "TG 第一句", "telegram")
        # 新開一個 SessionStore 實例（模擬另一介面/程序）仍讀得到
        store2 = SessionStore(tmp_path)
        ctx = store2.get_active_context("agent_akane", "user_bryan")
        assert ctx["history"][0]["role"] == "user"
        assert ctx["history"][0]["content"] == "TG 第一句"
        # VC 再寫一輪 → 按時間序追加，無覆蓋遺失
        store2.append_turn("agent_akane", "user_bryan", "assistant", "VC 回覆", "voice")
        ctx3 = store2.get_active_context("agent_akane", "user_bryan")
        assert [t["content"] for t in ctx3["history"]] == ["TG 第一句", "VC 回覆"]
        # channel 標記正確
        raw = store2._load_turns("agent_akane", "user_bryan")
        assert [t.channel for t in raw] == ["telegram", "voice"]


class TestTemporalPhases:
    def test_micro_30s(self, tmp_path):
        store = SessionStore(tmp_path)
        now = _ts(2026, 9, 8, 15, 0)
        _write_turns(store, "agent_akane", "user_bryan", [
            DialogueTurn(role="user", content="TG 剛講的", channel="telegram",
                         timestamp=now - 30),
        ])
        ctx = store.get_active_context("agent_akane", "user_bryan", now=now)
        assert ctx["phase"] == "MICRO"
        assert "直接延續該話題" in ctx["guidance"]
        assert "嚴禁任何開場問候" in ctx["guidance"]

    def test_short_30min(self, tmp_path):
        store = SessionStore(tmp_path)
        now = _ts(2026, 9, 8, 10, 30)
        _write_turns(store, "agent_akane", "user_bryan", [
            DialogueTurn(role="user", content="半小時前", channel="telegram",
                         timestamp=now - 1800),
        ])
        ctx = store.get_active_context("agent_akane", "user_bryan", now=now)
        assert ctx["phase"] == "SHORT"
        assert "自然過渡續接" in ctx["guidance"]

    def test_circadian_night_2330(self, tmp_path):
        store = SessionStore(tmp_path)
        now = _ts(2026, 9, 8, 23, 30)
        _write_turns(store, "agent_akane", "user_bryan", [
            DialogueTurn(role="user", content="晚上九點講的", channel="telegram",
                         timestamp=_ts(2026, 9, 8, 21, 0)),
        ])
        ctx = store.get_active_context("agent_akane", "user_bryan", now=now)
        assert ctx["phase"] == "CIRCADIAN_NIGHT"
        assert "深夜作息" in ctx["guidance"]
        assert "語調請更輕柔安靜" in ctx["guidance"]

    def test_long_new_day_14h_truncates_history(self, tmp_path):
        store = SessionStore(tmp_path)
        now = _ts(2026, 9, 8, 10, 0)
        _write_turns(store, "agent_akane", "user_bryan", [
            DialogueTurn(role="user", content="昨天講的", channel="telegram",
                         timestamp=now - 14 * 3600),
        ])
        ctx = store.get_active_context("agent_akane", "user_bryan", now=now)
        assert ctx["phase"] == "LONG_NEW_DAY"
        assert ctx["history"] == []
        assert "自然致意重聚" in ctx["guidance"]


class TestTokenBudget:
    def test_30_long_turns_trimmed_within_budget(self, tmp_path):
        store = SessionStore(tmp_path)
        for i in range(30):
            store.append_turn(
                "agent_akane", "user_bryan",
                "user" if i % 2 == 0 else "assistant",
                f"第{i}輪的長對話內容" + "這是一段約一百字左右的內容。" * 8,
                "telegram",
            )
        ctx = store.get_active_context("agent_akane", "user_bryan")
        assert len(ctx["history"]) < 30
        est = _estimate_tokens([
            DialogueTurn(role=t["role"], content=t["content"],
                         channel="x", timestamp=0)
            for t in ctx["history"]
        ])
        assert est <= TOKEN_BUDGET
        # 保留最新輪
        assert ctx["history"][-1]["content"].startswith("第29輪")


class TestBrainIntegration:
    def test_brain_injects_anchor_and_uses_session_history(self, tmp_path):
        import time as _time
        store = SessionStore(tmp_path)
        _write_turns(store, "agent_akane", "user_bryan", [
            DialogueTurn(role="user", content="TG 剛講的話題", channel="telegram",
                         timestamp=_time.time() - 30),
        ])
        seen = {}
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: (seen.__setitem__("m", msgs) or iter(["嗯。"])),
            session_store=store,
            config={"memory": {"enabled": False}, "temporal": {"enabled": False}},
        )
        list(brain.stream_respond("繼續"))
        msgs = seen["m"]
        system = msgs[0]["content"]
        assert "【跨介面時空體感】" in system
        assert "TEMPORAL CONVERSATION ANCHOR" in system
        # session history 取代傳入 history：user 回合來自 SessionStore
        assert msgs[1] == {"role": "user", "content": "TG 剛講的話題"}
        assert msgs[2] == {"role": "user", "content": "繼續"}
