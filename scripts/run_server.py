#!/usr/bin/env python3
"""
Soul OS — 主啟動入口
啟動 Event Bus + 所有模組 + FastAPI WebSocket Gateway
"""
import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

# 確保 configs/ 和 src/ 可以被找到
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

# === Faulthandler: 沉默死亡屍檢 (Lesson 38 2026-07-30 Bry 拍板) ===
# 動機：Soul OS 在 7/27 22:07 - 7/29 23:47 沉默死亡 47 小時，
#       .err log 完全沒有 traceback,Windows 事件也沒有 shutdown 訊號。
#       下次再死時要能直接看到卡在哪個 await。
#
# 三個機制（互補）:
#   1. faulthandler.enable() 攔 C-level crash（segfault / C extension panic）
#   2. faulthandler.dump_traceback_later(60s, repeat=True) 獨立 thread 每 60 秒
#      抓所有 thread 的 stack trace，**即使 asyncio event loop 卡死也能寫**
#      （Windows 用 thread + WaitForSingleObject，不是 signal,不受 signal 限制）
#   3. 下方 _heartbeat_dumper() 在 lifespan 啟動：asyncio-based,
#      每次 dump 覆寫 heartbeat_trace.log（只留最近一份,可讀性高），
#      loop 死了就只靠 #2 的 append 檔
#
# 檔案控制代碼是**模組層級變數**,**不能**放在函式內（會被 GC 導致 dump 寫到關閉的 handle）
import faulthandler

_FAULTHANDLER_PATH = _root / "data" / "faulthandler.log"
_FAULTHANDLER_PATH.parent.mkdir(parents=True, exist_ok=True)
_FAULTHANDLER_FILE = open(_FAULTHANDLER_PATH, "a", encoding="utf-8", buffering=1)  # line-buffered
faulthandler.enable(file=_FAULTHANDLER_FILE)
# 60 秒後第一次 dump,之後每 60 秒重複。檔案用 append,自然按時間順序排列。
faulthandler.dump_traceback_later(timeout=60, repeat=True, file=_FAULTHANDLER_FILE)
# Rotate 提醒：這個檔案會一直 append,如果手動看時太大,直接砍掉重來就好
# (下次 dump 會重新建立檔案 append,不會丟歷史以外的內容)

logger = logging.getLogger("soul_os.server")

