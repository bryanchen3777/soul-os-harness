# 工單：CAMPAIGN-B 全鏈路確定性 E2E 封頂（Capstone E2E）

> 開立：2026-10-24 12:00 EDT（主大腦；**主大腦依 Bry 2026-10-24 的兩選一裁定，裁示「甲＋乙並行」**——甲為戰役一封頂、零 LLM 成本、純離線；乙為唯讀掃雷，兩者互不衝突）
> 依賴：Phase 3（`aa3ff39`）／Phase 4（`61957a0`）／P5-①（`85b0ab7`）／P5-②（合入中）已合入 main
> 母案：`logs/CAMPAIGN-B-P5-2-DATA-PLANE-WORK-ORDER.md`

---

## 0. 出發點（已量現況，不猜）

| 事實 | 值 | 來源 |
|---|---|---|
| 戰役一各段守衛皆已獨立成立 | Phase 1 13 筆／Phase 2 16 筆／Phase 3 20 筆／Phase 4 21 筆（合計 70）／P5-① 15 筆／P5-② 12 筆 | 各票 |
| 但**缺一個「從頭跑到尾」的整合場景** | 每段各自綠，**無任何單一測試**覆蓋「張力 → drive_loop → working_state → proxy 注入」全鏈 | 主大腦 2026-10-24 覆盤 |
| P5-① 的代理注入讀的是**生產唯讀面** | `format_working_state_lines_production(agent_id)`（路徑不可注入，內部由 `src.paths.data_root()` 推導） | `85b0ab7` |
| P5-② 的資料面已可用**沙盒**路徑驗證 | `drive_loop_async(agent_id, now, store_dir, thinker=...)` 寫 `<store_dir>/working_state.jsonl` | P5-② |
| 🔺 **代理組裝的資料根在 import 期擷取** | `proxy.py` 的 `INNER_LIFE_DATA_DIR` 是**模組層常數**（import 時求值）；而 `format_working_state_lines_production()` 內部是**呼叫期**取 `src.paths.data_root` | 主大腦／P5-① worker 實讀 |
| P5-② 的真實 LLM 路徑已被驗證 | 真實 LLM → 決定 → working_state → P5-① 讀面逐位元讀回（1 次呼叫，ollama／deepseek-v4.1-flash:1017） | 主大腦 2026-10-24 11:1x |

---

## 1. 目標

用**一個純離線、零網路、秒級**的確定性測試場景，把戰役一封頂為「**從頭跑到尾**」的成型機制：
**產生 D1 資訊缺口張力 → 觸發 `drive_loop_async()` → 呼叫 MockThinker → 寫入 working_state → `proxy.py` 讀取並注入到 Prompt。**

**為什麼**：每一段都有高密度單元守衛，但「各段都綠」不等於「整條鏈路通」。Bry 的原話：「將整條戰役一封頂為 100% 決策完備、隨時可透過旗標上線的成型機制。」

---

## 2. 做法（決策已定，執行者照做）

### 2.1 新增測試檔

`tests/inner_life/test_campaign_b_e2e_closed_loop.py`（**新增**，單一檔案；執行時間目標 **< 5 秒**）。

### 2.2 沙盒隔離（**決定性前提**，必須解決）

P5-① 的代理注入走**生產唯讀面**，而 `proxy.py` 的 `INNER_LIFE_DATA_DIR` 是 **import 期擷取的模組常數** ⇒ 若不處理，測試會讀生產、且**測試順序會影響結果**。

**做法（決策已定）**：在測試內以 `pytest` 的 `monkeypatch` **同時**覆寫：
1. `src.paths.data_root` → 回傳沙盒資料根（`tmp_path/"data"`）。⇒ `format_working_state_lines_production()` 內部的 `from src.paths import data_root` 是**呼叫期**取屬性 ⇒ 覆寫生效。
2. `src.llm.proxy.INNER_LIFE_DATA_DIR` → 沙盒的 `inner_life` 目錄（若該模組確實以該常數讀 inner_life 區塊；**以實讀為準**）。
3. 其他在 import 期擷取資料根的**模組層常數**（worker 必須自行盤點並列出，見 §4.6）。

⇒ 結果：**測試全程不讀、不寫生產資料**；且**與測試執行順序無關**（monkeypatch 於每個測試結束時還原）。
🔴 **不得**用 `monkeypatch.setenv` 改 `SOUL_OS_DATA_DIR` 來取代（`data_root()` 是**行程期快取單例**，改環境變數不會立刻生效，且會污染同檔其他測試）。

