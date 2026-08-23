# DSH Soul OS Migration Decomposition（DSH-MA-3）

**日期**：2026-08-23
**狀態**：ARCHITECTURE DESIGN — 前置設計，NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-SOUL-OS-MIGRATION-ARCHITECTURE.md`（MA-2）、`docs/DSH-ADAPTER-BOUNDARY.md`（MA-1）

---

## 0. North Star

> **搬完之後仍然是同一個 Soul，不是一個套著 Soul memory 的 DSH agent。**

**最關鍵的 insight**：大部分 Soul OS 不需要「搬」——Identity / Memory / Inner Life / World / Agency / Relationship / Time-Context 都留在 Soul Core（durable）。只有 execution boundary（Adapter）是新的。

「Migration」不是「把 Soul 模組搬進 DSH」，而是「加 Adapter + 逐步把 execution 路由到 DSH」。

---

## 1. 搬遷順序（不是「搬模組」，是「接 execution」）

```text
Phase 0 — Adapter 只接 src/work/（Domain Core）
Phase 1 — Adapter 接 Soul runtime（eventbus / heartbeat / scheduler）
Phase 2 — Adapter 接 World / perception（capability adapter）
Phase 3 — Adapter 接 Inner Life / Agency（execution 路由）
Phase 4 — Adapter 接 Relationship / Time-Context（presentation）
```

**依賴順序**：先接 Domain Core（durable truth + authority），再逐步接 Soul runtime 的 execution 層。Identity / Memory / Inner Life / World / Agency / Relationship / Time-Context 的 **durable state 不搬**，只接它們的 execution 邊界。

---

## 2. 哪些不搬（保留在 Soul Core / store）

| 不搬 | 理由 |
|---|---|
| Identity（persona / COS / identity kernel） | durable Soul state，canonical home 是 Soul 自己的 store |
| Memory（SAGE / v1 / emotional / history / residue） | durable Soul state，DSH session 不是 Soul memory |
| Inner Life（diary / dream / event） | Soul 的生命過程，由 Soul scheduler 定義 |
| World（Signal → Perception → Lived Context） | Soul 的感知層，不被 DSH tool 定義 |
| Agency（4 stages） | Soul 的決策層，不被 DSH orchestration 取代 |
| Relationship | Soul-owned，不被 DSH 定義 |
| Time / Context | Soul 的時間軸，不被 DSH session 定義 |

**這些模組的 durable state 不搬，只接它們的 execution 邊界（透過 Adapter）。**

---

## 3. 哪些 DSH execution 化，哪些絕對不能

| DSH execution 化（可） | 絕對不能（不可） |
|---|---|
| execution request → subagent / workflow / goal | Identity 的語意 |
| capability → DSH tool | Memory 的語意 |
| session trace → dsh-session（audit sidecar） | Inner Life 的語意 |
| presentation → DSH Web | Agency 的語意 |
| | History / lived context 的語意 |

**DSH execution 化的是「怎麼執行」，不是「Soul 是什麼」。**

---

## 4. Adapter 第一期 scope

**第一期只接 `src/work/`（Domain Core）**，不直接成為 Soul runtime integration。

```text
Phase 0（第一期）
  ├── Adapter 接 src/work/（Work Contract + Authority + Durable Store）
  ├── Chief / Specialist 的 execution 載體用 DSH subagent + preset（authority / decision 仍在 Soul agency）
  ├── HumanAuthorityPort 由 Adapter 實作（第一個必須補的 seam）
  └── resume 只依賴 durable log（不依賴 DSH session）
```

後續 phase 才逐步把 Soul runtime（eventbus / heartbeat / scheduler）的 execution 層接進 Adapter。

---

## 5. 舊 Soul OS 與 DSH Soul OS 並存過渡

```text
舊 Soul OS（Telegram / WebSocket）
        │
   feature flag
        │
