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


# ─────────────────────────────────────────────────────────────────────
# TIME-PERCEPTION-1 — Temporal Representation（Owner 2026-10-03 裁定 A）
# ─────────────────────────────────────────────────────────────────────
# 裁定逐字：「精確時間保留，但降格」——
#   第一行 = Soul 感知到的 lived time（粗顆粒）
#   第二行 = grounding / safety anchor（精確），避免「4:36 卻說晚安」這類
#             factual mismatch；2026-08-04 修法 8 因此**不撤銷**。
# 硬限制：LLM 的語意生成主要依賴 coarse time；精確時間**不得**成為主要敘事錨點。
# 🔴 本段**只做輸入側措辭**。Soul 自主睡眠 / temporal drift / preference learning /
#    memory-pressure sleep / dream / persistent observer / 新 scheduler
#    全部**未授權**（Owner 2026-10-03 明確列為不授權）。
# 🔴 複用既有 `_WEEKDAY_CN` 與 `_period_label`（0 第四套口徑）。

# 12 小時制中文數字（粗顆粒用；僅 1-12）
_COARSE_NUM_CN = (
    "", "一", "兩", "三", "四", "五", "六",
    "七", "八", "九", "十", "十一", "十二",
)


def _coarse_hour12(hour: int) -> int:
    """24h → 12h 制（0 → 12）。"""
    return 12 if hour == 0 else (hour - 12 if hour > 12 else hour)


def coarse_time_phrase(dt: datetime) -> str:
    """粗顆粒時間短語（TIME-PERCEPTION-1）。

    Bry《時間感知》定義：「大概就夠，不需要 14:23:17」；「粗顆粒寫進去，
    **精確數字反而不要給**」。故本函式的輸出**不得**包含 `HH:MM`、秒、
    或 `EDT`/`EST` 等機器時間戳。

    **仍由 deterministic clock 推導**（不另設時鐘）：以 `dt` 為唯一真值，
    整點附近刻意用「剛過／差不多／快」製造不精確感——**那是設計，不是 bug**。

    參考輸出：09:05→「上午，剛過九點」、14:22→「下午，約兩點半」、
    20:40→「晚上，差不多八點半」、23:50→「深夜，快午夜了」。
    """
    h, m = dt.hour, dt.minute
    period = _period_label(h)          # 複用既有口徑（0 第四套，23 時回「凌晨」)

    # 深夜尾段：兩個詞本身就是質地，仍帶既有時段詞以維持 0 第四套口徑
    if h == 23:
        tail = "快午夜了" if m >= 30 else "快十一點了"
        return f"{period}，{tail}"
    # 凌晨：00 點在中文口語是「十二點」，不可輸出空字串
    if h < 4:
        h12 = 12 if h == 0 else h
        name = _COARSE_NUM_CN[h12]
        if m < 15:
            nxt = _COARSE_NUM_CN[1 if h12 == 12 else h12 + 1]
            return f"{period}，快{nxt}點了"
        return f"{period}，{name}點多"

    h12 = _coarse_hour12(h)
    name = _COARSE_NUM_CN[h12]
    if m < 20:
        return f"{period}，剛過{name}點"
    if m < 45:
        return f"{period}，約{name}點半"
    nxt = _COARSE_NUM_CN[1 if h12 == 12 else h12 + 1]
    return f"{period}，差不多{name}點半，快{nxt}點了"


def day_position(dt: datetime) -> str:
    """一天中的位置（Bry 的「自我定位」——回答「我在一天的哪裡」）。

    `_period_label` 回答「哪個時段」，本函式回答「這一段走到哪了」。
    兩者刻意區分：前者是時段標籤，後者是弧線進度。
    """
    h = dt.hour
    if 5 <= h < 11:
        return "今天才剛開始"
    if 11 <= h < 14:
        return "今天已經過了一段"
    if 14 <= h < 17:
        return "今天已經過了一大段"
    if 17 <= h < 20:
        return "今天快走完了"
    if 20 <= h < 23:
        return "今天快收尾了"
    if h >= 23 or h < 5:
        return "今天快走完了，屬於深夜了"
    return "今天正在走"


# 🔴 索引口徑差異（TIME-PERCEPTION-1，2026-10-03 修正）：
#    `_WEEKDAY_CN` 的順序對應 `strftime("%w")`（**週日=0**），
#    而 `datetime.weekday()` 是 **週一=0**。直接用 `weekday()` 索引會整組錯一格
#    （2026-10-03 實測誤判為「週五」，正確為「週六」；Bry 2026-10-03 親自抓到）。
#    正確寫法：用 `%w`（週日=0）索引，**或** weekday()+1 映射（週一=1 … 週日=7）。
#    此處採 `%w`，與既有 `_format_event_timestamp` 的用法一致。
def _weekday_cn(dt: datetime) -> str:
    """回傳中文星期（複用 `_WEEKDAY_CN`，0 第四套口徑）。

    ⚠️ **不得**寫成 `_WEEKDAY_CN[dt.weekday()]` —— 索引起點不同，會整組錯一格。
    """
    return _WEEKDAY_CN[int(dt.strftime("%w"))]


def coarse_now_line(dt: Optional[datetime] = None) -> str:
    """TIME-PERCEPTION-1 第一行：`週六，下午，約兩點半。今天已經過了一大段。`

    星期一律由 `_weekday_cn()` 推導（**deterministic clock 為唯一真值**）；
    不得硬編星期字串 —— 那會在換日後變成 regression。
    """
    dt = dt or now_local()
    return f"{_weekday_cn(dt)}。{coarse_time_phrase(dt)}。{day_position(dt)}。"


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
