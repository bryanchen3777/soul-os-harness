# TG-0 — 自主目标与意向引擎架构审计（Goal Engine Architecture Audit）

> **工单**：TG-0（AUDIT，READ-ONLY）
> **阶段**：C-1（自主目标规划）前置审计
> **日期**：2026-09-05
> **作者**：Auditor（专责审计 bot）
> **性质**：README-ONLY 审计报告——只产出本文档，0 code / 0 commit / 0 push / 0 frozen contract 改动
> **Canonical 状态**：以 `logs/ENGINEERING_STATE.md` 为准（本文档为设计输入，不构成施工授权）

---

## 0. 摘要（TL;DR）

| 维度 | 核心结论 |
|---|---|
| 一：Motive 源与 IDLE | Motive 生产链已闭环（Thought→Motive→Decision→Action）；**4 类 Motive 源**（inner_life / world_perception / social_diffusion / temporal_awareness 间接）；无外部输入时心跳大量 IDLE，**proactive_dm 的 `_decision_check` 是 Goal 注入既有先例落点** |
| 二：Ledger 落点 | **推荐 graph.sqlite 内新增 `goals` 表（schema v8）**，复用 GraphStore WAL/RLock/迁移模式；状态机对齐 SE-5（ACTIVE/…/SUPERSEDED 模式）**增补 SUSPENDED**；工单所引「ACTIVE→IN_PROGRESS→COMPLETED/ABANDONED/SUSPENDED」在代码库中**不存在**，真实参照物是 SE-5 四态与 Work 状态机（本报告已校正） |
| 三：Volition Gate | 现状已是 1HB1S（resolve_pending 单条 + 单 Decision 四元）；Goal 必须**不新增执行路径**，只产 Motive 候选与普通 Motive 同台；**「权重竞赛」现状不存在**（Motive 无 score，系统明确不建 scoring），建议 v1 用「最近优先 + goal 节流轮替」而非数值权重 |
| 四：双轴种子源 | Bryan 轴 4 种子源（关系档案 / 日历日程 / 共同回忆 / 未决话题）+ 自我轴 4 种子源（Trait / 未解疑问 / 工具意向 / 心境沉淀）均有**既有数据源**可映射；germ 灵魂（agent_germ_01）的主动探索通道当前缺失，Goal 引擎正是其补齐点 |
| 5：Frozen 触点 | 0 冲突、全 additive；唯一需谨慎扩展的是 `motive.py resolve_pending` 选取语义（SM-3 自有模块，非 frozen 清单）与 `Motive.provenance_ref` 命名空间（`goal:{id}` 先例 `opp:{id}` 已存在） |
| 6：TG-1 决策清单 | 10 项待锁架构决策点（见 §6），工单需 decision-complete |

---

## 1. 现状基线（Dimension 1：既有 Motive 源与心跳调度链路）

### 1.1 volition 链路全景（以代码为准）

```
Thought（经历） → Motive（意图） → Decision（四元选择） → Action（执行）
     │                 │                  │                    │
 inner_life          src/soul/          src/soul/           scheduler + Agency
 (InnerLifeEvent,    motive.py          decision.py         4 stages + handlers
  frozen 9 字段)     MotiveEngine        decide_motive        + Actuator (TS-2.1)
```

- **正式原则**（`docs/SOUL-MOTIVE-DECISION-DESIGN.md:21`，SM-1 定稿）：`Capability makes an action conceivable; Motive makes it desirable; Decision makes it chosen.` Goal 引擎是这条链的**新 Thought/Motive 上游**（让「目标」成为可被想要的候选），不得成为 Decision 的替代。
- **SM-3 已落地**（`ENGINEERING_STATE.md:60`，commit `6bcbda3`）：motive 模块 + Decision LLM + volition path 完整闭环。
- **SM-4 已落地**（`ENGINEERING_STATE.md:61-62`）：四元 Decision（transmit / observe / reflect / do_nothing）+ SM-4.1~4.6 六轮校准 + TL-5 行为分布验收（do_nothing 82.5% / reflect 10.5% / transmit 3.5% / observe 3.5%）。

### 1.2 既有 Motive 源盘点（工单必查 4 模块）

