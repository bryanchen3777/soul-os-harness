# -*- coding: utf-8 -*-
"""soeb.client — SOEB 的統一呼叫層（OpenCode Go 單一通道）

為什麼不用 `~/.minimax/skills/llm-call/scripts/llm_call.py`
==========================================================
`llm_call.py` 只回 ``{"model","response"}``：**沒有 usage、沒有 latency、沒有
finish_reason、沒有 raw request/response**。SOEB 的評分與可重現性要求每一格都
留下「送了什麼、回了什麼、花了多少 token、被截斷了沒」，否則無法區分

    *「模型不懂」*  vs  *「輸出預算被 reasoning 吃光」*

（2026-10-07 的 REM 迴歸就踩過後者：spark13 在 max_tokens=2048 時輸出全空，
重跑 8000 才拿到結果。）因此本模組自建 HTTP 呼叫，並刻意**不修改**
`llm_call.py`（它是共享技能，其他用途依賴它的輸出契約）。

三種協定（OpenCode Go 依模型分派，不可互換）
============================================
====================  ==========================  ==========================
協定                   端點                         文字所在
====================  ==========================  ==========================
``chat-completions``  ``POST {base}/chat/completions`` ``choices[0].message.content``
``responses``         ``POST {base}/responses``       ``output[] -> message -> content[] -> output_text``
``messages``          ``POST {base}/messages``        ``content[] -> text``（anthropic 形狀）
====================  ==========================  ==========================

安全邊界
========
1. **不在 import 時發任何請求**，也不在模組層讀取憑證；一切透過顯式建構。
2. **憑證永不進入回傳值或日誌**：`redact()` 對 body/header 做遮蔽後才落盤。
3. 本模組不啟動伺服器、不綁任何本機埠、不碰行程。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

# ─────────────────────────────────────────────────────────────────────────────
# 憑證解析
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = Path(
    os.environ.get("__MAVIS_PARENT_DATA_DIR")
    or os.environ.get("MINIMAX_DATA_DIR")
    or str(Path.home() / ".minimax")
) / "config.yaml"

#: OpenCode Go 三個 custom_provider 條目共用同一把 key 與同一個 baseURL，
#: 差別只在 `api`（=> 協定）。這裡把 provider 別名對到協定名。
PROVIDER_ALIASES = {
    "opencode-go": "chat-completions",
    "opencode-go-responses": "responses",
    "opencode-go-anthropic": "messages",
}

API_TO_PROTOCOL = {
    "openai-completions": "chat-completions",
    "openai-responses": "responses",
    "openai-codex-responses": "responses",
    "anthropic-messages": "messages",
}

#: 第二通道（2026-11-01 Owner 裁示）：DeepSeek V4.1 Flash 在 OpenCode Go 被 workspace
#: Privacy 地區設定擋掉（HTTP 400 `requires Global regions`），改走 Ollama Cloud。
#:
#: ⚠️ 這會破壞「六家同一通道」的可比性。Owner 已知並裁決採用，但**必須在報告裡揭露**：
#: 通道不同 ⇒ baseURL、金鑰、路由、可用參數、計費、量化版本都可能不同。
#: 尤其 Ollama Cloud 的 `deepseek-v4.1-flash:1017` 帶了 `:1017` 版本戳，那是
#: OpenCode Go 的 `deepseek-v4.1-flash` 沒有的資訊 ⇒ **兩者不保證是同一個權重**。
#: 因此 registry 用 `channel_note` 逐顆記錄，manifest 也會帶 `channels` 逐顆揭露。
OLLAMA_ALIAS = "ollama"
#: 端點鍵刻意不叫 `chat-completions`：協定相同、**通道不同**，混用會讓
#: 「哪一顆走哪條路」消失在程式碼裡，事後無法從觀測列還原。
OLLAMA_ENDPOINT_KEY = "ollama-chat"

_SESSION_ID: str | None = None


def session_id() -> str:
    """OpenCode 路由需要 session id；desktop runtime 自己會注入，腳本要自備。"""
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = (
            os.environ.get("OPENCODE_SESSION_ID") or f"soeb-{uuid.uuid4().hex[:16]}"
        )
    return _SESSION_ID


def _is_opencode_host(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host == "opencode.ai" or host.endswith(".opencode.ai")


#: 🔴 遮蔽**只認欄位名**，不認值的外觀（2026-11-01 實際外洩事故後改寫）。
#:
#: 事故：舊版 `redact()` 用「值像不像金鑰」判斷（`startswith("oc_sk_")`／
#: `startswith("sk-")`）。OpenCode Go 的 key 是 ``oc_sk_…`` 所以遮住了；但 Ollama
#: Cloud 的 key 格式是 ``<32hex>.<22base62>``（**中間有 `.`**）⇒ 條件不成立
#: ⇒ **完整金鑰原文被寫進 4 筆觀測列的 `request.headers.Authorization`**。
#:
#: 教訓：值的格式是對方決定的，你猜不完。判準必須是不可漂移的**欄位名**。
#: 反向風險（遮太多）只在欄位名判不出來時才用值長度當保險，且門檻刻意偏高。
_SENSITIVE_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "apikey",
        "x-goog-api-key",
        "cookie",
        "set-cookie",
    }
)

#: 欄位名片段（任何位置命中即視為憑證欄位）。
_SENSITIVE_NAME_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api-key",
    "apikey",
    "credential",
    "private-key",
)

#: 值型 fallback 的長度門檻。實測我們自己會送出的非憑證 header 值最長是
#: ``x-opencode-session: soeb-<16hex>`` = 21 字元、``anthropic-version: 2023-06-01``
#: = 10 字元 ⇒ 取 24 不會誤傷，卻能蓋住所有已知金鑰（最短的 ``sk-…`` 也 >= 24）。
_VALUE_FALLBACK_MIN_LEN = 24


def is_sensitive_name(name: str) -> bool:
    """這個欄位名是不是憑證欄位？純名稱比對，不看值。"""
    n = (name or "").strip().lower()
    if not n:
        return False
    if n in _SENSITIVE_HEADER_NAMES:
        return True
    return any(frag in n for frag in _SENSITIVE_NAME_FRAGMENTS)


def redact(value: str, *, name: str = "") -> str:
    """遮蔽憑證值。用於任何要落盤的欄位。

    判準順序（**先名稱、後保險**）：

    1. ``name`` 是憑證欄位名 ⇒ 一律遮蔽。這一條蓋掉所有我們自己會送出的 header。
    2. 名稱判不出來時的保守 fallback：長度 >= 24 且不含空白 ⇒ 也遮蔽。
       寧可遮太多（失去稽核價值）也不要外洩（不可逆）。

    刻意**不保留任何字元前綴**：先前版本留下 ``oc_sk_9…`` 這種片段，結果任何用
    ``grep oc_sk_`` 做的洩漏檢查都會命中遮蔽後的字串而誤報。
    """
    if not value:
        return value
    if name and is_sensitive_name(name):
        return f"<redacted:{len(value)}chars>"
    if len(value) >= _VALUE_FALLBACK_MIN_LEN and not any(ch.isspace() for ch in value):
        return f"<redacted:{len(value)}chars>"
    return value


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """逐 header 遮蔽。``Authorization`` 保留 scheme（``Bearer``）以便稽核。"""
    out = {}
    for k, v in (headers or {}).items():
        scheme, _, token = (v or "").partition(" ")
        if scheme and token and k.strip().lower() in ("authorization", "proxy-authorization"):
            out[k] = f"{scheme} {redact(token, name=k)}"
        else:
            out[k] = redact(v, name=k)
    return out


@dataclass(frozen=True)
class Endpoint:
    """一個可呼叫的 (base_url, api_key, protocol) 三元組。"""

    base_url: str
    api_key: str = field(repr=False)
    protocol: str = "chat-completions"


def load_endpoints(config_path: str | Path | None = None) -> dict[str, Endpoint]:
    """從 MiniMax Code 的 config.yaml 讀出可用端點。

    回傳 ``{endpoint_key: Endpoint}``：

    - ``chat-completions`` / ``responses`` / ``messages`` ← OpenCode Go（主通道）
    - ``ollama-chat`` ← Ollama Cloud（僅供被主通道擋掉的模型補位）

    缺少任何一項都會被明確跳過，不猜、不填預設值。Ollama 條目存在與否不影響主通道。
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"找不到 MiniMax Code 設定檔：{path}。"
            "SOEB 需要其中的 custom_provider.opencode-go-* 條目取得 baseURL 與金鑰。"
        )
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    custom = cfg.get("custom_provider") or {}
    out: dict[str, Endpoint] = {}
    for alias, fallback_protocol in PROVIDER_ALIASES.items():
        pconf = custom.get(alias)
        if not pconf:
            continue
        opts = pconf.get("options") or {}
        base = opts.get("baseURL") or ""
        key = opts.get("apiKey") or ""
        if not base or not key:
            continue
        protocol = API_TO_PROTOCOL.get(pconf.get("api") or "", fallback_protocol)
        out[protocol] = Endpoint(base_url=base.rstrip("/"), api_key=key, protocol=protocol)
    if not out:
        raise RuntimeError(
            f"{path} 內沒有任何可用的 opencode-go custom_provider 條目"
            f"（找過 {sorted(PROVIDER_ALIASES)}）。"
        )

    # 第二通道：Ollama Cloud。缺了不算錯（主通道照跑），只是 blocked 的模型補不了位。
    ollama = custom.get(OLLAMA_ALIAS)
    if ollama:
        opts = ollama.get("options") or {}
        base = (opts.get("baseURL") or "").rstrip("/")
        key = opts.get("apiKey") or ""
        if base and key:
            out[OLLAMA_ENDPOINT_KEY] = Endpoint(
                base_url=base, api_key=key, protocol="chat-completions"
            )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 請求組裝（三協定）
