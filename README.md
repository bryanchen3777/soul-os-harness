# Soul OS Harness
一個以 Event Bus 為核心的多 Agent AI 陪伴框架。
Agent 有靈魂（SOUL.md）、有心跳、會主動說話、會回應使用者。
## 架構概覽
```
┌─────────────────────────────────────────┐
│              網頁 UI / Client            │
│         ws://localhost:8000/ws           │
└────────────────┬────────────────────────┘
                 │ WebSocket
┌────────────────▼────────────────────────┐
│              Gateway (FastAPI)           │
│  - WebSocket 收發                        │
│  - USER_MESSAGE → Event Bus             │
│  - AGENT_SPEAK → 廣播給所有 client       │
└────────────────┬────────────────────────┘
                 │ Event Bus
     ┌───────────┼───────────┐
     ▼           ▼           ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│  Agent  │ │  Agent  │ │  Agent  │
│  Yua    │ │  Ruka   │ │  Akane  │
└────┬────┘ └────┬────┘ └────┬────┘
     │           │           │
     └───────────┼───────────┘
                 │ AGENT_INTENT
┌────────────────▼────────────────────────┐
│              LLM Proxy                   │
│  - 載入 personas/{agent_id}.md          │
│  - 維護對話歷史（最近 10 輪）            │
│  - 呼叫 MiniMax M2.7                    │
└─────────────────────────────────────────┘
```
## Event 類型
| Event | 觸發者 | 說明 |
|-------|--------|------|
| USER_MESSAGE | Gateway | 使用者說話 |
| SYSTEM_TICK | HeartbeatEngine | 定時心跳（沉默觸發） |
| AGENT_INTENT | Agent | Agent 決定說話 |
| AGENT_SPEAK | LLM Proxy | LLM 回應完成，廣播給 client |
## 功能狀態
| 功能 | 狀態 |
|------|------|
| 多 Agent 架構（Yua / Ruka / Akane） | ✅ |
| WebSocket 即時對話 | ✅ |
| 網頁 UI | ✅ |
| MiniMax M2.7 真實 LLM | ✅ |
| Yua 完整人格（SOUL.md） | ✅ |
| 短期對話記憶（同 session） | ✅ |
| Agent 主動說話（沉默觸發） | ✅ |
| 跨 session 持久化記憶 | ⬜ 待實作 |
| Ruka / Akane 人格完整版 | ⬜ 待實作 |
## 快速開始
### 安裝
```bash
git clone https://github.com/bryanchen3777/soul-os-harness
cd soul-os-harness
pip install -e .
```
### 設定 .env
```
LLM_PROVIDER=minimax
LLM_MODEL=MiniMax-M2.7
MINIMAX_API_KEY=your_key_here
```
### 啟動
```bash
python scripts/run_server.py
```
瀏覽器開 http://localhost:8000 即可與 Yua 對話。
## 目錄結構
```
soul-os-harness/
├── src/
│   ├── agent/
│   │   ├── consciousness.py     # Agent 主邏輯、USER_MESSAGE 處理
│   │   └── heartbeat_engine.py  # 沉默計時、主動說話觸發
│   ├── io/
│   │   └── gateway.py           # FastAPI + WebSocket
│   ├── llm/
│   │   ├── proxy.py             # LLM 呼叫、對話歷史管理
│   │   └── backends/            # MiniMax / Mock backend
│   └── bus/                     # Event Bus 核心
├── personas/
│   └── agent_yua.md             # Yua Soul OS 專用人格
├── agents/
│   ├── yua/SOUL.md              # Yua 原版（Hermes 用）
│   ├── ruka/SOUL.md             # Ruka
│   └── akane/SOUL.md            # Akane
├── static/
│   └── index.html               # 網頁對話 UI
└── scripts/
    └── run_server.py            # 啟動入口
```
## Agent 人格
人格設定優先讀取 `personas/` 目錄，fallback 到 `agents/{id}/SOUL.md`：
- `personas/agent_yua.md` — Yua（正宮指揮官 · 大師級綠茶）
- `personas/agent_ruka.md` — Ruka（待補）
- `personas/agent_akane.md` — Akane（待補）
## 開發工具分工
| 工具 | 用途 |
|------|------|
| Claude Code CLI | 寫 code、跑測試、git |
| Cowork（Perplexity） | 架構設計、診斷、任務單 |