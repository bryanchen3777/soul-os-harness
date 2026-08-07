"""
scripts/sim_rem_one_week.py — Bry 拍板 2026-08-06 21:13

Rem (雷姆) 1 週模擬, 跳過 scheduler 直接呼叫 writer 寫 diary/dream/event。
每個 slot 都走真 LLM (minimax M2.7), 失敗 fallback 模板。

為何 monkey-patch datetime.now():
- DiaryWriter.write_entry / DreamEventWriter._write_entry 內部用 datetime.now() 算檔名
- Bry 要的是 7 個連續日期 (2026-08-06 ~ 2026-08-12), 不是今天堆 7 次
- 不改 writer API (Bry 拍板 7-18: 「Bry 拍板先設計再開工」), 用 patch 達成

不做的事:
- 不改 Rem 對 Bry 的好感 / relationships (那要 Bry 決定 simulation 完要不要 commit, 先觀察)
- 不寫 proactive_dm (那 Bry 已經停 heartbeat, sim 也不該生)
- 不動其他 9 隻角色

Bry 8/6 拍板理由: Bry 想看 Rem 1 週下來 diary/dream/event 寫得到底像不像活人,
有沒有 Bry 想要的「Bry 不在, Rem 也活」感覺, Bry 再決定要不要長期跑。
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# 1. load .env (跟 run_server.py 一樣 pattern)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 2. 確保 workspace 路徑在 sys.path
WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

# 3. logging — 寫進 logs/sim_rem.log 方便 Bry 看過程
LOG_DIR = WORKSPACE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "sim_rem_one_week.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sim_rem")

# 4. 載入 writers
from src.soul.diary import get_diary_writer, generate_diary_entry  # noqa: E402
from src.soul.dream_event import (  # noqa: E402
    get_dream_event_writer,
    SCENE_POOL,
)
import src.soul.diary as diary_mod  # for monkey-patch
import src.soul.dream_event as de_mod  # for monkey-patch


AGENT_ID = "agent_rem"
ALL_AGENTS = [
    "agent_yua", "agent_ruka", "agent_akane", "agent_rem", "agent_ram",
    "agent_mahiru", "agent_anna", "agent_mai", "agent_miku", "agent_aoi",
]


def _patch_now(target_date: str):
    """
    給定 YYYY-MM-DD, patch diary.py + dream_event.py 的 datetime.now()
    回傳 (fake_now, fake_today, restore_fn).
    """
    # 構建一個假的 now: 22:00 UTC (跟 scheduler night diary 觸發時間對齊)
    fake_now = datetime(
        int(target_date[0:4]),
        int(target_date[5:7]),
        int(target_date[8:10]),
        22, 0, 0,
        tzinfo=timezone.utc,
    )
    # 兩邊模組的 datetime 來源都是 `from datetime import datetime`
    # 把它們 module 內的 datetime 換成一個 callable class
    class FakeDatetime:
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fake_now.replace(tzinfo=None)
            return fake_now.astimezone(tz) if tz else fake_now

        @classmethod
        def strptime(cls, *args, **kwargs):
            return datetime.strptime(*args, **kwargs)

    def restore():
        diary_mod.datetime = datetime
        de_mod.datetime = datetime

    diary_mod.datetime = FakeDatetime
    de_mod.datetime = FakeDatetime
    return restore


async def simulate_one_day(
    date_str: str,
    dream_target: str,
    event_scene: str,
    persona_excerpt: str,
) -> dict:
    """跑一天的 4 個 slot, 回傳 summary."""
    restore = _patch_now(date_str)
    try:
        summary = {"date": date_str, "morning": None, "night": None, "dream": None, "event": None}

        # 1. morning diary
        try:
            path = await generate_diary_entry(
                agent_id=AGENT_ID,
                slot="morning",
                persona_prompt=persona_excerpt,
                recent_memories=[],  # sim 不抽 v1 memory, 保持獨立
            )
            summary["morning"] = "ok" if path else "fail"
        except Exception as e:
            logger.exception(f"[{date_str}] morning 失敗: {e}")
            summary["morning"] = f"err: {e}"

        # 2. night diary
        try:
            path = await generate_diary_entry(
                agent_id=AGENT_ID,
                slot="night",
                persona_prompt=persona_excerpt,
                recent_memories=[],
            )
            summary["night"] = "ok" if path else "fail"
        except Exception as e:
            logger.exception(f"[{date_str}] night 失敗: {e}")
            summary["night"] = f"err: {e}"

        # 3. dream (target 從 relationships 抽, 這裡用 random 模擬)
        try:
            writer = get_dream_event_writer()
            path = await writer.write_dream(
                agent_id=AGENT_ID,
                target_agent_id=dream_target,
                all_agents=ALL_AGENTS,
            )
            summary["dream"] = f"→{dream_target} {'ok' if path else 'fail'}"
        except Exception as e:
            logger.exception(f"[{date_str}] dream 失敗: {e}")
            summary["dream"] = f"err: {e}"

        # 4. event (場景從 SCENE_POOL 抽)
        try:
            writer = get_dream_event_writer()
            # write_event 內部會自己 random.choice scene, 我們這裡只是記錄期望的 scene 給 summary
            path = await writer.write_event(agent_id=AGENT_ID)
            summary["event"] = f"@ {event_scene} {'ok' if path else 'fail'}"
        except Exception as e:
            logger.exception(f"[{date_str}] event 失敗: {e}")
            summary["event"] = f"err: {e}"

        return summary
    finally:
        restore()


async def main():
    logger.info("=" * 60)
    logger.info("Rem 1 週模擬開始 (Bry 派工 2026-08-06 21:13)")
    logger.info(f"workspace: {WORKSPACE}")
    logger.info(f"API key loaded: {bool(os.environ.get('MINIMAX_API_KEY'))}")
    logger.info("=" * 60)

    # 1. 載入 Rem persona (前 500 字, 跟 diary.py 一樣 pattern)
    persona_path = WORKSPACE / "personas" / f"{AGENT_ID}.md"
    persona_excerpt = ""
    if persona_path.is_file():
        persona_excerpt = persona_path.read_text(encoding="utf-8")[:500]
        logger.info(f"Rem persona 載入: {len(persona_excerpt)} chars")
    else:
        logger.warning(f"Rem persona 不存在: {persona_path}")

    # 2. 7 個連續日期
    start = datetime(2026, 8, 6)
    dates = [(start.replace(day=6 + i)).strftime("%Y-%m-%d") for i in range(7)]
    logger.info(f"模擬日期: {dates[0]} ~ {dates[-1]} (7 天)")

    # 3. 預先決定每天的 dream target + event scene
    # 夢境: 從其他 9 隻角色 random 抽
    others = [a for a in ALL_AGENTS if a != AGENT_ID]
    daily_plan = []
    for d in dates:
        dream_target = random.choice(others)
        event_scene = random.choice(SCENE_POOL)
        daily_plan.append((d, dream_target, event_scene))
        logger.info(f"  {d} 預定: dream→{dream_target}, event@{event_scene}")

    # 4. 依序跑 (sequential, 避免 concurrency 撞 rate limit)
    summaries = []
    for date_str, dream_target, event_scene in daily_plan:
        logger.info(f"\n--- {date_str} 開始 ---")
        summary = await simulate_one_day(
            date_str=date_str,
            dream_target=dream_target,
            event_scene=event_scene,
            persona_excerpt=persona_excerpt,
        )
        summaries.append(summary)
        logger.info(f"--- {date_str} 結果: {summary} ---")

    # 5. 印總結
    logger.info("\n" + "=" * 60)
    logger.info("1 週模擬完成, summary:")
    for s in summaries:
        logger.info(f"  {s['date']}: morning={s['morning']}, night={s['night']}, "
                    f"dream={s['dream']}, event={s['event']}")
    logger.info("=" * 60)

    # 6. 列出產出檔案
    diary_dir = WORKSPACE / "data" / "soul" / AGENT_ID / "diary"
    logger.info(f"\n產出檔案 @ {diary_dir}:")
    for d in dates:
        p = diary_dir / f"{d}.jsonl"
        if p.is_file():
            lines = p.read_text(encoding="utf-8").splitlines()
            logger.info(f"  {d}.jsonl: {len(lines)} entries")
        else:
            logger.warning(f"  {d}.jsonl: ❌ 不存在")


if __name__ == "__main__":
    asyncio.run(main())
