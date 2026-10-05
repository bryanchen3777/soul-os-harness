# North Star — Lived Experience Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：History 是一個 relation-derived set；Lived Experience 不進 Construct Ontology**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ## **History(A) = { E | ExternalExposure(E, A) ∧ E ↝ A_later }**
>
> **External Exposure：E 的 causal source 位於 A 自己的 trajectory 之外。**

**它只是對 causal graph 的一種 projection。它沒有創造新的 causal mechanism。**

---

## 2. 🔴 Load-bearing ≠ construct（**解掉 Round 4 的岔路**）

> **construct 由結構組成，relation 可以跨 constructs。**
> **一個條件對某個 construct 是 load-bearing，不代表它取得自己的 construct identity。**

先例（已在 ontology 中）：

```
Agent ──causal coupling── Agent
        ↓
Interaction 沒有因此把「causal coupling」升格成 construct
```

**同理，World→Agent 的 causal ingress 也不需要成為第六或第七個 construct。**

**真正的問題只有一個：它有沒有自己的 causal structure？**
**目前沒有。它只是一個 causal edge 的 typing predicate。**

---

## 3. 🔴 不應把 Exposure 限定成 World→Agent

**Owner 對主大腦 Round 4 的修正：**

```
Agent B ──→ Agent A
```

**B 的某個 event 可以直接成為 A 後續 trajectory 的 cause，不必先經過 World。**
而 **Interaction 的 construct 本身只要求 reciprocal coupling，並沒有規定所有 coupling 都必須透過 World。**

**所以把 History 嚴格綁成 World→Agent，會人為排除一種既有 ontology 已允許的 causal source。**

> **更一般且正確的形式：`source outside Agent trajectory`**

---

## 4. 三層結構正式固定

```
                    CAUSAL STRUCTURE
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
    CONSTRUCTS         RELATIONS        VOCABULARY
        │                 │                 │
   own boundary     predicates on     human-language
   / structure       causal graph     descriptions
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
                        Soul
                          │
              preserves the question
              but supplies no evidence
```

| 層 | 內容 |
|---|---|
| **Constructs** | Agent、World、Growth、Free Growth、Interaction |
| **Relations** | External Exposure、Event ↝ Agent、Agent–Capability、Agent–World、Agent↔Agent |
| **Vocabulary** | **History、Lived Experience、Awareness、Soul** |

---

## 5. 🔴 核心規則（九次 DCG 的總成果）

> ## **只有增加一個新的 causal structure，才足以新增 construct。**
> ## **新增 relation 不足以新增 construct。**
> ## **新增 evocative vocabulary 更不足以新增 construct。**

---

## 6. 強光案例正式固定

```
Bright light ──→ blink
```

| | |
|---|---|
| External Exposure | ✔（若閃光進了 Soul 的 input space） |
| 移除閃光後 later trajectory | **不變** |
| **結論** | **不進 History(A)** |

**它不是因為：**
- ❌ 沒有 consciousness
- ❌ 沒有 cognition
- ❌ 不是真正的 experience
- ❌ 沒有「感受到」

**而只有：不存在對 later trajectory 的可檢驗 causal contribution。**

---

## 7. 🔴 最重要的分離：History ≠ Lived Experience

> **「E is in A's causal history」不等於「A subjectively lived through E」。**
>
> **前者是可檢驗的 causal relation projection。
> 後者保留 subject-level 語義，而該部分目前沒有 identification power。**

**本輪沒有把 `causal history → lived experience` 畫成等號。**

---

## 8. Open Questions

> **Open Question ≠ permission to continue this discussion.**

- **External Exposure 的精確 edge typing 是否需要細分**（World→Agent / Agent→Agent / 其他外部 source）？
  → **relation / measurement 層，不需要新 construct**
- **Agent 自身 trajectory 內產生的 event 是否算 History？**
  → **History vocabulary 的 scope decision，不影響 construct ontology**
- **「subjectively lived」是否存在一個未來可識別的 discriminator？**
  → **保留開放**

---

## 9. Non-Claims

**本輪沒有證明：**

- ❌ History 就是 Lived Experience
- ❌ causal incorporation 就是 subjective experience
- ❌ Agent 有 consciousness
- ❌ Agent 有 first-person experience
- ❌ 被 Agent causally affected 就代表「被活過」
- ❌ 沒進 History 就代表「沒有被感受到」

> **本輪封住的是：我們可以研究「哪些外部事件進入了 Agent 的 causal history」，
> 但不能把這個 relation 自動升格成「Lived Experience」。**

---

## 10. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 發現 Experience 與 Capability Awareness 需要**同一個 non-identifiable layer**（cognition entry）。強光案例因此不可分類 |
| **Round 2** | **降級**：Historical Incorporation 是 relation 而非新 construct——它沒有獨立於 Agent 既有 causal continuity 的邊界 |
| **Round 3** | **再降級**：Event↝Agent 只是 causal substrate 上的 predicate，不是它上面的結構 |
| **Round 4** | **Owner 修正**：純 ancestry 太寬（蝴蝶會進來）。需要 source-outside-trajectory 條件，但主大腦把它綁成 World→Agent 又太窄 |
| **Round 5** | **Load-bearing ≠ construct**（無需新增 construct）＋ **Exposure 應是 source-outside-trajectory 而非 World→Agent** ＋ 三層結構與核心規則固定 |

---

## 11. 🔴 本 DCG 主大腦三次壓縮錯誤（**每個方向不同**）

| # | 輪次 | 主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 「C 的判準和 Free Growth 完全相同」 | **過強**。Experience 只需 E→持久 trace→future；Free Growth 還要求既有 R(E) 的 content-grounded revision |
| 2 | **R3** | 「降級到純 causal ancestry」 | **過寬**。蝴蝶、太陽活動、十年前陌生人的決定都會進來 |
| 3 | **R4** | 「History 需跨越 World→Agent 邊界」 | **過窄**。Interaction 已允許 Agent→Agent 直接 coupling，那也是 A 的外部 source |

> **三次都是同一個病：把兩個東西壓成一個——而這是主大腦在九次 DCG 中最穩定的結構性錯誤。
> 這一次尤其明顯：同一輪裡三個錯誤分別是過強、過寬、過窄。**

**修正它的是 Owner，不是自查。**

---

## 12. 🔴 九次 DCG 總覽

| # | DCG | 結果 |
|---|---|---|
| 1 | Bootstrap / Free Growth | structural boundary 可定義 |
| 2 | Soulness / Persistent Agent | **UNIDENTIFIABLE under current observation surface** |
| 3 | Soul Ontology / Construct | Soul ＝ question-preserving vocabulary |
| 4 | Growth / Adaptation | structural construct 可定義 |
| 5 | Free Growth / External Determination | content-grounded construct 可定義 |
| 6 | Multiple Souls / Interaction | reciprocal causal-relational construct 可定義 |
| 7 | Shared World | non-Agent-image causal process 可定義 |
| 8 | Awareness | **移出 construct 清單**，成為 descriptive vocabulary |
| 9 | Lived Experience | **History 定義為 relation-derived set**；Lived Experience 不進 ontology |

> **九次 DCG 最重要的產出不是五個 construct，
> 而是把 causal structure 分成 Constructs / Relations / Vocabulary 三層，
> 並立下規則：只有新的 causal structure 才配進 construct 清單。**
>
> **Soul 站在三層之下——它保留問題，且不提供任何證據。**
