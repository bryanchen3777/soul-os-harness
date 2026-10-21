# 工單：SIM-ACCEL-RECALL-30D — 虛擬時鐘快進沙盒 ＋ 方向 A 第二階段 Prompt 接入（saturation-validated, data-gated）

> **狀態**：決策完備，待派工
> **日期**：2026-10-20 22:50 EDT
> **Owner 授權**：Bryan 2026-10-20 22:37 EDT —— 「用實際時間來等，是最不實際的辦法，所以結論是不等」；批准立刻下發 SIM-ACCEL-RECALL-30D
> **前置**：`9465bc0`（§0.1.25 對帳入庫）／`ba19efd`（`query_recall`）／ELEVATION-RECALL-HARDENING-1（第一階段已合入）／`c327958`（SOUL-BUG-010-B 老化上線）／`c443643`·`1850e21`·`e47b667`（方向 A 第一階段硬化）

---

## 0. 主大腦的前提量測（2026-10-20 22:40 EDT，實測，非推論）

本票的所有設計決策都建立在下表上。這些數字是**已跑出來的**，不是預期值。

### 0.1 虛擬時鐘快進 30 天的效果（唯讀重現，來源＝生產 `data/elevation/`）

| 快進 | 池（非隔離 × 四層 × dormant\|superseded） | 型別分佈 |
|---|---|---|
| +0 天 | 0 顆 | — |
| +14 天 | **0 顆**（46 顆 dormant **全是 pattern**，被結構性排除） | — |
| **+30 天** | **53 顆** | value 47／belief 1／trait 5 |
| +45 天 | 53 顆（與 +30 天同，固定點） | 同上 |

### 0.2 四項 GO 判準在沙盒 T+30d 的實測（`recall_readiness_cli.py --source-dir <沙盒> --probes 200`）

| 判準 | 實測值 | 門檻 | 結果 |
|---|---|---|---|
| ① 可回憶池 | **53 顆** | ≥ 5 | **PASS** |
| ② 池中 superseded | **0 顆** | ≥ 1 | **FAIL** ← 唯一缺口 |
| ③ 真實命中率 | **21/200 = 10.50%**（命中 8 顆 unique 節點） | ≥ 5% | **PASS** |
| ④ 命中 100% 非隔離 | 比例 1.0000；隔離配對 **0 組** | = 1.0 | **PASS** |

**結論：3/4 已 PASS，缺口只有 ②。**

### 0.3 為什麼缺口只有 ②（這是本票最重要的設計約束）

引擎的狀態機是 `ACTIVE → WEAKENING → DORMANT → SUPERSEDED`，但**兩段由不同機制驅動**：

- `engine.evaluate_lifecycle()`（`soul_elevation/engine.py:1326-1360`）**只**做 `active→weakening`（age ≥ 7）與 `weakening→dormant`（age ≥ 30）。**它永遠不產 superseded**。
- `superseded` 只能由 `engine.supersede()` 觸發，前置條件（`soul_elevation/prior.py:68-69`）：
  - 獨立矛盾證據 ≥ `LIFECYCLE_N_SUPERSEDE` = **3**（同一 `source_id` 或同一 `event_identity` 只算 1 個）
  - 矛盾證據分布於 ≥ `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD` = **2** 個**不同模擬日**（防單日雜訊）

⇒ **純時間快進在數學上不可能讓判準 ② 通過。** 缺矛盾來源就永遠 GO 不了。
⇒ 而「矛盾分布在 2 個不同模擬日」**正好是虛擬時鐘的強項**（把矛盾 `ts` 設在兩個不同模擬日即可）⇒ 缺口可補。

### 0.4 快進的副作用確認（皆已實測）

- 快進 **2 級**才到固定點：pass1 = 495 筆（active→weakening）、pass2 = 307 筆（weakening→dormant）、pass3 = **0 筆冪等 no-op**。
- 生產三檔（nodes／edges／lifecycle_events）sha256 **前後逐位元相同**（已驗）。
- 關鍵反例 `6a92c906b245` 在 T+30d **會**變 dormant —— 這是預期（30 天無支持），**但「關鍵反例須維持 active」是單一夜槽紅線，兩者語意不同，不可混用**（見 §3.1 不得合併 `eval_lifecycle_cli.py` 的理由）。

