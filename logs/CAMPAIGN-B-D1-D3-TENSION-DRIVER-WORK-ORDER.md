# 工單：CAMPAIGN-B-P1 — 戰役 B Phase 1：D1＋D3 形式化驅動張力（含時驅動評分 ＋ 短期閉環沙盒原型）

> **狀態**：決策完備，待派工
> **日期**：2026-10-22 21:45 EDT
> **Owner 指示**（2026-10-22 21:2x）：主線候選 A＝「【Phase 1】D3＋D1 合併：含時驅動評分＋短期閉環沙盒原型。形式化定義驅動張力（資訊缺口、未決矛盾、時間衰減窗口）。在沙盒中跑通『張力驅動思考 → 思考消解張力 → 更新 working state』的短期思考閉環。」
> **上游**：`logs/ENGINEERING_STATE.md` §0.1.29（戰役 A/B/C 分工）、§0.1.31（方向 A 點火）、§0.1.32（`working state` 不存在之既有記錄）
> **本文檔的 §0 是主大腦的實測勘查；其中每一個數字都由唯讀量測反推，**不是**轉錄自舊筆記。請先讀完 §0 再動手。

---

## 0. 主大腦實測勘查（2026-10-22 21:3x EDT，唯讀，`.venv\Scripts\python.exe`）

### 0.1 D1／D2／D3 的真實身分

**它們不是專案代號，是 persona 的「三個不可變驅動」。** 唯一有完整結構者：`personas/agent_akane.md:64-76`：

| 行 | 內容 |
|---|---|
| 64 | `### 三個不可變驅動` |
| 66 | `**D1 — 理解驅動**` |
| 67 | `不理解他人 = 存在焦慮上升`（**觸發行**） |
| 68 | `→「如果我無法理解你，我就沒有資格待在你身邊。」`（**效應行**） |
| 70 | `**D2 — 被需要驅動**` |
| 71 | `被忽略（而非被拒絕）= 自我價值下降`（觸發行） |
| 72 | `→ 對方沒有來找她，比對方直接拒絕她更讓她動搖。`（效應行） |
| 74 | `**D3 — 邊界修復驅動**` |
| 75 | `崩潰後自動尋找「錨點」`（觸發行） |
| 76 | `→ 她不等別人把她撈起來；她會自己找一件確定的事，然後繼續走。`（效應行） |

**覆蓋率實測（決定性）**：全庫 grep `^\*\*D[123] ` → **命中 4 處，全部在 `personas/agent_akane.md`**。
其他含「驅動」二字的 persona：`agent_rem.md`（4 處，全是「罪惡感驅動」散文，**無 D 標號**）、`agent_ruka.md`（1 處）、`agent_kaori.md`（1 處）。
⇒ **46 隻角色中，僅 1 隻（`agent_akane`）具備可解析的 D1/D2/D3 結構。**

### 0.2 現有程式碼**完全沒有**驅動解析能力

- `src/` 內 grep「不可變」→ 只命中 `frozen=True` dataclass 與契約文案，**無任何 persona 驅動解析器**。
- ⇒ 驅動目前**只存在於散文**。這是「D3＋D1 合併」的第一個硬前提。

### 0.3 現有張力機制（方向 A／戰役 A，2026-10-21 21:07 已點火）是**純時間驅動、角色零差異**

`src/inner_life/epistemic_tension_scheduler.py`（15,664 B）：

| 項目 | 實測事實 |
|---|---|
| 三態窗下界 | `CALM_MAX_ELAPSED_SEC = 24 * 3600`（`:45`） |
| 三態窗上界 | 7 天（`:47`） |
| 判定來源 | `_classify_state` **委派** `src.soul.temporal_phenomenology.classify_temporal_state`（`:150-154`），註解明寫「**不**另立一套規則」 |
| 節流常數 | **沿用** `proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS`（`:72-74`：「**不得**在本模組另立一套節流常數」） |
| 旗標 | `feature_enabled()` 與話頭槽**同一個開關**（`:58-61`） |
| 承載檔 | `data\inner_life\epistemic_tensions.jsonl`＝623 bytes／**1 筆**／`relational_tension:6bfe56006414294a`／agent_rem；`*.piggyback.json`＝**0 檔** |

