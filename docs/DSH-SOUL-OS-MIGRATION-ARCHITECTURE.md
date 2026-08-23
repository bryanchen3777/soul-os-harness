# DSH Soul OS Migration Architecture（DSH-MA-2）

**日期**：2026-08-23
**狀態**：ARCHITECTURE DESIGN — 前置設計，NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-ADAPTER-BOUNDARY.md`（MA-1 Adapter Boundary）、`docs/DSH-SOUL-OS-MIGRATION-PLAN.md`（migration plan）

---

## 0. North Star

> **Soul OS owns the durable work truth. DSH owns ephemeral execution.**

MA-1 解決「Soul OS 如何安全地接觸 DSH」。MA-2 解決「整個 Soul OS 如何住進這個 Multi-Agent Environment」。

**最大的 architectural trap**：不要把 Soul OS 簡化成「有 memory 的 Chief Agent」。Memory 是 Soul 的生命過程之一，但不是 Soul 的全部。

```text
DSH Multi-Agent Environment
        │
  ┌─────┴─────┐
  │           │
Execution    Soul
Plane        Plane
  │           │
subagent/    Identity
workflow/    Memory
goal         Inner Life
session/     World
tools        Agency
  │           Relationship
  │           Time / Context
  │           │
  └── Adapter ──┘
        │
  Domain / Soul Core
        │
  Durable Truth / Authority
```

---

## 1. 兩個 Plane

| Plane | 內容 | 誰擁有 | 生命週期 |
|---|---|---|---|
| **Execution Plane** | subagent / workflow / goal / session / tools | DSH | ephemeral（重啟即失） |
| **Soul Plane** | Identity / Memory / Inner Life / World / Agency / Relationship / Time-Context | Soul OS | durable（Soul 自己的 store） |

Adapter 是兩個 Plane 之間唯一的耦合層（MA-1 已鎖定）。

---

## 2. 十個問題的答案

### Q1. Soul Identity 在 DSH 中的 canonical home 是什麼？

**Soul Identity 的 canonical home 是 Soul OS 自己的 durable state**（persona / COS / identity kernel），不是 DSH session / preset。

DSH preset 只是 execution 的投影（role → preset），不是 identity 的來源。identity 的語意與資料所有權只屬於 Soul Kernel（migration plan 四條 constitution 之一）。

### Q2. Memory 哪些屬於 durable Soul state、哪些只是 execution context？

| 類型 | 內容 | 歸屬 |
|---|---|---|
| durable Soul state | SAGE graph、v1 JSONL、emotional state、history、residue | Soul OS 自己的 store |
| execution context | DSH session log、tool call、subagent run | DSH（ephemeral audit sidecar） |

**DSH session log 不是 Soul memory**（migration plan §3.3 兩條 log 不合併）。Soul memory 的 durable truth 在 Soul 自己的 store。

### Q3. Inner Life 是否能觀察 DSH execution，而不能被 DSH 定義？

**Inner Life 觀察 DSH execution 的結果，但由 Soul 的 scheduler / agency 定義，不被 DSH 的 agent / session 定義。**

Inner Life（diary / dream / event）是 Soul 的生命過程，由 Soul 的 scheduler 觸發、由 Soul 的 agency 決策。DSH execution 只是 Inner Life 觀察的輸入之一，不是 Inner Life 的定義者。

### Q4. World / perception 如何進入 multi-agent environment？

World / perception 是 Soul 的感知層（Signal → Perception → Lived Context），透過 **capability adapter** 進入 multi-agent environment，不被 DSH 的 tool 定義。

World 的 source（calendar / weather / news）是 Soul 的 signal producer，不是 DSH 的 tool。DSH tool 只是 capability 的 execution 實作。

### Q5. Agency 是 Soul 的決策層，還是 DSH agent orchestration？

**Agency 是 Soul 的決策層（4 stages），DSH orchestration 是 execution。兩者分離。**

Agency 的 4 stages（check_eligibility → make_decision → select_action → execute_action_stub）是 Soul 的決策邏輯，由 Soul 的 scheduler 觸發。DSH orchestration（subagent / workflow / goal）只是 Execution stage 的實作。

### Q6. Chief 是 Soul 的 agency representation，還是單純 DSH subagent？

**Chief 是 Soul 的 agency 在 DSH 中的 representation，但它的 authority / decision 來自 Soul 的 agency，不是 DSH subagent 的自主。**

Chief 的 decision（assign / consume）由 Soul 的 agency 定義，DSH subagent 只是 Chief 的 execution 載體。Chief 的 authority 來自 Soul 的 capability policy + Human Approval，不是 DSH subagent 的自主權限。

### Q7. History / Memory / lived context 如何跨 DSH session 保存？

**這些是 Soul 的 durable state，跨 DSH session 保存（Soul 自己的 store），不依賴 DSH session。**

DSH session 結束（正常/異常）時，Soul 的 history / memory / lived context 仍在 Soul 自己的 store。DSH session 只是 execution 的 audit sidecar。

### Q8. Soul OS 能否在沒有 DSH 的情況下仍保持 identity + memory + inner state？

**能。** 這是 No-DSH Survival Test：拔掉 DSH 後，Soul Kernel 仍能 receive / tick / recover / snapshot / persist / perceive / form experience / memory retrieval / agency。

DSH 消失時，execution 消失，但 identity + memory + inner state 仍在。

### Q9. 多 agent 之間哪些東西是 Soul-owned，哪些只是 DSH execution artifact？

| Soul-owned（durable） | DSH execution artifact（ephemeral） |
|---|---|
| identity / persona / COS | session log |
| memory / history / residue | tool call / result |
| inner life（diary / dream / event） | subagent run |
| agency decision | workflow run |
| relationship | goal state |
| lived context | preset |

### Q10. 什麼東西絕對不能被 DSH 的 agent / session / workflow abstraction 重新定義？

**Soul 的 identity / memory / inner life / agency / history 的語意，不能被 DSH 的 agent / session / workflow abstraction 重新定義。**

DSH 的 abstraction（agent / session / workflow / goal / tool）是 execution 的實作，不是 Soul 的 ontology。Soul 的 ontology（identity / memory / inner life / agency / history）由 Soul Kernel 定義，DSH 只能投影，不能重新定義。

---

## 3. 關鍵 boundary（Soul-owned vs DSH execution artifact）

```text
Soul-owned（durable，Soul Kernel 定義）
  ├── Identity（persona / COS / identity kernel）
  ├── Memory（SAGE / v1 / emotional / history / residue）
  ├── Inner Life（diary / dream / event）
  ├── World（Signal → Perception → Lived Context）
  ├── Agency（4 stages）
  ├── Relationship
  └── Time / Context

