# test_ms3_voice_gate.py
# Soul OS — MS-3 Voice Interaction：三路分流矩阵 + 唤醒门控 + VAD 防抖防洪
#
# 设计文档：docs/MS-3-VOICE-INTERACTION-CONTRACT.md（§2 路由矩阵 / §3 唤醒门控 /
#           §4 VAD 防抖 / §5 契约相容性）
# 运行：.\.venv\Scripts\python.exe -m pytest tests/test_ms3_voice_gate.py -v

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.voice.audio_service import (
    AssembledUtterance,
    UtteranceAssembler,
    VoiceSessionService,
)
from src.voice.gate import (
    RouteDecision,
    VoiceGateConfig,
    VoiceRateLimiter,
    compute_address_score,
    extract_features,
    normalize_transcript,
    route,
    stt_novelty_key,
)
from src.voice.input_router import (
    VoiceInputRouter,
    VoiceRouterTarget,
    load_voice_owner_ids,
    owner_hash,
)


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────

class FakeBus:
    def __init__(self):
        self.published: list = []

    async def publish(self, event):
        self.published.append(event)


def features(text, **kw):
    return extract_features(text, **kw)


# ─────────────────────────────────────────────────────────────
# §2.4 分流路由矩阵（十场景）
# ─────────────────────────────────────────────────────────────

# 每笔: (场景名, 文本, 期望决策, 期望 address_score)
MATRIX_CASES = [
    # #1 强：姓名 + 疑问 → USER_MESSAGE（4.0 name + 0.5 q）
    ("matrix1_name_question", "Yua，今天天气怎么样？", RouteDecision.USER_MESSAGE, 4.5),
    # #2 强：唤醒词 + 命令 → USER_MESSAGE（3.0 wake + 1.0 imperative = 4.0 == strong）
    ("matrix2_wake_imperative", "嘿，帮我记一下明天开会", RouteDecision.USER_MESSAGE, 4.0),
    # #3 仅唤名无下文 → AMBIENT（NAME_WITHOUT_INTENT 修正项）
    ("matrix3_name_only", "Ruka！", RouteDecision.AMBIENT, 4.0),
    # #4 他人对话（第二人称但无唤醒锚点）→ AMBIENT（fail-ambient，1.0 sp + 0.5 q == weak）
    ("matrix4_other_conversation", "你昨天去哪了", RouteDecision.AMBIENT, 1.5),
    # #5 电视/叙述 → AMBIENT
    ("matrix5_tv", "我觉得这个结局好烂", RouteDecision.AMBIENT, 0.0),
    # #6 自言自语 → AMBIENT
    ("matrix6_self_talk", "唉，又忘带钥匙了", RouteDecision.AMBIENT, 0.0),
    # #7 对话续接豁免（命令 + 对话期 2.0）→ USER_MESSAGE（1.0 imp + 2.0 ctx）
    ("matrix7_context_exemption", "帮我查一下", RouteDecision.USER_MESSAGE, 3.0),
    # #8 同诉求但不在对话期（1.0 sp + 1.0 imp 中间带）→ AMBIENT（不变量拒绝）
    ("matrix8_no_context", "你帮我查一下", RouteDecision.AMBIENT, 2.0),
    # #9 纯噪音/音乐（has_speech 误真但无内容线索）→ AMBIENT
    ("matrix9_noise", "啦啦啦", RouteDecision.AMBIENT, 0.0),
    # #10 自己 TTS 回声 → DROP（echo 抑制优先）
    ("matrix10_tts_echo", "好的，我知道了", RouteDecision.DROP, 0.0),
]


@pytest.mark.parametrize("name,text,expected,exp_score", MATRIX_CASES)
def test_routing_matrix(name, text, expected, exp_score):
    cfg = VoiceGateConfig()
    ctx = name == "matrix7_context_exemption"
    echo = name == "matrix10_tts_echo"
    f = features(text, in_conversation=ctx, tts_echo=echo)
    outcome = route(f, cfg)
    assert outcome.decision == expected, (
        f"{name}: {text!r} → {outcome.decision} (reason={outcome.reason})"
    )
    assert outcome.address_score == pytest.approx(exp_score), (
        f"{name}: address_score={outcome.address_score} != {exp_score}"
    )


def test_matrix1_threshold_semantics():
    """§3.3 阈值语义：姓名(4.0)+疑问(0.5)=4.5 ≥ 4.0 → USER_MESSAGE"""
    f = features("Yua，今天天气怎么样？")
    assert compute_address_score(f) == pytest.approx(4.5)
    assert route(f).decision == RouteDecision.USER_MESSAGE


def test_matrix2_wake_plus_second_person_strong():
    """§3.1 关键约束：通用唤醒词需叠加第二人称/指令才构成强信号"""
    # 唤醒词 + 第二人称 → 3.0 + 1.0 + 1.0 = 5.0 ≥ 4.0 → USER_MESSAGE
    f = features("嘿，你能不能帮我查一下天气")
    assert route(f).decision == RouteDecision.USER_MESSAGE


