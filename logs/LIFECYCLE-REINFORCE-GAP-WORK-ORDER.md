# 工單：LIFECYCLE-REINFORCE-GAP — 生命週期老化鏈的三處量測假綠 ＋ 靈魂型節點「跨日支持＝0」的機制缺口

- **日期**：2026-10-22 12:4x EDT（夜槽 22:00 EDT 前 9.2 小時）
- **觸發**：今晚夜槽值守前的機械複核（11:30 EDT），以及隨後三小時的追根
- **狀態**：**量測缺陷三處已修完並雙向驗證**；**機制缺口已定案、未開工**（待 Owner 裁示定性與範圍）
- **本工單不動**：`src/**`、`personas/**`、`configs/**`、`data/**`（唯讀）、任何生產行程、任何服務重啟
- **量測口徑**：所有數字均由 repo 的 `.venv\Scripts\python.exe` 產出；邊／節點檔皆為**唯讀**；CLI 一律 sandbox-only（唯一寫入目標＝自建 temp 沙盒）

---

## 0. 摘要

本回合產出三組結論，重要性依序遞增：

1. **三處量測缺陷會讓對帳報「假綠」** —— 已修完、已用雙向變異實驗證明守衛會轉紅。
2. **機制缺口**：`soul_elevation.engine.reinforce()`（引擎裡「讓既有信念保活」的正式轉換）在**生產零呼叫者** ⇒ 靈魂型節點（value／belief／trait）的支持邊**永遠凍結在出生時刻** ⇒ 它們**必然**在出生＋7 天弱化，與這個 agent 是不是還在過日子**無關**。
3. **今晚 22:00 EDT 生產會真的發生 1 筆 belief 轉弱化、弱化包裝 0 → 1**（`agent_mahiru`）。⇒ 這**不是**「收割型機制的正常收斂」，而是第 2 點的**第一個可觀察症狀**。

> ⚠️ 本工單**不**自行決定第 3 點的定性與處置（涉及 Owner 明訂的紅線「弱化包裝維持 0」），故列為 §4 待裁示。

---

## 1. 量測缺陷（已修，三條皆雙向驗證）

### 缺陷 A：naive 評估時戳 ⇒ TypeError ⇒ fail-closed ⇒ 「假綠 PASS」

**因果鏈（全部實測，非推論）**：

| 環節 | 事實 |
|---|---|
| 觸發 | `%TEMP%\night_audit.py`／`morning_audit.py` 以 `CUT = slot_utc.strftime("%Y-%m-%dT%H:%M:%S")`（**naive 無時區**）傳 `--simulated-time` |
| 引擎 | `soul_elevation.engine._days_between` 拿 offset-aware 的 anchor 減 offset-naive 的 now ⇒ `TypeError: can't subtract offset-naive and offset-aware datetimes` |
| 吞例外 | `evaluate_lifecycle_all` 是 fail-closed 的（`elevation_adapter.py:1556-1563`：`except Exception` → warning → 回 `[]`） |
| 量測 | 「0 筆新轉變 ＋ 圖非空」⇒ CLI 判成**冪等 no-op** ⇒ **`VERDICT: PASS`** |

**對照實測（同一張生產圖譜，同一支 CLI）**：

| `--simulated-time` | 結果 |
|---|---|
| `2026-10-23T02:00:00`（naive） | sidecar 225 → **225**（0 筆）＋ `VERDICT: PASS` ＋ stderr 有 TypeError |
| `2026-10-23T02:00:00Z`（tz-aware） | sidecar 225 → **249**（**24 筆**）＋ `VERDICT: FAIL` |

⇒ **晨槽報告第 202–209 行的「沙盒重跑 225→225 VERDICT PASS」是假綠**（真值為 24 筆新轉變、含 1 筆 belief）。

**修法（移除能力，不是補判斷）**：
- `scripts/eval_lifecycle_cli.py` 新增 `_parse_simulated_time()`：**naive 時間戳在評估開始前硬拒**（exit 2、印明確原因、**不產任何 VERDICT**）。⇒ CLI 結構上不可能再進入「0 筆 ⇒ PASS」這條路。
- `Z` 與 `+00:00` 兩種形式都接受。

