"""
tests/test_epistemic_horizon_eh31.py

EH-3.1 (Fact-Level Assimilation Guard) — 誤標防護 + 跨 session 驗證驗收套件。
工單決策已由 Owner 授權並定稿（IMPLEMENTATION AUTHORIZED）。

背景: EH-3 的 turn-level 粗標會把「今天就是想休息」這類主觀生命狀態句誤標成
assimilated（文本含「就是」→ 整輪全標）。EH-3.1 升級為 Fact-Level 五閘門
（A source / B 定義句法 / C 人稱代名詞主語 / D 主觀情態句法實體 / E 被定義實體
相交），逐 Fact 判定; 未過閘門的 fact 維持預設 lived_experience。

驗收對照（全部在隔離 temp data_root 執行, 0 生產資料污染, 真實走 post_reply_commit）:
  - Test A: 純定義句（"氣炸鍋就是個…箱子。"）→ 氣炸鍋 fact 仍正確打標
            origin=assimilated / horizon_state=aware / learned_at float 非 NULL
  - Test B: 主觀生命狀態句（"今天就是想休息一下。"）→ 不打標:
            維持 lived_experience / aware / learned_at IS NULL
  - Test C: 混合句（"今天就是想休息。氣炸鍋就是個插電加熱的小箱子。"）→ 核心硬斷言:
            主觀 fact 維持 lived_experience; 氣炸鍋 fact 打標 assimilated —
            嚴格禁止 turn-level 整輪全標
  - Test D: 現代原生角色 (agent_akane) + 混合句 → 白名單 bypass 優先:
            所有 fact 維持 lived_experience（連定義句部分也不標）
  - Test E: agent_ram (NO_DIARY, skip_graph=True) → 0 筆寫入, Hook 平穩不拋異常
  - Test F: 跨 session 穩定召回 — Session 1 定義句打標落庫; 新 Provider /
            新 session_id 重載後 _format_horizon_block("agent_rem") 仍含
            氣炸鍋 Idiolect; Session 2 主觀句（"今天就是想吃點炸的。"）萃取
            維持 lived_experience, 且不污染 Session 1 已打標事實

Test count: 6
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.llm.proxy import _format_horizon_block
from src.memory.sage.provider import SAGELiteProvider
from src.paths import data_root, reset_data_root

# ── 工單固定輸入 ─────────────────────────────────────────────

EXPLANATORY_SENTENCE = "氣炸鍋就是個不用添柴或魔石、插電就能把食物烘烤酥脆的箱子。"
SUBJECTIVE_SENTENCE = "今天就是想休息一下。"
# 混合句以換行分隔兩個子句（機械抽取器只在 。！？!?. + 空白／換行處斷句）,
# 確保主觀句與定義句各自萃取出獨立 fact, 才能驗證 Fact-Level 分流。
MIXED_SENTENCE = "今天就是想休息。\n氣炸鍋就是個插電加熱的小箱子。"
SESSION2_SENTENCE = "今天就是想吃點炸的。"

# ── Helpers（隔離 data_root, 風格對齊 EH-3 套件）──────────────


def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provider(tmp_path: Path, profile_id: str, session_id: str = "s1"):
    """Isolated SAGELiteProvider (graph.sqlite 位於 tmp_path/data/memory/<profile>/)."""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["USE_LLM_JUDGE"] = "false"
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id=session_id)
    return prov, soul_dir


def _facts_by_entity(prov: SAGELiteProvider, *entities: str) -> list:
    """跨多個實體搜尋, 依 fact_id 去重。"""
    assert prov._store is not None
    merged: dict[str, object] = {}
    for e in entities:
        for f in prov._store.search_by_entity(e):
            merged[f.fact_id] = f
    return list(merged.values())


async def _commit(prov, sid, user, agent, source_pair=None) -> None:
    await prov.post_reply_commit(
        sid, user, agent,
        source_pair=source_pair,
    )


def _assert_untagged(facts: list, ctx: str) -> None:
    """未打標斷言: 維持 lived_experience / aware / learned_at IS NULL。"""
    assert facts, f"{ctx}: 竟無任何 fact 可驗證（抽取器行為改變?）"
    for f in facts:
        assert f.origin == "lived_experience", (
            f"{ctx}: origin={f.origin!r} != 'lived_experience' "
            f"(fact: {f.subject} {f.predicate} {f.object})"
        )
        assert f.horizon_state == "aware", (
            f"{ctx}: horizon_state={f.horizon_state!r} != 'aware'"
        )
        assert f.learned_at is None, (
            f"{ctx}: learned_at={f.learned_at!r} 非 NULL — 不該被 Gate 放行"
        )


def _assert_tagged(facts: list, ctx: str) -> None:
    """打標斷言: assimilated / aware / learned_at float 非 NULL。"""
    assert facts, f"{ctx}: 竟無任何 fact 可驗證（抽取器行為改變?）"
    for f in facts:
        assert f.origin == "assimilated", (
            f"{ctx}: origin={f.origin!r} != 'assimilated' "
            f"(fact: {f.subject} {f.predicate} {f.object})"
        )
        assert f.horizon_state == "aware", (
            f"{ctx}: horizon_state={f.horizon_state!r} != 'aware'"
        )
        assert isinstance(f.learned_at, float) and f.learned_at > 0, (
            f"{ctx}: learned_at={f.learned_at!r} 非正 float（契約: Unix timestamp）"
        )


# ───────────────────────────────────────────────────────────
# Test A: 純定義句仍正確打標（Fact-Level 閘門不擋真定義）
# ───────────────────────────────────────────────────────────


class TestA_DefinitionStillTagged:
    def test_a_definition_still_tagged(self, tmp_path):
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Rem nods softly.",
                source_pair="bryan:agent_rem",
            ))
            _assert_tagged(_facts_by_entity(prov, "氣炸鍋"), "Test A 定義句")
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test B: 主觀生命狀態句不誤標（就是想 → 維持 lived_experience）
# ───────────────────────────────────────────────────────────


class TestB_SubjectiveSentenceNotTagged:
    def test_b_subjective_sentence_not_tagged(self, tmp_path):
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", SUBJECTIVE_SENTENCE, "Rem tilts her head.",
                source_pair="bryan:agent_rem",
            ))
            _assert_untagged(_facts_by_entity(prov, "今天", "休息"), "Test B 主觀句")
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test C（核心硬斷言）: 混合句 — 主觀 fact 不標 + 定義 fact 標
# ───────────────────────────────────────────────────────────


class TestC_MixedSentenceFactLevel:
    def test_c_mixed_sentence_fact_level(self, tmp_path):
        """同一輪內「就是想」主觀句與「就是個」定義句共存:
        主觀 fact 維持 lived_experience, 氣炸鍋 fact 打標 —
        嚴格禁止 turn-level 整輪全標（EH-3 會把兩者都誤標）。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", MIXED_SENTENCE, "Rem nods softly.",
                source_pair="bryan:agent_rem",
            ))
            subjective = _facts_by_entity(prov, "今天", "休息")
            air_fryer = _facts_by_entity(prov, "氣炸鍋")
            _assert_untagged(subjective, "Test C 主觀句半")
            _assert_tagged(air_fryer, "Test C 定義句半")
            # 核心: 若不是 Fact-Level（turn-level 粗標）, 主觀 fact 也會被誤標
            tagged_ids = {f.fact_id for f in air_fryer}
            for f in subjective:
                assert f.fact_id not in tagged_ids, (
                    f"turn-level 誤標殘留: 主觀 fact 也被打標 "
                    f"(fact: {f.subject} {f.predicate} {f.object})"
                )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test D: 現代原生角色 Bypass（白名單優先於 Fact-Level 閘門）
