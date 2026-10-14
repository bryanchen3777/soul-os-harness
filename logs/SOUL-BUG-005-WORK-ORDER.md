# SOUL-BUG-005 — `timezone_utils.format_localized` 星期索引整組錯一格

**工單編號**：SOUL-BUG-005
**嚴重度**：P1（一行可修，影響所有時間感知輸出）
**狀態**：✅ **APPROVED & CLOSED（`277a8d9`，2026-10-13）**
**發現方式**：SOUL-TECH-DEBT-001 C 類深度鑑識（`tests/test_timezone_fix_v1.py`）
**守衛**：`tests/test_timezone_fix_v1.py::test_format_localized_weekday_matches_calendar`
——**已於 `277a8d9` 轉綠**

> ## ✅ 結案紀錄（2026-10-13）
>
> **修復**：commit `277a8d9`，一行。
> ```diff
> -    weekday = _WEEKDAY_CN[local.weekday()]
> +    weekday = _weekday_cn(local)
> ```
> `src/timezone_utils.py:250`。0 新增符號、0 新增邏輯、0 frozen contract 改動。
>
> **守衛轉綠實測**：
> ```
> $ .venv\Scripts\python.exe -m pytest -q tests/test_timezone_fix_v1.py::test_format_localized_weekday_matches_calendar
> PASSED  1 passed in 0.10s
> ```
>
> **行為實測**（修復後，UTC 輸入）：
> ```
> 2026-08-02（週日）→ "2026-08-02 週日 16:11 America/New_York（下午）"   ← 修復前是「週六」
> 2026-08-03（週一）→ "2026-08-03 週一 16:11 America/New_York（下午）"   ← 修復前是「週日」
> 2026-08-05（週三）→ "2026-08-05 週三 10:00 America/New_York（早上）"
> 2026-10-11（週日）→ "2026-10-11 週日 08:00 America/New_York（早上）"
> 2026-10-12（週一）→ "2026-10-12 週一 08:00 America/New_York（早上）"
> ```
> 探測腳本以 `SOUL_OS_DATA_DIR` 重定向至 temp 目錄執行（遵守 Bry 2026-10-13 新增之探測硬規則）。
>
> **相依測試全綠**：`test_timezone_fix_v1 / v2 / test_time_perception_1_coarse /
> test_ta2_temporal_phenomenology / test_ta1_simulation_day / test_ta1_temporal_orientation`
> → **135 passed in 1.59s**。
>
> **後續**：[SOUL-BUG-004-SINK](SOUL-BUG-004-SINK-WORK-ORDER.md) 匯端專屬工單已備妥，待 Bry 核准。

---

## 一、現象

`src/timezone_utils.py:250`（函式 `format_localized`，`:228-257`）：

```python
local = event_ts.astimezone(get_local_tz(cfg))
weekday = _WEEKDAY_CN[local.weekday()]
```

`:42` 的表是 **Sunday=0** 排列：

```python
_WEEKDAY_CN = ["週日", "週一", "週二", "週三", "週四", "週五", "週六"]
```

而 `datetime.weekday()` 是 **Monday=0**。

⇒ **輸出整組錯一格（早一天）**：

```
2026-08-02（真的是週日）→ "2026-08-02 週六 16:11 America/New_York（下午）"
2026-08-03（真的是週一）→ "2026-08-03 週日 16:11 America/New_York（下午）"
```

日期、時分、時區、時段標籤**全都正確**，**只有星期錯**——所以它躲過了所有「時區有沒有搞錯」的檢查。

## 二、為何是 P1 而不是 P0

- 錯的是**星期名**，不是**日期與時分**。Agent 對「今天／昨天／幾點」的基本感知不受影響。
- 但這是所有時間輸出的**共同源頭**，且 Bry 對時間正確性極度敏感（見 §三），
  錯一格會系統性地讓「我以為是週三、其實是週四」——在跨日界與星期節奏相關的場合造成實際誤判。
- 與 TIME-PERCEPTION-1（Bry 2026-10-03 裁定「精確時間降格但保留 safety anchor」）的設計直接衝突：
  安全錨點本身錨錯了，角色會據此說錯話。

## 三、🔴 這不是新 bug，是**已修過一次又漏改**的 bug（重要）

`src/timezone_utils.py:196-207` 同一個模組裡已經存在完整的修正記錄與防護函式：

```python
# 🔴 索引口徑差異（TIME-PERCEPTION-1，2026-10-03 修正）：
#    `_WEEKDAY_CN` 的順序對應 `strftime("%w")`（**週日=0**），
#    而 `datetime.weekday()` 是 **週一=0**。直接用 `weekday()` 索引會整組錯一格
#    （2026-10-03 實測誤判為「週五」，正確為「週六」；**Bry 2026-10-03 親自抓到**）。
#    正確寫法：用 `%w`（週日=0）索引，**或** weekday()+1 映射（週一=1 … 週日=7）。
#    此處採 `%w`，與既有 `_format_event_timestamp` 的用法一致。
def _weekday_cn(dt: datetime) -> str:
    """回傳中文星期（複用 `_WEEKDAY_CN`，0 第四套口徑）。

    ⚠️ **不得**寫成 `_WEEKDAY_CN[dt.weekday()]` —— 索引起點不同，會整組錯一格。
    """
    return _WEEKDAY_CN[int(dt.strftime("%w"))]
```

⇒ 2026-10-03 那次修正**只加了 `_weekday_cn()` 這條新路徑**，
`docs/TIME-PERCEPTION-PROPOSAL.md:56-60` 也只寫到「新增 `_weekday_cn()` 以 `%w` 索引」——
**既有的 `format_localized` 從未被改動**。

**這正是 Bry 2026-10-03 親手抓到的那個 bug，仍然活著。**

