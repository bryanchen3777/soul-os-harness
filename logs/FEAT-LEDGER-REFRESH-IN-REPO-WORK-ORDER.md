## 工單：FEAT-LEDGER-REFRESH-IN-REPO（台帳刷新器正式納入版控）

- **開立者**：主大腦（Engineering Brain）
- **來源**：Owner 2026-10-28「後續主線作戰視野」第 2 條
  （「配置 R5 白名單，將 repo 外的維護腳本轉正為 repo 列管資產」）
- **狀態**：開立（decision-complete；含一處需 Owner 一句話確認的分支，已給預設值）

---

### 目標

把目前**存在 repo 外、不受版控**的每日台帳刷新器
（`C:\Users\bbfcc\.local\bin\soul-os-harness-backups\ledger_refresh.py`）
轉正為 **repo 列管資產** ⇒ 版本可追溯、可被守衛覆蓋、可被 code review。

---

### 量測現況（主大腦 2026-10-28 實測）

| 項 | 實測 |
|---|---|
| 現況位置 | `C:\Users\bbfcc\.local\bin\soul-os-harness-backups\ledger_refresh.py`（**repo 外**、**不受版控**） |
| 備份鏈 | 同目錄有 `.bak-20261027`（原版）、`.bak-20261027-nb-none`、`.bak-20261027-gate2` ⇒ **版本歷史只存在於檔名裏**（⇒ 這正是要修的問題） |
| 今日已修的 4 處 | ① 保留 `types`/`bounds`/agent 註解 ② `not_before` fallback＝建立日＋8 天（`cohort_not_before`）③ 逐節點下界（`decay_thresholds`）④ **新 cohort 的 `nb` 不再是 `None`**（`else: nb = cohort_not_before(date)`） |
| 今日再修的 1 處 | **gate 2 接受 exit 3（`PENDING_COHORT`）** ⇒ 只有 exit 1／2 才是紅燈（`rc2 not in (0, 3)`） |
| repo 慣例 | `scripts/` 收**所有**工具腳本（`eval_lifecycle_cli.py`、`run_server.py`、`audit_*.py`…）；**repo root 只有 `debug_broadcast.py`** |
| R5 掃描範圍 | `tests/infra/test_no_production_spawn_guard.py:322` `scanned_python_files()` ＝ **repo-root `*.py` ＋ `tests/**/*.py`** ⇒ ⇒ **`scripts/` 不在 R5 掃描範圍** |
| R5 白名單形狀 | `SUBPROCESS_ALLOWLIST: Dict[Tuple[str, int], str]`（以 **(檔案, 行號)** 鎖定、附理由）、大小鎖定 `EXPECTED_SUBPROCESS_ALLOWLIST_SIZE = 22`、且會檢查**失效條目** |

---

### 做法（決策已定，執行者照做）

#### D1. 新增 `scripts/ledger_refresh.py`（repo 列管資產）

1. 內容＝**repo 外現行版**（**含今日 4＋1 處修正**）逐位元搬入；**不得**趁搬入改語意。
2. **路徑全部由 `__file__` 推導**：`REPO = Path(__file__).resolve().parents[1]`；`CLI`／`PY`／`ELEV`／`CANON` 一律從 `REPO` 推導 ⇒ **不得**殘留任何 `C:\Users\...` 絕對路徑（新增守衛會逐字檢查）。
   - 🔺 **實測**：現存版本 L52–57 共 6 個硬寫常數（`REPO`／`CLI`／`ELEV`／`PY`／`CANON`／`BACKUPS`）⇒ 全部必須改為推導。
   - 🔺 **實測（重要）**：`BACKUPS` 是**死常數**（定義後全文**無任何使用點**）⇒ ⇒
     **刷新器不寫 repo 外任何檔案**：它的寫入面只有 ① `scripts/eval_lifecycle_cli.py` 的台帳 literal
     ② `logs/ENGINEERING_STATE.md`（canonical）③ git commit/push ④ `%TEMP%` 沙盒
     ⇒ ⇒ repo 版**天然不越界** ⇒ `BACKUPS` 常數可直接刪除（刪除後由守衛 G2 覆蓋「不得 reintroduce 絕對路徑」）。
3. **不得**在 import 期做任何 IO／不建立連線（只定義函式與常數）⇒ 與 repo 既有腳本慣例一致。
4. 保留既有 CLI 介面：`--dry-run`（**dry-run 不得寫任何檔案**，含基準檔）與無參數模式（正式刷新）。
5. **不得**刪除／改動 repo 外那份（它是 20:40 cron 的現行執行來源）⇒ ⇒ D3 完成前它必須保持可用。

#### D2. 新增守衛 `tests/infra/test_ledger_refresh_in_repo_guard.py`

