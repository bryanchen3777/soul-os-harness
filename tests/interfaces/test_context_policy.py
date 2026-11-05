"""Context policy tests: memory states, headers, fingerprint, namespace."""

import pytest

from src.interfaces.context_policy import admit
from src.interfaces.context_policy import idempotency_namespace
from src.interfaces.context_policy import request_fingerprint
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import MemoryPolicy

try:
    from tests.interfaces.test_config import TOKEN_A
    from tests.interfaces.test_config import TOKEN_B
    from tests.interfaces.test_config import TOKEN_U
    from tests.interfaces.test_config import make_client_dict
    from tests.interfaces.test_config import make_dual_registry
    from tests.interfaces.test_config import make_registry_raw
except ImportError:
    from test_config import TOKEN_A  # type: ignore
    from test_config import TOKEN_B  # type: ignore
    from test_config import TOKEN_U  # type: ignore
    from test_config import make_client_dict  # type: ignore
    from test_config import make_dual_registry  # type: ignore
    from test_config import make_registry_raw  # type: ignore

from src.interfaces.config import validate_registry


def _body(text="hello world"):
    return {"model": "model-a", "messages": [{"role": "user", "content": text}]}


def _full_headers(conv="conv-01", branch="branch-01", turn="turn-01", key="key-01"):
    return {"X-Soul-Conversation-Id": conv, "X-Soul-Branch-Id": branch, "X-Soul-Turn-Id": turn, "Idempotency-Key": key}


def test_interactive_candidate_last_only():
    reg = make_dual_registry()
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-01")
    assert d.memory_policy == MemoryPolicy.INTERACTIVE_CANDIDATE
    assert d.segments[-1].memory_candidate is True
    assert all(s.memory_candidate is False for s in d.segments[:-1])
    assert d.current_user_text == "hello world"
    assert d.generation_limits.temperature == 0.5 and d.generation_limits.max_tokens == 512
    assert d.generation_limits.context_window_tokens == 4096
    assert d.generation_limits.provider_attempts == 1 and d.generation_limits.tool_rounds == 0
    assert d.context.branch_id == "branch-01"


def test_utility_all_false():
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility")])
    reg = validate_registry(raw)
    d = admit(reg, TOKEN_U, "soul-a", "backend-a", _body(), _full_headers(), "req-u-01")
    assert d.memory_policy == MemoryPolicy.UTILITY_NO_MEMORY
    assert all(s.memory_candidate is False for s in d.segments)


def test_ephemeral_missing_any_field():
    reg = make_dual_registry()
    for h in [{}, {"X-Soul-Conversation-Id": "c"}, {"X-Soul-Turn-Id": "t"}, {"Idempotency-Key": "k"}]:
        d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-e-01")
        assert d.memory_policy == MemoryPolicy.EPHEMERAL
        assert all(s.memory_candidate is False for s in d.segments)


def test_ephemeral_when_allow_mem_false():
    raw = make_registry_raw(clients=[make_client_dict("client-nw", TOKEN_A, purpose="interactive", allow_mem=False)])
    reg = validate_registry(raw)
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-nw")
    assert d.memory_policy == MemoryPolicy.EPHEMERAL
    assert all(s.memory_candidate is False for s in d.segments)


def test_branch_defaults_root():
    reg = make_dual_registry()
    h = {"X-Soul-Conversation-Id": "c-01", "X-Soul-Turn-Id": "t-01", "Idempotency-Key": "k-01"}
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-b")
    assert d.context.branch_id == "root"


def test_headers_case_insensitive_and_ignored_others():
    reg = make_dual_registry()
    h = {"x-soul-conversation-id": "c-01", "X-SOUL-BRANCH-ID": "b-01", "x-soul-turn-id": "t-01", "IDEMPOTENCY-KEY": "k-01", "X-Trace": "yes", "Content-Type": "application/json"}
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), h, "req-ci")
    assert d.context.conversation_id == "c-01"


