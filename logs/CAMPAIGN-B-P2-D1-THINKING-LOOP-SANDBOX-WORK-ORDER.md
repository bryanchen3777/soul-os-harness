# 工單：CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX（戰役一）

> **開立**：主大腦（2026-10-23 18:5x EDT）
> **依據裁定**：Owner (Bryan) 2026-10-23 18:4x「立即切換至週末主線施工」＋「工程大腦裁定：今晚值守已由排程完全接管」
> **起跑順序裁定（主大腦拍板）**：**先開戰役一**；戰役二（Belief 語意保活）**排在 10/24 夜槽實測簽收之後**，且需 Owner 明確重開「方向 A」。
> **本工單狀態**：決策完整，執行者照做即可，**不需要再做任何設計決策**。

---

## 0. 為什麼是戰役一（起跑順序的依據，不是偏好）

1. **戰役二與剛核准的機制耦合**：戰役二要改「支持邊語意」，而「祖先 `support_propagation` 邊＝0」正是 10/24 預期退役判準的**四條件之一**。若先動支持邊機制，10/24 夜槽的 `weak 1 → 10` 基準就不可信、退役白名單可能失效 ⇒ **兩件事都驗不成**。
2. **戰役一不碰被判準倚賴的機制**：D1 張力評分、衰減門檻（24h／7d）、支持邊語意**一律不動** ⇒ 與 10/24 觀測完全正交。
3. **戰役一的 Phase 1 骨架已存在且已驗證**：`src/inner_life/tension_closure_loop.py` 已有 `ThinkRequest`／`ClosureTrace`／`drive_loop()`／`working_state.jsonl` 承載／**生產-沙盒雙條件守門**（`check_store_dir`）／旗標 `SOUL_OS_TENSION_DRIVE_LOOP`（預設 OFF）⇒ Phase 2 是**接上思考層**，不是從零蓋。
4. **Phase 1 的 docstring 自己定義了 Phase 2 的邊界**：`tension_closure_loop.py` 第 9 行明寫「🔴 **本票不呼叫 LLM**（LLM 呼叫屬 Phase 2）」⇒ 本票即該 Phase 2 的**離線沙盒版**。

---

## 1. 目標

讓 D1（資訊缺口／時間態衰減）張力**真正驅動行為**，而非只被評分：

```
D1 張力（已驗證）
  → 觸發沙盒思考提詞（Thinking Prompt）      ← 本票新增
  → 思考回傳「部分消解」                     ← 本票新增（決定性 Mock，不燒 LLM）
  → 更新 working_state.jsonl（帶新欄位）       ← 本票擴充
```

**100% 離線沙盒**（`store_dir` 必須位於 temp 之下）、**不燒真實 LLM API**、**不碰生產 `data/`**。

---

## 2. 範圍

### 可動（repo 內共 4 個檔案）

| # | 檔案 | 動什麼 |
|---|---|---|
| 1 | `src/inner_life/tension_thinking.py` | **新建**：思考層（提示詞建構 ＋ Thinker 抽象 ＋ 決定性 Mock ＋ Null 對照） |
| 2 | `src/inner_life/tension_closure_loop.py` | **擴充**：注入 thinker、部分消解語意、working_state 新欄位 |
| 3 | `tests/inner_life/test_tension_closure_loop.py` | **擴充**（**不得新建測試檔**，見 §7 禁止事項） |
| 4 | `tests/test_observability_completeness_1.py` | `allowed` 集合**加 1 行**：`"src/inner_life/tension_thinking.py"`（逐票更新，治理慣例；`frozen_dirs` 11 筆**不得**動） |

另：工單本體（`logs/CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX-WORK-ORDER.md`，主大腦已開立，**執行者不要改**）。

### 🔴 動不得

- `src/inner_life/tension_scoring.py`（**衰減門檻 24h／7d、排序、三因子、門檻錨點一律不動**）
- `src/inner_life/drive_profile.py`、`personas/**`、`src/soul/**`、`src/llm/**`、`src/agency/**`、`src/memory/**`、`src/world/**`
- `data/**`（生產狀態，只讀）
- `tests/test_observability_completeness_1.py` 的 `frozen_dirs`（11 筆永久紅線）

---

## 3. 做法（決策已定，執行者照做）

### 3.1 `src/inner_life/tension_thinking.py`（新模組）

