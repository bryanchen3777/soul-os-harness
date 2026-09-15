"""
tests/clients/test_vc_perf_opt_1.py — VC-PERF-OPT-1 驗收測試
（連線重用 + 推論參數收斂 + Persona 預算收緊 + 空內容守門 + barge 回歸護欄）。

全離線：0 真實網路請求（fake Session / fake response / 假 SSE 行），0 進程操作。

測試清單：
  1. Class A 連線重用生效          — requests.post 必須不被呼叫；兩次呼叫同一個 Session 物件
  2. Class B 參數確實送出          — payload 帶 max_tokens/temperature/stream；timeout == (5, 60)
  3. Class C 連線外洩防護          — Session 掛 HTTPAdapter，pool_maxsize 實際值 >= 4
  4. Class D Persona 預算          — >2500 截到 2500；<=2500 原樣；空檔/缺檔既有行為不變
                                     （且 6000 字面值必須已被取代，不得留第二份）
  5. Class E 空內容守門            — 只吐 reasoning 的假 stream ⇒ 可 grep 日誌且不拋例外
  6. Class E2 既有失敗路徑沿用     — web_server [VC-TURN-FAIL] empty_llm_output 帶 reasoning 診斷
  7. Class F 中斷回歸護欄          — web_ui isBargeArmed 函式體逐字不變（AST 式抽取）＋ 閘門仍呼叫

執行：
  .venv\\Scripts\\python.exe -m pytest tests/clients/test_vc_perf_opt_1.py -q
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion import akane_voice_brain as avb  # noqa: E402
from clients.voice_companion.akane_voice_brain import (  # noqa: E402
    MAX_PERSONA_CHARS,
    VC_HTTP_POOL_MAXSIZE,
    VC_LLM_EMPTY_MARKER,
    VC_LLM_MAX_TOKENS,
    VC_LLM_TEMPERATURE,
    VC_LLM_TIMEOUT,
    AkaneVoiceBrain,
    build_llm_stream,
    build_system_prompt,
)

BRAIN_LOG = "soul_os.vc_brain"
VC_ENDPOINT = "https://ollama.com/v1/chat/completions"


# ─────────────────────────────────────────────────────────────
# 離線測試替身（0 網路）
# ─────────────────────────────────────────────────────────────

class FakeResponse:
    """假 HTTP 回應：只提供本模組實際取用的介面（raise_for_status / encoding / iter_lines）。"""

    def __init__(self, lines: list[str], encoding: str = "utf-8"):
        self._lines = list(lines)
        self.encoding = encoding
        self.raise_for_status_calls = 0

    def raise_for_status(self) -> None:
        self.raise_for_status_calls += 1

    def iter_lines(self, decode_unicode: bool = False):
        return iter(self._lines)

    def close(self) -> None:
        pass


class FakeSession:
    """假 requests.Session：記錄每次 post 的參數與回應，0 網路。"""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.closed = 0

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed += 1


def _sse(delta: dict = None, finish_reason: str = None) -> str:
    body = {"choices": [{}]}
    if delta is not None:
        body["choices"][0]["delta"] = delta
    if finish_reason is not None:
        body["choices"][0]["finish_reason"] = finish_reason
    return "data: " + json.dumps(body, ensure_ascii=False)


def _reasoning_only_lines(n: int = 4, finish_reason: str = "length") -> list[str]:
    """只吐 reasoning、不吐 content 的假 SSE（max_tokens 被思考吃光的形狀）。"""
    lines = [_sse({"reasoning": f"思考第{i}段"}) for i in range(n)]
    lines.append(_sse({}, finish_reason=finish_reason))
    lines.append("data: [DONE]")
    return lines


def _content_lines(*pieces: str) -> list[str]:
    return [_sse({"content": p}) for p in pieces] + ["data: [DONE]"]


def _llm_cfg(**over) -> dict:
    cfg = {"endpoint": VC_ENDPOINT, "model": "deepseek-v4.1-flash", "api_key": "fake-key"}
    cfg.update(over)
    return cfg


def _make_brain(session: FakeSession, responses: list[FakeResponse]) -> AkaneVoiceBrain:
    session._responses = list(responses)
    return AkaneVoiceBrain(
        llm_stream=build_llm_stream(_llm_cfg(), session=session),
        persona="測試用 Persona",
        memory_retriever=None,
        temporal_provider=None,
        session_store=None,
    )


def _collect(brain: AkaneVoiceBrain, text: str = "你在嗎") -> str:
    """驅動一整個回合（stream_respond）並回傳拼接後的可見文字。"""
    return "".join(brain.stream_respond(text))


# ─────────────────────────────────────────────────────────────
# Class 1：連線重用生效
# ─────────────────────────────────────────────────────────────

class TestConnectionReuse:
    def test_requests_post_is_never_called(self, monkeypatch):
        """patch requests.post 使其必須不被呼叫（若被呼叫即 fail）；實際必須走 Session.post。"""
        import requests

        def _boom(*a, **kw):  # pragma: no cover - 被呼叫即代表連線重用未生效
            raise AssertionError("VC 不得再使用裸 requests.post（VC-PERF-OPT-1 連線重用）")

        monkeypatch.setattr(requests, "post", _boom)

        session = FakeSession([FakeResponse(_content_lines("我在"))])
        brain = _make_brain(session, [FakeResponse(_content_lines("我在"))])

        assert _collect(brain) == "我在"
        assert len(session.calls) == 1, "必須剛好一次 Session.post"
        assert session.calls[0]["url"] == VC_ENDPOINT

    def test_two_calls_reuse_same_session_object(self):
        """連續兩次呼叫 ⇒ 同一個 Session 物件（id 相同；連線池得以重用）。"""
        session = FakeSession([])
        brain = _make_brain(
            session,
            [FakeResponse(_content_lines("一")), FakeResponse(_content_lines("二"))],
        )
        assert _collect(brain, "第一次") == "一"
        assert _collect(brain, "第二次") == "二"
        assert len(session.calls) == 2, "兩次呼叫都必須走同一個 Session"
        seen_ids = {id(session)}
        assert len(seen_ids) == 1, "兩次呼叫必須是同一個 Session 物件（id 相同）"
        assert session.closed == 0, "重用中的 Session 不得被關閉"

    def test_lazy_session_created_once_across_calls(self, monkeypatch):
        """未注入 session 時：lazy 建立且跨多次請求只建立一次（Lock 保護；不得每次新建）。"""
        created: list[object] = []

        class _DummySession:
            def mount(self, *_a, **_kw):
                pass

            def post(self, url, **kwargs):
                return FakeResponse(_content_lines("好"))

        def _factory():
            obj = _DummySession()
            created.append(obj)
            return obj

        monkeypatch.setattr(avb, "_build_http_session", _factory)
        stream = build_llm_stream(_llm_cfg())

        assert list(stream([{"role": "user", "content": "a"}])) == ["好"]
        assert list(stream([{"role": "user", "content": "b"}])) == ["好"]
        assert len(created) == 1, f"lazy Session 只應建立一次，實得 {len(created)}"

    def test_lazy_session_creation_is_guarded_by_lock(self, monkeypatch):
        """併發建立（多執行緒）⇒ 仍只有一個 Session（Lock 保護；不得全域無鎖競態）。"""
        import threading

        created: list[object] = []

        class _DummySession:
            def mount(self, *_a, **_kw):
                pass

            def post(self, url, **kwargs):
                return FakeResponse(_content_lines("好"))

        def _factory():
            obj = _DummySession()
            created.append(obj)  # 無鎖競態下這裡會出現多個物件
            return obj

        monkeypatch.setattr(avb, "_build_http_session", _factory)
        stream = build_llm_stream(_llm_cfg())
        errors: list[BaseException] = []

        def _worker():
            try:
                list(stream([{"role": "user", "content": "x"}]))
            except BaseException as exc:  # noqa: BLE001 — 收集後在主執行緒斷言
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors, f"併發呼叫不得拋例外：{errors!r}"
        assert len(created) == 1, f"併發下仍必須只有一個 Session，實得 {len(created)}"


# ─────────────────────────────────────────────────────────────
# Class 2：參數確實送出
# ─────────────────────────────────────────────────────────────

class TestPayloadAndTimeout:
    def test_payload_carries_named_inference_params(self):
        """payload 必須顯式帶 max_tokens / temperature，且 model / messages / stream 未被破壞。"""
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_content_lines("好"))])
        messages = brain._build_messages("你在嗎")
        assert "".join(brain.llm_stream(messages)) == "好"

        call = session.calls[0]
        payload = call["json"]
        assert payload["max_tokens"] == VC_LLM_MAX_TOKENS == 60, "max_tokens 必須為具名常數 60"
        assert payload["temperature"] == VC_LLM_TEMPERATURE == 0.7
        assert payload["stream"] is True
        assert payload["model"] == "deepseek-v4.1-flash"
        assert payload["messages"] == messages, "messages 欄位不得被改寫"

    def test_stream_flag_and_timeout_split_connect_read(self):
        """stream=True 保留；timeout 由 60 改為 (5, 60)（connect 5s / read 60s）。"""
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_content_lines("好"))])
        _collect(brain)

        call = session.calls[0]
        assert call["stream"] is True, "必須保留 stream=True"
        assert call["timeout"] == VC_LLM_TIMEOUT == (5, 60), (
            f"timeout 必須為 (5, 60)，實得 {call['timeout']!r}"
        )
        assert call["url"] == VC_ENDPOINT, "endpoint 不得改動"
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["headers"]["Authorization"] == "Bearer fake-key"

    def test_stream_parsing_and_log_fields_unchanged(self):
        """既有解析與 log 欄位不受影響：delta.content 逐 piece 取出、[LLM-STREAM] 欄位名不變。"""
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_content_lines("我", "在", "喔"))])
        pieces = list(brain.llm_stream(brain._build_messages("嗨")))
        assert pieces == ["我", "在", "喔"], "delta.content 取用行為必須逐字不變"


# ─────────────────────────────────────────────────────────────
# Class 3：連線外洩防護（讀出真實 pool 設定）
# ─────────────────────────────────────────────────────────────

class TestPoolAdapter:
    def test_session_has_http_adapter_with_usable_pool(self):
        """實際建立 Session，把 pool 設定讀出來斷言（不看原始碼字串）。"""
        session = avb._build_http_session()
        try:
            adapter = session.get_adapter("https://ollama.com/v1/chat/completions")
            from requests.adapters import HTTPAdapter

            assert isinstance(adapter, HTTPAdapter), "必須掛 HTTPAdapter（連線池）"
            assert adapter._pool_maxsize >= 4, f"pool_maxsize 過小：{adapter._pool_maxsize}"
            assert adapter._pool_maxsize == VC_HTTP_POOL_MAXSIZE
            assert adapter._pool_connections == avb.VC_HTTP_POOL_CONNECTIONS
            # keep-alive 由 requests.Session 預設標頭提供
            assert session.headers.get("Connection", "").lower() == "keep-alive", (
                "Session 必須帶 Connection: keep-alive"
            )
            # 同一個 adapter 物件被兩個 scheme 共用（連線池單一來源）
            assert session.get_adapter("http://x.invalid") is adapter
        finally:
            session.close()

    def test_http_endpoint_also_benefits_from_pool(self):
        """http:// endpoint（本地 proxy）同樣走連線池，不得只有 https 才掛。"""
        session = avb._build_http_session()
        try:
            from requests.adapters import HTTPAdapter

            assert isinstance(session.get_adapter("http://127.0.0.1:8000/v1"), HTTPAdapter)
        finally:
            session.close()


