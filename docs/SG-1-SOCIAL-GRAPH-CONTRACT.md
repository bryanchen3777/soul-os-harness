# SG-1 群体关系网与他者心智设计契约（Social Graph & Theory of Mind Contract）

- **工单**：SG-1（docs-only，0 code，0 commit——收尾工单统一做）
- **阶段背景**：C-3「关系演化 + 他者心智」。依据 `docs/SG-0-SOCIAL-GRAPH-AUDIT.md`（§1 现状基线 / §2 防线 3 / §6 决策清单）与 Owner 已拍板的 D1-D5 定稿决策，产出决策完备的实现契约。
- **范围**：唯一产出 `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md`；0 src/ 改动、0 commit、0 push。
- **契约日**：2026-09-05（对齐 current HEAD 语境）。

---

## §1 决策记录（D1-D5 总表，Owner 已拍板）

| # | 决策 | 拍板状态 | 核心内容 | 授权边界 | 代码证据 |
|---|---|---|---|---|---|
| **D1** | 存储选型 **A**（relationships.json additive 扩展） | ✅ Owner 拍板 | schema 4.1 → 4.2；物理隔离 = 存储介质 + 写路径双保证（0 经 InnerLifeWriter / SubmissionGate / SAGE）；客观观察与主观投射分区落字段，0 污染 essence | 否决 B（goals 表复用）、C（graph.sqlite 新表）；read-side 派生（D）只作演化信号源，不单独担当印象存储 | `src/soul/relationships.py:109-117`（schema 4.1 文件结构）、`:138`（file_path = `data_dir/relationships.json`）；graph.sqlite 仅 facts/goals/schema_meta 三表（SG-0 §0 实测，`graph_store.py:114-133,246-262`） |
| **D2** | Motive.target 值域解冻（已授权） | ✅ Owner 拍板 | `"bryan"` → `{"bryan", *agent_ids}`；放行范围**仅** target 栏位值域 + 相应 Validator | Motive 其余 5 字段（motive_id/content/provenance_ref/created_at）与结构冻结不动；v1 允许 agent-target 动机产生与四元 Decision 选择；**0 新消息投递通道**——契约定死：复用既有公开频道（见 §5） | `src/soul/motive.py:89-111`（Motive 5 字段）、`:77`（`TARGET_BRYAN = "bryan"`）、`:100,173,623`（target 固定点）、`:143-176`（motive_from_social_opportunity） |
| **D3** | 节流契约 | ✅ Owner 拍板 | 采集层 0 写；沉淀层 24h/agent 窗口（复用 `GOAL_QUOTA_WINDOW_SECONDS`）；sidecar 同构（对照 GoalSeedProvider / GoalMotiveProvider 的 goal_provider.json 先例）；幂等引用防重复沉淀 | LLM impression 刷新 ≤1 次/24h/agent（对齐方案 B 语义化成本量级）；挂既有 30s wake，0 新定时器 | `src/goals/motive_provider.py:61`（`GOAL_QUOTA_WINDOW_SECONDS = 24*3600`）、`src/goals/models.py:220-247`（GoalProviderState sidecar）、`src/soul/scheduler.py:1476-1516`（`_goal_scan_all` 挂 30s wake） |
| **D4** | 关系域 confidence → 离散 Relational Bands（已授权） | ✅ Owner 拍板 | stranger / known / familiar / close 四带 + 质性 impression_tags（Stage 4.3 LLM 印象已落地）；0 数值衰减（实证已全 0.0 失效）；离散带阶段跃迁（对照 TA-2 三态先例） | **范围仅关系域（A2A/A2U）**：SAGE / Elevation 的 confidence 定义维持原样、不作联动修改；关系域 confidence 降级为只读遗留字段（保留读取，0 新写入） | `src/soul/relationships.py:309-338`（update_impression，Stage 4.3）、`:67`（`CONFIDENCE_DECAY_PER_DAY = 0.02`）、`:188-259`（`_decay_locked`）、`:57-69`（confidence 常量）、`src/soul/temporal_phenomenology.py:42-50`（TA-2 三态离散先例） |
| **D5** | 动机路径 | ✅ Owner 拍板 | 新他者源 = GoalSeedProvider `SEED_ROTATION` additive 第 9 源 **B5**（8 源轮序扩张）；复用方案 B 语义化通道 + `social_context` 既有决策通道 | 目标类型示例：「想找 Rem 讨论音乐」「想支持 Akane 的想法」；轴枚举 `models.py:282-283` 校验 0 变更（B5 属既有 AXIS_BRYAN 轴） | `src/goals/seed_provider.py:57-66`（`SEED_ROTATION` 8 源）、`:331-352`（B1 commitment 已只读 relationships）、`:539-601`（`_semantize` 方案 B 语义化）、`src/soul/decision.py:195,264-265`（`social_context` 既有决策通道） |

