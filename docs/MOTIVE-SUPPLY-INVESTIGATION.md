# MOTIVE-SUPPLY-INVESTIGATION-1

**調查案**：今天 12:40（本地，UTC-4）首個 G7 通過的主動訊息窗口，`agent_ruka` 會不會有 pending motive？
**性質**：唯讀調查（0 程式碼／0 測試／0 生產資料／0 服務干擾）。
**調查時刻**：2026-09-14 08:55–09:20 本地。**HEAD** = `f5779be`（== `origin/main`）。
**生產服務**：`:8000` pid 24344，2026-09-14 07:40:43 啟動，已載入 `f5779be` 碼。

---

## TL;DR — 🔴 決定性答案

> ### **No。12:40 `agent_ruka` 不會有 pending motive，會停在 `scheduler.py:459`。**

**兩道獨立的閘門，各自都足以單獨擋下，且兩道都會在 12:40 之前／當下關上：**

| # | 機制 | 證據 | 時間 |
|---|---|---|---|
| **A** | 唯一的 pending 動機**已過 TTL**，將被惰性標記 `expired` | `data/soul/motive_trace.jsonl` 中 `motive_id=21f6c3f77d3141c7a22d84480d3d5562`、`agent_id=agent_ruka`、`status=pending`、`created_at=2026-09-10T19:52:03.022749-04:00`（= `2026-09-10T23:52:03Z`）。TTL = `MOTIVE_TTL_HOURS=24`（`src/soul/motive.py:56`）。**12:40 時 age = 88.80h**，於 `2026-09-11T23:52:03Z` 就已過期 | 早已過期 |
| **B** | Goal 供給池會被清空：信號 5 在 **12:00** 掛起全部 goal，而**候選裝配在同一 tick 內排在其後** | Bry `last_seen = 2026-09-14T11:57:19Z`（本地 07:57，`data/state/bryan_last_seen.json`）→ **+4h = 12:00** 本地，`apply_interrupt_signals()` 的 `last_seen_hours > 4.0` 成立（`src/goals/motive_provider.py:577-593`），把 ruka 7 筆 ACTIVE goal 全部 → `SUSPENDED`；隨後 `assemble_candidate()` 的 active_pool 為空 → `return None`（`:263-268`） | 12:00:xx（30s tick 內） |

**實際停在的行（預測）**：

```
[SM-3 Decision] agent_ruka 无 pending motive → skip publish (F1)      # src/soul/scheduler.py:459
```

**關鍵認知（與票 5 預測的差異）**：票 5 預測會停在 F1，方向正確；但**票 5 假設的病因（goal 全 `SUSPENDED` 導致無供給）只在 12:40 那一刻成立，而它的真正成因是「時鐘極性衝突」，不是「goal 引擎壞掉」**。
實測 09-14 08:55 全體 54 筆 goal = **100% `ACTIVE`（`SUSPENDED` = 0）**，與錨 A 的「100% `SUSPENDED`」**完全相反**——兩者都是**相位快照**，狀態每 4h 翻轉一次（見 §4）。
所以：**12:40 這道牆不是「goal 卡死」，而是「想念需要 Bry 沉默夠久（≥12h）才開門，而 Bry 沉默只要超過 4h，goal 就先被掛起」——兩者在時間軸上永遠錯開。**

**若 No，最少需要改什麼才會有**（詳見 §7）：
> **一行、且不動 Frozen Contract**：讓「掛起」走既有 fail-open 邊界，或讓信號 5 不再清空裝配池。
> 最小候選 = 在 `apply_interrupt_signals()` 的信號 5 迴圈加白名單／per-goal 判定（約 1–3 行），**但必須重啟服務才生效**，而重啟會把 G7b 配額與時序重算 → 12:40 前落地的**風險高於收益**（§7 有完整評估）。

---

## 1. 供給鏈 — pending motive 從哪裡來（`path:line`）

### 1.1 完整鏈路

```
scheduler 主迴圈 (每 30s, scheduler.py:1681 _goal_scan_all)
   └─→ 各 agent: apply_interrupt_signals() / scheduled_wakeup_scan()
        └─ 只「掛起 / 喚醒」goal，**不產 motive**          # motive_provider.py:1720-1721

G2 白名單 → G3 冷卻窗(2h) → G4 靜音(23:00-08:00) → G5 放行痕跡(不再中斷)
   → G7 想念 ≥ 0.3 → G7b 每日上限 1 則 → G9 活動 enrichment
        └─→ scheduler.py:1626  _publish_agency_trigger(agent_id, "proactive_dm", extra)
                 │   ← 全 src **唯一**呼叫點
                 ├─ scheduler.py:292  _inner_life_gate_check()      (G10, fail-open)
                 └─ scheduler.py:300  _decision_check()             (G11, fail-closed)
                          │
                          ├─ ① scheduler.py:430  engine.interpret_new_events(agent_id)
                          │      └─ motive.py:637  讀 inner_life trace → LLM → append_motive
                          │     【產出者 1：InnerLifeEvent】provenance_ref = event_id
                          │
                          ├─ ② scheduler.py:438-454  GoalMotiveProvider
                          │      ├─ :441 apply_interrupt_signals()   ← 信號5 在此**掛起** goal
                          │      ├─ :442 scheduled_wakeup_scan()     ← 喚醒 goal
                          │      └─ :449 assemble_candidate()        ← **唯一**產 goal 候選者
                          │             └─ motive_provider.py:263-268 active_pool 空 → None
                          │             └─ motive_provider.py:312 append_motive
                          │     【產出者 2：Goal】provenance_ref = "goal:{goal_id}"
                          │
                          └─ ③ scheduler.py:456  engine.resolve_pending(agent_id)
                                 └─ motive.py:829 → motive.py:350 MotiveTraceStore.resolve_pending
                                       讀 data/soul/motive_trace.jsonl
                                       條件: agent_id 相符 AND status=="pending" AND age ≤ 24h
                                       空 → scheduler.py:459 log F1 → return False
```

### 1.2 關鍵座標

