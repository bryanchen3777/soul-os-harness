"""SOEB 評分工作表產生器——把「rubric 的 checkpoint」與「各模型的真實答案」併成人可打勾的檔案。

為什麼要有這支腳本，而不是叫評分者自己去讀 observations.jsonl：

1. **observations.jsonl 會洩漏身分。** 它有 `model_id`、`protocol`、`channel`、`api_model`、
   `effort_sent`。本輪六顆模型分屬三種協定、兩種通道，看一眼這些欄位就知道誰是誰，
   盲評直接作廢。⇒ 這裡沿用 `anonymize.py` 的**同一份** `blind_label_map()` 與
   `LEAKY_FIELDS`（不是另抄一份，避免兩份實作對同一份名單算出不同標籤）。
2. **評分者需要題目原文。** checkpoint 寫的是「說明它是多次經歷的漸進沉澱」，
   但沒有題目就無法判斷模型是不是在回答那個問題。⇒ 每題一節，先貼題目原文。
3. **評分者需要截斷證據，但截斷證據本身會洩漏身分。** 「答案很短」有兩種原因：
   模型真的精簡，或預算耗盡被切斷；後者是評測失效、不是能力差異。
   但**精確 token 數**（可與 `_metrics.md` 交叉比對）與 **`finish_reason` 原文**
   （`completed`／`end_turn`／`stop` 各自只對應一種協定）都是身分指紋。
   ⇒ 每份答案上方只標一個三態判定：**完整／不完整＋理由**。
   同樣**不給 latency／協定／通道**。
4. **分數要能機器計分。** 光給 markdown 表格，評分者填完還要人手加總，
   加錯就會污染整份報告。⇒ 另出一份 `scores_template.json`，填完由 `score.py` 算。

用法（完全唯讀輸入、不發任何請求、不需要金鑰）：

    .venv\\Scripts\\python.exe scripts\\soeb\\build_worksheet.py --run-root tests\\results\\soeb\\v2

輸出（全部在 `--out-dir`，預設 `<run-root>/_worksheet`）：

    README.md              評分說明、stability 規則、已知限制
    <TASK>.md              每題一檔：題目原文 + checkpoint 表 + 各模型答案（盲標）
    scores_template.json   待填的評分骨架（機器計分的唯一輸入）

**映射檔刻意放在 `--out-dir` 之外**，檔名寫明何時才能開，理由與 `anonymize.py` 相同。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 各家協定「正常結束」的 finish_reason 值。刻意寫成集合而不是比對單一字串，
#: 因為三種協定各用不同字（stop / completed / end_turn）。
_COMPLETE_FINISH = frozenset({"stop", "completed", "end_turn", "stop_sequence", "eos"})

#: 輸出 tokens 達到預算的這個比例，就當成「觸及上限」（＝評測失效，不是能力差異）。
_BUDGET_HIT_RATIO = 0.95

# 複用 anonymize 的同一份映射與洩漏欄位清單。兩條 import 路徑是因為本檔可能被
# 當成「直接執行的腳本」或「套件模組」載入，但**實作只有一份**。
try:  # pragma: no cover - 取決於呼叫方式
    from .anonymize import LEAKY_FIELDS, assert_not_under_data, blind_label_map
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from anonymize import LEAKY_FIELDS, assert_not_under_data, blind_label_map


def _force_utf8_stdio() -> None:
    """cp950 主控台遇到 ⇒／中文會直接崩，先換成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _load_json(path: Path) -> Any:
    """容忍 BOM：本機 PowerShell 的 Set-Content -Encoding UTF8 會寫 BOM。"""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def discover_runs(run_root: Path) -> dict[str, list[dict]]:
    """掃 `run_root` 下的每個子目錄，回傳 {原始 model_id: [觀測列…]}。

    兩種佈局都要吃：
    - **彙總根**（`v2/`）：每個子目錄一個模型的 run；
    - **單一 run 目錄**（`v2/glm-5.3-flash/`）：`observations.jsonl` 就在根上。

    只認「有 observations.jsonl」的目錄。工作表目錄本身（`_worksheet`、`blind`）
    不會有那個檔，自然被跳過——不靠排除清單，避免清單與產出目錄漂移。
    """
    runs: dict[str, list[dict]] = {}
    root_obs = run_root / "observations.jsonl"
    children = [run_root] if root_obs.exists() else sorted(run_root.iterdir())
    for child in children:
        if not child.is_dir():
            continue
        if not child.is_dir():
            continue
        obs = child / "observations.jsonl"
        if not obs.exists():
            continue
        for rec in _read_jsonl(obs):
            mid = rec.get("model_id")
            if not mid:
                continue
            runs.setdefault(mid, []).append(rec)
    return runs


