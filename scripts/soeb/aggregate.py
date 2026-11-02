# -*- coding: utf-8 -*-
"""soeb.aggregate — 把觀測列聚合成「機械指標」（不需要評分者）。

為什麼要有這一支
================
SOEB 的 41 個 checkpoint 裡只有 4 個能用 `check_mech.py` 靜態判定，其餘要
**語意判定** ⇒ 需要一個非候選的評分者。但「可評分性、token 用量、截斷證據、
真實計費」這些**機械事實**不需要評分者，可以先算、先交付。

⇒ 本模組刻意**不碰分數**：它只回答「這一批資料值不值得評、花了多少錢」。

三條誠實性規則
==============
1. **不可評分 ≠ 0 分**：可用性用 `diagnose.classify()` 的三態，不做任何加權。
2. **未知計費顯示 n/a，不顯示 0**：Ollama 通道不回 `list_cost`；填 0 會讓
   「這顆免費」變成一個沒人查證過的結論。
3. **混通道必須逐顆揭露**：`channel` 欄直接寫出來，不讓讀者以為六顆同一條路。
4. **殘缺 run 不得看起來像完整 run**：`diagnose.classify()` 只判「這幾列可不可用」，
   它不知道**應該有幾列**。被中止的 run 會有 5/5 全 USABLE，讀起來比 10/8 還漂亮。
   ⇒ 本模組用 spec 的 `runs` 算出**應有呼叫數**，不符就標 `INCOMPLETE`。

用法
====::

    python scripts/soeb/aggregate.py --run-root tests/results/soeb/v2
    python scripts/soeb/aggregate.py --run-root tests/results/soeb/v2 --tasks scripts/soeb/tasks/soeb-v1.json --json m.json --md m.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # 允許 `python scripts/soeb/aggregate.py` 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.soeb.diagnose import classify


def _force_utf8_stdio() -> None:
    """cp950 主控台遇到 `⇒`／`—` 會 UnicodeEncodeError，先改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _iter_observations(path: Path):
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            obs = json.loads(line)
        except json.JSONDecodeError:
            print(f"  ⚠ {path}:{lineno} 不是合法 JSON，跳過", file=sys.stderr)
            continue
        if isinstance(obs, dict):
            yield obs


