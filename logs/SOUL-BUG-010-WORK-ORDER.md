# SOUL-BUG-010：靈魂節點生命週期接線 ＋ 分級消費（Tiered Consumption）（工單）

**狀態**：📋 工單已定案，**待 Owner 觀察期滿（24–48h）後授權實作**
**日期**：2026-10-18｜**嚴重度**：P1（架構缺口，非故障）｜**登記者**：主大腦
**前置依據**：`logs/ENGINEERING_STATE.md` 三條登記（`3b0ca0f` 前置審查、`a4ab2a7` 自我更正、`c234a43` 算法深查）

---

## 目標

讓生命週期機制在生產上**真的存在**，並讓 `dormant` 節點**真的停止繁殖**——同時保住「細水長流的跨週習慣仍可沉澱為靈魂」這條哲學底線。

一句話：**目前靈魂層只會生、不會死，而且就算接上生命週期也不會改變這件事。**

## 為什麼這不是「加一行呼叫」就完事（三層事實，全部實測）

1. **零呼叫點**：`evaluate_lifecycle`／`supersede`／`reinforce`／`record_contradiction` 在 `src/` 與 `scripts/` 中**呼叫點為 0**；唯一呼叫者是 `harness/tl4.py` 的 time-lapse 模擬器。
2. **存不下來**：`_persist_result`（`elevation_adapter.py:551`）只 append **本次新昇華**的節點，全檔**無 update 路徑**。程式碼 `:1275` 自陳「supersede 原地改不动文件」。
3. **🔴 就算前兩項都補上，生長數一顆都不會減少**：`lifecycle_state` **完全不出現在 `elevation_adapter.py`**。昇華路徑不讀它，被弱化的 pattern 照樣計入 `min_evidence`、照樣餵出新的 value 節點。

> 這是本票存在的理由。**只做 1+2 而不做 3，等於花數百行程式碼買到一個會顯示「動搖中」標籤、但繁殖力完全不變的假防線。**

## 已實測的訊號現況（主大腦獨立重算，344 節點／829 邊）

| 項目 | 實測值 |
|---|---|
| age 分佈 | min 0.003 天／median 6.19 天／max 14.60 天 |
| ≥7 天（會轉 `weakening`） | **160 顆**，且**全部是 `pattern`** |
| ≥30 天（會轉 `dormant`） | **0 顆**（距門檻尚差約 15.4 天） |
| 邊錨點較 `last_support_ts` 更新 | 13 顆 |
| `contradiction_pressure` | **344/344 長度為 0** |

## 三個選項的排除理由（Owner 裁定，不得重新翻案）

- **否決 (C) 走 supersede 正路**：矛盾證據依賴語義相反檢測（NLI），而生產**根本沒有矛盾邊的偵測與寫入管線**，`record_contradiction` 呼叫點為 0。在無感知輸入下談 supersede 是空中樓閣。
- **否決純 (A) 一刀切剝奪 `weakening` 投票權**：會引發認知災難。現有 160 顆 pattern 將全數作廢；而「週一一次、下週二又一次（間隔 8 天）」在人類心智中是**標準的長期跨週習慣**。若把 ≥7 天判為無效證據，系統將只剩「一週內密集成癮的短暫衝動」能升格，**真正細水長流的習慣永遠升不了格**，直接推翻 `prior.py:62` 信條「old ≠ outdated——年齡不是退場理由，失去支持才是」。

## 架構裁定：分級消費（決策已定，執行者照做）

```
[pattern 誕生]
      │  7 天無新支持
      ▼
[weakening] ──► 投影層：照常投影（仍有影響力）
                昇華層：保留投票權（跨週習慣仍可累積證據）
      │  30 天無新支持
      ▼
[dormant]  ──► 投影層：NON_PROJECTED（全面自 Prompt 隱形，防 Token 膨脹）
                昇華層：EXCLUDED（失去 min_evidence 計票資格，徹底失去繁殖力）
```

- **防線一（解 Token 與心智干擾）**：靠 `emergent_projection.py:64-68`／`:248-251` **既有**守門——**讀取端程式碼不需改**，只要狀態真的變成 `dormant` 就自動生效。
- **防線二（解圖譜無效繁殖）**：**本票唯一的 `elevation_adapter.py` 行為變更**——計算 `min_evidence` 時排除 `dormant`／`superseded`，放行 `active`／`weakening`。

## 做法（範圍明確，不得自行擴張）

### 步驟 1：新增純函式模組 `src/inner_life/lifecycle_overlay.py`

