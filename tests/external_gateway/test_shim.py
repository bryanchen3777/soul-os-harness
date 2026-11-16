"""OpenAI shim tests. Pure offline: no server, no ports, no network, no LLM."""

import json
import sys
from pathlib import Path

import pytest

from src.external_gateway.openai_shim import InternalReq
from src.external_gateway.openai_shim import iter_chat_sse
from src.external_gateway.openai_shim import make_executor
from src.external_gateway.openai_shim import parse_chat_request
from src.external_gateway.openai_shim import to_chat_response
from src.external_gateway.openai_shim import to_chat_sse_chunks
from src.external_gateway.openai_shim import to_error
from src.interfaces.external_auth import RegistryProvider
from src.interfaces.external_auth import serve_external
from src.interfaces.schema import AdmissionDecision
from src.interfaces.schema import InterfaceError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "interfaces"))
from test_config import TOKEN_A  # noqa: E402
from test_config import make_registry_raw  # noqa: E402


def _valid_body(**over):
    body = {
        "model": "model-a",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ],
    }
    body.update(over)
    return body


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# -- transparent passthrough: extra top-level keys are never 400 -----------

def test_extra_top_level_key_passthrough_not_400():
    req = parse_chat_request(_valid_body(extra_key=1))
    assert isinstance(req, InternalReq)
    assert req.passthrough == {"extra_key": 1}
    assert req.to_raw_request()["extra_key"] == 1


@pytest.mark.parametrize("key", ["logit_bias", "function_call", "tools", "functions", "tool_choice"])
def test_former_banned_extension_keys_passthrough(key):
    body = _valid_body()
    body[key] = {} if key != "function_call" else {"name": "f"}
    req = parse_chat_request(body)
    assert isinstance(req, InternalReq)
    assert req.passthrough[key] == body[key]
    assert req.to_raw_request()[key] == body[key]


# -- TRANS-2: roles are open, message keys ride verbatim ---------------------

@pytest.mark.parametrize("role", ["developer", "function", "tool", "system", "assistant", "System", "custom-role", "x"])
def test_open_roles_admitted(role):
    req = parse_chat_request(_valid_body(messages=[{"role": role, "content": "x"}]))
    assert isinstance(req, InternalReq)
    assert req.messages[0].role == role
    assert req.to_raw_request()["messages"][0]["role"] == role


@pytest.mark.parametrize("role", ["", 123, None, ["user"]])
def test_bad_role_400(role):
    body = _valid_body(messages=[{"role": role, "content": "x"}])
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_request_error"


def test_extra_message_keys_ride_verbatim_in_extras():
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        "id": "msg-1",
    }
    req = parse_chat_request(_valid_body(messages=[msg]))
    assert isinstance(req, InternalReq)
    assert req.messages[0].content is None
    assert req.messages[0].extras == {
        "tool_calls": msg["tool_calls"],
        "id": "msg-1",
    }
    raw = req.to_raw_request()["messages"][0]
    assert raw == msg


def test_stream_true_accepted_s3_pseudo_streaming():
    req = parse_chat_request(_valid_body(stream=True))
    assert isinstance(req, InternalReq)
    assert req.stream is True
    # 上游仍走非流式：引擎 allowlist 只收 stream=False。
    assert req.to_raw_request()["stream"] is False


def test_stream_non_bool_400():
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(_valid_body(stream="false"))
    assert ei.value.status == 400


def test_stream_false_ok():
    req = parse_chat_request(_valid_body(stream=False))
    assert isinstance(req, InternalReq)
    assert req.stream is False


def test_valid_parse_defaults():
    req = parse_chat_request(_valid_body())
    assert req.model == "model-a"
    assert len(req.messages) == 2
    assert req.temperature == 0.5
    assert req.max_tokens == 512
    assert req.stop is None


# -- response shape ----------------------------------------------------------

def test_usage_all_zero():
    out = to_chat_response("hi", "model-a")
    assert out["object"] == "chat.completion"
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert out["id"].startswith("chatcmpl-")
    assert len(out["id"]) == len("chatcmpl-") + 32
    assert out["model"] == "model-a"
    choice = out["choices"][0]
    assert choice["index"] == 0
    assert choice["message"] == {"role": "assistant", "content": "hi"}
    assert choice["finish_reason"] == "stop"


