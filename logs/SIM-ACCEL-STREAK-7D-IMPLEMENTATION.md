# SIM-ACCEL-STREAK-7D 實作紀錄（門檻三判準 1 的模擬時鐘驗證）

> 對應工單：`logs/SIM-ACCEL-STREAK-7D-WORK-ORDER.md`（決策完備版，2026-10-20 22:37 裁示）
> 交付物：`tests/soul/test_sim_accel_streak_7d.py`（唯一新增檔）＋ 本檔
> 性質：**純測試票**。0 `src/**` 改動、0 真實 LLM、0 生產寫入。

---

## 0. 一句話結論

**門檻三判準 1 屬於 (B) 容量／機率問題，不是 (A) 純時序問題。**

再等 2 天**不會**讓它轉綠：連續 7 天的門檻不是「時間走過去」就成立，而是
**每天都要有一條新的 thread_id**（fold 語意下：新線頭的最後更新必須落在那一天），
而在 `cap=2` 之下，每天要新開一條就必須**先關閉一條** —— 關閉只可能來自
**LLM 的 `complete`／`abandon` 動作**（M2 的 advisory 不變更狀態，已實測）。
生產實測每輪關閉率 `p_close = 35/456 = 0.0768`、每輪新建率 `p_create = 0.1053`
⇒ 每日「有新建」的機率 ≈ 0.030 ⇒ **連續 7 天 ≈ 1.9e-11（≈ 1/520 億）**。

⇒ 「維持自然觀察」維持的是一個**沒有保證到達路徑**的狀態。

---

## 1. 這個問題是什麼（為什麼要模擬時鐘）

工單 §0.1 的前提（主大腦實測）與本檔的**自行復現**（見 §4）：

1. 門檻三判準 1（連續 7 天每天都有主動推進）**不成立**，最高是 mahiru 的 5 天。
2. 10 隻 agent **全部滿載**（`active=2`／`cap=2`，active 總數 20）。
3. 🔴 **時間流逝不會讓 active 線頭完成**：M2 的 age（>14 天）／idle（>7 天）判定在
   `allow_soft_archive=False`（生產值）下只產生 **advisory**
   （`target_status=active`、`should_mutate=False`）⇒ 33 天未關閉的線頭仍然 active。
4. 🔴 `active` 線頭要 `should_mutate=True` **只有**一條路：呼叫端顯式注入
   `checkpoints_exhausted=True` —— 生產從未注入。

⇒ 所以「還差 2 天」的直覺是**錯的**：時間不是那個開關。開關是**容量 ＋ LLM 的關閉決策**。

---

## 2. 平面設計（決策與理由，全部可在測試檔內對照）

| 決策 | 取值 | 理由 |
|---|---|---|
| 起點狀態 | 生產 `data/soul/<agent>/life_threads.jsonl` **時間截斷**到 `cutoff = 2026-10-21T02:30:00Z`（＝ 10/20 22:30 local，生产 10/20 22:00 槽跑完之後） | append-only ⇒ 前綴穩定 ⇒ 模擬結果**確定性**可重現；此 cutoff **複現工單 §0.1.2 的現況基準**（10/10 滿載、active=20） |
| 記憶副本投影 | `FIELDS_KEPT` ＝ 9 欄（與 SIM-TIME-001 金樣 `fields_kept` 逐字同集合，**不含** `title`／`narrative_content`） | 敘事內容不影響狀態機；避免把生產敘事帶進模擬 |
| 時間軸 | 7 個 local 日 × 2 slot ＝ 14 slot（`08:00`／`22:00` local，America/New_York），起點 10/21 08:00 | 工單 §3.2 |
| streak 日期口徑 | **UTC 日期**（§0.1.22「有推進日（UTC）」） | 夜槽 22:00 local ＝ 次日 02:00Z ⇒ 7 個 local 日 ＝ **8 個 UTC 日期** ⇒ `streak_window` 的上限是 8 |
| 假時鐘寫入面接縫 | `lt.now_utc_iso` → 回傳模擬 slot 時刻（**測試平面**注入） | M1 落盤用 `now_utc_iso()`，`apply_actions` 不收 `updated_at` 參數 ⇒ 不注入則 14 個 slot 的寫入全落在真實牆鐘的**同一個日期**，streak 計數器將無法定義 |
| 世界事件 | 生產 `data/world/perception_trace.jsonl` 中「合格」的真實事件（`accepted=True`、來源 ∈ {weather, news, news_event, calendar, calendar_event}、有 `extra.summary`、未被 `consumed_by_wake` 消費、時間落在 cutoff 前 14h），**平移**到每個模擬 slot 前 14h | 生產的世界**是活的**（每數分鐘一筆）⇒ M3 的世界碰撞路徑每個 slot 都命中。若模擬窗內沒有世界事件，`world_collision` 起源的線頭會讓 agent **完全無法行動**（prompt 不可得 ⇒ 0 LLM）⇒ 量到的會是平面假象，不是容量動態 |
| 腳本化 LLM 劇本 A | 有到期線頭 ⇒ 關閉其中**最舊**的一條（`op=complete`）；**僅在「本輪開始時有容量空位」**（`active < cap`）時建立一條新線頭 | 「最舊」＝ `(created_at, thread_id)` 最小且限於**到期集合**（M4 輸出契約規則 4）；建立判斷用**本輪開始前**的 active 數（與 M4 prompt 容量區塊同口徑：「只有 active < cap 時才可以 op=create」） |
| 腳本化 LLM 劇本 B | 只回 `op=advance`（最舊的到期線頭）；**不新建、不關閉** | 工單 §3.2 的反事實 |
| `next_check_hours` | 24（生產實測區間 20~28 小時） | **不催熟**：新建線頭要滿 24 小時才到期 |
| 0 真實 LLM | 注入式 `ScriptedLLM` ＋ 真 proxy 探針（命中即紅）＋ `socket.create_connection` 攔阻 | 工單 §3.1 |
| 0 生產寫入 | 全部寫入落在 pytest `tmp_path`；生產 `data/**` 全程唯讀 | 工單 §3.1 ＋ AGENTS.md 行程安全鐵律 8 |

