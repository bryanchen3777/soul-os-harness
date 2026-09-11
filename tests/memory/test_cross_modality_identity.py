"""
MEM-IDENTITY-1 — 跨模態身分正規化測試（VC ↔ TG/Web 記憶互相可見）

背景：
  三模態共用同一個 SAGE graph.sqlite。VC 寫側硬編碼 "bryan:<agent_id>"，
  TG/Web inbound 帶數字 id 1696287850 → 寫成 "1696287850:<agent_id>"，
  讀側 filter 只放行單一身分 → 語音↔文字記憶互相遮蔽。

本測試驗證：
  - canonical_user_id / source_pair_candidates 的正規化與陌生人隔離
  - MemoryMiddleware 寫入側以 canonical id 組 source_pair
  - MemoryMiddleware 讀側 (prefetch) 傳給 reader 的 filter 含全部已知身分別
  - 真實 MemoryReader 下，TG 回合 filter 同時保留舊 (1696287850:*) 與新
    (bryan:*) 資料，陌生人 fact 被濾掉
"""

import asyncio

from src.eventbus.schema import EventType, SoulEvent
from src.memory.middleware import MemoryMiddleware
from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact
from src.memory.sage.reader import MemoryReader
from src.memory.user_identity import (
    CANONICAL_USER_ID,
    canonical_user_id,
    source_pair_candidates,
    user_identity_aliases,
)


class _FakeBus:
    """最小 event bus 替身（enrich re-publish 用）。"""

    def __init__(self) -> None:
        self.published: list[SoulEvent] = []

    async def publish(self, event: SoulEvent) -> None:
        self.published.append(event)


class _CaptureWriteProvider:
    """捕捉 post_reply_commit 收到的 source_pair（不跑 LLM / 不碰資料）。"""

    def __init__(self) -> None:
        self.captured: dict = {}

    async def post_reply_commit(
        self,
        session_id,
        user_text,
        agent_text,
        *,
        source_pair=None,
        inner_life_event_id=None,
    ) -> None:
        self.captured["source_pair"] = source_pair

    def prefetch(self, query, **kwargs):
        return ""


class _CapturePrefetchProvider:
    """捕捉 prefetch 收到的 source_pair_filter。"""

    def __init__(self) -> None:
        self.captured: dict = {}

    def prefetch(self, query, *, session_id, boost_tags=None, source_pair_filter=None):
        self.captured["filter"] = source_pair_filter
        return ""

    async def post_reply_commit(self, *args, **kwargs):
        return None


def _middleware(tmp_path, provider) -> MemoryMiddleware:
    mem = MemoryMiddleware(bus=_FakeBus(), data_dir=str(tmp_path / "memory"))
    mem._relationships_manager = None  # 測試隔離：不碰 production relationships
    mem._providers["agent_x"] = provider
    return mem


# ── 1. canonical 收斂（int / str / 大小寫）────────────────────────

def test_canonical_user_id_known_bryan_aliases():
    assert canonical_user_id(1696287850) == "bryan"
    assert canonical_user_id("1696287850") == "bryan"
    assert canonical_user_id("bryan") == "bryan"
    assert canonical_user_id("user_bryan") == "bryan"


def test_canonical_user_id_case_insensitive():
    assert canonical_user_id("BRYAN") == "bryan"
    assert canonical_user_id("User_Bryan") == "bryan"
    assert canonical_user_id("1696287850") == "bryan"


# ── 2. 陌生人隔離不被破壞 ──────────────────────────────────────────

def test_canonical_user_id_stranger_unchanged():
    assert canonical_user_id("99999999") == "99999999"
    assert canonical_user_id(99999999) == "99999999"


# ── 3. None / 空 → canonical ───────────────────────────────────────

def test_canonical_user_id_none_and_empty():
    assert canonical_user_id(None) == CANONICAL_USER_ID
    assert canonical_user_id("") == CANONICAL_USER_ID
    assert canonical_user_id("   ") == CANONICAL_USER_ID


# ── 4. source_pair_candidates（Bryan 三身分）───────────────────────

def test_source_pair_candidates_bryan_all_aliases():
    assert source_pair_candidates(1696287850, "agent_rem") == {
        "bryan:agent_rem",
        "user_bryan:agent_rem",
        "1696287850:agent_rem",
    }
    # 別名表拍板內容（含 canonical 自身）
    assert user_identity_aliases("bryan") >= {"bryan", "user_bryan", "1696287850"}