### 2.3 場景鏈路（**逐步可驗證**）

1. **產生 D1 資訊缺口張力**：在沙盒 store 下，用既有沙盒介面（`tension_closure_loop` 的 `drive_loop`／`drive_loop_async` ＋ `tension_scoring` 的評分路徑）讓 `agent_akane` 產生**一筆 D1 資訊缺口張力**（`epistemic_tensions.jsonl` 落於沙盒）。**不得**自造張力格式。
2. **驅動閉環**：`drive_loop_async(agent_id, now, sandbox_store, thinker=<Phase 2 的決定性 MockThinker>)` ⇒ 沙盒內產生 `working_state.jsonl`。
3. **讀回**：`format_working_state_lines(agent_id, sandbox_store)` ⇒ 取得預期行（格式 `- [YYYY-MM-DD 張力未解] 還有 N 個未解的問題（已想過 X%）`）。
4. **代理注入**：組裝 prompt（`proxy.py` 的 group 與 private **兩條**路徑）⇒ 斷言 system prompt **含** `[思考狀態]` 區塊、其正文與第 3 步**逐位元相同**、且位置在 `[最近內在生活]` **之後**、world_context **之前**。
5. **確定性**：同一組輸入**連跑兩次** ⇒ 兩次組出的 prompt **逐位元相同**。

### 2.4 MockThinker 與零成本

- **必須**使用 Phase 2 既有的**決定性 MockThinker**（沙盒、零 API 成本、可重現）⇒ **0 真實 LLM 呼叫**。
- 🔴 不得注入任何真實 proxy；不得 import `requests`／`httpx`／`urllib`；不得連網。

### 2.5 邊界（硬性）
1. 🔴 **不得**改 `src/**`（本票只**新增測試**；若發現 `src/**` 有缺陷 ⇒ **標示並回報**，不得自行修）。
2. 🔴 **不得**寫 `data/**`、不得重啟服務、不得部署、不得殺行程。
3. 🔴 **不得**打真實 LLM 呼叫。
4. 🔴 **不得**改既有守衛檔（`tests/inner_life/test_tension_closure_loop.py`／`test_p5_working_state_context.py`／`test_p5_data_plane_guard.py`）——本票**只新增**檔案。
5. 🔴 `tests/**` 禁用 `subprocess`／`os.spawn*`／`os.system`／`os.popen`（R5 護欄）。
6. 🔴 不得 `git add`／`git commit`／`git push`。

---

## 3. 守衛（新增檔，**恰 8 筆**，E2E-T1～E2E-T8）

| 編號 | 內容 |
|---|---|
| E2E-T1 | **全鏈路閉環**：沙盒內產生 D1 張力 → `drive_loop_async`（MockThinker）→ `working_state.jsonl` 落地 → 代理 prompt **含**正確的 `[思考狀態]` 行 |
| E2E-T2 | **注入位置**：`[思考狀態]` 在 `[最近內在生活]` 之後、world_context 之前（group 路徑） |
| E2E-T3 | **逐位元一致**：prompt 內的 `[思考狀態]` 正文 ＝ `format_working_state_lines()` 的讀回行 |
| E2E-T4 | **確定性**：同一輸入連跑兩次 ⇒ prompt **byte-identical** |
| E2E-T5 | **零網路／零真實 LLM**：AST 斷言（本檔與所觸及的模組無 `requests`／`httpx`／`urllib`）＋ MockThinker 的呼叫計數 ＝ 1 且**無任何真實 provider** |
| E2E-T6 | **沙盒隔離**：所有寫入都落在沙盒；且**生產 `data/inner_life/working_state.jsonl` 仍不存在**（該檔沒有任何生產寫入者 ⇒ 此斷言**決定性**、不受線上服務競態影響） |
| E2E-T7 | **兩條路徑皆注入**：group 與 private 都含該區塊 |
| E2E-T8 | **旗標與預算不受影響**：`drive_loop_async()` 的**沙盒**語意未被本票改變（既有守衛檔仍全綠） |

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| M1 | 讓代理**不注入**該區塊（移除注入呼叫） | E2E-T1 |
| M2 | 把注入位置移到 world_context **之後** | E2E-T2 |
| M3 | 讓注入正文與讀回行**不一致**（改寫其中一方） | E2E-T3 |
| M4 | 讓組裝帶入非確定性（時間戳／dict 迭代序） | E2E-T4 |
| M5 | 在鏈路中引入網路呼叫（import httpx） | E2E-T5 |
| M6 | 讓寫入落在**生產**路徑（去掉沙盒覆寫） | E2E-T6 |
| M7 | 只讓 group 路徑注入（private 不注入） | E2E-T7 |
| M8 | 動 `drive_loop_async()` 的沙盒語意 | E2E-T8（＋既有 70 筆） |

