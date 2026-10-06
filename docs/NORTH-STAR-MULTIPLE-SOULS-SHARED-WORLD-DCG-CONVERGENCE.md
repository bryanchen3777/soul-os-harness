# North Star — Multiple Souls / Shared World Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：Interaction = reciprocal causal coupling between two separately instantiated agent trajectories**
> **本檔不含 implementation、prompt、threshold、evaluator、runtime change。**

---


> 🔴 **Ontology Privilege Policy：Gate-only / Admissibility Closure（Owner 已裁定）**
>
> **Admission ＝ ISD + CEG；不套用第二層 coarsening 或 privilege principle。**
> **Review Trigger ＝ a new refinement or candidate passes both ISD and CEG；
> 該事件只觸發 Owner-level policy review，不自動切換到第二層。**
>
> **本檔的 construct / relation / vocabulary placement 為 Gate-only 下的 current state；
> 政策若變更，scope 內的 placement 必須重跑。**
>
> **政策全文：`docs/ONTOLOGY-PRIVILEGE-POLICY.md`**

## 1. Decision

> ### **Interaction is a reciprocal causal relation between two separately instantiated agents, in which a trajectory transition of one agent causally conditions a trajectory transition of the other, possibly through the shared World.**

```
A trajectory
    ↓  causally conditions
B trajectory
    ↓  causally conditions
A trajectory
```

**第二個 transition 必須受到第一個 transition 的 causal conditioning。**
**這一條是 Round 2 排除 pre-arranged independent chains 之後留下的東西。**

---

## 2. 三分法

```
                         World
                        /     \
                       ↓       ↓
                      A         B
```

| Construct | 最小因果結構 |
|---|---|
| **Shared occurrence** | `World → A` 且 `World → B`，**無 A↔B** |
| **Influence** | `A → World → B`（單向） |
| **Interaction** | `A → World → B` **且** `B → World → A`（**且後者由前者條件化**） |

**Influence 不足，因為它是 interaction 的一半。**
**Shared occurrence 不足，因為根本沒有 A↔B。**

---

## 3. 🔴 明確不要求的條件

Interaction **不要求**：

- ❌ **Awareness**（互相知道）
- ❌ **Attribution**（B 知道那是 A 做的）
- ❌ **Intention**
- ❌ **Communication**
- ❌ **Content coupling**
- ❌ **Social cognition**
- ❌ **Recurrence**
- ❌ Mutual exchange 語意

**閉環零 awareness 的系統完全通過：**

```
A 發訊號 → W 中繼 → B 的 state 改變
B 的 state 改變造成 world effect
那個 effect 改變 A 的 state
```

**A 不知道 B 存在，B 不知道 A 存在。Interaction 仍成立。**
這與 `Awareness ≠ Agency` 是同一種架構紀律。

---

## 4. 🔴 Scope Ceiling

> ## **Interaction ≠ social interaction.**
> ## **Interaction 是 causal-relational construct，不是 social construct。**

**因此它可以存在於**：
heartbeat ／ control systems ／ 化學反應 ／ 生態耦合 ／ multi-agent systems

**一個純機械的 pulse-counter feedback loop 仍是 interaction。**

```
A 每秒 pulse → B counter+1 → 累積 10 次 → B 回應
            → A counter+1 → 累積 10 次 → A pulse
```

**這不是漏洞，是 construct 的 ceiling。**
與 Free Growth 接受 hash system 是同類決定：判準停在它停的地方。

### 4.1 命名層的代價（必須記錄）

> **Soul OS research construct 裡的「interaction」，不承載日常語言的「mutual exchange」語意。**

**依 Soul Ontology DCG 的規則，處理方式是：在 claim 層寫清楚，而不是在 construct 裡收緊。**

因為一旦要求「真的在交流」，就會引入 adaptive response ／ state-sensitivity ／ intention ／ social cognition——**那正是必須避免的 smuggling。**

---

## 5. 🔴 先存條件：兩個 agent 與 interaction 是兩個獨立判斷