| 角色 | 位置 |
|---|---|
| pending 池的實體 | **單一 JSONL 檔** `data/soul/motive_trace.jsonl`（`src/soul/motive.py:280-282`） |
| 讀寫類別 | `MotiveTraceStore`（`src/soul/motive.py:278-448`） |
| 寫入 pending | `MotiveTraceStore.append_motive`（`src/soul/motive.py:316-338`），寫入時硬寫 `status="pending"` |
| pending 判定 | `MotiveTraceStore._latest_by_motive_id`（`:340-348`）取**每個 motive_id 的最後一行**；`resolve_pending`（`:350-409`）過濾 `agent_id` + `status=="pending"`，再檢查 TTL |
| TTL | `MOTIVE_TTL_HOURS = 24`（`src/soul/motive.py:56`）；超時 → `mark_expired` 惰性落盤（`:397-403`） |
| 查詢窗口 | `MOTIVE_INTERPRET_WINDOW_HOURS = 24`（`src/soul/motive.py:59`） |
| 產出者 1 | `MotiveEngine.interpret_new_events`（`src/soul/motive.py:637-724`） |
| 產出者 2 | `GoalMotiveProvider.assemble_candidate`（`src/goals/motive_provider.py:231-325`） |
| 消費／判定 | `scheduler._decision_check`（`src/soul/scheduler.py:409-516`） |

### 1.3 兩個產出者的觸發條件對照

| | 產出者 1（InnerLifeEvent） | 產出者 2（Goal） |
|---|---|---|
| 入口 | `motive.py:637` | `motive_provider.py:231` |
| 資料源 | `data/inner_life/trace.jsonl`（`NarrativeTraceReader.query_by_ts_range`） | `data/memory/{agent_id}/graph.sqlite` 的 `goals` 表 |
| 條件 | 窗口 24h 內、`provenance.actor_id == agent_id`、`event_id` 未被解釋過 | **24h 配額窗**（`:253`）**AND active_pool 非空**（`:263-268`，僅 `ACTIVE`/`IN_PROGRESS`） |
| 去重 | `known_provenance_refs()` | 輪替記憶 `rotation[-1]` 排除上次 goal |
| 產出 | `provenance_ref = event_id` | `provenance_ref = "goal:{goal_id}"` |

> **⚠️ 發現（供後續追蹤，非本票主線）**：`motive.py:675-679` 用 `known_provenance_refs()`（存放 `provenance_ref`）去比對 `r["event_id"]`。產出者 1 的 `provenance_ref` **就是** `event_id`（`:712`），所以比對成立；但一旦同一 event 曾被 Goal 路徑寫入、或有任何 `provenance_ref` 與 `event_id` 不同命名空間的紀錄，去重即失效 → 可能重複 interpretation。此為**潛在重複消費風險**，本次未觀察到實際後果。

---

## 2. 動機來源是 goal 還是 inner-life？（程式碼判定）

**兩者都是合法的 pending 產出者，但生產上實際只有 Goal 在供給。**

### 2.1 為何先前 2 筆真決策的 `provenance_ref` 是 `goal:`

`data/soul/decision_trace.jsonl` 共 20 行，其中 18 行為測試污染（`reason ∈ {"test","x"}`、agent 全為 `agent_rem`）。排除後只剩 **2 筆真決策**（皆 `2026-09-13T02:40` UTC）：

| 行 | ts (UTC) | agent | reason | provenance_ref | decision | motive_kind |
|---|---|---|---|---|---|---|
| 8 | 2026-09-13T02:40:33.449039 | agent_akane | `decision_llm_failure_or_bad_output` | `goal:24ed505d20e54029859b6ab8cfbf5107` | `do_nothing` | `""` |
| 11 | 2026-09-13T02:40:59.130599 | agent_yua | `decision_llm_failure_or_bad_output` | `goal:e41508968ea24780a87945d92e757365` | `do_nothing` | `""` |

**判定：來源 = Goal 引擎。** 程式碼證據：
- `provenance_ref` 前綴 `goal:` 只由**一個地方**產生：`src/goals/motive_provider.py:302`
  `provenance_ref=f"{GOAL_PROVENANCE_PREFIX}{goal.goal_id}"`。這是全 src 唯一的 `goal:` 命名空間寫入點。
- 產出者 1（inner-life）的 `provenance_ref` 是 **`event_id` 的裸 hex**（`src/soul/motive.py:712` `provenance_ref=event_id`），**不會**有 `goal:` 前綴。
- ⇒ 兩筆真決策的動機都經由 `assemble_candidate` 產生，即 **Goal 引擎**。

**為何 `motive_kind` 為空**：`motive_kind` 是 `decision_trace` 的 sidecar 欄位，源自 `Motive` 的既有欄位。`Motive` 是 **5 欄位凍結** dataclass（`motive_id/content/target/provenance_ref/created_at`，`src/soul/motive.py:139-159`），**沒有** `motive_kind`。Goal 路徑從不設此欄位 ⇒ 落盤為 `""`。
反觀 inner-life 路徑會額外寫一個 `motive_type`（`observe/reflect/transmit`），但那是寫進 **motive_trace** 而非 `Motive`（`src/soul/motive.py:331-332`、`:721-722`）。
⇒ **`motive_kind` 空 = 該動機來自 Goal 路徑的正常現象，不是錯誤，也不能用來判定「動機壞掉」。**

### 2.2 生產供給實況（誰真的在產）

| 產出者 | 生產證據 | 判定 |
|---|---|---|
| Goal | `[Goal] 候选装配` 生產共 **2 次**（09-05 11:58:30 ruka / 09-08 17:16:02 ruka）；motive_trace 中 `goal:` 前綴紀錄共 **7 筆** | ✅ 活著，但**極稀疏** |
| InnerLife | motive_trace 中**非** `goal:` 前綴的 pending 產出，最後一次為 `2026-09-05T15:03:17Z`（`d98618824b7f...`，agent_ruka，已 rejected）。`motive_trace.jsonl` 整體 **mtime = 2026-09-12 22:40:59**，之後 **0 新寫入** | ⚠️ 路徑程式碼活著，但近 48h 無新產出（見 §5） |

---

## 3. 生產現況數據（唯讀實測）

實測時刻：**2026-09-14 08:55–09:05 本地（UTC-4）**。所有 SQLite 以 `file:...?mode=ro` 開啟，0 寫入。

### 3.1 pending motive 現況 — 每個角色幾個？

**來源**：`data/soul/motive_trace.jsonl`（47 行 / 24 個 distinct `motive_id`；取每個 id 的最後一行）

