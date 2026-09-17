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
