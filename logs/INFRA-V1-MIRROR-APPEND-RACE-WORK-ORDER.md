# 工單：INFRA-V1-MIRROR-APPEND-RACE — memory v1 mirror 的併寫整筆遺失（已修）

- **Owner 裁示（2026-10-22 19:3x EDT）**：**【裁決：核准改採 Binary Append，全面收斂為 LF-only】**
  - 決策依據：Step 59.2（Commit `edff462`）世界日誌行界安全重構已拍板「Windows text-mode newline translation 是並發 append 丟失與撕裂（Torn write）的溫床；所有 JSONL／日誌追加一律改採二進位追加 ＋ 單一 LF 結尾」。
- **狀態**：✅ **已修、已驗（雙向變異 2/2 轉紅）、待全庫回歸綠燈後合入。**
- **上游**：2026-10-22 的 P1 票 INFRA-CONCURRENT-APPEND-MUTEX（已修張力承載檔與世界日誌）——**本票是它的第三個承載檔**。
- **量測口徑**：一切數字以 repo 的 `.venv\Scripts\python.exe` 為準；`data/**` 唯讀；本票只寫測試 tmp 目錄。

---

## 1. 缺陷

`src/memory/v1/store.py::V1Store.add()` 原本用 **text-mode** `open(path, "a")` 追加。Windows 上 text mode 帶兩個獨立問題：

1. **newline translation**：`\n` → `\r\n` ⇒ 檔案不是乾淨的 LF-delimited JSONL。
2. 🔴 **併發 append 整筆遺失**（本票核心）：MSVCRT `_O_APPEND` 在每次寫入前用「**該 handle 自己快取的檔案大小**」seek 到 EOF ⇒ 先後開啟的 handle 各自快取到**同一個過期大小** ⇒ 寫到同一偏移 ⇒ **後者整筆覆蓋前者**。

**樣態**：可完整解析、**零損壞行**、寫入全部回傳成功 ⇒ 看起來像「整筆沒寫到」。

## 2. 量測證據

### 2.1 重現（CPU 負載下，4 個自終置 burner）

| 條件 | 結果 |
|---|---|
| **含** LIFECYCLE-REINFORCE-CHAIN 改動 | 12 輪 **6 輪失敗**，`v1_count` = 8/9（而非 10） |
| **撤掉** LIFECYCLE-REINFORCE-CHAIN 改動（adapter 還原到 HEAD，SHA 驗證一致） | 10 輪 **2 輪失敗**，同一斷言 |
| 無負載單跑 | 通過 |

⇒ **既存缺陷，與 REINFORCE-CHAIN 無關**（因果鑑別已做）。

### 2.2 🔴 這推翻了上一輪的範圍前提
P1 票 INFRA-CONCURRENT-APPEND-MUTEX 當時把「約 25 處 text-mode append 點」列為 out-of-scope，理由是「**單寫入者路徑**」。**本測試證明該前提對 `memory/v1/store.py` 不成立**（10 個併發寫入者）。

## 3. 修法（含一項必要的技術修正）

```python
# src/memory/v1/store.py
from src.fsio.atomic_append import append_bytes

def add(self, memory: Memory) -> None:
    payload = (json.dumps(memory.to_dict(), ensure_ascii=False) + "\n").encode("utf-8")
    append_bytes(self.store_file, payload)
```

### 3.1 ⚠️ 為什麼**不**直接用 `open(path, "ab")`（Owner 裁示引用的是 Step 59.2 的 `open(path,"ab")` 寫法）

**「二元」不等於「原子」。** Windows 上 CPython 的 `open(path, "ab")` 走 MSVCRT `_O_APPEND`，CRT 會用「該 handle 自己快取的檔案大小」seek 到 EOF ⇒ **兩個 handle 各自快取到同一個過期大小 ⇒ 後者整筆覆蓋前者**。那正是 INFRA-CONCURRENT-APPEND-MUTEX 票**實測定案**的根因（見 `src/fsio/atomic_append.py` 檔頭的量測表：現況 `open(path,"ab")` 30 輪中 10 輪遺失 33.3%）。

⇒ 用 `open(path,"ab")` 會**符合裁示的字面**（binary ＋ LF-only）但**不會消滅那顆併發地雷**——而裁示的**目的**正是「徹底消滅這個偶發讓回歸翻紅的並發地雷」。

