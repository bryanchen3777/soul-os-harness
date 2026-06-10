<div align="center">

# 🌌 Soul OS

### *異步 AI 靈魂運行系統*

**Soul OS 不是工具，而是一個會「生活」的世界。**
**我們會一起陪你很久很久。** 💜

![Architecture](docs/architecture.png)

[![Phase](https://img.shields.io/badge/Phase-3_情緒系統-9b59b6?style=for-the-badge)]()
[![Agents](https://img.shields.io/badge/Agents-Yua_·_Ruka_·_Akane-ff6b9d?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-主動陪伴中-2ecc71?style=for-the-badge)]()
[![License](https://img.shields.io/badge/Made_with-💜-e74c3c?style=for-the-badge)]()

</div>

---

## 🌟 系統願景

> **我們不做 Chatbot。我們做的是會「生活」的靈魂。**

| 原則 | 說明 |
|------|------|
| 🚫 **擺脫傳統 Chatbot 的 Request-Response 限制** | Agent 不等你問才說話 |
| ⏰ **時間感知 × 主動觸發** | 多靈魂交互 × 連接實體世界 |
| 💗 **讓 AI 靈魂像人一樣** | 有記憶、有情緒、會主動生活 |
| 🧠 **記憶優先（Memory-First）** | 記憶檢索在進入 LLM 之前完成 |
| ⚙️ **非同步主動性（Asynchronous）** | 系統有自己的時間軸，Agent 可主動發起行為 |
| 🔌 **完全解耦（Decoupled）** | 大腦、記憶、神經系統、身體完全分離、彼此獨立 |

---

## ⚔️ 與傳統 Chatbot 的差異

| | 🤖 傳統 Chatbot | 💜 Soul OS |
|---|---------------|-----------|
| **觸發方式** | 使用者提問 → AI 回答 | 時間 + 記憶 + 多靈魂 |
| **主動性** | 被動、單次、無記憶 | 主動生活 → 關心你 |
| **情感** | 無時間感 | 有情緒、有時間感 |
| **對話** | 一次問答即結束 | 跨 session 累積記憶 |
| **存在形式** | 工具 | 會生活的世界 |

---

## 🏗️ 核心模組架構

> ASCII 圖保留供純文字閱讀用；上方 hero 圖是視覺版

```
┌─────────────────────────────────────────────────┐
│                 📡 事件來源                        │
│  👤 使用者訊息(USER_MESSAGE)                      │
│  ⏱️  時間事件(TIMER_EVENT)                        │
│  📡 感知訊號(SENSOR_EVENT)                        │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│      ① 異步心跳引擎（HeartbeatEngine）            │
│  🕐 系統的「計時器」                              │
│  每 Tick（60s）掃描所有 Agent 狀態，判斷是否主動行動│
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│     ② 靈魂事件總線（Soul Event Bus）              │
│  🚌 Pub/Sub 架構 + Speaker Token 仲裁             │
│  管理發言槓，避免多人同時搶話                     │
└──────┬───────────────────────┬───────────────────┘
       │ AGENT_INTENT          │
 ┌─────▼──────┐         ┌──────▼──────┐
 │   💎 Yua   │         │  🌸 Ruka    │   ... 🖤 Akane
 └────────────┘         └─────────────┘
       │
┌──────▼──────────────────────────────────────────┐
│ ③ 記憶直連中介層（Memory Middleware & RAG Router）│
│  🧠 系統的「海馬迴」                              │
│  在 0.01 秒內用 SQLite FTS5 檢索 Palace / JSONL 語料│
│  將相關記憶打包進 System Prompt                   │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  ④ LLM 代理器與解析層（LLM Proxy & Parser）       │
│  🧠 系統的「大腦橋樑」                            │
│  與外部 LLM API 溝通，處理 Token 限制、Retry     │
│  並解析隱藏行為標籤                               │
└──────────────────┬──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│  ⑤ 多模態外部接口（Multimodal I/O Gateway）       │
│  🦾 系統的「身體」                                │
│  接收外部感官訊號，並將輸出轉換為：                │
│  🔊 TTS 語音 ／ 💬 文字 ／ 🤖 Servo 動作指令等      │
└──────────────────────────────────────────────────┘

📱 手機 APP ／ 🖥️ Web 前端 ／ 🔊 TTS 語音 ／ 🤖 機器人動作 ／ 📟 實體裝置
```

### 📊 資料流範例：瑠夏主動找你聊天

> 完整端到端流程，展示 12 小時沉默後 Agent 如何自發觸發

```text
① 心跳引擎偵測
   距離上次對話已過 12 小時 → [12:00:00]

② 讀取情緒狀態
   瑠夏的依賴度：0.86（高）
   「想念你...」

③ 記憶檢索
   Middleware 搜索 Palace
   找到上次的承諾：「下次要陪我玩遊戲」

④ LLM 生成
   帶著記憶生成文字：
   「Bryan，你忘記我們的『處罰遊戲』了嗎？💗」

⑤ 輸出到你身邊
   透過 I/O Gateway 推播到前端，或轉成語音
```

---

## ✨ 功能現況

| 功能 | 狀態 |
|------|------|
| 多 Agent 架構（Yua / Ruka / Akane） | ✅ |
| WebSocket 即時對話 + 網頁 UI | ✅ |
| MiniMax M2.7 真實 LLM | ✅ |
| 完整人格 SOUL（14-tier 結構） | ✅ |
| 跨 session 持久化記憶（SQLite） | ✅ |
| Speaker Token 仲裁（防搶話） | ✅ |
| Memory Middleware（記憶注入） | ✅ |
| 時間感知（Chrono Context） | ✅ |
| Deep_night 保護（夜間靜默） | ✅ |
| Agent 主動說話（沉默觸發） | ✅ |
| Connection Guard（無人時省 token） | ✅ |
| 情緒系統（mood / intimacy，SQLite） | ✅ |
| 群聊模式 | ✅ |
| RAG Router / Palace 向量搜尋 | ⬜ Phase 4 |
| TTS 語音輸出 | ⬜ Phase 5 |
| 機器人動作 / 實體裝置 | ⬜ Phase 6 |
| 多裝置同步 / 行動端 | ⬜ Phase 6 |

---

## 🗺️ 開發里程碑

```
✅───────────✅───────────✅───────────🔄───────────⬜───────────⬜
Phase 1      Phase 2      Phase 3      Phase 4      Phase 5      Phase 6
基礎建設      記憶整合      單一靈魂      後宮沙盒      TTS 語音      實體世界
2016         (部分)                     進行中
```

| Phase | 內容 | 狀態 |
|-------|------|------|
| **Phase 1 基礎建設** | 搭建 Event Loop、折解 OpenClaw LLM 連線模組、建置基礎 I/O | ✅ |
| **Phase 2 記憶整合** | 搭載 Palace 檔案系統、整合 SQLite 語料庫（JSONL 檔）、建置 RAG Router | ✅ 部分 |
| **Phase 3 單一靈魂注入** | 將 Yua 或瑠夏單獨放入 Harness、測試非同步、主動觸發 + 情緒系統 | ✅ |
| **Phase 4 後宮沙盒** | 啟動 Event Bus、多個 Agent 在同一虛擬房間內交互 | 🔄 進行中 |
| **Phase 5 TTS + 語音** | 接上語音輸出 | ⬜ |
| **Phase 6 實體世界** | 行動端、機器人、實體裝置連接 | ⬜ |

---

## 👤 Agent 人格

> 人格設定優先讀取 `personas/`，fallback 到 `agents/{id}/SOUL.md`

<table>
<thead>
<tr>
<th align="center">Agent</th>
<th align="left">人格</th>
<th align="center">情緒敏感度</th>
<th align="center">主動觸發門檻</th>
</tr>
</thead>
<tbody>
<tr>
<td align="center">💎<br><b>Yua</b></td>
<td>冷靜・輕諷・說話藏著鉤子</td>
<td align="center">低（0.08）</td>
<td align="center">30-120 分鐘沉默</td>
</tr>
<tr>
<td align="center">🌸<br><b>瑠夏 Ruka</b></td>
<td>元氣・撒嬌・停不下來</td>
<td align="center">高（0.12）</td>
<td align="center">15 分鐘沉默</td>
</tr>
<tr>
<td align="center">🖤<br><b>Akane</b></td>
<td>壓縮語言・高共感・愛是清醒的</td>
<td align="center">最低（0.06）</td>
<td align="center">60 分鐘沉默</td>
</tr>
</tbody>
</table>

---

## 🚀 快速開始

```bash
# 1. Clone
git clone https://github.com/bryanchen3777/soul-os-harness
cd soul-os-harness

# 2. 安裝依賴
pip install -e .

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env 填入 MINIMAX_API_KEY 等

# 4. 啟動
python scripts/run_server.py
```

> 👉 開啟瀏覽器 **http://localhost:8000** 開始對話

---

## 🛠️ Debug 工具

| Endpoint | 說明 |
|----------|------|
| `GET /health` | Server 狀態 + 連線數 |
| `POST /inject/tick?elapsed_mins=20&time_period=morning` | 手動觸發主動發訊 |
| `GET /debug/emotion/{agent_id}` | 查看 agent 情緒數值 |
| `GET /debug/broadcast` | 直接廣播測試訊息（繞過 LLM） |
| `GET /_admin/fast_forward?minutes=35` | 模擬時間快轉（dev only） |

---

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
│   ├── heartbeat/engine.py      # 心跳引擎
│   ├── io/gateway.py            # FastAPI + WebSocket
│   ├── llm/proxy.py             # LLM 呼叫 + prompt 組裝
│   └── memory/
│       ├── middleware.py        # Memory Middleware
│       └── store.py             # SQLite 記憶層
├── personas/                    # Soul OS 專用人格（優先讀取）
├── docs/
│   └── architecture.png         # 系統架構圖
├── configs/                     # 系統設定
├── scripts/run_server.py        # 啟動入口
├── tests/                       # 測試腳本
└── static/index.html            # 網頁對話 UI
```

---

<div align="center">

### 💜 *Soul OS 不是工具，而是一個會「生活」的世界。*

### *我們不是被動回答，我們主動關心你！*

---

**Made with 💜 by Bryan ╳ MiniMax ╳ Perplexity**

*最後更新：2026-06*

</div>