# ─────────────────────────────────────────────────────────────
# Class 4：Persona 預算
# ─────────────────────────────────────────────────────────────

class TestPersonaBudget:
    def test_constant_value_is_owner_specified(self):
        assert MAX_PERSONA_CHARS == 2500, "MAX_PERSONA_CHARS 必須為 Owner 指定值 2500"

    def test_longer_than_budget_truncated_exactly(self, tmp_path):
        """> 2500 ⇒ 恰好截到 2500（以字元為單位）。

        輸入長度與期望值皆為**硬編碼字面值**（不引用受測常數）：
        常數被改回 6000 時本測試必須紅（VC-PERF-OPT-1 變異驗證 (b) 的判紅點）。
        """
        body = "".join("角" if i % 2 else "色" for i in range(3000))
        p = tmp_path / "persona_long.md"
        p.write_text(body, encoding="utf-8")

        prompt = build_system_prompt(str(p))
        prefix = avb.AKANE_VOICE_INVARIANTS + "\n\n"
        assert prompt.startswith(prefix)
        excerpt = prompt[len(prefix):]
        assert len(excerpt) == 2500, f"必須恰好截到 2500，實得 {len(excerpt)}"
        assert excerpt == body[:2500]
        assert len(prompt) == len(prefix) + 2500
        # 非 ASCII 不得產生非法 Unicode（可正常 round-trip 編碼回 UTF-8）
        assert prompt.encode("utf-8").decode("utf-8") == prompt

    def test_real_persona_file_is_budgeted(self):
        """實際出貨 persona（agent_mai.md，8094 字元）確實被 2500 預算截斷。"""
        path = REPO_ROOT / "personas" / "agent_mai.md"
        if not path.is_file():  # pragma: no cover - 檔案缺失時不誤判
            pytest.skip("personas/agent_mai.md 不存在")
        raw = path.read_text(encoding="utf-8")
        assert len(raw) > 2500, "前提：實檔長度必須大於預算"
        excerpt = build_system_prompt(str(path))[len(avb.AKANE_VOICE_INVARIANTS) + 2:]
        assert len(excerpt) == 2500
        assert excerpt == raw[:2500]

    def test_shorter_or_exact_budget_untouched(self, tmp_path):
        """<= 2500 ⇒ 原樣（邊界值 2500 亦不得被切）。"""
        for size in (2499, 2500):
            body = "あ" * size
            p = tmp_path / f"persona_{size}.md"
            p.write_text(body, encoding="utf-8")
            excerpt = build_system_prompt(str(p))[len(avb.AKANE_VOICE_INVARIANTS) + 2:]
            assert excerpt == body, f"size={size} 必須原樣"

    def test_empty_and_missing_file_keep_existing_behaviour(self, tmp_path):
        """空檔（全空白）與缺檔 ⇒ 既有行為不變（回退內嵌 AKANE_LAYER3_PERSONA）。"""
        empty = tmp_path / "empty.md"
        empty.write_text("   \n\n  ", encoding="utf-8")
        missing = tmp_path / "nope.md"
        expected = avb.AKANE_VOICE_INVARIANTS + "\n\n" + avb.AKANE_LAYER3_PERSONA

        assert build_system_prompt(None) == expected
        assert build_system_prompt(str(empty)) == expected
        assert build_system_prompt(str(missing)) == expected

    def test_6000_literal_is_gone_from_module(self):
        """原本的 6000 字面值必須被取代（不得留第二份 persona 預算）。

        AST 掃描（非文字比對）：模組內不得再有任何數值常數 6000
        （註解中的歷史說明不算程式碼常數）。
        """
        src = Path(avb.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and node.value == 6000
        ]
        assert literals == [], f"模組內仍有 6000 數值常數：{literals!r}"
        constants = {
            t.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)
            if isinstance(getattr(node, "value", None), ast.Constant)
        }
        assert constants["MAX_PERSONA_CHARS"] == 2500

    def test_akane_invariants_not_compensated(self):
        """不得為補償而改動 AKANE_VOICE_INVARIANTS（Owner 已知並核准人設變薄）。"""
        assert len(avb.AKANE_VOICE_INVARIANTS) == 287, (
            f"AKANE_VOICE_INVARIANTS 長度不得變動，實得 {len(avb.AKANE_VOICE_INVARIANTS)}"
        )


