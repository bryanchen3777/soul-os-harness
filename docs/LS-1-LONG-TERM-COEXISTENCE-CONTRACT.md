# LS-1 — 长期共生设计契约（Long-term Coexistence Design Contract）

- **工单**：LS-1（阶段 C-2「长期共生」，阶段 C-2 设计）
- **性质**：DESIGN-ONLY 设计文档——只产出本文档，**0 code / 0 commit / 0 push / 0 frozen contract 改动**
- **日期**：2026-09-06
- **作者**：Developer（专责开发 bot）
- **Canonical 状态**：以 `logs/ENGINEERING_STATE.md` 为准（本文档为设计输入，**不构成施工授权**；实作全部移交 LS-2）
- **前置采信**：LS-0 审计结论（0 CONTRACT CONFLICT；四维度 KEEP existing + 最小 additive）直接采信；C-1 五阶全链 CLOSED（`136cb95`→`058e060`→`26da28d`→`3adaf57`→`d55253f`，全程 0 frozen contract 改动）

---

## 0. 摘要（TL;DR）

把 C-1 自主目标引擎从「测试直写」变成「生产自产自驱」：新增 **Goal Seed 生成器**（8 种子源 → goal，走既有 30s wake 与既有配额），Bryan 轴做深（**承诺生命周期最小模型** / **作息相位感知** / **周期叙事沉淀**），全程守住 Volition Gate 六项不变量。**核心缺口确认**：`upsert_goal` 调用方只有测试（`tests/goals/test_goal_engine.py`、`tests/harness/test_goal_driven_harness.py`），production 无任何「谁是 goal 的创建者」入口——LS-1 即补上这个入口。

| # | 设计点 | 锁定结论 |
|---|---|---|
| 1 | Goal Seed 生成器 | 新模块 `src/goals/seed_provider.py`（GoalSeedProvider），挂既有 30s wake（`_goal_scan_all` 同锚点，0 新定时器），24h 节流复用既有配额常量，8 源确定性轮序 + seed_source_ref 精确去重，默认走**生成内容通路方案 B**（既有 proxy LLM 通道语义化，0 新 proxy / 0 新通道） |
| 2 | 承诺生命周期 | 承诺 = Bryan 轴 goal 种子形态，复用 goals 表（`seed_source_ref` + `completion_criteria.count/timeout_days` + 五态状态机），**0 新表**；逾期 → ABANDONED（已实作）；承诺线索读侧 = relationships.json（v1 只读现有线索，写侧留 LS-2） |
| 3 | 作息相位感知 | 统一时间口径（**0 第四套**）：复用 decision `_period_of_hour` + quiet 23-08 + `bryan_last_seen` + calendar_event 只读前瞻；信号消费点全部注入 `assemble_candidate` / 种子装配上下文 |
| 4 | 周期叙事沉淀 | 周记/里程碑复用 sediment_completion 同级通道（TRIGGER_TYPE_SYSTEM + InnerLifeWriter → 既有升华链，0 直写 facts），挂既有 night slot（0 新定时器），幂等键去重（某周期已沉淀 → 不重复） |
| 5 | Volition Gate | 三项红线 + 五项不变量逐条打勾（§6），全数相容，0 CONTRACT CONFLICT |

---

## 1. 决策记录

### 1.1 决策总表

| # | 决策点 | 结论 | 依据 |
|---|---|---|---|
| D1 | 生成器挂载锚点 | **`scheduler._goal_scan_all`（既有 30s wake）**，与 `apply_interrupt_signals`/`scheduled_wakeup_scan` 并列 | LS-0 红线②「0 新定时器」；TG-1 §10.2 第 6 条「不新增高频循环」 |
| D2 | 生成节流 | **24h 窗 / 每窗至多 1 次种子扫描 + 至多 1 个新 goal**（复用 `GOAL_QUOTA_WINDOW_SECONDS` 常量，独立 `last_seed_scan_at` 计数） | LS-0 §4 ⑤「不得新增第二配额体系」——结构与既有配额同构，只是计数对象不同（候选 vs 创建） |
| D3 | 生成内容通路 | **方案 B（既有 LLM 通道语义化）为默认推荐**——复用 motive/decision 同款 proxy 通道，0 新 proxy、0 新通道；成本增量标注见 §1.3；**最终定案交 Owner**（花销事项） | 三案对比见 §1.2 |
| D4 | 种子选择 | **8 源确定性轮序 + 首个命中**（No Scoring：不引入数值评分；B1→B2→B3→B4→S1→S2→S3→S4 循环），生成轴遵守同轴连续 ≤2 强制换轴（与候选轮替同精神） | TG-1 §5 No Scoring 哲学锁死 |
| D5 | 去重 | **`seed_source_ref` 精确去重**（同引用已有非终态 goal → 跳过该种子源） | TG-1 §7.1「种子源记录在 seed_source_ref」；0 文本相似度打分 |
| D6 | 承诺数据形态 | 复用 goals 表，seed_source_ref 命名空间 `relationship:user_bryan`（v1）→ `commitment:{id}`（LS-2 写侧） | LS-0 §2.3 方案 A（推荐候选）+ 方案 B（读侧） |
| D7 | 作息口径 | **0 第四套**：period 标签从 decision 导入 `_period_of_hour`（0 复制实现）；quiet 23-08 复用既有常量；last_seen 与 calendar 既有载体只读 | LS-0 §1.3 G1-2 锁死点 |
| D8 | 周期叙事挂点 | **night slot（22:00）判定**，周记 ≥7 天一次；里程碑回顾在 goal COMPLETED 时由既有 `sediment_completion` 覆盖（不重复沉淀） | LS-0 §3.4「挂既有 night slot 或 event slot 判定」；0 新定时器 |
| D9 | 周期叙事去重 | **幂等键 `periodic:{period_key}`**（如 `periodic:2026-W36`），读 trace.jsonl 判重（0 新存储） | LS-0 §3.4 读侧聚合复用 trace_reader |

