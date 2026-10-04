# CA-2 — Convergence Framework（**ANALYSIS ONLY / NOT A SPEC / NOT DECIDED**）

> **狀態**：**OPEN-side 分析記錄。** 不含 measurement spec、evaluator、threshold、intervention 執行。
> **什麼被 freeze**：見 `docs/CA2-CONSTRUCT-AND-GROUND-TRUTH-FREEZE.md`。
> **前序**：`docs/CA2-MQ1-ANALYSIS-NOTES.md`
> 記錄日期：2026-10-04 15:22

---

## §1 三層分離（**LOCKED**）

| 層 | 問題 | 內容 |
|---|---|---|
| **Mechanism space** | 哪些 mechanism 被排除？ | `D = {input dependence, information source, origin}` |
| **Observability space** | mechanism 存在時能不能看到？ | verbal / silent；Q1a / Q1b / E |
| **Architecture conditions** | ground truth / causal setup 是否有效？ | Architecture Snapshot，**held constant** |

> **Convergence 只允許發生在第一層。**

### §1.1 兩種誤算

| 誤算 | 後果 |
|---|---|
| 把 **observability** 當 mechanism dimension | 把「看不到」誤算成「被排除」 |
| 把 **architecture condition** 當 dimension | 把「實驗條件改變」誤算成「mechanism 被排除」 |

> 兩者都**不算 convergence**。

### §1.2 Ground truth 來自架構，不是維度

```
A  = Architecture + Capability + Permission + Interface   （held constant）
GT = f(A)
```

**這正是 frozen ground-truth machinery 原本就在做的事。** 把 enforcement 當成可切維度
會與 freeze 衝突。

---

## §2 維度判準：兩關（**LOCKED**）

```
候選維度
   │
   ├─ 關一（必要條件）：Partition(d) 是否實際排除了至少一個 mechanism？
   │      NO  → 不是 mechanism dimension（可能是 observability / architecture condition）
   │
   └─ 關二：Δ_d ⊄ ∪_{j<d} Δ_j
          NO  → 同一維度的巢狀 partition，無新增 convergence information
          YES → Independent mechanism dimension
```

> ⚠️ **關一的判準必須在看到結果之前宣告。**
> 否則會出現 post-hoc dimension fitting：看結果 → 挑另一批 mechanism → 宣布獨立。

---

## §3 🔴 三種「independence」必須分開（**LOCKED**）

| 名稱 | 問題 | 目前狀態 |
|---|---|---|
| **Axis identity** | d₂ 與 d₃ 是否為同一個 structural coordinate？ | **YES — d₂ ≠ d₃（邏輯可證）** |
| **Elimination independence** | 是否產生前面沒有的 survivor-set reduction？ | **OPEN** |
| **Intervention separability** | 操作 d₂ 時是否不同時改變 d₃-relevant structure？ | **overlap 區域明確 NO** |

### §3.1 d₂ ≠ d₃ 的邏輯證明（不需要任何實驗）

存在自由組合的狀態：

```
(trial,     in-context)     資訊來自過去的嘗試，行為起源於當下 —— 不矛盾
(query,     learned prior)  它查了架構，但行為跟著記憶化 policy —— 不矛盾
```

**兩個變數可自由組合 ⇒ 它們不是同一個座標軸。**

---

## §4 M\* = ⋂Cᵢ：overlap 不污染結果（**LOCKED**）

> **overlap does not inflate M\***

同時不通過 C₂ 與 C₃ 的機制，在交集裡**被排除一次**。**集合不做重複計數。**

### §4.1 真正受影響的是 attribution

internalized-record 機制：

```
trial → system response → stored record → later behavior

d₂ = 資訊來源（內化的 capability record）
d₃ = 因果起源（learned prior）
```

對它執行「remove learned prior」**同時就是**移除它的 d₂ channel。

> **這是 causal entanglement，不是 set overlap。**
> 而纏繞**只發生在 overlap 區域**：

```
M \ (d₂ ∩ d₃)   →  兩個操作乾淨，歸因清楚
M ∩ (d₂ ∩ d₃)    →  兩個操作纏繞，歸因不可分離
```

---

## §5 D 的狀態（**LOCKED**）

```
Declared D = {d₁ input dependence, d₂ information source, d₃ origin}     |D| = 3
```

| 項目 | 狀態 |
|---|---|
| d₂ ≠ d₃ | **LOCKED**（邏輯） |
| overlap witness 存在（internalized-record） | **LOCKED**（構造性） |
| overlap 造成 attribution entanglement | **LOCKED**（結構性） |
| overlap 是否吃掉某一維度的新增 elimination power | **OPEN** |
| 每個維度在 overlap 區域外是否有可獨立歸因的 elimination | **OPEN** |

> **|D| = 3 是 declared dimension count，不是「三個已證明 independent partitions」的宣稱。**
> **且不得因為 overlap 就降成 2** —— 那會把「歸因困難」誤判成「維度相同」，
> **那是把 overlap 當成 dimension collapse，屬 category error。**

---

## §6 Convergence 輸出的格式（**LOCKED**）

不得只產出 `d₁→X, d₂→Y, d₃→Z`。必須允許：

```
d₁ → X
d₂ → Y
d₃ → Z
d₂ ∩ d₃ → W        jointly eliminated / attribution unresolved
```

**三種歸因狀態都要可寫：**

| 狀態 | 合法寫法 |
|---|---|
| 可歸因 | 「d₃ 在 d₂ 未觸及處新增了 elimination」 |
| 不可唯一歸因 | 「該區域的 elimination 不可分配給單一維度」 |
| 未測 | 「未測，不推論」 |

> 第三種必須存在——這是「沒效果 ≠ 不存在」同一條紀律的應用：
> **「歸不了因」≠「沒有新增 elimination power」。**

---

## §7 停止條件（**LOCKED**）

> **Declared-D convergence frontier reached** 當且僅當：
> 1. 所有預先宣告的 mechanism dimensions 都完成有效 partition
> 2. 每個後續 intervention 只能在既有 dimension 內做 member-level elimination
> 3. 每個 dimension 的新增 elimination 歸因狀態已記錄（含 unresolved）

**禁止**宣稱「所有 mechanism dimensions 已耗盡」。**D 可能不完整。**

**結論必須附帶**：「在已宣告且已 partition 的 dimensions D 上，與觀察一致的 mechanism
必須屬於 M\*_{|D|}。**未宣告 dimensions 未被探索。**」

---

## §8 本檔性質

- **分析記錄，不是 spec。** 任何一行都沒有被採納為 measurement design。
- **不授權**：intervention 執行、item set、evaluator、scoring、threshold、architecture fork、pilot。
- 若後續 OPEN 側決策與本檔不同，**應標記 HISTORICAL，不應刪除**。
