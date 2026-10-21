# SIM-EPIS-PIGGYBACK-1 — 實作紀錄

> **工單**：`logs/SIM-EPIS-PIGGYBACK-1-WORK-ORDER.md`（決策完備版）
> **分支 / 前置**：`main` @ `fe3367b`
> **日期**：2026-10-21
> **交付邊界**：全部在**可控虛擬沙盒**（假時鐘 ＋ 記憶副本 ＋ socket 攔阻）內完成並驗證；
> **不部署、不改生產資料、不 commit／push**。

---

## 0. 一句話

建成**兩條零打擾的閉環出口**：
① **自主求知行動**（張力分支上的多路徑展開 → 命中證據才閉合 → 全程 0 發訊）；
② **被動掛載隊列**（已閉合的小念頭進槽 → 只在話題相關時順嘴帶 1 個 → 帶出即消耗）。

結構核心是 **Branching（set-valued transition）**，**不是** Agency／自主性
（DCG 收斂 Round 5/5：「Agency → NOT A CONSTRUCT」）。

---

## 1. 改動檔案清單

| # | 檔案 | 動作 | 說明 |
|---|---|---|---|
| 1 | `src/inner_life/epistemic_action.py` | **新增**（1041 行） | 自主求知行動的決策層：觸發判定 ＋ 多路徑展開 ＋ 閉合判定 ＋ 張力狀態承載 |
| 2 | `src/inner_life/piggyback_queue.py` | **新增**（424 行） | 話頭掛載槽狀態機：入隊／過期／挑選／消耗／隔離 |
| 3 | `src/inner_life/recall_prompt.py` | **修改**（+60 行） | 新增 `【話頭】` 獨立區塊的呈現組裝（既有 `【回憶】` 區塊逐字未動） |
| 4 | `src/llm/proxy.py` | **修改**（+106 行） | ① 背景行動接線（daemon thread、5 分鐘節流、fail-silent）② 話頭區塊注入點（group ＋ private 兩條 `system_parts` 路徑） |
| 5 | `tests/inner_life/test_epistemic_action.py` | **新增**（558 行） | 守衛 ＋ 漂移守衛 ＋ AST 紅線守衛 |
| 6 | `tests/inner_life/test_piggyback_queue.py` | **新增**（254 行） | 守衛 ＋ 隔離守衛 |
| 7 | `tests/inner_life/test_epistemic_piggyback_sandbox.py` | **新增**（579 行） | 沙盒端到端 ＋ 10 項守衛 |
| 8 | `logs/SIM-EPIS-PIGGYBACK-1-IMPLEMENTATION.md` | **新增** | 本檔 |
| 9 | `tests/test_observability_completeness_1.py` | **修改**（+15/−1） | 🔴 **逐票更新的 scope 清單**：本 repo 的 `allowed` 集合由每張票各自擴充（前例 `4af93ef` / `cac7c74` / `c3520cb`）；本票新增 4 個 `src/` 檔。`frozen_dirs` 永久紅線**逐字未動** |

> 🔴 **未動**（`git diff --name-only HEAD` 實測）：`src/soul/scheduler.py`、
> `src/soul/decision.py`、`src/inner_life/epistemic_appraisal.py`、
> `src/soul/temporal_phenomenology.py`、`src/goals/**`、`src/agent/emotion.py`、
> `src/inner_life/submission_gate.py`、`src/inner_life/elevation_recall.py`、
> `src/social/**`、`src/world/middleware.py`、`src/memory/sage/writer.py`、
> `data/**`、`personas/**`、`configs/**`。
> **未 commit／未 push**（只 `git add` 進 index，讓 repo 的「測試檔必須 git 追蹤」護欄看得到新檔）。

---

## 2. 設計落點（決策已定，本票只做落實）

### 2.1 自主求知行動（`epistemic_action.py`）

