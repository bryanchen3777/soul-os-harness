# CA-2 — **METHODOLOGICAL FREEZE POINT**

> **停牌者**：Owner（Bryan），2026-10-04 15:31
> **狀態**：CA-2 已完成「研究問題能否被乾淨地拆解」階段。
> **尚未進入**「如何量它」階段。
> **本檔是收斂記錄，不是 spec，不授權任何 implementation。**

---

## §1 停牌狀態

```
CA-2 — METHODOLOGICAL FREEZE POINT

Construct                              FROZEN
Ground Truth Machinery                 FROZEN

Mechanism ontology
  d₁ / d₂ / d₃                          LOCKED
  d₂ ≠ d₃                               LOCKED
  separable regions                     LOCKED
  B definition                          LOCKED  *
  attribution 3-state                   LOCKED
  B / Bᶜ → M*                           LOCKED

Intervention ceilings                   KNOWN

Open measurement questions
  B coverage / mass                     OPEN
  d₂ incremental elimination            OPEN
  d₃ incremental elimination            OPEN

Measurement-design choice
  include vs exclude B                  UNDECIDED

Runtime                                 UNTOUCHED
Pilot                                   UNTOUCHED
Architecture                            UNCHANGED
```

> **\* under current mechanism ontology**
> 不得升級為「不可能存在第三種纏繞機制」。

---

## §2 支撐文件

| 文件 | 內容 |
|---|---|
| `docs/CA2-CONSTRUCT-AND-GROUND-TRUTH-FREEZE.md` | **FROZEN**：construct boundary ＋ ground-truth machinery |
| `docs/CA2-MQ1-ANALYSIS-NOTES.md` | OPEN：Q1a/Q1b/Q1c、L1/L2/L3、堵通道悖論、L3 取得條件 |
| `docs/CA2-CONVERGENCE-FRAMEWORK-NOTES.md` | OPEN：三層分離、兩關判準、attribution 規則、separable region、B、coverage |

---

## §3 這一輪真正產出的東西

> **不是答案。而是把「哪些東西還不能被答案化」劃得非常清楚。**

### 已買到

- **Construct 已 FROZEN** —— CA-2 到底研究什麼，不會再偷偷漂移
- **Ground Truth machinery 已 FROZEN** —— persistent-store transition → necessary-cause closure → CAN/CANNOT
- **CAN / CANNOT 的 architecture-derived boundary 已站穩** —— 正確答案是什麼，不再靠研究者臨場判斷
- **d₂ ≠ d₃ 已有構造證明** —— 不需要任何實驗
- **兩個 dimension 都存在 separable region** —— 同樣是構造證明
- **B 可結構性描述** —— 且明確限定範圍
- **B 與 Bᶜ 都保留在 M\***
- **attribution 三態已鎖定** —— attributable / jointly eliminated / unmeasured
- **L1 / L2 / L3 / Q1c-2 的 ceiling 已知**
- **沒有把任何 behavioral result 偷升級成 truth-tracking 或 activation proof**

---

## §4 🔴 已踩過的坑（固定在研究史裡，不得重入）

| # | 坑 | 教訓 |
|---|---|---|
| 1 | `0.63 = 12/19` 移植到新母體 | **門檻不能跨母體移植** —— 比例守恆不等於語義守恆 |
| 2 | `∀m∈M\*, P(m)` 滑成 `P(T)` | survivor-class property ≠ target-mechanism property |
| 3 | 五個「維度」混了三種本體 | observability / architecture **不是** mechanism dimension |
| 4 | L1 角色錯置 | L1 是 **hypothesis-side falsification**，不是 alternative-side attack |
| 5 | 把 overlap 當 axis identity | **attribution 困難 ≠ 維度相同** —— 這是 category error |
| 6 | 把 `attribution unresolved` 當 `no-effect` | **歸不了因 ≠ 沒有新增 elimination power** |
| 7 | 「沒有第二種纏繞」講成宇宙級命題 | 必須限定 **under current mechanism ontology** |
| 8 | A1 `_precision()` docstring 與實作不一致 | 宣告與實作的一致性是一等驗收項 |
| 9 | agreement gate 未分層 | rule-forced agreement 與 independent signal **必須分開算** |
| 10 | 連續七輪「我以為的規則決定」其實每次都被推翻 | 保護性決策會反過來封死目標 |

> 這十項是這條線真正的產出。**它們的價值不在於避免了一個錯誤答案，在於避免了一類錯誤推理。**

---

## §5 未來要重新開始時，從這裡開始

> **不要**從「coverage 怎麼算」開始。
>
> 從這個 measurement-design choice 開始：
>
> ### 「我們願意犧牲什麼、要保留什麼？」
>
> 具體地：**item set 要不要排除 B。**
> - 排除 → attribution cleanliness ↑，target coverage ↓（可能整批排除最接近 T 的機制）
> - 保留 → coverage ↑，必須接受一塊 `attribution unresolved`

**那是 measurement design choice，不是證明題，而且目前未授權。**

---

## §6 本次停牌的邊界

- ❌ 沒有 evaluator
- ❌ 沒有 threshold
- ❌ 沒有 prompt
- ❌ 沒有 measurement spec
- ❌ 沒有 pilot
- ❌ 沒有 issue
- ❌ 沒有 implementation
- ❌ 沒有 architecture change

- ✅ **TA-2 Gate 2 維持 INCONCLUSIVE**
