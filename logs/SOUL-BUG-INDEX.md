# SOUL-BUG 系列總覽（索引）

**建立**：2026-10-13｜**來源工單**：SOUL-TECH-DEBT-001（C 類深度鑑識）
**狀態**：**001／002／003／005 已 CLOSED，004 匯端已結案、源端維持 Bry 裁示不動；006 OPEN**，全部有守衛，**嚴禁放水**。
**共通性質**：這些不是「環境雜訊」，是會在生產靜默作案的真缺陷——沒有 exception、沒有告警、沒有計數器。

---

## 一覽表

| 編號 | 嚴重度 | 一句話 | 狀態機 | 守衛 |
|---|---|---|---|---|
| [SOUL-BUG-001](SOUL-BUG-001-WORK-ORDER.md) | **P0** | 昇降持久層去重連帶丟證據邊，EL-DD-2 閘門結構性不可達 | **✅ CLOSED**（節點合併＋邊重綁＋邊去重） | `xfail` 已解除並轉綠 ✅ 三組變異實驗全紅 |
| [SOUL-BUG-002](SOUL-BUG-002-WORK-ORDER.md) | P1 | SAGE 單一零區辨力 token 即放行檢索 | **✅ CLOSED `e8aad13`** | **已轉綠** ✅ |
| [SOUL-BUG-003](SOUL-BUG-003-WORK-ORDER.md) | **P3** | SYSTEM_TICK 訂閱被 M5.7-2 註解，`:171` 死碼，測試仍期待 `agent_speak` | **✅ CLOSED**（端點除役＋死碼清理＋`/inject/yua` 加閘門） | 三度更名的 404 契約斷言轉綠；**三組變異實驗全紅** |
| [SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md) | **P0** | chrono 時間感知雙端切斷 | **匯端 ✅ 已結案（`f649224`）**；源端維持 Bry 裁示不動 | `xfail` 已解除並轉綠 ✅ |
| [SOUL-BUG-005](SOUL-BUG-005-WORK-ORDER.md) | ~~P1~~ | `format_localized` 星期索引整組錯一格 | **✅ CLOSED `277a8d9`** | **已轉綠** ✅ |
| [SOUL-BUG-006](SOUL-BUG-006-WORK-ORDER.md) | **P1** | 昇華候選幾乎不升格，且 value/trait 內容是未抽象化的原始簿記字串 | **✅ CLOSED `c3520cb`/`8b3a64e`/`429a9e5`/`0308b71`**，四階段全線上線（DEPLOY-RESTART-14/15） | **36 項守衛 ＋ 12 組變異全紅**；全庫 6545 passed / 0 failed（collected 6572 對平）；**世界端已獲生產實證**，夢境端待下一週期 |
| [SOUL-BUG-007](SOUL-BUG-007-WORK-ORDER.md) | **P1** | 簡繁字形分裂造成召回損失；無主題的泛化謂語單獨開 Gate 導致離題召回 | **✅ CLOSED `399b92c`**，已上線生產 | 17 項守衛 ＋ **14 組變異全紅**；全庫 6521 passed / 0 failed（collected 6527 對平） |
| SOUL-BUG-008 (1)（無工單檔） | **P0** | 群聊公開 system prompt 洩漏三塊 1:1 私域內容（Bry 近期訊息／對 Bry 的親密度 N/100／時間錨點的 longing 門檻） | **✅ CLOSED `37c1cac`**（+ `de5ba34` 標記 `_load_bry_recent` deprecated） | **BACKFILLED FROM GIT LOG**：commit 主題明載 P0；blast radius = 10 隻 agent × 每一句群聊發話 |
| SOUL-BUG-009（無工單檔） | **P0** [BACKFILLED] | 昇華恆取最舊 pattern 當錨點，內容與既有靈魂節點逐位元相同而被去重丟棄 ⇒ 靈魂層自 2026-10-11 起凍結 | **✅ CLOSED `76add6b`**（`a5bed77` 生產實證驗收） | **BACKFILLED FROM GIT LOG**：commit body 實測記載「每天 29 顆新 pattern、0 顆新靈魂節點」＋ trace 法醫（3,799 次昇華嘗試、3,798 次被去重吞噬，吞噬率 99.97%）⇒ 嚴重度由 Owner 授權反推為 P0 |
| [SOUL-BUG-010](SOUL-BUG-010-WORK-ORDER.md) | **P1** | 生命週期狀態在讀取面完全失效（節點檔 append-only 凍結狀態，唯一對外 API 無過濾）；R3 計票隔離因 frozen dataclass 從未執行過 | **✅ CLOSED `704f851`/`89397d5`**，已上線生產（DEPLOY-RESTART-18） | 20 支守衛全綠 ＋ **7/7 變異全紅**；全庫 6609 passed；16 顆受害節點已隔離 |
| [SOUL-BUG-010-B](SOUL-BUG-010B-WORK-ORDER.md) | **P1** | 生命週期自動老化與 night slot 串接（錨點欄位 `last_support_ts` 全庫無寫入者 ⇒ 衰減退化為年齡制，直接接線會誤傷 10 顆仍被持續支持的 value 節點） | ✅ **CLOSED `c327958`**，已上線生產（DEPLOY-RESTART-19，2026-10-19 22:00 起 nightly 自動老化生效） | 14 支守衛全綠 ＋ **5/5 變異全紅**；錨點改由原始邊檔計算並於餵入引擎前注入 |
| [SOUL-BUG-011](SOUL-BUG-011-WORK-ORDER.md) | **P1** | 日記／夢境輸出被左側截斷吞掉標題與開頭段落（240 筆歷史卡在 78~80 字元區間） | **✅ CLOSED `f91ca66`** | 15 支新守衛 ＋ **4/4 變異全紅**；11 支舊契約留痕解凍；全庫 6593 passed |
| SOUL-BUG-012（無工單檔） | **P0** | `dream:event` 先提交後寫入，使 A-i 反查前提不成立 | **✅ CLOSED `89e2cf1`** | AST 抽函式 ＋ exec 沙箱 5 支守衛 ＋ MUT-01 雙向驗證 |
| [SOUL-DECAY-FLAKY-AUDIT](SOUL-DECAY-FLAKY-AUDIT.md) | **P3** | `test_m5_13_5_untouched_decay.py` 全庫回歸偶發失敗（環境／時間敏感型 flaky） | 📋 **已立案，待排程**（嚴禁夾帶進 SOUL-BUG-010-B） | 單檔 ×20 全綠；基線 worktree 對照路徑**不可用**（全為 checkout 雜訊） |

