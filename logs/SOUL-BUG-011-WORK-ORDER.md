# SOUL-BUG-011 工作單（草案 v1，待 Owner 裁決）

> 狀態：**草案**。所有數字均為 2026-10-18 生產資料實測，附量測方法。
> 上一版「Prompt 負向約束 + `CLEAN_FIRST_LINE_HEADER_RE` 首行剝除」已由 Owner 裁決廢棄。

---

## 0. 根因（已於 22:10 收網以 Sidecar 第一手證據定案）

```
src/soul/diary.py:471   if len(clean) > DIARY_MAX_CLEAN_CHARS:
src/soul/diary.py:472       truncated = _safe_truncate_on_length(clean, max_chars=DIARY_MAX_CLEAN_CHARS)
src/llm/proxy.py:2813  「截斷策略: 取 raw 最後 max_chars 字」
```

**上游無罪**：`TRUNCATED-IN-SIDECAR=0`、`DANGLING-ASTERISK-IN-SIDECAR=0`。
上游 LLM 偶發吐日期標題（今晚 2/10：`2026年10月18日（夜）`、`2026-10-18（夜）`），但未被截斷。
**截斷 100% 發生在管線內部。**

---

## 1. 量化基準（實測，非估算）

### 1.1 受影響規模：240 筆日記被卡在 80 字邊界

落盤日記（`source == "llm"`）的 content 長度分佈：

| slot | n | p25 | median | p75 | p95 | max | **落在 78~80** | >80 |
|---|---|---|---|---|---|---|---|---|
| `night` | 712 | 38 | 47 | **80** | 569 | 939 | **123 (17.3%)** | 102 |
| `morning` | 686 | 38 | 45 | **80** | 538 | 946 | **100 (14.6%)** | 87 |
| `dream` | 353 | 45 | 52 | 71 | 479 | 564 | **17 (4.8%)** | 61 |
| `event` | 261 | 22 | 26 | 33 | 486 | 575 | 0 | 51 |

**`night` + `morning` 的 p75 精確等於 80，且 223 筆（15.9%）聚集在 78~80 區間** ——
這是取尾段截斷的指紋。約每 6 筆 llm 日記就有 1 筆開頭被吃掉。
（`>80` 的 189 筆來自不經此上限的寫入路徑，例如 `dream_event.py` 與上限生效前的歷史資料。）

### 1.2 今晚 Sidecar 原始長度（上游真實長度）

```
agent_anna  151  <== 超過 80
agent_akane 105  <== 超過 80
agent_ram    47 / yua 46 / rem 44 / mahiru 43 / mai 37 / aoi 34 / miku 33 / ruka 32
n=10  min=32  median=44  max=151  mean=57.2   >80 者 2/10
```

放寬上限的影響（以此 10 筆為樣本）：

| cap | 今晚仍會被砍 |
|---|---|
| **80（現況）** | **2/10** |
| 120 | 1/10 |
| 160 | 0/10 |
| **200** | **0/10** |

---

## 2. 決定性架構事實：**雙軌分離不需要新 plumbing**

先前裁決要求的「維度一：區分 `finish_reason == "length"` 與 `"stop"`」，
實測後發現 **這個閘門已經存在，且已經是對的**：

- `src/llm/proxy.py:1861-1900`（`OpenAIBackend.complete`）讀取
  `data["choices"][0].get("finish_reason")`，**僅在 `finish_reason == "length"` 時**
  呼叫 `_safe_truncate_on_length(raw, max_chars=200)`。
- 該處註解明寫此閘門的存在理由正是 Bry 2026-08-04 的原話：
  「唯一共同、可靠的訊號其實是 `finish_reason=length`」。

**真正的缺陷是：`diary.py` / `dream_event.py` 在下游對「任何」輸出再無條件砍一刀到 80。**
那不是缺少閘門，是**多了一道沒有信號判斷的閘門**。

`proxy.py:2803` 自陳適用範圍：「只在 `OpenAIBackend.complete` 內 `finish_reason=length` 觸發時呼叫」——
`diary.py` 卻把它搬去用於正常輸出，**改變了它的語意**。

---

