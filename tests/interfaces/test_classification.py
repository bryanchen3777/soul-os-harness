"""Classification tests: quotas, types, roles, history."""

import pytest

from src.interfaces.classification import classify_messages
from src.interfaces.classification import parse_request
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import Source

try:
    from tests.interfaces.test_config import make_dual_registry
except ImportError:
    from test_config import make_dual_registry  # type: ignore


def _route():
    return make_dual_registry().routes[0]


def _req(model="model-a", messages=None, **kw):
    if messages is None:
        messages = [{"role": "user", "content": "hello"}]
    raw = {"model": model, "messages": messages}
    raw.update(kw)
    return raw


def test_parse_minimal_ok_and_immutable():
    req = parse_request(_req(), _route())
    assert req.model == "model-a"
    assert isinstance(req.messages, tuple) and len(req.messages) == 1
    assert req.stream is False and req.temperature == 0.5 and req.max_tokens == 512 and req.n == 1
    with pytest.raises(Exception):
        req.model = "x"


def test_reject_non_mapping_and_bad_messages_container():
    with pytest.raises(InterfaceError):
        parse_request("nope", _route())
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages="hello"), _route())
    assert ei.value.status == 400
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[]), _route())
    assert ei.value.status == 400
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x"} for _ in range(129)]), _route())
    assert ei.value.status == 413


def test_reject_model_mismatch_and_bad_model():
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(model="other-model"), _route())
    assert ei.value.status == 400 and ei.value.code == "model_mismatch"
    with pytest.raises(InterfaceError):
        parse_request(_req(model=""), _route())
    with pytest.raises(InterfaceError):
        parse_request(_req(model="a/b"), _route())


@pytest.mark.parametrize("field", ["tools", "response_format", "stop", "logprobs", "store", "user", "metadata", "max_completion_tokens", "top_p"])
def test_reject_unknown_top_params(field):
    raw = _req()
    raw[field] = 1
    with pytest.raises(InterfaceError) as ei:
        parse_request(raw, _route())
    assert ei.value.status == 400 and ei.value.code == "unsupported_parameter"
    assert ei.value.param == "request"


def test_reject_unknown_message_field():
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "hi", "tool_calls": []}]), _route())
    assert ei.value.code == "unsupported_parameter"
    assert ei.value.param == "messages"


def test_unknown_request_key_no_secret_echo():
    secret = "sk-live-SECRET-REQ-999"
    raw = _req()
    raw[secret] = 1
    with pytest.raises(InterfaceError) as ei:
        parse_request(raw, _route())
    assert ei.value.code == "unsupported_parameter"
    assert ei.value.param == "request"
    assert secret not in str(ei.value.message)
    assert secret not in repr(ei.value)
    assert secret not in str(ei.value.param)
    raw2 = _req(messages=[{"role": "user", "content": "hi", secret: 1}])
    with pytest.raises(InterfaceError) as ei2:
        parse_request(raw2, _route())
    assert ei2.value.param == "messages"
    assert secret not in str(ei2.value.message)
    assert secret not in repr(ei2.value)


def test_stream_rules():
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(stream=True), _route())
    assert ei.value.status == 400 and ei.value.code == "unsupported_feature"
    for bad in [1, 0, "false", None]:
        with pytest.raises(InterfaceError) as ei:
            parse_request(_req(stream=bad), _route())
        assert ei.value.status == 400


def test_n_rules_bool_rejected():
    with pytest.raises(InterfaceError):
        parse_request(_req(n=2), _route())
    for bad in [True, False, 0, "1", 1.0, None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(n=bad), _route())


def test_temperature_rules():
    for bad in [True, False, float("nan"), float("inf"), -0.5, 2.5, "0.5", None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(temperature=bad), _route())
    ok = parse_request(_req(temperature=1), _route())
    assert ok.temperature == 1.0


def test_max_tokens_rules():
    for bad in [True, False, 0, 2049, 1.5, "512", None]:
        with pytest.raises(InterfaceError):
            parse_request(_req(max_tokens=bad), _route())


def test_unsupported_role_and_missing_current():
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "tool", "content": "x"}, {"role": "user", "content": "hi"}]), _route())
    assert ei.value.code == "unsupported_role"
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]), _route())
    assert ei.value.code == "missing_current_user"


def test_empty_history_ok_blank_current_rejected():
    req = parse_request(_req(messages=[{"role": "user", "content": ""}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "go"}]), _route())
    assert len(req.messages) == 3
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": "   "}]), _route())


def test_name_limit_and_type():
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": "hi", "name": "n" * 65}]), _route())
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": "hi", "name": 123}]), _route())
    req = parse_request(_req(messages=[{"role": "user", "content": "hi", "name": "display"}]), _route())
    assert req.messages[0].name == "display"


def test_reject_non_json_nan_surrogate_bytes():
    with pytest.raises(InterfaceError):
        parse_request(_req(temperature=float("nan")), _route())
    raw = _req()
    raw["messages"] = [{"role": "user", "content": "hi"}]
    raw["weird"] = {1, 2}
    with pytest.raises(InterfaceError):
        parse_request(raw, _route())
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": "a\ud800b"}]), _route())
    with pytest.raises(InterfaceError):
        parse_request(_req(messages=[{"role": "user", "content": b"bytes"}]), _route())


def test_byte_limits():
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "x" * 32769}]), _route())
    assert ei.value.status == 413
    with pytest.raises(InterfaceError) as ei:
        parse_request(_req(messages=[{"role": "user", "content": "y" * 30000} for _ in range(5)]), _route())
    assert ei.value.status == 413


def test_classify_sources_all_false():
    req = parse_request(_req(messages=[{"role": "system", "content": "sys"}, {"role": "developer", "content": "dev"}, {"role": "user", "content": "h1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "now"}]), _route())
    segs = classify_messages(req)
    assert isinstance(segs, tuple)
    assert segs[0].source == Source.EXTERNAL_INSTRUCTION
    assert segs[1].source == Source.EXTERNAL_INSTRUCTION
    assert segs[2].source == Source.EXTERNAL_HISTORY
    assert segs[3].source == Source.EXTERNAL_HISTORY
    assert segs[4].source == Source.CURRENT_USER
    assert all(s.memory_candidate is False for s in segs)
