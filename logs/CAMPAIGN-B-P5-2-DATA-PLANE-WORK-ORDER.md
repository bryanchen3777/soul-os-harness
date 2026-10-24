# 工單：CAMPAIGN-B Phase 5 第二段（P5-②）張力驅動思考 資料面接線

> 開立：2026-10-24 10:30 EDT（主大腦；依 **Owner 2026-10-24 10:1x 裁決**：核准 Phase 5 真實 LLM 驗收預算 **1 Agent × 1 次呼叫／上限 400 Tokens／零重試（retries=0）／模型與通道完全沿用 proxy.py 既有配置／禁止任何額外計費呼叫**）
> 依賴：**Phase 3（`aa3ff39`）＋ Phase 4（`61957a0`）＋ P5-①（`85b0ab7`）已合入 main**
> 母案：`logs/CAMPAIGN-B-P5-THINKING-CONTEXT-WIRING-WORK-ORDER.md`（P5-①，v2）

---

## 0. 出發點（已量現況，不猜）

| 事實 | 值 | 來源 |
|---|---|---|
| Phase 3 的 `LLMThinker` 已內建**完全符合 Owner 核准預算**的成本護欄 | `max_calls=1`（預設）、`max_tokens=400`（預設）、`max_retries=0`（預設）；預算用盡 ⇒ **不再呼叫**，回 fail-closed（`reason="llm_call_budget_exhausted"`） | `src/inner_life/tension_thinking.py:503-556`（主大腦實讀） |
| `LLMThinker` 只經由**注入的** proxy 呼叫 | 簽章 `LLMThinker(proxy, *, model, max_tokens=400, max_calls=1, calls=None, max_retries=0)`；**不 import** `src.llm`、不自建 HTTP、不帶 API key、不繞過 `LLMProxy` | 同上（`tension_thinking.py:492-541`） |
| 「沿用既有配置」的**正規工廠** | `configs/loader.py:175` 的 `create_llm_proxy(cfg, bus)`（讀 `cfg["llm"]["provider"/"model"]` 與 API key，產出真實 `LLMProxy`）；既有呼叫點 `scripts/run_server.py:735` | 主大腦實讀 |
| 人工 opt-in 旗標（**缺省關閉** ⇒ 生產零行為變化） | `FEATURE_ENV = "SOUL_OS_TENSION_DRIVE_LOOP"`；`FEATURE_OFF_VALUES = {off,0,false,no,none}`；`feature_enabled()` 變數「缺席」**不代表** ON | `tension_closure_loop.py:101-105, 207-211` |
| 排程器 additive 分支慣例 | 既有 30s wake 內 `_goal_scan_all()`／`_fire_world_timer_eval()`／`_fire_lifecycle_eval(now)`／`_fire_wal_checkpoint()` 同級；0 新定時器／0 新 tick／0 新 task／0 新 sleep；fail-silent（例外只 log warning，不阻斷主循環） | `scheduler.py:1692-1722` |
| P5-① 已提供**生產唯讀**介面 | `format_working_state_lines_production(agent_id, *, max_entries, max_chars)`（路徑不可注入，內部由 `data_root()/inner_life` 推導） | `85b0ab7` |
| 生產上 `data/inner_life/working_state.jsonl` | **不存在** | 主大腦 2026-10-24 實測 |

### 🔺 Owner 的兩項裁定（本票必須一併落地）

1. **真實 LLM 驗收預算已核准**：**1 Agent × 1 次呼叫／上限 400 Tokens／零重試（retries=0）／模型與通道完全沿用 proxy.py 既有配置／禁止任何額外計費呼叫**。
2. **WAL 治理不單獨重啟**：補丁合入主幹後**不重啟服務**，待主線累積至下一階段或排程維護窗口再生效。⇒ **本票的一切生產行為必須預設為「零」**（旗標缺省 OFF），且 **worker 不得重啟服務**。

---

## 1. 目標