| agent | 狀態 | 筆數 |
|---|---|---|
| agent_ruka | `pending` | **1** |
| agent_ruka | `expired` | 3 |
| agent_ruka | `rejected` | 15 |
| agent_ruka | `transmitted` | 1 |
| agent_akane | `rejected` | 2 |
| agent_yua | `rejected` | 2 |

> ### **全生產「現在」的 pending motive 總數 = 1，且只有 1 個角色有：`agent_ruka`。**

**那唯一一筆 `pending` 的完整內容與死期**：

| 欄位 | 值 |
|---|---|
| `motive_id` | `21f6c3f77d3141c7a22d84480d3d5562` |
| `agent_id` | `agent_ruka` |
| `status` | `pending` |
| `created_at` | `2026-09-10T19:52:03.022749-04:00` = **`2026-09-10T23:52:03Z`** |
| `provenance_ref` | `goal:c63ac99a84f94691ad0a653d5ed326db` |
| `content` | `夜晚的日记还开着，可现在是下午` |
| **TTL 到期** | `2026-09-11T23:52:03Z`（**已到期 2 天以上**） |
| **12:40 時 age** | **88.80 h** vs TTL **24 h** ⇒ `age > ttl_hours` 成立（`src/soul/motive.py:397`） |

`resolve_pending` 一旦被呼叫，此行即被 `mark_expired` 並回 `None` ⇒ 走 `scheduler.py:457-461` 的 F1 分支。

### 3.2 goal state 分布 — 與錨 A 相反

| 來源 DB | goals 筆數 | state 分布 |
|---|---|---|
| `data/memory/agent_ruka/graph.sqlite` | 7 | `ACTIVE` 7 |
| `data/memory/agent_akane/graph.sqlite` | 7 | `ACTIVE` 7 |
| `data/memory/agent_mai/graph.sqlite` | 7 | `ACTIVE` 7 |
| `data/memory/agent_rem/graph.sqlite` | 7 | `ACTIVE` 7 |
| `data/memory/agent_anna/graph.sqlite` | 5 | `ACTIVE` 5 |
| `data/memory/agent_mahiru/graph.sqlite` | 5 | `ACTIVE` 5 |
| `data/memory/agent_aoi/graph.sqlite` | 4 | `ACTIVE` 4 |
| `data/memory/agent_miku/graph.sqlite` | 4 | `ACTIVE` 4 |
| `data/memory/agent_ram/graph.sqlite` | 4 | `ACTIVE` 4 |
| `data/memory/agent_yua/graph.sqlite` | 4 | `ACTIVE` 4 |
| **合計** | **54** | **`ACTIVE` 54、`SUSPENDED` 0、`IN_PROGRESS` 0、`COMPLETED` 0、`ABANDONED` 0** |

**重要澄清**：
- goals 表**只存在於** 10 個 per-agent DB `data/memory/agent_*/graph.sqlite`（表名 `goals`）。`data/memory.db`、`data/memory/sage/graph.sqlite`、`data/memory/sage/agent_ram.db` **皆無** `goals` 表。
- `state_updated_at` **全部 = 2026-09-14 08:00**（今晨喚醒 tick）。
- `suspend_snapshot` **54/54 筆**皆為 `{"reason":"bryan_last_seen_timeout","last_seen_hours":4.0,"ts":1789373720.9}` = **2026-09-14 04:15:20 本地**。
- `created_at` 範圍 `2026-09-05 11:28` → `2026-09-12 16:47`，每日每 agent 恰 1 筆；**09-13 起 0 筆新 goal**。
- `advance_count` 總和 = **1**（僅 ruka 的 `7d05f60796a54ed580c3aa148563d12f`），**53/54 筆 = 0**。

### 3.3 24h 配額窗現況（`goal_provider.json`）

| agent | `last_candidate_at` | 換算 | 12:40 時距今 |
|---|---|---|---|
| **agent_ruka** | `1789084323.022749` | **2026-09-10 19:52:03** | **≈ 88.8h** ✅ 配額窗**已開** |
| agent_yua | `1788623928.709633` | 2026-09-05 11:58:48 | ≈ 218h ✅ 已開 |
| agent_akane | `1788639351.986018` | 2026-09-05 16:15:51 | ≈ 218h ✅ 已開 |
| agent_rem / ram / mahiru / anna / mai / miku / aoi | `0.0` | 從未產候選 | ✅ 已開 |

⇒ **12:40 時 24h 配額窗對 ruka 是「開」的**。配額**不是**這裡的 blocker；blocker 是 active_pool 為空（§4）。

### 3.4 其他時間錨

| 項目 | 值 |
|---|---|
| Bry `last_seen` | `2026-09-14T11:57:19.983981Z`（本地 **07:57:19**），`last_recv_agent=agent_mai`（`data/state/bryan_last_seen.json`） |
| 想念（實測） | 08:10:44 → **0.20**；08:40:45 → **0.21**（`data/server_nohup.err`）；門檻 `LONGING_THRESHOLD=0.3` |
| 已確認預測 | 12:10 → 0.2979（差 0.0021 被擋）、12:40 → **0.3104**（首度通過） |
| `motive_trace.jsonl` mtime | **2026-09-12 22:40:59**（近 48h 僅 1 行測試污染寫在 09-14 02:41–04:12） |
| `decision_trace.jsonl` mtime | 2026-09-14 04:12:26（最後 6 行皆測試污染 `agent_rem` / `reason="test"`） |

---

## 4. goal 為何 100% `SUSPENDED`？—— 根因與「只進不出」判定

### 4.1 🔴 根因：**不是「只進不出」的 dead code，是「時鐘極性衝突」的 bang-bang 振盪**

> **錨 A 的「100% `SUSPENDED`」是相位快照，不是恆定狀態。**
> 同一支信號（Bry last-seen 4h）以**相反極性**同時驅動「全體掛起」與「全體喚醒」：
> - **Bry 沉默 ≥ 4h** → 信號 5 **一次掛起所有** goal（**此路徑 0 行 log → 生產查無痕跡**）
> - **Bry 一開口（< 4h）** → 下一個 30s tick **全部喚醒**
>
> ⇒ 任何在「Bry 不在場」時取樣的快照都讀到 100% `SUSPENDED`；Bry 在場時讀到 100% `ACTIVE`。

