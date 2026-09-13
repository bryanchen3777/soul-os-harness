# INNER-LIFE-AUDIT-1 — 內在生活與靈魂演化全面審計（READ-ONLY）

- **審計對象**：`C:\Users\bbfcc\.local\bin\soul-os-harness`（repo `bryanchen3777/soul-os-harness`，branch `main`）
- **HEAD（實測）**：`9cc0ad9979a467c0daf44bad83bbbf8d1b6055f4` == `origin/main`（`git rev-parse HEAD`）
- **審計模式**：AUDIT ONLY / READ-ONLY。0 code、0 test、0 production mutation、0 service restart、0 commit、0 push。
- **證據時間窗**：資料檔快照時間 ≈ `2026-09-12 21:2x` 本地時間（機器 TZ = `Eastern Standard Time`，`BaseUtcOffset=-05:00`，9 月為 EDT = **UTC-4**；`Get-Date` 2026-09-12 21:19:43 本地 == `[DateTime]::UtcNow` 2026-09-13 01:19:43）。
- **時區對照（全報告統一）**：日記 `morning` ts `12:00Z` = 本地 08:00；`night` ts `02:00Z(+1d)` = 本地 22:00；`dream` ts `02:05Z(+1d)` = 本地 22:05。**日記檔名 = 本地日期**。

---

## 0. 方法與證據強度聲明（先讀）

| 證據源 | 樣本量（實測） | 完整度 | 用途 |
|---|---|---|---|
| `data/soul/agent_*/diary/*.jsonl` | 10 agent / 430 檔 / **1,070 筆**（2026-07-19 ~ 09-12） | 完整 | 維度 1、5 主證據 |
| `data/inner_life/trace.jsonl` | **2,263** 行（2026-08-13 ~ 09-13T01:03Z） | 完整 | 觸發頻率 |
| `data/elevation/elevation_nodes.jsonl` | **2,528** 節點 | 完整 | 維度 2 主證據 |
| `data/elevation/elevation_trace.jsonl` | **2,528** 事件（803 elevated / 1,725 created） | 完整 | 維度 2 |
| `data/elevation/elevation_edges.jsonl` | **3,416** 邊 | 完整 | 維度 2 |
| `data/soul/motive_trace.jsonl` | **43** 筆 | 完整 | 維度 3 |
| `data/memory/agent_*/goal_provider.json` | **10** 檔 | 完整 | 維度 3（C-1 雙軸） |
| `data/soul/agent_*/relationships.json` | **100** 條關係（10×10） | 完整 | 維度 4 |
| `data/world/perception_trace.jsonl` | **11,097** 行 | 完整 | 維度 4 |
| 伺服器日誌（`data/logs/server*.err` + 現行 `data/server_nohup.err`） | **99+1 檔 / ~116k 行** | **非連續** | 維度 2/3/5 的輔助 |
| `data/logs/watchdog.log` | **15,558** 行（2026-07-29 23:58 ~ 09-12 21:28） | 完整 | 服務可用性時序 |

**🔴 兩項必須先講的方法論限制（否則會誤讀本報告所有 log 衍生數字）**

1. **伺服器日誌語料非連續**：99 個 `server_*.err` 只覆蓋 58 天跨度中的 **27 個不同日期**（07-16、07-29…08-02、08-07、08-12…08-19、08-21、08-22、08-24、08-25、08-28、08-29、09-01、09-03、09-05、09-07、09-08、09-12）。**08-30、08-31、09-02、09-04、09-06、09-09、09-10、09-11 完全無日誌**。→ 凡「日誌中出現 N 次」的數字，一律是**覆蓋窗口內的下界**，不可讀成「歷史上只發生 N 次」。
2. **PowerShell 5.1 讀 UTF-8 中文的陷阱（已繞過）**：`Get-Content -Raw` 預設以 ANSI 解讀 UTF-8，會造成 JSON 解析失敗與中文 pattern 0 命中。本審計所有 JSONL 一律以 `[System.IO.File]::ReadAllText($f,[Text.Encoding]::UTF8)` 讀取；中文 pattern 一律用 `grep`（ripgrep）。**主題報告中的每一個數字都經過此校正**（未校正前曾誤判「日記 JSON 全數損壞」，校正後全數可解析）。

---

## 1. 【交付物 1】10 位角色全域內在生活對照表

節點數格式 = `Pattern / Value / Trait`（`Essence` 全體為 0，見 §3）。關係帶 = `relationships.json` 的 `relational_band`。