**跨檔 import 的取捨**：`tests/soul/` 無 `__init__.py` ⇒ **不**跨檔 import 另一個測試模組
（模組名解析依賴 pytest rootdir 插入順序，且會與 SIM-TIME-001 的 autouse 平面不變量糾纏）⇒
本檔**複製最小必要 primitive**（與 `tests/soul/test_life_thread_sim_m2_writeback.py` 同註記）。

**唯一的效能注入**：`lt._load_default_config` 換成 memo 版本（避免每輪重讀
`configs/default.yaml` ＋ dotenv）。**回傳值逐位元相同**（同一份 YAML），0 決策差異。
不注入時單輪模擬約 10 秒（14 slot × 10 agent），注入後約 4 秒。

---

## 3. 兩劇本的結果（逐 slot 明細見 pytest 報告）

### 3.1 劇本 A（每天新建一條線頭 ＋ 自主收束最舊的到期線頭）

* 14 個 slot、10 隻 agent、每次喚醒都由腳本化 LLM 回答（`WORLD_COLLISION_WAKE`）。
* **每隻 agent 新建 8~9 條**（mai 8、其餘 9）、**關閉 9~10 條**、
  **建立失敗（容量飽和）＝ 0 slot**、**容量死鎖（連續 ≥3 slot 無新建）＝ 0 段**。
* **標題多樣性：89／89 ＝ 1.0000**（分子＝不同標題數 89 個／分母＝新建線頭數 89 條）；
  每隻 agent 各自 9／9（mai 8／8）⇒ 重複標題 0 個。
* **streak 計數器逐 local 日（窗口口徑）＝ D1:2 D2:3 D3:4 D4:5 D5:6 D6:7 D7:8**（mai 末值 7）。
  ⇒ **第 7 天全部 10 隻 agent 都達 7**（9 隻頂到窗口上限 8、mai 為 7）。
  ⇒ canonical 口徑（`streak_all`，含歷史）第 7 天＝8（mai 7）。
* **唯一的例外（誠實記錄）**：`agent_mai` 錯過第 1 天 —— 它在第 1 個 slot 沒有到期線頭
  ⇒ 無法關閉 ⇒ 池仍滿 ⇒ 建立被容量規則擋下 ⇒ 第一次新建落在第 2 天。
  這是**喚醒時序**造成的，不是容量不足。

### 3.2 劇本 B（每天只推進既有線頭；反事實）

* **新建 0 條、關閉 0 條、推進 14 條／agent**（每個 slot 推進一條到期線頭）。
* **streak_window ＝ 2（不變）、streak_all ＝ 2（mahiru 5，來自歷史）⇒ 全員 < 7 ⇒ 不轉綠。**
* 🔴 **這是本票最重要的結構性發現**：門檻三的 streak 計數器建立在 **fold** 上，
  而 fold 只保留每條線頭的**最後一次**更新 ⇒ 「反覆推進同一條線頭」會把所有天的
  證據壓到**最後一天** ⇒ 連續天數恆為 1~2。
  ⇒ **判準 2 的「每天不同 thread_id」不是多餘條件，而是判準 1 的必要條件**（已由反事實證明）。

---

