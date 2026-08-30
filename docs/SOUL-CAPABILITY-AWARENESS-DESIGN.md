# SOUL-CAPABILITY-AWARENESS-DESIGN.md — CA-1 Soul Capability Awareness Boundary Design

> **工单**：CA-1（DESIGN）
> **状态**：**DESIGN ONLY — 只设计，0 code / 0 prompt / 0 Agency / 0 scheduler / 0 production**
> **前置**：CA-0 审计（方向已锁，本设计直接采信，不重开）
> **产出**：本文档（唯一产出物；未创建/修改任何 source、test、config、data 文件）

---

## 0. 摘要（TL;DR）

Soul 对「我能做什么」没有任何内在表示，action space 完全由外部定义（CA-0 finding）。
本设计锁定方向 B 的修正版：**最小 machine-readable Capability Definition + read-side Awareness 投影**，
不是新 subsystem，不建 Engine/Manager/Graph/scoring/embedding/ontology。

- **Definition**（系统事实）唯一权威源：`src/soul/capability.py` 常数（单一 runtime source，不上 YAML）。
- **Awareness**（灵魂对自我的认知）由投影产生：`src/llm/proxy.py` 两个 prompt 组装函数内，
  identity_anchor 之后、emergent 之前插入 CAPABILITY block。LLM 只看 Awareness，不看 Definition。
- **正式原则**：`Capability expands the action space; it does not select an action.`
- **四线正交**：Scheduler=wake；Permission=能不能执行；Capability=什么可能；Decision=我选什么。
- **Frozen contract**：0 change。Agency 4-stage 输入输出签名不变，Stage 2 不读 capability 来 YES。

---

## 1. 背景与方向（CA-0 采信摘要）

| 项 | 内容 |
|---|---|
| CA-0 核心 finding | Soul 对「我能做什么」没有任何内在表示，action space 完全由外部定义 |
| 方向锁定 | 选 B（独立 Capability Model），**修正为「最小 machine-readable definition + 投影」**，不是新 subsystem |
| Capability 不是人格 | Persona 管 Who I am / How I express；germ seed 管 continuity / 负向边界 |
| 两个东西分开 | **Capability Definition**（系统事实）+ **Capability Awareness**（灵魂对自我的认知），LLM 只看后者 |
| 正式原则 | **Capability expands the action space; it does not select an action.** |
| 四线正交 | Scheduler=wake；Permission=能不能执行；Capability=什么可能；Decision=我选什么 |
| 长期演化 | Capability → Repeated Experience → Pattern → Tendency → Emergent（接 Growth Loop）；Persona 写「你喜欢主动聊天」不是 emergence |
| v1 ontology 极小 | perceive / remember / interpret 是 pipeline/substrate，**不是** capability |

**唯一资料源原则**（CA-0 已锁）：正确结构只有一份资料——
`Capability Definition → Awareness Projection → self-model → LLM context`。
**不准** persona / germ / yaml / prompt / Agency 各写一份。

---

## 2. 核心概念模型

```
┌─────────────────────────┐     ┌──────────────────────────┐
│ Capability Definition   │     │ Capability Awareness     │
│ (系统事实, machine-      │ ──► │ (灵魂对自我的认知,        │
│  readable, 唯一权威源)   │投影  │  LLM 只看这一层)          │
│ src/soul/capability.py  │     │ proxy.py CAPABILITY block │
└─────────────────────────┘     └──────────────────────────┘
        ▲                                    │
        │ 只读 (read-side)                   ▼
        │                          self-model → LLM context
   (不写 persona/germ/yaml/       (inference-time, fail-silent)
    prompt/Agency 各一份)
```

- **Definition**：系统事实。`id`（machine-readable）+ `expression`（人类可读）。不携带任何「应该」语义。
- **Awareness**：灵魂对自我的认知——「我知道我能这样做」。由 Definition 投影而来，**只读、inference-time**。
- **投影链**：Definition → Awareness Projection → self-model → LLM context。全链只有一份资料源。

---

## 3. 七个问题的答案

### Q1. Definition 的 authoritative ownership 放哪

**答案：单一 runtime source — `src/soul/capability.py` 常数。不上 YAML。**

