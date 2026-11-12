# -*- coding: utf-8 -*-
"""soeb.runner — SOEB 執行器 CLI

設計原則（每一條都對應一次踩過的坑）
====================================
1. **預設不呼叫任何東西。** 沒給 ``--run`` 時只印出矩陣與成本估算然後結束。
   呼叫真實 LLM 會產生費用，這條線必須是顯式的。
2. **輸出路徑由呼叫端指定，且結構上不可能寫生產。** ``--out`` 是必要參數，
   解析後的絕對路徑若落在本 repo 的 ``data/`` 之下就直接拒絕。
   基準由 ``__file__`` 推導（不是環境變數、不是行程快取），所以**不會漂移**
   —— 這是 2026-10-19 就地寫入事故後立的規矩（AGENTS.md 鐵律 12）。
3. **每一格都留證據。** 請求、回應、usage、latency、finish_reason 一併落盤；
   空正文一律附上歸因（見 ``client.explain_empty``），避免把「預算被 thinking
   吃光」誤讀成「模型不懂」。
4. **不殺行程、不啟伺服器、不綁埠。** 本模組只有 HTTP 出去與檔案落盤。

用法
====
::

    # 只看矩陣與成本（安全預設，不發請求）
    .venv\\Scripts\\python.exe scripts/soeb/runner.py \\
        --tasks scripts/soeb/tasks/smoke.json --out tests/results/soeb/smoke

    # 真的跑（會產生 LLM 費用）
    .venv\\Scripts\\python.exe scripts/soeb/runner.py \\
        --tasks scripts/soeb/tasks/smoke.json --out tests/results/soeb/smoke --run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from soeb import client as soeb_client  # noqa: E402
from harness import budget as harness_budget  # noqa: E402  # 调用方集中限额（只活在 harness）


def _force_utf8_stdio() -> None:
    """Windows 主控台預設是 cp950，遇到 ``⇒``／``—`` 會直接 UnicodeEncodeError。

    2026-10-07 的 REM 迴歸在同一件事上炸過一次（llm_call.py 以 ANSI 寫 stdout，
    呼叫端 decode("utf-8") 把每個中文字變成 U+FFFD，中文 regex 全失效）。
    這裡在行程最早期就把兩條串流釘成 UTF-8；``errors="replace"`` 保證就算
    呼叫端是 cp950 主控台也只會顯示問號，不會讓整個 benchmark 跑不掉。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

HERE = Path(__file__).resolve().parent
SOEB_DIR = HERE
#: 由檔案位置推導，不依賴任何環境變數或行程快取 ⇒ 不會漂移。
REPO_ROOT = HERE.parent.parent
PRODUCTION_DATA_DIR = (REPO_ROOT / "data").resolve()
MODELS_JSON = SOEB_DIR / "models.json"


# ─────────────────────────────────────────────────────────────────────────────
# Task spec
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Task:
    id: str
    messages: list[dict[str, str]]
    max_tokens: int = 4096
    temperature: float | None = 0.0
    effort: str | None = None
    note: str = ""
    #: 同一題重複執行的次數（SOEB v1.0 第六節：T2/T4/T7 各兩次，用來測穩定性）。
    #: 每次都是獨立呼叫、獨立觀測列，不共用對話歷史。
    runs: int = 1

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, default_max_tokens: int) -> "Task":
        if "id" not in raw or "messages" not in raw:
            raise ValueError(f"task 缺少 id 或 messages：{raw!r}")
        if not raw["messages"]:
            raise ValueError(f"task {raw['id']!r} 的 messages 是空的")
        for msg in raw["messages"]:
            if "role" not in msg or "content" not in msg:
                raise ValueError(f"task {raw['id']!r} 有格式錯誤的 message：{msg!r}")
        runs = int(raw.get("runs", 1))
        if runs < 1:
            raise ValueError(f"task {raw['id']!r} 的 runs 必須 >= 1，拿到 {runs}")
        return cls(
            id=raw["id"],
            messages=raw["messages"],
            max_tokens=raw.get("max_tokens", default_max_tokens),
            temperature=raw.get("temperature", 0.0),
            effort=raw.get("effort"),
            note=raw.get("note", ""),
            runs=runs,
        )


