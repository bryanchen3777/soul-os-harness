# 工單：LIFE-THREAD-W2 — 修感知評分結構（解除 `accepted` 恆 False）

> **狀態**：待 Owner 核准後執行。
> **出票**：主大腦（Lin），2026-10-03。
> **依據**：Owner 2026-10-03 裁定（問卷 `ask_e461eaf8ca96f5d05b0d487f` 選項 **3**：「修評分結構本身——讓 `personal_significance`／`relevance` 真的能變動，而不是恆 0.20／0.05」）。
> **本票修的是**：`docs/INNER-LIFE-DEFINITION-OF-DONE.md` 門檻一的第三條腿（`world_collision` 起源）。
> **本票不含**：M3 閘門、線頭引擎、`goal_driven`（已確認不應工程修復）。

---

## 目標

讓世界感知在** Bry 不在場**時仍能看見「真的值得看」的世界事件，使 `accepted` 不再恆為 False，`world_collision` 起源恢復可達。

**為什麼是現在**：實測 `data/world/perception_trace.jsonl` 中 `accepted == True` 自 2026-09-21 起**恆為 0**（09-21 前 1,776 列）。因此 `world_collision` 無種子 → 門檻一的三分之一不可達 → 門檻三（連續性）也不可能成立。

---

## 範圍

**只可修改這 2 個檔案：**

| 檔案 | 改動性質 |
|---|---|
| `src/world/perception.py` | `TYPE_BASELINE_RELEVANCE` 表新增條目（純資料） |
| `tests/world/test_perception_scoring_relevance.py`（新檔） | 新測試 |

**只讀參照（不可改）**：`src/world/middleware.py`、`src/world/state.py`、`src/soul/**`、`docs/**`。

---

## 調查結論（已實測驗證，非推測）

### 1. 根因：四維度的「地板分」結構性低於門檻

`final = 0.30·relevance + 0.20·novelty + 0.25·personal_significance + 0.10·emotional_significance + 0.15·temporal_significance + min(1.0, priority_boost·0.05)`

權重來源 `src/world/perception.py:203-210`（`SCORE_WEIGHTS`），additive priority 見 `:212-216`（`PRIORITY_BOOST_WEIGHT = 0.05`）。門檻 `DEFAULT_ACCEPT_THRESHOLD = 0.35`（`:578`）。

**已用獨立算式重現生產 `reason` 字串，四捨五入至小數第 4 位完全一致**：

| 情境 | rel | nov | per | emo | tmp | pri | 實算 final | 生產 reason 字串 |
|---|---|---|---|---|---|---|---|---|
| news_event | 0.10 | 1.00 | 0.20 | 0.20 | 0.30 | 0.00 | **0.3450** | `final_score=0.35 < threshold=0.35` ✓ |
| weather | 0.05 | 1.00 | 0.20 | 0.20 | 0.30 | 0.00 | **0.3300** | `final_score=0.33 < threshold=0.35` ✓ |
| weather 重複 | 0.05 | 0.50 | 0.20 | 0.20 | 0.30 | 0.00 | **0.2300** | `final_score=0.23 < threshold=0.35` ✓ |

### 2. 真正的病灶不是門檻太高，是 `relevance` 權重（0.30）乘上一個極低的 baseline

`relevance` 是**權重最大**的維度（0.30），但：

- `news_event` **不在** `TYPE_BASELINE_RELEVANCE` 表內（`perception.py:365-377`）⇒ 落到 `DEFAULT_TYPE_BASELINE_RELEVANCE = 0.10`（`:378`）。
- `weather_temp_change` = 0.05（Bry 2026-08-07 拍板「minor temperature fluctuation」應被 reject）。
- 這兩個值**合計貢獻僅 0.03 / 0.015**，即使 `novelty` 滿分 1.0 也救不回來。

**關鍵量化**：若 `relevance` 能達到 `calendar_event` 的 baseline（0.30），`final` 即為 **0.4050 ≥ 0.35 ⇒ 通過**。這是門檻的**唯一結構性槓桿點**。

### 3. 為什麼 `personal_significance` 恆 0.20

`perception.py:515-524`：`sig_overlap > 0.5` → 0.8；`> 0` → 0.5；否則 0.2。`sig_overlap` 來自 `current_user_context_keywords` 與 event summary 的 CJK 2-gram 重疊。實測全部恆 0.20 ⇒ **生產環境 `user_keywords` 為空**（Bry 不在場時，沒有 user context 進場）。

**已驗算**：若 `per` 升至 0.5（僅需 sig_overlap > 0），news 的 final 即為 **0.4200 ≥ 0.35 ⇒ 通過**。

### 4. 為什麼 `emotional_significance` 恆 0.20

