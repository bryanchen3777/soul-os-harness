"""
src/llm/tts_toggle.py
Soul OS — TTS 全域開關（Bry 派工 2026-08-15）

讓 Bry 在 Telegram 用 /tts on / /tts off 開關是否使用 TTS。

設計：
  - 全域開關（不是 per-agent）：FishTTSHandler 是單一 handler 訂閱所有
    AGENT_SPEAK，開關影響全部 10 個角色。
  - 持久化到 data/state/tts_toggle.json（跟 last_tg_user.json 同目錄、
    同設計，server 重啟不丟失）。
  - 預設 True（backward compat：原本 proxy.py 硬寫 tts_enabled=True）。

讀取端：src/llm/proxy.py 發 AGENT_SPEAK 時讀 is_tts_enabled()
寫入端：src/io/channels/telegram.py 的 /tts command handler 呼叫 set_tts_enabled()

每次讀取都直接讀檔（不 cache），這樣 Bry 在 TG 切換後立刻生效，
不用重啟 server。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.paths import data_root

logger = logging.getLogger("soul_os.tts_toggle")

_STATE_DIR = data_root() / "state"
_STATE_FILE = _STATE_DIR / "tts_toggle.json"

# 預設值：原本 proxy.py 硬寫 tts_enabled=True，開關預設開啟保持 backward compat
_DEFAULT_ENABLED = True


def is_tts_enabled() -> bool:
    """讀取全域 TTS 開關狀態。檔案不存在 → 預設 True。"""
    if not _STATE_FILE.is_file():
        return _DEFAULT_ENABLED
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return bool(data.get("enabled", _DEFAULT_ENABLED))
    except Exception as e:
        logger.warning(f"[TTS toggle] 讀 {_STATE_FILE.name} 失敗: {e}")
        return _DEFAULT_ENABLED


def set_tts_enabled(enabled: bool) -> bool:
    """寫入全域 TTS 開關狀態。回傳是否寫入成功（不 raise）。"""
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": bool(enabled),
            "set_at": datetime.now(timezone.utc).isoformat(),
        }
        _STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[TTS toggle] set enabled={bool(enabled)}")
        return True
    except Exception as e:
        logger.warning(f"[TTS toggle] 寫 {_STATE_FILE.name} 失敗: {e}")
        return False
