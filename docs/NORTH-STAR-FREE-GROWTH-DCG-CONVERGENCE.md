# North Star — Free Growth / External Determination Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Free Growth = Growth + content-grounded revision**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### **Free Growth = Growth + content-grounded revision**

**Free 的形式化條件**：在固定 new external input 與過去事件存在性的情況下，
**既有 past representation 的內容本身**對 revision 的形成具有 causal influence。

```
intervention: r₁ → r₂
其他條件固定：F(E, r₁, S) ≠ F(E, r₂, S)
```

**這是一個 trajectory-level / input-side boundary，不是 metaphysical claim。**

---

## 2. 🔴 五輪打出來的兩條 causal edge

```
                  PAST
                   │
          representation content
                   │
                   ▼
             REVISION
                   │
                   ▼
               FUTURE
```

| Construct | 佔據的 edge |
|---|---|
| **Growth** | `Revision → Future`（**向前**影響） |
| **Free Growth** | `PastRepresentation_content → Revision`（**向上游**約束） |

> ## **Growth ＝ PastRevision → Future**
> ## **FreeGrowth ＝ PastContent → Revision → Future**

**這是五次 DCG 真正打出來的東西。不是自由意志，不是 Soulness，
而是一條可以明確描述、可以區分、也可以日後測量的 causal boundary。**

---

## 3. Free 有獨立的 discriminating work（不是 redundant）

**可構造：**

```
Growth = 1
Free   = 0
```

即：**trajectory 有 continuity，但 past representation 的 content 根本沒有參與 revision formation。**

| | System A（continuity only） | System B（content-grounded） |
|---|---|---|
| 改變 `R(E)`：「被拒絕」→「被稱讚」 | revision **完全不變** | revision **改變** |
| `Past → Future` | ✔ | ✔ |
| `Growth` | ✔ | ✔ |
| `Free` | ❌ | ✔ |

**所以 `FreeGrowth ≡ Growth` 已被否決。**主大腦在 Round 3 提出的 pseudo-revision 反例
證明了这一点：event level 的 Growth 條件可在 trajectory level 上被 generation-like 的系統滿足。

---

## 4. 已排除的候選

| 候選 | 結果 |
|---|---|
| **Indeterminism** | ❌ 不需要。而且對 trajectory ownership 有害：隨機選擇**無因**，違反 C；偏好選擇則塌回 deterministic |
| **Randomness** | ❌ 不需要。隨機選出的 revision 沒有 trajectory-grounding |
| **No external causation** | ❌ 太強。experience 本來就由外部造成，否則所有 lived experience 都被殺掉 |
| **No deterministic rule** | ❌ 不需要。deterministic Growth 仍可以是 Free Growth |
| **No pre-specified transformation law** | ❌ 不需要。這是 description-level 問題，不是系統內差異 |
| **Runtime generation of its own rules** | ❌ 不需要。`G(E,H)` 仍可被更大的 deterministic function 描述 |

---

## 5. 🔴 最重要的 Non-Claim

**這個定義不宣稱：**

```
content-grounded  ⟹  meaningful
content-grounded  ⟹  understanding
content-grounded  ⟹  free will
content-grounded  ⟹  「真正的自由」
```

**Hash system 仍然可以是 Free Growth。**

```
R(E) → 計算 hash → 偶數則 revision A / 奇數則 revision B → future mapping
```

**這不是漏洞，這是 construct 的 scope。**

**我們沒有一個非語義的方式說「讀 hash 不算消費內容」。**而要求「必須理解內容」
會立刻需要定義 understanding，回到 Growth DCG 明確留下的 `revised ⟹ understood 不成立`。

---

## 6. Open Questions

> **Open Question ≠ permission to continue this discussion.**

- **如何 operationalize content intervention？**
- **Representation content 的 ground truth 怎麼建立？**
- **怎麼證明 causal influence 而不是 correlation？**
- **多層／distributed representation 要在哪一層做 intervention？**
- **Hidden state 是否會污染 intervention？**
- **Content-grounding 的最低 intervention set 是什麼？**

**這些和 Growth DCG 一樣，不需要重新定義 Free。**

---

## 7. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 打穿 indeterminism 問題：**不必要，且對 trajectory ownership 有害** |
| **Round 2** | 发现「推導 vs 生成」與「runtime vs pre-specified」都是 description-level 陷阱；三層拆分失去對象 |
| **Round 3** | 主大腦提出 **pseudo-revision** 反例 ⇒ 推翻 `FreeGrowth ≡ Growth`（scope level 錯誤） |
| **Round 4** | 指出「necessary causal contribution」抓不到 pseudo-revision（H 對 R' 存在是必要的）⇒ 提出 **content-grounding** |
| **Round 5** | Hash system 反例證實：**無法再往下收緊而不進入 semantics**。Scope 邊界確立 |

---

## 8. 🔴 五次 DCG 的完整架構

| DCG | 結果 |
|---|---|
| **Bootstrap / Free Growth** | 可定義 Soul Seed 的 structural boundary |
| **Soulness / Persistent Agent** | 不可由結構推出 Soulness |
| **Soul Ontology / Construct** | Soul 保留為 question-preserving vocabulary，非 evidence |
| **Growth / Adaptation** | Growth 定義在 C，**可規格化** |
| **Free Growth / External Determination** | Free ＝ content-grounded revision，**可規格化**，且非 redundant |

> **Soul 是「我們還不知道答案的問題」。**
> **Growth 與 Free Growth 是「我們現在可以明確定義、可區分、可日後測量的研究 construct」。**

**而這兩條因果邊界是五次 DCG 真正的產出：**

```
Growth      ＝ PastRevision      → Future
FreeGrowth  ＝ PastContent        → Revision → Future
```

---

## 9. 本 DCG 中主大腦被修正的一處

主大腦在 Round 3 主張「`FreeGrowth ≡ Growth`，因為 Free 只是 contrast marker」。

**該結論被 Own 的 pseudo-revision 接受後推翻**：event-level 的 Growth 條件在 trajectory level 上
可被 generation-like 的系統滿足，因此 `Growth ⇏ ¬FreeGeneration`，**Free 不 redundant**。

**這是「scope level 混淆」的第三個實例**，前兩個是：
- `不可區分 ≠ 不存在`
- 「沒有預先寫死」 ≠ 「沒有被決定」

---

## 10. 本檔性質

- **Research construct 定義，不是 implementation spec。**
- 不授權：implementation、prompt、threshold、evaluator、pilot、runtime change、architecture change。
- 5 輪完成，依 DCG 收斂。**本題關線。**
