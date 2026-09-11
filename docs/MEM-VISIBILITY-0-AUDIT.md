# MEM-VISIBILITY-0 審計報告：跨進程記憶可見性（AUDIT ONLY）

- 日期：2026-09-10（Branch `main` @ `99fbd69`）
- 性質：純審計、純證據採集。**0 實作、0 Schema 變更、0 生產資料變更、0 commit**。
- 審計員角色：auditor（本報告為唯一交付檔案，不 commit）

---

## §0 核心結論（一句話）

**否——常駐不關閉的文字主服務寫入的普通生活記憶，VC 端**不**即時可見；而且比「最終一致」更嚴重的實測事實是：文字端持有未 commit 寫入期間，VC 端用 `default_memory_retriever` 同款流程開啟全新連線會**直接 `database is locked`（阻塞約 10 秒後 fail-silent 回傳 None）**——直到文字端觸發批次提交（20 筆）或 flush/shutdown 才恢復可讀。**

證據（A/B 實證 stdout，§4）+ 代碼證據（`src/memory/sage/graph_store.py:389-392` 批次提交門檻、`src/memory/sage/provider.py:481` EH-3.1 後置 flush 僅定義句、`tests/test_vc_eh_unification.py:353-355` Test 4 依賴 `shutdown()` 提交）。

---

## §1 完整調用鏈與執行緒上下文地圖

### 1.1 文字主服務事件鏈（生產路徑）

```
USER_MESSAGE (publish)
  └→ SoulEventBus._worker          src/eventbus/bus.py:249-305（單一 asyncio Task，bus.py:221）
       └→ MemoryMiddleware.handle_event       src/memory/middleware.py:226-232
            └→ _on_user_message               src/memory/middleware.py:234-260
                 └→ self._pending_user_text[session_id] = text（暫存，middleware.py:239）
AGENT_SPEAK (publish)
  └→ MemoryMiddleware._on_agent_speak        src/memory/middleware.py:400-512
       └→ fire-and-forget 受管 Task _commit_async   middleware.py:479-512（create_managed_task，middleware.py:512）
            └→ SAGELiteProvider.post_reply_commit   src/memory/sage/provider.py:306-392
                 ├→ loop.run_in_executor(None, partial(write_turn, ...))  provider.py:359-369  ← 執行緒切換點
                 │    └→ MemoryWriter.write_turn      src/memory/sage/writer.py:270-329
                 │         └→ extract_and_write → _write_single → store.add_fact   writer.py:381
                 │              └→ GraphStore.add_fact   src/memory/sage/graph_store.py:366-393
                 │                   └→ _pending_writes >= 20 才 conn.commit()      graph_store.py:389-392
                 └→ EH-3.1 hook（僅定義句）              provider.py:374-382
                      └→ _tag_explanatory_assimilations → self._store.flush()      provider.py:481
```

### 1.2 執行緒上下文判定

| 環節 | 執行場所 | 證據 |
|---|---|---|
| Event 派發（USER_MESSAGE / AGENT_SPEAK handler） | asyncio event loop 主線程（bus worker 為同 loop 內單一 Task；每個 handler 一個 dispatch Task） | `bus.py:221`（`soul_event_bus_worker`）、`bus.py:249-305`、`bus.py:294-302` |
| `_commit_async`（fire-and-forget） | 同 event loop 主線程的獨立 Task（「背景」是 asyncio 層級，非 thread） | `middleware.py:479-512`（create_managed_task 見 `src/async_utils.py:57-63`） |
| `write_turn`（SQLite INSERT） | **default ThreadPoolExecutor 的 worker thread**（`run_in_executor(None, ...)`） | `provider.py:359-369`（MEM-WIRING-1 partial keyword 綁定）；`provider.py:375-382`（EH-3.1 同樣在 executor 內） |
| SQLite 連線 | 單一 `sqlite3.Connection`（`check_same_thread=False`），由多執行緒共享，RLock 串行化（KI-008） | `graph_store.py:73-77`、`graph_store.py:24-36`、`graph_store.py:52-57` |
| 文字端讀側 prefetch | `asyncio.to_thread` worker thread，**同一條連線** | `middleware.py:300-304` → `provider.prefetch`（`provider.py:217-255`） |
| VC 端讀側 `default_memory_retriever` | 獨立進程、**每次全新 GraphStore 連線** | `clients/voice_companion/akane_voice_brain.py:337-361`（`:347` 新開 GraphStore、`:344` 同檔路徑） |

