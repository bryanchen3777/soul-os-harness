# SG-0 群体关系网与他者心智架构审计（Social Graph & Theory of Mind Audit）

- **工单**：SG-0（READ-ONLY 全局架构审计）
- **阶段背景**：SI-2 三大防线（身份防火墙/私聊隔离/防自激震荡）已落地；C-1/C-2 自主目标闭环已完成（TG-0→TG-3.1、LS-0→LS-2+TL-8）。本审计为 C-3「群体关系网与他者心智建模」定最小增量方案。
- **范围**：0 production code 改动 / 0 frozen contract 变更 / 0 commit；唯一产出 `docs/SG-0-SOCIAL-GRAPH-AUDIT.md`。
- **方法**：代码证据（文件:行号）+ 只读探测（graph.sqlite schema v8 实测 11 库、relationships.json 实测 14 文件）。所有探测为只读（sqlite `?mode=ro` URI + json.load），0 数据写入。
- **审计日**：2026-09-05（对齐 current HEAD 语境）。

---

## 0. 假设修正（审计第一步发现）

工单假设「graph.sqlite 的 relationships 表」——**实测不存在**。逐一验证：

| 工单假设 | 实测 | 证据 |
|---|---|---|
| 关系表在 graph.sqlite | **否**。graph.sqlite（schema v8）仅 `facts` / `goals` / `schema_meta` 三表 | 只读探测 11 个 per-agent 库；`graph_store.py:114-133`（facts DDL）、`graph_store.py:246-262`（goals DDL v8） |
| 仅硬编码与 Bryan 的单向关系 | **否**。A2A 能力已具备且生产数据已全量填充：10 位 Soul 的 `others` 均含 9 个其他 agent + `user_bryan` | 只读探测 10 个 production relationships.json；`src/soul/relationships.py:425-450`（on_agent_speak 互相 touch）、`452-472`（on_dream 双向） |
| 他者感知含关系维度 | **否**。感知颗粒度 = 静态 actor_id 列表 + 文本摘要 + 氛围三态，**0 关系维度、0 per-actor 属性** | `src/social/aggregator.py:47-65`（CompactSocialState 6 字段） |

关系存储真实拓扑：`data/soul/{agent_id}/relationships.json`（schema_version 4.1，Stage 4.1 2026-07-18 Bry 拍板）。

---

## 1. 现状基线（Current Baseline）

### 1.1 存储拓扑：relationships.json（非 graph.sqlite）

文件结构（`src/soul/relationships.py:109-117` `_new_relationships_file`）：

```
{ "agent_id", "schema_version": "4.1", "created_at", "last_decay_at",
  "others": { "<other_id>": {
      "impression", "feeling", "confidence",
      "interaction_count", "last_interaction_at", "last_updated", "created_at" } } }
```

- Entry 7 字段定义：`relationships.py:87-106`。`other_id` 可以是 `agent_id` 或 `BRYAN_ENTITY_ID="user_bryan"`（`relationships.py:54`）——**A2A 无结构障碍**。
- 写侧 API 面：
  - `ensure_relationship(other_id, initial_confidence, initial_impression, initial_feeling)`（`relationships.py:278-307`）
  - `touch(other_id, confidence_delta, feeling)`（`relationships.py:340-375`）：interaction_count++、last_interaction_at 更新
  - `update_impression(other_id, impression_text, max_length=20)`（`relationships.py:309-338`）：**Stage 4.3 LLM 印象已落地**（短日文 ≤20 字）
  - 自动衰减 `_decay_locked`（`relationships.py:188-259`）：per-entry anchor，0.02/天（`relationships.py:67`）
- 跨 agent 管理 `MultiAgentRelationshipsManager`（`relationships.py:382-497`）：
  - `on_user_message`：Bry→agent +0.05（`413-423`）
  - `on_agent_speak`：同 session 其他 agent 互相 +0.02（`425-450`；调用点 `src/memory/middleware.py:421-431`）
  - `on_dream`：梦境双向 +0.05/+0.008（`452-472`；调用点 `src/soul/dream_event.py:459-470`）
