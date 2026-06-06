#!/usr/bin/env python3
"""Run server and test WebSocket with MiniMax"""
import subprocess
import sys
import time
import asyncio
import websockets
import json
import urllib.request

def start_server():
    proc = subprocess.Popen(
        [sys.executable, 'scripts/run_server.py'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return proc

def wait_server(timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = urllib.request.urlopen('http://localhost:8000/health', timeout=2)
            return True
        except:
            time.sleep(0.5)
    return False

async def test_websocket():
    uri = 'ws://localhost:8000/ws'
    async with websockets.connect(uri, open_timeout=10) as ws:
        print('✅ Connected to WebSocket')
        await ws.send(json.dumps({'type': 'USER_MESSAGE', 'content': '你是誰？', 'user_id': 'user1'}))
        print('📤 Sent message')

        for i in range(25):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(msg)
                t = data.get('type')
                print(f'📥 [{i}] {t}')
                if t == 'agent_speak':
                    print()
                    print('=' * 50)
                    print('✅ Yua 說：', data.get('text', ''))
                    print('=' * 50)
                    return True
                elif t == 'SYSTEM_ERROR':
                    print('❌ Error:', data)
            except asyncio.TimeoutError:
                pass
        return False

if __name__ == '__main__':
    print('Starting server...')
    proc = start_server()
    print(f'Server PID: {proc.pid}')

    if not wait_server(timeout=12):
        print('❌ Server failed to start')
        proc.kill()
        sys.exit(1)

    print('✅ Server ready')
    print('Testing WebSocket...')

    try:
        success = asyncio.run(test_websocket())
        if success:
            print('\n✅ Test PASSED - MiniMax M2.7 working!')
        else:
            print('\n❌ Test timed out')
    except Exception as e:
        print(f'\n❌ Test error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        proc.kill()