"""
test_m7_memory_judge_fire_and_forget.py
M7-latency-fix (Bry 拍板 2026-08-18): 記憶 judge 改 fire-and-forget

驗證 _on_agent_speak 不再同步 await 慢的 post_reply_commit + shadow observe,
而是把它們包成背景 task, handler 立刻回傳, 不再阻塞單 worker event bus。

背景 (Bry 8/18 麻衣對話延時事件): LLM judge 12+ 次串行 call (~20-70s)
卡住 event bus, 導致 user_message 延遲 ~73s。
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.memory.middleware import MemoryMiddleware


class SlowFakeProvider:
    """post_reply_commit 模擬慢 (sleep 2s), 記錄被呼叫次數與完成次數。"""

    def __init__(self):
        self.post_reply_called = 0
        self.post_reply_done = 0

    def prefetch(self, *args, **kwargs):
        return ""

    def initialize(self, *args, **kwargs):
        pass

    def stats(self):
        return {}

    def shutdown(self):
        pass

    async def post_reply_commit(self, *args, **kwargs):
        self.post_reply_called += 1
        await asyncio.sleep(2.0)
        self.post_reply_done += 1


def _make_mw(tmp_path):
    bus = MagicMock()
    bus.publish = AsyncMock(return_value=None)
    mw = MemoryMiddleware(
        bus=bus,
        data_dir=str(tmp_path / "mem"),
        llm_proxy=None,
        events_dir=str(tmp_path / "events"),
    )
    mw._relationships_manager = None
    return mw


def _make_speak_event(agent_id="agent_mai"):
    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source=agent_id,
        target="broadcast",
        session_id="sess",
        payload={
            "agent_id": agent_id,
            "text": "測試回覆內容",
            "target_user_id": "bryan",
        },
        priority=EventPriority.NORMAL,
    )


class TestAgentSpeakFireAndForget:
    def test_on_agent_speak_returns_quickly(self, tmp_path, monkeypatch):
        """_on_agent_speak 應立刻回傳 (不 await 慢的 post_reply_commit)。"""
        import src.memory.shadow as shadow_mod

        async def noop_observe(*args, **kwargs):
            return None

        monkeypatch.setattr(shadow_mod, "maybe_observe", noop_observe)

        mw = _make_mw(tmp_path)
        provider = SlowFakeProvider()
        mw._providers = {"agent_mai": provider}
        mw._pending_user_text["sess"] = "user text"

        async def _run():
            t0 = time.monotonic()
            await mw._on_agent_speak(_make_speak_event())
            elapsed = time.monotonic() - t0
            # 給背景 task 時間跑完 (post_reply_commit sleep 2s)
            await asyncio.sleep(3.0)
            return elapsed

        elapsed = asyncio.run(_run())
        assert elapsed < 1.0, (
            f"_on_agent_speak 應 fire-and-forget (立刻回傳), 實際耗時 {elapsed:.2f}s"
        )

    def test_post_reply_commit_still_runs_in_background(self, tmp_path, monkeypatch):
        """fire-and-forget 後, post_reply_commit 仍會在背景完成。"""
        import src.memory.shadow as shadow_mod

        async def noop_observe(*args, **kwargs):
            return None

        monkeypatch.setattr(shadow_mod, "maybe_observe", noop_observe)

        mw = _make_mw(tmp_path)
        provider = SlowFakeProvider()
        mw._providers = {"agent_mai": provider}
        mw._pending_user_text["sess"] = "user text"

        async def _run():
            await mw._on_agent_speak(_make_speak_event())
            await asyncio.sleep(3.0)

        asyncio.run(_run())
        assert provider.post_reply_called == 1, "post_reply_commit 應該被呼叫"
        assert provider.post_reply_done == 1, "post_reply_commit 應該在背景完成"
