# MULTIMODAL-PERCEPTION-CONTRACT.md — MS-1 Multimodal Perception Contract（DESIGN, docs-only）

> **工单**：MS-1（DESIGN）
> **状态**：完成 —— **只设计，0 code**（唯一产出物为本设计文档；0 code / 0 config / 0 data 改动；未 commit、未 push，验收后由主大脑决定后续）
> **作者**：developer（专责 bot，工单派发）
> **日期**：2026-09
> **工作区**：`C:\Users\bbfcc\.local\bin\soul-os-harness`（`pwsh pwd` 确认）
> **前置采信**：MS-0 审计（`docs/MULTIMODAL-PERCEPTION-AUDIT.md`，已确认，直接采信）→ 本设计（MS-1）
> **性质**：**设计文档（非施工授权）**。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准；本档全部「提案/草案」均未落地，需主大脑验收 + 逐项批准后才进入 MS-2 实作。

---

## 0. 摘要（TL;DR）

Soul OS 补齐输入侧多模态感知（语音 STT / 相机视觉）。**v1 语义归属锁定（关键守门）**：语音输入一律作为 **Ambient Observation（环境感知）** 走 observe 路径回流 Context、统一交主决策层（Motive/Decision）判定，**严禁直通 `USER_MESSAGE` 绕过 Volition Gate**。

设计产出四块：

1. **9 项设计决策**（§1）：全部锁定，含 STT 语义归属、ASR 选型、采集策略、工具 schema、VALID_SOURCES、分类表扩展、薄 MCP 封装、permission、novelty_id。
2. **工具层三处 Additive 扩展**（§2）：`EXPLICIT_GROUP_MAP` / `EXPLICIT_PERMISSION_MAP` / `_OBSERVE_KEYWORDS` 的完整扩展清单——不扩展则多模态 MCP Server 会被 fail-closed 误拒（MS-0 §2.3 R4 已证实）。
3. **VALID_SOURCES 最小变更草案**（§3）：additive 加 `audio_input` / `camera_capture`，不破坏既有 5 个 source。**frozen contract 触点，标注需主大脑 + Owner 批准**（MS-0 §4.1 确认）。
4. **自研薄 MCP 封装边界**（§4）：audio-stream-mcp / camera-mcp 的 stdio 接口规范——独立进程、单次调用、5s 硬超时、无状态清理、fail-closed 降级。

**Frozen Contract 结论**：`CONTRACT CONFLICT = 0`（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 全部 0 触碰；唯一触点 VALID_SOURCES 以「需批准」标注，见 §3）。

---

## 1. 九项设计决策（Bryan 已拍板，锁死）

> 编号对齐 MS-0 §5 的 D1–D9 建议方向；本文档将其从「建议」落实为「已拍板设计值」。
> 每个决策给出：**决策值 / 理由 / 证据位置 / 落地阶段**。

### D1. STT 语义归属 — v1 锁 Ambient Observation（关键守门）✅ 与工单决策 1 一致

| 项 | 内容 |
|---|---|
| **决策值** | 语音输入（STT 转写结果）作为环境感知（Ambient Observation）回流 Context —— 走 observe 路径 → `WorldEvent` → 世界感知管线（validate → novelty → top-N → `world_context` 注入），统一交主决策层（Motive/Decision）判定。**严禁**转写文本改道 `USER_MESSAGE` 通道。 |
| **理由** | ① 环境声音（客厅、音乐、人声、电视）是**背景感知**，不是 Bryan 的对话指令；② `USER_MESSAGE` 通道（`gateway.py:835-867` / `router.py:792-858`）现为纯文字来源，若语音直通会绕过 Volition Gate、触发完整对话管线，且违反「Actuator 无 publish」硬规则（`actuator.py:20-21`）；③ 感知 ≠ 发言权：scheduler 发布端仍 `mark_rejected`（`scheduler.py:378`），多模态事件默认不进 `WORLD_QUALIFYING_TYPES`（`inner_life_adapter.py:121`）→ 不污染 InnerLife/SAGE。 |
| **证据** | MS-0 §3.3（方案 A/B 分叉）；`actuator.py:20-21`；`scheduler.py:378,417-420`；`inner_life_adapter.py:121` |
| **落地** | MS-2 接线（0 触碰 USER_MESSAGE 通道）。方案 B（语音 → USER_MESSAGE 交互输入）延后 MS-3+，需主大脑评估「工具执行器产生 USER_MESSAGE」合规性后再开新设计。 |