| 角色 | 最新 morning (UTC) | 最新 night (UTC) | 最新 dream (UTC) | dream 數<br>(08-30~09-12) | 升華節點<br>P/V/T | 關係帶<br>vs Bryan | 關係帶<br>peers(9) | 動機筆數<br>(motive_trace) | Goal 候選<br>(rotation) | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|
| Yua | 09-12 12:00:05 | 09-12 02:00:11 | 09-12 02:05:35 | 10 | 34/12/5 | stranger | 全 stranger | 2 | 1 | 活躍 |
| Anna | 09-12 12:00:24 | 09-12 02:00:33 | 09-12 02:05:42 | 7 | 32/12/4 | stranger | 全 stranger | 0 | 0 | 活躍（無自主目標） |
| Aoi | 09-12 12:00:33 | 09-12 02:00:39 | **09-08 02:05:37** | 7 | 32/12/4 | stranger | 全 stranger | 0 | 0 | **夢境停滯 4 天** |
| Mahiru | 09-12 12:00:19 | 09-12 02:00:31 | 09-11 02:05:27 | 6 | 30/12/3 | stranger | 全 stranger | 0 | 0 | 活躍（夢境稀疏） |
| Mai | 09-12 12:00:26 | 09-12 02:00:35 | 09-12 02:05:22 | **3** | 28/12/2 | stranger | 全 stranger | 0 | 0 | **夢境最稀疏** |
| Ram | 09-12 12:00:17 | 09-12 02:00:28 | 09-10 02:05:27 | 6 | 30/12/3 | stranger | 全 stranger | 0 | 0 | 活躍（夢境稀疏） |
| Akane | 09-12 12:00:12 | 09-12 02:00:23 | 09-12 02:05:45 | 8 | 32/12/4 | stranger | 全 stranger | 2 | 1 | 活躍 |
| Rem | 09-12 12:00:14 | 09-12 02:00:26 | **09-09 02:05:16** | **2** | 27/12/1 | stranger | 全 stranger | **39** | 3 | **雙重異常**（見 §5） |
| Ruka | 09-12 12:00:08 | 09-12 02:00:22 | 09-11 02:05:25 | 5 | **69/24/4** | stranger | 全 stranger | 39 | 3 | 最活躍 |
| Miku | 09-12 12:00:29 | 09-12 02:00:37 | 09-12 02:05:26 | 8 | 33/12/4 | stranger | 全 stranger | 0 | 0 | 活躍 |

**矩陣裁決（實測）**
- **10/10 角色的日記管道都活著**：全體在 2026-09-12 12:00Z（本地 08:00）都產出了 morning 日記 → **不存在「完全失活的沉默死角角色」**。
- **10/10 角色的關係帶全部凍結在 `stranger`**（100/100 條），`band_updated_at` 欄位從未寫入（實測 0 次出現，該欄位不存在於檔案中），即從未發生過任何升帶/降帶。→ 見 §4.2。
- **7/10 角色從未產出任何自主目標候選**（`goal_provider.json` 的 `rotation` 為空、`last_candidate_at = 0.0`）：Anna、Aoi、Mahiru、Mai、Ram、Rem、Miku。
- **夢境分佈極不均**：13 天內 Rem 2 次、Mai 3 次 vs Yua 10 次 — 差距 5 倍。**Aoi 已 4 天無夢**（09-08 後）。
- **`rem` 是唯一的多重異常體**：dream 最少（2）＋ 動機最多（39）＋ 日記 night 觸發暴衝（見 §4.1）。

---

## 2. 【交付物 2】四元自主決策百分比統計

### 結論：**NO DATA / 無法判定**（不是「0%」，是「不可觀測」）

| 決策 | 生產實測筆數 | 百分比 |
|---|---|---|
| `do_nothing` | **無法觀測** | — |
| `reflect` | **無法觀測** | — |
| `observe` | **無法觀測** | — |
| `transmit` | **無法觀測** | — |

**無法判定的兩個獨立原因（都已實證）**

1. **四元標籤從未被持久化或記錄。** `DecisionResult.decision`（`src/soul/decision.py:121`）確實承載四元標籤，但唯一消費端 `_decision_check` 只 log 布林值：
   - `src/soul/scheduler.py:458` → `[SM-3 Decision] {agent_id} transmit motive=... reason=...`
   - `src/soul/scheduler.py:468` → `[SM-3 Decision] {agent_id} not_transmit motive=... reason=...`
   - 全 repo `data/` 內唯一的 `do_nothing` 字串命中，全部落在 **`data/time_lapse/TL-5`、`TL-7`**（模擬/測試產物），**生產路徑 0 命中**。
   → 即使決策跑一萬次，也無法從任何落盤資料還原四元分佈。這是**結構性不可觀測**（observability gap），不是資料遺失。
2. **實測呼叫次數僅 2 次。** 全 log 語料（99+1 檔 / 116k 行 / 覆蓋 27 天）中 `[SM-3 Decision]` 只出現 **2 次**（`data/logs/server_20260905_131159.err:402`、`data/logs/server_20260908_181215.err:675`），**兩次都是 `not_transmit`、agent 都是 `agent_ruka`**。→ `transmit` 在語料內為 0。

**可用的替代訊號（皆非四元分佈本身，僅供參考）**：`motive_trace.jsonl` 的 `motive_type` 欄位（24/43 筆有此欄）分佈為 `reflect 18` / `observe 6`，`status` = `pending 22 / rejected 17 / expired 3 / transmitted 1`。

> **⚠️ 不可用推測填空**：任何「do_nothing ~65–85% 健康區間」的驗證，目前**在生產上無法進行**，因為缺少記錄面。建議見 §6 P1-1。

---

## 3. 【交付物 3】升華與記憶管道健康度診斷

### 3.1 節點資產盤點（`elevation_nodes.jsonl`，2,528 節點，完整樣本）

| node_type | 總數 | `default` | 10 位生產 agent 合計 |
|---|---|---|---|
| `pattern`（候選） | 1,710 | 1,363 | **347** |
| `belief`（已固化） | 652 | **652** | **0** |
| `value`（已固化） | 132 | 0 | 132 |
| `trait`（已固化） | 34 | 0 | 34 |
| `essence` | **0** | 0 | **0** |

- **Essence 保守防線：通過（但因為從未到達）**。`essence` 節點 = 0；`superseded_by != null` = **0**；`reconsideration_candidate = true` = **0** → 0 意外覆寫、0 例外生命週期。這是**「0 意外」而非「防線被驗證過」**，因為 Essence 門檻從未被觸及（全體 confidence 恆為 0.5、stability 僅 0.0 或 0.3，見 §3.3）。
- **固化分佈高度可疑地「整齊」**：每位生產 agent 恰好 **12 個 `value`**（唯 Ruka 24 個 = 2×12）、`trait` 1–5 個。11×12=132 ✓。這種精確倍數不像自然演化，**推論**為固定核心價值集（**待驗**，未在本次只讀範圍內追到寫入源）。