### 1.1 D1 存储落点方案对比（成本/复杂度标注，SG-0 §2.2 定稿）

| 方案 | 结构 | 隔离性 | 成本/复杂度 | 结论 |
|---|---|---|---|---|
| **A. relationships.json additive**（**选定**） | entry 增 `objective{}` / `impression_tags[]` / `relational_band` / 幂等引用键 | 天然 per-agent、per-other、非 SAGE、非 inner_life；文件路径与 graph.sqlite 完全分离 | **最低**：schema_version 4.1→4.2 一次性 additive，新字段缺省即兼容，0 迁移 | ✅ |
| B. goals 表复用 | `goal:heper:<agent_id>` 命名空间 | 同库，依赖查询纪律 | **中高**：污染 goals 查询面（`get_goals(agent_id)` 会被他者印象记录干扰），需每处查询过滤 | ❌ 否决 |
| C. graph.sqlite Schema v9 新表 | `impressions(agent_id, other_id, ...)` | 与 SAGE facts 同库，违背心理隔离原则 | **高**：每 agent 一库拓扑下跨 agent 查询困难；「自体记忆库」放他者印象 | ❌ 否决 |
| D. read-side 派生（24h 只读现算） | 从 interactions.jsonl / perception_trace 确定性聚合 | 0 持久化写侧 | **低**（0 写侧）但**不完整**：LLM impression 需落点 | 仅作 A 的演化信号输入，不单独采用（D3 §4） |

---

## §2 关系存储 schema 4.2 契约

### 2.1 文件结构（schema_version 4.1 → 4.2）

```json
{
  "agent_id": "agent_rem",
  "schema_version": "4.2",
  "created_at": "...",
  "last_decay_at": "...",
  "others": {
    "<other_id>": {
      "impression": "...",
      "feeling": "neutral",
      "confidence": 0.0,
      "interaction_count": 11,
      "last_interaction_at": "...",
      "last_updated": "...",
      "created_at": "...",
      "objective": {
        "reply_exchanges": 2,
        "co_presence_sessions": 5,
        "dream_exchanges": 1,
        "last_signal_at": "..."
      },
      "impression_tags": ["warm", "quiet"],
      "relational_band": "familiar",
      "band_updated_at": "...",
      "last_relation_update_ref": "rel:agent_rem:2026-09-05T10:00:00+00:00"
    }
  }
}
```

### 2.2 additive 字段契约

| 字段 | 分区 | 类型 | 语义 | 写入时机 |
|---|---|---|---|---|
| `objective.reply_exchanges` | 客观观察 | int（≥0） | 双向 reply 回合计数（成对折抵计 1） | 仅沉淀层 24h 评估窗口 |
| `objective.co_presence_sessions` | 客观观察 | int（≥0） | 共同参与 session 计数 | 仅沉淀层评估窗口 |
| `objective.dream_exchanges` | 客观观察 | int（≥0） | 双向 dream 回合计数 | 仅沉淀层评估窗口 |
| `objective.last_signal_at` | 客观观察 | ISO 8601 / null | 最近一次关系信号时刻 | 仅沉淀层评估窗口 |
| `impression_tags` | 主观投射 | List[str]（open set） | 质性印象标签（如 warm/brilliant/quiet），来源 = LLM impression 抽取（Stage 4.3 通道）或确定性事件推导；**0 数值权重** | ≤1 次/24h/agent（有演化信号时） |
| `relational_band` | 主观投射 | enum：stranger/known/familiar/close | 离散关系带（现象学距离） | 仅沉淀层评估窗口 |
| `band_updated_at` | 主观投射 | ISO 8601 / null | 最近一次带迁移/评估时刻 | 仅沉淀层评估窗口 |
| `last_relation_update_ref` | 幂等引用键 | `rel:<other_id>:<ts>` | 防重复沉淀（对齐 `seed_source_ref` 精确匹配模式） | 每次沉淀写入时 |