## 3. 修法定案

### 3.1 上限值採 **200**，不採自造常數

`200` 不是我們發明的數字 —— 它是 `proxy.py:1900` 為退化循環路徑**已確立的全專案截斷上限**。
把 diary 對齊到同一個值，可讓「退化兜底」與「正常輸出」共用同一條界線，
消除目前 80（diary）與 200（proxy）兩個互相矛盾的常數。

### 3.2 變更點

| # | 檔案:行 | 現況 | 改為 |
|---|---|---|---|
| 1 | `src/soul/diary.py:66` | `DIARY_MAX_CLEAN_CHARS = 80` | `= 200`（對齊 proxy:1900） |
| 2 | `src/soul/diary.py:471-481` | 無條件 `_safe_truncate_on_length(..., 80)` | **改為「保留前段 + 標點優雅收尾」**，不得取尾段 |
| 3 | `src/soul/dream_event.py:67` | `DREAM_EVENT_MAX_CLEAN_CHARS = 80` | `= 200` |
| 4 | `src/soul/dream_event.py` 截斷呼叫 | 同上取尾段 | 同上改前段優雅截斷 |
| 5 | `src/soul/diary.py:100` | `getattr(proxy.config, "model", None)` | `getattr(proxy, "model", None)`（`LLMProxy.model` 見 `proxy.py:3479`） |

**退化路徑不受影響**：`finish_reason == "length"` 的回應在 proxy 層已被截到 200，
diary 層的新上限 200 不會二次觸發。防崩潰兜底完整保留。

### 3.3 優雅截斷的新語意（取代取尾段）

```
若 len(clean) > CAP:
    在前 CAP 字元內找「最後一個句號 / 驚嘆號 / 問號 / 換行」
    切在該處（含標點）
    若前 CAP 內無任何終止標點 → 只切到 CAP（不做語義猜測）
```

保留敘事開頭（語義起點），寧可犧牲結尾而非開頭。

---

## 4. 守衛設計（MUT 必須轉紅）

| 測試 | 斷言 | 對應變異 |
|---|---|---|
| `test_normal_output_never_takes_tail` | 正常輸出（`finish_reason=stop`）超長時，結果**必須以原文開頭 `clean[0]` 起算** | 把截斷函式改回取尾段 → 必紅 |
| `test_truncation_keeps_head_and_punctuation` | 結果以 `。`／`！`／`？`／換行結尾，且長度 ≤ CAP | 改成字元死切 → 必紅 |
| `test_degenerate_length_path_still_truncated` | `finish_reason=length` 的 1913 字「嗯」仍被 proxy 層壓到 ≤200（**這條防「修壞防崩潰兜底」**） | 移除 proxy 閘門 → 必紅 |
| `test_cap_alignment` | `DIARY_MAX_CLEAN_CHARS == DREAM_EVENT_MAX_CLEAN_CHARS == 200`，且與 `proxy.py` 的 200 同源 | 改任一常數 → 必紅 |
| `test_raw_response_model_records_actual_model` | sidecar `model` 欄位非空且等於 `LLMProxy.model` | 還原 `proxy.config.model` → 必紅 |

⚠️ 第三條是**本工單最容易造成的回歸**：只修 diary 不驗 proxy，會讓 1913 字退化循環重新長回來。
變異實驗必須證明它會紅。

---

## 5. 資料面決策（Owner 待裁）

- **240 筆已被吃掉開頭的歷史條目不做回溯**（原始文字物理不可得，與 SOUL-BUG-011 既有 Forward-Only 裁定一致）。
- 需裁：是否要標記這批條目（如同 SOUL-BUG-010 的 5 節點隔離降級），或完全不動。

---

## 6. 不做（Out of Scope）

- 不動 `proxy.py` 的 `finish_reason` 閘門語意本身（那是 Bry 2026-08-04 拍板的正確設計）。
- 不動 `prompt` 文案（「1-2 句, 50 字以內」）。實測 median 44、最長 151，
  prompt 與實際產出量級相符；**矛盾在硬砍不在 prompt**，且放寬上限已讓 2/10 的異常值可完整保留。
- 不回溯歷史資料。
