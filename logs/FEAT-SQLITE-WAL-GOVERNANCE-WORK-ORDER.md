# 工單：FEAT-SQLITE-WAL-GOVERNANCE（第一段：B1 唯讀診斷 ＋ 最小修復提案）

> 開立：2026-10-23 23:20 EDT（主大腦；Owner 2026-10-23 裁示「准予另立維護票」＋本回合的 WAL 實測鐵證）
> 類型：**診斷先行**（本票第一段純唯讀，0 生產寫入）＋ 第二段提出**最小修復**（實作於隔離 worktree，**不 commit、不 push**）
> 母事故：`INCIDENT-GOAL-DB-LOCKED`（層 1 放大器已修、**層 2 觸發源仍未證明**）

---

## 0. 為什麼這張票現在開（觸發證據，全部為實測）

| 量 | 值 | 來源 |
|---|---|---|
| 重啟前 `graph.sqlite-wal` | **46 個／50,768,112 bytes** | 23:02 黃金軌跡（`%TEMP%\night_obs\golden_trace_pre.json`） |
| 重啟**後** `graph.sqlite-wal` | **47 個／50,768,112 bytes** | 23:04 實測 ⇒ **位元組數幾乎等量＝乾淨關閉並未 checkpoint** |
| 最大單檔 | `agent_yua` **6,147,072 bytes** | 同上 |
| `graph.sqlite-shm` | 47 個 | 同上 |
| 事故期間 `database is locked` | **1,000 筆**（每 30 秒 1 筆，14:31→23:02） | 重啟前日誌 |
| 重啟後 10 分鐘 | **0 筆** | `%TEMP%\night_obs\restart_layers.txt`（層 1 已修） |

🔺 **本票的真正價值不在「清理」，而在「層 2 觸發源」**：
WAL 之所以堆積到 4~6 MB 仍不 checkpoint，最可能的原因是**有長讀交易未結束**，把 checkpoint 擋在「不能截斷」的位置（SQLite 的 checkpoint 只能回收「最舊讀快照之前」的頁）。而**長讀快照正是 `SQLITE_BUSY_SNAPSHOT` 與寫入鎖死事故的典型前因**。⇒ 若本票證實「長讀交易阻塞 checkpoint」，它就是事故層 2 的**根因候選**，而不只是衛生問題。
⇒ 因此本票**第一段必須先量清楚機制**，**禁止**在未理解前直接加 checkpoint 排程（Owner 紀律：拒絕未經實證的自造常數）。

---

## 1. 目標

1. **B1（唯讀診斷）**：查明 47 個 agent 的 `graph.sqlite` 與 `data\memory.db` 為何 WAL 堆積不 checkpoint，給出**可辯護的機制結論**（不是猜測）。
2. **B2（最小修復提案）**：若診斷結論明確，於隔離 worktree 提出**最小**修復（PASSIVE checkpoint 代謝機制），並附守衛與變異實驗；**不 commit、不 push、不部署**。

---

## 2. 範圍

### 可讀（唯讀）
- `src/memory/sage/graph_store.py`（連線管理、pragma、交易生命週期）
- `src/memory/store.py`（v1 store、busy_timeout）
- `src/soul/scheduler.py`（排程器既有 30s wake；🔺 **本票可改** —— §5 步驟 5 明文要求「優先掛既有 30s wake（`scheduler.py` 的 additive 分支慣例）」⇒ **§3 原列「本票只讀，不改」與 §5 互相矛盾，以 §5 為準**；主大腦 2026-10-24 02:4x 於合入前獨立複核時發現並裁定，實作為 +39 行 additive 分支）
- `data/memory/agent_*/graph.sqlite` ＋ `-wal` ＋ `-shm`（**唯讀**）
- `data/memory.db`（唯讀）
- 備份快照：`data/_incident_backup_20261024T030255Z/`（139 檔／61,069,552 bytes，**本票的主要實驗素材**）

