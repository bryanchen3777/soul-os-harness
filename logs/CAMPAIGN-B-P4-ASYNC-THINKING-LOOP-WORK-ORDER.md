# 工單：CAMPAIGN-B-P4-ASYNC-THINKING-LOOP（戰役一 Phase 4）

> **開立**：主大腦（2026-10-23 22:0x EDT）
> **依據裁定**：Owner (Bry) 2026-10-23 22:0x「**週末不空轉：Phase 4 即刻開拓**」
> - 「**補齊 Async 閉環引擎**：實作 `drive_loop_async()`（解開 Phase 3 受限於 3 檔上限而留下的技術債）。讓非同步思考（`LLMThinker.think()`）能在不阻塞 Event Loop 的前提下，平滑驅動 Working State 的更新。」
> - 「**修復稽核軌跡錨點**：將 `think_prompt_sha256` 修正為精確記錄 `LLMThinker.last_prompt_sha256`，杜絕稽核指針指回 Phase 2 模板的缺陷。」
> - 「**推進動機管線（Motive Pipeline）前置對齊**：設計張力驅動思考產出的 `working_state`，如何作為**唯讀上下文**注入現有角色的 Inner Life，為後續接線提供確定性接口。」
> **Phase 4 定位**：**只提供接口與閉環引擎，不接生產**（實際注入呼叫是 Phase 5 的事）。
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**。

---

## 0. 出發點（Phase 3 已交付的現況）

- `src/inner_life/tension_thinking.py`：`build_llm_thinking_prompt()` / `parse_llm_outcome()`（**4 條護欄／5 個檢查點**，`reason` 代碼集合＝`llm_parse_failed`／`llm_outcome_invalid`／`llm_call_failed`／`llm_call_budget_exhausted`／`llm_outcome`，**不得擴張**）／`LLMThinker`（**async `think()`**、只經注入的 proxy、`max_calls` 預算、`calls` 計數、**`last_prompt_sha256`**）。
- `src/inner_life/tension_closure_loop.py`：`ThinkRequest`／`ClosureTrace`／**`drive_loop()`（同步，L399）**／`WORKING_STATE_FIELDS`（13 欄）／`feature_enabled()`／`check_store_dir()`／`_append_jsonl()`。
- 已知**技術債兩項**（Phase 3 因「恰好 3 檔」硬上限留下）：① `drive_loop_async()` 未實作 ② `think_prompt_sha256` 記的是 **Phase 2 提示詞**（2428 字元／`8fe5d587…`）而非實際送出的 LLM 提示詞（2943 字元／`cc8631a3…`）⇒ **稽核軌跡指錯對象**。

---

## 1. 範圍（可動 4 檔；**不需要**新增 `src/**` 檔 ⇒ 不必動 `allowed`）

| # | 檔案 | 動什麼 |
|---|---|---|
| 1 | `src/inner_life/tension_closure_loop.py` | 抽出共享核心 ＋ `drive_loop_async()` ＋ 稽核錨點修正 ＋ `format_working_state_lines()`（唯讀上下文接口） |
| 2 | `tests/inner_life/test_tension_closure_loop.py` | **擴充既有檔**（不得新建 `tests/**` 下的 collectable `.py`） |
| 3 | `logs/ENGINEERING_STATE.md` | **追加** §0.1.60 |
| 4 | `logs/CAMPAIGN-B-P4-ASYNC-THINKING-LOOP-WORK-ORDER.md` | 本工單本體（主大腦已開立，**執行者不要改**） |

🔴 **`src/llm/proxy.py` 不得改動**（Phase 5 的接線面）。🔴 **不新增 `src/**` 檔案** ⇒ `tests/test_observability_completeness_1.py` 的 `allowed`／`frozen_dirs` **零改動**。

---

## 2. 做法（決策已定）

### 2.1 抽出共享核心（同步路徑**逐位元不變**）

把 `drive_loop()` 的流程拆成：
```python
def _run_loop(agent_id, now, store_dir, outcome_provider) -> Optional[ClosureTrace]:
    """共享核心：階段 1 取張力 → 階段 1.5 取 outcome → 階段 2 分流 → 階段 3 寫 working_state。"""
```
- `outcome_provider` 是一個**同步** callable `(req, prompt) -> ThinkOutcome`。
- `drive_loop()` ＝ `_run_loop(..., outcome_provider=<同步 thinker>)`。
- 🔴 **同步路徑的既有行為必須逐位元不變**（既有 49 筆守衛全綠 ＝ 證據）。**不得**順手「優化」既有邏輯。

