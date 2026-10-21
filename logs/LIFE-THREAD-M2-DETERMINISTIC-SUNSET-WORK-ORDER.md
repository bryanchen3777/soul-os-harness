# 工單：LIFE-THREAD-M2-DETERMINISTIC-SUNSET — M2 確定性超期歸檔（解除門檻三的容量死鎖）

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 00:45 EDT
> **Owner 裁決**：Bryan 2026-10-21 00:23 —— 「採納方案 A：激活 M2 的確定性超期歸檔（Deterministic Sunset / Soft-Archive）機制。當線頭超過最大生命週期上限（如 14 天或 21 天），且經過至少 3 次檢查點無新進展時，允許 M2 狀態機將其標記為 completed 或 archived，自動釋放容量位元，為新線頭騰出空間。」**拒絕方案 B**（無腦調大 cap 或暴力開一關一）。
> **前置**：`9465bc0`（§0.1.25）／`0319c24`（§0.1.26 三戰役）／`e56a1bb`（SIM-ACCEL-RECALL-30D 工單）／SIM-TIME-001（`tests/soul/test_life_thread_sim_time.py`）
> **本文檔的 §0 是主大腦的實測勘查；其中有三處對 Owner 裁決原文的技術性修正，請先讀完 §0 再動手。**

---

## 0. 主大腦的前提勘查（2026-10-21 00:30~00:45 EDT，**已實測**）

### 0.1 🔴 裁決原文的「archived」狀態不存在 ⇒ 目標狀態必須是 `completed`

`src/soul/life_thread_dissolution.py:71` 的值域是 M1 的 `STATUS_VALUES`（**逐字相同**）：
```python
STATUS_VALUES: Tuple[str, ...] = ("active", "dormant", "completed", "abandoned")
TERMINAL_STATUSES: Tuple[str, ...] = ("completed", "abandoned")
```
**沒有 `archived`。** 新增一個狀態值＝改 M1 的值域 ＋ 契約 §2.2 ＋ 所有消費端（fold／過濾／投影）⇒ 遠超本票範圍。
**且 `dormant` 絕對不可用**：同檔註解 **INV-1** 明定「`dormant` **不催**（不計入閘門的到期檢驗點）⇒ 把 `active` 轉 `dormant` ＝**靜默停掉該線頭未來的檢驗點**，屬生產存活性政策」，故 `allow_soft_archive` 預設 `False`（第 15~18 行），**只有嚴格 `is True` 才是 opt-in**，其餘一律走諮詢訊號（fail-safe：往「不變更」方向錯）。
⇒ **本票裁決：sunset 目標狀態 = `completed`；`archived` 不採用（不存在）；`dormant` 不採用（INV-1 禁止）。**

### 0.2 🔴 裁決原文的「至少 3 次檢查點無新進展」—— 若照字面實作，**永遠不會觸發**

兩個最自然的「無進展」訊號，實測都恆不成立：

| 候選訊號 | 生產實測 | 結論 |
|---|---|---|
| 線頭在 N 槽內沒有被推進（`idle`） | 20 顆 active 線頭的 `idle` **全部 ≤ 1.11 天**（代理**每槽**都在寫入它們；15 顆的 `updated_at` ＝ 2026-10-21T02:00Z 那一槽，3 顆 ＝ 2026-10-20T12:00Z） | **0/20 會成立** |
| 線頭被推進但正文未變（無實質進展） | 588 筆 `updated` 事件中，**588 筆（100%）都改變了 `narrative_content`**，0 筆無變動 | **0/588 會成立** |

⇒ **照字面實作＝死機制。** 本票必須改用**第三個**可觀測訊號，且該訊號必須在生產現況下**會**對停滯組觸發、**不會**對健康組觸發（§3.2 的判別條件）。

### 0.3 ✅ 決定採用的訊號：**「該 agent 連續 ≥3 個槽沒有建立任何新線頭」**（agent 層級的「檢查點無新進展」）

語義：代理的檢查點循環若連續 3 次都沒有產出**新的**線頭，代表生命循環沒有向前 ⇒ 這才是「無新進展」的真實語義（線頭層面的「無進展」已被證偽）。