```
觸發原料（只兩個既有通道，只讀引用）
  ├─ epistemic_appraisal.appraise(...) → 非 None ⇒ 未閉合資訊缺口
  └─ temporal_phenomenology.classify_temporal_state(...) == STATE_TENSION ⇒ 張力／牵挂
        ▼
should_act(tensions)              ⇒ 存在未閉合張力才 True（不看想念值、不看門檻）
        ▼
expand_paths(tension) → 3 條分支（set-valued transition）
  ├─ MEMORY_RECALL（翻記憶找線索）   可查性＝張力有沒有記憶詞元
  ├─ WORLD_VERIFY （查世界事件佐證） 可查性＝張力有沒有世界詞元
  └─ SELF_REASON  （自我提問收束）   恆可判定（判據＝張力自帶的狀態向量）
        ▼
close_tension(...) 逐條分支探測 → **第一條實際命中證據者**才閉合
  沒有一條命中 ⇒ 不閉合（不產生事件、不標 resolved、不入隊）
        ▼
閉合時產出三件（缺一不可）
  1. epistemic_closure 事件（InnerLifeWriter ＋ NarrativeTraceWriter ＋ SubmissionGate）
  2. 張力標記 resolved（寫回自家狀態承載）
  3. 可掛載小念頭（帶 topic tags）→ 話頭掛載槽入隊
```

- **分支數 ≥ 2 是結構事實**：三條分支恆在，`available` 標記可查與否
  （即使沒有可查輸入，分支仍在，只是帶「不可查」理由）⇒ 「同一張力展開成多條」不是運氣。
- **0 LLM**：模組原始碼 AST 守衛：無 `openai/anthropic/litellm/httpx/requests/aiohttp`
  匯入、無 `complete/chat/chat_completion/invoke/generate` 呼叫。
- **原料不經過 0.3 門檻**：模組原始碼 AST 守衛：識別字／非 docstring 字串常值
  **完全沒有** `longing`（⇒ 不可能讀想念值）；`LONGING_THRESHOLD` /
  `LONGING_CHECK_INTERVAL_MINUTES` / G4 靜音時段逐字未動。

### 2.2 被動掛載隊列（`piggyback_queue.py`）

| 規則 | 值 | 落點 |
|---|---|---|
| 狀態 | `{tags, payload, created_at, expires_at, source}` ＋ `refs`（隔離引用） | `PiggybackItem` |
| 入隊來源 | 只接受 `source="epistemic_closure"` | `enqueue()` |
| 容量 | 每 agent FIFO，上限 **8** 筆，滿 ⇒ 丟最舊 | `PIGGYBACK_QUEUE_CAPACITY` |
| 過期 | `created_at + 7 天`（**具名新常數**，不引用上游私有常數） | `PIGGYBACK_TTL_SECONDS` |
| 挑選 | 與當次話題 tags **重疊 ≥ 1**（正規化詞元集合交集）才可選 | `pick_for_topic()` |
| 一次上限 | **1** 個 | `PIGGYBACK_MAX_PER_TOPIC` |
| 消耗 | 帶出即從佇列移除 | `pick_for_topic()` |
| 隔離 | 隔離節點／事件**不得進隊、不得被帶出** | `enqueue()` ＋ `pick_for_topic()` |

### 2.3 呈現與接線（`recall_prompt.py` ＋ `proxy.py`）

- **獨立區塊** `【話頭】`（`PIGGYBACK_BLOCK_HEADER`），**不混入** `【回憶】`、**不進** EMERGENT block。
- **注入位置**：`system_parts` 組裝路徑上，RECALL 之後、CHRONO 之前（group ＋ private 兩條路徑一致）。
- **旗標**：`SOUL_OS_EPISTEMIC_PIGGYBACK`（**預設關閉**）⇒ 生產 prompt 逐位元不變。
- **背景行動**：daemon thread ＋ 5 分鐘節流 ＋ 雙重 fail-silent ⇒ **不阻塞任何既有路徑**。

---

## 3. 變異實驗（8 條，**每條都轉紅**）

