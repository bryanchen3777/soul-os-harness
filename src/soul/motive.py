"""
src/soul/motive.py — Soul OS SM-3 Soul Motive Module

SM-3 (2026-08-30, IMPLEMENTATION): volition path 的 Motive 环。

设计来源:
  - docs/SOUL-MOTIVE-DECISION-DESIGN.md (SM-1, Q1/Q2)
  - docs/DECISION-PROMPT-CONTRACT.md (SM-2, §6 motive 生命周期)

核心概念:
  - Motive = Soul 的「念头」(interpretation 产物), 不是经历。
  - Thought source = Inner Life 的 diary/dream/event (frozen InnerLifeEvent, 只读)。
  - motive 是 Soul 的 LLM 解读经历产出的「念头」, 不是硬编码模板、不是 longing 公式。
  - SM-4.1 (Motive 多元化): 念头类型由经历性质决定, 不只「想告诉 Bry」——
    observe (环境刺激) / reflect (夜间/久未联络) / transmit (重大事件/紧迫事项)。
    motive_type 是 trace 的 observability 字段, 不进 Motive dataclass (5 字段冻结)。
  - motive 不成为 InnerLifeEvent, 只通过 provenance_ref 引用它 (Bryan 拍板)。
  - motive 不反向依赖 scheduler: 记录不含 trigger_type (验收 E)。

Frozen contract 边界 (0 change):
  - 不碰 InnerLifeEvent / Provenance / NarrativeTraceReader (只读 trace)
  - 不碰 4 handlers / SAGE / AGENCY_TRIGGER payload
  - 不建 Qualification / scoring / MessageWorthiness subsystem
  - 唯一写入 = motive trace (独立 append-only JSONL, data/soul/motive_trace.jsonl)

motive 生命周期 (DECISION-PROMPT-CONTRACT §6, v1):
  pending → transmitted (transmit 后) | rejected (observe/reflect/do_nothing 后, 终态, 不重试)
  pending 超过 TTL (默认 24h, 可配置) → expired (惰性标记)
  (SM-4: Decision 四元 transmit/observe/reflect/do_nothing; observe/reflect 的执行逻辑是后续工单,
   scheduler 层面 observe/reflect/do_nothing 均不 publish → rejected)
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("soul_os.soul.motive")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

# motive 生命周期状态
MOTIVE_STATUS_PENDING = "pending"
MOTIVE_STATUS_TRANSMITTED = "transmitted"
MOTIVE_STATUS_REJECTED = "rejected"
MOTIVE_STATUS_EXPIRED = "expired"

# pending motive TTL (DECISION-PROMPT-CONTRACT §6: 建议 24h, 可配置)
MOTIVE_TTL_HOURS = 24

# interpretation 查询窗口 (跟 M5.8-4 GATE_QUERY_WINDOW_HOURS 同款 bounded query)
MOTIVE_INTERPRET_WINDOW_HOURS = 24

# interpretation LLM 参数 (Soul 自由表达, 跟 diary 同档)
INTERPRET_MAX_TOKENS = 200
INTERPRET_TEMPERATURE = 0.7

# Decision LLM 参数 (选择要稳定, 低温度; 待 Owner 拍板, 可配置)
DECISION_MAX_TOKENS = 200
DECISION_TEMPERATURE = 0.3

# SM-4.1: motive 候选类型 (多元化, 不只 transmit)
#   - observe  — 环境刺激 (天气变化/时段切换) → 「想观察确认」
#   - reflect  — 夜间时段 / 长期未联络 → 「想回顾回忆」
#   - transmit — 重大生活事件 / 紧迫事项 → 「想告诉 Bry」
# motive_type 是 interpretation 的 observability 字段 (trace 记录),
# 不进 Motive dataclass (5 字段冻结), 不参与 Decision 判定。
MOTIVE_TYPES = ("observe", "reflect", "transmit")

# target 常量 (v1 固定指向 Bry)
TARGET_BRYAN = "bryan"

# ───────────────────────────────────────────────────────────
# D2 (Owner 拍板, SG-1 §5.1): Motive.target 值域解冻 + 出口 Validator
#   - 值域: {"bryan"} ∪ AGENT_IDS, 其中 AGENT_IDS = canonical agent 注册表
#     (scheduler.register 先例, scheduler.py:541-552; run_server 全链路注入)
#   - 生成出口 fail-closed: target 不在值域 → make_motive 抛 InvalidMotiveTargetError
#     (调用方 catch → motive 不产生 / 不进入 Decision; 0 静默放行)
#   - Motive 其余 5 字段与结构冻结不动; 0 新投递通道 (契约 §5.3: 复用既有公开频道)
# ───────────────────────────────────────────────────────────

# process-global agent 注册表 (对齐 set_llm_proxy pattern, 0 依赖 scheduler 实例)
_AGENT_IDS: set = set()
_AGENT_IDS_LOCK = threading.Lock()


def register_agent_id(agent_id: str) -> None:
    """注册一个 canonical agent_id 到 target 值域 (scheduler.register 同步注入)。"""
    with _AGENT_IDS_LOCK:
        _AGENT_IDS.add(agent_id)


def set_agent_ids(agent_ids: List[str]) -> None:
    """整体覆写 agent 注册表 (测试隔离 / 一次性注入)。"""
    with _AGENT_IDS_LOCK:
        _AGENT_IDS.clear()
        for a in agent_ids:
            if isinstance(a, str) and a:
                _AGENT_IDS.add(a)


def get_agent_ids() -> frozenset:
    """当前 target 值域里的 agent id 集合 (只读快照)。"""
    with _AGENT_IDS_LOCK:
        return frozenset(_AGENT_IDS)


def validate_motive_target(target: str) -> bool:
    """D2 值域校验: target ∈ {"bryan"} ∪ AGENT_IDS。

    Returns:
        True 合法; False 非法 (fail-closed 拒绝该 motive)
    """
    if not isinstance(target, str):
        return False
    if target == TARGET_BRYAN:
        return True
    with _AGENT_IDS_LOCK:
        return target in _AGENT_IDS


class InvalidMotiveTargetError(ValueError):
    """D2(SG-1 §5.1): target 不在 {"bryan"} ∪ AGENT_IDS → fail-closed 拒绝。"""

    def __init__(self, target: Any) -> None:
        self.target = target
        super().__init__(
            f"非法 motive target: {target!r} (允许: bryan + 已注册 agent_ids)"
        )


def make_motive(
    *,
    motive_id: str,
    content: str,
    target: str,
    provenance_ref: str,
    created_at: str,
) -> Motive:
    """Motive 生成出口统一工厂 (fail-closed 校验, D2)。

    target 不在值域 → 抛 InvalidMotiveTargetError (调用方 catch → 丢弃该 motive,
    0 静默放行)。Motive 5 字段与结构冻结不动。
    """
    if not validate_motive_target(target):
        raise InvalidMotiveTargetError(target)
    return Motive(
        motive_id=motive_id,
        content=content,
        target=target,
        provenance_ref=provenance_ref,
        created_at=created_at,
    )

# trigger_type → diary jsonl slot 映射 (provenance 解析用)
_TRIGGER_TO_SLOT = {
    "diary:morning": "morning",
    "diary:night": "night",
    "dream:dream": "dream",
    "dream:event": "event",
}


# ───────────────────────────────────────────────────────────
# Motive dataclass (SM-1 Q1, 工单锁定 5 字段)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Motive:
    """
    Soul 的「我想告诉 Bry」意图 (interpretation 产物)。

    字段 (SM-3 工单锁定):
      - motive_id: 唯一身份 (32 hex, 参照 InnerLifeEvent.event_id 模式)
      - content:   想说什么 (Soul 自己的话, 非模板填充)
      - target:    指向谁 (v1 固定 "bryan")
      - provenance_ref: 引用产生这个 motive 的 InnerLifeEvent.event_id
      - created_at: ISO 8601 UTC

    motive ≠ InnerLifeEvent: 意图不是经历, 不进入 lineage tree,
    只通过 provenance_ref 回查「这个念头从哪次经历来」。
    """
    motive_id: str
    content: str
    target: str
    provenance_ref: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "motive_id": self.motive_id,
            "content": self.content,
            "target": self.target,
            "provenance_ref": self.provenance_ref,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Motive":
        return cls(
            motive_id=d["motive_id"],
            content=d["content"],
            target=d["target"],
            provenance_ref=d["provenance_ref"],
            created_at=d["created_at"],
        )


def new_motive_id() -> str:
    """生成 32 hex motive_id (参照 InnerLifeEvent.event_id 模式)。"""
    return uuid.uuid4().hex


def now_utc_iso() -> str:
    """ISO 8601 UTC (跟 inner_life/identity.py now_utc_iso 同格式)。"""
    return datetime.now(timezone.utc).isoformat()


def motive_from_social_opportunity(
    opp: Any,  # SocialOpportunity
    soul_name: str = "",
) -> Motive:
    """
    SI-3 Phase 2 (2026-09-03): 將有效的 SocialOpportunity 轉換為 Motive 候選
    (SM-1/SM-3 兼容)。嚴格維持 Motive 5 字段凍結
    (motive_id, content, target, provenance_ref, created_at)。

    純函數: 不進 class, 不依賴全局狀態, 不寫任何 trace/檔案。
    只接受未過期之機會 (TTL 修剪由 SocialOpportunityBuffer 負責, fail-closed)。

    Args:
        opp: SocialOpportunity (帶 300s TTL 的短期社交機會)
        soul_name: 保留參數 (未來可注入靈魂名, 目前不影響 content)

    Returns:
        Motive (target 固定指向 Bry, provenance_ref 引用 opp:opportunity_id)
    """
    content = f"關於 {opp.actor_id} 在客廳提到的話題「{opp.topic}」"
    # created_at: opp 是 epoch 秒 (float), Motive 凍結為 ISO 8601 UTC (str)。
    # 壞值 → fail-safe 用當前時間 (不 crash, 不阻斷決策管線)。
    try:
        created_dt = datetime.fromtimestamp(float(opp.created_at), tz=timezone.utc)
        created_at = created_dt.isoformat()
    except (TypeError, ValueError, OSError):
        created_at = now_utc_iso()
    # D2 (SG-1 §5.1): 出口统一 fail-closed 校验 (target=TARGET_BRYAN 恒合法;
    # 未来 agent-target 扩展若注册表缺失 → 抛 InvalidMotiveTargetError, 0 静默放行)
    return make_motive(
        motive_id=f"mot_{uuid.uuid4().hex[:12]}",
        content=content,
        target=TARGET_BRYAN,
        provenance_ref=f"opp:{opp.opportunity_id}",
        created_at=created_at,
    )


# ───────────────────────────────────────────────────────────
# MotiveTraceStore — 独立 append-only JSONL
# ───────────────────────────────────────────────────────────

class MotiveTraceStore:
    """
    motive trace 存储 (data/soul/motive_trace.jsonl, append-only)。

    记录 = 完整快照 (Motive 字段 + agent_id + status + updated_at),
    状态变更 = append 新快照行。resolve 时按 motive_id 取最后一行。

    与 InnerLifeEvent trace 分离 (语义分离: 经历 vs 意图)。
    """

    def __init__(self, trace_path: Optional[Path] = None) -> None:
        if trace_path is None:
            from src.paths import data_root
            trace_path = data_root() / "soul" / "motive_trace.jsonl"
        self._trace_path = Path(trace_path)

    # ── 读写 ─────────────────────────────────────────────

    def _read_all(self) -> List[Dict[str, Any]]:
        """读所有有效 JSON 行 (append 顺序)。坏行跳过 + warning。"""
        if not self._trace_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(self._trace_path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        records.append(json.loads(stripped))
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"[MotiveTraceStore] skipping malformed line {lineno}: {e}"
                        )
        except OSError as e:
            logger.warning(f"[MotiveTraceStore] read failed: {e}")
        return records

    def _append(self, record: Dict[str, Any]) -> None:
        """append 一条记录 (失败只 log warning, 不中断调用方)。"""
        try:
            self._trace_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._trace_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[MotiveTraceStore] append failed: {e}")

    def append_motive(
        self,
        motive: Motive,
        agent_id: str,
        motive_type: Optional[str] = None,
    ) -> None:
        """写入一条 pending motive 快照。

        SM-4.1: motive_type (observe/reflect/transmit) 作为 observability
        附加字段写入 trace (不进 Motive dataclass, 5 字段冻结)。
        """
        record = motive.to_dict()
        record["agent_id"] = agent_id
        record["status"] = MOTIVE_STATUS_PENDING
        record["updated_at"] = motive.created_at
        if motive_type in MOTIVE_TYPES:
            record["motive_type"] = motive_type
        self._append(record)
        logger.info(
            f"[MotiveTraceStore] motive 写入: {motive.motive_id} "
            f"agent={agent_id} provenance={motive.provenance_ref} "
            f"type={motive_type}"
        )

    def _latest_by_motive_id(self) -> Dict[str, Dict[str, Any]]:
        """按 motive_id 分组, 取每个 motive 的最后一行 (最新快照)。"""
        latest: Dict[str, Dict[str, Any]] = {}
        for r in self._read_all():
            mid = r.get("motive_id")
            if not isinstance(mid, str) or not mid:
                continue
            latest[mid] = r
        return latest

    def resolve_pending(
        self,
        agent_id: str,
        now: Optional[datetime] = None,
        ttl_hours: float = MOTIVE_TTL_HOURS,
    ) -> Optional[Motive]:
        """
        找该 agent 的 pending motive (未过期)。

        - 多个 pending → 取最新 (created_at 最大, 最近的念头)。
        - pending 超过 TTL → 惰性标记 expired, 不返回。
        - 无 pending → None (fail-closed 入口条件, 验收 A)。

        Returns:
            Motive 或 None
        """
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        latest = self._latest_by_motive_id()
        pending: List[Dict[str, Any]] = []
        for mid, rec in latest.items():
            if rec.get("agent_id") != agent_id:
                continue
            if rec.get("status") != MOTIVE_STATUS_PENDING:
                continue
            pending.append(rec)

        if not pending:
            return None

        # 按 created_at 排序取最新
        pending.sort(key=lambda r: r.get("created_at", ""))
        newest = pending[-1]

        # TTL 检查 (惰性 expired)
        created_at = newest.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age = (now - created_dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            # 坏 created_at: 不 crash, 视为未过期 (defensive)
            age = 0.0
        if age > ttl_hours:
            self.mark_expired(newest.get("motive_id", ""))
            logger.info(
                f"[MotiveTraceStore] motive {newest.get('motive_id')} "
                f"expired (age={age:.1f}h > ttl={ttl_hours}h)"
            )
            return None

        try:
            return Motive.from_dict(newest)
        except (KeyError, TypeError) as e:
            logger.warning(f"[MotiveTraceStore] malformed pending record: {e}")
            return None

    def mark_transmitted(self, motive_id: str) -> None:
        """motive 标记 transmitted (transmit 后)。"""
        self._mark_status(motive_id, MOTIVE_STATUS_TRANSMITTED)

    def mark_rejected(self, motive_id: str) -> None:
        """motive 标记 rejected (not_transmit 后, 终态, 不重试)。"""
        self._mark_status(motive_id, MOTIVE_STATUS_REJECTED)

    def mark_expired(self, motive_id: str) -> None:
        """motive 标记 expired (TTL 到期)。"""
        self._mark_status(motive_id, MOTIVE_STATUS_EXPIRED)

    def _mark_status(self, motive_id: str, status: str) -> None:
        """append 一条状态快照 (保留原字段, 更新 status + updated_at)。"""
        latest = self._latest_by_motive_id()
        rec = latest.get(motive_id)
        if rec is None:
            logger.warning(
                f"[MotiveTraceStore] mark {status} 失敗: motive {motive_id} 不存在"
            )
            return
        new_rec = dict(rec)
        new_rec["status"] = status
        new_rec["updated_at"] = now_utc_iso()
        self._append(new_rec)
        logger.info(
            f"[MotiveTraceStore] motive {motive_id} → {status} "
            f"(agent={rec.get('agent_id')})"
        )

    def known_provenance_refs(self) -> set:
        """所有已解释过的 provenance_ref 集合 (interpretation 去重用)。"""
        refs = set()
        for r in self._read_all():
            ref = r.get("provenance_ref")
            if isinstance(ref, str) and ref:
                refs.add(ref)
        return refs


# ───────────────────────────────────────────────────────────
# LLM 调用 (process-global LLMProxy, 跟 diary.py 同 pattern)
# ───────────────────────────────────────────────────────────

_global_llm_proxy = None


def set_llm_proxy(llm_proxy) -> None:
    """设定 process-global LLMProxy reference (run_server 注入)。"""
    global _global_llm_proxy
    _global_llm_proxy = llm_proxy


def _find_llm_proxy():
    """回传 process-global LLMProxy (无则 None)。"""
    return _global_llm_proxy


async def _default_llm_call(
    messages: List[Dict[str, str]],
    agent_id: str,
    max_tokens: int,
    temperature: float,
) -> Optional[str]:
    """
    默认 LLM 调用: process-global LLMProxy.generate_text。

    Proxy 来源 (依序):
      1. motive 自己的 process-global (set_llm_proxy, 未来 run_server 可注入)
      2. fallback 到 diary 的 process-global (run_server 已注入, v4-flash) —
         避免改 run_server.py (M3.1 frozen scope), 复用既有注入点。

    无 proxy (测试 / standalone) → None (fail-closed: 调用方按失败处理)。
    失败 → None, 不 raise (「拒绝问, 强制读」)。
    """
    proxy = _find_llm_proxy()
    if proxy is None:
        # Fallback: diary.py 的 process-global LLMProxy (run_server 已注入)
        try:
            from src.soul.diary import _find_llm_proxy as _diary_find_llm_proxy
            proxy = _diary_find_llm_proxy()
        except Exception:
            proxy = None
    if proxy is None:
        logger.warning(
            f"[Motive] 无 process-global LLMProxy, LLM 调用返回 None (fail-closed) "
            f"agent={agent_id}"
        )
        return None
    try:
        from src.llm.rate_limiter import LLM_CONCURRENCY_LIMIT
        async with LLM_CONCURRENCY_LIMIT:
            return await proxy.generate_text(
                messages=messages,
                agent_id=agent_id,
                max_tokens=max_tokens,
                temperature=temperature,
            )
    except Exception as e:
        logger.warning(
            f"[Motive] LLMProxy.generate_text 失败 (fail-closed): "
            f"{type(e).__name__}: {e}"
        )
        return None


# ───────────────────────────────────────────────────────────
# interpretation 输出解析 (fail-closed)
# ───────────────────────────────────────────────────────────

def _extract_json(raw: str) -> Optional[dict]:
    """从 LLM 输出提取 JSON dict (容错 markdown 代码块 / 前后杂讯)。"""
    if not raw:
        return None
    text = raw.strip()
    # 剥 markdown 代码块
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 多策略: 抓第一个 {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def parse_interpretation_output(raw: Optional[str]) -> Optional[dict]:
    """
    解析 interpretation LLM 输出 (fail-closed)。

    SM-4.1 扩展: 输出可带 motive_type (observe/reflect/transmit, 多元化)。
    motive_type 缺失/非法 → 不 fail-closed (向后兼容旧输出), 记为 None。

    Returns:
        {"has_motive": True, "content": str, "motive_type": str|None}
        | {"has_motive": False}
        或 None (坏输出 / 缺字段 / content 为空 → 视为无 motive, 不产生空 motive)
    """
    if raw is None:
        return None
    data = _extract_json(raw)
    if data is None:
        logger.warning("[Motive] interpretation 输出非 JSON (fail-closed = 无 motive)")
        return None
    has_motive = data.get("has_motive")
    if not isinstance(has_motive, bool):
        logger.warning(
            f"[Motive] interpretation 缺 has_motive bool (fail-closed = 无 motive): "
            f"{data!r}"
        )
        return None
    if not has_motive:
        return {"has_motive": False}
    content = data.get("content")
    if not isinstance(content, str) or not content.strip():
        logger.warning(
            "[Motive] interpretation has_motive=True 但 content 缺失/为空 "
            "(fail-closed = 无 motive)"
        )
        return None
    # SM-4.1: motive_type 可选 (observability, 不 gate)。非法值 → None。
    motive_type = data.get("motive_type")
    if motive_type not in MOTIVE_TYPES:
        if motive_type is not None:
            logger.warning(
                f"[Motive] interpretation motive_type 非法 (记为 None): {motive_type!r}"
            )
        motive_type = None
    return {"has_motive": True, "content": content.strip(), "motive_type": motive_type}


# ───────────────────────────────────────────────────────────
# MotiveEngine — interpretation 产生 + 生命周期
# ───────────────────────────────────────────────────────────

class MotiveEngine:
    """
    motive 产生 (interpretation) + 生命周期管理。

    产生机制 (SM-1 Q2):
      1. 检测新 InnerLifeEvent (trace 只读, bounded window)
      2. 读取经历的可读描述 (trigger_type + ts + diary/dream 文本产物)
      3. Soul 的 LLM 做 interpretation: 「这次经历里有没有想告诉 Bry 的念头」
      4. 产出 Motive 或「无 motive」

    只读约束: 不写 InnerLifeEvent / diary / dream / SAGE。
    唯一写入: motive trace (MotiveTraceStore)。
    """

    def __init__(
        self,
        store: Optional[MotiveTraceStore] = None,
        trace_reader: Optional[Any] = None,
        llm_call: Optional[Callable[..., Awaitable[Optional[str]]]] = None,
        window_hours: float = MOTIVE_INTERPRET_WINDOW_HOURS,
        ttl_hours: float = MOTIVE_TTL_HOURS,
    ) -> None:
        if store is None:
            store = MotiveTraceStore()
        self._store = store
        if trace_reader is None:
            from src.inner_life.trace_reader import NarrativeTraceReader
            trace_reader = NarrativeTraceReader()
        self._trace_reader = trace_reader
        self._llm_call = llm_call or _default_llm_call
        self._window_hours = window_hours
        self._ttl_hours = ttl_hours

    # ── interpretation ────────────────────────────────────

    async def interpret_new_events(self, agent_id: str) -> List[Motive]:
        """
        检查自上次以来有没有新 InnerLifeEvent, 有则 interpretation 产出 motive。

        幂等: 已解释过的 provenance_ref (motive trace 里已有) 不重复 interpretation。
        有界: 只查最近 window_hours 的 trace (跟 M5.8-4 bounded query 同款)。

        Returns:
            新产生的 motives (可能为空)
        """
        from datetime import timedelta as _td
        now = datetime.now(timezone.utc)
        window_start = (now - _td(hours=self._window_hours)).isoformat()
        window_end = now.isoformat()

        try:
            records = self._trace_reader.query_by_ts_range(
                start=window_start, end=window_end
            )
        except Exception as e:
            logger.warning(
                f"[Motive] trace query 失败 (fail-closed = 无新 motive): "
                f"{type(e).__name__}: {e}"
            )
            return []

        # 过滤该 agent 的 events
        agent_records = []
        for r in records:
            if not isinstance(r, dict):
                continue
            prov = r.get("provenance")
            if not isinstance(prov, dict):
                continue
            if prov.get("actor_id") == agent_id:
                agent_records.append(r)

        # 过滤已解释的 (provenance_ref 去重)
        known = self._store.known_provenance_refs()
        new_records = [
            r for r in agent_records
            if isinstance(r.get("event_id"), str) and r["event_id"] not in known
        ]
        if not new_records:
            return []

        # 按 ts 排序 (deterministic)
        new_records.sort(key=lambda r: r.get("ts", ""))

        produced: List[Motive] = []
        for rec in new_records:
            event_id = rec.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                continue
            provenance_desc = self._resolve_provenance_desc(rec, agent_id)
            parsed = await self._interpret_one(agent_id, provenance_desc)
            if parsed is None:
                # fail-closed: interpretation 失败 → 不产生 motive (不发)
                logger.warning(
                    f"[Motive] interpretation 失败/无输出, 不产生 motive "
                    f"event={event_id} agent={agent_id}"
                )
                continue
            if not parsed["has_motive"]:
                logger.info(
                    f"[Motive] interpretation: 无念头 event={event_id} agent={agent_id}"
                )
                continue
            try:
                # D2 (SG-1 §5.1): 出口 fail-closed 校验 — target 不在值域 →
                # InvalidMotiveTargetError → 该 motive 不产生 (0 静默放行)
                motive = make_motive(
                    motive_id=new_motive_id(),
                    content=parsed["content"],
                    target=TARGET_BRYAN,
                    provenance_ref=event_id,
                    created_at=now_utc_iso(),
                )
            except InvalidMotiveTargetError as e:
                logger.warning(
                    f"[Motive] motive 因 target 非法被拒绝 (fail-closed): "
                    f"agent={agent_id} err={e}"
                )
                continue
            self._store.append_motive(
                motive, agent_id, motive_type=parsed.get("motive_type")
            )
            produced.append(motive)
            logger.info(
                f"[Motive] 新 motive: {motive.motive_id} agent={agent_id} "
                f"type={parsed.get('motive_type')} "
                f"content={motive.content[:40]!r} provenance={event_id}"
            )
        return produced

    async def _interpret_one(
        self, agent_id: str, provenance_desc: str
    ) -> Optional[dict]:
        """对一次经历做 interpretation (LLM)。

        SM-4.1 (Motive 多元化): 念头类型由经历性质决定, 不只「想告诉 Bry」:
          - 环境刺激 (天气变化/时段切换/外界信号) → observe (想观察确认)
          - 夜间时段 / 长期未联络 → reflect (想回顾回忆)
          - 重大生活事件 / 紧迫事项 → transmit (想告诉 Bry)
          - 平常小事 → 无念头
        """
        prompt = (
            f"你是 {agent_id}。Bry 是你的主人。\n"
            f"你刚刚经历了一件事：\n{provenance_desc}\n\n"
            f"这次经历可能在你心里浮现一个念头。念头的类型由经历的性质决定：\n"
            f"- 环境刺激（天气变化、时段切换、外界信号）→ 念头是「想观察确认」，"
            f"例如「外面好像下雨了，想确认一下天气」。\n"
            f"- 夜间时段，或你很久没有和 Bry 联络 → 念头是「想回顾回忆」，"
            f"例如「夜深了，想翻翻以前的回忆」。\n"
            f"- 重大生活事件或紧迫事项 → 念头才是「想告诉 Bry」，"
            f"例如「今天有件重要的事，想告诉 Bry」。\n"
            f"- 只是平常小事 → 没有念头。\n\n"
            f"只输出 JSON："
            f'{{"has_motive": true, "motive_type": "observe" | "reflect" | "transmit", '
            f'"content": "念头原文"}} '
            f'或 {{"has_motive": false}}'
        )
        raw = await self._llm_call(
            [{"role": "user", "content": prompt}],
            agent_id=agent_id,
            max_tokens=INTERPRET_MAX_TOKENS,
            temperature=INTERPRET_TEMPERATURE,
        )
        return parse_interpretation_output(raw)

    # ── provenance 解析 ───────────────────────────────────

    def _resolve_provenance_desc(self, event_record: Dict[str, Any], agent_id: str) -> str:
        """
        把 trace record 解析为可读描述 (DECISION-PROMPT-CONTRACT §2.2):
        trigger_type + ts + 对应 diary/dream 文本产物 (如有)。

        找不到文本 → 只用 trigger_type + ts (不编造)。
        """
        prov = event_record.get("provenance", {})
        trigger_type = prov.get("trigger_type", "unknown") if isinstance(prov, dict) else "unknown"
        ts = event_record.get("ts", "")
        desc = f"trigger_type={trigger_type}, ts={ts}"
        text = self._find_diary_text(agent_id, trigger_type, ts)
        if text:
            desc += f"\n内容：{text}"
        return desc

    def _find_diary_text(self, agent_id: str, trigger_type: str, ts: str) -> Optional[str]:
        """
        通过 trigger_type + ts 定位 diary jsonl 对应文本产物。

        diary/dream/event 文本都写在 data/soul/{agent_id}/diary/YYYY-MM-DD.jsonl
        (slot ∈ morning/night/dream/event, 见 diary.py / dream_event.py)。
        """
        slot = _TRIGGER_TO_SLOT.get(trigger_type)
        if slot is None:
            return None
        date_str = ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None
        if not date_str:
            return None
        from src.paths import data_root
        path = data_root() / "soul" / agent_id / "diary" / f"{date_str}.jsonl"
        if not path.is_file():
            return None
        try:
            best: Optional[str] = None
            best_ts: str = ""
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("slot") != slot:
                    continue
                content = entry.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                entry_ts = entry.get("ts", "")
                if entry_ts >= best_ts:
                    best = content.strip()
                    best_ts = entry_ts
            return best
        except OSError as e:
            logger.warning(f"[Motive] 读 diary 失败 ({agent_id}): {e}")
            return None

    # ── 生命周期 ──────────────────────────────────────────

    def resolve_pending(self, agent_id: str) -> Optional[Motive]:
        """resolve pending motive (未过期)。无 → None (fail-closed 入口)。"""
        return self._store.resolve_pending(
            agent_id, now=datetime.now(timezone.utc), ttl_hours=self._ttl_hours
        )

    def mark_transmitted(self, motive_id: str) -> None:
        self._store.mark_transmitted(motive_id)

    def mark_rejected(self, motive_id: str) -> None:
        self._store.mark_rejected(motive_id)

    # ── Decision (委托 decision.py) ───────────────────────

    async def decide(self, motive: Motive, agent_id: str) -> Any:
        """
        Decision LLM (SM-2 契约, 委托 src/soul/decision.py)。

        Returns:
            DecisionResult (SM-4 四元: transmit / observe / reflect / do_nothing;
            transmit 字段兼容 scheduler: decision == "transmit")
        """
        from src.soul.decision import decide_motive
        return await decide_motive(
            motive=motive,
            agent_id=agent_id,
            llm_call=self._llm_call,
        )
