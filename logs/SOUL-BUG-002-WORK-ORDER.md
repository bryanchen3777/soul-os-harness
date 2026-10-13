# SOUL-BUG-002 — SAGE 檢索在單一零區辨力 token 上即放行（語意檢索污染）

**工單編號**：SOUL-BUG-002
**嚴重度**：**P1**
**狀態**：APPROVED & OPEN（Bry 核准立案 2026-10-12）｜**測試維持紅燈，嚴禁放水**
**發現方式**：SOUL-TECH-DEBT-001 C 類深度鑑識
**涉及測試**：`tests/test_m5_3_s2_b_normalization.py::test_s2_b_4_negative_zero_false_positive`、`tests/test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_4_negative_no_false_positive`（同一缺陷的兩種回報）

---

## 一、現象

負向查詢（明確不該有記憶命中）卻產生多筆命中。實測證據：

```
query='Python 是 什麼 程式語言'  matched_memory='雷姆 幫不上 什麼'      matched_tokens=['什麼']
query='今天 天氣 怎麼 樣'       matched_memory='Bryan 起得很早 今天'   matched_tokens=['今天']
```

## 二、根因

`src/memory/v1/loader.py`：

1. `:179` 計分時只扣除 `agent_name_stopwords`（其來源 `_PER_AGENT_NAME_STOPWORDS` 定義於 `:333`，
   **僅該 agent 自己的名字**，且只列了 `agent_rem` / `agent_ram` 兩個）。
   **沒有任何通用／疑問詞／時間詞的停用詞集合。**
2. `:350` `MIN_OVERLAP_SHORT_CONTENT = 1` —— **單一 token 重疊即通過門檻**。

⇒ 任何含「今天」「什麼」「怎麼」的查詢，都會命中**任何**帶有這些詞的記憶。

## 三、為何長期未被發現（重要）

此缺陷原先被一個**測試自身的編碼問題**完全掩蓋：`capsys.disabled()` 下 `print` 觸及 `cp950` 主控台，`UnicodeEncodeError` 先行拋出，測試看起來只是「Windows 編碼問題」。SOUL-TECH-DEBT-001 修掉編碼問題後，缺陷才浮現。

`test_s2_a_1` 與 `test_s2_a_5` 在修掉編碼問題後**直接轉綠**——證明那兩個純屬環境雜訊，而 `a_4` 底下藏的是真缺陷。**若當時選擇對整個檔案加 skipif，缺陷將被永久埋入程式碼。**

## 四、資料相依性警告

這 4 筆 SAGE 測試**讀取生產狀態**：`V1Store(Path("data/memory"), "agent_rem")`，目前 357 筆記憶。

缺陷在**機制上**與資料無關（沒有任何語料能讓「今天」變得有區辨力），但**可觀察的命中數量**取決於語料內容。對照組 `test_s2_a_3_confidence_isolation` 正確使用 temp store，負向語料測試則未隔離。

⇒ 修復時應考慮一併把負向語料測試改用 temp store，避免其通過與否隨生產資料漂移。

## 五、修復方向（Bry 2026-10-12 裁示）

1. 補齊**標準 CJK 通用停用詞表**（疑問詞、時間詞、代詞等）。
2. 將高頻無區辨力詞彙**排除於短文本重疊計分之外**（即 `MIN_OVERLAP_SHORT_CONTENT` 不應由單一停用詞滿足）。

## 六、🔴 設計張力 —— Bry 2026-10-13 已裁示：**分級重疊（Tiered Overlap）**

原始問題：加停用詞表會讓「今天」這類詞永遠無法單獨構成證據，
但 Bry 的設計意圖是**記憶應該能引用時間脈絡**。
單純封殺時間詞會殺掉語意權重；單純放行則語意檢索被污染。

### 裁定內容

採用**兩級門檻**——停用詞在「單獨命中」時是噪音，在「與實質內容詞共同命中」時是特徵。

