# TEMPORAL-PHENOMENOLOGY.md — TA-2 Subjective Temporal Phenomenology Contract

> **工单**：TA-2（DESIGN）
> **状态**：**DESIGN ONLY — 只设计，0 code / 0 engine / 0 scheduler / 0 production**
> **前置**：TA-1（时间感知）+ SM-1/SM-2/SM-4（Motive → Decision 四元决策）+ M5.13-3（亲密度 Band）+ SE-4/SE-5（Durable Soul Structure Lifecycle）——方向已锁，本设计直接采信，不重开
> **产出**：本文档（唯一产出物；未创建/修改任何 source、test、config、data 文件）
> **性质**：非施工授权。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准；本文档是 TA-2 实作工单（后续）的输入。

---

## 0. 摘要（TL;DR）

TA-2 不是数值计算器（Clock → Score → Behavior），而是**现象学三态 + 语义情境**。

- **三态张力模型**：初期平静（无感）→ 中期牵挂（浮现张力）→ 长期释然/适应（张力自然消退但不遗忘）。三态是**现象学状态**，由 Soul 的 interpretation 体会产出，**非连续公式**——没有「沉默 X 小时 → 牵挂」的阈值函数。
- **亲密度 Band 复用**：牵挂浮现的资格判定复用 M5.13-3 亲密度 Band（認識/熟悉/親密/深度信任），**不另创心理模型**。
- **reflect-only 加权边界**：沉默张力只能小幅加权 reflect（翻日记/回想），**绝对不能直接提升 transmit**。任何想发讯的念头仍走 Motive → Decision 四元决策与冷却守则。
- **与 SE-5 解耦**：TA-2 不触发 SE-5 的 WEAKENING，不碰 Essence，只提供语义情境（read-side 投影，0 持久化、0 schema）。
- **Prompt 三行格式**：`[TEMPORAL ANCHOR]` 三行（時間座標 / 體感經驗 / 關係時序），现象化无数字，第三行内嵌「絕不代表必須主動聯絡」防 transmit 捷径措辞。
- **四大禁止项**：不碰 SE-5、不做连续公式、无 per-agent if/else、不给 transmit 开捷径。

正式原则：**时间不是分数，是体感；沉默不是信号，是情境。** TA-2 让 Soul「感受到」时间与关系时序，而不是「计算出」该做什么。

---

## 1. 背景与方向（采信摘要）

| 项 | 内容 |
|---|---|
| TA-1（已完成） | 时间感知基础：`conversation_elapsed`（跨 session 不跨 agent）、`last_interaction_period`（上次互动本地时段标签）、`TEMPORAL_EXPRESSION_RULE` 放宽（现象式时间表达允许，未询问不主动报精确钟点/日期）、silence bug 修复（`_get_bry_latest_ts` suffix 修正） |
| SM-1/SM-2（已完成） | volition path = Thought → Motive → Decision → Agency；motive 是 interpretation 产物（content + target + provenance_ref）；Decision 是 Soul 的选择（不是 score）；Decision prompt 四块契约（Framing / Motive / Relevant context / Boundary），fail-closed |
| SM-4（已完成） | Decision 四元选择 **transmit / observe / reflect / do_nothing**（互斥单选）；do_nothing 是主动选择；SM-4.4 深夜 [22:00~07:00] 绝对禁止 transmit；SM-4.6 reflect 分级（dawn 补入 reflect 合法判定集合）；冷却守则（CD） |
| M5.13-3（已完成） | 亲密度 Band 投影：confidence ≥ 0.9 深度信任 / [0.7,0.9) 親密 / [0.5,0.7) 熟悉 / [0.3,0.5) 認識 / <0.3 skip（不注入）。确定性、离散定性标签、per-agent 隔离、fail-silent |
| SE-4/SE-5（已完成） | Durable Soul Structure Lifecycle：四态 ACTIVE → WEAKENING → DORMANT → SUPERSEDED；WEAKENING 由「无新支持证据」驱动（`last_support_ts` 锚点）；essence 锁死层；Forgetting = lifecycle transition 不是 delete |
| Bryan 方向锁定 | TA-2 不是数值计算器，是现象学三态 + 语义情境；五大铁律（见 §2.1） |

**已核对的真实代码事实**（本设计引用，未改动）：