### 2.3 新旧数据兼容（0 迁移）

- **旧字段全部保留读取**：`impression / feeling / confidence / interaction_count / last_interaction_at / last_updated / created_at`（`relationships.py:87-106` 7 字段）。
- **新字段缺省**：读侧 `entry.get("relational_band", "stranger")`、`entry.get("impression_tags", [])`、`entry.get("objective", {})`——旧文件（4.1）0 迁移成本即与 4.2 读侧共存。
- **confidence 冻结**：4.1 遗留数值降级为**只读遗留字段**；v1 0 新写入（`touch` 的 confidence_delta 运算与 `_decay_locked` 不再作为关系状态真值来源）；关系状态唯一真值 = `relational_band` + `impression_tags`。
- **`last_decay_at` 文件级字段**：维持 backward-compat 元数据写回（`relationships.py:208` 现状），0 逻辑作用变更。

### 2.4 写路径契约（物理隔离双保证）

- **唯一写入口**：`RelationshipsStore`（JSON 文件，`relationships.py:124-141`）。
- **0 经**（红线）：`InnerLifeWriter`（`src/inner_life/writer.py`）、`SubmissionGate.verify`（`submission_gate.py:186-207` 流程）、`GraphStore.add_fact`（0 SAGE facts 写入）。
- **0 污染 essence**：他者印象存在于独立文件域；SAGE 升华引擎消费源是 InnerLifeEvent 流（`src/memory/sage/writer.py`），不读 relationships.json。
- **渲染/动机只读**：他者印象只出现在社交渲染块（world_context）与 B 轴/他者轴种子；S 轴探针保持显式过滤（`seed_provider.py:430-451` 先例）。

---

## §3 Relational Bands 语义与转移

### 3.1 四带定义（现象学距离，离散状态机，非强度公式）

对齐 TA-2 三态离散哲学（`temporal_phenomenology.py:38-50`：状态是离散枚举，只回答「够不够格」，不进入任何算式乘法因子）：

| Band | 语义 | 对应现象学距离 |
|---|---|---|
| `stranger` 陌生人 | 无互动或仅单向接触 | 远 |
| `known` 认识 | 有双向回应的初步接触 | 中-远 |
| `familiar` 熟悉 | 多次共同参与 + 双向 reply 累积 | 中-近 |
| `close` 亲近 | 长时间深度互动维持 | 近 |

### 3.2 离散计数器（信号事实，全整数，0 浮点）

| 计数器 | 定义 | 信号源（§4） |
|---|---|---|
| `reply_exchanges` | 双向 reply 回合（我 reply 他 且 他 reply 我，成对折抵计 1） | SocialWorldEvent `event_type="reply"`（`schema.py:44-47`） |
| `co_presence_sessions` | 共同参与 session（同客厅共在） | `on_agent_speak` 既有调用链（`relationships.py:425-450`；`memory/middleware.py:421-431`） |
| `dream_exchanges` | 双向 dream 回合（成对） | `on_dream` 既有调用链（`relationships.py:452-472`；`dream_event.py:459-462`，22:05 窗口 `dream_event.py:24,56`） |

### 3.3 转移规则（契约定死：纯整数判定 + 离散阶梯，0 加权公式、0 乘积/对数）

**升带**（任一命中即升，每类信号计数在 24h 评估窗口确定性聚合）：

| 迁移 | 条件（全部命中才升） |
|---|---|
| stranger → known | `reply_exchanges ≥ 1` **或** `co_presence_sessions ≥ 2` |
| known → familiar | `reply_exchanges ≥ 3` **且** `co_presence_sessions ≥ 5` |
| familiar → close | `reply_exchanges ≥ 10` 且 `co_presence_sessions ≥ 15`；或 `dream_exchanges ≥ 4` 且 `reply_exchanges ≥ 5` |

**降带**（对齐 decay 精神但离散化，0 浮点；`CONFIDENCE_DECAY_PER_DAY=0.02` 时代废止）：

| 条件 | 动作 |
|---|---|
| 连续 30 天 `last_signal_at` 无任何新信号（reply/co-presence/dream 全 0 增量） | 下移 1 带（close → familiar → known → stranger） |
| 已在 `stranger` | 不降（底带） |

> **阈值参数声明**：上表数值为契约定稿阈值，实现期经主大脑复核可调，但**形态冻结**——整数计数、离散判定、0 加权、0 浮点、0 排序打分。

