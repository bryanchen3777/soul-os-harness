"""
tests/test_epistemic_horizon_eh3.py

EH-3 (Write-Side Assimilation Pipeline) — 寫側內化閉環驗收套件。
工單決策已由 Owner 授權並定稿（IMPLEMENTATION AUTHORIZED），本套件驗證
post_reply_commit 後置 Hook 的完整鏈路：對「user 解釋句」萃取的 fact 打上
assimilated / aware / learned_at，讓讀側 Idiolect 檢索（_format_horizon_block）
二次遭遇自然流暢（閉環）。

驗收對照（全部在隔離 temp data_root 執行, 0 生產資料污染, 真實走 post_reply_commit）:
  - Test A : 解釋句（"氣炸鍋就是個…箱子。"）→ 相關 Fact origin=assimilated,
             horizon_state=aware, learned_at 為 float 且非 NULL
  - Test A2: 日常使用句（"我剛用氣炸鍋弄了豆腐。"）→ 不誤標:
             維持 origin=lived_experience、learned_at IS NULL
  - Test B : 現代原生角色 (agent_akane) + 解釋句 → 白名單 bypass:
             所有落庫 Fact 維持 lived_experience、learned_at IS NULL
  - Test C : 讀寫閉環 — Test A 打標後 _format_horizon_block("agent_rem")
             含 Idiolect 放行清單 + 「氣炸鍋」實體（學會後不再觸發阻力）
  - Test D : agent_ram (NO_DIARY, skip_graph=True) → 0 筆寫入, Hook 平穩不拋異常

Test count: 5
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
DAILY_SENTENCE = "我剛用氣炸鍋弄了豆腐。"

# ── Helpers（隔離 data_root, 風格對齊 MEM-WIRING-1 套件） ──────


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


def _graph_total(prov: SAGELiteProvider) -> int:
    assert prov._store is not None, "provider 未 initialize"
    return int(prov._store.stats()["total_facts"])


async def _commit(prov, sid, user, agent, source_pair=None) -> None:
    await prov.post_reply_commit(
        sid, user, agent,
        source_pair=source_pair,
    )


def _air_fryer_facts(prov: SAGELiteProvider) -> list:
    """本輪寫入中與「氣炸鍋」相關的 fact（subject 或 object 含實體）。"""
    assert prov._store is not None
    return list(prov._store.search_by_entity("氣炸鍋"))


# ───────────────────────────────────────────────────────────
# Test A: 解釋句成功打標（assimilated / aware / learned_at float）
# ───────────────────────────────────────────────────────────


class TestA_ExplanatoryTurnTagged:
    def test_a_explanatory_turn_tagged(self, tmp_path):
        """解釋句 → user 萃取 fact 標 origin=assimilated、horizon_state=aware、
        learned_at 為 float 且非 NULL。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Rem nods softly.",
                source_pair="bryan:agent_rem",
            ))
            facts = _air_fryer_facts(prov)
            assert facts, "解釋句應至少萃取出 1 個含氣炸鍋的 fact"
            for f in facts:
                assert f.origin == "assimilated", (
                    f"fact.origin={f.origin!r} != 'assimilated' "
                    f"(fact: {f.subject} {f.predicate} {f.object})"
                )
                assert f.horizon_state == "aware", (
                    f"fact.horizon_state={f.horizon_state!r} != 'aware'"
                )
                assert f.learned_at is not None, (
                    f"fact.learned_at is None — 應為 Gate 放行時間戳"
                )
                assert isinstance(f.learned_at, float), (
                    f"fact.learned_at={f.learned_at!r} 非 float（契約: Unix timestamp）"
                )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test A2: 日常使用句不誤標（維持 lived_experience / learned_at NULL）
# ───────────────────────────────────────────────────────────