def test_duplicate_header_different_case_400():
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Turn-Id": "a", "x-soul-turn-id": "b"}, "req-d")
    assert ei.value.status == 400
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"Content-Type": "a", "content-type": "b"}, "req-d2")
    assert ei.value.status == 400


def test_unknown_xsoul_unsupported_parameter():
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Converstion-Id": "c"}, "req-u")
    assert ei.value.status == 400 and ei.value.code == "unsupported_parameter"
    assert ei.value.param == "headers"


def test_unknown_header_name_no_secret_echo():
    secret = "X-Soul-SUPER-SECRET-KEY-XYZ"
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {secret: "v"}, "req-s")
    assert ei.value.code == "unsupported_parameter"
    assert ei.value.param == "headers"
    assert secret not in str(ei.value.message)
    assert secret not in repr(ei.value)
    assert secret not in str(ei.value.param)


def test_bad_header_ids_rejected():
    reg = make_dual_registry()
    for bad in ["a/b", ".", "..", "", "x" * 129]:
        with pytest.raises(InterfaceError):
            admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {"X-Soul-Conversation-Id": bad}, "req-x")


def test_request_id_rules_no_generation():
    reg = make_dual_registry()
    for bad in ["", "   ", "\t\n", "x" * 129, None, 123, "ab\x00cd", "ab\x1fcd", "a\ud800b"]:
        with pytest.raises(InterfaceError) as ei:
            admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), {}, bad)
        assert ei.value.status == 400


def test_auth_first_unknown_key_always_401():
    reg = make_dual_registry()
    bad_token = "bad-key-nope"
    with pytest.raises(InterfaceError) as ei:
        admit(reg, bad_token, "soul-a", "backend-a", _body(), {}, "")
    assert ei.value.status == 401
    with pytest.raises(InterfaceError) as ei:
        admit(reg, bad_token, "soul-a", "backend-a", _body(), {}, "x" * 129)
    assert ei.value.status == 401
    with pytest.raises(InterfaceError) as ei:
        admit(reg, bad_token, "no-soul", "no-backend", _body(), {}, "req-ok")
    assert ei.value.status == 401
    with pytest.raises(InterfaceError) as ei:
        admit(reg, bad_token, "soul-a", "backend-a", {"model": "model-a", "messages": "bad"}, {}, "req-ok")
    assert ei.value.status == 401
    with pytest.raises(InterfaceError) as ei:
        admit(reg, bad_token, "soul-a", "backend-a", _body(), {"X-Soul-Bad": "1"}, "req-ok")
    assert ei.value.status == 401


def test_dual_client_same_namespace_fingerprint():
    reg = make_dual_registry()
    h = _full_headers()
    d1 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-same")
    d2 = admit(reg, TOKEN_B, "soul-a", "backend-a", _body("same text"), h, "req-diff-id")
    assert idempotency_namespace(d1.context) == idempotency_namespace(d2.context)
    assert request_fingerprint(d1) == request_fingerprint(d2)
    assert "client-a" not in request_fingerprint(d1) and TOKEN_A not in request_fingerprint(d1)


