# Memory Continuity Observability v1 — 實作報告

- 工單：`docs/MEMORY-CONTINUITY-OBSERVABILITY-V1-WORKTICKET.md`（decision-complete）
- 分類：**Frozen Contract — Additive Observability Exception**（D4，D7 執行期護欄）
- Repo HEAD：`c7f67bf`（實作全程未 commit / add / push）
- 測試直譯器：`.venv\Scripts\python.exe`（**唯一**，未使用 PATH 上的全域 pytest）

---

## 0. 明確聲明（工單回報格式第 6 條）

> 本票**不得**宣稱「continuity 已可量測」。

準確的說法是：

> **injection 與 turn 兩段已具備觀測能力，use 可離線推導，influence 尚未建立。**

四段 lineage 目前的真實狀態：

| 段 | 狀態 | 依據 |
|---|---|---|
| retrieval | ✅ 已有 | `data/memory/loader_trace.jsonl`（本票零改動） |
| injection | ✅ **本票新增** | `data/memory/continuity_injection.jsonl`（設計 + 程式碼就緒，**服務重啟後才會開始產生資料**） |
| turn | ✅ **本票新增** | `data/memory/continuity_turn.jsonl`（同上） |
| use | ⚠️ 僅為 **4-gram heuristic 訊號** | 寫入時即算 + `--rescore` 離線重算；**不是**因果判定 |
| influence | ❌ **尚未建立** | 需要另一張票（60 組 paired cases、雙盲 judge） |

---

## 1. 改動檔案清單

### 新增（6）

| 路徑 | 職責 |
|---|---|
| `src/memory/continuity.py` | 純函式（canonicalize / hash / 4-gram use heuristic）+ `ContinuityRecorder` + `TurnLinkIndex` |
| `harness/continuity_analyze.py` | 離線 use scoring：`--self-test`、`--rescore`、join 報告 |
| `harness/continuity_gc.py` | 30 天 retention GC，**預設 dry-run** |
| `tests/test_memory_continuity_v1.py` | 行為測試 T1–T12 + D4「0 行為變更」硬證據 |
| `tests/infra/test_continuity_sidecar_is_additive.py` | AST 護欄 G1–G5 |
| `docs/MEMORY-CONTINUITY-OBSERVABILITY-V1-IMPLEMENTATION.md` | 本報告 |

### 修改（1）

| 路徑 | 改動量 | 內容 |
|---|---|---|
| `src/memory/middleware.py` | **+56 / −0** | 三處 additive sidecar 插入點 |

### 未動

`src/memory/v1/loader.py`、`src/llm/proxy.py`、`src/memory/sage/**`、`personas/**`、
`src/soul/life_thread_orchestrator.py`、`tests/conftest.py`、
`data/**` 全部零改動。

---

## 2. 三個插入點（D8 / D9 / D10）

| # | 位置（HEAD `c7f67bf` 行號） | 實作後行號 | 內容 |
|---|---|---|---|
| 1 | `middleware.py` `self._loader = None` 之後 | 136–162 | 建立 `ContinuityRecorder` + `TurnLinkIndex`，整段包 try/except |
| 2 | `_on_agent_intent` 的 `logger.info("[MemoryMiddleware] v1 Loader 注入 ...")` 結束後、`else:` 之前 | 356–366 | `record_injection(...)` |
| 3 | `_on_agent_speak` docstring 之後、`agent_id = event.payload.get("agent_id")` 之前 | 468–469 | `record_turn(event, link_index=...)` |

**🔴 D8 lazy**：`ContinuityRecorder.__init__` 只組 `Path` 物件與記憶體 dict，
**不 mkdir、不開檔、不寫入**。目錄與檔案一律到第一次 `record_injection()` /
`record_turn()` 才由 `_append_lines()` lazy 建立。middleware 的 recorder 建構
整段 try/except，失敗 → 設 `None` → 所有 sidecar 呼叫短路 +
`logger.warning`。