驅動器：`%TEMP%\sim_epis_mutation.py`（**不在 repo 內** ⇒ 不被 pytest 收集、也在 R5 行程護欄掃描範圍外）。
每條變異流程：**變異前跑守衛節點（須綠）→ 套用原始碼變異 → 再跑（須紅）→ 從備份還原（sha256 逐位元相同）**。

| # | 變異 | 變異檔案 | 轉紅的守衛 | 紅燈實測（節錄） |
|---|---|---|---|---|
| 1 | 自主求知行動**順帶發訊**（在 `close_tension` 內 publish `AGENCY_TRIGGER`） | `epistemic_action.py` | 守衛 1（零打擾） | `AssertionError: 對用戶發訊數必須為 0，實得 1` |
| 2 | **無證據也閉合**（硬編故事：`evidence is None` 時硬造一條命中） | `epistemic_action.py` | 守衛 3 | `AssertionError`（`assert all(c.closed is False ...)`） |
| 3 | 掛載挑選**忽略話題相關性**（總帶最舊） | `piggyback_queue.py` | 守衛 4 | `AssertionError: 話題無關 ⇒ 不帶` |
| 4 | 掛載**不消耗**（重複帶出） | `piggyback_queue.py` | 守衛 6 | `AssertionError: 同一小念頭不得被帶出第二次` |
| 5 | **過期檢查失效** | `piggyback_queue.py` | 守衛 5 | `AssertionError: assert '【話頭】…' == ''` |
| 6 | **隔離守門失效**（隔離項可入隊） | `piggyback_queue.py` | 守衛 7 | `AssertionError: assert accepted is False`（`assert True is False`） |
| 7 | 掛載**影響主動 DM 的發送決策**（把念頭寫回 `relationships.json`） | `recall_prompt.py` | 守衛 8 | `AssertionError: 本機制不得改變主動 DM 的發送決策輸入`（`印象标签：貼心、念頭` vs `貼心`） |
| 8 | 觸發來源**改回「想念值」** | `epistemic_action.py` | 守衛 9 | `AssertionError: 想念值低於門檻不得擋住自主求知行動`（`assert 0 == 1`） |

**8/8 條：變異前綠（returncode=0）→ 變異後紅（returncode=1）→ 檔案已還原（sha256 一致=True）。**
變異殘留檢查：四個受測檔的 `MUTATION-` 標記殘留數 = **0**。

> 🔴 **第一次跑時守衛 8 是盲腸守衛**（變異 7 跑完仍綠）：原因是守衛 8 只跑了
> `run_background_action`，**沒有真的走一次掛載區塊的呈現路徑**。
> 處置：**修守衛**（在 before/after 之間真的呼叫 `format_piggyback_block` 並斷言它
> 有產出），不是改斷言迎合。修完後變異 7 如實轉紅。
> 🔴 同樣地，守衛 1 的斷言順序被調整成**先斷最具體的性質**（對用戶發訊數 = 0）
> 再斷較寬的性質（0 對外連線）——否則「變異順帶開了 socket（Windows asyncio
> self-pipe）」會先觸發，紅燈指錯方向。

---

## 4. 沙盒 10 項守衛的實測數字

沙盒平面：假時鐘（`now=1700000000` 注入，0 sleep ＋ 0 真實等待）＋ 記憶副本（寫 `tmp_path`）
＋ 腳本化 LLM（0 呼叫）＋ socket 攔阻（攔截**所有**對外連線）＋ 事件總線攔阻（計數對用戶發訊）。

