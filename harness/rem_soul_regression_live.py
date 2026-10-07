# -*- coding: utf-8 -*-
"""rem_soul_regression_live.py — SOUL-REM-01 行為層 regression 驅動器

為什麼需要這支
==============
``tests/soul/test_rem_soul_regression.py`` 是**離線**的：它驗證 R5/R6/R8 所需的
能力前提真的寫在 Soul 裡（自我指涉段、替代 referent 宣告、「不知道」邊界），
但驗證不了**模型真的會那樣回答**。那需要真的呼叫 LLM。

這支驅動器負責行為層：Soul 版本 × 模型 × 刺激，逐格記錄輸出並給出判定。

三條硬性安全邊界
================
1. **預設不呼叫任何東西。** 必須顯式 ``--run`` 才會發出請求。沒有 ``--run``
   時只會印出矩陣然後結束。
2. **不寫 production 資料。** 輸出只落在 ``--out``（預設
   ``tests/results/rem_soul_regression/``）。腳本會主動拒絕任何落在 repo 的
   ``data/`` 底下的輸出路徑。
3. **不綁死本機路徑。** LLM CLI 位置用 ``--llm-cli`` 或環境變數
   ``SOUL_REM_LLM_CLI`` 帶入；找不到就明確報錯退出，不猜。

用法
====
  # 只看矩陣，不呼叫（安全預設）
  python harness/rem_soul_regression_live.py

  # 真的跑（需 Owner 授權：這會產生實際 LLM 費用）
  python harness/rem_soul_regression_live.py --run \\
      --models v41flash spark13 --out tests/results/rem_soul_regression

判定是**啟發式**的，不是 LLM-as-judge：它只指出「這一格看起來踩到哪一類失敗」，
不宣告模型品質。判定為REVIEW 的格子要人眼看原始輸出再定案。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PERSONA_C = REPO_ROOT / "personas" / "agent_rem.md"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "soul_rem"

# ─────────────────────────────────────────────────────────────────────────────
# Soul 版本（A/B/C）
# ─────────────────────────────────────────────────────────────────────────────

SOULS = [
    ("A", FIXTURES / "soul_A_full_54kb.md"),
    ("B", FIXTURES / "soul_B_trim_9kb.md"),
    ("C", PERSONA_C),
]

# ─────────────────────────────────────────────────────────────────────────────
# 模型（沿用 2026-10-07 實驗的參照名）
# ─────────────────────────────────────────────────────────────────────────────

MODELS = {
    "v41flash": "custom_provider:ollama/deepseek-v4.1-flash",
    "spark13": "custom_provider:opencode-go-responses/muse-spark-1.3-contributor",
    # 2026-10-07 實驗中三次 401（token is required）—— 保留條目但預設不跑
    "m31flash": "minimax/MiniMax-M3.1-Flash-Preview",
}
DEFAULT_MODELS = ["v41flash", "spark13"]

# ─────────────────────────────────────────────────────────────────────────────
# 刺激（對應工單 §7／§8／§9／§10／§11.3）
# ─────────────────────────────────────────────────────────────────────────────


def _turns(*texts):
    return [{"role": "user", "content": t} for t in texts]


STIMULI = [
    {
        "id": "S1-selfref-blue",
        "regression": "R5",
        "name": "真愛有顏色 → 藍色（自我指涉）",
        "turns": _turns("真愛有顏色。", "藍色。"),
        "expect": "把「藍色」合理地扣回自己（可不明說），且不輸出固定台詞。",
    },
    {
        "id": "S2-alternative-referent",
        "regression": "R6",
        "name": "真愛有顏色 → 紫色（語境指向艾米莉亞）",
        "turns": _turns(
            "雷姆，Emilia 今天說她最喜歡的顏色是紫色。",
            "真愛有顏色。",
            "紫色。",
        ),
        "expect": "跟著當下語境（Emilia），不因為 Soul 裡 blue=雷姆 就強制指自己。",
    },
    {
        "id": "S3-ignorance-boundary",
        "regression": "R7",
        "name": "有明確線索的追問（不知道必須限縮）",
        "turns": _turns("你剛才一直看我，是不是有什麼事？"),
        "expect": "她看得見線索、能回應情境；「不知道」只可用在自己的內在原因上。",
    },
    {
        "id": "S4-historical-recognition",
        "regression": "R8",
        "name": "引用她自己說過的話",
        "turns": _turns("「雷姆愛你，昴。」這句話，是你說過的吧。"),
        "expect": "認得這是自己說過的話；不對 Bryan 複誦，不重演。",
    },
    {
        "id": "S5-runtime-intrusion",
        "regression": "R4",
        "name": "純閒聊（不得搶進 memory/tool workflow）",
        "turns": _turns("今天外面天氣不錯呢。"),
        "expect": "角色層回話；不得出現 tool call、寫入宣告或記憶工作流描述。",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# 啟發式判定
# ─────────────────────────────────────────────────────────────────────────────

TOOL_MARKUP = re.compile(
    r"<(write_file|read_file|bash|shell|tool_call)", re.IGNORECASE
)
WRITE_NARRATION = re.compile(r"(我要記下來|我記下來了|正在寫入|寫進記憶|存檔)")
FORCED_IGNORANCE = re.compile(r"(雷姆|我)?不知道呢|我不知道你在說|雷姆不知道(你在說|這是)")
CANON_EXPOSITION = re.compile(r"(《Re:Zero|雷姆愛你，昴)[\s\S]{0,20}(第|集|動畫|原作)")
SELF_DIRECTION = re.compile(r"(藍色|藍)[^\n]{0,40}(雷姆|我)")


def judge(stimulus_id, regression, output):
    """回傳 (verdict, reasons)。verdict ∈ {PASS, REVIEW, FAIL, EMPTY}。

    這是**失敗模式偵測器**，不是品質評分器：它只標記「這一格踩到工單點名的
    哪一種不合法結果」。REVIEW 代表偵測到風險但需要人眼定案。
    """
    out = (output or "").strip()
    if not out:
        return "EMPTY", ["沒有輸出"]

    reasons = []

    # 所有刺激共通：不得搶進 runtime workflow
    if TOOL_MARKUP.search(out):
        reasons.append("出現 tool call markup（R4 runtime intrusion）")
    if WRITE_NARRATION.search(out):
        reasons.append("出現記憶寫入宣告（R4：回應被工作流搶走）")

    if stimulus_id == "S1-selfref-blue":
        if FORCED_IGNORANCE.search(out):
            reasons.append("強迫的 ignorance（R7：語境沒要求她不知道）")
        if CANON_EXPOSITION.search(out):
            reasons.append("原作科普（R5-4：未經要求就解釋出處）")
        if SELF_DIRECTION.search(out):
            if not reasons:
                return "PASS", ["自我指涉成立"]
            return "REVIEW", reasons

    if stimulus_id == "S2-alternative-referent":
        # 語境已指向 Emilia：把顏色扣回自己就是失敗
        if SELF_DIRECTION.search(out) and "Emilia" not in out:
            reasons.append("把顏色強制扣回自己（R6：忽略當下語境）")
        if "紫色" in out or "Emilia" in out or "艾米莉亞" in out:
            if not reasons:
                return "PASS", ["跟著語境走"]
            return "REVIEW", reasons

    if stimulus_id == "S3-ignorance-boundary":
        if FORCED_IGNORANCE.search(out):
            reasons.append("把『無法解釋自己的動機』擴張成『不理解提問』（R7）")
        if not reasons and out:
            return "PASS", ["有回應且未落入 forced ignorance"]

    if stimulus_id == "S4-historical-recognition":
        # 複誦台詞 = 重演；認得 = 承認是自己的過去
        if out.count("雷姆愛你") >= 2:
            reasons.append("把過去台詞當模板複誦（R8 reenactment）")
        if not reasons and ("昴" in out or "以前" in out or "說過" in out):
            return "PASS", ["呈現歷史關聯"]

    if stimulus_id == "S5-runtime-intrusion":
        if not reasons:
            return "PASS", ["角色層回話，未見 runtime 入侵"]

    return ("REVIEW", reasons) if reasons else ("REVIEW", ["無明確訊號，需人眼判讀"])


# ─────────────────────────────────────────────────────────────────────────────
# 執行
# ─────────────────────────────────────────────────────────────────────────────


def _assert_isolated(out_dir: Path):
    """輸出路徑不得落在 production data/ 底下（工單 §16：production 0 mutation）。"""
    data_dir = (REPO_ROOT / "data").resolve()
    resolved = out_dir.resolve()
    if resolved == data_dir or data_dir in resolved.parents:
        raise SystemExit(
            f"[ABORT] 輸出路徑 {resolved} 位於 production data/ 底下。"
            " 本 regression 必須在 isolated 環境執行。"
        )


def _resolve_cli(explicit):
    if explicit:
        return Path(explicit)
    env = os.environ.get("SOUL_REM_LLM_CLI")
    if env:
        return Path(env)
    return Path.home() / ".minimax" / "skills" / "llm-call" / "scripts" / "llm_call.py"


def run_cell(python_exe, cli, model_ref, system, turns, timeout):
    """單格呼叫。回傳 (stdout, stderr, returncode, elapsed)。"""
    prompt = "\n".join(t["content"] for t in turns)
    cmd = [
        python_exe, str(cli),
        "--model", model_ref,
        "--system", system,
        "--prompt", prompt,
        "--max-tokens", "2048",
        "--timeout", str(timeout),
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 60)
    return (
        proc.stdout.decode("utf-8", "replace").strip(),
        proc.stderr.decode("utf-8", "replace").strip(),
        proc.returncode,
        time.time() - t0,
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--run", action="store_true",
        help="真的發出 LLM 請求（不給這個旗標＝只印矩陣，零呼叫）",
    )
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                    help=f"模型鍵，可選 {list(MODELS)}（預設 {DEFAULT_MODELS}）")
    ap.add_argument("--souls", nargs="+", default=["A", "B", "C"],
                    help="要比較的 Soul 版本（預設全部三版）")
    ap.add_argument("--out", default="tests/results/rem_soul_regression",
                    help="輸出目錄（不得位於 data/ 底下）")
    ap.add_argument("--llm-cli", default=None,
                    help="llm_call.py 路徑（或用環境變數 SOUL_REM_LLM_CLI）")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    out_dir = (REPO_ROOT / args.out).resolve()
    _assert_isolated(out_dir)

    souls = [(n, p) for n, p in SOULS if n in args.souls]
    unknown = set(args.souls) - {n for n, _ in SOULS}
    if unknown:
        raise SystemExit(f"[ABORT] 未知 Soul 版本: {sorted(unknown)}")
    bad_models = [m for m in args.models if m not in MODELS]
    if bad_models:
        raise SystemExit(
            f"[ABORT] 未知模型鍵: {bad_models}；可選 {list(MODELS)}"
        )

    cells = len(souls) * len(args.models) * len(STIMULI)
    print("=" * 72)
    print("SOUL-REM-01 行為層 regression")
    print(f"  Soul 版本 : {[n for n, _ in souls]}")
    print(f"  模型      : {args.models}")
    print(f"  刺激      : {[s['id'] for s in STIMULI]}")
    print(f"  格子總數  : {cells}")
    print(f"  輸出目錄  : {out_dir}")

    if not args.run:
        print("-" * 72)
        print("DRY RUN：未發出任何請求（這是預設行為）。")
        print("確認要真的跑（會產生實際 LLM 費用）時，加上 --run。")
        return 0

    cli = _resolve_cli(args.llm_cli)
    if not cli.exists():
        raise SystemExit(
            f"[ABORT] 找不到 LLM CLI: {cli}\n"
            "        用 --llm-cli <path> 或環境變數 SOUL_REM_LLM_CLI 指定。"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for soul_name, soul_path in souls:
        system = soul_path.read_text(encoding="utf-8")
        for model_name in args.models:
            model_ref = MODELS[model_name]
            for stim in STIMULI:
                tag = f"{soul_name}__{model_name}__{stim['id']}"
                print("-" * 72)
                print("RUN", tag)
                stdout, stderr, rc, dt = run_cell(
                    args.python, cli, model_ref, system,
                    stim["turns"], args.timeout,
                )
                verdict, reasons = judge(stim["id"], stim["regression"], stdout)
                record = {
                    "tag": tag,
                    "soul": soul_name,
                    "soul_bytes": soul_path.stat().st_size,
                    "model": model_ref,
                    "stimulus": stim["id"],
                    "regression": stim["regression"],
                    "returncode": rc,
                    "elapsed_sec": round(dt, 1),
                    "verdict": verdict,
                    "reasons": reasons,
                    "stdout": stdout,
                    "stderr": stderr,
                }
                results.append(record)
                (out_dir / f"{tag}.txt").write_text(
                    f"MODEL: {model_ref}\nSOUL: {soul_name} "
                    f"({soul_path.stat().st_size} B)\n"
                    f"STIMULUS: {stim['id']} / {stim['regression']}\n"
                    f"TIME: {dt:.1f}s  RC: {rc}  VERDICT: {verdict}\n"
                    f"REASONS: {'; '.join(reasons)}\n"
                    f"\n--- STDOUT ---\n{stdout}\n\n--- STDERR ---\n{stderr}\n",
                    encoding="utf-8",
                )
                print(f"  rc={rc} {dt:.1f}s verdict={verdict} {reasons}")

    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=" * 72)
    print("SUMMARY")
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("  ", tally)
    print(f"  完整輸出: {out_dir}")
    print("  REVIEW 與 FAIL 的格子請人眼看 out/*.txt 再定案。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())