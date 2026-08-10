"""
src/world/trace.py — Soul OS M3 Phase 1

WorldPerceptionTraceWriter (Bry 拍板 2026-08-07 19:40):
- sidecar trace log, 寫 data/world/perception_trace.jsonl
- 跟 data/memory/loader_trace.jsonl 同 pattern
- 每個 WorldEvent 進 perception layer 都產一條 trace (不論 accept/reject)
- 寫入失敗不 raise, 只 log warning (跟 M2.0 派工精神: 「拒絕問, 強制讀」)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .perception import WorldPerceptionTrace

logger = logging.getLogger("soul_os.world.trace")


class WorldPerceptionTraceWriter:
    """
    Append-only sidecar log writer。

    檔案路徑: data/world/perception_trace.jsonl (跟 data/memory/loader_trace.jsonl 對齊)
    寫入模式: append, 不重寫歷史 (跟 loader_trace 一樣)
    """

    def __init__(self, trace_log_path: Optional[Path] = None):
        """
        Args:
            trace_log_path: 預設 data/world/perception_trace.jsonl (相對於 cwd)
            P0.5 (Bry 派工 2026-08-09 19:48): default uses data_root() for test isolation
        """
        if trace_log_path is None:
            from src.paths import data_root
            trace_log_path = data_root() / "world" / "perception_trace.jsonl"
        self.trace_log_path = Path(trace_log_path)
        # 確保父目錄存在
        self.trace_log_path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, trace: WorldPerceptionTrace) -> bool:
        """
        寫一條 trace record 到 jsonl。

        Returns: True (寫入成功) / False (失敗)
        失敗 log warning 不 raise, 跟 loader_trace pattern 一致。
        """
        try:
            line = trace.to_jsonl()
            with open(self.trace_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            return True
        except Exception as e:
            logger.warning(
                f"[WorldPerceptionTrace] 寫入失敗 (不影響主路徑): "
                f"{self.trace_log_path} | {type(e).__name__}: {e}"
            )
            return False

    def clear(self) -> None:
        """清空 trace log (測試用)。"""
        try:
            if self.trace_log_path.exists():
                self.trace_log_path.unlink()
        except Exception as e:
            logger.warning(f"[WorldPerceptionTrace] clear 失敗: {e}")
