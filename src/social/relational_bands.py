"""
src/social/relational_bands.py — SG-2 关系带状态机（Relational Bands, D4 纯函数模块）

设计来源: docs/SG-1-SOCIAL-GRAPH-CONTRACT.md（§3 Relational Bands 语义与转移）

四带离散状态机（现象学距离, 对齐 TA-2 三态离散哲学; 非强度公式）:
  - stranger（陌生人）→ known（认识）→ familiar（熟悉）→ close（亲近）

转移规则（SG-3 §5 重标定数值 + 形态冻结: 纯整数判定 + 离散阶梯,
0 加权公式、0 乘积/对数）:
  升带（形态 0 变更, 每 24h 评估窗口至多升 1 级, 计数为累计整数）:
    - stranger → known:    co_presence_sessions ≥ 1 或 reply_exchanges ≥ 1
    - known   → familiar:  co_presence_sessions ≥ 2 且 reply_exchanges ≥ 2
    - familiar → close:    co_presence_sessions ≥ 4 且 reply_exchanges ≥ 4
                           （原 (dream ≥ 4 且 reply ≥ 5) 行已按 OQ-4 删除,
                             未来接口注释保留见 _CLOSE_THRESHOLDS 上方）
  降带（对齐 decay 精神但离散化, 0 浮点）:
    - 连续 >90 天无任何新信号（last_signal_at 无更新）→ 下移 1 带
    - 已在 stranger → 不降（底带）

数值重标定的依据（SG-3 §4/§5, 实测绘出的信号产率模型 R）:
  旧表在最保守的实测产率（R_co_pair = 0.02246 /pair/day）下**结构性不可达**
  （旧 known→familiar 的 co≥5 需中位 208 天; 旧 familiar→close 的 co≥15
  需中位 653 天; 旧 dream 行因 R_dream ≡ 0 永不可达）→ 属 SG-3 §3 INV-5
  可達性不變量定义的**契约违规**。本表即对该条的数值重标定。

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
# 升带阈值表（SG-3 §5.1 重标定; 形态冻结 = 全整数, 0 浮点 / 0 权重）
#
# SG-3 §5.1 取代 SG-1 §3.3 的**数值**（形态 0 变更）:
#   - 计数口径 0 变更: objective.* 为累计整数（apply_relation_evaluation
#     增量累加）, 非窗内瞬时值; 每次结算至多升 1 级（0 变更）。
#   - 「≥2 个不同 24h 窗（累计制）」的实作机制（SG-3 §5.1 + 幕僚长拍板）:
#     结算端（src/social/relation_settlement.py settle_relations）对**同一 pair
#     的单窗增量上限设为 1**（co_presence_sessions 与 reply_exchanges 皆同）
#     → 「计数 ≥2」在结构上必然跨 ≥2 个不同结算窗
#     → **无需新增任何 schema 字段**即满足契约语义。
#   - 旧值（reply≥1 OR co≥2 / reply≥3 AND co≥5 / reply≥10 AND co≥15 OR
#     dream≥4 AND reply≥5）在实测讯号产率 R 下不可达, 见模块 docstring。
# ───────────────────────────────────────────────────────────

# 最低可得带的门槛（stranger → known）: 任一命中即升（SG-3 §5.1: co≥1 或 reply≥1）
# 可达性: P(≥1) 90 天 86.8%, 中位可达 ≈ 31 天（本列宣告地平线 T = 31 天）
_KNOWN_THRESHOLDS = {
    "reply_exchanges": 1,
    "co_presence_sessions": 1,
}
_KNOWN_MODE = "or"

# known → familiar: 全部命中才升（SG-3 §5.1: co≥2 且 reply≥2）
# 可达性: P(≥2) 90 天 60.0% / 180 天 91.2%, 中位可达 ≈ 75 天
_FAMILIAR_THRESHOLDS = {
    "reply_exchanges": 2,
    "co_presence_sessions": 2,
}
_FAMILIAR_MODE = "and"

# familiar → close: 单行全命中即升（SG-3 §5.1: co≥4 且 reply≥4）
# 可达性: P(≥4) 180 天 57.5% / 365 天 96.3%, 中位可达 ≈ 164 天
#
# OQ-4（Owner 核准, 2026-09-13）: **删除**原
#   ({"dream_exchanges": 4, "reply_exchanges": 5}, "and") 行。
#   理由: R_dream 结构性为 0（relation_settlement v1 无方向性持久载体,
#   diary dream entry 不含 target）→ 保留该行即 INV-5 违规。
#   未来接口（明文保留）: 若 SI-3 或后续工单补上 dream 方向性载体, 可重新加入
#   ({"dream_exchanges": 4, "reply_exchanges": 4}, "and") 行 —— 形态冻结,
#   届时必须附新的可達性推导（SG-3 §10 SI-3-d / SI-3-g）。
_CLOSE_THRESHOLDS = (
    ({"reply_exchanges": 4, "co_presence_sessions": 4}, "and"),
)

# 降带: 连续无信号天数阈值（SG-3 §5.2: 30 → 90; 形态冻结 = > 90 天才下移）
# INV-6: 降带门槛必须 ≥ 该轴讯号的平均到达间隔 E[gap_pair] = 44.5 天。
#   旧值 30 天会在「该 pair 还没被抽到第二次」时就降带 → 与旧门槛组合成
#   「只降不升」的单向棘轮（SG-3 §5.2）。90 天 ≈ 2.0 × 平均到达间隔。
DEMOTE_DAYS = 90
_DEMOTE_SECONDS = DEMOTE_DAYS * 86400  # 全整数秒（0.0 乘积避免: 90*86400 为 int）


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
# 降带判定（纯函数, 90 天形态冻结）
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
    """降带判定: 连续 >90 天无任何新信号 → True（下移 1 带）。

    - 主判据 last_signal_at（4.2 objective 字段）; 4.1 旧数据缺省时
      用 fallback_ts（last_interaction_at / band_updated_at 等既有字段）。
    - 两者皆缺 / 坏时间戳 → False（保守不降, 防误伤 legacy 数据）。
    - 边界: 恰好 90 天整不降, 超过 90 天才降（SG-3 §5.2「连续 > 90 天」）。
    - SG-3 §5.2: 每次结算至多降 1 带; 底带 stranger 不降（0 变更）。
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