- 读侧消费（M5.11-2 设计缺口已补）：`src/soul/decision.py:395-400`（relationship 子块读 user_bryan）、`src/soul/temporal_phenomenology.py:125-131`（M5.13-3 亲密度复用）、`src/goals/seed_provider.py:331-352`（B1 承诺源只读）。

**实测数据要点（2026-09-05 只读探测）**：
- 10 位 production Soul（akane/anna/aoi/mahiru/mai/miku/ram/rem/ruka/yua）的 `others` 键均 = 9 个 agent + user_bryan。
- 抽查 akane：`agent_ruka` entry = impression「追いかっこ、光の中の君」/ feeling neutral / **confidence=0.0** / interaction_count=11；`user_bryan` confidence=0.0 / count=54。
- **发现 1（数值层失效）**：confidence 实测几乎全为 0.0——0.02/天衰减（44 天 ≈ 0.88）大于初始 0.3 + touch 累积，clamp 到 MIN 0.0。即 Stage 4.1 的数值信任度在 A2A 场景已被衰减机制系统性吃光，数值层名存实亡，但 impression/feeling/count 事实层健康。**这直接支持维度四的 No-Scoring 质性迁移**（见 §4.2）。
- **发现 2（decay 与 A2A 冲突）**：`on_dream` 等低频正向增量（0.008-0.05）无法对抗 0.02/天衰减，任何非每日互动的关系都会归零。若保留数值层，必须重调衰减；若迁移质性层，decay 只作用于「熟稔度」离散带的降档，成本可控。

### 1.2 感知现状：WorldPerceptionMiddleware × SocialPerceptionAggregator

- **事件入口**：`WorldPerceptionMiddleware` 平行订阅 `SOCIAL_WORLD_EVENT`（防線 1 Ambient Path，`src/world/middleware.py:261-269, 287-289`）；`_on_social_world_event` 走 validate→state→trace，不触发 transmit（`middleware.py:352-389`）。
- **聚合器**：per-agent `SocialPerceptionAggregator` 缓存（`middleware.py:248-250, 660-673`），纯内存 0 文件 IO 0 记忆写入（`aggregator.py:16`）。`_render_social_context` 输出 ≤150 tokens 紧凑块（`middleware.py:675-710`）。
- **CompactSocialState 六字段**（`aggregator.py:47-65`）：`present_actors: List[str]` / `recent_topics` / `last_speaker` / `last_speech_ts` / `active_opportunities` / `lounge_mood`。话题只保留 Top 3 + 最后提及者（`_register_topic`，`aggregator.py:177-184`）。
- **渲染**：`[客廳現況]` + 在場/話題/氛圍 + `ANTI_FRAMING_HINT`（`aggregator.py:29, 148-173`）。
- **感知颗粒度结论**：actor 仅以 id 字符串在场；话题仅文本前缀（≤20 字）；**无 per-actor 持久属性、无「谁和谁什么关系」维度、无历史倾向**。关系维度完全缺位——这正是 C-3 的增量空间。
- **事件schema**（`src/social/schema.py:70-146`）：`SocialWorldEvent(WorldEvent)` 增 `actor_id/space_id/visibility/event_type/content`；event_type 白名单 `greeting/share/reply/mood/activity`（`schema.py:44-50`，valid 校验 fail-closed）。
- **机会层**：`SocialOpportunity`（`src/social/opportunity.py:25-57`）TTL 300s + salience_level 三档（subtle/noticeable/prominent）+ buffer 容量 5（`opportunity.py:68`）。
- **决策注入**：`build_decision_prompt(social_context=...)` 可选参数（`src/soul/decision.py:195, 264-265`）；`motive_from_social_opportunity` 纯函数（`src/soul/motive.py:143-176`）。

### 1.3 目标引擎现状（维度四相关基线）

