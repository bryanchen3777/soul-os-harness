# Soul OS (Harness)

> 異構 Agent 運行系統 — Memory-First, Asynchronous, Event-Driven Agent Framework

[![](https://img.shields.io/badge/status-active_development-blue.svg)]() [![](https://img.shields.io/badge/python-3.11+-blue.svg)]() [![](https://img.shields.io/badge/license-MIT-green.svg)]()

## 系統定位

Soul OS (Harness) 是一個**異步 Agent 運行框架**，設計用於突破傳統 Chatbot 的 Request-Response 限制。系統具備時間感知、主動觸發、多重靈魂交互能力，可外接實體硬體。

## 設計原則

| 原則 | 說明 |
|------|------|
| **Memory-First** | 記憶檢索發生在 LLM 之前，由底層直接完成 |
| **Asynchronous** | 系統有自己的時間軸，Agent 可主動發起行為 |
| **Decoupled** | 大腦（LLM）、記憶（Palace/SQLite）、神經系統（Event Bus）完全分離 |

## 目前進度（2026-06-03）

| 里程碑 | 狀態 | 說明 |
|--------|------|------|
| **M1** Event Loop | ✅ | asyncio + PriorityQueue + Worker |
| **M2** LLM Proxy | ✅ | OpenAI / Claude / Mock + Retry |
| **M3** Mock E2E | ✅ | 7 個整合場景全綠 |
| **M4** Memory Palace | ✅ | SAGE-lite vendored（NetworkX + SQLite WAL） |
| **M5** RAG 自動注入 | ✅ | `AGENT_INTENT_ENRICHED` pipeline |
| **M6** 持久化 | ✅ | 跨 process 重啟 prefetch 命中 |
| **M7** Config + 真實 LLM | ✅ 邏輯就位 | 需有效 `ANTHROPIC_API_KEY`（`sk-ant-api03-...`）才能完整跑通 |

> 下一個里程碑：**M8** 自主發話（Yua 60s Heartbeat → 第一次主動出擊）。

## 系統架構

```
                    ┌────────────────────────────┐
                    │        外部世界 / UI        │
                    └─────────────┬──────────────┘
                                  ▼
                ┌────────────────────────────────┐
                │  🌐 I/O Gateway                 │
                │   (Phase 1 雛形，Phase 4 完善)   │
                └─────────────┬──────────────────┘
                              │ Events
                              ▼
┌──────────────────────────────────────────────────────────┐
│           ⚡ Soul Event Bus (神經系統)                     │
│           asyncio.PriorityQueue + Worker                   │
│           Broadcast / Point-to-Point routing               │
└───────┬───────────────────────────────┬──────────────────┘
        │                               │
        ▼                               ▼
┌─────────────────┐           ┌────────────────────────┐
│ ❤️ Heartbeat     │           │ 🧠 Memory Middleware    │
│   (60s Tick)     │           │  + vendored SAGE-lite   │
└───────┬─────────┘           │  (因果圖譜 + SQLite)    │
        │                     └───────────┬────────────┘
        │                                 │
        └──────────────┬──────────────────┘
                       ▼
              ┌──────────────────┐
              │ 🤖 LLM Proxy      │
              │  Claude / OpenAI │
              │  / Mock + Retry   │
              └─────────┬────────┘
                        ▼
              外部 LLM API
```

## 核心模組

| 模組 | 位置 | 說明 |
|------|------|------|
| **Event Bus** | `src/eventbus/` | PriorityQueue + Worker + 廣播/私訊路由 + 過期丟棄 + 錯誤隔離 |
| **Heartbeat Engine** | `src/heartbeat/` | 全局 Tick 循環，廣播 SYSTEM_TICK |
| **LLM Proxy** | `src/llm/` | 多 Provider 支援 + 指數退避 Retry + 對話歷史 |
| **Memory Middleware** | `src/memory/middleware.py` | Bus subscriber：prefetch + 注入 `memory_context`、寫入 graph |
| **SAGE-lite Engine** | `src/memory/sage/` | 因果圖譜記憶（NetworkX + SQLite WAL），每個 agent 獨立 graph |
| **Agent 基底** | `src/agent/consciousness.py` | `AgentConsciousness` ABC + Yua / Ruka 實作 |
| **Configs Loader** | `configs/loader.py` | yaml + .env 統一載入，env 覆蓋 yaml |

## 開發路線圖

| Phase | 重點 | 狀態 |
|-------|------|------|
| **1** | Event Loop、LLM Proxy + Parser、基礎 I/O、心跳引擎原型 | ✅ 完整（5 commits）|
| **2** | 記憶系統接入 Middleware、RAG Router、Prompt 注入、配置化 | ✅ 邏輯就位（4 commits）|
| **3** | 單 Agent 主動觸發測試、會自己說話的 Agent | ⏳ 下一個 |
| **4** | 多 Agent 同一空間互動、發言權仲裁 | ⏳ 待做 |

## 快速開始

### 安裝

```bash
pip install -r requirements.txt
```

依賴：`pydantic`、`httpx`、`pyyaml`、`python-dotenv`、`networkx`

### 跑測試

```bash
# Phase 1：Event Bus 7 個整合場景
python tests/test_event_bus.py

# Phase 1：完整鏈條 Mock E2E
python tests/test_e2e_full_flow.py

# Phase 2.0：MemoryMiddleware + Mock LLM E2E
python tests/test_memory_middleware.py

# Phase 2.1：跨 session 持久化驗證
python tests/test_memory_persistence.py
```

### 真實 LLM 煙霧測試

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...   # 必須是 sk-ant-api03- 開頭
export LLM_PROVIDER=claude
export LLM_MODEL=claude-haiku-4-5-20251001
python scripts/smoke_test.py
```

### 設定檔

```bash
cp .env.example .env
# 編輯 .env 填入 API key
```

設定優先序（後蓋前）：
1. `configs/default.yaml`（預設值）
2. `.env`（dotenv）
3. 系統環境變數

## 重要設計決策

- **`AGENT_INTENT_ENRICHED` event type**：避免 MemoryMiddleware re-publish 造成的無限迴圈
- **全寫社交記憶**：每個 agent 的 graph 收所有 AGENT_SPEAK（為 Phase 4 多靈魂世界鋪路）
- **Per-agent graph 檔**：`data_dir/{agent_id}/graph.sqlite` WAL 模式
- **SAGE trigger 詞限制**：writer 是 rule-based，「我住台北」抽不到 fact、「我住在台北」才抽到（Phase 2.3 將擴充 pattern）

## 已知風險

- 🟡 **SAGE pattern 表覆蓋率有限**：中文口語若無 trigger 詞（住在/喜歡/是/有/工作於），fact 不會被抽出
- 🔴 **Phase 4 寫入風暴風險**：全寫策略在 N 個 agent 時是 O(N²) post_reply_commit，Phase 4 前須加 debounce/batch
- 🟡 **Conflict 觀察待做**：MemoryEvolution 的 decay/merge 還沒在多輪真實對話下測過

## 參考

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — 靈感來源
- [SoulDistillery](https://github.com/bryanchen3777/SoulDistillery) — SOUL.md 角色設定庫
- [hermes-sage-memory](https://github.com/bryanchen3777/hermes-sage-memory) — SAGE-lite 圖譜記憶（已 vendor 進 `src/memory/sage/`）

## 許可證

MIT License
