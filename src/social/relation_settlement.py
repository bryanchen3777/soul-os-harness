"""
src/social/relation_settlement.py — SG-2 关系演化沉淀层（D3, 24h/agent 窗口）

设计来源: docs/SG-1-SOCIAL-GRAPH-CONTRACT.md（§4 信号源与节流 / §7 防线）

职责: 每 agent 每 24h 至多 1 次关系演化评估（挂 scheduler._goal_scan_all
30s wake 并列分支, 0 新定时器）。评估 = 只读现查窗口信号 → 整数计数增量 →
RelationshipsStore.apply_relation_evaluation（唯一写入口, 带状态机 + 幂等 ref）。

信号口径（契约 §3.2/§4.1, 既有载体 0 新事件类型）:
  - reply_exchanges    : perception_trace.jsonl 窗口内 event_type="reply" 事件
    成对折抵 = min(对方 reply 事件数, 我方 reply 事件数) — 无 source_event_id
    持久化的 v1 近似（诚实标注, 契约歧义见报告）
  - co_presence_sessions: interactions.jsonl 窗口内 agents 同时含 agent & other
    的 session 记录数（同客厅共在, 既有 cross_chat/shared_event 载体）
  - dream_exchanges    : v1 无方向性持久载体（diary dream entry 不含 target,
    on_dream 只有 legacy touch）→ 生产计数恒 0, 状态机 dream 门保留
    （契约 §4.2.2 评估输入只列 reply/co-presence, 与契约字面一致）

节流与幂等（契约 §4.2）:
  - 24h/agent 窗口: GoalProviderState.last_relation_update_at（复用
    GOAL_QUOTA_WINDOW_SECONDS, sidecar 同构 last_seed_scan_at 先例）
  - 幂等: apply_relation_evaluation 带 last_relation_update_ref = rel:<other>:<ts>,
    同一信号窗口不重复写

防线: 0 直通 publish / 0 新定时器 / 0 直写 SAGE facts / 0 LLM / 0 打分
（本模块 0 import agency/actuator/tool_registry; 只读写 relationships.json +
sidecar goal_provider.json）。

Frozen contract 边界: 不触碰 Agency/TriggerEnvelope/InnerLifeEvent/handlers/
SAGE/SubmissionGate/Decision 主文本; 唯一写入面 = RelationshipsStore（D1 契约
§2.4 物理隔离双保证）+ goal_provider.json sidecar（additive 字段）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.goals.motive_provider import GOAL_QUOTA_WINDOW_SECONDS

logger = logging.getLogger("soul_os.social.relation_settlement")

# 信号窗口（契约 §3.3: 24h 评估窗口）
SIGNAL_WINDOW_HOURS = 24
WINDOW_SECONDS = SIGNAL_WINDOW_HOURS * 3600  # 全整数秒


def _data_root() -> Path:
    from src.paths import data_root
    return data_root()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """只读 jsonl（坏行跳过 + warning, 0 raise; 对齐 seed_provider 风格）。"""
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError as e:
        logger.warning(f"[RelSettle] jsonl 读取失败 (fail-closed): {e}")
    return records


def _ts_to_dt(ts: Any) -> Optional[datetime]:
    """宽容解析 ISO 时间戳（坏值 → None, 不 crash）。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def collect_window_signals(
    agent_id: str,
    now: datetime,
    *,
    window_hours: int = SIGNAL_WINDOW_HOURS,
    base_dir: Optional[Path] = None,
) -> Dict[str, Dict[str, int]]:
    """只读现查 24h 窗口信号计数（确定性聚合, 0 写）。

    Returns:
        {other_id: {"reply": int, "co_presence": int, "dream": int}}
        仅含窗口内有信号的 other（0 信号者不进结果）
    """
    root = base_dir or _data_root()
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    window_start = now_utc - timedelta(hours=window_hours)

    out: Dict[str, Dict[str, int]] = {}

    # ── reply 信号（perception_trace.jsonl, 既有中间件写）──
    perception_path = root / "world" / "perception_trace.jsonl"
    reply_by_actor: Dict[str, int] = {}
    for rec in _read_jsonl(perception_path):
        if rec.get("event_type") != "reply":
            continue
        extra = rec.get("extra") or {}
        if extra.get("event_kind") != "social":
            continue
        ts = _ts_to_dt(rec.get("timestamp"))
        if ts is None or ts < window_start or ts > now_utc:
            continue
        actor = str(extra.get("actor_id") or "").strip()
        if not actor:
            continue
        reply_by_actor[actor] = reply_by_actor.get(actor, 0) + 1
    my_replies = reply_by_actor.get(agent_id, 0)
    for actor, n in reply_by_actor.items():
        if actor == agent_id:
            continue
        # 成对折抵（契约 §3.2: 双向 reply 回合, 成对折抵计 1）:
        # v1 无 source_event_id 持久化, 用 min(双方 reply 事件数) 保守近似
        pair = min(n, my_replies) if my_replies > 0 else 0
        if pair > 0:
            out.setdefault(actor, {})["reply"] = pair

    # ── co_presence 信号（interactions.jsonl, 既有 cross_chat/shared_event）──
    interactions_path = root / "soul" / "interactions.jsonl"
    co_by_other: Dict[str, int] = {}
    for rec in _read_jsonl(interactions_path):
        agents = rec.get("agents") or []
        if not isinstance(agents, list) or agent_id not in agents:
            continue
        ts = _ts_to_dt(rec.get("ts"))
        if ts is None or ts < window_start or ts > now_utc:
            continue
        for other in agents:
            if other == agent_id:
                continue
            co_by_other[other] = co_by_other.get(other, 0) + 1
    for other, n in co_by_other.items():
        out.setdefault(other, {})["co_presence"] = n

    # dream: v1 无方向性持久载体 → 恒 0（契约 §4.2.2 评估输入不含 dream）
    return out


