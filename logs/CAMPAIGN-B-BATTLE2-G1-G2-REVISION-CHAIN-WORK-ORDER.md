# 工單：CAMPAIGN-B-BATTLE2-G1-G2——同源修訂鏈路（**lineage_depth ≥ 2 的首次合法誕生**）

> 狀態：**決策完備，可直接派工**（主大腦 2026-10-24 18:0x EDT，經 Owner 核定切片與邊界）
> 依據：Owner 2026-10-24 裁定「切片順序與施工邊界核定」＋ §0.1.72 前置量測六條事實
> 本票**零 LLM 成本、零生產寫入、零重啟**；**不動生產接線**（掛載屬下一片，須另行授權）

---

## 目標

在 Soul OS 的證據驅動迴圈裡，為「同候選維度、但命題已更新」的既有靈魂節點建立**第一條合法的跨版本修訂鏈路**：
既有 soul node（belief／value／trait）在收到**同維度的新成熟證據**時，**不是被 anti-runaway 吃掉**，而是產生一個
**同層後繼節點 N'**（`parent_node_id`＝舊、`lineage_depth`＝舊＋1、`lineage_path`＝舊路徑／新 id），
並讓舊節點的證據邊帶 `valid_until_ts` 留痕、新證據邊掛到 N'。

⇒ **一句話**：讓 `lineage_depth` 第一次在生產語意下**合法地**大於 1，而不是靠偽造邊或放寬判準。

## 範圍（**逐檔指名**）

| 檔案 | 動作 | 說明 |
|---|---|---|
| **新增** `src/inner_life/revision_chain.py` | 新增 | 本票的**全部**邏輯：候選篩選 ＋ F5 信心度防護 ＋ G-2 provenance 承接 ＋ 引擎接線薄層 |
| **新增** `tests/inner_life/test_revision_chain_guard.py` | 新增 | 守衛 **I1～I9**（9 筆） |
| `logs/ENGINEERING_STATE.md` | **追加** | 本票的 canonical 條目（追加式，**不得覆寫**既有條目） |
| **不動** `src/inner_life/elevation_adapter.py` | — | 本片**不改生產接線**（見「不做」）；掛載契約由 **I8** 釘死，掛載時不需重構 |

🔺 為什麼不動 `elevation_adapter.py`：`elevate_matured_patterns()` 是**生產路徑**；
Owner 核定本片「維持 0 LLM 成本、純離線沙盒與測試守衛驗證，未獲授權前不碰生產數據」⇒
掛載（改生產行為）屬**下一片**，須另行授權。本片只把**掛載契約**先釘死。

---

## 做法（決策已定，執行者照做）

### ① 新模組 `src/inner_life/revision_chain.py` 的公開介面（**簽章不可自創**）

```python
def select_revision_targets(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    agent_id: str,
) -> List[RevisionTarget]: ...

def compute_revise_confidence(old_node: Mapping[str, Any]) -> float: ...

def build_revise_provenance(
    old_node: Mapping[str, Any],
    *,
    old_source_ids: Sequence[str],
    new_source_ids: Sequence[str],
) -> Dict[str, Any]: ...

def apply_revision(
    engine: Any,
    target: RevisionTarget,
    *,
    source_type: str = "inner_life_event",
) -> bool: ...
```

`RevisionTarget` 為 `@dataclass(frozen=True)`，欄位固定：
`node_id`／`new_content`／`new_confidence`／`old_source_ids`（tuple）／`new_source_ids`（tuple）／`valence`。

### ② 候選篩選（`select_revision_targets`）——**決定性、可逐步複核**

依序套用，**任一條不成立即不選**：

1. 只看 `agent_id` 相符的節點。
2. **修訂目標**＝`node_type ∈ {belief, value, trait}` 的 soul node。
   🔺 **排除 `essence`**（引擎保守邊界：valence 反轉 ＋ ≥2 新獨立證據 ＋ confidence delta 超 0.3，否則只 reinforce、**不改 lineage**）
   ⇒ 排除後**永不**對 essence 發起 `revise()`。
   🔺 **排除 `pattern`**（`revise()` 對 pattern 會產出「候選 pattern」，**不是靈魂血統** ⇒ depth 不會 +1）。
3. **同族維度**＝`_node_candidate_dimension(node)`（**既有函式**，`elevation_adapter.py:869`）
   ⇒ **不得**自創維度、不得引入權重或門檻。
