# tests/soul/test_life_thread_dissolution_m2.py
# LIFE-THREAD-M2 — 蔡戈尼記憶溶解管線（純決策層）測試矩陣 T1~T18。
#
# 受測模組（唯讀）：`src/soul/life_thread_dissolution.py`
# 模組為 pure library（0 I/O / 0 LLM / 0 定時器 / 0 `src.*` import），
# 因此本檔**不讀寫 `data/**`**、不啟停任何服務、不動 `src/**`。
#
# 慣例對齊 `tests/soul/test_life_thread_wake_gate_m3.py`：`tests/soul/` 無 `__init__.py`，
# 本檔自行 `sys.path.insert(0, REPO_ROOT)`。
from __future__ import annotations

import ast
import copy
import inspect
import logging
import re
import sys
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.soul.life_thread_dissolution import (  # noqa: E402
    DEFAULT_MAX_ACTIVE_DURATION_DAYS,
    DEFAULT_STALE_CHECK_THRESHOLD_DAYS,
    DISSOLVE_MAX_PER_DAY,
    SECONDS_PER_DAY,
    SKIPPED_ALREADY_DISSOLVED,
    STATUS_VALUES,
    TERMINAL_STATUSES,
    DissolutionEvaluation,
    DissolutionReason,
    ThreadStatus,
    count_dissolved_on_date,
    evaluate_batch_dissolution,
    evaluate_thread_dissolution,
    remaining_daily_dissolution_budget,
)
from src.soul.life_threads import (  # noqa: E402  （M1 為真相來源；測試可讀，生產路徑不得 import）
    STATUS_VALUES as M1_STATUS_VALUES,
    TERMINAL_STATUSES as M1_TERMINAL_STATUSES,
)

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_dissolution.py"
MODULE_SOURCE = MODULE_PATH.read_text(encoding="utf-8")
MODULE_TREE = ast.parse(MODULE_SOURCE)
LOGGER_NAME = "soul_os.soul.life_thread_dissolution"

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = T0 + timedelta(days=30)

#: 契約 §3.2 的固定輸出契約尾綴（逐字；與模組常數獨立宣告，避免同源錯誤）。
_OUTPUT_CONTRACT = (
    '請只回 JSON：{"dissolution": "<第一人稱 1~3 句、<=400 字元>", '
    '"meaning_kind": "competence|connection|loss|relief|discovery|none"}'
)

_WARNING_FIELD_RE = re.compile(r"score|weight|confidence|probab", re.IGNORECASE)
_ALLOWED_IMPORT_ROOTS = frozenset(
    {"__future__", "logging", "enum", "dataclasses", "datetime", "typing", "collections"}
)


# ──────────────────────────────────────────────────────────────
# 共用建構器
# ──────────────────────────────────────────────────────────────


def _thread(**over: object) -> dict:
    """M1 fold 後的真實欄位（13 欄，逐字；**不含** agent_id）。"""
    base = {
        "thread_id": "t1",
        "event_seq": 1,
        "event_type": "created",
        "origin_type": "goal_driven",
        "status": "active",
        "title": "學吉他",
        "narrative_content": "每天練 20 分鐘",
        "created_at": T0.isoformat(),
        "updated_at": T0.isoformat(),
        "check_after_ts": None,
        "share_target": "none",
        "dissolved_at": None,
        "sage_fact_id": None,
    }
    base.update(over)
    return base


def _thread_without(*keys: str, **over: object) -> dict:
    t = _thread(**over)
    for key in keys:
        t.pop(key, None)
    return t


def _thread_at(created: timedelta, updated: timedelta, **over: object) -> dict:
    return _thread(
        created_at=(T0 + created).isoformat(),
        updated_at=(T0 + updated).isoformat(),
        **over,
    )


# ──────────────────────────────────────────────────────────────
# T1 介面釘死
# ──────────────────────────────────────────────────────────────


def test_signature_parameter_names_and_order() -> None:
    params = inspect.signature(evaluate_thread_dissolution).parameters
    assert list(params) == [
        "thread",
        "current_time",
        "max_active_duration_days",
        "stale_check_threshold_days",
        "allow_soft_archive",
        "agent_id",
        "checkpoints_exhausted",
        "terminal_reason",
    ]


def test_signature_defaults_exact() -> None:
    params = inspect.signature(evaluate_thread_dissolution).parameters
    assert params["thread"].default is inspect.Parameter.empty
    assert params["current_time"].default is inspect.Parameter.empty
    assert params["max_active_duration_days"].default == 14
    assert params["stale_check_threshold_days"].default == 7
    assert params["allow_soft_archive"].default is False
    assert params["agent_id"].default is None
    assert params["checkpoints_exhausted"].default is False
    assert params["terminal_reason"].default is None


def test_allow_soft_archive_default_is_false() -> None:
    """🔴 工單修正 #1：`allow_soft_archive` **預設 False**（active→dormant 是生產存活性政策）。"""
    default = inspect.signature(evaluate_thread_dissolution).parameters[
        "allow_soft_archive"
    ].default
    assert default is False


