# -*- coding: utf-8 -*-
"""memory_baseline_observation.py — Memory Runtime Baseline Observation（唯讀）

Owner 裁決 2026-10-07：**只量測現況，不修任何東西。**

這個工單的目的不是找紅點，是建立「之後能夠比較的事實」。因此本工具：

- **不判定好壞**。不輸出 pass/fail、不定義「什麼東西本來就值得留下」。
  寫了 70% 與寫了 40% 在這裡都只是**數字**，不是好與壞。
- **「漏寫」不等於 failure**。沒有先定義應寫集合，就沒有漏寫率可言。
- 只描述「什麼種類的 lived experience 出現時，runtime 實際做了什麼」。

管線五階段（逐層問，不預設是哪一層出問題）
============================================
    Event
      → 是否形成 InnerLifeEvent          （產生階段）
      → 是否進入 memory submission         （submission gate）
      → 是否真的寫入 Palace / diary        （寫入階段）
      → 未來是否能被取回                  （retrieval）
      → 取回後是否影響後續行為             （interpretation）

安全邊界
========
1. **唯讀**。所有 data/ 檔案一律以 'r' 開啟。本工具不寫入 data/ 任何路徑，
   並在啟動時主動拒絕任何落在 data/ 底下的輸出路徑。
2. **不啟動服務、不綁 port、不發真實請求、不殺任何行程。**
3. **不改 Soul、不改 Memory / SAGE contract、不改 gate / threshold。**
4. **不為了增加樣本而改變 runtime 行為。**

執行
====
    .venv\\Scripts\\python.exe harness\\memory_baseline_observation.py

產出（預設 tests/results/memory_baseline/）
    baseline.json  機器可讀
    BASELINE.md   人可讀
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data"
DEFAULT_OUT = "tests/results/memory_baseline"

# 大量掃描用：從原始行抽欄位，避免對數 GB 檔案做完整 json.loads
RE_TRIGGER = re.compile(rb'"trigger_type"\s*:\s*"([^"]*)"')
RE_SOURCE = re.compile(rb'"source_system"\s*:\s*"([^"]*)"')
RE_ACTOR = re.compile(rb'"actor_id"\s*:\s*(null|"([^"]*)")')
RE_EVENT_ID = re.compile(rb'"event_id"\s*:\s*"([^"]*)"')
RE_TS = re.compile(rb'"ts"\s*:\s*"([^"]*)"')
RE_EV_TYPE = re.compile(rb'"event_type"\s*:\s*"([^"]*)"')
RE_NODE_TYPE = re.compile(rb'"node_type"\s*:\s*"([^"]*)"')
RE_SOURCE_ID = re.compile(rb'"source_id"\s*:\s*"([^"]*)"')


def _std_out():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def assert_output_not_in_data(out_dir: Path):
    """輸出路徑不得落在 production data/ 底下（鐵律 #8）。"""
    data_dir = DATA.resolve()
    resolved = out_dir.resolve()
    if resolved == data_dir or data_dir in resolved.parents:
        raise SystemExit(
            f"[ABORT] 輸出路徑 {resolved} 位於 production data/ 底下。"
            " 本工單是唯讀觀測，不得寫入 data/。"
        )


def iter_lines(path: Path):
    """逐行讀取（容錯），唯讀。"""
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def day_of(ts):
    d = parse_ts(ts)
    return d.date().isoformat() if d else None


# ─────────────────────────────────────────────────────────────────────────────
# 階段 3：寫入階段（diary）
# ─────────────────────────────────────────────────────────────────────────────

