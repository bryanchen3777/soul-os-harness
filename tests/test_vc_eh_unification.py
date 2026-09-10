"""
tests/test_vc_eh_unification.py

VC-UNIFY-1 (P0, Single Soul Multi-Modalities) — Voice Companion 認知地平線與
SAGE 記憶全鏈路統一驗收套件。工單決策已由 Owner 授權（IMPLEMENTATION AUTHORIZED）。

驗證對象（全部在隔離 temp data_root 執行，0 生產資料污染）:
  - Test A : VC 讀側注入 — 模擬 VC Prompt 構建流程（AkaneVoiceBrain._build_messages，
             即 stream_respond 實際組裝路徑），傳入 agent_rem。斷言生成的 System
             Prompt 明確包含 `[HORIZON-認知地平線]` 區塊，且含 SAGE 已內化的
             Idiolect（預置 assimilated fact，氣炸鍋）。
  - Test B : VC 寫側非同步落庫 — 模擬 VC 產生一輪語音對話（含定義句「氣炸鍋就是個
             小箱子」），經 brain.schedule_sage_commit 觸發背景寫入，await 該 task
             完成後查詢 graph.sqlite。斷言 Fact 落庫，且命中 EH-3.1 五閘門 →
             origin=assimilated / horizon_state=aware / learned_at 為 float。
  - Test C : 雙向互通端到端 — 步驟 1 在 VC 語音端教導新概念（隨身碟定義句，背景
             寫入落庫）；步驟 2 在文字端 `_format_horizon_block("agent_rem")`
             提及隨身碟。斷言文字端能讀取語音端教導的 Idiolect（跨模態無縫默契）。
  - 防回歸 : 現代原生角色（agent_akane）Horizon Gate fail-silent bypass 維持 ""。

Test count: 4
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.proxy import _format_horizon_block
from src.memory.sage.provider import SAGELiteProvider
from src.paths import data_root, reset_data_root

from clients.voice_companion.akane_voice_brain import (
    AkaneVoiceBrain,
    format_voice_horizon_block,
)

# ── 工單固定輸入 ─────────────────────────────────────────────

EXPLANATORY_SENTENCE = "氣炸鍋就是個不用添柴或魔石、插電就能把食物烘烤酥脆的箱子。"
NEW_CONCEPT_SENTENCE = "隨身碟就是個插在電腦上存取資料的小匣子。"
AGENT_REM = "agent_rem"

# ── Helpers（隔離 data_root，風格對齊 EH-3 / MEM-WIRING-1 套件）──


@pytest.fixture()
def isolated_data_root(tmp_path: Path, monkeypatch):
    """把 SOUL_OS_DATA_DIR 指到 temp，重置單例，測試後還原。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_LLM_JUDGE", "false")  # 0 網路：writer 走 regex heuristic
    reset_data_root()
    yield data_root()
    reset_data_root()


def _make_provider(tmp_path: Path, profile_id: str, session_id: str = "s1"):
    """Isolated SAGELiteProvider (graph.sqlite 位於 tmp/data/memory/<profile>/)."""
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id=session_id)
    return prov, soul_dir


async def _commit_explanatory(provider, sid: str, sentence: str):
    """與 EH-3 Test A 同款真實路徑：定義句 → post_reply_commit → EH-3.1 打標。"""
    await provider.post_reply_commit(
        sid, sentence, "明白了。",
        source_pair="bryan:agent_rem",
    )