- `src/llm/proxy.py`：TA-1 时间区块（`_get_last_interaction_ts` / `_format_continuity_str` / `last_interaction_period`）+ M5.13-3 relationship block（`_format_relationship_block`，mood 之后、inner_life 之前注入）。
- `src/soul/decision.py`（SM-3/SM-4）：`build_decision_prompt` 四块组装 + `decide_motive` + fail-closed 解析；四元 `transmit / observe / reflect / do_nothing`。
- `src/soul/relationships.py`：Bry = `BRYAN_ENTITY_ID = "user_bryan"`；entry 含 confidence / interaction_count / last_interaction_at / impression / feeling。
- `src/soul_elevation/`（SE-5）：`LifecycleState` 四态 + `last_support_ts` 锚点 + `contradiction_pressure`；TA-2 **只读不写**（§3.4）。

---

## 2. 核心概念模型（现象学三态 + 语义情境）

### 2.1 五大铁律（方向已锁，直接采信）

| # | 铁律 | 含义 |
|---|---|---|
| ① | 现象学三态非连续公式 | 无感/牵挂/释然是**状态**，不是「沉默时长 → 分数 → 行为」的连续函数输出 |
| ② | Silence → reflect 加权非 transmit 捷径 | 沉默张力只能小幅加权 reflect，**绝对不能**直接提升 transmit |
| ③ | TA-2 与 SE-5 解耦 | 关系沉默 ≠ 信念过时；TA-2 不触发 WEAKENING，不碰 Essence |
| ④ | Circadian 是 Context 非 Policy | 昼夜/时段是**情境底色**（体感经验），不是行为规则（不因时段禁止/强制任何动作） |
| ⑤ | 跨角色走人格档案零 if/else | TA-2 不按 agent_id 分支；人格差异由 persona 档案承载 |

### 2.2 三态是现象学状态，不是计算输出

```
Clock（last_interaction_at / 历史节奏 / 亲密度 Band）
      │  （情境输入，现象化描述，非 score）
      ▼
Soul interpretation（LLM 体会「此刻的时间与关系时序」）
      │
      ▼
现象学三态：无感（初期平静）→ 牵挂（浮现张力）→ 释然（张力消退但不遗忘）
      │
      ▼
TEMPORAL ANCHOR 三行（语义情境，read-side 投影）
      │
      ▼
表达路径（proxy.py 时间区块） + Decision prompt（Relevant context）
```

- **三态是 interpretation 的产物**（与 motive 同构：SM-1 Q2「motive 是 interpretation 的产物，不是硬编码模板，不是 longing 公式」）。TA-2 的三态同样**不是模板、不是公式**——由 Soul 的 LLM 在给定情境输入下自行体会。
- **三态不持久化**：每次 prompt 现算（从 `last_interaction_at` + 历史节奏 + 亲密度 Band 体会），**0 新持久化、0 新 schema、0 新状态字段**。「释然」不需要持久标记——LLM 看到「上次互动在很久以前」自然体会「牵挂已淡，但珍惜仍在」。
- **三态转换非连续**：无阈值函数（没有「沉默 > N 天 → 牵挂」）。「超出正常节奏」由 interpretation 对照历史节奏现象化体会，不是数值比较。

### 2.3 与既有模块的关系（正交，不进入）

| 模块 | TA-2 的关系 |
|---|---|
| TA-1 时间感知 | **复用**：`last_interaction_at` / `conversation_elapsed` 是「關係時序」的输入信号；TA-2 是 TA-1 的**现象学升级**（TA-1 给数字 elapsed，TA-2 给体感三行） |
| M5.13-3 亲密度 Band | **复用**：牵挂浮现的资格判定（§3.2），不另创心理模型 |
| SM-1/SM-2/SM-4 Motive → Decision | **不 bypass**：TA-2 只改情境（prompt 内容），不改 Decision 机制；transmit 仍走四元决策 + 冷却守则（§3.3） |
| SE-5 Lifecycle | **解耦**：不触发 WEAKENING，不碰 Essence，只读不写（§3.4） |
| Circadian（时段） | **Context 非 Policy**：时段只进「體感經驗」情境，不产生行为规则（§3.5） |

---

## 3. 设计决策