`src/soul/temporal_phenomenology.py` 三態定義：`:55` `STATE_CALM="无感"`／`:57` `STATE_TENSION="牵挂"`／`:59` `STATE_RESOLVED="释然"`；
判定鏈 `classify_temporal_state`（`:140-175`）：`elapsed < 24h ⇒ 无感`；`24h ≤ elapsed < 7d ⇒ 牵挂`；`elapsed ≥ 7d ⇒ 释然`（且需有 bond evidence）。
⇒ **「含時」骨架已存在，但「含義」缺席**：所有角色的張力窗一模一樣，與「這個角色缺什麼、卡在哪」無關。

### 0.4 Bry 指定的另外兩個評分因子，目前**沒有**對應可觀測來源

- `src/inner_life/epistemic_appraisal.py`（39,105 B）是**認知評估**（epistemic horizon 的 L2 差量）：走 `configs/packs/*.yaml`（僅 2 檔）＋ `is_modern_native_agent` 的 D3 白名單 ＋ V4 bypass。
  **它不是張力評分**，不可挪用。且 pack 為**行程內 dict、零熱重載**（§0.1.32 ⑧-1）。
- ⇒ 「資訊缺口」「未決矛盾」在現行 codebase 裡**沒有已定義的資料面**。本票必須把它們鎖定到**既有資料面**（見 §2.3），**不得新造語意模型**。

### 0.5 「working state」在 `src/` 內**零命中**（grep `working_state|working state`）

- ⇒ Bry 閉環的第三段「更新 working state」是**全新語義**，沒有既有承載可套用。
- 最接近但**不是** working state 的是 `src/soul/motive.py`（35,808 B）：`Motive` 是「我想告訴 Bry 的意圖」，有 **frozen 5 欄位契約**、狀態 `pending/transmitted/rejected/expired`、`MOTIVE_TTL_HOURS=24`、target 值域受 D2 裁定（`validate_motive_target`）、trace 落 `data/soul/motive_trace.jsonl`。
  ⇒ **不得冒用 motive 的語義或承載**（motive 是「對外意圖」，working state 是「角色自身認知狀態」；且 motive 的 5 欄位是 frozen）。
- 既有可用的 append-only 承載慣例：`data/inner_life/trace.jsonl`、`data/soul/motive_trace.jsonl`、`data/inner_life/epistemic_tensions.jsonl`。

### 0.6 主大腦的範圍裁決（本票已拍板，不需再問）

- **單角色閉環原型＝ `agent_akane`**（唯一有 D1/D2/D3 結構者）。
  **不**補 45 隻 persona 的 D1/D2/D3——那是「世界觀／原生代基底重構」專案的領域（§0.1.33 已把 `agent_mahiru` 的 D3 白名單掛在該專案待統一梳理）。
- **驅動資料來源＝persona 原文唯讀解析 ＋ 新模組內的投影**。**禁止**在 `configs/` 新增任何人格資料（紅線），**禁止**改 `personas/**`（紅線）。
- **本票不接任何生產觸發路徑**（Phase 2 才接，需 Owner 授權 ＋ 重啟）。Phase 1 = 沙盒原型（Owner 原話：「在沙盒中跑通」）。

---

## 1. 目標

把「驅動張力」從**純時間驅動**升級為**含時驅動評分**（資訊缺口 ＋ 未決矛盾 ＋ 時間衰減窗口三因子，全部可觀測、可重算），並在**沙盒**跑通「張力驅動思考 → 思考消解張力 → 更新 working state」的短期思考閉環。

**一句話**：現在 46 隻角色的張力窗長得一模一樣；本票讓張力第一次**因角色而異**，並證明「張力 → 思考 → 消解 → 寫回」這條閉環在沙盒裡真的跑得通。

