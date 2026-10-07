# 工單：Memory Continuity Observability v1 — 實作（Layer 2 Injection Snapshot + Layer 3 Turn Sidecar）

- 版本：v1（decision-complete，執行者不需再做任何設計決策）
- 拍板：Bry 2026-10-07 D1–D5 全部凍結
- 設計來源：`docs/MEMORY-CONTINUITY-OBSERVABILITY-DESIGN.md`（commit `f3348ab`）
- 分類：**Frozen Contract — Additive Observability Exception**（D4）

---

## 目標

讓 Loader path 第一次形成可驗證的完整 lineage：

```
retrieval（已有）→ injection（本票補）→ turn（本票補）→ use（離線推導）
```

現在缺的是中段。`retrieval ≠ injection ≠ use ≠ influence` 四段中，**injection 與 turn
兩段完全沒有觀測**，因此任何 continuity claim 都沒有證據基礎。本票只補這兩段，且
**只觀測已發生的決策，不參與任何決策**。

---

## 範圍

### 新增檔案

| 路徑 | 職責 |
|---|---|
| `src/memory/continuity.py` | 純函式（canonicalize / hash / use heuristic）+ `ContinuityRecorder` + `TurnLinkIndex` |
| `harness/continuity_analyze.py` | 離線 use scoring，支援 `--rescore`（零費用重判）與 `--self-test` |
| `harness/continuity_gc.py` | 30 天 retention GC，**預設 dry-run** |
| `tests/test_memory_continuity_v1.py` | 行為測試 + D4「0 行為變更」硬證據 |
| `tests/infra/test_continuity_sidecar_is_additive.py` | AST 護欄（D4 不可繞過的機械化保證） |
| `docs/MEMORY-CONTINUITY-OBSERVABILITY-V1-IMPLEMENTATION.md` | 工程報告（完成後產出） |

### 修改檔案（**只有這一個**）

- `src/memory/middleware.py` — 純 additive

### 明確不動（見 Out of Scope）

---

## 執行授權邊界（D7：受限授權，Bry 2026-10-07 拍板）

⚠️ **「工單已決策完成」≠「已授權修改 frozen surface」。** 這兩件事分開簽。
本節是 D4 frozen-surface exception 的**執行期護欄**，取代「工單已寫完所以可以動」的推論。

### 允許

| 範圍 | 內容 |
|---|---|
| `src/memory/middleware.py` | **只增加** additive observability sidecar |
| `src/memory/continuity.py` | 新模組（純函式 + recorder + link index） |
| `harness/continuity_*.py` + `tests/**` | 本票新增的分析腳本與測試 |
| retention / GC machinery | 依工單，預設 dry-run |

### 禁止

- ❌ 修改 `eligible` 的內容、順序、threshold
- ❌ 修改 `format_for_prompt()`
- ❌ 修改 `_append_loader_trace()`
- ❌ 修改 SAGE（`src/memory/sage/**`）
- ❌ 修改 `src/llm/proxy.py`
- ❌ 新增任何 online LLM call
- ❌ 改 Soul / persona / config
- ❌ 修改既有 `data/memory/loader_trace.jsonl`

### 人工確認前置（Bry 親自過，執行者不得自審自放）

派 worker 前，三个 insertion points 必須由 Bry 人工檢查通過。確認的不是
「工單怎麼寫」，而是「實作是否忠實執行既定設計」——不重新設計、不擴 scope。

### 🔴 硬性驗收（Bry 指定，不可降格為普通測試項）

> **Observability 是旁路，不能變成 runtime dependency。**
> recording sidecar 失敗**不得**阻止原本的 memory injection。

此條與 T10 連動，但**必須單獨列為驗收項**：即使 sidecar 完全失效，
`memory_context` 仍必須逐 byte 等同於 sidecar 啟用時的結果。

---

## 已驗證座標（2026-10-07 實測，行號為 HEAD `f3348ab`）