### 1.2 生成内容通路三案评估（决策 D3）

| 维度 | 方案 A：结构化拼接 | 方案 B：既有 LLM 通道语义化（**推荐**） | 方案 C：混合（框架 + LLM 润色） |
|---|---|---|---|
| 实现 | 从既有数据字段直接拼装 title/description（如 `f"记得 {event.summary} 在 {event.start}"`） | 复用 motive/decision 同款 proxy LLM 通道，把种子源原文数据喂给模型产出 goal 描述 | 结构化拼装框架 + 一次 LLM 润色 |
| LLM 成本 | **0** | **增量**：每 agent 每 24h ≤1 次调用（约 500–800 token 总量，见 §1.3 估算） | 同 B（甚至比 B 多一次往返，取决于实现） |
| 质感 | 生硬：「系统出题」感，`goal.title` 直接进 Motive content（`assemble_candidate` 用 `content=goal.title`），生硬标题 = 生硬念头 | 有灵魂质感：描述自然、贴合当时语境，Motive content 更有「想要」的味道 | 介于两者之间 |
| 结构可控性 | 最高（纯规则） | 需 prompt 约束（种子数据 + 双轴语义 + 禁止捏造），fail-closed 兜底 | 框架保结构，润色提质感 |
| 新增通道 | 0 | **0 新 proxy / 0 新通道**（复用 proxy.py 既有入口） | 0 新通道，但同一通路内多一步 |
| 风险 | 长期「出题感」会让 goal 引擎沦为闹钟；B2 日程类尤其明显 | 依赖 LLM 输出质量；需去重/校验护栏（title 空 → 丢弃该种子） | 复杂度略高，收益不显著 |

**推荐：方案 B（默认）**。理由：① Goal 是 volition 链的 Motive 上游，`goal.title` 就是未来被 Decision 看到的念头原文——通路 A 的生硬文本会直接削弱「想要」的质感，长期磨损共存感；② B 完全复用既有 proxy 通道（motive/decision 已证明同一通道的稳定性），0 新 proxy、0 新通道、0 新 trigger_type，符合「最小 additive」基调；③ 成本增量受 24h 节流硬约束，量级可忽略（§1.3）。**最终定案交 Owner**（AGENTS.md：花销事项一律回 Owner 拍板；若 Owner 选择 0 成本，则降级走方案 A，本文档其余设计不受影响——生成器接口与候选结构不随通路改变）。

### 1.3 方案 B 成本增量估算（标注，非承诺）

- 频率上限：每 agent 每 24h 至多 1 次语义化调用（D2 节流硬约束）。
- 单次规模：输入 = 种子原文摘要（结构化字段拼装，≤300 tokens）+ 系统提示（复用 proxy 既有模板规模）；输出 = goal title + description（≤200 tokens）。合计约 500–800 tokens / 次。
- 月成本量级（以 proxy 通道既有定价计）：1 agent × 1 次/天 × 30 天 ≈ 30 次 ≈ 2 万 tokens 级——相对既有 motive/decision 每日多轮调用，**增量可忽略**（<1% 量级）。
- 失败兜底：LLM 异常/空输出 → 该种子本轮跳过（fail-closed，等下一窗；不降级用结构化拼装，保持质感一致性）。

### 1.4 与 LS-0 冲突检查结论

