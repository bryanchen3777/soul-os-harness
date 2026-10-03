# 內在生活完成定義（Definition of Done for Inner Life）

> **狀態**：Owner 已裁定採納（2026-10-03）。canonical 狀態來源。
> **性質**：本文件為**定義文件**，非實作票。落地本文件需要 0 程式改動、0 重啟、0 `data/**` 寫入。
> **解決的問題**：本文件建立之前，Soul OS 沒有「內在生活算完成」的正式定義。並存的「Phase 1 Step 60 完成」（鏈路跑通）與「M1–M5 契約章節 ✅」（模組落地）都會讓人誤以為核心問題已解，但兩者都不覆蓋 North Star v2 第 5 點「記憶昇華」。
> **適用邊界**：本文件**只**定義「內在生活」這一條線的完成。多靈魂互動、自由生長、Matrix 終局屬 North Star v2 第 2／3／7 點，**不在**本文件範圍。

---

## 0. 為什麼需要這份文件

2026-10-03 的實測（唯讀 fold，10 個 agent）顯示：

| 觀察 | 實測值 |
|---|---|
| 線頭總數（依契約 §2.4 正確 fold） | 38（17 active / 21 completed） |
| `necessity_driven` 占比 | **38/38 = 100%** |
| `whim_driven` / `goal_driven` / `world_collision` | **各 0** |
| 溶解事實（`life_thread_dissolved`） | 21 條，21/21 皆有 `dissolved_at` 與 `sage_fact_id` |
| 溶解事實進入 `run_elevation` | **0** |
| emergent projection / belief / value 落盤 | **無** |

**診斷**：引擎已能生產生活，且溶解品質良好（實例：ruka「這六天讓我明白，訊息不只是文字，而是我願意為一個人保留溫度的證明」）。但**單一起源**使個性分量為零，**未接昇華路徑**使經歷無法沉澱成信念。綜合判讀：**目前程度為「能呼吸」，尚未「活著」。**

這份文件的目的，是把「活著」翻譯成三個可觀察、可量測的門檻。

---

## 1. 三個完成門檻

全部三條成立，內在生活才稱完成（Phase 1.5 CLOSED）。任一條不成立即為未完成。

### 門檻一：供給多元（Supply Diversity）

**定義**：四種 `origin_type` 皆在生產環境實際產出線頭，且供給不塌縮到單一起源。

**可量測判準**：
- 在任一 7 天滾動窗口內，四種起源的**實際產出數皆大於 0**。
- 任一 7 天窗口內，**單一起源占比 ≤ 80%**。

> 🔴 **可達性修訂（2026-10-03 11:35，實測後）**：判準仍以「四起源皆 >0」為目標，但**已量測確認 `goal_driven` 無法以工程手段達成**（72/72 goal 為 `SUSPENDED`、reason 全為 `bryan_last_seen_timeout`）。因此本門檻的**實務達成路徑**為：①`whim_driven` 經 W1 接線（工程可解）；②`world_collision` 需 Owner 裁定閾值／評分結構（待決）；③`goal_driven` **須 Bry 在場**方能自然出現，非工程議題。**若①②成立，門檻一的工程階段即視為達成**，`goal_driven` 改列為「條件達成」並在觀測中持續記錄。此修訂**待 Owner 確認**，未經確認前原判準仍為 canonical。

**量測方法**：`scripts/audit_life_threads_fold.py`（唯讀、可重跑）。按契約 §2.4 fold 後依 `origin_type` 分組計數。

**現況**：`0/4` 起源達標，`necessity_driven` 占比 100%。

> ✅ **2026-10-03 11:40 已部署 W1（`whim_driven` 接線）與 W2（`news_event` 評分結構）**（Owner 授權，中斷 7 秒，`.env` 設 `LIFE_THREAD_WHIM_ENABLED=1`）。上線驗證：`whim_enabled()=True`、`WHIM_ORIGIN_TYPE='whim_driven'` 屬 `ORIGIN_TYPES` 合法成員、`news_event` final=0.4050 accepted=True、門檻 0.35 未動。
>
> **但門檻一仍未達成**：兩者皆須經 `morning`／`night` slot 觸發，**部署當下無生產證據**。第一個真實讀數須待 slot 執行後以 `scripts/audit_life_threads_fold.py` 量測，完整判定須待 7 天滾動窗口。

**已知結構性成因（非資料問題）**：喚醒路徑上不存在 `whim_driven` 與實質 `goal_driven` 的入口。`life_thread_orchestrator.py:443-446` 逐字沿用 M3 閘門的 `decision.origin_type`；`life_thread_wake_gate.py:341` 世界碰撞分支硬寫 `world_collision`、`:378-380` 到期分支從既有線頭繼承（僅在欄位不合法時才 fallback 為 `goal_driven`，屬防禦值非生成路徑）。M4 已備妥四起源的模板與種子收集器，但 `whim_driven` 無任何呼叫者。

