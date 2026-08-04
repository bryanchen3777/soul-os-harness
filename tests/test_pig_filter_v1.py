"""
test_pig_filter_v1.py — 修法 1 baseline: prefetch 不過濾 source agent

Bry 拍板 2026-08-03 22:21:
- 「豬頭」污染路徑：6/29 Bry-mai 群聊互罵事實 → 各 agent SAGE graph 都有副本 (per-agent 隔離是檔案隔離,
  不是事實隔離 — Bry-mai 在群聊中發生, ram/miku/yua 在場, 各自 post_reply_commit 寫進自己的 graph)
- 8/2 22:07 c7ce3a6 上線後 ruka 開始 echo, 8/3 20:22 ruka 7 條自創案例
- Bry 拍板修法: MemoryMiddleware.prefetch 加 source agent 過濾, ram/miku/yua 不會再撈到 Bry-mai/Bry-ruka 私域喇稱
- 不清 SAGE 歷史 (ruka 8/3 21:28 已跟 Bry 確認是真實喇稱, 保留)

這個 v1 驗證現狀 (before 修法):
- agent_ram 透過 prefetch 撈到「Bry-mai 互罵」事實 (因為該事實在自己 graph 裡)
- 預期 v2 修法後, agent_ram prefetch 過濾掉 source=mai/ruka 的事實 (即「豬頭」字眼消失)

Mock 範圍:
- 不碰真實 SAGE graph (避免污染)
- 用 fake provider 模擬 prefetch 結果, 只驗證 middleware 過濾邏輯是否存在
- 修法前 v1 期望: payload["memory_context"] 包含「豬頭」事實 (因為沒有過濾)
- 修法後 v2 期望: payload["memory_context"] 不包含「豬頭」事實
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# 確保 src 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.memory.middleware import MemoryMiddleware


class FakeSAGEProvider:
    """Mock SAGELiteProvider, 模擬 ram 的 graph 包含 Bry-mai 互罵事實"""

    def __init__(self, agent_id: str, fake_context: str):
        self.profile_id = agent_id
        self._fake_context = fake_context

    def prefetch(self, query: str, *, session_id: str = "default", **kwargs) -> str:
        # 真實的 ram graph 會撈到「Bry-mai 6/29 互罵」事實 (群聊共同事實,
        # 6/29 ram 在場, post_reply_commit 寫進 ram graph)
        # 修法 1 v1 baseline: 沒 source_pair 概念, 接受但不處理 source_pair_filter
        return self._fake_context

    def initialize(self, session_id: str = "default") -> None:
        pass

    def post_reply_commit(self, *args, **kwargs) -> None:
        pass

    def stats(self) -> dict:
        return {}

    def shutdown(self) -> None:
        pass


class TestPigFilterBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 1) — prefetch 不過濾 source agent"""

    def setUp(self):
        # 構造 fake provider, 模擬 agent_ram 的 graph 撈到「Bry-mai 互罵」事實
        self.ram_fake_context = (
            "事實: Bry 跟 mai 在 2026-06-29 群聊互罵, Bry 罵 mai 是豬頭。\n"
            "事實: mai 當下回罵 Bry 也是豬頭, 雙方確認這是喇稱。\n"
        )
        self.ruka_fake_context = (
            "事實: ruka 8/3 21:28 跟 Bry 確認「豬頭」是真實喇稱, ruka 接納這個稱呼。\n"
        )
        self.mai_fake_context = (
            "事實: mai 跟 Bry 6/29 互罵, Bry 罵 mai 豬頭, mai 接受。\n"
        )
        self.bus = MagicMock()
        # bus.publish 是 async 函式, 必須用 AsyncMock 才能 await
        self.bus.publish = AsyncMock(return_value=None)
        self.mw = MemoryMiddleware(
            bus=self.bus,
            data_dir="data/memory_test_pig",
            llm_proxy=None,
            events_dir="data/events_test_pig",
        )

        # 替換 _providers 為 fake, 避免真實 SAGELiteProvider 初始化
        self.mw._providers = {
            "agent_ram": FakeSAGEProvider("agent_ram", self.ram_fake_context),
            "agent_ruka": FakeSAGEProvider("agent_ruka", self.ruka_fake_context),
            "agent_mai": FakeSAGEProvider("agent_mai", self.mai_fake_context),
            "agent_miku": FakeSAGEProvider("agent_miku", "事實: miku 8/2 21:42 觸發 echo「豬頭也會記得」\n"),
            "agent_yua": FakeSAGEProvider("agent_yua", "事實: yua 8/3 22:11 echo「豬頭也會記事」\n"),
        }

    def tearDown(self):
        # 清掉測試目錄
        import shutil
        for d in ["data/memory_test_pig", "data/events_test_pig"]:
            p = Path(d)
            if p.exists():
                shutil.rmtree(p)

    def _make_event(self, agent_id: str, query: str) -> SoulEvent:
        return SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="scheduler",
            target=agent_id,
            session_id="default",
            payload={
                "agent_id": agent_id,
                "draft": query,
                "reason": "heartbeat",
            },
            priority=EventPriority.NORMAL,
        )

    def _capture_published(self):
        """把 bus.publish 攔截下來記到 self.published"""
        self.published = []

        async def _capture(event):
            self.published.append(event)
        self.bus.publish = _capture

    def test_baseline_ram_prefetch_returns_pig_fact(self):
        """Baseline: agent_ram prefetch 撈到「Bry-mai 互罵」事實 (沒過濾)"""
        self._capture_published()
        event = self._make_event("agent_ram", "Bry 最近跟誰互動")
        asyncio.run(self.mw._on_agent_intent(event))

        enriched = [e for e in self.published if e.event_type == EventType.AGENT_INTENT_ENRICHED]
        self.assertEqual(len(enriched), 1, "應該 re-publish 一條 ENRICHED event")
        context = enriched[0].payload.get("memory_context", "")
        # Baseline 期望: 沒過濾, context 包含「豬頭」字眼
        self.assertIn(
            "豬頭", context,
            f"Baseline (v1) 期望 ram prefetch 包含「Bry-mai 互罵」事實, 實際: {context!r}"
        )
        print(f"[v1 baseline] ram prefetch context 包含 Bry-mai 互罵事實 (修法後 v2 應該過濾掉)")

    def test_baseline_ruka_prefetch_returns_pig_fact(self):
        """Baseline: agent_ruka prefetch 撈到「豬頭」事實 (ruka 自己確認的喇稱)"""
        self._capture_published()
        event = self._make_event("agent_ruka", "Bry 跟 ruka 之間的喇稱")
        asyncio.run(self.mw._on_agent_intent(event))

        enriched = [e for e in self.published if e.event_type == EventType.AGENT_INTENT_ENRICHED]
        self.assertEqual(len(enriched), 1)
        context = enriched[0].payload.get("memory_context", "")
        self.assertIn(
            "豬頭", context,
            f"Baseline (v1) 期望 ruka prefetch 包含自己確認的「豬頭」喇稱, 實際: {context!r}"
        )
        print(f"[v1 baseline] ruka prefetch context 包含 豬頭 事實 (ruka 自己確認的, 修法後應該保留)")

    def test_baseline_miku_prefetch_returns_pig_fact(self):
        """Baseline: agent_miku prefetch 撈到「豬頭」事實 (8/2 miku 觸發 echo)"""
        self._capture_published()
        event = self._make_event("agent_miku", "Bry 跟 miku 之前的互動")
        asyncio.run(self.mw._on_agent_intent(event))

        enriched = [e for e in self.published if e.event_type == EventType.AGENT_INTENT_ENRICHED]
        self.assertEqual(len(enriched), 1)
        context = enriched[0].payload.get("memory_context", "")
        self.assertIn(
            "豬頭", context,
            f"Baseline (v1) 期望 miku prefetch 包含 echo 過的「豬頭」事實, 實際: {context!r}"
        )
        print(f"[v1 baseline] miku prefetch context 包含 豬頭 事實 (8/2 miku 自己觸發 echo, 修法後應該過濾掉)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