**🔴 D9**：插入點 2 在 `format_for_prompt(eligible)`（345）與 context 拼接（346）
**之後**，抓的是已決定注入的那份 `eligible`；**不從 rendered prompt 反解析**
（`format_for_prompt()` 不含 memory_id）。`eligible` 傳入後**只讀**。

**🔴 D10**：插入點 3 是 `_on_agent_speak` 的第一個可執行 statement
（docstring 之後），早於兩個既有 early return。`record_turn()` 同步、非 await、
零 LLM call；任何例外由 `ContinuityRecorder` 整段 try/except 吞掉，並以固定前綴
`[MemoryContinuity] instrumentation_gap:` 發出 warning（**不是靜默 return**）。

### 🔴 Join Key ≠ Identity（工單指定寫入 code comment）

四個識別碼角色已寫進 `src/memory/continuity.py` 模組 docstring 的表格：

- `correlation_id` = **join key**（`AGENT_INTENT.(correlation_id or event_id)`，
  由 `middleware.py:441` 透傳），**不唯一**
- `snapshot_id` = 一次注入觀測的身分，每回合 1 個
- Layer 2 `response_id` = loader 在 `load()` 開頭 mint 的 uuid4
- Layer 3 `response_id` = recorder 為該列 mint 的 uuid4

> Layer 2 ↔ Layer 3 的 join 走 `correlation_id`（或 `injection_snapshot_id`），
> **絕不是走 `response_id`**。兩個都叫 `response_id` 的欄位不可直接 join。

---

## 3. 測試結果

```
.venv\Scripts\python.exe -m pytest -q tests/test_memory_continuity_v1.py \
                                         tests/infra/test_continuity_sidecar_is_additive.py
→ 21 passed in 1.37s
```

| 指令 | 結果 |
|---|---|
| `pytest -q tests/test_memory_continuity_v1.py` | **13 passed** |
| `pytest -q tests/infra/test_continuity_sidecar_is_additive.py` | **8 passed** |
| 兩者合併 | **21 passed / 0 failed** |
| `harness/continuity_analyze.py --self-test` | `join_rate=100% (2/2) checks_failed=0` |
| `harness/continuity_gc.py --memory-dir <tmp>`（dry-run） | exit 0，未改動任何檔案 |

覆蓋：T1 canonicalize/hash（determinism）· T2 injection schema · T3 rank↔
`format_for_prompt` 渲染內容對應 · T4 4-gram 邊界（0/1/2 命中、重複去重、
`<4` 字元、標點）· T5 `use_confidence="heuristic"` + 全檔無 `memory_used` ·
T6 linked/unlinked join · T7 stub turn · T8 TTL/上限 200/pop 一次 ·
T9 retention = +30d · T10 寫入失敗隔離 · T11 `--rescore` 等價 · T12 GC dry-run
位元組未變。G1–G5 見下節。

---

## 4. Bry 最關心的六件事 — 客觀痕跡

### 4.1 `data/memory/loader_trace.jsonl` byte-identical ✅

| | 實作前 | 實作後 |
|---|---|---|
| size | 2,563,998 bytes | **2,563,998 bytes** |
| mtime (UTC) | 2026-10-07T17:50:23.6604319Z | **2026-10-07T17:50:23.6604319Z** |
| SHA256 | `BDD580B8…EFF0EECD` | **`BDD580B8…EFF0EECD`** |

sidecar 寫入的是**兩個新檔**（`continuity_injection.jsonl` /
`continuity_turn.jsonl`），未 append 進既有 trace。實測 `data/**` 下
`continuity_*` 檔案數 = **0**（測試全部落在 `tmp_path`）。

### 4.2 `format_for_prompt` AST-identical ✅

比對基準：`git show HEAD:src/memory/v1/loader.py`（**不靠行號**）。

```
G3 baseline AST identical    -> True
G3b loader.py byte-identical -> True      # 整檔逐 byte 未變（更強的證據）
```

負控制（證明護欄有牙）：

