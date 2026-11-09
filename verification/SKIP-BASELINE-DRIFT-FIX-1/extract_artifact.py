"""Scratch extractor: pull summary lines out of a pytest log file."""
import re
import sys

P = sys.argv[1] if len(sys.argv) > 1 else (r"C:\Users\bbfcc\.minimax\v2\sessions\2026\11\09\19-15-38-528-"
     r"session_bXZzXzQxYzM4OGNjNjNlODRhZmU4Y2Y2ZDU0YzBkNTgxNGI0"
     r"\reports\tool-outputs\08c15a392b15817ca8a3ff59-"
     r"6f79579accedd9450caf27cc7e230edb716f3b96b692a2097336035c76aba620.txt")

text = open(P, encoding="utf-8", errors="replace").read()
lines = text.splitlines()
print("total_lines", len(lines))
for i, ln in enumerate(lines):
    if ("SKIP-BASELINE" in ln or "short test summary" in ln
            or re.search(r"\d+ (passed|failed)", ln)
            or ln.startswith("FAILED") or ln.startswith("ERROR")):
        print(f"{i}: {ln}")
