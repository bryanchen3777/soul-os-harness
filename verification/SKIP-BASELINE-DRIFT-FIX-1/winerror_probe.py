"""Scratch probe (evidence): capture the live symlink OSError on this host."""
import os
import pathlib
import tempfile

d = tempfile.mkdtemp(prefix="winerror_probe_")
tgt = os.path.join(d, "t")
open(tgt, "w").write("x")
link = os.path.join(d, "l")
try:
    os.symlink(tgt, link, target_is_directory=False)
except OSError as e:
    print("winerror", getattr(e, "winerror", None))
    print(repr(e))
else:
    print("NO_SKIP_SYMLINK_OK")
    pathlib.Path(link).unlink()
