"""
src/social/relational_bands.py — SG-2 关系带状态机（Relational Bands, D4 纯函数模块）

设计来源: docs/SG-1-SOCIAL-GRAPH-CONTRACT.md（§3 Relational Bands 语义与转移）

四带离散状态机（现象学距离, 对齐 TA-2 三态离散哲学; 非强度公式）:
  - stranger（陌生人）→ known（认识）→ familiar（熟悉）→ close（亲近）

转移规则（契约 §3.3, 契约定死: 纯整数判定 + 离散阶梯, 0 加权公式、0 乘积/对数）:
  升带（任一行命中即升, 每 24h 评估窗口至多升 1 级, 计数为累计整数）:
    - stranger → known:    reply_exchanges ≥ 1 或 co_presence_sessions ≥ 2
    - known   → familiar:  reply_exchanges ≥ 3 且 co_presence_sessions ≥ 5
    - familiar → close:    (reply_exchanges ≥ 10 且 co_presence_sessions ≥ 15)
                           或 (dream_exchanges ≥ 4 且 reply_exchanges ≥ 5)
  降带（对齐 decay 精神但离散化, 0 浮点）:
    - 连续 >30 天无任何新信号（last_signal_at 无更新）→ 下移 1 带
    - 已在 stranger → 不降（底带）

No-Scoring 刚线（契约 §6）:
  - 本模块 0 浮点常量 / 0 权重公式 / 0 排序打分; 阈值全部为整数。
  - 状态机只回答「够不够格」, 不进入任何算式乘法因子。

Frozen contract 边界:
  - 不触碰 SAGE / Elevation confidence 定义（0 联动, 契约 §3.4）。
  - 纯函数模块, 唯一消费方 = RelationshipsStore.apply_relation_evaluation。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

# ───────────────────────────────────────────────────────────
# 四带枚举（离散, 契约 §3.1）
# ───────────────────────────────────────────────────────────

RELATIONAL_BANDS: tuple = ("stranger", "known", "familiar", "close")

BAND_STRANGER = "stranger"
BAND_KNOWN = "known"
BAND_FAMILIAR = "familiar"
BAND_CLOSE = "close"

# 带序（仅供降带步进与合法性校验, 0 排序打分用途）
_BAND_INDEX: Dict[str, int] = {
    BAND_STRANGER: 0,
    BAND_KNOWN: 1,
    BAND_FAMILIAR: 2,
    BAND_CLOSE: 3,
}


def valid_band(band: str) -> bool:
    """band 是否在四带枚举内（确定性校验, fail-closed）。"""
    return band in _BAND_INDEX


# ───────────────────────────────────────────────────────────
# 升带阈值表（契约 §3.3 照抄, 全整数, 0 浮点 / 0 权重）
# ───────────────────────────────────────────────────────────

# 最低可得带的门槛（stranger → known）: 任一命中即升
_KNOWN_THRESHOLDS = {
    "reply_exchanges": 1,
    "co_presence_sessions": 2,
}
_KNOWN_MODE = "or"

# known → familiar: 全部命中才升
_FAMILIAR_THRESHOLDS = {
    "reply_exchanges": 3,
    "co_presence_sessions": 5,
}
_FAMILIAR_MODE = "and"

# familiar → close: 两行任一命中即升（每行全命中）
_CLOSE_THRESHOLDS = (
    ({"reply_exchanges": 10, "co_presence_sessions": 15}, "and"),
    ({"dream_exchanges": 4, "reply_exchanges": 5}, "and"),
)

# 降带: 连续无信号天数阈值（契约 §3.3 形态冻结: 30 天, > 30 天才下移）
DEMOTE_DAYS = 30
_DEMOTE_SECONDS = DEMOTE_DAYS * 86400  # 全整数秒（0.0 乘积避免: 30*86400 为 int）


# ───────────────────────────────────────────────────────────
# 升带判定（纯函数, 整数比较）
# ───────────────────────────────────────────────────────────

def _threshold_hit(thresholds: Dict[str, int], mode: str, counts: Dict[str, int]) -> bool:
    """单行阈值判定: mode="or" 任一中即命中; mode="and" 全中才命中。

    计数器缺省按 0 计（确定性; 0 浮点比较, 全部整数）。
    """
    hits = 0
    total = len(thresholds)
    for key, need in thresholds.items():
        got = counts.get(key, 0)
        if got >= need:
            hits += 1
        elif mode == "and":
            return False
    if mode == "and":
        return hits == total
    return hits >= 1


def evaluate_band(
    current_band: str,
    *,
    reply_exchanges: int = 0,
    co_presence_sessions: int = 0,
    dream_exchanges: int = 0,
) -> str:
    """升带判定（纯函数）: 从当前带起, 每 24h 评估窗口至多升 1 级。

    Args:
        current_band: 当前带（stranger|known|familiar|close; 非法值按 stranger fail-closed）
        reply_exchanges / co_presence_sessions / dream_exchanges: 累计整数计数（≥0）

    Returns:
        新带（不满足下一级门槛 → 保持当前带; 带只升不降, 降带见 demote_band）
    """
    if current_band not in _BAND_INDEX:
        # 未知/脏值: fail-closed 视作 stranger 重新起算（不静默放行脏带）
        current_band = BAND_STRANGER
    counts = {
        "reply_exchanges": int(reply_exchanges),
        "co_presence_sessions": int(co_presence_sessions),
        "dream_exchanges": int(dream_exchanges),
    }
    if current_band == BAND_STRANGER:
        if _threshold_hit(_KNOWN_THRESHOLDS, _KNOWN_MODE, counts):
            return BAND_KNOWN
        return BAND_STRANGER
    if current_band == BAND_KNOWN:
        if _threshold_hit(_FAMILIAR_THRESHOLDS, _FAMILIAR_MODE, counts):
            return BAND_FAMILIAR
        return BAND_KNOWN
    if current_band == BAND_FAMILIAR:
        for thresholds, mode in _CLOSE_THRESHOLDS:
            if _threshold_hit(thresholds, mode, counts):
                return BAND_CLOSE
        return BAND_FAMILIAR
    # close 为顶带（带不降级; 计数单调增, 不满足也不回退——离散阶梯只回答升格）
    return BAND_CLOSE


# ───────────────────────────────────────────────────────────
# 降带判定（纯函数, 30 天形态冻结）
# ───────────────────────────────────────────────────────────

def demote_band(band: str) -> str:
    """降 1 带（close → familiar → known → stranger）; 底带 stranger 不再降。"""
    if band not in _BAND_INDEX:
        return BAND_STRANGER
    idx = _BAND_INDEX[band]
    if idx <= 0:
        return BAND_STRANGER
    return RELATIONAL_BANDS[idx - 1]


def should_demote(
    last_signal_at: Optional[str],
    now: datetime,
    *,
    fallback_ts: Optional[str] = None,
) -> bool:
    """降带判定: 连续 >30 天无任何新信号 → True（下移 1 带）。

    - 主判据 last_signal_at（4.2 objective 字段）; 4.1 旧数据缺省时
      用 fallback_ts（last_interaction_at / band_updated_at 等既有字段）。
    - 两者皆缺 / 坏时间戳 → False（保守不降, 防误伤 legacy 数据）。
    - 边界: 恰好 30 天整不降, 超过 30 天才降（契约「连续 >30 天」）。
    """
    ts_iso = last_signal_at or fallback_ts
    if not ts_iso:
        return False
    try:
        dt = datetime.fromisoformat(str(ts_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed = (now - dt).total_seconds()
    if elapsed <= 0:
        return False
    return elapsed > _DEMOTE_SECONDS


__all__ = [
    "RELATIONAL_BANDS",
    "BAND_STRANGER",
    "BAND_KNOWN",
    "BAND_FAMILIAR",
    "BAND_CLOSE",
    "DEMOTE_DAYS",
    "valid_band",
    "evaluate_band",
    "demote_band",
    "should_demote",
]