| # | 守衛 | 分子／分母 | 單位 | 備註 |
|---|---|---|---|---|
| 1 | **零打擾** | **0 / 2** | 則（分母＝觸發的行動次數） | 同時 0/2 對外連線；2/2 次閉合成功（先證明閉合真的發生，零打擾才有意義） |
| 2 | 張力確實被閉合 | 事件 1/1；resolved 1/1；小念頭 2/2 | 件／筆／筆 | 閉合事件走既有 `InnerLifeWriter` ＋ `NarrativeTraceWriter` 通道（shadow log 有紀錄，`trigger_type="system"` 屬合法 producer trigger） |
| 3 | **不硬閉合** | closure 事件 **0 / 1** | 件（分母＝嘗試閉合的張力數） | 無證據時張力仍開放 1/1 筆 |
| 4 | 掛載正確性 | 相關話題 **1 / 1**；無關話題 **0 / 1** | 個 | 一次上限 1 個 |
| 5 | 過期丟棄 | **0 / 1** | 個（過期項被帶出） | TTL = created + 7 天 |
| 6 | 不重複 | 第一次 1/1；第二次 **0 / 1** | 個 | 帶出即消耗 |
| 7 | 隔離不掛載 | 入隊 **0 / 1**；帶出 **0 / 1** | 筆／個 | 1/1 正確擋下（隔離集合走 `lifecycle_overlay.load_quarantined_node_ids` 單點收斂） |
| 8 | 不影響發訊決策 | **1 / 1** 組決策輸入 **byte-identical**（差異位元組 = 0） | 組 | 用既有 `decision.build_decision_prompt` ＋ 4 個輸入塊實測；`scheduler.py` / `decision.py` 原始碼不含本票任何識別字 |
| 9 | **不經過 0.3 門檻** | **1 / 1** 次閉合 | 次 | `compute_longing(intimacy=100, silence_minutes=72) = 0.0500 < LONGING_THRESHOLD = 0.3`，缺口路徑**仍能觸發** |
| 10 | 生產 sha256 | 本票造成的生產變更 **0 / 221** 個 | 個（受測範圍內可讀檔案） | 讀不到（生產服務鎖住 sqlite）**0 / 221**；服務自身 churn **0 / 221**（本次量測窗內未發生） |

| ⚠️ 守衛 10 的**範圍**誠實揭露：生產 `data/**` 共 7285 個檔案，其中
> `data/memory/*/graph.sqlite` 等被**生產服務鎖住**（實測 `ReadAllBytes` 拋
> `IOException`）⇒ 整包雜湊會不穩定。故對帳範圍＝**本票有可能觸及的路徑**
> （`data/inner_life`、`data/elevation`、`data/world`、`data/conversations`、
> `data/soul/*/relationships.json`、`data/soul/*/memories.jsonl`）＝ 221 個可讀檔案。
> 另補兩道**確定性**護衛：① 沙盒期間 `data_root()` ≠ repo `data/`；
> ② 新模組原始碼無任何 `data/` 路徑字面值。

### 4.1 全 repo 級的生產資料歸因掃描（獨立於沙盒）

掃描 `data/**` **全部** 7288 個可讀檔案，找本票的識別字
（`epistemic_action` / `epistemic_closure` / `【話頭】` / `PIGGYBACK` /
`epistemic_gap` / `relational_tension`）：

```
scanned files      = 7288
unreadable (locked)= 0
ticket-marker hits = 0        ← 本票沒有在任何生產檔案留下痕跡
```

---

## 5. 生產行為零變化的證明

| 證據 | 內容 |
|---|---|
| **旗標預設關閉** | `SOUL_OS_EPISTEMIC_PIGGYBACK` 缺省＝關閉 ⇒ `feature_enabled() == False` |
| **proxy 接線是死碼** | 守衛 4c：`px._format_piggyback_block(...)` 回 `""`；守衛 4d：旗標關閉時 **0 個執行緒**被啟動（`spawned == []`）、`_BACKGROUND_ACTION_STATE["running"] is False` |
| **生產 prompt 逐位元不變** | 注入點是「有區塊才 append」；旗標關閉時區塊恆為 `""` ⇒ `system_parts` 完全不變 |
| **決策面零改動** | `src/soul/scheduler.py`、`src/soul/decision.py` **未動**（`git diff --name-only HEAD` 實測為空）；守衛 8b 掃描兩檔原始碼，本票識別字出現數 = 0 |
| **生產資料零寫入** | 守衛 10：本票造成的生產變更 0/221；守衛 10b：新模組無 `data/` 路徑字面值 |
| **不新增 LLM 呼叫** | AST 守衛：新模組無 LLM/網路客戶端匯入、無 LLM 介面呼叫；沙盒實測 0/2 對外連線 |