| 級別 | 條件 | 處置 |
|---|---|---|
| **Gate（單一 Token 阻斷）** | Query 的有效匹配 token **僅有**通用停用詞（如只有 `['今天']` 或 `['什麼']`） | **嚴禁放行檢索**，視為 0 區辨力雜訊，該記憶不進 candidate 池 |
| **Boost（多 Token 協同加分）** | Query 已命中**至少一個實質內容詞**（如 `['會議', '今天']`） | 「今天」仍**保留**為時間相依特徵，參與排序評分，提供時間親和度加分 |

### 裁示原話要點

> 如此既殺死了 `query='今天天氣怎樣'` → `'Bryan 起得很早今天'` 的荒謬誤判，
> 又完整保留了時間脈絡的語意權重。

### 實作落點（決策完整，執行者照做）

1. 在 `src/memory/v1/loader.py` 新增**通用 CJK 停用詞表** `_GENERIC_STOPWORDS`
   （疑問詞、時間詞、代詞、虛詞）。
   **注意**：`_PER_AGENT_NAME_STOPWORDS`（`:333`）是 per-agent 專名，**語意不同，不可合併**——
   那是「agent 自己的名字在自己記憶庫裡不具區分力」，本次新增的是「這些詞在任何語料都不具區分力」。
2. 計分處（`:179` 附近）**不要**直接從 `query_components` 移除停用詞，
   必須保留它們並標記為 `degradable`（可降級），因為 Boost 級別還要用。
3. 在 `is_candidate_eligible`（門檻判定處）實作 Gate：
   若 `query_components` 去除 `_GENERIC_STOPWORDS` 後為空集 ⇒ 直接 return 無命中。
4. 在計分處實作 Boost：實質內容詞有命中時，degradable token 仍以較低權重參與。
5. **不可**直接把 `MIN_OVERLAP_SHORT_CONTENT`（`:350`）從 1 調到 2 當作修法——
   那會連帶改變 Bry §25(b) 為 long content 拍板的長文本門檻語意，屬超範圍副作用。

## 七、驗收

1. **Gate 級**（必須 0 命中）：`query='今天 天氣 怎麼 樣'`、`query='Python 是 什麼 程式語言'`
   產生 **0 筆** eligible 記憶。
2. **Boost 級**（必須仍有命中，時間脈絡未死）：`query='會議 今天'` 這類同時含
   實質內容詞與時間詞的查詢，**必須仍能召回**相關記憶，且排序分數高於不含時間詞的同義查詢。
   這一條是防止「修法 A 有效、修法 B 過度」的唯一證據，**不可省略**。
3. 負向語料測試改用 temp store，通過與否不再隨生產資料漂移。
4. `tests/test_m5_3_s2_b_normalization.py` 與 `tests/test_m5_3_s2_retrieval_diagnostic.py` 全綠。
5. 全庫回歸通過。

## 八、看門狗狀態（主大腦復核 2026-10-13）

本 bug **已有守衛**：§開頭列出的兩筆測試
（`test_s2_b_4_negative_zero_false_positive` / `test_s2_a_4_negative_no_false_positive`）
在 SOUL-TECH-DEBT-001 中**未被校準成綠燈**，仍維持紅燈，
證明缺陷發作時 CI 會報警。

⚠️ 但它們**讀取生產狀態**（見 §四）——修復後若轉綠，
應一併把負向語料改為 temp store（驗收第 3 點），否則守衛會隨生產資料漂移而失真。

## 九、主大腦複核紀錄（2026-10-13）

- 工單原稿的路徑 `src/memory/sage/loader.py` **不存在**；實際為 **`src/memory/v1/loader.py`**。
  已於本工單全文更正（主大腦 `glob` + `grep` 雙向確認，`src/**/loader.py` 僅此一檔）。
- `:179` 只扣 `agent_name_stopwords`、`:333` 僅定義 `agent_rem`/`agent_ram`、`:350` `= 1`：
  **已逐行讀取確認**。
- `_PER_AGENT_NAME_STOPWORDS` 僅被 `loader.py:160` 一處使用：**已 grep 確認**。