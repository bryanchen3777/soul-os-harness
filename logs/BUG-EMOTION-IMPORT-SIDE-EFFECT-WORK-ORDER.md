# 工單：BUG-EMOTION-IMPORT-SIDE-EFFECT（架構治理：收集期即以生產路徑開啟 sqlite）

> 🔺 **Owner 2026-10-24 17:5x EDT 核定命名**：本票原名 `FEAT-EMOTION-SINGLETON-LAZY-ISOLATION`，
> Owner 裁定改以 **`BUG-EMOTION-IMPORT-SIDE-EFFECT`** 立號，並明定
> **列為後續清理項目、不干擾今晚 22:00 夜槽與隨後的重啟窗口**。
> （⇒ 本票**不得**在 2026-10-25 12:20 EDT 晨槽前被排進任何重啟窗口。）

> 狀態：**待裁示**（主大腦 2026-10-24 於 FEAT-ISOLATED-DATA-ROOT-FACTORY 驗收期間量測發現）
> 來源：`logs/ENGINEERING_STATE.md` §2026-10-24 FEAT-ISOLATED-DATA-ROOT-FACTORY ⑧ 之補 R10

## 目標

消除「repo 的測試套件在**收集期**（conftest 的 autouse fixture 執行**之前**）即以
**生產路徑**開啟 sqlite 連線並寫入生產檔」這個既有隔離漏洞。

## 量測現況（2026-10-24，主大腦實測）

| 項目 | 分子 | 分母 | 單位 | 證據 |
|---|---|---|---|---|
| `data/memory.db` 在回歸窗內 mtime 變動 | 1 | 1 | 檔 | `mtime=2026-10-24 16:37:41`，回歸起 16:27:44；服務同秒**無**日誌 |
| 內容污染：`messages` 疑似測試資料列 | **0** | 24,216 | 列 | 複製 db→temp 沙盒後唯讀查證（不開生產連線） |
| 內容污染：`agent_emotions` 疑似測試資料列 | **0** | 11 | 列 | 同上 |
| 內容污染：`intimacy_decay_ledger` 疑似測試資料列 | **0** | 4 | 列 | 同上 |

⇒ **內容污染＝0**（量測事實）；**但開啟生產 sqlite 這件事確實發生**（機制事實）。

## 根因（讀原始碼確認，HEAD `b8c6f12` 即存在，**非本票引入**）

`src/agent/emotion.py`：

```python
DB_PATH = data_root() / "memory.db"          # ← 模組層級，import 期求值

class EmotionEngine:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)

emotion_engine = EmotionEngine()             # ← **import 期即建立連線**
```

pytest 的執行順序是「**先收集（import 全部測試模組）→ 再跑測試**」。
`tests/conftest.py` 的 autouse fixture（設 `SOUL_OS_DATA_DIR` ＋ `reset_data_root()`）
是在**測試執行期**才生效 ⇒ **收集期 `data_root()` 仍回傳 `<repo>/data`（生產）**
⇒ `emotion_engine` 在收集期就對生產 `data/memory.db` 開了連線。

🔺 **FEAT-ISOLATED-DATA-ROOT-FACTORY 無法閉合這個洞**：該票的 `reset_data_root()`
只在 fixture 執行期重指向常數；`emotion_engine` 這個**已存在的活連線**無法被重寫
（該票已如實揭露此殘餘缺口）。

## 為什麼不能只做「常數改成 lazy」

`src/llm/proxy.py:46` 有 `from src.agent.emotion import emotion_engine`（模組層級）
⇒ 即使把 `emotion_engine` 改成延遲求值，**proxy 的 import 仍會立刻觸發它**
⇒ 必須**同時**處理 proxy 的匯入面。

## 做法（決策已定，執行者照做）

1. `src/agent/emotion.py`：把 `emotion_engine` 改成**延遲建立**——
   以模組層 `__getattr__`（PEP 562）提供，**第一次存取時**才 `EmotionEngine()`。
   🔺 延遲時的 `data_root()` 已由 conftest fixture 指向 tmp ⇒ 指向 tmp，不碰生產。
   🔺 生產行程的語意不變（生產沒有 fixture，第一次存取時 `data_root()` 就是生產根）。
2. `src/llm/proxy.py`：把 `from src.agent.emotion import emotion_engine` 改成
   **延遲取用**（例如在呼叫點 `from src.agent.emotion import emotion_engine` 局部匯入），
   否則 proxy 的模組層匯入會提前觸發 singleton 建立。
3. **不得**改動 `EmotionEngine` 的行為語意、DB schema、`agent_emotions` 欄位、
   `intimacy_decay_ledger` 語意、任何 frozen contract。

## 驗收（完成的定義）

- 新守衛 `tests/infra/test_emotion_singleton_isolation_guard.py`：
  - **收集期不得開啟生產 sqlite**：以「import `src.agent.emotion` 後、`data_root()`
    仍為生產根」的模擬情境，斷言**未**建立 sqlite 連線（用 mock 計數或 monkeypatch
    `sqlite3.connect` 攔截，斷言未被呼叫）。
  - 生產語意不變：延遲建立後，第一次存取 `emotion_engine` 仍回傳可用的
    `EmotionEngine` 實例。
