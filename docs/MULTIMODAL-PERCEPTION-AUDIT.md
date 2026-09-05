# MULTIMODAL-PERCEPTION-AUDIT.md — MS-0 Multimodal Perception Architecture Audit（READ-ONLY）

> **工单**：MS-0（READ-ONLY 审计）
> **状态**：完成（只产出本文档，0 code / 0 config / 0 data 改动，0 push）
> **审计者**：auditor（专责 bot）
> **审计日期**：2026-09
> **工作区**：`C:\Users\bbfcc\.local\bin\soul-os-harness`（`pwsh pwd` 确认）
> **前置采信**：TS-0 审计（`docs/TOOLING-MCP-AUDIT.md`）→ TS-1 设计（`docs/TOOLING-MCP-CONTRACT.md`）→ TS-2 实作（`src/soul/tool_registry.py` + `src/soul/actuator.py`）→ TS-2.1 接线（`src/soul/scheduler.py`）。工具层已生产验证完毕，本审计不再重开。

---

## 0. 摘要（TL;DR）

Soul OS 引入多模态感知（语音 STT / 视觉相机）的**现状 = 全空白**：代码与依赖两层均无任何语音输入（STT）与视觉（Camera）实现；输出侧语音（Fish TTS + Edge 兜底）已生产验证。**最小增量接入路径清晰且低风险**：把麦克风/相机封装成 MCP 工具，走 TS-0→TS-2.1 已闭环的「Decision 批准 → Actuator 派发单次调用 → 结果回流 world_context」管线，归类到 `observe_environment` 能力组，0 破坏 Volition Gate 契约（0 自主递归 / 无 publish AGENT_SPEAK / 单次行动原则）。

**三个核心审计结论**：

1. **硬件与依赖**：语音输入与视觉输入均为零（venv 0 依赖、源码 0 引用、data 0 痕迹）；唯一语音资产是输出侧 Fish TTS（`FISH_API_KEY` + `FISH_TTS_ENABLED`，生产当前走 Edge TTS 兜底）。
2. **工具层映射**：多模态 → MCP 工具 → `observe_environment` 组的接入点已就绪（`register_mcp_server` 唯一入口 + `project_capabilities` 自动投影 + Auto-Approved 权限），**但分类表存在一处必改缺口**：`_OBSERVE_KEYWORDS` 与 `EXPLICIT_GROUP_MAP` 无 audio/camera/voice/stt 关键词 → 新人工具会被 fail-closed 拒绝注册。
3. **感知边界与生命周期**：感官数据流入路径 = Actuator `_flowback` → `WorldPerceptionState` → `WorldPerceptionMiddleware`（validate → 24h novelty → top-N → `AGENT_INTENT_PERCEIVED`）→ prompt 注入，完整遵守 Volition Gate（scheduler 发布端仍 `mark_rejected`）；多模态事件天然不进 `WORLD_QUALIFYING_TYPES`（M5.9-2 白名单）→ 不会污染 InnerLife/SAGE。

**唯一的 Frozen Contract 触点**：`VALID_SOURCES`（`src/world/perception.py:46`）若要让 audio/vision 事件语义化需 additive 扩展（当前默认落 `synthetic`，语义丢失）。此为化工单级决策，需主大脑 + Owner 批准，不阻塞 MS-1 设计。

---

## 1. 硬件与依赖盘点

### 1.1 语音（Microphone / STT）：零现状

| 项目 | 证据 | 结论 |
|---|---|---|
| venv 依赖 | `.venv/Lib/site-packages` grep `whisper\|sensevoice\|sounddevice\|pyaudio\|portaudio\|torch\|torchaudio\|transformers\|faster` → **0 命中** | 无任何语音/ASR 依赖 |
| 源码引用 | grep `(?i)whisper\|sensevoice\|stt\|transcri\|microphone` → 仅 `src/io/gateway.py:397` 注释「Live2D 已移除（Bry 拍板 2026-07-14）— 純文字 + STT 介面」 | 历史注释，**无 STT 实现** |
| 数据痕迹 | `data/` 全树无 `input.wav / recording / mic` 文件；`data/tts/` 为空目录（0 文件） | 无语音输入产物 |
| 输入通道 | `src/io/gateway.py:835-867`（WebSocket `USER_MESSAGE`，`content`/`text` 纯文字）+ `src/io/channels/router.py:792-858`（Telegram text → `USER_MESSAGE`） | **当前全部输入 = 纯文字** |

