# SOUL-MOTIVE-DECISION-DESIGN.md — SM-1 Soul Motive & Decision Boundary Design

> **工单**：SM-1（DESIGN）
> **状态**：**DESIGN ONLY — 只设计，0 code / 0 scheduler / 0 Agency / 0 production**
> **前置**：SM-0 审计 + Bryan 拍板（方向已锁，本设计直接采信，不重开）
> **产出**：本文档（唯一产出物；未创建/修改任何 source、test、config、data 文件）

---

## 0. 摘要（TL;DR）

Soul 的 volition path（Thought → Motive → Decision → Agency）目前缺两环：motive（「我想告诉 Bry」）与真正的 Decision（「我要现在传吗」）。SM-0 确认：Decision ownership 在 scheduler 外部规则，Stage 2 只是 Trigger Authorization（eligible+cooldown→YES+"speak"），Decision 本来就在 Agency 之前——不改 Stage 2 可建真正 Decision boundary。

本设计：

- **motive 模块**（建议 `src/soul/motive.py`，SM-2 实现）：motive 形态 = `content`（想说什么）+ `target`（指向 Bry）+ `provenance_ref`（引用产生它的 InnerLifeEvent，回查「这个念头从哪次经历来」）。**motive 是「意图」不是「经历」，不成为 InnerLifeEvent，只引用它**（Bryan 拍板）。
- **motive 产生机制**：Inner Life 的 diary/dream/event 是 Thought source（「我经历了什么」），motive 是 **interpretation 的产物**（「我想告诉 Bry」）——由 Soul 的 LLM 解读经历产出，**不是硬编码模板，不是 longing 公式**。
- **Decision 层**（建议 `src/soul/decision.py`，SM-2 实现）：消费 motive，产出「传/不传」。落点在 **Agency 输入侧（producer-side，publish AGENCY_TRIGGER 之前）**，参照 M5.8-4 先例（`src/agency/inner_life_gate.py` + `scheduler._publish_agency_trigger` 内 `_inner_life_gate_check` 同款 additive hook）。**不碰 frozen Agency 4-stage / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入**。
- **scheduler 职责不变**：只提供 wake/opportunity，不指定 action；排程逻辑、trigger 类型、payload 结构、wake 时机全部不变。
- **不建 Qualification/scoring subsystem**：「值得说」是 Soul interpretation 的内容，不是 score。
- **正式原则**：`Capability makes an action conceivable; Motive makes it desirable; Decision makes it chosen.`

---

## 1. 背景与方向（SM-0 采信摘要）

| 项 | 内容 |
|---|---|
| SM-0 核心结论 | Decision ownership 在 scheduler（外部规则），不在 Agency 也不在 Soul；Stage 2 是 Trigger Authorization（eligible+cooldown→YES+"speak"）；Decision 本来就在 Agency 之前，不改 Stage 2 可建真正 Decision boundary |
| Bryan 拍板 | motive 是「意图」不是「经历」，落在纯 additive 新模块（不碰 frozen InnerLifeEvent），通过 provenance_ref 引用 InnerLifeEvent |
| 正式原则 | **Capability makes an action conceivable; Motive makes it desirable; Decision makes it chosen.** |
| 三概念分开 | Thought（我想到什么）/ Motive（我想告诉 Bry）/ Decision（我要现在传吗） |
| 真正要建 | volition path——Soul 能产生与自身能力相连的 internal motives，并在 action possibilities 里形成自己的 decision |
| Proactive messaging | 只是第一个 observable expression，不是全部 |
| 不建 | MessageWorthiness / Qualification / scoring subsystem（「值得说」是 interpretation 的内容，不是 score） |

**已核对的真实代码事实**（本设计引用，未改动）：