## 4. 前提復現（工單 §0.1 的數字，本檔**自行重推**）

| 項目 | 工單轉錄 | 本檔實測 | 說明 |
|---|---|---|---|
| 起點滿載 agent 數 | 10／10（active=2／cap=2） | **10／10** ✓ | 一致 |
| 起點 active 總數 | 20 | **20** ✓ | 一致 |
| 最高 streak（起點） | mahiru 5 天 | **mahiru 5 天** ✓ | 一致（日期集合 10-03~10-07 連續 5） |
| age ≈ 33 天的 active 線頭 | 「6 條」 | **8 條／7 隻 agent**（不含 yua 為 6 條） | ⚠️ **差異**：本檔用 `created_at` 與 cutoff 算 age，得到 anna/aoi/miku/ram/rem/ruka 各 1 條 ＋ **yua 2 條**＝8 條；若排除測試 agent `agent_yua` 則恰為 6 條，與工單一致。**不影響**任何設計決策（容量基準與 streak 基準都完全複現） |
| 生產線頭總數（active/completed） | 20／35 | 20／**35** ✓ | 與 §3.4 的關閉輪數 35 互相對得上 |

---

## 5. 四項量測（分子／分母／單位）

| 量測 | 分子 | 分母 | 單位 | 劇本 A | 劇本 B |
|---|---|---|---|---|---|
| ① 容量動態：新建 | 8~9 條／agent | 14 slot | 條 | 新建 8~9、關閉 9~10、**容量飽和失敗 0 slot**、**死鎖 0 段** | 新建 0、關閉 0 |
| ② 標題多樣性 | 89（不同標題數） | 89（新建線頭數） | 個／條 | **1.0000**（重複 0 個） | N/A（0 條新建） |
| ③ streak 計數器 | 最長連續 UTC 日數 | — | 天 | 第 7 天＝**8**（mai 7）⇒ **達 7** | 第 7 天＝**2**（mahiru 5）⇒ **不轉綠** |
| ④ 反事實對照 | 劇本 A 第 7 天 streak_window | 劇本 B 同值 | 天 | 8 vs **2** ⇒ 判準 2 必要 | — |

（每隻 agent 的逐日值見 pytest 報告 `【量測 3】` 段。）

---

## 6. 容量動態模型 ＋ 機率表（§3.4）

### 6.1 解析模型（cap=2、起點 active=2、每天新建 1 條、每天關閉 1 條）

| 動作次序 | 第一次「空位不足」 | 代價 |
|---|---|---|
| **create-first**（M4 prompt 的容量口徑：LLM 依**本輪開始前**的 active 數判斷能不能建立） | **第 1 天 08:00 slot**（＝**起點即飽和，第一個 slot 就缺位**） | 建立會被 M1 的容量強制點拒絕 |
| **close-first**（同一輪先關後建） | **從未發生** | 但代價是**每個 agent 每天都要有一次 LLM 關閉決定** |

⇒ 關鍵不是「能不能建」，而是**誰決定先關**。生產 M4 契約的容量區塊明寫
「只有 `active < cap` 時才可以 `op=create`」⇒ 真實路徑是 **create-first** ⇒
**每天的第一次建立都會被擋下，直到前一天的關閉騰出空間**。

### 6.2 生產實測的決策基率（唯讀量測；分輪口徑＝ M4 `current_slot()`）

* `p_close`（每輪有閉閉的比率）＝ **35／456 ＝ 0.0768**
* `p_create`（每輪有新建的比率）＝ **48／456 ＝ 0.1053**
* 事件級關閉率 ＝ **35／678 ＝ 0.0516**
* 與 §4 對照：關閉輪數 35 ＝ 生產 completed 線頭數 35 ✓

### 6.3 機率表（每天 1 次關閉嘗試、成功率 p、7 天）

| p | P(7 天內至少 1 次關閉)＝1−(1−p)^7 | P(至少 6 次)＝Σ_{k≥6} C(7,k)p^k(1−p)^(7−k) |
|---|---|---|
| 0.2 | 0.7903 | 0.0004 |
| 0.4 | 0.9720 | 0.0188 |
| 0.6 | 0.9984 | 0.1586 |
| 0.8 | 1.0000 | 0.5767 |
| 1.0 | 1.0000 | 1.0000 |

**代入生產實測值**（每天 2 個 slot ＝ 2 次嘗試）：

* 每日至少一次關閉：`1−(1−0.0768)²` ＝ **0.1477**
* 每日至少一次新建：`1−(1−0.1053)²` ＝ **0.1995**
* 每日「先關後建」同時發生（**假設兩者獨立**，此為本模型的唯一假設）＝ 0.1477 × 0.1995 ＝ **0.0295**
* **連續 7 天 ≈ 0.0295⁷ ≈ 1.9e-11（≈ 1/520 億）**
* 即使放寬成「每天 1 次嘗試、p=0.0768」：`P(≥6 次關閉)` ＝ **1.3e-6**

