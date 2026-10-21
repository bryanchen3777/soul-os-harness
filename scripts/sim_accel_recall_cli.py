#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scripts/sim_accel_recall_cli.py — 虛擬時鐘快進沙盒 + 矛盾注入 + GO 判準量測 CLI。

**為什麼存在**（SIM-ACCEL-RECALL-30D 工單 §3.1）
--------------------------------------------------
方向 A（回憶召回）第二階段的接線判準是**四項 GO 判準**（池大小 / 池中 superseded /
真實命中率 / 命中非隔離）。其中第二項（superseded）**在數學上不可能靠時間快進達成**：
``engine.evaluate_lifecycle()`` 只做 active→weakening（age ≥ 7）與 weakening→dormant
（age ≥ 30），**永遠不產 superseded**；superseded 只能由 ``engine.supersede()`` 觸發，
而它的前置條件是「獨立矛盾證據 ≥ 3 且分布於 ≥ 2 個不同模擬日」。

⇒ 「矛盾跨 2 個不同模擬日」正好是**虛擬時鐘的強項**（把矛盾 ts 設在兩個不同模擬日）。
本 CLI 把「30 天老化 + 矛盾注入 + 四項判準量測」做成**十秒內可重跑**的離線工具。

🔴🔴🔴 **安全契約**（沿用 ``scripts/eval_lifecycle_cli.py`` 與
``scripts/recall_readiness_cli.py`` 的沙盒化紀律，逐條照做）：
  - **來源唯讀 ＋ 唯一寫入目標＝自建 temp 沙盒**。
  - **不提供任何就地寫入旗標**（2026-10-19 事故教訓：不要用「可能漂移的兩個值」去擋
    寫入——把寫入能力本身移除才是結構性保證）。
  - 沙盒必須在 ``tempfile.gettempdir()`` 之下，否則 ``[refuse]`` 並 exit 3。
  - 生產三檔 sha256 **前後對帳**；任一不同 ⇒ **中止並非零退出**（不是警告）。
  - **不 import** ``subprocess`` / ``requests`` / ``httpx`` / ``socket`` / ``uvicorn`` /
    ``fastapi``；不啟動伺服器、不綁埠、不發網路請求、不呼叫 LLM。
  - 不寫任何 repo 檔案（報告寫 ``%TEMP%``）。

**沙盒資料不得流回生產**：本 CLI 只寫自己的沙盒目錄，來源目錄全程唯讀。

用法：
    .venv\\Scripts\\python.exe scripts\\sim_accel_recall_cli.py --days 30
    .venv\\Scripts\\python.exe scripts\\sim_accel_recall_cli.py --days 45 --json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.inner_life import recall_readiness  # noqa: E402
from src.inner_life.elevation_adapter import (  # noqa: E402
    _load_edges,
    _rebuild_engine_for,
    evaluate_lifecycle_all,
)
from src.inner_life.emergent_projection import load_elevation_nodes  # noqa: E402
from src.inner_life.lifecycle_overlay import load_quarantined_node_ids  # noqa: E402
from src.inner_life.recall_prompt import collect_probe_messages  # noqa: E402
from soul_elevation import ContradictionRecord  # noqa: E402

NODES_FILENAME = "elevation_nodes.jsonl"
EDGES_FILENAME = "elevation_edges.jsonl"
LIFECYCLE_EVENTS_FILENAME = "elevation_lifecycle_events.jsonl"
#: 複製到沙盒的三個檔案（生產 ↔ 沙盒的對帳單位）。
COPY_FILES = (NODES_FILENAME, EDGES_FILENAME, LIFECYCLE_EVENTS_FILENAME)

