# SOUL-BUG 系列總覽（索引）

**建立**：2026-10-13｜**來源工單**：SOUL-TECH-DEBT-001（C 類深度鑑識）
**狀態**：**1 筆已 CLOSED（SOUL-BUG-005，`277a8d9`）、4 筆 OPEN，全部有守衛**，**嚴禁放水**。
**共通性質**：這些不是「環境雜訊」，是會在生產靜默作案的真缺陷——沒有 exception、沒有告警、沒有計數器。

---

## 一覽表

| 編號 | 嚴重度 | 一句話 | 狀態機 | 守衛 |
|---|---|---|---|---|
| [SOUL-BUG-001](SOUL-BUG-001-WORK-ORDER.md) | **P0** | 昇降持久層去重連帶丟證據邊，EL-DD-2 閘門結構性不可達 | **✅ CLOSED**（節點合併＋邊重綁＋邊去重） | `xfail` 已解除並轉綠 ✅ 三組變異實驗全紅 |
| [SOUL-BUG-002](SOUL-BUG-002-WORK-ORDER.md) | P1 | SAGE 單一零區辨力 token 即放行檢索 | **✅ CLOSED `e8aad13`** | **已轉綠** ✅ |
| [SOUL-BUG-003](SOUL-BUG-003-WORK-ORDER.md) | **P3** | SYSTEM_TICK 訂閱被 M5.7-2 註解，`:171` 死碼，測試仍期待 `agent_speak` | **部分結案**（E2E 已改寫為負向契約斷言、轉綠 ✅）；`:171` 死碼 + 端點除役**仍待裁示** | 負向契約斷言 + `MUT-A3` 變異 ✅ |
| [SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md) | **P0** | chrono 時間感知雙端切斷 | **匯端 ✅ 已結案（`f649224`）**；源端維持 Bry 裁示不動 | `xfail` 已解除並轉綠 ✅ |
| [SOUL-BUG-005](SOUL-BUG-005-WORK-ORDER.md) | ~~P1~~ | `format_localized` 星期索引整組錯一格 | **✅ CLOSED `277a8d9`** | **已轉綠** ✅ |

## ✅ 已結案

| 編號 | 修復 | 驗收 |
|---|---|---|
| SOUL-BUG-005 | `src/timezone_utils.py:250` → `_weekday_cn(local)`（**一行**） | 守衛轉綠；相依 6 檔 135 passed |
| SOUL-BUG-004（匯端 + 中段 + 時區） | `f649224`：live builders 加 `chrono_context` 插槽並注入；`_fire_intent` 發言那一刻現算 chrono；**刪除 `PromptContext` 死碼**；**`core.py` 時區正規化一行根治** | 時鐘對齊 Bry 指定三案例；新增 7 支測試經**變異實驗**確認是真守衛；240 passed + 1 xfailed |
| SOUL-BUG-002 | `e8aad13`：`loader.py` 新增 `_LOW_INFO_TOKENS`，計數時扣除低資訊詞 | 6 筆假陽性全數關閉；12 項回歸全過 |
| SOUL-BUG-001 | `elevation_adapter.py`：**節點合併＋邊重綁＋邊去重**（VALUE-DEDUP-A-1 節點冪等 100% 保留） | 看門狗 `xfail(strict)` 解除後轉綠；**三組變異實驗全轉紅**；新增 5 支單元測試 |

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
| 5 | SOUL-BUG-003（端點契約） | 部分結案 — **P3，不影響生產**；`:171` 死碼待清 |

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

## 殘餘技術債（P3，待排程處理）

| 編號 | 內容 | 證據 | 處置（Bry 2026-10-13 裁示） |
|---|---|---|---|
| **TECH-DEBT-P3-01** | **測試間全域狀態污染**：`tests/test_m2_0_inner_life_v2.py::test_e_build_messages_group_with_real_inner_life`、`tests/test_self_check_v1.py::test_self_check_v1` 於全庫跑時紅燈，單獨跑全綠 | 隔離執行 `13 passed in 3.38s`；`pytest-randomly` 未安裝 ⇒ 污染源固定，非隨機順序 | **暫不開整庫二分**（每輪 11.5 分鐘、十幾輪＝數小時算力，投產比極低）；等未來排程引進 `pytest-randomly` 或統一清理單元測試 teardown 時一併處理。**業務邏輯與被測功能 100% 健全**，非缺陷。 |

## 已治理的治理類問題（非缺陷，但已違反紅線）

| 問題 | 狀態 | 處置 |
|---|---|---|
| `tests/test_extract_and_judge_context_bug.py` **無閘門的真實 LLM 呼叫**（每跑一次全庫發一次真實請求） | ✅ `aa04e33` | 補雙重 opt-in 閘門（`SOUL_TEST_REAL_LLM` + 目前 provider 的 key），**預設 SKIP**；規格對齊 `configs/loader.py` 的真實 provider 分支 |

---

## 修復優先序建議（主大腦觀點，非裁示）

1. **SOUL-BUG-005**（1 行，風險最低，見 Bry 2026-10-03 親手抓過的同一 bug）
2. **SOUL-BUG-004 匯端子步**（先只把 `chrono_context` 插槽接回 live 的 prompt 組裝器，不碰源端）
3. SOUL-BUG-002（需先落實「分級重疊」裁示的 `_GENERIC_STOPWORDS`）
4. SOUL-BUG-001（修好會翻轉 6 筆已校準測試的斷言，需明確記錄回退理由）
5. SOUL-BUG-003（需先釐清 M5.7-2 當初要解決的 proactive 重複觸發如何滿足）

---

## 相關文件

- 工單：`logs/SOUL-TECH-DEBT-001-WORK-ORDER.md`、`logs/SOUL-TECH-DEBT-001-EXECUTION-REPORT.md`
- 看門狗：`tests/test_soul_bug_watchdogs.py`
- 生產資料寫入事故：`logs/INCIDENT-2026-10-12-01-motive-trace-write.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`