def test_fingerprint_changes_and_request_id_excluded():
    from dataclasses import replace
    from src.interfaces.schema import Purpose
    from src.interfaces.schema import Scope
    reg = make_dual_registry()
    h = _full_headers()
    base = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-1")
    other_text = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("other text"), h, "req-1")
    assert request_fingerprint(base) != request_fingerprint(other_text)
    same_other_req = admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-2")
    assert request_fingerprint(base) == request_fingerprint(same_other_req)
    turn2 = dict(h)
    turn2["X-Soul-Turn-Id"] = "turn-02"
    assert request_fingerprint(base) != request_fingerprint(admit(reg, TOKEN_A, "soul-a", "backend-a", _body("same text"), turn2, "req-1"))
    raw2 = make_registry_raw(routes=[{"soul_alias": "soul-a", "backend_alias": "backend-a", "agent_id": "agent-01", "public_model_id": "model-a", "backend_ref": "port-a", "context_window_tokens": 100}, {"soul_alias": "soul-b", "backend_alias": "backend-a", "agent_id": "agent-01", "public_model_id": "model-a", "backend_ref": "port-a", "context_window_tokens": 100}], clients=[make_client_dict("client-a", TOKEN_A, allowed_routes=["soul-a/backend-a", "soul-b/backend-a"])])
    reg2 = validate_registry(raw2)
    d_other_route = admit(reg2, TOKEN_A, "soul-b", "backend-a", _body("same text"), h, "req-1")
    assert request_fingerprint(base) != request_fingerprint(d_other_route)
    only_model_route = replace(base.context.route, public_model_id="model-b")
    only_model = replace(base, context=replace(base.context, route=only_model_route))
    assert request_fingerprint(base) != request_fingerprint(only_model), "public_model_id alone must change fingerprint"
    cases = []
    cases.append(("backend_ref", replace(base, context=replace(base.context, route=replace(base.context.route, backend_ref="port-b")))))
    cases.append(("agent_id", replace(base, context=replace(base.context, route=replace(base.context.route, agent_id="agent-02")))))
    cases.append(("scope", replace(base, context=replace(base.context, profile=replace(base.context.profile, scope=Scope.NONE)))))
    cases.append(("purpose", replace(base, context=replace(base.context, profile=replace(base.context.profile, purpose=Purpose.UTILITY)))))
    cases.append(("conversation_id", replace(base, context=replace(base.context, conversation_id="conv-02"))))
    cases.append(("branch_id", replace(base, context=replace(base.context, branch_id="branch-02"))))
    cases.append(("max_tokens", replace(base, generation_limits=replace(base.generation_limits, max_tokens=base.generation_limits.max_tokens + 1))))
    cases.append(("temperature", replace(base, generation_limits=replace(base.generation_limits, temperature=0.1 if base.generation_limits.temperature != 0.1 else 0.2))))
    last = base.segments[-1]
    renamed = replace(last, name="note-01" if last.name is None else None)
    cases.append(("segment.name", replace(base, segments=tuple(list(base.segments[:-1]) + [renamed]))))
    for label, variant in cases:
        assert request_fingerprint(base) != request_fingerprint(variant), label + " alone must change fingerprint"


def test_fingerprint_includes_policy_and_candidate():
    from dataclasses import replace
    raw_w = make_registry_raw(clients=[make_client_dict("client-w", TOKEN_A, purpose="interactive", allow_mem=True)])
    reg_w = validate_registry(raw_w)
    raw_nw = make_registry_raw(clients=[make_client_dict("client-nw", TOKEN_A, purpose="interactive", allow_mem=False)])
    reg_nw = validate_registry(raw_nw)
    h = _full_headers()
    d_w = admit(reg_w, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-fp")
    d_nw = admit(reg_nw, TOKEN_A, "soul-a", "backend-a", _body("same text"), h, "req-fp")
    assert d_w.memory_policy != d_nw.memory_policy
    assert request_fingerprint(d_w) != request_fingerprint(d_nw)
    policy_only = replace(d_w, memory_policy=d_nw.memory_policy)
    assert request_fingerprint(d_w) != request_fingerprint(policy_only), "memory_policy alone must change fingerprint"
    flipped = replace(d_w.segments[-1], memory_candidate=not d_w.segments[-1].memory_candidate)
    candidate_only = replace(d_w, segments=tuple(list(d_w.segments[:-1]) + [flipped]))
    assert request_fingerprint(d_w) != request_fingerprint(candidate_only), "memory_candidate alone must change fingerprint"


def test_admit_immutable_and_stateless():
    reg = make_dual_registry()
    d = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-im")
    with pytest.raises(Exception):
        d.current_user_text = "x"
    assert isinstance(d.segments, tuple)
    d2 = admit(reg, TOKEN_A, "soul-a", "backend-a", _body(), _full_headers(), "req-im")
    assert request_fingerprint(d) == request_fingerprint(d2)