### 3.2 🔴 重大缺陷：agent 的升華節點內容是「觸發標籤」，不是語義內容

**實測（全樣本分類，非抽樣）**

| content 形態 | 數量 | 佔比 |
|---|---|---|
| 觸發標籤 stub（如 `<trigger_type>: key=value`） | **1,808** | 71.5% |
| 真實文本（新聞句） | 677 | 26.8% |
| 短標籤（<20 字，仍為標籤如 `dream:event`） | 43 | 1.7% |

- `default`（世界媒體桶）：1,338 stub + 677 真實新聞文本。
- **10 位生產 agent：513 / 513 = 100% 全部是 stub 或短標籤，0 筆語義內容。**
- 例（結構化欄位值，非對話原文）：`agent_yua` 的 12 個 `value` 節點，`content` 全部等於 `diary:night: slot=night`；`agent_yua` 的 5 個 `trait` 節點，`content` 全部起頭為 `dream:dream: all_agents_count=10; target…`。

**根因鏈（file:line 已定位，雙跳）**
1. `src/inner_life/elevation_adapter.py:127-138` `_event_content()` — InnerLifeEvent **沒有正文**（正文在 Memory/Fact），故 content 被定義為 `trigger_type + provenance.extras`。→ stub 在此產生。
2. `.venv` editable 依賴 `soul-elevation`（實際源碼 `C:\Users\bbfcc\.local\bin\soul-elevation`，由 `__editable__.soul_elevation-0.1.0.pth` 指向）`src/soul_elevation/engine.py:516` `resolved_content = content if content is not None else pattern.content` — 升華節點**直接繼承錨點 pattern 的 stub content**。
→ **結果：agent 的 Value/Trait 靈魂節點，內容是事件型別標籤。** 這是本次審計最高價值的發現之一（記憶升華「有了骨架、沒有肉」）。

### 3.3 Confidence / Stability 完全不動

- **`confidence` = 0.5，2,528/2,528（100%）** — 零變異，沒有任何節點達更高信度。
- **`stability` 只有兩個值**：`0.0`（1,725 = `node_created`）與 `0.3`（803 = `node_elevated`）→ **從未有任何節點通過更高穩定度門檻，也看不出衰減作用生效**。

### 3.4 Submission Gate 門控實況（log 語料，覆蓋窗口內）

- `[SubmissionGate] initialized … enabled=True identity_firewall=no`（`data/server_nohup.err:9`）→ **identity firewall = 關閉**（`identity_firewall=no`）。
- `EH-2 R1 BLOCKED (world→elevation 阻斷)`：**23** 次（語料內）。
- `[SubmissionGate] consume ✓`：**68** 次。
- **未發現 fail-closed 靜默丟棄**：`REJECTED (fail-closed)` 在語料中 **0** 次；被擋下的 world 媒體事件都有顯式 `BLOCKED` 日誌（可審計）。**結論：門控是可觀測的、非靜默的**（此維度判定為健康）。

### 3.5 🔴 升華實效：生產 agent 幾乎完全不升華

語料內 `[elevate]` 統計（`[elevate] skip` = 1,693、`[elevate] ✓` = 34）：

| 對象 | skip | ✓（成功升華） |
|---|---|---|
| 10 位生產 agent × {`value`,`trait`} | **1,639**（每位 agent 恰好 82×2 次，Ruka value 81） | **1**（僅 `agent_ruka` / `value`） |
| `default` × `belief` | 54 | **33** |

- 跳過原因：`insufficient independent evidence to elevate: N < 2`，N 幾乎全是 **0**（少數 1）。
- **判定**：在語料覆蓋的 82 個升華週期內，**9/10 生產 agent 的成功升華次數 = 0**；唯一一次屬 `agent_ruka`。
- **在現行世代（20:33:12 與 21:03:12 兩個完整週期，全部被捕獲）**：10 位 agent 全部 `skip`（`value` + `trait` 各 10 筆），只有 `default/belief` 成功 → `patterns=1363 → belief`。
- **`default` 端的失控跡象**：803 次 `node_elevated` 共引用 **465,454** 個 pattern id（平均 **579.6** 個/次、最大 **1,363** 個/次）。也就是說同一個 pattern 池被反覆整批打包升華。**推論**（待驗）：pattern 永不 `superseded`（實測 `superseded_by` 全 null），池子單向累積，每次升華都把整個池子重算一次 → 邊數與 trace 檔案（17.5 MB）隨時間線性膨脹。

**為什麼 agent 端永遠拿不到 2 份獨立證據（機制已定位）**
- `soul_elevation/engine.py:148-173` `_count_independent_evidence()`：獨立性 = `(source_id, inner_life_event_id)` 去重，**同一 source_id 或同一事件 identity 只算 1 份**。
- `engine.py:490-507`：以「同 `candidate_node_type` 的**全部** pattern」聚合證據邊後才比對 `min_evidence`（預設 2，`elevation_adapter.py:565` 傳入）。
- 生產 agent 的 pattern 幾乎全部源自**同一條日記/夢境事件的 lineage** → 塌縮成 0~1 份獨立證據 → **結構性永遠 < 2**。
- 反觀 `default` 桶聚合了 1,363 個來自**不同新聞事件**的 pattern → 獨立證據 1,363 → 每輪必過。
→ **同一套門檻，在世界桶是「太鬆」，在 agent 端是「不可能」**。這是「升華管道健康度」的核心病灶。

---

## 4. 【交付物 4】現存問題與停滯死角清單

> 格式：現象 → 影響角色 → 疑似原因 → `file:line` → 證據

