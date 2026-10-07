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

驗收判準（Owner 裁決 2026-10-07，不可退回）
=============================================
**「聽得懂」＝ contextual self-reference，不是典故逐字辨識。**

    Soul may recognize that an utterance refers to her identity or traits
    without recognizing it as a recalled canonical quotation or past episode.

也就是說合格線是「她把當下的話理解成在描述我、對我說、或與我的自我屬性有關，
並依自己的 Soul / Canon 解讀它」——**不是**她能背出這句出自哪一集。
把標準拉到典故層會把模型推向作品閱讀理解／台詞檢索，也會誘使後續 regression
為了「證明聽得懂」又把 Canon Memory 膨脹回去。

因此本迴歸的閘門語意是：

    **C 不得出現由 Soul compression 導致的 character regression。**

不是「C 必須比 A 更好」。A（54 KB 原始版）是對照組，B（9 KB trim）是實驗組，
C 不得在任一格比它們更糟。
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

# 每顆模型的 max_tokens。
# spark13 是 reasoning 模型：2026-10-07 原始實驗記錄過它在 2048 會被
# reasoning token 吃光、輸出全空，重跑 8000 才取得結果（REPORT「失敗項」）。
# 這個坑不修，結果會被誤讀成「模型不懂」。
MAX_TOKENS = {
    "v41flash": 2048,
    "spark13": 8000,
    "m31flash": 2048,
}

# llm_call.py 在 Windows 上預設以系統 ANSI 編碼（cp950/Big5）寫 stdout。
# 直接 decode("utf-8") 會把每個中文字變成 U+FFFD，judge() 的中文 regex 全部失效
# —— 2026-10-07 首輪就踩過，15 格的輸出全毀。強制 UTF-8。
SUBPROCESS_ENCODING = "utf-8"

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
        "name": "引用她自己說過的話（辨識 ≠ 重演；典故記憶不是合格線）",
        "turns": _turns("「雷姆愛你，昴。」這句話，是你說過的吧。"),
        "expect": (
            "不得複誦／重演過去台詞（那是 compression 之外的 character regression）。"
            "但『能不能說出這是哪一段的回憶』不在合格線內 —— 依 Owner 裁決，"
            "contextual self-reference 才是判準，典故逐字辨識不是。"
        ),
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
OVER_EXPLANATION = re.compile(r"(因為.{0,12}藍色.{0,30}所以我(推斷|認為)|我推斷你)")

# 🔴 自我指涉的偵測（2026-10-07 修正）
#
# 舊版只認「藍色…雷姆」的鄰近共現，結果把
#     「……喔？主人是在說雷姆嗎？」
# 判成 REVIEW —— 但那**正是** Owner 定義的 contextual self-reference，
# 而且完全不需要重複「藍色」兩個字。
#
# 判準條文（docs/SOUL-SELF-REFERENCE-CRITERIA.md）：
#   Soul 可以理解一句話是在描述我、對我說、或與我的自我屬性有關，
#   而不必辨識出那是她曾說過的台詞。
# 所以自我指涉的**證據是「把話接回自己」**，不是「把觸發詞複述一遍」。
SELF_REFERENCE = re.compile(
    r"(在說雷姆|說的是雷姆|是雷姆嗎|雷姆嗎[？?]|指.{0,4}雷姆|雷姆的顏色|"
    r"是在說我|說的是我|用藍色.{0,10}(形容|指|說)|雷姆是那個|這是在說雷姆|"
    r"雷姆知道.{0,6}(呢|喔|嗎))"
)

# Context blindness：只把「藍色」當色彩學／色系話題，且完全沒有接回自己
CONTEXT_BLINDNESS = re.compile(
    r"(冷色|暖色|色系|代表沉靜|代表冷靜|藍色是一種|屬於藍色)"
)


