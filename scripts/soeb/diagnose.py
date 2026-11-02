"""SOEB 觀測診斷：唯讀分析 observations.jsonl，判斷「哪一筆真的可用」。

用途
----
`runner.py` 只保證 HTTP 200（`ok=True`），但那**不代表**模型有產出可用正文。
推理型模型的 thinking 通常計入 `completion_tokens`，於是題目很容易被輸出預算吃光。
本腳本把「可用性」做成可判定的三態，避免把截斷誤讀成能力。

判定
----
一筆觀測要能被評分，必須同時滿足：
  1. `ok == True`
  2. `text_chars > 0`（有正文；只有 reasoning 不算）
  3. 未被輸出預算截斷：`finish_reason` 不在截斷集合內，且 `completion_tokens < max_tokens`

安全性
------
- 只讀檔案，不發任何網路請求，不寫入任何路徑（除了 stdout）。
- 不顯示 `request.headers`（含憑證），只取 body 裡的 `max_tokens`。

用法
----
    .venv\\Scripts\\python.exe scripts\\soeb\\diagnose.py --run-dir tests\\results\\soeb\\v1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TRUNCATED_FINISH_REASONS = {
    "length",
    "max_tokens",
    "max_output_tokens",
    "incomplete",
    "content_filter",
}


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _iter_observations(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _max_tokens_of(obs: dict) -> int | None:
    body = (obs.get("request") or {}).get("body") or {}
    for key in ("max_tokens", "max_output_tokens"):
        val = body.get(key)
        if isinstance(val, int):
            return val
    return None


def _completion_tokens_of(obs: dict) -> int | None:
    usage = obs.get("usage") or {}
    for key in ("completion_tokens", "output_tokens"):
        val = usage.get(key)
        if isinstance(val, int):
            return val
    raw = obs.get("usage_raw") or {}
    if isinstance(raw, dict):
        for key in ("completion_tokens", "output_tokens"):
            val = raw.get(key)
            if isinstance(val, int):
                return val
    return None


def classify(obs: dict) -> dict:
    text = obs.get("text") or ""
    reasoning = obs.get("reasoning_text") or ""
    finish = (obs.get("finish_reason") or "").lower()
    max_tokens = _max_tokens_of(obs)
    completion = _completion_tokens_of(obs)

    exhausted = (
        isinstance(max_tokens, int)
        and isinstance(completion, int)
        and completion >= max_tokens
    )
    truncated = finish in TRUNCATED_FINISH_REASONS or exhausted

    if not obs.get("ok"):
        verdict = "ERROR"
    elif not text.strip():
        verdict = "NO_TEXT"
    elif truncated:
        verdict = "TRUNCATED"
    else:
        verdict = "USABLE"

    return {
        "model_id": obs.get("model_id"),
        "task": obs.get("task"),
        "run": obs.get("run"),
        "verdict": verdict,
        "finish_reason": obs.get("finish_reason"),
        "max_tokens": max_tokens,
        "completion_tokens": completion,
        "reasoning_tokens": (obs.get("usage") or {}).get("reasoning_tokens"),
        "text_chars": len(text),
        "reasoning_chars": len(reasoning),
        "latency_ms": obs.get("latency_ms"),
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 觀測診斷（唯讀）")
    ap.add_argument("--run-dir", required=True, help="包含 observations.jsonl 的目錄")
    ap.add_argument(
        "--show",
        action="store_true",
        help="逐筆列出（預設只彙總）",
    )
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    obs_path = run_dir / "observations.jsonl"
    if not obs_path.is_file():
        print(f"[錯誤] 找不到 {obs_path}")
        return 2

    rows = [classify(o) for o in _iter_observations(obs_path)]
    if not rows:
        print("[錯誤] observations.jsonl 是空的")
        return 2

    if args.show:
        print("== 逐筆 ==")
        header = (
            f"{'model':<28} {'task':<4} {'run':<3} {'verdict':<10} "
            f"{'finish':<20} {'out/max':<12} {'text':>7} {'reason':>7} {'ms':>8}"
        )
        print(header)
        print("-" * len(header))
        for r in rows:
            out_max = f"{r['completion_tokens']}/{r['max_tokens']}"
            print(
                f"{str(r['model_id']):<28} {str(r['task']):<4} {str(r['run']):<3} "
                f"{r['verdict']:<10} {str(r['finish_reason']):<20} {out_max:<12} "
                f"{r['text_chars']:>7} {r['reasoning_chars']:>7} "
                f"{str(r['latency_ms']):>8}"
            )
        print()

    # ---- 逐模型彙總 ----
    models: dict[str, dict[str, int]] = {}
    for r in rows:
        m = models.setdefault(
            str(r["model_id"]),
            {"USABLE": 0, "TRUNCATED": 0, "NO_TEXT": 0, "ERROR": 0, "total": 0},
        )
        m[r["verdict"]] += 1
        m["total"] += 1

    print("== 逐模型 ==")
    header = (
        f"{'model':<28} {'total':>5} {'USABLE':>7} {'TRUNC':>6} "
        f"{'NO_TEXT':>8} {'ERROR':>6}  判定"
    )
    print(header)
    print("-" * (len(header) + 20))
    for mid, m in models.items():
        if m["USABLE"] == m["total"]:
            judgement = "全部可用 → 可評分"
        elif m["USABLE"] == 0:
            judgement = "零可用 → 不可評分"
        else:
            judgement = f"部分可用 {m['USABLE']}/{m['total']} → 需重跑"
        print(
            f"{mid:<28} {m['total']:>5} {m['USABLE']:>7} {m['TRUNCATED']:>6} "
            f"{m['NO_TEXT']:>8} {m['ERROR']:>6}  {judgement}"
        )

    # ---- 逐題彙總（看哪一題預算最不夠） ----
    tasks: dict[str, list[int]] = {}
    for r in rows:
        tasks.setdefault(str(r["task"]), []).append(
            1 if r["verdict"] == "USABLE" else 0
        )
    print()
    print("== 逐題可用率 ==")
    for tid in sorted(tasks):
        vals = tasks[tid]
        print(f"  {tid}: {sum(vals)}/{len(vals)} 可用")

    # ---- 結論 ----
    total_usable = sum(1 for r in rows if r["verdict"] == "USABLE")
    print()
    print(
        f"== 結論 ==\n  總計 {len(rows)} 筆，可用 {total_usable} 筆，"
        f"不可用 {len(rows) - total_usable} 筆。"
    )
    if total_usable == len(rows):
        print("  ⇒ 本輪可進入盲評。")
        return 0
    print("  ⇒ 本輪**不可**進入盲評（截斷／空正文會把預算耗盡誤讀成能力差異）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