## ✅ SOUL-BUG-007：檢索字形正規化 ＋ 防離題 Gate（2026-10-17）

**兩道獨立缺陷，兩種病態，都屬「寧可誠實說不知道，也不能亂答」：**

**缺陷 A｜簡繁字形分裂（召回損失）**
繁體 persona 管線混進約 323 處簡體 tag。決定性證據：同一個 `agent_aoi` store，
`q=評價` 只召得 **2** 筆、`q=评价` 召得 **5** 筆——繁體查詢漏掉 60% 該找到的記憶，
純粹是字形不同。修後兩者皆為 **7** 筆（正規化同時把 `主动`→`主動` 等一併收斂）。

**缺陷 B｜泛化謂語單獨開 Gate（假召回）**
問 `agent_yua`「你喜歡看書嗎」，庫裡根本沒有「書」，卻因命中「喜歡」而召回
**5 筆「你喜歡聽…」的離題記憶**。低召回是誠實（我不知道），假召回會讓角色
一本正經地說錯話——後者嚴重得多。修後回 **0** 筆。

**設計要點**
- 正規化做在 `_expand_tag_components`（query 端與 memory 端的**唯一共同展開點**），
  **不碰 frozen 的 `writer.py`、不改磁碟既有資料**。放在 `derive_query_tags`
  會間接改變 frozen writer 的輸出，故刻意避開。
- 泛化謂語收進 `_LOW_INFO_TOKENS` 後**同時**是 SAGE-RANK-1 的 Secondary Boost：
  單獨命中開不了 Gate，但與真主題詞同時命中時，「喜歡讀書」會浮到「喜歡聽」之上。
- **刻意不收**：`顏色`（yua 真主題）、角色專名（瑠夏／雷姆／真昼／三玖）。
  頻率高 ≠ 零區辨力；這是本案最容易被做錯的地方。