def discover_runs(run_root: Path) -> dict[str, Path]:
    """回傳 `{model_dir_name: observations.jsonl}`。

    支援兩種佈局（與 `build_worksheet.discover_runs` 一致）：
    彙總根（子目錄各一模型）／單一 run 目錄（`observations.jsonl` 在根上）。
    """
    if not run_root.exists():
        raise FileNotFoundError(f"找不到 run 根目錄：{run_root}")
    direct = run_root / "observations.jsonl"
    if direct.exists():
        return {run_root.name: direct}
    out: dict[str, Path] = {}
    for child in sorted(run_root.iterdir()):
        cand = child / "observations.jsonl"
        if child.is_dir() and cand.exists():
            out[child.name] = cand
    return out


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def summarize(observations_path: Path, expected: dict[str, int] | None = None) -> dict[str, Any]:
    """單一模型的機械摘要。不含任何分數。

    ``expected``：``{task_id: 應有呼叫數}``（來自 spec 的 ``runs``）。
    給了就做完整性對帳 —— 沒有它，「5 列全 USABLE」會被讀成「乾淨跑完」，
    但它其實可能是被中止的殘骸。
    """
    obs_list = list(_iter_observations(observations_path))

    states: Counter[str] = Counter()
    finishes: Counter[str] = Counter()
    tasks: list[dict[str, Any]] = []
    channels: set[str] = set()
    protocols: set[str] = set()
    list_cost_total = 0.0
    list_cost_known = 0
    out_tokens_total = 0
    out_tokens_known = 0
    reasoning_total = 0
    reasoning_known = 0
    latencies: list[int] = []
    effort_values: set[str] = set()

    for obs in obs_list:
        info = classify(obs)
        state = info.get("verdict", "ERROR")
        states[state] += 1
        finish = (obs.get("finish_reason") or "") or "(未提供)"
        finishes[finish] += 1
        if obs.get("channel"):
            channels.add(str(obs["channel"]))
        if obs.get("protocol"):
            protocols.add(str(obs["protocol"]))
        effort_values.add(str(obs.get("effort_sent")))

        usage = obs.get("usage") or {}
        out_tok = _num(usage.get("output_tokens"))
        max_tok = _num(info.get("max_tokens"))
        rea_tok = _num(usage.get("reasoning_tokens"))
        if out_tok is not None:
            out_tokens_total += int(out_tok)
            out_tokens_known += 1
        if rea_tok is not None:
            reasoning_total += int(rea_tok)
            reasoning_known += 1
        lat = _num(obs.get("latency_ms"))
        if lat is not None:
            latencies.append(int(lat))

        oc = (obs.get("usage_raw") or {}).get("opencode") or {}
        c = _num(oc.get("list_cost"))
        if c is not None:
            list_cost_total += c
            list_cost_known += 1

        tasks.append(
            {
                "task": obs.get("task"),
                "run": obs.get("run"),
                "state": state,
                "finish_reason": finish,
                "output_tokens": out_tok,
                "max_tokens": max_tok,
                "reasoning_tokens": rea_tok,
                "latency_ms": lat,
                "list_cost": c,
            }
        )

    tasks.sort(key=lambda t: (str(t["task"]), t["run"] if t["run"] is not None else 0))
    lat_sorted = sorted(latencies)
    median_lat = lat_sorted[len(lat_sorted) // 2] if lat_sorted else None

    # ── 完整性對帳（沒有 expected 就不做，且明確寫「未對帳」而不是假裝通過）──
    actual_per_task = Counter(
        str(t["task"]) for t in tasks if t["task"] is not None
    )
    if expected is None:
        completeness = "UNCHECKED"
        missing: list[str] = []
        expected_total = None
    else:
        missing = []
        for task_id, want in sorted(expected.items()):
            got = actual_per_task.get(task_id, 0)
            if got != want:
                missing.append(f"{task_id}:{got}/{want}")
        # 多出來的題目（spec 沒有）也算不完整：它不屬於這個 spec
        extra = sorted(set(actual_per_task) - set(expected))
        missing.extend(f"{tid}:{actual_per_task[tid]}/0(不在 spec)" for tid in extra)
        completeness = "COMPLETE" if not missing else "INCOMPLETE"
        expected_total = sum(expected.values())

    return {
        "model_dir": observations_path.parent.name,
        "n_calls": len(obs_list),
        "expected_calls": expected_total,
        "completeness": completeness,
        "missing": missing,
        "states": dict(sorted(states.items())),
        "usable_rate": (
            f"{states.get('USABLE', 0)}/{len(obs_list)}" if obs_list else "0/0"
        ),
        "finish_reasons": dict(sorted(finishes.items())),
        "channels": sorted(channels),
        "protocols": sorted(protocols),
        "effort_sent_values": sorted(effort_values),
        "output_tokens": {
            "total": out_tokens_total if out_tokens_known else None,
            "known_calls": out_tokens_known,
        },
        "reasoning_tokens": {
            "total": reasoning_total if reasoning_known else None,
            "known_calls": reasoning_known,
        },
        "latency_ms_median": median_lat,
        "cost_list_usd": round(list_cost_total, 8) if list_cost_known else None,
        "cost_known_calls": list_cost_known,
        "tasks": tasks,
    }


def _fmt_cost(summary: dict[str, Any]) -> str:
    cost = summary["cost_list_usd"]
    if cost is None:
        return "n/a（該通道未回報計費）"
    n = summary["cost_known_calls"]
    suffix = "" if n == summary["n_calls"] else f"（僅 {n}/{summary['n_calls']} 列有值）"
    return f"${cost:.6f}{suffix}"


def _fmt_tokens(block: dict[str, Any]) -> str:
    if block["total"] is None:
        return "n/a"
    n = block["known_calls"]
    suffix = "" if n else ""
    return f"{block['total']}{suffix}"


def render_markdown(summaries: list[dict[str, Any]], run_root: Path) -> str:
    lines: list[str] = []
    lines.append("# SOEB 機械指標（不含分數）")
    lines.append("")
    lines.append(f"- run 根目錄：`{run_root.as_posix()}`")
    lines.append(f"- 模型數：{len(summaries)}")
    lines.append("")
    lines.append("> 本表**刻意不含任何分數**。可用性是 `diagnose.classify()` 的三態，")
    lines.append("> 不是評分結果；未知計費顯示 `n/a`，不顯示 0。")
    lines.append("")

    lines.append("## 總表")
    lines.append("")
    lines.append("| 模型目錄 | 完整 | 呼叫 | 可用 | 通道 | 協定 | 輸出 tokens | reasoning | 中位延遲 | 計費(USD) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        exp = s["expected_calls"]
        comp = (
            "未對帳"
            if s["completeness"] == "UNCHECKED"
            else ("完整" if s["completeness"] == "COMPLETE" else f"**殘缺**（{s['n_calls']}/{exp}）")
        )
        lines.append(
            "| {m} | {comp} | {n} | {u} | {c} | {p} | {o} | {r} | {l} | {cost} |".format(
                m=s["model_dir"],
                comp=comp,
                n=s["n_calls"],
                u=s["usable_rate"],
                c=", ".join(s["channels"]) or "?",
                p=", ".join(s["protocols"]) or "?",
                o=_fmt_tokens(s["output_tokens"]),
                r=_fmt_tokens(s["reasoning_tokens"]),
                l=f"{s['latency_ms_median']} ms" if s["latency_ms_median"] is not None else "n/a",
                cost=_fmt_cost(s),
            )
        )
    lines.append("")

    lines.append("## 逐模型明細")
    lines.append("")
    for s in summaries:
        lines.append(f"### {s['model_dir']}")
        lines.append("")
        if s["completeness"] == "UNCHECKED":
            lines.append(f"- 完整性：**未對帳**（未提供 spec，無法知道應有幾列）")
        elif s["completeness"] == "COMPLETE":
            lines.append(f"- 完整性：**完整**（{s['n_calls']}/{s['expected_calls']} 呼叫）")
        else:
            lines.append(
                f"- 完整性：🔴 **殘缺**（{s['n_calls']}/{s['expected_calls']} 呼叫；"
                f"缺口 {', '.join(s['missing'])}）"
                " ⇒ 全 USABLE 只代表『已落地的列都能用』，**不代表跑完**"
            )
        lines.append(
            f"- 可用：**{s['usable_rate']}**（{'／'.join(f'{k}={v}' for k, v in s['states'].items())}）"
        )
        lines.append(f"- finish_reason：{'／'.join(f'`{k}`={v}' for k, v in s['finish_reasons'].items())}")
        lines.append(f"- effort_sent：{', '.join(s['effort_sent_values'])}")
        lines.append(f"- 計費：{_fmt_cost(s)}")
        lines.append("")
        lines.append("| 題 | run | 狀態 | finish | out/max tokens | reasoning | 延遲 |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in s["tasks"]:
            out = t["output_tokens"]
            mx = t["max_tokens"]
            tok = f"{'?' if out is None else int(out)}/{'?' if mx is None else int(mx)}"
            rea = "n/a" if t["reasoning_tokens"] is None else int(t["reasoning_tokens"])
            lat = "n/a" if t["latency_ms"] is None else f"{int(t['latency_ms'])} ms"
            lines.append(
                f"| {t['task']} | {t['run']} | {t['state']} | `{t['finish_reason']}` | {tok} | {rea} | {lat} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="SOEB 機械指標聚合（唯讀）")
    parser.add_argument("--run-root", required=True, help="彙總根或單一 run 目錄")
    parser.add_argument("--tasks", help="spec JSON（提供後做完整性對帳）")
    parser.add_argument("--json", help="輸出 JSON 路徑")
    parser.add_argument("--md", help="輸出 Markdown 路徑")
    args = parser.parse_args(argv)

    expected: dict[str, int] | None = None
    if args.tasks:
        spec_path = Path(args.tasks)
        if not spec_path.is_absolute():
            spec_path = root / spec_path
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: 讀不到 spec {spec_path}: {exc}", file=sys.stderr)
            return 2
        expected = {}
        for task in spec.get("tasks") or []:
            tid = task.get("id")
            if not tid:
                continue
            runs = task.get("runs")
            expected[str(tid)] = int(runs) if isinstance(runs, int) and runs > 0 else 1
        if not expected:
            print(f"ERROR: spec {spec_path} 內沒有 tasks[].id", file=sys.stderr)
            return 2

    run_root = Path(args.run_root)
    if not run_root.is_absolute():
        run_root = root / run_root
    try:
        runs_map = discover_runs(run_root)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not runs_map:
        print(f"ERROR: {run_root} 之下找不到任何 observations.jsonl", file=sys.stderr)
        return 2

    summaries = [summarize(p, expected) for p in runs_map.values()]
    total_cost = sum(s["cost_list_usd"] or 0.0 for s in summaries)
    unknown = [s["model_dir"] for s in summaries if s["cost_list_usd"] is None]
    incomplete = [s for s in summaries if s["completeness"] == "INCOMPLETE"]

    print(f"模型數：{len(summaries)}")
    print(f"{'模型目錄':<32}{'呼叫':>8}{'可用':>8}{'輸出tok':>10}{'計費USD':>14}  完整性")
    for s in summaries:
        out = s["output_tokens"]["total"]
        cost = s["cost_list_usd"]
        exp = s["expected_calls"]
        comp = (
            "未對帳"
            if s["completeness"] == "UNCHECKED"
            else ("完整" if s["completeness"] == "COMPLETE" else f"殘缺 {s['n_calls']}/{exp}")
        )
        print(
            f"{s['model_dir']:<32}{s['n_calls']:>8}{s['usable_rate']:>8}"
            f"{('n/a' if out is None else out):>10}"
            f"{('n/a' if cost is None else f'{cost:.6f}'):>14}  {comp}"
        )
    print(f"\n計費合計（僅計有回報者）：${total_cost:.6f}")
    if unknown:
        print(f"計費未知（顯示 n/a）：{', '.join(unknown)}")
    for s in incomplete:
        print(f"🔴 殘缺 run：{s['model_dir']} 缺口 {', '.join(s['missing'])}")

    if args.json:
        out_json = Path(args.json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(
                {"run_root": run_root.as_posix(), "models": summaries},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8", newline="\n",
        )
        print(f"已寫 JSON：{out_json}")
    if args.md:
        out_md = Path(args.md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_markdown(summaries, run_root), encoding="utf-8", newline="\n")
        print(f"已寫 Markdown：{out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