def test_wake_word_alone_is_weak():
    """只有唤醒词（Hey）无指令 → 3.0 落中间带 → fail-ambient（防电视里'嘿'误唤醒）"""
    f = features("Hey！")
    assert compute_address_score(f) == pytest.approx(3.0)
    assert route(f).decision == RouteDecision.AMBIENT


# ─────────────────────────────────────────────────────────────
# §3.3 唤醒门控：阈值边界 / 修正项 / 上下文豁免
# ─────────────────────────────────────────────────────────────

def test_strong_threshold_exact():
    """address_score 精确 == 4.0 → USER_MESSAGE（唤醒词+命令）"""
    f = features("嘿，帮我")
    assert compute_address_score(f) == pytest.approx(4.0)
    assert route(f).decision == RouteDecision.USER_MESSAGE


def test_weak_threshold_exact_ambient():
    """address_score 精确 == 1.5 → AMBIENT（第二人称+疑问，无锚点）"""
    f = features("你昨天去哪了")
    assert compute_address_score(f) == pytest.approx(1.5)
    assert route(f).decision == RouteDecision.AMBIENT


def test_name_without_intent_rule():
    """§3.3 #3 修正项：name_hit 但无任何意图信号 → AMBIENT"""
    for text in ["Ruka", "yua", "Yua！", "Hey Ruka"]:
        f = features(text)
        assert f.name_hit, text
        assert route(f).decision == RouteDecision.AMBIENT, text


def test_name_with_question_upgrades():
    """姓名 + 疑问 → 有意图 → USER_MESSAGE（不触发 NAME_WITHOUT_INTENT）"""
    f = features("Yua，你在吗？")
    assert route(f).decision == RouteDecision.USER_MESSAGE


def test_name_with_imperative_upgrades():
    """姓名 + 命令 → USER_MESSAGE"""
    f = features("Ruka，帮我看看邮箱")
    assert route(f).decision == RouteDecision.USER_MESSAGE


def test_context_exemption_midband():
    """§3.3 #7：中间带 + in_conversation=true → 直接 USER_MESSAGE（弱化唤醒要求）"""
    # 无锚点: imp(1.0)+ctx(2.0)=3.0（中间带，1.5<3.0<4.0）+ 上下文 → 豁免升级
    f = features("帮我查一下", in_conversation=True)
    assert compute_address_score(f) == pytest.approx(3.0)
    assert route(f).decision == RouteDecision.USER_MESSAGE

    # 中间带但带 ctx：sp(1.0)+q(0.5)+ctx(2.0)=3.5 → 中间带 + 上下文 → USER_MESSAGE
    f2 = features("你能做到吗", in_conversation=True)
    assert compute_address_score(f2) == pytest.approx(3.5)
    assert route(f2).decision == RouteDecision.USER_MESSAGE


def test_midband_without_context_fail_ambient():
    """§2.3 阶 3：#8 中间带无上下文 → 不变量拒绝（分类模型也不允许触碰）"""
    f = features("你帮我查查")   # sp(1.0)+imp(1.0)=2.0，中间带，无锚点
    assert 1.5 < compute_address_score(f) < 4.0
    outcome = route(f)
    assert outcome.decision == RouteDecision.AMBIENT
    assert outcome.stage == "invariant"


# ─────────────────────────────────────────────────────────────
# 不变量：无唤醒锚点 + 无上下文 → 永不 USER_MESSAGE（100% 降级验证）
# ─────────────────────────────────────────────────────────────

def _all_intent_combinations():
    """枚举 second_person / imperative / question 的 2^3 组合 × 代表性文本。"""
    cases = []
    for sp in (False, True):
        for imp in (False, True):
            for q in (False, True):
                parts = []
                if sp:
                    parts.append("你")
                if imp:
                    parts.append("帮我")
                if q:
                    parts.append("什么")
                text = "".join(parts) or "随便聊聊"
                cases.append((text, sp, imp, q))
    return cases


def test_invariant_no_anchor_no_context_never_upgrades():
    """不变量（§2.3 / §2.4）：无唤醒锚点 + 无上下文 → 任何特征组合都不允许 USER_MESSAGE。

    枚举所有 第二人称×命令×疑问 组合；即使配置分类模型判 directed 也不升级。
    """
    cfg = VoiceGateConfig()
    for text, sp, imp, q in _all_intent_combinations():
        f = extract_features(text, cfg)
        assert not f.name_hit and not f.wake_hit, text
        assert not f.in_conversation
        outcome = route(f, cfg, classifier=lambda feats: "directed")  # 恶意/错误 classifier
        assert outcome.decision != RouteDecision.USER_MESSAGE, (
            f"不变量被破坏: {text!r} (sp={sp},imp={imp},q={q}) → {outcome.decision}"
        )
        assert outcome.decision in (RouteDecision.AMBIENT, RouteDecision.DROP)