### 2.2 `drive_loop_async()`（新增）

```python
async def drive_loop_async(agent_id: str, now: int, store_dir: Any, *,
                           thinker: Optional[Any] = None) -> Optional[ClosureTrace]:
```
- 🔴 **只接受 async thinker**（`think()` 必須回傳 awaitable）。
  - 傳入**同步** thinker ⇒ **明確 raise**（`TypeError`，訊息須指明「請改用 `drive_loop()` 或傳入 async thinker」）⇒ **不得**靜默、不得「湊合著跑」。
- 對稱地：**`drive_loop()`（同步）收到 async thinker ⇒ 明確 raise**（`TypeError`，訊息指明改用 `drive_loop_async()`）。
  ⇒ 兩條路徑**互斥且明確**；**不允許**任何一條路徑偷偷跑另一條的分支。
- 階段 1.5：`outcome = await thinker.think(req, prompt)`（**不阻塞 Event Loop**：整個 async 函式內不得有 `time.sleep`／同步阻塞 IO 逾 50 ms 以上的呼叫）。
- 其餘階段（分流、寫檔、欄位）**與同步路徑共用同一核心** ⇒ 兩條路徑產出的 working_state / tension 事件**逐位元相同**（同一輸入、同一 thinker 回應）✔（這是本票最關鍵的不變量）。
- `feature_enabled()` 為 False 時 ⇒ **不建立任何檔案、回 `None`**（與同步路徑同語意）✔。

### 2.3 稽核錨點修正（`think_prompt_sha256` 語意變更）

- `ThinkOutcome` **新增一個欄位** `think_prompt_sha256: str = ""`（**帶預設值** ⇒ 既有構造呼叫相容）。
- `think_prompt_sha256` 欄位的語意**從「Phase 2 提示詞的 sha256」改為「本次思考實際送出的 prompt 的 sha256」**：
  - `LLMThinker` 回傳的 outcome 必須填入**它實際送出的** prompt 的 sha256（即 `LLMThinker.last_prompt_sha256`）。
  - `DeterministicMockThinker` 填入**它送出的**（Phase 2）prompt 的 sha256。
  - `drive_loop()`／`drive_loop_async()` **不得自行重算** prompt 的 sha256 ⇒ 一律**取自 outcome**（這是「稽核指針指錯對象」的根因修正：sha256 必須由**送出者**回報，不是由接收者猜）。
- 🔴 **既有 `WORKING_STATE_FIELDS` 的 13 個欄位名**不變；`think_prompt_sha256` **仍在表內**（語意變更，不是新增欄位）⇒ 欄位數維持 13 ✔。
- 🔴 語意變更必須在 §0.1.60 與守衛中留痕。

### 2.4 `format_working_state_lines()`：working_state 的**唯讀**上下文接口（新增，**不接線**）

```python
def format_working_state_lines(agent_id: str, store_dir: Any, *,
                               max_entries: int = 5, max_chars: int = 60) -> List[str]:
    """把 working_state 的最新狀態轉成**唯讀**的內在生活風格行。

    格式**對齊**既有 Inner Life 區塊（`src/llm/proxy.py` 的
    `_format_recent_inner_life()`）：`- [<日期> <標籤>] <摘要 截斷至 60 字>`。
    """
```
**行為（逐條決策已定）**：
1. 讀 `store_dir` 下的 `working_state.jsonl`；**只讀**（不得寫、不得 append、不得 checkpoint）。
2. 取**最後一筆有效記錄**（壞行跳過、不 raise）；檔案不存在或無有效記錄 ⇒ **回空 list**（fail-silent，絕不 raise）。
3. 該筆 `decided is True`（已想完 ⇒ 無未解張力）⇒ **回空 list**。
4. 否則 ⇒ 回**一條**：
   `"- [{YYYY-MM-DD} 張力未解] 還有 {remaining_gap_count} 個未解的問題（已想過 {partial_resolution:.0%}）"`
   - 日期取該筆 `ts` 的前 10 字元。
   - `remaining_gap_count`／`partial_resolution` 缺值時以 `0`／`0.0` 代入（不得 raise）。
   - 整行**截斷至 `max_chars`**（60）⇒ 截斷後加 `...`（與 proxy.py 既有截斷語意同款：切到 `max_chars - 3` 再加 `...`）。