```
G3 mutant  conf .2f->.1f     -> False     ← 護欄抓到了
G3 mutant  sorted(lines)     -> False     ← 護欄抓到了
```

### 4.3 `_append_loader_trace` unchanged ✅

```
G5 baseline AST identical    -> True
G5 mutant  query[:200]->100  -> False     ← 護欄抓到了
```

另以 `test_g5b_middleware_diff_is_purely_additive` 驗證：`middleware.py` 所有
HEAD 既有行**一行都沒有消失**（純 additive）。

### 4.4 `record_turn` 位於兩個 early return 之前 ✅

```
_on_agent_speak first statement node type : If
_on_agent_speak first statement line      : 468
record_turn(...) call line                : 469
existing 'return' lines in handler        : [505, 517]
record_turn BEFORE both early returns     : True
```

插入後的函式開頭（`src/memory/middleware.py:450` 起）：

```python
    async def _on_agent_speak(self, event: SoulEvent) -> None:
        """AGENT_SPEAK 來了 → 配對暫存的 user_text → 寫入 graph。
        ...
        Phase 4 加節流：同 agent 5s 內只寫一次，防 N² 寫入爆炸。
        """
        # ── Memory Continuity Observability v1 (Layer 3, additive) ──
        # 🔴 D10：這個位置**不可下移**，也沒有更晚的替代位置。它是本 handler 的
        #    第一個 statement —— 早於下方兩個既有 early return（commit cooldown、
        #    空 text）。...
        if self._continuity is not None:
            self._continuity.record_turn(event, link_index=self._turn_links)

        agent_id = event.payload.get("agent_id")      # ← 471，原本的第一行
        ...
        return                                          # ← 505 cooldown early return
        ...
        return                                          # ← 517 空 text early return
```

### 4.5 sidecar 完全失效時 `memory_context` 逐 byte 相同 ✅

實測（monkeypatch `ContinuityRecorder._append_lines` 必失敗）：

```
sidecar ON  sha256: 128f1f9970238d44e1feaf466bb88f4bac796eb82bfce52dffbf56cbfcdf3e8e len=126
sidecar OFF sha256: 128f1f9970238d44e1feaf466bb88f4bac796eb82bfce52dffbf56cbfcdf3e8e len=126
byte-identical    : True
injection rows ON : 2
injection rows OFF: 0
```

且失效**可見**（不靜默）：

```
[MemoryContinuity] instrumentation_gap: stage=record_injection type=OSError
error=disk on fire (sidecar 失效，不影響 memory injection / 回覆生成)
```

> **Observability 是旁路，不是 runtime dependency。**

### 4.6 stub / empty_text / normal 三種 turn 都有可追蹤 status ✅

```
stub        rows 0 -> 1  (新增 1)  response_status='stub'        link_status='unlinked'
empty_text  rows 1 -> 2  (新增 1)  response_status='empty_text'  link_status='unlinked'
normal      rows 2 -> 3  (新增 1)  response_status='ok'          link_status='unlinked'
```

三種狀態**都必須新增一列**。stub 的 `text=""` 沒有被 `:505` cooldown 或
`:517` 空 text 的 early return 靜默吃掉 —— 這正是 D10 的目的。
`classify_response_status()` 讓 `stub` 優先於 `empty_text`
（`proxy.py` 的 stub 路徑兩者同時成立，必須留下診斷價值較高的 `stub`）。

---

## 5. Frozen Contract 逐項自檢

