"""harness/run_tl12.py — 執行 TA-2-A 真實 LLM 行為觀察（Gate 2）。

Owner 2026-10-03 授權：Ollama Cloud 為包月方案，可直接使用。

嚴守 §4.3：所有輸出寫在 derived / experiment boundary，**永不回寫 canonical store**。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from typing import Any, Dict

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.runner import make_real_llm_call  # noqa: E402
from harness.tl12_temporal import (  # noqa: E402
    TEMPORAL_CONTEXTS,
    TEMPORAL_IRRELEVANT_PROBES,
    TEMPORAL_SENSITIVE_PROBES,
    run_tl12,
)

OUT_DIR = REPO / "data" / "harness_out" / "tl12"


async def main() -> int:
    from configs.loader import load_config

    cfg = load_config()
    model = cfg.get("llm", {}).get("model", "unknown")
    llm_call = make_real_llm_call()

    print("=" * 72)
    print("TA-2-A — Temporal Context Participation（真實 LLM 行為觀察）")
    print("=" * 72)
    print(f"  model         : {model}")
    print(f"  soul          : agent_ruka")
    print(f"  contexts      : {[c for c, _ in TEMPORAL_CONTEXTS]}")
    print(f"  sensitive     : {[p for p, _ in TEMPORAL_SENSITIVE_PROBES]}")
    print(f"  irrelevant    : {[p for p, _ in TEMPORAL_IRRELEVANT_PROBES]}")
    print("  calls/arm     : 2 ctx × 5 probes × 3 runs = 30  (共 60)")
    print()

    res = await run_tl12(llm_call, llm_model=str(model))

    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    print(f"  verdict = {res['verdict']}")
    for r in res["inconclusive_reasons"]:
        print(f"    - {r}")
    print()
    print(f"  n_meaningful_sensitive_probes = {res['n_meaningful_sensitive_probes']}")
    print(f"  control_clean                 = {res['control_clean']}")
    print(f"  control_noise_floor           = {res['control_noise_floor']}")
    print(f"  determinism                   = {res['determinism']['determinism_verdict']}")
    print(f"  n_llm_calls_by_arm            = {res['n_llm_calls_by_arm']}")
    print()
    print("  注入的 coarse 時間行：")
    for k, v in res["temporal_lines"].items():
        print(f"    {k}: {v}")
    print()
    print("  difference-in-difference：")
    for k, v in res["difference_in_difference"].items():
        print(f"    {k}")
        print(
            f"       kind={v['kind']:<12} on_marker={v['on_marker_rate']:<6} "
            f"off_marker={v['off_marker_rate']:<6} delta={v['delta']:<+7} "
            f"text_changed={v['text_changed']}"
        )
        print(
            f"       on_stable={v['on_stable_across_runs']}  "
            f"off_stable={v['off_stable_across_runs']}  "
            f"on_distinct={v['on_distinct_outputs']}  "
            f"off_distinct={v['off_distinct_outputs']}"
        )
    print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "ta2a_temporal_ablation.json"
    out.write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"  結果已寫: {out.relative_to(REPO)}  （derived 邊界，非 canonical）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
