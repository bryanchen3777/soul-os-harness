"""Static B7 subscriber census guard (no runtime imports or side effects).

The inventory is intentionally pinned to concrete subscription edges. A new
subscriber or changed event filter must update the census before EI-2B can
claim that every side-effect consumer has an explicit policy.
"""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _registration_edges(relative: str) -> list[tuple[str, frozenset[str]]]:
    source = _source(relative)
    tree = ast.parse(source, filename=relative)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    edges: list[tuple[str, frozenset[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "subscribe":
            continue

        subscriber_id = None
        for keyword in node.keywords:
            if keyword.arg == "subscriber_id":
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    subscriber_id = keyword.value.value
                elif isinstance(keyword.value, ast.Attribute):
                    subscriber_id = ast.unparse(keyword.value)
                break
        if subscriber_id is None:
            # Positional subscriber IDs are used by Gateway and ChannelRouter.
            if node.args and isinstance(node.args[0], ast.Constant):
                if isinstance(node.args[0].value, str):
                    subscriber_id = node.args[0].value
        if subscriber_id is None:
            continue

        scope: ast.AST = node
        while scope in parents and not isinstance(
            scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)
        ):
            scope = parents[scope]
        event_filter = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "event_filter"),
            None,
        )
        filter_nodes = [event_filter] if event_filter is not None else []
        if isinstance(event_filter, ast.Name):
            filter_nodes = [
                child.value
                for child in ast.walk(scope)
                if isinstance(child, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == event_filter.id for target in child.targets)
            ]
            filter_nodes.extend(
                child.args[0]
                for child in ast.walk(scope)
                if isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "add"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == event_filter.id
                and child.args
            )
        event_names = frozenset(
            child.attr
            for filter_node in filter_nodes
            for child in ast.walk(filter_node)
            if isinstance(child, ast.Attribute)
            and isinstance(child.value, ast.Name)
            and child.value.id == "EventType"
        )
        edges.append((subscriber_id, event_names))
    return edges


def _has_edge(relative: str, subscriber: str, required_events: set[str]) -> bool:
    return any(
        actual_id == subscriber and required_events <= actual_events
        for actual_id, actual_events in _registration_edges(relative)
    )


def test_b7_outbound_and_memory_subscribers_are_censused():
    expected = [
        ("src/io/gateway.py", "io_gateway", {"AGENT_SPEAK"}),
        ("src/io/gateway.py", "io_gateway_audio", {"AGENT_AUDIO_READY"}),
        ("src/io/channels/router.py", "channel_router", {"AGENT_SPEAK"}),
        ("src/io/channels/router.py", "channel_router_audio", {"AGENT_AUDIO_READY"}),
        ("src/llm/fish_tts_handler.py", "fish_tts_handler", {"AGENT_SPEAK"}),
        ("src/memory/middleware.py", "memory_middleware", {"USER_MESSAGE", "AGENT_SPEAK"}),
        ("src/heartbeat/engine.py", "heartbeat_silence_tracker", {"AGENT_SPEAK"}),
        ("src/conversation_qualification/qualifier.py", "self.SUBSCRIBER_ID", {"SESSION_END"}),
        (
            "scripts/run_server.py",
            "social_world_event_producer",
            {"AGENT_SPEAK"},
        ),
    ]
    missing = [
        f"{path}:{subscriber} needs {sorted(events)}"
        for path, subscriber, events in expected
        if not _has_edge(path, subscriber, events)
    ]
    assert not missing, "B7 subscriber census changed; review and update policy: " + "; ".join(missing)


def test_b7_per_agent_and_activity_subscribers_are_censused():
    assert _has_edge(
        "src/agent/consciousness.py",
        "self.agent_id",
        {"USER_MESSAGE", "AGENT_SPEAK", "SESSION_END"},
    )
    assert _has_edge(
        "src/heartbeat/engine.py",
        "heartbeat_activity_tracker",
        {"USER_MESSAGE"},
    )


def test_b7_token_release_is_censused_and_proxy_is_not_a_speak_subscriber():
    token_edges = _registration_edges("src/eventbus/token_manager.py")
    assert any(
        subscriber == "speaker_token_manager" and "AGENT_SPEAK" in events
        for subscriber, events in token_edges
    )
    assert "AGENT_SPEAK" not in _source("src/llm/proxy.py").split("def register", 1)[1].split("def unregister", 1)[0]
    assert "SPEAKER_TOKEN_GRANTED" in _source("src/llm/proxy.py").split("def register", 1)[1].split("def unregister", 1)[0]


def test_b7_census_guards_never_import_runtime_modules():
    """Keep this guard AST/text-only so auditing subscriptions cannot wire runtime."""
    tree = ast.parse(_source(__file__.replace(str(ROOT) + "\\", "").replace("\\", "/")))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert imported <= {"ast", "pathlib"}
