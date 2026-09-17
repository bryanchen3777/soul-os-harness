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
