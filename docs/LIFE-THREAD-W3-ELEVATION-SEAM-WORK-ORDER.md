# 工單：LIFE-THREAD-W3 — 讓生活線頭取得 InnerLifeEvent 資格（門檻二接縫）

> **狀態**：待 Owner 核准後執行。
> **出票**：主大腦（Lin），2026-10-03。
> **依據**：`docs/INNER-LIFE-DEFINITION-OF-DONE.md` 門檻二；Owner 2026-10-03 12:20 裁定「沉澱成信念需要自己的經歷，光看完就沉澱是不合理的」。
> **本票為 Gate 開通票**：只建立「線頭 → 昇華」的通路，**0 新 LLM 通道、0 定時器、0 數值評分**。

---

## 目標

讓生活線頭產生的 lived experience 取得 `InnerLifeEvent` 資格，使 `submission_gate.consume()` 能被實際行使，產出 belief／value 候選節點。

**一句話**：昇華鏈的**每一道閘門都已對生活線頭敞開**，唯一的缺口是**生活線頭從未建立 `InnerLifeEvent`**。

---

## 範圍

**只可修改這 2 個檔案：**

| 檔案 | 改動性質 |
|---|---|
| `src/soul/life_thread_orchestrator.py` | 新增 1 個「線頭成熟時建立事件」路徑（旗標預設 OFF） |
| `tests/soul/test_life_thread_elevation_seam.py`（新檔） | 新測試 |

**只讀參照（不可改）**：`src/inner_life/submission_gate.py`、`src/inner_life/elevation_adapter.py`、`src/inner_life/event.py`、`src/soul/life_thread_wake_gate.py`、`src/soul/life_thread_origins.py`、`src/soul/life_thread_dissolution_exec.py`。

---

## 調查結論（已實測驗證，非推測）

### 1. 昇華鏈的每一道閘門，對生活線頭**都已敞開**

| 閘門 | 位置 | 對生活線頭的裁決 | 證據 |
|---|---|---|---|
| producer 合法性 | `submission_gate.py:82-131` | ✅ 非 `world:*` ⇒ 合法 | 生活線頭事件將用既有 `TRIGGER_TYPE_*` 常數，**不新增 trigger_type** |
| EH-2.1 R1 垂直防火牆 | `:392-405` | ✅ 0 阻斷 | 阻斷僅適用 `world:news*`／`world:feed*`／`world:celebrity_news`（外部媒體情報）；**所有非 `world:*` trigger 放行** |
| fact 層過濾 | `:407-418` | ✅ 不會被過濾 | **實測：21 條線頭溶解事實的 `origin` 全部為 `lived_experience`**，非 `external_world` |
| 只 consume 不 elevate | `:420-431`、`:31-33` | ✅ 可 consume | `run_elevation` 內部只 `engine.consume()`；`elevate()` **永不**被呼叫（AST 紅線鎖定） |

**全域事實分佈實測**：`origin` 唯一值為 `lived_experience`（1201 筆），**0 筆 `external_world`**。

### 2. 唯一的缺口

`life_thread_orchestrator` 的 docstring 明定「**0 InnerLifeEvent、0 bus、0 Agency 觸發鏈**」（M5 契約要求）。因此生活線頭**從未建立事件**，`submission_gate.consume()` 對線頭**從未被呼叫**，`run_elevation` 對線頭**從未被行使**。

**這是唯一缺口。** 不需要解除任何圍堵、不需要改動防火牆、不需要改變任何事實的 `origin`。

### 3. Owner 裁定確立了設計前提

> **「沉澱成信念需要自己的經歷。光看完就沉澱是不合理的。」**

故生活線頭（＝靈魂自己活過的事）**有資格**沉澱；新聞（＝只是看過）**不得**沉澱。**本票的每一項設計都必須服從此原則。**

---

## 做法（決策已定，執行者照做）

### 步驟 1：旗標

新增模組常數 `LIFE_THREAD_ELEVATION_ENABLED`，讀環境變數同名者。

**解析必須直接採用 `lt_wiring.TRUTHY_VALUES`（`{"1","true","yes","on"}`）**——⚠️ **不得**使用「非空且非 0／false 即 ON」那種寫法（W1 已因此出過 fail-open 事故，見 `logs/ENGINEERING_STATE.md` §0.1.2）。**缺席即 OFF ⇒ 落地後生產行為逐位元相同。**

### 步驟 2：何時建立事件