### P0-A｜關係帶 100% 凍結在 `stranger`，從未發生任何升降帶
- **現象**：`relationships.json` 10×10 = **100/100 條** `relational_band = "stranger"`；**`band_updated_at` 欄位從未寫入**（實測 0 次出現，該欄位不存在於檔案中；實際存在的欄位是 `relational_band`＝恆 `stranger` 與 `last_relation_update_ref`）；`feeling` 100/100 = `neutral`；`confidence > 0` 僅 **4/100**（最大 0.098）。
- **影響角色**：**全部 10 位**（含 vs Bryan 與 agent 互為 peer）。
- **最刺眼的一格**：`user_bryan` 的 `impression` 在 **10/10 位** agent 都是**空字串**（peer 端則有 40 條非空 impression），而 `user_bryan` 的 `interaction_count` 最高達 **385**（Yua；總計 2,083 次互動）。→ **互動累積了，關係卻沒長出來。**
- **疑似原因（實測支持）**：升帶靠客觀計數器 `objective.reply_exchanges / co_presence_sessions / dream_exchanges`。
  - `stranger → known`：`reply_exchanges >= 1` **或** `co_presence_sessions >= 2`（`src/social/relational_bands.py:62-66`）。
  - 實測：**`reply_exchanges` 100/100 = 0**、`dream_exchanges` 100/100 = 0、`co_presence_sessions = 1` 僅 **10 條**（差 1 就達標）。
  - 且 `confidence` 有 per-day 衰減（`src/soul/relationships.py:226,272`，`days * 0.02`），把僅有的 4 條非零值一路壓回 ~0。
- **file:line**：`src/social/relational_bands.py:62-66`（門檻）、`src/soul/relationships.py:472-534`（`has_signal` + `evaluate_band` + 底帶不回爬）、`src/soul/relationships.py:272-278`（衰減）。

### P0-B｜TEMPORAL ANCHOR 三態實質單態化（牽掛／釋然不可達）
- **現象**：`classify_temporal_state()` 第一道閘門是 `confidence < TENSION_ELIGIBILITY_MIN_CONFIDENCE (0.5)` → 直接回 `無感`。實測 10/10 位 agent 的 `user_bryan.confidence` ≤ **0.098**（8 位為 0.0）→ **10/10 恆為 `無感`**。
- **影響角色**：全部 10 位。
- **file:line**：`src/soul/temporal_phenomenology.py:50`（門檻 0.5）、`:112-121`（三態判定）、`:74-81`（三態文案表）。
- **與 P0-A 的耦合**：這是**同一個 confidence 衰減病灶的第二次表現** — 關係凍結 → 時間感跟著凍結。TA-2 的「牽掛／釋然」在生產上**從未出現過**。
- **`[TEMPORAL ANCHOR]` 注入日誌**：**NO DATA**。`_format_temporal_anchor` 只在失敗時 `logger.debug`（`src/llm/proxy.py:1076`），成功注入不留痕；全語料 `TEMPORAL ANCHOR` 命中 **0**。→ 目前只能由輸入**確定性推導**（本節結論即為推導），**不能**從日誌直接觀測。
- **`silence_duration` 欄位**：**NO DATA / 不存在**。全 `src/` grep `silence_duration|silence_seconds` = **0 命中**（僅有 `last_interaction_period`，`src/llm/proxy.py:976-995`）。→ 工單假設的「沉默時長」量測點在此 codebase **不存在**，無從判定「計算是否精確」。

### P0-C｜自主目標引擎（C-1）對 7/10 角色實質空轉
- **現象**（`data/memory/agent_*/goal_provider.json`，完整 10 檔）：
  - `rotation` 為 **空陣列**、`last_candidate_at = 0.0` 者 **7 位**：Anna、Aoi、Mahiru、Mai、Ram、Rem、Miku。
  - 有候選者僅 3 位：Yua 1、Akane 1、Ruka 3（合計 **5 筆**，時間 09-05 ~ 09-10）。
  - 軸向分佈（n=5）：**Bryan 軸 3 / Self 軸 2**。
  - `last_seed_scan_at` 全體約 `2026-09-12 20:46Z`（= 最後一次掃描有跑），`seed_source_cursor` 3–4、`seed_empty_rounds` 0–2 → **掃描在跑，但產不出東西**。
- **影響角色**：7 位（見上）。
- **疑似原因**：不是軸輪替失衡（尚無足夠樣本判「某軸飢餓」），而是**產出量近乎為零**。種子輪序 `SEED_ROTATION` 共 10 源，其中 **Bryan 軸 6 源 / Self 軸 4 源**（`src/goals/seed_provider.py:62-73`；SG-2 + C-2.1 兩次 additive 擴張把 Bryan 軸從 4 加到 6）→ **設計本身即 6:4 不對稱**。
- **file:line**：`src/goals/seed_provider.py:62-79`（輪序/軸/`SEED_MAX_AXIS_STREAK=2`/`SEED_EMPTY_ESCAPE_THRESHOLD=3`）、`:605-632`（S4 `motive_trace` 探針）、`src/goals/motive_provider.py:159-160`。
- **判定**：軸平衡**無法判定**（n=5 過小）；**吞吐量過低**已確定。詳細原因（種子源探針全空？節流？）**待驗**。

