# MEM-VISIBILITY-C1 — 即時可見性 Flush 契約（Immediate-Visibility Flush Contract）

- 日期：2026-09-10（Branch `main` @ `2818af8`，= `origin/main`）
- 性質：**DESIGN ONLY — 0 code、0 實作、0 解凍、0 部署**。本檔為唯一交付物。
- 作者角色：developer（契約作者）
- 上游依據：`docs/MEM-VISIBILITY-0-AUDIT.md`（跨進程記憶可見性審計，P0）＋ MEM-VISIBILITY-C2 已上線實作（commit `820174a`）
- 狀態：**C1 仍為凍結（Frozen）狀態，本票不解凍、不授權實作**。§8 僅為「解凍申請範圍」，決定權在 Owner。

---

## 硬約束（Owner 已拍板，原封不動引用，本契約不得改寫或放寬）

1. **掛載點唯一**：只能是 `SAGELiteProvider.post_reply_commit` **成功後**、在 **worker thread 內**執行 `store.flush()`，**對齊 VC-UNIFY-1.2 的既有做法**。不得另開新定時器、不得改 event loop 主線程語意、不得在其他位置偷掛第二個 flush 點。
2. **禁止為了 flush 取消 `MODERN_NATIVE_AGENTS` 的同化 bypass**。茜（Akane）／麻衣（Mai）不 flush 的問題，應由這條**通用後置 flush 消掉**，**不是**讓她們走進異世界同化路徑。
3. **本票 0 code**：不得修改 `src/`、`clients/`、`tests/`、`config/`、`personas/`；不得實作、不得提交任何 code diff。
4. **C1 仍為凍結狀態**：SAGE 寫入主幹屬 Frozen Contract，**本票不解凍**；契約只能「提出解凍申請的範圍與理由」，解凍決定權在 Owner。
5. 不得改 `SAGELiteProvider.post_reply_commit` 的既有簽名（除非契約中明確列為「需 Owner 批准的變更請求」）。
6. 不得改 `add_fact` 的批次語意（`_BATCH_SIZE=20`）或 `GraphStore.flush()` 的既有行為——C1 只在**呼叫時機**上做文章。

---

## §0 核心結論（一句話）

**C1 = 在 `SAGELiteProvider.post_reply_commit` 的「本輪寫入階段成功之後」、於 worker thread 內、對 `self._store` 做一次無條件 `flush()`（實際 commit 與否交給 `GraphStore.flush()` 既有的 `_pending_writes > 0` 守衛），並以 fail-safe 吞掉例外——單點、0 新狀態、0 新定時器、0 簽名變更、0 schema 變更。**

它補的是 C2 **刻意不補**的那一半：C2 讓跨進程讀者不再被擋（不再 `database is locked`），但讀者仍只看到**已提交舊快照**；C1 讓「本輪對話」在 `post_reply_commit` 返回時**已經提交**，因此任何在那之後新開的連線都必須 SELECT 得到本輪 fact。角色無關：`MODERN_NATIVE_AGENTS`（含茜／麻衣）的 EH-3.1 bypass **原封不動保留**，她們只是不再因為 bypass 而漏掉 commit。

---

## §1 現況基線

### 1.1 C2 已上線狀態（既有實證）

| 項目 | 現狀 | 證據 |
|---|---|---|
| C2 內容 | `_init_db` 加守衛：`current_version == _SCHEMA_VERSION` 時**跳過** `schema_meta` 的 `INSERT OR REPLACE` 與其 commit；新庫／版本不符維持既有 create / migrate / 寫 version | `src/memory/sage/graph_store.py:86-115`（守衛於 `:110`，註解於 `:104-109`） |
| C2 效果 | 讀側（VC `default_memory_retriever`）開連線**不再搶 WAL 寫鎖** → `database is locked`（~11s）失敗模式消失 | `logs/ENGINEERING_STATE.md:1633`（C2-V 矩陣：僅 `INSERT OR REPLACE schema_meta` 阻塞 11.000s；跳過後 reader 開連線 0.000s） |
| **C2 刻意不解的事** | 未 flush 的普通生活句，跨進程讀者讀到的仍是**已提交舊快照**（快照隔離）。**這是規格，不是漏修** | `logs/ENGINEERING_STATE.md:334`（「C2 只解『讀側開連線搶寫鎖』，**不解即時最新值**……即時同步屬 C1/C3，C1 未授權」）；`graph_store.py:108-109` 註解自述 |
| C1 目標 | 寫側每輪 flush → 即時可見（本契約） | 本檔 |

### 1.2 目前寫入路徑與 commit 時機（既有實證，file:line）

調用鏈（文字主服務生產路徑）：

```
AGENT_SPEAK (publish)
 └→ MemoryMiddleware._on_agent_speak
      └→ _commit_async（fire-and-forget 受管 Task，event loop 主線程）   middleware.py:479-493, :512
           └→ SAGELiteProvider.post_reply_commit（async）               provider.py:306-392
                ├→ [no-diary 分支] run_in_executor(write_turn, skip_graph=True) → return   provider.py:334-351
                ├→ run_in_executor(partial(write_turn, source_pair=…, inner_life_event_id=…))  provider.py:359-369
                │    └→ MemoryWriter.write_turn → extract_and_write → _write_single
                │         → GraphStore.add_fact                        writer.py:270-329, :381
                │              └→ 累積 _pending_writes >= 20 才 conn.commit()   graph_store.py:396-399
                ├→ if fact_ids: run_in_executor(_tag_explanatory_assimilations, …)   provider.py:374-382
                │    └→ [Gate 1] modern-native → return（0 store 寫入、0 flush）   provider.py:426-438
                │    └→ [Gate B] 無定義性 marker → return（0 flush）              provider.py:442-449
                │    └→ 打標迴圈 → self._store.flush()                          provider.py:471-481
                ├→ self._cache.invalidate()                                provider.py:383
                ├→ if _turn_count % 20 == 0: run_scheduled_decay + auto_resolve_conflicts  provider.py:385-391
                └→ self._turn_count += 1                                   provider.py:392
```

**關鍵事實（既有實證）**：普通生活句走完 `post_reply_commit` 後**沒有任何 flush / commit**（`provider.py:352-392` 主分支全程無 flush；`write_turn` 內部亦無）。pending 會一直掛在未提交事務上，直到累積滿 20 筆（`graph_store.py:396-399`）或 `shutdown()`（`provider.py:210-213` → `graph_store.py:899-904`）。

### 1.3 既有 flush / commit 呼叫點全清單（含判讀）

| # | 觸發點 | 位置 | 條件 | 本契約判讀 |
|---|---|---|---|---|
| 1 | `add_fact` 批次提交 | `graph_store.py:396-399` | `_pending_writes >= self.batch_size`（`_BATCH_SIZE=20`，`:20`） | **不動**（硬約束 6） |
| 2 | `set_fact_dimensions` 批次提交 | `graph_store.py:402-445` | 同上（EH-2 打標路徑） | **不動** |
| 3 | `update_weight` 批次提交 | `graph_store.py`（合併／強化路徑，同門檻） | 同上 | **不動** |
| 4 | EH-3.1 後置 flush | `provider.py:478-481`（`_tag_explanatory_assimilations` 結尾） | 需過 Gate 1（非 modern-native）＋ Gate 2（有 fact）＋ Gate B（有定義性 marker）＋ 打標迴圈走完 | **保留原樣**（§4.1） |
| 5 | `GraphStore.flush()` 顯式呼叫（VC 端） | `clients/voice_companion/akane_voice_brain.py:638-648`（VC-UNIFY-1.2） | 語音端 `sage_write.enabled` 且 `post_reply_commit` 成功返回 | **對齊對象**（§4.5），**不改動** |
| 6 | `GraphStore.flush()` 顯式呼叫（Goal Seed） | `src/goals/seed_provider.py:722` | goal 種子寫入後 | 既有呼叫點，與 C1 無交互，**不動** |
| 7 | `GraphStore.close()` = flush + close | `graph_store.py:899-904`，由 `provider.shutdown()` 觸發（`provider.py:210-213`） | 進程下線／middleware shutdown | **不動**（VC-UNIFY-1.2.1 Test 4 依賴此點） |
| 8 | 「記憶快取失效」 | `provider.py:301/350/383`（`_cache.invalidate()`） | TTL 30s 的**同進程** PrefetchCache（`provider.py:171`） | **與 SQLite commit 無關**，C1 不動它 |
| 9 | Harness 端補償性 flush | `harness/eh2_smoke_natural3.py:286`、`harness/fixture.py:237` 等 | 測試／harness 專用 | 不在 C1 範圍；**不得**以「測試端補償」替代生產機制（`tests/test_vc_eh_unification.py:506-531` 潔淨度自檢） |

