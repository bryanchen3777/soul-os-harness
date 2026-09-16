"""
tests/clients/test_vc_capture_pre_roll.py — VC-ASR-ONSET-FIX-1 驗收：
伺服器端「收音前置緩衝（capture pre-roll）」把起音被丟棄的幀接回句子開頭。

症狀（Owner 回報）：VC 語音「有時候一句話會砍掉開頭 1–2 個字」。
病根（唯讀鑑識已定案，兩個丟棄窗口）：
  1. `web_server.py` 的 `on_pcm()` 在 `state != LISTENING` 時整幀靜默 `return`
     （無計數 / 無 log / 無暫存）⇒「ptt_start 抵達伺服器**之前**」那一批幀（起音）直接消失。
     幀長實測 92.875ms（`msg binary bytes=2972` ⇒ 1486 samples @16k），實測丟棄量 650 / 1858 / 1579ms，
     亦有 0ms 實例 ⇒ 這正是「間歇性」來源（狀態翻轉與起音的競速）。
  2. `LISTENING` 但 VAD 尚未跨門檻期間，只累積 `in_speech` 為真的幀
     ⇒ 低能量起音（鼻音 / 濁音開頭，如「嗯」「我」「那」）的前幾幀也進不了 `_frames`。

修法（決策已拍板）：伺服器端 per-session `deque(maxlen=PRE_ROLL_SAMPLES)` 保留最新 500ms 音訊，
於本句首次 `in_speech` 累積時以「最舊→最新」順序前置進 `_frames`；收割點清空。

**本檔最關鍵的不變量**（`test_vad_never_receives_pre_roll_samples`）：
非 LISTENING 的幀只做 pre-roll 暫存，**絕不得餵給 VAD**
（這些幀從前就沒進 VAD，語意必須逐字保持）。

全離線：純 in-process，不碰瀏覽器 / 麥克風 / 音效卡 / 網路。
執行：.venv\\Scripts\\python.exe -m pytest tests/clients/test_vc_capture_pre_roll.py -q
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.web_server import (  # noqa: E402
    PRE_ROLL_MS,
    PRE_ROLL_SAMPLES,
    VAD_SAMPLE_RATE,
    WebSession,
)

FRAME = 100  # 每幀樣本數（測試用小幀；真實幀 1486 samples，語意相同）
PRE_ROLL_LOG_RE = re.compile(r"\[UTT\] pre-roll samples=(\d+) \((\d+)ms\)")

TEST_CONFIG = {"companion": {"id": "agent_test"}}


# ─────────────────────────────────────────────────────────────
# 測試替身（0 網路 / 0 音效硬體）
# ─────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class SpyVAD:
    """間諜 VAD：逐次記錄 `feed()` 收到的樣本，並以腳本決定 `in_speech` / `events`。

    腳本用罄後沿用最後一筆 ⇒ 可精確控制「哪一次 feed 才跨門檻」。
    """

    def __init__(self) -> None:
        self.calls: list[list[float]] = []
        self.in_speech = False
        self.reset_count = 0
        self._plan: list[tuple[bool, tuple]] = []

    def script(self, *entries: tuple[bool, tuple]) -> "SpyVAD":
        self._plan = list(entries)
        return self

    def feed(self, samples) -> list:
        self.calls.append(list(samples))
        idx = len(self.calls) - 1
        events: tuple = ()
        if self._plan:
            entry = self._plan[idx] if idx < len(self._plan) else self._plan[-1]
            self.in_speech, events = entry
        return list(events)

    def reset(self) -> None:
        self.reset_count += 1
        self.in_speech = False

    @property
    def fed_samples(self) -> list[float]:
        return [s for call in self.calls for s in call]


class FakeStreamer:
    def __init__(self) -> None:
        self.started = 0
        self.interrupts = 0

    def start(self) -> None:
        self.started += 1

    def interrupt(self) -> None:
        self.interrupts += 1

    def close(self) -> None:
        pass


class FakeSink:
    first_chunk_time = None

    def close(self) -> None:
        pass


class EmptyASR:
    """恆回空轉錄 ⇒ `_handle_utterance` 走最短的 asr-empty 分支（0 LLM / 0 refiner / 0 網路）。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_error = None

    def transcribe(self, wav: bytes) -> str:
        self.calls += 1
        return ""


def _pcm(values) -> bytes:
    return np.asarray(list(values), dtype="<i2").tobytes()


