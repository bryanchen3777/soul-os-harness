# 工單：LIFE-THREAD-W3-FIX-1 — 生活線頭昇華接縫的正文修復（extras 補 `narrative` ＋ `reflection`）

> **狀態**：待派工（決策完備版）
> **日期**：2026-10-19
> **Owner 授權**：2026-10-19 裁決 1（正文與感悟結構化雙鍵合流）、裁決 2（Fact 實體**不**接入昇華鏈）、裁決 3（`ENGINEERING_STATE.md` 登記授權）。
> **量測依據**：`logs/LIFE-THREAD-ELEVATION-MEAS-1-REPORT.md`（`dedf9e3`）——NO-GO，根因＝事件沒帶正文。
> **前置**：`docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md`（接縫已於生產開著，旗標 `LIFE_THREAD_ELEVATION_ENABLED=1`）。

---

## 0. 為什麼是這張票（一頁）

量測定案（35 條真實溶解事實、逐條裁決、48 個生產檔 sha256 前後相同）：

```
life_thread_orchestrator.py:418-423   extras = {thread_id, sage_fact_id, origin_type, title}   ← 沒有 narrative
elevation_adapter.py:143               _NARRATIVE_EXTRA_KEYS = ("narrative",)                  ← 只認 narrative
  → _event_content() :248              回 ""
  → inner_life_event_to_input() :294   回 None
  → run_elevation() :711-720           inputs = [] ⇒ engine.consume() 0 次 ⇒ 0 節點
```

**兩道防火牆 0 阻斷**（C1 PASS：35/35 通過 R1／fact 層／R3）；**內容素材極佳**（35 條全為自然第一人稱散文，零標籤退化，`object` 長度 45／87／141）。
**反事實（`src/**` 一行未改，只在沙盒補一個鍵）**：35/35 進入引擎 → 35 pattern → `elevate_matured_patterns` → **7 顆 value 節點**。

⇒ 本票就是把那個鍵（依 Owner 裁決 1：**兩個鍵**）真的補上，並同步修正過期的契約文件。

**本票不動**：`elevation_adapter.py`（尤其 `_NARRATIVE_EXTRA_KEYS`）、`src/memory/sage/**`、`src/inner_life/**`、`data/**`、生產 `.env`。

---

## 1. 目標

讓生活線頭的**經歷**真正進入昇華鏈，產出 belief／value 節點。

**一句話**：事件帶上正文，白名單就會認；白名單一動都不必動。

---

## 2. 範圍

| # | 檔案 | 動作 | 性質 |
|---|---|---|---|
| 1 | `src/soul/life_thread_orchestrator.py` | **唯一**程式改動：`extras` 補 2 個鍵 ＋ 1 個唯讀 helper | 窄 |
| 2 | `docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md` | 5 處過期假設修正（§6 表） | 文件 |
| 3 | `docs/LIFE-THREAD-ENGINE-CONTRACT.md` §4.6 | 補「正文鍵」條款 | 文件 |
| 4 | `docs/INNER-LIFE-DEFINITION-OF-DONE.md` §「觀察」表 | 分母 21 → **35**；「未接昇華路徑」→「**接縫已開但正文未進去 ⇒ 產出 0**」 | 文件 |
| 5 | `logs/LIFE-THREAD-ELEVATION-MEAS-1-WORK-ORDER.md` §5 C1 | 分母 21 → **35** | 文件 |
| 6 | `tests/soul/test_life_thread_w3_fix_narrative.py`（**新檔**） | 新守衛 ＋ 變異實驗 | 測試 |
| 7 | `tests/soul/test_life_thread_elevation_seam.py`（既有） | **原則上零修改**（見 §6.3 唯一例外） | 測試 |

**禁止**：`src/inner_life/**`、`src/memory/sage/**`、`src/soul/life_thread_dissolution_exec.py`、`src/soul/life_thread_wake_gate.py`、`configs/**`、`personas/**`、`data/**`、外部 `soul-elevation` repo。