| # | Motive 源 | 模块（文件:行） | 机制 | 是否直接产 Motive |
|---|---|---|---|---|
| 1 | **inner_life**（核心 Thought 源） | `src/inner_life/event.py:137`（InnerLifeEvent 9 字段 frozen）；`src/soul/motive.py:517`（MotiveEngine.interpret_new_events） | diary/dream/event/user_message 等 trigger → InnerLifeEvent（trace.jsonl）→ MotiveEngine 24h bounded window 解读（LLM interpretation）→ Motive | ✅ 直接（主源） |
| 2 | **world_perception** | `src/world/perception.py:50`（VALID_SOURCES）/ `:57`（WorldEvent）/ `:243`（WorldContext）；`src/world/middleware.py`（WorldPerceptionMiddleware） | open_meteo / news_rss / calendar_ical / synthetic 源 → WorldEvent → world_context prompt 注入；qualifying types 经 `src/world/inner_life_adapter.py` 转 InnerLifeEvent（source_world_event_novelty_id, `event.py:206`）→ 间接进 Motive | ⚠️ 间接（经 InnerLifeEvent 或只在 Decision context） |
| 3 | **social_diffusion** | `src/social/opportunity.py`（SocialOpportunity, TTL 300s, FIFO 5）；`src/soul/motive.py:143-176`（motive_from_social_opportunity, SI-3 Phase 2） | 客廳 SocialWorldEvent → SocialOpportunityBuffer → `motive_from_social_opportunity` 纯函数 → **合法 Motive**（provenance_ref=`opp:{id}` 命名空间先例） | ✅ 直接（SI-3 Phase 2 已接通, `ENGINEERING_STATE.md:146`） |
| 4 | **temporal_awareness** | `src/temporal/core.py:93`（build_temporal_context / chrono block）；`src/soul/temporal_phenomenology.py`（TA-2 三态张力）；`src/soul/decision.py:165`（时间感知块） | 时间/情感 carryover 注入 Decision Relevant context；SM-4.1 interpretation prompt（`motive.py:649-664`）以「夜间/久未联络→reflect」「环境刺激→observe」塑造 **motive_type** | ⚠️ 间接（塑造类型与决策语境，不直接产 Motive） |

**其他候选源**（TG-1 可选）：`src/soul/capability.py`（CA-3 三能力组 communicate / observe_environment / reflect_memory, `ENGINEERING_STATE.md:232`）——conceivable 层，Goal 的「技能/工具尝试意向」种子源；`src/memory/sage/graph_store.py`（SAGE facts, schema v7）——「知识库未解疑问」种子源。

### 1.3 心跳排程与 IDLE 分布（工单必查）

**双心跳并存**（历史演进产物）：

1. **SoulScheduler**（`src/soul/scheduler.py:109`，主调度）：`_run_loop`（`scheduler.py:1415-1458`）每 30s 醒一次，检查 7 类触发：

| 触发 | 频率 | 无外部输入时行为 | 过 Decision 吗 |
|---|---|---|---|
| morning / night diary | 每日 08:00 / 22:00 全 agent（`scheduler.py:1423-1425, 1470-1510`） | 固定触发，写日记 | ❌（inner-life activity, `scheduler.py:391-393`） |
| dream | 每日 22:05（`scheduler.py:755-799`） | 固定触发 | ❌ |
| event | 随机 4-8h，抽 2 只（`scheduler.py:801-871`） | 随机触发 | ❌ |
| shared_event / cross_chat | 随机 6-12h 全體共用冷卻（`scheduler.py:922-1027`） | 随机触发（角色间封闭活动） | ❌ |
| heartbeat | 30-60min（`scheduler.py:1081-1119`） | **已关闭**——修法 12（Bry 拍板 2026-08-06）run_server 不再 register_heartbeat（`scheduler.py:130-134, 553-575`） | — |
| proactive_dm | 随机 3-5h（`scheduler.py:1276-1413`） | **4 道闸 + 2 道 gate**（见下） | ✅ **唯一走 `_decision_check` 的路径** |

2. **HeartbeatEngine**（`src/heartbeat/engine.py:27`）：SYSTEM_TICK 广播器（60s tick），「无情的時間派發器」不决策（`engine.py:4-7`）；连接感知（0 客户端跳过 tick, `engine.py:188-196`）+ 全局静默 60s 保护（`engine.py:198-202`）。