DEFAULT_DAYS = 30
DEFAULT_CONTRADICTIONS = 1
DEFAULT_PROBES = 200
#: 快進輪次上限（nightly 一次一級：active→weakening→dormant 需 2 級，3 輪必冪等）。
MAX_FAST_FORWARD_PASSES = 6
#: 矛盾門檻（對齊 ``soul_elevation/prior.py``：獨立矛盾 ≥ 3、跨 ≥ 2 個不同模擬日）。
N_SUPERSEDE = 3
MIN_DAYS_SPREAD = 2
#: 矛盾注入只挑 ``value`` 型（essence 另有 valence 反轉 + confidence delta 門檻）。
SUPERSEDE_TARGET_NODE_TYPE = "value"


# ── 路徑 ──────────────────────────────────────────────────────────────────
def repo_store_dir() -> Path:
    """repo 內的實體生產圖譜目錄（與 ``SOUL_OS_DATA_DIR`` 無關）。**永不寫入。**"""
    return REPO_ROOT / "data" / "elevation"


def repo_conversations_dir() -> Path:
    """repo 內的實體生產對話目錄（探針來源，**唯讀**）。"""
    return REPO_ROOT / "data" / "conversations"


# ── I/O（來源唯讀）────────────────────────────────────────────────────────
def sha256_of(path: Path) -> str:
    """檔案 sha256；檔不存在回 ``""``（不 raise）。"""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def copy_graph(src: Path, dst: Path) -> List[str]:
    """把來源圖譜的必要檔案複製到沙盒（**不**複製 40MB 的 trace 檔）。"""
    dst.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for name in COPY_FILES:
        s = src / name
        if s.exists():
            shutil.copy2(s, dst / name)
            copied.append(name)
    return copied


