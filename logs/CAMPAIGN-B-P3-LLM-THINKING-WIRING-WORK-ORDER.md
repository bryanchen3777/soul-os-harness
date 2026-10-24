# 工單：CAMPAIGN-B-P3-LLM-THINKING-WIRING（選項甲）

> **開立**：主大腦（2026-10-23 20:0x EDT）
> **依據裁定**：Owner (Bry) 2026-10-23 19:2x 三步序列之**第三步**：「**完成前兩步解障後，立刻接續選項甲（Phase 3 接線）：依照 proxy.py 規範在沙盒內接入真實 LLM 思考提詞**」。
> **前置條件（硬性）**：① 步驟一 `INCIDENT-GOAL-DB-LOCKED` 已解障（否則 LLM 呼叫只會加劇 DB 鎖爭奪 —— Owner 原話：「如果資料庫被鎖死、讀取端又會默默吃掉未消解張力，此時把 Phase 3 的 LLM 接上去只會加劇鎖爭奪與狀態損壞」）；② 步驟二 `BUG-EPISTEMIC-PARTIALLY-RESOLVED` 已合入（讀取端必須先能誠實處理 `partially_resolved`）。
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**；唯二**需 Owner 裁示的參數**已列在 §4（皆為花錢面）。

---

## 0. 本票要做什麼（一句話）

把 Phase 2 的「決定性 Mock 思考器」換成**真的 LLM 思考器**：依 `src/llm/proxy.py` 的既有規範呼叫 LLM，把回應解析成 `ThinkOutcome`（**含護欄：LLM 不得聲稱消解了不存在的缺口**），**全流程仍在沙盒**、不接生產、不部署、不重啟。

**關鍵設計取捨（主大腦拍板）**：`LLMBackend` 是 **ABC**（`proxy.py:1926`），`OpenAIBackend`（:1941）／`ClaudeBackend`（:2222）為其實作 ⇒ **守衛測試可注入 script backend** ⇒ **接線面能零成本、可重現地驗證**；真實 LLM 呼叫則是**另需 Owner 核准成本的一次性驗收步驟**（§4）。

---

## 1. 範圍

### 可動（repo 內共 3 檔）

| # | 檔案 | 動什麼 |
|---|---|---|
| 1 | `src/inner_life/tension_thinking.py` | 新增 `LLMThinker` ＋ `build_llm_thinking_prompt()` ＋ `parse_llm_outcome()`（**既有 `build_thinking_prompt()` 與 `DeterministicMockThinker` 一個字都不動** ⇒ Phase 2 的守衛 P2-T1～T16 不受影響） |
| 2 | `tests/inner_life/test_tension_closure_loop.py` | 新增 P3 階段的守衛測試（**擴充既有檔**，不得新建 `tests/**` 下的 collectable `.py`） |
| 3 | `logs/ENGINEERING_STATE.md` | **追加** §0.1.56（本票落地與證據） |

### 🔴 動不得
- `src/llm/proxy.py`（**既有規範**；本票是它的**消費者**，不是改造它）。
- `src/inner_life/tension_closure_loop.py` 的既有行為（`drive_loop` 簽章、`DeterministicMockThinker` 預設、Phase 1 的 7 欄、Phase 2 的 6 欄）—— 本票**只新增**能力，**不**改既有路徑。
- `src/inner_life/tension_scoring.py`（**衰減門檻 24h／7d 凍結**）。
- `src/social/*`、`src/agency/*`、`src/memory/sage/writer.py` 等 `frozen_dirs` 11 筆。
- `data/**`、任何排程器、任何 handler。

---

## 2. 做法（決策已定）

### 2.1 提示詞：新增 `build_llm_thinking_prompt(req) -> str`
- **不得**改動既有 `build_thinking_prompt()`（Phase 2 守衛會盯它）。
- 新函式 ＝ **既有五段模板（ROLE／CONTEXT／FACTORS／GAPS／ASK）＋ 第六段 `OUTPUT`**。
- `OUTPUT` 段必須要求 LLM 回**單一 JSON 物件**（不得多餘文字），欄位固定：
  ```json
  {"resolved_gap_count": <int, 本次消解的缺口數>,
   "remaining_gap_count": <int, 思考後仍開著的缺口數>,
   "decided": <bool, 是否已想完>,
   "reason": <str, 短碼>}
  ```
- 🔴 **不得**在提示詞裡要求 LLM 產生任何新的「外部事實」；`OUTPUT` 段要明寫「只能從上列缺口逐項消解，不得臆造」。
- 決定性要求沿用 Phase 2：**同一輸入 ⇒ 逐位元相同的提示詞**（禁止 uuid／time／hash）。