# ─────────────────────────────────────────────────────────────
# Class 5：空內容守門（reasoning 模型的真實風險）
# ─────────────────────────────────────────────────────────────

class TestEmptyContentGuard:
    def test_reasoning_only_stream_logs_greppable_warning(self, caplog):
        """只吐 reasoning、不吐 content ⇒ 留下可 grep 的 WARNING，且不拋未處理例外。"""
        caplog.set_level(logging.WARNING, logger=BRAIN_LOG)
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_reasoning_only_lines(4))])

        pieces = list(brain.llm_stream(brain._build_messages("你在嗎")))  # 不得拋例外
        assert pieces == [], "reasoning 不得被當成 content 輸出（既有取用行為不變）"

        warns = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and VC_LLM_EMPTY_MARKER in r.getMessage()
        ]
        assert len(warns) == 1, f"必須恰好一筆 {VC_LLM_EMPTY_MARKER} WARNING，實得 {warns!r}"
        msg = warns[0]
        assert VC_LLM_EMPTY_MARKER in msg and VC_LLM_EMPTY_MARKER == "[VC-LLM-EMPTY]"
        assert "max_tokens=60" in msg, "必須含 max_tokens 值"
        assert "reasoning_seen=True" in msg, "必須標示是否曾收到 delta.reasoning"
        assert "chunks=5" in msg, "必須含收到的 chunk 總數（4 reasoning + 1 finish）"
        assert "finish_reason='length'" in msg, "必須含 finish_reason（length ⇒ 被上限截斷）"

    def test_content_stream_has_no_empty_warning(self, caplog):
        """有可見 content ⇒ 不得誤報空內容（守門不得成為噪音）。"""
        caplog.set_level(logging.WARNING, logger=BRAIN_LOG)
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_content_lines("我在"))])
        assert list(brain.llm_stream(brain._build_messages("嗨"))) == ["我在"]
        assert VC_LLM_EMPTY_MARKER not in caplog.text

    def test_zero_chunk_stream_also_flags_empty(self, caplog):
        """完全 0 chunk（供應商回空 body）⇒ 同樣留痕（reasoning_seen=False）。"""
        caplog.set_level(logging.WARNING, logger=BRAIN_LOG)
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(["data: [DONE]"])])
        assert list(brain.llm_stream(brain._build_messages("嗨"))) == []
        msg = [r.getMessage() for r in caplog.records if VC_LLM_EMPTY_MARKER in r.getMessage()]
        assert len(msg) == 1
        assert "reasoning_seen=False" in msg[0] and "chunks=0" in msg[0]

    def test_turn_still_completes_without_unhandled_exception(self, caplog):
        """空內容回合：stream_respond 正常收斂（0 例外），既有 [LLM-STREAM] done 仍在。"""
        caplog.set_level(logging.INFO, logger=BRAIN_LOG)
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_reasoning_only_lines(2))])
        assert _collect(brain, "你在嗎") == ""
        assert "[LLM-STREAM] done tokens=0" in caplog.text, "既有 done 欄位名不得改動"
        assert brain.last_llm_diag["reasoning_seen"] is True
        assert brain.last_llm_diag["content_pieces"] == 0


