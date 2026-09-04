"""
harness/run_tl7.py — TL-7 Social Opportunity & Volition Stability 验证入口

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl7.py

产出:
  data/time_lapse/TL-7/<run_id>/records/phases.jsonl — canonical evidence
  data/time_lapse/TL-7/<run_id>/derived.json        — derived 指标
  data/time_lapse/TL-7/summary.json                 — 实验总结
"""
from __future__ import annotations

import json
import logging
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
logger = logging.getLogger("tl7")


def main() -> int:
    from harness.tl7 import TL7Runner

    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    logger.info(
        "TL-7 实验开始: 社交机会生命周期与自主意志稳定性验证 "
        "(话题涌现 → 紧凑感知 → SM-4 留白 → 300s TTL 蒸发 → 0 僵尸回复, 3 runs)"
    )

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-7" / "summary.json"
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
                "ttl_expiration_passed": r["derived"].ttl_expiration_passed,
                "no_cascading_volition_passed": r["derived"].no_cascading_volition_passed,
                "total_phases": r["derived"].total_phases,
                "opportunity_generated": r["derived"].opportunity_generated,
                "zombie_replies": r["derived"].zombie_replies,
                "summary": r["derived"].summary,
            }
            for r in result["runs"]
        ],
    }

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 64)
    print("  TL-7 社交机会生命周期与自主意志稳定性验证总结")
    print("=" * 64)
    r1 = result["runs"][0]["derived"]
    print(f"  TTL Expiration Invariant : {'PASS (100% 蒸发, 0 遗留)' if r1.ttl_expiration_passed else 'FAIL'}")
    print(f"  No Cascading Volition    : {'PASS (0 自动连锁抢话)' if r1.no_cascading_volition_passed else 'FAIL'}")
    print(f"  D2 Determinism           : {'PASS (3 runs 轨迹一致)' if result['determinism_ok'] else 'FAIL'}")
    print(f"  Zero Production Mutation : {'PASS (0 diff)' if result['zero_mutation_ok'] else 'FAIL'}")
    print(f"  机会生成                 : {r1.opportunity_generated} 笔 (TTL=300s)")
    print(f"  僵尸回复                 : {r1.zombie_replies} 笔")
    print("=" * 64)
    print(f"  总体判定: {'ALL PASS (全指标绿灯)' if result['all_passed'] else 'FAIL'}\n")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
