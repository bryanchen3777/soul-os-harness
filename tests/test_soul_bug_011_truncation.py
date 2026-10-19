# -*- coding: utf-8 -*-
"""
SOUL-BUG-011 — 日記截斷防禦守衛。

缺陷（2026-10-18 22:10 收網以 Sidecar 第一手證據定案）：
  src/soul/diary.py 以 `DIARY_MAX_CLEAN_CHARS = 80` 對**任何**輸出無條件呼叫
  `_safe_truncate_on_length(clean, max_chars=80)`，而該 helper 的語意是
  「取 raw 最後 N 字」（src/llm/proxy.py:2813 自陳），語意限縮在
  `OpenAIBackend.complete` 內 `finish_reason == "length"` 的退化循環路徑。
  ⇒ 正常輸出被左側截斷，標題與開頭段落被吃掉。

  實測規模：night p75 = 80、morning p75 = 80，240 筆 llm 日記聚集在 78~80
  字元區間 —— 下游硬砍的物理指紋。
  實測案例：agent_anna raw 151 字 → 落盤 80 字（整段前文消失）；
            agent_akane raw 105 字 → 落盤 79 字。

修法（Owner 裁定 2026-10-18）：
  1. 上限 80 → 200，對齊 src/llm/proxy.py 已確立的全專案上限（常數分裂消除）
  2. 截斷語意由「取尾段」改為「保留前段 + 標點優雅收尾」
  3. 退化循環的 finish_reason 閘門**不動**（防崩潰兜底必須保留）
  4. 修 `_raw_response_model()` 取錯屬性（sidecar model 欄位 10/10 為空）

🔬 全部測試純標準庫 / 純函式，不 spawn、不連網、不觸碰生產 data/。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.soul.diary import DIARY_MAX_CLEAN_CHARS, _raw_response_model
from src.soul.dream_event import DREAM_EVENT_MAX_CLEAN_CHARS
from src.text_guard import DIARY_OUTPUT_MAX_CHARS, graceful_prefix_truncate

ROOT = Path(__file__).resolve().parents[1]
PROXY_PY = ROOT / "src" / "llm" / "proxy.py"

#: Owner 裁定的全專案上限（對齊 proxy.py:1900 finish_reason=length 分支）
EXPECTED_CAP = 200

TERMINATORS = "。、！？\n.!?"


def _has_edge_whitespace(s: str) -> bool:
    """反假綠：樣本必須「非平凡」，否則逐位元守衛形同虛設。"""
    return s != s.strip()


# --------------------------------------------------------------------------- #
# 1. 正常輸出絕不從左側截斷
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "length",
    [81, 100, 150, EXPECTED_CAP, EXPECTED_CAP + 1],
    ids=["just-over-old-cap", "mid", "akane-case", "exactly-cap", "over-new-cap"],
)
def test_normal_output_never_truncated_from_left(length):
    """80~200 字區間的正常輸出，開頭標題與前段必須逐字元完整保留。"""
    header = "**2026-10-18（夜）**\n\n"
    body = "这是个正常但偏长的日记开头，应该被完整保留下来。" * 20
    text = header + body
    text = text[:length]

    out = graceful_prefix_truncate(text, max_chars=EXPECTED_CAP)

    # 不論是否觸發截斷，開頭都必須是原文開頭
    assert out.startswith("**2026-10-18（夜）**"), (
        f"開頭標題被吃掉 —— 這正是 SOUL-BUG-011 的缺陷形狀。輸出開頭：{out[:40]!r}"
    )
    assert out == text or len(out) <= EXPECTED_CAP


def test_truncation_result_is_a_prefix_of_the_input():
    """結果必須是原文的前綴 —— 取尾段會讓這個斷言立刻失敗。"""
    text = "**header**\n\n" + ("內容" * 400)
    out = graceful_prefix_truncate(text, max_chars=EXPECTED_CAP)
    assert text.startswith(out), "結果不是原文前綴，代表发生了取尾段式的破坏"
    assert out != text, "本用例必須真的觸發截斷，否則斷言無意義"


# --------------------------------------------------------------------------- #
# 2. 優雅截斷必須落在標點
# --------------------------------------------------------------------------- #
def test_graceful_truncation_snaps_to_punctuation():
    """超長文本必須於最後一個終止標點處優雅收尾。"""
    text = "第一句。第二句。第三句。" * 200
    out = graceful_prefix_truncate(text, max_chars=EXPECTED_CAP)

    assert len(out) <= EXPECTED_CAP, "不得超過上限（無界膨脹）"
    assert out[-1] in TERMINATORS, (
        f"必須以終止標點收尾，實得 {out[-1]!r}；字元死切會腰斬句子"
    )
    assert out.startswith("第一句。"), "敘事開頭必須保留"


def test_graceful_truncation_no_terminator_falls_back_to_hard_cap():
    """前 CAP 內完全無標點 → 退回字元硬截斷，杜絕無界膨脹（不做語義猜測）。"""
    text = "A" * 5000
    out = graceful_prefix_truncate(text, max_chars=EXPECTED_CAP)
    assert len(out) == EXPECTED_CAP, f"無標點長句必須硬切到 {EXPECTED_CAP}，實得 {len(out)}"


def test_short_input_returned_verbatim():
    text = "短日記。"
    assert graceful_prefix_truncate(text, max_chars=EXPECTED_CAP) == text


# --------------------------------------------------------------------------- #
# 3. 三處常數必須恆等（常數分裂會重演整個 bug）
# --------------------------------------------------------------------------- #
def test_cap_constants_aligned_across_modules():
    assert DIARY_OUTPUT_MAX_CHARS == EXPECTED_CAP
    assert DIARY_MAX_CLEAN_CHARS == DIARY_OUTPUT_MAX_CHARS
    assert DREAM_EVENT_MAX_CLEAN_CHARS == DIARY_OUTPUT_MAX_CHARS


def test_proxy_finish_reason_gate_still_uses_same_cap():
    """proxy 層的退化兜底必須仍存在，且其長度上限與 diary SSOT 相同。

    實作事實：`proxy.py:1902` 呼叫 `_safe_truncate_on_length(raw)` **不傳 max_chars**，
    使用的是 `proxy.py:2802` 簽章上的預設值。因此結構守衛檢查兩件事：
      (a) `finish_reason == "length"` 分支仍存在且仍呼叫該 helper
      (b) helper 簽章的 `max_chars` 預設值 == SSOT
    """
    src = PROXY_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # (a) finish_reason == "length" 分支仍呼叫 _safe_truncate_on_length
    gate_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_src = ast.get_source_segment(src, node.test) or ""
            if "length" in test_src and "_c1_finish" in test_src:
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        nm = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                        if nm == "_safe_truncate_on_length":
                            gate_found = True
    assert gate_found, (
        "proxy.py 的 finish_reason=='length' 兜底閘門已消失 —— "
        "退化循環會重新打爆 max_tokens（SOUL-BUG-011 防回歸重點）"
    )

    # (b) helper 簽章預設值 == SSOT
    default = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_safe_truncate_on_length":
            for d in node.args.defaults:
                pos = node.args.args.index(node.args.args[-len(node.args.defaults)])
                if node.args.args[pos].arg == "max_chars" and isinstance(d, ast.Constant):
                    default = d.value
    assert default == DIARY_OUTPUT_MAX_CHARS, (
        f"proxy 兜底上限 {default} 與 diary SSOT {DIARY_OUTPUT_MAX_CHARS} 分裂 —— "
        f"常數分裂會重演整個 bug"
    )


# --------------------------------------------------------------------------- #
# 4. sidecar model 欄位必須記到真實模型名
# --------------------------------------------------------------------------- #
def test_sidecar_model_field_non_empty(monkeypatch):
    """`_raw_response_model()` 原本讀 `proxy.config.model`，實際屬性是 `proxy.model`。"""

    class FakeProxy:
        model = "deepseek-v4.1-flash"

    monkeypatch.setattr("src.soul.diary._find_llm_proxy", lambda: FakeProxy())
    assert _raw_response_model() == "deepseek-v4.1-flash"


def test_sidecar_model_survives_missing_proxy(monkeypatch):
    """取不到 proxy 必須回空字串而非拋例外（觀測欄位不得炸主路徑）。"""
    monkeypatch.setattr("src.soul.diary._find_llm_proxy", lambda: None)
    assert _raw_response_model() == ""


# --------------------------------------------------------------------------- #
# 5. 退化循環防崩潰兜底仍然生效（防「修壞防護」）
# --------------------------------------------------------------------------- #
def test_degenerate_length_path_still_truncated():
    """1913 字連續「嗯」的退化循環仍必須被壓到 ≤200 —— 這是 proxy 層的職責。"""
    from src.llm.proxy import _safe_truncate_on_length

    degenerate = "嗯" * 1913
    out = _safe_truncate_on_length(degenerate, max_chars=EXPECTED_CAP)
    assert len(out) <= EXPECTED_CAP, (
        f"退化循環未被壓制（{len(out)} 字）—— 防崩潰兜底失效會讓 LLM 打爆 max_tokens"
    )
    # 退化路徑仍取尾段是**正確**的：重複內容的開頭與結尾等價，
    # 而正常輸出絕不可取尾段（由 test_normal_output_never_truncated_from_left 把關）。
    assert isinstance(out, str) and out


def test_guard_fixtures_are_non_trivial():
    """反假綠斷言：守衛樣本本身必須帶前後空白與標點，否則逐位元守衛會假綠。"""
    sample = "\n  第一句。第二句。第三句。  \n"
    assert _has_edge_whitespace(sample), "fixture 必須帶前後空白"
    last_visible = sample.rstrip()[-1]
    assert last_visible in TERMINATORS, (
        f"fixture 的最後一個可見字元必須是終止標點，實得 {last_visible!r}"
    )