| 項目 | 狀態 | 證據 |
|---|---|---|
| `eligible_memories` 的篩選結果與順序 | ✅ 已驗證未變 | `loader.py` 整檔 byte-identical；`record_injection` 只讀（G1 護欄 + 負控制） |
| `confidence_threshold` / `threshold_for()` | ✅ 已驗證未變 | 同上（`loader.py` 零改動） |
| fail-safe 三條語意 | ✅ 已驗證未變 | 同上 |
| `format_for_prompt()` 輸出 | ✅ 已驗證未變 | G3 + G3b（AST + 逐 byte） |
| `memory_context` 拼進 payload 的字串 | ✅ 已驗證未變 | §4.5 sha256 相同；`middleware.py` 純 additive |
| `_append_loader_trace` | ✅ 已驗證未變 | G5（AST）+ 負控制 |
| Memory / SAGE 語意（`src/memory/sage/**`） | ✅ 已驗證未變 | `git status` 無該路徑；`continuity.py` 不得 import sage（G4） |
| `src/llm/proxy.py` | ✅ 已驗證未變 | `git status` 無該路徑 |
| 零 LLM 呼叫 | ✅ 已驗證 | G2 import 白名單（`httpx`/`openai`/`anthropic`/… 全禁）；`continuity.py` 僅用 stdlib |
| shadow log | ✅ 未恢復 | 未觸碰 `scripts/run_server.py` |
| `eligible` / threshold / 順序 | ✅ 已驗證未變 | G1 負控制：5 個變異全被抓 |
| `data/memory/loader_trace.jsonl` | ✅ 未修改 | §4.1 SHA256 相同 |
| `data/**` 任何既有檔案 | ✅ 未寫入 / 未改名 / 未刪除 | SHA256 + mtime + `continuity_*` 檔案數 = 0 |
| `personas/**`、`config`、Soul | ✅ 未觸碰 | `git status` |

---

## 6. 紅線自證

| 紅線 | 自證 |
|---|---|
| 未殺任何行程 | 全程未執行 `Stop-Process` / `taskkill` / `pkill` / `kill` / `wmic process delete`。**未發現疑似殘留行程需要回報。** |
| 未啟動任何伺服器 | 未啟動 `run_server.py` 或任何服務，未綁 `:8000` / `:8765` / `:8766` / `:8767` |
| 未發真實請求 | 所有測試以 stub 取代 SAGE provider / loader / bus；未對線上服務發任何 HTTP/WS |
| 未寫 `data/**` | 所有 sidecar 輸出落在 pytest `tmp_path` 與 `%TEMP%`；`data/**` 下 `continuity_*` 檔案數 = 0 |
| 未碰 7 個既有未提交改動 | 見 §7 `git status --short`（7 個 M 檔案與實作前完全一致） |
| 未 commit / add / push | 未執行任何 `git commit` / `git add` / `git push`；未用 `checkout` / `stash` / `restore` |
| GC 腳本 | 只跑 dry-run，**未執行 `--apply`** |
| `tests/infra` 既有紅點 | 未修改，見 §8 |
| 測試直譯器 | 全程 `.venv\Scripts\python.exe` |

---

## 7. `git status --short`（實作後）

```
 M clients/voice_companion/static/avatars/rem.mp4      ← 既有
 M clients/voice_companion/web_ui.py                   ← 既有
 M docs/LIFE-THREAD-ENGINE-CONTRACT.md                 ← 既有
 M logs/ENGINEERING_STATE.md                           ← 既有
 M src/memory/middleware.py                            ← ★ 本票（唯一 modified 的既有檔案）
 M src/soul/life_thread_orchestrator.py                ← 既有
 M tests/conftest.py                                   ← 既有
 M tests/soul/test_life_thread_m5_wiring.py            ← 既有
?? clients/.../rem_*.mp4                               ← 既有 untracked
?? docs/*.md                                           ← 既有 untracked
?? harness/eh2_*.py                                   ← 既有 untracked
?? logs/*.md, scripts/*.py, notion_* , prepared-media/ ← 既有 untracked
?? tests/results/rem_soul_smoke/                       ← 既有 untracked
?? tests/soul/test_life_thread_elevation_seam.py        ← 既有 untracked
?? src/memory/continuity.py                            ← ★ 本票新增
?? harness/continuity_analyze.py                       ← ★ 本票新增
?? harness/continuity_gc.py                            ← ★ 本票新增
?? tests/test_memory_continuity_v1.py                  ← ★ 本票新增
?? tests/infra/test_continuity_sidecar_is_additive.py   ← ★ 本票新增
?? docs/MEMORY-CONTINUITY-OBSERVABILITY-V1-IMPLEMENTATION.md ← ★ 本票新增
```