| 事實 | 證據 |
|---|---|
| Loader trace 路徑 = `data/memory/loader_trace.jsonl` | `middleware.py:131` |
| `data_dir` 預設 = `data_root() / "memory"` | `middleware.py:107` |
| `Memory` 有 `memory_id / category / tags / confidence / content` | `loader.py:191-224` candidate 欄位 |
| loader `load()` 回傳 `response_id / eligible_memories / trace` | `loader.py:249-253` |
| **`response_id` = loader 在 `load()` 開頭 mint 的 uuid4** | `loader.py:111` |
| injection 發生點 | `middleware.py:345-346`（`format_for_prompt(eligible)` + 拼進 context） |
| `eligible` 順序 == `format_for_prompt` 輸出順序 == `injection_rank` | `loader.py:214-224` append 順序 |
| **join key 已證明存在**：`AGENT_SPEAK.correlation_id == 注入當下 AGENT_INTENT.(correlation_id or event_id)` | `middleware.py:397`（re-publish 透傳）→ `src/llm/proxy.py:4074` 與 `:4156` |
| `AGENT_SPEAK.payload` 有 `text`（整合後廣播文字）、`text_jp`、`is_stub`、`reason` | `src/llm/proxy.py:4081-4090`、`:4150-4170` |
| **`data/conversations/*.json` 無 timestamp / event_id / correlation_id** | 實測檔案內容（不可用於 join） |
| `_on_agent_speak` 在主體前有 **2 個 early return** | `middleware.py:448-449`（cooldown）、`:456-461`（空 text） |

### 🔴 由「`_on_agent_speak` 有 early return」直接導出的強制設計決定

Layer 3 的寫入**必須是 `_on_agent_speak` 的第一個 statement**（`:407` 之後、
`agent_id = ...` 之前）。

理由：stub 路徑（`proxy.py:4151`）發出的 `AGENT_SPEAK` 帶 `text=""` 且 `is_stub=True`，
必然撞上 `:456-461` 的 early return。若 sidecar 寫在主體內，**所有 stub turn 會靜默消失**
—— 那正是 Bry 已鎖死的 P5 違反（缺資料被當不存在，而非標 gap）。

因此 `response_status` 必須區分 `ok` / `stub` / `empty_text`，讓 stub turn 留下可辨識的
gap 標記，而不是消失。

---

## Schema（凍結）

### Layer 2 — Injection Snapshot（每 memory 一列）

檔案：`data/memory/continuity_injection.jsonl`（append-only JSONL）

```json
{
  "schema_version": "continuity_injection_v1",
  "snapshot_id": "<uuid4，單次注入共用同一值>",
  "event_id": "<AGENT_INTENT.event_id>",
  "correlation_id": "<AGENT_INTENT.correlation_id or event_id>",
  "session_id": "<AGENT_INTENT.session_id>",
  "agent_id": "<payload.agent_id>",
  "memory_id": "<Memory.memory_id>",
  "memory_text": "<Memory.content 原文，即實際注入的 memory text>",
  "memory_text_hash": "<sha256(canonicalize(memory_text))>",
  "retrieval_confidence": "<Memory.confidence>",
  "injection_rank": 0,
  "injection_timestamp": "<UTC ISO8601>",
  "response_id": "<loader uuid4，對應 loader_trace 的 response_id>",
  "retention_expires_at": "<UTC ISO8601 = injection_timestamp + 30d>"
}
```

`injection_rank` = 該 memory 在 `eligible` 清單中的 0-based index。

### Layer 3 — Turn Sidecar（每 turn 一列）

檔案：`data/memory/continuity_turn.jsonl`（append-only JSONL）

```json
{
  "schema_version": "continuity_turn_v1",
  "response_id": "<本列自己的 uuid4>",
  "event_id": "<AGENT_SPEAK.event_id>",
  "correlation_id": "<AGENT_SPEAK.correlation_id>",
  "session_id": "<AGENT_SPEAK.session_id>",
  "agent_id": "<payload.agent_id>",
  "injection_snapshot_id": "<Layer 2 的 snapshot_id；查無則 null>",
  "link_status": "linked | unlinked",
  "response_status": "ok | stub | empty_text",
  "response_text": "<實際 response text 原文>",
  "response_text_hash": "<sha256(canonicalize(response_text))>",
  "use_heuristic": {
    "candidate": false,
    "matched_memory_ids": [],
    "matched_ngram_count": 0,
    "algorithm_version": "ngram4-v1"
  },
  "timestamp": "<UTC ISO8601>",
  "retention_expires_at": "<UTC ISO8601 = timestamp + 30d>"
}
```