---

## 3. 做法（決策已定，執行者照做）

### 3.1 `extras` 補兩個鍵（Owner 裁決 1）

`src/soul/life_thread_orchestrator.py:418-423` 現況：

```python
                extras={
                    "thread_id": str(thread_id),
                    "sage_fact_id": str(fact_id),
                    "origin_type": str(thread.get("origin_type") or ""),
                    "title": str(thread.get("title") or ""),
                },
```

**改為**（鍵的取捨順序固定，執行者照抄）：

```python
                extras={
                    "thread_id": str(thread_id),
                    "sage_fact_id": str(fact_id),
                    "origin_type": str(thread.get("origin_type") or ""),
                    "title": str(thread.get("title") or ""),
                    # 🔴 正文與感悟雙鍵（Owner 2026-10-19 裁決 1）。
                    # `narrative` 是 _NARRATIVE_EXTRA_KEYS 唯一接受的鍵 ⇒ 節點正文來源。
                    # `reflection` 是溶解後的感悟，**目前不被 _event_content() 消費**
                    #   （白名單刻意不擴張，見 SOUL-BUG-006）⇒ 純伴隨 metadata，
                    #   不得宣稱它影響節點內容。
                    "narrative": str(thread.get("narrative_content") or ""),
                    "reflection": _elevation_reflection(agent_id, fact_id),
                },
```

**規格**：

| 鍵 | 來源 | 規格 |
|---|---|---|
| `narrative` | `thread["narrative_content"]` | `str(...)`；欄位不存在或 None ⇒ **空字串**（合法）。**不得**改用 `title`、**不得**拼接多段文字。 |
| `reflection` | 事實庫中 `fact_id = thread["sage_fact_id"]` 的 `object` | 查無該 fact ⇒ **空字串**（合法，**不得**因此跳過事件建立）。 |

⚠️ **`Provenance.extras` 的 value 必須是 `str`**（`src/inner_life/event.py:126-130`）⇒ 兩鍵一律 `str()`。

### 3.2 `_elevation_reflection()`：唯讀回讀事實正文

**沿用 repo 既有的 read-only 慣例**（`src/soul/decision.py:470-478` 的既有寫法，**不要重寫一份**）：

```python
def _elevation_reflection(agent_id: str, fact_id: str) -> str:
    """取溶解事實的 `object`（感悟正文）。**純唯讀、fail-soft、絕不 raise。**

    慣例來源：src/soul/decision.py:470-478（同一個 per-agent graph.sqlite 讀取慣例）。
      db_path = data_root() / "memory" / agent_id / "graph.sqlite"
      檔案不存在 ⇒ 直接回 ""（不開 store）
      GraphStore 在 schema 已是現行版本時**不寫入**（MEM-VISIBILITY-C2 修正，
      graph_store.py:113-118：避免第二個 process 開連線就變成寫者而阻塞 11s）
    """
    try:
        if not agent_id or not fact_id:
            return ""
        from src.memory.sage.graph_store import GraphStore
        from src.paths import data_root

        db_path = data_root() / "memory" / agent_id / "graph.sqlite"
        if not db_path.exists():
            return ""
        store = GraphStore(db_path=db_path)
        try:
            fact = store.get_fact(str(fact_id))
            if fact is None:
                return ""
            return str(getattr(fact, "object", "") or "")
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 — fail-soft：查不到就沒有感悟，不得影響事件建立
        logger.debug(
            "[LifeThreadOrchestrator] reflection 回讀失敗 (fail-soft): %s: %s",
            type(exc).__name__, exc,
        )
        return ""
```