### 1.3 連線生命週期與同連線即時性

- 每個 `SAGELiteProvider` 持有一個 `GraphStore`（`provider.py:190-208`，`middleware.py:158-170` per-agent lazy init）。
- 同進程同連線的讀取能「立即」看到新 fact 的原因：`MemoryReader._gather_candidates` → `GraphStore.search_by_entity`（`reader.py:255`、`graph_store.py:604-637`）走**同一條連線**的 SQL；SQLite 語意是「連線看得到自己未 commit 的寫入」。另有 `_cache.invalidate()`（`provider.py:383`）只清同進程 prefetch 快取，與資料庫 commit 無關。
- 跨進程（VC 新連線）的讀取走 SQLite 的**已提交快照**：未 commit 的列不可見（§4 實證）。

---

## §2 事務提交（Commit / Flush）觸發點盤點

| # | 觸發點 | 位置 | 條件 |
|---|---|---|---|
| 1 | `add_fact` 批次提交 | `graph_store.py:389-392`（`_BATCH_SIZE=20`，`:20`） | `_pending_writes >= 20` 才 `conn.commit()` |
| 2 | `set_fact_dimensions` 批次提交 | `graph_store.py:442-445` | 同上（EH-2 打標路徑） |
| 3 | `update_weight` 批次提交 | `graph_store.py:473-476` | 同上（合併/強化路徑） |
| 4 | **EH-3.1 後置 flush（僅定義句）** | `provider.py:481`（`_tag_explanatory_assimilations` 結尾） | user 文本含定義 marker（`provider.py:52-55`）+ 五閘門（A–E，`provider.py:411-477`）；**現代原生角色直接 bypass**（`provider.py:434-438`，白名單 `horizon.py:61-69`） |
| 5 | `GraphStore.flush()` 顯式呼叫 | `graph_store.py:885-890` | VC-UNIFY-1.2 語音端背景 task 用（`akane_voice_brain.py:638-643`） |
| 6 | `GraphStore.close()` = flush + close | `graph_store.py:892-897` | 由 `provider.shutdown()` 觸發（`provider.py:210-213`）；**文字端 Test 4 依賴此點**（`tests/test_vc_eh_unification.py:353-355`「提交=自然生命週期：provider.shutdown()」） |
| 7 | 「記憶快取失效」 | `provider.py:301/350/383`（`self._cache.invalidate()`） | 僅同進程 PrefetchCache（TTL 30s，`provider.py:171`），**與 SQLite commit 完全無關** |

**關鍵事實**：普通生活句走完 `post_reply_commit` 後**沒有任何 flush / commit**（`provider.py:359-392` 全程無 flush 呼叫；`write_turn` 內部亦無，`writer.py:270-329` 只到 `add_fact`）。所以文字端常駐期間，普通句的 pending writes 會**一直掛在未提交事務上**，直到累積滿 20 筆（或進程 shutdown）。

另註：`_tag_explanatory_assimilations` 的註解自述了同一機制（`provider.py:479-481`：「set_fact_dimensions 只在 batch_size 達標時才自動 commit，讀側 Idiolect 檢索開新 sqlite 連接，未 commit 的 UPDATE 讀不到 → 閉環斷裂」）——只是該修法只覆蓋定義句，普通句沒有對等處理。

---

## §3 跨進程鎖與 WAL 併發實況

### 3.1 進程分離

- 文字主服務：`scripts/run_server.py`（uvicorn + FastAPI + EventBus，單進程）。
- VC 語音：`clients/voice_companion/web_server.py:919,982`、`akane_live.py:251,270` 為**獨立 `__main__` 入口**，與主服務分開部署（排程任務常駐；ENGINEERING_STATE §VC 系列記載 8765/8766/8767 三埠）。
- 兩邊共用同一檔案：`data_root()/memory/<agent_id>/graph.sqlite`（文字端 `middleware.py:161` / VC 端 `akane_voice_brain.py:344,563-568`）。

