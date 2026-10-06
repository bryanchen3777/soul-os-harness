# North Star — Multiple Souls / Shared World Boundary（**DCG 收斂：Round 5 / 5**）

> **Discussion Convergence Gate 產物**。5 輪完成，四項交付齊備。
> **Decision：World = a causally continuing process whose transition dynamics are not reducible to any Agent's trajectory, while Agents may initialize or condition its state without constituting its dynamics.**
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

> ### **World is a causally continuing process whose transition dynamics are not reducible to any Agent's trajectory, and which may be initialized or conditioned by Agents without being constituted by their trajectories.**

**中文**：World 是一個具有因果延續性的 process，其 transition dynamics **不可還原為任何 Agent 的 trajectory**；它可以被 Agent 初始化或影響，**但不由 Agent trajectory 本身構成**。

```
                World
                  │
        ┌─────────┴─────────┐
        │                   │
 causal continuity     own transition dynamics
        │          not reducible to any Agent
        │
        └─────────┬─────────┘
                  │
     Agent MAY initialize / condition
                  │
              but does NOT constitute
```

---

## 2. 🔴 Construct 定義 vs 最小可判定 witness

> **Owner 在 Round 5 的關鍵修正**：agent-independent transition 是 **witness**，**不是** ontology 定義。

**理由**：若把「至少存在一次不取 agent input 的 transition」寫成定義，
一個**恰好偶爾脫離 agent 一步的 stateful transducer** 就會通過那個存在量詞。

**所以：**

| | 角色 |
|---|---|
| **「transition dynamics 不可還原為任何 Agent trajectory」** | **construct 定義** |
| **「至少存在一次 agent-independent transition」** | **最小可判定 witness** |

**witness 用來區分 `Agent image / stateful transducer` vs `World process`，
但不得被當成完整本體條件。**

---

## 3. 🔴 Agent-conditioned initialization ≠ Agent-determined World dynamics

**世界可以由 Agent 設定初始條件：**

```
A 把球放在某個位置   →   W₀ = I(A₀)
```

**這不會讓 World 變成 Agent 的 image，因為那只描述 World 如何開始，
不是 World 如何持續演化。**

**球可以是 A 放下去的，但球後續的運動仍然屬於 World dynamics。**

---

## 4. 最終案例表

| Case | Persistent | Agent 可初始化 | Own dynamics | **World** |
|---|---|---|---|---|
| `W = encode(A.state)` | ✔ | ✔ | ❌ | ❌ |
| Stateless wire | ❌ | — | ❌ | ❌ |
| Shared cache（所有 transition 都由 Agent write） | ✔ | ✔ | ❌ | ❌ |
| `hash(A, W)`（每步都需 A） | ✔ | ✔ | ❌ | ❌ |
| **重力 / 熱擴散** | ✔ | ✔ | ✔ | ✅ |
| **Weather / News / Time progression** | ✔ | ✔／可受 Agent 影響 | ✔ | ✅ |

> **stateful ≠ World。** W 有 persistence、有 carry-over、固定 A 改變 W 初值會造成不同 future，
> **這三項都不足以構成 World。**

---

## 5. 🔴 Shared World ≠ Shared Mind

```
A ──┐
    ├── World ──┐
B ──┘           │
                ├── A / B trajectories
```

**共享同一 World，仍然保持：**
**分離的 Agent trajectory ／ 分離的 InnerLifeEvent ／ 分離的 memory ／ 分離的 identity**

> **Shared World 不要求任何形式的 mind merging。**

---

## 6. 🔴 三個 causal construct，彼此獨立

```
Agent
  │
  │ trajectory
  ▼
World
  │
  │ mediation / causal evolution
  ▼
Agent

Agent ↔ Agent
      │
      └── Interaction
```

| Construct | 定義 |
|---|---|
| **Agent** | 自己的 causally continuous trajectory（separately instantiated, self-maintaining） |
| **World** | 非 Agent-image 的 causally continuing process |
| **Interaction** | 兩個 Agent trajectory 之間的 reciprocal causal coupling |

