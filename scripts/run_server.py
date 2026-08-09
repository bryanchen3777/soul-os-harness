#!/usr/bin/env python3
"""
Soul OS — 主啟動入口
啟動 Event Bus + 所有模組 + FastAPI WebSocket Gateway
"""
import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

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


# === Event loop self-check (Bry 拍板 2026-08-03 13:40, module level) ===
# 跟 Lesson 38 dumper 互補: dumper 寫 thread dump 給 developer 除錯,
# self-check 寫簡單 timestamp 給 Bry / watchdog 看 event loop 存活.
# 範圍: 整個 server process, 不限特定 reason/角色.
# 失敗: 寫檔失敗 log warning, 不影響主路徑.
# 暴露為 module-level function 方便 mock test 直接 import 測.
async def event_loop_self_check(
    state_dir: Path,
    interval_seconds: int,
    first_delay_seconds: int = 60,
) -> None:
    """
    Args:
        state_dir: data/state/ 路徑, 寫 event_loop_alive.json 在這
        interval_seconds: 寫檔間隔 (預設 600s = 10 min,
            環境變數 SOULOS_SELF_CHECK_INTERVAL_SECS 可覆寫)
        first_delay_seconds: 第一次寫檔前的延遲 (預設 60s, 讓 init 跑完)
    """
    _path = state_dir / "event_loop_alive.json"
    _path.parent.mkdir(parents=True, exist_ok=True)
    _first = True
    while True:
        try:
            await asyncio.sleep(first_delay_seconds if _first else interval_seconds)
            _first = False
            _payload = {
                "last_alive_at": datetime.now(timezone.utc).isoformat(),
                "interval_seconds": interval_seconds,
                "source": "run_server_event_loop_self_check",
            }
            # atomic write: 寫到 .tmp 再 rename (跟 _last_observed_hash.txt 風格一致)
            _tmp = _path.with_suffix(".json.tmp")
            _tmp.write_text(
                json.dumps(_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _tmp.replace(_path)
            logger.debug(
                f"[self_check] event loop alive, wrote {_path.name}"
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            # 寫檔失敗不該殺掉 self-check 自己, 靜默 log
            try:
                logger.warning(f"[self_check] write failed: {e}")
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """所有初始化在同一個 event loop 裡，避免跨 loop 問題。"""
    from configs.loader import load_config, create_llm_proxy, create_agents
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

    # Bry 拍板 2026-08-07 20:02 P7 hardening: 註冊順序明確為
    #   MemoryMiddleware → WorldPerceptionMiddleware → SpeakerTokenManager
    # 跟生產 path 對應: AGENT_INTENT → ENRICHED → PERCEIVED → TOKEN_GRANTED
    # 範圍: 跟 P5 一致只動這三個的順序, 不動其他模組

    # β2.1 (Bry 拍板 2026-08-02 21:48): MemoryMiddleware 接受 llm_proxy 參考
    # 讓 middleware 在 _on_agent_intent 對 agent_akane + heartbeat 觸發事件生成
    # 範圍限定 pilot, 不影響其他 reason/角色
    mw = MemoryMiddleware(bus=bus, data_dir="data/memory", llm_proxy=llm)
    mw.register()

    # M3 Phase 1 (Bry 拍板 2026-08-07 19:40 + 2026-08-07 20:02 hardening):
    # WorldPerceptionMiddleware 在 MemoryMiddleware 之後接管
    # AGENT_INTENT_ENRICHED → AGENT_INTENT_PERCEIVED
    # 預設啟用 (production path), 緊急時可 SOULOS_WORLD_PERCEPTION_ENABLED=0 關閉
    # 對齊 2026-08-05 21:08 DISABLE_PROACTIVE 派工精神
    world_perception_enabled = os.getenv("SOULOS_WORLD_PERCEPTION_ENABLED", "1") == "1"
    if world_perception_enabled:
        from src.world import WorldPerceptionMiddleware
        world_perception = WorldPerceptionMiddleware(bus=bus)
        world_perception.register()
        # Production SpeakerTokenManager 只訂閱 AGENT_INTENT_PERCEIVED (避免 double-process)
        token_mgr = SpeakerTokenManager(
            bus, token_timeout_secs=120.0,
            intake_event_types={EventType.AGENT_INTENT_PERCEIVED},
        )
        logger.info("[Server] M3 WorldPerception 啟用 ✓ (production mode)")

        # P8 hardening: Synthetic World Source Seam (validation only)
        # SOULOS_WORLD_PERCEPTION_TEST_SOURCE=1 → 注入 1 個 deterministic rain event
        # 給 production smoke test 用, 確認 LLM 收到 world_context
        # Production default 永遠 OFF (環境變數預設 "0")
        if os.getenv("SOULOS_WORLD_PERCEPTION_TEST_SOURCE", "0") == "1":
            from src.world import SyntheticWorldEventSource
            async def _smoke_inject():
                await asyncio.sleep(2)  # 等 server 跟 bus 完全 ready
                await world_perception.inject_synthetic_events_for_smoke_test([
                    SyntheticWorldEventSource.build_rain_started(),
                ])
                logger.info("[Server] M3 synthetic smoke test event injected ✓")
            asyncio.create_task(_smoke_inject())
    else:
        # Fallback: 無 M3 時, SpeakerTokenManager 走 LEGACY (訂 ENRICHED + PERCEIVED)
        world_perception = None
        token_mgr = SpeakerTokenManager(bus, token_timeout_secs=120.0)
        logger.warning("[Server] M3 WorldPerception 關閉 (legacy mode, SOULOS_WORLD_PERCEPTION_ENABLED=0)")
    token_mgr.register()

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
        # 修法 11 (Bry 拍板 2026-08-06 16:xx): 加 proactive_agents 白名單
        # 只留 Ruka (瑠夏) 有主動生活/主動傳訊功能, 其他 9 個角色改回純被動
        # 動機: 8/5 21:08 Bry 被連環訊息轟炸, 從源頭減少觸發面
        # 範圍: 只影響 _fire_heartbeat / _fire_proactive_dm 的隨機抽樣池
        #       diary (morning/night) / dream / event 仍對全部 10 隻角色觸發
        # 驗證效果: 修法 7/8/9 (stale 過濾 + 時間上下文 + 跨 session 在線判斷)
        #           已經修好單次觸發的內容組裝邏輯, 這層是更根本的觸發面控制
        # 穩了之後可逐步加碼 (例: ["agent_ruka", "agent_yua"]), 一次加一隻
        scheduler = get_scheduler(
            bus=bus,
            proactive_agents=["agent_ruka"],
        )
        # M5.2-I Phase 7 (Bry 拍板 2026-08-08): 移除 _diary_noop_cb noop wrapper.
        #
        # I-6 Scheduler 已 AGENCY_TRIGGER-only (4 條 fire_* path 不再 invoke callback).
        # I-7 進一步: Production 不再需要用 noop callback 假裝 callback execution 是必要的.
        #
        # Architectural: morning/night diary path 仍可走 scheduler → fire_all → AGENCY_TRIGGER → DiaryHandler
        # (但 production 中 _callbacks 現在是 empty, _fire_all 不觸發 — 等 I-8+ iteration source 重構)
        #
        # DiaryHandler 仍 wired 到 bus (handler.executor 仍 lookup diary_callbacks_real)
        diary_callbacks_real: Dict[str, Any] = {}  # agent_id -> real callback (供 DiaryHandler.executor 用)
        for aid in agent_ids:
            cb_real = await diary_callback_factory(aid)
            diary_callbacks_real[aid] = cb_real

        # 4.2+缺口 1: dream + event 觸發時機 (Bry 2026-07-20 19:03 拍板)
        # 夢境 22:05, 事件隨機 4-8 小時, 100% 觸發, 不做觀察期
        # M5.2-H Phase 1/2 (Bry 拍板 2026-08-08): dream + event 真實執行路徑
        # 走 AGENCY_TRIGGER → DreamHandler / EventHandler (writer.write_dream / writer.write_event)
        # M5.2-J Phase J-2 (Bry 拍板 2026-08-08): production noop callback
        # (_dream_callback / _event_callback) 已無 runtime dependency, 完全移除。
        # scheduler.register_dream_event() API 仍保留為 compat surface (見 scheduler.py)。

        # ── Lesson 39 (2026-07-30 Bry 拍板): heartbeat + proactive DM ─
        # 修法 12 (Bry 拍板 2026-08-06 17:12): heartbeat 整條拿掉
        # Bry 派工: 「對話負擔不按訊息類型分」, heartbeat + proactive_dm 疊加對 Bry 同樣是對話量
        # Bry 派工 5-8 條/天期望, heartbeat 32 條/天遠超 Bry 上限
        # 只留 proactive DM 一條觸發鏈, 間隔 3-5 小時 (一天約 5-8 條)
        # heartbeat 機制保留在 scheduler.py 內部, 給未來 Bry 想恢復時不用重寫
        # 恢復方式: Bry 拍板後, 拿掉下方註解 + 加回 _heartbeat_callback + register_heartbeat
        from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT
        import random as _r39

        # 修法 12: heartbeat 暫停, Bry 8/6 17:12 拍板
        # async def _heartbeat_callback(agent_id: str) -> None:
        #     """Lesson 39-A: 輕量 check-in 訊息, 走 agent._fire_intent 走 UI.
        #
        #     Lesson 41 修: 必須把 _build_intent_payload 算出來的 draft 傳進 chrono_payload,
        #     不然 LLMProxy 收到 empty user_message → API 回 400 "chat content is empty"。
        #     """
        #     _agent = next((a for a in agents if a.agent_id == agent_id), None)
        #     if _agent is None:
        #         return
        #     _elapsed = _r39.uniform(60, 180)  # 1-3h 感覺
        #     _draft = _agent._build_intent_payload("heartbeat", _elapsed).get("draft", "")
        #     async with LLM_CONCURRENCY_LIMIT:
        #         try:
        #             await _agent._fire_intent(
        #                 reason="heartbeat",
        #                 elapsed_mins=_elapsed,
        #                 chrono_payload={"draft": _draft},
        #                 mode="private",
        #             )
        #         except Exception as e:
        #             logger.warning(f"[Heartbeat] {agent_id} 失敗: {e}")

        # M5.2-O-3 (Bry 拍板 2026-08-08): _proactive_dm_callback legacy def 移除
        # production 真正路徑: AGENCY_TRIGGER → AgencyTriggerHandler → _proactive_dm_llm_executor
        # 舊 callback 已無 production invocation (M5.2-G/I-6 後)

        # M5.2-G: AgencyTriggerHandler LLM executor (從舊 callback LLM 路徑搬到 executor)
        async def _proactive_dm_llm_executor(agent_id: str, trigger) -> None:
            """
            M5.2-G: 真正呼叫 LLM 的 executor, 由 AgencyTriggerHandler 在 decision=YES 時觸發。
            邏輯沿用 Lesson 39-B 既有 LLM 觸發路徑 (build_intent_payload → LLM → TG DM)。
            """
            _agent = next((a for a in agents if a.agent_id == agent_id), None)
            if _agent is None:
                logger.warning(f"[AgencyTriggerHandler] agent {agent_id} 找不到, skip LLM")
                return
            _elapsed = _r39.uniform(180, 300)  # 3-5h (跟 proactive_dm 觸發間隔對齊)
            _draft = _agent._build_intent_payload("proactive_dm", _elapsed).get("draft", "")
            async with LLM_CONCURRENCY_LIMIT:
                try:
                    await _agent._fire_intent(
                        reason="proactive_dm",
                        elapsed_mins=_elapsed,
                        chrono_payload={
                            "draft": _draft,            # 非空 draft (Lesson 41)
                            "target_channel": "telegram",
                            "target_user_id": "1696287850",  # Bry 的 TG chat_id
                        },
                        mode="private",
                    )
                except Exception as e:
                    logger.warning(f"[AgencyTriggerHandler] LLM executor {agent_id} 失敗: {e}")

        # 修法 12: heartbeat 暫停, Bry 8/6 17:12 拍板
        # scheduler.register_heartbeat(_heartbeat_callback)
        # M5.2-O-3 (Bry 拍板 2026-08-08): 移除 scheduler.register_proactive_dm(_proactive_dm_callback)
        # legacy callback 已無 production invocation (M5.2-G/I-6 後),
        # production 走 AGENCY_TRIGGER event bridge (下方 AgencyTriggerHandler 訂閱)

        # M5.2-G: Wire AgencyTriggerHandler 訂閱 AGENCY_TRIGGER
        # 從 src.agency import AgencyTriggerHandler (lazy import 避免循環)
        from src.agency import AgencyTriggerHandler
        _trigger_handler = AgencyTriggerHandler(
            state=None,  # 用預設 AgencyState
            llm_executor=_proactive_dm_llm_executor,
        )
        # 訂閱 AGENCY_TRIGGER event
        bus.subscribe(
            subscriber_id="agency_trigger_handler",
            handler=_trigger_handler.handle_event,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        logger.info(
            f"[M5.2-G] AgencyTriggerHandler 訂閱 AGENCY_TRIGGER ✓ (proactive_dm bridge active)"
        )

        # M5.2-H Phase 1 (Bry 拍板 2026-08-08): Wire EventHandler 訂閱 AGENCY_TRIGGER (trigger_type="event")
        # 跟 AgencyTriggerHandler 平行, 過濾 trigger_type=="event",
        # decision=YES 時呼叫 writer.write_event (WRITER_ONLY, 不調 LLM, 不發 AGENT_SPEAK)
        from src.agency import EventHandler
        async def _event_writer_executor(agent_id: str) -> None:
            """M5.2-H: 真正寫 diary 的 executor, 由 EventHandler 在 decision=YES 時觸發.

            邏輯沿用 M5.2-H Phase 1 event 觸發的 writer 路徑 (writer.write_event)。
            搬出來獨立是因為 M5.2-J Phase J-2 後舊 _event_callback noop 已完全移除,
            真實執行路徑走 EventHandler 透過 AGENCY_TRIGGER 觸發。
            """
            _writer = get_dream_event_writer()
            await _writer.write_event(agent_id)

        _event_handler = EventHandler(
            state=None,  # 用預設 AgencyState
            writer_executor=_event_writer_executor,
        )
        bus.subscribe(
            subscriber_id="agency_event_handler",
            handler=_event_handler.handle_event,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        logger.info(
            f"[M5.2-H] EventHandler 訂閱 AGENCY_TRIGGER ✓ (event bridge active, WRITER_ONLY)"
        )

        # M5.2-H Phase 2 (Bry 拍板 2026-08-08): Wire DreamHandler 訂閱 AGENCY_TRIGGER (trigger_type="dream")
        # 跟 AgencyTriggerHandler / EventHandler 平行, 過濾 trigger_type=="dream",
        # decision=YES 時呼叫 writer.write_dream (WRITER_ONLY, 包含 relationship side effect)
        from src.agency import DreamHandler
        async def _dream_writer_executor(
            dreamer: str,
            target_agent_id: str,
            all_agents: list,
        ) -> None:
            """M5.2-H Phase 2: 真正寫 dream 的 executor, 由 DreamHandler 在 decision=YES 時觸發.

            邏輯沿用 M5.2-H Phase 2 dream 觸發的 writer 路徑 (writer.write_dream)。
            writer.write_dream 內部會:
              1. 生成 dream 內容 (LLM 或 placeholder)
              2. 寫入 diary jsonl
              3. on_dream touch (relationships.json)
              4. _extract_impression 更新 relationships
            全部都是 1 次 writer 內部,handler 不額外做任何 relationship 操作
            M5.2-J Phase J-2 後舊 _dream_callback noop 已完全移除。
            """
            _writer = get_dream_event_writer()
            await _writer.write_dream(dreamer, target_agent_id, all_agents)

        _dream_handler = DreamHandler(
            state=None,  # 用預設 AgencyState
            dream_writer_executor=_dream_writer_executor,
        )
        bus.subscribe(
            subscriber_id="agency_dream_handler",
            handler=_dream_handler.handle_event,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        logger.info(
            f"[M5.2-H2] DreamHandler 訂閱 AGENCY_TRIGGER ✓ "
            f"(dream bridge active, WRITER_ONLY, relationship side effect via writer)"
        )

        # M5.2-H Phase 3 (Bry 拍板 2026-08-08): Wire DiaryHandler 訂閱 AGENCY_TRIGGER (morning + night)
        # 一個 Handler 同時負責 morning + night (兩者都是 diary_callback_factory pattern)
        # 過濾 trigger_type ∈ {morning, night}, decision=YES 時呼叫 diary_writer_executor(agent_id, slot)
        # diary_writer_executor 在 production 從 diary_callbacks_real lookup 既有 callback 並執行
        # (跟原 _fire_all 的 lookup 邏輯對齊, 但搬到 handler 端透過 Agency decision 控管)
        from src.agency import DiaryHandler
        async def _diary_writer_executor(agent_id: str, slot: str) -> None:
            """M5.2-H Phase 3: 真正跑 diary generation 的 executor, 由 DiaryHandler 在 decision=YES 時觸發.

            從 diary_callbacks_real lookup 既有 callback, 內部跑:
              1. 載入 persona prompt
              2. 抽最近 5 條 v1 mirror memory
              3. 呼叫 generate_diary_entry() 走 minimax M2.7
              4. 失敗 fallback placeholder
              5. DiaryWriter 寫入 jsonl
            Handler 不重新做這些, 只 delegate 回既有 callback.
            """
            cb_real = diary_callbacks_real.get(agent_id)
            if cb_real is None:
                logger.warning(
                    f"[DiaryHandler] no real callback for {agent_id} {slot}, "
                    f"skip (diary_callbacks_real lookup miss — "
                    f"production 維護 dict 漏了這個 agent_id, "
                    f"見 M5.2-I Phase 7 / M5.2-J Phase J-1 doc correction)"
                )
                return
            await cb_real(agent_id, slot)

        _diary_handler = DiaryHandler(
            state=None,  # 用預設 AgencyState (跟其他 handler 共用)
            diary_writer_executor=_diary_writer_executor,
        )
        bus.subscribe(
            subscriber_id="agency_diary_handler",
            handler=_diary_handler.handle_event,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        logger.info(
            f"[M5.2-H3] DiaryHandler 訂閱 AGENCY_TRIGGER ✓ "
            f"(morning + night bridge active, WRITER_ONLY, "
            f"delegate 到既有 diary_callbacks_real)"
        )

        # [TEMP-EMERGENCY-STOP] Bry 拍板 2026-08-05 21:08: 立刻停 proactive
        # Bry 派工原文: 暫停主動傳訊 (proactive_dm / heartbeat 觸發), Bry 被連環訊息轟炸
        # 根因懷疑: 33ab57e (spawn_intent chrono draft) + 317900b (M2 task 3 placeholder user role)
        # 修完後 proactive 觸發變迴圈, Bry 拍板環境變數 DISABLE_PROACTIVE=true 整個 skip
        # 修法: env var 開關, default False (保持原行為), 設 true 完全 skip scheduler start
        # 範圍: 只動 server startup path, 不動 scheduler 內部邏輯
        # 配套: stop() shutdown 路徑不變, DISABLE_PROACTIVE=true 啟動時 app.state._scheduler = None
        # 讓 _admin endpoint / 其他 caller 知道 scheduler 沒啟動
        # 修法 11 (Bry 拍板 2026-08-06 16:xx): 跟 proactive_agents 白名單搭配使用
        # DISABLE_PROACTIVE=true 是「全部停」(緊急開關), proactive_agents 是「只留 X」(精準控制)
        # 兩者是兩層防護: 白名單沒生效時 DISABLE 還能擋; DISABLE 解除時白名單還能控量
        if os.environ.get("DISABLE_PROACTIVE", "false").lower() == "true":
            logger.warning(
                "[Server][EMERGENCY-STOP] DISABLE_PROACTIVE=true, "
                "scheduler.start() SKIPPED — proactive_dm / heartbeat 不會觸發"
            )
            app.state._scheduler = None
        else:
            await scheduler.start()
            app.state._scheduler = scheduler
        logger.info(
            f"[Server] Stage 4.2 + 缺口 1 + 修法 11 + 修法 12 + M5.2-H1/H2/H3 啟動 ✓ "
            f"agents={len(agent_ids)} diary(LLM, Agency decision gate)+dream(22:05, Agency)+event(4-8h, Agency) "
            f"proactive_whitelist=['agent_ruka'] (Bry 拍板 8/6 收斂到單一角色) "
            f"proactive_dm 3-5h ≈ 5-8 條/天 (Bry 拍板 8/6 17:12, heartbeat 暫停) "
            f"AGENCY_BYPASS: heartbeat suspended (per 修法 12), 其他 5 條 trigger 全部 migrated"
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

    # ── Event loop self-check (Bry 拍板 2026-08-03 13:40) ───────
    # 跟 Lesson 38 dumper 互補:
    #   - dumper (60s): 寫 thread dump 給 developer 除錯用
    #   - self-check (10 min 預設): 寫簡單 timestamp 給 Bry / watchdog 看存活
    # 動機: 2026-08-03 02:15 hang (d190c96 observation window) 之前沒任何早期信號,
    # 4 小時才被 watchdog port 偵測發現; self-check 讓 hang 在 interval*2.5 內被抓到.
    # 失敗靜默 log warning, 不影響主路徑.
    _self_check_interval = int(os.getenv("SOULOS_SELF_CHECK_INTERVAL_SECS", "600"))
    app.state._self_check_task = asyncio.create_task(
        event_loop_self_check(
            _root / "data" / "state",
            _self_check_interval,
        )
    )
    logger.info(
        f"[Server] event loop self-check 啟動 "
        f"({_self_check_interval}s/次, 寫 data/state/event_loop_alive.json)"
    )

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

    # Bry 拍板 2026-08-03 13:40: 停掉 event loop self-check
    self_check = getattr(app.state, "_self_check_task", None)
    if self_check is not None:
        self_check.cancel()
        try:
            await self_check
        except asyncio.CancelledError:
            pass
        logger.info("[Server] event loop self-check 停止 ✓")

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
async def test_spawn_intent(agent_id: str, dry_run: bool = False):
    """灌指定 1 隻 agent 的 AGENT_INTENT (單隻驗證用)

    dry_run 模式 (Bry 拍板 2026-08-05 21:08): 測試觸發不走正式 TG 廣播管道,
    ChannelRouter 看到 AGENT_SPEAK event payload 帶 dry_run=True → log + skip TG 推播。
    LLM / MemoryWriter pipeline 還是會跑 (verify_stage1.py 仍能驗證記憶寫入邏輯)。
    預設 False (保持原行為, Bry 派工要求先做 dry-run 隔離再恢復 verify_stage1.py 跑)。
    """
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
            # Bry 拍板 2026-08-05 20:13: spawn_intent 是測試觸發,不是 Bry 真實對話,
            # 但 LLMProxy 構造 messages 需要 user 訊息 (M2.7 收到「user 訊息空」會回 400
            # "chat content is empty (2013)")。修法: 從 trigger context 構造一段 draft
            # 讓 chain 通 (consciousness._fire_intent 拿 draft → intent_payload["draft"]
            # → LLMProxy user_message → _build_messages_group append user role)。
            # 修法 1 範圍限定 run_server.py test endpoint, 不影響 proactive / heartbeat
            # 觸發鏈 (那邊 draft 從 _build_intent_payload 構造, 不依賴 chrono)。
            "draft": f"（verify_stage1.py 測試觸發，agent_id={agent_id}，Bry 尚未主動發言，請以角色身份自然回應。）",
            # Bry 拍板 2026-08-05 21:08: dry_run 標記從 chrono_payload 透傳到
            # intent_payload → AGENT_INTENT event → AGENT_SPEAK event,
            # ChannelRouter 看到 dry_run=True → log + skip TG 推播。
            "dry_run": dry_run,
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
            "dry_run": dry_run,
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
