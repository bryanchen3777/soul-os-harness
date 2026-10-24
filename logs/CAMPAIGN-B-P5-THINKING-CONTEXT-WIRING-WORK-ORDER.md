# 工單：CAMPAIGN-B Phase 5（P5-①）張力思考狀態 唯讀上下文接線

> 開立：2026-10-23 23:22 EDT（主大腦）；**2026-10-24 00:50 EDT 重大修訂（v2）**（主大腦派工前自檢發現 Phase 4↔Phase 5 工單互斥，已在 §2.1 解開並擴充範圍至 4 檔）
> 依賴：**Phase 4 已合入 main**（本票消費 Phase 4 的 `format_working_state_lines()` 與 `WORKING_STATE_FILENAME`）
> 母案：`logs/CAMPAIGN-B-P4-ASYNC-THINKING-LOOP-WORK-ORDER.md`（Phase 4 明確寫「Phase 5 不得把 `format_working_state_lines()` 接進 `proxy.py`——那是 Phase 5 的事」）

---

## 0. 出發點（已量現況，不猜）

| 事實 | 值 | 來源 |
|---|---|---|
| Phase 4 已提供唯讀接口 | `src/inner_life/tension_closure_loop.py` 的 `format_working_state_lines(agent_id, store_dir, *, max_entries=5, max_chars=60)` | Phase 4 交付（主大腦複核：4 檔逐位元一致、70 筆守衛、變異 M8～M13 全紅） |
| 該接口的回傳 | 未消解張力行列表（每行 `- [YYYY-MM-DD 張力未解] 還有 N 個未解的問題（已想過 X%）`），每行 ≤60 字元 | Phase 4 守衛 P5-T9 實測 |
| **生產上 `data/inner_life/working_state.jsonl` 不存在** | MISSING | 23:16 實測（`data/inner_life/` 只有 `trace.jsonl`、`epistemic_tensions.jsonl`） |
| 生產上 `epistemic_tensions.jsonl` | 1,245 bytes／2 筆（distinct tension_id＝1，重複待回報） | 22:48 實測 |
| 🔺 **Phase 4 的沙盒守門會擋掉生產讀取** | `check_store_dir()` 對生產路徑**三重拒絕** | 主大腦 2026-10-24 00:47 實讀 `tension_closure_loop.py:248-275` |

### 🔺 關鍵新事實（本票的核心設計前提）

`format_working_state_lines()` 的第一步是：

```python
allowed, reason = check_store_dir(store_dir)
if not allowed:
    return []
```

而 `check_store_dir()`（`tension_closure_loop.py:248-275`）對 `store_dir` 有**三重 fail-closed 拒絕**：
1. 解析後位於**生產資料根**（`src.paths.data_root()`）之下 ⇒ 拒絕；
2. 位於 **repo `data/`** 之下 ⇒ 拒絕；
3. **不在 temp 之下** ⇒ 拒絕。

⇒ **傳入生產路徑（`Path(data_root()) / "inner_life"`）必然回空 list。**
⇒ Phase 4 工單 §2.4-6 明文「**不得讀生產路徑**（`store_dir` 必須先過 `check_store_dir()` 的既有雙條件）」；
⇒ 而 Phase 5 若照舊版規格（讀生產路徑）⇒ **兩張工單互斥**，且 P5-① 在生產上**永遠是空區塊**＝接線了也不會生效。
⇒ **主大腦已在 §2.1 裁示解開；worker 不得自行選擇其中一邊。**

---

## 1. 目標

把「角色尚未想通的事」以**唯讀**方式接入 `src/llm/proxy.py` 的 Inner Life 提示詞組裝：
- **讀取面**：Phase 4 的沙盒介面**原封不動**（契約不變），**另外**新增一個**路徑不可注入**的生產唯讀介面；
- **接線面**：在 `_build_messages_group` 與 `_build_messages_private` 兩條路徑，於 `[最近內在生活]` 之後、`world_context` 之前注入；
- **無資料時完全不影響 prompt**（逐位元相同）。

---

## 2. 做法（決策已定，執行者照做）

### 2.1 🔺 主大腦裁示：Phase 4↔Phase 5 互斥的解法（**不得自行另選**）

**裁示：Phase 4 的 `format_working_state_lines()` 契約**原封不動**（仍是沙盒專用、仍過 `check_store_dir()`）；
**本票額外在同一模組新增一個「生產唯讀」介面**，專供 proxy.py 的接線使用。**