### 🔴 Join Key ≠ Identity（Bry 2026-10-07 D7 補充，必須寫進 code comment）

四個識別碼的**角色完全不同**，不得互相替代。錯誤使用會靜默產生錯誤 lineage：

| 欄位 | 角色 | 定義 | 唯一性範圍 |
|---|---|---|---|
| `correlation_id` | **Join key**（關聯同一次 lifecycle chain） | `AGENT_INTENT.(correlation_id or event_id)`，由 `middleware.py:397` 透傳 | **不唯一**：同一 chain 內 Layer 2 與 Layer 3 共用同一值 |
| `snapshot_id` | **一次 injection observation 的身分** | 單次注入共用同一 uuid4，其下多個 memory row 共用 | 每回合 1 個 |
| Layer 2 `response_id` | **loader trace 自己的 row identity** | `loader.py:111` mint 的 uuid4 | 每次 `load()` 1 個 |
| Layer 3 `response_id` | **turn observation 自己的 row identity** | recorder 為該列 mint 的 uuid4 | 每 turn 1 個 |

**一句話：`correlation_id` 是關聯用的，`snapshot_id` / 兩層的 `response_id` 是辨識用的。
Layer 2 ↔ Layer 3 的 join 走 `correlation_id`（或 `injection_snapshot_id`），
絕不是走 `response_id`。**

未來有人看到兩個都叫 `response_id` 的欄位時，這段必須讓他一眼看出不可直接 join。

### 主大腦新增欄位（4 + 3，明列供 Bry 否決）

| 新增 | 欄位 | 理由 |
|---|---|---|
| Layer 2 | `snapshot_id` | Layer 3 需要指向「一次注入」的識別碼，per-memory row 無法自成一組 |
| Layer 2 | `correlation_id` | **這是唯一經傳播鏈證明的 join key**，不加則 lineage 接不起來 |
| Layer 2 | `agent_id` | D3 要求 agent 分層抽樣，snapshot 必須帶 agent 身分 |
| Layer 3 | `correlation_id` | join 鍵 |
| Layer 3 | `link_status` | P5：join 失敗必須是**可見的 gap**，不是靜默 null |
| Layer 3 | `response_status` | 同上，stub turn 不可消失 |
| Layer 3 | `response_id` 改為本列 uuid4 | 避免與 Layer 2 的 loader uuid4 混淆 |

**不新增**：`prompt`、`conversation_history`、`memory_context` 全文。
D2 紅線：**不得保存完整 conversation prompt**。

---

## 演算法（凍結，不得自行調整）

### canonicalize（唯一實作，兩個 hash 都用它）

```python
def canonicalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()
```

**不去除空白、不去標點。** 保留可審計性，並把此限制寫進 code comment
（`"喜歡,咖啡"` 與 `"喜歡咖啡"` 不會互相命中 → 該 heuristic 是保守的，會低估而非高估）。

### use heuristic（Bry 凍結參數）

```
grams(s)  = { norm[i:i+4] for i in range(len(norm)-3) }   # norm = canonicalize(s)
                                              # len(norm) < 4 → 空集合
hits(m, r) = grams(memory_text) & grams(response_text)
use_candidate = len(hits(m, r)) >= 2          # 至少 2 個【彼此不同】的 4-gram
matched_ngram_count = len(hits(m, r))
algorithm_version = "ngram4-v1"
use_confidence = "heuristic"                   # 不得寫成 memory_used = true
```

Layer 3 rollup：`candidate` = 是否有任一 memory 命中；`matched_memory_ids` = 命中的
memory_id 清單；`matched_ngram_count` = 命中的 memory 各自 count 加總。