**模組級硬性禁令（結構性保證，不靠註解）**：
- **不得** import：`src.llm`（含 `src.llm.proxy`）、`httpx`、`requests`、`socket`、`subprocess`、`urllib`、`asyncio`。
- **不得**做任何網路呼叫、不得讀寫任何檔案（純函式層）。
- ⇒ 由 **P2-T15**（AST 層）把關。

**資料模型**（全部 `@dataclass(frozen=True)`）：

```python
@dataclass(frozen=True)
class ThinkOutcome:
    """一次思考的結果（**純資料**）。

    partial_resolution : float  0.0～1.0（本回合消解的比例）
    remaining_gap_count : int    思考後仍開著的資訊缺口數
    decided : bool               是否已「想完」（缺口全消）
    reason : str                 機器可讀的原因碼
    """
    partial_resolution: float
    remaining_gap_count: int
    decided: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]: ...
```

**提示詞建構**：

```python
def build_thinking_prompt(req: ThinkRequest) -> str:
    """由 ThinkRequest 建構「張力驅動思考」提示詞（**純函式、決定性**）。

    必含：agent_id、drive_code、temporal_state、decay_bucket、
          info_gap_count、unresolved_count，以及 source_rows 的
          **可稽核識別欄位摘要**（每列一行的識別子欄位）。
    🔴 **不得**把 source_rows 原文整段貼入（避免無界膨脹）——
          詳見 P2-T3。
    🔴 **不得**呼叫 LLM、不得讀檔、不得連網。
    """
```
- 提示詞**格式**：固定模板（`ROLE`／`CONTEXT`／`FACTORS`／`GAPS`／`ASK` 五段），**同一輸入必逐位元相同**（P2-T1）。
- `source_rows` 摘要：每列只取**識別用子欄位**（`thread_id`／`created_at`／`gap_kind`／`question` 之類，**取該列實際存在的鍵**；不存在就跳過），每列以單行呈現，最多 **20 列**（超出則截斷並標註 `(+N more)`）。
- 提示詞**不得**出現任何隨機／時間／雜湊成分（否則破壞決定性）。

**Thinker 抽象**：

```python
class Thinker(Protocol):           # 或 ABC；兩者皆可，執行者擇一並在 docstring 說明
    def think(self, req: ThinkRequest, prompt: str) -> ThinkOutcome: ...
```

**決定性 Mock（預設）**：

```python
class DeterministicMockThinker:
    """**不燒 LLM** 的決定性思考器。

    規則（主大腦拍板）：
      info_gap_count <= 0  ⇒ decided=True, partial_resolution=1.0,
                            remaining_gap_count=0, reason="no_open_info_gap"
      info_gap_count  > 0  ⇒ **每次思考只消解 1 個缺口**：
                            partial_resolution = 1/info_gap_count,
                            remaining_gap_count = info_gap_count - 1,
                            decided = (remaining_gap_count == 0),
                            reason = "resolved_one_info_gap"
    """
```
- 🔴 **同一輸入兩次 `think()` 結果必須完全相同**（P2-T7）——不得引入 `id()`／`time()`／`hash()`／物件雜湊等非確定性成分。
- 🔴 這就是「**部分消解**」的具體語意：缺口數 > 1 時**一次只消解一個** ⇒ 張力被分次消解 ⇒ 驅動「持續思考」，而非一步到位。

**Null 對照組**：

```python
class NullThinker:
    """不消解（對照組）：partial_resolution=0.0, remaining=req.info_gap_count,
    decided=False, reason="null_thinker"。"""
```

### 3.2 `src/inner_life/tension_closure_loop.py`（擴充）

**（a）`drive_loop` 簽章擴充**：

```python
def drive_loop(agent_id: str, now: int, store_dir: Any,
               *, thinker: Optional[Thinker] = None) -> Optional[ClosureTrace]:
```
- `thinker` 為 **keyword-only 且預設 `None`** ⇒ 預設時使用 `DeterministicMockThinker()`。
- 🔴 **既有呼叫語意不得被破壞**：若 `drive_loop` 有任何既有生產呼叫者，既有呼叫（不傳 thinker）必須**行為相容**；簽章只允許「新增預設參數」，**不得**改既有參數的位置或意義。
- **旗標與隔離不變**：`feature_enabled()` 預設 `False`（OFF ⇒ 不建立任何檔案、回 `None`）；`check_store_dir()` 的**雙條件**（不是生產 ＋ 位於 temp 之下）**沿用不動**。

