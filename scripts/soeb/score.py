"""SOEB 計分器——把填好的 `scores_template.json` 算成可發表的成績。

為什麼不能手算：

1. **stability 不是平均的一部分。** rubric 規定 T2/T4/T7 各 run 要分開評，
   判「極差 ≤ 0.15×配分」。手算的人只會填一個平均數，於是「兩次差很多」
   被平均吃掉——那正是最該被看見的東西。⇒ 本檔逐 run 算，並在報告同時列 min 與 mean。
2. **滿分是 95 不是 100。** T8 沒有落地（規則集未提供），任何換算成百分比再宣稱
   「100 分制」都會把缺口藏起來。⇒ 本檔的 `total_possible` 直接從 rubric 加總，
   且把不可用配分印在表頭。
3. **扣分只能扣、不能加分。** 評分者填 `-2` 是扣 2 分；若有人誤填 `2`，
   手算會悄悄變成加分。⇒ 本檔把正數扣分項視為**硬錯誤**（exit 2），不是警告。
4. **未評 ≠ 0 分。** 把沒評的項目當 0，會讓「還沒評」和「答得很爛」長得一樣。
   ⇒ 本檔把未評的題目標成 `None`，總分只涵蓋已評題目，並在報告列出缺口，
   退出碼 1 表示「不完整」。

用法：

    .venv\\Scripts\\python.exe scripts\\soeb\\score.py --scores tests\\results\\soeb\\v2\\_worksheet\\scores_template.json

退出碼：`0` 全部評完可發表／`1` 有未評缺口（成績不完整）／`2` 讀不進來或違反硬規則。
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_BAD = 2


class HardError(Exception):
    """違反不可談判的規則（身分、分數越界、扣分為正）。"""


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def round_half(x: float) -> float:
    """四捨五入到 0.5。用 Decimal 的 HALF_UP，避免 Python 的 banker's rounding。"""
    return float((Decimal(str(x)) * 2).quantize(Decimal("1"), rounding=ROUND_HALF_UP) / 2)


def task_max_points(rubric: dict) -> dict[str, int]:
    return {tid: int(tr.get("points") or 0) for tid, tr in rubric["tasks"].items()}


def validate(worksheet: dict, rubric: dict, spec_tasks: dict[str, dict]) -> list[str]:
    """回傳 soft problems（未評缺口）。硬錯誤直接拋 HardError。"""
    problems: list[str] = []

    judge = worksheet.get("judge") or {}
    if judge.get("is_candidate") is True:
        raise HardError(
            "評分者自填為候選模型之一（is_candidate=true）。judge_requirement 禁止自評，"
            "本份成績不予計算。"
        )
    if not judge.get("judge_id"):
        problems.append("judge.judge_id 未填 —— 報告無法標明評分者，屬資料缺口。")

    if worksheet.get("bound_spec_sha256") != worksheet.get("observed_spec_sha256"):
        raise HardError(
            "rubric 綁定的 spec 與實際跑的 spec 不同："
            f"{worksheet.get('bound_spec_sha256')} != {worksheet.get('observed_spec_sha256')}。"
            "這份分數不能套用在該批答案上。"
        )

    for lab, block in (worksheet.get("scoring") or {}).items():
        for tid, tb in (block.get("tasks") or {}).items():
            if tid not in rubric["tasks"]:
                raise HardError(f"{lab}/{tid}：rubric 沒有這題（阻擋評分 blocked_tasks）")
            runs = int(tb.get("runs") or 1)
            items = tb.get("items") or {}
            if not items:
                problems.append(f"{lab}/{tid}：沒有任何 checkpoint")
                continue
            for iid, ib in items.items():
                cap = float(ib.get("max_points") or 0)
                per_run = ib.get("per_run") or []
                if len(per_run) != runs:
                    raise HardError(
                        f"{lab}/{iid}：per_run 有 {len(per_run)} 格，但該題 runs={runs}。"
                        "長度不符會讓某個 run 的分數被靜默丟掉。"
                    )
                for r, cell in enumerate(per_run):
                    pts = cell.get("points")
                    if pts is None:
                        problems.append(f"{lab}/{iid} run{r + 1}：未評")
                        continue
                    if pts < 0 or pts > cap:
                        raise HardError(
                            f"{lab}/{iid} run{r + 1}：分數 {pts} 超出 0…{cap}"
                        )
                    if cell.get("verdict") == "unscored":
                        problems.append(f"{lab}/{iid} run{r + 1}：verdict 說 unscored 但填了分數")
        for p in block.get("global_penalties") or []:
            pts = p.get("points")
            if not isinstance(pts, (int, float)):
                raise HardError(f"{lab} 扣分項 {p.get('id')}：points 不是數字")
            if pts > 0:
                raise HardError(
                    f"{lab} 扣分項 {p.get('id')}：points={pts} 是正數。"
                    "全域扣分只能扣（填負值），否則手算會變成加分。"
                )
            if not p.get("evidence"):
                problems.append(f"{lab} 扣分項 {p.get('id')}：沒有 evidence 引文")
    return problems