### 3.2 SQLite 底層配置

| 項目 | 值 | 證據 |
|---|---|---|
| journal_mode | **WAL（每次開連線強制）** | `graph_store.py:80`（`PRAGMA journal_mode=WAL`） |
| synchronous | NORMAL | `graph_store.py:81` |
| busy_timeout | **10 秒**（`timeout=10.0`） | `graph_store.py:76`（實測阻塞 ~11.0s，§4） |
| cache / temp | cache_size=-8000、temp_store=MEMORY | `graph_store.py:82-83` |
| 執行緒安全 | `check_same_thread=False` + RLock（KI-008 修復） | `graph_store.py:75`、`:24-36`、`:52-57` |
| **結構性隱患** | **`GraphStore.__init__` 每次開連線都執行 `INSERT OR REPLACE INTO schema_meta` 並 commit**（`_init_db`，`graph_store.py:86-108`）→ **任何「讀者」開連線都要搶 WAL 寫鎖** | `graph_store.py:104-107` |

### 3.3 鎖競爭實況（隔離實證，§4）

- 文字端持有未 commit 事務時（SQLite 隱式事務在第一次 INSERT 後保持開啟直到 commit），**WAL 寫鎖被持續占用**。
- 此時任何第二連線（含號稱「唯讀」的 VC retriever）執行 `_init_db` 的 schema_meta 寫入 → 等待 10s（busy_timeout）→ `OperationalError: database is locked`。
- VC 端 `default_memory_retriever` 是 fail-silent（`akane_voice_brain.py:360-361`）→ 回傳 `None`，使用者無感但記憶完全缺席。
- 純讀者（raw SQL 唯讀連線）在 WAL 下**可以**同時讀已提交快照（實證 0 列），只是讀不到未 commit 資料。

---

## §4 隔離環境 A/B 實證（已實際執行）

### 4.1 執行環境

- 實驗程式：`%TEMP%\mem_vis_ab.py`（**工作區外**，不污染工作樹；0 commit）。
- 隔離：`tempfile.TemporaryDirectory()` → `SOUL_OS_DATA_DIR`（0 生產資料觸碰）；`src.paths.reset_data_root()` 重置快取。
- Agent：`agent_rem`（**非** Modern-Native 白名單，`horizon.py:61-69`；非 no-diary 白名單 `provider.py:44`）——實驗 B 首次使用 `agent_akane` 失敗的因果即為白名單 bypass（附加發現，§8.2）。
- LLM judge：未設 proxy → 依設計 fallback heuristic（`writer.py:491-494`，"LLMJudge not available"，0 網路）。
- 流程：文字端 `post_reply_commit`（與 middleware 同路徑、**不 shutdown**）→ ①同連線讀 ②VC 端全新 GraphStore 連線（= `default_memory_retriever` 同款流程，含 `_init_db` 寫入）③raw 唯讀 SQL 快照 ④控制組（文字端 flush 後重開）。

### 4.2 實驗原始輸出（實際 stdout，截自兩組完整執行）