### 可寫（僅兩處）
- `%TEMP%\wal_gov_wt\`（隔離 worktree，實驗與修復提案）
- `%TEMP%\wal_gov\`（實驗副本、報告暫存）

### 🔴 絕對不可
- **不得**對生產 DB 執行任何寫入或 checkpoint（**含 `PRAGMA wal_checkpoint`——它是寫操作**）；生產的連線一律 `mode=ro`。
- **不得**寫入／改名／刪除 `data/**` 的任何既有檔案。
- **不得**改任何 repo 檔（第一段）；第二段只改 worktree 內的副本，**不 commit／不 push／不部署**。
- **不得**重啟服務、不得對任何 PID 做 Stop-Process／taskkill／pkill。
- **不得**動 `src/memory/sage/writer.py`（**frozen_dirs 永久紅線**，Append-Only 權威）。

---

## 3. 做法（決策已定，執行者照做）

### 步驟 1：建構實驗沙盒（不碰生產）
1. `git -C <repo> worktree add --detach %TEMP%\wal_gov_wt HEAD`（基線＝當期 main，**唯讀使用**）。
2. 把 `data/_incident_backup_20261024T030255Z/` 整份**複製**到 `%TEMP%\wal_gov\copy\`（139 檔；**複製**不移動、不刪原檔）。
3. 所有實驗（開檔、寫入、checkpoint、量 WAL）**只在 `%TEMP%\wal_gov\copy\` 內**進行。

### 步驟 2：靜態盤點（每個 DB 一列，寫清分子/分母/單位）
對 **47 個 `graph.sqlite` ＋ 1 個 `memory.db`**：
- `journal_mode`（用 `mode=ro` 連線查 `PRAGMA journal_mode`）＝ WAL / delete / 其他
- `wal_autocheckpoint`（同樣唯讀查）
- `page_size`、`page_count`、`freelist_count`
- `-wal` 與 `-shm` 的 bytes（生產值取自現場；副本值取自備份）
- 對應程式碼：`graph_store.py`／`store.py` 裡的連線取得方式（每次新建？pool 快取？）、`busy_timeout`（事故補丁 P2b＝10s、P3＝5s）、**有無任何 `PRAGMA wal_checkpoint` 呼叫**（grep 全庫）
- 連線生命週期：是否有**長時間未結束的讀交易**（`SELECT` 後未 commit／未 close 的 transaction）——**這是核心假設 H1**

### 步驟 3：機制實驗（在副本上，逐項寫清量測）
- **E1（WAL 模式確認）**：副本上開一個連線寫入 N 筆 ⇒ `-wal` 增長 ⇒ 關閉連線 ⇒ 重開 ⇒ `-wal` 是否被截斷？
- **E2（checkpoint 可行性）**：副本上 `PRAGMA wal_checkpoint(PASSIVE)` ⇒ 記 `busy`/`log`/`checkpointed` 三欄回傳值（SQLite 回傳三個數：busy=0 表示沒有阻塞者）⇒ 觀察 `-wal` 是否縮減。
- **E3（長讀阻塞驗證）**：在副本上開一個**讀交易**（`BEGIN; SELECT …` 不 commit）⇒ 另一連線寫入並嘗試 `wal_checkpoint(PASSIVE)` ⇒ 若 `busy=1` 且 `-wal` 不縮 ⇒ **H1 成立**（長讀快照阻塞 checkpoint）⇒ 這就是「堆積不 checkpoint」的機制。
- **E4（誰是長讀者）**：把程式碼裡的讀交易路徑逐一對照 E3 的特徵（`BEGIN`/`in_transaction`、未 close 的 cursor、`fetchall` 後未 rollback 的 select、逾時長的 sweep/audit 迴圈）⇒ 列出**候選長讀點**（附檔案與行號）。
- **E5（恢復行為）**：讀交易 commit/rollback 之後，`-wal` 是否隨下一次 checkpoint 回收？

### 步驟 4：結論分級（**三選一，不接受「大概」**）
- **C-A：H1 成立且能指認具體長讀點** ⇒ 提出最小修復（見步驟 5）。
- **C-B：H1 不成立**（例如 `wal_autocheckpoint` 被設成極大值、或 journal_mode 其實不是 WAL、或程式碼根本沒開 WAL）⇒ 依實測指出真正的成因與最小修復。
- **C-C：證據不足**（例如副本實驗無法重現）⇒ **只交報告，明寫「未能證明」與已排除的假設**，**不得**硬給修復。

### 步驟 5：B2 最小修復提案（僅當 C-A／C-B）
- 機制：對每個 DB 以**非阻塞**方式定期 `PRAGMA wal_checkpoint(PASSIVE)`（`PASSIVE` 不阻塞寫入者、不等待讀者；`busy` 回傳值只記錄不重試）。
- 位置與節奏：**0 新排程器計時器**——優先掛既有 30s wake（`scheduler.py` 的 additive 分支慣例）或 store 層的既有連線取得點；**若需要新增計時器 ⇒ 必須在報告中說明理由並由主大腦另行裁示**。
- 邊界：fail-silent（任何例外只 log warning 且**不阻塞主循環**）、有界（每輪最多處理 N 個 DB）、0 新持久化、0 新 schema、0 LLM、0 網路。
- 🔴 **不得**把 `journal_mode` 從 WAL 改成其他（Owner 另立的 `memory.db` 遷移案是**另一票**，本票不碰）。
- 附：守衛測試（AST 層斷言：0 新 `sleep`／0 新 `create_task`／0 LLM／0 網路／fail-silent 路徑）＋**變異實驗**（移除 checkpoint 呼叫 ⇒ 守衛必須轉紅）。

### 步驟 6：交付
- 報告：`%TEMP%\wal_gov\WAL-GOVERNANCE-DIAGNOSIS.md`（UTF-8），章節：① 量測方法與沙盒說明 ② 48 個 DB 的靜態盤點表 ③ E1～E5 實驗結果（每個含分子/分母/單位）④ 結論 C-A／C-B／C-C ＋ 證據 ⑤ 若有修復提案：diff 摘要 ＋ 守衛／變異結果 ⑥ **未做與明文排除**。
- 修復提案若有：worktree 內的**未提交**變更（`%TEMP%\wal_gov_wt`），並產出 `%TEMP%\wal_gov\wal_gov_patch.diff`（`git diff --output=`，**位元保真，勿用 Out-File -Encoding utf8**）。

---

## 4. 驗收（完成的定義）
1. 報告存在且**結論分級明確**（C-A／C-B／C-C 三選一），每個結論有對應實驗編號與數字。
2. 48 個 DB 的 `journal_mode` 全部有值（不得用「推測」代替）。
3. E2／E3 都有**三欄回傳值或 WAL 位元組數**（busy/log/checkpointed 或 before/after bytes）⇒ 可否證。
4. 若 C-A／C-B：worktree 內有**未提交**的最小修復 + 守衛 + 變異實驗結果（每條變異必須轉紅；ABORT 與意外全綠同樣算失敗）。
5. 全程**未寫入 `data/**`**（可由生產檔 mtime/sha256 對照佐證）＋ **未重啟服務** ＋ **未殺任何行程**。

## 5. 測試
- 守衛測試必須是**新增 collectable 檔**（放 `%TEMP%\wal_gov_wt\tests\infra\`），且須在報告中標明「**新增 collectable 檔必須先 `git add`**，否則 repo hygiene 守衛會紅」——**本票不執行 `git add`**（由主大腦合入時處理）。
- 變異 harness 放 `%TEMP%`（**改副本**模式；repo 檔全程不寫入）；baseline 必須是**整個守衛檔全綠**。
- 🔴 引用測試數字必須註明解譯器是 **repo 的 `.venv`**。

## 6. 不做（Out of Scope）
- 不做 `memory.db` 的 journal mode 遷移（另一票）。
- 不動 `src/memory/sage/writer.py`（frozen）。
- 不動 scheduler 的既有分支語意。
- 不部署、不重啟、不 commit／push。
- 不做 A/B 對照實驗（不做「先關 WAL 再量」的破壞性實驗）。

## 7. Frozen Contract 注意
永久紅線 11 檔：`src/agency/stages.py`／`trigger_handler.py`／`event_handler.py`／`dream_handler.py`／`diary_handler.py`、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py` ⇒ **一檔都不許動**。
`src/memory/sage/graph_store.py` 與 `src/memory/store.py` **非** frozen（GOAL-DB-LOCKED-1 已列入 allowed），本票第二段可在 worktree 內改。

## 8. 回報格式
- 步驟 1～3 的關鍵數字（每個寫分子/分母/單位）。
- **結論分級（C-A／C-B／C-C）＋ 支撐實驗編號**。
- 修復提案摘要（若有）＋ 守衛/變異結果。
- **未做與明文排除**（未寫 data、未重啟、未殺行程、未 commit/push）。
- 明確指出：若結論為 C-C（證據不足），**不給修復**，只交報告。
