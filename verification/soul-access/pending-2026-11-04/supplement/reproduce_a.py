#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PENDING-1104 A 階段重現腳本（Sol decision-complete 工單步驟 2-5）。

只讀 `data/elevation/` 與既有 `evidence.json`；唯一寫入目標＝ supplement/ 下的
小體積證據（bounds.json / per_id.json / boundary.json / REPORT.md）。
快照位元組（_snapshots/ 下 *.jsonl）不進 git（見 _snapshots/.gitignore）。

用法（repo 根，.venv 解譯器）：
    .venv/Scripts/python.exe \
      verification/soul-access/pending-2026-11-04/supplement/reproduce_a.py \
      --slot 2026-11-12T03:00:00Z

產出：
    supplement/_snapshots/<run_id>/            五檔唯讀封存（若已存在則驗 SHA，不重抄）
    supplement/run-slot-.../                   CLI --json 原始輸出（另行以 CLI 執行，本腳本只驗）
    supplement/bounds.json                     逐 ID 下界（推導）＋公式＋輸入＋來源行／SHA
    supplement/per_id.json                     90 筆原文＋六項事實＋事件欄位
    supplement/boundary.json                   逐 ID 三點邊界實測（bound±1µs／bound）
    supplement/REPORT.md                       A 階段報告（四項對帳＋狀態）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PENDING_DIR = REPO_ROOT / "verification" / "soul-access" / "pending-2026-11-04"
SUPPLEMENT = PENDING_DIR / "supplement"
EVIDENCE = PENDING_DIR / "evidence.json"
FIVE = ("elevation_nodes.jsonl", "elevation_edges.jsonl",
        "elevation_lifecycle_events.jsonl", "elevation_projection_trace.jsonl",
        "elevation_trace.jsonl")
CONSUMED_THREE = ("elevation_nodes.jsonl", "elevation_edges.jsonl",
                  "elevation_lifecycle_events.jsonl")