理由（決策已定，執行者照做即可）：
1. Phase 4 的交付物已被主大腦獨立複核並核准（70 筆守衛＋變異 M8～M13 全紅＋4 檔逐位元一致）⇒ **不重新改寫、不重新驗證已核准的契約**。
2. 生產唯讀介面**唯一的呼叫者就是 proxy.py 接線** ⇒ 兩者同票落地，**不產生「已出貨但無呼叫者」的懸空 API**。
3. 本票在 **Phase 4 已合入 main 之後**才動 `tension_closure_loop.py` ⇒ **與 Phase 4 合入無衝突**（舊版 §2.3.2 的「不得改 tension_closure_loop.py」限制**解除**，理由即本條）。
4. **共享同一份格式化核心** ⇒ 行的文字格式**只有一個真實來源**，不會出現「契約與實作漂移」。

### 2.2 新增生產唯讀介面（`src/inner_life/tension_closure_loop.py`）

#### 2.2.1 抽出共享讀取核心（**逐位元搬移，不得改寫**）

把 `format_working_state_lines()` 中**位於 `check_store_dir()` 之後**的整段本體，**原樣**抽成一個私有函式：

```python
def _read_working_state_lines(
    agent_id: str,
    store_dir: Any,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """共享唯讀核心（**不做路徑守門**；守門由兩個公開介面各自負責）。

    本體自 `format_working_state_lines()` 原樣搬移，**逐字元不得改動**。
    """
```

🔺 **搬移規則（決定變異錨點能不能存活）**：
- 搬移範圍＝從 `path = Path(store_dir) / WORKING_STATE_FILENAME` 起，到 `return [line][: max(0, int(max_entries))]` 止（含 `path.is_file()` 的 fail-silent、`decided` 判斷、截斷、上限語意）。
- **縮排維持 4 空白**、**行內容逐位元不變**（含 `# ② 取…`／`# ③ 已想完…`／`# ④ 未解…`／`# ⑤ 截斷…`／`# ⑥ 上限語意。` 等註解）。
  ⇒ Phase 4 的變異 **M11**（錨點 `if not path.is_file():\n        return []`）、**M12**（錨點 `if record.get("decided") is True:\n        return []`）、**M13**（錨點 `# ⑥ 上限語意。\n    return [line][: max(0, int(max_entries))]`）**必須仍能逐位元命中**。
- 原 `format_working_state_lines()` 改為「守門 → 委派」：

```python
def format_working_state_lines(
    agent_id: str,
    store_dir: Any,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """（Phase 4 契約，**語意完全不變**）沙盒 store_dir 的唯讀上下文行。"""
    allowed, reason = check_store_dir(store_dir)
    if not allowed:
        logger.debug("[tension-closure-loop] 唯讀介面拒絕：%s", reason)
        return []
    return _read_working_state_lines(
        agent_id, store_dir, max_entries=max_entries, max_chars=max_chars
    )
```

#### 2.2.2 新增生產唯讀介面

```python
def format_working_state_lines_production(
    agent_id: str,
    *,
    max_entries: int = 5,
    max_chars: int = 60,
) -> List[str]:
    """（Phase 5 新增）生產 working_state 的唯讀上下文行。

    🔴 **路徑不可注入**：``store_dir`` **不在簽章裡**，由本函式內部從
    ``src.paths.data_root()`` 推導 ⇒ 呼叫端無法指定任何路徑。
    🔴 **唯讀**：只讀 ``<data_root>/inner_life/working_state.jsonl``；
    **不得寫、不得 append、不得 checkpoint**（守衛 P6-T14 以位元組對帳釘死）。
    🔴 **fail-silent**：解析失敗／檔案不存在／壞行 ⇒ 回空 list，**不得** raise。
    """
    try:
        from src.paths import data_root
        store_dir = Path(data_root()) / "inner_life"
    except Exception as exc:  # noqa: BLE001 — fail-silent
        logger.debug(
            "[tension-closure-loop] 生產資料根解析失敗：%s: %s", type(exc).__name__, exc
        )
        return []
    return _read_working_state_lines(
        agent_id, store_dir, max_entries=max_entries, max_chars=max_chars
    )
```

