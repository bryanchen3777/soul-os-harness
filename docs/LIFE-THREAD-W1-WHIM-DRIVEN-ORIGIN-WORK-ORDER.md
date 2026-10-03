# 工單：LIFE-THREAD-W1 — 接上 `whim_driven` 起源（解鎖個性供給）

> **狀態**：待 Owner 核准後執行。
> **出票**：主大腦（Lin），2026-10-03。
> **依據**：Owner 2026-10-03 裁定（問卷 `ask_e461eaf8ca96f5d05b0d487f` Q1 選 A：「兩條並進」）。
> **本票不含** `world_collision` 擴大來源（另票，見 Out of Scope）。

---

## 目標

讓 `whim_driven` 成為生產可達的起源，解除「十個靈魂只有一種生活質地」的瓶頸。

**一句話**：M4 已寫好 `whim_driven` 的 prompt 模板與種子收集器，但喚醒路徑上沒有任何呼叫者；本票在 orchestrator 層接通它，**不動 M3 閘門任何一行**。

**為什麼是它**：實測（2026-10-03，唯讀 fold）10 個 agent 共 38 條線頭，`origin_type` 為 `necessity_driven` 38/38（100%），`whim_driven` / `goal_driven` / `world_collision` 皆 0。VISION §4.3 稱 `whim_driven` 為「最貼近個性魅力」——目前個性分量為零。

---

## 範圍

**只可修改這 3 個檔案：**

| 檔案 | 改動性質 |
|---|---|
| `src/soul/life_thread_orchestrator.py` | 新增 1 個旗標分支 + 1 個每日計數閘門 |
| `docs/LIFE-THREAD-ENGINE-CONTRACT.md` | 新增 §4.5、§8.1 加一條例外、INV-3 加條件式例外 |
| `tests/soul/test_life_thread_whim_origin.py`（新檔） | 新測試 |

**只讀參照（不可改）：** `src/soul/life_thread_wake_gate.py`、`src/soul/life_thread_origins.py`、`src/soul/life_thread_bootstrap.py`、`src/soul/scheduler.py`。

---

## 做法（決策已定，執行者照做）

### 1. `life_thread_orchestrator.py` — 起源選定處加分支

現行（`:445-447`）：

```python
wake_origin_type = (
    lt_boot.BOOTSTRAP_ORIGIN_TYPE if bootstrap_mode else decision.origin_type
)
```

改為：在 `bootstrap_mode` 為假、且 `decision.should_wake` 為假的情況下，判定是否走 whim 路徑。

**設計依據**：契約 §4.4 的 bootstrap 已經建立先例——它**沒有**修改 M3 閘門的判定式或常數，只在 orchestrator 改「喚醒後用哪個起源」。本票沿用同一模式，**M3 閘門維持完全凍結**。

具體步驟：

1. **旗標**：新增模組常數 `LIFE_THREAD_WHIM_ENABLED`，讀環境變數 `LIFE_THREAD_WHIM_ENABLED`。**嚴格 fail-closed 解析**：僅當字串為非空、非 `"0"`、非 `"false"`（大小寫不敏感）時視為 ON；**缺席即 OFF**。解析失敗 → 記 warning ＋ 視為 OFF。

2. **每日計數器（全新機制）**：orchestrator **現況 0 個計數器**（已驗證：`MAX_PER_DAY` / `daily` / `per_day` 皆不存在於該檔），bootstrap 用的是一次性標記檔、不是計數器，**不可複用其結構**。本票需自建：模組級 dict `{agent_id: {"date": "YYYY-MM-DD", "count": int}}`，常數 `LIFE_THREAD_WHIM_MAX_PER_DAY = 1`（**硬編碼，不從 env 讀**，避免可被環境變數放大的成本面）。計數跨 slot 累計（morning 與 night 共用同一計數）；日期跨日自動歸零。記憶體狀態，**不落盤**（服務重啟後回到 0，屬 fail-closed 方向，可接受並在契約 §4.5 登記為已知限制）。

3. **觸發條件**（三者**全部**成立才走 whim）：
   - 旗標 ON
   - `decision.should_wake is False`（即 M3 判定當時段該安靜）
   - `soul_context` 非空（沿用 `:412` 已有的判定，**不得重複呼叫 `load_soul_context`**）