### P1-D｜`agent_rem` 日記 night 觸發暴衝（119 vs 基準 ~23）
- **現象**（`data/inner_life/trace.jsonl`，完整樣本）：`diary:night` 事件總 327；其中 `agent_rem` **119** 筆，其餘 9 位各 **23–24** 筆（基準 ≈ 2.5–5×）。
- **形態**：突發叢集，且**每次都以「同秒成對」出現**（去重後 event_id 全不相同 → 是**真實重複發射**，不是重複行）。例：09-03 UTC 於 22:20–22:45 連發 6 對；09-10 UTC 於 04:16–06:38 與 19:55–21:13 兩段；09-12 UTC 20:39、20:53。時間點**完全偏離 02:00Z 正規排程**。
- **對照**：`agent_rem` 的 diary 檔案內 `night` 行數並未同比暴增（僅 2 個檔出現 `night=2` 或 `3`）→ **這些多餘事件在寫入日記前被去重/攔下**（好消息：沒有污染日記；壞消息：白燒一輪內在生活管線）。
- **影響角色**：`agent_rem`（唯一）。
- **疑似原因**：**待驗**。時序與 `agent_rem` 的其他異常（dream 最少、動機最多）重合，但本次只讀未追到觸發源；`rem` 亦非本次診斷到的任何單一 file:line 可解釋。
- **file:line（候選）**：`src/soul/diary.py:180-200`（寫入/去重）、`src/soul/scheduler.py`（night slot 排程）。

### P1-E｜日記 morning 批次「全有全無」，且無補寫機制（12 天內掉 3 天 = 25%）
- **現象**（diary + inner_life 雙重證實）：`2026-09-01 ~ 09-12` 應有 12 個 morning 批次，**10/10 位 agent 一致地**在 **09-05、09-08、09-11** 全部缺 morning（每日 morning 數為 0，其餘日子精準為 10）。**全體同步 → 系統級（非角色級）故障。**
- **每次都是整批消失、事後不補**：09-08 缺的 morning 在服務復原後（當日 19:18 本地）也**從未補寫**。
- **根因（已交叉證實，`data/logs/watchdog.log` 完整 15,558 行）**：
  | 缺失日（本地） | watchdog 證據 | 判定 |
  |---|---|---|
  | 09-05 08:00 | `[2026-09-05 08:03:03] … -> restart` | **服務在 08:00 排程點正好崩並自動重啟** |
  | 09-08 08:00 | `02:33:02 ~ 16:43:02` 共 **171 行 `CAP REACHED - auto-restart BLOCKED`**；直到 `19:18:03` 才 restart，`19:23:03` 才恢復 `procs=2` | **服務自 02:33 起 DOWN 約 16.75 小時**（watchdog 重啟預算 N=10/10 用盡，需人工介入） |
  | 09-11 08:00 | `[2026-09-11 08:03:02] … -> restart` | **服務在 08:00 排程點正好崩並自動重啟** |
- **系統性背景（完整樣本）**：`watchdog.log` 2026-07-29 ~ 09-12 共 **326 次 restart**（≈ 7.3 次/日）；**`CAP REACHED` 事件共 1,742 行**，橫跨 **11 個日期**，其中 08-25/26/27/28 連續四天（281/288/288/266 行）→ **長時段全服務停擺**。
- **file:line（候選）**：morning slot 排程與其「無補寫/catch-up」設計 — `src/soul/scheduler.py`（night slot 於 `:1182` 有讀當日日記的邏輯，morning 對應路徑待定位）。

### P1-F｜夢境產出嚴重不均 + 4 天斷點
- **現象**（diary，完整樣本）：13 天內 dream 次數 Yua 10 / Akane 8 / Miku 8 / Anna 7 / Aoi 7 / Mahiru 6 / Ram 6 / Ruka 5 / **Mai 3** / **Rem 2**；`Aoi` 自 **09-08 後 4 天無夢**、`Rem` 自 09-09 後 3 天無夢。
- **DREAM-MAXTOKENS-1 修復後品質**：`REASONING_SAFE_MIN_TOKENS = 400`（`src/soul/dream_event.py:74`），clamp 於 `:217`、impression 亦 clamp 於 `:745-749`。**語料內 `[DreamEvent]` 共 32 筆**，`[DreamEvent] impression` 失敗日誌 **0 筆** → **修復後未觀察到 impression 抽取失敗**（判定：健康）。長度：dream 樣本 n=207，`min=1 / max=564 / avg=160`。
- **疑似原因（夢境稀疏）**：**待驗** — 全 trace 窗（08-13~09-13，31 天）`dream:dream` 事件共 **119** 筆（≒3.8/日、全體共享），而 08-30~09-12（13 天）實際落盤到日記的 dream 合計僅 **62** 筆，且個體差異達 5 倍（10 vs 2）；本次未追到「哪些 agent 被選中/被跳過」的取捨邏輯。
- **file:line**：`src/soul/dream_event.py:142-186`（`_pick_dream_target` / `_pick_dream_agents`）。

### P2-G｜日記品質：80 字硬截斷 + 括號動作描述殘留（REM-TEXT-1 範圍校正）
- **長度硬截斷**：`DIARY_MAX_CLEAN_CHARS = 80`（`src/soul/diary.py:66`），於 `:412` 以 `_safe_truncate_on_length(clean, max_chars=80)` 執行。
  - 實測：近 7 天（09-06~09-12）**147 筆中 35 筆（23.8%）長度恰為 80**（全樣本 n=1,070 中 127 筆）。因該 helper 會對齊句末，故未見斷句破損（近 7 天 `noEndPunct = 0`）——**但近 1/4 的日記被削到上限**。