4. **新成熟證據**＝某顆 `node_type == "pattern"` 且其候選維度命中目標維度，
   且其證據邊的 `(source_id, inner_life_event_id)` **不在**目標節點的既有證據集合內。
   既有證據集合＝由 `edges` 過濾 `e["node_id"] == target_node_id` 取得
   （🔺 呼叫端**持有完整 `edges`**；`_rebuild_engine_for` 只是不把 soul node 的邊載進**引擎**，邊本身仍可得）。
5. **內容確有更新**：`new_content`（＝新 pattern 的 `content`）與目標節點的 `content` **正規化後不等**
   （正規化＝去首尾空白；**不引入相似度門檻、不引入權重**）⇒ 相同內容不得觸發改寫。
6. `new_source_ids`＝該新 pattern 的證據 source_ids（去重、保持出現順序）。
   `old_source_ids`＝目標節點的既有證據 source_ids（去重、保持出現順序）。
7. 輸出**依 `node_id` 排序**（決定性；不得依 dict 迭代序）。

### ③ F5 防護：`compute_revise_confidence` ——**逐位元保留舊信心度**

```python
def compute_revise_confidence(old_node):
    return float(old_node["confidence"])   # 逐位元保留；**不得**回退到樁的 0.5
```

- 🔺 **理由**：`StubElevationLLM.classify()` 恆回傳 `confidence = 0.5`
  ⇒ 若不顯式傳 `new_confidence`，會把**樁的假值寫進真實 belief**。
- 🔺 **本片不引入任何新的信心度公式／加權**（Owner 邊界：「不引入任何新的常數或啟發式權重」）
  ⇒ **修訂不等於信心改變**：修訂只換命題版本，信心度**原封不動**。
  （任何信心度升降語義屬另片裁示。）

### ④ G-2 承接：`build_revise_provenance`

必含（**鍵名沿用引擎既有的 `old_*` 命名慣例**，`engine.py:709-711` 已用 `old_node_id`／`old_content`／`old_confidence`）：

| 鍵 | 內容 |
|---|---|
| `old_node_id` | 目標節點 `node_id` |
| `old_content` | 目標節點 `content` |
| `old_confidence` | 目標節點 `confidence` |
| `old_source_ids` | 目標節點的**全部**既有證據 source_ids（tuple） |
| `new_source_ids` | 本輪新證據的 source_ids（tuple） |

🔺 **為什麼這是「G-2 舊 Source 承接」**：`engine.revise()` 的 `provenance` 會被 `merged` 併入後交給
`self._llm.classify(new_content, merged, prior_for_llm)` ⇒ **舊命題與其證據血統就是「重估」的上下文**。
本片用樁（`StubElevationLLM.classify()` 只讀 `prior_node_type`）⇒ 該上下文**機械上不生效**；
但它必須**先備且被守衛釘死**，因為 G-3 的語意判定會直接讀它。

🔺 **G-2 的機械面由引擎既有行為承擔**（不需本票實作）：
`engine.revise()` 會把舊節點仍有效的證據邊標 `valid_until_ts=now`（**不刪 source_id**），
並為 N' 追加新證據邊 ⇒ 血緣在**邊層**已經留痕。

### ⑤ 引擎接線薄層：`apply_revision`

```python
def apply_revision(engine, target, *, source_type="inner_life_event") -> bool:
    try:
        engine.revise(
            target.node_id,
            target.new_content,
            target.new_confidence,
            valence=target.valence,
            provenance=build_revise_provenance(...),
            source_ids=list(target.new_source_ids),
            source_type=source_type,
        )
        return True
    except ValueError:
        return False      # 🔺 fail-closed：門檻未過 ⇒ 不中斷呼叫端、不重試
```

- 🔺 **只 catch `ValueError`**（引擎的門檻不足會拋 `ValueError`）⇒ 其他例外**不得**被吞掉。
- **不重試**、**不迴圈**、**不呼叫 LLM**。
- **不得**自行呼叫 `engine.record_contradiction()`／`engine.supersede()`／`engine.reinforce()`（**G-3 專屬**）。

### ⑥ 本模組的硬性禁令（AST 守衛）

**不得** import：`src.llm`（含 `src.llm.proxy`）、`httpx`、`requests`、`socket`、`subprocess`、`urllib`、`asyncio`、
**`soul_elevation`**（含 `soul_elevation.engine`）
（與 `src/inner_life/tension_thinking.py` 的 P2-T15 同款禁令；並比既有序號器的
`tests/inner_life/test_contradiction_accumulator_prototype.py` 的 S3 守衛**再嚴一格**）
⇒ **本片 0 LLM 成本是結構性保證，不是註解**。