def _decode(chunk: bytes) -> list[float]:
    """與 web_server.on_pcm 完全相同的解碼式（逐樣本精確比對的基準）。"""
    return (np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0).tolist()


def _make_session(vad: SpyVAD | None = None):
    ws = FakeWS()
    detector = vad if vad is not None else SpyVAD()
    session = WebSession(
        ws,
        config=TEST_CONFIG,
        brain=object(),
        refiner=object(),
        asr=EmptyASR(),
        streamer=FakeStreamer(),
        detector=detector,
        sink=FakeSink(),
    )
    return session, ws, detector


async def _send_frames(session, values, count: int) -> None:
    for i in range(count):
        await session.on_pcm(_pcm(values[i * FRAME:(i + 1) * FRAME]))


async def _harvest(session) -> None:
    """走真實收割觸發路徑：on_ptt_stop → create_task(_handle_utterance) → await。"""
    await session.on_ptt_stop()
    task = session._utterance_task
    assert task is not None, "on_ptt_stop 必須建立收割 task"
    await task


# ─────────────────────────────────────────────────────────────
# 0. 常數（決策拍板值）
# ─────────────────────────────────────────────────────────────

def test_pre_roll_constants_match_spec():
    """PRE_ROLL_MS=500 ⇒ PRE_ROLL_SAMPLES=8000 @16kHz（本票定案值）。"""
    assert PRE_ROLL_MS == 500
    assert VAD_SAMPLE_RATE == 16000
    assert PRE_ROLL_SAMPLES == 8000


# ─────────────────────────────────────────────────────────────
# 1. 前置恢復：IDLE 期間的幀必須回到句子最前面（最舊在前）
# ─────────────────────────────────────────────────────────────

def test_idle_frames_are_prepended_to_captured():
    """IDLE 送 K 幀 → 進 LISTENING 送語音幀 ⇒ captured 最前面就是那 K 幀（逐樣本、最舊在前）。"""
    pre_vals = list(range(1000, 1000 + FRAME * 5))
    speech_vals = list(range(2000, 2000 + FRAME))
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        assert session.state == session.STATE_IDLE
        await _send_frames(session, pre_vals, 5)          # IDLE ⇒ 只進 pre-roll
        assert session._frames == [], "IDLE 幀不得進 _frames"
        assert len(session._pre_roll) == FRAME * 5
        await session.on_ptt_start()                      # D4：不得清空 pre-roll
        assert len(session._pre_roll) == FRAME * 5, "on_ptt_start 不得清空 pre-roll"
        await session.on_pcm(_pcm(speech_vals))

    asyncio.run(_run())

    pre = _decode(_pcm(pre_vals))
    speech = _decode(_pcm(speech_vals))
    assert session._frames[:len(pre)] == pre, "被丟棄的起音幀必須接回句子開頭（最舊→最新）"
    assert session._frames == pre + speech
    assert len(session._pre_roll) == 0, "前置後 pre-roll 必須清空"


# ─────────────────────────────────────────────────────────────
# 2. 上限：只保留最新 PRE_ROLL_SAMPLES 樣本（deque maxlen）
# ─────────────────────────────────────────────────────────────