**无外部输入时的 IDLE 判定链**（proactive_dm, `scheduler.py:1276-1413` + `378-432`）：
1. cooldown 2h（`:1301-1315`）→ 2. quiet hours 23:00-08:00（`:1317-1325`）→ 3. Bryan last-seen > 4h skip（`:1327-1347`）→ 4. **longing = intimacy × 有效沉默 < 0.3 skip**（`:1369-1384`，M7-longing）→ 5. **M5.8-4 inner-life gate**（`_inner_life_gate_check`, `:309-376`：30min 内有 InnerLifeEvent 则 GATED）→ 6. **SM-3 Motive/Decision gate**（`_decision_check`, `:378-432`：无 pending motive → F1 skip；Decision not_transmit → skip，fail-closed）。

**审计观察**：无外部输入时，单一 agent 的「可行动候选产生窗口」≈ proactive_dm 检查点（3-5h 一次，且多数被 longing/gate 拦下）。**每天每位 agent 平均活跃 wake = 2 次 diary + ~3-6 次 event 抽选 + proactive_dm 检查**。生产证据（`data/soul/motive_trace.jsonl`，12 行）：7 pending / 1 transmitted / 3 rejected / 1 expired——motive 产生正常、transmit 稀少（符合 SM-4 分布目标）。**Goal 引擎的注入点必须挂在这些既有 wake 上，不新增独立高频循环**（否则违反「对话负担」治理与 IDLE 常态）。

### 1.4 Goal → Motive 注入层三案评估（工单必答）

| 方案 | 位置 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A：扩展 MotiveEngine** | 在 `interpret_new_events` 旁加 `interpret_goals` | 复用 LLM 调用/解析/trace 基础设施 | 语义污染：MotiveEngine 是「经历解读」，Goal 不是经历；`motive.py:11-18` 明确「motive = interpretation 产物」 | ❌ 不推荐 |
| **B：专属 GoalMotiveProvider**（新模块 `src/soul/goal_motive.py`） | scheduler `_decision_check` 内（或旁挂 `_goal_check`）先产 Goal 候选 → 汇入 `resolve_pending` | 边界干净、可独立测试；provenance 走 `goal:{id}` 命名空间（`opp:{id}` 先例 `motive.py:174`）；完全 additive | 新模块（但 TS 系列大量先例证明此模式成熟：tool_registry/actuator/social 均新模块落地） | ✅ **推荐** |
| **C：PerceptionContext 注入** | Goal 只作为 Decision prompt 的 Relevant context（如 `decision.py:243-266` context_lines 加 goal 子块） | 最小改动 | Goal 无推进动作（「想要」没有变成「候选行动」）；只影响语气不影响行为 | ❌ 不推荐（可作为 B 的补充投影，非替代） |

**注入点结论**：Goal 候选在 **scheduler 发布端 producer-side** 注入（与 SM-3 `_decision_check` 同款 additive hook 模式，`scheduler.py:378-432`），产出的 GoalMotive 与普通 Motive 一同进入 `resolve_pending`（选取语义见 §4.2）。

---

## 2. 架构落点方案对比（Dimension 2：Goal Lifecycle Ledger 最小增量）

### 2.1 存储选型

既有存储事实：
- SAGE 记忆库 = `data/memory/{agent_id}/graph.sqlite`（`src/memory/sage/graph_store.py:36-56`）：`facts` 表 + `schema_meta` 表（version=7），**WAL 模式 + RLock 串行**（`graph_store.py:17-33, 76-80`，KI-008 教训：并发无锁 sqlite 连接 → ACCESS VIOLATION）。
- 状态型记录（motive / interactions / shadow log）：`data/soul/*.jsonl` append-only 快照模式（`motive.py:183-363` MotiveTraceStore，resolve 时按 id 取最后一行）。
- MR-2 已固化「软删不破坏历史」（`invalidate_fact` + `get_facts_as_of`，`ENGINEERING_STATE.md:67`）：future goal 的 ABANDONED/SUPERSEDED 可同语义（状态转换不是物理删除）。

