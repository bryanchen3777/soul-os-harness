"""
harness/run_tl10.py — TL-10 Relational Expression End-to-End 验证入口

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl10.py

产出:
  data/time_lapse/TL-10/<scenario>/<run_id>/derived.json — 单场景派生指标
  data/time_lapse/TL-10/summary.json                      — 实验总结 (series)

验收判定 (Owner 拍板, 不可减弱):
  剧本 1 A2A 公开分流: resolve_proactive_delivery(agent) → {"mode": "group"} 0 穿透 Bryan;
  剧本 2 带差异化注入: stranger/familiar 标签逐字 + tokens ≤ 80 + stranger 也注入;
  剧本 3 A2U 保全: bryan/user_bryan 归一化 + private/telegram/Bry chat_id 100% 原状 + None 兼容;
  剧本 4 Fail-Safe: 无记录/非法 target/读取异常 → "" 0 崩溃; resolve 未知 → private 不报错。
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
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tl10")


def _print_checks(indent: str, checks: dict) -> None:
    for key in sorted(checks):
        mark = "PASS" if checks[key] else "FAIL"
        print(f"{indent}  [{mark}] {key}")


def main() -> int:
    from harness.tl10 import SCENARIO_LABELS, SCENARIOS, TL10Runner

    runner = TL10Runner(repo_root=REPO_ROOT, seed=42)
    logger.info(
        "TL-10 实验开始: 关系表达端到端实证 (C-3.1 验收钢印) — "
        "四大剧本 × 3 runs + D2 重现 + 0 production mutation"
    )

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-10" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 78)
    print("  TL-10 关系表达端到端完整验收报告 (C-3.1 钢印)")
    print("=" * 78)
    # 逐剧本细目: 重新跑单个剧本取 checks (series 只存判定; 细目取 run_1 记录)
    for s in result["scenarios"]:
        print(f"\n■ {s['scenario']} — {SCENARIO_LABELS[s['scenario']]}")
        print(f"  判定: {'ALL PASS' if s['all_passed'] else 'FAIL'}  "
              f"(3 runs 判定一致={s['determinism_ok']}, per-run={s['per_run_passed']})")
        if s["all_passed"]:
            out = runner.run_scenario(s["scenario"], run_id="report_detail")
            d = out["derived"]
            print("  硬断言细目:")
            _print_checks("  ", d.checks)
            print(f"  关键输出: {d.key_numbers}")
            print(f"  摘要: {d.summary}")
    print("\n" + "-" * 78)
    print(f"  {'D2 宏确定性 (判定一致)':<34}: "
          f"{'PASS' if all(s['determinism_ok'] for s in result['scenarios']) else 'FAIL'}")
    print(f"  {'Zero Production Mutation':<34}: "
          f"{'PASS (0 diff)' if result['zero_mutation_ok'] else 'FAIL'}")
    print("=" * 78)
    print(f"  总体判定: {'ALL PASS (四大剧本 + D2 重现全绿)' if result['all_passed'] else 'FAIL'}\n")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())