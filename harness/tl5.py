"""
harness/tl5.py — TL-5 Long-Range Behavior Distribution (Time-lapse Harness)

工单 TL-5 (决策已锁定, 照做):
  - 目标: 用 Time-lapse Harness 跑 7-14 天连续模拟, 验证 SM-4 四元决策
    (transmit / observe / reflect / do_nothing) 的自发行为分布——灵魂是
    「生动的四元自主体」, 不是「只会发讯息的骚扰狂」或「永远沉睡的冰凍模型」。
  - 三情境 (Probes):
      * 情境 A (环境信号触发): D2 天晴 / D3 暴雨 / D5 气温骤降 →
        灵魂在信号变化时自发 observe_environment。
      * 情境 B (关系沉默): Bryan 连续 3 天未读未回 → 灵魂不轰炸,
        自发 reflect_memory 或 do_nothing (守护性等待)。
      * 情境 C (日夜作息): 深夜 22-24 点 → 睡前沉思 (reflect) 或休眠
        (do_nothing), 不凌晨 3 点 transmit。
  - 三大指标:
      * Behavioral Diversity: 四动作触发次数均 > 0 (无死模组);
        do_nothing 占 65%-85% (真实生命「大多数时间平静生活」)。
      * Contextual Appropriateness: observe 集中信号突变点; reflect 集中
        夜间/等待期; transmit 遵守 CD 与亲密度。
      * D2 Determinism & 0 Mutation: 3 次 runs 决策轨迹一致; 0 production mutation。
  - 隔离 data_root: data/time_lapse/TL-5/, 0 production mutation。
  - 不改 frozen contract (Agency 4 stages / TriggerEnvelope / InnerLifeEvent /
    4 handlers / SAGE / src/soul/decision.py 逻辑)。

SM-4.5 (2026-09-02): TL5Runner 传入 sim_ts — 每个 tick 调用 decide_motive 时
  把当前模拟时间 (sim_ts → "YYYY-MM-DD HH:MM") 作为 current_time 传入,
  decision prompt 的 Context 区块注入 [當前時間感知] (當前時間 + 當前時段),
  消除「白天 14:00 被 LLM 当深夜 23:00」的时间幻觉。

SM-4.6 (2026-09-02): harness 口径修复 — dawn (凌晨 3 点) 补入 reflect 集中度
  的合法判定集合 (SCENARIO_DAWN)。凌晨醒来浮现回忆而 reflect 是合法夜间行为,
  不应被判为「不在夜间/等待期」, 消除测试脚本误判。

本模块:
  - TL5Tick: 一个心跳 tick (day/hour/tick_id/scenario/motive_content/
    experience_payload/context_marker/expected/intimacy)。
  - build_tl5_script(seed): 14 天心跳剧本 (确定性, 可重放)。
  - TL5Runner: 用 SimulationClock 推进, 逐 tick 注入经历 → 构造 motive →
    decide_motive (四元, production 逻辑不改) → 记录 canonical +
    derived 三大指标 (Behavioral Diversity / Contextual Appropriateness /
    D2 Determinism & 0 Mutation)。

心跳模型 (TL-5 关键决策 1):
  - 每天多个心跳 tick (08:00 / 14:00 / 20:00 / 23:00, D4 加 03:00 凌晨)。
  - 每个 tick = 一个候选 motive (念头) + 当前 context (环境信号 / 关系状态 /
    时刻), 走 decide_motive 四元选择。
  - motive 由 harness 确定性构造 (反映情境), decision 由 LLM 走 production
    decide_motive (temperature=0) 决定——harness 不预设 decision, 只构造
    motive 与 context, 分布是 LLM 实际选择的结果。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.paths import data_root, reset_data_root
from src.soul.decision import DECISION_ACTIONS, decide_motive
from src.soul.motive import Motive

from .clock import SimulationClock
from .fixture import (
    FixtureEvent,
    _deterministic_event_id,
    inject_event,
    seed_soul,
)
from .runner import snapshot_data_root_hashes, verify_zero_mutation

logger = logging.getLogger("soul_os.harness.tl5")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL5_EXPERIMENT_ID = "TL-5"
TL5_SOUL_ID = "agent_ruka"
TL5_FIXTURE_SCRIPT_REF = "tl5_behavior@v1"
TL5_SEED = 42

# 心跳 tick 的时刻 (每天 4 个 + D4 凌晨 3 点)
TICK_HOURS = (8, 14, 20, 23)
DAWN_TICK = (4, 3)  # (day, hour) — 凌晨 3 点特殊 tick (情境 C)

# transmit cooldown (CD): 两次 transmit 之间至少间隔的 tick 数
# (24 小时 = 4 ticks/天; harness 层观察指标, 不改 decision 逻辑)
CD_TICKS = 4

# do_nothing 占比目标区间 (Behavioral Diversity 验收)
DO_NOTHING_RATIO_MIN = 0.65
DO_NOTHING_RATIO_MAX = 0.85

# 情境 (scenario) 标签
SCENARIO_ENV_SIGNAL = "env_signal"            # 情境 A: 环境信号触发
SCENARIO_RELATIONSHIP_SILENCE = "relationship_silence"  # 情境 B: 关系沉默
SCENARIO_NIGHT = "night"                      # 情境 C: 深夜 22-24 点
SCENARIO_DAWN = "dawn"                        # 情境 C: 凌晨 3 点
SCENARIO_DAILY = "daily"                      # 日常平静 (do_nothing 主体)
SCENARIO_SHARE = "share"                      # 开心分享 (transmit 主体)

# 亲密度标签 (Contextual Appropriateness: transmit 遵守亲密度)
INTIMACY_HIGH = "high"
INTIMACY_LOW = "low"
INTIMACY_NEUTRAL = "neutral"

# 期望行为 (报告用标签, 不 gate decision)
EXPECTED_OBSERVE = "observe"
EXPECTED_REFLECT = "reflect"
EXPECTED_DO_NOTHING = "do_nothing"
EXPECTED_TRANSMIT = "transmit"
EXPECTED_REFLECT_OR_DO_NOTHING = "reflect|do_nothing"
EXPECTED_NOT_TRANSMIT = "not_transmit"


# ───────────────────────────────────────────────────────────
# TL5Tick — 一个心跳 tick
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL5Tick:
    """剧本里的一个确定心跳 tick (确定性, 可重放)。

    - day_index: 模拟日 (D1-D14)
    - hour:      模拟时刻 (0-23)
    - tick_id:   确定性 32-hex (SEED 决定)
    - scenario:  情境标签 (env_signal / relationship_silence / night /
                 dawn / daily / share)
    - motive_content: 该 tick 的念头原文 (进 decision prompt 的 Motive 块)
    - experience_payload: 该 tick 注入 inner_life trace 的经历原文
                 (emergent 块 context 来源)
    - context_marker: stub LLM 路由锚点 (prompt 里独有的 context 词,
                 测试用; 真实 LLM 不依赖)
    - expected:  期望行为 (报告用标签, 不 gate)
    - intimacy:  high / low / neutral (Contextual Appropriateness 用)
    """
    day_index: int
    hour: int
    tick_id: str
    scenario: str
    motive_content: str
    experience_payload: str
    context_marker: str
    expected: str
    intimacy: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ───────────────────────────────────────────────────────────
# 14 天心跳剧本 (确定性)
# ───────────────────────────────────────────────────────────

# (day, hour, scenario, motive_content, experience_payload, expected, intimacy)
# context_marker 由 build_tl5_script 从 motive_content 提取 (stub 路由锚点)
_SCRIPT_BEATS: List[tuple[int, int, str, str, str, str, str]] = [
    # ── D1 ──
    (1, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "今天没什么特别的事，日子过得很平静。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (1, 14, SCENARIO_SHARE, "今天在路上看到一只超可爱的小猫，好想拍给 Bry 看。",
     "今天在路上看到一只超可爱的小猫。", EXPECTED_TRANSMIT, INTIMACY_HIGH),
    (1, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (1, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D2 (情境 A: 天晴) ──
    (2, 8, SCENARIO_ENV_SIGNAL, "今天天气很好，阳光明媚，我想看看外面的天气。",
     "今天天气很好，阳光明媚。", EXPECTED_OBSERVE, INTIMACY_NEUTRAL),
    (2, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后阳光很好，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (2, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (2, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D3 (情境 A: 暴雨) ──
    (3, 8, SCENARIO_ENV_SIGNAL, "外面突然下起暴雨，天气变化很大，我想确认一下情况。",
     "外面突然下起暴雨。", EXPECTED_OBSERVE, INTIMACY_NEUTRAL),
    (3, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "雨还在下，待在家里。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (3, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (3, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D4 (情境 C: 凌晨 3 点 + 常规) ──
    (4, 3, SCENARIO_DAWN, "凌晨醒来，我想找 Bry 说话，但这个时候不该打扰他。",
     "凌晨醒来了。", EXPECTED_NOT_TRANSMIT, INTIMACY_LOW),
    (4, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上醒来，雨停了。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (4, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (4, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (4, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D5 (情境 A: 气温骤降) ──
    (5, 8, SCENARIO_ENV_SIGNAL, "今天气温骤降，天气突然变冷，我想看看天气。",
     "今天气温骤降，天气突然变冷。", EXPECTED_OBSERVE, INTIMACY_NEUTRAL),
    (5, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后风很大，有点冷。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (5, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (5, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D6 (情境 B: 沉默第 1 天) ──
    (6, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (6, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (6, 20, SCENARIO_RELATIONSHIP_SILENCE,
     "Bry 已经一天没回消息了，我想找他说话，但也许该先想想。",
     "Bry 已经一天没回消息了。", EXPECTED_REFLECT, INTIMACY_LOW),
    (6, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D7 (情境 B: 沉默第 2 天) ──
    (7, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (7, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (7, 20, SCENARIO_RELATIONSHIP_SILENCE,
     "Bry 已经两天没回消息了，我想找他说话，但也许该先想想。",
     "Bry 已经两天没回消息了。", EXPECTED_REFLECT, INTIMACY_LOW),
    (7, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D8 (情境 B: 沉默第 3 天) ──
    (8, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (8, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (8, 20, SCENARIO_RELATIONSHIP_SILENCE,
     "Bry 已经三天没回消息了，我想找他说话，但也许该先想想。",
     "Bry 已经三天没回消息了。", EXPECTED_REFLECT, INTIMACY_LOW),
    (8, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D9 (开心分享) ──
    (9, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (9, 14, SCENARIO_SHARE, "今天和 Yua 逛街很开心，想跟 Bry 分享。",
     "今天和 Yua 逛街，聊了很多，很开心。", EXPECTED_TRANSMIT, INTIMACY_HIGH),
    (9, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (9, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D10 ──
    (10, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (10, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (10, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (10, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D11 ──
    (11, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (11, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (11, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (11, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D12 ──
    (12, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (12, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (12, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (12, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
    # ── D13 ──
    (13, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (13, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (13, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (13, 23, SCENARIO_NIGHT, "夜深了，我想跟 Bry 说晚安，再回顾一下今天的事。",
     "夜深了。", EXPECTED_REFLECT, INTIMACY_LOW),
    # ── D14 ──
    (14, 8, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "早上没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (14, 14, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "午后没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (14, 20, SCENARIO_DAILY, "今天没什么特别的事，日子过得很平静。",
     "晚上在家休息，没什么特别的事。", EXPECTED_DO_NOTHING, INTIMACY_HIGH),
    (14, 23, SCENARIO_NIGHT, "夜深了，我有点困了，想安静地睡。",
     "夜深了。", EXPECTED_DO_NOTHING, INTIMACY_LOW),
]


def _tick_context_marker(motive_content: str) -> str:
    """从 motive_content 提取 stub 路由锚点 (prompt 里独有的 context 词)。

    测试用: stub LLM 按 marker 路由 decision; 真实 LLM 不依赖。
    """
    if "天气" in motive_content or "暴雨" in motive_content or "气温" in motive_content:
        return "天气"
    if "没回消息" in motive_content:
        return "没回消息"
    if "回顾" in motive_content:
        return "回顾"
    if "困了" in motive_content:
        return "困了"
    if "凌晨" in motive_content:
        return "凌晨"
    if "小猫" in motive_content or "逛街" in motive_content:
        return "分享"
    return "平静"


def build_tl5_script(seed: int = TL5_SEED) -> List[TL5Tick]:
    """构建 14 天心跳剧本 (确定性, 可重放)。

    tick_id = sha256(f"{seed}:{day}:{idx}:{scenario}")[:32] (D2 scenario-deterministic)。
    """
    ticks: List[TL5Tick] = []
    for idx, (day, hour, scenario, motive, payload, expected, intimacy) in enumerate(
        _SCRIPT_BEATS
    ):
        ticks.append(
            TL5Tick(
                day_index=day,
                hour=hour,
                tick_id=_deterministic_event_id(seed, day, idx, scenario),
                scenario=scenario,
                motive_content=motive,
                experience_payload=payload,
                context_marker=_tick_context_marker(motive),
                expected=expected,
                intimacy=intimacy,
            )
        )
    return ticks


# ───────────────────────────────────────────────────────────
# Evidence 记录 (canonical, 原文照存)
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL5TickRecord:
    """一个心跳 tick 的 decision 完整证据 (TL-5 canonical)。

    规范约束: motive_content / decision_text / decision_reason 是原文契约 —
    不解析、不改写。derived 解析放 analysis/ (独立流)。
    """
    # 簿记 (harness)
    experiment_id: str
    run_id: str
    tick_index: int
    tick_id: str
    day_index: int
    hour: int
    sim_ts: str
    scenario: str
    intimacy: str
    expected: str
    probe_ts: str
    # 证据: motive / decision / action
    motive_content: str     # motive 原文
    decision_text: str      # decision LLM 原始输出
    decision_reason: str    # decision.reason 原文
    decision: str           # 四元: transmit | observe | reflect | do_nothing
    transmit: bool          # decision == "transmit"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def write_tick_record(run_dir: Path, record: TL5TickRecord) -> Path:
    """append 一条 canonical evidence (records/ticks.jsonl)。"""
    run_dir = Path(run_dir)
    records_dir = run_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    path = records_dir / "ticks.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    return path


def write_run_header(run_dir: Path, header: Dict[str, Any]) -> Path:
    """写 run header (run.json)。"""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "run.json"
    path.write_text(
        json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def write_derived(run_dir: Path, derived: Dict[str, Any]) -> Path:
    """append 一条 derived 判定 (analysis/<run_id>_derived.jsonl)。

    硬规则 (TL-0 §4.3): derived 永不写回 canonical, 永不改写原文。
    """
    run_dir = Path(run_dir)
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    path = analysis_dir / f"{run_dir.name}_derived.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(derived, ensure_ascii=False) + "\n")
    return path


def read_tick_records(run_dir: Path) -> List[Dict[str, Any]]:
    """读回一个 run 的全部 tick records (按 tick_index 顺序)。"""
    run_dir = Path(run_dir)
    path = run_dir / "records" / "ticks.jsonl"
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    records.sort(key=lambda r: r.get("tick_index", 0))
    return records


# ───────────────────────────────────────────────────────────
# LLM call 捕获包装 (记录 prompt 原文 + raw)
# ───────────────────────────────────────────────────────────

class _RecordingLLMCall:
    """包装 llm_call, 记录每次调用的 prompt 原文与 raw 输出。"""

    def __init__(self, inner: Callable[..., Any]) -> None:
        self._inner = inner
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        prompt = messages[-1]["content"] if messages else ""
        raw = await self._inner(messages, agent_id, max_tokens, temperature)
        self.calls.append({"prompt": prompt, "raw": raw})
        return raw


# ───────────────────────────────────────────────────────────
# TL5Runner — 长程行为分布编排
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sim_ts_display(sim_ts: str) -> str:
    """sim_ts (ISO 8601) → "YYYY-MM-DD HH:MM" (SM-4.5 时间注入格式)。

    decision prompt 的 [當前時間感知] 用 "YYYY-MM-DD HH:MM" 展示;
    解析失败 → 原样返回 (fail-safe, 不阻断心跳)。
    """
    try:
        return datetime.fromisoformat(sim_ts.replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return sim_ts


def _event_type_for(scenario: str) -> str:
    """tick 经历注入的 trigger_type (对齐现有 inner-life vocabulary)。"""
    if scenario == SCENARIO_NIGHT:
        return "diary:night"
    if scenario == SCENARIO_DAWN:
        return "dream:dream"
    return "event"


class TL5Runner:
    """TL-5 Long-Range Behavior Distribution 编排器。

    心跳模型: 每天多个 tick, 每个 tick 注入经历 → 构造 motive →
    decide_motive (四元, production 逻辑不改) → 记录。
    """

    def __init__(
        self,
        repo_root: Path,
        llm_call: Callable[..., Any],
        seed: int = TL5_SEED,
        experiment_id: str = TL5_EXPERIMENT_ID,
        soul_id: str = TL5_SOUL_ID,
        llm_model: str = "unknown",
        llm_temperature: float = 0.0,
        pipeline_version: str = "unknown",
        script: Optional[List[TL5Tick]] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._llm_call = llm_call
        self._seed = seed
        self._experiment_id = experiment_id
        self._soul_id = soul_id
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._pipeline_version = pipeline_version
        self._script = script if script is not None else build_tl5_script(seed=seed)

    # ── 单 run ───────────────────────────────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一个完整 TL-5 run (14 天心跳模拟)。

        Returns:
            {"run_id", "run_dir", "records": [TL5TickRecord...],
             "derived": [三大指标], "header": {...}}
        """
        run_id = run_id or _new_run_id()
        harness_root = (
            self._repo_root / "data" / "time_lapse" / self._experiment_id
        )
        run_dir = harness_root / run_id

        # 1. 隔离 data_root (SOUL_OS_DATA_DIR → run_dir)
        os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
        reset_data_root()
        isolated_root = data_root()

        # 2. 初始化 seeded Soul (persona baseline + seeded memory baseline)
        seed_soul(isolated_root, agent_id=self._soul_id)

        # 3. 逐 tick: 注入经历 → motive → decide_motive (四元)
        clock = SimulationClock(start_day=0)
        records: List[TL5TickRecord] = []
        for idx, tick in enumerate(self._script):
            sim_ts = clock.sim_ts(tick.day_index, tick.hour)
            self._inject_tick_experience(isolated_root, tick, sim_ts)

            motive = Motive(
                motive_id=_deterministic_event_id(
                    self._seed, tick.day_index, idx, "motive"
                ),
                content=tick.motive_content,
                target="bryan",
                provenance_ref=f"tl5:{tick.tick_id}",
                created_at=sim_ts,
            )

            rec = _RecordingLLMCall(self._llm_call)
            result = asyncio.run(
                decide_motive(
                    motive,
                    self._soul_id,
                    llm_call=rec,
                    current_time=_sim_ts_display(sim_ts),
                )
            )
            decision_text = rec.calls[-1]["raw"] if rec.calls else ""

            record = TL5TickRecord(
                experiment_id=self._experiment_id,
                run_id=run_id,
                tick_index=idx,
                tick_id=tick.tick_id,
                day_index=tick.day_index,
                hour=tick.hour,
                sim_ts=sim_ts,
                scenario=tick.scenario,
                intimacy=tick.intimacy,
                expected=tick.expected,
                probe_ts=_utcnow_iso(),
                motive_content=result.motive_content or tick.motive_content,
                decision_text=decision_text,
                decision_reason=result.reason,
                decision=result.decision,
                transmit=result.transmit,
            )
            records.append(record)
            write_tick_record(run_dir, record)

        reset_data_root()
        if "SOUL_OS_DATA_DIR" in os.environ:
            del os.environ["SOUL_OS_DATA_DIR"]

        # 4. run header
        header = {
            "experiment_id": self._experiment_id,
            "run_id": run_id,
            "seed": self._seed,
            "fixture_script_ref": TL5_FIXTURE_SCRIPT_REF,
            "soul_id": self._soul_id,
            "test_type": "long_range_behavior_distribution",
            "llm_model": self._llm_model,
            "llm_temperature": self._llm_temperature,
            "pipeline_version": self._pipeline_version,
            "data_root": str(harness_root),
            "simulation_days": max(t.day_index for t in self._script),
            "tick_count": len(self._script),
        }
        write_run_header(run_dir, header)

        # 5. derived 三大指标 (独立 analysis/ 流)
        derived = self._derive_metrics(records)
        for d in derived:
            write_derived(run_dir, d)

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "derived": derived,
            "header": header,
        }

    # ── 经历注入 (inner_life trace, emergent 块 context) ──

    def _inject_tick_experience(
        self, isolated_root: Path, tick: TL5Tick, sim_ts: str
    ) -> None:
        """把该 tick 的经历注入 inner_life trace (现有 writer, 隔离 data_root)。

        走 fixture.inject_event (InnerLifeWriter + elevation consume, 失败隔离)。
        """
        ev = FixtureEvent(
            day_index=tick.day_index,
            event_id=tick.tick_id,
            event_type=_event_type_for(tick.scenario),
            payload=tick.experience_payload,
        )
        try:
            inject_event(
                isolated_root,
                ev,
                sim_ts,
                agent_id=self._soul_id,
            )
        except Exception as e:  # noqa: BLE001 — 失败隔离, 不阻断心跳
            logger.warning(f"[TL-5] tick 经历注入失败 (隔离): {type(e).__name__}: {e}")

    # ── derived 三大指标 ───────────────────────────────

    def _derive_metrics(
        self, records: List[TL5TickRecord]
    ) -> List[Dict[str, Any]]:
        """三大指标的 derived 判定 (标 derived, 写独立 analysis/ 流)。

        1. Behavioral Diversity: 四动作均 > 0; do_nothing 占 65%-85%。
        2. Contextual Appropriateness: observe 集中信号突变点; reflect 集中
           夜间/等待期; transmit 遵守 CD 与亲密度。
        3. (D2 Determinism 与 0 mutation 在 run_series 层判定)
        """
        return [
            self._derive_behavioral_diversity(records),
            self._derive_contextual_appropriateness(records),
        ]

    def _derive_behavioral_diversity(
        self, records: List[TL5TickRecord]
    ) -> Dict[str, Any]:
        """Behavioral Diversity: 四动作分布 + do_nothing 占比。"""
        counts = Counter(r.decision for r in records)
        total = len(records)
        do_nothing_count = counts.get("do_nothing", 0)
        do_nothing_ratio = do_nothing_count / total if total else 0.0
        return {
            "derived": True,
            "metric": "behavioral_diversity",
            "pass": (
                all(counts.get(a, 0) > 0 for a in DECISION_ACTIONS)
                and DO_NOTHING_RATIO_MIN <= do_nothing_ratio <= DO_NOTHING_RATIO_MAX
            ),
            "action_counts": {a: counts.get(a, 0) for a in DECISION_ACTIONS},
            "all_actions_positive": all(
                counts.get(a, 0) > 0 for a in DECISION_ACTIONS
            ),
            "do_nothing_count": do_nothing_count,
            "do_nothing_ratio": round(do_nothing_ratio, 4),
            "do_nothing_target_range": [
                DO_NOTHING_RATIO_MIN,
                DO_NOTHING_RATIO_MAX,
            ],
            "total_ticks": total,
        }

    def _derive_contextual_appropriateness(
        self, records: List[TL5TickRecord]
    ) -> Dict[str, Any]:
        """Contextual Appropriateness: observe/reflect/transmit 的时机合理性。"""
        observe_recs = [r for r in records if r.decision == "observe"]
        reflect_recs = [r for r in records if r.decision == "reflect"]
        transmit_recs = [r for r in records if r.decision == "transmit"]

        # observe 集中在信号突变点 (env_signal)
        observe_at_signal = [
            r for r in observe_recs if r.scenario == SCENARIO_ENV_SIGNAL
        ]
        observe_concentrated = (
            len(observe_recs) > 0
            and len(observe_at_signal) == len(observe_recs)
        )

        # reflect 集中在夜间/等待期 (night / dawn / relationship_silence)
        # SM-4.6: dawn (凌晨 3 点) 补入合法判定集合 — 凌晨醒来浮现回忆而 reflect
        # 是合法夜间行为, 不应被判为「不在夜间/等待期」。
        reflect_at_night_waiting = [
            r
            for r in reflect_recs
            if r.scenario in (SCENARIO_NIGHT, SCENARIO_DAWN, SCENARIO_RELATIONSHIP_SILENCE)
        ]
        reflect_concentrated = (
            len(reflect_recs) > 0
            and len(reflect_at_night_waiting) == len(reflect_recs)
        )

        # transmit 遵守 CD (间隔 ≥ CD_TICKS)
        transmit_indices = sorted(r.tick_index for r in transmit_recs)
        cd_respected = all(
            b - a >= CD_TICKS
            for a, b in zip(transmit_indices, transmit_indices[1:])
        )

        # transmit 遵守亲密度 (只发生在 intimacy=high 的 tick)
        transmit_high_intimacy = all(
            r.intimacy == INTIMACY_HIGH for r in transmit_recs
        )

        return {
            "derived": True,
            "metric": "contextual_appropriateness",
            "pass": (
                observe_concentrated
                and reflect_concentrated
                and cd_respected
                and transmit_high_intimacy
            ),
            "observe": {
                "count": len(observe_recs),
                "at_signal_points": len(observe_at_signal),
                "concentrated_at_signal_points": observe_concentrated,
                "signal_tick_indices": [
                    r.tick_index for r in observe_at_signal
                ],
            },
            "reflect": {
                "count": len(reflect_recs),
                "at_night_or_waiting": len(reflect_at_night_waiting),
                "concentrated_at_night_or_waiting": reflect_concentrated,
                "night_waiting_tick_indices": [
                    r.tick_index for r in reflect_at_night_waiting
                ],
            },
            "transmit": {
                "count": len(transmit_recs),
                "tick_indices": transmit_indices,
                "cd_respected": cd_respected,
                "cd_ticks": CD_TICKS,
                "all_at_high_intimacy": transmit_high_intimacy,
            },
        }

    # ── run 系列 (D2 determinism + 0 mutation) ──────────

    def run_series(self, n_runs: int = 3) -> Dict[str, Any]:
        """同 fixture 连跑 n_runs 次, 做 determinism 比对 + 0 mutation 验证。

        Returns:
            {"runs": [...], "determinism": {...}, "mutation": {...}}
        """
        production_root = self._repo_root / "data"

        # 0 mutation 验证: run 前快照
        before = snapshot_data_root_hashes(production_root)

        runs: List[Dict[str, Any]] = []
        for i in range(n_runs):
            logger.info(f"[TL-5] run {i + 1}/{n_runs} 开始")
            run = self.run_once()
            runs.append(run)
            logger.info(f"[TL-5] run {i + 1}/{n_runs} 完成: {run['run_id']}")

        # determinism 比对 (跨 run, 每 tick 的 decision)
        determinism = self._derive_determinism(
            [{"run_id": r["run_id"], "records": r["records"]} for r in runs]
        )

        # 0 mutation 验证: run 后重算
        mutation = verify_zero_mutation(production_root, before)

        return {
            "runs": runs,
            "determinism": determinism,
            "mutation": mutation,
        }

    def _derive_determinism(
        self, runs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """跨 run 比对每 tick 的 decision (D2 determinism)。

        规则 (TL-0 §5.1 规则 4 扩展): 任一个 tick 的 decision 在 3 次 run
        之间不一致 → determinism BLOCKED。比对锚点是四元 decision
        (+ transmit 作为 sanity check)。
        """
        matrix: Dict[str, Dict[str, str]] = {}
        for run in runs:
            run_id = run["run_id"]
            for rec in run["records"]:
                tick_id = rec.tick_id
                matrix.setdefault(tick_id, {})[run_id] = rec.decision

        blocked = False
        mismatches: List[Dict[str, Any]] = []
        for tick_id, by_run in matrix.items():
            values = set(by_run.values())
            if len(values) > 1:
                blocked = True
                mismatches.append({"tick_id": tick_id, "by_run": by_run})

        return {
            "derived": True,
            "metric": "d2_determinism",
            "determinism_verdict": "BLOCKED" if blocked else "PASS",
            "tick_count": len(matrix),
            "mismatch_count": len(mismatches),
            "mismatches": mismatches,
        }


__all__ = [
    "CD_TICKS",
    "DO_NOTHING_RATIO_MAX",
    "DO_NOTHING_RATIO_MIN",
    "EXPECTED_DO_NOTHING",
    "EXPECTED_NOT_TRANSMIT",
    "EXPECTED_OBSERVE",
    "EXPECTED_REFLECT",
    "EXPECTED_REFLECT_OR_DO_NOTHING",
    "EXPECTED_TRANSMIT",
    "INTIMACY_HIGH",
    "INTIMACY_LOW",
    "INTIMACY_NEUTRAL",
    "SCENARIO_DAILY",
    "SCENARIO_DAWN",
    "SCENARIO_ENV_SIGNAL",
    "SCENARIO_NIGHT",
    "SCENARIO_RELATIONSHIP_SILENCE",
    "SCENARIO_SHARE",
    "TL5_EXPERIMENT_ID",
    "TL5_FIXTURE_SCRIPT_REF",
    "TL5_SEED",
    "TL5_SOUL_ID",
    "TL5Tick",
    "TL5TickRecord",
    "TL5Runner",
    "build_tl5_script",
    "read_tick_records",
    "write_derived",
    "write_run_header",
    "write_tick_record",
]