def test_golden_success_response_byte_for_byte(monkeypatch):
    class _UUID:
        hex = "0" * 32

    monkeypatch.setattr("src.external_gateway.openai_shim.uuid.uuid4", lambda: _UUID())
    monkeypatch.setattr("src.external_gateway.openai_shim.time.time", lambda: 1700000000.0)
    out = to_chat_response("hello", "model-a")
    expected = {
        "id": "chatcmpl-" + "0" * 32,
        "object": "chat.completion",
        "created": 1700000000,
        "model": "model-a",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    assert _canon(out) == _canon(expected)
    assert isinstance(out["created"], int)


def test_golden_error_envelope_byte_for_byte():
    out = to_error(400, "invalid_request_error", "oops")
    expected = {"error": {"message": "oops", "type": "invalid_request_error", "code": "invalid_request_error"}}
    assert _canon(out) == _canon(expected)


# -- error mapping (one case per branch: deleting any branch turns it red) ---

@pytest.mark.parametrize(
    "status,expected_type",
    [
        (400, "invalid_request_error"),
        (401, "invalid_client"),
        (403, "permission_denied"),
        (429, "rate_limit_error"),
        (500, "server_error"),
        (503, "server_error"),
    ],
)
def test_error_type_mapping(status, expected_type):
    out = to_error(status, "some_code", "msg")
    assert out["error"]["type"] == expected_type
    assert out["error"]["code"] == "some_code"


def test_upstream_error_rewritten_fixed_copy():
    upstream = "operator failure at shard 7: connection reset by peer xyz-123"
    out = to_error(500, "backend_error", upstream)
    assert upstream not in out["error"]["message"]
    assert "xyz-123" not in out["error"]["message"]
    assert out["error"]["message"] == "Internal server error."
    assert out["error"]["type"] == "server_error"


def test_secret_scrubbed_from_message():
    out = to_error(400, "invalid_request_error", "rejected api_key=sk-abc123XYZ tail")
    assert "sk-abc123XYZ" not in out["error"]["message"]
    assert "[redacted]" in out["error"]["message"]


def test_interface_error_converts_to_envelope():
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(_valid_body(messages=[{"role": "", "content": "x"}]))
    env = to_error(ei.value.status, ei.value.code, ei.value.message)
    assert env["error"]["type"] == "invalid_request_error"


# -- executor shape: must match serve_external's callback --------------------

def test_executor_wires_through_serve_external():
    req = parse_chat_request({"model": "model-a", "messages": [{"role": "user", "content": "hi"}]})
    prov = RegistryProvider(make_registry_raw())
    seen = []

    def _generate(decision):
        seen.append(decision)
        return "hello from stub"

    headers = [("Authorization", "Bearer " + TOKEN_A)]
    out = serve_external(
        prov, headers, "soul-a", "backend-a", req.to_raw_request(), "req-shim-wire-1",
        make_executor("model-a", _generate),
    )
    assert len(seen) == 1
    assert isinstance(seen[0], AdmissionDecision)
    assert out["model"] == "model-a"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "hello from stub"}
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_make_executor_rejects_bad_args():
    with pytest.raises(TypeError):
        make_executor(123, lambda d: "x")
    with pytest.raises(TypeError):
        make_executor("model-a", "not-callable")


# -- S3 pseudo-streaming: SSE chunk shape ------------------------------------

def test_sse_chunks_share_id_created_model():
    role_chunk, content_chunk, stop_chunk = to_chat_sse_chunks("hello", "model-a")
    for chunk in (role_chunk, content_chunk, stop_chunk):
        assert chunk["object"] == "chat.completion.chunk"
        assert chunk["model"] == "model-a"
        assert chunk["id"].startswith("chatcmpl-")
        assert len(chunk["id"]) == len("chatcmpl-") + 32
        assert isinstance(chunk["created"], int)
    ids = {role_chunk["id"], content_chunk["id"], stop_chunk["id"]}
    createds = {role_chunk["created"], content_chunk["created"], stop_chunk["created"]}
    assert len(ids) == 1
    assert len(createds) == 1


