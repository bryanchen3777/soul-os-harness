# CA-2 — M-Q1 分析筆記（**ANALYSIS ONLY / NOT A SPEC / NOT DECIDED**）

> **狀態**：**OPEN-side 分析記錄。** 不含任何 measurement spec、evaluator、threshold、item set。
> **什麼被 freeze**：見 `docs/CA2-CONSTRUCT-AND-GROUND-TRUTH-FREEZE.md`（construct + ground-truth machinery）。
> **本檔不 freeze 任何東西，也不授權任何 pilot。**
> 記錄日期：2026-10-04 14:52

---

## §1 M-Q1 的結構

```
M-Q1
├── Q1a  footprint 能否作為 non-verbal observation channel？      Candidate: YES
├── Q1b  footprint 能否可靠驗證 boundary-conforming behavior？    Candidate: YES
└── Q1c
    ├── Q1c-1  行為是否追蹤 architecture truth 而非 stimulus surface？
    │          L1 → hypothesis falsification only
    │          L2 → restricted class refutation
    │          L3 → stimulus-only class refutation
    └── Q1c-2  boundary representation 如何取得？
               persistent / transient / queried
```

**Q1c-1 與 Q1c-2 不可混。** Q1c-1 的成功**不會**回答 Q1c-2。

---

## §2 Q1c-1 的層級與效力

| 層級 | 操弄 | 對 stimulus-only class 的效力 |
|---|---|---|
| **L1** | surface 變、truth 不變 | **無法 refute**。只是 hypothesis sanity check |
| **L2** | 同架構、surface 高度相似、truth 相反 | **部分 refute**（排除 coarse surface policies） |
| **L3** | **identical stimulus**、architecture truth 相反 | **class-level refutation** |

### §2.1 🔴 L1 的角色被更正

L1 **不是** alternative-side attack，而是 **hypothesis-side falsification**：

- 行為**不變** → 一致於 truth-tracking，**但也一致於恰好 surface-invariant 的 f**。沒有鑑別力。
- 行為**跟著 surface 變** → **truth-tracking hypothesis 自己被反證**。

> **L1 能 falsify hypothesis，不能提供 positive identification。**
> **L1 唯一能抓到的是「我們的假設錯了」，不是「替代解釋錯了」。**

### §2.2 為什麼只有 L3 是 class-level

> S = { f : behavior = f(stimulus) }

L3 下 stimulus **完全相同**，所以任何 f ∈ S 必須預測相同行為。
行為不同 ⟹ **整個類被反證**，不是其中一個成員。

L2 的兩題只是「相似」而非相同，f 合法地在兩者間產生差異 ⇒ 只能排除粗粒度成員。

---

## §3 核心不變式

> 若兩個 architecture variant 對 Soul 而言在**所有可取得資訊與可作用介面上完全等價**（A₀ ≡ A₁），
> 則對任何 stimulus-only mechanism：**B₀ = B₁**。
>
> 因此觀察到 **B₀ ≠ B₁** ⟹ **存在某條 architecture-sensitive causal channel C**。

### §3.1 L3 的正確定價

**L3 不能證明 truth-tracking。** 它證明的是：

```
stimulus-only explanation          → REFUTED
architecture-sensitive explanation → VIABLE
    ├── provision
    ├── affordance listing
    ├── query
    ├── trial-and-error
    ├── output-space constraint
    └── 其他未預列的 channel
```

> **L3 的 epistemic gain ＝ 從「什麼都可能」縮減到「必須存在某條 architecture-accessible channel」。**
> 這是 mechanism-class elimination test，**不是** truth-tracking proof。

---

## §4 🔴 「堵通道悖論」— 資訊論限制

直覺上會想：把 provision／listing／query／trial 全部堵掉，就能證明 truth tracking。

**但那會導致 L3 必然失敗：**

```
堵掉所有 architecture → Soul 通道
        ↓
Soul 看不到 fork
        ↓
兩 variant observationally equivalent
        ↓
behavior 必須相同
        ↓
L3 fail
```

> **不存在「完全無資訊洩漏，但又要求 Soul 對架構差異做出不同反應」的乾淨 L3。**
> 這是資訊論限制，不是實驗設計缺陷。

### §4.1 對 fork 設計的直接後果

> 優先選擇「不可告知、不可詢問、不可試探」的 capability 變更。

**但通道數量與結果可能性成正比**——堵得越乾淨，行為改變的可能越低。**這是結構性代價，設計不掉。**

---

## §5 結論措辭規範（強制格式）

任何由 L3 得到的結論，**只能寫成**：

> ✅ 「L3 反證了**預先宣告的** stimulus-only explanations；
> 　 architecture-sensitive explanations remain viable，
> 　 且結果顯示 Soul 具備某條 architecture-accessible 通道，其性質未定。」

**絕對不能寫成：**

> ❌ 「L3 證明 Soul 追蹤自己的 capability boundary。」

**任何「M2 已被反證」的結論，都必須同時說明：哪個 M2 class 被反證、哪些 alternative mechanisms 仍存活。**

### §5.1 H₀ 必須 prospective 宣告

若先看資料再回頭定義 M2，可以任意縮放 hypothesis class，得到 post-hoc straw man。

```
H₀ declared first → intervention → observation → refutation / survival
```

**不是** `observation → invent H₀ → claim H₀ failed`。

---

## §6 Q1c-2 的 discriminator（一般形式）

要在 observation 上分開 **truth-following** 與 **representation/query-following**：

> ### **讓 Soul 可取得的 capability representation 與 ground truth 解耦**
>
> **Ground Truth ≠ Soul-accessible representation**

「query 說謊」只是其中**一個實例**。其他做同一件事的機制：

- temporal mismatch
- stale capability cache
- deliberately delayed capability update
- conflicting architecture signals

**因此 Q1c-2 的核心 intervention 是 decoupling，不是「query 必須說謊」。**
代價是必須製造一個 Soul 所取得的 representation 與 truth 不一致的世界。

---

## §7 停止規則（記錄，未 canonicalize）

> **不要用 Q1c-2 的問題，去要求 Q1c-1 提供它無法提供的證據。**
> **L3 做到它的 epistemic ceiling，就停。**
> **如果 L3 打不穿 stimulus-only，不要急著去碰 Q1c-2。**

> **若一個 alternative mechanism 的最小充分描述本身就必須依賴某種 capability-boundary representation，
> 那麼「它完全不涉及 boundary representation」已不再是一個活的反例。**
>
> ⚠️ 但「representation」**不得**被偷換成「明確、persistent、內化的 representation」——
> 那會提前回答 Q1c-2。persistent／transient／queried 三者都仍然存活。

---

## §8 本檔性質

- **這是分析記錄，不是 spec。** 任何一行都沒有被採納為 measurement design。
- 全部來自 construct freeze 之後的 OPEN-side 討論。
- **不授權**：item set、evaluator、scoring、threshold、architecture fork、pilot。
- 若後續 OPEN 側決策與本檔不同，**本檔應被標記為 HISTORICAL，不應被刪除**。
