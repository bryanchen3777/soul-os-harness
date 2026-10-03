"""Read-only audit of life_threads.jsonl current-state fold (per contract 2.4)."""
import json
import collections
from pathlib import Path

root = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data\soul")

grand = collections.Counter()
titles_by_agent = {}

for d in sorted(root.iterdir()):
    f = d / "life_threads.jsonl"
    if not f.is_file():
        continue
    rows, bad = [], 0
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r.get("thread_id")].append(r)

    cur = []
    for tid, rs in groups.items():
        rs.sort(key=lambda r: (r.get("updated_at") or "", r.get("event_seq") or 0))
        cur.append(rs[-1])

    status = collections.Counter(r.get("status") for r in cur)
    origin = collections.Counter(r.get("origin_type") for r in cur)
    titles = collections.Counter((r.get("title") or "")[:40] for r in cur)

    grand.update(status)
    grand.update({"_rows": len(rows), "_threads": len(groups), "_bad": bad})
    titles_by_agent[d.name] = (len(groups), status, origin, titles)

    print(f"--- {d.name} ---")
    print(f"  rows={len(rows)} bad={bad} threads(current)={len(groups)}")
    print(f"  status: {dict(status)}")
    print(f"  origin: {dict(origin)}")
    print(f"  distinct titles: {len(titles)}")
    for t, c in titles.most_common(6):
        print(f"    {c:>3}x  {t}")

print()
print("=== GRAND TOTAL ===")
for k, v in grand.items():
    print(f"  {k} = {v}")