- 簡繁對照表只收無歧義字，並有交叉守衛（無自我對映／無鏈式對映／歧義字黑名單）。

**與 SAGE-RANK-1 的交叉影響（重要）**：BUG-007 收編「記得」等詞後，
RANK-1 自己的守衛測試有 4 支轉紅——它當初拿「記得」當高頻 fixture 詞。
已換成「事情」並加上 `_assert_fixture_words_pass_the_gate()` 反守衛。

## ✅ SOUL-BUG-006：昇華抽象化與內容去重（2026-10-17 登記 → 同日四階段結案）

> **結案狀態**：四階段全部實作並上線生產（DEPLOY-RESTART-14／15）。
> **Step 1** `c3520cb` 消費端 content 脫殼＋fail-closed ／
> **Step 2** `8b3a64e` world 生產端自然語言環境敘事（確定性、0 LLM）／
> **Step 3** `429a9e5` 生產圖譜 786→297 節點 ＋ 297 顆原地脫殼 ／
> **Step 4** `0308b71` 夢境端 A-i 消費端依 `event_id` 反查夢境正文。
> **供給三本柱**：世界／日程 ✅ 已獲生產實證（「今天的天氣有變化，氣溫 30.6 度，空氣炎熱。」）／
> 日記 ✅ 全程正常 ／ 夢境 ⏳ 已上線、待下一週期驗證生產實證。
> 🔴 **診斷修正**：下方「病灶 1／2」已於排查中**部分撤銷** —— 升格閘門實測
> 100% 正常運作（26 個候選維度組中達標組數 = 組數），原報「349 value 僅 10 升格」
> 是統計假象（把 753 顆 pattern 節點誤算進 value 組）。真正的根因只有兩個：
> ① `_event_content()` 回 key=value 序列化外殼；② world／dream 的 extras **沒有敘事欄位**。

**登記依據**：`SAGE-RANK-1` 第一階段唯讀診斷的 Part A（靈魂圖譜實證體檢）。
**裁示**：獨立立票，**排在 `SAGE-RANK-1` 收官之後**，不得併入檢索排序單。

### 三個實測病灶

1. **升格閘門近乎不通。** 候選端產出 value 349／trait 46／belief 357，
   但圖譜中 `node_type` 只有 value 10／trait 19／belief 1。
   SOUL-BUG-001 修復上線（2026-10-14 21:15）後新增的 176 顆節點裡
   **pattern 174、trait 1、belief 1、value 0** —— Bry 點名的
   rem／mai／akane／ruka／mahiru，修復後新增 value/trait **全部為零**。

2. **value/trait 內容未抽象化。** 現存的 29 顆 value/trait 全部建立於
   10-06 ～ 10-15 02:05（幾乎都早於修復上線），內容是原始簿記字串：
   - trait：`dream:dream: all_agents_count=10; target_agent_id=agent_X`
   - value：`diary:night: narrative=<日文日記原文>; slot=night`

3. **跨日內容重複。** 3 組前 60 字完全相同的節點：
   ×7（target=akane）／×6（target=rem）／×5（target=yua）。
   證據邊數量極不均衡：value 掛 28～81 條，trait 僅 2～6 條。

### 為何不能併入 `SAGE-RANK-1`

`SAGE-RANK-1` 修的是**讀取側**（檢索排序），本 bug 在**寫入側**（昇華抽象）。
SOUL-BUG-001 的邊重綁確實生效（持續產節點＋證據邊、0 重複三元組、0 孤兒邊），
證明基礎管線是通的；壞掉的是「把原始紀錄昇華成人格層命題」這一步本身。

### 排查時的既有旁證

repo 內註解早就寫下過診斷（`tests/test_m3_world_awareness.py:569-575` 類型：
「診斷被寫下來」≠「根因被修掉」）——開工前先搜既有註解是否已指出答案。

## ✅ 已結案