### 1.4 已實證事實 vs 推論（本契約的證據分級）

**已實證（可直接引用為契約前提）**

- E1：C2 後「reader 開連線 → 11s `database is locked` → fail-silent None」消失；未提交資料仍不可見。來源：C2-V 矩陣（`logs/ENGINEERING_STATE.md:1633`）＋ `tests/test_graph_store_c2_read_open.py:51-53, :93-99`。
- E2：普通生活句未提交時，跨進程讀者讀到 0 列；`flush()` 後同一讀者立即可見（控制組）。來源：MEM-VISIBILITY-0 §4 A 組原始 stdout（`docs/MEM-VISIBILITY-0-AUDIT.md:118-124, :143-147`）。
- E3：定義句（EH-3.1 路徑）**已**即時跨連線可見（pending=0、VC 新連線 0.0s 讀到 3 筆）。來源：同 §4 B 組。
- E4：現代原生角色（`MODERN_NATIVE_AGENTS`，`horizon.py:61-69`，含 `agent_akane`）在 EH-3.1 **Gate 1 直接 return**（`provider.py:434-438`）→ 連定義句也不 flush。來源：審計 §8.2（`docs/MEM-VISIBILITY-0-AUDIT.md:198`）；本次覆核程式碼確認。
- E5：`GraphStore.flush()` 的既有行為是**條件式 commit**：`if self._conn and self._pending_writes > 0:` 才 `commit()` 並歸零（`graph_store.py:892-897`，`@_locked` 於 `:892`）。→ **「無 pending 時呼叫 flush」是天然 no-op**。
- E6：`post_reply_commit` 內既有的兩個 SQLite 執行點都維持在 `run_in_executor` 的 worker thread 內（`provider.py:359-369`、`:374-382`），且函式註解明確記載「不在主 asyncio thread 觸發 SQLite 寫入」（`provider.py:370-372`）。
- E7：`GraphStore.close()` 亦為 `@_locked`（`graph_store.py:899`）→ flush 與 close **不會交錯**（RLock 互斥，`graph_store.py:24-36, :52-57`）。
- E8：`add_fact`／`flush`／`close` 的行為已被既有測試鎖定（`tests/test_graph_store_c2_read_open.py`、`tests/test_graph_store_concurrency.py`、`tests/test_graph_store_confidence.py`）。

**推論（未實測，需在實作票以測試固化）**

- I1：每輪一次的 commit 成本可忽略（WAL + `synchronous=NORMAL`，fsync 落在 checkpoint 層；生產 turn 頻率為分鐘級，10 角色 × 5s 節流 `middleware.py:118-122`）。來源：審計 §5 評估（`docs/MEM-VISIBILITY-0-AUDIT.md:163`）——**沿用，未新增實測**。
- I2：C1 之後「寫者持鎖窗口」由「累積 20 筆期間的持續占用」縮短為「單次寫事務毫秒級」，對**第二寫者**（同一 agent 檔的文字端＋語音端並存）反而更友善。此為本契約對審計 §5 的**重估**（§5.2），未實測。
- I3：崩潰耐久性由「最多丟 19 筆 pending」改善為「最多丟當前輪」。未實測。
- I4：`post_reply_commit` 因 C1 多一次 executor 往返（`await`），延遲量級 = 一次 commit 成本。因該函式本身已是 fire-and-forget（`middleware.py:479-493, :512`），**不影響回覆路徑**。

---

## §2 契約不變量（Invariants）

以下命題為 **C1 生效後必須成立** 的硬不變量，同時給出未來實作票的**可硬斷言形式**。

### INV-1（核心）即時可見性

> **在輪次 R 的 `post_reply_commit` 成功返回之後（且未拋例外），任何在該時刻之後新開的 SQLite 連線，必須能 SELECT 到輪次 R 所插入的全部 `facts` 列。**

- 可硬斷言：`write_turn` 回傳的 `fact_ids` 非空時，`await post_reply_commit(...)` 返回後**立刻**用全新連線 `SELECT fact_id FROM facts WHERE fact_id IN (...)` → 集合包含全部 `fact_ids`。
- 等價形式：返回後 `store._pending_writes == 0`。
- 範圍界定：只涵蓋輪次 R 產生／更新的 fact；不涵蓋其他進程在同時間寫入的內容（不同連線、不同 agent 檔）。

### INV-2（成功判準的定義）「成功返回」是充分條件

> `post_reply_commit` **未拋例外地返回**（awaited coroutine completes normally）⇒ INV-1 成立。若 `flush` 本身失敗，**不得**使 `post_reply_commit` 拋例外（見 INV-6），但 INV-1 降級為「不保證」（fail-safe 語意，§6）。

- 可硬斷言：對 flush 注入必拋例外的 stub → `post_reply_commit` 不得 raise；`facts` 列仍為 pending（未回滾）；隨後顯式 `flush()` 仍能提交同一批列（0 資料遺失）。

### INV-3 角色無關性（通用後置）

> INV-1 對所有 agent 一體成立，**不依賴 agent 身份、不依賴 EH-3.1 的任何閘門**。特別是 `MODERN_NATIVE_AGENTS` 的 7 個角色（`agent_akane`、`agent_mai`、`agent_anna`、`agent_aoi`、`agent_miku`、`agent_ruka`、`agent_yua`，`horizon.py:61-69`）與「無定義性 marker 的普通生活句」都必須成立。

- 可硬斷言：「現代原生角色 ＋ 無定義性 marker 的普通生活句」→ 返回後新連線可見（此為 C1 前**必然失敗**的 A/B 差異點）。
- 同時硬斷言：該輪 **0 打標**（`origin` 仍為預設 `lived_experience`、`horizon_state` 仍為預設）→ 證明 bypass 未被動用（INV-9）。

### INV-4 掛載點唯一、0 新機制

> C1 在生產代碼中**恰好新增一個** flush 呼叫點，且該點位於 `SAGELiteProvider.post_reply_commit` 的成功路徑內。**0 新定時器、0 背景輪詢、0 新 async task、0 新線程。**

- 可硬斷言（靜態）：`src/memory/sage/provider.py` 的 `flush` 呼叫點數量由 1（`:481`）→ 2；新增的那一個必須在 `post_reply_commit`（`ast` 取該函式節點）之內。
- 可硬斷言（靜態）：`src/` 全域不得新增 `asyncio.sleep` 輪詢、`threading.Timer`、`loop.call_later`、新增的 `create_task`／`create_managed_task` 站點（與 base commit `2818af8` 逐點比對）。
- 可硬斷言（結構）：新增呼叫點不使用 `loop.run_in_executor` 以外的執行手段，且**不得**出現在 `_tag_explanatory_assimilations` 內（該函式已有自己的 flush，維持原樣）。

