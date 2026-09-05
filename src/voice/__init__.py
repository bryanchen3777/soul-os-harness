"""src/voice — Soul OS 语音模块（MS-3 Voice Interaction）。

MS-3 在既有 src/voice（build_system_prompt / fish_tts / tts_service）基础上
**additive** 新增三个输入侧组件（设计文档 docs/MS-3-VOICE-INTERACTION-CONTRACT.md §6）：

  - gate.py          : VoiceGate 纯函数 —— 三路分流矩阵 + 唤醒门控 + 判定阶梯 + 防洪。
  - input_router.py  : VoiceInputRouter —— 语音 USER_MESSAGE 发布构造（契约对齐既有
                       gateway/router 通道）+ VOICE_OWNER_IDS 身份白名单。
  - audio_service.py : VoiceSessionService —— 会话监听窗口 + VAD 段合并（utterance
                       assembly）+ 冷却 / TTS echo 抑制 / stt:sha256 重复抑制。

frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers /
SAGE 写入逻辑 / EventType 枚举 / gateway / router / consciousness / proxy）0 触碰；
语音升级全部发生在本包，无旁路注入（唯一路径 = USER_MESSAGE → consciousness
`_fire_intent(reason="user_message")` → LLMProxy 既有 builder）。
"""