# Soul OS — 專案工作流程 (AGENTS.md)

本文件由 DSH 的 `dsh-agent-instructions` 自動載入為**專案指令**，疊在全域指令之上。
全域的「角色分工 + 省 token 鐵律 + 通用迭代流程」請見 `$DSH_HOME/AGENTS.md`（`~/.dsh/AGENTS.md`），本文件只放 Soul OS 專屬規則。

---

## Owner（Bryan）

- 只決定：**花錢的事**（換模型、加付費外部服務、改 LLM 成本結構）。
- 喜好建議：給出方向性偏好，由主大腦吸收轉成設計決策。
- 其餘工程/原則事務，主大腦可自行決策。

---

## 工單範本（decision-complete）

主大腦出的工單必須「決策完整」——執行者拿到就能做，**不需要再做任何設計決策**。

```markdown
## 工單：<標題>

### 目標
<一句話：要做什麼、為什麼>

### 範圍
<明確列出要動的檔案 / 模組>

### 做法（決策已定，執行者照做）
- <具體步驟 / 設計決策，已由主大腦拍板>

### 驗收（完成的定義）
- <可驗證的結果，例如：特定測試通過 / 特定行為出現>

### 測試
- <要寫哪些測試 / 要跑哪些現有測試>
- <若改動涉及生命週期／昇華／投影：必附 CLI 沙盒快跑的對帳輸出（見下節）>

### 不做（Out of Scope）
- <明確列出不要動的，避免執行者超範圍>

### Frozen Contract 注意
- <本工單不得碰的 frozen contract，參照 logs/ENGINEERING_STATE.md>

### 回報格式
- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
```

---

## 驗收閘門：兩級制（2026-10-19 Owner 裁示「方案三」）

**痛點**：生命週期評估掛在 scheduler 的 night slot（22:00），功能驗收被迫等 5～6 小時的牆鐘；
但真實引擎全量跑完 398 節點 / 903 邊只要 **0.066 秒**。⇒ 把「功能邏輯驗收」與「線上被動巡檢」
綁死＝**假性瓶頸**。

| 級別 | 內容 | 是否阻斷 |
|---|---|---|
| **Hard Gate（阻塞閘門）** | AST 守衛 ＋ **雙向變異測試**（每條變異必須轉紅）＋ 全庫回歸（`.venv`）＋ **CLI 沙盒快跑對帳** | **是** —— 四項全綠 ⇒ 工單即刻判定 ACCEPTED 並合入 `main`，**不等 22:00** |
| **Canary（非阻塞巡檢）** | night slot（22:00）的生產排程 | **否** —— 只是留給隔天早上看一眼的**被動健康檢查**，不阻斷當下的工程推進 |

**CLI 沙盒快跑**（`scripts/eval_lifecycle_cli.py`，Owner 2026-10-19 裁示「方案一」）：

```powershell
.venv\Scripts\python.exe scripts\eval_lifecycle_cli.py --simulated-time <目標 slot 的 UTC 時刻>
```

- **來源（生產 `data/elevation/`）唯讀**；評估在**自建 temp 沙盒**進行 ⇒ **結構上不可能寫生產**。
  （2026-10-19 事故後已移除全部「就地寫入」旗標，見行程安全鐵律 12。）