### D2. VALID_SOURCES — Additive 扩展（最小变更草案，需批准）✅ 与工单决策 2 一致

| 项 | 内容 |
|---|---|
| **决策值** | 原则同意 Additive 扩展。新增多模态感知源类型：`audio_input`（语音输入流）与 `camera_capture`（相机抓帧事件）。**最小变更草案见 §3**。 |
| **理由** | 不接受扩展则多模态事件默认落 `synthetic`（`actuator.py:342-349` `_source_for` 兜底），可用但丢失「这是耳朵/眼睛听到看到的」语义区分（MS-0 §3.1 已证）。 |
| **证据** | `perception.py:46`（`VALID_SOURCES = frozenset({weather, news, calendar, social, synthetic})`）；MS-0 §4.1（唯一 frozen 触点） |
| **落地** | 草案呈主大脑 + Owner 批准后，MS-2 改 `perception.py:46`（additive）。**未批准前维持 synthetic 兜底，不阻塞**。 |

### D3. 工具层三处 Additive 扩展（分类表）✅ 与工单决策 3 一致

| 项 | 内容 |
|---|---|
| **决策值** | `_OBSERVE_KEYWORDS`、`EXPLICIT_GROUP_MAP`、`EXPLICIT_PERMISSION_MAP` 三处 additive 扩展，完整清单见 **§2**。 |
| **理由** | MS-0 §2.3（R4）已证实：现分类表无 audio/camera/voice/stt 词 → audio-stream-mcp / camera-mcp 工具注册走语义兜底失败 → fail-closed 拒绝注册（`tool_registry.py:363-371`），observe_environment 组看不到多模态能力。 |
| **证据** | `tool_registry.py:98-111 / 115-128 / 134-137 / 166-179 / 182-190` |
| **落地** | MS-2 改 `src/soul/tool_registry.py`（TS-2 归属模块，0 触碰 frozen contract）。 |

### D4. 自研薄 MCP 封装边界 ✅ 与工单决策 4 一致

| 项 | 内容 |
|---|---|
| **决策值** | audio-stream-mcp 与 camera-mcp 为**独立 stdio MCP server 进程**（非 import 进主进程），最小依赖薄封装，维持**单次调用（single-shot）、5s 硬超时、无状态清理**三规范。接口设计见 **§4**。 |
| **理由** | ① 资源隔离：本地 ASR 模型（faster-whisper small ~500MB 内存 + 推理）不占主进程，防 R5 资源占用；② 对齐 TOOLING-MCP-CONTRACT（调用链 / 超时 / 降级已是生产闭环 TS-0→TS-2.1）；③ 工具 schema 描述文本必须含 audio/voice/camera 关键词以过归类（§2）。 |
| **证据** | `tool_registry.py:78`（5s 硬超时）、`tool_registry.py:320`（唯一注册入口）；TOOLING-MCP-CONTRACT §3.1 / §4.2；MS-0 R5 |
| **落地** | MS-2 实作（~200-400 行/个）。 |

### D5. 权限分级 ✅ 与工单决策 5 一致

| 项 | 内容 |
|---|---|
| **决策值** | `mic_listen` / `audio_transcribe` / `stt` → **auto_approved**（唯读感知类，与 weather/news 同语义）；`camera_capture` → **ask_required**（隐私：家庭环境可能捕捉私密画面，Bryan 控制）。 |
| **理由** | 麦克风「听」与相机「看」隐私等级不同：相机画面一刀切 auto_approved 风险高（MS-0 R1 P1）。可配置开关，默认 ask_required。 |
| **证据** | TS-2 §4.1.1 权限语义；MS-0 §2.2 表 + R1 |
| **落地** | MS-2 `EXPLICIT_PERMISSION_MAP`（§2.2）。 |

### D6. 不改 frozen contract ✅ 与工单决策 6 一致

