"""
tests/test_decision_failclosed_observability.py — DECISION-OBSERVABILITY-1

票 4 (決策失敗可觀測性): 生產決策層 2/2 全失敗, 但失敗原因無法判定。

病灶 (READ-ONLY 診斷證實):
  - ``src/soul/decision.py`` 的 ``FAIL_CLOSED_REASON`` **一個字串同時代表三種成因**:
      F2 ``raw is None``          → LLM 呼叫失敗 / 無輸出
      F3 ``_extract_json() is None`` → 非 JSON / 解析失敗
      F4 ``decision`` 缺失或不在 ``DECISION_ACTIONS`` → 欄位缺失 / 非法值
  - 原始 LLM 回覆從未落盤 → 事後完全無法判別。

本票做法 (純 additive, 0 判定語意變更): 三個 fail-closed 分支各加一行
``logger.warning``, 內含 ①分支代號 F2/F3/F4 ②原始回覆的截斷節錄
``raw[:200]`` (F2 明確標示 ``raw=None``) ③既有診斷變數 (extract 返回型別 /
非法 decision 值 / dict keys 前 10 個)。

本檔即為「判別力」見證: 在**未加 warning 的舊 decision.py** 上, 這些斷言
必然失敗 (無分支代號 / 無節錄可抽), 故本測試確實釘住了新行為。

紅線 (本票不得越界, 由 tests_semantics_unchanged 系列釘住):
  - ``reason`` 逐字仍為 ``FAIL_CLOSED_REASON``
  - ``decision`` 仍為 ``do_nothing``
  - 0 新增 LLM 呼叫 / 0 Frozen Contract 觸碰
"""
from __future__ import annotations

import ast
import logging
import re
from typing import List, Optional

import pytest

from src.soul.decision import (
    DECISION_ACTIONS,
    FAIL_CLOSED_REASON,
    parse_decision_output,
)

DECISION_LOGGER = "soul_os.soul.decision"

#: 逐字鎖死: 本票不得改變 reason 字串 (工單紅線)
EXPECTED_REASON_LITERAL = "decision_llm_failure_or_bad_output"

#: 節錄上限 (字元)
EXCERPT_MAX_CHARS = 200

#: 節錄在 log 中的固定標記 (測試據此抽取, 不靠中文文案)
EXCERPT_MARK = "raw[:200]="

_EXCERPT_RE = re.compile(re.escape(EXCERPT_MARK) + r"(?P<repr>.*)$", re.DOTALL)


# ───────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────

def _warning_messages(caplog: pytest.LogCaptureFixture) -> List[str]:
    """本次捕獲的 decision logger warning 訊息清單。"""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == DECISION_LOGGER
    ]


def _extract_excerpt(message: str) -> Optional[str]:
    """從 log 行取出 ``raw[:200]`` 節錄的**真實字串值** (還原 repr 轉義)。

    以 ``ast.literal_eval`` 還原, 故長度斷言量到的是節錄本身,
    不受 repr 轉義 (如 ``\\n`` → 2 字元) 膨脹影響。
    """
    m = _EXCERPT_RE.search(message)
    if m is None:
        return None
    return ast.literal_eval(m.group("repr").strip())


def _drive(raw: Optional[str], caplog: pytest.LogCaptureFixture) -> dict:
    """驅動一次 parse_decision_output 並回傳其結果 (capture 已就緒)。"""
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        return parse_decision_output(raw)


# 三種失敗模式的固定樣本
F2_RAW: Optional[str] = None
F3_RAW = "I think you should send it"          # 非 JSON, 無 {} 可抽
F4_RAW = '{"reason": "no decision field"}'     # 合法 JSON, 但缺 decision

LONG_LEN = 1000


# ───────────────────────────────────────────────────────────
# (a) 三個分支各產生一行「對應代號」的 warning — 三種成因可被區分
# ───────────────────────────────────────────────────────────

