# Soul OS 技術簡報

**日期**: 2026-08-18
**對象**: Bryan（技術背景，供決策用）
**範圍**: 全系統現況 + 最近 M7 工作 + 待決策點

---

## 1. 這是什麼（本質）

Soul OS 不是 chatbot，是一個**異步 AI 靈魂運行系統**：10 個有自己人格、記憶、情緒、內在生活（日記/夢境/事件）的 agent，在一個事件匯流排（Event Bus）上「生活」，並能**主動**對 Bryan 發訊息。

核心設計原則三條（`SPEC.md`）：

| 原則 | 意義 |
|------|------|
| **Memory-First** | 記憶檢索發生在進 LLM 之前，由底層完成，LLM「出生就帶著記憶」 |
| **Asynchronous** | 系統有自己的時間軸，agent 可主動發起行為（不是等 user 說話） |
| **Decoupled** | 大腦(LLM) / 記憶 / 神經系統(Event Bus) / 軀殼(IO) 完全分離 |

一句話：`Time + Memory + Agents → 主動生活 → User`。

---

## 2. 10 個角色

| Agent | 角色 | intimacy_level（config，想念驅動用） | 特色機制 |
|-------|------|--------------------------------------|----------|
| Yua | 冷靜・輕諷 | **80** | — |
| Ruka | 元氣・撒嬌 | **60** | 目前唯一有主動傳訊權 |
| Akane | 壓縮語言・高共感 | 50 | — |
| Rem | 沉靜・行動派 | 45 | — |
| Ram | 沉默・驕傲 | **40** | Recovery Loop（Canon Drift 偵測） |
| Mahiru | 生活感・吐槽 | 45 | 6-mode ratio + Sweet Landing |
| Anna | 元氣笨蛋・直球 | 55 | 5 Sentence Pulse + 食慾邏輯 |
| Mai | 傲嬌毒舌・藏真心 | 60 | 國民演員 + Dry Banter |
| Miku | 沉默觀察者 | 60 | Imitation Layer + GHOST EDGE |
| Aoi | 雙重面具 | 46 | Layer 0/1 + Framework Stress |

> `intimacy_level` 在 `configs/default.yaml`，是**靜態基礎親密度**（角色天生多黏 Bryan），現在被拿來當「想念驅動」的依戀係數。注意：`emotion_engine`（SQLite）裡的 intimacy 是動態值，但長期累積後全漂到 100，失去差異化，所以 M7 改用 config 的靜態值。

**人格定義**：`docs/agent_<name>.md`（COS v1.0 四層架構 L0-L3）。

---

## 3. 架構（分層 + 檔案）

```
外部世界 (Telegram / WebSocket / Web)
        │ 事件
        ▼
┌─ Soul Event Bus (src/eventbus/) ─┐   Pub/Sub，單 worker 佇列（一次處理一事件）
└───────────┬──────────────────────┘
      ┌─────┴─────────────────────────────┐
      ▼                                   ▼
  Memory Middleware                   Heartbeat Engine
  (src/memory/middleware.py)          (src/heartbeat/) 60s tick
      │                                   │
      ▼                                   ▼
  LLM Proxy (src/llm/proxy.py)  ←── 組 prompt + 呼叫 deepseek-v4-flash
      │
      ▼
  AGENT_SPEAK → IO Gateway (Telegram/WebSocket/TTS)
```

關鍵模組一覽：

