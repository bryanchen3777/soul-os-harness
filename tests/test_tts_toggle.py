"""
tests/test_tts_toggle.py
Soul OS — TTS 全域開關單元測試（Bry 派工 2026-08-15）

驗證：
  1. 預設 True（backward compat：原本 proxy.py 硬寫 tts_enabled=True）
  2. set_tts_enabled(False) → is_tts_enabled() 回 False
  3. set_tts_enabled(True) → is_tts_enabled() 回 True
  4. 狀態持久化到 data/state/tts_toggle.json（重啟不丟失）

用 SOUL_OS_DATA_DIR 指向 temp dir 隔離，不污染 production state。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _reset_toggle_module():
    """重設 data_root cache + 重新 import tts_toggle（讓它讀新的 data_root）。"""
    from src.paths import reset_data_root
    reset_data_root()
    # 重新載入 tts_toggle 讓 _STATE_FILE 指向新的 data_root
    import importlib
    import src.llm.tts_toggle as tt
    importlib.reload(tt)
    return tt


def test_tts_toggle_default_and_roundtrip():
    tmp = tempfile.mkdtemp(prefix="soul_os_tts_toggle_")
    os.environ["SOUL_OS_DATA_DIR"] = tmp
    try:
        tt = _reset_toggle_module()

        # 1. 預設 True（檔案不存在）
        assert tt.is_tts_enabled() is True, "預設應為 True"

        # 2. 關閉
        assert tt.set_tts_enabled(False) is True
        assert tt.is_tts_enabled() is False, "set False 後應回 False"

        # 3. 重新載入模組（模擬 server 重啟）→ 狀態應持久化
        tt2 = _reset_toggle_module()
        assert tt2.is_tts_enabled() is False, "重啟後應保持 False（持久化）"

        # 4. 開啟
        assert tt2.set_tts_enabled(True) is True
        assert tt2.is_tts_enabled() is True, "set True 後應回 True"

        # 5. 檔案確實存在
        state_file = Path(tmp) / "state" / "tts_toggle.json"
        assert state_file.is_file(), "tts_toggle.json 應被寫入"
    finally:
        os.environ.pop("SOUL_OS_DATA_DIR", None)
        _reset_toggle_module()


if __name__ == "__main__":
    test_tts_toggle_default_and_roundtrip()
    print("test_tts_toggle PASS")
