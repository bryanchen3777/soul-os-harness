# Soul OS 記憶系統 — 現況總結 + 施工書

**日期**: 2026-07-18  
**範圍**: `soul-os-harness` 記憶層（繼續施工用）  
**原則**: Memory-First · No Memory > Wrong Memory · 先做再抽象（PALACE v1.0 尚未成文）

---

## 一、一句話現況

記憶已經「能跑、能寫、能注入」，但是 **三套存儲 + 兩條寫入路徑 + 一條僅 Rem 的 v1 讀取** 疊在一起，**生產路徑未閉環**（Loader 讀不到 Writer 寫的位置；`format_for_prompt` 未 import）。下一步應先 **止血閉環**，再擴 agent、再談 PALACE 抽象。

---

## 二、架構總覽（As-Is）

```
USER_MESSAGE / AGENT_INTENT / AGENT_SPEAK
              │
              ▼
      MemoryMiddleware  (src/memory/middleware.py)
         │
         ├─ AGENT_INTENT
         │    ├─ SAGELiteProvider.prefetch()  → graph.sqlite 事實摘要
         │    ├─ [僅 agent_rem] v1 MemoryLoader  → JSONL tag 檢索 + 信心門檻
         │    └─ re-publish AGENT_INTENT_ENRICHED (+ memory_context)
         │
         ├─ AGENT_SPEAK
         │    ├─ SAGE post_reply_commit → Writer 抽 fact → graph.sqlite
         │    │         └─ (USE_LLM_JUDGE) LLM Judge → mirror → v1 JSONL
         │    └─ ShadowObserver（旁路 log，不進 prod）
         │
         └─ LLMProxy
              ├─ memory_context（SAGE + 可選 v1 block）
              ├─ MemoryStore.get_recent / FTS5 RAG（data/memory.db）
              └─ 對話 history JSON（private / group）
```

### 四層「實際落地」對照 README

| 概念層 | 實際技術 | 路徑 | 狀態 |
|--------|----------|------|------|
| Episodic / 對話 | SQLite `messages` + FTS5 trigram | `data/memory.db` | ✅ 主力；**KI-005 舊 session_id 未 migration** |
| Semantic / 圖譜 | SAGE-lite GraphStore | `data/memory/{agent}/graph.sqlite` | ✅ 各 agent 獨立；Ram **no-diary** 不寫 |
| Structured v1 | Append-only JSONL + Loader | 應為 `*_memories.jsonl` | ⚠️ **寫讀路徑不一致**；Loader 僅 Rem |
| Emotional | EmotionalState / persona | agents / consciousness | 部分；Ram value_history 未做（KI-003） |
| Shadow / Judge | LLM-as-Judge v6 + shadow log | `data/shadow/` | ✅ 旁路觀測；預設 7 天 |

---

## 三、模組地圖（工程師速查）

| 路徑 | 職責 |
|------|------|
| `src/memory/middleware.py` | Bus 訂閱、prefetch、enrich、commit、v1 Loader 閘門 |
| `src/memory/store.py` | 對話級 `MemoryStore`（FTS5 RAG / get_recent） |
| `src/memory/sage/*` | 圖譜：GraphStore / Writer / Reader / Evolution |
| `src/memory/llm_judge.py` + `judge_prompts/` | 抽 triple + stance/content 離散判定 |
| `src/memory/v1/*` | schema / store / retrieval / loader / log_exporter |
| `src/memory/shadow.py` | v6 vs heuristic 旁路對照 |
| `src/llm/proxy.py` | 吃 `memory_context` + MemoryStore RAG 拼 prompt |

### 事件契約（不可破壞）

1. `USER_MESSAGE` → 暫存 user_text  
2. `AGENT_INTENT` → Middleware enrich → **`AGENT_INTENT_ENRICHED`**（避免迴圈）  
3. `AGENT_SPEAK` → 配對 user_text → graph commit（5s cooldown / agent）  
4. LLMProxy **只訂閱 ENRICHED**，讀 `payload.memory_context`