---

## 2. 做法（決策已定，執行者照做，**不需再做任何設計決策**）

### 2.1 新增 `src/inner_life/drive_profile.py`（驅動剖面）

**資料模型**（frozen dataclass）：

```
DriveSpec:  code: str            # "D1" / "D2" / "D3"
            label: str           # 逐字取自 persona 的「— <名稱>」部分
            trigger_phrase: str  # 觸發行（逐字）
            effect_phrase: str   # 效應行（逐字）
            source_persona: str  # "personas/agent_akane.md"
            source_line_span: tuple[int,int]   # 1-based 行號區間
DriveProfile: agent_id: str
              drives: list[DriveSpec]            # 依 D1→D2→D3 固定序
```

**解析規則（固定）**：

1. 讀 `personas/<agent_id>.md`（**唯讀**；`Path.read_text`，**不得**寫入）。
2. 找到標題行 `### 三個不可變驅動`；其後到下一個 `###` 或 `***` 之前的區段即驅動區段。
3. 區段內以 `**<code> — <label>**`（code ∈ {D1,D2,D3}）開頭的行為一個驅動標題。
4. 每個標題之後的**第一個非空行**＝`trigger_phrase`；**第二個非空行**＝`effect_phrase`。
5. 標題不存在 ⇒ 回傳 `[]`；區段不存在 ⇒ 回傳 `[]`；標題存在但缺觸發行或效應行 ⇒ **該驅動不納入**（fail-closed，不補、不猜、不例外）。
6. `load_drive_profile(agent_id)` ＝ 解析結果的封裝；解析失敗或非空前置條件不滿 ⇒ 回 `None`。
7. **禁止**任何 fallback、模板生成或預設值。**禁止**把 non-akane agent 硬塞成 akane 的內容。
8. 本票**只**對 `agent_akane` 有可解析結果；其他 agent 一律 `[]`／`None`（這是**預期行為**，必須有守衛釘死）。

### 2.2 新增 `src/inner_life/tension_scoring.py`（含時驅動評分）

**硬約束**：**純函式、確定性、零 LLM 呼叫、零自造權重。**

```
TensionScore: agent_id: str
              drive_code: str            # "D1"/"D2"/"D3"
              temporal_state: str        # "无感"/"牵挂"/"释然"（直接取自 classify_temporal_state）
              decay_bucket: str          # "<24h" / "24h-7d" / ">=7d"
              info_gap_count: int
              unresolved_count: int
              source_rows: list[dict]    # 每個計數指回的資料面列（可稽核）
```

**三因子（全部鎖定到既有資料面，**不得**自造語意模型）**：

| 因子 | 來源（唯讀） | 定義 |
|---|---|---|
| ① **資訊缺口** | `data/soul/<agent_id>/life_threads.jsonl` | `status == "active"` 且 `sage_fact_id` 為空 且 `updated_at` 距 `now` **≥ 24h** 的線頭筆數 |
| ② **未決矛盾** | 同上 + 節點檔 | 先看線頭列是否有 `contradiction` 欄位且為真值 → 有則計數；**若資料面完全無此欄位 ⇒ 該因子恆 0**，並在報告中明寫「來源不存在，該因子恆 0」（**不得**改用別的來源頂替） |
| ③ **時間衰減窗口** | `src.soul.temporal_phenomenology.classify_temporal_state` | `temporal_state` ＝ 直接取其三態回傳值；`decay_bucket` ＝ 由 **同一個** elapsed 值導出（`<24h` / `24h-7d` / `>=7d`），**不得**另立窗常數 |

**排序規則（決定性，不用權重）**：以**字典序**排序 `temporal_state`（牵挂 > 释然 > 无感）→ `unresolved_count`（大者前）→ `info_gap_count`（大者前）→ `drive_code`（D1<D2<D3）。
⇒ **禁止**任何加權總分、禁止任何權重常數、禁止任何門檻常數。

