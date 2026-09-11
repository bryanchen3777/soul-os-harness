"""
tests/test_vc_eh_unification.py

VC-UNIFY-1 (P0, Single Soul Multi-Modalities) — Voice Companion 認知地平線與
SAGE 記憶全鏈路統一驗收套件。工單決策已由 Owner 授權（IMPLEMENTATION AUTHORIZED）。

VC-UNIFY-1.1 (P1 BUGFIX) — Canonical Source-Pair Alignment + Full Recall Proof：
  語音端 SAGE 寫入的 source_pair 統一為 canonical `bryan:{agent_id}`。

VC-UNIFY-1.2 (P1 BUGFIX) — Voice Ordinary-Memory Visibility Commit：
  背景 _commit() 成功路徑內顯式 flush GraphStore，使語音生活記憶（非定義句、
  受 batch 門檻影響）對文字端獨立 SQLite 連線立即可見（跨進程事務可見性）。
  0 語音延遲影響（flush 只在背景 task 內）、Fail-Silent、Barge-in 邊界維持。

VC-UNIFY-1.2.1 (TEST-ONLY) — Restore VC-UNIFY Regression Contracts：
  恢復 1.2 重寫測試時意外刪除的 1.0/1.1 核心驗證案例，重組為 8 項完整矩陣 +
  1 潔淨度自檢。生產代碼 0 改動；0 測試端 flush 補償（跨連線可見性全部依賴
  生產端自然提交機制：EH-3.1 打標後 production flush / VC-UNIFY-1.2 背景
  _commit flush / GraphStore 生命週期 close）。

驗證對象（全部在隔離 temp data_root 執行，0 生產資料污染）:
  - Test 1 (恢復): Voice 讀側 Horizon 與掛載順序 — 預置 agent_rem assimilated+
    aware 氣炸鍋 Fact後，_build_messages() 的 system prompt 必須含
    [HORIZON-認知地平線] 與氣炸鍋 Idiolect；順序斷言：Horizon 區塊在 Persona
    之後、最後的即時 User 訊息之前（Single Soul Multi-Modalities 讀側契約）。
  - Test 2 (恢復): Voice 定義句 → EH-3.1 來源純化 — User Fact 標記 assimilated /
    aware / learned_at float；Assistant / Inference Fact 不得被誤標（Gate A）；
    自然可見（0 手動 flush 補償）。
  - Test 3 (恢復): Voice 概念 → Text Idiolect 閉環 — 語音端教導新概念
    （隨身碟）後，文字端 _format_horizon_block("agent_rem") 必須讀到該 Idiolect。
  - Test 4 (恢復): Text 生活記憶 → VC 讀側檢索 雙向閉環 — 文字端標準 Canonical
    Pair（bryan:agent_rem）寫入普通生活 Fact，VC 端 default_memory_retriever
    必須能檢索到（提交=自然生命週期 shutdown/close，0 測試端 flush）。
  - Test 5 (保留 1.2): VC 普通生活 → 文字端獨立連線即時可讀（核心跨進程可見性）。
  - Test 6 (保留 1.2): Barge-in 邊界 — 中斷回合 0 graph 寫入 + 0 flush（spy）。
  - Test 7 (保留 1.2): 開關防護 — sage_write 關閉 → 0 task / 0 provider / 0 write。
  - Test 8 (保留 1.2): 現代角色 Bypass — Akane / Mai Horizon Block 為空。
  - 潔淨度自檢 (保留 1.2): 測試檔案不含補償性 flush helper 呼叫模式。

防回歸 : tests/test_vc_eh_unification.py、tests/clients/test_voice_companion.py、
         tests/test_epistemic_horizon_eh31.py、tests/test_m5_5_2_mem_wiring_1_skip_graph_misbind.py
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.proxy import _format_horizon_block
from src.paths import data_root, reset_data_root

from clients.voice_companion.akane_voice_brain import (
    AkaneVoiceBrain,
    default_memory_retriever,
    format_voice_horizon_block,
)

# ── 工單固定輸入 ─────────────────────────────────────────────

EXPLANATORY_SENTENCE = "氣炸鍋就是個不用添柴或魔石、插電就能把食物烘烤酥脆的箱子。"
USB_SENTENCE = "隨身碟就是個插進電腦就能存取檔案的巴掌大的小盒子。"
LIFE_SENTENCE = "我今天下午去公園散步，看到花開了。"
TEXT_LIFE_SENTENCE = "今天吃了蘋果，很甜。"
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


def _run_voice_commit(brain: AkaneVoiceBrain, user_text: str, agent_text: str, session_id: str):
    """語音端真實寫入路徑：schedule_sage_commit 背景 task，await 到完成。

    生產路徑 0 await（fire-and-forget）；測試在此等背景 task 結束後再以
    文字端獨立連線驗證跨進程可見性。
    """
    async def _turn():
        task = brain.schedule_sage_commit(user_text, agent_text, session_id=session_id)
        assert task is not None, "sage_write 啟用時必須建立背景 task"
        await task  # 驗收用：等背景寫入完成（生產路徑 0 await）

    asyncio.run(_turn())


def _text_side_reader(agent_id: str, query: str, source_pair_filter):
    """文字端標準檢索（對齊 src/memory/middleware.py 的 MemoryReader 用法）。

    內部新建 GraphStore / MemoryReader —— 與 VC 寫入連線完全獨立，
    等同另一進程的新連線視角。讀完 store.close() 屬正常生命週期關閉。
    """
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


# ───────────────────────────────────────────────────────────
# Test 1（恢復 1.0/1.1）：Voice 讀側 Horizon 與掛載順序
# ───────────────────────────────────────────────────────────


class Test1_VoiceReadSideHorizonMountOrder:
    def test_1_horizon_after_persona_before_user_message(self, isolated_data_root, tmp_path):
        """恢復：語音讀側 Horizon 掛載 + 順序契約。

        預置 agent_rem 的 assimilated + aware 氣炸鍋 Fact（定義句 → EH-3.1 打標，
        生產端 scoped flush 自然落盤，0 測試端 flush）→ _build_messages()：
        - System Prompt 必須含 [HORIZON-認知地平線] 與氣炸鍋 Idiolect
        - 順序：Horizon 區塊在 Persona 之後、最後的即時 User 訊息之前
        """
        # 前置：語音端教導氣炸鍋定義句（assimilated + aware 自然落盤）
        brain_writer = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(
            brain_writer, EXPLANATORY_SENTENCE,
            "明白了。氣炸鍋是主人教的用法。", "vc_1_preset",
        )
        facts = _graph_facts_about(AGENT_REM, "氣炸鍋")
        assert any(
            f["origin"] == "assimilated" and f["horizon_state"] == "aware"
            for f in facts
        ), "前置條件：agent_rem 必須存在 assimilated + aware 的氣炸鍋 Fact"

        # 讀側：全新語音大腦（0 寫入干擾）
        brain = _voice_brain(AGENT_REM, sage_write=False)
        user_text = "氣炸鍋是怎麼用的？"
        messages = brain._build_messages(user_text)

        assert messages and messages[0]["role"] == "system", (
            "第一則訊息必須是 system prompt"
        )
        content = messages[0]["content"]

        assert "[HORIZON-認知地平線]" in content, (
            "語音端 system prompt 必須注入認知地平線區塊（agent_rem 非現代原生）"
        )

        # Idiolect（契約 §3.5）：assimilated + aware 條目進入「默契事物清單」
        horizon_start = content.index("[HORIZON-認知地平線]")
        next_block = content.find("\n\n【", horizon_start)
        horizon_zone = content[horizon_start:] if next_block == -1 else content[horizon_start:next_block]
        assert "氣炸鍋" in horizon_zone, (
            "Horizon 區塊內必須含氣炸鍋 Idiolect（讀側檢索閉環）"
        )

        # 順序斷言 1：Horizon 在 Persona 之後（persona 是 sys_parts[0]，content 以其開頭）
        persona_first = next(
            line for line in brain.persona.splitlines() if line.strip()
        ).strip()
        persona_pos = content.index(persona_first)
        assert content.index("[HORIZON-認知地平線]") > persona_pos, (
            "Horizon 區塊必須掛載在 Persona 之後"
        )

        # 順序斷言 2：最後的即時 User 訊息之前（system 為 messages[0]、
        # 即時 user 訊息為 messages[-1]；Horizon 位於 system 內 → 天然在 User 之前）
        assert messages[-1]["role"] == "user", "最後一則必須為即時 User 訊息"
        assert messages[-1]["content"] == user_text, "最後一則必須是本輪即時輸入"
        assert len(messages) >= 2, "system + user 至少兩則"


# ───────────────────────────────────────────────────────────
# Test 2（恢復 1.0/1.1）：Voice 定義句 → EH-3.1 來源純化
# ───────────────────────────────────────────────────────────


class Test2_VoiceDefinitionalSourcePurification:
    def test_2_definitional_marks_user_fact_only(self, isolated_data_root, tmp_path):
        """恢復：定義句寫入 → EH-3.1 來源純化。

        User Fact 標記 assimilated / aware / learned_at float；Assistant /
        Inference Fact 不得被誤標（Gate A：User-Only Source）。
        自然可見：不呼叫手動 flush，依生產機制（EH-3.1 打標後 scoped flush）
        自然落盤，以獨立 sqlite 讀側驗證。
        """
        brain = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(brain, EXPLANATORY_SENTENCE, "明白了。氣炸鍋是主人教的用法。", "vc_2")

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

        # 來源純化閉環：文字端 Horizon Block 可見 Idiolect（跨模態無縫默契）
        block = _format_horizon_block(AGENT_REM)
        assert "[HORIZON-認知地平線]" in block
        assert "氣炸鍋" in block, "文字端必須能讀到語音端教導的 Idiolect（氣炸鍋）"


# ───────────────────────────────────────────────────────────
# Test 3（恢復 1.0/1.1）：Voice 概念 → Text Idiolect 閉環
# ───────────────────────────────────────────────────────────


class Test3_VoiceConceptToTextIdiolectLoop:
    def test_3_new_voice_concept_visible_in_text_horizon_block(self, isolated_data_root, tmp_path):
        """恢復：語音端教導全新概念 → 文字端 Horizon Block 雙向閉環。

        概念換新（隨身碟，非既有測試用語），證明閉環對任意新 Idiolect 成立，
        非氣炸鍋特例。寫入仍走語音端真實路徑（schedule_sage_commit），
        文字端 _format_horizon_block("agent_rem") 必須讀到「隨身碟」。
        """
        brain = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(brain, USB_SENTENCE, "明白了。隨身碟是主人教的用法。", "vc_3")

        # 打標前置確認（assimilated + aware 才進 Idiolect 檢索）
        facts = _graph_facts_about(AGENT_REM, "隨身碟")
        assert facts, "隨身碟定義句必須萃取出 fact 並落庫"
        assert any(
            f["origin"] == "assimilated" and f["horizon_state"] == "aware"
            for f in facts
        ), "隨身碟 Fact 必須被 EH-3.1 標記為 assimilated + aware（Idiolect 檢索契約 §3.5）"

        # 文字端讀側：全新連線，必須自然讀到（0 測試端 flush）
        block = _format_horizon_block(AGENT_REM)
        assert "[HORIZON-認知地平線]" in block
        assert "隨身碟" in block, (
            "文字端 Horizon Block 必須成功讀到語音端教導的隨身碟 Idiolect"
        )


# ───────────────────────────────────────────────────────────
# Test 4（恢復 1.0/1.1）：Text 生活記憶 → VC 讀側檢索 雙向閉環
# ───────────────────────────────────────────────────────────


class Test4_TextLifeMemoryToVCReadSideLoop:
    def test_4_text_life_memory_retrievable_by_vc_read_side(self, isolated_data_root, tmp_path):
        """恢復：反向閉環 — 文字端生活記憶必須能被 VC 讀側檢索到。

        寫入走文字端標準生產 API（SAGELiteProvider.post_reply_commit，與
        middleware 同一路徑）+ canonical source_pair（bryan:agent_rem）。
        提交=自然生命週期：provider.shutdown()（middleware 下線/進程重啟同款）
        → GraphStore.close 自然 commit pending writes（普通生活句不受 batch
        門檻約束的跨連線可見性）。0 測試端 flush 補償。
        """
        from src.memory.sage.provider import SAGELiteProvider

        agent_dir = data_root() / "memory" / AGENT_REM
        provider = SAGELiteProvider(profile_id=AGENT_REM, data_dir=str(agent_dir))
        provider.initialize(session_id="text_4")
        try:
            async def _text_turn():
                await provider.post_reply_commit(
                    session_id="text_4",
                    last_user_msg=TEXT_LIFE_SENTENCE,
                    agent_reply="蘋果很甜，多吃點。",
                    source_pair=f"bryan:{AGENT_REM}",
                )

            asyncio.run(_text_turn())
        finally:
            # 自然提交條件：provider 生命週期正常結束（如進程下線），
            # 內部 close 即 commit pending writes —— 生產機制，非測試補償
            provider.shutdown()

        # 文字端確認 fact 已萃取（生命週期關閉 = 自然提交完成後，新連線視角）
        written = _graph_facts_about(AGENT_REM, "蘋果")
        assert written, "文字端寫入路徑必須萃取含蘋果的 fact"

        # VC 端讀側（default_memory_retriever，全新連線, fail-silent）
        summary = default_memory_retriever("蘋果 今天吃了什麼", agent_id=AGENT_REM)
        assert summary, (
            "VC 讀側必須檢索到文字端寫入的普通生活 Fact（雙向閉環）"
        )
        assert "蘋果" in summary, (
            f"VC 讀側檢索內容必須含蘋果生活記憶，got {summary!r}"
        )


# ───────────────────────────────────────────────────────────
# Test 5（保留 1.2）：VC 普通生活 → 文字端獨立連線即時可讀
# ───────────────────────────────────────────────────────────


class Test5_LifeMemoryImmediatelyVisibleCrossConnection:
    def test_5_vc_life_memory_visible_to_text_reader_without_manual_flush(
        self, isolated_data_root, tmp_path
    ):
        """保留 1.2 核心斷言：VC 寫入普通生活句（非定義句 → 不打標 → 受 batch
        門檻）→ 等待背景 task 結束 → 文字端獨立 MemoryReader + source_pair_filter
        **立即**檢索到「散步」（全程嚴禁任何手動提交流程，依賴 1.2 背景
        _commit 內自動 flush）。"""
        brain = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(brain, LIFE_SENTENCE, "散步真好，看到花很開心。", "vc_5")

        # 文字端（獨立連線視角）：全程 0 手動 flush，直接檢索
        result = _text_side_reader(
            AGENT_REM, "散步", source_pair_filter={f"bryan:{AGENT_REM}"}
        )
        assert result.facts, (
            "語音生活記憶必須跨連線立即檢索到（0 手動 flush，即時陪伴感）"
        )
        assert any(
            "散步" in (f.subject or "") or "散步" in (f.object or "")
            for f in result.facts
        ), "檢索內容必須含散步生活記憶"
        assert "散步" in result.summary

        # 語音端檢索閉環（VC-UNIFY-1.1 雙向閉環防回歸）：canonical pair 可讀
        summary = default_memory_retriever("散步 花", agent_id=AGENT_REM)
        assert summary, "VC 檢索機制必須能召回生活 fact（雙向閉環）"
        assert "散步" in summary

        # canonical pair 防回歸（VC-UNIFY-1.1）
        pairs = _graph_source_pairs(AGENT_REM)
        assert pairs, "facts 表必須寫入 source_pair 欄位"
        for sp in pairs:
            assert sp == f"bryan:{AGENT_REM}", (
                f"source_pair={sp!r} != canonical 'bryan:agent_rem'（VC-UNIFY-1.1）"
            )
            assert "user_bryan" not in sp, "不得殘留 user_bryan 前綴（canonical 唯一格式）"


# ───────────────────────────────────────────────────────────
# Test 6（保留 1.2）：Barge-in 邊界（0 write + 0 flush）
# ───────────────────────────────────────────────────────────


class Test6_BargeInBoundaryPreserved:
    def test_6_barge_in_turn_zero_write_zero_flush(self, isolated_data_root, tmp_path, monkeypatch):
        """保留 1.2：被 barge-in 中斷的回合：0 排程、0 graph 寫入、0 flush
        （spy 斷言）。對照完整回合：排程成功、背景 _commit 成功路徑內自動
        flush 觸發。spy 僅監控（屬性攔截），非補償性呼叫。"""
        brain = _voice_brain(AGENT_REM, sage_write=True)
        provider = brain._get_sage_provider()
        store = provider._writer.store
        flush_calls: list = []
        original_flush = store.flush

        def _spy(*args, **kwargs):
            flush_calls.append(1)
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(store, "flush", _spy)

        # 中斷側：interrupt_event set → 回合視為中斷 → 不排程、不寫入、不 flush
        streamer = _FakeStreamer(interrupted=True)
        assert _maybe_commit(streamer, brain, EXPLANATORY_SENTENCE) is None, (
            "被 barge-in 中斷的回合不得排程 SAGE 寫入"
        )
        assert _graph_facts_about(AGENT_REM, "氣炸鍋") == [], (
            "barge-in 中斷回合必須 0 graph 寫入"
        )
        assert flush_calls == [], "barge-in 中斷回合不得觸發 flush"

        # 對照：完整回合 → 正常落庫（內部自動 flush，跨連線立即可見）
        streamer_ok = _FakeStreamer(interrupted=False)

        async def _full_round():
            task = _maybe_commit(streamer_ok, brain, EXPLANATORY_SENTENCE)
            assert task is not None, "完整回合必須排程 SAGE 寫入"
            await task

        asyncio.run(_full_round())
        assert flush_calls, "完整回合背景 _commit 成功路徑內必須觸發 flush"
        assert _graph_facts_about(AGENT_REM, "氣炸鍋"), (
            "完整回合必須 0 手動 flush 即跨連線可見"
        )


# ───────────────────────────────────────────────────────────
# Test 7（保留 1.2）：開關防護（0 task / 0 write / 0 flush）
# ───────────────────────────────────────────────────────────


class Test7_WriteSwitchGuard:
    def test_7_switch_off_zero_task_zero_provider_zero_write(
        self, isolated_data_root, tmp_path, monkeypatch
    ):
        """保留 1.2：寫入開關關閉時：0 task、0 provider 初始化、0 write
        （故 0 flush）。"""
        brain = _voice_brain(AGENT_REM, sage_write=False)

        def _boom(*args, **kwargs):
            raise AssertionError("開關關閉時不得初始化 SAGE provider")

        monkeypatch.setattr(brain, "_get_sage_provider", _boom)

        task = brain.schedule_sage_commit(LIFE_SENTENCE, "收到。")
        assert task is None, "開關關閉必須 0 task"
        assert _graph_facts_about(AGENT_REM, "散步") == [], "開關關閉必須 0 write"


# ───────────────────────────────────────────────────────────
# Test 8（保留 1.2）：現代角色 Bypass（Akane / Mai）
# ───────────────────────────────────────────────────────────


class Test8_ModernNativeBypass:
    def test_8_akane_mai_modern_native_behavior(self, isolated_data_root, tmp_path):
        """保留 1.2：現代原生角色（agent_akane / agent_mai）：語音端 Horizon Gate
        fail-silent bypass 維持 + Horizon Block 為空（VC-2.4 輸出守門不變）。"""
        for agent in ("agent_akane", "agent_mai"):
            brain = _voice_brain(agent, sage_write=False)
            messages = brain._build_messages("今天好累")
            assert "[HORIZON-認知地平線]" not in messages[0]["content"], (
                f"{agent} 不得注入 Horizon（現代原生 bypass 維持）"
            )
            assert format_voice_horizon_block(agent) == "", (
                f"{agent} Horizon Block 必須為空"
            )


# ───────────────────────────────────────────────────────────
# 潔淨度自檢（保留 1.2）：無補償性 flush helper
# ───────────────────────────────────────────────────────────


class TestHygiene_NoCompensatoryFlushHelper:
    def test_no_private_compensatory_flush_call(self):
        """靜態檢查：測試檔案不得含補償性私人 flush helper（已被 1.2 背景
        _commit 內的自動 flush / EH-3.1 生產端 scoped flush 取代）。

        模式字符串以拼接避開自指（檢查器不得匹配到檢查器自身）。

        注意：本檔案允許的 `flush` 字樣僅限三種語義，均非補償呼叫：
        (a) 註解說明、(b) Test 6 的 spy 屬性監控（攔截呼叫以斷言 0 觸發）、
        (c) 本檢查器的模式字符串（自檢目標）。任何以 store 提交 API 或
        provider 存取路徑直接呼叫提交的形式（呼叫括號緊接其後）一律禁止。
        """
        source = Path(__file__).read_text(encoding="utf-8")

        forbidden_helper = "_flush_brain_" + "store"
        assert forbidden_helper not in source, (
            "VC-UNIFY-1.1 時期遺留的補償性 flush helper 必須移除"
        )
        assert re.search(r"\.store\.flus" + "h\(", source) is None, (
            "測試不得手動以 store 的提交 API 補償 batch 門檻"
        )
        assert re.search(r"_get_sage_provider\(\).*\.flus" + "h\(", source) is None, (
            "測試不得經 brain 的 provider 存取路徑手動提交"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))