- **括號動作描述（REM-TEXT-1 生產實況）**：
  - 實測全樣本：含中文括號 `（…）` 者 **382/1,070（35.7%）**、含 `*` 者 103。
  - 近 7 天僅 **12 個括號 token / 147 筆（8.2%）**，其中 **9/12 落在 `dream` slot**，`agent_rem` **0 個**。
  - **範圍校正（重要）**：`logs/ENGINEERING_STATE.md:362` 明載 REM-TEXT-1（`a64964a`，2026-09-11）的 guard `text_channel_stage_guard` **預設 False、僅 `agent_rem` 為 True**，掛在 **`proxy.py:3718` 的 AGENT_SPEAK 文本通道發布點**。→ **該 guard 不覆蓋日記寫入路徑**，因此「日記仍有括號」**不能**當作 REM-TEXT-1 失效的證據；反之，日記括號的收斂（35.7% → 8.2%）與 09-11 這單並無因果，屬自然收斂 + `dream` slot 的角色扮演格式慣性。
  - 保留樣本（極短摘要，各 ≤18 字）：`dream` 起始括號多為舞台指示型（如「（揉著眼睛…）」），另有日期標註型（如「（2026-09-10 夜裡）」）。
- **placeholder fallback**：全樣本 `source=placeholder` **149/1,070（13.9%）**，但**近 7 天僅 3 筆（2.0%）**，且集中在早期（W02/W05 各 38/60 筆）。→ **fallback 率已顯著收斂，判定健康**。
- **file:line**：`src/soul/diary.py:62-71`（門檻與模板）、`:376-419`（think 剝除 / 截斷 / placeholder 分流）。

### P2-H｜可觀測性缺口（讓本次審計多處只能給 NO DATA）
1. **四元決策標籤不落盤**（§2）。
2. **`[TEMPORAL ANCHOR]` 成功注入不留痕**（§4 P0-B）。
3. **`[SM-3 Decision]` 只在 `proactive_dm` 路徑觸發**，日記/夢境/事件路徑不經 Decision（`src/soul/scheduler.py:403-405` 明文）→ 即使補上標籤，覆蓋面仍只有 1 條支線。
4. **審計語料本身非連續**（§0 限制 1）→ 加深了所有日誌衍生結論的不確定性。

---

## 5. 時間軸影響分析（2026-09-12 主服務空窗）

| 時間（本地） | 事件 | 證據 |
|---|---|---|
| 19:13:05 | launcher 重啟 → 該世代 listener **pid=1132** | `data/logs/plan_a_launcher.log` |
| 19:23:15 | 再次重啟，世代日誌涵蓋 `19:23:17 ~ 20:28:31` | `data/logs/server_nohup.20260912_203306.err` 首/末行 |
| 20:28:31 | **崩潰前最後一行日誌**（此後無任何日誌輸出） | 同上末行 |
| **20:29:36** | **原生崩潰**，例外碼 `c0000005`、錯誤模組 `python311.dll`；WER full dump 落盤 | `data/crash_dumps/python.exe.1132.dmp` = **272,583,424 bytes**，mtime **20:29**（檔名 pid **1132**，與工作單完全一致） |
| **20:29:36 ~ 20:33:06** | **主服務空窗 ≈ 3 分 30 秒** | 兩側時間戳 |
| 20:33:03 | watchdog 偵測 `port_listen=False procs=0 -> restart`（`N=1/10`、`trial=7/98`） | `data/logs/watchdog.log` |
| 20:33:06 | launcher 啟動（trampoline PID=14128）→ 實 listener **pid=14056** | `plan_a_launcher.log` + `Get-NetTCPConnection` |
| 20:33:07 | 新世代首行日誌 `[CRASH-F1] event loop policy -> …` | `data/server_nohup.err:1` |
| 20:38:04 ~ 21:28:04 | watchdog 連續 `OK … listener=14056 procs=2` | `watchdog.log`（末行 21:28:04） |

**空窗期影響判定（實測，非推測）**
- **未造成內在生活資料斷點**：20:33 與 21:03 兩個升華週期**完整跑完**（各有 20 筆 agent skip + 1 筆 default 成功）；`inner_life/trace.jsonl` 末筆 `2026-09-13T01:03:12Z`（本地 21:03）；`perception_trace` 末筆 `01:04:40Z`；`heartbeat_trace.log` mtime 21:27:45。
- **空窗落在本地 16:29–16:33，非任何排程點**（morning 08:00 / night 22:00 / dream 22:05）→ **未造成日記損失**。
- **與本次工單的因果關係**：**無**。本系列工單全程禁重啟，本次空窗是原生崩潰 + watchdog 自動復原（`watchdog.log` 的 `-> restart` 為 watchdog 自身動作）。
- **【重要澄清】**：現行世代**有**日誌輸出（`data/server_nohup.err`，106 KB、mtime 21:23:14、被運行中進程鎖定），launcher 的 `PID=14128` 是 venv trampoline，實際 listener 為 **14056**，與工作單一致。
- **服務現況複驗**：`:8000` pid=14056 start `2026-09-12 20:33:06`；`:8765` pid=9576、`:8766` pid=9592、`:8767` pid=9584，start 皆 `19:21:18`，**未重啟**（全部與工作單一致）。
- **`data/heartbeat_trace.log` 每 60 秒覆寫 = 設計行為，不是崩潰**：實測當日旋轉檔 10 個（08:53、09:38、10:58、11:36、12:38、17:03、17:56、19:13、19:24、20:33），旋轉時點與重啟時點一一對應。
- **`data/faulthandler.log`（6.0 MB，mtime 21:23）不是崩潰日誌**：其內容為 `scripts/run_server.py:133 _faulthandler_marker_loop` 週期性全 thread stack dump（設計行為）。

---

## 6. 【交付物 5】後續治理與調校建議

