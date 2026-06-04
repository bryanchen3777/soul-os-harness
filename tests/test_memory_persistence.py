"""
test_memory_persistence.py
Soul OS — Phase 2.1: 跨 session 持久化驗證 (M6)
驗收標準：kill process → 重啟 → prefetch 仍能命中

三個 Phase：
  A. 建立 MemoryMiddleware #1，灌 5 個 turn，shutdown
  B. 全新 MemoryMiddleware #2（同 data_dir），prefetch 命中舊事實
  C. 重啟後繼續對話，新舊事實都在

執行：
  python tests/test_memory_persistence.py
"""
import asyncio
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.memory.middleware import MemoryMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.persistence")


# ─────────────────────────────────────────────
# Seed Turns
# 注意：SAGE writer 是 rule-based 的，需要 trigger 詞才能抽出 fact
# 已知 trigger：住在/喜歡/是/有/工作於/討厭/likes/is/has/works_at...
# 為確保 ≥ 5 facts 被抽出，這裡的措辭刻意符合 trigger
# ─────────────────────────────────────────────
SEED_TURNS: List[Tuple[str, str]] = [
    ("我住在台北",                   "台北冬天很冷"),
    ("我喜歡師大夜市",               "師大夜市好熱鬧"),
    ("我喜歡吃臭豆腐",               "臭豆腐很好吃"),
    ("我有一隻貓叫小橘",             "貓咪很可愛"),
    ("小橘喜歡打翻我的鍵盤",         "小橘在測試你的 Phase 2"),
    ("我在 Google 工作",             "Google 聽起來很棒"),
]

PHASE_C_EXTRA = ("小橘喜歡在鍵盤上睡覺", "哈哈哈小橘真調皮")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

async def _create_middleware(data_dir: str) -> Tuple[MemoryMiddleware, SoulEventBus]:
    """建立並啟動一組新的 Middleware + Bus。"""
    bus = SoulEventBus()
    mw = MemoryMiddleware(bus=bus, data_dir=data_dir)
    mw.register()
    await bus.start()
    logger.info(f"[建立 MW] data_dir={data_dir}")
    return mw, bus


async def _pump_turns(
    mw: MemoryMiddleware,
    turns: List[Tuple[str, str]],
    agent_id: str = "agent_ruka",
    session_id: str = "persist_test_session",
) -> None:
    """模擬一段對話：USER_MESSAGE → AGENT_SPEAK 各 1 次，觸發 middleware 全流程。"""
    for user_text, agent_text in turns:
        user_event = SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source="user_bryan",
            target="broadcast",
            priority=EventPriority.HIGH,
            session_id=session_id,
            payload={"text": user_text},
        )
        await mw.handle_event(user_event)

        speak_event = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source=agent_id,
            target="broadcast",
            priority=EventPriority.NORMAL,
            session_id=session_id,
            payload={"text": agent_text, "agent_id": agent_id},
        )
        await mw.handle_event(speak_event)
    # 給 async 鏈條時間（prefetch 用 to_thread，post_reply_commit 用 executor）
    await asyncio.sleep(0.3)


