"""tests/clients/test_vc_model_registry.py — VC 模型白名單與設定檔編輯器（2026-10-24）。

要守住的兩件事：

1. **拼錯會靜默 fallback**：Fish Audio 的模型是 HTTP header 選的，header 缺或拼錯不報錯，
   會安靜退回 s2.1-pro ⇒ 白名單必須在**寫入前**就擋下不合法名稱。
2. **只能改 fish_audio 區塊**：設定檔裡的 `llm.model` 是 LLM 模型（deepseek-v4.1-flash），
   與 TTS 無關；誤傷它會把整套 LLM 換掉。

外加一條「單一事實來源」靜態守衛：ps1 腳本不得自己再寫一份模型清單（會漂）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion import drama3_markup, lang_tags, model_registry
from clients.voice_companion.model_registry import (
    ASR_MODELS,
    TTS_MODELS,
    ModelConfigError,
    apply_to_file,
    read_fish_audio_models,
    set_fish_audio_models,
    validate_asr_model,
    validate_tts_model,
)

CONFIG_TEXT = """{
  "companion": {
    "id": "agent_akane",
    "display_name": "黑川茜"
  },
  "fish_audio": {
    "api_key": "",
    "voice_id": "4c11d21b14284d428074f76a1cf32298",
    "model": "drama-3",
    "asr_model": "transcribe-1-pro",
    "mode": "rest"
  },
  "llm": {
    "endpoint": "https://ollama.com/v1",
    "model": "deepseek-v4.1-flash:1017"
  },
  "web": {
    "port": 8765
  }
}
"""


class TestWhitelist:
    def test_tts_models_match_official_enum(self):
        assert set(TTS_MODELS) == {"s1", "s2-pro", "s2.1-pro", "s2.1-pro-free", "drama-3"}

    def test_asr_models_match_official_enum(self):
        assert set(ASR_MODELS) == {"transcribe-1", "transcribe-1-pro"}

    def test_validate_accepts_allowed(self):
        assert validate_tts_model("s2.1-pro") == "s2.1-pro"
        assert validate_asr_model("transcribe-1-pro") == "transcribe-1-pro"

    def test_validate_rejects_typos(self):
        for bad in ("drama_3", "s2.1_pro", "DRAMA-3", "s2.1-pro ", "drama3"):
            with pytest.raises(ModelConfigError):
                validate_tts_model(bad)
        for bad in ("transcribe-1_pro", "transcribe1", "TRANSCRIBE-1-PRO"):
            with pytest.raises(ModelConfigError):
                validate_asr_model(bad)


class TestEditScoping:
    def test_only_fish_audio_model_changes(self):
        out = set_fish_audio_models(CONFIG_TEXT, tts="s2.1-pro")
        obj = json.loads(out)
        assert obj["fish_audio"]["model"] == "s2.1-pro"
        assert obj["fish_audio"]["asr_model"] == "transcribe-1-pro"

    def test_llm_model_is_untouched(self):
        out = set_fish_audio_models(CONFIG_TEXT, tts="s2.1-pro", asr="transcribe-1")
        obj = json.loads(out)
        assert obj["llm"]["model"] == "deepseek-v4.1-flash:1017"

    def test_other_keys_untouched(self):
        out = set_fish_audio_models(CONFIG_TEXT, tts="s2.1-pro")
        obj = json.loads(out)
        assert obj["fish_audio"]["voice_id"] == "4c11d21b14284d428074f76a1cf32298"
        assert obj["fish_audio"]["mode"] == "rest"
        assert obj["companion"]["id"] == "agent_akane"
        assert obj["web"]["port"] == 8765

    def test_output_is_valid_json(self):
        out = set_fish_audio_models(CONFIG_TEXT, tts="s2.1-pro")
        json.loads(out)  # 不拋即通過

    def test_idempotent_set_is_byte_identical(self):
        out = set_fish_audio_models(CONFIG_TEXT, tts="drama-3", asr="transcribe-1-pro")
        assert out == CONFIG_TEXT

    def test_invalid_model_raises_before_writing(self):
        with pytest.raises(ModelConfigError):
            set_fish_audio_models(CONFIG_TEXT, tts="drama_3")

    def test_missing_fish_audio_block_raises(self):
        with pytest.raises(ModelConfigError):
            set_fish_audio_models('{"llm": {"model": "x"}}', tts="s2.1-pro")

    def test_missing_key_raises(self):
        text = '{"fish_audio": {"voice_id": "v"}}'
        with pytest.raises(ModelConfigError):
            set_fish_audio_models(text, tts="s2.1-pro")


class TestReadAndApply:
    def test_read_models(self):
        got = read_fish_audio_models(CONFIG_TEXT)
        assert got == {"model": "drama-3", "asr_model": "transcribe-1-pro"}

    def test_apply_to_file_writes_and_reports(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(CONFIG_TEXT, encoding="utf-8", newline="")
        res = apply_to_file(str(p), tts="s2.1-pro")
        assert res["old_model"] == "drama-3"
        assert res["new_model"] == "s2.1-pro"
        obj = json.loads(p.read_text(encoding="utf-8"))
        assert obj["llm"]["model"] == "deepseek-v4.1-flash:1017"

    def test_apply_to_file_keeps_line_endings_and_no_bom(self, tmp_path):
        p = tmp_path / "config.json"
        p.write_text(CONFIG_TEXT, encoding="utf-8", newline="")
        apply_to_file(str(p), tts="s2.1-pro")
        raw = p.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" not in raw


class TestSingleSourceOfTruth:
    def test_capability_sets_come_from_registry(self):
        assert lang_tags.MODELS_WITH_LANGUAGE_TAGS is model_registry.MODELS_WITH_LANGUAGE_TAGS
        assert drama3_markup.MODELS_WITH_DRAMA3_MARKUP is model_registry.MODELS_WITH_DRAMA3_MARKUP

    def test_ps1_does_not_duplicate_the_whitelist(self):
        """ps1 必須向 model_registry 取白名單，不得自己再寫一份（會漂）。"""
        ps1 = (REPO_ROOT / "scripts" / "vc_model.ps1").read_text(encoding="utf-8-sig")
        assert "clients.voice_companion.model_registry" in ps1
        # 不得出現硬編碼的模型清單字面值
        for literal in ("s2.1-pro-free", "'drama-3'", '"drama-3"'):
            assert literal not in ps1, f"ps1 不得內嵌模型名清單（{literal}）——請改用 model_registry"

    def test_ps1_exists_and_is_utf8_bom(self):
        """PowerShell 5.1 讀無 BOM 的 .ps1 會用 ANSI(cp950) 解碼 ⇒ 中文全毀、腳本解析失敗。"""
        raw = (REPO_ROOT / "scripts" / "vc_model.ps1").read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