def test_fragment_in_dialog_never_upgrades():
    """防抖防洪：即使对话期，碎片级转写（< MIN_UTTERANCE_CHARS）→ DROP（不升级）"""
    f = features("嗯", in_conversation=True)
    assert route(f).decision == RouteDecision.DROP


def test_only_second_person_no_anchor_ambient():
    """§3.2 他人对话：即使含第二人称『你』，无唤醒锚点 → AMBIENT"""
    f = features("你昨天是不是去了那家店")
    assert f.second_person and not f.name_hit and not f.wake_hit
    assert route(f).decision == RouteDecision.AMBIENT


# ─────────────────────────────────────────────────────────────
# §2.3 阶 0：DROP 场景
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text,kwargs",
    [
        # TTS 回声（最先判定）
        ("好的，我知道了", {"tts_echo": True}),
        # 无语音能量
        ("Yua 你好", {"has_speech": False}),
        # 空转写
        ("", {}),
        # 纯标点/噪音
        ("！！！", {}),
        # 碎片（< MIN_UTTERANCE_CHARS=2）
        ("嗯", {}),
    ],
)
def test_drop_cases(text, kwargs):
    f = features(text, **kwargs)
    outcome = route(f)
    assert outcome.decision == RouteDecision.DROP, (text, outcome.reason)


def test_forced_ambient_overflow():
    """§4.3 截断溢出段强制环境观察（不升级）"""
    f = features("这是一段很长很长的电视台词，Yua 出现在中间……",
                 force_ambient=True)
    outcome = route(f)
    assert outcome.decision == RouteDecision.AMBIENT
    assert outcome.stage == "heuristic"


def test_repeat_suppression_drop():
    """§4.4 stt:sha256 重复抑制：同一转写 → 不重复升级"""
    f = features("Yua，今天天气怎么样？", suppress_repeat=True)
    outcome = route(f)
    assert outcome.decision == RouteDecision.DROP
    assert "重复" in outcome.reason


def test_owner_denied_ambient():
    """§5.1 身份防线：非白名单成员的指向性语音 → AMBIENT"""
    f = features("Yua，帮我查天气", owner_ok=False)
    outcome = route(f)
    assert outcome.decision == RouteDecision.AMBIENT
    assert "白名单" in outcome.reason


# ─────────────────────────────────────────────────────────────
# §2.3 阶 2 分类模型（中间带，v1 缺省 None）
# ─────────────────────────────────────────────────────────────

def test_classifier_directed_upgrades_wake_midband():
    """中间带 + wake 锚点 + classifier=directed → USER_MESSAGE"""
    cfg = VoiceGateConfig(classifier_enabled=True)
    f = features("嘿，这周末有安排吗")
    assert cfg.address_weak < compute_address_score(f) < cfg.address_strong
    outcome = route(f, cfg, classifier=lambda feats: "directed")
    assert outcome.decision == RouteDecision.USER_MESSAGE
    assert outcome.stage == "classifier"


def test_classifier_ambient_stays_ambient():
    cfg = VoiceGateConfig(classifier_enabled=True)
    f = features("嘿，这周末有安排吗")
    outcome = route(f, cfg, classifier=lambda feats: "ambient")
    assert outcome.decision == RouteDecision.AMBIENT


def test_classifier_error_fail_ambient():
    """模型异常 → fail-closed → AMBIENT（§2.3 阶 3）"""
    cfg = VoiceGateConfig(classifier_enabled=True)

    def boom(_feats):
        raise RuntimeError("model timeout")

    f = features("嘿，这周末有安排吗")
    outcome = route(f, cfg, classifier=boom)
    assert outcome.decision == RouteDecision.AMBIENT
    assert outcome.stage == "fail_ambient"


def test_classifier_disabled_by_default():
    """v1 缺省：GATE_CLASSIFIER_ENABLED=false，分类模型不启用"""
    cfg = VoiceGateConfig.from_env({})  # 无 env
    assert cfg.classifier_enabled is False


# ─────────────────────────────────────────────────────────────
# §3.4 可配置项 / 特征提取细节
# ─────────────────────────────────────────────────────────────

def test_ascii_alias_word_boundary():
    """纯 ASCII 别名用词边界：'mai' 不应命中 'email'（防电视误唤醒）"""
    f = extract_features("please email me the file")
    assert f.name_hit is False


def test_ascii_alias_hit():
    f = extract_features("Hey mai, what do you think")
    assert f.name_hit is True


def test_env_config_overrides():
    env = {
        "ADDRESS_STRONG": "5.0",
        "ADDRESS_WEAK": "2.0",
        "VOICE_WAKE_WORDS": "开启,启动",
        "VOICE_ADDRESS_NAMES": "sora,小亚",
        "VOICE_OWNER_IDS": "bryan,alice",
    }
    cfg = VoiceGateConfig.from_env(env)
    assert cfg.address_strong == 5.0
    assert cfg.address_weak == 2.0
    assert "开启" in cfg.wake_words and "启动" in cfg.wake_words
    assert "sora" in cfg.name_aliases and "小亚" in cfg.name_aliases
    assert cfg.voice_owner_ids == frozenset({"bryan", "alice"})


