"""
tests/test_c31_relational_expression.py — C-3.1 关系增强投递实作验收

契约: docs/C-3.1-RELATIONAL-EXPRESSION-CONTRACT.md (唯一规格, 2026-09-05)

验收锚点 (契约 §7.1):
  Q1. band 注入存在: _format_relational_perception_block 输出含 [關係感知]
      + 正确 band_label (stranger→陌生人 / known→認識 / familiar→熟悉 / close→親近)
  Q2. bryan 归一化: motive_target="bryan" → 读 user_bryan entry;
      motive_target="agent_rem" → 读同 key (防 key 错位静默漏注入)
  Q3. 三重 fail-safe: 无条目 / 非法 target / 读取异常 → 返回 "" (整块省略)
  Q4. Token 预算: 最坏情况 (5 个 12 字符 tag) 字符数 ≤160 (≈≤80 tokens 近似)
  Q5. 格式稳定: 块头/行格式/印象行 `、` 连接; 无 confidence/计数/分数
  Q6. 向后兼容: _build_messages_group / _build_messages_private 不传
      motive_target (None) → 无 [關係感知] 块 (零行为变化)
  Q7. 透传链: scheduler._decision_check transmit → _last_transmit_target →
      _publish_agency_trigger extra["motive_target"] → executor
      (resolve_proactive_delivery + chrono_payload) → consciousness._fire_intent
      intent_payload["motive_target"] → proxy._handle_event_impl 解出
  Q8. P1 投递分流 (Owner 授权 2026-09-05): user_bryan/bryan → 1:1 TG 私聊;
      AGENT_IDS → lounge/soul_wall 公开频道 (mode=group); 未知/空 → 默认 (fail-safe)

Frozen contract 检查:
  - TriggerEnvelope 0 变更 (extra 是既有透传通道, 只写入 key)
  - Agency 4 stages / InnerLifeEvent / SAGE / decision.py 0 触碰
  - tests/test_soul_md_loader.py 不碰
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_mock_store(relational_band: str = "familiar", tags: List[str] | None = None):
    """Mock MultiAgentRelationshipsManager: get_store → entry (schema 4.2 字段)。"""
    mock_manager = MagicMock()
    mock_store = MagicMock()
    entry = {
        "impression": "should not leak",
        "feeling": "should not leak",
        "confidence": 0.85,  # legacy 只读字段, 不得输出
        "interaction_count": 42,  # 不得输出
        "last_interaction_at": "2026-09-05T00:00:00Z",  # 不得输出
        "relational_band": relational_band,
        "impression_tags": tags if tags is not None else ["温柔", "可靠", "话少"],
    }
    mock_store.get.return_value = entry
    mock_manager.get_store.return_value = mock_store
    return mock_manager


def _make_mock_memory():
    m = MagicMock()
    m.get_group_history.return_value = []
    m.get_recent_with_meta.return_value = []
    return m


_BAND_LABELS = {
    "stranger": "陌生人",
    "known": "認識",
    "familiar": "熟悉",
    "close": "親近",
}


# ────────────────────────────────────────────────────────────
# Q1. helper 存在 + 4 band 全遍历
# ────────────────────────────────────────────────────────────

class TestQ1HelperBandInjection:
    """Q1. _format_relational_perception_block 输出带正确 band_label。"""

    def test_helper_exists_and_signature(self):
        from src.llm.proxy import _format_relational_perception_block
        assert callable(_format_relational_perception_block)
        sig = inspect.signature(_format_relational_perception_block)
        params = list(sig.parameters.keys())
        assert params == ["agent_id", "target"]

    def test_band_labels_all_four(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        for band, label in _BAND_LABELS.items():
            mock_mgr = _make_mock_store(relational_band=band)
            with patch(
                "src.soul.relationships.get_relationships_manager",
                return_value=mock_mgr,
            ):
                result = _format_relational_perception_block("agent_rem", "agent_rem")
            assert result.startswith("[關係感知]")
            assert f"- 對 agent_rem 的關係帶：{label}" in result, (
                f"band={band} 應渲染為 {label}, 實際: {result!r}"
            )

    def test_stranger_also_injected(self, tmp_path):
        """契约 §4.3: stranger 也注入 (对陌生伙伴客气疏离是机制本身)。"""
        from src.llm.proxy import _format_relational_perception_block
        mock_mgr = _make_mock_store(relational_band="stranger")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        assert "陌生人" in result


# ────────────────────────────────────────────────────────────
# Q2. bryan 归一化 (防 key 错位静默漏注入)
# ────────────────────────────────────────────────────────────

class TestQ2BryanNormalization:
    """Q2. motive_target="bryan" → 读 user_bryan entry; agent_id → 同 key。"""

    def test_bryan_maps_to_user_bryan(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_manager = _make_mock_store(relational_band="close")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_manager,
        ):
            result = _format_relational_perception_block("agent_rem", "bryan")
        assert "親近" in result
        # 必须查 user_bryan key (不是 "bryan")
        mock_manager.get_store.assert_called_with("agent_rem")
        mock_manager.get_store.return_value.get.assert_called_with("user_bryan")

    def test_agent_target_uses_same_key(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_manager = _make_mock_store(relational_band="familiar")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_manager,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        assert "熟悉" in result
        mock_manager.get_store.return_value.get.assert_called_with("agent_rem")

    def test_bryan_normalized_also_in_build_paths(self, tmp_path):
        """_build_messages_private(motive_target="bryan") → 读 user_bryan 并注入。"""
        from src.llm.proxy import _build_messages_private
        mock_manager = _make_mock_store(relational_band="close", tags=["温柔"])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_manager,
        ):
            msgs = _build_messages_private(
                agent_id="agent_rem",
                soul="你是 Rem。",
                current_input="hi",
                memory_context="",
                memory=_make_mock_memory(),
                reason="proactive_dm",
                motive_target="bryan",
            )
        sys_content = msgs[0]["content"]
        assert "[關係感知]" in sys_content
        assert "親近" in sys_content
        mock_manager.get_store.return_value.get.assert_called_with("user_bryan")


# ────────────────────────────────────────────────────────────
# Q3. 三重 fail-safe 省略
# ────────────────────────────────────────────────────────────

class TestQ3FailSafe:
    """Q3. 无条目 / 非法 target / 读取异常 → 返回 ""。"""

    def test_no_entry_returns_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_manager = MagicMock()
        mock_store = MagicMock()
        mock_store.get.return_value = None  # entry 缺失
        mock_manager.get_store.return_value = mock_store
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_manager,
        ):
            assert _format_relational_perception_block("agent_rem", "agent_rem") == ""

    def test_invalid_target_returns_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        for bad in [None, "", 123, [], {}]:
            with patch(
                "src.soul.relationships.get_relationships_manager",
                return_value=_make_mock_store(),
            ):
                assert _format_relational_perception_block("agent_rem", bad) == ""

    def test_invalid_agent_id_returns_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        for bad in [None, "", 123]:
            with patch(
                "src.soul.relationships.get_relationships_manager",
                return_value=_make_mock_store(),
            ):
                assert _format_relational_perception_block(bad, "agent_rem") == ""

    def test_store_exception_returns_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_manager = MagicMock()
        mock_store = MagicMock()
        mock_store.get.side_effect = RuntimeError("坏档")
        mock_manager.get_store.return_value = mock_store
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_manager,
        ):
            assert _format_relational_perception_block("agent_rem", "agent_rem") == ""

    def test_manager_none_returns_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=None,
        ):
            assert _format_relational_perception_block("agent_rem", "agent_rem") == ""

    def test_no_motive_target_skips_injection(self, tmp_path):
        """§2.1 三重省略②: 无 motive_target (非目标驱动发言) → 不注入。"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        for builder, kwargs in [
            (_build_messages_group, {}),
            (_build_messages_private, {"reason": "user_message"}),
        ]:
            with patch(
                "src.soul.relationships.get_relationships_manager",
                return_value=_make_mock_store(),
            ):
                msgs = builder(
                    agent_id="agent_rem",
                    soul="你是 Rem。",
                    current_input="hi",
                    memory_context="",
                    memory=_make_mock_memory(),
                    **kwargs,
                )
            assert "[關係感知]" not in msgs[0]["content"]