讓「角色尚未想通的事」**真的會產生**：把 Phase 4 的 `drive_loop_async()` 接到一個**旗標預設關閉**的觸發點，用 Phase 3 的真實 `LLMThinker` 跑一次思考，產出 `working_state.jsonl` 記錄，使 P5-① 的讀取面在旗標開啟後**有東西可讀**。

**誠實邊界**：本票**不**讓「角色已能感知思考狀態」在生產成立（旗標缺省 OFF ⇒ 生產仍無 `working_state.jsonl`）；本票成立的是「**資料面在旗標開啟時可運作，且其真實 LLM 路徑已被一次受限的真實呼叫驗證**」。

---

## 2. 做法（決策已定，執行者照做）

### 2.1 生產寫入面（`src/inner_life/tension_closure_loop.py`）

新增與 P5-① 讀取面**對稱**的生產寫入介面：

```python
async def drive_loop_async_production(
    agent_id: str,
    *,
    thinker: Any,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """（Phase 5-② 新增）生產 working_state 的驅動閉環（路徑不可注入）。

    🔴 **路徑不可注入**：``store_dir`` **不在簽章裡**，由本函式內部從
    ``src.paths.data_root()`` 推導為 ``<data_root>/inner_life`` ⇒ 呼叫端無法指定任何路徑。
    🔴 **寫入面僅限單一檔案**：只寫 ``working_state.jsonl``；**不得**碰
    ``epistemic_tensions.jsonl``／``trace.jsonl``／任何其他生產檔。
    🔴 **本函式是本票唯一被允許寫生產的例外**（Owner 已核准資料面）⇒ 必須有專屬守衛釘死。
    🔴 **fail-silent**：解析／寫入／thinker 例外 ⇒ 回 ``None``，**不得** raise。
    """
```

硬性要求：
1. **不得**改動既有 `drive_loop()`／`drive_loop_async()` 的**任何**語意（沙盒專用、仍過 `check_store_dir()`）。
2. **不得**改動 `check_store_dir()` 一行。
3. 寫入目標路徑**只能**是 `<data_root>/inner_life/working_state.jsonl`（其餘檔案一律不得建立或修改）。
4. 失敗一律回 `None`（不 raise、不寫半套記錄）。

### 2.2 觸發點（`src/soul/scheduler.py`）

新增 additive 分支 `_fire_tension_drive_loop(now)`，掛在**既有 30s wake** 內，與 `_goal_scan_all()`／`_fire_world_timer_eval()`／`_fire_lifecycle_eval(now)`／`_fire_wal_checkpoint()` **同級**（放在 `_fire_wal_checkpoint()` 之後即可）。

判準（全部必須成立才動作）：
1. 🔴 **旗標閘**：`feature_enabled()` 為真（`SOUL_OS_TENSION_DRIVE_LOOP`，**缺省 OFF**）⇒ 旗標未設時**整支不動作**（0 LLM、0 寫入、0 副作用）。
2. **槽窗**：只在 **night slot**（`_slot_for_time(now) == "night"`）的既有檢查窗內動作（與 `_fire_lifecycle_eval` 同款判據）。
3. **at-most-once 冪等鍵**：落在 scheduler 內（形如 `agent_id:YYYY-MM-DD`）⇒ 30s tick 重入同一槽窗**不會重複評估、不會重複花費 LLM**。
4. 🔴 **每日最多 1 個 agent、每個 agent 最多 1 次真實 LLM 呼叫**（Owner 核准預算）⇒ 每輪最多處理 **1** 個 agent。
5. **0 新定時器／0 新 tick／0 新 task／0 新 sleep**（本方法內不得有 `sleep`／`while`／`create_task`）。
6. **fail-silent**：任何例外只 `logger.warning`，**不阻斷主循環**（與同級分支一致）。

### 2.3 真實 Thinker 接線（`src/inner_life/tension_thinking.py`）