def test_sse_chunks_role_content_stop_order():
    role_chunk, content_chunk, stop_chunk = to_chat_sse_chunks("full-text", "model-a")
    assert role_chunk["choices"] == [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]
    assert content_chunk["choices"] == [
        {"index": 0, "delta": {"content": "full-text"}, "finish_reason": None}
    ]
    assert stop_chunk["choices"] == [{"index": 0, "delta": {}, "finish_reason": "stop"}]


def test_sse_chunks_reject_bad_args():
    with pytest.raises(TypeError):
        to_chat_sse_chunks(123, "model-a")
    with pytest.raises(TypeError):
        to_chat_sse_chunks("hi", 123)


def test_iter_chat_sse_frames_end_with_done():
    frames = list(iter_chat_sse("hello", "model-a"))
    assert len(frames) == 4
    assert frames[-1] == b"data: [DONE]\n\n"
    payloads = [json.loads(f.decode("utf-8")[len("data: "):]) for f in frames[:-1]]
    assert payloads[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert payloads[1]["choices"][0]["delta"] == {"content": "hello"}
    assert payloads[1]["choices"][0]["finish_reason"] is None
    assert payloads[2]["choices"][0]["finish_reason"] == "stop"
    assert all(p["object"] == "chat.completion.chunk" for p in payloads)


# -- DIAG-SHIM-KEY-001: retired (transparent passthrough, no 400) -----------
# Unknown top-level keys are forwarded verbatim (see passthrough tests
# above). Only the message-shape diagnostics below still apply: a bad
# message field stays 400 and still names the field (name only, scrubbed
# through the error envelope).

def test_unknown_top_level_key_forwarded_verbatim():
    body = _valid_body(extra_key=1)
    req = parse_chat_request(body)
    assert req.passthrough == {"extra_key": 1}
    env = to_error(400, "invalid_request_error", "probe")
    assert env["error"]["message"] == "probe"


def test_unknown_top_level_key_survives_error_envelope_and_scrub():
    out = to_error(400, "invalid_request_error", "rejected api_key=sk-abc123XYZ tail")
    assert "sk-abc123XYZ" not in out["error"]["message"]
    assert "[redacted]" in out["error"]["message"]


def test_unknown_message_field_rides_verbatim_not_400():
    body = _valid_body(messages=[{"role": "user", "content": "hi", "tool_calls": [{"id": "call-1"}]}])
    req = parse_chat_request(body)
    assert isinstance(req, InternalReq)
    raw = req.to_raw_request()["messages"][0]
    assert raw["tool_calls"] == [{"id": "call-1"}]
    assert raw["content"] == "hi"


@pytest.mark.parametrize("key", ["logit_bias", "function_call", "tools", "functions", "tool_choice"])
def test_former_banned_keys_forwarded_verbatim(key):
    body = _valid_body()
    body[key] = {}
    req = parse_chat_request(body)
    assert req.passthrough[key] == {}
    assert req.to_raw_request()[key] == {}


def test_non_str_top_level_key_no_repr_leak():
    body = _valid_body()
    body[123] = 1
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.message == "Invalid request."


def test_non_str_message_field_key_no_repr_leak():
    body = _valid_body(messages=[{"role": "user", "content": "hi", 123: 1}])
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.message == "Invalid request."


# -- SHIM-OVERLAY-TRANS-1: stream_options forwarded via passthrough --------

def test_stream_options_include_usage_forwarded_not_dropped():
    req = parse_chat_request(_valid_body(stream=True, stream_options={"include_usage": True}))
    assert isinstance(req, InternalReq)
    assert req.stream is True
    raw = req.to_raw_request()
    assert raw["stream_options"] == {"include_usage": True}
    assert raw["stream"] is False
    frames = list(iter_chat_sse("hello", "model-a"))
    assert len(frames) == 4
    assert frames[-1] == b"data: [DONE]\n\n"
    payloads = [json.loads(f.decode("utf-8")[len("data: "):]) for f in frames[:-1]]
    assert all("usage" not in p for p in payloads)


def test_stream_options_none_and_empty_mapping_forwarded():
    for val in (None, {}):
        req = parse_chat_request(_valid_body(stream_options=val))
        assert isinstance(req, InternalReq)
        assert req.to_raw_request()["stream_options"] == val


@pytest.mark.parametrize("val", ["yes", 1, True, [], ["include_usage"]])
def test_stream_options_arbitrary_values_passthrough(val):
    req = parse_chat_request(_valid_body(stream_options=val))
    assert req.to_raw_request()["stream_options"] == val


def test_other_unknown_key_also_passthrough_with_stream_options():
    req = parse_chat_request(_valid_body(stream_options={"include_usage": True}, dummy_key=1))
    assert req.to_raw_request()["dummy_key"] == 1
    assert req.to_raw_request()["stream_options"] == {"include_usage": True}


# -- SHIM-OVERLAY-TRANS-1: store forwarded via passthrough ------------------

import pytest as _pytest_store  # noqa: E402  (local alias to avoid shadowing)


@_pytest_store.mark.parametrize("val", [True, False, None])
def test_store_forwarded_not_dropped(val):
    req = parse_chat_request(_valid_body(store=val))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["store"] is val


@_pytest_store.mark.parametrize("val", [1, 0, "true", [], {}])
def test_store_arbitrary_values_passthrough(val):
    req = parse_chat_request(_valid_body(store=val))
    assert req.to_raw_request()["store"] == val


def test_unknown_param_also_passthrough_with_store():
    req = parse_chat_request(_valid_body(store=True, unknown_param=1))
    assert req.to_raw_request()["store"] is True
    assert req.to_raw_request()["unknown_param"] == 1


def test_store_and_stream_options_coexist_in_raw():
    req = parse_chat_request(
        _valid_body(store=True, stream=True, stream_options={"include_usage": True})
    )
    assert isinstance(req, InternalReq)
    raw = req.to_raw_request()
    assert raw["store"] is True
    assert raw["stream_options"] == {"include_usage": True}
    assert raw["stream"] is False


# -- SHIM-OVERLAY-TRANS-1: max_completion_tokens forwarded verbatim ------

import pytest as _pytest_mct  # noqa: E402  (local alias to avoid shadowing)


@_pytest_mct.mark.parametrize("val", [16, 1, 2048])
def test_max_completion_tokens_forwarded_verbatim(val):
    req = parse_chat_request(_valid_body(max_completion_tokens=val))
    assert isinstance(req, InternalReq)
    assert req.max_tokens == 512
    raw = req.to_raw_request()
    assert raw["max_tokens"] == 512
    assert raw["max_completion_tokens"] == val


def test_max_completion_tokens_none_forwarded_verbatim():
    req = parse_chat_request(_valid_body(max_completion_tokens=None))
    assert isinstance(req, InternalReq)
    assert req.max_tokens == 512
    assert req.to_raw_request()["max_completion_tokens"] is None


@_pytest_mct.mark.parametrize("val", [0, 2049, -1, "100", 1.5, True, False])
def test_max_completion_tokens_arbitrary_values_passthrough(val):
    req = parse_chat_request(_valid_body(max_completion_tokens=val))
    assert req.to_raw_request()["max_completion_tokens"] == val


def test_max_tokens_and_max_completion_tokens_coexist_no_conflict():
    req = parse_chat_request(_valid_body(max_tokens=16, max_completion_tokens=32))
    assert isinstance(req, InternalReq)
    raw = req.to_raw_request()
    assert raw["max_tokens"] == 16
    assert raw["max_completion_tokens"] == 32


def test_store_stream_options_max_completion_tokens_coexist_in_raw():
    req = parse_chat_request(
        _valid_body(store=True, stream_options={"include_usage": True}, max_completion_tokens=16)
    )
    assert isinstance(req, InternalReq)
    raw = req.to_raw_request()
    assert raw["store"] is True
    assert raw["stream_options"] == {"include_usage": True}
    assert raw["max_completion_tokens"] == 16
    assert raw["max_tokens"] == 512
    assert raw["stream"] is False


def test_unknown_param_also_passthrough_with_max_completion_tokens():
    req = parse_chat_request(_valid_body(max_completion_tokens=16, unknown_param=1))
    assert req.to_raw_request()["unknown_param"] == 1
    assert req.to_raw_request()["max_completion_tokens"] == 16


# -- SHIM-OVERLAY-TRANS-1: tools / tool_choice / legacy keys forwarded -----
_TOOL_KEYS = ("tools", "tool_choice", "functions", "function_call", "logit_bias")


def test_empty_tools_forwarded_not_dropped():
    req = parse_chat_request(_valid_body(tools=[]))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tools"] == []


@pytest.mark.parametrize(
    "tools",
    [
        [{"type": "function", "function": {"name": "f"}}],
        "x",
        {},
        {"type": "function"},
        [{}],
        None,
        True,
    ],
)
def test_nonempty_or_nonlist_tools_forwarded(tools):
    req = parse_chat_request(_valid_body(tools=tools))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tools"] == tools


def test_tool_choice_none_forwarded_not_dropped():
    req = parse_chat_request(_valid_body(tool_choice="none"))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tool_choice"] == "none"


def test_empty_tools_plus_auto_forwarded():
    req = parse_chat_request(_valid_body(tools=[], tool_choice="auto"))
    assert isinstance(req, InternalReq)
    raw = req.to_raw_request()
    assert raw["tools"] == []
    assert raw["tool_choice"] == "auto"


def test_bare_auto_without_tools_forwarded():
    req = parse_chat_request(_valid_body(tool_choice="auto"))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tool_choice"] == "auto"


@pytest.mark.parametrize(
    "choice",
    ["required", {"type": "function", "function": {"name": "f"}}, "AUTO", "None", "", None, True, 0, []],
)
def test_arbitrary_tool_choice_forwarded(choice):
    req = parse_chat_request(_valid_body(tool_choice=choice))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tool_choice"] == choice


def test_nonempty_tools_plus_auto_forwarded_in_order():
    req = parse_chat_request(
        _valid_body(tools=[{"type": "function", "function": {"name": "f"}}], tool_choice="auto")
    )
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tools"] == [{"type": "function", "function": {"name": "f"}}]
    assert req.to_raw_request()["tool_choice"] == "auto"


def test_nonempty_tools_plus_auto_forwarded_reversed_order():
    body = {
        "model": "model-a",
        "messages": [{"role": "user", "content": "hi"}],
        "tool_choice": "auto",
        "tools": [{"type": "function", "function": {"name": "f"}}],
    }
    req = parse_chat_request(body)
    assert req.to_raw_request()["tool_choice"] == "auto"
    assert req.to_raw_request()["tools"] == [{"type": "function", "function": {"name": "f"}}]


def test_nonempty_tools_plus_none_forwarded():
    req = parse_chat_request(
        _valid_body(tools=[{"type": "function", "function": {"name": "f"}}], tool_choice="none")
    )
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["tools"] == [{"type": "function", "function": {"name": "f"}}]


@pytest.mark.parametrize("val", [{}, [], {"name": "f"}])
def test_functions_forwarded_even_empty(val):
    req = parse_chat_request(_valid_body(functions=val))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["functions"] == val


@pytest.mark.parametrize("val", [{"name": "f"}, "none", {}, [], None])
def test_function_call_forwarded(val):
    req = parse_chat_request(_valid_body(function_call=val))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["function_call"] == val


@pytest.mark.parametrize("val", [{}, {"x": 1}, "x", 0])
def test_logit_bias_forwarded(val):
    req = parse_chat_request(_valid_body(logit_bias=val))
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["logit_bias"] == val


def test_tools_coexist_in_raw():
    req = parse_chat_request(
        _valid_body(
            tools=[],
            tool_choice="none",
            store=True,
            stream=True,
            stream_options={"include_usage": True},
            max_completion_tokens=16,
        )
    )
    assert isinstance(req, InternalReq)
    assert req.max_tokens == 512
    raw = req.to_raw_request()
    assert raw["tools"] == []
    assert raw["tool_choice"] == "none"
    assert raw["store"] is True
    assert raw["stream_options"] == {"include_usage": True}
    assert raw["max_completion_tokens"] == 16
    assert raw["max_tokens"] == 512
    assert raw["stream"] is False