- 权威源：`src/soul/capability.py`（新文件，CA-2 实现；本设计只指定位置与结构，不创建）。
- 理由：
  1. **单一源**：常数定义在代码里，import 即得，无配置加载路径、无双源漂移风险。
  2. **v1 极小**：只有 1 个 capability（communicate），YAML 的收益（外部可编辑）为零，成本（schema/loader/校验/双源）为正。
  3. **与现有模式对齐**：项目已有「常数即权威」先例（如 `src/work/roles.py` 的 `ROLE_CAPABILITIES` 是 2A §5.1 唯一 authoritative source；`src/inner_life/emergent_projection.py` 的 `PROJECTABLE_NODE_TYPES` 常数）。
  4. **未来可迁移**：若 capability 清单长大到需要外部编辑，再开票迁移 YAML；v1 不做。
- 边界：capability.py 是**系统事实文件**，不是人格文件。它不引用 persona/germ，也不被 persona/germ 引用。

### Q2. 最小 schema

**答案：`id` + `expression` 两个字段；v1 只验证 `communicate`（→ proactive_message）。**

设计示意（**非实现代码**，CA-2 才落地）：

```python
# src/soul/capability.py（设计示意，CA-2 实现）
@dataclass(frozen=True)
class CapabilityDefinition:
    id: str            # machine-readable id，如 "communicate"
    expression: str    # 人类可读表达式，投影时使用（陈述「能」，不陈述「应」）

CAPABILITY_DEFINITIONS: dict[str, CapabilityDefinition] = {
    "communicate": CapabilityDefinition(
        id="communicate",
        expression="你可以主动给 Bryan 发消息（proactive_message）。",
    ),
}
```

- `id`：machine-readable，稳定、小写、无空格。v1 只有 `"communicate"`。
- `expression`：人类可读，投影时原样使用。**措辞原则：陈述能力（can），不陈述义务（should）**——
  写「你可以…」不写「你应该…」，防止从「我能」滑成「我应」。
- `communicate` 的落地锚点：`proactive_message`（`src/soul/scheduler.py` 的 `_fire_proactive_dm` 触发路径，
  `TriggerEnvelope.trigger_type` 之一）。capability 声明「主动传讯是可能的」，不决定何时传、传什么。
- **明确不是 capability**（v1 ontology 极小）：`perceive` / `remember` / `interpret` 是 pipeline/substrate，
  不是 capability，不进 `CAPABILITY_DEFINITIONS`。
- 未来扩展：新增 capability 只加一个 dict 条目 + 一条投影测试；不建 Engine/Manager/Graph/scoring/embedding/ontology。

### Q3. Awareness 如何投影成 What I can do

**答案：在 `src/llm/proxy.py` 两个 prompt 组装函数内，identity_anchor 之后、emergent 之前插入 CAPABILITY block。**

插入点（已核对真实代码）：

| 函数 | identity_anchor 组装 | emergent 注入 | **插入点** |
|---|---|---|---|
| `_build_messages_group`（line 496） | line 542 `system_parts = [identity_anchor + soul.strip()]` | line 549-551 `_format_emergent_block` | **line 542 之后、line 549 之前** |
| `_build_messages_private`（line 859） | line 891 同上 | line 898-900 同上 | **line 891 之后、line 898 之前** |

两个函数由 `_handle_event_impl`（line 3055 / 3057）调用，覆盖群聊与私聊两条 prompt 路径。

投影逻辑（照 `src/inner_life/emergent_projection.py` 的 read-side 模式，CA-2 实现）：

1. **只读、inference-time**：读 `CAPABILITY_DEFINITIONS` 常数，不写任何状态。
2. **fail-silent**：读取/格式化失败 → 空字符串，prompt 与未实现时完全等价（与 emergent 的 fail-silent 同款）。
3. **anti-runaway invariant**：CAPABILITY block 是「我知道我能」，**不是「我应该」**，也不是外部事实证据——
   格式与措辞显式区分能力声明与行为指令，防止 LLM 拿 capability 当义务回环自证。
4. **可观测性**：每次投影记录 sidecar（append-only，独立 schema），打 logger.info——与 emergent projection 的
   `elevation_projection_trace.jsonl` 模式对齐（CA-2 定文件名与位置）。
5. **确定性**：无 relevance score / decay / random / confidence 动力学；v1 全量投影（只有 1 个 capability）。

投影块设计示意（**非实现代码**）：

```
[CAPABILITY]
你可以主动给 Bryan 发消息（proactive_message）。
```