| 项 | 内容 |
|---|---|
| **决策值** | Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑 一律 0 触碰；不加 trigger_type、不加 EventType、不加 handler。 |
| **理由** | 多模态走「Decision 批准 → Actuator 派发单次调用 → 结果回流 world_context」既有闭环，无新增触发路径（MS-0 §4.1 逐条审查 0 conflict）。 |
| **证据** | `stages.py`（4 stages pure function）；MS-0 §4.1 |
| **落地** | 全程（MS-2 也不碰）。 |

### D7. ASR 引擎选型 — v1 本地 faster-whisper small ✅（MS-0 D1 落实）

| 项 | 内容 |
|---|---|
| **决策值** | **v1 锁定本地 `faster-whisper`（CTranslate2，small 模型）**。离线、隐私好、CPU 可跑（模型 ~150MB 起，内存 ~500MB 上限接受）。 |
| **理由** | ① 家庭环境麦克风数据不出本机（与 camera ask_required 同隐私取向）；② 无 API 用量成本（**花钱事项默认不做**）；③ 中文能力够用（small 多语种）。 |
| **分支（需 Owner 拍板）** | 云端 STT API（Fish Audio / Azure / OpenAI）**零本地依赖但产生 API 用量费 = 花钱事项 → Owner (Bryan) 拍板**（全域 AGENTS.md 铁律）。SenseVoice（中文更强、时延更低）依赖较重（torch/onnxruntime），列为 v1.1 可替换候选。 |
| **落地** | MS-2（装依赖 = 花钱/环境变更 → 需主大脑审批流程）。 |

### D8. 采集策略 ✅（MS-0 D2/D3 落实）

| 项 | 内容 |
|---|---|
| **决策值（麦克风）** | `sounddevice`（PortAudio，跨平台 Win/macOS/Linux）。采样 = **静音门控 + 最大时长上限**：VAD 检测到语音才开始 / 静音自动截断；单次采样硬上限 **4s**（保留 1s 余量给收尾与返回，对齐 5s 硬超时，见 §4.3）。 |
| **决策值（相机）** | `opencv-python`（`cv2.VideoCapture`：Windows UVC / macOS AVFoundation / Linux V4L2 原生）。抓帧 = **按需单次（Motive 驱动）**，**不做常开流、不做定时流**。 |
| **理由** | ① 静音门控 + 时长上限 = 防感知洪泛（R2）第一道闸；② 相机常开流违反「单次行动原则」且资源/隐私双风险。 |
| **落地** | MS-2。 |

### D9. novelty_id 内容级哈希 ✅（MS-0 D7 落实）

| 项 | 内容 |
|---|---|
| **决策值** | 替换 `f"{tool.name}:{ts}"`（`actuator.py:335`，按时间戳去重 = 24h window 内失效）：<br>• **STT**：`novelty_id = "stt:" + SHA256(normalize(transcript))[:12]` —— 句级去重。normalize = 小写 + 去标点空白 + CJK 全角→半角。同一句话重复出现 → 同一 novelty_id → novelty 衰减生效。<br>• **Camera**：`novelty_id = "cam:" + scene_tag + ":" + utc_date` —— 场景语义桶（scene_tag 由 camera-mcp 返回的粗分类，如 `empty_room` / `person` / `dog`）；单帧像素 hash 会因光照噪点失真，故用语义桶 + 日桶。同一场景一天内多帧 → 同一桶 → 去重。<br>• **Fallback**：工具未提供内容特征时维持 `tool:ts`（不破坏既有路径）。 |
| **理由** | 语音一段话 / 相机多帧若按时间戳会产生大量不同 novelty_id 事件（R2 感知洪泛的主因）；内容级去重让 scoring 的 novelty 维度真正生效（`perception.py:473-475` `novelty = 1/max(1,count)`）。 |
| **落地** | MS-2（audio-stream-mcp / camera-mcp 返回内容特征 → `_to_world_event` 生成 novelty_id）。 |

**决策锁定确认**：D1–D9 与工单「锁死 9 项决策」一致，全部照做；本设计不再引入任何未拍板的新决策项。

---

