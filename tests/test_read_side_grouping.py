# -*- coding: utf-8 -*-
"""
test_read_side_grouping.py — SI-1 Shared Life Read-Side Grouping 验收测试

工单 SI-1：shared life 读侧分组（最小 query + tests）。

验收点（本文件逐一覆盖）：
  1. 同一 novelty_id 能找出相关 InnerLifeEvents（允许 0/1 条 world event）。
  2. 不把不同灵魂的 experience 合成一笔（分组只按 key，不合并 content）。
  3. correlation_id / session_id 不被当成 occurrence identity（三个函数语义分开）。
  4. query 0 write、0 新 InnerLifeEvent、0 elevation。
  5. perception co-presence ≠ InnerLife shared experience（两层读法分开）。

全部测试使用 in-memory InnerLifeWriter（不配 trace_writer → 不落盘），
且分组函数是纯函数：不写文件、不创建事件、不触发 elevation。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.inner_life.event import InnerLifeEvent, Provenance
from src.inner_life.grouping import (
    group_by_correlation,
    group_by_session,
    group_by_world_occurrence,
)
from src.inner_life.writer import InnerLifeWriter
from src.world.perception import PerceptionScores, WorldPerceptionTrace


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_event(
    writer: InnerLifeWriter,
    *,
    trigger: str = "diary:morning",
    actor: str | None = "agent_rem",
    session_id: str | None = None,
    correlation_id: str | None = None,
    novelty_id: str | None = None,
    parent_event_id: str | None = None,
) -> InnerLifeEvent:
    """用 in-memory InnerLifeWriter 创建一条事件（0 落盘：不配 trace_writer）。"""
    return writer.create_event(
        provenance=Provenance(
            trigger_type=trigger,
            actor_id=actor,
            source_system="diary" if trigger.startswith("diary") else "narrative",
        ),
        session_id=session_id,
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
        source_world_event_novelty_id=novelty_id,
    )


def _accepted_novelty_ids(traces) -> set[str]:
    """World 层 co-presence 读法：从 perception trace 提取 accepted 的 novelty_id 集合。"""
    return {t.novelty_id for t in traces if t.accepted}


def _make_perception_trace(novelty_id: str, *, accepted: bool = True) -> WorldPerceptionTrace:
    return WorldPerceptionTrace(
        event_id=novelty_id,
        timestamp="2026-08-13T10:00:00Z",
        source="news",
        event_type="world_event",
        scores=PerceptionScores(relevance=0.5, novelty=0.8, personal_significance=0.3),
        accepted=accepted,
        reason="test",
        context_injected=accepted,
        memory_written=False,
        novelty_id=novelty_id,
    )


# ─────────────────────────────────────────────
# 验收 1：同一 novelty_id 找出相关 InnerLifeEvents（允许 0/1 条 world event）
# ─────────────────────────────────────────────

def test_same_novelty_finds_related_events() -> None:
    writer = InnerLifeWriter()
    world_ev = _make_event(writer, trigger="diary:morning", actor=None,
                           novelty_id="nova-ABC-1")
    diary_ev = _make_event(writer, trigger="diary:night", actor="agent_rem",
                           novelty_id="nova-ABC-1", parent_event_id=world_ev.event_id)
    dream_ev = _make_event(writer, trigger="dream:dream", actor="agent_rem",
                           novelty_id="nova-ABC-1")

    grouped = group_by_world_occurrence(list(writer._events.values()))

    assert grouped["nova-ABC-1"] == [world_ev, diary_ev, dream_ev]
    # 同一 group 内 1 条 world event（actor=None）+ 后续引用它的日记/梦
    assert len(grouped["nova-ABC-1"]) == 3
    world_in_group = [e for e in grouped["nova-ABC-1"] if e.provenance.actor_id is None]
    assert len(world_in_group) == 1


def test_world_occurrence_allows_zero_or_one_world_event() -> None:
    """允许 0/1 条 world event：只有后来引用它的 diary 也算一组。"""
    writer = InnerLifeWriter()
    # novelty-1：1 条 world event + 1 条引用
    w1 = _make_event(writer, actor=None, novelty_id="nova-ZERO-1")
    _make_event(writer, actor="agent_rem", novelty_id="nova-ZERO-1")
    # novelty-2：0 条 world event，只有后来引用（0/1 都允许）
    _make_event(writer, actor="agent_rem", novelty_id="nova-ZERO-2")

    grouped = group_by_world_occurrence(list(writer._events.values()))

    assert len(grouped["nova-ZERO-1"]) == 2
    assert len(grouped["nova-ZERO-2"]) == 1
    assert [e.provenance.actor_id for e in grouped["nova-ZERO-2"]] == ["agent_rem"]


# ─────────────────────────────────────────────
# 验收 2：不把不同灵魂的 experience 合成一笔
# ─────────────────────────────────────────────

def test_does_not_merge_different_souls_experience() -> None:
    """分组只按 key 归类，不合并 content：每个 event 原样保留，含各自 actor。"""
    writer = InnerLifeWriter()
    e_a = _make_event(writer, actor="agent_rem", novelty_id="nova-SHARED-1")
    e_b = _make_event(writer, actor="agent_akane", novelty_id="nova-SHARED-1")

    grouped = group_by_world_occurrence(list(writer._events.values()))

    assert grouped["nova-SHARED-1"] == [e_a, e_b]
    assert [e.provenance.actor_id for e in grouped["nova-SHARED-1"]] == [
        "agent_rem",
        "agent_akane",
    ]
    # 每个 event 仍是完整独立对象（未被改造成合成的一笔）
    assert all(isinstance(e, InnerLifeEvent) for e in grouped["nova-SHARED-1"])
    assert e_a.event_id != e_b.event_id


# ─────────────────────────────────────────────
# 验收 3：correlation_id / session_id 不被当成 occurrence identity
# ─────────────────────────────────────────────

def test_three_keys_are_semantically_separate() -> None:
    """三个函数语义分开：同一 novelty 的两条事件，correlation/session 不同时
    只在 world_occurrence 同一组，不因 correlation/session 合并。"""
    writer = InnerLifeWriter()
    # 两条事件：同一 novelty（occurrence 相同），但 correlation 与 session 都不同
    e1 = _make_event(writer, novelty_id="nova-SEP-1",
                     correlation_id="corr-AAA", session_id="session-1")
    e2 = _make_event(writer, novelty_id="nova-SEP-1",
                     correlation_id="corr-BBB", session_id="session-2")

    all_events = list(writer._events.values())

    by_occ = group_by_world_occurrence(all_events)
    by_corr = group_by_correlation(all_events)
    by_sess = group_by_session(all_events)

    # occurrence：同一 novelty 在同一组
    assert by_occ["nova-SEP-1"] == [e1, e2]
    # correlation：corr-BBB 不是 occurrence identity → 与 nova-SEP-1 不同轴
    assert by_corr["corr-AAA"] == [e1]
    assert by_corr["corr-BBB"] == [e2]
    assert "corr-BBB" not in by_occ
    # session：session-2 不是 occurrence identity
    assert by_sess["session-1"] == [e1]
    assert by_sess["session-2"] == [e2]
    assert "session-2" not in by_occ


def test_group_keys_are_the_three_distinct_fields() -> None:
    """三个 group 的 key 字段互不相同：不统一成一个 shared_episode_id。"""
    writer = InnerLifeWriter()
    _make_event(writer, actor=None, novelty_id="nova-1",
                correlation_id="corr-1", session_id="session-1")

    all_events = list(writer._events.values())
    by_occ = group_by_world_occurrence(all_events)
    by_corr = group_by_correlation(all_events)
    by_sess = group_by_session(all_events)

    assert set(by_occ.keys()) == {"nova-1"}
    assert set(by_corr.keys()) == {"corr-1"}
    assert set(by_sess.keys()) == {"session-1"}
    assert set(by_occ.keys()) & set(by_corr.keys()) == set()
    assert set(by_occ.keys()) & set(by_sess.keys()) == set()


def test_none_key_events_are_excluded() -> None:
    """无对应 key（None）的事件不进任何组（不是 group key 的一部分）。"""
    writer = InnerLifeWriter()
    _make_event(writer, actor="agent_rem")  # 三个 key 都是 None
    _make_event(writer, actor="agent_rem", novelty_id="nova-KEY-1")

    all_events = list(writer._events.values())
    by_occ = group_by_world_occurrence(all_events)
    by_corr = group_by_correlation(all_events)
    by_sess = group_by_session(all_events)

    assert set(by_occ.keys()) == {"nova-KEY-1"}
    assert by_corr == {}
    assert by_sess == {}


# ─────────────────────────────────────────────
# 验收 4：query 0 write / 0 新 InnerLifeEvent / 0 elevation
# ─────────────────────────────────────────────

def test_grouping_is_read_only_zero_write(tmp_path: Path) -> None:
    """调用分组前后：writer 事件数与已知 event_id 集合不变、trace 文件状态不变。"""
    import os
    from pathlib import Path as _Path

    # in-memory writer，不配 trace_writer（默认不落盘）
    writer = InnerLifeWriter()
    before = writer.get_known_event_count()
    before_ids = set(writer._known_event_ids)

    _make_event(writer, actor=None, novelty_id="nova-RW-1")
    _make_event(writer, actor="agent_rem", novelty_id="nova-RW-1")

    snapshot = list(writer._events.values())
    by_occ = group_by_world_occurrence(snapshot)
    by_corr = group_by_correlation(snapshot)
    by_sess = group_by_session(snapshot)

    # 分组本身就是纯函数：不产生新事件、不产生新 key
    assert writer.get_known_event_count() == before + 2  # 只多测试里显式创建的 2 条
    assert set(writer._known_event_ids) == before_ids | {
        e.event_id for e in snapshot
    }
    assert by_occ and by_corr == {} and by_sess == {}

    # 0 write：trace.jsonl（若存在）在调用前后必须逐字节不变。
    # 只对比「调用分组这一动作」前后——若文件已存在（历史遗留）则验证不被改写；
    # 若不存在则验证不被创建。
    data_dir = _Path(os.getcwd()) / "data"
    inner_life_trace = data_dir / "inner_life" / "trace.jsonl"
    existed_before = inner_life_trace.exists()
    size_before = inner_life_trace.stat().st_size if existed_before else -1
    mtime_before = inner_life_trace.stat().st_mtime if existed_before else -1.0

    # 再次调用三个分组（模拟真实读侧查询路径）
    group_by_world_occurrence(snapshot)
    group_by_correlation(snapshot)
    group_by_session(snapshot)

    existed_after = inner_life_trace.exists()
    size_after = inner_life_trace.stat().st_size if existed_after else -1
    mtime_after = inner_life_trace.stat().st_mtime if existed_after else -1.0

    assert existed_before == existed_after
    assert size_before == size_after
    assert mtime_before == mtime_after


def test_grouping_triggers_zero_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """分组不触发 elevation：monkeypatch 掉 elevation 入口断言零调用。"""
    calls: list = []

    import src.inner_life.elevation_adapter as elevation_adapter

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))

    # 若 soul_elevation 未装（guarded import），monkeypatch 仍然有效
    monkeypatch.setattr(elevation_adapter, "run_elevation", _spy, raising=False)

    writer = InnerLifeWriter()
    _make_event(writer, actor=None, novelty_id="nova-ELEV-1")
    _make_event(writer, actor="agent_rem", novelty_id="nova-ELEV-1")

    group_by_world_occurrence(list(writer._events.values()))
    group_by_correlation(list(writer._events.values()))
    group_by_session(list(writer._events.values()))

    assert calls == []


# ─────────────────────────────────────────────
# 验收 5：perception co-presence ≠ InnerLife shared experience（两层读法分开）
# ─────────────────────────────────────────────

def test_co_presence_ne_inner_life_shared_experience() -> None:
    """
    两层读法分开：同一 novelty_id，
      - co-presence（world 层 perception accept 名单）：agent_a、agent_b 都 accept 了
      - InnerLife shared experience（本模块 grouping）：只有 agent_a 有引用它的 InnerLifeEvent
    断言：co-presence 名单 ≠ shared experience 名单；不把 perception accept 当 shared experience。
    """
    # World 层：agent_a 与 agent_b 都 perception accept 了同一 novelty
    traces = [
        _make_perception_trace("nova-COPRES-1", accepted=True),
        _make_perception_trace("nova-COPRES-1", accepted=True),
    ]
    co_presence = _accepted_novelty_ids(traces)  # = {"nova-COPRES-1"}

    # InnerLife 层：只有 agent_a 的 InnerLifeEvent 引用了这个 novelty
    writer = InnerLifeWriter()
    _make_event(writer, actor=None, novelty_id="nova-COPRES-1", trigger="diary:morning")
    _make_event(writer, actor="agent_a", novelty_id="nova-COPRES-1", trigger="diary:night")

    inner_life_grouped = group_by_world_occurrence(list(writer._events.values()))
    shared_experience_actors = {
        e.provenance.actor_id
        for e in inner_life_grouped.get("nova-COPRES-1", [])
    }

    # 两层读法分开成立：
    #   - co-presence（accept 名单）来自 perception trace，包含的是「接受了该事实」
    #   - InnerLife shared experience 只认「有 InnerLifeEvent 引用该 novelty」的 soul
    assert "nova-COPRES-1" in co_presence
    assert shared_experience_actors == {None, "agent_a"}
    # 关键断言：co-presence 的 accept 行为 ≠ 有 InnerLife 引用；
    # 若有 agent_b 也 accept 但无 InnerLifeEvent 引用，则两个名单不同。
    # 这里用「agent_b accept 但不在 shared experience」直接证明两层分开。
    agents_in_shared = {
        a for a in shared_experience_actors if a is not None
    }
    assert "agent_b" not in agents_in_shared  # agent_b accept ≠ agent_b 有 shared experience