def append_jsonl(path: Path, records: Sequence[dict]) -> None:
    """append-only 追加 JSONL 行（與 ``elevation_adapter._append_jsonl`` 同語意）。

    差異：這個版本**寫失敗就 raise**（CLI 的退出碼必須誠實），不做失敗隔離。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def shared_inode_files(sandbox: Path, source: Path) -> List[str]:
    """沙盒與來源**共用同一個 inode** 的檔名清單（硬連結／同一檔案）。

    🔴 為什麼需要這道閘門（2026-10-20 實測事故）：
    沙盒化的前提是「來源唯讀、寫入只落在沙盒」。若沙盒的檔案與來源**同一個 inode**
    （例如複製實作被換成硬連結），則「對沙盒 append」**物理上就是對來源 append**
    ⇒ 沙盒資料直接流回生產。

    2026-10-20 的實際事故：變異實驗（把 ``copy_graph`` 換成硬連結）讓 CLI 把
    **生產** ``elevation_nodes.jsonl``／``elevation_lifecycle_events.jsonl``
    硬連結進暫存目錄，CLI 的 append 於是寫進生產（500 筆假生命週期事件 ＋ 1 顆
    合成節點）。⇒ 這道閘門把「寫入目標必須與來源不同 inode」變成**結構性不可能**，
    而不是靠「複製有沒有正確」這種會漂移的假設。
    """
    shared: List[str] = []
    for name in COPY_FILES:
        src_file = source / name
        dst_file = sandbox / name
        try:
            if src_file.exists() and dst_file.exists() and os.path.samefile(
                str(src_file), str(dst_file)
            ):
                shared.append(name)
        except OSError:
            continue
    return shared


# ── 快進（虛擬時鐘）───────────────────────────────────────────────────────
def by_state(sandbox: Path) -> Dict[str, int]:
    """沙盒圖譜的 lifecycle_state 分布（已套 lifecycle overlay）。"""
    counter: Counter = Counter()
    for node in load_elevation_nodes(sandbox):
        if isinstance(node, dict):
            counter[str(node.get("lifecycle_state") or "active")] += 1
    return dict(sorted(counter.items()))


def fast_forward(
    sandbox: Path, sim_now: datetime, max_passes: int = MAX_FAST_FORWARD_PASSES
) -> List[Dict[str, Any]]:
    """把沙盒圖譜快進到 ``sim_now``（每輪一級，直到冪等 no-op）。

    迴圈條件：**回傳空清單**＝本輪沒有任何轉變＝已到固定點（no-op，不是失敗）。
    """
    sim_now_iso = sim_now.isoformat()
    passes: List[Dict[str, Any]] = []
    for index in range(1, max_passes + 1):
        events = evaluate_lifecycle_all(store_dir=str(sandbox), now_ts=sim_now_iso)
        passes.append({
            "pass": index,
            "events": len(events),
            "by_state": by_state(sandbox),
        })
        if not events:
            break
    return passes


# ── 矛盾注入（補 GO 判準 ②：池中 superseded ≥ 1）────────────────────────
def select_supersede_target(
    nodes: Sequence[dict], quarantined_ids: Optional[set] = None
) -> Optional[Dict[str, Any]]:
    """挑 1 顆可取代的 ``value`` 節點：非隔離 ∧ 狀態為 **dormant**。

    守門走 ``elevation_recall._is_recallable_node``（**共用管線**，含隔離守門），
    不另寫一份過濾邏輯。挑不到回 ``None``（呼叫端報 FAIL 並中止，**不許降級**）。

    🔴 為什麼狀態只認 ``dormant``（**不**認 ``superseded``）：
    ``superseded`` 是**終態**——引擎對已 superseded 的節點會直接
    ``ValueError: already SUPERSEDED (terminal)``。回憶池裡本來就會有
    superseded 節點（那是判準 ② 的量測對象），若拿它當「下一次取代的目標」，
    注入必然被引擎拒絕。⇒ **量測對象（池含 superseded）與注入目標（dormant）
    是兩件事，不可混用。**
    """
    from src.inner_life.elevation_recall import _is_recallable_node

    states = {"dormant"}
    candidates: List[Dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("node_type") != SUPERSEDE_TARGET_NODE_TYPE:
            continue
        agent_id = node.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            continue
        if not _is_recallable_node(node, agent_id, states, quarantined_ids):
            continue
        candidates.append(node)
    if not candidates:
        return None
    # deterministic：created_ts asc ＋ node_id asc（與既有排序語意同款）。
    candidates.sort(key=lambda n: (str(n.get("created_ts") or ""), str(n.get("node_id"))))
    return candidates[0]


def contradiction_sources(group: int) -> List[str]:
    """第 ``group`` 組合成矛盾的 ``source_id``（3 個**不同** id ⇒ 3 份獨立證據）。

    第 0 組沿用工單 §3.1 的字面值（``sim_contr_0/1/2``）；第 1 組起加前綴避免撞名。
    """
    if group == 0:
        return ["sim_contr_0", "sim_contr_1", "sim_contr_2"]
    return ["sim_contr_g%d_%d" % (group, i) for i in range(N_SUPERSEDE)]


def contradiction_timestamps(sim_now: datetime) -> Dict[str, str]:
    """把 3 份矛盾分配到 **2 個不同模擬日**（D ×2、 D+1 ×1）。

    跨日是 ``LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`` 的硬門檻：同一天連打三條不算
    獨立觀察（防單日雜訊）。
    """
    day_d = sim_now.isoformat()
    day_d_plus_1 = (sim_now + timedelta(days=1)).isoformat()
    sources = contradiction_sources(0)
    return {
        sources[0]: day_d,
        sources[1]: day_d,
        sources[2]: day_d_plus_1,
    }


def normalize_contradiction_pressure(nodes: Sequence[dict]) -> List[dict]:
    """把節點 dict 的 ``contradiction_pressure`` 轉成引擎要的 ``tuple[ContradictionRecord]``。

    **為什麼需要這一層**：節點檔是 JSONL ⇒ ``contradiction_pressure`` 落地是 **list**；
    但引擎的 ``ElevationNode.contradiction_pressure`` 預設是 **tuple**，而
    ``record_contradiction`` 會做 ``node.contradiction_pressure + (record,)``
    ⇒ list + tuple 直接 ``TypeError``。

    這是**既有落盤面與引擎面之間的型別落差**（引擎的 ``supersede`` 通道在生產從未被
    走過，所以這個落差一直沒被踩到）。本函式只做**讀側正規化**：把 JSON 物件還原成
    引擎的資料類別實例，**不改任何節點內容**，也不寫回檔案。
    """
    out: List[dict] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw = node.get("contradiction_pressure")
        if not isinstance(raw, list):
            out.append(node)
            continue
        records = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            records.append(ContradictionRecord(
                source_id=str(item.get("source_id") or ""),
                ts=str(item.get("ts") or ""),
                event_identity=item.get("event_identity"),
                provenance_ref=item.get("provenance_ref"),
            ))
        merged = dict(node)
        merged["contradiction_pressure"] = tuple(records)
        out.append(merged)
    return out


def inject_supersede(
    sandbox: Path,
    nodes: Sequence[dict],
    edges: Sequence[dict],
    target: Dict[str, Any],
    *,
    group: int,
    sim_now: datetime,
) -> Dict[str, Any]:
    """用**真實引擎**產生 1 顆 superseded（記矛盾 → ``engine.supersede()``）。

    - 引擎門檻是真的（``LIFECYCLE_N_SUPERSEDE`` / ``..._MIN_DAYS_SPREAD``），
      不足就會 raise ``ValueError`` ⇒ 呼叫端必須讓它炸出來（不許吞掉）。
    - 只挑 ``value`` 型 ⇒ 不會踩到 essence 的 valence 反轉 / confidence delta 分支。
    """
    node_id = str(target["node_id"])
    agent_id = str(target["agent_id"])
    engine = _rebuild_engine_for(
        sandbox,
        normalize_contradiction_pressure(nodes),
        list(edges),
        agent_id=agent_id,
        llm=None,
    )

    sources = contradiction_sources(group)
    ts_by_source = contradiction_timestamps(sim_now)
    for sid in sources:
        engine.record_contradiction(node_id, source_id=sid, ts=ts_by_source[sid])

    old_content = target.get("content") if isinstance(target.get("content"), str) else ""
    old_confidence = target.get("confidence")
    try:
        old_confidence = float(old_confidence)
    except (TypeError, ValueError):
        old_confidence = 0.5
    new_confidence = min(1.0, max(0.0, old_confidence + 0.05))
    valence = target.get("valence")
    if valence not in ("positive", "negative", "neutral"):
        valence = "neutral"

    state_before = str(target.get("lifecycle_state") or "active")
    new_node = engine.supersede(
        node_id,
        new_content="不再把「%s」當作我現在在意的價值" % old_content,
        new_confidence=new_confidence,
        valence=valence,
        source_ids=list(sources),
        ts=sim_now.isoformat(),
        inner_life_event_id="sim_accel_supersede_g%d" % group,
    )
    return {
        "engine": engine,
        "new_node": new_node,
        "old_node": engine.get_node(node_id),
        "state_before": state_before,
        "source_ids": sources,
        "ts_by_source": ts_by_source,
    }


def persist_supersede(
    sandbox: Path,
    result: Dict[str, Any],
    *,
    sim_now: datetime,
) -> Dict[str, Any]:
    """把取代結果 append-only 落到沙盒（節點檔 ＋ 生命週期 sidecar）。

    - 新節點：append 一行到沙盒 ``elevation_nodes.jsonl``（欄位沿用引擎既有欄位族）。
    - 舊節點：append 一筆 sidecar 事件（**欄位與 ``evaluate_lifecycle_all`` 完全一致**）。
      🔴 ``lifecycle_state`` 是 overlay ``_reduce_events`` 唯一辨識的 key，少它則事件
      被整個忽略、狀態永遠不生效；``superseded_by`` 一併帶上（供 lineage 降維查詢）。
    """
    engine = result["engine"]
    new_node = result["new_node"]
    old_node = result["old_node"]

    node_dict = dataclasses.asdict(new_node)
    append_jsonl(sandbox / NODES_FILENAME, [node_dict])

    anchor_fn = getattr(engine, "_lifecycle_anchor", None)
    anchor_ts = None
    if callable(anchor_fn):
        try:
            anchor_ts = anchor_fn(old_node, sim_now.isoformat())
        except Exception:  # noqa: BLE001 — 錨點取不到不影響狀態記錄
            anchor_ts = None
    if anchor_ts is None:
        anchor_ts = old_node.last_support_ts

    event = {
        "ts": sim_now.isoformat(),
        "node_id": old_node.node_id,
        "event": "lifecycle_transition",
        "lifecycle_state": "superseded",
        "lifecycle_state_before": result["state_before"],
        "lifecycle_state_after": "superseded",
        "anchor_ts": anchor_ts,
        "reason": "contradiction_supersede",
        "agent_id": old_node.agent_id,
        "node_type": old_node.node_type,
        "superseded_by": new_node.node_id,
    }
    append_jsonl(sandbox / LIFECYCLE_EVENTS_FILENAME, [event])
    return {"node": node_dict, "event": event}


# ── 量測（用同一套純函式，不另寫一份算法）───────────────────────────────
def measure(
    sandbox: Path,
    *,
    conversations_dir: Path,
    probes_limit: int = DEFAULT_PROBES,
) -> Dict[str, Any]:
    """在沙盒上算四項 GO 判準（``pool_summary`` / ``hit_rate`` / ``go_no_go``）。"""
    nodes = load_elevation_nodes(sandbox)
    quarantined = load_quarantined_node_ids(sandbox)
    summary = recall_readiness.pool_summary(nodes, quarantined)
    probes = collect_probe_messages(probes_limit, conversations_dir=conversations_dir)
    hit = recall_readiness.hit_rate(nodes, probes, quarantined_ids=quarantined)
    verdict = recall_readiness.go_no_go(summary, hit)
    return {"summary": summary, "hit": hit, "verdict": verdict, "probes_total": len(probes)}


# ── 報告 ──────────────────────────────────────────────────────────────────
def render_report(result: Dict[str, Any]) -> str:
    """繁中報告（寫到 ``%TEMP%``；逐項寫明分子／分母／單位）。"""
    summary = result["summary"]
    hit = result["hit"]
    verdict = result["verdict"]
    lines: List[str] = []
    add = lines.append

    add("# 虛擬時鐘快進沙盒 ＋ 方向 A 第二階段 GO 判準報告")
    add("")
    add("- 模式：`sandbox-only`（來源唯讀；唯一寫入目標＝自建 temp 沙盒）")
    add("- 來源（唯讀）：`%s`" % result["source_dir"])
    add("- 沙盒：`%s`" % result["sandbox_dir"])
    add("- 快進天數：%d 天（模擬時刻 `%s`）" % (result["days"], result["sim_now"]))
    add("")
    add("## 1. 快進輪次（每輪一級；空清單＝冪等 no-op）")
    add("")
    add("| 輪次 | 轉變筆數 | 轉變後 lifecycle_state 分布 |")
    add("|---|---|---|")
    for item in result["fast_forward_passes"]:
        add("| pass%d | %d | %s |" % (item["pass"], item["events"], item["by_state"]))
    add("")
    add("## 2. 矛盾注入（補判準 ②）")
    add("")
    for item in result["supersede_records"]:
        add("- 目標節點：`%s`（%s / %s）" % (item["old_node_id"], item["node_type"], item["agent_id"]))
        add("  - 矛盾來源：%s" % item["source_ids"])
        add("  - 矛盾模擬日：%s" % item["ts_by_source"])
        add("  - lineage：舊 `%s` → 新 `%s`（parent=`%s`，lineage_depth=%s，lineage_path=`%s`）"
            % (item["old_node_id"], item["new_node_id"], item["parent_node_id"],
               item["lineage_depth"], item["lineage_path"]))
    add("")
    add("## 3. 回憶池成熟度（單位：顆）")
    add("")
    add("- 節點總數：%d 顆" % summary["total_nodes"])
    add("- lifecycle_state 分布：%s" % summary["by_state"])
    add("- node_type 分布：%s" % summary["by_node_type"])
    add("- 隔離節點：%d 顆" % summary["quarantined"])
    add("- pattern（結構性排除）：%d 顆" % summary["excluded_pattern"])
    add("- 被隔離守門擋下（否則可回憶）：%d 顆" % summary["blocked_by_quarantine"])
    add("- **可回憶池（非隔離 × 四層 × dormant|superseded）：%d 顆**" % summary["recallable"])
    add("- 池內態分布：%s" % summary["recallable_by_state"])
    add("- 池內型別分布（分子＝顆，分母＝池總數 %d）：%s"
        % (summary["recallable"], summary["recallable_by_node_type"]))
    add("")
    add("## 4. 命中率（單位：probe ＝ 一筆真實 user 訊息）")
    add("")
    add("- 詞元級 union：**%d / %d = %.4f**（%.2f%%）"
        % (hit["probes_hit"], hit["probes_total"], hit["hit_rate"], hit["hit_rate"] * 100))
    add("- 命中的 unique 節點：%d 顆" % hit["unique_nodes_hit"])
    add("- 命中配對：%d 組；其中隔離節點：%d 組；非隔離命中比例：%.4f"
        % (hit["match_pairs"], hit["match_pairs_quarantined"], hit["non_quarantined_hit_ratio"]))
    add("- tags 全無效的 probe：%d 筆" % hit["probes_without_tags"])
    add("")
    add("## 5. 第二階段接線判準（四項全過才 GO）")
    add("")
    add("- **判定：%s**" % verdict["verdict"])
    add("")
    add("| 條件 | 值 | 門檻 | 單位 | 結果 |")
    add("|---|---|---|---|---|")
    for key, check in verdict["checks"].items():
        add("| %s | %s | %s | %s | %s |"
            % (key, check.get("value"), check.get("threshold"), check.get("unit", "-"),
               "PASS" if check.get("pass") else "FAIL"))
    if verdict["reasons"]:
        add("")
        add("### 未過原因")
        add("")
        for reason in verdict["reasons"]:
            add("- %s" % reason)
    add("")
    add("## 6. 生產檔位元組對帳（sha256）")
    add("")
    add("| 檔案 | before | after | 相同 |")
    add("|---|---|---|---|")
    for name in COPY_FILES:
        before = result["production_sha256_before"].get(name, "")
        after = result["production_sha256_after"].get(name, "")
        add("| %s | `%s` | `%s` | %s |"
            % (name, before[:16], after[:16], result["production_unchanged"].get(name)))
    add("")
    add("- 生產三檔全部逐位元相同：**%s**" % result["all_production_unchanged"])
    add("")
    return "\n".join(lines) + "\n"


# ── 主流程 ────────────────────────────────────────────────────────────────
def _resolve_sandbox_dir(out_dir: Optional[str]) -> Optional[Path]:
    """解析沙盒目錄；**必須**在 ``tempfile.gettempdir()`` 之下，否則回 ``None``。"""
    temp_root = Path(tempfile.gettempdir()).resolve()
    if out_dir:
        sandbox = Path(out_dir).resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sandbox = (temp_root / "sim_accel" / ("run_" + stamp)).resolve()
    if temp_root not in sandbox.resolve().parents:
        return None
    return sandbox


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="sim_accel_recall_cli",
        description="虛擬時鐘快進沙盒 + 矛盾注入 + 方向 A 第二階段 GO 判準量測"
                    "（唯讀來源 + 自建沙盒；永不寫入來源）",
    )
    ap.add_argument("--source-dir", default=None,
                    help="圖譜來源目錄（**唯讀**）。預設 = repo 內 data/elevation/")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help="快進天數（預設 %d）" % DEFAULT_DAYS)
    ap.add_argument("--out-dir", default=None,
                    help="沙盒輸出目錄（預設 %%TEMP%%\\sim_accel\\run_<UTC時間戳>）")
    ap.add_argument("--contradictions", type=int, default=DEFAULT_CONTRADICTIONS,
                    help="合成矛盾組數（預設 %d；每組 1 顆 superseded）" % DEFAULT_CONTRADICTIONS)
    ap.add_argument("--probes", type=int, default=DEFAULT_PROBES,
                    help="命中率探針數（單位：probe；預設 %d）" % DEFAULT_PROBES)
    ap.add_argument("--report", default=None,
                    help="完整 UTF-8 報告輸出路徑（預設寫到 %%TEMP%%）")
    ap.add_argument("--json", action="store_true", help="以 JSON 輸出到 stdout")
    args = ap.parse_args(argv)

    source = Path(args.source_dir) if args.source_dir else repo_store_dir()
    if not source.exists():
        print("[refuse] 來源目錄不存在：%s" % source, file=sys.stderr)
        return 2
    source = source.resolve()

    sandbox = _resolve_sandbox_dir(args.out_dir)
    if sandbox is None:
        print("[refuse] 沙盒必須位於 %%TEMP%% 之下（拒絕寫入：%s）" % args.out_dir,
              file=sys.stderr)
        return 3
    if sandbox.resolve() == source:
        print("[refuse] 沙盒不得等於來源目錄（%s）" % source, file=sys.stderr)
        return 3
    sandbox.mkdir(parents=True, exist_ok=True)

    before = {name: sha256_of(source / name) for name in COPY_FILES}
    copied = copy_graph(source, sandbox)

    # 🔴 結構性閘門：沙盒檔案與來源**不得**共用 inode（見 shared_inode_files 的說明）。
    #    這是 2026-10-20 事故的直接教訓：寫入目標必須與來源**物理上**是不同檔案。
    shared = shared_inode_files(sandbox, source)
    if shared:
        print("[refuse] 沙盒與來源共用同一個 inode（%s）—— 寫沙盒等於寫來源，拒絕執行"
              % ", ".join(shared), file=sys.stderr)
        return 3

    sim_now = datetime.now(timezone.utc) + timedelta(days=int(args.days))

    # ── 步驟 2：快進 ──
    passes = fast_forward(sandbox, sim_now)

    # ── 步驟 3：矛盾注入 ──
    supersede_records: List[Dict[str, Any]] = []
    groups = max(0, int(args.contradictions))
    for group in range(groups):
        nodes = load_elevation_nodes(sandbox)
        quarantined = load_quarantined_node_ids(sandbox)
        edges = _load_edges(sandbox)
        target = select_supersede_target(nodes, quarantined)
        if target is None:
            print("[abort] 沙盒池內找不到可取代的 %s 型節點（非隔離 ∧ dormant|superseded）"
                  % SUPERSEDE_TARGET_NODE_TYPE, file=sys.stderr)
            return 4
        try:
            result = inject_supersede(
                sandbox, nodes, edges, target, group=group, sim_now=sim_now
            )
        except ValueError as exc:
            print("[abort] 矛盾注入被引擎門檻拒絕（組 %d）：%s" % (group, exc), file=sys.stderr)
            return 4
        persisted = persist_supersede(sandbox, result, sim_now=sim_now)
        new_node = persisted["node"]
        supersede_records.append({
            "old_node_id": str(target["node_id"]),
            "new_node_id": str(new_node["node_id"]),
            "node_type": str(target.get("node_type")),
            "agent_id": str(target.get("agent_id")),
            "parent_node_id": new_node.get("parent_node_id"),
            "lineage_depth": new_node.get("lineage_depth"),
            "lineage_path": new_node.get("lineage_path"),
            "source_ids": result["source_ids"],
            "ts_by_source": result["ts_by_source"],
        })

    # ── 步驟 4：量測 ──
    measured = measure(
        sandbox,
        conversations_dir=repo_conversations_dir(),
        probes_limit=int(args.probes),
    )

    after = {name: sha256_of(source / name) for name in COPY_FILES}
    unchanged = {name: before[name] == after[name] for name in COPY_FILES}
    all_unchanged = all(unchanged.values())

    result = {
        "mode": "sandbox-only",
        "source_dir": str(source),
        "sandbox_dir": str(sandbox),
        "copied_files": copied,
        "days": int(args.days),
        "sim_now": sim_now.isoformat(),
        "fast_forward_passes": passes,
        "supersede_records": supersede_records,
        "summary": measured["summary"],
        "hit": measured["hit"],
        "verdict": measured["verdict"],
        "production_sha256_before": before,
        "production_sha256_after": after,
        "production_unchanged": unchanged,
        "all_production_unchanged": all_unchanged,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        lines = [
            "=" * 74,
            "SIM ACCEL RECALL (CLI) — mode=sandbox-only",
            "=" * 74,
            "source_dir (唯讀)     = %s" % source,
            "sandbox_dir (唯一寫入) = %s" % sandbox,
            "sim_now                = %s" % sim_now.isoformat(),
            "",
            "-- 快進輪次（空清單＝冪等 no-op）--",
        ]
        for item in passes:
            lines.append("  pass%d: events=%d by_state=%s"
                         % (item["pass"], item["events"], item["by_state"]))
        lines += [
            "",
            "-- 矛盾注入 --",
        ]
        for item in supersede_records:
            lines.append("  %s (%s) → %s  parent=%s depth=%s"
                         % (item["old_node_id"], item["node_type"], item["new_node_id"],
                            item["parent_node_id"], item["lineage_depth"]))
        lines += [
            "",
            "-- 回憶池（單位：顆）--",
            "recallable            = %d" % measured["summary"]["recallable"],
            "recallable_by_state   = %s" % measured["summary"]["recallable_by_state"],
            "recallable_by_type    = %s" % measured["summary"]["recallable_by_node_type"],
            "",
            "-- 命中率（單位：probe）--",
            "hit_rate              = %d/%d = %.4f（unique 命中 %d 顆）"
            % (measured["hit"]["probes_hit"], measured["hit"]["probes_total"],
               measured["hit"]["hit_rate"], measured["hit"]["unique_nodes_hit"]),
            "quarantined pairs     = %d  非隔離命中比例 = %.4f"
            % (measured["hit"]["match_pairs_quarantined"],
               measured["hit"]["non_quarantined_hit_ratio"]),
            "",
            "-- 第二階段接線判準 --",
            "VERDICT: %s" % measured["verdict"]["verdict"],
        ]
        for reason in measured["verdict"]["reasons"]:
            lines.append("  - %s" % reason)
        lines.append("")
        lines.append("production unchanged   = %s" % all_unchanged)
        print("\n".join(lines).encode("ascii", "backslashreplace").decode("ascii"))

    report_path = Path(args.report) if args.report else (
        Path(os.environ.get("TEMP", ".")) / "sim_accel_recall_report.md"
    )
    report_path.write_text(render_report(result), encoding="utf-8")
    print("[report] %s" % report_path)

    # 🔴 生產位元組對帳：任一不同 ⇒ 中止並非零退出（不是警告）。
    if not all_unchanged:
        print("[abort] 生產檔位元組被改動：%s" % unchanged, file=sys.stderr)
        return 5

    # 退出碼：GO → 0；NO-GO → 1（**設計行為，不是崩潰**）。
    return 0 if measured["verdict"]["verdict"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