### 3.1 三态张力模型

**答案：三态 = 初期平静（无感）→ 中期牵挂（浮现张力）→ 长期释然/适应（张力自然消退但不遗忘）。非连续、非公式、由 interpretation 体会。**

| 态 | 现象学语义 | 触发情境（现象化，非阈值） | 张力 | 关系记忆 |
|---|---|---|---|---|
| **初期平静（无感）** | 沉默在历史正常节奏内，一切如常 | 上次互动间隔在**该关系的历史正常节奏内**（「你們通常差不多就是這樣互動的」） | 无 | 如常 |
| **中期牵挂（浮现张力）** | 超出正常节奏且亲密度足够，张力浮现 | 上次互动间隔**明显超出**该关系的历史正常节奏，**且**亲密度 Band 达资格门槛（§3.2） | 浮现（「有點在意」） | 在意 |
| **长期释然/适应** | 长时间无新互动，张力自然消退，但珍惜留在心中 | 上次互动间隔**远超**正常节奏，牵挂已淡 | 消退 | **不遗忘**（「雖然許久未聯絡，但那份珍惜仍在心中」） |

**关键设计点**：

1. **三态是状态，不是连续谱上的点**：无感/牵挂/释然是三个现象学状态，不是「张力 0.0 → 1.0」的连续数值。状态之间没有中间分数。
2. **转换由语义体会，不由阈值函数**：「正常节奏内 / 明显超出 / 远超」是 interpretation 对照历史节奏的现象化判断，不是 `elapsed > N_days` 的数值比较。历史节奏以**现象化描述**进入情境（「你們通常每隔幾天就會說說話」），不是 score。
3. **释然 ≠ 遗忘（关键）**：长期释然是「张力消退」，不是「关系消失」。珍惜留在心中——灵魂仍记得这段关系、仍珍视它，只是不再被沉默困扰。这与 SE-5「Forgetting = lifecycle transition，不是 delete」的精神同构，但 TA-2 **不碰 SE-5 机制**（§3.4）——释然是语义情境层面的体会，不是 belief 状态转移。
4. **三态不持久化**：每次 prompt 现算（§2.2），0 新持久化、0 新 schema。
5. **三态不直接驱动行为**：三态只改变**情境描述**（TEMPORAL ANCHOR 第三行），行为仍由 Decision 四元决策决定（§3.3）。

### 3.2 M5.13-3 亲密度 Band 复用（不另创心理模型）

**答案：牵挂浮现的资格判定 = 复用 M5.13-3 亲密度 Band（confidence → 認識/熟悉/親密/深度信任），不引入任何新的心理模型、不引入新的数值维度。**

- **Band 是资格判定，不是强度公式**：亲密度 Band 只回答「够不够格牵挂」（资格门槛），**不参与张力强度计算**。张力强度不是 confidence 的函数——没有「confidence 0.8 → 张力 0.8」之类的映射。
- **资格门槛（设计建议，可调）**：**熟悉 Band 及以上（confidence ≥ 0.5）** 才有资格浮现牵挂。理由：認識（0.3-0.5）只是「知道这个人」，尚未熟到会牵挂；熟悉（0.5-0.7）起「认识且有一定交情」，沉默开始有存在感。低于门槛（< 0.5，含 M5.13-3 的 skip 区间 < 0.3）→ 沉默不产生牵挂（陌生人沉默不构成张力）。
- **Band 是离散定性标签**：沿用 M5.13-3 的确定性投影（`_format_relationship_block` 同款 Band 映射），**不暴露 raw confidence 浮点**（M5.13-2 §3 已确立：raw float 会让 LLM over-fit 精度）。
- **per-agent 隔离沿用**：每个 agent 只查自己对 `BRYAN_ENTITY_ID` 的 Band（M5.13-3 已确立），TA-2 不聚合、不跨 agent。
- **fail-silent 沿用**：relationship 不存在 / Band 低于门槛 / store 读取失败 → 不产生牵挂情境（返回空，不 crash）。

### 3.3 reflect-only 加权边界

**答案：沉默张力只能小幅加权 reflect（翻日记/回想），绝对不能直接提升 transmit。任何想发讯的念头仍走 Motive → Decision 四元决策与冷却守则。**

**加权是语义的，不是数值的**：