### 缺陷 B：關鍵反例比對用 `==` ⇒ 守衛 ③ 在真實資料上完全空轉

- `DEFAULT_KEY_NODE = "6a92c906b245"` 是 **12 字元前綴**；真實事件的 `node_id` 是 **32 字元完整 id**（`6a92c906b245458cb0c4c64ea214f2eb`）。
- 舊碼 `e.get("node_id") == key_node` ⇒ **恆為 False** ⇒ 守衛 ③「關鍵反例不得被轉」**從未生效過**。
- 既有單元測試餵的是假 id `"KEY"`（精確比對成立）⇒ **測試綠但守衛空轉**。
- **端到端實證**（修好後，拿生產圖譜跑 `2026-10-25T02:00:00Z`）：`key_node_transitions = 1` ⇒ 守衛 ③ 正確觸發。**舊守衛在這裡會報 0 —— 漏報真紅燈。**

**修法**：比對改為**前綴語意**（任一方向為另一方之前綴即命中），不影響精確相等。

### 缺陷 C：`⇒ 紅線 ③ 總判定：PASS` 是硬編碼

`night_audit.py:488` 無條件印 PASS —— 即使 CLI 回 FAIL 也照印。⇒ **修法**：總判定改由**實測證據**計算（轉變純度 0 ＋ 關鍵反例維持 `active` ＋ 關鍵反例無 sidecar 事件 ＋ CLI `VERDICT: PASS`），並**逐條拆解印出**每一條判準的實際值。

### 修法總表

| 檔案 | 改動 | 狀態 |
|---|---|---|
| `scripts/eval_lifecycle_cli.py` | ① naive 時戳硬拒 ② key-node 前綴比對 ③ 評估例外可視（`logging` 捕捉 fail-closed 警告 → `eval_error` → 強制 FAIL）④ **轉變未落地的結構性檢查** | 已修 |
| `tests/test_eval_lifecycle_cli.py` | 新增 3 個守衛類、**12 項**新測試 | 已修 |
| `%TEMP%\night_audit.py` | tz-aware 時戳 ＋ returncode 每次必現 ＋ 紅線 ③ 改為實測計算 | 已修（唯讀量測工具，不在 repo） |
| `%TEMP%\morning_obs\morning_audit.py` | 同上 | 已修 |

**③ 為何必要**：`evaluate_lifecycle_all` 的 fail-closed 使「評估失敗」與「真的冪等 no-op」在回傳值上**長得一模一樣**（都是 0 筆）。⇒ 沒有 `eval_error`，CLI 就會對「根本没評估」報 PASS。

**④ 為何必要（同一類漏洞的第三個出口）**：`evaluate_lifecycle_all` 回傳了 events、但 `_append_jsonl` 寫入失敗時（adapter 只發 warning、**不回 0**），sidecar 增量會是 0 ⇒ 舊邏輯同樣判成「冪等 no-op = PASS」。⇒ 修法是**結構性比對**：`undelivered = len(events) − len(new_events) > 0` ⇒ 直接 FAIL。**不靠字串比對日誌。**

⇒ 綜合起來，「0 筆新轉變」的**三種成因**（圖為空／真冪等 no-op／評估拋例外或未落地）在 CLI 裡已被**完全區分**，不再有任何一條能走到 PASS。

### 雙向變異實驗（每條守衛都必須轉紅）

| 變異 | 撤掉什麼 | 結果 |
|---|---|---|
| **M1** | naive 時戳硬拒 | **2 failed**（`test_naive_clock_is_refused_with_nonzero_exit`、`test_refused_run_produces_no_verdict_at_all`）⇒ **RED** |
| **M2** | key-node 前綴比對 | **2 failed**（`test_full_32char_id_is_matched_by_12char_prefix`、`test_cli_result_counts_key_node_transitions_by_prefix`）⇒ **RED** |
| **M3** | 評估例外可視 | **2 failed**（`test_eval_exception_makes_verdict_fail`、`test_eval_exception_disables_no_op_note`）⇒ **RED** |
| **M4** | 轉變未落地的結構檢查 | **1 failed**（`test_undelivered_transitions_are_flagged`）⇒ **RED** |

