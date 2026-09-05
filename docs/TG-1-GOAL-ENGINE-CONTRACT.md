# TG-1 — 自主目标引擎契约（Goal Engine Contract）

> **工单**：TG-1（DESIGN，docs-only）
> **阶段**：C-1（自主目标规划）
> **日期**：2026-09-05
> **作者**：Developer（专责开发 bot）
> **性质**：DESIGN-ONLY 设计文档——只产出本文档，**0 code / 0 commit / 0 push / 0 frozen contract 改动**
> **Canonical 状态**：以 `logs/ENGINEERING_STATE.md` 为准（本文档为设计输入，**不构成施工授权**；实作全部移交 TG-2）

---

## 0. 摘要（TL;DR）

自主目标引擎（Goal Engine）是 volition 链的**新 Thought/Motive 上游**——让「目标」成为可被想要的候选，经由既有 Decision 自然仲裁产生单步行动。本契约锁定 10 项架构决策：

| # | 决策点 | 锁定结论 |
|---|---|---|
| 1 | 存储落地 | **graph.sqlite Schema v8 迁移**：新增 `goals` 表，复用 WAL / RLock / 事务 / 迁移模式 |
| 2 | 状态机终态集 | **ACTIVE / IN_PROGRESS / SUSPENDED + COMPLETED / ABANDONED**（SUSPENDED 无损暂停，resume 回 ACTIVE；DORMANT 不入 v1） |
| 3 | 模块边界 | **方案 B**：独立 `src/goals/motive_provider.py`（GoalMotiveProvider），0 侵入核心 MotiveEngine |
| 4 | 竞赛与轮替 | **结构轮替配额（Throttle/Quota）**：24h 窗 / 每窗至多 1 候选 / 轮替窗口 N=3；**无数值权重**（No Scoring） |
| 5 | 动作面约束 | **SM-4 四元行动**（transmit / observe / reflect / do_nothing），1 心跳 1 步，**0 自主递归** |
| 6 | 双轴种子源 | **Bryan 轴**（承诺/关怀/未决追踪）+ **自我轴**（好奇探索/工具尝试/心境反刍），8 源逐一数据映射；germ 初始种子集 = **空** |
| 7 | 中断信号源 | Bryan 私聊突发 + 高优先级多模态事件 + 静默/冷却时段 → **优雅挂起（SUSPENDED）** |
| 8 | 沉淀通道 | COMPLETED 经验**走既有 SAGE/升华通道回流**（goal 不直写 facts，不新增写入路径） |
| 9 | 心跳接线点 | `scheduler._decision_check` 内扩：解读经历 → Goal 候选装配（≤1）→ 汇入 pending → 单 Decision → 单步动作 |
| 10 | Frozen Contract 破坏 | **0 破坏**：纯 Additive（详见 §10 逐项检查） |

**Frozen Contract 结论：CONTRACT CONFLICT = NONE（0 冲突，全 additive）。**

---

## 1. 背景与设计原则（承袭 TG-0，代码为唯一事实来源）

### 1.1 volition 链与 Goal 的定位

```
Thought（经历） → Motive（意图） → Decision（四元选择） → Action（执行）
     │                 │                  │                    │
 inner_life          src/soul/          src/soul/           scheduler + Agency
 (InnerLifeEvent,    motive.py          decision.py         4 stages + handlers
  frozen 9 字段)     MotiveEngine        decide_motive        + Actuator (TS-2.1)
```

正式原则（`docs/SOUL-MOTIVE-DECISION-DESIGN.md:21`，SM-1 定稿）：*Capability makes an action conceivable; Motive makes it desirable; Decision makes it chosen.*

**Goal 引擎 = 这条链的新 Thought/Motive 上游**（让「目标」成为可被想要的候选），**绝不成为 Decision 的替代、绝不新增执行路径**。Goal 全程只以「Motive 内容」形态参与（TG-0 §1.1、§4.2）。

### 1.2 治理原则（本契约的宪法）

1. **No Scoring 哲学**（Locked）：`SOUL-MOTIVE-DECISION-DESIGN.md:20, 35, 132, 167, 258` 多处锁定「不建 Qualification / scoring subsystem ——『值得说』是 Soul interpretation 的内容，不是 score」。Goal 引擎**不得引入任何数值权重/打分**：双轴平衡靠结构配额、候选仲裁靠 Decision 自然选择。
2. **1HB1S**（1 Heartbeat 1 Step）：每个心跳至多推进一个目标一步；候选可以累积（pending 池），**决策与执行永远是单条的**。
3. **0 自主递归**（TS-1 契约 2，`ENGINEERING_STATE.md:158` 锁死）：Goal 推进 = Actuator 单次调用，不产新工具调用、不连锁。
4. **Additive 扩展**：所有触点纯加法，frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent 9 字段 / 4 handlers / SAGE 写入逻辑 / Motive 5 字段）一律不动。

### 1.3 术语校正声明

