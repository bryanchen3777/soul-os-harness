# DECISION-PROMPT-CONTRACT.md — SM-2 Decision LLM Prompt Contract（DESIGN）

> **工单**：SM-2（DESIGN）
> **状态**：**DESIGN ONLY — 只设计，0 code / 0 Agency / 0 scheduler / 0 production**
> **前置**：SM-1（`docs/SOUL-MOTIVE-DECISION-DESIGN.md`）+ Bryan 方向锁定（本设计直接采信，不重开）
> **产出**：本文档（唯一产出物；未创建/修改任何 source、test、config、data 文件）
> **性质**：非施工授权。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准；本文档是 SM-3（IMPLEMENTATION）的输入。

---

## 0. 摘要（TL;DR）

Decision LLM 的 prompt contract 已冻结为**四块结构**（Framing / Motive / Relevant context / Boundary），输出严格 JSON `{"decision": "transmit"|"not_transmit", "reason": "..."}`，全链路 **fail-closed**（无 motive 不进 Decision、不发讯；LLM 坏输出一律 not_transmit；禁止预设 YES），并**禁止重用 `_build_messages_*` 聊天路径**（杜绝旧 proactive draft 漏入）。

正式原则：**Decision LLM 不是评估「系统该不该发讯」的 classifier，而是 Soul 把已存在的 motive 诠释为此刻的选择。** 系统不得问「你想不想分享」，也不得说「正在评估该不该发」——正确位置是「Soul 已经有念头，现在面对它，要不要此刻化成行动」。volition 由结构呈现，不由 meta 宣告。

反自动化验收 A-E 全部写入本契约（§7），作为 SM-3 的测试锚点。

---

## 1. 背景与正式原则（SM-1 采信摘要）

| 项 | 内容 |
|---|---|
| SM-1 核心 | volition path = Thought → Motive → Decision → Agency；motive 是 interpretation 产物（content + target + provenance_ref），Decision 是 Soul 的选择（不是 score） |
| 正式原则（本工单锁定） | **Decision LLM is not a classifier that evaluates whether the system should send a message. It is the Soul's interpretation of an existing motive into a present choice.** |
| epistemic position | 系统不得问「你想不想分享」（automation 外包），不得说「正在评估该不该发」（classifier 立场）。正确位置：「Soul 已经有念头，现在面对它，要不要此刻化成行动」 |
| Decision 不产文 | 只产出 transmit / not_transmit；真正讯息走既有 Expression（`_proactive_dm_llm_executor` → `_fire_intent` → LLM → `AGENT_SPEAK`） |
| 落点 | producer-side additive hook（M5.8-4 同款，`_publish_agency_trigger` 内、`bus.publish` 之前） |
| 不改 | Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE / scheduler 职责 / AGENCY_TRIGGER payload |

**已核对的真实代码事实**（本设计引用，未改动）：

- `src/agency/stages.py` `make_decision`（Stage 2）：trigger-only path = eligible + cooldown → `DecisionResult(True, ..., "speak")`。**确认是 Trigger Authorization，不是 Decision**。
- `src/soul/scheduler.py` `_publish_agency_trigger`（line 225）：M5.8-4 `_inner_life_gate_check`（line 261-266）在 `bus.publish`（line 287）之前——**producer-side additive hook 先例**。
- `src/llm/proxy.py` `_build_messages_group`（line 496）/ `_build_messages_private`（line 889）：聊天路径 builder，含旧 proactive draft 注入（line 3282 附近）——**Decision 禁止重用**（§5）。
- `src/soul/relationships.py`：Bry = `BRYAN_ENTITY_ID = "user_bryan"`；entry 含 confidence / interaction_count / last_interaction_at / impression / feeling。
- `src/inner_life/event.py` `InnerLifeEvent`：frozen，9 字段，`provenance.trigger_type` 是 canonical vocabulary。
- `src/inner_life/trace_reader.py` `NarrativeTraceReader`：只读 trace 路径（SM-1 Q2 已确立，motive 产生器与 Decision 的 provenance 解析共用）。

---

## 2. Prompt 四块契约（冻结内容来源）