def test_keyword_only_tail_parameters() -> None:
    params = inspect.signature(evaluate_thread_dissolution).parameters
    for name in ("max_active_duration_days", "stale_check_threshold_days", "allow_soft_archive"):
        assert params[name].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("agent_id", "checkpoints_exhausted", "terminal_reason"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_status_values_identical_to_m1() -> None:
    assert STATUS_VALUES == M1_STATUS_VALUES
    assert STATUS_VALUES == ("active", "dormant", "completed", "abandoned")


def test_terminal_statuses_identical_to_m1() -> None:
    assert TERMINAL_STATUSES == M1_TERMINAL_STATUSES
    assert TERMINAL_STATUSES == ("completed", "abandoned")


def test_thread_status_enum_matches_status_values() -> None:
    assert tuple(member.value for member in ThreadStatus) == STATUS_VALUES


def test_dissolution_reason_six_values() -> None:
    assert tuple(member.value for member in DissolutionReason) == (
        "goal_achieved",
        "checkpoints_exhausted",
        "time_horizon_exceeded",
        "manual_close",
        "stale_no_progress",
        "none",
    )


def test_dissolve_max_per_day_is_three() -> None:
    assert DISSOLVE_MAX_PER_DAY == 3


def test_time_constants_are_integers() -> None:
    assert SECONDS_PER_DAY == 86400
    assert isinstance(SECONDS_PER_DAY, int) and not isinstance(SECONDS_PER_DAY, bool)


def test_default_threshold_constants() -> None:
    assert DEFAULT_MAX_ACTIVE_DURATION_DAYS == 14
    assert DEFAULT_STALE_CHECK_THRESHOLD_DAYS == 7


def test_evaluation_is_frozen_dataclass() -> None:
    assert is_dataclass(DissolutionEvaluation)
    assert [f.name for f in fields(DissolutionEvaluation)] == [
        "thread_id",
        "target_status",
        "reason",
        "should_mutate",
        "reflection_prompt",
        "extra_metadata",
    ]
    evaluation = DissolutionEvaluation(
        "t1", ThreadStatus.ACTIVE, DissolutionReason.NONE, False
    )
    with pytest.raises(FrozenInstanceError):
        evaluation.reason = DissolutionReason.NONE  # type: ignore[misc]


def test_evaluation_optional_fields_default_none() -> None:
    evaluation = DissolutionEvaluation(
        "t1", ThreadStatus.ACTIVE, DissolutionReason.NONE, False
    )
    assert evaluation.reflection_prompt is None
    assert evaluation.extra_metadata is None


def test_batch_signature_exact() -> None:
    params = inspect.signature(evaluate_batch_dissolution).parameters
    assert list(params) == [
        "active_threads",
        "current_time",
        "max_active_duration_days",
        "stale_check_threshold_days",
        "allow_soft_archive",
    ]
    assert params["allow_soft_archive"].default is False


def test_quota_helper_signatures_exact() -> None:
    assert list(inspect.signature(count_dissolved_on_date).parameters) == [
        "threads",
        "current_time",
    ]
    assert list(inspect.signature(remaining_daily_dissolution_budget).parameters) == [
        "threads",
        "current_time",
    ]


def test_all_exports_declared() -> None:
    module = sys.modules[evaluate_thread_dissolution.__module__]
    for name in (
        "STATUS_VALUES",
        "TERMINAL_STATUSES",
        "DISSOLVE_MAX_PER_DAY",
        "SECONDS_PER_DAY",
        "DEFAULT_MAX_ACTIVE_DURATION_DAYS",
        "DEFAULT_STALE_CHECK_THRESHOLD_DAYS",
        "ThreadStatus",
        "DissolutionReason",
        "DissolutionEvaluation",
        "evaluate_thread_dissolution",
        "evaluate_batch_dissolution",
        "count_dissolved_on_date",
        "remaining_daily_dissolution_budget",
    ):
        assert name in module.__all__
        assert hasattr(module, name)


# ──────────────────────────────────────────────────────────────
# T2 步驟 0 fail-safe 矩陣（全部不得 raise）
# ──────────────────────────────────────────────────────────────

_BAD_DAYS = ({"max_active_duration_days": -1},
             {"max_active_duration_days": True},
             {"max_active_duration_days": "14"},
             {"max_active_duration_days": None},
             {"stale_check_threshold_days": -1},
             {"stale_check_threshold_days": True},
             {"stale_check_threshold_days": "7"},
             {"stale_check_threshold_days": None})

_STEP0_CASES = [
    ("non_mapping_int", lambda: 42, NOW, {}),
    ("non_mapping_none", lambda: None, NOW, {}),
    ("non_mapping_list", lambda: [], NOW, {}),
    ("non_mapping_str", lambda: "t1", NOW, {}),
    ("thread_id_missing", lambda: _thread_without("thread_id"), NOW, {}),
    ("thread_id_empty", lambda: _thread(thread_id=""), NOW, {}),
    ("thread_id_none", lambda: _thread(thread_id=None), NOW, {}),
    ("thread_id_int", lambda: _thread(thread_id=123), NOW, {}),
    ("status_dissolved", lambda: _thread(status="dissolved"), NOW, {}),
    ("status_archived", lambda: _thread(status="archived"), NOW, {}),
    ("status_suspended", lambda: _thread(status="suspended"), NOW, {}),
    ("status_none", lambda: _thread(status=None), NOW, {}),
    ("status_int", lambda: _thread(status=123), NOW, {}),
    ("status_empty", lambda: _thread(status=""), NOW, {}),
    ("created_at_missing", lambda: _thread_without("created_at"), NOW, {}),
    ("created_at_none", lambda: _thread(created_at=None), NOW, {}),
    ("created_at_garbage", lambda: _thread(created_at="not-a-date"), NOW, {}),
    ("created_at_int_bad", lambda: _thread(created_at="2026-13-45"), NOW, {}),
    ("updated_at_missing", lambda: _thread_without("updated_at"), NOW, {}),
    ("updated_at_garbage", lambda: _thread(updated_at="nope"), NOW, {}),
    ("current_time_garbage", _thread, "nope", {}),
    ("current_time_none", _thread, None, {}),
    ("current_time_bool", _thread, True, {}),
    ("current_time_object", _thread, object(), {}),
] + [
    (f"bad_days_{index}", _thread, NOW, kwargs) for index, kwargs in enumerate(_BAD_DAYS)
]


@pytest.mark.parametrize(
    "case_id,factory,now,kwargs", _STEP0_CASES, ids=[case[0] for case in _STEP0_CASES]
)
def test_step0_invalid_matrix_never_raises(case_id, factory, now, kwargs) -> None:
    thread = factory()
    evaluation = evaluate_thread_dissolution(thread, now, **kwargs)
    assert isinstance(evaluation, DissolutionEvaluation)
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.reflection_prompt is None
    assert evaluation.extra_metadata is not None
    assert evaluation.extra_metadata["advisory"] is False
    assert evaluation.extra_metadata["sedimentation_context"] is None
    assert evaluation.extra_metadata["skipped"] is None
    invalid = evaluation.extra_metadata["invalid"]
    assert isinstance(invalid, str) and invalid


def test_step0_non_mapping_yields_empty_thread_id() -> None:
    assert evaluate_thread_dissolution(42, NOW).thread_id == ""


def test_step0_non_string_thread_id_yields_empty_thread_id() -> None:
    assert evaluate_thread_dissolution(_thread(thread_id=123), NOW).thread_id == ""


def test_step0_keeps_thread_id_when_status_invalid() -> None:
    assert evaluate_thread_dissolution(_thread(status="archived"), NOW).thread_id == "t1"


def test_step0_does_not_mutate_input() -> None:
    for thread, kwargs in (
        (_thread(status="archived"), {}),
        (_thread(created_at="bad"), {}),
        (_thread(), {"max_active_duration_days": -1}),
        (_thread(status="completed"), {}),
        (_thread(), {}),
    ):
        before = copy.deepcopy(thread)
        before_repr = repr(thread)
        evaluate_thread_dissolution(thread, NOW, **kwargs)
        assert thread == before
        assert repr(thread) == before_repr


def test_step0_emits_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        evaluate_thread_dissolution(_thread(status="dissolved"), NOW)
    assert any("invalid input" in record.getMessage() for record in caplog.records)


def test_step0_extra_metadata_exact_keys() -> None:
    evaluation = evaluate_thread_dissolution(42, NOW)
    assert set(evaluation.extra_metadata) == {
        "invalid",
        "advisory",
        "sedimentation_context",
        "skipped",
    }


# ──────────────────────────────────────────────────────────────
# T3 時間解析（多表示法 ⇒ 同一時刻結果一致）
# ──────────────────────────────────────────────────────────────

_EPOCH = int(T0.timestamp())
_TIME_REPRESENTATIONS = [
    ("aware_datetime", lambda: datetime(2026, 1, 15, tzinfo=timezone.utc)),
    ("naive_datetime", lambda: datetime(2026, 1, 15)),
    ("epoch_int", lambda: _EPOCH + 14 * SECONDS_PER_DAY),
    ("epoch_float", lambda: float(_EPOCH + 14 * SECONDS_PER_DAY + 1)),
    ("iso_offset", lambda: (T0 + timedelta(days=14, seconds=1)).isoformat()),
    ("iso_z", lambda: (T0 + timedelta(days=14, seconds=1)).isoformat().replace("+00:00", "Z")),
]


@pytest.mark.parametrize(
    "case_id,builder", _TIME_REPRESENTATIONS, ids=[case[0] for case in _TIME_REPRESENTATIONS]
)
def test_time_parsing_accepts_each_representation(case_id, builder) -> None:
    evaluation = evaluate_thread_dissolution(_thread(), builder())
    assert evaluation.reason in (
        DissolutionReason.TIME_HORIZON_EXCEEDED,
        DissolutionReason.NONE,
    )
    assert evaluation.extra_metadata is not None
    assert "invalid" not in evaluation.extra_metadata


def test_equivalent_representations_produce_identical_evaluations() -> None:
    thread = _thread()
    aware = evaluate_thread_dissolution(
        thread, T0 + timedelta(days=14, seconds=1)
    )
    naive = evaluate_thread_dissolution(thread, datetime(2026, 1, 15, 0, 0, 1))
    epoch = evaluate_thread_dissolution(thread, _EPOCH + 14 * SECONDS_PER_DAY + 1)
    iso_z = evaluate_thread_dissolution(
        thread, (T0 + timedelta(days=14, seconds=1)).isoformat().replace("+00:00", "Z")
    )
    assert aware == naive == epoch == iso_z
    assert aware.reason is DissolutionReason.TIME_HORIZON_EXCEEDED
    assert aware.extra_metadata["age_days"] == 14


def test_naive_datetime_treated_as_utc() -> None:
    thread = _thread(created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
    naive_now = datetime(2026, 1, 15, 0, 0, 1)
    assert evaluate_thread_dissolution(thread, naive_now).reason is (
        DissolutionReason.TIME_HORIZON_EXCEEDED
    )


def test_epoch_float_accepted() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), float(_EPOCH) + 14 * 86400 + 1)
    assert evaluation.reason is DissolutionReason.TIME_HORIZON_EXCEEDED


