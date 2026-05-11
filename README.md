# Soul OS (Harness)

> 异构 Agent 运行系统 — Memory-First, Asynchronous, Event-Driven Agent Framework

[![](https://img.shields.io/badge/status-draft-orange.svg)]() [![](https://img.shields.io/badge/python-3.11+-blue.svg)]() [![](https://img.shields.io/badge/license-MIT-green.svg)]()

## 系统定位

Soul OS (Harness) 是一个**异步 Agent 运行框架**，设计用于突破传统 Chatbot 的 Request-Response 限制。系统具备时间感知、主動触发、多重灵魂交互能力，可外接实体硬件。

## 系统架构图

![Soul OS 系统架构图](docs/soul-os-architecture.png)

## 设计原则

| 原则 | 说明 |
|------|------|
| **Memory-First** | 记忆检索发生在 LLM 之前，由底层直接完成 |
| **Asynchronous** | 系统有自己的时间轴，Agent 可主动发起行为 |
| **Decoupled** | 大脑（LLM）、记忆（Palace）、神经系统（Event Bus）完全分离 |

## 核心模组

| 模组 | 功能 |
|------|------|
| **Heartbeat Engine** | 全局 Tick 循环，扫描 Agent 状态决定是否主动触发 |
| **Event Bus** | Pub/Sub 架构，管理 Speaker Token 避免多人同时发言 |
| **Memory Middleware** | 在 LLM 调用前完成 RAG 检索，直接注入记忆 |
| **LLM Proxy** | 多 Provider 支持，输出文字与行为标签分离 |
| **I/O Gateway** | WebSocket / REST，支援 TTS 与 Servo 指令 |

## 开发流程与里程碑

![Soul OS 开发流程图与里程碑](docs/soul-os-milestones.png)

### Phase 1: 基础建设
- Event Loop (asyncio)
- LLM Proxy + Parser
- 基础 I/O（WebSocket + REST）
- 心跳引擎原型

### Phase 2: 记忆诞生
- Palace 档案系统接入 Middleware
- JSONL 语料库接入 SQLite FTS5
- RAG Router 原型
- Prompt 注入逻辑

### Phase 3: 第一个灵魂
- 单 Agent 主動觸發測試
- 真正「會自己說話」的 Agent
- Speaker Token 机制验证

### Phase 4: 多灵魂世界
- Event Bus 启动
- 多 Agent 同一空间交互
- 多 Agent 发言权仲裁逻辑

## 参考

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — 灵感来源
- [SoulDistillery](https://github.com/bryanchen3777/SoulDistillery) — SOUL.md 角色设定库

## 许可证

MIT License