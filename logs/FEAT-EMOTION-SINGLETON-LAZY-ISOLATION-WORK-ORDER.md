# 工單：FEAT-EMOTION-SINGLETON-LAZY-ISOLATION（測試隔離漏洞：收集期即以生產路徑開啟 sqlite）

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
