"""
src/memory/v1/retrieval.py
v1 Retrieval — 唯一邏輯是 tag overlap,無加權無排序。

Bry 規範:
- v1 不評分(scoring)
- v1 不排序(所有命中的視為同等)
- v1 不去重(同一 memory_id 重複可見,讓 v1 看到真實行為)
- 全部記憶讀取(O(n) all),不索引
- 命中的判定:query_tags ∩ memory.tags ≠ ∅

未來(等 v1 看到真實 log 之後再說):
- 是否要加權(例如 tag 重要性)
- 是否要排序(例如 recency, frequency)
- 是否要去重(例如 fuzzy match)
- 是否要加 semantic search
"""
from typing import List, Set
import uuid
import time
from .schema import Memory, RetrievalLog
from .store import V1Store


def retrieve(store: V1Store, query_tags: List[str]) -> RetrievalLog:
    """
    純 tag overlap 檢索。

    Args:
        store: V1Store instance
        query_tags: 查詢的 tags list

    Returns:
        RetrievalLog: query_tags / retrieved_ids / rejected_ids / timestamp
    """
    query_set: Set[str] = set(query_tags)
    all_memories = store.all()

    retrieved = []
    rejected = []
    for m in all_memories:
        if query_set & set(m.tags):  # 交集非空 = 命中
            retrieved.append(m.memory_id)
        else:
            rejected.append(m.memory_id)

    return RetrievalLog(
        log_id=str(uuid.uuid4()),
        query_tags=list(query_tags),
        retrieved_ids=retrieved,
        rejected_ids=rejected,
        timestamp=time.time(),
    )
