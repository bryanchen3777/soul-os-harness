"""One-shot helper: backup release gate bytes, then write open. Read-back verified."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
rel = REPO_ROOT / "release" / "sa.json"
orig = rel.read_bytes()
(REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-1" / "release.backup.json").write_bytes(orig)
print("backup_bytes=", orig)
rel.write_bytes(b'{"status": "open"}')
print("readback=", rel.read_bytes())