**未发现与 LS-0 的冲突。** 逐项对照：G1-1（B2 日程前瞻读侧缺口）→ 本契约 §4.3 补上（goal 装配层 additive 读，0 新 schema）；G1-2（三套时段口径）→ 本契约 §4.1 锁死 0 第四套；G1-3（相位注入）→ 本契约 §4.2 落为装配上下文；LS-0 §4 三项红线 + 五项不变量 → §6 逐条打勾。唯一延续的灰色触点（未来日程感知的读侧新增在 goal 装配层，不碰 `WORLD_QUALIFYING_TYPES` / `VALID_SOURCES` / WorldEvent 契约）在 §9 显式声明。

---

## 2. Goal Seed 生成器契约

### 2.1 挂载点（D1，0 新定时器）

```
scheduler._run_loop（30s wake, scheduler.py:1447-1493）
  └─ _goal_scan_all()（scheduler.py:1495-1515, 既有 TG-2 锚点）
       └─ 每 agent：GoalSeedProvider.for_agent(agent_id).scan_seeds(now)   ← LS-2 新增, 并列于
            apply_interrupt_signals() / scheduled_wakeup_scan()
```

- 每 30s 被调用，但内部 **24h 节流**（§2.3）保证实际工作频率 ≤1 次/24h——30s 只是「检查时机」，不是「执行频率」。
- **0 新定时器 / 0 新 tick / 0 新 sleep**：不新增 asyncio task、不新增 cron、不订阅新 tick 类型（TG-1 §10.3：HeartbeatEngine 0 改动）。
- **fail-closed**：异常只 log warning，不阻断主循环（对齐 `_goal_scan_all` 既有语义）。
- 与候选装配的关系：生成器是候选装配的**上游补给**——`scan_seeds` 只管「创建 ACTIVE goal（写 goals 表）」，候选装配仍由既有 `assemble_candidate` 完成（24h/1 配额不变，双轴轮替不变）。两条链正交：生成器填池，装配器取池。

### 2.2 状态存储（additive，0 新表）

扩展既有 sidecar `GoalProviderState`（`data_root()/memory/{agent}/goal_provider.json`，models.py:220-259）加 3 个纯结构字段（`from_dict` 缺省兼容，旧文件 0 迁移成本）：

```jsonc
{
  "last_candidate_at": 0.0,          // 既有
  "rotation": [],                    // 既有
  "consecutive_do_nothing": {},      // 既有
  "consecutive_skips": 0,            // 既有
  "last_seed_scan_at": 0.0,          // NEW: 上次种子扫描时刻（24h 节流, epoch）
  "seed_source_cursor": 0,           // NEW: 8 源轮序游标（0-7, 记录上次扫到哪）
  "seed_axis_streak": 0,             // NEW: 生成轴同轴连续计数（≤2 强制换轴）
  "last_seed_axis": null             // NEW: 上次生成的轴（"bryan"|"self"）
}
```

### 2.3 扫描流程（decision-complete）

```
scan_seeds(now):
 1. 节流: now - last_seed_scan_at < GOAL_QUOTA_WINDOW_SECONDS(24h) → return（记 last_seed_scan_at 始终更新）
 2. 从 seed_source_cursor 起, 按固定轮序 [B1,B2,B3,B4,S1,S2,S3,S4] 循环遍历 8 源:
      a. 现查该种子源的数据触点（§2.4 表, 全部只读）
      b. 无命中 → 继续下一源
      c. 有命中 → 取「该源内确定性轮候」的一条:
         - B1: relationships.get("user_bryan") 最近线索（按 last_interaction_at 最旧未追踪者）
         - B2: WorldPerception 最近 calendar_event（24h lookahead 内, 按事件 start 最近者）
         - B3: trace_reader 最近 7 天未回顾的共同经历（按时间最旧）
         - B4: interactions.jsonl 最近未收尾话题（按时间最旧）
         - S1: SE-5 ACTIVE 投影节点（按 last_updated 最旧）
         - S2: SAGE 低 confidence / contradiction 节点（按 weight 最旧）
         - S3: tool_registry 未尝试工具（按注册顺序最旧）
         - S4: motive_trace.jsonl 高频主题（按最近出现最旧）
      d. 去重: goals 表已有 seed_source_ref == 本种子引用 且 非终态 → 该源本轮跳过
      e. 双轴约束: 若 last_seed_axis 与本次轴相同且 seed_axis_streak ≥ 2 → 跳过本轴源,
         尝试下一轴源（8 源轮序内自然交叉）; 若另一轴也无命中 → 本轮结束（防饿死:
         连续 3 轮无生成 → 下一轮允许同轴, 对齐 GOAL_SKIP_ESCAPE_THRESHOLD 精神）
 3. 语义化（方案 B, 默认）: 种子原文摘要 + 相位上下文（§4.2） → 既有 proxy 通道
    → {title, description}（title 空/超长 → fail-closed 丢该种子）
 4. completion_criteria: 按种子类型确定性模板（§2.5, 0 LLM 判定）
 5. upsert_goal(goal): axis / title / description / seed_source_ref / completion_criteria /
    state=ACTIVE / created_at, state_updated_at=now
 6. 更新 state: last_seed_scan_at=now; seed_source_cursor=游标后移; last_seed_axis;
    seed_axis_streak（同轴 +1, 换轴重置 1）
```