### INV-5 介面凍結

> `SAGELiteProvider.post_reply_commit` 的**簽名逐字不變**（`provider.py:306-321`，含 keyword-only 語意與 4 個具名參數）；`GraphStore` 的公開方法簽名與 `_BATCH_SIZE` 不變。

- 可硬斷言：`ast` 比對 base commit —— 函式 `args`／`defaults`／`kw_defaults` 節點完全一致；`graph_store.py` 的模組常數 `_BATCH_SIZE == 20`、`_SCHEMA_VERSION == 9` 不變。

### INV-6 fail-safe 不中斷

> flush 的任何例外（`sqlite3.OperationalError` / `ProgrammingError` / `RuntimeError` / 任意 `Exception`）**一律吞掉**，只記 warning，**絕不**向上拋、**絕不**回滾已 INSERT 的 fact、**絕不**影響回覆路徑。

- 可硬斷言：monkeypatch `store.flush` 為 raise → `await post_reply_commit(...)` 正常返回（0 raise）；呼叫端 `_commit_async` 的 `except` 分支（`middleware.py:490-493`）未被觸發；`logger.warning` 被呼叫。

### INV-7 批次語意不變

> `_BATCH_SIZE=20` 的批次提交保留（`graph_store.py:20, :396-399`）；`GraphStore.flush()` 的**函式體逐字不變**（仍為 `_pending_writes > 0` 才 commit）；`add_fact` / `set_fact_dimensions` / `update_weight` 的門檻邏輯不變。

- 可硬斷言：`graph_store.py` 與 base commit 的 `flush()` 函式 AST 完全一致；`_BATCH_SIZE` 由 base 到 C1 無 diff。
- 語意說明：單輪內若萃取出的 fact 超過 20 筆，**輪內**仍會觸發批次提交（不變）；C1 只是在輪末補一次。

### INV-8 無 pending 時 0 額外寫入

> 當輪次 R 沒有任何 pending（例如該輪 0 萃取、或 EH-3.1 已先 flush、或 no-diary 分支）時，C1 的呼叫**不得**產生任何 SQLite 寫入動作。

- 可硬斷言：`PRAGMA data_version`（或 `conn.total_changes`）在「0 fact 輪次」前後不變；且 `flush()` 因 E5 守衛直接 return。

### INV-9 保護既有 bypass 與白名單（**不得反向修改**）

> `MODERN_NATIVE_AGENTS` 的同化 bypass（`provider.py:426-438`）**逐字保留**；`horizon.py:61-69` 的白名單不變；`NO_DIARY_AGENTS = {"agent_ram"}`（`provider.py:44`）不變。C1 **不得**以「讓現代原生角色走進同化路徑」為手段達成可見性。

- 可硬斷言：`is_modern_native("agent_akane") is True` 且 `_tag_explanatory_assimilations` 對其為 early return（0 `set_fact_dimensions` 呼叫、0 打標）；`NO_DIARY_AGENTS` 集合逐字不變。
- 可硬斷言（靜態）：`provider.py:426-438` 與 `horizon.py:61-69` 與 base commit 逐字相同（AST 或文字 diff）。

### INV-10 只提交、不刪改

> `flush()` 只 `commit()`；C1 **不得**引入任何 prune / delete / decay / 資料變更。

- 可硬斷言：C1 diff 中 0 新增 `DELETE` / `prune` / `vacuum` 呼叫；`facts` 列數在 flush 前後只增不減。

---

## §3 掛載點精確定義

### 3.1 位置（唯一）

**在 `SAGELiteProvider.post_reply_commit` 的成功路徑內，緊接在 EH-3.1 hook 區塊（`provider.py:374-382`）之後、`self._cache.invalidate()`（`provider.py:383`）之前。**

理由（決策已定）：

1. 這是「本輪所有對 store 的寫入動作都已結束」的第一個時間點：fact INSERT（`:359-369`）與 EH-3.1 打標（`:471-481`）都在它之前；因此單次 flush 就覆蓋整輪。
2. 它在 `if self._turn_count % 20 == 0:` 的 evolution 區塊（`:385-391`）**之前** → 可見性保證**不耦合** evolution 的成敗（該區塊目前**未**包 try/except，若它拋例外會讓函式整體 raise；把 flush 放在它之前，可讓「本輪對話 fact 已提交」這件事不被 evolution 的失敗連坐）。
3. 它在 no-diary 分支（`:334-351`）的 `return` 之後才被執行到 → **該分支不可達**，因此**不需要也不得**在該分支另掛第二個 flush 點（硬約束 1；語意上該分支 0 graph 寫入，見 §4.4）。
4. 它不改動 `_turn_count` 的遞增順序（`:392` 仍為函式最後一項狀態更新）。

> **規格描述（非可貼入 `src/` 的實作代碼；本票 0 code）**：新增一個 `try` 區塊，內含 `self._store is not None` 的護衛與一次 `await loop.run_in_executor(None, self._store.flush)`；`except Exception` 分支只做 `logger.warning`。使用的 `loop` 即函式內既有變數（`provider.py:352`）。

### 3.2 成功判準（決策已定）

**「成功」= 本輪寫入階段成功**，即 `await loop.run_in_executor(..., write_turn, ...)`（`provider.py:359-369`）已正常返回（此時本輪 fact 已 INSERT 進當前未提交事務），且控制流已走到 §3.1 的位置。

- **不採用**「整個函式執行完畢」作為判準（理由見 §3.1 第 2 點；且若以此為準，flush 必須放在 `:392` 之後，會與 evolution 成敗耦合）。
- 此判準**不**要求 `fact_ids` 非空：0 萃取的輪次照樣呼叫（no-op，INV-8）。
- 此判準**不**改變「回覆已送出」的事實：呼叫鏈上游是 fire-and-forget 受管 Task（`middleware.py:479-493, :512`），`await` 不會阻塞回覆。
- ⚠️ 此判準的措辭是本契約對 Owner 硬約束中「成功後」一詞的**操作性定義**，已列入 §9 D-1 供 Owner 確認（另一選項見該條）。

### 3.3 條件式？（決策已定）

**無條件呼叫，不新增任何 provider 層的前置判斷。**

理由：`GraphStore.flush()` 本身已是條件式（E5：`if self._conn and self._pending_writes > 0`）。在 provider 層再加 `if self._store._pending_writes > 0:` 會（a）新增對私有狀態的耦合，（b）與硬約束 6「不改 flush 既有行為」的精神一致但要動更多東西，（c）對 INV-8 毫無增益。唯一保留的判斷是 `self._store is not None`（防禦 `Provider` 未 initialize 的呼叫，與 `provider.py:495` 既有 `if not self._store:` 風格一致）。

### 3.4 同步 / 非同步與執行緒（決策已定）

- **必須是 `await`（同步等待完成），不得 fire-and-forget。** 若採 fire-and-forget（另開 task／`run_in_executor` 不 await），則「`post_reply_commit` 返回後即可見」的 INV-1 會退化成競態——契約不成立。
- **必須在 worker thread 執行**（硬約束 1）：以 `loop.run_in_executor(None, self._store.flush)` 表達，與同函式既有兩處 SQLite 執行點（`:359-369`、`:374-382`）及函式註解（`:370-372`）一致。
- **不改 event loop 主線程語意**：新增的只有一個 executor 任務；`post_reply_commit` 仍為 async 函式、仍由 `_commit_async` 背景 Task 驅動（`middleware.py:479-493`）。