**寫入時即計算**（deterministic、零 LLM 呼叫、零費用），但 `harness/continuity_analyze.py
--rescore` 必須能從 `response_text` + Layer 2 `memory_text` **離線完整重算**，
`algorithm_version` 不符時標記待重算。這是 SOUL-REM-01 已驗證的 `--rescore` 模式。

### TurnLinkIndex

```
key   = correlation_id
value = {"snapshot_id": str, "linked_at": monotonic_ts}
TTL   = 15 分鐘；上限 200 筆；超限或過期 → 逐出最舊
```

- 只在 `_on_agent_intent` 寫入
- 只在 `_on_agent_speak` 讀取並 pop
- 查無 → `injection_snapshot_id=None` + `link_status="unlinked"`（**不是 exception**）

---

## 設定

- 環境變數 `SOULOS_CONTINUITY_OBSERVABILITY`：`"1"` = 啟用（**預設**），`"0"` = 完全停用
  （連檔案都不開）。讀取在 `continuity.py` 的 `enabled()`，可被 monkeypatch。
- 啟用需要**重啟服務**才生效。**agent 不得重啟服務**（鐵律），由 Bry 執行。

---

## 精確插入點（三處，全部 additive）

1. **`middleware.py:134` 之後**（`_loader = None` 下方）：初始化
   ```python
   self._continuity = ContinuityRecorder(data_dir=self.data_dir, enabled=enabled())
   self._turn_links = TurnLinkIndex()
   ```
   整段包 try/except；建構失敗 → 設 `None` → 所有 sidecar 呼叫短路 + `logger.warning`。
   **不得讓 recorder 的存在影響 middleware 建構。**

2. **`middleware.py:352` 之後、`:353`（`else:`）之前**：在 `if eligible:` 內，
   `logger.info(...)` 結束後呼叫 `self._continuity.record_injection(...)`。
   - 只在 `eligible` 非空時寫（空集合無可觀測之注入，與 fail-safe 分支一致）
   - **禁止**移到 `if load_result is not None:` 之外
   - **禁止**放在 `:362` `_append_loader_trace` 之前或取代它
   - 傳入的 `eligible` **只讀**，不得 `.append/.pop/.sort/.remove` 或重新賦值

3. **`middleware.py:407` 之後、`agent_id = event.payload.get("agent_id")` 之前**：
   `_on_agent_speak` 的**第一個 statement** 呼叫 `self._continuity.record_turn(event)`。
   理由見上方「🔴 early return」段落。放在這裡可確保 cooldown 與空 text 的
   turn 仍留下帶 `response_status` 的紀錄。

### 三個插入點的逐點驗收（Bry D7 指定四條）

1. **`_on_agent_speak` — 觀測在 gate 之前**
   ```
   _on_agent_speak(...)
       ↓
   [record Layer-3 observation]   ← 必須在此
       ↓
   cooldown?  /  empty_text?      ← 兩個既有 early return
       ↓
   existing behavior
   ```
   stub 的 `text=""` 必須留下 `response_status = stub / empty_text`，
   **不得**被 handler 既有 return 靜默吃掉。

2. **Injection snapshot — 在 eligible 還活著的地方取**
   ```
   eligible
       ↓
   snapshot(id + canonical text + lineage metadata)
       ↓
   原本的 format / injection
   ```
   - **禁止**反過來從 rendered prompt 做 parser（`format_for_prompt()` 不含 memory_id）
   - **禁止**在 snapshot 之後才做任何 post-processing

3. **Sidecar / GC — 新檔案，預設不破壞性**
   - 寫入新的 continuity sidecar，**不得** append 到既有 `loader_trace.jsonl`
   - GC 預設 dry-run；真正 destructive cleanup 必須明確 opt-in（`--apply`）

---

## 失效隔離（D4 靈魂）

`continuity.py` 內**所有**寫檔操作各自包 try/except，失敗只 `logger.warning`，
**絕不 raise 進主路徑**。`record_injection` / `record_turn` 本身也各自整段 try/except。

寫入失敗必須可見（warning log），但不可阻斷 memory injection 或回覆生成。

