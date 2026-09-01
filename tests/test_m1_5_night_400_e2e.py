"""
test_m1_5_night_400_e2e.py — M1.5 10/10 端到端驗證 (Bry 派工 2026-08-06 22:00)

Bry 派工原話: 「修完後不要只驗證單一 agent, 比照這次的教訓, 用會觸發全部
10 隻角色的方式重新驗證, 確認 10/10 都拿到 200 而不是 400」

Bry 派工原話: 「主對話功能修復優先, M0.5 觀察不停, 資源先放在這裡」

驗證方式:
- 對 10 隻角色各發一個 reason=night, mode=group, draft="" 的 AGENT_INTENT
- Mock LLM backend 回固定 text, 避免 529 / 真實 5xx 干擾
- 跑完整 _handle_event_impl pipeline
- 確認每隻角色 (1) 沒 raise 4xx 錯誤 (2) 有 AGENT_SPEAK 發出 (3) _complete_with_retry
  收到的 messages 至少有 1 條 user role

修法前提 (M1.5):
- 317900b placeholder 加在 pop 邏輯內 for loop 內, 只在 user_message 非空時跑
- reason=night + draft="" → user_message="" → 整個 block skip → placeholder 永遠沒加
- 結果: messages 沒 user role → M2.7 endpoint 400 "chat content is empty"
- 規模: 10/10 隻角色全中

修法後 (M1.5):
- placeholder 從 pop 邏輯內拉出來, 條件 `if reason != "user_message"`
- 任何 proactive 觸發都加 placeholder, 跟 user_message 空不空無關
"""
import asyncio
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.schema import SoulEvent, EventType, EventPriority
from src.llm.proxy import LLMProxy


ALL_AGENTS = [
    "agent_yua", "agent_ruka", "agent_akane", "agent_rem", "agent_ram",
    "agent_mahiru", "agent_anna", "agent_mai", "agent_miku", "agent_aoi",
]