> 對齊說明（**誠實記載差異**）：VC-UNIFY-1.2 的 flush（`akane_voice_brain.py:641-648`）在**呼叫位置語意**上與 C1 完全同構（`post_reply_commit` 成功返回後、顯式 flush、fail-silent、`hasattr` 取 store），但它執行在**背景 task 的 event loop 線程**上（`provider.post_reply_commit` 內部已把 SQLite 工作丟進 executor，返回後即回到 loop 線程）。C1 依 Owner 硬約束 1 採**更嚴格的 worker-thread 親和性**，與 `provider.py:370-372` 的既有慣例一致；此差異列為 §9 D-5（是否另案統一 VC 側）。

### 3.5 例外處理（fail-safe，決策已定）

| 情況 | 契約要求 |
|---|---|
| `flush()` 拋任何例外 | `except Exception`（含 `sqlite3.*`）→ `logger.warning` 一行（訊息需含 `profile_id` 與例外型別），**不 re-raise、不回滾、不重試** |
| `self._store is None` | 直接跳過（不呼叫、不記 warning） |
| `asyncio.CancelledError` | 屬 `BaseException`，不被 `except Exception` 吞；**維持既有語意**（背景 Task 被取消即取消，與 VC 側 `akane_voice_brain.py:649-650` 的處理一致） |
| 後續流程 | `_cache.invalidate()`（`:383`）、evolution（`:385-391`）、`_turn_count += 1`（`:392`）**一律照常執行**，不受 flush 成敗影響 |

### 3.6 本契約**不**做的事（掛載點唯一性驗證）

- 不在 `middleware.py` 的 `_commit_async` 內加 flush（會成為第二個掛載點）。
- 不在 `MemoryWriter` / `GraphStore` 內加「自動 flush」開關。
- 不在 `sync_turn`（`provider.py:268-304`）加 flush（該函式的 flush 覆蓋面列入 §9 D-9）。
- 不新增定時器／週期掃描／background task（硬約束 1）。
- 不把 `_BATCH_SIZE` 調小、不改為時間窗提交（那是 C3 的設計空間）。

---

## §4 與既有機制的交互

### 4.1 EH-3.1 的定義句 flush：重疊但**不合併**

| 面向 | 判讀 |
|---|---|
| 覆蓋關係 | **部分重疊**。對「非 modern-native ＋ 有定義性 marker ＋ 有 fact」的輪次，EH-3.1 的 flush（`provider.py:481`）會先提交（含本輪 fact），C1 的 flush 隨後成為 no-op（E5）。對其餘**絕大多數**輪次（普通生活句、或現代原生角色），EH-3.1 不 flush，C1 是唯一提交點。 |
| 是否合併／移除 | **不合併、不移除**。理由：① 它是 Idiolect 讀側閉環的原始修法，其註解（`provider.py:478-481`）自述了「讀側開新連線讀不到未 commit 的 UPDATE」；② 移除或改寫會擴大解凍範圍到 `_tag_explanatory_assimilations` 的語意；③ 保留它，C1 仍能獨立滿足 INV-1／INV-3。 |
| 實際提交點分佈 | 定義句（非原生角色）→ 提交發生在 EH-3.1（較早）；其他所有輪次 → 提交發生在 C1（§3.1 位置）。兩者**都**在 `post_reply_commit` 的同一輪內、**都**在 worker thread 執行，語意一致。 |
| 順序保證 | C1 位於 hook **之後**，故 hook 若要打標，其 UPDATE 一定先發生（且已被自己的 flush 提交）；C1 不會搶在打標前提交。 |

### 4.2 `_BATCH_SIZE=20` 批次提交：**完整保留**

- `add_fact` 的 `_pending_writes += 1` 與 `>= self.batch_size → commit()`（`graph_store.py:396-399`）逐字不變。
- 單輪 fact 數 > 20 時，輪內照樣發生批次提交（不變）；C1 只在輪末補一次 flush。
- 因此「批次合併的吞吐紅利」在**單輪內部**仍存在；C1 消掉的只是**跨輪累積**（那正是可見性延遲的來源）。

### 4.3 `MODERN_NATIVE_AGENTS` bypass 在 C1 下的實際行為（**應被通用後置消掉**）

**角色清單**（`horizon.py:61-69`，逐條）：`agent_akane`（茜）、`agent_mai`（麻衣）、`agent_anna`、`agent_aoi`、`agent_miku`、`agent_ruka`、`agent_yua`。
**非白名單**（走完整 EH-3.1）：`agent_ram`（no-diary，另有分支）、`agent_rem`（雷姆）、`agent_mahiru`（椎名真昼）。

| 環節 | C1 前 | C1 後 |
|---|---|---|
| 程式路徑 | `post_reply_commit` → `if fact_ids:` → `_tag_explanatory_assimilations` → **Gate 1**（`provider.py:426-438`）`is_modern_native(profile_id)` 為真 → `logger.debug` → `return` | **完全相同的路徑與 return**（逐字不動，INV-9） |
| 該輪 store 寫入 | 只有 fact INSERT（`add_fact`），**0 打標** | 只有 fact INSERT，**0 打標** |
| 該輪 commit | **0**（pending 掛著直到 20 筆或 shutdown） | **C1 在 hook 返回後**於 §3.1 位置提交 |
| 可見性 | 跨進程讀者讀不到（舊快照） | 返回後即可見（INV-1／INV-3） |

**逐條結論**：對上述 7 個角色，C1 **不改變**她們的同化語意（仍不內化異世界/常識定義句，仍不打 `assimilated/aware`），只把她們的**普通寫入**在輪末提交。**因此硬約束 2 以「通用後置」而非「取消 bypass」被滿足**——C1 的實作 diff 不得觸碰 `provider.py:426-438` 與 `horizon.py:61-69`（列入 INV-9 的靜態斷言）。

### 4.4 `NO_DIARY_AGENTS`（`agent_ram`）：不變

- no-diary 分支（`provider.py:334-351`）走 `write_turn(skip_graph=True)` → 不呼叫 `add_fact` → 該 provider 的 `_pending_writes` 恆為 0。
- 該分支在 §3.1 位置**之前** `return`，故 C1 的 flush 對其不可達 → 不需要、也不得在該分支加第二個 flush 點（否則違反硬約束 1）。
- 可見性語意不變：`agent_ram` 的對話本來就不寫 graph（`provider.py:40-44`）。

### 4.5 與 VC-UNIFY-1.2 的對稱性

| 面向 | 語音端（VC-UNIFY-1.2，不改動） | 文字端（C1，本契約） |
|---|---|---|
| 掛載位置 | `schedule_sage_commit` 的 `_commit()` 內、`await post_reply_commit(...)` 之後（`akane_voice_brain.py:630-648`） | `post_reply_commit` 內、寫入階段成功後（`provider.py:382/383` 之間） |
| 取得 store | `hasattr(provider, "_writer").store` → fallback `provider.store` | `self._store`（provider 自身狀態） |
| 執行執行緒 | event loop 線程（背景 task） | **worker thread**（硬約束 1） |
| 失敗處理 | `except Exception` → `logger.warning`（`:646-648`） | 同構（§3.5） |
| 條件 | `post_reply_commit` 成功返回 | 寫入階段成功（§3.2） |

**結論**：C1 讓文字端與語音端在「成功後顯式 flush」這一語意上對齊（硬約束 1 的「對齊 VC-UNIFY-1.2 既有做法」），並使**雙向**即時陪伴閉環成立（語音→文字已有；文字→語音由 C1 補上）。

### 4.6 與 C2 及讀側快取的交互

