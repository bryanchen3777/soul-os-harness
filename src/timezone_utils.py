"""
src/timezone_utils.py
Soul OS — 共用時區常數 + helper (Bry 拍板 2026-08-03 18:21)

設計動機 (Bry 派工單):
- 2026-08-03 18:21 Bry 拍板: 改用 America/New_York (Bry 人在紐約 EDT/EST)
- 之前 M0.4 (commit 932d552) 跟 f9105f1 假設 "Windows 沒 zoneinfo 可用, 用 timezone(timedelta)"
  是錯的: Python 3.9+ 內建 zoneinfo, Windows 也能用, 而且自動處理 EDT/EST 切換
- 之前 scheduler.py 跟 proxy.py 各自定義 ASIA_TZ (重複 2 份),
  統一從這裡 import, 改一個地方全改 (Bry 派工單: "別再維護兩份重複定義")
- 4 個檔案都用同一個 LOCAL_TZ:
    1. src/soul/scheduler.py     (11 處 datetime.now(ASIA_TZ))
    2. src/llm/proxy.py          (astimezone + "Asia/Taipei" 字串 + _WEEKDAY_CN)
    3. src/memory/middleware.py  (β2.1 我自己寫的 hours=8 hardcode)
    4. src/temporal/models.py     (PersonaConfig.timezone 預設 Asia/Tokyo, 沒人用但仍修)

優先序 (Bry 派工單 "加一個 config 開關"):
  1. 環境變數 SOULOS_TIMEZONE  (最高優先, 暫時覆寫用)
  2. configs/default.yaml 的 llm.timezone 欄位  (Bry 移地改這個就好)
  3. 預設 fallback: ZoneInfo("America/New_York")  (Bry 拍板 2026-08-03 18:21)

不要做的事:
- 不要 detect OS timezone (會跟 Bry 端不一致, Bry 端 EDT 跟 server Windows EDT 可能本來就對, 但 Bry 派工明確拍 America/New_York)
- 不要保留 ASIA_TZ (跟 Bry 派工精神 "改成" 不符, 不是 "加新常數廢棄舊的")
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("soul_os.timezone_utils")

# 預設 fallback: Bry 拍板 2026-08-03 18:21
# (M0.4 跟 f9105f1 預設 Asia/Taipei 是因為 Bry 當時在台灣,
#  現在 Bry 人在紐約, 預設改成 America/New_York)
DEFAULT_TIMEZONE_NAME = "America/New_York"

# 時段標籤 (跟 proxy.py _format_event_timestamp L57-68 邏輯一致, 移過來共用)
_WEEKDAY_CN = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"]


def _period_label(hour: int) -> str:
    """5-11 早上, 11-13 中午, 13-17 下午, 17-19 傍晚, 19-23 晚上, else 凌晨"""
    if 5 <= hour < 11:
        return "早上"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "凌晨"


def resolve_timezone_name(cfg: Optional[dict] = None) -> str:
    """
    解析時區名稱 (優先序: env > config > default).

    Args:
        cfg: 從 load_config() 拿的 config dict, 可選

    Returns:
        tz_name 例如 "America/New_York", 餵給 ZoneInfo()
    """
    # 1. 環境變數最高優先 (暫時覆寫用)
    env_tz = os.getenv("SOULOS_TIMEZONE")
    if env_tz:
        return env_tz
    # 2. config
    if cfg:
        llm_cfg = cfg.get("llm", {})
        cfg_tz = llm_cfg.get("timezone")
        if cfg_tz:
            return cfg_tz
    # 3. 預設 fallback
    return DEFAULT_TIMEZONE_NAME


def get_local_tz(cfg: Optional[dict] = None) -> ZoneInfo:
    """
    取得本地時區 (ZoneInfo 物件).

    Args:
        cfg: 從 load_config() 拿的 config dict, 可選

    Returns:
        ZoneInfo instance, e.g. ZoneInfo("America/New_York")
    """
    tz_name = resolve_timezone_name(cfg)
    try:
        return ZoneInfo(tz_name)
    except Exception as e:
        logger.warning(
            f"[timezone_utils] ZoneInfo({tz_name!r}) 失敗, "
            f"fallback {DEFAULT_TIMEZONE_NAME}: {e}"
        )
        return ZoneInfo(DEFAULT_TIMEZONE_NAME)


# Bry 派工單 "別再維護兩份重複定義" — 模組層級單一常數.
# 沒傳 cfg 預設 fallback, scheduler / proxy / middleware / models 共用.
LOCAL_TZ: ZoneInfo = get_local_tz()


def now_local(cfg: Optional[dict] = None) -> datetime:
    """
    取得本地時區 aware datetime (跟 datetime.now(LOCAL_TZ) 等價).
    取代 scheduler.py 11 處 datetime.now(ASIA_TZ) 跟 memory/middleware.py hardcode.
    """
    return datetime.now(get_local_tz(cfg))


def format_localized(
    event_ts: Optional[datetime],
    cfg: Optional[dict] = None,
) -> str:
    """
    把 SoulEvent.timestamp (UTC) 轉成本地時區顯示字串, 注入 LLM system prompt.

    跟 proxy.py _format_event_timestamp (L37-71) 邏輯一致,
    但時區從 cfg 動態讀, 字串從 zoneinfo.key 自動輸出 (不寫死 "Asia/Taipei").

    Bry 派工單原話: "proxy.py 的字串 "Asia/Taipei" 改成對應 America/New_York 顯示
    (可以用 zoneinfo 物件自己輸出, 不用再手動寫死字串)"

    Returns:
        字串例如 "2026-08-03 週日 18:21 America/New_York（下午）"
    """
    if event_ts is None:
        return "時間未知"
    # 失敗防護: event_ts 為 naive datetime, 假設 UTC (跟 SoulEvent schema default_factory 一致)
    if event_ts.tzinfo is None:
        event_ts = event_ts.replace(tzinfo=timezone.utc)
    local = event_ts.astimezone(get_local_tz(cfg))
    weekday = _WEEKDAY_CN[local.weekday()]
    period = _period_label(local.hour)
    # zoneinfo 物件自己輸出時區名 (e.g. "America/New_York" or "UTC")
    tz_key = local.tzinfo.key
    return (
        f"{local.strftime('%Y-%m-%d')} {weekday} "
        f"{local.strftime('%H:%M')} {tz_key}（{period}）"
    )
