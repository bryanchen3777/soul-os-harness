# SG-4 — 關係帶信號源定義擴充（Bry 軸讀側折抵）ADDENDUM

**性質**：CONTRACT / DOCS-ONLY 增補（本檔 0 code）。
**歸屬**：`docs/SG-3-RELATIONAL-CONTRACT.md`（`308f946`）的**增補條**，不取代其任何條文。
**工單**：RELATIONAL-BAND-FIX-1（修法）＋ RELATIONAL-BAND-INVESTIGATION-1（唯讀根因調查，`docs/RELATIONAL-BAND-INVESTIGATION.md`）。
**狀態**：修法已實作；本案**未重啟生產** ⇒ 尚未生效。

---

## §1 動機（為什麼需要這條增補）

SG-3 把關係帶門檻重標定為 `stranger→known = co_presence_sessions ≥ 1 OR reply_exchanges ≥ 1`，
並為 reply 動力加了**讀側折抵**（每個既有共在 session 折抵 1 個 reply_exchange）。
但 SG-3 沒有處理一個**結構性缺口**：**Bry 軸（Owner 通道，`user_bryan`）沒有任何信號載體。**

唯讀調查（RELATIONAL-BAND-INVESTIGATION-1）實測：

| 觀測 | 實測值 |
|------|--------|
| `data/soul/interactions.jsonl`（co_presence 唯一載體） | 26 行，`user_bryan` 出現 **0 次** |
| `data/world/perception_trace.jsonl`（reply 唯一載體） | 13,490 行，`user_bryan` 出現 **0 次** |
| `event_type == "reply"`（reply writer 的唯一條件） | 全庫 **0 筆** |
| `data/soul/<agent>/relationships.json` 的 `others.user_bryan.last_interaction_at` | 由採集層 `touch()` 正常維護（ruka 實測 `2026-09-14T04:15:05.675631+00:00`，`interaction_count = 243`） |

⇒ **Bry 軸三個 objective 計數器在生產上結構性恒 0**，而 `stranger→known` 的門檻是 `≥1`
⇒ **Bry 軸關係帶結構性永不可晉升**（且 `DEMOTE_DAYS = 90` 的降帶對底帶無意義）。
這不是「產率不夠」，是**信號源定義本身缺了 Owner 通道**。

---

## §2 口徑（信號源定義擴充，逐條凍結）

**S4-1｜Bry 軸 inbound 折抵。** 當該 pair 的 `other_id == user_bryan`，且
`relationships.json` 的 `others.user_bryan.last_interaction_at` **落在同一 24h 結算窗內**
（`window_start ≤ ts ≤ now`，與既有 co_presence 讀取的窗邊界口徑**逐值一致**）
⇒ 折抵 **1 次 `co_presence_sessions`**。

**S4-2｜reply 由既有折抵自動帶起。** 折抵值走既有 SG-3 讀側折抵算式
（`reply_raw = counts["reply"] + counts["co_presence"]`）⇒ `reply_exchanges` 隨之 +1。
**0 新增 reply 動力、0 新增 reply writer。**

**S4-3｜單窗上限 1。** Bry 軸折抵值**恒為 0 或 1**（不像 jsonl 聚合那樣隨筆數增長）
⇒ 與 SG-3 §5.1「同一 pair 單窗增量上限 1」同口徑；`settle_relations` 的 `min(raw, 1)`
照舊兜底（**不改**）。折抵與未來若真出現的 `user_bryan` jsonl 記錄**相加**後仍受該上限約束。

**S4-4｜冪等。** 判準 = 該 entry 的 `objective.last_signal_at` 是否**已落在本窗內**
（嚴格 `> window_start`）。
- 同窗重複 settle（含 `force=True` 繞過 24h 節流）⇒ 折抵 **0**，計數不得累加超過 1。
- 恰好等於窗起點者視為**上一窗**的戳 ⇒ 使 24h 節流邊界上的下一窗仍能正常折抵。
- 使用**既有**欄位作冪等載體 ⇒ **0 新增 schema 欄位 / 0 新增 sidecar**。

**S4-5｜載體選擇。** 折抵只讀 `relationships.json` 的**既有 4.1 欄位**
`others.user_bryan.last_interaction_at`（採集層 `touch()` 維護）。
**0 新增欄位 / 0 新增寫入路徑 / 0 新增事件類型 / 0 新增檔案。**

**S4-6｜窗口外 / 壞資料 fail-closed。** 窗外的 `last_interaction_at`、未來時間戳、
檔案缺失、壞 JSON、壞時間戳 ⇒ 折抵 **0**，且**不 crash 主循環**（與既有信號讀取一致）。

**S4-7｜落地位置。** 讀側折抵落在 `src/social/relation_settlement.py::collect_window_signals`
（＝既有唯一信號聚合函式），以 helper `_bry_axis_credit` 實作。
**不新增流程節點、不改調度拓撲**：`settle_relations` → `collect_window_signals` →
`RelationshipsStore.apply_relation_evaluation` 三段一字不動。

---

## §3 PD-1 不變量證明（0 新 LLM / 0 新事件類型 / 0 新定時器 / peer 軸逐位元不變）

PD-1（SG-3 §7：成本邊界）在本增補下**逐條守住**：