**完整振盪實錄（有 log 覆蓋的 33h 內）**：

| 時刻（本地） | 事件 | 筆數 | log 痕跡 |
|---|---|---|---|
| 09-13 22:42:34 | 全體**喚醒** → `ACTIVE` | 54 | `[Goal] 唤醒:` ×54 ✅ |
| **09-14 04:15:20** | 全體**掛起** → `SUSPENDED` | 54 | **0 行（程式碼無 logger）** ❌ |
| 09-14 08:00:14 | 全體**喚醒** → `ACTIVE` | 54 | `[Goal] 唤醒:` ×54 ✅ |

> 注意 **04:15 屬靜默時段（23:00–08:00）** ⇒ 證明 **`apply_interrupt_signals()` 無靜默時段過濾**（對照 `scheduled_wakeup_scan()` 有，`motive_provider.py:679-680`）。

### 4.2 誰會把 goal 設成 `SUSPENDED`（全部寫入點）

狀態常數：`src/goals/models.py:27-31`；轉移表 `:67-82`（`SUSPENDED` 唯一出邊 = `ACTIVE`，`:77-79`；終態無出邊 `:80-81`）；寫入器 `src/memory/sage/graph_store.py:721 transition_goal`。

| # | 位置 | 條件 | 生產實況 |
|---|---|---|---|
| **①** | `src/goals/motive_provider.py:577-593` **信號 5** | Bry `last_seen > 4h` → 對**所有** `ACTIVE`/`IN_PROGRESS` 逐筆掛起 | **✅ 生產主因**（54/54 筆 suspend_snapshot 皆 `bryan_last_seen_timeout`；**0 行 log**） |
| ② | `src/goals/motive_provider.py:417-436` `_handle_do_nothing` | Decision 連續 `not_transmit ≥ 3`（`:66`） | ❌ 生產 0 次 |
| ③ | `src/goals/motive_provider.py:605-613` **信號 6** | 同上（冪等兜底） | ❌ 生產 0 次 |
| ④ | `src/goals/motive_provider.py:637-661` `suspend_on_takeover()` | 私聊突發／多模態高優（信號 1/2） | ❌ **全 src 無呼叫者 → 生產 dead** |

排除：`GoalSeedProvider` 建 goal 時一律寫 `ACTIVE`（`src/goals/seed_provider.py:712`）。

### 4.3 誰會讓 goal 離開 `SUSPENDED`

**唯一入口**：`src/goals/motive_provider.py:686`
`store.transition_goal(g.goal_id, GOAL_STATE_ACTIVE)`，位於 `scheduled_wakeup_scan()`（`:666-691`）。
喚醒條件 `_wakeup_condition_met()`（`:693-714`）：
- ② **跨日**（`state_updated_at` 日期 ≠ 今日，`:695-701`）
- ③ **Bry `last_seen < 4h`**（`:702-708`）
- ④ 暫停 > 7 天（`GOAL_WAKE_FORCE_SECONDS`，`:711-713`）
- 前置：靜默時段（23:00–08:00）直接 return（`:679-680`）

**呼叫者（2 個，皆為生產活路）**：

| 呼叫點 | 上層 | 頻率 |
|---|---|---|
| `src/soul/scheduler.py:1721` | `_goal_scan_all()` ← 主迴圈 `scheduler.py:1681` | 每 30s |
| `src/soul/scheduler.py:442` | `_decision_check()` | 僅當 proactive_dm 過 G2–G7b |

### 4.4 「只進不出」判定

**兩個層次要分開回答：**

**(a) 狀態層：❌ 不是只進不出。**
出口 `scheduled_wakeup_scan` **有接線、且在生產確實被呼叫**——生產 log `[Goal] 唤醒:` 共 **134 條**（3 波：09-08 16:45 ×26、09-13 22:42 ×54、09-14 08:00 ×54）。這**直接排除**「離開 `SUSPENDED` 的函式從未被呼叫」這個假設。

**(b) 進度層：✅ 近似只進不出（真死鎖）。**
能把 goal 推進（`advance_count+1` / `IN_PROGRESS` / `COMPLETED`）的唯一入口是 `assemble_candidate`（`motive_provider.py:231-325`），而它：

```
assemble_candidate  ←── 只被 _decision_check 呼叫 (scheduler.py:449)
_decision_check     ←── 只被 _publish_agency_trigger("proactive_dm") 觸達 (scheduler.py:291/300)
_publish_agency_trigger ←── 全 src 唯一呼叫點 = scheduler.py:1626 (_fire_proactive_dm 尾端)
```

而 `_fire_proactive_dm` 的前置閘門是 **G2 白名單（生產 = `['agent_ruka']`）** + **G7 想念 ≥ 0.3**。
⇒ **互斥死鎖**：
- 想念（G7）是沉默時長的**單調不增**函數 → 需要 Bry **沉默夠久**才放行；
- 但 Bry 沉默 ≥ 4h 時，信號 5 已把**全體** goal 掛起 → `active_pool` 空 → `assemble_candidate` 直接 `return None`（`:267-268`）。
- 反之 Bry 在場（goal 才 `ACTIVE`）時想念低 → **G7 擋住**。

**生產直證（今天早上，此刻 goal 恰好是 `ACTIVE`，卻仍進不了裝配）**：

```
2026-09-14 08:10:44,728 [scheduler] 💬 proactive_dm 觸發: agent_ruka (whitelist=['agent_ruka'])
2026-09-14 08:10:44,773 [M7-longing] agent_ruka 想念 0.20 < 0.3, 排 30 min 後再查
2026-09-14 08:40:45,581 [scheduler] 💬 proactive_dm 觸發: agent_ruka (whitelist=['agent_ruka'])
2026-09-14 08:40:45,589 [M7-longing] agent_ruka 想念 0.21 < 0.3, 排 30 min 後再查
```
`data/server_nohup.err` 中 `[SM-3 Decision]` = **0**（現行 process 自 07:40 起）⇒ `_decision_check` 根本沒跑 ⇒ 無法裝配。

**附帶事實**：9 天生產只有 **2 次**候選裝配，且**兩次都是 `agent_ruka`**（因為白名單只有 ruka）⇒ **另外 9 個 agent 的 47 筆 goal 在構造上永遠無法推進**。

### 4.5 與「SE-5 生命週期家族生產 0 次呼叫」的關係