---

## 7. 結論：(B) 容量／機率問題

### 7.1 三條可量測的證據（不是推論）

1. **容量**：起點 10／10 滿載（`active=cap=2`）⇒ 任何新線頭都必須先有一個被關閉的槽位。
2. **時間軸不會收束**：對起點狀態直接跑 M2 決策層（生產政策值
   `max_active_duration_days=14`、`stale_check_threshold_days=7`、`allow_soft_archive=False`）
   ⇒ age>14 天的 active 線頭全部得到 **advisory**（`should_mutate=False`、
   `target_status=active`、`extra_metadata.advisory=True`）⇒ **時間流逝不會讓它們完成**。
3. **關閉是決策**：模擬期間每一次狀態轉移，都對應到腳本化 LLM 的一個 `complete` 動作
   （`transitioned ⊆ stub 的 complete 動作集合`）⇒ **沒有任何自動路徑會關閉線頭**。

### 7.2 要達成「連續 7 天」的最小條件

1. 每個 local 日至少 1 條**新**線頭（fold 語意：新線頭的最後更新落在該 UTC 日）
   —— 否則 streak 計數器不動（劇本 B 實測：恆為 2）。
2. `cap=2` ⇒ 每天新開 1 條就必須**先關閉 1 條**（否則第 2 天起池子滿 ⇒ 建立被拒）。
3. 關閉只能由**喚醒後的 LLM 決定**（M2 advisory 不變更狀態；`checkpoints_exhausted`
   生產從未注入）。
4. 喚醒本身還需要條件（到期線頭或世界碰撞）⇒ 劇本 A 實測：9／10 隻 agent 每個 slot 都醒，
   `agent_mai` 因首個 slot 沒有到期線頭而錯過第 1 天。

⇒ **最小條件 ＝「連續 7 天，每天至少一次 LLM 關閉決定成功（且當天有新建）」。**

### 7.3 當前是否成立：**不成立**

* 生產實測 `p_close`＝0.0768／輪、`p_create`＝0.1053／輪 ⇒ 每日成功機率 ≈ 0.0295
  ⇒ 連續 7 天 ≈ 1.9e-11。
* 生產至今仍有 8 條 age≈33 天的 active 線頭（33 天裡沒有一次自主收束）。
* 生產最近兩個槽（**本檔唯讀實測**，分母＝該槽內 `updated_at` 落在槽時窗內的事件）：

  | 生產槽 | 推進（`updated`）事件 | 有動作的 agent | **新建** | **關閉** |
  |---|---|---|---|---|
  | 10/20 08:00 local（`2026-10-20T12:00Z`） | **9** | **7** | **0** | **0** |
  | 10/20 22:00 local（`2026-10-21T02:00Z`） | **13** | **9** | **0** | **0** |

  ⇒ 池滿時，生產的 LLM **只推進、不關閉、更不新建** —— 與本票的容量模型完全一致
  （⚠️ 工單 §0.1.22 ⑥ 轉錄為「8 隻 agent 有推進」；本檔以事件時間窗重算得 7 隻
  （10/20 08:00）／9 隻（10/20 22:00）。數字差異不影響任何設計決策。）

⇒ **建議（供主大腦／Owner 裁示，本票不自行改政策）**：若要讓門檻三判準 1 有到達路徑，
必須新增一條**確定性**的收束路徑（例：M2 在 `allow_soft_archive=True` 下真的收束，
或 M1 在池滿時允許「關一開一」的原子替換），否則門檻三判準 1 在現狀下
**不是「再等 2 天」就會成立的事**。

---

## 8. 變異實驗（每條都必須轉紅；環境變數 `SIM_ACCEL_STREAK_7D_MUTATION`）

執行方式（每條一次獨立 pytest 行程）：

```powershell
$env:SIM_ACCEL_STREAK_7D_MUTATION='<1..5>'
.venv\Scripts\python.exe -m pytest -q --tb=line tests/soul/test_sim_accel_streak_7d.py
```