- **與 C2 的關係是互補、非重疊**：C2 解「讀者開連線搶寫鎖」（失敗模式）；C1 解「讀者讀不到最新列」（正確性模式）。C2 之後，C1 的 commit **不再**是「解除讀者阻塞」的手段——讀者在 C1 前後都不會被阻塞，差別只在**看得到哪個快照**。
- **同進程 PrefetchCache（TTL 30s，`provider.py:171`）不受 C1 影響**：它是記憶 prefetch 結果快取，不是 SQLite 快照快取；`_cache.invalidate()` 的時機不變。C1 承諾的是**跨進程／新連線**可見性，不對同進程的快取 TTL 作任何承諾。
- **VC 讀側每次開新連線**（`akane_voice_brain.py:337-361` 的 `default_memory_retriever` 每次 `GraphStore(db_path=...)` + `close()`）→ 無需任何快取失效配合；這正是 C1 能純靠「寫側提交時機」解決問題的原因。
- **`retrieve_idiolect`（`horizon.py:77-99`）同樣是新連線 SELECT-only** → 亦受益。

---

## §5 風險與代價

### 5.1 per-turn commit 的成本（沿用審計評估）

| 面向 | 評估 | 依據 |
|---|---|---|
| 吞吐量 | **低影響**。WAL + `synchronous=NORMAL`，commit 不需每次 fsync（fsync 落在 checkpoint 層）；單次 commit = WAL 寫鎖 + 少量頁面寫。生產 turn 頻率為分鐘級（10 角色、5s 節流 `middleware.py:118-122`） | `graph_store.py:80-81`；審計 §5（`docs/MEM-VISIBILITY-0-AUDIT.md:163`） |
| 額外 executor 往返 | 每輪 +1 次 thread-pool hop（與既有的 2 次同量級） | `provider.py:359-369, :374-382` |
| WAL 檔增長／checkpoint 頻率 | commit 次數上升 → 觸及 SQLite 自動 checkpoint（預設 1000 pages）的頻率略升；屬常態運作，無新機制 | 推論（I1） |
| 同進程鎖 | flush 取 `GraphStore._lock`（RLock，`graph_store.py:24-36, :52-57`）→ 與同進程讀側（`prefetch` 走 `asyncio.to_thread`）短暫互斥；量級 = 毫秒 | 推論 |

### 5.2 WAL 寫鎖競爭：**C2 之後的重估**

審計 §5 的併發評估（`docs/MEM-VISIBILITY-0-AUDIT.md:164-165`）是在 **C2 之前**寫的，當時「讀者開連線也要寫 schema_meta」尚未消除。重估如下：

| 情境 | C2 前 | C2 後（現況） | C2 + C1（提案） |
|---|---|---|---|
| 讀者（VC 新連線） | 需搶 WAL 寫鎖 → 被未提交 writer 阻塞 10s → `database is locked` | **純讀**：不被 writer 阻塞（E1） | 同 C2（無變化） |
| 讀者看到什麼 | 失敗（None）或舊快照 | 舊快照 | **最新已提交快照 = 本輪 fact**（INV-1） |
| 寫者持鎖窗口 | **長**：自第一次 INSERT 起持續到滿 20 筆／shutdown | 同左 | **短**：單輪（毫秒~數十毫秒） |
| 第二寫者（同 agent 檔文字端＋語音端並存） | 大機率撞上長事務 → 依 `busy_timeout=10s` 兜底 | 同左 | 仲裁次數變多但**每次窗口極短** → 撞鎖機率下降、超時機率下降 |

**重估結論**：C2 已把「讀者→寫鎖」這條最大宗的鎖競爭消滅；C1 剩下的只是**寫者↔寫者**，而 C1 把長事務切成短事務，對第二寫者是**淨改善**（I2，未實測，需在實作票以壓力測試固化）。審計 §5 所述「結構性風險（讀者每開必寫 schema_meta）」已由 C2 消除（`graph_store.py:110`）。

### 5.3 主要殘餘風險

1. **fail-safe 遮蔽（最大）**：flush 失敗時只有一行 warning，**沒有**可觀測指標 → 即時可見性會靜默降級回「舊快照」。緩解：INV-6 保證不中斷；觀測性是否加指標列 §9 D-7。
2. **每輪 commit 的真實成本未實測**（I1/I4）：生產長週期數據缺失（審計 §8.5 已記載無生產鎖競爭統計）。
3. **崩潰耐久性語意改變**（I3）：由「最多丟 19 筆」變為「最多丟當前輪」——屬改善，但屬**語意改變**，需在契約中明示。
4. **`_turn_count % 20` 的 evolution 寫入**：C1 的 flush 在其之前執行 → evolution 自己的寫入（`run_scheduled_decay` / `auto_resolve_conflicts` 走到 `update_weight`）**不受** C1 覆蓋，仍依 `_BATCH_SIZE` 門檻提交。此為**已知且刻意**的範圍邊界（§9 D-6）。

---

## §6 失敗模式與 rollback

### 6.1 失敗模式表

| # | 失敗 | 行為（C1 契約要求） | 系統最終狀態 |
|---|---|---|---|
| F1 | `flush()` 遇 `OperationalError: database is locked`（寫者↔寫者超過 `busy_timeout=10s`） | 吞掉 + warning；`post_reply_commit` 正常返回 | 本輪 fact 仍在未提交事務中；下次批次提交（滿 20）或 `shutdown()` 提交。跨進程可見性延遲回退為 C1 前語意 |
| F2 | `sqlite3.ProgrammingError`（連線已關／競態） | 同上 | 同上；由 E7 的 RLock 互斥，實務上極罕見 |
| F3 | 磁碟滿／I/O 錯誤 | 同上；**不得**重試風暴（不加重試迴圈） | pending 累積，每輪重試一次；不影響其他輪的提取與寫入 |
| F4 | 進程崩潰於 flush 前 | SQLite 自動回滾未提交事務 | **最多丟當前輪**（改善自「最多丟 19 筆」） |
| F5 | 解釋器關閉中（executor 已 shutdown） | `RuntimeError` 被吞 | 由 `provider.shutdown()`（`provider.py:210-213`）的 flush 兜底 |
| F6 | flush 期間 provider 被 `shutdown()` | RLock 互斥（E7）：兩者不交錯；`close()` 走 flush+close | 無遺失 |

### 6.2 Rollback 計畫

| 方案 | 內容 | 代價 | 建議 |
|---|---|---|---|
| **R1（主要）revert commit** | `git revert` C1 的實作 commit（或 `git reset --hard` 回到 base）。因 C1 **0 schema 變更、0 新狀態、0 新檔案、0 設定變更**，revert 即**逐字節回到 C2-only 行為** | 需一次部署重啟 | **建議採 R1** |
| R2 執行期開關（env / config flag） | 新增 `memory.sage_write.*` 或環境變數控制是否 flush | 需動 `config/`、引入新狀態與第二條分支（與「0 新機制」精神相衝），且切換後仍需重啟才生效 | 列 §9 D-3，**預設不做** |

**回退後系統回到什麼狀態（明確）**：

1. 寫入行為 = commit `2818af8` 的行為：`add_fact` 滿 20 筆才提交，`shutdown()` 兜底。
2. 跨進程可見性 = **C2-only 語意**：讀者不再被鎖（0 `database is locked`），但讀到的是**已提交舊快照**；文字端普通生活句對 VC 端不即時可見。
3. 資料**無遺失、無遷移、無需修復**：未提交事務由 SQLite 自然處理；已提交的列不受影響（C1 只增加 commit 時機，不改資料內容）。
4. 既有測試**不需改動**即可全綠（C1 不改變任何被斷言的行為契約，見 §7.3）。

---

## §7 驗收準則與測試計畫（規格；本票不寫測試程式）

### 7.1 A/B 硬斷言情境（未來實作票必過）

