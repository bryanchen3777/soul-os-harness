# LIFE-THREAD-M2-EXEC-COST-CALIBRATION

**票號**：`LIFE-THREAD-M2-EXEC-COST-CALIB-1`
**日期（UTC）**：2026-09-17
**基線**：`HEAD == origin/main == f9b7273`
**目的**：量測「線頭沉澱」的**單次真實成本與延遲**，作為「要不要開啟自動沉澱」的決策依據。

---

## 1. 授權範圍與實際付費請求數

| 項目 | 值 |
|---|---|
| Owner 授權 | **恰好 1 次**真實 LLM 呼叫 |
| **實際發出的付費請求數** | **1**（`PAID_REQUEST_COUNT = 1`，上限 1） |
| 重試次數 | **0**（硬性：無 retry 迴圈、無 `max_retries` 參數、無網路/429/5xx/逾時重試） |
| 結果 | **成功**（HTTP 200，`finish_reason=stop`，沉澱 `status=consolidated`） |

### 1.1 證據（腳本硬計數器輸出，逐字）

```
[HARD-COUNTER] claimed paid request slot 1/1
...
PAID_REQUEST_COUNT = 1 (limit 1)
```

計數器實作（`%TEMP%\m2exec_calib\m2exec_calib.py`）：`claim_paid_request_slot()` 在**任何**
`httpx` POST 之前被呼叫；當 `PAID_REQUEST_COUNT >= 1` 時直接
`raise HardStopPaidRequestLimit`（`SystemExit` 子類）⇒ 第 2 次呼叫在**送出前**即被硬擋。
該函式是通往 `client.post(...)` 的**唯一**路徑。

### 1.2 未送出 vs 已送出但失敗的判定方式

- `request_sent` 旗標**只在 `client.post()` 回傳後**（或拋出 transport 例外時）才寫入
  receipt；因此 `request_sent=false` ⇒ 確定**未送出**（純客戶端 bug／硬計數器擋下）。
- 本次 `request_sent=true` + `http_status=200` ⇒ 請求**確實送出且成功**。
- 若該次失敗：receipt 會記錄 `transport_error`（類名）或 `error_status` +
  `error_body_redacted`，且腳本**立即結束、不重試**，改由下一輪人工重新授權。

---

## 2. 模型與 API 路徑（步驟 1，唯讀）

### 2.1 目前生效值

| 項目 | 值 | 來源 |
|---|---|---|
| provider | `ollama` | `configs/default.yaml:104`（`LLM_PROVIDER` 可覆寫；`.env` 亦為 `ollama`，兩者一致） |
| **model** | **`deepseek-v4.1-flash`** | `configs/default.yaml:112`（`LLM_MODEL` 可覆寫；`.env` 生效值同） |
| base_url | `https://ollama.com/v1/chat/completions` | `configs/loader.py:157`（Ollama Cloud OpenAI-compat endpoint） |
| backend class | `OpenAIBackend` | `configs/loader.py:155` → `src/llm/proxy.py:1545` |
| **金鑰來源變數名** | **`OLLAMA_API_KEY`** | `configs/loader.py:148` |
| 金鑰是否已設定 | **是**（長度 57；**值不在本檔、不在任何輸出**） | — |

> 金鑰值全程 redact（receipt 內記為 `<redacted:OLLAMA_API_KEY:len=57>`）。

### 2.2 若 orchestrator 真的呼叫沉澱，會走哪個模型？

**`deepseek-v4.1-flash`（provider `ollama`，Ollama Cloud OpenAI-compat）。**

完整路徑（唯讀追蹤）：
```
scripts/run_server.py:589   create_llm_proxy(cfg, bus)
configs/loader.py:175      → create_llm_backend(cfg)            # ollama ⇒ OpenAIBackend(base_url=ollama.com/v1/chat/completions)
src/llm/proxy.py:3344      → LLMProxy(model=cfg.llm.model)
scripts/run_server.py:1060 → src.soul.motive.set_llm_proxy(llm)
src/soul/motive.py:469     → _default_llm_call(...) → proxy.generate_text(...)
```
`LLMProxy.generate_text`（`src/llm/proxy.py:4323`）是既有「通用短文本」通道，也是
`_default_llm_call` 唯一會呼叫的代理方法。

### 2.3 ⚠️ 兩項必須先講清楚的落差（影響成本紀律）

1. **執行層介面與生產通道簽名不相容（尚未接線）**：
   `consolidate_terminal_thread` 要求的 `llm_call` 是 `llm_call(prompt: str, max_tokens: int)`
   （`src/soul/life_thread_dissolution_exec.py:627`），而 `_default_llm_call` 的簽名是
   `(messages, agent_id, max_tokens, temperature)`。**目前全庫 0 個檔案 import 執行層**
   （該模組 docstring 自述「0 生產接線」）⇒ orchestrator 現階段**根本不會呼叫沉澱**。
   接線所需的 adapter 屬後續票，不在本票範圍。
2. **既有生產通道自帶重試，不符合「恰好 1 次」**：
   `_default_llm_call` → `generate_text` 自帶 1 次重試（`proxy.py:4353` 的
   `for attempt in range(2)`），且 `OpenAIBackend.complete` 另有 `max_retries=3` 預設
   （`proxy.py:1613`）⇒ **最壞可放大到 8 次 HTTP 請求**。
   本票**刻意繞過這兩層重試**，只發 1 次。這是**本票與生產通道的差異**，已在第 7 節申報。

---

## 3. 實測結果（步驟 3）

### 3.1 receipt（`%TEMP%\m2exec_calib\receipt.json`，redacted，逐字）

```json
{
  "ticket": "LIFE-THREAD-M2-EXEC-COST-CALIB-1",
  "authorization": "Owner authorized EXACTLY 1 real LLM call",
  "paid_request_limit": 1,
  "paid_request_count": 1,
  "paid_request_slot": 1,
  "request_sent": true,
  "retries": 0,
  "model": "deepseek-v4.1-flash",
  "provider": "ollama",
  "base_url": "https://ollama.com/v1/chat/completions",
  "api_key_env_var": "OLLAMA_API_KEY",
  "api_key": "<redacted:OLLAMA_API_KEY:len=57>",
  "timeout_client_seconds": 60.0,
  "timeout_patched_seconds": 60,
  "timeout_original_seconds": 20,
  "max_tokens": 300,
  "http_status": 200,
  "latency_ms": 26335.6,
  "finish_reason": "stop",
  "usage_source": "api_response.usage",
  "prompt_tokens": 121,
  "completion_tokens": 1790,
  "total_tokens": 1911,
  "usage_raw": {
    "prompt_tokens": 121,
    "prompt_tokens_details": { "cached_tokens": 0 },
    "completion_tokens": 1790,
    "total_tokens": 1911
  },
  "usage_local_estimate": null,
  "response_chars": 101,
  "response_text": "{\"dissolution\": \"我以為自己在救一盆薄荷，其實是在練習接受它已經枯了。七天的照顧沒有讓它復活，卻讓我明白，告別也可以是一種溫柔的照顧。\", \"meaning_kind\": \"loss\"}",
  "response_is_parseable_json": true,
  "response_has_dissolution": true,
  "dissolution_chars": 58,
  "meaning_kind": "loss",
  "meaning_kind_legal": true,
  "consolidation_status": "consolidated",
  "consolidation_reason": "ok",
  "consolidation_llm_calls": 1,
  "fact_writer_calls": 1,
  "sage_writes": 0,
  "data_writes": 0,
  "prompt": {
    "provenance": "contract_example",
    "sample_count": 0,
    "representative_chars": 177,
    "representative_title_chars": 14,
    "representative_days": 7,
    "worst_case_title_chars": 40,
    "worst_case_chars": 203,
    "sedimentation_context_chars_not_sent": 301,
    "soul_context": null,
    "source_fields": ["title", "days(created_at->now)", "fixed question", "fixed output contract"],
    "narrative_content_in_prompt": false
  },
  "transport_error": null,
  "error_body_redacted": null
}
```

