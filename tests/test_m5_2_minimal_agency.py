"""
tests/test_m5_2_minimal_agency.py — M5.2 Minimal Agency Implementation Tests

Bry 派工 2026-08-08 M5.2:
驗證 M5.1 I-A invariants 全部成立 + 6 行 negative-path matrix。
不修改既有 production / test。0 commit。
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import inspect
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from src.agency import (
    AgencyState,
    Agency,
    AgencyRunResult,
    run_agency,
    EligibilityResult,
    DecisionResult,
    ExecutionResult,
    AgencyTraceEntry,
    check_eligibility,
    make_decision,
    select_action,
    execute_action_stub,
)


# ─── Helpers ───────────────────────────────────────────────


def make_now(seconds_offset: int = 0) -> datetime:
    base = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds_offset)


def make_perception(accepted: bool, priority: int = 0) -> Dict[str, Any]:
    return {
        "agent_id": "agent_test",
        "world_context": "[mock] test world context",
        "world_perception_meta": {"accepted_count": 1 if accepted else 0, "rejected_count": 0 if accepted else 1},
        "accepted": accepted,
        "priority": priority,
    }


# ─── M5.1 Invariant Tests (I-A1 to I-A10) ──────────────────


def test_I_A1_agency_does_not_modify_perception():
    """
    I-A1: Agency 不得修改 perception score。

    驗證: Agency 接收的 perception dict 跟回傳時的 dict 完全一致。
    """
    perception = make_perception(accepted=True, priority=10)
    perception_snapshot = {k: (v if not isinstance(v, dict) else dict(v)) for k, v in perception.items()}
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    # perception 必須完全 unchanged
    assert perception == perception_snapshot, (
        f"I-A1 violation: agency modified perception\n"
        f"  before: {perception_snapshot}\n"
        f"  after:  {perception}"
    )
    # 確保 score 沒被改
    assert perception["accepted"] == perception_snapshot["accepted"]
    assert perception["priority"] == perception_snapshot["priority"]


def test_I_A2_priority_does_not_bypass_agency_gate():
    """
    I-A2: priority 不得 bypass agency gate 直接觸發 action。

    兩個 sub-test:
      (a) priority=20 + perception.rejected → NO (priority 不救 rejected)
      (b) priority=0 + perception.accepted → NO (priority=0 不 bypass)
    """
    # (a) priority=20 + rejected
    perception_rej = make_perception(accepted=False, priority=20)
    state = AgencyState()
    result_rej = run_agency(state, perception_rej, make_now())
    assert result_rej.decision.should_act is False
    assert "perception rejected" in result_rej.decision.reason
    # (b) priority=0 + accepted
    perception_zero = make_perception(accepted=True, priority=0)
    state2 = AgencyState()
    result_zero = run_agency(state2, perception_zero, make_now())
    assert result_zero.decision.should_act is False
    assert "no priority signal" in result_zero.decision.reason


def test_I_A5_rejected_perception_does_not_act():
    """I-A5: rejected perception 不得 act (即使 priority=20)。"""
    perception = make_perception(accepted=False, priority=20)
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    assert result.decision.should_act is False
    assert result.execution is None
    assert result.action_type is None
    assert "perception rejected" in result.decision.reason


def test_I_A7_no_decision_no_execution():
    """I-A7: 沒有 explicit decision 不得 execution。"""
    # decision=NO → execution 必須是 None
    perception = make_perception(accepted=False, priority=20)
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    assert result.decision.should_act is False
    assert result.execution is None
    assert result.action_type is None
    # 對照: decision=YES → execution 才有值
    state2 = AgencyState()
    perception_yes = make_perception(accepted=True, priority=10)
    result_yes = run_agency(state2, perception_yes, make_now())
    assert result_yes.decision.should_act is True
    assert result_yes.execution is not None
    assert result_yes.action_type is not None


def test_I_A8_decision_has_reason():
    """I-A8: decision 必須有 reason (不論 yes / no)。"""
    cases = [
        (True, 10),   # accepted + priority
        (True, 0),    # accepted + zero priority
        (False, 20),  # rejected
        (True, -5),   # accepted + negative priority
    ]
    for accepted, priority in cases:
        perception = make_perception(accepted=accepted, priority=priority)
        state = AgencyState()
        result = run_agency(state, perception, make_now())
        assert result.decision.reason, (
            f"I-A8 violation: empty reason for accepted={accepted}, priority={priority}"
        )
        # Eligibility 也必須有 reason
        assert result.eligibility.reason, (
            f"I-A8 violation: empty eligibility reason for accepted={accepted}, priority={priority}"
        )


def test_I_A9_agency_does_not_publish_AGENT_INTENT_PERCEIVED():
    """
    I-A9: Agency 不得偽造 AGENT_INTENT_PERCEIVED。

    驗證: Agency class 沒有任何方法可以 create / publish / modify perception。
    """
    agency_methods = [
        name for name, _ in inspect.getmembers(Agency, predicate=inspect.isfunction)
    ]
    forbidden = [
        "publish_AGENT_INTENT_PERCEIVED",
        "emit_perception",
        "create_perception",
        "modify_perception",
        "set_perception",
        "fake_perception",
    ]
    for fm in forbidden:
        assert fm not in agency_methods, (
            f"I-A9 violation: Agency has forbidden method {fm!r}"
        )
    # 額外: 確認 Agency 模組 source 沒 import eventbus
    from src import agency as agency_module
    src = inspect.getsource(agency_module)
    assert "eventbus" not in src.lower() or "WorldPerceptionMiddleware" in src, (
        "I-A9 violation: agency module references eventbus directly"
    )


def test_I_A10_cooldown_works():
    """
    I-A10: cooldown 必須生效 (Stage 1 action cooldown + Stage 2 decision cooldown)。
    """
    state = AgencyState(action_cooldown_seconds=60, decision_cooldown_seconds=30)
    perception = make_perception(accepted=True, priority=10)

    t0 = make_now(0)
    result1 = run_agency(state, perception, t0)
    assert result1.decision.should_act is True
    assert state.last_action_at == t0

    # 10 秒後: action cooldown 還在 (60s) → Stage 1 NO
    t10 = make_now(10)
    result2 = run_agency(state, perception, t10)
    assert result2.eligibility.eligible is False
    assert "action cooldown" in result2.eligibility.reason
    assert result2.decision.should_act is False

    # 70 秒後: action cooldown 過了 → Stage 1 YES, decision cooldown 也過了 (30s) → Stage 2 YES
    t70 = make_now(70)
    result3 = run_agency(state, perception, t70)
    assert result3.eligibility.eligible is True
    assert result3.decision.should_act is True


# ─── Negative-path matrix tests (6 rows) ──────────────────


def test_matrix_row_1_reject_20_yes_NO():
    """Matrix Row 1: Awareness=reject, Priority=20, Eligibility=yes, Decision=NO, Action=❌"""
    perception = make_perception(accepted=False, priority=20)
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is True
    assert result.decision.should_act is False
    assert result.execution is None
    assert "perception rejected" in result.decision.reason


def test_matrix_row_2_accept_0_yes_NO():
    """Matrix Row 2: Awareness=accept, Priority=0, Eligibility=yes, Decision=NO, Action=❌"""
    perception = make_perception(accepted=True, priority=0)
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is True
    assert result.decision.should_act is False
    assert result.execution is None
    assert "no priority signal" in result.decision.reason


def test_matrix_row_3_accept_20_no_NO():
    """Matrix Row 3: Awareness=accept, Priority=20, Eligibility=no (action cooldown), Decision=NO, Action=❌"""
    state = AgencyState(action_cooldown_seconds=60)
    state.last_action_at = make_now(-10)  # 10s ago, within 60s cooldown
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is False
    assert "action cooldown" in result.eligibility.reason
    assert result.decision.should_act is False
    assert result.execution is None


def test_matrix_row_4_accept_20_yes_NO_decision_cooldown():
    """
    Matrix Row 4: Awareness=accept, Priority=20, Eligibility=yes, Decision=NO (decision cooldown), Action=❌

    Stage 1 過 (eligibility yes), 但 Stage 2 內部 decision cooldown 還在, decision=NO。
    """
    state = AgencyState(decision_cooldown_seconds=30)
    state.last_decision_at = make_now(-10)  # 10s ago, within 30s decision cooldown
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is True
    assert result.decision.should_act is False
    assert "decision cooldown" in result.decision.reason
    assert result.execution is None


def test_matrix_row_5_accept_20_yes_YES():
    """Matrix Row 5: Awareness=accept, Priority=20, Eligibility=yes, Decision=YES, Action=✅"""
    state = AgencyState()
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is True
    assert result.decision.should_act is True
    assert result.decision.decision_type == "speak"
    assert result.action_type == "speak"
    assert result.execution is not None
    assert result.execution.executed is True
    # state updated
    assert state.last_action_at is not None


def test_matrix_row_6_accept_20_yes_cooldown_NO():
    """Matrix Row 6: Awareness=accept, Priority=20, Eligibility=no (cooldown), Decision=NO, Action=❌"""
    state = AgencyState(action_cooldown_seconds=60)
    state.last_action_at = make_now(-30)  # 30s ago, within 60s cooldown
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is False
    assert "action cooldown" in result.eligibility.reason
    assert result.decision.should_act is False
    assert result.execution is None


# ─── Positive path & integration tests ────────────────────


def test_happy_path_full_4_stages():
    """Happy path: 完整 4 個 stage 都通過。"""
    state = AgencyState()
    perception = make_perception(accepted=True, priority=10)
    result = run_agency(state, perception, make_now())

    # Stage 1: eligible
    assert result.eligibility.eligible is True
    assert result.eligibility.reason == "eligible"
    # Stage 2: should act
    assert result.decision.should_act is True
    assert result.decision.reason == "all conditions met"
    assert result.decision.decision_type == "speak"
    # Stage 3: action selected
    assert result.action_type == "speak"
    # Stage 4: executed (stub)
    assert result.execution is not None
    assert result.execution.executed is True
    assert result.execution.action_type == "speak"
    # state updated (last_action_at 設為 now)
    assert state.last_action_at is not None


def test_4_stage_separation_all_in_trace():
    """Happy path: 4 個 stage 都記錄到 trace。"""
    state = AgencyState()
    perception = make_perception(accepted=True, priority=10)
    result = run_agency(state, perception, make_now())
    stages = [e.stage for e in result.trace]
    assert stages == ["eligibility", "decision", "selection", "execution"], (
        f"Expected 4 stages in trace, got {stages}"
    )


def test_trace_skips_stages_when_decision_NO():
    """Negative path: decision=NO 時, Stage 3/4 不在 trace 裡。"""
    perception = make_perception(accepted=False, priority=20)
    state = AgencyState()
    result = run_agency(state, perception, make_now())
    stages = [e.stage for e in result.trace]
    assert "eligibility" in stages
    assert "decision" in stages
    assert "selection" not in stages
    assert "execution" not in stages


def test_4_stages_independently_callable():
    """4 個 stage 函數各自獨立可呼叫 (可分離性)。"""
    state = AgencyState()
    now = make_now()
    # Stage 1
    e = check_eligibility(state, now)
    assert e.eligible is True
    # Stage 2
    perception = make_perception(accepted=True, priority=10)
    d = make_decision(e, perception, state, now)
    assert d.should_act is True
    # Stage 3
    a = select_action(d.decision_type or "")
    assert a == "speak"
    # Stage 4
    ex = execute_action_stub(a)
    assert ex.executed is True


def test_dormant_character_cannot_act():
    """dormant 角色無法 act (Stage 1 拒絕)。"""
    state = AgencyState(is_dormant=True)
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is False
    assert "dormant" in result.eligibility.reason
    assert result.decision.should_act is False
    assert result.execution is None


def test_busy_character_cannot_act():
    """busy 角色無法 act (Stage 1 拒絕)。"""
    state = AgencyState(is_busy=True)
    perception = make_perception(accepted=True, priority=20)
    result = run_agency(state, perception, make_now())
    assert result.eligibility.eligible is False
    assert "busy" in result.eligibility.reason


def test_no_llm_or_scheduler_dependency():
    """M5.2 範圍: agency 不依賴 LLM 或 scheduler。"""
    from src import agency as agency_module
    src = inspect.getsource(agency_module)
    # 移除 docstring 跟註解, 只檢查實際 import / 函式定義
    import re
    code_only = re.sub(r'""".*?"""', '', src, flags=re.DOTALL)
    code_only = re.sub(r'#.*', '', code_only)
    # 不應該有 LLM 相關關鍵字
    for forbidden in ["openai", "anthropic", "llama_cpp", "transformers", "model.generate"]:
        assert forbidden not in code_only.lower(), (
            f"M5.2 violation: agency module references {forbidden!r}"
        )
    # 不應該有 scheduler 相關關鍵字
    for forbidden in ["scheduler", "cron", "asyncio.create_task"]:
        assert forbidden not in code_only.lower(), (
            f"M5.2 violation: agency module references {forbidden!r}"
        )


def test_agency_class_pure_no_side_effects():
    """Agency.run() 是 pure function: 相同 input → 相同 output, 除了 state mutations。"""
    # 兩個新 Agency 用相同 state 初始值
    state1 = AgencyState()
    state2 = AgencyState()
    perception = make_perception(accepted=True, priority=10)
    now = make_now()

    result1 = run_agency(state1, perception, now)
    result2 = run_agency(state2, perception, now)

    # Eligibility 跟 decision 結果應該一致
    assert result1.eligibility.eligible == result2.eligibility.eligible
    assert result1.decision.should_act == result2.decision.should_act
    assert result1.decision.decision_type == result2.decision.decision_type


def test_trace_format_observability():
    """Trace 格式: 每個 entry 有 timestamp / stage / input / output / reason。"""
    state = AgencyState()
    perception = make_perception(accepted=True, priority=10)
    result = run_agency(state, perception, make_now())
    for entry in result.trace:
        assert isinstance(entry, AgencyTraceEntry)
        assert isinstance(entry.timestamp, str)
        assert isinstance(entry.stage, str)
        assert isinstance(entry.input, dict)
        assert isinstance(entry.output, dict)
        assert isinstance(entry.reason, str)