**生產實測（10 隻 agent，逐隻）**：

| agent | active/cap | 最後新建線頭 | 距今 | 最舊 active age | sunset 會觸發？ |
|---|---|---|---|---|---|
| yua | 2/2 | 2026-09-18T02:00:30Z | 33.11 天 | 33.11 天 | **是** |
| ruka | 2/2 | 2026-10-12T12:00:22Z | 8.69 天 | 33.11 天 | **是** |
| akane | 2/2 | 2026-10-17T12:00:39Z | 3.69 天 | 11.69 天 | 否（age 未過） |
| rem | 2/2 | 2026-09-28T02:01:11Z | 23.11 天 | 33.11 天 | **是** |
| ram | 2/2 | 2026-10-15T12:00:42Z | 5.69 天 | 33.11 天 | **是** |
| mahiru | 2/2 | 2026-10-15T12:00:50Z | 5.69 天 | 7.69 天 | 否（age 未過） |
| anna | 2/2 | 2026-09-30T02:01:17Z | 21.11 天 | 33.11 天 | 否（age 未過 21） |
| mai | 2/2 | 2026-10-05T12:01:39Z | 15.69 天 | 19.69 天 | 否 |
| miku | 2/2 | 2026-10-15T12:01:08Z | 5.69 天 | 33.11 天 | **是** |
| aoi | 2/2 | 2026-09-24T02:02:05Z | 27.11 天 | 33.11 天 | **是** |

- **10/10** 代理人滿足「≥3 槽無新建線頭」（最近的一隻 akane 也有約 7 槽）。
- **7/10** 代理人同時滿足「最舊 active age > 21 天」⇒ **sunset 在生產現況下會觸發 7 隻**。
- **3 隻不會觸發**（akane／mahiru／anna／mai 的最舊線頭尚年輕）⇒ **機制有辨識力，不會無差別收割**。

### 0.4 ✅ 關鍵架構洞察：**M2 的純決策層零改動**

`life_thread_dissolution.py` **已經**支援本機制所需的行為：
- 步驟 3：`checkpoints_exhausted is True` ⇒ `target_status = completed`、`should_mutate = True`、`reason = CHECKPOINTS_EXHAUSTED`、並建 `reflection_prompt`（**確定性模板，非 LLM**）。
- 唯一缺口是：**`checkpoints_exhausted` 由呼叫端顯式注入**（預設 `False`），而 M1 fold 的 13 個欄位沒有檢驗點清單 ⇒ 模組註文明文「此事**無法從資料推斷**，故不得猜測」。

⇒ **本票的改動落點是「接線層如何確定性地算出這個旗標」，不是「改 M2 的狀態機」。**
⇒ 好處：M2 仍是 0 I/O、0 `src.*` import 的純函式模組；`reflection_prompt` 仍是確定性模板；**sunset 走完整溶解路徑（reflection ＋ fact ＋ `status=completed`），不是靜默改狀態** ⇒ 蔡戈尼記憶效應（張力被釋放、留下沉澱）完整保留。

### 0.5 槽的定義（08:00 / 22:00 local，**間隔不均**：14h / 10h）

排程：`src/timezone_utils.py::LOCAL_TZ = ZoneInfo("America/New_York")`、`DEFAULT_MORNING_TIME = 08:00`、`DEFAULT_NIGHT_TIME = 22:00`。
⇒ **不可用「天數 × 2」估算槽數**（一天兩個槽但間隔不均）。本票指定用**槽索引**（§3.1 步驟 1）。

---

## 1. 目標

1. 實作 **M2 確定性超期歸檔（Deterministic Sunset）**：當 §3.1 的複合條件成立時，讓**最舊**的 active 線頭**走完整溶解路徑**轉為 `completed`，**自動釋放容量位元**。
2. **在虛擬沙盒**驗證收束性：7 天 × 14 slot（0 真實等待、0 真實 LLM）下，**門檻三 streak 計數器於第 7 天精確轉綠**，且**沒有任何線頭在未滿足複合條件時被 sunset**。
3. 生產側**不催熟**：機制只對「已滿足全部條件」的線頭生效；**不得**為了讓門檻三轉綠而縮短排程間隔或調整喚醒週期。

