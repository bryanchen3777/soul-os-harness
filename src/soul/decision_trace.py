"""
src/soul/decision_trace.py — Soul OS SM-3 四元決策落盤（純觀測 sidecar）

P1-A-1 (2026-09-12, ADDITIVE OBSERVABILITY): 把 DecisionResult 的四元決策標籤
(transmit / observe / reflect / do_nothing) 落盤成獨立 append-only JSONL。

背景 (INNER-LIFE-AUDIT-1 斷點③「意志斷了」):
  DecisionResult 在唯一生產呼叫點 (scheduler._decision_check) 被 `if result.transmit:`
  壓成布林, observe/reflect/do_nothing 三者合併成同一條 not_transmit log
  → 四元標籤徹底消失, 決策分佈結構性不可觀測。
  本模組只把「已經發生的選擇」抄一份到磁碟, 讓分佈可統計。

Frozen contract 邊界 (0 判定語意變更):
  - 純觀測 sidecar: 只讀 DecisionResult / Motive 的既有欄位, 不參與任何判定。
  - 不改變 scheduler 的 gate 回傳值 / 分支結構 / mark_transmitted / mark_rejected。
  - 不碰 DecisionResult (frozen, 6 欄) / DECISION_ACTIONS / parse_decision_output。
  - 不碰任何門檻常數 (TENSION_ELIGIBILITY_MIN_CONFIDENCE / relational_bands / CONFIDENCE_*)。
  - 不寫入 Prompt 全文 / 系統提示 / 任何未列出的欄位 (不擴大記憶體與隱私外洩面)。
  - best-effort: append 失敗只 log warning, 永不 raise, 絕不中斷 gate。

落盤 (data/soul/decision_trace.jsonl, 全域單檔, append-only, 0 rotation / 0 async writer):
  {"ts","agent_id","decision","transmit","reason","motive_id",
   "motive_content","provenance_ref","motive_kind","motive_created_at"}

  與 MotiveTraceStore (data/soul/motive_trace.jsonl) 同目錄同域、語義分離:
  意圖 (motive) vs 選擇 (decision)。

  agent_id 與 ts 由呼叫端注入 (DecisionResult 本身沒有這兩欄)。
  motive_kind / motive_created_at 為 Motive 的觀察欄位; 屬性不存在時寫空字串
  (不猜測替代欄位)。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("soul_os.soul.decision_trace")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

# 落盤檔名 (路徑由 data_root() 組出: data/soul/decision_trace.jsonl)
DECISION_TRACE_FILENAME = "decision_trace.jsonl"

# 相對 data_root() 的子目錄 (與 motive_trace.jsonl 同目錄)
DECISION_TRACE_SUBDIR = "soul"


# ───────────────────────────────────────────────────────────
# DecisionTraceStore — 獨立 append-only JSONL
# ───────────────────────────────────────────────────────────

class DecisionTraceStore:
    """
    四元決策 trace 存儲 (data/soul/decision_trace.jsonl, append-only)。

    記錄 = 一次 Decision 的完整觀測快照 (DecisionResult 既有欄位 + agent_id + ts
    + motive 觀察欄位)。一行一筆, 只 append, 不改寫既有行。

    純觀測 sidecar: 本類別不參與判定, 不 publish, 不建立定時器,
    不對 result / motive 做任何屬性賦值或變更。
    """

    def __init__(self, trace_path: Optional[Path] = None) -> None:
        if trace_path is None:
            from src.paths import data_root
            trace_path = (
                data_root() / DECISION_TRACE_SUBDIR / DECISION_TRACE_FILENAME
            )
        self._trace_path = Path(trace_path)

    @property
    def trace_path(self) -> Path:
        """實際落盤路徑 (觀測/測試用, 唯讀)。"""
        return self._trace_path

    # ── 讀寫 ─────────────────────────────────────────────

    def build_record(self, *, agent_id: str, motive: Any, result: Any) -> Dict[str, Any]:
        """組一條落盤記錄 (純讀取, 不改動 result / motive)。

        欄位名固定 (P1-A-1 §3), 不得改名、不得新增未列出的欄位。
        """
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "decision": getattr(result, "decision", ""),
            "transmit": getattr(result, "transmit", False),
            "reason": getattr(result, "reason", ""),
            "motive_id": getattr(result, "motive_id", ""),
            "motive_content": getattr(result, "motive_content", ""),
            "provenance_ref": getattr(result, "provenance_ref", ""),
            "motive_kind": getattr(motive, "kind", ""),
            "motive_created_at": getattr(motive, "created_at", ""),
        }

    def append(self, *, agent_id: str, motive: Any, result: Any) -> bool:
        """append 一條決策記錄 → True (成功) / False (失敗)。

        best-effort: 任何例外只 log warning, 永不 raise (絕不中斷呼叫方 gate)。
        """
        try:
            record = self.build_record(
                agent_id=agent_id, motive=motive, result=result
            )
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as e:
            logger.warning(f"[DecisionTraceStore] append failed: {e}")
            return False