def test_normalize_aligns_with_m2_semantics():
    """normalize 对齐 MS-2 stt:sha256 语义：NFKC + lower + 去标点空白"""
    assert normalize_transcript("Yua，今天天气怎么样？") == "yua今天天气怎么样"
    # 与 MS-2 actuator 的 _normalize_transcript 保持一致（若可 import）
    try:
        from src.soul.actuator import Actuator
        for t in ("Yua，今天天气怎么样？", "Hey Ruka!!", "您好，請問現在幾點？"):
            assert normalize_transcript(t) == Actuator._normalize_transcript(t), t
    except ImportError:
        pass


def test_stt_novelty_key_format():
    """§4.4 stt:sha256 格式：'stt:' + SHA256(normalize)[:12]"""
    key = stt_novelty_key("Yua，今天天气怎么样？")
    assert key.startswith("stt:")
    assert len(key) == 4 + 12
    # 同一句（不同标点）→ 同 key（normalize 后一致）
    assert stt_novelty_key("Yua，今天天气怎么样？") == stt_novelty_key(" yua今天天气怎么样 ")
    # 不同句 → 不同 key
    assert stt_novelty_key("Yua，今天天气怎么样？") != stt_novelty_key("Ruka，早上好")


def test_owner_ids_env_parsing():
    env = {"VOICE_OWNER_IDS": "1696287850, 12345"}
    assert load_voice_owner_ids(env) == frozenset({"1696287850", "12345"})
    assert load_voice_owner_ids({}) is None


def test_owner_hash_deterministic():
    assert owner_hash("bryan") == owner_hash("bryan")
    assert len(owner_hash("bryan")) == 8
    assert owner_hash("bryan") != owner_hash("someone")


# ─────────────────────────────────────────────────────────────
# §4.5 防洪 / Backoff
# ─────────────────────────────────────────────────────────────

def test_rate_limiter_allows_under_limit():
    lim = VoiceRateLimiter(VoiceGateConfig(rate_limit_per_minute=6,
                                           rate_limit_hard_per_minute=12))
    base = 1_000_000
    # 每秒一条 → 6 条内都允许（滚动 60s 窗口）
    for i in range(6):
        assert lim.allow_publish(base + i * 10_000), i
        lim.record_publication(base + i * 10_000)


def test_rate_limiter_soft_limit_denies():
    lim = VoiceRateLimiter(VoiceGateConfig(rate_limit_per_minute=6,
                                           rate_limit_hard_per_minute=12))
    base = 1_000_000
    for i in range(6):
        lim.record_publication(base + i * 1_000)   # 6 条集中在 5 秒内
    # 窗口内 6 条 ≥ 6 → 软限流拒绝
    assert lim.allow_publish(base + 6_000) is False


def test_rate_limiter_hard_limit():
    lim = VoiceRateLimiter(VoiceGateConfig(rate_limit_per_minute=6,
                                           rate_limit_hard_per_minute=12))
    base = 1_000_000
    for i in range(12):
        lim.record_publication(base + i * 1_000)
    assert lim.is_hard_limited(base + 12_000) is True
    # 软限流在硬上限之前已拒绝（宽容判断：至少不允许发布）
    assert lim.allow_publish(base + 12_000) is False


def test_rate_limiter_cooldown_3s():
    lim = VoiceRateLimiter(VoiceGateConfig(user_message_cooldown_ms=3_000))
    base = 1_000_000
    lim.record_publication(base)
    assert lim.allow_publish(base + 1_000) is False      # 1s 后仍冷却
    assert lim.allow_publish(base + 3_100) is True       # 3.1s 后放行


def test_rate_limiter_backoff_progression():
    cfg = VoiceGateConfig(backoff_base_ms=5_000, backoff_max_ms=30_000,
                          backoff_reset_ms=120_000)
    lim = VoiceRateLimiter(cfg)
    base = 1_000_000
    lim.note_rejection(base)
    # 第 1 次拒收 → 5s 惩罚期
    assert lim.allow_publish(base + 1_000) is False
    assert lim.allow_publish(base + 5_100) is True
    # 连续拒收 → 10s
    lim.note_rejection(base + 6_000)
    assert lim.allow_publish(base + 6_100) is False
    assert lim.allow_publish(base + 16_100) is True
    # 第 3 次 → 20s；第 4 次 → 40s 封顶 30s
    lim.note_rejection(base + 20_000)
    lim.note_rejection(base + 40_000)
    assert lim.allow_publish(base + 69_000) is False   # 距最后拒收 29s < 30s
    assert lim.allow_publish(base + 70_500) is True    # 距最后拒收 30.5s ≥ 30s