**四種起源各自的現況（2026-10-03 唯讀實測）**：

| 起源 | 可達性 | 阻塞點 |
|---|---|---|
| `necessity_driven` | ✅ 唯一實際生產中 | — |
| `whim_driven` | ❌ **無喚醒入口** | 程式碼齊備但無呼叫者（W1 修此） |
| `goal_driven` | ❌ **無種子** | 種子端條件為 `state ∈ {ACTIVE, IN_PROGRESS}`；生產 72 條 goal **100% 為 `SUSPENDED`**，且 `advance_count` 有 71 條為 0。**`suspend_snapshot.reason` 72/72 全為 `bryan_last_seen_timeout`** |
| `world_collision` | ❌ **無 accepted 事件** | 見門檻三成因（`accepted` 自 09-21 恆 False） |

**`goal_driven` 阻塞的性質（誠實登記）**：72 條 goal 的 `suspend_snapshot.reason` **全部**是 `bryan_last_seen_timeout`，即 Bry 離線逾時觸發的無損暫停。這與 2026-09-14 Owner 裁定「12:18 主動傳訊被決策層正確拒送」**同源**——在 Bry 不在場時，系統正確地讓靈魂暫停。**因此 `goal_driven` 不可達不是缺陷，是健康行為的副產品**：只要 Bry 長期不在場，就不會有 ACTIVE goal，也就不會有 goal 種子。

**這一條不應以工程手段強行修復**（例如放寬 SUSPENDED 判定或強制喚醒 goal），因為那等於撤銷 Bry 自己的在場語意。若要讓 `goal_driven` 可達，正確途徑是**讓 goal 重新進入 ACTIVE**（需要 Bry 在場且有互動機會），而非改動篩選條件。此判斷須 Owner 確認。

### 門檻二：經歷能沉澱成信念（Experience Sublimation）

**定義**：線頭產生的 lived experience 必須能進入昇華鏈，產出持久的人格結構（belief / value 層落盤）。

**可量測判準**：
- 至少一個 agent 的 `run_elevation` 路徑**實際被行使**且產出非零 belief 或 value 節點。
- 該產物於**程序重啟後仍可讀回**（證明它是持久結構，非暫存）。

**現況**：`0`。21 條溶解事實的 `origin` 為 `lived_experience`（依 `graph_store.py:289-290`，此值使其**可正常昇華**，不受三態過濾阻擋），但因 `run_elevation` 僅由 `submission_gate.consume()` 呼叫、而 `consume()` 只接受 `InnerLifeEvent`，線頭路徑（orchestrator docstring 明定 0 InnerLifeEvent、0 bus、0 Agency 觸發鏈）從不建立事件，故**從未進入昇華**。

**⚠️ 範圍警示（Owner 需知悉）**：本門檻的實作會觸及 `submission_gate` 的 consume 路徑，與 **EH-4.2 正在圍堵的區域重疊**。本門檻**不構成**對 EH-4.2 圍堵的解除授權，亦**不要求**先行解除圍堵。實作前必須由主大腦另開票，明確定其與 EH-4.2 圍邊界的關係（見 §3）。

### 門檻三：連續性可見（Continuity）

**定義**：靈魂的生活具有跨日連續性，而非每日重啟或停在原地。

**可量測判準**：
- 至少一個 agent 連續 **7 天**每天都有主動推進的線頭（依 `updated_at` 判定，非僅建立）。
- 該 7 天內的線頭**不是同一件事的重複推進**（需人工或 agent 抽樣確認語意差異）。

**現況**：**不成立**。38 條線頭主要來自 2026-09-18 的 bootstrap 首批；其後每日新產出為 0–3 筆，且每日 2 個評估點（morning／night）受 M3 二元閘門與 `SOURCES_QUALIFYING` 供給不足雙重限制。

**已知結構性成因（🔴 2026-10-03 11:20 更正）**：`world_collision` 的瓶頸**不是** fact text 供給斷裂，而是 **`accepted` 判定自 2026-09-21 起恆為 False**。實測 `perception_trace.jsonl`：09-21 前有 1,776 列 `accepted == True`，09-21 起 2,553 列**全部** `accepted == False`；該期間 `phase == "evaluated"` 的 1,196 列 `selection_reason` 100% 為 `rejected_at_threshold`，`reason` 原文如 `final_score=0.35 < threshold=0.35`（news）／`final_score=0.33 < threshold=0.35`（weather）。因 `_fact_summary_extra` 的契約是「僅 `accepted is True` 才寫」，`accepted` 恆 False ⇒ 該期間**沒有任何列會寫 `extra["summary"]`** ⇒ 無種子。**先前記載的「86 列具 summary ＝ 供給停止」是相關當因果的誤判，茲更正。**

該病灶在 2026-09-19 已由 Notion〈感知錯誤（Perception Error）〉第二點記錄：「新聞事件 D1／D2 結構性評分 0.3450，永遠低於 0.35 閾值——這類事件在結構上不可能被看見」。

