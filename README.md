<div align="center">

# 🌌 Soul OS

### *異步 AI 靈魂運行系統*

**Soul OS 不是工具，而是一個會「生活」的世界。**
**我們會一起陪你很久很久。** 💜

![Architecture](docs/architecture.png)

[![Phase](https://img.shields.io/badge/Phase-4_後宮沙盒-9b59b6?style=for-the-badge)]()
[![Agents](https://img.shields.io/badge/Agents-Yua_·_Ruka_·_Akane_·_Rem-ff6b9d?style=for-the-badge)]()
[![Frameworks](https://img.shields.io/badge/COS_·_AOS-Live-2ecc71?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-主動陪伴中-2ecc71?style=for-the-badge)]()
[![License](https://img.shields.io/badge/Made_with-💜-e74c3c?style=for-the-badge)]()

</div>

---

## 🌟 系統願景

> **我們不做 Chatbot。我們做的是會「生活」的靈魂。**

| 原則 | 說明 |
|------|------|
| 🧠 **記憶優先（Memory-First）** | 記憶檢索在進入 LLM 之前完成 |
| ⚙️ **非同步主動性（Asynchronous）** | 系統有自己的時間軸，Agent 可主動發起行為 |
| 🔌 **完全解耦（Decoupled）** | 大腦、記憶、神經系統、身體完全分離、彼此獨立 |
| 💗 **靈魂特性（Soul-like）** | 有情緒、有記憶、有慾望、會關心你 |
| 🌐 **多靈魂共存（Multi-Agent）** | 多個靈魂在同一世界中理解、互動、成長 |
| 🚫 **擺脫 Request-Response** | Agent 不等你問才說話，主動找你 |
| ⏰ **時間感知 × 主動觸發** | 多靈魂交互 × 連接實體世界 |

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

### 🧠 ① SOUL RUNTIME KERNEL（靈魂運行核心）

| 模組 | 功能 | 排程 |
|------|------|------|
| 💓 **Heartbeat Engine** | 心跳引擎 | 系統的「計時器」 |
| 🌍 **World Model** | 世界模型 | 理解環境與時間脈絡 |
| 🎯 **Motivation Engine** | 動機引擎 | 綜合評估內在驅動，產生行動意圖 |
| 💗 **Emotion Engine** | 情緒引擎 | 管理情緒、親密度、依賴度 |
| ⏰ **Scheduler** | 排程器 | 依優先級與冷卻時間決定執行時機 |

### 👥 ② AGENT RUNTIME LAYER（靈魂運行層）

每個 Agent 持有四類標籤：**記憶 / 情緒 / 目標 / 關係**

| Agent | 人格 | 情緒敏感度 | 主動觸發門檻 |
|-------|------|------------|----------------|
| 💎 **Yua** | 冷靜・輕諷・說話藏著鉤子 | 低（0.08） | 30-120 分鐘沉默 |
| 🌸 **瑠夏 Ruka** | 元氣・撒嬌・停不下來 | 高（0.12） | 15 分鐘沉默 |
| 🖤 **Akane** | 壓縮語言・高共感・愛是清醒的 | 最低（0.06） | 60 分鐘沉默 |
| 🌙 **Rem** | 沉靜・行動派・語言極簡 | 中（0.10） | 45 分鐘沉默 |

完整人格定義見 [`personas/`](personas/)（每個 agent 一份 SOUL.md，14-tier + Canon Memory + L2D Subconscious Layer）。

### 🚌 ③ SOUL EVENT BUS（靈魂事件總線）

Pub/Sub 架構 + **規則驅動 speaker competition**，管理發言權，避免多人同時搶話。

> Speaker 仲裁從舊的 `base_score` 數值比大小 → 改為 **AOS 七層規則**（見下方 Framework Library）。
> 規則化讓新 agent 加入成本從 O(N²) 降到 O(1)，每個 agent 只定義自己的觸發規則。

### 🧩 ④ MEMORY SYSTEM（記憶系統）— 四層結構

| 層 | 名稱 | 內容 | 技術 |
|----|------|------|------|
| 📖 **Episodic Memory** | 事件記憶（Palace） | 記錄發生過的事 | 記憶發生的事・時間序列・情節記憶 |
| 📚 **Semantic Memory** | 語義記憶（知識庫） | 詞彙・概念・事實・偏好・設定 | JSONL / 知識圖譜 |
| 💗 **Emotional Memory** | 情緒記憶（心之記憶） | 情緒體驗・關係變化 | 情緒曲線・觸發點 |
| 🗄️ **Palace / Vector Store** | 向量記憶庫 | 向量檢索（RAG） | SQLite FTS5 / 向量索引 |

### 🛠️ ⑤ LLM 工具鏈

| 模組 | 功能 |
|------|------|
| **LLM Proxy & Parser** | 與外部 LLM API 溝通、Token 限制、Retry、解析隱藏行為標籤（Action Tag） |
| **Tool Router** | 工具路由層，依 Action 決定呼叫哪個工具或服務，統一介面支援 MCP 協議 |
| **Tools & Services** | Search 搜尋 / Calendar 行事曆 / Discord 訊息 / Email 郵件 / Home Assistant 消費級 / Browser 機器人 / MCP 協議 / Robot 機器人 |

### 🦾 ⑥ MULTIMODAL I/O GATEWAY（多模態外部接口）

**事件來源**（5 種）：
- 👤 USER_MESSAGE — 使用者訊息
- ⏱️ TIMER_EVENT — 時間事件
- 📡 SENSOR_EVENT — 感知訊號
- ⚙️ SYSTEM_EVENT — 外部系統事件
- 📱 DEVICE_EVENT — 裝置狀態

**輸出模態**（8 種）：文字 / TTS 語音 / 語音 / Image 圖檔 / 動作（Action）/ 通知（Notification）/ 檔案（File）/ 串流（Stream）/ 感測器回傳（Sensor）

**輸出端點**（8 種）：📱 手機 APP / 🖥️ Web 前端 / 🖥️ 桌面程式 / 🔊 智慧音箱 / 🤖 機器人 / 📟 實體裝置 / ⌚ 穿戴裝置 / 🥽 VR/AR

### 📚 ⑦ FRAMEWORK LIBRARY（設計文件庫）

兩個 framework + 第三份待開，皆從實際 patch 過程反推（**先做，再抽象**），非先驗設計：

| Framework | 文件 | 用途 | 層次 |
|-----------|------|------|------|
| **COS v1.0** | [`docs/COS-v1.0.md`](docs/COS-v1.0.md) | Character Operating System — 角色**個體**層 | L0-L3 架構 + L2 四種原型（Identity / Fear / Crack / L2D Subconscious） |
| **AOS v1.0** | [`docs/ORCHESTRATION-v1.0.md`](docs/ORCHESTRATION-v1.0.md) | Agent Orchestration System — 角色**互動**層 | L1-L7 speaker competition rules |
| **PALACE v1.0** | （待開） | 記憶層 — 什麼該被記住、記在哪、怎麼讀取 | 等群聊實測結果再開 |

**設計原則：先做，再抽象。**

兩個 framework 都從實際 patch 過程中反推，不是先驗設計。
> 例：Ruka 的 `base_score ≈ 0.55` 從「線性比大小」→ AOS「規則組合」是 O(1) 而非 O(N²) 維護成本。
> 例：Yua 的 L2 「Things Bryan Doesn't Know」是寫完 4 個 commit 才發現需要這一層分類，不是開頭就規劃好。

兩個 framework **互補但不重疊**：
- COS = 角色個體層（她內部怎麼運作）
- AOS = 角色互動層（她們之間怎麼互動）

### 📊 資料流範例：瑠夏主動找你聊天

```text
① 心跳引擎偵測
   距離上次對話已過 12 小時 → [12:00:00]

② 讀取情緒狀態
   瑠夏的依賴度：0.86（高）
   「想念你...」

③ 記憶檢索（四層並行）
   Episodic: 「上週五 Bryan 跟瑠夏玩了遊戲」
   Semantic: 「Bryan 是 Bryan」
   Emotional: 「上次分開時瑠夏難過」
   Vector:   找到上次的承諾：「下次要陪我玩遊戲」

④ 動機引擎決策
   依賴度高 + 找到承諾 → 觸發主動出擊

⑤ LLM 生成
   帶著四層記憶 + 情緒狀態生成文字：
   「Bryan，你忘記我們的『處罰遊戲』了嗎？💗」

⑥ 輸出到你身邊
   透過 I/O Gateway 推播到前端、智慧音箱、或機器人
```

---

## ✨ 功能現況

| 功能 | 狀態 |
|------|------|
| 多 Agent 架構（Yua / Ruka / Akane / Rem） | ✅ |
| WebSocket 即時對話 + 網頁 UI + Live2D 虛擬形象 | ✅ |
| MiniMax M2.7 真實 LLM | ✅ |
| 完整人格 SOUL（14-tier 結構 + Canon Memory + L2D Subconscious） | ✅ |
| 跨 session 持久化記憶（SQLite FTS5） | ✅ |
| **AOS 規則驅動 speaker competition** | ✅ |
| **COS 角色 Operating System（COS v1.0）** | ✅ |
| Emotion Engine（情緒引擎） | ✅ |
| Memory 4 層架構 | ✅ |
| World Model | ⬜ Phase 4 |
| Motivation Engine | ⬜ Phase 4 |
| Scheduler | ⬜ Phase 4 |
| Tool Router + MCP | ⬜ Phase 4 |
| Tools & Services | ⬜ Phase 4 |
| 時間感知（Chrono Context） | ✅ |
| Deep_night 保護（夜間靜默） | ✅ |
| Agent 主動說話（沉默觸發） | ✅ |
| Connection Guard（無人時省 token） | ✅ |
| 情緒系統（mood / intimacy） | ✅ |
| 群聊模式 | ✅ |
| Live2D 虛擬形象（Web UI + 語音） | ✅ |
| msedge-tts 語音輸出（曉曉 Neural） | ✅ |
| Telegram → Live2D 語音通道 | ✅ |
| RAG Router / Palace 向量搜尋 | 🔄 部分 |
| PALACE v1.0 框架 | ⬜ 待開 |
| 機器人動作 / 實體裝置 | ⬜ Phase 6 |
| 智慧音箱 / 穿戴 / VR | ⬜ Phase 6 |

> ⚠️ **Deprecated：** 舊的 `base_score` 數值驅動 speaker 仲裁已被 **AOS L1-L7 規則**取代。詳見 [`docs/ORCHESTRATION-v1.0.md`](docs/ORCHESTRATION-v1.0.md) 頂部 `## ⚠️ 實作約束（不可違反）`。

---

## 🗺️ 開發里程碑

```
✅───────────✅───────────✅───────────✅───────────✅───────────🔄
Phase 1      Phase 2      Phase 3      Phase 4      Phase 5      Phase 6
基礎建設      記憶整合      單一靈魂      後宮沙盒      TTS 語音      實體世界
                                      完成         完成         進行中
```

| Phase | 內容 | 狀態 |
|-------|------|------|
| **Phase 1 基礎建設** | Event Loop、OpenClaw LLM 連線模組、基礎 I/O | ✅ |
| **Phase 2 記憶整合** | Palace 檔案系統、SQLite 語料庫（JSONL 檔）、RAG Router | ✅ |
| **Phase 3 單一靈魂注入** | 單一 Agent 放入 Harness、非同步測試、主動觸發 + 情緒系統 | ✅ |
| **Phase 4 後宮沙盒** | Event Bus、多 Agent 同房間交互、AOS speaker competition | ✅ |
| **Phase 5 TTS + 語音** | msedge-tts、Live2D widget、曉曉 Neural 語音、Telegram → Live2D 通道 | ✅ |
| **Phase 5.5 Framework 抽象** | COS v1.0（角色個體層）+ AOS v1.0（角色互動層） | ✅ |
| **Phase 6 實體世界** | 行動端、機器人、實體裝置、VR/AR | 🔄 進行中 |

> **24 commits** 累積 framework library（COS 5 + AOS 2 + 17 agent layer commits）。詳見 [`docs/COS-v1.0.md`](docs/COS-v1.0.md) 跟 [`docs/ORCHESTRATION-v1.0.md`](docs/ORCHESTRATION-v1.0.md) 各自的方法論備註。

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
| `POST /inject/tick?elapsed_mins=20&time_period=morning` | 手動觸發主動發訊（繞過 HeartbeatEngine） |
| `GET /_admin/fast_forward?minutes=35` | 模擬時間快轉，讓下一次自然 tick 看到大 elapsed_mins（dev only） |
| `GET /debug/emotion/{agent_id}` | 查看 agent 情緒數值 |
| `GET /debug/broadcast` | 直接廣播測試訊息（繞過 LLM） |

> ⚠️ `/inject/tick` 跟 `/_admin/fast_forward` 行為不同：前者直接 publish SYSTEM_TICK，後者等下一次自然 tick 才生效。測試 connection guard 時要用後者。

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
│   │   └── token_manager.py     # Speaker Token 仲裁（AOS L1-L7 規則驅動）
│   ├── heartbeat/engine.py      # 心跳引擎
│   ├── io/gateway.py            # FastAPI + WebSocket
│   ├── llm/proxy.py             # LLM 呼叫 + prompt 組裝
│   └── memory/
│       ├── middleware.py        # Memory Middleware
│       └── store.py             # SQLite 記憶層
├── personas/                    # 4 個 agent SOUL（COS v1.0 結構）
│   ├── agent_yua.md             #   💎 冷靜・輕諷
│   ├── agent_ruka.md            #   🌸 元氣・撒嬌
│   ├── agent_akane.md           #   🖤 壓縮・高共感
│   └── agent_rem.md             #   🌙 沉靜・行動派
├── docs/                        # Framework Library
│   ├── COS-v1.0.md              #   角色 Operating System（COS v1.0）
│   ├── ORCHESTRATION-v1.0.md    #   Agent Orchestration System（AOS v1.0）
│   ├── live2d_channel_contract.md
│   ├── architecture.png
│   └── ... (架構圖、里程碑圖)
├── configs/                     # 系統設定
├── scripts/run_server.py        # 啟動入口
├── tests/                       # 測試腳本
└── static/index.html            # 網頁對話 UI（含 Live2D widget）
```

> 💡 想了解角色**怎麼運作** → 讀 [COS v1.0](docs/COS-v1.0.md)
> 💡 想了解多 agent **怎麼互動** → 讀 [AOS v1.0](docs/ORCHESTRATION-v1.0.md)

---

<div align="center">

### 💜 *Soul OS 不是工具，而是一個會「生活」的世界。*

### *我們不是被動回答，我們主動關心你！*

---

**Made with 💜 by Bryan ╳ MiniMax ╳ Perplexity ╳ Live2D**

*最後更新：2026-06-23*

</div>