---

## 1. 目標

1. **用虛擬時鐘在沙盒內跑完 30 天**（0.05 秒級），產出飽和回憶池 **＋ ≥1 顆 superseded**，把方向 A 第二階段的四項 GO 判準在沙盒做到 **4/4 PASS**。
2. **把方向 A 第二階段（Prompt 注入）的程式碼寫完、測試齊備、以「資料判準」而非日期自動啟用**，合入 `main` ⇒ **功能就地閉環**。
3. **生產側資料仍走自然老化**（不催熟、不造假）⇒ 沙盒資料**不得**流回生產。

**一句話**：**開發不等待，啟用靠數據。**

---

## 2. 範圍（你擁有這些檔案的寫入權；其他檔案一律不得動）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `scripts/sim_accel_recall_cli.py`（**新增**） | 沙盒快進 + 矛盾注入 + GO 判準量測 CLI |
| 2 | `src/inner_life/recall_prompt.py`（**新增**） | Prompt 注入區塊組裝 + 啟用判準（純函式為主） |
| 3 | `src/llm/proxy.py`（**最小改動**） | 新增 1 個 helper ＋ 2 處 `system_parts` 組裝各 3 行 |
| 4 | `tests/test_sim_accel_recall.py`（**新增**） | 沙盒守衛測試 + 變異實驗 |
| 5 | `tests/test_recall_prompt_injection.py`（**新增**） | 注入守衛測試 + 變異實驗 |
| 6 | `docs/ELEVATION-RECALL-CONTRACT.md` | **只加 `## 9. 附錄 B`**；§0–§8 **逐字不動** |
| 7 | `logs/SIM-ACCEL-RECALL-30D-IMPLEMENTATION.md`（**新增**） | 實作紀錄（量測數字、變異證據） |

**不得動**：`src/inner_life/elevation_recall.py`、`src/inner_life/recall_readiness.py`、`src/inner_life/lifecycle_overlay.py`、`src/inner_life/elevation_adapter.py`、`src/inner_life/emergent_projection.py`、`scripts/eval_lifecycle_cli.py`、`scripts/recall_readiness_cli.py`、`docs/ELEVATION-RECALL-CONTRACT.md` 的 §0–§8、`personas/**`、`configs/**`、`data/**`。

---

## 3. 做法（決策已定，執行者照做）

### 3.1 新增 `scripts/sim_accel_recall_cli.py`

**介面**：
```
--source-dir   圖譜來源（唯讀）。預設 = 生產 data/elevation/
--days         快進天數（預設 30）
--out-dir      沙盒輸出（預設 %TEMP%\sim_accel\run_<UTC時間戳>）
--contradictions  合成矛盾組數（預設 1）
--json         以 JSON 輸出到 stdout
```

**結構性保證（照 `scripts/eval_lifecycle_cli.py` 與 `scripts/recall_readiness_cli.py` 的既有紀律，逐條照做）**：
- **來源唯讀 ＋ 唯一寫入目標＝自建 temp 沙盒**；**不提供任何就地寫入旗標**（AGENTS.md 行程安全鐵律 12：不要用「可能漂移的兩個值」擋寫入，把寫入能力本身移除）。
- 沙盒必須在 `tempfile.gettempdir()` 之下，否則 `print("[refuse] ...")` 並 `return 3`。
- **不 import** `subprocess`／`requests`／`httpx`／`socket`／`uvicorn`／`fastapi`。
- 生產三檔 sha256 **前後對帳**；任一不同 ⇒ **assert 失敗並中止**（不是警告）。
- 不寫任何 repo 檔案（報告寫 `%TEMP%`）；不啟動伺服器、不綁埠、不發網路請求。