## 2. 工具层三处 Additive 扩展（分类表扩展清单）

> 目标文件：`src/soul/tool_registry.py`（TS-2 归属模块）。
> **全部 additive**：只增不减，既有 12 个工具映射 0 改动，行为 100% 保留。

### 2.1 EXPLICIT_GROUP_MAP 追加（`tool_registry.py:98-111`）

```python
EXPLICIT_GROUP_MAP: Dict[str, str] = {
    # ...既有 12 项 0 改动...
    # MS-1 D3：多模态感知工具 → observe_environment（additive）
    "mic_listen":         CAPABILITY_GROUP_OBSERVE,
    "audio_transcribe":   CAPABILITY_GROUP_OBSERVE,
    "stt":                CAPABILITY_GROUP_OBSERVE,
    "camera_capture":     CAPABILITY_GROUP_OBSERVE,
    "camera_snapshot":    CAPABILITY_GROUP_OBSERVE,
    "image_capture":      CAPABILITY_GROUP_OBSERVE,
}
```

### 2.2 EXPLICIT_PERMISSION_MAP 追加（`tool_registry.py:115-128`）

```python
EXPLICIT_PERMISSION_MAP: Dict[str, str] = {
    # ...既有 12 项 0 改动...
    # MS-1 D5：mic/STT 唯读感知 → auto_approved；camera 隐私敏感 → ask_required（默认）
    "mic_listen":         PERM_AUTO_APPROVED,
    "audio_transcribe":   PERM_AUTO_APPROVED,
    "stt":                PERM_AUTO_APPROVED,
    "camera_capture":     PERM_ASK_REQUIRED,   # D5：隐私，家庭环境
    "camera_snapshot":    PERM_ASK_REQUIRED,
    "image_capture":      PERM_ASK_REQUIRED,
}
```

### 2.3 _OBSERVE_KEYWORDS 追加（`tool_registry.py:134-137`）

```python
_OBSERVE_KEYWORDS = (
    # ...既有 12 词 0 改动...
    # MS-1 D3：多模态关键词（description 语义兜底，大小写不敏感）
    "audio", "voice", "speech", "speak", "camera", "image", "vision", "stt",
    "麦克风", "麥克風", "语音", "語音", "声音", "聲音", "说话", "說話",
    "相机", "相機", "摄像头", "攝像頭", "画面", "畫面", "图像", "圖像",
    "listen", "transcri",
)
```

> 注：`"stt"` 与 `"listen"`、`"transcri"`（覆盖 transcribe/transcription）为 substring 匹配，避免 `audio_transcribe` 因描述用词变体漏网。优先级顺序不变（reflect > communicate > observe，`tool_registry.py:154-162`）。

### 2.4 归类行为验证（注册后期望）

| 工具 | 归类路径 | 能力组 | 权限 |
|---|---|---|---|
| `mic_listen` | ①显式表 | observe_environment | auto_approved |
| `audio_transcribe` / `stt` | ①显式表 | observe_environment | auto_approved |
| `camera_capture` / `camera_snapshot` / `image_capture` | ①显式表 | observe_environment | ask_required |
| 未来未入表的多模态工具（描述含 voice/语音/相机 等） | ②语义兜底 | observe_environment | ask_required（fail-closed） |

**不再有**：`classify_tool` 返回 None → 拒绝注册（R4 解除）。

### 2.5 世界感知分类表扩展（同属「分类表扩展」，专责感知侧）

> 目标文件：`src/world/perception.py`（TYPE_KEYWORDS / TYPE_BASELINE_RELEVANCE）。
> **注意**：此节与 §3 VALID_SOURCES 同属感知侧 additive，但 `TYPE_*` **不是 frozen contract**（MS-0 §4.1 未列入 15 contracts），无需批准，随 MS-2 落地。