# ───────────────────────────────────────────────────────────


class TestD_ModernNativeBypass:
    def test_d_modern_native_bypass(self, tmp_path):
        """agent_akane（MODERN_NATIVE_AGENTS）+ 混合句 → 全部 fact 維持
        lived_experience（白名單 bypass 優先 — 連定義句部分也不標）。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_akane")
        try:
            asyncio.run(_commit(
                prov, "s1", MIXED_SENTENCE, "Akane nods softly.",
                source_pair="bryan:agent_akane",
            ))
            _assert_untagged(_facts_by_entity(prov, "今天", "休息", "氣炸鍋"),
                             "Test D 白名單")
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test E: Ram No-Diary 相容性（skip_graph=True, 0 寫入, 平穩）
# ───────────────────────────────────────────────────────────


class TestE_RamNoDiaryCompat:
    def test_e_ram_no_diary_compat(self, tmp_path):
        """agent_ram（NO_DIARY → skip_graph=True 分支）: 寫入 0 筆,
        Hook 不拋出異常, post_reply_commit 平穩執行。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_ram")
        try:
            # 不拋異常即通過（Hook 對 0 fact_ids 早退）
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Ram nods softly.",
                source_pair="bryan:agent_ram",
            ))
            assert prov._store is not None
            n_graph = int(prov._store.stats()["total_facts"])
            assert n_graph == 0, (
                f"agent_ram 竟寫了 graph: total_facts={n_graph} "
                f"（no-diary 分支應維持 skip_graph=True）"
            )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test F: 跨 session 穩定召回（重載 Provider 後 Idiolect 仍在;
