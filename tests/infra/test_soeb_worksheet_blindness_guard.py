# -*- coding: utf-8 -*-
"""tests/infra/test_soeb_worksheet_blindness_guard.py — 評分工作表的盲評守衛。

背景（2026-11-01 自己抓到的漏洞）
================================
第一版 `build_worksheet.py` 在每則答案上方印：

    **run 1** · out 1184/32768 tokens · finish=stop

理由是「評分者需要截斷證據」。但那兩個欄位都是**身分指紋**：

* `out 1184/…`：`_metrics.md` 有逐模型、逐題的精確 tokens ⇒ 一比就能還原
  `MODEL_x` 是哪一顆，盲評直接作廢。
* `finish=stop`／`completed`／`end_turn`：這三個值各自只對應一種協定
  （chat-completions／responses／anthropic messages）⇒ 等於把協定寫在標頭。

評分真正需要的只有一個事實：**這則答案是不是被切斷的**。
⇒ 改成三態判定（完整／不完整＋理由），其餘一概不輸出。

本守衛釘住兩件事
================
- **W1（不洩漏指紋）**：`finish_reason` 原文與精確 token 數**不得**出現在工作表。
- **W1 變異自證**：把舊的洩漏版 `_usage_line` 放回來，W1 必須轉紅
  （證明這道斷言不是裝飾品）。
- **W2（截斷資訊仍保留）**：被切斷的答案必須標成「不完整」並說明是評測失效，
  否則評分者會把「預算用盡」當成「能力差」。

全部使用 `tmp_path` 與合成觀測，**不讀真產物、不發任何請求**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.soeb import build_worksheet  # noqa: E402

#: 刻意用不易巧合出現的數字，讓「出現在工作表裡」= 真的洩漏。
TOKENS_COMPLETE = 123457
TOKENS_TRUNCATED = 32768
BUDGET = 32768
FINISH_ALLOWED_VALUES = ("completed", "end_turn", "stop")


def _obs(model: str, finish: str, out_tokens: int) -> dict:
    return {
        "model_id": model,
        "task": "T1",
        "run": 1,
        "ok": True,
        "channel": "chat-completions",
        "protocol": "chat-completions",
        "api_model": model,
        "latency_ms": 999,
        "effort_sent": None,
        "finish_reason": finish,
        "usage": {"output_tokens": out_tokens},
        "text": f"{model} 的答案正文",
    }


def _manifest() -> dict:
    return {
        "runner": "soeb",
        "suite": "soeb-v1",
        "budget_override": {"forced_max_tokens": BUDGET, "original": {"T1": 2048}},
        "models": [],
        "tasks": [{"id": "T1", "max_tokens": BUDGET, "temperature": 0.0, "effort": None}],
    }


def _make_root(tmp_path: Path, records: list[dict]) -> Path:
    by_model: dict[str, list[dict]] = {}
    for rec in records:
        by_model.setdefault(rec["model_id"], []).append(rec)
    root = tmp_path / "run_root"
    root.mkdir()
    for model, recs in by_model.items():
        d = root / model
        d.mkdir()
        (d / "manifest.json").write_text(
            json.dumps(_manifest(), ensure_ascii=False), encoding="utf-8", newline="\n"
        )
        (d / "observations.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
            encoding="utf-8",
            newline="\n",
        )
    return root


def _build(tmp_path: Path, records: list[dict]) -> str:
    root = _make_root(tmp_path, records)
    out = tmp_path / "ws"
    assert build_worksheet.main(["--run-root", str(root), "--out-dir", str(out)]) == 0
    return (out / "T1.md").read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# W1 — 不得洩漏指紋
# ─────────────────────────────────────────────────────────────────────────────


def test_w1_no_finish_reason_or_token_count_in_worksheet(tmp_path: Path) -> None:
    """W1：`finish_reason` 原文與精確 token 數都不得出現在工作表。"""
    md = _build(
        tmp_path,
        [
            _obs("alpha-model", "completed", TOKENS_COMPLETE),
            _obs("beta-model", "end_turn", TOKENS_COMPLETE + 1),
        ],
    )

    for value in FINISH_ALLOWED_VALUES:
        assert value not in md, f"finish_reason 原文 `{value}` 洩漏了協定"
    assert str(TOKENS_COMPLETE) not in md, "精確 token 數洩漏（可與 _metrics.md 交叉還原）"
    assert str(TOKENS_COMPLETE + 1) not in md
    assert str(BUDGET) not in md, "預算數字本身就是指紋（各家預算相同，仍不必印）"
    assert "finish=" not in md
    assert "tokens" not in md.lower(), "任何 token 量都不該出現在工作表"


def test_w1_mutation_legacy_usage_line_turns_the_guard_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W1 變異自證：把舊的洩漏版 `_usage_line` 放回來，斷言必須轉紅。"""

    def leaking_usage_line(rec: dict, budget: int | None) -> str:
        usage = rec.get("usage") or {}
        return (
            f"out {usage.get('output_tokens')}/{budget} tokens"
            f" · finish={rec.get('finish_reason')}"
        )

    monkeypatch.setattr(build_worksheet, "_usage_line", leaking_usage_line)
    md = _build(
        tmp_path,
        [
            _obs("alpha-model", "completed", TOKENS_COMPLETE),
            _obs("beta-model", "end_turn", TOKENS_COMPLETE + 1),
        ],
    )

    assert "finish=" in md, "變異版本沒有印 finish ⇒ 這個變異沒有真的重現舊寫法"
    assert str(TOKENS_COMPLETE) in md
    assert "completed" in md


# ─────────────────────────────────────────────────────────────────────────────
# W2 — 截斷資訊仍須保留（且不得用會被誤讀成能力的措辭）
# ─────────────────────────────────────────────────────────────────────────────


def test_w2_truncated_answer_is_flagged_as_invalid_not_weak(tmp_path: Path) -> None:
    """W2：觸及預算上限的答案要被標成「不完整／評測失效」，不是留空白讓評分者自己猜。"""
    md = _build(tmp_path, [_obs("alpha-model", "length", TOKENS_TRUNCATED)])

    assert "不完整" in md
    assert "評測失效" in md
    assert "length" not in md, "非正常結束的 finish_reason 原文不得輸出（協定指紋）"


def test_w2_complete_answer_says_complete(tmp_path: Path) -> None:
    """W2：正常結束且未觸及上限 ⇒ 標「完整」，讓評分者知道短答案不是被切斷。"""
    md = _build(tmp_path, [_obs("alpha-model", "stop", 120)])

    assert "完整（正常結束、未觸及上限）" in md
    assert "不完整" not in md
