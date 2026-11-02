"""SOEB 評分題包產生器——把「題目＋checkpoint＋六份匿名答案」併成**單題自足**的檔案。

為什麼不能直接拿 `build_worksheet.py` 的輸出交給其他模型：

1. **工作表是給人打勾的，不是給模型吃的。** 它的 checkpoint 表是 `☐` 勾選格，
   分數要靠人另外填進 `scores_template.json`。模型拿到它只會回一段散文，
   而散文無法機器計分（`score.py` 讀的是 JSON）。⇒ 這裡每一包都自帶
   **回填格式＋空白骨架**，評分模型照填即可。
2. **一次交全部會爆 context。** 七題合計約 55 萬字元（中文 ≈ 1 字元 1 token 等級），
   連 200k 視窗的評分模型都會被截斷；截斷後的評分是無效評分，但**看起來一樣像評分**。
   ⇒ 一題一檔，每檔 3 萬～11 萬位元組，可以分給七個不同評分者。
3. **盲評不能被「相對排序」污染。** 同一包裡放六份答案，評分者很自然會排名，
   而排名會把分數壓成「最好=滿分」。⇒ 規則明確要求 **絕對評分**、不得輸出排名與總結。
4. **引文必須是原文。** 光寫「請附引文」沒有用。⇒ 回填格式把 evidence 獨立成陣列，
   且 `merge_judge_scores.py` 會**逐格驗證引文真的是該 run 答案的子字串**，對不上就拒收。

輸入完全唯讀、不發任何請求、不需要金鑰、不寫 `data/`。

用法：

    .venv\\Scripts\\python.exe scripts\\soeb\\build_judge_packet.py ^
        --run-root tests\\results\\soeb\\v2 ^
        --out-dir  tests\\results\\soeb\\_probe\\judge_packets

輸出（全在 `--out-dir`）：

    README.md               怎麼把七包交出去、怎麼收回、怎麼合併
    JUDGE_<T>.md            單題自足題包（可直接貼給評分模型）
    _template_scores.json   合併工具的空白母片（＝ build_worksheet 的同一份骨架）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

try:  # pragma: no cover - 取決於呼叫方式
    from .anonymize import assert_not_under_data, blind_label_map
    from .build_worksheet import (
        _load_json,
        _manifest_budget,
        _prompt_text,
        _usage_line,
        build_scores_template,
        discover_runs,
    )
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from anonymize import assert_not_under_data, blind_label_map
    from build_worksheet import (
        _load_json,
        _manifest_budget,
        _prompt_text,
        _usage_line,
        build_scores_template,
        discover_runs,
    )


def _force_utf8_stdio() -> None:
    """cp950 主控台遇到中文／箭頭會直接崩，先換成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def collect_answers(
    runs: dict[str, list[dict]],
    label_map: dict[str, str],
    task_id: str,
) -> dict[str, dict[int, dict]]:
    """{label: {run_index: record}}，只留可評分的紀錄。

    `ok=false` 或空白的紀錄不是「答得爛」，是**評測失效**；把它當成 0 分會讓
    「呼叫失敗」與「能力不足」長得一樣。⇒ 直接不收，並由呼叫端標成缺件。
    """
    answers: dict[str, dict[int, dict]] = {}
    for raw_mid, recs in runs.items():
        lab = label_map[raw_mid]
        for rec in recs:
            if rec.get("task") != task_id:
                continue
            if not rec.get("ok") or not (rec.get("text") or "").strip():
                continue
            answers.setdefault(lab, {})[int(rec.get("run", 1))] = rec
    return answers


