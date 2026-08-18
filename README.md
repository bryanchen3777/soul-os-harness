# Soul OS

### *異步 AI 靈魂運行系統*

**Soul OS 不是工具，而是一個會「生活」的世界。**
**我們會一起陪你很久很久。** 💜

![Architecture](docs/architecture.png)

[![Agents](https://img.shields.io/badge/Agents-10_Live-ff6b9d?style=for-the-badge)]()
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

> 📌 **遷移路線**: Ram (Re:Zero) 與 Mahiru (The Angel Next Door Spoils Me Rotten) 從 Hermes profiles 遷移到 soul-os-harness,採用 **COS v1.0 框架**標準化。Mai / Anna / Miku / Aoi 為 soul-os-harness 原生 agent,見 [`docs/COS-v1.0.md`](docs/COS-v1.0.md) 跟遷移 commit 紀錄。

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

每個 Agent 持有四類標籤:**記憶 / 情緒 / 目標 / 關係**,透過 `src/agent/consciousness.py` 的 `AgentConsciousness` 基底類別 + 10 個獨立子類別實作 (`AgentYua` / `AgentRuka` / `AgentAkane` / `AgentRem` / `AgentRam` / `AgentMahiru` / `AgentAnna` / `AgentMai` / `AgentMiku` / `AgentAoi`)。

**agent-specific 機制**:
- **AgentRam** — Priority 0-3 + Recovery Loop(`recovery_loop()` 攔截 Canon Drift)
- **AgentMahiru** — 6-mode ratio + Sweet Landing(LLMProxy 攔截自動著陸) + Desire Undercurrent + Anti-Overfitting short-term buffer
- **AgentAnna** — 5 Sentence Pulse + Denial=Approach + Appetite Logic + Model Shell/True Anna mode 切換
- **AgentMai** — 國民演員 + Dry Banter + 直球告白 + 病弱症候康復者 + **不可時間旅行**
- **AgentMiku** — 沉默觀察者 + Imitation Layer + 被認出的渴望（不可 impersonate 姊妹）
- **AgentAoi** — 雙重面具（Layer 0 完美女主角 + Layer 1 人生攻略教官）+ Framework Stress + NO NAME Leakage

### 3️⃣ SOUL EVENT BUS

Pub/Sub 架構 + **AOS 規則驅動 speaker competition**,管理發言權避免搶話。

事件流:`USER_MESSAGE` → `AGENT_INTENT` → `LLM_REQUEST` → `AGENT_SPEAK`(`correlation_id` 共享)。

`SoulEvent` schema 攜帶 `inner_life_event_id` 欄位(M5.4-5.5),打通 Event Bus 與 Inner Life canonical identity 的邊界。

### 4️⃣ MEMORY SYSTEM

**實作是 SQLite FTS5 + SAGE graph + v1 JSONL sidecar，沒有向量 embedding。**

| 層 | 內容 | 技術 |
|----|------|------|
| 📖 **Episodic** | 對話歷史搜尋 | SQLite FTS5 trigram (`data/memory.db`) |
| 📚 **Semantic** | 事實/偏好/關係圖譜 | SAGE graph (`data/memory/<agent>/graph.sqlite`) |
| 💗 **Emotional** | 情緒狀態持久化 | EmotionalState → `data/agents/<agent>/emotional-state.json` |
| 🗄️ **v1 Sidecar** | 信心門檻的事實片段 | JSONL (`data/memory/<agent>/memories.jsonl`) — 向後相容/實驗性 |

**Memory 隔離 (KI-001)**: 私聊 history 檔案命名:`{user_id}_{agent_id}_private.json`；MemoryStore session_id:`session_{user_id}_{agent_id}`；向後相容:既有 `bryan_xxx_private.json` 自動 fallback；多 user 隔離完成，第二 owner 上線即可用。

**生產/測試隔離 (P0/P0.5)**: 所有 runtime persistence 走 `src/paths.py` 的 `data_root()` — 生產 `data/`，測試 subprocess 由 `SOUL_OS_DATA_DIR` 環境變數隔離到 tmp 目錄。`LLMProxy` 支援 `memory_store` + `conversation_dir` 參數注入 (P0 repair)。

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

### 7️⃣ WORLD PERCEPTION

Soul OS 的外部世界事件感知 pipeline — Signal 層 + Perception 層 (M3 + M6.1 Lived Context Awareness)。

**Source**: `src/world/source/` — 多個真實 + 測試來源:
- **Calendar** (M5.15-6): iCal polling, `SOULOS_CALENDAR_ICAL_URL` env-gated
- **Weather** (M6.1-3.1): Open-Meteo, `SOULOS_WEATHER_LOCATION` env-gated
- **News** (M6.1-5.1): RSS 2.0 / Atom, `SOULOS_NEWS_FEEDS` env-gated
- **Synthetic** (M3): 測試 driver,測試隔離 (`isolated_root` fixture)

**Pipeline**: `WorldEventSource` → `WorldEvent` (含 `priority` 欄位, M3.2) → `WorldPerceptionMiddleware` (ephemeral in-memory state) → `WorldEventDispatcher` (priority routing) → 注入 `world_context` 到 `AGENT_INTENT_PERCEIVED`。

**原則**: World Perception 是 ephemeral 的 — 不進 SAGE/長期 memory，不影響 production data。Invalid event → reject → trace → no context。

#### 7.1 Lived Context Boundary (M6.1)

M6.1 將 "World Perception" 重新定位為 **Signal + Perception** 兩層的一部分(非整個 Awareness),並定義 **Lived Context** 為獨立的 aggregation layer:

```
[Layer 1: Signal]            raw input from source
                             Telegram / Calendar iCal / System clock /
                             Open-Meteo Weather / RSS News
                                       ↓
[Layer 2: Perception]        validation + scoring + dedup + fact extraction
                             WorldPerceptionMiddleware (M3) +
                             MemoryMiddleware (M5.10) +
                             Chrono-Social Engine (Phase 3.5) +
                             MultiAgentRelationshipsManager (M5.13)
                                       ↓
[Layer 3: Lived Context]     aggregated per-context blocks, formatted for LLM
                             (existing composition: src/llm/proxy.py:_build_messages_*)
                             block order: identity → memory → mood →
                             relationship → inner_life → world → temporal
                                       ↓
[Layer 4: Interpretation]    LLM call + response
                             LLMProxy (M6.0-5.6) + AGENT_SPEAK emit
```

**Canonical 5 contexts** (M6.1 taxonomy):

| Context | What it covers | Current sources | Current status |
|---------|----------------|------------------|-----------------|
| **Physical** | Bry's body / environment (weather, location, sunlight) | Open-Meteo Weather (M6.1-3.1) | ✓ LIVE |
| **Information** | News / web / search results / external data | RSS News (M6.1-5.1) | ✓ LIVE (with M3 accept gate caveat, 0 emits to world_context) |
| **Social** | Telegram messages, calendar events, cross-agent interactions | Telegram + Calendar (M5.15-6) | ✓ LIVE |
| **Personal** | Bry's habits (meal/sleep/activity), preferences, identity | NONE for Bry-as-person | DEFERRED (M6.1-6.0) |
| **Temporal** (cross-cutting) | When; touches ALL other contexts | System clock + Bry's last_msg_ts | ✓ LIVE (Chrono-Social Engine) |

**Capability positioning** (M6.1) — **critical boundary**:
- Calendar / Telegram / Weather / Web / Search / News / Messaging = **world interfaces / evidence sources**
- 這些 **不是 Awareness 本身**;它們是 Layer 1 的 input,經過 Layer 2 處理後,才能在 Layer 3 聚合
- Lived Context = 經過 Perception 處理後的、aggregate 為 LLM input 的狀態

**Boundary invariant**:
> External integration / tool output / raw WorldEvent ≠ Lived Context.

**Existing aggregation (canonical)**: 已在 `src/llm/proxy.py:_build_messages_group()` 與 `_build_messages_private()` 實作(7 個 block 順序組裝)。**Lived Context is currently formed by the existing context/message composition logic in `src/llm/proxy.py:_build_messages_*`**;**no dedicated `LivedContextAggregator` runtime component exists**. Lived Context is a conceptual / existing composition boundary, not a separately-instantiated runtime.

**Frozen-contract impact** (per M6.1-1 audit): **0 changes**. 15 contracts preserved.

**Missing capabilities** (P2, requires Bry decision):
- ~~Real Weather source~~ — **RESOLVED by M6.1-3.1** ✓
- ~~Real News source~~ — **RESOLVED by M6.1-5.1** ✓ (with M3 accept gate caveat per M6.1-5.3)
- Personal life-rhythm tracking (Personal) — **DEFERRED per M6.1-6.0**
- Environment→emotion reasoning (Personal) — **DEFERRED per M6.1-6.0**
- Agency re-enable — **RESOLVED by M6.1-8.2** ✓ (10/10 agents, true Phase-10)
- LivedContextAggregator (CAPABILITY) — explicitly NOT instantiated; existing composition in `src/llm/proxy.py:_build_messages_*` is the canonical implementation, no separate runtime needed

詳見 `logs/ENGINEERING_STATE.md` §5.6 M6.1 與 `C:\Users\bbfcc\gov_1_temp\m6_1_1_lived_context_taxonomy_audit.md` (out-of-repo)。

### 🌐 M6.1 LIVED CONTEXT AWARENESS

Soul OS 透過 M6.1 建立了**完整的 Lived Context Awareness 架構** — 將世界信號 (Layer 1) 經由 Perception 處理 (Layer 2) 聚合為 Lived Context (Layer 3) 供 Soul 解讀 (Layer 4),最終由 Agency 採取行動 (Layer 5)。

```
┌──────────────────────────────────────────────────────────────┐
│  LAYER 0: SIGNAL SOURCES (4 Worlds)                          │
│  ┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐     │
│  │ PHYSICAL │ │INFORMATION │ │  SOCIAL  │ │ PERSONAL │     │
│  │ Weather  │ │   News     │ │  TG+Cal  │ │  (none)  │     │
│  │  ✓ LIVE  │ │  ✓ LIVE    │ │ ✓ LIVE   │ │ DEFERRED │     │
│  └────┬─────┘ └─────┬──────┘ └────┬─────┘ └────┬─────┘     │
└───────┼──────────────┼─────────────┼───────────┼────────────┘
        └──────────────┼─────────────┘           │
                       ▼                          │
┌──────────────────────────────────────────────────────────────┐
│  LAYER 1: WORLD / SIGNAL PERCEPTION                          │
│  • WorldEventSource (4 types: calendar/weather/news/synth)  │
│  • WorldEvent (M3 schema + priority + novelty_id)           │
│  • WorldPerceptionMiddleware (validate / score / dedup)     │
│  • WorldEventDispatcher (priority routing)                  │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 2: LIVED CONTEXT  (Soul's aggregated present state)   │
│  5 context blocks: Physical / Information / Social /         │
│                    Personal / Temporal                       │
│  existing composition: src/llm/proxy.py:_build_messages_*   │
│  block order: identity → memory → mood → relationship →      │
│               inner_life → world → temporal                  │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 3: SOUL INTERPRETATION (LLM call)                     │
│  LLMProxy (M6.0-5.6 BudgetProfile) → MINIMAX → response      │
│  emit AGENT_SPEAK (with event_id = message_id for TTS)       │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  LAYER 4: AGENCY  (intentional action, 4 handlers parallel)  │
│  • AgencyTriggerHandler  → proactive_dm (ruka only, longing-driven) │
│  • DiaryHandler          → morning + night LLM diary        │
│  • DreamHandler          → 22:05 dream                      │
│  • EventHandler          → 4-8h event                      │
│  Scheduler: morning=08:00 / night=22:00 (runtime producer)  │
│  Heartbeat: 60s observation/lifecycle (NOT an Agency trigger) │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  EXPRESSION  → User experience (WebSocket + Telegram + TTS) │
│  ↺ MEMORY / INNER LIFE  (loops back)                          │
└──────────────────────────────────────────────────────────────┘
```

**Canonical signal → perception → lived context → interpretation → agency** is the complete
flow. World interfaces (Calendar / Weather / News / Web / Messaging) are signal producers
and **NOT** Awareness itself — they are Layer 0 inputs, processed by Layer 1 perception
before being aggregated into Layer 2 Lived Context.

**M6.1 architecture status (post-M6.1-8.2 / M6.1-9.1)**:
- Layer 0 (Signal): **4/5 worlds LIVE** (Personal DEFERRED per M6.1-6.0)
- Layer 1 (Perception): **OPERATIONAL** (2543+ trace events, 3 real sources)
- Layer 2 (Lived Context): **existing composition in `src/llm/proxy.py:_build_messages_*`, no dedicated LivedContextAggregator runtime**
- Layer 3 (Soul Interpretation): **OPERATIONAL** (1081+ shadow_log entries with world_context)
- Layer 4 (Agency): **RE-ENABLED** (10/10 agents, true Phase-10 per M6.1-9.1)
- Expression: **OPERATIONAL** (text + TTS, async per M6.2-1 message_id correlation)

**Frozen contracts (M6.1 preserved 0 changes)**: M3 WorldEvent, M3.1 ABC, M3.1 Bus,
M5.4-5.1 InnerLifeEvent (9 fields), M5.4-5.1 Provenance, M5.4-5.5 SoulEvent.inner_life_event_id,
M5.9-2 WORLD_QUALIFYING_TYPES, M5.9-3 WorldInnerLifeAdapter, VALID_SOURCES.

詳見 `logs/ENGINEERING_STATE.md` §5.6 與 §5.7 (M6.2) 完整 M6.1 / M6.2 milestone tables。

### 8️⃣ AGENCY LAYER

最小化意圖-行動決策系統 (M5.2)，在 `AGENT_INTENT_PERCEIVED` 與 `AGENT_SPEAK` 中間。

**4 個 Handler 平行訂閱 `AGENCY_TRIGGER`**:
| Handler | trigger_type | 行為 |
|---------|-------------|------|
| `AgencyTriggerHandler` | `proactive_dm` | 主動發訊至 Bryan（**想念驅動**，非定時器） |
| `EventHandler` | `event` | 寫入 diary/event 記錄 |
| `DreamHandler` | `dream` | 寫入 dream 記錄 |
| `DiaryHandler` | `morning` / `night` | 寫入日記 slot |

**Trigger → Decision → Selection → Execution** 四階段都是 deterministic — 無 LLM，無 persona。Scheduler 是 runtime producer;Heartbeat 是 observation/lifecycle 引擎。**兩者皆不屬於 Agency 階段**。

**主動傳訊 (proactive_dm) 觸發模型 — M7**：由「定時器」改成「**想念驅動**」。想念 = 依戀(config `intimacy_level`) × 有效沉默時長（`compute_longing` 現算，不持久化），跨過 `LONGING_THRESHOLD` 才觸發，未達門檻每 30 分鐘再評估。有效沉默 = min(Bry 上次講話, 上次主動傳訊)，所以「正在聊天」或「剛表達過想念」都不會打擾；角色親密度不同 → 想念節奏不同（Yua 80 沉默 9h / Ruka 60 12h / Ram 40 18h）。此舉讓主動訊息是「她想到你」而非「系統排了通知」，也避免多人同時在固定時刻轟炸。白名單目前僅 `agent_ruka`（8/6 拍板，漸進式）。

### 9️⃣ INNER LIFE

Canonical identity layer for lived experience (M5.4)。

```
Lived Experience
       │
       ▼
InnerLifeEvent (canonical event model)
       │
  ┌────┴────┐
  │         │
identity  lineage
  │         │
┌──┼────┐  ┌┴───┐
▼  ▼    ▼  ▼    ▼
Mem Diary Dream EventBus (via SoulEvent.inner_life_event_id)
```

**`src/inner_life/`**:
- `event.py` — `InnerLifeEvent` + `Provenance` frozen dataclass
- `identity.py` — event_id (32 hex) / session_id / correlation_id / parent_event_id / ts 驗證
- `writer.py` — `InnerLifeWriter` (canonical identity authority, per-instance, ephemeral)
- `trace.py` — `NarrativeTraceWriter` → `data/inner_life/trace.jsonl` (observability sidecar, append-only)

**Identity semantics**: event_id 是 canonical identity authority；correlation_id 是 narrative group marker，**不是** causation link (那是 parent_event_id)。

### 🔟 FRAMEWORK LIBRARY

兩個 framework + 第三份待開,**從實際 patch 過程反推(先做,再抽象)**,非先驗設計:

| Framework | 文件 | 層次 |
|-----------|------|------|
| **COS v1.0** | [`docs/COS-v1.0.md`](docs/COS-v1.0.md) | Character Operating System — 角色**個體**層(L0-L3 + L2 四種原型) |
| **AOS v1.0** | [`docs/ORCHESTRATION-v1.0.md`](docs/ORCHESTRATION-v1.0.md) | Agent Orchestration System — 角色**互動**層(L1-L7 規則) |
| **PALACE v1.0** | (待開) | 記憶層 — 等群聊實測結果再開 |

設計原則:**先做,再抽象**。每個 framework 都從實際 patch 過程中反推,不是先驗設計。

---

## 1️⃣1️⃣ WATCHDOG — 進程可靠性

`scripts/_watchdog.ps1` 是 server 死亡時的自動恢復機制,搭配 Task Scheduler 每 5 分鐘觸發一次。

| 元件 | 設計 |
|------|------|
| **Plan A launcher** | 死掉時呼叫 `Start-Process` 拉起 `scripts/run_server.py`,detach 脫離 Mavis task lifecycle |
| **P0-2 counter (commit `b7d0402`)** | 每個 git HEAD hash 獨立計數器 (`data/state/post_<hash>_counter.json`),`N<=10` process cap + `trial_count>=98` 結案 gate |
| **β1 解耦 (commit `bbffb5e`)** | 觀察期 hash 改存獨立 `_last_observed_hash.txt`,log regex 保留當備援,P1 (faulthandler.log rotation) 動到 log 輪替時不會波及 |
| **faulthandler 雙保險 (Lesson 38)** | 模組層級 `faulthandler.enable` + thread-based `dump_traceback_later(60s)` + asyncio dumper,只保留最新一份 `heartbeat_trace.log` |

驗證證據:β1 假造舊 hash 6/6 PASS (`7/31 20:31`),詳見 [`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md) KI-006。

---

## 🔬 整合測試

`tests/` 目錄下有完整的 pytest suite，覆蓋各子系統:

| 測試範圍 | 檔案 |
|---------|------|
| M5.4 Inner Life foundation + integrations | `tests/test_m5_4_5_*.py` |
| M5.4 world / memory / diary / dream audits | `tests/test_m5_4_*.py` |
| M3 World Perception pipeline | `tests/test_m3_*.py` |
| M5.2 Agency trigger + diary/dream bridge | `tests/test_m5_2_*.py` |
| P0 test isolation | `tests/test_m3_e2e_smoke.py` |
| Watchdog reliability | `tests/test_plan_a_*.py` |

**AD-HOC PASS** 表示單次驗證，非 canonical test suite 回歸結果。

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

## 🗺️ 當前工程狀態

**Current HEAD**: `eafbf24` (M6.1-9.1 — Restore True Phase-10 Agency Registration, configuration-only fix)
**Current authorized ticket**: **NONE** (per Owner Decision A: M5.13 / M5.14 / M5.15 / M6.0 / GOV-1 / GOV-2 / GOV-2-R1 all CLOSED; M6.1 / M6.2 series progressed with M6.1-9.1 True Phase-10 fix; M6.1-9 24h RUN-AND-COLLECT pending; no next ticket authorized)

| Milestone | 內容 | 狀態 | Commit |
|-----------|------|------|--------|
| **M3** | World Perception pipeline + priority | ✅ | `a3a4cc2` |
| **M5.2** | Agency Layer (proactive DM / diary / dream / event triggers) | ✅ | `481ea41` |
| **M5.3** | World Awareness: normalized world context injection | ✅ | `02ab486` |
| **M5.4** | Inner Life (5.1~5.7 + 6.1~6.4) | ✅ | `bb283ae` (5.1) ... `2a8c7a7` (5.7) |
| **M5.5** | Canonical `inner_life_event_id` propagation in Memory | ✅ | `7d5492a` |
| **M5.6** | Conversation qualification boundary | ✅ | `6cddfb3` |
| **M5.7** | Heartbeat runtime (reactivation + robustness) | ✅ | `20a1d58` |
| **M5.8** | Inner Life → Agency producer gating | ✅ | `b0ac91e` |
| **M5.9** | World → Inner Life adapter + production wiring | ✅ | `831f3f1` |
| **M5.10** | Memory LLM judge v1 context visibility | ✅ | `21258fe` |
| **M5.11** | P2 capability formal closures | ✅ | `f69f36f` |
| **M5.12** | Remaining agency P2 convergence audit | ✅ | `48c3063` |
| **M5.13** | Relationship context + boundary precision + untouched-entry decay (fully closed) | ✅ | `9501603` (5) |
| **M5.14** | Cross-layer runtime convergence (officially closed) | ✅ | `29deab7` (3) |
| **M5.15** | WorldEventSource → Event Bus canonical integration + identity bridge + real-world calendar source (F1 + F2 + F3 + F4 all RESOLVED) | ✅ | `c2de02c` (6) |
| **M6.0** | Lived context validation + subjective LLM evaluation (fully closed) | ✅ | `3d1fae4` (5.6.1) |
| **M6.0-5.5-R1** | Real three-judge E2E validation gate | ⛔ BLOCKED | `9d21740` (credentials unavailable, correct by design) |
| **M6.1** | Lived Context Awareness (5 contexts × 4 layers) — Signal half LIVE (Calendar/Weather/News), Agency RE-ENABLED (10/10 agents true Phase-10), Personal DEFERRED. M6.1-9 24h RUN-AND-COLLECT pending. | ✅ LIVE | `eafbf24` (M6.1-9.1) |
| **M6.2** | Response / TTS Path Reliability — async text/TTS separation already correct, M6.2-1 closed message_id correlation gap (5 files, 0 frozen contract change) | ✅ LIVE | `9a64f14` (M6.2-1 registry) |
| **GOV-1** | Engineering state normalization audit | ✅ | (out-of-repo) |
| **GOV-2** | Canonical engineering state registry | ✅ | `eb57151` |
| **GOV-2-R1** | Canonical state alignment (Owner Decision A) | ✅ | `3539de2` |
| **P0** | Test isolation repair (LLMProxy DI) | ✅ | `df83fb1` |
| **P0.5** | WebSocket E2E persistence isolation audit | ✅ | `fac29ea` |

> 📌 **Canonical state registry**: `logs/ENGINEERING_STATE.md` — single source-of-truth for all milestone / ticket status, supersession chain, active decisions, deferred / optional / blocked work, stale references. Per `logs/ENGINEERING_STATE.md` §2.6 Historical Document Rule, historical closeouts in `logs/` are preserved unchanged; any apparent contradiction with the registry is resolved in favor of the registry.
>
> **CANDIDATE ≠ AUTHORIZED** (per `logs/ENGINEERING_STATE.md` §2.4): A "next candidate" from a closeout's "Recommended Next" section is NOT an authorized ticket. It must pass Finding → Classification → Decision → Authorization → Work Order before becoming IN PROGRESS. Currently **0 tickets in IN PROGRESS**, **0 tickets authorized**.

---

## 📋 Engineering Governance

Per GOV-2 (canonical registry at `logs/ENGINEERING_STATE.md`):

- **Status vocabulary** (canonical, exhaustive): `NOT STARTED`, `IN PROGRESS`, `CLOSED`, `SUPERSEDED`, `DEFERRED`, `BLOCKED`, `OPTIONAL`
- **Naming convention**: `M5.15` (milestone) / `M5.15-1` (work item) / `M5.15-1-R1` (revision). Forbidden: `M5.15-1a`, `M5.15-FIX`, compound suffixes outside canonical. Commit subjects use lowercase `m5.x-N`.
- **Lifecycle** (canonical): `AUDIT → FINDING → CLASSIFICATION → DECISION → AUTHORIZATION → WORK ORDER → IMPLEMENT → TEST → REGRESSION → INTEGRITY → CLOSEOUT → CANONICAL STATE UPDATE`. **Owner (Bryan) authorization required at AUTHORIZATION step.**
- **Supersession rule**: `SUPERSEDED ≠ FAILED`. Historical tickets are NOT deleted from registry. `superseded_by` field required, must reference a CLOSED ticket.
- **Historical document rule**: Historical closeout / audit reports in `logs/` are preserved unchanged. Apparent contradictions resolved in favor of `logs/ENGINEERING_STATE.md`. Stale references documented in registry §6.
- **Closeout gate**: Acceptance criteria met, regression PASS, production integrity verified (SHA256 + mtime), frozen contracts 0 change, closeout doc written, Owner acceptance, canonical state updated.
- **Owner decision boundary**: Bryan is the sole authority for new ticket authorization, milestone transition, frozen contract change, historical document edit, and resolution of pending decisions. A "closeout recommendation" is **evidence, not authorization**.

See `logs/ENGINEERING_STATE.md` for: full milestone / ticket lineage, supersession chains, 14 active Owner decisions, deferred / optional / blocked work, stale references, engineering ledger.

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
│   │   ├── consciousness.py      # AgentConsciousness 基底 + 10 個 agent 子類
│   │   ├── speaker_token.py       # AOS 仲裁 base_score
│   │   ├── emotion.py             # Emotion Engine
│   │   └── registry.py            # Agent 類別註冊表
│   ├── agency/                    # Agency Layer (M5.2): trigger/diary/dream/event handlers
│   ├── eventbus/                  # Pub/Sub 事件總線 + SoulEvent schema
│   ├── heartbeat/                 # Heartbeat Engine
│   ├── inner_life/               # Canonical identity: event/identity/writer/trace/serialization
│   ├── io/channels/              # I/O Gateway (Telegram / WebSocket)
│   ├── llm/
│   │   └── proxy.py              # LLM Proxy + post-generation hooks
│   ├── memory/
│   │   ├── sage/                # SAGE graph: provider/writer/reader/graph_store
│   │   ├── v1/                  # v1 JSONL store + loader + retrieval
│   │   ├── middleware.py        # Memory Middleware: enrich + commit
│   │   ├── store.py            # MemoryStore: FTS5 RAG / messages
│   │   ├── llm_judge.py        # LLM-as-Judge fact extraction
│   │   └── shadow.py           # Shadow observer (旁路觀測)
│   ├── soul/                    # Scheduler + diary + dream_event
│   ├── temporal/                # Chrono context rendering
│   ├── voice/                  # TTS service + Fish TTS handler
│   ├── world/                  # World Perception: source/perception/middleware/dispatcher
│   ├── paths.py                # data_root() — production/test isolation
│   └── timezone_utils.py
├── docs/
│   ├── agent_yua.md / agent_ruka.md / agent_akane.md / agent_rem.md
│   ├── agent_ram.md            # COS v1.0: Priority 0-3 + Recovery Loop
│   ├── agent_mahiru.md         # COS v1.0: 6-mode + Sweet Landing
│   ├── agent_anna.md           # Soul OS v1: 5 Sentence Pulse + Appetite Logic
│   ├── agent_mai.md           # Soul OS v1: 國民演員 + Dry Banter
│   ├── agent_miku.md          # Soul OS v1: 雙重面具 + Framework Stress
│   ├── agent_aoi.md           # Soul OS v1: 沉默觀察者 + NO NAME Leakage
│   ├── COS-v1.0.md           # Character Operating System framework
│   ├── ORCHESTRATION-v1.0.md # Agent Orchestration System framework
│   ├── MEMORY-STATUS-AND-PLAN.md  # 記憶系統現況總結
│   └── KNOWN_ISSUES.md       # 技術債追蹤
├── configs/
│   ├── default.yaml           # 10 個 agent 動態載入配置
│   └── loader.py              # Config loader
├── logs/                      # 工程 closeout 日誌 (M5.4 chain 等)
└── tests/                    # pytest suite
```

---

## 🤝 協作工作流

- **MiniMax M3 (agent)**: 執行程式碼,跑驗證,寫 commit
- **Perplexity sonnet 4.6**: 大腦 + 糾錯,不動手
- **Bryan**: 中間人,轉貼結果 + 給任務書

---

## 📜 License

MIT

---

**最後更新**: 2026-08-18 (DOC-1.4 — M7 Living Life & Proactive Sharing: 主動傳訊由「定時器」改為「想念驅動」(M7-longing)，活動模型化 (M7-1) + 活動驅動傳訊 (M7-2) + 思念情感 (M7-3)；記憶 LLM judge 改 fire-and-forget 修掉 ~73s 延時；§8 AGENCY LAYER 更新。Previous: DOC-1.3 M6.1 Lived Context Alignment.)