🔺 **為什麼連 `soul_elevation` 都不能 import**：`engine.revise()` 是**對注入物件的 duck-typed 方法呼叫**
（`apply_revision(engine, target)` 的 `engine: Any`）⇒ **本來就不需要 import 引擎**；
而既有 S3 守衛（`ENGINE_WRITE_CALLS = {"record_contradiction", "supersede"}` ＋ 禁 import `soul_elevation.engine`）
代表 repo 對「引擎寫路徑」的既有嚴格度 ⇒ 本模組**沿用同款嚴格度**（且更嚴：連型別都不引入）。
🔺 `revise` **不在** `ENGINE_WRITE_CALLS` 名單內 ⇒ 呼叫 `engine.revise()` 本身**不違反任何既有守衛**；
本條只是把邊界寫死，避免 worker 誤以為被擋而繞路。

---

## 驗收（完成的定義）

- **守衛 I1～I9 全綠**（9 筆）。
- **雙向變異 M1～M10 全數轉紅**（0 ABORT、0 意外全綠）。
- **全庫回歸**（repo 的 `.venv`、主 repo 權威）：**0 failed**，且 passed 數 ＝ 基線 ＋ 9（新增守衛）。
- **生產零寫入證據**：本票全程**不讀寫** `data/**`（沙盒測試用 `tmp_path`）⇒ 回歸前後生產
  `data/elevation/` 的檔案清單與 sha256 **不變**。
- **0 真實 LLM 呼叫、0 外部 API、0 網路**。

## 測試

### 守衛 I1～I9（`tests/inner_life/test_revision_chain_guard.py`，恰 9 筆）

| # | 守衛 | 斷言（摘要） |
|---|---|---|
| **I1** | 篩選正確性 | 給定「同維度已有 soul node ＋ 新成熟 pattern」⇒ 選出該 soul node；給定「**無**既有 soul node」⇒ **零回傳**（不得憑空造 depth-2） |
| **I2** | **F5 防護** | `compute_revise_confidence(old)` **逐位元**等於 `old["confidence"]`；且當 `old["confidence"] != 0.5` 時結果**不得**是 `0.5` |
| **I3** | **G-2 承接** | `build_revise_provenance()` 必含 `old_node_id`／`old_content`／`old_confidence`／`old_source_ids`（**逐筆**）／`new_source_ids`（**逐筆**） |
| **I4** | 對象限制 | `essence` 與 `pattern` **永不**出現在 `select_revision_targets()` 的結果內（首批排除，保守邊界） |
| **I5** | no-op 邊界 | 無新成熟 pattern、或新 pattern 內容與既有內容**正規化後相同** ⇒ **零回傳**（不製造無意義改寫） |
| **I6** | 引擎接線 fail-closed | 引擎拋 `ValueError` ⇒ `apply_revision()` 回 `False` 且**不中斷**呼叫端；**不得重試**（以計數假引擎斷言呼叫次數 ＝ 1） |
| **I7** | **零 LLM 成本（結構性）** | AST 檢查本模組**不得** import `src.llm`／`httpx`／`requests`／`socket`／`subprocess`／`urllib`／`asyncio`／**`soul_elevation`** |
| **I8** | **掛載契約對齊** | AST 檢查 `elevation_adapter.elevate_matured_patterns()` 函式體內確有 `nodes`／`edges`／`engine` 三個可用名稱 ⇒ **下一片掛載不需重構**；且本模組簽章的輸入型別與之一致 |
| **I9** | **血緣形狀** | 以**確定性假引擎**（記錄呼叫參數）跑完整路徑 ⇒ `revise()` 被呼叫時：`node_id` ＝ 舊節點 id、`new_confidence` ＝ 舊 confidence、`provenance` 含舊 source_ids、`source_ids` 含新 source_ids；且**不得**呼叫 `record_contradiction`／`supersede`／`reinforce` |

#### I9 的假引擎：**沿用 repo 既有樣式，不要重造**

- 既有可參照：**`tests/test_c31_relational_expression.py:441`** 的 `class _FakeEngine:`
  （用 `SimpleNamespace` ＋ 明確方法模擬被注入物件）⇒ 本票的假引擎**同款**即可。
- 假引擎只需**記錄** `revise()` 的呼叫參數（`node_id`／`new_content`／`new_confidence`／
  `valence`／`provenance`／`source_ids`／`source_type`）與呼叫次數 ⇒ 供 I6（呼叫次數 ＝ 1）
  與 I9（參數形狀）斷言使用。