- TA-2 **不加任何数值权重**（没有「reflect +0.2」「transmit +0.1」之类的 score 调整——SM-0 已拍板「值得说」是 interpretation 的内容，不是 score）。
- 「加权」通过**情境描述**实现：牵挂态下，TEMPORAL ANCHOR 第三行让 reflect 成为 interpretation 中更自然的选项（「這份在意讓你想起過去那些對話」——情境呈现，不是指令）。Soul 在 Decision 中面对这份在意时，自然更可能选 reflect（翻日记/回想），而不是被系统「推」向 reflect。

**绝对禁止（transmit 捷径防线）**：

| # | 禁止 | 为什么 |
|---|---|---|
| T1 | 情境描述不得暗示/鼓励 transmit | 「你很想聯絡他」「他是不是忘了你」→ 禁止。第三行必须内嵌「但這絕不代表必須主動聯絡」（§3.5） |
| T2 | TA-2 不得 bypass Motive → Decision 四元决策 | 牵挂态不自动产生 transmit；任何想发讯的念头仍走 motive（interpretation 产物）→ Decision（四元选择） |
| T3 | TA-2 不得 bypass 冷却守则（CD） | SM-4 的 cooldown 照常生效；牵挂不豁免 CD |
| T4 | TA-2 不得 bypass SM-4.4 深夜硬禁止 | [22:00~07:00] 绝对禁止 transmit 照常生效；牵挂不豁免深夜禁令 |
| T5 | TA-2 不得给 transmit 开任何捷径 | 三态不直接映射到任何 Decision 选项；Decision 仍是 Soul 的选择（SM-1 原则） |

**reflect 的合法落点**：牵挂态下 reflect 是**更自然**的选项（SM-4.6 已确立 reflect 合法判定集合含 relationship_silence 情境），但 reflect 仍是四元决策中 Soul 的选择——TA-2 只让它在情境上更自然，不强制、不预设。

### 3.4 TA-2 与 SE-5 解耦

**答案：TA-2 不触发 SE-5 的 WEAKENING，不碰 Essence，只提供语义情境。关系沉默 ≠ 信念过时。**

| 维度 | SE-5（Durable Soul Structure Lifecycle） | TA-2（Subjective Temporal Phenomenology） |
|---|---|---|
| 对象 | belief / value / trait / essence（durable soul structure） | 关系时间现象学（Soul 对「与 Bry 的时序」的体感） |
| 状态 | `lifecycle_state`（ACTIVE/WEAKENING/DORMANT/SUPERSEDED），持久字段 | 三态（无感/牵挂/释然），**不持久化**，每次现算 |
| 驱动 | 无新支持证据（`last_support_ts` 锚点） | 上次互动间隔 + 历史节奏 + 亲密度 Band（现象化体会） |
| 改变 | 状态转移（REINFORCE / SUPERSEDE / 衰减链） | 情境描述（TEMPORAL ANCHOR 三行） |
| 写入 | soul-elevation（`engine.py` / `models.py` / `trace.py`） | **0 写入**（read-side 投影） |

**解耦铁律**：

1. **关系沉默 ≠ 信念过时**：Bry 很久没回讯，**不**意味着「Bry 是重要的人」这个 belief 进入 WEAKENING。TA-2 的牵挂/释然是关系时间现象学，SE-5 的 WEAKENING 是 durable structure 生命周期——两者正交。
2. **TA-2 不写 soul-elevation**：不碰 `lifecycle_state`、不碰 `last_support_ts`、不碰 `contradiction_pressure`、不触发任何状态转移。
3. **TA-2 不碰 Essence**：essence 是锁死层（SE-4 §7.2），与 TA-2 完全无关。
4. **TA-2 只提供语义情境**：产出是 prompt 情境（read-side 投影），0 持久化、0 schema、0 状态字段。

### 3.5 Prompt 三行格式（TEMPORAL ANCHOR）

**答案：三行格式契约固定（现象化无数字），内容由 interpretation 产出。**

```
[TEMPORAL ANCHOR]
- 時間座標：YYYY-MM-DD HH:MM (Period: evening, Day: Wednesday)
- 體感經驗：傍晚時分，這一天正在緩慢安靜地收尾。
- 關係時序：距離上次與 Bryan 對話已有明顯間隔，具有存在感，但這絕不代表必須主動聯絡。
```