```
[實驗組 A(普通生活句)] agent=agent_rem
句子: '下午我去公園散步，看到花開了。'
[1.文字端] post_reply_commit 完成 | writer.store._pending_writes=3 | conn 未關閉
[2.同連線] 檢索 '散步 公園' → facts=3 | [('下午我去公園散步，看', '到', '花開了。'), ('下午我', '去', '公園散步，看到花開了。'), ('散步真好，看', '到', '花很開心。')]
[3.VC端] 全新 GraphStore 連線 → OperationalError: database is locked（耗時 11.0s） | fail-silent 回傳 None（default_memory_retriever 同款）
[4.唯讀快照] facts 表可見列數 = 0 | []
[5.控制組] flush 後 VC 端檢索 '散步 公園' → facts=3 | [('下午我去公園散步，看', '到', '花開了。'), ...]
[打標] get_idiolect_facts() → 0 筆 | []

[實驗組 B(定義句)] agent=agent_rem
句子: '氣炸鍋就是個插電烤熟食物的箱子。'
[1.文字端] post_reply_commit 完成 | writer.store._pending_writes=0 | conn 未關閉
[2.同連線] 檢索 '氣炸鍋' → facts=3 | [('氣炸鍋就', '是', '個插電烤熟食物的箱子。'), ...]
[3.VC端] 全新連線檢索 '氣炸鍋' → facts=3 | [('氣炸鍋就', '是', '個插電烤熟食物的箱子。'), ...] | summary='## Recalled Memory\n- [medium] 氣炸鍋就 是 個插電烤熟食物的箱子。 (score=0.69)\n...'（耗時 0.0s）
[4.唯讀快照] facts 表可見列數 = 4 | [('氣炸鍋就', '是', '個插電烤熟食物的箱子。'), ...]
[5.控制組] flush 後 VC 端檢索 '氣炸鍋' → facts=3 | ...
[打標] get_idiolect_facts() → 1 筆 | [('氣炸鍋就', 'assimilated', 'aware')]
```

（stderr 每組皆有 `[MemoryWriter] LLM judge 失敗,fallback heuristic: LLMJudge not available`——預期內，writer.py:491 的設計 fallback。）

### 4.3 A/B 數據表（常駐不關閉狀態）

| 維度 | 實驗組 A：普通生活句 | 實驗組 B：定義句（EH-3.1） | 判讀 |
|---|---|---|---|
| 寫入後 pending writes | **3（未提交）** | **0（已被 EH-3.1 flush）** | provider.py:481 flush 前後對照 |
| 同連線（文字端自身）可見性 | 即時（3 筆） | 即時（3 筆） | 萃取成功；同連線語意 |
| **VC 端全新連線（未 flush）** | **`database is locked`，11.0s，fail-silent None** | **立即可見，0.0s，3 筆 + summary** | **Immediate vs Blocked** |
| raw 唯讀快照（已提交快照） | 0 列 | 4 列 | 未 commit = 快照不可見 |
| flush 後 VC 端 | 3 筆可見 | 3 筆可見 | 受阻點 = 未 commit（控制組） |
| Idiolect 打標 | 0 | 1（assimilated + aware） | EH-3.1 五閘門生效 |

### 4.4 判讀

- **A 組**：結合「同連線 3 筆 / VC 新連線被鎖 / 唯讀快照 0 列 / flush 後 3 筆」四段證據 → 普通生活句在常駐狀態下**既不可見、而且把 VC 讀側連線直接堵死**（至少堵到下次 commit）。「受阻於未 commit」成立（控制組鐵證）。
- **B 組**：`pending=0` + VC 新連線 0.0s 讀到 + Idiolect 打標成功 → 定義句靠 EH-3.1 後置 flush 達成**即時跨連線可見**。VC-UNIFY-1.2.1 Test 4「shutdown 後才讀回」的成因即為 A 組機制（該測試用 shutdown 模擬進程下線的自然提交——`tests/test_vc_eh_unification.py:331-355` 文件自述；但**常駐主服務沒有對等機制**）。

---

## §5 跨進程併發風險評估（若文字主服務也啟用每輪 flush）

假設文字端比照 VC-1.2 在每輪 `post_reply_commit` 後 flush（純評估，未實作）：

