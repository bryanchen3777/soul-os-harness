"""清空 data/conversations/ 但把現存檔 archive 到 backup 目錄。"""
from pathlib import Path
import shutil

conv = Path("data/conversations")
backup = Path("data/conversations_backup_old")
backup.mkdir(exist_ok=True)

moved = 0
for p in conv.glob("*.json"):
    target = backup / f"cycle_{moved:02d}_{p.name}"
    shutil.copy2(str(p), str(target))
    p.unlink()
    moved += 1

print(f"archived {moved} files to {backup}")
print(f"conversations/ now: {list(conv.glob('*'))}")