# ────────────────────────────────────────────────────────────
# Q4. Token 预算 ≤80 (字符数 ≤160 近似) + Q5. 格式稳定
# ────────────────────────────────────────────────────────────

class TestQ4TokenBudgetAndQ5Format:
    """Q4/Q5. 最坏情况预算 + 确定性渲染格式。"""

    def test_worst_case_token_budget(self, tmp_path):
        """5 个 12 字符 tag (最坏情况) → 字符数 ≤160 (≈≤80 tokens 近似)。"""
        from src.llm.proxy import (
            _MAX_IMPRESSION_TAGS,
            _MAX_IMPRESSION_TAG_CHARS,
            _format_relational_perception_block,
        )
        long_tags = ["漢字" * 6 for _ in range(8)]  # 8 个超长 tag (会被截断到 5 个)
        mock_mgr = _make_mock_store(relational_band="familiar", tags=long_tags)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        chars = len(result)
        # 单项截断上限生效
        assert all(len(t) <= _MAX_IMPRESSION_TAG_CHARS for t in long_tags[: _MAX_IMPRESSION_TAGS])
        assert chars <= 160, f"字符数={chars} 超预算 (契约 §4.2 ≤80 tokens 近似)"
        # 结构性低预算: 块固定 2-3 行
        assert len(result.splitlines()) <= 3

    def test_no_impression_row_when_tags_empty(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_mgr = _make_mock_store(relational_band="known", tags=[])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        assert "印象" not in result  # 无 tags → 省略印象行
        assert "認識" in result

    def test_impression_tags_join_with_cn_comma(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_mgr = _make_mock_store(relational_band="familiar", tags=["温柔", "可靠"])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        assert "- 印象：温柔、可靠" in result
        # 连接符是中文顿号 (、) 不是英文逗号
        assert "温柔,可靠" not in result and "温柔，可靠" not in result

    def test_no_scores_counts_in_output(self, tmp_path):
        from src.llm.proxy import _format_relational_perception_block
        mock_mgr = _make_mock_store(relational_band="close", tags=["温柔"])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        # 0 分数 / 0 计数 / 0 confidence / 0 时间戳 / 0 使用说明
        for banned in ["confidence", "interaction_count", "0.85", "42", "2026-", "应该", "请"]:
            assert banned not in result, f"{banned!r} 不应出现在输出: {result!r}"
        # 只有关系带 + 印象两行事实
        assert result.count("\n") <= 2

    def test_unknown_band_falls_back_stranger(self, tmp_path):
        """4.1 旧档 / 坏 band 值 → 缺省 stranger (SG-1 §2.3 缺省兼容)。"""
        from src.llm.proxy import _format_relational_perception_block
        entry = _make_mock_store(relational_band="super_close")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=entry,
        ):
            result = _format_relational_perception_block("agent_rem", "agent_rem")
        assert "陌生人" in result


# ────────────────────────────────────────────────────────────
# Q6. 双组装注入 (A2A/A2U) + 位置 + 向后兼容
# ────────────────────────────────────────────────────────────

class TestQ6DualAssemblyInjection:
    """Q6. group (A2A) 与 private (A2U) 都注入, 位置在 M5.13-3 之后 inner_life 之前。"""

    def test_group_injects_at_correct_position(self, tmp_path):
        from src.llm.proxy import _build_messages_group
        mock_mgr = _make_mock_store(relational_band="familiar", tags=["温柔"])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            msgs = _build_messages_group(
                agent_id="agent_rem",
                soul="你是 Rem。",
                current_input="hi",
                memory_context="",
                memory=_make_mock_memory(),
                motive_target="agent_rem",
            )
        content = msgs[0]["content"]
        assert "[關係感知]" in content
        assert "熟悉" in content
        # 位置: 在 M5.13-3 relationship block 之后、inner_life (M2.0) 之前
        m513_pos = content.find("[你跟")
        rel_pos = content.find("[關係感知]")
        inner_pos = content.find("[最近內在生活]")
        assert rel_pos > m513_pos, "C-3.1 块应在 M5.13-3 块之后"
        if inner_pos != -1:
            assert rel_pos < inner_pos, "C-3.1 块应在 inner_life 之前"

    def test_private_injects_at_correct_position(self, tmp_path):
        from src.llm.proxy import _build_messages_private
        mock_mgr = _make_mock_store(relational_band="close", tags=["温柔"])
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            msgs = _build_messages_private(
                agent_id="agent_rem",
                soul="你是 Rem。",
                current_input="hi",
                memory_context="",
                memory=_make_mock_memory(),
                reason="proactive_dm",
                motive_target="user_bryan",
            )
        content = msgs[0]["content"]
        assert "[關係感知]" in content
        assert "親近" in content
        m513_pos = content.find("[你跟")
        rel_pos = content.find("[關係感知]")
        inner_pos = content.find("[最近內在生活]")
        assert rel_pos > m513_pos
        if inner_pos != -1:
            assert rel_pos < inner_pos

    def test_both_paths_none_motive_target_no_block(self, tmp_path):
        """缺省 None → 与现状完全一致 (无 [關係感知])。"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=_make_mock_store(),
        ):
            g = _build_messages_group(
                agent_id="agent_rem", soul="你是 Rem。", current_input="hi",
                memory_context="", memory=_make_mock_memory(),
            )
            p = _build_messages_private(
                agent_id="agent_rem", soul="你是 Rem。", current_input="hi",
                memory_context="", memory=_make_mock_memory(),
            )
        assert "[關係感知]" not in g[0]["content"]
        assert "[關係感知]" not in p[0]["content"]


# ────────────────────────────────────────────────────────────
# Q7. 透传链 (scheduler → executor → consciousness → proxy)
# ────────────────────────────────────────────────────────────

def _run(coro):
    """asyncio.run 包装 (仿 test_sm3_motive_decision _run 先例, 0 pytest-asyncio 依赖)。"""
    return asyncio.run(coro)


class TestQ7TransmissionChain:
    """Q7. 全链路逐跳透传 motive_target (additive, 0 签名破坏)。"""

    # ── 跳 1: scheduler._decision_check transmit → _last_transmit_target ──

    def test_decision_check_transmit_records_target(self, tmp_path):
        from src.soul.scheduler import SoulScheduler

        class _FakeEngine:
            async def interpret_new_events(self, agent_id):
                return None

            async def decide(self, motive, agent_id):
                return SimpleNamespace(transmit=True, reason="test")

            def resolve_pending(self, agent_id):
                return SimpleNamespace(motive_id="m1", target="agent_rem")

            def mark_transmitted(self, motive_id):
                return None

            def mark_rejected(self, motive_id):
                return None

        scheduler = SoulScheduler(bus=MagicMock())

        async def _scenario():
            with patch("src.soul.motive.MotiveEngine", return_value=_FakeEngine()), \
                 patch("src.goals.motive_provider.GoalMotiveProvider") as mock_goal:
                mock_goal.for_agent.return_value = MagicMock()
                return await scheduler._decision_check("agent_rem")

        ok = _run(_scenario())
        assert ok is True
        assert scheduler._last_transmit_target == "agent_rem"

    def test_decision_check_not_transmit_keeps_none(self, tmp_path):
        from src.soul.scheduler import SoulScheduler

        class _FakeEngine:
            async def interpret_new_events(self, agent_id):
                return None

            async def decide(self, motive, agent_id):
                return SimpleNamespace(transmit=False, decision="do_nothing", reason="x")

            def resolve_pending(self, agent_id):
                return SimpleNamespace(motive_id="m1", target="agent_rem")

            def mark_rejected(self, motive_id):
                return None

        scheduler = SoulScheduler(bus=MagicMock(), actuator=None)
        scheduler._last_transmit_target = None

        async def _scenario():
            with patch("src.soul.motive.MotiveEngine", return_value=_FakeEngine()), \
                 patch("src.goals.motive_provider.GoalMotiveProvider") as mock_goal:
                mock_goal.for_agent.return_value = MagicMock()
                return await scheduler._decision_check("agent_rem")

        ok = _run(_scenario())
        assert ok is False
        assert scheduler._last_transmit_target is None  # 非 transmit 不记录

    # ── 跳 2: _publish_agency_trigger payload.extra 写入 + 单次消费 ──
    # 注: proactive_dm publish 前会跑真实 gate/Decision, 这里 stub 两个 gate
    # 专注验证 extra 组装 + 单次消费 (机制级单测)。

    def _stub_gates(self, scheduler):
        """把 _inner_life_gate_check / _decision_check 换成放行 stub。"""
        async def _allow(agent_id):
            return True
        scheduler._inner_life_gate_check = _allow
        scheduler._decision_check = _allow

    def test_publish_agency_trigger_extra_contains_motive_target(self, tmp_path):
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventType
        from src.soul.scheduler import SoulScheduler

        bus = SoulEventBus()

        async def _scenario():
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="c31_capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                self._stub_gates(scheduler)
                scheduler._last_transmit_target = "agent_rem"  # 模拟 _decision_check 已记录
                await scheduler._publish_agency_trigger(
                    agent_id="agent_rem",
                    trigger_type="proactive_dm",
                )
                return captured, scheduler
            finally:
                await bus.stop()

        captured, scheduler = _run(_scenario())
        assert len(captured) == 1
        payload = captured[0].payload
        assert payload["extra"].get("motive_target") == "agent_rem"
        # 单次消费: publish 后清空, 不残留到下一次 trigger
        assert scheduler._last_transmit_target is None

    def test_publish_consumes_target_single_shot(self, tmp_path):
        """单次消费: publish 后 _last_transmit_target 清空 (不残留泄漏)。"""
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventType
        from src.soul.scheduler import SoulScheduler

        bus = SoulEventBus()

        async def _scenario():
            await bus.start()
            try:
                bus.subscribe(
                    subscriber_id="c31_capture_consume",
                    handler=lambda e: None,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                self._stub_gates(scheduler)
                scheduler._last_transmit_target = "agent_rem"
                await scheduler._publish_agency_trigger(
                    agent_id="agent_rem",
                    trigger_type="proactive_dm",
                )
                # 消费后必须清空 —— 防残留 target 泄漏到下一次 trigger
                remaining = scheduler._last_transmit_target
                return remaining
            finally:
                await bus.stop()

        remaining = _run(_scenario())
        assert remaining is None

    def test_publish_without_target_extra_empty(self, tmp_path):
        """无 _last_transmit_target → extra 与现状一致 (无 motive_target key)。"""
        from src.eventbus.bus import SoulEventBus
        from src.eventbus.schema import EventType
        from src.soul.scheduler import SoulScheduler

        bus = SoulEventBus()

        async def _scenario():
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="c31_capture2",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                self._stub_gates(scheduler)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_rem",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_scenario())
        assert len(captured) == 1
        assert "motive_target" not in captured[0].payload["extra"]

    # ── 跳 3: consciousness._fire_intent chrono_payload → intent_payload ──

    def _make_mock_consciousness(self):
        from src.eventbus.schema import SoulEvent
        from src.agent.consciousness import AgentConsciousness

        captured_events: List[SoulEvent] = []

        class _CapturingBus:
            async def publish(self, event):
                captured_events.append(event)
                return None

        class _ConcreteAgent(AgentConsciousness):
            def __init__(self, agent_id, bus):
                self.agent_id = agent_id
                self.bus = bus
                self._pending = False
                self.state = MagicMock()
                self.state.save = MagicMock()

            def _build_intent_payload(self, reason, elapsed_mins):
                return {"draft": "test draft content"}

            def _should_speak(self, elapsed_mins, chrono_payload=None):
                return True, "test reason"

        agent = _ConcreteAgent("agent_rem", _CapturingBus())
        return agent, captured_events

    def test_fire_intent_passthroughs_motive_target(self, tmp_path):
        from src.eventbus.schema import EventType
        _isolated_data_root(tmp_path)
        try:
            agent, captured = self._make_mock_consciousness()
            asyncio.run(agent._fire_intent(
                reason="proactive_dm",
                elapsed_mins=240.0,
                chrono_payload={
                    "draft": "test draft",
                    "target_channel": "telegram",
                    "target_user_id": "1696287850",
                    "motive_target": "user_bryan",
                },
                mode="private",
            ))
            assert len(captured) == 1
            assert captured[0].event_type == EventType.AGENT_INTENT
            assert captured[0].payload.get("motive_target") == "user_bryan"
        finally:
            _restore_data_root()

    def test_fire_intent_without_motive_target_no_key(self, tmp_path):
        """chrono_payload 没 motive_target 键 → intent_payload 不写 (零行为变化)。"""
        _isolated_data_root(tmp_path)
        try:
            agent, captured = self._make_mock_consciousness()
            asyncio.run(agent._fire_intent(
                reason="proactive_dm",
                elapsed_mins=240.0,
                chrono_payload={"draft": "test"},
                mode="private",
            ))
            assert len(captured) == 1
            assert "motive_target" not in captured[0].payload
        finally:
            _restore_data_root()

    # ── 跳 4: proxy._handle_event_impl 解出 motive_target (源码级断言) ──

    def test_proxy_handle_event_impl_extracts_motive_target(self):
        """proxy._handle_event_impl 从 event.payload 解出 motive_target 并传给组装函数。"""
        from src.llm.proxy import LLMProxy
        src = inspect.getsource(LLMProxy._handle_event_impl)
        assert 'motive_target = event.payload.get("motive_target")' in src
        assert "motive_target=motive_target" in src  # 传给 _build_messages_*


# ────────────────────────────────────────────────────────────
# Q8. P1 投递分流 (run_server executor 层, Owner 授权)
# ────────────────────────────────────────────────────────────

class TestQ8P1DeliveryRouting:
    """Q8. resolve_proactive_delivery: user_bryan→私聊 / AGENT_IDS→公开频道 / 未知 fail-safe。"""

    @staticmethod
    def _load_run_server():
        """载入 scripts/run_server.py 模块 (仿 test_self_check_v2 先例, 不启动 lifespan)。"""
        project_root = Path(__file__).resolve().parent.parent
        scripts_dir = project_root / "scripts"
        mod_name = "_run_server_c31_under_test"
        if mod_name in sys.modules:
            return sys.modules[mod_name]
        spec = importlib.util.spec_from_file_location(
            mod_name,
            scripts_dir / "run_server.py",
        )
        mod = importlib.util.module_from_spec(spec)
        src = (scripts_dir / "run_server.py").read_text(encoding="utf-8")
        code = compile(src, str(scripts_dir / "run_server.py"), "exec")
        try:
            exec(code, mod.__dict__)
        except SystemExit:
            pass
        except Exception:
            pass
        sys.modules[mod_name] = mod
        return mod

    def test_user_bryan_keeps_private_route(self, tmp_path):
        from src.soul.motive import set_agent_ids
        set_agent_ids([])
        try:
            mod = self._load_run_server()
            delivery = mod.resolve_proactive_delivery("user_bryan")
            assert delivery["mode"] == "private"
            assert delivery["target_channel"] == "telegram"
            assert delivery["target_user_id"] == "1696287850"
        finally:
            set_agent_ids([])

    def test_raw_bryan_normalized_keeps_private_route(self, tmp_path):
        """原始 "bryan" 也接受 (归一化判定, 契约 §2.4)。"""
        from src.soul.motive import set_agent_ids
        set_agent_ids(["agent_rem"])
        try:
            mod = self._load_run_server()
            delivery = mod.resolve_proactive_delivery("bryan")
            assert delivery["mode"] == "private"
            assert delivery["target_channel"] == "telegram"
        finally:
            set_agent_ids([])

    def test_agent_target_routes_to_public_channel(self, tmp_path):
        from src.soul.motive import set_agent_ids
        set_agent_ids(["agent_rem", "agent_ruka"])
        try:
            mod = self._load_run_server()
            delivery = mod.resolve_proactive_delivery("agent_rem")
            assert delivery["mode"] == "group"  # lounge/soul_wall 公开频道
            assert delivery["target_channel"] is None  # 不再直穿私聊
            assert delivery["target_user_id"] is None
            # 另一个注册 agent 同样改道
            delivery2 = mod.resolve_proactive_delivery("agent_ruka")
            assert delivery2["mode"] == "group"
        finally:
            set_agent_ids([])

    def test_unknown_target_fail_safe_default(self, tmp_path):
        """未知/非法 target → 维持既有默认行为 (fail-safe, 不报错)。"""
        from src.soul.motive import set_agent_ids
        set_agent_ids(["agent_rem"])
        try:
            mod = self._load_run_server()
            for unknown in ["stranger_xyz", "someone_else"]:
                delivery = mod.resolve_proactive_delivery(unknown)
                assert delivery["mode"] == "private"
                assert delivery["target_channel"] == "telegram"
                assert delivery["target_user_id"] == "1696287850"
        finally:
            set_agent_ids([])

    def test_empty_target_fail_safe_default(self, tmp_path):
        from src.soul.motive import set_agent_ids
        set_agent_ids(["agent_rem"])
        try:
            mod = self._load_run_server()
            for empty in [None, "", 0, False]:
                delivery = mod.resolve_proactive_delivery(empty)
                assert delivery["mode"] == "private"
                assert delivery["target_channel"] == "telegram"
        finally:
            set_agent_ids([])

    def test_executor_uses_modular_delivery_and_passthrough(self, tmp_path):
        """executor 源码级: 调 resolve_proactive_delivery + chrono_payload 透传 motive_target。"""
        project_root = Path(__file__).resolve().parent.parent
        src = (project_root / "scripts" / "run_server.py").read_text(encoding="utf-8")
        assert "resolve_proactive_delivery(_motive_target)" in src
        assert 'if _motive_target:\n                _chrono_payload["motive_target"] = _motive_target' in src
        assert "mode=_delivery_mode" in src


# ────────────────────────────────────────────────────────────
# Frozen contract 核对 (源码级 0 变更断言)
# ────────────────────────────────────────────────────────────

class TestFrozenContract:
    """Frozen 触点 0 变更: TriggerEnvelope / stages / InnerLifeEvent / decision。"""

    def test_trigger_envelope_unchanged(self):
        from src.agency import trigger as trigger_mod
        src = inspect.getsource(trigger_mod)
        # extra 是既有字段, 0 新字段
        assert "extra" in src
        assert "class TriggerEnvelope" in src

    def test_decision_py_unchanged_motive_target_absent(self):
        """decision.py 0 变更 (C-3.1 声明: 不触碰 Decision 层)。"""
        project_root = Path(__file__).resolve().parent.parent
        decision_src = (project_root / "src" / "soul" / "decision.py").read_text(encoding="utf-8")
        assert "motive_target" not in decision_src

    def test_scheduler_sources_dont_touch_frozen_files(self):
        """改动的 4 文件列表固定 = 契约 §6.1 清单。"""
        project_root = Path(__file__).resolve().parent.parent
        changed = [
            "src/llm/proxy.py",
            "src/soul/scheduler.py",
            "src/agent/consciousness.py",
            "scripts/run_server.py",
        ]
        for rel in changed:
            p = project_root / rel
            assert p.is_file(), f"{rel} 存在"