| # | 情境 | 硬斷言 | 對照（C1 前應 FAIL / 行為不同） |
|---|---|---|---|
| V1 | 非原生角色（如 `agent_rem`）＋**普通生活句**（無定義性 marker）＋ 隔離 `data_root`，`await post_reply_commit(...)`（**不** shutdown） | 返回後**立刻**用全新連線 SELECT 本輪 `fact_ids` → **全部命中**；且 `store._pending_writes == 0` | C1 前：0 列可見、pending ≥ 1 |
| V2 | 同上，但以 `default_memory_retriever` 同款流程（新 `GraphStore` + `MemoryReader.retrieve_context`）取回 | 檢索到本輪 fact；**0** `database is locked`；耗時 ≪ 10s（對照 C2 前 11.0s） | C1 前：可見 0（不鎖但仍讀不到） |
| V3 | **現代原生角色**（`agent_akane`）＋ 普通生活句 | 同 V1（可見）；**同時**硬斷言該輪 **0 打標**（`origin`/`horizon_state` 皆為預設；`set_fact_dimensions` 呼叫次數 0）→ 證明 flush 與 bypass 並存 | C1 前：不可見（E4 的 A 組重現） |
| V4 | **現代原生角色 ＋ 定義句**（如「氣炸鍋就是個…箱子」） | 可見（V1 斷言）＋ `get_idiolect_facts()` **仍為 0 筆**（bypass 不動） | C1 前：不可見（審計 §8.2 的第三次非對稱） |
| V5 | 非原生角色 ＋ 定義句 | 可見 ＋ 打標 1 筆（`assimilated`/`aware`）→ EH-3.1 回歸 | C1 前：已可見（E3），本項為回歸 |
| V6 | **fail-safe**：monkeypatch `store.flush` 為 `raise OperationalError` | `await post_reply_commit(...)` **正常返回**（0 raise）；warning 被記錄；fact 仍在 pending；隨後顯式 `flush()` 成功提交（**0 資料遺失**） | — （新增保證） |
| V7 | **no-diary**（`agent_ram`） | 該 provider `_pending_writes == 0`；`facts` 表 0 新增列（既有 `skip_graph` 語意不變） | C1 前相同（回歸） |
| V8 | **跨進程**：以上情境改為「文字端進程常駐不關閉 + 另一進程讀」（可沿用審計 §4 的隔離 harness 形態） | 讀側進程可見本輪 fact；期間 0 `database is locked` | C1 前：A 組現象（鎖死 11s→None，或 C2 後為 0 列） |
| V9 | **回退驗證**：revert C1 後複跑 V1／V3 | V1／V3 必須回落為 FAIL（證明保證可歸因於 C1，而非環境或 C2） | — （歸因證據） |

### 7.2 靜態／結構斷言（不需真跑 SQLite）

| # | 斷言 | 對應不變量 |
|---|---|---|
| S1 | `provider.py` 中 `flush` 呼叫點計數 = base + 1，且新增者在 `post_reply_commit` 的 AST 節點內 | INV-4 |
| S2 | `src/` 全域 0 新增 `threading.Timer` / `loop.call_later` / `asyncio.sleep` 輪詢 / 新增 `create_task` 站點（與 base `2818af8` 逐點比對） | INV-4（0 新定時器） |
| S3 | `post_reply_commit` 的 AST 簽名節點與 base 完全一致 | INV-5 |
| S4 | `graph_store.py` 的 `flush()` 函式 AST 與 base 完全一致；`_BATCH_SIZE == 20`、`_SCHEMA_VERSION == 9` 無 diff | INV-7 |
| S5 | `provider.py:426-438`（EH-3.1 Gate 1）與 `horizon.py:61-69`（白名單）與 base 逐字相同；`NO_DIARY_AGENTS` 不變 | INV-9 |
| S6 | C1 diff 中 0 個 `DELETE` / `prune` / `vacuum` 新增呼叫；`src/` 0 schema 變更、0 新欄位 | INV-10、INV-5 |

### 7.3 回歸範圍（已逐項覆核，附「為何仍綠」）

| 測試檔 | 為何 C1 不會破壞它（本次已讀碼覆核） |
|---|---|
| `tests/test_vc_eh_unification.py:327-368`（Test 4） | 斷言是「shutdown 後 VC 讀側必須檢索到」——C1 讓資料**更早**可見，「shutdown 後可見」仍為真（超集）。**注意**：其 docstring 自述「提交=自然生命週期 shutdown」將變成**過時描述**（提交點提前到 `post_reply_commit`），斷言不需改，但文件措辭應在實作票一併更新（屬 `tests/` 變更，需授權）。 |
| `tests/test_vc_eh_unification.py:506-531`（潔淨度自檢） | 該自檢**只掃描測試檔自身**（`Path(__file__).read_text()`），限制測試端不得補償性 flush；C1 改的是 `src/`，不觸發。 |
| `tests/test_vc_eh_unification.py:416-460`（Test 6，barge-in spy） | spy 掛在**語音端** store，斷言為 `assert flush_calls`（truthy）與「中斷回合 0 flush」。C1 不觸 `clients/`；完整回合的 flush 次數由 1 變 2 仍是 truthy → 仍綠。 |
| `tests/test_m5_5_2_mem_wiring_1_skip_graph_misbind.py:229-304`（AST 防回歸） | 檢查為 `len(run_in_executor_calls) >= 1`（非等值）；新增的 `run_in_executor(None, self._store.flush)` 內**無 `write_turn` reference** → 在迴圈中被 `continue` 跳過；`checked >= 1` 與 keyword 斷言由既有呼叫滿足 → 仍綠。**注意**：新增呼叫不得在 `run_in_executor` 後追加 >3 個位置參數（本契約不加任何位置參數）。 |
| `tests/test_epistemic_horizon_eh31.py`、`tests/test_epistemic_horizon_eh3.py` | 直呼 `post_reply_commit` 後驗打標結果；C1 只多一次 commit，不改打標 → 仍綠。 |
| `tests/test_graph_store_c2_read_open.py:51-53, :93-99` | 直接對 `GraphStore` 操作（`add_fact` → 斷言 `_pending_writes == 1 / == 19`），**不經過 provider** → C1 完全無關，仍綠（同時是 INV-7 的守門）。 |
| `tests/test_graph_store_concurrency.py`、`tests/test_graph_store_confidence.py` | 同上，直接操作 store，不經 provider。 |
| `tests/test_memory_middleware.py:161`、`tests/test_memory_persistence.py:95` | 走 middleware → provider 真實路徑；C1 只增加一次 commit，既有斷言（寫入成功、可讀）仍成立。 |

**建議回歸指令（實作票用）**：`tests/test_vc_eh_unification.py`、`tests/test_m5_5_2_mem_wiring_1_skip_graph_misbind.py`、`tests/test_graph_store_c2_read_open.py`、`tests/test_graph_store_concurrency.py`、`tests/test_epistemic_horizon_eh3.py`、`tests/test_epistemic_horizon_eh31.py`、`tests/test_memory_middleware.py`、`tests/test_memory_persistence.py`，外加全量 `pytest` 與基線比對（既有 5 筆 pre-existing 失敗需先取基線，勿混算）。

### 7.4 驗收完成定義（實作票的 DoD）

1. V1–V9 全過，其中 V1／V3／V4／V8 在 C1 前**必須**為 FAIL（A/B 對照留存原始輸出）。
2. S1–S6 靜態斷言全過。
3. §7.3 回歸面全綠（與基線同批失敗數一致）。
4. `0` 生產資料變更（隔離 `data_root`，逐檔 hash 0 diff）、`0` schema 變更、`0` 新設定項。
5. 部署觀察（若 Owner 授權）：重啟後確認 VC 端能即時讀到文字端當輪生活記憶，且 `database is locked` 出現次數為 0。