---

## 2. 範圍（你擁有這些檔案的寫入權；其他檔案一律不得動）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `src/soul/life_thread_sunset.py`（**新增**） | 純函式模組：槽索引、sunset 條件判定、sunset 目標挑選 |
| 2 | `src/soul/life_thread_orchestrator.py`（**最小改動**） | M2 接線層：算出條件 → 注入 `checkpoints_exhausted=True` |
| 3 | `docs/LIFE-THREAD-ENGINE-CONTRACT.md`（**附加式**） | §3.1 加一條 sunset 複合條件條款（§2.2 值域**不動**，仍無 `archived`） |
| 4 | `tests/soul/test_life_thread_m2_sunset.py`（**新增**） | 守衛測試 ＋ 變異實驗 |
| 5 | `tests/soul/test_sim_accel_streak_7d_sunset.py`（**新增**） | 沙盒收束性驗證（沿用 SIM-TIME-001 平面） |
| 6 | `logs/LIFE-THREAD-M2-DETERMINISTIC-SUNSET-IMPLEMENTATION.md`（**新增**） | 實作紀錄（實測數字、變異證據、收束性對帳） |

**不得動**：`src/soul/life_thread_dissolution.py`（**純決策層零改動**，見 §0.4）、`src/soul/life_threads.py`（M1 **零改動**：不需要新欄位、新事件型別）、`src/soul/life_thread_dissolution_exec.py`、`src/soul/life_thread_origins.py`、`src/paths.py`、`src/inner_life/**`、`data/**`、`personas/**`、`configs/**`、既有 SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1 測試檔。

---

## 3. 做法（決策已定，執行者照做）

### 3.1 新增 `src/soul/life_thread_sunset.py`（純函式，0 I/O、0 `src.*` import）

**常數（全部具名，不得寫裸字面量）**
```python
SUNSET_MAX_LIFETIME_DAYS = 21        # Owner 給的 14 或 21 區間取 21
SUNSET_MIN_NO_NEW_THREAD_SLOTS = 3    # Owner 原文「至少 3 次檢查點無新進展」
```
> 為什麼取 21 而不是 14：生產 13/20 顆 active 線頭 age > 14，若取 14 會對大量「仍年輕但被寫入」的線頭觸發（§3.2 的判別條件 B 會失敗）。21 則 7/10 觸發、且全部落在 21~33 天的明顯過期組。

**純函式 1：槽索引**
```python
def slot_index(ts: datetime, *, tz, morning_hour: int = 8, night_hour: int = 22) -> int:
    """該時間點所屬槽的全域序號（morning=偶數槽、night=奇數槽）。"""
```
- 以**本地時區** `tz` 計算：`day_ordinal(t) * 2 + (0 if t.hour < 12 else 1)`。
  - morning 槽（08:00）→ 該日第 0 槽；night 槽（22:00）→ 該日第 1 槽。
  - `day_ordinal` ＝ 該日期相對於任一固定基準的連續天數（用 `date.toordinal()` 即可，**只依日期、不受時區偏移影響**）。
- 邊界語意（**必須寫成會紅的測試**）：`08:00:00` 整點本身**屬於** morning 槽；`08:00:01` 也屬於 morning 槽（當日第 0 槽）；`21:59:59` 仍是 morning 槽；`22:00:00` 起進 night 槽。
- 理由：一天兩槽但間隔 14h／10h，**不可用「天數 × 2」或「小時 / 12」估算**（§0.5）。

**純函式 2：距上次新建線頭的槽數**
```python
def slots_since_last_creation(agent_events, *, now, tz, morning_hour=8, night_hour=22) -> int:
    """該 agent 自「最後一次建立新線頭」以來經過的槽數；從未建立過則回傳 +inf（或一個極大值）。"""
```
- 輸入為該 agent 的 M1 事件流（**已 fold 的 thread 集合亦可**，自行對齊；`created_at` 最小值那條即最早建立，**要的是「最後一次建立」＝ `created_at` 最大值**）。
- 槽數 ＝ `slot_index(now) − slot_index(last_created_at)`，**下限 0**。