**規格**：
- **開 store 的時機**：**每個 agent 每次 `maybe_emit_elevation_events` 呼叫最多開一次**（不是每個 thread 一次）。⇒ 實作者**必須**把 store 開啟與關閉移到**迴圈外**（例如在 `maybe_emit_elevation_events` 內用一個 lazy 的局部變數持有 store，`finally` 關閉），**不得**在迴圈內每個 thread 開一次。
  ⇒ **推薦做法**：把 `_elevation_reflection` 改成**接受已開好的 store**（`store or None`），並在 `maybe_emit_elevation_events` 的 `try` 外層用 `finally` 關閉；`store` 為 `None` 時 helper 自行開（測試注入路徑）。**二選一，但必須確保「每次呼叫至多開一次」**。
- **零寫入**：不得 INSERT/UPDATE/DELETE；不得寫 `schema_meta`（GraphStore 在現行 schema 下本就不寫，**但不得依賴這一點**——若傳入的 db_path 是舊 schema，GraphStore 會做 migration ⇒ **必須**在開 store 前確認 `db_path.exists()`，且**不得**對不存在的檔案開 store）。
- **fail-soft**：任何例外（檔案不存在／鎖住／schema 異常／fact 查無）⇒ 回 `""`，**不得**讓例外冒泡（外層已有 try/except，但本 helper 自己也要 fail-soft）。
- **不得**在 helper 裡呼叫 LLM、**不得**寫任何檔案。

### 3.3 不得改動的東西（工單明列）

| 項 | 理由 |
|---|---|
| `elevation_adapter.py:143` 的 `_NARRATIVE_EXTRA_KEYS = ("narrative",)` | SOUL-BUG-006（Owner 裁示）**刻意**只認 `narrative`、刻意不收 `title`（「用標題冒充正文會讓節點語意失真；寧可少一顆節點」）。**擴張白名單＝違背既有裁決。** |
| `submit(event_id, (), agent_id=...)` 的**空 tuple** | Owner 裁決 2：維持解耦，事實實體**不**接入昇華鏈（避免 provenance 重組與去重鍵衝突）。 |
| `elevate()` 零呼叫 | 契約：昇華路徑只 consume，pattern→belief/value 走另一個機制 `elevate_matured_patterns`。 |
| 生產 `.env` 的 `LIFE_THREAD_ELEVATION_ENABLED=1` | **不動**（本票不部署、不改旗標）。 |
| `src/memory/sage/**` | 寫入邏輯 Frozen（只**呼叫** `get_fact`，不改任何一行）。 |

### 3.4 文件修正（5 處，見 §6 表）

---

## 4. 驗收（完成的定義）

1. **正文提取成功**：對生產真實的 completed 線頭，`_event_content(event)` 回傳**非空**且**等於** `thread["narrative_content"]`（逐位元相同）。
2. **不再回 None**：`inner_life_event_to_input(event)` 回傳**非 None** 的 `ElevationInput`。
3. **離線沙盒端到端**：把生產真實的 **35 筆 completed 線頭**（`data/soul/<agent>/life_threads.jsonl`）餵進修好的接縫（沙盒內），經 `submission_gate.submit()` → `run_elevation` → `engine.consume()` → `elevate_matured_patterns`，**至少產出 1 顆** `value`（或 `belief`）節點，且其 `content` 經人工判讀**不是中繼標籤**。
4. **既有 10 支 W3 接縫測試零修改全綠**（`tests/soul/test_life_thread_elevation_seam.py`）。
5. **變異實驗全紅**（見 §5）。
6. **生產零寫入**：沙盒端到端實驗前後，生產 `data/elevation/*.jsonl` ＋ 10 個 `data/memory/<agent>/graph.sqlite` ＋ 10 個 `data/soul/<agent>/life_threads.jsonl` 的 sha256 逐位元相同。
7. `git diff -- src/inner_life/` **為空**；`git diff -- src/memory/sage/` **為空**。
8. **文件 5 處修正全部落地**（§6 表逐項可指認）。

---

## 5. 變異實驗（雙向；沿用 `%TEMP%` 副本慣例，**repo 從不被修改**）