**流程**：
1. 複製 `elevation_nodes.jsonl`／`elevation_edges.jsonl`／`elevation_lifecycle_events.jsonl` 到沙盒。
2. **快進**：`sim_now = utcnow + days`；迴圈呼叫 `evaluate_lifecycle_all(store_dir=str(sandbox), now_ts=sim_now)`，直到回傳 `[]`（冪等 no-op）為止，**上限 6 輪**（nightly 一次一級：`active→weakening→dormant` 需 2 級）。每輪記錄 `len(events)` 與 `by_state`。
   - ⚠️ **不得**修改 `evaluate_lifecycle_all` 的行為或 `scripts/eval_lifecycle_cli.py` 的判準。`eval_lifecycle_cli.py` 的「dormant/superseded 轉變＝FAIL」是**單一夜槽紅線語意**（一晚不該有節點掉到 dormant），與 30 天快進語意（dormant 正是預期結果）**互斥**。兩個工具各司其職，**不得合併、不得互相改名**。
3. **矛盾注入**（補 GO 判準 ②）：
   - 從沙盒池挑 **1 顆**節點：必須**非隔離** ∧ `node_type == "value"` ∧ 在池內。挑不到 ⇒ **報 FAIL 並中止**（**不許降級**、不許改用其他型別）。
   - 建引擎：`from src.inner_life.elevation_adapter import _rebuild_engine_for` ⇒ `_rebuild_engine_for(sandbox, nodes, edges, agent_id=<該節點的 agent_id>, llm=None)`。
   - 記矛盾（**三個不同 `source_id`，其中兩個 ts 在模擬日 D、另一個在 D+1**）：
     ```python
     engine.record_contradiction(node_id, source_id=f"sim_contr_{i}", ts=day_d)      # i=0,1
     engine.record_contradiction(node_id, source_id=f"sim_contr_2", ts=day_d_plus_1)
     ```
     `day_d = sim_now.isoformat()`；`day_d_plus_1 = (sim_now + 1 day).isoformat()`。
     ⇒ 獨立矛盾 3 個、跨 2 個不同模擬日 ⇒ 滿足 `LIFECYCLE_N_SUPERSEDE` 與 `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`。
   - 執行取代：`engine.supersede(node_id, new_content=..., new_confidence=..., valence=..., source_ids=[...], ts=sim_now, inner_life_event_id=...)`（**確切簽章請讀 `soul_elevation/engine.py` 的 `def supersede` 自行對齊**；`essence` 另有 valence 反轉 + confidence delta 門檻，本票**只挑 value 型**節點以避開該分支）。
   - **持久化到沙盒**（append-only）：
     - 新節點：append 一行到沙盒 `elevation_nodes.jsonl`（欄位沿用引擎回傳的 `ElevationNode` 的既有欄位族：`node_id`／`node_type`／`content`／`agent_id`／`parent_node_id`／`lineage_depth`／`lineage_path`／`created_ts`／`last_support_ts`／`confidence`／`stability`／`valence`／`provenance_ref`／`superseded_by` 等；**不得**自創欄位名）。
     - 舊節點：append 一筆 sidecar 事件到沙盒 `elevation_lifecycle_events.jsonl`，**欄位必須與 `evaluate_lifecycle_all` 產生的完全一致**：
       ```json
       {"ts": "<sim_now>", "node_id": "<舊節點>", "event": "lifecycle_transition",
        "lifecycle_state": "superseded", "lifecycle_state_before": "<原態>",
        "lifecycle_state_after": "superseded", "anchor_ts": "<引擎錨點>",
        "reason": "contradiction_supersede", "agent_id": "<agent>", "node_type": "value"}
       ```
       🔴 **`lifecycle_state` 是 overlay `_reduce_events` 唯一辨識的 key**，少它則事件被整個忽略、狀態永遠不生效。`superseded_by`（新節點 id）也要帶上。