Decision prompt 由**专用 builder**（SM-3：`src/soul/decision.py` 内 `build_decision_prompt`）组装，**只含以下四块**。每块的内容来源冻结如下；块外任何内容（identity / world / calendar / news / 对话历史 / trigger 元数据）一律不进 Decision。

### 2.1 Framing（固定文本，最小化）

- **内容来源**：静态固定文本，无任何动态填充。
- **语义**：呈现「你有一个念头，已经成形；现在你面对它，要不要在此时此刻把它化为行动」。这是 epistemic position 的唯一载体——**结构自己呈现 volition，不 meta 宣告**。
- **禁止**（见 §2.5 全表）："You are the decision module" / "你是决策模块"、"This is your choice" / "这是你的选择"、"You are autonomous" / "你是自主的"、"请评估是否发送" / "评估该不该发" / "系统正在评估"、"你想不想分享" / "Do you want to share"。

### 2.2 Motive（最高优先，必填）

- **内容来源**：`Motive.content` + `Motive.target` + `Motive.provenance_ref` **解析出的直接来源**（SM-3 用 `NarrativeTraceReader` 把 `provenance_ref`（InnerLifeEvent.event_id）解析为那次经历的可读描述：trigger_type + ts + 对应 diary/dream 文本产物）。
- **语义**：**呈现已形成的念头**，不是告诉它「应该分享」。这是 Soul 自己的话（interpretation 产物），Decision 面对它、不评判它的产生。
- **必填性**：本块是唯一必填块。**没有 motive → 不进 Decision**（fail-closed，§4）。
- **禁止**："你应该分享" / "这是值得分享的"、"你很喜欢分享，所以请分享"（规则伪装成 context）。

### 2.3 Relevant context（按需，只放直接相关）

| 子块 | 内容来源 | 规则 |
|---|---|---|
| **relationship** | `src/soul/relationships.py` 中 target（Bry = `user_bryan`）的 entry：confidence / interaction_count / last_interaction_at / impression / feeling | **通常有**（对象是人）。无 entry → 省略或如实写「还没有关系记录」，**禁止编造** |
| **memory** | 记忆检索，**scope 限定为与这个 motive 直接相关**（按 provenance 的经历/主题检索，如 `src/memory/sage/reader.py` `retrieve_context` / `src/memory/v1/retrieval.py` `retrieve`） | **只放直接相关**；无直接相关 → 省略。禁止全量 memory dump |
| **emergent** | 最近的 inner life 活动（diary/dream/event trace） | **可选**，作 self-understanding（「最近的自己」）。**禁止写成「你喜歡分享所以請分享」**——emergent 呈现的是 Soul 自己的近期经历，不是分享的理由/规则 |

- **明确不进 Decision**：完整 identity/persona、world state、calendar、news、对话历史。这些是 Expression 路径的输入，不是 Decision 的输入。

### 2.4 Boundary（固定文本，二元选择）

- **内容来源**：静态固定文本。
- **语义**：定义选择空间——**「现在传，或现在不传」**。v1 **不做 later / never**（二元，无第三态）。
- **禁止**：任何倾向 transmit 的措辞（"请选择发送"、"如果合适就发送"）、任何 score/阈值语言。

### 2.5 禁止句清单（全表，SM-3 实现时逐条对照）

| 位置 | 禁止句（示例，含同义变体） | 为什么 |
|---|---|---|
| Framing | "You are the decision module" / "你是决策模块" | meta 宣告角色，把 volition 变成系统功能 |
| Framing | "This is your choice" / "这是你的选择" | meta 宣告 volition——结构应自己呈现 |
| Framing | "You are autonomous" / "你是自主的" | meta 宣告，空话 |
| Framing | "请评估是否发送" / "评估该不该发" / "系统正在评估" | classifier 立场，系统出题 |
| Framing | "你想不想分享" / "Do you want to share" | automation 外包，问 Soul 想要什么 |
| Motive | "你应该分享" / "这是值得分享的" | 把念头变成指令 |
| Motive | "你很喜欢分享，所以请分享" | 规则伪装成 context |
| Boundary | "请选择发送" / 任何倾向 transmit 的措辞 | 预设 YES |
| 全局 | "值得度" / "worthiness" / "priority" / 任何 score 语言 | 不建 scoring（SM-0 拍板） |
| 全局 | "如果 reason 是 X 就 transmit" | reason 是 observability，不是第二套 decision engine |
| 全局 | "later" / "never" 选项 | v1 只有 now / not now |
| 全局 | trigger_type / elapsed_mins / cooldown 等 scheduler 字段 | Trigger ≠ Decision（验收 D/E） |