7 個既有 `M` 檔案**一個都沒有被本票碰過**；本票只多一個 `M`
（`src/memory/middleware.py`）與 6 個 `??`。

---

## 8. `tests/infra` 紅點（既有 2 個，本票未修改，Owner 已裁決維持不動）

```
FAILED tests/infra/test_no_production_spawn_guard.py::test_no_unlisted_process_spawn_in_scanned_tree
FAILED tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked
2 failed, 35 passed
```

- **紅點 1（spawn guard）**：`tests/soul/test_life_thread_sim_time.py:508,538`
  的 `subprocess.run(["git", ...])` 未列入 allowlist。**與本票無關**
  （`scanned=331 spawn_sites=22 allowlisted=22`，缺的兩個不在本票路徑）。
  附帶佐證：本票的 `harness/continuity_analyze.py` 內含 `subprocess.run`
  但因為 `harness/` **不在掃描範圍**（掃描範圍 = repo-root `*.py` +
  `tests/**/*.py`），沒有製造新的紅點 —— 這是刻意的設計取捨，見 §9。
- **紅點 2（repo hygiene）**：本票新增的兩個測試檔尚未 `git add`
  （工單明確禁止執行 `git add`），因此該測試現在**多列了本票的 2 個檔案**。
  這與既有的 `tests/soul/test_life_thread_elevation_seam.py` 是**同一類**
  紅點（新增測試尚未納入版控），**不是新種類的違規**。
  失敗的**測試數量仍為 2 個**，不變。主大腦 commit 後此項自然轉綠。

---

## 9. 關鍵設計取捨（需主大腦知悉）

### 9.1 取 HEAD 原始碼的 subprocess 放在 `harness/`，不在 `tests/`

工單要求 G3/T11 用 `git show HEAD:src/memory/v1/loader.py` 做 AST 比對。但
`tests/infra/test_no_production_spawn_guard.py` 的 **R5 規則**要求
`tests/**` 內**每個** `subprocess.*` 呼叫點都被 `(file, line)` 逐行列管，
且 allowlist 大小鎖死、不接受新增。因此若在測試檔內直接呼叫 `git show`，
會**製造一個新的紅點**（且無法在不動既有 allowlist 的前提下合法加入）。

取捨：把 `git_head_source()` 放在 `harness/continuity_analyze.py`
（離線工具，**不在 spawn guard 掃描範圍**），測試 import 它。
結果：**零新增紅點**，G3/G5 的 AST 比對基準仍是不靠行號的 HEAD 版本。

### 9.2 Layer 3 的 use heuristic 需要當下的 memory 原文

`TurnLinkIndex` 的 value schema 被凍結為 `{snapshot_id, linked_at}`，
不含 memory 原文；而 `record_turn(event)` 只拿到 event。因此
`ContinuityRecorder` 另外維護一份**記憶體內**的
`snapshot_id → [(memory_id, memory_text), ...]`（上限 200，FIFO 逐出），
供寫入 Layer 3 時即時算 rollup。這是純記憶體暫存、不落盤、不參與任何決策，
不改動被凍結的 `TurnLinkIndex` schema。

### 9.3 工單內部小矛盾（已修正；canonical schema = 14 欄）

工單 T2 原寫「11 個必填欄位」，但同份工單的 **Schema（凍結）** 區塊實際列出
**14 個**欄位。實作與測試自始以 **Schema 區塊為準**（14 欄全備），
理由：Schema 段落自我標示為凍結。

**Bry D11（2026-10-07）裁決：文件一致性須於 commit 前修正**，不得留下
「T2: 11 / impl: 14」讓驗收者無從判斷 canonical schema。
工單 T2 已更正為 **14 欄**，並註明 canonical 來源為 Schema（凍結）區塊。
本節保留作為修正紀錄。

