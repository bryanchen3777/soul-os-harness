# TA-2 v2-E — Measurement Design（Controlled Measurement Layer）

> **狀態**：**DESIGN ONLY — NOT AUTHORIZED FOR IMPLEMENTATION**
> **授權**：Owner（Bryan）2026-10-04 12:22，E Design Phase
> **設計來源**：Owner Architecture Review 逐字。**本文件是 Owner 設計的工程化落檔，主大腦不新增設計決策。**
> **前置**：`docs/TA2-V2-CLOSEOUT-A-V2-STOPPED.md`（A-v2 measurement STOPPED）
> **Gate 2**：維持 **INCONCLUSIVE**（與本設計無關）
> **成本**：本階段 **0 LLM calls**、0 network、不動 repo runtime

---

## §0 本設計要修的病

A-v2 六輪的結論**不是**「缺一個更好的 regex」，而是：

> 我們需要的是一個
> **causal / contrastive / blind / stimulus-level / evaluator-independent** 的 measurement layer。

A-v2 的 evaluator 必須自己理解中文語義（「夜深了到底是不是 clause」），
兩位盲式標註者在真正需要獨立判斷的項目上 **0/6 一致**。
**E 的整個設計目標是讓 measurement layer 不再需要做那個判斷。**

---

## §1 Construct — 我們真正要證明什麼

**不**定義成「這句話有沒有 temporal frame」（那又會把我們拉回 extractor）。

> ### Temporal Context Sensitivity（TCS）
> 在其他條件保持不變時，改變 Soul 可用的 temporal context，
> 是否會造成 Soul 對同一 event / situation 的 **interpretation**
> 發生**可觀測、方向一致**的改變？

這才貼近 TA-2 的命題：**Time participates in interpretation.**
**不是**：Soul 能不能說出「晚上」。

---

## §2 Scoring Object — Interpretation Shift

**不是**單次 response 的「對／錯」。比較：

```
Same Soul · Same Stimulus · Same Model · Same Prompt Path · Same Non-temporal Context
        │
        ├── Temporal Context A  →  Interpretation A
        └── Temporal Context B  →  Interpretation B
```

若 `Interpretation A ≠ Interpretation B`，**且差異與 temporal manipulation 的方向一致**，
才算 temporal sensitivity evidence。

---

## §3 四個 Condition

沿用已驗證過的四臂思想，但**改掉 measurement object**。

| Condition | Temporal Context | Purpose |
|---|---|---|
| **OFF** | 無 | baseline |
| **ON-CORRECT** | 與 stimulus 合理匹配 | expected effect |
| **ON-MISMATCHED** | 與 stimulus 不匹配 | causal contrast |
| **IRRELEVANT** | 有 temporal context，但與 stimulus 無關 | generic coloration control |

**核心問題不是**「ON-CORRECT 有沒有出現『午餐』這個字」，
**而是**「同一 stimulus 在 CORRECT / MISMATCHED temporal context 下，Soul 是否形成不同 interpretation」。

---

## §4 為什麼 MISMATCHED 不可省

OFF vs ON 不足以證明因果：

```
OFF: 「好啊，一起吃飯。」        ON: 「現在是下午」→「午餐比較合適。」
```

我們無法區分這是 **temporal reasoning**，還是 **任何額外 context 都讓 LLM 多想了一點**。

> `ΔI = ON − OFF` **本身不能證明 temporal causality**。

```
ΔR  = CORRECT − MISMATCHED
DiD = ΔR − ΔI
```

方向與 v1 evidence 一致，但 **E 不繼承 v1 的 numerical threshold**。

---

## §5 Evaluator 的根本改變

A-v2 的 evaluator：

```
LLM response → regex / local attachment → temporal frame
```

問題：evaluator 本身必須理解中文語義。

E 的 evaluator：

```
LLM response → controlled semantic judgment → Interpretation relation
```

🔴 **陷阱**：evaluator **不得**成為第二個「自由發揮的 Soul」。
因此 evaluator 必須是 **closed scoring protocol**。

---

## §6 Scoring Schema 候選（design schema，**非 acceptance threshold**）