def test_bool_current_time_is_invalid() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), True)
    assert "invalid" in evaluation.extra_metadata
    assert evaluation.should_mutate is False


def test_zulu_iso_with_microseconds_accepted() -> None:
    stamp = (T0 + timedelta(days=15)).isoformat().replace("+00:00", "Z")
    evaluation = evaluate_thread_dissolution(_thread(), stamp)
    assert evaluation.reason is DissolutionReason.TIME_HORIZON_EXCEEDED


# ──────────────────────────────────────────────────────────────
# T4 步驟 1 冪等（§3.3 L1）
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["completed", "abandoned"])
def test_step1_already_dissolved_is_skipped(status) -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status=status, dissolved_at="2026-01-10T00:00:00+00:00"), NOW
    )
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.target_status is ThreadStatus(status)
    assert evaluation.reflection_prompt is None
    assert evaluation.extra_metadata["skipped"] == SKIPPED_ALREADY_DISSOLVED
    assert evaluation.extra_metadata["skipped"] == "already_dissolved"
    assert "invalid" not in evaluation.extra_metadata


@pytest.mark.parametrize("status", ["completed", "abandoned"])
def test_step1_none_dissolved_at_falls_through_to_step2(status) -> None:
    evaluation = evaluate_thread_dissolution(_thread(status=status, dissolved_at=None), NOW)
    assert evaluation.should_mutate is True
    assert evaluation.extra_metadata["skipped"] is None
    assert evaluation.reason is (
        DissolutionReason.GOAL_ACHIEVED
        if status == "completed"
        else DissolutionReason.MANUAL_CLOSE
    )


def test_step1_empty_string_dissolved_at_is_treated_as_dissolved() -> None:
    """`is not None` 口徑：空字串仍非 None ⇒ 跳過（不重問 LLM）。"""
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", dissolved_at=""), NOW
    )
    assert evaluation.should_mutate is False
    assert evaluation.extra_metadata["skipped"] == "already_dissolved"


# ──────────────────────────────────────────────────────────────
# T5 步驟 2 終態溶解（§3.1 唯一觸發點）
# ──────────────────────────────────────────────────────────────


def test_step2_completed_maps_to_goal_achieved() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    assert evaluation.reason is DissolutionReason.GOAL_ACHIEVED
    assert evaluation.target_status is ThreadStatus.COMPLETED
    assert evaluation.should_mutate is True
    assert evaluation.reflection_prompt is not None
    assert evaluation.extra_metadata["sedimentation_context"] is not None
    assert evaluation.extra_metadata["advisory"] is False


def test_step2_abandoned_maps_to_manual_close() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="abandoned"), NOW)
    assert evaluation.reason is DissolutionReason.MANUAL_CLOSE
    assert evaluation.target_status is ThreadStatus.ABANDONED
    assert evaluation.should_mutate is True
    assert evaluation.reflection_prompt is not None


@pytest.mark.parametrize(
    "overriding",
    [
        "goal_achieved",
        "checkpoints_exhausted",
        "time_horizon_exceeded",
        "manual_close",
        "stale_no_progress",
    ],
)
def test_step2_legal_terminal_reason_override(overriding) -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed"), NOW, terminal_reason=overriding
    )
    assert evaluation.reason is DissolutionReason(overriding)
    assert evaluation.target_status is ThreadStatus.COMPLETED


def test_step2_enum_terminal_reason_override() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="abandoned"),
        NOW,
        terminal_reason=DissolutionReason.STALE_NO_PROGRESS,
    )
    assert evaluation.reason is DissolutionReason.STALE_NO_PROGRESS
    assert evaluation.target_status is ThreadStatus.ABANDONED


@pytest.mark.parametrize(
    "bad_reason", ["none", "nonsense", "", 123, DissolutionReason.NONE, True]
)
def test_step2_illegal_terminal_reason_ignored(bad_reason) -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed"), NOW, terminal_reason=bad_reason
    )
    assert evaluation.reason is DissolutionReason.GOAL_ACHIEVED


