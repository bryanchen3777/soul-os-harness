"""SOEB 合併：把多個「分模型」執行目錄併成一個可盲評的集合。

為什麼需要它
------------
一次跑 6 顆模型 × 10 呼叫，在推理模型上會變成數小時的單一命令；
一旦命令被逾時中止，`runner.py` 在**結束時**才寫檔，全部觀測會一起消失。
所以實務做法是**按模型拆成多個背景任務**、各自寫自己的 run dir。

但盲評要的是「一個 shuffled 集合」，而且必須保證被併進來的每一份
**規格完全相同**——否則拿不同 spec／不同 prompt／不同預算跑出來的結果
被併在一起，排名會是假的。

守門（不可漂移）
----------------
合併前逐欄位比對，任何一項不一致就**拒絕合併**（不留「警告但照併」的選項）：
`suite` / `spec_sha256` / `system_prompt_sha256` / `budget_override` / `tasks`。

用法
----
    .venv\\Scripts\\python.exe scripts\\soeb\\merge.py ^
        --out tests\\results\\soeb\\v2 \\
        tests\\results\\soeb\\v2\\glm-5.3-flash ^
        tests\\results\\soeb\\v2\\qwen3.8-flash

輸出不覆寫來源目錄：`--out` 不可以是任何來源目錄之一。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: 這些欄位一旦不同，就代表兩份觀測不可比，必須拒絕合併。
INVARIANT_KEYS = (
    "suite",
    "spec_sha256",
    "system_prompt_sha256",
    "budget_override",
    "tasks",
)


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _load_run(run_dir: Path) -> tuple[dict, list[dict]]:
    mpath = run_dir / "manifest.json"
    opath = run_dir / "observations.jsonl"
    if not mpath.is_file():
        raise FileNotFoundError(f"{run_dir} 缺少 manifest.json")
    if not opath.is_file():
        raise FileNotFoundError(f"{run_dir} 缺少 observations.jsonl")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    obs = [
        json.loads(line)
        for line in opath.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, obs


def _check_invariants(base: dict, other: dict, base_dir: Path, other_dir: Path) -> None:
    for key in INVARIANT_KEYS:
        if base.get(key) != other.get(key):
            raise ValueError(
                f"不可合併：{other_dir.name} 的 `{key}` 與 {base_dir.name} 不同。\n"
                f"  基準 = {json.dumps(base.get(key), ensure_ascii=False)}\n"
                f"  其他 = {json.dumps(other.get(key), ensure_ascii=False)}\n"
                "這些欄位不同就代表兩份觀測不可比，合併後的排名會是假的。"
            )


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="合併多個 SOEB 執行目錄（守門嚴格）")
    ap.add_argument("--out", required=True, help="合併後的輸出目錄")
    ap.add_argument("runs", nargs="+", help="至少兩個來源 run dir")
    args = ap.parse_args(argv)

    out_dir = Path(args.out).resolve()
    src_dirs = [Path(p).resolve() for p in args.runs]
    if len(src_dirs) < 2:
        print("Error: 至少需要兩個來源目錄（單一目錄不需要合併）", file=sys.stderr)
        return 2
    for d in src_dirs:
        if d == out_dir:
            print(f"Error: --out 不可等於來源目錄 {d}", file=sys.stderr)
            return 2
        if not d.is_dir():
            print(f"Error: 來源目錄不存在 {d}", file=sys.stderr)
            return 2

    try:
        loaded = []
        base_manifest, _ = _load_run(src_dirs[0])
        for d in src_dirs:
            m, o = _load_run(d)
            _check_invariants(base_manifest, m, src_dirs[0], d)
            loaded.append((d, m, o))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # 模型不可重複：同一個模型出現在兩個目錄裡，代表「同一題被跑了兩次」，
    # 直接併會讓該模型的每一筆都出現兩遍，統計會失真。
    seen: dict[str, Path] = {}
    dupes = []
    for d, _m, obs in loaded:
        for mid in {str(o.get("model_id")) for o in obs}:
            if mid in seen:
                dupes.append((mid, seen[mid].name, d.name))
            else:
                seen[mid] = d
    if dupes:
        print("Error: 有模型重複出現在多個來源目錄，拒絕合併：", file=sys.stderr)
        for mid, a, b in dupes:
            print(f"  {mid}: {a} / {b}", file=sys.stderr)
        return 2

    merged_obs: list[dict] = []
    channels: dict[str, dict] = {}
    excluded: list[str] = []
    for _d, m, obs in loaded:
        merged_obs.extend(obs)
        channels.update(m.get("channels") or {})
        for mid in m.get("excluded_models") or []:
            if mid not in excluded:
                excluded.append(mid)

    ok_count = sum(1 for o in merged_obs if o.get("ok"))
    merged = {
        "runner": "soeb",
        "merged_from": [str(d) for d, _m, _o in loaded],
        "spec_path": base_manifest.get("spec_path"),
        "spec_sha256": base_manifest.get("spec_sha256"),
        "suite": base_manifest.get("suite"),
        "started_at": min(m.get("started_at") or "" for _d, m, _o in loaded),
        "finished_at": max(m.get("finished_at") or "" for _d, m, _o in loaded),
        "models": sorted(seen),
        "excluded_models": excluded,
        "tasks": base_manifest.get("tasks"),
        "context_packet": base_manifest.get("context_packet"),
        "budget_override": base_manifest.get("budget_override"),
        "system_prompt_sha256": base_manifest.get("system_prompt_sha256"),
        "system_prompt_chars": base_manifest.get("system_prompt_chars"),
        "system_prompt_source": base_manifest.get("system_prompt_source"),
        "total_calls": len(merged_obs),
        "ok_calls": ok_count,
        "failed_calls": len(merged_obs) - ok_count,
        "raw_saved": all(m.get("raw_saved") for _d, m, _o in loaded),
        "channel": base_manifest.get("channel"),
        "channels": channels,
        "mixed_channels": len({v.get("endpoint") for v in channels.values()}) > 1,
        "registry_version": base_manifest.get("registry_version"),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "observations.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for rec in merged_obs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(out_dir / "manifest.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)

    print(f"合併 {len(loaded)} 個 run dir → {out_dir}")
    print(f"  模型  : {merged['models']}")
    print(f"  觀測  : {merged['total_calls']} 筆（ok {ok_count} / 失敗 {merged['failed_calls']}）")
    print(f"  規格  : spec {str(merged['spec_sha256'])[:12]}… "
          f"prompt {str(merged['system_prompt_sha256'])[:12]}…")
    print(f"  混通道: {merged['mixed_channels']}（True 代表有模型走不同端點，報告必須揭露）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
