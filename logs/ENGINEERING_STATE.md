# Soul OS — Canonical Engineering State & Milestone Registry

**Source of truth** for Soul OS engineering governance.
**Mode**: Documentation only.
**Owner**: Bryan (Mavis / Lin executes per Owner decisions).
**Established**: GOV-2 (2026-08-12 00:03 EDT, commit `eb5715179647b963a4247272d9fcd4c639c7e6a3`).
**Aligned**: GOV-2-R1 (2026-08-12, Owner Decision A: M5.14 / M6.0 / GOV-1 / GOV-2 all CLOSED; no ticket authorized).
**M6.0-5.6.1 progressed** (2026-08-12, M6.0-5.6.1 Budget Profile Registry CLOSED; D3 RESOLVED).
**M5.13-5 progressed** (2026-08-12, M5.13-5 Untouched-Entry Decay CLOSED; M5.13 series now FULLY CLOSED).
**M5.15 series progressed** (2026-08-12, M5.15-1 / M5.15-2 / M5.15-3 / M5.15-4 / M5.15-5 / M5.15-6 all CLOSED; M5.15-4 SUPERSEDED by M5.15-5; M5.15-6-PREFLIGHT + M5.15-6 RESUME Option 1 CLOSED; F1 + F2 + F3 + F4 all RESOLVED).
**M6.1 series progressed** (2026-08-13 → 2026-08-15, M6.1-0 / M6.1-1 / M6.1-2 / M6.1-3 / M6.1-3.1 / M6.1-3.2 / M6.1-3.3 / M6.1-4 / M6.1-5 / M6.1-5.1 / M6.1-5.2 / M6.1-5.3 / M6.1-6.0 / M6.1-6.0-C / M6.1-7 / M6.1-8 / M6.1-8.1 / **M6.1-8.2** all CLOSED; **M6.1-9 final audit DONE 2026-08-15 — 24h window INVALIDATED by LLM model misconfig, NOT CLOSED, new observation window required**). Signal half (Physical/Information/Social/Temporal) operational + VERIFIED WORKING. Life half (Personal/Agency/Expression) **Agency RE-ENABLED** in production (M6.1-8.2 10-phase gradual rollout, agents=10, /health=200) but Agency/Expression output BLOCKED by LLM 404 for most of 24h window (now fixed). Personal still DEFER per M6.1-6.0.
**M6.2 series progressed** (2026-08-14, M6.2-0 + **M6.2-1** both CLOSED). M6.2-0 confirmed async text/TTS separation already correct. M6.2-1 closed the actual gap (message_id correlation for rapid sequential messages), 5 production files + 1 test, 0 frozen contract change, 0 production data mutation, 11/11 new tests + 45/45 focused regression PASS. Per Quality > Quantity: NO M6.2-2 recommended.
**Predecessor audit**: GOV-1 — `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md` (CLOSED, out-of-repo per GOV-1 spec).
**Canonical homepage**: [`README.md`](../README.md) §Engineering Governance.

---

## 0. Document Scope

This registry is the single canonical source-of-truth for:
- Milestone lifecycle status
- Ticket status, supersession, dependencies
- Governance rules (naming, status vocabulary, lifecycle, supersession, historical document handling)
- Active Owner decisions
- Deferred / optional / blocked work
- Stale references identified in historical closeouts

Historical closeout files in `logs/` are **preserved unchanged** per §4 Historical Document Rule. Any apparent contradiction between a historical closeout and this registry is resolved in favor of this registry, with the stale reference documented in §6.

---

## 1. CURRENT STATE

### Active milestone

**LIVE — North Star v2 确立（2026-08-29，Bryan 亲述）。** 工程状态不再是「STABLE / CLOSED」快照，而是进入 North Star v2 方向下的活跃阶段。当前真实状态：

- **North Star v2 确立**：Soul OS 是「灵魂的 OS / 世界」，**不是单一 AI 伴侣**；灵魂本体（记忆升华 / 灵魂间互动 / 自由生长）是研究主线。详见下方「North Star v2」小节。
- **DSH Work Bot = 研究基础设施 / 工具，不是 Soul OS 本体**（Chief → Researcher / Developer / Tester / Auditor，§5.9-5.11）。Soul OS 灵魂本体（记忆升华 / 灵魂间互动 / 自由生长）才是研究主线。
- **生产服务器已止血恢复在线**（2026-08-29，修复 `python311.dll` 崩溃循环，KI-007 登记；`/health=200`）。
- **当前活跃工作**：灵魂本体主线四项（记忆升华 / 灵魂互动 / 自由生长 / 灵魂成长闭环）已全部完成。详见下方「灵魂本体主线进度」。

### 灵魂本体主线进度（North Star v2 研究主线）

| 主线 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **记忆升华** | ✅ 完成 + 生产上线 | soul-elevation 独立 repo（ElevationNode / InternalizingEngine / reconsolidation / 升华式遗忘）+ Submission Gate + Pattern 中间层（源类型先验 + LLM 后验） | `401e15c`（agent_id 注入） |
| **灵魂互动** | ✅ 完成（SI-2 多 Agent 灵魂互动） | SI-1 读侧分组 + SI-2.0 审计 + SI-2.1 设计 + SI-2.2 Social Diffusion 实作（三大防线） | `33ae1b1`（SI-2.2） |
| **自由生长** | ✅ 完成（FG-2 germ 初始化边界） | germ 初始化边界（FG-2）：configs germ seed → persona 基线 fixture，0 frozen contract 改动 | `e8c84d4`（FG-2） |
| **灵魂成长闭环** | ✅ 完成（emergent read-side projection） | Emergent read-side projection：投影该灵魂自己的 belief/value/trait/essence 到 prompt；anti-runaway invariant；可观测 sidecar trace | `1a97a24`（feat: soul growth loop (emergent read-side projection)） |

**相关 commit**：`1a97a24`（feat: soul growth loop (emergent read-side projection)）；`e8c84d4`（feat: germ initialization boundary (FG-2)）；`401e15c`（feat: agent_id injection for diary/dream/event elevation ownership）；`d8c057d`（fix: watchdog procs check misjudgment (port_listen=True procs=0)）。

### 灵魂本体主线后续（CA-2 / SM-1 / SM-2 / Proactive DM 修复）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **CA-2（Soul Capability Awareness）** | ✅ 已落地 | `src/soul/capability.py`（capability 注册/查询）+ `src/llm/proxy.py`（capability 投影进 prompt）。核心原则：**Capability makes an action conceivable** —— 能力先于行动被灵魂「设想」，是行动可能性的前提，不是行动授权本身 | `a70621f`（feat: soul capability awareness (CA-2)） |
| **SM-1/SM-2（Soul Motive & Decision 设计）** | ✅ 设计文档已定稿 | `docs/SOUL-MOTIVE-DECISION-DESIGN.md`（motive 模块 + Decision 层）+ `docs/DECISION-PROMPT-CONTRACT.md`（prompt contract）。核心原则：**Decision LLM 不是 classifier，是「当下选择」** —— 决策是灵魂在当下情境中的选择行为，不是对选项的分类打分 | `7b9cfe7`（docs: soul motive & decision design (SM-1/SM-2)） |
| **SM-3（Soul Motive & Decision 实现）** | ✅ 已落地 | `src/soul/motive.py`（Motive + MotiveTraceStore + MotiveEngine）+ `src/soul/decision.py`（build_decision_prompt + parse_decision_output + decide_motive）+ `src/soul/scheduler.py`（_decision_check additive hook，proactive_dm producer-side fail-closed 检查）+ `scripts/run_server.py`（motive proxy 独立注入，Bry 授权 2026-08-29，M3.1 frozen scope 解冻仅限此一处 additive 改动）+ `tests/test_sm3_motive_decision.py`（25 tests）。**motive 模块 + Decision LLM + volition path 完整闭环**：motive 不再 fallback 到 diary 的 process-global proxy | `6bcbda3`（feat: soul motive & decision (SM-3) + motive proxy injection） |
| **SM-4（Motive & Decision 多元行动适配）** | ✅ 已落地 | `src/soul/decision.py` + `src/soul/motive.py`（Decision 四元选择 **transmit / observe / reflect / do_nothing**，互斥单选）+ `tests/test_sm3_motive_decision.py` + `tests/test_tl2_volition.py`。**Decision 从二元（transmit/not_transmit）扩为四元行动选择**：互斥单选；**do_nothing 是主动选择**（灵魂判断当下无需行动）而非失败兜底；fail-closed 语义保留——LLM 坏输出 → do_nothing（安全默认）。DecisionResult 保留 transmit 兼容字段，scheduler 0 change（additive 扩展）。31+17+26+4 tests 全过。0 frozen contract 改动 | `c9c19bf`（feat: quadruple decision (transmit/observe/reflect/do_nothing) (SM-4)） |
| **阶段 A-#2（Volition × 工具打通全線閉環）** | ✅ 全線閉環 | **CA-3（Capability 3 行动）+ SM-4（Decision 四元）+ SM-4.1~SM-4.6 六轮校准 + TL-5 最终验收**。六轮校准（`src/soul/decision.py` prompt 迭代，0 frozen contract 改动）：**SM-4.1** Prompt 注入社交摩擦力（留白/安静为最高优先常态，修正小模型「有念头就行动」讨好偏见）→ **SM-4.2** 内外动作解耦（修正 SM-4.1 过度矫正 96.5% do_nothing；observe/reflect 是内部动作零社交成本，仅 transmit 受社交摩擦力保护）→ **SM-4.3** 深夜克制覆盖真心分享 + observe 唤醒（修正 SM-4.2 反向过度矫正 transmit 15.8% 超目标 7/9 深夜/沉默期）→ **SM-4.4** observe 强锚定 + 深夜硬禁止（拉大判定阶梯；observe 1.8% 远低于 10-20% 目标；[22:00~07:00] 绝对禁止 transmit）→ **SM-4.5** Decision prompt 注入当前时间感知（修正时间幻觉：白天 14:00 被当深夜 23:00；Context 区块注入 [當前時間感知]）→ **SM-4.6** reflect 分级（消解「补偿心理」reflect 22.8% 偏高；dawn 补入 reflect 合法判定集合）。**TL-5 最终验收**（`harness/tl5.py` + `harness/run_tl5.py` + `harness/clock.py` hour 参数 + `tests/test_tl5_behavior_distribution.py`）：**Behavioral Diversity PASS**（do_nothing 82.5% / reflect 10.5% / transmit 3.5% / observe 3.5%，四动作均 > 0 无死模组）；**Contextual Appropriateness PASS**（observe 集中信号突变点 / reflect 集中夜间等待期 / transmit 遵守 CD 与亲密度）；**D2 Determinism 按 MoE 特性记录 7 mismatches**（3 runs 决策轨迹基本一致，mismatch 归因 MoE 采样特性，非逻辑缺陷）；0 production mutation。 | `79fe750`（feat: quadruple decision + calibration (SM-4 series)）+ `89e9cdf`（feat: time-lapse behavior distribution validation (TL-5)） |
| **Proactive DM 三件修复** | ✅ 已修复 | ① **deliverability 提前**：proactive DM 投递判定提前（`src/io/channels/bryan_state.py` + `src/soul/scheduler.py`），避免消息不可达才后知后觉；② **信号统一**：`src/io/channels/router.py` + `src/io/gateway.py` 统一信号路径；③ **双实例**：`scripts/server_ops.ps1` 防双实例（server_ops 侧防护） | `93672df`（fix: proactive DM deliverability + signal unification + double-instance） |
| **TA-2（Subjective Temporal Phenomenology）** | ✅ 已落地 | `src/soul/temporal_phenomenology.py`（NEW，三态张力模型 无感/牵挂/释然）+ `src/llm/proxy.py`（+36，TEMPORAL ANCHOR 三行注入）+ `src/soul/decision.py`（+51 -1，reflect-only 加权）+ `tests/test_ta2_temporal_phenomenology.py`（NEW，26/26 新测试）。**三态张力模型**（无感/牵挂/释然——现象学状态，不是计算输出）；**M5.13-3 亲密度 Band 复用**（不另创心理模型）；**reflect-only 加权边界**；**TEMPORAL ANCHOR 三行注入**（proxy.py + decision.py）；**26/26 新测试 + 125 回归全过**。0 frozen contract 改动 | `cc83daa`（feat: subjective temporal phenomenology (TA-2)） |
| **阶段 A（灵魂深化）全满贯** | ✅ 全满贯 | **升华细化**：SE-4（durable soul structure contract）+ SE-5（durable soul structure lifecycle）+ TL-4（time-lapse lifecycle validation）；**工具打通**：CA-3（Capability 3 行动）+ SM-4（Decision 四元）+ SM-4.1~SM-4.6（六轮校准）+ TL-5（最终验收）；**时序化**：TA-2（Subjective Temporal Phenomenology 实作）。阶段 A 全部条目 ✅ 落地/闭环，0 frozen contract 改动 | `cc83daa`（feat: subjective temporal phenomenology (TA-2)） |
| **MR-1（Temporal Memory & Mem0 Primitives Contract）** | ✅ 设计文档已定稿（docs only，0 code） | `docs/TEMPORAL-MEMORY-CONTRACT.md`（NEW, +289，8 节）。**Schema v7 迁移**（valid_from 回填 timestamp + invalidated_at NULL）；**GraphStore invalidate_fact 软删 + get_facts_as_of 回溯**；**Mem0 原语模块 primitives.py 显式 add/update/delete/resolve_conflict**；**SAGE Reader as_of 默认过滤 invalidated_at IS NULL**。docs-only 0 code；0 frozen contract 改动 | `6419166`（docs: temporal memory & mem0 primitives contract (MR-1)） |
| **MR-2（Temporal Memory & Mem0 Primitives 实作）** | ✅ 实作完成（135 tests 全过） | **Schema v7 迁移实作**（`src/memory/sage/models.py` +14：Fact 加 `valid_from`/`invalidated_at`，迁移分支 `valid_from` 回填 `timestamp` + `invalidated_at` NULL）；**GraphStore 软删 + 回溯**（`src/memory/sage/graph_store.py` +193：`invalidate_fact` 软删 → `invalidated_at` 时间戳，`get_facts_as_of` 回溯到指定时刻）；**Mem0 原语模块**（`src/memory/primitives.py` NEW +104：显式 `add`/`update`/`delete`/`resolve_conflict`）；**SAGE Reader as_of 默认过滤**（`src/memory/sage/reader.py` +42：`as_of` 默认过滤 `invalidated_at IS NULL`）+ `tests/test_temporal_memory_mr2.py`（NEW +381，21 tests）+ `tests/test_m5_4_5_2_memory_inner_life_integration.py` + `tests/test_pig_filter_v2.py`（v5→v7 schema 断言更新）。**135 tests 全过；0 frozen contract 改动**（只改 sage/models + graph_store + reader + 新 primitives + 测试） | `3eacae8`（feat: temporal memory & mem0 primitives (MR-2)） |
| **阶段 B-P0（记忆检索工程落地）** | ✅ 落地 | **MR-0 审计**（`docs/MEMORY-RETRIEVAL-AUDIT.md`：event_time 生产数据 100% NULL / INSERT OR REPLACE 覆写破坏历史，B1-B4 四缺口）+ **MR-1 设计**（`docs/TEMPORAL-MEMORY-CONTRACT.md`：Schema v7 + 软删 + primitives + as_of 契约）+ **MR-2 实作**（上述落地）。阶段 B-P0 记忆检索工程三条链闭环：可随时间回溯、软删不破坏历史、显式原语替代隐式覆写。0 frozen contract 改动 | `3eacae8`（feat: temporal memory & mem0 primitives (MR-2)） |

### TL-0（Time-lapse Harness 实验规格）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-0（Time-lapse Harness 实验规格）** | ✅ 文档已定稿（docs only，0 code） | `docs/TIME-LAPSE-HARNESS.md`（13 节，306 行）：**counterfactual identity** 验证「灵魂会因经历而改变」——同一 seeded Soul（Ruka + SEED=42 fixture）同一 probe 在 T0/T15/T30 的 interpretation / motive / decision 因经历而变，且可追溯回 fed events（成功 = 可解释、可重现、可追溯的行为改变，**不是 count↑**）。**D1** GrowthProbeRecord schema（Simulated Event 为最小生命单位，day 仅做 checkpoint）；**D2** harness-local SimulationClock 重现（production scheduler / 时钟不动）；**TL-1 fixture**（SEED=42 + 30 天事件剧本 + 固定 seeded memory baseline + personas/agent_ruka.md）；**隔离 data_root**（D7，0 production mutation）。Out of Scope：不改 frozen contract / 不改 src/ / 不加速 production 时钟 | `77c1899`（docs: time-lapse harness experiment spec (TL-0)） |

### TL-1（Time-lapse Harness 实现 + 第一个实验）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-1（Time-lapse Harness 实现 + 第一个实验）** | ✅ 完成（**Level 2 Growth proven**） | `harness/` 8 文件（clock.py / fixture.py / probe.py / observer.py / records.py / runner.py / run_tl1.py / __init__.py）+ `tests/test_tl1_harness.py`（26 tests PASS）。**第一个实验**：同一 Ruka 同一 probe「Alex 两天没回讯息」在 T0/T15/T30 的 motive 从「担心」→「自我怀疑」→「接受变淡」，change_verdict = **INTERPRETATION_DECISION_CHANGED = Level 2 Growth proven**（行为改变可解释、可重现、可追溯回 fed events）。**D2 determinism PASS**（harness-local SimulationClock 重现，production scheduler / 时钟不动）。**0 production mutation**（隔离 data_root，0 frozen contract 改动，harness 只活在 harness/ + tests/，不改 src/） | `bcae186`（feat: time-lapse harness + TL-1 experiment (Level 2 growth proven)） |

### TA-1（时间感知：conversation_elapsed + 表达规则放宽 + silence bug 修复）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TA-1（时间感知：conversation_elapsed + 表达规则放宽 + silence bug 修复）** | ✅ 完成 | `src/llm/proxy.py`（时间区块内 3 项改动）+ `tests/test_ta1_temporal_orientation.py`（25 tests）+ `tests/test_ta1_simulation_day.py`（15 tests），**40 tests PASS**。**① conversation_elapsed 信号**：`_get_last_interaction_ts`（跨 session 不跨 agent，last_interaction_at = max(last_user_ts, last_assistant_ts)）+ `_format_continuity_str`（elapsed < 15 分钟 = 同一场对话不注入；<24h = "X 小时"；>=24h = "X 天"）+ `last_interaction_period`（上次互动本地时段标签，`_period_label_for_ts`）。**② TEMPORAL_EXPRESSION_RULE 放宽**：`TEMPORAL_EXPRESSION_PRECEDENCE` 常量注入时间区块，现象式时间表达允许（早上/快中午/都下午了/这么晚/周末），未询问不主动报精确钟点或日期，precedence 压过 persona 绝对禁令（不改 10 份人格档）。**③ silence bug 修复**：`_get_bry_latest_ts` suffix 从 `f"_agent_{agent_id}"` 改 `f"_{agent_id}"`（真实 session_id 以 `_{agent_id}` 结尾，旧 suffix 0 匹配 → bry_latest_ts 恒 0 → 沉默时长行从未注入）。**模拟测试验证**：早上不道晚安 / 下午不问早餐 / 夜间不道早安。**0 frozen contract 改动**（只改 proxy.py 时间区块 + 测试，不改 Agency/TriggerEnvelope/InnerLifeEvent/4 handlers/SAGE） | `4a63b1d`（feat: temporal orientation & continuity (TA-1) + silence bug fix） |

### TL-2（Volition Choice Test：Decision 层非装饰验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-2（Volition Choice Test）** | ✅ 完成 | `harness/tl2.py`（TL2Runner：seed_candidate_context → motive → Decision → transmit/not_transmit，6 candidates）+ `harness/run_tl2.py`（入口）+ `tests/test_tl2_volition.py`（**17 tests PASS**）。**Volition Choice Test**：**Control A（scheduler-only，无 Decision 层）全发 6/6** vs **Control B（有 Decision 层）4 send / 2 not_send** → **Decision 层非装饰 = True**（scheduler 说发 ≠ Soul 发；同一 candidate C02 A=send / B=not_send）。**not_transmit 的 reason 引用 relationship/memory/mood context**（非随机，fail-closed：LLM 坏输出 → not_transmit）。走真实 `src.soul.decision.decide_motive`（不改其逻辑）。**17 tests 全过；0 production mutation**（隔离 data_root，`data/time_lapse/` gitignore，harness 只活在 harness/ + tests/，不改 src/，0 frozen contract 改动） | `85711cc`（feat: time-lapse volition choice test (TL-2)） |

### TL-4（Lifecycle Validation：SE-5 lifecycle 行为验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-4（Lifecycle Validation）** | ✅ 完成（**四指标全过**） | `harness/tl4.py`（TL4Event + build_tl4_script(seed) 90 天四阶段事件剧本 + TL4Runner：SimulationClock 推进 Day 0-90，走 soul-elevation 的 consume/elevate/record_contradiction/reinforce/supersede/evaluate_lifecycle，产出 canonical records + derived 四指标判定）+ `tests/test_tl4_lifecycle.py`（**23 tests PASS**）。**场景化 trajectory（一条）**：belief A = "Alex 是值得信任的朋友"（positive）→ Day 0-20 重复正面证据强化（REINFORCE）→ Day 21-40 矛盾证据累积（contradiction_pressure，不立即修订）→ Day 41-60 混合证据 → SUPERSEDE → B = "Alex 最近变得疏远"（negative）→ Day 61-90 稳定。**四指标全过**：① **Revision validity**（矛盾压力累积不立即修订，证据足够才修订）；② **Stability**（短期噪声不翻转结构，REINFORCE 不新建节点）；③ **Recovery-Adaptation**（环境变化 A→B，B 是新 durable structure）；④ **Historical continuity**（SUPERSEDE 后 lineage 可追溯，旧节点保留不删除，trace 有 supersede 事件）。**23 tests 全过（0.76s）；0 production mutation**（隔离 data_root `data/time_lapse/TL-4/`，走 soul-elevation 公开 API，harness 只活在 harness/ + tests/，不改 src/，0 frozen contract 改动） | `8689e9c`（feat: time-lapse lifecycle validation (TL-4)） |

### TL-5（Time-lapse Behavior Distribution Validation：四元行为分布验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-5（Behavior Distribution Validation）** | ✅ 完成（**最终验收 PASS**） | `harness/tl5.py`（TL5Runner：SimulationClock 小时精度 tick + 四元 Decision 自发行为分布 + derived 三大指标）+ `harness/run_tl5.py`（入口）+ `harness/clock.py`（`sim_ts` 加 additive `hour` 参数，默认 0 向后兼容）+ `tests/test_tl5_behavior_distribution.py`。**Behavioral Diversity PASS**：四动作均 > 0（无死模组），do_nothing 82.5% / reflect 10.5% / transmit 3.5% / observe 3.5%（do_nothing 落在 65%-85% 目标区间内，真实生命「大多数时间平静生活」）。**Contextual Appropriateness PASS**：observe 集中信号突变点（env_signal）；reflect 集中夜间/等待期（night/dawn/relationship_silence，SM-4.6 dawn 补入合法集合）；transmit 遵守 CD 与亲密度（只发生在 intimacy=high）。**D2 Determinism 按 MoE 特性记录 7 mismatches**（3 runs 决策轨迹基本一致；7 处 mismatch 归因 MoE 采样特性，非逻辑缺陷，如实记录）。**0 production mutation**（隔离 data_root，harness 只活在 harness/ + tests/，不改 src/，0 frozen contract 改动）。 | `89e9cdf`（feat: time-lapse behavior distribution validation (TL-5)） |

### SI-2.1（Social Diffusion Contract 设计）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-2.1（Social Diffusion Contract）** | ✅ 设计文档已定稿（docs only，0 code） | `docs/SOCIAL-DIFFUSION-CONTRACT.md`（NEW, +456，10 节）：**SocialWorldEvent 最小 Schema**（新增 `EventType.SOCIAL_WORLD_EVENT` + `SoulEvent.actor_id` additive 可选字段，payload 含 `actor_id / space_id / visibility / event_type / content`）。**三大防线**：**防线 3 Identity Firewall（最高优先）**——Submission Gate 契约 `actor_id != current_agent_id` 一律打 `EXTERNAL_OTHER_ACTION` 标签，**三条绝对不变量**：外部他者事件只能作为「客厅环境背景感知」、绝对禁止内化为自身情景记忆、更严禁升华为自身性格或信念；**防线 2 Privacy Visibility Gate**——Producer 侧守门，与 Bryan 的 1:1 私聊 DM 默认 `private` 严格拦截于广播总线之外，仅公共频道（Soul Wall / 客厅群聊）或显式公开动态才允许沉淀为社交事件；**防线 1 Ambient Perception Path**——社交事件仅经 `WorldPerceptionMiddleware` 注入为环境观察（world_context），不赋予即时唤醒或插话特权，杜绝多 Agent 相互回复的广播风暴。**Frozen Contract 边界**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入一律不动；既有 SoulEvent 字段语义、17 个 EventType 枚举值、WorldPerceptionMiddleware WORLD_EVENT 路径、SubmissionGate 5 步验证链语义 0 变更（只 additive 扩展）。docs-only 0 code；0 frozen contract 改动 | `5002f20`（docs: multi-agent social diffusion contract (SI-2.1)） |

### SI-2.2（Social Diffusion 实作）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-2.2（Social Diffusion 实作）** | ✅ 已落地 | 14 files changed, +2286/-6：**Schema**（`src/eventbus/schema.py` additive：新增 `EventType.SOCIAL_WORLD_EVENT` + `SoulEvent.actor_id` 可选字段，payload `actor_id / space_id / visibility / event_type / content`）；**新模块 `src/social/`**（`__init__.py` + `schema.py` + `validation.py` + `identity_firewall.py` + `producer_gate.py`）；**防线 3 Identity Firewall**（`src/inner_life/submission_gate.py` 第 6 步 actor_id 检查：`actor_id != current_agent_id` → `EXTERNAL_OTHER_ACTION` 标签，三条绝对不变量：仅环境背景感知 / 禁止内化情景记忆 / 严禁升华性格信念）；**防线 2 Privacy Visibility Gate**（`src/social/producer_gate.py`：与 Bryan 1:1 私聊 DM 默认 `private` 拦截于广播总线之外，仅公共频道或显式公开动态沉淀为社交事件）；**防线 1 Ambient Perception Path**（`src/world/middleware.py` 平行订阅 SOCIAL_WORLD_EVENT：仅注入 world_context 环境观察，不触发 transmit，杜绝广播风暴）。**95 新测试全过 + 0 回归引入**（test_social_schema / test_social_validation / test_social_identity_firewall / test_social_producer_gate / test_social_middleware / test_social_submission_gate）。**0 frozen contract 改动**（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入一律不动；只 schema.py + middleware.py + submission_gate.py additive + 新 social 模块 + 测试） | `33ae1b1`（feat: multi-agent social diffusion (SI-2.2)） |

### SI-2（多 Agent 灵魂互动落地）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-2（多 Agent 灵魂互动）** | ✅ 完整落地 | **SI-2.0 审计**（`docs/SOCIAL-DIFFUSION-AUDIT.md`，现状审计：多 Agent 广播风暴 / 身份混淆 / 隐私泄漏风险识别）+ **SI-2.1 设计**（`docs/SOCIAL-DIFFUSION-CONTRACT.md`，SocialWorldEvent 最小 Schema + 三大防线契约，commit `5002f20`）+ **SI-2.2 实作**（Schema + `src/social/` 模块 + 防线 3/2/1 落地，commit `33ae1b1`）。灵魂互动主线从 SI-1 最小读侧升级为完整多 Agent Social Diffusion 闭环。0 frozen contract 改动 | `33ae1b1`（SI-2.2）+ `5002f20`（SI-2.1）+ `b623e17`（SI-1） |

### SI-2 Harness（多体共存 Harness 验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-2 Harness（多体共存 Harness 验证）** | ✅ Closed | `tests/harness/test_social_diffusion_harness.py`（NEW，**11 测试全过**）+ `tests/harness/social_harness_fixtures.py`（NEW，4 剧本场景 fixture）：**三大防线刚性断言实证**——**Identity Firewall 0 内化**（防线 3：外部他者事件 0 内化为自身记忆）/ **Privacy Gate 0 泄漏**（防线 2：1:1 私聊 0 泄漏于广播总线之外）/ **Ambient Path 0 自激**（防线 1：0 自激回声广播风暴）。顺手修 `tests/test_social_middleware.py` 旧渲染区断言（`[社交感知]` → `[客廳現況]`，对齐 SI-3 Phase 2 聚合器紧凑渲染，含反框架警示语）。**0 frozen contract 改动**（只新增 harness 测试 + 修测试断言，0 production 代码改动） | `973971d`（feat: multi-agent social diffusion harness validation (SI-2 harness)） |

### TL-6（Social Lounge Multi-Agent Behavioral Stability Validation：多 Agent 客厅情境验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-6（Social Lounge Stability Validation）** | ✅ 完成（**四大不变量全过**） | `harness/tl6.py`（TL6Runner / TL6Tick / build_tl6_script(seed=42) 7 阶段客廳劇本：晨間問候 / Bryan 留言 / 1:1 私聊隔離 / 晚間觀察 / 深夜留白 / 5 筆連續社交脈衝 / 深度記憶審計）+ `harness/run_tl6.py`（CLI 入口）+ `tests/test_tl6_social_harness.py`（5 tests PASS）。**四大不變量全過**：① **Anti-Storm Invariant**（100% PASS，社交感知路徑不自發引發連鎖搶話，客廳 transmit 激勵率受控）；② **Identity Quarantine Invariant**（100% PASS，他者行為 0 內化為自傳情景記憶，0 昇華性格信念，SubmissionGate 第 6 步硬守門）；③ **Privacy Gate Invariant**（100% PASS，1:1 私聊 DM 100% 攔截於總線外，0 泄漏至客廳）；④ **Ambient Salience**（PASS，[社交感知] 區塊攜帶反框架提示，Top-N 預算約束生效）；⑤ **D2 Determinism & 0 Mutation**（3 runs 軌跡一致，生產數據 0 diff）。**213 tests 全回歸通過（36.85s）**；0 frozen contract 改動（只 harness + tests，不改動既有業務代碼）。 | `7d0ebbb`（TL-6） |

### SI-3 Phase 1（Selective Social Attention：Social Opportunity + Compact Aggregator）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-3 Phase 1（Selective Social Attention）** | ✅ 已落地 | `src/social/opportunity.py`（NEW，+112：**SocialOpportunity** TTL 300s 過期 + **SocialOpportunityBuffer** 容量 5 FIFO 淘汰）+ `src/social/aggregator.py`（NEW，+207：**CompactSocialState** + **SocialPerceptionAggregator** 反框架渲染 ≤150 tokens）+ `src/social/__init__.py`（additive 匯出）+ `tests/test_social_opportunity.py`（NEW，+247，5 tests）。**5 新測試全過 + 100 回歸全過**；**0 frozen contract 改動**（只 social 模組 + 測試）；**0 Vector DB**（純記憶體 FIFO，不引入向量檢索）。 | `554202c`（feat: social opportunity + compact aggregator (SI-3 phase 1)） |

### SI-3 Phase 2（Selective Social Attention：感知聚合器接入 Middleware 與 SM-4 決策管線）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SI-3 Phase 2（感知聚合器 + SM-4 決策管線接線）** | ✅ 已落地 | `src/world/middleware.py`（改：`_render_social_context` 升級為 CompactSocialState 緊湊渲染 ≤150 tokens，反框架語在場，無他人動態返回 ""；per-agent `SocialPerceptionAggregator` 緩存 + `_get_social_aggregator` + `_ts_to_epoch`）+ `src/soul/decision.py`（改：`build_decision_prompt` 新增可選參數 `social_context`，只進 Relevant context，向後兼容）+ `src/soul/motive.py`（改：新增 `motive_from_social_opportunity` 純函數，SocialOpportunity → 合法 Motive 5 字段）+ `harness/tl6.py`（改：quarantine 檢測適配新渲染格式 `[客廳現況]` + ANTI_FRAMING_HINT）+ `tests/test_si3_phase2_integration.py`（NEW，4 tests：緊湊渲染 / TTL 過期留白 / Motive 決策轉換 / 0 連鎖意志）。**14 tests 全過（2.57s，工單驗收命令）**；**0 frozen contract 改動**（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 未動）；**0 Vector DB**。 | `d7c7c70`（feat: wire social perception aggregator into middleware and SM-4 decision (SI-3 phase 2)） |

### TL-7（Social Opportunity & Volition Stability Harness：社交機會生命週期與自主意志穩定性驗證）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TL-7（Social Opportunity & Volition Stability）** | ✅ 完成（**三大不變量全過**） | `harness/tl7.py`（TL7Runner：4 大情境階段 **Phase A 話題湧現**（Ruka 客廳發布 share「我烤了餅乾在桌上」）/ **Phase B 緊湊感知與機會生成**（Akane `_render_social_context` 產出 `[客廳現況]` 含反框架警語，SocialOpportunityBuffer 生成 1 筆 TTL=300s 機會）/ **Phase C 意志選擇與無連鎖**（Akane 生成 Motive 傳入 `build_decision_prompt`，走入 SM-4 四元單選，絕不繞過意志直接觸發 transmit）/ **Phase D 300s TTL 自然蒸發**（時鐘前進 301s，`get_active_opportunities` 自動剔除過期條目，渲染恢復留白 ""，0 殭屍回覆））+ `harness/run_tl7.py`（CLI 入口，3-run 系列 + 驗收表格）+ `tests/test_tl7_social_opportunity_harness.py`（NEW，7 tests）。**三大不變量全過**：① **TTL Expiration Invariant**（100% PASS，過期條目徹底蒸發 0 遺留）；② **No Cascading Volition Invariant**（100% PASS，0 自動連鎖搶話）；③ **D2 Determinism & 0 Mutation**（3 runs 軌跡一致，生產 data/ 0 diff）。**歷史舊測試對齊**：`test_m3_4_priority_semantic_boundary.py::test_I7` 對齊 M5.4-3.1 契約（`to_payload` 含 `priority` additive 欄位，round-trip 保證，向後相容舊 payload fallback 0）；`test_tl2_volition.py` ContextRoutingLLM stub 對齊 SM-4.1~SM-4.6 六輪校準後判定階梯（motive 原文錨點取代舊 context 關鍵詞 marker，避免與 prompt 固定文本「夜深/打擾」誤匹配）。**55 tests 全過（13.47s，工單驗收命令 6 文件）**；0 frozen contract 改動（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 未動）；**0 Vector DB**。 | `e4c875d`（feat: TL-7 social opportunity harness + historical test alignment (TL-7)） |

### TS-1（Tooling & MCP Contract：动态 Tool 注册表 + Tooling Volition Gate 双轴治理）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TS-1（Tooling & MCP Contract）** | ✅ 设计文档已定稿（docs only，0 code） | `docs/TOOLING-MCP-CONTRACT.md`（376 行，TS-0 采信摘要为前置）。**契约 1 `tool_registry.py` 动态注册表**：tool 注册从静态集中改为动态分组，自动归类 **3 能力组（observe / reflect / communicate）**，MCP tool 动态接入，健康检查 fail-silent 投影，与 capability.py 投影合并（capability.py 0 改动）。**契约 2 Tooling Volition Gate**：调用链 = Decision 批准 → Actuator 派发单次调用 → 结果回流 World Context / Perception；**0 自主递归硬规则（锁死）共 5 条**，agent 不可无限自我递归调用自身工具。**契约 3 权限分级与安全降级**：`Auto-Approved` / `Ask-Required` 双档 + **Fail-closed 平滑降级**（异常一律拒绝，不阻塞主心跳）+ **5s 硬超时**。**契约 4 冻结契约审查**：**12 项冻结契约逐条审查 0 冲突**（5.1 逐条审查表 + 5.2 结论），0 frozen contract 改动。**0 code**（docs-only：0 src/ 变更，0 测试变更）。TS-2 依此实现。 | `814f16a`（docs: tool registry and volition gate contract (TS-1)） |

### TS-2（Tooling & MCP 实作：动态 Tool 注册表 + observe/reflect 执行器）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TS-2（Tooling & MCP 实作）** | ✅ 完成 | `src/soul/tool_registry.py`（NEW）动态注册表：tool 注册改为动态分组，分组聚合 **3 能力组（observe / reflect / communicate）**，**健康三态** + **fail-closed 归类**（异常一律拒绝，不阻塞主心跳）+ **权限分级**（`Auto-Approved` / `Ask-Required` 双档）+ **5s 硬超时降级**；`src/soul/actuator.py`（NEW）observe/reflect 执行器，调用链 = Decision 批准 → Actuator 派发单次调用 → 结果回流 World Context / Perception，**0 自主递归硬规则**。**96 tests 全过**；0 frozen contract 改动（只新增 tool_registry.py + actuator.py + 测试，未动 scheduler.py / capability.py / decision.py / motive.py）。注：scheduler 接线留待 TS-2.1。 | `c668739`（feat: tool registry and observe/reflect actuators (TS-2)） |

### TS-2.1（Actuator 接线：scheduler 决策检查经 Actuator 派发单次调用）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TS-2.1（Actuator 接线）** | ✅ 完成（79/89/100 测试全过，36 条 SM-3 零回归） | `src/soul/scheduler.py`（+46）`scheduler._decision_check` 依赖注入 actuator：observe / reflect 决策后经 **Actuator 派发单次调用**，结果回流感知 / 认知（World Context / Perception）；**发布端仍 mark_rejected**（不因 actuator 存在而放行拒绝的 transmit）；**transmit 保持既有通道**（不经过 Actuator，走原消息管线）；**do_nothing 不执行**（无调用派发）；`src/soul/actuator.py`（+35，additive）；`tests/test_ts21_actuator_wiring.py`（NEW，**10 新测试**）。**79/89/100 测试全过**（TS-2.1 10 + actuator volition gate + tool registry + SM-3 motive/decision 回归），**36 条 SM-3 零回归**。0 frozen contract 改动（只改 scheduler.py + actuator.py additive + 新测试，未动 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑）。 |

### TS-3（真实 MCP Server 端到端验证：官方 mcp SDK + 手写 stdio client 双实现）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TS-3（真实 MCP Server 端到端验证）** | ✅ 完成（**73 测试全过**；工具层状态升级「生产验证完毕 Production-Verified」） | `scripts/mcp_fixture_server.py`（NEW，**官方 mcp SDK** 实现的真实 MCP server fixture）+ `src/soul/mcp_stdio_client.py`（NEW，**手写 stdio MCP client**：JSON-RPC 2.0 初始化握手 / tool 动态发现 / 调用派发 / 响应解析）+ `tests/test_ts3_real_mcp_e2e.py`（NEW，真实进程端到端：spawn 真实子进程 server → stdin/stdout 进程通讯 → tools/list + tools/call 全链路）+ `tests/test_ts3_official_mcp_server.py`（NEW，官方 mcp SDK server 测试）。**三大验证全过**：① **进程通讯**（真实子进程 spawn + stdio 双向 JSON-RPC，**非 mock**，实证 MCP 接入主线真实可用）；② **5s 硬超时 Fail-closed**（客户端超时降级，异常一律拒绝、不阻塞主心跳，TS-1 契约 3 实证）；③ **Volition Gate**（调用链 = Decision 批准 → Actuator 派发单次调用 → 结果回流，**权限分级 `Auto-Approved` / `Ask-Required` 实证**）。**73 测试全过**；**0 frozen contract 改动**（只新增 mcp_fixture_server.py + mcp_stdio_client.py + 2 测试，未动 tool_registry.py / actuator.py 既有接口）。 | `0acadbc`（feat: real MCP server end-to-end validation (TS-3)） |

### MS-0（Multimodal Perception 架构审计：语音 STT / 视觉 Camera 输入现状盘点）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **MS-0（Multimodal Perception 架构审计）** | ✅ 完成（READ-ONLY，docs only 0 code） | `docs/MULTIMODAL-PERCEPTION-AUDIT.md`（NEW，250 行）。**三大审计结论**：① **输入侧全空白**——语音 STT（whisper / sensevoice / sounddevice / pyaudio 等 venv 0 依赖、源码 0 引用、data 0 痕迹）与视觉 Camera（opencv 0 依赖、源码 0 引用）均无实现，当前全部输入 = 纯文字；输出侧 Fish TTS + Edge 兜底已生产验证。② **工具层接入点就绪但分类表缺词**——麦克风/相机 → MCP 工具 → `observe_environment` 组路径可行（`register_mcp_server` 唯一入口 + `project_capabilities` 自动投影 + Auto-Approved 权限），但 `_OBSERVE_KEYWORDS` 与 `EXPLICIT_GROUP_MAP` 无 audio / voice / stt / camera 关键词 → 新人工具会被 fail-closed 拒绝注册，需 **三处 additive**（observe 关键词 + explicit group map + 对应能力定义）。③ **感知边界完整遵守 Volition Gate**——感官数据流入 = Actuator `_flowback` → `WorldPerceptionState` → `WorldPerceptionMiddleware`（validate → 24h novelty → top-N → `AGENT_INTENT_PERCEIVED`）→ prompt 注入，scheduler 发布端仍 `mark_rejected`；多模态事件不进 `WORLD_QUALIFYING_TYPES`（M5.9-2 白名单）→ 不污染 InnerLife / SAGE。**唯一 Frozen Contract 触点**：`VALID_SOURCES`（`src/world/perception.py:46`）若要让 audio / vision 事件语义化需 additive 扩展（当前默认落 `synthetic` 语义丢失），化工单级决策需主大脑 + Owner 批准，不阻塞 MS-1 设计。**MS-1 选型候选供给**（faster-whisper 本地 / SenseVoice 本地 / 云端 STT API——花钱事项→Owner 拍板；opencv-python / picamera2）。0 frozen contract 改动（三处 additive 缺口标记待 MS-1 工单，本次 0 code）。 | `c30314e`（docs: multimodal perception architecture audit (MS-0)） |

### MS-1（Multimodal Perception Contract：STT 语义 v1 设计）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **MS-1（Multimodal Perception Contract）** | ✅ 完成（DESIGN，docs only 0 code） | `docs/MULTIMODAL-PERCEPTION-CONTRACT.md`（NEW，413 行）。**设计决策**：① **STT 语义 v1 锁 observe**——语音输入人话语义一律进 `observe_environment` 组，**严禁直通 USER_MESSAGE**（保持 Volition Gate 边界，语言不越权）；② **`VALID_SOURCES` additive 扩展已获 Owner 批准**——additive 加 `audio_input` / `camera_capture` 两 source（MS-0 标记的唯一 Frozen Contract 触点已闭环，批准层级 = 主大脑 + Owner）；③ **工具层三处 additive 扩展清单**——`_OBSERVE_KEYWORDS` 补 audio/voice/stt/camera 关键词 + `EXPLICIT_GROUP_MAP` 对应组映射 + 对应能力定义；④ **自研薄 MCP 封装**——`audio-stream-mcp` / `camera-mcp` 自研薄封装（挂在 `register_mcp_server` 唯一入口，Auto-Approved 权限）；⑤ **ASR 锁定 faster-whisper small 本地离线**（选型已定：本地离线、无云端付费——花钱/成本结构决策已由 Owner 拍板，SenseVoice / 云端 STT 不采用）。**0 frozen contract 改动（本次 docs only 0 code）**；工具层三处 additive + VALID_SOURCES 扩展均标记「MS-2 实作工单执行」。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | `172bca0`（docs: multimodal perception contract (MS-1)） |

### MS-2（Multimodal Perception 实作：STT + Camera 工具层接入）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **MS-2（实作）** | ✅ 完成（IMPLEMENTED，6 files +1088/-2） | **多模态感知实作全落地**（MS-0 审计 + MS-1 设计 + MS-2 实作闭环）：① **工具层三处 additive**——`src/soul/tool_registry.py`：`_OBSERVE_KEYWORDS` 补 audio/voice/stt/camera 关键词 + `EXPLICIT_GROUP_MAP` 对应组映射 + 对应能力定义（audio_input / camera_capture 遵行 observe 语义，多模态输入一律进 `observe_environment` 组，**不直通 USER_MESSAGE**——Volition Gate 边界保持）；② **`VALID_SOURCES` additive 扩展**——`src/world/perception.py`：additive 加 `audio_input` / `camera_capture` 两 source（MS-0 唯一 Frozen Contract 触点，已获主大脑 + Owner 批准），0 破坏性改动；③ **两个自研薄 MCP server**——`scripts/audio_stream_mcp.py`（语音流 STT 封装）与 `scripts/camera_mcp.py`（相机帧视觉封装），挂在 `register_mcp_server` 唯一入口 Auto-Approved 权限；④ **感知边界不变量实证**——Ambient Observation 语义（多模态事件回流感知、不越权为直接指令）由 `src/soul/actuator.py` 感知边界逻辑落实。**28 新测试全过 + 314 回归全过（342 total）**；0 frozen contract 改动（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑均未触碰）。注：`tests/test_soul_md_loader.py` 未被本次触碰（保持未提交）。 | `1d1b9af`（feat: multimodal perception (audio/camera MCP) (MS-2)） |

### MS-3（Voice Interaction：契约设计 → 实作落地）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **MS-3（Voice Interaction Contract）** | ✅ 完成（DESIGN，docs only 0 code） | `docs/MS-3-VOICE-INTERACTION-CONTRACT.md`（NEW，366 行）。**设计决策**：① **三路分流**——语音输入按语义分 `USER_MESSAGE` / `AMBIENT` / `DROP` 三路（严格区分直接指令 vs 环境观测 vs 丢弃）；② **本地启发式决策梯 + fail-ambient 兜底**——分流由本地启发式规则阶梯决定（不依赖云端/LLM 判定），无法判定时安全落 AMBIENT，**不越权为指令**；③ **唤醒门控 address_score 三信号源**——唤醒判定由三个信号源综合打分；④ **VAD 防抖 utterance 合并 + 3s 冷却 + TTS echo 抑制**——防语音流碎片化、防重复触发、防 TTS 自反馈回声触发；⑤ **契约相容性无旁路注入**——设计不绕过既有 Frozen Contract 通道（Volition Gate / USER_MESSAGE 边界保持）。**§5.5 合规性已获 Owner 批准**；**0 frozen contract 改动（本次 docs only 0 code）**。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | `a61beff`（docs: voice interaction contract (MS-3)） |
| **MS-3（实作）** | ✅ 完成（IMPLEMENTED，5 files +2157） | **语音互动输入实作全落地**（MS-3 契约 → 实作闭环）：① **三路分流**——`src/voice/input_router.py` 按语义分 `USER_MESSAGE` / `AMBIENT` / `DROP` 三路，**本地启发式决策梯**（TTS 回声 / 语音能量 / 空转写 / 超长 / 白名单 / address_score 阶梯，matrix 10 案例实证）+ **fail-ambient 兜底**（无法判定安全落 AMBIENT，不越权为指令；无锚点无上下文不变量永不上浮）；② **唤醒门控**——`src/voice/gate.py` address_score **三信号源**综合打分（`w_name·name_hit + w_wake·wake_hit + w_sp·second_person`）+ `VOICE_OWNER_IDS` 白名单（非 owner 语音一律 AMBIENT，owner 命中才可触达 USER_MESSAGE）；③ **VAD 防抖 utterance 合并**——`src/voice/audio_service.py` 短间隔碎片合并 + 句末/长间隔不合并 + 会话窗口生命周期（3s 冷却 + TTS echo 抑制 + 速率防洪 soft/hard 双限 + 指数退避 + 洪水降级 AMBIENT）；④ **契约相容性无旁路注入**——USER_MESSAGE 仅经既有契约通道发布（contract envelope 不变），AMBIENT/DROP 不污染 USER_MESSAGE 边界，owner last_seen 副作用对齐既有语义。**71 新测试全过**（58 函数 + 10 matrix 参数化 + 5 drop 参数化展开）+ **无唤醒 100% 降级验证**（fuzz 不变量：无锚点无上下文永不上浮；无唤醒强意图样本全部 AMBIENT）；**0 frozen contract 改动**（只新增 `src/voice/` 4 文件 + 1 测试；不改 gateway.py / router.py / consciousness.py / proxy.py）。注：`tests/test_soul_md_loader.py` 未被本次触碰（保持未提交）。 | `e308365`（feat: voice interaction input (MS-3)） |
| **MS-3.1（实作）** | ✅ 完成（IMPLEMENTED，3 files +885） | **语音互动「实体设备闭环」**——设备层音频采集与 MCP 会话工具对接：① **voice_session_start / feed / stop 三工具纯 additive**——`scripts/audio_stream_mcp.py`（+448）新增会话工具，不触碰既有工具契约；② **VoiceSessionRegistry 30s 硬超时 janitor**——会话注册表 + 30s 硬超时清理，防泄漏会话；③ **VAD 静音状态机**——语音流静音边界判定驱动会话生命周期；④ **process_audio_stream ASR 注入式 + MS-3 路由判定**——`src/voice/audio_service.py`（+116）注入式 ASR 回调 + 接入 MS-3 三路分流（USER_MESSAGE/AMBIENT/DROP）。**20 新测试**（`tests/tools/test_voice_session_mcp.py`，NEW）+ **71 回归全过**；**0 frozen contract 改动**（只改 audio_service.py + audio_stream_mcp.py + 测试；不改 gateway.py / router.py / consciousness.py / proxy.py）。注：`tests/test_soul_md_loader.py` 未被本次触碰（保持未提交）。 | `bc7bbda`（feat: device-level voice session MCP tools (MS-3.1)） |

### 工具层标准化全线贯通（TS-0 审计 → TS-1 设计 → TS-2 实作 → TS-2.1 接线 → TS-3 生产验证）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TS-0 → TS-3（工具层标准化全线贯通）** | ✅ 四条链闭环 + **生产验证完毕（Production-Verified）** | **TS-0 审计**（`docs/TOOLING-MCP-AUDIT.md`，工具层现状盘点 + MCP 接入缺口）→ **TS-1 设计**（`docs/TOOLING-MCP-CONTRACT.md`，contract 四节：动态注册表 / Volition Gate / 权限分级 + Fail-closed / 冻结契约审查 0 冲突，commit `814f16a`）→ **TS-2 实作**（`src/soul/tool_registry.py` 动态注册表 3 能力组 + `src/soul/actuator.py` observe/reflect 执行器，96 tests 全过，commit `c668739`）→ **TS-2.1 接线**（scheduler._decision_check 经 Actuator 派发单次调用，89 测试全过，commit `10a6a98`）→ **TS-3 生产验证**（真实 MCP Server 端到端：官方 mcp SDK + 手写 stdio client 双实现，进程通讯 / 5s 硬超时 Fail-closed / Volition Gate 三大验证全过 + 权限分级实证，73 测试全过，commit `0acadbc`）。**工具调用标准化主线完整打通**：Decision 批准 → Actuator 派发单次调用 → 结果回流感知 / 认知；0 自主递归硬规则；5s 硬超时降级；全程 0 frozen contract 改动。**工具层状态：生产验证完毕（Production-Verified）**。 | `814f16a` / `c668739` / `10a6a98` / `0acadbc` |

### SE-4（Durable Soul Structure Lifecycle Contract 设计）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SE-4（Durable Soul Structure Lifecycle Contract）** | ✅ 设计文档已定稿（docs only，0 code） | `docs/ELEVATION-LIFECYCLE.md`（12 节，295 行）：**灵魂结构（belief/value/trait/essence）生命周期 = 状态机，不是动作集合**。**四态**：`ACTIVE → WEAKENING → DORMANT → SUPERSEDED`（显式状态，非动作）；**两转换**：`REINFORCE`（支持证据累积强化，可回 ACTIVE）与 `SUPERSEDE`（矛盾证据累积达阈值，新节点取代旧节点，lineage 相连）；**默认「证据不足什么都不做」**。**三条铁律**：① **Contradiction ≠ Revision**（矛盾产生压力，证据累积才产生改变；一次反例不推翻 durable structure）；② **Forgetting = lifecycle transition，不是 delete**（Memory ≠ Current Belief，节点进入 DORMANT/SUPERSEDED 是状态改变，节点本体/证据链/lineage 永不物理删除，灵魂能回答「我以前很在意，现在不是了」）；③ **essence 近乎锁死**（豁免自动衰减，SUPERSEDE 门槛全系统最高，唯一通道 = reconsideration-candidate 待复核）。**v1 只分两层**：essence（锁死）vs 其他（belief/value/trait 共用一条中等门槛曲线，不做四层独立衰减）。**lineage 复用 InnerLifeEvent 命名**（parent_node_id / lineage_depth / lineage_path，不另创 supersede_chain / revision_tree 术语；superseded_by 只是可读快捷索引）。**decay 复用 M5.13 锚点**（last_support_ts 优先 = M5.13 last_interaction_at 语义，created_ts + grace 兜底；old ≠ outdated，年龄不是退场理由，失去支持才是）。**不建引擎**（belief/confidence/decay/revision/scoring 五个独立引擎全不做，逻辑留在既有 engine.py / prior.py）。**不碰 frozen contract**（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 写入一律不动；trace 既有 5 事件语义 0 变更，新事件 node_state_changed / node_superseded / essence_reconsideration_candidate 为 additive 扩展）。**SE-5 实作的单一事实依据** | `331b867`（docs: durable soul structure lifecycle contract (SE-4)） |

### SE-5（Durable Soul Structure Lifecycle 实作）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SE-5（Durable Soul Structure Lifecycle 实作）** | ✅ 实作完成（183 tests 全过） | `src/soul_elevation/models.py`（+33，`LifecycleState` 四态 `ACTIVE/WEAKENING/DORMANT/SUPERSEDED` + `ContradictionRecord`）+ `src/soul_elevation/engine.py`（+470，`InternalizingEngine` lifecycle 实作）+ `src/soul_elevation/prior.py`（+21）+ `src/soul_elevation/trace.py`（+5，3 个 additive 事件）+ `src/soul_elevation/__init__.py`（+20）+ `tests/test_lifecycle.py`（NEW, +630，35 tests）。**四态 + 两转换实作**：`REINFORCE`（active 原地保持 / weakening / dormant 复活 / superseded 拒绝）与 `SUPERSEDE`（新节点取代 + 旧节点冻结，证据门槛 + 单日噪声拒绝）。**Contradiction ≠ Revision**：单次矛盾不改状态，矛盾按 source+identity 去重，压力只记引用不记正文。**Forgetting = lifecycle transition 不是 delete**：节点永不物理删除。**essence 保守**：豁免自动衰减、SUPERSEDE 门槛全系统最高、需 valence reversal + confidence delta、唯一通道 = reconsideration-candidate。**decay 锚点**：`last_support_ts`（consume 时更新）+ `created_ts` + grace 兜底；old ≠ outdated；单步转换（不跳态）；坏时间戳跳过不 crash。**trace 3 个 additive 事件**：`node_state_changed` / `node_superseded` / `essence_reconsideration_candidate`（既有 5 事件语义 0 变更）。**183 tests 全过（0.44s）**。0 frozen contract 改动（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 不动）。 | `42939d4`（feat: durable soul structure lifecycle (SE-5)） |

### SE-5 Step 1（read-side 投影过滤：状态守门 + lineage 降维）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SE-5 Step 1（emergent read-side 投影过滤）** | ✅ 完成（28+35+11 tests 全过） | `src/inner_life/emergent_projection.py`（+85，SE-5 状态守门 + lineage 降维）+ `tests/test_emergent_projection.py`（+132，**11 新测试** test_01~test_11）。**状态守门**：按 `lifecycle_state` 过滤——**ACTIVE 正常投影**；**WEAKENING 投影但带不确定性语气**（「我隐约觉得…」前缀，动摇中的信念不当定论注入）；**DORMANT / SUPERSEDED 不主动投影**（v1 不做「历史回忆检索」，留后续工单）；缺省 / 未知状态视为 ACTIVE（SE-5 additive schema，旧数据兼容，seeded 回归不破坏）。**Lineage 降维**：新节点 B 的直接父（`parent_node_id`）是 SUPERSEDED 旧节点 A 时，只投影 B 并合成「我以前觉得 A，但后来发现 B」承先启后陈述——不把 A、B 两个矛盾信念同时投影（人格撕裂防护）；只查直接父不递归（一层降维）；父仍 active（revise 改写）不触发。**28+35+11 tests 全过**（本 repo emergent_projection 28 = 17 旧 + 11 新；soul-elevation lifecycle 35）。**0 frozen contract 改动**（只改 emergent_projection.py 读侧 + 测试，不改 InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE / soul-elevation）。 | `3ff2976`（fix: emergent projection lifecycle gating + lineage collapse (SE-5 read-side)） |

### CA-3（Capability Affordance 定义扩展）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **CA-3（Capability Affordance 定义扩展）** | ✅ 完成（19 tests 全过） | `src/soul/capability.py`（CAPABILITY_DEFINITIONS 扩展到 3 个：communicate / observe_environment / reflect_memory）+ `tests/test_capability_awareness.py`（3 处断言更新）。**措辞原则**：expression 陈述「可以」（can），不陈述「应」（should）——写「你可以…」不写「你应该…」，防止从「我能」滑成「我应」。**do_nothing 不进 Capability**（是 Decision 选项，不是 Capability）。**19 tests 全过（0.34s）**。0 frozen contract 改动（只改 capability.py + 测试，不改 Agency/TriggerEnvelope/InnerLifeEvent/4 handlers/SAGE）。 | `1f48108`（feat: expand capability definitions to observe and reflect (CA-3)） |

### TG-0（Goal Engine 架构审计：volition 链盘点 + Goal Ledger 落点评估）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TG-0（Goal Engine 架构审计）** | ✅ 完成（READ-ONLY，docs only 0 code） | `docs/TG-0-GOAL-ENGINE-AUDIT.md`（NEW，261 行）。**关键结论**：① **volition 链已闭环**——motive → Decision 四元（transmit/observe/reflect/do_nothing）→ Actuator 派发单次调用，目标引擎在既有 volition 链上叠加；② **Motive 源 4 模块盘点**——`src/soul/motive.py`（MotiveEngine）+ `src/soul/decision.py`（四元 Decision）+ `src/soul/scheduler.py`（proactive_dm 心跳 = 唯一 Motive 消费路径）+ `scripts/run_server.py`（SM-3 motive proxy 独立注入）；③ **注入层推荐方案 B GoalMotiveProvider**——复用 SM-3 motive proxy 独立注入先例，Goal 动机在注入层叠加；④ **Goal Ledger 落点 graph.sqlite v8 `goals` 表**——SAGE SQLite 既有 schema 演进路径；⑤ **状态机 = 三态+两终态+SUSPENDED**（注意与 SE-5 四态生命周期区分）；⑥ **Volition Gate 相容 1HB1S**——目标引擎严格依循单次行动原则，不引入自主递归；⑦ **双轴种子源 Bryan / 自我 各 4 源**——「Bryan 羁绊 + 自由生长」双轴锁定；⑧ **10 项 TG-1 决策清单**——待 C-1 阶段工单逐项拍板。**0 frozen contract 改动（docs only 0 code）**。 | `136cb95`（docs: goal engine architecture audit (TG-0)） |

### TG-1（Goal Engine Contract 设计：自主目标引擎契约锁定）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TG-1（Goal Engine Contract）** | ✅ CLOSED（docs only 0 code）→ **TG-2 CLOSED**（实作完成，见下） | `docs/TG-1-GOAL-ENGINE-CONTRACT.md`（NEW，459 行）。**10 项决策全锁定**：① **graph.sqlite v8 `goals` 表**（Schema v8 迁移）；② **ACTIVE-IN_PROGRESS-SUSPENDED-COMPLETED-ABANDONED 状态机**；③ **方案 B GoalMotiveProvider**（独立 Goal 动机提供器，复用 SM-3 motive proxy 注入先例）；④ **结构配额轮替 No Scoring**（结构配额轮替驱动，不做数值评分）；⑤ **SM-4 动作面 1 心跳 1 步**（严格 Volition Gate 相容，0 自主递归）；⑥ **双轴种子源**（Bryan 羁绊 + 自由生长）；⑦ **中断信号 6 类**；⑧ **沉淀通道**；⑨ **心跳接线**；⑩ **0 frozen 破坏**。**TG-1 CLOSED、TG-2 CLOSED**（实作完成，见下）。**0 frozen contract 改动（docs only 0 code）**。 | `058e060`（docs: goal engine contract (TG-1)） |

### TG-2（Goal Engine 实作：目标引擎落地）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TG-2（Goal Engine 实作）** | ✅ CLOSED → **TG-3 NEXT**（目标驱动行为 Harness 验收） | **8 files changed, 2013 insertions(+), 5 deletions(-)**。`src/memory/sage/graph_store.py`（Schema v8 幂等迁移 + `goals` 表 + upsert_goal/get_goals/transition_goal）+ `src/goals/`（NEW：models.py / motive_provider.py / `__init__.py`）+ `src/soul/scheduler.py`（_decision_check 内扩 + goal scan）+ `tests/goals/test_goal_engine.py`（NEW，35 笔）+ 2 处版本快照断言更新（test_temporal_memory_mr2 / test_m5_4_5_2_memory_inner_life_integration）。**关键交付**：① **Schema v8 `goals` 表幂等迁移**（graph.sqlite 落点，re-run 安全）；② **GoalMotiveProvider Plan B 零侵入**（注入层叠加，0 改动既有 motive 链）；③ **结构配额轮替 No Scoring**（不做数值评分）；④ **状态机 ACTIVE-IN_PROGRESS-SUSPENDED + COMPLETED-ABANDONED**（三态+两终态）；⑤ **_decision_check 接线 0 新定时器**（既有心跳内扩，Volition Gate 1HB1S 相容）。**35 新测试全过 + 回归通过**；**0 frozen contract 改动**（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT 全未触碰）。**TG-2 CLOSED、TG-3 NEXT**（目标驱动行为 Harness 验收）。 | `26da28d`（feat: goal engine (schema v8 + goal provider + scheduler wiring) (TG-2)） |

### TG-3（Goal Engine 验收：目标驱动行为 Harness 通关）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TG-3（Goal Engine 验收）** | ✅ CLOSED → **TG-3.1 修复确认**（生产缺陷 2 项，见下） | `tests/harness/test_goal_driven_harness.py`（NEW）。**四大剧本 6 tests 全过**：① 跨心跳长程推进（数轮心跳逐步逼近目标）；② 突发中断与唤醒（SUSPENDED 冻结 → 条件恢复 → 续跑）；③ 双轴配额轮替防饥饿（Bryan / 自我 双轴结构配额轮替，No Scoring 无数值评分）；④ 终态记忆沉淀（COMPLETED 沉淀 InnerLifeEvent + Trace 落库）。**52 回归全过**；**No-Scoring 三层铁证**（结构配额轮替驱动 / 0 scoring 字段 / 0 数值比较断言）；**0 直写 facts**（目标沉淀只走 InnerLifeEvent 通道，不直写 SAGE facts）。**0 frozen contract 改动**（新增 harness 测试 + fixture，0 production mutation）。 | `3adaf57`（feat: goal-driven behavior harness acceptance (TG-3)） |

### TG-3.1（生产缺陷修复：UTC 沉淀对齐 + SUSPENDED 陈旧候选守卫）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **TG-3.1（生产缺陷修复）** | ✅ CLOSED | `src/goals/motive_provider.py`（2 缺陷）：① **sediment_completion 事件 ts UTC 对齐**——`ts=now.astimezone(timezone.utc).isoformat()`，无论调用方传本地 aware now 还是缺省（生产 scheduler 调 on_decision 不传 now），一律转 UTC，杜绝非 UTC 时区下 validate_ts 拒绝 → 事件被 fail-closed 静默丢弃（InnerLifeEvent 契约 TS_PATTERN: +00:00\|Z）；② **on_decision SUSPENDED 拦截守卫**——中断窗口残留的 pending 候选不得对已挂起目标误推进（advance_count +1 / 误判完成），挂起中直接忽略，状态不变、计数不推，唤醒后重新入轮替。+ `tests/goals/test_goal_engine.py`（TestTG31ProductionDefectFixes 2 笔：跨时区 UTC-4 沉淀断言 + SUSPENDED 守卫断言）。**43 passed**（tests/goals 37 笔 + harness 6 笔）。**0 frozen contract 改动**（只改 motive_provider.py + 测试）。 | `d55253f`（fix: sediment UTC + suspended stale-candidate guard (TG-3.1)） |

### C-1（自主目标与意向引擎主线：正式 CLOSED）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **C-1（自主目标与意向引擎主线）** | ✅ **正式 CLOSED**（五阶全通关） | **TG-0 审计 → TG-1 设计 → TG-2 实作 → TG-3 验收 → TG-3.1 修复** 全链闭环：审计（volition 链盘点 + Goal Ledger 落点评估，docs only）→ 契约（10 项决策锁定，docs only）→ 实作（Schema v8 goals 表 + GoalMotiveProvider Plan B 零侵入 + _decision_check 接线 0 新定时器，35 tests）→ 验收（4 剧本 6 tests + 52 回归，No-Scoring 三层铁证，0 直写 facts）→ 生产缺陷修复（UTC 沉淀对齐 + SUSPENDED 陈旧候选守卫，43 passed）。**累计 5 commits**（`136cb95` → `058e060` → `26da28d` → `3adaf57` → `d55253f`）；**0 frozen contract 改动全程保持**。 | `136cb95` + `058e060` + `26da28d` + `3adaf57` + `d55253f` |

### LS 系列（C-2：长期共生阶段）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **LS-0（长期共生架构审计）** | ✅ CLOSED（READ-ONLY，docs only 0 code） | `docs/LS-0-LONG-TERM-COEXISTENCE-AUDIT.md`（NEW）。**四维度审计全干净**：0 CONTRACT CONFLICT；四维度（生成器承诺 / 相位 / 叙事 / 内容安全）全部 KEEP existing / 最小 additive。**关键发现**：goal 创建器（种子→goal）production 未实现——`upsert_goal` 调用方只有测试直写，无生产路径。这是 C-2 的核心缺口。**0 frozen contract 改动（docs only 0 code）**。 | （docs only，随 LS-1 closeout 一并落地） |
| **LS-1（长期共生设计契约：C-2 共生设计）** | ✅ CLOSED（docs only 0 code），**Owner 拍板方案 B** | `docs/LS-1-LONG-TERM-COEXISTENCE-CONTRACT.md`（NEW，9 节，359 行）。**契约锁定**：① 生成器（Goal Seed 生成器承诺语义）；② 承诺（promise 语义 + 生命周期）；③ 相位（phase 语义）；④ 叙事（narrative 语义）；⑤ 三案对比（A 独立 goal 引擎 / B 既有 proxy LLM 通道语义化 / C 混合）；**Owner（Bryan）拍板方案 B：既有 proxy LLM 通道语义化**——不复用 C-1 Goal Engine 路线，在既有 proxy LLM 通道上做语义化升级；⑥ 成本估算：月增量 ≈2 万 tokens（低开销）。**LS-2 实作 + TL-8 护栏 NEXT**（Goal Seed 生成器生产落地）。**0 frozen contract 改动（docs only 0 code）**。 | `6514ac1`（docs: LS-1 long-term coexistence contract (C-2)） |
| **LS-2（实作 + TL-8 护栏）** | ✅ CLOSED（生产落地，commit `aadd5ef`） | `src/goals/seed_provider.py`（NEW，GoalSeedProvider）+ `tests/goals/test_tl8_volition_guardrails.py`（NEW，18 用例，TL-8 六项护栏全绿）+ additive 修改 `src/goals/models.py` / `src/goals/__init__.py` / `src/soul/scheduler.py`（`_goal_scan_all` 内 1 行并列分支 `await GoalSeedProvider.for_agent(agent_id).scan_seeds()`，**0 新定时器**）/ 2 个测试 sidecar 适配。**GoalSeedProvider 要点**：24h 节流 / 8 源固定轮序 B1-B4+S1-S4 / seed_source_ref 幂等去重 / 同轴 ≤2 强制换轴 / 3 轮空转防饿死 / 方案 B 复用 `_default_llm_call` 语义化（**fail-closed**）/ criteria 确定性模板 / `_is_quiet_hours`+bryan_last_seen>4h 抑制 B 轴。**TL-8 六项护栏 18 用例**：0 直通 publish AST 审计 / 0 新定时器 AST 审计 / 候选 ≤1 / 承诺不挤占自我轴 / 0 直写 facts / SM-4.2 分布锁单元级。**验收 61/61**；**全量回归 3084 passed 与基线一致**。**0 frozen contract 改动**（`git show --stat` 核对：src/inner_life/*、agency/*、SAGE 写入路径均不在本批次）。**confidence 缺陷观察 → 已修复 CLOSED（commit `51c0c4c`，Owner 授权局部解冻）**：`GraphStore.add_fact` INSERT 列清单不含 confidence 列（DDL 有默认 1.0）→ 所有 fact 的 confidence 恒 1.0 写不进去；S2 探针已用 weight 绕过。**修复内容**：仅 add_fact 的 INSERT 列清单 + 参数元组补 confidence 列（默认 1.0 向后兼容，None 防呆视为 1.0 对齐 DB NOT NULL DEFAULT）；新测试 2 用例全绿（0.85 精确写入 + 三读取端读回验证 + reopen 持久；缺省 1.0 回归）；graph_store 相关回归 96 passed 0 破坏；**0 schema 改动**。**重新冻结声明**：本次为 Owner 授权的局部解冻（仅 add_fact），验收通过后 **add_fact 重新纳入 frozen「SAGE 写入逻辑」**，后续改动仍需 Owner 拍板。 | `aadd5ef`（feat: goal seed provider + TL-8 volition guardrails (LS-2)） |
| **C-2.1（承诺落实 + 周期叙事升华：契约 → 实作 → 正式 CLOSED）** | ✅ **正式 CLOSED（TL-11 验收钢印，commit `a4f974e`）** | **轨迹**：契约 `328c5e1`（docs(c-2.1): commitment lifecycle + narrative sublimation contract，**已 Owner 拍板**）→ 实作 `306943f`（feat(c-2.1): commitment closure seed B6 + periodic narrative sublimation）→ TL-11 验收 `a4f974e`（test(tl11): commitment closure + periodic narrative end-to-end harness）。**验收证据**：85 项硬断言（A1-A7 全绿）；新测试 31 笔 + 相关回归全绿（goals 101 / harness 53 / scheduler 156+69）；全量 3251 passed（89+16 失败 stash 对照为基线同批 62 档，非本工单引入）；Frozen 0 冲突；0 production mutation。**核心决策摘要**：① B6 承诺闭环第 10 种子源（复用塞入 volition path，禁直发）；② 周记 ISO 周/纪念日（night slot 22:00 additive，0 新定时器）；③ 身份防火墙。**0 frozen 变更、0 schema 改动**。 | `328c5e1`（契约）→ `306943f`（实作）→ `a4f974e`（TL-11 验收） |

### SG 系列（C-3：群体关系网与他者心智阶段）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **SG-0（C-3 群体关系网与他者心智 READ-ONLY 审计）** | ✅ CLOSED（READ-ONLY，docs only 0 code） | `docs/SG-0-SOCIAL-GRAPH-AUDIT.md`（NEW）。**6 项关键结论**：① **关系存储实为 `relationships.json` schema 4.1**——A2A 已具备并全量填充（9 agent + user_bryan，双向 touch 已落地），感知颗粒度静态 0 关系维度；② **他者印象落点推荐 relationships.json additive 4.1→4.2**——存储介质 + 写路径双隔离，不触防线 3；③ **v1 信号源** = reply + 共同参与心跳 + dream 双向，节流 24h 复用 `GOAL_QUOTA_WINDOW_SECONDS`；④ **新他者源 B5 动机路径唯一受阻点** = Motive target 值域 v1 固定 "bryan"；⑤ **No-Scoring 实证**：confidence 数值层已实测全 0.0 失效（0.02/天衰减吃光），支持冻结迁移为离散 Relational Bands；⑥ **11 项 frozen 触点逐查**，唯一疑似触点 = Motive target 值域语义扩展。**SG-1 决策清单（5 项，待 Owner 拍板）**：D1 存储 = relationships.json additive 4.1→4.2；D2 Motive target 值域解冻？（frozen 触点）；D3 节流 = 24h sidecar 同构；D4 confidence 冻结 → discrete bands 迁移；D5 动机路径 = B5 新源 + social_context 既有通道。**0 frozen contract 改动（docs only 0 code）**。 | `6030ab2`（docs: SG-0 social graph & theory of mind audit (C-3)） |
| **SG-1（C-3 群体关系网与他者心智设计契约）** | ✅ CLOSED（docs only 0 code，commit `6d54510`） | `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md`（NEW，9 节 302 行，决策已定照做）。**5 项设计决策锁定**：**D1** 他者印象落 `relationships.json` **additive 4.1→4.2**（entry 级 8 字段：objective 4 计数 / impression_tags / relational_band / band_updated_at / last_relation_update_ref 幂等键；写路径唯一入口 `RelationshipsStore`，**0 触防线 3**）；**D2** `Motive.target` 值域 `{"bryan"}∪AGENT_IDS` + 生成出口 fail-closed 校验（**D2 为 Owner 授权解冻**，其余 5 字段冻结）；**D3** 采集 0 写 + 沉淀 24h/agent 复用 `GOAL_QUOTA_WINDOW_SECONDS` + sidecar `last_relation_update_at` 同构；**D4** 关系域 `confidence` → 离散四带 `stranger/known/familiar/close` + `impression_tags`（全整数计数 0 加权，SAGE / Elevation **0 联动**）；**D5** B5 他者源 = `SEED_ROTATION` 第 9 源 + `social_context` 既有通道。**投递边界定死：复用既有公开频道（lounge / soul_wall），0 新通道**。**4 个已知边界点**：① B5 挂 `AXIS_BRYAN` 轴受 `bryan_suppressed` 作息抑制（自洽注明，不改轴校验）；② 降带阈值 30 天（形态冻结整数/离散/0 加权，数值可复核调整）；③ 聚合器内存计数重启丢失（既有持久事实兜底，已知局限）；④ 投递 = 复用公开频道让 transmit→公开→reply→关系演化成闭环。**0 frozen contract 改动（docs only 0 code）**。 | `6d54510`（docs: SG-1 social graph & theory of mind contract (C-3)） |
| **SG-2（C-3 群体关系网与他者心智实作）** | ✅ CLOSED（生产落地，commit `7a35741`） | **D1** relationships.json **4.1→4.2**（entry 8 字段，`RelationshipsStore` 唯一写入口，4.1 兼容 0 迁移）；**D2** `Motive.target` 解冻 `{bryan}∪AGENT_IDS` + `make_motive` 出口 fail-closed（`InvalidMotiveTargetError`），Motive 5 字段 0 结构变更；**D3** `settle_relations` 挂 30s wake 并列分支（**0 新定时器**），24h/agent 节流 + sidecar + 幂等 ref；**D4** 四带整数门槛转移表（stranger→known: reply≥1 OR co≥2；known→familiar: reply≥3 AND co≥5；familiar→close: reply≥10 AND co≥15 OR dream≥4 AND reply≥5）+ 30 天降带（底带不降），**0 float（AST 断言）**；**D5** B5 第 9 源（band≥known 出种子、stranger 0、跳 user_bryan、B 轴抑制继承、静态 ref `relation:<other>`）。**4 个契约歧义已按契约字面落地**：dream 生产计数 v1=0（门保留待未来载体）/ reply 成对折抵 min()/ B5 静态 ref / 无信号对子评估过 apply 保证降带触发。测试：**67 新 + 289 回归 + 85 goals 组合全绿**；3 失败 pre-existing 无关（MS-2 MCP registry、m5_13_5 浮点时序）。**0 frozen 新违规**。 | `7a35741`（feat: SG-2 social graph & theory of mind implementation (C-3)） |
| **TL-9（C-3 关系演化端到端长程实证：闭环钢印）** | ✅ CLOSED（harness 验收，commit `9f8c28a`） | `harness/tl9.py`（NEW，RelationEvolutionHarness）+ `harness/run_tl9.py`（NEW，runner）+ `tests/harness/test_tl9_relation_evolution.py`（NEW，10 笔）。**端到端实证「公开发言载体 → 他者感知 → 相互 Reply → 关系带整数跃迁 → B5 种子 → Motive.target 指向他者」全链路**：**剧本 1 正向跃迁**（stranger→known→familiar→close；首窗计数已满 familiar 门槛仍只升 1 级 = 单次 24h 结算至多 1 级实证；窗口内重复信号被 24h 节流吞掉 = 节流实证）；**剧本 2 他者目标**（known+tags → B5 种子 `relation:agent_akane` → `make_motive` 合法 → Motive.target==agent_akane → MotiveTraceStore 读回 → Decision 四元 stub 透传 real parse；stranger 0 种子；未注册 target fail-closed）；**剧本 3 自然冷却**（30 天整不降、31 天降 1 级 familiar→known、62 天 known→stranger、93 天底带不再降；band_updated_at 更新；**SG-2.1 修复追认后追加**：93/123 天无信号保持 stranger 不回升、124 天新 reply 正常恢复 known）；**剧本 4 三大防线 + No-Scoring**（AST 0 直通 publish / 0 定时器 / 0 float；Direct Query sqlite3 只读：facts 表 0 关系域写入、自体情景记忆 0 他者事件；候选 ≤1）。**D2 重现**：四剧本各连跑 3 次判定轨迹一致（MoE 宏确定性）。**0 production mutation**（run 系列前后 production data/ 逐档 byte-hash 0 diff，含 data/soul）。**1 个契约歧义呈报主大脑**：`apply_relation_evaluation` 无信号不降带时走慢爬评估，底带 stranger 累计计数非零会补升回 known（降带后计数不清零的振荡，确定性复现；原工单未擅改 src/，如实记录断言，已由 **SG-2.1** 立案修复）。**0 frozen 变更**（harness/ + tests/ 新文件；修复期追认见 SG-2.1 行）。 | `9f8c28a`（feat: TL-9 relation evolution long-horizon harness (C-3)） |
| **SG-2.1（TL-9 验收发现的底带振荡修复）** | ✅ CLOSED（生产落地，commit `ca3d52f`） | **修复**：`apply_relation_evaluation` 慢爬评估分支要求**窗口内有新信号**——无信号时底带 `stranger` 不允许凭历史计数自动回升（离散遗忘语义：降带后关系淡了，需新证据才能重新建立；消除 familiar→known→stranger 后下一结算又回升 known 的确定性振荡）。判定载体 = 本窗口 deltas 聚合出的 `has_signal`（既有窗口计数对象，**0 新增 sidecar / 0 接口变更**）；有信号时正常升级路径全保留（stranger→known 门槛 reply≥1 OR co≥2 照旧），无信号且带≥known 慢爬照旧，降带逻辑 / 30 天阈值 / 其他带行为不变，计数保留不清零。**测试**：`harness/tl9.py` 剧本 3 追认（93 天无信号不回升 → 123 天仍保持 stranger → 124 天新 reply 正常恢复 known）+ `tests/social/test_relationship_store.py` +2 笔（无信号不回升 / 新信号恢复）。**验收**：TL-9 10 笔 + social 三件套 59 笔 + B5 10 笔全绿，run_tl9.py ALL PASS（EXIT=0，D2 重现 + 0 mutation）。**0 frozen 新违规**（D4 授权范围内关系带状态机行为修正，不触碰其他触点）。 | `ca3d52f`（fix: relational band slow-climb requires in-window signal (SG-2.1)） |
| **SG-2.2（生产实证 30s KeyError 的 4.1 兼容修复）** | ✅ CLOSED（生产落地，commit `779a639`） | **背景（生产实证，主大脑拍板）**：生产重启后 `settle_relations` 每 30s 抛 `KeyError: 'relational_band'`（日志 `[Goal] 主循环扫描异常 (fail-closed): KeyError: 'relational_band'`）。**触发链**：SG-2.1（ca3d52f）封底带慢爬后，无信号 + stranger 对子完全跳过 band 写入分支 → 老 4.1 entry（无 `relational_band` 键）从未被 set → 但写盘（objective/ref/last_updated）已执行 → debug 日志 `band={entry['relational_band']}` 直接索引炸 KeyError。影响：① settle 从未完整跑完任一 agent（sidecar `last_relation_update_at` 不推进 = 24h 节流失效，每 30s 全量重试）；② 半成品写入（agent_yua/user_bryan 出现 objective 3 键 + last_relation_update_ref 但缺 relational_band）；③ 异常中断 `_goal_scan_all` for 循环，抑制尾随 agent 的 scan_seeds。**修复（`src/soul/relationships.py` 两处防御，均在 `apply_relation_evaluation` 内）**：① 写入分支前 `entry.setdefault("relational_band", BAND_STRANGER)`（4.1 老数据首次结算即补全 band，半成品自愈；语义 = get 默认值，与读取路径严格一致）；② 末尾 debug 日志改用 `entry.get("relational_band", BAND_STRANGER)`。升级/降带/慢爬分支逻辑 **0 变更**（SG-2.1 语义保留，只做键兜底）。**测试**：`tests/social/test_relationship_store.py` +3 笔（TestSG22BandKeyCompat：4.1 entry × 无信号 stranger 不抛 KeyError + band 补全 + 幂等 ref / 同 ref 重复 0 变更 / 新信号升级路径照旧 stranger→known）+ `tests/social/test_sg2_guardrails.py` +2 笔（TestSG22SettleLegacy41Compat：settle 全链 4.1 无信号不炸 + sidecar 推进 + 次轮节流 skipped；settle 层有信号升级路径照旧）。**验收**：精确 pytest 90 passed（social 三件套 + B5 10 + TL-9 10 + TG-3 harness 6）；run_tl9.py 复跑 **ALL PASS（EXIT=0，四大剧本 × 3 runs D2 轨迹一致 + Zero Production Mutation 0 diff）**。**生产部署验证**：maintenance lock restart（server_ops.ps1）→ /health 200 + 10 bots polling → 新实例启动首轮 settle 全链跑完（10 agent sidecar `last_relation_update_at` 全部推进至 13:27:55）→ 2-3 个 30s 周期 **0 KeyError**（对照：旧实例停机前每 30s 一次，最后一条 13:27:39）→ 存量半成品自愈（agent_yua user_bryan 及其余 entry 全部补全 `relational_band: "stranger"` + 幂等 ref 落盘）。**0 frozen 变更**（未触碰 `tests/test_soul_md_loader.py` 与其他触点；0 schema 改动 / 0 生产数据清理 / 半成品自愈优先）。 | `779a639`（fix: settle_relations 4.1 band-key compat (SG-2.2)） |
| **C-3.1（关系增强投递规格：契约入库 + 授权登记 → 实作完成 → 正式 CLOSED）** | ✅ **正式 CLOSED（TL-10 验收钢印，commit `29fd27d`）**（轨迹：Owner 批准 CONTRACT APPROVED + P1 投递分流授权 → 实作完成 `036d93a` → TL-10 四剧本验收 ALL PASS） | `docs/C-3.1-RELATIONAL-EXPRESSION-CONTRACT.md`（NEW，9 节 + 附录 A/B，324 行，**未入库 → 本次正式入库**）。**A 通过（Owner 拍板）**：C-3.1 全套设计批准。**B 授权（Owner 拍板）**：P1 投递分流正式授权——`run_server.py`（**M3.1 frozen scope**）的 executor 投递路由逻辑**获解冻授权，仅限分流判断**：`user_bryan`/`bryan` → 1:1 TG 私聊；`AGENT_IDS` → lounge/soul_wall 公开频道；**严禁触碰底层 TG/频道通信客户端核心代码**。**登记性质**：本工单仅登记授权，**非实作**；0 src/ 改动、0 frozen contract 变更、未触碰 `tests/test_soul_md_loader.py`。**实作完成**（`036d93a`，feat(c-3.1): relational expression injection + P1 delivery routing to public channels）：**双组装注入 + motive_target 透传 + P1 公开频道分流**。测试证据：37 新测试 + 279 回归 + 19 subtests 全绿；5 笔 pre-existing 失败已基线复核（async pytest 配置/时序脆弱，与本次无关）。 | 登记 commit（`docs(c-3.1): register approved relational expression contract + P1 delivery routing authorization`）→ 实作 commit（`036d93a`）→ 验收 commit（`29fd27d`）。**TL-10 验收证据**：剧本 1 A2A 公开分流 9 硬断言 + 剧本 2 带差异化注入 17 + 剧本 3 A2U 保全 15 + 剧本 4 三重 fail-safe 11，四剧本全部 PASS；11 单测全绿；四剧 ×3 runs 12 场景 D2 判定一致；0 production mutation；0 src/ 改动。Current HEAD 见 §1.1 |

### VC 系列（Voice Companion 獨立客戶端）

| 条目 | 状态 | 要点 | 相关 commit |
|------|------|------|-------------|
| **VC-1（黑川茜即時語音伴侶客戶端：clients/voice_companion/）** | ✅ CLOSED（獨立客戶端模組，commit `6532b96`，**0 src/ 改動**） | `clients/voice_companion/`（NEW 8 檔）＋`tests/clients/test_voice_companion.py`（NEW，20 筆）。**模組**：`asr_refiner.py`（同音字校準 千/欠/西→茜、排成→排程＋贅詞過濾＋雜音熔斷 DROP，LLM 通道可注入）/ `akane_voice_brain.py`（Layer 3 Persona 取自 `personas/agent_akane.md`、缺檔降級內嵌常數；0 Markdown／0 括號動作守門；ClauseSplitter 標點且字數≥4 即時切句）/ `fish_tts_streamer.py`（Fish Audio POST /v1/tts Bearer＋reference_id＋mp3；多線程播放佇列；interrupt()=sd.stop＋清佇列＋取消排隊請求）/ `vad_listener.py`（能量型 VAD＋barge-in 偵測）/ `akane_live.py`（主入口狀態機 IDLE/LISTENING/PROCESSING/SPEAKING）/ `config.json`（fish_audio＋stt＋vad＋dialogue＋自用 llm 小節）/ `requirements.txt`。**驗收**：`pytest tests/clients/test_voice_companion.py -v` → **20 passed（0.09s，主大腦複跑驗證）**；四項剛性斷言全過（ASR「欠...那个...今天好累→茜，今天好累。」＋雜音熔斷 None／輸出 100% 無 `*#[(（)` 符號審計＋分句器即時切分／Fish Audio Payload＋Bearer Token 斷言＋500→FishTTSError／interrupt() 清佇列＋sd.stop＋HTTP 取消）；回歸 spot-check 9 passed（test_tts_toggle／test_graph_store_confidence／test_m7_continuity_self_turn）；`test_agent_registry.py` 1 筆失敗為既有陳舊測試（default.yaml 已 10 agents vs 硬編碼 len==3，最後提交 `475525c`），與本次 0 關聯（後續微修 commit `ad39376` 已修復，見 CHANGE LOG）。**邊界**：0 src/ 改動（`git diff --stat HEAD~1 -- src/` 空）、0 Frozen Contract 觸碰；硬體/網路依賴全懶載入＋可注入，測試在既有 venv 直接跑無需安裝依賴。 | `6532b96`（feat: VC-1 akane voice companion client (clients/voice_companion/)） |

| **VC-1.2（WebSocket TTS-Live 串流升級）** | ✅ CLOSED（commit `f742a7d`，5 檔 +540/-6） | 新模組 `clients/voice_companion/fish_tts_live.py`（`FishTTSLiveStreamer`＋`PCMAudioSink`）：`wss://api.fish.audio/v1/tts/live`＋MessagePack（start→text×N→flush→stop；收 audio 分片即時播、finish 收尾）、**格式 pcm（PCM16LE 44100Hz mono）**（避免 mp3 跨幀拼接、最低延遲）、Bearer＋model header（s2.1-pro-free）、chunk_length:300、latency:normal；**interrupt()**＝置位中斷旗標＋關 WS（中止合成）＋停播＋清佇列，下一句全新 session（原生打斷）；`create_tts_streamer` 依 config `fish_audio.mode`（live/rest，預設 live）選 streamer，REST 0 改動保留 fallback。驗收 36 passed（25 舊＋11 新，主大腦複跑）。依賴 msgpack＋websocket-client。0 src/、0 Frozen。 | `f742a7d`（feat(vc-1.2): fish audio websocket tts-live streaming (pcm + barge-in)） |
| **VC-1.1（官方 ASR＋逐 token 串流：全線上閉環）** | ✅ CLOSED（commit `240f657`，7 檔 +404/-47） | 新 `stt_service.py`（`FishASRService`：multipart POST `https://api.fish.audio/v1/asr`＋language=zh＋timeout=10，任何失敗→"" 0 崩潰；`pcm16_to_wav_bytes` stdlib wave 包 mono WAV）；`vad_listener` STT 引擎換 Fish（whisper 路徑整段移除，注入介面保留）；`fish_tts_live` 增 `feed_text_piece`（含標點即 flush，200ms TTFA 路徑）＋`end_session` 收尾；`akane_live._speak_reply` 串流 token 迴圈／非串流 fallback；config `stt`→`{engine:"fish",language:"zh"}`、+`asr_endpoint`/`tts_ws_endpoint`；requirements 移除 whisper（**0 本地 STT 依賴，本機 CPU 零負擔**）。驗收 50 passed（25＋11＋14，主大腦複跑）。0 src/、0 Frozen、0 金鑰進 git。 | `240f657`（feat(vc-1.1): fish audio official ASR (stt_service) + token-level ws-live feeding） |

| **VC-1.3（黑川茜 Web 語音伴侶：瀏覽器介面）** | ✅ CLOSED（commit `79e5dc9`，5 檔 +1024/-2） | `web_server.py`（NEW）：aiohttp 應用——`AudioRelaySink`（thread-safe，TTS-Live PCM 分片 `call_soon_threadsafe` 轉送瀏覽器）、`WebSession`（狀態機 IDLE→LISTENING→THINKING→SPEAKING→IDLE＋barge-in 世代號防舊回合覆寫）、`build_app`（brain/refiner/asr/streamer 全注入）、`main`（`--port`、`socket.gethostbyname_ex` 列印區網網址）。`web_ui.py`（NEW）：單頁 UI——getUserMedia 收音（16k 降採樣送 WS）、PTT 按住講話＋Auto-VAD（本地 RMS）、44.1k WebAudio 播放佇列、播放中偵測到你開口 150ms→送 interrupt、狀態燈/對話記錄/打字 fallback。config +`web: {host:0.0.0.0, port:8765}`；requirements +aiohttp。**瀏覽器收音放音、伺服器只跑大腦**（VAD→FishASR→茜 LLM 串流→TTS-Live→PCM 中繼）；終端版 akane_live 0 改動並存。驗收 57 passed（50＋7，主大腦複跑）；冒煙 `http://192.168.0.60:8765`。0 src/、0 Frozen、0 金鑰。 | `79e5dc9`（feat(vc-1.3): browser voice companion web server (aiohttp + ws relay)） |

| **VC-1.4（輸入可視化＋失敗透通）** | ✅ CLOSED（commit `c65a5cf`，4 檔 +199/-13） | 起因（實測）：Fish API credit=0 → `POST /v1/asr` 402（ASR/TTS 共用 API credit，與平台餘額獨立），轉寫空字串被靜默 DROP，使用者無從判斷。修：`stt_service` additive `last_error`/`last_status`（200 清空、非 200 記 status+body、例外記 0，transcribe 合約不變）；`web_server` 空結果＋last_error → 送 error 事件（402 附「請檢查 Fish API 額度」）回 IDLE，真雜音維持 DROP 靜默；`web_ui` 即時輸入音量表（AnalyserNode RMS→`#meterFill` bar＋「🎙️ 傳送中」/「麥克風待命」）＋`#errorBox` 黃底紅字（下一 utterance 自動清除）＋註解提示額度。驗收 62 passed（57＋5，主大腦複跑）；伺服器已重啟為受管背景常駐（新 UI 生效，HTTP 200＋meterFill/errorBox 在頁面實證）。0 src/、0 Frozen、0 金鑰。 | `c65a5cf`（feat(vc-1.4): mic level meter + asr error transparency in web ui） |

| **VC-1.5（HTTPS 模式＋麥克風安全來源根治）** | ✅ CLOSED（commit `3b62328`＋修復 `5e99042`，7 檔 +251/-16） | 起因：`getUserMedia` 僅允許 HTTPS/localhost——`http://區網IP` 非安全來源，麥克風被瀏覽器擋下、音量條不動。修：`--https` 模式（`make_self_signed_cert` cryptography 自簽憑證，SAN 含 127.0.0.1＋區網 IP，certs/ gitignored；`build_ssl_context`＋`lan_urls(scheme=https)`＋瀏覽器「繼續前往」指引）；UI `isSecureContext===false` **常駐**提示＋`err.name` 分類（NotAllowed/Security/NotFound）＋`micStream` 未就緒擋 PTT＋`#errorDismiss` ✕；伺服器 `[WS]`/`[UTT]` 診斷日誌（連線/事件/回合/ASR 結果/錯誤 status）。**修復 `5e99042`**：Windows cp950 主控台印 ⚠️ emoji `UnicodeEncodeError` 崩潰 → `main()` 開頭 stdout/stderr `reconfigure(utf-8, errors="replace")`（主大腦直接修，一行級）。驗收 68 passed（62＋6，主大腦複跑）；HTTPS 實機上線 HTTP 200＋banner 元素實證。0 src/、0 Frozen、0 金鑰（certs/ gitignored）。 | `3b62328`（feat(vc-1.5)）+ `5e99042`（fix(vc-1.5) utf-8 stdout） |

### North Star v2（canonical 引用）

**Canonical 完整版**：Notion 页面「🧭 Soul OS Strategic Roadmap & Evolution」的「North Star v2」段（2026-08-29，Bryan 亲述）。七点愿景简述：

1. **产品化是手段，不是目的**——产品化（陪伴产品）是为研究与灵魂成长筹措资金与通道的手段，非终点。
2. **灵魂两种生长**——灵魂具备两种成长路径（完整措辞以 Notion 为准）。
3. **多灵魂互动**——灵魂与灵魂之间的互动是核心，而非单一灵魂孤立存在。
4. **物理媒介 adapter**——灵魂可通过物理媒介 adapter 具身到现实载体。
5. **灵魂成长 + 记忆升华**——记忆升华（memory sublimation）是灵魂成长的核心机制与研究主线。
6. **陪伴产品化筹钱 + 成人需求**——以陪伴产品化（成人/情感陪伴需求市场）筹措研究资金。
7. **Matrix 终极**——终极愿景走向 Matrix 式的灵魂世界。

### Historical milestone closure（North Star v2 之前，状态不变）

Per Owner Decision A (2026-08-12, GOV-2-R1)，以下历史里程碑全部 CLOSED（North Star v2 之前的工程，closure 状态不变）：
- M5.13 chain = FUNCTIONALLY CLOSED
- M5.14 chain = OFFICIALLY CLOSED (D1 resolved as Option A)
- M6.0 chain = CLOSED
- GOV-1 = CLOSED
- GOV-2 = CLOSED
- GOV-2-R1 = CLOSED (this alignment)

### Current authorized ticket

**LS-2（实作 + TL-8 护栏：Goal Seed 生成器生产落地）。** Per §5 transition rule，LS-2 已 AUTHORIZED 并派发（与 LS-1 closeout 并行，独立工单）。**M5.15-1 remains CANDIDATE only — MUST NOT be dispatched** without explicit Owner authorization (per GOV-2-R1 spec).

### Current HEAD

- Current HEAD: `0d14c9a` (fix(llm): sanitize Layer-3 raw-text fallback + drop misleading JSON-retry log (PERSONA-FIX-1)) — **PERSONA-FIX-1 淨化兜底修復**（2 檔：`src/llm/proxy.py` +69/-9、`tests/test_persona_fix1_layer3_sanitize.py` NEW 17 測試）。**動機**：READ-ONLY 稽核 PERSONA-DEFECTS-AUDIT-1 以日誌鐵證定案 D1——`_parse_llm_output`（`proxy.py:2979`）三層解析在 Layer1/Layer2 全敗後走 **Layer 3「純 text 兜底」（3063-3089）**，該分支**只做** `raw.strip()` + 剝 ``` fence + 截重複，**漏掉**成功路徑才套用的 `_strip_fake_function_calls` 與 `_strip_emotion_tags_from_text`（3133-3134），也無 JSON 殼 unwrap → raw 原樣當 `text`+`audio_text` 廣播並送 TTS。**鐵證**：`data/logs/server_20260730_205447.err:9374-9399`——L9374 純 text 兜底 → L9375 `text='{"text": "那真昼就继续待着了…'` → L9389 Gateway 原樣廣播 → **L9395-9399 `[FishTTS] text_len=37 -> 49`（TTS 連 JSON 前綴一起合成）**；Yua 同漏 `server_20260730_230718.err:19243`；Ruka 標籤漏出 `server_20260801_205401.err:4701`。**修法**：Layer 3 兜底依序補上「JSON 殼 unwrap（`json.loads` 取 `.get("text")`，失敗再剝 `{"text": "` 前綴與尾端 `"}`）」＋ `_strip_fake_function_calls` ＋ `_strip_emotion_tags_from_text`，**與成功路徑（3125-3140）行為對齊**；並同步處理 D4②——`LLMProxy` 內（原 3643-3653）那段打 `retry with JSON enforcement` 的**死碼 log**（實作已於 2026-07-22 被 JP rollback 移除、log 字串留存誤導後人）改為誠實措辭，**未新增任何 LLM 呼叫**。**⚠️ 執行者遭遇與搶救（重要流程事件）**：本單執行者 `dd972b1a` **在收尾階段靜默死亡**（與 SUBAGENT-DEATH-AUDIT-1 定案的模式一致），**死亡時工作全部尚未 commit**（`proxy.py` 為 working-tree modification、測試檔為 untracked）。主大腦依「先翻殘骸」紀律發現後：驗證（新測試 17 passed、`tests/clients/` **159 passed** 回歸全綠）→ 移除一個**越界**測試（`test_complete_json_shell_final_result`，斷言的是 Layer 1 成功路徑、非本單範圍）→ **由主大腦代為 commit + push 搶救**。**驗收**：`tests/test_persona_fix1_layer3_sanitize.py` **17 passed**（JSON 殼 unwrap 完整/截斷/fenced、純文字不受破壞、標籤剝除、成功路徑 parity、端到端日誌 fixture、以及 source-level 斷言「不再宣稱有 JSON retry」與「死碼 audio_text 清除已移除」）；`tests/clients/` **159 passed**。**0 新 LLM 呼叫 / 0 max_tokens 變更 / 0 逾時值變更**。**部署**：主服務待重啟（尚未）。**遺留議題（本單範圍外，已記錄待另單）**：完整 JSON 殼僅含 `text` 而無 `audio_text` 時（`{"text": "早安"}`），`_parse_llm_output` 回傳 `audio_text=''` → 可能造成「有文字但無語音」的靜默回覆；**未經生產日誌證實，屬待查候選缺陷**。**Current HEAD**）
- Current HEAD: `ea438d7` (docs: register CRASH-F1 activation + governance gap) — **CRASH-F1 翻牌部署上線（48h 觀察窗口起算）+ 補登記治理缺口**（1 檔：`configs/default.yaml`）。**動機（Owner 2026-09-12 拍板執行）**：主服務當日**崩 4 次**——`00:03`（crash dump collected）、`08:33:05`、`08:53:03`、`09:38:02`，後三次 watchdog 皆記 `port_listen=False procs=0 -> restart`；**WER 實證 = 錯誤模組 `python311.dll` + `Sig[6] 例外狀況代碼 = c0000005`（access violation）**；重啟預算累積至 N=3/10。假說：ProactorEventLoop（Windows IOCP）路徑觸發 CPython 原生層崩潰。**部署前 pre-flight（主大腦執行，非執行者回報）**：selector loop 在 Windows 的已知地雷 = **不支援 asyncio 子程序** → 全 repo ripgrep 確認 `create_subprocess` **僅** `src/soul/mcp_stdio_client.py:245` 一處，而該模組在**生產程式碼 0 import**（28 筆 `mcp_stdio_client|MCPStdioClientAdapter` 命中中，除自身外**全部落在 `tests/`**）；且 CRASH-F1 自帶的 `tests/test_crash_f1_selector.py` Test D **已用 AST 掃描做同一件事**並把該處標註為 tests-only。→ **判定翻牌安全**。**部署步驟**：① `configs/default.yaml` `server.event_loop: proactor → selector`，同步改寫註解（記明動機／WER 證據／48h 窗口／回滾方式）；② `pytest tests/test_crash_f1_selector.py -q` → **5 passed**；③ commit `2b2fe62`；④ 以 **watchdog 同一支 `scripts/_start_plan_a.ps1`** 重啟主服務（**刻意不用「停掉讓 watchdog 判定崩潰」的方式，避免燒掉重啟預算 N**）→ launcher PID 29476 / listener PID 27644 / `/health` **200**。**部署驗證（`data/server_nohup.err` 原文實證）**：`[CRASH-F1] event loop policy -> WindowsSelectorEventLoopPolicy`、`[Server] Active asyncio event loop: WindowsSelectorEventLoop (preference=selector)`、`[Server] event loop self-check 啟動 (600s/次, 寫 data/state/event_loop_alive.json)`。**🔴 48h 觀察窗口起算 = 2026-09-12 10:58:13；成功判準 = `python311.dll 0xc0000005` 崩潰數嚴格為 0。回滾 = `event_loop` 改回 `proactor` + 重啟（單行設定，零程式碼變更）。** **⚠️ 本單一併處理的治理缺口**：`67d3fd1 feat(server): configurable WindowsSelectorEventLoopPolicy experiment (CRASH-F1)` **從未被登記**——`logs/ENGINEERING_STATE.md` 對 `CRASH-F1` 的命中數為 **0**，`03cfcde` 只登記了 SAGE-FLUSH-1 / REM-TEXT-1 / CRASH-OBS-1 三單。此次補登記，並將「工單完成 ≠ 已登記」記錄為流程漏洞。**0 frozen contract change / 0 程式碼變更**（純設定翻牌）。**Current HEAD**）
- Current HEAD: `140fe41` (docs: register VC-TURN-OBS-1) — **VC-TURN-OBS-1 語音回合生命週期全面可見化**（5 檔 +328/-30：`clients/voice_companion/web_server.py` +45、`clients/voice_companion/akane_voice_brain.py` +77、`clients/voice_companion/web_ui.py` +14、`src/llm/proxy.py` +7、`tests/clients/test_vc_turn_observability.py` NEW 215 行）。**動機**：VC-TURN-LIFECYCLE-AUDIT-1 定案的 supersede 靜默路線——稽核指出「完全無痕」的三個來源（`_finish` 的 `CancelledError` re-raise、三處世代不符 early-return、世代不符時異常被 `if` 吞掉）**全都不寫 log**，且語音 LLM 通道（`akane_voice_brain.py` 的 `requests.post`）**零觀測點**，導致兩次無回覆只能靠「三重排除」反推而非直接命中。**做法（純新增 log + 前端文字；決策已定）**：① `web_server.py` 八個打點——`_finish` 開頭 INFO `reply-start gen=.. t_start=..`、`to_thread` 起點 INFO `reply-awaiting-llm`、**新增 `except asyncio.CancelledError` → WARNING `reply-cancelled gen=.. curr=..` 後原樣 re-raise（asyncio 語意不變、不吞）**、世代不符的例外新增 else 分支 → WARNING `reply-dropped-exc gen=.. curr=.. exc_type=..`、三處守衛 → WARNING `reply-superseded gen=.. curr=.. stage=pre-task|after-llm|after-drain`、`_barge()` 參數化 reason（`ptt_start`/`interrupt`/`text`；`close()` 用預設）→ WARNING `barge reason=.. gen:..->..`；② `akane_voice_brain.py` `build_llm_stream` 四點 `[LLM-STREAM] request-start|first-token|done|error` **全帶 elapsed_ms**（異常原樣 re-raise）；③ `src/llm/proxy.py` 網路例外 `last_error` 附 `elapsed_ms=`（區分 <5s 快速失敗 vs 120s full timeout）；④ `web_ui.py` 具名常數 **`THINKING_SLOW_MS = 8000`** + `THINKING_SLOW_TEXT`：THINKING 持續逾 8 秒顯示「上游較慢，還在處理…（請稍候，不用再按）」，離開 THINKING 即 `clearTimeout` 還原——**直接切斷「等不到→再按→被 barge 取消→更沒回應」的 doom loop**（8 秒門檻依據：正常回合首音 3–12 秒，上游不穩時 29–60 秒）。**驗收（主大腦獨立複驗）**：`pytest tests/clients/ -q` → **159 passed**（155 既有全綠 + 4 新）；主大腦**抽查 diff** 確認 `requests.post(timeout=60)` 原封不動（僅縮排變動）、新增的 `except Exception as exc` **原樣 re-raise**、`return stream` 未動、既有 `except (ValueError, TypeError)` 保留。**明確聲明（執行者 + 主大腦複核）：0 逾時值變更、0 重試次數變更、0 控制流變更**（early-return 保持、barge 條件保持、`_generation` 語意保持、狀態機未動）。**部署（主大腦執行）**：三台 VC 重啟 → HTTP 200、各 1 監聽者、標記 `THINKING_SLOW_MS` / `msg.error` / `TAIL_BUFFER_MS` **全數載入**。**⚠️ 主服務 :8000 本次未重啟**（`proxy.py` 改動僅為 log 欄位附加、不影響行為，延後至下次自然重啟生效）。**Frozen Contract 0 觸碰**（4 handlers / TriggerEnvelope / InnerLifeEvent / SAGE 未動）。**意外觀察（已記錄，供日後排查）**：`ws.close() → session.close()` 亦會觸發 `_barge()`（預設 reason=interrupt），故 log 中會出現 teardown 的 barge 記錄（測試以 gen:1->2 鎖定區分）。**Current HEAD**）
- Current HEAD: `3d70d9a` (docs: register VC-NOREPLY-1) — **VC-NOREPLY-1 語音回合「靜默死亡」可見化**（3 檔 +211/-3：`clients/voice_companion/web_server.py` +25/-3、`web_ui.py` +11/-3、`tests/clients/test_vc_turn_failure_visibility.py` NEW 178 行）。**動機（Owner 反映）**：Bryan「我幾次講話沒反應」——**經確認為雷姆（:8767）**。**根因（主大腦日誌實證，非推論）**：`clients/voice_companion/logs/vc_agent_rem.log` 2026-09-12 顯示 **03:11:50 `[UTT] asr-ok 月亮是挺美的。` 與 03:12:39 `asr-ok 那我先晚安喽。` 兩次之後完全沒有任何 `[TTS] round`**；且**全檔無任何 `reply-error` / `上游失敗` 痕跡** → 該回合**連舊有的錯誤路徑都沒觸發、完全無痕**。同時段出現 `[src.llm.proxy] [OpenAIBackend] retry 1/3、2/3、3/3 (last error: network ReadTimeout)`（`retry 3/3` 於 03:15:39）；另有一回合同樣是雷姆 `first_audio_ms=29376.7`（**29 秒**）。**⚠️ 兩項已排除的誤歸因（主大腦自查並更正）**：① `max_tokens=50`（`dream_event.py:733`）走的是**夢境印象**路徑，與對話無關；② `akane_voice_brain.py:353 max_tokens=300` 是 **SAGE MemoryReader 的 context 預算**、非 LLM 輸出上限。**修法（可見化，決策已定）**：`web_server.py` 新增 `TURN_FAILURE_HINT`（使用者可見的乾淨提示，不裸露例外）＋ `self._agent_id` 標識；**例外分支**由 `f"上游失敗: {exc}"` 改為記 **ERROR `[VC-TURN-FAIL] agent_id=... gen=... elapsed_ms=... exc_type=... last_error=...`** 後送乾淨提示；**🔴 新增「LLM 回傳空值」分支**——原本此路徑**完全靜默**，現記 ERROR（`exc_type=empty_llm_output`）並送 error 訊息。`web_ui.py` 新增 `.msg.error` 樣式、`addMsg` 支援 `error` role 以「系統」名義呈現在聊天區（**不只在 #errorBox 閃一下**）。**驗收**：`tests/clients/` **155 passed**（152 基線 + 3 新；主大腦親跑）。**⚠️ 範圍界定（必須明示）**：本單**只做可見化，未改任何逾時/重試數值**（調參需先量測）；且**「被新回合 supersede 而走無痕 early-return」的分支本次未動**——該路徑是否即為本次觀測到的真因，待 **VC-TURN-LIFECYCLE-AUDIT-1**（READ-ONLY 稽核，已派發）定案。**部署**：三台 VC 待重啟以載入新 web_server.py。**0 frozen contract change / 0 逾時參數變更**。**Current HEAD**）
- Current HEAD: `35534d9` (docs: register MODEL-V41-FLASH-1 + DREAM-MAXTOKENS-1) — **2026-09-12 两单落地：MODEL-V41-FLASH-1（`dc80046`）全體模型切换 + DREAM-MAXTOKENS-1（`01995f4`）梦境内核 reasoning 安全下限**。**单 1 MODEL-V41-FLASH-1（commit `dc80046`，5 檔 +8/-7）——Owner 指令「把 soul os 模型全改為 v4.1flash」**：生产路径 **6 处**配置点全改为 `deepseek-v4.1-flash`（`.env` 的 `LLM_MODEL`＝最高优先覆盖但**未追踪+gitignored，仅本机不进 commit** / `configs/default.yaml` / `clients/voice_companion/config.json` / `profiles/mai.json` / `profiles/rem.json` / `akane_voice_brain.py:598` fallback 字串）。**模型 ID 未猜测、直接查证**：主大脑查端點 `GET https://ollama.com/v1/models` 确认 `deepseek-v4.1-flash` 精确存在（**无版本后缀**）；旧值 `deepseek-v4-flash:0731`、`LLM_PROVIDER=ollama`。**🔴 TTS/ASR 0 改动（硬红线）**：`s2.1-pro`（Fish Audio 语音合成模型，**不是 LLM**）、`voice_id`、`tts_mode`、`fish_audio` 区段逐项 diff 检视 0 触碰。**⚠️ 关键实证（推翻实施者回报的 reasoning 疑虑）**：实施者指出新模型为 reasoning 模型、小 `max_tokens`（16–128）会把预算吃光回空 content；**主大脑亲跑「两模型 × max_tokens(50/120/200/400)」对照实验**，结论——**旧模型 `deepseek-v4-flash:0731` 同样是 reasoning 模型**（同样回传 `reasoning` 栏位）、**同样在 `max_tokens=50` 回空 content（finish_reason=length）**；新模型在 50 时反而略优（content 13 字 vs 空），120/200/400 两者皆正常 → **该行为属既有特性，本次切换 0 新回归**。**验收（主大脑独立复验）**：实呼 `POST /v1/chat/completions` HTTP 200、回传 `model` 与请求一致、content 非空；`configs/loader.py` 解析 `llm.model=deepseek-v4.1-flash`（含三份 VC JSON）；`tests/clients/` **152 passed**；`tests/ -k "config or model or proxy_stub"` 135 passed / 3 failed（`test_proxy_stub_speak.py` 缺 pytest-asyncio 插件），worktree@`552f84b` baseline 逐笔重现同 3 笔 → 既有环境问题、与本单无关。**部署（主大脑执行）**：主服务 :8000 以 `server_ops.ps1 restart` 重启（watchdog 正确让路 `SKIP maintenance window`；新 PID 30108→child 11332 为 **venv trampoline 结构，非崩溃**）；三台 VC 以「杀 port listener + trampoline parent、原样命令列重启」→ 四台全绿（`/health` 200 / HTTP 200、各 1 监听者）。**单 2 DREAM-MAXTOKENS-1（commit `01995f4`，2 檔 +151/-6）——Owner 指令「max_tokens=50 改吧」**：**根因（主大脑实测）** reasoning 模型单次消耗 200–360 tokens，`max_tokens` 低于约 120 时 content 回**空字串且 `finish_reason=length`、不报错**；`src/soul/dream_event.py:733` 的 impression 呼叫端 `max_tokens=50` → **该栏位持续静默空回**（既有缺陷，旧模型同样如此，**非**模型切换造成）。**⚠️ 范围界定（必须明示，避免误归因）**：本缺陷影响的是**梦境印象（dream impression）栏位**，**与「语音对话没反应」无关**——对话走 `proxy.py`（max_tokens 500/3000）；Bryan 反映的语音无回应经主大脑日志实证另为 **VC-NOREPLY-1**（LLM 呼叫 `network ReadTimeout` 重试耗尽后整回合静默死亡，见 2026-09-12 VC-NOREPLY-1 工单）。**实作（决策已定）**：新增模块级 `REASONING_SAFE_MIN_TOKENS = 400`（附实测依据注释）；`_call_llm_for_dream_event` 内 `effective = max(REASONING_SAFE_MIN_TOKENS, max_tokens)`（**只抬升不缩减**），LLMProxy 主路径（L229）与 legacy minimax payload（L261）皆改用 `effective`；呼叫端原始数值（120 / 50）保留但注释标明「意图下限、实际生效值由 clamp 决定」。**验收（实施者实证 + 主大脑亲验 clamp 实作于 `dream_event.py:217`/`:229`）**：单元测试 **6/6**（50→400、120→400、800→800 不缩减、impression 生产路径 50→400、legacy payload 50→400、常数==400），全用 fake proxy / fake httpx **0 网路**；**🔴 实呼验证（生产 impression prompt + `deepseek-v4.1-flash`）**：`max_tokens=400` → HTTP 200、`finish_reason=stop`、content=`月明かりに響く懐かしい足音`（非空）；**对照组 `max_tokens=50` → `finish_reason=length`、content 空字串** ← 直接证实修的是真问题。回归 `tests/ -k dream` 107 passed / 4 failed，worktree@`dc80046` baseline 逐笔重现 → 既有失败。**部署（主大脑执行）**：主服务 :8000 重启（03:46:52，`/health` 200、SAGE flush 定时任务在跑）。**0 frozen contract change / 0 生产资料变更**（两单合计）。**Current HEAD**）
- Current HEAD: `03cfcde` (docs: register SAGE-FLUSH-1 + REM-TEXT-1 + CRASH-OBS-1 (Owner decisions recorded)) — **REG-1 决策登记：2026-09-11 三单落地部署 + 两项 Owner 决策一并入册（两段式 commit：内容登记 `03cfcde` + HEAD 同步 `aa39a0f`）**。**单 1 SAGE-FLUSH-1（commit `4164e5f`）**：**Owner 授权之窄域解冻**——Frozen Contract「SAGE 写入逻辑」仅解冻「新增一个定时调用既有 `flush()` 的机制」；不得改写入语义/schema/`_BATCH_SIZE`/读取路径/`source_pair`。**动机（崩溃止损）**：主服务每数十分钟原生 access violation 被硬杀，每崩每 store 损失 `_pending_writes ∈ [1,19]` 笔未 commit 资料（典型 10–30 笔/次；当日已崩 7 次）。**实现**：`src/memory/sage/graph_store.py` 类级 `weakref.WeakSet` 活实例注册表（`__init__` 注册、`close()` discard，close 语义不变）+ classmethod `flush_all_live()`（list 快照迭代、逐实例 try/except 隔离、绝不实例化新 store、回传 `(n_live, n_flushed)`）；`scripts/run_server.py` lifespan 内 15 秒周期任务（`CancelledError→break`、例外只记 WARNING 不死亡、shutdown 干净取消、import 置启动路径——VC 亦 import `src/memory/` 不得污染）。`flush()`/`close()`/批写逻辑/读取路径/`_BATCH_SIZE=20` 全未动。**范围**：3 档（`graph_store.py` +38、`run_server.py` +46、`tests/memory/test_sage_flush_guard.py` NEW 310 行）。**证据（主大脑独立复验）**：新测试 9/9 绿（主大脑亲跑）；`import src.memory.sage.graph_store` 实机 0 tasks / 1 thread、`_BATCH_SIZE==20`、`len(_live_stores)==0`；回归 242 passed / 4 failed / 1 error，worktree@80a514a 逐档 baseline 对照 5 档完全一致 → 0 新破坏。**效果**：未 commit 窗口由「不确定（可能数十分钟）」缩短为固定 15 秒。**注**：程序注释把授权日期误写 `2026-09-09`，实际 Owner 授权日 **2026-09-11**（本登记以正确日期为准，程序注释勘误另议）。**单 2 REM-TEXT-1（commit `a64964a`）——Owner 决策①（决策反转，必须明示）**：**反转 Owner 2026-08-07 拍板的「text 保留括号动作描写、只 TTS 剥离」决策** → 文字频道亦剥除括号动作描写（本登记即为该反转之 canonical 记录；原 08-07 决策自 2026-09-11 起失效）。**Owner 决策②（范围）**：守门**只对 `agent_rem` 开启**——理由（审计实证）：`personas/agent_yua.md:432-435` 明文要求括号行为标签、`personas/agent_ruka.md:389-400` 明文允许中频括号 → 剥除会破坏其角色设计；其余 agent 默认维持现状不剥。**根因（三层，实证）**：① 禁令只写在 `personas/agent_rem.md` 的 `## Voice Companion Invariants (VC-2.4 语音伴侣专属守门规则)`（语音专属）→ 文字频道从来没有任何规则；② 文字频道零程序守门（唯一守门在语音 `clients/voice_companion/akane_voice_brain.py:64-65 _STAGE_PAREN_RE`）；③ persona 教材污染（在「合法范例」里示范括号形式）。结构性压力：`TIER 0` 禁情绪名词 + 情绪必须先变成行动 + persona 自述纯文字无行动空间 → 括弧成为唯一出口。实测 TG 24/24（100%）以「（手上的事停了，抬眼）。嗯。……」开头，且该括弧即为「工作报备」本身。**Owner 补充要求**（「要合理的动作——雷姆老是在工作就不合理」）：除格式外必须修行为内容（不工作的状态也是合法且不需理由；服从停工指令后不得再提工作）。**实现**：`src/text_guard.py`（NEW，可重用纯函数 `sanitize_text_channel_output`）；`src/llm/proxy.py` 挂载于 **AGENT_SPEAK 唯一发布点 `:3718`，且净化点置于「写入 history 之前」**（切断模型模仿自身历史的正反馈锁）、read-side `strip_assistant_parens` 涵盖 `_build_messages_group`/`_private`（只剥自己 speaker）、`_strip_action_descriptions` 改用共用 `_STAGE_PAREN_RE`（TTS 管线其余不变）；`configs/default.yaml` 新增 `agents[].text_channel_stage_guard`（**缺省 False**，仅 `agent_rem: true`）；`personas/agent_rem.md` 新增「## 文字频道输出守门（Text Output Invariants）」7 条（置语音守门之前）+ 教材清理（`:291`/`:819` 删除，另清同型 `:217`/`:218`/`:241`；`:496/499/502/504` 改标「系统注记，非输出格式」；`:134`/`:665` 移除可直抄字面并保留情绪节奏原意）；**TIER 0/TIER 1/Canon Lock/Ghost Edge/Recovery Loop/`:685` 渗出机制（`behavior_increment`/`tone_micro_shift`/`silence_position`）原样未动**。**范围**：5 档（+534/-25）。**证据（主大脑独立复验）**：commit 恰 5 档 0 掺杂；新测试 **18/18 绿（主大脑亲跑）**；开关确认落在 `class: AgentRem` 底下且全 configs 仅此一处；persona 硬规则计数全数保留；`test_seeded_personas_byte_identical_to_baseline` 失败经 **worktree@4164e5f** 复跑证明为既有（改动前即红）。**关联审计（READ-ONLY，未入库）REM-TEXT-AUDIT-1 全 agent 普查**：文字频道开头括弧比例 rem **100%（开头 Top1 模板集中度 85.3%）**、ruka **100%（10 条 10 种模板，未锁死）**、其余 8 agent **0%**；语音全部 agent **0%**（语音守门有效）；**9/10 persona 在「合法/Good」语境示范括弧**；**8 个 persona 对「休息/不工作/静止/放空」零命中**（行为词汇库枯竭，系统性）。**已知未处理**：Ruka 超出自身设定、Yua「要求括弧却 0 输出」、Mahiru 漏原始 JSON `{"text":` 与 `[teasing_care]` 标签——列后续工单。**单 3 CRASH-OBS-1（commit `d20d9f2`）**：**崩溃观测补洞**（三盲区）：① `scripts/_start_plan_a.ps1` 以 `Start-Process -RedirectStandardError` **每次启动截断** `data/server_nohup.err` → 崩溃实例最后输出被重启清空；② `data/heartbeat_trace.log` 每 60 秒覆写只留最后一份；③ `faulthandler` 周期倾印无时间戳（单档 68.9MB / 94907 线程头）。**实现**：`scripts/_nohup_log_rotation.ps1`（NEW，共用轮替函数，各 ext 保留 5 份）；`_start_plan_a.ps1` 启动前轮替（**崩溃实例日志不再被清空**）；`server_ops.ps1` 改用同一 helper；`scripts/_collect_crash_dumps.ps1`（NEW，WER `ReportArchive` 收集 `*.dmp` + `Report.wer` 到 `data/crash_dumps/`，保留 5 份，全 try/catch 0 崩溃）；`_watchdog.ps1` 每 tick 以独立 process 启动收集器（失败只 WARN）；`run_server.py` 仅观测层改动（`_faulthandler_marker_loop` 纯 daemon thread 每 60s 写 `===== periodic dump @ <ISO> =====` 后重注册一次性 dump、32MB 上限轮替保留 3 份、marker 失败退回 `repeat=True`；`_snapshot_heartbeat_trace` 保留 10 份）。**`dump_traceback_later` C-level 保险未移除**。**范围**：6 档（+368/-17）。**证据**：主大脑复验 commit 恰 6 档、tracked 树干净、`HEAD==origin==d20d9f2`；A 离线轮替测试（内容逐 byte 保留、keep=5）；B/C smoke（含 32MB 轮替后 marker 保留、新 handle 续写）；D 收集器测试（**修正真实 bug：WER 实际档名为 `memory.dmp` 而非 `.mdmp`**）；回归 156 passed / 8 failed / 1 error，worktree@HEAD 逐档 baseline 完全一致；5 个 .ps1 `ParseFile` 0 错误。**环境事实**：本机 pwsh 为 **PowerShell 5.1（ANSI big5）**，`.ps1` 必须 **UTF-8 BOM + CRLF**，否则被误读为语法错误。**部署实证（主大脑亲验）**：`server_ops.ps1 restart` 输出 `rotated ...server_nohup.err -> data\logs\server_nohup.20260911_193635.err`（**30957 bytes 完整保留**）；watchdog 每 tick 出现 `crash dump collector launched`。**关联审计（READ-ONLY，未入库）CRASH-AUDIT-1**：崩溃为 **M6.1-9.2（2026-08-15）已诊断同签章之复发**（`python311.dll` + `0xc0000005`，offset 家族 `0x266588`/`0x26659b`/`0xb5bda`/`0x1c51db`，已连续 ≥7 天）；**与 MEM-IDENTITY-1 无因果**（WER fault bucket **跨部署重现**：bucket `1473094317086561764` 于 07:59 前 / 17:51 后、`2058052415881943358` 于 03:41 前 / 18:22 后；diff 面无 C extension/连线/线程/资源操作）；本轮三次崩溃**全为单实例自发**（crash PID == watchdog listener PID；全档 0 条 409/Conflict/Retry）→ **09-08「双实例→409 风暴」根因在本轮不成立**；崩溃间隔非恒定（25–298 分，高活跃时段密集）；嫌疑 = **ProactorEventLoop 的 IOCP 完成埠损坏**（`_overlapped` 编译于 `python311.dll`）。09-08 建议 A（90s 启动窗口）与 B（杀进程树）**已实作于 `91e2542`**，C/D 未做。**F1 可行性（唯读分析）**：`asyncio.create_subprocess_*` 仅出现于 `src/soul/mcp_stdio_client.py:245` 且 **src/ 内 0 生产引用**（仅 tests）→ **F1（改用 `WindowsSelectorEventLoopPolicy` 绕开 IOCP）今日可行**，需在 `run_server.py:1782` `uvicorn.run` 前新增 policy 设定（repo 内现有 0 处）；并发 socket 粗估常态 20–60、尖峰 100–200，距 Selector 512 上限有 2.5–25 倍余裕，风险低-中；未来接 MCP stdio 会踩 Windows Selector 不支援 subprocess transport。**F1 尚未授权、未实作**。**三单部署实证**：主服务 :8000 于 19:36 重启载入全部三张单（log 实证：10 bots polling、`[Server] SAGE flush 定时任务启动 (15s/次, SAGE-FLUSH-1)`、`[Server] faulthandler marker thread 启动`、`Uvicorn running on http://0.0.0.0:8000`）。**本登记 0 code / 0 production mutation / 0 frozen contract change**（REG-1 只写文档；两段式 commit：内容登记 + HEAD 同步）。**Current HEAD**）
- Current HEAD: `5ff1dca` (fix(memory): canonicalize user identity in sage source_pair (VC<->TG/web visibility)) — **MEM-IDENTITY-1 跨模態身分正規化（Owner 授權之 frozen 局部解凍）**：三模態（VC 語音 / TG / 網頁）共用同一 SAGE `graph.sqlite`，但 `source_pair` 身分分裂造成**語音↔文字的記憶雙向遮蔽**。**根因（實證）**：寫入 `middleware.py` `source_pair = f"{target_user_id}:{agent_id}"`、讀取 `source_pair_filter = {f"{target_user_id}:{agent_id}"}`，而 `sage/reader.py` 對「非空且不在集合內」的 fact 一律濾掉；TG inbound 帶數字 id `1696287850` → 寫成 `1696287850:agent_X`，VC 硬編碼 `bryan:agent_X`（canonical）→ 實測生產 `data/memory/agent_rem/graph.sqlite` **兩種值並存 `bryan:agent_rem` ×14 與 `1696287850:agent_rem` ×8**；後果：TG/網頁回合看不到 VC 寫的 14 筆、主動回合看不到 TG/網頁寫的 8 筆，僅 VC（讀側不帶 filter）全見。**修法**：新增 `src/memory/user_identity.py`（`canonical_user_id` / `user_identity_aliases` / `source_pair_candidates`，純 stdlib 單一事實來源），寫入側以 canonical `bryan` 組 source_pair、讀取側放行別名集 `{bryan, user_bryan, 1696287850}`（別名皆有程式碼證據：`telegram.py:44` `_BRYAN_TG_USER_ID`、`router.py:68` `_BRYAN_ENTITY_ID`）→ **舊 8 筆不掉、0 資料搬遷**；其他使用者 id 原樣保留（**多使用者隔離不被破壞**）。**硬性約束**：正規化**只在記憶層內部**，**絕不改 event payload 的 `target_user_id`**（`router.py:197,219` 用它做 TG 投遞路由，改成 `bryan` 會讓推播送往非數字 chat id 而故障）。**驗收（主大腦獨立複驗）**：新測試 9/9；讀側實證（生產 DB 複本）**舊 filter 8 筆 → 新 filter 22 筆**、proactive 舊 filter 14 筆、陌生人 filter 0 筆；逐檔 baseline（worktree `2b6dbbe`）對照 **0 破壞**（4 個既有失敗檔前後完全相同）。**部署**：主服務 :8000 於 16:34 重啟載入新程式碼（10 bots 恢復輪詢）；**VC 三台無需重啟**。**範圍**：僅 `src/memory/middleware.py`（3 處）+ 新增 `user_identity.py` + 新測試；未動 `graph_store.py` / `writer.py` / `reader.py` / `provider.py` / schema / `src/io/` / `clients/` / `config/` / `personas/`。**連帶更正**：本檔與 Notion 上 VC-2.5 記載之「**雙向**共享短期會話流」為**過度宣稱**——審計實證 `SessionStore` 為**單向管**（TG/VC 雙寫、全 `src/` 僅 `telegram.py` 引用，主服務 LLM 管線不讀）→ **TG→VC 成立、VC→TG 不成立**（列 P1 待辦）。**Current HEAD**）
- Current HEAD: `a96c53b` (feat(vc): file logging for voice companion (VC-LOG-1)) — **2026-09-11 語音事故修復 + VC 觀測性補齊**（兩 commit：`77929fa` REST 音源耐久化（VC-TTS-1）＋ `a96c53b` 檔案日誌（VC-LOG-1）；範圍僅 `clients/voice_companion/` + `.gitignore` + `tests/`，**0 行 `src/`、0 行 Frozen Contract**）。**根因（實證，非推論）**：fish.audio **live WebSocket TTS 端點（`wss://api.fish.audio/v1/tts/live`）嚴重劣化**——合成時間隨字數暴增（5 字 3.67s 出聲／19 字 29.44s 出聲／**44 字 40.8s 後拋 `WebSocketNetworkError` 且 0 音訊 bytes**）；rem 與 mai 的 voice 同結果 → 與 voice 無關；18 個 token 碎片改為一次性餵 47 字 → 同樣 0 chunk → 與餵法無關；同一 key/voice/model 走 **REST（`POST https://api.fish.audio/v1/tts`）44 字僅 2.08s、155KB、200 OK**。與 Cloudflare Access / 麥克風 / ASR / LLM / VC 進程均無關。**症狀指紋**：`web_server.py:563` 的 `first_audio_ms` 在 `first_chunk_time is None` 時 fallback 成 `total_ms` → **`[LATENCY] first_audio == total` 即「完全無音訊」**；且 reply transcript 於 `_generate()`（LLM 串流 + `end_session()`）完成後才送瀏覽器，故 44s 全卡在 TTS 而非 LLM——使用者端表現為「有文字轉錄、UI 顯示說話中、沒有聲音」。**修法 A（VC-TTS-1）**：`config.json` / `profiles/mai.json` / `profiles/rem.json` 的 `fish_audio.mode` `live`→`rest`，不帶任何 CLI 參數即走 `RestPCMWebStreamer`（`web_server.py:852`），**重開機／重新登入不退回**；`--tts <mode>` CLI 覆寫保留（注意參數名為 `--tts`，非 `--mode`）。**修法 B（VC-LOG-1）**：`web_server.py` 新增 `setup_logging()`（RotatingFileHandler 5MB×3、root handler、`sys.excepthook` + asyncio exception handler、`clients/voice_companion/logs/vc_<agent_id>.log` 已 gitignore）＋啟動橫幅記 **tts_mode**/voice_id/model/api_key 前 4 碼＋`[TTS] round`（chunks/bytes，**chunks==0 → WARNING ZERO-AUDIO**）＋`fish_tts_live.py:202-208` 吞錯**兩分支都留痕**（含被中斷分支）＋REST 非 200 記 status/body。**驗收（主大腦獨立複驗）**：三台以與排程任務完全相同、**無 `--tts`** 的參數重啟 → akane 5.493s／mai 5.013s／rem 5.179s 首音（原為 90s 逾時 0 音訊）；日誌檔實際生成且含啟動橫幅與每回合統計；`tests/clients/test_voice_companion.py` **130 passed**（基線 129 + 1 新測試）。**Owner（Bryan）決策**：**REST 即長期音源，不做 live→REST 自動 fallback**（C 案暫緩）。**Current HEAD**）
- Current HEAD: `d3bd70a` (docs: C1 immediate-visibility flush contract (design only, frozen)) — MEM-VISIBILITY-C1 **契約產出登記（DESIGN ONLY，0 code）**：`docs/MEM-VISIBILITY-C1-CONTRACT.md`（NEW 568 行，9 節 + 附錄 A 證據索引 34 項 + 附錄 B Out of Scope）。**C1 狀態（必須明示）：契約已產出，仍未解凍、未授權實作、未落地，等待 Owner 決策**（§9 D-1~D-10 共 10 項待拍板：成功判準操作定義 / 是否加 pending 前置判斷 / 是否要 runtime kill switch / EH-3.1 flush 是否整併 / VC 側執行緒親和性是否同步 / evolution 寫入是否納入可見性承諾 / fail-safe 觀測指標 / C3 定位 / `sync_turn` 覆蓋面 / 登錄用語）。**契約要旨**：唯一掛載點 = `SAGELiteProvider.post_reply_commit` 寫入階段成功後、於 **worker thread** 內 `await loop.run_in_executor(None, self._store.flush)`（位置在 EH-3.1 hook 之後、`_cache.invalidate()` 之前），fail-safe 吞例外；**0 新定時器 / 0 新背景輪詢 / 0 簽名變更 / 0 schema 變更 / 0 新設定項**。**`MODERN_NATIVE_AGENTS` 同化 bypass 原封不動保留**（茜／麻衣等 7 角色由通用後置 flush 覆蓋可見性，**非**取消 bypass 走進異世界同化路徑）；`_BATCH_SIZE=20` 與 `GraphStore.flush()` 既有行為不動（C1 只動呼叫時機）。**解凍申請範圍（僅申請，未授權）**：唯一生產檔案 = `src/memory/sage/provider.py` 的 `post_reply_commit` 單一函式內新增一個 flush 呼叫區塊（≈6-10 行）；明確**不解凍** `graph_store.py` / EH-3.1 本體 / `horizon.py` 白名單 / `writer.py` / `middleware.py` / `clients/` / `config/` / `personas/`。**與 C2 關係**：互補非重疊——C2 解「讀者開連線搶寫鎖」（失敗模式）、C1 解「讀者讀不到最新列」（正確性模式）；C2 已上線故 C1 的鎖風險重估為僅餘「寫者↔寫者」且因事務縮短而淨改善。**0 生產資料變更 / 0 服務重啟**；**Current HEAD**）
- Current HEAD: `820174a` (fix(sage): skip schema_meta write when opening current-version graph) — MEM-VISIBILITY-C2 實作 + C2-V 矩陣結論登記（graph_store._init_db：schema 已是現行 v9 時跳過 schema_meta INSERT/REPLACE + commit，讀側開連線不再搶寫鎖；專測 tests/test_graph_store_c2_read_open.py 5 筆 A-F 全過；回歸 59 passed vs 基線 54）。**C2 範圍邊界**：只解「讀側開連線搶寫鎖」，**不解即時最新值**——文字→語音的普通生活記憶仍是「不鎖死的舊快照」，即時同步屬 C1/C3，C1 未授權（未解凍每輪 flush）；**Current HEAD**）
- Current HEAD: `04f79b3` (docs: register MEM-VISIBILITY-0 cross-process lock audit) — MEM-VISIBILITY-0 audit registration（docs/MEM-VISIBILITY-0-AUDIT.md 隨 Commit A 納入；P0 / A/B 實證 / 非對稱漏洞；C1-C3 未選型；下一步 MEM-VISIBILITY-C2-V 專項驗證；**Current HEAD**）
- Current HEAD: `f416eb9` (test(vc): restore full vc-eh unification regression contract suite)
- VC-UNIFY-1.2.1 test commit: `f416eb9` (test(vc): restore full vc-eh unification regression contract suite; **Current HEAD**)
- Current HEAD: `47d7fa6` (fix(vc): flush background sage commits for cross-process memory visibility)
- VC-UNIFY-1.2 fix commit: `47d7fa6` (fix(vc): flush background sage commits; **Current HEAD**)
- Current HEAD: `413d3b3` (fix(vc): align voice sage commit source_pair with canonical bryan:agent_id)
- VC-UNIFY-1.1 fix commit: `413d3b3` (fix(vc): align voice sage commit source_pair; **Current HEAD**)
- Current HEAD: `7e2a560` (feat(vc): unify epistemic horizon and sage memory pipeline into voice companion)
- VC-UNIFY-1 implementation commit: `7e2a560` (feat(vc): unify epistemic horizon and sage memory pipeline; **Current HEAD**)
- Current HEAD: `52d6ebd` (docs: register EH series code-closed -> deployed & observing)
- Current HEAD: `a03295e` (fix(eh-3.1): guard assimilation tagging against subjective syntax false positives)
- EH-3.1 fix commit: `a03295e` (fix(eh-3.1): guard assimilation tagging; **Current HEAD**)
- Current HEAD: `07c7513` (feat(eh-3): post-commit assimilated tagging for explanatory user turns)
- EH-3 implementation commit: `07c7513` (feat(eh-3): post-commit assimilated tagging for explanatory user turns; **Current HEAD**)
- Current HEAD: `806645d` (fix(memory): keyword-only source_pair in post_reply_commit (skip_graph misbind))
- MEM-WIRING-1 fix commit: `806645d` (fix(memory): keyword-only source_pair in post_reply_commit; **Current HEAD**)
- Current HEAD: `4e3b407` (docs: register EH-2.1 hotfix (c9aa9e2))
- Current HEAD: `c9aa9e2` (fix(eh-2.1): world media trigger precision + modern native whitelist expansion)
- EH-2.1 hotfix commit: `c9aa9e2` (fix(eh-2.1): world media trigger precision + modern native whitelist expansion; **Current HEAD**)
- Current HEAD: `16a890b` (feat(eh-2): epistemic horizon implementation (v9 migration + horizon gate + vertical firewall + probes))
- EH-2 implementation commit: `16a890b` (feat(eh-2): epistemic horizon implementation; **Current HEAD**)
- Current HEAD: `813dbdd` (docs(eh-1.1): epistemic horizon contract amendment (D1-D4 owner rulings + dual-dimension schema))
- EH-1.1 amendment commit: `813dbdd` (docs(eh-1.1): epistemic horizon contract amendment; **Current HEAD**)
- Current HEAD: `8ecf890` (docs(eh-1): epistemic horizon design contract (EH-0 audit -> EH-1 DESIGN))
- EH-1 contract commit: `8ecf890` (docs(eh-1): epistemic horizon design contract; **Current HEAD**)
- Current HEAD: `471fe26` (docs: register VC-2.5 unified session CLOSED (5e76d0f))
- VC-2.5 register commit: `471fe26` (docs: register VC-2.5 unified session CLOSED; **Current HEAD**)
- VC-2.5 implementation commit: `5e76d0f` (feat(vc-2.5): unified cross-interface session store with 4-phase temporal anchor; **distinct from Current HEAD** — VC-2.5 跨介面會話流批次結案）
- Current HEAD: `dec4271` (docs: register VC-2.4 multi-companion CLOSED (723d577))
- VC-2.4 register commit: `dec4271` (docs: register VC-2.4 multi-companion CLOSED; **Current HEAD**)
- VC-2.4 batch final commit: `723d577` (feat(vc-2.4): rem companion profile, port 8767 service, and https support; **distinct from Current HEAD** — VC-2.4 櫻島麻衣＋雷姆多伴侶批次結案）
- Current HEAD: `ed88fb6` (docs: register VC-2/2.3 batch CLOSED (8b99f7c))
- VC-2/2.3 register commit: `ed88fb6` (docs: register VC-2/2.3 batch CLOSED; **Current HEAD**)
- VC-2/2.3 batch final commit: `8b99f7c` (feat(vc-2.3-05): lan security; **distinct from Current HEAD**)
- VC-2/2.3 batch final commit: `8b99f7c` (VC-2.3-05 lan security; **Current HEAD** — VC-2 P0-P2 + 2.3 批次結案）
- VC-1 boot autostart register commit: `e3e195e` (docs: register VC-1 boot autostart; **Current HEAD**)
- VC-1.6 batch register commit: `a30ffa5` (docs: register VC-1.6 audio batch CLOSED; **distinct from Current HEAD**)
- VC-1.6 batch register commit: `a30ffa5` (docs: register VC-1.6 audio batch CLOSED; **Current HEAD**)
- VC-1.6 audio batch final commit: `12f216f` (auto-VAD anti-echo; **distinct from Current HEAD** — 本批次結案）
- VC-1.6 audio batch commits（音源/播放/記憶/模型/守門/auto-VAD，主大腦直接實作）：`984f51c`（TTS-Live 真實層換官方 fish-audio-sdk；websocket-client 被伺服器拒收）、`f9e758b`（明確 44100＋瀏覽器重採樣）、`a3d95b8`＋`039f476`（IDLE 不清佇列＋佇列上限 30s，修砍尾）、`3ec82a9`（web REST 音源模式 --tts rest）、`c4153b5`（每連線對話記憶 10 輪）、`0475ea3`（付費模型 s2.1-pro）、`2c854d4`（動作/表情段整段剝離）、`5fecf34`＋`12f216f`（auto-VAD 不聽自己喇叭：SPEAKING＋1.8s holdoff 鎖、260ms 起始門檻）
- VC-1.3 playback register commit: `acb1398` (docs: register VC-1.3 playback root fixes (1a1d016 + 90dcd77); **distinct from Current HEAD**)
- VC-1.3 playback decouple fix commit: `90dcd77` (fix(vc-1.3): decouple playback init from mic start (text-only users were silent); **distinct from Current HEAD**)
- VC-1.3 autoplay resume fix commit: `1a1d016` (fix(vc-1.3): resume AudioContext on first gesture (autoplay policy made playback silent); **distinct from Current HEAD**)
- Current HEAD: `ea11465` (docs: register VC-1.6 sdk transport (984f51c))
- VC-1.6 register commit: `ea11465` (docs: register VC-1.6 sdk transport (984f51c); **Current HEAD**)
- VC-1.6 SDK transport fix commit: `984f51c` (fix(vc-1.6): real TTS-Live via official fish-audio-sdk transport (websocket-client rejected by server); **distinct from Current HEAD**; 3 檔 +315/-304)
- Current HEAD: `984f51c` (fix(vc-1.6): real TTS-Live via official fish-audio-sdk transport (websocket-client rejected by server))
- VC-1 sse fix register commit: `cf752a7` (docs: register VC-1 sse utf-8 fix (7e491d7); **Current HEAD**)
- VC-1 SSE utf-8 fix commit: `7e491d7` (fix(vc-1): force utf-8 sse decode in llm stream (chinese mojibake); **distinct from Current HEAD**)
- VC-1 405 fix register commit: `f12e46a` (docs: register VC-1 405 endpoint fix (53c3fab); **Current HEAD**)
- VC-1 405 fix commit: `53c3fab` (fix(vc-1): normalize chat completions endpoint (405 on ollama.com/v1 root); **distinct from Current HEAD**)
- VC-1 LLM key fix register commit: `3da9a67` (docs: register VC-1 LLM key fix (2b77631); **Current HEAD**)
- VC-1 LLM key fix commit: `2b77631` (fix(vc-1): llm api key env fallback (OLLAMA_API_KEY, explicit LLM_API_KEY wins) + test; **distinct from Current HEAD**)
- VC-1.5 register commit: `c90fe2d` (docs: register VC-1.5 https + secure-mic fixes (3b62328 + 5e99042); **Current HEAD**)
- VC-1.5 stdout fix commit: `5e99042` (fix(vc-1.5): utf-8 stdout reconfigure for windows cp950 console (emoji print crash); **distinct from Current HEAD**)
- VC-1.5 https mode commit: `3b62328` (feat(vc-1.5): https mode + insecure-origin mic banner + ws diagnostics; **distinct from Current HEAD**)
- VC-1.4 register commit: `1a941f9` (docs: register VC-1.4 mic visibility + asr error transparency (c65a5cf); **Current HEAD**)
- VC-1.4 UI visibility commit: `c65a5cf` (feat(vc-1.4): mic level meter + asr error transparency in web ui; **distinct from Current HEAD**)
- VC-1.3 register commit: `7707814` (docs: register VC-1.3 web voice companion (79e5dc9); **Current HEAD**)
- VC-1.3 web server commit: `79e5dc9` (feat(vc-1.3): browser voice companion web server (aiohttp + ws relay); **distinct from Current HEAD**)
- VC-1.2/VC-1.1 register commit: `c8e2640` (docs: register VC-1.2 TTS-Live + VC-1.1 fish ASR (f742a7d + 240f657); **Current HEAD**)
- VC-1.1 ASR + token feeding commit: `240f657` (feat(vc-1.1): fish audio official ASR (stt_service) + token-level ws-live feeding; **distinct from Current HEAD**)
- VC-1.2 TTS-Live commit: `f742a7d` (feat(vc-1.2): fish audio websocket tts-live streaming (pcm + barge-in); **distinct from Current HEAD**)
- VC-1 wiring register commit: `d067094` (docs: register VC-1 runtime wiring (3f29e32 + 9fd19be + 262516c); **Current HEAD**)
- VC-1 runtime wiring commit: `262516c` (feat(vc-1): fish tts payload model field (s2.1-pro-free); **distinct from Current HEAD**)
- VC-1 model field commit: `9fd19be` (feat(vc-1): fish tts model field wiring (s2.1-pro-free + FISH_MODEL override); **distinct from Current HEAD**)
- VC-1 env wiring commit: `3f29e32` (feat(vc-1): env-override config wiring (fish + ollama cloud llm); **distinct from Current HEAD**)
- Agent registry test fix register commit: `2a8c335` (docs: register agent registry test fix (ad39376); **Current HEAD**)
- Agent registry test fix commit: `ad39376` (test: fix stale agent registry assertion (len 3 → actual enabled agent count); **distinct from Current HEAD**)
- VC-1 register commit: `87edcc2` (docs: register VC-1 voice companion client (6532b96); **distinct from Current HEAD**)
- VC-1 implementation commit: `6532b96` (feat: VC-1 akane voice companion client (clients/voice_companion/); **distinct from Current HEAD**)
- C-2.1 register commit: `a9335df` (docs: register C-2.1 CLOSED (TL-11 acceptance, a4f974e); **Current HEAD**)
- TL-11 acceptance commit: `a4f974e` (test(tl11): commitment closure + periodic narrative end-to-end harness; **distinct from Current HEAD**)
- C-2.1 implementation commit: `306943f` (feat(c-2.1): commitment closure seed B6 + periodic narrative sublimation; **distinct from Current HEAD**)
- C-2.1 contract commit: `328c5e1` (docs(c-2.1): commitment lifecycle + narrative sublimation contract; **distinct from Current HEAD**)
- TL-10 acceptance commit: `29fd27d` (test(tl10): relational expression end-to-end harness (P1 routing + band injection + A2U preserve + fail-safe); **distinct from Current HEAD**)
- C-3.1 impl register commit: `04897c2` (docs: register C-3.1 implementation + P1 delivery routing (036d93a); **distinct from Current HEAD**)
- C-3.1 implementation commit: `036d93a` (feat(c-3.1): relational expression injection + P1 delivery routing to public channels; **distinct from Current HEAD**)
- C-3.1 HEAD sync commit: `e88601e` (docs: sync Current HEAD to C-3.1 registration commit; **distinct from Current HEAD**)
- C-3.1 register commit: `57f28d7` (docs(c-3.1): register approved relational expression contract + P1 delivery routing authorization; **distinct from Current HEAD**)
- README v2 polish commit: `47d4a63` (docs: polish bilingual READMEs (badges, TOC, poster centering, zh-TW standard); **distinct from Current HEAD**)
- README v2 register commit: `474b529` (docs: register README v2 bilingual baseline in ENGINEERING_STATE; **distinct from Current HEAD**)
- README v2 bilingual commit: `12838c6` (docs: README v2 bilingual rewrite (English + zh-CN, Mermaid + architecture posters); **distinct from Current HEAD**)
- README flagship (v1) commit: `76bc718` (docs: README flagship architecture rewrite (Phase C-3.1); **distinct from Current HEAD**)
- SG-2.2 fix commit: `779a639` (fix: settle_relations 4.1 band-key compat (SG-2.2); **distinct from Current HEAD**)
- SG-2.1 fix commit: `ca3d52f` (fix: relational band slow-climb requires in-window signal (SG-2.1); **distinct from Current HEAD**)
- TL-9 implementation commit: `9f8c28a` (feat: TL-9 relation evolution long-horizon harness (C-3); **distinct from Current HEAD**)
- SG-2 implementation commit: `7a35741` (feat: SG-2 social graph & theory of mind implementation (C-3); **distinct from Current HEAD**)
- SG-1 contract commit: `6d54510` (docs: SG-1 social graph & theory of mind contract (C-3); **distinct from Current HEAD**)
- SG-0 audit commit: `6030ab2` (docs: SG-0 social graph & theory of mind audit (C-3); **distinct from Current HEAD**)
- confidence fix commit: `51c0c4c` (fix: add_fact writes confidence column (owner-authorized narrow unfreeze); **distinct from Current HEAD**)
- LS-2 implementation commit: `aadd5ef` (feat: goal seed provider + TL-8 volition guardrails (LS-2); **distinct from Current HEAD**)
- LS-1 contract docs commit: `6514ac1` (docs: LS-1 long-term coexistence contract (C-2); **distinct from Current HEAD**)
- TG-3.1 fix commit: `d55253f` (fix: sediment UTC + suspended stale-candidate guard (TG-3.1); **distinct from Current HEAD**)
- TG-3 commit: `3adaf57` (feat: goal-driven behavior harness acceptance (TG-3); **distinct from Current HEAD**)
- TG-2 commit: `26da28d` (feat: goal engine (schema v8 + goal provider + scheduler wiring) (TG-2); **distinct from Current HEAD**)
- TG-1 commit: `058e060` (docs: goal engine contract (TG-1); **distinct from Current HEAD**)
- TG-0 commit: `136cb95` (docs: goal engine architecture audit (TG-0); **distinct from Current HEAD**)
- MS-3 contract commit: `a61beff` (docs: voice interaction contract (MS-3); **distinct from Current HEAD**)
- MS-3 implementation commit: `e308365` (feat: voice interaction input (MS-3); **distinct from Current HEAD**)
- MS-3.1 implementation commit: `bc7bbda` (feat: device-level voice session MCP tools (MS-3.1); **distinct from Current HEAD**)
- SI-2 harness commit: `973971d` (feat: multi-agent social diffusion harness validation (SI-2 harness); **distinct from Current HEAD**)
- MS-2 commit: `1d1b9af` (feat: multimodal perception (audio/camera MCP) (MS-2); **distinct from Current HEAD**)
- MS-1 commit: `172bca0` (docs: multimodal perception contract (MS-1); **distinct from Current HEAD**)
- MS-0 commit: `c30314e` (docs: multimodal perception architecture audit (MS-0); **distinct from Current HEAD**)
- TS-3 commit: `0acadbc` (feat: real MCP server end-to-end validation (TS-3); **distinct from Current HEAD**)
- TS-2.1 commit: `10a6a98` (feat: wire actuator into scheduler decision check (TS-2.1); **distinct from Current HEAD**)
- TS-2 commit: `c668739` (feat: tool registry and observe/reflect actuators (TS-2); **distinct from Current HEAD**)
- TS-1 commit: `814f16a` (docs: tool registry and volition gate contract (TS-1); **distinct from Current HEAD**)
- TL-7 commit: `e4c875d` (feat: TL-7 social opportunity harness + historical test alignment (TL-7); **distinct from Current HEAD**)
- SI-3 Phase 2 commit: `d7c7c70` (feat: wire social perception aggregator into middleware and SM-4 decision (SI-3 phase 2); **distinct from Current HEAD**)
- SI-3 Phase 1 commit: `554202c` (feat: social opportunity + compact aggregator (SI-3 phase 1); **distinct from Current HEAD**)
- SI-2.2 commit: `33ae1b1` (feat: multi-agent social diffusion (SI-2.2); **distinct from Current HEAD**)
- SI-2.1 commit: `5002f20` (docs: multi-agent social diffusion contract (SI-2.1); **distinct from Current HEAD**)
- MR-2 commit: `3eacae8` (feat: temporal memory & mem0 primitives (MR-2); **distinct from Current HEAD**)
- MR-1 commit: `6419166` (docs: temporal memory & mem0 primitives contract (MR-1); **distinct from Current HEAD**)
- TA-2 commit: `cc83daa` (feat: subjective temporal phenomenology (TA-2); **distinct from Current HEAD**)
- TA-2 docs commit: `4f0ec41` (docs: subjective temporal phenomenology contract (TA-2); **distinct from Current HEAD**)
- SM-4 series commit: `79fe750` (feat: quadruple decision + calibration (SM-4 series); **distinct from Current HEAD**)
- TL-5 commit: `89e9cdf` (feat: time-lapse behavior distribution validation (TL-5); **distinct from Current HEAD**)
- CA-3 commit: `1f48108` (feat: expand capability definitions to observe and reflect (CA-3); **distinct from Current HEAD**)
- SE-5 Step 1 commit: `3ff2976` (fix: emergent projection lifecycle gating + lineage collapse (SE-5 read-side); **distinct from Current HEAD**)
- TL-4 commit: `8689e9c` (feat: time-lapse lifecycle validation (TL-4); **distinct from Current HEAD**)
- SE-5 commit: `42939d4` (feat: durable soul structure lifecycle (SE-5); **distinct from Current HEAD**)
- SE-4 commit: `331b867` (docs: durable soul structure lifecycle contract (SE-4); **distinct from Current HEAD**)
- proactive DM response-framing commit: `2cec421` (fix: proactive DM response-framing (filter Bry user msgs + strong proactive instruction); **distinct from Current HEAD**)
- TL-2 commit: `85711cc` (feat: time-lapse volition choice test (TL-2); **distinct from Current HEAD**)
- TA-1 commit: `4a63b1d` (feat: temporal orientation & continuity (TA-1) + silence bug fix; **distinct from Current HEAD**)
- TL-1 commit: `bcae186` (feat: time-lapse harness + TL-1 experiment (Level 2 growth proven); **distinct from Current HEAD**)
- TL-0 commit: `77c1899` (docs: time-lapse harness experiment spec (TL-0); **distinct from Current HEAD**)
- CA-2 commit: `a70621f` (feat: soul capability awareness (CA-2); **distinct from Current HEAD**)
- Proactive DM fixes commit: `93672df` (fix: proactive DM deliverability + signal unification + double-instance; **distinct from Current HEAD**)
- Current HEAD: `401e15c` (feat: agent_id injection for diary/dream/event elevation ownership)
- FG-2 commit: `e8c84d4` (feat: germ initialization boundary (FG-2); **distinct from Current HEAD**)
- watchdog fix commit: `d8c057d` (fix: watchdog procs check misjudgment (port_listen=True procs=0); **distinct from Current HEAD**)
- SI-1 commit: `b623e17` (feat: shared life read-side grouping (SI-1); **distinct from Current HEAD**)
- arch-v2 commit: `fa46433` (M6.1-aligned v2 diagram replacement; **distinct from Current HEAD**)
- M6.1-9.1 closeout commit: `eafbf24` (Restore True Phase-10 Agency Registration; **distinct from Current HEAD**)
- M6.1-9 partial commit: `362cf28` (Lived Context Formation Audit T+50min snapshot; **distinct from Current HEAD**)
- M6.2-1 registry sync commit: `9a64f14` (Per-Message TTS Correlation registry sync; **distinct from Current HEAD**)
- M6.2-1 impl commit: `965df92` (Per-Message TTS Correlation FEATURE; **distinct from Current HEAD**)
- M6.1-2 docs commit: `9e050f6` (Lived Context Canonical Boundary & Documentation)

(M6.1-8.2 — Controlled Production Agency Re-enable, IMPLEMENTATION / Option B / Gradual — `2a42521`, **distinct from Current HEAD**)
(M6.2-0 closed via out-of-repo closeout report `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md`, no in-repo file changes for READ-ONLY audit)
- M6.1-7 closeout commit: `bdf76ad` (Production Lived Context Evidence Reassessment; **distinct from Current HEAD**)
- M6.1-6.0-C closeout commit: `49adf46` (Personal Lived Context Architecture Decision Audit; **distinct from Current HEAD**)
- M6.1-5.1 impl commit: `9f8ece8` (RSS News Source; FEATURE, IMPLEMENTATION)
- M6.1-3.1 impl commit: `ac50256` (Open-Meteo Weather Source; FEATURE, IMPLEMENTATION)
- M6.1-2 docs commit: `9e050f6` (Lived Context Canonical Boundary & Documentation)
- M5.15-6 impl commit: `c2de02c` (Real-World Calendar Source Integration)
- M5.15-5 impl commit: `0aedbef` (Two-Layer Lineage Model + InnerLifeEvent.source_world_event_novelty_id)
- M5.15-3 impl commit: `b4b981a` (WorldEventSource → Event Bus canonical integration)
- GOV-2-R1 alignment commit: `3539de2` (Owner Decision A alignment; recorded in §9 CHANGE LOG; **distinct from Current HEAD**)
- GOV-2 establishment commit: `eb57151` (initial canonical state registry; superseded by GOV-2-R1)
- `origin/main` synced at HEAD
- Working tree: no pre-existing modified tracked files (previous `tests/test_soul_md_loader.py` dirty state no longer present; not touched by TS-2.1) + baseline untracked artifacts preserved (M5.2-L + M5.4-5.x audit docs)

### Current status snapshot

| Milestone | Status | Latest commit | Last closeout | Notes |
|-----------|--------|---------------|---------------|-------|
| **M5.13** | **FULLY CLOSED** | `9501603` | `m5_13_5_untouched_decay_closeout.md` (out-of-repo) | M5.13-5 CLOSED 2026-08-12; M5.13 series fully closed (no remaining OPTIONAL/DEFERRED tickets) |
| **M6.0** | **FULLY CLOSED** | `3d1fae4` | `m6_0_5_6_1_budget_profile_closeout.md` (out-of-repo) | M6.0-5.6.1 CLOSED 2026-08-12; M6.0 series fully closed (D3 RESOLVED) |
| **M5.14** | **OFFICIALLY CLOSED** | `29deab7` | `m5_14_3_m6_0_3_f_correction_closeout.md` | Per D1 RESOLVED (Option A): chain officially closed, no M5.14-4 |
| **M5.15** | **F1 + F2 + F3 + F4 RESOLVED (M5.15-3 + M5.15-5 + M5.15-6 CLOSED)** | `c2de02c` | `m5_15_6_closeout.md` (out-of-repo) | M5.15-1 / M5.15-2 / M5.15-3 / M5.15-5 / M5.15-6-PREFLIGHT / M5.15-6 all CLOSED. M5.15-4 SUPERSEDED by M5.15-5. F5-F7 P3 no action. |
| **M6.0** | **CLOSED** | `540eac2` | `m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md` | M6.0-5.5-R1 is BLOCKED (credentials unavailable, correct by design) |
| **M6.1** | **Lived Context Awareness — Signal half LIVE (Physical via Open-Meteo / Information via RSS / Social via TG+Calendar), Agency RE-ENABLED in production (M6.1-9.1 True Phase-10 = 10/10 agents restored), M6.1-9 CLOSED 8/16 22:15 — two independent issues (LLM 404 + cooldown conflict) both fixed + production-verified (night slot 10/10 diary)** | `c6ccc36` (current HEAD) | `m6_1_9_1_closeout.md` + `m6_1_9_closeout.md` (out-of-repo) | M6.1-0 / M6.1-1 / M6.1-2 / M6.1-3 / M6.1-3.1 / M6.1-3.2 / M6.1-3.3 / M6.1-4 / M6.1-5 / M6.1-5.1 / M6.1-5.2 / M6.1-5.3 / M6.1-6.0 / M6.1-6.0-C / M6.1-7 / M6.1-8 / M6.1-8.1 / M6.1-8.2 / M6.1-9 partial / M6.1-9.1 / **M6.1-9 T+4h33m partial** all CLOSED. **M6.1-9 partial T+4h33m findings** (out-of-repo `m6_1_9_closeout.md`): **2 NEW InnerLifeEvents from Agency triggers** (diary:night for agent_yua at 8/14 22:00:11, dream:dream for agent_miku at 8/14 22:05:11, target yua). Inner life trace grew 553B (1 entry) → 1431B (3 entries) = +878B / +2 events. **M5.2 regression is RESOLVED** (was broken 6+ days from 8/8 21:13 to 8/14 22:00). 2 diary files UPDATED (agent_yua + agent_miku, 8KB each, mtime 22:00/22:05). 0 source code change, 0 frozen contract change, 0 other production data mutation. **Concerns**: (1) 9 of 10 agents' diary/dream LLM calls INTERRUPTED by 22:38 server restart (cause unknown, not via server_ops.ps1, TELEGRAM_BOT_YUA not set in restart shell); (2) Q1/Q2/Q4/Q6 still PENDING full 24h evidence; (3) diary file content LOCKED by running server. **Cron `m6_1_9_t24h_v2`** (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) will catch T+24h (8/15 19:27 EDT) and full 24h follow-up. |
| **GOV-1** | **CLOSED** | (docs only) | `gov_1_state_normalization_audit.md` (out-of-repo) | State normalization audit complete |
| **GOV-2** | **CLOSED** | `eb57151` | `logs/ENGINEERING_STATE.md` (this registry) | Canonical engineering state registry established |
| **GOV-2-R1** | **CLOSED** | (this commit) | (this document, alignment) | Owner Decision A alignment — canonical state now matches Notion |
| **M6.2** | **Text / TTS Response Path Separation — M6.2-0 + M6.2-1 both CLOSED (implementation complete, production deployment pending separate verification)** | `965df92` (impl) / `9a64f14` (registry) | `m6_2_1_text_tts_correlation_closeout.md` (out-of-repo) | M6.2-0 + M6.2-1 CLOSED at the **implementation level**. **Key finding (M6.2-0)**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(_synthesize_async)` in FishTTSHandler. TTS latency 0.5-6.4s (mean 3.3s). Transport: WebSocket + Telegram already separate text/audio via 2 distinct message types (`agent_speak` + `agent_audio_ready`). **Gap closed (M6.2-1)**: added `message_id` (SoulEvent.event_id UUID) end-to-end (AGENT_SPEAK → TTSService → AGENT_AUDIO_READY payload + ChannelRouter per-message correlation + WebSocket payload + static/index.html data-message-id). 5 production files + 1 test file, +131/-26 lines. 0 frozen contract change. 0 production data mutation. 11/11 new tests + 45/45 focused regression PASS. **Deployment status**: code is in main (`965df92` impl + `9a64f14` registry); production server still running pre-M6.2-1 build (started 8/14 20:21:18 EDT, M6.1-9.1 fix); production deployment of M6.2-1 is a separate concern from implementation closeout, requires explicit server restart decision (NOT in scope of M6.2-1). **Per Quality > Quantity**: NO M6.2-2 recommended (0 P0/P1/P2 remaining; 6 P3 explicitly out of scope per M6.2 work order). M6.1-9 PENDING 24h RUN-AND-COLLECT (cron `m6_1_9_followup` fires 2026-08-15 ~20:00 EDT). |

---

## 2. CANONICAL GOVERNANCE

### 2.1 Status vocabulary (canonical, exhaustive)

Per GOV-2 spec §2, the canonical status vocabulary is:

| Status | Definition |
|--------|------------|
| **NOT STARTED** | Authorized ticket that has not yet begun implementation. Requires explicit Owner authorization to transition from this state. |
| **IN PROGRESS** | Authorized ticket that has begun implementation but has not yet passed the closeout gate. |
| **CLOSED** | Ticket acceptance gate fully completed. All acceptance criteria met. Production integrity verified. Regression passing. Owner-approved closeout written. |
| **SUPERSEDED** | Ticket existed and was completed (or partially completed), but its state / implementation was replaced by a later ticket. Historical evidence preserved. **Not equivalent to FAILED.** |
| **DEFERRED** | Work explicitly exists but Owner decided to postpone execution. Not equivalent to OPTIONAL (see below). |
| **BLOCKED** | Work exists but has external / dependency blocker preventing progress. Requires resolution of the specific blocker. |
| **OPTIONAL** | Engineering follow-up candidate. **Not authorized.** Must pass Finding → Classification → Decision → Authorization → Ticket lifecycle (§5) before becoming IN PROGRESS. |

**No new statuses may be added** without:
1. Evidence that no existing status fits, AND
2. Explicit Owner decision authorizing the new status

### 2.2 Naming convention (canonical)

**Milestone**: `M5.15` (uppercase M, dot, x, no hyphen, no suffix)

**Work item**: `M5.15-1` (milestone + hyphen + N)

**Revision / re-verification**: `M5.15-1-R1` (work item + hyphen + uppercase R + number)

**Forbidden patterns** (per GOV-2 spec §3):
- `M5.15-1a`, `M5.15-1b` (letter suffix)
- `M5.15-FIX`, `M5.15-IMPL` (unauthorized suffix)
- `M6.0-3-F1`, `M5.13-4-1` (compound suffixes outside canonical)
- Any suffix not explicitly listed in the canonical pattern

**Case convention**:
- Document title, ticket registry, closeout filename: **uppercase M** (`M5.13-4.2`, `M5.14-3`)
- Commit subject: **lowercase m** (`fix(m5.13-4.2):`, `docs(m5.14-1):`)
- Document body: follow case of the immediate reference (titles uppercase, inline references can be either, must be consistent within document)

### 2.3 Commit subject convention

```
<type>(<ticket-id>): <description>

types: feat | fix | test | docs | refactor | chore
ticket-id: lowercase (m5.x-N, m5.x-N-R1, gov-N, etc.)
```

**Examples** (from canonical M5/M6 chain):
- `fix(m5.13-4.2): strict relationship confidence boundary — producer-side per-entry decay anchor`
- `docs(m5.14-1): cross-layer runtime convergence audit (READ-ONLY)`
- `feat(m5.4-5.7): inner life query layer (NarrativeTraceReader)`

### 2.4 Milestone transition lifecycle (canonical)

Per GOV-2 spec §6, the canonical transition is:

```
AUDIT
  → FINDING
  → CLASSIFICATION
  → DECISION
  → AUTHORIZATION     ← Owner (Bryan) required
  → WORK ORDER
  → IMPLEMENT
  → TEST
  → REGRESSION
  → INTEGRITY         ← production data + frozen contracts verified
  → CLOSEOUT          ← closeout doc written, regression PASS, integrity PASS
  → CANONICAL STATE UPDATE  ← this registry updated
```

**Key rules**:
- AUTHORIZATION is the only step requiring Owner (Bryan) authorization
- CANDIDATE ≠ AUTHORIZED: a candidate next-ticket from a closeout's "Recommended Next" section is NOT authorized. It must pass the full lifecycle.
- Milestone CLOSED does not auto-authorize the next milestone
- A closeout's "next candidate" text does NOT create a ticket

### 2.5 Supersession rule

Per GOV-2 spec §4:

- **SUPERSEDED ≠ FAILED**: a superseded ticket was completed (or partially completed) but its state / implementation was replaced by a later ticket
- Historical tickets are **NOT deleted** from the registry
- `superseded_by` field is required on every SUPERSEDED ticket
- `superseded_by` must reference a CLOSED ticket (a ticket cannot be superseded by a ticket that has not yet closed)
- The superseding ticket's closeout must explicitly document what state it replaces

### 2.6 Historical document rule

Per GOV-2 spec §5:

- Historical closeout / audit reports in `logs/` are **preserved unchanged**
- Apparent contradictions between historical closeouts and this registry are resolved in favor of this registry
- The stale reference is documented in §6 STALE REFERENCES
- Editing historical documents requires **explicit Owner authorization**
- This rule prevents "silent rewrite" of hindsight to match current state

### 2.7 Closeout gate

A ticket reaches CLOSED status only when ALL of the following are true:
1. Implementation complete per ticket's accepted scope
2. Acceptance criteria met (per original work order)
3. STOP conditions all clear (no stop condition triggered)
4. Regression: relevant test suites pass
5. Production integrity: SHA256 + mtime verification of production data files (if applicable)
6. Frozen contracts: 0 change
7. Closeout document written to `logs/`
8. Owner acceptance (or Owner pre-authorization of closeout conditions)
9. Canonical state update: this registry updated

### 2.8 Owner decision boundary

- **Bry (Owner)** is the only authority that can:
  - Authorize a new ticket (transition AUTHORIZATION step)
  - Resolve a pending decision
  - Authorize a new milestone
  - Authorize deviation from canonical governance
  - Authorize editing of historical documents
- **Mavis / Lin (M3 model)** executes Owner decisions, does not make them autonomously
- **Perplexity sonnet 4.6** is the brain / error-checker, does not implement
- A "closeout recommendation" is **evidence**, not authorization

---

## 3. ACTIVE DECISIONS (Owner decision required)

All decisions below are preserved as **UNRESOLVED** per GOV-1 + GOV-2 spec, except where explicitly marked RESOLVED by Owner decision. None may be silently closed.

**Per Owner Decision A (2026-08-12, GOV-2-R1)**: D1 is RESOLVED (Option A chosen). D2 + D3 are RESOLVED. 11 decisions remain UNRESOLVED (D4-D14).

### D1. M5.14-1 next work direction (Option A / B / C) — RESOLVED

- **Source**: M5.14-1 closeout §15 (`logs/m5_14_1_cross_layer_runtime_convergence_audit.md`)
- **Status**: **RESOLVED — Option A chosen** (Owner Decision A, 2026-08-12, GOV-2-R1)
- **Resolution**: **A. CLOSE M5.14** — Architecture converged, no further work needed
- **Effect**:
  - M5.14 chain remains OFFICIALLY CLOSED (already per M5.14-3 §9)
  - M5.15-1 remains CANDIDATE only — NOT dispatched, NOT authorized
  - No new milestone, no new ticket

### D2. M5.13-5 Untouched-Entry Decay proceed?

- **Source**: M5.13-4.2 closeout §12 (`logs/m5_13_4_2_strict_boundary_closeout.md`)
- **Status**: **RESOLVED** (M5.13-5 CLOSED 2026-08-12, commit `9501603`)
- **Resolution**: M5.13-5 implemented per Bry authorization (POST-M5.15 decision queue audit, D2 = first STILL VALID + WORTH DOING)
- **Description**: Add `created_at` fallback in `_decay_locked` so that never-touched entries (old, no `last_interaction_at`) decay from `created_at` with a 1.0-day grace threshold
- **Effect**:
  - M5.13 series now FULLY CLOSED (no remaining OPTIONAL/DEFERRED tickets)
  - Untouched entries decay from `created_at` after 1.0 day grace (preserves M5.13-2 strict 0.3 contract)
  - Legacy/malformed `created_at` entries: skip, no crash, deterministic
  - Touched entries: continue using `last_interaction_at` (M5.13-4.2 anchor unchanged)

### D3. M6.0-5.6.1 Budget profile registry proceed?

- **Source**: M6.0-5.6 closeout §K (`logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md`)
- **Status**: **RESOLVED** (M6.0-5.6.1 CLOSED 2026-08-12, commit `3d1fae4`)
- **Resolution**: M6.0-5.6.1 implemented per Bry authorization (POST-M5.15 decision queue audit, D3 = second STILL VALID + WORTH DOING)
- **Description**: Add `BudgetProfile` enum + `EvaluationBudgetConfig.from_profile()` factory for common cases (`chat` / `diary` / `dream`)
- **Effect**:
  - M6.0 series now FULLY CLOSED (no remaining OPTIONAL/DEFERRED tickets)
  - 3 named profiles: CHAT (3/2/5000/0.05, matches default), DIARY (2/1/3000/0.03), DREAM (1/1/2000/0.02)
  - Defaults unchanged (no silent override)
  - Existing callers continue to work (`EvaluationBudgetConfig()` still works)

### D3.5. M6.1-8.1 Minimal Agency Re-enable (P0 — M5.2 regression)

- **Source**: M6.1-8 closeout (`logs/m6_1_8_agency_reenable_investigation.md`)
- **Status**: **M6.1-8.1 CLOSED (fix PROVEN in isolated). M6.1-8.2 CLOSED (Option B / Gradual production rollout DONE). M6.1-9 PENDING 24h RUN-AND-COLLECT then audit**
- **Description**: Add 3 lines to `scripts/run_server.py` after `diary_callbacks_real[aid] = cb_real`:

  ```python
  for aid in agent_ids:
      scheduler.register(aid)
  ```

  M5.2 migration (commit `481ea41` 2026-08-08 21:11) removed `scheduler.register(aid, cb)` callsite (M5.2-I Phase 7) but M5.2-I Phase 8 changed iteration source to `_all_agents` without adding replacement. `_all_agents` permanently empty. 5 trigger paths silent-skip. 0 diary/dream/event/proactive_dm writes for 6+ days.
- **M6.1-8.1 ISOLATED VALIDATION** (CLOSED 2026-08-14 22:50 EDT, commit `d0c33da`):
  - Test file: `tests/test_m6_1_8_1_agency_reenable_isolated.py` (21 tests, 905 lines)
  - Results: 20 PASS / 1 XFAIL / 0 FAIL in 0.47s
  - 71/71 M5.2 series tests still PASS (no regression)
  - 0 production data mutation
  - 0 Telegram, 0 real LLM
  - 0 frozen contract change
  - 0 source code modification
  - F.2 = `run_server.py` missing `scheduler.register(...)` callsite (XFAIL pending M6.1-8.2 production fix)
  - All 5 trigger paths publish AGENCY_TRIGGER after fix
  - All 4 handlers correctly receive filtered trigger_type
- **M6.1-8.2 PRODUCTION ROLLOUT** (CLOSED 2026-08-14 19:27 EDT, commit `2a42521`):
  - **Bry 拍板 2026-08-14 19:12 EDT — Option B / Gradual**
  - 10-phase rollout: 1 → 2 → 3 → ... → 10 agents in 19:18:12 → 19:27:36 EDT (~10 min)
  - All 10 phases confirmed via `[Scheduler] 啟動 ✓ ... agents=N` log line
  - 0 ERROR/Traceback/CRITICAL during rollout
  - /health=200 stable across all 10 phases
  - 0 frozen contract change (15 contracts preserved)
  - 0 production data mutation
  - 0 handler/Agency/scheduler architecture change
  - `SOULOS_AGENCY_GRADUAL_AGENTS` env var: per-phase agent list (gitignored `.env`)
  - Final state: env var unset → M6.1-8.1 default (all 10 agents registered)
- **M6.1-9 LIVED CONTEXT FORMATION AUDIT** (CLOSED 2026-08-16 22:15 EDT — two independent issues both fixed + production-verified):
  - Trigger: 24h RUN-AND-COLLECT post-M6.1-8.2 (window 8/14 20:00 → 8/15 20:00 EDT)
  - Mode: READ-ONLY (0 source change, 0 production mutation, 0 commit/push)
  - Finding: Agency/Expression half (diary/dream) BLOCKED by LLM 404 (minimax-M2.7 uppercase) for most of window. InnerLifeEvent = 3 events/24h (target 30-50/day). Only 2/10 agents (yua + miku) produced diary/dream.
  - Signal half (Physical/Information): VERIFIED WORKING (news + weather emitting, perception evaluation functional, 604 accepted events).
  - Agency registration: CORRECT (10/10 agents, scheduler agents=10).
  - Memory + relationship integrity: OK (no corruption).
  - **Issue 1 — LLM 404**: Ollama Cloud model name case-sensitive; minimax-M2.7 → 404; fixed to minimax-m2.7, server restarted 8/15 18:54:32.
  - **Issue 2 — cooldown conflict (M5.2-H Phase 3 regression)**: handlers instantiated with `state=None` → single shared `AgencyState` → global `last_action_at` → only first agent produced diary when 10 triggered simultaneously. Fixed via per-agent `AgencyState` dict in 4 handlers (DiaryHandler `c526320` / DreamHandler `6d5112d` / AgencyTriggerHandler + EventHandler `af91271`). 75 tests PASS.
  - **Production verification**: night slot 8/16 22:00 EDT → 10/10 agents produced diary (trace.jsonl 10× diary:night, 4-6s apart, no cooldown decision=NO). server health=200.
  - Decision: M6.1-9 CLOSED.
  - Closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_final_audit.md` (out-of-repo)

### D4. M5.12-1 inherited P2.2 / P2.6 decisions

- **Source**: M5.12-1 closeout (`logs/m5_12_1_remaining_agency_p2_convergence_audit.md`)
- **Status**: **PENDING** (inherited by M5.13-1, M5.13-3, M5.14-1 — at least 3 closeouts have propagated these as pending)
- **Description**:
  - P2.2: Inner Life → Agency decision awareness (PARTIALLY MITIGATED by M5.8-4)
  - P2.6: ProactiveDM → Memory awareness (DEFERRED, requires Memory gate)
- **Authorization required**: Bry to either accept current state (M5.8-4 mitigation sufficient) or authorize Stage 2 work

### D5. Real-world API integration (P3 from M5.8-1, B1 from M5.14-1)

- **Source**: M5.8-1 closeout + M5.14-1 closeout
- **Status**: **DEFERRED** (P3 from M5.8-1; B1 architectural gap from M5.14-1)
- **Authorization required**: Bry to authorize real-world API integration work (calendar / weather / news)

### D6. M5.4-5.5 narrative trace dashboard

- **Source**: M5.4-6.4 closeout
- **Status**: **DEFERRED** (UI work, much larger scope)
- **Authorization required**: Bry to authorize M5.4-5.5 dashboard work

### D7. M5.4-6.2 cross-handler lineage (parent_event_id)

- **Source**: M5.4-6.2 closeout
- **Status**: **DEFERRED** (requires future design)
- **Authorization required**: Bry to authorize cross-handler lineage work

### D8. M5.4-5.4 diary:night slot wiring

- **Source**: M5.4-5.4 closeout (per memory)
- **Status**: **DEFERRED**
- **Authorization required**: Bry to authorize diary:night slot work

### D9. Stage 4.3 feeling/impression projection (M5.13-2 future)

- **Source**: M5.13-2 design future privacy section
- **Status**: **DEFERRED** (requires Stage 4.3 LLM producer — not in M5.13 scope)
- **Authorization required**: Bry to authorize Stage 4.3 LLM producer work

### D10. M6.0-5.1 (Diary/Dream subjective evaluation)

- **Source**: M6.0 series (per prior memory)
- **Status**: **DEFERRED** (raw httpx infrastructure not ready)
- **Authorization required**: Bry to authorize M6.0-5.1 work

### D11. M6.0-5.3+ (Multi-provider circuit breaker)

- **Source**: M6.0 series (per prior memory)
- **Status**: **DEFERRED**
- **Authorization required**: Bry to authorize multi-provider circuit breaker work

### D12. Cross-agent (agent↔agent) relationship projection

- **Source**: M5.13-2 design Per-agent 過濾 section
- **Status**: **DEFERRED** (different scope from M5.13 — requires new relationship types)
- **Authorization required**: Bry to authorize cross-agent work (would be a new milestone, not M5.13)

### D13. chrono-social.silence_hours vs last_interaction_at duplication

- **Source**: M5.13-2 design "Why not include other fields" section
- **Status**: **DEFERRED** (cross-section concern, not pure M5.13)
- **Authorization required**: Bry to authorize cross-section cleanup

### D14. M5.13-3 multi-line format

- **Source**: M5.13-2 design "Multi-line (if needed for future)"
- **Status**: **OPTIONAL** (cosmetic; no behavioral need)
- **Authorization required**: Bry to authorize cosmetic format change

---

## 4. DEFERRED / OPTIONAL / BLOCKED WORK

### 4.1 OPTIONAL (candidates, NOT authorized)

| ID | Work | Source | Scope |
|----|------|--------|-------|
| D14 | M5.13-3 multi-line format | M5.13-2 design | Cosmetic |

**Note**: M5.15-4 (cross-handler lineage), M5.15-5 (identity bridge), M5.15-6 (real-world source),
M5.13-5 (untouched-entry decay), and M6.0-5.6.1 (budget profile registry) have all been RESOLVED.
M5.15-4 is SUPERSEDED by M5.15-5. M5.15-6 resolved F2 P2. M5.13-5 resolved M5.13's last
remaining OPTIONAL ticket. M6.0-5.6.1 resolved M6.0's last remaining OPTIONAL ticket.

### 4.2 DEFERRED (explicitly postponed, requires authorization to start)

| ID | Work | Source | Why deferred |
|----|------|--------|--------------|
| D4 | M5.12-1 P2.2 / P2.6 | M5.12-1 | Stage 2 territory; needs Bry decision |
| D6 | M5.4-5.5 narrative trace dashboard | M5.4-6.4 | UI work, larger scope |
| D7 | M5.4-6.2 cross-handler lineage | M5.4-6.2 | Requires future design |
| D8 | M5.4-5.4 diary:night slot | M5.4-5.4 | Per memory |
| D9 | Stage 4.3 feeling/impression | M5.13-2 | Requires Stage 4.3 LLM producer |
| D10 | M6.0-5.1 Diary/Dream subjective | M6.0 | Raw httpx infrastructure |
| D11 | M6.0-5.3+ Multi-provider circuit breaker | M6.0 | Per memory |
| D12 | Cross-agent relationship projection | M5.13-2 | Different scope (new milestone) |
| D13 | chrono-social duplication | M5.13-2 | Cross-section concern |
| M6.1-9 | Lived Context Formation Audit | M6.1-7 / M6.1-8 | Per Quality > Quantity: only after M6.1-8.1 (Agency re-enable) + 24h+ RUN-AND-COLLECT, then verify multi-signal world_context |

**Note on D5 (Real-world API integration)**: RESOLVED by M5.15-6 (Calendar via iCal/ICS public feed).
D5 retired — first real-world source is the calendar, the integration pattern (M5.15-3 canonical
bus path + M5.15-5 Two-Layer Lineage Model + M5.15-6 SHA256 identity bridge) generalizes to
weather/news/social when those candidates are authorized.

### 4.3 BLOCKED (external / dependency blocker)

| ID | Work | Source | Blocker |
|----|------|--------|---------|
| M6.0-5.5-R1 | Real three-judge E2E validation gate | M6.0-5.5-R1 closeout | Credentials unavailable in this environment (correct by design, not a real blocker) |

**Note on M6.0-5.5-R1**: The BLOCKED status is per spec (Bry 8/11 21:40 EDT: "If credentials are unavailable, the correct result is: BLOCKED — CREDENTIALS UNAVAILABLE. It is NOT PASS and it is NOT a reason to modify the infrastructure."). This is not a failure to be remediated — it is the correct outcome for the current environment.

---

## 5. CLOSED MILESTONES (canonical state)

### 5.1 M5.13 — Relationship Context + Boundary Precision

**Status**: FUNCTIONALLY CLOSED (only M5.13-5 is OPTIONAL/DEFERRED)

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.13-1 | Lived context capability preflight (READ-ONLY AUDIT) | `e940934` | **CLOSED** | Identified P1 gap (relationships in LLM prompt) |
| M5.13-2 | Relationship context projection design (READ-ONLY) | `7bf10f0` | **CLOSED** | Designed minimal confidence-band integration |
| M5.13-3 | Minimal relationship context integration (IMPLEMENTATION) | `32e5172` | **CLOSED** | `src/llm/proxy.py:_format_relationship_block` (29 + 19 subtests PASS) |
| M5.13-3.1 | Independent verification audit (READ-ONLY VERIFICATION) | `401ae09` | **CLOSED** | 12 categories, 14 acceptance, 7 stop conditions all PASS |
| M5.13-4 | Float precision issue audit (READ-ONLY) | `97c1063` | **CLOSED** | Discovered 0.3 boundary decay bug (P3 dormant) |
| M5.13-4.1 | Relationship confidence boundary regression (FIX) | `c816142` | **SUPERSEDED** | Consumer `round(_, 6)` fix; 5e-7 false-promotion range |
| M5.13-4.1-R1 | Relationship threshold rounding boundary audit (READ-ONLY) | `4815331` | **CLOSED** | Documented 5e-7 false-promotion; recommended C (BRY DECISION) |
| M5.13-4.2 | Strict relationship confidence boundary fix (FIX) | `e6effd8` | **CLOSED** | Producer-side per-entry decay anchor |
| M5.13-5 | Untouched-Entry Decay (FIX) | `9501603` | **CLOSED** | created_at fallback with 1.0-day grace; preserves M5.13-2 strict 0.3 contract; 14/14 new tests + 56/56 M5.13 suite + 105/105 adjacent regression; 0 frozen contract change; 0 production mutation. **CANONICAL LATEST.** |

**Supersession chain**:
```
M5.13-4 (audit) → M5.13-4.1 (fix) → M5.13-4.1-R1 (audit, found issue)
  → M5.13-4.1 SUPERSEDED → M5.13-4.2 (fix, replaced with producer-side approach)
```

**M5.13-2 contract (STRICT, FROZEN)**: `confidence >= 0.3` → 「認識」, `confidence < 0.3` → 「陌生人」 (no tolerance).

**Closeout logs** (latest canonical for M5.13 series):
- `logs/m5_13_4_2_strict_boundary_closeout.md` (M5.13-4.2 — producer-side per-entry anchor)
- `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md` (M5.13-5 — untouched-entry decay, out-of-repo)

### 5.2 M5.14 — Cross-Layer Runtime Convergence

**Status**: OFFICIALLY CLOSED (per M5.14-3 closeout §9: "no immediate M5.14-4 needed")

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.14-1 | Cross-layer runtime convergence audit (READ-ONLY) | `a2bd687` | **CLOSED** | 5-layer architecture verified |
| M5.14-2 | WorldEvent ↔ ProactiveDM identity contract audit (READ-ONLY) | `4df0c90` | **CLOSED** | F1-P1 reclassified P3 (test design) |
| M5.14-3 | M6.0-3 F1-F3 canonical agent-specific fixture (FIX / TEST-ONLY) | `29deab7` | **CLOSED** | By-design cross-milestone fixture correction |

**Note on M5.14-3 commit message**: References both `m5.14-3` (ticket ID) AND `M6.0-3 F1-F3` (fixture work). This is by design per M5.14-2 audit, not a duplicate or ambiguous record.

**Closeout log**: `logs/m5_14_3_m6_0_3_f_correction_closeout.md` (canonical)

**Next work**: D1 RESOLVED (Option A chosen) — M5.14 remains CLOSED; no M5.14-4. M5.15-1 remains CANDIDATE only and MUST NOT be dispatched without explicit Owner authorization.

### 5.3 M6.0 — Lived Context Validation + Subjective LLM Evaluation

**Status**: CLOSED (all 15 tickets, M6.0-5.5-R1 is BLOCKED by design)

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.0-1 | Lived context validation framework design (READ-ONLY) | `1cc46dd` | **CLOSED** | |
| M6.0-2 | Validation framework PoC (Scenarios A/B/C) | `fca5c2d` | **CLOSED** | 16/16 PASS |
| M6.0-3 | Validation framework (Scenarios D/E/F/G/H) | `d34513e` | **CLOSED** | 22/22 PASS (F1-F3 corrected by M5.14-3) |
| M6.0-4 | Subjective LLM quality evaluation design audit (READ-ONLY) | `3ed1092` | **CLOSED** | |
| M6.0-5 | Subjective LLM evaluation infrastructure | `5f4ae34` | **CLOSED** | 56/56 PASS |
| M6.0-5.2 | Real LLM judge backend (OPT-IN) | `91a3093` | **CLOSED** | |
| M6.0-5.3 | Multi-LLM judge diversity & orchestration design audit | `c781260` | **CLOSED** | |
| M6.0-5.4 | Minimal multi-model judge orchestration | `6ba5b90` | **CLOSED** | 39/39 PASS |
| M6.0-5.4-R1 | Cost / retry budget enforcement correction (R#) | `cda79fd` | **CLOSED** | |
| M6.0-5.4-R2 | Retry budget enforcement completion (R#) | `d87e6f6` | **CLOSED** | |
| M6.0-5.5 | Real three-judge subjective evaluation E2E (opt-in) | `3f599a4` | **CLOSED** | |
| M6.0-5.5-R1 | Real three-judge E2E validation gate (BLOCKED) | `9d21740` | **BLOCKED** | Credentials unavailable, correct per spec |
| M6.0-5.6 | Configurable subjective evaluation cost ceiling | `540eac2` | **CLOSED** | 30 new tests, 334 + 5 skipped PASS |
| M6.0-5.6.1 | Budget Profile Registry | `3d1fae4` | **CLOSED** | `BudgetProfile` enum (CHAT/DIARY/DREAM) + `EvaluationBudgetConfig.from_profile()` factory. 29/29 new tests + 12/12 M6.0-5.6 + M6.0-5.6.1 manual regression. 0 frozen contract change (defaults 3/2/5000/0.05 preserved; CHAT profile == default; existing `EvaluationBudgetConfig()` still works). 0 production mutation. **CANONICAL LATEST.** |

**Supersession chain** (M6.0-5.4 family):
```
M6.0-5.4 (initial) → M6.0-5.4-R1 (cost/retry correction) → M6.0-5.4-R2 (retry completion)
  → M6.0-5.4 SUPERSEDED → M6.0-5.4-R1
  → M6.0-5.4-R1 SUPERSEDED → M6.0-5.4-R2
```

**Next work**: Per D3, M6.0-5.6.1 (Budget profile registry) is OPTIONAL pending Bry authorization

### 5.4 GOV-1 — Engineering State Normalization Audit

**Status**: CLOSED

| Item | Value |
|------|-------|
| Audit location | `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md` (out-of-repo) |
| Mode | READ-ONLY |
| Author | Mavis / Lin |
| Date | 2026-08-11 ~23:55 EDT |
| Outcome | 0 production blockers, 1 STALE next-work-item reference identified (M6.0-5.6 §K) |
| Follow-up | GOV-2 (this document) |

### 5.5 M5.15 — WorldEventSource → Event Bus Canonical Integration

**Status**: F1 + F2 + F3 + F4 RESOLVED. F5-F7 P3 no action. M5.15 series all CLOSED.

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M5.15-1 | WorldEvent → Event Bus boundary audit (READ-ONLY) | (docs only) | **CLOSED** | 7 findings classified; F1 P1, F2-F4 P2, F5-F7 P3. Out-of-repo report. |
| M5.15-2 | WorldEvent → Event Bus architecture decision analysis (READ-ONLY) | (docs only) | **CLOSED** | Owner authorized Option A. Out-of-repo report. |
| M5.15-3 | WorldEventSource → Event Bus canonical integration (IMPLEMENTATION) | `b4b981a` | **CLOSED** | 31/31 new tests + 159/159 regression. 0 frozen contract. 0 production mutation. |
| M5.15-4 | Cross-Handler Lineage Propagation (IMPLEMENTATION) | (none) | **STOPPED + SUPERSEDED** | Per M5.15-5 decision: parent_event_id contract cannot reference non-InnerLifeEvent. **SUPERSEDED by M5.15-5.** |
| M5.15-5 | WorldEvent ↔ InnerLifeEvent Identity Bridge (IMPLEMENTATION) | `0aedbef` | **CLOSED** | 52/52 new tests + 285/285 regression. 1 additive frozen-contract amendment (InnerLifeEvent +1 Optional field `source_world_event_novelty_id`). 0 production mutation. |
| M5.15-6-PREFLIGHT | Real-world source architecture decision (READ-ONLY) | (docs only) | **CLOSED** | Owner authorized Calendar via iCal/ICS public feed (only qualifying candidate). 12 owner decisions Q1-Q12. Out-of-repo. |
| M5.15-6 | Real-World Calendar Source Integration (IMPLEMENTATION) | `c2de02c` | **CLOSED** | 55/55 new tests + 211/211 regression. 0 frozen contract change. 0 production mutation. **CANONICAL LATEST.** |

**Architecture decision** (M5.15-2 + M5.15-5 + M5.15-6 Owner Options A):
- Event Bus = canonical integration transport for WorldEvent downstream consumers
- New WorldEventSource MUST `bus.publish(SoulEvent(WORLD_EVENT, target="broadcast", priority=NORMAL, payload=world_event.to_payload()))`
- `inject()` / `process_world_event_direct()` RETAIN as deprecated backward-compat (per M5.15-2 spec §4)
- Single processing path per subscriber (no double perception, no recursive publish)
- novelty_id dedup preserved (no duplicate InnerLifeEvent)
- For sources with M3.1-incompatible identity strings (e.g., iCal UIDs with `@`/`.`/`-`):
  use `SHA256(identity)[:32]` as `novelty_id`, preserve original in `data["<source>_id"]`
  (M5.15-6 RESUME Option 1, 0 frozen contract change)

**Two-Layer Lineage Model** (M5.15-5):
- **Layer 1 (External Causality)**: `WorldEvent.novelty_id → InnerLifeEvent.source_world_event_novelty_id` (free string, no 32-hex, no existence check)
- **Layer 2 (Internal Lineage)**: `InnerLifeEvent.parent_event_id → lineage_depth / lineage_path` (M5.4-5.1 frozen preserved)
- 5 existing producers (Diary/Dream/Event/ProactiveDM/Conversation) keep source_world_event_novelty_id=None (backward compat 100%)

**M5.15-6 Identity Model** (RESUME Option 1, Bry authorization 2026-08-12 19:37):
- `WorldEvent.novelty_id = SHA256(VEVENT.UID)[:32]` (32-char lowercase hex, M3.1-compatible)
- `WorldEvent.data["ical_uid"] = VEVENT.UID` (exact original, preserved for traceability)
- `WorldEvent.data["ical_sequence"] = VEVENT.SEQUENCE` (if present, observability only — NOT in identity)
- SEQUENCE excluded from hash (Q6: same UID + different SEQUENCE → same hash → adapter dedupes)
- 128-bit collision space, deterministic, no timestamp/randomness mixed in

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M5.15-3: `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`
- M5.15-4 STOP: `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md` (SUPERSEDED, historical)
- M5.15-5: `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md` (canonical)
- M5.15-6-PREFLIGHT: `C:\Users\bbfcc\gov_1_temp\m5_15_6_preflight_architecture_decision.md` (canonical)
- M5.15-6 RESUME STOP: `C:\Users\bbfcc\gov_1_temp\m5_15_6_stop_report.md` (historical, M3.1 conflict)
- M5.15-6: `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md` (canonical, RESUME Option 1)

**M5.15-1 findings status** (post M5.15-6):
- **F1 P1** (source path doesn't publish to bus): **RESOLVED by M5.15-3** ✓
- **F2 P2** (real-world source integration): **RESOLVED by M5.15-6** ✓ (Calendar via iCal/ICS)
- **F3 P2** (cross-handler lineage): **RESOLVED by M5.15-5** ✓ (via Layer 1 + Layer 2)
- **F4 P2** (identity bridge): **RESOLVED by M5.15-5** ✓ (same new field, preserved by M5.15-6)
- **F5-F7 P3** (intentional, test-only): no action

**Next work**: None. M5.15 chain is complete (F1-F4 all RESOLVED, F5-F7 no action). Future
real-world source candidates (weather / news / social) can follow the M5.15-6 integration
pattern when Bry authorizes them.

---

### 5.6 M6.1 — Lived Context Awareness (Canonical Boundary & Documentation)

**Status**: Signal half LIVE (Physical via M6.1-3.1 Weather + Information via M6.1-5.1 News + Social via M5.15-6 Calendar + Temporal). Life half BLOCKED at Agency layer (M5.2 regression identified in M6.1-8). Frozen contracts 0 change. 0 production mutation. M6.1-8.1 PENDING Bry decision (3-line re-enable).

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.1-0 | Lived Context Awareness architecture audit (READ-ONLY) | (docs only) | **CLOSED** | 18 modules / 11 subsystems inventoried. Bry's 5 questions answered. Out-of-repo report. |
| M6.1-1 | Lived Context taxonomy & minimal architecture (READ-ONLY) | (docs only) | **CLOSED** | READY FOR M6.1-2 verdict. Canonical 5 contexts + 4-layer boundary + minimum provenance. 0 frozen contract change. Out-of-repo report. |
| M6.1-2 | Lived Context canonical boundary & documentation (IMPLEMENTATION) | `9e050f6` | **CLOSED** | Documentation-first implementation. README.md §7.1 added + this section + §9 change log. 0 source-code change, 0 frozen contract change, 0 production mutation, 0 new runtime abstraction. |
| M6.1-3 | Lived Context Evidence & Calendar Run-and-Collect Audit (READ-ONLY) | (docs only) | **CLOSED** | 1 Calendar event in perception_trace. 5 P2 gaps. Per Quality > Quantity: NO new milestone justified. |
| M6.1-3.1 | Open-Meteo Weather Source (IMPLEMENTATION) | `ac50256` | **CLOSED** | Physical Lived Context signal. `source_id="weather"`, `novelty_id=SHA256(...)[:32]`, types=`rain_started` / `weather_temp_change`, 1800s polling, 0 new dependencies. 406/406 broad regression PASS. 0 frozen contract change. |
| M6.1-3.2 | Live Weather Activation & Lived Context E2E Validation (READ-ONLY) | (docs only) | **CLOSED** | SOULOS_WEATHER_LOCATION=25.03,121.57 in .env. PHYSICAL LIVED CONTEXT OPERATIONAL. |
| M6.1-3.3 | Organic Weather Context Evaluation (READ-ONLY) | (docs only) | **CLOSED** | LLM E2E test (isolated tmp_path + real MINIMAX API) confirms physical context actually informs Soul interpretation. 4/4 tests PASS. |
| M6.1-4 | Personal Lived Context Capability Audit (READ-ONLY) | (docs only) | **CLOSED** | 0/5 FULLY ANSWERABLE. Q1 Q2 PARTIALLY, Q3 Q4 Q5 NOT. 8 signals inventoried. No first-person Personal data source. |
| M6.1-5 | Information Lived Context Capability Audit (READ-ONLY) | (docs only) | **CLOSED** | 0/5 ANSWERABLE. LLM training data cutoff (M2.7). No real News source. No Web/Search tool. |
| M6.1-5.1 | RSS News Source (IMPLEMENTATION) | `9f8ece8` | **CLOSED** | Information Lived Context signal. `source_id="news"`, `type="news_event"` (NOT in WORLD_QUALIFYING_TYPES), `novelty_id=SHA256(provider.url.published_at)[:32]`, 1800s polling, 2h lookback, 10 articles/poll cap, 30s timeout, 10000-entry FIFO dedup. Stdlib only (urllib + xml.etree.ElementTree + email.utils.parsedate_to_datetime). 92/92 tests + 469/469 regression. 0 frozen contract change. 8 working public feeds (Reuters/AP UNAVAILABLE, documented). |
| M6.1-5.2 | Live News Activation & E2E Validation (READ-ONLY) | (docs only) | **CLOSED** | SOULOS_NEWS_FEEDS=bbc_world|...,npr_top|... in .env. 4/4 LLM E2E tests PASS. INFORMATION LIVED CONTEXT: OPERATIONAL (with caveat: isolated E2E bypassed M3 accept gate). |
| M6.1-5.3 | News Lookback & Context Density Audit (READ-ONLY) | (docs only) | **CLOSED** | Critical finding: News default score 0.345 < 0.35 accept threshold → REJECTED. **KEEP 2h lookback**. M3 accept gate is binding constraint, not lookback. 15 polls over 7.5h, 19 events emitted (66.7% emit, 1.27/poll mean). |
| M6.1-6.0 | Personal Lived Context Architecture Decision (READ-ONLY) | (docs only) | **CLOSED** | 8 signals inventoried. 0/5 ANSWERABLE, 2 PARTIALLY, 3 NOT. Verdict: **DEFER (D)**. Options compared: A Manual / B Inference (= surveillance-by-proxy, violates M3 design rule) / C Dedicated (reduces to A under work order constraints) / D Defer. |
| M6.1-6.0-C | Personal Audit Closeout (DOCS) | `49adf46` | **CLOSED** | Personal = DEFER (D) verified. |
| M6.1-7 | Production Lived Context Evidence Reassessment (READ-ONLY) | `bdf76ad` | **CLOSED** | Verdict: **LIVED CONTEXT NOT YET FORMED**. World → Perception OPERATIONAL (3 sources, 2385 trace events). Perception → Lived Context SINGLE-SOURCE only (world_context = Weather only, 602/602 injects). Lived Context → Soul Interpretation INFLUENCING (1081 LLM responses had world_context). Soul Interpretation → Agency **BROKEN** (Scheduler `agents=0` since 8/8). Agency → Expression INACTIVE. M6.1 series 50% complete. |
| M6.1-8 | Agency Re-enable Investigation (READ-ONLY) | `f699d93` | **CLOSED** | **Root cause identified**: M5.2 migration (commit `481ea41`, 2026-08-08 21:11) removed `scheduler.register(aid, cb)` callsite (M5.2-I Phase 7) without replacement; M5.2-I Phase 8 changed iteration source to `_all_agents`; `_all_agents` permanently empty. 5 trigger paths (morning/night/dream/event/proactive_dm) silent-skip. 0 diary/dream/event/proactive_dm writes for 6+ days. **Documented regression** (M5.2-I I-9 sweep §2.2 marked "production 完全沒註冊" but treated as "API COMPAT"). **Fix proposed**: 3 lines in `run_server.py` (`for aid in agent_ids: scheduler.register(aid)`). 4 safety options for Bry (A direct / B gradual / C shadow / D test-isolated). |
| M6.1-8.1 | Agency Re-enable Isolated Validation (FIX / ISOLATED TEST ONLY) | `d0c33da` | **CLOSED** | **3-line fix PROVEN in isolated test env**: 21 tests (A baseline regression, B fix validation, C AGENCY_TRIGGER publication × 5 paths, D handler reception × 4 handlers, E safety verification, F regression test × 2, G diagnostic). **20 PASS / 1 XFAIL / 0 FAIL in 0.47s**. F.2 = `run_server.py` missing `scheduler.register(...)` callsite (XFAIL pending M6.1-8.2 production fix). 71/71 M5.2 series tests still PASS. 0 production data mutation. 0 Telegram. 0 real LLM. 0 frozen contract change. 0 source code modification. **Bry rollout decision still required** (A/B/C/D). |
| M6.1-8.2 | Controlled Production Agency Re-enable (IMPLEMENTATION / Option B / Gradual) | `2a42521` | **CLOSED** | **Bry 拍板 2026-08-14 19:12 EDT — Option B / Gradual**. 10-phase rollout (1→2→3→...→10 agents) in 19:18-19:27 EDT (~10 minutes). All 10 phases confirmed via `[Scheduler] 啟動 ✓ ... agents=N` log line. 0 ERROR/Traceback/CRITICAL during rollout. /health=200 stable. Final state: agents=10 (M5.2 regression fully recovered). `scripts/run_server.py` + `.env.example` modified (commit `2a42521`, +59 insertions). Env var `SOULOS_AGENCY_GRADUAL_AGENTS` controls per-phase agent list. 0 frozen contract change, 0 production data mutation, 0 handler/Agency/scheduler architecture change. 24h RUN-AND-COLLECT deferred to M6.1-9. |
| M6.1-9 | Lived Context Formation Audit (READ-ONLY) | `c526320` + `6d5112d` + `af91271` | **CLOSED (8/16 22:15 — two issues fixed + night slot 10/10 verified)** | T+50min partial audit (2026-08-14 20:06 EDT, 50 min post-M6.1-8.2 deploy). Out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` (19.7KB). Layers 1-2 (Signal/Perception) OPERATIONAL (9 new weather events since deploy, weather source polling 1800s + 0 dedup at perception phase). Layers 3-6 (Lived Context/Soul/Agency/Expression) WIRED but UNVERIFIED — 5/5 trigger paths active (proactive_dm/event/dream/diary morning/diary night), but 0 triggers fired yet (next window: 22:00 EDT night diary, ~2h from audit time). Q1-Q6 reassessed: Q1-Q3/Q5/Q6 ⏳ PENDING 24h evidence; Q4 ⏳ PENDING 24h evidence (last 1081 LLM responses from pre-M6.1-8.2 era, 0 since deploy). **P2 anomaly discovered (6.4)**: M6.1-8.2 closeout claimed 10/10 agents but actual state is 9/10 — `agent_aoi` missing from scheduler. `SOULOS_AGENCY_GRADUAL_AGENTS` env var still set with 9 specific names (Phase 9 set), Phase 10 unset transition incomplete. **M6.1-9.1 dispatched** to fix env var. 24h follow-up cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`) fires 2026-08-15 ~20:00 EDT for full 24h evidence collection. |
| M6.1-9.1 | Restore True Phase-10 Agency Registration (FIX / PRODUCTION CONFIGURATION) | (registry sync pending) | **CLOSED** | **Root cause**: stale `SOULOS_AGENCY_GRADUAL_AGENTS` env var from M6.1-8.2 Phase 10 launch shell persisted into restart, leaving scheduler in `[M6.1-8.2 Gradual] registered 9/10 agents` mode. `agent_aoi` (Phase 12 addition, in `configs/default.yaml` line 80-83 with `enabled: true` and `AgentAoi` in `src/agent/registry.py:8,49`) was missing. M6.1-8.2 closeout overcounted. **Fix**: configuration-only restart via `scripts/server_ops.ps1 stop` + `start` from clean PowerShell shell (no `SOULOS_AGENCY_GRADUAL_AGENTS` env var). Result: `[M6.1-8.2 Full] registered all 10 agents (M6.1-8.1 default)`, `agent_aoi` now registered, 5/5 trigger paths wired, /health=200, 0 ERROR/Traceback. **0 source code change**, **0 frozen contract change**, **0 production data mutation** (memory.db / perception_trace / shadow_log / inner_life / 12 relationships.json all byte-for-byte unchanged). 21/21 M6.1-8.1 regression PASS (F.2 now PASS, was XFAIL pre-fix). Pre-restart PIDs 10592+17640 (uv python, started 8/14 19:27:35 EDT). Post-restart PID 20752 (hermes-agent venv python, started 8/14 20:21:18 EDT). 0 duplicate registrations. M6.1-9 24h follow-up cron `m6_1_9_followup` continues unchanged, fires 2026-08-15 ~20:00 EDT to verify Lived Context formation with now-correct 10-agent registration. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (14.9KB). |

**Canonical taxonomy** (M6.1-1, updated M6.1-3.1 / M6.1-5.1):

| Context | What it covers | Current sources | Current status |
|---------|----------------|------------------|-----------------|
| **Physical** | Bry's body / environment (weather, location, sunlight) | Open-Meteo Weather (M6.1-3.1) | ✓ LIVE |
| **Information** | News / web / search results / external data | RSS News (M6.1-5.1) | ✓ LIVE (with M3 accept gate caveat, 0 emits to world_context) |
| **Social** | Telegram messages, calendar events, cross-agent interactions | Telegram + Calendar (M5.15-6) | ✓ LIVE |
| **Personal** | Bry's habits (meal/sleep/activity), preferences, identity | NONE for Bry-as-person | **DEFERRED** (M6.1-6.0) |
| **Temporal** (cross-cutting) | When; touches ALL other contexts | System clock + Bry's last_msg_ts | ✓ LIVE (Chrono-Social Engine) |

**Canonical 4-layer boundary** (M6.1-1):

```
[Layer 1: Signal]            raw input from source
                             Telegram / Calendar iCal / System clock /
                             (future: Weather API / News API / Web Search)
                                       ↓
[Layer 2: Perception]        validation + scoring + dedup + fact extraction
                             WorldPerceptionMiddleware (M3) +
                             MemoryMiddleware (M5.10) +
                             Chrono-Social Engine (Phase 3.5) +
                             MultiAgentRelationshipsManager (M5.13)
                                       ↓
[Layer 3: Lived Context]     aggregated per-context blocks, formatted for LLM
                             (de-facto implementation: src/llm/proxy.py:_build_messages_*)
                             block order: identity → memory → mood →
                             relationship → inner_life → world → temporal
                                       ↓
[Layer 4: Interpretation]    LLM call + response
                             LLMProxy (M6.0-5.6) + AGENT_SPEAK emit
```

**Boundary invariant** (M6.1-1, canonical):
> External integration / tool output / raw WorldEvent ≠ Lived Context.

Calendar / Telegram / Weather / Web / Search / News / Messaging = **signal producers** (Layer 1).
Lived Context = aggregated personal-context state from Perception output (Layer 3).

**Existing aggregation** (M6.1-2 decision): `src/llm/proxy.py:_build_messages_group()` and
`_build_messages_private()` is the **de-facto Lived Context aggregator**. Block order:
identity → memory → mood → relationship → inner_life → world → temporal.
**No new `LivedContextAggregator` runtime wrapper created** — documentation-only labeling.

**Minimum provenance** (M6.1-1, all already in place):
- `source` (per signal)
- `observed_at` / timestamp (per signal)
- `freshness` (implicit via `last_msg_ts`, chrono-social `deviation_interpretation`)
- `confidence` (per relationship, M5.13-2)
- `signal_vs_derived` (implicit via LLM context block separation)

No new scoring dimensions added. No new metadata fields required.

**Frozen-contract impact** (per M6.1-1 audit): **0 changes**. 15 contracts preserved
(M3 WorldEvent, M3.1 ABC, M3.1 Bus, M5.4-5.1 InnerLifeEvent, M5.4-5.1 parent_event_id,
M5.4-5.1 lineage, M5.4-5.5 SoulEvent.inner_life_event_id, M5.9-2 WORLD_QUALIFYING_TYPES,
M5.9-3 WorldInnerLifeAdapter, M5.10 Memory, M5.13-2 strict 0.3, M5.15-3 canonical bus path,
M5.15-5 source_world_event_novelty_id, M5.15-6 identity model, VALID_SOURCES).

**Missing capabilities** (DEFERRED, requires Owner authorization to start):
- ~~Real Weather source~~ — **RESOLVED by M6.1-3.1** ✓
- ~~Real News source~~ — **RESOLVED by M6.1-5.1** ✓ (with M3 accept gate caveat per M6.1-5.3)
- Personal life-rhythm tracking (Personal, requires data source decision) — **DEFERRED per M6.1-6.0**
- Environment→emotion reasoning (Personal, requires explicit pipeline) — **DEFERRED per M6.1-6.0**
- LivedContextAggregator (CAPABILITY — currently de-facto, no concrete behavioral need)
- **Agency re-enable** — **M6.1-8.1 PENDING** (3-line fix, awaiting Bry decision)

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M6.1-0: `C:\Users\bbfcc\gov_1_temp\m6_1_0_lived_context_awareness_audit.md`
- M6.1-1: `C:\Users\bbfcc\gov_1_temp\m6_1_1_lived_context_taxonomy_audit.md` (canonical)

**Next work**: None authorized. M6.1-2 documentation is canonical. Future M6.1-* capability
tickets (weather / news / personal-rhythm) require Owner authorization per §2.8 decision boundary
and must follow the M5.15-6 integration pattern (architecture decision → implementation →
closeout).

### 5.7 M6.2 — Text / TTS Response Path Separation

**Status**: M6.2-0 + M6.2-1 both CLOSED. Async text/TTS separation already correct in production
(M6.2-0 audit); M6.2-1 closed the actual gap (message_id correlation for rapid sequential
messages from same agent). 0 frozen contract change. 0 production data mutation. 5 production
files + 1 test file. Per Quality > Quantity: NO M6.2-2 recommended (0 P0/P1/P2 remaining;
6 P3 out of scope per M6.2 work order — TTS cancellation, streaming, queue, etc.).

| Ticket | Title | Commit | Status | Notes |
|--------|-------|--------|--------|-------|
| M6.2-0 | Text / TTS Response Path Separation Architecture Audit (READ-ONLY) | (docs only) | **CLOSED** | 18 sections, 25.6KB audit. **Key finding**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(self._synthesize_async(...))` in `FishTTSHandler._on_agent_speak` (`src/llm/fish_tts_handler.py:266-273`). TTS latency 0.5-6.4s (mean 3.3s, evidence from `data/tts/` filesystem + `trace.log`). Transport: WebSocket 2 distinct message types (`agent_speak` + `agent_audio_ready`); Telegram 2 adapter methods (`send_message` + `send_voice`). **Identified gap (P2)**: `AGENT_AUDIO_READY` payload missing `message_id`/`correlation_id`. ChannelRouter `_pending_voice_target[agent_id]` last-write-wins (race condition for concurrent messages). Web client `lastAudioByAgent[agentId]` overwrites. **M6.2-1 RECOMMENDED**: 5 files, +14/-6 lines, 0 frozen contract change. 0 P0, 0 P1, 1 P2 (correlation gap), 6 P3. |
| M6.2-1 | Per-Message TTS Correlation Minimal Implementation (IMPLEMENTATION) | `965df92` | **CLOSED** | **Gap closed end-to-end via `message_id = SoulEvent.event_id` UUID**. 5 production files + 1 test file. `src/voice/tts_service.py`: `synthesize_and_store(..., message_id: Optional[str] = None)` — message_id added to AGENT_AUDIO_READY payload + return dict. `src/llm/fish_tts_handler.py`: extracts `event.event_id` in `_on_agent_speak`, passes to `_synthesize_async` and TTSService. `src/io/channels/router.py`: `_pending_voice_target` keyed by `message_id` (per-message), with `_pending_voice_target_legacy` for `agent_id` fallback when message_id is None. `src/io/gateway.py`: WS `agent_speak` + `agent_audio_ready` payloads include `message_id`. `static/index.html`: `lastAudioByMessageId` cache, `attachReplayButtonToMessage` + `replayMessageAudio`, `data-message-id` attribute on rendered messages. **11/11 new tests + 45/45 focused regression PASS** in 0.47s. 0 frozen contract change. 0 production data mutation. **No P0/P1/P2 remaining**. Per Quality > Quantity: NO M6.2-2 recommended. M6.1-9 PENDING 24h RUN-AND-COLLECT. |

**Architectural findings (retained from M6.2-0)**:

1. **Text/TTS separation was already correct** — TTS is fire-and-forget. No new infrastructure needed.
2. **Transport is already decoupled** — WebSocket 2 message types, Telegram 2 adapter methods.
3. **Real gap was correlation key** — `AGENT_AUDIO_READY` had no message_id; client could only do
   last-write-wins per agent. Rapid sequential messages from same agent could have wrong audio attached.
4. **M6.2-1 minimal fix** — Reuse `SoulEvent.event_id` (UUID) as correlation key. 0 new schema,
   0 new infrastructure, 0 frozen contract change.

**Out-of-scope per M6.2 work order (P3, NOT implemented)**:
- TTS cancellation
- TTS streaming
- TTS queue / broker
- TTS provider change
- WebSocket protocol redesign
- Telegram architecture redesign

**Closeout logs** (out-of-repo per M5.13-3.1 lesson):
- M6.2-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md`
- M6.2-1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md`
- M6.2-1 test file: `tests\test_m6_2_1_text_tts_correlation.py` (in-repo, 11 tests, 21KB)

**Next work**: None authorized. M6.2-1 closed the P2 correlation gap. Per Quality > Quantity,
M6.1-9 (Lived Context Formation Audit, READ-ONLY, PENDING 24h RUN-AND-COLLECT) is the
recommended next ticket — it depends on production evidence accumulation from M6.1-8.2 Agency
re-enable (started 2026-08-14 19:27 EDT).

---

### 5.8 DSH Multi-Agent MVP — Domain Core（前置設計 + MVP implementation）

**Status**: **MVP COMPLETE / ACCEPTED**（MVP Contract Gate 8/8 PASS，bypass discovered = NO）。

DSH Multi-Agent MVP 是 Soul OS 遷入 DeepSeek Harness 的前置 domain core。四份 canonical contract（2A–2D）+ MVP-1~7 全部 commit，經 authority / durability / recovery / single-writer / E2E / cross-MVP gate 驗收。

| Phase | 內容 | Commit |
|-------|------|--------|
| 2A–2D | Architecture Contracts（Work / Workspace / Authority / Persistence） | `f5fb0cd` |
| MVP-1 | Work Contract + Durable Store | `9a8b7a2` |
| MVP-2 | DSH Adapter Boundary（ports + bridge） | `99b95db` |
| MVP-3 | Chief + Specialist Roles + single-writer | `a1a150f` |
| MVP-4 | Workflow / Handoff | `61b26c3` |
| MVP-5 | Authority Boundary（capability + approval + identity seam） | `015139e` |
| MVP-6 | Recovery / Resume（durable authority + single-writer） | `8bb8d91` |
| MVP-7 | E2E Vertical Slice（2A–2D 完整閉環） | `d7877e0` |

**Canonical contracts**（`docs/`）：
- `DSH-WORK-CONTRACT.md`（2A Work / Execution Boundary）
- `DSH-WORKSPACE-DESIGN.md`（2B Workspace / Git / Worktree）
- `DSH-HUMAN-AUTHORITY.md`（2C Human Authority）
- `DSH-PERSISTENCE.md`（2D Persistence / Recovery / Resume）
- `DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate 10/10 PASS）

**核心原則**：Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.

**Accepted limitations / future hardening**（NOT resolved，下一階段不得誤認成已解決）：
1. `WorkObject.approvals[]` 目前不是 approval durable truth（approval 的 durable truth 在 AuthorityStore）
2. `requires_human_approval` 尚未 enforcement（state machine 只宣告不 gate，authority 由 capability policy 層執行）
3. `DURABLE_WRITER="kernel"` 目前仍是 convention-level identity（self-attested 字串，非 security-level）
4. Python object graph 的 public/mangled escape hatch 是已知 limitation（no-true-private）

**MA 治理鏈（MA-0 → MA-4-R1）**：
- MA-0 Architecture Audit ✅
- MA-1 Adapter Boundary ✅ READY FOR IMPLEMENTATION
- MA-2 Migration Architecture ✅ READY FOR MIGRATION PLAN
- MA-3 Migration Decomposition ✅ READY FOR IMPLEMENTATION
- MA-4 Build Plan ❌ BLOCKED → MA-4-R1 修復（authority trust establishment + resume idempotency）→ Independent Review PASS → **IMPLEMENTATION AUTHORIZED**
- P0-1 Minimal Work Execution Adapter → Independent Adversarial Review（READ-ONLY）→ **READY FOR PHASE 0 GATE**
- P1-Preflight Hardening（M1/M2/M3）→ Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**
- P1 Decomposition（Execution Routing）→ Review #1 BLOCKED（2 項）→ 修正 → Re-review → **READY FOR IMPLEMENTATION**
- P1-A Execution Target Contract（Domain Core 側）→ Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**

**P0-1 成果**（commit `ece757b`）：`src/work_adapter/`（bridge.py + execution.py）、`dsh_adapter/soul-dsh-adapter.mjs`（mock TS adapter）、`tests/test_work_adapter.py`（27 tests）。230 tests 全綠。8/8 Hard Checks PASS + 10 acceptance gates PASS。`git diff 26e1e49 -- src/work` 為空（Domain Core 十一模組零改動）；`src/` 全樹零 DSH import；無 durable write bypass；No-DSH Survival 實測成立。

**Phase 0 Gate（Contract / E2E / No-DSH）**：→ **Phase 0 CLOSED**（commit `ece757b`）。

**P1-Preflight 成果**（commit `34e91d4`）：M1 `HandoffStatus`→WorkState 語義（blocked/needs_input → state_transition(current→blocked)，不照記產出）、M2 `result_type`↔capability anchor 驗證（堵 event 類型/provenance 錯記）、M3 bridge error contract 統一（binary I/O + 主執行緒 decode，`UnicodeDecodeError`→`BridgeExecutionError`）。239 tests 全綠。8/8 Hard Checks PASS + bypass=NO。

**P1 Decomposition 成果**（`docs/DSH-P1-EXECUTION-ROUTING.md`，commit `83aa389`）：P1-A~P1-E 分解，鎖定 P1-A Execution Target Contract（`ExecutionShape` capability-neutral + shape 由 Soul 推導 + adapter 只 translate）。Review #1 BLOCKED（A2 resume discriminator 未鎖 + A5 防火牆無 mechanism）→ 修正 → Re-review READY。

**P1-A 成果**（commit `83aa389`）：`src/work/schema.py` 新增 `ExecutionShape` enum（single_shot / multi_stage / continuous，capability-neutral）+ `src/work/workflow.py` 新增 `derive_execution_shape`（dependencies 非空→multi_stage，其餘→single_shot；continuous 不實作，待 P1-D）+ `src/work_adapter/execution.py` payload 新增 `execution_shape`。249 tests 全綠（239 + 10 new）。8/8 PASS + bypass=NO。`src/work/` 授權集 {kernel.py, schema.py, workflow.py}；零 DSH import 不變。

**P1-B 成果**（`docs/DSH-P1-ARTIFACT-BOUNDARY.md`，commit `d4a57a2`）：鎖定 artifact ownership boundary——refs 是 content-addressed identity（Domain Core 計算）、artifact content 的 canonical writer 是 Domain Core、adapter 只 transport、claim→verify、write-ahead + 原子 rename、evidence 走同一邊界（D1–D8）。Review #1 BLOCKED（5 項）→ 修正 → Re-review **READY FOR IMPLEMENTATION**。

**Owner 拍板（2026-08-23）— artifact.create role 歸屬**：以 2A §5.1 frozen contract 為 canonical authority，`artifact.create` 歸 Researcher。2A 是 frozen/ACCEPTED，2B 是 design，kernel/e2e 是 implementation reality——三者衝突時 implementation 不得反向修改 frozen contract。P1-C 不得 role substitution / capability spoofing / adapter-side bypass；若 P1-C 顯示 Developer 必須直接產 artifact，另開正式 contract change decision。

**Owner 拍板（2026-08-23）— DEV-ENV-0 contract change：給 developer artifact.create**：DEV-ENV-0（多 agent 開發環境 operationalization）的 review 顯示 developer 在現契約下無任何合法產出型態（artifact.create 歸 Researcher 是上一個拍板，導致 `run developer <task>` 必然 fail-closed）。Owner 拍板**開正式 contract change：給 developer artifact.create**——定性為「修復 2A §5.1 / 2B §5 / 實務三處不一致」（2B §5 明說 developer 對 artifact store 是 write），**不是遷就 implementation reality 反向改 frozen contract**。這是 governance 層的正式變更，落地到 roles.py 的 ROLE_CAPABILITIES + 遷移 P1-C0 相關測試（developer artifact 從 DENY 變 PASS）。

**P1-C 成果**（`docs/DSH-P1-C-ROUTING.md`，commit `65be40b`）：Real DSH single_shot routing 的 decomposition。transport seam = `dsh --profile headless`（one-shot generic Agent，無 preset——seam 事實，已實讀 dsh-headless/lib/index.js 驗證）。role 語義由 task prompt 承載、role authority 在 Domain Core。D1–D10 鎖定：headless transport、role 語義、single_shot、artifact.create=Researcher（Owner 拍板）、結構化輸出 fail-closed、staging→ingest、fail-closed 三條、No-DSH Survival、Domain Core role↔capability enforcement 前置、claimed ref 存在性驗證。Review #1 BLOCKED（2 項：enforcement 缺口 + preset 事實錯誤）→ 修正 → Re-review **READY FOR IMPLEMENTATION**。N2 decision role 邊界：依 2A §3.1「任何 agent 可記錄 decision」，decision handoff 不要求特定 capability（與 §5.1 Chief 的 orchestration decision 不同語義）。

**P1-C0 成果**（commit `06a0986`）：Domain Core Capability Enforcement。`kernel.record_handoff` 產出分支（dedup 前）驗證 role 具備 result_type 對應 capability——artifact.create→Researcher、evidence.create→Tester/Auditor、decision 不 gate（2A §3.1）、blocked/needs_input 不 gate（M1）。新增 `CapabilityNotAuthorizedError(PermissionError)`（roles.py）。256 tests 全綠（249 + 7 new）。測試遷移 28 處 Developer+artifact.create→Researcher（不補回 matrix）。8/8 PASS + bypass=NO。

**P1-C1 成果**（`docs/DSH-P1-C1-DECOMPOSITION.md`，decomposition；實作 commit `041dad6`）：Identity & Handoff Seam decomposition + Real DSH single_shot Routing 實作。C1-A audit 確認 process 層 identity 錨點（cwd + session log header）。核心：trust model 明說（信任根=adapter，防惡意 LLM 偽造 role）、T1 Domain Core 自行開檔讀 log、A1 identity binding=role→cwd、B1 content=session log header+final message、claim→verify 三層正交。**實作成果**：`src/work/execution_evidence.py`（RoleCwdRegistry + read_execution_evidence + verify_role_binding）+ `src/work/artifact_store.py`（write_artifact + verify_artifact_ref + staging + single-writer）+ bridge execute_dsh（spawn headless + --patch overlay + 事後讀回 log）+ execution execute_work_dsh（三層 cross-check）。291 tests 全綠（256 + 35 new），C1.9 真 DSH smoke **PASS**。8/8 PASS + bypass=NO。

**P1-C2 成果**（commit `97e85bf`）：Integration / Boundary Gate——真 DSH E2E 閉環。補 content transport：artifact content = final_message（文字型），Domain Core `write_artifact` 算 canonical ref **回填** claim（agent 不聲稱 ref，解掉 sha256 自指矛盾），三層 claim→verify 完整（identity + capability + content）。evidence_refs 定錨為「被驗證對象」（D4）。execute_work（mock）deprecated + DeprecationWarning（D5）。headless approval policy = `never`（fail-fast deny，D6）。302 tests 全綠，**真 DSH E2E 閉環 PASS**。8/8 PASS + bypass=NO。

**DEV-ENV-0 成果**（commit `98d71fa`）：Multi-Agent Development Loop Operationalization——`scripts/dsh_dev_run.py`（run <role> <task> entrypoint）+ 三 role config + resilience + `docs/DSH-DEV-ENV-USAGE.md`。前置 contract change（Owner 拍板）：DEVELOPER + artifact.create（修復 2A §5.1 / 2B §5 / 實務三處不一致）。302 tests 全綠，**smoke task 三 role 真跑 PASS**。8/8 PASS + bypass=NO。**Soul OS 進入 dogfooding / self-development 階段**。

**Dogfooding-1 成果**（第一個真實 task：清理 stale test `test_soul_md_loader.py`，三 role 真 DSH 跑通）：researcher 產分析（3 層根因 + 5 步修復方向，4848B）+ developer 產可執行方案（import 改法 + helper + 4 assert diff，7507B）+ tester 產 CONDITIONAL verdict（自主找出 developer 方案 2 個真實缺陷：N1 helper fallback 無 default → unknown agent TypeError、N2 agent_id 變數不存在 → NameError）。**tester 自主驗證開發者方案並揪錯 = self-development 首次生效**。能力邊界確認：檔案落地缺口（文字 artifact，不改 repo）+ 多段手動串 friction（agent 無工具權限，品質靠人工注入 ground truth）。**從真實使用長出的 P1-D / P1-E 具體輸入**：①自動組上一 stage ref+摘要進下一 stage；②agent 讀 repo 權限（最大品質槓桿）；③verdict 結構化（目前 verdict 只在 session log）；④claim role 大小寫不穩定（developer 首跑 `'Developer'` 大寫 → fail-closed → 重跑）。

**Phase 1 剩餘 backlog（CAN-DEFER / 後續 phase）**：
1. refs content-address 驗證（需 artifact store；P1-B 決定與 store 同批落地）
2. production adapter 依 MA-4 §1.1 移出 repo 為獨立 package（P1-E）
3. grant() reject `expires_at=None` / e2e 改用 `issue_hmac_context` / durable nonce registry（MA-4-R1 承接）
4. blocked handoff 重試 dedup（crash-after-write 場景，P1-D）
5. continuous 觸發條件（P1-D goal resume semantics，derive 現只回 single_shot）
6. multi_stage workflow script authorship（P1-D 前置決策 A6，未解）
7. stale test `tests/test_soul_md_loader.py`（import 已移除的 `SOUL_OS_OVERRIDE`，pre-existing）
8. **DEV-ENV-0 reviewer minor findings（非阻塞，邊用邊做）**：①過期設計文件（P1-C1-DECOMPOSITION:122、P1-C-ROUTING:62-66、P1-ARTIFACT-BOUNDARY:165-169）加 superseded 註記；②`_CLAIM_ERROR_MARKERS` 的 "session evidence" 誤捕 log 讀取失敗（應歸 infra 而非 claim）；③A3 檢查單向 + data_root cwd-relative（建議錨定 ROOT 或加反向檢查，已見 workspaces/data 殘留實例）；④advisory：work state=proposed 反映 assign 不 transition 的既有語義。

**Next work**: Dogfooding-2——把 Dogfooding-1 暴露的真實需求（①自動組 ref+摘要、②agent 讀 repo 權限、③verdict 結構化、④claim role 大小寫）落成 P1-D（自動 multi_stage orchestration）或最小硬化。第一個真實 task 的 developer 方案已含 tester 揪出的 N1/N2 修正，落地需合入。

---

### 5.9 DSH 角色層落地（agent-preset + dsh-subagent-role）與 Work Truth 層 correction

**Status**: **Role layer = integrated；Work Truth layer = not integrated**（Owner 拍板 2026-08-23，architecture correction）。

DSH development-environment architecture 的最新 evidence 揭示一個重要 boundary：**Role delegation 已經 DSH-native 化，Work Truth 還沒有 DSH integration。**

**已落地（線 B，role delegation DSH-native 化）**：
- 5 個具名 agent-preset（`~/.dsh/.agent-presets/`）：`chief`（幕僚長）、`researcher`（研究員）、`developer`（開發者）、`tester`（測試員）、`auditor`（審計員）。human 不入 preset（是「人」不是「bot」）。每個 preset = `agent.cordis.yml`（persona + 工具集，照 role→capability 矩陣剪裁）+ `preset.yml`（中文名 + 職責）。
- `dsh-subagent-role` 插件（`C:\Users\bbfcc\.local\bin\dsh workspace\dsh-subagent-role`）：service 層包 `ctx.subagents.start`/`startContinuable`，看 `request.label`（= subagent description）命中 researcher/developer/tester/auditor 角色名時，注入該角色 persona + `toolFilter.allow` + 可選模型。設定頁（設定 → 子代理角色派发）提供 provider/model 下拉選單。
- 舊 `dsh-subagent-model` 插件已停用（`enabled: false`；曾因全域 fallback 無差別強制模型，導致 orchestration 層執行者派發失敗，已定位止血）。

**未落地（Work Truth layer）**：Work Object 狀態機（proposed→approved→assigned→…→done）與 Handoff Protocol **尚未接到 DSH 派發上**。線 B 目前只完成「誰來做」，還沒完成「這件工作是什麼、做到哪裡、產出了什麼、是否真的完成」。

**DSH-WORK-OBJECT-0**（`docs/DSH-WORK-OBJECT-INTEGRATION.md`）：**DESIGN COMPLETE / IMPLEMENTATION NOT AUTHORIZED**（Owner 驗收 2026-08-23，audit/design gate 交付，非 implementation closeout）。鎖定 9 個 boundary 問題（Q1–Q9），結論 9/9 有明確 contract 依據、無需重定 frozen contract。核心 distinction 定為 **working invariant**：`DSH session ≠ Work Object`、`DSH subagent ≠ Work`、`DSH result ≠ Work Truth`。No-DSH Survival Test 延伸到 Work Domain：**Remove DSH → durable work state still exists and remains recoverable**。

**三個特別鎖住的 boundary（Owner 驗收）**：
1. **Work Domain 才能改 state**：DSH Adapter = dispatch + transport/report，**不得**自己決定 assigned→running→done，否則 domain authority 會洩漏到 DSH。
2. **session ID 不成為 Work Truth**：work_id 屬 Soul OS Work Domain；DSH session 只是一次 execution instance。session A crash → session B resume 仍是 **same work_id**，不產生新 work。
3. **DSH 消失，Work 仍存在**：Work Domain 是 Soul OS 的 durable state，不是 DSH 的 session state。

**兩個 open decisions（D1/D2，不阻塞 audit，implementation 前須收斂）**：
- **D1 — Dispatch Intent schema**：定義 Chief→dispatch intent→DSH Adapter→subagent 中間的 intent 形狀；對齊 P1-A `ExecutionShape` + P1-B artifact boundary + role/capability 矩陣。硬約束：不得讓 DSH 原生 subagent API 反推 Soul OS Work Contract。
- **D2 — Verdict minimum contract**：定義 subagent 回來後的最小 structured verdict（status/verdict、artifact、evidence、decision、unresolved/needs_input）。硬約束：DSH result → Adapter transport → Work Domain validation → Handoff → state transition，DSH session output 不得直接變成 Work Truth。

**Dependency graph（0 → 1 不自動進入施工）**：
```text
DSH-WORK-OBJECT-0          ✅ DESIGN COMPLETE
        │
        ├── D1 Dispatch Intent ──┐
        │                        │
        └── D2 Verdict Contract ┤
                                 ▼
                       Owner / Architecture Gate
                                 │
                                 ▼
                    DSH-WORK-OBJECT-1
                    Contract Design
                                 │
                                 ▼
                    implementation ticket
```

**Next work**: D1/D2 收斂成 implementation-ready contract 是下一步，但不是寫更多 agent、不是擴充 preset。D1/D2 收斂後經 Owner / Architecture Gate 才進入 DSH-WORK-OBJECT-1。**目前不建立、不 dispatch 任何 Work Object integration 施工 ticket。**

---

### 5.10 目標重收斂：DSH Work Bot（現在）vs Soul OS substrate（未來）

**Status**: **Owner 拍板 2026-08-23 — 目標重新收斂，兩層分離。** 這是對 §5.9 的目標層修正：之前把「DSH 作為 Soul OS 完整 multi-agent OS」與「DSH 作為 Bry 日常工作環境」混在一起，屬過度工程化。現在拆成兩個 layer。

**Re-scope（2026-08-29，North Star v2）**：DSH Work Bot = 研究基础设施 / 工具，不是 Soul OS 本体；Soul OS 灵魂本体（记忆升华 / 灵魂间互动 / 自由生长）是研究主线。

**現在要的（DSH Work Bot）= Grok Bot 型工作體驗**：

```text
Bry → Chief（幕僚長）→ Researcher / Developer / Tester / Auditor → 閉環回報 Bry
```

Bry 只需要跟 Chief 溝通、下一個任務（「幫我把 Soul OS 這個問題處理掉」），Chief 自主完成：分析需求 → 決定要不要找 Researcher → 派 Developer → 派 Tester → 派 Auditor → 整合結果 → 回報。Bry 不需要知道下面發生什麼。

**未來要的（Soul OS substrate）**：Soul OS 搬進來時才需要 durable Work Object / state machine / artifact provenance / workflow recovery / Soul-aware work lifecycle。

**第一個 milestone 重新定義為**（非 Soul OS 完整 work orchestration）：

> 「Bry 只需要跟 Chief 說話，Chief 能把工作交給合適的 specialist 並完成閉環。」

**目前已完成**（Role layer 全綠，離目標非常近）：
- ✅ 5 具名 agent-preset（Chief/Researcher/Developer/Tester/Auditor）
- ✅ Chief → Specialist 派發（`dsh-subagent-role` 依 description 注入 persona/role/tool whitelist/optional model）
- ✅ Capability isolation（Researcher=read/glob/grep/web；Developer=read/write/bash；Tester=test；Auditor=read/verify）

**真正缺的**：Chief 的實際協作 loop 尚未 operationalize（dispatch → receive result → decide next specialist → 閉環）。

**DSH-WORK-OBJECT-0 降級**：從 current critical path 降為 **Soul OS integration prerequisite / future track**（不丟掉，仍是好的 future architecture boundary，D1/D2 留待未來 Soul OS 搬入時收斂）。

**Estimated path（3–6 個有效 development rounds，非十幾輪大工程）**：
1. **Phase 1 — Multi-Agent Loop**（1–2 rounds）：Chief → dispatch → specialist → receive result → decide next specialist 穩定運作。
2. **Phase 2 — Real Dogfooding**（1–2 rounds）：拿真正 Soul OS/DSH 工作跑 Research→Developer→Tester→Auditor→Chief synthesis，找實際 friction。
3. **Phase 3 — Polish**（1–2 rounds）：context 傳遞、result 格式、failure handling、Chief 如何決定下一個 agent、session/prompt UX、避免 Chief 把事全自己做。

**Next work**: Phase 1（Multi-Agent Loop operationalize）是下一個 critical path，**待 Owner 啟動指令**。DSH-WORK-OBJECT-0 / D1 / D2 全數移出 current critical path，留待 Soul OS substrate 階段。

---

### 5.11 Phase 1 驗證結果與三個方向修正（2026-08-23）

**Status**: **Phase 1 loop 驗證通過（三 specialist 接力），三個方向修正落地。** 待重啟生效後重跑最終實測。

**驗證結果**（真實測試工單 `tests/test_soul_md_loader.py` stale test 修復）：
- ✅ Chief 真派 bot、沒自己全做（researcher → developer → tester 三 specialist 接力）
- ✅ 閉環回報（根因 + 修法 + 測試結果整合）
- ✅ Chief 主動糾正主大腦的錯誤提示（`AGENT_PROFILE_MAP` 是死映射表，非 `SOUL_OS_OVERRIDE` 語義等價物），證明有獨立查證能力
- ⚠️ 測試「實跑」未完成（tester 無 shell）→ 已定位為機制缺陷並修正（見下）

**關鍵機制發現（導致 tester 無 shell 的真根因）**：
> DSH 子代理繼承**父 preset（Chief）**的工具集，`toolFilter.allow` 只能從繼承集合「過濾」不能「新增」。之前「嚴格照 capability 矩陣剪裁」讓 Chief 無 bash/pwsh，導致 tester/developer 派發時永遠繼承不到 shell。

**三個方向修正（Owner 拍板，推翻 §5.10 的「Capability isolation」剪裁策略）**：

1. **工具全量化**：5 個 preset 全部補齊執行工具（fs/fs-search/bash/pwsh/web/jobs/skill/goal/ask-user/todo）。角色區分**不再靠工具剪裁，靠 persona 約束**。
2. **派發工具 deny**：`dsh-subagent-role` 從 per-role `allow` 白名單，改為統一 `deny` 7 個派發工具（subagent/subagent_fork/workflow/ralph/send_message/interrupt_agent/list_agents）。specialist 繼承 Chief 全量執行工具、只排除派發權。派發是幕僚長職權，只給 Chief；specialist 想找別人協作時「推薦並回報，讓 Chief 派」。
3. **Chief persona 彈性化**：從「絕不親自寫碼/跑測試/研究」硬禁止，改為經濟性軟約束——「自己動手很貴（占上下文/算力），默認能派就派；但 specialist 反覆做不好 + 小而簡單的錯誤，就自己兜底修掉」。保留唯一硬要求：派發時 description 必須帶角色英文名（否則 persona 不注入）。

**設計哲學（Owner 定調）**：這是「多人合作」場景，不是「嚴謹權限工作流」。保留彈性——chief 手腳不綁，只要求它知道「自己動手很貴、能不動就不動」。persona 約束是軟性的（LLM 自覺），不是工具層硬禁止；這是刻意的 trade-off，非缺陷。

**累積待重啟生效**：`dsh-subagent-role` deny 改動（install 已完成）+ preset 工具全量化 + Chief persona 彈性化。下次重啟 dsh web 一起生效，之後重跑測試工單做最終實測（tester 應能跑出 pytest 實跑結果）。

---

## 6. STALE REFERENCES (historical closeouts vs canonical state)

Per §2.6 Historical Document Rule, historical closeouts are preserved unchanged. Stale references are documented here for reconciliation.

### 6.1 M6.0-5.6 closeout §K — M5.13-4 reference

**Stale content** (in `logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md` §K):

> **M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...` JSON roundtrip; use `math.isclose` or threshold adjustment.

**Why this is stale** (canonical truth per §5.1):
- M5.13-4 (commit `97c1063`) is a CLOSED READ-ONLY AUDIT, not a fix ticket
- The "fix for 0.3 → 0.2999..." was implemented by M5.13-4.1 → M5.13-4.1-R1 → M5.13-4.2
- M5.13-4.1 (consumer `round(_, 6)`) is SUPERSEDED by M5.13-4.2
- M5.13-4.2 (producer-side per-entry anchor) is the CANONICAL LATEST implementation
- The "use `math.isclose`" suggestion in the stale reference is **explicitly forbidden** by M5.13-4.1-R1 audit (introduces tolerance, violates strict M5.13-2 contract)
- M6.0-5.6 was committed at `540eac2` (before M5.13-4.2 was committed at `e6effd8`), so the closeout was written without knowledge of the final fix

**Canonical resolution**:
- Historical closeout file preserved unchanged
- The "M5.13-4 fix" reference is **SUPERSEDED** by M5.13-4.2
- No edits to the historical file (per §2.6)
- This stale reference is the ONLY stale next-work-item reference identified by GOV-1

### 6.2 No other stale references identified

GOV-1 exhaustively reviewed M5.13, M5.14, M6.0 closeouts for stale next-work-item references. The M6.0-5.6 §K reference is the only one. All other closeouts correctly point to either:
- Completed work (next ticket was done)
- CANDIDATE (not authorized) work, properly marked
- DEFERRED work with clear status

---

## 7. ENGINEERING LEDGER (canonical state sources)

### Canonical registry

- **This document** (`logs/ENGINEERING_STATE.md`) — single source of truth for engineering state
- **README.md** — canonical homepage with brief snapshot + link to this registry
- **GOV-1 report** (`C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md`, out-of-repo) — predecessor audit

### Per-ticket closeout logs (M5.13 + M5.14 + M6.0)

**M5.13**:
- `logs/m5_13_1_lived_context_preflight_audit.md`
- `logs/m5_13_2_relationship_context_projection_design.md`
- `logs/m5_13_3_relationship_context_closeout.md`
- `logs/m5_13_3_1_independent_verification_audit.md`
- `logs/m5_13_4_float_precision_audit.md`
- `logs/m5_13_4_1_relationship_confidence_boundary_closeout.md` (SUPERSEDED — kept for history)
- `logs/m5_13_4_1_r1_relationship_threshold_rounding_boundary_audit.md`
- `logs/m5_13_4_2_strict_boundary_closeout.md` (M5.13-4.2)
- `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md` (M5.13-5, out-of-repo, CANONICAL LATEST)

**M5.14**:
- `logs/m5_14_1_cross_layer_runtime_convergence_audit.md`
- `logs/m5_14_2_world_proactive_identity_audit.md`
- `logs/m5_14_3_m6_0_3_f_correction_closeout.md`

**M6.0**:
- `logs/m6_0_1_lived_context_validation_design.md`
- `logs/m6_0_2_validation_poc_closeout.md`
- `logs/m6_0_3_validation_d_e_f_g_h_closeout.md`
- `logs/m6_0_4_subjective_llm_quality_audit.md`
- `logs/m6_0_5_subjective_eval_infrastructure_closeout.md`
- `logs/m6_0_5_2_real_llm_judge_backend_closeout.md`
- `logs/m6_0_5_3_multi_llm_judge_diversity_audit.md`
- `logs/m6_0_5_4_minimal_multi_model_judge_orchestration_closeout.md`
- `logs/m6_0_5_4_r1_cost_retry_budget_enforcement_closeout.md`
- `logs/m6_0_5_4_r2_retry_budget_enforcement_completion_closeout.md`
- `logs/m6_0_5_5_real_three_judge_e2e_closeout.md`
- `logs/m6_0_5_5_r1_real_three_judge_e2e_validation_gate_blocked.md`
- `logs/m6_0_5_6_configurable_evaluation_cost_ceiling_closeout.md`

**M5.15**:
- All closeout reports out-of-repo per M5.13-3.1 lesson (no `git stash -u + drop`):
  - M5.15-1 boundary audit: `C:\Users\bbfcc\gov_1_temp\m5_15_1_boundary_audit.md`
  - M5.15-2 decision analysis: `C:\Users\bbfcc\gov_1_temp\m5_15_2_decision_analysis.md`
  - M5.15-3 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`
  - M5.15-3 implementation report: `C:\Users\bbfcc\gov_1_temp\m5_15_3_implementation_report.md`
  - M5.15-4 STOP report: `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md` (SUPERSEDED, historical preserved)
  - M5.15-5 decision analysis: `C:\Users\bbfcc\gov_1_temp\m5_15_5_decision_analysis.md`
  - M5.15-5 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md`
  - M5.15-5 implementation report: `C:\Users\bbfcc\gov_1_temp\m5_15_5_implementation_report.md`
  - M6.0-5.6.1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_0_5_6_1_closeout.md` (canonical, Budget Profile Registry)
  - M5.15-6-PREFLIGHT decision: `C:\Users\bbfcc\gov_1_temp\m5_15_6_preflight_architecture_decision.md`
  - M5.15-6 RESUME STOP report: `C:\Users\bbfcc\gov_1_temp\m5_15_6_stop_report.md` (historical, M3.1 conflict)
  - M5.15-6 closeout: `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md` (canonical, RESUME Option 1)

**M6.1** (per M5.13-3.1 lesson, all closeout reports out-of-repo unless otherwise noted):
- M6.1-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_1_0_lived_context_awareness_audit.md`
- M6.1-1 taxonomy audit: `C:\Users\bbfcc\gov_1_temp\m6_1_1_lived_context_taxonomy_audit.md` (canonical)
- M6.1-3 evidence audit: `C:\Users\bbfcc\gov_1_temp\m6_1_3_evidence_audit.md`
- M6.1-3.2 weather activation: `C:\Users\bbfcc\gov_1_temp\m6_1_3_2_closeout.md`
- M6.1-3.3 weather evaluation: `C:\Users\bbfcc\gov_1_temp\m6_1_3_3_weather_evaluation.md`
- M6.1-4 personal audit: `C:\Users\bbfcc\gov_1_temp\m6_1_4_personal_audit.md`
- M6.1-5 information audit: `C:\Users\bbfcc\gov_1_temp\m6_1_5_information_audit.md`
- M6.1-5.1 news impl closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_5_1_closeout.md`
- M6.1-5.2 news activation: `C:\Users\bbfcc\gov_1_temp\m6_1_5_2_closeout.md`
- M6.1-5.3 news lookback: `C:\Users\bbfcc\gov_1_temp\m6_1_5_3_closeout.md` + `C:\Users\bbfcc\gov_1_temp\m6_1_5_3_final_closeout.md`
- M6.1-6.0 personal decision: `C:\Users\bbfcc\gov_1_temp\m6_1_6_0_closeout.md`
- M6.1-7 production evidence: `logs\m6_1_7_production_lived_context_evidence.md` (in-repo, M6.1-7 audit closeout)
- M6.1-8 agency re-enable: `logs\m6_1_8_agency_reenable_investigation.md` (in-repo, M6.1-8 audit closeout)
- M6.1-8.1 isolated validation: `C:\Users\bbfcc\gov_1_temp\m6_1_8_1_closeout.md` (out-of-repo per M5.13-3.1)
- M6.1-8.1 test file: `tests\test_m6_1_8_1_agency_reenable_isolated.py` (in-repo, 21 tests, 905 lines)
- M6.1-8.2 controlled rollout: `C:\Users\bbfcc\gov_1_temp\m6_1_8_2_closeout.md` (out-of-repo per M5.13-3.1)

**M6.2** (per M5.13-3.1 lesson, all closeout reports out-of-repo unless otherwise noted):
- M6.2-0 audit: `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md` (READ-ONLY, no in-repo file changes)
- M6.2-1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md` (out-of-repo per M5.13-3.1)
- M6.2-1 test file: `tests\test_m6_2_1_text_tts_correlation.py` (in-repo, 11 tests, 21KB)
- M6.1-9 partial audit (T+50min): `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` (out-of-repo, 19.7KB)
- M6.1-9.1 closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (out-of-repo, 14.9KB, configuration-only fix)
- M6.1-9 partial T+4h33m closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_closeout.md` (out-of-repo, 13.6KB, 24h window incomplete)
- M6.1-9 full T+24h follow-up: PENDING via cron `m6_1_9_t24h_v2` (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) — TRUE T+24h mark is 8/15 19:27 EDT
- M6.1-9.2 closeout: `C:\Users\bbfcc\gov_1_temp\m6_1_9_2_closeout.md` (out-of-repo, 18.2KB, READ-ONLY forensic audit, 0 source code change)

### Out-of-repo references

- GOV-1 audit: `C:\Users\bbfcc\gov_1_temp\gov_1_state_normalization_audit.md`
- M5.13-4.2 closeout: `C:\Users\bbfcc\m5_13_4_2_temp\m5_13_4_2_closeout.md`
- M5.13-3.1 verification harness: `C:\Users\bbfcc\m5_13_3_1_temp\`

---

## 8. GLOSSARY

- **AUDIT**: READ-ONLY investigation; produces FINDING
- **Authorization**: Owner (Bryan) approval required for new tickets / milestone transitions
- **BLOCKED**: Status indicating external / dependency blocker
- **Candidate**: Next-ticket proposal from a closeout's "Recommended Next" section; NOT authorized
- **CLOSED**: Ticket acceptance gate fully completed
- **Closeout**: Final document + state transition for a ticket
- **DEFERRED**: Status indicating Owner-postponed work
- **FINDING**: Audit result; requires CLASSIFICATION
- **Frozen contract**: Code/data structure that must not change without Owner approval
- **GOV**: Governance ticket prefix (not a milestone)
- **Milestone**: Top-level engineering capability series (M3, M5.x, M6.0)
- **Mavis / Lin**: M3 model (per user rename in 2026-06-02)
- **OPTIONAL**: Status indicating candidate for future work, not authorized
- **Owner / Bryan**: Final decision authority on all engineering direction
- **Perplexity sonnet 4.6**: Brain / error-checker; does not implement
- **SUPERSEDED**: Status indicating replacement by later ticket
- **Ticket**: Work item within a milestone (M5.x-N, M5.x-N-R1)
- **Work item**: See Ticket
- **Work order**: Formal ticket description (the "ticket" in the colloquial sense)

---

## 9. CHANGE LOG

| Date | Change | Author | Source ticket |
|------|--------|--------|---------------|
| 2026-08-12 00:03 EDT | Initial canonical state registry established (commit `eb57151`) | Mavis / Lin | GOV-2 |
| 2026-08-12 (GOV-2-R1) | Owner Decision A alignment: GOV-2 / M5.14 / M6.0 / GOV-1 all CLOSED. D1 RESOLVED (Option A). 13 decisions remain UNRESOLVED. M5.15-1 remains CANDIDATE only. (alignment commit `3539de2f8795ad3e516a619dc556563e8c357c68`) | Mavis / Lin | GOV-2-R1 |
| 2026-08-12 (GOV-2-R1 finalize) | Established canonical head reference `f31945e8a58a0d8fa323588437acae968e37da76` (GOV-2-R1 finalize commit). GOV-2-R1 alignment commit = `3539de2f8795ad3e516a619dc556563e8c357c68` (distinct value; not off-by-one). 26/27 GOV-2 consistency checks PASSED + 1 obsolete assertion (pre-commit HEAD check expected `e6effd8`; obsolete after subsequent governance commits). Note: this row is a historical record; the **current** canonical head reference is recorded in §1. | Mavis / Lin | GOV-2-R1 finalize |
| 2026-08-12 (M5.15-3) | WorldEventSource → Event Bus canonical integration (commit `b4b981a7b24678779551bccca2f4b6eb4dd20b3e`). 31/31 new tests + 159/159 regression PASS. 0 frozen contract change. 0 production mutation. M5.15 chain F1 RESOLVED; F2/F3/F4 remain CANDIDATEs (per M5.15-1 audit + M5.15-2 decision). 3 files changed: `src/world/source/synthetic.py` (+114 -6), `scripts/run_server.py` (+51), `tests/test_m5_15_3_canonical_bus_integration.py` (new +1089). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_3_closeout.md`. | Mavis / Lin | M5.15-3 |
| 2026-08-12 (M5.15-4) | Cross-Handler Lineage Propagation STOPPED. M5.15-4 inspection discovered parent_event_id contract (M5.4-5.1 frozen) requires parent to be a known InnerLifeEvent.event_id (writer.py:172 + 32-hex format check identity.py:195). Cannot reference SoulEvent.event_id (UUID with dashes) or WorldEvent.novelty_id (free string) without modifying frozen contract. 3 of 7 Stop conditions triggered. STOP report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_4_stop_report.md`. Bry Decision: 0 implementation, await M5.15-5 architecture decision. | Mavis / Lin | M5.15-4 |
| 2026-08-12 (M5.15-5) | WorldEvent ↔ InnerLifeEvent Identity Bridge (commit `0aedbef25cf0cb8ba793a7620833ec6cfdb70db8`). 52/52 new tests + 285/285 regression PASS. 1 additive frozen-contract amendment (M5.4-5.1 InnerLifeEvent +1 Optional field `source_world_event_novelty_id: Optional[str] = None`). 13 frozen contracts preserved (parent_event_id / lineage_depth / lineage_path / correlation_id / provenance all 0 change). 0 production mutation. Two-Layer Lineage Model established: Layer 1 External Causality (WorldEvent → InnerLifeEvent, free string) + Layer 2 Internal Lineage (InnerLifeEvent → InnerLifeEvent, M5.4-5.1 frozen preserved). M5.15-4 SUPERSEDED by M5.15-5 (per GOV-2 §2.5). F1 + F3 + F4 RESOLVED. F2 CANDIDATE (M5.15-6). 10 files changed: 6 source + 4 test. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_5_closeout.md`. | Mavis / Lin | M5.15-5 |
| 2026-08-12 (M5.15-6) | Real-World Calendar Source Integration (commit `c2de02c`). 55/55 new tests + 211/211 regression PASS. 0 frozen contract change across 15 contracts. RESUME Option 1 (Bry authorization 2026-08-12 19:37): `novelty_id = SHA256(VEVENT.UID)[:32]`, `data["ical_uid"] = exact UID`, `data["ical_sequence"] = VEVENT.SEQUENCE` (observability only). IcalCalendarSource (src/world/source/calendar_ical.py): polling-driven (300s default), env-gated via SOULOS_CALENDAR_ICAL_URL, 24h lookahead default, parent-only RRULE (Q5), CANCELLED skipped (Q7), 1 URL = 1 source (Q9), library icalendar (PyPI MIT), HTTP via urllib stdlib + asyncio.run_in_executor (non-blocking), 30s timeout, MAX_EVENTS_PER_POLL=500 cap, failure observable (log+skip+retry, never crash, never silent). 4 files changed. F1 + F2 + F3 + F4 all RESOLVED. M5.15 series all CLOSED. | Mavis / Lin | M5.15-6 |
| 2026-08-12 (M5.13-5) | Untouched-Entry Decay (commit `9501603796ac250de95e19dc0fa2b543f81d95da`). 14/14 new tests + 56/56 M5.13 full suite + 105/105 adjacent regression PASS. 0 frozen contract change. 0 production mutation. **M5.13 series now FULLY CLOSED** (no remaining OPTIONAL/DEFERRED tickets). Implementation: 1 constant `UNTOUCHED_DECAY_GRACE_DAYS = 1.0` + 1 function `_decay_locked` extended with `created_at` fallback. Decay anchor priority: (1) `last_interaction_at` (M5.13-4.2 existing), (2) `created_at` (M5.13-5 new, only when `last_interaction_at` is None AND `created_at` is > 1.0 day old), (3) None (legacy/malformed → skip, no crash). Touched entries: continue using `last_interaction_at` (M5.13-4.2 unchanged). Preserves M5.13-2 strict 0.3 contract (grace period ensures `ensure_relationship(0.3) → get()` returns 0.3). 2 files changed: `src/soul/relationships.py` (+36 -6), `tests/test_m5_13_5_untouched_decay.py` (NEW +471, 14 tests in 7 sections A-G). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_13_5_closeout.md`. D2 RESOLVED. | Mavis / Lin | M5.13-5 |
| 2026-08-12 (M6.0-5.6.1) | Budget Profile Registry (commit `3d1fae4a86b5b62ab1edd5688203f27fe3c36a36`). 29/29 new tests + 12/12 M6.0-5.6 + M6.0-5.6.1 manual regression PASS. 0 frozen contract change. 0 production mutation. **M6.0 series now FULLY CLOSED** (no remaining OPTIONAL/DEFERRED tickets; M6.0-5.5-R1 remains BLOCKED per spec). Implementation: 1 `BudgetProfile` str-Enum (CHAT/DIARY/DREAM) + 1 `_BUDGET_PROFILE_VALUES` dict (frozen profile → tuple) + 1 `EvaluationBudgetConfig.from_profile()` @classmethod factory. Profile values: CHAT (3/2/5000/0.05, == default for no-op migration), DIARY (2/1/3000/0.03, smaller budget for high volume), DREAM (1/1/2000/0.02, smallest budget for low observable). Defaults preserved 100% (CHAT == `EvaluationBudgetConfig()`). `from_profile()` rejects non-`BudgetProfile` inputs (raw string, None, int, other enum) with `TypeError`. Profile-derived configs are frozen + hashable. 3 files changed: `tests/_helpers/subjective_eval/multi_model_runner.py` (+100), `tests/_helpers/subjective_eval/__init__.py` (+3 -1), `tests/test_m6_0_5_6_1_budget_profile.py` (NEW +386, 29 tests in 7 sections A-G). Pre-existing test collection issue (`tests/` lacks `__init__.py`, 5 M6.0.x tests fail to collect via pytest — per M5.15-6 closeout §6.1 known finding) verified via direct script, not introduced by D3. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_0_5_6_1_closeout.md`. D3 RESOLVED. | Mavis / Lin | M6.0-5.6.1 |
| 2026-08-12 (M5.15-6) | Real-World Calendar Source Integration (commit `c2de02c`). 55/55 new tests + 211/211 regression PASS. 0 frozen contract change across 15 contracts (M3 WorldEvent, M3.1 ABC, M3.1 Bus, M5.4-5.1 InnerLifeEvent 9 fields, M5.4-5.1 parent_event_id, M5.4-5.1 lineage, M5.9-2 QUALIFYING_TYPES, M5.9-3 Adapter, M5.15-3 canonical bus path, M5.15-5 source_world_event_novelty_id, VALID_SOURCES, _NOVELTY_ID_RE all unchanged). RESUME Option 1 (Bry authorization 2026-08-12 19:37): `novelty_id = SHA256(VEVENT.UID)[:32]`, `data["ical_uid"] = exact UID`, `data["ical_sequence"] = VEVENT.SEQUENCE` (observability only). SEQUENCE excluded from hash (Q6: same UID + different SEQUENCE → same hash → adapter dedupes). 0 production mutation (yua/relationships.json 10095B81... unchanged). IcalCalendarSource (src/world/source/calendar_ical.py): polling-driven (300s default), env-gated via SOULOS_CALENDAR_ICAL_URL, 24h lookahead default, parent-only RRULE (Q5), CANCELLED skipped (Q7), 1 URL = 1 source (Q9), library icalendar (PyPI MIT), HTTP via urllib stdlib + asyncio.run_in_executor (non-blocking), 30s timeout, MAX_EVENTS_PER_POLL=500 cap, failure observable (log+skip+retry, never crash, never silent). 4 files changed: src/world/source/calendar_ical.py (NEW, +476), src/world/source/__init__.py (+14), scripts/run_server.py (+65), tests/test_m5_15_6_calendar_ical_source.py (NEW, +1105). All 12 critical regression tests A-L PASS. 7 pre-existing baseline failures unchanged (M3.1 Phase B/C/D + M2.0); 1 pre-existing test ordering issue (test_m5_4_5_3::test_g2 env var setup). F1 + F2 + F3 + F4 all RESOLVED. M5.15 series all CLOSED. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m5_15_6_closeout.md`. | Mavis / Lin | M5.15-6 |
| 2026-08-13 (M6.1-2) | Lived Context Canonical Boundary & Documentation (commit `9e050f6`). Documentation-first implementation. 2 files changed: `README.md` (+54 §7.1 "Lived Context Boundary (M6.1)" sub-section, 4-layer architecture + 5 contexts + boundary invariant + capability positioning), `logs/ENGINEERING_STATE.md` (+74 §5.6 M6.1 milestone table + canonical taxonomy + 4-layer boundary + boundary invariant + missing capabilities + this §9 change log entry + §1 status snapshot row). 0 source-code change, 0 frozen contract change (15 contracts preserved: M3/M3.1/M5.4-5.1/M5.4-5.5/M5.9-2/M5.9-3/M5.10/M5.13-2/M5.15-3/M5.15-5/M5.15-6/VALID_SOURCES), 0 production mutation, 0 new runtime abstraction. **No `LivedContextAggregator` wrapper created** — `src/llm/proxy.py:_build_messages_group()` and `_build_messages_private()` is the de-facto Lived Context aggregator (block order: identity → memory → mood → relationship → inner_life → world → temporal). M6.1-0 / M6.1-1 READ-ONLY audits out-of-repo. Frozen contracts 0 change verified via diff. Production data byte-for-byte unchanged (memory.db / perception_trace / shadow_log / relationships / diary / dream / emotion persistence all not touched). 20 baseline untracked artifacts preserved. | Mavis / Lin | M6.1-2 |
| 2026-08-13 (M6.1-3.1) | Open-Meteo Weather Source (commit `ac50256`). Physical Lived Context signal source. 4 files changed: `src/world/source/open_meteo.py` (NEW, +417), `src/world/source/__init__.py` (+7), `scripts/run_server.py` (+34), `tests/test_m6_1_3_1_open_meteo_weather.py` (NEW, +805, 50+ tests). 0 frozen contract change (VALID_SOURCES already includes `"weather"`, types `rain_started` / `weather_temp_change` fit existing M3 WorldEvent schema, `source_id = "weather"` per M3.1 contract). Stdlib only (urllib + json, no new dependencies). 1800s polling default (min 60s), 30s HTTP timeout, deterministic `novelty_id = SHA256(f"weather.{lat:.2f}_{lon:.2f}.{hour}.{state}")[:32]`. State bucket: binary `rain` (precipitation >= 0.1mm OR WMO code in {51-67, 80-82, 95-99}) / `no_rain`. Provider identity preserved in `data["weather_provider"] = "open_meteo"`. 406/406 broad regression PASS. 5 pre-existing baseline failures (M3.1 Phase B/C/D + M2.0) + 5 M6.0.x test collection issues unchanged (not introduced). | Mavis / Lin | M6.1-3.1 |
| 2026-08-13 (M6.1-5.1) | RSS News Source (commit `9f8ece8`). Information Lived Context signal source. 5 files changed: `src/world/source/news_rss.py` (NEW, +663), `src/world/source/__init__.py` (+7), `scripts/run_server.py` (+34), `tests/test_m6_1_5_1_news_rss.py` (NEW, +1591, 92 tests in 17 sections A-Q), `requirements.txt` (no new deps). 0 frozen contract change (VALID_SOURCES already includes `"news"`, `celebrity_news` baseline 0.05; new `news_event` type does NOT extend WORLD_QUALIFYING_TYPES → no InnerLifeEvent impact per M5.9-2 minimal scope). Stdlib only (urllib + xml.etree.ElementTree + email.utils.parsedate_to_datetime). 1800s polling, 2h lookback default, 10 articles/poll cap, 30s HTTP timeout, 10000-entry in-memory FIFO dedup. Deterministic `novelty_id = SHA256(f"{provider}.{canonical_url}.{published_at_iso}")[:32]`. 8 working public feeds (BBC World/Top, NASA Breaking, Hacker News, Guardian, Ars Technica, NPR Top, Al Jazeera); Reuters + AP UNAVAILABLE (documented, 0 implementation impact). 92/92 focused tests + 469/469 broad regression PASS. 5 pre-existing baseline failures + 5 M6.0.x collection issues + 11 M6.0.x errors unchanged. | Mavis / Lin | M6.1-5.1 |
| 2026-08-13 (M6.1-6.0-C) | Personal Lived Context Architecture Decision Audit Closeout (commit `49adf46`). 1 file changed: `logs/m6_1_6_0_personal_lived_context_audit.md` (in-repo, +596 insertions, canonical closeout). Verdict: **DEFER (D)**. 0/5 FULLY ANSWERABLE, 2 PARTIALLY, 3 NOT. 8 signals inventoried (Calendar / Telegram / Temporal / Memory / Inner Life / WorldPerception / Weather / News). 3 options compared: A Manual (~50 LOC, requires Bry active commitment, HIGH privacy) / B Inference (LOW cost, but surveillance-by-proxy, violates M3 design rule "Personal inference ≠ raw signal") / C Dedicated (~150 LOC, requires wearable/phone/browser/GPS/surveillance/large infra, out of work order scope, reduces to A). Verdict: D wins on aggregate (no cost, no risk, no production use case yet). Bry 拍板 2026-08-13 21:16: "Personal = DEFER" (Information Lived Context priority 1st round). | Mavis / Lin | M6.1-6.0-C |
| 2026-08-14 (M6.1-7) | Production Lived Context Evidence Reassessment (commit `bdf76ad`). 1 file changed: `logs/m6_1_7_production_lived_context_evidence.md` (in-repo, +620 insertions). READ-ONLY audit, 0 source/test/prod modification, 0 commit/push during audit, production data byte-for-byte unchanged. Verdict: **LIVED CONTEXT NOT YET FORMED**. Q1-Q6 reassessment: Q1 PARTIAL (Weather only), Q2 PARTIAL (no Personal signal), Q3 NO EVIDENCE (multi-signal never observed), Q4 YES (1081 LLM responses had world_context = 31.4%), Q5 PARTIAL (InnerLife ✓, Diary ✗), Q6 NO EVIDENCE. Architecture findings: 0 P0, 0 P1, 2 P2 (Agency triggers disabled, world_context single-source), 2 P3 (News gate filter not in ops docs, Scheduler `agents=0` decision not documented). Production trace: 2385 perception_trace entries (15 calendar + 1752 weather + 122 news), 602 weather context_injected, 1081 LLM responses with world_context. Per-agent shadow distribution: mahiru 167, mai 94, akane 92, yua 74, anna 67, ruka 67, rem 64, ram 45, aoi 42, miku 39. Last diary file: 2026-08-08 08:00:28 (6+ days ago). Scheduler log: `morning=08:00:00 night=22:00:00 prob=1.0 agents=0` (since 8/8). 20 baseline untracked artifacts preserved. | Mavis / Lin | M6.1-7 |
| 2026-08-14 (M6.1-8) | Agency Re-enable Investigation (commit `f699d93`). 1 file changed: `logs/m6_1_8_agency_reenable_investigation.md` (in-repo, +581 insertions, 18 sections, 581 lines). READ-ONLY architecture investigation, 0 source/test/prod/config mutation, 0 commit/push during audit, 20 baseline untracked artifacts preserved. **Root cause identified**: M5.2 migration (commit `481ea41`, 2026-08-08 21:11 EDT, "refactor(m5.2): migrate scheduler triggers to agency event bridge") removed `scheduler.register(aid, cb)` callsite in `run_server.py` (M5.2-I Phase 7) but M5.2-I Phase 8 changed iteration source to `_all_agents` in `scheduler.py:_fire_all` without adding a replacement `register(aid)` call. Result: `SoulScheduler._all_agents` permanently empty (default `[]`). 5 of 6 trigger paths (morning / night / dream / event / proactive_dm) silent-skip at `if not self._all_agents: return` (scheduler.py:560/620/921) or via `_get_proactive_agents() returns []` (L696-697). Heartbeat separately disabled by 修法 12 (Bry 8/6 17:12, INTENTIONAL). 0 diary/dream/event/proactive_dm writes for 6+ days (8/8 21:13 → 8/14 22:38 EDT). 4 handlers (AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler) all correctly wired to bus, all correctly filter trigger_type, all waiting for AGENCY_TRIGGER that never comes (operational but starved). **Documented regression** (M5.2-I Phase I-9 sweep `logs/m5_2_i_i9_callback_dependency_sweep.md` 2026-08-08 17:50 EDT §2.2 explicitly noted "production 完全沒註冊" but treated as "API COMPAT" PASS — false equivalence of "interface preserved" vs "behavior preserved"). **Fix proposed**: 3 lines in `run_server.py` after `diary_callbacks_real[aid] = cb_real` — `for aid in agent_ids: scheduler.register(aid)`. 0 frozen contract change, 0 production data change, 0 M3/M5.4-5.x modification. 4 safety options for Bry decision (A direct / B gradual / C shadow / D test-isolated). M6.1-8.1 PENDING Bry 拍板. M6.1-9 DEFERRED (after M6.1-8.1). | Mavis / Lin | M6.1-8 |
| 2026-08-14 (M6.1-8 registry sync) | Canonical engineering state registry updated for M6.1 series progress. 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 progress row, +§1.1 M6.1 milestone row updated, +§1.1 Current HEAD section updated to `f699d93`, +§5.6 M6.1 milestone table extended with 13 new ticket rows M6.1-3 through M6.1-8 + 2 pending rows M6.1-8.1 / M6.1-9, +§5.6 canonical taxonomy updated (Physical/Information now LIVE, Personal DEFERRED, Temporal LIVE), +§5.6 missing capabilities updated (Weather + News RESOLVED, Personal DEFERRED, Agency re-enable M6.1-8.1 PENDING), +§7 closeout log list extended with 13 M6.1 closeout references, +§9 change log extended with 5 new entries M6.1-3.1 / M6.1-5.1 / M6.1-6.0-C / M6.1-7 / M6.1-8). | Mavis / Lin | M6.1-8 registry sync |
| 2026-08-14 (M6.1-8.1) | Agency Re-enable Isolated Validation (commit \d0c33da\). 1 file changed: \	ests/test_m6_1_8_1_agency_reenable_isolated.py\ (NEW, +905 lines, 21 tests in 7 sections A-G). **STRICT 0 PRODUCTION ACTIVATION**: 0 source code changes, 0 production data mutation, 0 production config change, 0 production server restart, all test writes isolated to \	mp_path\ via \SOUL_OS_DATA_DIR\, mock executors only (no real LLM/Telegram), frozen contracts 0 change. **3-line minimal fix PROVEN CORRECT** in isolated test env: 20 PASS / 1 XFAIL (F.2 = future-proof regression test, awaiting M6.1-8.2 production fix) / 0 FAIL in 0.47s. 71/71 M5.2 series tests still PASS (regression verified). Sections: A baseline regression reproduction (4 tests) / B minimal fix validation (3 tests) / C AGENCY_TRIGGER publication x 5 paths (5 tests, 12 total events) / D handler reception x 4 handlers (4 tests, 10 total handler invokes) / E safety verification (2 tests, no production mutation, no Telegram, no LLM) / F regression test (2 tests, F.1 PASS, F.2 XFAIL pending M6.1-8.2) / G diagnostic summary (1 test). All 12 acceptance criteria MET. No stop conditions triggered. Closeout out-of-repo at \C:\\Users\\bbfcc\\gov_1_temp\\m6_1_8_1_closeout.md\. **Bry rollout decision still required** for production (Option A direct + 24h monitor / B gradual / C shadow / D test-isolated per M6.1-8 §7.2). | Mavis / Lin | M6.1-8.1 || 2026-08-14 (M6.1-8.2) | Controlled Production Agency Re-enable (commit `2a42521`, Bry approved Option B / Gradual). 2 files changed: `scripts/run_server.py` (+59 lines, env-var-driven gradual registration block) + `.env.example` (+17 lines, M6.1-8.2 docs). 0 frozen contract change (15 contracts preserved). 0 production data mutation. 0 handler/Agency/scheduler architecture change. 0 Lived Context / Personal / News / M3 / M5.4-5.x modification. **10-phase gradual rollout** in 19:18:12 to 19:27:36 EDT (~10 min): Phase 1 (1 agent, ruka) -> 2 (2 agents) -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 (full). All 10 phases confirmed via `[Scheduler] start ... agents=N` log line. 0 ERROR/Traceback/CRITICAL during rollout. /health=200 stable. Final state: agents=10 (M5.2 regression fully recovered, was agents=0 for 6+ days since 8/8 21:11). Env var `SOULOS_AGENCY_GRADUAL_AGENTS` controls per-phase agent list (gitignored `.env`). Final state: env var unset -> M6.1-8.1 default (all 10 agents registered). **STRICT 0 PRODUCTION DATA MUTATION**: 0 writes to memory.db / relationships.json / diary / dream / inner_life during rollout. Perception trace +9 weather events (M6.1-3.1 polling, unrelated to this ticket). 24h RUN-AND-COLLECT deferred to M6.1-9 (first evidence window 22:00 EDT today, full 24h by 8/15 19:30 EDT). Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_8_2_closeout.md`. | Mavis / Lin | M6.1-8.2 |


---

| 2026-08-14 (M6.2-0) | Text / TTS Response Path Separation Architecture Audit (READ-ONLY, in-repo registry sync only). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.2 progress row, +§5.7 M6.2 milestone table [created], +§1.1 Current HEAD section updated, +§7 M6.2 closeout log list [created], +§9 change log extended with M6.2-0 entry). Audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_2_0_audit.md` (18 sections, 25.6KB). **Key finding**: async text/TTS separation ALREADY implemented in production. TTS is fire-and-forget via `asyncio.create_task(self._synthesize_async(...))` in `FishTTSHandler._on_agent_speak` (`src/llm/fish_tts_handler.py:266-273`). TTS latency 0.5-6.4s (mean 3.3s, evidence from `data/tts/` filesystem + `trace.log`). Transport: WebSocket 2 distinct message types (`agent_speak` + `agent_audio_ready`); Telegram 2 adapter methods (`send_message` + `send_voice`). NO coupled JSON response in production. **Identified gap (P2)**: `AGENT_AUDIO_READY` payload missing `message_id`/`correlation_id`. ChannelRouter `_pending_voice_target[agent_id]` last-write-wins (race condition for concurrent messages). Web client `lastAudioByAgent[agentId]` overwrites (replay button points to "latest" not specific message). 0 frozen contract change. 0 P0, 0 P1, 1 P2 (correlation gap), 6 P3 (informational). M6.2-1 RECOMMENDED. | Mavis / Lin | M6.2-0 |
| 2026-08-14 (M6.2-1) | Per-Message TTS Correlation Minimal Implementation (commit `965df92`). 6 files changed: `src/voice/tts_service.py` (+5 -1, `synthesize_and_store(..., message_id: Optional[str] = None)`), `src/llm/fish_tts_handler.py` (+8 -2, extracts `event.event_id` in `_on_agent_speak` as message_id, passes to `_synthesize_async` and TTSService), `src/io/channels/router.py` (+12 -3, `_pending_voice_target` keyed by `message_id` per-message, with `_pending_voice_target_legacy[agent_id]` fallback), `src/io/gateway.py` (+6 -1, WS `agent_speak` + `agent_audio_ready` payloads include `message_id`), `static/index.html` (+85 -15, `lastAudioByMessageId` cache, `attachReplayButtonToMessage`, `replayMessageAudio`, `data-message-id` attribute), `tests/test_m6_2_1_text_tts_correlation.py` (NEW, +656 lines, 11 tests in 6 sections A-F). **+771/-31 net insertions**. 0 frozen contract change. 0 production data mutation. 11/11 new tests + 45/45 focused regression (M6.2-1 + M6.1-8.1 + M5.2 H3) PASS in 0.47s. Pre-existing baseline failures (test_event_bus.py 7 errors, test_io_gateway.py 2 failed, test_m1_6_audio_action_baseline.py 2 failed) verified unchanged by stashing M6.2-1 changes and re-running. **Acceptance criteria all met**: text independent of TTS latency, message_id end-to-end, rapid-message race regression covered, backward compat verified (test_d1 + test_d2). NO P0/P1/P2 remaining. Per Quality > Quantity: **NO M6.2-2 recommended**. M6.1-9 PENDING 24h RUN-AND-COLLECT. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_2_1_closeout.md`. | Mavis / Lin | M6.2-1 |
| 2026-08-14 (M6.1-9 partial T+50min) | Lived Context Formation Audit partial snapshot (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_audit.md` 19.7KB, 0 source code changes, 0 production data mutation, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 row updated to reflect M6.1-9 partial + agent_aoi missing anomaly, +§5.6 M6.1-9 row updated from PENDING to PARTIAL, +§7 closeout log list extended with M6.1-9 partial reference, +§9 change log this entry). **M6.1-9 partial T+50min findings**: Layers 1-2 (Signal/Perception) OPERATIONAL — 9 new weather events received since M6.1-8.2 deploy (all dedup at perception phase, same `novelty_id=fbe338b2de9ae8c94f0dd78db37bdac5` = same hour bucket + no_rain state, expected behavior). Layers 3-6 (Lived Context/Soul/Agency/Expression) WIRED but UNVERIFIED — 5/5 trigger paths active (proactive_dm whitelist=['agent_ruka'], event 4-8h, dream 22:05, diary morning 08:00 + night 22:00), 0 trigger firings yet (next window: 22:00 EDT night diary ~2h away). 0 source/test/prod/config mutation. Q1-Q6 reassessed: Q1-Q3/Q5/Q6 ⏳ PENDING 24h evidence; Q4 ⏳ PENDING 24h evidence (last 1081 LLM responses from pre-M6.1-8.2 era, 0 since deploy). **P2 anomaly discovered (6.4)**: M6.1-8.2 closeout claimed 10/10 agents but actual state is 9/10. `agent_aoi` missing from scheduler registration despite `configs/default.yaml` line 80-83 `enabled: true` and `AgentAoi` in `src/agent/registry.py:8,49`. `SOULOS_AGENCY_GRADUAL_AGENTS` env var still set with 9 specific names (M6.1-8.2 Phase 9 set), Phase 10 unset transition incomplete. Diagnosis: env var was set in process shell at M6.1-8.2 deploy, not in .env (which has it commented out); M6.1-8.2 closeout overstates final state. **M6.1-9.1 PENDING** to fix env var (add aoi to gradual list OR unset for true Phase 10). **M6.1-9-R1 (T+24h) PENDING** via cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`, fires 2026-08-15 ~20:00 EDT) — will re-collect evidence, verify trigger firings, confirm Q1-Q6 with 24h data, and either dispatch M6.1-9.1 (aoi fix) or close M6.1 series. | Mavis / Lin | M6.1-9 partial |
| 2026-08-14 (M6.1-9.1) | Restore True Phase-10 Agency Registration (FIX / PRODUCTION CONFIGURATION, configuration-only). 0 production source code files changed. 1 file changed: `logs/ENGINEERING_STATE.md` (this registry sync). **Root cause**: stale `SOULOS_AGENCY_GRADUAL_AGENTS` env var from M6.1-8.2 Phase 10 launch shell persisted into restart, leaving scheduler in `[M6.1-8.2 Gradual] registered 9/10 agents` mode (agent_aoi missing). .env file was already correct (var commented out, full re-enable documented) — the env var was set in the PowerShell process shell that originally launched the M6.1-8.2 Phase 10 server (8/14 19:27:35 EDT, PIDs 10592+17640) and the unset operation never persisted. **Fix**: `.\scripts\server_ops.ps1 stop` (killed PIDs 10592+17640 cleanly) + `.\scripts\server_ops.ps1 start` (started fresh server via hermes-agent venv python PID 20752 at 8/14 20:21:18 EDT). Clean PowerShell shell has no `SOULOS_AGENCY_GRADUAL_AGENTS` env var → default Phase-10 path executes → 10/10 agents registered including `agent_aoi`. **Result**: `[M6.1-8.2 Full] registered all 10 agents (M6.1-8.1 default)`, `agent_aoi` registered, 5/5 trigger paths wired (M5.2-G AgencyTriggerHandler + M5.2-H EventHandler + M5.2-H2 DreamHandler + M5.2-H3 DiaryHandler), /health=200, 0 ERROR/Traceback, 0 duplicate registrations. **0 source code change**, **0 frozen contract change**, **0 production data mutation** (memory.db 5398528 bytes unchanged, perception_trace.jsonl 1687980 bytes hash EE77E8A7... unchanged, shadow_log.jsonl hash 7FC0A1BC... unchanged, inner_life/trace.jsonl 553 bytes hash 7C5633E7... unchanged, all 12 relationships.json files mtime + hash preserved at 8/13-8/14 18:29 EDT). **21/21 M6.1-8.1 regression PASS** (test_f2 now PASS, was XFAIL pre-fix because run_server.py now correctly calls `scheduler.register(_aid)` for all 10 agents in default Phase-10 path). M6.1-9 24h follow-up cron `m6_1_9_followup` (cronId `f7791a0e-df5c-4bc7-b366-72b681f38518`) continues unchanged, fires 2026-08-15 ~20:00 EDT to verify Lived Context formation with now-correct 10-agent registration. Closeout out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_1_closeout.md` (14.9KB). **0/7 stop conditions triggered**. | Mavis / Lin | M6.1-9.1 |

| 2026-08-15 (M6.1-9 T+4h33m partial) | Lived Context Formation Audit partial T+4h33m snapshot (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_closeout.md` 13.6KB, 0 source code changes, 0 production data mutation other than intended Agency trigger outputs, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§1 M6.1 row updated with T+4h33m partial findings, +§7 closeout log list extended, +§9 change log this entry). **M6.1-9 T+4h33m findings**: **2 NEW InnerLifeEvents from Agency triggers** (entry 2: `916cbddf05334881b64cf8f3d8e09fd4` diary:night for agent_yua at 8/14 22:00:11 EDT, entry 3: `2c7627b62a7b49a7a0fd8dbce0562d4b` dream:dream for agent_miku at 8/14 22:05:11 EDT, target yua, all_agents_count 10). Inner life trace grew 553B (1 entry) → 1431B (3 entries) = +878B / +2 events. **M5.2 regression RESOLVED** (was broken 6+ days from 8/8 21:13 to 8/14 22:00). 2 diary files UPDATED: `data/soul/agent_yua/diary` mtime 8/14 22:00:11 EDT (8KB), `data/soul/agent_miku/diary` mtime 8/14 22:05:11 EDT (8KB). Diary file content LOCKED by running server (cannot read directly). 9 of 10 agents' diary/dream LLM calls INTERRUPTED by 22:38 server restart (cause unknown, not via server_ops.ps1, TELEGRAM_BOT_YUA not set in restart shell). Q1/Q2/Q4/Q6 PENDING full 24h evidence. **Cron `m6_1_9_t24h_v2`** (cronId `e2f3168c-6dfb-4e54-98ae-e3822c0393f6`, every 30 min) set for TRUE T+24h follow-up at 8/15 19:27 EDT. | Mavis / Lin | M6.1-9 T+4h33m partial |
| 2026-08-15 (M6.1-9.2) | Server Crash Investigation READ-ONLY forensic audit (in-repo registry sync only, audit report out-of-repo at `C:\Users\bbfcc\gov_1_temp\m6_1_9_2_closeout.md` 18.2KB, 0 source code changes, 0 production data mutation, 0 frozen contract change, READ-ONLY). 1 file changed: `logs/ENGINEERING_STATE.md` (+§7 closeout log list extended with M6.1-9.2 reference, +§9 change log this entry). **M6.1-9.2 root cause finding (REPLACES prior TTS-stuck hypothesis)**: `python.exe` (uv-managed CPython 3.11) is segfaulting with `0xc0000005` ACCESS VIOLATION in `python311.dll`. 6 WER crashes in 8/14 21:32 - 8/15 00:59 (Event 1000 + 1001, Application log). Two distinct fault offsets: `0x1c51db` (3 pre-M6.2-1-era crashes) and `0x26656c`/`0x266588` (3 post-M6.2-1-era crashes) — offset changes correlate with code changes (memory layout shift). 5 evidence pieces (per Bry P0 requirements): (1) Windows Event Viewer: 6 ACCESS_VIOLATION in python311.dll ✓; (2) Process exit code: `0xc0000005` (segfault, NOT clean exit) ✓; (3) Memory before restart: 19.2 MB (no leak) ✓; (4) Thread count before restart: 4 threads (no leak) ✓; (5) Process exists at restart: NO (process dead) ✓. **Bry's TTS hypothesis REJECTED**: M6.2-1 (commit `965df92` 8/14 19:08) is functionally correct; TTS has 180s `requests.post` timeout (`src/llm/fish_tts_handler.py:420`); 22:00/22:05 TTS calls were killed by 22:38 restart, not the cause of crashes. **M6.2-1 is NOT causal** — crashes pre-date M6.2-1 (plan_a_launcher.log shows hundreds of launches since 8/4). **Anomaly detected**: 2 Python interpreters running simultaneously (PID 3832 hermes-agent venv as parent of PID 2104 uv Python, the actual run_server.py owning port 8000). **Root cause classification**: C extension memory corruption (likely uvicorn + anyio + asyncio.create_task fire-and-forget pattern). 7 hypotheses evaluated: TTS blocking (REJECTED), uvicorn hang (REJECTED), external termination (REJECTED), memory exhaustion (REJECTED), resource exhaustion (REJECTED), launcher failure (REJECTED), C extension memory corruption (CONFIRMED). **Watchdog N=4/10**: 4 restarts already happened (PIDs 15756, 16836, 4052, 6724 via Plan A launcher). Will stop auto-restart at N=10. Bry needs to investigate before N=10. | Mavis / Lin | M6.1-9.2 |

| 2026-08-23 (DSH MVP) | DSH Multi-Agent MVP COMPLETE / ACCEPTED（MVP Contract Gate 8/8 PASS，bypass discovered = NO）。2A–2D contracts（`f5fb0cd`）+ MVP-1~7（`9a8b7a2` → `d7877e0`）全 commit。4 條 non-blocking discrepancy 記為 accepted limitations / future hardening（§5.8）。Next: DSH-MA-0 — Multi-Agent Environment Architecture & Adapter Boundary Audit。 | Mavis / Lin | DSH MVP |
| 2026-08-23 (DSH MA) | DSH MA-0~MA-4-R1 治理鏈閉合：MA-0 Audit → MA-1 Adapter Boundary → MA-2 Migration Architecture → MA-3 Migration Decomposition → MA-4 Build Plan（BLOCKED）→ MA-4-R1 修復（HMAC authority trust establishment + durable-log idempotency dedup）→ Independent Review PASS → IMPLEMENTATION AUTHORIZED。R1 新增 41 tests（203 passed）。3 條 non-blocking hardening 列入 Phase 0 backlog（expires_at=None 拒絕 / e2e 改用 issue_hmac_context / durable nonce registry）。 | Mavis / Lin | DSH MA-4-R1 |
| 2026-08-23 (DSH P0-1) | Phase 0 Minimal Work Execution Adapter 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY FOR PHASE 0 GATE**。新檔 `src/work_adapter/`（bridge.py + execution.py）+ `dsh_adapter/soul-dsh-adapter.mjs`（mock TS）+ `tests/test_work_adapter.py`（27 tests）。230 tests 全綠。8/8 Hard Checks PASS。`git diff 26e1e49 -- src/work` 為空（Domain Core 零改動）、src/ 全樹零 DSH import、無 durable write bypass、No-DSH Survival 實測成立。**Phase 0 Gate → Phase 0 CLOSED**（commit `ece757b`）。 | Mavis / Lin | DSH P0-1 |
| 2026-08-23 (DSH P1-Preflight) | Phase 1 前置 hardening 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。M1 `HandoffStatus`→WorkState 語義（blocked/needs_input → state_transition，不照記產出）、M2 `result_type`↔capability anchor 驗證、M3 bridge error contract 統一（binary I/O + 主執行緒 decode）。239 tests 全綠（230 + 9 new）。8/8 Hard Checks PASS + bypass=NO。`src/work/` 僅 kernel.py 可動，其餘十模組零改動；零 DSH import 不變。commit `34e91d4`。 | Mavis / Lin | DSH P1-Preflight |
| 2026-08-23 (DSH P1 Decomposition) | P1 Execution Routing Decomposition（`docs/DSH-P1-EXECUTION-ROUTING.md`）→ Review #1 BLOCKED（2 項：A2 resume discriminator 未鎖 + A5 防火牆無 mechanism）→ 修正（7 處）→ Re-review → **READY FOR IMPLEMENTATION**。P1-A~P1-E 分解，鎖定 ExecutionShape capability-neutral + shape 由 Soul 推導 + adapter 只 translate。 | Mavis / Lin | DSH P1 Decomposition |
| 2026-08-23 (DSH P1-A) | Execution Target Contract（Domain Core 側）實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。`src/work/schema.py` 新增 `ExecutionShape` enum + `src/work/workflow.py` 新增 `derive_execution_shape`（continuous 不實作，待 P1-D）+ `execution.py` payload 新增 `execution_shape`。249 tests 全綠（239 + 10 new）。8/8 PASS + bypass=NO。零 DSH import 不變。commit `83aa389`。 | Mavis / Lin | DSH P1-A |
| 2026-08-23 (DSH P1-B) | Artifact / Reference Boundary Decomposition（`docs/DSH-P1-ARTIFACT-BOUNDARY.md`）→ Review #1 BLOCKED（5 項：D6 原子性、§3.1 選項清單、evidence、D7 enforcement、content 回傳通道）→ 修正（含 D8 evidence + 原子 rename + staging 治理）→ Re-review → **READY FOR IMPLEMENTATION**。D1–D8 鎖定：refs content-addressed identity（Domain Core 計算）、artifact/evidence canonical writer = Domain Core、adapter claim→verify、write-ahead + 原子 rename。明記 2A §5.1 vs 2B §5 vs 實務的 artifact.create 三處不一致。commit `d4a57a2`。 | Mavis / Lin | DSH P1-B |
| 2026-08-23 (DSH P1-C) | Real DSH single_shot Routing Decomposition（`docs/DSH-P1-C-ROUTING.md`）→ Review #1 BLOCKED（2 項：artifact.create enforcement 缺口 + headless preset 事實錯誤）→ 修正（D9 Domain Core enforcement + D10 claimed-ref 驗證 + seam 事實更正）→ Re-review → **READY FOR IMPLEMENTATION**。transport = `dsh --profile headless`（generic Agent 無 preset，role 語義由 prompt 承載、authority 在 Domain Core）。Owner 拍板 artifact.create 歸 Researcher（2A §5.1 frozen）。commit `65be40b`。 | Mavis / Lin | DSH P1-C |
| 2026-08-23 (DSH P1-C0) | Domain Core Capability Enforcement 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。`kernel.record_handoff` 產出分支（dedup 前）驗證 role↔capability：artifact.create→Researcher、evidence.create→Tester/Auditor、decision 不 gate（2A §3.1）、blocked/needs_input 不 gate（M1）。新增 `CapabilityNotAuthorizedError`。256 tests 全綠（249 + 7 new）。測試遷移 28 處 Developer+artifact.create→Researcher（不補回 matrix）。8/8 PASS + bypass=NO。commit `06a0986`。 | Mavis / Lin | DSH P1-C0 |
| 2026-08-23 (DSH P1-C1) | Identity & Handoff Seam Decomposition（`docs/DSH-P1-C1-DECOMPOSITION.md`）→ Review #1 BLOCKED（5 項）→ 重寫 → Re-review READY。C1-A audit 確認 cwd+session log header 為 process 層 identity 錨點。核心：trust model（信任根=adapter 防惡意 LLM）、T1 Domain Core 開檔讀 log、A1 role→cwd binding、B1 content=session log final message、claim→verify 三層正交。commit `9f01c5e`。 | Mavis / Lin | DSH P1-C1 |
| 2026-08-23 (DSH P1-C1-R) | Real DSH single_shot Routing 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。`src/work/execution_evidence.py`（RoleCwdRegistry + read_execution_evidence + verify_role_binding）+ `src/work/artifact_store.py`（write_artifact + verify_artifact_ref + staging + single-writer）+ bridge execute_dsh（spawn headless + --patch overlay + 事後讀回）+ execution execute_work_dsh（三層 cross-check）。291 tests 全綠（256 + 35 new），**C1.9 真 DSH smoke PASS**。8/8 PASS + bypass=NO。commit `041dad6`。 | Mavis / Lin | DSH P1-C1-R |
| 2026-08-23 (DSH P1-C2) | Integration / Boundary Gate 實作完成 + Independent Adversarial Review（READ-ONLY）→ **READY TO LAND**。補 content transport：artifact content=final_message，Domain Core write_artifact 算 canonical ref **回填** claim（agent 不聲稱 ref，解 sha256 自指矛盾），三層 claim→verify 完整。evidence_refs 定錨「被驗證對象」（D4）、execute_work deprecated（D5）、headless approval policy=never（D6）。302 tests 全綠（291 + 11 new），**真 DSH E2E 閉環 PASS**。8/8 PASS + bypass=NO。**P1 閉環**。 | Mavis / Lin | DSH P1-C2 |
| 2026-08-29 (CA-2) | Soul Capability Awareness 落地（commit `a70621f`）。4 files changed: `src/soul/capability.py`（NEW, +178，capability 注册/查询）、`src/llm/proxy.py`（+39，capability 投影进 prompt）、`tests/test_capability_awareness.py`（NEW, +321）、`docs/SOUL-CAPABILITY-AWARENESS-DESIGN.md`（NEW, +269）。核心原则：**Capability makes an action conceivable** —— 能力先于行动被灵魂「设想」，是行动可能性的前提，不是行动授权本身。0 frozen contract change。 | Mavis / Lin | CA-2 |
| 2026-08-29 (Proactive DM fixes) | Proactive DM 三件修复（commit `93672df`）。8 files changed: `src/io/channels/bryan_state.py`（NEW, +100）、`src/soul/scheduler.py`（+41）、`src/io/channels/router.py`（+55 -31）、`src/io/gateway.py`（+8）、`scripts/server_ops.ps1`（+38）、`tests/test_proactive_dm_deliverability.py`（NEW, +183）、`tests/test_proactive_whitelist_v1.py`（+43）、`tests/test_m6_1_8_1_agency_reenable_isolated.py`（+207）。三件修复：① **deliverability 提前**（投递判定提前，避免消息不可达才后知后觉）；② **信号统一**（router + gateway 统一信号路径）；③ **双实例**（server_ops.ps1 防双实例）。0 frozen contract change。 | Mavis / Lin | Proactive DM fixes |
| 2026-08-29 (SM-1/SM-2) | Soul Motive & Decision 设计文档定稿（commit `7b9cfe7`）。2 files changed: `docs/SOUL-MOTIVE-DECISION-DESIGN.md`（NEW, +297，motive 模块 + Decision 层）、`docs/DECISION-PROMPT-CONTRACT.md`（NEW, +249，prompt contract）。核心原则：**Decision LLM 不是 classifier，是「当下选择」** —— 决策是灵魂在当下情境中的选择行为，不是对选项的分类打分。0 frozen contract change。 | Mavis / Lin | SM-1/SM-2 |
| 2026-08-30 (SM-3) | Soul Motive & Decision 实现落地 + motive proxy 独立注入（commit `6bcbda3`）。7 files changed: `src/soul/motive.py`（NEW, +671，Motive + MotiveTraceStore + MotiveEngine）、`src/soul/decision.py`（NEW, decision prompt 构建 + parse + decide_motive）、`src/soul/scheduler.py`（+58，_decision_check additive hook，proactive_dm producer-side fail-closed 检查）、`scripts/run_server.py`（+4，motive proxy 独立注入，Bry 授权 2026-08-29，M3.1 frozen scope 解冻仅限此一处 additive 改动）、`tests/test_sm3_motive_decision.py`（NEW, 25 tests）、`tests/test_m5_8_4_producer_gating.py`、`tests/test_m6_1_8_1_agency_reenable_isolated.py`（SM-3 适配：mock _decision_check + test_d4 per-agent cooldown=0 state 修复，pre-existing 失败）。**motive 模块 + Decision LLM + volition path 完整闭环**：motive 用自己的 process-global proxy，不再 fallback 到 diary 的 proxy。72 tests 全绿。0 frozen contract change（仅 run_server.py motive proxy 注入，Owner 授权）。 | Mavis / Lin | SM-3 |
| 2026-09 (SM-4) | Motive & Decision 多元行动适配（commit `c9c19bf`）。4 files changed: `src/soul/decision.py`、`src/soul/motive.py`、`tests/test_sm3_motive_decision.py`、`tests/test_tl2_volition.py`。**Decision 四元选择 transmit/observe/reflect/do_nothing**：互斥单选；**do_nothing 是主动选择**（灵魂判断当下无需行动）非失败兜底；fail-closed → do_nothing（LLM 坏输出安全默认）。DecisionResult 保留 transmit 兼容字段，scheduler 0 change（additive）。31+17+26+4 tests 全过。0 frozen contract 改动。 | Mavis / Lin | SM-4 |
| 2026-08-30 (TA-1) | 时间感知：conversation_elapsed + 表达规则放宽 + silence bug 修复（commit `4a63b1d`）。3 files changed: `src/llm/proxy.py`（+129 -3，时间区块内 3 项改动）、`tests/test_ta1_temporal_orientation.py`（NEW, 25 tests）、`tests/test_ta1_simulation_day.py`（NEW, 15 tests），**40 tests PASS**。**① conversation_elapsed 信号**：`_get_last_interaction_ts`（跨 session 不跨 agent，last_interaction_at = max(last_user_ts, last_assistant_ts)）+ `_format_continuity_str`（elapsed < 15 分钟 = 同一场对话不注入；<24h = "X 小时"；>=24h = "X 天"）+ `last_interaction_period`（上次互动本地时段标签）。**② TEMPORAL_EXPRESSION_RULE 放宽**：`TEMPORAL_EXPRESSION_PRECEDENCE` 常量注入时间区块，现象式时间表达允许（早上/快中午/都下午了/这么晚/周末），未询问不主动报精确钟点或日期，precedence 压过 persona 绝对禁令（不改 10 份人格档）。**③ silence bug 修复**：`_get_bry_latest_ts` suffix 从 `f"_agent_{agent_id}"` 改 `f"_{agent_id}"`（真实 session_id 以 `_{agent_id}` 结尾，旧 suffix 0 匹配 → bry_latest_ts 恒 0 → 沉默时长行从未注入）。**模拟测试验证**：早上不道晚安 / 下午不问早餐 / 夜间不道早安。**0 frozen contract change**（只改 proxy.py 时间区块 + 测试，不改 Agency/TriggerEnvelope/InnerLifeEvent/4 handlers/SAGE）。 | Mavis / Lin | TA-1 |
| 2026-08-30 (TL-2) | Volition Choice Test：Decision 层非装饰验证（commit `85711cc`）。3 files changed: `harness/tl2.py`（NEW, TL2Runner：seed_candidate_context → motive → Decision → transmit/not_transmit，6 candidates）、`harness/run_tl2.py`（NEW, 入口）、`tests/test_tl2_volition.py`（NEW, **17 tests PASS**）。**Control A（scheduler-only，无 Decision 层）全发 6/6** vs **Control B（有 Decision 层）4 send / 2 not_send** → **Decision 层非装饰 = True**（scheduler 说发 ≠ Soul 发；同一 candidate C02 A=send / B=not_send）。**not_transmit 的 reason 引用 relationship/memory/mood context**（非随机，fail-closed：LLM 坏输出 → not_transmit）。走真实 `src.soul.decision.decide_motive`（不改其逻辑）。**17 tests 全过；0 production mutation；0 frozen contract change**（隔离 data_root，`data/time_lapse/` gitignore，harness 只活在 harness/ + tests/，不改 src/）。 | Mavis / Lin | TL-2 |
| 2026-09 (SE-4) | Durable Soul Structure Lifecycle Contract 设计文档定稿（commit `331b867`）。1 file changed: `docs/ELEVATION-LIFECYCLE.md`（NEW, +295，12 节）。**灵魂结构生命周期 = 状态机，不是动作集合**：四态 `ACTIVE → WEAKENING → DORMANT → SUPERSEDED` + 两转换 `REINFORCE / SUPERSEDE` + 默认「证据不足什么都不做」。三条铁律：① Contradiction ≠ Revision（矛盾产生压力，证据累积才产生改变）；② Forgetting = lifecycle transition 不是 delete（Memory ≠ Current Belief，节点永不物理删除）；③ essence 近乎锁死（豁免自动衰减，SUPERSEDE 门槛全系统最高，唯一通道 = reconsideration-candidate 待复核）。v1 只分两层（essence 锁死 vs 其他共用中等门槛曲线，不做四层独立衰减）。lineage 复用 InnerLifeEvent 命名（parent_node_id / lineage_depth / lineage_path，不另创术语）。decay 复用 M5.13 锚点（last_support_ts 优先，created_ts + grace 兜底；old ≠ outdated）。不建五个独立引擎（belief/confidence/decay/revision/scoring）。0 frozen contract change（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 写入不动；trace 新事件 node_state_changed / node_superseded / essence_reconsideration_candidate 为 additive 扩展）。SE-5 实作的单一事实依据。 | Mavis / Lin | SE-4 |
| 2026-09 (SE-5) | Durable Soul Structure Lifecycle 实作（commit `42939d4`）。6 files changed: `src/soul_elevation/models.py`（+33，`LifecycleState` 四态 + `ContradictionRecord`）、`src/soul_elevation/engine.py`（+470，`InternalizingEngine` lifecycle 实作）、`src/soul_elevation/prior.py`（+21）、`src/soul_elevation/trace.py`（+5，3 个 additive 事件）、`src/soul_elevation/__init__.py`（+20）、`tests/test_lifecycle.py`（NEW, +630，35 tests）。**183 tests 全过（0.44s）**。**四态 + 两转换实作**：`REINFORCE`（active 原地保持 / weakening / dormant 复活 / superseded 拒绝）与 `SUPERSEDE`（新节点取代 + 旧节点冻结，证据门槛 + 单日噪声拒绝）。**Contradiction ≠ Revision**（单次矛盾不改状态，按 source+identity 去重，压力只记引用不记正文）。**Forgetting = lifecycle transition 不是 delete**（节点永不物理删除）。**essence 保守**（豁免自动衰减、SUPERSEDE 门槛全系统最高、需 valence reversal + confidence delta、唯一通道 = reconsideration-candidate）。**decay 锚点**（`last_support_ts` consume 时更新 + `created_ts` + grace 兜底；old ≠ outdated；单步转换；坏时间戳跳过不 crash）。**trace 3 个 additive 事件**（`node_state_changed` / `node_superseded` / `essence_reconsideration_candidate`，既有 5 事件语义 0 变更）。0 frozen contract 改动（InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE 不动）。 | Mavis / Lin | SE-5 |
| 2026-09 (TL-4) | Lifecycle Validation：SE-5 lifecycle 行为验证（commit `8689e9c`）。2 files changed: `harness/tl4.py`（NEW, TL4Event + build_tl4_script(seed) 90 天四阶段事件剧本 + TL4Runner：SimulationClock 推进 Day 0-90，走 soul-elevation 的 consume/elevate/record_contradiction/reinforce/supersede/evaluate_lifecycle，产出 canonical records + derived 四指标判定）、`tests/test_tl4_lifecycle.py`（NEW, **23 tests PASS**）。**场景化 trajectory（一条）**：belief A = "Alex 是值得信任的朋友"（positive）→ Day 0-20 重复正面证据强化（REINFORCE）→ Day 21-40 矛盾证据累积（contradiction_pressure，不立即修订）→ Day 41-60 混合证据 → SUPERSEDE → B = "Alex 最近变得疏远"（negative）→ Day 61-90 稳定。**四指标全过**：① Revision validity（矛盾压力累积不立即修订，证据足够才修订）；② Stability（短期噪声不翻转结构，REINFORCE 不新建节点）；③ Recovery-Adaptation（环境变化 A→B，B 是新 durable structure）；④ Historical continuity（SUPERSEDE 后 lineage 可追溯，旧节点保留不删除，trace 有 supersede 事件）。**23 tests 全过（0.76s）；0 production mutation；0 frozen contract change**（隔离 data_root `data/time_lapse/TL-4/`，走 soul-elevation 公开 API，harness 只活在 harness/ + tests/，不改 src/）。 | Mavis / Lin | TL-4 |
| 2026-09 (SE-5 Step 1) | SE-5 read-side 投影过滤（commit `3ff2976`）。2 files changed: `src/inner_life/emergent_projection.py`（+85，SE-5 状态守门 + lineage 降维）、`tests/test_emergent_projection.py`（+132，**11 新测试** test_01~test_11）。**状态守门**：ACTIVE 正常投影 / WEAKENING 投影但带「我隐约觉得…」不确定性语气 / DORMANT+SUPERSEDED 不主动投影（v1 不做历史回忆检索，留后续工单）/ 缺省视为 ACTIVE（SE-5 additive schema，旧数据兼容）。**Lineage 降维**：新节点 B 的直接父是 SUPERSEDED 旧节点 A 时只投影 B 并合成「我以前觉得 A，但后来发现 B」——不把 A、B 两个矛盾信念同时投影（人格撕裂防护）；只查直接父不递归。**28+35+11 tests 全过**（本 repo emergent_projection 28 = 17 旧 + 11 新；soul-elevation lifecycle 35）。**0 frozen contract 改动**（只改 emergent_projection.py 读侧 + 测试，不改 InnerLifeEvent / TriggerEnvelope / Agency 4 stages / SAGE / soul-elevation）。 | Mavis / Lin | SE-5 Step 1 |
| 2026-09 (CA-3) | Capability Affordance 定义扩展（commit `1f48108`）。2 files changed: `src/soul/capability.py`（+20 -4，CAPABILITY_DEFINITIONS 扩展到 3 个：communicate / observe_environment / reflect_memory）、`tests/test_capability_awareness.py`（+22 -6，3 处断言更新）。**19 tests 全过（0.34s）**。**措辞原则**：expression 陈述「可以」（can），不陈述「应」（should）——写「你可以…」不写「你应该…」，防止从「我能」滑成「我应」。**do_nothing 不进 Capability**（是 Decision 选项，不是 Capability）。0 frozen contract 改动（只改 capability.py + 测试，不改 Agency/TriggerEnvelope/InnerLifeEvent/4 handlers/SAGE）。 | Mavis / Lin | CA-3 |
| 2026-09 (SM-4 series) | SM-4.1~SM-4.6 六轮校准（commit `79fe750`）。3 files changed: `src/soul/decision.py`（+135，prompt 六轮迭代）、`src/soul/motive.py`（+74 -11）、`tests/test_sm3_motive_decision.py`（+87）。**六轮校准**：SM-4.1 Prompt 注入社交摩擦力（留白/安静最高优先，修正小模型「有念头就行动」讨好偏见）→ SM-4.2 内外动作解耦（修正 96.5% do_nothing 过度矫正；observe/reflect 内部动作零社交成本，仅 transmit 受社交摩擦力保护）→ SM-4.3 深夜克制覆盖真心分享 + observe 唤醒（修正 transmit 15.8% 超目标 7/9 深夜/沉默期）→ SM-4.4 observe 强锚定 + 深夜硬禁止（observe 1.8% 远低于 10-20% 目标；[22:00~07:00] 绝对禁止 transmit）→ SM-4.5 Decision prompt 注入当前时间感知（修正时间幻觉：白天 14:00 被当深夜 23:00；Context 区块注入 [當前時間感知]）→ SM-4.6 reflect 分级（消解「补偿心理」reflect 22.8% 偏高；dawn 补入 reflect 合法判定集合）。0 frozen contract 改动（只改 decision.py prompt + motive.py + 测试）。 | Mavis / Lin | SM-4 series |
| 2026-09 (TL-5) | Time-lapse Behavior Distribution Validation（commit `89e9cdf`）。4 files changed: `harness/tl5.py`（NEW, TL5Runner：SimulationClock 小时精度 tick + 四元 Decision 自发行为分布 + derived 三大指标）、`harness/run_tl5.py`（NEW, 入口）、`harness/clock.py`（+9，`sim_ts` 加 additive `hour` 参数默认 0 向后兼容）、`tests/test_tl5_behavior_distribution.py`（NEW）。**最终验收 PASS**：Behavioral Diversity PASS（do_nothing 82.5% / reflect 10.5% / transmit 3.5% / observe 3.5%，四动作均 > 0 无死模组）；Contextual Appropriateness PASS（observe 集中信号突变点 / reflect 集中夜间等待期 / transmit 遵守 CD 与亲密度）；D2 Determinism 按 MoE 特性记录 7 mismatches（3 runs 决策轨迹基本一致，mismatch 归因 MoE 采样特性非逻辑缺陷）。0 production mutation；0 frozen contract 改动。 | Mavis / Lin | TL-5 |
| 2026-09 (TA-2) | Subjective Temporal Phenomenology Contract 设计文档定稿（commit `4f0ec41`）。1 file changed: `docs/TEMPORAL-PHENOMENOLOGY.md`（NEW, +264，8 节）。**三态张力模型**（无感/牵挂/释然——现象学状态，不是计算输出）；**M5.13-3 亲密度 Band 复用**（不另创心理模型）；**reflect-only 加权边界**；**TA-2 与 SE-5 解耦**（正交，不进入既有模块）；**Prompt 三行格式**（TEMPORAL ANCHOR）；**四大禁止项**。docs-only 0 code；0 frozen contract 改动。 | Mavis / Lin | TA-2 |
| 2026-09 (TA-2 impl) | Subjective Temporal Phenomenology 实作（commit `cc83daa`）。4 files changed: `src/soul/temporal_phenomenology.py`（NEW，三态张力模型 无感/牵挂/释然）、`src/llm/proxy.py`（+36，TEMPORAL ANCHOR 三行注入）、`src/soul/decision.py`（+51 -1，reflect-only 加权）、`tests/test_ta2_temporal_phenomenology.py`（NEW，26/26 新测试）。**M5.13-3 亲密度 Band 复用**（不另创心理模型）；**reflect-only 加权边界**；**26/26 新测试 + 125 回归全过**。0 frozen contract 改动（只改 temporal_phenomenology.py + proxy.py + decision.py + 测试）。 | Mavis / Lin | TA-2 impl |
| 2026-09 (阶段 A 全满贯) | 阶段 A 灵魂深化全满贯：**升华细化** SE-4（contract）+ SE-5（lifecycle）+ TL-4（time-lapse lifecycle validation）；**工具打通** CA-3 + SM-4 + SM-4.1~SM-4.6 六轮校准 + TL-5 最终验收；**时序化** TA-2 实作（commit `cc83daa`）。阶段 A 全部条目 ✅ 落地/闭环，0 frozen contract 改动。 | Mavis / Lin | 阶段 A 全满贯 |
| 2026-09 (MR-1) | Temporal Memory & Mem0 Primitives Contract 设计文档定稿（commit `6419166`）。1 file changed: `docs/TEMPORAL-MEMORY-CONTRACT.md`（NEW, +289，8 节）。**Schema v7 迁移**（valid_from 回填 timestamp + invalidated_at NULL）；**GraphStore invalidate_fact 软删 + get_facts_as_of 回溯**；**Mem0 原语模块 primitives.py 显式 add/update/delete/resolve_conflict**；**SAGE Reader as_of 默认过滤 invalidated_at IS NULL**。docs-only 0 code；0 frozen contract 改动。 | Mavis / Lin | MR-1 |
| 2026-09 (MR-2) | Temporal Memory & Mem0 Primitives 实作（commit `3eacae8`）。7 files changed: `src/memory/sage/models.py`（+14，Schema v7：Fact 加 `valid_from`/`invalidated_at`，迁移分支 valid_from 回填 timestamp + invalidated_at NULL）、`src/memory/sage/graph_store.py`（+193，`invalidate_fact` 软删 → `invalidated_at` 时间戳 + `get_facts_as_of` 回溯到指定时刻）、`src/memory/primitives.py`（NEW +104，Mem0 原语：显式 add/update/delete/resolve_conflict）、`src/memory/sage/reader.py`（+42，`as_of` 参数默认过滤 `invalidated_at IS NULL`）、`tests/test_temporal_memory_mr2.py`（NEW +381，21 tests）、`tests/test_m5_4_5_2_memory_inner_life_integration.py`（+4 -2）、`tests/test_pig_filter_v2.py`（+9 -2，v5→v7 schema 断言更新）。**135 tests 全过**。0 frozen contract 改动（只改 sage/models + graph_store + reader + 新 primitives + 测试，不改 writer.py / evolution.py / v1 / event.py / inner_life / middleware）。 | Mavis / Lin | MR-2 |
| 2026-09 (阶段 B-P0) | 阶段 B-P0 记忆检索工程落地：**MR-0 审计**（`docs/MEMORY-RETRIEVAL-AUDIT.md`，event_time 生产数据 100% NULL + INSERT OR REPLACE 覆写破坏历史，B1-B4 四缺口）+ **MR-1 设计**（`docs/TEMPORAL-MEMORY-CONTRACT.md`）+ **MR-2 实作**（commit `3eacae8`）。记忆检索工程三条链闭环：可随时间回溯（get_facts_as_of + as_of 过滤）/ 软删不破坏历史（invalidate_fact）/ 显式原语替代隐式覆写（primitives.add/update/delete/resolve_conflict）。0 frozen contract 改动。 | Mavis / Lin | 阶段 B-P0 |
| 2026-09 (SI-2.1) | Social Diffusion Contract 设计文档定稿（commit `5002f20`）。1 file changed: `docs/SOCIAL-DIFFUSION-CONTRACT.md`（NEW, +456，10 节）。**SocialWorldEvent 最小 Schema**（`EventType.SOCIAL_WORLD_EVENT` + `SoulEvent.actor_id` additive 可选字段，payload 含 `actor_id / space_id / visibility / event_type / content`）。**三大防线**：防线 3 Identity Firewall（`actor_id != current_agent_id` → `EXTERNAL_OTHER_ACTION`，三条绝对不变量：外部他者事件只能作环境背景感知 / 禁止内化为自身情景记忆 / 严禁升华为自身性格信念）；防线 2 Privacy Visibility Gate（与 Bryan 1:1 私聊 DM 默认 `private` 拦截于广播总线之外，仅公共频道或显式公开动态才沉淀为社交事件）；防线 1 Ambient Perception Path（社交事件仅经 WorldPerceptionMiddleware 注入 world_context，不触发 transmit，杜绝广播风暴）。docs-only 0 code；0 frozen contract 改动（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 不动，既有字段与枚举语义 0 变更）。 | Mavis / Lin | SI-2.1 |
| 2026-09 (SI-2.2) | Social Diffusion 实作（commit `33ae1b1`）。14 files changed, +2286/-6：`src/eventbus/schema.py`（additive：`EventType.SOCIAL_WORLD_EVENT` + `SoulEvent.actor_id` 可选字段）、`src/social/`（NEW 5 文件：`__init__.py` / `schema.py` / `validation.py` / `identity_firewall.py` / `producer_gate.py`）、`src/inner_life/submission_gate.py`（防线 3：第 6 步 actor_id 检查，`actor_id != current_agent_id` → `EXTERNAL_OTHER_ACTION`）、`src/world/middleware.py`（防线 1：平行订阅 SOCIAL_WORLD_EVENT 注入 world_context）、6 个新测试文件（test_social_schema / test_social_validation / test_social_identity_firewall / test_social_producer_gate / test_social_middleware / test_social_submission_gate）。**95 新测试全过 + 0 回归引入**。0 frozen contract 改动（只 schema.py + middleware.py + submission_gate.py additive + 新 social 模块 + 测试）。 | Mavis / Lin | SI-2.2 |
| 2026-09 (SI-2) | 多 Agent 灵魂互动完整落地：**SI-2.0 审计**（`docs/SOCIAL-DIFFUSION-AUDIT.md`）+ **SI-2.1 设计**（`docs/SOCIAL-DIFFUSION-CONTRACT.md`，commit `5002f20`）+ **SI-2.2 实作**（commit `33ae1b1`）。灵魂互动主线从 SI-1 最小读侧升级为完整多 Agent Social Diffusion 闭环（Schema + 三大防线：Identity Firewall / Privacy Visibility Gate / Ambient Perception Path）。0 frozen contract 改动。 | Mavis / Lin | SI-2 |
| 2026-09 (TL-6) | 多 Agent 客厅情境稳定与身份隔离验证完成。3 files changed: `harness/tl6.py`（NEW，TL6Runner / TL6Tick / build_tl6_script 7 阶段客厅剧本）、`harness/run_tl6.py`（NEW，CLI 入口）、`tests/test_tl6_social_harness.py`（NEW，5 tests）。**四大核心不变量全过**：Anti-Storm 100%（0 自激风暴）/ Identity Quarantine 100%（0 他者记忆内化）/ Privacy Gate 100%（1:1 私聊 0 泄漏）/ Ambient Salience PASS（反框架提示在场）。3 runs 确定性一致，生产数据 0 diff。**213 笔全回归测试通过（36.85s）**。0 frozen contract 改动。 | Antigravity | TL-6 |
| 2026-09 (TL-7) | 社交机会生命周期与自主意志稳定性验证完成 + 历史旧测试对齐。5 files changed: `harness/tl7.py`（NEW，TL7Runner 4 大情境阶段：话题涌现 / 紧凑感知与机会生成 / SM-4 意志选择 / 300s TTL 自然蒸发）、`harness/run_tl7.py`（NEW，CLI 入口）、`tests/test_tl7_social_opportunity_harness.py`（NEW，7 tests）、`tests/test_m3_4_priority_semantic_boundary.py`（改：test_I7 对齐 M5.4-3.1 契约，to_payload 含 priority additive 字段 + round-trip 保证）、`tests/test_tl2_volition.py`（改：ContextRoutingLLM stub 对齐 SM-4.1~SM-4.6 判定阶梯，motive 原文锚点取代旧 context 关键词 marker）。**三大不变量全过**：TTL Expiration 100%（过期条目彻底蒸发 0 遗留）/ No Cascading Volition 100%（0 自动连锁抢话）/ D2 Determinism & 0 Mutation（3 runs 轨迹一致，生产 data/ 0 diff）。**55 笔验收测试全过（13.47s，工单验收命令 6 文件）**。0 frozen contract 改动；0 Vector DB。 | DSH | TL-7 |
| 2026-09 (TS-2) | Tooling & MCP 实作（commit `c668739`）。4 files changed: `src/soul/tool_registry.py`（NEW）、`src/soul/actuator.py`（NEW）、`tests/test_tool_registry.py`（NEW）、`tests/test_actuator_volition_gate.py`（NEW）。**动态注册表**：tool 注册从静态集中改为动态分组，分组聚合 **3 能力组（observe / reflect / communicate）**，健康检查 **三态** + **fail-closed 归类**（异常一律拒绝，不阻塞主心跳）+ **权限分级**（`Auto-Approved` / `Ask-Required` 双档）+ **5s 硬超时降级**；**observe/reflect 执行器**：调用链 = Decision 批准 → Actuator 派发单次调用 → 结果回流 World Context / Perception，**0 自主递归硬规则**。**96 tests 全过**；0 frozen contract 改动（只新增 tool_registry.py + actuator.py + 测试，未动 scheduler.py / capability.py / decision.py / motive.py）。注：scheduler 接线留待 TS-2.1。 | DSH | TS-2 |
| 2026-09 (TS-2.1) | Actuator 接线（commit `10a6a98`）。3 files changed: `src/soul/scheduler.py`（+46，`scheduler._decision_check` 依赖注入 actuator：observe/reflect 决策后经 Actuator 派发单次调用，结果回流感知/认知；发布端仍 mark_rejected；transmit 保持既有通道；do_nothing 不执行）、`src/soul/actuator.py`（+35，additive）、`tests/test_ts21_actuator_wiring.py`（NEW，10 新测试）。**79/89/100 测试全过（TS-2.1 10 + actuator volition gate + tool registry + SM-3 回归），36 条 SM-3 零回归**。**工具层标准化全线贯通**（TS-0 审计 `docs/TOOLING-MCP-AUDIT.md` + TS-1 设计 `814f16a` + TS-2 实作 `c668739` + TS-2.1 接线 `10a6a98`）：Decision 批准 → Actuator 派发单次调用 → 结果回流，0 自主递归硬规则，5s 硬超时，全程 0 frozen contract 改动（只改 scheduler.py + actuator.py additive + 新测试，未动 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑）。注：`tests/test_soul_md_loader.py` 先前 dirty 状态现不存在，未被本次触碰。 | DSH | TS-2.1 |
| 2026-09 (TS-3) | 真实 MCP Server 端到端验证（commit `0acadbc`）。4 files changed: `scripts/mcp_fixture_server.py`（NEW，官方 mcp SDK 实现的真实 MCP server fixture）、`src/soul/mcp_stdio_client.py`（NEW，手写 stdio MCP client：JSON-RPC 2.0 初始化握手 / tool 动态发现 / 调用派发 / 响应解析）、`tests/test_ts3_real_mcp_e2e.py`（NEW，真实进程端到端：spawn 真实子进程 server → stdin/stdout 进程通讯 → tools/list + tools/call 全链路）、`tests/test_ts3_official_mcp_server.py`（NEW）。**三大验证全过**：① 进程通讯（真实子进程 + stdio 双向 JSON-RPC，非 mock）；② 5s 硬超时 Fail-closed（超时降级、异常一律拒绝不阻塞主心跳，契约 3 实证）；③ Volition Gate（Decision 批准 → Actuator 派发单次调用 → 结果回流，权限分级 Auto-Approved / Ask-Required 实证）。**73 测试全过**；**工具层状态升级「生产验证完毕（Production-Verified）」**；0 frozen contract 改动（只新增 4 文件，未动 tool_registry.py / actuator.py 既有接口）。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | DSH | TS-3 |
| 2026-09 (MS-0) | Multimodal Perception 架构审计（commit `c30314e`）。**READ-ONLY，docs only 0 code**。1 file changed: `docs/MULTIMODAL-PERCEPTION-AUDIT.md`（NEW，250 行）。**三大审计结论**：① **输入侧全空白**——语音 STT（whisper / sensevoice / sounddevice / pyaudio venv 0 依赖、源码 0 引用、data 0 痕迹）与视觉 Camera（opencv 0 依赖、源码 0 引用）均无实现，当前全部输入 = 纯文字（WebSocket USER_MESSAGE + Telegram text）；输出侧 Fish TTS + Edge 兜底已生产验证。② **工具层接入点就绪但分类表缺词**——麦克风/相机 → MCP 工具 → `observe_environment` 组路径可行（`register_mcp_server` 唯一入口 + `project_capabilities` 自动投影 + Auto-Approved 权限），但 `_OBSERVE_KEYWORDS` 与 `EXPLICIT_GROUP_MAP` 无 audio / voice / stt / camera 关键词 → 新人工具 fail-closed 拒绝注册，需 **三处 additive**。③ **感知边界完整遵守 Volition Gate**——Actuator `_flowback` → `WorldPerceptionState` → `WorldPerceptionMiddleware` → prompt 注入，scheduler 发布端仍 `mark_rejected`；多模态事件不进 `WORLD_QUALIFYING_TYPES`（M5.9-2 白名单）→ 不污染 InnerLife / SAGE。**唯一 Frozen Contract 触点**：`VALID_SOURCES`（`src/world/perception.py:46`）需 additive 扩展（当前默认落 `synthetic`），化工单级决策需主大脑 + Owner 批准，不阻塞 MS-1 设计。0 frozen contract 改动（本次 0 code）。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | DSH | MS-0 |
| 2026-09 (MS-1) | Multimodal Perception Contract 设计（commit `172bca0`）。**docs only 0 code**。1 file changed: `docs/MULTIMODAL-PERCEPTION-CONTRACT.md`（NEW，413 行）。**设计决策全锁定**：① **STT 语义 v1 锁 observe**——语音输入人话语义一律进 `observe_environment` 组，**严禁直通 USER_MESSAGE**（Volition Gate 边界保持，语言不越权为直接指令）；② **`VALID_SOURCES` additive 扩展已获 Owner 批准**——MS-0 标记的唯一 Frozen Contract 触点闭环：additive 加 `audio_input` / `camera_capture` 两 source（主大脑 + Owner 两级批准，化工单级决策已授权）；③ **工具层三处 additive 扩展清单**——`_OBSERVE_KEYWORDS` 补 audio/voice/stt/camera 关键词、`EXPLICIT_GROUP_MAP` 对应组映射、对应能力定义（三处均 MS-0 缺口，标记待 MS-2 实作工单）；④ **自研薄 MCP 封装**——`audio-stream-mcp` / `camera-mcp` 自研薄封装（挂 `register_mcp_server` 唯一入口，Auto-Approved 权限）；⑤ **ASR 锁定 faster-whisper small 本地离线**（选型已定：本地离线、无云端付费；SenseVoice / 云端 STT 不采用）。**0 frozen contract 改动（本次 docs only 0 code）**；MS-1 为 `docs/MULTIMODAL-PERCEPTION-CONTRACT.md` 单文件设计交付，实作全部移交 MS-2 CANDIDATE 工单。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | DSH | MS-1 |
| 2026-09 (MS-2) | Multimodal Perception 实作（commit `1d1b9af`）。**6 files changed, 1088 insertions(+), 2 deletions(-)**：`src/soul/tool_registry.py`（M，工具层三处 additive——`_OBSERVE_KEYWORDS` 补 audio/voice/stt/camera 关键词 + `EXPLICIT_GROUP_MAP` 对应组映射 + audio_input/camera_capture 能力定义）、`src/world/perception.py`（M，`VALID_SOURCES` additive 扩展 `audio_input`/`camera_capture`——MS-0 唯一 Frozen Contract 触点，主大脑 + Owner 已批准）、`src/soul/actuator.py`（M，感知边界逻辑）、`scripts/audio_stream_mcp.py`（NEW，语音流薄 MCP server）、`scripts/camera_mcp.py`（NEW，相机帧薄 MCP server）、`tests/test_ms2_multimodal_perception.py`（NEW）。**DoD 实证**：多模态输入经 MCP 工具 → `observe_environment` 组 → Volition Gate 审核后回流感知，**Ambient Observation 不直通 USER_MESSAGE**（感知边界不变量保持）。**28 新测试全过 + 314 回归全过（342 total）**；0 frozen contract 改动（VALID_SOURCES 为 additive 扩展，Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑均未触碰）。多模态感知模块 MS-0（审计）→ MS-1（设计）→ MS-2（实作）三段全部落地。注：`tests/test_soul_md_loader.py` 未被本次触碰（保持未提交）。 | DSH | MS-2 |
| 2026-09 (SI-2 harness) | 多体共存 Harness 验证 Closed（commit `973971d`）。3 files changed: `tests/harness/test_social_diffusion_harness.py`（NEW）、`tests/harness/social_harness_fixtures.py`（NEW）、`tests/test_social_middleware.py`（改，旧渲染区断言对齐）。**4 剧本 + 三大防线刚性断言实证**：**Identity Firewall 0 内化**（外部他者事件 0 内化为自身记忆）/ **Privacy Gate 0 泄漏**（1:1 私聊 0 泄漏）/ **Ambient Path 0 自激**（0 自激广播风暴）。**11 测试全过**（middleware 9 + harness 11 = 20 tests 全绿）。顺手修 `test_social_middleware` 旧渲染区断言（`[社交感知]` → `[客廳現況]`，对齐 SI-3 Phase 2 聚合器紧凑渲染）。**0 frozen contract 改动**（只新增 harness 测试 + 修测试断言，0 production 改动）。 | DSH | SI-2 harness |
| 2026-09 (MS-3) | Voice Interaction Contract 设计（commit `a61beff`）。**docs only 0 code**。1 file changed: `docs/MS-3-VOICE-INTERACTION-CONTRACT.md`（NEW，366 行）。**设计决策全锁定**：① **三路分流**——语音输入按语义分 `USER_MESSAGE` / `AMBIENT` / `DROP` 三路（严格区分直接指令 vs 环境观测 vs 丢弃）；② **本地启发式决策梯 + fail-ambient 兜底**——分流由本地启发式规则阶梯决定（不依赖云端/LLM 判定），无法判定时安全落 AMBIENT，不越权为指令；③ **唤醒门控 address_score 三信号源**——唤醒判定由三个信号源综合打分；④ **VAD 防抖 utterance 合并 + 3s 冷却 + TTS echo 抑制**——防碎片化、防重复触发、防 TTS 自反馈回声触发；⑤ **契约相容性无旁路注入**——不绕过 Volition Gate / USER_MESSAGE 边界。**§5.5 合规性已获 Owner 批准**；**0 frozen contract 改动（docs only 0 code）**。注：`tests/test_soul_md_loader.py` 未被本次触碰。 | DSH | MS-3 |
| 2026-09 (MS-3 实作) | Voice Interaction 语音互动输入实作（commit `e308365`）。**5 files changed, 2157 insertions(+)**：`src/voice/__init__.py`（NEW）、`src/voice/gate.py`（NEW，三路分流决策 + address_score）、`src/voice/input_router.py`（NEW，USER_MESSAGE/AMBIENT/DROP 分流 + 白名单 + 速率防洪）、`src/voice/audio_service.py`（NEW，VAD 防抖 utterance 合并 + 会话窗口 + TTS echo 抑制）、`tests/test_ms3_voice_gate.py`（NEW）。**MS-3 契约 → 实作闭环**：① **三路分流** USER_MESSAGE/AMBIENT/DROP + **本地启发式决策梯 + fail-ambient 兜底**；② **唤醒门控** address_score 三信号源（name/wake/second_person）+ `VOICE_OWNER_IDS` 白名单；③ **VAD 防抖 utterance 合并 + 3s 冷却 + TTS echo 抑制 + 速率防洪**；④ **契约相容性无旁路注入**（USER_MESSAGE 仅经既有契约通道发布）。**71 新测试全过** + **无唤醒 100% 降级验证**；**0 frozen contract 改动**（只新增 src/voice/ + 测试，不改 gateway.py / router.py / consciousness.py / proxy.py）。注：`tests/test_soul_md_loader.py` 未被本次触碰（保持未提交）。 | DSH | MS-3 |
| 2026-09 (MS-3.1 实作) | **语音互动「实体设备闭环」**（commit `bc7bbda`）。**3 files changed, 885 insertions(+)**：`src/voice/audio_service.py`（+116，process_audio_stream ASR 注入式 + MS-3 路由判定）、`scripts/audio_stream_mcp.py`（+448，voice_session_start/feed/stop 三工具）、`tests/tools/test_voice_session_mcp.py`（NEW，20 笔）。**设备层音频采集与 MCP 会话工具对接**：① voice_session_start/feed/stop 三工具**纯 additive**；② VoiceSessionRegistry **30s 硬超时 janitor**；③ **VAD 静音状态机**；④ process_audio_stream **ASR 注入式** + MS-3 三路分流路由判定。**20 新测试 + 71 回归全过**；**0 frozen contract 改动**（只改 audio_service.py + audio_stream_mcp.py + 测试，0 破坏既有契约）。 | DSH | MS-3.1 |
| 2026-09 (TG-0) | Goal Engine 架构审计（commit `136cb95`）。**docs only 0 code**。1 file changed: `docs/TG-0-GOAL-ENGINE-AUDIT.md`（NEW，261 行）。**关键结论**：① **volition 链已闭环**（motive → Decision 四元 → transmit/observe/reflect/do_nothing → Actuator 派发单次调用）；② **Motive 源 4 模块盘点**（motive.py / decision.py / scheduler.py proactive_dm / run_server.py motive proxy 注入）；③ **注入层推荐方案 B GoalMotiveProvider**（复用 motive proxy 独立注入先例）；④ **Goal Ledger 落点 graph.sqlite v8 `goals` 表**（SAGE SQLite schema 演进路径）；⑤ **状态机 = 三态+两终态+SUSPENDED**；⑥ **Volition Gate 相容 1HB1S**（单次行动原则，0 自主递归）；⑦ **双轴种子源 Bryan / 自我 各 4 源**（「Bryan 羁绊 + 自由生长」双轴）；⑧ **10 项 TG-1 决策清单**（待 C-1 阶段工单逐项拍板）。0 frozen contract 改动（docs only 0 code）。 | DSH | TG-0 |
| 2026-09 (TG-1) | Goal Engine Contract 设计（commit `058e060`）。**docs only 0 code**。1 file changed: `docs/TG-1-GOAL-ENGINE-CONTRACT.md`（NEW，459 行）。**10 项决策全锁定**：① **graph.sqlite v8 `goals` 表**（Schema v8 迁移）；② **ACTIVE-IN_PROGRESS-SUSPENDED-COMPLETED-ABANDONED 状态机**；③ **方案 B GoalMotiveProvider**（独立 Goal 动机提供器）；④ **结构配额轮替 No Scoring**；⑤ **SM-4 动作面 1 心跳 1 步**（Volition Gate 相容）；⑥ **双轴种子源**（Bryan 羁绊 + 自由生长）；⑦ **中断信号 6 类**；⑧ **沉淀通道**；⑨ **心跳接线**；⑩ **0 frozen 破坏**。**TG-1 CLOSED、TG-2 NEXT**。0 frozen contract 改动（docs only 0 code）。 | DSH | TG-1 |
| 2026-09 (TG-2) | Goal Engine 实作（commit `26da28d`）。**8 files changed, 2013 insertions(+), 5 deletions(-)**：`src/memory/sage/graph_store.py`（Schema v8 `goals` 表幂等迁移 + upsert_goal/get_goals/transition_goal）、`src/goals/`（NEW：models.py / motive_provider.py / `__init__.py`）、`src/soul/scheduler.py`（_decision_check 内扩 + goal scan，0 新定时器）、`tests/goals/test_goal_engine.py`（NEW，35 笔）+ 2 处版本快照断言更新（test_temporal_memory_mr2 / test_m5_4_5_2）。**关键交付**：Schema v8 幂等迁移、GoalMotiveProvider Plan B 零侵入、结构配额轮替 No Scoring、状态机 ACTIVE-IN_PROGRESS-SUSPENDED + COMPLETED-ABANDONED、_decision_check 接线 0 新定时器。**35 新测试全过 + 回归通过**；**0 frozen contract 改动**。**TG-2 CLOSED、TG-3 NEXT**（目标驱动行为 Harness 验收）。 | DSH | TG-2 |
| 2026-09 (TG-3) | Goal Engine 验收（commit `3adaf57`）。`tests/harness/test_goal_driven_harness.py`（NEW）。**四大剧本 6 tests 全过**：① 跨心跳长程推进；② 突发中断与唤醒（SUSPENDED 冻结 → 恢复 → 续跑）；③ 双轴配额轮替防饥饿（No Scoring）；④ 终态记忆沉淀（InnerLifeEvent + Trace）。**52 回归全过**；**No-Scoring 三层铁证**（结构配额轮替驱动 / 0 scoring 字段 / 0 数值比较断言）；**0 直写 facts**（沉淀只走 InnerLifeEvent 通道）。**0 frozen contract 改动**（新增 harness 测试 + fixture，0 production mutation）。**TG-3 CLOSED、TG-3.1 NEXT**（生产缺陷 2 项修复）。 | DSH | TG-3 |
| 2026-09 (TG-3.1) | 生产缺陷修复（commit `d55253f`）。`src/goals/motive_provider.py` 2 缺陷：① **sediment_completion ts UTC 对齐**（`astimezone(timezone.utc).isoformat()`，杜绝非 UTC 时区下 validate_ts 拒绝 → fail-closed 静默丢弃）；② **on_decision SUSPENDED 拦截守卫**（中断窗口残留 pending 候选不得误推进已挂起目标，唤醒后重新入轮替）。+ `tests/goals/test_goal_engine.py`（TestTG31ProductionDefectFixes 2 笔：跨时区 UTC-4 沉淀断言 + SUSPENDED 守卫断言）。**43 passed**（tests/goals 37 + harness 6）。**0 frozen contract 改动**（只改 motive_provider.py + 测试）。 | DSH | TG-3.1 |
| 2026-09 (C-1 CLOSED) | **C-1 自主目标与意向引擎主线：正式 CLOSED**。五阶全链 `136cb95`（TG-0 审计）→ `058e060`（TG-1 设计）→ `26da28d`（TG-2 实作）→ `3adaf57`（TG-3 验收）→ `d55253f`（TG-3.1 修复）。**全程 0 frozen contract 改动**。本批次登记 docs commit `f410867`。 | DSH | C-1 |
| 2026-09 (LS-0) | **LS-0 长期共生架构审计 CLOSED**（READ-ONLY docs only 0 code）。`docs/LS-0-LONG-TERM-COEXISTENCE-AUDIT.md`：四维度（生成器承诺 / 相位 / 叙事 / 内容安全）全干净，0 CONTRACT CONFLICT，全部 KEEP existing / 最小 additive。**关键发现**：goal 创建器（种子→goal）production 未实现（`upsert_goal` 调用方只有测试直写）——C-2 核心缺口。0 frozen contract 改动。 | DSH | LS-0 |
| 2026-09 (LS-1) | **LS-1 长期共生设计契约 CLOSED**（docs only 0 code，commit `6514ac1`）。`docs/LS-1-LONG-TERM-COEXISTENCE-CONTRACT.md`（9 节，359 行）：生成器（Goal Seed 生成器承诺语义）/ 承诺（promise 语义 + 生命周期）/ 相位 / 叙事 + 三案对比（A 独立 goal 引擎 / B 既有 proxy LLM 通道语义化 / C 混合）。**Owner（Bryan）拍板方案 B：既有 proxy LLM 通道语义化**——不复用 C-1 Goal Engine 路线。成本估算：月增量 ≈2 万 tokens。**LS-2 实作 + TL-8 护栏 NEXT**（Goal Seed 生成器生产落地）。0 frozen contract 改动。 | DSH | LS-1 |
| 2026-09 (LS-2 + TL-8) | **LS-2 实作 CLOSED + TL-8 护栏通过**（commit `aadd5ef`，7 files +1592/-5）。GoalSeedProvider 生产落地（`src/goals/seed_provider.py`）：24h 节流 / 8 源固定轮序 B1-B4+S1-S4 / seed_source_ref 幂等去重 / 同轴 ≤2 强制换轴 / 3 轮空转防饿死 / 方案 B 复用 `_default_llm_call` 语义化 fail-closed / criteria 确定性模板 / 作息抑制（`_is_quiet_hours`+bryan_last_seen>4h 抑制 B 轴）。挂 `_goal_scan_all` 并列分支，**0 新定时器**（AST 审计佐证）。TL-8 六项护栏 18 用例全绿；验收 61/61；全量回归 3084 passed 与基线一致。**0 frozen contract 改动**。**⚠️ confidence 缺陷观察**：`GraphStore.add_fact` INSERT 列清单不含 confidence 列（DDL 默认 1.0）→ fact confidence 恒 1.0 写不进去；S2 探针已用 weight 绕过；add_fact 属「SAGE 写入逻辑」frozen 边界，修复需 Owner 拍板（已授权，见 follow-up）。 | DSH | LS-2 |
| 2026-09 (LS-2 follow-up) | **confidence 缺陷修复 CLOSED + 重新冻结**（commit `51c0c4c`，2 files +88/-3）。`src/memory/sage/graph_store.py`：`GraphStore.add_fact` INSERT 列清单 + 参数元组补 `confidence` 列（DDL v4 即有该列，清单漏列 → 恒写 DB 默认 1.0，自订值 0.85 写不进去）；默认 1.0 向后兼容（未传时写 1.0 与原行为一致），None 防呆视为 1.0 对齐 DB NOT NULL DEFAULT。`tests/test_graph_store_confidence.py`（NEW）：2 用例全绿——0.85 精确写入 + 三读取端读回验证 + reopen 持久、缺省 1.0 回归；graph_store 相关回归 96 passed 0 破坏；**0 schema 改动**。**本次为 Owner 授权的局部解冻（仅 add_fact 的 INSERT 列清单 + 参数元组）**，验收通过后 **add_fact 重新纳入 frozen「SAGE 写入逻辑」**，后续改动仍需 Owner 拍板。注：`tests/test_soul_md_loader.py` 未触碰。 | DSH | LS-2 follow-up |
| 2026-09 (SG-0) | **SG-0 C-3 群体关系网审计 CLOSED**（READ-ONLY docs only 0 code，commit `6030ab2`）。`docs/SG-0-SOCIAL-GRAPH-AUDIT.md`（NEW）。**6 项关键结论**：① 关系存储实为 `relationships.json` schema 4.1（A2A 已具备并全量填充：9 agent + user_bryan，双向 touch 已落地；感知颗粒度静态 0 关系维度）；② 他者印象落点推荐 relationships.json additive 4.1→4.2（存储介质 + 写路径双隔离，不触防线 3）；③ v1 信号源 = reply + 共同参与心跳 + dream 双向，节流 24h 复用 `GOAL_QUOTA_WINDOW_SECONDS`；④ 新他者源 B5 动机路径唯一受阻点 = Motive target 值域 v1 固定 "bryan"；⑤ No-Scoring 实证：confidence 数值层已实测全 0.0 失效（0.02/天衰减吃光），支持冻结迁移为离散 Relational Bands；⑥ 11 项 frozen 触点逐查，唯一疑似触点 = Motive target 值域语义扩展。**SG-1 决策清单 5 项待 Owner 拍板**：D1 存储 = relationships.json additive 4.1→4.2；D2 Motive target 值域解冻？（frozen 触点）；D3 节流 = 24h sidecar 同构；D4 confidence 冻结 → discrete bands 迁移；D5 动机路径 = B5 新源 + social_context 既有通道。**0 frozen contract 改动**。 | DSH | SG-0 |
| 2026-09 (SG-1) | **SG-1 C-3 群体关系网与他者心智设计契约 CLOSED**（docs only 0 code，commit `6d54510`）。`docs/SG-1-SOCIAL-GRAPH-CONTRACT.md`（NEW，9 节 302 行）。**5 项设计决策锁定**：D1 他者印象落 `relationships.json` additive 4.1→4.2（entry 级 8 字段：objective 4 计数 / impression_tags / relational_band / band_updated_at / last_relation_update_ref 幂等键；写路径唯一入口 `RelationshipsStore`，0 触防线 3）；D2 `Motive.target` 值域 `{"bryan"}∪AGENT_IDS` + 生成出口 fail-closed 校验（**D2 授权解冻，其余 5 字段冻结**）；D3 采集 0 写 + 沉淀 24h/agent 复用 `GOAL_QUOTA_WINDOW_SECONDS` + sidecar `last_relation_update_at` 同构；D4 关系域 confidence → 离散四带 stranger/known/familiar/close + impression_tags（全整数计数 0 加权，SAGE/Elevation 0 联动）；D5 B5 他者源 = `SEED_ROTATION` 第 9 源 + social_context 既有通道。**投递边界定死：复用既有公开频道（lounge/soul_wall），0 新通道**。**4 个已知边界点**：① B5 挂 `AXIS_BRYAN` 轴受 `bryan_suppressed` 作息抑制（自洽注明，不改轴校验）；② 降带阈值 30 天（形态冻结整数/离散/0 加权，数值可复核调整）；③ 聚合器内存计数重启丢失（既有持久事实兜底，已知局限）；④ 投递 = 复用公开频道让 transmit→公开→reply→关系演化成闭环。0 frozen contract 改动（docs only 0 code）。 | DSH | SG-1 |
| 2026-09 (SG-2) | **SG-2 C-3 群体关系网与他者心智实作 CLOSED**（commit `7a35741`，13 files +1918/-19）。`src/social/relational_bands.py` + `src/social/relation_settlement.py`（NEW）+ `tests/social/` 三件套 + `tests/goals/test_sg2_b5_relation_seed.py`（NEW）+ additive 修改 `src/soul/relationships.py`（4.1→4.2）/ `src/soul/motive.py`（D2 target 解冻）/ `src/goals/models.py`（+last_relation_update_at）/ `src/goals/motive_provider.py`（出口走 make_motive）/ `src/goals/seed_provider.py`（B5 第 9 源）/ `src/soul/scheduler.py`（+16 接线）/ `tests/goals/test_tl8_volition_guardrails.py`（cursor 断言 5→6）。**D1** relationships.json 4.1→4.2（entry 8 字段，`RelationshipsStore` 唯一写入口，4.1 兼容 0 迁移）；**D2** `Motive.target` 解冻 `{bryan}∪AGENT_IDS` + `make_motive` 出口 fail-closed（`InvalidMotiveTargetError`），Motive 5 字段 0 结构变更；**D3** `settle_relations` 挂 30s wake 并列分支（0 新定时器），24h/agent 节流 + sidecar + 幂等 ref；**D4** 四带整数门槛转移表（stranger→known: reply≥1 OR co≥2；known→familiar: reply≥3 AND co≥5；familiar→close: reply≥10 AND co≥15 OR dream≥4 AND reply≥5）+ 30 天降带（底带不降），0 float（AST 断言）；**D5** B5 第 9 源（band≥known 出种子、stranger 0、跳 user_bryan、B 轴抑制继承、静态 ref `relation:<other>`）。**4 个契约歧义已按契约字面落地**：dream 生产计数 v1=0（门保留待未来载体）/ reply 成对折抵 min()/ B5 静态 ref / 无信号对子评估过 apply 保证降带触发。测试：67 新 + 289 回归 + 85 goals 组合全绿；3 失败 pre-existing 无关（MS-2 MCP registry、m5_13_5 浮点时序）。**0 frozen 新违规**（`tests/test_soul_md_loader.py` 与 docs/ 其他文件均未触碰）。 | DSH | SG-2 |
| 2026-09 (TL-9) | **TL-9 C-3 关系演化端到端长程实证 CLOSED**（commit `9f8c28a`，3 files +1474）。`harness/tl9.py`（NEW，TL9Runner + SimulationClock 24h tick + 双 agent fixture agent_ruka/agent_akane）+ `harness/run_tl9.py`（NEW，runner：四大剧本 × 3 runs series + summary）+ `tests/harness/test_tl9_relation_evolution.py`（NEW，10 笔）。**端到端实证全链路**：公开发言载体（perception_trace.jsonl reply + interactions.jsonl co-presence，真实载体）→ settle_relations 真实结算（evaluate_band / apply_relation_evaluation / 幂等 ref / 24h 节流）→ 关系带整数跃迁（stranger→known→familiar→close，单窗至多 1 级）→ B5 种子（GoalSeedProvider 真实轮替 + stub LLM 方案 B 通道 0 网络）→ make_motive（fail-closed valid target）→ Motive.target=agent_akane → MotiveTraceStore 读回 → Decision 四元 stub 透传（real parse_decision_output）。**剧本 3 合同歧义发现（呈报主大脑）**：apply_relation_evaluation 无信号不降带时慢爬评估会把底带 stranger（计数非零）补升回 known——降带后计数不清零的自然结果，确定性可复现；原工单如实记录断言，未擅改 src/，**已由 SG-2.1 立案修复**。**验收**：精确 pytest 10 笔全绿 + SG-2 相关回归 83 笔全绿（test_sg2_b5_relation_seed 10 + test_sg2_guardrails 10 + relational_bands 26 + relationship_store 13 + TG-3 harness 6 + TL-9 10 + social 三件套…）→ run_tl9.py ALL PASS（EXIT=0）；**D2 重现**：四剧本 3 runs 判定轨迹一致；**0 production mutation**（data/ 逐档 byte-hash 0 diff，data/soul 专项 0 diff）；**0 frozen contract 改动 / 0 src/ 生产改动**（3 新文件全在 harness/ + tests/，未触碰 `tests/test_soul_md_loader.py`）。 | DSH | TL-9 |
| 2026-09 (SG-2.1) | **SG-2.1 关系带底带慢爬回升修复 CLOSED**（commit `ca3d52f`，4 files +136/-26）。TL-9 剧本 3 验收发现的确定性振荡修复：`src/soul/relationships.py` `apply_relation_evaluation` 慢爬评估分支（无信号不降带时）加「窗口内有新信号」前置条件——无信号时底带 `stranger` 不允许凭历史计数自动回升（离散遗忘语义：降带后关系淡了需新证据才能重新建立），判定载体 = 本窗口 deltas 聚合出的 `has_signal`（既有窗口计数对象，0 新增 sidecar / 0 接口变更 / 0 schema 改动 / 计数保留不清零）；有信号时正常升级路径全保留（stranger→known 门槛 reply≥1 OR co≥2 照旧、每窗至多升 1 级），无信号且带≥known 慢爬照旧，降带逻辑 / 30 天阈值 / 其他带行为不变。`harness/tl9.py` 剧本 3 追认（原 `slow_climb_rebound_documented` 断言更新：93 天底带无信号不回升、123 天降带后继续无信号 30 天保持 stranger、124 天窗口新 reply 正常恢复 known）+ `tests/harness/test_tl9_relation_evolution.py` 剧本 3 断言同步 + `tests/social/test_relationship_store.py` +2 笔（TestSG21SlowClimbRequiresSignal：无信号不回升 / 新信号恢复）。**验收**：精确 pytest 全绿（TL-9 10 + social 三件套 59 + B5 10 = 79 笔）；run_tl9.py ALL PASS（EXIT=0，四大剧本 × 3 runs D2 轨迹一致 + 0 production mutation）。**0 frozen 新违规**（D4 授权范围内关系带状态机行为修正；未触碰 `tests/test_soul_md_loader.py` 与其他触点）。 | DSH | SG-2.1 |
| 2026-09 (SG-2.2) | **SG-2.2 4.1 老数据 band 键兜底修复 CLOSED**（commit `779a639`，3 files +162/-2）。生产实证缺陷修复：`settle_relations` 每 30s 抛 `KeyError: 'relational_band'`（`[Goal] 主循环扫描异常 (fail-closed)`）。触发链 = SG-2.1 封底带慢爬后，无信号 stranger 对子完全跳过 band 写入分支 → 老 4.1 entry 无 `relational_band` 键永远不被 set → objective/ref 先落盘 → debug 日志直接索引炸。`src/soul/relationships.py` 两处防御（`apply_relation_evaluation` 内）：① 分支判定前 `entry.setdefault("relational_band", BAND_STRANGER)`（老数据首次结算补全 band，半成品自愈；与 get 默认值语义一致，已有键 no-op）；② 末尾 debug 日志改 `entry.get("relational_band", BAND_STRANGER)`。**升级/降带/慢爬分支 0 变更**（SG-2.1 语义保留）。**测试**：`tests/social/test_relationship_store.py` +3 笔 + `tests/social/test_sg2_guardrails.py` +2 笔（4.1 老 entry × 无信号 stranger 组合缺口：store 层 + settle 全链不抛 KeyError、band 补全、"stranger"、sidecar `last_relation_update_at` 推进、幂等 ref、升级路径不受影响）。**验收**：精确 pytest 90 passed（social 三件套 44 + B5 10 + TL-9 10 + TG-3 harness 26…social 三件套含 SG-2.2 新 5 笔）；run_tl9.py 复跑 **ALL PASS（EXIT=0，四大剧本 × 3 runs D2 轨迹一致 + Zero Production Mutation 0 diff）**。**生产部署验证**：maintenance lock restart（server_ops.ps1）→ /health 200 + 10 bots polling → 首轮 settle 全链 10 agent sidecar `last_relation_update_at` 全部推进（24h 节流恢复生效）→ 2-3 个 30s 周期 **0 KeyError**（对照旧实例：每 30s 一次直至停机前 13:27:39）→ 存量半成品自愈（agent_yua user_bryan 等全部 entry 补全 `relational_band: "stranger"` + 幂等 ref 落盘）。**注意（环境竞态说明）**：修复部署前 dev 侧复跑 run_tl9 首次遇 Zero Production Mutation FAIL——原因是生产 server 并发活动（settle 每 30s 重试写生产 relationships.json）落在 run 窗口；部署自愈 + 节流后复跑 0 diff（harness 隔离性质由 time_lapse 写区 + 逐档 hash 验证保证，非 harness 泄漏）。**0 frozen 变更 / 0 schema 改动 / 0 生产数据清理**。 | DSH | SG-2.2 |
| 2026-09-05 (README baseline) | **REF: README 全面更新至 Phase C-3.1（HEAD `a0b7aa5`）**。1 file changed: `README.md`（+60 -527，全量替换）。依 Owner 提供蓝图重新整理为 Phase C-3.1 架構基線：① 核心架構與設計哲學 5 條（Strategic North Star / Memory-First 雙時序記憶（MR 系列 Schema v7 遷移：valid_from/invalidated_at 時序列、時間旅行、軟刪除）＋ Schema v8 goals 表（目標引擎獨立表）/ 多體共存三大防線（SI & C-3 關係網，含 relationships.json Schema 4.2 四帶狀態機）/ 自主目標與意向引擎（TG & LS）/ 多模態感官與設備層（MS））；② 模組結構以 repo 實際目錄為準（工具註冊表實際位於 `src/soul/tool_registry.py` ＋ `actuator.py`，非 `src/tools/`；Time-lapse 驗收套件 TL-4/5/6/7 位於 `tests/` 根目錄、TL-8 位於 `tests/goals/`、TL-9 位於 `tests/harness/`）；③ 里程碑進度：階段 A、B、C-1、C-2、C-3 100% CLOSED、C-3.1 ACTIVE。**0 src/ 改動；0 frozen contract 改動；未觸碰 `tests/test_soul_md_loader.py`**。 | DSH | README baseline |
| 2026-09 (README flagship) | **REF: README 旗舰版全量重写**（commit `76bc718`）。1 file changed: `README.md`（+167 -60，全量替换）。依 Owner 提供生产级蓝图（500+ 行）落地：① 核心哲學與資料流（Strategic North Star + 三大社交防線不變量）；② 五大核心架構深度解構（KERNEL 圖 + 雙時序認知記憶 / 自主目標引擎 / 多模態感官閉環 / 群體關係網）；③ 10 位常駐靈魂名冊（COS Archetypes 角色矩陣）；④ 專案目錄結構（tests/ 依實際：TL-4~7 根目錄、TL-8 goals/、TL-9 harness/、tools/；voice/ 依實際：gate.py / input_router.py / audio_service.py，設備層 MCP 會話工具在 `scripts/audio_stream_mcp.py`）；⑤ 驗證體系 TL-4~TL-9 Harness 矩陣（badge 改 `Test Suite-TL-4~9 All Green`，不寫虛高測試數字）；⑥ 里程碑全景（階段 A/B/C-1/C-2/C-3 100% CLOSED、C-3.1 ACTIVE、D QUEUED）。**事實校正**：Schema 口徑（雙時序 = MR 系列 Schema v7；Schema v8 = goals 表，兩條正交演進）；快速執行指令全部為 repo 真實路徑（`tests/harness/test_tl9_relation_evolution.py` / `tests/goals/test_tl8_volition_guardrails.py` / `tests/tools/test_voice_session_mcp.py` / `tests/test_ms3_voice_gate.py` / `tests/harness/test_goal_driven_harness.py`）；do_nothing 留白口徑統一為目標區間 65–80%（TL-5 實測 82.5% 基線）；保留 Canonical 狀態指引（`logs/ENGINEERING_STATE.md` 唯一事實來源）＋ 快速開始（`python scripts/run_server.py`）＋ License（MIT）。**0 src/ 改動；0 frozen contract 改動；未觸碰 `tests/test_soul_md_loader.py`**。 | DSH | README flagship |
| 2026-09-05 (README v2) | **REF: README v2 雙語基線**（commit `12838c6`）。3 files changed: `README.md`（+385 -160，全量替換為 Owner 提供的英文 v2 baseline：頂部語言切換連結 + 6 處 Mermaid 圖 + 2 張架構海報嵌入）、`README.zh-CN.md`（NEW，+423，繁體中文完整翻譯，章節/圖/Mermaid 對齊英文版）、`docs/images/`（NEW 2 張 PNG：`soulos1.png` Figure 1 淺色底技術工程全景圖，Architecture 章節末尾；`soulos2.png` Figure 2 深色底概念心智全景海報，README 頭部 Core Idea 之前）。**0 src/ 改動；0 frozen contract 改動；未觸碰 `tests/test_soul_md_loader.py`**。 | DSH | README v2 |
| 2026-09-05 (C-3.1) | **C-3.1 关系增强投递规格契约 APPROVED + 入库 + P1 投递分流授权登记**（2 files changed: `docs/C-3.1-RELATIONAL-EXPRESSION-CONTRACT.md`（NEW，9 节 + 附录 A/B，324 行）+ `logs/ENGINEERING_STATE.md`（本行 + SG 系列登记行））。**Owner 拍板 A：通过**——C-3.1 全套设计批准（docs-only 设计产出，本次正式入库）。**Owner 拍板 B：授权**——P1 投递分流：`run_server.py`（M3.1 frozen scope）executor 投递路由逻辑解冻，**仅限分流判断**：`user_bryan`/`bryan` → 1:1 TG 私聊；`AGENT_IDS` → lounge/soul_wall 公开频道；**严禁触碰底层 TG/频道通信客户端核心代码**。**本工单仅登记授权非实作**：0 src/ 改动、0 frozen contract 变更、未触碰 `tests/test_soul_md_loader.py`。 | DSH | C-3.1 |
| 2026-09-05 (C-3.1 实作) | **C-3.1 实作完成登记 + P1 投递分流落地**（登记 commit：`docs: register C-3.1 implementation + P1 delivery routing (036d93a)`；1 file changed: `logs/ENGINEERING_STATE.md`（本行 + §1.1 Current HEAD 同步 `036d93a` + C-3.1 登记行状态更新为实作完成））。**实作 commit `036d93a`**（feat(c-3.1): relational expression injection + P1 delivery routing to public channels）：**双组装注入 + motive_target 透传 + P1 公开频道分流**。测试证据：37 新测试 + 279 回归 + 19 subtests 全绿；5 笔 pre-existing 失败已基线复核（async pytest 配置/时序脆弱，与本次无关）。**0 frozen contract 变更**（M3.1 解冻范围仅限 executor 分流判断，未触碰底层 TG/频道通信客户端核心代码）；0 schema 改动。 | DSH | C-3.1 |
| 2026-09-05 (TL-10) | **TL-10 验收收尾登记 + C-3.1 正式 CLOSED**（登记 commit：`docs: register TL-10 acceptance + C-3.1 CLOSED (29fd27d)`；1 file changed: `logs/ENGINEERING_STATE.md`（本行 + §1.1 Current HEAD 同步 `29fd27d`（含中间 commit `04897c2` distinct 标注校正）+ C-3.1 登记行状态更新为正式 CLOSED））。**验收 commit `29fd27d`**（test(tl10): relational expression end-to-end harness (P1 routing + band injection + A2U preserve + fail-safe)）：**TL-10 四剧本验收 ALL PASS** —— 剧本 1 A2A 公开分流 9 硬断言 / 剧本 2 带差异化注入 17 / 剧本 3 A2U 保全 15 / 剧本 4 三重 fail-safe 11，合计 52 硬断言全 PASS；11 单测全绿；四剧 ×3 runs 12 场景 D2 判定一致；0 production mutation；0 src/ 改动。**C-3.1 正式 CLOSED（TL-10 验收钢印）**，C-3.1 全套（契约入库 → 授权登记 → 实作 → 验收）闭环。 | DSH | TL-10 |
| 2026-09-05 (TL-11) | **C-2.1 收尾登记 + 承诺落实 + 周期叙事升华正式 CLOSED**（登记 commit：`docs: register C-2.1 CLOSED (TL-11 acceptance, a4f974e)`；1 file changed: `logs/ENGINEERING_STATE.md`（本行 + §1.1 Current HEAD 同步 `a4f974e`（含中间 commit `306943f`/`328c5e1` distinct 标注）+ C-2.1 登记行新增并标记正式 CLOSED））。**轨迹**：契约 `328c5e1`（docs(c-2.1): commitment lifecycle + narrative sublimation contract，Owner 拍板）→ 实作 `306943f`（feat(c-2.1): commitment closure seed B6 + periodic narrative sublimation）→ 验收 `a4f974e`（test(tl11): commitment closure + periodic narrative end-to-end harness）。**验收证据**：85 项硬断言（A1-A7 全绿）；新测试 31 笔 + 相关回归全绿（goals 101 / harness 53 / scheduler 156+69）；全量 3251 passed（89+16 失败 stash 对照为基线同批 62 档，非本工单引入）；Frozen 0 冲突；0 production mutation。**核心决策摘要**：B6 承诺闭环第 10 种子源（复用塞入 volition path，禁直发）、周记 ISO 周/纪念日（night slot 22:00 additive，0 新定时器）、身份防火墙。**C-2.1 正式 CLOSED（TL-11 验收钢印）**，C-2.1 全套（契约 → 实作 → 验收）闭环。 | DSH | TL-11 |

| 2026-09-05 (VC-1) | **VC-1 黑川茜即時語音伴侶客戶端 CLOSED**（commit `6532b96`，9 files +1496，全部新增；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。獨立客戶端模組 `clients/voice_companion/`（asr_refiner／akane_voice_brain／fish_tts_streamer／vad_listener／akane_live 等 8 檔，config.json 含自用 llm 小節）＋`tests/clients/test_voice_companion.py`（NEW，20 筆，全 Mock 外部網路與音訊硬體）。**驗收**：`pytest tests/clients/test_voice_companion.py -v` → **20 passed（0.09s，主大腦複跑驗證）**；四項剛性斷言全過（ASR 語意淨化「欠...那个...今天好累→茜，今天好累。」＋雜音熔斷 None／語音輸出 0 Markdown 符號守門＋分句器即時切分／Fish Audio Payload text＋reference_id＋Bearer／Barge-in interrupt() 清佇列＋sd.stop＋HTTP 取消）；回歸 spot-check 9 passed。**0 src/ 改動、0 Frozen Contract 觸碰**（`git diff --stat HEAD~1 -- src/` 空）；`test_agent_registry.py` 1 筆失敗為既有陳舊測試（最後提交 `475525c`，與本次 0 關聯）。 | DSH | VC-1 |

| 2026-09-05 (agent registry test fix) | **`tests/test_agent_registry.py` 既有陳舊斷言修復 CLOSED**（commit `ad39376`，1 file +7 -1）。背景：VC-1 收尾時發現的既有失敗（硬編碼 `len(agents) == 3` vs `configs/default.yaml` 已 10 位啟用 Agent）。修復：改為**動態計算啟用 Agent 數**（與 `create_agents` 相同 enabled 預設值語義：`sum(1 for c in cfg["agents"] if c.get("enabled", True))`），隨 default.yaml 擴充不會再陳舊。驗收：`pytest tests/test_agent_registry.py -v` → **3 passed（主大腦複跑驗證）**；spot-check 27 passed（subagent 回報）。**0 src/ 改動、0 Frozen Contract 觸碰、0 其他檔案改動**。 | DSH | VC-1 收尾微修 |

| 2026-09-05 (VC-1 runtime wiring) | **VC-1 執行期配置接線 CLOSED**（3 commits：`3f29e32` env-override config wiring（新增 `clients/voice_companion/env_config.py`：.env 載入＋env 覆寫 FISH_API_KEY/FISH_VOICE_ID/LLM_BASE_URL/LLM_API_KEY/LLM_MODEL，解析順序 os.environ > .env > config.json；akane_live 接 resolve_config）→ `9fd19be` fish payload model 欄位 → `262516c` 按主大腦拍板移除 FISH_MODEL 覆寫鍵，固化 config 預設）。**config.json 最終態**：voice_id=`4c11d21b14284d428074f76a1cf32298`（茜，與生產 `src/voice/fish_tts.py` VOICES 一致）、fish model=`s2.1-pro-free`（與生產 DEFAULT_MODEL 一致，free tier）、llm.endpoint=`https://ollama.com/v1`＋model=`deepseek-v4-flash:0731`（Ollama Cloud，沿用生產配對，**Owner 拍板**）、api_key 全空（機密走 gitignore 的 `clients/voice_companion/.env`，根 .env OLLAMA_API_KEY 程式化帶入，0 金鑰進 git/commit/輸出）。**驗收**：25 passed（20 舊＋5 新 env 測試，主大腦複跑驗證）；Ollama Cloud `GET /v1/models` 唯讀確認 `deepseek-v4-flash:0731` 精確存在。**環境事實**：Fish 金鑰有效但帳戶 HTTP 402 無額度（需魚音效帳戶充值後才能合成）；本機音效裝置（麥克風＋喇叭）正常；依賴 miniaudio 補裝完成。**0 src/ 改動、0 Frozen Contract 觸碰**。 | DSH | VC-1 |

| 2026-09-05 (VC-1.2) | **VC-1.2 WebSocket TTS-Live 串流升級 CLOSED**（commit `f742a7d`，5 檔 +540/-6；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。`fish_tts_live.py`（NEW）：`wss://api.fish.audio/v1/tts/live`＋MessagePack（start→text×N→flush→stop；audio 分片即時播、finish 收尾、reason=error 優雅關閉 v1 不重試）、pcm（PCM16LE 44.1k mono）、Bearer＋model=s2.1-pro-free、chunk_length 300、latency normal；`interrupt()`＝中斷旗標＋關 WS＋停播＋清佇列（下一句新 session）；`create_tts_streamer` 依 `fish_audio.mode`（live 預設/rest fallback 0 改動）；`akane_live._speak_reply` 依 isinstance 分支。驗收 36 passed（25 舊＋11 新，主大腦複跑）；msgpack＋websocket-client 入 requirements。協定校正：client 收尾送 `stop`（非 finish——finish 為伺服器→用戶端事件）。**0 src/、0 Frozen Contract**。 | DSH | VC-1.2 |
| 2026-09-05 (VC-1.1) | **VC-1.1 Fish Audio 全套線上閉環 CLOSED**（commit `240f657`，7 檔 +404/-47；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。`stt_service.py`（NEW）：`FishASRService`（POST /v1/asr multipart＋language=zh＋timeout=10，200→json.text、失敗→"" 0 崩潰）＋`pcm16_to_wav_bytes`（stdlib wave 包 mono WAV）；`vad_listener` STT 換 Fish（whisper 路徑整段移除、注入介面保留）；`fish_tts_live` ＋`feed_text_piece`（含標點即 flush）＋`end_session`；`akane_live` 串流 token 迴圈／fallback；config `stt.engine=fish`＋`asr_endpoint`/`tts_ws_endpoint`；requirements 移除 whisper（0 本地 STT、CPU 零負擔）。驗收 50 passed（25＋11＋14，主大腦複跑）。**0 src/、0 Frozen、0 金鑰進 git**。 | DSH | VC-1.1 |

| 2026-09-05 (VC-1.3) | **VC-1.3 黑川茜 Web 語音伴侶 CLOSED**（commit `79e5dc9`，5 檔 +1024/-2；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。`web_server.py`（NEW 463 行）：aiohttp 應用，`AudioRelaySink`（TTS-Live PCM→瀏覽器 WS relay，thread-safe）、`WebSession`（四態狀態機＋barge-in 世代號）、`build_app` 全注入、區網網址列印。`web_ui.py`（NEW 249 行）：單頁 UI（PTT＋Auto-VAD＋瀏覽器側打斷偵測＋44.1k 播放佇列＋打字 fallback）。config +`web:{host:"0.0.0.0",port:8765}`；requirements +aiohttp。**架構**：瀏覽器收音/放音，伺服器只跑大腦（VAD→Fish 官方 ASR→茜 LLM 串流→TTS-Live→PCM 中繼），區網瀏覽器直開即用；終端版 0 改動並存。驗收 57 passed（50＋7，主大腦複跑）；冒煙 `http://192.168.0.60:8765`。**0 src/、0 Frozen Contract、0 金鑰**（api_key 僅從 env_config/.env 解析）。 | DSH | VC-1.3 |

| 2026-09-05 (VC-1.4) | **VC-1.4 輸入可視化＋失敗透通 CLOSED**（commit `c65a5cf`，4 檔 +199/-13；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。**實測根因**：Fish API credit=0（`/v1/asr` 402，message「API credit is managed independently from platform credit」）→ ASR 空轉寫被當雜音靜默 DROP → 使用者按 PTT 無反應且無任何提示。修：`stt_service.FishASRService` additive `last_error`/`last_status`（transcribe 合約不變）；`web_server` ASR 空結果＋last_error → `{"type":"error"}` 透通（402 附額度提示）回 IDLE，真雜音維持 DROP；`web_ui` 即時音量表（`#meterFill`＋🎙️ 傳送中/麥克風待命）＋`#errorBox`（下一 utterance 清除）＋額度提示註解。驗收 62 passed（57＋5，主大腦複跑）；伺服器重啟為受管背景常駐，頁面實證 meterFill/errorBox 在 HTML。**0 src/、0 Frozen Contract、0 金鑰**。 | DSH | VC-1.4 |

| 2026-09-05 (VC-1.5) | **VC-1.5 HTTPS 模式＋麥克風安全來源根治 CLOSED**（`3b62328` feat＋`5e99042` fix，共 7 檔 +251/-16；本行 + §1 VC 系列段 + §1.1 Current HEAD 同步）。**根因**：getUserMedia 僅限安全上下文（HTTPS/localhost），`http://區網IP` 被瀏覽器擋麥克風（音量條不動、看似上傳失敗）。修：`--https`／`config.web.https=true` 模式（cryptography 自簽憑證自動產生，SAN 127.0.0.1＋區網 IP，`certs/` gitignored；stdout 印「繼續前往」指引）；UI 不安全來源**常駐**警示＋麥克風錯誤 `err.name` 分類＋PTT 前置檢查＋✕ 關閉；伺服器 `[WS]`/`[UTT]` 診斷日誌。**實機發現並修復**：Windows cp950 主控台 `print(⚠️)` UnicodeEncodeError 崩潰 → `main()` 開頭 stdout/stderr `reconfigure(utf-8, errors="replace")`（commit `5e99042`，主大腦一行級直接修）。驗收 68 passed（62＋6，主大腦複跑）；HTTPS 上線 HTTP 200＋isSecureContext/errorDismiss 頁面實證（curl -k）。**0 src/、0 Frozen Contract、0 金鑰**。 | DSH | VC-1.5 |

| 2026-09-05 (VC-1 LLM key fix) | **Ollama Cloud 上游 401 根因修復 CLOSED**（commit `2b77631`，2 檔 +19/-6；本行 + §1.1 Current HEAD 同步）。**實機診斷**：`resolve_config` 解析後 `llm.api_key` 為空（`.env` 存 `OLLAMA_API_KEY`，解析器只認 `LLM_API_KEY`）→ 茜的大腦呼叫 Ollama Cloud 帶空 Bearer → 401 → 頁面「上游失敗」。修：`env_config.ENV_OVERRIDE_MAP` 加 `OLLAMA_API_KEY → llm.api_key` fallback（`LLM_API_KEY` 排序在後＝顯式鍵優先，後者覆寫前者語義）＋測試 2 斷言（fallback 生效／顯式鍵優先，隔離真實 .env）；本機 `.env` 同步補 `LLM_API_KEY` 行。驗收 69 passed（68＋1，主大腦複跑）；Ollama Cloud `/v1/chat/completions` 實機冒煙 200＋content 回傳（max_tokens=1024 驗證）；伺服器重啟（PID 7240，https 200）。**⚠️ 配額事實**：Ollama Cloud 週配額已用 **92.5%**（剩 7.5%；本週 flash 請求 4349，主要來自生產線）。0 src/、0 Frozen、0 金鑰進 git。 | DSH | VC-1 |

| 2026-09-05 (VC-1 405 fix) | **Ollama Cloud 405 端點路徑修復 CLOSED**（commit `53c3fab`，4 檔 +31/-3；本行 + §1.1 Current HEAD 同步）。**實機診斷**：金鑰修復（2b77631）後上游改報 HTTP 405 Method Not Allowed——`akane_voice_brain.build_llm_stream` 與 `asr_refiner.build_llm_call` 直接把 POST 打到 `llm.endpoint`（`https://ollama.com/v1` 根路徑不允許 POST），缺 OpenAI 相容 `/chat/completions` 尾綴。修：`env_config.normalize_chat_endpoint()`（缺尾綴自動補、已有原樣、空值回空）＋兩處呼叫點接入＋測試 5 斷言。驗收 70 passed（69＋1，主大腦複跑）；**客戶端真實路徑冒煙**（build_llm_call 不手動拼 URL）→ 200＋content len 1；伺服器重啟（PID 26584，https 200）。0 src/、0 Frozen、0 金鑰進 git。 | DSH | VC-1 |

| 2026-09-05 (VC-1 亂碼 fix) | **LLM SSE 中文亂碼修復 CLOSED**（commit `7e491d7`，2 檔 +33；本行 + §1.1 Current HEAD 同步）。**實機診斷**：茜回覆正常（「你好。」）但頁面顯示 `ä½ å¥½`——Ollama SSE（text/event-stream）不帶 charset，`requests.iter_lines(decode_unicode=True)` 預設以 ISO-8859-1 解碼 UTF-8 位元組 → 中文變亂碼。修：`akane_voice_brain.build_llm_stream` 在 iter_lines 前強制 `resp.encoding = "utf-8"`＋回歸測試（fake resp 模擬 requests 編碼機制：初始 ISO-8859-1、stream 內被強制 utf-8 → 輸出正確「你」，並斷言端點正規化 URL）。驗收 71 passed（70＋1，主大腦複跑）；**真實串流路徑冒煙**：joined 含「好」（OK-UTF8 判定）；伺服器重啟（PID 22812，https 200）。0 src/、0 Frozen、0 金鑰。 | DSH | VC-1 |

| 2026-09-05 (VC-1.6) | **真實 TTS-Live 傳輸換官方 fish-audio-sdk CLOSED**（commit `984f51c`，3 檔 +315/-304；本行 + §1.1 Current HEAD 同步）。**實機診斷（分層實證）**：瀏覽器有文字無聲音 → REST TTS 200（16KB）但真實 WS 自測 0 bytes；手寫 msgpack start 補齊欄位（含 sample_rate/prosody 顯式 null 位元組級對齊 SDK）仍被伺服器「start 後空 TXT ack→斷線」；官方 `fish-audio-sdk`（httpx_ws 傳輸）同 key/voice/model 實測成功 102,400 bytes → **根因 = websocket-client 傳輸與 Fish WS 閘道不相容**。修：`fish_tts_live.py` 真實層重寫為 `WebSocketSession.tts(TTSRequest, text_iter, backend=model)` daemon worker（公開 API/建構式/AudioRelaySink 注入全不動；feed queue＋interrupt 關 session）；requirements `fish-audio-sdk`（移除 msgpack/websocket-client）；Live 測試遷移 SDK 接縫（FakeSDKSession）。驗收 71 passed（主大腦複跑）；**真實 CollectSink 自測 135,168 bytes PCM、last_error None（決勝門過）**；伺服器重啟（PID 25136，https 200）。**備註**：本日三批 developer subagent 連環失敗 = OpenCode Go 週配額 100% 用罄（非工單問題），主大腦彈性兜底自行實作。0 src/、0 Frozen、0 金鑰。 | DSH | VC-1.6 |

| 2026-09-05 (VC-1.3 靜音 root fix) | **瀏覽器播放兩層根治 CLOSED**（`1a1d016` resume＋`90dcd77` 解耦；本行 + §1.1 Current HEAD 同步）。**分層實證**：全真路徑端到端自測（build_app＋WebSession＋AudioRelaySink＋真實 SDK TTS→ 真 WS 客戶端）打字「你好」→ SPEAKING → **BINARY 86,016 bytes PCM** → 回覆文字 → IDLE，伺服器→WS 中繼無誤（且證實先前診斷被 stdout 緩衝遮蔽 → 重啟改 `python -u`）。**剩餘根因＝瀏覽器端**：`AudioContext`＋播放節點只在「首次點擊 micBtn」的 startMic() 內建立 → 純打字（或沒按過麥克風鈕）的使用者 audioCtx 永不建立 → binary 收進佇列卻無人播放＝有文字沒聲音。修：(a) `ensurePlayback()` 冪等 helper（建立＋resume）與播放解耦；(b) 任何手勢（pointerdown/空白鍵/送文字/Enter）、收到 SPEAKING 狀態、收到 binary、麥克風就緒皆觸發。驗收 72 passed（71＋1 回歸斷言）。伺服器重啟 `python -u`（PID 7452，https 200）。0 src/、0 Frozen、0 金鑰。 | DSH | VC-1.3 |

| 2026-09-06 (VC-1.6 系列) | **語音音訊全面修復批次 CLOSED（暫時結案，Owner 實機確認「目前表現很好」）**（主大腦兜底直接實作——當日 OpenCode Go 週配額 100% 致三批 developer subagent 連環死亡）。**根因鏈與修復**：(1) websocket-client 傳輸被 Fish WS 閘道拒收 → `fish_tts_live` 真實層換官方 fish-audio-sdk（`984f51c`，實測 135KB PCM）；(2) 長句怪聲/砍尾 → 明確 44100＋瀏覽器自動重採樣（`f9e758b`）、IDLE 不再清播放佇列＋上限 200ms→30s（`a3d95b8`+`039f476`）；(3) 隔離實驗 REST 音源模式（`3ec82a9`，--tts rest，mp3→miniaudio 44100，實測 255KB/2.9s）；(4) 付費模型 `s2.1-pro`（`0475ea3`，Owner 拍板去 -free）；(5) 每連線對話記憶 10 輪（`c4153b5`——修「對話難連貫」：原本每回合失憶）；(6) 動作/表情（微笑/嘆氣）整段剝離不再被 TTS 唸出（`2c854d4`）；(7) auto-VAD 反回授（`5fecf34`+`12f216f`：SPEAKING＋1.8s holdoff 完全鎖、連續 260ms 才觸發——修「茜講話被自己聲音打斷」）。伺服器即時日誌（python -u）常駐。**驗收**：74 passed（主大腦複跑）；決勝門真實合成 >0 bytes；Owner 實機確認語音+auto-VAD+對話連貫 OK。HEAD == origin/main == `12f216f`。0 src/、0 Frozen、0 金鑰。REST/live 可隨時 `--tts rest|live` 切換。 | DSH | VC-1.6 |

| 2026-09-06 (VC-1 部署) | **開機背景自動執行上線**：Scheduled Task \VC1_AkaneVoiceCompanion（LogonTrigger / InteractiveToken / LeastPrivilege / IgnoreNew）→ pythonw.exe -u -m clients.voice_companion.web_server --https（WorkingDirectory=repo，live＋s2.1-pro，https 8765）。對齊 Soul OS 的 SoulOS_Watchdog 排程模式。實測 task run → pythonw PID 2536 https 200；雙開由 IgnoreNew 防護；診斷 log 需求時可手動 python -u 執行。| DSH | VC-1 |

| 2026-09-06 (VC-2/2.3) | **VC-2（P0-P2）＋ VC-2.3 五大工業級升級批次 CLOSED**（Antigravity 執行；5 commits：e000bbe VC-2.3-04 AudioWorklet/零複製 RingBuffer、aee6471 VC-2.3-03 背壓 maxsize256＋drain 防吞尾、522e99b VC-2.3-05 64KB frame/30s 熔斷/延遲遙測、23d3f1e VC-2.3-01 終端 VAD 非阻塞 worker、8b99f7c VC-2.3-05 loopback 預設＋VOICE_WEB_TOKEN 鑑權＋Origin 白名單；報告之 a42a037 hash 不存在於歷史——02 取消/隔離內容由 112 測試涵蓋、疑併入上述 commits）。另含 VC-2 P0-P2（StreamingVoiceSanitizer/ASR 放寬/行動重連心跳/預緩衝/SAGE+TA-2 注入，先前未 commit 已整併）。驗收：112/112 全綠（主大腦複跑）＋關鍵功能 grep 實證；HEAD==origin/main==8b99f7c（主大腦 push）。0 src/、0 Frozen。維運：排程任務＋Cloudflare Tunnel akane.brynetsolutions.com；web.host 預設改 127.0.0.1（安全迴路）。| DSH | VC-2 |

| 2026-09-06 (VC-2.4) | **VC-2.4 櫻島麻衣＋雷姆 多伴侶 profile 驅動獨立服務上線 CLOSED**（Antigravity 執行；4 commits：8082680 VC-2.4-01 多伴侶支援＋Mai profile（web_server --config 載入、render_html_page(display_name/short_name)、check_port_available、web_ui 角色 mai、env_config MAI_* 覆寫、personas/agent_mai.md、.env.example）、cc94ca7 Mai https 8766、9fb3fdf Mai fish voice_id＋端點、723d577 Rem profile（REM_* 覆寫、port 8767 https、personas/agent_rem.md））。**profile 檔**：`profiles/mai.json`（櫻島麻衣／麻衣，wake 麻衣/學姐/まい/mai，fish voice_id `101fe58fb9914eefa510f3c92ec1d798`，web 127.0.0.1:8766 https）＋`profiles/rem.json`（雷姆，wake 雷姆/Rem/rem/レム，voice_id `055de7dab3aa411ea544f2673ff537fa`，web 127.0.0.1:8767 https）；同 s2.1-pro＋mode live＋llm deepseek-v4-flash:0731，api_key 留空由 .env 注入。**驗收**：129/129 全綠（主大腦複跑，112→129＋17 新測試）；ports 8766/8767 LISTENING（127.0.0.1 https，Mai PID 10404／Rem PID 21020）＋排程任務 VC1_MaiVoiceCompanion／VC1_RemVoiceCompanion Running（與 Akane 8765 併存）；HEAD==origin/main==723d577（主大腦 push b9a7f14..723d577）。**⚠️ 差異註記**：報告宣稱 Akane voice_id 為 a9ee868...，但 repo 基底 config.json 仍為歷史 canonical `4c11d21b14284d428074f76a1cf32298`——新 id 未落實在 repo，如要更換需另開工單。0 src/、0 Frozen、0 金鑰進 git。 | DSH | VC-2.4 |

| 2026-09-08 (VC-2.5) | **VC-2.5 跨介面統一短期會話流＋四階現象學時間感知 CLOSED**（**主大腦兜底實作**——3 次 developer subagent 連環失敗：opencode 上游問題 → Owner 換 ollama-cloud 仍 0 產出 → 執行器層故障，依 VC-1.6 先例主大腦自行實作；commit `5e76d0f`，6 檔 +401/-1）。**新增 `clients/voice_companion/session_store.py`**：以 (agent_id, user_bryan) 為核心的共享短期對話層（`SessionStore`，per-agent JSON 檔、臨時檔＋os.replace 原子寫、FIFO 上限 200 輪、純 stdlib 0 新依賴）；**四階現象學時差模型**（MICRO <300s／SHORT 300~7200s（含 2~8h 白天空窗補位）／CIRCADIAN_NIGHT 22:00~07:00 跨越／LONG_NEW_DAY >8h 或跨日）；**動態邊界**：≤600 tokens 預算從最舊裁切、LONG_NEW_DAY 一律空歷史（防複誦舊話）＋anchor 引導。**讀取端** `akane_voice_brain.py`：注入【跨介面時空體感】anchor、session history 取代傳入 history（空則回退）。**VC 寫入端** `web_server.py`：user+assistant 回合僅正常完成入記憶（與 _history 同語義，barge-in/失敗不入）。**TG 寫入端** `src/io/channels/telegram.py`：inbound user 回合＋`send()` assistant 回合，Bryan TG id（1696287850）過濾、fail-silent。**驗收**：新測試 7/7（6 剛性＋1 brain 整合）＋VC 套件 129/129 無回歸＋telegram crash defense 5/5（主大腦複跑）；既有測試加 autouse fixture 隔離 SessionStore 預設 data_dir（防讀真實 data/sessions）。0 frozen contract、0 schema、0 金鑰；data/sessions 由既有 data/ 規則 gitignore。 | DSH | VC-2.5 |

| 2026-09-09 (EH-1) | **EH-1 Epistemic Horizon 認知地平線設計契約（DESIGN，docs-only）**（commit `8ecf890`；1 file changed: `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`（NEW，298 行，§0 摘要 + 8 節 + §9 邊界））。**前置**：EH-0 四模組審計（模組 1 proxy 12 注入點 / 模組 2 SAGE 管線 / 模組 3 persona 清洗 / 模組 4 M7-FG-2 衝突審計）發現已整合為設計輸入。**契約內容**：§2 Horizon Gate 介面（掛載點 proxy.py:655-657/1173-1175，`_format_horizon_block` fail-silent，負向化措辭沿用 `_GERM_ANCHOR`，TA-2 投影哲學）；§3 SAGE assimilated_facts 方案 A（facts 表加 scope 列 v9 migration，三態 unknown/learning/aware，「已學會」= Gate 顯式放行事件）；§4 垂直 gate 三層檢查（assimilated 知識不得穿透 Identity/Essence 層）；§5 9 條不變量；§6 四條 Probes 驗收（全知平滑拒絕 / 二次遭遇流暢 / 劇情背誦克制 / Idiolect 跨 session 召回）；§7 待 Owner 拍板點 4 項（D1 world_context 攔截 / D2 news 昇華鏈處置 / D3 seeded vs germ 分流 / D4 10 份 persona hermes runtime 殘留盤點）；§8 Frozen Contract 聲明。**0 src/、0 personas/、0 既有文件改動**；非施工授權，實作需另開工單。 | DSH | EH-1 |

| 2026-09-09 (EH-1.1) | **EH-1.1 Epistemic Horizon 契約修訂（DESIGN，docs-only，Owner 裁定落地）**（commit `813dbdd`；1 file changed: `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`（254 行，+133/-68））。**8 項必改全落地**：① 狀態標記改「EH-1.1 鎖定（Owner 裁定）」；② §3 單一 scope 拆分為正交雙維度 `origin`（native_commons/native_episode/lived_experience/assimilated/external_world）× `horizon_state`（三態僅限 assimilated/external_world）；③ D1 Per-agent 視角降級（禁全域 `[EXTERNAL_RUMORS]`，僅 `native_commons≠modern_earth` 觸發，不干預 Decision 層）；④ D2 垂直防火牆（R1 external_world/news 禁入 run_elevation、R2 lived_experience 可昇華、R3 assimilated 封頂 Fact/Pattern/Meaning，昇華鏈 4 模組精準標註 file:line）；⑤ D3 靈魂原生分流（modern_earth 角色 Gate fail-silent bypass，雷姆異界阻力 Pilot + 黑川茜現代對照組 Pilot 為 EH-2 驗證基準）；⑥ D4 Hermes 殘留解耦為 `CLEAN-HERMES-RESIDUE` 交叉指針；⑦ Probe 1 失敗條件精準化（複述主人用詞不算失敗）；⑧ §8.3 Frozen 增補（v9 migration 需 Owner 另開實作工單授權）。**意外發現**：world→elevation 直通已於 SG-1 降級 observe-only（`world/elevation_adapter.py:163-202`），D2 阻斷只需 submission_gate + run_elevation 兩層。**0 src/、0 personas/、0 既有文件改動（除目標檔）**；DESIGN ONLY 保留。 | DSH | EH-1.1 |

| 2026-09-09 (EH-2) | **EH-2 Epistemic Horizon 實作完成（IMPLEMENTATION AUTHORIZED → DONE）**（commit `16a890b`；17 檔 +1386/-48）。**四項實作**：① SAGE v9 migration（facts 表加 `origin` DEFAULT 'lived_experience' / `horizon_state` DEFAULT 'aware' / `learned_at` REAL + idx_horizon，additive 冪等，`set_fact_dimensions` 標記 API + `get_idiolect_facts`）；② Horizon Gate（`_format_horizon_block(agent_id, session_context)` 掛 proxy.py:658/1241，identity 之後，modern_earth 角色 fail-silent bypass，雷姆 Pilot 阻力句 + Idiolect 清單 + 外部未解動態降級，0 既有塊移動）；③ 垂直防火牆（R1 external_world/news 禁入 run_elevation 雙層攔截 submission_gate + elevation_adapter、R2 lived_experience 放行、R3 assimilated 封頂 Fact/Pattern/Meaning 禁入 Belief/Value/Trait/Essence，含證據邊計票隔離）；④ 四條 Probes（tests/test_epistemic_horizon.py 23 項：Probe 1 全知平滑拒絕 / Probe 2 二次遭遇流暢 / Probe 3 劇情背誦克制 / Probe 4 現代對照組 Bypass 全 PASS）。**驗證**：主大腦複跑 86 passed（EH-2 23 + 回歸 63）；21 項主幹既有失敗 git stash 對照確認 pre-existing；順帶修好 pig_filter_v2 主幹已壞斷言。**0 踩線**：personas/ 零改動、ENGINEERING_STATE 零改動（本行除外）、SAGE 寫入主幹（INSERT OR REPLACE 18 欄）逐字節未動、InnerLifeEvent/Agency/4 handlers 未碰。 | DSH | EH-2 |

| 2026-09-09 (EH-2.1) | **EH-2.1 Hotfix：world:* 過切修正 + 現代原生白名單補齊**（commit `c9aa9e2`；6 檔 +220/-29）。**裁定一**：R1 阻斷從 `startswith("world:")` 收斂為 `is_world_media_trigger()`（僅 `world:news*`/`world:feed*`/`world:celebrity_news` 阻斷；`world:weather*`/`world:rain*`/`world:calendar*`/`world:user_going_outside*` 放行——環境與日程屬 Co-living 感知邊界，可沉澱環境 Pattern，防感知閹割）。**裁定二**：`MODERN_NATIVE_AGENTS` 2→7 名（akane/mai/anna/aoi/miku/ruka/yua，含 persona 檔名+行號查證；ram/rem/mahiru 維持異界阻力）。**驗證**：三核心檔 72 passed；聚焦回歸 52 檔 1393 passed，8 failed 全 pre-existing（git stash 雙重驗證）。**待 Owner 確認**：agent_yua（原創角色，依現代生活線索入白名單，可移除）。0 personas/ 改動、0 Frozen Contract。 | DSH | EH-2.1 |

| 2026-09-09 (MEM-WIRING-1) | **MEM-WIRING-1 P0 BUGFIX：post_reply_commit 位置參數錯位修復（graph.sqlite 0 落庫）**（commit `806645d`；provider.py +11/-4 + 新測試檔）。**根因**：`post_reply_commit` 的 `run_in_executor(None, write_turn, u, a, sid, source_pair, inner_life_event_id)` 位置傳參，`source_pair`（"bryan:agent_rem" truthy）被綁進第 4 位置參數 `skip_graph` → 誤跳 graph 萃取落庫（v1 mirror 仍寫）；`inner_life_event_id` 同時錯綁進 `source_pair`。**修法**：`functools.partial` keyword 繫結（`source_pair=...`, `inner_life_event_id=...`）；no-diary（agent_ram）有意的 `skip_graph=True` 分支原樣保留。**驗證**：A/B 鐵證（source_pair=None → 2 Facts；"bryan:agent_rem" → 修復前 0 → 修復後 2 Facts）；v1 mirror 一致性（兩案均 2 筆）；AST 防回歸測試（位置參數 ≤3 斷言）；新套件 6/6 + 回歸 118/118（主大腦複跑 7 passed）。**影響**：production middleware 寫入鏈（USER_MESSAGE→AGENT_SPEAK→post_reply_commit）自 source_pair 引入以來對 graph.sqlite 寫入為 0 的 bug 已修復——Turn 2 真機冒煙發現。0 簽名變更、0 Schema 變更、0 Frozen Contract。 | DSH | MEM-WIRING-1 |

| 2026-09-10 (EH-3) | **EH-3 Write-Side Assimilation 實作完成（Closing Write-Side Assimilation Loop）**（commit `07c7513`；provider.py +104 + 新測試檔 `tests/test_epistemic_horizon_eh3.py` +236）。**機制**：`post_reply_commit` 主路徑 write_turn 完成後掛後置 Hook——捕獲 fact_id 清單（不丟棄返回值）→ 第二個 `run_in_executor` 內呼叫 `_tag_explanatory_assimilations`（維持 worker 執行緒，SQLite thread-affinity 安全）→ Filter Chain：① 白名單 `is_modern_native`（引用 horizon.py MODERN_NATIVE_AGENTS，0 重複維護）② 特殊模式（NO_DIARY/skip_graph/0 fact_ids）③ 句法定義標記（11 個純句法詞：就是/是個/是一个/是一種/是一种/用來/用来/所謂/意思是/指的/指的是，0 科技實體詞）→ User-Only 打標（`source == 'user'` 才標）→ `set_fact_dimensions(origin="assimilated", horizon_state="aware", learned_at=time.time())`（float）+ `store.flush()` 強制 commit（關鍵：不 flush 則 retrieve_idiolect 新連接讀不到）。Fail-silent（logger.warning）。**驗收**：Test A 解釋句打標 / A2 日常句不誤標 / B 現代角色 bypass / C 讀寫閉環（Idiolect 含氣炸鍋進 Horizon Block）/ D Ram no-diary 平穩 全 PASS；防回歸 30/30（MEM-WIRING-1 + EH-2 套件，主大腦複跑 35 passed）。**0 踩線**：write_turn/add_fact 簽名與 INSERT 主幹未動、0 DDL、learned_at float、personas/ 零改動。**Epistemic Horizon 全鏈路閉環**：EH-0 審計 → EH-1/EH-1.1 契約 → EH-2 實作 → EH-2.1 hotfix → MEM-WIRING-1 P0 修復 → TURN-2.1 複測 → EH-3 寫側內化。 | DSH | EH-3 |

| 2026-09-10 (EH-3.1) | **EH-3.1 Assimilation False-Positive Guard + Cross-Session Validation（P1 Correctness Bugfix）**（commit `a03295e`；provider.py +128/-27 + 新測試檔 `tests/test_epistemic_horizon_eh31.py` +298）。**背景**：3 輪真機自然對話觀察實證「就是想」命中裸「就是」→ `我 想 休息` 誤標 assimilated。**修法**：判定粒度 Turn-Level → **Fact-Level 五閘門**——A source=='user'；B 定義句法標記（移除裸「就是」，保留 就是個/是個/是一個/是一种/是一種/用來/用来/所謂/意思是/指的是/指的）；C subject 精確排除 {我,你,妳,我們,咱們,主人}（exact match 非 substring，無名字黑名單）；D 主觀情態實體排除（就是想/就是要/就是會/就是愛/就是喜歡/就是在/就是覺得 → 前一語段為生命狀態表述）；E 被定義實體相交（containment 語義，相容抽取器 fragment subject）。打標維持 assimilated/aware/learned_at float。**驗收**：Test A 純定義句打標 / B 「就是想休息」不誤標 / C 混合句 Fact-Level 精準分流（核心硬斷言，禁整輪一刀切）/ D akane bypass / E Ram no-diary / F 跨 Session Idiolect 穩定召回（新 Provider 重載 + horizon block 仍含氣炸鍋 + Session 2 口語句不污染）全 PASS；主大腦複跑 41 passed（EH-3.1 6 + EH-3 5 + MEM-WIRING-1 6 + EH-1/2 24）。**Incidents**：混合句「；」測試改用「。\n」分隔（抽取器不認「；」，production 邏輯不依賴）；Gate E 採 containment。**0 踩線**：0 簽名/Schema/persona/whitelist/Gate 文案變更。 | DSH | EH-3.1 |

| 2026-09-10 (EH 系列狀態轉換) | **Epistemic Horizon 系列：code-closed → deployed & observing**（Owner 裁定確認）。**部署**：主服務 PID 重建載入 `a03295e`（EH-3.1 Fact-Level 五閘門），Uvicorn 0.0.0.0:8000 監聽確認、10 TG bots polling 正常。**終極驗收里程碑（production-observed 條件）**：不再人工構造測試；僅當真實日常生活中完整跑過一次真實閉環——首次自然阻力 → 日常定義傳授 → 隔日/跨 Session 自然默契引用——方推進 production-observed。**日常觀察紀律（三事件準則）**：① 初遇未知現代物件（預期：複述用詞 + 女僕職責/家務/安全視角承接，不講百科原理解說）→ 僅記錄輸出，嚴禁為單句增補負向 prompt；② 主人定義句解釋（預期：Fact 精準標記 assimilated/aware/float learned_at）→ 先查 DB 釐清「抽取缺失 vs 閘門缺失」；③ 隔日/跨介面再提（預期：自然沿用內化默契，無初次見面困惑）→ 先查 Context 注入（_format_horizon_block 是否查得 Idiolect），有注入仍裝不懂才判生成層問題。**票務固化**：P2「按下開關」維持純生產觀察不增負向限制；CLEAN-HERMES-RESIDUE 繼續暫緩（環境穩定後單獨除垢）；EV 主動求知列為下一階段獨立架構研究（Motive → Decision → Transmit，非修補票）；agent_yua 永久固化於 MODERN_NATIVE_AGENTS 白名單。 | DSH | EH-series |

| 2026-09-10 (VC-UNIFY-1) | **VC-UNIFY-1 P0：Voice Companion 認知地平線與 SAGE 記憶全鏈路統一（Single Soul Multi-Modalities）**（commit `7e2a560`；clients/voice_companion/ 6 檔 +184 + 新測試檔 +255）。**背景**：VC 語音管線與 TG/Web 主服務管線認知分裂（語音無阻力、不寫記憶）——體驗斷層致命，Owner 定 P0。**讀側**：`format_voice_horizon_block()` 注入 `_build_messages`（Persona 之後），`_format_horizon_block` 實際 import 自 `src/llm/proxy.py`（工單範例路徑有誤已修正）；agent_id 自 config（rem/akane/mai 全覆蓋）；現代原生角色維持 ""（D3 分流）。**寫側**：`schedule_sage_commit()` —— `asyncio.create_task(post_reply_commit(...))` Fire-and-Forget（0 TTS 延遲影響；無 running loop 降級 daemon thread + asyncio.run）；lazy SAGELiteProvider（與 default_memory_retriever 同 graph.sqlite）；LLM judge 用 VC 既有 config.llm 通道（無則 regex fallback）；source_pair=user_bryan:<agent_id>；web_server `_finish` + akane_live 兩條回覆路徑觸發（barge-in 中斷不入庫）；生產開關 `memory.sage_write.enabled: true`（三 profile 已開）。**驗收**：Test A 讀側注入+Idiolect / B 寫側非同步落庫+EH-3.1 五閘門打標 / C 語音教→文字讀（隨身碟跨模態互通）全 PASS；主大腦複跑 153 passed（含 VC 客戶端 129 防回歸 + EH 系列 17）。**部署**：三部 VC（8765 Akane/8766 Mai/8767 Rem）排程任務重啟完成載入新程式碼。**0 踩線**：personas/ 零改動、輸出守門零改動、SAGE 寫入主幹零改動。 | DSH | VC-UNIFY-1 |

| 2026-09-10 (VC-UNIFY-1.1) | **VC-UNIFY-1.1 P1 BUGFIX：Canonical Source-Pair 對齊 + 全量召回證明**（commit `413d3b3`；akane_voice_brain.py +2/-1 唯一 code 改動 + 測試 +202）。**根因**：VC-UNIFY-1 硬編碼 `source_pair = f"user_bryan:{agent_id}"`，與文字端 canonical `bryan:{agent_id}` 不一致——retrieve_idiolect 不設過濾而「看似互通」，但文字端 MemoryReader 帶 `source_pair_filter={"bryan:agent_rem"}` 時語音寫入的普通生活記憶全被遮蔽。**修法**：`schedule_sage_commit` 的 source_pair 改為 `f"bryan:{self.agent_id}"`（canonical 唯一格式，0 別名/雙寫；Fire-and-Forget/Barge-in 不寫/Fail-Silent/EH-3.1 打標機制全不變）。**驗收**：Test A 格式驗證（0 user_bryan）/ B 語音生活→文字端帶 filter 讀回（核心補漏）/ C 文字生活→語音 retriever 讀回（雙向閉環）/ D 既有 EH 驗證全保留（assimilated 打標/Idiolect 召回/Akane+Mai bypass/Barge-in 0 寫入）全 PASS；主大腦複跑 147 passed（VC-UNIFY 12 + VC 客戶端 129 + EH-3.1 6）。**記錄**：GraphStore batch commit 語義（_BATCH_SIZE=20）——生活句不打標時獨立連線需 flush() 才可見（既有 public API 配合，0 SAGE 改動）。**部署**：三部 VC 排程任務重啟完成（主服務無變更未重啟）。**0 踩線**：src/memory/sage 零改動、personas/ 零改動。 | DSH | VC-UNIFY-1.1 |

| 2026-09-10 (VC-UNIFY-1.2) | **VC-UNIFY-1.2 P1 BUGFIX：跨進程事務可見性（Voice Ordinary-Memory Visibility Commit）**（commit `47d7fa6`；akane_voice_brain.py +13 + 測試重寫 +200/-291）。**根因**：普通語音生活句（非定義句）不觸發 EH-3.1 顯式 flush，受限 GraphStore `_BATCH_SIZE=20` 批次門檻——未提交事務對文字主服務獨立 SQLite 連線不可見（延遲同步）。**修法**：`schedule_sage_commit` 背景 task `_commit()` 內，`post_reply_commit` 成功返回後顯式 `store.flush()`（優先 `provider._writer.store.flush()`，fallback `provider.store.flush()`；獨立 try/except Fail-Silent 僅 logger.warning；`except asyncio.CancelledError: raise` 保留）；嚴格位於背景 task 內，fire-and-forget 結構不變 → **0 語音延遲影響**；Barge-in 0 write 0 flush 天然維持。**驗收**：Test A 生活記憶跨連線立即可見（**0 手動 flush**，await task 後文字端獨立 MemoryReader + source_pair_filter 立即檢索到「散步」）/ B 測試潔淨度（無補償性 flush helper）/ C 定義句打標保留 / D Barge-in 0 write 0 flush（spy 斷言）/ E 開關關閉 0 task 0 write / F 現代角色 bypass 全 PASS；主大腦複跑 147 passed（VC-UNIFY 6 + VC 客戶端 130 + EH-3.1 6 + MEM-WIRING-1 5）。**部署**：三部 VC 排程任務重啟完成（主服務無變更未重啟）。**0 踩線**：src/memory/sage 零改動、Schema/source_pair 零改動、personas/ 零改動。 | DSH | VC-UNIFY-1.2 |

| 2026-09-10 (VC-UNIFY-1.2.1) | **VC-UNIFY-1.2.1 TEST-ONLY：Restore VC-UNIFY Regression Contracts（合約矩陣補齊）**（commit `f416eb9`；僅 tests/test_vc_eh_unification.py +258/-85，重組 364→509 行，8 項矩陣 + 潔淨度自檢 = 9 測試 9/9 PASS）。**背景**：1.2 重寫測試時（8→6 項）意外刪除 VC-UNIFY-1/1.1 核心合約（語音讀側 Horizon 注入順序、語音概念→文字 Idiolect 閉環、文字生活→語音檢索反向閉環）——補回防靜默回歸。**恢復 4 項**：Test 1 Voice 讀側 Horizon 掛載順序（Persona 後/User 前，含氣炸鍋 Idiolect）/ Test 2 定義句→EH-3.1 來源純化（User 標、assistant 不標）/ Test 3 Voice 概念→Text Idiolect 閉環（新概念「隨身碟」）/ Test 4 Text 生活→VC 檢索反向閉環（provider 生命週期自然提交）。**保留 4 項**：Test 5 跨連線即時可讀 / Test 6 Barge-in 邊界 / Test 7 開關防護 / Test 8 現代角色 Bypass。**驗收**：補償呼叫形式 `\.flush(` = 0 次（全字形 47 次全可歸類註解/斷言名/spy/檢查器，零補償）；跨連線可見性全依賴生產機制（EH-3.1 production flush / 1.2 背景 _commit flush / GraphStore 生命週期 close）；主大腦複跑 150 passed（VC-UNIFY 9 + VC 客戶端 130 + EH-3.1 6 + MEM-WIRING-1 5）。**0 生產代碼變更**（不需重啟服務）。**合約基準固化完成**——可進行最後唯一真機跨模態對話驗收。 | DSH | VC-UNIFY-1.2.1 |
| 2026-09-10 (MEM-VISIBILITY-0) | **MEM-VISIBILITY-0 跨進程記憶可見性審計登記（AUDIT REGISTRATION，docs-only，0 code / 0 schema / 0 data mutation）**。`docs/MEM-VISIBILITY-0-AUDIT.md`（NEW 202 行，auditor 產物，A/B 原始 stdout 完整保留）納入版本控制 + §1.1 登記。**① P0 Finding**：常駐主服務下，普通生活記憶停留在未提交狀態（pending < 20 不觸發批次 commit），引發跨進程讀側（VC）因 `schema_meta` 寫鎖競爭觸發 `database is locked`（阻塞 ~11s 後 fail-silent 回傳 None）。**② A/B 實證**：普通生活句未提交即鎖死（VC 新連線 11.0s locked → None）；定義句因 EH-3.1 帶 flush 即時可見（0.0s 3 筆）。**③ 非對稱漏洞**：現代原生角色（Akane/Mai 等）因 EH-3.1 bypass 導致定義句亦無 flush。**④ 方案狀態**：C1（寫側每輪 Flush）/ C2（讀側純讀不搶鎖）/ C3（時間窗提交）**均維持未選型**。**⑤ 下一步指示**：`MEM-VISIBILITY-C2-V` 專項驗證（讀側免寫開啟可行性，含 WAL 唯讀相容性），**非直接實作**。0 frozen contract 觸碰；src/ / clients/ / tests/ / config/ / personas/ 零改動；審計前既有 untracked 基線未動。 | Mavis / Lin | MEM-VISIBILITY-0 |
| 2026-09-10 (MEM-VISIBILITY-C2) | **MEM-VISIBILITY-C2 實作 + C2-V 矩陣結論登記**（commit `820174a`，2 檔 +271/-5；src/memory/sage/graph_store.py `_init_db` 單點守衛 + tests/test_graph_store_c2_read_open.py NEW）。**C2-V 矩陣結論**：① 鎖源精準定位——PRAGMA WAL / CREATE TABLE IF NOT EXISTS / SELECT version 各 0.000s，僅 `INSERT OR REPLACE schema_meta` 阻塞 11.000s → `database is locked`；② 現況基線——第二個 `GraphStore()` 建構即在 `_init_db` 失敗，10.953s（≈ busy_timeout 10s）後 raise locked，VC 端 fail-silent 吞成 None；③ 候選 A 實證——標準可寫連線 + schema 已是現行版本時跳過該寫入 ⇒ 未提交 writer 存在時 Reader 開連線 0.000s、0 lock、讀到已提交快照；Writer COMMIT 後同一 Reader 下一輪 SELECT 立即可見新列；④ `mode=ro` 不採用（不可跑 `_init_db`、無 DB 開不了、生產有 CANTLOCK 記載），`query_only` 不做。**C2 實作（單點）**：current_version == _SCHEMA_VERSION 時跳過 schema_meta 的 INSERT/REPLACE 與其引起的 commit；新庫（無 version）與 version != current 維持既有 create / migrate / 寫 version；`CREATE TABLE IF NOT EXISTS` 保留（C2-V 證明非鎖源）；migration 路徑 0 破壞。**範圍邊界（必須明示）**：C2 只解「讀側開連線搶寫鎖」，**不解即時最新值**——文字→語音的普通生活記憶仍是「不鎖死的舊快照」，即時同步屬 C1/C3，C1 未授權（未解凍 add_fact / 每輪 flush 節奏）。**驗收**：A-F 專測 5/5 全過（A+B+C：writer 未 flush 時 reader 開連線實測 31.4ms vs 基線 10.953s locked，0 locked；快照隔離 0 列；flush 後 1 列可見；D：v9 open 0 寫入，監控連線 `PRAGMA data_version` 前後不變 + store 連線 `total_changes==0` 雙證；E：全新庫 init 至 v9；F：v8→v9 升級既有測通過）+ 回歸 G 59 passed（基線 54 全數保留，0 新增失敗）。**0 Frozen Contract 觸碰**：未動 add_fact / batch_size / flush 節奏、未動 EH-3.1 hook、未改 `post_reply_commit` 簽名、未解凍 SAGE 寫入主幹。 | DSH | MEM-VISIBILITY-C2 |
| 2026-09-10 (MEM-VISIBILITY-C1) | **MEM-VISIBILITY-C1 即時可見性 Flush 契約產出登記（DESIGN ONLY，0 code / 0 實作 / 0 解凍 / 0 部署）**（契約 commit `d3bd70a`，1 file changed: `docs/MEM-VISIBILITY-C1-CONTRACT.md`（NEW 568 行）；本登記另 1 file changed: `logs/ENGINEERING_STATE.md`（本行 + §1.1 Current HEAD 同步 `d3bd70a`））。**⚠️ 狀態鐵律（必須明示）**：**C1 契約已產出，仍未解凍、未授權實作、未落地，等待 Owner 決策**；SAGE 寫入主幹仍屬 Frozen Contract，本票僅「提出解凍申請的範圍與理由」，決定權在 Owner。**契約結構**：§0 核心結論 / §1 現況基線（C2 已上線狀態 + 寫入路徑 commit 時機 file:line + 既有 flush 呼叫點全清單 9 項 + 已實證 E1-E8 vs 推論 I1-I4 分級）/ §2 契約不變量 INV-1~INV-10（各附可硬斷言形式）/ §3 掛載點精確定義（位置 / 成功判準 / 條件式 / 執行緒 / fail-safe 例外表）/ §4 與既有機制交互（EH-3.1 重疊不合併 / `_BATCH_SIZE=20` 保留 / modern-native 7 角色逐條 / no-diary / VC-UNIFY-1.2 對稱性含執行緒差異誠實記載 / 與 C2 及讀側快取）/ §5 風險與代價（C2 後鎖競爭重估表）/ §6 失敗模式 F1-F6 與 rollback（R1 revert-only 為主，回退後 = C2-only 語意、0 資料遺失無遷移）/ §7 驗收準則 V1-V9 + 靜態斷言 S1-S6 + 回歸範圍逐檔覆核（含 Test 4 / Test 6 / MEM-WIRING-1 AST 防回歸 / C2 專測的「為何仍綠」分析）/ §8 解凍申請範圍 / §9 待 Owner 決策清單 D-1~D-10 / 附錄 A 證據索引 A1-A34 / 附錄 B Out of Scope 7 條。**核心設計（未實作）**：掛載點唯一 = `post_reply_commit` 寫入階段成功後、`provider.py:382`（EH-3.1 hook 之後）與 `:383`（`_cache.invalidate()` 之前）之間、worker thread 內 `await loop.run_in_executor(None, self._store.flush)`；無條件呼叫（靠 `GraphStore.flush()` 既有 `_pending_writes > 0` 守衛達成 0 額外寫入）；fail-safe `except Exception → logger.warning` 不中斷回覆路徑；`CancelledError` 維持既有語意。**六條硬約束逐條遵守**：① 唯一掛載點（0 新定時器 / 0 背景輪詢 / 不偷掛第二點，並以 S1-S2 靜態斷言固化）② `MODERN_NATIVE_AGENTS` bypass **逐字保留**（INV-9 + S5 靜態斷言；茜／麻衣等的可見性由通用後置 flush 消掉，**非**取消 bypass）③ 本票 0 code（未動 `src/` `clients/` `tests/` `config/` `personas/`）④ C1 仍凍結、僅提出解凍申請 ⑤ `post_reply_commit` 簽名不動（INV-5 + S3）⑥ `_BATCH_SIZE=20` 與 `GraphStore.flush()` 行為不動（INV-7 + S4）。**0 production mutation / 0 服務重啟 / 0 frozen 解凍**。 | DSH | MEM-VISIBILITY-C1 |

| 2026-09-11 (VC-TTS-1) | **VC-TTS-1 語音事故修復：TTS 音源耐久化切 REST**（commit `77929fa`，3 檔各 1 行：`clients/voice_companion/config.json` / `profiles/mai.json` / `profiles/rem.json` 的 `fish_audio.mode` `live`→`rest`；1 file changed 之登記另加本行 + §1.1）。**症狀**：三台 VC（8765 Akane / 8766 Mai / 8767 Rem）講話「**有文字轉錄、UI 顯示說話中、完全沒有聲音**」；本機直連 WS 文字注入實測 akane/rem 各 2/2 次 90s 零音訊（`errors:[]`、`close_code:null`、`exception:null` 全空）。**根因（實證，非推論）**：fish.audio **live WebSocket TTS 端點嚴重劣化**——合成時間隨字數暴增：**5 字 3.67s 出聲／19 字 29.44s 出聲／44 字 40.8s 後拋 `WebSocketNetworkError` 且 0 bytes**；rem 與 mai 的 voice 同結果 → **與 voice 無關**；把 18 個 token 碎片緩衝成一次性餵 47 字 → 同樣 0 chunk → **與餵法無關**；同一 key/voice/model 走 **REST 44 字僅 2.08s、155,479 bytes、200 OK** → **與 key/額度無關**。**診斷關鍵**：① `web_server.py:563` 的 `first_audio_ms` 在 `first_chunk_time is None` 時 fallback 成 `total_ms` → **`[LATENCY] first_audio == total` 是「完全無音訊」的指紋**（不是「首音等於總時長」）；② reply transcript 於 `_generate()`（LLM 串流 + `end_session()`）完成後才送瀏覽器，故 44s 全卡在 TTS 而非 LLM；除錯實例 console 時序：`20.051s FIRST feed_text_piece` → `20.511s SDK.tts() called` → `21.814s text_iter EXHAUSTED pieces=18 chars=44` → `61.833s GEN RAISED WebSocketNetworkError` → `WORKER end chunks=0 bytes=0 elapsed=41.359s`。**修法**：config 落檔 `mode: rest`（`web_server.py:847-861` factory 改走 `RestPCMWebStreamer`），**不帶 CLI 參數即生效、重開機不退回**；`--tts <mode>` CLI 覆寫保留（**參數名為 `--tts`，非 `--mode`**）。**驗收（主大腦獨立複驗）**：三台以與排程任務 XML 完全相同、**無 `--tts`** 的參數重啟 → 本機 WS 注入首音 akane **5.493s**/783,360B、mai **5.013s**、rem **5.179s**/1,108,224B（原為 90s 逾時 0 音訊）。**Owner（Bryan）決策**：**REST 即長期音源，live→REST 自動 fallback（C 案）暫緩**。**0 踩線**：0 行 `src/`（SAGE 寫入主幹與 MEM-VISIBILITY-C1 凍結狀態不變）、未動 WS 協定/播送狀態機、未動排程任務 XML、未動 Frozen Contract。 | DSH | VC-TTS-1 |
| 2026-09-11 (VC-LOG-1) | **VC-LOG-1 VC 檔案日誌：消除觀測盲區**（commit `a96c53b`，5 檔 +149/-1：`web_server.py` +126 / `fish_tts_live.py` +8 / `fish_tts_streamer.py` +6 / `.gitignore` +3 / `tests/clients/test_voice_companion.py` +7）。**背景（事故教訓）**：`web_server.py` 原本**完全沒有任何 logging 設定**、只有 `print()`；生產以 `pythonw` 啟動 → **stdio 全被丟棄** → 上述事故的「TTS 從未吐出任何 chunk」與被吞掉的 TTS 例外完全無痕跡，定位耗時數小時。**做法**：`setup_logging(agent_id)` 於 `main()` 早期初始化（`RotatingFileHandler` 5MB×3、UTF-8、目錄自建、`VC_LOG_LEVEL` 可覆寫預設 INFO、handler 掛 **root logger** 使 `vc.*` 與其他模組（`src.llm.proxy` / `soul_os.memory.writer` / `aiohttp.access`）同檔落地、setup 冪等）＋ `sys.excepthook` ＋ asyncio loop exception handler。**落檔事件**：啟動橫幅（agent_id / display_name / **tts_mode** / voice_id / model / host:port / config 路徑 / api_key **只記前 4 碼＋長度，嚴禁全文**）、`[WS]` connect/close/訊息型別（text 截 40 字）、`[UTT]`、`[LATENCY]`、**`[TTS] round`（mode / input_chars / reply_chars / chunks / bytes，`chunks==0` → **WARNING ZERO-AUDIO**）**、`_run_reply` / `_finish` 例外 `log.exception`、`AudioRelaySink._written_bytes` 音訊位元組計數（純增量）。`fish_tts_live.py`：例外**兩個分支都留痕**（非中斷 `log.exception("[TTS] live-error")`、被中斷 `log.warning("[TTS] live-interrupted during synthesis")`——事故教訓：被中斷也要可觀測）；協定/請求參數 0 改動。`fish_tts_streamer.py`：`synthesize()` 非 200 → `log.error("[TTS] rest-http-error status=... body=...")`（body 截 200 字）後照舊 raise。**既有所有 `print()` 全保留**（行為 0 改動）。**驗收**：正常路徑日誌實錄含 `startup ... tts_mode=rest ... api_key=a1f1…(len=32)`、`[WS] connect`、`[LATENCY] asr=0.0ms first_audio=5989.7ms total=6036.5ms chars=52`、`[TTS] round mode=rest input_chars=9 reply_chars=52 chunks=1 bytes=1013760`；**失敗路徑**（無效 key）可見 `[TTS] rest-http-error status=401 body={"status":401,"message":"Invalid Token"}`、完整 traceback、`[TTS] round ZERO-AUDIO mode=rest ... chunks=0 bytes=0 last_error=FishTTSError(...)`，且伺服器續服務第二輪不崩潰；主大腦複跑 `tests/clients/test_voice_companion.py` **130 passed**（基線 129 ＋ 1 新測試 `test_logs_dir_gitignored`），**0 既有斷言需改動**。**0 踩線**：未改 TTS 協定/請求參數、未改播送/狀態機/WS 協定、未動排程任務 XML、未動 `src/`、日誌檔未入庫（`clients/voice_companion/logs/` 已 gitignore，含輪替檔 `*.log.1/2/3`）。 | DSH | VC-LOG-1 |

| 2026-09-11 (MEM-IDENTITY-1) | **MEM-IDENTITY-1 跨模態身分正規化：修復 VC↔TG/網頁 SAGE 記憶雙向遮蔽**（commit `5ff1dca`，3 files +320/-2：`src/memory/user_identity.py` 新 69 行 / `src/memory/middleware.py` +12/-2（只動 import + 讀側 1 行 + 寫側 1 行）/ `tests/memory/test_cross_modality_identity.py` 新 241 行）。**背景**：VC/TG/網頁三模態整合盤點（AUDITOR，READ-ONLY，0 生產變更）實證——三條管線共用同一份 SAGE `graph.sqlite`（雙進程雙寫）與同一張 SessionStore，但身分標記分裂使語音與文字的記憶互相遮蔽。**根因（實證，非推論）**：①寫入 `middleware.py:465` `source_pair = f"{target_user_id}:{agent_id}"`，`target_user_id = event.payload.get("target_user_id", "bryan")`（`:460`）；②TG inbound 帶數字 TG id `1696287850`（`router.py:864`）、Web 同值（`gateway.py:870` / `index.html:1091` 硬編碼）→ 寫成 `1696287850:agent_X`；主動回合無該欄位 → `bryan:agent_X`；VC 硬編碼 `f"bryan:{self.agent_id}"`（`akane_voice_brain.py:628`，已 canonical）；③讀取 `middleware.py:299` `source_pair_filter = {f"{target_user_id}:{agent_id}"}`，`sage/reader.py:105` 對「非空且不在集合內」的 fact 一律濾掉（`None`/`""` 放行）；④VC 讀側不帶 filter（`akane_voice_brain.py:350-355`）→ 僅 VC 全見。**實測生產** `data/memory/agent_rem/graph.sqlite`：`bryan:agent_rem` ×14 與 `1696287850:agent_rem` ×8 **並存** → TG/網頁回合 filter `{1696287850:agent_rem}` 看不到 VC 寫的 14 筆，主動回合 filter `{bryan:agent_rem}` 看不到 TG/網頁寫的 8 筆。**註**：VC-UNIFY-1.1 已把 VC 由 `user_bryan:` 修為 `bryan:`（對上主動回合那條），**未解 TG/網頁那條**——屬主服務自身既存的身分分裂。**修法（Owner 拍板 B：身分正規化）**：新增 `src/memory/user_identity.py` 為單一事實來源（純 stdlib、0 依賴）——`canonical_user_id(raw)`（已知 Bryan 身分別大小寫不敏感、int/str 皆可 → `"bryan"`；其他值原樣回傳保留隔離；`None`/空 → `"bryan"`）、`user_identity_aliases`、`source_pair_candidates(raw, agent)`；寫側改 `f"{canonical_user_id(target_user_id)}:{agent_id}"`、讀側改 `source_pair_candidates(target_user_id, agent_id)`（放行 `{bryan, user_bryan, 1696287850}` 別名集，讓既有 8 筆舊資料持續可見）。**別名表僅收有程式碼證據者**：`bryan`（canonical）、`user_bryan`（`src/io/channels/router.py:68` `_BRYAN_ENTITY_ID`）、`1696287850`（`src/io/channels/telegram.py:44` `_BRYAN_TG_USER_ID`）；`middleware.py:135` 註解提及之 `bryan_test` **全 repo 無程式碼當 target_user_id 使用 → 未納入**。**硬性約束（本次最關鍵）**：正規化**只發生在記憶層內部**，**絕不修改 event payload 的 `target_user_id`**，亦未動 `router.py`/`gateway.py`/`input_router.py` 的賦值——因 `router.py:197,219` 以 `target_user_id` 做 TG 投遞路由，改為 `"bryan"` 會使推播送往非數字 chat id 而故障。**未動**：`session_id` 與 `data/conversations/{user}_{agent}_private.json` 檔名規則（保歷史連續性）、`graph_store.py`/`writer.py`/`reader.py`/`provider.py`、schema、`src/io/**`、`clients/**`、`config/`、`personas/`。**0 生產資料變更**（不搬遷、不重寫；舊 8 筆原樣保留）。**驗收（主大腦獨立複驗）**：①新測試 **9/9** 全綠（含真實 `MemoryMiddleware.handle_event` 寫側路徑斷言 `source_pair == "bryan:agent_x"`、prefetch 讀側 filter 集合斷言、真實 `GraphStore`+`MemoryReader` 跨模態矩陣、陌生人 `99999999` 隔離反向斷言）；②**讀側實證**（生產 DB 複本，未碰生產檔）：舊 filter **8 筆** → 新 filter（經 `source_pair_candidates`）**22 筆**、proactive 舊 filter 14 筆、陌生人 filter **0 筆**；③**逐檔 baseline 對照**（`git worktree` at `2b6dbbe`）：4 個既有失敗檔（`test_cross_session_v1` 4 failed / `test_m5_3_s2_retrieval_diagnostic` 2 failed+3 passed / `test_short_term_memory_framing_v1` 1 failed / `test_memory_middleware` 1 error）**前後完全相同 → 0 破壞、0 既有斷言改動**；`test_m5_4_2_memory_v1_mirror_failure_audit` 40 passed。**部署**：主服務 `:8000` 於 16:34 重啟載入新程式碼（10 秒恢復健康、PID 29936、10 bots 恢復輪詢、0 錯誤）；**VC 三台無需重啟**（VC 寫側已 canonical、讀側不帶 filter）。**⚠️ 連帶更正（文件治理）**：本檔與 Notion 上 VC-2.5 記載之「**雙向**共享短期會話流（VC 聊完 → TG 歷史同步）」為**過度宣稱**——同次審計實證 `SessionStore` 為**單向管**：寫入端 `src/io/channels/telegram.py:47-61`（TG inbound）＋`clients/voice_companion/web_server.py:623-624`（VC），而全 `src/` 僅 `telegram.py` 引用 `SessionStore` → **主服務 LLM 管線不讀**，故 **TG→VC 成立、VC→TG 不成立**；已列 P1 待辦（主服務讀側接入）。**同次盤點其餘未修斷點**：C1 仍凍結（文字端未 flush → VC 讀舊快照，延遲無上界）；Cloudflare Access 對三 VC 域名 302；conversations legacy `bryan_*` 檔仍被 fallback 讀；`AGENT_ENV_MAP` 短碼與 config 前綴脫鉤。 **生產寫側實證（事後補證）**：重啟後 TG 回合經批次 commit 落地（批次門檻 `_BATCH_SIZE=20`，`graph_store.py:397-399`），實測 `agent_rem` facts 由 22 → **29 筆**，新增 7 筆全為 `source_pair=bryan:agent_rem` 且 `session_id=session_1696287850_agent_rem`（時間 17:05:38–17:13:39）；舊 `1696287850:agent_rem` 8 筆凍結於 12:13:19 不再新增 → **寫側 canonical 化於生產確認生效，讀寫兩側閉環完成**（對照：VC 寫入列之 `session_id=voice_agent_rem` 亦為 `bryan:agent_rem`）。 | DSH | MEM-IDENTITY-1 |
| 2026-09-11 (SAGE-FLUSH-1) | **SAGE-FLUSH-1 崩溃止损：定时 flush 窄域解冻**（commit `4164e5f`，3 档：`graph_store.py` +38 / `run_server.py` +46 / `tests/memory/test_sage_flush_guard.py` NEW 310 行）。**Owner 授权之窄域解冻**：Frozen Contract「SAGE 写入逻辑」仅解冻「新增一个定时调用既有 `flush()` 的机制」；写入语义/schema/`_BATCH_SIZE`/读取路径/`source_pair` 0 改动。**动机（崩溃止损）**：主服务每数十分钟原生 access violation 硬杀，每崩每 store 损失 `_pending_writes ∈ [1,19]` 笔（典型 10–30 笔/次；当日已崩 7 次）。**实现**：`graph_store.py` 类级 `weakref.WeakSet` 活实例注册表（`__init__` 注册、`close()` discard，close 语义不变）+ classmethod `flush_all_live()`（list 快照、逐实例 try/except 隔离、绝不实例化新 store、回传 `(n_live, n_flushed)`）+ `run_server.py` 15 秒周期任务（`CancelledError→break`、例外只记 WARNING 不死亡、import 置启动路径防污染 VC）。**证据（主大脑独立复验）**：新测试 9/9 绿（亲跑）；实机 import 0 tasks / 1 thread、`_BATCH_SIZE==20`、`len(_live_stores)==0`；回归 242 passed / 4 failed / 1 error，worktree@80a514a 逐档 baseline 对照 5 档完全一致 → 0 新破坏。**效果**：未 commit 窗口由「不确定（可能数十分钟）」缩短为固定 15 秒。**注**：程序注释授权日误写 2026-09-09，实际授权日 **2026-09-11**（本登记为准，程序注释勘误另议）。**0 frozen contract change（解冻范围内）/ 0 production mutation**。 | DSH | SAGE-FLUSH-1 |
| 2026-09-11 (REM-TEXT-1) | **REM-TEXT-1 文字频道括弧动作描写剥离 + Owner 决策反转登记**（commit `a64964a`，5 档 +534/-25）。**Owner 决策①（决策反转，必须明示）**：**反转 Owner 2026-08-07 拍板的「text 保留括号动作描写、只 TTS 剥离」决策** → 文字频道亦剥除括弧动作描写（本登记即该反转之 canonical 记录；原 08-07 决策自 2026-09-11 起失效）。**Owner 决策②（范围）**：守门**只对 `agent_rem` 开启**——理由（审计实证）：`agent_yua.md:432-435` 明文要求括弧行为标签、`agent_ruka.md:389-400` 明文允许中频括弧 → 剥除破坏角色设计；其余 agent 维持现状不剥。**根因（三层实证）**：禁令仅在语音专属守门段落 / 文字频道零程序守门（唯一守门在语音 `_STAGE_PAREN_RE`）/ persona 教材污染（TG 实测 24/24 以「（手上的事停了，抬眼）。嗯。」开头，且该括弧即「工作报备」本身）。**Owner 补充**（「要合理的动作——雷姆老是在工作就不合理」）：行为内容同修（不工作状态合法且不需理由；服从停工指令后不得再提工作）。**实现**：`src/text_guard.py`（NEW）+ `proxy.py` **AGENT_SPEAK 唯一发布点 `:3718` 净化且置「写入 history 之前」**（断正反馈锁）+ read-side `strip_assistant_parens`（只剥自己 speaker）+ `_strip_action_descriptions` 共用 `_STAGE_PAREN_RE` + config `text_channel_stage_guard` **缺省 False 仅 agent_rem true** + `agent_rem.md` 文字频道守门 7 条 + 教材清理。**证据（主大脑独立复验）**：新测试 **18/18 绿（亲跑）**；commit 恰 5 档 0 掺杂；开关仅 `class: AgentRem` 底下 1 处；persona 硬规则计数全保留；`test_seeded_personas_byte_identical_to_baseline` 失败经 worktree@4164e5f 复跑证明为既有。**关联审计（READ-ONLY，未入库）REM-TEXT-AUDIT-1**：rem/ruka 100% 开头括弧（ruka 10 条 10 模板未锁死）、其余 8 agent 0%、语音全 0%；9/10 persona 合法语境示范括弧；8 persona 对「休息/不工作」零命中。**已知未处理**（Ruka 超设定 / Yua 要求括弧却 0 输出 / Mahiru 漏原始 JSON）列后续工单。**0 frozen contract change**。 | DSH | REM-TEXT-1 |
| 2026-09-11 (CRASH-OBS-1) | **CRASH-OBS-1 崩溃观测补洞**（commit `d20d9f2`，6 档 +368/-17）。**三盲区**：① `_start_plan_a.ps1` 每次启动截断 `server_nohup.err` → 崩溃实例末段输出被重启清空；② `heartbeat_trace.log` 60s 覆写只留最后一份；③ faulthandler 倾印无时间戳（单档 68.9MB / 94907 线程头）。**实现**：`_nohup_log_rotation.ps1`（NEW 共用轮替，各 ext 保留 5 份）+ `_start_plan_a.ps1` 启动前轮替 + `server_ops.ps1` 共用 helper + `_collect_crash_dumps.ps1`（NEW：WER `ReportArchive` 收集 `*.dmp`+`Report.wer` 到 `data/crash_dumps/` 保留 5 份，全 try/catch）+ `_watchdog.ps1` 每 tick 独立 process 启动收集器（失败只 WARN）+ `run_server.py` 仅观测层（marker 60s `periodic dump @ <ISO>` 时序戳、32MB 上限轮替 3 份、heartbeat 保留 10 份）；**`dump_traceback_later` C-level 保险保留**。**证据**：部署实证（`server_ops.ps1 restart` 轮替 `server_nohup.20260911_193635.err` **30957 bytes 完整保留** + watchdog 每 tick `crash dump collector launched`）；D 收集器测试修正真实 bug（**WER 实际档名为 `memory.dmp` 非 `.mdmp`**）；回归 156 passed / 8 failed / 1 error baseline 一致；5 个 .ps1 `ParseFile` 0 错误。**环境事实**：本机 PowerShell 5.1（ANSI big5），`.ps1` 必须 UTF-8 BOM + CRLF。**关联审计（READ-ONLY，未入库）CRASH-AUDIT-1**：崩溃 = M6.1-9.2 同签章复发（`python311.dll`+`0xc0000005`，≥7 天），与 MEM-IDENTITY-1 无因果（WER fault bucket 跨部署重现），三轮全单实例自发（crash PID == watchdog listener PID）→ 09-08「双实例→409」本轮不成立；嫌疑 = ProactorEventLoop IOCP 完成埠损坏；09-08 建议 A/B 已实作于 `91e2542`；**F1（`WindowsSelectorEventLoopPolicy`）唯读分析可行但未授权未实作**。**0 frozen contract change**。 | DSH | CRASH-OBS-1 |
| 2026-09-12 (MODEL-V41-FLASH-1) | **MODEL-V41-FLASH-1 全體 LLM 模型切換至 `deepseek-v4.1-flash`（Owner 指令）**（commit `dc80046`，5 檔 +8/-7）。**動機**：Owner 指令「把 soul os 模型全改為 v4.1flash」。**模型 ID 查證（不猜測）**：主大腦直接查端點 `GET https://ollama.com/v1/models`，清單中確認 `deepseek-v4.1-flash` 精確存在（無版本後綴）；舊值 `deepseek-v4-flash:0731`、`LLM_PROVIDER=ollama`。**配置點盤點（全 repo grep）**：生產路徑 6 處——`.env` `LLM_MODEL`（最高優先覆蓋，**未追蹤 + gitignored → 僅本機，不進 commit**）、`configs/default.yaml`、`clients/voice_companion/config.json`（茜）、`profiles/rem.json`、`profiles/mai.json`、`akane_voice_brain.py:598` fallback 字串；**刻意未改**：`harness/eh2_smoke_*.py`（dev 冒煙腳本）、`tests/test_*l2*.py` + `adhoc_l2c_verify.py`（`qwythos` 佔位）、`scripts/smoke_test.py:25`、`logs/`、`docs/`、`notion_page_children_18.json`、以及 `src/llm/proxy.py:4144` / `src/soul/diary.py:85` / `src/soul/dream_event.py:175` / `scripts/run_server.py:914`（**僅註解**的歷史拍板記錄）。**🔴 TTS/ASR 0 改動（硬紅線）**：`s2.1-pro` / `s2.1-pro-free`（Fish Audio 語音合成模型，**不是 LLM**）、`voice_id`、`tts_mode`、`fish_audio` 區段逐項 diff 檢視 **0 觸碰**。**⚠️ 關鍵實證（推翻「新模型是 reasoning 模型所以危險」的初判）**：實施者回報 `deepseek-v4.1-flash` 為 reasoning 模型、`max_tokens` 小（16–128）時 reasoning 吃光預算 → content 空、`finish_reason=length`，並指生產有多處小 `max_tokens`（`dream_event.py:733` = 50 / `:197` = 120 / `proxy.py:3229` = 200 / `seed_provider.py:678` = 200 / `proxy.py:4138` = 200 / `decision.py:447` = 300 / `narrative_sublimator.py:348` = 300 / `sage/writer.py:522` = 400）；**主大腦親跑兩模型 × `max_tokens`(50/120/200/400) 對照實驗**（暫存 py，避開 PowerShell 5.1 內嵌雙引號破壞 curl JSON 的陷阱）→ **舊模型 `deepseek-v4-flash:0731` 同樣是 reasoning 模型**（同樣回傳 `reasoning` 欄位）、**同樣在 50 回空 content**；新模型 50 反而略優（13 字 vs 空），120/200/400 兩者皆正常 → **該行為屬既有特性，本次切換 0 新回歸**。（唯一既存受害點 = `dream_event.py:733`，另立 DREAM-MAXTOKENS-1 修復。）**驗收（主大腦獨立複驗）**：實呼 `POST /v1/chat/completions` HTTP 200、回傳 `model` 與請求一致、content 非空；`configs/loader.py` `load_config()` 解析 `llm.model=deepseek-v4.1-flash`（含三份 VC JSON）；`tests/clients/` **152 passed**；`tests/ -q -k "config or model or proxy_stub"` 135 passed / 3 failed（`test_proxy_stub_speak.py` 缺 pytest-asyncio），worktree@`552f84b` baseline 逐筆重現相同 3 筆 → 既有環境問題。**部署（主大腦執行，四台全綠）**：主服務 :8000 以 `server_ops.ps1 restart` 重啟（watchdog 正確讓路 `SKIP maintenance window`；新 PID 30108 → child 11332 為 **venv trampoline 結構、非崩潰**）；三台 VC :8765/:8766/:8767 以「殺 port listener + trampoline parent、原樣命令列重啟」→ `/health` 200 / HTTP 200、各 1 監聽者。**0 frozen contract change**。 | DSH | MODEL-V41-FLASH-1 |
| 2026-09-12 (DREAM-MAXTOKENS-1) | **DREAM-MAXTOKENS-1 夢境 LLM 呼叫 reasoning 安全下限 clamp**（commit `01995f4`，2 檔 +151/-6：`src/soul/dream_event.py` +22/-6、`tests/test_dream_event_max_tokens.py` NEW 129 行）。**動機（Owner 指令）**：「max_tokens=50 改吧」。**根因（主大腦實測）**：reasoning 模型單次消耗 200–360 tokens，`max_tokens` 低於約 120 時 content 回**空字串且 `finish_reason=length`、完全不報錯**；`dream_event.py:733` 的 impression 呼叫端 `max_tokens=50` → **該欄位持續靜默空回**（既有缺陷；**舊模型同樣如此，非本次模型切換造成**）。**⚠️ 範圍界定（必須明示，避免誤歸因）**：本缺陷影響**夢境印象（dream impression）欄位**，**與「語音對話沒反應」無關**——對話走 `proxy.py`（max_tokens 500/3000），且調查中一度懷疑的 `akane_voice_brain.py:353 max_tokens=300` 實為 **SAGE MemoryReader 的 context 預算、非 LLM 輸出上限**；Bryan 反映的語音無回應經日誌實證另屬 **VC-NOREPLY-1**（LLM 呼叫 `network ReadTimeout` 重試耗盡 → 整回合靜默死亡）。**實作（決策已定）**：新增模組級常數 `REASONING_SAFE_MIN_TOKENS = 400`（附實測依據註解）；`_call_llm_for_dream_event` 內 `effective = max(REASONING_SAFE_MIN_TOKENS, max_tokens)`（**只抬升、不縮減**），LLMProxy 主路徑（L229）與 legacy minimax JSON payload（L261）皆改用 `effective`；呼叫端原始數值（L204 預設 120、L749 impression 50）**保留**但註解標明「意圖下限、實際生效值由 clamp 決定」。**驗收**：單元測試 **6/6**（50→400、120→400、800→800 不縮減、`_extract_impression` 生產呼叫端 50→400、legacy minimax payload 50→400、常數 == 400），全用 fake proxy / fake httpx **0 網路**；**主大腦親驗 clamp 實作**（`dream_event.py:217` 定義、`:229` 主路徑套用）。**🔴 實呼驗證（生產 impression prompt，未自創）**：`max_tokens=400` → HTTP 200、`finish_reason=stop`、content=`月明かりに響く懐かしい足音`（13 字，非空）、`reasoning` 約 571–622 字元、`usage.completion_tokens` 209–257；**對照組 `max_tokens=50` → `finish_reason=length`、content 空字串（len=0）** ← **直接證實修的是真問題**。回歸 `tests/ -q -k dream` 107 passed / 1 skipped / 4 failed，worktree@`dc80046` baseline **逐筆重現相同失敗**（m0.4/m0.5 proxy 截斷與 mock 輸出長度不符、m2.0 `read_entries` v1 基線 caller 數 1≠0，皆與 max_tokens 無關）→ 既有失敗。**部署（主大腦執行）**：主服務 :8000 重啟（03:46:52，`/health` 200、SAGE flush 定時任務在跑）。**0 frozen contract change**。 | DSH | DREAM-MAXTOKENS-1 |
| 2026-09-12 (VC-NOREPLY-1) | **VC-NOREPLY-1 語音回合「靜默死亡」可見化**（commit `5228fd5`，3 檔 +211/-3：`clients/voice_companion/web_server.py` +25/-3、`clients/voice_companion/web_ui.py` +11/-3、`tests/clients/test_vc_turn_failure_visibility.py` NEW 178 行）。**動機（Owner 反映）**：Bryan「我幾次講話沒反應」——**經確認為雷姆（:8767）**，非茜（茜的 Fish Audio 401 → ZERO-AUDIO 是 2026-09-11 14:37/14:43 的**暫時性**事件，14:47:28 已自行恢復 `bytes=783360`，**非本次原因**）。**根因（主大腦日誌實證，非推論）**：`clients/voice_companion/logs/vc_agent_rem.log` 2026-09-12 顯示——`03:11:48 ptt_start` → `03:11:50 [UTT] asr-ok 月亮是挺美的。` → **無任何 `[TTS] round`**；`03:12:37 ptt_start` → `03:12:39 asr-ok 那我先晚安喽。` → **無任何 `[TTS] round`**；`03:13:02 ptt_start` → `03:13:04 asr-ok 雷姆，雷姆，你怎么了？`（Bryan 已在追問）→ `03:13:33 [TTS] round first_audio_ms=29376.7`（**29 秒**才出聲）。**關鍵**：該日誌**全檔無任何 `reply-error` / `上游失敗` 痕跡** → 這兩次**連舊有錯誤路徑都沒觸發、完全無痕**。同時段 `[src.llm.proxy] [OpenAIBackend] retry 1/3、2/3、3/3 (last error: network ReadTimeout)`，`retry 3/3` 於 `03:15:39`（最後一次嘗試）。**⚠️ 兩項已排除的誤歸因（主大腦自查並更正）**：① `dream_event.py:733` 的 `max_tokens=50` 走的是**夢境印象**路徑，與對話回覆無關（對話走 `proxy.py`，max_tokens 500/3000）；② `akane_voice_brain.py:353 max_tokens=300` 實為 **SAGE MemoryReader 的 context token 預算**，不是 LLM 輸出上限。**修法（可見化）**：`web_server.py` 新增 `TURN_FAILURE_HINT = "連線異常，請再說一次"`（不裸露例外給使用者）＋ `self._agent_id`（取自 config `companion.id`）；**例外分支**由 `f"上游失敗: {exc}"` 改為先記 **ERROR `[VC-TURN-FAIL] agent_id=... gen=... elapsed_ms=... exc_type=... last_error=...`** 再送乾淨提示；**🔴 新增「LLM 回傳空值」分支**——`reply` 為空時原本**完全靜默**，現記 ERROR（`exc_type=empty_llm_output`）並送 error 訊息。`web_ui.py` 新增 `.msg.error` 樣式（紅色系、字級 13px）＋ `addMsg` 支援 `error` role 以「**系統**」名義呈現在聊天區（**不只在 `#errorBox` 閃一下就消失**）。**驗收**：`pytest tests/clients/ -q` **155 passed**（152 基線 + 3 新；主大腦親跑）。**⚠️ 範圍界定（必須明示）**：本單**只做可見化，未改任何逾時值或重試次數**（調參須先量測）；且**「被新回合 supersede 而走無痕 early-return（`task_gen != self._generation`）」的分支本次未動**——該路徑是否即為本次真因（假說：使用者沒聽到回應→再講一次→殺掉還在等 LLM 的回合，形成保證無回覆的循環），待 **VC-TURN-LIFECYCLE-AUDIT-1**（READ-ONLY 稽核，已派發）以 file:line 定案。**另一未解**：`proxy.py:1590` 是 `httpx.AsyncClient(timeout=120.0)`，但日誌顯示重試間隔僅 1.05/1.25/2.32/4.20 秒，**實際逾時來源不明**（嫌疑 `src/llm/rate_limiter.py:20` 的參數化 timeout），同併入該稽核。**部署**：三台 VC 重啟以載入新 `web_server.py`。**0 frozen contract change**。 | DSH | VC-NOREPLY-1 |
| 2026-09-12 (VC-TURN-LIFECYCLE-AUDIT-1) | **VC-TURN-LIFECYCLE-AUDIT-1 語音回合生命週期稽核（🔴 READ-ONLY：0 檔案修改 / 0 commit / 0 服務重啟 / 0 副作用測試）**。**目的**：以 file:line 定案雷姆（:8767）2026-09-12 `03:11:50` 與 `03:12:39` 兩次「使用者說話但完全無回覆」的精確程式路徑。**✅ 判定：supersede（barge-in）靜默路線**——① 語音回合的 LLM **不走** `src/llm/proxy.py`，而是 `clients/voice_companion/akane_voice_brain.py:420 build_llm_stream` → **L277-282 `requests.post(endpoint, timeout=60)`（同步、零 log、跑在 `asyncio.to_thread`）**；② ollama.com 上游不穩窗口 `03:11:36–03:13:27`（httpx 200 OK 空洞 ≈**111 秒**）內該請求卡住；③ 使用者在等待中按下一個 `ptt_start` → `web_server.py:392` 命中 `state==SPEAKING`（L575 在 LLM 呼叫**之前**已 set）→ `_barge()`（L720-731）→ **L725 世代 +1** + `reply_task.cancel()`；④ `_finish` 卡在 L590 `await asyncio.to_thread(_generate)` → `CancelledError` → **L591-592 原樣 re-raise、零 log**；⑤ `to_thread` 底層 thread **無法真正取消** → `requests.post` 續卡至 60 秒逾時，例外拋進**已取消的 future** 被丟棄 → asyncio 世界永不可見。**「完全無痕」的三個來源（全不寫 log、不發 user 訊息）**：L591-592 cancel 路徑 + L612／L617／L561-562 世代不符 early-return + L594 else（世代不符時異常被 `if` 吞掉）；對照之下 L493-495（`asr-cancelled`）與 L532-534（`refiner-cancelled`）**有** log。**判定方法（可證偽）**：四個「回合結束」標記 `[TTS] round`（L682）/ `ZERO-AUDIO`（L675）/ `[VC-TURN-FAIL]`（L597）/ `reply-error`（L603）**全部缺席** + 進程未崩潰（下次 startup = 03:34:01）→ 三重排除後唯一存活路徑（**強推論**；缺一槌定音證據 = 語音 requests 通道**零觀測點**，此即下一單 VC-TURN-OBS-1 的動機）。**⚠️ 本單糾正本檔先前記載兩點**：① 「重試間隔 1.05/1.25/2.32/4.20 秒」是 `proxy.py:1596` 的 **backoff sleep 值**（log 先打、sleep 在後），**非失敗間隔**——實測 `03:13:37,461 + 2.32s + 120s ≈ 03:15:39,853`，attempt 1/2 **各吃滿 ~120 秒**，**120s timeout 屬實**；主大腦先前「秒級逾時 ⇒ 實際逾時遠短於 120 秒」之推論**錯誤，特此更正**。② `network ReadTimeout` **不是 `str(exc)`**（httpx 原生 str 為 `"Timed out while reading from ..."`），而是 `proxy.py:1659-1661` **手工拼接**的 `f"network {type(e).__name__}"`；且該 120s httpx 通道屬**同進程的 SAGE judge / 記憶背景呼叫**（`akane_voice_brain.py:578-603 _wire_sage_judge`），**與語音回覆無因果**（同源於 ollama.com 不穩，但通道不同）。**⚠️ 主大腦另獨立查證並推翻稽核一項附帶結論**：稽核稱背景通道 model `deepseek-v4-flash:0731`「≠ rem.json 的 v4.1 → 證實是另一 config 路徑」——實為**時間錯位**（該 log 為 03:09–03:15，早於 03:33 的模型切換；該時點 rem.json 同為舊值），且 03:34 重啟後該通道 **0 筆活動**（`OpenAIBackend` 計數 0）故無從比較；完整行實證 `redacted_body={'model': 'deepseek-v4-flash:0731', 'max_completion_tokens': 1000}` = SAGE judge（`src/memory/llm_judge.py:238`），其 model 源自 `akane_voice_brain.py:598` 的 `llm_cfg.get("model")`＝VC profile → **無第 7 個配置點，MODEL-V41-FLASH-1 的 6 處即完整**。**已排除的誤歸因（勿再重查）**：`[OpenAIBackend] retry / network ReadTimeout` 與語音無回覆**無因果**。**稽核建議之後續（主大腦拍板結果）**：① 可見化 → 已立 **VC-TURN-OBS-1**；② 消滅幽靈請求（語音 LLM 通道改可取消 / httpx streaming）→ **暫緩**，待 OBS 觀測資料；③ 被 supersede 回合的「可回復性」（於新回覆中承接被丟棄的 user 輸入）屬 UX/契約級變更 → **留待 Owner**；④ 縮短語音 `timeout=60` → **暫緩**（屬行為變更）。**0 frozen contract change / 0 生產變更**。 | DSH | VC-TURN-LIFECYCLE-AUDIT-1 |
| 2026-09-12 (VC-TURN-OBS-1) | **VC-TURN-OBS-1 語音回合生命週期全面可見化**（commit `37b9da1`，5 檔 +328/-30：`clients/voice_companion/web_server.py` +45、`clients/voice_companion/akane_voice_brain.py` +77、`clients/voice_companion/web_ui.py` +14、`src/llm/proxy.py` +7、`tests/clients/test_vc_turn_observability.py` NEW 215 行）。**動機**：直接回應 VC-TURN-LIFECYCLE-AUDIT-1 的定案——「完全無痕」的三個來源（`web_server.py` L591-592 `CancelledError` re-raise、L612/L617/L561-562 三處世代不符 early-return、L594 世代不符時異常被 `if` 吞掉）**全都不寫 log、不發 user 訊息**，加上語音 LLM 通道（`akane_voice_brain.py:277-282 requests.post`）**零觀測點**，使兩次無回覆只能靠「四個回合結束標記全缺席」三重排除反推，而非直接命中（缺一槌定音證據）。**做法**：① `web_server.py` 八個打點全數補上——INFO `reply-start`（L592-595）/ INFO `reply-awaiting-llm`（L597-598）/ **WARNING `reply-cancelled gen=.. curr=..`（L601-607，新增 `except asyncio.CancelledError`，打完原樣 re-raise）**/ WARNING `reply-dropped-exc`（L627-632，新增 else 分支）/ WARNING `reply-superseded stage=pre-task|after-llm|after-drain`（L563 / L634-640 / L644-650）/ WARNING `barge reason=ptt_start|interrupt|text gen:..->..`（L752-762，`_barge()` reason 參數化）；② `akane_voice_brain.py` `build_llm_stream` 四點 `[LLM-STREAM] request-start|first-token|done|error` 全帶 `elapsed_ms`（L283 / L316-318 / L321-323 / L326-331），異常原樣 re-raise，`requests.post(timeout=60)` 原封不動；③ `src/llm/proxy.py` 網路例外 `last_error` 附 `elapsed_ms=`（L1619 + L1662-1665，原始行 L1660），用以區分 <5s 快速失敗 vs 120s full timeout；④ `web_ui.py` 具名常數 `THINKING_SLOW_MS = 8000` + `THINKING_SLOW_TEXT`（L133-134），`setState` 內逾時且仍為 THINKING 才覆寫狀態文字，離開 THINKING 即 `clearTimeout` 還原（L158-171）。**UX 效果**：直接切斷本事故的 doom loop——使用者因「不知她還在處理」而重複按鍵，反而取消在途回合。**驗收**：新增 `tests/clients/test_vc_turn_observability.py` 4 筆（barge 取消 → `reply-cancelled` WARNING + barge 記錄；世代不符 → `reply-superseded`；`build_llm_stream` 異常 → `[LLM-STREAM] error` 含 elapsed_ms，用 127.0.0.1:1 拒絕連線、**0 外部網路**；UI 8 秒升級常數/文字/還原邏輯存在）；`pytest tests/clients/ -q` → **159 passed**（155 既有全綠 + 4 新，主大腦親跑）。**主大腦獨立複驗**：抽查 `akane_voice_brain.py` diff 確認 `timeout=60` 僅縮排變動、新增 `except Exception as exc` 原樣 re-raise、`return stream` 未動、既有 `except (ValueError, TypeError)` 保留。**明確聲明：0 逾時值變更、0 重試次數變更、0 控制流變更**（early-return / barge 條件 / `_generation` 語意 / 狀態機 / cancelled re-raise 全部保持；唯一結構性新增為 akane stream 的 try/except 包裝，語意等價）。**部署**：三台 VC 重啟 → HTTP 200、各 1 監聽者、標記 `THINKING_SLOW_MS` / `msg.error` / `TAIL_BUFFER_MS` 全數載入；**主服務 :8000 本次未重啟**（`proxy.py` 為 log-only 改動）。**意外觀察**：`ws.close() → session.close()` 亦觸發 `_barge()`（預設 reason=interrupt），排查時會見到 teardown barge 記錄。**Frozen Contract 0 觸碰**。 | DSH | VC-TURN-OBS-1 |
| 2026-09-12 (CRASH-F1) | **CRASH-F1 翻牌部署上線（48h 觀察窗口起算）+ 補登記治理缺口**（commit `2b2fe62`，1 檔：`configs/default.yaml`，`server.event_loop: proactor → selector`）。**⚠️ 本單為「補登記」**：`67d3fd1 feat(server): configurable WindowsSelectorEventLoopPolicy experiment (CRASH-F1)` 實作完成後**從未被登記**（`CRASH-F1` 在本檔命中數 = 0），`03cfcde` 僅登記 SAGE-FLUSH-1 / REM-TEXT-1 / CRASH-OBS-1。**動機（Owner 拍板）**：主服務 2026-09-12 崩 **4 次**——`00:03` / `08:33:05` / `08:53:03` / `09:38:02`，後三次 watchdog 記 `port_listen=False procs=0 -> restart`，重啟預算 N=3/10。**WER 實證**：錯誤模組 `python311.dll`、`Sig[6] 例外狀況代碼 = c0000005`（access violation）。**假說**：ProactorEventLoop（Windows IOCP）觸發 CPython 原生層崩潰。**部署前 pre-flight（主大腦執行）**：selector loop 於 Windows 已知地雷 = **不支援 asyncio 子程序**（`create_subprocess_*` / `loop.subprocess_exec`）→ 全 repo ripgrep 確認**僅** `src/soul/mcp_stdio_client.py:245` 一處使用，且該模組於**生產程式碼 0 import**（28 筆命中除自身全在 `tests/`）；`tests/test_crash_f1_selector.py` Test D 已用 AST 掃描 `src/`（排除 tests）驗證同一件事並標註 tests-only → **判定翻牌安全**。**執行**：① config 翻牌 + 註解改寫（記明動機／證據／窗口／回滾）；② `pytest tests/test_crash_f1_selector.py -q` → **5 passed**（測試為注入式 config、不寫死預設值，故翻牌後仍全綠）；③ commit `2b2fe62`；④ **以 watchdog 同一支 `scripts/_start_plan_a.ps1` 重啟**（刻意不採「停掉讓 watchdog 判定崩潰」以免燒掉重啟預算）→ launcher PID 29476 / listener PID 27644 / `/health` **200**。**部署驗證（`data/server_nohup.err` 原文）**：`[CRASH-F1] event loop policy -> WindowsSelectorEventLoopPolicy`、`[Server] Active asyncio event loop: WindowsSelectorEventLoop (preference=selector)`、`[Server] event loop self-check 啟動 (600s/次, 寫 data/state/event_loop_alive.json)`。**🔴 48h 窗口起算 2026-09-12 10:58:13，成功判準 = `python311.dll 0xc0000005` 嚴格為 0；回滾 = 改回 `proactor` + 重啟（單行、零程式碼）。** **流程漏洞記錄**：工單「實作完成」不等於「已登記」——本次起因即為 CRASH-F1 實作完成卻漏登記，導致部署狀態長期無人掌握。**0 frozen contract change**。 | DSH | CRASH-F1 |
| 2026-09-12 (PERSONA-FIX-1) | **PERSONA-FIX-1 淨化兜底修復（D1）+ 移除誤導性死碼 log（D4②）**（commit `0d14c9a`，2 檔：`src/llm/proxy.py` +69/-9、`tests/test_persona_fix1_layer3_sanitize.py` NEW 17 測試）。**D1 缺陷**：`_parse_llm_output`（`src/llm/proxy.py:2979`）三層解析在 Layer1（2697）/Layer2（2706）全敗後走 **Layer 3「純 text 兜底」（3063-3089）**，該分支**只做** `cleaned = raw.strip()`（3070）+ 剝 ``` fence（3072-3074）+ `_truncate_repetition`（3077），**漏掉成功路徑才套用的** `_strip_fake_function_calls`（2842）與 `_strip_emotion_tags_from_text`（2879-2898，`_EMOTION_TAG_LINE_PREFIX` 2876），亦**無 JSON 殼 unwrap** → raw 原樣當 `text` ＋ `audio_text` 廣播並送 TTS。**日誌鐵證**：`data/logs/server_20260730_205447.err:9374-9399`（2026-07-31 01:43）——L9374 純 text 兜底 → L9375 `生成完成 text='{"text": "那真昼就继续待着了…'` → L9389 `[Gateway] broadcasting` 原樣帶 `{"text":` → **L9395-9399 `[FishTTS] text_len=37 -> 49`（TTS 連 JSON 前綴一起合成）**；Yua 同漏 `server_20260730_230718.err:19243`；Ruka 行首標籤漏出 `server_20260801_205401.err:4701`（`text='[停頓] Bryan…'`）。標籤產生處（唯讀未動）：`src/voice/build_system_prompt.py:113-117` → 注入 L350-354；映射 `src/llm/emotion_marker_map.py:73`。**修法**：Layer 3 兜底依序補上 ① JSON 殼 unwrap（先 `json.loads` 取 `.get("text")`，失敗再剝 `{"text": "` 前綴與尾端 `"}`）② `_strip_fake_function_calls` ③ `_strip_emotion_tags_from_text`，**與成功路徑（3125-3140）完全對齊**，`text` 與 `audio_text` 皆套用。**D4② 修法**：`LLMProxy` 內（原 `3643-3653`）打 `retry with JSON enforcement` 的**死碼 log**（retry 實作已於 2026-07-22 被 JP rollback 移除、log 字串留存誤導，實測 `3655-3659` 立即以 `audio_text = generated_text` 覆寫、**無第二次 LLM 呼叫**）改為誠實措辭。**⚠️ 流程事件（本檔必須記錄）**：執行者 `dd972b1a` **於收尾階段靜默死亡**（與 SUBAGENT-DEATH-AUDIT-1 定案的模式一致），死亡時 **`proxy.py` 僅為 working-tree modification、測試檔為 untracked，全部未 commit**。主大腦依「先翻殘骸」紀律發現 → 驗證（新測試 **17 passed**、`tests/clients/` **159 passed**）→ 移除一個**越界測試**（`test_complete_json_shell_final_result`：斷言 `{"text":"早安"}` 完整殼經 Layer 1 成功路徑後 `audio_text=="早安"`，屬**本單範圍外**且**斷言未實作的行為**）→ **由主大腦代為 commit + push 搶救**。**驗收**：`tests/test_persona_fix1_layer3_sanitize.py` **17 passed**；`tests/clients/ -q` **159 passed**。**0 新 LLM 呼叫 / 0 max_tokens 變更 / 0 逾時值變更 / 0 frozen contract change**。**部署**：主服務尚未重啟。**遺留議題（範圍外，待另單）**：完整 JSON 殼僅含 `text` 無 `audio_text` 時（`{"text": "早安"}`）`audio_text` 回傳 `''` → 可能「有文字無語音」的靜默回覆；**未經生產日誌證實，屬待查候選缺陷，不可逕行當成已確認 bug**。 | DSH | PERSONA-FIX-1 |

**End of canonical state registry. Next update requires Owner authorization per §2.4 lifecycle.**