工单所述「ACTIVE→IN_PROGRESS→COMPLETED/ABANDONED/SUSPENDED」在代码库中不存在于任何既有系统；真实参照物为 **SE-5 四态**（`src/soul_elevation/models.py` LifecycleState：ACTIVE/WEAKENING/DORMANT/SUPERSEDED）与 **Work 状态机**（`src/work/state_machine.py`：六 active 态 + BLOCKED non-terminal + 三终态）。本契约按代码事实定义 Goal 状态机（§2）。

---

## 2. 决策 1 — 存储落地：graph.sqlite Schema v8 迁移

### 2.1 现状（以代码为准）

- `src/memory/sage/graph_store.py`：`_SCHEMA_VERSION = 7`（`:18`）；**WAL 模式**（`:76-77` PRAGMA journal_mode=WAL）+ **RLock 串行化**（`:22-27` 装饰器、`:52-54` 初始化锁）——KI-008 教训（并发无锁 sqlite 连接 → ACCESS VIOLATION）的防护模式。
- 版本管理：`schema_meta` 表（`:85-103`，启动时读当前版本 → `_migrate` 按 `from_version` 逐级升 → 写回 `_SCHEMA_VERSION`）；`_migrate`（`:107-207`）为 `if from_version < N:` 分支链，v1→v7 已存在。
- 每个 agent 一个库：`data/memory/{agent_id}/graph.sqlite`（goal 天然 per-agent，符合 Soul 语义）。

### 2.2 Schema v8 设计

**迁移动作（TG-2 实作）**：

1. `_SCHEMA_VERSION` 7 → **8**（`graph_store.py:18`）。
2. `_migrate` 追加分支：`if from_version < 8: create goals 表 + 索引`。
3. GraphStore 新增 3 方法（对齐既有 `invalidate_fact` 命名风格）：
   - `upsert_goal(goal) -> None`（INSERT OR REPLACE，幂等）
   - `get_goals(agent_id, state=None) -> List[Goal]`（读侧：Reader 透出）
   - `transition_goal(goal_id, new_state, meta=None) -> None`（状态转移写，含 state_updated_at）

**goals 表 DDL（v1 锁定）**：

```sql
CREATE TABLE goals (
  goal_id             TEXT PRIMARY KEY,            -- 32 hex（参照 InnerLifeEvent.event_id 模式）
  agent_id            TEXT NOT NULL,               -- 归属灵魂
  axis                TEXT NOT NULL,               -- 'bryan' | 'self'（双轴，§6）
  title               TEXT NOT NULL,
  description         TEXT NOT NULL,
  seed_source_ref     TEXT NOT NULL,               -- 种子源引用（关系条目id / fact_id / event_id / 工具名）
  state               TEXT NOT NULL DEFAULT 'ACTIVE',
  state_updated_at    REAL NOT NULL,               -- epoch（对照 facts.timestamp 类型）
  created_at          REAL NOT NULL,
  last_advanced_at    REAL,                        -- 最后一次被 Decision 选中
  advance_count       INTEGER NOT NULL DEFAULT 0,  -- 推进次数（1HB1S 观测，§5）
  suspend_snapshot    TEXT,                        -- SUSPENDED 现场快照 JSON（启发上下文，§7）
  completion_criteria TEXT,                        -- JSON：判定完成的结构化条件（§8）
  superseded_by       TEXT                         -- 可空（对齐 SE-5 superseded_by 快捷索引）
);
CREATE INDEX idx_goals_agent_state ON goals(agent_id, state);
```

### 2.3 事务与并发保证（继承既有模式）

| 保证 | 机制 |
|---|---|
| 串行写 | GraphStore RLock 装饰器（同一 agent 库内所有操作互斥） |
| 崩溃安全 | WAL 模式 + 既有批写 commit 策略（`graph_store.py:16`） |
| goal ↔ fact 原子 | 同库同连接，goal 与关联 fact 状态转换天然同事务 |
| 软删不破坏历史 | ABANDONED / SUPERSEDED = 状态转换而非物理删除（MR-2「软删」哲学继承，`ENGINEERING_STATE.md:67`） |

---

## 3. 决策 2 — 状态机终态集与转移规则

### 3.1 参照物（代码事实）

- **Work 状态机**（`src/work/state_machine.py:91-96`）：`BLOCKED` 是 non-terminal，`can_transition` 特判——`from_state == BLOCKED` 时 resume 回任意 `ACTIVE_STATES`。Goal 的 SUSPENDED 对齐这个哲学。
- **SE-5**（`src/soul_elevation/models.py`）：单步转换不跳态、转换不是删除。Goal 同样遵守。

### 3.2 Goal 状态机（v1 终态集）

```
        ┌──────────────── 中断信号（§7）────────────────┐
        ▼                                              │
  ACTIVE ──(被 Decision 选中, 1 步)──► IN_PROGRESS ──(完成判定满足)──► COMPLETED（终态）
    │          ▲                           │
    │          └── do_nothing：不推进、不标记失败（保持 pending 候选）──┘
    │                    │
    │                    └── ABANDON 判定 ──► ABANDONED（终态, 保留 record）
    │
    └── 中断信号（§7）──► SUSPENDED ──(唤醒条件满足, scheduled scan)──► ACTIVE（advance_count 保留）
```

