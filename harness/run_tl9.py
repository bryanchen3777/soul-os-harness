"""
harness/run_tl9.py — TL-9 Relation Evolution Long-Horizon 验证入口

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl9.py

产出:
  data/time_lapse/TL-9/<scenario>/<run_id>/records/bands.jsonl — canonical evidence
  data/time_lapse/TL-9/<scenario>/<run_id>/derived.json        — 单场景派生指标
  data/time_lapse/TL-9/summary.json                            — 实验总结 (series)
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
logger = logging.getLogger("tl9")


def main() -> int:
    from harness.tl9 import SCENARIO_LABELS, TL9Runner

    runner = TL9Runner(repo_root=REPO_ROOT, seed=42)
    logger.info(
        "TL-9 实验开始: 关系演化端到端长程实证 (C-3 闭环钢印) — "
        "四大剧本 × 3 runs + D2 重现 + 0 production mutation"
    )

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-9" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 72)
    print("  TL-9 关系演化端到端长程实证总结 (C-3 闭环钢印)")
    print("=" * 72)
    for s in result["scenarios"]:
        print(f"  {s['scenario']:<22} : {'ALL PASS' if s['all_passed'] else 'FAIL'}"
              f"  (3 runs 轨迹一致={s['determinism_ok']}, "
              f"per-run={s['per_run_passed']})")
    print(f"  {'D2 宏确定性 (decision/band 轨迹)':<28} : "
          f"{'PASS' if all(s['determinism_ok'] for s in result['scenarios']) else 'FAIL'}")
    print(f"  {'Zero Production Mutation':<28} : "
          f"{'PASS (0 diff)' if result['zero_mutation_ok'] else 'FAIL'}")
    print("=" * 72)
    print(f"  总体判定: {'ALL PASS (四大剧本 + D2 重现全绿)' if result['all_passed'] else 'FAIL'}\n")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())