⇒ 4/4 轉紅；變異後檔案以 **sha256 驗證 byte-identical 還原**。
⇒ **沒有任何守衛被放寬**；四條守衛都是「升級不變式」。

---

## 2. 🔴 機制缺口：靈魂型節點的「跨日支持」恆為 0

### 2.1 量測（唯讀探針，`%TEMP%\night_obs\edge_revisit_probe.py`）

資料基準（2026-10-22 12:1x EDT）：節點 **708**（pattern 596／value 103／belief 4／trait 5）、邊 **1554**、`valid_until_ts` 非 None 者 **0** 筆。

逐日掛邊樞紐（掛到**建檔當日**的邊 vs 掛到**更早建的節點**＝跨日回訪）：

| 邊日 | 同日（目標型別） | **跨日回訪**（目標型別） |
|---|---|---|
| 2026-10-04 ～ 10-14 | pattern 20～21／value 186（10-11） | — |
| 2026-10-15 | pattern 23／belief 2 | **value 114** |
| 2026-10-16 | pattern 20 | **value 92** |
| 2026-10-17 | pattern 21 | **value 89** |
| **2026-10-18** | pattern 48／value 20 | **—（歸零）** |
| 2026-10-19 | pattern 42／value 20 | pattern 9 |
| 2026-10-20 | pattern 72／trait 8／value 46 | pattern 13 |
| 2026-10-21 | belief 6／pattern 57／trait 2／value 64 | pattern 38 |
| 2026-10-22 | pattern 106／value 152 | pattern 82 |

**逐節點樞紐（value／belief／trait 合計 112 顆）**：

| 建檔日 | 型別 | 節點數 | **有跨日邊的節點** |
|---|---|---|---|
| 2026-10-11 | value | 10 | 10（**最後支持日 10-17**） |
| 2026-10-15 | belief | 1 | **0** |
| 2026-10-18 | value | 10 | **0** |
| 2026-10-19 | value | 10 | **0** |
| 2026-10-20 | value／trait | 14 | **0** |
| 2026-10-21 | value／belief／trait | 23 | **0** |
| 2026-10-22 | value | 44 | **0** |

⇒ **靈魂型節點的跨日支持邊 = 0 ／ 112 顆。** 跨日回訪機制本身**沒死**（pattern 跨日邊 10-19 → 10-22 是 9 → 13 → 38 → 82，在長）；死的是「**既有靈魂節點被新事件重新支持**」這一條路。

### 2.2 那 10 顆 10-11 的 value 為什麼有跨日邊？（必須澄清，否則會誤判成「機制正常」）

`76add6b`（SOUL-BUG-009，2026-10-18 01:17）的 commit 訊息記載：**靈魂層自 2026-10-11 起是凍結的** —— `engine.elevate(pids[0])` 恆取候選維度組內**最舊**的 pattern ⇒ 產出的 content 與既有靈魂節點逐位元相同 ⇒ 被 `_persist_result` 去重丟棄。

⇒ 那 10 顆節點在 10-15／16／17 收到的跨日邊，是 **`_persist_result` 的「節點合併 ＋ 邊重綁」（SOUL-BUG-001）** 落到 canonical 節點上的**副產物**：邊確實掛到了 value 節點，但**該節點的 content 從 10-11 起就沒變過**。

⇒ **換句話說：跨日支持邊存在，但被支持的命題是凍結的。** 這是「帳面保活」，不是「命題真的還在被驗證」。

### 2.3 根因（讀碼 ＋ 全域搜尋，雙證據）

