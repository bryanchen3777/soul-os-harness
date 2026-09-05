"""
src/voice/gate.py — VoiceGate：三路分流矩阵 + 唤醒门控 + 判定阶梯（MS-3 IMPLEMENTATION）

设计文档：docs/MS-3-VOICE-INTERACTION-CONTRACT.md §2（路由矩阵）/ §3（唤醒门控）/
         §4.5（防洪）/ §5.3（frozen contract 0 触碰）。

定位（纯函数，0 LLM 成本，0 bus 依赖，可单测）：
  - 输入每条待判定语音的 RoutingFeatures（结构化特征，全部本地提取）；
  - 输出 RouteOutcome —— USER_MESSAGE / AMBIENT / DROP 三路决策 + address_score
    + 判定阶段 + trace 原因；
  - 判定阶梯（§2.3）：
      阶 1  本地启发式（tts_echo / has_speech / 空文本 / 超长 / 白名单 / address_score）
      阶 2  轻量分类模型兜底（仅「有弱唤醒锚点」的中间带；v1 缺省 None）
      阶 3  fail-ambient（任何不确定 → AMBIENT，绝不误升对话）
  - 不变量（§2.3 / §2.4 矩阵）：**无唤醒锚点（name/wake 均无）+ 无上下文
    （in_conversation=false）→ 永不 USER_MESSAGE**——即使分类模型存在也一样
    （分类器只允许在 wake_hit=true 的中间带打破僵局）。

frozen contract：本模块 0 import EventBus / SpeakerToken / LLMProxy；不发布事件；
Actuator 侧 publish 权限边界保持（src/soul/actuator.py 0 改动）。
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Sequence

# ─────────────────────────────────────────────────────────────
# 常量与默认配置（§3.3 / §3.4 / §4.2 / §4.4 / §4.5）
# ─────────────────────────────────────────────────────────────

# 默认姓名表：从 src/agent/registry.py AGENT_CLASS_MAP（AgentYua → "yua"）推导，
# admin 可用 env VOICE_ADDRESS_NAMES 追加（逗号分隔）。别名以「对 Soul 说话」为准，
# 不含与既有 agent 无关的示例名（如设计矩阵的 "Sora"，本仓无此 agent，避免电视误触）。
DEFAULT_NAME_ALIASES: tuple[str, ...] = (
    "yua", "ruka", "akane", "rem", "ram",
    "mahiru", "anna", "mai", "miku", "aoi",
)

# 显式唤醒词（§3.4 默认建议：嘿/喂/你好/听着/Hey/Listen + 前缀变体）
DEFAULT_WAKE_WORDS: tuple[str, ...] = (
    "嘿", "喂", "你好", "听着", "hey", "listen", "excuse me",
)

# 第二人称指向（§3.2 / §2.2）——中文「你/您」+ 英文 you/your
_SECOND_PERSON_TERMS: tuple[str, ...] = ("你", "您", "you", "your", "yourself")

# 命令/请求动词表（§2.2 imperative_verb）——设计示例：帮我/告诉我/查一下/回答/听/看
_IMPERATIVE_TERMS: tuple[str, ...] = (
    # 中文祈使/请求
    "帮我", "请", "告诉我", "告诉", "查一下", "查查", "查", "搜", "搜索",
    "回答", "解释", "翻译", "记一下", "记住", "写", "打开", "关闭", "开",
    "关", "放", "播放", "唱", "讲", "讲一下", "推荐", "提醒", "找一下",
    "找到", "看看", "听听", "听", "看", "问一下", "问", "设定", "设置",
    "给我", "帮", "麻烦",
    # English imperatives
    "please", "tell", "show", "search", "check", "find", "remind",
    "play", "stop", "start", "answer", "explain", "translate", "look",
    "listen", "help",
)

# 疑问句式标记（§2.2 question_marker）——设计示例：吗/呢/？/怎么/什么/谁/哪
_QUESTION_TERMS: tuple[str, ...] = (
    "吗", "呢", "？", "怎么", "怎样", "如何", "什么", "为什么", "为何",
    "谁", "哪", "哪里", "哪儿", "什么时候", "何时", "多少", "几",
    "是不是", "有没有", "能不能", "会不会", "要不要", "可否", "吧",
    # English question markers
    "what", "who", "where", "when", "why", "how", "which",
    "is it", "are you", "do you", "did you", "can you", "could you",
    "would you", "will you",
)

# 终止标点（§4.3 结句判定）：前段末尾有这些 → 不合并（句中停顿语义）
_SENTENCE_END_PUNCT: tuple[str, ...] = ("。", "！", "？", "…", ".", "!", "?")

# 已归一化文本的「短语级去重」前缀（对齐 MS-2 actuator.py:359-370 stt:sha256）
_DUP_PREFIX = "stt:"

# env 可配置键（admin 可配，§3.4 / §5.1）
_ENV_NAMES = "VOICE_ADDRESS_NAMES"
_ENV_WAKE = "VOICE_WAKE_WORDS"
_ENV_STRONG = "ADDRESS_STRONG"
_ENV_WEAK = "ADDRESS_WEAK"
_ENV_OWNERS = "VOICE_OWNER_IDS"


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ─────────────────────────────────────────────────────────────
# 归一化（对齐 actuator.py:376-380 `_normalize_transcript` 语义：
# NFKC + lower + 去 \s\W_；与 MS-2 stt:sha256 novelty 同一 normalize）
# ─────────────────────────────────────────────────────────────

def normalize_transcript(text: str) -> str:
    """小写 + Unicode NFKC（全形→半形）+ 去标点空白。与 src/soul/actuator.py
    `_normalize_transcript`（MS-2 D9）语义完全一致，用于长度判定与重复抑制 key。"""
    norm = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\W_]+", "", norm, flags=re.UNICODE)


def stt_novelty_key(text: str) -> str:
    """MS-2 句级去重 key：``"stt:" + SHA256(normalize(text))[:12]``（§4.4 重复抑制）。"""
    norm = normalize_transcript(text)
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
    return f"{_DUP_PREFIX}{digest}"


def _soft_text(text: str) -> str:
    """词法分析用软归一化：NFKC + lower，**保留标点与空白**（疑问/终止标点判定需要）。"""
    return unicodedata.normalize("NFKC", text).lower()


# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VoiceGateConfig:
    """唤醒门控 + 路由 + 防抖配置（§3.3 / §3.4 / §4.2 / §4.4 / §4.5）。

    全部字段带默认值；`from_env()` 从环境变量读取 admin 覆盖（VOICE_ADDRESS_NAMES /
    VOICE_WAKE_WORDS / ADDRESS_STRONG / ADDRESS_WEAK / VOICE_OWNER_IDS）。
    """

    # ── address_score 权重（§3.3）──
    w_name: float = 4.0      # 名字 = 最强锚点
    w_wake: float = 3.0      # 显式唤醒词
    w_sp: float = 1.0        # 第二人称
    w_imp: float = 1.0       # 命令/请求动词
    w_q: float = 0.5         # 疑问句式
    w_ctx: float = 2.0       # 对话续接期内

    # ── 阈值（§3.3）──
    address_strong: float = 4.0   # ≥ → USER_MESSAGE
    address_weak: float = 1.5     # ≤ → AMBIENT（fail-ambient 默认）

    # ── 长度 / 碎片（§2.3 / §4.2 / §4.3）──
    max_utterance_chars: int = 500     # 超长 → AMBIENT（防电视长段）
    min_utterance_chars: int = 2       # 短于此 → 纯噪音/碎片 → DROP

    # ── 唤醒词表 / 姓名表（§3.1 / §3.4）──
    name_aliases: tuple[str, ...] = DEFAULT_NAME_ALIASES
    wake_words: tuple[str, ...] = DEFAULT_WAKE_WORDS

    # ── 语音身份白名单（§5.1，对齐 TELEGRAM_OWNER_ID 语义）──
    # None = 未配置不拦（向后兼容，测试友好）；set = 白名单，非成员指向性语音 → AMBIENT
    voice_owner_ids: Optional[frozenset[str]] = None

    # ── 对话续接窗口（§2.2 in_conversation / §3.3 豁免）──
    conversation_context_ms: int = 45_000   # 最近 USER_MESSAGE / AGENT_SPEAK 后此窗口内算续接

    # ── 防洪（§4.5）──
    rate_limit_per_minute: int = 6          # rolling window 超限 → AMBIENT + 计数
    rate_limit_hard_per_minute: int = 12    # 硬上限超限 → DROP
    backoff_base_ms: int = 5_000            # 连续拒收惩罚：5s → 10s → 30s
    backoff_max_ms: int = 30_000
    backoff_reset_ms: int = 120_000         # 120s 无拒收 → 重置步进

    # ── 会话/防抖（§4.1 / §4.2 / §4.3 / §4.4，audio_service 使用）──
    listen_window_ms: int = 30_000
    listen_window_max_ms: int = 120_000     # 对话续接滚动延长上限
    tail_silence_ms: int = 1_200            # 静音超此 → 段结束
    merge_gap_ms: int = 1_500               # 相邻段间隔 < 此 → 合并
    max_segment_seconds: float = 8.0        # 段级硬上限
    max_utterances_per_window: int = 8      # 窗口内 utterance 上限 → 强制关窗
    user_message_cooldown_ms: int = 3_000   # USER_MESSAGE 发布后 3s 冷却
    tts_echo_guard_ms: int = 500            # TTS 播放时长 + 此 → echo 防护窗
    novelty_window_ms: int = 24 * 60 * 60 * 1000  # stt:sha256 去重窗口（对齐 MS-2 日级）

    # ── 分类模型（阶 2，§2.3 / §3.4；v1 缺省 None）──
    classifier_enabled: bool = False
    classifier_timeout_ms: int = 1_500

    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> "VoiceGateConfig":
        """从环境变量读取 admin 覆盖；未设置的键用默认值。"""
        env = env if env is not None else os.environ
        kw: dict = {}

        names_str = env.get(_ENV_NAMES, "").strip()
        if names_str:
            extra = tuple(
                n.strip().lower() for n in re.split(r"[,，]", names_str) if n.strip()
            )
            kw["name_aliases"] = DEFAULT_NAME_ALIASES + extra

        wake_str = env.get(_ENV_WAKE, "").strip()
        if wake_str:
            kw["wake_words"] = tuple(
                w.strip().lower() for w in re.split(r"[,，]", wake_str) if w.strip()
            )

        strong_str = env.get(_ENV_STRONG, "").strip()
        if strong_str:
            try:
                kw["address_strong"] = float(strong_str)
            except ValueError:
                pass

        weak_str = env.get(_ENV_WEAK, "").strip()
        if weak_str:
            try:
                kw["address_weak"] = float(weak_str)
            except ValueError:
                pass

        owners_str = env.get(_ENV_OWNERS, "").strip()
        if owners_str:
            owners = frozenset(
                o.strip() for o in re.split(r"[,，]", owners_str) if o.strip()
            )
            kw["voice_owner_ids"] = owners

        classifier_str = env.get("GATE_CLASSIFIER_ENABLED", "").strip()
        if classifier_str.lower() in ("1", "true", "yes", "on"):
            kw["classifier_enabled"] = True

        return cls(**kw)


# ─────────────────────────────────────────────────────────────
# 路由特征 / 决策
# ─────────────────────────────────────────────────────────────

class RouteDecision(str, Enum):
    USER_MESSAGE = "user_message"   # 定向对话：升级为既有 USER_MESSAGE 链（§5.1）
    AMBIENT = "ambient"             # 环境观察：MS-2 observe 路径原样保留
    DROP = "drop"                   # 无有效语音：仅日志 + 计数


@dataclass(frozen=True)
class RoutingFeatures:
    """§2.2 输入特征向量（全部本地提取，无 LLM 主决策消耗）。"""

    text: str = ""                  # 原始转写
    text_normalized: str = ""       # normalize 后（长度判定 / 去重 key）
    has_speech: bool = True         # mic 能量门控结果
    lang: str = "unknown"           # ASR 语言标签（不参与判定）
    name_hit: bool = False          # 姓名/昵称命中（§3.1）
    wake_hit: bool = False          # 显式唤醒词命中（§3.1）
    second_person: bool = False     # 「你/您」第二人称指向
    imperative_verb: bool = False   # 命令/请求动词
    question_marker: bool = False   # 疑问句式
    in_conversation: bool = False   # 对话冷却期内 / 上一轮本 Soul 发声后短间隔
    tts_echo: bool = False          # 落在 TTS 发声防护窗口（§4.4）
    owner_ok: bool = True           # 语音身份白名单通过（§5.1）
    suppress_repeat: bool = False   # §4.4 stt:sha256 重复 → 不重复升级（DROP）
    force_ambient: bool = False     # §4.3 截断溢出段强制环境观察（防电视长段混入对话）
    device_ref: str = "default"     # 采集设备引用（source 追溯）
    ts_ms: int = 0                  # 事件时间戳（速率/冷却判定）


@dataclass(frozen=True)
class RouteOutcome:
    """路由判定结果（含 trace，供回归与调阈）。"""

    decision: RouteDecision
    address_score: float
    stage: str                 # heuristic / classifier / fail_ambient / invariant / dropped
    reason: str                # 判定原因（trace 用）
    features: RoutingFeatures
    ts_ms: int = 0

    @property
    def is_user_message(self) -> bool:
        return self.decision == RouteDecision.USER_MESSAGE


# ─────────────────────────────────────────────────────────────
# 特征提取（语言分析，全部本地规则）
# ─────────────────────────────────────────────────────────────

def _ascii_word_boundary(alias: str) -> bool:
    """纯 ASCII 别名用词边界正则匹配，防 'mai' in 'email' 类误唤醒。"""
    return bool(re.fullmatch(r"[A-Za-z0-9]+", alias))


def _contains_alias(text_soft: str, alias: str) -> bool:
    if _ascii_word_boundary(alias):
        # ASCII 别名：两侧不能是 ASCII 字母数字（防 'mai' 命中 'email'），
        # 但允许紧邻中文（"Yua帮我" 口语无空格仍算唤名；中文不是 \b 边界）
        return re.search(
            rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text_soft
        ) is not None
    return alias in text_soft


def extract_features(
    text: str,
    config: Optional[VoiceGateConfig] = None,
    *,
    has_speech: bool = True,
    in_conversation: bool = False,
    tts_echo: bool = False,
    owner_ok: bool = True,
    suppress_repeat: bool = False,
    force_ambient: bool = False,
    device_ref: str = "default",
    ts_ms: Optional[int] = None,
    lang: str = "unknown",
) -> RoutingFeatures:
    """§2.2 特征提取：文本侧语言分析（姓名/唤醒词/第二人称/命令/疑问）。

    词法分析在「软归一化」（NFKC + lower，保留标点）上进行——疑问标记「？」、
    终止标点等需要原始标点；text_normalized 用严格 normalize（去标点空白）。
    """
    cfg = config or VoiceGateConfig()
    soft = _soft_text(text)

    name_hit = any(_contains_alias(soft, a) for a in cfg.name_aliases)
    wake_hit = any(_contains_alias(soft, w) for w in cfg.wake_words)

    second_person = any(t in soft for t in _SECOND_PERSON_TERMS)
    imperative_verb = any(t in soft for t in _IMPERATIVE_TERMS)
    question_marker = any(t in soft for t in _QUESTION_TERMS)

    return RoutingFeatures(
        text=text,
        text_normalized=normalize_transcript(text),
        has_speech=has_speech,
        lang=lang,
        name_hit=name_hit,
        wake_hit=wake_hit,
        second_person=second_person,
        imperative_verb=imperative_verb,
        question_marker=question_marker,
        in_conversation=in_conversation,
        tts_echo=tts_echo,
        owner_ok=owner_ok,
        suppress_repeat=suppress_repeat,
        force_ambient=force_ambient,
        device_ref=device_ref,
        ts_ms=ts_ms if ts_ms is not None else _now_ms(),
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


# ─────────────────────────────────────────────────────────────
# address_score（§3.3）
# ─────────────────────────────────────────────────────────────

def compute_address_score(
    features: RoutingFeatures, config: Optional[VoiceGateConfig] = None
) -> float:
    """§3.3 置信度公式：

        address_score = w_name·name_hit + w_wake·wake_hit + w_sp·second_person
                      + w_imp·imperative_verb + w_q·question_marker + w_ctx·in_conversation
    """
    cfg = config or VoiceGateConfig()
    return (
        cfg.w_name * int(features.name_hit)
        + cfg.w_wake * int(features.wake_hit)
        + cfg.w_sp * int(features.second_person)
        + cfg.w_imp * int(features.imperative_verb)
        + cfg.w_q * int(features.question_marker)
        + cfg.w_ctx * int(features.in_conversation)
    )


# ─────────────────────────────────────────────────────────────
# 判定阶梯（§2.3）— 纯函数，无状态
# ─────────────────────────────────────────────────────────────

ClassifierFn = Callable[[RoutingFeatures], str]  # 返回 "directed" | "ambient" | 其他


def route(
    features: RoutingFeatures,
    config: Optional[VoiceGateConfig] = None,
    classifier: Optional[ClassifierFn] = None,
) -> RouteOutcome:
    """三阶判定（§2.3）。纯函数：classifier 为可选注入（v1 缺省 None）。

    阶 1（启发式，确定性场景全覆盖）→ 阶 2（分类模型，仅「有弱唤醒锚点」的中间带）
    → 阶 3（fail-ambient）。不变量锁死：无唤醒锚点 + 无上下文 → 永不 USER_MESSAGE。
    """
    cfg = config or VoiceGateConfig()
    score = compute_address_score(features, cfg)
    ts = features.ts_ms or _now_ms()

    # ── 阶 0：无条件丢弃（任何分数都无效）──────────────────────
    if features.tts_echo:
        return RouteOutcome(RouteDecision.DROP, score, "dropped",
                            "tts_echo: Soul 自身发声被麦克风拾到（§4.4 echo 抑制优先）",
                            features, ts)
    if features.suppress_repeat:
        return RouteOutcome(RouteDecision.DROP, score, "dropped",
                            "stt:sha256 重复抑制：同一转写 novelty 窗口内不重复升级（§4.4）",
                            features, ts)
    if features.force_ambient:
        return RouteOutcome(RouteDecision.AMBIENT, score, "heuristic",
                            "force_ambient: §4.3 截断溢出段强制环境观察（不升级）",
                            features, ts)
    if not features.has_speech:
        return RouteOutcome(RouteDecision.DROP, score, "dropped",
                            "has_speech=false: 无语音能量（§2.3 阶 1）", features, ts)
    if not features.text_normalized:
        return RouteOutcome(RouteDecision.DROP, score, "dropped",
                            "空转写/纯噪音（§2.1 DROP 定义）", features, ts)
    if len(features.text_normalized) < cfg.min_utterance_chars:
        return RouteOutcome(RouteDecision.DROP, score, "dropped",
                            f"碎片转写 len={len(features.text_normalized)} < "
                            f"{cfg.min_utterance_chars}（§4.2 噪音/碎片）", features, ts)

    # ── 阶 1：本地启发式 ──────────────────────────────────────
    if len(features.text_normalized) > cfg.max_utterance_chars:
        return RouteOutcome(RouteDecision.AMBIENT, score, "heuristic",
                            f"超长段 {len(features.text_normalized)} > "
                            f"{cfg.max_utterance_chars}（§4.3 截断合并防电视长段）",
                            features, ts)

    # 身份防线（§5.1）：非白名单成员的指向性语音不升级（→ AMBIENT）
    if not features.owner_ok:
        return RouteOutcome(RouteDecision.AMBIENT, score, "heuristic",
                            "voice owner 不在 VOICE_OWNER_IDS 白名单（§5.1 身份防线）",
                            features, ts)

    # 强阈值（§3.3：≥ 4.0 → USER_MESSAGE）
    if score >= cfg.address_strong:
        # 「仅唤名无下文」修正项（§3.3 #3）：name_hit 但无任何意图信号 → AMBIENT
        # （防「喊名字闲聊/测试」误升级；v1 保守 AMBIENT，不挂起等待）
        if (
            features.name_hit
            and not features.second_person
            and not features.imperative_verb
            and not features.question_marker
        ):
            return RouteOutcome(RouteDecision.AMBIENT, score, "heuristic",
                                "NAME_WITHOUT_INTENT: 仅唤名无下文（§3.3 修正项）",
                                features, ts)
        return RouteOutcome(RouteDecision.USER_MESSAGE, score, "heuristic",
                            f"address_score={score:.1f} ≥ {cfg.address_strong}",
                            features, ts)

    # 弱阈值（§3.3：≤ 1.5 → AMBIENT，fail-ambient 默认）
    if score <= cfg.address_weak:
        return RouteOutcome(RouteDecision.AMBIENT, score, "heuristic",
                            f"address_score={score:.1f} ≤ {cfg.address_weak} "
                            "（无唤醒锚点 → fail-ambient）", features, ts)

    # ── 中间带（weak < score < strong）─────────────────────
    anchor = features.name_hit or features.wake_hit

    if features.in_conversation:
        # §3.3 例外 #7：对话续接期内 → 直接 USER_MESSAGE（弱化唤醒要求）
        return RouteOutcome(RouteDecision.USER_MESSAGE, score, "heuristic",
                            "上下文续接豁免（in_conversation=true，§3.3 #7）",
                            features, ts)

    if not anchor:
        # 不变量（§2.3 设计原则 / §2.4 矩阵）：无唤醒锚点 + 无上下文 → 永不升级。
        # 分类模型也不允许触碰无锚点语音（fail-ambient 是低置信语音的宿命）。
        return RouteOutcome(RouteDecision.AMBIENT, score, "invariant",
                            "无唤醒锚点 + 无上下文 → 不变量拒绝升级（§2.4）",
                            features, ts)

    # 有弱唤醒锚点（wake_hit 且无 name_hit，score 3.x 中间带）→ 阶 2 分类兜底
    if cfg.classifier_enabled and classifier is not None:
        try:
            label = classifier(features)
            if label == "directed":
                return RouteOutcome(RouteDecision.USER_MESSAGE, score, "classifier",
                                    f"阶 2 分类判定 directed（score={score:.1f}）",
                                    features, ts)
            return RouteOutcome(RouteDecision.AMBIENT, score, "classifier",
                                f"阶 2 分类判定 {label!r}（score={score:.1f}）",
                                features, ts)
        except Exception as e:  # fail-closed：模型异常 → 阶 3
            return RouteOutcome(RouteDecision.AMBIENT, score, "fail_ambient",
                                f"阶 2 分类异常 → fail-ambient：{e!r}", features, ts)

    # 阶 3：fail-ambient（v1 无分类模型 / 未启用）
    return RouteOutcome(RouteDecision.AMBIENT, score, "fail_ambient",
                        f"中间带 score={score:.1f} 无分类模型 → fail-ambient（§2.3 阶 3）",
                        features, ts)


# ─────────────────────────────────────────────────────────────
# 防洪 / Backoff（§4.5）— 有状态，独立于纯函数路由
# ─────────────────────────────────────────────────────────────

@dataclass
class VoiceRateLimiter:
    """滚动速率限制（6/min）+ 硬上限（12/min）+ 连续拒收 backoff（5s→10s→30s）。

    - allow_publish(now_ms)  : 是否能升一条 USER_MESSAGE（窗口计数 + backoff 冷却）
    - record_publication()   : 发布成功计数（rolling window）
    - note_rejection()       : 语音被降级/拒收 → backoff 步进（连续误唤醒惩罚）
    - note_acceptance()      : 一次成功的定向对话 → 部分重置
    - reset()                : 窗口重置（重置后 120s 无拒收 → 惩罚归零）
    """

    config: VoiceGateConfig = field(default_factory=VoiceGateConfig)
    _publish_times_ms: list[int] = field(default_factory=list)
    _reject_count: int = 0
    _last_reject_ms: int = 0
    _last_publish_ms: int = 0

    def _backoff_duration(self, now_ms: Optional[int] = None) -> int:
        """指数 backoff：5s → 10s → 30s（封顶），120s 无拒收 → 归零。"""
        now = now_ms if now_ms is not None else _now_ms()
        if self._last_reject_ms and (now - self._last_reject_ms) > self.config.backoff_reset_ms:
            self._reject_count = 0
        if self._reject_count <= 0:
            return 0
        n = min(self._reject_count, 5)
        return min(self.config.backoff_base_ms * (2 ** (n - 1)), self.config.backoff_max_ms)

    def allow_publish(self, now_ms: Optional[int] = None) -> bool:
        """rolling window 6/min 内可发布 且 backoff 冷却已过 且未达硬上限。"""
        now = now_ms if now_ms is not None else _now_ms()
        window_start = now - 60_000
        self._publish_times_ms = [t for t in self._publish_times_ms if t > window_start]

        hard = self.config.rate_limit_hard_per_minute
        if len(self._publish_times_ms) >= hard:
            return False  # 硬上限 → DROP 级

        soft = self.config.rate_limit_per_minute
        if len(self._publish_times_ms) >= soft:
            return False  # §4.5 滚动软限流 → 该语音降级 AMBIENT

        if self._publish_times_ms and (now - self._publish_times_ms[-1]) < self.config.user_message_cooldown_ms:
            return False  # 单轮冷却

        if self._backoff_duration(now) > 0 and (now - self._last_reject_ms) < self._backoff_duration(now):
            return False  # backoff 惩罚期
        return True

    def is_hard_limited(self, now_ms: Optional[int] = None) -> bool:
        """§4.5 硬上限（12/min）→ DROP 级。"""
        now = now_ms if now_ms is not None else _now_ms()
        window_start = now - 60_000
        self._publish_times_ms = [t for t in self._publish_times_ms if t > window_start]
        return len(self._publish_times_ms) >= self.config.rate_limit_hard_per_minute

    def record_publication(self, now_ms: Optional[int] = None) -> None:
        now = now_ms if now_ms is not None else _now_ms()
        self._publish_times_ms.append(now)
        self._last_publish_ms = now

    def note_rejection(self, now_ms: Optional[int] = None) -> None:
        """语音被拒收/降级（门控拒绝率高）→ backoff 步进。"""
        now = now_ms if now_ms is not None else _now_ms()
        if self._last_reject_ms and (now - self._last_reject_ms) > self.config.backoff_reset_ms:
            self._reject_count = 0
        self._reject_count += 1
        self._last_reject_ms = now

    def note_acceptance(self) -> None:
        """成功的定向对话（USER_MESSAGE 升级）：重置拒收计数（§4.5 backoff 重置）。"""
        self._reject_count = 0
        self._last_reject_ms = 0


# ─────────────────────────────────────────────────────────────
# trace 编写（§5.4 可观测性，additive）
# ─────────────────────────────────────────────────────────────

class VoiceGateTracer:
    """把每次判定写结构化 trace（decision / features / address_score / 去路）。

    默认 no-op 收集到内存列表；集成层可注入 writer（如 jsonl file）用于回归与调阈。
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def record(self, outcome: RouteOutcome) -> None:
        self.records.append(
            {
                "ts": _utc_ts(),
                "decision": outcome.decision.value,
                "address_score": round(outcome.address_score, 2),
                "stage": outcome.stage,
                "reason": outcome.reason,
                "text": outcome.features.text,
                "features": {
                    "has_speech": outcome.features.has_speech,
                    "name_hit": outcome.features.name_hit,
                    "wake_hit": outcome.features.wake_hit,
                    "second_person": outcome.features.second_person,
                    "imperative_verb": outcome.features.imperative_verb,
                    "question_marker": outcome.features.question_marker,
                    "in_conversation": outcome.features.in_conversation,
                    "tts_echo": outcome.features.tts_echo,
                    "owner_ok": outcome.features.owner_ok,
                },
            }
        )