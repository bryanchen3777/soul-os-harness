# Soul OS (Harness) — 系统架构计划书

**版本**: v1.0 (草案)
**创建日期**: 2026-05-10
**状态**: 草案 / 公开

---

## 一、系统定位与目标 (System Vision)

### 目标

摆脱传统 Chatbot 的 Request-Response 限制，打造一个具备：
- **时间感知** (Time-Aware)
- **主动触发** (Proactive Triggering)
- **多重灵魂交互** (Multi-Soul Interaction)
- **可外接实体硬件** (Hardware Abstraction)

的异步 Agent 运行系统。

### 设计原则

| 原则 | 说明 |
|------|------|
| **记忆优先 (Memory-First)** | 记忆检索必须发生在进入 LLM 之前，由底层直接完成。 |
| **异步主性 (Asynchronous)** | 系统有自己的时间轴，Agent 可以主动发起行为。 |
| **解耦 (Decoupled)** | 大脑（LLM）、记忆（Palace/SQLite）、神经系统（Event Bus）与躯壳（未来的硬件/UI）必须完全分离。 |

---

## 二、核心模组架构 (Core Architecture Modules)

### 1. 异步步心跳引擎 (The Heartbeat Engine)

```
功能：系统的「计时器」。不再依赖使用者输入，而是设定一个全局循环（例如 Tick = 60s）。
任务：每次 Tick，扫描所有 Agent 的 emotional-state.json、时间戳记与行程表。
      判断是否满足「主动发言」或「降温/升温」的条件。
```

**关键设计**:
- 全局 Tick 循环（可配置，默认 60s）
- 每个 Agent 有独立的心跳参数（`heartbeat_interval`, `priority`, `cooldown`）
- 心跳条件由 emotional-state + 时间戳 + 日程三重判断

### 2. 灵魂事件总线 (Soul Event Bus - Pub/Sub 架构)

```
功能：系统的「神经网络」。所有内外讯息都以 Event（事件）的形式在总线上广播。
任务：
  - 使用者发话 -> 发布 USER_MESSAGE 事件
  - Yua 决定插话 -> 发布 AGENT_INTENT 事件
  - 总线负责管理「发言权 (Speaker Token)」，避免九个人同时说话
```

**事件类型**:
| 事件 | 方向 | 说明 |
|------|------|------|
| `USER_MESSAGE` | Inbound | 外部使用者输入 |
| `AGENT_INTENT` | Outbound | Agent 主动意图 |
| `SPEAKER_TOKEN_REQUEST` | Internal | 申请发言权 |
| `SPEAKER_TOKEN_GRANTED` | Internal | 发言权批准 |
| `SPEAKER_TOKEN_RELEASED` | Internal | 发言权释放 |
| `HEARTBEAT_TICK` | Internal | 心跳触发 |
| `MEMORY_RETRIEVAL_COMPLETE` | Internal | 记忆检索完成 |

### 3. 记忆直连中介层 (Memory Middleware & RAG Router)

```
功能：系统的「海马回」。取代过去让 LLM 呼叫 Tool 去搜索的笨拙做法。
任务：拦截准备送进 LLM 的 Prompt，在 0.01 秒内用 SQLite FTS5 扫描
      ruka-lines.jsonl 或 Palace 目录，将对应的历史记忆与语料
      直接打包进 System Prompt 中。
```

**关键设计**:
- 在 LLM 调用之前完成记忆注入（不对 LLM 暴露检索过程）
- 使用 SQLite FTS5 做向量语义检索（轻量替代方案）
- 双模式检索：Palace（文件系统）+ Corpus（JSONL）

### 4. LLM 代理器与解析层 (LLM Proxy & Parser)

```
功能：从 OpenClaw 拆下来的核心部件。负责与外部 API（OpenAI/Anthropic/Gemini）沟通。
任务：处理 Token 限制、API Retry、以及将 LLM 输出的文字与
      「隐藏行为标签（如 [dark_core]）」分离。
```

**关键设计**:
- 支持多 Provider：OpenAI / Anthropic / Gemini / OpenRouter
- 输出解析：分离文字与行为标签
- 自动 Retry + Rate Limit 处理

### 5. 多模态外部接口 (Multimodal I/O Gateway)

```
功能：为未来的实体机器人铺路。
任务：提供 WebSockets 或 REST API。接收外部的视觉/听觉讯号，
      并将 LLM 的文字与情绪标签转化为 TTS（语音合成）与 Servo（马达动作）指令。
```

