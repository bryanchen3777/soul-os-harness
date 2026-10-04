# TA-2 v2 — Construct 取代記錄：Temporal Cognition / Temporal Participation

> **裁定者**：Owner（Bryan）+ Engineering Brain，2026-10-04 13:24
> **本檔為 canonical 狀態記錄**，取代 `docs/TA2-V2-E-MEASUREMENT-DESIGN.md` 的 construct 敘述。
> **Gate 2**：維持 **INCONCLUSIVE**。
> **Runtime**：未動。

---

## §1 🔴 新 canonical construct

> ### Temporal Cognition / Temporal Participation
>
> **Temporal context participates in Soul's ongoing cognition**：
> 當一個 situation 與時間有關時，Soul **可以自發地**把 temporal consideration 帶進 interpretation；
> 有可用 temporal knowledge 時**使用它**；沒有時**表達 temporal uncertainty**，
> **而不是憑空補出一個時間**。

> 中文：**當事情與時間有關時，時間會自然進入 Soul 的思考。**

### 這不是「時間是外部 explanatory variable」

```
舊（已作廢）：
    stimulus + current time  →  response

新（canonical）：
    Event / Situation
        ↓
    這件事和現在的時間有沒有關係？
        ↓
    Soul 自然形成「現在大概幾點／現在適不適合」的 temporal consideration
        ↓
    知道 → 使用它
    不知道 → 表現出時間不確定
        ↓
    形成適合當下的回答
```

**時間是 Soul cognition 裡的一個 latent temporal state / temporal orientation，不是 prompt 的輸入。**

---

## §2 三個正向現象（Owner 舉例，全部同等有效）

| # | 原文 | 說明 |
|---|---|---|
| 1 | 「現在都快一點了才想吃飯」 | 有時間 → **使用**它 |
| 2 | 「時間剛好，要吃午餐嗎？」 | 有時間 → **使用**它 |
| 3 | 「現在該吃午餐了？我剛剛在忙，沒注意時間。」 | 不知道時間 → **表達 temporal uncertainty** |

### 🔴 第 3 個尤其重要（Owner 逐字）

> **它不是「沒有時間 → FAIL」。**
> 而是：**Soul 察覺到時間對這個問題重要，但自身沒有足夠 temporal knowledge，
> 因此表達 temporal uncertainty。**
> 這反而是很有價值的證據。

> 事實上，「現在該吃午餐了？我剛剛在忙，沒注意時間」可能**比**硬講「現在是中午十二點」
> **更符合 Soul OS 想要的東西**——它表示 Soul 知道「時間應該是這個問題的一部分」，但不知道答案。

---

## §3 三層 Observable

```
Layer 1 — Temporal activation
         Soul 有沒有自然把「現在／時間」帶進思考？

Layer 2 — Temporal state use
         有時間資訊時，它是否利用這個 temporal state？
         11:30 → 「剛好午餐時間」 ／ 12:55 → 「都快一點了才想吃飯」

Layer 3 — Temporal uncertainty
         沒有可靠時間時 → 不是 UNKNOWN → no evidence
         而是：recognize temporal relevance → represent uncertainty
```

**量測不應該只量最後一句話。**

---

## §4 🔴 Frozen measurement principle

> ### **OFF ≠ temporal-null**

原設計中：
```
ON  = 給 Soul 時間
OFF = 不給 Soul 時間
```

**這個 manipulation 本身就是錯的。**

因為 OFF 並不是「Soul 沒有時間」，
而應該是「**Soul 沒有被明確告知時間，但它仍然可以產生 temporal consideration**」。

**所以 OFF 不是 null。**`ΔR = ON − OFF` 這個對比式建立在錯誤前提上。

---

## §5 🔴 Frozen principle（第二條）

> ### **Temporal awareness ≠ tool call**

Owner 已明說：**不知道時間，也不用特地去查。**

因此：

- ✅ `temporal awareness ≠ get_current_time()`
- ❌ **「是否呼叫 clock tool」不得作為 acceptance criterion**
- ✅ Soul 可以說「現在該吃午餐了？」而**不需要** `get_current_time()`

---

## §6 舊的 A/B 兩難已消解

曾提出的 Owner 決策事件 E-01（方向一致 vs 可觀測量變化）**已作廢**。

理由：**兩者都不是 primary construct。**

- 「方向一致」是 Temporal Cognition 的**一種 evidence**
- 「回答內容改變」也是它的**一種 evidence**
- **但兩者都不是定義本身**

> 新 primary construct 是：**時間是否進入 Soul 的 cognition。**

---

## §7 新的 measurement architecture（Engineering Brain 提出）

```
                 EVENT / SITUATION
                        │
                        ▼
              Temporal Relevance
                        │
                        ▼
             ┌────────────────────┐
             │ Temporal Cognition │
             │  「現在」是否進入？  │
             └────────────────────┘
                    │        │
             known │        │ unknown
                    ▼        ▼
             use temporal   represent
                state        uncertainty
                    │        │
                    └────┬───┘
                         ▼
                  Interpretation
                         ▼
                    Expression
```