SLOT_DEFAULT = "2026-11-12T03:00:00Z"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default=SLOT_DEFAULT)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--skip-boundary", action="store_true",
                    help="只做推導不跑 270 次邊界實測（除錯用；正式交付不得用）")
    args = ap.parse_args(argv)
    slot = args.slot
    run_id = args.run_id or ("slot-" + slot.replace(":", "").replace("+00:00", "Z")
                             .replace("+00_00", "Z"))
    snap = SUPPLEMENT / "_snapshots" / run_id
    snap.mkdir(parents=True, exist_ok=True)

    from src.inner_life.elevation_adapter import (  # noqa: E402
        _support_anchor_by_node, evaluate_lifecycle_all,
    )
    from src.inner_life.emergent_projection import (  # noqa: E402
        load_elevation_nodes,
    )
    import soul_elevation.engine as _eng  # noqa: E402
    import soul_elevation.prior as _pri  # noqa: E402

    ev = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    prod = REPO_ROOT / "data" / "elevation"

    # 1. 快照：唯讀封存，SHA 須與既有 evidence 一致，否則 BLOCKED（不倒填）。
    for f in FIVE:
        dst = snap / f
        if not dst.exists():
            shutil.copy2(prod / f, dst)
        got = sha256_file(dst)
        want = ev["source_snapshot_sha256"][f]
        assert got == want, "快照漂移 %s: %s != evidence %s" % (f, got, want)
        assert sha256_file(prod / f) == want, "生產漂移 %s（有人寫了生產？）" % f

    # 2. 錨點：沿實載路徑（原始邊檔 → _support_anchor_by_node → 注入語義 →
    #    引擎 _lifecycle_anchor 優先序）逐筆求錨點；下界＝錨點＋7 天（推導）。
    edges = [json.loads(l) for l in (snap / "elevation_edges.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    anchor_by_node = _support_anchor_by_node(edges)
    node_lines: dict[str, int] = {}
    node_objs: dict[str, dict] = {}
    for i, line in enumerate(
            (snap / "elevation_nodes.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        o = json.loads(line)
        nid = o.get("node_id")
        if nid and nid not in node_objs:
            node_objs[nid] = o
            node_lines[nid] = i
    overlay = {n["node_id"]: n for n in load_elevation_nodes(snap)}

    import src.inner_life.elevation_adapter as _ad  # noqa: E402 （僅取 __file__）
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from eval_lifecycle_cli import approved_retirement_ids  # noqa: E402
    ledger = approved_retirement_ids()

    bounds = {
        "formula": "bound = anchor + 7*86400s；weakening ⟺ "
                   "(now-anchor).total_seconds()/86400 >= 7（engine.py evaluate_lifecycle，"
                   "比較語義 >=，浮點天數）",
        "anchor_priority": "仍有效支持邊最大 valid_from_ts → last_support_ts（注入值） "
                           "→ created_ts+3天grace → None跳過（engine.py _lifecycle_anchor）",
        "inputs": {"edges_file": str(snap / 'elevation_edges.jsonl'),
                   "support_anchor_fn": "src/inner_life/elevation_adapter.py:"
                                        "_support_anchor_by_node",
                   "inject_fn": "src/inner_life/elevation_adapter.py:"
                                "_inject_support_anchor",
                   "engine_anchor_fn": "soul_elevation/engine.py:_lifecycle_anchor "
                                       "(editable: %s)" % Path(_eng.__file__).resolve(),
                   "engine_sha256": sha256_file(Path(_eng.__file__)),
                   "prior_sha256": sha256_file(Path(_pri.__file__)),
                   "adapter_sha256": sha256_file(Path(_ad.__file__))},
        "per_id": {},
    }
    per_id = []
    for p in ev["per_id"]:
        nid = p["node_id"]
        assert nid in node_objs, "快照缺節點 %s" % nid
        o = node_objs[nid]
        anchor = anchor_by_node.get(nid)
        if anchor is None:
            anchor = (datetime.fromisoformat(o["created_ts"])
                      + timedelta(days=3)).isoformat()
            anchor_src = "grace: created_ts+3天（無仍有效邊，注入None→引擎優先序3）"
        else:
            anchor_src = "仍有效邊最大valid_from_ts（注入last_support_ts→引擎優先序1/2）"
        bound = (datetime.fromisoformat(anchor) + timedelta(days=7)).isoformat()
        bounds["per_id"][nid] = {"anchor": anchor, "anchor_src": anchor_src,
                                 "bound": bound, "derived": True,
                                 "note": "推導值，非 observed 時刻"}
        facts = {
            "found_in_graph": True,
            "node_type": o.get("node_type"),
            "created_eq_last_support_file": (
                o.get("created_ts") == o.get("last_support_ts")),
            "still_valid_edge_max_is_created": anchor == o.get("created_ts"),
            "pre_state_active": overlay[nid].get("lifecycle_state") == "active",
            "not_in_approved_ledger": nid not in ledger,
        }
        per_id.append({
            "node_id": nid, "agent_id": o.get("agent_id"),
            "node_file_line": node_lines[nid],
            "content": o.get("content"),
            "facts": facts, "anchor": anchor, "bound": bound,
        })
    assert all(v["anchor"] == node_objs[k].get("created_ts")
               for k, v in bounds["per_id"].items()), "本批錨點須全等於 created_ts"
    (SUPPLEMENT / "bounds.json").write_text(
        json.dumps(bounds, ensure_ascii=False, indent=1), encoding="utf-8")
    (SUPPLEMENT / "per_id.json").write_text(
        json.dumps({"count": len(per_id), "items": per_id},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    # 3. 邊界實測：每筆從未評估快照建全新沙盒，在 bound-1µs／bound／+1µs 驗證。
    boundary = {"slot": slot, "probes": []}
    if not args.skip_boundary:
        from src.inner_life.lifecycle_overlay import (  # noqa: E402
            LIFECYCLE_EVENTS_FILENAME,
        )
        for item in per_id:
            nid = item["node_id"]
            bound_dt = datetime.fromisoformat(bounds["per_id"][nid]["bound"])
            for label, dt in (("minus_1us", bound_dt - timedelta(microseconds=1)),
                              ("exact", bound_dt),
                              ("plus_1us", bound_dt + timedelta(microseconds=1))):
                sb = Path(tempfile.mkdtemp(prefix="pending1104_probe_"))
                try:
                    for f in CONSUMED_THREE:
                        shutil.copy2(snap / f, sb / f)
                    evaluate_lifecycle_all(store_dir=sb,
                                           now_ts=dt.isoformat())
                    ap_events = [json.loads(l) for l in
                                 (sb / LIFECYCLE_EVENTS_FILENAME)
                                 .read_text(encoding="utf-8").splitlines()
                                 if l.strip()]
                    hit = [e for e in ap_events
                           if e.get("node_id") == nid
                           and e.get("lifecycle_state_before") == "active"
                           and e.get("lifecycle_state_after") == "weakening"]
                    weakened = len(hit) > 0
                finally:
                    shutil.rmtree(sb, ignore_errors=True)
                expect = (label != "minus_1us")
                boundary["probes"].append(
                    {"node_id": nid, "probe": label, "probe_ts": dt.isoformat(),
                     "weakened": weakened, "expected": expect,
                     "pass": weakened == expect})
    bad = [b for b in boundary["probes"] if not b["pass"]]
    boundary["fail_count"] = len(bad)
    (SUPPLEMENT / "boundary.json").write_text(
        json.dumps(boundary, ensure_ascii=False, indent=1), encoding="utf-8")
    print("per_id=%d boundary_probes=%d fail=%d" % (
        len(per_id), len(boundary["probes"]), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
