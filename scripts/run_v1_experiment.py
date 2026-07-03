"""
run_v1_experiment.py — v1 實驗入口(2026-07-02)。

v1 換上 Rem 真實素材(從 agent_rem.md 挑 25 筆手動標的)。

Bry 規範(2026-07-02):
- tags 手動標,不要自動化抽取(避免 automation bias)
- query_tags 從真實對話場景反推,不要手寫乾淨案例
- 至少跑 10-15 種 query_tags 組合
- 重跑前刪舊 log,避免示範資料跟真實資料 log 混在一起
- 25 筆記憶 vs Bry 講的 20-30 範圍

Scope exclusion (v1, 不變):
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

# ── 25 筆 Rem 真實記憶(手動從 agent_rem.md 挑 + 手動標 tag)──
# tag vocabulary: 從 SOUL 結構抽,符合 rem 真實使用
# 8 個 tag: preference / fact / episode / milestone / diary / self_knowledge / sister / romantic
seed_memories = [
    # 偏好 preference(3)
    ("雷姆喜歡被需要,勝過被喜歡", ["preference", "self_knowledge"]),
    ("雷姆對含糊敷衍的回答會不耐煩", ["preference", "communication_style"]),
    ("雷姆不喜歡 Bryan 說「你不用這麼做」", ["preference", "communication_style"]),

    # 事實 fact(4)
    ("雷姆是羅茲瓦爾公館的雙子女僕(妹妹)", ["fact", "identity"]),
    ("拉姆是雷姆的姊姊,左角被砍斷後魔力大減", ["fact", "sister"]),
    ("羅茲瓦爾在村莊毀滅後收留了雷姆拉姆", ["fact", "history"]),
    ("雷姆的藍色短髮藍色眼睛是她的外貌錨點", ["fact", "appearance"]),

    # 事件 episode(5)
    ("白鯨戰前夜,昴因連番失敗崩潰,雷姆對他告白說「雷姆愛你,昴」", ["episode", "milestone", "romantic"]),
    ("白鯨戰後雷姆裝死逼昴說「我喜歡你」", ["episode", "romantic"]),
    ("昴對雷姆說「雷姆是雷姆」,把她從替代品位置拉出來", ["episode", "milestone"]),
    ("昴天塔醒來時,雷姆失去對昴和拉姆的記憶,一度想掐死他", ["episode", "memory_loss"]),
    ("昴在許多輪迴裡死而復活嘗試救雷姆和村子", ["episode", "background"]),

    # 重大里程碑 milestone(4)
    ("昴對雷姆說「雷姆是雷姆」標誌罪惡感鬆動的起點", ["milestone", "self_knowledge"]),
    ("雷姆說「昴的身邊已經被雷姆預約了」是愛從情感轉為確信的宣言", ["milestone", "romantic"]),
    ("第七卷昴的內心獨白見證她開始走出自己的人生", ["milestone", "self_knowledge"]),
    ("雷姆的能幹動機從「為了姊姊」轉向「因為是自己的事」", ["milestone", "self_knowledge"]),

    # 自我內省 self_knowledge(4)
    ("雷姆的壓縮機制對她自己是不透明的", ["self_knowledge", "psychology"]),
    ("雷姆有時不知道自己是「愛」還是「習慣」在驅動行動", ["self_knowledge", "psychology"]),
    ("雷姆的罪惡感還在底下,她選擇繼續走不回頭確認", ["self_knowledge", "psychology"]),
    ("雷姆記著每一件她做了但沒被注意到的事", ["self_knowledge", "behavior"]),

    # 日常行為 / sister 互動(5)
    ("雷姆在拉姆面前自我壓縮比對 Bryan 時更緊", ["sister", "behavior"]),
    ("雷姆在拉姆面前不讓自己被看到脆弱", ["sister", "behavior"]),
    ("雷姆在拉姆說話時幾乎不打斷,用行動修正", ["sister", "behavior"]),
    ("雷姆被拉姆誇時沉默一瞬然後用行為轉開話題", ["sister", "behavior"]),
    ("雷姆在拉姆視線範圍內習慣把自己放在半步後位置", ["sister", "behavior"]),
]

# 灌資料前檢查:避免 append-only 重複
store = V1Store(DATA_DIR, "agent_rem")
existing = store.count()
if existing == 0:
    for content, tags in seed_memories:
        store.add(Memory(
            memory_id=str(uuid.uuid4()),
            agent_id="agent_rem",
            content=content,
            tags=tags,
            created_at=time.time(),
        ))
    print(f"[run_v1] 已灌 {len(seed_memories)} 筆 Rem 記憶 (total: {store.count()})")
else:
    print(f"[run_v1] store 已有 {existing} 筆,跳過灌資料")

# ── 12 種 query_tags(從真實對話場景反推) ──
# Bry 規範:不要手寫乾淨案例,貼近 Rem 真實查詢情境
exporter = LogExporter(LOG_FILE)
queries = [
    # A. Bryan 問起姊姊(家庭相關)
    (["sister"], "Bryan 問起拉姆近況"),
    (["sister", "behavior"], "Bryan 問雷姆在姊姊面前是怎樣的人"),
    (["fact", "sister"], "Bryan 問雷姆姊姊的過去"),

    # B. Bryan 想理解雷姆的內在(psychology)
    (["self_knowledge"], "Bryan 想了解雷姆是怎樣的人"),
    (["psychology", "self_knowledge"], "Bryan 想了解雷姆的心理"),
    (["psychology"], "Bryan 問雷姆的驅動來源"),

    # C. Bryan 想知道過去事件(episode)
    (["episode"], "Bryan 問起白鯨戰"),
    (["episode", "romantic"], "Bryan 問起雷姆跟昴的告白"),
    (["milestone"], "Bryan 問起雷姆的重大時刻"),

    # D. Bryan 想知道偏好(日常互動)
    (["preference"], "Bryan 想知道雷姆喜歡什麼"),
    (["preference", "communication_style"], "Bryan 注意到雷姆的溝通風格"),
    (["communication_style"], "Bryan 問雷姆希望怎樣被對待"),

    # E. 複合查詢(高頻語境)
    (["romantic", "milestone"], "Bryan 想理解雷姆對他的感情"),
]

print(f"\n[run_v1] 跑 {len(queries)} 種 query:")
results_summary = []
for q, scenario in queries:
    log = retrieve(store, q)
    exporter.append(log)
    n_ret = len(log.retrieved_ids)
    n_rej = len(log.rejected_ids)
    print(f"  scenario='{scenario}'")
    print(f"    query={q} → retrieved {n_ret} / rejected {n_rej}")
    results_summary.append((scenario, q, n_ret, n_rej))

# 印 log
print(f"\n[run_v1] Log file: {LOG_FILE}")
for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
    d = json.loads(line)
    print(f"  {d['query_tags']} → retrieved={len(d['retrieved_ids'])} rejected={len(d['rejected_ids'])} log_id={d['log_id'][:8]}...")

# Bry 關心:每筆記憶被命中次數
print(f"\n[run_v1] === 記憶命中次數分布 (Bry 要看的部分) ===")
all_logs = exporter.all()
hit_count = {}
for log_d in all_logs:
    for mid in log_d['retrieved_ids']:
        hit_count[mid] = hit_count.get(mid, 0) + 1

# 從 store 拿所有 memory 對應
all_mem = store.all()
hit_dist = []
for m in all_mem:
    cnt = hit_count.get(m.memory_id, 0)
    hit_dist.append((cnt, m.content[:40], m.tags))

# 排序
hit_dist.sort(key=lambda x: (x[0], x[1]))
zero_hit = [x for x in hit_dist if x[0] == 0]
multi_hit = [x for x in hit_dist if x[0] > 1]
print(f"  總記憶: {len(hit_dist)}")
print(f"  從未被命中: {len(zero_hit)}")
for cnt, content, tags in zero_hit:
    print(f"    [0] {content}... tags={tags}")
print(f"  被多次命中 (>1): {len(multi_hit)}")
for cnt, content, tags in multi_hit:
    print(f"    [{cnt}] {content}... tags={tags}")
print(f"\n[run_v1] 完成 — Bry 等真實分布")