| # | 變異 | 目標守衛（轉紅者） | 紅燈實測輸出 |
|---|---|---|---|
| 1 | streak 計數器改成「距今天數」（§0.1.22 已作廢口徑） | **守衛 5** `test_streak_counter_is_not_days_since_last_update` | `AssertionError: streak 計數器回傳 30（預期 1；得到 30 ⇒ 復辟了已作廢口徑）`；連帶 **守衛 4** `test_streak_counter_hand_computed` 也紅（`案例 1 的 streak＝14（預期 3）`）、`test_start_state_is_all_saturated`（`mahiru 起點 streak_all＝0（預期 5）`）、`test_scenario_a_...`（`第 7 天 streak_window 達 7 的 agent＝0／10`）⇒ **4 failed, 6 passed** |
| 2 | 容量檢查失效（`lt.capacity` → 99） | 容量動態量測 `test_start_state_is_all_saturated` ＋ `test_scenario_a_...` ＋ `test_conclusion_...` | `AssertionError: 起點未全部滿載：[('agent_akane', 2, 99), …, ('agent_yua', 2, 99)]`；`AssertionError: agent_akane 的新建線頭數＝14 條（預期 7~10 條／7 天）` ⇒ **3 failed, 7 passed** |
| 3 | 腳本化 LLM 漏接某個 slot（第 4 個 slot 改注入 `llm_caller=None`） | **守衛 2** `test_no_real_llm_and_no_sleep` | `AssertionError: 決策路徑碰到真實 LLM proxy：[{'via': '_find_llm_proxy'} × 20]`；同時 20 筆 `fail-closed: AssertionError: …變異 3：真實 LLM proxy 路徑被觸及（硬攔，連外不可能發生）` ⇒ **1 failed, 9 passed**（**0 次連外**） |
| 4 | memory fork 寫回「生產」（隔離失效） | **守衛 3** `test_production_untouched` | `AssertionError: 生產生活線頭日誌被改動：新增=[] 消失=[] 變動=['agent_akane/life_threads.jsonl', …, 'agent_yua/life_threads.jsonl']`（10 個檔案全變動）⇒ **1 failed, 9 passed** |
| 5 | 標題多樣性統計漏掉重複計數（分母被換成集合大小） | **守衛 4** `test_streak_counter_hand_computed` | `assert div["distinct"] == 3 and div["total"] == 4 and div["ratio"] == 0.75` → `E assert (3 == 3 and 4 == 4 and 1.0 == 0.75)` ⇒ **1 failed, 9 passed** |

**5/5 條變異全部轉紅**，且每條都由**指定的守衛測試**（加粗者）紅燈。

**執行安全（AGENTS.md 鐵律 7）**：凡「移除某道閘門」型變異，被解除防護的程式碼
**不可能真的執行**——

* 變異 3：真 proxy 探針在命中當場 `raise` ⇒ 連外不可能發生（且 `socket.create_connection` 全程攔阻）；
* 變異 4：被寫入的「生產」是 `tmp_path` 內的**沙盒副本**（真生產零寫入），
  且記憶副本的**來源**永遠是真生產（唯讀）；
* 變異 1／2／5：純計算／純容量參數，無任何外部觸達。

**變異 2 的偏差（誠實記錄）**：工單預期「死鎖數必須改變」，但實測**死鎖數在變異前後都是 0**
（死鎖的定義是「連續 ≥3 個 slot 沒有建立新線頭」，而建立節奏由**劇本腳本**決定、不是由 cap 決定）。
實際改變的是：起點 `cap`（2→99）、每 agent 新建數（8~9→14）、以及「模擬期間曾滿載」這個斷言
（`active == cap` 不再成立）⇒ 容量動態量測確實整組改變，**守衛不是盲腸**。

---

## 9. 測試結果

* 指令：`.venv\Scripts\python.exe -m pytest -q tests/soul/test_sim_accel_streak_7d.py`
* **解譯器：repo 的 `.venv`（Python 3.11）**（AGENTS.md 鐵律 10：引用數字必須聲明解譯器）
* 本檔：**10 passed**（收集 10 支／0 error）；單輪模擬約 4 秒，整模組約 40 秒。
* **完整報告（14 slot 逐 slot 明細 ＋ 4 項量測 ＋ 模型與機率表）**：

  ```powershell
  .venv\Scripts\python.exe -m pytest -q -s tests/soul/test_sim_accel_streak_7d.py::test_report
  ```

  （`-s` 關掉輸出捕獲；報告由 `render_report()` 印出，內容＝本檔 §3／§5／§6 的完整版。）
* 全庫回歸：見 §11。

### 9.1 全庫回歸（`.venv`，與其他 worker 並行時執行）

```
13 failed, 6732 passed, 24 skipped, 27 subtests passed in 742.64s (0:12:22)
```

**13 筆失敗全部與本票無關**（逐筆歸因，每筆都有實測輸出）：