🔺 **硬性要求**：
1. **不得**新增任何 `store_dir`／`path` 參數（守衛 P6-T15 以 `inspect.signature` 釘死）。
2. **不得**改動 `check_store_dir()` 的任何一行（沙盒守門語意不變）。
3. **不得**在 `__all__` 加入 `_read_working_state_lines`（私有）；**必須**加入 `format_working_state_lines_production`。
4. **不得**改動 `format_working_state_lines()` 的**對外語意**（引數、預設值、回傳格式、fail-silent 行為全部不變）。

### 2.3 proxy.py 的 helper（`src/llm/proxy.py`）

```python
def _format_working_state_block(agent_id: str) -> str:
    """P5-① 張力思考狀態（唯讀）。雙重失敗隔離：任何例外 → ""（prompt 逐位元不變）。"""
    try:
        from src.inner_life.tension_closure_loop import (
            format_working_state_lines_production,
        )
        lines = format_working_state_lines_production(agent_id)
    except Exception:  # noqa: BLE001 — 雙重失敗隔離：思考狀態缺失不影響 prompt
        return ""
    if not lines:
        return ""
    ...（bounded 後回傳 block 字串）
```
- **雙重失敗隔離**（與 `_format_recall_block`／`_format_piggyback_block`／`_format_emergent_block` 同款）：import 失敗 → ""；讀取／解析例外 → ""。
- **有界**：最多 **3 行**、每行 **≤60 字元**、總字元 **≤200**（超長截斷並加省略號）。
- **0 洩漏**：回傳字串**不得**含檔案路徑（`C:\`／`data\`）、不得含完整 32 字元 thread_id（若介面已回傳 thread_id，必須只留前 8 字元或完全不含）。
- 區塊文字（固定模板，行數不隨資料變動）：
  ```
  \n[思考狀態] 以下是你最近尚未想通的事。\n{lines}\n
  ```

### 2.4 注入位置（**兩條路徑都要**）
- `_build_messages_group`（proxy.py:817 起）與 `_build_messages_private`（proxy.py:1666 起）兩處，**緊接既有 `[最近內在生活]`（inner_life）區塊之後、`world_context` 之前**。
  （實查位置：group 路徑 inner_life 區塊在 962-969 行、world_context 在 978-979 行；private 路徑 inner_life 在 1787-1794 行、world_context 在 1817-1818 行。）
- 注入寫法與既有慣例一致：
  ```python
  working_state = _format_working_state_block(agent_id)
  if working_state:
      system_parts.append(working_state)
  ```
- **不得**改動任何既有區塊的**相對順序**、文案、或條件（relationship → inner_life → **working_state** → world_context → 當下時間）。

### 2.5 邊界（硬性）
1. 🔴 **不得**接 `drive_loop_async()`、不得接排程器、不得寫任何生產資料（`working_state.jsonl` 由 P5-② 負責產生）。
2. 🔴 **不得**改動 `check_store_dir()`、**不得**放寬 `format_working_state_lines()` 的沙盒守門（`format_working_state_lines(agent_id, <生產路徑>)` 必須仍回空 list）。
3. 🔴 **不得**動任何 `frozen_dirs` 檔（11 檔永久紅線）。
4. 🔴 **不得**引入任何 LLM／HTTP／網路／外部 API 呼叫；不得引入新的持久化、schema、計時器、背景執行緒。
5. 🔴 **不得**改 `src/llm/proxy.py` 與 `src/inner_life/tension_closure_loop.py` 中與本票無關的任何一行（最小差異；`git diff` 必須能逐段說明）。
6. 🔴 **不得**動 `src/inner_life/tension_thinking.py`（Phase 3/4 的檔案）。

---

## 3. 守衛測試（新增檔 `tests/inner_life/test_p5_working_state_context.py`）

| 編號 | 內容 |
|---|---|
| P6-T1 | 無 `working_state.jsonl` 檔 ⇒ block 回 `""`；**且組出的 prompt 與未實作前逐位元相同** |
| P6-T2 | 有檔且有未消解張力 ⇒ block 含該行**逐位元正確**（格式：`- [YYYY-MM-DD 張力未解] 還有 N 個未解的問題（已想過 X%）`） |
| P6-T3 | `format_working_state_lines_production` 拋例外 ⇒ block＝`""`、**不拋出**、prompt 逐位元不變 |
| P6-T4 | import 失敗（模擬）⇒ block＝`""`、prompt 逐位元不變 |
| P6-T5 | 有界：≤3 行、每行 ≤60 字元、總字元 ≤200（餵超量資料驗證截斷） |
| P6-T6 | 0 洩漏：block 內不得出現 `C:\`／`data\`／完整 32 字元 UUID |
| P6-T7 | ordering：`[思考狀態]` 區塊在 `[最近內在生活]` **之後**、world_context **之前**（以組裝後的 system_parts 順序斷言） |
| P6-T8 | group 與 private **兩條**路徑皆注入；且**只有**這兩條（其他組裝路徑不受影響） |
| P6-T9 | **AST 守衛**：`proxy.py` 新增區段與 `tension_closure_loop.py` 新增區段內**不得**出現 HTTP／LLM／網路／寫檔呼叫（`requests`／`httpx`／`urllib`／`openai`／`write_text`／`open(..., "w")` 等） |
| P6-T10 | 0 frozen contract 變更（沿用既有 `frozen_dirs` 斷言） |
| P6-T11 | **既有 70 筆守衛未被本票削弱**（見下方 🔺 實作限制） |
| P6-T12 | **生產等價**：以生產現況（無 working_state 檔）組 prompt ⇒ 與「未實作」的 golden **逐位元相同**（byte-identity） |
| **P6-T13** | **生產唯讀介面**：檔案不存在 ⇒ `format_working_state_lines_production()` 回 `[]`、**不 raise**、**不建立檔案** |
| **P6-T14** | **生產唯讀介面只讀**：呼叫前後，生產 `working_state.jsonl`／`epistemic_tensions.jsonl` **位元不變** |
| **P6-T15** | **路徑不可注入 ＋ 沙盒守門仍在**：① `inspect.signature(format_working_state_lines_production)` **不含** `store_dir`／`path` 參數；② `format_working_state_lines(agent_id, <生產 inner_life 路徑>)` 仍回 `[]`（沙盒守門未被放寬） |

### 🔺 P6-T11 的實作限制（**這是本票最容易踩的坑**）

P6-T11 的語意是「既有 70 筆守衛仍全綠」。**它不得用 `subprocess` 跑另一個 pytest 來實作** —— `tests/**` 禁用 `subprocess`／`os.spawn*`／`os.system`／`os.popen`（R5 護衛），用了會讓整票紅。

**正確實作**：P6-T11 只斷言**本票沒有削弱既有守衛檔**（例如：以 AST 斷言 `tests/inner_life/test_tension_closure_loop.py` 的測試函式數仍為 70、或斷言該檔在 `git diff` 中未被本票改動）。
「70 筆實際全綠」是**驗收層級**的事實，由 §5 的**合併執行**證成（兩個檔一起跑 ⇒ 85 passed），**不是**新檔內的一個測試。

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| M14 | 空資料也注入（移除 `if not lines` 早退） | P6-T1、P6-T12 |
| M15 | 刪掉 `except` 分支（例外冒泡） | P6-T3、P6-T4 |
| M16 | 放寬長度上限（3→99、60→999、200→9999） | P6-T5 |
| M17 | 把注入位置移到 world_context 之後 | P6-T7 |
| M18 | 讓 block 帶上路徑／完整 UUID | P6-T6 |
| M19 | 在 helper 內加一次 HTTP 呼叫 | P6-T9 |
| **M20** | **生產唯讀介面加上 `store_dir` 參數（路徑變得可注入）** | P6-T15 |
| **M21** | **生產唯讀介面在檔案不存在時 raise** | P6-T13 |
| **M22** | **移除 `format_working_state_lines()` 的 `check_store_dir` 守門（沙盒介面也能讀生產）** | P6-T15 |

- 變異 harness 放 `%TEMP%`（**改副本**模式，repo 檔全程不寫入；baseline 必須是**整個守衛檔全綠**）。
- 🔴 新增 collectable 測試檔**必須先 `git add`**，否則 repo hygiene 守衛會紅 ⇒ **本票不執行 `git add`**（由主大腦合入時處理），但報告必須明寫此提醒。

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 4 檔**：`src/inner_life/tension_closure_loop.py`、`src/llm/proxy.py`、`tests/inner_life/test_p5_working_state_context.py`（新增）、`logs/ENGINEERING_STATE.md`（追加條目）。多一檔少一檔都**停止並回報**。
2. 新守衛檔 **15 筆全綠**（P6-T1～P6-T15）＋ 既有 70 筆仍全綠 ⇒ 該檔 **85 筆 collected**。
3. 變異 **M14～M22 全數轉紅**（9 條）；harness 印出 repo 檔 sha256 執行前後不變。
4. **生產等價驗收**：以生產現況（`data/inner_life/` 無 working_state 檔）組 prompt，與未實作版本 **byte-identical**。
5. **沙盒守門未放寬**：`format_working_state_lines(agent_id, <生產 inner_life 路徑>)` 回 `[]`。
6. 0 外部呼叫、0 生產寫入、0 frozen 變更、0 排程接線。
7. **`logs/ENGINEERING_STATE.md` 追加條目（canonical 不覆寫＝**追加**新條目，例：§0.1.63）必須寫明**：
   ① **主大腦對 Phase 4↔Phase 5 互斥的裁示**（Phase 4 的 `format_working_state_lines()` 契約**原封不動**；本票新增 `format_working_state_lines_production()` 路徑不可注入；共享核心 `_read_working_state_lines()` 逐位元搬移以保住 M11／M12／M13 錨點）——**這是契約變更，必須留 canonical 痕跡，不能只存在工單裡**；
   ② `check_store_dir()` 的**三重拒絕**語意（生產根之下／repo `data/` 之下／非 temp 之下）與「守門適用範圍隨呼叫語意重新界定」的理由；
   ③ **本票未接資料面**（`data/inner_life/working_state.jsonl` 仍不存在）⇒「角色已能感知思考狀態」**尚不成立**；
   ④ 守衛 15 筆 ＋ 變異 M14～M22；
   ⑤ 真實 LLM 呼叫 **0 次**、0 外部 API、0 生產寫入。

## 5. 測試
- 定向：`pytest -q tests/inner_life/test_p5_working_state_context.py tests/inner_life/test_tension_closure_loop.py` ⇒ 預期 **85 passed / 0 failed**（15 新 ＋ 70 既有）。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests` ⇒ 預期 **7296 passed / 0 failed**（Phase 4 後 7281 ＋ 本票 15）。🔴 引用數字必須註明解譯器是 **repo 的 `.venv`**。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**（pytest 在零失敗時輸出裡根本沒有 "failed" 字樣——主大腦 2026-10-24 00:35 實測踩過這個坑：曾把「7260 passed / 0 failed」的綠燈誤判成 failed=-1 而中止一整輪）。
- 🔴 測試**不得**啟動伺服器、不得綁 `:8000`／`:8765-8767`、不得對生產發真實請求（`tests/**` 禁用 subprocess／os.spawn／os.system／os.popen；R5 護衛）。
- 🔴 變異 harness 只能改**副本**；repo 檔全程不寫入；收尾必印 repo 檔 sha256 與初始值相同。

## 6. 不做（Out of Scope）
- **不接** `drive_loop_async()`、不接排程器、**不產生** working_state（P5-② 另票，須 Owner 核准真實 LLM 預算）。
- 不改 `src/inner_life/tension_thinking.py`；不改 `check_store_dir()`；不放寬 `format_working_state_lines()` 的沙盒守門。
- 不改 `src/llm/proxy.py` 的既有區塊文案／順序／條件。
- 不動 11 個 frozen_dirs 檔；不改 `src/llm/proxy.py` 與 `src/inner_life/tension_closure_loop.py` 以外的 src 檔。
- 不部署、不重啟、不 commit／push／git add（由主大腦合入時處理）。
- 不打任何真實 LLM 呼叫（本票 **0 成本**）。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/stages.py`／`trigger_handler.py`／`event_handler.py`／`dream_handler.py`／`diary_handler.py`、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。
`src/llm/proxy.py` 與 `src/inner_life/tension_closure_loop.py` **皆非** frozen（兩者都已在 observability 測試的 `allowed` 集合內：proxy.py 第 781 行、tension_closure_loop.py 第 841 行）⇒ 本票可改，但**最小差異**原則適用。

## 8. 回報格式
- 4 檔範圍 ＋ numstat。
- 新守衛 15 筆 ＋ 既有 70 筆的數字；變異 M14～M22 逐條（轉紅的測試名）。
- **生產等價 byte-identity 的比對結果**（雜湊或 bytes 數）。
- **沙盒守門未放寬的證據**（`format_working_state_lines(agent_id, <生產路徑>)` 回 `[]` 的實測）。
- **Phase 4 變異錨點仍可命中的證據**（M11／M12／M13 的錨點仍逐位元存在於新檔案）。
- 明確聲明：**本票未接資料面（working_state 仍不存在）**，因此「角色已能感知思考狀態」**尚不成立**；並列出 P5-② 的待裁示項（真實 LLM 預算）。
- 未做與明文排除。