def test_step2_none_terminal_reason_uses_default() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="abandoned"), NOW, terminal_reason=None
    )
    assert evaluation.reason is DissolutionReason.MANUAL_CLOSE


def test_step2_illegal_terminal_reason_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        evaluate_thread_dissolution(_thread(status="completed"), NOW, terminal_reason="nope")
    assert any("terminal_reason" in record.getMessage() for record in caplog.records)


def test_step2_override_keeps_terminal_target_status() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="abandoned"),
        NOW,
        terminal_reason=DissolutionReason.CHECKPOINTS_EXHAUSTED,
    )
    assert evaluation.target_status is ThreadStatus.ABANDONED
    assert evaluation.extra_metadata["sedimentation_context"]["terminal_status"] == "abandoned"


# ──────────────────────────────────────────────────────────────
# T6 步驟 3 呼叫端顯式宣告檢驗點耗盡
# ──────────────────────────────────────────────────────────────


def test_step3_active_with_exhausted_flag_mutates_to_completed() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="active"), NOW, checkpoints_exhausted=True
    )
    assert evaluation.target_status is ThreadStatus.COMPLETED
    assert evaluation.reason is DissolutionReason.CHECKPOINTS_EXHAUSTED
    assert evaluation.should_mutate is True
    assert evaluation.reflection_prompt is not None
    assert evaluation.extra_metadata["sedimentation_context"]["terminal_status"] == "completed"


def test_step3_dormant_with_flag_is_unchanged() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="dormant"), NOW, checkpoints_exhausted=True
    )
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.should_mutate is False
    assert "invalid" not in evaluation.extra_metadata


def test_step3_default_flag_is_false_and_contract_pure() -> None:
    """預設 False ⇒ 契約 §3.1 純淨：`active` 不因「看起來舊」而被溶解。"""
    evaluation = evaluate_thread_dissolution(_thread(status="active"), T0 + timedelta(days=1))
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.should_mutate is False


def test_step3_terminal_status_wins_over_flag() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed"), NOW, checkpoints_exhausted=True
    )
    assert evaluation.reason is DissolutionReason.GOAL_ACHIEVED


@pytest.mark.parametrize("truthy_non_bool", [1, "true", "yes", None, [], 0])
def test_step3_non_bool_flag_treated_as_false(truthy_non_bool) -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="active"), T0 + timedelta(days=1), checkpoints_exhausted=truthy_non_bool
    )
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE
    assert "invalid" not in evaluation.extra_metadata


def test_step3_non_bool_flag_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        evaluate_thread_dissolution(
            _thread(status="active"), T0 + timedelta(days=1), checkpoints_exhausted=1
        )
    assert any(
        "checkpoints_exhausted" in record.getMessage() for record in caplog.records
    )


# ──────────────────────────────────────────────────────────────
# T7 步驟 4 邊界（-1s / exact / +1s；對「> 改 >=」的突變敏感）
# ──────────────────────────────────────────────────────────────

_BOUNDARY_CASES = [
    # (id, created_delta, updated_delta, now_delta, kwargs, reason, extra_key, extra_value)
    ("age_minus_1s", timedelta(0), timedelta(0), timedelta(days=14, seconds=-1),
     {}, DissolutionReason.NONE, None, None),
    ("age_exact_14d", timedelta(0), timedelta(0), timedelta(days=14),
     {}, DissolutionReason.NONE, None, None),
    ("age_plus_1s", timedelta(0), timedelta(0), timedelta(days=14, seconds=1),
     {}, DissolutionReason.TIME_HORIZON_EXCEEDED, "age_days", 14),
    ("age_plus_2d", timedelta(0), timedelta(0), timedelta(days=16),
     {}, DissolutionReason.TIME_HORIZON_EXCEEDED, "age_days", 16),
    ("idle_minus_1s", timedelta(0), timedelta(seconds=1), timedelta(days=7),
     {}, DissolutionReason.NONE, None, None),
    ("idle_exact_7d", timedelta(0), timedelta(seconds=1), timedelta(days=7, seconds=1),
     {}, DissolutionReason.NONE, None, None),
    ("idle_plus_1s", timedelta(0), timedelta(seconds=1), timedelta(days=7, seconds=2),
     {}, DissolutionReason.STALE_NO_PROGRESS, "idle_days", 7),
    ("idle_plus_3d", timedelta(0), timedelta(seconds=1), timedelta(days=10, seconds=1),
     {}, DissolutionReason.STALE_NO_PROGRESS, "idle_days", 10),
    ("idle_not_evaluated_when_never_updated", timedelta(0), timedelta(0),
     timedelta(days=10), {}, DissolutionReason.NONE, None, None),
    ("custom_max_zero_exact", timedelta(0), timedelta(0), timedelta(0),
     {"max_active_duration_days": 0}, DissolutionReason.NONE, None, None),
    ("custom_max_zero_plus_1s", timedelta(0), timedelta(0), timedelta(seconds=1),
     {"max_active_duration_days": 0}, DissolutionReason.TIME_HORIZON_EXCEEDED, "age_days", 0),
    ("custom_stale_zero_plus_1s", timedelta(0), timedelta(seconds=1),
     timedelta(seconds=2), {"stale_check_threshold_days": 0},
     DissolutionReason.STALE_NO_PROGRESS, "idle_days", 0),
    ("custom_stale_zero_exact", timedelta(0), timedelta(seconds=1),
     timedelta(seconds=1), {"stale_check_threshold_days": 0},
     DissolutionReason.NONE, None, None),
]


@pytest.mark.parametrize(
    "case_id,created_delta,updated_delta,now_delta,kwargs,reason,extra_key,extra_value",
    _BOUNDARY_CASES,
    ids=[case[0] for case in _BOUNDARY_CASES],
)
def test_step4_boundaries_are_strictly_greater(
    case_id, created_delta, updated_delta, now_delta, kwargs, reason, extra_key, extra_value
) -> None:
    thread = _thread_at(created_delta, updated_delta)
    evaluation = evaluate_thread_dissolution(thread, T0 + now_delta, **kwargs)
    assert evaluation.reason is reason
    assert evaluation.should_mutate is False
    assert evaluation.target_status is ThreadStatus.ACTIVE
    if reason is DissolutionReason.NONE:
        assert evaluation.extra_metadata["advisory"] is False
        assert extra_key is None
    else:
        assert evaluation.extra_metadata["advisory"] is True
        assert evaluation.extra_metadata[extra_key] == extra_value


