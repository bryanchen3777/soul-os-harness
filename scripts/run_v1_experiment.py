"""
scripts/run_v1_experiment.py
v1 實驗單一入口(2026-07-02)。

目的:跑一次,產一批真實 retrieval log。
Bry 規範:示範資料只是為了驗證 pipeline 能跑通,真實素材(Rem/Ruka 等)重跑要另外做。

Bry 修正(2026-07-02):Bry 看到上一版缺 import json,補上。

Scope exclusion (v1):
- ✗ 評分 / confidence / weight
- ✗ 語義判斷 / contradiction detection
- ✗ threshold / trigger rules
- ✗ behavior delta / dedup / merge / decay
- ✗ cross-agent memory
- ✗ LLM judge
"""
import json
import sys
import uuid
import time
from pathlib import Path

sys.path.insert(0, r"C:\Users\bbfcc\.local\bin\soul-os-harness")

from src.memory.v1.schema import Memory
from src.memory.v1.store import V1Store
from src.memory.v1.retrieval import retrieve
from src.memory.v1.log_exporter import LogExporter

DATA_DIR = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data\memory_v1")
LOG_FILE = DATA_DIR / "retrieval.log.jsonl"

# 1. 灌 4 筆示範記憶(之後換成真實的 Rem/Ruka 對話)
store = V1Store(DATA_DIR, "agent_demo")
seed_memories = [
    ("我喜歡黑色,白色太刺眼", ["preference", "color"]),
    ("今天跟 Bryan 去吃拉麵,他點了味噌口味", ["episode", "food"]),
    ("Bryan 住台北", ["fact", "location"]),
    ("我喜歡貓,不喜歡狗", ["preference", "animal"]),
]

# 檢查 store 已有幾筆(避免重複灌,append-only)
existing = store.count()
if existing == 0:
    for content, tags in seed_memories:
        store.add(Memory(
            memory_id=str(uuid.uuid4()),
            agent_id="agent_demo",
            content=content,
            tags=tags,
            created_at=time.time(),
        ))
    print(f"[run_v1] 已灌 {len(seed_memories)} 筆示範記憶 (total: {store.count()})")
else:
    print(f"[run_v1] store 已有 {existing} 筆記憶,跳過灌資料")

# 2. 跑 3 種 query
exporter = LogExporter(LOG_FILE)
queries = [
    ["preference"],
    ["episode"],
    ["fact", "preference"],
]

print(f"\n[run_v1] 跑 {len(queries)} 次 query:")
for q in queries:
    log = retrieve(store, q)
    exporter.append(log)
    print(f"  query={q} → retrieved {len(log.retrieved_ids)} / rejected {len(log.rejected_ids)}")

# 3. 印 log
print(f"\n[run_v1] Log file: {LOG_FILE}")
for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
    d = json.loads(line)
    print(f"  {d['query_tags']} → retrieved={len(d['retrieved_ids'])} rejected={len(d['rejected_ids'])} log_id={d['log_id'][:8]}...")

print(f"\n[run_v1] 完成 — 等 Bry 看真實分布決定下一步")