1. **`engine.reinforce()`（`soul_elevation/engine.py:1047`，REINFORCE 轉換：新支持證據累積 → 強化／重新激活、`last_support_ts` 刷新）在生產零呼叫者。**
   - 全域 `git grep reinforce`（repo + `soul-elevation` 套件）⇒ 唯一呼叫者在 **`harness/tl4.py:310,316`**（參考實作），生產 `src/**` 零呼叫。
   - `harness/tl4.py:297` 的註解寫的就是設計意圖：「喂經歷 → consume (pattern 候選) ＋ **reinforce 當前 durable belief**」。
2. **`engine.elevate()` 會把被聚合 pattern 的有效邊 supersede 掉**（`engine.py:546-547` `_supersede_edges`），並把快照複製到靈魂節點（`engine.py:550-564`）。⇒ **pattern 後續累積的邊，沒有任何機制流回已存在的靈魂節點。**
3. ⇒ **靈魂節點的支持集合永遠停在昇華那一刻** ⇒ 衰減錨點＝出生時刻 ⇒ **必然**在出生＋7 天弱化（門檻 `LIFECYCLE_T_WEAKEN_DAYS = 7`，`prior.py:63`）。

### 2.4 為什麼這不是「收割型機制的正常收斂」

`prior.py:62` 的原則是「**old ≠ outdated —— 年齡不是退場理由，失去支持才是**」。

- 「正常收斂」應該長成：命題**不再被新的生活支持** ⇒ 才弱化。
- 實際長成：命題**是否還在被支持根本無從表達**（支援鏈在 pattern → soul 的邊界斷掉）⇒ 所有靈魂型節點在出生＋7 天**排隊**弱化。

**兩者的可觀測差別**：目前 112 顆靈魂型節點的弱化時點＝`created_ts + 7d`，**與該 agent 的生活是否仍在產生相關經歷完全無關**。若真是「失去支持」，時點應該分散且與經歷密度相關。

⇒ **定性：這是機制缺口，不是自然收斂。** 今晚是第一個撞到紅線的案例。

---

## 3. 今晚（2026-10-22 22:00 EDT ＝ 2026-10-23T02:00:00Z）槽的預告

以**生產圖譜唯讀** ＋ 正確時鐘跑 sandbox CLI（本回合親測）：

| 評估時鐘 | 總轉變 | by node_type | weak_prefix | key_node_transitions | VERDICT |
|---|---|---|---|---|---|
| **2026-10-23T02:00:00Z（今晚）** | **24** | pattern 23／**belief 1** | **0 → 1** | 0 | **FAIL** |
| 2026-10-25T02:00:00Z | 75 | pattern 65／value 9／belief 1 | 0 → 10 | **1** | FAIL |
| 2026-10-30T02:00:00Z | 483 | pattern 373／value 101／trait 5／belief 4 | 0 → 110 | 1 | FAIL |

- 今晚那 1 筆 belief：`agent_mahiru` 的 `57392ccdb3dd411e846f352e19d22e76`，建檔 2026-10-15T02:05:08Z、支持邊 2 條（建檔當日）⇒ 錨點＝建檔時刻 ⇒ **+7 天 = 2026-10-22T02:05:08Z** ⇒ 今晚 22:00 EDT 的槽（= 10-23T02:00Z）**在它之後** ⇒ 必然轉弱化。
- Prompt 投影：`agent_mahiru` 正文 865 → 872 字，**弱化包裝「我隐约觉得」0 → 1**（`src/inner_life/emergent_projection.py:70`）。
- 關鍵反例 `6a92c906b245` 今晚**不動**（維持 `active`）；但 **2026-10-24 夜槽會被轉**（錨點 2026-10-17T12:28:12Z ＋ 7 天）⇒ **舊守衛會在這裡報 0（漏報）**，修好後正確報 1。

**修好後的 night_audit 預演（`--dry-run`，不寫累積檔）**：