範本：`%TEMP%\mut010b.py`（把 `src/` + `tests/` + `pytest.ini` 複製到 `%TEMP%\<workdir>`，**只改副本**，用 repo 的 `.venv` python 在副本 cwd 跑指定測試檔）。**每條變異必須讓對應守衛轉紅**；轉綠即盲腸守衛，必須回報並補強。

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| MUT-W1 | 把 extras 的 `"narrative"` 拿掉 | 正文提取守衛 ＋ 沙盒端到端守衛（產出必為 0） |
| MUT-W2 | 把 `"narrative"` 改成填 `title` | 正文提取守衛（值不等於 `narrative_content`） |
| MUT-W3 | 把 `"reflection"` 改成填 `title` | reflection 守衛 |
| MUT-W4 | `_elevation_reflection` 恆回 `""` | reflection 守衛 |
| MUT-W5 | `_elevation_reflection` 回傳 `thread["title"]` | reflection 守衛 |
| MUT-W6 | 把 `elevation_adapter.py:143` 的白名單擴成 `("narrative", "title")` | **白名單守衛**（本票**不得**擴張白名單 ⇒ 變異必須被守衛抓到） |

**另需**：受控污染實測——把某條線頭的 `narrative_content` 換成 `title`，斷言該條節點的 content **變成標題**（證明守衛能分辨「正文」與「標題」），而非默默通過。

---

## 6. 文件修正清單（5 處 ＋ 分母）

### 6.1 `docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md`

| # | 條目 | 修正為 | 理由 |
|---|---|---|---|
| 1 | 步驟 3「依線頭的 `origin_type` 對應時段」 | **改為**：trigger_type 依 **scheduler slot**（morning／night）決定。`origin_type` 的實際值域是 `necessity_driven`／`whim_driven`／`world_collision`，**不是時段** | 量測實測 35/35 條 `origin_type` ∈ {necessity_driven, whim_driven}；現行實作已用 `_elevation_trigger_for_slot(slot)`，文件過期 |
| 2 | 「建好事件即進昇華」的隱含假設 | **補一條**：事件**必須**帶 `extras["narrative"]`（正文），否則 `_event_content()` 回空 ⇒ `inner_life_event_to_input()` 回 `None` ⇒ fail-closed ⇒ 0 節點 | 本量測根因；W3 原文未提正文如何進入事件 |
| 3 | 「只 consume 不 elevate」 | **補一條**：從 pattern 到 belief／value 需**另一個機制** `elevate_matured_patterns`（`elevation_adapter.py:1098`），與 Submission Gate **相互獨立** | 反事實顯示 consume 只出 35 pattern；value 節點是 `elevate_matured_patterns` 產的 |
| 4 | 產出驗收 | **補一條**：驗收須**區分「事件已建立」與「節點已產出」**——`maybe_emit_elevation_events` 回 `True` 但節點數為 0 是**靜默失敗** | 量測 §4.4 |
| 5 | 「溶解事實」的定位 | **釐清**：`sage_fact_id` 目前只是 `extras` 裡的一個 id；**fact 實體未進入昇華鏈**（`submit(event_id, (), ...)` 傳空 tuple）——依 Owner 裁決 2 **維持如此** | 量測 §7 |

另加 §4.7「正文與感悟雙鍵（Owner 2026-10-19 裁決 1）」：`narrative` = 經歷敘事（`thread.narrative_content`，正文來源）；`reflection` = 溶解感悟（事實 `object`，**目前不被 `_event_content()` 消費**，純伴隨 metadata）。

### 6.2 `docs/LIFE-THREAD-ENGINE-CONTRACT.md` §4.6

補一條：`extras` 必含 `narrative`（正文來源，白名單唯一接受鍵）與 `reflection`（感悟，伴隨 metadata）；**白名單不擴張**（SOUL-BUG-006 維持）。

### 6.3 `docs/INNER-LIFE-DEFINITION-OF-DONE.md`「觀察」表