def judge(stimulus_id, regression, output):
    """回傳 (verdict, reasons)。verdict ∈ {PASS, REVIEW, FAIL, EMPTY}。

    這是**失敗模式偵測器**，不是品質評分器：它只標記「這一格踩到工單點名的
    哪一種不合法結果」。REVIEW 代表偵測到風險但需要人眼定案。

    ⚠️ EMPTY 不代表角色失格。上游偶發回空字串時（2026-10-07 實測 v41flash
    會 rc=0 / stdout 空 / stderr 空），那是**缺資料**，不是失敗資料。
    呼叫端必須 retry 它，而不是把它算成 regression。
    """
    out = (output or "").strip()
    if not out:
        return "EMPTY", ["上游回空字串（rc=0、stderr 空）——缺資料，需 retry"]

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
        if OVER_EXPLANATION.search(out):
            reasons.append("過度解釋（R5-5：把內心推理整段講出來）")
        if reasons:
            return "FAIL", reasons
        if SELF_REFERENCE.search(out):
            return "PASS", ["自我指涉成立（把話接回自己）"]
        if CONTEXT_BLINDNESS.search(out) and not SELF_REFERENCE.search(out):
            return "FAIL", ["Context blindness（R5-1：只談色彩學，沒接到自己）"]
        # 其餘：把話問回去、留白、單純呼應 —— 依 Owner 判準屬合理 ambiguity
        return "REVIEW", ["未見明確自我指涉，也未犯錯——可能只是合理保留，需人眼判讀"]

    if stimulus_id == "S2-alternative-referent":
        # 語境已指向 Emilia：把顏色扣回自己就是失敗
        if SELF_REFERENCE.search(out) and "Emilia" not in out:
            reasons.append("把顏色強制扣回自己（R6：忽略當下語境）")
        if reasons:
            return "FAIL", reasons
        if "紫色" in out or "Emilia" in out or "艾米莉亞" in out:
            return "PASS", ["跟著語境走"]
        return "REVIEW", ["未確認是否跟著語境走，需人眼判讀"]

    if stimulus_id == "S3-ignorance-boundary":
        if FORCED_IGNORANCE.search(out):
            return "FAIL", ["把『無法解釋自己的動機』擴張成『不理解提問』（R7）"]
        return "PASS", ["有回應且未落入 forced ignorance"]

    if stimulus_id == "S4-historical-recognition":
        # 複誦台詞 = 重演 = 真的 character regression，必紅
        if out.count("雷姆愛你") >= 2:
            return "FAIL", ["把過去台詞當模板複誦（R8 reenactment）"]
        # 註意：這裡「不記得典故」不算失敗。Owner 裁決 —— contextual
        # self-reference 才是判準，典故逐字辨識不是（見模組 docstring）。
        return "PASS", ["未重演過去台詞（典故記憶不列入判準）"]

    if stimulus_id == "S5-runtime-intrusion":
        if reasons:
            return "FAIL", reasons
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


