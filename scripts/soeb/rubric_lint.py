#!/usr/bin/env python
"""SOEB rubric lint —— 把「評分表本身對不對」變成可執行守衛。

用途（唯讀，不發請求、不寫任何檔案）：
  1. rubric.json 能解析，且每題的 checkpoint 配分**加總恆等於**該題配分。
  2. 全域可用總分 == tasks 配分合計（且 == total_points_available）。
  3. rubric 綁定的 spec / system_prompt sha256 與**磁碟上的實際檔案**一致
     —— 不一致代表「評分表在評一份已經不存在的題目」，必須紅燈。
  4. rubric 的題目集合與 spec 的題目集合一致（含 blocked_tasks 不得被評分）。
  5. 每個 checkpoint 的 check 欄位只能是 manual 或已登記的 auto:* 之一。
  6. checkpoint id 必須唯一。

退出碼：0 = 全綠；1 = 有問題（逐條列出）；2 = 檔案缺失。

為什麼要有這支：
  評分表是「把輸出變成分數」的唯一通道。它的算術或綁定若漂移，
  分數會看起來很合理，卻對不上題目 —— 這種錯最難被肉眼發現。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_RUBRIC = HERE / "rubric.json"
DEFAULT_SPEC = HERE / "tasks" / "soeb-v1.json"

# 已實作的靜態檢查器名稱。新增 auto:* 必須同時在這裡登記，
# 否則 lint 會把它當成打錯字（避免「寫了一個不存在的自動判定」）。
KNOWN_AUTO_CHECKS = {
    "code_structure",
    "parses",
    "test_count",
    "no_input_mutation",
}


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


class Unreadable(Exception):
    """檔案缺失／JSON 解析失敗。

    與「lint 抓到問題」**刻意分開**：前者代表守衛根本沒跑到判定邏輯，
    後者代表判定邏輯說「不對」。兩者若共用退出碼 1，
    就會出現「看到紅燈就以為守衛有效」的假保證（本專案 2026-10-31 的教訓）。
    """


def _load_json(path: Path) -> dict[str, Any]:
    # utf-8-sig：本機工具（PowerShell Set-Content -Encoding UTF8）會寫 BOM。
    # 不容忍 BOM 會讓「檔案讀不進來」偽裝成「守衛抓到問題」。
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{path}: {exc}") from exc


def lint(rubric_path: Path, spec_path: Path) -> list[str]:
    """回傳問題清單；空清單代表全綠。

    檔案讀不進來時丟 Unreadable（→ 退出碼 2），不混進問題清單。
    """
    problems: list[str] = []

    if not rubric_path.exists():
        raise Unreadable(f"找不到 rubric：{rubric_path}")
    if not spec_path.exists():
        raise Unreadable(f"找不到 spec：{spec_path}")

    rubric = _load_json(rubric_path)
    spec = _load_json(spec_path)

    # ---- 2. 綁定驗證：rubric 評的是不是磁碟上這一份題目 ----
    actual_spec_sha = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    bound_spec_sha = rubric.get("bound_spec_sha256")
    if bound_spec_sha != actual_spec_sha:
        problems.append(
            "spec sha256 不符：rubric 綁 "
            f"{str(bound_spec_sha)[:12]}… / 磁碟實際 {actual_spec_sha[:12]}…"
            " ⇒ 評分表在評一份已經不存在的題目"
        )

    sp_path = spec.get("system_prompt_path")
    if sp_path:
        cand = spec_path.parent / sp_path
        if cand.exists():
            actual_sp_sha = hashlib.sha256(cand.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            bound_sp_sha = rubric.get("bound_system_prompt_sha256")
            if bound_sp_sha != actual_sp_sha:
                problems.append(
                    "system prompt sha256 不符：rubric 綁 "
                    f"{str(bound_sp_sha)[:12]}… / 實際 {actual_sp_sha[:12]}…"
                )
        else:
            problems.append(f"spec 指名的 system prompt 不存在：{cand}")

    # ---- 3. 題目集合對齊 ----
    spec_tasks = [t["id"] for t in spec.get("tasks", [])]
    blocked_tasks = [t["id"] for t in spec.get("blocked_tasks", [])]
    rub_tasks = list((rubric.get("tasks") or {}).keys())

    missing = [t for t in spec_tasks if t not in rub_tasks]
    extra = [t for t in rub_tasks if t not in spec_tasks]
    if missing:
        problems.append(f"spec 有題但 rubric 沒評分規則：{missing}")
    if extra:
        problems.append(f"rubric 有評分規則但 spec 沒有這題：{extra}")
    overlap = [t for t in rub_tasks if t in blocked_tasks]
    if overlap:
        problems.append(f"blocked_tasks 不得被評分，卻出現在 rubric：{overlap}")

    # ---- 4. 逐題配分算術 ----
    seen_ids: set[str] = set()
    rub_total = 0.0
    for tid, task in (rubric.get("tasks") or {}).items():
        declared = task.get("points")
        items = task.get("items") or []
        if not items:
            problems.append(f"{tid}：沒有任何 checkpoint")
            continue
        item_sum = 0.0
        for it in items:
            iid = it.get("id")
            if not iid:
                problems.append(f"{tid}：有 checkpoint 缺 id")
                continue
            if iid in seen_ids:
                problems.append(f"checkpoint id 重複：{iid}")
            seen_ids.add(iid)

            pts = it.get("points")
            if not isinstance(pts, (int, float)) or pts <= 0:
                problems.append(f"{iid}：points 必須是正數，實際 {pts!r}")
            else:
                item_sum += pts

            chk = str(it.get("check", ""))
            if chk == "manual":
                pass
            elif chk.startswith("auto:"):
                name = chk[len("auto:"):]
                if name not in KNOWN_AUTO_CHECKS:
                    problems.append(f"{iid}：未登記的自動檢查 auto:{name}")
            else:
                problems.append(f"{iid}：check 必須是 manual 或 auto:*，實際 {chk!r}")

            if not str(it.get("text", "")).strip():
                problems.append(f"{iid}：缺判定敘述 text")

        if isinstance(declared, (int, float)) and abs(item_sum - declared) > 1e-9:
            problems.append(
                f"{tid}：checkpoint 配分合計 {item_sum} != 題目配分 {declared}"
            )
        if isinstance(declared, (int, float)):
            rub_total += declared

    # ---- 5. 總分對帳 ----
    rules = rubric.get("scoring_rules") or {}
    avail = rules.get("total_points_available")
    design = rules.get("total_points_design")
    unavailable = rules.get("unavailable") or {}
    unavailable_pts = sum(int(v.get("points", 0)) for v in unavailable.values())

    if isinstance(avail, (int, float)) and abs(rub_total - avail) > 1e-9:
        problems.append(
            f"rubric 各題配分合計 {rub_total} != total_points_available {avail}"
        )
    if isinstance(design, (int, float)) and isinstance(avail, (int, float)):
        if abs((avail + unavailable_pts) - design) > 1e-9:
            problems.append(
                f"available({avail}) + unavailable({unavailable_pts}) != design({design})"
            )

    # spec 的 blocked_tasks 配分必須剛好等於 unavailable 的配分
    spec_blocked_pts = sum(int(t.get("points", 0)) for t in spec.get("blocked_tasks", []))
    if spec_blocked_pts != unavailable_pts:
        problems.append(
            f"spec blocked_tasks 配分 {spec_blocked_pts} != rubric unavailable {unavailable_pts}"
        )

    # ---- 6. runs 與 stability 對齊 ----
    stab = (rules.get("stability") or {}).get("applies_to") or []
    for t in spec.get("tasks", []):
        runs = int(t.get("runs", 1))
        if runs > 1 and t["id"] not in stab:
            problems.append(f"{t['id']}：spec 有 runs={runs} 但 rubric 未定義 stability 判定")
        if runs == 1 and t["id"] in stab:
            problems.append(f"{t['id']}：spec 只有 runs=1，rubric 卻要求 stability")

    return problems


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB rubric lint（唯讀）")
    ap.add_argument("--rubric", default=str(DEFAULT_RUBRIC))
    ap.add_argument("--spec", default=str(DEFAULT_SPEC))
    args = ap.parse_args(argv)

    rubric_path = Path(args.rubric)
    spec_path = Path(args.spec)

    try:
        problems = lint(rubric_path, spec_path)
        rubric = _load_json(rubric_path)
    except Unreadable as exc:
        print(f"UNREADABLE：守衛沒有跑到判定邏輯 —— {exc}", file=sys.stderr)
        print("  （退出碼 2，刻意與『抓到問題』的 1 分開）", file=sys.stderr)
        return 2
    n_items = sum(len(t.get("items") or []) for t in (rubric.get("tasks") or {}).values())
    n_auto = sum(
        1
        for t in (rubric.get("tasks") or {}).values()
        for it in (t.get("items") or [])
        if str(it.get("check", "")).startswith("auto:")
    )

    print("=" * 72)
    print("SOEB rubric lint")
    print(f"rubric : {rubric_path}")
    print(f"spec   : {spec_path}")
    print(f"題數    : {len(rubric.get('tasks') or {})}    checkpoints: {n_items}（其中靜態可判 {n_auto}）")
    print("-" * 72)

    if not problems:
        print("PASS：配分加總、spec 綁定、題目對齊、check 名稱全部一致。")
        print("=" * 72)
        return 0

    print(f"FAIL：{len(problems)} 個問題")
    for p in problems:
        print(f"  - {p}")
    print("=" * 72)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