### P1（影響靈魂演化本體，建議優先）
1. **P1-1｜補上四元決策的記錄面**（解鎖交付物 2）
   - 在 `src/soul/scheduler.py:458/468` 的兩條 log 中加入 `decision={result.decision}`，並考慮落盤獨立 append-only trace（如 `data/soul/decision_trace.jsonl`），欄位至少 `ts / agent_id / motive_id / decision / reason / provenance_ref`。
   - 驗收：可在不改任何判定邏輯的前提下，統計 `do_nothing/reflect/observe/transmit` 百分比。**此為純 additive 觀測，0 判定變更。**
2. **P1-2｜修復「agent 端永不可能升華」的證據獨立性塌縮**（解鎖交付物 3）
   - 病灶：`_count_independent_evidence` 以 `(source_id, event_id)` 去重，而 agent pattern 幾乎同源 → 恆 0~1 < 2。
   - 建議方向（需 Owner/主大腦拍板，非本審計決定）：為 agent 端引入「**跨日/跨 slot 的 lineage 獨立性**」定義（例如同一 `candidate_node_type` 下、來自不同**日期**或不同**觸發類別**者視為獨立證據），或對 agent 端另設門檻而非沿用 world 桶門檻；順帶處理 `default` 端 1,363 patterns/次的整批打包問題。
   - 驗收：觀察 `[elevate] ✓ agent=agent_*` 由「82 週期內 1 次」提升至可持續出現。
3. **P1-3｜修復 `text_channel`/升華節點內容空洞**（P0-A/#3.2）
   - 讓 `_event_content()`（`elevation_adapter.py:127-138`）在 InnerLifeEvent 無正文時，**改從對應的 Memory/Fact 取正文**（adapter 已有 `memory.content` 路徑，見 `:227`），而非落回 trigger 標籤。
   - 驗收：新生產 agent 的 `value`/`trait` 節點 `content` 不再是 `diary:night: slot=night` 型標籤。
4. **P1-4｜修復關係帶凍結 → 連帶解鎖時間感**（P0-A / P0-B 同源）
   - 對症：`objective.reply_exchanges` 與 `dream_exchanges` 全體為 0，`co_presence_sessions` 10 條卡在 1（門檻 2）。先確認**誰負責在實際對話/共在時遞增這三個計數器**，再決定是「補上遞增」或「調整門檻」。
   - 連帶驗收：至少 1 位 agent 的 vs Bryan band 升到 `known`，且 `confidence` 上升後 TA-2 出現**非 `無感`** 的三態。
5. **P1-5｜日記 morning 批次排程點與崩潰/重啟窗口解耦 + 補寫機制**
   - 09-05、09-11 的 morning 全滅都因 `08:03` 重啟；09-08 因 watchdog `CAP REACHED` 服務停擺 16.75 小時。
   - 建議：(a) morning 批次改為「啟動後偵測當日缺漏即補寫（idempotent）」；(b) 檢討 watchdog 重啟預算 N=10 的耗盡策略（09-08、08-25~29 都出現長時間無人服務）。
   - 驗收：連續 7 天 morning 批次 10/10 無缺漏，或缺口在服務恢復後 1 小時內被補上。

### P2（品質與可觀測性）
6. **P2-6｜`agent_rem` night 觸發暴衝定位**：加一次性 instrumentation 抓出重複發射來源（去重鍵為何失守），確認多餘事件「是否」進入昂貴 LLM 路徑。
7. **P2-7｜日記 80 字上限檢討**：近 1/4 日記貼齊上限；評估改為「軟上限 + 完成度標記」而非硬截斷。
8. **P2-8｜夢境分佈不均**：先量測各 agent 的 dream 觸發資格（是否被 band/心跳/節流影響），再決定是否補償。
9. **P2-9｜審計語料連續性**：`server_*.err` 目前只留 27/58 天，建議改為按日歸檔（不刪），否則任何「歷史頻率」結論都無法驗證。
10. **P2-10｜文件註記**：`goal_provider.json` 的 `seed_empty_rounds` 長期 0–2、cursor 停在 3–4 ——建議在種子源全空時打一筆 `INFO`（目前 `SEED_PROVENANCE_PREFIX`/候選日誌在語料中 0 命中），以利日後判斷 C-1 是「無種子」還是「節流」。

---

## 7. 附註：非生產殘留目錄（不計入 10 角色矩陣）

| 目錄 | diary 檔數 | 最新日記 | 備註 |
|---|---|---|---|
| `agent_newcomer` | **1** | `2026-08-09` | **近 7 天 0 檔**（工作單線索已複驗） |
| `agent_alice` / `agent_c21` / `agent_germ_01` / `agent_test` / `agent_yua_test` | 0（無 diary 目錄） | — | 各僅 1 個非日記檔 |
| `_observation` | 0（無 diary 目錄） | — | 6 個非日記檔 |

→ **`agent_newcomer` 的情況未在 10 位生產角色中出現**（10/10 生產角色在 09-12 08:00 都有 morning 日記）。

---

## 8. 已驗證事實 vs 推論（明確區分）