---

## §8 解凍申請範圍（**申請，非授權**）

> 本節僅為「若 Owner 批准 C1，需要解凍什麼」的**最小清單**。本票**未**解凍、**未**授權實作、**未**動任何 code。

### 8.1 需要解凍（最小集）

| 檔案 | 範圍（精確到函式／區塊） | 變更量級（估計） |
|---|---|---|
| `src/memory/sage/provider.py` | **僅** `SAGELiteProvider.post_reply_commit` 函式體內、`provider.py:382`（EH-3.1 hook 區塊結尾）與 `:383`（`self._cache.invalidate()`）之間，新增一個 flush 呼叫區塊（`try` / `self._store is not None` 護衛 / `await loop.run_in_executor(None, self._store.flush)` / `except Exception → logger.warning`）＋ 註解 | ≈ 6-10 行（含註解），**不改任何既有行** |

**這是唯一需要解凍的生產檔案與函式。**「SAGE 寫入邏輯」的 frozen 邊界在本案中**只開這一個入口**。

### 8.2 需要新增（非解凍，但屬實作票範圍）

| 檔案 | 內容 |
|---|---|
| `tests/test_mem_visibility_c1_flush.py`（NEW，名稱待實作票定） | V1–V9 的驗收測試（隔離 `data_root`、0 生產污染、確定性，不連網） |

（`tests/` 不在 frozen 契約列管項內，但屬本票「不做」範圍；且 §7.3 提到的 Test 4 docstring 措辭更新若要做，需 Owner 一併同意。）

### 8.3 **不解凍**（明確禁止事項）

| 對象 | 說明 |
|---|---|
| `src/memory/sage/graph_store.py` | `add_fact` / `flush` / `close` / `_init_db` / `_migrate` / `_BATCH_SIZE` / `_SCHEMA_VERSION` **全部不動**（硬約束 6、INV-7） |
| `_tag_explanatory_assimilations`（`provider.py:396-490`） | EH-3.1 本體與其 flush **逐字保留**（§4.1） |
| `provider.py:426-438`、`src/memory/sage/horizon.py:61-74` | `MODERN_NATIVE_AGENTS` bypass 與白名單 **不得修改**（硬約束 2、INV-9） |
| `NO_DIARY_AGENTS`（`provider.py:44`）與 no-diary 分支（`:334-351`） | 不動（§4.4） |
| `post_reply_commit` 的簽名（`:306-321`） | 不動（硬約束 5、INV-5） |
| `src/memory/sage/writer.py` | `write_turn` 及萃取鏈不動 |
| `src/memory/middleware.py` | `_commit_async` 等**不得**新增 flush（避免第二掛載點） |
| `src/memory/sage/provider.py` 的 `sync_turn` / `shutdown` / `_init_components` | 不動（`sync_turn` 的覆蓋面見 §9 D-9） |
| `src/goals/seed_provider.py:722` | 既有 flush 呼叫點不動 |
| `clients/voice_companion/*` | VC-UNIFY-1.2 **不動**（§4.5、§9 D-5） |
| `config/`、`personas/` | 完全不動（本契約 0 新設定項） |
| `logs/ENGINEERING_STATE.md` | 本票僅登記狀態；實作票另循流程 |

### 8.4 解凍理由（一句話）

C1 是 MEM-VISIBILITY-0 P0（跨進程記憶可見性）中**唯一**能在「0 新機制、0 schema、0 設定、0 介面變更」前提下補上「文字→語音即時可見」的寫側手段，且與已上線的 VC-UNIFY-1.2 語意對稱——解凍面最小（單檔單函式），可 revert 性最高。

---

## §9 待 Owner 決策清單

> 以下為**工程面無法自行拍板**者。每項給選項與建議，**不代 Owner 決定**。

### D-1「成功」的操作性定義（掛載點位置）

- **問題**：硬約束 1 的「`post_reply_commit` 成功後」是指 (a) **本輪寫入階段成功**，還是 (b) **整個方法執行完畢**？
- **選項 A（建議）**：寫入階段成功 → flush 掛在 hook 之後、evolution 之前（§3.1）。優點：可見性保證不耦合 evolution 成敗；單點覆蓋本輪全部 store 寫入。
- **選項 B**：整個方法完成 → flush 掛在 `self._turn_count += 1`（`provider.py:392`）之後。優點：字面上更貼近「成功後」；缺點：`_turn_count % 20` 的 evolution（目前**未**包 try/except）一旦拋例外，本輪 fact 就不會被提交。
- **為何需要 Owner**：此為 Owner 硬約束措辭的操作性解釋，屬契約語意定義權。

### D-2 是否在 provider 層加 `pending > 0` 前置判斷

- **選項 A（建議）**：不加，無條件呼叫，靠 `GraphStore.flush()` 既有守衛（E5）→ 0 新私有狀態耦合。
- **選項 B**：加 `if self._store._pending_writes > 0:` → 呼叫次數變少，但耦合私有欄位、且未來 GraphStore 內部語意改變時會靜默失效。
- **為何需要 Owner**：屬「是否容許 provider 讀取 store 私有狀態」的邊界判斷（可純工程決定，但涉及 frozen 邊界風格，建議一併確認）。

### D-3 是否需要 runtime kill switch

- **選項 A（建議）**：**不需要**，rollback 靠 `git revert`（R1）。
- **選項 B**：新增 env / config 開關（如 `MEM_VISIBILITY_C1_FLUSH=0/1`）→ 可不重啟部署即關閉，但引入新設定項與第二條分支，且切換需讀 config（通常仍需重啟才生效）。
- **為何需要 Owner**：新增設定項屬產品／運維面決策（且 Owner 關注的「0 新機制」精神）。

### D-4 EH-3.1 的 flush 是否在 C1 落地後整併

- **選項 A（建議）**：**保留原樣、不合併**（避免擴大解凍範圍，且它服務 Idiolect 讀側閉環的原始目的）。
- **選項 B**：C1 落地後移除 EH-3.1 的 flush（由 C1 統一覆蓋）→ 減少一次冗餘 commit，但改動 `_tag_explanatory_assimilations`（另一個 frozen 相鄰區塊）且需另證 Idiolect 讀側不受影響。
- **為何需要 Owner**：屬 frozen 相鄰區塊的取捨與解凍面擴大，須 Owner 同意。

### D-5 VC-UNIFY-1.2 的 flush 是否同步移到 worker thread

- **現況**：VC 側 flush 在 event loop 線程（`akane_voice_brain.py:641-648`），C1 在 worker thread——**存在執行緒親和性不對稱**。
- **選項 A（建議）**：不在 C1 內處理；若要統一，另開小案（動 `clients/`）。
- **選項 B**：C1 一併把 VC 側改為 executor 內 flush（一致性更高，但擴大到 `clients/`，且 VC 每次 flush 的 SQLite 寫入會移出 loop 線程——需重跑 VC 全套）。
- **為何需要 Owner**：跨模組範圍決定。

### D-6 evolution（`_turn_count % 20`）的寫入是否納入即時可見性承諾

- **選項 A（建議）**：**不納入**（C1 只承諾本輪對話 fact；evolution 的 weight/decay 寫入仍依批次門檻）。
- **選項 B**：納入 → 需把 flush 移到 evolution 之後（即 D-1 選項 B），換得 evolution 寫入也可見，代價是與 evolution 成敗耦合。
- **為何需要 Owner**：屬「即時可見性」的承諾邊界。

### D-7 觀測性（flush 失敗是否需指標）

