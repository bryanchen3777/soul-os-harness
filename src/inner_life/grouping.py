"""
src/inner_life/grouping.py — SI-1 Shared Life Read-Side Grouping (pure functions)

工单 SI-1（灵魂互动最小实现）：shared life 的读侧分组。

背景（SI-0 审计收敛，直接采信）：
  - SharedSocialEpisode 不是 domain object 不是 store；v1 是 ephemeral read-side grouping。
  - 三个 key 分开，不准压成一个 shared_episode_id：
      * group_by_world_occurrence(events) → 按 source_world_event_novelty_id 分组
      * group_by_correlation(events)      → 按 correlation_id 分组
      * group_by_session(events)          → 按 session_id 分组
  - 现况：一条 world InnerLifeEvent、actor_id=None、source_world_event_novelty_id
    当 occurrence key。共在看的是「谁 perception accept 了同一 novelty_id」，
    不是两份 Inner Life。
  - 两层读法分开：
      * InnerLife grouping（本模块）：同一 novelty_id 下现有 InnerLifeEvent
        （常 0/1 条 world event + 后来引用它的日记/梦）
      * Co-presence grouping（不在本模块）：perception accept 名单（谁 accept 了
        同一 novelty_id）——那是 world 层的读法（WorldPerceptionTrace / accepted
        名单），不是本模块职责。

关键决策（已定，照做）：
  1. 优先复用既有 InnerLife query：correlation_id / session_id 已是 InnerLifeEvent
     既有字段，InnerLifeWriter.get_events_by_correlation / get_events_by_session、
     NarrativeTraceReader.query_by_correlation_id / query_by_session_id 已存在。
     本模块的 group_by_correlation / group_by_session 只是「极薄纯函数」分组形态，
     与既有 query 共享同一数据字段，不包 engine、不重复造 wheel。
  2. source_world_event_novelty_id 缺分组能力 → 加 group_by_world_occurrence 纯函数。
  3. 结果 ephemeral：最多 key + events[]，无 SharedSocialEpisode class / 无
     repository / 无 store。
  4. 三 key 分开：三个独立纯函数，不统一成一个 shared_episode_id。
  5. 不触发 elevation、不产 InnerLifeEvent、不写 production data：纯读侧，0 write。

禁止遵守：无新 store / 无新 subsystem / 无新 domain id / 无 interaction /
不改 frozen contract / 0 write / 0 新 InnerLifeEvent / 0 elevation。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Union

# 输入可以是 InnerLifeEvent 实例或 dict（trace_reader 返回 dict 形态）。
EventLike = Union[Any, Dict[str, Any]]

# 三个 key 的字段名（InnerLifeEvent 既有字段，M5.15-5 / M5.4-5.1 已定义）
_FIELD_WORLD_OCCURRENCE = "source_world_event_novelty_id"
_FIELD_CORRELATION = "correlation_id"
_FIELD_SESSION = "session_id"


def _value(event: EventLike, field: str) -> Any:
    """从 InnerLifeEvent 实例或 dict 提取字段值（dict 优先，容忍缺字段）。"""
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)


def _group(events: Iterable[EventLike], field: str) -> Dict[str, List[EventLike]]:
    """通用极薄分组：key → list[event]，key 为 None 的事件不进任何组。

    不合并 content：events 按原样保留，只按 key 归类。保序（输入顺序）。
    结果 ephemeral：仅在调用内存在，无缓存、无持久化。
    """
    grouped: Dict[str, List[EventLike]] = {}
    for event in events:
        key = _value(event, field)
        if key is None:
            continue
        grouped.setdefault(key, []).append(event)
    return grouped


def group_by_world_occurrence(
    events: Iterable[EventLike],
) -> Dict[str, List[EventLike]]:
    """
    按 ``source_world_event_novelty_id`` 分组（occurrence = WorldEvent.novelty_id）。

    同一 novelty_id 找出相关 InnerLifeEvents——允许 0/1 条 world event（通常：
    1 条 world-triggered event + 后来引用它的 diary/dream）。

    Args:
        events: InnerLifeEvent 实例或 dict 的可迭代对象。

    Returns:
        dict[novelty_id, list[event]]（ephemeral）。无该 key（None）的事件不进组。
    """
    return _group(events, _FIELD_WORLD_OCCURRENCE)


def group_by_correlation(
    events: Iterable[EventLike],
) -> Dict[str, List[EventLike]]:
    """
    按 ``correlation_id`` 分组（narrative group，**不是** occurrence identity）。

    与 InnerLifeWriter.get_events_by_correlation / NarrativeTraceReader
    .query_by_correlation_id 语义一致（同一数据字段），本函数是极薄纯函数分组形态。

    Args:
        events: InnerLifeEvent 实例或 dict 的可迭代对象。

    Returns:
        dict[correlation_id, list[event]]（ephemeral）。无该 key（None）的事件不进组。
    """
    return _group(events, _FIELD_CORRELATION)


def group_by_session(
    events: Iterable[EventLike],
) -> Dict[str, List[EventLike]]:
    """
    按 ``session_id`` 分组（runtime session anchor，**不是** occurrence identity）。

    与 InnerLifeWriter.get_events_by_session / NarrativeTraceReader
    .query_by_session_id 语义一致（同一数据字段），本函数是极薄纯函数分组形态。

    Args:
        events: InnerLifeEvent 实例或 dict 的可迭代对象。

    Returns:
        dict[session_id, list[event]]（ephemeral）。无该 key（None）的事件不进组。
    """
    return _group(events, _FIELD_SESSION)