- 🔺 假引擎**不得**真的 import `soul_elevation`（I7 同款禁令）⇒ 用 `SimpleNamespace` 模擬即可。

### 變異 M1～M10（**每條必須恰好打紅預期的那一個守衛**）

| # | 變異 | 預期轉紅 |
|---|---|---|
| M1 | 篩選器移除「該維度已有 soul node」條件 | I1 |
| M2 | `compute_revise_confidence` 改成回傳 `0.5` | I2 |
| M3 | `build_revise_provenance` 丟掉 `old_source_ids` | I3 |
| M4 | 篩選器允許 `essence` 進入目標集合 | I4 |
| M5 | 移除「內容正規化後須不等」的條件 | I5 |
| M6 | `apply_revision` 不 catch `ValueError` | I6 |
| M7 | 本模組 `import src.llm.proxy` | I7 |
| M8 | I8 的 AST 檢查被放寬（移除 `engine` 名稱檢查） | I8 |
| M9 | `apply_revision` 傳給 `revise()` 的 `node_id` 改成新 id（自我引用） | I9 |
| M10 | 本模組 `from soul_elevation.engine import ElevationNode`（引入引擎型別） | I7 |

> 🔺 **M10 的必要性**：`revise` 不在既有 `ENGINE_WRITE_CALLS` 名單內 ⇒ 只靠既有守衛**抓不到**
> 「本模組開始 import 引擎型別」這個退化 ⇒ 必須由本票的 I7 自己釘死。

### 變異 harness 紀律

- harness 放 `%TEMP%`；採「**改副本**」模式：**repo 檔全程不寫入**；收尾以 sha256 逐檔比對證明。
- **baseline 必須是「整個守衛檔全綠」**才允許做變異。
- **ABORT 與 GREEN 同等嚴重**：任何一條變異**沒有轉紅**⇒ **ABORT**（不重試、不加裝未經驗證的網路封鎖），回報主大腦。

---

## 不做（Out of Scope）

- **不**修改 `src/inner_life/elevation_adapter.py`（**掛載屬下一片**，須另行授權；本片只以 I8 釘死契約）。
- **不**修改 `src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、
  `src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`、`src/agency/**`
  （**frozen 紅線 11 檔**）。
- **不**修改外部套件 `C:\Users\bbfcc\.local\bin\soul-elevation`（repo 外）。
  🔺 其中 `LIFECYCLE_N_SUPERSEDE=3`／`LIFECYCLE_ESSENCE_N_SUPERSEDE=5`／
  `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD=2`／`LIFECYCLE_ESSENCE_CONFIDENCE_DELTA=0.3` **一字不動**。
- **不**放寬任何 lineage 判準、**不**偽造矛盾邊、**不**調整核心門檻（Owner 凍結條款）。
- **不**實作 G-3（`record_contradiction`／`supersede`／語意判定）：**G-3 屬另片**，
  且其契約見 `logs/CAMPAIGN-B-BATTLE2-G3-SEMANTIC-JUDGMENT-CONTRACT.md`。
- **不**引入任何新的常數、權重、門檻、相似度度量。
- **不**新增排程器／計時器（Owner 邊界：入口掛載於既有 `elevate_matured_patterns()` 迴圈）。
- **不**寫 `data/**`、不重啟服務、不殺行程、0 真實 LLM 呼叫、0 網路。
- **不得** `git add .`；**不得** commit／push（由主大腦合入）。

## Frozen Contract 注意

- `src/agency/**`（5 檔）、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、
  `src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`
  ⇒ **11 檔永久紅線**，本票一個字都不能碰。
- `soul-elevation` 套件（repo 外）的四個 `LIFECYCLE_*` 常數與 `revise()`／`record_contradiction()`／
  `supersede()` 的門檻語意 ⇒ **不得**以任何方式放寬。
- `StubElevationLLM` 的 `confidence = 0.5` ⇒ **不得**讓它進入生產資料（F5 防護）。

## 回報格式

- 改動檔案清單（逐檔 numstat）。
- 守衛 I1～I9 逐筆結果（筆數／分母／單位）。
- 變異 M1～M10 **逐條**（轉紅的是哪一個守衛；有 ABORT 必須明寫）。
- 全庫回歸數字（passed／failed／skipped／耗時／returncode）＋ 解譯器＝repo 的 `.venv`。
- 生產零寫入證據（`data/elevation/` 檔案清單與 sha256 前後對照）。
- repo 檔 sha256 前後不變的證明。
- **是否有踩到 Frozen Contract／意外行為**。
