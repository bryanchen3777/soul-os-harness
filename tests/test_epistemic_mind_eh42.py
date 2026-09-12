"""
tests/test_epistemic_mind_eh42.py

EH-4.2 — SAGE 五元組概念同化 ＆ UR-2 第一輪靜默邊界（Phase 2）驗收套件。

契約：``docs/EH-4-EPISTEMIC-MIND-CONTRACT.md``
  §4.1 立體概念節點（五元組 / eh4_ 保留命名空間）
  §4.2 非同步寫回 + 安全熔接 SF-1~SF-7（I1 冪等 / I2 每實體 ≤4 列 / I5 fail-silent）
  §4.3 L4-D3 投影規則（只投影 eh4_idiolect + eh4_mental_model）
  §2.2 S3 已內化鎖定（讀側二次遭遇 0 差量對撞）

測試項目（工單 Test A~D）:
  A UR-2 靜默修復驗證（核心）: 「我買了一台氣炸鍋。」→ None（0 隱喻 / 0 品名回退 /
    0 內化或衍生 Fact 寫入）
  B 定義句五元組同化寫回驗證: 真實走 post_reply_commit（0 LLM Judge）→ 4 條 eh4_
    衍生列 + 槽位 1 既有列；安全直覺熔接進 mental_model；未帶定義之購買句 0 寫入；
    I1 冪等
  C 二次遭遇鎖定驗證（核心）: 已同化該實體後 appraise() → None（差量段空），
    但 Horizon Block 仍有 Idiolect 投影（mental_model + idiolect；raw safety/duty 不投影）
  D 防回歸全套: L4-D3 投影規則單元斷言 + 既有三套件（eh41 / eh31 / clients）全綠

隔離：全程 tmp_path（SOUL_OS_DATA_DIR）→ 0 生產資料庫污染、0 服務重啟。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life.epistemic_appraisal import (  # noqa: E402
    AppraisalInput,
    appraise,
    clear_pack_cache,
    format_epistemic_horizon_delta,
)
from src.llm.proxy import _format_horizon_block  # noqa: E402
from src.memory.sage.provider import SAGELiteProvider  # noqa: E402
from src.paths import data_root, reset_data_root  # noqa: E402

# ── 工單固定輸入 ─────────────────────────────────────────────

AGENT_REM = "agent_rem"
#: 未經特徵描述或定義的純購買句（UR-2 靜默邊界）
PURCHASE_SENTENCE = "我買了一台氣炸鍋。"
#: 主人的定義句（EH-3.1 定義句閘門唯一觸發源）
DEFINITION_SENTENCE = "氣炸鍋就是個插電吹熱風把食物烤熟的箱子，外殼會燙要注意。"
#: 二次遭遇（已同化後的日常使用句）
SECOND_ENCOUNTER = "用氣炸鍋弄點薯條吧。"
#: 二次遭遇（帶次級物理特徵的強變體：若無 S3 鎖定必定觸發 forced_air_heat 差量）
SECOND_ENCOUNTER_WITH_FEATURE = "用氣炸鍋弄點薯條吧，它一直吹熱風"

_EH4_PREDICATES = ("eh4_mental_model", "eh4_safety_rule", "eh4_duty_action", "eh4_idiolect")


# ── Helpers（風格對齊 EH-3.1 套件）────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """隔離 data_root（SAGE graph.sqlite）＋ 關閉 LLM Judge（決定性抽取）。

    S3 已內化鎖定會讀 ``data_root()/memory/<agent>/graph.sqlite`` → 必須隔離，
    否則套件會隨生產資料庫狀態漂移；teardown 一律重置 data_root 快取。
    """
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("USE_LLM_JUDGE", "false")
    clear_pack_cache()
    reset_data_root()
    yield
    clear_pack_cache()
    reset_data_root()


def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provider(tmp_path: Path, profile_id: str = AGENT_REM, session_id: str = "s1"):
    """Isolated SAGELiteProvider（graph.sqlite 位於 tmp_path/data/memory/<profile>/）。"""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "memory" / profile_id
    soul_dir.mkdir(parents=True, exist_ok=True)
    prov = SAGELiteProvider(profile_id=profile_id, data_dir=str(soul_dir))
    prov.initialize(session_id=session_id)
    return prov, soul_dir


async def _commit(prov, user: str, agent: str = "雷姆輕輕點頭。", sid: str = "s1") -> None:
    """真實走 post_reply_commit（MemoryMiddleware 在 AGENT_SPEAK 階段呼叫的路徑）。"""
    await prov.post_reply_commit(sid, user, agent, source_pair=f"bryan:{prov.profile_id}")


def _all_facts(prov: SAGELiteProvider) -> list:
    assert prov._store is not None
    return list(prov._store.get_all_facts())


def _eh4_facts(prov: SAGELiteProvider) -> list:
    """五元組衍生列（``eh4_`` 保留命名空間；契約 §4.1）。"""
    return [f for f in _all_facts(prov) if str(f.predicate).startswith("eh4_")]


# ─────────────────────────────────────────────────────────────────────
# Test A — UR-2 靜默修復驗證（核心）
# ─────────────────────────────────────────────────────────────────────


class TestA_UR2Silence:
    def test_a_purchase_sentence_appraises_to_none(self, tmp_path):
        """「我買了一台氣炸鍋。」→ appraise() None ＋ 0 差量字串（0 隱喻/0 品名回退）。"""
        _isolated_data_root(tmp_path)
        for utterance in (PURCHASE_SENTENCE, "我買了一台氣炸鍋", "我入手了一台氣炸鍋"):
            record = appraise(AppraisalInput(agent_id=AGENT_REM, utterance=utterance))
            assert record is None, f"{utterance!r} 竟產生差量: {record}"
            delta = format_epistemic_horizon_delta(AGENT_REM, utterance)
            assert delta == "", f"{utterance!r} 竟注入差量段: {delta!r}"
            assert "[本體參照]" not in delta

    def test_a_purchase_sentence_writes_no_assimilation(self, tmp_path):
        """L2/L4 寫側靜默：0 內化列、0 eh4_ 衍生列（正規抽取列不屬本閘門）。"""
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, PURCHASE_SENTENCE))
            assert _eh4_facts(prov) == []
            assert prov._store.get_idiolect_facts() == []
            assert [f for f in _all_facts(prov) if f.origin == "assimilated"] == []
        finally:
            prov.shutdown()
            _restore_data_root()

    def test_a_secondary_feature_still_routes(self, tmp_path):
        """反向鋼印：句中**有**次級物理特徵詞時，取得句仍正常分流（未誤殺）。"""
        _isolated_data_root(tmp_path)
        record = appraise(
            AppraisalInput(
                agent_id=AGENT_REM,
                utterance="我買了一台冰箱，裡面的菜冰著、放著不壞",
            )
        )
        assert record is not None
        assert record.extraction_ladder == "spoken_acquisition"
        assert record.anchor_class == "cold_preservation"


# ─────────────────────────────────────────────────────────────────────
# Test B — 定義句五元組同化寫回驗證
# ─────────────────────────────────────────────────────────────────────


class TestB_QuintupleWriteBack:
    def test_b_definition_assimilates_quintuple(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, DEFINITION_SENTENCE))
            aware = prov._store.get_idiolect_facts()
            by_pred = {f.predicate: f for f in aware}

            # 槽位 2~5：4 條衍生列（I2：每輪每實體最多 4 條）
            for predicate in _EH4_PREDICATES:
                assert predicate in by_pred, f"缺槽位 {predicate}: {sorted(by_pred)}"
            assert all(by_pred[p].source == "inference" for p in _EH4_PREDICATES)
            # 槽位 1 earth_term：EH-3.1 既有 user 列（現況不動）
            assert any(f.source == "user" for f in aware), "槽位 1（既有 user 列）竟不存在"

            # 契約 §4.1 欄位映射：五元組同 subject、assimilated/aware/learned_at float
            subjects = {f.subject for f in aware}
            assert len(subjects) == 1, subjects
            for fact in aware:
                assert fact.origin == "assimilated", fact
                assert fact.horizon_state == "aware", fact
                assert isinstance(fact.learned_at, float) and fact.learned_at > 0, fact

            # 安全熔接（SF-1/SF-2）：安全直覺進 mental_model 描述句，非命令句
            mental_model = by_pred["eh4_mental_model"].object
            assert "外殼會燙" in mental_model, mental_model
            assert "必須" not in mental_model and "禁止" not in mental_model
            assert by_pred["eh4_safety_rule"].object, "raw safety_rule 列竟為空（SF-5）"
            assert by_pred["eh4_duty_action"].object, "raw duty_action 列竟為空"
            assert by_pred["eh4_idiolect"].object, "idiolect 稱謂竟為空"
        finally:
            prov.shutdown()
            _restore_data_root()

    def test_b_purchase_without_definition_writes_zero(self, tmp_path):
        """未帶定義之購買句 → 0 eh4_ 衍生列、0 內化列（唯一閘門＝定義句閘門）。"""
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, PURCHASE_SENTENCE))
            assert _eh4_facts(prov) == []
            assert prov._store.get_idiolect_facts() == []
        finally:
            prov.shutdown()
            _restore_data_root()

    def test_b_idempotent_on_redefinition(self, tmp_path):
        """I1 冪等：同 (subject, predicate) 已存在 → skip，不重複、不覆寫。"""
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, DEFINITION_SENTENCE))
            first = {f.predicate: f.object for f in _eh4_facts(prov)}
            asyncio.run(_commit(prov, DEFINITION_SENTENCE))
            second = {f.predicate: f.object for f in _eh4_facts(prov)}
            assert len(_eh4_facts(prov)) == 4, _eh4_facts(prov)
            assert first == second
        finally:
            prov.shutdown()
            _restore_data_root()

    def test_b_no_write_for_non_definition_sentence_with_features(self, tmp_path):
        """特徵齊全但**非**定義句（純描述）→ 仍 0 寫入（閘門是定義句，不是特徵詞）。"""
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, "那個箱子一直吹熱風，外殼很燙。"))
            assert _eh4_facts(prov) == []
            assert prov._store.get_idiolect_facts() == []
        finally:
            prov.shutdown()
            _restore_data_root()


# ─────────────────────────────────────────────────────────────────────
# Test C — 二次遭遇鎖定驗證（核心）
# ─────────────────────────────────────────────────────────────────────


class TestC_SecondEncounterLock:
    def test_c_locked_delta_and_idiolect_projection(self, tmp_path):
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, DEFINITION_SENTENCE))
            aware = prov._store.get_idiolect_facts()
            by_pred = {f.predicate: f for f in aware}
            subject = by_pred["eh4_mental_model"].subject

            # 讀側 S3 已內化鎖定：0 差量對撞（含特徵齊全的強變體）
            assert appraise(AppraisalInput(agent_id=AGENT_REM, utterance=SECOND_ENCOUNTER)) is None
            assert (
                appraise(
                    AppraisalInput(
                        agent_id=AGENT_REM, utterance=SECOND_ENCOUNTER_WITH_FEATURE
                    )
                )
                is None
            )
            assert format_epistemic_horizon_delta(AGENT_REM, SECOND_ENCOUNTER_WITH_FEATURE) == ""

            # 對照組（0 內化）：同一強變體在**無** SAGE 內化列的環境仍會產生差量
            other_root = tmp_path / "clean"
            os.environ["SOUL_OS_DATA_DIR"] = str(other_root)
            reset_data_root()
            contrast = format_epistemic_horizon_delta(AGENT_REM, SECOND_ENCOUNTER_WITH_FEATURE)
            assert "[本體參照]" in contrast, "對照組竟無差量 —— 本測試失去區辨力"
            # 回到已同化環境（讀側 Idiolect 來源）
            _isolated_data_root(tmp_path)

            # Horizon Block：差量段為空，但 Idiolect 投影已內化的心智模型與稱謂
            block = _format_horizon_block(AGENT_REM, session_context="", utterance=SECOND_ENCOUNTER)
            assert "[本體參照]" not in block
            assert f"- {subject}（你的理解）：{by_pred['eh4_mental_model'].object}" in block
            assert f"- {subject}：{by_pred['eh4_idiolect'].object}" in block
            # SF-7：安全直覺在二次遭遇仍可見（熔接進 mental_model）
            assert "外殼會燙" in block

            # L4-D3：raw safety_rule / duty_action **不投影**（防偽指令）
            assert f"- {subject}：{by_pred['eh4_safety_rule'].object}" not in block
            assert f"- {subject}：{by_pred['eh4_duty_action'].object}" not in block
            assert by_pred["eh4_safety_rule"].object not in block
        finally:
            prov.shutdown()
            _restore_data_root()


# ─────────────────────────────────────────────────────────────────────
# Test D — 防回歸全套
# ─────────────────────────────────────────────────────────────────────


class TestD_Regression:
    def test_d_l4_d3_projection_rules(self, tmp_path):
        """L4-D3：eh4_idiolect / eh4_mental_model 投影；safety/duty 不投影；既有列照舊。"""
        prov, _ = _make_provider(tmp_path)
        try:
            asyncio.run(_commit(prov, DEFINITION_SENTENCE))
            block = _format_horizon_block(AGENT_REM, session_context="")
            assert "你已理解的默契事物清單（Idiolect）：" in block
            assert "（你的理解）：" in block
            assert "- " in block
            # 無 eh4_ 前綴的既有列（槽位 1）照舊 `- subject predicate object`
            legacy = [f for f in prov._store.get_idiolect_facts() if not str(f.predicate).startswith("eh4_")]
            assert legacy, "缺槽位 1 既有列"
            assert f"- {legacy[0].subject} {legacy[0].predicate} {legacy[0].object}" in block
            # 現代原生角色 D3 bypass 不受影響
            assert _format_horizon_block("agent_akane", session_context="") == ""
        finally:
            prov.shutdown()
            _restore_data_root()

    def test_d_legacy_suites_green(self):
        """既有兩套件全綠（EH-4.1 / EH-3.1）。"""
        repo = Path(__file__).resolve().parent.parent
        for target in (
            "tests/test_epistemic_mind_eh41.py",
            "tests/test_epistemic_horizon_eh31.py",
        ):
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", target, "-q"],
                cwd=str(repo),
                capture_output=True,
                text=True,
            )
            assert proc.returncode == 0, f"{target} 回歸失敗:\n{proc.stdout[-3000:]}"
