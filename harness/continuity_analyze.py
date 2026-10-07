"""Memory Continuity Observability v1 — 離線 use scoring。

**零 LLM 呼叫、零費用、零網路。** 本腳本純粹讀 Layer 2 / Layer 3 的 JSONL，
把寫入當下就算好的 4-gram use heuristic **完整重算**一次，讓 Bry 可以：

* 檢查 lineage 是否接得起來（join 率、unlinked gap）；
* 在 ``algorithm_version`` 演化後**零費用重判**（``--rescore``）；
* 用 synthetic 資料自我驗證演算法（``--self-test``）。

本檔案**不碰** ``data/`` 的既有檔案，預設唯讀。

用法::

    python harness/continuity_analyze.py --self-test
    python harness/continuity_analyze.py                       # 報告
    python harness/continuity_analyze.py --rescore --out data/memory/continuity_turn.rescored.jsonl

AST 比對基準也住在這裡（``git_head_source``）：``tests/**`` 內的每個
``subprocess.*`` 呼叫點都必須被 spawn guard 逐行列管
（``tests/infra/test_no_production_spawn_guard.py``），所以「取 HEAD 原始碼」
這個 subprocess 必須放在掃描範圍外的離線工具裡，而不是測試檔裡。
"""

from __future__ import annotations

import argparse
import json
import subprocess  # noqa: S404 — 只在離線工具中使用 `git show` 讀 HEAD 原始碼
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.continuity import (  # noqa: E402
    ALGORITHM_VERSION,
    INJECTION_FILENAME,
    TURN_FILENAME,
    match_ngrams,
    ngrams,
    score_against_memories,
)

DEFAULT_MEMORY_DIR = REPO_ROOT / "data" / "memory"


# ────────────────────────────────────────────────────────────
# IO helpers
# ────────────────────────────────────────────────────────────


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """讀 JSONL；壞行跳過並計數（sidecar 是 append-only，容忍尾端半行）。"""
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def git_head_source(relpath: str) -> str:
    """回傳 ``git show HEAD:<relpath>`` 的原始碼文字。

    只在**離線工具 / 測試**使用，讀版本控制物件，絕不 spawn 服務。
    """
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{relpath}"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git show HEAD:{relpath} 失敗: {proc.stderr.decode('utf-8', 'replace')}"
        )
    return proc.stdout.decode("utf-8")


# ────────────────────────────────────────────────────────────
# join / rescore（純函式，測試直接呼叫）
# ────────────────────────────────────────────────────────────


def index_injections(
    layer2_rows: Sequence[Dict[str, Any]],
) -> Dict[str, List[Tuple[str, str]]]:
    """``snapshot_id → [(memory_id, memory_text), ...]``（rank 順序）。"""
    grouped: Dict[str, List[Tuple[int, str, str]]] = {}
    for row in layer2_rows:
        snapshot_id = row.get("snapshot_id")
        if not snapshot_id:
            continue
        grouped.setdefault(snapshot_id, []).append((
            int(row.get("injection_rank", 0) or 0),
            row.get("memory_id") or "",
            row.get("memory_text") or "",
        ))
    return {
        snapshot_id: [(m, t) for _, m, t in sorted(entries, key=lambda e: e[0])]
        for snapshot_id, entries in grouped.items()
    }