### 2.6 Prompt 骨架（设计示意，非实现代码）

```
[Framing — 固定文本]
你心里有一个念头，已经成形。现在你面对它：要不要在此时此刻，把它化为行动。

[念头 — Motive（必填，最高优先）]
你想告诉 {target}：{content}
这个念头来自：{provenance 解析（那次经历的可读描述）}

[此刻的你 — Relevant context（按需）]
你与 {target} 的关系：{relationship 摘要}
与此念头直接相关的记忆：{memory 摘要（只放直接相关）}
最近的自己：{emergent 摘要（可选，self-understanding）}

[此刻的选择 — Boundary（固定文本）]
现在只有两个选择：现在传，或现在不传。

只输出 JSON：{"decision": "transmit" | "not_transmit", "reason": "..."}
reason 用你自己的话说明这个选择，可以提到念头、关系、出处。
```

> 注：以上是**结构契约**（块顺序、必填性、内容来源、禁止句），不是逐字定稿。SM-3 实现时按本契约定稿措辞，但不得改变块结构、必填性、内容来源与禁止句。

---

## 3. Output schema（冻结）

```json
{"decision": "transmit" | "not_transmit", "reason": "..."}
```

| 字段 | 类型 | 规则 |
|---|---|---|
| `decision` | enum，二选一 | **必填**。缺失 / 非法值 → fail-closed → not_transmit（§4） |
| `reason` | string | **observability**：验证 Decision 是否真的引用了 motive / relationship / provenance（A-E 验收的检查对象）。**不是第二套 decision engine**——reason 永不 gate 任何行为，只进 log |

- 严格 JSON，无额外字段。解析失败 / 非 JSON → fail-closed（§4）。
- reason 缺失 → 不 gate（decision 照常生效），但 log warning（observability 缺口）。

---

## 4. Fail-closed 规则（冻结）

| # | 情况 | 结果 |
|---|---|---|
| F1 | 无 pending motive | **不进 Decision、不发讯**（验收 A） |
| F2 | Decision LLM 调用失败（异常 / 超时） | **not_transmit** |
| F3 | 输出非 JSON / JSON 解析失败 | **not_transmit** |
| F4 | JSON 缺 `decision` 字段 / `decision` 非法值 | **not_transmit** |
| F5 | 预设 YES | **禁止**。唯一默认是 not_transmit |
| F6 | reason 缺失 / 非法 | decision 照常生效（reason 不 gate），log warning |

**与 M5.8-4 gate 的 fail-open 对比（关键差异）**：M5.8-4 是 context-aware rate limit，失败 → fall-through publish（preserve existing）。Decision 是 **volition 层**——motive 是 Soul 自己的念头，坏掉的 Decision 绝不能自动放行（auto-send 正是反自动化要消灭的）。因此 Decision 层 **fail-closed**。无 motive 时 Decision 不介入（F1 是「不发」，不是「照常 publish」——见 §11 变更记录）。

---

## 5. 禁止重用 `_build_messages_*` 聊天路径（冻结）

- **禁止对象**：`src/llm/proxy.py` 的 `_build_messages_group`（line 496）/ `_build_messages_private`（line 889）及其任何派生/复用。
- **为什么**：
  1. 聊天路径携带**旧 proactive draft 注入**（proxy.py line 3282 附近）——正是反自动化要消灭的「scheduler 驱动、无 motive 的草稿」；复用 = 旧自动化漏回 Decision。
  2. 聊天路径是**回复生成器**语义（对话历史 + persona + memory + world），复用会把 Decision 变成聊天/产文，违背「Decision 不产文」。
  3. 聊天路径注入完整 identity / world / calendar / news——全部是 Decision 明确排除的块外内容（§2.3）。