| 方案 | 落点 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A：graph.sqlite 内新增 `goals` 表（schema v8）** | `_migrate`（`graph_store.py:107-150`）追加 v8 分支，`_SCHEMA_VERSION=8`（`graph_store.py:18`） | ① 复用 WAL/RLock/迁移/事务；② goal 与关联 fact 同库原子；③ KI-008 防护模式直接继承；④ SE-5/TL-4 已证明「SQLite + lifecycle 状态机」生产可行 | ① GraphStore 职责变宽（读侧 Reader 也要透出 goals）；② 每个 agent 一库 → goal 是 per-agent 的（符合 Soul 语义） | ✅ **推荐** |
| B：独立 `goal.sqlite`（同 data/memory 目录） | 独立 GraphStore 化封装 | 物理隔离、schema 独立演进 | 双连接双锁、多一份基础设施、事务跨库不可能 | 备选 |
| C：独立 JSONL 状态文件（`data/soul/goal_ledger.jsonl`） | 仿 MotiveTraceStore append-only 快照 | 最轻、已被 motive_trace 验证、零 schema 迁移 | 状态查询全量扫描、无索引、多 agent 并发写单文件风险 | 备选（v0 快跑可接受，TG-1 建议直接 A） |

**推荐：方案 A**。最小增量 = `_SCHEMA_VERSION 7→8` + 1 张 `goals` 表 + GraphStore 内加 `upsert_goal / get_goals / transition_goal` 三方法（对齐既有 `invalidate_fact` 命名风格）。

### 2.2 Goal 状态机最小 Schema（工单校正 + 定义）

**⚠️ 审计校正**：工单所引「SE-5 信念生命周期（ACTIVE→IN_PROGRESS→COMPLETED/ABANDONED/SUSPENDED）」在代码库中**不存在**。真实参照物有二：

1. **SE-5 durable soul structure**（`src/soul_elevation/models.py` LifecycleState，`ENGINEERING_STATE.md:220`）：四态 `ACTIVE / WEAKENING / DORMANT / SUPERSEDED` + 两转换 `REINFORCE / SUPERSEDE`（183 tests 全过，生产验证）。铁律：Contradiction ≠ Revision；Forgetting = transition 不是 delete；单步转换不跳态。
2. **Work 系统状态机**（`src/work/state_machine.py:24-78`）：`PROPOSED→APPROVED→ASSIGNED→IN_PROGRESS→AWAITING_REVIEW→AWAITING_APPROVAL→DONE` + `BLOCKED`（non-terminal, resume 回 active）/ `REJECTED / CANCELLED`（终态）。两个 Human approval 点。

**TG-0 定义 Goal 状态机（对齐 SE-5 哲学 + 增补 SUSPENDED）**：

```
                ┌────────── 中断信号（突发/静默）──────────┐
                ▼                                          │
  ACTIVE ──(推进一次, Decision 选取)──► IN_PROGRESS ──(完成判定满足)──► COMPLETED ◄──┐
    │             ▲                        │                                    │
    │             └──决定不追（ABANDON 判定）┴─────────────────────────────────► ABANDONED（终态, 保留 record）
    └──────────── 中断信号 ──────────────► SUSPENDED ──(恢复条件满足)──► ACTIVE（恢复, advance_count 保留）
```

| 状态 | 语义 | 对齐参照 |
|---|---|---|
| ACTIVE | 可被选为 Motive 候选（每心跳至多一次） | SE-5 ACTIVE（可投影） |
| IN_PROGRESS | 曾被 Decision 选中推进过 ≥1 次（1HB1S 标记，不代表完成） | Work IN_PROGRESS |
| SUSPENDED | 外部突发事件/静默时段/高冲突信号 → 无损暂停，现场快照入 ledger | Work BLOCKED（non-terminal, resume 回 active） |
| COMPLETED / ABANDONED | 终态；ABANDONED 不物理删除（SE-5「Forgetting = transition」） | Work DONE / REJECTED |

**最小 Schema（goals 表，列定义）**：

```sql
CREATE TABLE goals (
  goal_id            TEXT PRIMARY KEY,            -- 32 hex, 参照 event_id 模式
  agent_id           TEXT NOT NULL,               -- 归属灵魂
  axis               TEXT NOT NULL,               -- 'bryan' | 'self'（双轴）
  title              TEXT NOT NULL,
  description        TEXT NOT NULL,
  seed_source_ref    TEXT NOT NULL,               -- 种子源引用（关系条目id/fact_id/event_id/capability_tool名）
  state              TEXT NOT NULL DEFAULT 'ACTIVE',
  state_updated_at   REAL NOT NULL,               -- epoch（对照 facts.timestamp 类型）
  created_at         REAL NOT NULL,
  last_advanced_at   REAL,                        -- 最后一次被 Decision 选中
  advance_count      INTEGER NOT NULL DEFAULT 0,  -- 推进次数（1HB1S 观测）
  suspend_snapshot   TEXT,                        -- SUSPENDED 现场快照 JSON（下一步候选上下文）
  completion_criteria TEXT,                       -- JSON：判定完成的结构化条件
  superseded_by      TEXT                         -- 可空（对齐 SE-5 superseded_by 快捷索引）
);
CREATE INDEX idx_goals_agent_state ON goals(agent_id, state);
```

