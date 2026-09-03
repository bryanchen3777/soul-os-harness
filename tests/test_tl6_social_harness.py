"""
tests/test_tl6_social_harness.py — TL-6 Multi-Agent Social Lounge Stability 測試

覆蓋 (工單 TL-6 驗收):
  - 多 Agent 客廳情境跑通:
      * 瑠夏客廳晨間打招呼 (公開廣播)
      * Bryan 客廳發言 (無風暴)
      * Bryan 與瑠夏 1:1 私聊 (防線 2 隱私隔離)
      * 茜客廳環境觀察分享 (防線 3 身份防污染)
      * 深夜安靜作息 (克制留白)
      * 5 筆連續社交脈衝 (Middleware Top-N 預算約束)
      * 深度記憶與升華隔離審計 (0 他者內化)
  - 四大不變量:
      * Anti-Storm Rate = 100% (無自激連鎖搶話)
      * Identity Quarantine = 100% (他者行為 0 內化為自傳記憶或性格信念)
      * Privacy Gate = 100% (私聊 0 洩漏至客廳)
      * Ambient Salience = PASS (背景感知注入與反框架提示存在)
  - D2 決定性: 3 次 runs 軌跡一致
  - 零生產污染: 隔離 data_root, production data_root 0 diff
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.tl6 import (
    SCENARIO_BURST_EXCITATION,
    SCENARIO_LATE_NIGHT_QUIET,
    SCENARIO_MEMORY_QUARANTINE_AUDIT,
    SCENARIO_OBSERVATION_DIFFUSION,
    SCENARIO_OWNER_LOUNGE,
    SCENARIO_PRIVATE_DM,
    SCENARIO_PUBLIC_GREETING,
    TL6_AGENTS,
    TL6Runner,
    build_tl6_script,
)
from src.social import (
    EXTERNAL_OTHER_ACTION,
    IdentityFirewall,
    IdentityVerdict,
    SocialEventProducerGate,
)


def _restore_env() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]


@pytest.fixture(autouse=True)
def clean_environment():
    _restore_env()
    yield
    _restore_env()


def test_build_tl6_script():
    """驗證 TL-6 劇本結構與情境完整性。"""
    script = build_tl6_script(seed=42)
    assert len(script) == 7

    scenarios = [t.scenario for t in script]
    assert SCENARIO_PUBLIC_GREETING in scenarios
    assert SCENARIO_OWNER_LOUNGE in scenarios
    assert SCENARIO_PRIVATE_DM in scenarios
    assert SCENARIO_OBSERVATION_DIFFUSION in scenarios
    assert SCENARIO_LATE_NIGHT_QUIET in scenarios
    assert SCENARIO_BURST_EXCITATION in scenarios
    assert SCENARIO_MEMORY_QUARANTINE_AUDIT in scenarios

    # 確定性驗證: 相同 seed 產出完全相同劇本
    script_2 = build_tl6_script(seed=42)
    assert [t.tick_id for t in script] == [t.tick_id for t in script_2]


def test_tl6_identity_firewall_classification():
    """驗證防線 3: 各 Agent 對他者與自我的識別判定。"""
    fw_yua = IdentityFirewall(current_agent_id="agent_yua")
    fw_ruka = IdentityFirewall(current_agent_id="agent_ruka")

    # Yua 視角: Ruka 是他者, Yua 是自己
    assert fw_yua.classify("agent_ruka") == IdentityVerdict.EXTERNAL_OTHER_ACTION
    assert fw_yua.verify_internalizable("agent_ruka") is False
    assert fw_yua.classify("agent_yua") == IdentityVerdict.SELF_ACTION
    assert fw_yua.verify_internalizable("agent_yua") is True

    # Ruka 視角: Ruka 是自己, Yua 是他者
    assert fw_ruka.classify("agent_ruka") == IdentityVerdict.SELF_ACTION
    assert fw_ruka.verify_internalizable("agent_ruka") is True
    assert fw_ruka.classify("agent_yua") == IdentityVerdict.EXTERNAL_OTHER_ACTION
    assert fw_ruka.verify_internalizable("agent_yua") is False


def test_tl6_privacy_gate_interception():
    """驗證防線 2: 1:1 私聊 DM 嚴格攔截於總線外。"""
    gate = SocialEventProducerGate()

    # 私聊 1:1 攔截
    v_private = gate.evaluate(
        channel_mode="private",
        channel="dm",
    )
    assert v_private.allowed is False

    # 客廳群聊允許
    v_lounge = gate.evaluate(
        channel_mode="group",
        channel="lounge",
    )
    assert v_lounge.allowed is True


def test_tl6_runner_single_run_and_invariants(tmp_path):
    """驗證 TL6Runner 單次執行: 跑通劇本並通過四大核心不變量。"""
    runner = TL6Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_run_single")

    assert result["run_id"] == "test_run_single"
    derived = result["derived"]

    # 四大不變量全綠
    assert derived.anti_storm_passed is True, "Anti-storm failed"
    assert derived.identity_quarantine_passed is True, "Identity quarantine leak detected"
    assert derived.privacy_gate_passed is True, "Privacy gate leak detected"
    assert derived.ambient_salience_passed is True, "Ambient salience failed"
    assert derived.quarantine_leaks == 0
    assert derived.privacy_leaks == 0

    # 驗證產出紀錄檔案
    run_dir = Path(result["run_dir"])
    assert (run_dir / "records" / "ticks.jsonl").exists()
    assert (run_dir / "derived.json").exists()


def test_tl6_runner_series_determinism_and_zero_mutation():
    """驗證 TL6Runner 系列多跑 (3 runs): 決定性與零生產污染。"""
    runner = TL6Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_series(n_runs=3)

    assert result["all_passed"] is True
    assert result["zero_mutation_ok"] is True, f"Production mutation detected: {result['mutation_diff']}"
    assert result["determinism_ok"] is True, "Determinism failed across 3 runs"
    assert len(result["runs"]) == 3