| 面向 | 評估 | 依據 |
|---|---|---|
| 吞吐量影響 | **低**。WAL + `synchronous=NORMAL` 下，commit 不需每次 fsync（fsync 落在 checkpoint 層），單次 commit 成本為 WAL 寫鎖 + 少量頁面寫；生產 turn 頻率為分鐘級（10 角色、5s 節流 `middleware.py:122`），20 筆批次省下的成本約為每 turn 一次 commit 指令，量級可忽略 | `graph_store.py:80-81`；`middleware.py:121-122` |
| 鎖競爭頻率 | **上升但窗口縮短**：每輪 commit 使「持鎖窗口」從「累積 20 筆期間的持續占用」縮短為「單次寫事務毫秒級」，跨進程阻塞（§4 A 組）消失；但若文字端與 VC 同時寫同一 agent 檔（如 agent_akane 文字端 + 語音端都啟用 sage_write），兩寫者仲裁次數增加，busy_timeout=10s 仍可兜底 | `graph_store.py:76` |
| 結構性風險（未隨之消失） | **`GraphStore.__init__` 每開必寫 schema_meta**（`graph_store.py:104-107`）——即使每輪 flush，讀者開連線仍是一次寫事務；週期窗內寫者持鎖的機率大幅下降，但「讀者搶寫鎖」的設計缺陷仍存在 | §3.2 |
| 回歸面 | `post_reply_commit` / `GraphStore.flush` 已有多處既有呼叫（EH-3.1 `provider.py:481`、VC `akane_voice_brain.py:641-643`、`shutdown` `provider.py:212`），行為語義一致，回歸測試面可控；但 **SAGE 寫入主幹屬 Frozen Contract（AGENTS.md「SAGE 寫入邏輯」），任何著陸都需主大腦 + Owner 解凍授權** | AGENTS.md；ENGINEERING_STATE §8.3 先例（v9 migration 亦需授權） |

**結論**：每輪 flush 的吞吐代價可忽略、可見性立即獲益；真正需要一起處理的是「讀者開連線也要寫 schema_meta」的結構問題，以及 Frozen Contract 授權流程。

---

## §6 候選修復方案比較（只列優缺點，**不選型、不寫實作代碼**）

| 方案 | 做法（方向性） | 優點 | 缺點 / 風險 | 侵入性 |
|---|---|---|---|---|
| **C1：文字端 per-turn flush**（對齊 VC-1.2） | 文字端 `post_reply_commit` 成功後（或 middleware `_commit_async` 內）顯式 `store.flush()`，與 `akane_voice_brain.py:638-643` 同構 | ①與 VC-1.2 完全對稱，即時可見性最直接；②改動極小（1-2 行級）；③行為語義與既有 EH-3.1 / VC-1.2 flush 一致，回歸可控 | ①每 turn 多一次 commit（成本低但需實測確認）；②文字端 + VC 同檔雙寫時鎖仲裁次數增加；③**不動現代原生白名單**：文字端 `agent_akane` 等寫的定義句仍不 flush（hook bypass，`provider.py:434-438`）——需把 flush 放在 write 層而非 hook 層才全面；④SAGE 寫入路徑屬 Frozen，需授權 | 低（provider.py 或 middleware.py 單點） |
| **C2：讀側「免寫」開啟** | 讀者（`default_memory_retriever` 或 GraphStore 新 read-only 模式）開連線時不執行 `schema_meta` 寫入（版本判斷改純讀，或 retriever 改普通連線 + SELECT-only） | ①根治「讀者搶寫鎖」：`database is locked` 失敗模式消失（§4 A 組的 11s 阻塞直接消滅）；②不碰寫入主幹語義，對既有寫者 0 影響；③VC 記憶缺席（fail-silent None）問題在「鎖」層面解決 | ①**不解決最終一致**：資料仍要等 commit 才跨連線可見（只是不再「失敗」，改為「讀舊」）；②WAL 下 `mode=ro` URI 無法讀 WAL（代碼已記載 `horizon.py:96-98` SQLITE_READONLY_CANTLOCK），需特殊處理（一般連線 + 只讀語句、或接受快照語義）；③改 GraphStore 開連線行為，屬 SAGE 週邊，仍需審查 | 中（graph_store.py `_init_db` 或 retriever 單點） |
| **C3：有界時間窗提交** | 把「批次門檻」從「計數 20」擴充為「計數 20 或時間窗（如 N 秒/每輪結束）到達即提交」；落在 middleware fire-and-forget task 或 provider 層的週期檢查 | ①可見性延遲有明確上界，保留批次合併的吞吐紅利；②普通句與定義句統一由提交策略覆蓋（不受 EH-3.1 hook 依賴）；③若直接在 `_commit_async` 結尾 flush 則實現最簡（≈C1 的時間窗變體） | ①引入新狀態/計時機制（新增組件或參數），測試面最大；②時間窗參數需決策（幾秒？）；③同樣觸及 SAGE 寫入路徑 Frozen；④若要嚴格 immediate 仍需 C1 式 per-turn | 中~高（provider/graph_store 雙點） |