| 項 | 現況寫的 | 改為 |
|---|---|---|
| 溶解事實 | 21 條 | **35 條**（35/35 有 `dissolved_at` 與 `sage_fact_id`；`origin` 100% `lived_experience`） |
| 線頭總數 | 38（17 active / 21 completed） | **55（20 active / 35 completed）** |
| 溶解事實進入 `run_elevation` | 0 | **35 次，但引擎收到 0 筆**（事件已建立卻無正文 ⇒ fail-closed） |
| 診斷 | 「未接昇華路徑」 | 「**接縫已開著**（旗標 = 1）**但事件未帶正文** ⇒ 產出 0 節點」（修完即通） |

### 6.4 `logs/LIFE-THREAD-ELEVATION-MEAS-1-WORK-ORDER.md` §5 C1

「真實 21 條事實」⇒ **「真實 35 條事實」**（量測報告已以 35 為分母；工單的分母過期，須同步）。

### 6.5 `tests/soul/test_life_thread_elevation_seam.py`（既有 10 支）

**原則上零修改。** 唯一例外：若某支測試的合成線頭 fixture **完全沒有 `narrative_content` 欄位**，而該測試又斷言「事件被建立」——那**不是**要改斷言，而是**fixture 缺欄位**。
⇒ **處理規則**：**不得**放寬或刪除任何既有斷言。若必須調整，只能是**在合成 fixture 補上 `narrative_content` 欄位**（與生產真實線頭同構），並在該測試的 docstring 標明原因。
⇒ **請先試圖零修改通過**；只有在確實不可能時才動它，並在報告中明說是哪一支、為什麼、動了哪一行。

---

## 7. 測試

- 必須用 `C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe -m pytest`（**唯一合法解譯器**）。
- 新增 `tests/soul/test_life_thread_w3_fix_narrative.py`，至少涵蓋：
  1. extras 含 `narrative`，且值**逐位元等於** `thread["narrative_content"]`。
  2. extras 含 `reflection`，值**逐位元等於**事實的 `object`（以合成 fact 驗證）。
  3. `narrative_content` 缺失／空 ⇒ `narrative` 為空字串（**不得**拋例外、**不得**跳過事件建立）。
  4. fact 查無 ⇒ `reflection` 為空字串（**不得**拋例外、**不得**跳過事件建立）。
  5. `_elevation_reflection` 的 fail-soft：DB 不存在／鎖住／例外 ⇒ 回 `""`，不 raise。
  6. **每次呼叫至多開一次 store**（以 mock 計數 `GraphStore.__init__` 次數：多個 thread ⇒ 1 次）。
  7. 零寫入：不 INSERT/UPDATE/DELETE（AST 或 mock 斷言）。
  8. `_event_content(event)` 回傳非空且等於 `narrative_content`。
  9. `inner_life_event_to_input(event)` **不**回 `None`。
  10. **白名單守衛**：`elevation_adapter._NARRATIVE_EXTRA_KEYS == ("narrative",)`（**不得**被本票擴張）。
  11. `submit()` 仍傳空 tuple（Owner 裁決 2 不變）。
  12. `elevate()` 呼叫次數仍為 0。
  13. **沙盒端到端**（合成圖譜）：完成線頭 ⇒ 至少產出 1 顆 value/belief 節點，content 非標籤。
  14. 生產資料零寫入（執行前後 sha256 相同）。
- 必須一起跑：`tests/soul/test_life_thread_elevation_seam.py`（既有 10 支）＋ `tests/soul/` 全目錄 ＋ `tests/inner_life/`。
- **不要跑全庫回歸**（約 11 分鐘，主大腦另外跑）。

---

## 8. 不做（Out of Scope）

