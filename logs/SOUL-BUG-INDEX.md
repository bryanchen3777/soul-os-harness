# SOUL-BUG 系列總覽（索引）

**建立**：2026-10-13｜**來源工單**：SOUL-TECH-DEBT-001（C 類深度鑑識）
**狀態**：5 筆全部 OPEN，**全部刻意留紅 / xfail 守衛中**，**嚴禁放水**。
**共通性質**：這些不是「環境雜訊」，是會在生產靜默作案的真缺陷——沒有 exception、沒有告警、沒有計數器。

---

## 一覽表

| 編號 | 嚴重度 | 一句話 | 狀態機 | 守衛 |
|---|---|---|---|---|
| [SOUL-BUG-001](SOUL-BUG-001-WORK-ORDER.md) | **P0** | 昇降持久層去重連帶丟證據邊，EL-DD-2 閘門結構性不可達 | OPEN | `xfail(strict=True)` ✅ |
| [SOUL-BUG-002](SOUL-BUG-002-WORK-ORDER.md) | P1 | SAGE 單一零區辨力 token 即放行檢索 | OPEN | 直接留紅 ✅（但讀生產狀態，見工單 §八） |
| [SOUL-BUG-003](SOUL-BUG-003-WORK-ORDER.md) | P2 | SYSTEM_TICK 訂閱被 M5.7-2 註解，`:171` 死碼，測試仍期待 `agent_speak` | OPEN | 直接留紅 ✅ |
| [SOUL-BUG-004](SOUL-BUG-004-WORK-ORDER.md) | **P0** | chrono 時間感知雙端切斷，全角色時間感知喪失 | OPEN | `xfail(strict=True)` ✅ |
| [SOUL-BUG-005](SOUL-BUG-005-WORK-ORDER.md) | P1 | `format_localized` 星期索引整組錯一格 | OPEN | 直接留紅 ✅ |

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
