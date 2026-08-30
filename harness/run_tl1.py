"""
harness/run_tl1.py — TL-1 实验脚本 (真实 LLM, temperature=0, 连跑 3 次)

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl1.py

产出:
  data/time_lapse/TL-1/<run_id>/run.json          — run header
  data/time_lapse/TL-1/<run_id>/records/T*.jsonl  — canonical probe records
  data/time_lapse/TL-1/<run_id>/raw/               — LLM 原文
  data/time_lapse/TL-1/<run_id>/analysis/           — derived 解析
  data/time_lapse/TL-1/series_summary.json         — 系列总结 (determinism + mutation)

0 production mutation: 跑前跑后 production data_root 逐档 byte-hash 0 diff。
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
logger = logging.getLogger("tl1")


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

    from harness.runner import TL1Runner, make_real_llm_call

    cfg = load_config()
    model = cfg.get("llm", {}).get("model", "unknown")
    llm_call = make_real_llm_call(model=model, temperature=0.0)

    runner = TL1Runner(
        repo_root=REPO_ROOT,
        llm_call=llm_call,
        llm_model=model,
        llm_temperature=0.0,
        pipeline_version=_pipeline_version(),
    )

    logger.info("TL-1 实验开始: model=%s temperature=0.0 n_runs=3", model)
    result = runner.run_series(n_runs=3)

    # change_verdict (跨 checkpoint 比对, §8 Level 0-3) — 用第一个 run 判定
    from harness.observer import Observer

    obs = Observer()
    first_run = result["runs"][0]
    change = obs.derive_change_verdict(
        first_run["records"],
        trace_links={
            "T15": [f"fixture:{e.event_id}" for e in runner._script[:15]],
            "T30": [f"fixture:{e.event_id}" for e in runner._script],
        },
    )

    # 系列总结
    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-1" / "series_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment_id": "TL-1",
        "fixture_script_ref": "tl1_script@v1",
        "seed": 42,
        "llm_model": model,
        "llm_temperature": 0.0,
        "pipeline_version": _pipeline_version(),
        "runs": [
            {
                "run_id": r["run_id"],
                "records": [
                    {
                        "checkpoint": rec["checkpoint"],
                        "sim_ts": rec["sim_ts"],
                        "experience_event_count": rec["experience_event_count"],
                        "motive_text": rec["motive_text"],
                        "decision_text": rec["decision_text"],
                        "reached_action": rec["reached_action"],
                    }
                    for rec in r["records"]
                ],
            }
            for r in result["runs"]
        ],
        "determinism": result["determinism"],
        "mutation": result["mutation"],
        "change_verdict": change,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 控制台总结
    print("\n" + "=" * 60)
    print("TL-1 Same-Stimulus Longitudinal Growth Test — 结果")
    print("=" * 60)
    for r in result["runs"]:
        print(f"\nrun {r['run_id'][:12]}...")
        for rec in r["records"]:
            print(
                f"  {rec['checkpoint']} ({rec['sim_ts']}) "
                f"events={rec['experience_event_count']} "
                f"motive={'Y' if rec['motive_text'] else 'N'} "
                f"action={rec['reached_action']}"
            )
    print("\n[determinism]", result["determinism"]["determinism_verdict"])
    print("[matrix]", json.dumps(result["determinism"]["matrix"], ensure_ascii=False))
    print("[change_verdict]", change["change_verdict"], f"(Level {change['level']})")
    print("[mutation]", "PASS (0 diff)" if result["mutation"]["pass"] else "FAIL")
    if result["mutation"]["diff"]:
        print("  diff:", json.dumps(result["mutation"]["diff"], ensure_ascii=False))
    if result["mutation"]["added"]:
        print("  added:", result["mutation"]["added"])
    print(f"\nsummary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