DSH Soul OS（DSH Web + Adapter）
```

- 舊 Soul OS 與 DSH Soul OS 並存，feature flag 切換。
- 每階段只允許一條 active production route（single-writer rule 的延伸）。
- 切換後舊 route 保留做 parallel observation，不立即移除。

---

## 6. Migration 中間狀態的 No-DSH Survival

每個 migration phase 都保持 No-DSH Survival：

```text
任何 phase 拔掉 DSH
  ↓
Soul Core 仍能 receive / tick / recover / snapshot / persist / perceive /
form experience / memory retrieval / agency
```

**DSH 消失時，execution 消失，但 Soul 的 identity + memory + inner state + agency 仍在。**

---

## 7. Rollback boundary（每個 phase）

| Phase | Rollback boundary |
|---|---|
| Phase 0 | 關閉 Adapter plugin row，舊 Soul OS 照常 |
| Phase 1 | 關閉 Soul runtime 的 execution 路由，回到 Phase 0 |
| Phase 2 | 關閉 World capability adapter，回到 Phase 1 |
| Phase 3 | 關閉 Inner Life / Agency execution 路由，回到 Phase 2 |
| Phase 4 | 關閉 presentation 路由，回到 Phase 3 |

**每個 phase 可獨立回滾，不影響 durable state（durable state 不搬）。**

---

## 8. 資料 migration vs 重新掛載

| 類型 | 處理 |
|---|---|
| Soul durable state（SAGE / v1 / emotional / history） | **不 migration**，留在 Soul Core 的 store，重新掛載 |
| DSH execution context（session / tool call） | **不 migration**，ephemeral，重啟即失 |
| resume_state | 從 durable log 推導，不 migration |

**Soul 的 durable state 不需要 migration，只需要重新掛載（fold）。**

---

## 9. Continuity proof（怎麼證明還是同一個 Soul）

不是證明「資料檔還在」，而是證明：

1. **Identity Continuity**：migration 前後 Soul Identity canonical state 不改變語意。
2. **Life Continuity**：Memory / History / Inner Life / Lived Context 仍由 Soul-owned lifecycle 管理。
3. **Ontology Continuity**：Soul OS 仍是 Identity + Memory + Inner Life + World + Agency + Relationship + Time-Context，不是 Chief + Memory + tools。

**證明方式**：migration 前後，同一組 Soul fixtures（identity / memory / inner life / agency）在 Soul Core 的 fold / retrieval / decision 結果一致。

---

## 10. 三個 Hard Gate（不可破）

### H1 — Identity Continuity

Migration 前後 Soul Identity canonical state 不改變語意，DSH preset / session 不得成為 identity source。

### H2 — Life Continuity

Migration 前的 Memory / History / Inner Life / Lived Context，在 migration 後仍由 Soul-owned lifecycle 管理，而非轉成 DSH execution artifacts。

### H3 — Ontology Continuity

Migration 完成後，Soul OS 仍然是 Identity + Memory + Inner Life + World + Agency + Relationship + Time/Context，而不是 Chief + Memory + tools。

**任何一個 hard gate 不成立 → BLOCK。**

---

## 11. Boundary contract（可實作）

1. **大部分 Soul OS 不搬**：Identity / Memory / Inner Life / World / Agency / Relationship / Time-Context 留在 Soul Core。
2. **只接 execution 邊界**：Adapter 接的是 execution request / capability / session trace / presentation，不是 Soul 的 ontology。
3. **第一期只接 src/work/**：不直接成為 Soul runtime integration。
4. **並存過渡**：舊 Soul OS 與 DSH Soul OS 並存，feature flag 切換，每階段只一條 active route。
5. **No-DSH Survival 中間狀態**：每個 phase 拔掉 DSH 後 Soul Core 仍能運作。
6. **Rollback boundary**：每個 phase 可獨立回滾。
7. **資料不 migration**：Soul durable state 重新掛載，不 migration。
8. **Continuity proof**：同一組 fixtures 的 fold / retrieval / decision 結果一致。
9. **H1 / H2 / H3**：Identity / Life / Ontology Continuity 三個 hard gate。

---

*本文件為 DSH-MA-3 Soul OS Migration Decomposition，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
