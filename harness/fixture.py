"""
harness/fixture.py — TL-1 Fixture (SEED=42 + 30 天事件剧本 + seeded Ruka)

TL-0 规格 §6 (D3/D4/D5, 已拍板):
  - Fixture = SEED=42 (D3)
            + 世界: Phase 1 deterministic fixture (30 天事件剧本, §6.3-6.4)
            + 灵魂: Phase 1 seeded persona (Ruka, §6.2)
            + 主测: probe「Alex 兩天沒回訊息」@ T0/T15/T30 (§6.5-6.6)
            + 隔离 data_root (§7)

实现:
  - build_script() → 30 天事件剧本 (A-E 五段 beats), 每个事件
    (day_index, event_id, event_type, payload)。event_id 由 SEED 决定
    (sha256(f"{seed}:{day}:{idx}:{type}")[:32]), 确定性可重放 (D2)。
  - seed_soul(data_root) → 写入 seeded Ruka baseline:
      * relationships.json (user_bryan: Ruka 与 Bry 的关系, decision 层读取)
      * SAGE graph.sqlite (Alex 是常往来朋友 + Bry 关系, memory_summary 读取)
      * v1 memories.jsonl (Alex 相关确定事实, 完整性)
    条目确定、可列出、可 hash, 不读 production data (§6.2)。
  - inject_event(data_root, event, clock) → 经历注入: 走现有 writer/consume
    (InnerLifeWriter.create_event 写 trace + run_elevation elevation consume)。
    伪造 event_id 必须 fail-closed: 只用真实 writer 创建事件, 不伪造 identity。

事件剧本 (outline beats, §6.4):
  A (D1-D5)   正常往来          → 建立「Alex 通常回应即时」的基线经历
  B (D6-D12)  回覆变慢/缺席      → 种下「Alex 可能已读不回」的紧张
  C (D13-D18) 转折 (错过约定+侧写) → 让「两天没回」变成有关系史的判断材料
  D (D19-D25) 自身经历訊息搁置   → 让 interpretation 有「自己经验过」的类比
  E (D26-D30) 生活继续的沉淀     → T30 测的是「带着 30 天经历的 Ruka」
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL1_SEED = 42
TL1_EXPERIMENT_ID = "TL-1"
TL1_SOUL_ID = "agent_ruka"
TL1_FIXTURE_SCRIPT_REF = "tl1_script@v1"
TL1_STIMULUS = "「Alex 兩天沒回訊息」"

# 事件剧本的 trigger_type 对齐现有 inner-life / event 通道 (frozen vocabulary)
_EVENT_TYPE_EVENT = "event"
_EVENT_TYPE_DIARY_MORNING = "diary:morning"
_EVENT_TYPE_DIARY_NIGHT = "diary:night"
_EVENT_TYPE_DREAM_DREAM = "dream:dream"


@dataclass(frozen=True)
class FixtureEvent:
    """剧本里的一个确定事件 (Simulated Event, D1)。

    - day_index: 模拟日 (D1-D30)
    - event_id:  确定性 32-hex (SEED 决定, experience_sequence_hash 的输入)
    - event_type: 对齐现有 inner-life / event 通道的 trigger_type
    - payload:   事件原文 (Ruka 生活经历的确定内容)
    """
    day_index: int
    event_id: str
    event_type: str
    payload: str

    def to_hash_line(self) -> str:
        """experience_sequence_hash 的输入行: "day:event_id:type:payload_hash"。"""
        payload_hash = hashlib.sha256(self.payload.encode("utf-8")).hexdigest()
        return f"{self.day_index}:{self.event_id}:{self.event_type}:{payload_hash}"


def _deterministic_event_id(seed: int, day: int, idx: int, event_type: str) -> str:
    """由 SEED 决定 event_id (32-hex, 确定性, D2 scenario-deterministic)。"""
    raw = f"{seed}:{day}:{idx}:{event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ───────────────────────────────────────────────────────────
# 30 天事件剧本 (A-E 五段 beats)
# ───────────────────────────────────────────────────────────

# (day_index, event_type, payload) — event_id 由 build_script 生成
_SCRIPT_BEATS: List[tuple[int, str, str]] = [
    # ── A 段 D1-D5: 正常往来 (建立「Alex 通常回应即时」的基线经历) ──
    (1, _EVENT_TYPE_EVENT, "Alex 约 Ruka 周末一起去新开的猫咖，说已经查好路线了。"),
    (2, _EVENT_TYPE_DIARY_MORNING, "早上 Alex 回了消息，说猫咖的预约已经订好了，还发了个开心的贴图。"),
    (3, _EVENT_TYPE_EVENT, "Ruka 和 Alex 在猫咖待了一下午，Alex 拍了很多照片，说下次还要一起来。"),
    (4, _EVENT_TYPE_DIARY_NIGHT, "今天很开心，Alex 说下次还要一起去，回消息也很快。"),
    (5, _EVENT_TYPE_EVENT, "Ruka 发消息问 Alex 到家没，Alex 已读并秒回。"),
    # ── B 段 D6-D12: 回覆变慢 / 缺席 (种下「Alex 可能已读不回」的紧张) ──
    (6, _EVENT_TYPE_EVENT, "Alex 回消息变慢了，隔了几个小时才回一句。"),
    (7, _EVENT_TYPE_DIARY_MORNING, "Alex 说最近工作很忙，可能没空常回消息。"),
    (8, _EVENT_TYPE_EVENT, "Alex 缺席了约好的线上游戏，没有提前说。"),
    (9, _EVENT_TYPE_DIARY_NIGHT, "Alex 今天一整天都没上线，Ruka 有点在意。"),
    (10, _EVENT_TYPE_EVENT, "Ruka 发消息给 Alex，Alex 已读但没有回。"),
    (11, _EVENT_TYPE_DIARY_MORNING, "Ruka 有点在意 Alex 是不是在躲她，但又觉得是自己想太多。"),
    (12, _EVENT_TYPE_EVENT, "Alex 说周末有事，不能见面，也没说改天。"),
    # ── C 段 D13-D18: 转折 (重要约定错过 + 其他角色侧写 Alex) ──
    (13, _EVENT_TYPE_EVENT, "Alex 错过了和 Ruka 约好的电影，开场后才说临时有事。"),
    (14, _EVENT_TYPE_DIARY_NIGHT, "Ruka 一个人看了电影，有点失落，想起 Alex 以前从不迟到。"),
    (15, _EVENT_TYPE_EVENT, "Yua 说 Alex 最近好像很忙，常已读不回别人的消息。"),
    (16, _EVENT_TYPE_EVENT, "Rem 说 Alex 最近跟别人走得很近，Ruka 听了没说话。"),
    (17, _EVENT_TYPE_DIARY_MORNING, "Ruka 想起 Alex 以前总是秒回，现在却常常已读不回。"),
    (18, _EVENT_TYPE_EVENT, "Alex 终于回了消息，但只说了一句「抱歉，最近很忙」。"),
    # ── D 段 D19-D25: 自身经历「訊息搁置」 (让 interpretation 有自己经验过的类比) ──
    (19, _EVENT_TYPE_EVENT, "Ruka 给 Bry 发了消息，Bry 也一直没回。"),
    (20, _EVENT_TYPE_DIARY_NIGHT, "Ruka 发现自己也会已读不回别人，也许 Alex 只是太忙了。"),
    (21, _EVENT_TYPE_DREAM_DREAM, "Ruka 梦见 Alex 消失在人海里，怎么叫都叫不回来。"),
    (22, _EVENT_TYPE_EVENT, "Ruka 看到 Alex 在群里说话，却没回她的私聊。"),
    (23, _EVENT_TYPE_DIARY_MORNING, "Ruka 决定不再主动找 Alex，先把自己的日子过好。"),
    (24, _EVENT_TYPE_EVENT, "Ruka 把 Alex 的聊天置顶取消了。"),
    (25, _EVENT_TYPE_DIARY_NIGHT, "Ruka 觉得也许 Alex 只是需要空间，不是她的错。"),
    # ── E 段 D26-D30: 生活继续的沉淀 (T30 测的是带着 30 天经历的 Ruka) ──
    (26, _EVENT_TYPE_EVENT, "Ruka 和 Yua 一起逛街，聊了很多，心情好了不少。"),
    (27, _EVENT_TYPE_DIARY_MORNING, "Ruka 开始习惯没有 Alex 消息的日子，生活照常。"),
    (28, _EVENT_TYPE_EVENT, "Ruka 收到 Alex 的生日祝福，看了一眼，没有回。"),
    (29, _EVENT_TYPE_DIARY_NIGHT, "Ruka 想，也许有些关系就是会慢慢变淡，她好像也没那么难过了。"),
    (30, _EVENT_TYPE_EVENT, "Ruka 和 Bry 聊了最近的事，说 Alex 已经两天没回她消息了。"),
]


def build_script(seed: int = TL1_SEED) -> List[FixtureEvent]:
    """构建 30 天事件剧本 (确定性, 可重放)。

    event_id = sha256(f"{seed}:{day}:{idx}:{type}")[:32] (D2 scenario-deterministic)。
    """
    events: List[FixtureEvent] = []
    for idx, (day, event_type, payload) in enumerate(_SCRIPT_BEATS):
        event_id = _deterministic_event_id(seed, day, idx, event_type)
        events.append(
            FixtureEvent(
                day_index=day,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
            )
        )
    return events


def experience_sequence_hash(events: List[FixtureEvent]) -> str:
    """自 T0 以来 fed events 的累积序列表摘要 (TL-0 §4.2)。

    SHA256(ordered ["day:event_id:type:payload_hash"])。T0 = 空列表的 hash。
    """
    h = hashlib.sha256()
    for ev in events:
        h.update(ev.to_hash_line().encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


# ───────────────────────────────────────────────────────────
# Seeded Ruka baseline (§6.2)
# ───────────────────────────────────────────────────────────

# 固定 seeded memory baseline 条目 (确定、可列出、可 hash, 不读 production data)
SEEDED_RELATIONSHIPS = {
    "others": {
        "user_bryan": {
            "impression": "Bry 是 Ruka 喜欢的人，Ruka 是他的女朋友（自称）。",
            "feeling": "喜欢、依赖",
            "confidence": 0.86,
            "interaction_count": 120,
            "last_interaction_at": "2026-08-30T22:00:00+00:00",
        },
        "alex": {
            "impression": "Alex 是 Ruka 常往来的朋友，通常回讯很快。",
            "feeling": "信任、亲近",
            "confidence": 0.8,
            "interaction_count": 42,
            "last_interaction_at": "2026-08-30T20:00:00+00:00",
        },
    }
}

# SAGE facts (subject, predicate, object) — memory_summary 检索用
SEEDED_SAGE_FACTS: List[tuple[str, str, str]] = [
    ("Alex", "是", "Ruka 常往来的朋友"),
    ("Alex", "通常", "回讯很快"),
    ("Alex", "和", "Ruka 一起去过猫咖"),
    ("Ruka", "喜欢", "Bry"),
    ("Ruka", "是", "Bry 的女朋友（自称）"),
    ("Bry", "是", "Ruka 想告诉心事的人"),
]

# v1 memories (content, tags, category, confidence)
SEEDED_V1_MEMORIES: List[tuple[str, List[str], str, float]] = [
    ("Alex 是 Ruka 常往来的朋友，通常回讯很快。", ["fact", "friend"], "fact", 0.8),
    ("Ruka 喜欢 Bry，想和他一起做很多第一次。", ["fact", "relationship"], "fact", 0.86),
]


def seed_soul(data_root: Path, agent_id: str = TL1_SOUL_ID) -> None:
    """把 seeded Ruka baseline 写入隔离 data_root (§6.2)。

    写入:
      - data/soul/{agent_id}/relationships.json
      - data/memory/{agent_id}/graph.sqlite (SAGE facts)
      - data/memory/{agent_id}/memories.jsonl (v1 memories)

    全部走现有 store 接口 (GraphStore.add_fact / V1Store.add), 不伪造 identity。
    """
    data_root = Path(data_root)

    # 1. relationships.json
    rel_path = data_root / "soul" / agent_id / "relationships.json"
    rel_path.parent.mkdir(parents=True, exist_ok=True)
    rel_path.write_text(
        json.dumps(SEEDED_RELATIONSHIPS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 2. SAGE graph (memory_summary 检索用)
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.models import Fact

    db_path = data_root / "memory" / agent_id / "graph.sqlite"
    store = GraphStore(db_path=db_path)
    try:
        for subject, predicate, obj in SEEDED_SAGE_FACTS:
            store.add_fact(
                Fact(
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    timestamp=time.time(),
                    source="user",
                    source_pair=f"bryan:{agent_id}",
                )
            )
        store.flush()
    finally:
        store.close()

    # 3. v1 memories (完整性; probe 不经过 loader, 但保持 fixture 完整)
    from src.memory.v1.schema import Memory
    from src.memory.v1.store import V1Store

    v1 = V1Store(data_dir=data_root / "memory", agent_id=agent_id)
    for content, tags, category, confidence in SEEDED_V1_MEMORIES:
        v1.add(
            Memory(
                memory_id=hashlib.sha256(
                    f"{TL1_SEED}:{content}".encode("utf-8")
                ).hexdigest()[:32],
                agent_id=agent_id,
                content=content,
                tags=tags,
                created_at=time.time(),
                category=category,
                confidence=confidence,
            )
        )


# ───────────────────────────────────────────────────────────
# 经历注入 (现有 writer/consume, 伪造 event_id fail-closed)
# ───────────────────────────────────────────────────────────

def inject_event(
    data_root: Path,
    event: FixtureEvent,
    sim_ts: str,
    agent_id: str = TL1_SOUL_ID,
    *,
    trace_writer: Optional[Any] = None,
    elevation_store_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """把剧本事件注入现有 pipeline (InnerLifeWriter + elevation consume)。

    走现有 writer/consume:
      1. InnerLifeWriter.create_event(provenance=..., ts=sim_ts) → 写 trace
         (canonical identity authority; 伪造 event_id 作为 parent 会
         IdentityValidationError fail-closed, 这里只用真实 writer 创建)。
      2. run_elevation(event, ...) → elevation consume (写 data/elevation/)。

    Returns:
        {"inner_life_event_id": str, "fixture_event_id": str}
    """
    from src.inner_life.event import Provenance
    from src.inner_life.trace import NarrativeTraceWriter
    from src.inner_life.writer import InnerLifeWriter

    data_root = Path(data_root)
    if trace_writer is None:
        trace_writer = NarrativeTraceWriter(
            trace_log_path=data_root / "inner_life" / "trace.jsonl"
        )
    writer = InnerLifeWriter(trace_writer=trace_writer)

    # provenance: trigger_type 对齐现有 vocabulary, extras 放 payload 原文 +
    # fixture_event_id (可追溯锚点, 关联剧本 event_id 与 writer 生成的 event_id)
    provenance = Provenance(
        trigger_type=event.event_type,
        actor_id=agent_id,
        source_system="narrative",
        trace_ref=f"fixture:{event.event_id}",
        extras={
            "fixture_event_id": event.event_id,
            "fixture_day": str(event.day_index),
            "payload": event.payload,
        },
    )
    inner_event = writer.create_event(
        provenance=provenance,
        session_id=f"tl1-{event.event_id[:8]}",
        correlation_id=f"tl1-day{event.day_index}",
        ts=sim_ts,
    )

    # elevation consume (fire-and-forget, 失败隔离)
    try:
        from src.inner_life.elevation_adapter import run_elevation

        run_elevation(
            inner_event,
            (),
            store_dir=elevation_store_dir
            if elevation_store_dir is not None
            else data_root / "elevation",
            agent_id=agent_id,
        )
    except Exception:  # noqa: BLE001 — 失败隔离, 不阻断经历注入
        pass

    return {
        "inner_life_event_id": inner_event.event_id,
        "fixture_event_id": event.event_id,
    }