def test_f2_raw_none_emits_branch_code(caplog):
    """F2: raw=None → 一行含 [F2] 且明確標示 raw=None 的 warning。"""
    result = _drive(F2_RAW, caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, f"F2 應恰好一行 warning, 實得: {msgs}"
    assert "[F2]" in msgs[0], msgs[0]
    assert "raw=None" in msgs[0], msgs[0]
    assert result["decision"] == "do_nothing"


def test_f3_non_json_emits_branch_code(caplog):
    """F3: 非 JSON → 一行含 [F3] 的 warning。"""
    _drive(F3_RAW, caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, f"F3 應恰好一行 warning, 實得: {msgs}"
    assert "[F3]" in msgs[0], msgs[0]
    assert _extract_excerpt(msgs[0]) == F3_RAW[:EXCERPT_MAX_CHARS]


def test_f4_missing_decision_emits_branch_code(caplog):
    """F4: 缺 decision → 一行含 [F4] 的 warning。"""
    _drive(F4_RAW, caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, f"F4 應恰好一行 warning, 實得: {msgs}"
    assert "[F4]" in msgs[0], msgs[0]
    assert _extract_excerpt(msgs[0]) == F4_RAW[:EXCERPT_MAX_CHARS]


def test_f4_illegal_value_also_emits_branch_code(caplog):
    """F4 (非法值變體): decision 不在 DECISION_ACTIONS → 同一分支代號 [F4]。"""
    _drive('{"decision": "maybe", "reason": "x"}', caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, msgs
    assert "[F4]" in msgs[0], msgs[0]


def test_three_failure_modes_are_distinguishable(caplog):
    """判別力核心: 三種成因產生**三個不同代號**, 而非同一個字串。"""
    codes = []
    for raw in (F2_RAW, F3_RAW, F4_RAW):
        caplog.clear()
        _drive(raw, caplog)
        msgs = _warning_messages(caplog)
        assert len(msgs) == 1, msgs
        found = [c for c in ("[F2]", "[F3]", "[F4]") if c in msgs[0]]
        assert len(found) == 1, f"應恰好命中一個分支代號, 實得 {found}: {msgs[0]}"
        codes.append(found[0])

    assert codes == ["[F2]", "[F3]", "[F4]"], f"三成因必須可區分, 實得 {codes}"


# ───────────────────────────────────────────────────────────
# (b) 節錄長度 ≤ 200 字元 (1000 字元輸入 → 輸出節錄不得超過 200)
# ───────────────────────────────────────────────────────────

def test_f3_long_raw_excerpt_truncated_to_200(caplog):
    """F3 截斷: 1000 字元非 JSON 輸入 → 節錄恰為前 200 字元。"""
    long_raw = "z" * LONG_LEN
    assert len(long_raw) == LONG_LEN
    _drive(long_raw, caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, msgs
    excerpt = _extract_excerpt(msgs[0])
    assert excerpt is not None, f"未取得節錄: {msgs[0]}"
    assert len(excerpt) <= EXCERPT_MAX_CHARS, f"節錄超長: {len(excerpt)}"
    assert len(excerpt) == EXCERPT_MAX_CHARS, f"應截到上限, 實得 {len(excerpt)}"
    assert excerpt == long_raw[:EXCERPT_MAX_CHARS]
    # 不得整段落盤: 節錄以外不得再現原始長度
    assert len(long_raw) > len(excerpt)


def test_f4_long_raw_excerpt_truncated_to_200(caplog):
    """F4 截斷: 1000 字元合法 JSON (非法 decision 值) → 節錄恰為前 200 字元。"""
    long_raw = '{"decision": "nope", "reason": "' + ("y" * 1000) + '"}'
    assert len(long_raw) > LONG_LEN
    _drive(long_raw, caplog)

    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, msgs
    excerpt = _extract_excerpt(msgs[0])
    assert excerpt is not None, f"未取得節錄: {msgs[0]}"
    assert len(excerpt) <= EXCERPT_MAX_CHARS, f"節錄超長: {len(excerpt)}"
    assert excerpt == long_raw[:EXCERPT_MAX_CHARS]


@pytest.mark.parametrize("raw", [F3_RAW, F4_RAW], ids=["F3", "F4"])
def test_excerpt_never_exceeds_limit_for_boundary_inputs(raw, caplog):
    """邊界: 短輸入的節錄自然 <= 200, 且等於輸入全長 (無截斷損失)。"""
    _drive(raw, caplog)
    msgs = _warning_messages(caplog)
    excerpt = _extract_excerpt(msgs[0])
    assert excerpt is not None
    assert len(excerpt) <= EXCERPT_MAX_CHARS
    assert excerpt == raw[:EXCERPT_MAX_CHARS]


# ───────────────────────────────────────────────────────────
# (c) raw=None 不明文拋例外, 仍產生 warning
# ───────────────────────────────────────────────────────────

def test_f2_raw_none_does_not_raise_and_still_warns(caplog):
    """raw=None 走完不拋例外, 且確實留下 warning (含 raw=None 標示)。"""
    with caplog.at_level(logging.WARNING, logger=DECISION_LOGGER):
        try:
            result = parse_decision_output(None)
        except Exception as exc:  # pragma: no cover - 失敗即為本票回歸
            pytest.fail(f"raw=None 不得拋例外, 實得 {type(exc).__name__}: {exc}")

    assert result == {"decision": "do_nothing", "reason": FAIL_CLOSED_REASON}
    msgs = _warning_messages(caplog)
    assert len(msgs) == 1, msgs
    assert "raw=None" in msgs[0], msgs[0]


# ───────────────────────────────────────────────────────────
# (d) 語意不變: reason 逐字 / decision == do_nothing
# ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [F2_RAW, F3_RAW, F4_RAW, '{"decision": "maybe", "reason": "x"}', "", "garbage"],
    ids=["F2_none", "F3_non_json", "F4_missing", "F4_illegal", "F3_empty", "F3_garbage"],
)
def test_semantics_unchanged_reason_and_decision(raw, caplog):
    """🔴 語意不變: 三種失敗模式下 reason 逐字等於 FAIL_CLOSED_REASON, decision == do_nothing。"""
    result = _drive(raw, caplog)

    assert result["reason"] == FAIL_CLOSED_REASON
    assert result["reason"] == EXPECTED_REASON_LITERAL
    assert result["decision"] == "do_nothing"
    assert set(result.keys()) == {"decision", "reason"}, "DecisionResult dict 結構不得改變"


def test_fail_closed_reason_literal_is_frozen():
    """鎖死常數本身: FAIL_CLOSED_REASON 不得被本票改寫。"""
    assert FAIL_CLOSED_REASON == EXPECTED_REASON_LITERAL


def test_decision_actions_unchanged():
    """鎖死 DECISION_ACTIONS: 本票不得改動四元行動集合。"""
    assert tuple(DECISION_ACTIONS) == ("transmit", "observe", "reflect", "do_nothing")
    assert "do_nothing" in DECISION_ACTIONS


def test_observability_lines_are_additive_only(caplog):
    """純 additive: 失敗路徑**仍只**產生 warning, 且不 bump 出其他等級雜訊。"""
    for raw in (F2_RAW, F3_RAW, F4_RAW):
        caplog.clear()
        _drive(raw, caplog)
        levels = {r.levelno for r in caplog.records if r.name == DECISION_LOGGER}
        assert levels == {logging.WARNING}, f"預期只有 WARNING, 實得 {levels}"
        assert len(_warning_messages(caplog)) == 1