### 3.2 tokens 為**實測值**（非估算）

`usage_source = "api_response.usage"`：直接取自**該次 HTTP 200 回應的 `usage` 欄位**
（最底層那一次回應，**未為此多發任何請求**）。`usage_local_estimate = null`
（`src/memory/sage/token_utils.py` 的本地估算**未被使用**）。

| 指標 | 實測值 |
|---|---|
| `prompt_tokens` | **121** |
| `completion_tokens` | **1790** |
| `total_tokens` | **1911** |
| latency | **26 335.6 ms**（≈ 26.34 s） |
| `finish_reason` | **`stop`** |
| 回應可解析性 | **合法 JSON**，含非空 `dissolution`（58 字元） |
| `meaning_kind` | **`loss`**（∈ 6 值合法枚舉） |
| `ConsolidationResult` | `status=consolidated`、`llm_calls=1`、`reason=ok` |

### 3.3 🔴 兩個必須回報 Owner 的實測異常

1. **`max_completion_tokens=300` 未被 provider 遵守**：
   實測 `completion_tokens = 1790`，是名目上限的 **約 6 倍**，而可見回應僅 101 字元
   ⇒ 約 1690 tokens 屬**未顯示的推理（reasoning）token**，仍計費。
   **結論：300 token 不是成本上界**，只是一廂情願的參數。
2. **生產預設逾時 20 s 比實測延遲短**：
   `CONSOLIDATION_TIMEOUT_SECONDS = 20`（`life_thread_dissolution_exec.py:103`），
   而實測單次 **26.34 s** ⇒ 若照生產預設，這次呼叫會在 20 s 被
   `asyncio.wait_for` **客戶端 abort**，但**付費請求已送出**（推理成本已發生），
   且結果被丟棄 ⇒ `llm_failed` + 白花錢 + 下一輪重試再花一次。
   **本票據此把該常數 monkeypatch 為 60 s**（見第 7 節）。

---

## 4. prompt 形狀分佈與最壞情況上界（步驟 2）

### 4.1 資料來源：**0 個真實樣本**

唯讀掃描 `data/soul/*/life_threads.jsonl`：

| 項目 | 值 |
|---|---|
| 找到的檔案數 | **0** |
| 讀到的行數 | 0 |
| **終態樣本數（`completed`/`abandoned`）** | **0** |
| title 長度分佈（min／median／p90／max） | **無法計算**（n=0） |
| 歷時天數分佈（min／median／p90／max） | **無法計算**（n=0） |

> **事實**：`life_threads.jsonl` 在 `data/` 下**完全不存在**（M1 尚未在生產落盤任何線頭）。
> 因此本節的分佈統計**無觀測依據**，改採**契約示例**當代表（票面允許）。

### 4.2 代表性合成 thread（契約示例）

| 欄位 | 採用值 | 依據 |
|---|---|---|
| `title` | `想把陽台那盆枯掉的薄荷救回來`（14 字元） | 契約 §2.2 表格的逐字範例（`docs/LIFE-THREAD-ENGINE-CONTRACT.md:94`） |
| `narrative_content` | 合成敘事（見下） | 契約 §2.2 範例語意（`:95`） |
| `origin_type` | `world_collision` | 契約 §2.2 範例值 |
| `days` | **7** | repo 自身既有 fixture `tests/soul/test_life_thread_dissolution_exec.py:53`（「歷時 7 天」） |
| `agent_id` | `agent_m2exec_calib` | 合成 |

### 4.3 prompt 由**真實 M2 程式碼**產生

走 M2 真實公開入口 `evaluate_thread_dissolution(thread, now, agent_id=...)`
（`src/soul/life_thread_dissolution.py:366`）⇒ `target_status=completed`、
`reason=goal_achieved`、`should_mutate=True`、`reflection_prompt` 非空；
再以 `_build_sedimentation_context`（`:284`）產生沉澱輸入。
**這是生產會送出的同一段 prompt 形狀。**

代表性 prompt（合成資料，逐字）：

```
你剛完成一條生活線頭：「想把陽台那盆枯掉的薄荷救回來」，歷時 7 天。
以第一人稱回答：這段經歷，對「我」意味著什麼？
請只回 JSON：{"dissolution": "<第一人稱 1~3 句、<=400 字元>", "meaning_kind": "competence|connection|loss|relief|discovery|none"}
```

### 4.4 prompt 組成與字元數

| 項目 | 值 |
|---|---|
| **prompt 總字元數（代表）** | **177** |
| prompt 總字元數（title 取契約上界 40 字元） | **203** |
| `sedimentation_context` 字元數 | 301（**執行層不送出**；僅存於 `extra_metadata`） |
| **是否有 `soul_context`** | **`None`**（契約 §3.2 明訂 `load_persona` 由 M5 呼叫端補；`soul_context` **不在 prompt 內**） |
| prompt 來源欄位 | `title`、`days`（`created_at → now` 的 `.days`）、固定提問句、固定輸出契約尾綴 |
| `narrative_content` 是否在 prompt 內 | **否** |

> 🔴 **重要發現**：`_build_reflection_prompt`（`:265-281`）**只使用 `title` 與 `days`**。
> `narrative_content`（「溶解的唯一事實輸入」，契約 §3.2）與 `soul_context`
> **都沒有進入送給 LLM 的 prompt**，只落在 `sedimentation_context` 這個**未被執行層讀取**的
> metadata。⇒ 目前 prompt 幾乎是**常數長度**，與線頭敘事長度**無關**。
> （此為 M2 決策層既有行為，本票**只回報、不修改**。）

### 4.5 最壞情況上界（prompt tokens）