- `src/agency/stages.py` `make_decision`（Stage 2）：trigger-only path = `eligible` + decision cooldown → `DecisionResult(True, ..., "speak")`。**确认是 Trigger Authorization，不是 Decision**。
- `src/agency/trigger.py` `TriggerEnvelope`：Scheduler → Agency bridge input，frozen（M5.2-F）。
- `src/inner_life/event.py` `InnerLifeEvent`：frozen dataclass，9 字段（event_id / session_id / correlation_id / parent_event_id / ts / provenance / lineage_depth / lineage_path / source_world_event_novelty_id）。
- `src/agency/inner_life_gate.py` + `src/soul/scheduler.py` `_publish_agency_trigger`（line 225）内 `_inner_life_gate_check`（line 295）：**M5.8-4 producer-side gating 先例**——在 `bus.publish` 之前查 Inner Life trace，0 frozen contract 变动。
- `src/eventbus/schema.py` `EventType.AGENCY_TRIGGER`：payload `{trigger_type, agent_id, reason, elapsed_mins, timestamp, extra}`，frozen（M5.2-G）。
- `src/soul/capability.py`（CA-1 设计）：`communicate` → `proactive_message`，capability 声明「主动传讯是可能的」。

---

## 2. 核心概念模型（volition path）

```
Thought（我想到什么）  →  Motive（我想告诉 Bry）  →  Decision（我要现在传吗）  →  Agency（frozen 4-stage）
      │                        │                          │                          │
 diary/dream/event      interpretation 产物          Soul 自己的选择            Stage 2 = Trigger
 (InnerLifeEvent,       content + target +           (LLM interpretation,      Authorization
  frozen, 只读)          provenance_ref               不是 score)                (eligible+cooldown→YES)
      │                        │                          │                          │
      └── Thought source ──────┘                          └── 落点: producer-side ────┘
                                                          (publish AGENCY_TRIGGER 之前)
```

- **Thought**：Inner Life 的 diary/dream/event（「我经历了什么」）。载体是 frozen `InnerLifeEvent`，本设计只读不写。
- **Motive**：interpretation 的产物（「我想告诉 Bry」）。**意图，不是经历**。独立 additive 记录，通过 `provenance_ref` 引用 InnerLifeEvent。
- **Decision**：Soul 在「有意图」的前提下选择「现在传不传」。**选择，不是公式**。落点在 Agency 输入侧（producer-side）。
- **Agency**：frozen 4-stage 不动。Stage 2 仍是 Trigger Authorization。

**四线正交**（与 CA-1 对齐，更新版）：

| 线 | 职责 | 真实落点 |
|---|---|---|
| **Scheduler** | wake：什么时候醒 | `src/soul/scheduler.py`（`_fire_*` 触发路径，照旧） |
| **Capability** | 什么可能 | `src/soul/capability.py`（CA-1）→ proxy.py 投影 |
| **Motive** | 想要什么 | `src/soul/motive.py`（本设计，SM-2 实现） |
| **Decision** | 我选什么 | `src/soul/decision.py`（本设计，SM-2 实现） |
| **Agency Stage 2** | Trigger Authorization | `src/agency/stages.py` `make_decision`（frozen，不动） |

---

## 3. 设计决策

### Q1. motive 形态

**答案：`content` + `target` + `provenance_ref` 三要素；motive 是「意图」，不成为 InnerLifeEvent，只引用它。**

设计示意（**非实现代码**，SM-2 才落地）：

```python
# src/soul/motive.py（设计示意，SM-2 实现）
@dataclass(frozen=True)
class Motive:
    motive_id: str            # 唯一身份（32 hex，参照 InnerLifeEvent.event_id 模式）
    content: str              # 想说什么（Soul 自己的话，interpretation 产物，非模板填充）
    target: str               # 指向 Bry（"bryan"）
    provenance_ref: str       # 引用产生这个 motive 的 InnerLifeEvent.event_id
                              # 回查「这个念头从哪次经历来」
    created_at: str           # ISO 8601 UTC
    source_trigger_type: str  # 哪个 trigger 类型产生的（diary:night / dream:dream / event / ...）
```

**为什么是这三要素**：