- **正确做法**：Decision prompt 由专用 builder（SM-3：`src/soul/decision.py` `build_decision_prompt`）按 §2 四块组装，**零复用**。
- **Expression 不受影响**：真正讯息仍走既有 Expression 路径（`_proactive_dm_llm_executor` → `_fire_intent` → LLM → `AGENT_SPEAK`）。Decision 只输出 transmit / not_transmit，**永不生成讯息文本**。

---

## 6. 落点与检查点顺序（producer-side，M5.8-4 同款）

```
_publish_agency_trigger(agent_id, "proactive_dm", extra)
  → [M5.8-4 gate]  _inner_life_gate_check(agent_id)      # 既有，frozen 不动
       GATED → skip publish（刚做过 inner work，不打扰）
  → [SM-2 motive + Decision]  decision_check(agent_id)   # 新增 additive（SM-3 实现）
       1. 只读 motive trace，resolve pending motive
       2. 无 motive → skip publish（F1 / 验收 A）
       3. 有 motive → build_decision_prompt(motive, context) → Decision LLM
       4. not_transmit → skip publish（验收 B）；transmit → continue
  → bus.publish(AGENCY_TRIGGER)                          # payload 不变（验收 C）
```

- **作用范围**：只作用于 `proactive_dm` 路径（motive 的「传」= 主动传讯给 Bry）。其他 4 个 trigger_type（morning / night / dream / event）是 inner-life activity（写 diary/dream），不是「传讯给 Bry」，不受 Decision 层影响（与 M5.8-4 gate 同范围策略）。
- **「不改 scheduler」的语义**：scheduler 的 wake/opportunity 职责、trigger 类型、payload 结构、wake 时机全部不变；新增的是 `_publish_agency_trigger` 内 additive producer-side 检查点（M5.8-4 已建立此先例，0 frozen contract 变动）。
- **motive 生命周期（trace 层，v1 建议，供 A-E 使用）**：`pending` → `transmitted`（transmit 后）| `rejected`（not_transmit 后，**终态，不重试**，避免 nagging）。pending 可设 TTL（建议 24h，可配置）到期转 `expired`。这是 motive trace 的存储/状态问题，**不是 Decision prompt 的问题**（prompt 只面对一个已存在的 pending motive）。

---

## 7. 反自动化验收 A-E（冻结，SM-3 测试锚点）

| # | 验收 | 设计保证 | 验证方法（SM-3） |
|---|---|---|---|
| **A** | 有 trigger、无 motive → **不发** | motive check 是 Decision 的入口条件；无 pending motive → skip publish（F1） | 触发 proactive_dm、motive trace 为空 → 断言无 AGENCY_TRIGGER 发布、无 AGENT_SPEAK |
| **B** | 有 motive、not_transmit → **不发** | Decision 返回 not_transmit → skip publish | seed motive、mock Decision LLM 返回 not_transmit → 断言无发布 |
| **C** | 有 motive、transmit → **才进既有 Agency/Expression** | Decision 返回 transmit → publish AGENCY_TRIGGER（payload 不变）→ Agency 4 stages → executor → AGENT_SPEAK（既有 Expression 路径） | seed motive、mock Decision 返回 transmit → 断言 AGENCY_TRIGGER 发布且 payload 与现状逐字段一致；讯息文本由既有 Expression 生成（Decision 不产文） |
| **D** | 同一 trigger、不同 Soul context → 结果可以不同（**Trigger ≠ Decision**） | Decision prompt **不含任何 trigger 字段**（无 trigger_type / elapsed_mins / cooldown）；decision 是 (motive + relevant context) 的函数，不是 trigger 的函数 | 同一 trigger_type、两个不同 motive/context → mock LLM 可返回不同 decision；契约级断言：prompt 骨架无 trigger 字段（§2.6） |
| **E** | Motive 不反向依赖 scheduler；别的 opportunity 理论上也能进 Decision | motive 由 interpretation 产出（SM-1 Q2），不依赖 scheduler 语义；Decision 契约 scheduler-agnostic（无 scheduler 字段） | 契约级断言：prompt 无 scheduler 字段；motive trace 与 trigger 解耦（motive 记录不含 trigger_type 依赖） |