4. **量測**（在沙盒上跑，**不得**另寫一份算法）：
   - `from src.inner_life.recall_readiness import pool_summary, hit_rate, go_no_go`
   - `nodes = load_elevation_nodes(sandbox)`（已套 overlay）；`quarantined = load_quarantined_node_ids(sandbox)`
   - 探針：`data/conversations/*.json` 最近 **200** 筆真實 user 訊息（檔名形如 `…_agent_<name>_private.json`），詞元走 SSOT `src/memory/v1/loader.py::derive_query_tags`（**不得重寫一份詞元化**）。
   - 輸出 `summary`／`hit`／`verdict`（`go_no_go`）。
5. **報告**（繁中，寫 `%TEMP%`）必須逐項列出：
   - 快進每一輪的 `len(events)` 與 `by_state`（證明 2 級到固定點、第 3 輪冪等 no-op）
   - 池大小 + 型別分佈（分子＝顆，分母＝池總數）
   - 命中率：分子＝命中 probe 數，分母＝probe 總數，單位＝probe；unique 命中節點數
   - 隔離配對數、非隔離命中比例
   - **四項 GO 判準逐項 PASS/FAIL**（`verdict` 必須是 **GO**）
   - superseded 節點的 lineage（舊節點 id → 新節點 id、parent／lineage_depth）
   - 生產三檔 sha256 前後對帳結果
   - 退出碼：`GO` → 0；`NO-GO` → 1（**這是設計行為，不是崩潰**）

### 3.2 新增 `src/inner_life/recall_prompt.py`

**（a）區塊組裝（純函式）**
```python
RECALL_BLOCK_HEADER = "【回憶】（這些是你過去在意過的事，不代表你現在的想法）"

def format_recall_block(agent_id: str, utterance: str, *,
                        store_dir: Optional[Any] = None,
                        max_hits: int = 3) -> str:
    """回憶區塊（契約 §3.1 語氣隔離 + §9 附錄 B）。fail-silent：例外 → ""。"""
```
- 命中來源：`query_recall_by_tags(agent_id, tags, store_dir=store_dir)`；`tags` 走 SSOT `derive_query_tags(utterance)`。
- 每顆命中用既有 `format_recall_hit(hit)` 呈現（**該函式逐字不改**）；`max_hits=3`，取前 3 顆。
- 區塊格式：header + 每行一顆命中（`format_recall_hit` 的輸出）。
- **召回區塊不得進入 EMERGENT block**（契約 §3.1 明文）。
- 區塊不得與 MEMORY 區塊混用同一前綴（獨立標題）。
- **fail-silent**：任何例外 → 回傳 `""`（`except Exception`）⇒ prompt 與未實作時逐位元等價。
- 零 LLM、deterministic。

**（b）啟用判準（資料判準，非日期）**
```python
def is_recall_enabled(*, store_dir: Optional[Any] = None) -> bool:
    """生產圖譜的四項 GO 判準是否全過。全過才啟用注入。"""
```
- 用**同一套純函式**（`pool_summary` / `hit_rate` / `go_no_go`）對**生產**圖譜算一次，`verdict == "GO"` 才 True。
- **不得**用日期、天數、人工開關決定啟用。
- 必須有**行程內快取**：以「生產 `elevation_nodes.jsonl`／`elevation_lifecycle_events.jsonl` 的 mtime ＋ `data/conversations` 目錄最新 mtime」為 key，TTL **30 分鐘**；key 不變且 TTL 未到 ⇒ 直接回傳快取值（避免每次 prompt 組裝都掃對話）。
- **kill switch**：環境變數 `SOUL_OS_RECALL_INJECTION` 設為 `off`/`0`/`false` ⇒ 一律 False（**這是唯一的人工開關**，用於緊急止血；預設為「自動 = 資料判準」）。
- 啟用狀態**變化時**寫一行 log（`logger.info`），含四項判準的值。

### 3.3 接線到 prompt（`src/llm/proxy.py`，最小改動）

- 新增 helper（與既有 `_format_emergent_block` 同款雙重失敗隔離）：
  ```python
  def _format_recall_block(agent_id: str, utterance: str) -> str:
      try:
          from src.inner_life.recall_prompt import format_recall_block
          return format_recall_block(agent_id, utterance)
      except Exception:
          return ""
  ```