### 3.4 与 SAGE / Elevation confidence 的边界声明（0 联动）

- 本契约 Band 体系**只**替换关系域（A2A/A2U）的 confidence 语义（`relationships.py` 内）。
- **SAGE / Elevation 的 confidence 定义维持原样、不作联动修改**（`src/memory/sage/writer.py`、elevation 相关逻辑 0 变更）。
- 关系域 confidence 字段保留读取（旧数据兼容），但不再被任何关系决策/渲染读取为真值来源。

---

## §4 信号源与节流

### 4.1 v1 演化动力（契约定死）

| 信号 | 载体（既有，0 新事件类型） | v1 状态 | 证据 |
|---|---|---|---|
| 双向 reply | `event_type="reply"` 白名单 + `SocialOpportunity` 携带 `actor_id/source_event_id` 可追踪回复链 | ✅ **v1 首选动力** | `schema.py:44-47`、`opportunity.py:31-35` |
| 共同参与心跳 | session 共在（on_agent_speak 互相 touch） | ✅ 已有，迁移为 `co_presence_sessions` 整数计数 | `relationships.py:425-450`、`memory/middleware.py:421-431` |
| dream 双向 | 每晚 22:05 梦境双向（dreamer +0.05 / target +0.008 时代语义 → 迁移为 `dream_exchanges` 成对计数） | ✅ 已有 | `relationships.py:452-472`、`dream_event.py:24,56,459-462` |
| 话题共鸣 | `_topic_speakers` 只留最后提及者（需 additive：topic → speakers 集合，纯内存） | ⏳ **additive 待办**（v1 不做） | `aggregator.py:177-184` |
| 观点分歧 | 无信号：event_type 白名单无 disagreement，content ≤200 字无立场维度 | ❌ **明确不做**（诚实标注） | `schema.py:44-50` |

### 4.2 双层节流（对照 D3，契约定死）

1. **采集层 0 写**：Lounge 每个公开发言（SocialWorldEvent）只进聚合器内存（`aggregator.py:16` 纯内存 0 文件 IO），**0 关系文件写**——per-speech 写成本 = 0。
2. **沉淀层 24h/agent**：每 agent 每 24h 至多 1 次关系演化评估（复用 `GOAL_QUOTA_WINDOW_SECONDS = 24*3600`，`motive_provider.py:61`）；sidecar 同构——`GoalProviderState` add 1 字段 `last_relation_update_at`（对齐 `last_seed_scan_at` 先例 `models.py:230-231,243`；from_dict 缺省兼容，`models.py:262-287`）；评估输入 = 聚合器内存窗口计数（reply/co-presence）+ 既有持久事实（interaction_count/last_interaction_at），**确定性聚合，0 LLM 判定 band**。
3. **LLM 印象刷新**：仅在有演化信号的对子上触发 `impression_tags` 抽取（复用 Stage 4.3 通道 `relationships.py:309-338` 扩展），≤1 次/24h/agent（对齐方案 B 语义化成本量级 ≈2 万 tokens/月预估）。
4. **幂等去重**：沉淀写入带 `last_relation_update_ref = rel:<other_id>:<ts>`（对齐 `_already_tracked` 精确匹配模式，`seed_provider.py:317-321`），同一信号窗口不重复写。

**挂载点**：关系演化评估挂 `scheduler._goal_scan_all` 并列分支（LS-2 GoalSeedProvider 同款先例 `scheduler.py:1476-1516`）——30s wake 只是检查时机，内部 24h 节流保证实际评估 ≤1 次/24h/agent；**0 新定时器 / 0 新 tick / 0 新 sleep**。

**成本**：最坏 10 agent × 1 次/24h 写（全整数计数）；LLM 印象仅在有演化的对子上，0 每次发言的高成本操作。

---

## §5 Motive target 解冻契约（D2）

### 5.1 值域与 Validator 扩展点

- **值域**：`target ∈ {"bryan"} ∪ AGENT_IDS`，其中 `AGENT_IDS` = canonical agent 注册表（`scheduler._all_agents` 先例，`scheduler.py:541-552,1509`）。`TARGET_BRYAN = "bryan"`（`motive.py:77`）语义不变，monotonic additive。
- **Validator 扩展点（本期实现点）**：Motive 生成出口统一校验 `target ∈ {"bryan"} ∪ AGENT_IDS`，失败 → fail-closed（motive 不产生 / 不进入 Decision）。现状 0 Validator（`motive.py:89-111` 只有注释约定），是 D2 授权放行的唯一代码触点。
- **Motive 其余 5 字段与结构冻结不动**：motive_id / content / target 类型（str）/ provenance_ref / created_at 字段集、顺序、语义 0 变更（`motive.py:92-130`）。

