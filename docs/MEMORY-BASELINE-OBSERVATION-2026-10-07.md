# Memory Runtime Baseline Observation — 現況基線

**工單**：Memory Downsampling — Baseline Observation
**狀態**：完成（唯讀觀測，未介入）
**日期**：2026-10-07
**工具**：`harness/memory_baseline_observation.py`
**產出**：`tests/results/memory_baseline/baseline.json`

---

## 0. 執行邊界（先確認沒越界）

| 約束 | 狀態 |
|---|---|
| 不修改 Soul | ✅ `personas/agent_rem.md` 全程未動 |
| 不修改 Memory / SAGE runtime | ✅ 未動 `src/memory/**` |
| 不改寫 gate / threshold | ✅ 未動 |
| 不改 Palace 寫入策略 | ✅ 未動 |
| 不為了增加樣本而改變 runtime 行為 | ✅ 未觸發任何行為 |
| 不啟動服務、不綁 port、不發真實請求 | ✅ |
| 不殺任何行程 | ✅ |
| 不寫入 production `data/` | ✅ 工具啟動時主動拒絕落在 `data/` 底下的輸出路徑 |

**本檔不含任何 pass/fail。** 依 Owner 裁決：
> 漏寫不能直接等同於 failure。因為我們還沒有定義「什麼東西本來就值得留下」。
> 寫了 70% = 好、寫了 40% = 壞 —— 這個判斷本階段不成立。

---

## 1. 五階段觀測結果

```
Event
  → 是否形成 InnerLifeEvent          4,987 筆（2026-08-13 → 10-07）
  → 是否進入 memory submission       （無獨立記錄，見 §4）
  → 是否真的寫入 diary               1,817 筆（74 天 / 11 agents）；Rem 199
  → 未來是否能被取回                 2,081 次呼叫中 517 次有 eligible
  → 取回後是否影響行為               ⚠️ 無法觀測（instrumentation 停擺，見 §4）
```

### 產生階段：InnerLifeEvent 組成

| trigger_type | 筆數 | 佔比 |
|---|---|---|
| `world:news_event` | 2,549 | 51.1% |
| `world:weather_temp_change` | 844 | 16.9% |
| `diary:night` | 597 | 12.0% |
| `diary:morning` | 442 | 8.9% |
| `world:rain_started` | 255 | 5.1% |
| `dream:dream` | 239 | 4.8% |
| `agent_reply` | 26 | 0.5% |
| `dream:event` | 19 | 0.4% |
| **`conversation:user_message`** | **9** | **0.18%** |
| `system` / `world:calendar_event` | 7 | 0.1% |

**world 類合計 3,648 筆（73.2%）；定時 diary 槽 1,039 筆（20.8%）；真實對話 35 筆（0.7%）。**

### 寫入階段

| 指標 | 值 |
|---|---|
| diary 條目總數 | 1,817（11 agents，2026-07-20 → 10-07） |
| Rem 條目 | 199 |
| Rem 可追溯到 InnerLifeEvent | 116 / 199（58.3%） |
| Rem 不可追溯 | 83（多為早期條目，無 `inner_life_event_id`） |
| **寫入延遲 p50** | **2.09 秒** |
| 寫入延遲 p90 / max | 4.13 秒 / 28.5 秒 |

### 寫入內容語言

| 類型 | 筆數 |
|---|---|
| 含假名（日文） | 1,275（70.2%） |
| 純漢字或空 | 542（29.8%） |

### Retrieval 階段

| 指標 | 值 |
|---|---|
| 總呼叫 | 2,081（Rem 679 / Yua 840） |
| **0 個 candidate** | **1,546（74.3%）** |
| `all_rejected_low_confidence` fail-safe | 1,562（75.0%） |
| 有 eligible candidate | 517（24.8%） |

### Elevation（graph）

4,402 筆：`node_created` 3,023 / `node_elevated` 1,379。
node_type：`pattern` 3,008 / `belief` 915 / `value` 383 / `trait` 96。

---

## 2. 逐層觀察（描述，不是判斷）

### 2.1 產生階段：對話在 lived-experience 流裡幾乎不存在

9 筆 `conversation:user_message` 事件的 `qualification_reason` 揭露了門檻：

```
duration=30.1min>=5.0 AND turn_depth=20>=4
```

也就是說，只有**同時滿足 5 分鐘以上、4 輪以上**的 session 才會因為使用者訊息
產生 InnerLifeEvent。全期符合的只有 9 次，而且全落在 `session_1696287850_*`
與 `session_bryan_test_agent_yua` 兩個 session id 上。

**觀察**：記憶基質目前主要由 world（新聞／天氣／下雨）與定時 diary 槽供應。
對一個 Canon 全部圍繞關係生活的角色而言，這是結構性的。
**本檔不判斷這是好是壞** —— 但要討論「降頻」，得先知道頻寬目前被誰佔走。

### 2.2 寫入階段：寫入是近同步的，不是 session 結束前

p50 = 2.09 秒。這與兩處既有假設不符：

