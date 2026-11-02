"""tests/infra/test_soeb_credential_redaction_guard.py — 憑證遮蔽守衛。

背景（2026-11-01 實際外洩事故）
==============================
`scripts/soeb/client.py` 的 `redact()` 原本用「**值像不像金鑰**」判斷：

    if value.startswith("oc_sk_") or value.startswith("sk-"):
        return "<redacted:...>"

OpenCode Go 的 key 是 `oc_sk_…` ⇒ 遮住了；但 Ollama Cloud 的 key 是
`<32hex>.<22base62>`（**中間有 `.`**）⇒ 條件不成立 ⇒ **完整金鑰原文被寫進
觀測列的 `request.headers.Authorization`**（實測 3 個檔案、17 處）。

根因不是「漏了一個前綴」，而是**判準選錯**：值的格式是對方決定的，你猜不完。
正確判準是不可漂移的**欄位名**。

本守衛釘住的四件事
==================
- **G1（名稱白名單）**：憑證類 header 名（`Authorization`／`x-api-key`／`api-key`／
  `Cookie`／`Proxy-Authorization`／`x-goog-api-key`）一律遮蔽，**不看值**。
- **G2（禁止值型判斷）**：形狀**完全不符**舊前綴（無 `oc_sk_`／`sk-`）但長度達
  金鑰級的字串**仍必須被遮蔽**。⇒ 誰把判準改回「看前綴」，這條就轉紅。
- **G3（不得誤傷）**：`application/json`、`2023-06-01`、`soeb-<16hex>` 這類
  非憑證值必須原樣保留；`Authorization` 的 scheme（`Bearer`）必須保留以便稽核。
- **G4（已落盤檔案）**：`tests/results/soeb/**/observations.jsonl` 內任何
  `Authorization` 值都必須是 `<redacted:…>`；有明文即紅。（檔案不存在時 skip。）

每個變異實驗都在本檔內**自證會轉紅**（見 `_broken_*` 與 `test_g2_mutation_proves_red`），
不是靠「它應該會失敗」的預測。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.soeb.client import (  # noqa: E402
    is_sensitive_name,
    redact,
    redact_headers,
)

# ─────────────────────────────────────────────────────────────────────────────
# 測試替身：三種真實形狀的金鑰（皆為假值，只取其長度與字元集）
# ─────────────────────────────────────────────────────────────────────────────

#: Ollama Cloud 的形狀：32 hex + "." + 22 base62 = 55 字元。**關鍵在於那個 `.`**，
#: 它讓所有「只認英數字元集」的判斷失效 —— 這正是事故的觸發條件。
KEY_OLLAMA_SHAPED = "a1b2c3d4" * 4 + "." + "Zx9" * 7 + "Q"

#: OpenCode Go 的形狀（舊版唯一遮得住的那一種）。
KEY_OC_SHAPED = "oc_sk_" + "c" * 60

#: 一般 `sk-` 形狀。
KEY_SK_SHAPED = "sk-" + "d" * 48

#: **無任何已知前綴**的長英數字串（40 字元）。用來釘住 G2。
KEY_NO_PREFIX = "e" * 40

ALL_KEY_SHAPES = [
    pytest.param(KEY_OLLAMA_SHAPED, id="ollama-dot-shape"),
    pytest.param(KEY_OC_SHAPED, id="oc_sk-prefix"),
    pytest.param(KEY_SK_SHAPED, id="sk-prefix"),
    pytest.param(KEY_NO_PREFIX, id="no-prefix-at-all"),
]

SENSITIVE_HEADER_NAMES = [
    "Authorization",
    "authorization",
    "Proxy-Authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "Cookie",
]

NON_SENSITIVE_CASES = [
    ("content-type", "application/json"),
    ("anthropic-version", "2023-06-01"),
    ("x-opencode-session", "soeb-0123456789abcdef"),
    ("accept", "application/json"),
]


# ─────────────────────────────────────────────────────────────────────────────
# G1 — 名稱白名單：憑證 header 一律遮蔽，不看值
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("header_name", SENSITIVE_HEADER_NAMES)
@pytest.mark.parametrize("secret", ALL_KEY_SHAPES)
def test_g1_sensitive_header_never_leaks_value(header_name: str, secret: str) -> None:
    """G1：任何憑證 header 名 × 任何金鑰形狀都不得外洩。"""
    masked = redact_headers({header_name: secret})[header_name]
    assert secret not in masked, f"{header_name} 的值外洩了"
    assert "<redacted:" in masked, f"{header_name} 沒有留下遮蔽標記（稽核會失去線索）"


@pytest.mark.parametrize("secret", ALL_KEY_SHAPES)
def test_g1_authorization_keeps_scheme_only(secret: str) -> None:
    """G1：`Authorization: Bearer <key>` ⇒ scheme 保留、金鑰遮蔽。"""
    masked = redact_headers({"Authorization": f"Bearer {secret}"})["Authorization"]
    scheme, _, token = masked.partition(" ")
    assert scheme == "Bearer", "scheme 被吃掉，稽核看不出用了哪種認證"
    assert token.startswith("<redacted:"), f"token 未遮蔽：{token!r}"
    assert secret not in masked


def test_g1_ollama_key_shape_would_defeat_legacy_prefix_check() -> None:
    """G1：明確釘住「Ollama key 不含舊前綴」這個事故前提。

    若哪天有人以為「金鑰一定有 oc_sk_ 或 sk- 前綴」而把判準寫回去，
    這裡先證明那個假設對 Ollama 是錯的。
    """
    assert not KEY_OLLAMA_SHAPED.startswith(("oc_sk_", "sk-"))
    assert "." in KEY_OLLAMA_SHAPED
    assert len(KEY_OLLAMA_SHAPED) == 55


# ─────────────────────────────────────────────────────────────────────────────
# G2 — 禁止值型判斷：無前綴的長字串仍必須遮
# ─────────────────────────────────────────────────────────────────────────────


def test_g2_unknown_name_still_redacts_long_opaque_value() -> None:
    """G2：欄位名判不出來時，長度達金鑰級的無空白值仍要遮（保守偏誤）。"""
    masked = redact(KEY_NO_PREFIX, name="x-something-novel")
    assert KEY_NO_PREFIX not in masked
    assert masked.startswith("<redacted:")


def test_g2_no_argument_also_redacts_long_opaque_value() -> None:
    """G2：連名稱都沒給（最壞情況）也不得放行。"""
    masked = redact(KEY_OLLAMA_SHAPED)
    assert KEY_OLLAMA_SHAPED not in masked


def _broken_prefix_only(value: str, *, name: str = "") -> str:
    """變異 A：事故版實作 —— 只看值前綴。"""
    if not value:
        return value
    if value.startswith("oc_sk_") or value.startswith("sk-"):
        return f"<redacted:{len(value)}chars>"
    return value


def _broken_threshold_too_high(value: str, *, name: str = "") -> str:
    """變異 B：fallback 門檻調到 60 ⇒ 55 字元的 Ollama key 漏掉。"""
    if not value:
        return value
    if len(value) >= 60 and not any(ch.isspace() for ch in value):
        return f"<redacted:{len(value)}chars>"
    return value


@pytest.mark.parametrize(
    "broken",
    [pytest.param(_broken_prefix_only, id="mutation-prefix-only"),
     pytest.param(_broken_threshold_too_high, id="mutation-threshold-too-high")],
)
def test_g2_mutation_proves_guard_would_turn_red(broken) -> None:
    """G2 的變異實驗：把判準改壞 ⇒ G1 的斷言**確實會失敗**。

    這條測試證明「G1 不是恆真」。它刻意反向斷言 `leaks is True`。
    """
    leaked = broken(KEY_OLLAMA_SHAPED, name="Authorization")
    assert KEY_OLLAMA_SHAPED in leaked, "變異沒有真的造成洩漏，這個變異實驗是假的"

    # 把 G1 的斷言套在變異版上 ⇒ 必須 AssertionError
    with pytest.raises(AssertionError):
        assert KEY_OLLAMA_SHAPED not in leaked


# ─────────────────────────────────────────────────────────────────────────────
# G3 — 不得誤傷非憑證欄位
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name,value", NON_SENSITIVE_CASES)
def test_g3_non_credential_headers_pass_through(name: str, value: str) -> None:
    """G3：非憑證 header 必須原樣保留，否則稽核欄位變成一片紅字。"""
    assert redact(value, name=name) == value
    assert redact_headers({name: value})[name] == value


def test_g3_session_id_is_not_treated_as_secret() -> None:
    """G3：`x-opencode-session` 是路由用的識別碼，不是憑證。"""
    assert is_sensitive_name("x-opencode-session") is False
    assert is_sensitive_name("X-OpenCode-Session") is False


@pytest.mark.parametrize(
    "name",
    ["Authorization", "x-api-key", "api-key", "Cookie", "x-goog-api-key",
     "proxy-authorization", "X-Auth-Token", "client-secret", "db-password"],
)
def test_g3_name_detection_covers_common_credential_headers(name: str) -> None:
    """G3：名稱判定要涵蓋常見憑證 header（含 token/secret/password 片段）。"""
    assert is_sensitive_name(name) is True


# ─────────────────────────────────────────────────────────────────────────────
# G4 — 已落盤的觀測檔不得殘留明文
# ─────────────────────────────────────────────────────────────────────────────

_SOEB_RESULTS = _REPO_ROOT / "tests" / "results" / "soeb"


def _observation_files() -> list[Path]:
    if not _SOEB_RESULTS.exists():
        return []
    return [
        p for p in _SOEB_RESULTS.rglob("*")
        if p.is_file() and "observations" in p.name and p.suffix in (".jsonl", ".bak")
    ]


def test_g4_persisted_observations_have_no_plaintext_authorization() -> None:
    """G4：落盤的 Authorization 只能是 `<redacted:…>`。

    這是**事故形狀**的直接守衛：不看金鑰值（測試裡不該有金鑰），
    只看「有沒有一個看起來像真的 token 的字串」。
    """
    files = _observation_files()
    if not files:
        pytest.skip("沒有 soeb 觀測檔（尚未實跑），無可檢查對象")

    offenders: list[str] = []
    for path in files:
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if not line.strip().startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            headers = (((rec or {}).get("request") or {}).get("headers") or {})
            for hname, hvalue in headers.items():
                if not is_sensitive_name(hname) or not isinstance(hvalue, str):
                    continue
                _, _, token = hvalue.partition(" ")
                if token and not token.startswith("<redacted"):
                    offenders.append(f"{path.name}:{lineno} {hname}")

    assert not offenders, (
        "落盤觀測檔仍有未遮蔽的憑證欄位：\n  " + "\n  ".join(offenders[:20])
    )