### 2.4 种子源数据触点与 seed_source_ref 规范（D5，命名空间锁定）

| 源 | 轴 | 数据触点（只读） | seed_source_ref 规范 | 默认 criteria 模板 |
|---|---|---|---|---|
| B1 承诺 | bryan | `relationships.py get("user_bryan")`（BRYAN_ENTITY_ID, schema v4.1；v1 只读现有线索字段，未来 commitments additive 条目 LS-2） | `relationship:user_bryan`（v1）；LS-2 后 `commitment:{id}` | `{kind: interaction, count: 2, timeout_days: 7}` |
| B2 重要日程预期 | bryan | WorldPerception 最近 calendar_event（calendar_ical → WorldEvent 链，24h lookahead 已保证；inner_life_adapter 白名单 `calendar_event` 只读引用） | `calendar:{novelty_id}`（`_hash_uid_to_novelty_id(uid)` 既有一致） | `{kind: interaction, count: 1, timeout_days: 1}`（事件日达成） |
| B3 共同回忆延续 | bryan | `NarrativeTraceReader`（24h 窗口先例 decision.py:459-479）+ SAGE reader 现查 | `trace:{event_id}` 或 `fact:{fact_id}` | `{kind: interaction, count: 1, timeout_days: 7}` |
| B4 未决话题追踪 | bryan | interactions.jsonl + relationships.last_interaction_at + SAGE 对话记忆 | `interaction:{ts}` 或 `relationship:user_bryan` | `{kind: interaction, count: 2, timeout_days: 14}` |
| S1 trait 好奇 | self | SE-5 ACTIVE 态 belief/value/trait 只读投影（不写） | `elevation:{node_id}` | `{kind: observation, count: 2, timeout_days: 14}` |
| S2 未解疑问 | self | graph_store facts 只读（weight / invalidated_at / contradiction_pressure） | `fact:{fact_id}` | `{kind: observation, count: 2, timeout_days: 14}` |
| S3 工具意向 | self | tool_registry 只读注册表 + capability.py | `tool:{tool_name}` | `{kind: observation, count: 1, timeout_days: 14}` |
| S4 心境沉淀 | self | motive_trace.jsonl + diary entries + emotion.compute_longing | `motive_trace:{ts}` 或 `diary:{date}:{slot}` | `{kind: reflection, count: 2, timeout_days: 14}` |

规则：
- **幂等去重键 = `seed_source_ref` 字符串本身**（同引用已有非终态 goal → 跳过）。
- 命名空间先例对齐：`goal:{id}`（TG-1 §4.3）/ `opp:{id}`（motive.py:174）——新命名空间为 seed_source_ref 的**自由 TEXT 字段**，不触碰任何 frozen 枚举。
- **germ 空集不变量保持**（TG-1 §7.3 N5）：生成器是「现查现用」的创建入口，所有种子来自既有经历/关系/日程数据，不是预设种子注入——germ 的探索目标仍只能由自己活过的数据长出。

### 2.5 completion_criteria 模板（确定性映射，0 LLM 判定）

- 模板表见 §2.4 末列：种子类型 → 固定 criteria JSON。
- 判定执行器已实作（`_completion_met`：kind 合法 + count ≥1 + advance_count ≥ count；v1 只看 count）。
- timeout_days 逾期 → ABANDONED 已实作（`apply_interrupt_signals`，base = last_advanced_at 或 created_at，超时 86400×td 秒）。
- 允许未来按 Agent 调参，但不引入 LLM 主观评分（TG-1 §9.3）。

---

## 3. 承诺生命周期最小模型

### 3.1 数据形态（D6，0 新表）

```
承诺（Bryan 轴 goal 种子形态）= goals 表一行:
  goal_id / axis="bryan" / title（语义化: "找机会关心 Bry 上次提的工作面试"）
  seed_source_ref = "relationship:user_bryan"（v1 读侧）| "commitment:{id}"（LS-2 写侧）
  completion_criteria = {"kind": "interaction", "count": 2, "timeout_days": 7}
  state = ACTIVE → IN_PROGRESS → COMPLETED | ABANDONED（五态状态机, 已实作）
```