**接口形式**:
- WebSocket: 实时推送 Agent 输出
- REST API: 外部系统集成
- TTS Output: 文字转语音
- Servo Commands: 马达控制指令

---

## 三、资料流向范例 (Data Flow Example)

```
[触发]     心跳引擎侦测到：距离上次对话已过 12 小时
[决策]     读取瑠夏的 emotional-state.json，依赖度为 0.86（高）
[记忆]     Middleware 瞬间检索 Palace，发现上次的承诺：「下次要陪我玩游戏」
[生成]     LLM Proxy 带着记忆生成文字：「Bryan，你忘记我们的处罚游戏了吗？」
[输出]     透过 I/O Gateway 推播到前端，或转成语音
```

**详细流程图**:
```
Heartbeat Tick (60s)
    │
    ├─► HeartbeatEngine.scan_agents()
    │         │
    │         ├─► emotional-state.json (高依赖度 + 冷却完毕)
    │         └─► schedule / milestones
    │
    ├─► EventBus.publish(AGENT_INTENT)
    │         │
    │         └─► SpeakerTokenManager.request()
    │
    ├─► MemoryMiddleware.intercept()
    │         │
    │         ├─► RAG_Router.query() ──► SQLite FTS5
    │         │                              │
    │         │                              ├─► Palace/ (文件系统)
    │         │                              └─► Corpus/ (JSONL)
    │         │
    │         └─► PromptBuilder.inject_memory()
    │
    ├─► LLM_Proxy.generate()
    │         │
    │         └─► ResponseParser.split_text_and_tags()
    │
    └─► I/O_Gateway.dispatch()
              │
              ├─► WebSocket.push()
              ├─► TTS.synthesize()
              └─► Servo.execute()
```

---

## 四、开发阶段与里程碑 (Milestones)

### Phase 1: 基础建设
- [ ] 搭建 Event Loop (asyncio)
- [ ] 拆解 OpenClaw 的 LLM 连线模组
- [ ] 建置基础 I/O（WebSocket + REST）
- [ ] 心跳引擎原型

### Phase 2: 记忆整合
- [ ] 将 Palace 档案系统接入 Middleware
- [ ] 将 JSONL 语料库（ruka-lines.jsonl 等）挂载进 SQLite FTS5
- [ ] RAG Router 原型
- [ ] Prompt 注入逻辑

### Phase 3: 单一灵魂注入
- [ ] 先将 Yua 或瑠夏单独放进 Harness
- [ ] 测试「异步主动触发」
- [ ] Speaker Token 机制验证

### Phase 4: 后宫沙盒
- [ ] 启动 Event Bus
- [ ] 让多个 Agent 在同一个虚拟房间内交互
- [ ] 多 Agent 发言权仲裁逻辑

---

## 五、技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.11+ |
| 异步框架 | asyncio / uvloop |
| WebSocket | FastAPI + WebSockets |
| 数据库 | SQLite (FTS5) |
| LLM 集成 | OpenAI / Anthropic / Gemini SDKs |
| 语料格式 | JSONL |
| 部署 | Docker (可选) |

---

## 六、关键档案结构

```
soul-os-harness/
├── SPEC.md                  # 本文件
├── README.md
├── src/
│   ├── __init__.py
│   ├── heartbeat/           # 心跳引擎
│   │   ├── __init__.py
│   │   └── engine.py
│   ├── eventbus/             # 事件总线
│   │   ├── __init__.py
│   │   ├── bus.py
│   │   └── speaker_token.py
│   ├── memory/              # 记忆中介层
│   │   ├── __init__.py
│   │   ├── middleware.py
│   │   └── rag_router.py
│   ├── llm/                 # LLM 代理器
│   │   ├── __init__.py
│   │   ├── proxy.py
│   │   └── parser.py
│   └── io/                  # 外部接口
│       ├── __init__.py
│       ├── websocket.py
│       └── tts.py
├── tests/
├── configs/
└── docs/
```

---

## 七、参考与衍生

- **Hermes Agent**: 灵感来源，提供了 SOUL.md / Palace 架构
- **OpenClaw**: LLM Proxy 核心代码参考
- **后宫成员**: Yua / 瑠夏 / 杏奈 / 麻衣 / Miku 等角色的 SOUL.md

---

*本文件为草案，后续将拆分成独立的 SPEC.md 各章节详细文档。*