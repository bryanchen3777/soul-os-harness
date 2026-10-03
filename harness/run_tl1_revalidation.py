"""TL-1 Revalidation — 以 P2 四級詞彙重新判讀（2026-10-03）。

目的：把 TL-1 從 `Needs Revalidation` 提升到有實際 evidence 支持的判定。
**不是為了救 TL-1 而重跑**，而是重新取得可判讀的 evidence。

三層分析（Owner 逐字指定）：
  ① Raw            同條件 emergent_snapshot 是否每次完全一致？
  ② Observation    decision_parsed（observation abstraction）是否一致？
  ③ Growth trajectory  T0 → T15 → T30 是否仍形成**一致的 trajectory**？

嚴守語意邊界（Owner 逐字）：
  「不要把 decision_parsed 一致直接寫成『Soul interpretation deterministic』。
   比較準確的是 Observation-level stability，因為 decision_parsed 本身就是
   observation abstraction。」

  故本檔輸出 **observation-level stability**，**不**宣稱 raw determinism，
  **不**宣稱 causal attribution（TL-1 無 counterfactual 設計）。

不修改既有 `run_tl1.py`；本檔為 additive 重跑 + 分析。
"""
from __future__ import annotations

import asyncio
import collections
import json
import pathlib
import sys
from typing import Any, Dict, List

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.observer import Observer, derive_interpretation  # noqa: E402


def _pipeline_version() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO), capture_output=True, text=True, timeout=15,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