**（b）階段 1.5（新增）：張力驅動思考**
1. 既有階段 1 取 top-1 張力 ⇒ 組 `ThinkRequest`（**不動**）。
2. **新增**：`prompt = build_thinking_prompt(req)` ⇒ `outcome = thinker.think(req, prompt)`。
3. 提示詞**只寫進 working_state 的 sha256 指紋**（不落全文，避免無界膨脹）。

**（c）階段 2（改）：思考消解張力 —— 由「完全消解」改為「依 outcome 決定」**
- `outcome.decided == True` ⇒ 事件狀態 `resolved`（與 Phase 1 同形）。
- `outcome.decided == False` ⇒ 事件狀態 **`partially_resolved`**（**新值**）＋ 記錄 `partial_resolution`、`remaining_gap_count`、`reason`。
- 🔴 **沙盒 `epistemic_tensions.jsonl` 仍恰好 append 1 筆**（Phase 1 的 T7 不變式必須成立）。
- 🔴 `epistemic_tensions.jsonl` 仍是 **append-only**（既有行位元不動）。

**（d）階段 3（改）：更新 working state**
- 沙盒 `working_state.jsonl` 仍恰好 **append 1 筆**。
- **既有欄位全部保留**（`agent_id`／`ts`／`resolved_tension_id`／`drive_code`／`drives_touched`／`open_gap_count_after`／`open_contradiction_count_after`）。
- **新增欄位**：`open_gap_count_before`（int）、`partial_resolution`（float）、`remaining_gap_count`（int）、`decided`（bool）、`thinker`（str，類別名）、`think_prompt_sha256`（str，64 hex）。
- 🔴 `working_state.jsonl` **append-only**：既有行的位元**不得**被改寫（P2-T14）。

**（e）`ClosureTrace`** 可新增欄位以承載 outcome（`partial_resolution`／`remaining_gap_count`／`decided`／`think_prompt_sha256`），**既有欄位不得刪除或改名**。

### 3.3 守衛測試（新增於 `tests/inner_life/test_tension_closure_loop.py`）

🔴 **命名前綴 `P2-T`**（Phase 1 已有 `T11`＝「D3 不得回歸」，不得撞名）。既有 Phase 1 測試**全部保留**；若因「部分消解」語意而必須調整既有期望值，**只可調整期望值並在 docstring 註明 Phase 2 語意**，**不得刪除或弱化任何既有斷言**。

| 編號 | 測試 | 斷言要點 |
|---|---|---|
| P2-T1 | 提示詞決定性 | 同一 `ThinkRequest` 兩次 `build_thinking_prompt` **逐位元相同** |
| P2-T2 | 提示詞必含因子 | 含 `agent_id`／`drive_code`／`temporal_state`／`decay_bucket`／`info_gap_count`／`unresolved_count` |
| P2-T3 | 提示詞不膨脹 | **不含** `source_rows` 原文全文（只含識別子欄位摘要） |
| P2-T4 | 提示詞無副作用 | 呼叫前後沙盒目錄檔案數／位元組數**不變**（不讀不寫） |
| P2-T5 | Mock：無缺口 | `info_gap_count=0` ⇒ `decided=True`、`partial_resolution=1.0`、`remaining=0` |
| P2-T6 | Mock：部分消解 | `info_gap_count=3` ⇒ `partial_resolution=1/3`、`remaining=2`、`decided=False` |
| P2-T7 | Mock 決定性 | 同輸入兩次 `think()` 結果**完全相同** |
| P2-T8 | Null 對照 | `NullThinker` ⇒ `partial=0.0`、`decided=False`、`remaining=info_gap_count` |
| P2-T9 | 閉環事件分流 | 注入 mock ⇒ 沙盒 `epistemic_tensions.jsonl` **恰好 +1 筆**，且 `info_gap_count≥2` 時事件為 `partially_resolved`、`=1` 時為 `resolved` |
| P2-T10 | Null 注入 | 注入 `NullThinker` ⇒ 事件為 `partially_resolved` 且 `partial_resolution=0.0` |
| P2-T11 | working state 新欄位 | 沙盒 `working_state.jsonl` **恰好 +1 筆**，且 §3.2(d) 的 6 個新欄位齊全且型別正確 |
| P2-T12 | 沙盒隔離 | 執行前後**生產** `data/inner_life/epistemic_tensions.jsonl` 與 `data/soul/*/life_threads.jsonl` **位元恆等** |
| P2-T13 | 舊行不可改寫 | working_state 既有行的位元在第二次執行後**不變**（append-only） |
| P2-T14 | 旗標預設 OFF | `feature_enabled()` 預設 `False`；OFF 呼叫 `drive_loop` ⇒ 不建立任何檔案、回 `None` |
| P2-T15 | **無 LLM 路徑**（AST） | `tension_thinking.py` 不得 import `src.llm`／`httpx`／`requests`／`socket`／`subprocess`／`urllib`／`asyncio` |
| P2-T16 | **閉環不得接 LLM**（AST） | `tension_closure_loop.py` 不得 import `src.llm.proxy`（延續 Phase 1 §2.3 禁令） |