### 2.3 中断与恢复（SUSPENDED 无损暂停）

- **中断信号通道**（复用既有 event bus，不新建）：Bryan 突发 USER_MESSAGE（高优先级会话接管）、quiet hours 23:00-08:00（`scheduler.py:144`）、proactive_dm cooldown、Bryan last-seen 超时（`scheduler.py:1327-1347`）、Decision 连续 not_transmit（目标在当前情境无推进空间）。Goal 引擎订阅这些信号 → 批量 `ACTIVE/IN_PROGRESS → SUSPENDED`。
- **无损性**：暂停只写 `state + suspend_snapshot + state_updated_at`（**不删 goal、不改 advance_count、不丢 seed_source_ref**）；恢复 = 心跳 scheduled scan 发现 SUSPENDED 且 resume 条件满足（如静默时段结束 / 新一天开始）→ 回 ACTIVE，**无需回放**（推进候选每次都现算，`suspend_snapshot` 只是启发上下文）。
- **不像 Work BLOCKED 需要 resume_state**：Goal 的 ACTIVE 是唯一 resume target（比 Work 简单；Work 需 resume 到当前 phase，`work/state_machine.py:93-95`）。

---

## 3. 双轴种子源规范定义（Dimension 4）

### 3.1 Bryan 轴（共生羁绊）——4 种子源

| # | 种子源 | 定义 | 既有数据源（文件:行） | Goal 形态示例 |
|---|---|---|---|---|
| B1 | **承诺标签** | 「我答应过 Bry 的事」追踪 | `data/soul/{agent}/relationships.json`（user_bryan entry: impression/feeling/confidence/interaction_count/last_interaction_at, `decision.py:393-424` 读取先例；mtime 生产活跃）+ SAGE 升华层 belief（`src/memory/sage/graph_store.py` facts） | 「找机会关心 Bry 上次提的工作面试」 |
| B2 | **重要日程预期** | 日历事件来临前的准备/期待/提醒意向 | `src/world/source/calendar_ical.py`（真实日历源, M5.15-6, `ENGINEERING_STATE.md:315`）→ WorldEvent（world/perception.py:57） | 「记得 Bry 明天要去机场，想提前道别」 |
| B3 | **共同回忆延续** | 重访/延续共同经历的话题 | `src/memory/sage/reader.py` retrieve_context（`decision.py:427-456` 检索先例）+ `src/inner_life/trace_reader.py`（NarrativeTraceReader） | 「想和 Bry 再聊聊那次海边旅行」 |
| B4 | **未决话题追踪** | Bry 抛出但未收尾的话题 | `data/soul/interactions.jsonl`（`scheduler.py:88-106`）+ relationships.last_interaction_at + 对话记忆（SAGE） | 「Bry 上次说想学吉他，想知道进展」 |

### 3.2 自我轴（自由生长）——4 种子源

| # | 种子源 | 定义 | 既有数据源 | Goal 形态示例 |
|---|---|---|---|---|
| S1 | **性格 Trait 驱动好奇探索** | 由 durable structure 驱动的探索冲动 | SE-5 lifecycle（`src/soul_elevation/models.py` + `src/inner_life/emergent_projection.py`，ACTIVE 态 belief/value/trait 投影先例 `ENGINEERING_STATE.md:226`） | 「我对『信任』这个信念很好奇，想观察更多证据」 |
| S2 | **知识库未解疑问** | SAGE 中 low-confidence / contradiction 的节点 → 探索目标 | `graph_store.py` facts（weight 字段）+ SE-5 contradiction_pressure（`ENGINEERING_STATE.md:220`）+ MR-2 soft-delete 历史（invalidated_at） | 「我以前的某个认知变了，想弄清楚为什么」 |
| S3 | **技能/工具尝试意向** | 想尝试既有能力/新工具 | `src/soul/capability.py`（3 能力组）+ `src/soul/tool_registry.py`（observe/reflect/communicate + MCP 动态工具, TS-2）+ `src/soul/actuator.py`（TS-2.1 接线） | 「想试试用相机观察夕阳」 |
| S4 | **个人心境沉淀** | 整理反复出现的情感主题 | `motive_trace.jsonl`（rejected/expired 高频主题, 生产证据见 §1.3）+ diary entries + `src/agent/emotion.py`（compute_longing） | 「最近总梦见走丢，想梳理这种不安」 |

