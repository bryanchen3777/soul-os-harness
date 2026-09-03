"""Mem0 式顯式記憶原語層（MR-2 實作，MR-1 契約 §4）。

只提供新 API，不攔截/改寫既有 SAGE 寫入管線（writer 隱式流程、evolution 硬刪）。
原語層與隱式流程互不呼叫。

Frozen Contract 聲明（MR-1 契約 §4.3 鎖定）:
- writer.add_fact / extract_and_write / write_turn（隱式）: 原封不動。
- evolution._prune / _merge（硬刪）: 原封不動，原語層不呼叫 evolution。
- graph_store.update_merge_lineage: 原語層**複用**（只讀呼叫，不改其簽名）。
- v1 mirror: 不涉及，原語層不寫 v1。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from .sage.graph_store import GraphStore
from .sage.models import Fact

logger = logging.getLogger("soul_os.memory.primitives")


class MemoryPrimitives:
    """Mem0 式顯式記憶原語層。只提供新 API，不攔截/改寫既有 SAGE 寫入管線。

    絕不破壞: writer._write_single 隱式流程（抽取/合併/矛盾）、
    evolution 硬刪 prune、v1 mirror。原語層與隱式流程互不呼叫。
    """

    def __init__(self, graph_store: GraphStore) -> None:
        """持有 GraphStore 引用。不持有 writer/evolution。"""
        self._store = graph_store

    def add_fact(self, fact: Fact) -> str:
        """顯式新增。映射到 graph_store.add_fact。

        - fact.valid_from 未設定時預設 = time.time()（寫入前填充）。
        - 返回 fact_id；失敗返回 ""（與 writer.add_fact 返回約定一致）。
        """
        try:
            if fact.valid_from is None:
                fact.valid_from = time.time()
            return self._store.add_fact(fact)
        except Exception:  # noqa: BLE001
            logger.exception("[MemoryPrimitives] add_fact failed")
            return ""

    def update_fact(
        self, fact_id: str, new_fact: Fact, reason: str = "update"
    ) -> str:
        """顯式更新：寫新版本 + 失效舊版本 + lineage。

        - 新 fact 寫入（新 fact_id，valid_from = now）。
        - 舊 fact invalidate_fact(fact_id, now)（軟刪，可回溯）。
        - 新 fact.merged_from = [fact_id]，merge_reason = reason。
        - 返回新 fact_id；舊 fact_id 不存在時返回 ""（不靜默建立）。
        """
        old = self._store.get_fact(fact_id)
        if old is None:
            return ""
        now = time.time()
        # 新版本 = 新 fact_id（防呆：避免呼叫者誤傳與舊 fact 相同 id 而 INSERT OR REPLACE 覆蓋舊行）
        new_fact.fact_id = str(uuid.uuid4())
        new_fact.valid_from = now
        new_fact.merged_from = [fact_id]
        new_fact.merge_reason = reason
        new_id = self._store.add_fact(new_fact)
        self._store.invalidate_fact(fact_id, at_time=now)
        # lineage 持久化：add_fact 的 INSERT 不寫 merged_from/merge_reason 列（既有行為），
        # 複用 update_merge_lineage（與 evolution._merge / resolve_conflict 同款，MR-1 契約 §4.3）
        self._store.update_merge_lineage(new_id, [fact_id], reason)
        return new_id

    def delete_fact(self, fact_id: str, reason: str = "delete") -> bool:
        """顯式刪除（軟刪）。映射到 invalidate_fact(fact_id, now)。

        - 絕不硬刪。reason 僅用於日誌/審計。
        - 返回 invalidate_fact 的結果。
        """
        logger.info(
            "[MemoryPrimitives] delete_fact fact_id=%s reason=%s", fact_id, reason
        )
        return self._store.invalidate_fact(fact_id, at_time=time.time())

    def resolve_conflict(
        self, winner_id: str, loser_id: str, reason: str = "conflict"
    ) -> bool:
        """衝突解決：winner 保留 + loser 失效 + lineage 記錄。

        - invalidate_fact(loser_id, now)（軟刪，loser 可回溯）。
        - update_merge_lineage(winner_id, merged_from=[loser_id], reason)
          （複用既有 graph_store.update_merge_lineage）。
        - 返回 True = 成功；winner/loser 任一不存在 = False。
        """
        winner = self._store.get_fact(winner_id)
        loser = self._store.get_fact(loser_id)
        if winner is None or loser is None:
            return False
        self._store.invalidate_fact(loser_id, at_time=time.time())
        return self._store.update_merge_lineage(
            winner_id, merged_from=[loser_id], merge_reason=reason
        )