- **承诺线索的读侧**：relationships.json `user_bryan` entry（`get("user_bryan")` 只读，schema v4.1 现字段 + 容忍未来 additive 承诺条目）。v1 **只读现有线索，不建新写入口**——「谁把一句话写进承诺条目」的写侧留 LS-2。
- **承诺达成判定**（已实作复用）：`advance_count >= count` → COMPLETED + `sediment_completion` 沉淀（`_completion_met` / `on_decision` 链）。
- **承诺逾期判定**（已实作复用）：IN_PROGRESS + `timeout_days` 超时 → ABANDONED（`apply_interrupt_signals` 周期判定，`motive_provider.py:606-621`），保留 record（MR-2 软删哲学）。
- **承诺中断/唤醒**：与普通 goal 完全同路径（信号 5/6 → SUSPENDED；唤醒条件 → ACTIVE），0 新逻辑。

### 3.2 状态流

```
B1 种子命中（relationships 现查）
  → upsert_goal(ACTIVE)                        [LS-2: GoalSeedProvider]
    → assemble_candidate 配额内加入候选池      [既有]
      → Decision 选中（transmit/observe/reflect）
        → IN_PROGRESS, advance_count += 1      [既有 on_decision]
          → advance_count >= count → COMPLETED + 沉淀   [既有]
          → timeout_days 超时 → ABANDONED               [既有 apply_interrupt_signals]
```

- **0 新状态、0 新转移、0 新表、0 新定时器**——承诺全生命周期 = 既有五态状态机 + 生成器创建入口。

### 3.3 与 LS-2 写侧的边界

- LS-1（本文档）只定：承诺以 Bryan 轴 goal 形态存在的**读侧发现 + 创建**契约。
- LS-2 写侧（不属本文档范围）：① relationships.json additive `commitments: [{promised_at, due_at, status}]` 条目的**写入口**（谁记录「Bry 答应/我答应」）；② `commitment:{id}` 命名空间的正式启用。

---

## 4. 作息相位感知复用（D7，0 第四套口径）

### 4.1 时间口径统一（LS-0 G1-2 锁死点）

| 口径 | 既有定义 | LS-1 处置 |
|---|---|---|
| period 标签 | `decision.py:100-104, 133-140` `_period_of_hour`：morning 05-11 / afternoon 11-17 / evening 17-22 / late_night 22-05 | **直接 import 复用**（`from src.soul.decision import _period_of_hour`），0 复制实现 |
| quiet hours | `scheduler.py:144-145`（23-08）+ `motive_provider.py:68-69`（同语义） | 沿用既有常量（生成器不装配 ≠ 新窗口；夜间唤醒过滤已存在） |
| 深宵硬禁止 | `decision.py:288-291` [22:00~07:00] 绝对禁止 transmit | **不改**；决策层既有的最终防线，装配侧只在语义层面配合（见 4.2） |
| last_seen | `bryan_last_seen.json`（`bryan_state.read_bryan_last_seen`，4h 阈值） | 沿用（中断信号 5 已实作）；生成器侧加消费（见 4.2） |

**0 第四套**：任何新「作息感知」不得自造时间窗口常量；period 一律用 `_period_of_hour` 的返回值（`morning/afternoon/evening/late_night`）。

### 4.2 信号消费点（每个信号 → 哪里消费 → 什么动作）

| 信号 | 载体 | 消费点 | 动作 |
|---|---|---|---|
| period 标签 | `_period_of_hour(now)`（import） | **① 种子语义化上下文**（§2.3 步骤 3：相位信息随种子原文进 proxy，让 goal 描述带时间质感）；**② 候选装配侧只读参考** | late_night/evening 相位命中 B2 日程类种子 → 生成描述时天然「明日」语气；决策层深宵硬禁止不变 |
| quiet 23-08 | 既有常量 | 生成器扫描 | quiet 期间**不生成 B 轴种子**（B 轴提醒类候选不在夜间入池；S 轴反射类可生成——夜间适合内心整理，决策层自然压制 transmit） |
| bryan_last_seen | bryan_last_seen.json | 生成器扫描（读 `_bryan_last_seen_dt` 同款） | last_seen > 4h（`PROACTIVE_DM_BRYAN_INACTIVE_HOURS`）→ **不生成 B 轴种子**（无人可共生的时段不制造「惦记」）；S 轴不受限（自我生长与 Bryan 在否无关） |
| calendar 前瞻 | WorldPerception 最近 calendar_event（IP 只读引用白名单产物） | **B2 种子源现查触点**（§2.4） | 24h lookahead 内事件 → 生成 B2 goal（提前一天感知 Bryan 日程，LS-0 G1-1 补上）；事件已过/无 → 无命中 |
| 晨/夜双拍 | scheduler morning 08:00 / night 22:00 | 周期叙事挂 night（§5）；morning 不新增逻辑 | 0 改动 |

