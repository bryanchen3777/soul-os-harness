#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/eval_lifecycle_cli.py — 生命週期評估的**離線 CLI 驅動器**（唯讀來源 + 自建沙盒）。

**為什麼存在**（Owner 2026-10-19 裁示「方案一」）：
`evaluate_lifecycle_all()` 全量跑完 398 節點 / 903 邊只要 0.066 秒，但接線在
scheduler 的 night slot（22:00）上 ⇒ 功能驗收被迫等 5～6 小時的牆鐘。
本腳本把「功能邏輯驗收」從牆鐘排程上解耦：十秒內跑完並印出對帳報告。

**保真度保證**：`evaluate_lifecycle_all()` 是純 disk→disk 函式（自節點檔＋邊檔重建
引擎，不依賴伺服器行程的任何記憶體狀態），因此「在生產圖譜的**副本**上跑」與
「22:00 在生產目錄上跑」的**計算結果逐位元相同**，唯一差別是 sidecar 的寫入目標。
⇒ 離線驗收不是模擬，是等價重現。

🔴🔴🔴 **安全契約（2026-10-19 事故後重寫）— 本腳本沒有任何「就地寫入」路徑**：
本腳本**永遠只寫入自己 `tempfile.mkdtemp()` 建立的沙盒**。來源目錄（預設＝生產
`data/elevation/`）只被**讀取與複製**，永遠不被寫入。

**為什麼是「移除能力」而不是「加更嚴的判斷」**：
前一版曾提供 `--data-dir … --in-place`（就地寫入），其「拒絕寫生產」的判斷是
`_same_dir(target, production_dir)`。該判斷**被實測證偽**：生產目錄的解析值取自
`src.paths.data_root()`，而它是**行程期快取**的單例；`tests/conftest.py` 的
autouse fixture 會把 `SOUL_OS_DATA_DIR` 指向每個測試自己的 tmp 目錄，於是
「測試行程快取到的生產目錄」與「CLI 子行程即時重讀到的生產目錄」**不是同一個**，
比較失效 ⇒ CLI 對生產執行了就地寫入，寫進 167 筆 lifecycle_transition。
⇒ 教訓：**不要用「可能漂移的兩個值」去擋寫入**。**把寫入能力本身拿掉**，才是
結構性保證。本檔因此**不提供任何就地寫入旗標**。

**其他硬性保證**：
- 不啟動任何伺服器、不綁任何埠、不發任何網路請求（`llm=None`）。
- 不修改 `src/**`、`data/**`；只新增 `scripts/eval_lifecycle_cli.py`。
- 沙盒在結束時刪除；報告寫到 `%TEMP%`。

用法：
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py --simulated-time 2026-10-20T02:00:00+00:00
    .venv\\Scripts\\python.exe scripts\\eval_lifecycle_cli.py --source-dir <graph dir>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life.elevation_adapter import evaluate_lifecycle_all  # noqa: E402
from src.inner_life.emergent_projection import (  # noqa: E402
    WEAKENING_TONE_PREFIX,
    format_emergent_block,
)
from src.inner_life.lifecycle_overlay import LIFECYCLE_EVENTS_FILENAME  # noqa: E402

NODES_FILENAME = "elevation_nodes.jsonl"
EDGES_FILENAME = "elevation_edges.jsonl"
COPY_FILES = (NODES_FILENAME, EDGES_FILENAME, LIFECYCLE_EVENTS_FILENAME)

SOUL_TYPES = ("belief", "value", "trait", "essence")
DEFAULT_KEY_NODE = "6a92c906b245"


# ── 路徑 ──────────────────────────────────────────────────────────────────
def runtime_store_dir() -> Path:
    """**執行期**的資料根（可能受 `SOUL_OS_DATA_DIR` 影響）。僅作預設來源。"""
    try:
        from src.paths import data_root

        return Path(data_root()) / "elevation"
    except Exception:
        return REPO_ROOT / "data" / "elevation"


def repo_store_dir() -> Path:
    """repo 內的實體生產目錄（與 `SOUL_OS_DATA_DIR` 無關）。**永不寫入。**"""
    return REPO_ROOT / "data" / "elevation"