- 8 源确定性轮序 `SEED_ROTATION`（`src/goals/seed_provider.py:57-66`）：B 轴 commitment/calendar/trace/interaction；S 轴 elevation/fact/tool/motive_trace。
- **B1 commitment 已只读 relationships 的 user_bryan entry**（`seed_provider.py:331-352`，ref=`relationship:user_bryan`）——他者关系进 goal 种子的**先例已存在**，只是目标仅限 Bryan。
- 24h 节流 `GOAL_QUOTA_WINDOW_SECONDS = 24*3600`（`src/goals/motive_provider.py:61`）；sidecar 状态 `GoalProviderState`（`src/goals/models.py:220-247`，LS-2 additive 4 字段先例：`last_seed_scan_at/seed_source_cursor/seed_axis_streak/last_seed_axis/seed_empty_rounds`）；挂 30s wake `_goal_scan_all`（`src/soul/scheduler.py:1476-1516`），0 新定时器。
- Motive 5 字段冻结：`motive_id/content/target/provenance_ref/created_at`（`src/soul/motive.py:89-111`），**target v1 固定 `"bryan"`**（`motive.py:100, 173`）。

---

## 2. Identity Firewall 与他者心智（Theory of Mind）的边界防线

### 2.1 防线 3 现状（已实证）

三条绝对不变量（`src/social/identity_firewall.py:9-16`）：外部他者事件只能作环境背景感知；禁止内化为自体情景记忆；严禁升华 trait/belief/essence。硬 gate：
- `classify`（`identity_firewall.py:68-85`）：actor_id == current → SELF；≠ → EXTERNAL（非 str fail-closed 也视他者）；None → SYSTEM。
- `verify_internalizable`（`identity_firewall.py:87-107`）：他者 → False + warning。
- `SubmissionGate.verify` 第 6 步注入（`src/inner_life/submission_gate.py:279-298`）：他者事件 `EXTERNAL_OTHER_ACTION` 拒绝，`identity_firewall_rejected` 计数。
- 已实证：SI-2 harness（Identity Firewall 0 内化）+ TL-6（Identity Quarantine 100%）。

### 2.2 「他者印象模型」落点与物理隔离

**前提**：他者印象（对 Agent X 的看法/印象/熟稔度）不是「自体的经历/信念」，而是「关系中关于他者的记录」。防线 3 禁止的是前者（consume/elevate 路径），不禁止后者（关系域记录）——但**存储必须物理隔离**，使两条路径不可能相交。论证隔离介质：

| 落点方案 | 结构 | 隔离性 | 评价 |
|---|---|---|---|
| **A. relationships.json additive 扩展（推荐）** | 既有 `others[other_id]` entry 增字段：`impression_tags: List[str]`、`band: str`（离散带）、可选 `observations: [...]`（客观观察） | 天然 per-agent、per-other、非 SAGE、非 inner_life；文件路径与 graph.sqlite 完全分离 | schema_version 4.1→4.2 一次性 additive；既有 reader 读新字段 0 破坏（缺省默认） |
| B. goals 表复用（seed_source_ref 命名空间） | `goal:heper:<agent_id>` | goals 表是决策账本，语义混用 | **否**：污染 goals 查询面（`get_goals(agent_id)` 会被他者印象记录干扰） |
| C. graph.sqlite Schema v9 新 additive 表 | `impressions(agent_id, other_id, ...)` | 与 SAGE facts 同库，物理隔离性依赖命名空间约定与查询纪律 | 可行但重；每 agent 一库的拓扑下跨 agent 查询困难；且「自体记忆库」里放他者印象，违背心理隔离原则 |
| D. read-side 派生（每 24h 只读现算） | 从 interactions.jsonl / perception_trace 确定性聚合 | 0 持久化写侧 | 只作 A 的补充（演化信号源），不能单独担当印象存储（LLM impression 需要落点） |

