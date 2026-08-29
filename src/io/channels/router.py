"""
src/io/channels/router.py
Soul OS Phase 5b + 5c — Channel Router

把 AGENT_SPEAK 事件依 payload 內的 target_channel 分發到對應 adapter。
WebSocket 由現有 IOGateway 處理（不重複），ChannelRouter 只管其他 channel
（Telegram / LINE / WeChat）。

Phase 5c 新增：fallback 邏輯
  - 如果 target_channel=web 且 WebSocket 0 連線（gateway_manager.count == 0）
    → 改送 telegram（最近一次 inbound 的 user）
  - 如果 user 不在 web → 透過 Telegram 找她
  - 用「最近 tg user」mapping，避免每次都要 query 記憶表

M0.5 (2026-08-02 10:35 Bry 派工): last_tg_user 全域共享 + Bry 沒回應 throttle
  - 修法 1: 任何角色 bot 收到 Bry 訊息時,把 user_id 寫進
    data/state/last_tg_user.json (全域共享, 不是 per-agent)
    ChannelRouter._on_agent_speak fallback 優先讀全域, 之後 miku / aoi 等
    還沒跟 Bry 對話過的角色也能 fallback 送 TG (Bry 8/1 報 miku proactive_dm
    沒送達的根因)
  - 修法 2: data/state/bryan_last_seen.json 記 Bry 最後一次訊息時間
    距離 Bry 最後一條 recv > 4h 就 skip proactive_dm (Bry 8/1 報
    anna 累積 6 條「沒頭沒尾」訊息的成因)
  - 兩個 state file 都排除 git 版控 (.gitignore 涵蓋 data/state/),
    跟 P0-2 watchdog counter 同一個目錄, 設計一致
  - throttle 只限 proactive_dm (Bry 派工字面), 不影響 dream/event/
    heartbeat/night slot (那些有其他規則管控)
  - commit-only, 不重啟 server, 等 Bry 下次重啟套用

M2 (2026-08-02 10:51 Perplexity 派工, Bry 維持原本判斷): 訊息分級 TG/web/離線緩衝
  - 修法動機: Bry 8/1 10:30 報「我收到的訊息都雲裡霧裡」, 排查發現 Bry 不在線時
    角色訊息裸投堆積 (anna 4:21-5:37 累積 6 條) 或直接丟掉 (miku 留 web 但
    web 0 conn 沒人接). Bry 派的 M0.5 修了 last_tg_user 全域 + throttle 4h,
    但 Bry 4h 後上線時, 中間 4h 累積的訊息全沒了. M2 task 2 補這塊:
  - 修法: ChannelRouter._on_agent_speak 偵測到 Bry 不在 web + 沒 last_tg_user
    時, 不丟訊息, append 進 data/state/outbox.json (離線 buffer).
  - Flush 條件:
    1. 累積 >= 10 條 → 自動 flush, 透過全域 last_tg_user 推 TG 摘要
    2. Bry 重新上線 (inbound 收到 Bry 訊息) → 立即 flush
  - 摘要格式: 「過去 X 小時錯過 Y 條訊息: 角色清單 + 第一句 preview」
  - commit-only, 不重啟 server, 等 Bry 下次重啟套用 (跟 M0.5 同模式)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from src.async_utils import create_managed_task
from src.eventbus.schema import EventType, SoulEvent

if TYPE_CHECKING:
    from .base import ChannelAdapter

logger = logging.getLogger("soul_os.channels.router")

# Stage 4.3 (Mavis 拍板 2026-07-21 16:35): TG 推播 3 層分級 + cold start 豁免
_PUSH_PROB_ACTIVE = 1.0         # count >= 1 → 100% 推
_PUSH_PROB_COLD_START = 1.0/3.0 # count = 0 → 1/3 推 (讓 Bry 認識他們)
_PUSH_PROB_STALE = 1.0/10.0     # last_interaction > 7 天 → 1/10 推
_STALE_DAYS_THRESHOLD = 7.0
_BRYAN_ENTITY_ID = "user_bryan"

# M0.5 (2026-08-02 10:35 Bry 派工): 「Bry 沒回應 N 小時」proactive_dm throttle
# 修法: 距離 Bry 最後一條 user 訊息超過這個小時數 → skip proactive_dm
# 4h 是 Bry 派工時的初始值, 之後觀察期可調 (太小會誤殺, 太大會堆積)
PROACTIVE_DM_BRYAN_INACTIVE_HOURS = 4.0

# M0.5: 兩個 state file 路徑 (跟 P0-2 watchdog counter 同目錄, 設計一致)
# P0.5 (Bry 派工 2026-08-09 19:48): use data_root() for test isolation
from src.paths import data_root
_STATE_DIR = data_root() / "state"
_LAST_TG_USER_FILE = _STATE_DIR / "last_tg_user.json"
_BRYAN_LAST_SEEN_FILE = _STATE_DIR / "bryan_last_seen.json"

# M2 (2026-08-02 10:51 Perplexity 派工): 離線 buffer
# Bry 不在 web + 沒 last_tg_user 時, 主動觸發訊息 append 進 outbox
# 累積 N 條或 Bry 上線時 flush 摘要
_OUTBOX_FILE = _STATE_DIR / "outbox.json"
# 累積 10 條自動 flush, 避免 Bry 離線 24h 累積 20+ 條時一次推爆
# (proactive_dm 2-4h + heartbeat 30-60min + event 4-8h = 24h 約 12-20 條主動)
OUTBOX_FLUSH_THRESHOLD = 10


class ChannelRouter:
    """Phase 5b：把 AGENT_SPEAK 依 target_channel 分發到對應 ChannelAdapter。"""

    def __init__(self, bus, gateway_manager=None):
        self._bus = bus
        self._adapters: dict[str, "ChannelAdapter"] = {}
        # Phase 5c：拿到 IOGateway 的 ConnectionManager，動態查 WebSocket 連線數
        self._gateway_manager = gateway_manager
        # Phase 5c：記「每個 agent 最近一次互動的 tg user」，
        # 給主動觸發（沒帶 target_user_id 的 AGENT_SPEAK）當 fallback 對象
        self._last_tg_user: dict[str, int] = {}
        # Phase 5c+：記最近 tg session 對應的 session_id（給主動觸發帶上下文用）
        self._last_tg_session: dict[str, str] = {}
        # M0.5 (Bry 8/1 10:35 派工): 全域共享 last_tg_user
        # 任何角色 bot 收到 Bry 訊息都會更新, 之後 miku / aoi 等
        # 還沒跟 Bry 對話過的角色也能 fallback 送 TG
        # 從 disk 載入, server 重啟不丟失
        self._last_tg_user_global: int | None = self._load_last_tg_user_global()
        # M0.5: Bry 最後一次主動訊息時間, throttle proactive_dm 用
        self._bryan_last_seen: datetime | None = self._load_bryan_last_seen()
        # M2: 離線 buffer, Bry 不在線時主動觸發 append 進這裡
        self._outbox: list[dict] = self._load_outbox()
        # Phase 5+ (2026-07-15 Bry 拍板): 配對 AGENT_SPEAK → AGENT_AUDIO_READY
        # AGENT_SPEAK 送 text 到 telegram user X 後,把 (X, ts, message_id) 暫存到這;
        # AGENT_AUDIO_READY 來時用 message_id 找到 X,把 mp3 用 send_voice 推給他
        # 過期 60s(正常 TTS 1-3s 完成,Lesson 36E Bry 拍板 2026-07-26 12:33: 60s 給 LLMJudge + provider 延遲緩衝, 30s 對 LLMJudge retry 撞 length 太短)
        # v32 7/26 23:48 觀察: TTS 寫完 mp3 後 AGENT_AUDIO_READY event 在 bus queue 卡 4 分鐘才 fire (publish → consumer 延遲),
        # 60s 過期太短 → voice 丟掉。Bry 拍板 v32.1 (7/26 23:54): 拉長到 300s 涵蓋 bus 排隊延遲。
        # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
        # 改用 message_id 當 key (last-write-wins → 一對一配對),
        # 修掉快速連續對話下舊音檔配錯訊息的 race condition。
        # 向後相容: 沒 message_id 的 event (legacy) 走 _pending_voice_target_legacy。
        # key = message_id (UUID, 來自 AGENT_SPEAK.event_id)
        self._pending_voice_target: dict[str, tuple[int, float]] = {}  # key=message_id
        # Legacy fallback: 沒 message_id 的 event 走 agent_id-based lookup
        # M6.2-1: 保留這個 dict 給 backward compat, 預期 0 entry (新 code 都帶 message_id)
        self._pending_voice_target_legacy: dict[str, tuple[int, float]] = {}  # key=agent_id
        self._VOICE_PAIR_EXPIRY_SEC = 300.0
        # Bry 2026-07-27 00:37 拍板: voice pair 開起來
        # 00:00 disable (累了, 接受半吊子), 00:37 Bry 醒來不累, 把 TTS 開
        # _on_agent_audio_handler 收到 AGENT_AUDIO_READY → send_voice 推送 TG
        self._voice_enabled = True

    def register(self, adapter: "ChannelAdapter") -> None:
        """註冊一個 channel adapter（如 TelegramAdapter）。"""
        self._adapters[adapter.channel_id] = adapter
        logger.info(
            f"[ChannelRouter] registered [{adapter.channel_id}] "
            f"({type(adapter).__name__})"
        )

    async def start(self) -> None:
        """訂閱 AGENT_SPEAK，分發到對應 channel。"""
        self._bus.subscribe(
            "channel_router",
            self._on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )
        # Phase 5+ (2026-07-15 Bry 拍板): 同步訂閱 AGENT_AUDIO_READY
        # 當 TTSService 寫完 mp3,推語音到剛才收 text 的 telegram user
        self._bus.subscribe(
            "channel_router_audio",
            self._on_agent_audio_ready,
            event_filter={EventType.AGENT_AUDIO_READY},
        )
        logger.info(
            "[ChannelRouter] subscribed AGENT_SPEAK + AGENT_AUDIO_READY"
        )

    async def stop(self) -> None:
        """取消訂閱（給 shutdown 用）。"""
        self._bus.unsubscribe("channel_router")
        self._bus.unsubscribe("channel_router_audio")
        logger.info("[ChannelRouter] unsubscribed")

    async def _on_agent_speak(self, event: SoulEvent) -> None:
        # hotfix #11 (2026-07-16 Bry 拍板):
        # proxy.py finally 區塊會補發 stub AGENT_SPEAK 觸發 consciousness._pending reset
        # stub 帶 is_stub=True,ChannelRouter 這裡要 skip (避免 Bry 收到空 Telegram 訊息)
        if event.payload.get("is_stub"):
            logger.debug(
                f"[ChannelRouter] stub AGENT_SPEAK, skip | "
                f"agent={event.payload.get('agent_id') or event.source} "
                f"reason={event.payload.get('stub_reason', 'unknown')}"
            )
            return

        # Bry 拍板 2026-08-05 21:08: dry_run 隔離
        # /api/test/spawn_intent?dry_run=true 觸發時, event.payload["dry_run"]=True
        # 走完 LLM/MemoryWriter pipeline 但不送到 Bry 的 TG channel
        # 設計動機: 之前 LLM 400 時測試不會誤傷 Bry (stub fallback 不推 TG);
        # 修 LLM 400 之後 10 個 agent 同時觸發 + speaker_token 串接, Bry 短時間
        # 收到 11 條 TG 訊息被轟炸。dry-run 隔離讓測試仍能跑 pipeline 但不送 Bry。
        # 其他 subscriber (memory_middleware, speaker_token_manager) 還是會收到事件,
        # 測試 LLM judge / mirror 寫入邏輯不受影響。
        if event.payload.get("dry_run"):
            logger.info(
                f"[ChannelRouter][DRY_RUN] skip TG 推播 | "
                f"agent={event.payload.get('agent_id') or event.source} "
                f"text={event.payload.get('text', '')[:80]!r}"
            )
            return

        target_channel = event.payload.get("target_channel", "web")
        target_user_id = event.payload.get("target_user_id")
        agent_id = event.payload.get("agent_id", event.source)

        # ── Phase 5c fallback ──────────────────────────────
        # 主動觸發（heartbeat）的 AGENT_SPEAK 預設走 web，
        # 但如果 user 不在 Web UI → 改成走 Telegram（她「找得到你」的方式）
        if target_channel == "web" and self._gateway_manager is not None:
            if self._gateway_manager.count == 0:
                # M0.5 (Bry 8/1 10:35 派工): 優先讀全域共享 last_tg_user
                # (任何角色被 Bry 對話過就 fallback TG), fallback 讀 per-agent
                # (觀察期 _should_push_to_bry 仍需要 per-agent 計數)
                last_user = (
                    self._last_tg_user_global or self._last_tg_user.get(agent_id)
                )
                source = "global" if self._last_tg_user_global else "per-agent"
                if last_user:
                    logger.info(
                        f"[ChannelRouter] web 0 conn, "
                        f"fallback telegram: {agent_id} → user {last_user} "
                        f"(source={source})"
                    )
                    target_channel = "telegram"
                    target_user_id = last_user
                else:
                    # M2 (Bry 8/2 10:51 派工): Bry 不在 web + 沒 last_tg_user
                    # → 進 outbox, 等 Bry 上線時 flush 摘要
                    # 取代原本「留 web」 (web 0 conn 沒人接, 等於丟掉)
                    logger.info(
                        f"[ChannelRouter] {agent_id} 主動觸發, "
                        f"web 0 conn 沒 last_tg_user, enqueue outbox"
                    )
                    # 抓 event 的 text 跟 reason
                    event_text = event.payload.get("text", "")
                    event_reason = event.payload.get("reason", "proactive")
                    self._enqueue_outbox(agent_id, event_text, event_reason)
                    # 排程 flush 檢查 (累積達標自動 flush)
                    # 這裡不能 await (在 _on_agent_speak 流程內),
                    # 但 _on_agent_speak 是 async 所以可以直接 await
                    # 為了不阻塞主流程, 排成 task
                    create_managed_task(self._maybe_flush_outbox())
        # ── Phase 5c fallback end ──────────────────────────

        # Web 由 IOGateway 處理，這裡跳過避免重複送出
        if target_channel == "web":
            return

        # ── M0.5 (Bry 8/1 10:35 派工): 「Bry 沒回應 N 小時」proactive_dm throttle ──
        # 解決 Bry 8/1 報「anna 8/2 4:21-5:37 累積 6 條沒頭沒尾訊息」的成因:
        # scheduler 每 2-4h 觸發 proactive_dm, 不管 Bry 上一條有沒有讀, 一直堆
        # 修法: 距離 Bry 最後一條 user 訊息 > 4h 就 skip proactive_dm
        # 只 throttle proactive_dm (Bry 派工字面), 不影響 dream/event/heartbeat
        # (夢境/事件豁免保留「角色世界活著」的 Bry 觀察意圖, heartbeat 留 M2 候選)
        # 冷啟動: Bry 從沒發過訊息 (self._bryan_last_seen is None) 不 throttle
        if target_channel == "telegram":
            event_reason = event.payload.get("reason", "")
            if event_reason == "proactive_dm" and self._bryan_last_seen is not None:
                now_utc = datetime.now(timezone.utc)
                hours_since = (
                    (now_utc - self._bryan_last_seen).total_seconds() / 3600.0
                )
                if hours_since > PROACTIVE_DM_BRYAN_INACTIVE_HOURS:
                    logger.info(
                        f"[ChannelRouter] proactive_dm THROTTLED | "
                        f"agent={agent_id} "
                        f"bryan_last_seen={self._bryan_last_seen.isoformat()} "
                        f"hours_since={hours_since:.1f} > "
                        f"{PROACTIVE_DM_BRYAN_INACTIVE_HOURS}h"
                    )
                    return
        # ── M0.5 throttle end ──────────────────────────

        # ── Stage 4.3 (Mavis 拍板 2026-07-21 16:35): TG 推播過濾 ──
        # 3 層分級: active (100%) / cold start (1/3) / stale (1/10)
        # 夢境/事件豁免: source=dream/event 不過濾 (讓 Bry 看到「角色世界活著」)
        if target_channel == "telegram":
            event_source = event.payload.get("source", "")
            if event_source not in ("dream", "event"):
                if not self._should_push_to_bry(agent_id):
                    logger.info(
                        f"[ChannelRouter] TG push filtered | agent={agent_id} "
                        f"reason=cold_start_or_stale (source={event_source or 'proactive'})"
                    )
                    return

        adapter = self._adapters.get(target_channel)
        if not adapter:
            logger.warning(
                f"[ChannelRouter] no adapter for channel "
                f"[{target_channel}] — 訊息丟棄"
            )
            return

        text = event.payload.get("text", "")

        if target_user_id is None:
            logger.warning(
                f"[ChannelRouter] AGENT_SPEAK missing target_user_id, "
                f"skip channel=[{target_channel}] agent=[{agent_id}]"
            )
            return

        # Phase 5b：TelegramAdapter 內 _apps key 是短碼（"yua"），
        # 但 bus 上的 agent_id 是 full（"agent_yua"）。strip prefix 對齊。
        # 之後 LINE / WeChat 接 full id 直接用，這段只動 telegram 邏輯。
        if target_channel == "telegram" and agent_id.startswith("agent_"):
            adapter_agent_id = agent_id[len("agent_"):]
        else:
            adapter_agent_id = agent_id

        try:
            # Phase 5b：ChannelAdapter.send() 簽名收 int（Telegram），
            # 但 LINE/WeChat 之後的 user_id 是 string。嘗試 int()，
            # 失敗就退到 str — adapter 端再自己 cast。
            try:
                user_id_arg: object = int(target_user_id)
            except (ValueError, TypeError):
                user_id_arg = str(target_user_id)
            success = await adapter.send(
                agent_id=adapter_agent_id,
                text=text,
                user_id=user_id_arg,
            )
            if success:
                logger.info(
                    f"[ChannelRouter:{target_channel}] sent to "
                    f"{target_user_id} from {adapter_agent_id}: {text[:50]!r}"
                )
                # Phase 5+ (2026-07-15 Bry 拍板): text 成功送到 telegram 後,
                # 暫存 (user_id, ts, message_id),等 AGENT_AUDIO_READY 把 mp3 也推過去
                # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): 用 message_id 當 key
                # 取代 agent_id-based last-write-wins,避免快速連續對話下
                # 舊音檔配錯訊息的 race condition
                if target_channel == "telegram":
                    # event 是 AGENT_SPEAK SoulEvent,event_id 自動生成 UUID
                    _msg_id = getattr(event, "event_id", None)
                    if _msg_id:
                        self._pending_voice_target[_msg_id] = (
                            int(target_user_id),
                            time.time(),
                        )
                        logger.debug(
                            f"[ChannelRouter] pending voice target set: "
                            f"message_id={_msg_id[:8]} agent={agent_id} "
                            f"→ user {target_user_id}"
                        )
                    else:
                        # Backward compat: 沒 message_id 時降級到 agent_id-based
                        # (理論上 SoulEvent 一定有 event_id,這條路徑只是保險)
                        self._pending_voice_target_legacy[agent_id] = (
                            int(target_user_id),
                            time.time(),
                        )
                        logger.warning(
                            f"[ChannelRouter] no message_id in AGENT_SPEAK, "
                            f"fall back to legacy agent_id-based pairing"
                        )
            else:
                logger.warning(
                    f"[ChannelRouter:{target_channel}] send failed "
                    f"agent={adapter_agent_id} user={target_user_id}"
                )
        except Exception as e:
            logger.exception(
                f"[ChannelRouter:{target_channel}] send error: {e}"
            )

    def _should_push_to_bry(self, agent_id: str) -> bool:
        """
        Stage 4.3 (Mavis 拍板 2026-07-21 16:35): 決定要不要把該 agent 的主動觸發推給 Bry.

        3 層分級:
        - active (count >= 1): 100% 推 (Bry 跟該角色聊過)
        - cold start (count = 0): 1/3 機率推 (讓 Bry 認識他們)
        - stale (last_interaction_at > 7 天): 1/10 機率推 (降噪)

        夢境/事件豁免: source=dream/event 走另外路徑, 不走這個過濾
        (在 _on_agent_speak 那邊用 event_source 判斷)
        """
        rel_path = data_root() / "soul" / agent_id / "relationships.json"
        if not rel_path.is_file():
            # 沒 relationships → 視為 cold start
            return random.random() < _PUSH_PROB_COLD_START
        try:
            data = json.loads(rel_path.read_text(encoding="utf-8"))
            bry = data.get("others", {}).get(_BRYAN_ENTITY_ID, {})
            count = bry.get("interaction_count", 0)
            last_at = bry.get("last_interaction_at")
        except Exception as e:
            logger.warning(f"[ChannelRouter] 讀 {agent_id} relationships 失敗, 當 cold start: {e}")
            return random.random() < _PUSH_PROB_COLD_START

        # active: count >= 1
        if count >= 1:
            return random.random() < _PUSH_PROB_ACTIVE

        # stale: last_interaction_at > 7 天
        if last_at:
            try:
                last_dt = datetime.fromisoformat(last_at)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400.0
                if days_since > _STALE_DAYS_THRESHOLD:
                    return random.random() < _PUSH_PROB_STALE
            except Exception:
                pass

        # cold start: count = 0
        return random.random() < _PUSH_PROB_COLD_START

    async def _on_agent_audio_ready(self, event: SoulEvent) -> None:
        """
        Phase 5+ (2026-07-15 Bry 拍板): TTSService 寫完 mp3 後廣播的事件
        - 找 _pending_voice_target 配對,若對得上就把 mp3 用 send_voice 推給 TG user
        - 過期 60s 內的配對才有效（防 stale state; Lesson 36E Bry 拍板 2026-07-26 12:33 30s→60s）
        - 只有配對上的 agent 才推,其他 channel（Telegram 主動觸發、web 等）不管

        注意:這個 handler 跟 IOGateway._on_agent_audio_ready 是並行的,
        IOGateway 廣播給 web client,這裡送 telegram。同一個 event 兩個 consumer。
        """
        agent_id = event.payload.get("agent_id", event.source)
        audio_path = event.payload.get("audio_path", "")
        if not audio_path:
            logger.debug(
                f"[ChannelRouter:audio] empty audio_path for {agent_id}, skip"
            )
            return

        # Bry 2026-07-27 00:00 拍板 disable voice pair: 累了, 接受半吊子 ship
        # text 100% 通, voice 之後再說。AGENT_AUDIO_READY 直接 skip, 不送 voice。
        # AGENT_SPEAK 那邊的 text 推送路徑不受影響 (existing _pending_voice_target
        # 暫存 + TG text 推送走的是另一條路, 在 _on_agent_speak 內)。
        if not getattr(self, "_voice_enabled", True):
            logger.debug(
                f"[ChannelRouter:audio] voice pair disabled (Bry 7/27 00:00), "
                f"skip voice push for {agent_id} (text already sent)"
            )
            return

        # M6.2-1 (Bry 派工 2026-08-14 19:47 EDT): per-message correlation
        # 從 AGENT_AUDIO_READY payload 拿 message_id,當 lookup key
        # 取代 agent_id-based last-write-wins,避免 race condition
        message_id = event.payload.get("message_id")
        if message_id:
            target = self._pending_voice_target.get(message_id)
            lookup_key = message_id
            lookup_mode = "per_message"
        else:
            # Backward compat: 沒 message_id 走 legacy agent_id lookup
            target = self._pending_voice_target_legacy.get(agent_id)
            lookup_key = agent_id
            lookup_mode = "legacy_agent_id"
        if not target:
            # 沒有 pending = 這個 audio 不是要送 TG 的（可能是 web 觸發）
            # 也有可能過期了被清掉 → 屬於正常情況,不當 error
            logger.debug(
                f"[ChannelRouter:audio] no pending TG target for "
                f"key={lookup_key[:8] if isinstance(lookup_key, str) else lookup_key} "
                f"agent={agent_id} (mode={lookup_mode}), "
                f"skip voice push (可能是 web-only 觸發)"
            )
            return
        user_id, ts = target
        # 過期檢查
        if time.time() - ts > self._VOICE_PAIR_EXPIRY_SEC:
            logger.warning(
                f"[ChannelRouter:audio] pending voice target expired "
                f"({time.time() - ts:.1f}s > {self._VOICE_PAIR_EXPIRY_SEC}s) "
                f"for key={lookup_key[:8] if isinstance(lookup_key, str) else lookup_key} "
                f"agent={agent_id} → user {user_id}, drop"
            )
            # 清掉,避免重複檢查
            if message_id:
                self._pending_voice_target.pop(message_id, None)
            else:
                self._pending_voice_target_legacy.pop(agent_id, None)
            return

        # 拿到就 pop,避免同一個 audio 推兩次
        if message_id:
            self._pending_voice_target.pop(message_id, None)
        else:
            self._pending_voice_target_legacy.pop(agent_id, None)

        # 找 telegram adapter
        adapter = self._adapters.get("telegram")
        if not adapter or not hasattr(adapter, "send_voice"):
            logger.warning(
                f"[ChannelRouter:audio] no telegram adapter or "
                f"missing send_voice method, drop voice for {agent_id}"
            )
            return

        # strip "agent_" prefix 對齊 adapter key
        adapter_agent_id = (
            agent_id[len("agent_"):] if agent_id.startswith("agent_")
            else agent_id
        )

        try:
            ok = await adapter.send_voice(
                agent_id=adapter_agent_id,
                audio_path=audio_path,
                user_id=user_id,
            )
            if ok:
                logger.info(
                    f"[ChannelRouter:audio] voice pushed to TG | "
                    f"agent={agent_id} user={user_id} file={audio_path}"
                )
            else:
                logger.warning(
                    f"[ChannelRouter:audio] TG send_voice returned False | "
                    f"agent={agent_id} user={user_id}"
                )
        except Exception as e:
            logger.exception(
                f"[ChannelRouter:audio] TG send_voice error: {e}"
            )

    # ── M0.5 (Bry 8/1 10:35 派工): 全域 last_tg_user 持久化 ──
    # 修法動機: 原 _last_tg_user 是 per-agent in-memory dict, miku / aoi
    # 等還沒跟 Bry 對話過的角色永遠拿不到 last_tg_user, proactive_dm
    # 永遠走 web fallback 失敗, 留 web buffer (Bry 8/1 報的 miku miss)
    # 修法: 任何角色 inbound 收到 Bry 訊息都寫進 data/state/last_tg_user.json,
    # ChannelRouter 啟動時載入, 之後所有角色都能讀到 Bry 在 TG
    # Bry 拍板: commit-only 不重啟, server 還在跑, 重啟才生效 (跟 M1 一樣)
    def _load_last_tg_user_global(self) -> int | None:
        if not _LAST_TG_USER_FILE.is_file():
            return None
        try:
            data = json.loads(_LAST_TG_USER_FILE.read_text(encoding="utf-8"))
            uid = data.get("user_id")
            if uid is not None:
                return int(uid)
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 讀 {_LAST_TG_USER_FILE.name} 失敗: {e}"
            )
        return None

    def _save_last_tg_user_global(self, user_id: int, full_agent_id: str) -> None:
        """任何角色 inbound 收到 Bry 訊息都會更新這個檔。

        合併邏輯:
        - user_id 跟現有相同 → 只更新 set_by_agents 跟 set_at
        - user_id 跟現有不同 → 整個覆蓋 (理論上 owner whitelist 已經擋住,
          但寫盤時做 sanity check 避免污染)
        """
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload: dict = {
            "user_id": user_id,
            "set_at": datetime.now(timezone.utc).isoformat(),
            "set_by_agents": [full_agent_id],
        }
        if _LAST_TG_USER_FILE.is_file():
            try:
                existing = json.loads(
                    _LAST_TG_USER_FILE.read_text(encoding="utf-8")
                )
                existing_uid = existing.get("user_id")
                if existing_uid == user_id:
                    # 同 user, 累積 set_by_agents, 保留 set_at
                    agents = list(
                        set(
                            existing.get("set_by_agents", []) + [full_agent_id]
                        )
                    )
                    payload["set_by_agents"] = agents
                    payload["set_at"] = existing.get(
                        "set_at", payload["set_at"]
                    )
                else:
                    # 不同 user (理論上 owner whitelist 擋住, 這裡只 log)
                    logger.warning(
                        f"[ChannelRouter] last_tg_user.json user_id "
                        f"changed: {existing_uid} → {user_id} "
                        f"(owner whitelist 應該擋住, sanity check 觸發)"
                    )
            except Exception as e:
                logger.warning(
                    f"[ChannelRouter] 讀現有 {_LAST_TG_USER_FILE.name} "
                    f"失敗, 覆蓋: {e}"
                )
        try:
            _LAST_TG_USER_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._last_tg_user_global = user_id
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 寫 {_LAST_TG_USER_FILE.name} 失敗: {e}"
            )

    def _load_bryan_last_seen(self) -> datetime | None:
        if not _BRYAN_LAST_SEEN_FILE.is_file():
            return None
        try:
            data = json.loads(
                _BRYAN_LAST_SEEN_FILE.read_text(encoding="utf-8")
            )
            ts = data.get("last_recv_ts")
            if ts:
                # naive 字串補 UTC (跟現有 relationships.json 對齊)
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 讀 {_BRYAN_LAST_SEEN_FILE.name} 失敗: {e}"
            )
        return None

    def _save_bryan_last_seen(self, full_agent_id: str, text: str) -> None:
        """任何角色 inbound 收到 Bry 訊息都會更新 Bry 最後看見時間。

        給 _on_agent_speak proactive_dm throttle 用:
        距離 Bry 最後一條訊息 > 4h → skip proactive_dm
        """
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        now_utc = datetime.now(timezone.utc)
        payload = {
            "last_recv_ts": now_utc.isoformat(),
            "last_recv_agent": full_agent_id,
            "last_recv_preview": text[:50],
        }
        try:
            _BRYAN_LAST_SEEN_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._bryan_last_seen = now_utc
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 寫 {_BRYAN_LAST_SEEN_FILE.name} 失敗: {e}"
            )

    # ── M2 (2026-08-02 10:51 Perplexity 派工): 離線 outbox ──
    # 修法動機: Bry 8/1 報「角色突然全部消失」/「訊息雲裡霧裡」, 排查發現
    # Bry 完全離線 (不在 web + 沒 last_tg_user) 時, 角色主動觸發會:
    # - 原本 log "留 web" 但 web 0 conn 沒人接, 訊息丟掉
    # - 或 fallback 到 per-agent last_tg_user (假設 miku 從沒跟 Bry 對話)
    # 修法: 不丟訊息, append 進 outbox. 累積 N 條或 Bry 上線時 flush 摘要.
    def _load_outbox(self) -> list[dict]:
        if not _OUTBOX_FILE.is_file():
            return []
        try:
            data = json.loads(_OUTBOX_FILE.read_text(encoding="utf-8"))
            msgs = data.get("messages", [])
            if isinstance(msgs, list):
                return msgs
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 讀 {_OUTBOX_FILE.name} 失敗: {e}"
            )
        return []

    def _save_outbox(self) -> None:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _OUTBOX_FILE.write_text(
                json.dumps(
                    {"messages": self._outbox},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(
                f"[ChannelRouter] 寫 {_OUTBOX_FILE.name} 失敗: {e}"
            )

    def _enqueue_outbox(
        self, agent_id: str, text: str, reason: str
    ) -> None:
        """Bry 不在 web + 沒 last_tg_user 時, append 進 outbox.

        Bry 派工 8/2 10:35 維持 throttle 只限 proactive_dm,
        這裡 outbox 接收所有主動觸發 (heartbeat / event / dream / proactive_dm),
        給 Bry 重新上線時的摘要. Night / morning slot 走 diary 不走 AGENT_SPEAK,
        不會進 outbox.
        """
        now_utc = datetime.now(timezone.utc).isoformat()
        self._outbox.append(
            {
                "ts": now_utc,
                "agent_id": agent_id,
                "reason": reason,
                "text": text,
            }
        )
        self._save_outbox()
        logger.info(
            f"[ChannelRouter] outbox enqueue | "
            f"agent={agent_id} reason={reason} "
            f"size={len(self._outbox)}/{OUTBOX_FLUSH_THRESHOLD}"
        )

    async def _flush_outbox_to_bry(self) -> None:
        """Outbox 累積達標或 Bry 上線時, 生成摘要送 Bry 的 TG.

        摘要格式: 「過去 X 小時錯過 Y 條訊息: 角色清單 + 每隻第一句 preview」
        限制: 摘要不超過 800 字 (TG single message 4096 char, 留 buffer)
        發送目標: 全域 last_tg_user (M0.5 修完後 Bry 跟任一角色對話就有)
        """
        if not self._outbox:
            return
        if self._last_tg_user_global is None:
            # 沒有全域 user_id (Bry 從沒跟任何角色對話過, 不可能 flush)
            logger.debug(
                f"[ChannelRouter] outbox 有 {len(self._outbox)} 條但 "
                f"沒有 last_tg_user_global, 留著等 Bry 對話後再 flush"
            )
            return

        # 算時段
        first_ts = self._outbox[0].get("ts", "")
        last_ts = self._outbox[-1].get("ts", "")
        try:
            first_dt = datetime.fromisoformat(first_ts)
            last_dt = datetime.fromisoformat(last_ts)
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            hours_span = (last_dt - first_dt).total_seconds() / 3600.0
        except Exception:
            hours_span = 0.0

        # 角色 + 第一句 preview (去重複角色, 每隻角色只列第一條)
        seen_agents: dict[str, str] = {}
        for m in self._outbox:
            aid = m.get("agent_id", "?")
            if aid not in seen_agents:
                seen_agents[aid] = m.get("text", "")[:50]

        # 組摘要
        lines = [
            f"你離線 {hours_span:.1f} 小時, "
            f"錯過 {len(self._outbox)} 條角色主動訊息:",
        ]
        for aid, preview in seen_agents.items():
            lines.append(f"  - {aid}: {preview}")
        if len(seen_agents) < len(self._outbox):
            lines.append(
                f"  (其他 {len(self._outbox) - len(seen_agents)} 條同角色略)"
            )
        summary = "\n".join(lines)
        if len(summary) > 800:
            summary = summary[:797] + "..."

        # 透過 telegram adapter 送 Bry
        adapter = self._adapters.get("telegram")
        if not adapter:
            logger.warning(
                f"[ChannelRouter] outbox flush 失敗: 沒有 telegram adapter"
            )
            return

        # adapter key 是短碼 (e.g. "yua"), 但 Bry 收到的應該是 broadcast channel
        # 用 "system" 短碼 (M2 預留 channel 給 system 訊息, 不存在)
        # 改: 借用任一 adapter instance 都可以, 因為 send() 只用 user_id
        # 找第一個 adapter 來呼叫 send
        first_adapter = next(iter(self._adapters.values()), None)
        if not first_adapter:
            logger.warning(
                f"[ChannelRouter] outbox flush 失敗: 沒任何 adapter"
            )
            return

        try:
            ok = await first_adapter.send(
                agent_id="system",
                text=summary,
                user_id=self._last_tg_user_global,
            )
            if ok:
                logger.info(
                    f"[ChannelRouter] outbox flush sent to Bry | "
                    f"size={len(self._outbox)} summary_len={len(summary)}"
                )
                # 清 outbox (已經送達)
                flushed_size = len(self._outbox)
                self._outbox = []
                self._save_outbox()
                logger.info(
                    f"[ChannelRouter] outbox cleared after flush | "
                    f"was {flushed_size} msgs"
                )
            else:
                logger.warning(
                    f"[ChannelRouter] outbox flush send failed, "
                    f"outbox 保留 (下次再試)"
                )
        except Exception as e:
            logger.exception(
                f"[ChannelRouter] outbox flush error: {e}"
            )

    async def _maybe_flush_outbox(self) -> None:
        """累積達標自動 flush. 由 _on_agent_speak 在 append 之後呼叫."""
        if len(self._outbox) >= OUTBOX_FLUSH_THRESHOLD:
            logger.info(
                f"[ChannelRouter] outbox 達標 {len(self._outbox)} >= "
                f"{OUTBOX_FLUSH_THRESHOLD}, 自動 flush"
            )
            await self._flush_outbox_to_bry()

    async def inbound(
        self,
        agent_id: str,
        text: str,
        user_id: int,
        channel: str = "telegram",
    ) -> None:
        """Telegram / LINE / WeChat 收到 user 訊息 → 發 USER_MESSAGE 進 Event Bus。

        安全：Phase 5c+ 加 owner whitelist。從 TELEGRAM_OWNER_ID env 讀取合法
        user_id（支援多 owner 用逗號分隔，例如 "12345,67890"）。
        不在白名單的 user → 靜默忽略（不回錯誤、不廣播）。

        session_id 對齊 LLMProxy 讀 history 的 key（_session_key(agent_id)
        回傳 "session_{agent_id}"），這樣 Telegram 跟 WebSocket 的對話歷史
        會寫進同一個 session，LLM 看得到。

        target 必須是「完整 agent_id」（如 "agent_yua"），因為
        AgentConsciousness.register() 用 target_filter=agent_id
        （完整前綴）。早期 code 用 target=agent_id（短碼），bus match 不到。
        """
        # Phase 5c+：owner whitelist（避免陌生人觸發 agent）
        if channel == "telegram":
            allowed_str = os.environ.get("TELEGRAM_OWNER_ID", "")
            if allowed_str:
                allowed = {int(x.strip()) for x in allowed_str.split(",") if x.strip().isdigit()}
                if user_id not in allowed:
                    logger.warning(
                        f"[inbound:{channel}] REJECTED user_id={user_id} "
                        f"(not in owner whitelist)"
                    )
                    return

        # Phase 5c：記「這個 agent 最近一次互動的 tg user」
        # 給主動觸發（heartbeat）fallback 用
        # 注意：key 必須是「完整 agent_id」（full_agent_id）
        # 因為 _on_agent_speak 拿到的 agent_id 是 "agent_yua"（consciousness 來的）
        # 而 inbound 拿到的可能是 "yua"（Telegram callback 短碼）→ 統一用 full
        full_agent_id = (
            agent_id if agent_id.startswith("agent_")
            else f"agent_{agent_id}"
        )
        if channel == "telegram":
            self._last_tg_user[full_agent_id] = user_id
            # M0.5: 寫入全域共享 + persistent state, miku 等沒跟 Bry 對話過
            # 的角色也能 fallback TG (Bry 8/1 報 miku miss 的根因)
            self._save_last_tg_user_global(user_id, full_agent_id)
            # M0.5: 記 Bry 最後一次訊息時間, throttle proactive_dm 用
            self._save_bryan_last_seen(full_agent_id, text)
            # M2 (Bry 8/2 10:51 派工): Bry 上線了, 立即 flush outbox
            # Bry 重新上線是「過去累積的訊息要立刻摘要給 Bry 看」的信號
            if self._outbox:
                logger.info(
                    f"[ChannelRouter] Bry 上線, 立即 flush outbox | "
                    f"size={len(self._outbox)}"
                )
                # 排程 flush (不阻塞 inbound 流程)
                create_managed_task(self._flush_outbox_to_bry())
            # Step 1 fix: session 隔離每個 user，避免陌生人污染 Bryan 記憶
            # Bryan: session_1696287850_agent_yua, 陌生人: session_99999999_agent_yua
            tg_session = f"session_{user_id}_{full_agent_id}"
            self._last_tg_session[full_agent_id] = tg_session

        # Phase 5c+ fix：session_id 跟 LLMProxy _session_key 對齊
        # Bryan 的 Telegram 記憶: session_1696287850_agent_yua
        # 陌生人的 Telegram 記憶: session_99999999_agent_yua（永遠不混）
        session_id = f"session_{user_id}_{full_agent_id}"
        event = SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source=f"{channel}:{user_id}",
            target=full_agent_id,  # 私訊模式，target = 完整 agent_id
            payload={
                "content": text,        # USER_MESSAGE 慣例用 content（consciousness.py 看得到）
                "text": text,           # 跟 LLMProxy 的 _on_user_message 一致
                "user_id": str(user_id),
                "agent_id": full_agent_id,
                "target_agent": full_agent_id,
                "channel": channel,
                "target_channel": channel,   # 透傳到 AGENT_INTENT → AGENT_SPEAK
                "target_user_id": user_id,   # 透傳，給 router outbound 用
                "mode": "private",           # Telegram 預設一對一私聊
            },
            session_id=session_id,
        )
        await self._bus.publish(event)
        logger.info(
            f"[inbound:{channel}] {full_agent_id} ← user_id={user_id} "
            f"session={session_id} text={text[:50]!r}"
        )