- 舊 Soul 寫的是「每次對話中偵測到就必須立即寫入」。
- 修訂方案 v1 寫的是「在 session 結束前寫進去」。

實測是**事件產生後約 2 秒就寫**。延遲不是瓶頸。

### 2.3 Retrieval 階段：多數時候沒有東西可取

74.3% 的呼叫連一個 candidate 都撈不到，75% 觸發
`all_rejected_low_confidence` fail-safe。

**但這不能直接判定成 gate 太嚴**：目前的記錄裡沒有「這次查詢的 query 是什麼」，
所以無法區分是「有查詢但被 gate 擋掉」還是「查詢本身沒對應到任何記憶」。
這兩者的處置方向相反，必須先有 query 側的資料才能說。

### 2.4 Interpretation 層：可觀測性已中斷

`data/shadow/shadow_log.jsonl` 最後一筆是 **2026-08-19T12:18:31**，
距今 **49 天無新資料**。`loader_trace.jsonl` 是最新的（2026-10-07 17:50）。

在有資料的那段期間：

| 指標 | 值 |
|---|---|
| 回應總數 | 3,504 |
| `context_provided=true` | 1,142（32.6%） |
| 其中 **0 facts** | **916（80.2%）** |

即：記憶上下文被提供時，八成的情況下抽不出任何事實。

**但因為這一層已停擺 49 天，這個數字不能代表現況**。
「取回後是否影響後續行為／interpretation」這一格，目前無法產出證據。

---

## 3. 代表性樣本

### 3.1 Rem diary 各 slot 樣本（2026-07-20，早期）

```
[dream]  （2026-07-20 夜裡）做了個模糊的夢, 內容記不清。只記得一些光影。
[night]  ```json
         {"timestamp": "...", "time_of_day": "night", "mood": "安らぎ", "weather":
[morning] {"memory_type": "diary", "date": "2026-07-21", ...
[event]  <think>
         The user is setting up a roleplay scene. Let me parse the context:
```

**觀察**：早期 diary 條目是**未經清理的模型原始輸出** —— 含 `<think>` 推理段、
未剝除的 ```json 圍籬、截斷的 JSON。這些條目 `inner_life_event_id` 皆為 none。
後期的條目才是乾淨散文（日文）。這是資料品質觀察，本階段不修。

### 3.2 對話類 InnerLifeEvent 樣本（全 9 筆中的前 6 筆）

```
2026-08-22  actor=session_1696287850_agent_rem      duration=30.1min>=5.0 AND turn_depth=20>=4
2026-08-29  actor=session_1696287850_agent_mahiru   duration=30.6min>=5.0 AND turn_depth=20>=4
2026-09-02  actor=session_1696287850_agent_mahiru   duration=35.5min>=5.0 AND turn_depth=20>=4
2026-09-03  actor=session_1696287850_agent_mahiru   duration=35.4min>=5.0 AND turn_depth=20>=4
2026-09-14  actor=session_1696287850_agent_mahiru   duration=35.5min>=5.0 AND turn_depth=20>=4
2026-09-14  actor=session_bryan_test_agent_yua      duration=35.4min>=5.0 AND turn_depth=20>=4
```

---

## 4. 必須先補的可觀測性缺口

這兩格沒有它們，「降頻是否退化」永遠算不出來：

1. **Submission gate 沒有獨立記錄。** 目前只能看到 InnerLifeEvent 存在與否，
   看不出「產生之後、被寫入之前」發生了什麼。若降頻發生在這一層，
   現在的資料會顯示成「寫入變少」，但原因不可見。

2. **Interpretation 層（記憶是否影響行為）已停擺 49 天。**
   `shadow_log` 停在 2026-08-19。在修復前，任何「取回後有沒有形成 continuity」
   的結論都只能用 8 月以前的資料 —— 那是降頻**之前**的狀態，
   正好不能用來當降頻後的對照。

---

## 5. 下一階段要算「漏寫」還缺什麼

本階段**故意沒有**產出漏寫率。要算它，必須先定義應寫集合：

| 待定義 | 為什麼現在算不出來 |
|---|---|
| 什麼樣的 lived experience 值得留下 | 目前只有「實際寫了多少」，沒有「應該寫多少」 |
| 對話事件的門檻是否合理 | `duration≥5min AND turns≥4` 是既有實作，但沒有對照組說明它是否達到目的 |
| world 事件佔 73% 是否為預期 | 這是產品決策，不是觀測能回答的 |
| 降頻的目標量級 | 沒有基線偏好值，無從談「降」 |

**建議順序**：先定義應寫集合 → 再量漏寫 → 最後才談 downsampling。
反過來做只會得到一個沒有基準的數字。

---

## 6. 可重跑

```
.venv\Scripts\python.exe harness\memory_baseline_observation.py
.venv\Scripts\python.exe harness\memory_baseline_observation.py --skip-inner-life   # 快取模式
```

工具唯讀，輸出只落 `tests/results/memory_baseline/`。