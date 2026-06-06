"""
configs/loader.py
Soul OS — Phase 2.2: 統一配置載入器

讀取順序（後蓋前）：
  1. configs/default.yaml
  2. .env（dotenv load）
  3. 系統環境變數（dotenv 不覆蓋既有 env，load_dotenv 預設行為）

提供 factory 函式：
  - create_llm_backend(cfg)         → OpenAIBackend / ClaudeBackend
  - create_llm_proxy(cfg, bus)      → LLMProxy（含 retry / model 設定）
  - create_heartbeat(cfg, bus)      → HeartbeatEngine

執行範例：
  from configs.loader import load_config, create_llm_proxy, create_heartbeat
  cfg = load_config()
  bus = SoulEventBus()
  llm = create_llm_proxy(cfg, bus)
  llm.register()
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

logger = logging.getLogger("soul_os.config")


# ── 讀取 ──────────────────────────────────────────────

def load_config(
    config_path: str | None = None,
    env_path: str | None = None,
) -> dict[str, Any]:
    """
    載入 yaml + .env，env 覆蓋 yaml 對應欄位。
    路徑預設為相對於本檔案（configs/loader.py）的位置，
    不受 CWD 影響。
    """
    _base = Path(__file__).parent  # configs/

    if config_path is None:
        config_path = str(_base / "default.yaml")
    yaml_path = Path(config_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(yaml_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # dotenv：若 .env 存在則 load；不存在不報錯
    if env_path is None:
        env_path = str(_base.parent / ".env")  # repo 根目錄
    if Path(env_path).exists():
        load_dotenv(env_path, override=False)

    _inject_env(cfg)
    return cfg


def _inject_env(cfg: dict) -> None:
    """把環境變數注入對應 config 欄位（env 優先於 yaml）。"""
    env_map = {
        # 巢狀寫入：把 path 解析成 list of keys
        "ANTHROPIC_API_KEY":   ["llm", "claude", "api_key"],
        "MINIMAX_API_KEY":     ["llm", "minimax", "api_key"],
        "OPENAI_API_KEY":      ["llm", "openai", "api_key"],
        "OPENAI_BASE_URL":     ["llm", "openai", "base_url"],
        "LLM_PROVIDER":        ["llm", "provider"],
        "LLM_MODEL":           ["llm", "model"],
        "ANTHROPIC_MODEL":     ["llm", "claude_model"],
        "HEARTBEAT_INTERVAL":  ["heartbeat", "tick_interval_seconds"],
        "SOUL_LOG_LEVEL":      ["logging", "level"],
    }
    for env_key, path_keys in env_map.items():
        val = os.getenv(env_key)
        if val is None or val == "":
            continue
        # 沿路建立巢狀 dict
        node = cfg
        for k in path_keys[:-1]:
            node = node.setdefault(k, {})
        # 嘗試 int 轉型（HEARTBEAT_INTERVAL 是數字）
        try:
            node[path_keys[-1]] = int(val)
        except (ValueError, TypeError):
            node[path_keys[-1]] = val


# ── Factories ──────────────────────────────────────────

def create_llm_backend(cfg: dict):
    """根據 cfg.llm.provider 建立對應 Backend。"""
    from src.llm.proxy import ClaudeBackend, OpenAIBackend

    provider = cfg.get("llm", {}).get("provider", "claude").lower()
    llm_cfg = cfg.get("llm", {})

    if provider == "minimax":
        # MiniMax 提供 Anthropic-compatible endpoint
        # 直接複用 ClaudeBackend，只換 BASE_URL
        minimax_cfg = llm_cfg.get("minimax", {})
        api_key = (
            minimax_cfg.get("api_key")
            or os.getenv("MINIMAX_API_KEY", "")
        )
        if not api_key:
            raise ValueError(
                "MINIMAX_API_KEY is required when LLM_PROVIDER=minimax. "
                "Set it in .env or env."
            )
        backend = ClaudeBackend(api_key=api_key)
        # 覆寫 class-level BASE_URL（Python instance attr 優先於 class attr）
        backend.BASE_URL = "https://api.minimax.io/anthropic/v1/messages"
        return backend

    if provider == "openai":
        openai_cfg = llm_cfg.get("openai", {})
        api_key = openai_cfg.get("api_key") or os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai. "
                "Set it in .env or env."
            )
        return OpenAIBackend(
            api_key=api_key,
            base_url=openai_cfg.get("base_url"),
        )

    if provider == "mock":
        # Mock 模式：回傳 None，呼叫端應用 MockLLMBackend 替代
        return None

    # 預設 / claude
    claude_cfg = llm_cfg.get("claude", {})
    api_key = claude_cfg.get("api_key") or os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required when LLM_PROVIDER=claude. "
            "Set it in .env or env."
        )
    return ClaudeBackend(api_key=api_key)


def create_llm_proxy(cfg: dict, bus):
    """建立 LLMProxy，注入完整設定。"""
    from src.llm.proxy import LLMProxy

    backend = create_llm_backend(cfg)
    llm_cfg = cfg.get("llm", {})

    # model 解析：unified "model" 欄位 > provider-specific default
    model = llm_cfg.get("model")
    if not model:
        provider = llm_cfg.get("provider", "claude").lower()
        if provider == "openai":
            model = llm_cfg.get("openai_model", "gpt-4o-mini")
        else:
            model = llm_cfg.get("claude_model", "claude-haiku-4-5-20251001")

    return LLMProxy(
        bus=bus,
        backend=backend,
        model=model,
        max_tokens=llm_cfg.get("max_tokens", 300),
        temperature=llm_cfg.get("temperature", 0.85),
        max_retries=llm_cfg.get("max_retries", 3),
        max_history_turns=llm_cfg.get("max_history_turns", 10),
    )


def create_agents(cfg: dict, bus, speaker_token_bus=None) -> list:
    """
    從 config 動態實例化所有 enabled Agent。
    回傳已 register() 的 Agent 列表。
    """
    from src.agent.registry import get_agent_class

    agents = []
    for agent_cfg in cfg.get("agents", []):
        if not agent_cfg.get("enabled", True):
            continue
        agent_id = agent_cfg["id"]
        class_name = agent_cfg["class"]
        intimacy = agent_cfg.get("intimacy_level", 50)

        cls = get_agent_class(class_name)
        agent = cls(agent_id, bus, speaker_token_bus=speaker_token_bus)
        agent.state.intimacy_level = intimacy
        agent.register()
        agents.append(agent)

        logger.info(f"[Loader] Agent 載入：{agent_id} ({class_name}) intimacy={intimacy}")

    return agents


def create_heartbeat(cfg: dict, bus, agent_ids: list[str] | None = None):
    """建立 HeartbeatEngine，注入 tick_interval 和 agent_ids。"""
    from src.heartbeat.engine import HeartbeatEngine

    hb_cfg = cfg.get("heartbeat", {})
    return HeartbeatEngine(
        bus=bus,
        tick_interval_seconds=hb_cfg.get("tick_interval_seconds", 60),
        data_dir="data/agents",
        agent_ids=agent_ids or [],
    )


# ── CLI：方便直接看 config 結果 ─────────────────────────

if __name__ == "__main__":
    import json
    cfg = load_config()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
