"""
src/io/channels/bryan_state.py
Soul OS — Bry 活躍信號統一讀寫 (Proactive DM 三件修復 #2, Bry 拍板 2026-08-29)

單一事實來源: data/state/bryan_last_seen.json
- router.py (TG inbound) 寫  → touch_bryan_last_seen
- gateway.py (web inbound) 寫 → touch_bryan_last_seen
- scheduler.py (可送達檢查) 讀 → read_bryan_last_seen
- router.py (M0.5 throttle 兜底) 讀 → read_bryan_last_seen

背景 (Proactive DM 審計 2026-08-29):
  兩個「Bry 活躍」信號打架 — scheduler 用 relationships.json (跨渠道),
  router 用 bryan_last_seen (僅 TG)。Bry 只用 web 時, proactive_dm 永遠
  無法送達 TG, 但持續燒 LLM。修法: 統一信號源 = bryan_last_seen.json,
  web inbound 也更新它。

凍結契約: 0 變動 (不碰 InnerLifeEvent / TriggerEnvelope / Agency 4 stages /
4 handlers / SAGE 寫入邏輯)。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.paths import data_root

logger = logging.getLogger("soul_os.channels.bryan_state")

# M0.5 (Bry 派工 2026-08-02 10:35): 「Bry 沒回應 N 小時」proactive_dm throttle
# 4h 是 Bry 派工時的初始值, 之後觀察期可調 (太小會誤殺, 太大會堆積)
# 單一事實來源: router.py 的 M0.5 常數改從這裡 import, 避免兩處漂移
PROACTIVE_DM_BRYAN_INACTIVE_HOURS = 4.0


def _bryan_last_seen_file() -> Path:
    """動態求值 (每次呼叫), 讓測試可透過 SOUL_OS_DATA_DIR + reset_data_root() 隔離。

    不用模組級常量的原因: data_root() 是 subprocess-lifetime 快取,
    模組級常量在 import 時就固定, 測試改 env 後會讀到舊路徑。
    """
    return data_root() / "state" / "bryan_last_seen.json"


def touch_bryan_last_seen(full_agent_id: str, text: str) -> bool:
    """任何 channel inbound 收到 Bry 訊息都更新 Bry 最後看見時間。

    統一信號源 (Proactive DM 三件修復 #2): TG (router) + web (gateway)
    都寫同一個檔案, scheduler 的可送達檢查跟 router 的 M0.5 throttle
    都讀同一個檔案。

    Args:
        full_agent_id: 完整 agent_id (如 "agent_yua")
        text: 訊息 preview (存前 50 字)

    Returns:
        True 寫檔成功, False 寫檔失敗 (fail-open: 呼叫方自行決定)
    """
    path = _bryan_last_seen_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    payload = {
        "last_recv_ts": now_utc.isoformat(),
        "last_recv_agent": full_agent_id,
        "last_recv_preview": text[:50],
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        logger.warning(f"[bryan_state] 寫 {path.name} 失敗: {e}")
        return False


def read_bryan_last_seen() -> datetime | None:
    """讀 Bry 最後看見時間 (UTC aware datetime)。

    Returns:
        UTC aware datetime, 或 None (沒檔案 / 沒 last_recv_ts / 解析失敗)。
        None 代表「冷啟動」— 呼叫方應視為不 throttle (跟 router M0.5 一致)。
    """
    path = _bryan_last_seen_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ts = data.get("last_recv_ts")
        if ts:
            # naive 字串補 UTC (跟 relationships.json 對齊)
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as e:
        logger.warning(f"[bryan_state] 讀 {path.name} 失敗: {e}")
    return None