`perception.py:535-540`：`vulnerability_window` → 0.6；`anticipatory_flavor in ("longing","anxious")` → 0.5；否則 0.2。實測恆 0.20 ⇒ 這些情感狀態訊號在該期間皆未觸發。

**已驗算**：若 `emo` 為 0.6，news final = **0.3850 ⇒ 通過**；若 `tmp` 為 0.5（`temporal_salience="medium"`），final = **0.3750 ⇒ 通過**。

### 5. 與既有裁定的邊界（必須尊重）

- **2026-08-07 Bry 拍板**：`weather_temp_change` = 0.05 且註解明寫「對齊 brief §6 Test B (celebrity) + Test D (temp) 應該被 reject」⇒ **本票不得調高 `weather_temp_change`**，否則撤銷該拍板。
- **2026-09-14 Bry 裁定**：「開窗發現天氣變涼」定性為不值得傳訊的日常小事 ⇒ **本票不得讓純天氣波動變得可見**。
- `calendar_event` / `user_going_outside` 的 baseline 0.30 與 `per ≥ 0.7` boost **不得變動**（那是 2026-08-07 20:02 明確拍板的「user 相關事件給 type-based boost」）。

---

## 做法（決策已定，執行者照做）

### 步驟 1：`news_event` 納入 baseline 表

在 `TYPE_BASELINE_RELEVANCE`（`perception.py:365-377`）**新增一條**：

```python
"news_event": 0.30,
```

**理由**：`news_event` 是**生產中實際存在**的事件類型（實測 09-21 起 534 次評估），但它**從未被列入 baseline 表**，只能吃未知型別的 `DEFAULT_TYPE_BASELINE_RELEVANCE = 0.10`。這是**表遺漏**，不是刻意低評。0.30 對齊 `calendar_event`／`user_going_outside` 的既有量級（皆為「與 Bry 的生活直接相關」之事件）。

**其餘條目一律不動**（含 `weather_temp_change = 0.05`、`celebrity_news = 0.05`）。

### 步驟 2：`DEFAULT_TYPE_BASELINE_RELEVANCE` 不動

維持 0.10。**不得**為了讓事件可見而拉高預設值——那會影響所有未列舉型別。

### 步驟 3：門檻不動

`DEFAULT_ACCEPT_THRESHOLD = 0.35` **不動**。**本票的設計前提就是「不動門檻」**（這是方案 3 與方案 2 的根本差異）。

### 步驟 4：僅在 `compute_scores` 補一行防禦性註解

在 `perception.py:500` 的 `baseline = TYPE_BASELINE_RELEVANCE.get(...)` 上方，加一行註解說明 `news_event` 已於 W2 納入表中、其 0.30 對齊 `calendar_event` 量級、以及 Bry 2026-08-07 的 `weather_temp_change = 0.05` 刻意不動。**純註解，0 可執行邏輯變動**。

### 步驟 5：測試

`tests/world/test_perception_scoring_relevance.py`（新檔），使用既有 `WorldEvent` 與 `compute_scores`，全部斷言**具體數值**：

| 測試 | 斷言 |
|---|---|
| `test_news_event_baseline_is_030` | `TYPE_BASELINE_RELEVANCE["news_event"] == 0.30` |
| `test_news_event_reaches_threshold` | `compute_scores(news_event, novelty_count=1)` 的 `final()` ≥ 0.35，且 `should_accept` 回 `(True, ...)` |
| `test_news_event_final_is_0405` | `final()` ≈ 0.4050（容差 1e-6）— 釘死加權算式 |
| `test_weather_temp_change_still_rejected` | `weather_temp_change` 的 `final()` ≈ 0.3300 **且** `should_accept` 為 `False` — **保護 2026-08-07 拍板** |
| `test_celebrity_news_still_rejected` | `celebrity_news` 仍被拒 — 同上保護 |
| `test_threshold_unchanged` | `DEFAULT_ACCEPT_THRESHOLD == 0.35` |
| `test_calendar_and_outside_baselines_unchanged` | `calendar_event` 與 `user_going_outside` 仍為 0.30 |
| `test_weather_with_context_can_pass` | 當 `vulnerability_window=True`（emo 0.6），`weather_temp_change` 可通過 — 證明「天氣不是永遠不可見，只是不該僅憑溫度波動可見」 |

---

## 驗收（完成的定義）

1. `.venv\Scripts\python.exe -m pytest tests/world/test_perception_scoring_relevance.py -q` 全數通過。
2. `git diff -- src/world/perception.py` 的**可執行行變動恰為 1 行**（新增 `"news_event": 0.30,`），其餘為註解。
3. `git diff -- src/world/middleware.py` **為空**。
4. `git diff -- src/soul/` **為空**。
5. `grep -n "DEFAULT_ACCEPT_THRESHOLD" src/world/perception.py` 仍為 `0.35`。
6. **未重啟服務、未改任何 `data/**`、未發任何訊息。**