class TestM15E2E10Agents(unittest.TestCase):
    """M1.5 修法: 10 隻角色 reason=night 端到端驗證."""

    def _make_proxy(self):
        """建立 LLMProxy 實例, mock backend + bus."""
        proxy = LLMProxy.__new__(LLMProxy)
        proxy.bus = MagicMock()
        proxy.backend = MagicMock()
        proxy.model = "minimax-M2.7"
        proxy.max_tokens = 3000
        proxy.temperature = 0.85
        proxy.max_retries = 1
        proxy.max_history_turns = 10
        proxy.config = {}
        proxy.thinking = None
        proxy.event_max_tokens = 200
        proxy.event_temperature = 0.7
        proxy._memory = MagicMock()
        proxy._history = {}
        proxy._in_flight = set()
        proxy._conversation_dir = None  # sentinel: 走 module-level CONV_DIR (P0 隔離 persistence)
        return proxy

    def _make_event(self, agent_id, reason="night", mode="group", draft=""):
        """構造 AGENT_INTENT 事件."""
        return SoulEvent(
            event_id=f"test-{agent_id}-{reason}",
            event_type=EventType.AGENT_INTENT,
            source="scheduler",
            target=agent_id,
            priority=EventPriority.NORMAL,
            timestamp=datetime.now(timezone.utc),
            payload={
                "agent_id": agent_id,
                "reason": reason,
                "draft": draft,
                "memory_context": "",
                "target_user_id": "bryan",
                "mode": mode,
            },
        )

    async def _run_one_agent(self, proxy, agent_id):
        """跑單隻 agent, mock LLM 確認 messages 結構."""
        event = self._make_event(agent_id)
        # 抓 _complete_with_retry 收到的 messages
        captured = {"messages": None, "calls": 0}
        async def fake_complete(messages, **kwargs):
            captured["messages"] = messages
            captured["calls"] += 1
            # 回傳固定 text, 模擬 LLM 成功
            return "おはよう"
        # mock bus 避免真發布事件
        proxy.bus.publish = AsyncMock()
        # mock RAG + memory writer 避免真寫入
        with patch.object(proxy, "_complete_with_retry", side_effect=fake_complete), \
             patch("src.llm.proxy._parse_llm_output",
                   return_value={"text": "おはよう", "audio_text": "おはよう", "emotion": "calm", "_parse_failed": False}):
            try:
                await proxy._handle_event_impl(event)
            except Exception as e:
                # 4xx 錯誤會 raise 出來 (因為 _complete_with_retry 會 trigger 400)
                # 我們要驗證: 修法後不 raise
                return {"agent_id": agent_id, "ok": False, "error": str(e), "captured": captured}
        return {"agent_id": agent_id, "ok": True, "captured": captured}

    def test_01_all_10_agents_no_4xx_with_night_trigger(self):
        """Bry 派工: 10/10 都拿到 200 而不是 400.

        reason=night + draft="" 對所有 10 隻角色發送 AGENT_INTENT.
        修法前: 10 隻全 400 (M1.5 baseline 已證明).
        修法後: 10 隻都應該正常 call LLM, 不 raise 4xx.
        """
        async def run_all():
            proxy = self._make_proxy()
            results = []
            for agent_id in ALL_AGENTS:
                r = await self._run_one_agent(proxy, agent_id)
                results.append(r)
            return results
        results = asyncio.run(run_all())
        # 統計
        ok_count = sum(1 for r in results if r["ok"])
        fail_count = len(results) - ok_count
        print(f"\n  [M1.5 e2e] 10 隻角色 night 觸發: {ok_count}/10 ok, {fail_count} fail")
        for r in results:
            status = "OK" if r["ok"] else f"FAIL: {r.get('error', '?')[:60]}"
            print(f"    {r['agent_id']:20s}  {status}")
        # Bry 派工: 10/10 都應該 OK
        self.assertEqual(ok_count, 10, f"M1.5 修法後 10/10 應該都正常, 實際 {ok_count}/10")
        # 確認所有 10 隻都有呼叫 _complete_with_retry (沒被 pop 邏輯卡住)
        for r in results:
            if r["ok"]:
                self.assertEqual(r["captured"]["calls"], 1,
                    f"{r['agent_id']} 應該呼叫 _complete_with_retry 1 次")

    def test_02_messages_has_user_role_for_each_agent(self):
        """驗證每隻角色的 messages 至少有 1 條 user role (M2.7 不會 400)."""
        async def run_all():
            proxy = self._make_proxy()
            results = []
            for agent_id in ALL_AGENTS:
                event = self._make_event(agent_id)
                captured = {"messages": None}
                async def fake_complete(messages, **kwargs):
                    captured["messages"] = messages
                    return "OK"
                proxy.bus.publish = AsyncMock()
                with patch.object(proxy, "_complete_with_retry", side_effect=fake_complete), \
                     patch("src.llm.proxy._parse_llm_output",
                           return_value={"text": "OK", "audio_text": "OK", "emotion": "calm", "_parse_failed": False}):
                    await proxy._handle_event_impl(event)
                results.append({"agent_id": agent_id, "messages": captured["messages"]})
            return results
        results = asyncio.run(run_all())
        print(f"\n  [M1.5 e2e] messages user role 統計:")
        for r in results:
            user_count = sum(1 for m in r["messages"] if m["role"] == "user")
            placeholder = any(m.get("content") == "（你主动发起讯息，不是回应任何人）" for m in r["messages"])
            print(f"    {r['agent_id']:20s}  user roles: {user_count}  placeholder: {placeholder}")
            # 每隻角色都應該有 placeholder user role
            self.assertGreaterEqual(user_count, 1, f"{r['agent_id']} 至少 1 條 user role")
            self.assertTrue(placeholder, f"{r['agent_id']} 應該有 placeholder user role")


if __name__ == "__main__":
    print("=" * 60)
    print("M1.5 e2e 10-agent verify (Bry 派工 2026-08-06 22:00)")
    print("=" * 60)
    unittest.main(verbosity=2)