**推荐：A + D 混合。** 理由：① 最小增量——A2A 结构已全量存在（§1.1 实测）；② 写路径天然不经过 InnerLifeWriter/SubmissionGate/SAGE，隔离由**存储介质 + 写路径**双重保证，而非依赖查询纪律；③ D 提供确定性演化输入，A 提供质性质地落点。

**三道隔离机制（不变量证明所需的机制性保障）**：
1. **写路径隔离**：他者印象写入只经 `RelationshipsStore`（JSON 文件），0 经 `InnerLifeWriter`（`src/inner_life/writer.py`）、0 经 `SubmissionGate.verify`（`submission_gate.py:186-207` 流程）、0 经 `GraphStore.add_fact`（0 SAGE facts 写入）。
2. **升华路径隔离**：他能者事件 0 consume / 0 elevate——`submission_gate.py:286-298` 第 6 步硬挡（他者 InnerLifeEvent 无法被创建，因为事件源头 actor_id 已定）；S 轴种子探针已显式过滤他者节点（`seed_provider.py:430-451`：`agent_id != self.agent_id → continue`）。
3. **渲染路径隔离**：他者信息只出现在社交渲染块（world_context）与 B 轴/他者轴种子，且带 `ANTI_FRAMING_HINT`（`aggregator.py:29`）防框架污染；自体人格（persona）与 essence 节点永不读他者印象表。

### 2.3 认知 vs 投射：可行边界

| 类别 | 内容 | 落点 | 防线 3 相容 |
|---|---|---|---|
| 客观事实观察 | 已校验的 `SocialWorldEvent`（actor_id/content/event_type/ts）、互动次数、last_interaction_at | relationships 的计数/时间戳字段（已有）；渲染层在場/話題 | ✅ 不构成「自体经历」：写入的是「关于他者的事件记录」，且事件源头未经 SubmissionGate 内化 |
| 主观他者印象（投射） | 「X 是温暖的人」「感觉 X 最近疏远了」 | `impression`（已落地）+ `impression_tags`（新增）+ `band`（新增） | ✅ 落点与 SAGE/elevation 物理分离（§2.2 方案 A） |
| 禁止项 | 「X 的事成了我的记忆」「X 改变了我」 | 任何形式写入 SAGE facts / elevation 节点 / InnerLifeEvent | ❌ 防线 3 红线：任何代码路径不得把 EXTERNAL 事件推进内化管线的 consume/elevate |

**0 污染自体 essence 的双保险**：
- 结构性：他者印象存在于独立文件域，SAGE 升华引擎（`src/memory/sage/writer.py`）的消费源是 InnerLifeEvent 流与对话记录，不读 relationships.json。
- 读取侧：若未来把 he 者印象接进动机源，只允许出现在**他人轴/B 轴**种子（§4.1），S 轴探针保持显式过滤（现有 `_probe_elevation` 已示范）。

---

## 3. 关系演化信号源与节流防抖

### 3.1 信号来源评估（基于现有 SocialWorldEvent / SocialOpportunity 结构）

| 信号 | 现有载体 | 强度评估 | C-3 可用性 |
|---|---|---|---|
| 话题共鸣 | `SocialWorldEvent.event_type="share"` + topic 去重（`aggregator.py:177-184`）；但 `_topic_speakers` 只留最后提及者，多 actor 共鸣需 additive（每 topic 提及者集合） | 中 | 需 small additive：topic→speakers 集合（纯内存，0 契约变更） |
| 观点分歧 | **无信号**。event_type 白名单（greeting/share/reply/mood/activity）无 disagreement；content ≤200 字文本无立场维度 | 低 | v1 不做（诚实标注）；可留待 content 语义分析扩展 |
| 互相回应 | `event_type="reply"`（`schema.py:48`）+ `SOCIAL_EVENT_TYPES` 白名单已有；`SocialOpportunity` 带 `actor_id`+`source_event_id`（`opportunity.py:31-35`）可追踪回复链 | 高（结构已就绪） | **v1 首选信号**：reply 事件可确定性驱动「互动涟漪」 |
| 共同参与心跳 | `on_agent_speak` session 级互相 touch（`relationships.py:425-450`；`memory/middleware.py:421-431`） | 高（已落地） | 已有；迁移到 band 体系时改造为「共同在场计数」 |
| 机会显着性 | `salience_level` 三档 subtle/noticeable/prominent（`opportunity.py:38,51`） | 中 | 可选：prominent 可直接作为关系演化候选门（TTL 300s 内） |