| 失敗 | 紅燈實測 | 歸因 |
|---|---|---|
| `test_observability_completeness_1.py::…::test_no_frozen_contract_file_touched` | `src/ 改動超出許可集: {'src/llm/proxy.py', 'src/inner_life/recall_prompt.py'}` | **RECALL-30D worker 的在途 src 改動**（本票 0 `src/**` 改動） |
| `test_life_thread_origins_m4.py::test_legacy_git_grep_false_green_is_fixed` | `出現白名單外的 M4 引用：scripts/sim_accel_thread_cli.py:511` | **THREAD-DISSOLVE worker 的新腳本**（本票未動 `scripts/**`） |
| `test_sim_accel_recall.py`（7 筆）、`test_recall_prompt_injection.py`（1 筆） | `ValueError: node '…' is already SUPERSEDED (terminal)` 等 | **RECALL-30D worker 的在途測試** |
| `test_life_thread_origins_m4.py::test_module_is_not_wired_into_production_paths`、`…::test_module_declares_m5_wiring…`、`test_life_threads_m1.py::test_s2_1_module_not_imported_by_production_paths` | 單跑皆 **passed**（並行全庫跑時失敗 ＝ 執行當下工作樹正被其他 worker 改寫） | **並行工作樹變動的瞬態**，非本票 |

**針對性複核（本票相關面）**：

```
.venv\Scripts\python.exe -m pytest -q tests/soul tests/infra
⇒ 1 failed, 1323 passed in 92.75s
```

唯一那 1 筆失敗就是上表第 2 列（`scripts/sim_accel_thread_cli.py` 的白名單），
**與本票檔案無關**；本票新增的 `tests/soul/test_sim_accel_streak_7d.py`
在 `tests/soul` ＋ `tests/infra` 內 **10/10 全綠**。

---

## 10. 生產 sha256 前後對帳

* 對象：生產 `data/soul/**/life_threads.jsonl`（10 個檔案）的逐檔 sha256 ＋ 聚合 sha256。
* 測試內部守衛（`test_production_untouched`）：模擬前後**逐檔相同**（本檔 10 支全綠）。
* 本次工作階段的獨立對帳（`Get-FileHash`，工作階段**前後各算一次**）：

```
工作階段開始：aggregate_sha256 = a31dd3f9c00afc0d3a1434f5fa4f1525e6c7dbc28598aee587508f1ade51e59e
工作階段結束：aggregate_sha256 = a31dd3f9c00afc0d3a1434f5fa4f1525e6c7dbc28598aee587508f1ade51e59e
⇒ 一致（10 個檔案；聚合值＝各檔 sha256 文字串依路徑排序後再取 sha256）
```

* `git status` 對帳：本票只新增 `tests/soul/test_sim_accel_streak_7d.py` 與
  `logs/SIM-ACCEL-STREAK-7D-IMPLEMENTATION.md`（**已 `git add` 進 index，未 commit**；
  原因：repo 自身的 `test_every_collectable_test_file_under_tests_is_git_tracked`
  守衛要求 `tests/**` 的可收集測試檔必須在 git index 內，否則全庫回歸會紅）。

---

## 11. Frozen Contract / 意外行為

* **Frozen Contract：0 觸碰**。本票 0 `src/**` 改動；Agency 4 stages／TriggerEnvelope／
  InnerLifeEvent／4 handlers／SAGE 寫入邏輯全部未動。
* **生產資料：0 寫入**（見 §10）。`data/**` 全程唯讀。
* **0 真實 LLM 推理**、0 連外（socket 全程攔阻）、0 `time.sleep`、0 伺服器啟動、0 行程操作。
* **未觸發** synthetic diary／dream；未執行任何 `scripts/manual_*.py`。
* **意外行為（已觀察、非本票造成）**：
  1. `agent_mai` 的第 1 個模擬 slot 沒有到期線頭 ⇒ 首日無法新建（喚醒時序造成的缺口）。
  2. 引擎在「LLM 回空動作」時會記 `LLM 失敗或非 JSON，本時段不落盤` 的 warning
     —— 這是 M4 對空 `actions` 的既有日誌語意，非本票引入。

---

## 12. 已知限制（誠實揭露）

1. **起點狀態依賴生產前綴**：若有人重寫（而非追加）cutoff 之前的生產列，模擬結果會變
   ⇒ 起點狀態的守衛（`test_start_state_is_all_saturated`）會轉紅（這是刻意的可觀測性）。
2. **世界事件是平移的**（真實事件、位移到模擬窗）⇒ 模擬窗內的「世界密度」沿用生產最後 14 小時；
   若生產世界活動性大幅改變，喚醒時機的分布會跟著變（容量結論不受影響，因為結論不依賴喚醒頻率）。
3. **腳本化 LLM 是「順從型」**：劇本 A 假設 LLM 願意自主收束；真實 LLM 的關閉率是
   0.0768（§6.2）⇒ 劇本 A 是**上界**，生產實況遠低於此。
