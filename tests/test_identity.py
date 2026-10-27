# -*- coding: utf-8 -*-
"""test_identity.py — 線上服務的身分錨點探針（**預設不跑**）。

🔺 2026-10-27 實測修正（本檔原本會直接打生產 :8000）
────────────────────────────────────────────────────
本檔原本硬寫 `ws://localhost:8000/ws` 與 `http://localhost:8000`，而且
**完全沒有任何 opt-in 閘門**。pytest 會收集本檔的 `async def test_identity`
⇒ 每次全庫回歸都直連生產服務、送出 `USER_MESSAGE`，讓生產服務寫入對話／記憶檔、
產生 TTS，並跑**真實 LLM 推理**（成本非 0）。2026-09-17 事故同形。

2026-10-27 實測證據（三方交叉確認，非推測）：
  · 服務日誌：2026-10-27 01:12:53 / 01:13:04 / 01:13:10 三次 WS 連線，
    `WS raw msg: {'type': 'USER_MESSAGE', ..., 'user_id': 'bryan_test'}`；
  · 生產資料差異：`conversations/bryan_test_agent_{yua,ruka}_private.json` 變動、
    `tts/agent_*/20261027T0513*.mp3` 新增 3 筆、`memories.jsonl` 增長；
  · 檔案內容：本檔無任何 skipif／env 閘門。

修法（**把寫入能力本身移除**，不只是加一個條件比較 ——
      AGENTS.md 行程安全鐵律 12：守門條件必須不可漂移）：
  · 目標位址改由環境變數 `SOUL_OS_LIVE_SERVICE_BASE_URL` 提供，且**預設空字串**
    ⇒ 環境未設定時「沒有目標」⇒「沒有能力」；
  · 測試以 `pytest.mark.skipif` 釘住**同一個** env 變數 ⇒ 預設 skip；
  · 硬寫的生產位址全數移除（`tests/infra/test_no_production_spawn_guard.py` 的
    `ADDRESS_ALLOWLIST` 對應 2 筆同步移除，9 → 7）；
  · 新增結構性護衛 `tests/infra/test_live_service_probes_are_opt_in.py`
    ⇒ 「ws 目標由 env 決定」這件事本身就被釘住，日後不能默默再加回來。

手動執行（必須指向**隔離**實例，不是生產）：
    $env:SOUL_OS_LIVE_SERVICE_BASE_URL = "http://localhost:8765"
    .venv\Scripts\python.exe tests\test_identity.py
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets  # noqa: E402

#: 線上服務目標。**預設空字串** ⇒ 環境未設定時沒有目標、沒有能力。
LIVE_BASE = os.environ.get("SOUL_OS_LIVE_SERVICE_BASE_URL", "")
SERVER_URL = LIVE_BASE
WS_URL = (
    LIVE_BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
    if LIVE_BASE
    else ""
)


def check_server():
    """探測服務是否在線（唯讀 /health）。"""
    import urllib.request
    if not SERVER_URL:
        print("[server] 未設定 SOUL_OS_LIVE_SERVICE_BASE_URL ⇒ 不探測")
        return False
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as r:
            data = json.loads(r.read())
            print(f"[server] status={data.get('status')}")
            return True
    except Exception as e:
        print(f"[server] NOT reachable: {e}")
        return False


async def send_and_get_response(text, agent_id, mode):
    results = []

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        payload = {
            "type": "USER_MESSAGE",
            "content": text,
            "user_id": "bryan_test",
            "mode": mode,
        }
        if mode == "private":
            payload["target_agent"] = agent_id
        else:
            payload["participants"] = [agent_id]

        print(f"  [send] mode={mode} agent={agent_id} text={text}")
        await ws.send(json.dumps(payload))

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    results.append(data)
                    break
            except asyncio.TimeoutError:
                continue

    return results


@pytest.mark.skipif(
    not os.environ.get("SOUL_OS_LIVE_SERVICE_BASE_URL", ""),
    reason="需要明確指定 SOUL_OS_LIVE_SERVICE_BASE_URL（隔離實例）；預設 skip，避免測試打生產 :8000",
)
async def test_identity():
    print("=" * 60)
    print("Test: Identity Anchor")
    print("=" * 60)

    if not check_server():
        print("ERROR: Server not running!")
        return

    # Test 1: Yua "你是誰"
    print("\n[Test 1] 私聊 Yua: 你是誰")
    results = await send_and_get_response("你是誰", "agent_yua", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")

    await asyncio.sleep(1)

    # Test 2: Ruka "你是誰"
    print("\n[Test 2] 私聊 Ruka: 你是誰")
    results = await send_and_get_response("你是誰", "agent_ruka", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")

    await asyncio.sleep(1)

    # Test 3: Yua "你是 Ruka 嗎？"
    print("\n[Test 3] 私聊 Yua: 你是 Ruka 嗎？")
    results = await send_and_get_response("你是 Ruka 嗎？", "agent_yua", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    if not LIVE_BASE:
        raise SystemExit(
            "請先設定 SOUL_OS_LIVE_SERVICE_BASE_URL 指向一個隔離實例"
            "（例：$env:SOUL_OS_LIVE_SERVICE_BASE_URL='http://localhost:8765'）"
        )
    asyncio.run(test_identity())
