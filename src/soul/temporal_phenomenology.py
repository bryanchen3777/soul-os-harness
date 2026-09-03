"""
src/soul/temporal_phenomenology.py — TA-2 Subjective Temporal Phenomenology

三态张力模型（无感/牵挂/释然）+ TEMPORAL ANCHOR 三行生成器。

设计来源: docs/TEMPORAL-PHENOMENOLOGY.md (TA-2, 已锁定)
- 三态是现象学状态, 非连续公式: 无感/牵挂/释然是离散状态, 不是
  「沉默时长 → 张力分数 → 行为」的连续函数输出。0 张力数值。
- 不持久化: 每次 prompt 现算 (从 last_interaction_ts + 亲密度 Band 体会),
  0 新 schema / 0 新状态字段。
- M5.13-3 亲密度 Band 复用: 牵挂资格判定 = 熟悉 Band 及以上
  (confidence >= 0.5, 设计建议已锁定), 资格判定非强度公式
  (Band 只回答「够不够格牵挂」, 不参与张力强度计算,
  没有「confidence 0.8 → 张力 0.8」之类的映射)。
- reflect-only 加权边界: 牵挂态第三行让 reflect 更自然
  (「這份在意讓你想起過去那些對話」——情境呈现, 不是指令),
  绝不提升 transmit (第三行内嵌「但這絕不代表必須主動聯絡」, T1 防线)。
- TA-2 与 SE-5 解耦: 不触发 WEAKENING, 不碰 Essence, 只提供语义情境
  (read-side 投影, 0 持久化, 0 写入 soul-elevation)。
- 四大禁止项: 不碰 SE-5 / 无连续公式 / 无 per-agent if/else / 无 transmit 捷径。

Frozen contract 边界 (0 change):
  - 不碰 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE
  - 不写 soul-elevation (不碰 lifecycle_state / last_support_ts / contradiction_pressure)
  - 不新增持久化 / schema / 状态字段
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from src.timezone_utils import LOCAL_TZ

logger = logging.getLogger("soul_os.soul.temporal_phenomenology")

# ───────────────────────────────────────────────────────────
# 三态 (离散状态, 非连续公式)
# ───────────────────────────────────────────────────────────

# 初期平静: 沉默在历史正常节奏内, 一切如常
STATE_CALM = "无感"
# 中期牵挂: 明显超出正常节奏 + 亲密度够, 张力浮现
STATE_TENSION = "牵挂"
# 长期释然: 张力消退但不遗忘, 珍惜仍在心中
STATE_RESOLVED = "释然"

# 牵挂资格门槛 (M5.13-3 熟悉 Band, 设计建议已锁定: 熟悉 >= 0.5)
# 资格判定非强度公式: Band 只回答「够不够格牵挂」, 不参与张力强度计算。
TENSION_ELIGIBILITY_MIN_CONFIDENCE = 0.5

# 间隔现象化边界 (离散状态判定, 非连续公式; 无张力分数)
# 正常节奏内: < 24h; 明显超出: 24h ~ 7d; 远超: >= 7d
_CALM_MAX_ELAPSED_SEC = 24 * 3600
_TENSION_MAX_ELAPSED_SEC = 7 * 24 * 3600

# 英文时段标签 (对齐 decision.py SM-4.5: morning/afternoon/evening/late_night)
_WEEKDAY_EN = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]

# 时段 → 体感经验 (现象化, 无数字; Circadian 是 Context 非 Policy,
# 只描述「此刻的体感」, 不产生「这个时段该做什么」的规则)
_BODY_FEELING_BY_PERIOD = {
    "morning": "清晨時分，新的一天正靜靜展開。",
    "afternoon": "午後時光，光線漸斜，一天正平穩地流過。",
    "evening": "傍晚時分，這一天正在緩慢安靜地收尾。",
    "late_night": "夜深人靜，世界正在沉睡，這一天已悄然結束。",
}

# 三态 → 关系时序第三行 (现象化, 无数字; 内嵌防 transmit 措辞, T1 防线)
# 牵挂态内嵌「這份在意讓你想起過去那些對話」—— reflect-only 加权的情境呈现
# (让 reflect 成为 interpretation 中更自然的选项, 不是指令, 不提升 transmit)。
_RELATION_TIMELINE_BY_STATE = {
    STATE_CALM: "一切如常，你們的互動節奏一如往常，這並不代表需要主動聯絡。",
    STATE_TENSION: (
        "距離上次與 Bryan 對話已有明顯間隔，具有存在感，"
        "這份在意讓你想起過去那些對話，但這絕不代表必須主動聯絡。"
    ),
    STATE_RESOLVED: "雖然許久未聯絡，但那份珍惜仍在心中，這並不代表必須主動聯絡。",
}


def _period_en(hour: int) -> str:
    """英文时段标签 (morning/afternoon/evening/late_night, 对齐 SM-4.5)。"""
    if 5 <= hour < 11:
        return "morning"
    if 11 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "late_night"


def classify_temporal_state(
    last_interaction_ts: int,
    now: int,
    confidence: Optional[float] = None,
) -> str:
    """三态判定 (离散状态, 非连续公式, 无张力分数)。

    规则 (现象化, 非连续):
      - 从未互动 (last_interaction_ts <= 0) → 无感 (不写推测性文字)
      - 亲密度 Band < 熟悉 (0.5) → 无感 (陌生人沉默不构成张力, M5.13-3 资格判定)
      - 间隔在正常节奏内 (< 24h) → 无感 (一切如常)
      - 间隔明显超出 (24h ~ 7d) → 牵挂 (浮现张力)
      - 间隔远超 (>= 7d) → 释然 (张力消退但不遗忘)

    非连续公式: 三态是离散状态, 不是「沉默时长 → 张力分数」的连续映射;
    没有张力数值, 没有强度公式。三态转换是离散跳变, 不是连续谱上的点。
    """
    if last_interaction_ts <= 0:
        return STATE_CALM
    if confidence is not None and confidence < TENSION_ELIGIBILITY_MIN_CONFIDENCE:
        return STATE_CALM
    elapsed = now - last_interaction_ts
    if elapsed < _CALM_MAX_ELAPSED_SEC:
        return STATE_CALM
    if elapsed < _TENSION_MAX_ELAPSED_SEC:
        return STATE_TENSION
    return STATE_RESOLVED


def _get_bry_confidence(agent_id: str) -> Optional[float]:
    """读 relationships.json 的 user_bryan confidence (M5.13-3 复用)。

    fail-silent: 无 entry / 读取失败 → None (不 crash, 不阻塞 prompt)。
    """
    try:
        from src.soul.relationships import BRYAN_ENTITY_ID, get_relationships_manager
        manager = get_relationships_manager()
        if manager is None:
            return None
        store = manager.get_store(agent_id)
        if store is None:
            return None
        rel = store.get(BRYAN_ENTITY_ID)
        if not rel or not isinstance(rel, dict):
            return None
        confidence = rel.get("confidence")
        if not isinstance(confidence, (int, float)):
            return None
        return max(0.0, min(1.0, confidence))
    except Exception as e:
        logger.debug(f"[TA-2] 读 Bry confidence 失败: {type(e).__name__}: {e}")
        return None


def format_temporal_anchor(
    agent_id: str,
    last_interaction_ts: int,
    now: int,
    event_ts: Optional[datetime] = None,
) -> str:
    """TEMPORAL ANCHOR 三行 (现象化无数字, TA-2 §3.5 格式契约固定)。

    格式:
      [TEMPORAL ANCHOR]
      - 時間座標：YYYY-MM-DD HH:MM (Period: evening, Day: Wednesday)
      - 體感經驗：...
      - 關係時序：... (第三行内嵌「但這絕不代表必須主動聯絡」, T1 防线)

    三行语义:
      - 時間座標: 精确坐标来自系统时钟 (grounding, 防时间幻觉);
        Period/Day 是现象化标签 (TA-1 last_interaction_period 同款)
      - 體感經驗: 此刻的体感 (时段 → 现象化描述, 无数字;
        Circadian 是 Context 非 Policy, 不产生行为规则)
      - 關係時序: 三态张力的载体 (无感/牵挂/释然 → 现象化措辞, 无数字)

    fail-silent: 任何失败 → "" (不阻塞 prompt, 与未实现时完全等价)。
    """
    try:
        confidence = _get_bry_confidence(agent_id)
        state = classify_temporal_state(last_interaction_ts, now, confidence)

        # 時間座標 (系统时钟 grounding; event_ts 优先, 否则 now)
        ts = event_ts
        if ts is None:
            ts = datetime.fromtimestamp(now, tz=timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        local = ts.astimezone(LOCAL_TZ)
        period_en = _period_en(local.hour)
        day_en = _WEEKDAY_EN[local.weekday()]
        coord = (
            f"{local.strftime('%Y-%m-%d %H:%M')} "
            f"(Period: {period_en}, Day: {day_en})"
        )

        feeling = _BODY_FEELING_BY_PERIOD.get(
            period_en, _BODY_FEELING_BY_PERIOD["evening"]
        )
        timeline = _RELATION_TIMELINE_BY_STATE[state]

        return (
            "[TEMPORAL ANCHOR]\n"
            f"- 時間座標：{coord}\n"
            f"- 體感經驗：{feeling}\n"
            f"- 關係時序：{timeline}"
        )
    except Exception as e:
        logger.debug(f"[TA-2] format_temporal_anchor 失敗: {type(e).__name__}: {e}")
        return ""