4. **CLI 沙盒快跑不適用**：本票 0 `src/**` 改動 ⇒ `scripts/eval_lifecycle_cli.py` 的
   計算結果逐位元不變（生命週期／昇華／投影面未被觸及）；本票的等價保證是
   「生產 `data/**` sha256 逐位元不變」。
5. 本票**沒有**改 cap、沒有改門檻、沒有催熟線頭、沒有調大任何數值來讓結論好看；
   「不轉綠」與「轉綠」兩種結果都照實回報。

---

## 附錄 B：沙盒紀律稽核（2026-10-20 23:34 RECALL-30D 污染事故的教訓）

主大腦緊急通知：另一票的變異外掛把沙盒 CLI 的 `copy_graph` 換成**硬連結**，
造成「沙盒＝來源 inode」⇒ append 直接寫進生產（`data/elevation/**` 污染 805 筆假事件）。

### B.1 本檔的三條複製路徑 —— 全部產生**獨立 inode**（0 硬連結）

| 路徑 | 實作 | inode | 產物 |
|---|---|---|---|
| ① 記憶副本（life_threads） | `read_text` → `json.loads` → **重新序列化** → `write_text` | **全新**（不是位元組複製，更不是硬連結） | `tmp_path/sim_root/soul/<agent>/life_threads.jsonl` |
| ② 世界事件（perception trace） | 同上（讀進記憶體 → `write_text`） | **全新** | `tmp_path/sim_root/world/perception_trace.jsonl` |
| ③ 變異 4 的沙盒副本 | `dst.write_bytes(src.read_bytes())` | **全新**（位元組複製） | `tmp_path/prod_sandbox/soul/**` |

* 本檔**沒有任何** `os.link`／硬連結／`shutil.copy2`（`grep os.link|hardlink|shutil` ＝ 0 命中）。
* 所有引擎寫入只經由 `SOUL_OS_DATA_DIR` 導向 `tmp_path`（正常）或 `tmp_path` 內的沙盒（變異 4）。
* 生產檔**只被讀**（`PROD_SOUL_DIR_REAL`），從不被 append。

### B.2 新增的 **inode 共享斷言**（把不變量寫成會紅的斷言）

`test_production_untouched` 內新增：

```python
assert not os.path.samefile(prod_file, fork_file)   # 沙盒副本不得與生產檔共用 inode
```

（`os.path.samefile` 在本機 Windows 實測：同檔＝True、位元組複製＝False。）

### B.3 每條變異的**獨立**（工具層）生產摘要前後對帳

摘要由 `Get-FileHash`（工具層，**不是**測試內的守衛）前後各算一次：

| 變異 | pytest 結果 | `data/soul/**/life_threads.jsonl`（10 檔） | `data/elevation/**`（5 檔） |
|---|---|---|---|
| baseline | 10 passed | **SAME**（`a31dd3f9c00afc0d…` → `a31dd3f9c00afc0d…`） | **SAME**（`1147474bcbeda7a0…` → `1147474bcbeda7a0…`） |
| 1 | 4 failed, 6 passed | **SAME** | **SAME** |
| 2 | 3 failed, 7 passed | **SAME** | **SAME** |
| 3 | 1 failed, 9 passed | **SAME** | **SAME** |
| 4 | 1 failed, 9 passed | **SAME** | **SAME** |
| 5 | 1 failed, 9 passed | **SAME** | **SAME** |

工作階段最終對帳（canonical 聚合口徑）：`a31dd3f9c00afc0d3a1434f5fa4f1525e6c7dbc28598aee587508f1ade51e59e`
＝ **與工作階段開始完全相同**。

### B.4 觀察到（**只回報，未觸碰**）

唯讀複核發現 RECALL-30D 事故的污染**確實存在**於生產：
`data/elevation/elevation_lifecycle_events.jsonl`（369,272 bytes／1,011 行，其中 **805 筆**帶
`sim_accel_supersede_g0` 或 `2026-11-20` 的假事件）與
`elevation_nodes.jsonl`（387,297 bytes；與主大腦所述「乾淨前綴 386,472 bytes ＋ 825 bytes」吻合）。
⇒ **本票未觸碰 `data/elevation/**` 任何檔案**（本票的寫入面只有
`soul/<agent>/life_threads.jsonl` 與 `world/perception_trace.jsonl`，且全在 `tmp_path` 內），
截斷還原與 Owner 授權屬主大腦／Owner 裁決範圍，本票不動。

---

## 附錄 A：14 個 slot 逐 slot 聚合明細（兩劇本）

