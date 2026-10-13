# SOUL-TECH-DEBT-001 — 測試套件環境修復與存量失敗清理（Test Suite Baseline Normalization）

**工單編號**：SOUL-TECH-DEBT-001
**工單標題**：測試套件環境修復與存量失敗清理
**工單負責人**：Lin（主大腦）
**審批人**：Bryan
**優先級**：P1（High）
**狀態**：READY TO IMPLEMENT（**已修正 §A 工單前提錯誤，見「工單前提核對」**）
**立項日期**：2026-10-12
**上游工單**：SOUL-MEM-001 Phase 0（`f2afdd5`）

---

## 工單前提核對（2026-10-12 開工前量測）

依 SOUL-MEM-001 建立的紀律「外部 agent 開立的工單，其現況描述可能是幻覺」，開工前逐項核對原始工單描述，**發現兩處前提錯誤**：

### 🔴 錯誤 1：任務 1「安裝 pytest-asyncio」——套件早已存在

```
pytest-asyncio 1.4.0     ← 已安裝於 .venv
```

**真正的問題不是缺套件，而是模式設定。** `pytest-asyncio` 1.x 預設為 **strict mode**，要求每個 async 測試帶 `@pytest.mark.asyncio` 標記。實測：

| 檢查項 | 結果 |
|---|---|
| `.venv` 內 `pytest-asyncio` | ✅ 1.4.0 已安裝 |
| 全庫 `@pytest.mark.asyncio` 標記數 | ❌ **0** |
| `pytest.ini` 的 `asyncio_mode` 設定 | ❌ **無** |

⇒ 真正的修法是**在 `pytest.ini` 設定 `asyncio_mode = auto`**，**不需要安裝任何套件**。

實測驗證（`-o asyncio_mode=auto` 臨時覆寫，未寫入任何檔案）：

| 測試檔 | strict（現況） | auto |
|---|---|---|
| `test_proxy_stub_speak.py`（3 個 async 測試） | 3 failed | **3 passed** ✅ |

### 🔴 錯誤 2：任務 1 與任務 2 互相獨立，非同一根因

原工單暗示「補完 async 後 `bus` fixture 會一起好」。實測否證：切到 auto mode 後 `test_event_bus.py` 等**仍是 9 個 ERROR**，`fixture 'bus' not found` 依舊。兩個阻塞獨立，必須分開修。

### 🔴 錯誤 3：驗收標準「105 → 0」的線性假設不成立

開啟 auto mode 會**一次性解封 155 個 async 測試（分布在 27 個檔案）**——這些測試在當前環境下**從未執行過**。它們很可能因其他原因失敗（fixture 缺失、API 漂移），因此失敗總數**不保證單調下降**。

| 檢查項 | 數值 |
|---|---|
| 全庫 `async def test` 函式數 | **155** |
| 分布檔案數 | **27** |
| 現況全庫回歸（strict） | `105 failed / 6231 passed / 15 errors` |

---

## 一、背景與目標

SOUL-MEM-001 回歸時全庫為 `105 failed, 6231 passed`，經可逆還原 A/B 對照確認**全部為存量技術債，非本次改動引入**。本工單目標是清理環境與修復陳舊斷言，恢復 pytest 的綠燈公信力，避免後續工單被迫仰賴「手工比對差分」驗收。

## 二、具體任務範圍

### 任務 1：啟用 async 測試支援

1. **在 `pytest.ini` 加入 `asyncio_mode = auto`**（不需安裝套件，見前提核對錯誤 1）。
2. 加入前必須先取得 auto mode 的全庫基線數字（155 個解封測試的實際失敗率），作為本工單的驗收基準。
3. ⚠️ **不得為了讓測試變綠而移除或改寫任何生產安全防線**。

### 任務 2：補齊 `bus` fixture

1. `tests/conftest.py` 內目前 `def bus` 出現次數為 **0**，但多個測試檔使用 `bus` fixture（`test_event_bus.py` 全 7 個、`test_self_check_v1.py`、`test_timezone_fix_v1.py`）。
2. 補上符合契約的 `bus` fixture（`SoulEventBus` 實例），確認不與 `tests/conftest.py` 既有的 `TEST-ISOLATION-FIX-1` autouse fixture（`SOUL_OS_DATA_DIR` 隔離）衝突。
3. 修復後這 9 個 ERROR 應轉為 pass 或真實失敗。

### 任務 3：校準過期的 baseline 斷言

2026-08 那批歷史修法已改變程式碼行為但測試未同步。已知案例：`test_stale_filter_v1::test_c` 斷言 proxy **不應**含 `STALE_THRESHOLD_SEC`，但修法 7 已於 2026-08-04 加入該常數。

1. 盤點「程式碼已修、測試未更」的全部案例。
2. 依現行程式碼的真實契約校正預期值。
3. 杜絕假陽性報警——**不得為迎合測試而破壞生產防線**。

## 三、驗收標準（Definition of Done）

| # | 標準 | 說明 |
|---|---|---|
| 1 | Async 測試可執行 | 不再出現 `async def functions are not natively supported` |
| 2 | `bus` fixture ERROR 清零 | 9 個連帶 ERROR 解決 |
| 3 | 失敗數降至可維護水準 | **驗收門檻待 auto mode 基線量測後由 Bry 拍板**（原訂「105 → 0」不保證可達） |
| 4 | 0 破壞性變更 | 不得為迎合測試破壞生產安全防線；每項修正須可解釋 |
| 5 | 治理更新 | `logs/ENGINEERING_STATE.md` §9 登記本次修復 |

## 四、不做（Out of Scope）

- 不升級 pytest 本體版本（9.1.1 維持）。
- 不刪除或 `xfail` 任何測試以製造綠燈（若確有深層阻礙，須逐案列明 `reason` 並經 Bry 核准）。
- 不碰 `data/**` 生產資料。
- 不動 SOUL-MEM-001 已交付的 `sanitize_legacy_runaway_turn` 實作。

## 五、安全邊界（AGENTS.md 行程安全鐵律）

- **不得殺任何行程**，不得清理殘留行程。
- 不得啟動伺服器、不得綁生產埠、不得發真實請求（LLM 成本 = 0）。
- `data/**` 只讀。
- 測試數字一律以 `.venv\Scripts\python.exe -m pytest` 為準，並在報告中聲明解釋器。

## 六、回報格式

- 改動檔案清單 + 原因
- 測試結果（跑了哪些、過幾筆、有無失敗；**必須聲明解釋器**）
- auto mode 解封的 155 個測試的實際結果分類
- 是否踩到 Frozen Contract / 意外行為