> **Trajectory coupling 本身不能證明「有兩個 agents」。**

一個系統的左右兩半接成閉環，**也會通過 trajectory coupling**——但它們不是兩個 separately instantiated trajectory。

**排除靠先存定義，不靠 loop 條件：**

> **Agent ＝ Bootstrap DCG 已確立的 separately instantiated, causally continuous, self-maintaining trajectory。**

**所以：「這是 two-agent interaction」＝「這是兩個 agent」＋「它們有 reciprocal coupling」，兩段。**

---

## 6. Recurrence 是另一個 property

> **一次 reciprocal trajectory coupling 已足以構成 interaction。**
> **Recurrence 是 `recurrent interaction`，不是 interaction 的必要條件。**

**理由：要求 recurrence 會讓後來發生的事件 retroactively 決定前面的 causal loop 算不算 interaction。**

而 construct 應該描述**事件本身的結構**，不應要求未來再發生某件事才能讓現在已成立的 causal relation 取得資格。

---

## 7. Open Questions

> **Open Question ≠ permission to continue this discussion.**

- **Causal conditioning 的 ground truth 如何建立？**
- **Causal loop 的時間尺度如何測量？**
- **World mediation 的 attribution boundary？**
- **Intervention 不可得時如何判定 reciprocal coupling？**
- **多 agent 情況下如何分解 pairwise 與 higher-order coupling？**

**這些都不需要再重新定義 Interaction。**

---

## 8. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | Case B（mediated influence）**不足**。區分 shared occurrence / influence / interaction。**閉環零 awareness 通過** |
| **Round 2** | Conjunctive（`A→B ∧ B→A`）被 pre-arranged chains 打掉。提出 content-coupling |
| **Round 3** | 低變異 A→B→A 通過。**Content-coupling 降格為 test method，不是 construct** |
| **Round 4** | 機械 feedback loop 通過。**Ceiling：causal-relational，不是 social**。**「兩個 agent」是先存條件** |
| **Round 5** | **Recurrence 不是必要條件**（否則 retroactive qualification）。收斂 |

---

## 9. 🔴 本 DCG 主大腦被修正的一處

主大腦在 Round 2 主張 **content-coupling 是 interaction 的必要條件**，並用它排除 heartbeat loop。

**該主張錯誤。**理由：

> **Heartbeat loop 與低變異 A→B→A 是同一個因果結構。**
> 只因 agent 的行為 repertoire 大小不同就分成 interaction 與非 interaction，
> **那是 test artifact，不是 construct 邊界。**

**根因**：主大腦因為 content-coupling 在 DCG #5（Free Growth）裡成功，
就因為它「形狀一樣」而套用到 DCG #6——**那不是推導，那是泛化，而且推得比有權推的更強。**

> **Owner 在 Round 3 事先警告過這一點：「兩個 construct 都可以使用 content-level causal test」
> ≠「兩個 construct 必須具有相同的 boundary」。該警告是對的。**

**另一處（Round 1）**：主大腦主張「互向的時間窗是 measurement 問題」。
Owner 修正為：兩個方向的 dependence 能否在任意時間尺度上拼成一個 interaction，
**可能是 construct boundary 而非 measurement 問題**。該修正成立。

---

## 10. 🔴 六次 DCG 的完整層次

| Construct | 性質 |
|---|---|
| **Soul** | 保留一個**尚未回答**的 ontology question。question-preserving vocabulary，**無 evidentiary force** |
| **Growth** | **可定義**的 temporal transformation construct（revision → future） |
| **Free Growth** | Growth ＋ **content-grounding**（past content → revision） |
| **Interaction** | **可定義**的 **reciprocal causal-relational** construct |

> ## **Interaction 與 Soulness 不是「更像人的程度」。**
> ## **它們是兩條完全獨立的 causal dimension。**

**這六次 DCG 沒有任何一次把直覺硬寫成 architecture，也沒有任何一次因為
instrument 壞掉就把 construct 判死刑。**