- 檔名常數 `LIFECYCLE_EVENTS_FILENAME = "elevation_lifecycle_events.jsonl"`。
- `load_lifecycle_events(store_dir) -> list[dict]`：讀取差分日誌，**缺檔／讀失敗／單行壞 → 回 `[]`，絕不 raise**（沿用本專案既有 fail-silent 慣例）。
- `apply_lifecycle_overlay(nodes: list[dict], store_dir) -> list[dict]`：以 `node_id` 為鍵，把每筆事件的 `lifecycle_state`（與 `superseded_by`）覆蓋到節點 dict 上。**同 node_id 多筆事件以檔案順序取最後一筆**（append-only ⇒ 檔案順序即時間序）。
- **純標準函式庫，不得 import `soul_elevation`**（`emergent_projection.py:62-63` 明文要求 read-side 獨立於該可選依賴，缺裝也要能工作）。

### 步驟 2：三條讀取路徑一律套用 overlay（**最容易漏、也最致命**）

主大腦已查清全庫共 **4 條**路徑讀 `elevation_nodes.jsonl`，**漏掉任何一條，該條防線就形同虛設**：

| # | 位置 | 套用後的效果 |
|---|---|---|
| 1 | `elevation_adapter.py:462` 既有節點載入（同時餵給 `engine._nodes` 於 `:921-924`） | 引擎看到正確狀態，`evaluate_lifecycle` 才能逐級轉移 |
| 2 | `emergent_projection.py:102` `load_elevation_nodes()` | **防線一**：dormant 不再投影進 prompt |
| 3 | `elevation_recall.py:162-164` 自有讀取 | 歷史回憶通道才記得「dormant／superseded 是可召回的」 |
| 4 | `src/world/elevation_adapter.py:56`（import `NODES_FILENAME`） | 確認其讀取是否需 overlay；**若只取檔名常數則不需改**，但必須在回報中明說 |

⚠️ **注意**：`elevation_recall.py` **自己讀檔**（只 import 了 `NODES_FILENAME` 與 `_resolve_store_dir`），**不會**自動吃到步驟 2-2 的修正。

### 步驟 3：`elevation_adapter.py` 新增全域生命週期評估函式

- `evaluate_lifecycle_all(store_dir, llm=None) -> list[ElevationNode]`：載入**全部 agent** 的節點與邊成單一 engine → 呼叫 `engine.evaluate_lifecycle()` → 把每個轉變以 `{ts, node_id, event, lifecycle_state_after, lifecycle_state_before, anchor_ts, reason}` 追加進 `elevation_lifecycle_events.jsonl`（**只用既有 `_append_jsonl`**，不新造寫入層）→ 回傳轉變清單。
- **不做去重**：同一 node_id 每天最多一筆轉變（引擎本身逐級、一次一級），重複寫入無害且符合 append-only 審計性。
- **失敗隔離**：整體包 `try/except Exception` 並 `logger.warning(..., exc_info=True)`，**絕不讓生命週期評估中斷任何既有主路徑**。

### 步驟 4：`elevation_adapter.py` 昇華層 dormant 排除（防線二）

- 在計算每個候選維度的有效證據時，**排除 `lifecycle_state ∈ {dormant, superseded}` 的 pattern**。
- **只排除，不改權重**：`active` 與 `weakening` 的計票方式**逐位元不變**。
- **必須留註解說明語意**：這是「一個月完全無人問津的陳年廢棄 pattern 不得被挖出來湊數升格」，而**不是**「年紀大就沒資格」——跨週習慣在 `weakening` 區間內仍有完整投票權。

### 步驟 5：接線點（**每日一次，刻意不接 30s**）

- 掛在 `scripts/run_server.py` 的 **night slot（22:00 槽位）**，與既有 diary/dream 寫入同槽。
- **不要掛 30 秒 scheduler 分支**：門檻是 7／30 天，30 秒重算毫無意義，且 `_lifecycle_anchor` 為 **O(N×E)**（逐節點掃全邊），圖譜成長後是實質成本。
- 必須包在既有 fire-and-forget 的失敗隔離風格內。

## 驗收（完成的定義）

1. **狀態真的落盤且跨重啟存活**：跑一次 `evaluate_lifecycle_all` 後，殺程序重啟，`lifecycle_state` 仍是 `weakening`／`dormant`（**不是每次從 `active` 重算**）。
2. **`elevation_nodes.jsonl` 位元組不變**：接線前後檔案大小與 sha256 **逐位元相同**（Append-Only 鐵律的可驗證證據）。
3. **防線二有效**：構造一個 ≥30 天無支持且未被佔用的 pattern，證明它**無法**單獨把某維度推過 `min_evidence`；同時構造一個 8 天（`weakening` 區間）的 pattern，證明它**仍可**正常升格。
4. **防線一有效**：同一個 `dormant` 節點出現在 `project_emergent()` 的輸入時，**不得**出現在輸出。
5. **零迴歸**：全庫 `.venv\Scripts\python.exe -m pytest -q tests` 與實作前**同數**，且解釋器口徑一致。