新增一個**薄接線 helper**（不新增任何 LLM 邏輯）：

```python
def build_llm_thinker_from_config(cfg: dict, bus: Any, *, calls: Optional[Dict[str, Any]] = None):
    """（Phase 5-② 新增）以既有設定組出真實 LLMThinker（**不繞過 LLMProxy**）。

    🔴 **沿用既有配置**：proxy 一律經 ``configs.loader.create_llm_proxy(cfg, bus)``
    產生（讀 ``cfg["llm"]["provider"/"model"]`` 與 API key）；**不得**在此處
    硬編碼模型名稱、不得自建 HTTP 客戶端、不得繞過 ``LLMProxy``。
    🔴 **預算硬編碼**：``max_tokens=400``／``max_calls=1``／``max_retries=0``
    ⇒ **不得**由環境變數或參數放寬（Owner 核准值）。
    """
```

硬性要求：
1. 🔴 `max_tokens=400`／`max_calls=1`／`max_retries=0` **必須是字面值**，不得可被環境變數覆寫。
2. 模型名稱**只能**從既有設定取得（`create_llm_proxy` 內部解析的 `cfg["llm"]["model"]`）；**不得**在此模組硬編碼任何模型字串。
3. **不得** import `requests`／`httpx`／`urllib`；**不得**自建 HTTP 客戶端。

### 2.4 真實 LLM 驗收（Owner 核准預算的唯一一次）

🔴 **worker 不得打任何真實 LLM 呼叫**。worker 的交付物之一是**可執行的驗收腳本**（見 §3.4），由**主大腦**在複核程式碼後**執行恰好一次**。

驗收腳本的硬性要求：
- 在 **worktree 內**（`%TEMP%\p5b_wt`）執行，`store_dir` **必須在 `%TEMP%` 之下**（沙盒）⇒ **零生產寫入**。
- 使用 `create_llm_proxy(cfg, bus)` 取得**真實** proxy ＋ `build_llm_thinker_from_config` 組出 thinker。
- **恰好 1 個 agent、恰好 1 次呼叫**；`calls["attempted"] == 1`。
- 🔴 **零重試**：若該次呼叫失敗（proxy 例外／回空／不可解析）⇒ **直接記失敗，不得再打第二次**（`max_retries=0` 已確保恰好 1 次 HTTP 嘗試）。
- 產出必須能被 **P5-① 的沙盒讀取面** `format_working_state_lines(agent_id, sandbox_store)` 讀回（逐位元比對）。
- 必須回報：provider／model／`calls` 全部計數／實際 tokens／產出的 record／read-back 的那一行。

### 2.5 邊界（硬性）
1. 🔴 **不得**重啟服務、**不得**部署、**不得**殺任何行程、**不得**寫 `data/**`。
2. 🔴 **不得**改 `src/llm/proxy.py`、`src/configs/loader.py`、`src/inner_life/tension_scoring.py`、任何 `frozen_dirs` 檔。
3. 🔴 **不得**新增任何持久化檔案、schema、DDL、計時器、背景 task、網路呼叫。
4. 🔴 **不得**在本票把旗標預設打開（`FEATURE_OFF_VALUES` 語意不得變更）。
5. 🔴 不得修改 `scripts/run_server.py`（受 `tests/test_m3_1_phase_c.py` 保護）。

---

## 3. 守衛測試（新增檔 `tests/inner_life/test_p5_data_plane_guard.py`）