以實測比值校準：`121 tokens / 177 chars = 0.684 tokens/char`。

| 情境 | prompt 字元 | prompt tokens（**估算**） |
|---|---|---|
| 代表（title 14 字元） | 177 | **121（實測）** |
| 契約上界（title 40 字元） | 203 | ≈ 139（等比縮放） |
| 契約上界（保守：多出的 26 個 CJK 字元各計 1 token） | 203 | ≈ 147 |

> **最壞情況 prompt tokens 上界 ≈ 150（估算，非實測）。**
> 由於 `days` 最多貢獻數個字元，此上界對「線頭活多久」幾乎不敏感。
> **真正的成本主項是 completion（實測 1790），不是 prompt（實測 121）。**

---

## 5. 每日成本含意表

**單次實測基準**：prompt 121 ＋ completion 1790 ＝ **1911 tokens／次**。

### 5.1 以執行層兩道上限換算

| 上限 | 來源 | 呼叫數／日 | prompt tokens／日 | completion tokens／日 | **合計 tokens／日** |
|---|---|---|---|---|---|
| **單 agent 滿載** | `MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY = 24` | 24 | 2 904 | 42 960 | **45 864** |
| **全域滿載** | `MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY = 200` | 200 | 24 200 | 358 000 | **382 200** |

（常數位置：`src/soul/life_thread_dissolution_exec.py:106,109`）

### 5.2 兩道上限誰先咬住（治理要點）

- 全域上限 ÷ 單 agent 上限 ＝ 200 ÷ 24 ＝ **8.33**
  ⇒ **第 9 個 agent 起，全域 200 先咬住**，單 agent 的 24 已不可能全員跑滿。
- 本機 `configs/default.yaml` 啟用 **10 個 agent**（契約 §8.3）⇒ **全域 200 才是實質上界**。

### 5.3 延遲含意（實測 26.34 s／次）

| 情境 | 串行 LLM 時間 |
|---|---|
| 單 agent 滿載（24 次） | ≈ 632 s ≈ **10.5 分鐘／日** |
| 全域滿載（200 次） | ≈ 5 267 s ≈ **87.8 分鐘／日** |

### 5.4 與契約舊值的落差（回報，不改）

契約 §3.6 寫 `DISSOLVE_MAX_PER_DAY = 3`，而執行層常數是
`MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY = 24`（**8 倍**）。
本檔依票面要求以 **24／200** 換算；**兩者不一致一事回報主大腦裁決**。

### 5.5 單價

**單價待主大腦確認，本檔不含價格臆測。**

已查證：`configs/**` 與 `docs/**` 內**不存在**任何 Ollama Cloud 單價（每百萬 token 價格）
記載（grep `單價|每百萬|per million|USD|pricing` 無命中）。
因此本檔只提供 **token 用量**，換算金額需 Owner／主大腦提供單價後另行補算。

---

## 6. 不管道／不回寫聲明