```
CLI returncode = 1
eval_error     = 無（評估正常完成）        ← 舊版這裡是「TypeError 假綠」
soul_type_counts = {'belief': 1, 'value': 0, 'trait': 0, 'essence': 0}
weak_prefix 0 -> 1
VERDICT: FAIL
⇒ 紅線 ③ 總判定：FAIL
  判準拆解：轉變純度（soul_touched）= 0 筆 ⇒ PASS
            關鍵反例 lifecycle_state = active ⇒ PASS
            關鍵反例 sidecar 事件 = 0 筆 ⇒ PASS
            沙盒 CLI 重跑 VERDICT = FAIL ⇒ FAIL（見上方 CLI 輸出）
```

⇒ **對照：同一支腳本、同一個槽，舊版會印「紅線 ③ 總判定：PASS」（假綠）；修好後印 FAIL（真實預警）。**

---

## 4. 待 Owner 裁示（主大腦不自行決定）

### 裁示 1：今晚那 1 筆 belief 弱化的定性

| 選項 | 內容 | 主大腦觀察 |
|---|---|---|
| **甲** | **定性為「機制缺口的首個可觀察症狀」** —— 觀測口徑**維持**「弱化包裝 0」為紅線，今晚**照實標紅**，並開票修 REINFORCE 鏈 | 守衛不放寬，紅線是對的；缺口是真的 |
| **乙** | **升級不變式**：把「弱化包裝＝0」改寫為「弱化包裝只允許由已跨 7 天門檻且無新支持邊的節點產生，並須附門檻跨越算術」 | 口徑合理化；但**會讓 112 顆節點的弱化變成合法**，等於把缺陷正規化 |
| **丙** | 維持現口徑標紅，但今晚**人工豁免** | 每次都要人工豁免 ⇒ 7 天內會有 112 次 |

主大腦傾向 **甲**：今晚**該紅就紅**（它是真的），但紅的原因要寫成「機制缺口」而不是「系統正常」。

### 裁示 2：REINFORCE 鏈的修復範圍（若採甲 / 乙，建議獨立成票）

**不做**：不動 `engine.py`（soul-elevation 套件）、不動生命週期門檻、不動投影語意、不動任何 Frozen Contract。
**要決定**：生產端何時、依什麼規則把「新經歷」配對到「既有靈魂節點」並呼叫 `reinforce()` —— 這是**匹配規則**的設計決策（content_hash 完全相同？語意相似？同 agent 同型別最近節點？）。

**主大腦已備好的參考**：匹配規則的可行實作見 `harness/tl4.py:295-320`（但它是**指定式**配對，非自動配對）；生產側的既有匹配基礎設施見 `src/inner_life/elevation_adapter.py` 的 content-hash 合併機制（SOUL-BUG-001）。⇒ **這張票需要決策完整的工單，不能即興實作。**

### 裁示 3：是否要先擋住今晚的 belief 弱化

若採甲 ⇒ **不擋**（擋＝改生產行為、且等於催熟／壓制機制，違反「不催熟」處置合約）。
若 Bry 認為今晚不該紅 ⇒ 需要一個**只作用於 belief 型**的暫時處置（機制級、非口徑級），這是一個新的工程決策，需另開票。

---

## 5. 本輪已落地 / 未做

**已落地**：`scripts/eval_lifecycle_cli.py`（3 項修正）＋ `tests/test_eval_lifecycle_cli.py`（＋11 項守衛，3/3 變異轉紅）＋ `%TEMP%` 兩支 audit 腳本（3 項修正）。

**本輪未做（明文）**：
- 未改 `src/**`（`evaluate_lifecycle_all` 的 fail-closed 行為**刻意保留** —— 它是生產隔離保險；問題在**量測端**看不見它，已由 CLI 的 `eval_error` 解決）。
- 未改 `soul-elevation` 套件任何檔案。
- 未改生命週期門檻、投影語意、Frozen Contract。
- 未寫 `data/**`（全欄位唯讀；CLI 只寫自建 temp 沙盒）。
- 未殺任何行程、未重啟服務。
- **未寫 `logs/ENGINEERING_STATE.md`** —— 該檔 footer 明載「Next update requires Owner authorization per 禮2.4 lifecycle」，且本節核心是**待裁示的定性**；待裁示落地後再登錄 §0.1.38。
