"""
tests/test_persona_fix3_ruka_heartbeat_gate.py — PERSONA-FIX-3 (Ruka heartbeat session-once gate)

驗收對象: src/llm/proxy.py
  - constants: RUKA_HEARTBEAT_EMOTION="heartbeat" / RUKA_HEARTBEAT_DOWNGRADE_EMOTION="approaching"
  - gate: enforce_ruka_heartbeat_session_once(emotion, session_id, fired_sessions) -> str
  - session 身份: _session_key(agent_id, user_id) -> f"session_{user_id}_{agent_id}"
    與 AGENT_SPEAK payload 的 session_id 同源 (proxy.py:3858)
  - 配線 (proxy.py:3829-3840): 只在 agent_id == "agent_ruka" 時呼叫 gate,
    其餘角色 (agent_yua/agent_mahiru/...) 根本不進呼叫點, emotion 一字不動

斷言 (工單驗收):
  1. 同 session 第一次 heartbeat → 原樣放行 (不降級)
  2. 同 session 第二次 heartbeat → 降級為 approaching
  3. 其他 agent (agent_yua / agent_mahiru) 連續多次 heartbeat → 完全不受影響 (隔離性)
  4. 換一個 session → 又允許一次
  5. 非 heartbeat emotion → 原樣, 不記錄不污染

全離線: 只 import proxy 模組的純函式, 0 網路、0 資料夾副作用。
"""
import sys
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')  # PowerShell cp950 不能編碼中文
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.proxy import (
    RUKA_HEARTBEAT_DOWNGRADE_EMOTION,
    RUKA_HEARTBEAT_EMOTION,
    _session_key,
    enforce_ruka_heartbeat_session_once,
)


def _dispatch_gate(agent_id, emotion, user_id, fired_sessions):
    """複刻 _handle_event_impl (proxy.py:3829-3840) 的實際配線分支:
    只有 agent_ruka 進 gate, 其餘角色 emotion 一字不動。"""
    if agent_id == "agent_ruka":
        return enforce_ruka_heartbeat_session_once(
            emotion, _session_key(agent_id, user_id), fired_sessions
        )
    return emotion


class TestRukaHeartbeatSessionOnceGate(unittest.TestCase):
    """gate 純函式單元測試 (session id 用 production 的 _session_key 產生)"""

    def test_first_heartbeat_same_session_passes(self):
        """驗收 1: 同 session 第一次 heartbeat → 原樣放行 + 記錄 session"""
        fired = set()
        sid = _session_key("agent_ruka", "bryan")
        result = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, sid, fired
        )
        self.assertEqual(result, RUKA_HEARTBEAT_EMOTION)
        self.assertIn(sid, fired)

    def test_second_heartbeat_same_session_downgraded(self):
        """驗收 2: 同 session 第二次 heartbeat → 降級為 approaching"""
        fired = set()
        sid = _session_key("agent_ruka", "bryan")
        enforce_ruka_heartbeat_session_once(RUKA_HEARTBEAT_EMOTION, sid, fired)
        result = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, sid, fired
        )
        self.assertEqual(result, RUKA_HEARTBEAT_DOWNGRADE_EMOTION)

    def test_further_heartbeats_still_downgraded(self):
        """同 session 第三次(含以後) 仍降級, 不是二過一就放行"""
        fired = set()
        sid = _session_key("agent_ruka", "bryan")
        for _ in range(3):
            enforce_ruka_heartbeat_session_once(RUKA_HEARTBEAT_EMOTION, sid, fired)
        result = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, sid, fired
        )
        self.assertEqual(result, RUKA_HEARTBEAT_DOWNGRADE_EMOTION)

    def test_new_session_allows_heartbeat_once(self):
        """驗收 4: 換一個 session (不同 user) → 又允許一次, 不互相污染"""
        fired = set()
        sid_a = _session_key("agent_ruka", "bryan")
        sid_b = _session_key("agent_ruka", "yui")
        result_a = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, sid_a, fired
        )
        result_b = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, sid_b, fired
        )
        self.assertEqual(result_a, RUKA_HEARTBEAT_EMOTION)
        self.assertEqual(result_b, RUKA_HEARTBEAT_EMOTION)
        self.assertEqual(fired, {sid_a, sid_b})

    def test_non_heartbeat_emotion_passthrough_unrecorded(self):
        """非 heartbeat emotion → 原樣回傳, 不記錄 (不污染 fired set)"""
        fired = set()
        sid = _session_key("agent_ruka", "bryan")
        result = enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_DOWNGRADE_EMOTION, sid, fired
        )
        self.assertEqual(result, RUKA_HEARTBEAT_DOWNGRADE_EMOTION)
        self.assertEqual(fired, set())


class TestRukaHeartbeatGateIsolation(unittest.TestCase):
    """驗收 3 (最重要): 其他 agent 完全不受影響 — 走 production 配線分支"""

    def test_agent_yua_heartbeats_unaffected(self):
        """agent_yua 連續 5 次 heartbeat → 全部原樣 (產線分支根本不會呼叫 gate)"""
        fired = set()
        for _ in range(5):
            result = _dispatch_gate(
                "agent_yua", RUKA_HEARTBEAT_EMOTION, "bryan", fired
            )
            self.assertEqual(result, RUKA_HEARTBEAT_EMOTION)
        self.assertEqual(fired, set())

    def test_agent_mahiru_heartbeats_unaffected(self):
        """agent_mahiru 連續 5 次 heartbeat → 全部原樣"""
        fired = set()
        for _ in range(5):
            result = _dispatch_gate(
                "agent_mahiru", RUKA_HEARTBEAT_EMOTION, "bryan", fired
            )
            self.assertEqual(result, RUKA_HEARTBEAT_EMOTION)
        self.assertEqual(fired, set())

    def test_ruka_gate_does_not_affect_other_agents_after_firing(self):
        """ruka 的 session gate 觸發後, yua/mahiru 的 heartbeat 依然原樣
        (gate 只認 ruka 的 session key, 其他 agent 的 session 永不碰撞)"""
        fired = set()
        ruka_sid = _session_key("agent_ruka", "bryan")
        enforce_ruka_heartbeat_session_once(
            RUKA_HEARTBEAT_EMOTION, ruka_sid, fired
        )
        for agent_id in ("agent_yua", "agent_mahiru"):
            result = _dispatch_gate(
                agent_id, RUKA_HEARTBEAT_EMOTION, "bryan", fired
            )
            self.assertEqual(result, RUKA_HEARTBEAT_EMOTION)
        # ruka 的 fired session 記錄也只含 ruka 的 session, 沒有其他 agent 污染
        self.assertEqual(fired, {ruka_sid})


if __name__ == "__main__":
    unittest.main()