### 3.4 變異實驗（M1～M7，每條**必須轉紅**）

🔴 變異 harness **必須放 `%TEMP%`**（`%TEMP%\campaign_b_p2_mutations\mutation_harness.py`）——R5：`tests/**` 禁用 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`。
🔴 **必須採用「改副本」模式**（2026-10-23 主大腦實測：in-place 還原會以 `OSError: [Errno 22]` 失敗，會把 repo 檔留在變異狀態）：複製 `src/inner_life/tension_thinking.py` 與 `src/inner_life/tension_closure_loop.py` 到 `%TEMP%` ⇒ 改**副本** ⇒ 用**環境變數覆寫載入點**指向副本跑守衛測試 ⇒ **repo 檔全程不被寫入** ⇒ 收尾印出 repo 檔 sha256 == 初始值。守衛測試的載入點覆寫**必須**只接受 `%TEMP%` 底下的路徑（其餘一律 `RuntimeError` 拒絕），CI／生產永遠載入 repo 的真檔。

| # | 變異 | 應轉紅 |
|---|---|---|
| M1 | `build_thinking_prompt` 漏掉 `info_gap_count` 欄位 | P2-T2 |
| M2 | Mock 改成「一次消解全部缺口」（`partial` 恆 1.0） | P2-T6 |
| M3 | Mock 引入非確定性成分（例：混入 `id()`／`time()`） | P2-T1／P2-T7 |
| M4 | `drive_loop` 忽略 thinker 輸出（恆 `partial_resolution=1.0`、`decided=True`） | P2-T9／P2-T10 |
| M5 | `_working_state_row` 不寫新欄位 | P2-T11 |
| M6 | `tension_thinking.py` 引入 `from src.llm import proxy` | P2-T15 |
| M7 | `_append_jsonl` 改成「全量重寫」而非 append | P2-T13 |

### 3.5 沙盒實測對帳（驗收用，必須貼出實際輸出）

用 `.venv\Scripts\python.exe` 跑**離線沙盒**（`store_dir` 在 `%TEMP%` 之下），報告下列實測值（每個寫分子／分母／單位）：

- 沙盒 `working_state.jsonl` 的筆數（期望 **+1**）與 6 個新欄位的實測值；
- 沙盒 `epistemic_tensions.jsonl` 的筆數（期望 **+1**）與事件狀態值（`resolved` / `partially_resolved`）；
- 提示詞的 sha256（同一輸入兩次須相同）與其行數／字元數；
- **生產** `data/inner_life/epistemic_tensions.jsonl` 與 `data/soul/*/life_threads.jsonl` 執行前後的 sha256 比對（期望 **0 差異**）。

---

## 4. 驗收（完成的定義）

1. 守衛測試 **P2-T1～P2-T16 ＋ 既有 Phase 1 全部全綠**（用 `.venv\Scripts\python.exe`）。
2. 變異 **M1～M7 全部轉紅**，且 **repo 檔案 sha256 與初始值逐位元相同**（harness 印出證據）。
3. 全庫回歸 **0 failed**（`.venv\Scripts\python.exe -m pytest -q tests`；基線 **7207 passed / 0 failed / 24 skipped / 32 subtests**；本票新增 16 筆 ⇒ 預期 **7223 passed / 0 failed**）。
4. `git diff --name-only HEAD` **恰好**等於 §2 的 4 個檔案（工單檔由主大腦另行加入）。
5. `data/**` **零改動**（全量 sha256 清單比對，期望 0 差異）。
6. `frozen_dirs` 11 筆**零觸碰**；`allowed` **只加 1 行**。
7. `src/inner_life/tension_scoring.py` **零改動**（衰減門檻、排序、三因子、錨點一律未動）⇒ `git diff --numstat` 中不得出現該檔。

---

## 5. 測試

```powershell
$repo='C:\Users\bbfcc\.local\bin\soul-os-harness'
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'

# 本票守衛（快速）
& "$repo\.venv\Scripts\python.exe" -m pytest -q "$repo\tests\inner_life\test_tension_closure_loop.py"

# 變異 harness（必須放 %TEMP%，且採「改副本」模式）
& "$repo\.venv\Scripts\python.exe" "$env:TEMP\campaign_b_p2_mutations\mutation_harness.py"

# 全庫回歸（🔴 避開生產槽窗：08:00 EDT 前 15 分～09:30、22:00 前 15 分～23:00）
& "$repo\.venv\Scripts\python.exe" -m pytest -q "$repo\tests"
```

🔴 **測試數字一律以 repo 的 `.venv\Scripts\python.exe` 為準**（PATH 上的全域 pytest 是另一個世界，收集數會對不上）。
🔴 全庫回歸約需 13～15 分鐘；**被槽窗擋住就照實回報並等待**，不得在槽窗內硬跑。

---

## 6. 不做（Out of Scope）

- ❌ **真實 LLM 呼叫**（Phase 3）。本票的思考器是**決定性 Mock**，`tension_thinking.py` 不得有 LLM 路徑。
- ❌ 接入 `src/llm/proxy.py`、`src/inner_life/epistemic_tension_scheduler.py`、任何排程器、任何 handler。
- ❌ 改 `tension_scoring.py`（**衰減門檻 24h／7d 凍結**、排序、三因子、門檻錨點）。
- ❌ 改 `drive_profile.py`、`personas/**`、`src/soul/**`（`motive` 的 frozen 5 欄位契約不得冒用）。
- ❌ 改 `working_state.jsonl`／`epistemic_tensions.jsonl` 的**既有行**（append-only）。
- ❌ 寫入 `data/**`、重啟服務、殺任何行程、清空或繞過 `_ELEVATION_EMITTED`。
- ❌ **戰役二（Belief 語意回憶與保活）**——排在 10/24 夜槽實測簽收之後，且需 Owner 明確重開「方向 A」。
- ❌ **新建 `tests/**` 下的 collectable `.py`**（repo hygiene 守衛要求新測試檔先 `git add`，而 worker 無權 `git add`）⇒ 守衛測試一律擴充既有檔。
- ❌ `git add` / `git commit` / `git push`（由主大腦處理）。
- ❌ 為了讓守衛變綠而放寬判準、刪斷言、或調整門檻。

---

## 7. Frozen Contract 注意

本票**不得**碰觸 `tests/test_observability_completeness_1.py` 的 `frozen_dirs` 11 筆（`src/agency/*`、`src/inner_life/submission_gate.py`、`src/social/*`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py` 等）。

另：**Phase 1 的 at-most-once 語意（`life_thread_orchestrator` 的 `_ELEVATION_EMITTED`）本票只讀不改**——本票動的是「張力如何被思考消解」，不是「接縫如何發射」。

---

## 8. 回報格式

1. **改動檔案清單**（4 檔，逐條列行數增減；%TEMP% 的 harness 也要列）
2. **守衛測試**：P2-T1～P2-T16 ＋ 既有 Phase 1 各打了幾筆、紅幾筆
3. **變異**：M1～M7 逐條 →（轉紅的測試名）／repo 檔 sha256 是否逐位元相同
4. **沙盒實測對帳**：§3.5 的五項實測值（每項寫分子／分母／單位）
5. **全庫回歸**：`.venv` 直譯器、`-q tests`、passed／failed／skipped ＋ 耗時
6. **Frozen 檢查**：`git diff --name-only HEAD` 完整輸出（必須只有 4 檔）、`data/**` sha256 比對結果、`tension_scoring.py` 零改動的自證
7. **踩到的意外行為**（若有）

---

## 9. 執行者須知（2026-10-23 的三個坑，別重蹈）

1. **守衛測試的合成 fixture 必須與生產事件同形**（欄位齊備）。2026-10-23 主大腦實測：新增 fail-closed 前置條件會讓舊 fixture「意外全綠」⇒ 變異失守而無人察覺 ⇒ 你的每個變異都必須**真的轉紅**，ABORT 與 GREEN 同等嚴重。
2. **變異 harness 不得 in-place 改 repo 檔**：2026-10-23 實測還原步驟以 `OSError: [Errno 22]` 失敗，repo 檔殘留在變異狀態（可被誤 commit 的損壞工作樹）⇒ 改**副本**，repo 檔全程只讀。
3. **守衛測試的檔案載入點覆寫必須綁死在 %TEMP% 內**（非 %TEMP% 路徑一律 `RuntimeError` 拒絕），CI／生產永遠載入 repo 的真檔。