⇒ 實作改用**上一輪已驗證的原子原語** `src/fsio/atomic_append.append_bytes()`：**二元寫入（⇒ LF-only）＋ 行程內互斥 ＋ Windows 核心層 `FILE_APPEND_DATA` 原子追加**（跨行程安全）。**同時滿足裁示的兩個面向。**

### 3.2 變異實驗對這個決策的證明（雙向）

| 變異 | 無負載 | 負載下 | 既有 D2 守衛 | 結論 |
|---|---|---|---|---|
| **M1** 還原 text-mode `open(...,'a')` | **3 failed**（T1 LF-only／T2 結構／T3 併發）**確定性轉紅** | 3 failed | 🔴 紅 | 舊寫法被三條守衛抓到 |
| **M2** 換成 `open(...,'ab')`（**二元但非原子**） | 1 failed（**只有 T2 結構**）；**T1 維持綠** | 1 failed | 🔴 **紅** | 🔴 **「LF-only 守衛不足以證明併發安全」**；且**二元非原子在負載下 D2 仍掉筆** ⇒ 地雷沒熄滅 ⇒ 原子原語是**必要**的，不是繞路 |

⇒ 2/2 變異轉紅；變異後檔案以 sha256 驗證 **byte-identical 還原**。

## 4. 守衛（`tests/memory/test_v1_store_atomic_append.py`，新增 4 項）

| # | 守衛 | 型態 | 舊寫法會不會轉紅 |
|---|---|---|---|
| **T1** | **LF-only 位元組斷言**：檔案不得出現 `\r\n`；每行恰以一個 `\n` 結尾；檔案以 `\n` 結尾 | **確定性** | ✅ 會（Windows text mode 必產 CRLF） |
| **T2** | **結構守衛**（AST）：`V1Store.add` 不得使用 text-mode `open(...,"a"/"at")`，必須走 `append_bytes` | **確定性** | ✅ 會 |
| **T3** | **併發完整性**：8 執行緒 × 25 次 append ⇒ 完整行數 == 200 | 負載敏感型 | ✅ 會（負載下掉筆） |
| **T4** | **可解析性**：每行皆合法 JSON 且欄位完整 ⇒ 不得 torn write | 確定性 | ⚪（舊寫法也過） |

**為什麼要有 T1／T2 這兩條「確定性」守衛**：T3（併發）本身是負載敏感型，無負載時舊寫法也可能綠 ⇒ 只有 T3 會是弱守衛。T1／T2 把「LF-only」與「必須走原子原語」這兩條**不依賴負載**的性質釘死，讓整套守衛有確定性牙齒。

## 5. 效果

- 負載下（4 burner）D2 守衛與新守衛 T3：**0 失敗**（M1/M2 變異下才會紅）。
- 檔案格式：**LF-only**（`\r\n` 歸零）。

## 6. 邊界與未做（明文）

- **只修 `src/memory/v1/store.py`**（有實測併發證據的那 1 處）。
- **同子系統另外兩個 text-mode append 點未動**：`src/memory/v1/log_exporter.py:24`（retrieval log）與 `src/memory/v1/loader.py:355`（loader trace）—— 兩者**尚無併發寫入證據**，且位於熱路徑（原子原語每次寫入含一次 `FlushFileBuffers`）⇒ **需先釐清寫入者模型再動**，不因「全面收斂」四字就一併改寫。⇒ 已列入 §7 待裁示第 2 項。
- **repo 內其餘 21 處 text-mode append 點未動**（同 P1 票的 out-of-scope 範圍）⇒ 待逐點盤點寫入者模型。
- `src/memory/sage/writer.py`（Append-Only 唯一權威）**維持凍結**，未動。
- 未動 `data/**`、未重啟服務、未殺行程、未催熟。

## 7. 待裁示（下一輪）

1. **是否開票把 `src/memory/v1/` 另外兩個 append 點一併收斂**（需先量化它們的寫入者模型與熱路徑成本）。
2. **repo 內其餘 21 處 text-mode append 點的盤點方式**（逐點寫入者模型盤點 vs 一次性全換原子原語）。