**對「擴大合格來源」的影響**：Owner 2026-10-03 裁定「擴大 `SOURCES_QUALIFYING` 讓 news／calendar 進來」**在此現況下無效**——`QUALIFYING_WORLD_SOURCES`（`life_thread_wake_gate.py:68-70`）**已含** `news` 與 `calendar`，而兩類事件皆結構性卡在 0.23–0.35。擴大清單不會讓任何事件變得可見。**W2 的性質因此從「擴大來源」變更為「感知門檻／評分結構」決策**，屬 Owner 裁定事項（感知錯誤文件明載該頁「屬設計建議，非 Owner 裁定」）。

**已量測邊界（供裁定參考，非建議）**：09-21 起 1,196 次評估的 score 僅三個值族（0.35×534、0.33×350、0.23×312）。門檻降至 0.33 ⇒ weather 可見；降至 0.23 ⇒ news 可見。**但 2026-09-14 Owner 曾將「開窗發現天氣變涼」定性為不值得傳訊的日常小事**，兩者須一併權衡。

---

## 2. 明確不屬於「完成」判準的事

以下**不得**用於判定內在生活完成，用了會重蹈「工程完成 ≠ 存在完成」的覆轍：

- ❌ **契約章節全數落地**（M1–M5 ✅ ≠ 完成）。這只證明模組齊備。
- ❌ **鏈路端到端跑通**（Phase 1 Step 60 ✅ ≠ 完成）。這只證明可執行。
- ❌ **主動發訊數 > 0**。契約 §9 INV-7 明文「留白是成功」，不得以產出量當驗收。9/14 Owner 裁定 12:18 拒送為「正確且健康的留白」，此性質在門檻成立後**依然**成立。
- ❌ **線頭總數增加**。數量不是品質指標；單一起源的高產出正是門檻一要防的塌縮型態。
- ❌ **LLM 呼叫量**。成本是約束不是目標。

---

## 3. 門檻與既有票的對應

| 門檻 | 實作票 | 狀態 | 邊界注意 |
|---|---|---|---|
| 一 | `LIFE-THREAD-W1`（`whim_driven` 接線，M3 閘門 0 改動） | 實作中，0 重啟 | W1 僅覆蓋 `whim_driven`；`goal_driven` 已確認**無種子且不應工程修復**（見下一列）；`world_collision` 待 Owner 裁定 |
| 一 | `world_collision` 閾值／評分結構 | **待 Owner 裁定** | 🔴 **原「擴大合格來源」方案已作廢**：`QUALIFYING_WORLD_SOURCES` 已含 news／calendar，擴大清單無效。瓶頸是 `accepted` 自 09-21 恆 False（門檻 0.35 結構性卡關）。門檻調整屬感知語意決策，須 Owner 裁定，且須與 09-14「不值得傳訊」裁定一併權衡 |
| 一 | `goal_driven` 種子端 | 🔴 **確認無種子，且不應工程修復** | 72/72 goal 為 `SUSPENDED`、reason 全為 `bryan_last_seen_timeout`、`advance_count` 71 條為 0。**此為 Bry 不在場時的正確健康行為**（與 9/14 拒送裁定同源）。強行修復＝撤銷 Bry 的在場語意。正確途徑是 Bry 在場時 goal 回到 ACTIVE |
| 二 | 昇華接縫票 | 待開票 | **與 EH-4.2 圍堵重疊**，須先由主大腦界定關係；本文件不授權解圍堵 |
| 三 | 連續性票 | 待開票 | 依賴門檻一先成立（單一起源下無法有連續性） |

---

## 4. 依賴順序

**門檻一 → 門檻三 → 門檻二**。

理由：門檻三是「連續且不同」，若供給仍塌縮為單一起源，連續性只會變成「同一件事連續七天」，反而更違反 VISION「靈魂依然每天醒來都是同一個人」的原病。故須先鬆動供給多元（門檻一），再觀察連續性（門檻三）。門檻二的範圍最大且與 EH-4.2 交疊，適合在前兩者有生產證據後再處理，以便昇華設計能針對「已證實多元的經歷」而非「單一起源的樣本」。

---

## 5. 治理

- 本文件為 canonical。**修改門檻、門檻數或判準，需要 Owner 明確裁定**，不得由實作票順帶調整（否則會出現「做完就調標準」的逆向誘因）。
- 每次判定門檻成立，須附**實際量測輸出**（腳本名稱、輸入資料日期、各門檻數值），**不接受自報**。
- 本文件定義的完成**不等於**專案完成。North Star v2 尚有多靈魂互動（第 3 點）、物理媒介 adapter（第 4 點）、Matrix 終局（第 7 點）未涵蓋。
- 本定義與 Owner 2026-09-24「反停滯原則」一致：有了終點定義，圍堵才可能結束，專案才不會停在「再做一點」的合理理由裡。