補充（三案共通）：任何著陸點都需遵守 ENGINEERING_STATE 的 Frozen Contract 流程（主大腦出工單 + Owner 授權 + 隔離驗證 + 回歸），且不得以「測試端補償 flush」替代生產機制（VC-UNIFY-1.2.1 潔淨度自檢禁止補償式 flush，`tests/test_vc_eh_unification.py`）。

---

## §7 產出檔案、git 狀態與 Frozen Contract 聲明

- 產出：
  1. `docs/MEM-VISIBILITY-0-AUDIT.md`（本報告，**不 commit**）
  2. 實驗程式 `%TEMP%\mem_vis_ab.py`（工作區外，0 污染）
- `git status --short`（審計結束後）：**僅新增 `?? docs/MEM-VISIBILITY-0-AUDIT.md` 為本審計產物**；另有審計前既存的 untracked 檔案（docs/*.md、harness/、logs/、scripts/、tests/、notion_*.json 等，審計開始前的 git status 已存在，非本審計產生）。**0 modified tracked 檔案**。
- HEAD：`99fbd69`（審計前後一致）。
- **Frozen Contract 踩線檢查：0 踩線**。未修改 `src/`、`clients/`、`tests/`、`config/`、`personas/`、`logs/ENGINEERING_STATE.md`；未 commit / push / add；未碰 production 資料（實驗全隔離）。

---

## §8 未驗證疑點 / 限制

1. **生產節奏下的累積行為（未實測）**：本實驗單輪寫入後立即探測。若文字端在 20 筆內持續寫入、期間 VC 也持續輪詢，`database is locked` 是否存在「間歇讀到舊快照」的窗口未做長期壓力測試（代碼推斷：鎖會持續到 commit，VC 會持續被鎖；未驗證推測）。
2. **agent_akane 現代原生附加發現（代碼證據，未做 VC 真機）**：文字端若由 `MODERN_NATIVE_AGENTS`（`horizon.py:61-69`，含 agent_akane）寫定義句，EH-3.1 hook 直接 return（`provider.py:434-438`）→ **定義句也不 flush** → 與 VC 端 flush 的語音寫入形成第三次非對稱（文字端現代原生角色 → VC）。本報告 B 組首次使用 agent_akane 失敗即此機制（換 agent_rem 後成功）。未在真機 VC 上複驗（本審計 0 部署）。
3. **`database is locked` 的精確超時組成**：實測 11.0s ≈ busy_timeout 10s + 開銷，未逐層拆分（`timeout=10.0` 為 `sqlite3.connect` 參數，`graph_store.py:76`）。
4. **唯讀快照連線的 WAL 相容性**：raw `mode=ro` 探測在「WAL 檔存在且寫者持鎖」狀態下成功返回 0 列（無錯誤），但 `horizon.py:96-98` 記載唯讀 URI 連線在 WAL 下可能 `SQLITE_READONLY_CANTLOCK` 失敗——兩者情境（是否需讀 WAL 內容/checkpoint 狀態）差異未窮舉（未驗證推測：快照落在 main db 已 checkpoint 部分則可讀）。此點直接影響 C2 方案可行性，建議開實作工單前先做專項驗證。
5. **生產 VC 端 `_get_sage_provider` 與文字端共用檔案的實際衝突頻率**：未對 production `data/` 做任何讀寫（0 觸碰），生產長週期鎖競爭統計無資料。
6. **`git status` 基線含大量既有 untracked 檔案**：接受準則「只顯示新增報告」在現行工作樹下無法嚴格成立（基線即非乾淨），本審計如實記錄基線快照，主張「本審計僅新增 1 個檔案」。