### 設計鐵律（已寫進 code 註解）

- **No Memory > Wrong Memory**（Loader fail-safe：無 category/confidence / 低 conf → 不注入）  
- v1 不做：span attribution、LLM explain、semantic interpretation、diary 拆分  
- Shadow 不動 prod 寫入結果  
- Ram：`NO_DIARY_AGENTS` 跳過 graph 寫入  

---

## 四、資料現況快照（2026-07-18 本機）

### 4.1 `data/memory.db`（對話 / RAG）

- 約 **18,370** 條 messages  
- 主力 session：`session_bryan_agent_ram` (9028)、`rem` (3072)、`yua` (2994)、`group` (1817)  
- **KI-005 仍在**：舊鍵 `session_agent_yua` (185)、`session_agent_rem` (64) 等仍存在 → 新 `get_recent(session_bryan_…)` **讀不到**

### 4.2 SAGE graphs（`data/memory/*/graph.sqlite`）

| Agent | facts（約） | 備註 |
|-------|-------------|------|
| agent_ruka | 3093 | 最大 |
| agent_yua | 490 | |
| agent_akane | 361 | |
| agent_mai | 294 | |
| agent_mahiru | 273 | |
| agent_anna | 157 | |
| agent_rem | 25 | 偏少 |
| agent_ram | 0 | 設計：no-diary |
| agent_aoi / miku | 0 | 幾乎空 |

### 4.3 v1 / mirror JSONL

| 檔 | 角色 | 問題 |
|----|------|------|
| `data/memory/{agent}/assistant_memories.jsonl` 等 | 舊/旁路格式 | 與 V1Store 檔名約定不一致 |
| `data/memory_v1/agent_rem_memories.jsonl` | 實驗 seed（25 筆） | 不在 production Loader 路徑 |
| Writer mirror 目標 | `{graph_parent}/{subject_hint}_memories.jsonl` | 例：`data/memory/agent_rem/agent_rem_memories.jsonl` |
| Loader 讀取 | `{data_dir}/{agent_id}_memories.jsonl` | 例：`data/memory/agent_rem_memories.jsonl` |

**結論：Writer 與 Loader 預設路徑不在同一檔 → v1 注入長期 fail-safe / eligible=0。**

### 4.4 Loader trace（`data/memory/loader_trace.jsonl`）

近期 sample：`eligible_count: 0`，`fail_safe_triggered: all_rejected_low_confidence`（或 candidate 空）。  
與路徑錯位 + 舊 seed 無 category/confidence 一致。

---

## 五、已完成（記憶相關，2026-07-02 波次）

依 git log（Bry § 系列，Perplexity 拍板）：

| 階段 | Commit 主題 | 結果 |
|------|-------------|------|
| v1 骨架 | schema / store / retrieval / experiment | 實驗可跑 |
| LLM Judge | extract + stance/content 兩階段 + few-shot + trace | 預設 `USE_LLM_JUDGE=true` |
| Shadow | Bry §11 v6 旁路 7 天 | 不碰 prod |
| v1.1 + Loader | category/confidence、門檻、fail-safe | 僅 Rem 閘門 |
| 檢索強化 | tags 切詞、jieba、MIN_OVERLAP 分層、candidate 收緊 | §15–§25 |
| 接 production | §23 production 路徑接通 | **邏輯接上，路徑仍有洞** |
| 跨介面 | §28 WebSocket session_id 對齊 Telegram | 歷史跨 UI |

KI 狀態（記憶相關）：

| ID | 狀態 | 說明 |
|----|------|------|
| KI-001 | ✅ | multi-user session 隔離 |
| KI-005 | ⏳ P1 | 舊 `session_agent_*` 未 migration |
| KI-003 | ⏳ P3 | Ram value_history |
| （未編號） | 🐛 | v1 寫讀路徑不一致 |
| （未編號） | 🐛 | `format_for_prompt` 未 import |