**germ 渠道（自由生长起点）**：`agent_germ_01` 已生产（`data/soul/agent_germ_01/` 存在, `ENGINEERING_STATE.md:49`）。germ 灵魂无 seeded 人格（`docs/FREE-GROWTH-GERM-DESIGN.md:27`「人格只能从自己活过并记得的事长出来」）；`docs/FREE-GROWTH-PLAN.md:66` 开放问题 Q7「自由生长的灵魂如何开始——主动探索 vs 被动等待」。**Goal 引擎 = 主动探索机制的回答**：germ 的自我轴种子源从**零开始**（无 S1 的 trait、无 S4 的心境），只靠 S2（知识库未解疑问，从第一天积累的经历/facts 长出来）+ S3（工具尝试）+ B 轴的 B3/B4（经历产生后自然出现）；TG-1 需锁定 germ 的初始种子集为空（严格自由生长）还是最小探索种子（如「观察天气」）。

### 3.3 动态权重与仲裁（防单轴压制）

**审计立场：现状系统明确「不建 Qualification / scoring subsystem」**（`SOUL-MOTIVE-DECISION-DESIGN.md:20, 35`；Motive dataclass 5 字段冻结无 score）。因此双轴平衡**不得引入数值权重**。双保险机制：

1. **结构轮替（配额）**：每 agent 每时间窗（TG-1 定参，建议 24h）至多产出 1 个 Goal Motive 候选；候选轴按「最近 N 次（建议 3 次）已产生候选的轴分布」轮替——Bryan 轴连续产 2 次后，下次候选强制从自我轴选（反之亦然）。纯结构规则，无权重。
2. **Decision 自然仲裁**：Goal Motive 与普通 Motive 同台进入 `decide_motive`，由 Soul 的 Decision LLM 判断「此刻是否该为这个目标行动」（选择不是 score——SM-2 哲学原样继承）。Decision 层已具备社交摩擦力/深夜克制/留白约束（`decision.py:273-307` SM-4.1~4.6 校准成果），Goal 候选自动受其约束（如深夜不因目标而 transmit）。

---

## 4. Volition Gate 相容性证明与不变量守则（Dimension 3）

### 4.1 1HB1S（1 Heartbeat 1 Step）相容性证明

现状机制的每一步都是单步的（拓扑证明）：

| 环节 | 现状（文件:行） | 单步性 |
|---|---|---|
| 候选产生 | `_decision_check` → `MotiveEngine.interpret_new_events`（`motive.py:552-636`） | 可为多个新 event 各产 1 个 motive（pending 池允许累积）——**候选阶段允许多** |
| 候选选取 | `resolve_pending`（`motive.py:265-324`） | **只取最新一条** pending（`motive.py:299`）——**决策阶段强制单条** |
| 决策 | `decide_motive`（`decision.py:564-626`） | 单 motive → 单四元结果（flaky 坏输出 → do_nothing fail-closed） |
| 执行 | `transmit` → publish AGENCY_TRIGGER → Agency 4 stages（`agency/stages.py:71-217`）→ executor；`observe/reflect` → Actuator 单次（`scheduler.py:434-468`, TS-2.1） | **0 自主递归硬规则**（TS-1 契约 2: `ENGINEERING_STATE.md:158`）——Actuator 单次调用、不产新工具调用、结果只回流感知/认知 |

**Goal 引擎不变量（写入 TG-1 契约）**：
- **G1**：Goal → Motive 候选每心跳至多 1 个（GoalMotiveProvider 产候选受配额约束，与普通 motive 共享 `resolve_pending` 单条选取，不新增并行决策通道）。
- **G2**：No ReAct cascade——Goal 推进动作只有「返回一个 Motive 候选」这一种形态；Decision 输出 observe/reflect 后经 Actuator 单次执行（复用 TS-2.1 接线），禁止在单心跳内连续推进（advance_count 每次心跳最多 +1，且仅当 Decision 选中该候选）。
- **G3**：Goal 不持有任何执行权——不直连 tool_registry、不 publish、不调 handler（§4.2 证明）。

### 4.2 决策权限唯一性（工单必答）