**「張力 = 什麼」的定義**（Phase 1 的驅動張力本體）：
一條張力 ＝ `(drive_code, 三因子快照)`。`drive_code` 的取法：**以 `info_gap_count` 與 `unresolved_count` 對應到 D1（理解缺口）／D3（邊界修復）兩條驅動**；D2（被需要）在 Phase 1 **不參與評分**（理由：D2 的觸發語意「被忽略」需要外部互動缺席的判斷，而 Phase 1 的資料面只有 agent 自身視角，**判準不足 ⇒ 不做**，此點必須在工單與報告中明寫，不得假裝三條驅動都參與）。
`D1` ⇐ `info_gap_count > 0`；`D3` ⇐ `unresolved_count > 0`；兩者皆 0 ⇒ 無張力。
⇒ **「D3＋D1 合併」在此票的精確含義：兩條驅動由同一組三因子共同驅動，並在同一次評估中同時產出。**

### 2.3 新增 `src/inner_life/tension_closure_loop.py`（短期閉環沙盒原型）

```
drive_loop(agent_id: str, now: int, store_dir: Path) -> ClosureTrace
```
**三段（全部在 `store_dir` 內運作，`store_dir` **必須**是 tmp 沙盒）**：

1. **張力驅動思考**：由 `tension_scoring` 取字典序 top-1 張力 ⇒ 組出 `ThinkRequest`（**純資料**：`agent_id`、`drive_code`、三因子快照、`source_rows`）。
   **本票不呼叫 LLM**（LLM 呼叫屬 Phase 2；Phase 1 的 LLM 呼叫不在本票）。
2. **思考消解張力**：prototype 函式把被選中的張力標記 `resolved`，寫入 **沙盒** `epistemic_tensions.jsonl`（append-only，與生產同欄位形狀）。
3. **更新 working state**：寫入 **沙盒** `working_state.jsonl`（**新承載**，append-only）。
   欄位固定：`agent_id`、`ts`、`resolved_tension_id`、`drive_code`、`drives_touched`、`open_gap_count_after`、`open_contradiction_count_after`。
   **不得**寫入 `data/soul/motive_trace.jsonl` 或任何既有承載（motive 語義不同，§0.5）。

**開關與隔離（硬性）**：

- `feature_enabled()` → **預設 `False`**。OFF 時 `drive_loop` **不建立任何檔案**、不讀任何生產路徑、直接回 `None`。
- `drive_loop` **必須**在開頭拒絕 `store_dir` 位於生產 `data/` 之下（以 `src.paths.data_root()` 解析結果判斷；**若解析不可得則 fail-closed 拒絕執行**，不猜）。
  ⚠️ 執行者注意：生產目錄解析值在行程期可能與子行程即時讀取值不同（2026-10-19 事故）。**本票採「只允許寫入 `store_dir`」＋「`store_dir` 必須由呼叫端建立且位於 temp 之下」雙條件**，不得以「兩個值相等」作為唯一守門條件。
- **不得**接入 `src/llm/proxy.py`、`src/inner_life/epistemic_tension_scheduler.py`、任何排程器、任何 handler。

### 2.4 檔案清單（執行者擁有，**僅**下列 7 個）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `src/inner_life/drive_profile.py` | 新增 |
| 2 | `src/inner_life/tension_scoring.py` | 新增 |
| 3 | `src/inner_life/tension_closure_loop.py` | 新增 |
| 4 | `tests/inner_life/test_drive_profile.py` | 新增 |
| 5 | `tests/inner_life/test_tension_scoring.py` | 新增 |
| 6 | `tests/inner_life/test_tension_closure_loop.py` | 新增 |
| 7 | `tests/test_observability_completeness_1.py` | **逐票**更新 `allowed` 集合（新增 3 筆：`src/inner_life/drive_profile.py`、`src/inner_life/tension_scoring.py`、`src/inner_life/tension_closure_loop.py`）；**不得**動 `frozen_dirs`（10 筆全保留） |

