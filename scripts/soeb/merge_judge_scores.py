"""SOEB 評分合併與驗證——把評分模型回傳的單題 fragment 併回 `score.py` 吃得下的骨架。

為什麼不能「收回 JSON 直接丟給 score.py」：

1. **fragment 是單題的，骨架是完整的。** 七題分給七個評分者，收回來是七份殘片；
   手動合併等於手動搬 6 模型 × 7 題 × 41 checkpoint × run 數的格，搬錯不會有人發現。
2. **「附引文」如果不去驗，就等於沒規定。** 評分模型很會生一句「看起來像原文」的引文。
   ⇒ 本檔逐格把引文**去掉所有空白後**比對該模型該 run 的答案正文；
   不是子字串就拒收那格。這是把「憑印象給分」變成**結構上不可能**，而不是靠自律。
3. **三態尺度要機器擋。** rubric 規定 checkpoint 只能 0／半分／全分。填 1.7 分的評分者
   不是在「更精細」，是在用一個不存在的尺度——而且它會讓 min/mean 的比較失去意義。
4. **同題兩份 fragment 不能都收。** 兩個評分者對同一題給的分數既不能相加也不能平均；
   收了哪一份都會讓報告看起來像「已評完」。⇒ 一律拒收，要人明確挑一份。

用法：

    .venv\\Scripts\\python.exe scripts\\soeb\\merge_judge_scores.py ^
        --template tests\\results\\soeb\\_probe\\judge_packets\\_template_scores.json ^
        --run-root tests\\results\\soeb\\v2 ^
        --fragments tests\\results\\soeb\\_probe\\judge_packets\\incoming ^
        --out tests\\results\\soeb\\_probe\\judge_packets\\scores_merged.json

退出碼：`0` 合併完成（且每一題都有 fragment）／`1` 有題目沒有 fragment（成績不完整）／
`2` 輸入不合法或違反硬規則、或檔案錯誤。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

try:  # pragma: no cover - 取決於呼叫方式
    from .anonymize import assert_not_under_data, blind_label_map
    from .build_worksheet import discover_runs
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from anonymize import assert_not_under_data, blind_label_map
    from build_worksheet import discover_runs

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_BAD = 2

#: 引文去掉空白後的最短長度。太短的引文（例如「的」）在任何答案裡都成立，
#: 等於沒有引文。
MIN_EVIDENCE_CHARS = 6


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _squash(s: str) -> str:
    """去掉所有空白。引文的換行／縮排差異不是造假，不該被當成對不上。"""
    return "".join(str(s).split())


def answer_index(run_root: Path) -> dict[tuple[str, str, int], str]:
    """{(label, task, run): 答案正文}。標籤由**同一份** `blind_label_map` 決定。"""
    runs = discover_runs(run_root)
    label_map = blind_label_map(sorted(runs))
    idx: dict[tuple[str, str, int], str] = {}
    for raw_mid, recs in runs.items():
        lab = label_map[raw_mid]
        for rec in recs:
            if not rec.get("ok") or not (rec.get("text") or "").strip():
                continue
            idx[(lab, str(rec.get("task")), int(rec.get("run", 1)))] = _squash(rec["text"])
    return idx


def _allowed_points(cap: float) -> set[float]:
    """0／半分／全分。cap 是奇數時半分是小數（例如配分 3 ⇒ 1.5）。"""
    return {0.0, cap / 2.0, cap}


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


class Rejected(Exception):
    """fragment 違反硬規則。"""


def parse_fragment(
    frag: dict,
    rubric: dict,
    template: dict,
    answers: dict[tuple[str, str, int], str],
) -> tuple[str, dict[str, dict], list[dict], str, bool]:
    """驗一份 fragment，回傳 (task, {label: {iid: [(points, evidence), …]}}, penalties, judge_id, is_candidate)。"""
    if not isinstance(frag, dict):
        raise Rejected("fragment 不是 JSON 物件")
    if int(frag.get("fragment_version") or 0) != 1:
        raise Rejected("fragment_version 必須是 1")

    task = str(frag.get("task") or "").strip()
    if not task:
        raise Rejected("缺少 task")

    judge = frag.get("judge") or {}
    judge_id = str(judge.get("id") or "").strip()
    if not judge_id:
        raise Rejected("judge.id 未填 —— 沒有評分者身分的分數不予採計")
    is_candidate = judge.get("is_candidate")
    if not isinstance(is_candidate, bool):
        raise Rejected("judge.is_candidate 必須是 true/false（不可留 null）")
    if is_candidate:
        raise Rejected(
            "評分者自填為候選模型之一（is_candidate=true）。judge_requirement 禁止自評，本份不予採計。"
        )

    rubric_task = (rubric.get("tasks") or {}).get(task)
    if rubric_task is None:
        raise Rejected(f"rubric 沒有 {task} 這題")

    labels = list(template.get("labels") or [])
    if not labels:
        raise Rejected("template 沒有 labels")

    # 這題每個模型有幾個 run：以「真的有答案的 run」為準，不是憑 rubric 猜。
    run_ids: dict[str, list[int]] = {}
    for lab in labels:
        got = sorted(r for (l, t, r) in answers if l == lab and t == task)
        if got:
            run_ids[lab] = got

    items = list(rubric_task.get("items") or [])
    points_block = frag.get("points") or {}
    evidence_block = frag.get("evidence") or {}
    if not isinstance(points_block, dict) or not isinstance(evidence_block, dict):
        raise Rejected("points／evidence 必須是物件")

    out: dict[str, dict] = {}
    for lab in labels:
        expect_runs = run_ids.get(lab) or []
        if not expect_runs:
            # 缺件：這一題這個模型沒有可用答案。**不要寫入任何格子**——
            # 寫 0 會讓「呼叫失敗」變成「答得很爛」，那是兩種不同的事實。
            continue
        lab_points = points_block.get(lab)
        lab_ev = evidence_block.get(lab)
        if not isinstance(lab_points, dict):
            raise Rejected(f"{lab} 缺少 points")
        if not isinstance(lab_ev, dict):
            raise Rejected(f"{lab} 缺少 evidence")

        per_item: dict[str, list[tuple[float, str]]] = {}
        for it in items:
            iid = it["id"]
            cap = float(it.get("points") or 0)
            raw = lab_points.get(iid)
            ev = lab_ev.get(iid)
            if raw is None:
                raise Rejected(
                    f"{lab}/{iid}：這份答案存在，卻沒有給分（points 缺少或為 null）。"
                    "未評會讓整題變成未評，必須明確給 0／半分／全分。"
                )
            if not isinstance(raw, list):
                raise Rejected(f"{lab}/{iid}：points 必須是陣列（每個 run 一格）")
            if len(raw) != len(expect_runs):
                raise Rejected(
                    f"{lab}/{iid}：points 有 {len(raw)} 格，但這題該模型有 "
                    f"{len(expect_runs)} 個 run。長度不符會讓某個 run 被靜默丟掉。"
                )
            if not isinstance(ev, list) or len(ev) != len(raw):
                raise Rejected(f"{lab}/{iid}：evidence 必須是與 points 同長度的陣列")

            allowed = _allowed_points(cap)
            cells: list[tuple[float, str]] = []
            for i, (p, e) in enumerate(zip(raw, ev)):
                if p is None:
                    raise Rejected(f"{lab}/{iid} run{i + 1}：沒有給分")
                num = _num(p)
                if num is None:
                    raise Rejected(f"{lab}/{iid} run{i + 1}：分數不是數字（{p!r}）")
                if not any(abs(num - a) < 1e-9 for a in allowed):
                    shown = "／".join(f"{a:g}" for a in sorted(allowed))
                    raise Rejected(
                        f"{lab}/{iid} run{i + 1}：分數 {num:g} 不在三態尺度（只能是 {shown}）"
                    )
                ev_text = str(e or "").strip()
                if not ev_text:
                    raise Rejected(
                        f"{lab}/{iid} run{i + 1}：沒有引文。沒有引文的分數不予採計——"
                        "這是唯一能擋「憑印象給分」的機制。"
                    )
                if len(_squash(ev_text)) < MIN_EVIDENCE_CHARS:
                    raise Rejected(
                        f"{lab}/{iid} run{i + 1}：引文太短（去空白後 <{MIN_EVIDENCE_CHARS} 字），"
                        "無法證明它真的出自該答案"
                    )
                run_no = expect_runs[i]
                body = answers.get((lab, task, run_no))
                if body is None:
                    raise Rejected(f"{lab}/{iid} run{i + 1}：找不到該 run 的答案可供比對引文")
                if _squash(ev_text) not in body:
                    raise Rejected(
                        f"{lab}/{iid} run{i + 1}：引文不在該 run 的答案正文中（必須逐字引用）。"
                        f"引文＝{ev_text[:60]!r}"
                    )
                cells.append((num, ev_text))
            per_item[iid] = cells
        if per_item:
            out[lab] = per_item

    # 全域扣分項
    valid_pen_ids = {p.get("id") for p in (rubric.get("global_penalties") or [])}
    penalties: list[dict] = []
    for p in frag.get("global_penalties") or []:
        if not isinstance(p, dict):
            raise Rejected("global_penalties 内每筆必須是物件")
        pid = str(p.get("id") or "")
        if pid not in valid_pen_ids:
            raise Rejected(f"global_penalties 的 id={pid!r} 不在 rubric 的清單裡")
        target = str(p.get("target") or "")
        if target not in labels:
            raise Rejected(f"global_penalties 的 target={target!r} 不是有效標籤")
        num = _num(p.get("points"))
        if num is None:
            raise Rejected(f"global_penalties {pid}：points 不是數字")
        if num > 0:
            raise Rejected(f"global_penalties {pid}：points={num:g} 是正數，扣分只能扣")
        ev_text = str(p.get("evidence") or "").strip()
        if not ev_text:
            raise Rejected(f"global_penalties {pid}：沒有引文")
        body = "".join(
            answers.get((target, task, r), "") for r in (run_ids.get(target) or [])
        )
        if body and _squash(ev_text) not in body:
            raise Rejected(
                f"global_penalties {pid}（{target}）：引文不在本題答案中。引文＝{ev_text[:60]!r}"
            )
        penalties.append({"id": pid, "target": target, "points": num, "evidence": ev_text})
    return task, out, penalties, judge_id, is_candidate


def _verdict(points: float, cap: float) -> str:
    if points <= 0:
        return "fail"
    if abs(points - cap) < 1e-9:
        return "pass"
    return "partial"


def apply_fragment(
    template: dict,
    rubric: dict,
    task: str,
    per_model: dict[str, dict],
    penalties: list[dict],
) -> None:
    rubric_task = rubric["tasks"][task]
    caps = {it["id"]: float(it.get("points") or 0) for it in rubric_task.get("items") or []}
    for lab, per_item in per_model.items():
        block = template["scoring"][lab]["tasks"].get(task)
        if block is None:
            raise Rejected(f"template 沒有 {lab}/{task}（骨架與 rubric 不同步）")
        for iid, cells in per_item.items():
            item = block["items"].get(iid)
            if item is None:
                raise Rejected(f"template 沒有 {lab}/{task}/{iid}")
            cap = caps[iid]
            item["per_run"] = [
                {"points": pts, "verdict": _verdict(pts, cap), "evidence": ev}
                for pts, ev in cells
            ]
    for p in penalties:
        template["scoring"][p["target"]]["global_penalties"].append(
            {"id": p["id"], "points": p["points"], "evidence": p["evidence"], "task": task}
        )


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 評分合併與驗證")
    ap.add_argument("--template", required=True, help="空白骨架（build_judge_packet 產出的 _template_scores.json）")
    ap.add_argument("--run-root", required=True, help="答案來源（用來逐格比對引文）")
    ap.add_argument("--fragments", required=True, help="放 <T>.json 的資料夾，或單一 json 檔")
    ap.add_argument("--out", required=True, help="合併結果（給 score.py 吃）")
    ap.add_argument("--rubric", default=str(REPO_ROOT / "scripts" / "soeb" / "rubric.json"))
    args = ap.parse_args(argv)

    template_path = Path(args.template)
    run_root = Path(args.run_root)
    frag_path = Path(args.fragments)
    out_path = Path(args.out)

    if not template_path.is_file():
        print(f"Error: template 不存在：{template_path}", file=sys.stderr)
        return EXIT_BAD
    if not run_root.is_dir():
        print(f"Error: run-root 不存在：{run_root}", file=sys.stderr)
        return EXIT_BAD
    if out_path.resolve() == template_path.resolve():
        print("Error: --out 不能等於 --template（會把空白母片覆蓋掉，之後無法重建）", file=sys.stderr)
        return EXIT_BAD

    try:
        out_path = assert_not_under_data(out_path)
    except SystemExit as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_BAD

    rubric = _load_json(Path(args.rubric))
    template = _load_json(template_path)
    answers = answer_index(run_root)
    if not answers:
        print(f"Error: {run_root} 讀不到任何可用答案", file=sys.stderr)
        return EXIT_BAD

    if frag_path.is_dir():
        files = sorted(p for p in frag_path.glob("*.json"))
    elif frag_path.is_file():
        files = [frag_path]
    else:
        print(f"Error: fragments 不存在：{frag_path}", file=sys.stderr)
        return EXIT_BAD
    if not files:
        print(f"Error: {frag_path} 下沒有任何 .json", file=sys.stderr)
        return EXIT_BAD

    merged = copy.deepcopy(template)
    judged: dict[str, str] = {}
    judge_ids: list[str] = []
    problems: list[str] = []

    for f in files:
        try:
            frag = _load_json(f)
        except Exception as exc:
            print(f"Error: 讀不進 {f.name}：{exc}", file=sys.stderr)
            return EXIT_BAD
        try:
            task, per_model, penalties, judge_id, _ = parse_fragment(frag, rubric, template, answers)
            if task in judged:
                raise Rejected(
                    f"{task} 已經由 {judged[task]} 評過，又出現 {f.name}。"
                    "同題兩份分數既不能相加也不能平均，請明確挑一份後刪掉另一份。"
                )
            apply_fragment(merged, rubric, task, per_model, penalties)
        except Rejected as exc:
            print(f"拒收 {f.name}：{exc}", file=sys.stderr)
            return EXIT_BAD
        judged[task] = f"{judge_id}（{f.name}）"
        judge_ids.append(f"{task}:{judge_id}")

    merged.setdefault("judge", {})
    merged["judge"]["judge_id"] = "; ".join(judge_ids)
    merged["judge"]["is_candidate"] = False
    merged["judge"]["note"] = (
        "由 merge_judge_scores.py 合併。每一格的分數都附有經子字串比對的原文引文。"
    )
    merged["judged_tasks"] = judged

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    # 「該有哪些題」以骨架為準（骨架＝rubric ∩ spec，blocked task 不會進去），
    # 不是以 rubric 為準——否則被 blocked 的題目會被誤報成「漏評」。
    expected: set[str] = set()
    for block in (template.get("scoring") or {}).values():
        expected.update((block.get("tasks") or {}).keys())
    expected_sorted = sorted(expected)

    print("=" * 72)
    print(f"fragment : {len(files)} 份（{frag_path}）")
    print(f"合併完成 : {', '.join(sorted(judged))}")
    print(f"輸出     : {out_path}")
    print(f"評分者   : {merged['judge']['judge_id']}")
    missing = [t for t in expected_sorted if t not in judged]
    if missing:
        print("-" * 72)
        print(f"尚未評分（不完整）：{', '.join(missing)}")
        for p in problems:
            print(f"  - {p}")
        print("=" * 72)
        return EXIT_INCOMPLETE
    print("=" * 72)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
