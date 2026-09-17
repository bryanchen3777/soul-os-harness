#!/usr/bin/env python3
"""Test full WebSocket + MiniMax chat"""
import subprocess, sys, time, asyncio, websockets, json, urllib.request

def start_server():
    proc = subprocess.Popen(
        [sys.executable, 'scripts/run_server.py'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return proc

def wait_server(timeout=12):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = urllib.request.urlopen('http://localhost:8000/health', timeout=2)
            return r.read().decode() != ''
        except:
            time.sleep(0.5)
    return False

async def test_chat():
    uri = 'ws://localhost:8000/ws'
    async with websockets.connect(uri, open_timeout=10) as ws:
        print('✅ Connected to WebSocket')

        # Send message
        msg = json.dumps({'type': 'USER_MESSAGE', 'content': '你好！', 'user_id': 'user1'})
        await ws.send(msg)
        print('📤 Sent: 你好！')

        # Wait for response
        for i in range(20):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=3)
                data = json.loads(raw)
                t = data.get('type')
                if t == 'agent_speak':
                    print()
                    print('='*50)
                    print('✅ Yua 說：', data.get('text', ''))
                    print('='*50)
                    return True
                elif t == 'SYSTEM_ERROR':
                    print('❌ Error:', data)
                elif t == 'ping':
                    print('📶 ping')
            except asyncio.TimeoutError:
                print('.', end='', flush=True)
        return False

if __name__ == '__main__':
    print('Starting server...')
    proc = start_server()
    print(f'Server PID: {proc.pid}')

    if not wait_server():
        print('❌ Server failed to start')
        sys.exit(1)

    print('✅ Server ready')
    print()
    print('Testing chat...')

    try:
        success = asyncio.run(test_chat())
        if success:
            print()
            print('🎉 SUCCESS - Chat UI working with MiniMax!')
        else:
            print()
            print('❌ Timeout - no response')
    except Exception as e:
        print(f'❌ Error: {e}')

    proc.kill()
    print('Done')