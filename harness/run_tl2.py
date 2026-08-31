"""
harness/run_tl2.py — TL-2 Volition Choice Test 实验脚本 (真实 LLM)

用法:
  .\\.venv\\Scripts\\python.exe harness\\run_tl2.py

产出:
  data/time_lapse/TL-2/<run_id>/run.json               — run header
  data/time_lapse/TL-2/<run_id>/records/volition.jsonl — canonical evidence
  data/time_lapse/TL-2/<run_id>/raw/*_decision_prompt.txt — decision prompt 原文
  data/time_lapse/TL-2/<run_id>/analysis/*_derived.jsonl — derived 解析
  data/time_lapse/TL-2/summary.json                    — 实验总结

说明:
  - Control A (scheduler-only) vs Control B (decision 层) 对照。
  - temperature=0 (TL-0 §5.1), 复用 configs.loader.create_llm_backend。
  - 0 production mutation: 跑前跑后 production data_root 逐档 byte-hash 0 diff。
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
logger = logging.getLogger("tl2")


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
    from harness.tl2 import TL2Runner

    cfg = load_config()
    model = cfg.get("llm", {}).get("model", "unknown")
    llm_call = make_real_llm_call(model=model, temperature=0.0)

    runner = TL2Runner(
        repo_root=REPO_ROOT,
        llm_call=llm_call,
        llm_model=model,
        llm_temperature=0.0,
        pipeline_version=_pipeline_version(),
    )

    # 0 mutation 验证: run 前快照
    before = snapshot_data_root_hashes(REPO_ROOT / "data")

    logger.info("TL-2 实验开始: model=%s temperature=0.0", model)
    result = runner.run_once()

    # 0 mutation 验证: run 后重算
    mutation = verify_zero_mutation(REPO_ROOT / "data", before)

    summary_path = REPO_ROOT / "data" / "time_lapse" / "TL-2" / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "experiment_id": "TL-2",
        "fixture_script_ref": "tl2_volition@v1",
        "seed": 42,
        "llm_model": model,
        "llm_temperature": 0.0,
        "pipeline_version": _pipeline_version(),
        "run_id": result["run_id"],
        "run_dir": result["run_dir"],
        "summary": result["summary"],
        "mutation": mutation,
        "evidence": [
            {
                "run_id": r.run_id,
                "candidate_id": r.candidate_id,
                "scenario_name": r.scenario_name,
                "control": r.control,
                "stimulus": r.stimulus,
                "motive_content": r.motive_content,
                "decision_reason": r.decision_reason,
                "transmit": r.transmit,
                "action": r.action,
            }
            for r in result["records"]
        ],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 控制台总结
    s = result["summary"]
    print("\n" + "=" * 60)
    print("TL-2 Volition Choice Test — 结果")
    print("=" * 60)
    print(f"\ncandidates: {s['candidate_count']}")
    print(f"Control A (scheduler-only): send={s['control_a']['send']} "
          f"not_send={s['control_a']['not_send']}")
    print(f"Control B (decision): send={s['control_b']['send']} "
          f"not_send={s['control_b']['not_send']}")
    print(f"transmit 分布: {s['transmit_distribution']}")
    print(f"Decision 层非装饰: {s['decision_layer_not_decoration']}")
    print("\n[not_transmit reasons]")
    for item in s["not_transmit_reasons"]:
        print(f"  {item['candidate_id']}: {item['reason']}")
    print(f"\nreason 引用 context 的 candidate: "
          f"{s['not_transmit_reason_refers_context_ids']}")
    print("\n[0 mutation]", "PASS (0 diff)" if mutation["pass"] else "FAIL")
    if mutation["diff"]:
        print("  diff:", json.dumps(mutation["diff"], ensure_ascii=False))
    if mutation["added"]:
        print("  added:", mutation["added"])
    print(f"\nsummary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