class TestA2_DailyUseNotTagged:
    def test_a2_daily_use_not_tagged(self, tmp_path):
        """日常使用句（無定義詞）→ 不打標：維持 lived_experience、learned_at NULL。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", DAILY_SENTENCE, "Rem smiles.",
                source_pair="bryan:agent_rem",
            ))
            facts = _air_fryer_facts(prov)
            if not facts:
                pytest.skip("日常句未萃取出含氣炸鍋的 fact（抽取器行為）, 本斷言不適用")
            for f in facts:
                assert f.origin == "lived_experience", (
                    f"日常使用句竟被打標: fact.origin={f.origin!r} "
                    f"(fact: {f.subject} {f.predicate} {f.object})"
                )
                assert f.learned_at is None, (
                    f"日常使用句竟被標 learned_at: {f.learned_at!r}"
                )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test B: 現代原生角色 Bypass（白名單直接引用 horizon.py）
# ───────────────────────────────────────────────────────────


class TestB_ModernNativeBypass:
    def test_b_modern_native_bypass(self, tmp_path):
        """agent_akane（MODERN_NATIVE_AGENTS）+ 相同解釋句 → 不打標：
        所有落庫 Fact 維持 lived_experience、learned_at NULL。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_akane")
        try:
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Akane nods softly.",
                source_pair="bryan:agent_akane",
            ))
            facts = _air_fryer_facts(prov)
            assert facts, "agent_akane 應正常落庫（白名單只 bypass 打標, 不擋寫入）"
            for f in facts:
                assert f.origin == "lived_experience", (
                    f"現代原生角色竟被打標 assimilated: origin={f.origin!r}"
                )
                assert f.learned_at is None, (
                    f"現代原生角色竟被標 learned_at: {f.learned_at!r}"
                )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test C: 讀寫閉環（Test A 打標 → Idiolect 進 Horizon Block）
# ───────────────────────────────────────────────────────────


class TestC_ReadWriteLoop:
    def test_c_idiolect_in_horizon_block_after_tagging(self, tmp_path):
        """Test A 同款解釋句落庫後, _format_horizon_block('agent_rem') 應包含
        Idiolect 放行清單且含「氣炸鍋」實體 — 驗證學會後不再觸發阻力的閉環。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_rem")
        try:
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Rem nods softly.",
                source_pair="bryan:agent_rem",
            ))
            facts = _air_fryer_facts(prov)
            assert facts, "前置: 解釋句應已萃取出氣炸鍋 fact"
            assert facts[0].origin == "assimilated", "前置: Test A 打標應已生效"

            block = _format_horizon_block("agent_rem")
            assert "Idiolect" in block, (
                f"Horizon Block 缺 Idiolect 放行清單: {block!r}"
            )
            assert "氣炸鍋" in block, (
                f"Horizon Block 缺氣炸鍋實體（內化未進讀側）: {block!r}"
            )
        finally:
            _restore_data_root()
            prov.shutdown()


# ───────────────────────────────────────────────────────────
# Test D: Ram No-Diary 相容性（skip_graph=True, 0 寫入, 平穩）
# ───────────────────────────────────────────────────────────


class TestD_RamNoDiaryCompat:
    def test_d_ram_no_diary_compat(self, tmp_path):
        """agent_ram（NO_DIARY → skip_graph=True 分支）: 寫入 0 筆,
        Hook 不拋出異常, post_reply_commit 平穩執行。"""
        prov, _soul_dir = _make_provider(tmp_path, "agent_ram")
        try:
            # 不拋異常即通過（Hook 對 0 fact_ids 早退）
            asyncio.run(_commit(
                prov, "s1", EXPLANATORY_SENTENCE, "Ram nods softly.",
                source_pair="bryan:agent_ram",
            ))
            n_graph = _graph_total(prov)
            assert n_graph == 0, (
                f"agent_ram 竟寫了 graph: total_facts={n_graph} "
                f"（no-diary 分支應維持 skip_graph=True）"
            )
        finally:
            _restore_data_root()
            prov.shutdown()