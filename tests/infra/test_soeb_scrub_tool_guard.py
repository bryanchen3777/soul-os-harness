# -*- coding: utf-8 -*-
"""tests/infra/test_soeb_scrub_tool_guard.py — 清洗工具的結構性守衛。

背景（2026-11-01 同一場外洩事故的第二次失誤）
============================================
第一版清洗腳本做了「先備份再清洗」：

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(raw, ...)      # ← raw 含明文金鑰
    path.write_text(cleaned, ...)

結果 **`.bak` 變成明文的第二份副本**，而且第一輪複查用副檔名白名單
（`.jsonl/.json/.md/.txt/.log`）掃描，`.jsonl.bak` 的 suffix 是 `.bak`
⇒ **複查漏掉自己剛製造的那一份**。

⇒ 修復不是「記得掃 .bak」，是**移除製造明文副本的能力**：
`scripts/soeb/scrub.py` 不建立任何備份，寫入走同目錄 temp + `os.replace`。

本守衛釘住的三件事
==================
- **S1（清洗正確性）**：憑證欄位名一律遮蔽並保留 scheme；夾在散文裡的完整金鑰
  也會被 fallback 取代；非憑證欄位不受影響。
- **S2（寫入範圍不可覆寫）**：`SCAN_ROOT` 寫死在 `tests/results/soeb`，
  `main()` **不得**提供任何更改寫入範圍的旗標（AGENTS.md 鐵律 12：
  移除寫入能力，而不是拿會漂移的值去守門）。
- **S3（不得製造明文副本）**：端到端跑完後，目錄內不得出現 `.bak`、
  不得留下 temp 檔；並以 `_broken_legacy_backup` 變異自證「這個斷言真的會轉紅」。

全部使用合成金鑰（假值）與 pytest 的 `tmp_path`，**不讀真憑證、不碰真產物**。
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.soeb import scrub  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 測試替身：合成金鑰（形狀取自真實，值為假）
# ─────────────────────────────────────────────────────────────────────────────

#: Ollama 形狀：32 hex + "." + 22 base62。那個 `.` 是舊值型判準失效的原因。
FAKE_OLLAMA = "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOpQrStUv"
#: OpenCode Go 形狀。
FAKE_OC = "oc_sk_" + "F" * 44
SECRETS = {"ollama-chat": FAKE_OLLAMA, "chat-completions": FAKE_OC}


def _observation_line() -> str:
    """模擬舊 runner 寫出的一列觀測（含明文金鑰與散文中的殘留）。"""
    return json.dumps(
        {
            "model_id": "m",
            "task": "T1",
            "ok": True,
            "request": {
                "url": "https://example.invalid/v1/chat/completions",
                "headers": {
                    "Authorization": f"Bearer {FAKE_OC}",
                    "x-api-key": FAKE_OLLAMA,
                    "Content-Type": "application/json",
                },
            },
            "note": f"embedded leak {FAKE_OLLAMA} in prose",
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# S1 — 清洗正確性
# ─────────────────────────────────────────────────────────────────────────────


def test_s1_masks_sensitive_fields_and_keeps_scheme() -> None:
    """S1：憑證欄位名一律遮蔽，`Authorization` 的 scheme 必須保留。"""
    cleaned, changed = scrub.scrub_jsonl_text(_observation_line() + "\n", SECRETS)
    record = json.loads(cleaned)

    assert FAKE_OC not in cleaned
    assert FAKE_OLLAMA not in cleaned
    assert changed >= 1

    headers = record["request"]["headers"]
    scheme, _, token = headers["Authorization"].partition(" ")
    assert scheme == "Bearer", "scheme 被吃掉，稽核看不出用了哪種認證"
    assert token.startswith("<redacted:"), f"token 未遮蔽：{token!r}"
    assert headers["x-api-key"].startswith("<redacted:")


def test_s1_replaces_embedded_key_in_prose() -> None:
    """S1：金鑰若被別人塞進自由文本，也要被 fallback 取代（不能只靠欄位名）。"""
    cleaned, _ = scrub.scrub_jsonl_text(_observation_line() + "\n", SECRETS)
    assert "<redacted:leak-fallback>" in cleaned
    assert FAKE_OLLAMA not in cleaned


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("Content-Type", "application/json"),
        ("anthropic-version", "2023-06-01"),
        ("x-opencode-session", "soeb-0123456789abcdef"),
    ],
)
def test_s1_non_credential_values_survive(name: str, value: str) -> None:
    """S1：非憑證欄位不得被誤傷（否則清洗會破壞可稽核性）。"""
    _, changed = scrub.scrub_structure({name: value}, SECRETS)
    assert changed == 0


# ─────────────────────────────────────────────────────────────────────────────
# S2 — 寫入範圍不可覆寫（鐵律 12）
# ─────────────────────────────────────────────────────────────────────────────


def test_s2_scan_root_is_pinned_under_repo() -> None:
    """S2：掃描根目錄寫死在 repo 的 `tests/results/soeb`。"""
    assert scrub.SCAN_ROOT == _REPO_ROOT / "tests" / "results" / "soeb"


@pytest.mark.parametrize("flag", ["--scan-root", "--root", "--path", "--dir", "--out"])
def test_s2_main_has_no_write_scope_flag(flag: str) -> None:
    """S2：`main()` 不得提供更改寫入範圍的旗標。

    這是鐵律 12 的直接落地：**移除寫入能力**，而不是用兩個會漂移的值比對來擋。
    """
    assert flag not in inspect.getsource(scrub.main)


# ─────────────────────────────────────────────────────────────────────────────
# S3 — 不得製造明文副本（端到端 + 變異自證）
# ─────────────────────────────────────────────────────────────────────────────


def _leftover_artifacts(root: Path) -> list[Path]:
    """回傳「不該存在」的檔案：備份檔與清洗用的 temp 檔。"""
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and (path.name.endswith(".bak") or path.name.endswith(".scrubtmp"))
    )


def test_s3_end_to_end_leaves_no_plaintext_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S3：跑完 `main()` 後，不得留下 `.bak`／temp，原檔必須已清洗。"""
    target = tmp_path / "observations.jsonl"
    target.write_text(_observation_line() + "\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(scrub, "SCAN_ROOT", tmp_path)
    monkeypatch.setattr(scrub, "load_secrets", lambda: dict(SECRETS))

    assert scrub.main([]) == 0

    text = target.read_text(encoding="utf-8")
    assert FAKE_OC not in text
    assert FAKE_OLLAMA not in text
    assert "<redacted:" in text

    leftovers = _leftover_artifacts(tmp_path)
    assert not leftovers, f"清洗後留下了明文副本或暫存檔：{leftovers}"


def _broken_legacy_backup(path: Path, raw: str, cleaned: str) -> None:
    """變異：重現第一版腳本的「先備份再清洗」（會製造明文 `.bak`）。"""
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(raw, encoding="utf-8", newline="\n")
    path.write_text(cleaned, encoding="utf-8", newline="\n")


def test_s3_mutation_legacy_backup_turns_the_guard_red(tmp_path: Path) -> None:
    """S3 變異自證：把舊寫法放進來，`_leftover_artifacts` 必須抓到它。

    這證明「不得留下 .bak」這條斷言不是裝飾品 —— 它對已知的錯誤寫法會轉紅。
    """
    target = tmp_path / "observations.jsonl"
    raw = _observation_line() + "\n"
    target.write_text(raw, encoding="utf-8", newline="\n")

    _broken_legacy_backup(target, raw, '{"scrubbed": true}\n')

    leftovers = _leftover_artifacts(tmp_path)
    assert leftovers, "變異版本沒有留下 .bak，代表這個斷言根本偵測不到明文副本"
    assert any(p.name.endswith(".bak") for p in leftovers)
    # 明文確實進到副本裡（這就是為什麼不能備份原始內容）
    backup = next(p for p in leftovers if p.name.endswith(".bak"))
    assert FAKE_OLLAMA in backup.read_text(encoding="utf-8")