**純函式 3：sunset 條件**
```python
def should_sunset(thread, *, now, agent_active_count: int, agent_cap: int,
                  slots_since_new_thread: int) -> bool:
    """複合條件，四項全部成立才 True。"""
```
條件（四項**全部**成立）：
1. `age_days = (now − created_at) / 86400 > SUNSET_MAX_LIFETIME_DAYS`（**嚴格大於**；`== 21` 不算）
2. `agent_active_count >= agent_cap`（**容量壓力**；沒有壓力就沒有釋放的必要）
3. `slots_since_new_thread >= SUNSET_MIN_NO_NEW_THREAD_SLOTS`（**連續 3 槽無新線頭** ＝「檢查點無新進展」）
4. 該線頭是該 agent 的**最舊** active 線頭（`created_at` 最小者；同值時以 `thread_id` 字典序取最小，**確保決定性**）

**純函式 4：sunset 目標挑選**
```python
def pick_sunset_target(threads, *, now, agent_active_count, agent_cap, slots_since_new_thread) -> Optional[dict]:
    """回傳應被 sunset 的那一條（或 None）。只挑一條（每次評估最多釋放一個位元）。"""
```
- 在條件 1~3 成立時，回傳 `created_at` 最小的那條 active 線頭（`thread_id` 字典序 tiebreak）。
- **每次評估最多一條**（避免一次釋放多個位元、破壞蔡戈尼效應的精緻感）。

### 3.2 接線層（`src/soul/life_thread_orchestrator.py`，**最小改動**）

- 在**既有的 M2 評估呼叫點**（既有 `evaluate_thread_dissolution(...)` 呼叫處）加入：
  ```python
  checkpoints_exhausted = sunset_mod.should_sunset_for_agent(...)   # 見下
  ```
  再把 `checkpoints_exhausted=checkpoints_exhausted` 傳入 `evaluate_thread_dissolution(...)`。
- **一次評估只允許一條**被 sunset（用 §3.1 純函式 4 的結果）。
- **必須走既有溶解路徑**：sunset 觸發的線頭要與其他終態線頭**走同一條** `dissolve_hook`（`life_thread_dissolution_exec`）⇒ 產生 reflection ＋ fact ＋ `status=completed`。
  🔴 **不得**直接改 `status` 而跳過溶解（那會靜默丟失張力釋放與沉澱，違反蔡戈尼記憶效應）。
- **旗標語意**：`checkpoints_exhausted` 目前是「呼叫端宣告檢驗點耗盡」的注入點；sunset 讓它**從 false 變成可被確定性算出**。**M2 純決策層不動**（§0.4）。
- **不得**改動既有旗標的預設值（`LIFE_THREAD_CONSOLIDATION_ENABLED` 等）**不得**新增旗標來開關 sunset（sunset 是預設啟用的確定性機制；若需要可停用的開關，另開票由 Owner 裁定）。

### 3.3 契約（`docs/LIFE-THREAD-ENGINE-CONTRACT.md`）

- **§2.2 值域不動**（仍為 `active / dormant / completed / abandoned`，**無 `archived`**）。
- **§3.1 加一條**：sunset 複合條件（四項全成立）＋ 目標狀態 `completed` ＋ 走完整溶解路徑 ＋ INV-1（`dormant` 不催）仍然有效。
- 既有條款**逐字不動**（用位元級比對證明）。

### 3.4 守衛測試（`tests/soul/test_life_thread_m2_sunset.py`）