| 聲明 | 實作 |
|---|---|
| **不寫入 SAGE** | `fact_writer` 為**假物件** `FakeFactWriter`：只把收到的 fact 記在記憶體 list 並回傳假 id `FAKE_FACT_ID_NOT_PERSISTED_0001`；**不呼叫 `MemoryWriter.add_fact`、不觸碰 `src/memory/sage/**`**。`sage_writes = 0` |
| **不寫入 `data/**`** | 全腳本對 `data/**` **只讀**（讀 `data/soul/*/life_threads.jsonl`，實得 0 檔）。`data_writes = 0` |
| **不啟動伺服器／不綁埠** | 無 `run_server`、無 `uvicorn`、無 `bind`；未對 `:8000`／`:8765`／`:8766`／`:8767` 發任何請求 |
| **不殺行程** | 全程 0 個 kill 類指令 |
| **暫存位置** | 腳本與 receipt 全在 `%TEMP%\m2exec_calib\`；repo 內**只新增本檔** |

**receipt 內 `data_writes: 0`、`sage_writes: 0`、`fact_writer_calls: 1`（呼叫但未落地）。**

---

## 7. 偏離、不確定點與紅線自檢

### 7.1 偏離（主動申報）

| # | 偏離 | 理由 | 風險 |
|---|---|---|---|
| D-1 | **`CONSOLIDATION_TIMEOUT_SECONDS` 20 → 60（monkeypatch）** | 避免「付了錢卻在 20 s 被 abort」（實測需 26.34 s）。**已在 receipt 記錄 `timeout_original_seconds=20`／`timeout_patched_seconds=60`** | 僅影響本次 scratch 執行；**未修改 repo 內任何常數** |
| D-2 | **未使用 `LLMProxy.generate_text`，改為直呼同一 endpoint 的單次 POST** | 生產通道自帶 **雙層重試**（最壞 8 次請求）＋ 4xx 時會**寫入 `data/logs/llm_4xx_response.log`**（`proxy.py:1691-1700`）⇒ **同時違反「恰好 1 次」與「不得寫入 data/**」兩條紅線** | 請求 body 逐欄比對生產 `generate_text`／`OpenAIBackend.complete`：`model`、`messages`、`max_completion_tokens`、`temperature` 皆同；**未帶 `response_format`**（與 `generate_text` 一致）。差別僅在**移除重試**與**移除 4xx 落檔** |
| D-3 | 代表性輸入**非觀測資料**（0 樣本），改用**契約示例** | 票面明訂的 fallback | prompt tokens 的代表性未經真實分佈驗證 |
| D-4 | `days = 7` 取自 repo 既有 fixture，非觀測中位數 | 無觀測樣本可用 | `days` 對 prompt 長度影響 ≤ 數個字元，影響可忽略 |

### 7.2 不確定點（誠實）

1. **tokens 是實測還是估算？**
   **本次為實測**（`prompt_tokens=121`／`completion_tokens=1790`／`total_tokens=1911`，
   取自 HTTP 200 回應的 `usage`）。**唯一的估算**是 §4.5 的「最壞情況 prompt tokens
   上界 ≈ 150」——該數字是**由實測比值外推的估算**，已明確標示。
2. **單次樣本**：n=1，無法給出延遲或 token 的變異數／p90。26.34 s 與 1790 completion
   都只是**一個觀測點**。
3. **`max_completion_tokens` 失效的通用性未知**：無法從單次判定是「此 model 一律忽略」
   還是「本次特例」；也不確定 1790 這個量級是否會隨 prompt 或思考長度上升。
4. **`narrative_content` 未進 prompt** 是本次對 M2 原始碼的靜態發現，**未經真實線頭驗證**
   （因無任何真實線頭）。
5. **執行層尚未接線** ⇒ 本量測反映的是「**若接線後會長什麼樣**」，
   而非「**生產現在正在花的錢**」（現況為 0）。

### 7.3 紅線自檢（逐字列出所有與行程／網路有關的命令）

| 命令／動作 | 次數 | 說明 |
|---|---|---|
| `httpx.AsyncClient(...).post(BASE_URL, ...)` | **1** | 唯一的對外付費請求（HTTP 200） |
| `Stop-Process` | **0** | 未使用 |
| `taskkill` | **0** | 未使用 |
| `pkill` / `kill` / `wmic process delete` / Job 終止 | **0** | 未使用 |
| 啟動伺服器（`run_server`／`uvicorn`／`Start-Process` server） | **0** | 未使用 |
| 綁定 `:8000` / `:8765` / `:8766` / `:8767` | **0** | 未綁定 |
| 對 `:8000` / `:8765` / `:8766` / `:8767` 發請求 | **0** | 未發送 |
| 寫入 `data/**` | **0** | 僅唯讀（實得 0 檔） |
| 寫入 SAGE | **0** | `fact_writer` 為假物件 |
| **付費請求總數** | **1** | 硬計數器擋在第 2 次之前 |

**唯一執行的網路命令逐字**（腳本內）：
```python
async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT_SECONDS) as client:
    resp = await client.post(
        BASE_URL,
        headers={"Authorization": f"Bearer {API_KEY}"},
        json=body,
    )
```
（`BASE_URL = https://ollama.com/v1/chat/completions`）

### 7.4 環境前後對照（客觀痕跡）

| 項目 | 跑前 | 跑後 | 判定 |
|---|---|---|---|
| `:8000` socket owner PID | **16568** | **16568** | **不變** ✓ |
| `python.exe` 清單 | 16568, 25352 | 16568, 25352 | **不變** ✓ |
| `pythonw.exe` 清單 | 5976, 16308, 21992, 23560, 24952, 26592 | 同左（6 隻） | **不變** ✓ |
| `data/**` 檔案數 | 6 209 | 6 209 | **不變**（無新增／無刪除）✓ |

**`data/**` mtime 差異（僅 2 筆，皆為生產服務正常寫入）：**

| 檔案 | 跑前 mtime (UTC) | 跑後 mtime (UTC) | delta | 判定 |
|---|---|---|---|---|
| `data/heartbeat_trace.log` | 19:43:57 | 19:46:57 | **180 s 整** | 生產 heartbeat 週期寫入 |
| `data/heartbeats/telegram_channel.json` | 19:44:25 | 19:47:25 | **180 s 整** | 生產 heartbeat 週期寫入 |

**如何區分「生產服務正常寫入」與「腳本寫入」：**
1. **檔名**：變動的兩檔都是生產 heartbeat 產物；腳本**沒有任何寫入 `data/**` 的程式碼路徑**
   （腳本對 `data/` 只做 `read_text`）。
2. **週期性**：兩檔 delta 皆為**正好 180 秒**，且與彼此維持 28 秒的固定相位差
   ⇒ 是**定時器**行為，不是一次性事件的副作用。
3. **檔案數不變**：6 209 → 6 209，**無任何新增檔案**；若腳本寫過 SAGE 或線頭，必然出現新檔。
4. **時間窗**：該次付費請求於 **19:47:12 UTC** 完成，落在兩筆週期寫入之間；
   若腳本有寫入，mtime 應落在請求完成的同一秒，而非 180 秒節拍上。
5. **`life_threads.jsonl`／SAGE 檔**：前者**不存在**，後者未被觸碰。

### 7.5 給「要不要開啟自動沉澱」的三點決策輸入

1. **真正的成本驅動是 completion，不是 prompt**：實測 1911 tokens 中 **1790（93.7%）是
   completion**，且 `max_completion_tokens=300` **無效**。要壓成本必須先解決
   **推理 token 不受上限約束**這件事，調 prompt 幾乎無效。
2. **生產預設逾時 20 s 會白花錢**：實測 26.34 s > 20 s ⇒ 上線前**必須**先處理逾時與
   「已送出但被 abort」的計費語意。
3. **滿載量級**：全域上限 200 次／日 ⇒ **≈ 38.2 萬 tokens／日**（≈ 87.8 分鐘／日串行延遲）。
   是否可接受，**取決於 Ollama Cloud 的實際單價**（本檔不含單價）。

---

# A2 推理抑制探針

**票號**：`LIFE-THREAD-M2-EXEC-REASONING-PROBE-1`
**日期（UTC）**：2026-09-17
**基線**：`HEAD == origin/main == 19f8e62`
（`LIFE-THREAD-M2-EXEC-HARDEN-1` 已落地，取代本檔第 1 節的 `b7cdcf3`）
**目的**：測定能否**關閉推理 token**（第 3.3 節實測：單次 1 790 completion tokens 中約 1 690 為計費推理）。

> ⚠️ 本節為**附加**內容。第 1～7 節的既有校準數據（`b7cdcf3` 基線）**逐字保留、未刪改**。
> 兩節基線不同，數字不可直接混用；A2 的對照基準一律是**第 3 節的 `b7cdcf3` 實測值**。

## A2.1 付費請求數（硬計數器）

| 項目 | 值 |
|---|---|
| Owner 授權 | **最多 3 次**真實 LLM 呼叫 |
| **實際發出的付費請求數** | **1**（`PAID_REQUEST_COUNT = 1`） |
| 未使用的授權額度 | **2**（主動保留，見 A2.6「提前停止理由」） |
| 重試次數 | **0**（無 retry 迴圈、無 `max_retries`、任何錯誤一律不重試） |
| 結果 | **HTTP 200，PASS** |

### A2.1.1 硬計數器輸出（逐字）

```
[HARD-COUNTER] claimed paid request slot 1/3 for probe=none
...
VERDICT(none) = PASS
PAID_REQUEST_COUNT = 1 (limit 3)
```

計數器檔案 `%TEMP%\m2exec_probe\paid_counter.json`（逐字）：

```json
{
  "paid_request_count": 1,
  "limit": 3,
  "probes": [
    { "slot": 1, "probe": "none" }
  ],
  "note": "reset after aborted attempt that never reached client.post (OLLAMA_API_KEY missing)"
}
```

`claim_paid_request_slot()` 是通往 `client.post(...)` 的**唯一**路徑，在**任何** HTTP POST
**之前**被呼叫；當 `paid_request_count >= 3` 時直接 `raise HardStopPaidRequestLimit`
（`SystemExit` 子類）⇒ 第 4 次在**送出前**即被硬擋。

**計數器歸零申報（誠實）**：`paid_request_count` 曾被歸零一次。原因是第一次啟動時
`OLLAMA_API_KEY` 未注入行程環境，腳本在 `claim_paid_request_slot()` **領取槽位後、
`httpx` POST 之前**即 `FATAL: OLLAMA_API_KEY not set` 中止。該次**未建立任何 HTTP 連線、
未送出任何位元組、未計費**。歸零後 `probes` 僅含真正送出的 1 筆。此為**首筆計數的更正**，
非「抹除一次已送出請求」。

## A2.2 步驟 0：repo 既有推理旋鈕盤點（免費、唯讀）

### A2.2.1 生產程式碼**已有** `thinking` 旋鈕（但只給 Anthropic 風格）

| 位置 | 內容 | 對本題的意義 |
|---|---|---|
| `src/llm/proxy.py:1822` | `ClaudeBackend.complete(..., thinking: Optional[Dict] = None, ...)` | `thinking` 是**簽名參數** |
| `src/llm/proxy.py:1851-1852` | `if thinking: request_body["thinking"] = thinking` | Claude 風格**會**放進 body |
| `src/llm/proxy.py:1614` | **`# kwargs 剩下的 (e.g. thinking) OpenAI 不支援, ignore`** | 🔴 OpenAI 風格**直接丟棄** |
| `src/llm/proxy.py:1569-1571` | `其他 kwargs (e.g. thinking) OpenAI 不支援,直接忽略` | 同上，註解逐字 |
| `src/llm/proxy.py:1616-1621` | `json_body` **只**由 `model`／`messages`／`max_completion_tokens`／`temperature` 構成 | 🔴 body 白名單是**硬編碼** |
| `src/llm/proxy.py:3833` | `治根 … 留待之後 Bry 拍板:加 reasoning_effort 關 thinking 或 max_tokens 提到 4000` | repo 內**已被指名但從未實作**的旋鈕 |
| `configs/loader.py:107-108` | `… 是為了 disable thinking …` | 歷史上確曾為「關 thinking」換過 endpoint |

**結論（步驟 0 的關鍵發現）**：repo 內**存在** `thinking` 旋鈕，但它是 **ClaudeBackend（Anthropic 風格）專用**；
本票實際走的 provider 是 `ollama`，由 `configs/loader.py:142-158` 建構 **`OpenAIBackend`**，
而 `OpenAIBackend` 的 body 白名單**不含** `thinking`，也**不含** `reasoning_effort`。
⇒ **不存在**可用的「生產既有作法」；必須用 `reasoning_effort` 這條 OpenAI 標準路徑，
且**必須確認它能否穿過生產通道**（見 A2.5）。

### A2.2.1b 歷史前例（untracked 檔案，支援候選 1 的排序）

`scripts/manual_disable_thinking.py`（**untracked**，故不在 `src/**` 的 grep 範圍內，僅在
`--untracked-files=all` 可見）是**同一問題的前例**：它對 **minimax-M2.7** 試了 5 種關 thinking 方法，
其中**方法 3 逐字就是** `{"reasoning_effort": "low"}`（`:41-42`，註解標為「OpenAI o1 風格」）。
其餘 4 種為 `enable_thinking: false`（Qwen 風格）、`thinking: {type: disabled}`（Anthropic 風格）、
以及換 model 名（`minimax-M2.7-no-think` / `-nothink`）。

**歷史結果（逐字引用 `src/llm/translate.py:35-36`）**：

```
# Bry 拍板 2026-07-20 20:12: 換 M3 via anthropic endpoint (M3 在 OpenAI 端點強制定 thinking,
# 3 種 disable 參數都失敗, anthropic endpoint thinking 預設 disabled)
```

**這段前例對本票的兩個意義**：

1. ✅ **支持候選排序**：`reasoning_effort` 確實是團隊曾認定要走的路（與 `proxy.py:3833` 的
   `加 reasoning_effort 關 thinking` 互相印證），故列為順位 1。
2. ⚠️ **但前例的結論是「OpenAI 端點的 disable 參數無效」**——**與本票結果相反**。
   差異在**不同 provider**：前例打的是 **minimax 的 OpenAI 端點**（該端點對 M2.7 強制 thinking），
   本票打的是 **Ollama Cloud 的 OpenAI-compat 端點**（`deepseek-v4.1-flash`），
   而 Ollama 實測**接受並生效** `reasoning_effort: "none"`。
   ⇒ **結論不可跨 provider 類推**（記入 U-3 的具體理由）。

### A2.2.2 端點／模型支援哪個參數

- `configs/default.yaml:100-112`：`provider: ollama`、`model: deepseek-v4.1-flash`。
- `configs/loader.py:157`：`base_url = https://ollama.com/v1/chat/completions`（**OpenAI-compat**）。
- `configs/loader.py:146` 註解記錄端點實測：`GET /api/tags` 200、`POST /v1/chat/completions` 200。
- 免費 web 搜尋**未能**取得可信的 Ollama Cloud 參數支援文件（引擎回傳大量無關結果，
  且 `free-search` 對本查詢無有效命中）⇒ **不以文件推論，改由實測判定**（本票即為實測）。

### A2.2.3 候選排序理由（最多 3 個，此為實際探查順序）

| 順位 | 候選 | 排序理由 |
|---|---|---|
| **1** | `"reasoning_effort": "none"` | ① `reasoning_effort` 是 **OpenAI 標準**欄位，本端點是 `/v1/chat/completions` **OpenAI-compat** ⇒ 最可能被辨識；② repo 內 `proxy.py:3833` **已指名**這個欄位名作為治根方案 ⇒ 前人已判斷它是本 provider 家族的正解；③ 若 provider 走 OpenAI 語意，`"none"` 是**語意最強**的關閉值（預設值通常是 `medium`）。 |
| **2** | `"think": false` | Ollama **原生** API 的關閉開關（`/api/chat` 用 `think`）。但本端點是 **`/v1/` OpenAI-compat 外殼**，`think` **不是** OpenAI 欄位 ⇒ 最可能被**忽略**（靜默無效）或 **400**。列第 2 是因為「若 shim 有轉譯 `think`」就可能一次命中。 |
| **3** | `"reasoning_effort": "minimal"` | 與順位 1 **同欄位**、僅**值不同**。語意上 `minimal` 是「**降到最低**」而非「**關閉**」，推理 token **未必歸零**；且 Ollama 對 `reasoning_effort` 的**值域映射未知**（可能只認 `low/medium/high`，`none`/`minimal` 皆被拒或皆被當未知值忽略）⇒ 把握最低。 |

## A2.3 實測結果

### A2.3.1 receipt（`%TEMP%\m2exec_probe\receipt_none.json`，redacted，逐字）

```json
{
  "model": "deepseek-v4.1-flash",
  "base_url": "https://ollama.com/v1/chat/completions",
  "api_key_env_var": "OLLAMA_API_KEY",
  "api_key": "<redacted:OLLAMA_API_KEY:len=57>",
  "request_body_redacted": {
    "model": "deepseek-v4.1-flash",
    "messages": [
      { "role": "user", "content": "<prompt:177 chars>" }
    ],
    "max_completion_tokens": 300,
    "temperature": 0.85,
    "reasoning_effort": "none"
  },
  "param_under_test": { "reasoning_effort": "none" },
  "request_sent": true,
  "retries": 0,
  "http_status": 200,
  "latency_ms": 1177.1,
  "finish_reason": "stop",
  "usage_raw": {
    "prompt_tokens": 95,
    "prompt_tokens_details": { "cached_tokens": 0 },
    "completion_tokens": 75,
    "total_tokens": 170
  },
  "prompt_tokens": 95,
  "completion_tokens": 75,
  "total_tokens": 170,
  "reasoning_tokens": null,
  "response_chars": 130,
  "response_text": "{\"dissolution\": \"我以為自己在救一盆薄荷，其實是在練習接受有些枯萎不是我能逆轉的。第七天，我剪下還活著的那一小段，插進水裡，看著它慢慢長出細根——原來放手不是放棄，是換一種方式陪它活下去。\", \"meaning_kind\": \"relief\"}",
  "transport_error": null,
  "error_status": null,
  "error_body_redacted": null,
  "is_json": true,
  "has_dissolution": true,
  "dissolution_chars": 85,
  "meaning_kind": "relief",
  "meaning_kind_legal": true,
  "parse_error": null,
  "paid_request_slot": 1,
  "probe_key": "none",
  "candidate_label": "reasoning_effort=\"none\"",
  "prompt_provenance": "evaluate_thread_dissolution",
  "prompt_chars": 177,
  "verdict": "PASS"
}
```

### A2.3.2 可比性保證（prompt 與 body 形狀與基準一致）

| 項目 | 基準（第 3 節，`b7cdcf3`） | A2 探針（`19f8e62`） | 是否可比 |
|---|---|---|---|
| prompt 產生方式 | M2 真實 `_build_reflection_prompt` | **同一入口** `evaluate_thread_dissolution`（`life_thread_dissolution.py:366`）→ `_build_reflection_prompt`（`:265`） | ✓ |
| **prompt 字元數** | **177** | **177（逐字元相同，已比對）** | ✓ |
| `model` | `deepseek-v4.1-flash` | 同 | ✓ |
| `messages` | 單一 user 訊息 | 同（`content` 為同一段 177 字元 prompt） | ✓ |
| `max_completion_tokens` | 300 | 300 | ✓ |
| `temperature` | 0.85 | 0.85 | ✓ |
| `response_format` | 未帶（與 `generate_text` 一致） | 未帶 | ✓ |
| **唯一差異** | — | **多了 `reasoning_effort`** | — |

> 探針腳本**不重測基準**（票面明令），基準值一律引用第 3 節既有實測。

### A2.3.3 判定表

| 候選 | 送出 | `completion_tokens` | vs 基準 | `total_tokens` | `latency_ms` | `finish_reason` | 合法 JSON | `meaning_kind` | **判定** |
|---|---|---|---|---|---|---|---|---|---|
| **基準（未重測）** | 1 | **1 790** | — | 1 911 | 26 335.6 | `stop` | ✓ | `loss` | — |
| **`reasoning_effort: "none"`** | **1** | **75** | **−95.8%**（基準的 **4.2%**） | **170** | **1 177.1** | `stop` | ✓ | **`relief`** ✓ 合法 | ✅ **PASS** |
| `think: false` | **0** | — | — | — | — | — | — | — | ⏸ **未測（主動保留額度）** |
| `reasoning_effort: "minimal"` | **0** | — | — | — | — | — | — | — | ⏸ **未測（主動保留額度）** |

**PASS 判準逐項核對**（票面：`completion_tokens` ≤ 基準 1/3 **且** 格式完好）：

| 判準 | 門檻 | 實測 | 通過 |
|---|---|---|---|
| `completion_tokens` 顯著下降 | ≤ 1 790 / 3 ≈ **597** | **75** | ✅（遠優於門檻） |
| 回應為合法 JSON | 是 | `is_json=true` | ✅ |
| 含合法 `dissolution` | 非空 | 85 字元 | ✅ |
| `meaning_kind` 為合法值 | ∈ 6 值枚舉 | `relief` ∈ 枚舉 | ✅ |
| HTTP 狀態 | 200 | 200 | ✅ |

**未測候選不判定 PASS／FAIL**（0 次呼叫）——本節不臆測其結果。

### A2.3.4 兩項附帶觀測（誠實申報）

1. **`finish_reason=stop`，非 `length`**：`max_completion_tokens=300` 在此次**未被觸及**
   （僅用 75）。搭配第 3.3 節的既有發現（基準時 300 上限被 1 790 突破），
   再次印證該參數在本端點**不是**可靠的成本上界。
2. **`prompt_tokens` 由 121 變 95（同一段 177 字元 prompt）**：兩次量測的 prompt **逐字元相同**，
   但 provider 回報的 `prompt_tokens` 不同（121 → 95）。可能原因：tokenizer 版本／端點路由差異，
   或基準那次的 `usage` 含未列出的欄位。**此差異無法由本票 n=2 判定** ⇒ 列為不確定點（A2.7）。
   對結論**無影響**：比較主軸是 `completion_tokens`，且 `reasoning_tokens` 欄位在本端點**不回報**
   （基準與本次皆為 `null`／缺欄）⇒ 「推理是否真的關閉」是以 **`completion_tokens` 量級**
   與**延遲**為證，而非以 provider 的推理欄位為證。

### A2.3.5 「參數真的生效」vs「被靜默忽略」的判定依據

本次**無法**用「送一個已知無效值看是否同結果」的對照來證明參數生效（會多花 1 次付費呼叫）。
因此「生效」的判定建立在**三重收斂證據**上：

| 證據 | 基準 | 本次 | 若參數被「靜默忽略」的預期 |
|---|---|---|---|
| `completion_tokens` | 1 790 | **75** | 應仍在 1 790 同級（隨機性不會降到 4%） |
| `latency_ms` | 26 335.6 | **1 177.1** | 應仍在 26 s 同級 |
| `total_tokens` | 1 911 | **170** | 應仍在 1 911 同級 |

三項**同時**下降一個數量級 ⇒ **參數被忽略的假設與觀測不相容**。
**殘留不確定**：無法排除「端點側同時發生了與本參數無關的推理預設變更」；
此風險以「2 個未使用的付費額度」保留給後續驗證（A2.7）。

## A2.4 新的每日成本含意表（與基準並列）

換算基準：`MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY = 3`、
`MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY = 200`
（`src/soul/life_thread_dissolution_exec.py:119,122`，**`19f8e62` 生效值**）。

> ⚠️ **與第 5 節的差異申報**：第 5.1 節用的是 `24／200`（`b7cdcf3` 當時的值）。
> `LIFE-THREAD-M2-EXEC-HARDEN-1`（`19f8e62`）已把單 agent 上限由 **24 改為 3**
> （對齊契約 §3.6 的 `DISSOLVE_MAX_PER_DAY = 3`）。**第 5 節數字未刪改，仍為當日實測之忠實紀錄**；
> 下表為 `19f8e62` 的現行值。

### A2.4.1 兩道的**獨立**上界（各自跑滿）

| 情境 | 上限 | 呼叫數／日 | prompt tokens／日 | completion tokens／日 | **合計 tokens／日** | 串行延遲／日 |
|---|---|---|---|---|---|---|
| 單 agent 滿載 | `3／agent／日` | 3 | 285 | 225 | **510** | ≈ 3.5 s |
| 全域滿載 | `200／日` | 200 | 19 000 | 15 000 | **34 000** | ≈ 235.4 s ≈ **3.9 分鐘** |

### A2.4.2 基準 vs 最佳候選（並列對照）

| 情境 | 基準完成 tokens／日 | **A2 候選完成 tokens／日** | 降幅 | 基準總 tokens／日 | **A2 總 tokens／日** | 降幅 |
|---|---|---|---|---|---|---|
| 單 agent 滿載（3 次） | 5 370 | **225** | **−95.8%** | 5 733 | **510** | **−91.1%** |
| 全域滿載（200 次） | 358 000 | **15 000** | **−95.8%** | 382 200 | **34 000** | **−91.1%** |

| 延遲情境 | 基準 | **A2 候選** | 改善 |
|---|---|---|---|
| 單次 | 26 335.6 ms | **1 177.1 ms** | **快 22.4 倍** |
| 單 agent 滿載（3 次） | ≈ 79.0 s | **≈ 3.5 s** | 快 22.4 倍 |
| 全域滿載（200 次） | ≈ 87.8 分鐘 | **≈ 3.9 分鐘** | 快 22.4 倍 |

### A2.4.3 兩道上限誰先咬住（治理要點，`19f8e62` 版）

- 單 agent 3／日，全域 200／日 ⇒ **全域 200 需要 ≥ 67 個 agent 才可能被咬住**
  （`200 ÷ 3 ≈ 66.7`）。
- 本機 `configs/default.yaml` 啟用 **10 個 agent**（第 5.2 節）⇒ 全部 agent 跑滿也只有
  **30 次／日**（≤ 200）⇒ **單 agent 的 3／日 才是實質上界**，全域 200／日 只是理論天花板。
- ⇒ **實際日成本上界 = 510 tokens／日（3 次 × 170）**，而非 34 000。
  亦即 HARDEN-1 把單 agent 上限由 24 降到 3 之後，**成本已被兩層各自壓低**：
  ① 呼叫數 ÷ 8；② 單次 tokens ÷ 11.2。
- 若未來 agent 數 ≥ 67，則全域 200 先咬住，日成本上界回到 **34 000 tokens／日**。

**單價**：仍**待主大腦／Owner 提供**（沿用第 5.5 節結論：`configs/**`、`docs/**` 內不存在
Ollama Cloud 單價記載，本檔不做價格臆測）。本節只提供 token 與延遲量。

## A2.5 結論與對「未來接線票」的可執行建議

### A2.5.1 結論：**可以關推理——但生產通道目前會把它剝掉**

1. **技術上可行**：`"reasoning_effort": "none"` 在本端點**確定有效**
   （completion 1 790 → **75**，延遲 26.3 s → **1.18 s**，格式完好，**PASS**）。
2. 🔴 **但現在接線「不會有任何省錢效果」**：生產通道**不會**轉發這個參數。
   即使把它加進 `OpenAIBackend.complete` 的簽名，也仍到不了端點——**共 3 處**需要打通：

| # | 位置 | 現況 | 需要的改動 |
|---|---|---|---|
| **W-1** | `configs/loader.py:142-158` | `ollama` ⇒ `OpenAIBackend` | **不需改**（通道本身是對的） |
| **W-2** | `src/llm/proxy.py:1554-1562`（`complete` 簽名） | 有 `**kwargs`，但 `reasoning_effort` 未具名 | **需具名接收**，否則被 `kwargs` 吸收後丟棄 |
| **W-3** | `src/llm/proxy.py:1616-1621`（`json_body` 白名單） | **硬編碼** `model`／`messages`／`max_completion_tokens`／`temperature` | **需加** `json_body["reasoning_effort"] = reasoning_effort`（僅非 `None` 時） |
| **W-4** | `src/llm/proxy.py:4355-4360`（`generate_text` → `backend.complete`） | 🔴 **呼叫點把參數寫死**：只傳 `messages`/`model`/`max_tokens`/`temperature`/`thinking=None` | **需加** 傳遞 `reasoning_effort`；**且** `generate_text` 自己的簽名（`:4323-4329`）也要收這個參數 |

3. **一定要提醒接線票的兩件事**（否則「關了推理卻沒省到／反而更貴」）：
   - **重試放大**：`generate_text` 自帶 `for attempt in range(2)`（`proxy.py:4353`），
     `OpenAIBackend.complete` 另有 `max_retries=3` 預設（`proxy.py:1613`）⇒ 最壞 **8 次** HTTP。
     執行層「恰好 1 次呼叫」的保證**只到 `llm_call` 介面為止**，**不包括** adapter 內部的重試。
   - **4xx 會寫 `data/**`**：`proxy.py:1687-1706` 會把 4xx body dump 到
     `data/logs/llm_4xx_response.log`。若接線票要求「執行層不得寫 `data/**`」，
     走 `generate_text` 這條路會**違反**該限制 ⇒ 需要另做處理（不在本票範圍）。

### A2.5.2 三個可選路徑（給 Owner／主大腦裁決）

| 路徑 | 內容 | 成本 | 風險 |
|---|---|---|---|
| **P-1（建議）** | 接線票**一併**打通 W-2／W-3／W-4 三處，並在**同一票**內驗證 `reasoning_effort="none"` 確實出現在 pre-request log 的 `request body keys`（`proxy.py:1659,1732` 已印 keys，是現成的驗收錨點） | 0 額外付費（走既有 log 驗證） | 需動 `src/llm/proxy.py`（**本票禁改**） |
| **P-2** | 接線票在 adapter 層**直呼** endpoint（繞過 `generate_text`），自帶 `reasoning_effort` 且**不重試**、**不寫 `data/**`** | 需新程式碼 | 與校準票 D-2 相同的偏離（繞過生產通道）⇒ 生產行為仍未驗證 |
| **P-3** | 先**不動**通道，接受現況成本 | 0 | 全域 200／日滿載 ≈ 382 200 tokens／日（基準）；但單 agent 3／日 ⇒ 實際 ≈ 5 733 tokens／日（10 agent 全滿） |

**建議採 P-1**：本票已證明參數有效，剩下的是**純粹的通道轉發工程**，
且 `proxy.py:1659/1732` 既有的 `request body keys=` log 讓驗收**不需要再花錢**。

### A2.5.3 本票**不建議**再燒剩餘 2 個付費額度的理由

- 票面規則 2「一次就有效就停」：候選 1 已 **PASS** 且大幅優於門檻（75 vs 門檻 597）。
- 剩餘 2 槽若用來測 `think=false`／`minimal`，**對決策沒有增量價值**：
  `reasoning_effort` 已是 OpenAI 標準欄位、已是接線建議路徑；`think` 若有效也只是**同一效果的替代寫法**。
- ⇒ **保留 2 槽**給「接線後在**真實生產通道**上驗證轉發是否成功」這種**更有價值**的用途。

## A2.6 附錄：探針腳本與紅線自檢

### A2.6.1 腳本位置（全部在 `%TEMP%`，repo 內只改本檔）

| 檔案 | 用途 |
|---|---|
| `%TEMP%\m2exec_probe\m2exec_probe.py` | 探針主體（硬計數器、無重試） |
| `%TEMP%\m2exec_probe\run_probe.ps1` | 只把 `OLLAMA_API_KEY` 由 `.env` 注入子行程（不印值） |
| `%TEMP%\m2exec_probe\paid_counter.json` | 硬計數器狀態 |
| `%TEMP%\m2exec_probe\receipt_none.json` | 本次 receipt |

### A2.6.2 紅線自檢（逐字列出所有與網路／行程有關的命令）

| 命令／動作 | 次數 | 說明 |
|---|---|---|
| `httpx.AsyncClient(...).post(BASE_URL, ...)` | **1** | 唯一的對外付費請求（HTTP 200） |
| `Stop-Process` | **0** | 未使用 |
| `taskkill` | **0** | 未使用 |
| `pkill` / `kill` / `wmic process delete` / Job 終止 | **0** | 未使用 |
| 啟動伺服器（`run_server`／`uvicorn`／`Start-Process`） | **0** | 未使用 |
| 綁定 `:8000` / `:8765` / `:8766` / `:8767` | **0** | 未綁定 |
| 對 `:8000` / `:8765` / `:8766` / `:8767` 發請求 | **0** | 未發送 |
| 寫入 `data/**` | **0** | 全程唯讀 |
| 寫入 SAGE | **0** | 探針**不含** `fact_writer`，不觸碰 `src/memory/sage/**` |
| repo 內修改檔案 | **1** | 僅 `docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md`（本節） |
| **付費請求總數** | **1** | 硬計數器擋在第 4 次之前 |

**唯一執行的網路命令逐字**（腳本內）：

```python
async with httpx.AsyncClient(timeout=CLIENT_TIMEOUT_SECONDS) as client:
    resp = await client.post(
        BASE_URL,
        headers={"Authorization": "Bearer %s" % api_key},
        json=body,
    )
```
（`BASE_URL = https://ollama.com/v1/chat/completions`；`CLIENT_TIMEOUT_SECONDS = 120.0`，
對齊 `19f8e62` 的 `CONSOLIDATION_TIMEOUT_SECONDS = 120`）

**金鑰**：全程 redact（receipt 記為 `<redacted:OLLAMA_API_KEY:len=57>`）；
`run_probe.ps1` 僅印長度、**不印值**；未寫入任何 commit 檔案。

## A2.7 不確定點與偏離（誠實申報）

### A2.7.1 不確定點

| # | 不確定點 | 說明 |
|---|---|---|
| U-1 | **n 極小（每個候選 1 次）** | 無法給出變異數／p90。75 tokens 與 1 177 ms 都是**單一觀測點**。 |
| U-2 | **無法排除「靜默忽略」的殘餘可能** | 未做「無效值對照」（會多花付費額度）。以三重收斂證據（A2.3.5）**推定**生效，非嚴格證明。 |
| U-3 | **同模型通則性未知** | 只測 `deepseek-v4.1-flash`。其他 model 是否同樣接受 `reasoning_effort="none"` **未知**。 |
| U-4 | **`think=false` 與 `"minimal"` 完全未測** | 主動保留額度，**不臆測**其結果。 |
| U-5 | **`prompt_tokens` 121 → 95 的差異未解** | 同一段 177 字元 prompt，provider 回報不同。不影響結論（主軸為 completion）。 |
| U-6 | **格式風險** | `meaning_kind` 由 `loss` 變 `relief`（**兩者皆合法**）。n=1 無法判定是否影響語意品質分布；`dissolution` 85 字元（基準 58）皆在契約 ≤400 字元內。 |
| U-7 | **端點側預設值未知** | 不確定 `reasoning_effort` 的 provider 預設值與完整值域；`"none"` 的有效性已實測，但**為何有效**（真關閉 vs 降到極低）無法從 `usage` 區分（本端點不回報 `reasoning_tokens`）。 |
| U-8 | **未在真實生產通道驗證** | 本票直呼 endpoint，**未**經 `LLMProxy.generate_text`／`OpenAIBackend.complete`。通道轉發問題（A2.5.1）為**靜態程式碼分析**結論，未經執行驗證。 |

### A2.7.2 偏離

| # | 偏離 | 理由 | 風險 |
|---|---|---|---|
| A-D-1 | **未重測基準** | 票面明令（省錢紀律），沿用第 3 節 `b7cdcf3` 實測值 | 兩次量測相隔一個 commit；prompt 與 body 已逐欄比對一致，且 provider 未換 model |
| A-D-2 | **硬計數器歸零 1 次** | 首次啟動時 `OLLAMA_API_KEY` 未注入，在 POST **之前**即中止 ⇒ 未送出、未計費 | 已於 A2.1.1 完整申報；歸零後 `probes` 僅含真正送出的 1 筆 |
| A-D-3 | **直呼 endpoint，繞過生產通道** | 生產通道自帶雙層重試（最壞 8 次）＋ 4xx 會寫 `data/logs/**` ⇒ 同時違反「不重試」與「不寫 `data/**`」兩條紅線（沿用校準票 D-2 的理由） | 同上，已記為 U-8 |
| A-D-4 | **client timeout 設 120 s** | 對齊 `19f8e62` 的 `CONSOLIDATION_TIMEOUT_SECONDS = 120`（非 monkeypatch，該常數已由 HARDEN-1 落庫） | 本次實測僅 1.18 s，逾時未觸發 |
| A-D-5 | **基線 commit 由 `b7cdcf3` 變為 `19f8e62`** | 開票時 `HEAD == origin/main == b7cdcf3`，但 `LIFE-THREAD-M2-EXEC-HARDEN-1` 在本票執行**期間**落地（`19f8e62`，2026-09-17 15:54:52 −0400）⇒ 依票面「若 HARDEN-1 已落地則以最新 HEAD 為準」，改用 `19f8e62` | 成本常數由 `24／200` 變 `3／200`，故 A2.4 另列現行值；A2 的**對照基準數字**仍為第 3 節（同 model、同 prompt、同 body） |