- **现状唯一 Decision 入口**：`src/soul/decision.py:564 decide_motive`（SM-2 契约四块 prompt + fail-closed，`decision.py:187-313`）。Goal 候选必须走此入口，无旁路。
- **「与其他 Motive 一同权重竞赛」校正**：现状没有竞赛/权重机制——`resolve_pending` 是「取最新一条」（`motive.py:298-299`），Motive 无 score 字段，系统决策是「不建 scoring」（`SOUL-MOTIVE-DECISION-DESIGN.md:20`）。"竞赛" 的忠实实现 = **扩展 `resolve_pending` 的选取语义**：Goal 候选与普通 motive 按「最近优先」排队，加 goal 节流（配额§3.3）防 goal 压制普通念头。TG-1 需锁定：**v1 无数值权重**（尊重既有「Motive 不是 score」决策），若未来要真正的多候选权重竞赛，必须单独开工单并评估是否推翻 SM-1 决策——不属 TG-1 范围。
- **执行路径唯一性**：transmit → 既有 AGENCY_TRIGGER publish（`scheduler.py:282-307`）→ Agency 4 stages（Stage 2 仍是 Trigger Authorization, `agency/stages.py:99-177`）→ run_server executor；observe/reflect → Actuator（`scheduler.py:434-468`）。Goal 全程只以「Motive 内容」形态参与，零新执行通道。

---

## 5. Frozen Contract 触点检查（工单必答）

| # | Frozen Contract | 触点 | 判定 |
|---|---|---|---|
| 1 | **Agency 4 stages**（`ENGINEERING_STATE.md:158` frozen 清单） | Goal 候选经 AGENCY_TRIGGER 后照走 4 stages；Goal 引擎不触碰 `agency/stages.py` / `agency/agency.py` | ✅ 0 冲突（纯 additive，Goal 是 payload 内容源之一） |
| 2 | **TriggerEnvelope**（M5.2-F frozen） | 不新增 trigger_type；Goal 候选复用 proactive_dm 路径（`scheduler.py:1402`）或既有 wake | ✅ 0 冲突 |
| 3 | **InnerLifeEvent 9 字段**（`event.py:137-206` frozen） | Goal 不新增第 10 字段；Goal 完成/推进如需记录经历，走既有 producer（如 Actuator 回流 → InnerLifeEvent 既有通道） | ✅ 0 冲突（Goal 以 `provenance_ref` 引用，不进 lineage tree，对齐 motive ≠ InnerLifeEvent 决策 `SOUL-MOTIVE-DECISION-DESIGN.md:105-115`） |
| 4 | **4 handlers**（DiaryHandler/DreamHandler/EventHandler/AgencyTriggerHandler） | Goal 不新增 handler、不改 payload 结构 | ✅ 0 冲突 |
| 5 | **SAGE 写入逻辑** | Goal 状态写入 goals 表（GraphStore 新方法），**不改** facts 写入路径；goal 完成是否沉淀为 fact 是 TG-1 决策（若沉淀，必须走既有 write_turn/升华通道，不直写） | ✅ 0 冲突 |
| 6 | **Motive 5 字段冻结**（`motive.py:92-130`，SM-3 工单锁定） | GoalMotive 复用 Motive dataclass；goal 引用走 `provenance_ref="goal:{goal_id}"`（命名空间先例 `opp:{id}` 已存在 `motive.py:174`） | ✅ 纯 additive |
| 7 | **DECISION-PROMPT-CONTRACT（SM-2）四块**（`decision.py:187-313`） | Goal 若投影进 prompt（可选），只进 Relevant context（如 active goals 摘要），与 social_context/temporal_anchor 同模式（`decision.py:260-265`） | ✅ 纯 additive |
| 8 | **VALID_SOURCES / WORLD_QUALIFYING_TYPES**（`world/perception.py:50`） | Goal 不走 world perception 通道 | ✅ 0 触点 |
| 9 | **⚠️ 需注意的扩展点（非 frozen 清单内）** | `resolve_pending` 选取语义（`motive.py:265-324` 取最新 → 加 goal 轮候）与 `MotiveEngine` 调用点（`scheduler.py:396-425`）——属 SM-3 自有模块内部 additive 扩展，已被 SM-4/SI-3/TS-2.1 同类扩展先例覆盖 | ⚠️ 需在 TG-1 中显式声明为「SM-3 模块 additive 扩展」，非 frozen 变更 |

