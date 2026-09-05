"""
akane_live.py — 黑川茜即時語音伴侶客戶端主入口（VC-1 模組 5）。

終端狀態機指示（IDLE → LISTENING → PROCESSING → SPEAKING）與主調度循環：

    監聽 → ASR → AsrRefiner 淨化（雜音 DROP）→ 喚醒詞閘門
         → AkaneVoiceBrain 回應 → ClauseSplitter 邊生邊播 → FishTTSStreamer 播放
    Bryan 開口（speaking 期間）→ VADListener barge-in → streamer.interrupt()

構件全部可注入/懶載入：import 本模組不需要任何重型依賴（離線可測）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# 相對/絕對導入相容（支援 python -m 與直接執行兩種方式）
try:  # pragma: no cover - 導入路徑相容
    from .asr_refiner import AsrRefiner
    from .akane_voice_brain import AkaneVoiceBrain
    from .fish_tts_streamer import FishTTSStreamer
    from .vad_listener import VADListener
except ImportError:  # pragma: no cover
    from asr_refiner import AsrRefiner
    from akane_voice_brain import AkaneVoiceBrain
    from fish_tts_streamer import FishTTSStreamer
    from vad_listener import VADListener


def load_config(path: Optional[str] = None) -> dict:
    """載入客戶端 config.json（預設為模組旁 config.json）。"""
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
# 主應用：終端狀態機 + 主調度循環
# ─────────────────────────────────────────────────────────────

class VoiceCompanionApp:
    STATE_IDLE = "IDLE"
    STATE_LISTENING = "LISTENING"
    STATE_PROCESSING = "PROCESSING"
    STATE_SPEAKING = "SPEAKING"

    _STATE_ICONS = {
        STATE_IDLE: "●",
        STATE_LISTENING: "● 聆聽中…",
        STATE_PROCESSING: "▶ 理解中…",
        STATE_SPEAKING: "▶ 茜說話中…",
    }

    def __init__(self, config, refiner=None, brain=None, streamer=None, listener=None):
        self.config = config
        self.refiner = refiner
        self.brain = brain
        self.streamer = streamer
        self.listener = listener
        self.state = self.STATE_IDLE
        self._lock = threading.Lock()
        self._last_interaction = 0.0

    @classmethod
    def from_config(cls, config: Optional[dict] = None) -> "VoiceCompanionApp":
        """依 config.json 組裝全套構件（含 Persona 檔載入與回呼佈線）。"""
        cfg = config or load_config()
        refiner = AsrRefiner.from_config(cfg)
        brain = AkaneVoiceBrain(
            config=cfg,
            persona_file=str(REPO_ROOT / "personas" / "agent_akane.md"),
        )
        streamer = FishTTSStreamer.from_config(cfg)
        app = cls(cfg, refiner=refiner, brain=brain, streamer=streamer, listener=None)
        listener = VADListener(
            config=cfg,
            on_transcript=app._handle_transcript,
            on_barge_in=app._handle_barge_in,
            playing_check=lambda: streamer.is_playing,
        )
        app.listener = listener
        return app

    # ── 主調度 ──

    def _handle_transcript(self, raw_text: str) -> None:
        """監聽斷句 → 淨化 → 喚醒閘門 → 茜回應 → 邊生邊播。"""
        self._set_state(self.STATE_PROCESSING)
        try:
            clean = self.refiner.refine_speech_text(raw_text)
            if not clean:
                self._log("DROP · 雜音熔斷（不打擾茜）")
                return

            dialogue = self.config.get("dialogue") or {}
            ephemeral = bool(dialogue.get("ephemeral_mode", False))
            if not ephemeral and not self._in_continuous_window() and not self._has_wake_word(clean):
                self._log(f"未喚醒 · ignore：{clean}")
                return

            self._log(f"ASR → {clean}")
            reply = self.brain.respond(clean)
            self._speak_reply(reply)
        except Exception as exc:  # 主循環不可死
            self._log(f"ERROR · {exc}")
        finally:
            self._set_state(self.STATE_LISTENING)

    def _handle_barge_in(self) -> None:
        """Bryan 重新開口 → 立即打斷茜的語音輸出。"""
        self.streamer.interrupt()
        self._log("⏸ barge-in · 茜已收聲")
        self._set_state(self.STATE_LISTENING)

    def _speak_reply(self, reply: str) -> None:
        self._set_state(self.STATE_SPEAKING)
        for clause in self.brain.splitter.split_stream([reply]):
            self.streamer.speak(clause)
        self._last_interaction = time.time()
        self._log(f"茜 → {reply}")

    # ── 喚醒閘門 ──

    def _has_wake_word(self, text: str) -> bool:
        words = [w.lower() for w in (self.config.get("dialogue") or {}).get("wake_words", []) if w]
        low = text.lower()
        return any(w in low for w in words)

    def _in_continuous_window(self) -> bool:
        timeout = float((self.config.get("dialogue") or {}).get("continuous_timeout_sec", 30.0))
        return bool(self._last_interaction) and (time.time() - self._last_interaction) < timeout

    # ── 狀態指示 ──

    def run(self) -> None:
        """主循環：啟動監聽與播放，由回呼驅動狀態，Ctrl-C 優雅退出。"""
        self._log("黑川茜即時語音伴侶已啟動（VC-1）— Ctrl-C 結束")
        self.listener.start()
        self.streamer.start()
        self._set_state(self.STATE_LISTENING)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        self._log("關閉中…")
        if self.listener is not None:
            self.listener.stop()
        if self.streamer is not None:
            self.streamer.stop()
        self._set_state(self.STATE_IDLE)
        self._log("已關閉。")

    # ── 內部 ──

    def _set_state(self, state: str) -> None:
        with self._lock:
            self.state = state
            self._print_state()

    def _print_state(self) -> None:
        icon = self._STATE_ICONS.get(self.state, self.state)
        sys.stdout.write(f"\r  {icon}   ")
        sys.stdout.flush()

    def _log(self, message: str) -> None:
        print(f"\n[{time.strftime('%H:%M:%S')}] {message}")


def main(argv: Optional[list] = None) -> int:
    """CLI 入口：python -m clients.voice_companion.akane_live [--config path]"""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = None
    if "--config" in args:
        i = args.index("--config")
        if i + 1 < len(args):
            config_path = args[i + 1]
    app = VoiceCompanionApp.from_config(load_config(config_path) if config_path else None)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())