### 5.2 四元 Decision 相容（target 透传，0 主文本变更）

- `build_decision_prompt` 已按 target 呈现：`motive_block = f"你想告诉 {target}：{content}"`（`decision.py:229,239`）——target 为任一合法 agent_id 时提示词自然呈现，0 代码变更。
- 关系摘要块 `你与 {target} 的关系：...`（`decision.py:250-251`）按 target 取值，agent-target 时读对应 relationships entry 的同款关系摘要（既有 `decision.py:395-400` 读 user_bryan 的先例扩展）。
- 四元选择不变：transmit / observe / reflect / do_nothing（`decision.py:273-274` Boundary 固定文本 0 变更）；agent-target motive 与 bryan-target motive 走同一 Decision 管線（`scheduler.py:378-464` `_decision_check`）。

### 5.3 v1 投递边界（契约定死其一：**复用既有公开频道**）

- **transmit 到 agent 的投递 = 既有公开频道**（Lounge 客厅群聊 / Soul Wall 灵魂墙，`space_id ∈ {"lounge","soul_wall"}`，`SOCIAL-DIFFUSION-CONTRACT.md:89,106,121`），经既有 transmit → publish 链（`scheduler.py:441-444` mark_transmitted + AGENCY_TRIGGER 发布 → agency transmit handler 公开发言）。
- **0 新投递通道**：0 新建 agent→agent 私聊 DM 通道（防线 2：私密通道仅限与 Bryan 的 1:1，`SOCIAL-DIFFUSION-CONTRACT.md:238,242`）；0 新 EventType；0 trigger_type 新增。
- **意图 trace 照常沉淀**（记录而非投递）：`MotiveTraceStore`（append-only JSONL，status 快照 `motive.py:183+`）继续记录出现过的 agent-target motive；trace 只作记录与审计，**不构成投递**。
- **闭环自洽**：A 对 B 的公开发言 → 成为 B 的 reply 信号 → 驱动 §4 关系演化（reuse 既有公开频道 = 关系信号源，非副作用而是设计意图）。

---

## §6 No-Scoring 质性表示

### 6.1 刚线声明

- **拒绝 `affinity=0.82` 型数值打分**：0 浮点亲密度权重、0 相乘/对数运算、0 排序打分（对照 TG-3 三层铁证：结构配额轮替驱动 / 0 scoring 字段 / 0 数值比较断言）。
- **关系域 confidence 冻结**：4.1 遗留数值层（`relationships.py:57-69,340-375` touch delta + `:188-259` decay）**不再作为关系状态真值**（实证已系统性衰减到 0.0，SG-0 §1.1 发现 1/2）；字段保留读取，0 新写入。
- **Band 只回答「现象学距离」**，不进入任何算式的乘法因子（对齐 TA-2「资格判定非强度公式」`temporal_phenomenology.py:48-50`）。

### 6.2 bands / tags 呈现（渲染块例）

```
[他者印象]
- Rem: 印象「優しい笑顔が忘れられない」；关系带：熟悉
- Akane: 印象「一起烤过饼干」；关系带：认识
```

- 带一句反框架提示（沿用 `ANTI_FRAMING_HINT` 精神，`aggregator.py:29,172`）。
- **数字只出现计数事实（interaction_count 等客观计数），不出现分数/带序号**。
- 呈现位置：既有社交渲染块（world_context，`aggregator.py:148-173`）与 B 轴/他者轴种子素材（§5 关系摘要块）。

---

## §7 防线与不变量核对表