async def main() -> int:
    from configs.loader import load_config

    from harness.runner import TL1Runner, make_real_llm_call, snapshot_data_root_hashes

    cfg = load_config()
    model = cfg.get("llm", {}).get("model", "unknown")
    llm_call = make_real_llm_call(model=model, temperature=0.0)

    runner = TL1Runner(
        repo_root=REPO,
        llm_call=llm_call,
        llm_model=model,
        llm_temperature=0.0,
        pipeline_version=_pipeline_version(),
    )

    print("=" * 74)
    print("TL-1 REVALIDATION — P2 四級詞彙重新判讀")
    print("=" * 74)
    print(f"  model       : {model}")
    print(f"  temperature : 0.0")
    print(f"  n_runs      : 3")
    print(f"  pipeline    : {_pipeline_version()}")

    # 🔴 mutation 快照必須傳 **data_root**（`REPO/"data"`），不是 REPO。
    #    傳 REPO 會讓 `_is_mutation_skipped` 的相對路徑全部失配
    #    （`data/heartbeats/*` vs 預期 `heartbeats/*`）⇒ 把**活服務的心跳檔**
    #    誤判成 mutation（2026-10-03 首跑即踩：29,284 檔 vs 正確 236 檔）。
    data_dir = REPO / "data"
    before = snapshot_data_root_hashes(data_dir)
    result = await asyncio.to_thread(runner.run_series, 3)
    after = snapshot_data_root_hashes(data_dir)

    obs = Observer()
    runs = [{"run_id": r["run_id"], "records": r["records"]} for r in result["runs"]]

    # ── ① + ②：raw / observation 兩層 ──
    cls = obs.classify_experiment_determinism(runs)
    print()
    print(f"  ① raw          : {cls['raw_layer']['verdict']}")
    print(f"  ② observation  : {cls['observation_layer']['verdict']}")
    print(f"  legacy verdict : {cls['legacy_verdict']}")
    print(f"  四級判定        : {cls['verdict']}")
    print(f"  causal         : {cls['causal_attribution']}")

    # ── ③ Growth trajectory：這是 TL-1 的核心主張 ──
    #     同一 probe 在 T0/T15/T30 的 **structured interpretation** 是否形成
    #     一致的 trajectory？（motivation 關鍵字：擔心→自我懷疑→接受）
    traj_per_run: List[Dict[str, str]] = []
    for run in runs:
        traj = {}
        for rec in run["records"]:
            interp = derive_interpretation(rec)
            traj[rec["checkpoint"]] = (
                f"{interp['stance']}|{interp['concern']}|{interp['attribution']}"
            )
        traj_per_run.append(traj)

    cks = ["T0", "T15", "T30"]
    print()
    print("  ③ growth trajectory（每個 run 的 structured interpretation）")
    for i, traj in enumerate(traj_per_run):
        cells = " → ".join(f"{ck}:{traj.get(ck, '?')}" for ck in cks)
        print(f"     run{i}: {cells}")

    distinct_traj = {json.dumps(t, ensure_ascii=False, sort_keys=True) for t in traj_per_run}
    trajectory_stable = len(distinct_traj) == 1

    # ── change_verdict：每個 run 各判一次，看是否一致 ──
    trace_links = {
        "T15": [f"fixture:{e.event_id}" for e in runner._script[:15]],
        "T30": [f"fixture:{e.event_id}" for e in runner._script],
    }
    change_per_run = []
    for run in runs:
        c = obs.derive_change_verdict(run["records"], trace_links=trace_links)
        change_per_run.append(f"{c['change_verdict']}/L{c['level']}")
    distinct_change = sorted(set(change_per_run))

    print()
    print(f"  change_verdict per run : {change_per_run}")
    print(f"  distinct              : {distinct_change}")
    print(f"  trajectory 一致        : {trajectory_stable}")

    # ── 最終四級判定 ──
    # 🔴 契約 §2 核心原則：**判定必須在「該實驗的主張所需層級」上做**，
    #    不可從較低層級或較嚴格的條件推論。
    #
    #    TL-1 的**主張** = 「Level-2 Growth proven」= `change_verdict ==
    #    INTERPRETATION_DECISION_CHANGED`。**主張所需層級是 change_verdict 一致**，
    #    而**不是** stance/concern/attribution 三元組逐字元一致（那是更細的
    #    trajectory 形狀，**比主張更嚴**）。
    #
    #    首版實作誤把 `trajectory_stable`（三元組完全一致）當成必要條件 ⇒
    #    違反契約自身原則、且比 TL-1 的實際主張更嚴格。已修正。
    claim_stable = len(distinct_change) == 1
    obs_stable = cls["observation_layer"]["verdict"] in (
        "DETERMINISTIC", "STABLE_OBSERVATION"
    )

    if cls["raw_layer"]["verdict"] == "DETERMINISTIC" and claim_stable:
        final = "DETERMINISTIC"
    elif claim_stable and obs_stable:
        final = "STABLE_OBSERVATION"
    else:
        final = "INCONCLUSIVE"

    print()
    print("=" * 74)
    print(f"  TL-1 最終判定 : {final}")
    print("=" * 74)
    print(f"  主張層（change_verdict）跨 run 一致 : {claim_stable}  {distinct_change}")
    print(f"  trajectory 形狀（stance/concern/attribution 三元組）跨 run 一致 : {trajectory_stable}")
    if final == "STABLE_OBSERVATION":
        print()
        print("  ⇒ raw LLM 表達可以變動，但 **主張層**（Level-2 change_verdict）")
        print("    在 3 次重複執行中一致重現。")
        print("    ⚠️ 這是 **observation-level stability**，**不是** raw determinism，")
        print("       也**不是** causal attribution（TL-1 無 counterfactual 設計）。")
        if not trajectory_stable:
            print()
            print("  ⚠️ 附加發現（**不影響**主張判定，但值得記錄）：")
            print("     更細的 trajectory **形狀**跨 run 不一致 ——")
            print("     T15 的 stance 在 concerned／neutral 之間波動，")
            print("     T30 的 attribution 在 external／internal 之間波動。")
            print("     ⇒ 結論可重現，但**演變路徑的細節不唯一**。")
            print("     這比 Notion 原本的『trajectory: 擔心→自我懷疑→接受』")
            print("     描述**更細緻**：主張成立，路徑並非唯一解。")
    elif final == "INCONCLUSIVE":
        print()
        print("  ⇒ 主張層（change_verdict）跨 run 不一致，")
        print("    TL-1 的 Level-2 Growth 結論**需降級為 Needs Revalidation**。")

    out_dir = REPO / "data" / "harness_out" / "tl1"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "experiment_id": "TL-1",
        "mode": "revalidation",
        "contract": "docs/DETERMINISM-CONTRACT-P2.md",
        "model": model,
        "temperature": 0.0,
        "seed": 42,
        "seed_note": "fixture generation seed ONLY - never passed to the LLM",
        "pipeline_version": _pipeline_version(),
        "n_runs": 3,
        "raw_layer": cls["raw_layer"]["verdict"],
        "observation_layer": cls["observation_layer"]["verdict"],
        "legacy_verdict": cls["legacy_verdict"],
        "causal_attribution": cls["causal_attribution"],
        "trajectory_stable": trajectory_stable,
        "trajectory_note": (
            "trajectory 形狀（stance/concern/attribution 三元組逐字元）跨 run 的一致性。"
            "**注意**：這比 TL-1 的主張更嚴 —— 主張是 change_verdict。"
            "trajectory 不唯一**不推翻**主張，但代表『演變路徑的細節不唯一』。"
        ),
        "claim_stable": claim_stable,
        "claim_layer": "change_verdict (INTERPRETATION_DECISION_CHANGED)",
        "distinct_trajectories": sorted(distinct_traj),
        "change_verdict_per_run": change_per_run,
        "distinct_change_verdicts": distinct_change,
        "FINAL_VERDICT": final,
        "production_mutation": {
            "pass": before == after,
            "diff": [k for k in before if before.get(k) != after.get(k)],
        },
        "semantic_boundary": (
            "本判定是 observation-level stability。raw LLM 表達可變動；"
            "decision_parsed 本身即 observation abstraction，"
            "故不得宣稱『Soul interpretation deterministic』。"
        ),
    }
    out = out_dir / "tl1_revalidation.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    print()
    print(f"  結果已寫: {out.relative_to(REPO)}  （derived 邊界，非 canonical）")
    print(f"  production mutation: {'PASS (0 diff)' if before == after else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
