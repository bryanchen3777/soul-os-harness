"""tests/observability/test_prompt_trace.py — OBS-CHRONO-1 守衛

本檔的核心價值不是「trace 有寫」，而是**它寫的東西裡永遠不含對話原文**。
若哪天有人為了除錯而把原文寫進 trace，`test_sentinel_*` 會立刻轉紅。
"""

from __future__ import annotations

import json

import pytest

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.llm.proxy import LLMBackend, LLMProxy
from src.observability import prompt_trace


SENTINEL = "SOULTRACE-SENTINEL-9f3a2b1c-do-not-log-me"


@pytest.fixture(autouse=True)
def _reset_force_override():
    prompt_trace.set_trace_enabled(None)
    yield
    prompt_trace.set_trace_enabled(None)


@pytest.fixture(autouse=True)
def _isolated_trace(monkeypatch, tmp_path):
    """把 trace 檔導到 tmp，避免測試寫進任何生產資料。"""
    monkeypatch.setenv(prompt_trace.ENV_FLAG, "1")
    target = tmp_path / "observability" / prompt_trace.TRACE_FILENAME
    monkeypatch.setattr(
        prompt_trace, "trace_path", lambda: target, raising=True
    )
    return target


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _messages(system: str, user: str):
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


CHRONO_BLOCK = (
    "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
    "time_period=afternoon\n"
    "silence=3.2h\n"
    "arrival_deviation=none\n"
    "reaction_bias=steady\n"
    "[/CHRONO_SOCIAL_CONTEXT]\n"
)
INNER_LIFE_BLOCK = "[最近內在生活] 以下是你最近的日記/夢境/事件摘要。\n## 你的最近內在生活\n……\n"
SAGE_BLOCK = "你記得以下這些事情:\n- 使用者喜歡深夜寫程式\n"