| 編號 | 修復 | 驗收 |
|---|---|---|
| SOUL-BUG-005 | `src/timezone_utils.py:250` → `_weekday_cn(local)`（**一行**） | 守衛轉綠；相依 6 檔 135 passed |
| SOUL-BUG-004（匯端 + 中段 + 時區） | `f649224`：live builders 加 `chrono_context` 插槽並注入；`_fire_intent` 發言那一刻現算 chrono；**刪除 `PromptContext` 死碼**；**`core.py` 時區正規化一行根治** | 時鐘對齊 Bry 指定三案例；新增 7 支測試經**變異實驗**確認是真守衛；240 passed + 1 xfailed |
| SOUL-BUG-002 | `e8aad13`：`loader.py` 新增 `_LOW_INFO_TOKENS`，計數時扣除低資訊詞 | 6 筆假陽性全數關閉；12 項回歸全過 |
| SOUL-BUG-001 | `elevation_adapter.py`：**節點合併＋邊重綁＋邊去重**（VALUE-DEDUP-A-1 節點冪等 100% 保留） | 看門狗 `xfail(strict)` 解除後轉綠；**三組變異實驗全轉紅**；新增 5 支單元測試 |
| SOUL-BUG-003 | `gateway.py` 除役 `/inject/tick`、刪 `consciousness.py` 死碼、`/inject/yua` 加 `SOUL_ENV=production` 403 | 404 契約斷言 + AST 死碼守衛；**三組變異實驗全轉紅** |
| TECH-DEBT-P3-01 | `git rm tests/test_self_check_v1.py`（與 v2 互為邏輯否定的過期測試，隔離執行時還會空轉假綠） | `test_self_check_v2.py` 為唯一驗收守衛 |

## ⚠️ SOUL-BUG-004 源端的現況（Bry 裁示維持不動）

`consciousness.py:155` 的 `SYSTEM_TICK` 訂閱**維持註解**（M5.7-2，`95891e8`），
`_on_tick` 死碼亦未清理。

這**不再是時間感知缺陷**：chrono 改由 `_fire_intent` 在發言那一刻 just-in-time 現算，
不依賴 tick 推播。源端取捨歸入 [SOUL-BUG-003](SOUL-BUG-003-WORK-ORDER.md)（**已下調 P3**）。

**但有一項連帶收益**：`core.py` 的時區正規化是根治點，
因此若 Bry 日後決定恢復 SYSTEM_TICK，`heartbeat/engine.py:205` 也會自動拿到正確時鐘，
不必再改第二次。

---

## 修復優先序（Bry 2026-10-14 更新）

| 順序 | 編號 | 狀態 |
|---|---|---|
| 1 | SOUL-BUG-005（星期偏移） | ✅ 已結案 `277a8d9` |
| 2 | SOUL-BUG-004（時間感知 + 時區） | ✅ 已結案 `f649224` |
| 3 | SOUL-BUG-002（SAGE 停用詞） | ✅ 已結案 `e8aad13` |
| 4 | SOUL-BUG-001（昇降去重：節點合併＋邊重綁＋邊去重） | ✅ 已結案 |
| 5 | SOUL-BUG-003（端點契約） | ✅ 已結案 2026-10-16（端點除役、哨兵改由 `test_m1` 免費提供） |
| 6 | SOUL-BUG-006（昇華抽象化＋內容去重） | ✅ 已結案 2026-10-17（四階段全線上線；夢境生產實證待下一週期） |
| 7 | SOUL-BUG-007（字形正規化＋防離題 Gate） | ✅ 已結案 2026-10-17（`399b92c`，已上線） |

---

## 兩種守衛形式的差別（不要混用）

**`xfail(strict=True)`**（001 / 004）
：缺陷發作時報 XFAIL，歸屬寫在 reason 裡，**不佔紅燈名額、不污染日常信號**。
一旦有人修好，pytest 立刻噴 XPASS（視為 FAILED），
**強迫修復者顯式移除標記、把測試轉正為常態回歸**。

**直接留紅**（002 / 003 / 005）
：缺陷發作時報 FAILED。優點是零偽裝；代價是長期佔紅燈。
但這正是 Bry 明訂的**「零未分類紅燈」**驗收線所容許的——
它們是**已分類**的缺陷紅燈，不是未分類雜訊。

---

## 為什麼這 5 筆不能「順手修掉」

SOUL-TECH-DEBT-001 的測試紀律是**只修測試、絕不動生產代碼**。
這 5 筆全部落在 `src/**`：