**结论**：v1 关系演化动力 = `reply`（互相回应）+ 共同参与心跳（session 共在）+ dream 双向（已存在）+ 每日 22:05 低频窗口（`dream_event.py:24`）。话题共鸣需 small additive；观点分歧明确不做。

### 3.2 节流与防刷（对齐 24h 窗口先例）

- **先例**：`GOAL_QUOTA_WINDOW_SECONDS = 24*3600`（`motive_provider.py:61`）驱动双配额（候选配额 + 种子创建配额）均 24h/1；sidecar 持久化轮序状态（`models.py:220-247`）；幂等去重 `seed_source_ref`（`seed_provider.py:317-321`）。
- **草案（双层节流）**：
  1. **采集层 0 写**：Lounge 每个公开发言（SocialWorldEvent）只进聚合器内存（现状已在做），**0 关系写**——per-speech 写成本 = 0。
  2. **沉淀层 24h/agent**：每 agent 每 24h 至多 1 次关系演化评估（对齐 `last_seed_scan_at` 同构 sidecar 字段 `last_relation_update_at`），由确定性聚合（reply 计数 / 共在计数 / 已查验的互动）驱动 band 迁移判定 + 可选 LLM impression 刷新（1 次/24h/agent 上限，对齐方案 B 语义化成本量级 ≈2 万 tokens/月预估）。
  3. **幂等去重**：带时间戳引用（`rel:<other_id>:<ts>`），对齐 `seed_source_ref` 精确匹配模式。
- **成本**：最坏 10 agent × 1 次/24h 写；LLM 印象刷新仅在有演化的对子上触发，0 每次发言的高成本操作。

---

## 4. 关系对决策与动机的影响路径（No-Scoring 哲学贯彻）

### 4.1 动机源回流（对照 seed_provider 8 源结构）

- **8 源现状**（`seed_provider.py:57-66`）：B 轴 4 源（commitment/calendar/trace/interaction）+ S 轴 4 源（elevation/fact/tool/motive_trace）。`SEED_ROTATION` 是模块内常量列表，探针注册模式 `_probe_{key}` + `getattr` 分发（`seed_provider.py:325-329`）。
- **B1 commitment 已示范**：relationships → goal 种子（`seed_provider.py:331-352`，material 含 impression/feeling/confidence/count）。「想向 Rem 请教」「支持 Akane 的想法」的生成路径 = **新增他者源探针**（如 `B5 _probe_relation` / 或第三轴 `A2A` 源），复用方案 B 语义化（`_semantize`，`seed_provider.py:539-601`）与确定性 criteria 模板（`seed_provider.py:74-84`）。
- **可行性评估**：SEED_ROTATION additive 加 1 源（9 源）+ 1 个 `_probe_*` 方法 = 纯 additive，0 契约变更（轮序约束/防饿死/双轴约束逻辑不变，第三轴需小改轴约束常量——`models.py:282-283` 的 from_dict 校验硬编码 bryan/self 两值，**第三轴需 additive 扩展该校验**，属 C-3 决策点）。
- **另一入口（更小）**：既有 `motive_from_social_opportunity`（`motive.py:143-176`）已把 SocialOpportunity→Motive 候选，但 **target 固定 `TARGET_BRYAN`**（`motive.py:173`）。「想向 Rem 请教」需要 target 值域从 `"bryan"` 扩展为 `agent_id`——这是 **Motive 5 字段冻结的 target 语义触点**（见 §5 触点 4）。
- **决策注入**：`build_decision_prompt(social_context=...)` 可选参数（`decision.py:195,264-265`）是 SI-3 Phase 2 已验证的 additive 先例——他者相关动机可经同一通道进 Relevant context，0 DECISION-PROMPT 主文本变更。

