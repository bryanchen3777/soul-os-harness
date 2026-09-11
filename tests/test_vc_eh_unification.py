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

驗證對象（全部在隔離 temp data_root 執行，0 生產資料污染）:
  - Test A : 生活記憶跨連線立即可見（核心斷言）— VC 寫入普通生活句 → 等待背景
             task 結束 → 文字端獨立 MemoryReader + source_pair_filter 立即檢索到
             「散步」（嚴禁任何手動提交流程）。
  - Test B : 測試潔淨度 — 測試檔案不含 VC-UNIFY-1.1 時期遺留的補償性私人
             flush helper 呼叫（1.2 已由背景 _commit 內自動 flush 取代）。
  - Test C : 定義句功能保留 — EH-3.1 打標（assimilated / aware / learned_at
             float）維持，文字端 Horizon Block 可見 Idiolect。
  - Test D : Barge-in 邊界維持 — 中斷回合 0 graph 寫入且 0 flush（spy 斷言）。
  - Test E : 開關防護 — sage_write 關閉 → 0 task / 0 provider 初始化 / 0 write。
  - Test F : 現代角色 Bypass — Akane / Mai 維持現代原生行為。

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
LIFE_SENTENCE = "我今天下午去公園散步，看到花開了。"
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
# Test A（核心）：生活記憶跨連線立即可見（VC-UNIFY-1.2）
# ───────────────────────────────────────────────────────────


class Test12A_LifeMemoryImmediatelyVisibleCrossConnection:
    def test_a_voice_life_memory_visible_to_text_reader_without_manual_flush(
        self, isolated_data_root, tmp_path
    ):
        """核心斷言：VC 寫入普通生活句（非定義句 → 不打標 → 受 batch 門檻）→
        等待背景 task 結束 → 文字端獨立 MemoryReader + source_pair_filter
        **立即**檢索到「散步」（全程嚴禁任何手動提交流程）。"""
        brain = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(brain, LIFE_SENTENCE, "散步真好，看到花很開心。", "vc_12a")

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
# Test B：測試潔淨度檢查（VC-UNIFY-1.2）
# ───────────────────────────────────────────────────────────


class Test12B_TestHygiene_NoCompensatoryFlushHelper:
    def test_b_no_private_compensatory_flush_call(self):
        """靜態檢查：測試檔案不得含 VC-UNIFY-1.1 時期遺留的補償性私人
        flush helper（已被 1.2 背景 _commit 內的自動 flush 取代）。

        模式字符串以拼接避開自指（檢查器不得匹配到檢查器自身）。
        """
        source = Path(__file__).read_text(encoding="utf-8")

        forbidden_helper = "_flush_brain_" + "store"
        assert forbidden_helper not in source, (
            "VC-UNIFY-1.1 時期遺留的補償性 flush helper 必須移除"
        )
        assert re.search(r"\.store\.flush\(", source) is None, (
            "測試不得手動以 store 的提交 API 補償 batch 門檻"
        )
        assert re.search(r"_get_sage_provider\(\).*\.flush\(", source) is None, (
            "測試不得經 brain 的 provider 存取路徑手動提交"
        )


# ───────────────────────────────────────────────────────────
# Test C：定義句功能保留（EH-3.1 + Horizon Block）
# ───────────────────────────────────────────────────────────


class Test12C_DefinitionalSentencePreserved:
    def test_c_definitional_still_marks_dimensions_and_horizon(self, isolated_data_root, tmp_path):
        """定義句仍正常標記 assimilated / aware / learned_at float，
        且文字端 Horizon Block 可見 Idiolect。"""
        brain = _voice_brain(AGENT_REM, sage_write=True)
        _run_voice_commit(brain, EXPLANATORY_SENTENCE, "明白了。氣炸鍋是主人教的用法。", "vc_12c")

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

        # 文字端 Horizon Block 可見 Idiolect（跨模態無縫默契）
        block = _format_horizon_block(AGENT_REM)
        assert "[HORIZON-認知地平線]" in block
        assert "氣炸鍋" in block, "文字端必須能讀到語音端教導的 Idiolect（氣炸鍋）"


# ───────────────────────────────────────────────────────────
# Test D：Barge-in 邊界維持（0 write + 0 flush）
# ───────────────────────────────────────────────────────────


class Test12D_BargeInBoundaryPreserved:
    def test_d_barge_in_turn_zero_write_zero_flush(self, isolated_data_root, tmp_path, monkeypatch):
        """被 barge-in 中斷的回合：0 排程、0 graph 寫入、0 flush（spy 斷言）。
        對照完整回合：排程成功、背景 _commit 成功路徑內自動 flush 觸發。"""
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
# Test E：開關防護（0 task / 0 write / 0 flush）
# ───────────────────────────────────────────────────────────


class Test12E_WriteSwitchGuard:
    def test_e_switch_off_zero_task_zero_provider_zero_write(
        self, isolated_data_root, tmp_path, monkeypatch
    ):
        """寫入開關關閉時：0 task、0 provider 初始化、0 write（故 0 flush）。"""
        brain = _voice_brain(AGENT_REM, sage_write=False)

        def _boom(*args, **kwargs):
            raise AssertionError("開關關閉時不得初始化 SAGE provider")

        monkeypatch.setattr(brain, "_get_sage_provider", _boom)

        task = brain.schedule_sage_commit(LIFE_SENTENCE, "收到。")
        assert task is None, "開關關閉必須 0 task"
        assert _graph_facts_about(AGENT_REM, "散步") == [], "開關關閉必須 0 write"


# ───────────────────────────────────────────────────────────
# Test F：現代角色 Bypass（Akane / Mai）
# ───────────────────────────────────────────────────────────


class Test12F_ModernNativeBypass:
    def test_f_akane_mai_modern_native_behavior(self, isolated_data_root, tmp_path):
        """現代原生角色（agent_akane / agent_mai）：語音端 Horizon Gate fail-silent
        bypass 維持 + Horizon Block 為空（VC-2.4 輸出守門不變）。"""
        for agent in ("agent_akane", "agent_mai"):
            brain = _voice_brain(agent, sage_write=False)
            messages = brain._build_messages("今天好累")
            assert "[HORIZON-認知地平線]" not in messages[0]["content"], (
                f"{agent} 不得注入 Horizon（現代原生 bypass 維持）"
            )
            assert format_voice_horizon_block(agent) == "", (
                f"{agent} Horizon Block 必須為空"
            )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))