# Phase 5b：基本 logging config，否則 logger.info 全部吞掉
# uvicorn 預設只接 WARNING+，需要明確指定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# Phase 5b：抑制 httpx/telegram 把 URL（含 bot token）印進 log
# 4-strike 教訓：python-telegram-bot 預設 INFO 級別會在每個 HTTP
# request log 帶完整 https://api.telegram.org/bot<TOKEN>/...
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Phase 5b：load .env（讓 TELEGRAM_BOT_* / MINIMAX_API_KEY 等生效）
# 沒 .env 也不報錯
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class MockLLMBackend:
    """
    階段 3+ 升級版（2026-07-14 Bry 拍板）：
    10 個角色都回 v2 schema JSON（含日文台詞 + emotion）,
    跟 stage3/stage4 E2E 測試的 mock 風格一致

    使用方式：
      - 沒設 LLM_API_KEY → 自動用這個 mock（run_server.py lifespan 內有 fallback 邏輯）
      - 設了 LLM_API_KEY → 用 real LLM backend（這個 mock 不會跑）
    """
    async def complete(self, messages, model, max_tokens, temperature):
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")

        # 嚴格前綴匹配,避免 Ram/Rem 互觸發
        agent_id = ""
        for marker in [
            "agent_rem", "agent_aoi", "agent_ram",
            "agent_yua", "agent_ruka", "agent_akane", "agent_mahiru",
            "agent_anna", "agent_mai", "agent_miku",
        ]:
            if marker in sys_content[:200]:
                agent_id = marker
                break

        print(f"[MockLLM] matched agent_id={agent_id!r}", flush=True)

        # 10 角色 v2 schema JSON
        # text = 中文翻譯（給 UI）
        # audio_text = 日文台詞（給 Fish TTS 合成 + 給 LLMProxy audio_text 欄位）
        # emotion = 從 build_system_prompt.py 白名單中選一個（trigger 階段 5 emotion marker 對應）
        mock_responses = {
            "agent_yua": (
                '{"text": "……嗯，我聽著。", '
                '"audio_text": "[warm tone] ……うん、聞いてるよ。", '
                '"emotion": "connecting"}'
            ),
            "agent_ruka": (
                '{"text": "你去哪裡了！", '
                '"audio_text": "[confident] どこ行ってたの！ずっと待ってたのに！", '
                '"emotion": "approaching"}'
            ),
            "agent_akane": (
                '{"text": "……茜在的。", '
                '"audio_text": "[calm] ……あかね、いるよ。", '
                '"emotion": "observing"}'
            ),
            "agent_rem": (
                '{"text": "——嗯，我一直都在的。", '
                '"audio_text": "[gentle and devoted tone] ——うん、ブレイアン。レムはずっとそばにいるよ。", '
                '"emotion": "devotion_active"}'
            ),
            "agent_ram": (
                '{"text": "——是這樣。", '
                '"audio_text": "[calm] ——そう。", '
                '"emotion": "observing"}'
            ),
            "agent_mahiru": (
                '{"text": "……哈囉。", '
                '"audio_text": "[chuckling] ……やあ。", '
                '"emotion": "teasing_care"}'
            ),
            "agent_anna": (
                '{"text": "えへへ、你好啊！", '
                '"audio_text": "[happy] えへへ、やあ！", '
                '"emotion": "bright"}'
            ),
            "agent_mai": (
                '{"text": "……嗯。", '
                '"audio_text": "[calm] ……うん。", '
                '"emotion": "dry_care"}'
            ),
            "agent_miku": (
                '{"text": "……", '
                '"audio_text": "……", '
                '"emotion": "silent"}'
            ),
            "agent_aoi": (
                '{"text": "……這樣啊。", '
                '"audio_text": "[calm] ……そう。", '
                '"emotion": "aoi_stable"}'
            ),
        }
        if agent_id in mock_responses:
            return mock_responses[agent_id]
        # fallback
        return '{"text": "[MOCK] fallback", "audio_text": "[calm] [MOCK]", "emotion": "calm"}'