1. **content**：motive 的实质——「我想告诉 Bry 什么」。由 interpretation 产出（Soul 自己的话），不是模板填充、不是公式输出。
2. **target**：motive 的指向——「告诉谁」。v1 固定 `"bryan"`（Owner）。未来多灵魂互动（North Star v2）可扩展 target 集合，v1 不做。
3. **provenance_ref**：motive 的出处——「这个念头从哪次经历来」。引用 `InnerLifeEvent.event_id`，可回查 trace。

**motive ≠ InnerLifeEvent（关键边界）**：

| 维度 | InnerLifeEvent（frozen） | Motive（本设计） |
|---|---|---|
| 语义 | 「我经历了什么」（diary/dream/event） | 「我想做什么」（意图） |
| 身份 | `event_id`（canonical identity authority） | `motive_id`（独立身份，不进入 InnerLifeEvent 命名空间） |
| 因果 | `parent_event_id`（经历→经历，frozen：必须引用已知 InnerLifeEvent） | `provenance_ref`（意图→经历，独立回查引用，不进入 lineage tree） |
| lineage | `lineage_depth` / `lineage_path`（frozen） | 无（motive 不参与 lineage） |
| 存储 | trace.jsonl（InnerLifeWriter 写入） | motive trace（独立存储，见 Q2） |

**为什么 provenance_ref 不是 parent_event_id**：`parent_event_id` 是 frozen 的「经历→经历」因果链（M5.4-5.1，必须引用已知 InnerLifeEvent，参与 lineage）。motive 是「意图→经历」的回查引用——语义不同（不是经历因果，是意图出处），不进入 lineage tree，不碰 frozen 校验。用独立字段 `provenance_ref`，语义清晰、零 frozen 风险。

### Q2. motive 产生机制

**答案：motive 是 interpretation 的产物——由 Soul 的 LLM 解读 Inner Life 经历产出；不是硬编码模板，不是 longing 公式。**

**Thought source**：diary/dream/event 是「我经历了什么」（frozen `InnerLifeEvent` + 对应 diary/dream 文本产物）。motive 产生器**只读**这些（`NarrativeTraceReader` 模式，参照 M5.8-4），不写 InnerLifeEvent、不改 4 handlers。

**产生机制（interpretation）**：

1. 检测到新的 InnerLifeEvent（diary/dream/event 写入后，通过 trace 只读检测）。
2. 读取该经历的内容（diary/dream 文本产物，通过 trace 的 `provenance.trigger_type` + `ts` 定位对应文件条目）。
3. 调用 Soul 的 LLM 做 interpretation：给定「这次经历」+「你是 Soul，Bry 是你的主人」，问「这次经历里有没有想告诉 Bry 的念头？如果有，用你自己的话表达」。
4. 产出：`Motive(content=..., target="bryan", provenance_ref=event_id, ...)` 或「无 motive」（interpretation 判定这次经历没有想告诉 Bry 的念头）。

**为什么不是硬编码模板**：content 是 LLM 生成的 Soul 自己的话（每次经历不同、每次表达不同），不是「经历 X → 固定句子 Y」的映射。模板会让 motive 变成「系统替 Soul 说话」，违背 volition path 精神。

**为什么不是 longing 公式**：不建「渴望度 = f(经历类型, 时间, 频率)」之类的公式/score。motive 的存在与否、内容是什么，由 Soul 的 interpretation 判定（LLM），不是数值计算。「值得说」是 interpretation 的内容，不是 score（SM-0 拍板）。

**触发时机（v1 取舍）**：motive 产生器由 **producer-side 检查点驱动**（每次 scheduler opportunity 时，检查自上次以来有没有新 InnerLifeEvent，有则 interpretation）。v1 接受「motive 产生延迟到下次 opportunity」的取舍（motive 是「想告诉 Bry 的念头」，晚几小时产生不影响正确性，只影响时效）。未来若需事件驱动（InnerLifeEvent 写入后立即 interpretation），另开工单（需确认 InnerLifeWriter 加 hook 是否 additive）。