⚠️ **M1～M8 的變異落點分散在多個檔案**（`proxy.py`／`tension_closure_loop.py`／新測試檔）⇒ harness 必須**逐檔複製**到沙盒後再變異，**repo/worktree 檔全程不寫入**；baseline 必須是**整個新 E2E 檔全綠**（8 筆）**且**既有 97 筆守衛仍全綠；收尾必印被測檔 sha256 與初始值相同。

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 2 檔**：`tests/inner_life/test_campaign_b_e2e_closed_loop.py`（新增）、`logs/ENGINEERING_STATE.md`（追加條目）。多一檔少一檔都**停止並回報**。
2. 新 E2E 檔 **8 筆全綠**，執行時間 **< 5 秒**（報告須附實測耗時）。
3. 變異 **M1～M8 全數轉紅**；既有守衛（**以當下工作樹實際存在的守衛檔為準**：Phase 1–4 的 70 ＋ P5-① 的 15；若 P5-② 守衛檔已存在則再加 12）仍全綠。
4. 全庫回歸：期望＝**當下 HEAD 的基線 ＋ 8 筆**（🔴 **基線必須在合入當下重算**，不得沿用工單寫死的數字——派工到合入之間 HEAD 可能又合入別的票；請先量當下基線再報）。引用數字必須註明解譯器是 repo 的 `.venv`。
5. `logs/ENGINEERING_STATE.md` 追加條目（例 §0.1.65）必須寫明：① **本票證明的是「鏈路在沙盒中閉環」**（含逐位元一致性與確定性）② **真實 LLM 路徑已由 P5-② 以 1 次受限呼叫驗證**（兩者互補，不可混為一談）③ **生產上旗標仍缺省 OFF ⇒ `working_state.jsonl` 仍不存在 ⇒「角色已能感知思考狀態」在生產仍不成立** ④ 守衛 8 條 ＋ 變異 M1～M8 ⑤ **0 真實 LLM 呼叫、0 網路、0 生產寫入**。

## 5. 測試
- 定向：`pytest -q tests/inner_life/test_campaign_b_e2e_closed_loop.py` ⇒ 預期 **8 passed / 0 failed**。
- 既有守衛：`pytest -q tests/inner_life/test_tension_closure_loop.py tests/inner_life/test_p5_working_state_context.py tests/inner_life/test_p5_data_plane_guard.py` ⇒ 預期 **97 passed / 0 failed**。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests` ⇒ 預期 **7324 passed / 0 failed**。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**（pytest 在零失敗時輸出裡根本沒有 "failed" 字樣）。
- 🔴 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765-8767`、**不得**對生產發真實請求。
- 🔴 變異 harness 只能改**副本**；repo 檔全程不寫入。

## 6. 不做（Out of Scope）
- **不**改 `src/**`（發現缺陷只回報）。
- **不**改既有守衛檔（只新增檔案）。
- **不**寫 `data/**`、不重啟、不部署、不打真實 LLM、不連網。
- 不 commit／push／git add（由主大腦合入時處理）。
- 不做 A/B 對照實驗；不製造 synthetic 張力事件到生產。
- 不把 E2E 做成「會改變生產行為」的測試（例如不得在測試中把旗標打開）。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/` 5 檔、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。本票**不動** `src/**` ⇒ 僅需在報告中聲明未觸碰。

## 8. 回報格式
- 2 檔範圍 ＋ numstat。
- E2E 8 筆 ＋ 既有 97 筆的**實際數字**（分子/分母/單位）＋ **E2E 實測耗時**。
- 變異 **M1～M8 逐條**（轉紅的測試名）＋ harness 的 copy 模式與 sha256 前後對帳。
- **沙盒隔離的實際做法**：你覆寫了哪些 import 期資料根常數（逐項列出）＋ 如何證明與測試順序無關。
- **逐位元證據**：prompt 內的 `[思考狀態]` 行 ＝ 讀回行（兩者都貼出）＋ 兩次組装的 prompt sha256 相同。
- **零成本證明**：MockThinker 呼叫計數 ＝ 1、無真實 provider、AST 斷言結果。
- `logs/ENGINEERING_STATE.md` 追加條目內容摘要。
- **誠實聲明**：本票證明「鏈路在沙盒閉環」；**生產資料面仍未通**（旗標缺省 OFF）。
- 未做與明文排除。
