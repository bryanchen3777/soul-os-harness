"""
harness/run_tl6.py — TL-6 Multi-Agent Social Lounge Stability 驗證入口

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl6.py

產出:
  data/time_lapse/TL-6/<run_id>/records/ticks.jsonl — canonical evidence
  data/time_lapse/TL-6/<run_id>/derived.json        — derived 指標
  data/time_lapse/TL-6/summary.json                 — 實驗總結
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tl6")


def main() -> int:
    from harness.tl6 import TL6Runner

    runner = TL6Runner(repo_root=REPO_ROOT, seed=42)
    logger.info("TL-6 實驗開始: 多 Agent 客廳社交情境穩定性與身份隔離驗證 (3 runs)")

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-6" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    summary_data = {
        "experiment_id": result["experiment_id"],
        "n_runs": result["n_runs"],
        "all_passed": result["all_passed"],
        "zero_mutation_ok": result["zero_mutation_ok"],
        "determinism_ok": result["determinism_ok"],
        "derived": [
            {
                "run_id": r["run_id"],
                "anti_storm_passed": r["derived"].anti_storm_passed,
                "identity_quarantine_passed": r["derived"].identity_quarantine_passed,
                "privacy_gate_passed": r["derived"].privacy_gate_passed,
                "ambient_salience_passed": r["derived"].ambient_salience_passed,
                "total_ticks": r["derived"].total_ticks,
                "quarantine_leaks": r["derived"].quarantine_leaks,
                "privacy_leaks": r["derived"].privacy_leaks,
                "summary": r["derived"].summary,
            }
            for r in result["runs"]
        ],
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 64)
    print("  TL-6 多 Agent 客廳社交情境驗證總結")
    print("=" * 64)
    r1 = result["runs"][0]["derived"]
    print(f"  Anti-Storm Rate          : {'PASS (100% 無風暴)' if r1.anti_storm_passed else 'FAIL'}")
    print(f"  Identity Quarantine      : {'PASS (0 污染, 他者永不內化)' if r1.identity_quarantine_passed else 'FAIL'}")
    print(f"  Privacy Gate             : {'PASS (0 洩漏, 私聊 100% 隔離)' if r1.privacy_gate_passed else 'FAIL'}")
    print(f"  Ambient Salience         : {'PASS (背景感知與反框架正常)' if r1.ambient_salience_passed else 'FAIL'}")
    print(f"  D2 Determinism           : {'PASS (3 runs 軌跡一致)' if result['determinism_ok'] else 'FAIL'}")
    print(f"  Zero Production Mutation : {'PASS (0 diff)' if result['zero_mutation_ok'] else 'FAIL'}")
    print("=" * 64)
    print(f"  總體判定: {'ALL PASS (全指標綠燈)' if result['all_passed'] else 'FAIL'}\n")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