5. `max_entries` 上限（預設 5）——目前只輸出 1 條，但**介面必須支援上限語意**（未來多筆記錄時的上限位）。
6. 🔴 **不得**呼叫 LLM、**不得**連網、**不得**讀生產路徑（`store_dir` 必須先過 `check_store_dir()` 的既有雙條件；不合格 ⇒ 回空 list，**不得** raise）。
7. 🔴 **本票不得**把這個函式接進 `proxy.py` 或任何 prompt 組裝路徑 ⇒ 它只是一個**確定性接口**，供 Phase 5 使用。

---

## 3. 守衛測試（新增 **P5-T1～P5-T12**；P5 = 本票批次，與 Phase 3 的 `P4-T*` 區分）

🔴 命名：`P5-T*` = CAMPAIGN-B **Phase 4** 的守衛批次（`P4-T*` 是 Phase 3 的，不要撞名）。
🔴 **既有 49 筆守衛（Phase 1 13 ＋ Phase 2 16 ＋ Phase 3 20）必須全部維持綠**。

| 編號 | 測試 | 斷言 |
|---|---|---|
| P5-T1 | 同步路徑拒絕 async thinker | `drive_loop()` 收到 async thinker ⇒ **raise**（`TypeError`）且**不得**建立任何檔案 |
| P5-T2 | async 路徑拒絕同步 thinker | `drive_loop_async()` 收到同步 thinker ⇒ **raise**（`TypeError`）且**不得**建立任何檔案 |
| P5-T3 | async 端到端 | 注入假 async thinker ⇒ 沙盒 `working_state.jsonl` **+1**、`epistemic_tensions.jsonl` **+1**、事件狀態分流正確 |
| P5-T4 | **兩路徑產出逐位元相同** | 同一輸入、同一 outcome ⇒ 同步路徑與 async 路徑寫出的 working_state / tension 事件**逐位元相同** |
| P5-T5 | **稽核錨點** | `working_state.think_prompt_sha256` ＝ **thinker 回報的** sha256（用假 thinker 回報一個與 Phase 2 prompt 不同的值來驗證）⇒ **不得**是 Phase 2 prompt 的 sha256 |
| P5-T6 | `ThinkOutcome` 相容性 | 未填 `think_prompt_sha256` 時（預設 `""`）不得 raise 既有呼叫點 |
| P5-T7 | 介面：無檔案 | `format_working_state_lines()` 回**空 list**（不得 raise、不得建立檔案） |
| P5-T8 | 介面：已想完 | 該筆 `decided=True` ⇒ 回**空 list** |
| P5-T9 | 介面：未解張力 | `decided=False` ⇒ 回**一行**、格式 `- [YYYY-MM-DD 張力未解] …`、長度 ≤ 60 |
| P5-T10 | 介面：壞行 | 檔案含壞行 ⇒ 跳過該行、不 raise |
| P5-T11 | 介面**只讀** | 呼叫前後 `working_state.jsonl` 與 `epistemic_tensions.jsonl` **位元不變** |
| P5-T12 | 介面**不觸碰生產** | 傳入不合格的 `store_dir`（生產路徑）⇒ 回空 list、**不得** raise、**不得**寫入生產 |

---

## 4. 變異實驗（M8～M13，每條**必須轉紅**）