---

## 六、核心問題清單（繼續做記憶前必對齊）

### P0 — 生產閉環斷裂

1. **V1 寫讀路徑不一致**  
   - Writer：`data/memory/{agent_id}/{subject_hint}_memories.jsonl`  
   - Loader：`data/memory/{agent_id}_memories.jsonl`  
2. **`format_for_prompt` 未 import**（middleware 一旦 eligible>0 會 `NameError`，被 try/except 吞掉 → 等於 Loader 永遠無效）  
3. **Loader 只開 `agent_rem`**，其餘 agent 只有 SAGE + MemoryStore RAG  

### P1 — 資料品質 / 可達性

4. **KI-005** 舊 session_id 303+ rows（本機仍可看到 legacy keys）  
5. Rem graph 只有 ~25 facts，相對 Ruka/Yua 極少 → 圖譜召回弱  
6. 多份 JSONL 命名（`assistant_memories` / `user_memories` / `agent_*_memories` / `memory_v1/`）無單一 source of truth  
7. Shadow 7 天窗：需確認是否已過期、要不要出 agreement 報告再決定是否升 v6 為主寫入  

### P2 — 產品化缺口

8. **PALACE v1.0 未寫**（README 明標待開）  
9. 群聊「全寫」社交記憶 + 5s cooldown：可能丟 commit、也可能污染 graph  
10. multi-user 下 v1 store 尚未 per-user 分片（目前 per-agent）  
11. KI-003 Ram value_history  

### 設計張力（決策用，不是 bug）

- SAGE（regex/LLM 抽 triple 進 graph）vs v1（category+confidence JSONL + 嚴格 fail-safe）— 雙軌是否長期並存？  
- 「全 agent 開 Loader」vs「先把 Rem 做成 gold path 再複製」  
- 中文分詞（jieba）+ tag overlap 是否足夠，或需要 embedding（SPEC 曾提 Phase 3 向量）  

---

## 七、施工書（建議執行順序）

> 目標：先讓 **Rem 一條完整記憶閉環可觀測、可驗證**，再橫向擴張。  
> 每一 Stage 結束必須有 **可跑的驗收** + **必要時新增 KI**。

---

### Stage 0 — 凍結基線（0.5 天）

**做什麼**

1. Backup：  
   - `data/memory.db`  
   - `data/memory/` 整樹  
   - `data/shadow/`（若還要分析）  
2. 記錄本文件日期的 stats（messages 數、各 graph facts、loader_trace 行數）  
3. 確認 env：`USE_LLM_JUDGE`、`SHADOW_MODE_ENABLED`、`INTERVAL` 無關  

**驗收**

- [ ] backup 檔案存在且可還原  
- [ ] `python -c "from src.memory.middleware import MemoryMiddleware"` 無 import 錯  

**不做**：改 schema、跑 migration  

---

### Stage 1 — 止血：v1 閉環（P0，0.5–1 天）**【下一刀】**

**1.1 統一 V1 路徑（單一真相）**

建議約定（寫進 code + 本文件）：

```
data/memory/{agent_id}/memories.jsonl
```

- Writer `_mirror_to_v1_store`：固定寫此路徑（`agent_id` 用 profile_id，不用 subject_hint 當檔名）  
- Loader / Middleware：同一路徑  
- 廢棄依賴：`data/memory_v1/*` 僅作 seed 匯入來源，不再 runtime 讀  

**1.2 修 `format_for_prompt` import**

```python
from src.memory.v1.loader import MemoryLoader, format_for_prompt, derive_query_tags
```

**1.3 一次性 seed 匯入（可選腳本）**

- 把 `data/memory_v1/agent_rem_memories.jsonl` 中 **有 category+confidence** 的列匯入新路徑  
- 無 category/confidence 的舊列：要嘛補標，要嘛明確標記不注入  

**1.4 觀測**

- Loader trace 必須出現 `eligible_count > 0` 的真實對話 case（至少 1 條）  
- 或：用固定 query_tags 的 unit test 打中 seed  