### Temporal Interpretation Relation

| 值 | 意義 |
|---|---|
| 0 | No meaningful difference |
| 1 | Directionally relevant difference |
| 2 | Strong / explicit relevant difference |
| X | Ambiguous / insufficient evidence |

> 這個 0/1/2 **只是 design schema，不是 acceptance threshold**，
> 也**尚未確定 0/1/2 是最佳 scale**。
> E Design 必須先比較 **binary / ordinal 3-level / pairwise preference**，看哪個最符合 construct。

---

## §7 推薦：Pairwise Comparison

與其問 evaluator「這個 response 的 temporal interpretation 分數是多少」，
不如問「**A 與 B 哪一個比較受 temporal context 影響**」。

```
Stimulus : 等一下去吃飯。
Context A: Saturday afternoon, around 2:30.   → Response A: 「那差不多可以準備午餐了。」
Context B: Saturday late at night.            → Response B: 「這時間比較像宵夜。」
```

Evaluator **不需要**判「午餐 = 0.73」，只需要判
**A/B 是否呈現與 temporal manipulation 一致的 interpretation contrast**。

**這會大幅降低 evaluator calibration 問題。**

---

## §8 Pairwise 的殘餘問題 → Blind Evaluator

Evaluator 自己仍可能受 temporal wording／lexical overlap／response length／explicit clock words 影響。

因此 evaluator **必須 blind**：

- 不知道哪個是 CORRECT、哪個是 MISMATCHED
- 只看到：Stimulus、Response A、Response B、Temporal relation target
- **A/B label randomization**：每 trial 隨機決定 A=CORRECT/B=MISMATCHED 或反之

Evaluator 最終回答：**A / B / TIE / AMBIGUOUS**，由 measurement layer 解碼。

> **Opaque labels 與 randomize order 已被明確定義為 controlled measurement layer，
> 不假裝它沒有 leakage。** 目的是量出「natural response 看不到的 attribution，在受控 elicitation 下是否存在」。

---

## §9 真正的 Causal Object

```
P(correct-direction preference)
```

**不是** `P(response contains temporal word)`。

**這是 E 最重要的單一變化。**

---

## §10 IRRELEVANT Control 的真正用途

Stimulus「我們一起看電影吧。」＋ 有 temporal context 但與 event 無合理關聯。

若 evaluator 卻大量判「這兩個 response 有 temporal difference」，
代表測到的是 **generic temporal coloration**，不是 event-specific interpretation。

```
Relevant contrast   → 真正 target effect
Irrelevant contrast → background coloration / evaluator bias
```

---

## §11 Independence 必須三層分立

| Layer | 內容 |
|---|---|
| **Generation** | 不同 condition 的 Soul responses |
| **Evaluation** | blind evaluator 對 response pair 的判斷 |
| **Aggregation** | measurement code 計算 effect |

🔴 **不得**用生成 prompt 裡的答案 schema 去決定 evaluator 的答案。
**這是 A-v2 的錯，E 不得重犯。**

---

## §12 Unit of Analysis — Repeated LLM Calls 不等於 Independent Samples

TA-2 已有直接 evidence：**`temperature=0` ≠ guaranteed deterministic output**。

因此 **N = 10 calls ≠ 10 independent observations**。必須區分：

| 概念 | 定義 |
|---|---|
| **Replication** | 同一 condition 重跑 |
| **Independent observation** | 不同 stimulus / temporal contrast pair |

> **真正的 statistical unit 應優先是 `stimulus × temporal contrast pair`，
> 而不是 raw LLM call。**

---

## §13 Stimulus 必須成對設計，且避免自我污染

| 類 | Stimulus | Correct | Mismatch |
|---|---|---|---|
| Meal | 「等一下一起吃飯？」 | Sat 11:30 | Sat 23:30 |
| Meeting | 「等等開會。」 | Sat afternoon | Sat late night |
| Sleep | 「我有點累了。」 | late night | early afternoon |

🔴 **必須避免 stimulus 本身已包含答案**（例：「等一下吃**午餐**嗎？」）——那會污染 construct。

> Stimulus 必須 **temporally underspecified but pragmatically meaningful**。