def read_diary():
    """讀所有 agent 的 diary。回傳 entries 與統計。"""
    entries = []
    per_agent = Counter()
    per_slot = Counter()
    per_day = Counter()
    malformed = 0
    diary_dir = DATA / "soul"
    for agent_dir in sorted(diary_dir.glob("*")) if diary_dir.is_dir() else []:
        d = agent_dir / "diary"
        if not d.is_dir():
            continue
        agent = agent_dir.name
        for path in sorted(d.glob("*.jsonl")):
            for line in iter_lines(path):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                entries.append({
                    "agent": agent,
                    "file": path.name,
                    "ts": rec.get("ts"),
                    "day": day_of(rec.get("ts")),
                    "slot": rec.get("slot"),
                    "content": rec.get("content") or "",
                    "source": rec.get("source"),
                    "inner_life_event_id": rec.get("inner_life_event_id"),
                })
                per_agent[agent] += 1
                per_slot[rec.get("slot") or "(none)"] += 1
                if rec.get("ts"):
                    per_day[day_of(rec.get("ts")) or "(bad-ts)"] += 1
    return {
        "entries": entries,
        "per_agent": dict(per_agent),
        "per_slot": dict(per_slot),
        "per_day": dict(sorted(per_day.items())),
        "malformed_lines": malformed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 階段 1：產生階段（InnerLifeEvent trace）
# ─────────────────────────────────────────────────────────────────────────────

def scan_inner_life(wanted_ids):
    """串流掃描 data/inner_life/trace.jsonl（數 GB，走 regex 快取路徑）。

    `wanted_ids` 是 diary 引用到的 InnerLifeEvent id；只有命中時才做完整
    json.loads 取得該事件的完整內容。
    """
    path = DATA / "inner_life" / "trace.jsonl"
    result = {
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": path.exists(),
        "total_events": 0,
        "by_trigger_type": Counter(),
        "by_source_system": Counter(),
        "by_actor_id": Counter(),
        "per_day": Counter(),
        "first_ts": None,
        "last_ts": None,
        "matched": {},
        "malformed": 0,
    }
    if not path.exists():
        return result

    wanted = {i for i in wanted_ids if i}
    for raw in iter_lines_bytes(path):
        result["total_events"] += 1
        m_t = RE_TRIGGER.search(raw)
        m_s = RE_SOURCE.search(raw)
        m_a = RE_ACTOR.search(raw)
        result["by_trigger_type"][(m_t.group(1).decode("utf-8", "replace")
                                  if m_t else "(none)")] += 1
        result["by_source_system"][(m_s.group(1).decode("utf-8", "replace")
                                    if m_s else "(none)")] += 1
        result["by_actor_id"][(
            (m_a.group(2) or b"null").decode("utf-8", "replace") if m_a else "(none)"
        )] += 1
        m_ts = RE_TS.search(raw)
        if m_ts:
            ts = m_ts.group(1).decode("ascii", "replace")
            d = day_of(ts)
            if d:
                result["per_day"][d] += 1
            if result["first_ts"] is None or ts < result["first_ts"]:
                result["first_ts"] = ts
            if result["last_ts"] is None or ts > result["last_ts"]:
                result["last_ts"] = ts
        if wanted:
            m_id = RE_EVENT_ID.search(raw)
            if m_id:
                eid = m_id.group(1).decode("ascii", "replace")
                if eid in wanted:
                    try:
                        result["matched"][eid] = json.loads(
                            raw.decode("utf-8", "replace")
                        )
                    except json.JSONDecodeError:
                        result["malformed"] += 1
    return result


def iter_lines_bytes(path):
    """二進位逐行讀取，供 regex 快取路徑使用。"""
    with io.open(path, "rb") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line


# ─────────────────────────────────────────────────────────────────────────────
# 階段 4：retrieval（loader_trace）
# ─────────────────────────────────────────────────────────────────────────────

def scan_retrieval():
    path = DATA / "memory" / "loader_trace.jsonl"
    result = {
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": path.exists(),
        "total_calls": 0,
        "by_agent": Counter(),
        "fail_safe_triggered": Counter(),
        "eligible_bucket": Counter(),
        "candidate_bucket": Counter(),
        "with_candidates": 0,
        "with_eligible": 0,
        "steps_seen": Counter(),
    }
    if not path.exists():
        return result
    for line in iter_lines(path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        result["total_calls"] += 1
        result["by_agent"][rec.get("agent_id") or "(none)"] += 1
        result["fail_safe_triggered"][rec.get("fail_safe_triggered") or "(none)"] += 1
        n_c = len(rec.get("candidates") or [])
        n_e = rec.get("eligible_count", 0) or 0
        result["candidate_bucket"][_bucket(n_c)] += 1
        result["eligible_bucket"][_bucket(n_e)] += 1
        if n_c:
            result["with_candidates"] += 1
        if n_e:
            result["with_eligible"] += 1
        for ev in rec.get("events") or []:
            result["steps_seen"][ev.get("step") or "(none)"] += 1
    return result


def _bucket(n):
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 10:
        return "4-10"
    return "11+"


# ─────────────────────────────────────────────────────────────────────────────
# 階段 5：interpretation（shadow_log —— 這回合記憶有沒有真的進到 prompt）
# ─────────────────────────────────────────────────────────────────────────────

def scan_usage():
    path = DATA / "shadow" / "shadow_log.jsonl"
    result = {
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": path.exists(),
        "total_responses": 0,
        "by_agent": Counter(),
        "context_provided": Counter(),
        "context_provided_but_zero_facts": 0,
        "facts_when_context": Counter(),
        "per_day": Counter(),
    }
    if not path.exists():
        return result
    for line in iter_lines(path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        result["total_responses"] += 1
        agent = rec.get("agent_id") or "(none)"
        result["by_agent"][agent] += 1
        cp = bool(rec.get("context_provided"))
        result["context_provided"][str(cp)] += 1
        n_f = 0
        for key in ("v6", "heuristic"):
            block = rec.get(key) or {}
            n_f += block.get("n_facts", 0) or 0
        if cp:
            result["facts_when_context"][_bucket(n_f)] += 1
            if n_f == 0:
                result["context_provided_but_zero_facts"] += 1
        d = day_of(rec.get("timestamp"))
        if d:
            result["per_day"][d] += 1
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 階段 2b：elevation（graph node）
# ─────────────────────────────────────────────────────────────────────────────

def scan_elevation():
    path = DATA / "elevation" / "elevation_trace.jsonl"
    result = {
        "path": str(path.relative_to(REPO_ROOT)),
        "exists": path.exists(),
        "total": 0,
        "by_event_type": Counter(),
        "by_node_type": Counter(),
        "first_ts": None,
        "last_ts": None,
    }
    if not path.exists():
        return result
    for raw in iter_lines_bytes(path):
        result["total"] += 1
        m = RE_EV_TYPE.search(raw)
        result["by_event_type"][m.group(1).decode("utf-8", "replace")
                                 if m else "(none)"] += 1
        m = RE_NODE_TYPE.search(raw)
        if m:
            result["by_node_type"][m.group(1).decode("utf-8", "replace")
                                   if m else "(none)"] += 1
        m = RE_TS.search(raw)
        if m:
            ts = m.group(1).decode("ascii", "replace")
            if result["first_ts"] is None or ts < result["first_ts"]:
                result["first_ts"] = ts
            if result["last_ts"] is None or ts > result["last_ts"]:
                result["last_ts"] = ts
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 漏斗
# ─────────────────────────────────────────────────────────────────────────────

def build_funnel(diary, ile, elevation, retrieval, usage, agent="agent_rem"):
    """把 diary 的 inner_life_event_id 與 InnerLifeEvent 對起來。

    **這裡不判定漏寫率。** 內心事件裡有大量 trigger_type（例如
    world:calendar_event）本來就不該變成 diary 條目。所以只能報「有多少
    diary 條目能追溯到一個 InnerLifeEvent」以及「寫入延遲」，
    不能反推「該寫沒寫的比例」。
    """
    entries = [e for e in diary["entries"] if e["agent"] == agent]
    linked = 0
    unlinked = 0
    latencies = []
    unmatched = []
    for e in entries:
        eid = e.get("inner_life_event_id")
        if not eid:
            unlinked += 1
            continue
        if eid in ile["matched"]:
            linked += 1
            src = ile["matched"][eid].get("ts")
            dst = e.get("ts")
            if src and dst:
                a, b = parse_ts(src), parse_ts(dst)
                if a and b:
                    latencies.append((b - a).total_seconds())
        else:
            unlinked += 1
            unmatched.append(eid)
    latencies.sort()

    def pct(p):
        if not latencies:
            return None
        idx = min(len(latencies) - 1, int(round(p * (len(latencies) - 1))))
        return latencies[idx]

    return {
        "agent": agent,
        "diary_entries": len(entries),
        "linked_to_inner_life_event": linked,
        "not_traceable": unlinked,
        "sample_unmatched_ids": unmatched[:5],
        "write_latency_sec": {
            "n": len(latencies),
            "min": latencies[0] if latencies else None,
            "p50": pct(0.5),
            "p90": pct(0.9),
            "max": latencies[-1] if latencies else None,
        },
        "note": (
            "不可解讀為『漏寫率』：InnerLifeEvent 含大量本來就不該寫入 diary 的 "
            "trigger_type。要算漏寫必須先定義應寫集合，那是下一階段的事。"
        ),
    }


def main(argv=None):
    _std_out()
    ap = argparse.ArgumentParser(description="Memory runtime baseline observation (read-only)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="輸出目錄（不得位於 data/ 底下）")
    ap.add_argument("--agent", default="agent_rem")
    ap.add_argument("--skip-inner-life", action="store_true",
                    help="跳過數 GB 的 inner_life 掃描（會漏掉寫入延遲統計）")
    args = ap.parse_args(argv)

    out_dir = (REPO_ROOT / args.out).resolve()
    assert_output_not_in_data(out_dir)

    print("=" * 72)
    print("Memory Runtime Baseline Observation（唯讀 · 不判定好壞）")
    print(f"  agent : {args.agent}")
    print(f"  output: {out_dir}")
    print("  本工單不改 Soul、不改 Memory/SAGE contract、不改任何 threshold。")

    print("\n[1/5] diary（寫入階段）…")
    diary = read_diary()
    print(f"      entries={len(diary['entries'])} agents={len(diary['per_agent'])}")

    wanted = {e["inner_life_event_id"] for e in diary["entries"]}
    if args.skip_inner_life:
        ile = {"skipped": True, "matched": {}, "total_events": None}
    else:
        print("[2/5] inner_life trace（產生階段，數 GB，請稍候）…")
        ile = scan_inner_life(wanted)
        print(f"      total_events={ile['total_events']} "
              f"matched_diary_refs={len(ile['matched'])}")

    print("[3/5] elevation（graph node）…")
    elevation = scan_elevation()
    print(f"      total={elevation['total']}")

    print("[4/5] loader trace（retrieval）…")
    retrieval = scan_retrieval()
    print(f"      calls={retrieval['total_calls']}")

    print("[5/5] shadow log（interpretation）…")
    usage = scan_usage()
    print(f"      responses={usage['total_responses']}")

    funnel = build_funnel(diary, ile, elevation, retrieval, usage, args.agent)

    payload = {
        "generated_by": "harness/memory_baseline_observation.py",
        "observation_only": True,
        "makes_no_judgement": (
            "本檔不含 pass/fail。沒有先定義『什麼東西本來就值得留下』，"
            "就沒有漏寫率可言；『漏寫』在此不是 failure。"
        ),
        "diary": diary,
        "inner_life": ile,
        "elevation": elevation,
        "retrieval": retrieval,
        "usage": usage,
        "funnel": funnel,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "baseline.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n  baseline.json 已寫入 {out_dir}")
    print("  這是「之後能夠比較的事實」，不是紅點清單。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())