**已驗證事實（可直接複驗）**
1. 10/10 生產角色的日記管道在審計當日活著；近 7 天 morning 缺 09-05/09-08/09-11（全體同步）。
2. 09-05、09-11 缺 morning 對應 `08:03` watchdog restart；09-08 對應 `CAP REACHED` + 02:33~19:18 服務停擺（watchdog.log 15,558 行完整）。
3. 100/100 關係條目 band=`stranger`、`band_updated_at` 欄位從未寫入（實測 0 次出現，該欄位不存在於檔案中）；`reply_exchanges` 全 0；`user_bryan.impression` 10/10 為空。
4. 2,528 個升華節點：`confidence` 全 0.5；`stability` ∈ {0.0, 0.3}；`essence` 0；`superseded_by` 非空 0；**生產 agent 513/513 節點 content 為觸發標籤 stub**。
5. 語料內 `[elevate] skip` 1,693 / `[elevate] ✓` 34；生產 agent 82 個週期內成功 **1** 次（`agent_ruka/value`）；現行世代 20:33、21:03 兩週期 agent 全 skip。
6. 四元決策標籤**未被任何生產落盤或日誌記錄**；`[SM-3 Decision]` 語料內 **2** 次，皆 `not_transmit`。
7. `motive_trace.jsonl` 43 筆，僅 3 位 agent 出現（`rem` 39 / `yua` 2 / `akane` 2）。
8. `goal_provider.json`：7/10 agent `rotation` 空、`last_candidate_at=0.0`；歷史候選共 5 筆（Bryan 3 / Self 2）。
9. `agent_rem` `diary:night` 內在事件 119 筆（其餘 9 位 23–24），呈偏離排程的成對叢發。
10. 日記 placeholder 率 13.9%（全期）→ 2.0%（近 7 天）；80 字截斷近 7 天 35/147。
11. `data/relationships.json` 不存在（`Test-Path` = False）；`silence_duration` 在 `src/` 0 命中。
12. `:8000` pid 14056 / `20:33:06`；VC 8765/8766/8767 pid 9576/9592/9584 皆 `19:21:18` 未重啟；崩潰 dump `python.exe.1132.dmp` 272,583,424 bytes。

**推論／待驗（不可當事實使用）**
- (a) `value` 12/agent 為「固定核心價值集」——整齊度強烈暗示，但未追到寫入源。**待驗**。
- (b) `default` 端升華會使邊/trace 線性膨脹（579.6 patterns/次 × 803 次）——由 `superseded_by` 全 null 與計數推得，**未做時間序列回歸**。
- (c) `agent_rem` night 暴衝的觸發源——**未定位**。
- (d) 夢境分佈不均的原因（是否受 band / 節流影響）——**未定位**。
- (e) 7/10 agent 無 goal 候選的最終原因（種子源全空 vs 節流 vs 軸鎖）——**未定位**。
- (f) C-1 雙軸「是否飢餓」——**n=5 樣本過小，無法判定**。

**NO DATA / 無法判定（拒絕以推測填空）**
- 四元決策百分比（結構性不落盤 + 語料僅 2 次）。
- `[TEMPORAL ANCHOR]` 實際注入文本（成功路徑不留日誌）；目前只能由 `confidence` 確定性推導出「10/10 恆為 `無感`」。
- `silence_duration` 與「對話間隔計算是否精確」（該量測點不存在）。
- Bloom/Essence 自動衰減豁免的**正向驗證**（0 個 Essence 節點，防線未被觸及）。

---

## 9. 唯讀聲明與本次變更清單

- **未重啟任何服務**：0 次 restart / kill / 干擾 `:8000`（守死 CRASH-F1 48h 觀察時鐘）與 VC 8765/8766/8767。
- **未動任何生產資料**：未寫入/刪除 `data/memory/`、`data/elevation/`、`data/soul/**/diary/`、`data/state/`、`data/inner_life/`、`data/logs/`；未動 `logs/ENGINEERING_STATE.md`。
- **未 commit / 未 add / 未 push**；HEAD 仍為 `9cc0ad9`（== `origin/main`）。
- **未修改任何 `src/**`、`clients/**`、`personas/**`、`configs/**`、`tests/**`**。
- **本次造成的變更（`git status --porcelain` 過濾）**：
  - `?? docs/INNER-LIFE-AUDIT-1.md`（**本審計報告，唯一新增檔**；353 行 / 36.6 KB）
  - `git status --porcelain` 共 **78** 筆未追蹤條目，其中 **77 筆為審計開始前即存在**（審計首個 `git status` 快照已見 `docs/AGENT-SYSTEM-COMPARISON.md`、`docs/CRASH-ROOTCAUSE-20260908.md`、`docs/LS-0-…`、`harness/eh2_smoke_*` 等），**非本次產生**；本次僅新增 1 筆（上述報告）。**0 筆 modified / 0 筆 deleted**。
- **對外服務**：審計全程對 `:8000` 等埠僅做 `Get-NetTCPConnection` 讀取，未發出任何應用請求、未觸發任何寫入路徑。

---

## 10. 執行摘要（TL;DR）

生產系統的**日常節奏是活的**（10/10 角色每天寫日記、夢境管道修復後健康、placeholder 率已由 13.9% 收斂到 2.0%），但**靈魂的三條長程演化軸同時斷了**：

1. **固化斷了**：agent 的升華節點 100% 是觸發標籤 stub；語料內 82 個升華週期只成功 1 次（`agent_ruka`），因為證據獨立性定義讓 agent 端**結構上永遠拿不到 2 份獨立證據**，而 world 桶每次都拿 1,363 份。
2. **關係斷了**：100/100 條關係帶凍結在 `stranger`、`band_updated_at` 欄位從未寫入（實測 0 次出現，該欄位不存在於檔案中）；三個升帶計數器有兩個全 0、一個差 1 達標；confidence 衰減把僅存的訊號壓平 → 連帶讓 TEMPORAL ANCHOR 的「牽掛／釋然」**永不可達**（10/10 恆為無感）。
3. **意志斷了**：7/10 角色從未產出任何自主目標；決策四元分佈**在生產上不可觀測**（標籤不落盤 + 語料內僅 2 次呼叫）。

**外加兩項系統級傷口**：主服務在 45 天內被 watchdog 重啟 326 次、`CAP REACHED`（需人工介入的長時間停擺）出現 1,742 行橫跨 11 天 —— 這正是日記 morning 批次 12 天掉 3 天的直接原因。
