"""
tests/test_vc_eh_unification.py

VC-UNIFY-1 (P0, Single Soul Multi-Modalities) — Voice Companion 認知地平線與
SAGE 記憶全鏈路統一驗收套件。工單決策已由 Owner 授權（IMPLEMENTATION AUTHORIZED）。

VC-UNIFY-1.1 (P1 BUGFIX) — Canonical Source-Pair Alignment + Full Recall Proof：
  語音端 SAGE 寫入的 source_pair 統一為 canonical `bryan:{agent_id}`（0 user_bryan
  前綴），使文字端 source_pair_filter 不再遮蔽語音寫入的普通生活記憶。

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
  - VC-UNIFY-1.1 A : Source-Pair 格式驗證 — VC 寫入一輪 → 落盤 Fact 的
             source_pair == f"bryan:{agent_id}"（無 user_bryan 前綴）。
  - VC-UNIFY-1.1 B : 語音生活 → 文字讀取（核心補漏）— VC 寫入普通生活對話，
             文字端標準 MemoryReader 帶 source_pair_filter={"bryan:agent_rem"}
             能讀回該生活 Fact（不再被過濾遮蔽）。
  - VC-UNIFY-1.1 C : 文字生活 → 語音讀取（雙向閉環）— 文字端 canonical pair
             寫入生活 Fact，VC 端 default_memory_retriever 能檢索到。
  - VC-UNIFY-1.1 D : Barge-in 中斷回合 0 graph 寫入 + 現代原生角色（Mai）bypass。

Test count: 4 (VC-UNIFY-1) + 4 (VC-UNIFY-1.1) + 既有 EH 防回歸
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.proxy import _format_horizon_block
from src.memory.sage.provider import SAGELiteProvider
from src.paths import data_root, reset_data_root

from clients.voice_companion.akane_voice_brain import (
    AkaneVoiceBrain,
    default_memory_retriever,
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


# ───────────────────────────────────────────────────────────
# VC-UNIFY-1.1：Canonical Source-Pair Alignment + Full Recall Proof
# ───────────────────────────────────────────────────────────

# 普通生活記憶（非定義句 → 不打 EH-3.1 標 → Lived Experience 而非 Idiolect）
LIFE_SENTENCE = "我今天下午去公園散步，看到花開了。"


def _graph_source_pairs(agent_id: str) -> list[str]:
    """撈取該 agent graph.sqlite 全部 facts 的原始 source_pair 值（DB 實體值）。"""
    db = data_root() / "memory" / agent_id / "graph.sqlite"
    if not db.is_file():
        return []
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT source_pair FROM facts").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _text_side_reader(agent_id: str, query: str, source_pair_filter):
    """文字端標準檢索（對齊 src/memory/middleware.py 的 MemoryReader 用法）。"""
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.reader import MemoryReader

    db_path = data_root() / "memory" / agent_id / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    try:
        return MemoryReader(store).retrieve_context(
            query=query, top_k=3, max_tokens=300, mode="precise",
            source_pair_filter=source_pair_filter,
        )
    finally:
        store.close()


class _FakeStreamer:
    """對齊 akane_live 判定所需的最小介面（interrupt_event）。"""

    def __init__(self, interrupted: bool = False):
        self.interrupt_event = threading.Event()
        if interrupted:
            self.interrupt_event.set()


def _maybe_commit(streamer, brain: AkaneVoiceBrain, user_text: str):
    """與 akane_live._speak_streaming (VC-UNIFY-1 契約) 同構判定：
    完整回合（未被 barge-in 打斷）才 schedule_sage_commit。"""
    if streamer.interrupt_event.is_set():
        return None
    return brain.schedule_sage_commit(user_text, "茜的回覆。")


def _flush_brain_store(brain: AkaneVoiceBrain) -> None:
    """強制 commit brain 内部 SAGE provider 的 pending writes。

    對齊既有 batch commit 語義（GraphStore._BATCH_SIZE=20，且 EH-3.1 打標路徑
    provider.py:481 強制 flush）：生活句不打標 → pending 卡在 buffer，測試的
    獨立連線讀不到。flush 屬 GraphStore 既有 public API（@_locked），0 SAGE 改動。
    """
    provider = brain._get_sage_provider()
    if provider is not None and getattr(provider, "_writer", None) is not None:
        provider._writer.store.flush()


class Test11A_CanonicalSourcePairFormat:
    def test_a_voice_commit_lands_canonical_bryan_pair(self, isolated_data_root, tmp_path):
        """VC 寫入一輪對話 → 落盤 fact 的 source_pair == 'bryan:agent_rem'（0 user_bryan）。"""
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=True)

        async def _turn():
            task = brain.schedule_sage_commit(
                LIFE_SENTENCE, "好呀，散步時看到的花很好看。", session_id="vc_11a",
            )
            assert task is not None, "sage_write 啟用時必須建立背景 task"
            await task

        asyncio.run(_turn())

        _flush_brain_store(brain)  # 既有 batch commit 語義：強制 commit 後獨立連線才可讀
        facts = _graph_facts_about(AGENT_REM, "散步")
        assert facts, "VC 寫入回合必須有 fact 落盤"
        pairs = _graph_source_pairs(AGENT_REM)
        assert pairs, "facts 表必須寫入 source_pair 欄位"
        for sp in pairs:
            assert sp == f"bryan:{AGENT_REM}", (
                f"source_pair={sp!r} != canonical 'bryan:agent_rem'（VC-UNIFY-1.1）"
            )
            assert "user_bryan" not in sp, "不得殘留 user_bryan 前綴（canonical 唯一格式）"


class Test11B_VoiceLifeToText_ReadableWithFilter:
    def test_b_text_reader_with_canonical_filter_reads_voice_life_memory(
        self, isolated_data_root, tmp_path
    ):
        """核心補漏：VC 寫入的普通生活記憶，文字端帶 source_pair_filter 能讀回。"""
        # 步驟 1：VC 語音端寫入普通生活對話（非定義句 → Lived Experience）
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=True)

        async def _turn():
            task = brain.schedule_sage_commit(
                LIFE_SENTENCE, "散步真好。", session_id="vc_11b",
            )
            assert task is not None
            await task

        asyncio.run(_turn())
        _flush_brain_store(brain)  # 既有 batch commit 語義：強制 commit 後獨立連線才可讀
        assert _graph_facts_about(AGENT_REM, "散步"), "前置：生活 fact 必須已落庫"

        # 步驟 2：文字端標準 MemoryReader + source_pair_filter（middleware 同款白名單）
        result = _text_side_reader(
            AGENT_REM, "散步", source_pair_filter={f"bryan:{AGENT_REM}"}
        )
        assert result.facts, "source_pair 過濾後必須仍能召回語音寫入的生活 fact"
        assert any(
            "散步" in (f.subject or "") or "散步" in (f.object or "")
            for f in result.facts
        ), "召回內容必須含散步生活記憶（一般生活記憶不再被過濾遮蔽）"
        assert "散步" in result.summary


class Test11C_TextLifeToVoice_ClosedLoop:
    def test_c_voice_retriever_reads_text_written_life_memory(
        self, isolated_data_root, tmp_path
    ):
        """雙向閉環：文字端 canonical pair 寫入生活 fact → VC 檢索機制可讀。"""
        # 步驟 1：文字端（canonical pair bryan:agent_rem）寫入普通生活 fact
        prov, _ = _make_provider(tmp_path, AGENT_REM)

        async def _write_via_text():
            await prov.post_reply_commit(
                "s1", LIFE_SENTENCE, "嗯，散步是好事。",
                source_pair=f"bryan:{AGENT_REM}",
            )

        asyncio.run(_write_via_text())
        prov._writer.store.flush()  # 既有 batch commit 語義：強制 commit 後新連線才可讀

        # 步驟 2：VC 端 default_memory_retriever（語音端實際檢索機制）讀取
        summary = default_memory_retriever("散步 花", agent_id=AGENT_REM)
        assert summary, "VC 檢索機制必須能召回文字端寫入的生活 fact（雙向閉環）"
        assert "散步" in summary, "召回摘要必須含散步生活記憶"


class Test11D_BargeInAndModernBypass:
    def test_d_barge_in_interrupted_turn_zero_graph_write(
        self, isolated_data_root, tmp_path
    ):
        """Barge-in 中斷回合 → 不排程 SAGE 寫入 → 0 graph 寫入。"""
        _make_provider(tmp_path, AGENT_REM)
        brain = _voice_brain(AGENT_REM, sage_write=True)

        # 中斷側：interrupt_event set → 回合視為中斷 → 不排程、不寫入
        streamer = _FakeStreamer(interrupted=True)  # 播放中被 barge-in
        assert _maybe_commit(streamer, brain, EXPLANATORY_SENTENCE) is None, (
            "被 barge-in 中斷的回合不得排程 SAGE 寫入"
        )
        assert _graph_facts_about(AGENT_REM, "氣炸鍋") == [], (
            "barge-in 中斷回合必須 0 graph 寫入"
        )

        # 對照：完整回合（未中斷）→ 正常落庫（在 event loop 內排程，取得 task）
        streamer_ok = _FakeStreamer(interrupted=False)

        async def _full_round():
            task = _maybe_commit(streamer_ok, brain, EXPLANATORY_SENTENCE)
            assert task is not None, "完整回合必須排程 SAGE 寫入"
            await task

        asyncio.run(_full_round())
        _flush_brain_store(brain)
        assert _graph_facts_about(AGENT_REM, "氣炸鍋"), "完整回合必須正常落庫"

    def test_d_modern_native_mai_bypass_unchanged(self, isolated_data_root, tmp_path):
        """防回歸：現代原生角色 Mai（agent_mai）語音端 Horizon Gate 維持 bypass。"""
        _make_provider(tmp_path, "agent_mai")
        brain = _voice_brain("agent_mai", sage_write=False)
        messages = brain._build_messages("今天好累")
        assert "[HORIZON-認知地平線]" not in messages[0]["content"]
        assert format_voice_horizon_block("agent_mai") == ""


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))