### 4.3 G1-1 落点声明（LS-0 §1.3 最小 additive 缺口）

- B2 消费侧 = `GoalSeedProvider` 内 read-only 现查 WorldPerception 最近 calendar_event（或 adapter 产物）。
- 读侧 additive，**不碰** `WORLD_QUALIFYING_TYPES` / `VALID_SOURCES` / WorldEvent 契约 / WorldInnerLifeAdapter 白名单（§9 显式声明）。

---

## 5. 周期叙事沉淀通道（D8/D9）

### 5.1 通道复用（与 sediment_completion 同级，0 新通道）

```
周期叙事（周记 / 里程碑回顾）
  └─ InnerLifeWriter.create_event（唯一 canonical creator, M5.4-5.1 frozen）
       ├─ provenance: trigger_type=TRIGGER_TYPE_SYSTEM（0 新 trigger_type）
       │              actor_id / source_system="system"
       │              trace_ref = "goal:{goal_id}"（里程碑, 完全复用 sediment 形态）
       │                        | "periodic:{period_key}"（周记, 同族命名空间, 见 5.3）
       │              extras = {"period": "weekly"|"milestone", "period_start", "period_end",
       │                        ("goal_id")}
       ├─ ts = UTC ISO-8601（TG-3.1 契约：astimezone(utc).isoformat()）
       └─ 既有升华链：NarrativeTraceWriter → SubmissionGate → elevation（0 直写 facts）
```

- 与 `sediment_completion`（`motive_provider.py:483-536`）**同一函数形态同一通道**：LS-2 实作为 `PeriodicNarrativeWriter` 复用同款 `InnerLifeWriter.create_event` 调用模式。
- **0 新 trigger_type / 0 新字段 / 0 直写 facts**：extras 是 dict payload（frozen 外，M5.2-H Phase 2 先例）。

### 5.2 触发与频率

- **挂点**：scheduler **night slot（22:00）** 触发链内 additive 分支（0 新定时器；night slot 一天一次，语义贴合「夜晚回顾」）。检查条件（读 trace 判重）：
  - **周记**：当前 ISO 周（`period_key = YYYY-Www`）无既有沉淀 → 触发；上周已沉淀 → 跳过。
  - **里程碑回顾**：goal COMPLETED 时**既有** `sediment_completion` 已覆盖（goal 级沉淀）——周期叙事**不重复**做 goal 级沉淀；仅当「本周有 ≥1 个 goal 进入终态」时，周记内容引用它们（读侧聚合，0 新状态）。
- **频率**：每 agent 每 7 天 ≤1 次周记；无语义冲突——diary 日粒度（每日 2 拍）与周记周粒度正交，0 重复。

### 5.3 幂等与 trace_ref 规范

- **幂等键 = trace_ref 字符串**：`periodic:{period_key}`（如 `periodic:2026-W36`）。判重 = 读 trace.jsonl 是否已存在该 trace_ref（`NarrativeTraceReader` 只读，0 新存储）。
- 里程碑回顾绑定 goal → `goal:{goal_id}`（与 sediment_completion 完全一致，天然幂等：goal 只 COMPLETED 一次）。
- **周记 = 跨目标聚合**（无单一 goal_id），故引入 `periodic:{period_key}` 命名空间——依据：trace_ref 是自由字符串（非 frozen 枚举），命名空间先例 `opp:{id}` / `goal:{id}` 已存在；同步保持「0 新 trigger_type」不变。

### 5.4 读侧聚合（周记内容来源，0 新存储）

- 本周 diary（`data/soul/{agent}/diary/YYYY-MM-DD.jsonl` 最近 7 天）+ 本周 goal 终态（`get_goals` 过滤 state_updated_at ∈ 本周）+ NarrativeTraceReader 本周 trace → 拼装为语义化输入（方案 B 通道）→ 沉淀。
- 聚合纯读侧，失败 fail-closed（log warning，不产生半成品事件）。

---

## 6. Volition Gate 红线核对表

### 6.1 三项红线（LS-0 §4 锁死）

