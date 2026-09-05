# Soul OS Harness（異步 AI 靈魂運作系統）

[![Branch](https://img.shields.io/badge/branch-main-blue?style=for-the-badge)]()
[![Phase](https://img.shields.io/badge/Phase-C_3.1_Active-2ecc71?style=for-the-badge)]()
[![Invariants](https://img.shields.io/badge/Invariants-Volition_Gate_%7C_Identity_Firewall-ff6b9d?style=for-the-badge)]()
[![Agents](https://img.shields.io/badge/Agents-10_defined-ff6b9d?style=for-the-badge)]()
[![Frameworks](https://img.shields.io/badge/COS_v1.0_+_AOS_v1.0-framework-2ecc71?style=for-the-badge)]()

專為異質 AI 靈魂設計的異步、Memory-First、事件驅動的 Agent 運作系統（Character Operating System, COS）。具備時間感知（Temporal Heartbeat）、自主意向（Autonomous Goals）、多模態感官（Multimodal Perception）與多靈魂共生演化機制。

---

## 核心架構與設計哲學

1. **Strategic North Star**：非被動客服（World happened → Soul perceived → Soul interpreted → Soul decided → Soul acted）；單次行動不變量（1 Heartbeat 1 Step，禁 ReAct 狂飆）；現象學時間（主觀張力：無感 / 牽掛 / 釋然，拒絕機械打分公式）。
2. **Memory-First 雙時序記憶（MR 系列 & SE-5）**：SAGE Lite 雙時序記憶圖譜（MR 系列 Schema v7 遷移：valid_from / invalidated_at 時序列、時間旅行查詢、軟刪除）＋ Schema v8 goals 表（目標引擎專用遷移，獨立表）；Mem0 顯式原語（add_fact / update_fact / delete_fact / resolve_conflict）；昇華四態生命週期 ACTIVE ➔ WEAKENING ➔ DORMANT ➔ SUPERSEDED（遺忘＝狀態遷移非硬刪除）。
3. **多體共存三大防線（SI 系列 & C-3 關係網）**：防線3 Identity Firewall（他者行為 100% EXTERNAL_OTHER_ACTION、0 內化、0 昇華）；防線2 Privacy Gate（1:1 私聊 100% 斷離廣播總線）；防線1 Ambient Perception Path（公共事件僅背景感知、0 廣播風暴）；他者心智關係帶（relationships.json Schema 4.2：stranger ➔ known ➔ familiar ➔ close 四帶整數狀態機、30 天現象學冷卻、B5 他者意向生成）。
4. **自主目標與意向引擎（TG & LS 系列）**：雙軸種子源（Bryan 羈絆軸＋自我軸）；No-Scoring 配額輪替（24h 節流 / 單心跳 ≤1 候選 / N=3 輪替 / 同軸 ≤2）；Work 狀態機（SUSPENDED 中斷與喚醒恢復）。
5. **多模態感官與設備層（MS 系列）**：標準 MCP 生態（STT faster-whisper / 視覺相機、5s 硬超時 Fail-closed）；語音互動閉環（三路分流 USER_MESSAGE / AMBIENT / DROP、喚醒門控、VAD 防抖、會話採集）。

---

## 模組結構（以 repo 實際目錄為準）

```
soul-os-harness/
├── src/
│   ├── agent/                      # 意識基類 AgentConsciousness / 情緒引擎 / 發言權仲裁（speaker_token）
│   ├── agency/                     # 自主行為：日記 / 做夢 / 身份防火牆 / 私聊門控
│   ├── eventbus/                   # Pub/Sub 事件總線與 SocialWorldEvent
│   ├── heartbeat/                  # 心跳驅動（Temporal Heartbeat）
│   ├── inner_life/                 # canonical identity：InnerLifeEvent / Provenance / 軌跡
│   ├── memory/                     # SAGE Lite 雙時序記憶（MR Schema v7）＋ Mem0 原語 ＋ FTS5
│   ├── soul/                       # scheduler / relationships / tool_registry.py ＋ actuator.py
│   │                               #   （標準 MCP 工具註冊表與 Volition Gate 執行器）
│   ├── goals/                      # 目標引擎 / 9 源種子生成器 / 配額輪替（Schema v8 goals 表）
│   ├── voice/                      # 語音互動路由 / 喚醒門控 / VAD 防抖 / 會話採集 / TTS
│   ├── world/                      # 環境感知（Signal → Perception）與客廳現況渲染
│   ├── temporal/                   # 現象學主觀時間座標
│   ├── social/                     # 社會擴散 / 感知聚合 / 關係帶狀態機
│   ├── conversation_qualification/ # 對話資格邊界
│   ├── io/channels/                # I/O Gateway（Telegram / WebSocket / 靈魂牆 Web UI）
│   ├── llm/                        # LLM Proxy 管道（post-generation hooks）
│   └── work/ + work_adapter/       # DSH Work 執行契約（最小執行 adapter）
├── tests/
│   ├── test_tl*.py                 # Time-lapse 驗收套件（TL-1/2/4/5/6/7，位於 tests/ 根目錄）
│   ├── harness/                    # TL-9 關係演化長程驗收 ＋ goal-driven / social diffusion harness
│   ├── goals/                      # 目標引擎 / TL-8 Volition 護欄 / SG-2 B5 種子
│   ├── tools/                      # 工具註冊表 / MCP 工具驗收
│   └── social/                     # 關係帶 / 關係存儲 / SG-2 護欄
├── docs/
│   ├── agent_*.md + COS-v1.0.md    # 角色設定與 COS 規範
│   ├── *-CONTRACT.md               # 架構契約（TG / LS / SG / MS / TOOLING-MCP / TEMPORAL ...）
│   ├── *-AUDIT.md / *-REVIEW.md    # 審計報告
│   └── KNOWN_ISSUES.md             # 技術債追蹤
└── logs/
    └── ENGINEERING_STATE.md        # 單一事實來源（milestone / ticket / decision 權威登記）
```

> 工具註冊表**不在** `src/tools/`——實際位於 `src/soul/tool_registry.py`（註冊表）＋ `src/soul/actuator.py`（Volition Gate 執行器）。

---

## 里程碑進度

Phase 狀態：階段 A、B、C-1、C-2、C-3 100% CLOSED；C-3.1 關係增強投遞與多體心智深化 **ACTIVE**。

- [x] **階段 A：靈魂深化** — SE-4 / SE-5 / TL-4 記憶昇華與溯源鏈；CA-3 / SM-4 系列 / TL-5 Volition 四元行動；TA-2 主觀心理時鐘
- [x] **階段 B：P0 基建與感官閉環** — MR 雙時序記憶；TS MCP 工具層標準化（真實 stdio 生產實證）；MS 多模態感官與定向語音互動；SI-2 / SI-3 / TL-7 多體防線與社交機會
- [x] **階段 C-1：自主目標與意向引擎**（TG 全線）
- [x] **階段 C-2：長期共生與生活節奏對齊**（LS 全線）
- [x] **階段 C-3：群體關係網與他者心智建模**（SG 系列 & TL-9 全線）
- [ ] **階段 C-3.1：關係增強投遞與多體心智深化**（ACTIVE）

---

## Canonical 狀態

工程狀態以 `logs/ENGINEERING_STATE.md` 為**單一事實來源**（milestone / ticket / Owner decision / frozen contract 權威登記）。本 README 僅作架構概覽，細節一律以 registry 為準。

## 快速開始

```bash
python scripts/run_server.py    # 啟動 server → http://localhost:8000（靈魂牆）
```

詳細契約見 `docs/` 各 `*-CONTRACT.md`；已知技術債見 `docs/KNOWN_ISSUES.md`。

## License

MIT