def load_spec(path: Path, *, default_max_tokens: int) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到 task spec：{path}")
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    if "tasks" not in spec or not spec["tasks"]:
        raise ValueError(f"{path} 內沒有 tasks")
    spec["_tasks"] = [
        Task.from_dict(t, default_max_tokens=default_max_tokens) for t in spec["tasks"]
    ]
    spec["_path"] = str(path)
    spec["_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    # 統一 System Prompt（SOEB v1.0 第六節第 1 步）：六家收到的 bytes 必須完全相同。
    # 兩種寫法任選：spec 內直接給字串，或給一個相對於 spec 檔的 .md/.txt 路徑
    # （題目集本身很長，把 prompt 放在獨立檔案比較好審）。
    prompt = spec.get("system_prompt") or ""
    prompt_path = spec.get("system_prompt_path")
    if prompt and prompt_path:
        raise ValueError(f"{path} 同時給了 system_prompt 與 system_prompt_path，只能擇一")
    if prompt_path:
        p = (path.parent / prompt_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"{path} 指定的 system_prompt_path 不存在：{p}")
        prompt = p.read_text(encoding="utf-8")
        spec["_system_prompt_source"] = str(p)
    spec["_system_prompt"] = prompt
    spec["_system_prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return spec


def load_registry(path: Path = MODELS_JSON) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ─────────────────────────────────────────────────────────────────────────────
# 輸出路徑安全
# ─────────────────────────────────────────────────────────────────────────────


def assert_safe_out_dir(out_dir: Path) -> Path:
    """拒絕任何落在本 repo ``data/`` 之下的輸出路徑。

    這裡比較的兩個值**都是結構性的**（由 ``__file__`` 推導的常數，與呼叫端給的
    絕對路徑），不涉及環境變數或行程快取，因此不會出現「兩邊各自漂移」的失效。
    """
    resolved = out_dir.resolve()
    if resolved == PRODUCTION_DATA_DIR or PRODUCTION_DATA_DIR in resolved.parents:
        raise SystemExit(
            f"拒絕寫入生產資料目錄：{resolved}\n"
            f"data/ 是生產狀態（AGENTS.md 鐵律 8）。請指定 repo 外的路徑，"
            f"或使用 tests/results/ 之類的位置。"
        )
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# 成本估算
# ─────────────────────────────────────────────────────────────────────────────


def estimate_cost(
    registry: dict[str, Any],
    tasks: list[Task],
    model_ids: list[str],
    *,
    system_prompt: str = "",
) -> list[dict[str, Any]]:
    """用「輸入 ≈ 提示字元數 / 3.5、輸出 ≈ max_tokens」粗估上限。

    這是**上限**估算，不是帳單：實際 reasoning token 可能用不到 max_tokens。
    目的只是讓 Owner 在看到「幾十次呼叫」時知道量級。
    重複執行（``runs``）與統一 System Prompt 都必須計入，否則會少估。
    """
    by_id = {m["id"]: m for m in registry["models"]}
    total_chars = sum(len(x["content"]) for t in tasks for x in t.messages)
    total_chars += len(system_prompt) * len(tasks)
    rows = []
    for mid in model_ids:
        m = by_id.get(mid)
        if not m:
            continue
        in_tok = sum((total_chars / len(tasks) / 3.5) * t.runs for t in tasks)
        out_tok = sum(t.max_tokens * t.runs for t in tasks)
        # 價格未知就顯示 unknown，**不要用 0 代填**：0 會讓「不知道」看起來像「免費」，
        # 而這正是 Owner 要拿去做成本效率排名的欄位。
        price_known = m.get("price_in") is not None and m.get("price_out") is not None
        usd = None
        if price_known:
            usd = (in_tok / 1e6) * m["price_in"] + (out_tok / 1e6) * m["price_out"]
        rows.append(
            {
                "model": mid,
                "status": m.get("status", "ready"),
                "protocol": m["protocol"],
                "endpoint": m.get("endpoint") or m["protocol"],
                "calls": sum(t.runs for t in tasks),
                "est_in_tokens": int(in_tok),
                "est_out_token_ceiling": int(out_tok),
                "price_known": price_known,
                "est_usd_ceiling": round(usd, 6) if usd is not None else None,
            }
        )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────


def run_matrix(
    endpoints: dict[str, soeb_client.Endpoint],
    registry: dict[str, Any],
    tasks: list[Task],
    model_ids: list[str],
    *,
    system_prompt: str,
    save_raw: bool,
    timeout: float,
    sink: Callable[[dict[str, Any]], None] | None = None,
    budget_doc: dict[str, Any] | None = None,
    budget_ledger: Any | None = None,
    presend_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """逐模型 × 逐題 × 逐次執行。

    `sink` 會在**每一筆觀測產生時**被呼叫。呼叫端用它做增量落盤：
    推理模型一題可能要跑幾十分鐘，若只在全部跑完才寫檔，
    任何一次中斷（harness 逾時、斷線、例外）都會讓**已完成的所有觀測一起消失**。

    限額（SA-HARNESS-BUDGET-1）：傳入 budget_doc + budget_ledger 時，
    每一發在 `soeb_client.call` 之前先持久化預留（reserve 落盤後才發送），
    發後按協議結算（含 reasoning）；缺 usage / 超時 / 存證失敗 ⇒ 保留預留、
    標 UNKNOWN 並停止後續發送（halt）。本票禁自動重試加額。
    不傳 budget 參數時行為與舊版逐位元相同。
    """
    by_id = {m["id"]: m for m in registry["models"]}
    observations: list[dict[str, Any]] = []

    for mid in model_ids:
        if budget_ledger is not None and budget_ledger.halted:
            print("[BUDGET-HALT] 账本已 HALT，停止后续发送")
            break
        meta = by_id[mid]
        protocol = meta["protocol"]
        # 端點鍵 != 協定：`ollama-chat` 與 `chat-completions` 是同一種協定、不同通道。
        # 分開命名是為了讓「哪一顆走哪條路」留在資料裡，事後可從觀測列還原。
        endpoint_key = meta.get("endpoint") or protocol
        endpoint = endpoints.get(endpoint_key)
        if endpoint is None:
            for task in tasks:
                for run_idx in range(1, task.runs + 1):
                    observations.append(
                        {
                            # 欄位名必須與 CallResult.to_dict() 一致（model_id / task），
                            # 否則下游評分腳本會在「有 skipped 筆」時才 KeyError。
                            "model_id": mid,
                            "task": task.id,
                            "run": run_idx,
                            "ok": False,
                            "skipped": True,
                            "error": f"config.yaml 內沒有 {endpoint_key} 端點",
                        }
                    )
            continue

        # registry 的 id 是「參賽者」，不一定是上游 API 的模型名（Ollama 帶版本戳）。
        api_model = meta.get("api_model") or mid

        for task in tasks:
            if budget_ledger is not None and budget_ledger.halted:
                print("[BUDGET-HALT] 账本已 HALT，停止后续发送")
                break
            # 統一 System Prompt：六家收到的 bytes 必須完全相同，所以由 spec 提供、
            # 在這裡前置，而不是各題自己寫進 messages（那會讓題目間不一致）。
            messages = list(task.messages)
            if system_prompt:
                messages = [{"role": "system", "content": system_prompt}, *messages]

            for run_idx in range(1, task.runs + 1):
                effort = task.effort
                if effort and effort not in (meta.get("effort_supported") or []):
                    effort = None  # 不支援就別送，避免 400 混進結果
                # ── 限额预留（先落盘后发送；禁绕过）──
                _rid: str | None = None
                _caps: dict[str, Any] | None = None
                if budget_doc is not None and budget_ledger is not None:
                    _key = harness_budget.ProfileKey(
                        channel=endpoint_key,
                        api_model=api_model,
                        task_sha256=harness_budget.canonical_task_sha256(messages),
                        effort=effort,
                    )
                    _, _caps = harness_budget.resolve_profile(budget_doc, _key)
                    budget_ledger.check_fits(
                        single_shot_max_completion=int(_caps["single_shot_max_completion"]),
                        total_max_completion=int(_caps["total_max_completion"]),
                        max_shots=int(_caps["max_shots"]),
                        planned_max_tokens=int(task.max_tokens),
                        planned_new_shots=1,
                    )
                    _rid = budget_ledger.reserve(
                        shot_id=f"{mid}/{task.id}/r{run_idx}",
                        pre_tokens=int(task.max_tokens),
                    )
                    if presend_dir is not None:
                        _url, _headers, _body = soeb_client.build_request(
                            endpoint.protocol,
                            endpoint,
                            api_model,
                            messages,
                            max_tokens=task.max_tokens,
                            temperature=task.temperature,
                            effort=effort,
                        )
                        _redacted = dict(_headers)
                        try:
                            _redacted = soeb_client.redact_headers(_headers)
                            harness_budget.validate_redacted_headers(_redacted)
                        except harness_budget.BudgetError as _be:
                            budget_ledger.mark_unknown(_rid, reason=f"presend-redact-fail: {_be}")
                            raise SystemExit(f"预算拒绝（发前存证失败，已标 UNKNOWN 并停止）：{_be}")
                        presend_dir.mkdir(parents=True, exist_ok=True)
                        _pf = presend_dir / f"presend-{mid}-{task.id}-r{run_idx}.json"
                        try:
                            with open(_pf, "w", encoding="utf-8", newline="\n") as _fh:
                                json.dump(
                                    {
                                        "reservation": _rid,
                                        "channel": endpoint_key,
                                        "api_model": api_model,
                                        "task": task.id,
                                        "run": run_idx,
                                        "effort_sent": effort,
                                        "task_sha256": _key.task_sha256,
                                        "request": {
                                            "url": _url,
                                            "headers": _redacted,
                                            "body": _body,
                                        },
                                    },
                                    _fh,
                                    ensure_ascii=False,
                                    indent=2,
                                )
                        except OSError as _oe:
                            budget_ledger.mark_unknown(_rid, reason=f"presend-persist-fail: {_oe}")
                            raise SystemExit(f"预算拒绝（发前存证落盘失败，已标 UNKNOWN 并停止）：{_oe}")
                result = soeb_client.call(
                    endpoint,
                    api_model,
                    messages,
                    max_tokens=task.max_tokens,
                    temperature=task.temperature,
                    effort=effort,
                    timeout=timeout,
                    save_raw=save_raw,
                )
                if budget_doc is not None and budget_ledger is not None and _rid is not None:
                    _billed, _btotal, _unknown = soeb_client.settlement_for_result(result)
                    if _unknown or _billed is None or _btotal is None:
                        budget_ledger.mark_unknown(
                            _rid,
                            reason=(
                                f"unknown-settlement: ok={result.ok} "
                                f"status={result.status_code} err={(result.error or '')[:120]}"
                            ),
                        )
                        rec_u = result.to_dict()
                        rec_u["model_id"] = mid
                        rec_u["api_model"] = api_model
                        rec_u["channel"] = endpoint_key
                        rec_u["task"] = task.id
                        rec_u["run"] = run_idx
                        rec_u["task_note"] = task.note
                        rec_u["effort_sent"] = effort
                        rec_u["budget_reservation"] = _rid
                        rec_u["budget_status"] = "UNKNOWN"
                        observations.append(rec_u)
                        if sink is not None:
                            sink(rec_u)
                        print(
                            f"[BUDGET-UNKNOWN] {mid:<26} {task.id:<28} r{run_idx} "
                            f"预留已保留并 HALT，停止后续发送"
                        )
                        break
                    assert _caps is not None
                    if int(_billed) > int(_caps["single_shot_max_completion"]):
                        budget_ledger.mark_unknown(_rid, reason=f"over-single-shot: billed={_billed}")
                        raise SystemExit(
                            f"预算拒绝（单发超限 billed={_billed}，已标 UNKNOWN 并停止）"
                        )
                    budget_ledger.settle(
                        _rid,
                        billed_completion=int(_billed),
                        billed_total=int(_btotal),
                        assoc={
                            "model_id": mid,
                            "task": task.id,
                            "run": run_idx,
                            "status_code": result.status_code,
                            "finish_reason": result.finish_reason,
                        },
                    )
                rec = result.to_dict()
                # 觀測列的 model_id 一律是 registry 的參賽者 id，不是上游 API 名。
                # 否則 deepseek 會以 `deepseek-v4.1-flash:1017` 的身分出現，
                # 下游評分／匿名化就會把它當成第七個參賽者。
                rec["model_id"] = mid
                rec["api_model"] = api_model
                rec["channel"] = endpoint_key
                rec["task"] = task.id
                rec["run"] = run_idx
                rec["task_note"] = task.note
                rec["effort_sent"] = effort
                if _rid is not None:
                    rec["budget_reservation"] = _rid
                    rec["budget_status"] = "settled"
                observations.append(rec)
                if sink is not None:
                    sink(rec)
                flag = "OK " if result.ok else "ERR"
                print(
                    f"[{flag}] {mid:<26} {task.id:<28} r{run_idx} {result.latency_ms:>6}ms "
                    f"in={result.usage.get('input_tokens')} out={result.usage.get('output_tokens')}"
                    + (f" :: {result.empty_reason}" if result.empty_reason else "")
                    + (f" :: {result.error[:120]}" if result.error else "")
                )
    return observations


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="SOEB 執行器（OpenCode Go 單一通道）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tasks", required=True, help="task spec JSON 路徑")
    parser.add_argument("--out", required=True, help="輸出目錄（不得落在 repo data/ 之下）")
    parser.add_argument(
        "--models",
        default="all",
        help="逗號分隔的模型 id，或 all（預設排除 registry 標記 blocked 的模型，除非指名）",
    )
    parser.add_argument("--max-tokens", type=int, default=2048, help="task 未指定時的預設值")
    parser.add_argument(
        "--force-max-tokens",
        type=int,
        default=None,
        help=(
            "強制覆寫**所有** task 的 max_tokens（含 spec 內已明確指定者）。"
            "推理模型的 thinking 通常計入 completion_tokens，"
            "原預算會把正文吃光 ⇒ 重跑時用它把天花板拉高，並在 manifest 記錄。"
        ),
    )
    parser.add_argument("--temperature", type=float, default=None, help="覆寫所有 task 的溫度")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--config", default=None, help="MiniMax Code config.yaml 路徑（預設自動偵測）")
    parser.add_argument("--no-save-raw", action="store_true", help="不保存原始回應（省空間）")
    parser.add_argument("--run", action="store_true", help="真的發送請求（會產生 LLM 費用）")
    parser.add_argument(
        "--budget-profile",
        default=None,
        help="预算 profile 名（budgets.json 内条目；--run 必填，无回落默认值）",
    )
    parser.add_argument(
        "--budgets",
        default=None,
        help="budgets.json 路径（默认 scripts/soeb/budgets.json）",
    )
    parser.add_argument(
        "--budget-auth-id",
        default=None,
        help="账本独占授权 ID（默认取 --budget-profile 的值）",
    )
    parser.add_argument(
        "--budget-ledger",
        default=None,
        help="账本路径（默认 <out>/budget_ledger.json，必在 out 之下）",
    )
    parser.add_argument("--allow-blocked", action="store_true", help="連 registry 標記 blocked 的模型也一起打")
    parser.add_argument(
        "--only",
        default=None,
        help=(
            "只跑指定題目（逗號分隔，例如 T1,T6）。"
            "用途是「預算探針」：先確認某顆模型在拉高天花板後會不會自然結束，"
            "再決定要不要花整輪重跑；避免把題目文字複製成第二份 spec 造成漂移。"
        ),
    )
    args = parser.parse_args(argv)

    out_dir = assert_safe_out_dir(Path(args.out))
    registry = load_registry()
    try:
        spec = load_spec(Path(args.tasks), default_max_tokens=args.max_tokens)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    tasks: list[Task] = spec["_tasks"]
    system_prompt: str = spec["_system_prompt"]

    if args.only:
        wanted = [x.strip() for x in args.only.split(",") if x.strip()]
        known = {t.id for t in tasks}
        unknown_tasks = [x for x in wanted if x not in known]
        if unknown_tasks:
            print(f"Error: spec 內沒有這些題目：{unknown_tasks}", file=sys.stderr)
            return 2
        tasks = [t for t in tasks if t.id in set(wanted)]

    if args.temperature is not None:
        for t in tasks:
            t.temperature = args.temperature

    # 輸出預算強制覆寫。必須在 load_spec 之後、送請求之前，
    # 且記錄「原值 → 覆寫值」，否則重跑後沒人能還原當時用的是哪個預算。
    budget_override: dict[str, Any] | None = None
    if args.force_max_tokens is not None:
        if args.force_max_tokens < 1:
            print("Error: --force-max-tokens 必須 >= 1", file=sys.stderr)
            return 2
        budget_override = {
            "forced_max_tokens": args.force_max_tokens,
            "original": {t.id: t.max_tokens for t in tasks},
        }
        for t in tasks:
            t.max_tokens = args.force_max_tokens

    by_id = {m["id"]: m for m in registry["models"]}
    if args.models.strip().lower() == "all":
        model_ids = [
            m["id"]
            for m in registry["models"]
            if args.allow_blocked or m.get("status") != "blocked"
        ]
        excluded = [m["id"] for m in registry["models"] if m["id"] not in model_ids]
    else:
        model_ids = [x.strip() for x in args.models.split(",") if x.strip()]
        excluded = []
        unknown = [m for m in model_ids if m not in by_id]
        if unknown:
            print(f"Error: registry 內沒有這些模型：{unknown}", file=sys.stderr)
            return 2

    print("=" * 78)
    print(f"suite      : {spec.get('suite', '(未命名)')}   (spec sha256 {spec['_sha256'][:12]}…)")
    print(f"tasks      : {len(tasks)} 筆")
    print(f"models     : {len(model_ids)} 顆  {model_ids}")
    if excluded:
        print(f"排除       : {excluded}（registry 標記 blocked，--allow-blocked 可強制）")
    print(f"out        : {out_dir}")
    if system_prompt:
        src = spec.get("_system_prompt_source", "spec 內嵌")
        print(
            f"sys prompt : {len(system_prompt)} 字元 "
            f"(sha256 {spec['_system_prompt_sha256'][:12]}…, {src}) —— 六家位元相同"
        )
    else:
        print("sys prompt : (無)")
    print("-" * 78)
    print(f"{'model':<28}{'status':<10}{'endpoint':<18}{'calls':>6}{'est$ ceiling':>14}")
    for row in estimate_cost(registry, tasks, model_ids, system_prompt=system_prompt):
        cost = f"{row['est_usd_ceiling']:>14.6f}" if row["price_known"] else f"{'n/a':>14}"
        print(
            f"{row['model']:<28}{row['status']:<10}{row['endpoint']:<18}"
            f"{row['calls']:>6}{cost}"
        )
    print("=" * 78)

    if not args.run:
        print("未指定 --run ⇒ 只做可行性檢查，未發出任何請求、未寫入任何檔案。")
        print("確認上面的矩陣與成本量級後，加上 --run 才會真的執行。")
        return 0

    # ── 限额前置（SA-HARNESS-BUDGET-1）：--run 必须指定 profile；超限直接拒绝 ──
    if not args.budget_profile:
        print("Error: --run 必须同时指定 --budget-profile（无默认回落）", file=sys.stderr)
        return 2
    _budgets_path = Path(args.budgets) if args.budgets else (SOEB_DIR / "budgets.json")
    try:
        budget_doc = harness_budget.load_budgets(_budgets_path)
    except harness_budget.BudgetError as exc:
        print(f"Error: 预算配置无效：{exc}", file=sys.stderr)
        return 2
    if args.budget_profile not in (budget_doc.get("profiles") or {}):
        print(
            f"Error: budgets 内无 profile {args.budget_profile!r}（禁回落），"
            f"可选：{sorted((budget_doc.get('profiles') or {}))}",
            file=sys.stderr,
        )
        return 2
    _auth_id = args.budget_auth_id or args.budget_profile
    _ledger_path = Path(args.budget_ledger) if args.budget_ledger else (out_dir / "budget_ledger.json")
    try:
        _ledger_resolved = _ledger_path.resolve()
        _out_resolved = out_dir.resolve()
        if _ledger_resolved != _out_resolved and _out_resolved not in _ledger_resolved.parents:
            print(f"Error: 账本必须落在 out 之下：{_ledger_path}", file=sys.stderr)
            return 2
    except OSError as exc:
        print(f"Error: 账本路径无效：{exc}", file=sys.stderr)
        return 2
    budget_ledger = harness_budget.Ledger(_ledger_path)
    try:
        budget_ledger.acquire(_auth_id)
    except harness_budget.BudgetError as exc:
        print(f"Error: 账本独占锁拒绝：{exc}", file=sys.stderr)
        return 2
    # 发前总量预检：任一 (模型,题,次) 无显式配置或超限 ⇒ 直接拒绝，不发送
    try:
        for _mid in model_ids:
            _meta = by_id[_mid]
            _ekey = _meta.get("endpoint") or _meta["protocol"]
            _api = _meta.get("api_model") or _mid
            for _t in tasks:
                _msgs = list(_t.messages)
                if system_prompt:
                    _msgs = [{"role": "system", "content": system_prompt}, *_msgs]
                _eff = _t.effort
                if _eff and _eff not in (_meta.get("effort_supported") or []):
                    _eff = None
                _k = harness_budget.ProfileKey(
                    channel=_ekey,
                    api_model=_api,
                    task_sha256=harness_budget.canonical_task_sha256(_msgs),
                    effort=_eff,
                )
                _, _caps = harness_budget.resolve_profile(budget_doc, _k)
                budget_ledger.check_fits(
                    single_shot_max_completion=int(_caps["single_shot_max_completion"]),
                    total_max_completion=int(_caps["total_max_completion"]),
                    max_shots=int(_caps["max_shots"]),
                    planned_max_tokens=int(_t.max_tokens),
                    planned_new_shots=1,
                )
                # 预检只判“本发是否放得下”，不占位（占位在发送前逐发 reserve）。
                # 但发数上限需按整批总量判：剩余容量必须 ≥ 整批剩余发数。
        _planned_total = sum(_t.runs for _t in tasks) * len(model_ids)
        _first_caps: dict[str, Any] | None = None
        for _mid in model_ids:
            _meta = by_id[_mid]
            _ekey = _meta.get("endpoint") or _meta["protocol"]
            _api = _meta.get("api_model") or _mid
            for _t in tasks:
                _msgs = list(_t.messages)
                if system_prompt:
                    _msgs = [{"role": "system", "content": system_prompt}, *_msgs]
                _eff = _t.effort
                if _eff and _eff not in (_meta.get("effort_supported") or []):
                    _eff = None
                _, _caps = harness_budget.resolve_profile(
                    budget_doc,
                    harness_budget.ProfileKey(
                        channel=_ekey,
                        api_model=_api,
                        task_sha256=harness_budget.canonical_task_sha256(_msgs),
                        effort=_eff,
                    ),
                )
                _first_caps = _first_caps or _caps
                break
            if _first_caps is not None:
                break
        if _first_caps is not None and budget_ledger.used_shots() + _planned_total > int(_first_caps["max_shots"]):
            print(
                f"Error: 发数超限：已用 {budget_ledger.used_shots()} + 本批 {_planned_total} > "
                f"max_shots={_first_caps['max_shots']}，拒绝发送",
                file=sys.stderr,
            )
            return 2
    except harness_budget.BudgetError as exc:
        print(f"Error: 预算拒绝（未发送）：{exc}", file=sys.stderr)
        return 2
    presend_dir = out_dir / "presend"
    presend_dir.mkdir(parents=True, exist_ok=True)

    endpoints = soeb_client.load_endpoints(args.config)
    print(f"已載入 {len(endpoints)} 個協定端點：{sorted(endpoints)}")

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 增量落盤：每產生一筆觀測就立刻寫入並 flush。
    # 推理模型單題可能跑幾十分鐘；只在全部跑完才寫檔，任何一次中斷都會讓
    # 已完成的所有觀測一起消失（而費用已經花掉了）。
    out_dir.mkdir(parents=True, exist_ok=True)
    obs_path = out_dir / "observations.jsonl"
    obs_fh = open(obs_path, "w", encoding="utf-8", newline="\n")

    def _sink(rec: dict[str, Any]) -> None:
        obs_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        obs_fh.flush()

    try:
        observations = run_matrix(
            endpoints,
            registry,
            tasks,
            model_ids,
            system_prompt=system_prompt,
            save_raw=not args.no_save_raw,
            timeout=args.timeout,
            sink=_sink,
            budget_doc=budget_doc,
            budget_ledger=budget_ledger,
            presend_dir=presend_dir,
        )
    finally:
        obs_fh.close()

    # 账本摘要（发后存证，留在 harness out 内）
    budget_summary = budget_ledger.summary()
    with open(out_dir / "budget_settle.json", "w", encoding="utf-8", newline="\n") as _bf:
        json.dump(budget_summary, _bf, ensure_ascii=False, indent=2)

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    ok_count = sum(1 for o in observations if o.get("ok"))
    manifest = {
        "runner": "soeb",
        "spec_path": spec["_path"],
        "spec_sha256": spec["_sha256"],
        "suite": spec.get("suite"),
        "started_at": started_at,
        "finished_at": finished_at,
        "models": model_ids,
        "excluded_models": excluded,
        "tasks": [{"id": t.id, "max_tokens": t.max_tokens, "temperature": t.temperature,
                   "effort": t.effort, "note": t.note} for t in tasks],
        "context_packet": spec.get("context_packet"),
        "budget_override": budget_override,
        "system_prompt_sha256": spec["_system_prompt_sha256"],
        "system_prompt_chars": len(system_prompt),
        "system_prompt_source": spec.get("_system_prompt_source", "inline"),
        "total_calls": len(observations),
        "ok_calls": ok_count,
        "failed_calls": len(observations) - ok_count,
        "raw_saved": not args.no_save_raw,
        "channel": registry["channel"],
        # 逐顆揭露通道：混通道會破壞可比性，這個欄位是讓報告不能假裝沒這回事。
        "channels": {
            mid: {
                "endpoint": by_id[mid].get("endpoint") or by_id[mid]["protocol"],
                "api_model": by_id[mid].get("api_model") or mid,
                "channel_note": by_id[mid].get("channel_note", "opencode-go"),
            }
            for mid in model_ids
        },
        "mixed_channels": len(
            {
                by_id[mid].get("endpoint") or by_id[mid]["protocol"]
                for mid in model_ids
            }
        )
        > 1,
        "registry_version": registry["registry_version"],
        "budget_profile": args.budget_profile,
        "budget_auth_id": _auth_id,
        "budget_ledger": str(_ledger_path),
        "budget_summary": budget_summary,
    }
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    print("-" * 78)
    print(f"完成：{ok_count}/{len(observations)} 成功")
    print(f"觀測   : {obs_path}")
    print(f"清單   : {manifest_path}")
    if ok_count != len(observations):
        for rec in observations:
            if not rec.get("ok"):
                print(f"  失敗 {rec.get('model_id')} / {rec.get('task')}: {(rec.get('error') or '')[:200]}")
    return 0 if ok_count == len(observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