## 測試

- 新測試檔 `tests/test_soul_bug_010_lifecycle_wiring.py`，**不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求、不得殺任何行程**。
- `store_dir` 一律指向 `tmp_path`。
- 必須做**變異實驗**，至少覆蓋：拿掉 overlay 套用、拿掉防線二的排除、拿掉步驟 3 的持久化、把 `weakening` 誤一併排除——**每一條都必須轉紅**。變異沒轉紅＝該維度沒被測到，守衛視為盲腸邏輯必須重寫。
- 反空轉設計：每條「不得發生 X」都要配「該發生的位置確實發生了」的對照組。

## 不做（Out of Scope）

- ❌ **不改** `soul_elevation` 任何一行（repo 外 editable 依賴，動它不會隨本 repo 版控；且生命週期邏輯本身不需要改）。
- ❌ **不接** `record_contradiction`／`supersede`（走 (C) 正路的前提是 NLI 矛盾偵測，那是另一張票）。
- ❌ **不整檔覆寫或重排** `elevation_nodes.jsonl`（Owner 明令紅線：摧毀 Append-Only 審計基石，並發下有鎖死／截斷／損毀風險）。
- ❌ **不動** `emergent_projection.py` 的過濾邏輯（防線一已由既有 `:248-251` 免費提供）。
- ❌ **不設**任何入口限流門檻（SOUL-BUG-009 已拔除此類冗餘閘門；MUT-02 實證不可獨立觀察）。
- ❌ **不補歷史資料**：本票只處理接線與邏輯，**不回溯**寫入既有 344 顆節點的狀態差分（首次評估自然會產生）。
- ❌ 不碰 `personas/`、`configs/`、`data/`（`data/` 只讀）。

## Frozen Contract 注意

- `elevation_nodes.jsonl` **append-only 單一權威**，不得引入 update／rewrite 路徑。
- 讀取端過濾契約（`NON_PROJECTED_LIFECYCLE_STATES`、`_normalize_lifecycle_state` 缺省→`active`）**不得改動**，既有測試 `test_04_superseded_not_projected`／`test_05_missing_lifecycle_state_treated_as_active`／`test_T3_superseded_readonly` 必須維持綠。
- SOUL-BUG-009 的錨點政策（`_select_anchor_representative`）與 SOUL-BUG-001 的 `VALUE-DEDUP-A-1`／`INV-2` 不得更動。
- `elevation_adapter.py` 外層 `except` 的 `exc_info=True`（SOUL-BUG-009 修的靜默回傳 [] 缺陷）不得移除。

## 執行順序（Owner 裁定，不得跳階）

1. **觀察期（24–48 小時）**：今早 10 隻 agent 各長 1 顆 value，本質是 SOUL-BUG-009 解凍後**一口氣消化過去 14 天累積的 29 份歷史證據**（首波洩洪效應）。該批證據已消化、未消耗證據回到 0~1。**保持服務運行，觀察真實自然生長速度是否如推算收斂，不得因單日數字波動而恐慌操作。**
2. **Owner 授權後**才開工本工單的實作。
3. 實作完成後**另案部署重啟**（需 Owner 當次明示授權）。

## 回報格式

- 改動檔案清單（逐檔一行，標明新增／修改）。
- 測試結果：跑了哪些、幾筆過幾筆、有無失敗；**變異實驗 N/N 全紅的逐條對照**。
- `elevation_nodes.jsonl` 接線前後的 **sha256 與檔案大小對照**（Append-Only 鐵律的證據）。
- 四條讀取路徑的 overlay 套用結果逐條回報（第 4 條須明說是否需要改）。
- 是否踩到 Frozen Contract、有無意外行為。
- 誠實揭露：**哪些是實測、哪些是推估、哪些無法確定**。

## 相關

- 索引：`logs/SOUL-BUG-INDEX.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`（`3b0ca0f`／`a4ab2a7`／`c234a43`）
- 演算法原始碼：`soul-elevation/src/soul_elevation/engine.py:1295-1377`、`prior.py:62-76`（**repo 外**）
- 本票由 SOUL-BUG-009 生產實證驗收（`a5bed77`）翻出的生長速率問題衍生