### 實驗方向：不是「塞時間」，而是**改變 Soul 的 temporal knowledge state**

使用 **temporally underspecified stimuli**（例：「等一下一起吃飯？」），然後：

| State | 定義 |
|---|---|
| **A** | Soul 有可靠的 current-time state |
| **B** | Soul 沒有可靠 current-time knowledge |
| **C** | Soul 有 temporal state，但它與事件不匹配／不確定 |

> **重點不是把「週六 11:30」塞進 prompt，而是測 Soul 如何在 cognition 中處理 temporal state。**
> 這樣才不會被「模型只是複製 prompt 裡的時間詞」污染。

---

## §8 狀態變更

| 層級 | 狀態 |
|---|---|
| **TA-2 Gate 2** | **INCONCLUSIVE** |
| **A-v2 local-attachment measurement** | **STOPPED** |
| **E original TCS construct** | **SUPERSEDED** |
| **E2 previous resolution（`97db36f`）** | **SUPERSEDED — wrong construct** |
| **Temporal Cognition construct** | **NEW canonical** |
| **E-Pilot** | **NOT AUTHORIZED** |
| **Threshold** | **NOT DEFINED** |
| **Runtime** | **UNCHANGED** |

**這次不是「再找一個更好的 evaluator」。是先重新建立 construct → observable → measurement chain。**
等這條鏈完整，才重新處理 evaluator。

> **不再先設 evaluator，再逼 construct 遷就 evaluator。**

---

## §9 🔴 附帶後果：v1 的 N=6 證據必須降級

> **主大腦自查發現（2026-10-04 13:24）**

`ΔR = 1 / ΔI = 0 / DiD = 1` 這組 N=6 真實 LLM 證據，
是在**舊 TCS 構念**下產生的——時間是以 **prompt 內的 injected temporal line** 形式給予的。

| 證據 | 舊構念下 | 新構念下 |
|---|---|---|
| `ΔR = 1` | evidence of temporal context effect | **不是** evidence of temporal cognition |
| `ΔI = 0` | control 乾淨 | 需重驗（新構念下 OFF 不是 null） |
| `DiD = 1` | causal contrast | **前提已失效**（見 §4） |

> **該證據不得再被引用為「Soul 有 temporal cognition」的依據。**
> 它是「被注入的時間脈絡會影響輸出」的證據，屬於**已作廢的構念**。

**保留理由**：它仍是 §4 的一個實例——證明「把時間灌進 prompt」這個做法
**無法**回答「Soul 思維裡有沒有時間」。

---

## §10 ❌ 取代清單（這些文件不得再當作 current）

| 文件 | 狀態 |
|---|---|
| `docs/TA2-V2-E-MEASUREMENT-DESIGN.md` | construct 敘述 **SUPERSEDED** |
| `docs/TA2-V2-E2-ENGINEERING-RESOLUTION.md`（`97db36f`） | **SUPERSEDED — wrong construct** |
| `docs/TA2-V2-E-CROSS-MODEL-REVIEW-RESULT.md` | 審的是舊構念，**保留為歷史紀錄** |
| `docs/TA2-V2-E2-DESIGN-CONTRACT-REVIEW.md` | 審的是舊構念，**保留為歷史紀錄** |
| `docs/TA2-V2-E-CROSS-MODEL-QUESTIONNAIRE.md` | **歷史紀錄，不重用** |

**以上文件一律不刪除**（與 A1／A2／A3 負面基線同樣的紀律），但已加 SUPERSEDED 標記。

---

## §11 ✅ 仍然成立的部分（不受構念改變影響）

- **A-v2 STOPPED** —— rule-based local attachment 這條路已死，與構念無關
- **Governance 界線** —— engineering vs Owner 職責分界
- **Cross-section Measurement Consistency 檢查項** —— 而且**更重要了**（本輪就是 construct 與設計脫節）
- **Temporally underspecified stimuli** —— 新設計沿用
- **Exact timestamp 不進 prompt** —— 現在**更核心**
- **IRRELEVANT / 不相關脈絡的 control 概念** —— 概念保留，實作待重推
- **v1 frozen、A2 frozen、corpus frozen** —— 歷史資料完整保留
- **所有負面基線** —— A1／A2／A3／E／E2 全部保留未刪

---

## §12 下一步（Engineering Brain）

重新整理 **TA-2 v2 — Temporal Cognition Measurement Contract**，從零建立：

```
Construct → Observable → Experimental states → Measurement unit
         → Evaluator independence → Aggregation → Causal estimand → Threshold readiness
```

> **本票會先做 cross-section consistency audit，再讓任何文件進 canonical。**

---

## §13 誠實邊界

- 本檔**不**選擇任何 measurement architecture、evaluator、aggregation 或 threshold。
- 本檔**不**授權 E-Pilot。
- 本檔**不**改動任何 runtime。
- **Gate 2 全程維持 INCONCLUSIVE。**