# ── I/O ───────────────────────────────────────────────────────────────────
def read_jsonl(path: Path) -> list:
    out = []
    if not path.exists():
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def copy_graph(src: Path, dst: Path) -> None:
    """把來源圖譜的必要檔案複製到沙盒（**不**複製 40MB 的 trace 檔）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)


def agent_ids(nodes: list) -> list:
    ids = {n.get("agent_id") for n in nodes if isinstance(n, dict)}
    ids.discard("default")
    ids.discard(None)
    return sorted(ids)


def prompt_snapshot(store_dir: Path, agents: list) -> dict:
    """用**真實**的 prompt 組裝函式離線算出各 agent 的投影正文。

    ``record_trace=False`` ⇒ 不寫 projection trace sidecar（唯讀）。
    """
    snap = {}
    for a in agents:
        try:
            block = format_emergent_block(a, store_dir=store_dir, record_trace=False)
        except Exception as exc:
            snap[a] = {"ok": False, "error": type(exc).__name__ + ": " + str(exc)}
            continue
        snap[a] = {
            "ok": True,
            "block": block,
            "len": len(block),
            "weak_prefix_count": block.count(WEAKENING_TONE_PREFIX),
        }
    return snap


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def assess(
    *,
    new_events: list,
    prompt_diffs: list,
    key_node: str,
    weak_before: int,
    weak_after: int,
    graph_nodes: int = 0,
    soul_types: tuple = SOUL_TYPES,
) -> list:
    """**純函式**：依四項準則回傳失敗清單（空清單 = 全過）。

    刻意抽成純函式：守衛測試能直接餵入構造出來的惡意事件，證明這套判定
    **不是空轉報綠**（例：塞一筆 value 類型的轉變進去，它必須報 FAIL）。

    ⚠️ 「0 筆新轉變」有兩種截然不同的成因，**不可混為一談**：
      - **圖是空的**（0 節點）⇒ 評估根本沒東西可算 ⇒ **FAIL**。
      - **圖非空但無新轉變** ⇒ 該轉的都已经轉過了（**冪等 no-op**）⇒ **不是失敗**。
        （本機 2026-10-19 實例：那 167 顆 pattern 已在 sidecar 裡是 weakening，
        重跑不會再降一級 ⇒ 今晚 22:00 的預期結果就是 0 筆。）
    ⇒ `graph_nodes` 必須傳入，才能分辨這兩者。
    """
    fails = []
    by_type = {}
    to_state = {}
    for e in new_events:
        by_type[e.get("node_type")] = by_type.get(e.get("node_type"), 0) + 1
        to_state[e.get("lifecycle_state")] = to_state.get(e.get("lifecycle_state"), 0) + 1

    if not new_events and graph_nodes <= 0:
        fails.append("① 沒有任何轉變產生，且圖為空（評估無圖可算）")
    soul_hits = {t: by_type.get(t, 0) for t in soul_types if by_type.get(t, 0)}
    if soul_hits:
        fails.append("② 靈魂結構誤傷：%s" % soul_hits)
    non_pattern = {k: v for k, v in by_type.items() if k != "pattern"}
    if non_pattern:
        fails.append("② 出現非 pattern 轉變：%s" % non_pattern)
    for bad in ("dormant", "superseded"):
        if to_state.get(bad):
            fails.append("② 轉變目標含 %s" % bad)
    key_hits = [e for e in new_events if e.get("node_id") == key_node]
    if key_hits:
        fails.append("③ 關鍵反例 %s 被轉變 %d 次" % (key_node, len(key_hits)))
    if prompt_diffs:
        fails.append("④ Prompt 投影正文改變：%s" % prompt_diffs)
    if weak_after != weak_before:
        fails.append("④ 弱化包裝計數 %d → %d" % (weak_before, weak_after))
    return fails


# ── 主流程 ────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="eval_lifecycle_cli",
        description="生命週期評估離線驅動器（唯讀來源 + 自建沙盒；永不寫入來源）",
    )
    ap.add_argument("--source-dir", default=None,
                    help="圖譜來源目錄（**唯讀**）。預設 = 執行期資料根的 elevation/")
    ap.add_argument("--simulated-time", default=None,
                    help="評估時鐘（ISO 8601）。預設 = 真實 now。"
                         "要預測某個 night slot 的結果時，傳入該 slot 的 UTC 時刻")
    ap.add_argument("--key-node", default=DEFAULT_KEY_NODE,
                    help="關鍵反例節點 id（必須維持 active，不得轉 weakening）")
    ap.add_argument("--report", default=None,
                    help="完整 UTF-8 報告輸出路徑（預設寫到 %%TEMP%%）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出到 stdout")
    args = ap.parse_args(argv)

    source = Path(args.source_dir) if args.source_dir else runtime_store_dir()
    if not source.exists():
        print("[refuse] 來源目錄不存在：%s" % source, file=sys.stderr)
        return 2

    sandbox = Path(tempfile.mkdtemp(prefix="lifecycle_cli_sandbox_"))
    try:
        # 🔴 結構性保證：寫入目標只有 sandbox，且必須位於 temp 之下。
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root not in sandbox.resolve().parents:
            print("[refuse] 沙盒不在 temp 之下（%s）—— 內部錯誤，中止。" % sandbox,
                  file=sys.stderr)
            return 3

        copy_graph(source, sandbox)
        sidecar = sandbox / LIFECYCLE_EVENTS_FILENAME
        nodes = read_jsonl(sandbox / NODES_FILENAME)
        agents = agent_ids(nodes)

        before_events = read_jsonl(sidecar)
        before_prompt = prompt_snapshot(sandbox, agents)

        t0 = time.perf_counter()
        events = evaluate_lifecycle_all(store_dir=sandbox, now_ts=args.simulated_time)
        elapsed = time.perf_counter() - t0

        after_events = read_jsonl(sidecar)
        after_prompt = prompt_snapshot(sandbox, agents)
        new_events = after_events[len(before_events):]

        by_type = {}
        to_state = {}
        for e in new_events:
            by_type[e.get("node_type")] = by_type.get(e.get("node_type"), 0) + 1
            to_state[e.get("lifecycle_state")] = to_state.get(e.get("lifecycle_state"), 0) + 1

        prompt_diffs = []
        for a in agents:
            b, aft = before_prompt.get(a, {}), after_prompt.get(a, {})
            if not (b.get("ok") and aft.get("ok")):
                prompt_diffs.append({"agent": a, "error": b.get("error") or aft.get("error")})
                continue
            if b.get("block") != aft.get("block"):
                prompt_diffs.append({
                    "agent": a,
                    "before_len": b.get("len"),
                    "after_len": aft.get("len"),
                    "before_weak": b.get("weak_prefix_count"),
                    "after_weak": aft.get("weak_prefix_count"),
                })

        weak_total_before = sum(v.get("weak_prefix_count", 0) for v in before_prompt.values())
        weak_total_after = sum(v.get("weak_prefix_count", 0) for v in after_prompt.values())

        # 評估後的圖譜狀態分布（overlay 套用後 ⇒ 反映 sidecar 的實際效果）
        state_dist = {}
        try:
            from src.inner_life.emergent_projection import load_elevation_nodes

            for n in load_elevation_nodes(sandbox):
                st = n.get("lifecycle_state") or "active"
                state_dist[st] = state_dist.get(st, 0) + 1
        except Exception:
            state_dist = {}
        graph_nodes = sum(state_dist.values())

        fails = assess(
            new_events=new_events,
            prompt_diffs=prompt_diffs,
            key_node=args.key_node,
            weak_before=weak_total_before,
            weak_after=weak_total_after,
            graph_nodes=graph_nodes,
        )

        result = {
            "mode": "sandbox-only",
            "source_dir": str(source),
            "source_is_repo_production": _same(source, repo_store_dir()),
            "sandbox_dir": str(sandbox),
            "simulated_time": args.simulated_time,
            "elapsed_seconds": round(elapsed, 4),
            "agents": agents,
            "sidecar_before": len(before_events),
            "sidecar_after": len(after_events),
            "transitions": len(new_events),
            "engine_events_returned": len(events),
            "by_node_type": by_type,
            "by_to_state": to_state,
            "soul_type_transitions": {t: by_type.get(t, 0) for t in SOUL_TYPES},
            "graph_nodes": graph_nodes,
            "graph_state_distribution": state_dist,
            "no_op_note": (not new_events and graph_nodes > 0),
            "key_node": args.key_node,
            "key_node_transitions": len([e for e in new_events
                                         if e.get("node_id") == args.key_node]),
            "weak_prefix_before": weak_total_before,
            "weak_prefix_after": weak_total_after,
            "prompt_diffs": prompt_diffs,
            "verdict": "FAIL" if fails else "PASS",
            "failures": fails,
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            lines = [
                "=" * 74,
                "LIFECYCLE OFFLINE EVAL (CLI) — mode=sandbox-only",
                "=" * 74,
                "source_dir (唯讀)    = %s" % source,
                "  來源=repo 生產目錄  = %s" % result["source_is_repo_production"],
                "sandbox_dir (唯一寫入)= %s" % sandbox,
                "simulated_time       = %s" % (args.simulated_time or "(real now)"),
                "elapsed_seconds      = %s" % result["elapsed_seconds"],
                "sidecar  %d -> %d   (新增 %d 筆轉變)"
                % (result["sidecar_before"], result["sidecar_after"], result["transitions"]),
                "by_node_type         = %s" % by_type,
                "by_to_state          = %s" % to_state,
                "soul_type_counts     = %s   (value/belief/trait/essence 必須全 0)"
                % result["soul_type_transitions"],
                "graph_nodes          = %d   state_dist=%s" % (graph_nodes, state_dist),
                "no_op_note           = %s" % (
                    "是（0 筆新轉變，但圖非空 ⇒ 冪等 no-op，非失敗）"
                    if result["no_op_note"] else "否"),
                "key_node %s transitions = %d  (必須 0)"
                % (args.key_node, result["key_node_transitions"]),
                "weak_prefix  %d -> %d" % (weak_total_before, weak_total_after),
                "prompt_diffs         = %s" % (prompt_diffs or "無（所有 agent 正文逐位元相同）"),
                "",
                "VERDICT: %s" % result["verdict"],
            ]
            for f in fails:
                lines.append("  - " + f)
            print("\n".join(lines).encode("ascii", "backslashreplace").decode("ascii"))

        report_path = Path(args.report) if args.report else (
            Path(os.environ.get("TEMP", ".")) / "eval_lifecycle_report.md")
        body = [
            "# 生命週期離線評估報告（eval_lifecycle_cli）",
            "",
            "- 模式：`sandbox-only`（唯一寫入目標＝自建沙盒）",
            "- 來源（唯讀）：`%s`" % source,
            "- 來源是否為 repo 生產目錄：%s" % result["source_is_repo_production"],
            "- 沙盒：`%s`" % sandbox,
            "- 評估時鐘：%s" % (args.simulated_time or "(real now)"),
            "- 耗時：%.4f 秒" % elapsed,
            "- sidecar：%d → %d（新增 %d 筆）"
            % (result["sidecar_before"], result["sidecar_after"], result["transitions"]),
            "- 轉變類型分布：%s" % by_type,
            "- 轉變目標狀態：%s" % to_state,
            "- 靈魂結構轉變（必須全 0）：%s" % result["soul_type_transitions"],
            "- 關鍵反例 `%s` 轉變次數（必須 0）：%d"
            % (args.key_node, result["key_node_transitions"]),
            "- 弱化包裝計數：%d → %d" % (weak_total_before, weak_total_after),
            "- Prompt 正文差異：%s" % (prompt_diffs or "無"),
            "- **判定：%s**" % result["verdict"],
            "",
            "## 各 agent 投影（評估前 → 評估後）",
            "",
            "| agent | before_len | after_len | weak 前 | weak 後 |",
            "|---|---|---|---|---|",
        ]
        for a in agents:
            b, aft = before_prompt.get(a, {}), after_prompt.get(a, {})
            body.append("| %s | %s | %s | %s | %s |" % (
                a, b.get("len", "-"), aft.get("len", "-"),
                b.get("weak_prefix_count", "-"), aft.get("weak_prefix_count", "-")))
        report_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("[report] %s" % report_path)
        return 1 if fails else 0
    finally:
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