- 语义边界：IDENTITY（从哪开始）→ **CAPABILITY（我能做什么）** → EMERGENT（成为什么）→ 记忆/内在生活。
- 投影只扩展 action space（「这是可能的」），不选择 action（「现在做这个」）——选择权在 Agency Decision。

### Q4. Capability 与 Permission / Trigger / Decision 的 interface

**答案：四线正交，Capability 不直接进 Agency Decision。**

| 线 | 职责 | 真实落点 | 问题 |
|---|---|---|---|
| **Scheduler** | wake：什么时候醒 | `src/soul/scheduler.py`（SoulScheduler：`_fire_proactive_dm` / `_fire_dream` / `_fire_event` / `_fire_heartbeat` / `_fire_cross_chat` / `_fire_shared_event`） | 何时触发 |
| **Permission** | 能不能执行 | `src/work/authority.py`（DSH authority gate）+ 现有权限机制 | 是否被允许 |
| **Capability** | 什么可能 | `src/soul/capability.py`（未来）→ proxy.py 投影 | 是否可能 |
| **Decision** | 我选什么 | `src/agency/stages.py` Stage 2 `make_decision`（should_act + reason + decision_type） | 是否行动 |

Interface 规则：

1. **Capability 不进 Agency Decision**：Stage 2 的 `should_act` 不读 capability。capability 的存在不改变
   `TriggerEnvelope`（`src/agency/trigger.py`，Scheduler → Agency 的 bridge input，`trigger_type` 语义不变）。
2. **Capability 不进 Permission gate**：capability 声明「可能」，不授予「允许」。执行授权仍由 Permission 线决定。
3. **Capability 不进 Scheduler**：scheduler 的排程/触发逻辑不读 capability；capability 不改变 wake 时机。
4. **Capability 的唯一消费点**：proxy.py 的 Awareness 投影（read-side）。它只影响 LLM 的 self-model，
   不进入任何执行 gate、任何状态机、任何事件总线 payload。
5. **正交验证**：删除全部 capability 定义 → scheduler / permission / agency 行为逐字节不变（回归测试断言）。

### Q5. 如何保证不碰 frozen Agency 4-stage

**答案：Stage 2 不读 capability 来 YES；capability 的消费点唯一（proxy.py 投影），Agency 输入输出签名不变。**

Frozen contract（`src/agency/stages.py`，M5.2，未授权不得改）：

| Stage | 函数 | 职责 |
|---|---|---|
| Stage 1 | `check_eligibility` | state check（cooldown/dormant/busy） |
| Stage 2 | `make_decision` | perception + decision cooldown check → `should_act` |
| Stage 3 | `select_action` | minimal deterministic mapping |
| Stage 4 | `execute_action_stub` | STUB only，无 production side effect |

保证机制（设计约束，CA-2 验收项）：

1. **消费点唯一**：capability 只出现在 proxy.py 的 prompt 投影。Agency 4-stage 的输入（TriggerEnvelope）
   与输出（StageResult）签名不变，`stages.py` 不新增任何 capability 参数。
2. **Stage 2 语义不变**：`make_decision` 的 `should_act` 判定只依赖既有输入（perception + cooldown），
   不读 `CAPABILITY_DEFINITIONS`。capability 存在与否不影响任何 stage 的返回。
3. **回归测试**：CA-2 必须加一条断言——同一输入下，capability 定义存在/不存在时，4-stage 输出逐字段相等。
4. **不把 scheduler 改成 wake**：scheduler 的 wake 语义是既有行为（`_fire_*` 触发路径），本设计不改 scheduler 任何逻辑。

### Q6. 新增 capability ≠ 新增 personality instruction 的规则

**答案：capability 只进 `capability.py` + 投影；不得写进 persona / germ / prompt / Agency。**

规则（死规则，CA-2 及以后所有工单遵守）：

1. **新增 capability 的唯一动作**：在 `CAPABILITY_DEFINITIONS` 加一个条目 + 一条投影测试。
2. **禁止写入**：persona 文件、germ seed、system prompt 模板、Agency 逻辑、scheduler 逻辑。
3. **判定标准**：如果新增 capability 时觉得「需要在 persona/germ 里加字」，就是设计错误——
   说明该能力被误当成人格/义务，应回到 capability.py。