- **選項 A（建議）**：僅既有 `logger.warning`（0 新指標、0 新機制）。
- **選項 B**：加結構化計數（成功／失敗次數）供運維觀測 → 需新狀態與新的對外介面。
- **為何需要 Owner**：屬運維可觀測性投入。

### D-8 C1 落地後的 C3（有界時間窗提交）定位

- **選項 A（建議）**：C3 降級為 OPTIONAL——C1 已提供「每輪即時可見」，C3 的時間窗只在「單輪內大量寫入」場景才有邊際價值。
- **選項 B**：C3 維持並行立案（保留給 CLI 批次／非對話寫入路徑）。
- **為何需要 Owner**：路線圖取捨。

### D-9 `sync_turn` 是否納入本契約

- **事實（已查證）**：`sync_turn`（`provider.py:268-304`）同樣寫 graph 但**不 flush**；`src/`、`clients/`、`scripts/` 內**無生產呼叫者**（現存呼叫者僅 harness：`harness/eh2_smoke_turn2.py` 等）。
- **選項 A（建議）**：**不納入**（硬約束 1 明確限定唯一掛載點為 `post_reply_commit`；且無生產呼叫者）。
- **選項 B**：後續另案為 `sync_turn` 補對等 flush（若未來有生產路徑使用它）。
- **為何需要 Owner**：屬硬約束 1 的範圍解釋；工程建議為 A，但需 Owner 確認此已知邊界可接受。

### D-10 契約狀態登錄用語

- **問題**：`logs/ENGINEERING_STATE.md` 中 C1 應登記為「契約已產出、**未解凍、未授權實作**、等待 Owner 決策」。
- **選項 A（建議）**：照上述用語登記（本票已照此辦理），並在 §1.1 與 §9 明示。
- **選項 B**：Owner 另有指定用語。
- **為何需要 Owner**：治理狀態措辭的最終確認。

---

## 附錄 A：證據索引（file:line）

| 代號 | 位置 | 內容 |
|---|---|---|
| A1 | `src/memory/sage/graph_store.py:20-21` | `_BATCH_SIZE = 20`、`_SCHEMA_VERSION = 9` |
| A2 | `src/memory/sage/graph_store.py:24-36` | `_locked` decorator（RLock 串行化） |
| A3 | `src/memory/sage/graph_store.py:52-57` | `self._lock = threading.RLock()`（KI-008） |
| A4 | `src/memory/sage/graph_store.py:71-84` | `_get_conn`：`timeout=10.0`、`journal_mode=WAL`、`synchronous=NORMAL` |
| A5 | `src/memory/sage/graph_store.py:86-115` | `_init_db`；C2 守衛於 `:110-115`（註解 `:104-109`） |
| A6 | `src/memory/sage/graph_store.py:372-400` | `add_fact`（`@_locked` `:372`；batch commit `:396-399`） |
| A7 | `src/memory/sage/graph_store.py:892-897` | `flush()`：`if self._conn and self._pending_writes > 0` |
| A8 | `src/memory/sage/graph_store.py:899-904` | `close()` = flush + close（`@_locked`） |
| A9 | `src/memory/sage/provider.py:44` | `NO_DIARY_AGENTS = {"agent_ram"}` |
| A10 | `src/memory/sage/provider.py:165` / `:171` | `self._store: Optional[GraphStore] = None` / `PrefetchCache(ttl_seconds=30.0)` |
| A11 | `src/memory/sage/provider.py:190-213` | `_init_components` / `shutdown()`（flush + close） |
| A12 | `src/memory/sage/provider.py:306-321` | `post_reply_commit` 簽名（frozen） |
| A13 | `src/memory/sage/provider.py:334-351` | no-diary 分支（`return`） |
| A14 | `src/memory/sage/provider.py:352-369` | write 階段（`run_in_executor` + `partial` keyword 綁定） |
| A15 | `src/memory/sage/provider.py:370-382` | EH-3.1 hook 呼叫（`if fact_ids:`）＋註解「不在主 asyncio thread 觸發 SQLite 寫入」 |
| A16 | `src/memory/sage/provider.py:383` | `self._cache.invalidate()` |
| A17 | `src/memory/sage/provider.py:385-392` | `_turn_count % 20` evolution ＋ `_turn_count += 1` |
| A18 | `src/memory/sage/provider.py:426-438` | EH-3.1 Gate 1：modern-native → `return`（bypass） |
| A19 | `src/memory/sage/provider.py:442-449` | EH-3.1 Gate B：無定義性 marker → `return` |
| A20 | `src/memory/sage/provider.py:471-481` | 打標迴圈 ＋ `self._store.flush()`（含註解） |
| A21 | `src/memory/sage/provider.py:486-490` | EH-3.1 fail-silent（`except Exception → warning`） |
| A22 | `src/memory/sage/horizon.py:61-74` | `MODERN_NATIVE_AGENTS`（7 角色）＋ `is_modern_native` |
| A23 | `src/memory/sage/writer.py:270-329` | `write_turn`（回傳 `user_ids + assistant_ids`） |
| A24 | `src/memory/middleware.py:118-122` | 寫入節流 `COMMIT_COOLDOWN_SECS = 5.0` |
| A25 | `src/memory/middleware.py:479-493, :512` | `_commit_async`（fire-and-forget 受管 Task） |
| A26 | `src/goals/seed_provider.py:722` | 既有 `flush()` 呼叫點（與 C1 無交互） |
| A27 | `clients/voice_companion/akane_voice_brain.py:630-652` | VC-UNIFY-1.2 的 post-success flush（對齊對象） |
| A28 | `clients/voice_companion/akane_voice_brain.py:337-361` | `default_memory_retriever`（每次新連線、fail-silent） |
| A29 | `tests/test_vc_eh_unification.py:327-368` / `:416-460` / `:506-531` | Test 4（shutdown 提交）／Test 6（barge-in spy）／潔淨度自檢 |
| A30 | `tests/test_m5_5_2_mem_wiring_1_skip_graph_misbind.py:229-304` | `post_reply_commit` AST 防回歸（`>= 1`） |
| A31 | `tests/test_graph_store_c2_read_open.py:51-53, :78, :93-99, :134` | C2 專測（`_pending_writes == 1 / 19`、reader 開連線不鎖） |
| A32 | `docs/MEM-VISIBILITY-0-AUDIT.md` | §3.2 / §4.2 / §5 / §8.2（審計原始證據） |
| A33 | `logs/ENGINEERING_STATE.md:334`、`:1632-1633` | C2 範圍邊界宣告（「不解即時最新值……C1 未授權」） |
| A34 | `logs/ENGINEERING_STATE.md:289` | C-3.1 的「授權登記 → 實作 → 驗收」流程範本（本案比照） |

## 附錄 B：明文不做（Out of Scope）

1. 不寫任何實作代碼、不改任何非 docs 檔案（本票 0 code）。
2. 不宣稱 C1 已實作、不宣稱已解凍。
3. 不設計任何新定時器／背景輪詢／週期掃描。
4. 不建議取消 `MODERN_NATIVE_AGENTS` 同化 bypass（硬約束 2）。
5. 不恢復與本契約無關的 `MEM-VISIBILITY-C2-V`。
6. 不改 `_BATCH_SIZE`、不改 `GraphStore.flush()` 行為、不改 `post_reply_commit` 簽名。
7. 不部署、不重啟服務、不觸碰生產 `data/`。

---

**狀態宣告**：本檔為 **DESIGN ONLY** 交付。C1 **未解凍、未授權實作、未落地**。所有實作、測試、部署行為須待 Owner 對 §9 決策清單與 §8 解凍範圍作出裁定後，另開實作工單執行。