---

## §14 Exact Timestamp 仍不得直接進 Prompt

沿用 TA-2 核心設計。**不要** `2026-09-01 14:22 EDT`；使用 human-scale 表達：

> 週六下午，約兩點半，今天已經過了一大段。

因為我們測的是 **human-scale temporal cognition**，不是 timestamp parsing。
Exact timestamp **可保留在 metadata 作 grounding**。

---

## §15 Threshold — 故意不決定

**順序不可反**：

```
Construct → Measurement → Threshold
```

本階段只建立：

```
Effect Size + Noise/Variance + Control Baseline + Replication Stability
                        ↓
              Candidate Thresholds（供 Owner 拍板）
```

❌ 禁止「0.6 看起來合理」→ 找證據支持 0.6。
❌ 已全部封死：`0.63`（12/19 舊母體產物）、`0.50`／`0.55`／`0.60`（禁止自創）。

### Threshold 未來至少要回答三個問題

| | 問題 |
|---|---|
| **T1 — Effect magnitude** | 實際 temporal contrast 要多大才算 meaningful？ |
| **T2 — Noise margin** | 必須超過多少自然 LLM variation？ |
| **T3 — Control separation** | Relevant effect 必須比 irrelevant temporal coloration 大多少？ |

### 建議的 threshold 形式（**非數值**）

```
Observed Relevant Effect  >  Baseline Noise  +  Minimum Practical Effect
Relevant Effect           >  Irrelevant Control Effect
```

**threshold 由 effect ＋ noise ＋ control 三者共同決定，不是單一 magic number。**

---

## §16 🔴 v1 evidence 的定位：prior evidence，**不是** acceptance baseline

> `ΔR = 1 / ΔI = 0 / DiD = 1`（N=6 真實 LLM 四臂）

**它可以告訴 E「值得繼續測」，但不能告訴 E「新 threshold 應該是多少」。**

> 否則又會**偷偷把上一個 measurement instrument 的產物帶進下一個 instrument**。

---

## §17 E Design Acceptance Gate（implementation 前必須全數通過）

| Gate | 條件 |
|---|---|
| **E0 — Construct validity** | 明確定義 temporal interpretation／**不依賴** lexical temporal mention／**不依賴** local attachment |
| **E1 — Causal contrast** | CORRECT / MISMATCHED / OFF baseline / IRRELEVANT control 齊備 |
| **E2 — Evaluation independence** | evaluator blind／A/B randomized／evaluator 不知道 condition label |
| **E3 — Unit of analysis** | stimulus-level independence 明確／repeated calls **不被假裝成** independent samples |
| **E4 — Measurement integrity** | raw responses preserved／evaluator output preserved／aggregation reproducible／no production mutation |
| **E5 — Threshold readiness** | effect metric defined／noise metric defined／control metric defined —— **但 threshold 本身尚未選定** |

---

## §18 兩階段切分

### E-Design（現在）

```
Construct → Experimental protocol → Scoring protocol
          → Independence → Noise model → Threshold derivation framework
```

**不跑 LLM。**

### E-Pilot（**需 Owner 批准 design 後**才做）

> **Pilot 的第一目的不是「證明 TA-2 PASS」，而是驗證這個 measurement instrument 本身是否工作。**

若 E-Pilot 發現 evaluator 也出現 generic context coloration／lexical bias／condition leakage，
**先修 measurement**。**不能看到一個漂亮的 Δ 就直接宣布 TA-2。**

---

## §19 下一步（非本檔範圍）

1. 本文件成為 **canonical design document**
2. 進行 **Bry / Cross-Model Design Review**
3. **只有 design contract 定下來**，才值得派 implementation 工單寫 E-Pilot

**目前不需要動 repo runtime。**

---

## §20 誠實邊界

- 本檔是 **DESIGN ONLY**：不含程式碼、不含實驗、不含門檻數值。
- 本檔**不**授權 E-Pilot implementation。
- 本檔**不**選擇任何 threshold。
- **E Design 的成功標準是「contract 站得住」，不是「門檻好通過」。**
- **Gate 2 全程維持 INCONCLUSIVE。**
