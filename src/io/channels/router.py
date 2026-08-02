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
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

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
_STATE_DIR = Path("data/state")
_LAST_TG_USER_FILE = _STATE_DIR / "last_tg_user.json"
_BRYAN_LAST_SEEN_FILE = _STATE_DIR / "bryan_last_seen.json"


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
        # Phase 5+ (2026-07-15 Bry 拍板): 配對 AGENT_SPEAK → AGENT_AUDIO_READY
        # AGENT_SPEAK 送 text 到 telegram user X 後,把 (X, ts) 暫存到這;
        # AGENT_AUDIO_READY 來時用 agent_id 找到 X,把 mp3 用 send_voice 推給他
        # 過期 60s(正常 TTS 1-3s 完成,Lesson 36E Bry 拍板 2026-07-26 12:33: 60s 給 LLMJudge + provider 延遲緩衝, 30s 對 LLMJudge retry 撞 length 太短)
        # v32 7/26 23:48 觀察: TTS 寫完 mp3 後 AGENT_AUDIO_READY event 在 bus queue 卡 4 分鐘才 fire (publish → consumer 延遲),
        # 60s 過期太短 → voice 丟掉。Bry 拍板 v32.1 (7/26 23:54): 拉長到 300s 涵蓋 bus 排隊延遲。
        # key = full agent_id (e.g. "agent_mahiru")
        self._pending_voice_target: dict[str, tuple[int, float]] = {}
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
                    # 從來沒跟 user 互動過，沒辦法送
                    # 留給 web 廣播（雖然沒人接，總比丟掉好）
                    logger.info(
                        f"[ChannelRouter] {agent_id} 主動觸發，"
                        f"web 0 conn 但沒有 last_tg_user, 留 web"
                    )
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
                # 暫存 (user_id, ts),等 AGENT_AUDIO_READY 把 mp3 也推過去
                if target_channel == "telegram":
                    self._pending_voice_target[agent_id] = (
                        int(target_user_id),
                        time.time(),
                    )
                    logger.debug(
                        f"[ChannelRouter] pending voice target set: "
                        f"{agent_id} → user {target_user_id}"
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
        rel_path = Path("data/soul") / agent_id / "relationships.json"
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

        # 找配對
        target = self._pending_voice_target.get(agent_id)
        if not target:
            # 沒有 pending = 這個 audio 不是要送 TG 的（可能是 web 觸發）
            # 也有可能過期了被清掉 → 屬於正常情況,不當 error
            logger.debug(
                f"[ChannelRouter:audio] no pending TG target for {agent_id}, "
                f"skip voice push (可能是 web-only 觸發)"
            )
            return
        user_id, ts = target
        # 過期檢查
        if time.time() - ts > self._VOICE_PAIR_EXPIRY_SEC:
            logger.warning(
                f"[ChannelRouter:audio] pending voice target expired "
                f"({time.time() - ts:.1f}s > {self._VOICE_PAIR_EXPIRY_SEC}s) "
                f"for {agent_id} → user {user_id}, drop"
            )
            # 清掉,避免重複檢查
            self._pending_voice_target.pop(agent_id, None)
            return

        # 拿到就 pop,避免同一個 audio 推兩次
        self._pending_voice_target.pop(agent_id, None)

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
