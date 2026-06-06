"""
scripts/run_test_auto.py
自動啟動 server、跑測試、關閉
"""
import subprocess
import time
import sys
import asyncio

def main():
    print("啟動 server...")
    server = subprocess.Popen(
        [sys.executable, "scripts/run_server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # 等 server ready
    print("等待 server 啟動...")
    started = False
    for _ in range(20):
        line = server.stdout.readline()
        if line:
            print(f"  [server] {line.rstrip()}")
        if "啟動完成" in line or "started server" in line.lower():
            started = True
            break
        time.sleep(0.5)

    if not started:
        print("⚠️  Server 可能未啟動，繼續嘗試...")

    time.sleep(2)

    try:
        print("\n開始測試...\n")
        from test_group_chat import run_test
        result = asyncio.run(run_test())

        yua_count = result.get("agent_yua", 0)
        if yua_count < 2:
            print("\n建議：將 agent_yua 初始搶答 base 提高到 0.90")
            print("  → 修改 src/agent/speaker_token.py 的 BASE_SCORES")
    finally:
        print("\n關閉 server...")
        server.terminate()
        try:
            stdout, _ = server.communicate(timeout=5)
            if stdout:
                print(stdout[-500:])
        except subprocess.TimeoutExpired:
            server.kill()
        print("完成。")


if __name__ == "__main__":
    main()