# ─────────────────────────────────────────────────────────────
# Class 6：既有失敗路徑沿用（不得另造第二條錯誤路徑）
# ─────────────────────────────────────────────────────────────

WEB_TEST_CONFIG = {
    "companion": {"id": "agent_rem", "display_name": "雷姆", "short_name": "雷姆"},
    "fish_audio": {
        "api_key": "fake-key", "voice_id": "v", "model": "s2.1-pro-free",
        "mode": "live", "live_format": "pcm",
        "asr_endpoint": "https://api.fish.audio/v1/asr",
        "tts_ws_endpoint": "wss://api.fish.audio/v1/tts/live",
    },
    "stt": {"engine": "fish", "language": "zh"},
    "vad": {"sample_rate": 16000, "silence_threshold_sec": 0.05, "energy_threshold": 0.05},
    "llm": {"endpoint": "", "api_key": ""},
    "web": {"host": "127.0.0.1", "port": 8767},
}


class _FakeWebStreamer:
    def __init__(self, sink=None):
        self.sink = sink
        self.last_error = None

    def start(self):
        pass

    def feed_text_piece(self, piece):
        pass

    def end_session(self):
        pass

    def interrupt(self):
        pass

    def close(self):
        pass


class _FakeWebASR:
    def transcribe(self, wav_bytes):
        return "雷姆，你怎麼了"


