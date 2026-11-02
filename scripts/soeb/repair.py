"""SOEB 單筆修復：把一次**失敗**的呼叫，換成同規格重跑得到的成功結果。

為什麼需要它
------------
`runner.py` 以 `"w"` 開 `observations.jsonl`，所以「只補跑一題」不可能直接寫回
既有 run dir —— 整檔會被截斷。實務上只能：
  (a) 整顆模型 10 次呼叫全部重跑（貴，而且其餘 9 筆本來就好的會白白重買），或
  (b) 用 `--only T6` 跑一個探針目錄，再把那一筆**換進**正式目錄。

沒有工具時 (b) 只能手動改 JSONL —— 那等於「靜默改結果」，是最糟的做法。
本工具把 (b) 變成**只能做對的事**的操作。

結構性限制（刻意不提供的旗標）
------------------------------
* **不能覆蓋成功的紀錄**：目標那一筆必須是 `ok == false`。想「用更好的答案換掉
  本來就成功的答案」＝重跑整個 run，不是修復。沒有 `--force` 之類的逃生口。
* **不能跨預算替換**：`budget_override` 必須完全相同。這一條是方法論護欄——
  用 65536 的結果去補 32768 的洞，會讓該模型的 T6 與其他模型**不可比**，
  而報表上看不出來。真要換預算，就得整輪六顆一起換。
* **不能跨規格替換**：`suite` / `spec_sha256` / `system_prompt_sha256` / `tasks`
  任一不同就拒絕。
* **不能寫到別的地方**：只有 `--run-dir` 一個寫入目標。
* **不留備份明文**：原子寫入，不建 `.bak`。

用法
----
    .venv\\Scripts\\python.exe scripts\\soeb\\repair.py \\
        --run-dir tests\\results\\soeb\\v2\\mimo-v2.6-flash \\
        --replacement tests\\results\\soeb\\_probe\\mimo_t6_retry \\
        --model mimo-v2.6-flash --task T6 --run-index 1

退出碼：0 = 修復完成；1 = 拒絕（訊息說明違反哪一條）；2 = 參數／檔案錯誤。
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

#: 這些欄位一旦不同，代表兩份觀測不可比 ⇒ 拒絕替換（全域層）。
#: 當題層（`budget_override` / `tasks`）由 `_check_invariants()` 逐題比對，
#: 因為 `--only` 探針的 manifest 只會記錄被跑的那一題。
GLOBAL_INVARIANT_KEYS = (
    "suite",
    "spec_sha256",
    "system_prompt_sha256",
)

#: 修復記錄檔（append-only，供事後稽核「哪一筆被誰換掉」）。
REPAIR_LOG = "repairs.jsonl"


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


def _select(obs: list[dict], model: str, task: str, run_index: int, *, where: str) -> int:
    """回傳唯一符合的索引；0 筆或多筆都拒絕（多筆＝來源本身有問題，不猜）。"""
    hits = [
        i
        for i, o in enumerate(obs)
        if str(o.get("model_id")) == model
        and str(o.get("task")) == task
        and int(o.get("run") or 1) == int(run_index)
    ]
    if not hits:
        raise LookupError(f"{where} 找不到 {model} / {task} / r{run_index}")
    if len(hits) > 1:
        raise LookupError(
            f"{where} 有 {len(hits)} 筆 {model} / {task} / r{run_index}（預期 1 筆）"
        )
    return hits[0]


def _task_entry(tasks: object, task_id: str) -> dict | None:
    for entry in tasks or []:
        if isinstance(entry, dict) and str(entry.get("id")) == task_id:
            return entry
    return None


def _check_invariants(base: dict, other: dict, task_id: str) -> None:
    """逐欄位比對兩份 manifest，任何不一致就拒絕替換。

    判準刻意分成兩層，因為 `runner.py --only` 產生的探針 manifest **只會記錄
    被跑的那一題**：

    * **全域層**（`suite` / `spec_sha256` / `system_prompt_sha256`）：整包必須相同。
    * **當題層**（`budget_override.forced_max_tokens`、`budget_override.original[task]`、
      該題的 `tasks` 條目）：只比「被修復的那一題」。

    為什麼不整包比 `budget_override` / `tasks`：`--only T6` 的 `original` 只有
    `{"T6": 6144}`，而正式目錄有七題 ⇒ 整包比對**恆不相等**，工具會被自己的
    過度嚴格廢掉，逼人回去手改 JSONL —— 那比沒有工具更糟。
    但「該題的預算」與「該題的題目設定」不同，就真的是不可比，必須拒絕。
    """
    for key in GLOBAL_INVARIANT_KEYS:
        if base.get(key) != other.get(key):
            raise ValueError(
                f"不可替換：`{key}` 兩邊不同 ⇒ 結果不可比。\n"
                f"  目標 = {json.dumps(base.get(key), ensure_ascii=False)[:300]}\n"
                f"  替換 = {json.dumps(other.get(key), ensure_ascii=False)[:300]}\n"
                "跨規格的替換會讓報表看起來正常、實際不可比。"
            )

    b_budget = base.get("budget_override") or {}
    o_budget = other.get("budget_override") or {}
    if b_budget.get("forced_max_tokens") != o_budget.get("forced_max_tokens"):
        raise ValueError(
            "不可替換：`budget_override.forced_max_tokens` 不同 ⇒ 該題預算不同。\n"
            f"  目標 = {b_budget.get('forced_max_tokens')!r}\n"
            f"  替換 = {o_budget.get('forced_max_tokens')!r}\n"
            "用更高預算的答案補低預算的洞，會讓該題與其他模型不可比。"
        )

    b_orig = (b_budget.get("original") or {}).get(task_id, "<missing>")
    o_orig = (o_budget.get("original") or {}).get(task_id, "<missing>")
    if b_orig != o_orig:
        raise ValueError(
            f"不可替換：`budget_override.original[{task_id}]` 不同 ⇒ 該題原始預算不同。\n"
            f"  目標 = {b_orig!r}\n"
            f"  替換 = {o_orig!r}"
        )

    b_task = _task_entry(base.get("tasks"), task_id)
    o_task = _task_entry(other.get("tasks"), task_id)
    if b_task is None or o_task is None:
        raise ValueError(
            f"不可替換：{task_id} 的題目設定在其中一邊不存在 ⇒ 無法確認同題同規格。\n"
            f"  目標 = {json.dumps(b_task, ensure_ascii=False)}\n"
            f"  替換 = {json.dumps(o_task, ensure_ascii=False)}"
        )
    if b_task != o_task:
        raise ValueError(
            f"不可替換：{task_id} 的題目設定不同（max_tokens／temperature／effort／note）。\n"
            f"  目標 = {json.dumps(b_task, ensure_ascii=False)[:300]}\n"
            f"  替換 = {json.dumps(o_task, ensure_ascii=False)[:300]}"
        )


def _text_of(rec: dict) -> str:
    return str(rec.get("text") or "")


def _repair_blockers(old: dict, new: dict) -> str | None:
    """回傳「不准修復」的理由；None 代表可修復。

    刻意寫成獨立函式，讓守衛測試能用 monkeypatch **把這道閘門整個拿掉**，
    證明「覆蓋成功結果」與「換成空白結果」真的會被擋 —— 否則那些斷言只是裝飾。
    """
    if old.get("ok"):
        return (
            f"目標那一筆本來就成功（ok=true, finish={old.get('finish_reason')!r}）。\n"
            "  覆蓋成功結果＝重跑整個 run，不是修復。本工具不提供此能力。"
        )
    if not new.get("ok"):
        return f"替換來源那一筆仍是失敗（ok=false, error={new.get('error')!r}）。"
    if not _text_of(new).strip():
        return "替換來源的正文為空 ⇒ 換進去也無法評分。"
    return None


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="SOEB 單筆修復（只修失敗的呼叫）")
    ap.add_argument("--run-dir", required=True, help="要修復的 run dir（唯一寫入目標）")
    ap.add_argument("--replacement", required=True, help="重跑探針 run dir（唯讀來源）")
    ap.add_argument("--model", required=True, help="模型 id")
    ap.add_argument("--task", required=True, help="題目 id，例如 T6")
    ap.add_argument("--run-index", type=int, default=1, help="run 序號（預設 1）")
    args = ap.parse_args(argv)

    target = Path(args.run_dir).resolve()
    source = Path(args.replacement).resolve()
    if target == source:
        print("Error: --replacement 不可等於 --run-dir", file=sys.stderr)
        return 2
    if not target.is_dir():
        print(f"Error: --run-dir 不存在 {target}", file=sys.stderr)
        return 2
    if not source.is_dir():
        print(f"Error: --replacement 不存在 {source}", file=sys.stderr)
        return 2

    try:
        t_manifest, t_obs = _load_run(target)
        s_manifest, s_obs = _load_run(source)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.model not in (t_manifest.get("models") or []):
        print(
            f"Error: {args.model} 不在目標 manifest 的 models {t_manifest.get('models')}",
            file=sys.stderr,
        )
        return 2

    try:
        _check_invariants(t_manifest, s_manifest, args.task)
    except ValueError as exc:
        print(f"拒絶修復：{exc}", file=sys.stderr)
        return 1

    try:
        ti = _select(t_obs, args.model, args.task, args.run_index, where="目標")
        si = _select(s_obs, args.model, args.task, args.run_index, where="替換來源")
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    old = t_obs[ti]
    new = s_obs[si]

    # 唯一定義：修復＝把「失敗」換成「成功」。反過來或平換都不是修復。
    blocked = _repair_blockers(old, new)
    if blocked is not None:
        print(f"拒絕修復：{blocked}", file=sys.stderr)
        return 1

    # 保留可追溯性：被替換的事實寫進紀錄本身，不只寫進旁檔。
    patched = dict(new)
    patched["repaired_from"] = {
        "source_run_dir": str(source),
        "replaced_error": str(old.get("error") or ""),
        "replaced_status_code": old.get("status_code"),
        "replaced_latency_ms": old.get("latency_ms"),
        "replaced_finish_reason": old.get("finish_reason"),
        "replaced_text_chars": len(_text_of(old)),
    }

    new_obs = list(t_obs)
    new_obs[ti] = patched

    ok_count = sum(1 for o in new_obs if o.get("ok"))
    out_lines = [json.dumps(o, ensure_ascii=False) for o in new_obs]
    payload = "\n".join(out_lines) + "\n"

    # 原子寫入：同目錄 temp + os.replace（不建 .bak，不複製舊內容）
    tmp = target / "observations.jsonl.repairtmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target / "observations.jsonl")

    rec = {
        "repaired_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "model": args.model,
        "task": args.task,
        "run": args.run_index,
        "source_run_dir": str(source),
        "replaced": {
            "ok": old.get("ok"),
            "status_code": old.get("status_code"),
            "error": old.get("error"),
            "latency_ms": old.get("latency_ms"),
            "text_chars": len(_text_of(old)),
        },
        "replacement": {
            "ok": new.get("ok"),
            "status_code": new.get("status_code"),
            "finish_reason": new.get("finish_reason"),
            "latency_ms": new.get("latency_ms"),
            "text_chars": len(_text_of(new)),
        },
    }
    with open(target / REPAIR_LOG, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print("修復完成")
    print(f"  目標    : {target}")
    print(f"  替換來源: {source}")
    print(f"  項目    : {args.model} / {args.task} / r{args.run_index}")
    print(f"  被替換  : ok={old.get('ok')} status={old.get('status_code')} "
          f"error={old.get('error')!r} latency={old.get('latency_ms')}ms")
    print(f"  換成    : ok={new.get('ok')} finish={new.get('finish_reason')!r} "
          f"latency={new.get('latency_ms')}ms text={len(_text_of(new))} 字元")
    print(f"  可用數  : {ok_count}/{len(new_obs)}")
    print(f"  稽核    : {target / REPAIR_LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