- harness 必須放 `%TEMP%`（`tests/**` 禁 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`）、採「**改副本**、repo 檔全程不寫入」模式。
- 🔴 **baseline 必須是「整個守衛檔全綠」**（2026-10-23 實測：只測部分測試的 baseline 會讓**恆紅的守衛**冒充有效的變異證據）。

| # | 變異 | 應轉紅 |
|---|---|---|
| M8 | `drive_loop()` 對 async thinker **靜默忽略**（不 raise） | P5-T1 |
| M9 | `drive_loop_async()` 對同步 thinker 靜默忽略 | P5-T2 |
| M10 | `think_prompt_sha256` 改回記 **Phase 2 prompt** 的 sha256 | P5-T5 |
| M11 | `format_working_state_lines()` 在檔案不存在時 **raise** | P5-T7 |
| M12 | `format_working_state_lines()` 漏掉 `decided` 判斷（已想完也輸出） | P5-T8 |
| M13 | 介面函式**寫入** working_state（破壞只讀） | P5-T11 |

---

## 5. 邊界（硬性）

1. 🔴 **只可改 §1 的 4 檔**；**不得**改 `src/llm/proxy.py`、`src/inner_life/tension_scoring.py`、`src/inner_life/tension_thinking.py`（本票**不**需要動它）、任何 `frozen_dirs` 檔、任何 `allowed` 集合。
2. 🔴 **不得**把 `format_working_state_lines()` 接進 `proxy.py` 或任何 prompt 組裝路徑（Phase 5 的事）。
3. 🔴 **不得**接排程器／handler；**不得**新增生產寫入路徑；**不得**寫 `data/**`；**不得**部署；**不得**重啟服務；**不得**殺任何行程。
4. 🔴 **不得**打真實 LLM 呼叫（Owner 額度已用完，**禁止**第二次）。
5. 🔴 **不得跑全庫回歸**（由主大腦在合入前執行）；你只跑受影響的守衛測試檔。
6. 🔴 **時間紀律**：21:45–22:45 EDT 為低噪音窗（今晚 22:00 夜槽演化中）⇒ **該窗內只讀碼、寫碼，不跑長時間測試**；定向測試與變異等 **22:45 之後**再跑。
7. 變異 harness 必須放 `%TEMP%`、改副本、repo 檔全程不寫入、baseline 整檔全綠。
8. 不得 `git add`／`git commit`／`git push`（主大腦處理）。
9. 測試數字一律用 repo 的 `.venv\Scripts\python.exe`。
10. 合成 fixture 必須與生產事件**同形**（欄位齊備）——2026-10-23 實測：新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺。

---

## 6. 驗收（完成的定義）

1. **P5-T1～P5-T12 ＋ 既有 49 筆全綠**（`.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_tension_closure_loop.py`）。
2. **M8～M13 全部轉紅** ＋ baseline 整檔全綠 ＋ repo 檔 sha256 逐位元相同。
3. `git diff --name-only HEAD` **恰好** 4 檔（§1）。
4. `data/**` 零改動（位元組對帳自證）。
5. `frozen_dirs` 11 筆 ＋ `allowed` 集合**零改動**；`src/llm/proxy.py` 零改動（用 `git diff --numstat` 自證）。
6. §0.1.60 登錄完成（含 `think_prompt_sha256` **語意變更**的留痕 ＋ P5-T* 與 M8～M13 結果 ＋ Phase 5 的接線前置說明）。

---

## 7. 回報格式

1. `_run_loop()` 抽取後**同步路徑行為不變**的自證（既有 49 筆全綠 ＋ 兩路徑逐位元相同的證據）。
2. `drive_loop_async()` 的簽章與兩條路徑的**互斥／明確 raise** 語意（各舉一個輸入與其結果）。
3. `think_prompt_sha256` 語意變更的落點（哪裡填、值從哪來）＋ 守衛 P5-T5 的實測。
4. `format_working_state_lines()` 的逐條行為實測（無檔案／已想完／未解／壞行／只讀／不觸碰生產）。
5. 守衛：P5-T1～P5-T12 ＋ 既有 49 筆，各打了幾筆、紅幾筆。
6. 變異 M8～M13 逐條 →（轉紅的測試名）／repo 檔 sha256 是否逐位元相同／baseline 整檔是否全綠。
7. Frozen 檢查：`git diff --name-only HEAD` 完整輸出 ＋ `data/**` 位元組對帳 ＋ `allowed`／`frozen_dirs`／`proxy.py` 零改動自證。
8. 未做與明文排除（未合 main、未部署、未重啟、未接線、未打真實 LLM、未跑全庫回歸）。
9. 未解決疑點 / 需 Owner 裁示的項目。

---

## 8. 給執行者的一句提醒

**Phase 4 的核心不是「多寫一個 async 函式」，而是「讓同步與 async 兩條路徑共用同一個核心、產出逐位元相同」** —— 否則 async 閉環就會變成第二套語意、第二個真相來源。同樣地，**稽核 sha256 必須由送出者回報**（`LLMThinker.last_prompt_sha256`），接收者不得自行重算 —— 否則稽核指針還是會指錯對象。