@asynccontextmanager
async def lifespan(app: FastAPI):
    """所有初始化在同一個 event loop 裡，避免跨 loop 問題。"""
    from configs.loader import load_config, create_llm_proxy, create_heartbeat, create_agents
    from src.eventbus import SoulEventBus
    from src.eventbus.token_manager import SpeakerTokenManager
    from src.agent.speaker_token import SpeakerTokenBus
    from src.eventbus.schema import EventType, SoulEvent
    from src.memory.middleware import MemoryMiddleware
    from src.io.gateway import IOGateway

    cfg = load_config()

    # Live2D 已移除（Bry 拍板 2026-07-14）— 不再載入 live2d config

    bus = SoulEventBus()
    await bus.start()

    mw = MemoryMiddleware(bus=bus, data_dir="data/memory")
    mw.register()

    token_mgr = SpeakerTokenManager(bus, token_timeout_secs=120.0)
    token_mgr.register()

    # ── SpeakerTokenBus：USER_MESSAGE 仲裁 ─────────────────────
    speaker_token_bus = SpeakerTokenBus(cooldown_secs=4.0)
    # submit_bid 採用 lazy open，不需要單獨的 listener

    provider = cfg.get("llm", {}).get("provider", "mock")
    key = os.getenv(f"{provider.upper()}_API_KEY", "")
    if not key:
        logger.warning(f"[Server] LLM_PROVIDER={provider} 但找不到 API key，使用 MockLLMBackend")
        from src.llm.proxy import LLMProxy
        llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock", max_tokens=200)
    else:
        logger.info(f"[Server] LLM backend: real {provider}")
        llm = create_llm_proxy(cfg, bus)
    llm.register()

    # Phase 12 LLM-as-judge: 設定 process-global LLMProxy reference,
    # 讓 MemoryWriter._get_llm_judge() 跨模組邊界可以拿到
    from src.memory.sage.writer import set_llm_proxy
    set_llm_proxy(llm)
    logger.info("[Server] LLMProxy wired into MemoryWriter (LLM judge ready)")

    # Bry §11 shadow mode (2026-07-02): 對每一筆真實訊息 v6 並行 observation
    # 7 天自動到期, 不影響 prod 路徑結果
    from src.memory.shadow import init_shadow_observer
    shadow_dir = _root / "data" / "shadow"
    shadow_obs = init_shadow_observer(shadow_dir, enabled=True, llm_proxy=llm)
    logger.info(f"[Server] Shadow mode 啟動 (7天): {shadow_dir}/shadow_log.jsonl")

    # 動態載入所有 enabled Agent（帶 SpeakerTokenBus）
    agents = create_agents(cfg, bus, speaker_token_bus=speaker_token_bus)
    agent_ids = [a.agent_id for a in agents]
    # Stage 4.3.1 (Mavis 拍板 2026-07-21 17:20+): expose agents 給 /api/test/spawn_cold_intents
    app.state._agents = agents

    gateway = IOGateway(bus=bus, app=app)
    gateway.register()

    # ── Phase 4 FishTTSHandler：訂閱 AGENT_SPEAK 自動觸發 Fish TTS ──
    # 不影響主對話流程（fire-and-forget，失敗不 raise）
    # 環境變數 FISH_TTS_ENABLED=0 可關閉（debug 用）
    if os.environ.get("FISH_TTS_ENABLED", "1") == "1":
        from src.llm.fish_tts_handler import FishTTSHandler
        tts_handler = FishTTSHandler(
            bus=bus,
            output_dir=_root / "data" / "tts",
        )
        tts_handler.register()
    else:
        logger.info("[Server] FISH_TTS_ENABLED=0, skip FishTTSHandler")

    # M1.2 (2026-07-31 23:30 Perplexity 派工): Heartbeat Engine 停用
    # 跟 scheduler heartbeat (Lesson 39, 30-60 min 隨機) 兩套並存, 停止 src/heartbeat/
    # 60s tick 改由 scheduler 觸發 + 角色自主行為負責。
    # 保留 create_heartbeat import 待 M1.3 回歸測試確認後決定刪除。
    # app.state._heartbeat = None 讓 /_admin/fast_forward 知道沒有了
    # heartbeat = create_heartbeat(cfg, bus, agent_ids=agent_ids)
    # heartbeat._manager = gateway.manager
    # await heartbeat.start()
    app.state._heartbeat = None  # Heartbeat Engine 停用 (M1.2)

    # ── Stage 4.2 (Bry 拍板 2026-07-18 18:24+): 排程器 + diary ───────
    # morning 08:00 / night 22:00 自動觸發, 1 天驗殘留感
    # 第一刀用 placeholder, Bry 看過決定要不要升級到 LLM 真生成
    # Bry 2026-07-20 18:58 升級 LLM, 2026-07-20 19:03 加 4.2+缺口 1 (夢境/事件)
    try:
        from src.soul import get_scheduler, diary_callback_factory
        from src.soul.dream_event import get_dream_event_writer
        # Lesson 39 (正式值): heartbeat 30-60 分鐘隨機,proactive DM 2-4 小時隨機
        # 測試時可暫時改成 1-2 分鐘驗證路徑
        # M1.1 (2026-07-31 23:30 Perplexity 派工): 傳 bus 給 scheduler,
        # 讓 5 個 _fire_* 觸發點 callback 跑之前發布 AGENT_INTENT 到 bus
        scheduler = get_scheduler(bus=bus)
        for aid in agent_ids:
            cb = await diary_callback_factory(aid)
            scheduler.register(aid, cb)

        # 4.2+缺口 1: 註冊 dream + event callback (Bry 2026-07-20 19:03 拍板)
        # 夢境 22:05, 事件隨機 4-8 小時, 100% 觸發, 不做觀察期
        async def _dream_callback(agent_id: str, target_agent_id: str):
            writer = get_dream_event_writer()
            await writer.write_dream(agent_id, target_agent_id, scheduler._all_agents)

        async def _event_callback(agent_id: str, slot: str):
            writer = get_dream_event_writer()
            await writer.write_event(agent_id)

        scheduler.register_dream_event(_dream_callback, _event_callback)

        # ── Lesson 39 (2026-07-30 Bry 拍板): heartbeat + proactive DM ─
        # 兩條背景任務給角色自主活動:
        #   A. heartbeat: 30-60 分鐘隨機 1-2 隻角色輕量 check-in (走 UI broadcast)
        #   B. proactive DM: 2-4 小時隨機 1 隻角色主動 TG DM 找 Bry
        # 兩者共用 LLM_CONCURRENCY_LIMIT 限流, 跟 diary/dream 不會疊加撞 provider
        # proactive DM 還有冷卻窗 (2h) + 靜音時段 (23:00-08:00) 防護, 在 scheduler 內做
        from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT
        import random as _r39

        async def _heartbeat_callback(agent_id: str) -> None:
            """Lesson 39-A: 輕量 check-in 訊息, 走 agent._fire_intent 走 UI.

            Lesson 41 修: 必須把 _build_intent_payload 算出來的 draft 傳進 chrono_payload,
            不然 LLMProxy 收到 empty user_message → API 回 400 "chat content is empty"。
            """
            _agent = next((a for a in agents if a.agent_id == agent_id), None)
            if _agent is None:
                return
            _elapsed = _r39.uniform(60, 180)  # 1-3h 感覺
            _draft = _agent._build_intent_payload("heartbeat", _elapsed).get("draft", "")
            async with LLM_CONCURRENCY_LIMIT:
                try:
                    await _agent._fire_intent(
                        reason="heartbeat",
                        elapsed_mins=_elapsed,
                        chrono_payload={"draft": _draft},  # 用 _build_intent_payload 的 draft
                        mode="private",
                    )
                except Exception as e:
                    logger.warning(f"[Heartbeat] {agent_id} 失敗: {e}")

        async def _proactive_dm_callback(agent_id: str) -> None:
            """Lesson 39-B: 角色主動透過 TG DM 找 Bryan.

            Lesson 41 修: 同上, draft 必須來自 _build_intent_payload, 不能空字串。
            """
            _agent = next((a for a in agents if a.agent_id == agent_id), None)
            if _agent is None:
                return
            _elapsed = _r39.uniform(120, 240)  # 2-4h
            _draft = _agent._build_intent_payload("proactive_dm", _elapsed).get("draft", "")
            async with LLM_CONCURRENCY_LIMIT:
                try:
                    await _agent._fire_intent(
                        reason="proactive_dm",
                        elapsed_mins=_elapsed,
                        chrono_payload={
                            "draft": _draft,            # Lesson 41: 非空 draft
                            "target_channel": "telegram",
                            "target_user_id": "1696287850",  # Bry 的 TG chat_id
                        },
                        mode="private",
                    )
                except Exception as e:
                    logger.warning(f"[ProactiveDM] {agent_id} 失敗: {e}")

        scheduler.register_heartbeat(_heartbeat_callback)
        scheduler.register_proactive_dm(_proactive_dm_callback)

        await scheduler.start()
        app.state._scheduler = scheduler
        logger.info(
            f"[Server] Stage 4.2 + 缺口 1 啟動 ✓ "
            f"agents={len(agent_ids)} diary(LLM)+dream(22:05)+event(4-8h)"
        )
    except ImportError as e:
        logger.warning(f"[Server] Stage 4.2 模組 import 失敗, scheduler 不啟動: {e}")

    # ── ChannelRouter：聚合所有 channel（Telegram / Live2D / 之後）──
    # Phase 5b/5c：Telegram 走 TELEGRAM_BOT_YUA env flag 啟動
    # Phase 6.1：Live2D 永遠啟動（純前端，無外部依賴）
    tg_adapter = None
    live2d_adapter = None
    channel_router = None
    from src.io.channels.router import ChannelRouter

    # Live2D 已移除（Bry 拍板 2026-07-14）— ChannelRouter 只給 Telegram 用
    # 沒 Telegram 也要建（給未來 channel 預留接口）

    if os.environ.get("TELEGRAM_BOT_YUA"):
        from src.io.channels.telegram import TelegramAdapter

        tg_adapter = TelegramAdapter()
        # Phase 5c：傳 gateway_manager 給 ChannelRouter，heartbeat 觸發時
        # 動態查 WebSocket 連線數，0 連線就 fallback 走 Telegram
        channel_router = ChannelRouter(bus, gateway_manager=gateway.manager)
        channel_router.register(tg_adapter)
        await channel_router.start()

        async def _on_tg_message(agent_id: str,
                                  text: str,
                                  user_id: int) -> None:
            """Telegram bot 收到 user 訊息 → 送進 Event Bus"""
            await channel_router.inbound(
                agent_id, text, user_id, channel="telegram"
            )

        await tg_adapter.start(_on_tg_message)
        # 10 個 bot (Phase 5a: 3 + Phase 6.5: 1 + Phase 7-11: 5 + Phase 12: 1)
        logger.info(f"[Server] Telegram channel started (10 bots polling)")
    else:
        # 沒 Telegram 也要有 ChannelRouter（給未來 channel 預留接口）
        channel_router = ChannelRouter(bus, gateway_manager=gateway.manager)
        await channel_router.start()
        logger.info("[Server] TELEGRAM_BOT_YUA not set, skip Telegram channel")
    # Live2D channel 已移除 — 純文字 + Fish TTS 鏈路
    # ── Phase 5b end ──────────────────────────────────

    logger.info("[Server] 所有模組啟動完成")

    # === Async heartbeat dumper (Lesson 38) ===
    # 跟上面 faulthandler.dump_traceback_later 互補：asyncio-based，
    # 每次 dump 覆寫 heartbeat_trace.log（只留最新一份）,可讀性高
    # 如果 event loop 死了,這條會停;faulthandler 的 thread-based dump 還是會繼續
    async def _heartbeat_dumper():
        _dumper_path = _root / "data" / "heartbeat_trace.log"
        _first = True
        while True:
            try:
                # 第一次等 30s（讓 init 跑完）,之後每 60s
                await asyncio.sleep(30 if _first else 60)
                _first = False
                with open(_dumper_path, "w", encoding="utf-8") as f:
                    f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} (overwrite, every 60s) ===\n")
                    faulthandler.dump_traceback(file=f, all_threads=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # dump 失敗不該殺掉 dumper 自己,靜默吞
                try:
                    sys.stderr.write(f"[heartbeat_dumper] error: {e}\n")
                except Exception:
                    pass

    app.state._dumper_task = asyncio.create_task(_heartbeat_dumper())
    logger.info("[Server] heartbeat dumper 啟動 (60s/次, 寫 data/heartbeat_trace.log)")

    yield

    # ── Shutdown ────────────────────────────────────────────
    # Lesson 38: 停掉 heartbeat dumper
    dumper = getattr(app.state, "_dumper_task", None)
    if dumper is not None:
        dumper.cancel()
        try:
            await dumper
        except asyncio.CancelledError:
            pass
        logger.info("[Server] heartbeat dumper 停止 ✓")

    if channel_router is not None:
        await channel_router.stop()
    if tg_adapter is not None:
        await tg_adapter.stop()
        logger.info("[Server] Telegram channel stopped")
    # M1.2: Heartbeat Engine 停用, stop 也跳過
    # await heartbeat.stop()
    # Stage 4.2 (Bry 拍板 2026-07-18 18:24+): 排程器 shutdown
    if getattr(app.state, "_scheduler", None) is not None:
        try:
            await app.state._scheduler.stop()
            logger.info("[Server] Stage 4.2 scheduler 停止 ✓")
        except Exception as e:
            logger.warning(f"[Server] scheduler stop 失敗: {e}")
    await bus.stop()
    logger.info("[Server] 關閉完成")


app = FastAPI(lifespan=lifespan)


# ── Stage 4.3.1 cold 33% 推驗證 admin endpoint ─────────────────
# Mavis 拍板 2026-07-21 17:20+:
# Bry 要驗 cold start 33% 推過濾,但 USER_MESSAGE 觸發會在 agent_speak 之前
# 把 count++ (memory.middleware._on_user_message L217),所以 USER_MESSAGE
# 觸發 = 100% 推,驗不到 cold 33%。
# 這個 endpoint 直接呼叫 consciousness._fire_intent 灌 AGENT_INTENT,
# target_channel=telegram 讓 ChannelRouter 走到 _should_push_to_bry,
# 觸發 cold 33% 推邏輯 (5 隻 cold 角色 → 預期 1-2 隻會從 TG 收到)
@app.post("/api/test/spawn_cold_intents")
async def test_spawn_cold_intents():
    """
    灌 5 隻 cold 角色 (akane/anna/rem/ruka/miku) 的 AGENT_INTENT
    模擬「角色主動想說話」,直接走 cold 33% 推路徑
    """
    agents = getattr(app.state, "_agents", None)
    if not agents:
        return {"ok": False, "error": "agents not initialized"}

    COLD_AGENTS = ["agent_akane", "agent_anna", "agent_rem", "agent_ruka", "agent_miku"]
    BRYAN_TG_ID = "1696287850"

    results = []
    for agent in agents:
        if agent.agent_id not in COLD_AGENTS:
            continue
        try:
            chrono = {
                "target_channel": "telegram",
                "target_user_id": BRYAN_TG_ID,
            }
            await agent._fire_intent(
                reason="manual_cold_test",
                elapsed_mins=10.0,
                chrono_payload=chrono,
            )
            results.append({
                "agent_id": agent.agent_id,
                "status": "intent_fired",
                "target_channel": "telegram",
            })
        except Exception as e:
            results.append({
                "agent_id": agent.agent_id,
                "status": "error",
                "error": str(e),
            })
    return {
        "ok": True,
        "intent_count": len(results),
        "results": results,
        "note": "5 隻 cold 角色 AGENT_INTENT 已灌, target_channel=telegram, 走 cold 33% 推路徑。預期 1-2 隻會從 TG 收到 (33% × 5 ≈ 1.65)",
    }


@app.post("/api/test/spawn_intent")
async def test_spawn_intent(agent_id: str):
    """灌指定 1 隻 agent 的 AGENT_INTENT (單隻驗證用)"""
    agents = getattr(app.state, "_agents", None)
    if not agents:
        return {"ok": False, "error": "agents not initialized"}

    BRYAN_TG_ID = "1696287850"
    target = None
    for a in agents:
        if a.agent_id == agent_id:
            target = a
            break
    if not target:
        return {"ok": False, "error": f"agent {agent_id} not found"}

    try:
        chrono = {
            "target_channel": "telegram",
            "target_user_id": BRYAN_TG_ID,
        }
        await target._fire_intent(
            reason="manual_cold_test_single",
            elapsed_mins=10.0,
            chrono_payload=chrono,
        )
        return {
            "ok": True,
            "agent_id": agent_id,
            "status": "intent_fired",
            "target_channel": "telegram",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