def settle_relations(
    agent_id: str,
    now: Optional[datetime] = None,
    *,
    base_dir: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """关系演化沉淀评估（挂 scheduler._goal_scan_all 30s wake 并列分支）。

    流程（契约 §4.2）:
      1. 24h 节流（sidecar last_relation_update_at, 复用 GOAL_QUOTA_WINDOW_SECONDS）
      2. 只读现查窗口信号（collect_window_signals）
      3. 对每个有信号 / 需要降带检查的 entry 调
         RelationshipsStore.apply_relation_evaluation（唯一写入口, 幂等 ref）
      4. 无信号的 entry 也过降带检查（30 天 stale → 降 1 带）

    Returns:
        {"skipped": str|None, "updated": int, "demoted": int}
        fail-closed: 任何异常只 log warning 不 raise（scheduler 主循环不受阻）
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    st = now.timestamp()

    # 1) 24h/agent 节流（sidecar 同构: GoalProviderState.last_relation_update_at）
    try:
        from src.goals.motive_provider import GoalMotiveProvider
        provider = GoalMotiveProvider.for_agent(agent_id)
        state = provider._load_state()
    except Exception as e:
        logger.warning(
            f"[RelSettle] sidecar 读取失败 (fail-closed 跳过本轮): "
            f"agent={agent_id} {type(e).__name__}: {e}"
        )
        return {"skipped": "sidecar_error", "updated": 0, "demoted": 0}
    if not force and st - state.last_relation_update_at < GOAL_QUOTA_WINDOW_SECONDS:
        logger.debug(
            f"[RelSettle] 24h 节流窗内跳过 agent={agent_id} "
            f"(last={state.last_relation_update_at:.0f})"
        )
        return {"skipped": "throttle", "updated": 0, "demoted": 0}

    # 2) 窗口信号现查（只读; 失败 → 空信号, 仍走降带检查）
    signals: Dict[str, Dict[str, int]] = {}
    try:
        signals = collect_window_signals(
            agent_id, now, base_dir=base_dir
        )
    except Exception as e:
        logger.warning(
            f"[RelSettle] 窗口信号收集异常 (fail-closed 空信号): "
            f"agent={agent_id} {type(e).__name__}: {e}"
        )

    # 3) 读 relationships（agent 自己的 store）; 失败 → 跳过本轮
    try:
        from src.soul.relationships import get_relationships_manager
        store = get_relationships_manager(
            data_dir=str(base_dir / "soul") if base_dir else None
        ).get_store(agent_id)
    except Exception as e:
        logger.warning(
            f"[RelSettle] relationships 读取失败 (fail-closed 跳过): "
            f"agent={agent_id} {type(e).__name__}: {e}"
        )
        return {"skipped": "store_error", "updated": 0, "demoted": 0}

    others = store.get_all()
    now_iso = now.isoformat()
    ref_ts = now_iso
    updated = 0
    demoted = 0
    band_order = {
        "stranger": 0, "known": 1, "familiar": 2, "close": 3,
    }
    for other_id in others:
        counts = signals.get(other_id, {})
        reply = int(counts.get("reply", 0))
        co = int(counts.get("co_presence", 0))
        dream = int(counts.get("dream", 0))
        # 每个对子都过评估: 有信号 → 增量 + 升带; 无信号 → 0 增量, apply 内部
        # 走 30 天降带检查（stale → 降 1 带）与慢爬评估（幂等）
        before = store.get(other_id)
        band_before = (
            before.get("relational_band", "stranger") if before else "stranger"
        )
        entry = store.apply_relation_evaluation(
            other_id,
            reply_exchanges_delta=reply,
            co_presence_sessions_delta=co,
            dream_exchanges_delta=dream,
            ref=f"rel:{other_id}:{ref_ts}",
            now_iso=now_iso,
        )
        band_after = entry.get("relational_band", "stranger")
        if band_order.get(band_after, 0) < band_order.get(band_before, 0):
            demoted += 1
            logger.info(
                f"[RelSettle] {agent_id}→{other_id} 降带: {band_before}→{band_after} "
                f"(30 天无新信号)"
            )
        elif band_after != band_before:
            logger.info(
                f"[RelSettle] {agent_id}→{other_id} 升带: {band_before}→{band_after}"
            )
        if reply or co or dream:
            updated += 1

    # 5) sidecar 节流戳（任何成功评估都推进; 幂等 ref 保证不重复写）
    state.last_relation_update_at = st
    try:
        provider._save_state(state)
    except Exception as e:
        logger.warning(f"[RelSettle] sidecar 保存失败: {type(e).__name__}: {e}")

    return {"skipped": None, "updated": updated, "demoted": demoted}


__all__ = [
    "SIGNAL_WINDOW_HOURS",
    "collect_window_signals",
    "settle_relations",
]