**只在線頭「完成（`completed`）」時建立事件，且僅在該線頭已有對應的溶解事實（`sage_fact_id`）時。**

理由：`completed` 是契約 §2 的終態，代表「這件事真的發生了，而且被走完了」；此刻的 `life_thread_dissolved` 事實即為該經歷的沉澱物，正是 Owner 所言「自己的經歷」。

**排除**：`active`（還在進行中，不是經歷）、`abandoned`（被放棄，不是經歷）。

### 步驟 3：trigger_type 的選擇（**關鍵決策**）

**使用既有的 `TRIGGER_TYPE_DIARY_MORNING`／`TRIGGER_TYPE_DIARY_NIGHT`（依線頭的 `origin_type` 對應時段），不得新增常數。**

理由：① 這是**既有合法 producer**（`:83-90` 已在 `VALID_PRODUCER_TRIGGER_TYPES` 內）；② 生活線頭本來就是 morning／night 兩個時段的產物，語意相符；③ **最小改動**——不碰 `event.py`（M5.4-5.1 frozen）、不碰 `VALID_PRODUCER_TRIGGER_TYPES`。

**🚫 嚴禁**使用 `TRIGGER_TYPE_SYSTEM` 敷衍了事（語意不符，會讓後續查證難以分辨來源）。

### 步驟 4：走既有 Writer，不繞過 Gate

事件**必須**經 `InnerLifeWriter` 建立（`submission_gate.py:207-208` 明載其為「sole canonical InnerLifeEvent creator, per M5.4-5.1 frozen」），並經 `submission_gate.submit()` 進入。

**🚫 嚴禁**直接呼叫 `run_elevation`（會繞過 Gate 的六道驗證，違反契約）。**🚫 嚴禁**直接寫 `facts` 表。

### 步驟 5：fail-closed 與可觀測性

- 整段包在 `try/except`，任何例外只 `logger.warning`，**不得**讓異常冒泡到 scheduler。
- 旗標缺席或事件建立失敗 ⇒ **該線頭不產生事件**（寧可漏一次，不可重複花費，且不可產生未經驗證的事件）。
- 產出 `summary` 新鍵 `"elevation_event_created": True`（僅在真的建立時出現）。

### 步驟 6：契約同步

`docs/LIFE-THREAD-ENGINE-CONTRACT.md` 新增 §4.6，**逐字引用 Owner 裁定**「沉澱成信念需要自己的經歷，光看完就沉澱是不合理的」作為設計前提，並載明：0 新 trigger_type、0 新 LLM 通道、0 新定時器、防火牆不動。

---

## 驗收（完成的定義）

1. `.venv\Scripts\python.exe -m pytest tests/soul/test_life_thread_elevation_seam.py -q` 全數通過。
2. `git diff -- src/inner_life/` **為空**（SubmissionGate／elevation_adapter／event 全部未動）。
3. `git diff -- src/soul/life_thread_wake_gate.py` **為空**（M3 凍結）。
4. `git diff -- src/soul/life_thread_dissolution_exec.py` **為空**。
5. 契約 §4.6 存在且含 Owner 裁定逐字引文。
6. **未重啟服務、未改任何 `data/**`、未發任何訊息。**

---

## 測試

```powershell
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -m pytest tests/soul/test_life_thread_elevation_seam.py -q
.venv\Scripts\python.exe -m pytest tests/soul -q
.venv\Scripts\python.exe -m pytest tests/inner_life -q
```

**必須**附 `git diff --stat` 原始輸出作為「未動凍結面」的客觀證據。**自報不算證據。**

### 測試清單（合成資料，不依賴生產）

| 測試 | 斷言 |
|---|---|
| `test_flag_off_is_noop` | 旗標缺席 ⇒ 建立事件 0 次、consume 0 次、**0 LLM 呼叫** |
| `test_completed_thread_with_fact_creates_event` | `completed` + 有 `sage_fact_id` ⇒ 建立 1 個事件 |
| `test_active_thread_creates_nothing` | `active` 線頭 ⇒ 0 事件 |
| `test_abandoned_thread_creates_nothing` | `abandoned` 線頭 ⇒ 0 事件 |
| `test_no_new_trigger_type_constant` | 事件 trigger_type ∈ `VALID_PRODUCER_TRIGGER_TYPES`；`event.py` **0 改動**（AST） |
| `test_goes_through_submission_gate` | 呼叫點必須是 `submission_gate.submit`，**不得**直接 `run_elevation`（AST／mock 斷言） |
| `test_never_calls_elevate` | `elevate()` 呼叫次數 == 0（AST 紅線） |
| `test_failure_is_fail_silent` | `submit()` 拋例外 ⇒ 記 warning、正常返回、**不 raise** |
| `test_no_new_timer_no_new_llm_channel` | INV-2／0 新 LLM 通道（AST 掃描） |
| `test_does_not_mutate_data_dir` | 執行前後 `data/**` 檔案清單與 mtime 逐位元一致 |