```python
TYPE_KEYWORDS: Dict[str, List[str]] = {
    # ...既有 5 type 0 改动...
    "voice_transcript":  ["voice", "speech", "transcript", "语音", "語音", "说话", "說話", "转写", "轉寫"],
    "ambient_audio":     ["audio", "sound", "music", "环境", "環境", "声音", "聲音"],
    "camera_scene":      ["camera", "scene", "看到", "看见", "看見", "画面", "畫面"],
}

TYPE_BASELINE_RELEVANCE: Dict[str, float] = {
    # ...既有 5 type 0 改动...
    "voice_transcript": 0.30,   # 含 Bryan 相关语音的可能性高，但可能是环境人声 → 中基线
    "ambient_audio":    0.10,   # 环境噪声/音乐一般无感（对齐 weather_temp_change 同档）
    "camera_scene":     0.25,   # 相机看到的场景可能相关（对齐 user_going_outside 略低）
}
```

> 语义：语音感知一律落 `voice_transcript` / `ambient_audio` 两个细分 type（转写内容中含 Bryan/指令性关键词 → relevance 由 user context overlap 拉高），相机落 `camera_scene`。这些 type **不在** `WORLD_QUALIFYING_TYPES`（`inner_life_adapter.py:121`）→ 不写 InnerLifeEvent/SAGE = 正确防守行为，0 触碰 frozen。

---

## 3. VALID_SOURCES 最小变更草案（frozen contract 触点，需批准）

### 3.1 草案（diff 级最小变更）

**现状**（`src/world/perception.py:46`，frozen，M6.1-2 确认 15 contracts 之一）：

```python
VALID_SOURCES = frozenset({"weather", "news", "calendar", "social", "synthetic"})
```

**提案（additive，只增不减）**：

```python
VALID_SOURCES = frozenset({
    "weather", "news", "calendar", "social", "synthetic",   # 既有 5 source 0 变动
    "audio_input",     # MS-1 D2：语音输入流（STT 转写 → Ambient Observation）
    "camera_capture",  # MS-1 D2：相机抓帧事件
})
```

### 3.2 为什么 additive 不破坏既有 source

| 检查 | 结果 |
|---|---|
| 既有 5 source 语义 | 100% 保留（frozenset 只增元素，无元素改名/删除） |
| `validation.py:100` whitelist 检查 | `source not in VALID_SOURCES` 对既有 source 行为不变；新 source 自动合法 |
| `actuator.py:342-349` `_source_for` | 未知工具仍 fallback `synthetic`（向后兼容）；audio/camera 工具在 MS-2 认领新 source |
| `WorldContext.to_text` 渲染（`perception.py:270`） | `[source/type]` 纯字符串拼接，无需改 |
| 既有测试 | 需在 MS-2 回归确认**无测试断言「恰好 5 个 source」**；若存在则同步更新（additive 断言） |

### 3.3 命名与语义

- 命名对齐既有风格：全小写 + 下划线（weather/news/calendar/social/synthetic 同风格）。
- `audio_input`（非 `audio`）：强调「语音输入流」而非原型「声音」——STT 转写事件属人声输入；`camera_capture`（非 `vision`）：强调「相机抓帧事件」——视觉感知的触发形态。
- `source` 语义 = 管道标识（TOOLING-MCP-CONTRACT / `social/schema.py:19` 注释对齐）：`audio_input` = 麦克风输入管道，`camera_capture` = 相机抓帧管道。

### 3.4 批准状态 ⚠️

- **frozen contract 触点**：`VALID_SOURCES` 为 M6.1-2 确认的 15 contracts 之一 → **需主大脑 + Owner 批准**。
- **本草案非施工授权**：批准前 MS-2 不得改 `perception.py:46`；多模态事件以 `synthetic` + type 区分（`voice_transcript` / `camera_scene`）运行，语义可接受，0 阻塞。
- 若 Owner 拒绝扩展：维持 `synthetic` + type 双区分方案（MS-0 §4.1 已认可「可行但丢失语义区分」），不阻塞 MS-2 其余工作。

---

## 4. 自研薄 MCP 封装边界（audio-stream-mcp / camera-mcp）

### 4.1 进程与协议

