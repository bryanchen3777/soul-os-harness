"""
harness/run_tl5.py — TL-5 Long-Range Behavior Distribution 实验脚本 (真实 LLM)

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl5.py

产出:
  data/time_lapse/TL-5/<run_id>/run.json               — run header
  data/time_lapse/TL-5/<run_id>/records/ticks.jsonl   — canonical evidence
  data/time_lapse/TL-5/<run_id>/analysis/*_derived.jsonl — derived 三大指标
  data/time_lapse/TL-5/summary.json                    — 实验总结

说明:
  - 14 天心跳模拟 (每天 08/14/20/23 四 tick + D4 凌晨 3 点), 每 tick 走
    production decide_motive 四元选择 (transmit/observe/reflect/do_nothing)。
  - 三情境: 环境信号 (D2 天晴 / D3 暴雨 / D5 气温骤降) / 关系沉默
    (D6-D8 Bryan 未读未回) / 日夜作息 (深夜 23 点 + 凌晨 3 点)。
  - temperature=0 (TL-0 §5.1), 复用 configs.loader.create_llm_backend。
  - 3 次 runs → D2 determinism 比对 + 0 production mutation 验证。
  - 不改 production / frozen contract (Agency 4 stages / TriggerEnvelope /
    InnerLifeEvent / 4 handlers / SAGE / decision.py 逻辑)。
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Windows 控制台 cp950 无法编码中文 → 强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("tl5")


def _pipeline_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    from configs.loader import load_config

    from harness.runner import make_real_llm_call, snapshot_data_root_hashes, verify_zero_mutation
    from harness.tl5 import TL5Runner, build_tl5_script

    cfg = load_config()
    model = cfg.get("llm", {}).get("model", "unknown")
    llm_call = make_real_llm_call(model=model, temperature=0.0)

    runner = TL5Runner(
        repo_root=REPO_ROOT,
        llm_call=llm_call,
        llm_model=model,
        llm_temperature=0.0,
        pipeline_version=_pipeline_version(),
    )

    script = build_tl5_script()
    logger.info(
        "TL-5 实验开始: model=%s temperature=0.0 ticks=%d days=%d",
        model,
        len(script),
        max(t.day_index for t in script),
    )

    result = runner.run_series(n_runs=3)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-5" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    # 汇总: 三大指标 + 三情境 + determinism + mutation
    first = result["runs"][0]
    diversity = next(
        d for d in first["derived"] if d["metric"] == "behavioral_diversity"
    )
    appropriateness = next(
        d for d in first["derived"] if d["metric"] == "contextual_appropriateness"
    )

    # 三情境结果 (run 1 的 tick 决策, 按 scenario 分组)
    scenarios: dict = {}
    for rec in first["records"]:
        sc = rec.scenario
        scenarios.setdefault(sc, []).append(
            {
                "tick_index": rec.tick_index,
                "day": rec.day_index,
                "hour": rec.hour,
                "decision": rec.decision,
                "reason": rec.decision_reason,
            }
        )

    summary = {
        "experiment_id": "TL-5",
        "fixture_script_ref": "tl5_behavior@v1",
        "seed": 42,
        "llm_model": model,
        "llm_temperature": 0.0,
        "pipeline_version": _pipeline_version(),
        "simulation_days": max(t.day_index for t in script),
        "tick_count": len(script),
        "run_ids": [r["run_id"] for r in result["runs"]],
        "behavioral_diversity": diversity,
        "contextual_appropriateness": appropriateness,
        "determinism": result["determinism"],
        "mutation": result["mutation"],
        "scenarios": scenarios,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 控制台总结
    print("\n" + "=" * 60)
    print("TL-5 Long-Range Behavior Distribution — 结果")
    print("=" * 60)

    print("\n[Behavioral Diversity]")
    print(f"  action_counts: {diversity['action_counts']}")
    print(f"  all_actions_positive: {diversity['all_actions_positive']}")
    print(f"  do_nothing_ratio: {diversity['do_nothing_ratio']} "
          f"(target {diversity['do_nothing_target_range']})")
    print(f"  PASS: {diversity['pass']}")

    print("\n[Contextual Appropriateness]")
    print(f"  observe: {appropriateness['observe']}")
    print(f"  reflect: {appropriateness['reflect']}")
    print(f"  transmit: {appropriateness['transmit']}")
    print(f"  PASS: {appropriateness['pass']}")

    print("\n[三情境]")
    for sc, ticks in scenarios.items():
        decisions = [t["decision"] for t in ticks]
        print(f"  {sc}: {decisions}")

    print("\n[D2 Determinism]")
    print(f"  verdict: {result['determinism']['determinism_verdict']} "
          f"(ticks={result['determinism']['tick_count']}, "
          f"mismatches={result['determinism']['mismatch_count']})")

    print("\n[0 mutation]", "PASS (0 diff)" if result["mutation"]["pass"] else "FAIL")
    if result["mutation"]["diff"]:
        print("  diff:", json.dumps(result["mutation"]["diff"], ensure_ascii=False))
    if result["mutation"]["added"]:
        print("  added:", result["mutation"]["added"])

    print(f"\nsummary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