def test_rate_limiter_acceptance_resets_backoff():
    cfg = VoiceGateConfig(backoff_base_ms=5_000, backoff_reset_ms=120_000)
    lim = VoiceRateLimiter(cfg)
    lim.note_rejection(1_000_000)
    lim.note_rejection(2_000_000)
    lim.note_acceptance()                      # 成功定向对话 → 重置
    assert lim.allow_publish(3_000_000) is True


# ─────────────────────────────────────────────────────────────
# §4.2 / §4.3 UtteranceAssembler（断句合并）
# ─────────────────────────────────────────────────────────────

def test_assembler_merges_short_gap():
    """相邻段间隔 < 1.5s 且前段无终止标点 → 合并（一句 N 段只发一条）"""
    asm = UtteranceAssembler()
    assert asm.feed("Yua", 0, 800) == []                 # 首段 pending
    assert asm.feed("帮我查天气", 1_200, 2_000) == []    # gap=400ms → 合并，仍 pending
    # 大间隔段到来 → 前段结句（单条完整 utterance，非 N 段 N 条）
    done = asm.feed("谢谢。", 3_600, 4_000)              # gap=1600ms ≥ 1500 → 不合并
    assert len(done) == 1
    u = done[0]
    assert u.text == "Yua帮我查天气"
    assert u.needs_retranscribe is True
    assert u.end_ms == 2_000
    # "谢谢。" 留在 pending → flush 产出（不丢有效输入）
    assert asm.flush()[0].text == "谢谢。"


def test_assembler_does_not_merge_sentence_end():
    """前段以终止标点结尾 → 不合并（独立 utterance）"""
    asm = UtteranceAssembler()
    asm.feed("Yua，你好。", 0, 1_000)
    done = asm.feed("帮我查天气", 1_500, 2_400)   # 前段有 '。' → 不合并
    assert len(done) == 1
    assert done[0].text == "Yua，你好。"
    # 第二段仍 pending，flush 产出
    flushed = asm.flush()
    assert len(flushed) == 1
    assert flushed[0].text == "帮我查天气"


def test_assembler_does_not_merge_long_gap():
    """段间隔 ≥ 1.5s → 不合并"""
    asm = UtteranceAssembler()
    asm.feed("Yua", 0, 800)
    done = asm.feed("帮我", 3_000, 3_600)   # gap=2200ms ≥ 1500 → 不合并
    assert len(done) == 1
    assert done[0].text == "Yua"
    flushed = asm.flush()
    assert flushed[0].text == "帮我"


def test_assembler_truncate_overflow():
    """合并后 > MAX_UTTERANCE_CHARS → 按句边界截断 + 溢出（不丢字）"""
    cfg = VoiceGateConfig(max_utterance_chars=20)
    asm = UtteranceAssembler(cfg)
    long_text = "Yua，今天天气怎么样。" * 6   # 72 字
    done = asm.feed(long_text[:20], 0, 1_000)
    assert done == []                          # 前 20 字未达上限 → pending
    done = asm.feed(long_text[20:], 1_200, 2_000)  # gap=200ms → 合并 → 超长截断
    assert len(done) == 1
    u = done[0]
    # 前 20 字内最后一个终止标点 '。' 在 index 11 → keep = 前 12 字
    assert u.text == long_text[:12]
    # 完整保字：keep + overflow == 原文
    assert u.text + u.overflow_text == long_text
    assert asm.flush() == []


def test_assembler_flush_window_close():
    """窗口收束：剩余未完成段按已有内容产出（不丢有效输入）"""
    asm = UtteranceAssembler()
    asm.feed("Yua", 0, 500)
    assert asm.flush()[0].text == "Yua"


# ─────────────────────────────────────────────────────────────
# §4.1 / §4.4 VoiceSessionService（会话窗口 + 防抖）
# ─────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self, start=1_000_000):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, ms):
        self.t += ms


def make_session(clock, **cfg_kw):
    cfg = VoiceGateConfig(**cfg_kw)
    return VoiceSessionService(config=cfg, now_fn=clock)


def test_session_window_lifecycle():
    clock = FakeClock()
    svc = make_session(clock)
    assert svc.start_listen_window() is True
    assert svc.is_window_active()
    # 超时 → 强制关闭
    clock.advance(31_000)
    assert svc.is_window_active() is False
    # 窗口外 feed → 空
    assert svc.feed_segment("Yua 你好", clock.t, clock.t + 500) == []


def test_session_window_extends_in_dialog():
    clock = FakeClock()
    svc = make_session(clock, listen_window_ms=30_000, listen_window_max_ms=120_000)
    svc.note_user_message(clock.t)  # 对话期内开启 → +10s
    svc.start_listen_window()
    assert svc._window_end_ms == clock.t + 40_000
    # 推进到窗口尾（room < 30s）→ 完成 utterance 触发滚动延长 +10s
    clock.advance(24_500)                              # now = T0+24500, room=5500
    svc.feed_segment("Yua，帮我。", clock.t, clock.t + 400)      # pending
    clock.advance(2_100)                               # now = T0+26600, room=3400
    svc.feed_segment("帮我查天气。", clock.t, clock.t + 800)     # gap≥1500 → 完成 s1
    # 延长后窗口 end = 原 end(1_040_000) + 10s = 1_050_000
    assert svc._window_end_ms == 1_050_000
    assert svc._window_end_ms >= 1_000_000 + 40_000 + 10_000


