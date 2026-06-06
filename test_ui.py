#!/usr/bin/env python3
"""Test if the static HTML is served correctly"""
import subprocess, sys, time, urllib.request, os

# Kill existing
subprocess.run(['pkill', '-f', 'run_server'], capture_output=True)
time.sleep(1)

# Start server
proc = subprocess.Popen(
    [sys.executable, 'scripts/run_server.py'],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True
)
print('Server PID:', proc.pid)
time.sleep(7)

# Test
try:
    r = urllib.request.urlopen('http://localhost:8000/', timeout=5)
    content = r.read().decode('utf-8', errors='replace')
    print('Length:', len(content))
    print('Has 與 Yua:', '與 Yua' in content)
    print('Has ws://:', 'ws://localhost:8000/ws' in content)
    if '與 Yua' in content:
        print('✅ SUCCESS - static HTML served!')
    else:
        print('❌ Using fallback DEMO_HTML')
        print('First 200 chars:', content[:200])
except Exception as e:
    print('Error:', e)

# Cleanup
proc.kill()