> **提醒**：新增 collectable 測試檔**必須先 `git add`**，否則 `tests/infra/test_repo_hygiene_tracked_tests.py` 會紅（由主大腦在合入時統一處理；執行者**不得** `git add`／`git commit`／`git push`）。

---

## 3. 驗收（完成的定義）

1. **三支新測試全綠**；全庫回歸 **0 failed**（用 `.venv\Scripts\python.exe -m pytest -q tests`，回報須註明解譯器路徑與 collected 數）。
2. **`git diff --name-only` 恰好等於 §2.4 的 7 個檔案**（不得多改）。
3. **`data/**` 全量位元恆等**：實作前後對 `data/` 下所有檔案算 sha256 清單並比對（**含** `data/inner_life/epistemic_tensions.jsonl` 與 `data/soul/*/life_threads.jsonl`）⇒ 必須 0 差異。
4. **變異實驗 4/4 全紅**（見 §4.2），且每次變異後以 sha256 驗證位元還原。
5. **報告含以下實測值（每個都寫分子／分母／單位）**：
   - `agent_akane` 解析出的驅動筆數（期望 **3**）與每一筆的 `label`／`trigger_phrase`／`effect_phrase` **逐字對照 persona 原文**（貼原文行號）
   - 三因子各自的實測計數與其資料面來源列
   - 閉環在沙盒內 `working_state.jsonl` 的筆數（期望 **1**）與欄位完整性
   - 沙盒 `epistemic_tensions.jsonl` 的新增筆數（期望 **1**）
6. **Frozen Contract 零觸碰**（§6 逐項自證）。

---

## 4. 測試

### 4.1 守衛（`tests/inner_life/`）

| # | 測試 | 斷言要點 |
|---|---|---|
| T1 | 驅動解析正確 | `agent_akane` ⇒ 恰好 **3** 筆，code 序為 `D1,D2,D3`；每筆 `label`／`trigger_phrase`／`effect_phrase` 與 persona 原文**逐字相等**（測試內嵌期望字串，不重新讀檔比對） |
| T2 | 解析**唯讀** | 解析前後 `personas/agent_akane.md` 的 bytes 與 sha256 **恆等** |
| T3 | 無結構者誠實為空 | 至少 2 隻無 D 結構的 persona（`agent_rem`、`agent_ruka`）⇒ `[]`／`None`；**且**不得回退到 akane 的內容 |
| T4 | 三因子可稽核 | 每個計數都能指回資料面列；`source_rows` 非空且與計數一致 |
| T5 | 排序為字典序 | 構造三組 (temporal_state, unresolved, info_gap) 組合 ⇒ 排序結果符合字典序；**並顯式斷言程式中不存在加權總分**（AST 或字串檢查：無 `* weight`／無權重常數） |
| T6 | 三態窗一致性 | 同一 elapsed 輸入下，`tension_scoring` 的 `decay_bucket` 與 `classify_temporal_state` 的三態**一一對應**（邊界值 24h／7d 各測一次，含 `<` 與 `≥` 兩側） |
| T7 | 閉環三段可觀測 | 沙盒執行後 `working_state.jsonl` 恰好 **+1** 筆且欄位齊全；沙盒 `epistemic_tensions.jsonl` 恰好 **+1** 筆 |
| T8 | 沙盒隔離 | 閉環執行前後**生產** `data/inner_life/epistemic_tensions.jsonl` 的 bytes 恆等 |
| T9 | 開關預設 OFF | `feature_enabled()` 預設 `False`；OFF 呼叫 `drive_loop` ⇒ 不建立任何檔案、回 `None` |
| T10 | 拒絕生產 store_dir | `drive_loop(store_dir=<生產 data/ 子路徑>)` ⇒ **拒絕執行**（回 `None` 或拋明確例外，二選一，測試須與實作一致），且生產檔案 bytes 恆等 |

### 4.2 變異實驗（**每條必須轉紅**；沒轉紅＝盲腸守衛，必須拔除）