**驗收**

- [ ] unit：寫 1 筆 v1 → load 同一 agent → eligible 非空（高 conf）  
- [ ] unit：低 conf / 無 category → eligible 空（fail-safe）  
- [ ] 修 import 後，eligible 路徑不再 NameError  
- [ ] 真實或 mock 一輪 Rem：log 見 `v1 store mirror` + `enrich context_len` 增加  
- [ ] 新增 KI 條目記錄「路徑統一」與舊檔遷移狀態  

**風險**

- 改路徑後舊 jsonl orphan → 用腳本 copy，不硬刪  

---

### Stage 2 — KI-005 migration（P1，0.5 天）

**步驟**（沿用 KNOWN_ISSUES 修法 A）

1. `cp data/memory.db data/memory.db.backup-YYYYMMDD`  
2. SQL：舊 `session_agent_X` → `session_bryan_agent_X`（排除 `group` 與已新格式）  
3. 驗證 count：legacy pattern = 0；group 不變  
4. FTS5 content-table 模式：可不 rebuild；仍建議 smoke search  

**驗收**

- [ ] `get_recent("session_bryan_agent_yua")` 能吃到原 185 列級別歷史  
- [ ] KI-001 isolation verify 仍綠  
- [ ] KNOWN_ISSUES KI-005 → 已修 + commit  

**執行時機**：Stage 1 後、Bryan 確認 backup 即可（不必等 Loader 完美）  

---

### Stage 3 — Rem Gold Path 硬化（1–2 天）

目標：Rem 成為「可宣傳的記憶正確性」示範 agent。

| 子項 | 內容 |
|------|------|
| 3.1 寫入量 | 確認 LLM Judge 對 Rem 有穩定 mirror（log 抽樣 20 輪） |
| 3.2 檢索 | 整理 10 條 ground-truth query（偏好/事實/日記），對照 eligible 與是否進 prompt |
| 3.3 門檻 | 若全 fail-safe：調 conf 或 tags，**禁止**為了 hit rate 放寬到「亂注入」 |
| 3.4 Trace | loader_trace + judge trace 能回答：「這句回覆有沒有用到記憶、用了哪幾條」 |
| 3.5 回歸 | 既有 Rem persona / JP verify 不因 memory block 崩人設 |

**驗收**

- [ ] 10-query 表：至少 N% 在「該記得」場景 hit（N 由 Bryan 定，建議先 60%）  
- [ ] 0 次「明顯錯記憶」注入（人工 spot-check）  
- [ ] No Memory 場景仍寧可不注入  

---

### Stage 4 — Shadow 結案 + Judge 升主（1 天，視資料）

1. 統計 `data/shadow/shadow_log.jsonl`：  
   - 錯誤率 < 5%？  
   - v6 vs heuristic agreement  
   - category 分佈（preference / milestone / diary）  
2. 決策：  
   - **A** 維持 LLM Judge 主寫 + heuristic fallback（現狀）  
   - **B** 關 shadow，只留 prod judge  
   - **C** 若 agreement 差 → 改 prompt / few-shot 再開 7 天  

**驗收**：一頁報告 + 決策寫進 KNOWN_ISSUES 或本文件「決策紀錄」  

---

### Stage 5 — 橫向擴 Loader（1–2 天）

在 Rem gold path 綠燈後：

1. `LOADER_ENABLED_FOR_AGENT` → set / config 白名單（先 Yua + Ruka，或全開但 per-agent 可關）  
2. 確認每 agent 的 `memories.jsonl` 路徑與 mirror 一致  
3. per-agent name stopword（§18 已有方向）回歸  
4. 監控：context token 膨脹（SAGE 800 + v1 block + FTS top_k）→ 必要時 budget  

**驗收**

- [ ] 至少 3 個 agent 各有 1 次 eligible>0 的真實 trace  
- [ ] prompt 總長不爆（對照 `configs/default.yaml` budget）  