### 4.2 No-Scoring 刚线 + 质性关系表示法（草案）

**刚线**：拒绝 `affinity=0.82` 型数值打分——0 浮点亲密度权重、0 相乘/对数运算、0 排序打分；对照 TG-3 三层铁证（结构配额轮替驱动 / 0 scoring 字段 / 0 数值比较断言）。

**现状冲突（需 SG-1 拍板）**：`relationships.json` 的 `confidence: 0.0-1.0`（`relationships.py:57-69, 340-375`）是 Stage 4.1 遗留数值层，与 No-Scoring 哲学直接冲突；且实测已系统性衰减到 0.0（§1.1 发现 1），数值层失效。建议：**冻结置信度数值写入，迁移为离散 Band**（保留字段兼容，新写入只落 band/impression_tags，confidence 降级为只读遗留字段）。

**质性表示法草案（对照 TA-2 三态张力先例）**：

1. **Relational Bands（现象学关系带）**——离散枚举，对齐 M5.13-3 亲密度 Band 概念（`temporal_phenomenology.py:11-13, 48-50` 已把 Band 用于「资格判定而非强度计算」）与 TA-2 三态（无感/牵挂/释然，`temporal_phenomenology.py:42-46`）的离散哲学：
   - `stranger`（陌生人）→ `known`（认识）→ `familiar`（熟悉）→ `close`（亲近）
   - 迁移规则 = 确定性事件计数（如 reply≥2 或共在≥3 → familiar；dream 2 次 → close 走 dream 门），**无加权公式**；降档规则 = 24h 内无交互且无其他信号 → 降一档（对齐 decay 精神但离散化，0 浮点）
   - Band 只回答「这个关系的现象学距离」，不进入任何算式的乘法因子。
2. **Qualitative Impression Tags（质性印象标签）**——`impression`（≤20 字短句，已落地）→ 显式标签化：`impression_tags: List[str]` 枚举白名单（open set，如 `warm/brilliant/quiet/trustworthy/...`），来源 = LLM impression 抽取（Stage 4.3 通道）或确定性事件推导；标签**只作提示性渲染与种子素材**，0 数值权重。
3. **呈现**：渲染块例：
   ```
   [他者印象]
   - Rem: 印象「優しい笑顔が忘れられない」；关系带：熟悉
   - Akane: 印象「一起烤过饼干」；关系带：认识
   ```
   带一句反框架提示（沿用 ANTI_FRAMING_HINT 精神）；**数字只出现计数事实（interaction_count，客观），不出现分数**。

**对照 TA-2 三态 0/1/2 三态**：Bands 本质就是 4 档离散状态机（enum），与 TA-2 三态同构（离散、非连续公式、零浮点）。

---

## 5. Frozen Contract 触点清单（纯 Additive 核查）