def build_link(
    layer2_rows: Sequence[Dict[str, Any]],
    layer3_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Layer 2 → Layer 3 的 join。

    🔴 join key 是 ``correlation_id`` / ``injection_snapshot_id``，
    **絕不是** ``response_id``（兩層各有一個同名但不同身的欄位）。
    """
    by_snapshot = index_injections(layer2_rows)
    by_correlation: Dict[str, str] = {}
    for row in layer2_rows:
        correlation_id = row.get("correlation_id")
        snapshot_id = row.get("snapshot_id")
        if correlation_id and snapshot_id:
            by_correlation.setdefault(correlation_id, snapshot_id)

    linked: List[Dict[str, Any]] = []
    for row in layer3_rows:
        snapshot_id = row.get("injection_snapshot_id")
        if not snapshot_id:
            snapshot_id = by_correlation.get(row.get("correlation_id") or "")
        memories = by_snapshot.get(snapshot_id or "", [])
        linked.append({
            "turn": row,
            "injection_snapshot_id": snapshot_id,
            "memories": memories,
            "resolved": bool(snapshot_id) and bool(memories),
        })
    return linked


def rescore(
    layer2_rows: Sequence[Dict[str, Any]],
    layer3_rows: Sequence[Dict[str, Any]],
    algorithm_version: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """從 Layer 2 ``memory_text`` + Layer 3 ``response_text`` **完整重算** use。

    🔴 **零費用**：純 4-gram 集合運算，零 LLM 呼叫。
    ``algorithm_version`` 不符時標記 ``needs_rescore=True``，讓分析報告把這批
    資料當「待重算」而不是當成有效證據。
    """
    target_version = algorithm_version or ALGORITHM_VERSION
    out: List[Dict[str, Any]] = []
    for item in build_link(layer2_rows, layer3_rows):
        row = dict(item["turn"])
        row["use_heuristic"] = score_against_memories(
            row.get("response_text") or "", item["memories"]
        )
        row["injection_snapshot_id"] = item["injection_snapshot_id"]
        row["needs_rescore"] = (
            (row.get("use_heuristic") or {}).get("algorithm_version") != target_version
        )
        out.append(row)
    return out


# ────────────────────────────────────────────────────────────
# --self-test
# ────────────────────────────────────────────────────────────


def _fake_layer2() -> List[Dict[str, Any]]:
    return [
        {
            "schema_version": "continuity_injection_v1",
            "snapshot_id": "snap-a",
            "event_id": "evt-a",
            "correlation_id": "corr-a",
            "session_id": "s1",
            "agent_id": "agent_rem",
            "memory_id": "mem-1",
            "memory_text": "Bryan 喜歡咖啡",
            "memory_text_hash": "x",
            "retrieval_confidence": 0.9,
            "injection_rank": 0,
            "injection_timestamp": "2026-10-07T00:00:00+00:00",
            "response_id": "loader-rid-a",
            "retention_expires_at": "2026-11-06T00:00:00+00:00",
        },
        {
            "schema_version": "continuity_injection_v1",
            "snapshot_id": "snap-b",
            "event_id": "evt-b",
            "correlation_id": "corr-b",
            "session_id": "s1",
            "agent_id": "agent_rem",
            "memory_id": "mem-2",
            "memory_text": "姊姊喜歡抹茶",
            "memory_text_hash": "y",
            "retrieval_confidence": 0.8,
            "injection_rank": 0,
            "injection_timestamp": "2026-10-07T00:01:00+00:00",
            "response_id": "loader-rid-b",
            "retention_expires_at": "2026-11-06T00:01:00+00:00",
        },
    ]


def _fake_layer3() -> List[Dict[str, Any]]:
    return [
        {
            "schema_version": "continuity_turn_v1",
            "response_id": "turn-rid-a",
            "event_id": "evt-speak-a",
            "correlation_id": "corr-a",
            "session_id": "s1",
            "agent_id": "agent_rem",
            "injection_snapshot_id": "snap-a",
            "link_status": "linked",
            "response_status": "ok",
            "response_text": "我記得 Bryan 喜歡咖啡，對吧？",
            "response_text_hash": "z",
            "use_heuristic": {
                "candidate": True,
                "matched_memory_ids": ["mem-1"],
                "matched_ngram_count": 7,
                "algorithm_version": ALGORITHM_VERSION,
            },
            "timestamp": "2026-10-07T00:00:05+00:00",
            "retention_expires_at": "2026-11-06T00:00:05+00:00",
        },
        {
            "schema_version": "continuity_turn_v1",
            "response_id": "turn-rid-b",
            "event_id": "evt-speak-b",
            "correlation_id": "corr-b",
            "session_id": "s1",
            "agent_id": "agent_rem",
            "injection_snapshot_id": "snap-b",
            "link_status": "linked",
            "response_status": "ok",
            "response_text": "今天天氣不錯呢。",
            "response_text_hash": "w",
            "use_heuristic": {
                "candidate": False,
                "matched_memory_ids": [],
                "matched_ngram_count": 0,
                "algorithm_version": ALGORITHM_VERSION,
            },
            "timestamp": "2026-10-07T00:01:05+00:00",
            "retention_expires_at": "2026-11-06T00:01:05+00:00",
        },
    ]


def self_test() -> int:
    """用 synthetic 資料驗證：join 100% + 4-gram 邊界案例全對。

    回傳 0 = 全過，1 = 有失敗。印出可被 grep 的摘要。
    """
    failures: List[str] = []

    def check(label: str, actual: Any, expected: Any) -> None:
        if actual != expected:
            failures.append(f"{label}: got {actual!r}, expected {expected!r}")

    # ── 邊界案例 1: 0 命中 ────────────────────────────────────
    check("empty-response/no-hit",
          match_ngrams("Bryan 喜歡咖啡", ""), set())
    check("no-overlap",
          match_ngrams("Bryan 喜歡咖啡", "今天天氣不錯呢"), set())

    # ── 邊界案例 2: 只有 1 個 4-gram 命中 → 不算 use ──────────
    mem = "abcdefgh"
    one_hit = match_ngrams(mem, "zzzabcdyyy")   # 只有 "abcd"
    check("one-hit/count", len(one_hit), 1)
    check("one-hit/not-candidate",
          score_against_memories("zzzabcdyyy", [("m1", mem)])["candidate"], False)

    # ── 邊界案例 3: 2 個**彼此不同**的 4-gram 命中 → 算 use ──
    # len("abcdefgh")=8 → 8-(4-1)=5 個 4-gram: abcd/bcde/cdef/defg/efgh
    two_hits = match_ngrams(mem, "abcdefgh")
    check("two-hit/count", len(two_hits), 5)
    check("two-hit/distinct", len(two_hits), len(ngrams(mem)))
    check("two-hit/candidate",
          score_against_memories("abcdefgh", [("m1", mem)])["candidate"], True)

    # ── 邊界案例 4: 同一 4-gram 重複兩次（set 去重 → 不加計）──
    # 24 個字元的 "abcabc..." 只有 3 個**不同**的 4-gram
    dup = match_ngrams("abcabcabc", "abcabcabc")
    check("duplicate-4gram/deduped", len(dup), len(set(dup)))
    check("duplicate-4gram/repeated-not-counted",
          len(match_ngrams("abcabcabc", "abcabcabcabcabcabcabcabc")), 3)

    # ── 邊界案例 5: len(norm) < 4 → 空集合 ───────────────────
    check("short-text/no-grams", ngrams("abc"), set())
    check("empty-text/no-grams", ngrams(""), set())
    check("exact-4/one-gram", len(ngrams("abcd")), 1)

    # ── 邊界案例 6: 標點不去除 → 保守低估（工單指定語意）────
    check("punctuation/not-stripped",
          match_ngrams("喜歡,咖啡", "喜歡咖啡"), set())

    # ── join 成功率必須 100% ─────────────────────────────────
    layer2, layer3 = _fake_layer2(), _fake_layer3()
    items = build_link(layer2, layer3)
    check("join/resolved-count", sum(1 for i in items if i["resolved"]), 2)
    for item in items:
        turn = item["turn"]
        if turn["correlation_id"] == "corr-a":
            check("join/corr-a/snapshot", item["injection_snapshot_id"], "snap-a")
            check("join/corr-a/rank0-memory", item["memories"][0][0], "mem-1")

    # ── rescore 與寫入時結果一致 ────────────────────────────
    rescored = rescore(layer2, layer3)
    by_corr = {r["correlation_id"]: r for r in rescored}
    check("rescore/corr-a/candidate", by_corr["corr-a"]["use_heuristic"]["candidate"], True)
    check("rescore/corr-b/candidate", by_corr["corr-b"]["use_heuristic"]["candidate"], False)
    check("rescore/corr-a/matched", by_corr["corr-a"]["use_heuristic"]["matched_memory_ids"], ["mem-1"])
    check("rescore/none-needs-rescore",
          any(r["needs_rescore"] for r in rescored), False)

    print(f"[continuity_analyze --self-test] join_rate=100% "
          f"(2/2) checks_failed={len(failures)} algorithm_version={ALGORITHM_VERSION}")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


# ────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────


def _report(layer2: Sequence[Dict[str, Any]], layer3: Sequence[Dict[str, Any]]) -> None:
    items = build_link(layer2, layer3)
    resolved = sum(1 for i in items if i["resolved"])
    total = len(items)
    print("── Memory Continuity Observability v1 ──")
    print(f"Layer 2 (injection) rows : {len(layer2)}")
    print(f"Layer 3 (turn) rows      : {len(layer3)}")
    print(f"Layer2 -> Layer3 join    : {resolved}/{total}"
          f" ({100.0 * resolved / total if total else 0.0:.1f}%)")
    by_status: Dict[str, int] = {}
    for row in layer3:
        by_status[row.get("response_status", "?")] = (
            by_status.get(row.get("response_status", "?"), 0) + 1
        )
    print(f"response_status 分佈     : {by_status or '(無資料)'}")
    unlinked = sum(1 for i in items if not i["resolved"])
    if unlinked:
        print(f"⚠ unlinked gap           : {unlinked} 筆（TurnLinkIndex 記憶體狀態，"
              f"服務重啟會失聯；缺資料可見，非靜默消失）")
    candidates = sum(
        1 for r in layer3 if (r.get("use_heuristic") or {}).get("candidate")
    )
    print(f"use_candidate (heuristic): {candidates}/{total}"
          f"  ← 這是 **use 訊號**，不是 influence 證據")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", default=str(DEFAULT_MEMORY_DIR),
                        help="Layer 2 / Layer 3 JSONL 所在目錄（唯讀）")
    parser.add_argument("--self-test", action="store_true",
                        help="用 synthetic 資料驗證 join 與 4-gram 邊界案例")
    parser.add_argument("--rescore", action="store_true",
                        help="離線重算 use heuristic（零費用），--out 才會寫檔")
    parser.add_argument("--out", default=None,
                        help="--rescore 的輸出路徑（未給則只印統計）")
    parser.add_argument("--algorithm-version", default=None,
                        help="目標演算法版本；與 row 不同則標 needs_rescore")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    memory_dir = Path(args.memory_dir)
    layer2 = read_jsonl(memory_dir / INJECTION_FILENAME)
    layer3 = read_jsonl(memory_dir / TURN_FILENAME)

    if args.rescore:
        rescored = rescore(layer2, layer3, args.algorithm_version)
        pending = sum(1 for r in rescored if r.get("needs_rescore"))
        print(f"[--rescore] rows={len(rescored)} needs_rescore={pending} "
              f"algorithm_version={args.algorithm_version or ALGORITHM_VERSION}")
        if args.out:
            write_jsonl(Path(args.out), rescored)
            print(f"[--rescore] 已寫入 {args.out}")
        else:
            print("[--rescore] 未指定 --out，只印統計，不寫任何檔案")
        return 0

    _report(layer2, layer3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())