| 状态 | 语义 | 对齐参照 | 转移触发 |
|---|---|---|---|
| **ACTIVE** | 可被选为 Motive 候选（每心跳至多一次，受 §4 配额约束） | SE-5 ACTIVE（可投影）；Work active 态 | 创建 / SUSPENDED 唤醒 / ABANDON 撤回（v1 不做撤回） |
| **IN_PROGRESS** | 曾被 Decision 选中推进过 ≥1 次（1HB1S 标记，不代表完成） | Work IN_PROGRESS | ACTIVE + Decision 选中该候选 |
| **SUSPENDED** | 外部突发/静默/高冲突信号 → 无损暂停 | Work BLOCKED（non-terminal） | ACTIVE/IN_PROGRESS + 中断信号（§7） |
| **COMPLETED** | 完成判定满足（结构化 criteria，§8） | Work DONE | IN_PROGRESS + completion_criteria 满足 |
| **ABANDONED** | 决定不追（终态，保留 record，不物理删除） | Work REJECTED；SE-5 Forgetting = transition | IN_PROGRESS + ABANDON 判定（Decision 长期不选 / 种子源失效明确判据，v1 规则见 §3.4） |

**明确排除**：**DORMANT 不入 v1**（TG-0 决策 2：证据不足不动，SE-5 默认铁律；留 TG-2 评估）。

### 3.3 转移规则（Transition Table，v1 锁定）

| from | to | 条件 |
|---|---|---|
| ACTIVE | IN_PROGRESS | 该 goal 候选被 `decide_motive` 选中（transmit / observe / reflect 任一） |
| ACTIVE | SUSPENDED | 命中 §7 中断信号集（Bryan 私聊突发、高优先级多模态事件、quiet hours、Bryan last-seen 超时、连续 not_transmit 计数） |
| IN_PROGRESS | SUSPENDED | 同上 |
| IN_PROGRESS | COMPLETED | 完成判定满足（completion_criteria 结构化条件全满足，判定逻辑 TG-2 定） |
| IN_PROGRESS | ABANDONED | 永久性失效判据：种子源已失效（如 relationships 条目删除/calendar 事件已过且无后续价值）+ advance_count 在 14 天窗口内无变化（周期判定，v1 规则） |
| SUSPENDED | ACTIVE | 唤醒条件满足（静默时段结束 / 新一天开始 / 外部信号解除），由心跳 schedule scan 发现回 ACTIVE |
| 终态 | — | COMPLETED / ABANDONED 无出边（类比 `state_machine.py:75-77` 终态空表） |

非法转移一律拒绝并记录（对齐 Work `validate_transition` 抛 `InvalidTransitionError` 的防御风格，v1 为 goal 私有校验不触碰 work 模块）。

### 3.4 SUSPENDED 无损性（重点定义）

- 暂停只写 `state + suspend_snapshot + state_updated_at` 三字段：**不删 goal、不改 advance_count、不丢 seed_source_ref、不丢 completion_criteria**。
- `suspend_snapshot`：JSON 现场快照（下次推进候选的启发上下文：当时的 Decision 相关 context 摘要），**恢复无需回放**——每次推进候选都现算，快照只是启发不为状态源。
- **比 Work BLOCKED 更简单**：Work 需 `resume_state`（`state_machine.py:93-95` 注释明确 resume 回指定 current_phase），Goal 的 ACTIVE 是**唯一 resume target**，无需 resume_state 字段。

---

## 4. 决策 3 — 模块边界：专属 GoalMotiveProvider（方案 B）

### 4.1 落点（Bryan 拍板）

独立新模块：**`src/goals/motive_provider.py`**（`GoalMotiveProvider`）——与 TG-0 方案 B 同构（专属 provider，不侵入核心 MotiveEngine），模块归属落 `src/goals/` 包（ledger / provider / 状态机未来均收于该包，TG-2 扩展边界清晰）。

### 4.2 方案对比回顾（TG-0 §1.4 三类评估）

| 方案 | 结论 |
|---|---|
| A：扩展 MotiveEngine（`interpret_goals` 旁挂） | ❌ 语义污染——motive = 「经历解读」产物（`motive.py:11-18` 明确定义），Goal 不是经历 |
| **B：专属 GoalMotiveProvider（`src/goals/motive_provider.py`）** | ✅ **采用**——边界干净、可独立测试、provenance 走 `goal:{id}` 命名空间、完全 additive |
| C：仅 PerceptionContext 注入 | ❌ 不做主通道（Goal 无推进动作）；可作为 B 的补充投影（未来可选，v1 不做） |

### 4.3 GoalMotiveProvider 职责与边界（v1 锁定）