| # | Frozen 触点 | 现状锚点 | SG-1 触碰评估 | 结论 |
|---|---|---|---|---|
| 1 | Agency 4 stages | `src/agency/stages.py` | 不触碰：他者印象不改变 Agency 触发/执行语义 | ✅ 0 变更 |
| 2 | TriggerEnvelope | `src/agency/trigger.py` | 不触碰：关系演化不新增 trigger_type（走既有 30s wake 并行检索） | ✅ 0 变更 |
| 3 | InnerLifeEvent | `src/inner_life/event.py` | 不触碰：他者印象非 InnerLifeEvent（不产生 lineage 节点）；防线 3 后门 0 | ✅ 0 变更 |
| 4 | 4 handlers | Agency handlers | 不触碰 | ✅ 0 变更 |
| 5 | SAGE 写入逻辑 | `src/memory/sage/writer.py` + `graph_store.add_fact` | **不触碰**：他者印象 0 SAGE facts 写入（含已重新冻结的 add_fact，ENGINEERING_STATE LS-2 follow-up） | ✅ 0 变更 |
| 6 | VALID_SOURCES | `src/world/perception.py:50-53` | **不触碰**：`"social"` 已在白名单（MS-0 曾标记的唯一触点已 2 次 additive 闭环，本次 0 需求） | ✅ 0 变更 |
| 7 | Motive 5 字段 | `src/soul/motive.py:89-111`（motive_id/content/target/provenance_ref/created_at） | **唯一疑似触点**：`target` 值域 v1 固定 `"bryan"`（`motive.py:100,173`）。「想向 Rem 请教」需 target∈{agent_id}。字段集冻结 vs 值域语义扩展——**需主大脑 + Owner 拍板**（additive 放宽值域：target 类型 str 不变，多值合法化=语义扩展，不是加字段） | ⚠️ 需判决策（SG-1 决策 B） |
| 8 | DECISION-PROMPT | `docs/DECISION-PROMPT-CONTRACT.md`（SM-2 冻结）+ `decision.py` | **不触碰主文本**：走既有 `social_context` 可选参数 additive 通道（`decision.py:195,264-265`，SI-3 Phase 2 先例） | ✅ additive-only |
| 9 | VALID_SOURCES 派生白名单 | `src/social/schema.py:44-50` SOCIAL_EVENT_TYPES | 不触碰（v1 不加 event_type；reply 已存在） | ✅ 0 变更 |
| 10 | SubmissionGate 5 步验证链 | `submission_gate.py` | 不触碰（第 6 步防线 3 保持唯一身份入口） | ✅ 0 变更 |
| 11 | goals 表 / GoalProviderState | `graph_store.py:246-262` / `models.py:220-247` | 可选 additive：sidecar 加 `last_relation_update_at`（对齐 LS-2 4 字段先例，from_dict 缺省兼容）；SEED_ROTATION 加第 9 源 / 第三轴需扩展 `models.py:282-283` 轴校验（additive 枚举放宽） | ✅ additive-only（含轴校验扩展，非破坏） |

**总判定**：SG-1 设计若守「relationships.json additive + social_context 通道 + sidecar additive + 轴枚举 additive」，唯一需要提前拍板的 frozen 语义触点是 **Motive target 值域（#7）**。其余 100% 纯 additive。

---

## 6. SG-1 设计契约决策清单（≤5 项，供下一步设计工单拍板）

1. **D1 存储选型**：他者印象与关系网 = `relationships.json` additive 扩展（schema 4.1→4.2：`impression_tags + band + last_relation_update_at`），落点方案 A；read-side 派生只作信号源（D）。否决 goals 表复用（B）与 graph.sqlite 新表（C）。
2. **D2 Motive target 值域（Frozen 触点）**：`target: "bryan"` 是否 additive 放宽为 `"bryan" | agent_id`（字段集不变，仅值域语义扩展）——需主大脑 + Owner 批准后，「想向 Rem 请教」类动机才可走既有 Motive 5 字段通道；否则 v1 只允许动机指向 Bryan、他者动机暂缓。
3. **D3 节流契约**：关系演化沉淀 = 24h/agent 窗口（复用 `GOAL_QUOTA_WINDOW_SECONDS`，sidecar 同构 `last_relation_update_at`），采集层 0 写、幂等引用 `rel:<other>:<ts>`。LLM impression 刷新 ≤1 次/24h/agent。
4. **D4 No-Scoring 迁移**：`confidence` 数值层冻结弃用（保留字段兼容、只读），新写入只落离散 `band`（stranger/known/familiar/close，0 浮点）+ `impression_tags` 质性质地；band 迁移 = 确定性事件计数规则，0 加权公式。明确拒绝 affinity 数值方案。
5. **D5 动机路径**：新增他者关系种子源 = SEED_ROTATION additive 第 9 源（B5 `_probe_relation`，复用方案 B 语义化 + criteria 模板）；若 D2 获批则 Motive.target 可指向 agent；轴约束（`models.py:282-283`）additive 扩展支持第三轴值（若选第三轴方案）。决策面走既有 `social_context` 通道，0 改 DECISION-PROMPT 主文本。