> **不同一條路徑，不是同一件事。**

| | SE-5 家族（REINFORCE／decay／forget／evaluate_lifecycle／revise／supersede） | 本案（goal `SUSPENDED`） |
|---|---|---|
| 模組 | 記憶／fact 生命週期 | goal 引擎信號 5／§8.3 喚醒 |
| 病症 | **函式從未被呼叫**（dead） | 出口**被呼叫且有 134 條痕跡**；病在**上游閘門極性衝突** + 唯一推進入口被白名單/想念門檻限流 |
| 判定 | dead code | **bang-bang 振盪 + 互斥死鎖** |

兩者只共用「某分支在生產實質不可達」這個**缺陷類別**；程式碼、模組、根因皆不同。**不應合併成同一張單。**

### 4.6 其他生產異常（順帶發現）

`data/logs/server_nohup.20260905_131159.err`：`2026-09-05 13:12–13:27` 出現
`[Goal] 主循环扫描异常 (fail-closed): KeyError: 'relational_band'` **×32**（每 30s 一次）。
`_goal_scan_all` 的 `try/except` 包住**整個 per-agent for 迴圈**（`scheduler.py:1718-1731` 包在 `:1715-1735` 的單一 try 內）⇒ 任一 agent 在 `settle_relations` 拋錯會**整段中止**，當時後續 agent 的掃描全被跳過。**此為治理風險（單點失敗污染全體），建議另立票。**

---

## 5. InnerLifeEvent → interpretation → motive 這條路是否活著？

**結論：程式碼活著、資料源活著，但「產出動機」這一環在近 48h 內 0 次成功，且沒有任何可觀測性。**

### 5.1 資料源（`data/inner_life/trace.jsonl`）— ✅ 活的

| 項目 | 實測 |
|---|---|
| 檔案 | 2,434 行，1.19 MB，**mtime = 2026-09-14 08:40:47**（持續寫入） |
| ts 範圍 | `2026-08-13T02:49:22Z` → `2026-09-14T12:40:47Z` |
| `provenance.actor_id` 分布 | `None`: 1689（絕大多數是 `world:news_event`）、`agent_ruka`: **84**、`agent_rem`: 172、`agent_yua`: 68、`agent_miku`: 64、`agent_aoi`: 63、`agent_akane`: 60、`agent_anna`: 60、`agent_ram`: 57、`agent_mahiru`: 57、`agent_mai`: 55 |

⇒ **有 `actor_id` 的事件存在**（ruka 84 筆），所以 `interpret_new_events` 的 `prov.get("actor_id") == agent_id` 過濾（`motive.py:671`）**不是**永遠空集。**此路徑的入口條件是可滿足的。**

### 5.2 掃描／節流節奏

- **沒有**獨立定時器。`interpret_new_events` 只在 `_decision_check` 內被呼叫（`scheduler.py:430`），
  而 `_decision_check` 只在 proactive_dm 過 G2–G7b 後才跑（`scheduler.py:300`）。
- 窗口 = `MOTIVE_INTERPRET_WINDOW_HOURS = 24`（`motive.py:59`），即**只解釋最近 24h 內的事件**。
- ⇒ **節流節奏 = 「想念 ≥ 0.3」的節奏**，不是固定週期。

### 5.3 生產 log 有無 interpretation 嘗試的痕跡？

| pattern | 命中 |
|---|---|
| `[Motive] interpretation 失败/无输出`（`motive.py:695-698`） | **0** |
| `[Motive] interpretation: 无念头`（`motive.py:701-703`） | **0** |
| `[Motive] motive 因 target 非法被拒绝`（`motive.py:716-719`） | **0** |
| `[MotiveTraceStore] motive 写入`（`motive.py:334-338`） | **0**（現行 process；`ENGINEERING_STATE.md` 的行是文件內文非 log） |
| `[Motive] trace query 失败`（`motive.py:657-660`） | **0** |

**⇒ 現行 process 從 07:40 起，`interpret_new_events` 的 5 條 log 路徑一條都沒觸發。這與「`_decision_check` 從未執行」完全一致（`[SM-3 Decision]` = 0）。**

### 5.4 `motive_kind` 為空的原因

見 §2.1 末段：`Motive` 是 **5 欄位凍結** dataclass，**根本沒有** `motive_kind` 欄位；`motive_kind` 是 `decision_trace` 的 sidecar，只有 inner-life 路徑才會另外寫 `motive_type`（且寫在 **motive_trace**，不是 `Motive`）。
**⇒ `motive_kind` 空 = Goal 路徑的正常簽名，不是故障指標。**

### 5.5 本節判定

| 環節 | 狀態 |
|---|---|
| InnerLifeEvent 產生 | ✅ 活（trace 持續寫入，ruka 84 筆） |
| `interpret_new_events` 被呼叫 | ⚠️ **只在 G7 通過時**；近 48h 因 G7 未通過而 0 次 |
| interpretation LLM 成功產動機 | ❌ 無證據（log 0 痕跡，motive_trace 自 09-05 15:03 後無非 `goal:` 產出） |
| 可觀測性 | ❌ **現行 process 完全沉默**——`_decision_check` 未跑時，整條鏈沒有任何一行 log |

---

## 6. ⏰ 12:40 全閘門預測表（獨立、可轉貼）

> **用法**：12:40 後直接在 `data/server_nohup.err` 由下往上 grep L1 → L11，**第一次出現的行就是停下的那一關**。
> **預測**：會走到 **L10**（`无 pending motive`），並在 L10 之前先看到 L6 的「想念 ≥ 0.3」。
> **12:40 前必然出現（12:00 前後）**：54 條 `[Goal] 唤醒` **不會**出現；出現的是「無聲掛起」——goal 由 `ACTIVE` 轉 `SUSPENDED` **不留任何 log**，只能藉 12:00 後下一次實測 DB 觀察。

