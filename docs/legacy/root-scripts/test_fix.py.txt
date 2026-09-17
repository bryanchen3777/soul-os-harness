#!/usr/bin/env python3
"""Test fixes: only Yua responds, no prompt leak"""
import subprocess, sys, time, asyncio, websockets, json, urllib.request

def start_server():
    proc = subprocess.Popen(
        [sys.executable, 'scripts/run_server.py'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True
    )
    return proc

def wait_server(timeout=12):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = urllib.request.urlopen('http://localhost:8000/health', timeout=2)
            return True
        except:
            time.sleep(0.5)
    return False

async def test():
    uri = 'ws://localhost:8000/ws'
    async with websockets.connect(uri, open_timeout=10) as ws:
        print('✅ Connected')
        await ws.send(json.dumps({'type': 'USER_MESSAGE', 'content': '你好', 'user_id': 'user1'}))
        print('📤 Sent: 你好')

        agents_responded = set()
        for i in range(25):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(raw)
                t = data.get('type')
                if t == 'agent_speak':
                    agent = data.get('agent_id', 'unknown')
                    agents_responded.add(agent)
                    print()
                    print('='*50)
                    print(f'✅ {agent} 說：', data.get('text', '')[:150])
                    print('='*50)
                elif t == 'SYSTEM_ERROR':
                    print('❌ Error:', data)
            except asyncio.TimeoutError:
                print('.', end='', flush=True)

        print()
        print(f"Agents responded: {agents_responded}")
        if len(agents_responded) > 1:
            print('❌ FAIL - Multiple agents responded!')
        elif 'agent_yua' not in agents_responded:
            print('❌ FAIL - No Yua response!')
        else:
            print('✅ PASS - Only Yua responded')

if __name__ == '__main__':
    print('Starting server...')
    proc = start_server()

    if not wait_server():
        print('❌ Server failed')
        sys.exit(1)

    print('✅ Server ready\n')
    try:
        asyncio.run(test())
    except Exception as e:
        print(f'Error: {e}')
    finally:
        proc.kill()
        stdout, _ = proc.communicate(timeout=1)
        # Print DEBUG lines from server output
        for line in stdout.split('\n')[-50:]:
            if 'LLMProxy-DEBUG' in line or 'agent=' in line.lower():
                print(line)