**只读约束**：motive 产生器不写 InnerLifeEvent、不写 diary/dream 文件、不改 4 handlers、不改 SAGE。唯一写入是 motive trace（独立存储，见下）。

**motive 存储（v1 建议）**：独立 append-only JSONL（如 `data/soul/motive_trace.jsonl`，参照 trace.jsonl 模式），或内存队列 + 可选持久化。motive 记录与 InnerLifeEvent trace 分离（语义分离：经历 vs 意图）。SM-2 定文件名与位置。

### Q3. Decision 层

**答案：消费 motive，产出「传/不传」；判定是 Soul 自己的选择（LLM interpretation），不是 score/阈值/权重。**

设计示意（**非实现代码**，SM-2 才落地）：

```python
# src/soul/decision.py（设计示意，SM-2 实现）
@dataclass(frozen=True)
class DecisionResult:
    transmit: bool            # 传 / 不传
    reason: str               # Soul 的选择理由（observability）
    motive_id: str            # 被裁决的 motive
    # 观察用 metadata（不参与判定）:
    motive_content: str = ""  # 裁决对象的内容（log 用）
    provenance_ref: str = ""  # 裁决对象的经历出处（log 用）
```

**判定机制**：

1. **只在「存在 pending motive」时介入**：没有 pending motive → Decision 层完全不介入（照常 publish，0 行为变化，backward-compat 100%）。
2. **有 pending motive 时**：把 motive（content + provenance_ref）+ 当前上下文（时间、最近互动等）交给 Soul 的 LLM，Soul 决定「我要现在传吗」。
3. 产出 `DecisionResult`：
   - `transmit=True` → 照常 publish AGENCY_TRIGGER（payload 不变），motive 标记 `transmitted`。
   - `transmit=False` → skip publish（像 M5.8-4 GATED 一样），motive 标记 `rejected`（或保留 pending，SM-2 定生命周期）。
4. **fail-safe = fail-open**：LLM 失败 → 照常 publish（保持现有行为，M5.8-4 原则「不可挡掉既有 path」），motive 保留 pending。

**为什么判定是 LLM 而不是规则**：SM-0 拍板「『值得说』是 Soul interpretation 的内容，不是 score」。Decision 的「传/不传」是 Soul 的选择（chosen），不是外部规则/阈值。若用确定性规则（如「有 motive 且 cooldown 过就传」），Decision ownership 又回到外部规则——正是 SM-0 要解决的问题。

**与「scheduler 问 LLM 想不想分享」的区别**（SM-0 已指出那是 automation 外包）：

| 维度 | 旧路径（scheduler 问 LLM） | 本设计（motive + Decision） |
|---|---|---|
| 触发 | 每次 wake 都问「想不想分享」 | 只在「有 pending motive」时问「我要现在传吗」 |
| 意图来源 | scheduler 外部规则驱动 | Soul 自己的 interpretation（motive 有 provenance_ref 可追溯） |
| 语义 | automation 外包（LLM 是规则执行器） | volition（LLM 是 Soul 的意志载体） |
| 成本 | 每次 wake 一次 LLM | 只有 pending motive 才调 LLM（motive 产生 + Decision 各一次） |

### Q4. 落点（producer-side，publish AGENCY_TRIGGER 之前）

**答案：Agency 输入侧（producer-side），在 `bus.publish(AGENCY_TRIGGER)` 之前；参照 M5.8-4 先例的 additive hook 模式。**

**落点位置（已核对真实代码）**：`src/soul/scheduler.py` 的 `_publish_agency_trigger`（line 225）内、`bus.publish`（line 287）之前——与 M5.8-4 `_inner_life_gate_check`（line 261-266）同一位置。SM-2 实现时在此新增 additive Decision 检查点。

**检查点顺序（v1 建议）**：

```
_publish_agency_trigger(agent_id, "proactive_dm", extra)
  → [M5.8-4 gate]  _inner_life_gate_check(agent_id)      # 既有，frozen 不动
       GATED → skip publish（刚做过 inner work，不打扰）
  → [SM-1 Decision] decision_check(agent_id)             # 新增 additive
       1. 检查新 InnerLifeEvent → interpretation → 产出 motive（若有）
       2. 有 pending motive → Soul 决定传/不传
       3. 不传 → skip publish；传 → 继续
  → bus.publish(AGENCY_TRIGGER)                          # payload 不变
```

