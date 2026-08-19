"""
test_m7_continuity_self_turn.py
M7-continuity (Bry 拍板 2026-08-18): 角色近期發言注入 (避免自我矛盾)

驗證:
  A. _load_self_recent 過濾 role='assistant' + 取最近 N 條
  B. _build_messages_private 注入「你最近的發言」區塊
"""
from unittest.mock import MagicMock

from src.llm import proxy as proxy_mod
from src.llm.proxy import _load_self_recent, _build_messages_private


class TestLoadSelfRecent:
    def test_filters_assistant_only(self, monkeypatch):
        monkeypatch.setattr(
            proxy_mod,
            "_load_private",
            lambda a, u: [
                {"role": "user", "content": "Bry 說的話"},
                {"role": "assistant", "content": "角色說 A"},
                {"role": "assistant", "content": "角色說 B"},
            ],
        )
        out = _load_self_recent("agent_mai", "bryan", limit=3)
        assert [m["content"] for m in out] == ["角色說 A", "角色說 B"]

    def test_limits_to_last_n(self, monkeypatch):
        monkeypatch.setattr(
            proxy_mod,
            "_load_private",
            lambda a, u: [{"role": "assistant", "content": f"第{i}句"} for i in range(5)],
        )
        out = _load_self_recent("agent_mai", "bryan", limit=3)
        assert [m["content"] for m in out] == ["第2句", "第3句", "第4句"]

    def test_exception_returns_empty(self, monkeypatch):
        def boom(a, u):
            raise Exception("boom")

        monkeypatch.setattr(proxy_mod, "_load_private", boom)
        assert _load_self_recent("agent_mai", "bryan") == []


class TestSelfTurnInjection:
    def test_build_private_injects_self_turn_block(self, monkeypatch):
        """_build_messages_private 有 self_recent 時, 應注入「你最近的發言」。"""
        # 把 format helpers 都 mock 掉, 只測 self-turn 注入
        monkeypatch.setattr(proxy_mod, "emotion_engine", type("E", (), {"mood_description": lambda self, m: ""})())
        monkeypatch.setattr(proxy_mod, "_format_relationship_block", lambda a: "")
        monkeypatch.setattr(proxy_mod, "_format_recent_inner_life", lambda a: "")
        monkeypatch.setattr(proxy_mod, "_compute_silence_str", lambda t, n: None)
        monkeypatch.setattr(proxy_mod, "_load_self_recent", lambda a, u, limit: [
            {"role": "assistant", "content": "我兩天沒消息了"},
        ])

        mem = MagicMock()
        mem.get_recent_with_meta.return_value = []

        msgs = _build_messages_private(
            "agent_mai", "SOUL", "", "", mem, user_id="bryan"
        )
        system_content = msgs[0]["content"]
        assert "你最近的發言" in system_content
        assert "我兩天沒消息了" in system_content
        assert "不要否認" in system_content