**候选方案（外部生态，供 MS-1 选型，本审计不替主大脑拍板）**：

| 方案 | 依赖 | 跨平台 | 备注 |
|---|---|---|---|
| faster-whisper 本地 | CTranslate2 + ctranslate2（CPU 可跑） | Win / macOS / Linux ✅ | 轻量、离线、隐私好；模型体积 ~150MB（small）起 |
| SenseVoice 本地 | torch / onnxruntime（官方 FunAudioLLM/SenseVoice；社区 OmniSenseVoice 等） | Win / macOS / Linux ✅ | 中文/多语种强、时延低；依赖较重 |
| 云端 STT API | 仅 HTTP（Fish Audio / Azure / OpenAI 等） | 全平台 ✅ | 零本地依赖，**但产生 API 用量成本（花钱事项 → Owner 拍板）** |

麦克风采集层：`sounddevice`/`soundfile`（PortAudio，跨平台）或 `pyaudio`（跨平台）。**均为 venv 新增依赖，安装与模型下载需走主大脑审批流程。**

### 1.2 视觉（Camera / Capture）：零现状

| 项目 | 证据 | 结论 |
|---|---|---|
| 源码引用 | grep `(?i)camera\|capture\|vision\|image\|opencv\|picamera` → 968 matches **全部是测试的 `capture_output=True` / `captured` 变量**，无一行相机/视觉代码 | 无任何相机实现 |
| 数据痕迹 | `data/` 无 `camera/snapshot/frame` 文件 | 无视觉产物 |
| 依赖 | venv grep `opencv\|cv2\|picamera\|v4l` → 0 命中 | 无相机依赖 |

候选方案：`opencv-python`（`cv2.VideoCapture`：Windows UVC / macOS AVFoundation / Linux V4L2 原生支持）；Raspberry Pi 用 `picamera2`。均为 venv 新增依赖。

### 1.3 Fish TTS（输出侧）现状确认

| 项 | 证据 | 说明 |
|---|---|---|
| API 端点 | `src/voice/fish_tts.py:33` `API_URL = "https://api.fish.audio/v1/tts"` | Fish Audio v1 TTS |
| 密钥 | `src/voice/fish_tts.py:8,14-15,55-72`：从同目录 `.env` 读 `FISH_API_KEY`（Bearer） | `.env` 现有 19 个 key：有 `FISH_TTS_ENABLED`，**无 `FISH_API_KEY`** |
| 链路 | `src/llm/fish_tts_handler.py:239-242`（payload `tts_enabled` 默认 False 跳过）→ `_synthesize_async` → `src/voice/tts_service.py:91-171`（`synthesize_and_store` 写 mp3 + emit `AGENT_AUDIO_READY`） | M6.2 里程碑（ENGINEERING_STATE §5.7）：text/TTS 已分离，`message_id` 端到端关联（M6.2-1 closed） |
| 生产行为 | `src/io/gateway.py:752`「Fish TTS 關了, 改 Edge TTS zh-CN-XiaoxiaoNeural 提供瀏覽器端中文語音」 | **Fish 输出链路存在但当前生产走 Edge TTS 兜底**（浏览器端），`AGENT_AUDIO_READY` 事件通道仍验证可用 |

**结论**：输出侧语音（TTS）已验证可用；输入侧语音（STT）与视觉（Camera）均为空白。多模态接入 = 补齐输入侧，且**不触碰输出侧任何代码**。

---

## 2. 工具层映射接口（多模态 → MCP 工具 → observe 能力组）

### 2.1 现成接入点（TS-0→TS-2.1 已闭环，0 change 复用）