| # | 不變量 | 證明 |
|---|--------|------|
| P1 | **0 新增 LLM 呼叫** | 折抵路徑 = `open(relationships.json)` + 整數比較 + 既有純函式。`relation_settlement.py` 全檔 0 處 LLM client import（`openai` / `ollama` / `anthropic` / `asyncio` 字串 **0 命中**，見測試 `test_settlement_module_is_the_only_write_axis`）。 |
| P2 | **0 新增事件類型** | 不產生、不消費任何新 event。唯一新讀取面 = 既有 `relationships.json` 4.1 欄位。`interactions.jsonl` / `perception_trace.jsonl` 的寫入端 0 改動。 |
| P3 | **0 新增定時器** | 唯一生產掛載點仍是 `src/soul/scheduler.py:1730-1731`（`settle_relations(agent_id)`，掛既有 30s wake 的 `_goal_scan_all` per-agent 迴圈）。**本票未改 `scheduler.py` 一個字元。** |
| P4 | **0 新增寫入路徑** | 折抵本身 **0 寫**（純讀）。唯一的寫仍經 `RelationshipsStore.apply_relation_evaluation`（SG-1 §2.4 唯一寫入口）。 |
| P5 | **peer 軸（agent↔agent）逐位元不變** | 折抵僅對 `other_id == user_bryan` 一個 slot 生效（`out.setdefault(BRYAN, {})["co_presence"] += credit`），對其他 `other_id` 的 `out` 條目**零改動**。實證：`test_peer_counters_and_band_identical_with_and_without_bry`（有/無 Bry entry 兩次 settle 的 peer entry 深度相等）、`test_collect_window_signals_peer_slice_identical`、`test_no_bry_axis_write_when_agent_only_has_peers`。 |
| P6 | **門檻未動** | `src/social/relational_bands.py` 本票 **0 改動**；門檻值與 `DEMOTE_DAYS = 90` 以常量斷言 ＋ 源碼文本字面斷言雙釘（`test_threshold_constants_literal` / `test_threshold_source_text_literals`）。 |
| P7 | **Frozen Contract 0 觸碰** | Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 / SubmissionGate / SI-2.1 三道防線 / `DecisionResult` 形狀 / `DECISION_ACTIONS` 全部 0 改動；`scripts/_watchdog.ps1` 與 `src/soul/scheduler.py` 0 改動。 |
| P8 | **0 新 schema 欄位** | 冪等載體 = 既有 `objective.last_signal_at`；折抵讀 = 既有 `last_interaction_at`。entry 鍵集合與修改前一致。 |

**已知的既有副作用（登記，非本增補引入）**：`RelationshipsStore` 每次存取都會跑
`_decay_locked()`，它會把**所有** entry 的 `last_updated` 重寫成 wall clock、並依
`last_interaction_at` 重算 `confidence`。這是 SG-2 之前的既有行為，**與 Bry 折抵無關**
（任何排在 peer 之後的 other 都會造成同樣的 `last_updated` 位移；實證見
`test_last_updated_churn_is_ordering_not_bry`）。本增補**不擴大**該副作用：
`last_updated` / `confidence` 之外的 peer 欄位（objective / band / band_updated_at /
last_relation_update_ref / interaction_count / last_interaction_at）逐位元不變。

---

## §4 驗收觀測點

| 觀測點 | 位置 / 形式 | 預期 |
|--------|-------------|------|
| V1 | `[RelSettle][BAND_MIGRATION]` structured log，`other == "user_bryan"`、`direction == "promote"`、`from_band == "stranger"`、`to_band == "known"` | 新碼生效後首個含 Bry 窗內互動的結算窗出現 **1 行** |
| V2 | `relationships.json` 的 `others.user_bryan.band_updated_at` | **首次由 `null` 變為 ISO 8601 UTC**（該欄位自 SG-2 落地以來從未寫入過） |
| V3 | `others.user_bryan.objective.co_presence_sessions` / `reply_exchanges` | 由 `0` 變為 `≥1`（每窗至多 +1） |
| V4 | `others.user_bryan.objective.last_signal_at` | 首次寫入 ISO 8601 UTC |
| V5 | peer entries（`objective` / `relational_band` / `band_updated_at` / `last_relation_update_ref`） | 與新碼生效前**逐位元相同** |

**一次性重評工具**：`scripts/relband_reeval.py`（預設 dry-run 0 寫入，`--apply` 才寫）。
它**不硬寫 band 值** —— 一律經 `apply_relation_evaluation` → 純函式 `evaluate_band` 算出，
並**復用既有** `_log_band_migration`（0 新事件類型）產生 V1 的審計行。
本增補**不授權**對生產 `data/**` 執行該工具；執行時機由後續工單決定（新碼生效後）。

---

## §5 誠實記載的邊界（不修，登記）

1. **折抵是「有互動」的證據，不是「互動強度」。** `interaction_count = 243` 與
   `interaction_count = 1` 都只折抵 1 ⇒ Bry 軸升到 `familiar`（`co≥2 且 reply≥2`）
   需 **≥2 個不同結算窗**、`close` 需 **≥4 個**。這是 SGD-3 §5.1 累計制的刻意語意
  （每窗至多 1 級），不是遺漏。
2. **`last_interaction_at` 是「最後一次」，不是「窗口內有幾次」。** 24h 窗內 Bry 來了
   10 次也只折抵 1（單窗上限 1）。要改需新載體 ⇒ 屬新票範圍。
3. **時間錯位的歷史計數不會被本增補重評。** 10 個 peer entry 擱淺於
   `{reply:0, co:1}` × `band=stranger`（SG-2 舊門檻與 SG-3 新門檻之間的時間錯位，
   且 SG-2.1 要求「窗口內必須有新信號」）**不在本增補範圍**，本增補對 peer 軸 0 位移。
4. **本增補不改變 `feeling` 恒 `neutral` 的現象**（附帶發現 1，下一張票處理）。
