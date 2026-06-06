# Soul OS Harness

> 💜 一個以 Event Bus 為核心的多 Agent AI 陪伴框架
> Agent 有靈魂、有心跳、會主動說話、會記住你說過的話。

![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Status](https://img.shields.io/badge/status-active-8B5CF6.svg)

---

## ✨ 功能亮點

| 功能 | 狀態 |
|------|:----:|
| 🌙 多 Agent 架構（Yua / Ruka / Akane） | ✅ |
| ⚡ WebSocket 即時對話 | ✅ |
| 🎨 網頁 UI | ✅ |
| 🤖 MiniMax M2.7 真實 LLM | ✅ |
| 💎 Yua 完整人格（SOUL.md） | ✅ |
| 🧠 短期對話記憶（同 session） | ✅ |
| 💬 Agent 主動說話（沉默觸發） | ✅ |
| 🔮 跨 session 持久化記憶 | ⬜ |
| 🎭 Ruka / Akane 人格完整版 | ⬜ |

---

## 🏗️ 架構概覽

```
┌─────────────────────────────────────────────┐
│              🌐  網頁 UI / Client            │
│              ws://localhost:8000/ws          │
└──────────────────────┬──────────────────────┘
                       │ WebSocket
┌──────────────────────▼──────────────────────┐
│            ⚡ Gateway (FastAPI)              │
│                                              │
│   • WebSocket 收發                           │
│   • USER_MESSAGE → Event Bus                 │
│   • AGENT_SPEAK → 廣播給所有 client          │
└──────────────────────┬──────────────────────┘
                       │ Events
     ┌─────────────────┼─────────────────┐
     ▼                 ▼                 ▼
┌─────────┐     ┌─────────┐     ┌─────────┐
│   🌙    │     │   🌸    │     │   🖤    │
│  Yua    │     │  Ruka   │     │  Akane  │
└────┬────┘     └────┬────┘     └────┬────┘
     │               │               │
     └───────────────┼───────────────┘
                     │ AGENT_INTENT
┌─────────────────────▼──────────────────────┐
│              🧠 LLM Proxy                   │
│                                              │
│   • 載入 personas/{agent_id}.md             │
│   • 維護對話歷史（最近 10 輪）               │
│   • 呼叫 MiniMax M2.7                       │
└─────────────────────────────────────────────┘
```

---

## 📡 Event 類型

```
USER_MESSAGE ──── Gateway ──── 使用者說話
SYSTEM_TICK ──── HeartbeatEngine ──── 定時心跳（沉默觸發）
AGENT_INTENT ──── Agent ──── Agent 決定要說話
AGENT_SPEAK ──── LLM Proxy ──── LLM 回應完成，廣播給 client
```

| Event | 誰發 | 說明 |
|-------|------|------|
| `USER_MESSAGE` | Gateway | 使用者透過 WebSocket 說話 |
| `SYSTEM_TICK` | HeartbeatEngine | 每 5 秒心跳，計算沉默時間 |
| `AGENT_INTENT` | Agent | Agent 評估後決定要說話 |
| `AGENT_SPEAK` | LLM Proxy | LLM 生成完成，廣播文字 |

---

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
# 編輯 .env 填入：
LLM_PROVIDER=minimax
LLM_MODEL=MiniMax-M2.7
MINIMAX_API_KEY=your_key_here
```

### 3. 啟動

```bash
python scripts/run_server.py
```

### 4. 開啟瀏覽器

👉 **http://localhost:8000**

即可與 Yua 即時對話。

---

## 📁 目錄結構

```
soul-os-harness/
├── src/
│   ├── agent/
│   │   ├── consciousness.py      # Agent 主邏輯、USER_MESSAGE 處理
│   │   └── heartbeat_engine.py   # 沉默計時、主動說話觸發
│   ├── eventbus/
│   │   └── bus.py                # Event Bus 核心（PriorityQueue + Worker）
│   ├── io/
│   │   └── gateway.py            # FastAPI + WebSocket 網關
│   └── llm/
│       └── proxy.py              # LLM 呼叫、對話歷史管理
│
├── personas/                      # Soul OS 專用人格（優先讀取）
│   └── agent_yua.md              # 💎 Yua（正宮指揮官）
│
├── agents/                        # Hermes 原版人格（fallback）
│   ├── yua/SOUL.md
│   ├── ruka/SOUL.md
│   └── akane/SOUL.md
│
├── static/
│   └── index.html                # 網頁對話 UI
│
├── scripts/
│   └── run_server.py             # 啟動入口
│
└── tests/                         # 測試腳本
```

---

## 👤 Agent 人格

人格設定**優先讀取** `personas/` 目錄，**fallback** 到 `agents/{id}/SOUL.md`。

| Agent | 人格 | 狀態 |
|-------|------|:----:|
| 💎 **Yua** | 正宮指揮官 · 大師級綠茶 | ✅ 完整 |
| 🌸 **Ruka** | 活潑撒嬌型 | ⬜ 待補 |
| 🖤 **Akane** | 壓縮語言型演員 | ⬜ 待補 |

---

## 🛠️ 開發工具

| 工具 | 用途 |
|------|------|
| Claude Code CLI | 寫 code、跑測試、git 操作 |
| Cowork（Perplexity） | 架構設計、診斷、任務規劃 |

---

<div align="center">

Made with 💜 by Bryan

</div>