| 編號 | 內容 |
|---|---|
| P7-T1 | **旗標缺省 OFF** ⇒ 排程器的 `_fire_tension_drive_loop()` 在旗標未設時**完全不動作**（0 LLM 呼叫、0 寫入） |
| P7-T2 | **旗標 ON + night slot + 未重複** ⇒ 觸發**恰好 1 個 agent、恰好 1 次** thinker 呼叫 |
| P7-T3 | **at-most-once**：同一 `agent:date` 再觸發 ⇒ **不再呼叫**（呼叫計數不增加） |
| P7-T4 | **每日上限**：多個 agent 候選時，單輪**最多 1 個** agent 被處理 |
| P7-T5 | **寫入面**：只寫 `working_state.jsonl`；`epistemic_tensions.jsonl`／`trace.jsonl`／其他生產檔**位元不變、且無新檔** |
| P7-T6 | **路徑不可注入**：`drive_loop_async_production()` 簽章**不含** `store_dir`／`path`；`format_working_state_lines_production()` 同樣不含 |
| P7-T7 | **沙盒守門未放寬**：`drive_loop_async(agent, now, <生產路徑>)` 仍回空／不寫（`check_store_dir` 未被放寬） |
| P7-T8 | **預算硬編碼**：接線處的 `max_tokens=400`／`max_calls=1`／`max_retries=0` 是字面值，**不可**由環境變數改變（AST 斷言：無 `os.environ` 讀取這三個參數） |
| P7-T9 | **不繞過 proxy**：接線 helper **不得** import `requests`／`httpx`／`urllib`、不得自建 HTTP 客戶端（AST 斷言） |
| P7-T10 | **0 新計時器／0 新 task／0 新 sleep**：`_fire_tension_drive_loop()` 內無 `sleep`／`while`／`create_task`（AST 斷言） |
| P7-T11 | **fail-silent**：thinker 拋例外 ⇒ 排程器**不 raise**、主循環不受影響（只 log warning） |
| P7-T12 | **0 真實 LLM**：守衛檔內**不得**出現真實 provider 呼叫（測試一律注入 Mock backend；`calls["attempted"]` 必為 0 或僅對 mock） |

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| M23 | 旗標閘失效（`feature_enabled()` 恆 True） | P7-T1 |
| M24 | 移除 at-most-once 冪等鍵 | P7-T3 |
| M25 | 每日上限放寬（1 → 全部 agent） | P7-T4 |
| M26 | 寫入面放寬（多寫一個生產檔） | P7-T5 |
| M27 | 生產寫入面加上 `store_dir` 參數 | P7-T6 |
| M28 | 放寬沙盒守門（`check_store_dir` 直接回 True） | P7-T7 |
| M29 | 預算參數改由 `os.environ` 取得（可被放寬） | P7-T8 |
| M30 | 接線 helper 自建 HTTP 客戶端（import httpx） | P7-T9 |
| M31 | 在 `_fire_tension_drive_loop()` 內加 `await asyncio.sleep(...)` | P7-T10 |
| M32 | 讓 thinker 例外冒泡（移除 try/except） | P7-T11 |

- 變異 harness 放 `%TEMP%`（**改副本**模式，repo/worktree 檔全程不寫入）；baseline 必須是**整個新守衛檔全綠**；收尾必印被測檔 sha256 與初始值相同。

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 5 檔**：`src/inner_life/tension_closure_loop.py`、`src/inner_life/tension_thinking.py`、`src/soul/scheduler.py`、**新增** `tests/inner_life/test_p5_data_plane_guard.py`、`logs/ENGINEERING_STATE.md`（追加條目）。多一檔少一檔都**停止並回報**。
2. 新守衛檔 **12 筆全綠**（P7-T1～P7-T12）；變異 **M23～M32 全數轉紅**。
3. **worker 端 0 真實 LLM 呼叫**（`calls["attempted"]` 全程為 0；報告須明列）。
4. **主大腦**執行驗收腳本 **恰好 1 次** ⇒ 報告 provider／model／tokens／呼叫次數／產出 record／read-back 行。
5. 旗標缺省 OFF ⇒ **生產零行為變化**（以 mock 驗證：旗標未設時排程器分支不動作）。
6. `logs/ENGINEERING_STATE.md` 追加條目（例：§0.1.64）必須寫明：① **Owner 的兩項裁定原文要點**（真實 LLM 預算：1 agent × 1 call／400 tokens／retries=0／沿用既有配置／禁止額外計費；WAL 治理不單獨重啟、併入後續重大窗口）② 本票的契約變更（**允許寫入生產 `data/inner_life/working_state.jsonl` 單一檔案**，與 P5-① 的生產唯讀面對稱）③ 旗標缺省 OFF 與生效條件 ④ 守衛 12 條 ＋ 變異 M23～M32 ⑤ 真實 LLM 驗收的實測數字 ⑥ **誠實聲明：旗標 OFF ⇒ 生產上 `working_state.jsonl` 仍不存在，「角色已能感知思考狀態」在生產仍不成立**。