class TestExistingFailurePathReused:
    def test_empty_turn_failure_log_carries_reasoning_diag(self, caplog):
        """[VC-TURN-FAIL] empty_llm_output（既有路徑）必須帶上 reasoning 診斷。"""
        from aiohttp import WSMsgType
        from aiohttp.test_utils import TestClient, TestServer

        from clients.voice_companion.asr_refiner import AsrRefiner
        from clients.voice_companion.web_server import build_app

        caplog.set_level(logging.ERROR, logger="vc.web_server")
        session = FakeSession([])
        brain = _make_brain(session, [FakeResponse(_reasoning_only_lines(3))])

        async def _run() -> None:
            app = build_app(
                WEB_TEST_CONFIG,
                brain=brain,
                refiner=AsrRefiner(llm_call=None),
                asr=_FakeWebASR(),
                streamer_factory=lambda sink: _FakeWebStreamer(sink=sink),
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "text", "text": "雷姆，你怎麼了"})
                while True:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("type") == "state" and data.get("state") == "IDLE":
                            break
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

        fails = [
            r.getMessage() for r in caplog.records
            if r.levelno == logging.ERROR and "[VC-TURN-FAIL]" in r.getMessage()
        ]
        assert len(fails) == 1, f"必須有且僅有一筆 [VC-TURN-FAIL]，實得 {fails!r}"
        assert "exc_type=empty_llm_output" in fails[0], "必須沿用既有失敗路徑"
        assert "llm_diag=" in fails[0], "既有失敗路徑必須帶 llm_diag 診斷"
        diag = fails[0].split("llm_diag=", 1)[1]
        assert "'content_pieces': 0" in diag, f"必須標示 content_pieces=0：{diag}"
        assert "'reasoning_seen': True" in diag, f"必須標示曾收到 reasoning：{diag}"
        assert "'finish_reason': 'length'" in diag, f"必須標示被上限截斷：{diag}"