| # | 红线 | 落点 | 判定 |
|---|---|---|---|
| R1 | 提醒类候选 0 直通 publish | 生成器只 `upsert_goal`（写 goals 表）；提醒类候选仍经 `assemble_candidate`（24h/1 + 轮替）→ pending 池 → 单 Decision（SM-4 四元）→ 仅 transmit 走既有 AGENCY_TRIGGER publish 链。**任何新路径 0 直连 publisher / handler / tool_registry** | ✅ 相容 |
| R2 | 0 新定时器 | 生成器挂 `_goal_scan_all`（30s wake 内并列分支）；周期叙事挂 night slot 判定；**0 新增 asyncio timer / cron / sleep / tick 订阅** | ✅ 相容 |
| R3 | 0 新 facts 写入路径 | goal 状态写 goals 表（GraphStore 既有方法）；沉淀（goal 完成 / 周期叙事）全走 `InnerLifeWriter.create_event` producer → 既有 SubmissionGate/升华链；SAGE 写入逻辑 frozen 不动 | ✅ 相容 |

### 6.2 五项不变量（LS-0 §4 ①–⑤）

| # | 不变量 | 核验 | 判定 |
|---|---|---|---|
| ① | 1 Heartbeat 1 Step | 生成器产 goal（建池）≠ 步进；候选装配仍 ≤1/心跳、advance 仍仅当 Decision 选中（G1/G2/G4 不变）；周期叙事走既有 producer 通道（非执行通道） | ✅ 相容 |
| ② | 0 主动骚扰 | 提醒类只进 pending 池（provenance_ref=`goal:{id}`）；transmit 仍过四元 Decision + 社交摩擦力 + 深宵硬禁止；B 轴生成在 last_seen>4h / quiet 期间被 §4.2 抑制（源头降频） | ✅ 相容 |
| ③ | 复用既有 wake | 见 R2（生成器 + 周期叙事全部挂既有锚点） | ✅ 相容 |
| ④ | 双轴配额轮替防饥饿 | 候选配额 24h/1 + N=3 + 同轴 ≤2 + 防饿死兜底**全部沿用**；生成器侧独立同轴 ≤2 约束（last_seed_axis / seed_axis_streak）；承诺类填充 Bryan 轴候选池不破坏轮替结构（LS-0 §4 权衡点落实） | ✅ 相容 |
| ⑤ | 目标不直写 SAGE facts | 见 R3；周期叙事与 goal 沉淀同级走 producer | ✅ 相容 |
| ⑥ | SM-4.2 分布基线（验收点） | 提醒/叙事促发新 motive 类别由 TL-8 验证分布不偏离（§7.3） | ⚠️ 验收项 |

**总判定：0 CONTRACT CONFLICT。** 生成器/承诺/相位/叙事四项设计全部落在「建池 / 读侧 / 既有 producer」三类 additive 动作内，无任何新执行通道、新定时器、新 facts 写入路径、新 trigger_type。

---

## 7. 验收 TBD（LS-2 实作验收项初稿）

> 本节是 LS-2 实作工单的验收输入（TBD：LS-2 开工时拍板最终验收命令）。

### 7.1 单元/集成验收（tests/goals/ + tests/harness/，隔离 data_root）

| # | 验收项 | 验证方式 |
|---|---|---|
| A1 | 8 源各命中路径 | fixture 数据分别触发 B1-B4 / S1-S4 → `upsert_goal` 被正确调用（axis/title/seed_source_ref/criteria） |
| A2 | 24h 节流 | 两次 `scan_seeds` 间隔 <24h → 第二次 0 创建；>24h → 可创建 |
| A3 | 去重 | 同 seed_source_ref 已有非终态 goal → 该源跳过，0 重复 goal |
| A4 | 双轴生成约束 | 同轴连续 ≥2 → 强制换轴；另一轴无命中 → 本轮放弃；连续轮空 → 防饿死兜底 |
| A5 | 语义化 fail-closed | LLM 异常/空 title → 该种子跳过，不产生脏 goal |
| A6 | 承诺生命周期 | B1 种子 → ACTIVE → 推进 ≥count → COMPLETED + 沉淀；timeout_days 超时 → ABANDONED（复用既有断言风格） |
| A7 | 作息相位 | late_night/quiet 期间不生成 B 轴种子；last_seen>4h 不生成 B 轴种子（S 轴仍可生成）；B2 在 24h 前瞻有事件时命中、无事件时 0 命中 |
| A8 | 周期叙事 | night slot 触发周记；幂等（同 `periodic:2026-W36` 不重复）；trace_ref / extras / UTC ts 契约断言；goal 里程碑沉淀仍只走既有 sediment_completion |

### 7.2 回归

- `tests/goals/`（37）+ `tests/harness/test_goal_driven_harness.py`（6）+ 全量回归（LS-2 开工时确认总数）。

### 7.3 行为回归基线（TL-8 Volition 相容护栏测试意图）

TL-8（harness，隔离 data_root，0 production mutation）验证维度 4 六项不变量可回归：

