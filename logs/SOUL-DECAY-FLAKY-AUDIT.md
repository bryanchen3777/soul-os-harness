# SOUL-DECAY-FLAKY-AUDIT：decay 測試 flaky 獨立稽核（工單）

**狀態**：✅ **已實作完成**（2026-10-19）—— 重寫為**注入固定時鐘**的確定性版本；
變異實驗 MUT-A / B / C **三條全紅**；失敗觸發條件已量化。
**日期**：2026-10-19｜**嚴重度**：P3（測試品質，非產品缺陷）｜**登記者**：主大腦
**立案依據**：Owner 裁決 4（2026-10-19）「Flaky Decay 測試單獨立項，嚴禁夾帶進 SOUL-BUG-010-B」
**完工依據**：本檔第 54-55 行已預先核准此修法方向（注入固定時鐘，而非放寬斷言）

---

## 為什麼獨立立案

`tests/test_m5_13_5_untouched_decay.py` 的
`TestUntouchedBeyondGraceDecays::test_untouched_just_over_grace_decays`
在全庫回歸中偶發失敗。三層實證已確認它**與本輪所有產品變更無因果關係**：

| 實證 | 結果 |
|---|---|
| 單獨跑 | 通過 |
| 重複 5 次第 3 次才失敗 | **兩次觀察到「不同的測試」失敗** ⇒ 決定性 flaky 特徵 |
| 基線 `04da8a3` worktree，decay 單檔 ×20 | **0/20 失敗** |
| 本輪樹，decay 單檔 ×20 | **0/20 失敗** |
| 本輪樹，BUG-012 全檔 ＋ decay ×20 | **0/20 失敗** |
| 程式碼觸碰 | 未碰 `src/soul/relationships.py` 或任何 decay 邏輯 |

⚠️ **已知反例**：基線 worktree 的**全庫**回歸跑出 23 failed，但那批失敗**全部是 worktree 環境雜訊**
（checkout 造成的 CRLF、未追蹤檔缺失、`git diff HEAD` 型守衛讀到不同 index），**不能當控制組**。
⇒ 「與基線對照」這條路徑目前**不可用**，本票若要取得可靠控制組，須改用**同樹、不同時段**對照。

---

## 根因（已實證，非推測）

**分類：時間／負載敏感的確定性缺陷（測試脆弱），不是產品邏輯錯誤。**
排除「跨日邊界」與「浮點精度」兩項既有假設。

### 機制

| 位置 | 事實 |
|---|---|
| `src/soul/relationships.py:42` | `from datetime import datetime, timezone, timedelta` ⇒ 模組層級 `datetime` **就是類別本身**（可用 `patch(...)` 注入，不必改 `src/`） |
| `src/soul/relationships.py:236` | `_decay_locked()` **自己**取 `now = datetime.now(timezone.utc)` |
| `src/soul/relationships.py:269` | `days = (now - anchor_dt).total_seconds() / 86400.0` |
| `src/soul/relationships.py:272` | `decay = days * CONFIDENCE_DECAY_PER_DAY`（0.02，`:67`） |
| 舊版測試 `_set_created_at_days_ago()` | **另外**取一次 `datetime.now(timezone.utc)` 當參考時刻，再寫 `created_at = (now - N days).isoformat()` |

⇒ **實得 age = N + Δ**，Δ ＝ 測試取時與實作取時之間的牆鐘漂移。

### 翻紅預算（解析）

```
places=10 ⇒ 容差 0.5e-10（confidence）
Δ 上限 = 0.5e-10 / 0.02 = 2.5e-9 天 = 2.16e-4 秒 = 0.216 ms
```

**任何超過 0.216 ms 的延遲（GC pause／OS 排程搶占／防毒掃描／磁碟 IO）都會讓這支測試失敗。**

### 受影響斷言（places=10 ⇒ 0.216 ms 預算）

- `TestUntouchedBeyondGraceDecays::test_untouched_beyond_grace_decay_at_0_5`
- `TestUntouchedBeyondGraceDecays::test_untouched_beyond_grace_decay_at_0_9`
- `TestUntouchedBeyondGraceDecays::test_untouched_just_over_grace_decays`
- `TestTouchedEntryUsesLastInteractionNotCreatedAt::test_touched_recently_with_old_created_at_no_decay`
- `TestTouchedEntryUsesLastInteractionNotCreatedAt::test_touched_5_days_ago_decays_from_last_interaction`

