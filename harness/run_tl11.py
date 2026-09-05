"""
harness/run_tl11.py — TL-11 Commitment Closure + Periodic Narrative End-to-End 验证入口

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl11.py

产出:
  data/time_lapse/TL-11/<scenario>/<run_id>/derived.json — 单场景派生指标
  data/time_lapse/TL-11/summary.json                      — 实验总结 (series)

验收判定（契约 docs/C-2.1-COMMITMENT-AND-NARRATIVE-CONTRACT.md §8, 不可减弱）:
  A1 承诺状态转移闭环: B1→ACTIVE→推进→COMPLETED+sediment / timeout→ABANDONED, 0 直发;
  A2 反馈走 volition path: B6 产关怀 goal → 候选池 → 四元 Decision → 仅既有 publish 链;
  A3 周记频率: 同 ISO 周 0 二次沉淀, 跨周 1 次, 非每日产物;
  A4 0 新定时器: scheduler/叙事/种子模块静态 AST 断言;
  A5 0 直写 facts: 沉淀后 SAGE facts 表计数 0 + 静态反例路径不存在;
  A6 身份防火墙: 他者 diary/trace 0 内化, 沉淀事件 actor_id==self;
  A7 D2 确定性: 同剧本连跑 3 次判定一致 + production data_root 0 mutation。
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
logger = logging.getLogger("tl11")

# A1-A7 断言来源映射: 每项 → (来源层, check key 前缀列表)
ASSERTION_MAP = {
    "A1": ("b6_closure (epoch_completed + epoch_abandoned)", ["a1_"]),
    "A2": ("b6_closure (双 epoch) + 静态", ["a2_"]),
    "A3": ("weekly", ["a3_"]),
    "A4": ("全局静态断言 (static_assertions)", ["a4_"]),
    "A5": ("memorial + identity_firewall + 静态", ["a5_", "a6_facts_zero_after_sediment"]),
    "A6": ("identity_firewall + memorial", ["a6_"]),
}


def _print_checks(indent: str, checks: dict) -> None:
    for key in sorted(checks):
        mark = "PASS" if checks[key] else "FAIL"
        print(f"{indent}  [{mark}] {key}")


def _per_scenario_checks(scenario: str) -> Dict[str, bool]:
    """取 run_1 的 checks（细目展示; run_series 只存判定）。"""
    from harness.tl11 import TL11Runner
    runner = TL11Runner(repo_root=REPO_ROOT, seed=42)
    out = runner.run_scenario(scenario, run_id="report_detail")
    return out["derived"].checks


def main() -> int:
    from harness.tl11 import (
        SCENARIO_LABELS,
        SCENARIOS,
        TL11Runner,
    )

    runner = TL11Runner(repo_root=REPO_ROOT, seed=42)
    logger.info(
        "TL-11 实验开始: 承諾閉環 + 週期敘事端到端实证 (C-2.1 验收钢印) — "
        "四大剧本 × 3 runs + D2 重现 + A4 静态 + 0 production mutation"
    )

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-11" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    static_ok = result["static_ok"]
    if not static_ok:
        # 细目: 静态断言失败的键
        failed_static = [k for k, v in result["static_assertions"].items() if not v]
        print(f"  ⚠ 静态断言失败: {failed_static}")

    print("\n" + "=" * 78)
    print("  TL-11 承諾閉環 + 週期敘事端到端完整验收报告 (C-2.1 钢印)")
    print("=" * 78)

    # ── 逐剧本细目 ──
    detail_checks: Dict[str, Dict[str, bool]] = {}
    for s in result["scenarios"]:
        print(f"\n■ {s['scenario']} — {SCENARIO_LABELS[s['scenario']]}")
        print(f"  判定: {'ALL PASS' if s['all_passed'] else 'FAIL'}  "
              f"(3 runs 判定一致={s['determinism_ok']}, per-run={s['per_run_passed']})")
        if s["all_passed"]:
            checks = _per_scenario_checks(s["scenario"])
            detail_checks[s["scenario"]] = checks
            print(f"  硬断言细目 ({len(checks)} 项):")
            _print_checks("  ", checks)
        print(f"  摘要: {s['summary']}")

    # ── A1-A7 逐项判定 ──
    print("\n" + "-" * 78)
    print("  七项硬断言逐项判定 (契约 §8)")
    print("-" * 78)

    # 收集全部 checks（脚本本 run 的 run_1 判定 + 静态断言）
    all_check_map: Dict[str, Dict[str, bool]] = dict(detail_checks)
    all_check_map["__static__"] = result["static_assertions"]

    a_results: Dict[str, bool] = {}
    for assertion, (source, prefixes) in ASSERTION_MAP.items():
        keys = []
        for scenario_, checks_ in all_check_map.items():
            for k, v in checks_.items():
                if any(k.startswith(p) for p in prefixes):
                    keys.append((scenario_, k, v))
        ok = all(v for _, _, v in keys)
        a_results[assertion] = ok
        print(f"  {assertion}  {_A_LABELS[assertion]:<28}: "
              f"{'PASS' if ok else 'FAIL'}  "
              f"[{source}, {len(keys)} 项断言]")
        if not ok:
            failed = [f"{scenario_}:{k}" for scenario_, k, v in keys if not v]
            print(f"       └─ 失败项: {failed}")

    # A7: run_series 全局判定（D2 确定性 + 0 mutation）
    a7_ok = (
        all(s["determinism_ok"] for s in result["scenarios"])
        and result["zero_mutation_ok"]
    )
    a_results["A7"] = a7_ok
    print(f"  A7  D2 确定性 (3 runs 判定一致 + 0 mutation)  : "
          f"{'PASS' if a7_ok else 'FAIL'}  "
          f"[run_series 全局, determinism={[s['determinism_ok'] for s in result['scenarios']]}]")

    # ── 汇总 ──
    print("\n" + "-" * 78)
    print(f"  {'A4 静态断言':<36}: "
          f"{'PASS (全部)' if static_ok else 'FAIL'}")
    print(f"  {'D2 宏确定性 (3 runs 判定一致)':<36}: "
          f"{'PASS' if all(s['determinism_ok'] for s in result['scenarios']) else 'FAIL'}")
    print(f"  {'Zero Production Mutation':<36}: "
          f"{'PASS (0 diff)' if result['zero_mutation_ok'] else 'FAIL'}")
    print(f"  {'mutation_diff':<36}: {result['mutation_diff']}")
    print(f"  {'mutation_added':<36}: {result['mutation_added']}")
    print("=" * 78)
    all_ok = result["all_passed"] and all(a_results.values())
    print(f"  七项硬断言: {a_results}")
    print(f"  总体判定: {'ALL PASS (A1-A7 全绿)' if all_ok else 'FAIL'}\n")

    return 0 if all_ok else 1


_A_LABELS = {
    "A1": "承诺状态转移闭环 (COMPLETED+sediment / ABANDONED)",
    "A2": "反馈走 volition path 不直发 (0 bypass)",
    "A3": "周记频率 (ISO 周判据 + 幂等 + 非每日)",
    "A4": "0 新定时器 (AST 静态断言)",
    "A5": "0 直写 facts (沉淀后 facts 表计数 0)",
    "A6": "身份防火墙 (他者 0 内化 + actor_id==self)",
}


if __name__ == "__main__":
    sys.exit(main())