- 在**兩處** `system_parts` 組裝點各加 3 行（`proxy.py` 的 `~793` 與 `~1439` 附近；兩處的 `system_parts` 組裝）：
  ```python
  recall_block = _format_recall_block(agent_id, current_input)
  if recall_block:
      system_parts.append(recall_block)
  ```
  （`current_input` 是該處既有的當輪輸入變數，見 `proxy.py:774` 的 `utterance=current_input`。）
- **注入位置決策**：插在 **MEMORY 區塊之後**（`memory_context` 那個 `system_parts.append` 之下）、chrono 區塊之前。語義順序為
  `IDENTITY → CAPABILITY → EMERGENT → MEMORY → RECALL → CHRONO → 對話歷史`。
  理由：回憶屬於「過去」，但**必須**與 EMERGENT（「你成為什麼」＝現在的自我定義）分開，才能守住契約 §3.1 的語氣隔離。
- 現況（生產池 = 0）下 `recall_block` 恆為 `""` ⇒ **prompt 與未實作時逐位元相同**（硬驗收條件）。

### 3.4 守衛測試 + 變異實驗

**`tests/test_sim_accel_recall.py`**
1. 快進 30 天能產出 dormant，且**生產三檔 sha256 前後逐位元相同**。
2. 矛盾注入能產出 **1 顆 superseded**：舊節點狀態 = `superseded`、新節點 `parent_node_id` = 舊節點 id、`superseded_by` 正確。
3. **矛盾不足 3 個 ⇒ 必須失敗**（`supersede` 拋 `ValueError` 或不產生 superseded）—— 證明門檻真的在生效。
4. **矛盾只跨 1 個模擬日 ⇒ 必須失敗**（`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD` 守門生效）。
5. **四項 GO 判準在沙盒 4/4 PASS**（`verdict == "GO"`）。**這支必須是「非空轉綠」守衛**：必須另外證明它在條件不滿時會轉紅（見變異 5）。

**`tests/test_recall_prompt_injection.py`**
1. 生產池 = 0 時，prompt 組裝結果**與未實作時逐位元相同**（區塊為空）。
2. 沙盒池含 dormant 節點時，區塊含 `format_recall_hit` 的語氣字串（「我以前很在意」）。
3. 沙盒池含 superseded 節點時，區塊含「我以前覺得 A，但後來發現 B」。
4. **隔離節點永遠不出現在區塊裡**（硬不變式）。
5. 召回區塊**不進** EMERGENT block。
6. 啟用判準未達 GO 時區塊為空；`SOUL_OS_RECALL_INJECTION=off` 時區塊為空。

**變異實驗（每一條都必須轉紅；轉不了紅就是盲腸守衛，必須拔除）**

| # | 變異 | 必須轉紅的測試 |
|---|---|---|
| 1 | 拔除隔離守門（讓 quarantined 節點可召回） | `test_recall_prompt_injection` 的隔離不變式 |
| 2 | 啟用判準從「GO」改成「永遠 True」 | 「現況池 = 0 時區塊為空」 |
| 3 | 矛盾去重失效（同一 `source_id` 記 3 次也算 3 個獨立） | 「矛盾不足 3 個必須失敗」 |
| 4 | sidecar 事件拿掉 `lifecycle_state` key | 「superseded 節點狀態正確」 |
| 5 | 沙盒 GO 判準被硬寫成 True | 「四項判準在沙盒 4/4 PASS」必須會轉紅（用條件不滿的輸入） |

🔴 **變異實驗的執行安全（AGENTS.md 行程安全鐵律 7）**：凡「移除某道閘門」型變異，必須讓被解除防護的程式碼**不可能真的執行**（用 pytest `pytest_runtest_call` hook 在 call 階段直接 `raise`，或把目標指向不可路由位址）。**不得**靠「它應該會失敗」的預測。**不得**讓任何變異觸達生產（`:8000`／`data/**`）。

