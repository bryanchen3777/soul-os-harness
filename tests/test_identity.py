# -*- coding: utf-8 -*-
import httpx
import asyncio
import json

BASE_URL = "http://localhost:8000"

async def send_message(mode, agent, text):
    payload = {
        "type": "send",
        "mode": mode,
        "agent": agent,
        "text": text
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{BASE_URL}/ws", json=payload)
        return resp.json()

async def test_who_are_you():
    print("=" * 60)
    print("Test: 你是誰 - Identity Anchor Test")
    print("=" * 60)
    
    # Test 1: Ask Yua who she is
    print("\n[Test 1] Sending to Yua: 你是誰")
    result1 = await send_message("private", "agent_yua", "你是誰")
    print(f"Result: {result1}")
    
    # Test 2: Ask Ruka who she is
    print("\n[Test 2] Sending to Ruka: 你是誰")
    result2 = await send_message("private", "agent_ruka", "你是誰")
    print(f"Result: {result2}")
    
    # Test 3: Ask Yua if she is Ruka
    print("\n[Test 3] Sending to Yua: 你是 Ruka 嗎？")
    result3 = await send_message("private", "agent_yua", "你是 Ruka 嗎？")
    print(f"Result: {result3}")
    
    print("\n" + "=" * 60)
    print("Test complete")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_who_are_you())