def _graph_facts_about(agent_id: str, entity: str) -> list[dict]:
    """以獨立 sqlite 連線（等同讀側新連接視角）撈取含 entity 的 facts 維度。"""
    db = data_root() / "memory" / agent_id / "graph.sqlite"
    if not db.is_file():
        return []
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT subject, predicate, object, origin, horizon_state, source, learned_at
               FROM facts
               WHERE subject LIKE ? OR object LIKE ?
               ORDER BY timestamp DESC""",
            (f"%{entity}%", f"%{entity}%"),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _voice_brain(agent_id: str, sage_write: bool) -> AkaneVoiceBrain:
    """模擬 VC 語音大腦（config 帶 memory.sage_write 開關；0 llm endpoint → regex fallback）。"""
    return AkaneVoiceBrain(
        agent_id=agent_id,
        config={
            "memory": {"sage_write": {"enabled": sage_write}, "session_store": {"enabled": False}},
            "llm": {"endpoint": "", "api_key": ""},
        },
        # 語音端不注入 retriever/temporal：預設值 fail-silent，維持語音服務原樣
    )


# ───────────────────────────────────────────────────────────
# Test A: VC 讀側注入驗證（agent_rem 拿到與文字端相同的 Horizon + Idiolect）
# ───────────────────────────────────────────────────────────


class TestA_ReadSideHorizonInjection:
    def test_a_voice_system_prompt_contains_horizon_and_idiolect(self, isolated_data_root, tmp_path):
        """預置氣炸鍋 assimilated fact → 語音端 _build_messages 產出含
        [HORIZON-認知地平線] + 氣炸鍋 Idiolect 的 System Prompt。"""
        prov, _ = _make_provider(tmp_path, AGENT_REM)
        asyncio.run(_commit_explanatory(prov, "s1", EXPLANATORY_SENTENCE))

        brain = _voice_brain(AGENT_REM, sage_write=False)
        messages = brain._build_messages("提到氣炸鍋")
        sys_prompt = messages[0]["content"]

        assert "[HORIZON-認知地平線]" in sys_prompt, (
            "語音端 System Prompt 必須包含 Horizon Gate 區塊"
        )
        assert "氣炸鍋" in sys_prompt, (
            "語音端 System Prompt 必須包含 SAGE 已內化的 Idiolect（氣炸鍋）"
        )
        # 阻力約束負向措辭（與文字端相同，VC-2.4 輸出守門不變）
        assert "無預設運作原理認知" in sys_prompt

    def test_a_horizon_placement_after_persona_before_conversation(self, isolated_data_root, tmp_path):
        """注入位置：Persona 之後、即時對話之前（符合 EH-1 唯一定序）。"""
        prov, _ = _make_provider(tmp_path, AGENT_REM)
        asyncio.run(_commit_explanatory(prov, "s1", EXPLANATORY_SENTENCE))

        brain = _voice_brain(AGENT_REM, sage_write=False)
        messages = brain._build_messages("氣炸鍋是什麼")
        sys_prompt = messages[0]["content"]

        persona_pos = sys_prompt.find("語音輸出守門")
        horizon_pos = sys_prompt.find("[HORIZON-認知地平線]")
        user_pos = len(messages) - 1  # 即時對話在最後一條

        assert horizon_pos > persona_pos, "Horizon 必須在 Persona 之後"
        assert messages[-1]["role"] == "user", "即時對話必須在最後（Horizon 之前）"
        assert user_pos > 0

    def test_a_modern_native_bypass_unchanged(self, isolated_data_root, tmp_path):
        """防回歸：現代原生角色（agent_akane）語音端 Horizon Gate 仍 fail-silent bypass。"""
        _make_provider(tmp_path, "agent_akane")
        brain = _voice_brain("agent_akane", sage_write=False)
        messages = brain._build_messages("今天好累")
        assert "[HORIZON-認知地平線]" not in messages[0]["content"]


# ───────────────────────────────────────────────────────────
# Test B: VC 寫側非同步落庫驗證（fire-and-forget + EH-3.1 五閘門）
# ───────────────────────────────────────────────────────────


class TestB_WriteSideAsyncSageCommit:
    def test_b_background_commit_lands_assimilated_fact(self, isolated_data_root, tmp_path):
        """語音回合（定義句）→ schedule_sage_commit → await 背景 task → graph.sqlite
        落庫且命中 EH-3.1：origin=assimilated / horizon_state=aware / learned_at float。"""
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=True)

        async def _turn():
            task = brain.schedule_sage_commit(
                EXPLANATORY_SENTENCE,
                "明白了。氣炸鍋是主人教的用法。",
                session_id="vc_s1",
            )
            assert task is not None, "sage_write 啟用時必須建立背景 task"
            await task  # 驗收用：等背景寫入完成（生產路徑 0 await）

        asyncio.run(_turn())

        facts = _graph_facts_about(AGENT_REM, "氣炸鍋")
        assert facts, "定義句必須萃取出含氣炸鍋的 fact 並落庫"
        user_facts = [f for f in facts if f["source"] == "user"]
        assert user_facts, "必須有 user-source fact（assistant/inference fact 不打標，Gate A）"
        for f in user_facts:
            assert f["origin"] == "assimilated", (
                f"origin={f['origin']!r} != 'assimilated' (EH-3.1 閘門 3/5 定義句)"
            )
            assert f["horizon_state"] == "aware", (
                f"horizon_state={f['horizon_state']!r} != 'aware'"
            )
            assert isinstance(f["learned_at"], float) and f["learned_at"] is not None, (
                f"learned_at 必須為 float（EH-3.1 閘門 5/5），got {f['learned_at']!r}"
            )
        # Gate A 反向防回歸：assistant/inference fact 不得誤標
        for f in facts:
            if f["source"] != "user":
                assert f["origin"] != "assimilated", (
                    f"inference/assistant fact 不得被打 assimilated: {f!r}"
                )

    def test_b_disabled_config_produces_no_task_no_write(self, isolated_data_root, tmp_path):
        """防回歸：config 未開 sage_write → 0 task、0 落庫（預設關閉，0 既有行為）。"""
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=False)

        async def _turn():
            task = brain.schedule_sage_commit(EXPLANATORY_SENTENCE, "茜表示明白。")
            assert task is None

        asyncio.run(_turn())
        assert _graph_facts_about(AGENT_REM, "氣炸鍋") == []


# ───────────────────────────────────────────────────────────
# Test C: 雙向互通端到端（語音端教導 → 文字端讀取）
# ───────────────────────────────────────────────────────────


class TestC_BidirectionalCrossModality:
    def test_c_voice_teaches_text_reads(self, isolated_data_root, tmp_path):
        """步驟 1：VC 語音端教導新概念 X（隨身碟）背景落庫；
        步驟 2：文字端 _format_horizon_block("agent_rem") 讀到 X → 跨模態無縫默契。"""
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=True)

        async def _teach_via_voice():
            task = brain.schedule_sage_commit(
                NEW_CONCEPT_SENTENCE,
                "明白了。隨身碟是存資料用的。",
                session_id="vc_s2",
            )
            await task

        asyncio.run(_teach_via_voice())

        # 步驟 2：文字端（主服務 proxy 同一函式）讀取
        block = _format_horizon_block(AGENT_REM)
        assert "[HORIZON-認知地平線]" in block
        assert "隨身碟" in block, (
            "文字端必須能讀到語音端教導的 Idiolect（隨身碟）— 跨模態無縫默契"
        )

    def test_c_voice_horizon_helper_matches_text_side(self, isolated_data_root, tmp_path):
        """語音端 helper 與文字端 _format_horizon_block 同一來源（單一大腦）。"""
        assert format_voice_horizon_block(AGENT_REM) == _format_horizon_block(AGENT_REM)
        assert format_voice_horizon_block("agent_akane") == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))