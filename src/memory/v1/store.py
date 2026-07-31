"""
src/memory/v1/store.py
v1 Store — 一個 agent 一個 JSONL 檔,append-only。

不變性:
- 寫入:append-only(只能新增,不可改不可刪)
- 讀取:全檔掃描(給 retrieval 用,小資料量 OK)
- 不索引(避免 v1 過度設計)
- 不去重(同一 memory_id 可加多次,觀察實際行為)

Constitution:
- 不做 dedup / merge / decay
- 不支援 update / delete
- 不維護索引(讓 v1 看到真實的全量掃描成本)

路徑約定 (Bry 拍板 2026-07-18, Stage 1.1):
  - 單一真相: data/{data_dir}/{agent_id}/memories.jsonl
  - 傳入 data_dir = 父目錄 (例如 data/memory), 不再傳 agent 子目錄
  - 不再用 "{agent_id}_memories.jsonl" 命名, 統一 "memories.jsonl"
  - 跟 SAGE GraphStore 共用同一個 agent 子目錄, 不同檔名 (.jsonl vs .sqlite)
"""
import json
from pathlib import Path
from typing import List, Optional
from .schema import Memory


class V1Store:
    """per-agent append-only JSONL store。

    接受「父目錄 + agent_id」, 自動組出標準路徑:
        {data_dir}/{agent_id}/memories.jsonl
    """

    def __init__(self, data_dir: Path, agent_id: str):
        self.agent_id = agent_id
        self.data_dir = Path(data_dir)
        # 統一路徑: data_dir/{agent_id}/memories.jsonl
        # 自動建立 agent 子目錄, 跟 SAGE GraphStore 共用
        self.agent_dir = self.data_dir / agent_id
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.store_file = self.agent_dir / "memories.jsonl"

    def add(self, memory: Memory) -> None:
        """append 一筆。"""
        with open(self.store_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(memory.to_dict(), ensure_ascii=False) + "\n")

    def all(self) -> List[Memory]:
        """全量讀回(給 retrieval 用)。"""
        if not self.store_file.exists():
            return []
        memories = []
        with open(self.store_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    memories.append(Memory(**data))
                except (json.JSONDecodeError, TypeError) as e:
                    # Constitution: 不修改 corrupt row,跳過(append-only)
                    # 但留下 log,讓分析層知道有 corrupt
                    print(f"[V1Store] corrupt row in {self.store_file}: {e}")
        return memories

    def get(self, memory_id: str) -> Optional[Memory]:
        """單筆查找(O(n),v1 不索引)。"""
        for m in self.all():
            if m.memory_id == memory_id:
                return m
        return None

    def count(self) -> int:
        """總筆數(便宜:用 wc)。"""
        if not self.store_file.exists():
            return 0
        with open(self.store_file, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
