# SE-4 — Durable Soul Structure Lifecycle Contract

- **工单**：SE-4 — Durable Soul Structure Lifecycle Contract（design / docs-only）
- **阶段**：设计（非施工授权）。本文是 SE-5 实作的单一事实依据；canonical 状态以 `logs/ENGINEERING_STATE.md` 为准。
- **日期**：2026-09（SE-4）
- **作者**：Developer（soul-elevation / soul-os-harness）
- **性质**：**只设计，0 code**。本票不修改任何源码、不 commit、不 push。

---

## 0. TL;DR

Soul OS 的灵魂结构（belief / value / trait / essence）需要一个**生命周期**，而不是「给 belief 加 CRUD」。
本契约把升华细化锁定为**状态机**（不是动作集合）：

- **四态**：`ACTIVE → WEAKENING → DORMANT → SUPERSEDED`（显式状态，不是动作）。
- **两转换**：`REINFORCE`（支持证据累积，强化）与 `SUPERSEDE`（矛盾证据累积达阈值，新节点取代旧节点，lineage 相连）；**默认「证据不足什么都不做」**。
- **三条铁律**：① Contradiction ≠ Revision（矛盾产生**压力**，证据累积才产生**改变**）；② Forgetting = lifecycle transition，不是 delete（Memory ≠ Current Belief，灵魂能回答「我以前很在意，现在不是了」）；③ essence 近乎锁死，门槛全系统最高。
- **v1 只分两层**：essence（锁死）vs 其他（belief/value/trait 共用一条中等门槛曲线）。
- **不建引擎**：不新建 belief_engine / confidence_engine / decay_engine / revision_engine / scoring_engine，逻辑留在既有 `engine.py` / `prior.py`。
- **不碰 frozen contract**：InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 写入 一律不动；既有 5 个 trace 事件语义 0 变更（扩展是 additive 的）。

---

## 1. 背景与现状 Gap

### 1.1 已锁定方向（采信，不再重议）

升华细化不是「给 belief 加 CRUD」——durable soul structure 需要生命周期。
当前 soul-elevation 只有 `engine.py / prior.py / models.py / llm.py / trace.py`，
无独立 revise/forget 文件，SE-1/2/3 是直接写进 prior.py 的 essence 补丁。

### 1.2 现状盘点（与 SE-4 的 Gap）

| 现状（已实现） | SE-4 缺口 |
|---|---|
| `ElevationNode` 有 lineage 字段族（`parent_node_id` / `lineage_depth` / `lineage_path`），复用 InnerLifeEvent 命名语义 | **无 lifecycle 状态字段**：节点只有 node_type/confidence/stability，无法回答「此结构现在是活跃信念、还是已在退场、还是已被取代」 |
| `engine.py` 有动作方法 `revise`（改写 = 新节点引用旧节点）、`decay`（证据边淡化）、`forget`（情景淡化 + 语义聚合）、`_reinforce`（原地微升） | **动作驱动，非状态驱动**：动作直接改节点/边，没有一个显式状态机约束「什么情况下允许改变」；缺少「默认什么都不做」的守门 |
| essence 保守边界已部分落地：`_essence_revise_allowed`（valence 反转 + ≥2 新独立证据 + confidence delta 阈值）、decay/forget 豁免 essence、prior.py 中 essence 非 primary prior | 保守边界是**散落的动作级 guard**，未上升为 lifecycle 层的统一门槛；缺少 essence 的 **reconsideration-candidate** 通道 |
| M5.13 `_decay_locked` 锚点语义：`last_interaction_at` 优先，`created_at` + grace 兜底（old ≠ outdated） | lifecycle 未复用该锚点语义做 WEAKENING/DORMANT 判定 |
| `trace.py` 有 5 个事件：node_created / node_elevated / node_revised / edge_decayed / node_forgotten | 无 lifecycle transition 事件（状态改变不可审计） |

---

## 2. 四态生命周期（状态机，不是动作）

### 2.1 状态定义