def test_step4_exact_fourteen_days_is_not_exceeded_smoke() -> None:
    """工單指定的邊界 smoke：14 天整不算超限、+1s 才算。"""
    thread = _thread()
    at_exact = evaluate_thread_dissolution(thread, T0 + timedelta(days=14))
    at_plus = evaluate_thread_dissolution(thread, T0 + timedelta(days=14, seconds=1))
    assert at_exact.extra_metadata["advisory"] is False
    assert at_exact.reason is DissolutionReason.NONE
    assert at_plus.reason is DissolutionReason.TIME_HORIZON_EXCEEDED
    assert at_plus.extra_metadata["advisory"] is True


def test_step4_days_are_integer_truncated_not_rounded() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=14, seconds=86399)
    )
    assert evaluation.extra_metadata["age_days"] == 14
    evaluation2 = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=15))
    assert evaluation2.extra_metadata["age_days"] == 15


# ──────────────────────────────────────────────────────────────
# T8 步驟 4 預設：諮詢訊號（不變更）
# ──────────────────────────────────────────────────────────────


def test_step4_default_advisory_for_time_horizon() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=15))
    assert evaluation.should_mutate is False
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.reason is DissolutionReason.TIME_HORIZON_EXCEEDED
    assert evaluation.extra_metadata["advisory"] is True
    assert evaluation.reflection_prompt is None
    assert evaluation.extra_metadata["sedimentation_context"] is None
    assert evaluation.extra_metadata["age_days"] == 15


def test_step4_default_advisory_for_stale() -> None:
    thread = _thread_at(timedelta(0), timedelta(seconds=1))
    evaluation = evaluate_thread_dissolution(thread, T0 + timedelta(days=9))
    assert evaluation.should_mutate is False
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.reason is DissolutionReason.STALE_NO_PROGRESS
    assert evaluation.extra_metadata["advisory"] is True
    assert evaluation.reflection_prompt is None
    # idle = 9 天 - 1 秒 ⇒ .days 截斷為 8（整數天數，非四捨五入）。
    assert evaluation.extra_metadata["idle_days"] == 8


def test_step4_advisory_reason_is_preserved_not_erased() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=20))
    assert evaluation.reason is not DissolutionReason.NONE
    assert evaluation.extra_metadata["skipped"] is None
    assert "invalid" not in evaluation.extra_metadata


def test_step4_advisory_only_carries_applicable_age_key() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=20))
    assert "age_days" in evaluation.extra_metadata
    assert "idle_days" not in evaluation.extra_metadata


def test_step4_advisory_only_carries_applicable_idle_key() -> None:
    thread = _thread_at(timedelta(0), timedelta(seconds=1))
    evaluation = evaluate_thread_dissolution(thread, T0 + timedelta(days=9))
    assert "idle_days" in evaluation.extra_metadata
    assert "age_days" not in evaluation.extra_metadata


# ──────────────────────────────────────────────────────────────
# T9 步驟 4 opt-in：soft archive（M5 政策旋鈕）
# ──────────────────────────────────────────────────────────────


def test_step4_soft_archive_opt_in_maps_to_dormant() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=15), allow_soft_archive=True
    )
    assert evaluation.target_status is ThreadStatus.DORMANT
    assert evaluation.should_mutate is True
    assert evaluation.reason is DissolutionReason.TIME_HORIZON_EXCEEDED
    assert evaluation.reflection_prompt is not None
    assert evaluation.extra_metadata["advisory"] is True
    assert evaluation.extra_metadata["sedimentation_context"]["terminal_status"] == "dormant"
    assert evaluation.extra_metadata["age_days"] == 15


def test_step4_soft_archive_opt_in_for_stale() -> None:
    thread = _thread_at(timedelta(0), timedelta(seconds=1))
    evaluation = evaluate_thread_dissolution(
        thread, T0 + timedelta(days=9), allow_soft_archive=True
    )
    assert evaluation.target_status is ThreadStatus.DORMANT
    assert evaluation.should_mutate is True
    assert evaluation.reason is DissolutionReason.STALE_NO_PROGRESS
    assert evaluation.extra_metadata["idle_days"] == 8


@pytest.mark.parametrize("non_bool", [1, "yes", "True", None, []])
def test_step4_soft_archive_requires_strict_true(non_bool) -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=15), allow_soft_archive=non_bool
    )
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.should_mutate is False
    assert evaluation.extra_metadata["advisory"] is True


def test_step4_soft_archive_without_signal_stays_active() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=1), allow_soft_archive=True
    )
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.extra_metadata["advisory"] is False


def test_step4_soft_archive_never_touches_terminal_threads() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed"), T0 + timedelta(days=99), allow_soft_archive=True
    )
    assert evaluation.target_status is ThreadStatus.COMPLETED
    assert evaluation.reason is DissolutionReason.GOAL_ACHIEVED


def test_step4_soft_archive_never_touches_dormant_threads() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="dormant"), T0 + timedelta(days=99), allow_soft_archive=True
    )
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE


# ──────────────────────────────────────────────────────────────
# T10 優先序與 dormant 免疫
# ──────────────────────────────────────────────────────────────


def test_priority_time_horizon_beats_stale() -> None:
    thread = _thread_at(timedelta(0), timedelta(seconds=1))
    evaluation = evaluate_thread_dissolution(thread, T0 + timedelta(days=20))
    assert evaluation.reason is DissolutionReason.TIME_HORIZON_EXCEEDED
    assert "age_days" in evaluation.extra_metadata
    assert "idle_days" not in evaluation.extra_metadata


def test_dormant_never_triggered_by_step4() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="dormant"), T0 + timedelta(days=365)
    )
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.target_status is ThreadStatus.ACTIVE


def test_dormant_never_triggered_by_step4_with_flag() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="dormant"),
        T0 + timedelta(days=365),
        checkpoints_exhausted=True,
        allow_soft_archive=True,
    )
    assert evaluation.should_mutate is False
    assert evaluation.reason is DissolutionReason.NONE


def test_only_terminal_statuses_trigger_default_dissolution() -> None:
    """§3.1 唯一觸發點：沒有呼叫端旗標時，非終態**永不** `should_mutate=True`。"""
    for status in ("active", "dormant"):
        for kwargs in ({}, {"allow_soft_archive": False}, {"checkpoints_exhausted": False}):
            evaluation = evaluate_thread_dissolution(
                _thread(status=status), T0 + timedelta(days=400), **kwargs
            )
            assert evaluation.should_mutate is False
            assert evaluation.target_status is ThreadStatus.ACTIVE
            if status == "dormant":
                assert evaluation.reason is DissolutionReason.NONE
            else:
                assert evaluation.reason in (
                    DissolutionReason.NONE,
                    DissolutionReason.TIME_HORIZON_EXCEEDED,
                )
                assert evaluation.extra_metadata["advisory"] is True


def test_terminal_statuses_always_mutate_without_flags() -> None:
    for status in ("completed", "abandoned"):
        evaluation = evaluate_thread_dissolution(
            _thread(status=status), T0 + timedelta(days=400)
        )
        assert evaluation.should_mutate is True
        assert evaluation.target_status is ThreadStatus(status)