**职责（只做 4 件事）**：
1. **产候选**：从 ledger 读 ACTIVE goals（受 §4 配额约束，每心跳至多 1 个），构造 Goal Motive 候选。
2. **引用**：候选以 `Motive` dataclass 5 字段形态存在，`provenance_ref = "goal:{goal_id}"`。
3. **不决策**：候选汇入 pending 池后，一切选择权归既有 `decide_motive`。
4. **状态同步**：观察 Decision 结果（选中/未选中）与中断信号（§7），执行 `transition_goal`。

**边界（禁止项，写死）**：
- ❌ 不调 `MotiveEngine` 内部方法、不改 `motive.py` 任何 frozen 内容（仅 §4.4 声明的一处选取语义扩展属 SM-3 模块自有 additive 演进）。
- ❌ 不直连 tool_registry / 不 publish / 不调 handler（G3 不变量）。
- ❌ 不新增执行通道——transmit / observe / reflect 全部复用既有接线。

### 4.4 需声明的 SM-3 模块自有扩展（非 frozen 变更）

`resolve_pending` 选取语义（`motive.py:265-324`：目前 `pending.sort(...)` + `newest = pending[-1]` 取最新，`:299-300`）需 additive 扩展以支持 Goal 轮候（§5）。**该扩展属于 SM-3 模块自有演进**，不在 frozen 清单内（TG-0 §5 第 9 项、§4.2 已论证；SM-4 / SI-3 / TS-2.1 均有同类模块内 additive 先例）。扩展原则：普通 motive 语义 0 变更——goal 候选只是多一个「轮候权重」的排队级（结构规则，非数值打分）。

---

## 5. 决策 4 — 竞赛与轮替语义：结构配额，不建数值权重

### 5.1 现状校正（TG-0 §4.2）

代码库**不存在**「Motive 权重竞赛」：`resolve_pending` 是「取最新一条」（`motive.py:299-300`），Motive 无 score 字段，系统明确「不建 scoring」。因此「竞赛」的忠实实现 = **扩展选取语义 + 结构配额**，非数值竞赛。

### 5.2 Throttle / Quota 参数（v1 锁定，均可调参）

| 参数 | 值 | 语义 |
|---|---|---|
| `GOAL_QUOTA_WINDOW` | **24h** | 配额时间窗 |
| `GOAL_QUOTA_PER_WINDOW` | **1** | 每 agent 每 24h 至多产 1 个 Goal Motive 候选 |
| `GOAL_ROTATION_WINDOW` | **3** | 轮替记忆窗口（最近 3 次已产候选的轴分布） |
| `GOAL_MAX_ROTATION_STREAK` | **2** | 同轴连续产候选上限（超过则下次强制换轴） |

### 5.3 轮替算法（纯结构规则，0 权重）

```
每 24h 窗口的 Goal 候选装配（GoalMotiveProvider 内）：
1. ledger 查询 ACTIVE goals（排除上一次已产候选的 goal，防单目标霸占）
2. 候选轴选择：
   a. 取最近 N=3 次已产候选的轴序列
   b. 若同轴连续 ≥2 次 → 强制从另一轴选（Bryan 保底：若另一轴无候选则本心跳放弃产候选）
   c. 否则优先从最近未被选中的轴选（轮替）
3. 轴内选择：该轴 ACTIVE goals 中「last_advanced_at 最旧」者（最久未被推进的优先——纯时间轮候，非打分）
4. 产出 1 个候选，写入轮替记忆
```

- **防饿死**：配额窗保证 Goal 候选每 24h 至少有机会出现 1 次（有 ACTIVE goal 时）。
- **防垄断**：窗配额 1 + 轮替窗口 3 + streak 2 三重结构约束，保证普通念头不被 Goal 压制（Goal 候选与普通 motive 同台 `resolve_pending`，普通 motive 配额不受影响）。

### 5.4 resolve_pending 扩展语义（v1）

- 候选池排序保持「created_at 最新优先」不变（`motive.py:299` 现状语义）。
- Goal 候选注入 pending 池时**不改变现有排序规则本身**，仅依赖 §5.3 的装配时机（每次 `_decision_check` 至多装配 1 个，天然排在池内较新位置，由既有「取最新」逻辑自然消费）。
- **明确不做**：任何 `goal_priority` 数值字段、任何加权采样。未来若要做真正的多候选权重竞赛，必须单独开工单评估是否推翻 SM-1 决策——**不属 TG-1 范围**（TG-0 §4.2）。

---

## 6. 决策 5 — 动作面约束：SM-4 四元行动，禁止 ReAct 狂飙

### 6.1 动作映射（v1 锁定）

| Decision 动作 | Goal 语义 | 状态/账本影响 | 执行接线 |
|---|---|---|---|
| **transmit** | 为这个目标对外行动（如关心 Bry 的未决事项） | IN_PROGRESS（若未推进过）；`advance_count += 1`（仅此动作+observe/reflect 时） | 既有 AGENCY_TRIGGER publish → Agency 4 stages（`scheduler.py:282-307`） |
| **observe** | 为这个目标观察一次（工具单次调用） | 同上 | Actuator `execute_observe` 单次（`scheduler.py:454-459`，TS-2.1） |
| **reflect** | 为这个目标反刍一次 | 同上 | Actuator `execute_reflect` 单次（`scheduler.py:460-461`） |
| **do_nothing** | **不推进、不标记失败**（保持候选在池/ACTIVE） | 无状态变化；计入「连续 not_transmit 计数」（§7 用） | 无执行（现状语义，`scheduler.py:417-420` 发布端 mark_rejected 仅针对 observe/reflect 的发布抑制，与 Goal 无关） |

