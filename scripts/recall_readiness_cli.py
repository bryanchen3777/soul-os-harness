#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/recall_readiness_cli.py — 回憶通道的成熟度量測 CLI（唯讀來源 + 自建沙盒）。

**為什麼存在**（ELEVATION-RECALL-HARDENING-1 工單 §3.4）：
第二階段（prompt 注入）不該靠日期靠感覺開工，而該靠**數據判準**：
  ① 生產圖譜的「可回憶池」到底有幾顆（成熟度）；
  ② 真實 user 訊息打進來的命中率是多少（可量測）；
  ③ 四項門檻全過才 GO（工單 §9）。

本腳本把這三件事做成**十秒內可重跑**的離線量測。

🔴🔴🔴 **安全契約（沿用 eval_lifecycle_cli.py 的沙盒化紀律）**：
本腳本**沒有任何寫入來源的路徑**。
  - 來源（預設＝生產 `data/elevation/`）**只被讀取與複製**，永遠不被寫入。
  - 唯一寫入目標＝本腳本 `tempfile.mkdtemp()` 建立的沙盒（量測都在副本上跑）
    ＋ `%TEMP%` 裡的報告檔。
  - **不提供任何就地寫入旗標**（2026-10-19 事故教訓：不要用「可能漂移的兩個值」
    去擋寫入——把寫入能力本身移除才是結構性保證）。
  - 報告前後對生產檔算 sha256 並對帳 ⇒ 「位元組前後不變」是可驗證的，不是承諾。

**其他硬性保證**：
  - 不啟動伺服器、不綁任何埠、不發任何網路請求。
  - 不 import `subprocess` / `requests` / `httpx` / `socket` / `uvicorn` / `fastapi`。
  - 不寫任何 repo 檔案（報告寫 `%TEMP%`）。
  - 詞元來源 SSOT：`src/memory/v1/loader.py::derive_query_tags`（**不重寫一份**）。

用法：
    .venv\\Scripts\\python.exe scripts\\recall_readiness_cli.py
    .venv\\Scripts\\python.exe scripts\\recall_readiness_cli.py --probes 59
    .venv\\Scripts\\python.exe scripts\\recall_readiness_cli.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life.elevation_recall import query_recall  # noqa: E402
from src.inner_life.emergent_projection import load_elevation_nodes  # noqa: E402
from src.inner_life.lifecycle_overlay import (  # noqa: E402
    LIFECYCLE_EVENTS_FILENAME,
    load_quarantined_node_ids,
)
from src.inner_life.recall_readiness import (  # noqa: E402
    go_no_go,
    hit_rate,
    pool_summary,
)

NODES_FILENAME = "elevation_nodes.jsonl"
COPY_FILES = (NODES_FILENAME, LIFECYCLE_EVENTS_FILENAME)
DEFAULT_PROBES = 200


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


def runtime_conversations_dir() -> Path:
    """對話（探針來源）目錄，預設＝repo 內的生產 `data/conversations/`。"""
    return REPO_ROOT / "data" / "conversations"


# ── I/O（全部唯讀）────────────────────────────────────────────────────────
def sha256_of(path: Path) -> str:
    """檔案 sha256；檔不存在回 ``""``（不 raise）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def copy_graph(src: Path, dst: Path) -> None:
    """把來源圖譜的必要檔案複製到沙盒（**不**複製 40MB 的 trace 檔）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for name in COPY_FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)


