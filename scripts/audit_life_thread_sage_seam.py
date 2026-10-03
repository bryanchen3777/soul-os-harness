"""READ-ONLY audit: Life Thread -> InnerLifeEvent seam evidence.

Nothing here writes to data/**. It only folds life_threads.jsonl and reports.
"""
import collections
import json
import pathlib

root = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data\soul")

print("=" * 68)
print("A. 線頭 fold 後的 dissolve / SAGE 憑據實況")
print("=" * 68)

tot = collections.Counter()
detail = []
for d in sorted(root.iterdir()):
    f = d / "life_threads.jsonl"
    if not f.is_file():
        continue
    g = collections.defaultdict(list)
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            g[json.loads(line).get("thread_id")].append(json.loads(line))
        except json.JSONDecodeError:
            pass
    for tid, rs in g.items():
        rs.sort(key=lambda r: (r.get("updated_at") or "", r.get("event_seq") or 0))
        last = rs[-1]
        st = last.get("status")
        dis = bool(last.get("dissolved_at"))
        sid = (last.get("sage_fact_id") or "").strip()
        tot["threads"] += 1
        tot[f"status:{st}"] += 1
        if dis:
            tot["dissolved_at_set"] += 1
        if sid:
            tot["sage_fact_id_set"] += 1
        if dis and sid:
            tot["both_set"] += 1
        if st in ("completed", "abandoned") and not dis:
            tot["terminal_NO_dissolved_at"] += 1
        if st in ("completed", "abandoned") and not sid:
            tot["terminal_NO_sage_fact_id"] += 1
        detail.append((d.name, st, dis, sid[:36], (last.get("title") or "")[:24]))

for k in sorted(tot):
    print(f"  {k} = {tot[k]}")

print()
print("  --- 終態線頭明細（completed/abandoned）---")
for a, st, dis, sid, ti in sorted(detail):
    if st in ("completed", "abandoned"):
        print(f"    {a:<14} {st:<10} dissolved_at={str(dis):<5} sage={sid or '(empty)':<36} {ti}")

print()
print("=" * 68)
print("B. 事件欄位是否曾出現 InnerLifeEvent 相關欄位")
print("=" * 68)
allkeys = collections.Counter()
for d in sorted(root.iterdir()):
    f = d / "life_threads.jsonl"
    if not f.is_file():
        continue
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            allkeys.update(json.loads(line).keys())
        except json.JSONDecodeError:
            pass
print("  所有欄位鍵聯集：")
for k, v in sorted(allkeys.items()):
    print(f"    {k} ({v})")
interesting = [
    "event_id", "inner_life_event_id", "event_id_ref", "trace_id",
    "provenance", "actor_id", "eligible", "lived",
]
hit = [k for k in interesting if k in allkeys]
print(f"  InnerLifeEvent / lived-eligibility 相關欄位命中：{hit if hit else 'NONE'}")
