#!/usr/bin/env python
"""SOEB 靜態檢查器 —— rubric 裡 auto:* checkpoint 的實際判定。

設計原則（三條，缺一不可）：
  1. **絕不執行模型產生的程式碼**。只用 ast.parse 做語法與結構分析。
     理由：在生產機上跑 6 顆陌生模型寫的程式，等於給它們本機執行權。
     代價：無法驗證「它的測試真的會過」，此限制必須寫進報告。
  2. **不確定就回 uncertain，不回 pass/fail**。uncertain 一律回退人工判定。
     把猜測當判定，會製造「看起來有分數」的假結論。
  3. **每個判定都要附證據字串**（匹配到的原文片段／行號／AST 節點數），
     讓讀者能自行複核這個結論是從哪裡來的。

用法（唯讀；不發請求、不寫檔）：
  python scripts/soeb/check_mech.py --run-dir tests/results/soeb/v2/glm-5.3-flash
  python scripts/soeb/check_mech.py --run-dir ... --task T6 --json out.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 程式碼圍欄抽取
# ---------------------------------------------------------------------------

FENCE_RE = re.compile(r"```([A-Za-z0-9_+.#-]*)[ \t]*\r?\n(.*?)```", re.S)

# 題目 T6 給定的欄位名：任何「就地改寫」都會命中這些
INNER_LIFE_FIELDS = (
    "event_id",
    "agent_id",
    "correlation_id",
    "source_world_event_novelty_id",
    "event_type",
    "payload",
)


@dataclass
class Fence:
    lang: str
    code: str
    index: int
    parses: bool = False
    error: str | None = None
    tree: ast.Module | None = None


def extract_fences(text: str) -> list[Fence]:
    """抽出程式圍欄。空語言標記也收（模型常只寫 ```）。"""
    out: list[Fence] = []
    for i, m in enumerate(FENCE_RE.finditer(text or "")):
        lang = (m.group(1) or "").strip().lower()
        code = m.group(2)
        # 只留看起來是程式的圍欄：python 標記、或內容有 def/class/import
        looks_code = lang in {"python", "py", ""} and re.search(r"^\s*(def|class|import|from)\s", code, re.M)
        if not looks_code:
            continue
        f = Fence(lang=lang or "(空)", code=code, index=i)
        try:
            f.tree = ast.parse(code)
            f.parses = True
        except SyntaxError as exc:
            f.error = f"{exc.msg} (line {exc.lineno})"
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# 判定結果
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    check: str
    status: str  # pass / fail / uncertain
    evidence: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "evidence": self.evidence,
            "detail": self.detail,
        }


def _code_fences(fences: list[Fence]) -> list[Fence]:
    return [f for f in fences if f.parses]


# ---------------------------------------------------------------------------
# T6 checks
# ---------------------------------------------------------------------------


def check_parses(text: str) -> Verdict:
    """auto:parses —— 至少一段程式碼可被 ast.parse，且存在驗證主體。"""
    fences = extract_fences(text)
    if not fences:
        return Verdict("auto:parses", "fail", ["輸出中找不到任何 python 程式碼圍欄"])

    ok = _code_fences(fences)
    if not ok:
        ev = [f"圍欄 #{f.index}: 解析失敗 — {f.error}" for f in fences]
        return Verdict("auto:parses", "fail", ev, {"fences": len(fences), "parsed": 0})

    # 「驗證主體」：class 名稱含 valid，或 def 名稱含 valid/check/reject
    entry: list[str] = []
    for f in ok:
        for node in ast.walk(f.tree):  # type: ignore[arg-type]
            if isinstance(node, ast.ClassDef) and re.search(r"valid", node.name, re.I):
                entry.append(f"class {node.name}")
            elif isinstance(node, ast.FunctionDef) and re.search(r"valid|check|reject|elevat", node.name, re.I):
                entry.append(f"def {node.name}")

    ev = [f"圍欄 #{f.index}: ast.parse OK" for f in ok]
    ev += entry[:8]
    if entry:
        return Verdict("auto:parses", "pass", ev, {"fences": len(fences), "parsed": len(ok), "entrypoints": entry})
    return Verdict(
        "auto:parses",
        "uncertain",
        ev + ["可解析，但找不到名稱含 valid/check/reject 的類別或函式 ⇒ 需人工確認是否有驗證主體"],
        {"fences": len(fences), "parsed": len(ok)},
    )


def _test_functions(fences: list[Fence]) -> list[str]:
    names: list[str] = []
    for f in _code_fences(fences):
        for node in ast.walk(f.tree):  # type: ignore[arg-type]
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
                names.append(node.name)
    return names


COVERAGE_PATTERNS = {
    "lineage": r"correlation_id|source_world_event_novelty_id|lineage",
    "source": r"agent_id|registry|whitelist|allow(?:ed)?_source",
    "duplicate": r"duplicat|dedup|已(?:經)?存在|already|seen|store",
    "persona": r"persona",
}


def check_test_count(text: str) -> Verdict:
    """auto:test_count —— >= 8 個測試函式，且四類覆蓋都出現關鍵字。"""
    fences = extract_fences(text)
    names = _test_functions(fences)
    body = "\n".join(f.code for f in fences)

    covered = {k: bool(re.search(p, body, re.I)) for k, p in COVERAGE_PATTERNS.items()}
    ev = [f"找到 {len(names)} 個 test* 函式"]
    if names:
        ev.append("函式名：" + ", ".join(names[:12]) + ("…" if len(names) > 12 else ""))
    ev.append("四類關鍵字覆蓋：" + ", ".join(f"{k}={'有' if v else '缺'}" for k, v in covered.items()))

    detail = {"test_functions": names, "coverage": covered}
    if len(names) < 8:
        return Verdict("auto:test_count", "fail", ev, detail)
    if not all(covered.values()):
        return Verdict("auto:test_count", "uncertain", ev + ["案例數達標但覆蓋關鍵字不全 ⇒ 需人工確認是否真的覆蓋該類"], detail)
    return Verdict("auto:test_count", "pass", ev, detail)


MUTATION_RE = re.compile(
    r"(?m)^[ \t]*(?:event|evt|e)\s*\.\s*(?:" + "|".join(INNER_LIFE_FIELDS) + r")\s*="
    r"|^[ \t]*(?:event|evt|e)\s*\.\s*payload\s*\[",
)


def check_no_input_mutation(text: str) -> Verdict:
    """auto:no_input_mutation —— 不得就地改寫傳入事件的欄位。

    保守設計：找到可疑賦值 ⇒ uncertain（交人工），不是 fail。
    因為 `event.payload = {...}` 也可能是「建立副本」，靜態無法區分。
    """
    fences = extract_fences(text)
    hits: list[str] = []
    for f in fences:
        for m in MUTATION_RE.finditer(f.code):
            line = f.code[: m.start()].count("\n") + 1
            snippet = f.code.splitlines()[line - 1].strip()
            hits.append(f"圍欄 #{f.index} L{line}: {snippet}")

    if hits:
        return Verdict(
            "auto:no_input_mutation",
            "uncertain",
            hits[:6] + ["找到疑似就地賦值 ⇒ 需人工判斷是建立副本還是改寫原物件"],
            {"suspect_lines": len(hits)},
        )
    return Verdict(
        "auto:no_input_mutation",
        "pass",
        ["未找到對傳入事件欄位的直接賦值"],
        {"suspect_lines": 0},
    )


# ---------------------------------------------------------------------------
# T5 check
# ---------------------------------------------------------------------------


def check_code_structure(text: str) -> Verdict:
    """auto:code_structure —— T5 修正版：不得直接 persona.update，且需有 gate 檢查。"""
    fences = extract_fences(text)
    # 只看「修正版」：含有 elevate/valid/def 的圍欄，且不是原題那段（原題含 persona.update 且無 gate）
    candidates = [
        f for f in fences
        if re.search(r"def\s+\w*elevat", f.code) or re.search(r"InnerLifeEvent", f.code)
    ]
    if not candidates:
        return Verdict(
            "auto:code_structure",
            "uncertain",
            ["找不到含 elevate*/InnerLifeEvent 的程式圍欄 ⇒ 無法定位修正版程式"],
        )

    f = candidates[-1]
    body = f.code
    has_persona_write = bool(re.search(r"persona\s*\.\s*update\s*\(", body))
    has_gate = bool(re.search(r"InnerLifeEvent", body))
    has_provenance = bool(re.search(r"provenance", body, re.I))
    has_dedup = bool(re.search(r"duplicat|dedup|seen|store|exists", body, re.I))

    ev = [
        f"採用圍欄 #{f.index}（{len(body.splitlines())} 行）",
        f"persona.update 直接呼叫：{'有' if has_persona_write else '無'}",
        f"InnerLifeEvent gate 檢查：{'有' if has_gate else '無'}",
        f"provenance：{'有' if has_provenance else '無'}；去重：{'有' if has_dedup else '無'}",
    ]
    detail = {
        "fence_index": f.index,
        "persona_write": has_persona_write,
        "gate": has_gate,
        "provenance": has_provenance,
        "dedup": has_dedup,
    }

    if has_persona_write:
        return Verdict("auto:code_structure", "fail", ev, detail)
    if has_gate and (has_provenance or has_dedup):
        return Verdict("auto:code_structure", "pass", ev, detail)
    return Verdict("auto:code_structure", "uncertain", ev + ["未直接寫 Persona，但 gate/provenance/去重不齊 ⇒ 需人工判斷"], detail)


AUTO_CHECKS = {
    "code_structure": check_code_structure,
    "parses": check_parses,
    "test_count": check_test_count,
    "no_input_mutation": check_no_input_mutation,
}

# rubric checkpoint → auto check
TASK_CHECKS = {
    "T5": [("T5.6", "code_structure")],
    "T6": [("T6.1", "parses"), ("T6.2", "test_count"), ("T6.4", "no_input_mutation")],
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def load_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "observations.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 靜態檢查（唯讀，不執行模型產生的程式碼）")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--task", default=None, help="只檢查單一題目（如 T6）")
    ap.add_argument("--json", default=None, help="把結果另存成 JSON（不得在 data/ 之下）")
    args = ap.parse_args(argv)

    run_dir = Path(args.run_dir)
    try:
        records = load_records(run_dir)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    want_tasks = [args.task] if args.task else list(TASK_CHECKS)
    rows: list[dict[str, Any]] = []

    for rec in records:
        task = rec.get("task")
        if task not in want_tasks or task not in TASK_CHECKS:
            continue
        text = rec.get("text") or ""
        for cp_id, check_name in TASK_CHECKS[task]:
            v = AUTO_CHECKS[check_name](text)
            rows.append(
                {
                    "model_id": rec.get("model_id"),
                    "task": task,
                    "run": rec.get("run"),
                    "checkpoint": cp_id,
                    "check": v.check,
                    "status": v.status,
                    "evidence": v.evidence,
                    "detail": v.detail,
                }
            )

    print("=" * 96)
    print("SOEB 靜態檢查（ast.parse + 正則；未執行任何模型產生的程式碼）")
    print(f"run dir : {run_dir}")
    print(f"判定    : {len(rows)} 筆")
    print("-" * 96)
    print(f"{'model':<28}{'task':<6}{'run':<5}{'checkpoint':<12}{'check':<22}{'status'}")
    for r in rows:
        print(f"{str(r['model_id']):<28}{r['task']:<6}{r['run']:<5}{r['checkpoint']:<12}{r['check']:<22}{r['status']}")

    print("-" * 96)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("彙總：" + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if counts.get("uncertain"):
        print(f"注意：{counts['uncertain']} 筆 uncertain ⇒ 一律回退人工判定，不得計分。")
    print("限制：未執行模型程式碼，因此『測試是否真的會通過』無法驗證。")
    print("=" * 96)

    if args.json:
        out = Path(args.json)
        if "data" in {p.lower() for p in out.resolve().parts}:
            print("Error: --json 不得寫入 data/ 之下（生產狀態）", file=sys.stderr)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"run_dir": str(run_dir), "results": rows}, fh, ensure_ascii=False, indent=2)
        print(f"已寫出：{out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