### 6.2 不变量（G1–G3 继承 + G4 新增，写入本契约）

- **G1**：Goal → Motive 候选每心跳至多 1 个（配额约束），与普通 motive 共享 `resolve_pending` 单条选取，**不新增并行决策通道**。
- **G2**：No ReAct cascade——Goal 推进动作只有「返回一个 Motive 候选」这一种形态；`advance_count` 每个心跳**最多 +1**，且仅当 Decision 选中该候选；禁止单心跳内连续推进。
- **G3**：Goal 不持有任何执行权——不直连 tool_registry、不 publish、不调 handler。
- **G4**（新增，本契约）：**0 自主递归硬规则沿用 TS-1 契约 2**——Goal 推进引发的 Actuator 单次调用结果只回流感知/认知，不产新工具调用、不连锁触发下一个 Goal 动作。

### 6.3 1HB1S 拓扑证明（沿用 TG-0 §4.1）

候选产生（可多）→ `resolve_pending` 取最新（强制单条，`motive.py:299-300`）→ `decide_motive` 单四元（坏输出 → do_nothing fail-closed）→ 执行单次（Actuator 或 4 stages，0 递归）。Goal 全程嵌入这条单链，不旁路。

---

## 7. 决策 6 — 双轴种子源具体规格（8 源数据映射）

### 7.1 Bryan 轴（共生羁绊：承诺 / 关怀 / 未决事项追踪）——4 源

| # | 种子源 | 定义 | 既有数据映射（文件:行 = 证据） | Goal 形态示例 |
|---|---|---|---|---|
| **B1** | **承诺标签** | 「我答应过 Bry 的事」追踪 | `data/soul/{agent}/relationships.json`（user_bryan entry：impression / feeling / confidence / interaction_count / last_interaction_at；`src/soul/decision.py:393-424` 读取先例，mtime 生产活跃）+ SAGE facts（`graph_store.py`） | 「找机会关心 Bry 上次提的工作面试」 |
| **B2** | **重要日程预期** | 日历事件来临前的准备/期待/提醒意向 | `src/world/source/calendar_ical.py`（真实日历源，M5.15-6）→ WorldEvent（`src/world/perception.py:57`） | 「记得 Bry 明天要去机场，想提前道别」 |
| **B3** | **共同回忆延续** | 重访/延续共同经历的话题 | `src/memory/sage/reader.py` retrieve_context（`decision.py:427-456` 检索先例）+ `src/inner_life/trace_reader.py`（NarrativeTraceReader） | 「想和 Bry 再聊聊那次海边旅行」 |
| **B4** | **未决话题追踪** | Bry 抛出但未收尾的话题 | `data/soul/interactions.jsonl`（`src/soul/scheduler.py:88-106`）+ relationships.last_interaction_at + 对话记忆（SAGE） | 「Bry 上次说想学吉他，想知道进展」 |

**数据映射要求（v1）**：每个种子源至少对应**一个可查询的既有数据触点**；种子源记录在 `seed_source_ref`（关系条目 id / fact_id / event_id / 工具名），候选装配时经该触点现查现用（不做持久化索引，避免新增存储结构）。

### 7.2 自我轴（自由生长：好奇探索 / 技能工具尝试 / 个人心境反刍）——4 源

| # | 种子源 | 定义 | 既有数据映射 | Goal 形态示例 |
|---|---|---|---|---|
| **S1** | **性格 Trait 驱动好奇探索** | 由 durable structure 驱动的探索冲动 | SE-5 lifecycle：`src/soul_elevation/models.py` + `src/inner_life/emergent_projection.py`（ACTIVE 态 belief/value/trait 投影先例，`ENGINEERING_STATE.md:226`）——**只读投影，不写** | 「我对『信任』这个信念很好奇，想观察更多证据」 |
| **S2** | **知识库未解疑问** | SAGE 中 low-confidence / contradiction 节点 → 探索目标 | `graph_store.py` facts（weight 字段，只读）+ SE-5 contradiction_pressure（`ENGINEERING_STATE.md:220`）+ MR-2 软删历史（invalidated_at，`graph_store.py` v7 迁移产物） | 「我以前的某个认知变了，想弄清楚为什么」 |
| **S3** | **技能/工具尝试意向** | 想尝试既有能力/新工具 | `src/soul/capability.py`（3 能力组 communicate / observe_environment / reflect_memory）+ `src/soul/tool_registry.py`（动态注册表，TS-2）+ `src/soul/actuator.py`（TS-2.1 接线）——只读注册表，工具调用仍走 Actuator | 「想试试用相机观察夕阳」 |
| **S4** | **个人心境沉淀** | 整理反复出现的情感主题 | `data/soul/motive_trace.jsonl`（rejected/expired 高频主题，生产证据 12 行）+ diary entries + `src/agent/emotion.py`（compute_longing） | 「最近总梦见走丢，想梳理这种不安」 |