# ──────────────────────────────────────────────────────────────
# T11 步驟 5：其餘保持活躍
# ──────────────────────────────────────────────────────────────


def test_step5_fresh_active_thread_is_untouched() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=1))
    assert evaluation.target_status is ThreadStatus.ACTIVE
    assert evaluation.reason is DissolutionReason.NONE
    assert evaluation.should_mutate is False
    assert evaluation.reflection_prompt is None


def test_step5_extra_metadata_exact() -> None:
    evaluation = evaluate_thread_dissolution(_thread(), T0 + timedelta(days=1))
    assert evaluation.extra_metadata == {
        "advisory": False,
        "sedimentation_context": None,
        "skipped": None,
    }


def test_step5_thread_id_preserved() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(thread_id="thread-xyz"), T0 + timedelta(days=1)
    )
    assert evaluation.thread_id == "thread-xyz"


def test_step5_does_not_raise_on_extra_unknown_fields() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(unknown_field="x", agent_id="bryan"), T0 + timedelta(days=1)
    )
    assert evaluation.should_mutate is False


# ──────────────────────────────────────────────────────────────
# T12 reflection_prompt（確定性模板）
# ──────────────────────────────────────────────────────────────


def test_prompt_is_deterministic() -> None:
    first = evaluate_thread_dissolution(_thread(status="completed"), NOW).reflection_prompt
    second = evaluate_thread_dissolution(_thread(status="completed"), NOW).reflection_prompt
    assert first == second
    assert isinstance(first, str)


def test_prompt_contains_title_and_duration_days() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", title="寫完論文"), T0 + timedelta(days=23)
    )
    assert "寫完論文" in evaluation.reflection_prompt
    assert "23 天" in evaluation.reflection_prompt


def test_prompt_ends_with_output_contract() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    assert evaluation.reflection_prompt.endswith(_OUTPUT_CONTRACT)
    assert '"meaning_kind": "competence|connection|loss|relief|discovery|none"' in (
        evaluation.reflection_prompt
    )
    assert "請只回 JSON：" in evaluation.reflection_prompt


def test_prompt_first_person_question_phrasing() -> None:
    achieved = evaluate_thread_dissolution(_thread(status="completed"), NOW).reflection_prompt
    abandoned = evaluate_thread_dissolution(_thread(status="abandoned"), NOW).reflection_prompt
    dormant = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=20), allow_soft_archive=True
    ).reflection_prompt
    assert "這段經歷，對「我」意味著什麼？" in achieved
    assert "這次放棄，對「我」意味著什麼？" in abandoned
    assert "這段懸而未決的經歷，對「我」意味著什麼？" in dormant


def test_prompt_three_semantics_are_distinct() -> None:
    achieved = evaluate_thread_dissolution(_thread(status="completed"), NOW).reflection_prompt
    abandoned = evaluate_thread_dissolution(_thread(status="abandoned"), NOW).reflection_prompt
    dormant = evaluate_thread_dissolution(
        _thread(), T0 + timedelta(days=20), allow_soft_archive=True
    ).reflection_prompt
    assert len({achieved, abandoned, dormant}) == 3


def test_prompt_checkpoint_exhausted_uses_completion_semantics() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(), NOW, checkpoints_exhausted=True
    )
    assert "你剛完成一條生活線頭" in evaluation.reflection_prompt


@pytest.mark.parametrize("title", [None, "", "   ", 123])
def test_prompt_missing_title_fallback(title) -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed", title=title), NOW)
    assert "（無題）" in evaluation.reflection_prompt


def test_prompt_present_title_is_not_replaced() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", title="整理房間"), NOW
    )
    assert "整理房間" in evaluation.reflection_prompt
    assert "（無題）" not in evaluation.reflection_prompt


def test_prompt_zero_days_for_same_instant() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), T0)
    assert "0 天" in evaluation.reflection_prompt


def test_prompt_negative_duration_clamped_to_zero() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), T0 - timedelta(days=5))
    assert "0 天" in evaluation.reflection_prompt


# ──────────────────────────────────────────────────────────────
# T13 sedimentation_context（§3.2 輸入欄位）
# ──────────────────────────────────────────────────────────────


def test_context_exact_key_set() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    context = evaluation.extra_metadata["sedimentation_context"]
    assert set(context) == {
        "agent_id",
        "title",
        "narrative_content",
        "origin_type",
        "terminal_status",
        "soul_context",
        "created_at",
        "updated_at",
    }


def test_context_soul_context_is_always_none() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed"), NOW, agent_id="bryan"
    )
    assert evaluation.extra_metadata["sedimentation_context"]["soul_context"] is None


def test_context_agent_id_parameter_wins_over_thread_value() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", agent_id="from-thread"), NOW, agent_id="from-param"
    )
    assert evaluation.extra_metadata["sedimentation_context"]["agent_id"] == "from-param"


def test_context_agent_id_falls_back_to_thread_value() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", agent_id="from-thread"), NOW
    )
    assert evaluation.extra_metadata["sedimentation_context"]["agent_id"] == "from-thread"


def test_context_agent_id_none_when_absent_everywhere() -> None:
    evaluation = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    assert evaluation.extra_metadata["sedimentation_context"]["agent_id"] is None


def test_context_missing_narrative_content_is_none() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread_without("narrative_content", status="completed"), NOW
    )
    assert evaluation.extra_metadata["sedimentation_context"]["narrative_content"] is None


def test_context_echoes_narrative_origin_and_timestamps() -> None:
    evaluation = evaluate_thread_dissolution(
        _thread(status="completed", origin_type="world_collision"), NOW
    )
    context = evaluation.extra_metadata["sedimentation_context"]
    assert context["narrative_content"] == "每天練 20 分鐘"
    assert context["origin_type"] == "world_collision"
    assert context["created_at"] == T0.isoformat()
    assert context["updated_at"] == T0.isoformat()
    assert context["title"] == "學吉他"


def test_context_terminal_status_matches_target() -> None:
    completed = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    abandoned = evaluate_thread_dissolution(_thread(status="abandoned"), NOW)
    assert completed.extra_metadata["sedimentation_context"]["terminal_status"] == "completed"
    assert abandoned.extra_metadata["sedimentation_context"]["terminal_status"] == "abandoned"


# ──────────────────────────────────────────────────────────────
# T14 批量函式
# ──────────────────────────────────────────────────────────────


def _batch_fixture() -> list:
    return [
        _thread(thread_id="t3", status="active"),
        _thread(thread_id="t1", status="completed"),
        _thread(thread_id="t5", status="abandoned"),
        _thread(thread_id="t2", status="dormant"),
        _thread(thread_id="t4", status="bogus"),
    ]


