"""
scripts/relband_reeval.py — RELATIONAL-BAND-FIX-1 §③ 一次性 Bry 軸關係帶重評工具

设计来源: docs/RELATIONAL-BAND-INVESTIGATION.md（唯讀根因調查）+
          docs/SG-4-RELATIONAL-SIGNAL-ADDENDUM.md（信号源定义扩充）

目的
----
Bry 軸（Owner 通道, `user_bryan`）的三个 objective 计数器在生产上**结构性恒 0**
（`data/soul/interactions.jsonl` 26 行 / `data/world/perception_trace.jsonl`
13,490 行中出现 `user_bryan` **0 次**）→ `stranger→known` 永不可达。
新码（`src/social/relation_settlement.py` 的 **Bry 軸讀側折抵**）生效后, 本工具
以**正規寫入口** `RelationshipsStore.apply_relation_evaluation(..., ref=...)`
對 Bry 軸重評一次, 讓帶位經由正常路徑晉升 —— 留下
`[RelSettle][BAND_MIGRATION] other=user_bryan direction=promote` 審計證據與
`band_updated_at` 首次寫入。

用法
----
    python scripts/relband_reeval.py                 # dry-run（預設, 0 寫入）
    python scripts/relband_reeval.py --apply         # 真正寫入（走正規入口）
    python scripts/relband_reeval.py --agent agent_ruka --apply
    python scripts/relband_reeval.py --data-root <tmp> --now 2026-09-14T05:00:00+00:00

    --data-root   資料根（預設 = src.paths.data_root(); 測試一律指向 tmp root）
    --agent       可重複; 預設 = 所有**已含 Bry entry** 的 agent
    --now         ISO 8601; 預設 = 當下 UTC（決定 24h 窗口與冪等 ref）
    --window-hours 窗口長度（預設 24, 即契約的 24h 結算窗）
    --apply       真正寫入; 省略 = dry-run

紅線（本工具自身即契約）
----
  - **dry-run 0 寫入**: 不實例化 `RelationshipsStore`（其 `_load_or_init` 會落盤）,
    純 `json.load` 讀 + 純函式 `evaluate_band` 預測。
  - **只碰 Bry 軸**: 除 `user_bryan` 外的 entry（peer 軸）一個位元都不寫;
    不新增 entry（無 Bry entry 的 agent 直接跳過, 不 `ensure`）。
  - **禁止硬寫 band 值**: band 一律由 `apply_relation_evaluation` → 純函式
    `evaluate_band` 算出（硬寫會被下次 settle 覆寫, 且破壞可審計性）。
  - **0 新定時器 / 0 新事件類型 / 0 LLM 呼叫 / 0 新寫入路徑**（PD-1）。
  - 幂等: `ref = rel:user_bryan:<now_iso>`, 同 `--now` 重跑 = 0 變更。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.social.relation_settlement import (  # noqa: E402
    _log_band_migration,
    collect_window_signals,
)
from src.social.relational_bands import (  # noqa: E402
    BAND_STRANGER,
    evaluate_band,
)
from src.soul.relationships import (  # noqa: E402
    BRYAN_ENTITY_ID,
    MultiAgentRelationshipsManager,
)

LOGGER_NAME = "soul_os.social.relation_settlement"
DEFAULT_WINDOW_HOURS = 24


# ───────────────────────────────────────────────────────────
# 只讀發現: 含 Bry entry 的 agent
# ───────────────────────────────────────────────────────────

def _load_relationships_raw(root: Path, agent_id: str) -> Optional[Dict[str, Any]]:
    """純 json 讀 relationships.json（0 寫; 壞檔 → None, fail-closed）。"""
    path = root / "soul" / agent_id / "relationships.json"
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def discover_agents_with_bry(root: Path) -> List[str]:
    """掃出 `others` 內**已存在** `user_bryan` entry 的 agent（只讀, 0 建 entry）。"""
    soul = root / "soul"
    if not soul.is_dir():
        return []
    out: List[str] = []
    for path in sorted(soul.glob("*/relationships.json")):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        others = data.get("others") if isinstance(data, dict) else None
        if isinstance(others, dict) and isinstance(others.get(BRYAN_ENTITY_ID), dict):
            out.append(path.parent.name)
    return out


# ───────────────────────────────────────────────────────────
# 單 agent 重評
# ───────────────────────────────────────────────────────────

def _bry_deltas(
    root: Path, agent_id: str, now: datetime, window_hours: int
) -> tuple[Dict[str, int], Dict[str, int]]:
    """走既有信號源 `collect_window_signals`, 取 Bry 軸該 pair 的單窗增量。

    口径与 `settle_relations` 逐字一致（SG-3 + SG-4）:
      reply_raw = counts["reply"] + co_raw;  reply = min(reply_raw, 1);  co = min(co_raw, 1)
    """
    signals = collect_window_signals(
        agent_id, now, window_hours=window_hours, base_dir=root
    )
    counts = signals.get(BRYAN_ENTITY_ID, {}) or {}
    co_raw = int(counts.get("co_presence", 0))
    reply = min(int(counts.get("reply", 0)) + co_raw, 1)
    co = min(co_raw, 1)
    dream = int(counts.get("dream", 0))
    return (
        {"reply_exchanges": reply, "co_presence_sessions": co, "dream_exchanges": dream},
        {
            "reply_exchanges": int(counts.get("reply", 0)),
            "co_presence_sessions": co_raw,
            "dream_exchanges": dream,
        },
    )


def reeval_agent(
    root: Path,
    agent_id: str,
    now: datetime,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    apply: bool = False,
    manager: Optional[MultiAgentRelationshipsManager] = None,
) -> Dict[str, Any]:
    """對單一 agent 的 Bry 軸重評一次。dry-run（apply=False）保證 0 寫入。"""
    now_iso = now.isoformat()
    ref = f"rel:{BRYAN_ENTITY_ID}:{now_iso}"

    data = _load_relationships_raw(root, agent_id)
    if data is None:
        return {"agent": agent_id, "status": "no_relationships_file"}
    entry_before = (data.get("others") or {}).get(BRYAN_ENTITY_ID)
    if not isinstance(entry_before, dict):
        # 不 ensure / 不新建: Bry 軸限定, 無 entry 者直接跳過（0 寫入）
        return {"agent": agent_id, "status": "no_bry_entry"}

    band_before = entry_before.get("relational_band", BAND_STRANGER) or BAND_STRANGER
    obj_before = entry_before.get("objective") if isinstance(
        entry_before.get("objective"), dict
    ) else {}
    deltas, raw = _bry_deltas(root, agent_id, now, window_hours)
    has_signal = any(v > 0 for v in deltas.values())

    base: Dict[str, Any] = {
        "agent": agent_id,
        "bry_last_interaction_at": entry_before.get("last_interaction_at"),
        "band_before": band_before,
        "counts_before": {
            "reply_exchanges": int(obj_before.get("reply_exchanges", 0)),
            "co_presence_sessions": int(obj_before.get("co_presence_sessions", 0)),
            "dream_exchanges": int(obj_before.get("dream_exchanges", 0)),
        },
        "window_raw": raw,
        "deltas": deltas,
        "ref": ref,
    }

    if not has_signal:
        # 窗口內無 Bry 信號 → **0 寫入**（不推進 ref, 不改任何 entry）。
        base["status"] = "no_signal"
        base["band_after"] = band_before
        return base

    if not apply:
        # dry-run 預測: 有信號 → apply_relation_evaluation 必然跳過降帶分支,
        # 走 evaluate_band（純函式, 與寫入口同一個）。0 寫入。
        after = evaluate_band(
            band_before,
            reply_exchanges=base["counts_before"]["reply_exchanges"] + deltas["reply_exchanges"],
            co_presence_sessions=base["counts_before"]["co_presence_sessions"]
            + deltas["co_presence_sessions"],
            dream_exchanges=base["counts_before"]["dream_exchanges"] + deltas["dream_exchanges"],
        )
        base.update(status="dry_run", band_after=after, applied=False)
        return base

    # ── 正規寫入口（唯一寫路徑）────────────────────────────
    mgr = manager or MultiAgentRelationshipsManager(data_dir=str(root / "soul"))
    store = mgr.get_store(agent_id)
    entry = store.apply_relation_evaluation(
        BRYAN_ENTITY_ID,
        reply_exchanges_delta=deltas["reply_exchanges"],
        co_presence_sessions_delta=deltas["co_presence_sessions"],
        dream_exchanges_delta=deltas["dream_exchanges"],
        ref=ref,
        now_iso=now_iso,
    )
    band_after = entry.get("relational_band", BAND_STRANGER) or BAND_STRANGER
    base.update(status="applied", band_after=band_after, applied=True)
    if band_after != band_before:
        # 復用既有 OQ-3 審計路徑（同一個 `_log_band_migration`, 0 新事件類型）
        direction = "promote" if _is_promote(band_before, band_after) else "demote"
        _log_band_migration(
            agent_id=agent_id,
            other_id=BRYAN_ENTITY_ID,
            from_band=band_before,
            to_band=band_after,
            direction=direction,
            window_deltas=deltas,
            entry=entry,
            ts=now_iso,
        )
        base["direction"] = direction
    return base


def _is_promote(band_before: str, band_after: str) -> bool:
    order = {"stranger": 0, "known": 1, "familiar": 2, "close": 3}
    return order.get(band_after, 0) > order.get(band_before, 0)


# ───────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────

def _parse_now(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="RELATIONAL-BAND-FIX-1: Bry 軸關係帶一次性重評（預設 dry-run）"
    )
    ap.add_argument("--apply", action="store_true",
                    help="真正寫入（走正規入口 apply_relation_evaluation）; 省略 = dry-run 0 寫入")
    ap.add_argument("--agent", action="append", default=None,
                    help="限定 agent（可重複）; 預設 = 所有已含 Bry entry 的 agent")
    ap.add_argument("--data-root", default=None, help="資料根（預設 = src.paths.data_root()）")
    ap.add_argument("--now", default=None, help="ISO 8601 評估時刻（預設 = 當下 UTC）")
    ap.add_argument("--window-hours", type=int, default=DEFAULT_WINDOW_HOURS,
                    help="信號窗口小時數（預設 24）")
    args = ap.parse_args(argv)

    if args.data_root:
        root = Path(args.data_root).resolve()
    else:
        from src.paths import data_root
        root = data_root()

    now = _parse_now(args.now)
    mode = "apply" if args.apply else "dry-run"

    if args.apply:
        # 讓既有 [RelSettle][BAND_MIGRATION] 審計行真的輸出（同一個 logger）
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    agents = args.agent if args.agent else discover_agents_with_bry(root)

    results: List[Dict[str, Any]] = []
    for agent_id in agents:
        res = reeval_agent(
            root, agent_id, now, window_hours=args.window_hours, apply=args.apply
        )
        results.append(res)
        band_txt = f"{res.get('band_before')} → {res.get('band_after')}"
        print(
            f"[Bry軸重評] agent={agent_id} band: {band_txt} "
            f"status={res['status']} ref={res.get('ref')} "
            f"deltas={res.get('deltas')} "
            f"bry_last_interaction_at={res.get('bry_last_interaction_at')}"
        )

    summary = {
        "mode": mode,
        "data_root": str(root),
        "now": now.isoformat(),
        "window_hours": args.window_hours,
        "agents_scanned": len(agents),
        "bry_entries": sum(1 for r in results if r["status"] != "no_bry_entry"),
        "credited": sum(1 for r in results if int(r.get("deltas", {}).get(
            "co_presence_sessions", 0)) > 0),
        "writes": sum(1 for r in results if r.get("applied")),
        "changed": sum(1 for r in results if r.get("band_after") != r.get("band_before")),
        "results": results,
    }
    print(f"[Bry軸重評][SUMMARY] {json.dumps(summary, ensure_ascii=False, sort_keys=True)}")
    if not args.apply:
        print("[Bry軸重評] dry-run: 0 寫入（未實例化 store, 未呼叫 apply_relation_evaluation）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