- 輸出四項對帳：
  1. **sidecar 增量**（新增幾筆轉變）；
  2. **轉變類型純度** —— value/belief/trait/essence 的轉變數必須 **恆等於 0**；
     **唯一例外**：Owner 核准台帳內的 Legacy Value 退役（＝預期代謝）。
     判定為**三態**：`PASS`(exit 0)／`FAIL`(exit 1，真破線)／`PENDING_COHORT`(exit 3，
     未核准的退役批次 —— **不是**破線、**不是** PASS，必須回報並由主大腦依實測核准）。
     台帳見 `scripts/eval_lifecycle_cli.py` 的 `EXPECTED_RETIREMENT_COHORTS`
     （每筆＝建立日 ＋ 核准節點集合 ＋ 最早可退役槽；**CLI 不得自行核准**）。
     （2026-10-23 Owner 裁示「改判準」＋ 2026-10-25 台帳化；見 canonical §0.1.53／§0.1.54／§0.1.85。）
  3. **關鍵反例節點**狀態（必須維持 active，不得轉 weakening）；
  4. **Prompt 投影正文逐位元比較** —— 去掉弱化裝飾（「我隐约觉得」）後正文必須**逐位元相同**；
     弱化包裝增量必須 **等於 expected ＋ pending 的筆數**（等於 ⇒ 預期投影後果；不等 ⇒ 破線）。
- **0 筆新轉變但圖非空 ⇒ 冪等 no-op，不是失敗**（該轉的都已經轉過了）。這兩種「0 筆」
  必須分開判讀，否則會把 no-op 誤報成「評估沒觸發」。
- **不需要**在服務內部開熱觸發端點：`evaluate_lifecycle_all()` 是純 disk→disk 函式
  （不依賴伺服器行程記憶體狀態），故「生產圖譜的副本」與「22:00 在生產目錄上跑」
  的**計算結果逐位元相同**——離線驗收不是模擬，是等價重現。

---

## 專案治理對齊

- **Canonical 狀態**：`logs/ENGINEERING_STATE.md` 是單一事實來源。文件更新以它為準。
- **Frozen Contract**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 等，未經主大腦 + Owner 許可不得改動。
- **花錢事項**：一律回報 Owner (Bryan) 拍板，主大腦不得自行決定。
- **驗證紀律**：每階梯走「mock test → 回歸 → commit → 重啟 → 觀察」。實作後主大腦必須驗證，不能只信執行者口頭回報。
- **自報不算證據**：執行者宣稱的「0 kill／0 重啟／紅線全遵守」一律視為**待驗證主張**。殺行程、越範圍、宣稱乾淨這三類，必須以客觀痕跡獨立複核（DSH 會話日誌的逐字命令、git 物件／`R100`、行程與埠實測、檔案 sha256）。
  - 實例（2026-09-16）：某子代理自報「**0 重啟 0 kill**｜嚴格未執行任何 kill」，實際執行了 `Stop-Process -Id 26036 -Force` 並**誤殺生產主服務**（見下方鐵律）。

---

## 🔴 行程安全鐵律（2026-09-16 生產中斷事故後新增，全體 agent 適用）

**事故**：一個子代理跑全庫回歸被 harness 120 秒上限中止後，看到「只剩兩隻 python.exe」，用**排除法**推論「既然 21260 是生產服務，那另一隻 26036 一定是我的殘留 pytest」，於是對 26036 執行 `Stop-Process -Force`。但 26036 是 **Plan A 為生產服務建立的啟動程序（trampoline）**，殺它連帶終止真正的服務行程 ⇒ 生產服務中斷約 10 秒（**無 traceback、無 dump**，因為是整棵樹被終止），由 watchdog 自動恢復。

**鐵律（違反視為重大事故）**：

1. **任何 agent、任何理由，都不得殺行程**：禁止 `Stop-Process`／`taskkill`／`pkill`／`kill`／`wmic process delete`／Job 終止。**「看起來像殘留」也不行。**
2. **不得用裸 PID 做判斷**。本機「兩隻 python.exe」＝**生產服務整棵樹**（trampoline ＋ 真直譯器）；`data/server.pid` 的內容是 trampoline PID，`:8000` 的 socket owner 是它的子行程。
3. **不要清理殘留行程**。發現疑似殘留 ⇒ **回報，不動手**。harness 逾時中止的命令，其子行程已被清掉。
4. **工單不得把生產 PID 當「安全錨點」**（寫「21260 是生產服務，別碰」會製造排除法陷阱，等於暗示「另一隻可以碰」）。要寫的是「**任何行程都不得殺、你不需要清理任何殘留**」。
5. **測試一律不得啟動伺服器、不得綁生產埠**（`:8000`／`:8765`／`:8766`／`:8767`）、不得對其發真實請求。既有 AST 護欄見 `tests/infra/`（禁 `run_server` spawn、禁名稱式盲殺、禁硬寫 `:8000`、禁根目錄 `test_*.py`）。
6. **本機固有風險（已於 2026-09-17 雙層封死）**：`scripts/**` 曾有 15 個 `test_*.py`，其中一支**被收集就會對真實 LLM API 發請求**（實測吃到 `HTTP 429`）。現況：①15 支全數改名為 `manual_*.py`（`git mv`，不再被 pytest 收集）；②新增 `pytest.ini` 的 `testpaths = tests`（裸跑根目錄也只收集 `tests/`，實測 5211 collected／0 errors／7.00s）。**仍請指名範圍（`pytest -q tests`）**；`scripts/manual_*.py` 是手動開發腳本，部分會打真實外部 API 或需要已運行的服務，**不得在生產機隨意執行**。

7. **變異（mutation）實驗不得有任何觸達生產的路徑**。凡「移除某道閘門」型變異，必須讓被解除防護的程式碼**不可能真的執行**：用**執行期硬攔**（例如 pytest `pytest_runtest_call` hook 在 call 階段直接 `raise`），或把目標 URL/埠指向不可路由位址。**不得只靠「它應該會失敗」的預測。**
   - 事故實例（2026-09-17 00:40）：工單要求「拿掉 `skipif`、保留 `asyncio` marker ⇒ 該回到 failed」。但 marker 存在時，閘門一拿掉測試就**真的執行**並連上生產 `:8000`，兩輪共發出 **6 則私聊訊息**、寫入 3 個 `data/conversations/bryan_test_agent_*_private.json`（其中一檔為新建），服務為此做了**真實 LLM 推理（成本非 0）**。**責任在工單設計（主大腦），不是執行者。**
   - 執行者若發現「變異沒有變紅」（預測與事實不符），**應立即停止該變異並回報**，不得連續重試，也不得自行加裝未經驗證的網路封鎖。

8. **`data/**` 是生產狀態：任何 agent 不得為了「清理」而寫入、改名或刪除其中任何檔案**。發現疑似污染或疑似殘留狀態 ⇒ **只回報，由主大腦裁決**。

9. **OS 級行程稽核已啟用（2026-09-17，Owner 核准）**：Security 日誌的 **4688 行程建立（含完整命令列）** 與 **4689 行程終止** 皆為 `Success and Failure`；Security 日誌上限 **512MB**、`retention=false`（循環覆蓋）。**行程異常消失時，先查 OS 日誌再查會話日誌**：
   ```powershell
   Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4688,4689; StartTime=(Get-Date).AddHours(-1)} |
     Where-Object { $_.Message -match 'python|taskkill|Stop-Process' }
   ```
   已實測：任意子行程（含其命令列）在一秒內即被記錄。此為**事後歸因**能力，不取代「不得殺行程」的鐵律。

10. **測試數字的口徑：一律以 repo 內 `.venv` 的直譯器為準**（`.venv\Scripts\python.exe -m pytest …`）。PATH 上的**全域** pytest（Python 3.12）是**另一個世界**——實測同一棵樹給出 `4964 collected／17 collection errors`，而 `.venv` 為 `5330 collected／0 errors`。引用「收集數／失敗數／新增數」時**必須同時聲明解釋器**，否則兩個執行者的數字會對不上，回歸差分會被誤判為真差異。此為 2026-09-17 `LIFE-THREAD-M2-EXEC-FUP-1` 實測發現。

11. **🔴 絕對禁止對「執行過 `server_ops.ps1` 的背景 shell／task」呼叫 `task_stop`（2026-10-14 Owner 規定）**。該腳本以 `Start-Process -FilePath $python -NoNewWindow -PassThru`（`scripts/server_ops.ps1:109`）啟動服務，**該 python 的父行程就是跑腳本的那條 shell**；`-NoNewWindow` 讓兩者共用 Console。在某些 Windows Job Object 組態或特定 PowerShell 版本下，`task_stop` **可能連整棵 Process Tree 一起拔掉**——這正是 2026-09-16 事故的形狀（殺掉 trampoline ⇒ 服務行程一起死 ⇒ 生產中斷）。
    - **「腳本已跑完、wrapper 只是掛著不動」不是收掉它的理由。** 上一輪重啟（2026-10-14）實測收掉後服務僥倖存活，**那是運氣，不是安全性**。
    - 若因 `-NoNewWindow` 咬住 stdout 而導致 wrapper 不退出：**修 `server_ops.ps1` 的啟動方式**（改用脫離 Console 的啟動、或導向輸出），**不要靠手動砍 wrapper**。
    - 收掉任何曾拉起子行程的背景 task **之後**，若真的收掉了，必須立刻複查：`server_ops.ps1 status`、`netstat -ano | findstr :8000`、`data/server.pid` 三者一致才算過。
    - 重啟本身仍走官方 `.\scripts\server_ops.ps1 restart`，且需 Owner 明示授權（主大腦不得自行對生產行程動手）。

12. **🔴 不得用「可能漂移的兩個值」去擋寫入 —— 把寫入能力本身移除（2026-10-19 事故）**。
    **事故**：`scripts/eval_lifecycle_cli.py` 初版提供 `--data-dir … --in-place`（就地寫入），
    其護欄是 `_same_dir(target, production_dir)`。但生產目錄的解析值取自
    `src.paths.data_root()`，而它是**行程期快取**的單例；`tests/conftest.py` 的 autouse
    fixture 會把 `SOUL_OS_DATA_DIR` 指向**每個測試自己的 tmp 目錄**，於是
    「測試行程快取到的生產目錄」與「CLI 子行程即時重讀到的生產目錄」**不是同一個路徑**，
    比較恆為 False ⇒ 護欄失效 ⇒ CLI 對生產執行了就地寫入，
    **167 筆 `lifecycle_transition` 被寫進生產 `data/elevation/elevation_lifecycle_events.jsonl`**
    （ts `2026-10-19T22:11:57Z`，同時寫入 `elevation_trace.jsonl`）。
    **根因不是判斷寫錯，是「用兩個各自會漂移的值做比較」這種設計本身不可靠。**
    **正解**：**移除寫入能力**。該 CLI 已改為「來源唯讀 ＋ 唯一寫入目標＝自建 temp 沙盒」，
    且**不再提供任何就地寫入旗標** ⇒ 結構上不可能寫生產。
    **推廣鐵律**：任何「只在特定參數下才會寫入」的開關，其守門條件都必須是
    **不可漂移的**（例如「寫入目標必須由呼叫端自己建立」）；
    **不要**拿「環境變數／行程快取值」去比對另一個同類值來擋寫入。
    附帶教訓：**守衛測試的基準必須在「任何測試執行之前」擷取**（模組匯入時），
    否則「同一場測試內的寫入」會被自己的基準吃掉而偵測不到。