---

## 4. 驗收（完成的定義）

1. `.venv\Scripts\python.exe scripts\sim_accel_recall_cli.py --days 30` 輸出 **VERDICT: GO**（四項判準全 PASS），且報告列出生產三檔 sha256 前後**逐位元相同**。
2. 現況下（生產池 = 0）prompt 組裝結果**與未實作時逐位元相同**（硬條件）。
3. 沙盒飽和池下，區塊含正確語氣隔離字串，且**隔離節點不出現**、**召回區塊不進 EMERGENT**。
4. `.venv\Scripts\python.exe -m pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，且註明解譯器是 repo 的 `.venv`）。
5. **5 條變異實驗全部轉紅**，並附「變異前綠 → 變異後紅」的實測證據。
6. `docs/ELEVATION-RECALL-CONTRACT.md` 加 `## 9. 附錄 B`，且 **§0–§8 逐字未動**（用位元級比對證明）。
7. 報告寫入 `logs/SIM-ACCEL-RECALL-30D-IMPLEMENTATION.md`（含全部實測數字與變異證據）。

## 5. 測試

- 新測試：`tests/test_sim_accel_recall.py`、`tests/test_recall_prompt_injection.py`（如上）。
- 必跑現有測試：`tests/test_elevation_recall_hardening.py`、`tests/test_elevation_recall.py`、`tests/infra/`（AST 守衛）。
- 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（**必須指名範圍**，不得裸跑根目錄）。
- 不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。

## 6. 不做（Out of Scope）

- 不得改 `src/inner_life/elevation_recall.py`／`recall_readiness.py`／`lifecycle_overlay.py`／`elevation_adapter.py`／`emergent_projection.py`。
- 不得改 `scripts/eval_lifecycle_cli.py`／`scripts/recall_readiness_cli.py`（語意不同，**不得合併**）。
- 不得改契約 §0–§8。
- 不得寫入／改名／刪除 `data/**` 下任何檔案；**沙盒資料不得流回生產**；**不得為了讓生產 GO 而造假生產資料**。
- 不得重啟服務、不得啟動／停止任何行程、不得對任何 PID 做 `Stop-Process`／`taskkill`／`pkill`。
- 不得為了讓生產池長大而縮短排程間隔／調整喚醒週期／人為催促線頭推進。
- 不得觸發 synthetic diary／dream。
- **不得 commit／push**（由主大腦驗證後執行）。
- 不得跑 `scripts/manual_*.py`。

## 7. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `docs/ELEVATION-RECALL-CONTRACT.md` §0–§8 | **逐字不動**，只可在檔尾加 `## 9. 附錄 B` |
| `format_recall_hit` | 簽章與語意**逐字不動** |
| `query_recall`／`query_recall_by_tags` | 簽章、比對語意、回傳形狀、排序、失敗靜默**逐字不動** |
| `format_emergent_block` | **逐字不動**；召回區塊**禁入** EMERGENT block |
| 隔離永不召回 | **硬不變式**，任何 path 下不可召回 `quarantined` 節點 |
| elevation 節點檔 | **append-only**；召回路徑**不得**寫任何節點／邊檔 |
| 詞元化 | SSOT = `src/memory/v1/loader.py::derive_query_tags`，**不得重寫一份** |
| GO 判準四項 | 門檻值**不得**為了讓本票過而調整（`recall_readiness.py` 的常數不動） |

## 8. 回報格式

1. 改動檔案清單（新增／修改，逐檔一行）。
2. 測試結果：跑了哪些指令、收集數／通過／跳過／失敗，**並註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：5 條逐條的「變異內容 → 轉紅的測試名 → 紅燈實測輸出」。
4. 沙盒實測：快進輪次與 by_state、池大小與型別分佈、命中率分子／分母／單位、隔離配對數、四項判準逐項 PASS/FAIL、superseded lineage、生產 sha256 前後對帳。
5. 是否有踩到 Frozen Contract／意外行為；契約 §0–§8 的位元級未動證明。
