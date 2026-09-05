"""
clients/voice_companion — 黑川茜（Akane）即時語音伴侶客戶端（VC-1）。

模組：
- asr_refiner.py         ASR 語意淨化層（同音錯字 / 贅詞 / 雜音熔斷）
- akane_voice_brain.py   黑川茜語音專用大腦（Layer 3 Persona + 0 Markdown 守門 + 分句器）
- fish_tts_streamer.py   Fish Audio TTS 串流播放器 + Barge-in 打斷
- vad_listener.py        sounddevice 常駐採集 + VAD 靜音斷句 + Barge-in 偵測
- akane_live.py          客戶端主入口（終端狀態機 + 主調度循環）

設計約束：硬體/網路依賴（sounddevice / requests / whisper / LLM client）一律
懶載入 + 可注入，保證在本專案 venv 內離線可測（0 安裝重型依賴）。
"""

__version__ = "0.1.0"