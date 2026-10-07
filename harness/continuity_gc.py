"""Memory Continuity Observability v1 — 30 天 retention GC。

🔴 **預設 dry-run**：只印出「將刪 N 筆 Layer 2 / M 筆 Layer 3」與時間區間，
**不碰任何檔案**。真正改寫必須明確 ``--apply``。

代理紅線（AGENTS.md #8）：``data/**`` 是 production state，任何 agent 都不得
為了「清理」而寫入 / 改名 / 刪除其中任何檔案。因此**本工具預設 dry-run，
agent 也不得對 ``data/**`` 跑 ``--apply``**；``--apply`` 只留給 Bry 在明確
決策後執行。

過期的 continuity sidecar **不得**升格進 Soul Memory / Palace ——
Soul Memory 長期、Conversation 生命史、Observability 30 天暫存（責任分離）。

用法::

    python harness/continuity_gc.py                              # dry-run
    python harness/continuity_gc.py --memory-dir <tmp> --apply    # 才會改寫
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.continuity import (  # noqa: E402
    INJECTION_FILENAME,
    RETENTION_DAYS,
    TURN_FILENAME,
)

DEFAULT_MEMORY_DIR = REPO_ROOT / "data" / "memory"


def read_rows(path: Path) -> Tuple[List[str], List[Dict[str, Any]]]:
    """回傳 ``(原始行, 解析後 row)``；壞行原樣保留（不丟資料）。"""
    if not path.exists():
        return [], []
    raw: List[str] = []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.rstrip("\n")
            if not stripped.strip():
                continue
            raw.append(stripped)
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                rows.append({"__unparseable__": True})
    return raw, rows


def is_expired(row: Dict[str, Any], now: datetime) -> bool:
    """以 row 自帶的 ``retention_expires_at`` 為準；缺欄位 = 不過期（保守保留）。"""
    expires = row.get("retention_expires_at")
    if not expires:
        return False
    try:
        return datetime.fromisoformat(expires) <= now
    except ValueError:
        return False


def plan(
    memory_dir: Path,
    now: Optional[datetime] = None,
    retention_days: int = RETENTION_DAYS,
) -> Dict[str, Any]:
    """計算（不執行）刪除計畫。"""
    now = now or datetime.now(timezone.utc)
    result: Dict[str, Any] = {
        "now": now.isoformat(),
        "retention_days": retention_days,
        "files": {},
    }
    for label, filename in (("layer2", INJECTION_FILENAME), ("layer3", TURN_FILENAME)):
        path = memory_dir / filename
        raw, rows = read_rows(path)
        keep_idx: List[int] = []
        drop_idx: List[int] = []
        for idx, row in enumerate(rows):
            (drop_idx if is_expired(row, now) else keep_idx).append(idx)
        stamps = [
            row.get("injection_timestamp") or row.get("timestamp")
            for row in rows
            if not row.get("__unparseable__")
        ]
        stamps = [s for s in stamps if s]
        result["files"][label] = {
            "path": str(path),
            "exists": path.exists(),
            "total": len(rows),
            "would_drop": len(drop_idx),
            "would_keep": len(keep_idx),
            "keep_indices": keep_idx,
            "oldest_timestamp": min(stamps) if stamps else None,
            "newest_timestamp": max(stamps) if stamps else None,
        }
    return result


def print_plan(plan_result: Dict[str, Any], apply: bool) -> None:
    mode = "APPLY（將改寫檔案）" if apply else "DRY-RUN（未改動任何檔案）"
    print("── Memory Continuity GC ──")
    print(f"模式      : {mode}")
    print(f"基準時間  : {plan_result['now']}")
    print(f"Retention : {plan_result['retention_days']} 天")
    for label, info in plan_result["files"].items():
        if not info["exists"]:
            print(f"{label}: 檔案不存在（{info['path']}）→ 略過")
            continue
        print(
            f"{label}: 將刪 {info['would_drop']} 筆 / 保留 {info['would_keep']} 筆"
            f"（共 {info['total']}）"
        )
        print(f"    路徑  : {info['path']}")
        print(f"    時間區間: {info['oldest_timestamp']} → {info['newest_timestamp']}")
    if not apply:
        print("\n（dry-run 結束。實際改寫需明確加 --apply；"
              "代理紅線：不得對 data/** 執行 --apply。）")


def apply_plan(plan_result: Dict[str, Any]) -> None:
    """就地重寫為保留列 + 原子 replace。**只應由 Bry 對 data/** 明確執行。"""
    for label, info in plan_result["files"].items():
        if not info["exists"] or info["would_drop"] == 0:
            continue
        path = Path(info["path"])
        raw, _ = read_rows(path)
        keep = set(info["keep_indices"])
        kept_lines = [line for idx, line in enumerate(raw) if idx in keep]
        directory = path.parent
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".continuity_gc")
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                for line in kept_lines:
                    handle.write(line + "\n")
            os.replace(str(tmp_path), str(path))
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        print(f"{label}: 已原子改寫 {path}（{len(kept_lines)} 筆保留）")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", default=str(DEFAULT_MEMORY_DIR))
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS)
    parser.add_argument("--apply", action="store_true",
                        help="實際改寫檔案（預設 dry-run）")
    args = parser.parse_args(argv)

    result = plan(Path(args.memory_dir), retention_days=args.retention_days)
    print_plan(result, args.apply)
    if args.apply:
        apply_plan(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())