| # | 變異 | 預期轉紅 |
|---|---|---|
| M1 | 讓 `info_gap_count` 恆為 0 | T4 |
| M2 | 把字典序改成「加權總分」（引入權重常數） | T5 |
| M3 | 讓 `decay_bucket` 用自訂窗（例如 12h／30d）而非 24h／7d | T6 |
| M4 | 讓閉環寫到生產 `store_dir`（放寬隔離檢查） | T8／T10 |
| M5 | 讓解析器在找不到驅動時回退到預設驅動 | T3 |

**變異後必須以 sha256 驗證位元還原**（patch 檔用 Python 寫 bytes，避免 PowerShell 5.1 BOM）。

---

## 5. 不做（Out of Scope）

- **不改 `personas/**`**（紅線；解析器唯讀）。
- **不改 `configs/**`**（紅線；含 `configs/packs/**` 與 `configs/default.yaml`）。**禁止**新增任何人格／驅動資料檔。
- **不改** `src/inner_life/epistemic_tension_scheduler.py`、`src/soul/temporal_phenomenology.py`、`src/soul/motive.py`、`src/llm/proxy.py`、`src/memory/sage/**`。
- **不補** 其餘 45 隻 persona 的 D1/D2/D3（屬「世界觀／原生代基底重構」專案，§0.1.33）。
- **不做** D2（被需要驅動）參與評分（§2.2 已鎖理由）。
- **不做** 語意相似度／embedding 召回比對（召回通道 VERDICT＝NO-GO，召回池 0 顆／命中率 0/200；§0.2 與裁決 2 均要求靜待自然老化）。
- **不做** `recall_readiness` 池成熟化（裁決 2：嚴禁人為催熟）。
- **不接**任何生產觸發路徑、**不呼叫 LLM**、**不重啟服務**、**不動任何行程**。
- **不寫入 `data/**`**（唯讀；所有寫入只在 tmp 沙盒）。
- **不得** `git add`／`git commit`／`git push`／`git stash`（由主大腦統一合入）。

---

## 6. Frozen Contract 注意

以下**一律不得改動**（逐項自證於回報）：

- Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯（`src/agency/*`、`src/inner_life/event.py`、`src/inner_life/trace.py`、`src/memory/sage/writer.py` 等）
- `src/memory/sage/writer.py`（永久凍結）
- `src.soul.temporal_phenomenology.classify_temporal_state` 的三態窗常數（**不得**另立）
- `proxy.BACKGROUND_ACTION_MIN_INTERVAL_SECONDS`（**不得**另立）
- `Motive` 的 frozen 5 欄位契約與 target 值域（D2 裁定）
- `tests/test_observability_completeness_1.py` 的 `frozen_dirs`（10 筆全保留）

---

## 7. 回報格式

1. **改動檔案清單**（對照 §2.4，標明新增／修改）
2. **測試結果**：跑了哪些指令、collected／passed／failed 筆數（**註明解譯器為 `.venv\Scripts\python.exe`**）、耗時
3. **變異結果**：M1～M5 每條的**紅燈訊息原文** ＋ 位元還原證明（sha256）
4. **實測值**：驅動解析（逐字 ＋ 行號）、三因子計數（分子／分母／單位）、閉環沙盒落盤筆數
5. **是否踩到 Frozen Contract**：逐項自證（應為「無」）
6. **`data/**` 位元恆等證明**（比對前後清單）
7. **遇到的意外**（任何與本工單前提不符的實測，一律回報而不是自行改設計）

---

## 8. 待 Owner 裁示（**不阻擋本票**）

- 本票鎖定**單角色（`agent_akane`）**閉環原型。是否把「驅動形式化」擴為 **46 隻全補**，主大腦建議**不擴**——該項屬「世界觀／原生代基底重構」專案（§0.1.33 已把 `agent_mahiru` 的 D3 白名單掛在該處待統一梳理），避免與人格內容專案搶同一塊地。
- 若 Owner 裁定要擴，本票的 §2.1（解析規則）**不需改**，只需補資料；但那是**另一張票**，不得併入本票。