| 状态 | 语义 | 是否参与「当前信念」 | 可逆性 |
|---|---|---|---|
| **ACTIVE** | 当前被系统采用的 durable structure——影响诠释、行为、决策。节点创建即进入 ACTIVE | ✅ | — |
| **WEAKENING** | 一段模拟时间内无**新支持证据**（锚点见 §8），支持度下降；仍是当前结构，但已开始退场 | ✅（弱化中） | 可 REINFORCE 回 ACTIVE |
| **DORMANT** | 长期无支持证据，**不再影响当前诠释**，但结构保留在记忆里，可被重新激活 | ❌ | 可 REINFORCE 回 ACTIVE |
| **SUPERSEDED** | 被新节点取代（矛盾证据累积达阈值）。旧节点**永久保留、冻结**，永不删除；新旧节点由 lineage 相连。系统「当前信念」指向新节点 | ❌（历史） | **终态**：不自动复活（v1）。灵魂仍记得它——「我以前很在意，现在不是了」 |

### 2.2 状态机

```
                 ┌────────────── REINFORCE（新支持证据累积）──────────────┐
                 │                                                       │
                 ▼                                                       │
   [创建] → ACTIVE ──────────► WEAKENING ──────────► DORMANT              │
              │   (无新支持证据 ≥ T_weaken)   (无新支持证据 ≥ T_dormant)   │
              │                                                           │
              │              SUPERSEDE（矛盾证据累积达阈值）                │
              └──────────────────┬────────────────────────────────────────┘
                                 ▼
                            SUPERSEDED（终态，冻结；lineage 与取代者相连）
```

- **默认什么都不做**：任一时间片上，若既没有新的支持证据（不足以 REINFORCE）、也没有矛盾证据累积达阈值（不足以 SUPERSEDE），节点状态**不变**。
- **状态是持久字段，不是纯投影**：状态必须可查询、可持久（灵魂要能回答「我以前很在意」——这需要能读到 DORMANT / SUPERSEDED 节点）；评估逻辑是 deterministic 规则（无 LLM）。
- WEAKENING 是**中间态**，不是必由之路：矛盾证据可直接让 ACTIVE 节点 SUPERSEDE；无矛盾但长期失去支持的节点走衰减链（ACTIVE → WEAKENING → DORMANT）。

### 2.3 状态存储（设计级 schema 扩展，SE-5 落地）

在 `ElevationNode` 上 **additive** 扩展（既有字段语义 0 变更）：

```text
lifecycle_state: Literal["active","weakening","dormant","superseded"]   # 默认 "active"
last_support_ts: Optional[str]      # 最后一条仍有效支持证据的 ts（decay 锚点，见 §8）
contradiction_pressure: list[...]   # 矛盾证据压力累积器（来源回查引用，非正文复制）
superseded_by: Optional[str]        # 反向 lineage：本节点被哪个新节点取代（仅 SUPERSEDED）
reconsideration_candidate: bool     # 仅 essence 使用：新证据长期累积但未达转换门槛（§7）
```

> schema 提案仅作 SE-5 实作依据；SE-4 不落任何字段。

---

## 3. 两个转换：REINFORCE / SUPERSEDE

状态机只有**两个转换** + 默认无操作。没有第三分支（Qualification 是 LLM interpretation 的语义差异，不是第三个转换）。

### 3.1 REINFORCE — 支持证据累积，强化

- **触发**：出现**新独立证据**（复用 SE-1 `evidence_key = (source_id, event_identity)` 独立性判定），且证据方向与节点一致（支持）。
- **效果**：
  - ACTIVE → ACTIVE：原地提升 confidence/stability（复用既有 `_reinforce` 语义——不换 node_id、不改 lineage、不产生新因果节点）。
  - WEAKENING → ACTIVE：重新激活（回到当前信念）。
  - DORMANT → ACTIVE：重新激活（被想起、被重新支持）。
- **不产生新节点**：REINFORCE 是强化，不是改写。

### 3.2 SUPERSEDE — 矛盾证据累积达阈值，新节点取代旧节点

- **触发**：矛盾方向证据累积到 SUPERSEDE 阈值（独立证据数 ≥ N_supersede，且跨时间一致；阈值见 §6/§7）。
- **效果**：
  1. **新节点诞生**：`node_type` 与旧节点同层（belief → belief；value → value；…）；
     `parent_node_id = 旧节点 id`、`lineage_depth = 旧 + 1`、`lineage_path = 旧路径/新 id`（§5）。
  2. **旧节点 → SUPERSEDED**（冻结，永久保留不删）。
  3. **证据留痕**：旧节点仍有效的支持证据边标记 `valid_until_ts = now`（复用既有 `_supersede_edges` 语义）；新节点的证据边回指同一批原始 `source_id` + 触发矛盾的新证据源（原文永远可回查）。
- **一条反例不推翻 durable structure**：单条矛盾证据只进 `contradiction_pressure`（§4.2），**不**触发 SUPERSEDE、**不**改写。