| # | 關卡 | 位置 | 12:40 預期 | 確切 log 樣式（可複製 grep） |
|---|---|---|---|---|
| **L1** | G2 白名單 | `scheduler.py:1141-1160` | ✅ 通過（唯一白名單 `agent_ruka`） | `grep "proactive_dm 觸發: agent_ruka" data/server_nohup.err` |
| **L1b** | G2 白名單配錯警告 | `scheduler.py:1158-1160` | 未觸發 | `grep "⚠️ proactive whitelist" data/server_nohup.err` |
| **L2** | G3 冷卻窗（2h） | `scheduler.py:1469` | ✅ 通過（距上次成功送達已久） | `grep "不可送達: G3 冷卻窗未滿" data/server_nohup.err` |
| **L3** | G4 靜音時段（23:00–08:00） | `scheduler.py:1491` | ✅ 通過（12:40 非靜音） | `grep "不可送達: G4 靜音時段" data/server_nohup.err` |
| **L4** | G5 Bry 閒置（**已放寬，只留痕不中斷**） | `scheduler.py:1530-1536` | ⚠️ **應會出現**（Bry 已閒置 >4h）——**這行是「放行」不是「擋下」，看到它代表流程繼續** | `grep "proactive_dm G5 放行" data/server_nohup.err` |
| **L5** | 想念 < 0.3（**擋下**） | `scheduler.py:1565-1573` | ❌ 12:10 會出現（0.2979）；12:40 **不會** | `grep "想念 0\.\|想念 .* < 0.3" data/server_nohup.err` |
| **L6** | 想念 ≥ 0.3（**放行**） | `scheduler.py:1574-1576` | ✅ **首度出現**（0.3104）——**這是 12:40 的開門訊號** | `grep "想念 .* >= 0.3, 觸發主動傳訊" data/server_nohup.err` |
| **L7** | G7b 每日上限（1 則） | `scheduler.py:1590-1595` | ✅ 通過（今日尚未用配額） | `grep "每日上限已達" data/server_nohup.err` |
| **L8** | G9 活動 enrichment（觀測） | `scheduler.py:1611-1625` | 兩者之一皆正常 | `grep -e "proactive_dm 帶活動" -e "proactive_dm 未帶活動" data/server_nohup.err` |
| **L9** | G10 Inner Life Gate | `scheduler.py:373-395` | 預期 `EMITTED`（fail-open，不擋） | `grep "M5.8-4 Inner Life Gate" data/server_nohup.err` |
| **L10** | **G11 `無 pending motive` → skip（F1）** | **`scheduler.py:457-460`** | 🔴 **預測停在這裡** | `grep "\[SM-3 Decision\] agent_ruka 无 pending motive" data/server_nohup.err` |
| **L11** | G11 Decision 結果（**不會到**） | `scheduler.py:488-501` | 不會出現 | `grep -e "transmit motive=" -e "not_transmit motive=" data/server_nohup.err` |
| **L11b** | G11 例外（fail-closed） | `scheduler.py:510-515` | 不會出現 | `grep "\[SM-3 Decision\] agent_ruka exception" data/server_nohup.err` |
| **L12** | G7b 配額落帳（**只在 publish 後**） | `scheduler.py:1627-1635` | 不會出現（L10 已 return，配額**不會**被燒掉） | `grep "gate=G7b, .* 則 (本地日)" data/server_nohup.err` |

### 6.1 12:40 前的側面證據（可同步查）

```powershell
# 1) 確認 pending motive 是否已被惰性標記 expired（應在 12:40 後出現）
grep "expired (age=" data/server_nohup.err

# 2) 確認 goal 是否在 12:00 後被無聲掛起（log 不會有，只能查 DB / 看 suspend_snapshot ts）
#    <workspace>\.venv\Scripts\python.exe 讀 data/memory/agent_ruka/graph.sqlite
#    SELECT state, COUNT(*) FROM goals GROUP BY state;
#    並看 suspend_snapshot 的 ts 是否 ≈ 12:00

# 3) 12:40 前後確認想念爬升曲線（跨檔案）
grep "想念" data/server_nohup.err
```

### 6.2 預測的信心與反例條件

- **主預測（L10）信心：高。** 兩道獨立閘門（§TL;DR A 與 B）各自都足以單獨造成 F1，且時間上**都已確定發生**（A 早在 09-11 過期；B 在 12:00 由 30s tick 觸發）。
- **會被推翻的情況**（若 12:40 沒停在 L10，先檢查這些）：
  1. **Bry 在 12:00–12:40 之間又講話** → 信號 5 不掛起（或掛起後被 ③ 喚醒），goal 保持 `ACTIVE`。
     **但結論不變**：A（TTL 過期，§TL;DR 閘門 A）**獨立成立**，`resolve_pending` 仍回 `None` → **仍停在 L10**。
  2. 有人在 12:00–12:40 之間**手動寫入一行 `status="pending"` 的新 motive 到 `data/soul/motive_trace.jsonl`** → **這是唯一能改變結果的人為介入**（本調查未做、也不應做）。
  3. `last_candidate_at` 若被重設成 < 24h 內 → 配額窗關閉 → 仍停在 L10。
- **補充：跨日喚醒（條件 ②）在 12:40 前不會救場。** 今日 08:00 的喚醒之所以成立，是因為當時 `state_updated_at` 仍是 **09-13 22:42**（≠ 今日）。一旦 12:00 信號 5 再度掛起，`state_updated_at` 被更新為 **09-14**，條件 ② 就**失效到明天**。
  ⇒ 12:00 之後唯一還能喚醒 goal 的只剩 **③「Bry 開口（last_seen < 4h）」**——而這正是「想念（G7）需要 Bry 沉默夠久」的**反面**。**兩者在時間軸上永遠錯開，這就是本案的核心矛盾。**
- **唯一能讓 12:40 成功（走到 L11 `transmit`）的條件**：**在 12:40 之前**，`agent_ruka` 的 `active_pool` 非空（goal 保持 `ACTIVE`）**且**配額窗開啟——才可能於 12:40 同一 tick 內由 `assemble_candidate` 產出新 pending motive，供 `resolve_pending` 取用（新 motive 的 `created_at` = 當下，TTL 不會擋）。

---

## 7. 最小修法候選（只提設計，不實作）

**評估基準**：改動量 / 風險 / 是否需重啟 / 是否碰 Frozen Contract。
**時間預算前提**：現在 ~09:20，決策時刻 12:40，可用窗約 **3h20m**；其中「改碼 + 測試 + 重啟 + 觀察」的**安全落地窗**建議保留 ≥40min。

