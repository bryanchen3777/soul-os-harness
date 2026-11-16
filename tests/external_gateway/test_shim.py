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


# -- allowlist ---------------------------------------------------------------

def test_extra_top_level_key_400():
    body = _valid_body(extra_key=1)
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_request_error"


@pytest.mark.parametrize("banned", ["logit_bias", "function_call", "tools"])
def test_banned_extension_keys_400(banned):
    body = _valid_body()
    body[banned] = {} if banned != "function_call" else {"name": "f"}
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_request_error"


@pytest.mark.parametrize("role", ["developer", "function", "tool", "System", ""])
def test_bad_role_400(role):
    body = _valid_body(messages=[{"role": role, "content": "x"}])
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.code == "invalid_request_error"


def test_extra_message_key_400():
    body = _valid_body(messages=[{"role": "user", "content": "hi", "tool_calls": []}])
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400


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
        parse_chat_request(_valid_body(messages=[{"role": "developer", "content": "x"}]))
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


# -- DIAG-SHIM-KEY-001: 400 carries the offending key name (name only) ------

def test_unknown_top_level_key_message_carries_key_name():
    body = _valid_body(extra_key=1)
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.message == "Unsupported parameter: extra_key"


def test_unknown_top_level_key_survives_error_envelope_and_scrub():
    body = _valid_body(extra_key=1)
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    env = to_error(ei.value.status, ei.value.code, ei.value.message)
    assert env["error"]["message"] == "Unsupported parameter: extra_key"
    assert "extra_key" in env["error"]["message"]


def test_unknown_message_field_carries_field_name():
    body = _valid_body(messages=[{"role": "user", "content": "hi", "tool_calls": []}])
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.message == "Unsupported message field: tool_calls"


@pytest.mark.parametrize("banned", ["logit_bias", "function_call", "tools", "functions", "tool_choice"])
def test_banned_keys_do_not_leak_key_name(banned):
    body = _valid_body()
    body[banned] = {}
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(body)
    assert ei.value.status == 400
    assert ei.value.message == "Unsupported parameter."
    assert banned not in ei.value.message


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