4. **起源值**：**字面量 `"whim_driven"`**，直接寫在 orchestrator 內。已驗證 `life_thread_origins.py` **並無** `WHIM_DRIVEN` / `GOAL_DRIVEN` / `NECESSITY_DRIVEN` 常數（該模組只有 `WORLD_COLLISION`、`ORIGIN_TYPES`、`_ORIGIN_BLOCKS`），故**不得 import 那些名字**，也不得為了取得常數去改 `origins`。字面量須於契約 §4.5 明文登記為 §2.3 既有四值之一。

5. **種子**：傳 `due_threads=None`（沿用現行 `:452` 的既有呼叫慣例，M4 自行做 due 過濾），`build_kwargs={"soul_context": soul_context}`（**不傳 `world_records`**——`whim_driven` 必須純內生，傳入世界記錄會污染語意，違反 `collect_whim_seeds` 的「不得觸碰 Lived Context」限制）。

6. **fail-closed**：整段包在 `try/except`，任何例外只 `logger.warning`，**不得**讓異常冒泡到 scheduler（`:1795` 已有同構保護，本票不得改動該保護）。

7. **可觀測性**：whim 路徑完成時，`summary` 加一個鍵 `"whim_wake": True`（非 whim 路徑**不得**出現此鍵，維持既有鍵集合不變）。

### 2. `docs/LIFE-THREAD-ENGINE-CONTRACT.md` — 契約同步

- **§4.5（新增）**：`whim_driven` 接線條目。逐字載明：觸發條件、旗標、成本上限、**防捏造約束**（whim 產生的線頭**不得**宣稱任何具體外部事件，語意沿用 `WHIM_NEUTRAL_ANCHOR` 的「不捏造」原則）、以及**明文聲明 M3 閘門 0 改動**。
- **§8.1**：把「本引擎 0 條 LLM 路徑」改為「兩條常態路徑 ＋ bootstrap 例外 ＋ **whim 例外（每 agent 每日 ≤1、旗標預設 OFF）**」。
- **§7 INV-3**：追加條件式例外，形式與 bootstrap 例外一致。

### 3. 測試 `tests/soul/test_life_thread_whim_origin.py`（新檔）

用 mock LLM 計數斷言，**不得**依賴真實模型：

| 測試 | 斷言 |
|---|---|
| `test_whim_flag_off_is_silent` | 旗標缺席時，whim 觸發次數 == 0，LLM 呼叫數 == 0 |
| `test_whim_flag_on_uses_whim_origin` | 旗標 ON ＋ M3 判定安靜 ＋ soul_context 非空 ⇒ 實際傳給 `run_origin_round` 的 origin 字串 == `"whim_driven"` |
| `test_whim_not_called_when_m3_wakes` | M3 `should_wake is True` 時**不得**走 whim（此時沿用 `decision.origin_type`） |
| `test_whim_respects_daily_cap` | 同一 agent 同一日呼叫兩次 slot ⇒ whim 生效恰 1 次 |
| `test_whim_empty_soul_context_blocked` | soul_context 為空 ⇒ 不呼叫，且 **0 LLM 花費** |
| `test_whim_does_not_pass_world_records` | 傳給 `run_origin_round` 的 `build_kwargs` **不含** `world_records` 鍵 |
| `test_m3_gate_untouched` | AST 斷言 `life_thread_wake_gate.py` 的公開函式簽章與本票前的基線**逐位一致** |
| `test_fail_closed_on_exception` | `run_origin_round` 拋例外時，orchestrator 記 warning 並正常返回，**不 raise** |

---

## 既有測試參照（風格與守門範本）

`tests/soul/` 已有 13 支 life-thread 測試。本票的新測試**必須沿用**其既有慣例，不得另創一套：

| 參照檔 | 長處 | 本票用途 |
|---|---|---|
| `test_life_thread_m5_wiring.py`（104KB） | 既有 orchestrator 接線測試、flag fail-closed 解析、mock LLM 計數 | 模仿旗標解析與計數斷言的寫法 |
| `test_life_thread_wake_gate_m3.py`（129KB） | M3 凍結面的 AST 守門 | 模仿「凍結面 0 改動」的斷言形式 |
| `test_life_thread_bootstrap.py`（53KB） | 一次性標記、每日上限、0 成本路徑 | 模仿 `test_whim_empty_soul_context_blocked` 與每日上限斷言 |
| `test_life_thread_origins_m4.py`（60KB） | 四起源的種子收集器測試 | 確認 `whim_driven` 既有行為，本票**不改**該檔 |