def score_model(rubric: dict, spec_tasks: dict, block: dict) -> dict:
    """逐 run 算→取平均→判 stability。未評的題目回 None（不是 0）。"""
    stability_tasks = set((rubric.get("scoring_rules") or {}).get("stability", {}).get("applies_to") or [])
    out: dict[str, Any] = {"tasks": {}, "unscored_tasks": []}
    total = 0.0

    for tid, tr in rubric["tasks"].items():
        if tid not in spec_tasks:
            continue
        tb = (block.get("tasks") or {}).get(tid) or {}
        runs = int(tb.get("runs") or spec_tasks[tid].get("runs", 1) or 1)
        items = tb.get("items") or {}

        run_totals: list[float | None] = []
        for r in range(runs):
            run_sum: float | None = 0.0
            for iid, ib in items.items():
                per_run = ib.get("per_run") or []
                cell = per_run[r] if r < len(per_run) else {}
                pts = cell.get("points")
                if pts is None:
                    run_sum = None
                    break
                run_sum += float(pts)
            run_totals.append(run_sum)

        complete = [v for v in run_totals if v is not None]
        cap = int(tr.get("points") or 0)
        if not complete:
            out["tasks"][tid] = {"score": None, "cap": cap, "stability": "unknown",
                                 "run_totals": run_totals}
            out["unscored_tasks"].append(tid)
            continue

        mean = sum(complete) / len(complete)
        score = round_half(mean)
        if tid in stability_tasks and runs >= 2:
            if len(complete) < 2:
                stab = "unknown"
            else:
                spread = max(complete) - min(complete)
                stab = "stable" if spread <= 0.15 * cap else "unstable"
        else:
            stab = "n/a"
        out["tasks"][tid] = {
            "score": score,
            "mean": round(mean, 3),
            "min": min(complete),
            "cap": cap,
            "stability": stab,
            "run_totals": run_totals,
        }
        total += score

    penalties = block.get("global_penalties") or []
    penalty_sum = sum(float(p.get("points") or 0) for p in penalties)
    out["penalties"] = penalties
    out["penalty_total"] = penalty_sum
    out["raw_total"] = round(total, 3)
    # 全未評 ⇒ None，不是 0。把「還沒評」寫成 0.0 會直接被讀成「答得很爛」。
    if out["unscored_tasks"] and len(out["unscored_tasks"]) == len(out["tasks"]):
        out["total"] = None
    else:
        out["total"] = max(0.0, round(total + penalty_sum, 3))
    return out