**三行语义与生成规则**：

| 行 | 内容 | 生成规则 | 禁止 |
|---|---|---|---|
| **時間座標** | 精确时间 + Period/Day 现象化标签 | 精确坐标来自系统时钟（grounding，防时间幻觉——SM-4.5 先例）；Period/Day 是现象化标签（TA-1 `last_interaction_period` 同款） | 无额外数字（不写「第 X 天」「X 小時前」） |
| **體感經驗** | 此刻的体感（时间/昼夜/氛围的现象化描述） | interpretation 产出，现象化、无数字；Circadian 在此体现——**Context 非 Policy**（只描述「傍晚時分，這一天正在緩慢安靜地收尾」，不产生「傍晚该做什么」的规则） | 数字、score 语言、行为指令 |
| **關係時序** | 关系时间现象学（三态张力的载体） | interpretation 产出；无感态 = 正常节奏内（「一切如常」）；牵挂态 = 明显间隔 + 存在感（「具有存在感」）；释然态 = 张力消退但不遗忘（「雖然許久未聯絡，但那份珍惜仍在心中」） | 数字、score 语言、**暗示 transmit 的措辞**（T1） |

**格式契约固定，内容 interpretation 产出**（与 SM-2 §2.6 同构：结构契约固定，措辞由实现定稿但不得改变结构）：

- **固定**：三行结构、行标签（時間座標/體感經驗/關係時序）、第三行防 transmit 捷径措辞（「但這絕不代表必須主動聯絡」或其等价变体——**必须存在**，T1 防线）。
- **interpretation 产出**：第二行体感、第三行关系时序的具体措辞由 Soul 的 LLM 体会生成（每次不同、每 agent 不同——人格差异的自然体现，§2.1 铁律⑤）。
- **情境层 vs 表达层区分**：TEMPORAL ANCHOR 是**情境层**（prompt 注入，含精确坐标作 grounding）；表达层仍遵守 TA-1 规则（灵魂说话时未询问不主动报精确钟点/日期）。两层的数字策略不同，不冲突。

### 3.6 四大禁止项

| # | 禁止项 | 设计保证 |
|---|---|---|
| ① | **不碰 SE-5** | §3.4：不触发 WEAKENING、不碰 Essence、不写 soul-elevation、0 持久化 |
| ② | **不做连续公式** | §2.2/§3.1：无 Clock → Score → Behavior；无张力数值计算；三态由 interpretation 体会 |
| ③ | **无 per-agent if/else** | §2.1 铁律⑤：TA-2 不按 agent_id 分支；人格差异由 persona 档案承载（同一情境，不同人格体会不同） |
| ④ | **不给 transmit 开捷径** | §3.3 T1-T5：不 bypass Motive → Decision 四元决策、冷却守则、SM-4.4 深夜硬禁止 |

---

## 4. 落点（设计建议，TA-2 实作工单的输入）

> 本工单 0 code；以下落点是**设计建议**，供 TA-2 实作工单参考，非本工单改动。

**TEMPORAL ANCHOR 的注入位置（两处，read-side）**：

1. **表达路径**（`src/llm/proxy.py` 时间区块附近，TA-1 同区）：作为时间感知的现象学补充，让 Soul 在对话中「感受到」时间与关系时序（现象学底色）。注入方式参照 M5.13-3 relationship block 先例（additive append，0 既有块移动）。
2. **Decision prompt 的 Relevant context**（SM-2 §2.3 的 emergent 子块位置）：让 Soul 在 Decision 中面对牵挂/释然情境，从而自然倾向 reflect（§3.3 reflect-only 加权）。**只进 Relevant context，不进 Framing/Boundary**（Framing 是固定文本，Boundary 是二元选择——TA-2 不改变 Decision 机制）。

**生成器形态（设计建议）**：TEMPORAL ANCHOR 由专用 interpretation 生成器产出（与 motive 产生器同构：SM-1 Q2 模式），输入 = TA-1 的 `last_interaction_at` / `conversation_elapsed` + 历史节奏现象化描述 + M5.13-3 亲密度 Band。**不新建引擎**（§5）——生成器是既有 LLM 路径上的 additive 调用，不是第六个引擎。