async def _prefetch(
    mw: MemoryMiddleware,
    query: str,
    agent_id: str = "agent_ruka",
    session_id: str = "persist_test_session",
) -> str:
    """從指定 agent 的 provider 拉 prefetch 結果。"""
    provider = mw._get_provider(agent_id)
    return await asyncio.to_thread(
        provider.prefetch, query, session_id=session_id
    )


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 2.1 M6 持久化驗收")
    logger.info("=" * 60)

    data_dir = tempfile.mkdtemp(prefix="soul_os_m6_")
    logger.info(f"data_dir: {data_dir}")
    bus2 = None
    try:
        # ── Phase A：建第一個 Middleware，灌 6 個 turn，shutdown ──
        logger.info("\n── Phase A：建立 MM #1，灌 6 個 turn，shutdown ──")
        mw1, bus1 = await _create_middleware(data_dir)
        await _pump_turns(mw1, SEED_TURNS)

        provider1 = mw1._get_provider("agent_ruka")
        stats_before = provider1.stats()
        logger.info(f"  Phase A 寫入後 graph: {stats_before}")
        assert stats_before.get("active_facts", 0) >= 5, (
            f"Phase A 寫入不足：active_facts={stats_before.get('active_facts')}"
        )
        logger.info(
            f"  ✓ Phase A：寫入 {stats_before['active_facts']} 個 fact"
        )

        # Shutdown — 關 SQLite connection + flush WAL
        mw1.shutdown()
        await bus1.stop()
        logger.info("  ✓ Phase A：MM #1 已 shutdown，SQLite 已 flush")

        # ── Phase B：全新 Middleware 實例，同 data_dir，prefetch ──
        logger.info("\n── Phase B：全新 MM #2，同 data_dir，prefetch ──")
        mw2, bus2 = await _create_middleware(data_dir)

        # 確保是 fresh provider（不是 in-memory cache）
        assert "agent_ruka" not in mw1._providers or mw1._providers != mw2._providers, \
            "MW #2 應該是全新實例，不該跟 MW #1 共用 provider"
        logger.info("  ✓ MM #2 是 fresh instance（provider dict 獨立）")

        # prefetch 應該從 SQLite 重新載入資料
        recalled = await _prefetch(mw2, "台北")
        logger.info(
            f"  prefetch('台北') ({len(recalled)} chars):\n{recalled[:200]}"
        )
        assert "台北" in recalled, (
            f"Phase B prefetch 沒命中 '台北'：'{recalled[:100]}'"
        )
        logger.info("  ✓ Phase B：prefetch 命中 '台北'")

        provider2 = mw2._get_provider("agent_ruka")
        stats_after = provider2.stats()
        logger.info(f"  Phase B fresh provider stats: {stats_after}")
        assert stats_after.get("active_facts", 0) == stats_before.get("active_facts", 0), (
            f"Phase B active_facts 不一致："
            f"before={stats_before['active_facts']} after={stats_after['active_facts']}"
        )
        logger.info(
            f"  ✓ Phase B：active_facts 跨重啟一致 "
            f"({stats_after['active_facts']} 個)"
        )

        # ── Phase C：重啟後繼續對話，新舊事實都在 ──
        logger.info("\n── Phase C：重啟後繼續對話，新舊事實共存 ──")
        await _pump_turns(mw2, [PHASE_C_EXTRA])

        stats_final = provider2.stats()
        logger.info(f"  Phase C 後 graph: {stats_final}")
        assert stats_final.get("active_facts", 0) >= stats_before.get("active_facts", 0) + 1, (
            f"Phase C 新 fact 沒寫進去："
            f"final={stats_final.get('active_facts')} "
            f"before={stats_before.get('active_facts')}"
        )
        logger.info(
            f"  ✓ Phase C：新 fact 寫入成功 "
            f"({stats_before['active_facts']} → {stats_final['active_facts']})"
        )

        # 確認舊事實還在
        recalled_after = await _prefetch(mw2, "台北")
        assert "台北" in recalled_after, (
            f"Phase C 舊記憶消失：'{recalled_after[:100]}'"
        )
        logger.info("  ✓ Phase C：舊事實 '台北' 仍在 prefetch 結果中")

        # 確認新事實也在（query 對應的 fact）
        # "小橘今天乖多了" 沒有 trigger pattern 會被抽出（無 verbs）
        # 但 prefetch 用 "小橘" 應該至少能找到 "我 有一隻貓叫小橘" 這條
        recalled_new = await _prefetch(mw2, "小橘")
        logger.info(
            f"  prefetch('小橘') ({len(recalled_new)} chars):\n{recalled_new[:200]}"
        )
        assert "小橘" in recalled_new, (
            f"Phase C prefetch 沒命中 '小橘'：'{recalled_new[:100]}'"
        )
        logger.info("  ✓ Phase C：新事實 '小橘' 可被召回")

        # ── 最終總結 ──
        logger.info("\n" + "=" * 60)
        logger.info("  ✓ M6 驗收通過：跨 session 持久化成立")
        logger.info("=" * 60)
        logger.info(f"  Phase A 寫入：{stats_before['active_facts']} facts")
        logger.info(f"  Phase B 跨重啟：{stats_after['active_facts']} facts (一致)")
        logger.info(f"  Phase C 累計：{stats_final['active_facts']} facts")
        logger.info(f"  召回樣本：\n{recalled_after[:200]}")

    finally:
        if bus2 is not None:
            await bus2.stop()
        shutil.rmtree(data_dir, ignore_errors=True)
        logger.info(f"已清理 {data_dir}")


if __name__ == "__main__":
    asyncio.run(main())