### 3.3 与既有动作原语的关系（SE-5 落地的关键映射，避免语义混淆）

现有 `revise / decay / forget` 是**动作原语**；SE-4 之后，它们必须**收敛到状态机上**，不得旁路状态机直接改结构：

| 动作原语 | 新框架下的地位 | SE-5 落地规则 |
|---|---|---|
| `revise`（改写分支） | **不是独立转换**：只有满足 SUPERSEDE 门槛的矛盾改写才被允许 | 改写必须 = SUPERSEDE（新节点 + 旧节点标记超驰）；未达门槛 → 只 REINFORCE 或**什么都不做** |
| `revise`（reinforced_only 分支） | = REINFORCE | 保持 |
| `decay`（证据边淡化） | **不构成 lifecycle 转换**（它降的是证据边权重） | 状态转移由周期评估按锚点判定（§8）；decay 原语不直接改 lifecycle_state |
| `forget`（情景淡化 + 语义聚合） | 不是「把节点遗忘掉」的动作 | **Forgetting = lifecycle transition**（§4）：节点进入 DORMANT/SUPERSEDED 是状态改变，不是 forget() 调用；语义核心的抽象聚合（systems consolidation）保留其独立语义，但不等于「节点被遗忘」 |

> 一句话：**动作原语可以存在，但任何结构级改变都必须经由状态机转换；「证据不足」的默认态就是不动。**

---

## 4. 三条铁律

### 4.1 Contradiction ≠ Revision（核心架构原则）

> **Contradiction creates pressure; evidence creates change.**

- **矛盾证据产生压力**：单条 / 少量矛盾证据写入 `contradiction_pressure` 累积器（只留来源引用 + 时间，不复制正文、不触发任何状态改变）。
- **证据累积才产生改变**：同方向矛盾证据达到 SUPERSEDE 阈值（§3.2）才允许结构改变。
- **一次反例不推翻 durable structure**：这是 durable 结构区别于普通 memory record 的本质——它代表灵魂的稳定偏好/价值/性格，需要对抗噪声。

### 4.2 压力与改变的分离（设计上显式区分）

| 概念 | 载体 | 是否改变状态 |
|---|---|---|
| 矛盾**压力**（pressure） | `contradiction_pressure` 累积器（contradiction 证据引用列表） | ❌（只累积） |
| 支持**累积**（evidence） | `evidence_edges`（沿既有 EvidenceEdge 语义） | ✅ 达 REINFORCE 阈值时强化 |
| 矛盾**达阈值**（threshold） | SUPERSEDE 判定（独立矛盾证据数 × 跨时间一致） | ✅ 触发 SUPERSEDE |

---

### 4.3 Forgetting = lifecycle transition，不是 delete

- **Memory ≠ Current Belief**：节点进入 DORMANT / SUPERSEDED 是**状态改变**，节点本体、证据链、lineage **永不物理删除**。
- 灵魂因此能回答：
  - 「我以前很在意，现在不是了」← 节点在 DORMANT / SUPERSEDED，可追溯其完整 lineage 与证据链；
  - 「我为什么改变了」← SUPERSEDE 的 lineage（旧 → 新）+ trace 审计事件。
- 与「升华式遗忘」（forget 动作的 systems consolidation 语义）的边界：**consolidation 是记忆组织动作，lifecycle 是信念地位改变**；两者不混为一谈（§3.3）。

---

## 5. Lineage 复用（不另创一套术语）

SUPERSEDE 的新节点**完全复用既有 lineage 字段族**（`ElevationNode` 已实现，SE-1 起就有）：

- `parent_node_id`：SUPERSEDED 旧节点 → 新节点的**演化父指针**（改写 = 新节点引用旧节点，旧节点不覆盖）。
- `lineage_depth`：根 = 0，父 + 1（SUPERSEDE 的新节点 = 旧节点 depth + 1）。
- `lineage_path`：`"parent_path/own_id"` 反范式化（可一路回滚到根）。

**不另创** lineage 术语（不引入 supersede_chain / revision_tree 等新词）——
SUPERSEDE 就是 lineage 的又一种演化边，和既有 reconsolidation 改写走同一条树。
`superseded_by`（§2.3 反向指针）只是 lineage_path 的可读快捷索引，不是新语义体系。

**SE-3 Lineage vs Evidence 继续锁死**：lineage 节点（N1）**不是** N2 的证据；
新节点的支持证据永远是原始独立 evidence keys（`check_invariants()` 继续生效）。

