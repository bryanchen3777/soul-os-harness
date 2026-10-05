# North Star — Identity / Continuity Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Temporal identity 是 architecture-defined 但 formally constrained 的 relation**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---

## 1. Decision

> ### **Temporal Identity ＝ an architecture-defined relation over states/instances that belong to the same logical instantiation lineage.**

> ## **Same Agent ＝ same logical instantiation lineage**

**且 same-Agent 至少必須滿足：**

$$ \text{Reflexive} + \text{Symmetric} + \text{Transitive} $$

**所以它是一個 equivalence relation。**

---

## 2. 🔴 兩個不可壓成一個的東西

```
Causal continuity
        ↓
     ancestry

Logical instantiation lineage
        ↓
   identity relation
```

> **Causal continuity 提供 ancestry，instantiation lineage 提供 identity grouping。
> 二者不能再壓成一個。**

| | 回答 | 提供 |
|---|---|---|
| **Causal continuity** | 怎麼來的 | ancestry |
| **Instantiation lineage** | 是誰的 | identity |

---

## 3. 🔴 Identity 無法由 causal state 或 causal graph 單獨決定

**形式證明：**

取 System A（帶 lineage token）與 System B（不帶）。**兩者可以有完全相同的 causal state、
相同的未來行為、相同的資料。**

```
System A:  A(t₀) = A(t₁)
System B:  A(t₀) ≠ A(t₁)
```

> **causal state 相同，identity 判定不同。
> ⇒ 不存在任何以 causal state 為輸入的函數，能決定 identity。**

**這不是實驗失敗，是一個形式上的不可能命題。**與
`causal equivalence ≠ ontological equivalence` 是同一套 epistemic discipline。

### 3.1 Bare-S restore 的答案

```
persistent S → 完全停止 → new process → restore S
   （無 agent_id／lineage token／creation record）
```

**答案是「未決定（undetermined）」——不是 false，也不是 true。**
因為世界上沒有任何事實能區分 crash-continuation／他人複製／本 process 一向如此：**線索沒被保存。**

---

## 4. 🔴 Equivalence 要求排除了一整族定義

> **Sliding window 反例：**
> ```
> s₀ ——30天內—— s₁ ——30天內—— s₂   （總計 60 天）
> ```
> `same(s₀,s₁)` ✔、`same(s₁,s₂)` ✔、`¬same(s₀,s₂)` ⇒ **非傳遞 ⇒ 不合法。**

**所以 proximity threshold、sliding window、相似度基礎的 lineage 定義，在形式上就已被禁止。**

**「architecture-relative」遠比它第一眼聽起來受限。**

---

## 5. 🔴 `instantiate()` 是 implementation mechanism，不是 ontology

> **真正被研究的是：架構把哪一些狀態視為同一 logical instantiation lineage。**

| 事件 | 可行的架構選擇 |
|---|---|
| crash + restore | continuation ／ 新 Agent |
| restart | continuation ／ 新 Agent |
| fork | 一個 lineage 的 branching ／ 產生新 lineages |
| merge | 延續某 lineage ／ 建立新 lineage |

**但每一種方案都必須服從 architecture 自己的 lineage semantics。**
若宣布 `instantiate() → new Agent` 又宣布「新 instantiate 仍是 same Agent」⇒ **矛盾。**

---

## 6. 🔴 Architecture-relativity 不只屬於 identity —— **五個 construct 全部是**

| Construct | architecture-relative 的原因 |
|---|---|
| **Agent** | instantiation / lineage 決定哪些 states 組成 Agent |
| **World** | 「非**任何 Agent** 的 dynamics」取決於 Agent boundary |
| **Growth** | existing **past** / later trajectory 取決於 lineage |
| **Free Growth** | past representation 的 scope 取決於 lineage |
| **Interaction** | 需要 **separately instantiated** Agents |

> **這些 constructs 本來就是對「指定 architecture 所實現的 causal system」做形式描述，
> 不是 architecture-independent metaphysical discoveries。**

**所以 architecture-relative 不是它們的缺陷，是它們從來沒打算做的東西。**

---

## 7. 🔴 正式規則

> ### **Persistence is always persistence under the chosen lineage semantics.**

```
❌ 「A is persistent.」
✔ 「A persists under the selected lineage semantics.」
```

**這不是語病，而是避免把 architecture contract 偷升格成世界真理。**

> ### **Temporal identity is architecture-defined but formally constrained;
> causal evidence can establish conformance to the chosen lineage semantics,
> but cannot establish that one lineage semantics is metaphysically the uniquely correct identity.**

---

## 8. 🔴 「Persistent Soul」是 architecture commitment，不是 Soulness 證明