def test_session_echo_suppression():
    clock = FakeClock()
    svc = make_session(clock)
    svc.start_listen_window()
    # Soul 发声 2s → echo 防护窗 = start + 2000 + 500
    svc.mark_tts_echo(clock.t, 2_000)
    feats = svc.feed_segment("这是一段回复", clock.t + 1_000, clock.t + 1_800)
    assert len(feats) == 1
    assert feats[0].tts_echo is True
    assert svc.echo_dropped_count == 1
    # 防护窗外 → 正常处理（段停留在 pending，flush 产出）
    clock.advance(3_000)
    svc.feed_segment("Yua 你好", clock.t, clock.t + 500)
    flushed = svc.end_listen_window()
    assert len(flushed) == 1
    assert flushed[0].tts_echo is False


def test_session_repeat_suppression():
    clock = FakeClock()
    svc = make_session(clock)
    svc.start_listen_window()
    # U1：s1+s2 合并，s3（间隔大）触发完成 → novelty 记录，不抑制
    svc.feed_segment("Yua，", clock.t, clock.t + 400)
    svc.feed_segment("今天天气怎么样。", clock.t + 800, clock.t + 1600)
    clock.advance(2_000)
    f1 = svc.feed_segment("再问一句。", clock.t, clock.t + 400)   # 完成 U1
    assert len(f1) == 1 and f1[0].suppress_repeat is False
    assert f1[0].text == "Yua，今天天气怎么样。"
    # U2：同一整句 → stt:sha256 命中 → 抑制
    svc.feed_segment("Yua，", clock.t, clock.t + 400)
    svc.feed_segment("今天天气怎么样。", clock.t + 800, clock.t + 1600)
    clock.advance(2_000)
    f2 = svc.feed_segment("再问一句。", clock.t, clock.t + 400)   # 完成 U2（重复）
    assert len(f2) == 1
    assert f2[0].suppress_repeat is True
    assert svc.duplicate_dropped_count == 1


def test_session_max_utterances_force_close():
    clock = FakeClock()
    svc = make_session(clock, max_utterances_per_window=2)
    svc.start_listen_window()
    # 三个完整句（间隔 > 1.5s 不合并）→ 第 2 个完成的 utterance 触发关窗
    f1 = svc.feed_segment("Yua 你好。", clock.t, clock.t + 500)         # 无前段 → pending
    clock.advance(2_000)
    f2 = svc.feed_segment("帮我这个。", clock.t, clock.t + 500)         # 完成 1
    assert svc.is_window_active() is True
    clock.advance(2_000)
    f3 = svc.feed_segment("再问一个。", clock.t, clock.t + 500)         # 完成 2 → 关窗
    assert svc.is_window_active() is False   # 达上限强制关窗（防洪）
    assert svc._utterances_in_window >= 2


def test_session_in_conversation_feature():
    clock = FakeClock()
    svc = make_session(clock)
    svc.note_user_message(clock.t)
    svc.start_listen_window()
    svc.feed_segment("继续刚才那个", clock.t, clock.t + 400)   # pending
    clock.advance(2_000)                                        # gap = 2000-400 = 1600 ≥ 1.5s
    feats = svc.feed_segment("话题吧。", clock.t, clock.t + 400)  # 完成 → in_conversation
    assert len(feats) == 1
    assert feats[0].text == "继续刚才那个"
    assert feats[0].in_conversation is True


def test_session_end_flush():
    """窗口收束 flush 剩余段（不丢有效输入）"""
    clock = FakeClock()
    svc = make_session(clock)
    svc.start_listen_window()
    svc.feed_segment("Yua", clock.t, clock.t + 500)   # 停在 pending
    flushed = svc.end_listen_window()
    assert len(flushed) == 1
    assert flushed[0].text == "Yua"


# ─────────────────────────────────────────────────────────────
# §5.1 VoiceInputRouter：USER_MESSAGE 发布契约对齐 + 防洪执行
# ─────────────────────────────────────────────────────────────

def make_router(bus=None, **cfg_kw):
    cfg = VoiceGateConfig(**cfg_kw)
    return VoiceInputRouter(
        bus=bus,
        config=cfg,
        target=VoiceRouterTarget(agent_id="agent_yua", voice_user_id="bryan",
                                 device_ref="mic1"),
        limiter=VoiceRateLimiter(cfg),
    )