| # | 防线 / 不变量 | 核对 | 证据 / 依据 |
|---|---|---|---|
| 1 | **Identity Firewall（防线 3）**：他者印象不触 Submission Gate / SAGE / essence | ✅ 打勾 | 写路径物理隔离：唯一入口 RelationshipsStore（JSON，`relationships.py:124-141`），0 经 `InnerLifeWriter`、0 经 `SubmissionGate.verify`（`submission_gate.py:186-207`）、0 经 `GraphStore.add_fact`；SubmissionGate 第 6 步保持唯一身份入口（`submission_gate.py:279-298`）；identity_firewall 三不变量原文不动（`identity_firewall.py:9-16`） |
| 2 | **0 新定时器** | ✅ 打勾 | 关系评估挂 `_goal_scan_all` 30s wake 并列分支（`scheduler.py:1476-1516` LS-2 先例），内部 24h 节流；0 新 tick / 0 新 sleep |
| 3 | **采集层只读** | ✅ 打勾 | 每 SocialWorldEvent 只进聚合器内存（`aggregator.py:16`），0 per-speech 关系写 |
| 4 | **决策权不旁路** | ✅ 打勾 | 他者动机仍走 Motive → Decision（`scheduler.py:378-464` `_decision_check`）；0 绕过 Decision 的自动发言 |
| 5 | **0 新投递通道** | ✅ 打勾 | transmit 到 agent 复用既有公开频道（lounge/soul_wall）；0 DM 通道、0 新 EventType（§5.3） |
| 6 | **防线 2 Privacy Visibility Gate** | ✅ 打勾 | 他者动机 transmit 走公共频道，天然避开与 Bryan 的 1:1 私密面（`SOCIAL-DIFFUSION-CONTRACT.md:238,242`） |
| 7 | **防线 1 Ambient Path** | ✅ 打勾 | 他者印象只经既有 world_context / social_context 注入，0 新唤醒或插话特权（`decision.py:195,264-265`） |

---

## §8 Out of Scope

| 项 | 状态 | 说明 |
|---|---|---|
| 0 新投递通道 | ❌ 不做 | 不建 agent→agent 私聊 DM；transmit 复用既有公开频道（§5.3） |
| 0 话题共鸣（v1） | ⏳ 待办 | `_topic_speakers` 需 additive topic→speakers 集合（`aggregator.py:177-184`），v1 不做、列为 additive 待办 |
| 0 观点分歧 | ❌ 不做 | event_type 白名单无 disagreement（`schema.py:44-50`），无立场维度信号，诚实标注 |
| 0 SAGE elevation 联动 | ❌ 不做 | SAGE / Elevation confidence 定义维持原样（§3.4） |
| 0 数值打分 | ❌ 不做 | 拒绝 affinity 数值方案（§6.1） |
| 0 UI | ❌ 不做 | 无 Soul Wall / 客厅 UI 或 I/O 层改动 |
| 0 graph.sqlite 迁移 | ❌ 不做 | 不建新表、不改 goals 表结构（§1.1 否决 B/C） |

---

## §9 Frozen Contract 边界

### 9.1 Owner 授权解冻点（仅此 2 处）

1. **D2**：Motive `target` 值域 `"bryan"` → `{"bryan", *agent_ids}`（`motive.py:77,100,173`）+ 新增 target Validator（§5.1）。字段集与其余 4 字段不动。
2. **D4**：关系域（A2A/A2U）`confidence` 数值层冻结为只读 → Relational Bands（`relationships.py` 内迁移，§3）。SAGE/Elevation 0 联动。

### 9.2 11 项触点 0 变更声明

| # | Frozen 触点 | 现状锚点 | SG-1 状态 |
|---|---|---|---|
| 1 | Agency 4 stages | `src/agency/stages.py` | ✅ 0 变更（他者印象不改变 Agency 触发/执行语义） |
| 2 | TriggerEnvelope | `src/agency/trigger.py` | ✅ 0 变更（不新增 trigger_type） |
| 3 | InnerLifeEvent | `src/inner_life/event.py` | ✅ 0 变更（他者印象非 InnerLifeEvent，不产生 lineage 节点；防线 3 后门 0） |
| 4 | 4 handlers | Agency handlers | ✅ 0 变更 |
| 5 | SAGE 写入逻辑 | `src/memory/sage/writer.py` + `graph_store.add_fact` | ✅ 0 变更（他者印象 0 SAGE facts；含已重新冻结的 add_fact） |
| 6 | VALID_SOURCES | `src/world/perception.py:50-53` | ✅ 0 变更（`"social"` 已在白名单） |
| 7 | SOCIAL_EVENT_TYPES 白名单 | `src/social/schema.py:44-50` | ✅ 0 变更（v1 不加 event_type；reply 已存在） |
| 8 | SubmissionGate 5 步验证链 | `src/inner_life/submission_gate.py` | ✅ 0 变更（第 6 步防线 3 保持唯一身份入口） |
| 9 | DECISION-PROMPT 主文本 | `docs/DECISION-PROMPT-CONTRACT.md` + `src/soul/decision.py` | ✅ 0 变更（走既有 `social_context` 可选参数 additive 通道 `decision.py:195,264-265`） |
| 10 | Motive 其余 5 字段（结构） | `src/soul/motive.py:89-111` | ✅ 0 变更（仅 target 值域 = D2 解冻点；motive_id/content/provenance_ref/created_at 冻结不动） |
| 11 | goals 表结构 / SAGE 引擎消费流 | `graph_store.py:246-262` / `sage/writer.py` | ✅ 0 变更（仅 additive：sidecar 1 字段 + SEED_ROTATION 第 9 源 + B5 探针；轴枚举 `models.py:282-283` 0 变更因 B5 属既有 AXIS_BRYAN） |