### 2.2 解析：新增 `parse_llm_outcome(text: str, req: ThinkRequest) -> ThinkOutcome`
**fail-closed 優先**：任何不確定一律「不消解」。

1. 從回應文字中抽出 JSON：允許 ```` ```json ```` 包裹、允許前後有空白；**取第一個可解析的 JSON 物件**。
2. 解析失敗／找不到 JSON ⇒ 回 `ThinkOutcome(partial_resolution=0.0, remaining_gap_count=req.info_gap_count, decided=False, reason="llm_parse_failed")`。
3. 欄位型別校驗（任一不符 ⇒ 同上 fail-closed，`reason="llm_outcome_invalid"`）：
   - `resolved_gap_count`：**int**（排除 bool）且 **≥ 0**
   - `remaining_gap_count`：**int**（排除 bool）且 **≥ 0**
   - `decided`：**bool**
   - `reason`：**str**（可空字串）
4. 🔴 **幻覺護欄（本案最重要的一條）**：
   - `resolved_gap_count` **不得大於** `req.info_gap_count`（LLM 不得聲稱消解了不存在的缺口）⇒ 違反 ⇒ fail-closed（`reason="llm_outcome_invalid"`）。
   - `remaining_gap_count` **不得大於** `req.info_gap_count` ⇒ 同上。
   - `resolved_gap_count + remaining_gap_count` **不得大於** `req.info_gap_count` ⇒ 同上。
   - `decided is True` 時 **必須** `remaining_gap_count == 0` ⇒ 否則 fail-closed。
5. 通過 ⇒ 組 `ThinkOutcome`：
   - `partial_resolution` ＝ `resolved_gap_count / req.info_gap_count`（若 `info_gap_count == 0` ⇒ `1.0`）
   - `remaining_gap_count` ＝ 解析值
   - `decided` ＝ 解析值
   - `reason` ＝ 解析值（**空字串時填 `"llm_outcome"`**）
6. 🔴 **不得**讓 LLM 的自由文字直接進入 `reason` 之外的任何欄位；`reason` 長度上限 **64 字元**（超出截斷）。

### 2.3 `LLMThinker`
```python
class LLMThinker:
    """以真實 LLM 執行的思考器（**沙盒**；失敗一律 fail-closed）。"""
    def __init__(self, proxy, *, model: str, max_tokens: int = 400,
                 max_calls: int = 1, calls: Optional[dict] = None): ...
    def think(self, req: ThinkRequest, prompt: str) -> ThinkOutcome: ...