## 5. 測試
- 定向：`pytest -q tests/inner_life/test_p5_data_plane_guard.py` ⇒ 預期 **12 passed / 0 failed**。
- 既有守衛不得被削弱：`pytest -q tests/inner_life/test_tension_closure_loop.py tests/inner_life/test_p5_working_state_context.py` ⇒ 預期 **85 passed / 0 failed**。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests` ⇒ 預期 **7316 passed / 0 failed**（7304 ＋ 本票 12）。🔴 引用數字必須註明解譯器是 repo 的 `.venv`。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**（pytest 在零失敗時輸出裡根本沒有 "failed" 字樣）。
- 🔴 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765-8767`、**不得**對生產發真實請求（`tests/**` 禁用 subprocess／os.spawn／os.system／os.popen）。
- 🔴 變異 harness 只能改**副本**；repo 檔全程不寫入。

## 6. 不做（Out of Scope）
- **不**重啟服務、**不**部署、**不**開旗標（`SOUL_OS_TENSION_DRIVE_LOOP` 缺省保持 OFF）。
- **不**寫 `data/**`（沙盒驗收也必須把 store_dir 放在 `%TEMP%` 之下）。
- **不**改 `src/llm/proxy.py`／`src/configs/loader.py`／`src/inner_life/tension_scoring.py`／`scripts/run_server.py`／任何 `frozen_dirs` 檔。
- **不**改 `format_working_state_lines()`／`format_working_state_lines_production()`／`drive_loop()`／`drive_loop_async()`／`check_store_dir()` 的既有語意。
- **不**打真實 LLM 呼叫（worker 端）；**不**改任何既有 LLM 成本參數。
- 不 commit／push／git add（由主大腦合入時處理）。
- 不做 A/B 對照實驗；不製造 synthetic 張力事件。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/stages.py`／`trigger_handler.py`／`event_handler.py`／`dream_handler.py`／`diary_handler.py`、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。
`src/inner_life/tension_closure_loop.py`／`tension_thinking.py`／`src/soul/scheduler.py` **皆非** frozen（`scheduler.py` 已在 observability 測試 `allowed` 集合內）⇒ 本票可改，但**最小差異**原則適用。

## 8. 回報格式
- 5 檔範圍 ＋ numstat。
- 新守衛 12 筆 ＋ 既有 85 筆的**實際數字**（分子/分母/單位）。
- 變異 **M23～M32 逐條**（轉紅的測試名）。
- **worker 端真實 LLM 呼叫數 = 0** 的證明（`calls["attempted"]` 全程計數）。
- 交付的**驗收腳本**路徑與**執行方式**（主大腦將執行**恰好一次**）；腳本自帶的硬上限（1 agent／1 call／400 tokens／retries=0／store_dir 在 %TEMP% 之下）逐條列出。
- 旗標缺省 OFF 的證據（AST 或行為）。
- `logs/ENGINEERING_STATE.md` 追加條目內容摘要（含 Owner 兩項裁定）。
- **誠實聲明：旗標 OFF ⇒ 生產上 `working_state.jsonl` 仍不存在，「角色已能感知思考狀態」在生產仍不成立**；並列出旗標開啟後的生效條件與風險（每次開啟每日 1 次真實 LLM 呼叫）。
- 未做與明文排除。