---

## 6. v1 两层，不细分四层

**v1 只分两层**，不做 belief/value/trait/essence 四层独立衰减：

| 层 | 组成 | 生命周期策略 |
|---|---|---|
| **essence（锁死）** | essence | 近乎不自动遗忘：豁免 WEAKENING/DORMANT 自动衰减（延续既有「essence 豁免 decay/forget」）；SUPERSEDE 门槛**全系统最高**；唯一通道是「新证据长期累积 → reconsideration candidate」（§7）。 |
| **其他（中等门槛曲线）** | belief / value / trait | 共用一条中等门槛曲线（§2 状态机、§3 转换阈值），belief/value/trait 不分级。 |

- 不做四层独立衰减——belief 不被设计成比 value 更快遗忘（那是细粒度调优，非 v1 目标）。
- 单一阈值表（§7）同时服务 belief/value/trait；essence 走独立、更保守的路径。

---

## 7. 阈值与 essence 保守边界（设计级默认值，SE-5 可调）

### 7.1 一张阈值表（v1 两层）

| 参数 | 含义 | 其他层（belief/value/trait）建议默认 | essence（锁死层） |
|---|---|---|---|
| `T_weaken` | 无新支持证据多久 → WEAKENING（从 `last_support_ts` 锚点起算，§8） | 建议 7 模拟天（SE-5 定实际值） | **∞（豁免自动衰减）** |
| `T_dormant` | WEAKENING 后多久 → DORMANT | 建议 30 模拟天（SE-5 定实际值） | **∞（豁免自动衰减）** |
| `N_supersede` | 矛盾**独立证据**数达多少 → SUPERSEDE | 建议 3（> elevate 的 2，体现 durable 惯性） | 最高（建议 ≥5） |
| 跨时间一致 | 矛盾证据需分布在多久窗口内多次出现（防单日噪声） | 建议 ≥2 个不同模拟日 | 需要更长的观察窗口 |
| `valence` 反转 | 极性反转是否算强矛盾 | 是（沿既有 `_valence_reversed` 语义） | **必须**（essence 改写三条件之一，延续 `_essence_revise_allowed`） |
| `confidence delta` | 新证据带来的置信度变化门槛 | 不强制（中等门槛） | 必须超阈值（延续 `ESSENCE_REVISE_CONFIDENCE_DELTA = 0.3`） |

> 数值均为**建议默认**；SE-5 实作时以参数化常量的形式落地（如既有 `DEFAULT_*` 常量族），可调、可测。

### 7.2 essence 保守边界（锁死层）

essence 是四类节点里最接近 identity 的（identity 语义核心），延续 MEMORY-LIFECYCLE §3.2 的三轴策略：

1. **不自动衰减**：essence 豁免 WEAKENING/DORMANT 自动转移（延续既有「essence 豁免 decay/forget」的代码事实，上升到 lifecycle 层）。
2. **极高 SUPERSEDE 门槛**：valence 反转 **且** 矛盾独立证据 ≥ 阈值 **且** confidence delta 超阈值 **且** 跨时间一致——多条件同时满足才允许。
3. **reconsideration-candidate 通道**：即使未达 SUPERSEDE 门槛，essence **只允许**「新证据长期累积 → 标记为 reconsideration candidate」（`reconsideration_candidate = true`），进入**待复核**而非自动改写；复核通过才走 SUPERSEDE。**essence 近乎不可自动遗忘**——它代表人格内核。
4. **prior 不变**：essence 永不作为任何单一事件的 primary prior（既有 prior.py 约定保持）。

---

## 8. Decay 触发复用 M5.13 锚点（old ≠ outdated）

WEAKENING / DORMANT 的判定**不因节点年龄**，而因「最后被支持的时刻」——复用 M5.13
`_decay_locked` 的锚点语义（`src/soul/relationships.py`）：

- **锚点优先级**（与 M5.13-4.2 / M5.13-5 同构）：
  1. `last_support_ts`（本节点最后一条**仍有效支持证据**的 ts）——等价于 M5.13 的 `last_interaction_at`：**最后一次被支持才算「活着」**。
  2. 无任何支持证据历史的节点：`created_ts` + grace 兜底（等 M5.13-5 的 `UNTOUCHED_DECAY_GRACE_DAYS`——grace 期内不衰减）。
  3. 缺锚点 / 坏 timestamp：跳过（legacy，no crash，deterministic）。