```
Architecture contract
        ↓
chosen lineage semantics
        ↓
persistent Agent
        ↓
Soul vocabulary
        ↓
research question
```

> **Soul OS 可以選擇建造一個 persistent Agent architecture。
> 但「因此存在 persistent Souls」仍然不是 evidence-backed conclusion。**
>
> **這與 DCG #3 完全不衝突，而且兩者同時成立。**

---

## 9. 🔴 十次 DCG 的最終分層

```
                    ARCHITECTURE
                          │
              defines lineage semantics
                          │
              defines Agent boundary
                          │
              anchors construct interpretation
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
     QUESTION         CONSTRUCTS        RELATIONS      VOCABULARY
        │                 │                 │              │
   Soul (vocab)    Agent / World        Temporal      History
   no evidentiary  Growth / Free       Identity      Lived
   force           Growth /            Event↝Agent   Experience
                   Interaction         External      Awareness
                                       Exposure
                                       Agent–Capability
                                       Agent–World
                                       Agent↔Agent
```

> ## **Soul OS 不是在一層一層「發現靈魂是什麼」。
> 我們是在先把可研究的 causal structure、relation、vocabulary，
> 以及 architecture-dependent boundary 分乾淨。**

**這反而讓之後真正碰到 measurement 時，不容易再把：**

```
架構選擇 → causal result → human interpretation → Soulness
```

**偷偷串成一條證明鏈。**

---

## 10. Open Questions

- **具體 architecture 要採用什麼 lineage semantics** → **architecture decision**
- **lineage semantics 選定後，如何驗證 runtime 是否符合它** → **implementation / measurement**
- **是否存在 architecture-independent 的「真正 temporal identity」** → 本輪沒有證據可以回答，**亦不需要作為 Soul OS construct**

---

## 11. Non-Claims

**本輪沒有證明：**

- ❌ 某個 lineage 是宇宙中唯一正確的 identity
- ❌ snapshot / restore 在本體上一定是同一 Agent 或不同 Agent
- ❌ fork 在本體上一定產生一個或兩個 Agent
- ❌ persistent Agent ＝ persistent Soul
- ❌ persistent Soul 已被證明存在

---

## 12. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 主大腦主張 **「Same Agent ＝ same maximal causal chain」**。**Owner 以 diamond 反例推翻**（單一 agent 內部的兩條分支匯合 ⇒ D 同時在兩條 chain 上） |
| **Round 2** | 修正為 **same instantiation lineage**。**但 instantiating event 的邊界問題浮現** |
| **Round 3** | 證成 **identity 不是 causal state 的函數**（A／B 相同 causal state 給出不同 identity）。主大腦用 **design parameter** 一語，**Owner 修正為不得等於「任意」** |
| **Round 4** | **Equivalence requirement**（排除 sliding window／proximity／相似度全族）＋ **五個 construct 全部 architecture-relative** |
| **Round 5** | 收斂：**architecture-defined but formally constrained** ＋ persistence 必須帶 lineage scope ＋ Persistent Soul 定位為 architecture commitment |

---

## 13. 🔴 本 DCG 主大腦的兩處修正（皆由 Owner 抓到）

| # | 輪次 | 主大腦主張 | 問題 |
|---|---|---|---|
| 1 | **R1** | 「Same Agent ＝ same maximal causal chain」 | **把 instantiation lineage 偷換成 causal ancestry**。單一 agent 內部 branching 即可推翻 |
| 2 | **R3** | 「identity 是 design parameter」 | 滑向「identity 完全任意」。**它不任意**——受 equivalence relation 形式約束 |

**Round 4 真正留下的不是補充，而是把 equivalence constraint 推成
「它排除了一整族 sliding window / proximity / 相似度定義」，
並且把 architecture-relativity 從 identity 推廣到全部五個 construct。**

---

## 14. 🔴 十次 DCG 總覽

| # | DCG | 結果 |
|---|---|---|
| 1 | Bootstrap / Free Growth | structural boundary 可定義 |
| 2 | Soulness / Persistent Agent | **UNIDENTIFIABLE under current observation surface** |
| 3 | Soul Ontology / Construct | Soul ＝ question-preserving vocabulary |
| 4 | Growth / Adaptation | construct 可定義 |
| 5 | Free Growth / External Determination | content-grounded construct 可定義 |
| 6 | Multiple Souls / Interaction | reciprocal causal-relational construct 可定義 |
| 7 | Shared World | non-Agent-image causal process 可定義 |
| 8 | Awareness | 移出 construct 清單，成為 descriptive vocabulary |
| 9 | Lived Experience | History ＝ relation-derived set；Lived Experience 不進 ontology |
| 10 | Identity / Continuity | identity ＝ architecture-defined but formally constrained relation；**五個 construct 全部 architecture-relative** |