`test_naive_created_at_no_tzinfo_handled` 用 `places=6` ⇒ **2160 ms（2.16 秒）預算 ⇒ 本來就安全**
（實測確認，見下表）。**該支測試刻意不動。**

---

## 修法（注入固定時鐘，不放寬斷言）

`src/soul/relationships.py` 的模組層級 `datetime` 就是類別本身，因此可在**測試端**
`patch("src.soul.relationships.datetime", FrozenDatetime)` —— `FrozenDatetime` 是
`datetime` 的**子類**，只覆寫 classmethod `now()` 回傳固定時刻，
`fromisoformat()` **保持原生**（實作端靠它解析 entry 的 ISO 字串，語意不可變）。

⇒ 測試端所有參考時刻（`created_at` / `last_interaction_at`）與實作端讀到的 `now`
**來自同一個時鐘** ⇒ **Δ ≡ 0** ⇒ `places=10` 從「靠運氣」變成**位元精確的確定性斷言**。

**`places` 只可保持或收緊，永遠不可放寬**（工單硬性要求）。AST 守衛已把這條釘死。

實測可行性（先驗後改）：

- `datetime` **可以**被 subclass；`now()` 是 classmethod，覆寫有效。
- `FrozenDatetime.fromisoformat()` 回傳 `FrozenDatetime` 實例（`datetime` 子類），
  減法／`tzinfo` 處理／naive→UTC 替換**全部與原生一致**（已實測）。
- `patch` 生效與否與「store 在 patch 前建立 / patch 後建立」**無關**（兩種順序皆可）。

---

## 失敗觸發條件（可重現描述，取代「偶爾失敗」）

量測腳本：`%TEMP%\decay_flaky_repro.py`（用 repo 的 `.venv` 直譯器執行）

### 有效容差（由 places 反推）

| places | 容差（confidence） | 可忍受的時鐘漂移 Δ |
|---|---|---|
| 10 | 5.000e-11 | **0.216 ms** |
| 6 | 5.000e-07 | **2160 ms（2.16 秒）** |

### 實測 Δ 分佈（測試取時 ↔ 實作取時，N=500）

| 統計量 | 值 |
|---|---|
| max | **1.522 ms** |
| p99 | 1.015 ms |
| mean | 0.0248 ms |
| **超出 0.216 ms 預算的樣本** | **12 / 500（2.40%）** |

⇒ 本機**在無人工延遲的情況下**就已經會超過預算（不是只有負載下才會）。

### 可重現失敗

| 場景 | 結果 |
|---|---|
| 舊版測試**原樣**重跑 N=500（無人工延遲） | **9 / 500 翻紅（1.80%）** |
| 新版測試重跑 N=500 | **0 / 500（0.00%）** |
| 舊版 + 受控延遲 `time.sleep(0.005)` | **翻紅**（confidence = 0.3999999987699074，Δ_conf = −1.23e-09） |
| 新版 + 同一個受控延遲 | **仍綠**（confidence = 0.4，Δ_conf = **0.0** 位元精確） |

### 翻紅門檻（偏移時鐘掃描）

| Δ (ms) | 舊版 | 新版 |
|---|---|---|
| 0.0000 | GREEN | GREEN |
| 0.1500 | GREEN | GREEN |
| 0.2000 | GREEN | GREEN |
| 0.2150 | GREEN | GREEN |
| **0.2160** | **RED** | GREEN |
| 0.2500 / 1.0000 / 5.0000 | RED | GREEN |

⇒ **實測翻紅門檻 ≈ 0.2160 ms，與解析預算 0.216 ms 完全吻合。**
新版在所有偏移下皆綠（Δ ≡ 0，位元精確）。

---

## 變異實驗（雙向；repo 從未被修改）

Harness：`%TEMP%\mut_decay_flaky.py` —— 把 `src/` + `tests/` + `pytest.ini` 複製到
`%TEMP%\mut_decay_flaky_work`，**只改副本**，用 repo 的 `.venv\Scripts\python.exe`
在副本 cwd 跑指定測試檔。
報告：`%TEMP%\mut_decay_flaky_report.txt`／`%TEMP%\mut_decay_flaky_results.json`

