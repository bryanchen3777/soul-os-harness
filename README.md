# Soul OS Harness

> 💜 一個以 Event Bus 為核心的多 Agent AI 陪伴框架  
> Agent 有靈魂、有心跳、有情緒、會主動說話、會記住你說過的話。




***

## ✨ 功能現況

| 功能 | 狀態 |
|------|------|
| 多 Agent 架構（Yua / Ruka / Akane） | ✅ |
| WebSocket 即時對話 | ✅ |
| 網頁 UI | ✅ |
| MiniMax M2.7 真實 LLM | ✅ |
| 完整人格 SOUL（14-tier 結構） | ✅ |
| 跨 session 持久化記憶（SQLite） | ✅ |
| Agent 主動說話（沉默觸發 + Connection Guard） | ✅ |
| Speaker Token 仲裁（防多人搶話） | ✅ |
| 情緒系統（mood / intimacy，SQLite 持久化） | ✅ |
| 群聊模式（Yua / Ruka / Akane 同場） | ✅ |
| Memory Middleware（MemoryStore 注入） | ✅ |
| 時間感知（Chrono Context，deep_night 保護） | ✅ |
| TTS 語音輸出 | ⬜ |
| 向量記憶搜尋（Phase 4） | ⬜ |

***

## 🏗️ 架構概覽

```
┌──────────────────────────────────────────┐
│           🌐 網頁 UI / Client             │
│         ws://localhost:8000/ws            │
└─────────────────┬────────────────────────┘
                  │ WebSocket
┌─────────────────▼────────────────────────┐
│         ⚡ IOGateway (FastAPI)            │
│  USER_MESSAGE → Event Bus                │
│  AGENT_SPEAK  → 廣播所有 client          │
└──────┬──────────────────────┬────────────┘
       │ Events               │
┌──────▼──────┐      ┌────────▼───────────┐
│ HeartBeat   │      │   SoulEventBus     │
│ Engine      │      │  (PriorityQueue)   │
│ tick/60s    │      └────────┬───────────┘
└─────────────┘               │
                  ┌───────────┼───────────┐
                  ▼           ▼           ▼
            ┌─────────┐ ┌─────────┐ ┌─────────┐
            │  Yua    │ │  Ruka   │ │  Akane  │
            │ 💎      │ │ 🌸      │ │ 🖤      │
            └────┬────┘ └────┬────┘ └────┬────┘
                 └───────────┼───────────┘
                      AGENT_INTENT
                 ┌───────────▼───────────┐
                 │  MemoryMiddleware     │
                 │  注入相關記憶          │
                 └───────────┬───────────┘
                      AGENT_INTENT_ENRICHED
                 ┌───────────▼───────────┐
                 │  SpeakerTokenManager  │
                 │  仲裁發言權            │
                 └───────────┬───────────┘
                      SPEAKER_TOKEN_GRANTED
                 ┌───────────▼───────────┐
                 │      LLMProxy         │
                 │  載入 persona + mood  │
                 │  組裝 prompt → LLM    │
                 └───────────┬───────────┘
                      AGENT_SPEAK → broadcast
```

### 📡 Event 流

| Event | 誰發 | 說明 |
|-------|------|------|
| `USER_MESSAGE` | Gateway | 使用者透過 WebSocket 說話 |
| `SYSTEM_TICK` | HeartbeatEngine | 每 60 秒心跳，計算沉默時間 |
| `AGENT_INTENT` | AgentConsciousness | Agent 決定要說話 |
| `AGENT_INTENT_ENRICHED` | MemoryMiddleware | 注入記憶後轉發 |
| `SPEAKER_TOKEN_GRANTED` | SpeakerTokenManager | 授予發言權 |
| `AGENT_SPEAK` | LLMProxy | LLM 生成完成，廣播文字 |
| `SPEAKER_TOKEN_RELEASED` | SpeakerTokenManager | 發言完成，釋放 token |

***

## 🗺️ 開發路線圖

| Phase | 內容 | 狀態 |
|-------|------|------|
| Phase 1 | Event Bus + 基礎 Agent + WebSocket Gateway | ✅ |
| Phase 2 | SQLite 記憶持久化 + Memory Middleware | ✅ |
| Phase 2.5 | Speaker Token 仲裁 + 群聊模式 | ✅ |
| Phase 3 | 情緒系統（mood / intimacy，SQLite） | ✅ |
| Phase 4 | 向量記憶搜尋（semantic recall） | ⬜ |
| Phase 5 | TTS 語音輸出 | ⬜ |
| Phase 6 | 多裝置同步 / 行動端 | ⬜ |

***

## 👤 Agent 人格

人格設定優先讀取 `personas/`，fallback 到 `agents/{id}/SOUL.md`。

| Agent | 人格 | 情緒敏感度 | 主動觸發門檻 |
|-------|------|------------|----------------|
| 💎 Yua | 冷靜・輕諷・藏著鉤子 | 低（0.08） | 30-120 分鐘沉默 |
| 🌸 Ruka | 元氣・撒嬌・停不下來 | 高（0.12） | 15 分鐘沉默 |
| 🖤 Akane | 壓縮語言・高共感 | 最低（0.06） | 60 分鐘沉默 |

***

## 🚀 快速開始

### 1. 安裝

```bash
git clone https://github.com/bryanchen3777/soul-os-harness
cd soul-os-harness
pip install -e .
```

### 2. 設定 .env

```bash
cp .env.example .env
# 填入：
# LLM_PROVIDER=minimax
# LLM_MODEL=MiniMax-M2.7
# MINIMAX_API_KEY=your_key_here
```

### 3. 啟動

```bash
python scripts/run_server.py
```

### 4. 開啟瀏覽器

👉 http://localhost:8000

***

## 🛠️ Debug 工具

| Endpoint | 說明 |
|----------|------|
| `GET /health` | Server 狀態 + 連線數 |
| `POST /inject/tick?elapsed_mins=20&time_period=morning` | 手動觸發主動發訊 |
| `GET /debug/emotion/{agent_id}` | 查看 agent 目前情緒數值 |
| `GET /debug/broadcast` | 直接廣播測試訊息 |
| `GET /_admin/fast_forward?minutes=35` | 模擬時間快轉（dev only） |

***

## 📁 目錄結構

```
soul-os-harness/
├── src/
│   ├── agent/
│   │   ├── consciousness.py     # Agent 主邏輯
│   │   ├── emotion.py           # 情緒引擎（Phase 3）
│   │   └── agents/              # 各 agent 子類
│   ├── eventbus/
│   │   ├── bus.py               # Event Bus 核心
│   │   └── token_manager.py     # Speaker Token 仲裁
│   ├── heartbeat/
│   │   └── engine.py            # 心跳引擎
│   ├── io/
│   │   └── gateway.py           # FastAPI + WebSocket
│   ├── llm/
│   │   └── proxy.py             # LLM 呼叫 + prompt 組裝
│   └── memory/
│       ├── middleware.py        # Memory Middleware
│       └── store.py             # SQLite 記憶層
├── personas/                    # Soul OS 專用人格（優先讀取）
├── configs/                     # 系統設定
├── scripts/
│   └── run_server.py            # 啟動入口
├── tests/                       # 測試腳本
└── static/
    └── index.html               # 網頁對話 UI
```

***

## 🤝 工作流程

| 工具 | 角色 |
|------|------|
| MiniMax Code | 寫 code、跑測試、git 操作 |
| Perplexity（Cowork） | 架構設計、診斷、任務規劃、second opinion |

Made with 💜 by Bryan