def test_pre_roll_keeps_only_newest_samples():
    """連續送 > PRE_ROLL_SAMPLES（8000）樣本 ⇒ captured 開頭只保留最新 8000，內容＝尾部切片。"""
    total = PRE_ROLL_SAMPLES + 3 * FRAME  # 8300 > 8000
    vals = list(range(1000, 1000 + total))
    assert len(vals) == total
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        for i in range(total // FRAME):
            await session.on_pcm(_pcm(vals[i * FRAME:(i + 1) * FRAME]))
        assert len(session._pre_roll) == PRE_ROLL_SAMPLES, "deque(maxlen) 必須淘汰最舊樣本"
        await session.on_ptt_start()
        await session.on_pcm(_pcm(list(range(20000, 20000 + FRAME))))

    asyncio.run(_run())

    expected_tail = _decode(_pcm(vals))[-PRE_ROLL_SAMPLES:]
    assert session._frames[:PRE_ROLL_SAMPLES] == expected_tail
    assert session._frames[:PRE_ROLL_SAMPLES] == _decode(_pcm(vals[3 * FRAME:])), "內容必須是最新 8000 樣本"
    assert len(session._frames) == PRE_ROLL_SAMPLES + FRAME


# ─────────────────────────────────────────────────────────────
# 3. ★ 最關鍵：VAD 語意逐字不變（pre-roll 樣本絕不得進 VAD）
# ─────────────────────────────────────────────────────────────

def test_vad_never_receives_pre_roll_samples():
    """送 N 幀（IDLE）＋ M 幀（LISTENING）⇒ feed() 恰好 M 次，且從未收到 pre-roll 樣本。"""
    n_idle, m_listen = 4, 3
    idle_vals = list(range(1000, 1000 + FRAME * n_idle))
    listen_vals = list(range(2000, 2000 + FRAME * m_listen))
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        await _send_frames(session, idle_vals, n_idle)
        assert vad.calls == [], "IDLE 幀絕不得餵 VAD"
        await session.on_ptt_start()
        await _send_frames(session, listen_vals, m_listen)

    asyncio.run(_run())

    assert len(vad.calls) == m_listen, f"feed() 必須恰好被呼叫 {m_listen} 次（實得 {len(vad.calls)}）"
    for i, call in enumerate(vad.calls):
        assert call == _decode(_pcm(listen_vals[i * FRAME:(i + 1) * FRAME])), "餵給 VAD 的必須逐幀是 LISTENING 幀"
    idle_set = set(_decode(_pcm(idle_vals)))
    assert not (set(vad.fed_samples) & idle_set), "pre-roll 樣本絕不得進 VAD（本票最關鍵不變量）"


# ─────────────────────────────────────────────────────────────
# 4. 不跨句洩漏：收割點清空 pre-roll（D3）
# ─────────────────────────────────────────────────────────────

def test_pre_roll_does_not_leak_across_utterances():
    """完整跑「送前置幀 → 觸發收割 → 第二句送幀」⇒ 第二句 captured 不得含第一句的 pre-roll 樣本。"""
    stale_vals = list(range(3000, 3000 + FRAME * 2))
    speech2_vals = list(range(4000, 4000 + FRAME))
    session, _ws, vad = _make_session()
    vad.script((False, ()), (False, ()), (True, ()))  # 第 1 句兩幀皆未跨門檻；第 2 句首幀跨門檻

    async def _run():
        # 第一句：LISTENING 但 VAD 未跨門檻 ⇒ 全進 pre-roll，_frames 仍空
        await session.on_ptt_start()
        await _send_frames(session, stale_vals, 2)
        assert session._frames == []
        assert len(session._pre_roll) == FRAME * 2
        # 觸發真實收割（captured 為空 ⇒ skip 分支）
        await _harvest(session)
        assert session.state == session.STATE_IDLE
        assert len(session._pre_roll) == 0, "D3：收割點必須清空 pre-roll"
        # 第二句
        await session.on_ptt_start()
        await session.on_pcm(_pcm(speech2_vals))

    asyncio.run(_run())

    speech2 = _decode(_pcm(speech2_vals))
    assert session._frames == speech2, "第二句開頭不得被前置第一句的殘留音訊"
    assert not (set(session._frames) & set(_decode(_pcm(stale_vals)))), "第一句 pre-roll 不得洩漏到第二句"


# ─────────────────────────────────────────────────────────────
# 5. 第二個丟棄窗口：LISTENING 但 VAD 未跨門檻的低能量起音
# ─────────────────────────────────────────────────────────────

def test_listening_below_vad_threshold_frames_are_prepended():
    """LISTENING 但尚未跨門檻期間的低能量幀，必須在首次 in_speech 時被前置進 captured。"""
    low_vals = list(range(3000, 3000 + FRAME * 3))
    onset_vals = list(range(4000, 4000 + FRAME))
    session, _ws, vad = _make_session()
    vad.script((False, ()), (False, ()), (False, ()), (True, ()))

    async def _run():
        await session.on_ptt_start()
        await _send_frames(session, low_vals, 3)
        assert session._frames == [], "未跨門檻的幀不得進 _frames"
        assert len(session._pre_roll) == FRAME * 3, "未跨門檻的幀必須進 pre-roll"
        await session.on_pcm(_pcm(onset_vals))

    asyncio.run(_run())

    low = _decode(_pcm(low_vals))
    assert session._frames == low + _decode(_pcm(onset_vals)), "低能量起音幀必須接回句子開頭"
    assert len(session._pre_roll) == 0


# ─────────────────────────────────────────────────────────────
# 6. 超長幀：任何狀態下都丟棄，且不得進 pre-roll
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["IDLE", "LISTENING"])
def test_oversize_frame_dropped_and_never_buffered(state):
    """len(chunk) > MAX_FRAME_BYTES ⇒ 丟棄且不進 _pre_roll（IDLE 與 LISTENING 兩種狀態）。"""
    huge = b"\x10\x00" * (WebSession.MAX_FRAME_BYTES // 2 + 1)  # 65538 bytes > 65536
    assert len(huge) > WebSession.MAX_FRAME_BYTES
    session, _ws, vad = _make_session()

    async def _run():
        if state == "LISTENING":
            await session.on_ptt_start()
        await session.on_pcm(huge)

    asyncio.run(_run())

    assert len(session._pre_roll) == 0, "畸形超長幀絕不得進 pre-roll"
    assert session._frames == []
    assert vad.calls == []


# ─────────────────────────────────────────────────────────────
# 7. prepend 只發生一次（非首次累積不得重複前置）
# ─────────────────────────────────────────────────────────────

def test_prepend_happens_only_once_per_utterance():
    """同一句話內多次累積 ⇒ pre-roll 樣本在 captured 中恰好出現一次。"""
    pre_vals = list(range(1000, 1000 + FRAME * 2))
    speech = [list(range(2000 + i * FRAME, 2000 + (i + 1) * FRAME)) for i in range(3)]
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        await _send_frames(session, pre_vals, 2)
        await session.on_ptt_start()
        for chunk in speech:
            await session.on_pcm(_pcm(chunk))

    asyncio.run(_run())

    pre = _decode(_pcm(pre_vals))
    expected = pre + _decode(_pcm(speech[0])) + _decode(_pcm(speech[1])) + _decode(_pcm(speech[2]))
    assert session._frames == expected, "不得重複前置"
    pre_set = set(pre)
    assert sum(1 for x in session._frames if x in pre_set) == len(pre), "pre-roll 樣本必須恰好出現一次"
    assert session._frames.count(pre[0]) == 1


# ─────────────────────────────────────────────────────────────
# 8. 可觀測性：prepend 非空恰一行；為空不得出現該行
# ─────────────────────────────────────────────────────────────

def test_pre_roll_logs_exactly_one_line_when_prepend_nonempty(capsys, caplog):
    """prepend 非空 ⇒ 恰出現一行 `[UTT] pre-roll samples=N (Xms)`（print + log 各一）。"""
    caplog.set_level(logging.INFO, logger="vc.web_server")
    pre_vals = list(range(1000, 1000 + FRAME * 2))
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        await _send_frames(session, pre_vals, 2)
        await session.on_ptt_start()
        await session.on_pcm(_pcm(list(range(2000, 2000 + FRAME))))
        await session.on_pcm(_pcm(list(range(2100, 2100 + FRAME))))  # 第二次累積不得再記一行

    asyncio.run(_run())

    n = FRAME * 2
    ms = n / VAD_SAMPLE_RATE * 1000
    expected_line = f"[UTT] pre-roll samples={n} ({ms:.0f}ms)"

    printed = [ln for ln in capsys.readouterr().out.splitlines() if PRE_ROLL_LOG_RE.search(ln)]
    assert len(printed) == 1, f"必須恰出現一行（實得 {printed}）"
    assert printed[0].endswith(expected_line)

    logged = [r for r in caplog.records if PRE_ROLL_LOG_RE.search(r.getMessage())]
    assert len(logged) == 1, f"log 必須恰出現一行（實得 {[r.getMessage() for r in logged]}）"
    assert logged[0].getMessage() == expected_line
    assert logged[0].levelno == logging.INFO


def test_pre_roll_does_not_log_when_prepend_empty(capsys, caplog):
    """pre-roll 為空（LISTENING 首幀即跨門檻）⇒ 不得出現該行。"""
    caplog.set_level(logging.INFO, logger="vc.web_server")
    session, _ws, vad = _make_session()
    vad.script((True, ()))

    async def _run():
        await session.on_ptt_start()
        await session.on_pcm(_pcm(list(range(2000, 2000 + FRAME))))

    asyncio.run(_run())

    assert len(session._pre_roll) == 0
    assert session._frames == _decode(_pcm(list(range(2000, 2000 + FRAME))))
    printed = [ln for ln in capsys.readouterr().out.splitlines() if PRE_ROLL_LOG_RE.search(ln)]
    assert printed == [], f"pre-roll 為空時不得出現該行（實得 {printed}）"
    assert [r for r in caplog.records if PRE_ROLL_LOG_RE.search(r.getMessage())] == []