1. 槽索引邊界：`07:59:59` / `08:00:00` / `08:00:01` / `21:59:59` / `22:00:00` 的槽序號（寫死期望值）。
2. `slots_since_last_creation` 的正確值（含「從未建立」的情形）。
3. **複合條件的四項邊界**（逐項）：age 恰好 21 天 → **False**（嚴格大於）；age 21 天 +1 秒 → 視其餘三項；`active_count < cap` → False；`slots == 2` → False、`slots == 3` → True；非最舊線頭 → False。
4. **決定性**：兩條線頭 `created_at` 相同 ⇒ 恆選 `thread_id` 字典序較小者（**跑 10 次結果一致**）。
5. 一次評估**最多釋放一條**。
6. 走完整溶解路徑：sunset 觸發時**有** reflection ＋ fact ＋ `status=completed`（**不是**靜默改狀態）。
7. **M2 純決策層零改動**：`git diff` 對 `src/soul/life_thread_dissolution.py` 必須為空（用 AST 斷言或檔案雜湊斷言）。

### 3.5 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的測試 |
|---|---|---|
| 1 | 把 age 邊界由 `>` 改成 `>=` | 守衛 3（age 恰好 21 天必須 False） |
| 2 | 移除「容量飽和」條件 | 守衛 3（`active_count < cap` 必須 False） |
| 3 | 把「≥3 槽」改成「≥1 槽」 | 守衛 3（slots==2 必須 False） |
| 4 | 移除「只挑最舊」限制（挑任意一條） | 守衛 4（決定性） |
| 5 | 讓 sunset 路徑跳過 `dissolve_hook`（靜默改狀態） | 守衛 6（必須有 reflection ＋ fact） |

### 3.6 沙盒收束性驗證（`tests/soul/test_sim_accel_streak_7d_sunset.py`）

- **沿用 SIM-TIME-001 平面**：假時鐘 ＋ 記憶副本 ＋ 腳本化 LLM ＋ socket 攔阻；**0 真實等待、0 真實 LLM**。
  ⚠️ `tests/soul/` **無 `__init__.py`** ⇒ **不得跨檔 import 另一個測試模組**；**複製最小必要 primitive**（沿用 M2-WRITEBACK-SIM-1／M2-CATCHUP-1 的既有風格）。
- **兩劇本各跑 7 天 × 14 slot**：劇本 A（順從型 LLM：可關一開一）＋ 劇本 B（反事實：不新建不關閉）。
- **驗收對帳（逐項給分子／分母）**：
  1. 劇本 A：**第 7 天 streak 計數器 10/10 達 7**（判準 1 轉綠）；標題多樣性維持 1.0000。
  2. 劇本 B：**sunset 不得發生**（因為條件 2「容量飽和」與條件 3 在反事實劇本中的表現，須以實測說明；若劇本 B 反而 sunset，須在報告中解釋並調整條件）。
  3. **每一次 sunset 都可逐條對帳**：列出被 sunset 的線頭（`thread_id`、`created_at`、`age_days`、`slots_since_new_thread`、四項條件的值）⇒ **不得有任何一次 sunset 的四項條件不全成立**（這是「不誤傷」的硬證據）。
  4. **不誤傷**：age ≤ 21 天的線頭**必須 0 次**被 sunset。
  5. 生產 `data/soul/**/life_threads.jsonl` 的 sha256 **前後逐位元相同**（所有寫入都在 `tmp_path`）。
  6. 總耗時 < 60 秒、**0 `time.sleep`**。

---

## 4. 驗收（完成的定義）