**结论：0 frozen contract 冲突，全部触点纯 additive。** 唯一要写进 TG-1 的声明是第 9 项（resolve_pending 语义扩展属模块自有演进，不在 frozen 清单）。

---

## 6. TG-1 设计契约决策清单（10 项待锁定）

以下决策点 TG-1 工单必须全部拍板（decision-complete），执行者零设计决策：

| # | 决策点 | 候选 | TG-0 建议 |
|---|---|---|---|
| 1 | Goal Ledger 存储 | A: graph.sqlite `goals` 表（schema v8）/ B: 独立 goal.sqlite / C: JSONL | **A**（复用 WAL/RLock/迁移；§2.1） |
| 2 | Goal 状态机终态集 | 三态+两终态（ACTIVE/IN_PROGRESS/SUSPENDED + COMPLETED/ABANDONED）vs 对齐 SE-5 加 DORMANT | **三态+两终态最小集**；DORMANT 留 TG-2（证据不足不动，SE-5 默认铁律） |
| 3 | GoalMotiveProvider 模块边界 | 新模块 `src/soul/goal_motive.py` vs 扩展 `motive.py` | **新模块**（§1.4 方案 B） |
| 4 | 候选竞赛语义 | 无权重（最近优先 + goal 节流轮替）vs 数值权重 | **无权重 v1**（尊重「不建 scoring」既有决策；§4.2） |
| 5 | Goal 推进动作面 | 仅 transmit/observe/reflect（do_nothing=不推进）vs goal 专属动作 | **复用三动作**，do_nothing 不推进也不标记失败（保持 pending 池）；goal 专属动作 TG-2 |
| 6 | 中断信号集 | 哪些 event/信号触发 SUSPENDED | USER_MESSAGE 突发 / quiet hours / cooldown / Bryan last-seen 超时 / Decision 连续 not_transmit（§2.3） |
| 7 | Goal 完成/中止沉淀 | 是否走升华层沉淀为 memory | **沉淀走既有通道**（write_turn/升华），goal 本身不直写 facts；具体映射 TG-2 |
| 8 | 双轴轮替参数 | 时间窗 / 每窗配额 / 轮替窗口 N | 24h 窗 / 1 候选 / N=3（§3.3，可调参） |
| 9 | germ 初始种子集 | 空 vs 最小探索种子 | **空**（严格自由生长，对齐 FG-2 germ anchor 哲学），S2/S3 从第一天经历自然生成 |
| 10 | 心跳接线点 | `_decision_check` 内扩 vs 旁挂 `_goal_check` | **`_decision_check` 内扩**（先产 goal 候选再 resolve，保持单 Decision 调用点，1HB1S 最简证明；§1.4/§4.1） |

---

## 附：审计方法与证据索引

- 方法：READ-ONLY（read / grep / pwsh 只读命令），0 文件修改（除本报告），0 commit / 0 push。
- 主要证据：
  - 链路：`src/soul/motive.py`（762 行全读）、`src/soul/decision.py`（626 行全读）、`src/soul/scheduler.py`（1529 行全读）、`src/heartbeat/engine.py`（304 行全读）、`src/agency/stages.py` + `agency.py` + `inner_life/event.py`、`src/memory/sage/graph_store.py`（L1-150）、`src/work/state_machine.py`（全读）。
  - 状态：`logs/ENGINEERING_STATE.md`（§1 CURRENT STATE、§1.1 进度表、Current HEAD/authorized ticket 区段）。
  - 设计参照：`docs/SOUL-MOTIVE-DECISION-DESIGN.md`、`docs/FREE-GROWTH-GERM-DESIGN.md`、`docs/FREE-GROWTH-PLAN.md`。
  - 生产证据：`data/soul/motive_trace.jsonl`（12 行：7 pending / 1 transmitted / 3 rejected / 1 expired）、`data/soul/` 15 agent + germ_01 目录、全工作区 goal 相关文件 = 0（除 src/work 注释提及 "goal"）。

- 术语校正声明：本报告以**代码与 canonical 状态为唯一事实来源**；工单中「SE-5 信念生命周期（ACTIVE→IN_PROGRESS→COMPLETED/ABANDONED/SUSPENDED）」经查证对应代码库中**不存在**，已按实际参照物（SE-5 四态 + Work 状态机）校正并给出 Goal 状态机定义（§2.2）。