**注意**：`test_life_thread_origins_m4.py` 已測試 `collect_whim_seeds`，該函式**行為正確、只缺呼叫者**。本票**不得**修改此測試檔。

---

## 驗收（完成的定義）

1. `.venv\Scripts\python.exe -m pytest tests/soul/test_life_thread_whim_origin.py -q` 全數通過。
2. `git diff` 顯示 `life_thread_wake_gate.py` **零改動**。
3. `git diff` 顯示 `life_thread_origins.py` / `life_thread_bootstrap.py` / `scheduler.py` **零改動**。
4. 契約文件 §4.5、§8.1、INV-3 三處已同步，且 §4.5 含「M3 閘門 0 改動」明文。
5. **未重啟服務、未改任何 `data/**`、未發任何訊息**。

---

## 測試

```powershell
$env:PYTHONIOENCODING='utf-8'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
.venv\Scripts\python.exe -m pytest tests/soul/test_life_thread_whim_origin.py -q
```

跑完後**必須**附上 `git diff --stat`，作為「未動凍結面」的客觀證據。**自報「0 改動」不算證據**，以 `git diff` 輸出為準。

---

## 不做（Out of Scope）

- ❌ **不動** `life_thread_wake_gate.py`（M3 凍結面）。
- ❌ **不動** `life_thread_origins.py`、`life_thread_bootstrap.py`、`scheduler.py`。
- ❌ **不接** `goal_driven`（種子端是否真有 ACTIVE goal **尚未驗證**，另票）。
- ❌ **不碰** `world_collision`（Owner 已裁定擴大 `SOURCES_QUALIFYING`，另票；**不得**在本票順手改 `SOURCES_QUALIFYING` 或 fact text 供給）。
- ❌ **不新增**第五個 `origin_type`。
- ❌ **不新增定時器**（INV-2：0 新 `threading.Timer` / `APScheduler` / `call_later`）。
- ❌ **不新增**任何數值評分欄位（INV-5：0 `score` / `weight` / `urgency` / `priority`）。
- ❌ **不部署**：本票產出的是程式碼與契約，**是否開啟旗標屬 Owner 另行決定**。
- ❌ **不刪改** `data/**` 任何檔案（含 bootstrap 標記）。

---

## Frozen Contract 注意

| 凍結面 | 本票處置 |
|---|---|
| `life_thread_wake_gate.py` 全檔 | **0 改動**，有 AST 測試守門 |
| `WORLD_COLLISION_WINDOW_HOURS = 4` | 不動 |
| `SOURCES_QUALIFYING` | 不動（另票處理） |
| `TYPE_BASELINE_RELEVANCE` / 門檻 `0.35` | 不動 |
| `life_thread_dissolution_exec.py` | **0 改動** |
| §2.3 的四值域 | 不動（只用既有 `whim_driven`） |
| INV-2（0 新定時器） | 必須成立 |
| INV-3（安靜時 0 LLM） | 僅在旗標 ON 時有**條件式例外**，須同步寫入契約 |
| INV-5（0 數值打分） | 必須成立 |
| INV-7（留白是成功） | 驗收測試**不得**出現「線頭數 > 0」形式的 assert |

### 行程與生產安全紅線（違反視為重大事故）

1. **任何理由都不得殺行程**（`Stop-Process` / `taskkill` / `pkill` / `kill` 一律禁止），**「看起來像殘留」也不行**。
2. **不得使用裸 PID 做判斷**。
3. **不得為了清理而寫入、改名或刪除 `data/**` 任何檔案**。
4. 測試**不得**啟動伺服器、**不得**綁 `:8000` / `:8765` / `:8766` / `:8767`、**不得**對生產埠發真實請求。
5. `scripts/**` 下的 `manual_*.py` **不得**在生產機執行。
6. 引用測試數字**必須**聲明解釋器為 repo 內 `.venv\Scripts\python.exe`，不可用 PATH 上的全域 pytest。

---

## 回報格式

1. **改動檔案清單**（附 `git diff --stat` 原始輸出）
2. **測試結果**：跑了哪個命令、過幾筆、失败幾筆（**附 pytest 摘要行原文**）
3. **是否有踩到 Frozen Contract**：逐項回答上表，每項標「0 改動」並附 `git diff -- <file>` 為空的證據
4. **行程紀律自證**：本回合是否執行過任何行程終止命令（預期答案：**無**）
5. **未完成或受阻事項**：如實列出，不要以「大致完成」帶過