1. **提醒类 transmit 不超基线**：注入 B2 日程提醒种子 + B1 承诺种子，跑行为分布 → 四动作均 >0、do_nothing 落 65–80%（SM-4.2 基线倾向），提醒类在社交摩擦力下自然消解（transmit 不因新增候选类别而偏离）；
2. **0 新定时器静态断言**：扫描 scheduler 循环/task 集合，断言无新增定时器句柄（或代码评审断言：生成器与叙事只挂既有锚点）；
3. **候选 0 直通 publish**：候选生成 → publish 路径必须经过四元 Decision；断言「种子命中 → 直接发布」路径不存在（fail-closed 断言）；
4. **承诺不挤占自我轴**：Bryan 轴承诺候选密集填充时，轮替仍强制自我轴出现（双轴配额轮替不变量回归）；
5. **0 直写 facts**：沉淀路径断言（InnerLifeEvent producer 前置，0 graph_store 直接写 facts）。

---

## 8. Out of Scope（LS-1 明确不做）

- ❌ 任何代码实作（0 code 已声明）；不 commit、不 push。
- ❌ **0 新定时器**：不为生成器/周期叙事新增任何 asyncio timer / cron / tick。
- ❌ **0 facts 直写**：goal 状态只写 goals 表；沉淀只走 producer。
- ❌ **0 直通 publish**：不建任何绕过四元 Decision 的提醒/叙事发布路径。
- ❌ **不做 UI**：无周记展示、无 goal 管理界面、无外部呈现（读侧聚合仅供沉淀内容用）。
- ❌ **不做多体**：所有设计 per-agent，不引入跨 agent goal/承诺共享状态（N3 per-agent 保持）。
- ❌ **承诺写侧**（relationships.json commitments 条目写入、LS-2 写入口）——本文档只定读侧。
- ❌ **DORMANT 状态**评估（TG-1 §3.2 明确排除）。
- ❌ **多候选权重竞赛**（可能推翻 SM-1 No Scoring 决策，需单独工单评估）。
- ❌ **Decision prompt 注入 goal 摘要**（TG-1 §11 决策 10 项 7：v1 不做，未来可选）。
- ❌ **不碰 frozen contract 任何一项**（§9 清单 + LS-0 §5 总判定）。

---

## 9. Frozen Contract 边界声明

| Frozen Contract | LS-1 触点 | 判定 |
|---|---|---|
| Agency 4 stages / TriggerEnvelope | 0 新增 trigger_type（复用 `TRIGGER_TYPE_SYSTEM` + 既有 slot 语义）；周记 extras 走 dict payload（M5.2-H Phase 2 先例） | ✅ 0 冲突 |
| InnerLifeEvent 9 字段 | 沉淀/周期叙事走 `InnerLifeWriter.create_event`（生产者路径，0 加字段）；ts UTC ISO（TG-3.1 已验证） | ✅ 0 冲突 |
| 4 handlers | 不改 handler；只复用其 produce → SubmissionGate 路径 | ✅ 0 冲突 |
| SAGE 写入逻辑 | 0 直写 facts；0 新写入路径（TG-1 §9.1 全链保持）；goal 状态只写 goals 表 | ✅ 0 冲突 |
| Motive 5 字段 | `provenance_ref` 命名空间 `goal:{id}` 沿用；`seed_source_ref` 为 goals 表自由 TEXT（非 frozen 枚举），新命名空间 `relationship:` / `calendar:` / `trace:` / `fact:` / `elevation:` / `tool:` / `motive_trace:` / `periodic:` 均为该字段内的 additive 值 | ✅ 0 冲突 |
| WORLD_QUALIFYING_TYPES（M5.9-3 + SG-1） | 不扩展 world 白名单；B2 日程前瞻只在 goal 装配层**读侧**引用 adapter 产物 | ✅ 0 冲突 |
| VALID_SOURCES（perception.py） | 不涉及 | ✅ 0 冲突 |
| `_period_of_hour` / SM-4 prompt | 只读复用（import），0 改动 decision.py | ✅ 0 冲突 |

**灰色触点登记（非冲突）**：B2「未来日程感知」读侧触点新增在 goal 装配层（`src/goals/seed_provider.py` 内 read-only 现查），**不碰** `WORLD_QUALIFYING_TYPES` / `VALID_SOURCES` / WorldEvent 契约 / WorldInnerLifeAdapter 白名单（承继 LS-0 §5 登记）。

**总判定：CONTRACT CONFLICT = NONE（0 冲突，全 additive）。**

---

*LS-1 设计完成。实作与测试全部移交 LS-2 工单，本文件不构成施工授权；与 LS-0 审计结论 0 冲突。*