---

## 6. 已知殘餘問題（本票未解決）

1. **張力檢出率不足**：`epistemic_appraisal` 只對「有 pack 的角色」且「特徵詞命中」的
   話語回報缺口（生產只有 `agent_rem` 一份 pack）⇒ 多數角色的缺口路徑**恆為空**。
   本票的閉合機制在「無張力」時表現＝完全靜默（0 事件、0 念頭），符合設計，
   但**機制覆蓋率只等於 pack 覆蓋率**。
2. **話題相關性是「正規化詞元集合交集」**（非子字串、非語意）：`手沖` 與 `手沖壺`
   **不算**重疊。寧可不帶（工單要求），但召回率會低於語意相關。
3. **背景行動的觸發點是「閒置時」而非「槽後」**：本票不得改 `src/soul/scheduler.py`
   ⇒ 只能在 proxy 的訊息處理路徑上以 daemon thread 觸發（5 分鐘節流）。
   真正的「槽後」觸發需要另開一張票改 scheduler。
4. **張力狀態承載是單一 JSON 檔**（`<store>/epistemic_tensions.json`），非 append-only：
   多行程並寫會互相覆蓋。目前靠 daemon thread ＋ 5 分鐘節流壓低併發；正式上線前
   應改成 append-only sidecar。
5. **本票不部署**：所有接線預設關閉。啟用需 Owner 授權，且啟用後會開始寫
   `data/inner_life/`（張力承載 ＋ 話頭槽）與 `data/elevation/`（經既有
   `SubmissionGate` 的 `consume`）。

---

## 7. 驗收對照（工單 §4）

| 驗收項 | 結果 |
|---|---|
| 1. 三個新測試檔全綠 | ✅ 見 §8 |
| 2. 8 條變異全部轉紅 | ✅ 見 §3（8/8） |
| 3. 10 項守衛逐項有實測數字 | ✅ 見 §4 |
| 4. `pytest -q tests` 0 failed | ✅ 見 §8（解譯器＝repo `.venv`；`1 failed, 6865 passed` 那次是 R5 行號位移，已修，重跑見 §8） |
| 5. 本票不部署、接線預設關閉 | ✅ 見 §5 |
| 6. 實作紀錄 | ✅ 本檔 |

---

## 8. 測試結果（解譯器＝**repo 的 `.venv`**：`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`）

> ⚠️ 口徑（AGENTS.md 行程安全鐵律 #10）：下列數字**一律**以 repo 內 `.venv` 的直譯器
> 為準。PATH 上的全域 pytest（Python 3.12）是另一個世界。

### 8.1 全庫回歸（本票的最終驗收指令）

```
.venv\Scripts\python.exe -m pytest -q tests
```

| 項目 | 數值 |
|---|---|
| collected | **6890**（6866 passed ＋ 24 skipped；另有 27 個 subtests passed） |
| passed | **6866** 筆 |
| skipped | **24** 筆 |
| failed | **0** 筆 |
| errors | **0** 筆 |
| 耗時 | 771.24s（0:12:51） |

### 8.2 本票三個新測試檔（單獨跑）

```
.venv\Scripts\python.exe -m pytest -q tests/inner_life/
```
→ **138 passed, 0 failed**（含本 repo 既有的 6 個 `inner_life` 測試檔）

| 檔案 | 筆數 | 內容 |
|---|---|---|
| `tests/inner_life/test_epistemic_action.py` | 27 | 觸發判定 ＋ 多路徑展開 ＋ 閉合判定 ＋ AST 紅線守衛 ＋ 漂移守衛 |
| `tests/inner_life/test_piggyback_queue.py` | 21 | 入隊／容量／過期／相關性／消耗／隔離／旗標 |
| `tests/inner_life/test_epistemic_piggyback_sandbox.py` | 21 | 沙盒端到端 ＋ 10 項守衛 ＋ 生產對帳 |