- **雙向變異測試**：每條變異必須**恰好**打紅預期的那一個守衛。
  - V1：把延遲建立改回 import 期建立 ⇒ 守衛轉紅。
  - V2：把 `proxy.py` 的延遲取用改回模組層匯入 ⇒ 守衛轉紅。
- **全庫回歸**（repo 的 `.venv`，乾淨樹）：失敗集合與基線**逐筆相同**。
- **生產零寫入證據**：回歸前後，生產 `data/memory.db` 的 mtime 與 sha256 **不變**
  （以「收集期開啟」的模擬情境獨立驗證，不用整輪回歸當唯一證據）。

## 不做（Out of Scope）

- 不改 `src/memory/store.py` 的 `DB_PATH`（同樣的 import 期綁定，但它沒有 module 級
  singleton ⇒ 由 FEAT-ISOLATED-DATA-ROOT-FACTORY 的常數重指向即可覆蓋）。
- 不改 `src/memory/sage/**`、不碰 11 個 frozen contract 檔。
- 不改任何生產行為語意（除「延遲建立」這一項時序）。
- 不重啟服務、不殺行程、不寫生產 `data/**`、0 真實 LLM、0 網路。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 變異結果（每條變異轉紅哪一個守衛）
- 生產零寫入證據（mtime + sha256 前後對照）
- 是否有踩到 Frozen Contract / 意外行為
"""
---

## 實作後補記（2026-10-27 19:xx EDT，主大腦）

Owner 2026-10-27「第二棒，開工」＝本工單**核准執行**。以下記錄**實際落地內容**、
**相對於原設計的兩項補強**、以及**被既有守衛機制連帶要求同步更新的清單**。
（canonical 為 as-built 記錄：`logs/ENGINEERING_STATE.md` §0.1.98。）

### 補強一：連線本身也必須惰性（否則工單的驗收條件不成立）

原工單只要求「`emotion_engine` 延遲建立」。但 `EmotionEngine.__init__(self, db_path: Path = DB_PATH)`
的**預設參數在 def 時求值** ⇒ 若 `DB_PATH` 仍是模組層級常數，import 期仍會呼叫 `data_root()`；
而 `__init__` 內的 `sqlite3.connect` + `_init_schema()` 也會在**第一次建立引擎**時立刻發生
⇒ 測試模組在 collection 期的 `from src.agent.emotion import emotion_engine`
仍會在收集期開啟 sqlite 連線 ⇒ ⇒ **工單的「收集期不得開啟生產 sqlite」不成立**。

⇒ 實際落地為三層惰性：`DB_PATH`（模組 `__getattr__`）、`emotion_engine`（延遲建立）、
**連線**（`conn` 屬性首次取用時才 connect ＋ 建 schema，並保留 setter）。

### 補強二：模組內部不得以全域名引用 `emotion_engine`（潛在 NameError）

Python 的全域名查找**不會**走模組層的 `__getattr__`（PEP 562 只攔到模組屬性存取）
⇒ 模組內部若直接用全域名 `emotion_engine`，只要沒有人先做過 `module.emotion_engine`，
就會 **NameError**；而該處的 `except Exception` 會把錯誤吞掉
⇒ **親密衰減靜默失效且無人知**。
⇒ 內部一律用新增的 `_engine()`（守衛 **G7** 釘死；變異 **V3** 驗證）。

### 被既有守衛機制連帶要求同步更新的清單

1. `src/paths.py::DERIVED_DATA_ROOT_CONSTANTS`：移除 `src.agent.emotion.DB_PATH`
   （已不再是 import 期資料根常數）⇒ 12→11 筆、6→5 模組。
2. `tests/infra/test_isolated_data_root_guard.py`：「預設參數捕捉點」移除 emotion 一筆
   （`__init__` 預設參數已改為 `None`）⇒ 3→2 筆。
   **連帶效果**：emotion 的資料根改在**首次取用**時解析 ⇒ 自動跟著隔離根走。
3. `tests/test_m7_3_longing.py`（2 處）與 `tests/test_m7_continuity_self_turn.py`（1 處）：
   patch 接縫由 `src.llm.proxy` 模組改為**來源模組** `src.agent.emotion`
   （proxy 改為呼叫點局部匯入後，原接縫失效 ⇒ `AttributeError`）。
4. `tests/test_intimacy_growth_2.py`（2 處）：「旗標 OFF ⇒ 零持久寫入」的基準改為
   **先建立連線再取**（語意不變，斷言未放寬）。
5. `tests/test_observability_completeness_1.py`：`allowed` **逐票更新** → 加 `src/agent/emotion.py`。

### 守衛與變異

G1～G8（`tests/infra/test_emotion_singleton_isolation_guard.py`）；變異 **V1／V2／V3 三條全數精準轉紅**
（harness 在 `%TEMP%` 山影樹執行，**不動 repo**；變異前後 repo 檔案 sha256 不變）。

### 量測混淆的一個實例（值得記）

G1 最初斷言「import 後資料根目錄不存在」⇒ **失敗**。根因：`reset_data_root()` **本身**就會建立
資料根 ⇒ 「目錄存在與否」不是 import 的指標 ⇒ 改為量「sandbox 內有沒有 `memory.db`」
＋「`sqlite3.connect` 呼叫次數」⇒ 才是真正的指標。