| 项 | 设计 |
|---|---|
| 进程形态 | **独立 stdio MCP server 进程**（`node`/`python` 均可，走 ToolRegistry `register_mcp_server` 接入，`tool_registry.py:320`）。不 import 进主进程 → ASR 模型内存/推理与主进程隔离（R5）。 |
| 协议 | MCP stdio（JSON-RPC over stdin/stdout）。仅需实现：`initialize` / `tools/list` / `tools/call` / `shutdown`。不实现任何 streaming / SSE / 长连接能力。 |
| 依赖预算 | audio-stream-mcp：`sounddevice` + `soundfile` + `faster-whisper`（ctranslate2）；camera-mcp：`opencv-python`。均为 venv 新增依赖 → **安装 = 花钱/环境变更 → 主大脑审批流程（MS-2）**。 |

### 4.2 工具 schema（tools/list 契约）

**audio-stream-mcp**

```jsonc
// mic_listen — 麦克风采样（静音门控，≤4s）
{
  "name": "mic_listen",
  "description": "Listen to ambient audio via microphone. Capture voice input as ambient observation. 通过麦克风采集周围环境声音（语音感知）。",  // 含 listen/voice/ambient/麦克风 关键词（§2 归类）
  "inputSchema": {
    "type": "object",
    "properties": {
      "duration_seconds": {"type": "number", "minimum": 1, "maximum": 4, "description": "Max sampling duration (default 3)"}
    },
    "required": []
  }
}
// 返回：{ "wav_ref": "audio_20260901T000000Z.wav", "duration": 3.2, "has_speech": true, "peak_level": 0.42 }
// 静音检测失败（has_speech=false）→ 不产生转写，直接返回（防洪泛）

// audio_transcribe — 本地转写
{
  "name": "audio_transcribe",
  "description": "Transcribe recorded audio (wav_ref from mic_listen) to text using local ASR. 将录音转写为文字（本地语音识别 STT）。",  // 含 transcribe/stt/语音 关键词
  "inputSchema": {
    "type": "object",
    "properties": {
      "wav_ref": {"type": "string", "description": "wav_ref returned by mic_listen"}
    },
    "required": ["wav_ref"]
  }
}
// 返回：{ "text": "...", "language": "zh", "duration": 3.2 }
// 后续由 actuator 侧对 text 做 normalize → SHA256 → novelty_id（D9）
```

**camera-mcp**

```jsonc
// camera_capture — 单帧抓拍（按需，无常开流）
{
  "name": "camera_capture",
  "description": "Capture a single frame from the camera and describe the scene briefly. 相机单帧抓拍并返回场景摘要（视觉感知）。",  // 含 camera/vision/相机 关键词
  "inputSchema": {
    "type": "object",
    "properties": {
      "tag_hint": {"type": "string", "description": "Optional scene tag hint, e.g. room / door / desk"}
    },
    "required": []
  }
}
// 返回：{ "image_ref": "frame_20260901T000000Z.jpg", "scene_tag": "empty_room", "captured_at": "..." }
// scene_tag ∈ {empty_room, person, pet, activity, other}（粗分类，供语义桶 novelty_id）
// image_ref 仅供 trace 观察，不注入 prompt（避免隐私文字化）
```

### 4.3 运行规范（硬约束，对齐 TOOLING-MCP-CONTRACT §4.2）

| 规范 | 设计 |
|---|---|
| **单次调用（single-shot）** | 每个工具 = 一次调用一个结果；无流式、无长连接、无内部循环。`mic_listen` 与 `audio_transcribe` 是两次独立调用（各自 5s 预算），不合并成一次长调用。 |
| **5s 硬超时** | 客户端 `registry.call` 硬超时 5s（`tool_registry.py:78` 默认，可配置）；**服务端自限更严**：采样 ≤4s（留 1s 给收尾+返回）、转写 small 模型 4s 音频 CPU 推理 ~1-2s（预算内）；超时即放弃 → 降级兜底（R5）。 |
| **无状态清理** | MCP server 在**每次调用结束**（含异常路径）自行删除自己产生的临时文件（`wav_ref` / `image_ref` 指向 OS temp）；进程退出时兜底清理；主进程不持有任何 server 内部状态。 |
| **fail-closed 降级** | 工具异常/超时 → registry `_degrade`（空结果/预设缓存）→ 主循环不阻塞（`tool_registry.py:629-678`）。 |
| **不发声 / 不递归** | MCP server 无 EventBus / SpeakerToken / LLM 句柄（对齐 `tool_registry.py:33-34` 注册表隔离）；工具结果不产生新 Motive/Decision（SM-1 Q2 冻结）。 |