```
- **只能經由注入的 `proxy` 呼叫**（型別上接受任何有 `generate_text` 的物件）⇒ **測試注入 script backend**（`LLMBackend` 的假實作）⇒ **守衛測試零真實呼叫、零成本**。
- 🔴 **不得**自建 HTTP 客戶端、**不得**直接 `urllib`／`httpx`／`requests`、**不得**自帶 API key、**不得**繞過 `LLMProxy`（繞過會失去 proxy 的 retry／計費／審計／人格注入）。
- 呼叫面：`await proxy.generate_text(messages, agent_id=..., max_tokens=...)`。
  🔴 **注意**：`generate_text` 是 **async** ⇒ `Thinker.think()` 若是同步介面會無法直接 await ⇒ **本票的 `LLMThinker.think()` 必須是 async**，或提供 `think_async()` 並讓 `drive_loop` 走 async 路徑。**取捨由主大腦拍板（本票已拍板）**：
  - **採 `async def think(...)`**（與 `LLMProxy` 一致）⇒ `drive_loop` 必須**新增一個 async 變體**（`async def drive_loop_async(...)`），**既有同步 `drive_loop()` 語意一個字都不動**（Phase 2 守衛不動）⇒ 同步路徑在 Phase 3 仍用 Mock。
  - 🔴 `drive_loop_async()` 與既有 `drive_loop()` **不得**共用會造成重複寫入的邏輯 ⇒ 建議抽 `_run_loop(store_dir, outcome_provider)` 內部函式，兩者皆呼叫它；**既有 `drive_loop()` 的行為與輸出必須逐位元不變**。
- **成本護欄（執行期）**：
  - `max_calls` ＝ 每個 thinker 實例最多呼叫次數（預設 **1**）⇒ 超過 ⇒ **不再呼叫**，直接回 fail-closed 的 `ThinkOutcome(reason="llm_call_budget_exhausted")`。
  - 呼叫次數必須**可稽核**（實例上暴露 `calls` 計數字典；`ThinkOutcome.reason` 也要能看出是否因預算而未消解）。
  - 🔴 `max_calls` 與 `max_tokens` 是**花錢參數** ⇒ 預設值見 §4，**實際跑真實 LLM 前需 Owner 核准**。
- **失敗路徑**：`proxy` 拋例外 / 回 `None` / 回空字串 ⇒ 一律 fail-closed（`reason` 分別為 `"llm_call_failed"`／`"llm_call_failed"`／`"llm_parse_failed"`）⇒ **不得**讓例外冒泡到 agent 訊息路徑（呼應 `proxy.py:5213-5214` 的既有語意：「失敗不污染訊息路徑」）。
- messages 組法：`[{"role": "system", "content": <思考指令>}, {"role": "user", "content": build_llm_thinking_prompt(req)}]`；system 段不得要求 LLM 扮演對話角色（**它不是角色發言**，是一次內省推理）。

---

## 3. 守衛測試（新增，**擴充既有守衛檔**）

新增至少 **10 筆**，每筆具名斷言：

| 編號 | 測試 | 斷言 |
|---|---|---|
| P4-T1 | 提示詞含 `OUTPUT` 段 | `build_llm_thinking_prompt()` 必含六段標題且含四個 JSON 欄位名 |
| P4-T2 | 既有提示詞零變動 | `build_thinking_prompt()` 的輸出與 Phase 2 的基準**逐位元相同**（既有 P2-T1 仍綠） |
| P4-T3 | 解析成功 | 合法 JSON ⇒ `partial_resolution`／`remaining_gap_count`／`decided`／`reason` 正確且型別正確 |
| P4-T4 | 解析失敗 fail-closed | 非 JSON / 空字串 ⇒ `partial_resolution == 0.0`、`decided is False`、`reason == "llm_parse_failed"`、`remaining_gap_count == req.info_gap_count` |
| P4-T5 | **幻覺護欄**：`resolved_gap_count > info_gap_count` ⇒ fail-closed（`reason == "llm_outcome_invalid"`） |
| P4-T6 | **幻覺護欄**：`remaining_gap_count > info_gap_count` 或 `resolved+remaining > info_gap_count` ⇒ fail-closed |
| P4-T7 | `decided is True` 但 `remaining_gap_count != 0` ⇒ fail-closed |
| P4-T8 | 型別錯誤（bool 冒充 int、欄位缺漏）⇒ fail-closed |
| P4-T9 | **呼叫預算**：`max_calls` 用盡後**不再呼叫**（script backend 的呼叫計數**不得增加**）且回 fail-closed（`reason == "llm_call_budget_exhausted"`） |
| P4-T10 | **失敗不污染**：proxy 拋例外 ⇒ `think()` 回 fail-closed 的 `ThinkOutcome`，**不拋出** |
| P4-T11 | **接線面（script backend）**：以假 backend 回傳合法 JSON ⇒ 閉環產出 `partially_resolved` 事件 ＋ working_state 的 4 個 extras 正確（`partial_resolution`／`remaining_gap_count`／`decided`／`reason`） |
| P4-T12 | **零真實呼叫**：全部 P4 守衛測試執行期間，**不得**有任何真實網路呼叫 ⇒ 用「script backend 計數 ＝ 1」＋ AST 斷言（`tension_thinking.py` 不得 import `httpx`／`requests`／`urllib`／`socket`／`asyncio`？——**注意**：`asyncio` 會被 async 需要 ⇒ 允許 `asyncio`，但**不得** import `httpx`／`requests`／`urllib`／`socket`／`subprocess`） |

🔴 **既有 Phase 2 的 16 筆守衛不得刪除或弱化**（`build_thinking_prompt`／`DeterministicMockThinker` 一律不動 ⇒ 應全綠）。

---

## 4. 變異實驗（M1～M6，每條**必須轉紅**）

harness 必須放 `%TEMP%`、採「**改副本**、repo 檔全程不寫入」模式（2026-10-23 實測：in-place 還原會以 `OSError: [Errno 22]` 失敗並把 repo 檔留在變異狀態）；載入點覆寫**只接受 `%TEMP%` 底下路徑**；收尾必印「repo 檔 sha256 == 初始值」。

| # | 變異 | 應轉紅 |
|---|---|---|
| M1 | `parse_llm_outcome` 移除「`resolved_gap_count > info_gap_count`」檢查 | P4-T5 |
| M2 | 移除「`remaining_gap_count > info_gap_count` 與 `resolved+remaining` 合計」檢查 | P4-T6 |
| M3 | 解析失敗時**改回**回 `decided=True`（fail-open 復原） | P4-T4 |
| M4 | `LLMThinker` 忽略 `max_calls`（預算用盡仍呼叫） | P4-T9 |
| M5 | `LLMThinker` 讓 proxy 例外**冒泡**（不 fail-closed） | P4-T10 |
| M6 | `build_llm_thinking_prompt` 漏掉 `OUTPUT` 段 | P4-T1 |

---

## 5. 🔴 需 Owner 裁示的花錢參數（**未核准前不得跑真實 LLM 呼叫**）

| 參數 | 主大腦建議 | 說明 |
|---|---|---|
| 每 thinker 實例最大呼叫次數 `max_calls` | **1** | 沙盒單次驗收足矣；生產接線再議 |
| 每次呼叫 `max_tokens` | **400** | 思考提詞已含事實摘要，夠用且省 |
| model | **沿用 `src/llm/proxy.py` 既有的 agent model 設定**（**不得**另立模型） | 避免引入新的成本面 |
| 真實呼叫的重試次數 | **0**（失敗即 fail-closed） | 重試＝花錢，且失敗可安全 fail-closed |
| 真實 LLM 驗收範圍 | **1 個 agent × 1 次呼叫**（沙盒內） | 最小成本證明接線面通 |

⇒ **一次真實 LLM 呼叫的成本與「可不可以打」都由你拍板。** 在你核准前，worker 只做 **script backend** 的守衛測試與變異（**零成本**），並把真實呼叫驗收步驟標為「待核准」。

---

## 6. 邊界（硬性）

1. 🔴 **不得**跑真實 LLM 呼叫（未獲 §5 核准）；**不得**自建 HTTP 客戶端、**不得**自帶 API key、**不得**繞過 `LLMProxy`。
2. 🔴 **不得**接任何排程器／handler；**不得**寫 `data/**`；**不得**部署；**不得**重啟服務；**不得**殺任何行程。
3. 🔴 **不得**改 `src/llm/proxy.py`、`tension_scoring.py`、`tension_closure_loop.py` 的既有行為、任何 `frozen_dirs` 檔。
4. 🔴 **不得跑全庫回歸**（由主大腦在合入前執行；避免與生產搶資源、也避免與事故勘查並行）。
5. 🔴 **今晚 22:00 EDT 夜槽不得被擾動**：21:45 EDT 起停止長時間操作。
6. 變異 harness 必須放 `%TEMP%`、改副本、repo 檔全程不寫入。
7. 不得 `git add`／`git commit`／`git push`（由主大腦處理）。
8. 測試數字一律用 repo 的 `.venv\Scripts\python.exe`。
9. 合成 fixture 必須與生產事件**同形**（欄位齊備）——2026-10-23 實測：新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺。

---

## 7. 驗收（完成的定義）

1. **P4-T1～P4-T12 ＋ 既有 Phase 2 16 筆 ＋ 既有 Phase 1 13 筆全綠**（`.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_tension_closure_loop.py`）。
2. **M1～M6 全部轉紅** ＋ repo 檔 sha256 逐位元相同。
3. `git diff --name-only HEAD` **恰好** 3 檔（工單 §1）。
4. `data/**` 零改動（位元組對帳）。
5. `frozen_dirs` 11 筆零觸碰；`allowed` 不需變更（`tension_thinking.py` 已在表內）。
6. **真實 LLM 驗收**：**待 Owner 核准 §5 後**執行一次（1 agent × 1 呼叫），貼出實際輸出與成本；**未核准則本項標「待核准」即可交付**。
7. 生產接線（把 `LLMThinker` 接到排程器）**不在本票** ⇒ 另立 Phase 4 票，且**需 Owner 授權部署與重啟**。

---

## 8. 回報格式

1. **三個新增函式的簽章與語意**（一行一項）。
2. **守衛測試**：P4-T1～P4-T12 ＋ 既有 29 筆，各打了幾筆、紅幾筆。
3. **變異**：M1～M6 逐條 →（轉紅的測試名）／repo 檔 sha256 是否逐位元相同。
4. **幻覺護欄的三條判斷**各自在什麼輸入下 fail-closed（各舉一個具體輸入）。
5. **零真實呼叫證明**：script backend 呼叫計數 ＋ AST 斷言結果。
6. **真實 LLM 驗收**：已核准／未核准（未核准則不執行）。
7. **Frozen 檢查**：`git diff --name-only HEAD` 完整輸出 ＋ `data/**` 位元組對帳。
8. **未做與明文排除**（含「未合 main、未部署、未重啟、未跑全庫回歸」的自證）。
9. **未解決疑點 / 需 Owner 裁示的項目**。

---

## 9. 給執行者的一句提醒

**這個模組的唯一安全邊界是「LLM 說了不算」。** 護欄不是裝飾：`resolved_gap_count` 不得大於實際缺口數、`resolved+remaining` 不得大於實際缺口數、`decided=True` 必須 `remaining==0`、任何解析／型別失敗一律 fail-closed。**如果 LLM 幻覺說「我已想完」，系統不許信。**
