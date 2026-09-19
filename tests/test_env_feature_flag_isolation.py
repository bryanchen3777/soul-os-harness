from __future__ import annotations

import os
from pathlib import Path

from configs.loader import load_config
from src.soul.life_thread_bootstrap import BOOTSTRAP_ENABLED_ENV, bootstrap_enabled


def test_bootstrap_flag_isolated_from_dotenv_reload(tmp_path: Path):
    """Empty env blocks dotenv override=False from rehydrating a production flag."""
    assert os.environ.get(BOOTSTRAP_ENABLED_ENV) == ""
    env_file = tmp_path / "production-like.env"
    env_file.write_text(f"{BOOTSTRAP_ENABLED_ENV}=1\n", encoding="utf-8")
    load_config(env_path=str(env_file))
    assert os.environ.get(BOOTSTRAP_ENABLED_ENV) == ""
    assert bootstrap_enabled() is False


def test_bootstrap_flag_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setenv(BOOTSTRAP_ENABLED_ENV, "1")
    assert bootstrap_enabled() is True