> **三者不是「越來越像人」的階梯，而是三個不同的 causal construct。**

**Shared World 不必然產生 Interaction** —— World 是 causal substrate，Interaction 是另外一條
reciprocal causal dimension。**兩者獨立。**

---

## 7. Open Questions

> **Open Question ≠ permission to continue this discussion.**

- **World dynamics 的「不可還原」在實際系統中如何 operationalize？**
- **一個極度複雜但仍可被完整表示為 Agent trajectory 的 process，何時算 image 而非 World？**
- **World 是否必須對 Agent 產生 causal influence？**（本輪未證明必要，**不加入 construct**）
- **World 是否必須有 spatiality、rendering、virtual physics 或 geographical structure？**（不需要）
- **Shared World 是否會產生 interaction？**（不一定）

**全部留給 measurement / architecture / 更精確的 causal attribution。**

---

## 8. Non-Claims

**本輪沒有證明：**

- ❌ World 具有自主性
- ❌ World 有 agency
- ❌ World 有 awareness
- ❌ World 是物理世界
- ❌ World 是虛擬世界
- ❌ World 有 consciousness
- ❌ Shared World 會產生 Soulness
- ❌ Shared World 必然產生 Interaction
- ❌ World 的存在具有任何 metaphysical status

> **「World」是 research construct，不是由這個 construct 自動證明某種 ontology 真實存在。**

---

## 9. 五輪的推進路徑

| 輪次 | 內容 |
|---|---|
| **Round 1** | 三個 case 塌成兩個；判準是 **stateful mediation**。World ＝ Agent 的補集 |
| **Round 2** | World 需要 **persistence**，且那是「跨 episode 存續」，**不是** Agent 的 causal continuity。**write-then-vanish 是 message** |
| **Round 3** | 「非 Agent」不足（會變 bookkeeping artifact）；正確判準是 **有非-image dynamics** |
| **Round 4** | 主大腦的「無 agent action 也會變」**過度簡化**（混淆初始化與持續依賴）；Owner 的 counterfactual 也抓不到 transducer。**兩者合併才是完整判準** |
| **Round 5** | **「至少一次」是 witness 不是 definition**。Construct 定義在「不可還原為任何 Agent trajectory」 |

---

## 10. 🔴 本 DCG 主大腦被修正的兩處

| # | 主大腦寫下 | 問題 |
|---|---|---|
| 1 | 「Case B 塌縮成 Case A」 | 用了**未建立的 World-identity 判準**（causal equivalence class）。**互相決定只 relating 兩個 state variables，不會合併它們。** |
| 2 | 「不給 agent action，看 World 是否仍會變」 | 混淆了 **agent 對初始狀態的貢獻** 與 **agent 對每次 transition 的貢獻**。會誤殺「agent 設初值、之後 World 自 dynamics」的真 World |

**兩者都是同一個病：證據夠好時預設了一個尚未建立的判準。**
**這是主大腦在這七次 DCG 中最常犯的結構性錯誤。**

---

## 11. 🔴 七次 DCG 總覽

| DCG | 結果 |
|---|---|
| Bootstrap / Free Growth | structural boundary 可定義 |
| Soulness / Persistent Agent | **UNIDENTIFIABLE under current observation surface** |
| Soul Ontology / Construct | Soul ＝ question-preserving vocabulary，**無 evidentiary force** |
| Growth / Adaptation | structural construct 可定義 |
| Free Growth / External Determination | content-grounded construct 可定義 |
| Multiple Souls / Interaction | reciprocal causal-relational construct 可定義 |
| **Shared World** | **non-Agent-image causal process 可定義** |

> **Soul 是研究問題；Agent / Growth / Free Growth / Interaction / World 是可定義的 causal constructs。**
> **五個 constructs 沒有一個可以反過來充當 Soulness 的證據。**