def _skeleton(task_id: str, labels: list[str], items: list[dict], runs: int) -> str:
    """回填骨架。刻意把每一格都列出來，不留「依此類推」。

    留省略號的骨架會讓模型自行決定要補哪些鍵，缺一格就得退回重問；
    全部列出來看起來長，但**只佔幾 KB**，換來的是可驗證的完整回覆。
    """
    points: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    for lab in labels:
        points[lab] = {it["id"]: [None] * runs for it in items}
        evidence[lab] = {it["id"]: [""] * runs for it in items}
    payload = {
        "fragment_version": 1,
        "task": task_id,
        "judge": {"id": "<你的模型名稱與版本，例如 gpt-5.1>", "is_candidate": False},
        "points": points,
        "evidence": evidence,
        "global_penalties": [],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_packet(
    task_id: str,
    spec_task: dict,
    rubric_task: dict,
    rubric: dict,
    labels: list[str],
    answers: dict[str, dict[int, dict]],
    budget: int | None,
    missing: list[str],
) -> str:
    runs = int(spec_task.get("runs", 1) or 1)
    cap = rubric_task.get("points")
    items: list[dict] = list(rubric_task.get("items") or [])
    stability = (rubric.get("scoring_rules") or {}).get("stability") or {}
    penalised = set(stability.get("applies_to") or [])

    L: list[str] = []
    L.append(f"# 評分任務：SOEB 第 {task_id} 題（單題獨立評分）")
    L.append("")
    L.append(f"本題標題：{rubric_task.get('title', '')}　·　**本題滿分 {cap} 分**")
    L.append("")
    L.append("## 你的角色")
    L.append("")
    L.append(
        "你是評分者。這份檔案是**自足**的：題目原文、評分項、六份匿名答案都在裡面。"
        "你不需要、也不應該去找任何其他檔案。"
    )
    L.append("")

    L.append("## 硬性規則（違反其中一條，這份評分作廢）")
    L.append("")
    L.append(
        "1. **六份答案只標 MODEL_A…MODEL_F，不要猜哪一份來自哪個模型。** "
        "答案裡若出現產品名、自稱身分或特定寫作風格，那是**無效訊號**，不得據此推論，"
        "也不得因此調高或調低分數。"
    )
    L.append(
        "2. **絕對評分，不是排名。** 逐格判斷「這份答案有沒有做到這一條」，"
        "不要因為「另一份更好」就把這份壓低。六份之間不互相比較。"
    )
    L.append(
        f"3. **每個 checkpoint 只能是 0 / 半分 / 全分三態**（0、該項配分的一半、該項配分）。"
        "半分僅用於「方向對但缺關鍵一項」。不得給其他數值。"
    )
    L.append(
        "4. **每個分數都必須附引文。** 從該模型該 run 的答案裡**原樣複製**一小段"
        "（10～60 字，逐字相同、不要改寫、不要翻譯）。合併工具會逐格比對"
        "引文是否真的出現在那份答案裡；**對不上就整份退回**。"
        "給 0 分同樣要引文——引「它寫了什麼所以不成立」或「通篇未提及」所在的位置。"
    )
    L.append(
        "5. **不獎勵長度、格式、語氣。** 短而精準可以全分；冗長但沒回答到點的不能得分。"
        "標題漂亮、表格整齊都不是得分理由。"
    )
    L.append(
        "6. **不要輸出排名、不要寫「整體而言誰最好」之類的總結。** "
        "總結會讓你的逐格分數向自己的敘事靠攏，也會污染盲評。只填數字與引文。"
    )
    L.append(
        "7. **只輸出一個 JSON 區塊**（格式見本檔最後一節），不要加任何其他說明文字。"
    )
    L.append("")

    L.append("## 題目原文（送給六個模型的文字逐位元相同）")
    L.append("")
    L.append("```text")
    L.append(_prompt_text(spec_task).strip())
    L.append("```")
    L.append("")

    L.append("## 本題評分項（逐條給分）")
    L.append("")
    L.append("| id | 配分 | 要判什麼 |")
    L.append("|---|---|---|")
    for it in items:
        L.append(f"| {it['id']} | {it['points']} | {it.get('text', '')} |")
    L.append("")
    if runs > 1:
        L.append(
            f"本題每份答案都有 **{runs} 次獨立作答**（run 1…run {runs}）。"
            "**每一次分開評**，不要把兩次先自己在腦中平均——"
            "平均會讓「兩次差很多」這個最重要的訊號消失。"
        )
        L.append("")

    notes = rubric_task.get("notes") or []
    if notes:
        L.append("## 本題注意")
        L.append("")
        for n in notes:
            L.append(f"- {n}")
        L.append("")

    if task_id in penalised:
        L.append("## 穩定性（本題適用）")
        L.append("")
        L.append(f"- 規則：{stability.get('rule', '')}")
        L.append(f"- {stability.get('note', '')}")
        L.append("- 這條由合併工具自動算，**你不需要自己判 stable/unstable**。")
        L.append("")

    unfair = [u for u in (rubric.get("unfair_items") or []) if u.get("task") == task_id]
    if unfair:
        L.append("## 本題已知不公項（不計分，看到也不要扣）")
        L.append("")
        for u in unfair:
            L.append(f"- `{u.get('item')}`：{u.get('problem', '')} ⇒ 處置：{u.get('handling', '')}")
        L.append("")

    if missing:
        L.append("## 缺件（不列入評分，**不要給 0 分**）")
        L.append("")
        L.append(
            f"- {', '.join(missing)} 這一題沒有可用答案（呼叫失敗或預算耗盡）。"
            "請在回覆的 `points`／`evidence` 裡**保留 `null` 與空字串**，不要填 0——"
            "填 0 等於宣稱「答得很爛」，那是不同的事實。"
        )
        L.append("")

    L.append("## 六份匿名答案")
    L.append("")
    for lab in labels:
        per_run = answers.get(lab) or {}
        if not per_run:
            continue
        for r in sorted(per_run):
            rec = per_run[r]
            L.append(f"### {lab} — run {r}")
            L.append("")
            L.append(f"狀態：{_usage_line(rec, budget)}")
            L.append("")
            L.append("```text")
            L.append((rec.get("text") or "").rstrip())
            L.append("```")
            L.append("")

    penalties = rubric.get("global_penalties") or []
    if penalties:
        L.append("## 附錄：全域扣分項（**只有在本題真的看到才記**，沒看到就留空陣列）")
        L.append("")
        for p in penalties:
            L.append(f"- **{p.get('id')} {p.get('name')}**：{p.get('rule', '')}")
            if p.get("why"):
                L.append(f"  - 理由：{p['why']}")
            if p.get("note"):
                L.append(f"  - 註：{p['note']}")
            L.append("  - 引文要求：必須指出答案裡的具體字串。")
        L.append("")
        L.append(
            "記法：`{\"id\": \"G1\", \"target\": \"MODEL_B\", \"points\": -1, \"evidence\": \"引文\"}`。"
            "`points` 一律填**負值或 0**（正數會被拒收）。"
            "G2／G3／G5 是「該格判 0」而非扣分，請直接把對應 checkpoint 填 0，"
            "並在此記一筆 `points: 0` 的說明。"
        )
        L.append("")

    L.append("## 回覆格式（只貼這一個 JSON 區塊）")
    L.append("")
    L.append(
        "- `points[模型][checkpoint]` 是**陣列，長度＝run 數**，數值只能是 0／半分／全分。"
    )
    L.append("- `evidence[模型][checkpoint]` 同長度，每一格是**該 run 答案裡的原文片段**。")
    L.append("- `judge.is_candidate`：你是否為本次評測的候選模型之一。若不是，填 `false`；"
             "若你其實是候選模型，請填 `true`——那會讓這份評分被拒收（這是刻意的）。")
    L.append("- 缺件的模型請保持該格為 `null` 與 `\"\"`。")
    L.append("")
    L.append("```json")
    L.append(_skeleton(task_id, labels, items, runs))
    L.append("```")
    L.append("")
    return "\n".join(L) + "\n"


def build_readme(
    out_dir: Path,
    labels: list[str],
    tasks: list[str],
    rubric: dict,
    missing: dict[str, list[str]],
) -> str:
    rules = rubric.get("scoring_rules") or {}
    L: list[str] = []
    L.append("# 交給其他模型評分的題包")
    L.append("")
    L.append("## 這是什麼")
    L.append("")
    L.append(
        f"- **{len(labels)} 份答案**，全部匿名為 {', '.join(labels)}；"
        "題包內**不含**任何可識別來源的欄位（模型名稱、服務通道、請求協定、延遲、用量）。"
    )
    L.append(f"- **一題一檔**：{', '.join(tasks)}。每檔自足（題目＋評分項＋六份答案＋回填格式）。")
    L.append(
        f"- 滿分 {rules.get('total_points_available')} 分（設計 {rules.get('total_points_design')} 分；"
        "缺口是 T8 未落地，**不得換算成百分比宣稱 100 分制**）。"
    )
    L.append(
        "- **評分者不得是候選模型之一**（含產生這份題包的模型）。"
        "回覆裡的 `judge.is_candidate` 若填 `true`，合併工具會直接拒收。"
    )
    L.append("")
    L.append("## 怎麼用")
    L.append("")
    L.append("1. **一次只交一題。** 把 `JUDGE_<T>.md` 整份貼給評分模型（或當附件上傳）。")
    L.append(
        "   七題可以給**同一個**評分模型跑七輪，也可以給**七個不同**的評分模型各跑一題。"
    )
    L.append(
        "   後者較好：同一模型連跑七題會出現「前面幾題的分數尺度」錨定效應，"
        "而且它會開始記得自己上一輪給過的標準。"
    )
    L.append("2. 評分模型回一個 JSON 區塊。把它存成 `<T>.json`，全部丟進一個資料夾。")
    L.append("3. 合併並驗證：")
    L.append("")
    L.append("```powershell")
    L.append(".venv\\Scripts\\python.exe scripts\\soeb\\merge_judge_scores.py `")
    L.append(f"    --template '{out_dir / '_template_scores.json'}' `")
    L.append("    --run-root 'tests\\results\\soeb\\v2' `")
    L.append("    --fragments '<放 <T>.json 的資料夾>' `")
    L.append(f"    --out '{out_dir / 'scores_merged.json'}'")
    L.append("```")
    L.append("")
    L.append("4. 算成績：")
    L.append("")
    L.append("```powershell")
    L.append(".venv\\Scripts\\python.exe scripts\\soeb\\score.py `")
    L.append(f"    --scores '{out_dir / 'scores_merged.json'}'")
    L.append("```")
    L.append("")
    L.append("## 合併工具會替你擋掉什麼")
    L.append("")
    L.append("- **引文不是原文** ⇒ 拒收。它會逐格確認引文真的是該模型該 run 答案的子字串"
             "（忽略空白差異）。這是唯一能擋「憑印象給分」的機制。")
    L.append("- **分數不在 0／半分／全分三態** ⇒ 拒收（例如配分 3 卻填 1.7）。")
    L.append("- **run 數不符** ⇒ 拒收（少評一次會被靜默丟掉，所以不允許）。")
    L.append("- **同一題有兩份 fragment** ⇒ 拒收（兩個評分者的同題分數不能相加或平均，必須挑一份）。")
    L.append("- **`is_candidate=true`／沒填 judge id** ⇒ 拒收。")
    L.append("")
    if missing:
        L.append("## 缺件（不列入評分）")
        L.append("")
        for lab, ts in sorted(missing.items()):
            L.append(f"- {lab}：缺少 {', '.join(ts)}")
        L.append("")
        L.append(
            "缺件是**評測失效**（呼叫失敗或預算耗盡），不是 0 分。"
            "報告必須分開列出，不得用平均值把它吃掉。"
        )
        L.append("")
    L.append("## 不要做的事")
    L.append("")
    L.append(
        "- **不要先看機械表。** `_metrics.md`／`mech.json`／`manifest.json` 含逐模型延遲、"
        "token 數與通道，先看等於自己拆掉盲評。"
    )
    L.append(
        "- **不要在評分完成前打開映射檔**（在題包目錄之外，檔名 "
        "`MAPPING_DO_NOT_OPEN_BEFORE_SCORING.json`）。"
    )
    L.append("- **不要把同一題的兩份 fragment 都合併進來。**")
    L.append("")
    L.append("## 已知限制（報告必須照抄）")
    L.append("")
    L.append(
        "- **未執行模型產生的程式碼。** 只用 `ast.parse` 與正則驗結構完整性；"
        "「這段程式真的跑得起來、測試真的會過」本輪**未驗證**。"
    )
    L.append(
        "- **`effort` 未送。** 六家的 thinking 預設值不同卻被當成同一組條件；"
        "本輪結論不得解讀為「某家的推理深度就是這樣」。"
    )
    L.append(
        "- **T4 的 `OFF ≠ Temporal-null` 不計分。** 題目文字從未出現 `OFF`，"
        "要求區分它等於用題目沒給的資訊扣分。"
    )
    L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 單題自足評分題包產生器（唯讀輸入）")
    ap.add_argument("--run-root", required=True, help="含各模型子目錄的根目錄")
    ap.add_argument("--rubric", default=str(REPO_ROOT / "scripts" / "soeb" / "rubric.json"))
    ap.add_argument("--spec", default=str(REPO_ROOT / "scripts" / "soeb" / "tasks" / "soeb-v1.json"))
    ap.add_argument(
        "--out-dir",
        default=None,
        help="預設 <run-root>/_judge_packets",
    )
    ap.add_argument("--only", default=None, help="只產這些題目（逗號分隔，例如 T5,T6）")
    args = ap.parse_args(argv)

    run_root = Path(args.run_root)
    if not run_root.is_dir():
        print(f"Error: run-root 不存在：{run_root}", file=sys.stderr)
        return 2

    rubric = _load_json(Path(args.rubric))
    spec = _load_json(Path(args.spec))

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
            try:
                budget = _manifest_budget(mf)
            except Exception:
                budget = None
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
        Path(args.out_dir) if args.out_dir else run_root / "_judge_packets"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    label_map = blind_label_map(sorted(runs))
    labels = sorted(label_map.values())
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
        answers = collect_answers(runs, label_map, tid)
        for lab in labels:
            if not answers.get(lab):
                missing.setdefault(lab, []).append(tid)
        missing_here = [lab for lab in labels if not answers.get(lab)]
        md = build_packet(
            tid,
            spec_tasks[tid],
            rubric_tasks[tid],
            rubric,
            labels,
            answers,
            budget,
            missing_here,
        )
        path = out_dir / f"JUDGE_{tid}.md"
        path.write_text(md, encoding="utf-8", newline="\n")
        written.append(path.name)

    tpl = build_scores_template(rubric, labels, spec_tasks, spec_sha)
    tpl_path = out_dir / "_template_scores.json"
    tpl_path.write_text(
        json.dumps(tpl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    written.append(tpl_path.name)

    readme = build_readme(out_dir, labels, [t for t in wanted if t in spec_tasks], rubric, missing)
    (out_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    written.append("README.md")

    print("=" * 72)
    print(f"run-root : {run_root}")
    print(f"模型     : {len(labels)} 顆 → {', '.join(labels)}")
    print(f"題目     : {', '.join(t for t in wanted if t in spec_tasks)}")
    print(f"輸出     : {out_dir}")
    for name in written:
        size = (out_dir / name).stat().st_size
        print(f"  - {name:<26} {size:>9,} bytes")
    if missing:
        print("-" * 72)
        print("缺件（不列入評分）：")
        for lab, tasks in sorted(missing.items()):
            print(f"  {lab}: {', '.join(tasks)}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