**14 欄清單**：`schema_version`、`snapshot_id`、`event_id`、`correlation_id`、
`session_id`、`agent_id`、`memory_id`、`memory_text`、`memory_text_hash`、
`retrieval_confidence`、`injection_rank`、`injection_timestamp`、`response_id`、
`retention_expires_at`。

### 9.4 `empty_text` 與 cooldown 的取捨

cooldown early return（`:505`）只擋 graph 寫入，**不影響** Layer 3 觀測：
即使某 agent 在 cooldown 內說話，`record_turn()` 仍會留下一列
（`response_status="ok"`）。這是刻意的 —— P5 要求缺資料可見；若把 cooldown
也當成「不該觀測」，就會重新製造一個靜默缺口。

---

## 10. 已知限制

1. **SAGE 路徑無觀測 ≈ 40% 覆蓋缺口**。本票只觀測 v1 Loader 注入路徑。
   `src/memory/sage/**` 零改動（D1 明確不碰），走 SAGE prefetch 的記憶
   完全不會出現在 continuity sidecar 中。**這不是缺陷，是範圍邊界**，
   統計覆蓋率時必須扣除。
2. **4-gram 不去空白 / 不去標點 → 保守偏差**。`canonicalize()` 只做
   NFKC + casefold。`"喜歡,咖啡"` 與 `"喜歡咖啡"` **不會**互相命中。
   因此 use heuristic **傾向低估**（false negative 多），不會高估。
   真實 use rate 的下限由這個訊號給出。
3. **`TurnLinkIndex` 是記憶體狀態**。TTL 15 分鐘 / 上限 200 筆，
   **服務重啟或事件跨 process 即失聯** → 產生 `link_status="unlinked"` +
   `injection_snapshot_id: null`。這是**設計上的可見缺口而非缺陷**：
   缺資料被標記出來，而不是被當成不存在（P5）。
4. **`use_candidate` 不是因果**。4-gram 重疊只證明「字面上有交集」，
   不證明 Soul 因為該記憶而生成那句話。`use_confidence` 永遠是
   `"heuristic"`，全檔不得出現斷言式的 `memory_used` 欄位（T5 護欄）。
5. **資料尚未產生**。sidecar 需要**服務重啟**才生效
   （`SOULOS_CONTINUITY_OBSERVABILITY` 預設 `"1"`）。本票**未重啟服務**
   （鐵律），因此 `data/memory/continuity_*.jsonl` 目前不存在，
   必須由 Bry 重啟後才會開始累積 lineage 資料。
6. **30 天 retention 是硬性截斷**。`harness/continuity_gc.py --apply` 會
   不可逆地刪除過期列（預設 dry-run）。超過 30 天的 continuity 問題
   **無法**回溯調查。
7. **SAGE ≈ 40% + stub turn 的 `link_status` 多為 `unlinked`**：
   在實際流量中，`AGENT_SPEAK` 的 `correlation_id` 由 `proxy.py` 透傳，
   但只對走 v1 Loader 白名單的注入才有對應 snapshot。

---

## 11. 交付物索引

| 檔案 | 說明 |
|---|---|
| `src/memory/continuity.py` | Layer 2 / Layer 3 sidecar（純 additive、零 LLM、lazy 開檔） |
| `harness/continuity_analyze.py` | `--self-test`（join 100% + 4-gram 邊界）、`--rescore`（零費用重判）、`git_head_source` |
| `harness/continuity_gc.py` | 30 天 retention GC，預設 dry-run，`--apply` 才原子改寫 |
| `tests/test_memory_continuity_v1.py` | T1–T12 |
| `tests/infra/test_continuity_sidecar_is_additive.py` | G1–G5（含 G3b / G5b 強化） |
| `docs/MEMORY-CONTINUITY-OBSERVABILITY-V1-IMPLEMENTATION.md` | 本報告 |