| # | 不變量 |
|---|---|
| G1 | `scripts/ledger_refresh.py` 存在**且被 git 追蹤**（`git ls-files` 可查到） |
| G2 | 檔內**無任何絕對路徑字面值**（`C:\`／`C:/`）⇒ `REPO` 必須由 `__file__` 推導 |
| G3 | **import 期零 IO**：import 該模組**不得**產生任何檔案／子行程（用 tmp cwd ＋ monkeypatch 探針證明） |
| G4 | **今日 5 處修正必須仍在**：`cohort_not_before(date)` 的 `else` 分支、`rc2 not in (0, 3)`、`types`／`bounds` 保留、`decay_thresholds` 逐節點下界、`cohort_not_before` fallback ＝建立日＋8 天 |
| G5 | `--dry-run` **不寫任何檔案**（跑前後 `data/elevation/` 與 `scripts/eval_lifecycle_cli.py` 的 sha256 相同） |

⇒ 每條都要有**專屬變異實驗**（把該項改掉 ⇒ 必紅），變異 harness 放 `%TEMP%`、**repo 檔案全程零寫入**（前後 sha256 相同）。

#### D3. 交接（一次只切一個開關）

1. 先用 **repo 版**跑 `--dry-run` ⇒ 與 **repo 外現行版**的 `--dry-run` 輸出**逐位元相同**（含每個 cohort 的 `approved → merged` 數字與 verdict）。
2. 再跑 repo 版的 **gate 1／gate 2 邏輯**（守衛測試 ＋ 沙盒 CLI）確認綠。
3. **最後**才把 20:40 cron（`f197c501`）的執行路徑切到 repo 版（cron 指令更新由主大腦執行；**執行者不得動 cron**）。
4. 切換後**保留** repo 外那份作為 frozen backup（在該檔頭加一行註記：已轉正至 `scripts/ledger_refresh.py`，此份凍結備用）⇒ **不得刪除**。

#### D4. ✅ 已裁決（Owner 2026-10-28）：採**預設分支**，R5 掃描器**維持現狀**

**裁定原文**：「核准採納預設分支 `scripts/ledger_refresh.py`……R5 掃描器維持現狀，
`EXPECTED_SUBPROCESS_ALLOWLIST_SIZE = 22` 一字不改，避免無謂的規則計數擾動。
嚴守『兩版 `--dry-run` 逐位元相符才切換 Cron 指針』的平滑交接合約；repo 外部備份維持 Frozen 存檔作為兜底。」

⇒ **本票的最終決策（無分支、無待決項）**：
1. 刷新器正規化為 **`scripts/ledger_refresh.py`**（依循 `run_server.py`／`server_ops.ps1`／`eval_lifecycle_cli.py` 的既有慣例）。
2. **R5 掃描器不動**、`EXPECTED_SUBPROCESS_ALLOWLIST_SIZE = 22` **一字不改** ⇒ **不新增** R5 白名單條目。
3. **平滑交接合約**：兩版 `--dry-run` 輸出**逐位元相同**才切換 cron 指針（cron 指針由**主大腦**切，執行者不得動 cron）。
4. repo 外部那份（`soul-os-harness-backups/ledger_refresh.py`）**維持 Frozen 存檔作為兜底** ⇒ **不得刪除**。

---

### 驗收（完成的定義）

1. `scripts/ledger_refresh.py` 入版（commit + push）⇒ `git log` **可查到它的完整歷史**（與 repo 外那份不同：不再只有檔名備份鏈）。
2. 新守衛 G1～G5 全綠 + 五條變異各自精準轉紅（repo 零寫入）。
3. repo 版 `--dry-run` 與 repo 外現行版 `--dry-run` 輸出**逐位元相同**。
4. 20:40 cron 路徑已切換（由主大腦執行）⇒ 且 repo 外那份已標記為 frozen backup。

---

### 不做（Out of Scope）

- 不改台帳**判準**（型別中立化／逐節點下界／三態判定皆已閉環）⇒ 本票**零語意變更**。
- 不改 `scripts/eval_lifecycle_cli.py`。
- 不動 20:40 cron 的**排程時刻**（只換執行路徑）⇒ 也不得由執行者觸發該 cron。
- 不刪除 repo 外那份與其 `.bak` 鏈。
- 不把刷新器改成 pytest 會收集的檔名（不得 `test_*.py`；`testpaths = tests` 已保證不收集，但檔名也不得是 `test_*.py`）。

---

### 回報格式

1. 改動檔案清單（逐檔）。
2. 守衛 G1～G5 的「變異內容 → 哪條轉紅 → repo sha256 前後相同」證據。
3. 兩版 `--dry-run` 的**逐位元相同**證據（貼關鍵行）。
4. 20:40 cron 切換的**交接步驟**與「repo 外那份已標記 frozen」的截圖／diff。
5. 是否採用 D4 的預設分支（放 `scripts/`、不動 R5 白名單），或 Owner 已指定別的分支。