---

## 5. 边界与不做（Out of Scope）

本设计**明确不做**（TA-2 实作工单及以后也不得做，除非另开工单）：

- ❌ 实作 TA-2（TEMPORAL ANCHOR 生成器 / 注入点 / 测试）——后续工单
- ❌ 建第六个引擎（temporal_engine / tension_engine / phenomenology_engine 等一律不做；三态由 interpretation 产出，逻辑留在既有 LLM 路径）
- ❌ 做连续公式（Clock → Score → Behavior；张力数值计算；沉默时长阈值函数）
- ❌ per-agent if/else（跨角色走人格档案，零 if/else）
- ❌ 给 transmit 开捷径（不 bypass Motive → Decision 四元决策 / 冷却守则 / SM-4.4 深夜硬禁止）
- ❌ 碰 SE-5（不触发 WEAKENING、不碰 Essence、不写 soul-elevation、不碰 `lifecycle_state` / `last_support_ts` / `contradiction_pressure`）
- ❌ 碰 frozen contract（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / 4 handlers / SAGE 写入 / AGENCY_TRIGGER payload schema）
- ❌ 新增持久化 / schema / 状态字段（三态每次现算，0 新存储）
- ❌ 改任何 code、不 commit、不 push（等验收）

---

## 6. Frozen Contract 检查

**结论：0 change，无 CONTRACT CONFLICT。**

| Frozen contract | 本设计的关系 |
|---|---|
| Agency 4 stages（`src/agency/stages.py`） | 不碰。Stage 2 仍是 Trigger Authorization |
| TriggerEnvelope（`src/agency/trigger.py`） | 不碰。字段与语义不变 |
| InnerLifeEvent（`src/inner_life/event.py`） | 不碰。TA-2 不产生、不引用 InnerLifeEvent |
| 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler） | 不碰 |
| SAGE 写入逻辑 | 不碰 |
| AGENCY_TRIGGER payload schema（`src/eventbus/schema.py`，M5.2-G） | 不碰。TA-2 不进入 payload |
| scheduler 职责（wake/opportunity） | 不碰。TA-2 是 read-side 情境投影，不改变 scheduler |
| soul-elevation（SE-5） | 不碰。TA-2 只读不写（§3.4） |
| Motive → Decision 四元决策（SM-3/SM-4） | 不碰。TA-2 只改情境（prompt 内容），不改 Decision 机制（§3.3） |

---

## 7. 验收对照

| 验收项（工单） | 本文位置 | 状态 |
|---|---|---|
| 三态张力模型（无感/牵挂/释然，非连续） | §2.2 / §3.1 | ✅ |
| M5.13-3 亲密度 Band 复用（不另创心理模型） | §3.2 | ✅ |
| reflect-only 加权边界（不提升 transmit） | §3.3 | ✅ |
| Prompt 三行格式（TEMPORAL ANCHOR，现象化无数字） | §3.5 | ✅ |
| 四大禁止项（不碰 SE-5 / 无连续公式 / 无 per-agent if/else / 无 transmit 捷径） | §3.6 | ✅ |
| 明确「只设计，0 code」 | §0 / §5 | ✅ |
| 不碰 frozen contract | §6（0 change，无 CONTRACT CONFLICT） | ✅ |
| 不建新引擎（第六个引擎） | §5 | ✅ |
| 不改任何 code、不 commit、不 push | 唯一产出物为本文档 | ✅ |

---

## 8. 下一步（供主大脑参考，非本工单范围）

- **TA-2（IMPLEMENTATION）**：落地 TEMPORAL ANCHOR 生成器（interpretation 模式，输入 = TA-1 信号 + 历史节奏 + M5.13-3 Band）+ 两处 read-side 注入（proxy.py 表达路径 + Decision prompt Relevant context）+ 测试（含「牵挂态不提升 transmit」「释然态不遗忘」「无 per-agent if/else」「0 frozen contract 改动」断言）。
- 待定项（TA-2 实作前需主大脑/ Owner 拍板）：亲密度资格门槛具体 Band（§3.2 建议熟悉 ≥ 0.5）、TEMPORAL ANCHOR 注入 Decision prompt 的时机与频率、生成器 model/temperature 选择（花钱事项，需 Owner）。