---

## Retention 與 GC（D2：30 天）

- 兩層都在寫入時填 `retention_expires_at = timestamp + 30d`（UTC ISO8601）
- `harness/continuity_gc.py`：
  - **預設 dry-run**：只印出「將刪 N 筆 Layer2 / M 筆 Layer3」與時間區間
  - `--apply` 才實際改寫（就地重寫為保留列 + 原子 replace）
  - **agent 不得對 `data/**` 執行 `--apply`**（鐵律 #8）。此腳本預設 dry-run
    正是為了讓 agent 可以安全地跑診斷
- 過期的 sidecar **不得**升格進 Soul Memory / Palace（Bry 責任分離原則：
  Soul Memory 長期、Conversation 生命史、Observability 30 天暫存）

---

## Frozen Contract 注意（D4 措辭，全文照抄進 code review 檢查表）

> 不得改變任何 memory selection / rejection / injection behavior；
> sidecar 只能觀測已經發生的決策。
> 不得讓 snapshot 變成「順便調整 eligible 結構」的入口。

以下**逐項不得改動**：

| 項目 | 現況位置 |
|---|---|
| `eligible_memories` 的篩選結果與順序 | `loader.py:190-236` |
| `confidence_threshold` / `threshold_for()` | `loader.py:216` |
| fail-safe 三條語意 | `loader.py:76`、`114-155`、`228-233` |
| `format_for_prompt()` 輸出（逐 byte） | `loader.py:296-309` |
| `memory_context` 拼進 `event.payload` 的字串 | `middleware.py:371` |
| Memory / SAGE 語意 | `src/memory/sage/**` |

**零 LLM 呼叫**（P4）：`continuity.py` 不得 import 或參照
`src/llm/proxy.py`、`httpx`、`openai`、`anthropic`、`google.generativeai`。
本票**不得**恢復 shadow log（維持 `scripts/run_server.py:1229` `enabled=False`）。

---

## 驗收（完成的定義）

1. `git status` 在本工單路徑下只出現：`middleware.py`（modified）+ 上表 6 個新檔案。
   **既有 7 個未提交改動與所有 untracked 檔案必須原樣不動。**
2. `tests/test_memory_continuity_v1.py` 全綠。
3. `tests/infra/test_continuity_sidecar_is_additive.py` 全綠。
4. `tests/infra` 既有 2 個紅點數量**不變**（spawn guard allowlist 行號漂移、
   `test_life_thread_elevation_seam.py` 未追蹤 —— Bry 已裁決維持不動）。
5. `harness/continuity_analyze.py --self-test`：用 synthetic 資料，
   Layer 2 → Layer 3 join 成功率 100%，且 4-gram 邊界案例（1 個命中、2 個命中、
   空文字、< 4 字元）判定全對。
6. `harness/continuity_gc.py`（dry-run）可跑，輸出合理筆數，且**未改動任何檔案**。
7. 🔴 **旁路性硬驗收（D7）**：sidecar 完全失效時（monkeypatch 寫檔必失敗），
   `memory_context` 仍與 sidecar 啟用時**逐 byte 相同**。Observability 不得
   變成 runtime dependency。
8. 🔴 **stub 可見性硬驗收**：構造 `text=""` + `is_stub=True` 的 `AGENT_SPEAK`，
   `continuity_turn.jsonl` **必須新增一列**且 `response_status ∈ {stub, empty_text}`。
   零新增列 = 不合格。
7. 客觀痕跡（不可用自報）：
   - `data/memory/loader_trace.jsonl` 的 **大小與 mtime 在實作前後完全不變**
     （證明 sidecar 沒有混寫進既有檔）
   - **未重啟服務、未殺任何行程、未發任何真實請求**
   - `.venv\Scripts\python.exe` 為唯一測試直譯器

---

## 測試（必寫，逐項對應）

`tests/test_memory_continuity_v1.py`：