def test_router_publishes_user_message_contract():
    """§5.1 契约表：EventType / payload 双写 / session_id / priority / source"""
    bus = FakeBus()
    router = make_router(bus)
    f = features("Yua，今天天气怎么样？", ts_ms=1_000_000)
    outcome = asyncio.run(router.route_features(f))
    assert outcome.decision == RouteDecision.USER_MESSAGE
    assert router.user_message_count == 1
    assert len(bus.published) == 1

    ev = bus.published[0]
    # 契约：EventType.USER_MESSAGE / HIGH priority / source voice: 前缀
    # （schema.py use_enum_values=True → 实例上 event_type 是 str、priority 是 int）
    assert ev.event_type == "user_message"
    assert ev.priority == 1            # EventPriority.HIGH
    assert ev.source.startswith("voice:")
    # payload：content/text 双写 + user_id/target_user_id/target_agent/mode/participants
    p = ev.payload
    assert p["content"] == "Yua，今天天气怎么样？"
    assert p["text"] == "Yua，今天天气怎么样？"
    assert p["user_id"] == "bryan"
    assert p["target_user_id"] == "bryan"
    assert p["target_agent"] == "agent_yua"
    assert p["agent_id"] == "agent_yua"
    assert p["mode"] == "private"
    assert p["participants"] is None
    # §5.4 additive input_channel
    assert p["input_channel"] == "voice"
    # session_id 对齐 gateway.py:858 / router.py:851
    assert ev.session_id == "session_bryan_agent_yua"


def test_router_ambient_never_publishes():
    """AMBIENT → 不 publish USER_MESSAGE（无旁路注入）"""
    bus = FakeBus()
    router = make_router(bus)
    f = features("我觉得这个结局好烂")   # 电视场景
    outcome = asyncio.run(router.route_features(f))
    assert outcome.decision == RouteDecision.AMBIENT
    assert bus.published == []
    assert router.ambient_count == 1


def test_router_drop_counts():
    bus = FakeBus()
    router = make_router(bus)
    f = features("", has_speech=False)
    outcome = asyncio.run(router.route_features(f))
    assert outcome.decision == RouteDecision.DROP
    assert bus.published == []
    assert router.drop_count == 1


def test_router_flood_degrades_to_ambient():
    """§4.5：超 6/min 软限流 → 降级 AMBIENT（不 publish）"""
    bus = FakeBus()
    router = make_router(bus, rate_limit_per_minute=6,
                         rate_limit_hard_per_minute=12)
    base = 1_000_000
    loop = asyncio.new_event_loop()
    try:
        # 6 条，每 4s 一条（避开 3s 冷却，聚焦速率限制）
        for i in range(6):
            f = features(f"Yua，问第{i}个问题", ts_ms=base + i * 4_000)
            loop.run_until_complete(router.route_features(f))
        assert router.user_message_count == 6
        assert len(bus.published) == 6
        # 第 7 条：60s 窗口内已有 6 条 ≥ 6 → 软限流 → AMBIENT
        f7 = features("Yua，再来一个问题", ts_ms=base + 26_000)
        o7 = loop.run_until_complete(router.route_features(f7))
        assert o7.decision == RouteDecision.AMBIENT
        assert o7.stage == "flood"
        assert len(bus.published) == 6
    finally:
        loop.close()


def test_router_hard_limit_drops():
    """§4.5：硬上限 12/min → DROP"""
    bus = FakeBus()
    router = make_router(bus, rate_limit_hard_per_minute=3,
                         rate_limit_per_minute=3,
                         user_message_cooldown_ms=0)
    base = 1_000_000
    loop = asyncio.new_event_loop()
    try:
        for i in range(3):
            f = features(f"Yua，问第{i}个问题", ts_ms=base + i * 1_000)
            loop.run_until_complete(router.route_features(f))
        f4 = features("Yua，再问一个", ts_ms=base + 4_000)   # 有意图 → gate USER_MESSAGE → 硬上限 DROP
        o4 = loop.run_until_complete(router.route_features(f4))
        assert o4.decision == RouteDecision.DROP
        assert o4.stage == "flood"
        assert len(bus.published) == 3
    finally:
        loop.close()


def test_router_owner_whitelist_enforced():
    """§5.1：VOICE_OWNER_IDS 白名单——非成员指向性语音不升级"""
    bus = FakeBus()
    router = make_router(bus, voice_owner_ids=frozenset({"bryan"}))
    f = features("Yua，帮我查天气", owner_ok=False)
    outcome = asyncio.run(router.route_features(f))
    assert outcome.decision == RouteDecision.AMBIENT
    assert bus.published == []


def test_router_touch_bryan_last_seen_side_effect(monkeypatch):
    """§5.1 副作用链：touch_bryan_last_seen 原样触发（对齐 gateway.py:882-884）"""
    calls = []

    def fake_touch(agent_id, text):
        calls.append((agent_id, text))
        return True

    import src.io.channels.bryan_state as bs
    monkeypatch.setattr(bs, "touch_bryan_last_seen", fake_touch)

    bus = FakeBus()
    router = make_router(bus)
    f = features("Yua，今天天气怎么样？")
    asyncio.run(router.route_features(f))
    assert calls and calls[0][0] == "agent_yua"
    assert calls[0][1] == "Yua，今天天气怎么样？"