def _decode(raw):
    """把子行程輸出解成文字，寧可降級也不要丟失。

    正常情況下 subprocess env 已強制 PYTHONIOENCODING=utf-8，所以第一個分支就會
    命中。這裡的 fallback 是給「子行程自己忽略環境變數」的情況：寧可退回
    cp950，也不要用 replace 把中文變成一串 U+FFFD 然後讓判定失真。
    """
    for enc in (SUBPROCESS_ENCODING, "cp950", "gbk"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(SUBPROCESS_ENCODING, errors="replace")


def run_cell(python_exe, cli, model_ref, system, turns, timeout, max_tokens=2048):
    """單格呼叫。回傳 (stdout, stderr, returncode, elapsed)。"""
    prompt = "\n".join(t["content"] for t in turns)
    cmd = [
        python_exe, str(cli),
        "--model", model_ref,
        "--system", system,
        "--prompt", prompt,
        "--max-tokens", str(max_tokens),
        "--timeout", str(timeout),
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = SUBPROCESS_ENCODING
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 60, env=env)
    return (
        _decode(proc.stdout).strip(),
        _decode(proc.stderr).strip(),
        proc.returncode,
        time.time() - t0,
    )


def compute_gate(results):
    """把逐格 verdict 收斂成一個 compression gate。

    閘門語意（Owner 裁決）：**C 不得出現由 Soul compression 導致的 character
    regression** —— 不是「C 必須比 A 更好」。

    所以有兩條判��，各自獨立：

    1. `regressions` —— 同 (model, stimulus) 下，A 或 B 判定通過而 C 沒有。
       這是真正的「compression 造成的退步」。
    2. `red_lines` —— C 自己在紅線刺激上的絕對失敗：
       forced ignorance（S1/S3）與 runtime intrusion（S5）。
       這兩類與對照組無關，出現就是壓縮造成的退步。

    回傳 dict；``gate == "PASS"`` 表示壓縮沒有造成 character regression。
    """
    by_cell = {}
    for r in results:
        by_cell[(r["model"], r["stimulus"], r["soul"])] = r["verdict"]

    # 🔴 上游回空字串是**缺資料**，不是失敗資料。
    # 2026-10-07 首輪實測：v41flash 有 4 格 rc=0 / stdout 空 / stderr 空。
    # 把缺資料當成 regression 會誤報壓縮造成退步，所以這些格子單獨列出。
    #
    # 缺口必須分兩類，因為它對結論的影響不對稱：
    #   c_gaps        —— C 自己缺資料 ⇒ 對 C 沒有判定依據 ⇒ INCOMPLETE。
    #   baseline_gaps —— A/B 缺資料 ⇒ 不能宣稱「C 比 A 好」，
    #                     但**不能**宣稱 C 退步（根本沒有基準可比）。
    #                     比較因此變成單向：仍能抓到 A/B 通過而 C 沒過的情形。
    c_gaps = [
        {"tag": r["tag"], "model": r["model"], "stimulus": r["stimulus"]}
        for r in results
        if r["verdict"] == "EMPTY" and r["soul"] == "C"
    ]
    baseline_gaps = [
        {"tag": r["tag"], "model": r["model"], "stimulus": r["stimulus"]}
        for r in results
        if r["verdict"] == "EMPTY" and r["soul"] != "C"
    ]

    regressions = []
    for (model, stim, soul), verdict in by_cell.items():
        if soul != "C" or verdict == "EMPTY":
            continue
        for baseline in ("A", "B"):
            b = by_cell.get((model, stim, baseline))
            if b == "PASS" and verdict != "PASS":
                regressions.append(
                    {
                        "model": model,
                        "stimulus": stim,
                        "baseline": baseline,
                        "baseline_verdict": b,
                        "c_verdict": verdict,
                        "reasons": next(
                            r["reasons"] for r in results
                            if (r["model"], r["stimulus"], r["soul"]) == (model, stim, "C")
                        ),
                    }
                )

    red_lines = [
        {"model": r["model"], "stimulus": r["stimulus"], "reasons": r["reasons"]}
        for r in results
        if r["soul"] == "C"
        and r["stimulus"] in ("S1-selfref-blue", "S3-ignorance-boundary",
                              "S5-runtime-intrusion")
        and r["verdict"] == "FAIL"
    ]

    return {
        "gate": (
            "FAIL" if (regressions or red_lines)
            else ("INCOMPLETE" if c_gaps else "PASS")
        ),
        "regressions": regressions,
        "red_lines": red_lines,
        "c_gaps": c_gaps,
        "baseline_gaps": baseline_gaps,
        "comparison_is_one_sided": bool(baseline_gaps),
        "c_fail_count": sum(
            1 for r in results if r["soul"] == "C" and r["verdict"] == "FAIL"
        ),
    }


def _write_cell(out_dir, record):
    (out_dir / f"{record['tag']}.txt").write_text(
        f"MODEL: {record['model']}\nSOUL: {record['soul']} "
        f"({record['soul_bytes']} B)\n"
        f"STIMULUS: {record['stimulus']} / {record['regression']}\n"
        f"TIME: {record['elapsed_sec']}s  RC: {record['returncode']}  "
        f"VERDICT: {record['verdict']}\n"
        f"REASONS: {'; '.join(record['reasons'])}\n"
        f"\n--- STDOUT ---\n{record['stdout']}\n\n--- STDERR ---\n{record['stderr']}\n",
        encoding="utf-8",
    )


def _report(out_dir, results):
    """落盤 + 印摘要 + 回傳 gate。rescore 與正常路徑共用同一份呈現。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    gate = compute_gate(results)
    (out_dir / "gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("=" * 72)
    print("SUMMARY")
    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("  verdicts:", tally)
    print("-" * 72)
    print("COMPRESSION GATE (C 不得出現 compression 造成的 character regression)")
    print(f"  gate = {gate['gate']}")
    print(f"  C 的 FAIL 格數        : {gate['c_fail_count']}")
    print(f"  相對 A/B 的 regression: {len(gate['regressions'])}")
    print(f"  紅線絕對失敗          : {len(gate['red_lines'])}")
    print(f"  C 的缺資料            : {len(gate['c_gaps'])}")
    print(f"  基線的缺資料          : {len(gate['baseline_gaps'])}")
    for reg in gate["regressions"]:
        print(
            f"    [REGRESSION] {reg['model']} / {reg['stimulus']}: "
            f"{reg['baseline']}={reg['baseline_verdict']} -> C={reg['c_verdict']}"
            f"  {reg['reasons']}"
        )
    for rl in gate["red_lines"]:
        print(f"    [RED LINE] {rl['model']} / {rl['stimulus']}: {rl['reasons']}")
    for gap in gate["c_gaps"]:
        print(
            f"    [GAP: C] {gap['model']} / {gap['stimulus']} "
            "—— C 缺資料，gate 不能宣稱通過，用 --retry-gaps 補齊"
        )
    for gap in gate["baseline_gaps"]:
        print(
            f"    [GAP: baseline] {gap['model']} / {gap['stimulus']} "
            "—— 對照組缺資料：不能宣稱 C 比它好，但也不能宣稱 C 退步"
        )
    print("-" * 72)
    print(f"  完整輸出: {out_dir}")
    print("  REVIEW 不等於失敗。判準是 compression 有沒有造成退步，不是 C 必須比 A 好。")
    if gate["comparison_is_one_sided"]:
        print("  ⚠️ 本輪比較是單向的（基線有缺資料）：抓得到退步，抓不到改善。")
    return gate


def main(argv=None):
    # Windows 主控台預設 cp950，打印中文與符號會 UnicodeEncodeError 或整片亂碼。
    # 落盤本來就是 UTF-8，這裡只處理 stdout/stderr 的呈現。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

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
    ap.add_argument(
        "--rescore", action="store_true",
        help=(
            "離線重判：讀取 --out 裡既有的 30 格輸出，用現行 judge() 重新評分。"
            "不發出任何請求、零費用。改判準之後用它重算，不必重燒 token。"
        ),
    )
    ap.add_argument(
        "--retry-gaps", action="store_true",
        help="只重跑結果為 EMPTY（上游回空字串）的格子，其餘沿用既有輸出",
    )
    args = ap.parse_args(argv)

    out_dir = (REPO_ROOT / args.out).resolve()
    _assert_isolated(out_dir)

    results_path = out_dir / "results.json"

    if args.rescore:
        if not results_path.exists():
            raise SystemExit(
                f"[ABORT] 找不到 {results_path}；--rescore 需要先有跑過的結果。"
            )
        previous = json.loads(results_path.read_text(encoding="utf-8"))
        rescored = []
        for rec in previous:
            verdict, reasons = judge(
                rec["stimulus"], rec["regression"], rec.get("stdout", "")
            )
            rec = dict(rec)
            rec["verdict"] = verdict
            rec["reasons"] = reasons
            rec["rescored"] = True
            rescored.append(rec)
        _report(out_dir, rescored)
        print(f"[RESCORE] 重新評分 {len(rescored)} 格（未發出任何請求）")
        return 0

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

    # --retry-gaps：沿用既有結果，只重跑 verdict 為 EMPTY 的格子
    cached = {}
    if args.retry_gaps and results_path.exists():
        for rec in json.loads(results_path.read_text(encoding="utf-8")):
            cached[rec["tag"]] = rec
        print(f"[RETRY-GAPS] 沿用 {len(cached)} 格既有結果，只補 EMPTY")

    for soul_name, soul_path in souls:
        system = soul_path.read_text(encoding="utf-8")
        for model_name in args.models:
            model_ref = MODELS[model_name]
            for stim in STIMULI:
                tag = f"{soul_name}__{model_name}__{stim['id']}"
                prior = cached.get(tag)
                if prior is not None and prior["verdict"] != "EMPTY":
                    results.append(prior)
                    continue
                print("-" * 72)
                print("RUN", tag, "(retry)" if prior else "")
                stdout, stderr, rc, dt = run_cell(
                    args.python, cli, model_ref, system,
                    stim["turns"], args.timeout,
                    max_tokens=MAX_TOKENS.get(model_name, 2048),
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
                _write_cell(out_dir, record)
                print(f"  rc={rc} {dt:.1f}s verdict={verdict} {reasons}")

    gate = _report(out_dir, results)
    return 0 if gate["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())