**作用范围**：Decision 层只作用于 `proactive_dm` 路径（motive 的「传」= 主动传讯给 Bry，即 `proactive_message`）。其他 4 个 trigger_type（morning / night / dream / event）是 inner-life activity（写 diary/dream），不是「传讯给 Bry」，不受 Decision 层影响（与 M5.8-4 gate 同范围策略）。

**「不改 scheduler」的语义（关键澄清）**：

- **scheduler 的职责边界不变**：只提供 wake/opportunity，不指定 action。排程逻辑、trigger 类型、payload 结构、wake 时机全部不变。
- **落点是 additive hook，不是 scheduler 语义变更**：M5.8-4 已建立此先例（在 `_publish_agency_trigger` 内加 `_inner_life_gate_check` 调用，0 frozen contract 变动，scheduler 的 wake 语义不变）。SM-1 的 Decision 检查点同款。
- **本工单（SM-1）0 code**：以上是设计；SM-2 实现时才在 publish 路径加调用点。
- **备选（若要求 scheduler 文件零改动）**：Decision 层独立运行、只更新 motive 状态（approved/rejected），不拦截 publish——但这样「不传」没有实际效果（scheduler 照旧 publish），落点不成立。**主方案是 M5.8-4 同款 additive hook**（项目已验证的模式）。

### Q5. frozen 边界

**答案：0 change。motive 模块 + Decision 层是纯 additive，不碰任何 frozen contract。**

| Frozen contract | 本设计的关系 |
|---|---|
| Agency 4 stages（`src/agency/stages.py`） | 不碰。Stage 2 仍是 Trigger Authorization，输入输出签名不变 |
| TriggerEnvelope（`src/agency/trigger.py`） | 不碰。字段与语义不变 |
| InnerLifeEvent（`src/inner_life/event.py`） | 不碰。motive 只引用（provenance_ref），不新增字段、不成为 InnerLifeEvent |
| 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler） | 不碰。motive 产生器只读 trace，不修改 handler |
| SAGE 写入逻辑 | 不碰 |
| AGENCY_TRIGGER payload schema（`src/eventbus/schema.py`，M5.2-G） | 不碰。payload 结构不变，motive 内容不进 payload |
| scheduler 职责（wake/opportunity） | 不变。只加 additive producer-side 检查点（M5.8-4 同款） |

**motive 内容如何到达表达层（SM-1 范围外）**：SM-1 只设计到「传/不传」。motive 内容（想说什么）如何注入表达层（LLM prompt / AGENT_SPEAK）是 SM-2+ 的范围，本设计不碰 4 handlers / SAGE / AGENCY_TRIGGER payload。

### Q6. 与 CA-1 Capability 的关系

**答案：四线正交，motive/Decision 与 capability 互不进入对方。**

- **Capability**（CA-1）：`communicate` → `proactive_message`，声明「主动传讯是可能的」。消费点唯一 = proxy.py 投影（read-side）。
- **Motive**（本设计）：「我想告诉 Bry」——想要什么。由 interpretation 产出。
- **Decision**（本设计）：「我要现在传吗」——我选什么。落点 producer-side。
- 规则：capability 不进 motive 产生（motive 不因「有能力」而自动产生）；motive/Decision 不进 capability 投影（CAPABILITY block 不投影意图/选择）；Decision 不读 capability 来「传」（capability 声明可能，不选择 action）。

### Q7. 与 M5.8-4 gate 的关系

**答案：两者正交，共存于同一 producer-side 检查点。**