- 001 → `src/inner_life/elevation_adapter.py:384-392`（且修好會讓 6 筆已校準的測試跟著紅，須回退）
- 002 → `src/memory/v1/loader.py`（且牽動 Bry §25(b) 的分層門檻語意）
- 003 → `src/agent/consciousness.py:155`（動它等於回退 M5.7-2 的決策）
- 004 → `consciousness.py:155` + `llm/proxy.py:1555-1598`（跨模組重構，且不得恢復 SYSTEM_TICK 訂閱）
- 005 → `src/timezone_utils.py:250`（**一行，唯一低風險者，可優先處理**）

⇒ 每筆都需要獨立工單獨立驗收，不得併單。

---

## 殘餘技術債（~~P3~~）—— ✅ 全部結案

| 編號 | 內容 | 證據 | 處置 |
|---|---|---|---|
| **TECH-DEBT-P3-01** | ~~測試間全域狀態污染~~ **✅ 已結案 2026-10-14**（commit `1875191`；原 Bry 2026-10-13 裁示為「暫不開整庫二分」） | 根因：`src/llm/proxy.py:139` 的 `INNER_LIFE_DATA_DIR = str(data_root()/"soul")` 是**匯入期即算死**的 module-level 常數，`conftest.py` 的 autouse per-test 隔離（`reset_data_root()`）**救不回已算好的常數**。原本只需「二分找污染源」，最終走的是測試側釘針，**零生產風險、不需二分**。 | ① `tests/test_self_check_v1.py`（與 v2 互為邏輯否定的過期測試）已 `git rm`；② `test_m2_0_inner_life_v2.py::test_e_build_messages_group_with_real_inner_life` 已用 `original_dir` / `try` / `finally` 還原的釘針，把常數指回真實 `data/soul`（範本 `tests/test_m3_prompt_integrity.py:117-133`）。**2026-10-17 實測複核**：單跑 12 passed；當晚三次全庫回歸（6537 / 6545 passed）**均未出現此紅燈** ⇒ 順序相依已消除。 |

## 已治理的治理類問題（非缺陷，但已違反紅線）

| 問題 | 狀態 | 處置 |
|---|---|---|
| `tests/test_extract_and_judge_context_bug.py` **無閘門的真實 LLM 呼叫**（每跑一次全庫發一次真實請求） | ✅ `aa04e33` | 補雙重 opt-in 閘門（`SOUL_TEST_REAL_LLM` + 目前 provider 的 key），**預設 SKIP**；規格對齊 `configs/loader.py` 的真實 provider 分支 |

---

## 修復優先序建議（主大腦觀點，非裁示）

✅ 1–5 全部已結案（SOUL-BUG-005／004 匯端／002／001／003，見上方優先序表）。
**當前無待修的 P0/P1 SOUL-BUG 票。**

**下一階段候選（需 Bry 選件）**
| 候選 | 內容 | 產出比 | 風險 |
|---|---|---|---|
| ~~TECH-DEBT-P3-01~~ | ~~順序相依紅燈~~ **已於 2026-10-14 結案**（見下方殘餘技術債表已改寫） | — | — |
| SOUL-BUG-006 夢境生產實證 | 等下一週期自動發生 | 被動 | 無 |
| SAGE-RANK-1 / SOUL-BUG-007 生產實證 | 需 Bry 真實對話才會產生 trace | 被動 | 無 |

> ⚠️ **本節曾誤列 TECH-DEBT-P3-01 為候選（2026-10-17，已更正）**：主大腦依殘餘技術債
> 表的過時記載提議開工，但該票實於 **2026-10-14** 即已結案（commit `1875191`
> 「DEPLOY-RESTART-8 登記 … TECH-DEBT-P3-01 上線」）。**提議前應先驗現況，不該只讀索引。**
> 這是今晚第二次「過時文件比記憶體更不可靠」的實例（前一次是索引謊報 SOUL-BUG-006/007 進度）。

---

## 相關文件

- 工單：`logs/SOUL-TECH-DEBT-001-WORK-ORDER.md`、`logs/SOUL-TECH-DEBT-001-EXECUTION-REPORT.md`
- 看門狗：`tests/test_soul_bug_watchdogs.py`
- 生產資料寫入事故：`logs/INCIDENT-2026-10-12-01-motive-trace-write.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`