### 4.4 数据流（MS-2 接线后，0 触碰 frozen）

```
麦克风/相机（硬件）
  → audio-stream-mcp / camera-mcp（独立 stdio 进程，§4.1-4.3）
  → ToolRegistry.register_mcp_server（tool_registry.py:320，自动归类 observe，auto/ask 权限）
  → Motive 产出 → Decision 四元选 observe（SM-3/SM-4 既有环，0 改）
  → Actuator.execute_observe（actuator.py:162，单次调用）
      ├─ route() 路由 mic_listen/audio_transcribe 或 camera_capture（actuator.py:226-256）
      ├─ registry.call（5s 硬超时，fail-closed 降级）
      └─ _flowback → _to_world_event（actuator.py:269-340）
          ├─ source: audio_input / camera_capture（D2 批准后）或 synthetic 兜底
          ├─ type: voice_transcript / ambient_audio / camera_scene（§2.5）
          └─ novelty_id: 内容级 hash（D9）
              → WorldPerceptionState.add（ephemeral，24h window）
                  → WorldPerceptionMiddleware（validate → top-N=3 → AGENT_INTENT_PERCEIVED）
                      → world_context 注入 prompt（SI-3 Phase 2 已接线，0 改）
```

**Volition Gate 逐条核验**（多模态归属 observe 后天然满足，MS-0 §3.2 原样适用）：
- 0 自主递归 ✅（工具结果不产生新 Motive/Decision；Actuator 纯函数式单次调用）
- 无 publish AGENT_SPEAK ✅（MCP server 无 EventBus/SpeakerToken；scheduler 发布端仍 `mark_rejected`）
- 单次行动原则 ✅（observe/reflect 选择后不 publish，actuator 注入才执行）
- 结果不污染 InnerLife/SAGE ✅（`voice_transcript`/`camera_scene` 不在 `WORLD_QUALIFYING_TYPES`）
- 主心跳不阻塞 ✅（5s 硬超时 + fail-closed 降级）

---

## 5. Frozen Contract 审查（0 conflict，1 触点待批）

| Frozen Contract | 位置 | MS-1 触碰 | 结论 |
|---|---|---|---|
| Agency 4 stages | `src/agency/stages.py` | ❌ 0 触碰（不加 trigger_type / 不扩 stage） | 0 conflict |
| TriggerEnvelope | `src/agency/trigger.py` | ❌ 0 触碰 | 0 conflict |
| InnerLifeEvent | `src/inner_life/event.py` | ❌ 0 触碰（多模态 type 不进 `WORLD_QUALIFYING_TYPES`） | 0 conflict |
| 4 handlers | `src/agency/*_handler.py` | ❌ 0 触碰（不加 handler） | 0 conflict |
| SAGE 写入逻辑 | `src/memory/sage/writer.py` | ❌ 0 触碰 | 0 conflict |
| **VALID_SOURCES** | `src/world/perception.py:46` | ⚠️ **唯一触点**：§3 additive 草案 | **需主大脑 + Owner 批准**；批准前 synthetic 兜底运行 |
| EventType 枚举 / EventBus | `src/eventbus/` | ❌ 复用 `WORLD_EVENT`（0 改）；不加新 EventType | 0 conflict（有 SI-2.1 additive 先例，但 MS-1 不需要） |
| M5.9-2 WORLD_QUALIFYING_TYPES | `src/world/inner_life_adapter.py:121` | ❌ 0 触碰（新增 type 不在白名单 = 正确防守） | 0 conflict |
| scheduler 职责 | `src/soul/scheduler.py` | ❌ 0 触碰（TS-2.1 已接线） | 0 conflict |
| SM-2 Decision Prompt / Motive 5 字段 | `src/soul/decision.py` / `motive.py` | ❌ 0 触碰（能力组能力只进 Relevant context，SI-3 Phase 2 先例） | 0 conflict |

**结论：`CONTRACT CONFLICT = 0`（唯一 frozen 触点 VALID_SOURCES 以「需批准草案」呈现，见 §3.4，不构成冲突，构成待批提案）。**

