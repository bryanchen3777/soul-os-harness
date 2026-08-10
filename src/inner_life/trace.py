"""
src/inner_life/trace.py — Soul OS Inner Life Narrative Trace Sidecar

M5.4-5.6 (Bry 派工 2026-08-09 22:30) — Inner Life Narrative Trace Sidecar

派工派工派工派工 (派工明列):
  - Trace is OBSERVABILITY ONLY
  - Must NOT become canonical event storage
  - Must NOT become identity authority
  - Must NOT become event dispatcher
  - Must NOT become persistence dependency
  - Must NOT replace InnerLifeEvent

派工派工派工派工 (派工派工派工派工派工):
  - "The canonical event must remain valid even if trace writing fails"
  - "Trace failure cannot invalidate canonical event creation"
  - "Trace should reference the canonical identity rather than create another identity"
  - "Do not duplicate arbitrary event payload/content"
  - "Existing applications using InnerLifeWriter must continue working"
  - "Existing event behavior must not change"

派工派工派工 (派工派工派工派工):
  InnerLifeWriter
        │
        ├── canonical event registry (in-memory, per-instance)
        │
        └── optional Trace sidecar (this module)
                  │
                  ▼
          data/inner_life/trace.jsonl

派工派工派工派工派工 (this module follows the same pattern):
  - src/world/trace.py  (WorldPerceptionTraceWriter → data/world/perception_trace.jsonl)
  - src/memory/v1/loader.py + src/memory/middleware.py (loader_trace.jsonl)
  - src/memory/v1/store.py (canonical append-only jsonl)
  - src/memory/v1/log_exporter.py (retrieval log)
  - src/memory/shadow.py (shadow log)

This module provides:
  - NarrativeTraceWriter: append-only jsonl sidecar
  - Default path uses data_root() (P0.5 isolation contract)
  - write() returns True/False (failure isolated via try/except + logger.warning)
  - clear() for test cleanup
  - read_all() for debugging (read-only, no schema validation)

派工派工派工派工派工 (派工派工):
  - persistent event registry (M5.4-5.1 deliberately does NOT persist events)
  - event query layer (M5.4-5.7 future)
  - correlation semantics changes
  - payload/content duplication (only identity + lineage, NOT content)
  - production data cleanup
  - multi-thread safety (current runtime is single-process single-threaded)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .event import InnerLifeEvent
from .serialization import event_to_dict

logger = logging.getLogger("soul_os.inner_life.trace")


class NarrativeTraceWriter:
    """
    Append-only sidecar log writer for InnerLifeEvent creation.

    派工派工派工派工派工:
      - data/inner_life/trace.jsonl (default; P0.5 isolated via data_root())
      - append mode (no rewrite, no overwrite)
      - failure isolated: log warning, return False, do NOT raise
      - trace records = event_to_dict() — identity + lineage only, NO content duplication

    派工派工派工派工:
      - write() 失敗 → False + logger.warning, 主路徑不中斷
      - clear() 失敗 → logger.warning, 測試清理 best-effort
      - read_all() → 讀回所有 records (debug/observability, NO schema validation)

    派工派工派工派工派工 (派工派工派工派工派工):
      - 派工派工派工派工派工派工派工派工 (InnerLifeWriter is the only canonical authority)
      - identity authority (派工派工派工派工派工派工, 派工派工派工派工派工派工)
      - event dispatcher (派工派工派工派工派工派工派工派工派工)
      - persistence dependency (派工派工派工派工派工派工派工)
      - replacement for InnerLifeEvent (派工派工派工派工派工派工派工)
    """

    def __init__(self, trace_log_path: Optional[Path] = None) -> None:
        """
        Args:
            trace_log_path: 自訂 trace 檔路徑 (選填, None = 用 default).
                            P0.5 派工 2026-08-09 19:48: default uses data_root() for test isolation.
        """
        if trace_log_path is None:
            from src.paths import data_root
            trace_log_path = data_root() / "inner_life" / "trace.jsonl"
        self.trace_log_path = Path(trace_log_path)
        # 確保父目錄存在
        self.trace_log_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: InnerLifeEvent) -> bool:
        """
        寫一條 trace record (canonical event 序列化) → trace.jsonl.

        Trace record = event_to_dict() (identity + lineage only, NOT content duplication).

        Returns: True (寫入成功) / False (失敗)
        失敗 log warning 不 raise, 主路徑 (create_event) 照常 return event.
        """
        try:
            record = event_to_dict(event)
            line = json.dumps(record, ensure_ascii=False, default=str)
            with open(self.trace_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except Exception as e:
            logger.warning(
                f"[NarrativeTrace] 寫入失敗 (不影響主路徑): "
                f"{self.trace_log_path} | {type(e).__name__}: {e}"
            )
            return False

    def clear(self) -> None:
        """清空 trace log (測試用)."""
        try:
            if self.trace_log_path.exists():
                self.trace_log_path.unlink()
        except Exception as e:
            logger.warning(f"[NarrativeTrace] clear 失敗: {e}")

    def read_all(self) -> list[dict]:
        """
        讀回所有 trace records (debug/observability only).

        Returns: list of dict (each = event_to_dict() 序列化結果).
        失敗回傳 partial list + log warning, 不 raise.
        """
        records: list[dict] = []
        if not self.trace_log_path.exists():
            return records
        try:
            with open(self.trace_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    records.append(json.loads(line))
        except Exception as e:
            logger.warning(
                f"[NarrativeTrace] read_all 失敗: "
                f"{self.trace_log_path} | {type(e).__name__}: {e}"
            )
        return records
