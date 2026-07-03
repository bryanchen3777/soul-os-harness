"""
src/memory/v1/schema.py
v1 Memory Schema — 凍結版。

不變性 (Constitution):
- @dataclass(frozen=True): 任何修改需要重新建立物件,不可 in-place 改
- 禁止新增欄位:schema 變更 = v1 終止,需要走 v2 spec
- 禁止評分/語義/weight 欄位:v1 不做判斷,只記錄

Bry 規範(2026-07-02):frozen=True 補上,讓程式碼與文件宣告的不變性一致。
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
import json
import time


@dataclass(frozen=True)
class Memory:
    """單筆記憶 — 不可變,只能新增。"""
    memory_id: str          # uuid
    agent_id: str           # "agent_rem" / "agent_yua" / "agent_aoi" 等
    content: str            # 原文(任何字串,包括中文)
    tags: List[str]          # 例如 ["preference", "episode", "fact", "milestone", "diary"]
    created_at: float        # unix timestamp

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalLog:
    """每次 retrieval 觸發一筆 log。append-only。"""
    log_id: str              # uuid
    query_tags: List[str]     # 查詢用的 tags
    retrieved_ids: List[str]  # 命中的 memory_id(交集非空)
    rejected_ids: List[str]   # store 內但沒命中的 id
    timestamp: float          # unix timestamp

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