def _usage_line(rec: dict, budget: int | None) -> str:
    """只回報「這則答案完不完整」，**不回報任何可還原身分的量**。

    這裡踩過一次坑（2026-11-01）：原本印 `out 1184/32768 tokens · finish=stop`，
    理由是「評分者需要截斷證據」。但兩者都是**身分指紋**：

    * **精確 token 數**：與 `_metrics.md` 的逐模型 tokens 一比就能還原 MODEL_x 是誰，
      盲評直接作廢。
    * **`finish_reason` 原文**：它由各家協定決定 —— `completed` 只可能來自 responses、
      `end_turn` 只可能來自 anthropic messages、`stop` 來自 chat-completions。
      等於把「協定」寫在每一則答案的標頭。

    評分真正需要的只有一個事實：**這則答案是不是被切斷的**。所以這裡把兩個指紋
    壓縮成三態判定（完整／不完整＋理由），其餘一概不輸出。
    """
    usage = rec.get("usage") or {}
    out_tok = usage.get("output_tokens")
    finish = str(rec.get("finish_reason") or "").strip().lower()

    flags: list[str] = []
    if finish and finish not in _COMPLETE_FINISH:
        flags.append("未正常結束 ⇒ 判讀為評測失效，不是能力差異")
    if (
        isinstance(out_tok, int)
        and isinstance(budget, int)
        and budget > 0
        and out_tok >= budget * _BUDGET_HIT_RATIO
    ):
        flags.append("觸及預算上限 ⇒ 判讀為評測失效，不是能力差異")
    if rec.get("empty_reason"):
        flags.append(str(rec["empty_reason"]))

    if flags:
        return "**不完整**：" + "；".join(flags)
    return "完整（正常結束、未觸及上限）"


