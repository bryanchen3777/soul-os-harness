"""
scripts/run_memory_test.py
自動啟動 server、跑記憶分離測試、關閉
"""
import subprocess, time, sys, httpx
from pathlib import Path

_scripts = Path(__file__).parent.resolve()
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

server = subprocess.Popen(
    [sys.executable, "scripts/run_server.py"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
)
print("啟動 server...")
time.sleep(2)

# 等 health endpoint ready
import asyncio
for i in range(15):
    try:
        resp = httpx.get("http://localhost:8000/health", timeout=2)
        if resp.status_code == 200:
            print(f"  server ready ({i*0.5:.1f}s)")
            break
    except Exception:
        pass
    time.sleep(0.5)
else:
    print("  ⚠️  server 未就緒，繼續嘗試...")

try:
    import test_memory_split
    asyncio.run(test_memory_split.run_test())
finally:
    server.terminate()
    try:
        out, err = server.communicate(timeout=5)
        if out: print(out[-500:])
        if err: print(err[-500:])
    except subprocess.TimeoutExpired:
        server.kill()
    print("完成。")