"""
tests/clients/test_vc_vad_timing.py — VC-VAD-TIMING-1 驗收：Auto-VAD 開麥錨點重錨 + 反回音護欄。

與 tests/clients/test_voice_companion.py 同風格：對 web_ui.HTML_PAGE 內嵌 JS 做**靜態斷言**
（不在 Python 中真跑瀏覽器）；伺服器診斷 log 追加則對 web_server.py 原始碼做靜態斷言。

- Test A（核心）：Worklet 緩衝排空開麥驗證 —— available > 0 期間 autoMuted 必為 true；
  實際排空（available == 0）+ 400ms Tail Buffer 到期後才翻 false；舊「IDLE + 固定 1.8s」
  錨點（autoHoldFrames / AUTO_HOLD_MS）已完全移除。
- Test B：真實打斷通道保留驗證 —— barge-in（0.04 / 200ms）分支維持原樣且仍 armed，
  打斷後可立即解除 autoMuted（0 倒退），flushPlayback 記錄調用源頭。
- Test C：約束設置驗證 —— getUserMedia 含 echoCancellation / noiseSuppression /
  autoGainControl（皆 exact，未降級 ideal）+ Mic constraints 實測 log。
- Test D：伺服器診斷 —— [TTS] round log 補 reply 原文（截斷 40 字）。

執行：.venv\\Scripts\\python.exe -m pytest tests/clients/test_vc_vad_timing.py -v
全離線：不呼叫瀏覽器 / 麥克風 / 音效卡。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.web_ui import HTML_PAGE  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Test A：Worklet 緩衝排空開麥驗證（VC-VAD-TIMING-1 D4 核心）
# ─────────────────────────────────────────────────────────────

class TestAUnmuteAnchoredOnActualDrain:
    def test_old_idle_1800ms_anchor_removed(self):
        """舊錨點（伺服器 IDLE + 固定 1.8s holdoff）必須完全消失"""
        assert "autoHoldFrames" not in HTML_PAGE
        assert "AUTO_HOLD_MS" not in HTML_PAGE

    def test_auto_muted_gated_on_drain_state(self):
        """autoMuted 綁定 playbackDrained（播放緩衝未排空期間必為 true，嚴禁 1.8s 後即開麥）"""
        assert 'state === "SPEAKING" || !playbackDrained' in HTML_PAGE

    def test_new_audio_keeps_mic_muted(self):
        """queuePlaybackSamples：任何新音訊進來 → playbackDrained=false + 取消 Tail Timer（保持靜音）"""
        assert "playbackDrained = false;" in HTML_PAGE
        assert "cancelTailTimer();" in HTML_PAGE

    def test_worklet_posts_drained_when_available_reaches_zero(self):
        """Worklet 在緩衝區實際排空（available == 0）時通知主執行緒"""
        assert "hadAudio && this.available === 0" in HTML_PAGE
        assert "this.port.postMessage({ type: 'drained' })" in HTML_PAGE

    def test_tail_buffer_within_300_500ms(self):
        """Tail Buffer 常數必須落在 300–500ms 規範窗口內"""
        m = re.search(r"TAIL_BUFFER_MS = (\d+)", HTML_PAGE)
        assert m, "TAIL_BUFFER_MS 常數必須存在"
        assert 300 <= int(m.group(1)) <= 500, f"TAIL_BUFFER_MS={m.group(1)} 超出 300–500ms"

    def test_unmute_only_after_tail_timer_expiry(self):
        """正式開麥（playbackDrained=true）只能發生在 Tail Timer 到期後"""
        assert "function onPlaybackDrained()" in HTML_PAGE
        assert "tailTimer = setTimeout(function ()" in HTML_PAGE
        assert "playbackDrained = true;" in HTML_PAGE

    def test_fallback_path_also_detects_drain(self):
        """ScriptProcessor fallback 路徑必須有同等排空偵測（不可留下第二條漏時鐘路徑）"""
        assert "hadAudio && fallbackAvail === 0" in HTML_PAGE
        assert "onPlaybackDrained();" in HTML_PAGE


# ─────────────────────────────────────────────────────────────
# Test B：真實打斷通道保留驗證（barge-in 0 倒退）
# ─────────────────────────────────────────────────────────────

class TestBBargeInPreserved:
    def test_barge_threshold_untouched(self):
        """Barge-in 能量門檻原封不動（0.04 / 200ms）"""
        assert "BARGE_AUTO_THRESHOLD = 0.04" in HTML_PAGE
        assert "BARGE_AUTO_MS = 200" in HTML_PAGE

    def test_barge_flow_still_armed(self):
        """SPEAKING 期間 barge-in 分支仍 armed：積分判定 → interrupt → LISTENING → sendPttStart"""
        assert "speakEnergyMs >= BARGE_AUTO_MS" in HTML_PAGE
        assert 'send({ type: "interrupt" })' in HTML_PAGE
        assert 'setState("LISTENING")' in HTML_PAGE
        assert "sendPttStart();" in HTML_PAGE

    def test_barge_unblocks_drain_gate(self):
        """打斷路徑（barging）立即解除 autoMuted：barge 後不落入 tail 窗口二次靜音（插話能力 0 倒退）"""
        assert "cancelTailTimer();" in HTML_PAGE
        assert "playbackDrained = true;" in HTML_PAGE
        assert "barging = false" in HTML_PAGE
        assert "barging = true" in HTML_PAGE

    def test_flush_origin_logged(self):
        """flushPlayback 記錄調用源頭，供追蹤是否由真實打斷引起"""
        assert "[Playback] flushPlayback triggered by " in HTML_PAGE
        assert 'flushPlayback("autoBargeIn")' in HTML_PAGE
        assert 'flushPlayback("manualBargeIn")' in HTML_PAGE
        assert 'flushPlayback("sendPttStart")' in HTML_PAGE


# ─────────────────────────────────────────────────────────────
# Test C：麥克風約束設置驗證（VC-VAD-TIMING-1 D1）
# ─────────────────────────────────────────────────────────────

class TestCMicConstraints:
    def test_getusermedia_constraints_present(self):
        """getUserMedia 同時要求 echoCancellation / noiseSuppression / autoGainControl"""
        assert "echoCancellation: true" in HTML_PAGE
        assert "noiseSuppression: true" in HTML_PAGE
        assert "autoGainControl: true" in HTML_PAGE

    def test_constraints_not_downgraded_to_ideal(self):
        """spec exact 約束不得改成 ideal（會靜默降級）"""
        assert "echoCancellation: {" not in HTML_PAGE
        assert "autoGainControl: {" not in HTML_PAGE

    def test_mic_settings_probe_logged(self):
        """連線成功後以 getSettings() 印出裝置實際生效的處理"""
        assert "stream.getAudioTracks()[0]" in HTML_PAGE
        assert "getSettings" in HTML_PAGE
        assert "[VC Audio] Mic constraints active:" in HTML_PAGE


# ─────────────────────────────────────────────────────────────
# Test D：伺服器診斷 log 補齊（web_server.py 僅新增 log，0 行為變更）
# ─────────────────────────────────────────────────────────────

class TestDServerDiagnostics:
    def test_tts_round_logs_reply_preview(self):
        """[TTS] round log 補上 reply 原文（截斷 40 字），補齊 ASR/TTS 比對證據缺口"""
        src = (REPO_ROOT / "clients/voice_companion/web_server.py").read_text(encoding="utf-8")
        assert "reply_text=%s" in src
        assert "reply[:40]" in src
        assert "[TTS] round mode=%s input_chars=%d reply_chars=%d chunks=%d bytes=%d " in src