**注意**：依 §0.1.3 原則，**全部測試使用合成資料與假時鐘，不得依賴真實時間流逝或生產資料累積。**

---

## 不做（Out of Scope）

- ❌ **不動** `src/inner_life/**` 任何檔案（SubmissionGate／elevation_adapter／event 全部凍結）。
- ❌ **不動** `life_thread_wake_gate.py`（M3 凍結面）。
- ❌ **不動** `life_thread_dissolution_exec.py`。
- ❌ **不新增** `trigger_type` 常數（走既有 diary 常數）。
- ❌ **不動** EH-2.1 R1 防火牆的任何一行。
- ❌ **不改變**任何事實的 `origin`（線頭事實維持 `lived_experience`）。
- ❌ **不新增**定時器（INV-2）、**不新增** LLM 通道、**不新增**數值評分欄位（INV-5）。
- ❌ **不新增** `elevate()` 呼叫點（契約明定只 consume）。
- ❌ **不部署**：是否開啟旗標屬 Owner 另行決定（涉及一次排程路徑重啟）。
- ❌ **不碰** `data/**`（本票 0 生產資料寫入；事件建立於記憶體，僅經既有 Writer 走正常路徑）。

---

## Frozen Contract 注意

| 項目 | 本票處置 |
|---|---|
| `src/inner_life/event.py`（M5.4-5.1 frozen） | **0 改動** |
| `src/inner_life/submission_gate.py` | **0 改動** |
| `src/inner_life/elevation_adapter.py` | **0 改動** |
| `VALID_PRODUCER_TRIGGER_TYPES`（8+world） | **0 改動**（沿用既有 diary 常數） |
| `EH-2.1 R1` 垂直防火牆 | **0 改動** |
| `life_thread_wake_gate.py`（M3 凍結） | **0 改動** |
| `life_thread_dissolution_exec.py` | **0 改動** |
| `elevate()` 零呼叫 | 必須維持 |
| 21 條既有事實的 `origin = lived_experience` | **不得變更** |
| INV-2（0 新定時器）／INV-5（0 數值打分） | 必須成立 |

### 行程與生產安全紅線（違反視為重大事故）

1. **任何理由都不得殺行程**（`Stop-Process`／`taskkill`／`pkill`／`kill` 一律禁止），**「看起來像殘留」也不行**。發現疑似殘留 → 回報，不動手。
2. **不得使用裸 PID 做判斷。**
3. **不得為了清理而寫入、改名或刪除 `data/**` 任何檔案。**
4. **不得重啟服務**（本票 0 重啟）。
5. 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`、**不得**對生產埠發真實請求。
6. `scripts/**` 下的 `manual_*.py` **不得**執行。
7. **不得**用 `git add -A`、**不得** commit、**不得** push。改動留在工作區。
8. 引用測試數字**必須**用 `.venv\Scripts\python.exe`。
9. **不得**用 `git checkout --` 或 `git show rev:path > file` 還原（`core.autocrlf=true` 會寫出 CRLF）。

### Windows 環境陷阱

- 跑任何 Python 前設 `$env:PYTHONIOENCODING='utf-8'` 與 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`（cp950 會 `UnicodeEncodeError`）。
- 多行 Python 一律寫成 `.py` 檔再執行，**不可**用 `python -c "<多行>"`。

---

## 回報格式

1. **改動檔案清單** + `git diff --stat` 原始輸出
2. **測試結果**：命令、exit code、過/失敗數，附 pytest 摘要行原文
3. **Frozen Contract 逐項回答**：上表每項標「0 改動」並附 `git diff -- <file>` 為空的證據
4. **行程紀律自證**：本回合是否執行過任何行程終止命令（預期：無）
5. **未完成或受阻事項**：如實列出

**誠實要求**：若發現「線頭建立事件」在設計上與某項契約衝突，**停下來回報**，不要自行繞過。執行者宣稱的「0 kill／0 重啟／紅線全遵守」一律是待驗證主張。