- **T1 canonicalize / hash**：中英日、emoji、CRLF 皆 deterministic；同輸入同 hash
- **T2 injection schema 完整**：11 個必填欄位齊全、型別正確、`injection_rank` 連續
- **T3 rank 對應**：`eligible` 第 k 筆 → `injection_rank=k`，且 `memory_text` 等於
  `format_for_prompt` 實際渲染出去的 content
- **T4 4-gram 邊界**：0 命中 / 1 命中（不算）/ 2 個**不同** 4-gram（算）/ 重複同一
  4-gram 兩次（不算，因為是 set）/ `len(norm) < 4`
- **T5 use 標記語意**：寫入的是 `use_confidence="heuristic"` + `use_candidate`，
  **全檔不得出現 `memory_used`**（AST/字串斷言）
- **T6 join**：`correlation_id` 一致時 `link_status="linked"` 且
  `injection_snapshot_id` 正確；不一致 → `"unlinked"` + null，**且不拋例外**
- **T7 stub turn**：`text=""` + `is_stub=True` 時仍寫出一列，
  `response_status="stub"`，`response_text` 為空字串
- **T8 TurnLinkIndex 邊界**：TTL 過期、上限 200 逐出、pop 後不可重複消費
- **T9 retention**：`retention_expires_at == timestamp + 30d`（容差 1 秒）
- **T10 寫入失敗隔離**：monkeypatch 讓寫檔丟例外 → 主路徑不中斷，
  `memory_context` 仍正確注入，`record_*` 不 raise
- **T11 `--rescore` 等價**：改 `algorithm_version` 後重算，結果與原始一致
- **T12 GC dry-run**：執行後兩個 jsonl **位元組完全未變**

`tests/infra/test_continuity_sidecar_is_additive.py`：

- **G1** `record_injection` 函式體內**不得**出現對 `eligible` / `load_result` 的
  賦值，或 `.append/.pop/.sort/.remove/.clear`
- **G2** `continuity.py` 不得出現任何 LLM client import（白名單外全禁）
- **G3** `format_for_prompt` 函式碼 AST 與 HEAD 版本逐節點相同
- **G4** `continuity.py` 不得 import `src.memory.sage`
- **G5** `middleware.py` 中 `_append_loader_trace` 函式體未被改動

**T11/G3 的比對基準**：用 `git show HEAD:src/memory/v1/loader.py` 取原始碼做 AST 比對，
不要靠行號。

---

## Out of Scope（本票不做）

- **SAGE prefetch 觀測**：D1 明確不碰，`src/memory/sage/**` 零改動
  （約 60% coverage 是**已知限制**，須在報告中明寫，不得隱瞞）
- `src/memory/v1/loader.py` 零改動（含 `format_for_prompt`）
- `src/llm/proxy.py` 零改動（`correlation_id` 已足夠，不需新增欄位）
- `personas/**` 零改動（`agent_rem.md` 已 FREEZE）
- 不恢復 shadow log（維持 `enabled=False`）
- **不做 influence replay**（60 組 paired cases、雙盲 judge 是**下一張票**）
- 不改、不 migration `loader_trace.jsonl` 的既有 schema
- 不寫、不改名、不刪除 `data/**` 任何既有檔案
- 不重啟服務、不殺任何行程（鐵律 #1）
- 不做 downsampling

---

## 回報格式

1. 改動檔案清單（modified / new 分開）
2. 測試結果：跑了哪些指令、過幾筆、幾失敗，**並聲明用的是
   `.venv\Scripts\python.exe`**
3. **Frozen Contract 逐項自檢**：上表 6 項各自「已驗證未變」或「無法驗證 + 原因」
4. **客觀痕跡**：`loader_trace.jsonl` 前後大小/mtime、服務 PID 未變、未發請求
5. **已知限制**明列（至少：SAGE 覆蓋缺口、4-gram 不去標點的保守偏差、
   `TurnLinkIndex` 是記憶體狀態 —— 服務重啟或事件跨 process 會產生
   `link_status="unlinked"` gap，這是設計上的可見缺口而非缺陷）
6. **不得**宣稱「continuity 已可量測」—— 只能宣稱
   「injection 與 turn 兩段已具備觀測能力，use 可離線推導，
   influence 尚未建立」