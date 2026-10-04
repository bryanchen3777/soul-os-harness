# TA-2 v2 — 收尾裁定：A-v2 Local-Attachment Measurement 路線 STOP

> **裁定者**：Owner（Bryan），2026-10-04 12:15
> **本檔為 canonical 狀態記錄。**取代此前任何「GO E」「A survives」「A failed」的表述。
> **Gate 2**：**INCONCLUSIVE**（與本裁定無關，全程未變）

---

## §1 正式狀態

| 項目 | 狀態 |
|---|---|
| **TA-2 Gate 2** | **INCONCLUSIVE** |
| **A-v2 local-attachment measurement** | **STOPPED** |
| **Candidate A hypothesis** | **OPEN** |
| **Candidate E** | **RECOMMENDED, NOT AUTHORIZED** |
| **A/E threshold** | **OWNER DECISION REQUIRED** |

---

## §2 🔴 被否定的是什麼，尚未被否定的是什麼（架構邊界）

### 已被否定

> **A = 用 rule-based local attachment extractor 測量 event temporal attribution**
> 這個 **measurement implementation path**。

### 尚未被否定

> **A 的 underlying hypothesis**：
> temporal context 能否改變 Soul 對 event 的 temporal interpretation / attribution？

**這個問題仍然完全開放。** 量測儀器壞掉**不構成**對被測構念的否證。

### 必須保留的既有 evidence

N=6 真實 LLM 四臂結果仍然有效且值得保留：

```
ΔR = 1   ΔI = 0   DiD = 1
```

吃飯／睡覺的 correct vs mismatched context **確實產生了不同的 temporal framing**。

> 🔴 **不得**因為 measurement instrument 失效，就連帶把
> 「temporal context affects interpretation」一起判死刑。

---

## §3 證據（兩位盲式標註者，Rule 5 鎖定後最終重跑）

| 層 | 結果 | 判讀 |
|---|---|---|
| Stage 1 agreement | 23/25 = **92.0%** | defensibility 基礎仍有少量歧義 |
| Stage 3 raw agreement | 14/21 = 66.7% | 不高 |
| Stage 3 partition agreement | 15/21 = **71.4%** | **表面過 validity gate** |
| 🔴 `none` 真正獨立判斷 | **0/6 = 0.0%** | **關鍵證據** |

### 71.4% 是 **false pass**

**不是因為 gate 設錯，而是因為：**

> **規則強制一致 ≠ 人類語義判斷一致。**

15 筆一致**全部**是「至少有一位標註者判定為 `rule_constrained`」的項目。
6 筆真正需要獨立判斷的，**兩位完全沒有任何一筆一致（0/6）**。

`71.4%` 是「100% 與 0% 的混合」，剛好擠過門檻。

---

## §4 真正的 bottleneck（不是 R3，也不是 R1）

> **中文的「local temporal attachment」本身，不是一個可以靠這套有限規則穩定裁決的 surface property。**

卡住的問題已經不是 `token → rule → label` 能否解決的事：

- 「**夜深了**」是 temporal **predicate**，還是 temporal **state / adverbial expression**？
- 「**熬到現在**」是不是**獨立 predicate**？
- 「**凌晨快四點的飯，算宵夜**」的「飯」是 **event-side expression**，還是 **nominalized event**？

### 為什麼不再加規則

若繼續走「disagreement → 新規則 → agreement ↑ → 新 disagreement → 新規則」，
最終確實會得到一個非常漂亮的 agreement。

**但那會變成 TA-2 corpus-specific classifier，而不是 measurement instrument。**

兩位標註者**都遵守了**「發現新 ambiguity 就停、不加 Rule 6」的最後護欄
（分別獨立找出 **6 個** 與 **8 個** 實質語義歧義並主動揭露）。
**這反而讓結果非常乾淨。**

> **禁止開 Rule 6。本路線在 measurement 層面結束。**

---

## §5 R3 的實際命運（記錄，不補救）

**R3 在整個 corpus 上一次都沒命中（0/25）。**

五筆 siesta 全部同時含 event-side activity（`睡`／`補眠`）與真時段詞（`中午`），
**R3 的明文護欄每一筆都把它擋掉。**

> 護欄成功防止誤殺真陽性，代价是它**從未生效**。
> **記錄，不補救。**

---

## §6 候選 E：RECOMMENDED, NOT AUTHORIZED

E 的優勢正是繞開本輪的瓶頸：

> E **不要求 measurement layer 自己理解**「這句中文是不是 local temporal attachment」。
> 而是讓 LLM 在受控條件下直接回答：
> **同一個 stimulus，在不同 temporal context 下，是否產生不同的 event temporal interpretation？**

measurement layer 只負責：condition randomization／opaque labels／arm isolation／
response capture／independent scoring／effect calculation／confidence / error bars。

這比讓 regex 判斷「夜深了到底是不是 clause」乾淨很多。

### 但 E 需要新的 measurement threshold

而以下門檻**已全部封死**：

```
0.63   （12/19 的舊母體產物，已作廢）
0.50 / 0.55 / 0.60   （禁止自創）
```

> 沒有 Owner 授權，**不得**把新的 statistical threshold 變成 engineering contract。
> 主大腦**不得**自行生出一個「合理看起來」的數字。

---

## §7 設計教訓（推廣用，已記入主大腦記憶）

> **`partition agreement ≥ 70%` 這類 validity gate，若樣本中混有 deterministic rule 決定的項目，會系統性高估量測品質。**

任何 agreement gate **必須先按 `rule_constrained` 與 `none` 分層計算**，否則門檻失去鑑別力。

**未來所有 rule-based measurement 一律雙層報告：**

```
overall agreement      ＋    independent-subset agreement
```

---

## §8 Production / 完整性

- **0 實驗 LLM calls**、0 network、0 replay、0 corpus mutation
- frozen v1 sha256 未變：`bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253`
- A1／A2 實作未動、corpus 未動、`src/**` 未動
- 0 kill、0 restart、`:8000` 無寫入
- A1／A2／A3 的所有負面基線**全部保留未刪**

---

## §9 誠實邊界

- 本輪**不是**乾淨的 pre-registration：語料已被看過。所有門檻與規則皆為
  **prospective decision rule**，不是盲式預註冊。
- 本裁定**不**宣稱 Candidate A 的構念為假。
- 本裁定**不**宣稱 Soul 沒有 temporal effect。
- 本裁定**不**授權 Candidate E，也不選擇任何 A/E threshold。
- **Gate 2 全程維持 INCONCLUSIVE。**