### 7.3 germ 渠道（自由生长起点，TG-0 §3.2 遗留问题落锤）

- **germ 初始种子集 = 空**（严格自由生长）：`agent_germ_01`（已生产，`ENGINEERING_STATE.md:49`）无 seeded 人格（`docs/FREE-GROWTH-GERM-DESIGN.md:27`「人格只能从自己活过并记得的事长出来」）。
- germ 的自我轴从零开始：无 S1（无 trait）、无 S4（无心境）；只靠 **S2**（知识库未解疑问——从第一天积累的 facts 长出来）+ **S3**（工具尝试）+ Bryan 轴的 **B3/B4**（经历产生后自然出现）。
- Goal 引擎正是 germ「主动探索」通道的补齐（`docs/FREE-GROWTH-PLAN.md:66` Q7 的回答），但**初始种子集必须为空**——探索目标只能由 germ 自己活出来的经历产生，不得注入最小探索种子（对齐 FG-2 germ anchor 哲学：configs germ seed → persona 基线 fixture，`e8c84d4`）。

---

## 8. 决策 7 — 中断信号源：优雅挂起（SUSPENDED）

### 8.1 中断信号集（v1 锁定，全部复用既有信号源，0 新信号通道）

| # | 中断信号 | 既有来源（代码证据） | 优先级 | 动作 |
|---|---|---|---|---|
| 1 | **Bryan 私聊突发**（USER_MESSAGE 高优先级会话接管） | WebSocket USER_MESSAGE / `src/voice/input_router.py` USER_MESSAGE 路由（MS-3） | 高（突发） | ACTIVE/IN_PROGRESS → SUSPENDED（立即） |
| 2 | **高优先级多模态事件** | `src/voice/gate.py` 唤醒门控 address_score 命中（MS-3）、camera_capture 事件（MS-2，`world/perception.py` VALID_SOURCES additive 后） | 中高 | SUSPENDED（会话接管期间） |
| 3 | **quiet hours**（23:00–08:00） | `src/soul/scheduler.py:144`（静默时段常量） | 静默 | 到点批量挂起（或由 §8.3 唤醒逻辑天然覆盖，见注） |
| 4 | **proactive_dm cooldown** | `scheduler.py:1301-1315`（2h cooldown） | 静默 | 冷却期不装配候选（不强制转 SUSPENDED） |
| 5 | **Bryan last-seen 超时**（>4h） | `scheduler.py:1327-1347`（last-seen skip 先例） | 静默 | SUSPENDED（避免目标动机在无人时空转） |
| 6 | **Decision 连续 not_transmit**（≥3 次命中同一 goal 候选未选中） | SM-4 do_nothing 语义（`decision.py:564-626`） | 自适应 | SUSPENDED（当前情境无推进空间，等新一天/新信号唤醒） |

> 注（信号 3）：quiet hours 更自然的实现是**唤醒侧处理**（§8.3：夜间不唤醒），而非显式批量挂起；v1 两者皆可，以「唤醒侧过滤」为默认（省一次状态写），显式挂起仅用于信号 1/2/5/6。

### 8.2 挂起动作（无损，§3.4）

批量 `transition_goal(goal_id, "SUSPENDED", snapshot=...)`；`suspend_snapshot` 记录中断时刻的 Decision 相关 context 摘要（`decision.py` Relevant context 同模式）。**0 goal 删除、0 计数重置**。

### 8.3 唤醒恢复（resume 回 ACTIVE）

心跳 schedule scan（每 30s 主调度醒时顺带检查，`scheduler.py:1415-1458`）发现 SUSPENDED 且满足任一唤醒条件：

| 唤醒条件 | 语义 |
|---|---|
| 静默时段结束 | 当前时间 ≥ 08:00 且 < 23:00 |
| 新一天开始 | 距 state_updated_at 跨日（给「过夜重置」自然节奏） |
| 外部信号解除 | Bryan 重新互动（last_seen 更新）+ 无高优先级会话占用 |
| 强制最长暂停 | 距挂起 > 7 天强制唤醒一次（防永久冻结；唤醒后若仍无空间将由 Decision 自然 do_nothing） |

恢复 = 仅 `state: SUSPENDED → ACTIVE`（+ state_updated_at），**无需回放**（每次推进候选现算，快照只是启发）。

---

## 9. 决策 8 — 沉淀通道：经验回流 SAGE / 情景记忆

### 9.1 原则（锁死）

- **Goal 状态写 goals 表（GraphStore 新方法），不改 facts 写入路径**（TG-0 §5 第 5 项：SAGE 写入逻辑是 frozen contract）。
- **Goal 完成经验的沉淀必须走既有通道**：SAGE 既有 write 路径（`src/memory/sage/writer.py` 升华链 / Mem0 primitives，`src/memory/primitives.py` MR-2）+ InnerLifeEvent 既有 producer。