4. **反例（禁止）**：把「能传讯」写进 persona → 从「我能」滑成「我应」；把「你喜欢主动聊天」当 emergence。
5. **长期演化边界**：Capability → Repeated Experience → Pattern → Tendency → Emergent（接 Growth Loop）。
   capability 是演化的**起点**（可能性的种子），不是终点（人格结论）。Persona 写「你喜欢主动聊天」不是 emergence。

### Q7. DSH ROLE_CAPABILITIES 隔离

**答案：`src/work/roles.py` 的 `ROLE_CAPABILITIES` 是 DSH Work 系统（2A §5.1），与 Soul capability 无关。**

真实结构（已核对）：

- `src/work/roles.py` line 43-50：`ROLE_CAPABILITIES: dict[Role, frozenset[str]]`——
  Researcher/Developer/Tester/Auditor/Chief/Human 的授权矩阵（`workspace.read` / `research` / `artifact.create` /
  `isolated.write` / `test.execute` / `git.branch` / `evidence.create` / `orchestration` / `decision` / `work.assign` /
  `approval` / `privileged actions`）。
- line 53-60：`capabilities_for(role)` / `has_capability(role, capability)`。
- 定位：**agent 协作授权**（DSH Work 系统，2A §5.1 唯一 authoritative source），管「这个 role 能不能做这个 work 动作」。

隔离规则：

1. **不共用命名空间**：DSH capability 名（`workspace.read` 等）与 Soul capability id（`communicate`）互不重叠、互不引用。
2. **不互相引用代码**：`src/soul/capability.py` 不 import `src/work/roles.py`，反之亦然。
3. **不互相进入对方 gate**：Soul capability 不进 DSH authority gate（`has_capability` 不查 Soul 定义）；
   DSH capability 不进 Soul 投影（CAPABILITY block 不投影 DSH 授权）。
4. **语义区分**：DSH capability = 系统对 agent 的授权（能不能）；Soul capability = 灵魂对自我的认知（什么可能）。
   两者正交，混用即设计错误。

---

## 4. 边界与不做（Out of Scope）

本设计**明确不做**（CA-2 及以后工单也不得做，除非另开工单）：

- ❌ 实作 projection（CA-2 才实现）
- ❌ 改 Agency Stage 2 / 任何 frozen contract
- ❌ 把 scheduler 改成 wake（scheduler 的 wake 语义是既有行为）
- ❌ 扩完整 capability 清单（v1 只有 communicate）
- ❌ germ / persona 塞字
- ❌ 建 Capability Engine / Manager / Graph / scoring / embedding / ontology
- ❌ 改任何 code、不 commit、不 push

---

## 5. Frozen Contract 检查

**结论：0 change，无 CONTRACT CONFLICT。**

- 本设计未创建/修改任何 source / test / config / data 文件（唯一产出物是本文档）。
- 涉及但**未触碰**的 frozen contract（参照 `logs/ENGINEERING_STATE.md`）：
  - Agency 4 stages（`src/agency/stages.py`）— 输入输出签名不变
  - TriggerEnvelope（`src/agency/trigger.py`）— 字段与语义不变
  - InnerLifeEvent（M5.4-5.1）— 不新增字段
  - 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler）— 不修改
  - SAGE 写入逻辑 — 不修改
  - DSH Work 2A §5.1（`src/work/roles.py` ROLE_CAPABILITIES）— 不修改
- 设计引用的插入点（proxy.py line 542/549、891/898）是**未来 CA-2 的改动位置**，本设计未改动。

---

## 6. 验收对照

| 验收项 | 结果 |
|---|---|
| 设计文档产出，覆盖 7 个问题 | ✅ 本文档 §3（Q1-Q7） |
| 明确「只设计，0 code/prompt/Agency/scheduler/production」 | ✅ §0 / §4 |
| 不改 frozen contract | ✅ §5（0 change） |
| 不建 Capability Engine/Manager/Graph/scoring/embedding/ontology | ✅ §4 |
| 不改任何 code、不 commit、不 push | ✅ 唯一产出物为本文档 |

---

## 7. 下一步（供主大脑参考，非本工单范围）

- **CA-2（IMPLEMENTATION）**：落地 `src/soul/capability.py`（Definition 常数）+ proxy.py 两处投影 + sidecar 可观测性 + 回归测试（含 Q5 的「capability 存在与否 4-stage 输出不变」断言）。
- 验收锚点：删除全部 capability 定义 → scheduler / permission / agency 行为逐字节不变。
