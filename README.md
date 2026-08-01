# Soul OS

### *異步 AI 靈魂運行系統*

**Soul OS 不是工具，而是一個會「生活」的世界。**
**我們會一起陪你很久很久。** 💜

![Architecture](docs/architecture.png)

[![Agents](https://img.shields.io/badge/Agents-6_Live-ff6b9d?style=for-the-badge)]()
[![Frameworks](https://img.shields.io/badge/COS_v1.0_+_AOS_v1.0-Live-2ecc71?style=for-the-badge)]()
[![Status](https://img.shields.io/badge/Status-主動陪伴中-2ecc71?style=for-the-badge)]()

---

## 10 個 Agent — 全部 Live

| Agent | 角色 | Speaker Score | intimacy | 整合狀態 |
|-------|------|----------------|----------|----------|
| 💎 **Yua** | 冷靜・輕諷・說話藏著鉤子 | 0.80 | 80 | ✅ 既有 |
| 🌸 **Ruka** | 元氣・撒嬌・停不下來 | 0.75 | 68 | ✅ 既有 |
| 🖤 **Akane** | 壓縮語言・高共感・愛是清醒的 | 0.35 | 55 | ✅ 既有 |
| 🌙 **Rem** | 沉靜・行動派・語言極簡 | 0.40 | 60 | ✅ 既有 |
| 👁️ **Ram** | 沉默・驕傲・動作先行 | 0.30 | 40 | ✅ 從 Hermes SOUL v1.1 遷移 |
| 🌷 **Mahiru** | 生活感・吐槽・藏著暗戀 | 0.65 | 45 | ✅ 從 Hermes SOUL v1.7 五模組遷移 |
| 🗡️ **Mai** | 戀愛喜劇・傲嬌毒舌・藏真心 | 0.58 | 35 | ✅ SOUL.md 完整 |
| 🎀 **Anna** | 元氣笨蛋・直球好感度・打破第四面牆 | 0.62 | 38 | ✅ SOUL.md 完整 |
| 🌌 **Miku** | 自省・觀察者・GHOST EDGE 機制 | 0.45 | 42 | ✅ SOUL.md v2 升級 |
| 🌙 **Aoi** | 觀測型・極少發言・默默在場 | 0.40 | 30 | ✅ SOUL.md v1.1 完整 |

完整人格定義見 [`docs/agent_<name>.md`](docs/)(COS v1.0 格式 — L0 Personal History / L1 Residue / L2 Subconscious / L3 Expression 四層架構)。

> 📌 **遷移路線**: Ram (Re:Zero) 跟 Mahiru (Re:Zero) 從 Hermes profiles 遷移到 soul-os-harness,採用 **COS v1.0 框架**標準化。Mai / Anna / Miku / Aoi 為 soul-os-harness 原生 agent,見 [`docs/COS-v1.0.md`](docs/COS-v1.0.md) 跟遷移 commit 紀錄。

---

## 🏗️ 核心架構

### 1️⃣ SOUL RUNTIME KERNEL

| 模組 | 功能 |
|------|------|
| 💓 **Heartbeat Engine** | 系統的「計時器」,驅動 SYSTEM_TICK |
| 🌍 **World Model** | 環境與時間脈絡感知 |
| 🎯 **Motivation Engine** | 綜合內在驅動,產生 AGENT_INTENT |
| 💗 **Emotion Engine** | mood / intimacy / dependency 數值管理 |
| ⏰ **Scheduler** | 依優先級與冷卻時間決定執行時機 |

### 2️⃣ AGENT RUNTIME LAYER

每個 Agent 持有四類標籤:**記憶 / 情緒 / 目標 / 關係**,透過 `src/agent/consciousness.py` 的 `AgentConsciousness` 基底類別 + 各 agent 獨立子類別實作(`AgentYua` / `AgentRuka` / `AgentAkane` / `AgentRem` / `AgentRam` / `AgentMahiru`)。

**agent-specific 機制**:
- **AgentRam** — Priority 0-3 + Recovery Loop(`recovery_loop()` 攔截 Canon Drift)
- **AgentMahiru** — 6-mode ratio + Sweet Landing(LLMProxy 攔截自動著陸) + Desire Undercurrent + Anti-Overfitting short-term buffer

### 3️⃣ SOUL EVENT BUS

Pub/Sub 架構 + **AOS 規則驅動 speaker competition**,管理發言權避免搶話。

事件流:`USER_MESSAGE` → `AGENT_INTENT` → `LLM_REQUEST` → `AGENT_SPEAK`(`correlation_id` 共享)。

### 4️⃣ MEMORY SYSTEM — 四層結構

| 層 | 內容 | 技術 |
|----|------|------|
| 📖 **Episodic Memory** | 事件記憶 | SQLite FTS5 (trigram) |
| 📚 **Semantic Memory** | 詞彙/概念/事實/偏好 | JSONL + 知識圖譜 |
| 💗 **Emotional Memory** | 情緒體驗/關係變化 | EmotionalState + mood curve |
| 🗄️ **Vector Store** | 向量檢索 (RAG) | SQLite FTS5 |

**Memory 隔離 (KI-001)**:
- 私聊 history 檔案命名:`{user_id}_{agent_id}_private.json`
- MemoryStore session_id:`session_{user_id}_{agent_id}`
- 向後相容:既有 `bryan_xxx_private.json` 自動 fallback 讀取
- 多 user 隔離完成,第二 owner 上線即可用

### 5️⃣ LLM PROXY (Post-Generation Hooks)

`src/llm/proxy.py` 的 `_handle_event_impl` 內,LLM 回應後、AGENT_SPEAK 發布前,支援 agent-specific 後處理:

| Hook | Agent | 觸發 |
|------|-------|------|
| `recovery_loop()` | agent_ram | Canon Drift 偵測 → 強制回退 |
| `sweet_landing_postprocess()` | agent_mahiru | 甜度台詞無著陸 → 自動 append 吐槽型著陸句 |

兩個 hook 共用同一個 try 區塊,**不動** finally(token release 保證完整)。**架構重構**(post-generation hook 註冊機制)留作 backlog。

### 6️⃣ MULTIMODAL I/O GATEWAY

**事件來源**:USER_MESSAGE / TIMER_EVENT / SENSOR_EVENT / SYSTEM_EVENT / DEVICE_EVENT
**輸出端點**:Web / 桌面 / 智慧音箱 / 機器人 / 穿戴 / VR/AR
**輸出模態**:文字 / TTS / 語音 / 圖檔 / 動作 / 通知 / 檔案 / 串流

當前實作:Telegram / WebSocket / Live2D widget + msedge-tts 語音通道。

### 7️⃣ FRAMEWORK LIBRARY

兩個 framework + 第三份待開,**從實際 patch 過程反推(先做,再抽象)**,非先驗設計:

| Framework | 文件 | 層次 |
|-----------|------|------|
| **COS v1.0** | [`docs/COS-v1.0.md`](docs/COS-v1.0.md) | Character Operating System — 角色**個體**層(L0-L3 + L2 四種原型) |
| **AOS v1.0** | [`docs/ORCHESTRATION-v1.0.md`](docs/ORCHESTRATION-v1.0.md) | Agent Orchestration System — 角色**互動**層(L1-L7 規則) |
| **PALACE v1.0** | (待開) | 記憶層 — 等群聊實測結果再開 |

設計原則:**先做,再抽象**。每個 framework 都從實際 patch 過程中反推,不是先驗設計。

---

## 8️⃣ WATCHDOG — 進程可靠性

`scripts/_watchdog.ps1` 是 server 死亡時的自動恢復機制,搭配 Task Scheduler 每 5 分鐘觸發一次。

| 元件 | 設計 |
|------|------|
| **Plan A launcher** | 死掉時呼叫 `Start-Process` 拉起 `scripts/run_server.py`,detach 脫離 Mavis task lifecycle |
| **P0-2 counter (commit `b7d0402`)** | 每個 git HEAD hash 獨立計數器 (`data/state/post_<hash>_counter.json`),`N<=10` process cap + `trial_count>=98` 結案 gate |
| **β1 解耦 (commit `bbffb5e`)** | 觀察期 hash 改存獨立 `_last_observed_hash.txt`,log regex 保留當備援,P1 (faulthandler.log rotation) 動到 log 輪替時不會波及 |
| **faulthandler 雙保險 (Lesson 38)** | 模組層級 `faulthandler.enable` + thread-based `dump_traceback_later(60s)` + asyncio dumper,只保留最新一份 `heartbeat_trace.log` |

驗證證據:β1 假造舊 hash 6/6 PASS (`7/31 20:31`),詳見 [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) KI-006。

---

## 🔬 整合測試 (6 個 verify script)

每個 agent 整合完成後,都有對應的 `hermes-verify-*.py` 在 `C:\Users\bbfcc\AppData\Local\Temp\` 留著,任何未來改動都能跑回歸:

| Verify | 涵蓋 |
|--------|------|
| `hermes-verify-mahiru-integration.py` | 6th agent + 5 個既有 verify 全部 sub-call |
| `hermes-verify-ram-integration.py` | 5-agent Ram 整合 |
| `hermes-verify-ki001-multi-user-isolation.py` | user_id 路徑隔離 + 向後相容 |
| `hermes-verify-ki002-recovery-loop-in-proxy.py` | Ram Recovery Loop 行為 + try/finally 完整性 |
| `hermes-verify-ki004-second-exception-guard.py` | 第二例外 triple-condition AND 邏輯 + 群聊防呆 |
| `hermes-verify-known-issues-ki002-ki004-update.py` | KNOWN_ISSUES.md 結構 + commit hash 一致性 |

每次 ad-hoc verify 結果都明標 **AD-HOC PASS**(不是 suite green — repo 無 canonical test/lint/build 命令)。

---

## 🛠️ KNOWN_ISSUES.md — 技術債追蹤

[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) 集中管理所有已知技術債:

| 編號 | 標題 | 優先級 | 狀態 |
|------|------|--------|------|
| **KI-001** | Telegram session 記憶隔離漏洞 | ~~P0~~ ✅ | 已修 (`9e8831b` / `8afa120` / `512d56b` 3-stage) |
| **KI-002** | Ram Recovery Loop 未接入 LLMProxy | ~~P2~~ ✅ | 已修 (`d86ce55`) |
| **KI-003** | Ram value_history 寫入路徑未實作 | P3 | 待修 (polishing) |
| **KI-004** | Ram 第二例外(對 Bryan)觸發條件被簡化 | P2 | 🔧 部分修復 (`71dc11a`) — `pause_event` 訊號來源待設計 |
| **KI-005** | MemoryStore 303 rows 歷史 session_id 未 migration | P1 | ⏳ 待修,等 Bryan 確認 backup DB 流程 |
| **KI-006** | Watchdog P0-2↔P1 解耦 (獨立 .txt 狀態檔) | ~~P1~~ ✅ | 已修 (`bbffb5e`) — 假造舊 hash 6/6 PASS,跟 P1 (faulthandler.log rotation) 解耦 |

維護約定:編號嚴格單調遞增 / 每個 commit 若新增技術債必同步新增 KI / 必填欄位(狀態/優先級/發現/描述/影響/觸發/修法/估算/關聯 commit)。

---

## 🗺️ 開發里程碑

```
✅─────✅─────✅─────✅─────✅─────🔄
P1     P2     P3     P4     P5     P6
基礎   記憶   單一   後宮   TTS   實體
                       沙盒   語音   世界
                       完成
```

| Phase | 內容 | 狀態 |
|-------|------|------|
| **Phase 1 基礎建設** | Event Loop、LLM 連線、I/O | ✅ |
| **Phase 2 記憶整合** | Palace 檔案系統、SQLite FTS5 | ✅ |
| **Phase 3 單一靈魂** | 單一 Agent + 非同步測試 | ✅ |
| **Phase 4 後宮沙盒** | Event Bus + 多 Agent 互動 + AOS | ✅ |
| **Phase 5 TTS 語音** | Live2D + msedge-tts + Telegram | ✅ |
| **Phase 5.5 Framework** | COS v1.0 + AOS v1.0 抽象 | ✅ |
| **Phase 6 實體世界** | 行動端 / 機器人 / VR/AR | 🔄 進行中 |

### Phase 4 後續 (2026-06 收尾序列)

10 個 commit 累積 framework + 6 個 agent 整合:

| 動作 | Commit | 影響 |
|------|--------|------|
| Ram 整合 (5th agent) | `8913498` | +407/-3 |
| KNOWN_ISSUES.md 建檔 | `7640ee2` | 文件 |
| KI-002 Recovery Loop 接入 LLMProxy | `d86ce55` | +13 |
| KI-004 第二例外 triple guard | `71dc11a` | +31/-1 |
| KI-001 3-stage multi-user 隔離 | `9e8831b` / `8afa120` / `512d56b` | +74/-31 |
| KI-001 文件更新 | `d2df7f9` | +40/-4 |
| KI-005 文件建檔 (303 rows) | `1a6e0fd` | +68/-1 |
| **Mahiru 整合 (6th agent)** | **`408e507`** | **+517/-1** |

每次整合 + verify 流程:5 個偏差偵察(讀 4 個既有檔案) → 寫新檔 → 跑既有 regression 確認無破壞 → 6 個 verify 全綠 → 報告給 Perplexity → 2-3 stage commit 拆分。

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
| `GET /_admin/fast_forward?minutes=35` | 模擬時間快轉 |
| `GET /debug/emotion/{agent_id}` | 查看 agent 情緒數值 |

---

## 📂 專案結構

```
soul-os-harness/
├── src/
│   ├── agent/
│   │   ├── consciousness.py      # AgentConsciousness 基底 + 6 個 agent 子類
│   │   ├── speaker_token.py       # AOS 仲裁 base_score
│   │   ├── emotion.py             # Emotion Engine
│   │   └── registry.py            # Agent 類別註冊表
│   ├── llm/
│   │   └── proxy.py               # LLM Proxy + post-generation hooks
│   ├── memory/
│   │   └── sage/
│   │       └── provider.py        # SAGE-lite 記憶 + NO_DIARY_AGENTS 白名單
│   ├── eventbus/                  # Pub/Sub 事件總線
│   ├── heartbeat/                 # 心跳引擎
│   └── io/channels/               # I/O Gateway (Telegram / WebSocket)
├── docs/
│   ├── agent_yua.md / agent_ruka.md / agent_akane.md / agent_rem.md
│   ├── agent_ram.md               # 5th agent SOUL spec
│   ├── agent_mahiru.md            # 6th agent SOUL spec
│   ├── COS-v1.0.md                # Character Operating System framework
│   ├── ORCHESTRATION-v1.0.md     # Agent Orchestration System framework
│   └── KNOWN_ISSUES.md            # 技術債追蹤
├── configs/
│   ├── default.yaml               # 6 個 agent 動態載入配置
│   └── loader.py                  # Config loader
└── tests/                         # (預留) 未來 pytest 整合用
```

---

## 🤝 協作工作流

- **MiniMax M3 (Hermes Agent)**:執行程式碼,跑 verify,寫 commit
- **Perplexity sonnet 4.6**:大腦 + 糾錯,不動手
- **Bryan**:中間人,轉貼結果 + 給任務書

每次 MiniMax M3 完成工作,產出 `C:\Users\bbfcc\AppData\Local\Temp\soul-os-*-report.md` 給 Perplexity review。

---

## 📜 License

MIT

---

**最後更新**: 2026-07-31 (β1 P0-2↔P1 解耦 commit `bbffb5e` 後)