| 變異 | 內容 | 結果 |
|---|---|---|
| BASELINE | 未變異 | **GREEN**（25 passed） |
| **MUT-A** | 拿掉重寫後測試的凍結時鐘注入（改回真實牆鐘） | **RED**（14 failed / 11 passed） |
| **MUT-B** | 實作錨點 `created_at` → `created_at + UNTOUCHED_DECAY_GRACE_DAYS` | **RED**（6 failed / 19 passed） |
| **MUT-C** | `decay = days * CONFIDENCE_DECAY_PER_DAY * 1.01` | **RED**（8 failed / 17 passed） |

### 關鍵守衛節點逐一驗證（必須轉紅的那幾支）

| 變異 | 節點 | 結果 |
|---|---|---|
| MUT-A | `test_race_budget_quantified_and_frozen_delta_is_zero` | **RED**（1 failed） |
| MUT-B | `test_untouched_beyond_grace_decay_at_0_5` | **RED**（1 failed） |
| MUT-B | `test_untouched_beyond_grace_decays_from_created_at` | **RED**（1 failed） |
| MUT-C | `test_untouched_beyond_grace_decay_at_0_5` | **RED**（1 failed） |
| MUT-C | `test_decay_rate_is_exact_under_frozen_clock` | **RED**（1 failed） |

**MUT-B 是最關鍵的一條**：它證明凍結後的測試**仍然有牙** —— 不是同義反復的假綠。

---

## 改動檔案（3 個，`src/**` 零改動）

| 檔案 | 變化 |
|---|---|
| `tests/test_m5_13_5_untouched_decay.py` | 重寫為凍結時鐘版（416 → 519 行）；**14 支既有測試的名稱與意圖全部保留**；`test_naive_created_at_no_tzinfo_handled` **逐字未動** |
| `tests/test_soul_decay_flaky_audit.py` | **新增**（329 行）：競態量化／注入點有效性／衰減語意／污染／容差政策（AST）五組守衛 |
| `logs/SOUL-DECAY-FLAKY-AUDIT.md` | 本檔（63 → 216 行）：狀態由「待排程調查」改為實作完成，填入根因／修法／量測／變異證據 |

新增的可重用 helper（在 `tests/test_m5_13_5_untouched_decay.py`）：

- `FROZEN_NOW` —— 單一凍結時刻（2026-10-19T12:00:00+00:00）
- `class FrozenDatetime(datetime)` —— 只覆寫 `now()`
- `frozen_clock()` —— context manager，`patch("src.soul.relationships.datetime", FrozenDatetime)`
- `reference_instant()` —— 測試端參考時刻（與實作端**同一個**時刻）

---

## 測試結果

解譯器（**唯一合法**）：`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`（Python 3.11.15）

| 範圍 | 結果 |
|---|---|
| `tests/test_m5_13_5_untouched_decay.py` ＋ `tests/test_soul_decay_flaky_audit.py` | **25 passed / 0 failed**，0.47 s |
| 同上**連跑 10 次** | **10/10 全綠**（0 失敗） |
| 全部 28 個「import／使用 `src.soul.relationships`」的測試檔**一起跑** | **598 passed + 19 subtests passed / 0 failed**，27.68 s（無污染） |

---

## 不做（Out of Scope）— 未違反

- ❌ **未**修改 `src/**`、`personas/**`、`configs/**`、`data/**`。
  自證：`git diff --stat -- src/` **為空**。
- ❌ **未**放寬任何斷言容差（AST 守衛 `test_no_assertion_tolerance_was_loosened` 釘住）。
- ❌ **未**刪除／skip／xfail 任何既有測試；14 支既有測試名稱與意圖全保留。
- ❌ **未**修改 `logs/ENGINEERING_STATE.md`、`logs/SOUL-BUG-INDEX.md`、`logs/SOUL-BUG-*.md`。
- ❌ **未**夾帶進 SOUL-BUG-010-B（此票獨立立案，Owner 裁決 4）。
- ❌ **未**啟動任何伺服器、**未**綁 `:8000`／`:8765`／`:8766`／`:8767`、**未**對生產發真實請求。
- ❌ **未**殺任何行程（`Stop-Process`／`taskkill`／`pkill` 全部未執行）。
- ❌ **未** push。

---

## 相關

- 索引：`logs/SOUL-BUG-INDEX.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`（2026-10-19 登記條目 ⑧）
- 受影響檔案：`tests/test_m5_13_5_untouched_decay.py`
- 守衛檔案：`tests/test_soul_decay_flaky_audit.py`
- 量測腳本（%TEMP%，不入 repo）：`mut_decay_flaky.py`、`decay_flaky_repro.py`