### 候選 1（最小、推薦討論）— 信號 5 加白名單／per-goal 判定
- **位置**：`src/goals/motive_provider.py:577-593`（信號 5 迴圈）
- **改動量**：約 **1–3 行**（例如在 `for g in store.get_goals(...)` 內加 `if self.agent_id not in <白名單>: continue`，或改為 per-goal 判定而非全體批次）
- **檔案**：僅 `src/goals/motive_provider.py`
- **Frozen Contract**：**不碰**（不改 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯；不改 goal 狀態機轉移表）
- **需重啟**：**是**（Python 模組層改動）
- **優點**：直接保住 `active_pool`，12:40 的 `assemble_candidate` 就能產出候選 → 有望走到 L11
- **風險**：**中**。① 改變「Bry 不在場時不推進 goal」的既有語意，屬**設計層變更**，依 AGENTS.md 需主大腦 + Owner 背書；② 若只加白名單，會讓 9 個非白名單 agent 的行為與 ruka 分歧（一致性問題）
- **12:40 前可否落地**：**可以（時間上）**，但**不建議**——見 §7.6

### 候選 2（治本、範圍最大）— 拆掉 `assemble_candidate` 對 proactive_dm 的唯一依賴
- **位置**：`src/soul/scheduler.py:449`（`_decision_check` 內的 `assemble_candidate()` 呼叫）
- **改動量**：**中**（需新增掛載點，例如在 `_goal_scan_all` 內做「只裝配、不決策」，或讓 event/dream 路徑也跑 decision check）
- **檔案**：`src/soul/scheduler.py`（可能 +`src/goals/motive_provider.py`）
- **Frozen Contract**：**邊界模糊**——`_publish_agency_trigger` 的「唯一呼叫點」目前是刻意的 1HB1S 拓撲（`scheduler.py:1709` 註解明示），動它需契約審查
- **需重啟**：**是**
- **優點**：解決 §4.4 的**互斥死鎖**，也讓另外 9 個 agent 的 47 筆 goal 有機會推進
- **風險**：**高**（新增 tick／通道，違反「0 新定時器」治理原則）
- **12:40 前可否落地**：**不可**（需要契約設計 + 審查，超出時窗）

### 候選 3（治本、語意最乾淨）— 讓想念與 goal 存活的「沉默門檻」不再互斥
- **位置**：`LONGING_THRESHOLD`（`scheduler.py:84-86` 附近）與 `PROACTIVE_DM_BRYAN_INACTIVE_HOURS = 4.0`（`src/io/channels/bryan_state.py:34`）的**關係**
- **改動量**：**小**（調門檻或改信號 5 的觸發基準）
- **Frozen Contract**：**需確認**——`PROACTIVE_DM_BRYAN_INACTIVE_HOURS` 被 `scheduler.py:1523-1526` 註解明列為「本票不碰」，且同樣被 router M0.5 / `goals/seed_provider.py` 使用 ⇒ **牽連面廣**
- **需重啟**：**是**
- **優點**：直擊病根（兩者時間軸錯開）
- **風險**：**高**（4.0h 是多處共用的常數，改動會波及 router 與 seed 路徑）
- **12:40 前可否落地**：**不可**

### 候選 4（純觀測、零行為變更）— 補上 `SUSPENDED` 轉移的 log
- **位置**：`src/goals/motive_provider.py:584-592`（信號 5）、`:605-613`（信號 6）
- **改動量**：**約 2 行**（加 `logger.info`）
- **Frozen Contract**：**不碰**
- **需重啟**：**是**（但無語意變更）
- **優點**：根治「全體 SUSPENDED 卻查無痕跡」的觀測盲區（本次調查最大的取證障礙）
- **風險**：**極低**
- **12:40 前可否落地**：**可以且安全**，但**無法改變 12:40 的結果**（只增加可觀測性）

### 7.6 🔴 12:40 前落地的總體建議

> **不建議在 12:40 前改任何程式碼。** 理由：
> 1. **候選 1/3 的改動會改變「主動」語意**，依 AGENTS.md 屬設計決策，需主大腦 + Owner 背書（尤其 `PROACTIVE_DM_BRYAN_INACTIVE_HOURS` 已被明文列為「不碰」）。
> 2. **任何落地都需要重啟**：重啟會 ① 重算 `_next_proactive_dm_time` 與 G7b 記憶體配額 ② 讓 12:40 的想念曲線與時序錨點全部位移——**反而可能把已經可預測的窗口變成不可預測**。
> 3. **12:40 的失敗是「可觀測、可解釋」的失敗**（停在 L10，原因已釘死），這比「冒險改動後得到一個無法解釋的結果」更有價值。
>
> **建議**：把 12:40 當作**一次乾淨的觀測**（用 §6 的表逐關核對，驗證本報告的預測），12:40 之後再依 §7 候選 1/4 開正式工單（含契約審查 + Owner 背書）。

---

## 8. 調查結論摘要（一頁）

| 問題 | 答案 |
|---|---|
| **12:40 ruka 會有 pending motive 嗎？** | **No** |
| **會停在哪？** | `src/soul/scheduler.py:459` `[SM-3 Decision] agent_ruka 无 pending motive → skip publish (F1)` |
| **G7b 配額會被燒掉嗎？** | **不會**——配額記在 `_publish_agency_trigger` **之後**（`scheduler.py:1627-1635`），L10 已 `return False`，走不到 |
| **pending motive 存在哪？** | `data/soul/motive_trace.jsonl`（單一 JSONL；非 DB、非表） |
| **現在全生產有幾個 pending？** | **1 個**，僅 `agent_ruka`，且**已過 TTL 88.8h** |
| **動機來自 goal 還是 inner-life？** | 兩者皆為合法產出者；**生產上實際供給者 = Goal**（`provenance_ref` 前綴 `goal:`） |
| **goal 為何 100% SUSPENDED？** | **是相位快照**。真因 = 信號 5（Bry idle >4h）**全體掛起**、Bry 開口則**全體喚醒**的 bang-bang 振盪；**非 dead code**（出口生產被呼叫 134 次） |
| **有「只進不出」嗎？** | 狀態層**沒有**；**進度層有真死鎖**（唯一推進入口被 G2 白名單 + G7 想念門檻與「沉默 4h 即掛起」互斥鎖死） |
| **與 SE-5 家族關係？** | **無關**，不同模組／不同根因，只是同屬「分支實質不可達」缺陷類別 |
| **inner-life 路徑活著嗎？** | 資料源活（trace 持續寫、ruka 84 筆）；但**產出動機近 48h 為 0**，且**完全無 log 可觀測** |
| **`motive_kind` 為何空？** | `Motive` 是 5 欄位凍結 dataclass，**無此欄位**；空 = Goal 路徑的正常簽名 |