# ── 5. 陌生人隔離（不含任何 bryan 身分）────────────────────────────

def test_source_pair_candidates_stranger_isolated():
    assert source_pair_candidates("99999999", "agent_rem") == {"99999999:agent_rem"}


# ── 6. 寫側：target_user_id=1696287850 → source_pair="bryan:agent_x" ─

def test_write_side_source_pair_canonicalized(tmp_path):
    """TG/Web (1696287850) 回合寫入 → source_pair 為 canonical "bryan:agent_x"。"""
    provider = _CaptureWriteProvider()
    mem = _middleware(tmp_path, provider)

    async def run():
        await mem.handle_event(SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source="tg:1696287850",
            target="agent_x",
            session_id="session_1696287850_agent_x",
            payload={"text": "嗨", "target_user_id": 1696287850},
        ))
        await mem.handle_event(SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source="agent_x",
            target="broadcast",
            session_id="session_1696287850_agent_x",
            payload={
                "text": "你好",
                "agent_id": "agent_x",
                "target_user_id": 1696287850,
            },
        ))
        # fire-and-forget managed task：給它時間跑到 fake post_reply_commit
        for _ in range(50):
            if provider.captured.get("source_pair"):
                break
            await asyncio.sleep(0.01)

    asyncio.run(run())
    assert provider.captured.get("source_pair") == "bryan:agent_x"


# ── 7. 讀側：prefetch 的 source_pair_filter 含全部已知身分別 ────────

def test_read_side_source_pair_filter_contains_all_aliases(tmp_path):
    """TG/Web (1696287850) 回合 prefetch → reader 收到 bryan + 1696287850 候選。"""
    provider = _CapturePrefetchProvider()
    mem = _middleware(tmp_path, provider)

    async def run():
        await mem.handle_event(SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="tg:1696287850",
            target="agent_x",
            session_id="session_1696287850_agent_x",
            payload={
                "agent_id": "agent_x",
                "target_user_id": 1696287850,
                "draft": "今天做了什麼",
            },
        ))

    asyncio.run(run())
    assert provider.captured["filter"] == {
        "bryan:agent_x",
        "user_bryan:agent_x",
        "1696287850:agent_x",
    }


# ── 8. 跨模態可見性矩陣（真實 MemoryReader + tmp graph.sqlite）─────

def test_cross_modality_visibility_matrix(tmp_path):
    """TG 回合 filter 下：舊 (1696287850:*) 與新 (bryan:*) 都保留、陌生人被濾。"""
    agent_dir = tmp_path / "memory" / "agent_rem"
    agent_dir.mkdir(parents=True, exist_ok=True)
    store = GraphStore(agent_dir / "graph.sqlite")
    try:
        store.add_fact(Fact(
            fact_id="f_tg_old",
            subject="Bry", predicate="散步", object="公園",
            weight=1.0, confidence=1.0, source="user",
            session_id="s_tg", source_pair="1696287850:agent_rem",  # TG/Web 舊資料
        ))
        store.add_fact(Fact(
            fact_id="f_vc_new",
            subject="Bry", predicate="爬山", object="觀音山",
            weight=1.0, confidence=1.0, source="user",
            session_id="s_vc", source_pair="bryan:agent_rem",  # VC 新資料 (canonical)
        ))
        store.add_fact(Fact(
            fact_id="f_stranger",
            subject="Bry", predicate="打麻將", object="朋友",
            weight=1.0, confidence=1.0, source="user",
            session_id="s_x", source_pair="99999999:agent_rem",  # 陌生人
        ))
        store.flush()

        reader = MemoryReader(store)
        result = reader.retrieve_context(
            "Bry 最近做什麼",
            top_k=10, max_hops=2, max_tokens=800,
            source_pair_filter=source_pair_candidates(1696287850, "agent_rem"),
        )
        pairs = {f.source_pair for f in result.facts}
        assert "1696287850:agent_rem" in pairs, (
            f"舊 TG/Web 資料被濾掉: pairs={pairs!r}"
        )
        assert "bryan:agent_rem" in pairs, (
            f"新 VC 資料被濾掉: pairs={pairs!r}"
        )
        assert "99999999:agent_rem" not in pairs, (
            f"陌生人 fact 未被濾掉: pairs={pairs!r}"
        )
    finally:
        store.close()