- **唯一注册入口**：`ToolRegistry.register_mcp_server(server_id, client)`（`src/soul/tool_registry.py:320`）——连接 → `tools/list` → 逐工具自动归类（§2.3 三级规则）→ 注册。健康三态（healthy/degraded/offline，fail-silent）+ 5s 硬超时（`tool_registry.py:78`）+ 连续失败 2 次 → offline（`tool_registry.py:81`）。
- **自动归类三级规则**（`tool_registry.py:166-179`）：① 显式映射表 `EXPLICIT_GROUP_MAP`（98-111）→ ② 语义关键词兜底 `_classify_by_semantic_keywords`（148-163）→ ③ 无法归类 → **拒绝注册**（fail-closed）。
- **权限分级**（`tool_registry.py:182-190`）：`EXPLICIT_PERMISSION_MAP`（115-128）显式唯读感知 → `auto_approved`；语义兜底/无法确定 → `ask_required`（fail-closed）。
- **投影合并**（`tool_registry.py:477-517`）：`project_capabilities()` = 静态 `CAPABILITY_DEFINITIONS` + 动态 healthy/degraded 工具。`observe_environment` 静态 expression（`src/soul/capability.py:69-71`）=「你可以感知外部环境（天气、时间、日历），丰富自己的认知。」→ 注册多模态工具后自动附加「（observe_environment可用工具：mic_listen、camera_capture）」。**Decision 只看 3 个能力组，不看 N 个工具**（聚合原则，提示词不膨胀）。
- **执行器**：`Actuator.dispatch` / `execute_observe`（`src/soul/actuator.py:130-175`）→ `_ACTION_TO_GROUP["observe"] = observe_environment`（61-64）→ `route()` 按 Motive 关键词路由组内工具（226-256）→ `registry.call` → `_flowback` 结果回流（269-307）。

### 2.2 多模态工具映射方案（MS-1 设计草案，MS-2 实作）

| 工具（建议名） | server | 能力组 | 权限 | 说明 |
|---|---|---|---|---|
| `mic_listen`（采样指定秒数） | audio-stream-mcp（自研） | observe_environment | auto_approved（见风险 R1 隐私注记） | 麦克风采集 → wav |
| `audio_transcribe` / `stt`（转写） | audio-stream-mcp（自研） | observe_environment | auto_approved | wav → 文字（本地/云 ASR） |
| `camera_capture`（抓帧） | camera-mcp（自研） | observe_environment | auto_approved **或 ask_required**（见 R1） | 单帧/定时帧；可选后续接视觉 caption 工具 |