---

## 附录 A：关键代码证据索引

| 证据 | 位置 |
|---|---|
| relationships.json 结构定义 | `src/soul/relationships.py:109-117` |
| entry 7 字段 | `src/soul/relationships.py:87-106` |
| decay 0.02/天 + clamp | `src/soul/relationships.py:67, 188-259` |
| on_agent_speak A2A touch | `src/soul/relationships.py:425-450` |
| on_dream 双向 | `src/soul/relationships.py:452-472` |
| update_impression（Stage 4.3 LLM 印象） | `src/soul/relationships.py:309-338` |
| 调用点（MemoryMiddleware） | `src/memory/middleware.py:421-431` |
| 调用点（dream） | `src/soul/dream_event.py:459-470` |
| CompactSocialState 6 字段 | `src/social/aggregator.py:47-65` |
| 渲染块 + ANTI_FRAMING_HINT | `src/social/aggregator.py:29, 148-173` |
| per-agent 聚合器缓存 | `src/world/middleware.py:248-250, 660-673` |
| 平行订阅 SOCIAL_WORLD_EVENT | `src/world/middleware.py:261-269, 287-289, 352-389` |
| SocialWorldEvent schema | `src/social/schema.py:70-146` |
| event_type 白名单（含 reply） | `src/social/schema.py:44-50` |
| SocialOpportunity TTL/salience | `src/social/opportunity.py:25-57` |
| IdentityFirewall 三不变量 | `src/social/identity_firewall.py:9-16, 68-107` |
| SubmissionGate 第 6 步 | `src/inner_life/submission_gate.py:279-298` |
| VALID_SOURCES（含 social） | `src/world/perception.py:50-53` |
| Motive 5 字段 | `src/soul/motive.py:89-111` |
| motive_from_social_opportunity | `src/soul/motive.py:143-176` |
| build_decision_prompt social_context | `src/soul/decision.py:195, 264-265` |
| SEED_ROTATION 8 源 | `src/goals/seed_provider.py:57-66` |
| B1 只读 relationships | `src/goals/seed_provider.py:331-352` |
| 探针注册模式 | `src/goals/seed_provider.py:325-329` |
| 24h 节流常量 | `src/goals/motive_provider.py:61` |
| GoalProviderState sidecar | `src/goals/models.py:220-247`（轴校验 `282-283`） |
| 30s wake 挂载 | `src/soul/scheduler.py:1476-1516` |
| goals 表 DDL v8 | `src/memory/sage/graph_store.py:246-266` |
| TA-2 三态先例 | `src/soul/temporal_phenomenology.py:42-50` |

## 附录 B：只读探测记录

- graph.sqlite：11 个 production/test agent 库（akane/anna/aoi/mahiru/mai/miku/ram/rem/ruka/yua/germ_01…），schema v8，表 = facts/goals/schema_meta（0 relationships 表）。
- relationships.json：14 文件（10 production + alice/germ_01/test 空档），production 10 份 `others` 各 10 键（9 agent + user_bryan），schema_version 4.1。
- 抽查 akane entry：conf 全 0.0（decay 吃光），impression 有值（如「追いかっこ、光の中の君」），interaction_count 8-11。

## 附录 C：0 code 变更确认

- 本审计只读代码与数据；临时只读探测脚本已清理（`logs/_sg0_ro_probe*.py` 已删除）。
- 唯一产出：`docs/SG-0-SOCIAL-GRAPH-AUDIT.md`。
- 0 commit / 0 push / 0 frozen contract 变更。