def read_json(path: Path):
    """讀 JSON 檔；壞檔回 ``None``（不 raise）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def agent_id_from_stem(stem: str) -> str:
    """從對話檔名推回 agent id（``…_agent_<name>_private`` → ``agent_<name>``）。"""
    head = stem
    if head.endswith("_private"):
        head = head[: -len("_private")]
    if "_agent_" in head:
        return "agent_" + head.rsplit("_agent_", 1)[-1]
    return ""


def collect_user_messages(conv_dir: Path, limit: int):
    """收集真實 user 訊息（唯讀）並詞元化。

    以檔案 mtime 由新到舊排序（＝「最近」），每個檔案內照原序取
    ``role == "user"`` 的訊息；回傳 ``[(agent_id, tags, content), ...]``，
    上限 ``limit`` 筆。

    詞元化走 SSOT ``src/memory/v1/loader.py::derive_query_tags``（**不重寫一份**）。

    只認得出身為 ``…_agent_<name>_private`` 的檔案（單一靈魂的私聊）——
    群聊（``group_chat.json``）沒有唯一 agent，不能當探針。
    """
    try:
        from src.memory.v1.loader import derive_query_tags
    except Exception as exc:  # pragma: no cover — 依賴缺失時明確失敗
        print("[refuse] 無法載入詞元 SSOT（derive_query_tags）：%s: %s"
              % (type(exc).__name__, exc), file=sys.stderr)
        return []

    if not conv_dir.exists():
        return []

    files = [p for p in conv_dir.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)

    out = []
    for path in files:
        if len(out) >= limit:
            break
        agent_id = agent_id_from_stem(path.stem)
        if not agent_id:
            continue
        records = read_json(path)
        if not isinstance(records, list):
            continue
        for rec in records:
            if len(out) >= limit:
                break
            if not isinstance(rec, dict):
                continue
            if rec.get("role") != "user":
                continue
            content = rec.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            out.append((agent_id, derive_query_tags(content), content))
    return out


# ── 主流程 ────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="recall_readiness_cli",
        description="回憶通道成熟度量測（唯讀來源 + 自建沙盒；永不寫入來源）",
    )
    ap.add_argument("--source-dir", default=None,
                    help="圖譜來源目錄（**唯讀**）。預設 = 執行期資料根的 elevation/")
    ap.add_argument("--conversations-dir", default=None,
                    help="對話（探針）目錄（**唯讀**）。預設 = repo 內 data/conversations/")
    ap.add_argument("--probes", type=int, default=DEFAULT_PROBES,
                    help="取最近 N 筆真實 user 訊息當探針（預設 %d）" % DEFAULT_PROBES)
    ap.add_argument("--report", default=None,
                    help="完整 UTF-8 報告輸出路徑（預設寫到 %%TEMP%%）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出到 stdout")
    args = ap.parse_args(argv)

    source = Path(args.source_dir) if args.source_dir else runtime_store_dir()
    if not source.exists():
        print("[refuse] 來源目錄不存在：%s" % source, file=sys.stderr)
        return 2
    conv_dir = Path(args.conversations_dir) if args.conversations_dir \
        else runtime_conversations_dir()

    # 🔴 結構性保證：寫入目標只有 sandbox，且必須位於 temp 之下。
    sandbox = Path(tempfile.mkdtemp(prefix="recall_readiness_sandbox_"))
    try:
        temp_root = Path(tempfile.gettempdir()).resolve()
        if temp_root not in sandbox.resolve().parents:
            print("[refuse] 沙盒不在 temp 之下（%s）—— 內部錯誤，中止。" % sandbox,
                  file=sys.stderr)
            return 3

        before = {name: sha256_of(source / name) for name in COPY_FILES}
        copy_graph(source, sandbox)

        # 圖譜在**沙盒副本**上讀（⇒ 來源結構上不可能被寫）。
        nodes = load_elevation_nodes(sandbox)
        # 隔離 id 集合（sidecar 的唯一解讀者）——與節點讀自同一個沙盒。
        quarantined = load_quarantined_node_ids(sandbox)
        summary = pool_summary(nodes, quarantined)

        # 探針：最近 N 筆真實 user 訊息 → 詞元（SSOT）。
        messages = collect_user_messages(conv_dir, max(0, args.probes))
        probes = []
        raw_probes = []
        for agent_id, tags, content in messages:
            probes.append((agent_id, tags))
            raw_probes.append((agent_id, content))

        hit = hit_rate(nodes, probes, quarantined_ids=quarantined)

        # 契約路徑（整句子字串）對照組：同一批探針、同樣的守門。
        contract_hits = 0
        for agent_id, content in raw_probes:
            if query_recall(agent_id, content, store_dir=sandbox):
                contract_hits += 1

        verdict = go_no_go(summary, hit)

        after = {name: sha256_of(source / name) for name in COPY_FILES}
        unchanged = {name: (before[name] == after[name]) for name in COPY_FILES}

        result = {
            "mode": "sandbox-only",
            "source_dir": str(source),
            "source_is_repo_production": str(source) == str(repo_store_dir()),
            "sandbox_dir": str(sandbox),
            "conversations_dir": str(conv_dir),
            "production_sha256_before": before,
            "production_sha256_after": after,
            "production_unchanged": unchanged,
            "all_production_unchanged": all(unchanged.values()),
            "summary": summary,
            "hit": hit,
            "contract_path": {
                "description": "契約路徑 query_recall（整句子字串）的同一批探針對照組",
                "probes_total": len(raw_probes),
                "probes_hit": contract_hits,
                "hit_rate": (contract_hits / len(raw_probes)) if raw_probes else 0.0,
                "unit": "probe",
            },
            "verdict": verdict,
        }

        if args.json:
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            lines = [
                "=" * 74,
                "RECALL READINESS (CLI) — mode=sandbox-only",
                "=" * 74,
                "source_dir (唯讀)    = %s" % source,
                "  來源=repo 生產目錄  = %s" % result["source_is_repo_production"],
                "sandbox_dir (唯一寫入)= %s" % sandbox,
                "conversations_dir     = %s" % conv_dir,
                "",
                "-- 回憶池成熟度（單位：顆）--",
                "total_nodes           = %d" % summary["total_nodes"],
                "by_state              = %s" % summary["by_state"],
                "by_node_type          = %s" % summary["by_node_type"],
                "quarantined           = %d" % summary["quarantined"],
                "excluded_pattern      = %d" % summary["excluded_pattern"],
                "blocked_by_quarantine = %d" % summary["blocked_by_quarantine"],
                "recallable (池)        = %d" % summary["recallable"],
                "recallable_by_state   = %s" % summary["recallable_by_state"],
                "",
                "-- 命中率（單位：probe＝一筆真實 user 訊息）--",
                "token-level (新入口)  = %d/%d = %.4f  命中 unique 節點 %d 顆"
                % (hit["probes_hit"], hit["probes_total"], hit["hit_rate"],
                   hit["unique_nodes_hit"]),
                "contract path (對照)  = %d/%d = %.4f"
                % (contract_hits, len(raw_probes),
                   result["contract_path"]["hit_rate"]),
                "quarantined matches   = %d  非隔離命中比例 = %.4f"
                % (hit["match_pairs_quarantined"], hit["non_quarantined_hit_ratio"]),
                "",
                "-- 第二階段接線判準 --",
                "VERDICT: %s" % verdict["verdict"],
            ]
            for r in verdict["reasons"]:
                lines.append("  - %s" % r)
            lines.append("")
            lines.append("production unchanged   = %s" % result["all_production_unchanged"])
            print("\n".join(lines).encode("ascii", "backslashreplace").decode("ascii"))

        report_path = Path(args.report) if args.report else (
            Path(os.environ.get("TEMP", ".")) / "recall_readiness_report.md"
        )
        body = [
            "# 回憶通道成熟度報告（recall_readiness_cli）",
            "",
            "- 模式：`sandbox-only`（唯一寫入目標＝自建沙盒）",
            "- 來源（唯讀）：`%s`" % source,
            "- 來源是否為 repo 生產目錄：%s" % result["source_is_repo_production"],
            "- 沙盒：`%s`" % sandbox,
            "- 對話（探針）來源：`%s`" % conv_dir,
            "",
            "## 生產檔位元組對帳（sha256）",
            "",
            "| 檔案 | before | after | 相同 |",
            "|---|---|---|---|",
        ]
        for name in COPY_FILES:
            body.append("| %s | `%s` | `%s` | %s |" % (
                name, before[name][:16], after[name][:16], unchanged[name]))
        body += [
            "",
            "## 回憶池成熟度（單位：顆）",
            "",
            "- 節點總數：%d" % summary["total_nodes"],
            "- lifecycle_state 分布：%s" % summary["by_state"],
            "- node_type 分布：%s" % summary["by_node_type"],
            "- 隔離節點：%d" % summary["quarantined"],
            "- pattern（結構性排除）：%d" % summary["excluded_pattern"],
            "- 被隔離守門擋下（否則可回憶）：%d" % summary["blocked_by_quarantine"],
            "- **可回憶池（非隔離 × 四層 × dormant|superseded）：%d**"
            % summary["recallable"],
            "- 池內態分布：%s" % summary["recallable_by_state"],
            "",
            "## 命中率（單位：probe＝一筆真實 user 訊息）",
            "",
            "- 詞元級 union（新入口）：**%d / %d = %.4f**（%.2f%%）"
            % (hit["probes_hit"], hit["probes_total"], hit["hit_rate"],
               hit["hit_rate"] * 100),
            "- 命中的 unique 節點：%d 顆" % hit["unique_nodes_hit"],
            "- 契約路徑（整句子字串）對照組：%d / %d = %.4f（%.2f%%）"
            % (contract_hits, len(raw_probes), result["contract_path"]["hit_rate"],
               result["contract_path"]["hit_rate"] * 100),
            "- 命中配對數：%d 組；其中隔離節點：%d 組；非隔離命中比例：%.4f"
            % (hit["match_pairs"], hit["match_pairs_quarantined"],
               hit["non_quarantined_hit_ratio"]),
            "- tags 全無效的 probe：%d" % hit["probes_without_tags"],
            "",
            "## 第二階段接線判準（go/no-go）",
            "",
            "- **判定：%s**" % verdict["verdict"],
            "",
            "| 條件 | 值 | 門檻 | 通過 |",
            "|---|---|---|---|",
        ]
        for key, c in verdict["checks"].items():
            body.append("| %s | %s | %s | %s |" % (
                key, c.get("value"), c.get("threshold"), "是" if c.get("pass") else "否"))
        if verdict["reasons"]:
            body.append("")
            body.append("### 未過原因")
            body.append("")
            for r in verdict["reasons"]:
                body.append("- %s" % r)
        body.append("")
        report_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        print("[report] %s" % report_path)
        return 0 if verdict["verdict"] == "GO" else 1
    finally:
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