1. `.venv\Scripts\python.exe -m pytest -q tests/soul/test_life_thread_m2_sunset.py tests/soul/test_sim_accel_streak_7d_sunset.py` **全綠**。
2. 沙盒 **劇本 A 第 7 天 streak 計數器 10/10 達 7**；**每一次 sunset 的四項條件逐條可對帳**；**age ≤ 21 天的線頭 0 次被 sunset**。
3. **M2 純決策層零改動**（`src/soul/life_thread_dissolution.py` 的 `git diff` 為空）。
4. 契約 §2.2 值域**逐字未動**；§3.1 新增條款為**純附加**。
5. 全庫回歸 `.venv\Scripts\python.exe -m pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
6. **5 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
7. 實作紀錄寫入 `logs/LIFE-THREAD-M2-DETERMINISTIC-SUNSET-IMPLEMENTATION.md`。

## 5. 測試

- 新測試：`tests/soul/test_life_thread_m2_sunset.py`、`tests/soul/test_sim_accel_streak_7d_sunset.py`。
- 必跑現有測試：`tests/soul/test_life_thread_sim_time.py`、`tests/soul/test_life_thread_sim_m2_writeback.py`、`tests/soul/test_life_thread_sim_m2_catchup.py`、`tests/soul/` 下線頭相關測試、`tests/infra/`（AST 守衛）。
- 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（**必須指名範圍**）。
- 不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。

## 6. 不做（Out of Scope）

- **不得改 `src/soul/life_thread_dissolution.py`**（M2 純決策層零改動，§0.4）。
- **不得改 `src/soul/life_threads.py`**（M1 零改動：不新增欄位、不新增事件型別、不改值域）。
- **不得新增 `archived` 狀態值**（不在 M1 值域內；新增值域＝改契約 §2.2 ＋ 所有消費端，遠超本票）。
- **不得用 `dormant` 作為 sunset 目標**（INV-1：`dormant` 不催，會靜默停掉未來的檢驗點）。
- **不得調大 `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`**、不得調小任何既有門檻（方案 B 已被拒絕）。
- **不得跳過 `dissolve_hook`**（sunset 必須走完整溶解路徑）。
- 不得寫入／改名／刪除 `data/**`；不得改 `personas/**`、`configs/**`、`src/inner_life/**`。
- 不得重啟服務、不得啟動／停止任何行程、不得對任何 PID 做 `Stop-Process`／`taskkill`／`pkill`。
- **不得觸發任何真實 LLM 推理**（腳本化 LLM 100% 覆蓋）。
- 不得觸發 synthetic diary／dream；不得跑 `scripts/manual_*.py`。
- **不得 commit／push**（由主大腦驗收後執行）。

## 7. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `life_thread_dissolution.py` 純決策層 | **零改動**；0 I/O、0 `src.*` import、0 LLM、決定性輸出排序（`thread_id` 字典序） |
| `STATUS_VALUES` / `TERMINAL_STATUSES` | **逐字不動**；sunset 目標 = `completed` |
| INV-1（`dormant` 不催） | **不得**用 `dormant` 作為 sunset 目標 |
| 邊界一律嚴格大於（`>`） | 21 天整不算超限（**+1 秒才算**）；`>=` 即錯 |
| 兩時距軸互相獨立 | 存活軸與無進展軸各自判定，**任一命中即成立**，不得互相抑制 |
| `checkpoints_exhausted` 語意 | 「呼叫端顯式注入」；**不得**由測試或模組從資料猜測 |
| `reflection_prompt` | **確定性模板，非 LLM**；同一輸入逐字同一輸出 |
| 每日溶解配額 `DISSOLVE_MAX_PER_DAY = 3` | 屬 LLM 呼叫層；決策層不得因配額抑制 `should_mutate` |
| 蔡戈尼記憶效應 | sunset 必須**釋放張力並留下沉澱**（reflection ＋ fact），**不是靜默丟棄** |
| M1 契約 §2.2 / §2.4 | **值域與欄位逐字不動** |
| 生產 `data/**` | 只讀；沙盒寫入一律落 `tmp_path` |

## 8. 回報格式

1. 改動檔案清單（新增／修改，逐檔一行）。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：5 條逐條的「變異內容 → 轉紅的測試名 → 紅燈實測輸出」。
4. **沙盒收束性對帳**：劇本 A／B 逐 slot 明細、**每一次 sunset 的四項條件逐條對帳表**、streak 計數器逐日值、標題多樣性、不誤傷統計（age ≤ 21 天者被 sunset 次數）、生產 sha256 前後對帳、總耗時。
5. **明確結論**：門檻三判準 1 是否在沙盒第 7 天轉綠；本機制在生產現況下會對幾隻 agent 生效（預期 7/10，依 §0.3 表）。
6. 是否有踩到 Frozen Contract／意外行為。
