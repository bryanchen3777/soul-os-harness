"""Bry 派工: 11 條破折號樣本, 看實際格式, 調整 regex 避免誤殺對話."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
LOG_DIR = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/logs")

samples = []
for log in LOG_DIR.glob("server_*.err"):
    if log.stat().st_size < 1000:
        continue
    try:
        content = log.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for m in re.finditer(
        r"\[LLMProxy\] 生成完成 \| agent=(\w+) text='((?:[^'\\]|\\.)*)' audio_text='((?:[^'\\]|\\.)*)'",
        content,
    ):
        agent, text, audio_text = m.group(1), m.group(2), m.group(3)
        if re.search(r"——[^—\n]+——", audio_text):
            samples.append((agent, audio_text))

print(f"=== 全部 11 條破折號 audio_text 樣本 ===\n")
for i, (agent, at) in enumerate(samples[:15]):
    print(f"  [{i+1}] [{agent}] {at}")
    print()