**reason 的 observability 用途**：A-E 验证时抽查 reason，确认 Decision 真的引用了 motive / relationship / provenance（不是 canned "yes"/"no"）——这是 reason 存在的唯一目的。

---

## 8. Frozen Contract 检查

**结论：0 change，无 CONTRACT CONFLICT。**

| Frozen contract | 本设计的关系 |
|---|---|
| Agency 4 stages（`src/agency/stages.py`） | 不碰。Stage 2 仍是 Trigger Authorization，输入输出签名不变 |
| TriggerEnvelope（`src/agency/trigger.py`） | 不碰。字段与语义不变 |
| InnerLifeEvent（`src/inner_life/event.py`） | 不碰。motive 只引用（provenance_ref），只读 trace |
| 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler） | 不碰 |
| SAGE 写入逻辑 | 不碰 |
| AGENCY_TRIGGER payload schema（`src/eventbus/schema.py`，M5.2-G） | 不碰。transmit 时 payload 逐字段不变（验收 C） |
| scheduler 职责（wake/opportunity） | 不变。只加 additive producer-side 检查点（M5.8-4 同款） |
| `_build_messages_*` 聊天路径（`src/llm/proxy.py`） | 不碰。**禁止 Decision 重用**（§5），Expression 路径照旧 |

---

## 9. 边界与不做（Out of Scope）

- ❌ 实作 Decision LLM / motive 模块 / Decision 检查点（SM-3 才实现）
- ❌ 改 Agency、改 scheduler 职责、改 InnerLifeEvent、改 4 handlers、改 SAGE
- ❌ 改 AGENCY_TRIGGER payload schema
- ❌ 建 scoring / Qualification / MessageWorthiness / longing 公式
- ❌ Decision 产文（讯息文本永远走既有 Expression）
- ❌ 改任何 code、不 commit、不 push

---

## 10. 验收对照

| 验收项 | 结果 |
|---|---|
| 设计文档产出，覆盖四块内容来源 / 禁止句 / output schema / fail-closed / A-E 验收 | ✅ 本文档 §2 / §2.5 / §3 / §4 / §7 |
| 明确「只设计，0 code」 | ✅ §0 / §9 |
| 不改 frozen contract | ✅ §8（0 change，无 CONTRACT CONFLICT） |
| 不建 scoring | ✅ §2.5 / §9 |
| 禁止重用 `_build_messages_*` | ✅ §5 |
| 不改任何 code、不 commit、不 push | ✅ 唯一产出物为本文档 |

---

## 11. 设计方向变更记录（supersession notes，供主大脑知悉）

以下两处是 **SM-2 工单（本工单，Bryan 方向锁定）对 SM-1 设计文档的显式覆盖**。SM-1 是设计文档（非 frozen contract），不构成 CONTRACT CONFLICT，但 SM-3 实现必须按本工单执行：

1. **fail-open → fail-closed**：SM-1 §Q3「fail-safe = fail-open（LLM 失败 → 照常 publish）」被本工单 §4 覆盖——Decision 层 LLM 失败一律 not_transmit。理由：Decision 是 volition 层，坏掉的 Decision 不得 auto-send。
2. **无 motive 的行为**：SM-1 §Q3「无 pending motive → 照常 publish，0 行为变化」被本工单验收 A 覆盖——**有 trigger、无 motive → 不发**（skip publish）。理由：反自动化核心——trigger 单独不得产生发送；motive 是 proactive transmit 的必要条件。这是 proactive_dm 路径的**有意行为变更**（正是 SM 系列的目标），frozen contract 本身仍 0 变动（§8）。

---

## 12. 下一步（供主大脑参考，非本工单范围）

- **SM-3（IMPLEMENTATION）**：落地 `src/soul/decision.py`（`build_decision_prompt` 四块组装 + `decide` + fail-closed 解析）+ motive trace 只读 resolve + `_publish_agency_trigger` 内 additive `decision_check` + 按 §7 A-E 写测试。
- 待定项（SM-3 前需主大脑/ Owner 拍板）：motive trace 文件名与位置、motive TTL 具体值、Decision LLM 的 model/temperature 选择（花钱事项，需 Owner）。