同時注意 `format_localized` 的 docstring（`:235`）自陳「跟 proxy.py `_format_event_timestamp` (L37-71) 邏輯一致」，
而 `:201` 的新註解也說「與既有 `_format_event_timestamp` 的用法一致」。

**主大腦復核補充（2026-10-13）**：這兩處提到的「proxy.py L37-71」**早已不存在**。
`src/llm/proxy.py:34` 現在只是一行 import alias：

```python
from src.timezone_utils import format_localized as _format_event_timestamp
```

⇒ **proxy 裡沒有第二份實作**。`format_localized` 就是全系統唯一的時間戳格式化函式，
proxy 只是換個名字用。

**影響面因此比看起來更大**：每一次角色發話的 system prompt 時間行、
每一個事件的時間標，都出自這一個函式。修好它＝一次修好全部；
不修它＝錯一格的星期會出現在每一句話裡。

⇒ 這也解釋了這個 bug 為何能活到今天：三處文件都宣稱「跟 proxy 一致」，
而讀者會合理推論「那 proxy 應該是對的、這裡只是複本」——**沒有人去核對 alias 指向誰**。

## 四、修復（Bry 2026-10-13 裁示：改為呼叫同模組既有的 `_weekday_cn(local)`）

```diff
--- a/src/timezone_utils.py
+++ b/src/timezone_utils.py
@@ -250
-    weekday = _WEEKDAY_CN[local.weekday()]
+    weekday = _weekday_cn(local)
```

**一行。** 不新增常數、不新增邏輯、不改任何既有函式的行為——
`_weekday_cn` 已在同模組且已通過 Bry 的驗收。

⚠️ 修復者請先讀 `:196-207` 的整段註解再動手。那段文字是本 bug 的**事後驗屍報告**。

## 五、驗收

1. `tests/test_timezone_fix_v1.py` 全綠（其中那支刻意留紅的週期索引測試自然轉綠）。
2. 邊界斷言已存在且必須保持：三個案例 `2026-08-02`（週日）、`2026-08-03`（週一）、
   `2026-08-05`（週三），輸出星期**不可對調**。
3. 全庫 grep `_WEEKDAY_CN\[` 殘留檢查（**主大腦已跑過一次，基準如下**）：

   | 位置 | 性質 | 處置 |
   |---|---|---|
   | `src/timezone_utils.py:205` | docstring 禁令文字 | 保留 |
   | `src/timezone_utils.py:207` | `_weekday_cn` 正解 | 保留 |
   | **`src/timezone_utils.py:250`** | **缺陷本體** | **本次修復** |
   | `tests/test_timezone_fix_v1.py:192` | 測試 docstring 內的缺陷引文 | 隨修復更新 |
   | `tests/test_time_perception_1_coarse.py:74` | 正解斷言 | 保留 |
   | `tests/test_time_perception_1_coarse.py:85` | **刻意記錄錯用結果**（「本斷言刻意記錄錯用的結果」） | **保留** |
   | `docs/TIME-PERCEPTION-PROPOSAL.md:56,60` | 歷史提案文件 | 保留（歷史記錄） |

   ⇒ 修復後 `src/` 內**只應剩 `:207` 這一個真實索引點**，其餘皆為文字。
4. 全庫回歸通過。
5. **不得**順帶修改 `tests/test_time_perception_1_coarse.py:85` 那支「記錄錯用結果」的測試——
   它的價值就是釘死這個坑曾經存在。

## 六、看門狗狀態（主大腦復核結論：守衛已存在）

本 bug **已有紅燈守衛**：`tests/test_timezone_fix_v1.py` 內一支獨立的週期索引測試，
其 docstring 即為本缺陷的完整驗屍報告（`:180-241`），並明確寫著「**故意留紅**」與修法。

它與 Bry 為 001/004 掛的 `xfail(strict=True)` 形式不同——
**這支是「直接留紅」型守衛**，優點是缺陷發作時立刻 FAILED、零偽裝；
代價是它會長期佔用紅燈名額（這正是 Bry 明訂「零未分類紅燈」要治理的對象，
而本筆屬**已分類**缺陷紅燈，不是未分類雜訊）。

⇒ **不需再補 `xfail` 看門狗**。修復本 bug 時，該測試自然轉綠即可。

### ⚠️ 修復後的連帶影響

同一檔案 `:209` 與 `:237` 的斷言訊息內嵌了 `timezone_utils.py:250` 的行號與寫法字串。
若修復方式不是 `→ _weekday_cn(local)`（例如重構掉整個 `format_localized`），
這些字串斷言會跟著紅，須一併更新並**明確記錄調整理由**。

## 七、主大腦複核紀錄（2026-10-13）

- `format_localized` 函式簽章（`:228-231`）與 docstring「跟 proxy.py `_format_event_timestamp` (L37-71) 邏輯一致」（`:235`）：**已讀取確認**。
- `proxy._format_event_timestamp` 只是 `timezone_utils.format_localized` 的 import alias（`proxy.py:34`）：**已讀取確認**，故無第二份實作。
- `:42` 表以 Sunday=0 排列：**已讀取確認**。
- `:250` 使用 `_WEEKDAY_CN[local.weekday()]`：**已讀取確認**。
- `:196-207` 存在 TIME-PERCEPTION-1 的禁令與 `_weekday_cn()`：**已讀取確認**。
- `docs/TIME-PERCEPTION-PROPOSAL.md:56-60` 記載 2026-10-03 修法只新增 helper：**已讀取確認**。
- 全庫 `_WEEKDAY_CN[` 掃描（見 §五-3 表格）：**已執行**，7 處命中全數分類完畢。
- 守衛存在性：`tests/test_timezone_fix_v1.py:180-241` 刻意留紅測試：**已讀取確認**。