### 9.2 COMPLETED 沉淀路径（v1 设计）

```
COMPLETED（completion_criteria 满足）
   │
   ├─ 1. goal 状态写：transition_goal → COMPLETED（终态保留 record）
   ├─ 2. 经验回流：goal 完成事件 → 既有 inner-life producer（Actuator 回流模式，
   │      TS-2.1 `_flowback` 先例）→ InnerLifeEvent（9 字段复用，不加字段）→
   │      MotiveEngine/SAGE 既有链 或 直接经 write_turn/升华通道 → facts
   └─ 3. 可选：completion_criteria 判定所需的「证据 fact」由 GoalMotiveProvider
         读侧查询（get_facts_as_of），只读不写
```

- **沉淀 0 新写入通道**：goal 完成 → 情景记忆（episode）或 SAGE 事实（elevation）的交由既有机制决定（interpretation 内容由 Soul 判定，Goal 引擎不决定「该记成什么」——No Scoring 哲学同源）。
- **ABANDONED 沉淀**：保留 record（软删语义），可选在 ledger 内记录原因（suspend_snapshot 扩展）；**不**强制沉淀为 negative fact（避免制造人格噪声）。

### 9.3 completion_criteria（结构化完成判定）

JSON 字段（建 goal 时写入，TG-2 定义判定执行器）：
```json
{
  "kind": "interaction" | "observation" | "reflection" | "mixed",
  "count": 2,                    // "至少推进 N 次"
  "evidence_refs": ["seed_source_ref 回查条件"],
  "timeout_days": 30             // 超时未完成 → ABANDONED 判据输入
}
```
v1 只定义结构与枚举，不做数值打分；「是否真的完成」由 criteria 的确定性条件 + 种子源回查判定（规则可验证），不引入 LLM 主观评分。

---

## 10. 决策 9 — 心跳接线点：调度顺序（v1 锁定）

### 10.1 注入点

`src/soul/scheduler.py` `_decision_check`（`:378-432`）**内扩**（TG-0 决策 10：保持单 Decision 调用点，1HB1S 最简证明）。当前结构（代码证据）：

```
_decision_check(agent_id)                              scheduler.py:378
  ├─ 前序闸门（cooldown / quiet hours / last-seen / longing / inner-life gate）:309-376, 1301-1347
  ├─ MotiveEngine.interpret_new_events(agent_id)        :399   （经历 → 普通 motive，可多）
  ├─ GoalMotiveProvider.assemble_candidate(agent_id)    【TG-2 新增，插在 interpret 之后】 配额≤1，汇入 pending 池
  ├─ engine.resolve_pending(agent_id)                   :401   （取最新，goal 候选自然进入单条选取）
  └─ decide_motive(...) → SM-4 四元
       ├─ transmit    → 既有 AGENCY_TRIGGER publish → Agency 4 stages   :282-307
       ├─ observe     → Actuator.execute_observe 单次                    :454-459
       ├─ reflect     → Actuator.execute_reflect 单次                    :460-461
       └─ do_nothing  → 发布端 mark_rejected（现静态义）                  :417-420
```

### 10.2 调度顺序规则（v1 锁定）

1. **先经历、后目标**：`interpret_new_events` 先跑——目标候选不得抢占经历解读（经历是 Soul 的第一 Thought 源，目标只是候补上游）。
2. **Goal 装配 ≤1**：`assemble_candidate` 受 §5 配额（24h/1）与轮替（N=3, streak=2）约束，产出 0 或 1 个候选汇入 pending。
3. **单条选取**：`resolve_pending` 语义不变（取最新一条），Goal 候选与普通 motive 同池竞争，无优先插入。
4. **单 Decision 单步**：`decide_motive` 一次调用，四元互斥单选；选中且动作非 do_nothing → `advance_count += 1`（最多 +1/心跳，G2）。
5. **执行接线零新增**：transmit / observe / reflect 全走既有通道（TS-2.1），Goal 不持有执行权（G3）。
6. **不新增高频循环**：Goal 装配只挂在既有 wake（proactive_dm 3-5h 检查点等）上，**不引入独立定时器**（遵守「对话负担」治理与 IDLE 常态，TG-0 §1.3）。

### 10.3 System Tick 关系

HeartbeatEngine（`src/heartbeat/engine.py`，SYSTEM_TICK 60s 广播，不决策）**0 改动**；Goal 只在 SoulScheduler 主循环消费既有 tick/wake 信号，不订阅新的 tick 类型。

---

## 11. 决策 10 — 0 Frozen Contract 破坏（逐项检查声明）