#         Session 2 主觀句不打標、不污染已打標事實）
# ───────────────────────────────────────────────────────────


class TestF_CrossSessionStability:
    def test_f_cross_session_stability(self, tmp_path):
        prov1, _soul_dir = _make_provider(tmp_path, "agent_rem", session_id="s1")
        try:
            # Session 1: 定義句 → 打標落庫
            asyncio.run(_commit(
                prov1, "s1", EXPLANATORY_SENTENCE, "Rem nods softly.",
                source_pair="bryan:agent_rem",
            ))
            _assert_tagged(_facts_by_entity(prov1, "氣炸鍋"), "Test F Session 1")
            prov1.shutdown()
            prov1 = None  # 完全關閉, 模擬跨 session 重載

            # Session 2: 新 Provider + 新 session_id, 重載同一 graph.sqlite
            prov2, _soul_dir2 = _make_provider(tmp_path, "agent_rem", session_id="s2")
            try:
                # 跨 session 穩定召回: 讀側 Horizon Block 仍含氣炸鍋 Idiolect
                block = _format_horizon_block("agent_rem")
                assert "Idiolect" in block, (
                    f"Horizon Block 缺 Idiolect 放行清單: {block!r}"
                )
                assert "氣炸鍋" in block, (
                    f"Session 2 重載後 Idiolect 丟失（跨 session 召回不穩）: {block!r}"
                )
                # Session 2: 主觀句 → 不打標（限定 session_id="s2" 的事實,
                # 避免「炸」誤命中 Session 1 的氣炸鍋事實）
                asyncio.run(_commit(
                    prov2, "s2", SESSION2_SENTENCE, "Rem smiles.",
                    source_pair="bryan:agent_rem",
                ))
                s2_facts = [
                    f for f in _facts_by_entity(prov2, "想吃", "點炸")
                    if f.session_id == "s2"
                ]
                _assert_untagged(s2_facts, "Test F Session 2 主觀句")
                # Session 1 已打標事實不受 Session 2 污染
                _assert_tagged(_facts_by_entity(prov2, "氣炸鍋"),
                               "Test F Session 1 保留")
            finally:
                _restore_data_root()
                prov2.shutdown()
        finally:
            if prov1 is not None:
                _restore_data_root()
                prov1.shutdown()