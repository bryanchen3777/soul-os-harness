"""
src/memory/v1/schema.py
v1 Memory Schema — 凍結版。

不變性 (Constitution):
- @dataclass(frozen=True): 任何修改需要重新建立物件,不可 in-place 改

v1.1 修訂 (Perplexity 拍板 via Bry, 2026-07-02):
- Append-only 加兩個 Optional 欄位 `category` 跟 `confidence`
- 既有的 5 個必填欄位不變
- 給 default = None, 既有資料 (沒這兩個欄位) 自動讀成 None
- Loader 用 fail-safe 自然排除 (沒 category/confidence 的 Memory 不注入)
- 不算 v1 終止 (v1 → v1.1 schema 升級, 不是 schema 變更 v2 重建)

Bry 規範(2026-07-02):frozen=True 補上,讓程式碼與文件宣告的不變性一致。
"""
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional  # Bry §12 spec 加 category/confidence 用 Optional
import json
import time


@dataclass(frozen=True)
class Memory:
    """單筆記憶 — 不可變,只能新增。

    Perplexity 拍板 (Bry 轉, 2026-07-02): 升級 schema 改 append-only 加兩個欄位
    `category` 跟 `confidence`,不破壞既有 frozen dataclass。
    既有資料沒這兩個欄位的,可以是 None (由 Loader fail-safe 自然排除)。
    """
    memory_id: str          # uuid
    agent_id: str           # "agent_rem" / "agent_yua" / "agent_aoi" 等
    content: str            # 原文(任何字串,包括中文)
    tags: List[str]          # 例如 ["preference", "episode", "fact", "milestone", "diary"]
    created_at: float        # unix timestamp
    # Bry §12 spec 需要:
    # category: 4 種 - identity / fact / preference / diary (Loader per-category threshold)
    # confidence: 0.0-1.0 (Loader threshold gating)
    category: Optional[str] = None  # 升級加的欄位, Perplexity (b)
    confidence: Optional[float] = None  # 升級加的欄位, Perplexity (b)

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