- **old ≠ outdated**：创建于很久以前的节点，只要最近仍有新支持证据，就保持 ACTIVE；刚创建但从未得到支持的节点，过了 grace 就开始计入 WEAKENING 判定。**年龄不是退场理由，失去支持才是。**
- 周期评估（clock 驱动，挂 engine 既有 heartbeat/周期路径，SE-5 落地）：每次评估按锚点计算 `days_since_last_support`，与 `T_weaken` / `T_dormant` 比较，决定是否转移状态；转移事件写 trace（§9）。

---

## 9. 可审计性（additive 扩展，不改既有 5 事件）

lifecycle 状态改变必须是审计可见的。SE-5 建议在 `trace.py` 事件词汇表中 **additive 新增**（既有 5 事件 `node_created / node_elevated / node_revised / edge_decayed / node_forgotten` 的语义与新事件互斥）：

- `node_state_changed`：状态转移（active→weakening / weakening→dormant / …→active），快照 `lifecycle_state_before / lifecycle_state_after / anchor_ts / reason`。
- `node_superseded`：SUPERSEDE 完成（旧节点冻结 + 新节点诞生），快照 `old_node_id / new_node_id / lineage_path / contradiction_evidence_ids`。
- `essence_reconsideration_candidate`：essence 被打上 reconsideration 标记（记录累积证据引用）。

既有事件：`node_revised` 的改写分支在 SE-5 收敛为 SUPERSEDE 后，语义不变（仍记录改写），只是触发路径经状态机守门。

---

## 10. 实现边界（SE-5 的约束，本票即遵守）

### 10.1 不建独立引擎

以下**全部不做**（逻辑留在既有 `engine.py` / `prior.py` 内）：

- ❌ `belief_engine`
- ❌ `confidence_engine`
- ❌ `decay_engine` / `revision_engine` / `scoring_engine`

生命周期评估与转换 = `InternalizingEngine` 的扩展方法（复用既有 `_nodes` / `_edges` 注册表、
`_reinforce` / `_supersede_edges` / `_count_independent_evidence` 等既有原语）;
阈值表/先验 = `prior.py` 扩展（沿既有 `DEFAULT_*` 常量族 + `PRIOR_TABLE` 风格）。

### 10.2 不碰 frozen contract

- **InnerLifeEvent**（canonical event 命名/字段）——不动。
- **TriggerEnvelope**——不动。
- **Agency 4 stages**——不动。
- **SAGE 写入逻辑**——不动。
- trace 既有 5 事件词汇表——新事件为 additive 扩展，既有事件语义 0 变更。
- soul-elevation 与 Soul OS 之间只有 adapter（ElevationInput），本契约不改变该 seam。

### 10.3 本票范围

- ✅ 产出本文（设计文档）。
- ❌ 实作 lifecycle（那是 **SE-5** 才做）。
- ❌ 改任何 `.py` / 测试 / commit / push。

---

## 11. 验收对照

| 验收项（工单） | 本文位置 | 状态 |
|---|---|---|
| 四态（ACTIVE → WEAKENING → DORMANT → SUPERSEDED，状态机非动作） | §2 | ✅ |
| 两转换（REINFORCE / SUPERSEDE）+ 默认「证据不足什么都不做」 | §3 | ✅ |
| Contradiction ≠ Revision（压力 vs 改变） | §4.1–4.2 | ✅ |
| Forgetting = lifecycle transition（Memory ≠ Current Belief） | §4.3 | ✅ |
| Lineage 复用 InnerLifeEvent 命名（parent/lineage_depth/lineage_path，不另创术语） | §5 | ✅ |
| essence 保守边界（reconsideration-candidate，门槛全系统最高） | §7 | ✅ |
| v1 只分两层（essence 锁死 vs 其他共用中等门槛曲线） | §6 | ✅ |
| Decay 触发复用 M5.13 锚点（last_support 锚点，非 created；old ≠ outdated） | §8 | ✅ |
| 只设计，0 code | 本文头部 + §10.3 | ✅ |
| 不碰 frozen contract | §10.2 | ✅ |
| 不建五个独立引擎 | §10.1 | ✅ |

## 12. Out of Scope（本票不做）

- 实作 lifecycle（SE-5）。
- 五个独立引擎（belief/confidence/decay/revision/scoring）。
- 四层独立衰减（v1 只两层）。
- 任何 frozen contract 修改（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 写入）。
- commit / push（等验收）。