@pytest.mark.parametrize("bad_input", [None, "abc", 42, {"t1": {}}, tuple()])
def test_batch_non_list_returns_empty(bad_input) -> None:
    assert evaluate_batch_dissolution(bad_input, NOW) == []


def test_batch_non_list_warns(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        evaluate_batch_dissolution(42, NOW)
    assert any("active_threads" in record.getMessage() for record in caplog.records)


def test_batch_empty_list_returns_empty() -> None:
    assert evaluate_batch_dissolution([], NOW) == []


def test_batch_length_is_preserved_for_mixed_statuses() -> None:
    evaluations = evaluate_batch_dissolution(_batch_fixture(), NOW + timedelta(days=1))
    assert len(evaluations) == 5
    by_id = {evaluation.thread_id: evaluation for evaluation in evaluations}
    assert by_id["t1"].reason is DissolutionReason.GOAL_ACHIEVED
    assert by_id["t1"].should_mutate is True
    assert by_id["t5"].reason is DissolutionReason.MANUAL_CLOSE
    assert by_id["t5"].should_mutate is True
    assert by_id["t2"].reason is DissolutionReason.NONE
    assert by_id["t4"].reason is DissolutionReason.NONE
    assert "invalid" in by_id["t4"].extra_metadata


def test_batch_output_sorted_by_thread_id() -> None:
    evaluations = evaluate_batch_dissolution(_batch_fixture(), NOW + timedelta(days=1))
    assert [evaluation.thread_id for evaluation in evaluations] == ["t1", "t2", "t3", "t4", "t5"]


def test_batch_output_is_input_order_independent() -> None:
    threads = _batch_fixture()
    forward = evaluate_batch_dissolution(threads, NOW + timedelta(days=1))
    backward = evaluate_batch_dissolution(list(reversed(threads)), NOW + timedelta(days=1))
    permuted = evaluate_batch_dissolution(
        [threads[3], threads[0], threads[4], threads[1], threads[2]], NOW + timedelta(days=1)
    )
    assert forward == backward == permuted
    assert repr(forward) == repr(backward) == repr(permuted)


def test_batch_unresolvable_thread_ids_go_last() -> None:
    threads = [42, _thread(thread_id="t2", status="completed"), _thread(thread_id="t1")]
    evaluations = evaluate_batch_dissolution(threads, NOW + timedelta(days=1))
    assert [evaluation.thread_id for evaluation in evaluations] == ["t1", "t2", ""]


def test_batch_unresolvable_items_order_is_deterministic() -> None:
    items = [42, _thread(thread_id="t1"), {"status": "active"}]
    forward = evaluate_batch_dissolution(items, NOW)
    backward = evaluate_batch_dissolution(list(reversed(items)), NOW)
    assert forward == backward


def test_batch_does_not_apply_daily_quota() -> None:
    """🔴 決策層不套用 §3.6 配額：5 筆終態全部 `should_mutate=True`。"""
    threads = [
        _thread(thread_id=f"t{index}", status="completed") for index in range(5)
    ]
    evaluations = evaluate_batch_dissolution(threads, NOW)
    assert len(evaluations) == 5
    assert all(evaluation.should_mutate is True for evaluation in evaluations)


def test_batch_passes_soft_archive_through() -> None:
    threads = [_thread(thread_id="t1", status="active")]
    evaluations = evaluate_batch_dissolution(
        threads, T0 + timedelta(days=20), allow_soft_archive=True
    )
    assert evaluations[0].target_status is ThreadStatus.DORMANT
    assert evaluations[0].should_mutate is True


def test_batch_invalid_current_time_marks_every_entry_invalid() -> None:
    evaluations = evaluate_batch_dissolution(_batch_fixture(), "nope")
    assert len(evaluations) == 5
    assert all("invalid" in evaluation.extra_metadata for evaluation in evaluations)
    assert all(evaluation.should_mutate is False for evaluation in evaluations)


def test_batch_never_mutates_inputs() -> None:
    threads = _batch_fixture()
    snapshot = copy.deepcopy(threads)
    snapshot_repr = repr(threads)
    evaluate_batch_dissolution(threads, NOW + timedelta(days=1), allow_soft_archive=True)
    assert threads == snapshot
    assert repr(threads) == snapshot_repr


# ──────────────────────────────────────────────────────────────
# T15 §3.6 配額純輔助
# ──────────────────────────────────────────────────────────────


def _dissolved(thread_id: str, dissolved_at) -> dict:
    return _thread(thread_id=thread_id, status="completed", dissolved_at=dissolved_at)


def test_count_same_day() -> None:
    threads = [
        _dissolved("a", (T0 + timedelta(days=3)).isoformat()),
        _dissolved("b", (T0 + timedelta(days=3, hours=5)).isoformat()),
        _dissolved("c", (T0 + timedelta(days=4)).isoformat()),
    ]
    assert count_dissolved_on_date(threads, T0 + timedelta(days=3, hours=23)) == 2


def test_count_other_day_returns_zero() -> None:
    threads = [_dissolved("a", (T0 + timedelta(days=3)).isoformat())]
    assert count_dissolved_on_date(threads, T0 + timedelta(days=5)) == 0


def test_count_accepts_zulu_and_epoch_forms() -> None:
    threads = [
        _dissolved("a", (T0 + timedelta(days=3)).isoformat().replace("+00:00", "Z")),
        _dissolved("b", int((T0 + timedelta(days=3, hours=1)).timestamp())),
    ]
    assert count_dissolved_on_date(threads, T0 + timedelta(days=3, hours=2)) == 2


def test_count_skips_unparseable_dissolved_at() -> None:
    threads = [_dissolved("a", "not-a-date"), _dissolved("b", (T0 + timedelta(days=3)).isoformat())]
    assert count_dissolved_on_date(threads, T0 + timedelta(days=3, hours=1)) == 1


def test_count_skips_missing_dissolved_at() -> None:
    threads = [_thread(thread_id="a", status="completed"), _thread(thread_id="b")]
    assert count_dissolved_on_date(threads, NOW) == 0


def test_count_skips_non_mapping_items() -> None:
    threads = [42, None, _dissolved("b", (T0 + timedelta(days=3)).isoformat())]
    assert count_dissolved_on_date(threads, T0 + timedelta(days=3, hours=1)) == 1


def test_count_non_list_returns_zero() -> None:
    for bad in (None, "abc", 42, {"a": 1}):
        assert count_dissolved_on_date(bad, NOW) == 0


def test_count_invalid_current_time_returns_zero() -> None:
    threads = [_dissolved("a", (T0 + timedelta(days=3)).isoformat())]
    assert count_dissolved_on_date(threads, "nope") == 0
    assert count_dissolved_on_date(threads, None) == 0


def test_remaining_budget_subtracts_usage() -> None:
    threads = [_dissolved("a", (T0 + timedelta(days=3)).isoformat())]
    assert remaining_daily_dissolution_budget(threads, T0 + timedelta(days=3, hours=1)) == 2


def test_remaining_budget_zero_when_three_used() -> None:
    threads = [
        _dissolved(name, (T0 + timedelta(days=3, minutes=index)).isoformat())
        for index, name in enumerate(("a", "b", "c"))
    ]
    assert remaining_daily_dissolution_budget(threads, T0 + timedelta(days=3, hours=1)) == 0


def test_remaining_budget_floor_is_zero_when_over_used() -> None:
    threads = [
        _dissolved(f"t{index}", (T0 + timedelta(days=3, minutes=index)).isoformat())
        for index in range(4)
    ]
    assert remaining_daily_dissolution_budget(threads, T0 + timedelta(days=3, hours=1)) == 0


def test_remaining_budget_full_when_nothing_dissolved() -> None:
    assert remaining_daily_dissolution_budget([], T0) == DISSOLVE_MAX_PER_DAY


def test_remaining_budget_invalid_current_time_returns_zero() -> None:
    threads = [_dissolved("a", T0.isoformat())]
    assert remaining_daily_dissolution_budget(threads, "nope") == 0
    assert remaining_daily_dissolution_budget(threads, None) == 0


def test_quota_helpers_do_not_mutate_inputs() -> None:
    threads = [_dissolved("a", (T0 + timedelta(days=3)).isoformat())]
    snapshot = copy.deepcopy(threads)
    count_dissolved_on_date(threads, T0 + timedelta(days=3, hours=1))
    remaining_daily_dissolution_budget(threads, T0 + timedelta(days=3, hours=1))
    assert threads == snapshot


# ──────────────────────────────────────────────────────────────
# T16 純函式不變量
# ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,kwargs",
    [
        ("active", {}),
        ("active", {"checkpoints_exhausted": True}),
        ("active", {"allow_soft_archive": True}),
        ("completed", {}),
        ("completed", {"dissolved_at": "2026-01-02T00:00:00+00:00"}),
        ("abandoned", {}),
        ("dormant", {}),
    ],
)
def test_same_input_yields_identical_evaluation(status, kwargs) -> None:
    overrides = {"status": status}
    dissolved = kwargs.pop("dissolved_at", None)
    if dissolved is not None:
        overrides["dissolved_at"] = dissolved
    thread = _thread(**overrides)
    first = evaluate_thread_dissolution(thread, NOW, **kwargs)
    second = evaluate_thread_dissolution(thread, NOW, **kwargs)
    assert first == second
    assert repr(first) == repr(second)


