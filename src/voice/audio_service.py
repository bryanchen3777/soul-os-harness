"""
src/voice/audio_service.py — VoiceSessionService + UtteranceAssembler（MS-3 IMPLEMENTATION）

设计文档：docs/MS-3-VOICE-INTERACTION-CONTRACT.md §4（音频分段与防抖防洪）。

职责（输入侧纯逻辑，可单测；设备/VAD 侧真实音频采集属 MCP 层，本模块接收
「段级 STT 结果」流）：
  - §4.1 会话级监听窗口：LISTEN_WINDOW_MS=30s（对话续接滚动延长 +10s/轮，上限 120s），
    MAX_UTTERANCES_PER_WINDOW=8 强制关窗（防洪）；
  - §4.2/§4.3 断句合并（utterance assembly）：相邻段间隔 < MERGE_GAP_MS(1.5s) 且
    前段末尾无终止标点 → 合并；合并后 > MAX_UTTERANCE_CHARS → 按句边界截断，
    多余部分单独作为 AMBIENT 溢出（防电视长段）；窗口收束 flush 剩余段（不丢有效输入）；
  - §4.4 防抖：USER_MESSAGE 发布 3s 冷却（router/limiter 侧执行）、TTS echo 抑制
    （AGENT_AUDIO_READY 时间戳 + 播放时长 + 500ms 防护窗内捕获 → DROP）、
    stt:sha256 重复抑制（novelty 窗口内同句不重复升级）；
  - §2.2 in_conversation 上下文特征：最近 USER_MESSAGE / AGENT_SPEAK 后
    CONVERSATION_CONTEXT_MS 内 → 续接豁免信号。

契约相容性：本模块 0 发布事件、0 import consciousness/LLMProxy；产出的
RoutingFeatures 交由 VoiceInputRouter 走既有 USER_MESSAGE 链（无旁路注入）。
frozen contract：与 MS-2 `mic_listen`/`audio_transcribe`（single-shot）无冲突——
本模块是 MS-3 additive 会话模式的纯逻辑层，脚本侧会话工具后续 additive 接入。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from src.voice.gate import (
    RouteDecision,
    RoutingFeatures,
    VoiceGateConfig,
    _now_ms,
    extract_features,
    route,
    stt_novelty_key,
)

logger = logging.getLogger("soul_os.voice")

# 终止标点（§4.3 结句判定）——与 gate._SENTENCE_END_PUNCT 同源
_SENTENCE_END_PUNCT = ("。", "！", "？", "…", ".", "!", "?")


# ─────────────────────────────────────────────────────────────
# UtteranceAssembler（§4.2 / §4.3）— 纯合并状态机
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AssembledUtterance:
    """一段完成（或被截断收束）的 utterance。"""

    text: str
    start_ms: int
    end_ms: int
    needs_retranscribe: bool = False   # 合并产生 → 优先整段重转写（§4.3 备注）
    overflow_text: str = ""            # 截断合并的多余部分（→ AMBIENT，§4.3）


@dataclass
class _PendingUtterance:
    parts: list = field(default_factory=list)  # list[(text, start_ms, end_ms)]
    start_ms: int = 0
    end_ms: int = 0
    needs_retranscribe: bool = False

    def merged_text(self) -> str:
        return "".join(p[0] for p in self.parts)

    def ends_with_sentence_end(self) -> bool:
        return self.merged_text().rstrip().endswith(_SENTENCE_END_PUNCT)


@dataclass
class UtteranceAssembler:
    """§4.3 断句合并状态机（防「一句切 N 段 → N 并发 USER_MESSAGE」）。

    规则：
      - 相邻段间隔 < MERGE_GAP_MS 且前段末尾无终止标点 → 合并追加；
      - 合并后长度 > MAX_UTTERANCE_CHARS → 按句边界截断（溢出部分 AMBIENT）；
      - 窗口收束（flush）→ 剩余未完成段按已有内容产出（不丢有效输入）。
    """

    config: VoiceGateConfig = field(default_factory=VoiceGateConfig)
    _pending: Optional[_PendingUtterance] = None

    def feed(
        self, text: str, start_ms: int, end_ms: int
    ) -> list[AssembledUtterance]:
        """送入一段 STT 结果，返回**已完成**的 utterance（0..n 条）。

        未完成的段停留在内部 pending，等待下一段或 flush。
        """
        text = (text or "").strip()
        if not text:
            return []

        done: list[AssembledUtterance] = []
        if self._pending is not None:
            gap = start_ms - self._pending.end_ms
            can_merge = (
                gap < self.config.merge_gap_ms
                and not self._pending.ends_with_sentence_end()
            )
            if can_merge:
                self._pending.parts.append((text, start_ms, end_ms))
                self._pending.end_ms = end_ms
                self._pending.needs_retranscribe = True
                merged = self._pending.merged_text()
                if len(merged) > self.config.max_utterance_chars:
                    # §4.3 截断合并：按句边界截断，多余部分 → AMBIENT 溢出
                    done.append(self._truncate(self._pending))
                    self._pending = None
                return done
            # 不合并 → 前一段完成，本段开启新 pending
            done.append(self._finalize(self._pending))
            self._pending = None

        self._pending = _PendingUtterance(
            parts=[(text, start_ms, end_ms)],
            start_ms=start_ms,
            end_ms=end_ms,
            needs_retranscribe=False,
        )
        return done

    def flush(self) -> list[AssembledUtterance]:
        """窗口收束（§4.3）：把剩余 pending 按已有内容产出。"""
        if self._pending is None:
            return []
        done = [self._finalize(self._pending)]
        self._pending = None
        return done

    def reset(self) -> None:
        self._pending = None

    # ── 内部 ────────────────────────────────────────────────
    def _finalize(self, p: _PendingUtterance) -> AssembledUtterance:
        over = ""
        text = p.merged_text()
        if len(text) > self.config.max_utterance_chars:
            # 收束时兜底截断（理论上 feed 时已处理；双保险）
            text, over = self._split_at_sentence_boundary(
                text, self.config.max_utterance_chars
            )
        return AssembledUtterance(
            text=text,
            start_ms=p.start_ms,
            end_ms=p.end_ms,
            needs_retranscribe=p.needs_retranscribe,
            overflow_text=over,
        )

    def _truncate(self, p: _PendingUtterance) -> AssembledUtterance:
        text = p.merged_text()
        keep, over = self._split_at_sentence_boundary(
            text, self.config.max_utterance_chars
        )
        return AssembledUtterance(
            text=keep,
            start_ms=p.start_ms,
            end_ms=p.end_ms,
            needs_retranscribe=True,
            overflow_text=over,
        )

    @staticmethod
    def _split_at_sentence_boundary(text: str, max_chars: int) -> tuple[str, str]:
        """§4.3 按句边界截断到 ≤ max_chars：优先在终止标点后断句（丢掉不完整尾巴），
        无句边界则硬切并保留溢出（不丢字）。"""
        if len(text) <= max_chars:
            return text, ""
        # 在 max_chars 内找最后一个终止标点
        cut = -1
        for i in range(min(max_chars, len(text)) - 1, -1, -1):
            if text[i] in _SENTENCE_END_PUNCT:
                cut = i + 1
                break
        if cut <= 0:
            return text[:max_chars], text[max_chars:]
        return text[:cut], text[cut:]


# ─────────────────────────────────────────────────────────────
# VoiceSessionService（§4.1 / §4.4）
# ─────────────────────────────────────────────────────────────

@dataclass
class VoiceSessionService:
    """会话级语音监听窗口 + 防抖去重（MS-3 additive，0 破坏 MS-2 single-shot）。

    用法：
      svc.start_listen_window()
      for seg in mic_segments:            # MCP 会话工具产出段级 STT
          features_list = svc.feed_segment(seg.text, seg.start_ms, seg.end_ms)
          # 每条 features → router.route_features(features)
      for features in svc.end_listen_window():   # 窗口收束 flush
          ...
    """

    config: VoiceGateConfig = field(default_factory=VoiceGateConfig)
    assembler: UtteranceAssembler = field(default_factory=UtteranceAssembler)
    # §4.3 备注：优先「整段重转写」的注入点（本地 ASR）；None → 文本拼接降级路径
    retranscribe: Optional[Callable[[str], str]] = None
    now_fn: Callable[[], int] = _now_ms

    # ── 状态 ────────────────────────────────────────────────
    _window_active: bool = False
    _window_start_ms: int = 0
    _window_end_ms: int = 0
    _utterances_in_window: int = 0
    _last_user_message_ms: int = 0
    _last_agent_speak_ms: int = 0
    _tts_echo_until_ms: int = 0
    _seen_novelty: dict = field(default_factory=dict)  # key → first_seen_ms

    # 统计（观测 / 测试断言）
    duplicate_dropped_count: int = 0
    echo_dropped_count: int = 0
    overflow_ambient_count: int = 0

    # ── §4.1 监听窗口 ───────────────────────────────────────
    def start_listen_window(self, now_ms: Optional[int] = None) -> bool:
        now = now_ms if now_ms is not None else self.now_fn()
        if self._window_active:
            return False
        window = self.config.listen_window_ms
        if self.in_dialog_context(now):
            window = min(window + 10_000, self.config.listen_window_max_ms)
        self._window_active = True
        self._window_start_ms = now
        self._window_end_ms = now + window
        self._utterances_in_window = 0
        self.assembler.reset()
        logger.info(
            f"[VoiceSession] 窗口开启 window_ms={window} "
            f"end={self._window_end_ms} (延長={self.in_dialog_context(now)})"
        )
        return True

    def _maybe_extend_window(self, now_ms: int) -> None:
        """§4.1 对话续接滚动延长 +10s/轮（上限 120s）。"""
        if not self._window_active:
            return
        room = self._window_end_ms - now_ms
        if room < self.config.listen_window_ms:
            new_end = min(
                self._window_end_ms + 10_000,
                self._window_start_ms + self.config.listen_window_max_ms,
            )
            if new_end > self._window_end_ms:
                self._window_end_ms = new_end
                logger.info(f"[VoiceSession] 窗口滚动延长 → end={new_end}")

    def is_window_active(self, now_ms: Optional[int] = None) -> bool:
        now = now_ms if now_ms is not None else self.now_fn()
        if not self._window_active:
            return False
        if now > self._window_end_ms:
            self._window_active = False   # §4.1 窗口超时 → 强制关闭
            return False
        return True

    def end_listen_window(self, now_ms: Optional[int] = None) -> list[RoutingFeatures]:
        """§4.3 窗口收束：flush 剩余段按已有内容产出（不丢有效输入）。"""
        _ = now_ms if now_ms is not None else self.now_fn()
        if not self._window_active:
            return []
        out: list[RoutingFeatures] = []
        for u in self.assembler.flush():
            out.extend(self._utterance_to_features(u))
        self._window_active = False
        return out

    # ── §4.2 / §4.3 段进 ────────────────────────────────────
    def feed_segment(
        self, text: str, seg_start_ms: int, seg_end_ms: int
    ) -> list[RoutingFeatures]:
        """送入一段级 STT 结果 → 合并 → 产出**已完成 utterance** 的特征列表。

        - TTS echo 防护窗内（§4.4）→ 该段直接 DROP（不并入 utterance）；
        - 窗口未开启/超时 → 返回 []（需显式 start_listen_window）。
        """
        now = self.now_fn()
        if not self.is_window_active(now):
            return []

        if self._tts_echo_until_ms > now:
            self.echo_dropped_count += 1
            logger.info("[VoiceSession] TTS echo 抑制 → DROP（§4.4）")
            return [RoutingFeatures(
                text="", text_normalized="", has_speech=False,
                tts_echo=True, ts_ms=now,
            )]

        done = self.assembler.feed(text, seg_start_ms, seg_end_ms)
        out: list[RoutingFeatures] = []
        for u in done:
            self._utterances_in_window += 1
            self._maybe_extend_window(now)
            out.extend(self._utterance_to_features(u))
            if self._utterances_in_window >= self.config.max_utterances_per_window:
                # §4.1 / §4.5 会话上限 → 强制关窗（防洪）
                self._window_active = False
                logger.warning(
                    f"[VoiceSession] 達 {self.config.max_utterances_per_window} "
                    "utterances → 强制关窗"
                )
                break
        return out

    # ── §4.4 echo 抑制 / §2.2 上下文 ────────────────────────
    def mark_tts_echo(self, play_start_ms: int, play_duration_ms: int = 0) -> None:
        """§4.4 TTS echo 防护窗：Soul 自己开口（AGENT_AUDIO_READY）期间捕获 → DROP。

        防护窗 = 播放开始 + 播放时长 + TTS_ECHO_GUARD_MS(500ms)。
        """
        self._tts_echo_until_ms = (
            play_start_ms + int(play_duration_ms) + self.config.tts_echo_guard_ms
        )

    def is_tts_echo(self, now_ms: Optional[int] = None) -> bool:
        now = now_ms if now_ms is not None else self.now_fn()
        return self._tts_echo_until_ms > now

    def note_user_message(self, ts_ms: Optional[int] = None) -> None:
        """记录最后 USER_MESSAGE（§2.2 in_conversation / §4.4 3s 冷却参照）。"""
        self._last_user_message_ms = ts_ms if ts_ms is not None else self.now_fn()

    def note_agent_speak(self, ts_ms: Optional[int] = None) -> None:
        """记录 Soul 发声时间戳（in_conversation 信号源之一）。"""
        self._last_agent_speak_ms = ts_ms if ts_ms is not None else self.now_fn()

    def in_dialog_context(self, now_ms: Optional[int] = None) -> bool:
        """§2.2 in_conversation：最近 USER_MESSAGE / AGENT_SPEAK 后窗口内。"""
        now = now_ms if now_ms is not None else self.now_fn()
        w = self.config.conversation_context_ms
        return (
            (self._last_user_message_ms and now - self._last_user_message_ms < w)
            or (self._last_agent_speak_ms and now - self._last_agent_speak_ms < w)
        )

    def user_message_cooldown_active(self, now_ms: Optional[int] = None) -> bool:
        """§4.4 3s 单轮冷却（发布侧由 VoiceRateLimiter 执行；此处供观测/测试）。"""
        now = now_ms if now_ms is not None else self.now_fn()
        return (
            self._last_user_message_ms > 0
            and (now - self._last_user_message_ms) < self.config.user_message_cooldown_ms
        )

    # ── §4.4 重复抑制 ───────────────────────────────────────
    def _is_novel(self, text: str, now_ms: int) -> bool:
        key = stt_novelty_key(text)
        first = self._seen_novelty.get(key)
        if first is None:
            self._seen_novelty[key] = now_ms
            # 清理窗口外的旧 key（防无限膨胀）
            stale = [k for k, t in self._seen_novelty.items()
                     if now_ms - t > self.config.novelty_window_ms]
            for k in stale:
                self._seen_novelty.pop(k, None)
            return True
        return (now_ms - first) >= self.config.novelty_window_ms

    # ── 收尾构造 ────────────────────────────────────────────
    def _utterance_to_features(self, u: AssembledUtterance) -> list[RoutingFeatures]:
        now = self.now_fn()
        out: list[RoutingFeatures] = []

        # §4.4 重复抑制：同一转写（stt:sha256）在 novelty 窗口内 → 不重复升级
        if not self._is_novel(u.text, now):
            self.duplicate_dropped_count += 1
            logger.info(f"[VoiceSession] 重复抑制 stt:{stt_novelty_key(u.text)} → DROP")
            out.append(extract_features(
                u.text, self.config, has_speech=True,
                in_conversation=self.in_dialog_context(now),
                device_ref="voice-session", ts_ms=now,
                suppress_repeat=True,
            ))
            return out

        # §4.3 备注：优先整段重转写（注入 retranscribe）；异常 → 文本拼接降级
        text = u.text
        if u.needs_retranscribe and self.retranscribe is not None:
            try:
                rt = self.retranscribe(u.text)
                if rt and rt.strip():
                    text = rt.strip()
            except Exception as e:
                logger.warning(f"[VoiceSession] 重转写失败，用文本拼接：{e!r}")

        feats = extract_features(
            text,
            self.config,
            has_speech=True,
            in_conversation=self.in_dialog_context(now),
            device_ref="voice-session",
            ts_ms=now,
        )
        out.append(feats)

        # §4.3 截断溢出的多余部分 → AMBIENT（防电视长段混入对话；force_ambient 锁死不升级）
        if u.overflow_text:
            self.overflow_ambient_count += 1
            out.append(extract_features(
                u.overflow_text, self.config, has_speech=True,
                in_conversation=False, device_ref="voice-session", ts_ms=now,
                # gate 阶 1 强制 AMBIENT（§4.3 截断边界之外内容不升级）
                force_ambient=True,
            ))
        return out


# ─────────────────────────────────────────────────────────────
# AudioStreamResult / process_audio_stream（MS-3.1 additive：设备层完整音频流
# → ASR（注入）→ MS-3 路由判定，端到端闭环）
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AudioStreamResult:
    """设备层完整音频流经 ASR + MS-3 路由判定后的结构化结果（voice_session_stop 返回源）。

    route 字段 = RouteDecision.name（"USER_MESSAGE" / "AMBIENT" / "DROP"）。
    """

    route: str
    decision: Any
    text: str
    has_speech: bool
    address_score: float
    stage: str
    reason: str
    features: RoutingFeatures
    asr_error: str = ""

    @property
    def is_user_message(self) -> bool:
        return self.decision == RouteDecision.USER_MESSAGE


async def process_audio_stream(
    pcm: Any,
    asr_fn: Callable[[Any], str],
    config: Optional[VoiceGateConfig] = None,
    router: Optional[Any] = None,
    *,
    sample_rate: int = 16000,
    energy_threshold: float = 0.01,
    in_conversation: bool = False,
    ts_ms: Optional[int] = None,
    device_ref: str = "voice-session:stream",
    asr_timeout_sec: float = 45.0,
) -> AudioStreamResult:
    """完整音频流端到端：VAD 能量门控 → ASR（注入回调）→ MS-3 路由判定。

    定位（MS-3.1）：设备/MCP 层负责 PCM 缓冲与 VAD 分片；本函数接收完整 float32
    PCM（16k mono）与 asr_fn 注入，产出**已过 MS-3 判定阶梯**的结果——「转写后
    100% 走 InputRouter 判定」的落点（router 注入 VoiceInputRouter(bus=None) 实例；
    None → gate.route 纯函数兜底）。

    不变量（§2.4 锁死）：in_conversation=False（设备层无对话上下文通道）+ 无唤醒
    锚点 → gate 阶 3/不变量 100% 降级 AMBIENT/DROP，**永不 USER_MESSAGE**。

    fail-closed：
      - pcm 空/静音 → has_speech=False → gate DROP（无语音能量）；
      - ASR 异常/超时 → has_speech 抹平为 False → 降级 DROP（asr_error 标注），
        绝不因 ASR 坏而误升 USER_MESSAGE，也不抛未捕获异常阻断主循环。
    """
    cfg = config or VoiceGateConfig()
    ts = ts_ms if ts_ms is not None else _now_ms()

    import numpy as np  # 惰性：纯计算库，保持模块顶部 0 重依赖

    try:
        arr = np.asarray(pcm, dtype=np.float32).reshape(-1)
        arr = arr[np.isfinite(arr)]  # NaN/Inf 防护
    except Exception:  # pragma: no cover — 异常输入只降级不抛出
        arr = np.zeros(0, dtype=np.float32)

    has_speech = False
    if arr.size > 0:
        rms = float(np.sqrt(np.mean(np.square(arr))))
        has_speech = rms >= energy_threshold

    text = ""
    asr_error = ""
    if has_speech:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(asr_fn, arr), timeout=asr_timeout_sec
            )
            text = (result or "").strip()
        except Exception as e:
            asr_error = repr(e)
            # fail-closed：ASR 失败 → 抹平语音判定 → DROP 级，绝不误升
            has_speech = False
            logger.warning(
                f"[VoiceStream] ASR 失败 fail-closed → DROP（{e!r}）；"
                f"sample_rate={sample_rate} elapsed_ms={_now_ms() - ts}"
            )

    features = extract_features(
        text, cfg, has_speech=has_speech, in_conversation=in_conversation,
        device_ref=device_ref, ts_ms=ts,
    )
    if router is not None:
        # 100% 走 InputRouter 判定链（gate + §4.5 防洪 + trace），无旁路
        outcome = await router.route_features(features)
    else:
        outcome = route(features, cfg)

    return AudioStreamResult(
        route=outcome.decision.name,
        decision=outcome.decision,
        text=features.text,
        has_speech=has_speech,
        address_score=outcome.address_score,
        stage=outcome.stage,
        reason=outcome.reason,
        features=features,
        asr_error=asr_error,
    )