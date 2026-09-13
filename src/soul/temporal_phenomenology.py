"""
src/soul/temporal_phenomenology.py — TA-2 Subjective Temporal Phenomenology

三态张力模型（无感/牵挂/释然）+ TEMPORAL ANCHOR 三行生成器。

设计来源:
  - docs/TEMPORAL-PHENOMENOLOGY.md (TA-2 原设计)
  - docs/SG-3-RELATIONAL-CONTRACT.md §6 (SG-3 换轨, 取代 TA-2 原 §3.2 资格门槛)

SG-3 §6 换轨要点（本档现行语义）:
- 三态是现象学状态, 非连续公式: 无感/牵挂/释然是离散状态, 不是
  「沉默时长 → 张力分数 → 行为」的连续函数输出。0 张力数值。
- 不持久化: 每次 prompt 现算（从 last_interaction_ts + 羁绊证据体会）,
  0 新 schema / 0 新状态字段。
- **资格判据 = 羁绊证据（BondEvidence）**, 优先序 P1 impression_tags 非空
  → P2 relational_band != "stranger" → P3 fallback（Bryan 轴专用）
  interaction_count ≥ 10 且 last_interaction_at 为合法 ISO。
  资格判定非强度公式（证据只回答「够不够格牵挂」, 不参与张力强度计算,
  没有「count 385 → 张力 385」之类的映射）。
- **`confidence` 完全退出 TA-2 判定链**（SG-3 §6.3 明文禁止退回）:
  `TENSION_ELIGIBILITY_MIN_CONFIDENCE = 0.5` 已**废止**; legacy confidence
  是 SG-1 §2.3 的只读遗留栏位。**禁止**把 legacy confidence 的 `0.5`
  对映到 SG-2 的 `familiar`（同名不同尺度陷阱, SG-3 §6.3 明令）:
  TA-2 的 0.5 = legacy M5.13-3 confidence band 的「熟悉」（proxy.py:350）,
  SG-2 的 familiar = counter band（co≥2 且 reply≥2, SG-3 §5.1）。
- reflect-only 加权边界: 牵挂态第三行让 reflect 更自然
  （「這份在意讓你想起過去那些對話」——情境呈现, 不是指令）,
  绝不提升 transmit（第三行内嵌「但這絕不代表必須主動聯絡」, T1 防线）。
- TA-2 与 SE-5 解耦: 不触发 WEAKENING, 不碰 Essence, 只提供语义情境
  (read-side 投影, 0 持久化, 0 写入 soul-elevation)。
- 四大禁止项: 不碰 SE-5 / 无连续公式 / 无 per-agent if/else / 无 transmit 捷径。

Frozen contract 边界 (0 change):
  - 不碰 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE
  - 不写 soul-elevation (不碰 lifecycle_state / last_support_ts / contradiction_pressure)
  - 不新增持久化 / schema / 状态字段
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from src.timezone_utils import LOCAL_TZ

logger = logging.getLogger("soul_os.soul.temporal_phenomenology")

# ───────────────────────────────────────────────────────────
# 三态 (离散状态, 非连续公式)
# ───────────────────────────────────────────────────────────

# 初期平静: 沉默在历史正常节奏内, 一切如常
STATE_CALM = "无感"
# 中期牵挂: 明显超出正常节奏 + 羁绊证据成立, 张力浮现
STATE_TENSION = "牵挂"
# 长期释然: 张力消退但不遗忘, 珍惜仍在心中
STATE_RESOLVED = "释然"

# ───────────────────────────────────────────────────────────
# 羁绊证据 (SG-3 §6.3; 取代原 TENSION_ELIGIBILITY_MIN_CONFIDENCE 资格门槛)
#
# 只回答「够不够格牵挂」, 0 强度 / 0 数值 / 0 中间值（INV-1/INV-2）。
# ───────────────────────────────────────────────────────────

# P1: D4 真值源之一（SG-1 §2.2）
BOND_BASIS_IMPRESSION_TAGS = "P1_impression_tags"
# P2: D4 真值源之二（SG-1 §2.2）
BOND_BASIS_RELATIONAL_BAND = "P2_relational_band"
# P3: Bryan 轴 fallback（客观计数, SG-3 §6.3; E10 实测最小 31, 全 10 位皆过）
BOND_BASIS_INTERACTION_COUNT = "P3_interaction_count"
# P4: 全部落空
BOND_BASIS_NONE = "P4_none"

# P3 fallback 的客观计数门槛（全整数; 0 浮点）
BOND_FALLBACK_MIN_INTERACTION_COUNT = 10

# P2 判定值域 = 非 stranger 的三带（与 src/social/relational_bands 四带枚举一致）
_NON_STRANGER_BANDS = frozenset({"known", "familiar", "close"})


@dataclass(frozen=True)
class BondEvidence:
    """羁绊证据（SG-3 §6.3, 只读投影）。

    Args:
        eligible: P1/P2/P3 任一命中 = True; 全落空 = False
        basis:    命中的判据标签（仅供观测/测试, **不进入任何算式**）
    """

    eligible: bool
    basis: str = BOND_BASIS_NONE


# 间隔现象化边界 (离散状态判定, 非连续公式; 无张力分数)
# 正常节奏内: < 24h; 明显超出: 24h ~ 7d; 远超: >= 7d
# ⚠️ 三格边界值与 SG-3 §6.2 逐值一致（0 变更）
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
    bond_evidence: Optional[BondEvidence] = None,
) -> str:
    """三态判定 (离散状态, 非连续公式, 无张力分数)。

    SG-3 §6.2 完整判定式（取代原 §3.2 confidence 资格门槛）:
      STEP 1 从未互动 (last_interaction_ts <= 0) → 无感 (不写推测性文字)
      STEP 2 羁绊资格 (BOND GATE): 未取得羁绊证据 (not eligible) → 无感
             —— `bond_evidence is None`（读取失败/缺 entry）同样 → 无感
                (fail-silent, SG-3 §6.2 / INV-8)
      STEP 3 间隔现象化 (离散三格, 非连续函数):
             elapsed <  24h        → 无感 (一切如常)
             24h <= elapsed < 7d   → 牵挂 (浮现张力)
             elapsed >= 7d         → 释然 (张力消退但不遗忘)
      - `elapsed <= 0`（未来时间戳 / 时钟回拨）落第一格 → 无感（保守, 不编造）
      - 边界钉死（与 SG-3 §6.2 逐值一致, 0 变更）:
        `< 86400` 无感; `86400 <= elapsed < 604800` 牵挂; `>= 604800` 释然。

    非连续公式: 三态是离散状态, 不是「沉默时长 → 张力分数」的连续映射;
    没有张力数值, 没有强度公式。三态转换是离散跳变, 不是连续谱上的点。

    0 per-agent if/else: 本函数**不接收 agent_id**（INV-7, 签名 0 变更）。
    0 confidence: 退役栏位完全退出本判定链（SG-3 §6.3 明文禁止退回）。
    """
    if last_interaction_ts <= 0:
        return STATE_CALM
    if bond_evidence is None or not bond_evidence.eligible:
        return STATE_CALM
    elapsed = now - last_interaction_ts
    if elapsed < _CALM_MAX_ELAPSED_SEC:
        return STATE_CALM
    if elapsed < _TENSION_MAX_ELAPSED_SEC:
        return STATE_TENSION
    return STATE_RESOLVED


def _parse_iso_ts(value: Any) -> Optional[int]:
    """宽容解析 ISO 时间戳 → epoch 秒（缺失 / 非 str / 坏值 → None, 0 raise）。"""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _read_bond_evidence(agent_id: str) -> Optional[BondEvidence]:
    """读 relationships.json 的 `others.user_bryan` entry → BondEvidence。

    SG-3 §6.3 完整优先序（**OR 串联, 任一条命中即 eligible=True**）:
      P1 `impression_tags` 非空           → D4 真值源之一
      P2 `relational_band != "stranger"`  → D4 真值源之二
      P3 `interaction_count >= 10` **且** `last_interaction_at` 为合法 ISO
                                          → Bryan 轴 fallback（客观计数）
      P4 其余                             → eligible=False（→ 无感）

    **只读、不写、不创建 entry**（C-3.1 §3.3「读侧 0 写副作用」; 用 get 语义,
    绝不调 ensure_relationship）。恒只查 BRYAN_ENTITY_ID（A2A 轴对称说明,
    SG-3 §6.4: 没有「其他 agent 的 tags 可借用」的路径）。

    fail-silent（SG-3 §6.4 / INV-8）: entry 缺失 / 任何读取例外 → None
    （→ 无感）。**绝不退回 confidence**（退役栏位; 同名不同尺度陷阱,
    SG-3 §6.3 明文禁止把 legacy confidence 0.5 对映 SG-2 的 familiar）。
    """
    try:
        from src.paths import data_root
        from src.soul.relationships import BRYAN_ENTITY_ID

        path = data_root() / "soul" / agent_id / "relationships.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        others = data.get("others") if isinstance(data, dict) else None
        entry = others.get(BRYAN_ENTITY_ID) if isinstance(others, dict) else None
        if not isinstance(entry, dict):
            return None
        # P1: impression_tags 非空
        tags = entry.get("impression_tags")
        if isinstance(tags, list) and any(str(t).strip() for t in tags):
            return BondEvidence(True, BOND_BASIS_IMPRESSION_TAGS)
        # P2: relational_band 非 stranger
        band = entry.get("relational_band")
        if isinstance(band, str) and band in _NON_STRANGER_BANDS:
            return BondEvidence(True, BOND_BASIS_RELATIONAL_BAND)
        # P3: Bryan 轴 fallback（客观计数 + 合法时间戳, 全整数）
        count = entry.get("interaction_count")
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and count >= BOND_FALLBACK_MIN_INTERACTION_COUNT
            and _parse_iso_ts(entry.get("last_interaction_at")) is not None
        ):
            return BondEvidence(True, BOND_BASIS_INTERACTION_COUNT)
        # P4: 全部落空
        return BondEvidence(False, BOND_BASIS_NONE)
    except Exception as e:
        logger.debug(f"[TA-2] 读 Bryan 羁绊证据失败: {type(e).__name__}: {e}")
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
        # SG-3 §6.3: 羁绊证据只读 relationships.json 的 user_bryan entry
        # （0 浮点、0 confidence; 0 写作副作用）。
        bond = _read_bond_evidence(agent_id)
        state = classify_temporal_state(last_interaction_ts, now, bond)

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