# ─────────────────────────────────────────────────────────────
# 1. 開關語意
# ─────────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(prompt_trace.ENV_FLAG, raising=False)
        assert prompt_trace.trace_enabled() is False

    def test_enabled_only_by_truthy_values(self, monkeypatch):
        for value in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv(prompt_trace.ENV_FLAG, value)
            assert prompt_trace.trace_enabled() is True, value
        for value in ("0", "", "false", "no", "off", "2"):
            monkeypatch.setenv(prompt_trace.ENV_FLAG, value)
            assert prompt_trace.trace_enabled() is False, value

    def test_disabled_writes_nothing(self, monkeypatch, _isolated_trace):
        """🔴 生產預設路徑：關閉時連檔案都不該被建立（零 IO）。"""
        prompt_trace.set_trace_enabled(False)
        prompt_trace.trace_prompt(
            _messages(CHRONO_BLOCK, SENTINEL), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        assert not _isolated_trace.exists(), "關閉時不得建立任何 trace 檔"


# ─────────────────────────────────────────────────────────────
# 2. 防敏感對話外洩（本機制存在的核心理由）
# ─────────────────────────────────────────────────────────────


class TestNoSensitiveLeak:
    def test_sentinel_user_text_never_hits_disk(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages("你是一個角色。", SENTINEL),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        raw = _isolated_trace.read_text(encoding="utf-8")
        assert SENTINEL not in raw, "使用者原話絕不可落盤"

    def test_sentinel_inside_blocks_never_hits_disk(self, _isolated_trace):
        """block 內部就算塞了敏感內容，也只能留下長度與 hash。"""
        poisoned = CHRONO_BLOCK + INNER_LIFE_BLOCK + SAGE_BLOCK + SENTINEL
        prompt_trace.trace_prompt(
            _messages(poisoned, "早安"), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        raw = _isolated_trace.read_text(encoding="utf-8")
        assert SENTINEL not in raw, "system prompt 內容絕不可落盤"

    def test_row_carries_no_plaintext_fields(self, _isolated_trace):
        """列出的欄位必須全是中繼資料；不得出現任何自由文字欄位。"""
        prompt_trace.trace_prompt(
            _messages(CHRONO_BLOCK + SENTINEL, SENTINEL),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        row = _rows(_isolated_trace)[0]
        for key in ("system_prompt", "user_prompt", "content", "text", "messages"):
            assert key not in row, f"trace 列不得含有原文欄位 {key!r}"

    def test_original_is_still_identifiable_by_hash(self, _isolated_trace):
        """hash 的用途是「同一份內容可比對」，不是還原原文。"""
        system = CHRONO_BLOCK
        prompt_trace.trace_prompt(
            _messages(system, "早安"), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        row = _rows(_isolated_trace)[0]
        assert row["system_prompt_sha256_12"] == prompt_trace._sha12(system)
        assert len(row["system_prompt_sha256_12"]) == 12


# ─────────────────────────────────────────────────────────────
# 3. 三個觀測目標
# ─────────────────────────────────────────────────────────────


class TestObservabilityGoals:
    def test_route_and_temperature_attribution(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages("x", "y"), agent_id="agent_rem",
            route="roleplay:user_message", temperature=0.85,
        )
        prompt_trace.trace_prompt(
            _messages("x", "y"), agent_id="agent_rem",
            route="event", temperature=0.7,
        )
        rows = _rows(_isolated_trace)
        assert [(r["route"], r["temperature"]) for r in rows] == [
            ("roleplay:user_message", 0.85),
            ("event", 0.7),
        ], "目標①：每次呼叫的 route ＋ temperature 必須可歸因"

    def test_chrono_block_detected_with_metadata(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages(CHRONO_BLOCK, "早安"), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        chrono = _rows(_isolated_trace)[0]["blocks"]["chrono"]
        assert chrono["present"] is True
        assert chrono["time_period"] == "afternoon"
        assert chrono["silence_hours"] == pytest.approx(3.2)

    def test_absent_blocks_report_false(self, _isolated_trace):
        """缺失必須誠實報 false——不能因為「沒有」就假裝有。"""
        prompt_trace.trace_prompt(
            _messages("你是一個角色。", "早安"), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        blocks = _rows(_isolated_trace)[0]["blocks"]
        for name in ("chrono", "inner_life", "sage_memory"):
            assert blocks[name]["present"] is False, name
            assert blocks[name]["len"] == 0

    def test_inner_life_and_sage_detected(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages(INNER_LIFE_BLOCK + SAGE_BLOCK, "早安"),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        blocks = _rows(_isolated_trace)[0]["blocks"]
        assert blocks["inner_life"]["present"] is True
        assert blocks["sage_memory"]["present"] is True
        assert blocks["inner_life"]["len"] > 0


# ─────────────────────────────────────────────────────────────
# 3b. OBS-CHRONO-1.1：block_order 與 provider（補第一版漏掉的兩個欄位）
# ─────────────────────────────────────────────────────────────


class TestBlockOrder:
    """block_order 必須反映**實際 offset 順序**，不是宣告順序。"""

    def test_order_follows_text_offsets(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages(CHRONO_BLOCK + INNER_LIFE_BLOCK + SAGE_BLOCK, "早安"),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        assert _rows(_isolated_trace)[0]["block_order"] == ["chrono", "inner_life", "sage_memory"]

    def test_order_changes_when_prompt_order_changes(self, _isolated_trace):
        """🔴 順序守衛：同一批區塊、不同組裝順序 ⇒ 必須報出不同順序。

        這個反向測試才是真的在測「順序有被量到」；只測一個固定順序的話，
        把 block_order 寫成常數也能全綠。
        """
        prompt_trace.trace_prompt(
            _messages(SAGE_BLOCK + CHRONO_BLOCK + INNER_LIFE_BLOCK, "早安"),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        assert _rows(_isolated_trace)[0]["block_order"] == ["sage_memory", "chrono", "inner_life"]

    def test_absent_blocks_are_not_listed(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages("你是一個角色。", "早安"), agent_id="agent_rem",
            route="roleplay", temperature=0.85,
        )
        assert _rows(_isolated_trace)[0]["block_order"] == []

    def test_partial_blocks_only_lists_present_ones(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages(INNER_LIFE_BLOCK + CHRONO_BLOCK, "早安"),
            agent_id="agent_rem", route="roleplay", temperature=0.85,
        )
        # inner_life 在文字中較早出現 ⇒ 順序必須反映實際 offset
        assert _rows(_isolated_trace)[0]["block_order"] == ["inner_life", "chrono"]


class TestProviderId:
    class _WithUrl:
        BASE_URL = "https://api.openai.com/v1/chat/completions"

        def __init__(self, base_url=None):
            self.base_url = base_url or self.BASE_URL

    class _NoUrl:
        pass

    def test_class_and_host_are_captured(self):
        got = prompt_trace.provider_id(self._WithUrl())
        assert got == "_WithUrl@api.openai.com"

    def test_never_none_for_a_real_backend(self):
        """死欄位防護：沒有 base_url 也必須給得出類別名。"""
        got = prompt_trace.provider_id(self._NoUrl())
        assert got == "_NoUrl", f"provider 不得為 None，實得 {got!r}"
        assert got is not None

    def test_credentials_are_stripped(self):
        """🔴 base_url 若含 userinfo / query，不得整條寫進 trace。"""
        got = prompt_trace.provider_id(
            self._WithUrl("https://user:SECRET-TOKEN@internal.example.com:8443/v1?key=ALSO-SECRET")
        )
        assert "SECRET" not in got
        assert got == "_WithUrl@internal.example.com:8443"

    def test_ollama_style_local_endpoint(self):
        assert prompt_trace.provider_id(
            self._WithUrl("http://localhost:11434/v1/chat/completions")
        ).endswith("@localhost:11434")

    def test_none_passthrough(self):
        assert prompt_trace.provider_id(None) is None


@pytest.mark.asyncio
class TestProxyHookProvider:
    async def test_hook_records_real_provider_not_none(self, spy_backend, _isolated_trace):
        """🔴 插針層：provider 必須是有效值，而不是上一版的恆 None。"""
        bus = SoulEventBus()
        proxy = LLMProxy(bus=bus, backend=spy_backend, model="mock-model")
        await bus.start()
        try:
            await proxy.generate_event_text(
                messages=[{"role": "system", "content": "世界觀敘事者"}],
                agent_id="agent_akane",
            )
        finally:
            await bus.stop()

        row = _rows(_isolated_trace)[-1]
        assert row["provider"] is not None, "provider 不得為 None（第一版的死欄位）"
        assert "SpyBackend" in row["provider"]

    async def test_hook_records_block_order(self, spy_backend, _isolated_trace):
        bus = SoulEventBus()
        proxy = LLMProxy(bus=bus, backend=spy_backend, model="mock-model")
        await bus.start()
        try:
            event = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="agent_akane", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={
                    "agent_id": "agent_akane", "reason": "heartbeat",
                    "draft": "……在喔。", "mood": 0.0, "mode": "group",
                    "target_user_id": "bryan", "memory_context": "",
                },
                session_id="session_obs_chrono_11",
            )
            await proxy._handle_event_impl(event)
        finally:
            await bus.stop()

        row = _rows(_isolated_trace)[-1]
        assert "block_order" in row, "插針層必須寫出 block_order 欄位"
        assert isinstance(row["block_order"], list)


# ─────────────────────────────────────────────────────────────
# 4. schema 與穩健性
# ─────────────────────────────────────────────────────────────


class TestSchemaAndRobustness:
    def test_schema_version_present(self, _isolated_trace):
        prompt_trace.trace_prompt(
            _messages("x", "y"), agent_id="agent_rem", route="roleplay", temperature=0.85
        )
        assert _rows(_isolated_trace)[0]["schema_version"] == prompt_trace.SCHEMA_VERSION

    def test_append_not_overwrite(self, _isolated_trace):
        for i in range(3):
            prompt_trace.trace_prompt(
                _messages(f"sys{i}", f"usr{i}"), agent_id="agent_rem",
                route="roleplay", temperature=0.85,
            )
        assert len(_rows(_isolated_trace)) == 3

    def test_trace_failure_never_raises(self, monkeypatch, _isolated_trace):
        """🔴 觀測不得拖垮主流程：寫入炸掉也必須安靜吞掉。"""
        def _boom(*a, **k):
            raise OSError("disk on fire")
        monkeypatch.setattr(prompt_trace, "_rotate_if_needed", _boom)
        prompt_trace.trace_prompt(
            _messages("x", "y"), agent_id="agent_rem", route="roleplay", temperature=0.85
        )  # 不應拋出

    def test_empty_messages_is_safe(self, _isolated_trace):
        prompt_trace.trace_prompt(
            [], agent_id="agent_rem", route="roleplay", temperature=0.85
        )
        row = _rows(_isolated_trace)[0]
        assert row["n_messages"] == 0
        assert row["system_prompt_len"] == 0


# ─────────────────────────────────────────────────────────────
# 5. 凍結禁令：觀測層不得反向相依 LLM
# ─────────────────────────────────────────────────────────────


class TestFrozenProhibition:
    def test_module_imports_no_llm_client(self):
        """OBS-CHRONO-1 凍結禁令：sidecar 不得 import 任何 LLM client。

        否則「觀測層改變被觀測對象」，量測本身會改變被量測的東西。
        """
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(prompt_trace))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                imported.add(mod)
                imported.update(f"{mod}.{a.name}" for a in node.names)
        forbidden = ("openai", "httpx", "anthropic", "src.llm", "llm.proxy")
        for name in imported:
            assert not any(f in name for f in forbidden), f"觀測層不得相依 {name}"


# ─────────────────────────────────────────────────────────────
# 6. 插針層守衛：必須真的從 proxy 那一點觸發
# ─────────────────────────────────────────────────────────────
# 教訓（變異實驗實測）：只測 trace_prompt() 本身時，把 proxy 插針的
# temperature 改成 None、event 插針改成 0.0，**整套測試仍然全綠**——
# 因為沒有人從 proxy 那一點觀察過。歸因數值若不由這組測試守住等於沒有守衛。


class _SpyBackend(LLMBackend):
    """只記錄、不發真實請求的 LLM backend。"""

    def __init__(self):
        self.calls = []

    async def complete(self, messages, model, max_tokens, temperature, **kwargs):
        self.calls.append({"temperature": temperature, "n_messages": len(messages)})
        return "[MOCK] ……在喔。"


@pytest.fixture
def spy_backend():
    return _SpyBackend()


@pytest.mark.asyncio
class TestProxyHookAttribution:
    async def test_roleplay_hook_records_route_and_temperature(self, spy_backend, _isolated_trace):
        bus = SoulEventBus()
        proxy = LLMProxy(bus=bus, backend=spy_backend, model="mock-model")
        await bus.start()
        try:
            event = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="agent_akane", target="broadcast",
                priority=EventPriority.NORMAL,
                payload={
                    "agent_id": "agent_akane", "reason": "heartbeat",
                    "draft": "……在喔。", "mood": 0.0, "mode": "group",
                    "target_user_id": "bryan", "memory_context": "",
                },
                session_id="session_obs_chrono_1",
            )
            await proxy._handle_event_impl(event)
        finally:
            await bus.stop()

        rows = _rows(_isolated_trace)
        assert rows, "角色路徑的插針沒有產生任何 trace（觀測站形同虛設）"
        row = rows[-1]
        assert row["route"] == "roleplay:heartbeat"
        assert row["agent_id"] == "agent_akane"
        # 🔴 歸因守衛：trace 記的溫度必須等於**實際送給 backend** 的溫度。
        assert row["temperature"] == spy_backend.calls[-1]["temperature"]
        assert row["temperature"] == pytest.approx(proxy.roleplay_temperature)

    async def test_event_hook_records_event_route_and_temperature(self, spy_backend, _isolated_trace):
        bus = SoulEventBus()
        proxy = LLMProxy(bus=bus, backend=spy_backend, model="mock-model")
        await bus.start()
        try:
            await proxy.generate_event_text(
                messages=[{"role": "system", "content": "世界觀敘事者"}, {"role": "user", "content": "描述場景"}],
                agent_id="agent_akane",
            )
        finally:
            await bus.stop()

        rows = _rows(_isolated_trace)
        assert rows, "事件路徑的插針沒有產生任何 trace"
        row = rows[-1]
        assert row["route"] == "event"
        # 🔴 事件路徑的溫度必須是 event_temperature（0.7），不能被角色路徑的 0.85 蓋掉。
        assert row["temperature"] == pytest.approx(proxy.event_temperature)
        assert row["temperature"] == spy_backend.calls[-1]["temperature"]
        assert row["temperature"] != pytest.approx(proxy.roleplay_temperature)

    async def test_hook_inactive_when_disabled(self, spy_backend, _isolated_trace):
        """關閉時插針不得建立檔案——這是「零額外 IO」的實際保證。"""
        prompt_trace.set_trace_enabled(False)
        bus = SoulEventBus()
        proxy = LLMProxy(bus=bus, backend=spy_backend, model="mock-model")
        await bus.start()
        try:
            await proxy.generate_event_text(
                messages=[{"role": "system", "content": "世界觀敘事者"}],
                agent_id="agent_akane",
            )
        finally:
            await bus.stop()
        assert spy_backend.calls, "backend 應仍被呼叫（觀測不得影響功能）"
        assert not _isolated_trace.exists()