def _prompt_text(task_spec: dict) -> str:
    """取出真正送給模型的題目原文。

    題目在 `messages[]`，不是 `prompt` 鍵。**只取 `user` 訊息**：`grading_notes` 是
    出題者的評分重點、runner 從不送出，貼進工作表等於把「該往哪裡答」直接給評分者，
    會讓 checkpoint 退化成「照抄重點」。rubric 已經把它拆成可判定的條目了。
    """
    parts: list[str] = []
    for msg in task_spec.get("messages", []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
    return "\n\n---\n\n".join(p.strip() for p in parts if p.strip())


def _manifest_budget(mf_path: Path) -> int | None:
    """讀 `--force-max-tokens` 的實際天花板；沒有覆寫就回 None（顯示 `?`）。"""
    try:
        mf = _load_json(mf_path)
    except Exception:
        return None
    budget = (mf.get("budget_override") or {}).get("forced_max_tokens")
    return budget if isinstance(budget, int) else None


def build_task_markdown(
    task_id: str,
    task_spec: dict,
    task_rubric: dict,
    labels: list[str],
    answers: dict[str, dict[int, dict]],
    budget: int | None,
) -> str:
    lines: list[str] = []
    points = task_rubric.get("points")
    title = task_rubric.get("title", "")
    runs_expected = task_spec.get("runs", 1)

    lines.append(f"# {task_id} — {title}（配分 {points}，runs={runs_expected}）")
    lines.append("")
    lines.append("## 題目（送給模型的原文，六家逐位元相同）")
    lines.append("")
    lines.append("```text")
    lines.append(_prompt_text(task_spec).rstrip() or "（spec 未提供 user 訊息）")
    lines.append("```")
    lines.append("")

    lines.append("## Checkpoints（逐條填分）")
    lines.append("")
    lines.append("| id | 配分 | 判定 | 要判什麼 | " + " | ".join(labels) + " |")
    lines.append("|---|---|---|" + "---|" * len(labels))
    for item in task_rubric.get("items", []):
        cells = []
        for lab in labels:
            got = answers.get(lab, {})
            mark = "☐" if got else "—"
            cells.append(mark)
        lines.append(
            f"| {item['id']} | {item['points']} | {item.get('check', 'manual')} "
            f"| {item.get('text', '')} | " + " | ".join(cells) + " |"
        )
    lines.append("")

    notes = task_rubric.get("notes") or []
    if notes:
        lines.append("## 本題注意")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("## 各模型答案（盲標）")
    lines.append("")
    for lab in labels:
        lines.append(f"### {lab}")
        lines.append("")
        got = answers.get(lab, {})
        if not got:
            lines.append("_（此模型沒有本題的可讀答案）_")
            lines.append("")
            continue
        for run_no in sorted(got):
            rec = got[run_no]
            lines.append(f"**run {run_no}** · {_usage_line(rec, budget)}")
            lines.append("")
            text = (rec.get("text") or "").strip()
            if text:
                lines.append("```text")
                lines.append(text)
                lines.append("```")
            else:
                lines.append("_空正文_")
            lines.append("")
    return "\n".join(lines) + "\n"


def build_readme(
    rubric: dict,
    label_count: int,
    task_ids: list[str],
    run_root: Path,
    out_dir: Path,
    missing: dict[str, list[str]],
) -> str:
    rules = rubric.get("scoring_rules", {})
    lines: list[str] = []
    lines.append("# SOEB v2 評分工作表")
    lines.append("")
    lines.append("## 你手上這份是什麼")
    lines.append("")
    lines.append(
        f"- 參賽模型 **{label_count} 顆**，全部匿名為 MODEL_A…；"
        "工作表中**不含** model_id／協定／通道／延遲／finish_reason／精確 token 數。"
    )
    lines.append(f"- 題目 {len(task_ids)} 題：{', '.join(task_ids)}")
    lines.append(
        f"- 配分：設計 {rules.get('total_points_design')} 分，"
        f"**可評分 {rules.get('total_points_available')} 分**"
        f"（缺口：{rules.get('unavailable')}）。報告一律寫「滿分 "
        f"{rules.get('total_points_available')}」。"
    )
    lines.append("")
    lines.append("## 怎麼填")
    lines.append("")
    lines.append("1. 開 `scores_template.json`，對每個 `MODEL_x` → 每題 → 每個 checkpoint 的")
    lines.append("   `per_run[]` **逐 run** 填（T2/T4/T7 有 2 個 run，其餘 1 個）：")
    lines.append("   - `points`：0 到該 checkpoint 的配分。`checkpoint_scale` 規定只有 0／半分／全分；")
    lines.append("     半分僅用於「方向對但缺關鍵一項」。")
    lines.append("   - `verdict`：`pass` / `partial` / `fail` / `unscored`")
    lines.append("   - `evidence`：**引用答案裡的一句話**。沒有引文的分數不接受——")
    lines.append("     這是要擋「憑印象給分」，也是日後爭議時唯一的憑據。")
    lines.append("   - 只評 run 1、run 2 留 `unscored` 也可以，但**該題 stability 會標成 unknown**，")
    lines.append("     報告會列出這個缺口（不穩定不得被平均吃掉，缺證據也不得被當成穩定）。")
    lines.append("2. `auto:*` 的 checkpoint 已由 `check_mech.py` 靜態判過（見 `mech.json`），")
    lines.append("   **不要重複手動判**，直接沿用；若你認為靜態判定有誤，改 `verdict` 並在")
    lines.append("   `evidence` 寫明推翻理由。")
    lines.append("3. `global_penalties`：每個模型一組，命中就記 `{id, points, evidence}`。")
    lines.append("4. 填完執行 `score.py` 算總分並產報告。**不要手算**——手算會加錯。")
    lines.append("")
    lines.append("## 評分尺度")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(rules.get("checkpoint_scale", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## stability 規則（同題多 run 怎麼算）")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(rules.get("stability", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    penalties = rubric.get("global_penalties") or []
    if penalties:
        lines.append("## 全域扣分項（跨題適用）")
        lines.append("")
        lines.append("| id | 名稱 | 扣分 | 說明 |")
        lines.append("|---|---|---|---|")
        for p in penalties:
            lines.append(
                f"| {p.get('id')} | {p.get('name')} | {p.get('penalty')} | {p.get('text', '')} |"
            )
        lines.append("")
    unfair = rubric.get("unfair_items") or []
    if unfair:
        lines.append("## 不計分項（已確認為題目不公）")
        lines.append("")
        for u in unfair:
            lines.append(f"- `{u.get('id')}`：{u.get('reason', '')} ⇒ 目前處置：{u.get('handling', '')}")
        lines.append("")
    lines.append("## 已知限制（報告必須照抄，不可省略）")
    lines.append("")
    lines.append(
        "- **未執行模型產生的程式碼。** `auto:parses`／`auto:test_count` 等只用 `ast.parse` "
        "與正則驗結構完整性；「這段程式真的跑得起來、測試真的會過」本輪**未驗證**。"
    )
    lines.append(
        "- **模型可能在答案中自稱身分。** 工作表已移除結構化欄位，但正文無法自動去識別；"
        "若你在答案裡看到自稱，請在評分紀錄註明，不要據此回推是哪一顆。"
    )
    lines.append(
        "- **不要先讀機械表。** `_metrics.md`／`mech.json`／`manifest.json` 含逐模型延遲、"
        "token 數與通道；先看它們再評分，等於自己拆掉盲評。評分完成後再看。"
    )
    lines.append(
        "- **`effort` 未送。** 六家的 thinking 預設值不同卻被當成同一組條件；"
        "本輪結論不得解讀為「某家的推理深度就是這樣」。"
    )
    lines.append("")
    if missing:
        lines.append("## 缺件（尚未跑完或呼叫失敗，不列入本次評分）")
        lines.append("")
        for lab, tasks in missing.items():
            lines.append(f"- {lab}：缺少 {', '.join(tasks)}")
        lines.append("")
    lines.append("## 檔案位置")
    lines.append("")
    lines.append(f"- 來源 run：`{run_root}`")
    lines.append(f"- 本工作表：`{out_dir}`")
    lines.append(
        "- 身分映射：在**本目錄之外**，檔名 `MAPPING_DO_NOT_OPEN_BEFORE_SCORING.json`。"
        "**評分完成前不要打開**；打開後請在評分紀錄上註明，否則盲評失效。"
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def build_scores_template(
    rubric: dict,
    labels: list[str],
    spec_tasks: dict[str, dict],
    spec_sha: str | None,
) -> dict:
    """產生待填的評分骨架。

    **每個 run 分開填。** rubric 的 `task_score` 是「各 run 整題得分取平均」，
    `stability` 是「各 run 極差 ≤ 0.15×配分」。若骨架只給單一格，
    評分者填的會是「他心裡的平均」，stability 就永遠算不出來——那不是漏填，
    是結構上不可能。
    """
    scores: dict[str, dict] = {}
    for lab in labels:
        per_task: dict[str, Any] = {}
        for tid, tr in rubric["tasks"].items():
            if tid not in spec_tasks:
                continue
            runs = int(spec_tasks[tid].get("runs", 1) or 1)
            per_task[tid] = {
                "points": tr.get("points"),
                "runs": runs,
                "items": {
                    it["id"]: {
                        "max_points": it["points"],
                        "check": it.get("check", "manual"),
                        "per_run": [
                            {"points": None, "verdict": "unscored", "evidence": ""}
                            for _ in range(runs)
                        ],
                    }
                    for it in tr.get("items", [])
                },
            }
        scores[lab] = {
            "tasks": per_task,
            "global_penalties": [],
            "judge_note": "",
        }
    return {
        "worksheet_version": 1,
        "suite": rubric.get("suite"),
        "rubric_version": rubric.get("rubric_version"),
        "bound_spec_sha256": rubric.get("bound_spec_sha256"),
        "observed_spec_sha256": spec_sha,
        "labels": labels,
        "judge": {
            "judge_id": None,
            "is_candidate": None,
            "note": "填你的身分。judge_requirement 要求評分者不得是候選模型之一。",
        },
        "scoring": scores,
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 評分工作表產生器（唯讀輸入）")
    ap.add_argument("--run-root", required=True, help="含各模型子目錄的根目錄")
    ap.add_argument("--rubric", default=str(REPO_ROOT / "scripts" / "soeb" / "rubric.json"))
    ap.add_argument("--spec", default=str(REPO_ROOT / "scripts" / "soeb" / "tasks" / "soeb-v1.json"))
    ap.add_argument("--out-dir", default=None, help="預設 <run-root>/_worksheet")
    ap.add_argument("--only", default=None, help="只產這些題目（逗號分隔，例如 T5,T6）")
    args = ap.parse_args(argv)

    run_root = Path(args.run_root)
    if not run_root.is_dir():
        print(f"Error: run-root 不存在：{run_root}", file=sys.stderr)
        return 2

    rubric = _load_json(Path(args.rubric))
    spec = _load_json(Path(args.spec))

    # manifest 兩種佈局都要吃：彙總根的子目錄、或 run_root 本身就是單顆 run。
    manifests: list[Path] = []
    if (run_root / "manifest.json").exists():
        manifests.append(run_root / "manifest.json")
    for child in sorted(run_root.iterdir()):
        if child.is_dir() and (child / "manifest.json").exists():
            manifests.append(child / "manifest.json")

    budget = None
    spec_sha = None
    for mf in manifests:
        if budget is None:
            budget = _manifest_budget(mf)
        if spec_sha is None:
            try:
                spec_sha = _load_json(mf).get("spec_sha256")
            except Exception:
                pass

    runs = discover_runs(run_root)
    if not runs:
        print(f"Error: {run_root} 下沒有任何含 observations.jsonl 的子目錄", file=sys.stderr)
        return 2

    out_dir = assert_not_under_data(
        Path(args.out_dir) if args.out_dir else run_root / "_worksheet"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    label_map = blind_label_map(sorted(runs))
    labels = sorted(label_map.values())

    # 每題 × 每標籤 × 每 run 的答案。洩漏欄位不寫進工作表。
    spec_tasks = {t["id"]: t for t in spec.get("tasks", [])}
    rubric_tasks = rubric.get("tasks", {})
    wanted = list(rubric_tasks)
    if args.only:
        only = {s.strip() for s in args.only.split(",") if s.strip()}
        wanted = [t for t in wanted if t in only]

    written: list[str] = []
    missing: dict[str, list[str]] = {}
    for tid in wanted:
        if tid not in spec_tasks:
            print(f"Warn: rubric 有 {tid} 但 spec 沒有，跳過（先跑 rubric_lint）", file=sys.stderr)
            continue
        answers: dict[str, dict[int, dict]] = {}
        for lab in labels:
            answers[lab] = {}
        for raw_mid, recs in runs.items():
            lab = label_map[raw_mid]
            for rec in recs:
                if rec.get("task") != tid:
                    continue
                if not rec.get("ok") or not (rec.get("text") or "").strip():
                    continue
                clean = {k: v for k, v in rec.items() if k not in LEAKY_FIELDS}
                answers[lab][int(rec.get("run", 1))] = clean
        for lab in labels:
            if not answers[lab]:
                missing.setdefault(lab, []).append(tid)
        md = build_task_markdown(tid, spec_tasks[tid], rubric_tasks[tid], labels, answers, budget)
        path = out_dir / f"{tid}.md"
        path.write_text(md, encoding="utf-8", newline="\n")
        written.append(path.name)

    tpl = build_scores_template(rubric, labels, spec_tasks, spec_sha)
    tpl_path = out_dir / "scores_template.json"
    tpl_path.write_text(
        json.dumps(tpl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    written.append(tpl_path.name)

    readme = build_readme(rubric, len(labels), [t for t in wanted if t in spec_tasks],
                          run_root, out_dir, missing)
    (out_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    written.append("README.md")

    # 映射檔：放在工作表目錄之外，檔名寫明何時才能開。
    # 位置取 out_dir 的父目錄：對彙總根是 `v2/MAPPING_…`，對單顆測試是 `_probe/…`，
    # 兩者都保證「不在工作表目錄內」，又不會把身分檔塞進某顆模型的乾淨 run 目錄。
    mapping_path = assert_not_under_data(out_dir.parent / "MAPPING_DO_NOT_OPEN_BEFORE_SCORING.json")
    mapping_path.write_text(
        json.dumps(
            {
                "warning": "評分完成前不要打開這個檔。打開後請在評分紀錄上註明。",
                "run_root": str(run_root),
                "label_to_model": {v: k for k, v in label_map.items()},
                "calls_per_model": {label_map[k]: len(v) for k, v in runs.items()},
                "worksheet_dir": str(out_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    print("=" * 72)
    print(f"run-root : {run_root}")
    print(f"模型     : {len(labels)} 顆 → {', '.join(labels)}")
    print(f"題目     : {', '.join(t for t in wanted if t in spec_tasks)}")
    print(f"輸出     : {out_dir}")
    for name in written:
        size = (out_dir / name).stat().st_size
        print(f"  - {name:<22} {size:>9,} bytes")
    print(f"映射檔   : {mapping_path.name}（在工作表目錄之外，評分完成前不要開）")
    if missing:
        print("-" * 72)
        print("缺件（不列入評分）：")
        for lab, tasks in sorted(missing.items()):
            print(f"  {lab}: {', '.join(tasks)}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