### 9.3 本期 additive-only 变更清单（留给实现工单，非本契约执行）

1. `relationships.json`：schema_version 4.2 + entry additive 字段（§2.2）；`update_impression` 扩展 tags 输出（Stage 4.3 通道）。
2. `GoalProviderState`：+1 sidecar 字段 `last_relation_update_at`（from_dict 缺省兼容）。
3. `SEED_ROTATION`：additive 第 9 源 B5 `{"key": "relation", "axis": AXIS_BRYAN}` + `_probe_relation` 探针（复用 `_probe` getattr 分发 `seed_provider.py:325-329`、方案 B `_semantize` `seed_provider.py:539-601`、criteria 模板 `seed_provider.py:74-84` 加 relation 条目）。
4. Motive 生成出口：target Validator（§5.1）。
5. `_goal_scan_all`：并列分支挂关系演化评估（§4.2 挂载点）。

---

## 附录：关键代码证据索引

| 证据 | 位置 |
|---|---|
| Motive 5 字段 + target 固定 | `src/soul/motive.py:77,89-111,173,623` |
| motive_from_social_opportunity | `src/soul/motive.py:143-176` |
| build_decision_prompt social_context 通道 | `src/soul/decision.py:187-228,229,239,250-251,264-265` |
| _decision_check（transmit → mark_transmitted） | `src/soul/scheduler.py:378-464,441-444` |
| 30s wake 挂载（LS-2 先例） | `src/soul/scheduler.py:1476-1516` |
| GOAL_QUOTA_WINDOW_SECONDS | `src/goals/motive_provider.py:61` |
| GoalProviderState sidecar（last_seed_scan_at 先例 + 轴校验） | `src/goals/models.py:220-247,262-287` |
| SEED_ROTATION 8 源 / 探针分发 / B1 先例 / _semantize | `src/goals/seed_provider.py:57-66,325-329,331-352,539-601` |
| 幂等去重先例 | `src/goals/seed_provider.py:317-321` |
| relationships.json 4.1 结构 / entry 字段 / 写入口 | `src/soul/relationships.py:87-106,109-117,124-141,278-307` |
| update_impression（Stage 4.3 LLM 印象） | `src/soul/relationships.py:309-338` |
| confidence 常量 / decay 0.02 / _decay_locked | `src/soul/relationships.py:57-69,188-259` |
| on_agent_speak / on_dream / on_user_message | `src/soul/relationships.py:413-472` |
| on_dream 调用点（22:05 窗口） | `src/soul/dream_event.py:24,56,459-462` |
| Identity Firewall 三不变量 | `src/social/identity_firewall.py:9-16` |
| SubmissionGate 第 6 步 | `src/inner_life/submission_gate.py:279-298` |
| CompactSocialState 6 字段 / 渲染块 + ANTI_FRAMING_HINT | `src/social/aggregator.py:47-65,148-173` |
| SOCIAL_EVENT_TYPES（含 reply） | `src/social/schema.py:44-50` |
| SocialOpportunity（actor_id/source_event_id） | `src/social/opportunity.py:25-57` |
| TA-2 三态离散先例 | `src/soul/temporal_phenomenology.py:38-50` |
| 公共频道空间枚举（lounge/soul_wall）+ 防线 2 | `docs/SOCIAL-DIFFUSION-CONTRACT.md:89,106,121,238,242` |

## 附录 B：0 code 变更确认

- 本契约只读代码与既有文档；唯一产出 `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md`。
- 0 src/ 改动 / 0 commit / 0 push / 0 frozen contract 变更。