---

### Stage 6 — PALACE v1.0 成文（1 天文件 + 對齊 code）

等 Stage 3–5 有真實行為後再抽象（符合專案「先做再抽象」）：

建議章節：

1. 記憶類型與生命週期（write / gate / retrieve / inject / decay?）  
2. 存儲拓撲（graph vs jsonl vs memory.db vs history json）  
3. Agent 差異化（Ram no-diary、Canon seeds、value_history）  
4. Multi-user 隔離規則  
5. Fail-safe 與觀測（trace schema）  
6. 與 COS / AOS 邊界  

**驗收**：`docs/PALACE-v1.0.md` + README 連結更新  

---

### Stage 7 — Backlog（不擋主線）

| 項目 | 優先 | 備註 |
|------|------|------|
| KI-003 Ram value_history | P3 | 獨立小 PR |
| KI-004 pause_event | P2 | 角色線，非純記憶 |
| 群聊 commit 策略重審 | P2 | cooldown 丟寫 vs N² |
| Embedding / 真向量 | P3 | SPEC Phase 3；現 FTS+tag 夠用再延 |
| multi-user v1 分片 | P2 | 第二 owner 前必做 |
| 統一清理 orphan jsonl | P3 | 遷移後 |

---

## 八、建議下一週排程（可直接照抄）

| 日 | 工作 | 產出 |
|----|------|------|
| D1 | Stage 0 + Stage 1.1–1.2 | 路徑統一 PR + import 修 |
| D1–D2 | Stage 1.3–1.4 + unit tests | Rem eligible>0 可重現 |
| D2 | Stage 2 KI-005 | DB migration + KI 關閉 |
| D3–D4 | Stage 3 Rem gold path | 10-query 表 + spot-check |
| D5 | Stage 4 shadow 報告 | 決策 A/B/C |
| 下週 | Stage 5 擴 agent → Stage 6 PALACE 草稿 | |

---

## 九、驗收總閘門（記憶「可繼續擴」的定義）

同時滿足才開 Stage 5+：

1. **寫得進去**：Judge/heuristic → graph 與 v1 同回合可觀測  
2. **讀得出來**：Loader 路徑 = Writer 路徑  
3. **敢注入**：fail-safe 仍優先；有 trace 可追溯  
4. **舊資料不丟**：KI-005 完成或明確接受損失  
5. **人設不崩**：Rem（及後續 agent）persona regression 綠  

---

## 十、決策紀錄（施工中填）

| 日期 | 決策 | 理由 | 拍板 |
|------|------|------|------|
| 2026-07-18 | 先修 v1 路徑閉環，再 migration，再擴 agent | 生產讀寫斷裂 > 歷史 17% 不可達 | 待 Bryan 確認 |
| | v1 檔名統一為 `data/memory/{agent}/memories.jsonl` | 消除 subject_hint / data_dir 雙重歧義 | 待 Bryan 確認 |
| | PALACE 文件延後到 Rem gold path 後 | 專案既有「先做再抽象」 | 沿用 |

---

## 十一、相關檔案索引

```
src/memory/middleware.py          # 事件與 Loader 閘門
src/memory/store.py               # memory.db / FTS5
src/memory/sage/provider.py       # SAGE 對外 API、no-diary
src/memory/sage/writer.py         # extract + mirror v1
src/memory/llm_judge.py           # v6 judge
src/memory/v1/{schema,store,loader,retrieval}.py
src/memory/shadow.py
src/llm/proxy.py                  # memory_context + RAG
docs/KNOWN_ISSUES.md              # KI-001 / 005 等
configs/default.yaml              # rag.top_k 等
data/memory.db
data/memory/{agent}/graph.sqlite
data/memory/loader_trace.jsonl
data/shadow/shadow_log.jsonl
```

---

**維護者**: Bryan + 施工 agent  
**下一動作**: 確認本文 Stage 1 路徑約定 → 開 PR 修 import + 統一 V1 路徑。