DSH execution artifact（ephemeral，DSH 定義）
  ├── session log
  ├── tool call / result
  ├── subagent run
  ├── workflow run
  ├── goal state
  └── preset
```

**Adapter 是唯一跨 boundary 的耦合層。** Soul-owned 的東西不 import DSH；DSH execution artifact 不滲透回 Soul-owned。

---

## 4. 最大的 architectural trap（不可犯）

**不要把 Soul OS 簡化成「有 memory 的 Chief Agent」。**

- Memory 是 Soul 的生命過程之一，但不是 Soul 的全部。
- Soul OS = Identity + Memory + Inner Life + World + Agency + Relationship + Time-Context，不是「Chief Agent + memory store」。
- 若把 Soul OS 簡化成 Chief Agent，就會失去 Inner Life / World / Agency / Relationship 的獨立性，最後變成「被 DSH 綁死的 Soul」。

---

## 5. Boundary contract（可實作）

1. **Soul Identity 的 canonical home 是 Soul 自己的 durable state**，不是 DSH session / preset。
2. **DSH session log 不是 Soul memory**（兩條 log 不合併）。
3. **Inner Life 觀察 DSH execution，不被 DSH 定義**。
4. **World / perception 透過 capability adapter 進入，不被 DSH tool 定義**。
5. **Agency 是 Soul 的決策層，DSH orchestration 是 execution**。
6. **Chief 的 authority 來自 Soul 的 agency，不是 DSH subagent 的自主**。
7. **History / Memory / lived context 跨 DSH session 保存（Soul 自己的 store）**。
8. **No-DSH Survival**：拔掉 DSH 後 Soul Kernel 仍能運作。
9. **Soul-owned vs DSH execution artifact 分離**。
10. **Soul 的 ontology 不被 DSH 的 abstraction 重新定義**。

---

*本文件為 DSH-MA-2 Soul OS Migration Architecture，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