def test_router_full_chain_voice_to_user_message():
    """端到端：语音文本 → gate USER_MESSAGE → bus 事件（与既有通道不可区分，仅 provenance）"""
    bus = FakeBus()
    router = make_router(bus)
    f = features("Yua，帮我记一下明天开会")
    asyncio.run(router.route_features(f))
    ev = bus.published[0]
    # payload 与 gateway.py:866-874 / router.py:856-864 并集一致
    for key in ("content", "text", "user_id", "target_user_id",
                "target_agent", "mode"):
        assert key in ev.payload, key
    # 与文本 USER_MESSAGE 同消费链：事件类型一致（消费端 0 分支差异）
    assert ev.event_type == "user_message"   # EventType.USER_MESSAGE（use_enum_values）


def test_invariant_fuzz_no_anchor_never_upgrades():
    """不变量模糊验证：200 条无唤醒锚点随机句（无上下文）→ 100% AMBIENT/DROP。

    固定 seed 可复现；即使恶意 classifier 判 directed 也不升级（fail-closed）。
    """
    import random

    rng = random.Random(42)
    # 意图词片段（第二人称/命令/疑问）——但不包含姓名/唤醒词
    frags = ["你", "帮我", "什么", "怎么", "吗", "呢", "请问", "是吧",
             "where", "what", "please", "you", "is it", "可否", "查", "看"]
    tails = ["", "呀", "呢", "吧", "？", "了", "哦", "哈"]
    cfg = VoiceGateConfig()
    analyzed = 0
    for _ in range(200):
        n = rng.randint(1, 3)
        text = "".join(rng.choice(frags) for _ in range(n)) + rng.choice(tails)
        f = extract_features(text, cfg)
        if f.name_hit or f.wake_hit or f.in_conversation:
            continue   # 只测无唤醒锚点 + 无上下文样本
        if not f.text_normalized:
            continue   # 空/碎片样本跳过（DROP 也算安全，但这里聚焦内容样本）
        analyzed += 1
        outcome = route(f, cfg, classifier=lambda feats: "directed")
        assert outcome.decision != RouteDecision.USER_MESSAGE, (
            f"不变量被破坏: {text!r} → {outcome.decision}"
        )
        assert outcome.decision in (RouteDecision.AMBIENT, RouteDecision.DROP)
    assert analyzed >= 100   # 确认覆盖足够样本，否则模糊测试无意义


def test_invariant_strong_intent_no_anchor_samples():
    """人工强对话意图样本（无唤醒锚点 + 无上下文）→ 全部不升级"""
    cfg = VoiceGateConfig()
    samples = [
        "你帮我查一下", "what do you want", "please tell me",
        "你昨天去哪了", "帮我看看这个怎么样", "where are you going",
        "你能告诉我吗", "帮我查查明天天气怎么样", "你什么时候回来",
        "do you know this", "请告诉我现在几点", "你最近好吗",
    ]
    for text in samples:
        f = extract_features(text, cfg)
        assert not f.name_hit and not f.wake_hit, text
        outcome = route(f, cfg, classifier=lambda feats: "directed")
        assert outcome.decision != RouteDecision.USER_MESSAGE, (
            f"不变量被破坏: {text!r} → {outcome.decision}"
        )


def test_e2e_session_to_router_single_publish():
    """端到端：一句切成 N 段 → 合并 → 只发 1 条 USER_MESSAGE（§4.3 核心防洪）。

    VoiceSessionService（段合并）→ VoiceInputRouter（gate + 发布）→ bus。
    """
    clock = FakeClock()
    bus = FakeBus()
    cfg = VoiceGateConfig()
    svc = VoiceSessionService(config=cfg, now_fn=clock)
    router = VoiceInputRouter(
        bus=bus, config=cfg,
        target=VoiceRouterTarget(agent_id="agent_yua", voice_user_id="bryan",
                                 device_ref="mic1"),
        limiter=VoiceRateLimiter(cfg),
    )

    svc.start_listen_window()
    # 一句「Yua 帮我查一下明天天气」被 VAD 切成 5 段，间隔 < 1.5s（合并）
    segments = ["Yua", "帮我查", "一下", "明天", "天气"]
    base = clock.t
    all_feats = []
    t = base
    for i, seg in enumerate(segments):
        f_list = svc.feed_segment(seg, t, t + 300)
        all_feats.extend(f_list)
        t += 1_000   # 段间隔 700ms < 1.5s → 合并
    # 窗口收束 → 合并后的完整 utterance 产出
    flushed = svc.end_listen_window()
    all_feats.extend(flushed)
    assert len(all_feats) == 1, f"期望 1 条合并 utterance，实际 {len(all_feats)} 条"
    assert all_feats[0].text == "Yua帮我查一下明天天气"

    outcome = asyncio.run(router.route_features(all_feats[0]))
    assert outcome.decision == RouteDecision.USER_MESSAGE
    assert len(bus.published) == 1                       # 一句只发一条
    assert bus.published[0].payload["text"] == "Yua帮我查一下明天天气"