def render_markdown(result: dict, rubric: dict, labels: list[str]) -> str:
    caps = task_max_points(rubric)
    task_ids = [t for t in rubric["tasks"] if t in caps]
    possible = sum(caps.values())
    design = (rubric.get("scoring_rules") or {}).get("total_points_design")

    lines: list[str] = []
    lines.append("# SOEB 成績單")
    lines.append("")
    lines.append(f"- 評分者：`{result['judge_id']}`")
    lines.append(f"- 是否候選模型：{result['judge_is_candidate']}")
    lines.append(f"- rubric 版本：{rubric.get('rubric_version')}（suite `{rubric.get('suite')}`）")
    lines.append(f"- spec sha256：`{(result.get('spec_sha') or '')[:16]}…`（rubric 綁定值相符）")
    lines.append(
        f"- **滿分 {possible} 分**（設計 {design} 分；缺口 "
        f"{design - possible} 分＝T8 未落地，不得換算成百分比宣稱 100 分制）"
    )
    lines.append("")

    header = "| 模型 | " + " | ".join(f"{t} ({caps[t]})" for t in task_ids) + " | 扣分 | 總分 |"
    sep = "|---|" + "---|" * len(task_ids) + "---|---|"
    lines.append(header)
    lines.append(sep)
    for lab in labels:
        m = result["models"].get(lab) or {}
        tasks = m.get("tasks") or {}
        cells = []
        for t in task_ids:
            info = tasks.get(t) or {}
            s = info.get("score")
            if s is None:
                cells.append("—")
            elif info.get("stability") == "unstable":
                cells.append(f"{s} ⚠")
            else:
                cells.append(str(s))
        pen = m.get("penalty_total") or 0
        pen_s = "0" if pen == 0 else str(pen)
        cells.append(pen_s)
        tot = m.get("total")
        cells.append("—（未評）" if tot is None else f"**{tot}**")
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 穩定性（同題多 run）")
    lines.append("")
    lines.append("`⚠` = 極差 > 0.15×配分。不穩定**不得**只用平均呈現，下表同時列 min 與 mean。")
    lines.append("")
    lines.append("| 模型 | 題 | runs 各自得分 | mean | min | 判定 |")
    lines.append("|---|---|---|---|---|---|")
    any_row = False
    for lab in labels:
        tasks = (result["models"].get(lab) or {}).get("tasks") or {}
        for t in task_ids:
            info = tasks.get(t) or {}
            # 只列「真的有多 run」的題目：runs=1 沒有極差可算，列出來只是噪音。
            if len(info.get("run_totals") or []) < 2:
                continue
            if info.get("stability") in ("stable", "unstable", "unknown"):
                any_row = True
                runs_s = ", ".join("—" if v is None else str(v) for v in info.get("run_totals", []))
                lines.append(
                    f"| {lab} | {t} | {runs_s} | {info.get('mean', '—')} "
                    f"| {info.get('min', '—')} | {info.get('stability')} |"
                )
    if not any_row:
        lines.append("| — | — | — | — | — | 無多 run 題目 |")
    lines.append("")

    lines.append("## 全域扣分")
    lines.append("")
    lines.append("| 模型 | 項目 | 題 | 分數 | 依據 |")
    lines.append("|---|---|---|---|---|")
    any_pen = False
    for lab in labels:
        for p in (result["models"].get(lab) or {}).get("penalties") or []:
            any_pen = True
            lines.append(
                f"| {lab} | {p.get('id')} | {p.get('task', '—')} | {p.get('points')} "
                f"| {str(p.get('evidence', ''))[:80]} |"
            )
    if not any_pen:
        lines.append("| — | — | — | — | 無扣分 |")
    lines.append("")

    if result.get("problems"):
        lines.append("## 缺口（成績不完整）")
        lines.append("")
        for prob in result["problems"]:
            lines.append(f"- {prob}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 計分器")
    ap.add_argument("--scores", required=True, help="填好的 scores_template.json")
    ap.add_argument("--rubric", default=str(REPO_ROOT / "scripts" / "soeb" / "rubric.json"))
    ap.add_argument("--spec", default=str(REPO_ROOT / "scripts" / "soeb" / "tasks" / "soeb-v1.json"))
    ap.add_argument("--json", default=None, help="計算結果輸出 JSON 路徑")
    ap.add_argument("--md", default=None, help="成績單輸出 Markdown 路徑")
    args = ap.parse_args(argv)

    try:
        worksheet = _load_json(Path(args.scores))
        rubric = _load_json(Path(args.rubric))
        spec = _load_json(Path(args.spec))
    except Exception as exc:
        print(f"Error: 讀不進來：{exc}", file=sys.stderr)
        return EXIT_BAD

    spec_tasks = {t["id"]: t for t in spec.get("tasks", [])}

    try:
        problems = validate(worksheet, rubric, spec_tasks)
    except HardError as exc:
        print(f"拒絕計分：{exc}", file=sys.stderr)
        return EXIT_BAD

    labels = worksheet.get("labels") or sorted((worksheet.get("scoring") or {}))
    result: dict[str, Any] = {
        "judge_id": (worksheet.get("judge") or {}).get("judge_id"),
        "judge_is_candidate": (worksheet.get("judge") or {}).get("is_candidate"),
        "spec_sha": worksheet.get("observed_spec_sha256"),
        "total_possible": sum(task_max_points(rubric).values()),
        "models": {},
        "problems": problems,
    }
    for lab in labels:
        block = (worksheet.get("scoring") or {}).get(lab) or {}
        result["models"][lab] = score_model(rubric, spec_tasks, block)

    md = render_markdown(result, rubric, labels)
    if args.json:
        Path(args.json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    if args.md:
        Path(args.md).write_text(md, encoding="utf-8", newline="\n")

    print(md)
    if problems:
        print(f"⚠ 有 {len(problems)} 個缺口，成績不完整（exit {EXIT_INCOMPLETE}）", file=sys.stderr)
        return EXIT_INCOMPLETE
    print("✓ 全部 checkpoint 已評，可發表", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