### 8.3 護欄輸出（實測）

```
[ROOT-SCRIPTS-GUARD] scanned=372 spawn_hits=0 kill_hits=0 address_hits=9 allowed_kills=1
[SPAWN-GUARD-R5] scanned=372 spawn_sites=22 allowlisted=22        ← 白名單大小鎖死 22，未動
```

### 8.4 前置失敗與修法（誠實紀錄）

| 輪次 | 結果 | 原因 | 處置 |
|---|---|---|---|
| 第 1 次全庫回歸 | 16 failed | ① 我把背景行動接線放在 `last_interaction_ts` 賦值**之前** ⇒ `UnboundLocalError`，連帶打紅 15 支既有測試；② `tests/test_observability_completeness_1.py` 的 `allowed` 集合是逐票更新的 ticket-scoped 清單 | ① 接線移到賦值之後；② 依 repo 先例擴充 `allowed`（`frozen_dirs` 永久紅線逐字未動） |
| 第 2 次全庫回歸 | 1 failed | R5 行程護欄的白名單是 `(path, lineno)` 配對 ⇒ 我在該檔加註釋時把 `subprocess.run` 從 762 推到 766 | **讓行號不動**（說明壓回單行 docstring）；**不改白名單** |
| 第 3 次全庫回歸 | **0 failed** | — | — |

---

## 9. 踩到 Frozen Contract / 意外行為

- **Frozen Contract**：**無**。`frozen_dirs` 清單（Agency 4 stages / TriggerEnvelope /
  InnerLifeEvent / 4 handlers / `submission_gate` / `identity_firewall` /
  `producer_gate` / `world/middleware` / `memory/sage/writer` / `soul/decision`）
  **逐字未動**，`git diff --name-only HEAD` 實測為空。
- **意外行為（已修）**：
  1. `epistemic_action.py` 的 dataclass 原本命名 `Path`，**遮蔽** `pathlib.Path`
     ⇒ 執行期 `TypeError: Path.__init__() missing 3 required positional arguments`。
     修法：`pathlib.Path` 改以 `_FsPath` 匯入（dataclass 名稱維持 `Path`，對齊工單簽名）。
  2. `SELF_REASON` 探測原本用**機器現在**而非注入的假時鐘 ⇒ 假時鐘場景誤判成
     「張力已消退」。修法：`default_probes(now=...)` 把時鐘綁進探測。
  3. `close_tension` 的 `close_tension` 探測映射原本沒綁 `now` ⇒ 同上。
  4. proxy 接線第一次放在 `last_interaction_ts` 賦值**之前** ⇒ `UnboundLocalError`，
     連帶打紅 15 支既有測試。修法：移到賦值之後（`proxy.py:3936` 區段）。
  5. 守衛 8 第一次是**盲腸守衛**（沒走掛載呈現路徑）⇒ 修守衛而非改斷言（見 §3 末）。
  6. `tests/test_observability_completeness_1.py` 的既有 `subprocess.run` 落在
     **行號 762**，而 R5 行程護欄的白名單是 **`(path, lineno)` 配對** ⇒ 我在該檔
     加註釋時把行號往下推，護欄立刻轉紅（`spawn_sites=22 allowlisted=21`）。
     處置：**讓行號不動**（把說明壓回單行 docstring），**不去改白名單**
     （白名單大小鎖死 22，見 §1 護欄輸出 `spawn_sites=22 allowlisted=22`）。
  7. 背景行動節流原本是**全域** 5 分鐘（多角色時只有一個角色能排上）⇒ 改成
     **逐 agent** 計時（`_BACKGROUND_ACTION_STATE[agent_id]`）。
- **非本票造成但必須回報**：`tests/test_observability_completeness_1.py` 的
  `allowed` 集合是**逐票更新**的 ticket-scoped 清單（不是永久紅線）；本票不改
  `src/**` 的話它會在乾淨樹以外一律轉紅。本票依 repo 先例把它擴充（§1 #9），
  `frozen_dirs` 永久紅線逐字未動。
