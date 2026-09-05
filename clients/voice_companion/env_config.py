"""
env_config.py — VC-1 客戶端執行期配置解析（env 覆寫 + .env 載入）。

金鑰安全規則：
- API 金鑰只從環境變數讀取，絕不寫入 config.json / git / 日誌。
- config.json 只放非機密預設值（如茜的音色 reference_id）。

解析順序：os.environ > config.json 預設值。
- 啟動時若 clients/voice_companion/.env 存在則載入到 os.environ（不覆蓋既有的環境變數）。
- 環境變數覆寫鍵（對應 config 路徑）：
  FISH_API_KEY  → fish_audio.api_key
  FISH_VOICE_ID → fish_audio.voice_id
  LLM_BASE_URL  → llm.endpoint
  LLM_API_KEY   → llm.api_key
  LLM_MODEL     → llm.model
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENV_FILE_NAME = ".env"

# (環境變數名, config 區段, config 鍵)
ENV_OVERRIDE_MAP: list[tuple[str, str, str]] = [
    ("FISH_API_KEY", "fish_audio", "api_key"),
    ("FISH_VOICE_ID", "fish_audio", "voice_id"),
    ("FISH_MODEL", "fish_audio", "model"),
    ("LLM_BASE_URL", "llm", "endpoint"),
    ("LLM_API_KEY", "llm", "api_key"),
    ("LLM_MODEL", "llm", "model"),
]


def load_dotenv(path: Optional[str] = None) -> bool:
    """載入 .env（預設為本模組旁的 .env）到 os.environ。

    - 檔案不存在 → 回傳 False（不算錯誤）。
    - 已存在的環境變數一律不覆蓋（os.environ 優先）。
    - 支援整行註解（# 開頭）與單/雙引號包覆的值。
    """
    p = Path(path) if path else Path(__file__).resolve().parent / ENV_FILE_NAME
    if not p.is_file():
        return False
    loaded = 0
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return True


def apply_env_overrides(config: dict) -> dict:
    """以 os.environ 中「存在且非空」的變數覆寫 config 對應鍵（原地修改並回傳）。"""
    for env_name, section, key in ENV_OVERRIDE_MAP:
        value = os.environ.get(env_name)
        if value:
            config.setdefault(section, {})[key] = value
    return config


def resolve_config(config: dict, env_file: Optional[str] = None) -> dict:
    """完整解析：載入 .env（不覆蓋既有 env）→ os.environ 覆寫 config → 回傳最終 config。"""
    load_dotenv(env_file)
    return apply_env_overrides(config)