---

### 附錄：本次調查的讀取範圍與紅線遵守

- **寫入**：僅本檔 `docs/MOTIVE-SUPPLY-INVESTIGATION.md`。0 改動 `src/**`、`tests/**`、`configs/**`、`scripts/**`、`data/**`、`logs/ENGINEERING_STATE.md`。
- **服務**：0 重啟／0 kill／0 干擾。未觸碰 `:8000`(pid 24344)、`:8765/8766/8767`；未執行 server_ops／watchdog／restart。
- **生產資料**：`data/**` 全程唯讀；SQLite 一律以 `file:...?mode=ro` 開啟。
- **`data/faulthandler.log`：未開啟**（含 `faulthandler.*.log` 一律跳過）。
- **Git**：僅 `git add docs/MOTIVE-SUPPLY-INVESTIGATION.md`（逐檔 add），**未用 `git add -A`**。
- **Frozen Contract**：0 改動。
- **log**：0 清理、0 輪替。

**已知限制**：`data/logs/` 有覆蓋缺口（09-09/10/11/13 無有效 app log、僅 tiny nohup stub）；故 §4.2 的「生產 0 次」僅對**已掃描檔案**成立。但 `SUSPENDED` 掛起路徑「程式碼層就無 logger」是**原始碼事實**，不依賴 log，因此「全體 SUSPENDED 卻查無痕跡」的解釋成立。

---

## 附錄：協調者複核修正

> **性質**：本節為**追加**。**上列原文（含 TL;DR 的「🔴 決定性答案」、§6 全閘門預測表、§8 結論摘要）全部保留、一字未刪。**
> **誰做的**：協調者（幕僚長），於本報告交付後獨立複核。
> **依據**：對 `interpret_new_events` 實際過濾條件的逐字重讀 ＋ 對 `data/inner_life/trace.jsonl` 與 `MotiveTraceStore.known_provenance_refs()` 的**唯讀**實測。

### 修正 1（推翻本報告的決定性結論）：TTL 殺不死「新解讀出的動機」

報告 TL;DR 與 §6 給的「**12:40 ruka 會有 pending motive 嗎？→ No**」為**決定性 No**，其 **gate A 只涵蓋既存的那一筆**（`agent_ruka` `2026-09-10T19:52:03-04:00`，`age 88.8h > MOTIVE_TTL_HOURS=24`）。

**但漏看了一個順序**：

```
_decision_check (:300)
  → interpret_new_events (:430)   ← 先執行：讀新事件 → 產生新動機
  → assemble_candidate (:449)
  → resolve_pending (:456)        ← 才做 TTL 過期判定
      → 空則 :459 F1
```

⇒ **`interpret_new_events`（`:430`）在 `resolve_pending`（`:456`）之前執行**，**新解讀出的動機不受 TTL 影響**（TTL 只對「已存在池中」的舊動機生效）。因此 gate A 的「池中唯一 pending 已過期」**推不出**「12:40 無 pending motive」。

### 修正 2（實測）：inner-life 供給路徑**完好**，且有**新鮮輸入**

依 `interpret_new_events` 的**實際過濾條件**（`provenance.actor_id == agent_id` 且 `event_id not in store.known_provenance_refs()`）唯讀實測 ⇒ **`agent_ruka` 在 24h 窗口內有 3 筆尚未被解讀的事件**：

| # | `event_id` | 時間 | 類型 |
|---|---|---|---|
| 1 | `374e25e5a623` | `2026-09-13T17:27:35Z` | `dream:event` |
| 2 | `78b29a8796d7` | `2026-09-14T02:00:33Z` | `diary:night` |
| 3 | `6b749c27fd44` | `2026-09-14T12:00:17Z` | `diary:morning` |

⇒ 這三筆**未被任何既存動機引用**（不在 `known_provenance_refs()` 中），會在 `:430` 被解讀成**新動機**。**gate A 殺不掉它**（TTL 不適用於新解讀之物）、**gate B 殺不掉它**（信號 5 只碰 goal，不碰 inner-life）。

### 修正 3：修正後的預測（取代 TL;DR 的「決定性 No」）

| 項目 | 原報告 | **修正後** |
|---|---|---|
| 12:40 有 pending motive 嗎？ | **No（決定性）** | **很可能 Yes** —— 由 `:430` 新解讀供給，**與 TTL 無關** |
| 會停在哪？ | `:459` F1 | **F1 很可能通過**；真正變數是**決策層判斷**（`decide_motive` / Decision LLM） |
| 決策層歷史勝率 | （未列） | 歷史 **24 筆**動機中 **19 筆 `rejected`**、**僅 1 筆 `transmitted`**；被拒者多為 `motive_type=reflect`（例：「夜深了，想翻翻以前的回憶。」） |

⇒ **F1 不是 12:40 的 binding constraint；決策層的拒絕率才是。**

### ⚠️ 證據等級聲明（必讀）

本節為**靜態證據推論**（程式碼路徑順序 ＋ 唯讀資料實測），**尚未觀測 12:40 的實際結果**。
**最終判定以 `2026-09-14 12:40` 的生產實測為準**；若與本節預測不符，**以實測為準**並回頭更正本節。

### 本節不改變的結論（原報告仍成立）

- **供給鏈拓撲（報告 §1）**：`data/soul/motive_trace.jsonl` 為唯一池、`_publish_agency_trigger`（`scheduler.py:1626`）為全 `src` 唯一呼叫點 ⇒ **不變**。
- **gate A 的算術**（池中 1 筆 pending 已過 TTL 88.8h）⇒ **不變**，只是**不足以推出決定性 No**。
- **gate B 的程式碼事實**（信號 5 把該 agent 全部 `ACTIVE`/`IN_PROGRESS` goal 轉 `SUSPENDED`，且該區塊 **0 logger**）⇒ **不變**。
- **報告 §4 的 bang-bang 振盪判定與「進度層互斥死鎖」根因** ⇒ **不變**（本節只修 TL;DR／§6／§8 的**預測**，不修其**根因分析**）。