def test_evaluation_equality_is_value_based() -> None:
    first = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    second = evaluate_thread_dissolution(_thread(status="completed"), NOW)
    assert first is not second
    assert first == second


def test_input_thread_is_bitwise_unchanged_across_all_paths() -> None:
    cases = [
        (_thread(status="completed"), NOW, {}),
        (_thread(status="abandoned"), NOW, {}),
        (_thread(status="active"), T0 + timedelta(days=20), {}),
        (_thread(status="active"), T0 + timedelta(days=20), {"allow_soft_archive": True}),
        (_thread(status="dormant"), T0 + timedelta(days=20), {}),
        (_thread(status="completed", dissolved_at="2026-01-05T00:00:00+00:00"), NOW, {}),
        (_thread(status="active"), NOW, {"checkpoints_exhausted": True}),
    ]
    for thread, now, kwargs in cases:
        before = copy.deepcopy(thread)
        before_repr = repr(thread)
        evaluate_thread_dissolution(thread, now, **kwargs)
        assert thread == before
        assert repr(thread) == before_repr


def test_no_logging_side_effect_on_happy_path(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        evaluate_thread_dissolution(_thread(status="completed"), NOW)
        evaluate_thread_dissolution(_thread(), T0 + timedelta(days=1))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ──────────────────────────────────────────────────────────────
# T17 No-Scoring / 純度靜態檢查（AST 自掃）
# ──────────────────────────────────────────────────────────────


def _identifier_names(tree: ast.AST) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
                names.add(arg.arg)
        elif isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def test_no_float_literals_in_module() -> None:
    floats = [
        node
        for node in ast.walk(MODULE_TREE)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []


def test_no_scoring_identifiers_in_module() -> None:
    offenders = sorted(name for name in _identifier_names(MODULE_TREE) if _WARNING_FIELD_RE.search(name))
    assert offenders == []


def test_no_src_imports_in_module() -> None:
    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("src")
        elif isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("src")


def test_imports_are_stdlib_whitelisted() -> None:
    roots = set()
    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= _ALLOWED_IMPORT_ROOTS


def test_no_io_or_concurrency_primitives() -> None:
    offenders = set()
    for node in ast.walk(MODULE_TREE):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"open", "eval", "exec", "compile", "__import__"}:
                offenders.add(node.func.id)
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"sleep", "Thread", "Timer", "run"}:
                offenders.add(node.func.attr)
    assert offenders == set()


def test_no_forbidden_module_tokens() -> None:
    for token in ("random", "threading", "asyncio", "time.sleep", "socket", "subprocess", "requests", "urllib"):
        assert token not in MODULE_SOURCE


def test_module_has_no_timers_or_main_entry() -> None:
    assert "__main__" not in MODULE_SOURCE
    assert "while True" not in MODULE_SOURCE


def test_module_file_is_lf_only_without_bom() -> None:
    raw = MODULE_PATH.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw


def test_module_declares_all_and_docstring() -> None:
    assert MODULE_TREE.body[0].value is not None  # module docstring
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
        for node in MODULE_TREE.body
    )


# ──────────────────────────────────────────────────────────────
# T18 模組邊界（票面不存在的欄位／狀態值不得滲入）
# ──────────────────────────────────────────────────────────────


def test_module_source_has_no_nonexistent_ticket_fields() -> None:
    for token in ("progress_state", "is_resolved", "check_points"):
        assert token not in MODULE_SOURCE


def test_module_source_has_no_phantom_status_values() -> None:
    for token in ('"dissolved"', '"archived"', '"suspended"'):
        assert token not in MODULE_SOURCE


def test_enum_values_exclude_phantom_statuses() -> None:
    phantom = {"dissolved", "archived", "suspended"}
    assert not phantom & {member.value for member in ThreadStatus}
    assert not phantom & {member.value for member in DissolutionReason}


def test_dissolution_reason_has_no_time_horizon_alias_drift() -> None:
    assert DissolutionReason.TIME_HORIZON_EXCEEDED.value == "time_horizon_exceeded"
    assert DissolutionReason.STALE_NO_PROGRESS.value == "stale_no_progress"
    assert DissolutionReason.NONE.value == "none"