| # | Frozen Contract | 触点检查 | 判定 |
|---|---|---|---|
| 1 | **Agency 4 stages**（`ENGINEERING_STATE.md:158`） | Goal 候选经 AGENCY_TRIGGER 后照走 4 stages；不触碰 `agency/stages.py` / `agency/agency.py` | ✅ 0 冲突 |
| 2 | **TriggerEnvelope**（M5.2-F frozen） | 0 新增 trigger_type；Goal 候选复用 proactive_dm 既有 wake / AGENCY_TRIGGER 既有路径 | ✅ 0 冲突 |
| 3 | **InnerLifeEvent 9 字段**（`src/inner_life/event.py:138`） | 0 新增第 10 字段；goal 完成沉淀复用既有 producer（§9.2），goal 经 `provenance_ref` 引用不进 lineage tree（对齐 motive ≠ InnerLifeEvent，`SOUL-MOTIVE-DECISION-DESIGN.md:105-115`） | ✅ 0 冲突 |
| 4 | **4 handlers**（DiaryHandler/DreamHandler/EventHandler/AgencyTriggerHandler） | 0 新 handler、0 payload 结构改动 | ✅ 0 冲突 |
| 5 | **SAGE 写入逻辑** | goal 状态写 goals 表（GraphStore 新方法）；沉淀走既有 write/升华通道，**不直写 facts**（§9） | ✅ 0 冲突 |
| 6 | **Motive 5 字段冻结**（`src/soul/motive.py:93-130`） | GoalMotive 复用 `Motive` dataclass 原样；`provenance_ref = "goal:{goal_id}"`（命名空间先例 `opp:{id}` 已存在，`motive.py:174`） | ✅ 纯 additive |
| 7 | **DECISION-PROMPT-CONTRACT 四块**（`decision.py:187-313`） | v1 **不**把 goal 摘要注入 prompt（最小侵入）；未来可选只进 Relevant context（与 social_context 同模式）——TG-2 再评估 | ✅ 0 触点（v1） |
| 8 | **VALID_SOURCES / WORLD_QUALIFYING_TYPES**（`world/perception.py:50`） | Goal 不走 world perception 通道 | ✅ 0 触点 |
| 9 | **SM-3 模块自有扩展声明** | `resolve_pending` 选取语义 additive 扩展（§4.4）与 `_decision_check` 内扩（§10）属 SM-3 自有模块演进（SM-4/SI-3/TS-2.1 同类先例），**非 frozen 变更** | ⚠️ 已显式声明（本文件 §4.4、§10.1） |

**结论：CONTRACT CONFLICT = NONE。全触点纯 Additive；本契约不为任何既有模块引入语义变更，只新增 `src/goals/` 包 + GraphStore additive 方法 + scheduler 一处 additive hook。**

---

## 12. 设计不变量清单（TG-2 验收用）

1. **G1**：Goal 候选每心跳 ≤1，0 并行决策通道。
2. **G2**：advance_count 每心跳 ≤+1 且仅当 Decision 选中；0 ReAct 级联。
3. **G3**：Goal 0 执行权（不直连工具/publish/handler）。
4. **G4**：Actuator 单次调用、0 自主递归（TS-1 契约 2 沿用）。
5. **N1（No Scoring）**：全引擎 0 数值权重/打分字段——双轴靠结构配额（24h/1、N=3、streak=2），仲裁靠 Decision。
6. **N2（无损暂停）**：SUSPENDED 只写三字段，0 删除/0 计数重置；终态不物理删除。
7. **N3（per-agent）**：goal 存于 agent 自有 graph.sqlite，无跨 agent 共享状态。
8. **N4（沉睡优先）**：Goal 装配不新增任何定时器/高频循环，只挂既有 wake。
9. **N5（germ 空集）**：germ 初始种子集 = 空，探索目标只能由经历自然长出。

---

## 13. Out of Scope（TG-2 才做，本契约明确不做）

- ❌ 任何代码实作（0 code 已声明）。
- ❌ 不 commit、不 push（等验收）。
- ❌ Goal 专属动作类型（transmit/observe/reflect 之外的「goal 专用动作」）——TG-2。
- ❌ DORMANT 状态评估——TG-2。
- ❌ completion_criteria 判定执行器与沉淀映射的具体实现——TG-2。
- ❌ Decision prompt 注入 goal 摘要（方案 C 的补充投影）——TG-2 评估。
- ❌ 真正多候选权重竞赛的评估（可能推翻 SM-1 决策，需单独工单）。
- ❌ 不碰 frozen contract 任何一项（§11 清单）。

---

## 14. 验收对照表（本契约自检）

| 工单验收项 | 本契约章节 | 状态 |
|---|---|---|
| 覆盖 10 项决策 | §2–§11 一一对应 | ✅ |
| 明确「只设计，0 code」 | 头部声明 + §13 | ✅ |
| 不碰 frozen contract | §11 逐项检查 = 0 冲突 | ✅ |
| 状态机终态集与 SUSPENDED/唤醒定义 | §3.2/§3.3/§3.4/§8 | ✅ |
| 双轴种子源数据映射 | §7.1/§7.2 | ✅ |
| 心跳调度顺序 | §10.1/§10.2 | ✅ |
| 沉淀通道走既有 SAGE | §9.2 | ✅ |
| CONTRACT CONFLICT 结论 | §11 | **NONE** |

---

*TG-1 设计完成。实作与测试全部移交 TG-2 工单，本文件不构成施工授权。*