---

## 6. Out of Scope（本工单不做）

- ❌ **实作**（MS-2 才做：audio-stream-mcp / camera-mcp、tool_registry 三处 additive、感知分类表、测试）。
- ❌ 不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑）。
- ❌ **不 commit、不 push**（等验收后由主大脑决定，符合全域 AGENTS.md 分工：commit 是执行者 MS-2 的事，本工单明确禁止）。
- ❌ 不装 venv 依赖 / 不下载 ASR 模型（花钱/环境变更，主大脑审批）。
- ❌ 不触碰 USER_MESSAGE 输入通道（D1 方案 B 延后 MS-3+）。
- ❌ 不触碰 Fish TTS / 输出侧任何代码。

---

## 7. 验收对照

| 验收项 | 结果 |
|---|---|
| 设计文档产出 `docs/MULTIMODAL-PERCEPTION-CONTRACT.md` | ✅ 本文档 |
| 覆盖 9 项决策（ASR 选型 / 采集 / 工具 schema / 语义归属 / novelty_id / 权限） | ✅ §1（D1-D9 决策表，含 D7 ASR / D8 采集 / D5 工具 schema 与权限 / D1 语义归属 / D9 novelty_id） |
| 分类表扩展 | ✅ §2（三处 additive 完整清单 + 感知侧 TYPE_* 扩展 + 归类验证表） |
| VALID_SOURCES 最小变更草案 | ✅ §3（diff 级草案 + additive 论证 + 命名 + 批准状态） |
| 自研薄 MCP 封装边界 | ✅ §4（stdio 接口 / 工具 schema / 单次调用 / 5s 硬超时 / 无状态清理 / fail-closed） |
| 明确「只设计，0 code」 | ✅ 本文档（唯一产出物；0 code / 0 config / 0 data；git status 无 tracked 变更，见 §8） |
| 不碰 frozen contract（除 VALID_SOURCES additive 提案，标注需批准） | ✅ §3.4 / §5（CONTRACT CONFLICT = 0） |
| CONTRACT CONFLICT 声明 | ✅ §5（0 conflict，1 触点待批） |

---

## 8. 证据索引（带路径/行号）

| 证据 | 位置 |
|---|---|
| VALID_SOURCES（frozen） | `src/world/perception.py:46` |
| TYPE_KEYWORDS / TYPE_BASELINE_RELEVANCE（感知分类表） | `src/world/perception.py:336-359` |
| novelty scoring（1/count 衰减） | `src/world/perception.py:473-475` |
| whitelist 校验 | `src/world/validation.py:100` |
| EXPLICIT_GROUP_MAP / EXPLICIT_PERMISSION_MAP / _OBSERVE_KEYWORDS | `src/soul/tool_registry.py:98-111 / 115-128 / 134-137` |
| 归类三级规则 / 权限分级 / 5s 硬超时 / 唯一注册入口 / 降级 | `src/soul/tool_registry.py:166-179 / 182-190 / 78 / 320 / 629-678` |
| _to_world_event source 映射（未知→synthetic）/ novelty_id=tool:ts | `src/soul/actuator.py:309-340 / 342-349 / 335` |
| Actuator 0 自主递归 / 无 publish | `src/soul/actuator.py:15-24 / 20-21` |
| scheduler mark_rejected + actuator 注入 | `src/soul/scheduler.py:378 / 417-420 / 444-461` |
| Agency 4 stages | `src/agency/stages.py`（Stage1-4 pure function） |
| USER_MESSAGE 纯文字通道（不得直通） | `src/io/gateway.py:835-867` / `src/io/channels/router.py:792-858` |
| WORLD_QUALIFYING_TYPES（M5.9-2） | `src/world/inner_life_adapter.py:121` |
| 工具层契约（单次调用 / 5s 硬超时 / 降级） | `docs/TOOLING-MCP-CONTRACT.md` §3.1 / §4.2 |
| MS-0 审计（9 项决策建议 / R1-R6 / 唯一触点） | `docs/MULTIMODAL-PERCEPTION-AUDIT.md` §2.3 / §3.3 / §4 / §5 |