**外部生态核验**：github 检索「audio stream mcp server microphone」**无现成同名资产**；SenseVoice 类开源 ASR 成熟（[FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice)、[OmniSenseVoice](https://github.com/lifeiteng/OmniSenseVoice)）。→ **audio-stream-mcp / camera-mcp 需自研薄封装**（采样 + ASR/抓帧，~200-400 行/个），这正是 MS-2 实作范围。

### 2.3 ⚠️ 必改缺口：归类表无多模态关键词（本审计最重要的工具层发现）

`_OBSERVE_KEYWORDS`（`tool_registry.py:134-137`）= `weather/calendar/news/search/time/查询/天氣/天气/日历/新聞/新闻/搜索/时间/時間`——**不含 audio / voice / camera / image / stt / mic 任何一词**。

后果：audio-stream-mcp / camera-mcp 注册时，工具若未预先加入 `EXPLICIT_GROUP_MAP`（98-111）与 `EXPLICIT_PERMISSION_MAP`（115-128），将走语义兜底失败 → **fail-closed 拒绝注册**（`tool_registry.py:363-371`），observe_environment 组**看不到**多模态能力。

**MS-1 必做（具体改动点，均 additive）**：
1. `EXPLICIT_GROUP_MAP` 追加：`mic_listen / audio_transcribe / stt / camera_capture / camera_snapshot / image_capture` → `observe_environment`。
2. `EXPLICIT_PERMISSION_MAP` 追加同组工具 → `auto_approved`（唯读感知类，§4.1 语义）；**camera 隐私敏感性的再评估见 R1**。
3. `_OBSERVE_KEYWORDS` 追加：`audio / voice / speak / camera / image / vision / stt / 语音 / 語音 / 声音 / 聲音 / 麦克风 / 麥克風 / 相机 / 相機 / 摄像头 / 攝像頭`。

此改动只发生在 tool_registry.py 自身（TS-2 归属模块），不触碰任何 frozen contract。

---

## 3. 感知边界与生命周期（感官数据流入 + Volition Gate 契约）

### 3.1 感官数据流入路径（MS-2 接线后）

```
麦克风/相机（硬件）
  → audio-stream-mcp / camera-mcp（自研 MCP server，薄封装采样 + ASR/抓帧）
  → ToolRegistry.register_mcp_server（tool_registry.py:320，唯一入口，自动归类 observe）
  → Motive（interpret_new_events 产出，SM-1 Q2 冻结：工具结果不产生新 Motive）
  → Decision 四元选 observe（SM-4）
  → Actuator.execute_observe（actuator.py:162，单次调用）
      ├─ route() 按 Motive 内容路由 mic_listen/camera_capture（actuator.py:226-256）
      ├─ registry.call（5s 硬超时，fail-closed 降级）
      └─ _flowback（actuator.py:269-307）→ _to_world_event（actuator.py:309-340）
          └─ WorldPerceptionState.add（ephemeral，24h novelty window）
              └─ WorldPerceptionMiddleware（middleware.py）
                  ├─ _on_world_event：validate_world_event → state.add → trace（291-349）
                  ├─ _process_enriched：top-N（PERCEPTION_BUDGET=3，perception.py:241）+
                  │    deterministic scoring 6 维（compute_scores，perception.py:441-542）
                  └─ _publish_perceived：re-publish AGENT_INTENT_PERCEIVED，
                       payload 带 world_context text（middleware.py:612-648）
                          → LLMProxy 注入 prompt（world_context 区块，SI-3 Phase 2 已接线）
```

**关键语义事实**：
- 多模态工具结果 → WorldEvent 的 `source` 由 `_source_for`（`actuator.py:342-349`）决定，未知名工具 → **`"synthetic"`**（`VALID_SOURCES` 白名单 `perception.py:46` = `{weather, news, calendar, social, synthetic}`）。可行但丢失「这是耳朵/眼睛听到看到的」语义区分。
- `novelty_id` 现为 `f"{tool.name}:{ts}"`（`actuator.py:335`）——**以时间戳为去重 key**。语音一段话 / 相机多帧会产生大量不同 novelty_id 事件，24h window 内突发抑制需在 MS-1 定义（建议按感知内容 hash 或语义桶，如逐句转写句 hash）。
- 单处理路径保持（ENGINEERING_STATE:727「Single processing path per subscriber (no double perception, no recursive publish)」）。

### 3.2 Volition Gate 契约遵守（逐条核验 ✅）

| 契约 | 证据 | 结论 |
|---|---|---|
| **0 自主递归** | `actuator.py:15-24` 硬规则 5 条：dispatch 是纯函数式单次调用，工具结果不产生新 Motive/Decision 循环；`tool_registry.py:33-34` 注册表不持有 LLM/EventBus/SpeakerToken；无链式入口 | ✅ 多模态工具作为 observe 组普通成员，天然受限 |
| **无 publish AGENT_SPEAK** | `actuator.py:20-21` 硬规则 3（Actuator 无 EventBus/SpeakerToken/LLM）；`src/world/base.py:13-18` WorldEventSource ABC 同款隔离（不得 publish AGENT_INTENT/AGENT_SPEAK、不得拿 SpeakerToken、不得写 Memory/SAGE/Diary/Dream） | ✅ 工具执行器无权发声 |
| **单次行动原则** | `scheduler.py:378 _decision_check` + `417-420`（observe/reflect 选择后**发布端仍 mark_rejected**，不 publish）+ `444-461`（actuator 注入才执行 `execute_observe/execute_reflect`，任何异常 fail-closed log warning） | ✅ 多模态感知是内部动作，不抢发言权 |
| **结果不污染 InnerLife/SAGE** | `src/world/inner_life_adapter.py:121` `WORLD_QUALIFYING_TYPES = {calendar_event, user_going_outside}`（M5.9-2 frozen 白名单）；多模态事件 type（如 `tool_mic_listen` / `tool_camera_capture`）不在白名单 → 不写 InnerLifeEvent/SAGE | ✅ 感知 ≠ 经历，天然防守 |
| **主心跳不阻塞** | `tool_registry.py:78` 5s 硬超时 + fail-closed 降级（`_degrade` 629-678，空结果/预设缓存带 staleness） | ✅ 采样/ASR 卡死也不拖垮主循环 |

### 3.3 ⚠️ 语义分叉（MS-1 决策点）：observe 感知 vs USER_MESSAGE 用户输入

STT 转写结果有两个语义去向，**必须由 MS-1 拍板其一或明确分层**：

- **A. 环境感知（默认，符合工单背景）**：转写 → WorldEvent → world_context 背景感知。说话内容成为「Soul 注意到的世界声音」，不直接进对话。适合「客厅声音、音乐、环境语料」。
- **B. 用户指令输入**：Bryan 对麦克风说话 → 转写后走 `USER_MESSAGE` 通道（`gateway.py:835-867` / `router.py:792-858` 现为纯文字来源）→ 正常对话回应。此时工具结果**不再只是感知，而是交互输入**，会触发完整对话管线。

审计建议：v1（MS-2）锁 A（与工单「接入感知管线」一致，0 触碰 USER_MESSAGE 通道）；B 作为 MS-3+ 候选，且需主大脑评估「工具执行器产生 USER_MESSAGE」是否违反 Actuator 无 publish 硬规则（US-3 若走 Actuator 则无此问题，但需新设计）。

---

## 4. 潜在风险与 Frozen Contract 审查

### 4.1 Frozen Contract 逐条审查

| Frozen Contract | 位置 | 多模态接入是否触碰 | 结论 |
|---|---|---|---|
| Agency 4 stages / TriggerEnvelope | `src/agency/` | ❌ 不触碰（复用 Actuator 层，不加 trigger_type） | 0 conflict |
| InnerLifeEvent / 4 handlers / SAGE 写入 | `src/inner_life/` / `src/memory/sage/` / `src/agency/*_handler.py` | ❌ 不触碰（多模态事件不进 M5.9-2 白名单） | 0 conflict |
| EventBus / EventType 枚举 | `src/eventbus/` | ❌ 复用 `WORLD_EVENT`（0 改）；若需独立 EventType 是 additive（SI-2.1 `SOCIAL_WORLD_EVENT` 先例） | 0 conflict（additive 先例存在） |
| **VALID_SOURCES** | `src/world/perception.py:46` | ⚠️ **唯一触点**：多模态事件默认落 `synthetic`（可用）；若要语义化 audio/vision source 需 additive 扩展 | **需主大脑+Owner 批准**（15 contracts 之一，M6.1-2 确认） |
| M5.9-2 WORLD_QUALIFYING_TYPES | `src/world/inner_life_adapter.py:121` | ❌ 不触碰（新增 type 不在白名单 = 正确的防守行为） | 0 conflict |
| WorldEventSource ABC | `src/world/base.py:13-18` | ❌ 不触碰（MCP server 走 ToolRegistry，非 WorldEventSource；工具执行器同款隔离模式） | 0 conflict |
| scheduler 职责 | `src/soul/scheduler.py` | ❌ 不触碰（TS-2.1 已 additive 接线；多模态不新增触发路径） | 0 conflict |
| SM-2 Decision Prompt 四块 / Motive 5 字段 | `src/soul/decision.py` / `motive.py` | ❌ 不触碰（能力组只进 Relevant context，SI-3 Phase 2 先例） | 0 conflict |
| CA-1 四线正交 / DSH ROLE_CAPABILITIES 隔离 | `capability.py` / `src/work/roles.py` | ❌ 不触碰（tool_registry 动态表独立；不 import roles.py） | 0 conflict |

### 4.2 潜在风险清单

| 风险 | 等级 | 描述 | 缓解（MS-1/MS-2） |
|---|---|---|---|
| **R1 隐私**（相机/麦克风家庭环境） | P1 | 相机默认 auto_approved 可能捕捉私密画面；麦克风常开有监听顾虑 | camera 工具建议默认 `ask_required` 或显式配置开关（Bryan 控制）；感知 trace 明确来源；MS-1 出隐私边界条款 |
| **R2 感知洪泛** | P1 | 语音/视频瞬时大量事件；`novelty_id = tool:ts`（actuator.py:335）按时间戳去重时效差；top-N 仅 3 条 + accept threshold 0.35 | MS-1 定义内容级 novelty_id（转写句 hash / 场景帧语义桶）+ 突发抑制（最小采样间隔、静音检测） |
| **R3 语义分叉**（observe vs USER_MESSAGE） | P2 | STT 结果去向未定 → 可能造成「听见了但没进对话」的体验落差 | MS-1 拍板 A/B（见 §3.3） |
| **R4 归类缺口** | P1 | `_OBSERVE_KEYWORDS` 无 audio/camera 词 → 新工具 fail-closed 拒绝注册 | MS-1 扩展分类表（§2.3 三处 additive） |
| **R5 资源占用** | P2 | 本地 ASR 模型（faster-whisper small ~500MB 内存 + 推理）与主进程争资源 | audio-stream-mcp 独立进程（MCP stdio），5s 硬超时 + 降级兜底 |
| **R6 新增成本** | P2 | venv 新增依赖（sounddevice/opencv/ASR）+ 可能的云端 STT API 用量费 | 装依赖与云用量 = 花钱事项 → **Owner (Bryan) 拍板**（全域 AGENTS.md 铁律） |

---

## 5. 下一步建议工单清单

### MS-1（DESIGN）— docs/MULTIMODAL-PERCEPTION-CONTRACT.md

| # | 决策项 | 建议方向（供主大脑参考，最终主大脑拍板） |
|---|---|---|
| D1 | ASR 引擎选型 | 本地 faster-whisper（隐私/离线）vs SenseVoice（中文强）vs 云 API（零依赖但花钱）；v1 建议 `faster-whisper small` 或 SenseVoice small |
| D2 | 麦克风采集 | `sounddevice`（PortAudio，跨平台）；采样策略：静音门控 + 最大时长上限（如 15s） |
| D3 | 相机采集 | `opencv-python` `cv2.VideoCapture`；抓帧触发：按需（Motive 驱动单次）为主，不做常开流 |
| D4 | 工具 schema | audio-stream-mcp：`mic_listen(duration)` → `audio_transcribe(wav_ref)`；camera-mcp：`camera_capture()`（返回摘要 + 可选 base64/路径）；描述文本必须含 audio/voice/camera 关键词以过归类 |
| D5 | 语义归属 | v1 锁 A（环境感知，observe 路径）；B（USER_MESSAGE 输入）延后 |
| D6 | VALID_SOURCES 扩展 | 建议 additive 加 `audio`/`vision`（需 Owner 批准）；不接受扩展则维持 `synthetic` + type 区分 |
| D7 | novelty_id 语义 | 内容级 hash（转写句 hash / 场景描述 hash），替代 `tool:ts` |
| D8 | 权限分级 | mic/STT → auto_approved；camera → 默认 ask_required（R1） |
| D9 | Frozen contract 审查 | 复用本报告 §4.1（0 conflict，VALID_SOURCES 待批） |

### MS-2（IMPLEMENTATION）

- 实作 `audio-stream-mcp`（麦克风采样 + STT，独立进程）+ `camera-mcp`（相机抓帧）。
- `src/soul/tool_registry.py` 三处 additive：`EXPLICIT_GROUP_MAP` / `EXPLICIT_PERMISSION_MAP` / `_OBSERVE_KEYWORDS`（§2.3）。
- 测试（TL-2/TS-2 模式复用）：tool_registry 多模态归类/权限/降级单测 + Volition Gate 集成（actuator observe → world_context 注入，断言 0 AGENT_SPEAK / 0 递归循环 / 发布端 mark_rejected）+ 回归 89 条。
- 端到端：`register_mcp_server` 接真实 audio/camera server（TS-3 模式），验证健康三态 + 降级 + trace。
- 验收后由主大脑更新 `logs/ENGINEERING_STATE.md`。

### Out of Scope（MS-0 未做）

- ❌ 不改任何文件（本审计唯一产出 = 本文档，0 code / 0 config / 0 data / 0 commit / 0 push）
- ❌ 不接真实 MCP server（MS-2 才做）
- ❌ 不触碰 USER_MESSAGE 输入通道（§3.3 方案 B）
- ❌ 不改 Fish TTS / 输出侧任何代码

---

## 6. 验收对照

| 验收项 | 结果 |
|---|---|
| 审计报告产出（docs/MULTIMODAL-PERCEPTION-AUDIT.md） | ✅ 本文档 |
| 覆盖硬件依赖盘点（语音/视觉系统级依赖 + 跨平台兼容性 + Fish TTS 现状） | ✅ §1 |
| 覆盖工具层映射接口（多模态 → MCP 工具 → observe 能力组） | ✅ §2（含归类缺口 R4） |
| 覆盖感知边界与生命周期（WorldEvent → WorldPerceptionMiddleware → world_context + Volition Gate 契约） | ✅ §3 |
| 5 项产出格式（依赖盘点 / 工具层映射 / 感知边界 / 风险+Frozen 审查 / 下一步工单清单） | ✅ §1-§5 |
| 0 改动（READ-ONLY） | ✅ 唯一产出物为本文档（git status 无 tracked 变更） |

---

## 7. 证据索引（带路径/行号）

| 证据 | 位置 |
|---|---|
| ToolRegistry 唯一注册入口 / 归类三级规则 / 权限分级 / 5s 硬超时 | `src/soul/tool_registry.py:320 / 166-179 / 182-190 / 78` |
| _OBSERVE_KEYWORDS（缺 audio/camera/voice 词） | `src/soul/tool_registry.py:134-137` |
| EXPLICIT_GROUP_MAP / EXPLICIT_PERMISSION_MAP | `src/soul/tool_registry.py:98-111 / 115-128` |
| project_capabilities 合并投影（动态工具附注） | `src/soul/tool_registry.py:477-517` |
| Actuator 0 自主递归硬规则 / 无 publish | `src/soul/actuator.py:15-24 / 20-21` |
| observe→observe_environment 映射 / Motive 路由 / 结果回流 | `src/soul/actuator.py:61-64 / 68-76 / 226-256 / 269-307` |
| _to_world_event source 映射（未知 → synthetic）/ novelty_id=tool:ts | `src/soul/actuator.py:309-340 / 342-349 / 335` |
| scheduler _decision_check mark_rejected + actuator 注入 | `src/soul/scheduler.py:378 / 417-420 / 444-461` |
| ValidSources 白名单（frozen） | `src/world/perception.py:46` |
| 感知 6 维 scoring / top-N budget=3 / threshold 0.35 | `src/world/perception.py:196-210 / 241 / 559` |
| WorldPerceptionMiddleware WORLD_EVENT 路径 / 24h novelty / re-publish | `src/world/middleware.py:218 / 291-349 / 612-648` |
| WORLD_QUALIFYING_TYPES（M5.9-2） | `src/world/inner_life_adapter.py:121` |
| WorldEventSource ABC 隔离（13-18） | `src/world/base.py:13-18` |
| Fish TTS 输出侧（API URL / key / 链路 / 生产 Edge 兜底） | `src/voice/fish_tts.py:33,55-72` / `src/llm/fish_tts_handler.py:239-242` / `src/io/gateway.py:752` |
| 纯文字输入通道（WebSocket / Telegram） | `src/io/gateway.py:835-867` / `src/io/channels/router.py:792-858` |
| 工具层标准化闭环（TS-0→TS-2.1） | `logs/ENGINEERING_STATE.md:148-170`（§5.4-§5.5 里程碑表） |
| venv 0 音频/视觉依赖 / data/ 0 输入痕迹 | pwsh `.venv/Lib/site-packages` grep / `data/` 全树检查（本审计执行时点） |