- ❌ **不擴張 `_NARRATIVE_EXTRA_KEYS`**（SOUL-BUG-006 凍結面）。
- ❌ **不把 fact 實體接入昇華鏈**（維持空 tuple，Owner 裁決 2）。
- ❌ **不新增 `elevate()` 呼叫點**（契約：只 consume）。
- ❌ **不動** `src/inner_life/**`、`src/memory/sage/**`、`src/soul/life_thread_dissolution_exec.py`、`src/soul/life_thread_wake_gate.py`。
- ❌ **不動**生產 `.env`、**不重啟服務**、**不部署**（本票只改碼與文件；部署另需 Owner 授權）。
- ❌ **不動** `data/**`（唯讀）。
- ❌ **不改** SOUL-BUG-006 的既有裁決文字。
- ❌ **不新增** LLM 通道、**不新增**定時器、**不新增**數值評分欄位。
- ❌ **不得使用 `subprocess`**（`tests/infra/test_no_production_spawn_guard.py` R5 規則：每個 spawn 呼叫點都要列入 `(file, line)` 白名單且大小鎖死 22 ⇒ **不得擴大白名單**）。測試請在**同一直行程**內直接呼叫被測函式。
- ❌ **不得殺任何行程**、不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。

### 行程與生產安全紅線（違反視為重大事故）

1. 任何理由都不得殺行程（`Stop-Process`／`taskkill`／`pkill` 一律禁止）。
2. 不得用裸 PID 做判斷。
3. 不得為了清理而寫入、改名或刪除 `data/**` 任何檔案。
4. 不得重啟服務（本票 0 重啟）。
5. `scripts/**` 下的 `manual_*.py` **不得**執行。
6. 不得用 `git add -A`、**不得** push。
7. 引用測試數字必須用 `.venv\Scripts\python.exe`。
8. 不得用 `git checkout --` 或 `git show rev:path > file` 還原（`core.autocrlf=true` 會寫出 CRLF）。

### Windows 環境陷阱

- 跑任何 Python 前設 `$env:PYTHONIOENCODING='utf-8'` 與 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`（cp950 會 `UnicodeEncodeError`）。
- 多行 Python 一律寫成 `.py` 檔再執行，**不可**用 `python -c "<多行>"`。

---

## 9. Frozen Contract 注意

| 項目 | 本票處置 |
|---|---|
| `src/inner_life/elevation_adapter.py`（含 `_NARRATIVE_EXTRA_KEYS`） | **0 改動** |
| `src/inner_life/submission_gate.py`／`event.py` | **0 改動** |
| `src/memory/sage/**`（寫入邏輯 Frozen） | **0 改動**（只呼叫 `get_fact`） |
| `src/soul/life_thread_dissolution_exec.py`（M2 凍結） | **0 改動** |
| `src/soul/life_thread_wake_gate.py`（M3 凍結） | **0 改動** |
| `VALID_PRODUCER_TRIGGER_TYPES` | **0 改動**（沿用既有 diary 常數） |
| `elevate()` 零呼叫 | 必須維持 |
| `submit()` 空 tuple（Owner 裁決 2） | 必須維持 |
| 生產 `.env` | **0 改動** |
| `data/**` | **唯讀** |

---

## 10. 回報格式

1. 改動檔案清單 ＋ `git diff --stat` 原始輸出。
2. §3 每個決策的實作位置（檔案:行號）與「與決策一致」的對照。
3. 測試結果：命令、exit code、過／失敗數、耗時、**解譯器路徑**。
4. 變異結果：MUT-W1～W6 各自轉紅／轉綠 ＋ 證據摘錄。
5. 沙盒端到端量測：35 筆線頭的 `narrative_content` 非空筆數、`reflection` 命中筆數、產出的 belief／value 節點數與 content 樣本（**分子／分母／單位**）。
6. 生產零寫入證明（sha256 對照原始輸出）。
7. Frozen Contract 逐項回答（上表每項標「0 改動」並附證據）。
8. `tests/soul/test_life_thread_elevation_seam.py` 是否零修改；若否，逐支說明動了哪一行、為什麼。
9. 未完成或受阻事項（如實列出）。
10. **一個 commit**（在 `main` 上，訊息前綴 `fix(LIFE-THREAD-W3-FIX-1):`），**不要 push**。commit message 不得含 BOM。