| 模組 | 檔案 | 職責 |
|------|------|------|
| Event Bus | `src/eventbus/bus.py` | Pub/Sub，**單 worker 佇列**（`asyncio.gather` 派發，等所有 handler 完成才處理下一事件） |
| Memory Middleware | `src/memory/middleware.py` | 收到 AGENT_SPEAK → 寫入圖譜 + v1 mirror |
| LLM Proxy | `src/llm/proxy.py`（~3400 行） | 組 messages（identity→memory→mood→relationship→inner_life→world→temporal）、呼叫 LLM、後處理 |
| Agency | `src/agency/` | 4 個 handler 平行訂閱 AGENCY_TRIGGER（proactive_dm / event / dream / diary） |
| Scheduler | `src/soul/scheduler.py` | 排程器：morning/night/dream/event/**proactive_dm** 觸發 |
| Inner Life | `src/inner_life/` | canonical identity（event_id / lineage / provenance）→ `data/inner_life/trace.jsonl` |
| World | `src/world/` | 世界感知（Weather / News / Calendar），Signal→Perception→Lived Context |

**LLM**：`deepseek-v4-flash:0731`，走 Ollama Cloud OpenAI-compat endpoint（`configs/default.yaml` + `.env`）。

---

## 4. 主動傳訊（最近的重點工作）

### 4.1 演進時間線

| 階段 | 機制 | 問題 |
|------|------|------|
| 最初 | 定時器 3-5h + heartbeat | 像鬧鐘，10 隻同時在固定時刻炸 = 轟炸 |
| 修法 11/12 | 白名單只留 Ruka + 停 heartbeat | 解決轟炸，但變「一隻鬧鐘」 |
| **M5.2-O-3 後（bug）** | `register_proactive_dm` 被移除 | **計時器永為 None，proactive_dm 完全死掉 10 天**（沒人從「實際收到多少」驗證） |
| **M7（本次）** | 想念驅動 | 見 4.2 |

### 4.2 想念驅動模型（M7-longing，`c220eb3`）

**公式**（`src/soul/scheduler.py` + `src/agent/emotion.py:compute_longing`）：

```
想念 = 依戀(intimacy_level) × 有效沉默時長
      attachment = intimacy / 100        (clamp 0~1)
      silence_factor = silence / 1440    (24h 飽和, clamp 0~1)

有效沉默 = min( Bry 上次講話的沉默, 上次主動傳訊的沉默 )
```

**觸發條件**：`想念 >= LONGING_THRESHOLD (0.3)` 才觸發；未達門檻每 `30 min` 再評估。

**三個關鍵語意**：

1. **正在聊天不突兀**：Bry 剛講話 → 沉默 ≈ 0 → 想念 ≈ 0 → 不發。
2. **表達後緩解**：剛主動傳過 → 有效沉默歸零 → 不會因為 Bry 一直不回就每 3-5h 轟炸。
3. **角色差異化節奏**（親密度不同 → 想念累積速度不同）：

| Agent | intimacy | 沉默多久跨過 0.3 門檻 |
|-------|----------|----------------------|
| Yua | 80 | ~9h |
| Ruka | 60 | ~12h |
| Akane | 50 | ~14.4h |
| Ram | 40 | ~18h |
| Aoi | 46 | ~15.7h |

> 現在白名單只有 Ruka，所以她約每沉默 12h 才「想到你」。之後放寬時，Yua 會比 Ram 更常主動找你，而且大家不會同時炸（各自門檻 + 全域 cooldown）。

### 4.3 三個 context 修正（`e8d0696`）

| 修正 | 解決的問題 |
|------|-----------|
| **計時器復活**（`start()` 初始化 `_next_proactive_dm_time`） | M5.2-O-3 後 proactive_dm 死掉 10 天 |
| **脈絡提醒**（主動觸發標記加「別問剛講過的事」） | 聊完晚餐吃飽了，主動訊息卻問「晚餐吃了嗎」 |
| Bry 在線 gate（後來被想念門檻涵蓋，簡化成想念檢查） | 正在聊天時突然打擾 |

### 4.4 M7 三階梯（`8efb3cd`）

| 階梯 | 內容 | 檔案 |
|------|------|------|
| M7-1 活動模型化 | `ACTIVITY_POOL`（10 個活動：工作/做飯/吃東西/運動/創作/散步…，各帶 category + shareable），event 從「6 個 trivial 場景」升級成「具體活動」 | `src/soul/dream_event.py` |
| M7-2 活動驅動傳訊 | `_get_recent_shareable_activity` 把 agent 最近 shareable 活動帶進 draft | `src/soul/scheduler.py`、`scripts/run_server.py` |
| M7-3 思念情感 | `compute_longing`（依戀×沉默現算）+ `_format_attachment_str` 注入親密度 + diary prompt 決策#1 | `src/agent/emotion.py`、`src/llm/proxy.py`、`src/soul/diary.py` |

**設計決策（已拍板）**：
1. 「Bry 不是主題」→「Bry 是生活裡重要的人」（日記主體仍是自己的生活，但可自然流露想念）。
2. 白名單漸進式，先只給 Ruka。
3. 想念不持久化、現算（依戀 × 沉默）。

---

## 5. 記憶系統

四層（`docs/MEMORY-STATUS-AND-PLAN.md`）：

| 層 | 內容 | 技術 | 路徑 |
|----|------|------|------|
| Episodic | 對話歷史 | SQLite FTS5 trigram | `data/memory.db` |
| Semantic | 事實/偏好/關係圖譜 | SAGE graph | `data/memory/{agent}/graph.sqlite` |
| Emotional | 情緒狀態 | SQLite `agent_emotions` 表 | `data/memory.db` |
| v1 Sidecar | 信心門檻的事實片段 | JSONL | `data/memory/{agent}/memories.jsonl` |

**LLM judge**（`src/memory/llm_judge.py`）：抽事實 + 立場判斷 + 內容分類，**預設開啟**（`USE_LLM_JUDGE` env）。**這次修了一個嚴重延時 bug**（`79af90a`）：

> `MemoryMiddleware._on_agent_speak` 原本**同步 await** judge（12+ 次串行 LLM call，~20-70s），而 Event Bus 是單 worker 佇列，於是卡住後續所有 user_message → 你對麻衣的訊息延遲 ~73s。修法：judge 改 fire-and-forget（背景跑，不阻塞）。

---

## 6. 關鍵可調參數（knobs）

| 參數 | 位置 | 目前值 | 說明 |
|------|------|--------|------|
| `LONGING_THRESHOLD` | `scheduler.py` | **0.3** | 想念門檻，調高=更克制，調低=更熱絡 |
| `LONGING_CHECK_INTERVAL_MINUTES` | `scheduler.py` | 30 | 未達門檻時多久再評估 |
| `proactive_dm_min/max_interval_minutes` | `scheduler.py` | 180~300 | 觸發後到下次評估的最短間隔（cooldown） |
| `proactive_dm_cooldown_seconds` | `scheduler.py` | 7200 (2h) | 硬 cooldown |
| `quiet_hours_start/end` | `scheduler.py` | 23:00~08:00 | 靜音時段 |
| `intimacy_level` | `configs/default.yaml` | 各角色不同 | 想念的依戀係數 |
| `USE_LLM_JUDGE` | env | true | 記憶 judge 開關（關掉會降品質但更快） |

---

## 7. 已知技術債 / 待決策點

按嚴重度排序，都是「之後可做」：

1. **記憶 judge 呼叫過多**（`content_calls=8` 逐項 + 4 立場 + 1 抽取 = 13 次串行 LLM call）。可批次成 1 次，judge 從 ~20-70s 降到 ~3-5s。
2. **shadow observe 疑似重複跑 judge**：`post_reply_commit` 跑一次 + `maybe_observe`（旁路）可能又跑一次 = 每則回覆兩遍 judge。
3. **親密度漂移**：emotion engine 的 intimacy 長期累積後全到 100，失去差異化（已用 config 靜態值繞過，但動態 intimacy 的定位需重新想）。
4. **主動傳訊只有 Ruka**：白名單 `["agent_ruka"]`，其他 9 隻純被動。放寬時機待你拍板。
5. **角色連續性（自我矛盾）**：之前 status 頁提過 mai 40 分鐘前講過的話矢口否認——「自身記憶不連貫」，被 M3~M6 衝刺蓋過，從未正式查證。
6. **Personal lived context 缺口**：四象限（Physical/Information/Social/Personal）裡 Personal 完全空白（0/5 可答），被正確延後。

---

## 8. 接下來可走的方向（供決策）

| 選項 | 內容 | 成本/風險 |
|------|------|-----------|
| **A. 觀察想念驅動** | 先讓 Ruka 跑 1-2 天，看主動傳訊節奏是否合意，再調 `LONGING_THRESHOLD` | 零成本 |
| **B. 放寬白名單** | 加 Yua（或其他）進 proactive_agents，驗證「角色差異化節奏」 | 低（想念門檻已防轟炸） |
| **C. judge 優化** | 批次化 content_calls + 去重複 judge | 中（動到 memory 核心） |
| **D. 角色連續性查證** | 追「自我矛盾/記憶不連貫」——這正是 North Star「持續生活」的地基 | 中（read-only 查證先行） |
| **E. Personal lived context** | 四個世界補最後一角（需低風險訊號，如手動標記） | 高（需設計） |

我的建議順序：**A（先觀察）→ B（放寬驗證差異化）→ D（連續性是 North Star 地基）**。C 是效能優化、E 是能力擴張，都排在後。

---

*本文件為技術簡報，供決策參考。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