# ─────────────────────────────────────────────────────────────────────────────


def build_request(
    protocol: str,
    endpoint: Endpoint,
    model_id: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float | None = None,
    effort: str | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """回傳 ``(url, headers, body)``。純函式，不發請求。"""
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if protocol == "chat-completions":
        url = f"{endpoint.base_url}/chat/completions"
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if effort:
            body["reasoning_effort"] = effort

    elif protocol == "responses":
        url = f"{endpoint.base_url}/responses"
        headers["Authorization"] = f"Bearer {endpoint.api_key}"
        items: list[dict[str, Any]] = []
        instructions: list[str] = []
        for msg in messages:
            if msg["role"] == "system":
                instructions.append(msg["content"])
            else:
                items.append({"role": msg["role"], "content": msg["content"]})
        body = {
            "model": model_id,
            "input": items,
            "max_output_tokens": max_tokens,
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        if temperature is not None:
            body["temperature"] = temperature
        if effort:
            # Responses 形狀用 reasoning.effort；實測 Go 對 `effort` 頂層欄位無效。
            body["reasoning"] = {"effort": effort}

    elif protocol == "messages":
        url = f"{endpoint.base_url}/messages"
        headers["x-api-key"] = endpoint.api_key
        headers["anthropic-version"] = "2023-06-01"
        system_parts: list[str] = []
        filtered: list[dict[str, str]] = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
            else:
                filtered.append(msg)
        body = {"model": model_id, "max_tokens": max_tokens, "messages": filtered}
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if temperature is not None:
            body["temperature"] = temperature

    else:
        raise ValueError(f"未知協定：{protocol!r}")

    if _is_opencode_host(url) and not any(
        k.lower() == "x-opencode-session" for k in headers
    ):
        headers["x-opencode-session"] = session_id()

    return url, headers, body


# ─────────────────────────────────────────────────────────────────────────────
# 回應解析（三協定）
# ─────────────────────────────────────────────────────────────────────────────


def extract_text(protocol: str, data: dict[str, Any]) -> str:
    if protocol == "chat-completions":
        choices = data.get("choices") or []
        return (choices[0].get("message") or {}).get("content") or "" if choices else ""

    if protocol == "responses":
        flat = data.get("output_text")
        if isinstance(flat, str) and flat:
            return flat
        chunks: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") not in (None, "message"):
                continue  # reasoning / tool-call item 不含助手正文
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    chunks.append(part.get("text") or "")
        return "".join(chunks)

    if protocol == "messages":
        for block in data.get("content") or []:
            if block.get("type") == "text":
                return block.get("text") or ""
        return ""

    raise ValueError(f"未知協定：{protocol!r}")


def extract_reasoning(protocol: str, data: dict[str, Any]) -> str:
    """取出 thinking/reasoning 正文（若有）。只用於診斷，不參與評分。"""
    if protocol == "chat-completions":
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return msg.get("reasoning") or msg.get("reasoning_content") or ""

    if protocol == "responses":
        chunks: list[str] = []
        for item in data.get("output") or []:
            if item.get("type") == "reasoning":
                for part in item.get("content") or []:
                    if part.get("type") in ("reasoning_text", "summary_text", "text"):
                        chunks.append(part.get("text") or "")
        return "".join(chunks)

    if protocol == "messages":
        chunks = []
        for block in data.get("content") or []:
            if block.get("type") in ("thinking", "reasoning"):
                chunks.append(block.get("thinking") or block.get("text") or "")
        return "".join(chunks)

    return ""


def extract_finish_reason(protocol: str, data: dict[str, Any]) -> str:
    if protocol == "chat-completions":
        choices = data.get("choices") or []
        if choices:
            return choices[0].get("finish_reason") or ""
        return ""
    if protocol == "responses":
        if data.get("status") == "incomplete":
            details = data.get("incomplete_details") or {}
            return f"incomplete:{details.get('reason') or 'unknown'}"
        return data.get("status") or ""
    if protocol == "messages":
        return data.get("stop_reason") or ""
    return ""


def normalize_usage(protocol: str, data: dict[str, Any]) -> dict[str, Any]:
    """把三協定的 usage 攤平成同一組欄位，原始 usage 另外完整保留。

    三協定欄位名不同（`prompt_tokens` vs `input_tokens`），且 reasoning token
    可能各自放在 details 裡 —— 這正是「輸出為空」最常見的誤判來源，必須攤平。
    """
    raw = data.get("usage") or {}
    if protocol == "chat-completions":
        in_tok = raw.get("prompt_tokens")
        out_tok = raw.get("completion_tokens")
        total = raw.get("total_tokens")
        cached = (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
        reasoning = (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    elif protocol == "responses":
        in_tok = raw.get("input_tokens")
        out_tok = raw.get("output_tokens")
        total = raw.get("total_tokens")
        cached = (raw.get("input_tokens_details") or {}).get("cached_tokens")
        reasoning = (raw.get("output_tokens_details") or {}).get("reasoning_tokens")
    elif protocol == "messages":
        in_tok = raw.get("input_tokens")
        out_tok = raw.get("output_tokens")
        total = (in_tok or 0) + (out_tok or 0) if (in_tok is not None or out_tok is not None) else None
        cached = raw.get("cache_read_input_tokens")
        reasoning = (raw.get("output_tokens_details") or {}).get("reasoning_tokens")
    else:
        in_tok = out_tok = total = cached = reasoning = None

    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": total,
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 呼叫結果
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CallResult:
    """一次呼叫的完整證據。``request``/``response_raw`` 已遮蔽金鑰。"""

    model_id: str
    protocol: str
    ok: bool
    latency_ms: int
    text: str = ""
    reasoning_text: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    usage_raw: dict[str, Any] = field(default_factory=dict)
    status_code: int | None = None
    error: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    response_raw: dict[str, Any] | None = None
    empty_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def explain_empty(result: CallResult) -> str:
    """回答「為什麼正文是空的」—— 不讓空字串被誤讀成「模型不懂」。

    已知三種：輸出預算被 reasoning 吃光 / 真的被截斷 / 模型就是回了空白。
    """
    if result.text:
        return ""
    fr = result.finish_reason or ""
    reasoning = result.usage.get("reasoning_tokens")
    if "length" in fr or "max_tokens" in fr or "incomplete" in fr:
        if reasoning:
            return f"輸出預算耗盡：finish_reason={fr}，其中 reasoning_tokens={reasoning}"
        return f"輸出預算耗盡：finish_reason={fr}"
    if reasoning:
        return f"finish_reason={fr or '(未提供)'}，reasoning_tokens={reasoning}，正文為空"
    return f"模型回傳空正文：finish_reason={fr or '(未提供)'}"


def call(
    endpoint: Endpoint,
    model_id: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 4096,
    temperature: float | None = None,
    effort: str | None = None,
    timeout: float = 180.0,
    save_raw: bool = True,
) -> CallResult:
    """發出一次呼叫，永不拋出網路錯誤（一律轉成 ``ok=False`` 的結果）。"""
    url, headers, body = build_request(
        endpoint.protocol,
        endpoint,
        model_id,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        effort=effort,
    )
    request_record = {
        "url": url,
        "headers": redact_headers(headers),
        "body": body,
    }

    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=body)
        latency_ms = int((time.perf_counter() - started) * 1000)
    except Exception as exc:  # 網路層：明確記錄，不重試（重試會破壞可比性）
        latency_ms = int((time.perf_counter() - started) * 1000)
        return CallResult(
            model_id=model_id,
            protocol=endpoint.protocol,
            ok=False,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
            request=request_record,
        )

    if resp.status_code != 200:
        return CallResult(
            model_id=model_id,
            protocol=endpoint.protocol,
            ok=False,
            latency_ms=latency_ms,
            status_code=resp.status_code,
            error=resp.text[:2000],
            request=request_record,
        )

    try:
        data = resp.json()
    except Exception as exc:
        return CallResult(
            model_id=model_id,
            protocol=endpoint.protocol,
            ok=False,
            latency_ms=latency_ms,
            status_code=resp.status_code,
            error=f"回應不是 JSON：{type(exc).__name__}: {exc}",
            request=request_record,
        )

    result = CallResult(
        model_id=model_id,
        protocol=endpoint.protocol,
        ok=True,
        latency_ms=latency_ms,
        text=extract_text(endpoint.protocol, data),
        reasoning_text=extract_reasoning(endpoint.protocol, data),
        finish_reason=extract_finish_reason(endpoint.protocol, data),
        usage=normalize_usage(endpoint.protocol, data),
        usage_raw=(data.get("usage") or {}),
        status_code=resp.status_code,
        request=request_record,
        response_raw=data if save_raw else None,
    )
    result.empty_reason = explain_empty(result)
    return result


def ping(endpoint: Endpoint, model_id: str, *, timeout: float = 120.0) -> CallResult:
    """最小可用性探測：確認這個模型在本協定下真的能回話。"""
    return call(
        endpoint,
        model_id,
        [{"role": "user", "content": "Reply with exactly one word: pong"}],
        max_tokens=256,
        temperature=0.0,
        timeout=timeout,
        save_raw=False,
    )


__all__ = [
    "CallResult",
    "Endpoint",
    "build_request",
    "call",
    "explain_empty",
    "extract_finish_reason",
    "extract_reasoning",
    "extract_text",
    "load_endpoints",
    "normalize_usage",
    "ping",
    "redact",
    "redact_headers",
    "session_id",
]