> 完整逐 agent 明細（每個 slot × 每隻 agent 的建立／關閉／推進／失敗／active／streak／
> 新 thread_id／標題）由 `pytest -q -s …::test_report` 印出；下表是同一次執行的聚合版。

| slot | local | UTC 日期 | 喚醒 | 有新建的 agent | 有關閉的 agent | 有推進的 agent | streak_window 分布 |
|---|---|---|---|---|---|---|---|
| **劇本 A** | | | | | | | |
| 01 | 10-21 08:00 | 2026-10-21 | 10/10 WORLD_COLLISION_WAKE | 0 | 9 | 0 | {1} |
| 02 | 10-21 22:00 | 2026-10-22 | 10/10 | 9 | 10 | 0 | {2} |
| 03 | 10-22 08:00 | 2026-10-22 | 10/10 | 10 | 1 | 0 | {1,2} |
| 04 | 10-22 22:00 | 2026-10-23 | 10/10 | 1 | 9 | 0 | {2,3} |
| 05 | 10-23 08:00 | 2026-10-23 | 10/10 | 9 | 10 | 0 | {2,3} |
| 06 | 10-23 22:00 | 2026-10-24 | 10/10 | 10 | 1 | 0 | {3,4} |
| 07 | 10-24 08:00 | 2026-10-24 | 10/10 | 1 | 9 | 0 | {3,4} |
| 08 | 10-24 22:00 | 2026-10-25 | 10/10 | 9 | 10 | 0 | {4,5} |
| 09 | 10-25 08:00 | 2026-10-25 | 10/10 | 10 | 1 | 0 | {4,5} |
| 10 | 10-25 22:00 | 2026-10-26 | 10/10 | 1 | 9 | 0 | {5,6} |
| 11 | 10-26 08:00 | 2026-10-26 | 10/10 | 9 | 10 | 0 | {5,6} |
| 12 | 10-26 22:00 | 2026-10-27 | 10/10 | 10 | 1 | 0 | {6,7} |
| 13 | 10-27 08:00 | 2026-10-27 | 10/10 | 1 | 9 | 0 | {6,7} |
| 14 | 10-27 22:00 | 2026-10-28 | 10/10 | 9 | 10 | 0 | {7,8} |
| **劇本 B** | | | | | | | |
| 01 | 10-21 08:00 | 2026-10-21 | 10/10 | 0 | 0 | 9 | {1} |
| 02 | 10-21 22:00 | 2026-10-22 | 10/10 | 0 | 0 | 10 | {2} |
| 03 | 10-22 08:00 | 2026-10-22 | 10/10 | 0 | 0 | 10 | {1} |
| 04 | 10-22 22:00 | 2026-10-23 | 10/10 | 0 | 0 | 10 | {2} |
| 05 | 10-23 08:00 | 2026-10-23 | 10/10 | 0 | 0 | 10 | {1} |
| 06 | 10-23 22:00 | 2026-10-24 | 10/10 | 0 | 0 | 10 | {2} |
| 07 | 10-24 08:00 | 2026-10-24 | 10/10 | 0 | 0 | 10 | {1} |
| 08 | 10-24 22:00 | 2026-10-25 | 10/10 | 0 | 0 | 10 | {2} |
| 09 | 10-25 08:00 | 2026-10-25 | 10/10 | 0 | 0 | 10 | {1} |
| 10 | 10-25 22:00 | 2026-10-26 | 10/10 | 0 | 0 | 10 | {2} |
| 11 | 10-26 08:00 | 2026-10-26 | 10/10 | 0 | 0 | 10 | {1} |
| 12 | 10-26 22:00 | 2026-10-27 | 10/10 | 0 | 0 | 10 | {2} |
| 13 | 10-27 08:00 | 2026-10-27 | 10/10 | 0 | 0 | 10 | {1} |
| 14 | 10-27 22:00 | 2026-10-28 | 10/10 | 0 | 0 | 10 | {2} |

**讀法**：

* 劇本 A：偶數 slot（夜槽）＝「池有位 ⇒ 建立」；奇數 slot（晨槽）＝「池滿 ⇒ 只能關閉」。
  `next_check_hours=24` 讓「關閉 ⇒ 24 小時後到期 ⇒ 再被喚醒」形成日節奏。
  第 1 個 slot 只有 9 隻 agent 有到期線頭（`agent_mai` 沒有）⇒ 池仍滿 ⇒ 首次建立延到第 2 個 slot。
* 劇本 B：**新建 0、關閉 0、每 slot 推進 10 隻 agent**，`streak_window` 只在 1↔2 間震盪
  ⇒ **反覆推進既有線頭對 streak 計數器完全無效**（fold 只留每條線頭的最後更新）。