---

## 測試

```powershell
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -m pytest tests/world/test_perception_scoring_relevance.py -q
.venv\Scripts\python.exe -m pytest tests/world tests/test_world_perception.py -q
```

第二條是回歸（若路徑不存在，請改用 `pytest -q tests -k perception`）。**必須**附 `git diff --stat` 原始輸出作為「未動其他檔案」的客觀證據。**自報不算證據。**

---

## 不做（Out of Scope）

- ❌ **不動門檻**（`DEFAULT_ACCEPT_THRESHOLD`）。
- ❌ **不動** `weather_temp_change = 0.05` 與 `celebrity_news = 0.05`（撤銷 Bry 2026-08-07 拍板）。
- ❌ **不動** `calendar_event` / `user_going_outside` 的 baseline 與 boost。
- ❌ **不動** `SCORE_WEIGHTS` 任何權重（那會是重新縮放全系統評分）。
- ❌ **不動** `middleware.py` 的 `compute_scores` 呼叫端（`user_keywords` 補供給是**另一張票**，需先確認該訊號在 Bry 不在場時的語意）。
- ❌ **不新增** LLM 評分（維持 deterministic）。
- ❌ **不動** `src/soul/**`（含 M3 閘門）。
- ❌ **不部署**：本票產出程式碼與測試，**是否讓新程式碼進入生產需 Owner 另行授權**（涉及一次排程路徑重啟）。

---

## Frozen Contract 注意

| 項目 | 本票處置 |
|---|---|
| `src/soul/life_thread_wake_gate.py`（M3 凍結） | **0 改動** |
| `DEFAULT_ACCEPT_THRESHOLD = 0.35` | **0 改動** |
| `SCORE_WEIGHTS` 五維權重 | **0 改動** |
| `PRIORITY_BOOST_WEIGHT = 0.05` | **0 改動** |
| `weather_temp_change` / `celebrity_news` baseline | **0 改動**（Bry 2026-08-07 拍板） |
| `calendar_event` / `user_going_outside` baseline 與 boost | **0 改動**（Bry 2026-08-07 20:02 拍板） |
| 2026-09-14 「天氣小事不值得傳訊」裁定 | **不得**因本票使純天氣波動可見（由 `test_weather_temp_change_still_rejected` 守門） |

### 行程與生產安全紅線（違反視為重大事故）

1. **任何理由都不得殺行程**（`Stop-Process` / `taskkill` / `pkill` / `kill` 一律禁止），**「看起來像殘留」也不行**。發現疑似殘留 → 回報，不動手。
2. **不得使用裸 PID 做判斷。**
3. **不得為了清理而寫入、改名或刪除 `data/**` 任何檔案。**
4. 測試**不得**啟動伺服器、**不得**綁 `:8000` / `:8765` / `:8766` / `:8767`、**不得**對生產埠發真實請求。
5. `scripts/**` 下的 `manual_*.py` **不得**執行。
6. **不得**重啟任何服務（本票 0 重啟）。
7. **不得**用 `git add -A`、**不得** commit、**不得** push。改動留在工作區。
8. 引用測試數字**必須**用 `.venv\Scripts\python.exe`（不可用 PATH 上的全域 pytest）。

### Windows 環境陷阱

- 跑任何 Python 前設 `$env:PYTHONIOENCODING='utf-8'` 與 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`（cp950 會 `UnicodeEncodeError`）。
- 多行 Python 一律寫成 `.py` 檔再執行，**不可**用 `python -c "<多行>"`。
- **不得**用 `git checkout --` 或 `git show rev:path > file` 還原（`core.autocrlf=true` 會寫出 CRLF）。

---

## 回報格式

1. **改動檔案清單** + `git diff --stat` 原始輸出
2. **測試結果**：命令、exit code、過/失敗數，附 pytest 摘要行原文
3. **Frozen Contract 逐項回答**：上表每項標「0 改動」並附 `git diff -- <file>` 為空的證據
4. **行程紀律自證**：本回合是否執行過任何行程終止命令（預期：無）
5. **未完成或受阻事項**：如實列出

---

## 附錄：本票**沒有**解決的問題（誠實登記）

本票只處理 `relevance` 這一維。實測顯示 `personal_significance`（恆 0.20）、`emotional_significance`（恆 0.20）、`temporal_significance`（恆 0.30）在 Bry 不在場時**都**停在下限，因為其依賴的 context 訊號（`user_keywords` / `vulnerability_window` / `temporal_salience`）皆未進場。

**這些訊號為何不進場，屬於另一個問題**（是否應在 Bry 不在場時提供 per-agent 的情感狀態作為背景），本票**不回答**。若本票生效後 `news_event` 仍不足 4 小時內產生線頭（因時間窗與 slot 交錯的機率問題），下一張票應處理該問題，而非再動評分。