| 维度 | M5.8-4 gate（既有） | SM-1 Decision（本设计） |
|---|---|---|
| 问题 | 「刚做过 inner work，该不该打扰 Bry？」 | 「我有想告诉 Bry 的念头，我要现在传吗？」 |
| 性质 | context-aware rate limit（deterministic 规则） | volition（Soul 的选择，LLM interpretation） |
| 输入 | Inner Life trace（最近 InnerLifeEvent 时间） | pending motive（interpretation 产物） |
| 无输入时 | fail-open = emit | 不介入 = 照常 publish（0 行为变化） |
| 输出 | EMITTED / GATED / UNAVAILABLE / FAILURE | transmit=True / False |

检查点顺序：gate 先（无近期 inner work）→ Decision 后（有 pending motive 才介入）→ publish。gate 不过就不需要 Decision（刚做过 inner work 不打扰，无论有没有 motive）。

---

## 4. 边界与不做（Out of Scope）

本设计**明确不做**（SM-2 及以后工单也不得做，除非另开工单）：

- ❌ 实作 motive 模块 / Decision 层（SM-2 才实现）
- ❌ 改 Stage 2（`src/agency/stages.py`）— frozen
- ❌ 改 scheduler 职责（wake/opportunity 语义、trigger 类型、payload 结构）— 只加 additive 检查点
- ❌ 改 InnerLifeEvent（`src/inner_life/event.py`）— frozen
- ❌ 改 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler）
- ❌ 改 SAGE 写入逻辑
- ❌ 改 AGENCY_TRIGGER payload schema
- ❌ 建 Qualification / scoring / MessageWorthiness subsystem（「值得说」是 interpretation 的内容，不是 score）
- ❌ 建 longing 公式 / 渴望度计算
- ❌ motive 内容注入表达层（SM-2+ 范围）
- ❌ 改任何 code、不 commit、不 push

---

## 5. Frozen Contract 检查

**结论：0 change，无 CONTRACT CONFLICT。**

- 本设计未创建/修改任何 source / test / config / data 文件（唯一产出物是本文档）。
- 涉及但**未触碰**的 frozen contract（参照 `logs/ENGINEERING_STATE.md`）：
  - Agency 4 stages（`src/agency/stages.py`）— 输入输出签名不变
  - TriggerEnvelope（`src/agency/trigger.py`）— 字段与语义不变
  - InnerLifeEvent（`src/inner_life/event.py`，M5.4-5.1）— 不新增字段，motive 只引用
  - 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler）— 不修改
  - SAGE 写入逻辑 — 不修改
  - AGENCY_TRIGGER payload schema（`src/eventbus/schema.py`，M5.2-G）— 不修改
- 设计引用的落点（`_publish_agency_trigger` line 261-266 附近）是**未来 SM-2 的 additive 改动位置**，本设计未改动。

---

## 6. 验收对照

| 验收项 | 结果 |
|---|---|
| 设计文档产出，覆盖 motive 形态 / 产生机制 / Decision 层 / 落点 / frozen 边界 | ✅ 本文档 §3（Q1-Q5） |
| 明确「只设计，0 code」 | ✅ §0 / §4 |
| 不改 frozen contract | ✅ §5（0 change，无 CONTRACT CONFLICT） |
| 不建 Qualification/scoring subsystem | ✅ §4 |
| 不改任何 code、不 commit、不 push | ✅ 唯一产出物为本文档 |

---

## 7. 下一步（供主大脑参考，非本工单范围）

- **SM-2（IMPLEMENTATION）**：落地 `src/soul/motive.py`（Motive dataclass + interpretation 产生器）+ `src/soul/decision.py`（DecisionResult + decide）+ `_publish_agency_trigger` 内 additive Decision 检查点 + motive trace 存储 + 回归测试（含「无 pending motive 时 0 行为变化」断言）。
- 验收锚点：删除全部 motive/Decision 逻辑 → scheduler / Agency / handlers 行为逐字节不变（backward-compat 100%）。
- 待定项（SM-2 前需主大脑/ Owner 拍板）：motive 生命周期状态机（pending / transmitted / rejected / expired）、motive trace 文件名与位置、Decision LLM 的 prompt 边界。