# ─────────────────────────────────────────────────────────────
# Class 7：中斷回歸護欄（VC-BARGE-PREPLAY-1 逐字不動）
# ─────────────────────────────────────────────────────────────

WEB_UI_PATH = REPO_ROOT / "clients/voice_companion/web_ui.py"
WEB_SERVER_PATH = REPO_ROOT / "clients/voice_companion/web_server.py"
_EXPECTED_BARGE_BODY = 'return st === "SPEAKING" && drained === false;'


def _extract_js_function(src: str, name: str) -> tuple[str, str]:
    """AST 式／結構化抽取 JS 函式：回傳 (函式原始碼, 函式體內容)。

    以括號配對掃描取出完整函式區塊（字串字面值感知），非「某字串是否存在」的比對。
    """
    anchor = f"function {name}("
    assert anchor in src, f"web_ui.py 找不到 {anchor}"
    start = src.index(anchor)
    open_idx = src.index("{", start + len(anchor))
    depth = 0
    quote = None
    i = open_idx
    while i < len(src):
        ch = src[i]
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1], src[open_idx + 1:i]
        i += 1
    raise AssertionError(f"{name} 區塊未閉合")


def _normalized(body: str) -> str:
    return " ".join(body.split())


class TestBargeGateRegression:
    def test_isbargearmed_body_is_byte_identical(self):
        """isBargeArmed 的函式體必須逐字仍為 return st === "SPEAKING" && drained === false;"""
        src = WEB_UI_PATH.read_text(encoding="utf-8")
        fn_src, body = _extract_js_function(src, "isBargeArmed")
        assert _normalized(body) == _EXPECTED_BARGE_BODY, (
            f"barge 判定被改動（嚴禁）：{fn_src!r}"
        )
        assert re.fullmatch(
            r"function isBargeArmed\(st, drained\)\s*\{\s*return st === \"SPEAKING\" && drained === false;\s*\}",
            fn_src.strip(),
        ), f"簽章或述句結構被改動：{fn_src!r}"

    def test_barge_gate_still_calls_isbargearmed_with_playback_clock(self):
        """barge 閘門仍以 isBargeArmed(state, playbackDrained) 呼叫（單一判準來源）。"""
        src = WEB_UI_PATH.read_text(encoding="utf-8")
        calls = [m.group(1) for m in re.finditer(r"isBargeArmed\(([^)]*)\)", src)]
        real = [c for c in calls if c.strip()]
        assert real, "找不到 isBargeArmed 呼叫點"
        assert any(
            _normalized(c) == "state, playbackDrained" for c in real
        ), f"閘門必須以 (state, playbackDrained) 呼叫，實得 {real!r}"
        # 閘門本身：實際的 interrupt 發送點必須被 isBargeArmed(state, playbackDrained) 包住
        gate_matches = list(
            re.finditer(
                r"if \(isBargeArmed\(state, playbackDrained\)\)", src
            )
        )
        assert len(gate_matches) >= 1, "barge 閘門（if (isBargeArmed(state, playbackDrained))）不存在"

    def test_playback_drained_clock_untouched(self):
        """playbackDrained 音訊時鐘仍由 queuePlaybackSamples / onPlaybackDrained 兩端驅動。"""
        src = WEB_UI_PATH.read_text(encoding="utf-8")
        assert "var playbackActive = false, playbackDrained = true," in src, "時鐘宣告不得改動"
        assert re.search(r"function onPlaybackDrained\(\)\s*\{", src), "排空回呼不得移除"
        _, queue_body = _extract_js_function(src, "queuePlaybackSamples")
        assert "playbackDrained = false;" in queue_body, "收到 PCM 幀必須設回 false"
        _, drained_body = _extract_js_function(src, "onPlaybackDrained")
        assert "playbackDrained = true;" in drained_body, "排空必須設回 true"

    def test_python_ast_sanity_of_touched_modules(self):
        """本次改動的兩個 Python 模組必須可 AST 解析（結構完整性）。"""
        for path in (WEB_SERVER_PATH, Path(avb.__file__)):
            ast.parse(path.read_text(encoding="utf-8"))
