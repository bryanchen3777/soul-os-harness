"""
src/io/channels/telegram.py
Soul OS Phase 5a — Telegram Channel Adapter

十個 Bot 對應十個 Agent（Phase 5a ~ 12）:
  yua     → @Yua_Hermes_bot
  ruaka   → @Ruka_Clawra_bot
  akane   → @Akane_Clawra_bot
  rem     → @Rem_Hermes_bot
  ram     → <Ram 對應 bot, Bryan 從 BotFather 拿>
  mahiru  → <Mahiru 對應 bot>
  anna    → <Anna 對應 bot>
  mai     → <Mai 對應 bot>
  miku    → <Miku 對應 bot>
  aoi     → <Aoi 對應 bot>

Bot token 從環境變數讀（.env），不寫死 source code。
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import time
import traceback
from typing import Callable, Awaitable, Optional

from telegram import Update
from telegram.error import Conflict, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from .base import ChannelAdapter, OnMessageCallback
# TTS 全域開關 (Bry 派工 2026-08-15): /tts on|off 切換是否使用 TTS
from src.llm.tts_toggle import is_tts_enabled, set_tts_enabled

# VC-2.5：Bryan 的 TG 對話寫入共享短期會話流（SessionStore）
# Bryan 的 TG user id 見 src/memory/middleware.py L135（TG: 1696287850）
_BRYAN_TG_USER_ID = 1696287850


def _append_session_turn(agent_id: str, role: str, text: str, user_id) -> None:
    """Bryan 的 TG 回合寫入 SessionStore（fail-silent，0 影響主流程）。"""
    try:
        if int(user_id) != _BRYAN_TG_USER_ID:
            return
        from clients.voice_companion.session_store import SessionStore
        full_agent_id = (
            agent_id if agent_id.startswith("agent_")
            else f"agent_{agent_id}"
        )
        SessionStore().append_turn(
            full_agent_id, "user_bryan", role, text, "telegram"
        )
    except Exception:
        pass

logger = logging.getLogger("soul_os.channels.telegram")

# ============================================================================
# TG-STABILITY-1 (2026-09-15): polling 存活監督常數
#
# 事故背景: 2026-09-15 16:51-16:54 TG 入站輪詢靜默死亡 (日誌零 traceback),
# Telegram 端累積 4 則 update 無人領取, 停服 1h40m, pid 對 149.154.166.110:443
# 留下 8 條 CLOSE_WAIT (socket 洩漏)。根因不是「錯誤沒印」, 而是「沒有任何
# 監督層在觀察 poller 的死」。本區常數定義監督/重建參數。
# ============================================================================

#: 監督層輪詢週期（秒）。預設 5 秒；__init__ 的 supervise_interval 可覆寫
#: （測試注入短週期, 不讓測試真的等 5 秒）。
SUPERVISE_INTERVAL_SECONDS = 5.0

#: 指數退避序列（秒）; idx = min(rebuild_count, len-1) ⇒ 上限 60 秒。
REBUILD_BACKOFF_SECONDS = (1, 2, 4, 8, 16, 32, 60)

#: 連續重建門檻。達到此值 ⇒ 進入「慢速續試期」（D-3: 永不停止監督、永不放棄）。
MAX_CONSECUTIVE_REBUILDS = 5

#: 慢速續試間隔（秒）。連續重建達 MAX_CONSECUTIVE_REBUILDS 之後, 每次重試固定
#: 等這麼久, 且每次都記 logger.critical（持續可見, 不得只剩一行 log 就永久躺平）。
SLOW_RETRY_SECONDS = 300.0

#: 連續健康達此秒數 ⇒ 歸零 _rebuild_counts（「連續」的定義）。
HEALTHY_RESET_SECONDS = 600.0

#: 每 bot 只保留最近 N 筆錯誤（有界, 不得無限成長）。
MAX_LAST_ERRORS = 5


# ============================================================================
# TG-STALL-DETECTION-1 (2026-09-16): 「卡住但沒死」的停滯偵測
#
# 事故背景: TG 入站無聲停止約 1h40m, 指紋是 pid 對 149.154.166.110:443 留下多條
# CLOSE_WAIT、零 ERROR、零 traceback、getUpdates 不再成功。
# 獨立審計讀 ptb 22.8 原始碼證明: Updater.running 只是純旗標, start_polling 的
# 輪詢任務沒有 done-callback, network_retry_loop(max_retries=-1) 遇錯永不中止也
# 不清旗標 ⇒ 「running=True 但 getUpdates 再也沒成功」不會被判死。
# 鐵則: 健康判據必須是「最近一次真實成功」, 不是任務狀態旗標。
# ============================================================================

#: 停滯門檻（秒）。ptb 長輪詢預設約 10s 一輪 —— 即使沒有任何新訊息, getUpdates
#: 也會「成功回傳空 list」。因此連續這麼久沒有任何一次成功 ⇒ 判定停滯
#: （卡住但沒死: updater.running 仍為 True, 但連線已卡死 / 網路已斷）。
STALL_THRESHOLD_SECONDS = 120.0


# ============================================================================
# INFRA-WATCHDOG-TG-HEALTH (2026-09-16): 心跳落盤 —— 真活性訊號的「唯讀外部形式」
#
# 事故背景: 2026-09-15 TG 入站輪詢靜默死亡 1h40m, 進程內偵測（前一票的
# STALL_THRESHOLD_SECONDS）只在進程還活著時有效; 進程整個死掉 / 事件迴圈卡死時
# **沒有任何外部信號**。本票把「實質成功 getUpdates 的時間戳」落成一個唯讀心跳檔
# （data/heartbeats/telegram_channel.json）, 由 scripts/_watchdog.ps1 每一 tick
# 讀取並告警（**只告警, 永不動作**）。
#
# 資料源鐵則: 一律以「實質成功 getUpdates 的時間戳」為準（snapshot_health() 體系）。
# **嚴禁**回退 `updater.running` —— 它只是純旗標, 判不出「卡住但沒死」。
#
# 🔑 時鐘語意（本票最容易做錯的地方）:
#   既有 per-bot `last_poll_success` 是 **time.monotonic() 秒**
#   （None = 當前 app 從未成功）; 而 watchdog 端要用 **Unix epoch** 做
#   `$nowEpoch - $ts` 減法。**兩者混用會產生偽 CRITICAL 風暴**（monotonic
#   起點是開機時間, 通常遠小於 epoch ⇒ age 天文數字）。
#   ⇒ 另記 per-bot `last_success_epoch`（time.time() 的 float epoch）, 供落盤用。
# ============================================================================

#: 心跳停滯門檻（秒）。watchdog 讀取端用**同一個數值**判定 stale / CRITICAL。
HEARTBEAT_STALE_SECONDS = 180.0

#: 心跳寫入週期（秒）。start() 完成後會**立即同步先寫一次**（不等這個週期）,
#: 之後才由週期協程每 HEARTBEAT_INTERVAL_SECONDS 秒重寫。
HEARTBEAT_INTERVAL_SECONDS = 30.0

#: 心跳週期的睡眠原語。**刻意在模組載入時捕獲**, 不走 `asyncio.sleep` 的動態查找。
#: 理由: 既有的 telegram 測試（`fast_sleep` / `gated_sleep`）會把
#: `tg_module.asyncio.sleep` 換成「記錄延遲 + 立即讓出」的假版本, 用來斷言
#: 重建退避數列（1,2,4,8,16,32,60）。心跳的 30 秒週期若共用同一個 patch 點,
#: 會 (a) 把 30.0 混進那些退避斷言 ⇒ 既有測試由綠轉紅,
#: (b) 讓心跳在測試中退化成熱迴圈（假 sleep 立即返回）。
#: 心跳與重建退避是**兩條互不相干的週期**, 不得互相干擾。
_HEARTBEAT_SLEEP = asyncio.sleep


# 環境變數名稱常數（方便測試時 mock）
ENV_TOKEN_YUA = "TELEGRAM_BOT_YUA"
ENV_TOKEN_RUKA = "TELEGRAM_BOT_RUKA"
ENV_TOKEN_AKANE = "TELEGRAM_BOT_AKANE"
ENV_TOKEN_REM = "TELEGRAM_BOT_REM"
# Phase 7-11: Ram / Mahiru / Anna / Mai / Miku（5 個新加 agent）
ENV_TOKEN_RAM = "TELEGRAM_BOT_RAM"
ENV_TOKEN_MAHIRU = "TELEGRAM_BOT_MAHIRU"
ENV_TOKEN_ANNA = "TELEGRAM_BOT_ANNA"
ENV_TOKEN_MAI = "TELEGRAM_BOT_MAI"
ENV_TOKEN_MIKU = "TELEGRAM_BOT_MIKU"
# Phase 12: Aoi (弱角友崎同學)
ENV_TOKEN_AOI = "TELEGRAM_BOT_AOI"

# Agent 對應環境變數
# 註: key 用短 ID (e.g. "yua", "ram") 不是 "agent_yua"
# 這跟 configs/default.yaml 用 "agent_yua" 脫鉤,
# AGENT_ENV_MAP 跟 config 對齊需另開票修(本票 scope: 補 5 個新加 agent 的 env 對應)
AGENT_ENV_MAP = {
    "yua":    ENV_TOKEN_YUA,
    "ruka":   ENV_TOKEN_RUKA,
    "akane":  ENV_TOKEN_AKANE,
    "rem":    ENV_TOKEN_REM,
    "ram":    ENV_TOKEN_RAM,
    "mahiru": ENV_TOKEN_MAHIRU,
    "anna":   ENV_TOKEN_ANNA,
    "mai":    ENV_TOKEN_MAI,
    "miku":   ENV_TOKEN_MIKU,
    "aoi":    ENV_TOKEN_AOI,
}


def _load_tokens() -> dict[str, str]:
    """從環境變數讀三個 bot token。

    缺一就 raise（fail-fast，避免啟動後某個 agent 默默不能通訊）。
    """
    tokens = {}
    missing = []
    for agent_id, env_key in AGENT_ENV_MAP.items():
        val = os.environ.get(env_key)
        if not val:
            missing.append(env_key)
        else:
            tokens[agent_id] = val
    if missing:
        raise RuntimeError(
            f"Missing Telegram bot tokens in env: {missing}. "
            f"請在 .env 設定 TELEGRAM_BOT_YUA / RUKA / AKANE / REM / RAM / MAHIRU / ANNA / MAI / MIKU / AOI。"
        )
    return tokens


class TelegramAdapter(ChannelAdapter):
    channel_id = "telegram"

    def __init__(self, tokens: Optional[dict[str, str]] = None,
                 supervise_interval: float = SUPERVISE_INTERVAL_SECONDS,
                 monotonic_clock: Optional[Callable[[], float]] = None,
                 wall_clock: Optional[Callable[[], float]] = None,
                 heartbeat_path: Optional[str] = None):
        """Args:
            tokens: 測試用覆寫；正式環境省略 → 從 env 讀
            supervise_interval: 存活監督檢查週期（秒）。預設 5 秒；測試注入短週期。
            monotonic_clock: 時間來源（秒）。預設 time.monotonic；測試注入假時鐘
                （沿用 supervise_interval 的注入風格, 生產路徑不留需要真等待的後門）。
            wall_clock: **牆鐘**來源（Unix epoch 秒）。預設 time.time；測試注入假時鐘。
                （INFRA-WATCHDOG-TG-HEALTH：心跳檔一律用 epoch, 與 monotonic 分開兩條。）
            heartbeat_path: 心跳檔路徑。None ⇒ 預設
                `data_root()/heartbeats/telegram_channel.json`（生產 = repo
                `data/heartbeats/telegram_channel.json`）。**必須可注入**:
                測試一律走注入的臨時路徑, 絕不寫生產 data/**。
        """
        self._tokens = tokens or _load_tokens()
        self._apps: dict[str, Application] = {}
        self._on_message: Optional[OnMessageCallback] = None
        # 方案 C (2026-09-08): 409 Conflict 重試計數, ≥3 次 fail-closed 停止該 bot polling
        self._conflict_counts: dict[str, int] = {}

        # --- TG-STABILITY-1 (2026-09-15): 輪詢存活監督與異常重建 ---
        self._supervise_interval = float(supervise_interval)
        #: 每 bot 一個存活監督 task
        self._supervisors: dict[str, asyncio.Task] = {}
        #: 關閉中旗標 ⇒ 監督層直接 return, 永不重建（關機時無限重建 = 關不掉服務）
        self._closing: bool = False
        #: 每 bot 連續重建次數（重建上限 / 退避 index 都用它）
        self._rebuild_counts: dict[str, int] = {}
        #: 每 bot 開始連續健康的 monotonic 時間戳（None = 目前不健康）
        self._healthy_since: dict[str, Optional[float]] = {}
        #: 每 bot 一把鎖 ⇒ 防止同 bot 併發重建 ⇒ 避免雙實例 ⇒ 避免 409 風暴
        self._locks: dict[str, asyncio.Lock] = {}
        #: 每 bot 最近 N 筆 TelegramError（含完整 traceback），有界
        self._last_errors: dict[str, list[str]] = {}
        #: 上一次 409 fail-closed 停止的 bot 集合 ⇒ 重建直接從最大退避（60s）起
        self._conflict_failclosed: set[str] = set()

        # --- TG-STALL-DETECTION-1 (2026-09-16): 真活性訊號 ---
        #: 時間來源（秒）。預設 time.monotonic（== asyncio loop.time()）; 測試可注入。
        self._now: Callable[[], float] = monotonic_clock or time.monotonic
        #: 每 bot 最近一次 getUpdates 成功回傳的 monotonic 時間
        #: （None = 當前 app 從未成功過 ⇒ 以 _app_built_at 起算, 不得當成永遠健康）
        self._last_poll_success: dict[str, Optional[float]] = {}
        #: 每 bot 累計成功的 getUpdates 次數（跨重建累計, 供事故一眼診斷）
        self._poll_success_counts: dict[str, int] = {}
        #: 每 bot 累計失敗的 getUpdates 次數（跨重建累計）
        self._poll_failure_counts: dict[str, int] = {}
        #: 每 bot 當前 app 建構完成時間（last_poll_success is None 時的起算點）
        self._app_built_at: dict[str, float] = {}

        # --- INFRA-WATCHDOG-TG-HEALTH (2026-09-16): 心跳落盤（寫給 watchdog 讀）---
        #: 牆鐘來源（Unix epoch 秒）。預設 time.time；測試注入假時鐘。
        #: 與 `self._now`（monotonic）是**兩條獨立的時鐘**, 不得互相替代。
        self._wall_clock: Callable[[], float] = wall_clock or time.time
        #: 進程啟動 epoch（adapter 建構時取一次, **永不再改**）。
        #: 冷啟動（本進程尚無任何成功 getUpdates）時, 心跳導出值用它當起算點 ⇒
        #: watchdog 拿到的是「永不為 null」的數值, 且 180 秒內不告警。
        self._process_start_epoch: float = float(self._wall_clock())
        #: 每 bot 最近一次 getUpdates 成功的 **epoch** 秒（None = 本進程從未成功）。
        #: ⚠ **絕不被 `_build_app()` 歸零** —— 跨 app 重建存活。理由見
        #: `_derive_last_success_ts()` 的 docstring（拿 _app_built_at 當退回值
        #: 會讓 180 秒告警永遠不響 = 把告警閹割）。
        self._last_success_epoch: dict[str, Optional[float]] = {}
        #: 心跳週期協程（start() 內建立, stop() 內取消, 不得阻塞關機）
        self._heartbeat_task: Optional[asyncio.Task] = None
        #: 心跳檔路徑覆寫（None ⇒ 走 `_heartbeat_path()` 的預設解析）
        self._heartbeat_path_override: Optional[str] = (
            str(heartbeat_path) if heartbeat_path is not None else None
        )

    # ------------------------------------------------------------------
    # INFRA-WATCHDOG-TG-HEALTH: 心跳落盤（寫入端）
    # ------------------------------------------------------------------
    def _heartbeat_path(self) -> str:
        """心跳檔路徑（可注入；預設 `data_root()/heartbeats/telegram_channel.json`）。

        走 `src.paths.data_root()`（P0.5 canonical resolver）而**不是**硬寫
        `"data/..."` 字面值:
          - 生產（`SOUL_OS_DATA_DIR` 未設）解析結果就是 repo 的
            `data/heartbeats/telegram_channel.json` —— 與工單指定的預設路徑一致。
          - 測試環境（`tests/conftest.py` 的 autouse 隔離 fixture 會把
            `SOUL_OS_DATA_DIR` 指向 tmp）**自動被隔離** ⇒ 既有測試呼叫 `start()`
            不會污染生產 `data/**`（站規: 團隊不得寫生產 data/）。
        """
        if self._heartbeat_path_override is not None:
            return self._heartbeat_path_override
        try:
            from src.paths import data_root
            return str(data_root() / "heartbeats" / "telegram_channel.json")
        except Exception:
            # 極端情況下（src.paths 不可用）仍要有路徑, 不得讓心跳整體失效
            return os.path.join("data", "heartbeats", "telegram_channel.json")

    def _derive_last_success_ts(self, agent_id: str) -> float:
        """導出值: `last_success_epoch` 有值就用它, 否則用 `process_start_epoch`。

        ⇒ **永不為 None** ⇒ watchdog 端可以純數值運算（`$nowEpoch - $ts`）,
        不需要處理 null。

        ⚠ **為什麼退回值不能用 `_app_built_at`**: `_build_app()` 每一次（初始啟動
        與每一次重建）都會把 `_app_built_at[agent_id]` 重設成「現在」⇒ 若拿它當
        退回值, **每次重建都把時鐘歸零** ⇒ watchdog 的 180 秒停滯告警**永遠不會響**
        （真停滯反而看不到）—— 等於把告警閹割。這是本決策的核心理由。
        `process_start_epoch` 只在 adapter 建構時取一次、永不再改 ⇒
          - 冷啟動（本進程尚無成功）: 時鐘自進程啟動起算, 180 秒內不告警 ✓
          - 真停滯: 上一次真實成功的 epoch 一直留著 ⇒ 一定會超過門檻而告警 ✓
        """
        epoch = self._last_success_epoch.get(agent_id)
        if epoch is None:
            return float(self._process_start_epoch)
        return float(epoch)

    def _heartbeat_snapshot(self, now_epoch: Optional[float] = None) -> dict:
        """組出心跳檔內容（純資料, **絕不含 token**）。

        格式（`updated_at` / `last_success_ts` 皆為 epoch 浮點數）::

            {"updated_at": 1758026400.0, "pid": 10444,
             "bots": {"mai": {"last_success_ts": 1758026398.0,
                              "age_s": 2.0, "status": "ok"}}}

        `age_s` 是我方加值欄位（供人眼與測試）; watchdog 仍以 epoch 減法為準。
        """
        if now_epoch is None:
            now_epoch = float(self._wall_clock())
        now_epoch = float(now_epoch)
        agent_ids = sorted(set(self._tokens) | set(self._apps))
        bots: dict = {}
        for agent_id in agent_ids:
            last_success_ts = self._derive_last_success_ts(agent_id)
            age_s = now_epoch - last_success_ts
            bots[agent_id] = {
                "last_success_ts": last_success_ts,
                "age_s": age_s,
                "status": (
                    "ok" if age_s <= HEARTBEAT_STALE_SECONDS else "stale"
                ),
            }
        return {
            "updated_at": now_epoch,
            "pid": os.getpid(),
            "bots": bots,
        }

    def _write_heartbeat(self) -> bool:
        """原子落盤心跳檔。**失敗一律不得影響輪詢**。

        手法: 先寫同目錄 `telegram_channel.json.tmp.<pid>`, 再 `os.replace()`
        原子替換（同目錄 rename ⇒ 讀者永不看到半截檔）。

        整個寫入包在 try/except Exception 內, 失敗只記 WARNING 後吞掉:
        `os.replace` 在 Windows 上可能因讀者佔用而 `PermissionError`
        ⇒ 下一 tick 重試即可, **絕不拋出**（拋出會往上打到輪詢/監督路徑）。
        """
        try:
            now_epoch = float(self._wall_clock())
            payload = self._heartbeat_snapshot(now_epoch)
            path = self._heartbeat_path()
            directory = os.path.dirname(os.path.abspath(path))
            os.makedirs(directory, exist_ok=True)
            tmp_path = f"{path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
            return True
        except Exception as e:
            logger.warning(
                f"[TG] heartbeat write failed (ignored, retried next tick): {e!r}"
            )
            return False

    async def _heartbeat_loop(self) -> None:
        """週期寫心跳（HEARTBEAT_INTERVAL_SECONDS）。任何錯誤都不得外拋。

        第一筆由 `start()` 同步寫入（冷啟動首寫）; 這裡只補後續週期筆。
        必須在 `stop()` 內被取消, 不得阻塞關機。

        睡眠走 `_HEARTBEAT_SLEEP`（模組載入時捕獲的真 `asyncio.sleep`）,
        刻意避開測試對 `tg_module.asyncio.sleep` 的 monkeypatch, 理由見該常數註解。
        """
        while True:
            try:
                await _HEARTBEAT_SLEEP(HEARTBEAT_INTERVAL_SECONDS)
                self._write_heartbeat()
            except asyncio.CancelledError:
                logger.debug("[TG] heartbeat task cancelled")
                raise
            except Exception:
                # 通道層自癒: 單次迭代失敗不得讓心跳週期永久停擺
                logger.exception("[TG] heartbeat iteration error (absorbed)")

    def _make_handler(self, agent_id: str):
        """每個 bot 一個 handler，closure 帶 agent_id。"""
        async def handler(
            update: Update,
            ctx: ContextTypes.DEFAULT_TYPE,
        ):
            if not update.message:
                return
            raw_text = update.message.text or ""
            user = update.message.from_user
            if not user:
                return
            user_id = user.id
            # JP rollback (Bry 拍板 2026-07-22 20:59): Plan A 砍掉
            # 不再 user 中文先翻日文, text 直接送 LLMProxy
            text = raw_text
            logger.info(
                f"[TG:{agent_id}] recv from {user_id} "
                f"(@{user.username or '?'}): {text[:50]!r}"
            )
            _append_session_turn(agent_id, "user", text, user_id)  # VC-2.5
            if self._on_message:
                # Phase 5d+：typing indicator — 立刻送 typing，然後背景每 4s 重送
                # 直到 callback 完成。LLM 慢的時候不會讓用戶以為 bot 壞了。
                typing_task = asyncio.create_task(
                    self._keep_typing(agent_id, user_id)
                )
                try:
                    await self._on_message(agent_id, text, user_id)
                except Exception as e:
                    logger.exception(
                        f"[TG:{agent_id}] on_message callback error: {e}"
                    )
                finally:
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
        return handler

    def _make_tts_command_handler(self, agent_id: str):
        """/tts on | /tts off | /tts — 全域 TTS 開關（Bry 派工 2026-08-15）。

        全域開關：任何角色 bot 收到 /tts 都會切換全域 TTS 狀態，
        影響全部 10 個角色（FishTTSHandler 是單一 handler 訂閱所有 AGENT_SPEAK）。
        """
        async def handler(
            update: Update,
            ctx: ContextTypes.DEFAULT_TYPE,
        ):
            if not update.message:
                return
            user = update.message.from_user
            if not user:
                return
            args = ctx.args or []
            cmd = args[0].lower() if args else ""
            if cmd in ("on", "enable", "1"):
                ok = set_tts_enabled(True)
                reply = "✅ TTS 已開啟" if ok else "⚠️ TTS 開關寫入失敗，請看 server log"
            elif cmd in ("off", "disable", "0"):
                ok = set_tts_enabled(False)
                reply = "🔇 TTS 已關閉" if ok else "⚠️ TTS 開關寫入失敗，請看 server log"
            else:
                state = "開啟" if is_tts_enabled() else "關閉"
                reply = f"TTS 目前狀態：{state}\n用法：/tts on 或 /tts off"
            logger.info(
                f"[TG:{agent_id}] /tts cmd={cmd!r} from {user.id} → {reply}"
            )
            try:
                await update.message.reply_text(reply)
            except Exception as e:
                logger.error(f"[TG:{agent_id}] /tts reply error: {e}")
        return handler

    # ------------------------------------------------------------------
    # 單一建構路徑 (TG-STABILITY-1 §1): 初始啟動與重建都必須走這裡,
    # 確保 get_updates_request(HTTPXRequest(connection_pool_size=5, pool_timeout=1.0))
    # 只存在於這一處。
    # ------------------------------------------------------------------
    def _build_app(self, agent_id: str, token: str) -> Application:
        """建構單一 bot 的 Application（唯一的建構路徑）。"""
        # 方案 D (2026-09-08): 限制 get_updates 連接池大小, 防極端並發下
        # httpx/httpcore 連接池過載損壞 Windows IOCP (crash root cause 2026-09-08)
        app = (
            ApplicationBuilder()
            .token(token)
            .get_updates_request(
                HTTPXRequest(connection_pool_size=5, pool_timeout=1.0)
            )
            .build()
        )
        # TTS 開關指令（Bry 派工 2026-08-15）：/tts on|off
        app.add_handler(
            CommandHandler("tts", self._make_tts_command_handler(agent_id))
        )
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._make_handler(agent_id),
            )
        )
        # TG-STALL-DETECTION-1 §1: 真活性訊號。單一建構路徑 ⇒ 初始啟動與重建
        # 都會裝上心跳 wrapper。
        self._install_poll_heartbeat(agent_id, app)
        # 新 app 的活性從零起算: 重建 ⇒ 新的 STALL_THRESHOLD_SECONDS 觀測窗。
        # 不得沿用舊 app 的成功時間, 否則重建完會立刻再被判停滯 ⇒ 重建風暴。
        self._last_poll_success[agent_id] = None
        self._app_built_at[agent_id] = self._now()
        # ⚠ INFRA-WATCHDOG-TG-HEALTH: 這裡**只**歸零 monotonic 的
        # `_last_poll_success`（上一票的語意, 逐字不變）。
        # `_last_success_epoch` **絕不在這裡歸零** —— 它是跨重建存活的真活性
        # epoch, 心跳導出值靠它才能在「重建完但還沒成功」時仍指回上一次真實成功,
        # 而不是被歸零成現在（那會讓 180 秒停滯告警永遠不響）。
        return app

    def _install_poll_heartbeat(self, agent_id: str, app) -> None:
        """TG-STALL-DETECTION-1 §1: 包一層 `app.bot.get_updates`, 取得真活性訊號。

        ptb 的輪詢路徑（`telegram/ext/_updater.py` 的 `polling_action_cb`）是
        `await self.bot.get_updates(...)`, 每次輪詢都重新取屬性 ⇒ 實例屬性會命中
        這裡的 wrapper。wrapper:
          - 成功回傳 ⇒ 記錄 `last_poll_success` + 累計 `poll_success_count`
            （成功但沒有新訊息 = 回傳空 list 也算成功）。
          - 拋例外 ⇒ 累計 `poll_failure_count` + 把完整 traceback 寫進**既有**的
            有界 `_last_errors`（不另造第二套），然後**原樣重拋**
            （絕不吞掉例外、絕不改變 ptb `network_retry_loop` 的重試語意）。
          - 透明: 參數/回傳值/例外型別/時序語意都不變。

        ⚠ 這裡**必須**用 `object.__setattr__`, 不得改回普通賦值
        （`app.bot.get_updates = wrapper`）:
        ptb 22.8 的 `TelegramObject.__setattr__` 在 `_frozen=True` 時對非底線屬性
        直接 raise, 而 `Bot.__init__` 尾端會呼叫 `self._freeze()`
        ⇒ 普通賦值在生產路徑**必定**失敗:
        `AttributeError: Attribute 'get_updates' of class 'ExtBot' can't be set!`
        `object.__setattr__` 直接寫入實例 `__dict__`（實例屬性遮蔽類別方法）,
        且**不動 `_frozen`**（不解除 ptb 的不可變保護）。
        """
        bot = getattr(app, "bot", None)
        if bot is None:
            logger.error(
                f"[TG:{agent_id}] poll heartbeat NOT installed (app has no .bot) — "
                f"stall detection degraded to app-built-at only"
            )
            return

        original = getattr(bot, "get_updates", None)
        if original is None:
            logger.error(
                f"[TG:{agent_id}] poll heartbeat NOT installed "
                f"(bot has no get_updates) — stall detection degraded"
            )
            return

        async def _heartbeat_get_updates(*args, **kwargs):
            try:
                result = await original(*args, **kwargs)
            except asyncio.CancelledError:
                # 關機語意: 取消不是「輪詢失敗」, 不計數; 仍必須原樣重拋
                raise
            except BaseException as exc:
                # 記錄後原樣重拋: 不得吞掉例外、不得改變 ptb 的重試語意
                self._record_poll_failure(agent_id, exc)
                raise
            self._record_poll_success(agent_id)
            return result

        try:
            # 只為可讀性/可除錯性保留原函式 metadata（不影響行為）
            functools.update_wrapper(_heartbeat_get_updates, original)
        except Exception:
            logger.debug(
                f"[TG:{agent_id}] heartbeat wrapper metadata copy skipped"
            )

        try:
            object.__setattr__(bot, "get_updates", _heartbeat_get_updates)
        except Exception:
            logger.exception(
                f"[TG:{agent_id}] poll heartbeat install FAILED — stall detection "
                f"degraded to app-built-at only"
            )
            return

        if getattr(bot, "get_updates", None) is not _heartbeat_get_updates:
            # 遮蔽沒生效 ⇒ 活性訊號永遠是 None ⇒ 一定要吵（絕不靜默回到只看 running）
            logger.error(
                f"[TG:{agent_id}] poll heartbeat install did not take effect "
                f"(instance attribute shadowing failed) — stall detection degraded"
            )

    def _record_poll_success(self, agent_id: str) -> None:
        """真活性訊號: 一次 getUpdates 成功（含回傳空 list）。

        INFRA-WATCHDOG-TG-HEALTH: 同時記 **epoch** 版的成功時間
        （`_last_success_epoch`）—— 這是心跳檔與 watchdog 唯一可用的時鐘語意。
        既有 monotonic 欄位（`_last_poll_success`）語意逐字不變。
        """
        self._poll_success_counts[agent_id] = (
            self._poll_success_counts.get(agent_id, 0) + 1
        )
        self._last_poll_success[agent_id] = self._now()
        try:
            self._last_success_epoch[agent_id] = float(self._wall_clock())
        except Exception as e:
            # 牆鐘讀取失敗不得影響輪詢（心跳退化為 process_start_epoch 起算）
            logger.warning(
                f"[TG:{agent_id}] wall clock read failed — heartbeat epoch "
                f"not updated: {e!r}"
            )

    def _record_poll_failure(self, agent_id: str, exc: BaseException) -> None:
        """一次 getUpdates 失敗（例外原樣重拋之前先記錄）。"""
        self._poll_failure_counts[agent_id] = (
            self._poll_failure_counts.get(agent_id, 0) + 1
        )
        self._record_last_error(agent_id, exc)

    def _record_last_error(self, agent_id: str, exc: BaseException) -> None:
        """把例外（含完整 traceback）寫進既有有界 `_last_errors` 機制。

        TG-STALL-DETECTION-1 §1: 輪詢心跳 wrapper 與 ptb error_callback 共用
        這一個儲存（不得另造第二套）。有界: 每 bot 只留最近 MAX_LAST_ERRORS 筆。
        """
        try:
            entry = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            bucket = self._last_errors.setdefault(agent_id, [])
            bucket.append(entry)
            del bucket[:-MAX_LAST_ERRORS]
        except Exception:
            logger.exception(
                f"[TG:{agent_id}] failed to record error in _last_errors"
            )

    def _poller_health(self, agent_id: str, app) -> tuple[bool, float, bool]:
        """TG-STALL-DETECTION-1 §2: 健康判據 = 「最近一次真實成功」, 不是任務旗標。

        回傳 `(healthy, 距最近一次成功幾秒, 是否曾經成功過)`:
          - `running=False` ⇒ 不健康（既有語意不變）。
          - `running=True` 但距最近一次成功 >= `STALL_THRESHOLD_SECONDS`
            ⇒ 停滯（卡住但沒死）。
          - `last_poll_success is None`（當前 app 從未成功）⇒ 以 **app 建構完成
            時間**起算, 超過門檻即視為停滯 —— 不得讓 None 被當成「永遠健康」。
        """
        updater = getattr(app, "updater", None) if app is not None else None
        running = bool(getattr(updater, "running", False))

        last_success = self._last_poll_success.get(agent_id)
        ever_succeeded = last_success is not None
        if last_success is not None:
            reference = last_success
        else:
            reference = self._app_built_at.get(agent_id)
            if reference is None:
                # 未經 _build_app 建構的 app（例如測試直接注入）⇒ 以首次觀測起算
                reference = self._now()
                self._app_built_at[agent_id] = reference

        elapsed = self._now() - reference
        healthy = running and elapsed < STALL_THRESHOLD_SECONDS
        return healthy, elapsed, ever_succeeded

    async def start(self, on_message: OnMessageCallback) -> None:
        """啟動十個 bot 開始 polling（AGENT_ENV_MAP 列出多少就多少）。

        TG-STABILITY-1 §4: 每個 bot 的初始啟動各自包 try/except Exception —
        單一 bot 失敗不得中止其他 bot, 且 start() 本身不得因 Telegram 不可用
        而拋出（通道層崩潰一律在通道層吸收自癒, 嚴禁外拋至主事件迴圈）。
        """
        self._on_message = on_message
        self._closing = False
        failed: list[str] = []
        for agent_id, token in self._tokens.items():
            app = None
            try:
                app = self._build_app(agent_id, token)
                self._apps[agent_id] = app

                await app.initialize()
                await app.start()
                # 方案 C (2026-09-08): 409 Conflict 重試限制 — error_callback 檢測
                # Conflict 計數, ≥3 次 fail-closed 停止該 bot polling, 避免 ptb
                # network_retry_loop (max_retries=-1 無限重試) 重試風暴打崩 IOCP
                await app.updater.start_polling(
                    error_callback=self._make_error_callback(agent_id)
                )
                self._healthy_since[agent_id] = self._now()
                logger.info(
                    f"[TG:{agent_id}] polling started "
                    f"(token={token[:8]}...)"
                )
            except Exception:
                # 單一 bot 失敗不得中止其他 bot
                logger.exception(
                    f"[TG:{agent_id}] initial start failed — "
                    f"tearing down partial app and supervising for rebuild"
                )
                failed.append(agent_id)
                # D-1 (TG-STABILITY-1-HOTFIX): 失敗路徑必須清場, 不得 continue。
                # (a) 拆掉半成品 app（initialize 已建 HTTPX client）⇒ 杜絕 CLOSE_WAIT;
                # (b) 從 _apps 移除 ⇒ 監督層看到 app is None ⇒ running=False ⇒
                #     走 _rebuild_poller 重建（否則該 bot 永久無聲死亡）。
                if app is not None:
                    await self._teardown_app(agent_id, app)
                    self._apps.pop(agent_id, None)

            # D-1: 任何 bot 都必須有 supervisor —— 初始啟動成功或失敗都一樣。
            self._supervisors[agent_id] = asyncio.create_task(
                self._supervise(agent_id)
            )

        # D-1: 頻道層啟動摘要（唯一可 grep 格式: "[TG] start summary: "）。
        # 消除「0 個 bot 起來卻被下游印成 10 bots polling」的誤導。
        total = len(self._tokens)
        ok = total - len(failed)
        if failed:
            logger.warning(
                "[TG] start summary: %d/%d bots polling (failed: %s)",
                ok, total, ", ".join(failed),
            )
        else:
            logger.info("[TG] start summary: %d/%d bots polling", ok, total)

        # --- INFRA-WATCHDOG-TG-HEALTH: 冷啟動首寫 + 週期協程 ---
        # 1. **立即同步寫一次**（不得等 30 秒）。目的: 杜絕重啟後頭 30 秒 watchdog
        #    讀到「前一進程遺留的舊心跳檔」而觸發偽 CRITICAL。
        self._write_heartbeat()
        # 2. 再啟動週期協程（stop() 內取消, 不得阻塞關機）。
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def _make_error_callback(self, agent_id: str):
        """方案 C (2026-09-08): 409 Conflict 重試限制。

        ptb 的 network_retry_loop 對 polling 用 max_retries=-1 (無限重試) + interval=0
        (無退避)。雙實例或 token 衝突時, 每個 bot 的 get_updates 會無限 409 重試,
        重試風暴 → httpx 連接池過載 → Windows IOCP 損壞 → access violation。
        此 callback 檢測 Conflict(409) 計數, ≥3 次 fail-closed 停止該 bot polling。
        error_callback 必須是同步函數 (ptb 文檔明確), 內部用 create_task 調度 stop。

        TG-STABILITY-1 §2: 409 語意逐字不變（既有測試在守）；新增「任何
        TelegramError 都寫入 _last_errors[agent_id]（含完整 traceback，每 bot
        只留最近 5 筆）」—— 這是「例外透出」的資料來源。
        """
        def _on_error(exc: TelegramError) -> None:
            # TG-STABILITY-1 §2: 記錄所有例外（不只 409），含完整 traceback。
            # 有界: 每 bot 只留最近 MAX_LAST_ERRORS 筆。
            # TG-STALL-DETECTION-1: 與輪詢心跳 wrapper 共用同一機制。
            self._record_last_error(agent_id, exc)

            if not isinstance(exc, Conflict):
                return
            count = self._conflict_counts.get(agent_id, 0) + 1
            self._conflict_counts[agent_id] = count
            if count >= 3:
                logger.error(
                    f"[TG:{agent_id}] 409 Conflict x{count} — fail-closed stopping "
                    f"polling to prevent retry storm (crash root cause 2026-09-08)"
                )
                self._stop_updater_for(agent_id)
                self._conflict_counts[agent_id] = 0
                # TG-STABILITY-1 §3: 讓監督層知道「上一次失敗是 409 fail-closed」
                # ⇒ 重建直接從最大退避值 (60s) 開始, 不從 1s 爬。
                self._conflict_failclosed.add(agent_id)
            else:
                logger.warning(
                    f"[TG:{agent_id}] 409 Conflict x{count}/3 — will stop polling "
                    f"if conflict persists"
                )
        return _on_error

    def _stop_updater_for(self, agent_id: str) -> None:
        """排程當前 app 的 updater.stop()（同步 context 內用, 保持 409 既有語意）。"""
        app = self._apps.get(agent_id)
        if app is not None:
            asyncio.create_task(app.updater.stop())

    async def _teardown_app(self, agent_id: str, app) -> None:
        """徹底拆除舊 app：關閉 HTTPX session, 杜絕 CLOSE_WAIT 洩漏。

        TG-STABILITY-1 §3: 三步各自包 try/except 且可重複呼叫（idempotent）。
        """
        for step, fn in (
            ("updater.stop", lambda: app.updater.stop()),
            ("stop", lambda: app.stop()),
            ("shutdown", lambda: app.shutdown()),
        ):
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    f"[TG:{agent_id}] teardown {step} failed (idempotent, "
                    f"continuing): {e!r}"
                )

    async def _supervise(self, agent_id: str) -> None:
        """每 bot 一個存活監督任務（TG-STABILITY-1 §3 + TG-STALL-DETECTION-1 §2）。

        每 supervise_interval 秒檢查一次:
          - _closing 為真 ⇒ 直接 return, 永不重建。
          - 健康（running 且「最近一次真實成功」仍在 STALL_THRESHOLD_SECONDS 內）
            ⇒ 連續健康 >=HEALTHY_RESET_SECONDS 就把 _rebuild_counts 歸 0。
          - 不健康（running=False 或 停滯）⇒ 進 _rebuild_poller（唯一重建路徑,
            同一連續失敗計數器 / 同一退避 / 同一慢速期語意, 有界非熱迴圈）。
        任何錯誤都不得向外拋出（通道層自癒, 嚴禁外拋至主事件迴圈）。
        """
        while True:
            try:
                await asyncio.sleep(self._supervise_interval)

                if self._closing:
                    logger.debug(f"[TG:{agent_id}] supervisor exiting (closing)")
                    return

                app = self._apps.get(agent_id)
                updater = getattr(app, "updater", None) if app is not None else None
                running = bool(getattr(updater, "running", False))
                # TG-STALL-DETECTION-1 §2: 健康判據 = running 且「最近一次真實成功」
                healthy, seconds_since_success, ever_succeeded = self._poller_health(
                    agent_id, app
                )

                if healthy:
                    # 連續健康累計 ⟶ 歸零重建計數
                    since = self._healthy_since.get(agent_id)
                    if since is None:
                        self._healthy_since[agent_id] = self._now()
                    elif (
                        self._now() - since
                        >= HEALTHY_RESET_SECONDS
                    ):
                        if self._rebuild_counts.get(agent_id):
                            logger.info(
                                f"[TG:{agent_id}] healthy for "
                                f">={HEALTHY_RESET_SECONDS:.0f}s — resetting "
                                f"rebuild counter"
                            )
                        self._rebuild_counts[agent_id] = 0
                    continue

                self._healthy_since[agent_id] = None
                recent = self._last_errors.get(agent_id) or []

                if not running:
                    # --- 輪詢已死（旗標已被清）---
                    last_error = recent[-1] if recent else "<no error recorded>"
                    logger.error(
                        f"[TG:{agent_id}] poller not running "
                        f"(updater.running=False) — rebuilding ... "
                        f"rebuild_count={self._rebuild_counts.get(agent_id, 0)} "
                        f"recent_errors={len(recent)} last_error={last_error}"
                    )
                else:
                    # --- 停滯: 卡住但沒死（running 仍為 True, getUpdates 再也不成功）---
                    # 舊判據（只看 updater.running）完全看不到這個失效模式;
                    # 2026-09-15 事故指紋正是它: CLOSE_WAIT + 零 traceback +
                    # getUpdates 不再成功 + running 仍為 True。
                    logger.error(
                        f"[TG:{agent_id}] poller STALLED: updater.running=True but "
                        f"no successful getUpdates — "
                        f"seconds_since_last_poll_success="
                        f"{seconds_since_success:.1f} "
                        f"(threshold {STALL_THRESHOLD_SECONDS:.0f}s) "
                        f"poll_success_count="
                        f"{self._poll_success_counts.get(agent_id, 0)} "
                        f"poll_failure_count="
                        f"{self._poll_failure_counts.get(agent_id, 0)} "
                        f"last_poll_success="
                        f"{'never' if not ever_succeeded else 'recorded'} "
                        f"rebuild_count={self._rebuild_counts.get(agent_id, 0)} "
                        f"recent_errors={len(recent)} — rebuilding ..."
                    )

                await self._rebuild_poller(agent_id)
            except asyncio.CancelledError:
                # 關機語意: 收到 cancel 絕不觸發重建 ⇒ 直接往外拋
                logger.debug(f"[TG:{agent_id}] supervisor cancelled")
                raise
            except Exception:
                logger.exception(
                    f"[TG:{agent_id}] supervisor iteration error (absorbed)"
                )

    def _rebuild_backoff(self, agent_id: str) -> float:
        """指數退避: 1,2,4,8,16,32,60,60…

        409 fail-closed 觸發的重建 ⇒ 直接從最大退避值 (60s) 開始。
        """
        if agent_id in self._conflict_failclosed:
            return float(REBUILD_BACKOFF_SECONDS[-1])
        idx = min(
            self._rebuild_counts.get(agent_id, 0),
            len(REBUILD_BACKOFF_SECONDS) - 1,
        )
        return float(REBUILD_BACKOFF_SECONDS[idx])

    async def _rebuild_poller(self, agent_id: str) -> None:
        """重建單一 bot 的 polling。任何錯誤都不得向外拋出。"""
        lock = self._locks.setdefault(agent_id, asyncio.Lock())
        try:
            async with lock:
                # 鎖內再確認: 可能在等鎖期間被 stop() 關掉, 或已被其他路徑救回
                if self._closing:
                    return
                app = self._apps.get(agent_id)
                # TG-STALL-DETECTION-1 §2: 鎖內複查必須用同一個健康判據 ——
                # 只看 updater.running 會讓「卡住但沒死」的重建在鎖內被自己擋掉
                # （running 仍為 True ⇒ 直接 return ⇒ 永遠重建不了）。
                if app is not None and self._poller_health(agent_id, app)[0]:
                    return

                # 1. 先徹底拆除舊的（關閉舊 HTTPX session ⇒ 杜絕 CLOSE_WAIT 洩漏）
                if app is not None:
                    await self._teardown_app(agent_id, app)

                # 2. D-3 (TG-STABILITY-1-HOTFIX): 連續重建達門檻後 **不再永久放棄**。
                #    舊行為（logger.critical + _stop_supervisor）會讓該 bot 一直躺到
                #    下次人工重啟, 主觀體驗等同「無聲死亡」⇒ 改為進入慢速續試期:
                #    每次重試前記 CRITICAL（持續可見）, 間隔固定 SLOW_RETRY_SECONDS。
                #    supervisor 永續存活; 外部恢復 ⇒ 重建成功 ⇒ poller 跑起來 ⇒
                #    既有 HEALTHY_RESET_SECONDS 健康歸零邏輯把 count 歸 0 ⇒ 自動
                #    回到快速退避期。
                count = self._rebuild_counts.get(agent_id, 0)
                slow_mode = count >= MAX_CONSECUTIVE_REBUILDS
                if slow_mode:
                    logger.critical(
                        f"[TG:{agent_id}] {count} consecutive rebuilds without "
                        f"{HEALTHY_RESET_SECONDS:.0f}s healthy window — slow retry "
                        f"mode: retrying every {SLOW_RETRY_SECONDS:.0f}s "
                        f"(never gives up; supervisor stays alive)"
                    )

                # 3. 快速期: 指數退避（上限 60s）; 409 fail-closed ⇒ 直接 60s。
                #    慢速期: 固定 SLOW_RETRY_SECONDS。
                delay = (
                    SLOW_RETRY_SECONDS
                    if slow_mode
                    else self._rebuild_backoff(agent_id)
                )
                self._rebuild_counts[agent_id] = count + 1
                logger.warning(
                    f"[TG:{agent_id}] rebuilding poller in {delay:.0f}s "
                    f"(attempt {count + 1}/{MAX_CONSECUTIVE_REBUILDS})"
                )
                await asyncio.sleep(delay)

                # 再次確認（退避期間可能被關機）
                if self._closing:
                    return

                # 4. 走單一建構路徑重建
                token = self._tokens.get(agent_id, "")
                new_app = None
                try:
                    new_app = self._build_app(agent_id, token)
                    await new_app.initialize()
                    await new_app.start()
                    await new_app.updater.start_polling(
                        error_callback=self._make_error_callback(agent_id)
                    )
                except asyncio.CancelledError:
                    # D-2: 關機語意不變（重拋）, 但半成品 app 必須先拆掉
                    if new_app is not None:
                        await self._teardown_app(agent_id, new_app)
                    raise
                except Exception:
                    logger.exception(
                        f"[TG:{agent_id}] rebuild attempt "
                        f"{count + 1}/{MAX_CONSECUTIVE_REBUILDS} failed"
                    )
                    # D-2: 失敗的 new_app 從未寫入 _apps ⇒ stop() 永遠清不到它。
                    # 這裡必須主動拆（initialize/start 可能已建 keep-alive 連線）
                    # ⇒ 否則每次失敗都遺棄一個孤兒 app（session / CLOSE_WAIT 洩漏）。
                    if new_app is not None:
                        await self._teardown_app(agent_id, new_app)
                    return

                self._apps[agent_id] = new_app
                self._conflict_failclosed.discard(agent_id)
                self._healthy_since[agent_id] = self._now()
                logger.info(
                    f"[TG:{agent_id}] poller rebuilt OK "
                    f"(attempt {count + 1}/{MAX_CONSECUTIVE_REBUILDS})"
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 任何錯誤都不得向外拋出（通道層自癒）
            logger.exception(f"[TG:{agent_id}] rebuild error (absorbed)")

    async def send(self, agent_id: str, text: str,
                   user_id: "int | str") -> bool:
        """送訊息給指定 user（user_id = Telegram user id, int）。"""
        app = self._apps.get(agent_id)
        if not app:
            logger.warning(f"[TG] No app for agent [{agent_id}]")
            return False
        try:
            await app.bot.send_message(chat_id=int(user_id), text=text)
            logger.info(
                f"[TG:{agent_id}] sent to {user_id}: {text[:50]!r}"
            )
            _append_session_turn(agent_id, "assistant", text, user_id)  # VC-2.5
            return True
        except Exception as e:
            logger.error(f"[TG:{agent_id}] send error: {e}")
            return False

    async def send_voice(self, agent_id: str, audio_path: str,
                         user_id: "int | str") -> bool:
        """送語音訊息給指定 user（給 mp3 檔路徑，會用 bot.send_voice 上傳）。

        Phase 5+ (2026-07-15 Bry 拍板): 配合 TTSService 寫完 mp3 後的
        AGENT_AUDIO_READY 事件，把日文 TTS 結果也推到 Telegram 給 user 聽。
        用 ptb 的 send_voice 走 voice bubble（不是普通 audio 附件），
        手機端可以直接 inline 播。

        Args:
            agent_id: "yua" | "mahiru" | ... (短碼，跟 self._apps 對齊)
            audio_path: 本地 mp3 檔絕對路徑（從 AGENT_AUDIO_READY.payload.audio_path）
            user_id: Telegram user id
        """
        from pathlib import Path
        app = self._apps.get(agent_id)
        if not app:
            logger.warning(f"[TG] No app for agent [{agent_id}] (send_voice)")
            return False
        p = Path(audio_path)
        if not p.exists():
            logger.warning(
                f"[TG:{agent_id}] audio file not found: {audio_path}"
            )
            return False
        try:
            # ptb v13+ send_voice 接受 file path / file-like / InputFile
            # 用 open() 確保 with 區塊內 file handle 還在
            with open(p, "rb") as f:
                await app.bot.send_voice(
                    chat_id=int(user_id),
                    voice=f,
                    filename=p.name,
                )
            logger.info(
                f"[TG:{agent_id}] voice sent to {user_id}: "
                f"{p.name} ({p.stat().st_size} bytes)"
            )
            return True
        except Exception as e:
            logger.error(f"[TG:{agent_id}] send_voice error: {e}")
            return False

    async def _keep_typing(self, agent_id: str, user_id: int) -> None:
        """Phase 5d+：每 4s 重送 typing，直到被 cancel。

        Telegram 的 typing indicator 5s 過期，所以 4s 重送比較安全。
        LLM 慢的時候（昨天看到 28s spike）用戶會一直看到「Yua 正在輸入」。
        """
        app = self._apps.get(agent_id)
        if not app:
            return
        try:
            while True:
                try:
                    await app.bot.send_chat_action(
                        chat_id=int(user_id),
                        action="typing",
                    )
                except Exception as e:
                    logger.debug(f"[TG:{agent_id}] typing send error: {e}")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            logger.debug(f"[TG:{agent_id}] typing task cancelled")
            raise

    async def stop(self) -> None:
        """停止所有 bot（TG-STABILITY-1 §5）。

        順序: 設 _closing ⇒ 取消所有 supervisor task ⇒ 再停 updater/app。
        idempotent, 且不得吞掉 CancelledError。
        """
        # 1. 關閉語意: 先關旗標, 監督層看到就不再重建（關機時無限重建 = 關不掉服務）
        self._closing = True

        # 2. 取消所有監督 task（CancelledError 必須往外拋, 不得吞掉）
        tasks = list(self._supervisors.values())
        self._supervisors.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[TG] supervisor join error: {e}")

        # 3. INFRA-WATCHDOG-TG-HEALTH: 取消心跳週期協程（不得阻塞關機）。
        #    先清欄位再 await ⇒ 重複呼叫 stop() 仍 idempotent。
        heartbeat_task = self._heartbeat_task
        self._heartbeat_task = None
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[TG] heartbeat join error: {e}")

        # 4. 再停 updater / app（idempotent 拆除, 關閉 HTTPX session）
        apps = list(self._apps.items())
        self._apps.clear()
        for agent_id, app in apps:
            await self._teardown_app(agent_id, app)
            logger.info(f"[TG:{agent_id}] stopped")

    def snapshot_health(self) -> dict:
        """per-agent 輪詢健康快照。

        TG-STALL-DETECTION-1 §3 既有欄位（**語意逐字不變**）:
          - `last_poll_success`: 最近一次 getUpdates 成功的 monotonic 秒
            （None = 當前 app 從未成功過）
          - `poll_success_count` / `poll_failure_count`: 跨重建累計次數
          - `running`: ptb updater 旗標（**不可單獨當健康判據**）
          - `rebuild_count`: 連續重建次數（既有計數器）

        INFRA-WATCHDOG-TG-HEALTH 擴充欄位（純新增, 無既有消費者）:
          - `last_success_epoch`: 最近一次成功的 **epoch** 秒（None = 本進程從未成功）
          - `last_success_ts`: 導出值（epoch, **永不為 None**）—— 有成功用它,
            否則用進程啟動 epoch（見 `_derive_last_success_ts()`）
          - `process_start_epoch`: 本 adapter 建構時的 epoch
          - `age_s`: 距 `last_success_ts` 幾秒
          - `status`: `"ok"` / `"stale"`（門檻 `HEARTBEAT_STALE_SECONDS`）

        心跳檔落盤（`data/heartbeats/telegram_channel.json`）由 `_write_heartbeat()`
        負責; 本方法只回傳記憶體內狀態。
        """
        agent_ids = sorted(set(self._tokens) | set(self._apps))
        now_epoch = float(self._wall_clock())
        snapshot: dict = {}
        for agent_id in agent_ids:
            app = self._apps.get(agent_id)
            updater = getattr(app, "updater", None) if app is not None else None
            last_success_ts = self._derive_last_success_ts(agent_id)
            age_s = now_epoch - last_success_ts
            snapshot[agent_id] = {
                "last_poll_success": self._last_poll_success.get(agent_id),
                "poll_success_count": self._poll_success_counts.get(agent_id, 0),
                "poll_failure_count": self._poll_failure_counts.get(agent_id, 0),
                "running": bool(getattr(updater, "running", False)),
                "rebuild_count": self._rebuild_counts.get(agent_id, 0),
                "last_success_epoch": self._last_success_epoch.get(agent_id),
                "last_success_ts": last_success_ts,
                "process_start_epoch": self._process_start_epoch,
                "age_s": age_s,
                "status": (
                    "ok" if age_s <= HEARTBEAT_STALE_SECONDS else "stale"
                ),
            }
        return snapshot
